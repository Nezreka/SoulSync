import { afterEach, describe, expect, it, vi } from 'vitest';

import { resolveLibraryTrack } from './play-track';

/**
 * resolve-track answers with library v2 rows and names their ids. the payload
 * built here dropped lib2_track_id / lib2_artist_id, so a song played from the
 * dashboard or an artist page by title + artist reached Now Playing with no
 * library row: Report issue was hidden and Go to artist took the long way.
 */

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('resolveLibraryTrack', () => {
  it('carries the v2 ids the server names', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(
        async () =>
          new Response(
            JSON.stringify({
              success: true,
              track: {
                id: 41,
                title: 'Odo',
                file_path: '/m/Ado/Kyougen/01.flac',
                artist_id: 7,
                album_id: 9,
                lib2_track_id: 41,
                lib2_artist_id: 7,
                album_title: 'Kyougen',
                artist_name: 'Ado',
              },
            }),
          ),
      ),
    );
    const track = await resolveLibraryTrack('Odo', 'Ado');
    expect(track).toMatchObject({ lib2_track_id: 41, lib2_artist_id: 7, album: 'Kyougen' });
  });

  it('is null on a miss', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => new Response(JSON.stringify({ success: false }))),
    );
    expect(await resolveLibraryTrack('Nope', 'Nobody')).toBeNull();
  });
});
