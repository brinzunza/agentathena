import {
  UploadInterrupted,
  UploadRejected,
  chunkSizeFor,
  formatBytes,
  isAbort,
  uploadFile,
  uploadFiles,
  MAX_CHUNK_BYTES,
  MIN_CHUNK_BYTES,
} from './uploader';

// --- fakes: just enough of a File and of the server's behaviour ---

class FakeFile {
  constructor(name, size, { lastModified = 1000, fill = 7 } = {}) {
    this.name = name;
    this.size = size;
    this.lastModified = lastModified;
    this.bytes = Uint8Array.from({ length: size }, (_, i) => (i * 31 + fill) % 251);
  }

  slice(start, end) {
    return { bytes: this.bytes.slice(start, end), size: end - start };
  }
}

const apiError = (message, status, extra = {}) => Object.assign(new Error(message), { status, ...extra });
const networkError = () => new Error('network error');

class FakeServer {
  constructor({ chunkSize = 1000 } = {}) {
    this.sessions = {};
    this.chunkSize = chunkSize;
    this.nextId = 1;
    this.requests = [];        // [offset, length] of every chunk request that reached the API
    this.failures = [];        // queued behaviours for upcoming chunk requests
    this.createFailures = [];
  }

  fail(...behaviours) {
    this.failures.push(...behaviours);
    return this;
  }

  get api() {
    return {
      createSession: async ({ filename, size, lastModified }) => {
        const failure = this.createFailures.shift();
        if (failure) throw failure;
        const existing = Object.values(this.sessions).find(
          (s) => s.filename === filename && s.size === size && s.lastModified === lastModified
        );
        if (existing) return this.describe(existing, true);
        const session = { id: `up${this.nextId++}`, filename, size, lastModified, data: new Uint8Array(size), received: 0 };
        this.sessions[session.id] = session;
        return this.describe(session, false);
      },
      putChunk: async (uploadId, offset, chunk, { signal, checksum } = {}) => {
        this.requests.push([offset, chunk.size]);
        // A client stuck in a loop would otherwise spin forever and hang the test run instead of failing it
        if (this.requests.length > 500) throw apiError('runaway upload loop', 400, { code: 'runaway' });
        this.lastChecksum = checksum;
        const behaviour = this.failures.shift();
        if (behaviour === 'network') throw networkError();
        if (behaviour === 'server') throw apiError('server error', 503);
        if (behaviour === 'damaged') throw apiError('the chunk was damaged in transit', 422, { code: 'checksum' });
        if (behaviour === 'expire') delete this.sessions[uploadId];
        if (behaviour && behaviour.status) throw behaviour;
        const session = this.sessions[uploadId];
        if (!session) throw apiError('upload not found', 404, { code: 'not_found' });
        if (offset !== session.received) {
          throw apiError('offset mismatch', 409, { code: 'offset_mismatch', received: session.received });
        }
        session.data.set(chunk.bytes, offset);
        session.received += chunk.size;
        if (behaviour === 'reply-lost') throw networkError();   // stored, but the client never hears
        return { received: session.received, complete: session.received >= session.size };
      },
    };
  }

  describe(session, resumed) {
    return {
      upload_id: session.id, size: session.size, received: session.received, resumed,
      complete: session.received >= session.size, chunk_size: this.chunkSize,
    };
  }

  stored(file) {
    const session = Object.values(this.sessions).find((s) => s.filename === file.name);
    return session && session.received === file.size && session.data.every((b, i) => b === file.bytes[i]);
  }
}

const noWait = async () => {};

function options(server, extra = {}) {
  const delays = [];
  const progress = [];
  return {
    delays,
    progress,
    options: {
      api: server.api,
      wait: async (ms) => { delays.push(ms); },
      onProgress: (p) => progress.push(p),
      ...extra,
    },
  };
}

// --- chunk sizing and formatting ---

