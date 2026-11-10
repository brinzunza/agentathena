import { buildTerminalLines, terminalSummary, toolArgument } from './terminal';

const user = (content) => ({ role: 'user', content });
const reply = (steps, extra = {}) => ({ role: 'assistant', content: 'answer', steps, ...extra });

describe('toolArgument', () => {
  test('shows the query a search used, quoted', () => {
    expect(toolArgument({ tool: 'search_documents', args: { query: 'due date' } })).toBe('"due date"');
  });

  test('picks the argument that matters for each tool', () => {
    expect(toolArgument({ tool: 'calculator', args: { expression: '2+2' } })).toBe('"2+2"');
    expect(toolArgument({ tool: 'fetch_url', args: { url: 'https://example.com' } })).toBe('"https://example.com"');
    expect(toolArgument({ tool: 'read_document', args: { name: 'a.pdf' } })).toBe('"a.pdf"');
  });

  test('run_python shows its code on separate lines, not here', () => {
    expect(toolArgument({ tool: 'run_python', args: { code: 'print(1)' } })).toBe('');
  });

  test('a step with no arguments has none to show', () => {
    expect(toolArgument({ tool: 'current_datetime' })).toBe('');
    expect(toolArgument({ tool: 'list_documents', args: {} })).toBe('');
  });

  test('a very long argument is cut', () => {
    const arg = toolArgument({ tool: 'web_search', args: { query: 'x'.repeat(500) } });
    expect(arg.length).toBeLessThanOrEqual(120);
    expect(arg.endsWith('…')).toBe(true);
  });
});

