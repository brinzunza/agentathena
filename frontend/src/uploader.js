// Chunked, resumable uploads (the browser half; the server half is app/uploads.py).
//
// A file is sent in pieces, each at an explicit offset. The only progress that
// counts is what the server confirms it has stored, so the progress bar is
// honest. When something goes wrong the server's stored count is the source of
// truth: the upload picks up from there instead of starting again.
//
// This module does no network I/O itself. It talks to an `api` object
// (see uploadApi.js), which keeps every failure mode testable.

export const MAX_CHUNK_BYTES = 1024 * 1024;
export const MIN_CHUNK_BYTES = 64 * 1024;
// A small file still gets about this many steps, so its progress isn't a single jump
export const TARGET_STEPS = 20;

const MAX_RESYNCS_WITHOUT_PROGRESS = 3;
const MAX_SESSION_RECREATIONS = 2;

// The upload broke and could not be repaired by retrying; it can be resumed later
export class UploadInterrupted extends Error {
  constructor(message, { uploadId = null, loaded = 0, total = 0, cause = null } = {}) {
    super(message);
    this.name = 'UploadInterrupted';
    this.uploadId = uploadId;
    this.loaded = loaded;
    this.total = total;
    this.cause = cause;
  }
}

// The server refused the upload for a reason retrying won't change (not a PDF, too big...)
export class UploadRejected extends Error {
  constructor(message, { status = 0, code = null } = {}) {
    super(message);
    this.name = 'UploadRejected';
    this.status = status;
    this.code = code;
  }
}

export const isAbort = (error) => error?.name === 'AbortError';

const abortError = () => Object.assign(new Error('upload cancelled'), { name: 'AbortError' });

export function sleep(ms, signal) {
  return new Promise((resolve, reject) => {
    if (signal?.aborted) {
      reject(abortError());
      return;
    }
    const timer = setTimeout(() => {
      signal?.removeEventListener('abort', onAbort);
      resolve();
    }, ms);
    const onAbort = () => {
      clearTimeout(timer);
      reject(abortError());
    };
    signal?.addEventListener('abort', onAbort, { once: true });
  });
}

export function chunkSizeFor(size, serverChunk = MAX_CHUNK_BYTES) {
  const ceiling = Math.max(1, serverChunk || MAX_CHUNK_BYTES);
  const wanted = Math.ceil(size / TARGET_STEPS);
  return Math.min(ceiling, Math.max(Math.min(MIN_CHUNK_BYTES, ceiling), wanted));
}

