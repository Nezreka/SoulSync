import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import { describe, expect, it } from 'vitest';

/**
 * on a phone the download modal squeezed eight table columns into 390px, so
 * title and artist were cut to "Finall..." and "Sarah H...", and the discover
 * mix modal pushed its actions and selection bar off the right edge. measured
 * in chromium at 360/390/768: no element past the viewport, the full title and
 * artist on their own lines, the "downloaded" stat label unclipped at 360.
 *
 * things that looked right and weren't: a bare 1fr track won't shrink below
 * the title's min-content (minmax(0, 1fr) does), and the play buttons kept
 * going oval until the global 38px button floor skipped them.
 */
const STATIC = join(__dirname, '..', '..', 'static');
const CSS = readFileSync(join(STATIC, 'style.css'), 'utf8');
const MOBILE = readFileSync(join(STATIC, 'mobile.css'), 'utf8');

function rules(css: string, selector: string): string {
  const re = new RegExp(
    '(^|[\\s},])' + selector.replace(/[.*+?^${}()|[\]\\]/g, '\\$&') + '\\s*\\{([^}]*)\\}',
    'gm',
  );
  return Array.from(css.matchAll(re), (m) => m[2]).join('\n');
}

const ROW = '.download-missing-modal-content .download-tracks-table tbody';

describe('download modal rows on a phone', () => {
  it('turns each row into a two-line grid: title over artist', () => {
    const row = rules(MOBILE, `${ROW} tr`);
    expect(row).toMatch(/display:\s*grid/);
    expect(row).toMatch(/grid-template-columns:\s*auto minmax\(0, 1fr\)/);
    expect(row).toMatch(/"sel name\s+match\s+act"/);
    expect(row).toMatch(/"sel artist download act"/);
    expect(rules(MOBILE, `${ROW} td.track-name`)).toMatch(/grid-area:\s*name/);
    expect(rules(MOBILE, `${ROW} td.track-artist`)).toMatch(/grid-area:\s*artist/);
  });

  it('no longer caps the title and artist at 100px', () => {
    expect(rules(MOBILE, `${ROW} td`)).toMatch(/max-width:\s*none/);
  });

  it('keeps the play buttons round', () => {
    const floor = MOBILE.match(/button(:not\([^)]*\))+\s*\{\s*min-height:\s*38px/);
    expect(floor?.[0]).toContain(':not(.modal-track-play-btn)');
  });

  it('stacks the progress cards instead of running them off the edge', () => {
    expect(rules(MOBILE, '.download-missing-modal-content .download-progress-section')).toMatch(
      /grid-template-columns:\s*minmax\(0, 1fr\);/,
    );
  });
});

describe('discover mix modal on a phone', () => {
  it('wraps the header actions and the selection bar', () => {
    expect(rules(MOBILE, '.mix-modal-actions')).toMatch(/flex-wrap:\s*wrap/);
    expect(rules(MOBILE, '.mix-modal-selbar')).toMatch(/flex-wrap:\s*wrap/);
  });

  it('gives the title and artist the width the number and album had', () => {
    expect(rules(MOBILE, '.discover-playlist-track-compact.has-select')).toMatch(
      /grid-template-columns:\s*22px 40px minmax\(0, 1fr\) auto 32px/,
    );
    expect(rules(MOBILE, '.discover-playlist-track-compact .track-compact-album')).toMatch(
      /display:\s*none/,
    );
  });
});

describe('floating chrome over these modals', () => {
  it('hides the hamburger while either modal is open', () => {
    expect(
      rules(CSS, 'body:has(.download-missing-modal[style*="display: flex"]) .hamburger-btn'),
    ).toMatch(/display:\s*none !important/);
    expect(rules(CSS, 'body:has(#mix-modal-overlay) .hamburger-btn')).toMatch(
      /display:\s*none !important/,
    );
  });
});
