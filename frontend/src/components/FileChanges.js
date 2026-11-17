import React, { useState } from 'react';
import { ChevronRightIcon } from './Icons';
import { diffRows, filesHeading, statusOf, totals } from '../fileChanges';

function Diff({ file }) {
  if (!file.diff) {
    return <div className="diff-note">{file.note || 'no changes to show'}</div>;
  }
  return (
    <div className="diff" role="region" aria-label={`changes to ${file.path}`}>
      {diffRows(file.diff).map((row) => (
        <div key={row.key} className={`diff-row diff-${row.kind}`}>
          <span className="diff-sign" aria-hidden="true">{row.sign}</span>
          <span className="diff-text">{row.text}</span>
        </div>
      ))}
    </div>
  );
}

// The files a reply created or changed. Each one is a button: click it to read what changed in it.
function FileChanges({ files }) {
  const [openPath, setOpenPath] = useState(null);
  const sum = totals(files);
  return (
    <div className="file-changes">
      <div className="file-changes-head">
        <span className="file-changes-title">{filesHeading(files)}</span>
        <span className="file-changes-total">
          {sum.added > 0 && <span className="diff-count-add">+{sum.added}</span>}
          {sum.removed > 0 && <span className="diff-count-remove">−{sum.removed}</span>}
        </span>
      </div>
      <ul className="file-list">
        {files.map((file) => {
          const open = openPath === file.path;
          const status = statusOf(file);
          return (
            <li key={file.path} className={open ? 'file-item is-open' : 'file-item'}>
              <button
                type="button"
                className="file-row"
                onClick={() => setOpenPath(open ? null : file.path)}
                aria-expanded={open}
                title={open ? 'hide the changes' : 'see what changed'}
              >
                <span className={`arrow ${open ? 'expanded' : ''}`}><ChevronRightIcon /></span>
                <span className={`file-status file-status-${status.tone}`}>{status.label}</span>
                <span className="file-path">{file.path}</span>
                <span className="file-counts">
                  {file.added > 0 && <span className="diff-count-add">+{file.added}</span>}
                  {file.removed > 0 && <span className="diff-count-remove">−{file.removed}</span>}
                </span>
              </button>
              {open && <Diff file={file} />}
            </li>
          );
        })}
      </ul>
    </div>
  );
}

export default FileChanges;