describe('chunkSizeFor', () => {
  test('small files still get about twenty steps', () => {
    expect(chunkSizeFor(1_000_000, MAX_CHUNK_BYTES)).toBe(50_000 > MIN_CHUNK_BYTES ? 50_000 : MIN_CHUNK_BYTES);
    expect(Math.ceil(1_000_000 / chunkSizeFor(1_000_000))).toBeGreaterThanOrEqual(15);
  });

  test('large files use the server\'s chunk size', () => {
    expect(chunkSizeFor(200 * 1024 * 1024)).toBe(MAX_CHUNK_BYTES);
    expect(chunkSizeFor(200 * 1024 * 1024, 512 * 1024)).toBe(512 * 1024);
  });

  test('never smaller than the minimum, and never larger than the server allows', () => {
    expect(chunkSizeFor(10_000)).toBe(MIN_CHUNK_BYTES);
    expect(chunkSizeFor(10_000, 4_000)).toBe(4_000);
    expect(chunkSizeFor(1, 1)).toBe(1);
  });
});

test('formatBytes', () => {
  expect(formatBytes(0)).toBe('0 B');
  expect(formatBytes(512)).toBe('512 B');
  expect(formatBytes(1536)).toBe('1.5 KB');
  expect(formatBytes(150 * 1024)).toBe('150 KB');
  expect(formatBytes(3.2 * 1024 * 1024)).toBe('3.2 MB');
  expect(formatBytes(42 * 1024 * 1024)).toBe('42 MB');
});

// --- a clean upload ---

describe('uploading', () => {
  test('sends the file in chunks and the server ends up with exactly its bytes', async () => {
    const server = new FakeServer({ chunkSize: 1000 });
    const file = new FakeFile('hw.pdf', 3500);
    const { options: opts } = options(server);
    const result = await uploadFile(file, opts);
    expect(result.resumedFrom).toBe(0);
    expect(server.requests).toEqual([[0, 1000], [1000, 1000], [2000, 1000], [3000, 500]]);
    expect(server.stored(file)).toBe(true);
  });

  test('progress is the bytes the server confirmed, in order, ending at the file size', async () => {
    const server = new FakeServer({ chunkSize: 1000 });
    const file = new FakeFile('hw.pdf', 2500);
    const { options: opts, progress } = options(server);
    await uploadFile(file, opts);
    expect(progress.map((p) => p.loaded)).toEqual([0, 1000, 2000, 2500]);
    expect(progress.every((p) => p.total === 2500)).toBe(true);
  });

  test('a file smaller than one chunk is a single request', async () => {
    const server = new FakeServer({ chunkSize: 1_000_000 });
    const file = new FakeFile('tiny.pdf', 5000);
    await uploadFile(file, options(server).options);
    expect(server.requests).toHaveLength(1);
    expect(server.stored(file)).toBe(true);
  });

  test('chunk checksums are computed and sent when a hasher is provided', async () => {
    const server = new FakeServer({ chunkSize: 1000 });
    const file = new FakeFile('hw.pdf', 1500);
    const hashed = [];
    const hashChunk = async (chunk) => { hashed.push(chunk.size); return `sum-${chunk.size}`; };
    await uploadFile(file, options(server, { hashChunk }).options);
    expect(hashed).toEqual([1000, 500]);
    expect(server.lastChecksum).toBe('sum-500');
  });

  test('reports the session id as soon as it has one', async () => {
    const server = new FakeServer();
    const seen = [];
    await uploadFile(new FakeFile('hw.pdf', 100), options(server, { onSession: (id) => seen.push(id) }).options);
    expect(seen).toEqual(['up1']);
  });
});

// --- resuming ---

