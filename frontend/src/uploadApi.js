// The real network side of uploader.js: talks to the chunked-upload endpoints.
// Failures are turned into errors with `status`, `code` and (for a 409) the
// server's stored `received` count, which is what the uploader reasons about.
// A request that never got an answer has no `status`, which the uploader
// treats as a connection problem worth retrying.

async function call(url, options = {}) {
  let response;
  try {
    response = await fetch(url, options);
  } catch (error) {
    if (error?.name === 'AbortError') throw error;
    throw Object.assign(new Error("couldn't reach the server"), { cause: error });
  }

  if (!response.ok) {
    let detail;
    try {
      detail = (await response.json()).detail;
    } catch (_) { /* no readable body */ }
    const message = (typeof detail === 'string' && detail) || detail?.message || `the server returned an error (${response.status})`;
    throw Object.assign(new Error(message), { status: response.status, code: detail?.code, received: detail?.received });
  }
  return response.json();
}

const json = (body) => ({
  method: 'POST',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify(body),
});

// SHA-256 of a chunk as hex, so the server can spot a chunk damaged in transit.
// Only available in secure contexts (https and localhost); without it chunks go unchecked.
export async function sha256Hex(blob) {
  const subtle = typeof crypto !== 'undefined' ? crypto.subtle : undefined;
  if (!subtle || typeof blob.arrayBuffer !== 'function') return undefined;
  const digest = await subtle.digest('SHA-256', await blob.arrayBuffer());
  return Array.from(new Uint8Array(digest), (byte) => byte.toString(16).padStart(2, '0')).join('');
}

// Resolves once the browser reports it is back online (immediately if it already is)
export function waitForOnline(signal) {
  if (typeof navigator === 'undefined' || navigator.onLine !== false) return Promise.resolve();
  return new Promise((resolve, reject) => {
    const done = () => {
      window.removeEventListener('online', done);
      signal?.removeEventListener('abort', cancelled);
      resolve();
    };
    const cancelled = () => {
      window.removeEventListener('online', done);
      reject(Object.assign(new Error('upload cancelled'), { name: 'AbortError' }));
    };
    window.addEventListener('online', done, { once: true });
    signal?.addEventListener('abort', cancelled, { once: true });
  });
}

export function createUploadApi(base, chatId) {
  const root = `${base}/chats/${chatId}/uploads`;
  return {
    createSession: ({ filename, size, lastModified }) =>
      call(root, json({ filename, size, last_modified: lastModified })),

    putChunk: (uploadId, offset, chunk, { signal, checksum } = {}) =>
      call(`${root}/${uploadId}?offset=${offset}`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/octet-stream', ...(checksum ? { 'X-Chunk-SHA256': checksum } : {}) },
        body: chunk,
        signal,
      }),

    process: (uploadIds) => call(`${root}/process`, json({ upload_ids: uploadIds })),

    abort: (uploadId) => call(`${root}/${uploadId}`, { method: 'DELETE' }),

    listUnfinished: async () => (await call(root)).uploads,
  };
}
