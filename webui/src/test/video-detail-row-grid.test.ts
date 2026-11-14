import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { describe, expect, it } from 'vitest';

/**
 * The episode row grid has to have a column for every child it can produce.
 *
 * An episode emits six: index, thumbnail, info, the badge cell, the action
 * group, and the chevron. The grid once declared five, so the chevron fell onto
 * a second implicit row at the far left of the card.
 *
 * Oct 2026: owned rows stopped wearing an "Owned" pill (only a gap gets one),
 * but they still emit the badge CELL, empty. Without it an owned row's actions
 * sit one column left of a missing row's and the action column jumps.
 */

const CSS = readFileSync(resolve(process.cwd(), 'static/video/video-side.css'), 'utf8');

function columns(selector: string): string[] {
  const at = CSS.indexOf(`\n${selector} {`);
  expect(at, `${selector} has no own rule`).toBeGreaterThan(-1);
  const rule = CSS.slice(at, CSS.indexOf('}', at) + 1);
  const decl = /grid-template-columns:\s*([^;]+);/.exec(rule);
  expect(decl, `${selector} declares no columns`).not.toBeNull();
  // minmax(a, b) holds a comma; collapse it so the split counts tracks.
  return decl![1]
    .replace(/minmax\([^)]*\)/g, 'minmax')
    .trim()
    .split(/\s+/);
}

describe('the episode row grid fits every child it emits', () => {
  it('has a column for each of an episode’s six children', () => {
    // the row itself is React now (features/video-library/episode-list.tsx),
    // and its test counts the six children an owned and a missing row emit
    expect(columns('.vd-ep').length).toBeGreaterThanOrEqual(6);
  });

  it('keeps the youtube row in step with its own child count', () => {
    // No index column there, and its badge lives inside the action group.
    expect(columns('.vd-ep--yt').length).toBe(4);
  });

  it('stops the badge and buttons stretching to fill their column', () => {
    // A grid item fills its track unless told otherwise, which turned the Owned
    // badge into a 300px pill with its text stranded at the left edge.
    // justify-content places the TRACKS; only justify-self stops an ITEM growing.
    const rule = CSS.slice(CSS.indexOf('.vd-ep-badge, .vd-ep-get, .vd-ep-chev'));
    expect(rule.slice(0, 90)).toContain('justify-self: start');
    // ...but the info column is meant to fill its track, so it must be excluded.
    expect(rule.slice(0, 90)).not.toContain('.vd-ep-info');
  });

  it('caps the text column so the actions stay beside the episode', () => {
    expect(columns('.vd-ep')).toContain('minmax');
    expect(CSS.slice(CSS.indexOf('\n.vd-ep {'), CSS.indexOf('\n.vd-ep {') + 300)).toContain(
      'justify-content: start',
    );
  });
});

describe('an episode row with its menu open', () => {
  // Measured in chromium (Oct 2026): every row is its own layer, so a menu
  // opened in row 3 was painted under row 4's buttons. Raising the open row
  // is what fixed it; a z-index on the menu alone did nothing.
  it('sits above the rows after it', () => {
    const at = CSS.indexOf('\n.vd-ep.vd-ep--menu {');
    expect(at, 'the open-row rule is gone').toBeGreaterThan(-1);
    const rule = CSS.slice(at, CSS.indexOf('}', at));
    expect(rule).toContain('position: relative');
    expect(rule).toMatch(/z-index:\s*\d+/);
  });

  it('keeps its actions on screen while the pointer is in the menu', () => {
    expect(CSS).toContain(':not(.vd-ep--open):not(.vd-ep--menu) .vd-ep-get');
  });
});
