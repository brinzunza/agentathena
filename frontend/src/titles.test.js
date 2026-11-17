import { needsTitle, watchForTitle, TITLE_POLL_DELAYS } from './titles';

const chats = (untitled) => [{ id: 'a', name: 'x', untitled: false }, { id: 'b', name: 'new chat', untitled }];
const instantly = () => Promise.resolve();

describe('needsTitle', () => {
  test('a chat marked untitled needs one', () => expect(needsTitle(chats(true), 'b')).toBe(true));
  test('a named chat does not', () => expect(needsTitle(chats(false), 'b')).toBe(false));
  test('a chat that is not in the list does not', () => expect(needsTitle(chats(true), 'zzz')).toBe(false));
  test('an older server that never says anything about it does not', () => expect(needsTitle([{ id: 'b', name: 'n' }], 'b')).toBe(false));
});

describe('watchForTitle', () => {
  test('stops as soon as the chat has been named', async () => {
    const fetchChats = jest.fn()
      .mockResolvedValueOnce(chats(true))
      .mockResolvedValueOnce(chats(false));
    expect(await watchForTitle('b', fetchChats, instantly)).toBe(true);
    expect(fetchChats).toHaveBeenCalledTimes(2);
  });

  test('gives up after the last delay, so a model that never answers is not asked about for ever', async () => {
    const fetchChats = jest.fn().mockResolvedValue(chats(true));
    expect(await watchForTitle('b', fetchChats, instantly)).toBe(false);
    expect(fetchChats).toHaveBeenCalledTimes(TITLE_POLL_DELAYS.length);
  });

  test('waits the delays in order', async () => {
    const waits = [];
    await watchForTitle('b', jest.fn().mockResolvedValue(chats(true)), (ms) => { waits.push(ms); return Promise.resolve(); });
    expect(waits).toEqual(TITLE_POLL_DELAYS);
  });

  test('a failed request is tried again later rather than ending the watch', async () => {
    const fetchChats = jest.fn()
      .mockRejectedValueOnce(new Error('offline'))
      .mockResolvedValueOnce(chats(false));
    expect(await watchForTitle('b', fetchChats, instantly)).toBe(true);
  });
});
