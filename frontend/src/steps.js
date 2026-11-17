// Every state a step (one tool call by the agent) can be in, and how each one is shown.
//
// The server reports how a call ended: ok, error, denied or skipped (STEP_STATUSES in app/agent.py;
// a test keeps the two in step). The browser adds two of its own: running, from the moment the step
// starts until its result arrives, and stopped, for a run that was cut off. The step list and the
// terminal both read this table, so a new state is added here once.

export const STEP_STATUS = {
  running: { label: 'working…', line: 'running…', tone: 'run' },
  ok: { label: 'done', line: 'done', tone: 'ok', mark: '✓' },
  error: { label: 'failed', line: 'failed', tone: 'err', mark: '✕' },
  denied: { label: 'not allowed', line: 'not allowed', tone: 'err', mark: '✕' },
  stopped: { label: 'stopped', line: 'stopped', tone: 'dim', mark: '■' },
  skipped: { label: 'redirected', line: 'redirected', tone: 'dim', mark: '↷' },
};

// The word shown beside a step; an unknown status shows as itself rather than as nothing
export const statusLabel = (status) => STEP_STATUS[status]?.label ?? String(status);

// A step whose result has arrived. If its code was still waiting for an answer, the server has
// stopped waiting (it times out after a while), so the prompt can no longer be answered.
export function applyStepResult(step, { status, result, approval }) {
  const waiting = step.permission?.state === 'pending';
  return {
    ...step,
    status,
    result,
    ...(approval ? { approval } : {}),
    permission: waiting ? { ...step.permission, state: 'expired' } : step.permission,
  };
}

// A step cut off before it finished (the run was cancelled or the page was left) is saved as
// stopped, and a prompt nobody answered as expired, so a reload doesn't show them live forever.
export function settleStep(step) {
  return {
    ...step,
    status: step.status === 'running' ? 'stopped' : step.status,
    permission: step.permission?.state === 'pending' ? { ...step.permission, state: 'expired' } : step.permission,
  };
}

// How a call that needs approval went ahead without being put to the user. The server says which (APPROVAL_NOTES in
// app/approvals.py; a test keeps the two in step); this is what the step list and the terminal say about it.
export const APPROVAL_NOTE = {
  chat: 'ran without asking: you allowed this for the chat',
  bypass: 'ran without asking: this chat is in bypass mode',
};

// What an approval request says, by what is being approved: `ask` is the prompt shown to the user,
// `waiting` the line in the terminal while nothing has been answered.
export const PERMISSION_TEXT = {
  code: {
    ask: 'athena wants to run this code on your computer',
    waiting: 'waiting for your approval to run this code…',
    approved: 'you allowed this code to run',
    denied: 'you did not allow this code to run',
  },
  save: {
    ask: 'athena wants to save this file on your computer',
    waiting: 'waiting for your approval to save this file…',
    approved: 'you allowed this file to be saved',
    denied: 'you did not allow this file to be saved',
  },
};

// The words for a request in its current state. `pendingWording` picks which of the two pending
// phrasings applies ('ask' or 'waiting'). A request of an unknown kind reads as code, the original kind.
export function permissionText(permission, pendingWording = 'ask') {
  if (permission.state === 'expired') return 'this request expired';
  const words = PERMISSION_TEXT[permission.kind] || PERMISSION_TEXT.code;
  return permission.state === 'pending' ? words[pendingWording] : words[permission.state];
}
