// Guards the palette. There are two surfaces in each theme: the canvas (the sidebar, on charcoal in
// dark) and the paper (the chat card and dialogs, which re-point the usual colour names at the p-
// tokens). Every text colour must stay readable (WCAG AA) on the surfaces it can appear on, on both,
// in both themes, and the hues must stay what the design says they are.

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
const paperScopeBlock = css.match(/\.main,\s*\.modal-card\s*{([^}]*)}/)[1];
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

const chroma = (hex) => Math.max(...rgb(hex)) - Math.min(...rgb(hex));

// The colour names the paper scope re-points, and the p- token each one reads
const PAPER_MAP = {
  '--color-bg': '--p-paper',
  '--surface': '--p-paper',
  '--bg-sunken': '--p-sunken',
  '--color-ink': '--p-ink-1',
  '--ink-1': '--p-ink-1',
  '--ink-2': '--p-ink-2',
  '--ink-3': '--p-ink-3',
  '--line': '--p-line',
  '--line-strong': '--p-line-strong',
  '--hover': '--p-hover',
  '--wash': '--p-wash',
  '--blue': '--p-blue',
  '--blue-strong': '--p-blue-strong',
  '--blue-wash': '--p-blue-wash',
  '--blue-line': '--p-blue-line',
  '--green': '--p-green',
  '--green-text': '--p-green-text',
  '--green-wash': '--p-green-wash',
  '--red': '--p-red',
  '--red-wash': '--p-red-wash',
  '--red-line': '--p-red-line',
  '--on-blue': '--p-on-blue',
  '--on-red': '--p-on-red',
};

// A theme seen from each surface, under the usual token names
function surfaceView(t, surface) {
  if (surface === 'canvas') return { ...t, '--color-bg': t['--canvas'], '--surface': t['--canvas'] };
  const view = { ...t };
  Object.entries(PAPER_MAP).forEach(([name, source]) => { view[name] = t[source]; });
  return view;
}