describe('buildTerminalLines', () => {
  test('no messages or no steps means an empty log', () => {
    expect(buildTerminalLines([])).toEqual([]);
    expect(buildTerminalLines(undefined)).toEqual([]);
    expect(buildTerminalLines([user('hi'), { role: 'assistant', content: 'hello' }])).toEqual([]);
  });

  test('a question that needed no tools leaves no trace', () => {
    expect(buildTerminalLines([user('hi'), { role: 'assistant', content: 'hello', steps: [] }])).toEqual([]);
  });

  test('only the question that used tools is logged', () => {
    const lines = buildTerminalLines([
      user('hello'), { role: 'assistant', content: 'hi', steps: [] },
      user('what is 2+2?'), reply([{ id: 1, tool: 'calculator', args: { expression: '2+2' }, status: 'ok', result: '4' }]),
    ]);
    expect(lines.filter((l) => l.kind === 'turn').map((l) => l.text)).toEqual(['what is 2+2?']);
  });

  test('each question starts a turn, followed by the commands the agent ran', () => {
    const lines = buildTerminalLines([
      user('when is it due?'),
      reply([{ id: 1, tool: 'search_documents', args: { query: 'due date' }, status: 'ok', result: '3 passages' }]),
    ]);
    expect(lines.map((l) => l.kind)).toEqual(['turn', 'cmd', 'out', 'status']);
    expect(lines[0].text).toBe('when is it due?');
    expect(lines[1]).toMatchObject({ name: 'search_documents', arg: '"due date"' });
    expect(lines[3]).toMatchObject({ tone: 'ok', text: 'done' });
  });

  test('code the agent ran is shown line by line, then its output', () => {
    const lines = buildTerminalLines([
      reply([{ id: 1, tool: 'run_python', args: { code: 'a = 1\nprint(a)' }, status: 'ok', result: '1' }]),
    ]);
    expect(lines.filter((l) => l.kind === 'code').map((l) => l.text)).toEqual(['a = 1', 'print(a)']);
    expect(lines.find((l) => l.kind === 'out').text).toBe('1');
  });

  test('a step that is still running ends with a running line', () => {
    const lines = buildTerminalLines([reply([{ id: 1, tool: 'web_search', args: { query: 'x' }, status: 'running' }], { streaming: true })]);
    expect(lines[lines.length - 1]).toMatchObject({ kind: 'status', tone: 'run' });
  });

  test('a failure is marked as an error, and so is its output', () => {
    const lines = buildTerminalLines([reply([{ id: 1, tool: 'fetch_url', args: { url: 'u' }, status: 'error', result: 'timed out' }])]);
    expect(lines.find((l) => l.kind === 'out').tone).toBe('err');
    expect(lines[lines.length - 1]).toMatchObject({ tone: 'err', text: 'failed' });
  });

  test('code waiting for approval says so, and does not claim to be done', () => {
    const lines = buildTerminalLines([
      reply([{ id: 1, tool: 'run_python', status: 'running', permission: { id: 'p', code: 'import os', state: 'pending' } }]),
    ]);
    expect(lines.find((l) => l.kind === 'code').text).toBe('import os');
    expect(lines.some((l) => /waiting for your approval/.test(l.text || ''))).toBe(true);
  });

  test('code you refused is logged as refused, not as a plain failure', () => {
    const lines = buildTerminalLines([
      reply([{ id: 1, tool: 'run_python', status: 'denied', permission: { id: 'p', code: 'x', state: 'denied' }, result: 'not run' }]),
    ]);
    expect(lines.some((l) => l.text === 'you did not allow this code to run')).toBe(true);
    expect(lines.filter((l) => l.text === 'not allowed')).toHaveLength(0);
  });

  test('long output is cut with a note saying how much was left out', () => {
    const result = Array.from({ length: 40 }, (_, i) => `row ${i}`).join('\n');
    const lines = buildTerminalLines([reply([{ id: 1, tool: 'read_document', args: { name: 'a' }, status: 'ok', result }])]);
    expect(lines.filter((l) => l.kind === 'out')).toHaveLength(14);
    expect(lines.some((l) => l.text === '… 26 more lines')).toBe(true);
  });

  test('how long a finished reply took is the last line of its turn', () => {
    const lines = buildTerminalLines([reply([{ id: 1, tool: 'calculator', args: { expression: '1' }, status: 'ok' }], { response_time: 2.5 })]);
    expect(lines[lines.length - 1]).toMatchObject({ kind: 'dim', text: 'finished in 2.50s' });
  });

  test('a reply that is still streaming has no finish line yet', () => {
    const lines = buildTerminalLines([reply([{ id: 1, tool: 'calculator', status: 'ok' }], { response_time: 1, streaming: true })]);
    expect(lines.some((l) => /finished/.test(l.text || ''))).toBe(false);
  });

  test('every line has a unique key', () => {
    const lines = buildTerminalLines([
      user('a'), reply([{ id: 1, tool: 'run_python', args: { code: 'a\nb' }, status: 'ok', result: 'x\ny' }, { id: 2, tool: 'calculator', status: 'ok' }]),
      user('b'), reply([{ id: 1, tool: 'calculator', status: 'ok' }]),
    ]);
    const keys = lines.map((l) => l.key);
    expect(new Set(keys).size).toBe(keys.length);
  });

  test('the user text is collapsed to one line and cut', () => {
    const [line] = buildTerminalLines([user(`first\n\nsecond ${'y'.repeat(300)}`), reply([{ id: 1, tool: 'calculator', status: 'ok' }])]);
    expect(line.text.includes('\n')).toBe(false);
    expect(line.text.length).toBeLessThanOrEqual(140);
  });
});

describe('terminalSummary', () => {
  test('counts the steps and notices one that is running', () => {
    const messages = [reply([{ id: 1, status: 'ok' }, { id: 2, status: 'running' }]), reply([{ id: 1, status: 'ok' }])];
    expect(terminalSummary(messages)).toEqual({ steps: 3, running: true });
  });

  test('a pending approval counts as running: the agent is waiting', () => {
    expect(terminalSummary([reply([{ id: 1, status: 'ok', permission: { state: 'pending' } }])]).running).toBe(true);
  });

  test('nothing yet', () => {
    expect(terminalSummary([user('hi')])).toEqual({ steps: 0, running: false });
    expect(terminalSummary(undefined)).toEqual({ steps: 0, running: false });
  });
});
