// Cuts `text` into pieces marked as matching the search query or not, so the
// matching words can be highlighted. Every word of the query counts, any case.
export function splitHighlight(text, query) {
  const terms = [...new Set(query.toLowerCase().split(/\s+/).filter(Boolean))].sort((a, b) => b.length - a.length);
  if (!terms.length || !text) return [{ text: text || '', hit: false }];
  const pattern = new RegExp(`(${terms.map((term) => term.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')).join('|')})`, 'gi');
  return text
    .split(pattern)
    .filter((piece) => piece !== '')
    .map((piece) => ({ text: piece, hit: terms.includes(piece.toLowerCase()) }));
}
