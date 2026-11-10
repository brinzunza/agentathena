// Light / dark theme. With nothing saved the app follows the system; the first press of the
// toggle saves an explicit choice, which is then applied before the page paints (see index.html).

export const THEME_KEY = 'athena-theme';

export const systemPrefersDark = () =>
  typeof window !== 'undefined' && !!window.matchMedia?.('(prefers-color-scheme: dark)').matches;

// What is showing, given a saved choice ('light' | 'dark' | anything else for "follow the system")
export const effectiveTheme = (saved, prefersDark) =>
  saved === 'light' || saved === 'dark' ? saved : prefersDark ? 'dark' : 'light';

// The theme the toggle switches to
export const toggledTheme = (saved, prefersDark) =>
  effectiveTheme(saved, prefersDark) === 'dark' ? 'light' : 'dark';

export function readSavedTheme() {
  try {
    const value = window.localStorage.getItem(THEME_KEY);
    return value === 'light' || value === 'dark' ? value : null;
  } catch (error) {
    return null;       // storage can be blocked; the app just follows the system
  }
}

export function saveTheme(theme) {
  try {
    window.localStorage.setItem(THEME_KEY, theme);
  } catch (error) {
    // not saved; it still applies for this session
  }
}

// Sets (or, for null, clears) the explicit choice on <html>. For a moment every colour eases
// to its new value, so the switch is a soft crossfade rather than a flash.
export function applyTheme(theme, { animate = true } = {}) {
  const root = document.documentElement;
  if (animate) {
    root.classList.add('theme-switching');
    window.setTimeout(() => root.classList.remove('theme-switching'), 450);
  }
  if (theme) root.setAttribute('data-theme', theme);
  else root.removeAttribute('data-theme');
}
