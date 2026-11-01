import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { describe, expect, it } from 'vitest';

/**
 * Episode, season and series searches sent only title + numbers, so the
 * server judged every result by the Default profile (a 3 GB cap showed 4 GB
 * releases as meeting the show's profile) and searched the indexers by text
 * alone. Every search now names its title the way the grab already did.
 */

const JS = readFileSync(resolve(process.cwd(), 'static/video/video-download-view.js'), 'utf8');
const SRC = JS.slice(JS.indexOf('function withIdentity'), JS.indexOf('function searchInto'));
type Params = Record<string, unknown>;
// the vanilla file's function on its own, as the sync tests load theirs
// eslint-disable-next-line @typescript-eslint/no-implied-eval
const withIdentity = new Function(`${SRC}; return withIdentity;`)() as (
  container: unknown,
  resultsEl: { closest: () => unknown },
  params: Params,
) => Params;

const panel = (host: unknown) => ({ closest: () => host });

describe('a video search names its title', () => {
  it('takes the show page state', () => {
    const container = { _dl: { mediaId: 2021, mediaSource: 'library', tvId: 247718 } };
    const out = withIdentity(container, panel(null), { scope: 'episode', season: 1, episode: 1 });
    expect(out).toMatchObject({
      media_id: 2021,
      media_source: 'library',
      tmdb_id: 247718,
      season: 1,
    });
  });

  it('takes the manual search modal and get-modal opts', () => {
    const host = { _opts: { id: 7, source: 'tmdb' } };
    expect(withIdentity({}, panel(host), { scope: 'movie' })).toMatchObject({
      media_id: 7,
      media_source: 'tmdb',
    });
  });

  it('leaves a search that already names its title alone', () => {
    const params = { scope: 'movie', media_id: 1, media_source: 'library' };
    expect(withIdentity({ _dl: { mediaId: 9 } }, panel(null), params)).toBe(params);
  });

  it('a show render drops an earlier movie the reused modal still held', () => {
    expect(JS).toContain(
      "if (opts.kind === 'show') { container._opts = null; renderShow(container, opts); return; }",
    );
  });

  it('the soulseek poll carries the title too', () => {
    const poll = JS.slice(
      JS.indexOf('function _pollSearch'),
      JS.indexOf("getJSON('/api/video/downloads/search/poll'"),
    );
    expect(poll).toContain("'&media_id=' + encodeURIComponent(params.media_id)");
  });
});
