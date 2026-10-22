import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { describe, expect, it } from 'vitest';

/**
 * The stuck-row diagnostics drawer.
 *
 * The row's tooltip said how often it had been searched and gave a headline
 * refusal. It could not say WHICH releases came back, why each lost, where the
 * file would land, which external ids the search was keyed on, or whether
 * something was already downloading - so "why is this stuck" meant reading
 * three screens and guessing. Twice in one day that guess was wrong.
 *
 * The wishlist is React now. These run the real drawer component against the
 * same payloads the vanilla drawer was pinned with; the frozen vanilla
 * diagRowsHTML is compared line for line in -video.helpers.differential.test.ts.
 * The cases here live in -ui/video-diag-drawer.test.tsx; this file keeps the
 * css and wiring pins that were here.
 */

const CSS = readFileSync(resolve(process.cwd(), 'static/wishlist-premier.css'), 'utf8');
const SHEETS = readFileSync(
  resolve(process.cwd(), 'src/routes/wishlist/-ui/video-sheets.tsx'),
  'utf8',
);

describe('the drawer is reachable and self-contained', () => {
  it('opens from a stuck episode and from a movie that has searched', () => {
    // the episode row's detail mounts it for a stuck row; the movie panel has
    // a "What the last search found" toggle.
    expect(SHEETS).toContain('<VideoDiagDrawer');
    expect(SHEETS).toContain("kind: 'episode'");
    expect(SHEETS).toContain("diagKey={{ kind: 'movie', tmdb_id: movie.tmdb_id }}");
  });

  it('asks about the row it hangs off, by that row identity', () => {
    // re-deriving it from somewhere else is how a drawer ends up describing the
    // wrong episode. the key is built from the row's own season + episode.
    expect(SHEETS).toContain('season_number: season.season_number,');
    expect(SHEETS).toContain('episode_number: ep.episode_number,');
  });

  it('toggles closed on a second click', () => {
    expect(SHEETS).toContain('setShowDiag((v) => !v)');
    expect(SHEETS).toContain('setOpen((v) => !v)');
  });

  it('styles everything it emits', () => {
    for (const cls of [
      '.wlp-diag ',
      '.wlp-diag--note',
      '.wlp-diag-line.is-gap',
      '.wlp-diag-line.is-live',
      '.wlp-diag-rel.is-ok',
      '.wlp-diag-rel.is-none',
      '.wlp-diag-src',
    ]) {
      expect(CSS, `${cls} emitted but never styled`).toContain(cls);
    }
  });
});
