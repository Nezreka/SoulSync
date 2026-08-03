import { afterEach, describe, expect, it, vi } from 'vitest';

import type { ExplorerArtist, ExplorerMeta } from './-explorer.types';

import {
  buildExplorerWishlistPayload,
  explorerAlbumStatusText,
  fetchAlbumTracks,
  fetchMirroredPlaylists,
  streamBuildTree,
  submitExplorerWishlist,
} from './-explorer.api';

/**
 * The explorer's four endpoints. The interesting cases are the NDJSON reader's
 * chunk stitching and its refusal to let one malformed line abort a tree.
 */

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

/** A body that hands back exactly these chunks, byte boundaries and all. */
function streamOf(chunks: string[]): ReadableStream<Uint8Array> {
  const encoder = new TextEncoder();
  let i = 0;
  return new ReadableStream({
    pull(controller) {
      if (i < chunks.length) controller.enqueue(encoder.encode(chunks[i++]));
      else controller.close();
    },
  });
}

describe('fetchMirroredPlaylists', () => {
  it('accepts a bare array and a {playlists} envelope, and copes with neither', async () => {
    const bodies = [
      JSON.stringify([{ id: 1 }]),
      JSON.stringify({ playlists: [{ id: 2 }] }),
      JSON.stringify({ error: 'nope' }),
    ];
    let i = 0;
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => new Response(bodies[i++])),
    );
    expect(await fetchMirroredPlaylists()).toEqual([{ id: 1 }]);
    expect(await fetchMirroredPlaylists()).toEqual([{ id: 2 }]);
    expect(await fetchMirroredPlaylists()).toEqual([]);
  });
});

describe('streamBuildTree', () => {
  it('posts the playlist id and mode', async () => {
    const calls: RequestInit[] = [];
    vi.stubGlobal(
      'fetch',
      vi.fn(async (_url: RequestInfo | URL, init?: RequestInit) => {
        calls.push(init!);
        return new Response(streamOf([]));
      }),
    );
    await streamBuildTree(42, 'discographies', { onMeta: vi.fn(), onArtist: vi.fn() });
    expect(JSON.parse(String(calls[0]?.body))).toEqual({
      playlist_id: 42,
      mode: 'discographies',
    });
    expect(calls[0]?.method).toBe('POST');
  });

  it('routes meta then artists, numbering the artists from one', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(
        async () =>
          new Response(
            streamOf([
              '{"type":"meta","playlist_name":"Mix","total_artists":2}\n',
              '{"type":"artist","name":"A"}\n',
              '{"type":"artist","name":"B"}\n',
              '{"type":"complete"}\n',
            ]),
          ),
      ),
    );
    const meta: ExplorerMeta[] = [];
    const artists: [ExplorerArtist, number][] = [];
    await streamBuildTree(1, 'albums', {
      onMeta: (m) => meta.push(m),
      onArtist: (a, i) => artists.push([a, i]),
    });
    expect(meta).toEqual([{ type: 'meta', playlist_name: 'Mix', total_artists: 2 }]);
    expect(artists).toEqual([
      [{ type: 'artist', name: 'A' }, 1],
      [{ type: 'artist', name: 'B' }, 2],
    ]);
  });

  it('stitches a line split across chunk boundaries', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(
        async () => new Response(streamOf(['{"type":"art', 'ist","name":"Split ', 'Name"}\n'])),
      ),
    );
    const artists: ExplorerArtist[] = [];
    await streamBuildTree(1, 'albums', { onMeta: vi.fn(), onArtist: (a) => artists.push(a) });
    expect(artists).toEqual([{ type: 'artist', name: 'Split Name' }]);
  });

  it('skips a malformed line instead of abandoning the tree', async () => {
    const warn = vi.spyOn(console, 'warn').mockImplementation(() => {});
    vi.stubGlobal(
      'fetch',
      vi.fn(
        async () =>
          new Response(
            streamOf([
              '{"type":"artist","name":"A"}\n',
              'not json at all\n',
              '\n',
              '{"type":"artist","name":"C"}\n',
            ]),
          ),
      ),
    );
    const artists: ExplorerArtist[] = [];
    await streamBuildTree(1, 'albums', { onMeta: vi.fn(), onArtist: (a) => artists.push(a) });
    expect(artists.map((a) => a.name)).toEqual(['A', 'C']);
    expect(warn).toHaveBeenCalledTimes(1);
  });

  it('throws the server error, and a default when there is none', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(
        async () => new Response(JSON.stringify({ error: 'no such playlist' }), { status: 400 }),
      ),
    );
    await expect(
      streamBuildTree(1, 'albums', { onMeta: vi.fn(), onArtist: vi.fn() }),
    ).rejects.toThrow('no such playlist');

    vi.stubGlobal(
      'fetch',
      vi.fn(async () => new Response('<html>500</html>', { status: 500 })),
    );
    await expect(
      streamBuildTree(1, 'albums', { onMeta: vi.fn(), onArtist: vi.fn() }),
    ).rejects.toThrow('Failed to build tree');
  });
});

