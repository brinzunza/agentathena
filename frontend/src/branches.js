// Helpers for chat branches, kept apart from the components so they can be tested on their own.

// Orders chats as a tree: each chat followed by its branches, indented by depth.
// A branch whose parent isn't in the list (deleted, or not loaded) is shown at the top
// level, and a chain of parents that loops back on itself can't hang the list.
export function buildChatTree(chats) {
  const ids = new Set(chats.map((chat) => chat.id));
  const childrenOf = new Map();
  const roots = [];
  chats.forEach((chat) => {
    if (chat.parent_id && ids.has(chat.parent_id) && chat.parent_id !== chat.id) {
      if (!childrenOf.has(chat.parent_id)) childrenOf.set(chat.parent_id, []);
      childrenOf.get(chat.parent_id).push(chat);
    } else {
      roots.push(chat);
    }
  });

  const rows = [];
  const seen = new Set();
  const visit = (chat, depth) => {
    if (seen.has(chat.id)) return;
    seen.add(chat.id);
    rows.push({ chat, depth });
    (childrenOf.get(chat.id) || []).forEach((child) => visit(child, depth + 1));
  };
  roots.forEach((chat) => visit(chat, 0));
  chats.forEach((chat) => visit(chat, 0));   // anything only reachable through a loop
  return rows;
}

// The answers in a branch that can be merged back: the ones after the point it was made,
// each with the question it answers. A branch whose chat was cleared is shorter than
// where it started, and then everything in it is new.
export function branchCandidates(messages, branchPoint) {
  const start = branchPoint != null && branchPoint <= messages.length ? branchPoint : 0;
  const candidates = [];
  let question = null;
  messages.forEach((message, index) => {
    if (message.role === 'user') question = message.content;
    if (index < start || message.role !== 'assistant' || message.streaming || message.merged_from) return;
    if (typeof message.content !== 'string' || !message.content.trim() || question === null) return;
    candidates.push({ index, question, answer: message.content });
  });
  return candidates;
}
