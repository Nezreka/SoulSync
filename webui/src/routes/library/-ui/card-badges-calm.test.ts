/**
 * #1909: the library grid led with six service logos on every card. On a
 * pointer they now wait for hover or focus; phones already drop them. jsdom
 * can't hover, so this pins the rule itself.
 */

import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { describe, expect, it } from 'vitest';

const css = readFileSync(resolve(__dirname, 'library-v2-page.module.css'), 'utf8');

describe('calm library cards', () => {
  const at = css.indexOf('the service badges are for looking an artist up');
  const block = css.slice(at, css.indexOf('\n}\n', at) + 3);

  it('hides the badge column until the card is hovered or focused', () => {
    expect(at).toBeGreaterThan(0);
    expect(block).toContain('@media (hover: hover)');
    expect(block).toMatch(/\.library-artist-card > \.card-badge-container\) \{\s*opacity: 0;/);
    expect(block).toContain(':global(.library-artist-card:hover > .card-badge-container)');
    // keyboard users tab onto the links, so focus has to show them too
    expect(block).toContain(':global(.library-artist-card:focus-within > .card-badge-container)');
  });
});
