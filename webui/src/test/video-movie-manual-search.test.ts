import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { describe, expect, it, vi } from 'vitest';

import { extractFunction } from './vanilla-extract';

/**
 * #2122: a movie gets the same manual search an episode has.
 *
 * An episode row had "Manual search" in its menu; a movie only had "Search
 * releases" in its Files section, which exists only once the movie is in the
 * library. The movie page's menu now offers Manual search for any movie, a
 * library one or a TMDB preview, through the one release picker.
 */

const SRC = readFileSync(resolve(process.cwd(), 'static/video/video-detail.js'), 'utf8');

function run(data: Record<string, unknown>) {
  const manualSearch = vi.fn();
  // eslint-disable-next-line @typescript-eslint/no-implied-eval
  const fn = new Function(
    'window',
    'VideoDownload',
    // the page's source falls back to currentSource (#2176)
    `var data = ${JSON.stringify(data)};\nvar currentSource = data.source;\n${extractFunction('_dataSource', SRC)}\n${extractFunction('manualSearchMovie', SRC)}\nreturn manualSearchMovie;`,
  )({ VideoDownload: { manualSearch } }, { manualSearch }) as () => void;
  fn();
  return manualSearch;
}

describe('movie manual search (#2122)', () => {
  it('searches a library movie with its own id and tmdb id', () => {
    const ms = run({
      kind: 'movie',
      source: 'library',
      id: 7,
      tmdb_id: 438631,
      title: 'Dune',
      year: 2021,
      has_poster: true,
    });
    expect(ms).toHaveBeenCalledWith({
      title: 'Dune',
      scope: 'movie',
      year: 2021,
      mediaId: 7,
      mediaSource: 'library',
      tmdbId: 438631,
      poster: '/api/video/poster/movie/7',
    });
  });

  it('searches a movie that is not in the library yet by its tmdb id', () => {
    const ms = run({
      kind: 'movie',
      source: 'tmdb',
      id: 438631,
      title: 'Dune',
      year: 2021,
      poster_url: 'https://img/x.jpg',
    });
    expect(ms.mock.calls[0][0]).toMatchObject({
      scope: 'movie',
      mediaId: 438631,
      mediaSource: 'tmdb',
      tmdbId: 438631,
      poster: 'https://img/x.jpg',
    });
  });

  it('does nothing for a show', () => {
    expect(run({ kind: 'show', source: 'library', id: 1, title: 'Primal' })).not.toHaveBeenCalled();
  });

  it('is in the movie menu and wired to its action', () => {
    expect(SRC).toContain("getMore += moreItem('manualsearch', ");
    // a show's ⋯ menu has its own (#2233); a movie's still opens the movie's
    expect(SRC).toContain(
      "else if (which === 'manualsearch') { if (data && data.kind === 'show') manualSearchShow(); else manualSearchMovie(); }",
    );
    // the library tab's bridge goes through the same function
    expect(SRC).toContain("if (scope === 'movie') { manualSearchMovie(); return; }");
  });
});
