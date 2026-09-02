import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { describe, expect, it } from 'vitest';

/**
 * One page measure.
 *
 * The billboard capped its content at 720px while the body below it ran the
 * full viewport, so the detail page had two different widths: a cramped hero on
 * the left with dead space beside it, and an episode list stretched edge to
 * edge whose action buttons ended up most of a screen away from the episode
 * they belonged to.
 *
 * NOTE ON WHAT THIS CAN AND CANNOT DO. jsdom does not lay out, and this
 * environment has no Chromium, so nothing here MEASURES anything - it pins the
 * declarations that have to agree. The rendered result still needs a real
 * browser (or a pair of eyes) to confirm. It is a regression fence, not proof.
 */

const CSS = readFileSync(resolve(process.cwd(), 'static/video/video-side.css'), 'utf8');

/** The declaration block for a top-level rule, by exact selector. */
function block(selector: string): string {
  const at = CSS.indexOf(`\n${selector} {`);
  expect(at, `${selector} has no own rule`).toBeGreaterThan(-1);
  return CSS.slice(at, CSS.indexOf('}', at) + 1);
}

describe('the detail page has one content measure', () => {
  it('defines the measure once, on the page root', () => {
    expect(block('.vd-page')).toContain('--vd-measure:');
  });

  it('binds the billboard and the body to that same measure', () => {
    // If either of these goes back to a literal width, the two halves drift
    // apart again and nothing else in the suite would notice.
    expect(block('.vd-bb-content')).toContain('max-width: var(--vd-measure)');
    expect(block('.vd-body')).toContain('max-width: var(--vd-measure)');
  });

  it('centres both, so they share a left edge instead of one hugging it', () => {
    expect(block('.vd-bb-content')).toMatch(/margin:\s*0 auto/);
    expect(block('.vd-body')).toMatch(/margin:\s*0 auto/);
  });

  it('keeps hero text to a readable line length inside the wider container', () => {
    // Widening the container without this makes the overview run the full
    // measure, which is unreadable prose.
    const rule = CSS.slice(
      CSS.indexOf('.vd-bb-content > .vd-title'),
      CSS.indexOf('.vd-bb-content > .vd-title') + 700,
    );
    for (const child of ['.vd-meta', '.vd-genres', '.vd-links', '.vd-ratings', '.vd-tagline']) {
      expect(rule, `${child} would stretch to the full measure`).toContain(
        `.vd-bb-content > ${child}`,
      );
    }
    expect(rule).toMatch(/max-width:\s*760px/);
    // The overview keeps its own, narrower measure - it is the longest prose.
    expect(block('.vd-overview')).toContain('max-width: 640px');
  });

  it('leaves the backdrop full-bleed', () => {
    // Only CONTENT is constrained; constraining the art would letterbox the hero.
    expect(block('.vd-bb-bg')).not.toContain('--vd-measure');
  });
});
