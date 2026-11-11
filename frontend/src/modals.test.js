import { MODAL_NAMES, openModal, closeModal, afterSearchShortcut } from './modals';

describe('openModal', () => {
  test('names a dialog, with an optional payload', () => {
    expect(openModal('context')).toEqual({ name: 'context', payload: null });
    expect(openModal('source', { name: 'a.pdf' })).toEqual({ name: 'source', payload: { name: 'a.pdf' } });
  });

  test('refuses a dialog that does not exist, so a typo cannot open nothing silently', () => {
    expect(() => openModal('contxt')).toThrow(/unknown dialog/);
  });

  test('opening one replaces whichever was open: there is only ever one', () => {
    const state = openModal('agent');
    expect(openModal('library', null)).not.toBe(state);
    expect(openModal('library').name).toBe('library');
  });
});

describe('closeModal', () => {
  test('with no names it closes whatever is open, and does nothing when none is', () => {
    expect(closeModal(openModal('agent'))).toBeNull();
    expect(closeModal(null)).toBeNull();
  });

  test('with names it closes only if the open dialog is one of them', () => {
    const merge = openModal('merge');
    expect(closeModal(merge, ['library', 'context', 'merge', 'source'])).toBeNull();
    expect(closeModal(merge, ['search'])).toBe(merge);
  });

  test("a dialog's own close button cannot close a different dialog that opened since", () => {
    const search = openModal('search');
    expect(closeModal(search, ['newChat'])).toBe(search);
  });
});

describe('the search shortcut', () => {
  test('opens search when nothing is open', () => {
    expect(afterSearchShortcut(null)).toEqual({ name: 'search', payload: null });
  });

  test('leaves an open dialog alone, so half-filled work is not buried', () => {
    const merge = openModal('merge');
    expect(afterSearchShortcut(merge)).toBe(merge);
  });

  test('pressing it again with search open changes nothing', () => {
    const search = openModal('search');
    expect(afterSearchShortcut(search)).toBe(search);
  });
});

test('every dialog the app renders is a known name', () => {
  expect(MODAL_NAMES).toEqual(['newChat', 'apiKey', 'library', 'agent', 'merge', 'search', 'context', 'source']);
});
