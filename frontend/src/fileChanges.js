// The list of files a reply created or changed, as the server sends it (app/file_changes.py): each entry has a
// path, a status (created, modified or deleted), how many lines were added and removed, and a unified diff
// (or a note saying why there isn't one).

export const FILE_STATUS = {
  created: { label: 'new', tone: 'ok' },
  modified: { label: 'edited', tone: 'run' },
  deleted: { label: 'deleted', tone: 'err' },
};

export const statusOf = (file) => FILE_STATUS[file.status] || { label: String(file.status), tone: 'dim' };

export function totals(files) {
  return (files || []).reduce(
    (sum, file) => ({ added: sum.added + (file.added || 0), removed: sum.removed + (file.removed || 0) }),
    { added: 0, removed: 0 },
  );
}

export const filesHeading = (files) => `${files.length} ${files.length === 1 ? 'file' : 'files'} changed`;

// A diff as rows to draw: a hunk header, an added line, a removed line, or one for context
export function diffRows(diff) {
  return String(diff || '').split('\n').map((text, index) => {
    let kind = 'context';
    if (text.startsWith('@@')) kind = 'hunk';
    else if (text.startsWith('+')) kind = 'add';
    else if (text.startsWith('-')) kind = 'remove';
    return { key: index, kind, text: kind === 'hunk' ? text : text.slice(1) || ' ', sign: { add: '+', remove: '−' }[kind] || '' };
  });
}
