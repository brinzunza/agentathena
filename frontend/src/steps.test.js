import { STEP_STATUS, PERMISSION_TEXT, permissionText, statusLabel, applyStepResult, settleStep } from './steps';
import { buildTerminalLines } from './terminal';

describe('the step status table', () => {
  test('every status has a word for the step list and a line for the terminal', () => {
    Object.values(STEP_STATUS).forEach((shown) => {
      expect(shown.label).toBeTruthy();
      expect(shown.line).toBeTruthy();
      expect(['run', 'ok', 'err', 'dim']).toContain(shown.tone);
    });
    // running is shown by its live dot; every other state has a mark
    const unmarked = Object.entries(STEP_STATUS).filter(([name, shown]) => name !== 'running' && !shown.mark);
    expect(unmarked).toEqual([]);
  });

  test('an unknown status shows as itself, never as nothing', () => {
    expect(statusLabel('exploded')).toBe('exploded');
    expect(statusLabel(undefined)).toBe('undefined');
    expect(statusLabel('ok')).toBe('done');
  });

  test('the step list and the terminal agree on every status', () => {
    Object.entries(STEP_STATUS).filter(([name]) => name !== 'running').forEach(([name, shown]) => {
      const lines = buildTerminalLines([{ role: 'assistant', steps: [{ id: 1, tool: 'calculator', status: name }] }]);
      const last = lines[lines.length - 1];
      expect(last).toMatchObject({ kind: 'status', text: shown.line, tone: shown.tone });
    });
  });
});

describe('applyStepResult', () => {
  const waiting = { id: 1, tool: 'run_python', status: 'running', permission: { id: 'p', code: 'x', state: 'pending' } };

  test('a result that arrives while the prompt is unanswered closes the prompt', () => {
    // the server only sends one after it has stopped waiting (it times out), so the buttons must go
    const step = applyStepResult(waiting, { status: 'denied', result: 'the user did not allow this code to run' });
    expect(step.status).toBe('denied');
    expect(step.permission.state).toBe('expired');
    expect(step.result).toMatch(/did not allow/);
  });

  test('an answered prompt keeps the answer', () => {
    const approved = { ...waiting, permission: { ...waiting.permission, state: 'approved' } };
    expect(applyStepResult(approved, { status: 'ok', result: '1' }).permission.state).toBe('approved');
    const denied = { ...waiting, permission: { ...waiting.permission, state: 'denied' } };
    expect(applyStepResult(denied, { status: 'denied', result: 'no' }).permission.state).toBe('denied');
  });

  test('a step with no prompt gains none', () => {
    const step = applyStepResult({ id: 2, tool: 'calculator', status: 'running' }, { status: 'ok', result: '4' });
    expect(step).toEqual({ id: 2, tool: 'calculator', status: 'ok', result: '4', permission: undefined });
  });

  test('it does not change the step it was given', () => {
    const before = JSON.stringify(waiting);
    applyStepResult(waiting, { status: 'ok', result: '' });
    expect(JSON.stringify(waiting)).toBe(before);
  });
});

describe('settleStep', () => {
  test('a running step is saved as stopped, and an unanswered prompt as expired', () => {
    const step = settleStep({ id: 1, status: 'running', permission: { id: 'p', state: 'pending' } });
    expect(step.status).toBe('stopped');
    expect(step.permission.state).toBe('expired');
  });

  test('finished steps are left as they are', () => {
    const done = { id: 1, status: 'ok', permission: { id: 'p', state: 'approved' } };
    expect(settleStep(done)).toEqual(done);
  });
});

describe('permissionText', () => {
  const ask = (kind, state) => permissionText({ kind, state });

  test('running code is asked about, answered and logged in the words it always used', () => {
    expect(ask('code', 'pending')).toBe('athena wants to run this code on your computer');
    expect(ask('code', 'approved')).toBe('you allowed this code to run');
    expect(ask('code', 'denied')).toBe('you did not allow this code to run');
    expect(permissionText({ kind: 'code', state: 'pending' }, 'waiting')).toBe('waiting for your approval to run this code…');
  });

  test('saving a file says file, not code, in every state', () => {
    expect(ask('save', 'pending')).toBe('athena wants to save this file on your computer');
    expect(ask('save', 'approved')).toBe('you allowed this file to be saved');
    expect(ask('save', 'denied')).toBe('you did not allow this file to be saved');
    expect(permissionText({ kind: 'save', state: 'pending' }, 'waiting')).toBe('waiting for your approval to save this file…');
  });

  test('an expired request reads the same for both kinds', () => {
    expect(ask('save', 'expired')).toBe('this request expired');
    expect(ask('code', 'expired')).toBe('this request expired');
  });

  test('a request saved before kinds existed has none, and reads as code', () => {
    expect(permissionText({ state: 'approved' })).toBe('you allowed this code to run');
    expect(permissionText({ kind: 'mystery', state: 'pending' })).toBe('athena wants to run this code on your computer');
  });

  test('every kind has all four phrases', () => {
    Object.values(PERMISSION_TEXT).forEach((words) => {
      expect(Object.keys(words).sort()).toEqual(['approved', 'ask', 'denied', 'waiting']);
    });
  });
});

describe('how a step came to run without asking', () => {
  test('the result carries it onto the step', () => {
    const step = applyStepResult({ id: 1, status: 'running' }, { status: 'ok', result: '1', approval: 'chat' });
    expect(step.approval).toBe('chat');
  });

  test('a result without one leaves the step without one', () => {
    expect('approval' in applyStepResult({ id: 1, status: 'running' }, { status: 'ok', result: '1' })).toBe(false);
  });
});
