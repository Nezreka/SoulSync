import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';

import { describe, expect, it } from 'vitest';

/**
 * The Tools job-settings panel must fit inside its tile.
 *
 * Reported by TheHomeGuy: the settings fields in Tools were cut off and could
 * not be filled in. It was not a popover mispositioned off-screen — the panel
 * is in-flow inside `.repair-tile`, which is `overflow: hidden`, so a row
 * wider than the tile is silently CLIPPED rather than scrolled.
 *
 * Two defaults made the row un-shrinkable:
 *   - a form control's `min-width` is `auto`, which resolves to its INTRINSIC
 *     width (~170px for a number input) and overrides any `width` we set;
 *   - a long label ("Fingerprint Threshold") will not break.
 * Together they put the row's minimum at about the tile's inner width, so it
 * clipped on some setups and not others — which is why it read as a
 * resolution-specific "scaling" bug.
 *
 * These rules are asserted rather than the layout because the stylesheet is
 * served raw (not bundled) and jsdom does no layout, so a rendering test
 * could not catch a regression here. Same approach as
 * `discover/-ui/artist-map-hub.test.tsx`, which also reads style.css.
 */

const CSS = readFileSync(resolve(process.cwd(), 'static/style.css'), 'utf8');

/**
 * The declaration block for a selector, with COMMENTS STRIPPED.
 *
 * Both halves matter. Scoping to the block stops an assertion matching a
 * coincidental occurrence elsewhere in a 65k-line stylesheet; stripping
 * comments stops it matching the prose ABOVE the declaration — the comments
 * here quote the property names they explain, so a test that skipped this
 * passed even with the real rules deleted. Found by mutating the fix.
 */
function block(selector: string): string {
  const at = CSS.indexOf(`${selector} {`);
  expect(at, `${selector} is missing from style.css`).toBeGreaterThan(-1);
  return CSS.slice(at, CSS.indexOf('}', at)).replace(/\/\*[\s\S]*?\*\//g, '');
}

describe('Tools job-settings panel fits its tile', () => {
  it('lets the input shrink below its intrinsic width', () => {
    // The single most important line: without it the control ignores its
    // declared width and pushes the row past the tile edge.
    expect(block('.repair-setting-input')).toMatch(/min-width:\s*0/);
  });

  it('never lets the input exceed the panel', () => {
    expect(block('.repair-setting-input')).toMatch(/max-width:\s*100%/);
  });

  it('counts padding inside the input width', () => {
    // width:100px plus 10px side padding would otherwise be a 120px box.
    expect(block('.repair-setting-input')).toMatch(/box-sizing:\s*border-box/);
  });

  it('wraps the row rather than clipping it as a last resort', () => {
    expect(block('.repair-setting-row')).toMatch(/flex-wrap:\s*wrap/);
  });

  it('lets the label give way before the input does', () => {
    const label = block('.repair-setting-row label');
    expect(label).toMatch(/min-width:\s*0/);
    expect(label).toMatch(/overflow-wrap:\s*anywhere/);
  });

  it('keeps the save button inside the panel', () => {
    expect(block('.repair-save-settings-btn')).toMatch(/max-width:\s*100%/);
  });

  it('documents that the tile still clips, which is why the above matters', () => {
    // If the tile ever stops hiding overflow, these rules become belt-and-
    // braces rather than load-bearing — worth knowing before deleting them.
    expect(block('.repair-tile')).toMatch(/overflow:\s*hidden/);
  });
});
