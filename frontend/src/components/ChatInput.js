import React, { useLayoutEffect, useRef } from 'react';
import { SendIcon, StopIcon } from './Icons';

const MAX_HEIGHT = 200;

function ChatInput({ input, setInput, onSubmit, onCancel, canChat, loading, agentEnabled }) {
  const box = useRef(null);
  const ready = canChat && !loading && input.trim().length > 0;

  // The box grows with what you type, up to a limit, then scrolls
  useLayoutEffect(() => {
    const el = box.current;
    if (!el) return;
    el.style.height = 'auto';
    el.style.height = `${Math.min(el.scrollHeight, MAX_HEIGHT)}px`;
  }, [input]);

  // Enter sends; shift+enter makes a new line. Enter while an input method is composing is left alone.
  const onKeyDown = (e) => {
    if (e.key !== 'Enter' || e.shiftKey || e.nativeEvent.isComposing) return;
    e.preventDefault();
    if (ready) e.currentTarget.form.requestSubmit();
  };

  return (
    <form onSubmit={onSubmit} className="chat-form">
      <div className="chat-input-wrap">
        <textarea
          ref={box}
          rows={1}
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={onKeyDown}
          placeholder={canChat ? (agentEnabled ? 'ask anything...' : 'ask about your documents...') : 'upload pdf files first'}
          disabled={!canChat || loading}
          className="chat-input"
          aria-label="message"
        />
        {loading ? (
          <button type="button" className="send-button stop-button" onClick={onCancel} aria-label="Stop generating">
            <StopIcon />
            <span className="send-label">stop</span>
          </button>
        ) : (
          <button type="submit" className="send-button" disabled={!canChat || !input.trim()} aria-label="Send message">
            <span className="send-label">send</span>
            <SendIcon />
          </button>
        )}
      </div>
      {input.startsWith('/') ? (
        <div className="command-hint">
          <code>/context</code> view and edit · <code>/compact</code> · <code>/forget &lt;topic&gt;</code> · <code>/context keep only info on …</code> · <code>/context clear</code> · <code>/research &lt;topic&gt;</code> · <code>/branch [name]</code> · <code>/merge</code>
        </div>
      ) : (
        <div className="composer-hint">
          <span><kbd>enter</kbd> to send</span>
          <span><kbd>shift</kbd> + <kbd>enter</kbd> for a new line</span>
          <span><kbd>/</kbd> for commands</span>
        </div>
      )}
    </form>
  );
}

export default ChatInput;