describe.each(Object.entries(themes))('%s theme', (themeName, t) => {
  describe.each(['canvas', 'paper'])('on the %s', (surfaceName) => {
    const v = surfaceView(t, surfaceName);
    const base = { canvas: '--canvas', paper: '--p-paper' }[surfaceName];
    const recessed = { canvas: '--bg-sunken', paper: '--p-sunken' }[surfaceName];

    test('every colour used as text or fill is a plain hex value', () => {
      ['--color-bg', '--surface', '--bg-sunken', '--ink-1', '--ink-2', '--ink-3', '--blue', '--blue-strong', '--green',
        '--green-text', '--red', '--on-blue', '--on-red'].forEach((token) => expect(v[token]).toMatch(/^#[0-9a-fA-F]{6}$/));
    });

    describe.each([['surface', base], ['recessed area', recessed]])('text on the %s', (n, token) => {
      const surface = t[token];

      test('primary text is easy to read (AAA, 7:1)', () => {
        expect(contrast(v['--ink-1'], surface)).toBeGreaterThanOrEqual(7);
      });

      test.each(['--ink-2', '--ink-3', '--blue', '--blue-strong', '--green-text', '--red'])('%s meets AA (4.5:1)', (name) => {
        expect(contrast(v[name], surface)).toBeGreaterThanOrEqual(4.5);
      });

      test.each(['--blue', '--green'])('%s as an icon or dot is visible (3:1)', (name) => {
        expect(contrast(v[name], surface)).toBeGreaterThanOrEqual(3);
      });
    });

    test.each([['--on-blue', '--blue'], ['--on-red', '--red'], ['--color-bg', '--ink-1']])(
      'text on a solid fill: %s on %s meets AA', (on, fill) => {
        expect(contrast(v[on], v[fill])).toBeGreaterThanOrEqual(4.5);
      });

    test('recessed areas are distinct from the surface they sit in', () => {
      expect(contrast(t[recessed], t[base])).toBeGreaterThan(1.03);
    });
  });

  test('the paper card stands clear of the canvas around it', () => {
    expect(contrast(t['--p-paper'], t['--canvas'])).toBeGreaterThan(1.25);
  });
});

describe('the palette', () => {
  const family = (h) => ((h >= 85 && h < 165) ? 'green' : (h >= 205 && h < 250) ? 'blue' : (h < 12 || h > 350) ? 'red' : 'other');

  test.each(Object.entries(themes))('%s: blue is blue and green is an olive-to-leaf green', (name, t) => {
    ['--blue', '--blue-strong', '--p-blue', '--p-blue-strong'].forEach((token) => {
      expect(family(hsl(t[token]).h)).toBe('blue');
    });
    ['--green', '--green-text', '--p-green', '--p-green-text'].forEach((token) => {
      expect(family(hsl(t[token]).h)).toBe('green');
    });
  });

  test.each(Object.entries(themes))('%s: red is only for failure, and is a true red', (name, t) => {
    ['--red', '--p-red'].forEach((token) => {
      const { h } = hsl(t[token]);
      expect(h < 12 || h > 350).toBe(true);
    });
  });

  test.each(Object.entries(themes))('%s: blue and green are clearly different hues', (name, t) => {
    expect(Math.abs(hsl(t['--blue']).h - hsl(t['--green']).h)).toBeGreaterThan(70);
    expect(Math.abs(hsl(t['--p-blue']).h - hsl(t['--p-green']).h)).toBeGreaterThan(70);
  });

  test.each(Object.entries(themes))('%s: the greys are neutral', (name, t) => {
    ['--color-bg', '--canvas', '--surface', '--bg-sunken', '--ink-1', '--ink-2', '--ink-3', '--p-paper', '--p-sunken',
      '--p-ink-1', '--p-ink-2', '--p-ink-3'].forEach((token) => {
      expect(chroma(t[token])).toBeLessThan(0.03);
    });
  });

  test('dark is charcoal, not black, with a light-grey paper card', () => {
    expect(luminance(dark['--canvas'])).toBeGreaterThan(0.02);
    expect(luminance(dark['--canvas'])).toBeLessThan(0.1);
    expect(luminance(dark['--p-paper'])).toBeGreaterThan(0.4);
    expect(luminance(dark['--ink-1'])).toBeGreaterThan(0.8);
    expect(luminance(dark['--p-ink-1'])).toBeLessThan(0.02);
  });

  test('light is a pale grey canvas with near-white paper and near-black ink', () => {
    expect(luminance(light['--canvas'])).toBeGreaterThan(0.5);
    expect(luminance(light['--p-paper'])).toBeGreaterThan(0.85);
    expect(luminance(light['--ink-1'])).toBeLessThan(0.02);
  });

  test('there is no fifth colour: every saturated token is blue, green or red', () => {
    const strangers = Object.entries(light)
      .filter(([, value]) => /^#[0-9a-f]{6}$/i.test(value))
      .filter(([, value]) => hsl(value).s > 0.25 && family(hsl(value).h) === 'other')
      .map(([token]) => token);
    expect(strangers).toEqual([]);
  });
});

describe('the terminal', () => {
  // It is a dark console whatever the theme, so one set of colours has to work on its one background
  test.each(Object.entries(themes))('%s: its text is readable on the console', (name, t) => {
    const bg = t['--term-bg'];
    expect(contrast(t['--term-ink'], bg)).toBeGreaterThanOrEqual(7);
    ['--term-dim', '--term-green', '--term-blue', '--term-red'].forEach((token) => {
      expect(contrast(t[token], bg)).toBeGreaterThanOrEqual(4.5);
    });
  });

  test('its colours keep to the palette: green, blue and red only', () => {
    const family = (h) => ((h >= 85 && h < 165) ? 'green' : (h >= 205 && h < 250) ? 'blue' : (h < 12 || h > 350) ? 'red' : 'other');
    expect(family(hsl(light['--term-green']).h)).toBe('green');
    expect(family(hsl(light['--term-blue']).h)).toBe('blue');
    expect(family(hsl(light['--term-red']).h)).toBe('red');
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
      '--on-blue', '--on-red', '--grain', '--paper-shadow', ...Object.values(PAPER_MAP)].forEach((token) => {
      expect(darkOnly).toContain(token);
    });
  });

  test('the paper scope re-points every colour name at its p- token', () => {
    const scope = parseTokens(paperScopeBlock);
    Object.entries(PAPER_MAP).forEach(([name, source]) => expect(scope[name]).toBe(`var(${source})`));
  });

  test('the grain is a plain data URI, so the stylesheet parses in one piece', () => {
    expect(light['--grain']).toMatch(/^url\("data:image\/svg\+xml,[^"]*"\)$/);
    expect(dark['--grain']).toMatch(/^url\("data:image\/svg\+xml,[^"]*"\)$/);
  });

  test('motion is switched off for people who prefer reduced motion', () => {
    expect(css).toMatch(/@media \(prefers-reduced-motion: reduce\)[^}]*{[^}]*animation: none/);
  });

  test('no animation steps: motion is eased, never blocky', () => {
    expect(css).not.toMatch(/steps\(/);
  });
});