describe('fetchAlbumTracks', () => {
  it('returns the tracks on success', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(
        async () =>
          new Response(JSON.stringify({ success: true, tracks: [{ track_number: 1, name: 'X' }] })),
      ),
    );
    expect(await fetchAlbumTracks('al1')).toEqual([{ track_number: 1, name: 'X' }]);
  });

  it('returns null — not an empty list — when the payload is unsuccessful', async () => {
    // Null is what lets the next double-click retry; [] would read as
    // "expanded with no tracks" and the retry would be lost.
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => new Response(JSON.stringify({ success: false }))),
    );
    expect(await fetchAlbumTracks('al1')).toBeNull();
  });

  it('returns null and logs when the request throws', async () => {
    const error = vi.spyOn(console, 'error').mockImplementation(() => {});
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => {
        throw new Error('offline');
      }),
    );
    expect(await fetchAlbumTracks('al1')).toBeNull();
    expect(error).toHaveBeenCalled();
  });

  it('puts the album id in the path', async () => {
    const urls: string[] = [];
    vi.stubGlobal(
      'fetch',
      vi.fn(async (url: RequestInfo | URL) => {
        urls.push(String(url));
        return new Response(JSON.stringify({ success: true, tracks: [] }));
      }),
    );
    await fetchAlbumTracks('4aawyAB9');
    expect(urls).toEqual(['/api/playlist-explorer/album-tracks/4aawyAB9']);
  });
});

describe('buildExplorerWishlistPayload', () => {
  it('sorts deluxe editions first and sends a null source per album', () => {
    const payload = buildExplorerWishlistPayload(
      [
        { spotify_id: 'std', title: 'Album', track_count: 10 },
        { spotify_id: 'dlx', title: 'Album (Deluxe)', track_count: 18 },
        { spotify_id: 'unknown', title: null },
      ],
      'Boards of Canada',
    );
    expect(payload).toEqual({
      albums: [
        {
          id: 'dlx',
          name: 'Album (Deluxe)',
          artist_name: 'Boards of Canada',
          source: null,
        },
        { id: 'std', name: 'Album', artist_name: 'Boards of Canada', source: null },
        { id: 'unknown', name: '', artist_name: 'Boards of Canada', source: null },
      ],
      artist_name: 'Boards of Canada',
    });
  });

  it('leaves the caller array untouched', () => {
    const albums = [
      { spotify_id: 'a', track_count: 1 },
      { spotify_id: 'b', track_count: 9 },
    ];
    buildExplorerWishlistPayload(albums, 'A');
    expect(albums.map((a) => a.spotify_id)).toEqual(['a', 'b']);
  });
});

describe('explorerAlbumStatusText', () => {
  it('pluralises, and mentions skips only when there are some', () => {
    expect(explorerAlbumStatusText({ tracks_added: 1 })).toBe('Added 1 track');
    expect(explorerAlbumStatusText({ tracks_added: 4 })).toBe('Added 4 tracks');
    expect(explorerAlbumStatusText({ tracks_added: 4, tracks_skipped: 2 })).toBe(
      'Added 4 tracks, 2 skipped',
    );
    expect(explorerAlbumStatusText({})).toBe('Added 0 tracks');
  });
});

describe('submitExplorerWishlist', () => {
  it('streams one artist at a time and totals only the finished albums', async () => {
    const urls: string[] = [];
    const bodies: string[] = [
      '{"album_id":"a1","status":"done","tracks_added":3}\n{"status":"complete","total_added":3}\n',
      '{"album_id":"b1","status":"error","message":"nope"}\n' +
        '{"album_id":"b2","status":"processing","tracks_added":99}\n' +
        '{"album_id":"b2","status":"done","tracks_added":5}\n',
    ];
    let i = 0;
    vi.stubGlobal(
      'fetch',
      vi.fn(async (url: RequestInfo | URL) => {
        urls.push(String(url));
        return new Response(streamOf([bodies[i++]!]));
      }),
    );

    const seen: [unknown, string | undefined][] = [];
    const total = await submitExplorerWishlist(
      [
        { artistId: 'art1', name: 'One', albums: [{ spotify_id: 'a1', track_count: 1 }] },
        {
          artistId: 'art2',
          name: 'Two',
          albums: [
            { spotify_id: 'b1', track_count: 1 },
            { spotify_id: 'b2', track_count: 2 },
          ],
        },
      ],
      (albumId, update) => seen.push([albumId, update.status]),
    );

    expect(urls).toEqual([
      '/api/artist/art1/download-discography',
      '/api/artist/art2/download-discography',
    ]);
    expect(seen).toEqual([
      ['a1', 'done'],
      ['b1', 'error'],
      ['b2', 'processing'],
      ['b2', 'done'],
    ]);
    // 3 + 5. The `complete` summary is ignored, and so is the in-flight
    // line's running count — only a finished album adds to the total.
    expect(total).toBe(8);
  });

  it('logs a failed artist and carries on with the next', async () => {
    const error = vi.spyOn(console, 'error').mockImplementation(() => {});
    let call = 0;
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => {
        call += 1;
        if (call === 1) throw new Error('offline');
        return new Response(streamOf(['{"album_id":"b1","status":"done","tracks_added":2}\n']));
      }),
    );
    const total = await submitExplorerWishlist(
      [
        { artistId: 'art1', name: 'One', albums: [] },
        { artistId: 'art2', name: 'Two', albums: [] },
      ],
      vi.fn(),
    );
    expect(total).toBe(2);
    expect(error).toHaveBeenCalled();
  });
});
