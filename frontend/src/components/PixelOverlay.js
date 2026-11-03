import React, { useLayoutEffect, useRef, useState } from 'react';
import { PRESETS, easeOutCubic, planFrame } from '../pixel';

const prefersReducedMotion = () =>
  typeof window !== 'undefined' && !!window.matchMedia?.('(prefers-reduced-motion: reduce)').matches;

// The colours blocks are painted with, read from the current theme so the
// transition matches light or dark: the colour it sits on hides content, and a
// few blocks are flecked with the blue, orange and green accents.
function readPalette(ground) {
  const css = getComputedStyle(document.documentElement);
  const read = (name, fallback) => css.getPropertyValue(name).trim() || fallback;
  return {
    cover: read(ground, '#F9F4E8'),
    accents: [read('--blue', '#2B6A91'), read('--orange', '#E08838'), read('--green', '#4C8564')],
  };
}

/**
 * A grid of blocks that covers its parent and dissolves away, revealing what
 * is underneath. Put it inside a `position: relative` element.
 *
 *   preset  which look to use (see PRESETS in pixel.js)
 *   ground  the CSS variable of the colour the overlay sits on: the content
 *           card by default, '--color-bg' on the sidebar
 *   play    starts the first time this becomes true (so content that isn't
 *           there yet can be waited for); it never replays by itself
 *   onDone  called once it has finished (or was skipped)
 *
 * Skipped entirely, with no flash, for people who prefer reduced motion.
 */
function PixelOverlay({ preset = 'response', play = true, onDone, accentShare, ground = '--surface' }) {
  const canvasRef = useRef(null);
  const started = useRef(false);
  const [finished, setFinished] = useState(false);

  useLayoutEffect(() => {
    if (!play || started.current) return undefined;
    started.current = true;

    const canvas = canvasRef.current;
    const host = canvas?.parentElement;
    const context = canvas?.getContext?.('2d');
    const look = { ...PRESETS[preset], ...(accentShare !== undefined ? { accentShare } : {}) };
    if (!context || !host || prefersReducedMotion()) {
      setFinished(true);
      onDone?.();
      return undefined;
    }

    const palette = readPalette(ground);
    const seed = Math.floor(Math.random() * 1_000_000);
    const ratio = window.devicePixelRatio || 1;
    const startedAt = performance.now();
    let frame;

    const paint = (now) => {
      const progress = Math.min(1, (now - startedAt) / look.duration);
      const width = host.clientWidth;
      const height = host.clientHeight;
      // The host can grow while this runs (a streaming reply), so size to it every frame
      if (canvas.width !== Math.ceil(width * ratio) || canvas.height !== Math.ceil(height * ratio)) {
        canvas.width = Math.ceil(width * ratio);
        canvas.height = Math.ceil(height * ratio);
      }
      context.setTransform(ratio, 0, 0, ratio, 0, 0);
      context.clearRect(0, 0, width, height);

      const plan = planFrame({
        width, height, seed,
        block: look.block, variant: look.variant, direction: look.direction,
        steps: look.steps, accentShare: look.accentShare,
        progress: easeOutCubic(progress),
      });
      for (const cell of plan.blocks) {
        context.fillStyle = cell.accent >= 0 ? palette.accents[cell.accent] : palette.cover;
        context.fillRect(cell.x, cell.y, cell.w, cell.h);
        if (cell.accent < 0 && cell.tone > 0) {
          // A whisper of blue over the cover colour, in two strengths
          context.globalAlpha = cell.tone * 0.07;
          context.fillStyle = palette.accents[0];
          context.fillRect(cell.x, cell.y, cell.w, cell.h);
          context.globalAlpha = 1;
        }
      }

      if (progress < 1) {
        frame = requestAnimationFrame(paint);
      } else {
        setFinished(true);
        onDone?.();
      }
    };

    paint(startedAt);          // the fully covered first frame, before the browser paints the content
    frame = requestAnimationFrame(paint);
    return () => cancelAnimationFrame(frame);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [play]);

  if (finished) return null;
  return <canvas ref={canvasRef} className="pixel-canvas" aria-hidden="true" />;
}

export default PixelOverlay;
