// Code in a reply is not shown as it is written. While the model writes it, the reply says it is building;
// once it is done, a short snippet reads as normal and a long block folds away behind a one-line summary.
// The files it ended up in are listed at the end of the reply (FileChanges.js).
//
// `splitReply` cuts a reply's text into plain-text and fenced-code segments. A fence that is never closed
// is a block still being written (or one a cut-off reply lost the end of): it comes back with open: true.

// A line of code this long or longer is folded in a finished reply; shorter ones stay where they are
export const FOLD_AT_LINES = 8;

// Three backticks at the start of a line (up to three spaces in), then the language
const FENCE = /^ {0,3}```[ \t]*([\w+#.-]*)[^\n]*$/;

export function splitReply(content) {
  const segments = [];
  let text = [];
  let code = null;       // the block being read: { lang, rows }

  const flushText = () => {
    if (text.length) segments.push({ type: 'text', text: text.join('\n') });
    text = [];
  };

  const rows = String(content || '').split('\n');
  rows.forEach((row) => {
    if (code) {
      if (/^ {0,3}```\s*$/.test(row)) {
        segments.push({ type: 'code', lang: code.lang, code: code.rows.join('\n'), lines: code.rows.length, open: false });
        code = null;
      } else {
        code.rows.push(row);
      }
      return;
    }
    const opening = FENCE.exec(row);
    if (opening) {
      flushText();
      code = { lang: opening[1] || '', rows: [] };
      return;
    }
    text.push(row);
  });

  if (code) {
    // A trailing blank row is just the newline before the text that hasn't arrived yet
    const written = code.rows.length && code.rows[code.rows.length - 1] === '' ? code.rows.slice(0, -1) : code.rows;
    segments.push({ type: 'code', lang: code.lang, code: written.join('\n'), lines: written.length, open: true });
  }
  flushText();
  return segments.filter((segment) => segment.type === 'code' || segment.text.trim() !== '');
}

// A finished block as markdown again, so the ordinary renderer can show it
export function asFence(segment) {
  return `\`\`\`${segment.lang}\n${segment.code}\n\`\`\``;
}

export const shouldFold = (segment) => segment.lines >= FOLD_AT_LINES;

export function describeCode(segment) {
  const what = segment.lang || 'code';
  return `${what} · ${segment.lines} ${segment.lines === 1 ? 'line' : 'lines'}`;
}

// Text with its code swapped for a short note, for places that only have room for a line or two
export function withoutCode(content) {
  return splitReply(content)
    .map((segment) => (segment.type === 'text' ? segment.text.trim() : `(${describeCode(segment)})`))
    .join(' ');
}
