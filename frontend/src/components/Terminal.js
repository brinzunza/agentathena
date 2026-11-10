import React, { useLayoutEffect, useMemo, useRef, useState } from 'react';
import { buildTerminalLines, terminalSummary } from '../terminal';

// A read-only console under the chat: what the agent is doing, as it does it.
function Terminal({ messages, onClose }) {
  const [tall, setTall] = useState(false);
  const body = useRef(null);
  const following = useRef(true);
  const lines = useMemo(() => buildTerminalLines(messages), [messages]);
  const { steps, running } = useMemo(() => terminalSummary(messages), [messages]);

  // Stays pinned to the newest line, unless you have scrolled up to read something
  useLayoutEffect(() => {
    const el = body.current;
    if (el && following.current) el.scrollTop = el.scrollHeight;
  });

  const onScroll = () => {
    const el = body.current;
    following.current = el.scrollHeight - el.scrollTop - el.clientHeight < 40;
  };

  return (
    <section className={`terminal ${tall ? 'terminal-tall' : ''}`} aria-label="agent terminal">
      <header className="terminal-bar">
        <span className="terminal-title">terminal</span>
        <span className="terminal-count">
          {steps === 0 ? 'idle' : `${steps} ${steps === 1 ? 'step' : 'steps'}`}
          {running && <span className="terminal-live" aria-label="working" />}
        </span>
        <span className="terminal-spacer" />
        <button type="button" className="terminal-button" onClick={() => setTall(!tall)} aria-pressed={tall}>
          {tall ? 'shrink' : 'expand'}
        </button>
        <button type="button" className="terminal-button" onClick={onClose} aria-label="close the terminal">
          close
        </button>
      </header>

      <div className="terminal-body" ref={body} onScroll={onScroll} role="log" aria-live="off">
        {lines.length === 0 && (
          <div className="t-line t-dim">
            <span className="t-prompt">❯</span> waiting for the agent. Searches, code and results show up here as it works.
          </div>
        )}
        {lines.map((line) => {
          if (line.kind === 'turn') return <div key={line.key} className="t-line t-turn"># {line.text}</div>;
          if (line.kind === 'note') return <div key={line.key} className="t-line t-note">{line.text}</div>;
          if (line.kind === 'cmd') {
            return (
              <div key={line.key} className="t-line t-cmd">
                <span className="t-prompt">❯</span> <span className="t-name">{line.name}</span>
                {line.arg && <span className="t-arg"> {line.arg}</span>}
              </div>
            );
          }
          if (line.kind === 'code') return <div key={line.key} className="t-line t-code">{line.text || ' '}</div>;
          if (line.kind === 'out') return <div key={line.key} className={`t-line t-out ${line.tone === 'err' ? 't-err' : ''}`}>{line.text || ' '}</div>;
          if (line.kind === 'status') {
            return (
              <div key={line.key} className={`t-line t-status t-${line.tone}`}>
                {line.mark ? `${line.mark} ` : ''}{line.text}
              </div>
            );
          }
          return <div key={line.key} className="t-line t-dim">{line.text}</div>;
        })}
        <div className="t-line t-idle" aria-hidden="true">
          <span className="t-prompt">❯</span> <span className="t-cursor" />
        </div>
      </div>
    </section>
  );
}

export default Terminal;