describe('resuming', () => {
  test('continues from what the server already holds and sends only the rest', async () => {
    const server = new FakeServer({ chunkSize: 1000 });
    const file = new FakeFile('hw.pdf', 3500);
    const first = options(server, { retries: 0 });
    server.fail(null, null, 'network');                      // two chunks arrive, the third breaks
    await expect(uploadFile(file, first.options)).rejects.toBeInstanceOf(UploadInterrupted);
    expect(Object.values(server.sessions)[0].received).toBe(2000);

    server.requests.length = 0;
    const second = options(server);
    const result = await uploadFile(file, second.options);   // the same file, announced again
    expect(result.resumedFrom).toBe(2000);
    expect(server.requests).toEqual([[2000, 1000], [3000, 500]]);       // nothing already stored is sent again
    expect(second.progress[0]).toMatchObject({ loaded: 2000, resumedFrom: 2000 });
    expect(server.stored(file)).toBe(true);
  });

  test('a file the server already has in full sends nothing', async () => {
    const server = new FakeServer({ chunkSize: 1000 });
    const file = new FakeFile('hw.pdf', 2000);
    await uploadFile(file, options(server).options);
    server.requests.length = 0;
    const result = await uploadFile(file, options(server).options);
    expect(server.requests).toEqual([]);
    expect(result.resumedFrom).toBe(2000);
  });

  test('a different file with the same name is a new upload, not a resume', async () => {
    const server = new FakeServer({ chunkSize: 1000 });
    await uploadFile(new FakeFile('hw.pdf', 2000, { lastModified: 1 }), options(server).options);
    const result = await uploadFile(new FakeFile('hw.pdf', 2000, { lastModified: 2 }), options(server).options);
    expect(result.resumedFrom).toBe(0);
    expect(Object.keys(server.sessions)).toHaveLength(2);
  });
});

// --- retrying ---

describe('transient failures', () => {
  test('a dropped request is retried and the upload still completes intact', async () => {
    const server = new FakeServer({ chunkSize: 1000 });
    const file = new FakeFile('hw.pdf', 3000);
    server.fail(null, 'network', 'server');
    const { options: opts } = options(server);
    await uploadFile(file, opts);
    expect(server.stored(file)).toBe(true);
    expect(server.requests.map(([offset]) => offset)).toEqual([0, 1000, 1000, 1000, 2000]);
  });

  test('retries back off exponentially up to a cap, and report themselves', async () => {
    const server = new FakeServer({ chunkSize: 1000 });
    const file = new FakeFile('hw.pdf', 1000);
    server.fail('network', 'network', 'network', 'network', 'network');
    const retries = [];
    const { options: opts, delays } = options(server, {
      retries: 6, backoffMs: 500, maxBackoffMs: 3000, onRetry: (r) => retries.push([r.attempt, r.delayMs]),
    });
    await uploadFile(file, opts);
    expect(delays).toEqual([500, 1000, 2000, 3000, 3000]);
    expect(retries).toEqual([[1, 500], [2, 1000], [3, 2000], [4, 3000], [5, 3000]]);
  });

  test('the failure count resets after a success, so scattered hiccups never add up to giving up', async () => {
    const server = new FakeServer({ chunkSize: 100 });
    const file = new FakeFile('hw.pdf', 1000);              // ten chunks
    server.fail('network', null, 'network', null, 'network', null, 'network', null);
    await uploadFile(file, options(server, { retries: 1 }).options);
    expect(server.stored(file)).toBe(true);
  });

  test('a damaged chunk (checksum mismatch) is simply sent again', async () => {
    const server = new FakeServer({ chunkSize: 1000 });
    const file = new FakeFile('hw.pdf', 1000);
    server.fail('damaged');
    await uploadFile(file, options(server).options);
    expect(server.requests).toHaveLength(2);
    expect(server.stored(file)).toBe(true);
  });

  test('creating the session is retried too', async () => {
    const server = new FakeServer();
    server.createFailures.push(networkError(), apiError('unavailable', 503));
    const file = new FakeFile('hw.pdf', 100);
    await uploadFile(file, options(server).options);
    expect(server.stored(file)).toBe(true);
  });

  test('when retries run out it throws an interruption that says how far it got', async () => {
    const server = new FakeServer({ chunkSize: 1000 });
    const file = new FakeFile('hw.pdf', 3000);
    server.fail(null, 'network', 'network', 'network');
    let error;
    try {
      await uploadFile(file, options(server, { retries: 2 }).options);
    } catch (e) {
      error = e;
    }
    expect(error).toBeInstanceOf(UploadInterrupted);
    expect(error).toMatchObject({ uploadId: 'up1', loaded: 1000, total: 3000 });
  });

  test('waits for the browser to come back online before retrying', async () => {
    const server = new FakeServer({ chunkSize: 1000 });
    const order = [];
    server.fail('network');
    const waitForOnline = async () => { order.push('online-check'); };
    const { options: opts } = options(server, { waitForOnline, wait: async () => { order.push('backoff'); } });
    await uploadFile(new FakeFile('hw.pdf', 1000), opts);
    expect(order).toEqual(['online-check', 'backoff']);
  });
});

