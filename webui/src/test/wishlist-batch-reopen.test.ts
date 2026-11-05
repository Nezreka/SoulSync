import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { extractFunction } from './vanilla-extract';

/**
 * #1982 (cremonies): opening a running wishlist batch showed "0 tracks" and
 * an empty table while 32 downloaded. The modal re-fetched the wishlist by
 * the batch's cycle; a playlist run's cycle is "playlist", which no wishlist
 * track is. It builds the rows from the batch's own tasks now, each at its
 * track_index, where the status poll looks for it.
 */
const SRC = readFileSync(resolve(process.cwd(), 'static/downloads.js'), 'utf8');

function load(): (batchId: string) => Promise<{ name: string; artists: string[] }[] | null> {
  // eslint-disable-next-line @typescript-eslint/no-implied-eval
  return new Function(
    `${extractFunction('_wishlistBatchTracks', SRC)}; return _wishlistBatchTracks;`,
  )();
}

afterEach(() => vi.unstubAllGlobals());

describe('reopening a running wishlist batch', () => {
  it('builds the rows from the batch tasks, in track_index order', async () => {
    const fetchMock = vi.fn(
      async () =>
        new Response(
          JSON.stringify({
            tasks: [
              { track_index: 2, track_info: { name: 'Run Away', artist: 'Ritual Mental' } },
              {
                track_index: 0,
                track_info: { track_name: 'Where Do You Go', artists: ['No Mercy'] },
              },
            ],
          }),
        ),
    );
    vi.stubGlobal('fetch', fetchMock);
    const tracks = await load()('batch-7');
    expect(fetchMock).toHaveBeenCalledWith('/api/playlists/batch-7/download_status');
    expect(tracks?.map((t) => [t.name, t.artists])).toEqual([
      ['Where Do You Go', ['No Mercy']],
      ['…', []],
      ['Run Away', ['Ritual Mental']],
    ]);
  });

  it('gives null while the batch has no tasks, so the wishlist is read instead', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => new Response(JSON.stringify({ tasks: [] }))),
    );
    expect(await load()('batch-7')).toBeNull();
  });

  it('never filters the wishlist by a cycle that is not a category', () => {
    expect(SRC).toMatch(/if \(category !== 'albums' && category !== 'singles'\) category = null;/);
    const spotify = readFileSync(resolve(process.cwd(), 'static/sync-spotify.js'), 'utf8');
    expect(spotify).toContain('openDownloadMissingWishlistModal(current_cycle, null, batch_id)');
  });
});
