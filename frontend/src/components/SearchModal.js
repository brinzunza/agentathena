import React, { useEffect, useRef, useState } from 'react';
import axios from 'axios';
import { API_BASE } from '../App';
import { CloseIcon, SearchIcon } from './Icons';
import { splitHighlight } from '../searchHighlight';

const DEBOUNCE_MS = 200;
const ROLE_LABELS = { user: 'you', assistant: 'athena', chat: 'chat name' };

function Highlighted({ text, query }) {
  return splitHighlight(text, query).map((part, i) => (
    part.hit ? <mark key={i}>{part.text}</mark> : <React.Fragment key={i}>{part.text}</React.Fragment>
  ));
}

function SearchModal({ onClose, onOpen }) {
  const [query, setQuery] = useState('');
  const [results, setResults] = useState(null);       // null until the first search finishes
  const [searching, setSearching] = useState(false);
  const [error, setError] = useState(null);
  const [active, setActive] = useState(0);
  const latest = useRef(0);
  const list = useRef(null);

  useEffect(() => {
    const handleKeyDown = (e) => {
      if (e.key === 'Escape') onClose();
    };
    document.addEventListener('keydown', handleKeyDown);
    return () => document.removeEventListener('keydown', handleKeyDown);
  }, [onClose]);

  // Search shortly after typing stops; an answer to an older query is ignored
  useEffect(() => {
    const text = query.trim();
    latest.current += 1;
    const mine = latest.current;
    if (!text) {
      setResults(null);
      setSearching(false);
      setError(null);
      return undefined;
    }
    setSearching(true);
    const timer = setTimeout(async () => {
      try {
        const response = await axios.get(`${API_BASE}/chats/search`, { params: { q: text } });
        if (mine !== latest.current) return;
        setResults(response.data.results);
        setActive(0);
        setError(null);
      } catch (err) {
        if (mine !== latest.current) return;
        setError('search failed. is the backend running?');
      }
      setSearching(false);
    }, DEBOUNCE_MS);
    return () => clearTimeout(timer);
  }, [query]);

  useEffect(() => {
    list.current?.querySelector('.search-result.active')?.scrollIntoView?.({ block: 'nearest' });
  }, [active, results]);

  const handleKeyDown = (e) => {
    if (!results?.length) return;
    if (e.key === 'ArrowDown') {
      e.preventDefault();
      setActive((i) => Math.min(i + 1, results.length - 1));
    } else if (e.key === 'ArrowUp') {
      e.preventDefault();
      setActive((i) => Math.max(i - 1, 0));
    } else if (e.key === 'Enter') {
      e.preventDefault();
      onOpen(results[active]);
    }
  };

  return (
    <div className="modal-backdrop" onClick={onClose}>
      <div className="modal-card search-modal" onClick={(e) => e.stopPropagation()}>
        <div className="modal-header">
          <h2>search chats</h2>
          <button className="modal-close" onClick={onClose} aria-label="close">
            <CloseIcon />
          </button>
        </div>

        <div className="modal-search">
          <SearchIcon />
          <input
            type="text"
            placeholder="search chat names and messages..."
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            onKeyDown={handleKeyDown}
            aria-label="search chats"
            autoFocus
          />
        </div>

        <div className="modal-body search-results" ref={list}>
          {error && <div className="modal-empty">{error}</div>}
          {!error && results === null && !searching && (
            <div className="modal-empty">type to search every chat. all words must match.</div>
          )}
          {!error && results !== null && results.length === 0 && !searching && (
            <div className="modal-empty">nothing found for &ldquo;{query.trim()}&rdquo;</div>
          )}
          {!error && results?.map((result, i) => (
            <button
              type="button"
              key={`${result.chat_id}:${result.index}`}
              className={`search-result ${i === active ? 'active' : ''}`}
              onClick={() => onOpen(result)}
              onMouseEnter={() => setActive(i)}
            >
              <span className="search-result-meta">
                <span className="search-result-chat">{result.chat_name}</span>
                <span className="search-result-role">{ROLE_LABELS[result.role] || result.role}</span>
              </span>
              <span className="search-result-snippet"><Highlighted text={result.snippet} query={query} /></span>
            </button>
          ))}
        </div>
      </div>
    </div>
  );
}

export default SearchModal;