// --- staying in step with the server ---

describe('resynchronising', () => {
  test('a lost reply (the chunk arrived, the answer did not) is recognised and not sent twice', async () => {
    const server = new FakeServer({ chunkSize: 1000 });
    const file = new FakeFile('hw.pdf', 3000);
    server.fail(null, 'reply-lost');
    await uploadFile(file, options(server, { retries: 5 }).options);
    expect(server.stored(file)).toBe(true);
    // chunk 2 was sent once, got a lost reply, was "retried" as an offset the server had passed (409),
    // and the client moved on from the server's count
    expect(server.requests.map(([offset]) => offset)).toEqual([0, 1000, 1000, 2000]);
  });

  test('if the server is ahead (another tab, or a replayed request) the client jumps to its count', async () => {
    const server = new FakeServer({ chunkSize: 1000 });
    const file = new FakeFile('hw.pdf', 5000);
    const { options: opts } = options(server);
    let triggered = false;
    const api = {
      ...server.api,
      putChunk: async (id, offset, chunk, o) => {
        if (!triggered && offset === 1000) {         // someone else uploads the next three chunks first
          triggered = true;
          for (let at = 1000; at < 4000; at += 1000) await server.api.putChunk(id, at, file.slice(at, at + 1000), o);
        }
        return server.api.putChunk(id, offset, chunk, o);
      },
    };
    await uploadFile(file, { ...opts, api });
    expect(server.stored(file)).toBe(true);
  });

  test('gives up rather than looping forever if resynchronising never makes progress', async () => {
    const api = {
      createSession: async () => ({ upload_id: 'x', received: 0, chunk_size: 1000, size: 3000 }),
      putChunk: async () => { throw apiError('offset mismatch', 409, { received: 0 }); },
    };
    await expect(uploadFile(new FakeFile('hw.pdf', 3000), { api, wait: noWait })).rejects.toBeInstanceOf(UploadInterrupted);
  });

  test('a session the server has lost (expired, cleared) is recreated and the upload carries on', async () => {
    const server = new FakeServer({ chunkSize: 1000 });
    const file = new FakeFile('hw.pdf', 3000);
    server.fail(null, 'expire');
    const seen = [];
    await uploadFile(file, options(server, { onSession: (id) => seen.push(id) }).options);
    expect(seen).toEqual(['up1', 'up2']);
    expect(server.stored(file)).toBe(true);
  });

  test('a server that keeps losing the session is not retried forever', async () => {
    const server = new FakeServer({ chunkSize: 1000 });
    server.fail('expire', 'expire', 'expire', 'expire');
    await expect(uploadFile(new FakeFile('hw.pdf', 3000), options(server).options)).rejects.toBeInstanceOf(UploadRejected);
  });
});

// --- refusals and cancelling ---

describe('refusals', () => {
  test.each([[400, 'not_pdf'], [413, 'too_big'], [429, 'too_many'], [403, 'forbidden']])(
    'a %i from the server is final: no retry, and the reason is kept', async (status, code) => {
      const server = new FakeServer({ chunkSize: 1000 });
      server.fail(apiError('refused', status, { code }));
      const { options: opts, delays } = options(server);
      const error = await uploadFile(new FakeFile('hw.pdf', 3000), opts).catch((e) => e);
      expect(error).toBeInstanceOf(UploadRejected);
      expect(error).toMatchObject({ status, code });
      expect(delays).toEqual([]);
      expect(server.requests).toHaveLength(1);
    }
  );

  test('a refusal when creating the session is final too', async () => {
    const server = new FakeServer();
    server.createFailures.push(apiError('only pdf files allowed', 400, { code: 'not_pdf' }));
    await expect(uploadFile(new FakeFile('notes.txt', 100), options(server).options)).rejects.toBeInstanceOf(UploadRejected);
  });
});

