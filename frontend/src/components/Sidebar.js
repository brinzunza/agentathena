import React, { useEffect, useRef, useState } from 'react';
import { MoonIcon, SunIcon } from './Icons';
import { buildChatTree } from '../branches';

function Sidebar({
  chats,
  activeChatId,
  onSwitchChat,
  onNewChat,
  onDeleteChat,
  onBranch,
  onOpenMerge,
  onRename,
  onClearChat,
  onResetDatabase,
  onOpenLibrary,
  onOpenContext,
  onOpenSearch,
  contextUsage,
  onOpenAgent,
  terminalOpen,
  onToggleTerminal,
  onOpenApiKey,
  apiKeyStatus,
  view,
  onOpenInfo,
  onOpenSetup,
  theme,
  onToggleTheme,
  loading,
  dbStatus,
  chatsLoaded
}) {
  // Which destructive button is waiting for a second click: 'clear' | 'reset' | 'delete'
  const [confirming, setConfirming] = useState(null);

  const [renaming, setRenaming] = useState(false);
  const [draftName, setDraftName] = useState('');

  useEffect(() => {
    setConfirming(null);
    setRenaming(false);
  }, [activeChatId]);

  const activeChat = chats.find((chat) => chat.id === activeChatId);
  const parentChat = activeChat?.parent_id ? chats.find((chat) => chat.id === activeChat.parent_id) : null;
  const branchCount = chats.filter((chat) => chat.parent_id === activeChatId).length;

  // Enter and leaving the field both finish a rename, and unmounting the field can fire the
  // second one, so only the first counts. Escape cancels.
  const renameDone = useRef(false);
  const startRename = () => {
    renameDone.current = false;
    setDraftName(activeChat?.name || '');
    setRenaming(true);
  };
  const finishRename = (save) => {
    if (renameDone.current) return;
    renameDone.current = true;
    setRenaming(false);
    const name = draftName.trim();
    if (save && name && name !== activeChat?.name) onRename(activeChatId, name);
  };

  // Chats present when the list first loaded don't animate; one that appears later does
  const knownChats = useRef(null);
  if (knownChats.current === null && chatsLoaded) knownChats.current = new Set(chats.map((chat) => chat.id));

  // A button that asks "are you sure?" with yes / no in its place before it does anything
  const confirmable = (key, label, question, action) => (confirming === key ? (
    <span className="chat-list-confirm session-delete-confirm">
      {question}
      <button className="confirm-yes" onClick={() => { setConfirming(null); action(); }}>yes</button>
      <button className="confirm-cancel" onClick={() => setConfirming(null)}>no</button>
    </span>
  ) : (
    <button onClick={() => setConfirming(key)} className="nav-item nav-item-danger" disabled={loading}>
      {label}
    </button>
  ));

  return (
    <div className="sidebar">
      <div className="sidebar-brand">
        <svg className="sidebar-brand-mark" viewBox="0 0 24 24" aria-hidden="true">
          <rect width="24" height="24" rx="7" className="brand-tile" />
          <path d="M7.25 17.25 12 6.75l4.75 10.5M9.1 13.4h5.8" className="brand-glyph" />
          <circle cx="12" cy="6.75" r="1.35" className="brand-dot" />
        </svg>
        <span className="sidebar-brand-name">athena</span>
        <button
          type="button"
          className="theme-toggle"
          onClick={onToggleTheme}
          aria-label={theme === 'dark' ? 'switch to the light theme' : 'switch to the dark theme'}
          title={theme === 'dark' ? 'light theme' : 'dark theme'}
        >
          <span className="theme-toggle-icon" key={theme}>{theme === 'dark' ? <SunIcon /> : <MoonIcon />}</span>
        </button>
      </div>

      <div className="sidebar-section">
        <div className="sidebar-label">chats</div>
        <div className="chat-list">
          {buildChatTree(chats).map(({ chat, depth }) => (
            <div
              key={chat.id}
              className={`chat-list-item ${chat.id === activeChatId ? 'active' : ''} ${depth > 0 ? 'chat-list-branch' : ''}`}
              style={depth > 0 ? { marginLeft: Math.min(depth, 4) * 14 } : undefined}
              onClick={() => onSwitchChat(chat.id)}
              title={chat.name}
              {...(knownChats.current && !knownChats.current.has(chat.id)
                ? { 'data-new': '', onAnimationEnd: () => knownChats.current.add(chat.id) }
                : {})}
            >
              <span className="chat-list-name">{chat.name}</span>
            </div>
          ))}
        </div>
        <button onClick={onNewChat} className="nav-item">
          new chat
        </button>
        <button onClick={onOpenSearch} className="nav-item" title="search chats (ctrl/cmd + K)">
          search chats
        </button>
      </div>

      {activeChatId && (
        <>
          <div className="sidebar-section">
            <div className="sidebar-label">knowledge base</div>
            <button onClick={onOpenLibrary} className="nav-item" disabled={loading}>
              documents
            </button>
          </div>

          <div className="sidebar-section">
            <div className="sidebar-label">session</div>
            <button onClick={onOpenAgent} className="nav-item">
              agent
            </button>
            <button
              onClick={onToggleTerminal}
              className={`nav-item ${terminalOpen ? 'active' : ''}`}
              aria-pressed={terminalOpen}
              title="show what the agent is doing, live (ctrl + `)"
            >
              terminal
            </button>
            <button onClick={onOpenContext} className="nav-item">
              {contextUsage?.enabled && contextUsage.total_exchanges > 0 && (
                <span
                  className={`nav-item-badge ${contextUsage.left_out > 0 ? 'nav-item-badge-warn' : ''}`}
                  title={contextUsage.left_out > 0
                    ? `${contextUsage.left_out} older exchanges are no longer sent to the model`
                    : 'how much of the context window each message uses'}
                >
                  {contextUsage.percent}%
                </span>
              )}
              context
            </button>
            <button onClick={onBranch} className="nav-item" disabled={loading} title="fork this chat: the branch starts with its messages, memory and documents">
              branch chat
            </button>
            {parentChat && (
              <button onClick={onOpenMerge} className="nav-item" disabled={loading} title={`merge into “${parentChat.name}”`}>
                merge into parent
              </button>
            )}
            {renaming ? (
              <form className="sidebar-rename" onSubmit={(e) => { e.preventDefault(); finishRename(true); }}>
                <input
                  className="field-input"
                  value={draftName}
                  maxLength={80}
                  autoFocus
                  onFocus={(e) => e.target.select()}
                  aria-label="chat name"
                  onChange={(e) => setDraftName(e.target.value)}
                  onKeyDown={(e) => { if (e.key === 'Escape') finishRename(false); }}
                  onBlur={() => finishRename(true)}
                />
              </form>
            ) : (
              <button onClick={startRename} className="nav-item">
                rename
              </button>
            )}
            {confirmable('clear', 'clear chat', 'clear this chat?', onClearChat)}
            {confirmable('reset', 'reset database', 'reset database?', onResetDatabase)}
            {confirmable('delete', 'delete chat',
              branchCount > 0 ? `delete this chat? its ${branchCount === 1 ? 'branch stays' : `${branchCount} branches stay`}` : 'delete this chat?',
              () => onDeleteChat(activeChatId))}
          </div>

          <div className="sidebar-status">
            <span className={`status-dot ${dbStatus.exists ? 'active' : ''}`} />
            {dbStatus.exists
              ? `${dbStatus.file_count} document${dbStatus.file_count === 1 ? '' : 's'} indexed`
              : 'no documents indexed'}
          </div>
        </>
      )}

      <div className="sidebar-section sidebar-section-settings">
        <div className="sidebar-label">help</div>
        <button onClick={onOpenInfo} className={`nav-item ${view === 'info' ? 'active' : ''}`}>
          how it works
        </button>
        <button onClick={onOpenSetup} className={`nav-item ${view === 'setup' ? 'active' : ''}`}>
          setup guide
        </button>
      </div>

      <div className="sidebar-section">
        <div className="sidebar-label">settings</div>
        <button onClick={onOpenApiKey} className="nav-item">
          {apiKeyStatus && (
            <span className="nav-item-badge">
              {apiKeyStatus.provider}
            </span>
          )}
          ai provider
        </button>
      </div>
    </div>
  );
}

export default Sidebar;
