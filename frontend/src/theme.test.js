// Guards the palette: white, near-black, blue and green. Every text colour must stay readable
// (WCAG AA) on the surfaces it can appear on, in both themes, and the hues must stay what the
// design says they are.

const fs = require('fs');
const path = require('path');

const css = fs.readFileSync(path.join(__dirname, 'App.css'), 'utf8');

function parseTokens(block) {
  const tokens = {};
  for (const [, name, value] of block.matchAll(/(--[a-z0-9-]+)\s*:\s*([^;]+);/g)) tokens[name] = value.trim();
  return tokens;
}

const lightBlock = css.match(/:root\s*{([^}]*)}/)[1];
const darkMediaBlock = css.match(/@media \(prefers-color-scheme: dark\)\s*{\s*:root:not\(\[data-theme="light"\]\)\s*{([^}]*)}/)[1];
const darkAttributeBlock = css.match(/:root\[data-theme="dark"\]\s*{([^}]*)}/)[1];
const light = parseTokens(lightBlock);
const dark = { ...light, ...parseTokens(darkAttributeBlock) };
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

function hsl(hex) {
  const [r, g, b] = rgb(hex);
  const max = Math.max(r, g, b);
  const min = Math.min(r, g, b);
  const d = max - min;
  const l = (max + min) / 2;
  const s = d === 0 ? 0 : d / (1 - Math.abs(2 * l - 1));
  let h = 0;
  if (d !== 0) {
    if (max === r) h = ((g - b) / d) % 6;
    else if (max === g) h = (b - r) / d + 2;
    else h = (r - g) / d + 4;
  }
  return { h: (h * 60 + 360) % 360, s, l };
}

describe.each(Object.entries(themes))('%s theme', (name, t) => {
  const surfaces = { canvas: '--canvas', tile: '--surface', 'recessed area': '--bg-sunken' };

  test('every colour used as text or fill is a plain hex value', () => {
    ['--color-bg', '--canvas', '--bg-sunken', '--surface', '--ink-1', '--ink-2', '--ink-3', '--blue', '--blue-strong',
      '--green', '--green-text', '--red', '--on-blue', '--on-red']
      .forEach((token) => expect(t[token]).toMatch(/^#[0-9a-fA-F]{6}$/));
  });

  describe.each(Object.entries(surfaces))('text on the %s', (surfaceName, surfaceToken) => {
    const surface = t[surfaceToken];

    test('primary text is easy to read (AAA, 7:1)', () => {
      expect(contrast(t['--ink-1'], surface)).toBeGreaterThanOrEqual(7);
    });

    test.each(['--ink-2', '--ink-3', '--blue', '--blue-strong', '--green-text', '--red'])(
      '%s meets AA (4.5:1)', (token) => {
        expect(contrast(t[token], surface)).toBeGreaterThanOrEqual(4.5);
      });

    test.each(['--blue', '--green'])('%s as an icon or dot is visible (3:1)', (token) => {
      expect(contrast(t[token], surface)).toBeGreaterThanOrEqual(3);
    });
  });

  test.each([['--on-blue', '--blue'], ['--on-red', '--red'], ['--color-bg', '--ink-1']])(
    'text on a solid fill: %s on %s meets AA', (on, fill) => {
      expect(contrast(t[on], t[fill])).toBeGreaterThanOrEqual(4.5);
    });

  test('the tiles stand out from the canvas they sit on', () => {
    expect(contrast(t['--surface'], t['--canvas'])).toBeGreaterThan(1.05);
  });

  test('recessed areas inside a tile are distinct from the tile', () => {
    expect(contrast(t['--bg-sunken'], t['--surface'])).toBeGreaterThan(1.02);
  });
});

describe('the palette', () => {
  test.each(Object.entries(themes))('%s: blue is blue and green is green', (name, t) => {
    ['--blue', '--blue-strong'].forEach((token) => {
      const { h } = hsl(t[token]);
      expect(h).toBeGreaterThan(205);
      expect(h).toBeLessThan(250);
    });
    ['--green', '--green-text'].forEach((token) => {
      const { h } = hsl(t[token]);
      expect(h).toBeGreaterThan(130);
      expect(h).toBeLessThan(165);
    });
  });

  test.each(Object.entries(themes))('%s: red is only for failure, and is a true red', (name, t) => {
    const { h } = hsl(t['--red']);
    expect(h < 12 || h > 350).toBe(true);
  });

  test.each(Object.entries(themes))('%s: blue and green are clearly different hues', (name, t) => {
    expect(Math.abs(hsl(t['--blue']).h - hsl(t['--green']).h)).toBeGreaterThan(70);
  });

  test.each(Object.entries(themes))('%s: the neutrals are nearly colourless', (name, t) => {
    ['--color-bg', '--canvas', '--bg-sunken', '--surface', '--ink-1', '--ink-2', '--ink-3'].forEach((token) => {
      expect(hsl(t[token]).s).toBeLessThan(0.2);
    });
  });

  test('light is white with near-black ink', () => {
    expect(light['--color-bg'].toUpperCase()).toBe('#FFFFFF');
    expect(luminance(light['--ink-1'])).toBeLessThan(0.01);
  });

  test('dark is near-black with near-white ink', () => {
    expect(luminance(dark['--color-bg'])).toBeLessThan(0.01);
    expect(luminance(dark['--ink-1'])).toBeGreaterThan(0.85);
  });

  test('there is no fifth colour: every saturated token is blue, green or red', () => {
    const family = (h) => ((h >= 130 && h < 165) ? 'green' : (h >= 205 && h < 250) ? 'blue' : (h < 12 || h > 350) ? 'red' : 'other');
    const strangers = Object.entries(light)
      .filter(([, value]) => /^#[0-9a-f]{6}$/i.test(value))
      .filter(([, value]) => hsl(value).s > 0.25 && family(hsl(value).h) === 'other')
      .map(([token]) => token);
    expect(strangers).toEqual([]);
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
    const extra = Object.keys(parseTokens(darkAttributeBlock)).filter((name) => !(name in light));
    expect(extra).toEqual([]);
  });

  test('the dark theme chosen with the toggle matches the one that follows the system', () => {
    expect(parseTokens(darkAttributeBlock)).toEqual(parseTokens(darkMediaBlock));
  });

  test('both themes define the same colours', () => {
    const darkOnly = Object.keys(parseTokens(darkAttributeBlock));
    ['--color-bg', '--canvas', '--bg-sunken', '--surface', '--ink-1', '--ink-2', '--ink-3', '--blue', '--green', '--red',
      '--on-blue', '--on-red'].forEach((token) => expect(darkOnly).toContain(token));
  });

  test('motion is switched off for people who prefer reduced motion', () => {
    expect(css).toMatch(/@media \(prefers-reduced-motion: reduce\)[^}]*{[^}]*animation: none/);
  });

  test('no animation steps: motion is eased, never blocky', () => {
    expect(css).not.toMatch(/steps\(/);
  });
});
