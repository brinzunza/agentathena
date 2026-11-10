import { buildChatTree, branchCandidates } from './branches';

const chat = (id, parent_id = null) => ({ id, name: id, parent_id });
const shape = (chats) => buildChatTree(chats).map(({ chat: c, depth }) => `${depth}:${c.id}`);

describe('buildChatTree', () => {
  test('a flat list stays in order at depth 0', () => {
    expect(shape([chat('a'), chat('b'), chat('c')])).toEqual(['0:a', '0:b', '0:c']);
  });

  test('branches follow their parent, indented, in the order they were made', () => {
    const chats = [chat('a'), chat('b'), chat('a1', 'a'), chat('b1', 'b'), chat('a2', 'a')];
    expect(shape(chats)).toEqual(['0:a', '1:a1', '1:a2', '0:b', '1:b1']);
  });

  test('branches of branches nest to any depth', () => {
    expect(shape([chat('a'), chat('b', 'a'), chat('c', 'b'), chat('d', 'c')]))
      .toEqual(['0:a', '1:b', '2:c', '3:d']);
  });

  test('a branch whose parent is missing becomes a top-level chat', () => {
    expect(shape([chat('a'), chat('x', 'gone')])).toEqual(['0:a', '0:x']);
  });

  test('a branch listed before its parent still nests under it', () => {
    expect(shape([chat('b', 'a'), chat('a')])).toEqual(['0:a', '1:b']);
  });

  test('a loop of parents cannot hang the list or drop chats', () => {
    const rows = shape([chat('a', 'b'), chat('b', 'a'), chat('c', 'c')]);
    expect(rows.sort()).toEqual(['0:a', '0:c', '1:b']);
  });

  test('every chat appears exactly once', () => {
    const chats = [chat('a'), chat('b', 'a'), chat('c', 'a'), chat('d', 'c'), chat('e')];
    expect(buildChatTree(chats)).toHaveLength(chats.length);
  });

  test('does not change its input', () => {
    const chats = [chat('b', 'a'), chat('a')];
    const copy = JSON.parse(JSON.stringify(chats));
    buildChatTree(chats);
    expect(chats).toEqual(copy);
  });
});

describe('branchCandidates', () => {
  const user = (content) => ({ role: 'user', content });
  const athena = (content, extra = {}) => ({ role: 'assistant', content, ...extra });

  test('lists the answers after the branch point with their questions', () => {
    const messages = [user('q0'), athena('a0'), user('q1'), athena('a1'), user('q2'), athena('a2')];
    expect(branchCandidates(messages, 4)).toEqual([{ index: 5, question: 'q2', answer: 'a2' }]);
  });

  test('inherited answers are not offered', () => {
    expect(branchCandidates([user('q'), athena('a')], 2)).toEqual([]);
  });

  test('skips notes, errors, research cards, empty and unfinished answers', () => {
    const messages = [
      user('q'), { role: 'system', content: 'note' }, { role: 'error', content: 'bad' },
      { role: 'research', research_id: 'x' }, athena('  '), athena('partial', { streaming: true }),
      athena('real answer'),
    ];
    expect(branchCandidates(messages, 0)).toEqual([{ index: 6, question: 'q', answer: 'real answer' }]);
  });

  test('answers that were merged in from another branch are not offered again', () => {
    expect(branchCandidates([user('q'), athena('a', { merged_from: 'abc' })], 0)).toEqual([]);
  });

  test('when the branch was cleared and is now shorter than its branch point, everything is new', () => {
    expect(branchCandidates([user('fresh'), athena('answer')], 6))
      .toEqual([{ index: 1, question: 'fresh', answer: 'answer' }]);
  });

  test('a missing branch point means the whole chat', () => {
    expect(branchCandidates([user('q'), athena('a')], null)).toHaveLength(1);
  });

  test('two answers to one question each keep that question', () => {
    const messages = [user('q'), athena('first'), athena('second')];
    expect(branchCandidates(messages, 0).map((c) => c.question)).toEqual(['q', 'q']);
  });
});