export function formatBytes(bytes) {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(bytes < 10 * 1024 ? 1 : 0)} KB`;
  const mb = bytes / (1024 * 1024);
  return `${mb.toFixed(mb < 10 ? 1 : 0)} MB`;
}

// Network failures (no status), server trouble, timeouts and a damaged chunk are
// worth another try. Anything else (a 400, 413, 429...) is the server saying no.
const isRetryable = (error) => {
  const status = error?.status;
  return !status || status >= 500 || status === 408 || status === 422;
};

/**
 * Upload one file in chunks, resuming whatever the server already holds.
 *
 * Returns { uploadId, resumedFrom }. Throws UploadInterrupted when retries run
 * out (call again with the same file to resume), UploadRejected when the server
 * refuses the file, or an AbortError when `signal` is aborted.
 */
export async function uploadFile(file, options) {
  const {
    api,
    signal,
    onProgress = () => {},
    onSession = () => {},
    onRetry = () => {},
    retries = 5,
    backoffMs = 500,
    maxBackoffMs = 8000,
    wait = sleep,
    waitForOnline = async () => {},
    hashChunk = null,
  } = options;

  const total = file.size;
  const state = { failures: 0, uploadId: null, loaded: 0 };

  // Runs one request, retrying transient failures with backoff. Statuses in
  // `passThrough` are handed back to the caller untouched.
  const attempt = async (operation, passThrough = []) => {
    for (;;) {
      if (signal?.aborted) throw abortError();
      try {
        const result = await operation();
        state.failures = 0;
        return result;
      } catch (error) {
        if (isAbort(error) || signal?.aborted) throw isAbort(error) ? error : abortError();
        if (passThrough.includes(error?.status)) throw error;
        if (!isRetryable(error)) {
          throw new UploadRejected(error.message, { status: error.status, code: error.code });
        }
        state.failures += 1;
        if (state.failures > retries) {
          throw new UploadInterrupted(
            `upload interrupted: ${error.message}`,
            { uploadId: state.uploadId, loaded: state.loaded, total, cause: error }
          );
        }
        const delayMs = Math.min(backoffMs * 2 ** (state.failures - 1), maxBackoffMs);
        onRetry({ attempt: state.failures, retries, delayMs, error });
        await waitForOnline(signal);
        await wait(delayMs, signal);
      }
    }
  };

  const open = async () => {
    const session = await attempt(() => api.createSession({
      filename: file.name,
      size: file.size,
      lastModified: file.lastModified || 0,
    }));
    state.uploadId = session.upload_id;
    onSession(session.upload_id);
    return session;
  };

  let session = await open();
  const resumedFrom = session.received;
  let offset = session.received;
  state.loaded = offset;
  onProgress({ loaded: offset, total, resumedFrom, uploadId: session.upload_id });

  let recreations = 0;
  let stuck = 0;
  while (offset < total) {
    // This iteration's values, fixed, so a retry can't see them change underneath it
    const at = offset;
    const uploadId = session.upload_id;
    const size = chunkSizeFor(total, session.chunk_size);
    const chunk = file.slice(at, Math.min(at + size, total));
    let checksum;
    if (hashChunk) checksum = await hashChunk(chunk);

    try {
      const reply = await attempt(
        () => api.putChunk(uploadId, at, chunk, { signal, checksum }),
        [404, 409]
      );
      offset = reply.received;
      stuck = 0;
    } catch (error) {
      if (error?.status === 409) {
        // The server's count is the truth: its reply to an earlier request may
        // have been lost after the data arrived, or another tab got ahead.
        const known = Number.isFinite(error.received) ? error.received : offset;
        stuck = known === offset ? stuck + 1 : 0;
        if (stuck > MAX_RESYNCS_WITHOUT_PROGRESS) {
          throw new UploadInterrupted('upload interrupted: out of step with the server',
            { uploadId: state.uploadId, loaded: state.loaded, total, cause: error });
        }
        offset = known;
      } else if (error?.status === 404 && recreations < MAX_SESSION_RECREATIONS) {
        // The server no longer has this upload (it expired or was cleared): start a fresh session
        recreations += 1;
        session = await open();
        offset = session.received;
      } else if (error?.status === 404) {
        throw new UploadRejected('the server keeps losing this upload', { status: 404, code: 'not_found' });
      } else {
        throw error;
      }
    }
    state.loaded = offset;
    onProgress({ loaded: offset, total, resumedFrom, uploadId: session.upload_id });
  }
  return { uploadId: session.upload_id, resumedFrom };
}

/**
 * Upload several files one after another, reporting progress across all of
 * them. Files the server already has in full (from an earlier attempt) cost
 * nothing, so resuming a batch is just calling this again with the same files.
 *
 * Returns the upload ids in the same order as `files`. An UploadInterrupted
 * carries `fileIndex`, the file that was in flight.
 */
export async function uploadFiles(files, options) {
  const list = Array.from(files);
  const sent = list.map(() => 0);
  const total = list.reduce((sum, file) => sum + file.size, 0);
  const ids = [];

  for (let index = 0; index < list.length; index += 1) {
    const file = list[index];
    try {
      const { uploadId } = await uploadFile(file, {
        ...options,
        onProgress: (progress) => {
          sent[index] = progress.loaded;
          options.onProgress?.({
            loaded: sent.reduce((sum, n) => sum + n, 0),
            total,
            fileIndex: index,
            fileCount: list.length,
            fileName: file.name,
            fileLoaded: progress.loaded,
            fileTotal: file.size,
            resumedFrom: progress.resumedFrom,
            uploadId: progress.uploadId,
          });
        },
      });
      ids.push(uploadId);
    } catch (error) {
      if (error instanceof UploadInterrupted) error.fileIndex = index;
      throw error;
    }
  }
  return ids;
}
