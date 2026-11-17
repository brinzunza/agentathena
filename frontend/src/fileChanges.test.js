import { splitReply, asFence, shouldFold, describeCode, withoutCode, FOLD_AT_LINES } from './codeBlocks';
import { diffRows, filesHeading, statusOf, totals } from './fileChanges';

describe('splitReply', () => {
  it('leaves a reply with no code as one piece of text', () => {
    expect(splitReply('just words\n\nmore words')).toEqual([{ type: 'text', text: 'just words\n\nmore words' }]);
  });

  it('cuts text, code and text apart', () => {
    const parts = splitReply('before\n```python\nprint(1)\nprint(2)\n```\nafter');
    expect(parts.map((p) => p.type)).toEqual(['text', 'code', 'text']);
    expect(parts[1]).toEqual({ type: 'code', lang: 'python', code: 'print(1)\nprint(2)', lines: 2, open: false });
  });

  it('calls a block that is never closed open: it is still being written', () => {
    const parts = splitReply('here it is\n```js\nconst a = 1;\nconst b');
    expect(parts[1]).toMatchObject({ type: 'code', lang: 'js', lines: 2, open: true });
  });

  it('does not count the newline after the last row as a line', () => {
    expect(splitReply('```py\na\nb\n')[0]).toMatchObject({ lines: 2, open: true });
  });

  it('handles several blocks and a block with no language', () => {
    const parts = splitReply('```\none\n```\nmiddle\n```sh\ntwo\n```');
    expect(parts.map((p) => p.type === 'code' ? p.lang : p.type)).toEqual(['', 'text', 'sh']);
  });

  it('is not fooled by backticks inside a line', () => {
    expect(splitReply('use ```inline``` here')).toEqual([{ type: 'text', text: 'use ```inline``` here' }]);
  });

  it('copes with an empty or missing reply', () => {
    expect(splitReply('')).toEqual([]);
    expect(splitReply(undefined)).toEqual([]);
  });
});

describe('folding finished code', () => {
  const block = (lines) => ({ type: 'code', lang: 'python', code: 'x\n'.repeat(lines).trim(), lines, open: false });

  it('folds long blocks and leaves short ones', () => {
    expect(shouldFold(block(FOLD_AT_LINES))).toBe(true);
    expect(shouldFold(block(FOLD_AT_LINES - 1))).toBe(false);
  });

  it('says what a block is in a few words', () => {
    expect(describeCode(block(42))).toBe('python · 42 lines');
    expect(describeCode({ ...block(1), lang: '' })).toBe('code · 1 line');
  });

  it('turns a block back into markdown', () => {
    expect(asFence({ lang: 'js', code: 'a()' })).toBe('```js\na()\n```');
  });
});

describe('withoutCode', () => {
  it('swaps code for a note about it', () => {
    expect(withoutCode('Here:\n```python\na = 1\nb = 2\n```\nDone.')).toBe('Here: (python · 2 lines) Done.');
  });

  it('does the same for code still being written', () => {
    expect(withoutCode('Here:\n```py\na = 1')).toBe('Here: (py · 1 line)');
  });

  it('leaves plain text alone', () => {
    expect(withoutCode('no code here')).toBe('no code here');
  });
});

describe('the changed files list', () => {
  it('names each status', () => {
    expect(statusOf({ status: 'created' }).label).toBe('new');
    expect(statusOf({ status: 'modified' }).label).toBe('edited');
    expect(statusOf({ status: 'odd' }).label).toBe('odd');
  });

  it('adds up lines across files', () => {
    expect(totals([{ added: 3, removed: 1 }, { added: 2 }, {}])).toEqual({ added: 5, removed: 1 });
    expect(totals(undefined)).toEqual({ added: 0, removed: 0 });
  });

  it('counts files in a sentence', () => {
    expect(filesHeading([{}])).toBe('1 file changed');
    expect(filesHeading([{}, {}])).toBe('2 files changed');
  });

  it('turns a diff into rows by what each line did', () => {
    const rows = diffRows('@@ -1,2 +1,2 @@\n a\n-b\n+B');
    expect(rows.map((r) => r.kind)).toEqual(['hunk', 'context', 'remove', 'add']);
    expect(rows[2]).toMatchObject({ text: 'b', sign: '−' });
    expect(rows[3]).toMatchObject({ text: 'B', sign: '+' });
  });
});
