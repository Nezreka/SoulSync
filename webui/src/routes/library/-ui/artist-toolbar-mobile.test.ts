/**
 * The artist page jumped up and down on phones while scrolling past the hero
 * (Boulder, Nov 4 2026). The toolbar's name tag wrapped it from 48px to 86px,
 * which moved the hero's edge back on screen, which took the tag away again:
 * measured in chromium at iPhone size, 9 flips across one slow pass, and 11 at
 * every desktop width too. jsdom has no layout, so these pin the two parts of
 * the fix as text; the measurement lives in the commit.
 */

import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { describe, expect, it } from 'vitest';

const dir = resolve(process.cwd(), 'src/routes/library/-ui');
const css = readFileSync(resolve(dir, 'library-v2-page.module.css'), 'utf8');
const page = readFileSync(resolve(dir, 'library-v2-page.tsx'), 'utf8');

describe('artist toolbar name tag', () => {
  it('is hidden on phones, where the toolbar does not stick', () => {
    const phone = css.slice(
      css.indexOf('@media (max-width: 768px) {\n  .detailView .pageToolbar {'),
    );
    const block = phone.slice(0, phone.indexOf('\n}\n'));
    expect(block).toContain('position: static');
    expect(block).toMatch(/\.detailView \.toolbarIdentity \{\s*display: none;/);
  });

  it('decides from the scroll position, not from whether the hero edge is on screen', () => {
    const hook = page.slice(page.indexOf('function useScrolledPast'));
    const body = hook.slice(0, hook.indexOf('\n}\n'));
    expect(body).not.toContain('IntersectionObserver');
    // the edge is only measured while the tag is not shown, so the tag can't move it
    expect(body).toMatch(/if \(!pastRef\.current\) edge = topOf\(\);/);
  });
});

describe('a folded biography on a phone', () => {
  it('fades with a mask but keeps the four-line clamp', () => {
    // display: block switched the clamp off, so with "Read more" showing the
    // bio was its full height under the fade (3,800px for Taylor Swift), the
    // size watcher measured it as fitting, the toggle went, the clamp came
    // back: the hero swung 927 <-> 4,765px and the page with it (Nov 4 2026)
    const start = css.indexOf('{', css.indexOf('/* a folded biography: four lines') + 1);
    const rule = css.slice(start, css.indexOf('}', start)); // the body, not its comment
    expect(rule).toContain('mask-image');
    expect(rule).not.toMatch(/\bdisplay:/);
    expect(css).toMatch(
      /\.premierHero :global\(\.artist-hero-bio:not\(\.expanded\)\) :global\(\.bio-text\) \{[^}]*-webkit-line-clamp: 4/,
    );
  });
});
