import React, { useEffect, useMemo, useState } from 'react';
import { CloseIcon } from './Icons';
import { branchCandidates } from '../branches';

const MODES = [
  { id: 'pick', label: 'pick answers', note: 'bring chosen answers, with their questions, into the main chat and its memory' },
  { id: 'summary', label: 'merge a summary', note: 'add one summary of what this branch found since it started' },
  { id: 'replace', label: 'replace the main chat', note: 'the main chat becomes this branch, as it is now' },
];

const clip = (text, limit) => (text.length <= limit ? text : `${text.slice(0, limit).trimEnd()}…`);

function MergeModal({ branch, parent, messages, parentBusy, onMerge, onClose }) {
  const [mode, setMode] = useState('pick');
  const [selected, setSelected] = useState(() => new Set());
  const [focus, setFocus] = useState('');
  const [deleteBranch, setDeleteBranch] = useState(false);
  const [merging, setMerging] = useState(false);
  const [error, setError] = useState(null);

  const candidates = useMemo(() => branchCandidates(messages, branch.branch_point), [messages, branch.branch_point]);

  useEffect(() => {
    const handleKeyDown = (e) => {
      if (e.key === 'Escape' && !merging) onClose();
    };
    document.addEventListener('keydown', handleKeyDown);
    return () => document.removeEventListener('keydown', handleKeyDown);
  }, [onClose, merging]);

  // Replacing leaves the branch with nothing left to do, so it defaults to removing it
  const chooseMode = (next) => {
    setMode(next);
    setDeleteBranch(next === 'replace');
    setError(null);
  };

  const toggle = (index) => setSelected((prev) => {
    const next = new Set(prev);
    if (next.has(index)) next.delete(index); else next.add(index);
    return next;
  });

  const allSelected = candidates.length > 0 && candidates.every((c) => selected.has(c.index));
  const nothingToMerge = mode === 'pick' ? selected.size === 0 : mode === 'summary' ? candidates.length === 0 : false;
  const blocked = merging || nothingToMerge || parentBusy;

  const handleSubmit = async (e) => {
    e.preventDefault();
    if (blocked) return;
    setMerging(true);
    setError(null);
    try {
      await onMerge({
        mode,
        message_indexes: mode === 'pick' ? [...selected].sort((a, b) => a - b) : undefined,
        focus: mode === 'summary' ? focus.trim() : '',
        delete_branch: deleteBranch,
      });
    } catch (err) {
      const detail = err.response?.data?.detail;
      setError((typeof detail === 'string' ? detail : detail?.message) || 'failed to merge');
      setMerging(false);
    }
  };

  const submitLabel = merging ? 'merging...' : mode === 'replace' ? 'replace main chat' : 'merge';

  return (
    <div className="modal-backdrop" onClick={merging ? undefined : onClose}>
      <div className="modal-card" onClick={(e) => e.stopPropagation()}>
        <div className="modal-header">
          <h2>merge into “{parent.name}”</h2>
          <button className="modal-close" onClick={onClose} disabled={merging} aria-label="close">
            <CloseIcon />
          </button>
        </div>

        <form className="new-chat-form merge-form" onSubmit={handleSubmit}>
          {MODES.map((m) => (
            <div key={m.id}>
              <label className="radio-row">
                <input type="radio" name="merge-mode" checked={mode === m.id} onChange={() => chooseMode(m.id)} />
                {m.label}
              </label>
              {mode === m.id && <div className="merge-note">{m.note}</div>}
            </div>
          ))}

          {mode === 'pick' && (
            candidates.length === 0 ? (
              <div className="merge-note">nothing new in this branch yet, so there is nothing to pick</div>
            ) : (
              <div className="merge-picks">
                <button
                  type="button"
                  className="merge-link"
                  onClick={() => setSelected(allSelected ? new Set() : new Set(candidates.map((c) => c.index)))}
                >
                  {allSelected ? 'select none' : 'select all'}
                </button>
                {candidates.map((c) => (
                  <label key={c.index} className="merge-pick">
                    <input type="checkbox" checked={selected.has(c.index)} onChange={() => toggle(c.index)} />
                    <span className="merge-pick-text">
                      <span className="document-row-name">{clip(c.question, 120)}</span>
                      <span className="merge-pick-answer">{clip(c.answer, 200)}</span>
                    </span>
                  </label>
                ))}
              </div>
            )
          )}

          {mode === 'summary' && (
            <>
              <label className="field-label" htmlFor="merge-focus">focus (optional)</label>
              <input
                id="merge-focus"
                type="text"
                className="field-input"
                placeholder="e.g. only what we decided about pricing"
                maxLength={500}
                value={focus}
                onChange={(e) => setFocus(e.target.value)}
              />
              {candidates.length === 0 && <div className="merge-note">nothing new in this branch yet to summarize</div>}
            </>
          )}

          {mode === 'replace' && (
            <div className="merge-warning">
              the main chat’s current messages and memory are lost. its documents and settings stay as they are.
            </div>
          )}

          <label className="radio-row merge-delete">
            <input type="checkbox" checked={deleteBranch} onChange={(e) => setDeleteBranch(e.target.checked)} />
            delete this branch afterwards
          </label>
          <div className="merge-note">documents added in this branch are not moved to the main chat</div>

          {parentBusy && <div className="field-error">the main chat is still answering. wait for it to finish.</div>}
          {error && <div className="field-error">{error}</div>}

          <button type="submit" className="primary-action" disabled={blocked}>{submitLabel}</button>
        </form>
      </div>
    </div>
  );
}

export default MergeModal;
