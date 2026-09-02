import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { describe, expect, it } from 'vitest';

/**
 * Where the episode row's spare width goes.
 *
 * Three attempts, and the first two each traded one problem for another:
 *
 * 1. The row grid gave its TEXT column the 1fr, so every extra pixel of screen
 *    went into the description and shoved the action buttons to the far right
 *    edge - most of a screen away from the episode they belong to.
 * 2. Capping the whole page fixed the distance but centred the hero, which read
 *    as the page having shrunk. A full-bleed backdrop wants its content anchored
 *    to the artwork.
 * 3. Left-anchoring the capped body fixed that, and left a dead band down the
 *    right of every list instead.
 *
 * The fix belongs in the ROW: cap the text column and pack the tracks left, so
 * the buttons sit beside their episode and the page keeps the full width it was
 * given.
 *
 * NOTE ON WHAT THIS CAN AND CANNOT DO. jsdom does not lay out and this
 * environment has no Chromium, so nothing here MEASURES anything - it pins the
 * declarations that have to agree. A regression fence, not proof.
 */

const CSS = readFileSync(resolve(process.cwd(), 'static/video/video-side.css'), 'utf8');

function block(selector: string): string {
  const at = CSS.indexOf(`\n${selector} {`);
  expect(at, `${selector} has no own rule`).toBeGreaterThan(-1);
  return CSS.slice(at, CSS.indexOf('}', at) + 1);
}

describe('the episode row keeps its actions next to the episode', () => {
  it('caps the text column instead of letting it absorb the screen', () => {
    // `1fr` here is the original bug: the description grows, the buttons leave.
    const row = block('.vd-ep');
    expect(row).toContain('minmax(0, 720px)');
    expect(row).not.toMatch(/grid-template-columns:[^;]*\b1fr\b/);
  });

  it('packs the tracks left so the slack lands after the buttons', () => {
    expect(block('.vd-ep')).toContain('justify-content: start');
  });

  it('does the same for a youtube row, which has no index column', () => {
    const yt = block('.vd-ep--yt');
    expect(yt).toContain('minmax(0, 720px)');
    expect(yt).not.toMatch(/\b1fr\b/);
  });
});

describe('the page keeps the width it was given', () => {
  it('does not cap the body', () => {
    // Capping left a dead band down the right of every list. The row grid above
    // is what keeps the buttons close, so the page does not need to shrink.
    expect(block('.vd-body')).not.toContain('max-width');
  });

  it('does not centre either half', () => {
    expect(block('.vd-body')).not.toMatch(/margin:\s*0 auto/);
    expect(block('.vd-bb-content')).not.toMatch(/margin:\s*0 auto/);
  });

  it('anchors the billboard and the body to the same left edge', () => {
    expect(block('.vd-bb-content')).toContain('padding: 0 0 52px 56px');
    expect(block('.vd-body')).toContain('padding: 30px 56px 70px');
  });

  it('keeps the hero text to its own readable measure over the artwork', () => {
    expect(block('.vd-bb-content')).toContain('max-width: 720px');
    expect(block('.vd-overview')).toContain('max-width: 640px');
  });

  it('leaves the backdrop full-bleed', () => {
    expect(block('.vd-bb-bg')).not.toContain('max-width');
  });
});
