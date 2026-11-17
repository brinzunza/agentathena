// A chat started without a name is called "new chat" until its first exchange has been answered; then the
// server names it from what was said (app/titles.py). That happens a moment after the reply ends, so the page
// asks for the chat list a few times, at growing gaps, until the name has changed.

export const TITLE_POLL_DELAYS = [1500, 3000, 6000, 12000];

export const needsTitle = (chats, chatId) => Boolean(chats.find((chat) => chat.id === chatId)?.untitled);

// Asks `fetchChats` (which resolves to the chat list) until `chatId` has a name, or the delays run out.
// `wait` is how to pause; tests pass a version that doesn't. Returns whether the chat got its name.
export async function watchForTitle(chatId, fetchChats, wait = (ms) => new Promise((resolve) => setTimeout(resolve, ms))) {
  for (const delay of TITLE_POLL_DELAYS) {
    await wait(delay);
    let chats;
    try {
      chats = await fetchChats();
    } catch (error) {
      continue;
    }
    if (!chats || !needsTitle(chats, chatId)) return true;
  }
  return false;
}
