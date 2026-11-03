// Guards the palette: every text colour must stay readable (WCAG AA) on the
// surfaces it can appear on, in both themes, and the hues must keep the
// relationships the design is built on.

const fs = require('fs');
const path = require('path');

const css = fs.readFileSync(path.join(__dirname, 'App.css'), 'utf8');

function parseTokens(block) {
  const tokens = {};
  for (const [, name, value] of block.matchAll(/(--[a-z0-9-]+)\s*:\s*([^;]+);/g)) tokens[name] = value.trim();
  return tokens;
}

const lightBlock = css.match(/:root\s*{([^}]*)}/)[1];
const darkBlock = css.match(/@media \(prefers-color-scheme: dark\)\s*{\s*:root\s*{([^}]*)}/)[1];
const light = parseTokens(lightBlock);
const dark = { ...light, ...parseTokens(darkBlock) };
const themes = { light, dark };

const rgb = (hex) => [1, 3, 5].map((i) => parseInt(hex.slice(i, i + 2), 16) / 255);

function luminance(hex) {
  const [r, g, b] = rgb(hex).map((v) => (v <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4));
  return 0.2126 * r + 0.7152 * g + 0.0722 * b;
}

function contrast(a, b) {
  const [hi, lo] = [luminance(a), luminance(b)].sort((x, y) => y - x);
  return (hi + 0.05) / (lo + 0.05);
}

function hue(hex) {
  const [r, g, b] = rgb(hex);
  const max = Math.max(r, g, b);
  const min = Math.min(r, g, b);
  const d = max - min;
  if (d === 0) return 0;
  let h;
  if (max === r) h = ((g - b) / d) % 6;
  else if (max === g) h = (b - r) / d + 2;
  else h = (r - g) / d + 4;
  return (h * 60 + 360) % 360;
}

const hueGap = (a, b) => {
  const gap = Math.abs(hue(a) - hue(b)) % 360;
  return Math.min(gap, 360 - gap);
};

describe.each(Object.entries(themes))('%s theme', (name, t) => {
  const surfaces = { ground: '--color-bg', card: '--surface' };

  test('every colour used as text or fill is a plain hex value', () => {
    ['--color-bg', '--surface', '--ink-1', '--ink-2', '--ink-3', '--blue', '--blue-strong', '--orange',
      '--orange-text', '--green', '--green-text', '--red', '--brown', '--on-blue', '--on-orange', '--on-red']
      .forEach((token) => expect(t[token]).toMatch(/^#[0-9a-fA-F]{6}$/));
  });

  describe.each(Object.entries(surfaces))('text on the %s', (surfaceName, surfaceToken) => {
    const surface = t[surfaceToken];

    test('primary text is easy to read (AAA, 7:1)', () => {
      expect(contrast(t['--ink-1'], surface)).toBeGreaterThanOrEqual(7);
    });

    test.each(['--ink-2', '--ink-3', '--blue', '--blue-strong', '--orange-text', '--green-text', '--red'])(
      '%s meets AA (4.5:1)', (token) => {
        expect(contrast(t[token], surface)).toBeGreaterThanOrEqual(4.5);
      });
  });

  test.each([['--on-blue', '--blue'], ['--on-orange', '--orange'], ['--on-red', '--red']])(
    'text on a solid fill: %s on %s meets AA', (on, fill) => {
      expect(contrast(t[on], t[fill])).toBeGreaterThanOrEqual(4.5);
    });

  test('the content card is distinct from the ground it sits on', () => {
    expect(contrast(t['--surface'], t['--color-bg'])).toBeGreaterThan(1.05);
  });
});

describe('colour relationships', () => {
  test.each(Object.entries(themes))('%s: orange is blue\'s complement (roughly opposite on the wheel)', (name, t) => {
    expect(hueGap(t['--blue'], t['--orange'])).toBeGreaterThan(150);
  });

  test.each(Object.entries(themes))('%s: green is analogous to blue (neighbouring hues)', (name, t) => {
    expect(hueGap(t['--blue'], t['--green'])).toBeGreaterThan(30);
    expect(hueGap(t['--blue'], t['--green'])).toBeLessThan(90);
  });

  test('the light neutrals are warm - beige and brown, close to orange', () => {
    ['--color-bg', '--surface', '--ink-2', '--ink-3', '--brown'].forEach((token) => {
      expect(hueGap(light[token], light['--orange'])).toBeLessThan(30);
    });
  });

  test('the dark ground is a deep blue, in the blue family', () => {
    expect(hueGap(dark['--color-bg'], dark['--blue'])).toBeLessThan(30);
    expect(luminance(dark['--color-bg'])).toBeLessThan(0.02);
  });

  test('the light text colour is a deep blue-slate, the complement of the warm ground', () => {
    expect(hueGap(light['--ink-1'], light['--blue'])).toBeLessThan(25);
  });
});

describe('the stylesheet', () => {
  test('every var(--token) it uses is defined', () => {
    const defined = new Set(Object.keys(light));
    const used = new Set([...css.matchAll(/var\((--[a-z0-9-]+)/g)].map((m) => m[1]));
    const missing = [...used].filter((name) => !defined.has(name));
    expect(missing).toEqual([]);
  });

  test('the dark theme only overrides tokens the light theme defines', () => {
    const extra = Object.keys(parseTokens(darkBlock)).filter((name) => !(name in light));
    expect(extra).toEqual([]);
  });

  test('both themes define the same colours', () => {
    const darkOnly = Object.keys(parseTokens(darkBlock));
    ['--color-bg', '--surface', '--ink-1', '--ink-2', '--ink-3', '--blue', '--orange', '--green', '--red',
      '--on-blue', '--on-orange', '--on-red'].forEach((token) => expect(darkOnly).toContain(token));
  });

  test('motion is switched off for people who prefer reduced motion', () => {
    expect(css).toMatch(/@media \(prefers-reduced-motion: reduce\)[^}]*{[^}]*animation: none/);
  });
});
