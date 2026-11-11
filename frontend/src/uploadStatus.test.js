import {
  UPLOADING, INTERRUPTED, CONVERTING, INDEXING, CLEANUP, COMPLETED, ERROR, PROCESSING,
  isTransferring, isFinished, isWorking, pollFailureOutcome, MAX_POLL_FAILURES,
} from './uploadStatus';

describe('upload states', () => {
  test('only the server\'s three processing stages count as processing', () => {
    expect([...PROCESSING].sort()).toEqual([CLEANUP, CONVERTING, INDEXING].sort());
  });

  test('bytes are moving, or could move again, only while uploading or interrupted', () => {
    expect([UPLOADING, INTERRUPTED].every(isTransferring)).toBe(true);
    expect([CONVERTING, INDEXING, CLEANUP, COMPLETED, ERROR].some(isTransferring)).toBe(false);
  });

  test('completed and error are the only finished states', () => {
    expect([COMPLETED, ERROR].every(isFinished)).toBe(true);
    expect([UPLOADING, INTERRUPTED, CONVERTING, INDEXING, CLEANUP].some(isFinished)).toBe(false);
  });

  test('an interrupted upload is not working: it waits for you', () => {
    expect(isWorking(INTERRUPTED)).toBe(false);
    expect([UPLOADING, CONVERTING, INDEXING, CLEANUP].every(isWorking)).toBe(true);
    expect(isWorking(COMPLETED) || isWorking(ERROR)).toBe(false);
  });

  test('every state is exactly one of: finished, waiting for you, or working', () => {
    [UPLOADING, INTERRUPTED, CONVERTING, INDEXING, CLEANUP, COMPLETED, ERROR].forEach((status) => {
      const kinds = [isFinished(status), status === INTERRUPTED, isWorking(status)].filter(Boolean);
      expect(kinds).toHaveLength(1);
    });
  });
});

describe('pollFailureOutcome', () => {
  const notFound = { response: { status: 404 } };

  test('a job the server no longer knows is given up on at once, and says why', () => {
    const outcome = pollFailureOutcome(notFound, 1);
    expect(outcome.giveUp).toBe(true);
    expect(outcome.message).toMatch(/restarted/);
    expect(outcome.message).toMatch(/upload any that are missing/);
  });

  test('a dropped connection is retried, not given up on', () => {
    expect(pollFailureOutcome(new Error('Network Error'), 1)).toEqual({ giveUp: false, message: '' });
    expect(pollFailureOutcome({ response: { status: 502 } }, MAX_POLL_FAILURES - 1).giveUp).toBe(false);
  });

  test('after enough failures in a row it gives up and says processing may still finish', () => {
    const outcome = pollFailureOutcome(new Error('Network Error'), MAX_POLL_FAILURES);
    expect(outcome.giveUp).toBe(true);
    expect(outcome.message).toMatch(/may still finish/);
  });

  test('the two ways of giving up say different things', () => {
    expect(pollFailureOutcome(notFound, 1).message).not.toBe(pollFailureOutcome(new Error('x'), MAX_POLL_FAILURES).message);
  });
});
