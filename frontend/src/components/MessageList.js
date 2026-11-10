import React, { useEffect, useRef } from 'react';
import Message from './Message';

// How close to the bottom still counts as "following along"
const STICK_THRESHOLD_PX = 80;

// Which messages have already played their entrance, per chat, so
// switching away and back doesn't replay them.
const played = {};
const playedIn = (chatId) => (played[chatId] = played[chatId] || new Set());

function MessageList({
  chatId, onDecidePermission, messages, expandedSources, onToggleSources, onViewSource, onOpenSettings,
  canRedo, onRegenerate, onEdit, onBranch, animateFrom = Infinity, focus = null, onFocusDone
}) {
  const scroller = useRef(null);
  const content = useRef(null);
  const following = useRef(true);

  const scrollToBottom = () => {
    const element = scroller.current;
    if (element) element.scrollTop = element.scrollHeight;
  };

  // Follows new content (streamed text, tool steps, a research card filling in)
  // down the page - but only while the reader is at the bottom. Scroll up to
  // read something and it leaves you alone until you come back down.
  useEffect(() => {
    const element = scroller.current;
    const onScroll = () => {
      following.current = element.scrollHeight - element.scrollTop - element.clientHeight < STICK_THRESHOLD_PX;
    };
    element.addEventListener('scroll', onScroll, { passive: true });
    const observer = new ResizeObserver(() => {
      if (following.current) scrollToBottom();
    });
    observer.observe(content.current);
    return () => {
      element.removeEventListener('scroll', onScroll);
      observer.disconnect();
    };
  }, []);

  // A different chat, or the user sending something, always lands at the bottom
  useEffect(() => {
    following.current = true;
    scrollToBottom();
  }, [chatId]);

  const lastMessage = messages[messages.length - 1];
  const sentCount = messages.filter((message) => message.role === 'user').length;
  useEffect(() => {
    following.current = true;
    scrollToBottom();
  }, [sentCount]);

  // Redoing a message removes some and adds new ones at the same positions; those are new
  useEffect(() => {
    const seen = playedIn(chatId);
    [...seen].forEach((index) => { if (index >= messages.length) seen.delete(index); });
  }, [chatId, messages.length]);

  // A search hit: scroll to that message and flash it. It may not be there yet
  // (its chat is still loading), so this runs again when the messages arrive.
  useEffect(() => {
    if (!focus) return;
    if (messages.length > 0 && focus.index >= messages.length) {
      onFocusDone?.();        // the chat has changed since the search
      return;
    }
    const target = content.current?.children[focus.index];
    if (!target) return;
    following.current = false;
    target.scrollIntoView?.({ block: 'center' });
    target.classList.add('message-found');
    setTimeout(() => target.classList.remove('message-found'), 2200);
    onFocusDone?.();
  }, [focus, messages.length, onFocusDone]);

  const lastUserIndex = messages.reduce((found, message, i) => (message.role === 'user' ? i : found), -1);

  return (
    <div className="messages" ref={scroller}>
      <div className="messages-inner" ref={content}>
        {messages.map((message, index) => (
          <Message
            key={index}
            chatId={chatId}
            message={message}
            onDecidePermission={onDecidePermission}
            expanded={!!expandedSources[index]}
            onToggleSources={() => onToggleSources(index)}
            onViewSource={onViewSource}
            onOpenSettings={onOpenSettings}
            isLastReply={message === lastMessage || index === lastUserIndex}
            canRedo={canRedo}
            onRegenerate={onRegenerate}
            onEdit={onEdit}
            onBranch={onBranch ? () => onBranch(index) : undefined}
            reveal={index >= animateFrom && !playedIn(chatId).has(index)
              ? { onDone: () => playedIn(chatId).add(index) }
              : null}
          />
        ))}
      </div>
    </div>
  );
}

export default MessageList;
