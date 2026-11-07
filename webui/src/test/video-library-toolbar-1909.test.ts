/**
 * #1909: the video library reused the music library css, which stacks
 * .library-controls as a column. there the search box's flex: 1 1 240px was
 * 240px of HEIGHT, a big empty block with the glass icon floating in it. jsdom
 * can't lay out, so this pins the rules that measured right in chromium.
 */

import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { describe, expect, it } from 'vitest';

const css = readFileSync(resolve(process.cwd(), 'static/video/video-side.css'), 'utf8');

describe('the video library toolbar', () => {
  it('is a wrapping row, so flex-basis is a width again', () => {
    expect(css).toMatch(
      /#video-library-page \.library-controls \{\s*flex-direction: row;\s*flex-wrap: wrap;/,
    );
  });

  it('puts the tabs on their own line above search and filters', () => {
    expect(css).toMatch(/#video-library-page \.video-library-tabs \{ order: -2; \}/);
    expect(css).toMatch(
      /#video-library-page \.library-controls::before \{[^}]*order: -1;[^}]*flex-basis: 100%;/,
    );
    expect(css).toMatch(
      /#video-library-page \.library-controls > \.alphabet-selector \{[^}]*order: 1;[^}]*flex-basis: 100%;/,
    );
  });
});
