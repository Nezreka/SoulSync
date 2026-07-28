import { afterEach, describe, expect, it, vi } from 'vitest';

import {
  EMPTY_TOP_TRACKS,
  formatPlaycount,
  loadTopTracks,
  playTrackByMetadata,
  topTracksBulkContext,
  topTracksTitle,
  trackArtistLabel,
  wishlistTrackBody,
} from './-artist-detail.top-tracks';

function json(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });
}

/** Route by URL so a test can fail one pass and answer the next. */
function stubRoutes(routes: Record<string, () => Response | Promise<Response>>) {
  const calls: string[] = [];
  vi.stubGlobal(
    'fetch',
    vi.fn(async (input: RequestInfo | URL) => {
      const url = input instanceof Request ? input.url : String(input);
      calls.push(url);
      for (const [fragment, handler] of Object.entries(routes)) {
        if (url.includes(fragment)) return handler();
      }
      throw new Error(`unstubbed ${url}`);
    }),
  );
  return calls;
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('topTracksTitle', () => {
  it('labels the known sources', () => {
    expect(topTracksTitle('spotify')).toBe('Top Tracks (Spotify)');
    expect(topTracksTitle('deezer')).toBe('Top Tracks (Deezer)');
    expect(topTracksTitle('lastfm')).toBe('Popular on Last.fm');
  });

  it('falls back for an unknown or missing source', () => {
    expect(topTracksTitle('tidal')).toBe('Top Tracks');
    expect(topTracksTitle(undefined)).toBe('Top Tracks');
  });
});

describe('loadTopTracks', () => {
  it('prefers the metadata source and marks it downloadable', async () => {
    const calls = stubRoutes({
      '/top-tracks': () => json({ success: true, source: 'spotify', tracks: [{ name: 'Xtal' }] }),
    });
    const state = await loadTopTracks(42, 'Aphex Twin');

    expect(state).toEqual({
      source: 'spotify',
      title: 'Top Tracks (Spotify)',
      tracks: [{ name: 'Xtal' }],
      downloadable: true,
    });
    // Last.fm is never consulted when the source answered.
    expect(calls.some((u) => u.includes('lastfm'))).toBe(false);
  });

  it('falls back to Last.fm when the source cannot rank by popularity', async () => {
    // iTunes / Discogs / MusicBrainz answer success=false rather than 404,
    // which is the only reason the second pass is reachable.
    stubRoutes({
      '/top-tracks': () => json({ success: false }),
      'lastfm-top-tracks': () => json({ success: true, tracks: [{ name: 'Ageispolis' }] }),
    });
    const state = await loadTopTracks(42, 'Aphex Twin');

    expect(state.source).toBe('lastfm');
    expect(state.title).toBe('Popular on Last.fm');
    // Display-only: Last.fm rows carry no metadata to download with.
    expect(state.downloadable).toBe(false);
  });

  it('falls back when the source returns an EMPTY track list', async () => {
    stubRoutes({
      '/top-tracks': () => json({ success: true, source: 'deezer', tracks: [] }),
      'lastfm-top-tracks': () => json({ success: true, tracks: [{ name: 'A' }] }),
    });
    expect((await loadTopTracks(42, 'X')).source).toBe('lastfm');
  });

  it('falls back on a non-ok source response', async () => {
    stubRoutes({
      '/top-tracks': () => json({ success: true, tracks: [{ name: 'ignored' }] }, 500),
      'lastfm-top-tracks': () => json({ success: true, tracks: [{ name: 'A' }] }),
    });
    expect((await loadTopTracks(42, 'X')).source).toBe('lastfm');
  });

  it('skips the source pass entirely without an artist id', async () => {
    const calls = stubRoutes({
      'lastfm-top-tracks': () => json({ success: true, tracks: [{ name: 'A' }] }),
    });
    await loadTopTracks(null, 'X');
    expect(calls.every((u) => !u.includes('/top-tracks?'))).toBe(true);
  });

  it('stays empty — so the sidebar stays hidden — when neither pass has rows', async () => {
    stubRoutes({
      '/top-tracks': () => json({ success: false }),
      'lastfm-top-tracks': () => json({ success: true, tracks: [] }),
    });
    expect(await loadTopTracks(42, 'X')).toEqual(EMPTY_TOP_TRACKS);
  });

  it('swallows a thrown Last.fm request rather than failing the hero', async () => {
    stubRoutes({
      '/top-tracks': () => json({ success: false }),
      'lastfm-top-tracks': () => {
        throw new Error('offline');
      },
    });
    expect(await loadTopTracks(42, 'X')).toEqual(EMPTY_TOP_TRACKS);
  });
});

describe('trackArtistLabel', () => {
  it('joins the track artists', () => {
    expect(trackArtistLabel({ artists: [{ name: 'A' }, { name: 'B' }] }, 'Page')).toBe('A, B');
  });

  it('drops nameless entries instead of leaving empty separators', () => {
    expect(trackArtistLabel({ artists: [{ name: 'A' }, {}] }, 'Page')).toBe('A');
  });

  it('falls back to the page artist when the track lists none', () => {
    expect(trackArtistLabel({ artists: [] }, 'Page')).toBe('Page');
    expect(trackArtistLabel({}, 'Page')).toBe('Page');
  });
});

describe('formatPlaycount', () => {
  it('abbreviates millions and thousands, trimming a trailing .0', () => {
    expect(formatPlaycount(2_400_000)).toBe('2.4M');
    expect(formatPlaycount(2_000_000)).toBe('2M');
    expect(formatPlaycount(3400)).toBe('3.4K');
    expect(formatPlaycount(1000)).toBe('1K');
  });

  it('leaves small counts alone and floors junk at 0', () => {
    expect(formatPlaycount(999)).toBe('999');
    expect(formatPlaycount(0)).toBe('0');
    expect(formatPlaycount(undefined)).toBe('0');
    expect(formatPlaycount(-5)).toBe('0');
  });
});

describe('wishlistTrackBody', () => {
  it('keeps the track artists when it has them', () => {
    const body = wishlistTrackBody(
      { name: 'Xtal', artists: [{ name: 'AFX' }], album: { name: 'SAW', album_type: 'album' } },
      'Aphex Twin',
      42,
    );
    expect(body.track.artists).toEqual([{ name: 'AFX' }]);
    expect(body.source_context).toEqual({
      artist_name: 'Aphex Twin',
      album_name: 'SAW',
      album_type: 'album',
    });
  });

  it('substitutes the page artist when the track has none', () => {
    const body = wishlistTrackBody({ name: 'X' }, 'Aphex Twin', 42);
    expect(body.track.artists).toEqual([{ name: 'Aphex Twin' }]);
  });

  it('defaults the album to an object and the type to album', () => {
    // The backend indexes into album.*; a null album would throw there.
    const body = wishlistTrackBody({ name: 'X', album: null as never }, 'A', 42);
    expect(body.album).toEqual({});
    expect(body.source_context.album_type).toBe('album');
  });

  it('sends an empty id rather than the string "null"', () => {
    expect(wishlistTrackBody({ name: 'X' }, 'A', null).artist.id).toBe('');
  });
});

describe('topTracksBulkContext', () => {
  it('builds a playlist id that does NOT trigger album-download mode', () => {
    // downloads.js keys is_album_download off an `artist_album_` /
    // `enhanced_search_album_` prefix; a top-tracks bundle must avoid it so
    // each track lands in its own real album folder.
    const context = topTracksBulkContext(
      { source: 'spotify', title: '', tracks: [{ name: 'a' }, { name: 'b' }], downloadable: true },
      'Aphex Twin',
      42,
    );
    expect(context.virtualPlaylistId).toBe('top_tracks_spotify_42');
    expect(context.virtualPlaylistId.startsWith('artist_album_')).toBe(false);
    expect(context.virtualPlaylistId.startsWith('enhanced_search_album_')).toBe(false);
    expect(context.playlistName).toBe('Aphex Twin — Top Tracks');
    expect(context.wrapperAlbum.album_type).toBe('compilation');
    expect(context.wrapperAlbum.total_tracks).toBe(2);
  });

  it('names the id "unknown" rather than "null" without an artist id', () => {
    const context = topTracksBulkContext(EMPTY_TOP_TRACKS, 'A', null);
    expect(context.virtualPlaylistId).toBe('top_tracks__unknown');
  });
});

describe('playTrackByMetadata', () => {
  const bridge = () =>
    ({
      playLibraryTrack: vi.fn(),
      startStream: vi.fn(),
      showLoadingOverlay: vi.fn(),
      hideLoadingOverlay: vi.fn(),
    }) as never;

  it('plays the owned copy and never reaches the streamer', async () => {
    const calls = stubRoutes({
      'resolve-track': () =>
        json({
          success: true,
          track: { id: 7, title: 'Xtal', file_path: '/x.flac', album_title: 'SAW' },
        }),
    });
    const b = bridge();
    await playTrackByMetadata(b, 'Xtal', 'Aphex Twin');

    expect(
      (b as never as { playLibraryTrack: ReturnType<typeof vi.fn> }).playLibraryTrack,
    ).toHaveBeenCalledWith(
      expect.objectContaining({ id: 7, file_path: '/x.flac' }),
      'SAW',
      'Aphex Twin',
    );
    expect(calls.some((u) => u.includes('stream-track'))).toBe(false);
  });

  it('streams on a library MISS', async () => {
    stubRoutes({
      'resolve-track': () => json({ success: false }),
      'stream-track': () => json({ success: true, result: { username: 'peer' } }),
    });
    const b = bridge() as never as { startStream: ReturnType<typeof vi.fn> };
    await playTrackByMetadata(b as never, 'Xtal', 'Aphex Twin');
    expect(b.startStream).toHaveBeenCalledWith({ username: 'peer' });
  });

  it('streams when the library resolve THROWS instead of surfacing an error', async () => {
    stubRoutes({
      'resolve-track': () => {
        throw new Error('offline');
      },
      'stream-track': () => json({ success: true, result: { username: 'peer' } }),
    });
    const b = bridge() as never as { startStream: ReturnType<typeof vi.fn> };
    await playTrackByMetadata(b as never, 'Xtal', 'A');
    expect(b.startStream).toHaveBeenCalled();
  });

  it('reports the streamer error and clears the overlay', async () => {
    window.showToast = vi.fn();
    stubRoutes({
      'resolve-track': () => json({ success: false }),
      'stream-track': () => json({ success: false, error: 'nothing found' }),
    });
    const b = bridge() as never as {
      hideLoadingOverlay: ReturnType<typeof vi.fn>;
      startStream: ReturnType<typeof vi.fn>;
    };
    await playTrackByMetadata(b as never, 'Xtal', 'A');

    expect(b.hideLoadingOverlay).toHaveBeenCalled();
    expect(b.startStream).not.toHaveBeenCalled();
    expect(window.showToast).toHaveBeenCalledWith('nothing found', 'error');
  });
});