describe('cancelling', () => {
  test('aborting stops the upload and sends nothing more', async () => {
    const server = new FakeServer({ chunkSize: 1000 });
    const controller = new AbortController();
    const file = new FakeFile('hw.pdf', 5000);
    const { options: opts } = options(server, { signal: controller.signal, onProgress: (p) => { if (p.loaded >= 2000) controller.abort(); } });
    const error = await uploadFile(file, opts).catch((e) => e);
    expect(isAbort(error)).toBe(true);
    expect(server.requests).toHaveLength(2);
  });

  test('aborting while waiting to retry ends the wait immediately', async () => {
    const server = new FakeServer({ chunkSize: 1000 });
    server.fail('network');
    const controller = new AbortController();
    const wait = (ms, signal) => new Promise((resolve, reject) => {
      signal.addEventListener('abort', () => reject(Object.assign(new Error('aborted'), { name: 'AbortError' })));
      controller.abort();
    });
    const error = await uploadFile(new FakeFile('hw.pdf', 1000), { api: server.api, signal: controller.signal, wait }).catch((e) => e);
    expect(isAbort(error)).toBe(true);
  });

  test('an already-aborted signal does no work at all', async () => {
    const server = new FakeServer();
    const controller = new AbortController();
    controller.abort();
    const error = await uploadFile(new FakeFile('hw.pdf', 1000), { api: server.api, signal: controller.signal }).catch((e) => e);
    expect(isAbort(error)).toBe(true);
    expect(server.requests).toEqual([]);
  });
});

// --- several files ---

describe('uploadFiles', () => {
  test('uploads every file and reports progress across all of them', async () => {
    const server = new FakeServer({ chunkSize: 1000 });
    const files = [new FakeFile('a.pdf', 2000), new FakeFile('b.pdf', 1500)];
    const { options: opts, progress } = options(server);
    const ids = await uploadFiles(files, opts);
    expect(ids).toEqual(['up1', 'up2']);
    expect(files.every((f) => server.stored(f))).toBe(true);
    const overall = progress.map((p) => p.loaded);
    expect(overall).toEqual([...overall].sort((a, b) => a - b));            // never goes backwards
    expect(overall[overall.length - 1]).toBe(3500);
    expect(progress.every((p) => p.total === 3500 && p.fileCount === 2)).toBe(true);
    expect(progress[progress.length - 1]).toMatchObject({ fileIndex: 1, fileName: 'b.pdf', fileLoaded: 1500, fileTotal: 1500 });
  });

  test('resuming a batch re-sends nothing for files that already finished', async () => {
    const server = new FakeServer({ chunkSize: 1000 });
    const files = [new FakeFile('a.pdf', 2000), new FakeFile('b.pdf', 3000)];
    server.fail(null, null, null, 'network');                 // a.pdf completes, b.pdf breaks after one chunk
    const error = await uploadFiles(files, options(server, { retries: 0 }).options).catch((e) => e);
    expect(error).toBeInstanceOf(UploadInterrupted);
    expect(error.fileIndex).toBe(1);

    server.requests.length = 0;
    const { options: opts, progress } = options(server);
    const ids = await uploadFiles(files, opts);
    expect(ids).toEqual(['up1', 'up2']);                       // the same sessions
    expect(server.requests).toEqual([[1000, 1000], [2000, 1000]]);     // only what b.pdf was missing
    expect(progress[0].loaded).toBe(2000);                      // a.pdf counts as done from the start
    expect(files.every((f) => server.stored(f))).toBe(true);
  });

  test('an empty batch uploads nothing', async () => {
    expect(await uploadFiles([], options(new FakeServer()).options)).toEqual([]);
  });
});
