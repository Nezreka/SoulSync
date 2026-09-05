import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { describe, expect, it } from 'vitest';

/**
 * Where the release card's play button sits, and why it is not the middle.
 *
 * The card and the button do two different things: clicking the card opens the
 * album detail modal, clicking the button replaces the player queue. That split
 * is already pinned by release-card.test.tsx (the button stops propagation).
 *
 * The bug was never the wiring, it was the affordance. The button was centred
 * on the artwork at left:50%/top:50%, which is exactly where someone aims when
 * they mean "open this album" - so a click meant to show the tracklist silently
 * started playing instead. Boulder: "users may accidentally click that button
 * thinking it will open the detail modal ... maybe play button is in bottom left".
 *
 * jsdom cannot do layout, so this reads the rule rather than measuring it. The
 * real geometry was measured in headless chrome on a 260x260 card: the title
 * strip is 65px tall with its top edge 66px up, so bottom:74px clears it, and
 * left:14px matches the strip's own inset so the button lines up under the
 * album name. The library artist card has always used a corner for the same
 * reason.
 */

const CSS = readFileSync(resolve(process.cwd(), 'static/style.css'), 'utf8');

function rule(selector: string): string {
  const i = CSS.indexOf(selector);
  expect(i, `missing rule ${selector}`).toBeGreaterThan(-1);
  return CSS.slice(i, CSS.indexOf('}', i));
}

describe('the release card play button', () => {
  const base = rule('.artist-detail-page .release-card-play-btn {');

  it('is anchored to the bottom-left, not the centre of the artwork', () => {
    expect(base).toContain('left: 14px');
    expect(base).toContain('bottom: 74px');
    // the centring pair is what put it under the cursor's natural target
    expect(base).not.toContain('left: 50%');
    expect(base).not.toContain('top: 50%');
  });

  it('clears the title strip rather than sitting on the album name', () => {
    // measured: strip is 65px tall, its top edge 66px above the card bottom
    const bottom = Number(/bottom:\s*(\d+)px/.exec(base)?.[1]);
    expect(bottom).toBeGreaterThan(66);
  });

  it('never uses a centring transform, in any state', () => {
    // A leftover translate(-50%, -50%) would drag the button back over the
    // middle even with the anchor moved - and the touch-device block is the
    // easy one to miss, because it is the state that is always visible.
    const hover = rule('.artist-detail-page .release-card.album-card:hover .release-card-play-btn,');
    // Find the touch block by scanning every '@media (hover: none)' for the one
    // whose body actually mentions this button. Reaching for indexOf/lastIndexOf
    // lands on unrelated blocks - an earlier version of this test did exactly
    // that and asserted against CSS for a completely different component, so it
    // passed while the button was centred.
    const blocks: string[] = [];
    for (let i = CSS.indexOf('@media (hover: none)'); i !== -1;
         i = CSS.indexOf('@media (hover: none)', i + 1)) {
      blocks.push(CSS.slice(i, CSS.indexOf('\n}', i)));
    }
    const touch = blocks.filter((b) => b.includes('.release-card-play-btn'));
    expect(touch, 'no touch-device block covers the play button').toHaveLength(1);

    for (const chunk of [base, hover, ...touch]) {
      expect(chunk).not.toMatch(/translate\(\s*-50%/);
    }
  });
});
