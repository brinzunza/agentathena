// The maths behind the pixel-dissolve transitions: which blocks of an overlay
// are still covering the content at a given moment. No DOM in here, so it can
// be tested; components/PixelOverlay.js paints what this plans.
//
// An overlay is a grid of square blocks. Each block has a threshold between 0
// and 1: it stays until the animation's progress reaches it, then disappears,
// uncovering the content. The thresholds decide the look:
//   dissolve  blocks vanish in random order (static clearing)
//   wave      a diagonal front sweeps across, with some noise in its edge
//   sweep     a wider, noisier diagonal front (for creating a chat)

// Which accent colours (indexes into the overlay's palette) tint the blocks
export const ACCENT_COUNT = 3;

export const VARIANTS = ['dissolve', 'wave', 'sweep'];

// Plain covering blocks come in this many tonal shades (0 = the bare cover
// colour), so the pixel grid shows up even before any accent flecks do
export const TONE_COUNT = 3;

// Deterministic pseudo-random number in [0, 1) for a grid cell, so a frame
// can be re-planned any number of times and always look the same.
export function hash2(x, y, seed = 0) {
  let h = (Math.imul(x | 0, 374761393) + Math.imul(y | 0, 668265263) + Math.imul(seed | 0, 1274126177)) | 0;
  h = Math.imul(h ^ (h >>> 13), 1274126177);
  h ^= h >>> 16;
  return (h >>> 0) / 4294967296;
}

// When (as a fraction of the animation) a block stops covering the content
export function threshold(variant, ix, iy, cols, rows, seed = 0, direction = 'ltr') {
  const noise = hash2(ix, iy, seed);
  if (variant === 'dissolve') return noise;

  const across = cols > 1 ? ix / (cols - 1) : 0;
  const down = rows > 1 ? iy / (rows - 1) : 0;
  const front = ((direction === 'rtl' ? 1 - across : across) + down * 0.6) / 1.6;
  const mix = variant === 'sweep' ? 0.4 : 0.25;
  return Math.min(0.999999, front * (1 - mix) + noise * mix);
}

// Snap progress to a few discrete steps, which is what makes the dissolve
// read as pixel-art stepping rather than a smooth fade.
export function quantize(progress, steps) {
  if (progress >= 1) return 1;
  if (progress <= 0) return 0;
  return Math.floor(progress * steps) / steps;
}

export const easeOutCubic = (t) => 1 - (1 - t) ** 3;

export function toneFor(ix, iy, seed = 0) {
  return Math.floor(hash2(ix, iy, seed + 31) * TONE_COUNT);
}

// The accent for a covering block, or -1 for the plain cover colour. A small
// share of blocks are tinted, which gives the glitchy, techy flecks.
export function accentFor(ix, iy, seed = 0, share = 0.1) {
  const r = hash2(ix, iy, seed + 977);
  return r < share ? Math.min(ACCENT_COUNT - 1, Math.floor((r / share) * ACCENT_COUNT)) : -1;
}

/**
 * Plan one frame: the blocks still covering the content.
 *
 * progress: 0 (everything covered) to 1 (everything uncovered)
 * Returns { cols, rows, progress (quantized), blocks: [{ x, y, w, h, accent, tone }] }
 */
export function planFrame({ width, height, block = 8, variant = 'dissolve', progress = 0, seed = 0,
  steps = 9, direction = 'ltr', accentShare = 0.1 }) {
  const size = Math.max(1, block);
  const cols = Math.max(1, Math.ceil(width / size));
  const rows = Math.max(1, Math.ceil(height / size));
  const shown = quantize(progress, steps);
  const blocks = [];
  if (shown >= 1 || width <= 0 || height <= 0) return { cols, rows, progress: shown, blocks };

  for (let iy = 0; iy < rows; iy += 1) {
    for (let ix = 0; ix < cols; ix += 1) {
      if (threshold(variant, ix, iy, cols, rows, seed, direction) >= shown) {
        blocks.push({
          x: ix * size,
          y: iy * size,
          w: Math.min(size, width - ix * size),
          h: Math.min(size, height - iy * size),
          accent: accentFor(ix, iy, seed, accentShare),
          tone: toneFor(ix, iy, seed),
        });
      }
    }
  }
  return { cols, rows, progress: shown, blocks };
}

// The look of each kind of transition, in one place
export const PRESETS = {
  prompt:   { variant: 'wave',     direction: 'rtl', block: 3,  duration: 480, steps: 8,  accentShare: 0.1 },
  response: { variant: 'dissolve', direction: 'ltr', block: 4,  duration: 640, steps: 9,  accentShare: 0.1 },
  note:     { variant: 'dissolve', direction: 'ltr', block: 3,  duration: 420, steps: 7,  accentShare: 0.06 },
  card:     { variant: 'wave',     direction: 'ltr', block: 5,  duration: 560, steps: 9,  accentShare: 0.12 },
  switch:   { variant: 'dissolve', direction: 'ltr', block: 7,  duration: 420, steps: 8,  accentShare: 0.06 },
  create:   { variant: 'sweep',    direction: 'ltr', block: 8,  duration: 820, steps: 12, accentShare: 0.24 },
  listItem: { variant: 'wave',     direction: 'rtl', block: 3,  duration: 600, steps: 9,  accentShare: 0.3 },
};
