import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { describe, expect, it } from 'vitest';

/**
 * How wide the detail page's content runs.
 *
 * Two failure modes, and fixing one caused the other:
 *
 * 1. The body originally ran the FULL viewport while the billboard capped at
 *    720px, so on an ultrawide monitor an episode's action buttons sat most of
 *    a screen away from the episode they belonged to.
 * 2. The first fix centred both halves on a shared measure. That reads as the
 *    page having shrunk - a full-bleed backdrop wants its content anchored to
 *    the artwork, not floating in the middle of it (Boulder, on sight).
 *
 * So: cap the body, but keep BOTH halves anchored to the same left edge.
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

describe('the detail page content width', () => {
  it('caps the body instead of letting it run the whole viewport', () => {
    expect(block('.vd-body')).toContain('max-width: var(--vd-measure)');
    expect(block('.vd-page')).toContain('--vd-measure:');
  });

  it('never centres either half', () => {
    // `margin: 0 auto` on either one is the "why has the page shrunk" bug.
    expect(block('.vd-body')).not.toMatch(/margin:\s*0 auto/);
    expect(block('.vd-bb-content')).not.toMatch(/margin:\s*0 auto/);
  });

  it('anchors the billboard and the body to the same left edge', () => {
    // 56px both, so the episode list starts under the title rather than
    // stepping in or out from it.
    expect(block('.vd-bb-content')).toContain('padding: 0 0 52px 56px');
    expect(block('.vd-body')).toContain('padding: 30px 56px 70px');
  });

  it('keeps the hero text to its own readable measure', () => {
    // The billboard sits over artwork and has always been the narrower column;
    // the body below it is free to be wider.
    expect(block('.vd-bb-content')).toContain('max-width: 720px');
    expect(block('.vd-overview')).toContain('max-width: 640px');
  });

  it('leaves the backdrop full-bleed', () => {
    // Only CONTENT is constrained; constraining the art would letterbox the hero.
    expect(block('.vd-bb-bg')).not.toContain('--vd-measure');
  });
});
