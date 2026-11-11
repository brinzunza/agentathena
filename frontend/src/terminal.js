// The terminal panel is a read-only log of what the agent did, drawn from the chat's own messages:
// each question, then every tool the agent called, the code it ran, and what came back. Nothing here
// runs anything. `buildTerminalLines` turns messages into plain line objects the panel renders.

import { STEP_STATUS } from './steps';

export const TERMINAL_KEY = 'athena-terminal';

const MAX_RESULT_LINES = 14;
const MAX_CODE_LINES = 60;
const MAX_LINE_CHARS = 220;

const clip = (text, limit = MAX_LINE_CHARS) => {
  const value = String(text);
  return value.length <= limit ? value : `${value.slice(0, limit - 1).trimEnd()}…`;
};

// The one argument worth showing next to a tool's name
export function toolArgument(step) {
  const args = step.args || {};
  if (step.tool === 'run_python') return '';
  const value = args.query ?? args.expression ?? args.url ?? args.name ?? args.topic ?? args.fact;
  return value ? clip(JSON.stringify(String(value)), 120) : '';
}

function permissionLine(permission) {
  if (permission.state === 'pending') return { text: 'waiting for your approval to run this code…', tone: 'run' };
  if (permission.state === 'approved') return { text: 'you allowed this code to run', tone: 'dim' };
  if (permission.state === 'denied') return { text: 'you did not allow this code to run', tone: 'err' };
  return { text: 'the request expired', tone: 'dim' };
}

// Lines for one step, as { kind, ... } objects. `key` makes them stable across re-renders.
function stepLines(step, prefix) {
  const lines = [];
  const push = (line) => lines.push({ key: `${prefix}:${lines.length}`, ...line });

  if (step.thought) push({ kind: 'note', text: clip(step.thought, 300) });
  push({ kind: 'cmd', name: step.tool, arg: toolArgument(step) });

  const code = step.permission?.code || (step.tool === 'run_python' ? step.args?.code : '');
  if (code) {
    const rows = String(code).split('\n');
    rows.slice(0, MAX_CODE_LINES).forEach((row) => push({ kind: 'code', text: row }));
    if (rows.length > MAX_CODE_LINES) push({ kind: 'dim', text: `… ${rows.length - MAX_CODE_LINES} more lines` });
  }
  if (step.permission) {
    const { text, tone } = permissionLine(step.permission);
    push({ kind: 'status', tone, text });
  }

  if (step.result) {
    const rows = String(step.result).split('\n').filter((row, i, all) => row.trim() || i < all.length - 1);
    const tone = step.status === 'error' || step.status === 'denied' ? 'err' : 'out';
    rows.slice(0, MAX_RESULT_LINES).forEach((row) => push({ kind: 'out', tone, text: clip(row) }));
    if (rows.length > MAX_RESULT_LINES) push({ kind: 'dim', text: `… ${rows.length - MAX_RESULT_LINES} more lines` });
  }

  const shown = STEP_STATUS[step.status];
  if (step.status === 'running') {
    push({ kind: 'status', tone: shown.tone, text: shown.line });
  } else if (shown && !(step.permission && step.permission.state !== 'approved')) {
    push({ kind: 'status', tone: shown.tone, mark: shown.mark, text: shown.line });
  }
  return lines;
}

export function buildTerminalLines(messages) {
  const lines = [];
  // A question is only logged once the agent used tools for it, so plain chat leaves no trace
  let question = null;
  (messages || []).forEach((message, index) => {
    if (message.role === 'user') {
      question = { key: `m${index}`, kind: 'turn', text: clip(String(message.content).replace(/\s+/g, ' '), 140) };
    } else if (message.role === 'assistant' && message.steps?.length) {
      if (question) lines.push(question);
      question = null;
      message.steps.forEach((step) => {
        lines.push(...stepLines(step, `m${index}:s${step.id}`));
      });
      if (!message.streaming && message.response_time != null) {
        lines.push({ key: `m${index}:end`, kind: 'dim', text: `finished in ${message.response_time.toFixed(2)}s` });
      }
    }
  });
  return lines;
}

// How many tool calls the log holds, and whether any is still going
export function terminalSummary(messages) {
  let steps = 0;
  let running = false;
  (messages || []).forEach((message) => {
    (message.role === 'assistant' ? message.steps || [] : []).forEach((step) => {
      steps += 1;
      if (step.status === 'running' || step.permission?.state === 'pending') running = true;
    });
  });
  return { steps, running };
}

export function readTerminalOpen() {
  try {
    return window.localStorage.getItem(TERMINAL_KEY) === 'open';
  } catch (error) {
    return false;
  }
}

export function saveTerminalOpen(open) {
  try {
    window.localStorage.setItem(TERMINAL_KEY, open ? 'open' : 'closed');
  } catch (error) {
    // not remembered; it still works for this session
  }
}
