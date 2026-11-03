import {
  ACCENT_COUNT,
  PRESETS,
  TONE_COUNT,
  VARIANTS,
  accentFor,
  easeOutCubic,
  hash2,
  planFrame,
  quantize,
  threshold,
  toneFor,
} from './pixel';

describe('hash2', () => {
  test('is deterministic', () => {
    expect(hash2(3, 7, 11)).toBe(hash2(3, 7, 11));
    expect(hash2(3, 7, 11)).not.toBe(hash2(7, 3, 11));
    expect(hash2(3, 7, 11)).not.toBe(hash2(3, 7, 12));
  });

  test('stays within [0, 1) and is spread out evenly', () => {
    const buckets = new Array(10).fill(0);
    let sum = 0;
    const samples = 20000;
    for (let i = 0; i < samples; i += 1) {
      const value = hash2(i % 173, Math.floor(i / 173), 42);
      expect(value).toBeGreaterThanOrEqual(0);
      expect(value).toBeLessThan(1);
      buckets[Math.floor(value * 10)] += 1;
      sum += value;
    }
    expect(sum / samples).toBeGreaterThan(0.48);
    expect(sum / samples).toBeLessThan(0.52);
    buckets.forEach((count) => expect(count / samples).toBeGreaterThan(0.08));
  });
});

describe('threshold (when a block stops covering the content)', () => {
  const grid = { cols: 30, rows: 20 };

  test.each(VARIANTS)('%s is always within [0, 1)', (variant) => {
    for (let iy = 0; iy < grid.rows; iy += 1) {
      for (let ix = 0; ix < grid.cols; ix += 1) {
        const t = threshold(variant, ix, iy, grid.cols, grid.rows, 5, 'ltr');
        expect(t).toBeGreaterThanOrEqual(0);
        expect(t).toBeLessThan(1);
      }
    }
  });

  const meanAt = (variant, direction, ixFrom, ixTo) => {
    let sum = 0;
    let n = 0;
    for (let iy = 0; iy < grid.rows; iy += 1) {
      for (let ix = ixFrom; ix < ixTo; ix += 1) {
        sum += threshold(variant, ix, iy, grid.cols, grid.rows, 9, direction);
        n += 1;
      }
    }
    return sum / n;
  };

  test.each(['wave', 'sweep'])('%s going left to right clears the left side first', (variant) => {
    expect(meanAt(variant, 'ltr', 0, 5)).toBeLessThan(meanAt(variant, 'ltr', 25, 30) - 0.2);
  });

  test.each(['wave', 'sweep'])('%s going right to left clears the right side first', (variant) => {
    expect(meanAt(variant, 'rtl', 25, 30)).toBeLessThan(meanAt(variant, 'rtl', 0, 5) - 0.2);
  });

  test('dissolve has no direction: both sides clear about equally fast', () => {
    expect(Math.abs(meanAt('dissolve', 'ltr', 0, 5) - meanAt('dissolve', 'ltr', 25, 30))).toBeLessThan(0.1);
  });

  test('a single column or row does not divide by zero', () => {
    expect(Number.isFinite(threshold('wave', 0, 0, 1, 1, 3, 'ltr'))).toBe(true);
    expect(Number.isFinite(threshold('sweep', 0, 4, 1, 9, 3, 'rtl'))).toBe(true);
  });
});

describe('quantize and easing', () => {
  test('snaps to steps', () => {
    expect(quantize(0, 5)).toBe(0);
    expect(quantize(0.19, 5)).toBe(0);
    expect(quantize(0.21, 5)).toBeCloseTo(0.2);
    expect(quantize(0.99, 5)).toBeCloseTo(0.8);
    expect(quantize(1, 5)).toBe(1);
    expect(quantize(1.4, 5)).toBe(1);
    expect(quantize(-1, 5)).toBe(0);
  });

  test('easeOutCubic starts at 0, ends at 1, and is front-loaded', () => {
    expect(easeOutCubic(0)).toBe(0);
    expect(easeOutCubic(1)).toBe(1);
    expect(easeOutCubic(0.5)).toBeGreaterThan(0.5);
  });
});

describe('accents and tones', () => {
  test('about the requested share of blocks get an accent, spread across the palette', () => {
    const counts = new Array(ACCENT_COUNT).fill(0);
    let plain = 0;
    const total = 20000;
    for (let i = 0; i < total; i += 1) {
      const accent = accentFor(i % 200, Math.floor(i / 200), 8, 0.1);
      if (accent < 0) plain += 1;
      else counts[accent] += 1;
    }
    expect(1 - plain / total).toBeGreaterThan(0.085);
    expect(1 - plain / total).toBeLessThan(0.115);
    counts.forEach((count) => expect(count).toBeGreaterThan(total * 0.02));
  });

  test('a share of zero tints nothing, and tones stay within range', () => {
    for (let i = 0; i < 500; i += 1) {
      expect(accentFor(i, i * 3, 1, 0)).toBe(-1);
      const tone = toneFor(i, i * 7, 1);
      expect(tone).toBeGreaterThanOrEqual(0);
      expect(tone).toBeLessThan(TONE_COUNT);
    }
  });
});

