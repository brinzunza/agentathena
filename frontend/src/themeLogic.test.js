import { effectiveTheme, readSavedTheme, saveTheme, applyTheme, toggledTheme, THEME_KEY } from './theme';

describe('which theme shows', () => {
  test.each([
    ['light', true, 'light'],
    ['dark', false, 'dark'],
    [null, true, 'dark'],
    [null, false, 'light'],
    [undefined, false, 'light'],
    ['sepia', true, 'dark'],     // anything unknown follows the system
  ])('saved %s, system dark %s -> %s', (saved, prefersDark, expected) => {
    expect(effectiveTheme(saved, prefersDark)).toBe(expected);
  });
});

describe('the toggle', () => {
  test.each([
    ['light', false, 'dark'],
    ['dark', true, 'light'],
    [null, true, 'light'],      // following a dark system, it goes to light
    [null, false, 'dark'],
  ])('saved %s, system dark %s -> switches to %s', (saved, prefersDark, expected) => {
    expect(toggledTheme(saved, prefersDark)).toBe(expected);
  });
});

describe('remembering the choice', () => {
  beforeEach(() => window.localStorage.clear());

  test('round-trips', () => {
    saveTheme('dark');
    expect(window.localStorage.getItem(THEME_KEY)).toBe('dark');
    expect(readSavedTheme()).toBe('dark');
  });

  test('nothing saved means follow the system', () => {
    expect(readSavedTheme()).toBeNull();
  });

  test('a garbled saved value is ignored', () => {
    window.localStorage.setItem(THEME_KEY, 'purple');
    expect(readSavedTheme()).toBeNull();
  });

  test('blocked storage does not break anything', () => {
    const original = Storage.prototype.getItem;
    const originalSet = Storage.prototype.setItem;
    Storage.prototype.getItem = () => { throw new Error('blocked'); };
    Storage.prototype.setItem = () => { throw new Error('blocked'); };
    try {
      expect(readSavedTheme()).toBeNull();
      expect(() => saveTheme('dark')).not.toThrow();
    } finally {
      Storage.prototype.getItem = original;
      Storage.prototype.setItem = originalSet;
    }
  });
});

describe('applying it', () => {
  afterEach(() => {
    document.documentElement.removeAttribute('data-theme');
    document.documentElement.classList.remove('theme-switching');
  });

  test('sets the choice on <html>, and clears it', () => {
    applyTheme('dark', { animate: false });
    expect(document.documentElement.getAttribute('data-theme')).toBe('dark');
    applyTheme(null, { animate: false });
    expect(document.documentElement.hasAttribute('data-theme')).toBe(false);
  });

  test('eases the change for a moment, then stops', () => {
    jest.useFakeTimers();
    try {
      applyTheme('light');
      expect(document.documentElement.classList.contains('theme-switching')).toBe(true);
      jest.advanceTimersByTime(500);
      expect(document.documentElement.classList.contains('theme-switching')).toBe(false);
    } finally {
      jest.useRealTimers();
    }
  });
});
