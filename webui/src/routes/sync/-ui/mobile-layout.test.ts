/**
 * The sync page's mobile layout invariants, asserted against style.css.
 *
 * WHY THIS FILE EXISTS. jsdom has no layout engine: it cannot measure a flex
 * item, a grid track, or an overflow, so not one test in this repo can notice
 * an element holding a row wider than the screen. Three separate bugs of
 * exactly that shape shipped before anyone looked at a phone, and each of my
 * first attempts at a fix was itself wrong in a way the rendered tests happily
 * passed.
 *
 * The declarations pinned here were arrived at by MEASURING the real stylesheet
 * in Chromium at 390px, not by reasoning about the cascade. Two fixes that
 * sounded right and did nothing are recorded alongside them, because the
 * tempting thing on seeing these rules is to "simplify" them back:
 *
 *   - `overflow-x: auto` on the chip strip alone did nothing. The strip was
 *     736px wide inside a 246px parent, so it had no overflow of its OWN to
 *     scroll; it overflowed upward until an ancestor clipped it.
 *   - `min-width: 0` on the chain did not help either, and neither did
 *     `flex-basis: 100%` — that percentage resolves against a parent whose main
 *     size is indefinite here, so it falls back to content size.
 *
 * What worked was taking the strips out of the flex/grid chains that were
 * refusing to shrink. Hence the two rules below.
 */

import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { describe, expect, it } from 'vitest';

/** Comments stripped: they discuss the very properties being asserted. */
const css = readFileSync(resolve(process.cwd(), 'static/style.css'), 'utf8').replace(
  /\/\*[\s\S]*?\*\//g,
  '',
);

const phoneBlocks = [...css.matchAll(/@media[^{]*max-width:\s*768px[^{]*\{([\s\S]*?)\n\}/g)].map(
  (m) => m[1],
);

function phoneRule(selector: string): string {
  const escaped = selector.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
  for (const block of phoneBlocks) {
    const found = new RegExp(`(?:^|[,{}\\n])\\s*${escaped}\\s*\\{([^}]*)\\}`).exec(block);
    if (found) return found[1];
  }
  return '';
}

describe('the strips can actually scroll', () => {
  it('the filter row is a BLOCK on a phone, not a flex container', () => {
    // As a flex item the chip strip will not shrink below its min-content
    // width — nine source chips — so it never had its own overflow to scroll.
    // As a block-level child it is simply parent-width, and overflow-x can do
    // its job. Measured: scrollWidth 736 inside a 246px box.
    expect(phoneRule('.library-filters')).toMatch(/display:\s*block/);
  });

  it('the header action row is a BLOCK too, for the same reason', () => {
    // Seven buttons held .sync-header-row open, which widened the shell and
    // then the document: the page came out 793px wide in a 390px viewport, so
    // EVERY page in the app scrolled sideways.
    expect(phoneRule('.sync-header-row')).toMatch(/display:\s*block/);
  });

  it('both strips still declare the overflow that does the scrolling', () => {
    const strips = phoneBlocks.find(
      (b) => b.includes('.library-sources') && b.includes('.sync-header-actions'),
    );
    expect(strips, 'no phone block sets up the scrolling strips').toBeTruthy();
    expect(strips).toMatch(/overflow-x:\s*auto/);
  });
});

describe('no grid track refuses to shrink', () => {
  it('.sync-content-area never uses a bare `1fr`', () => {
    // A bare `1fr` is `minmax(auto, 1fr)`, and that `auto` minimum is the
    // item's MIN-CONTENT size — so the track will not go narrower than the
    // widest unbreakable thing inside it, whatever the viewport says. This one
    // held .sync-main-panel at 748px on a 390px screen.
    const rules = [...css.matchAll(/\.sync-content-area[^{]*\{([^}]*)\}/g)].map((m) => m[1]);
    expect(rules.length).toBeGreaterThan(0);
    for (const rule of rules) {
      const track = /grid-template-columns:([^;]*);/.exec(rule)?.[1];
      if (!track) continue;
      // minmax() groups removed first — the `1fr` inside `minmax(0, 1fr)` is
      // the FIX, not the bug. What must not survive is a bare one.
      const outside = track.replace(/minmax\([^)]*\)/g, '');
      expect(outside, `bare fr in grid-template-columns:${track}`).not.toMatch(/[\d.]+fr/);
    }
  });
});