describe('planFrame', () => {
  const base = { width: 200, height: 120, block: 10, seed: 3, steps: 10 };

  test('at the start every block covers the content; at the end none does', () => {
    const start = planFrame({ ...base, variant: 'dissolve', progress: 0 });
    expect(start.cols).toBe(20);
    expect(start.rows).toBe(12);
    expect(start.blocks).toHaveLength(20 * 12);
    expect(planFrame({ ...base, variant: 'dissolve', progress: 1 }).blocks).toEqual([]);
  });

  test.each(VARIANTS)('%s uncovers blocks steadily, never re-covering one', (variant) => {
    const counts = [];
    const recovered = [];         // blocks that were gone in one frame and back in the next
    let previousKeys = null;
    for (let step = 0; step <= 10; step += 1) {
      const plan = planFrame({ ...base, variant, progress: step / 10 });
      const keys = new Set(plan.blocks.map((b) => `${b.x},${b.y}`));
      const before = previousKeys;
      if (before) recovered.push(...[...keys].filter((key) => !before.has(key)));
      counts.push(plan.blocks.length);
      previousKeys = keys;
    }
    expect(recovered).toEqual([]);
    expect(counts).toEqual([...counts].sort((a, b) => b - a));      // never goes up
    expect(counts[0]).toBeGreaterThan(0);
    expect(counts[counts.length - 1]).toBe(0);
  });

  test('the same seed gives the same frame, a different seed a different one', () => {
    const a = planFrame({ ...base, variant: 'dissolve', progress: 0.5 });
    const b = planFrame({ ...base, variant: 'dissolve', progress: 0.5 });
    const c = planFrame({ ...base, variant: 'dissolve', progress: 0.5, seed: 4 });
    expect(a).toEqual(b);
    expect(a.blocks).not.toEqual(c.blocks);
  });

  test('progress moves in discrete steps, which is the pixel-art cadence', () => {
    const count = (p) => planFrame({ ...base, variant: 'dissolve', progress: p, steps: 4 }).blocks.length;
    expect(count(0.26)).toBe(count(0.49));
    expect(count(0.51)).toBeLessThan(count(0.49));
    expect(planFrame({ ...base, variant: 'dissolve', progress: 0.3, steps: 4 }).progress).toBe(0.25);
  });

  test('edge blocks are clipped to the area, so nothing is painted outside it', () => {
    const plan = planFrame({ width: 95, height: 47, block: 10, variant: 'dissolve', progress: 0, seed: 1 });
    expect(plan.cols).toBe(10);
    expect(plan.rows).toBe(5);
    plan.blocks.forEach((b) => {
      expect(b.w).toBeGreaterThan(0);
      expect(b.h).toBeGreaterThan(0);
      expect(b.x + b.w).toBeLessThanOrEqual(95);
      expect(b.y + b.h).toBeLessThanOrEqual(47);
    });
    const area = plan.blocks.reduce((sum, b) => sum + b.w * b.h, 0);
    expect(area).toBe(95 * 47);
  });

  test('an empty area plans nothing', () => {
    expect(planFrame({ ...base, width: 0, height: 50, variant: 'dissolve', progress: 0 }).blocks).toEqual([]);
    expect(planFrame({ ...base, width: 50, height: 0, variant: 'dissolve', progress: 0 }).blocks).toEqual([]);
  });

  test('a wave from the right leaves the left covered longest', () => {
    const plan = planFrame({ ...base, variant: 'wave', direction: 'rtl', progress: 0.4 });
    const left = plan.blocks.filter((b) => b.x < 60).length;
    const right = plan.blocks.filter((b) => b.x >= 140).length;
    expect(left).toBeGreaterThan(right);
  });

  test('blocks carry an accent index (or -1) and a tone', () => {
    planFrame({ ...base, variant: 'sweep', progress: 0, accentShare: 0.3 }).blocks.forEach((b) => {
      expect(b.accent).toBeGreaterThanOrEqual(-1);
      expect(b.accent).toBeLessThan(ACCENT_COUNT);
      expect(b.tone).toBeGreaterThanOrEqual(0);
      expect(b.tone).toBeLessThan(TONE_COUNT);
    });
  });
});

describe('presets', () => {
  test.each(Object.entries(PRESETS))('%s is a valid look', (name, preset) => {
    expect(VARIANTS).toContain(preset.variant);
    expect(['ltr', 'rtl']).toContain(preset.direction);
    expect(preset.block).toBeGreaterThanOrEqual(4);
    expect(preset.duration).toBeGreaterThan(200);
    expect(preset.duration).toBeLessThan(1200);     // transitions stay quick
    expect(preset.steps).toBeGreaterThanOrEqual(6);
    expect(preset.accentShare).toBeGreaterThanOrEqual(0);
    expect(preset.accentShare).toBeLessThan(0.5);
  });

  test('creating a chat is the grandest transition: biggest blocks, longest, most flecks', () => {
    const others = Object.entries(PRESETS).filter(([name]) => name !== 'create').map(([, p]) => p);
    others.forEach((other) => {
      expect(PRESETS.create.block).toBeGreaterThanOrEqual(other.block);
      expect(PRESETS.create.duration).toBeGreaterThan(other.duration);
    });
  });

  test('your prompt sweeps in from the right; replies dissolve', () => {
    expect(PRESETS.prompt.direction).toBe('rtl');
    expect(PRESETS.response.variant).toBe('dissolve');
  });
});
