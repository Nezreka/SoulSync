import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { describe, expect, it } from 'vitest';

import { extractFunction } from './vanilla-extract';

/**
 * Text placed on the poster-sampled accent.
 *
 * The detail page lifts its accent colour out of the artwork, and the Trailer
 * button uses that accent as a solid background with text on top. That text was
 * hard-coded white, so Dark Matter - whose poster is pale - sampled to a
 * near-white accent and rendered a white button with white text. It was not
 * dimmed or low-contrast; it was invisible.
 *
 * A colour taken from arbitrary artwork cannot assume white text just because
 * the page around it is dark, so the foreground has to be derived from it.
 */

const SRC = readFileSync(resolve(process.cwd(), 'static/video/video-detail.js'), 'utf8');

// eslint-disable-next-line @typescript-eslint/no-implied-eval
const accentFg = new Function(
  `${extractFunction('accentFg', SRC)}\nreturn accentFg;`,
)() as (rgb: number[]) => string;

const DARK = '#fff';
const LIGHT = '#0b0b0f';

describe('text on a poster-sampled accent', () => {
  it('goes dark on the pale accent that broke the Trailer button', () => {
    expect(accentFg([235, 232, 220])).toBe(LIGHT); // Dark Matter's cream
    expect(accentFg([255, 255, 255])).toBe(LIGHT);
    expect(accentFg([255, 241, 118])).toBe(LIGHT); // saturated yellow reads light
  });

  it('stays white on a genuinely dark accent', () => {
    expect(accentFg([0, 0, 0])).toBe(DARK);
    expect(accentFg([88, 101, 242])).toBe(DARK); // the default theme accent
    expect(accentFg([139, 0, 0])).toBe(DARK);
  });

  it('weights green over blue, because the eye does', () => {
    // Pure blue is far darker to the eye than pure green at the same value;
    // a naive (r+g+b)/3 average calls them equal and picks wrong on both.
    expect(accentFg([0, 0, 255])).toBe(DARK);
    expect(accentFg([0, 255, 0])).toBe(LIGHT);
  });

  it('never returns something a stylesheet cannot use', () => {
    for (const rgb of [[0, 0, 0], [255, 255, 255], [17, 200, 90], [200, 30, 30]]) {
      expect(accentFg(rgb)).toMatch(/^#[0-9a-f]{3,8}$/);
    }
  });
});

describe('the accent contrast is actually wired up', () => {
  const CSS = readFileSync(resolve(process.cwd(), 'static/video/video-side.css'), 'utf8');

  it('gives every accent-backed control the derived foreground', () => {
    // A hard-coded `color: #fff` on an accent background is the original bug.
    const accentBacked = CSS.split('\n').filter(
      (l) => l.includes('background: rgb(var(--vd-accent-rgb))') ||
             l.includes('background: rgba(var(--vd-accent-rgb), 0.85)'),
    );
    expect(accentBacked.length).toBeGreaterThan(0);
    for (const rule of ['.vd-trailer-btn', '.vd-vt-btn--active']) {
      // Anchor at the start of a line: `.vd-trailer-btn` also appears inside the
      // compound `.vd-actions .vd-trailer-btn` rule, which sets only sizing.
      const at = CSS.indexOf(`\n${rule} {`);
      expect(at, `${rule} has no own rule`).toBeGreaterThan(-1);
      const block = CSS.slice(at, at + 400);
      expect(block, `${rule} still hard-codes its text colour`).toContain('var(--vd-accent-fg)');
    }
  });

  it('has a fallback for before any poster has been sampled', () => {
    expect(CSS).toContain('--vd-accent-fg: #fff;');
  });

  it('sets the variable next to the accent it belongs to', () => {
    expect(SRC).toContain("r0.style.setProperty('--vd-accent-fg', accentFg(best));");
  });
});
