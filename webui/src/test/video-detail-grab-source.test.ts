import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { describe, expect, it } from 'vitest';

/**
 * #2176: a show page grab sent the library show id as media_id with no
 * media_source (a library payload carries no `source`), and the downloads
 * card read it as a TMDB id: library show 37 showed TMDB's Seven of One.
 * The show page now names where its id comes from.
 */

const JS = readFileSync(resolve(process.cwd(), 'static/video/video-detail.js'), 'utf8');
const SRC = JS.slice(
  JS.indexOf('function _dataSource'),
  JS.indexOf('// Optimistic per-episode state'),
);

type Params = Record<string, unknown>;
function grabParams(data: Record<string, unknown>, currentSource: string): Params {
  // the vanilla file's functions on their own, as the other vanilla tests load theirs
  // eslint-disable-next-line @typescript-eslint/no-implied-eval
  const fn = new Function(
    'data',
    'currentSource',
    'selectedSeason',
    '_showPoster',
    `${SRC}; return _grabParams;`,
  )(data, currentSource, 13, () => null) as (en: number, src: string) => Params;
  return fn(30, 'torrent');
}

describe('show page grabs name their id', () => {
  it('a library show (no source on the payload) says library', () => {
    const p = grabParams({ id: 37, title: 'Last Week Tonight', tmdb_id: 60694 }, 'library');
    expect(p).toMatchObject({ mediaId: 37, mediaSource: 'library', tmdbId: 60694 });
  });

  it('a tmdb preview keeps sending its tmdb id, not a library id', () => {
    const p = grabParams({ id: 60694, source: 'tmdb', tmdb_id: 60694 }, 'tmdb');
    expect(p).toMatchObject({ mediaId: null, mediaSource: 'tmdb', tmdbId: 60694 });
  });

  it('the season grab and the manual search use the same source', () => {
    expect(JS).not.toContain('mediaSource: data.source');
  });
});
