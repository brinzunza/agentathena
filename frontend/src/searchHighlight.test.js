import { splitHighlight } from './searchHighlight';

const join = (parts) => parts.map((part) => part.text).join('');
const hits = (parts) => parts.filter((part) => part.hit).map((part) => part.text);

test('marks every occurrence of the query, keeping the original casing', () => {
  const parts = splitHighlight('Paris is in France. I love paris.', 'paris');
  expect(hits(parts)).toEqual(['Paris', 'paris']);
  expect(join(parts)).toBe('Paris is in France. I love paris.');
});

test('every word of the query is marked, longer words first so overlaps stay whole', () => {
  expect(hits(splitHighlight('red apples and a red apple', 'apple apples red'))).toEqual(['red', 'apples', 'red', 'apple']);
});

test('characters that mean something in a regex are matched literally', () => {
  const parts = splitHighlight('is 2+2 (really) 4? a.b', '2+2 (really) 4? a.b');
  expect(hits(parts)).toEqual(['2+2', '(really)', '4?', 'a.b']);
  expect(join(parts)).toBe('is 2+2 (really) 4? a.b');
});

test('a blank query or empty text marks nothing', () => {
  expect(splitHighlight('hello', '   ')).toEqual([{ text: 'hello', hit: false }]);
  expect(splitHighlight('', 'x')).toEqual([{ text: '', hit: false }]);
  expect(splitHighlight(undefined, 'x')).toEqual([{ text: '', hit: false }]);
});

test('text with no match comes back whole and unmarked', () => {
  expect(splitHighlight('nothing here', 'zzz')).toEqual([{ text: 'nothing here', hit: false }]);
});
