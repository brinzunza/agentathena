// The states of an upload as the queue shows it, in one place.
//
// The browser owns the first two: uploading (the bytes are going to the server) and interrupted (the
// connection broke; it can be resumed). Once every byte is stored the server takes over and reports
// converting, indexing and cleanup, then completed or error (app/processing_status.py has the same
// names; a test keeps the two in step).
//
//   uploading <-> interrupted
//   uploading -> converting -> indexing -> cleanup -> completed
//                        any processing state -> error

export const UPLOADING = 'uploading';
export const INTERRUPTED = 'interrupted';
export const CONVERTING = 'converting';
export const INDEXING = 'indexing';
export const CLEANUP = 'cleanup';
export const COMPLETED = 'completed';
export const ERROR = 'error';

export const PROCESSING = new Set([CONVERTING, INDEXING, CLEANUP]);

// Bytes are still moving, or could be once the upload is resumed
export const isTransferring = (status) => status === UPLOADING || status === INTERRUPTED;
// Nothing more will happen to this upload
export const isFinished = (status) => status === COMPLETED || status === ERROR;
// Something is working on it right now
export const isWorking = (status) => !isFinished(status) && status !== INTERRUPTED;

// How many failed progress checks in a row before the browser stops asking
export const MAX_POLL_FAILURES = 5;

/**
 * What to do after a failed check on a processing job.
 *
 * The server keeps processing jobs in memory, so a 404 means it no longer knows the job (it
 * restarted): asking again will never help, and the files may not have been indexed. Any other
 * failure is treated as a dropped connection and retried a few times, because the job itself
 * carries on regardless of whether anyone is watching.
 *
 * Returns { giveUp, message }. `message` is what to tell the user when giving up.
 */
export function pollFailureOutcome(error, failuresInARow) {
  if (error?.response?.status === 404) {
    return {
      giveUp: true,
      message: 'lost track of the processing of your documents. the backend was probably restarted. '
        + 'check the documents list, and upload any that are missing again.',
    };
  }
  if (failuresInARow >= MAX_POLL_FAILURES) {
    return {
      giveUp: true,
      message: 'lost the connection to the backend while your documents were processing. '
        + 'they may still finish: check the documents list in a moment.',
    };
  }
  return { giveUp: false, message: '' };
}
