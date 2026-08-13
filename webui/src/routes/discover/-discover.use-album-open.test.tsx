import { act, renderHook } from '@testing-library/react';
import { HttpResponse, http } from 'msw';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';

import { server } from '@/test/msw';

import type { AlbumOpenToast } from './-discover.use-album-open';

import { useAlbumOpen } from './-discover.use-album-open';

/**
 * The four album-card → download-modal flows, tested at their decision seams:
 * which source is asked, which fallback fires, which virtual id keys the
 * modal, and which of the vanilla's error strings each dead end produces.
 */

let toasts: AlbumOpenToast[] = [];
let artistAlbumCalls: unknown[][] = [];
let youtubeCalls: unknown[][] = [];
let overlays: string[] = [];
let overlayHides = 0;
let albumHits: { source: string; id: string; search: string }[] = [];
let resolveHits = 0;

/** A fresh album-detail payload, distinct from every card row on purpose. */
const DETAIL = {
  id: 'AD1',
  name: 'Fresh Name',
  album_type: 'single',
  total_tracks: 2,
  release_date: '2020-01-01',
  images: [{ url: 'img' }],
  artists: [{ name: 'AlbumArtist' }],
  tracks: [
    { id: 't1', name: 'T1', artists: [{ name: 'TrackArtist' }], duration_ms: 100, track_number: 1 },
    { id: 't2', name: 'T2' },
  ],
};

function stubAlbum(
  respond: (source: string, id: string) => ReturnType<typeof HttpResponse.json> = () =>
    HttpResponse.json(DETAIL),
) {
  server.use(
    http.get('/api/discover/album/:source/:id', ({ params, request }) => {
      const source = String(params.source);
      const id = String(params.id);
      albumHits.push({ source, id, search: new URL(request.url).search });
      return respond(source, id);
    }),
  );
}

function stubResolve(payload: Record<string, unknown> | null) {
  server.use(
    http.get('/api/discover/resolve-cache-album', () => {
      resolveHits += 1;
      return payload ? HttpResponse.json(payload) : HttpResponse.json({}, { status: 400 });
    }),
  );
}

function mount() {
  return renderHook(() => useAlbumOpen((t) => toasts.push(t)));
}

beforeEach(() => {
  toasts = [];
  artistAlbumCalls = [];
  youtubeCalls = [];
  overlays = [];
  overlayHides = 0;
  albumHits = [];
  resolveHits = 0;
  window.openDownloadMissingModalForArtistAlbum = (...args: unknown[]) => {
    artistAlbumCalls.push(args);
  };
  window.openDownloadMissingModalForYouTube = (...args: unknown[]) => {
    youtubeCalls.push(args);
  };
  window.showLoadingOverlay = (message?: string) => {
    overlays.push(message ?? '');
  };
  window.hideLoadingOverlay = () => {
    overlayHides += 1;
  };
});

afterEach(() => {
  delete window.openDownloadMissingModalForArtistAlbum;
  delete window.openDownloadMissingModalForYouTube;
  delete window.showLoadingOverlay;
  delete window.hideLoadingOverlay;
  server.resetHandlers();
});

describe('openYourAlbum — per-source dispatch (1521-1543)', () => {
  it('skips an empty first source, opens from the second, maps tracks + context', async () => {
    stubAlbum((source) =>
      source === 'spotify'
        ? HttpResponse.json({ ...DETAIL, tracks: [] })
        : HttpResponse.json(DETAIL),
    );
    const { result } = mount();
    await act(async () => {
      await result.current.openYourAlbum(
        {
          album_name: 'Row Album',
          artist_name: 'Row Artist',
          spotify_album_id: 'SP1',
          deezer_album_id: 'DZ1',
        },
        3,
      );
    });
    // Spotify answered with no tracks, so Deezer was asked next (1535).
    expect(albumHits.map((h) => h.source)).toEqual(['spotify', 'deezer']);
    expect(artistAlbumCalls).toHaveLength(1);
    const [virtualId, name, tracks, albumObj, artistObj, overlayFlag] = artistAlbumCalls[0] as [
      string,
      string,
      { artists: unknown; album: { album_type: string } }[],
      { artists: { name?: string }[] },
      { id: null; name?: string },
      boolean,
    ];
    expect(virtualId).toBe('discover_album_SP1');
    expect(name).toBe('Fresh Name');
    // Track artists fall through track → album payload (1547-1550).
    expect(tracks[0].artists).toEqual(['TrackArtist']);
    expect(tracks[1].artists).toEqual(['AlbumArtist']);
    expect(tracks[0].album.album_type).toBe('single');
    // The modal's artist is the ROW's name, id-less (1569).
    expect(albumObj.artists).toEqual([{ name: 'Row Artist' }]);
    expect(artistObj).toEqual({ id: null, name: 'Row Artist' });
    expect(overlayFlag).toBe(false);
    expect(overlays).toEqual(['Loading tracks for Row Album...']);
    expect(overlayHides).toBe(1);
  });

  it('falls back to the name search when no source has an id (1540)', async () => {
    stubAlbum();
    server.use(
      http.get('/api/discover/album/spotify/search', ({ request }) => {
        albumHits.push({ source: 'search', id: '', search: new URL(request.url).search });
        return HttpResponse.json(DETAIL);
      }),
    );
    const { result } = mount();
    await act(async () => {
      await result.current.openYourAlbum({ album_name: 'A', artist_name: 'B' }, 7);
    });
    expect(albumHits).toEqual([{ source: 'search', id: '', search: '?name=A&artist=B' }]);
    // No source id at all — the virtual id falls back to the grid index (1563).
    expect((artistAlbumCalls[0] as string[])[0]).toBe('discover_album_7');
  });

  it('reports the vanilla no-tracks error when every avenue is empty', async () => {
    stubAlbum(() => HttpResponse.json({ ...DETAIL, tracks: [] }));
    server.use(
      http.get('/api/discover/album/spotify/search', ({ request }) => {
        albumHits.push({ source: 'search', id: '', search: new URL(request.url).search });
        return HttpResponse.json({ ...DETAIL, tracks: [] });
      }),
    );
    const { result } = mount();
    await act(async () => {
      await result.current.openYourAlbum(
        { album_name: 'A', artist_name: 'B', spotify_album_id: 'S' },
        0,
      );
    });
    // An empty PAYLOAD (not just a failed request) still reaches the name
    // search — that is what the `albumData = null` reset in the loop buys.
    expect(albumHits.map((h) => h.source)).toEqual(['spotify', 'search']);
    expect(artistAlbumCalls).toEqual([]);
    expect(toasts).toEqual([
      { message: 'Failed to load album: No tracks found for this album', level: 'error' },
    ]);
    expect(overlayHides).toBe(1);
  });

  it('rejects a missing row without fetching (1508)', async () => {
    stubAlbum();
    const { result } = mount();
    await act(async () => {
      await result.current.openYourAlbum(undefined, 0);
    });
    expect(albumHits).toEqual([]);
    expect(toasts).toEqual([{ message: 'Album data not found', level: 'error' }]);
    expect(overlays).toEqual([]);
  });
});

describe('openRecentAlbum (11486)', () => {
  it('opens via the row source, keys the bar on discover_album_<id>, rebuilds context fresh', async () => {
    stubAlbum();
    const { result } = mount();
    await act(async () => {
      await result.current.openRecentAlbum({
        album_name: 'Stale Row Name',
        artist_name: 'RA',
        album_deezer_id: 'DZ9',
      });
    });
    expect(albumHits).toEqual([
      { source: 'deezer', id: 'DZ9', search: '?name=Stale+Row+Name&artist=RA' },
    ]);
    const [virtualId, name, tracks, artistContext, albumContext] = youtubeCalls[0] as [
      string,
      string,
      unknown[],
      { name: string; source: string },
      { name: string; album_type: string; source?: string },
    ];
    expect(virtualId).toBe('discover_album_DZ9');
    // FRESH data, not the cached row (11518, the vanilla's "critical fix").
    expect(name).toBe('Fresh Name');
    expect(albumContext.name).toBe('Fresh Name');
    expect(albumContext.album_type).toBe('single');
    // Recent's album context carries NO source key — seasonal's does.
    expect('source' in albumContext).toBe(false);
    expect(artistContext.name).toBe('RA');
    expect(artistContext.source).toBe('deezer');
    expect(tracks).toHaveLength(2);
  });

  it('names the failing source when the row has no id (11502)', async () => {
    stubAlbum();
    const { result } = mount();
    await act(async () => {
      await result.current.openRecentAlbum({ album_name: 'X', artist_name: 'Y' });
    });
    expect(albumHits).toEqual([]);
    expect(toasts).toEqual([
      { message: 'Failed to load album: No itunes album ID available', level: 'error' },
    ]);
  });
});

describe('openSeasonalAlbum (4422)', () => {
  it('keys on seasonal_album_<id> and stamps the SOURCE into the album context (4488)', async () => {
    stubAlbum();
    const { result } = mount();
    await act(async () => {
      await result.current.openSeasonalAlbum({
        album_name: 'S',
        artist_name: 'SA',
        spotify_album_id: 'SEAS1',
        source: 'itunes',
      });
    });
    const [virtualId, , , , albumContext] = youtubeCalls[0] as [
      string,
      string,
      unknown[],
      unknown,
      { source?: string },
    ];
    expect(virtualId).toBe('seasonal_album_SEAS1');
    expect(albumContext.source).toBe('itunes');
  });

  it("uses the seasonal flow's own toast prefix on failure (4500)", async () => {
    stubAlbum();
    const { result } = mount();
    await act(async () => {
      await result.current.openSeasonalAlbum({ album_name: 'S', artist_name: 'SA' });
    });
    expect(toasts).toEqual([
      { message: 'Failed to load album tracks: No album ID available', level: 'error' },
    ]);
  });
});

describe('openCacheItem — album branch (10548-10616)', () => {
  it('resolves a stale 404 id but still keys the modal on the ORIGINAL id (10607)', async () => {
    stubAlbum((_source, id) =>
      id === 'OLD' ? HttpResponse.json({}, { status: 404 }) : HttpResponse.json(DETAIL),
    );
    stubResolve({ success: true, entity_id: 'NEW', source: 'deezer' });
    const { result } = mount();
    await act(async () => {
      await result.current.openCacheItem('undiscovered', {
        entity_id: 'OLD',
        name: 'C',
        artist_name: 'CA',
        source: 'spotify',
      });
    });
    expect(resolveHits).toBe(1);
    expect(albumHits.map((h) => `${h.source}/${h.id}`)).toEqual(['spotify/OLD', 'deezer/NEW']);
    expect((youtubeCalls[0] as string[])[0]).toBe('discover_cache_OLD');
  });

  it('skips the refetch when the resolver returns the SAME id, and reports unavailable', async () => {
    stubAlbum(() => HttpResponse.json({}, { status: 404 }));
    stubResolve({ success: true, entity_id: 'OLD', source: 'spotify' });
    const { result } = mount();
    await act(async () => {
      await result.current.openCacheItem('undiscovered', {
        entity_id: 'OLD',
        name: 'C',
        artist_name: 'CA',
      });
    });
    expect(albumHits).toHaveLength(1);
    expect(toasts).toEqual([
      {
        message:
          'Failed to load album: Album not available — it may have been removed from the source',
        level: 'error',
      },
    ]);
  });

  it('rejects an id-less card up front (10549)', async () => {
    stubAlbum();
    const { result } = mount();
    await act(async () => {
      await result.current.openCacheItem('undiscovered', { name: 'C', artist_name: 'CA' });
    });
    expect(albumHits).toEqual([]);
    expect(toasts).toEqual([{ message: 'No album ID available', level: 'error' }]);
  });
});

describe('openCacheItem — track branch (10485-10545)', () => {
  it('requires an artist name before doing anything (10493)', async () => {
    stubAlbum();
    const { result } = mount();
    await act(async () => {
      await result.current.openCacheItem('deep_cuts', { name: 'T', album_id: 'AL1' });
    });
    expect(albumHits).toEqual([]);
    expect(overlays).toEqual([]);
    expect(toasts).toEqual([
      { message: 'No artist data available for this track', level: 'error' },
    ]);
  });

  it('resolves when the track has no album_id, and keys on the RESOLVED id (10538)', async () => {
    stubAlbum();
    stubResolve({ success: true, entity_id: 'RES1', source: 'itunes' });
    const { result } = mount();
    await act(async () => {
      await result.current.openCacheItem('genre_dive_tracks', {
        name: 'Track',
        album_name: 'Dive Album',
        artist_name: 'DA',
      });
    });
    expect(resolveHits).toBe(1);
    expect(albumHits).toEqual([
      { source: 'itunes', id: 'RES1', search: '?name=Dive+Album&artist=DA' },
    ]);
    expect((youtubeCalls[0] as string[])[0]).toBe('discover_cache_RES1');
  });

  it('falls back to the resolver when the album_id fetch FAILS (10510: hadAlbumId && !ok)', async () => {
    stubAlbum((_source, id) =>
      id === 'DEAD' ? HttpResponse.json({}, { status: 404 }) : HttpResponse.json(DETAIL),
    );
    stubResolve({ success: true, entity_id: 'RES2', source: 'spotify' });
    const { result } = mount();
    await act(async () => {
      await result.current.openCacheItem('deep_cuts', {
        name: 'Track',
        album_name: 'Dive Album',
        artist_name: 'DA',
        album_id: 'DEAD',
        source: 'deezer',
      });
    });
    expect(resolveHits).toBe(1);
    expect(albumHits.map((h) => `${h.source}/${h.id}`)).toEqual(['deezer/DEAD', 'spotify/RES2']);
    expect((youtubeCalls[0] as string[])[0]).toBe('discover_cache_RES2');
  });

  it('goes straight through when the album_id fetch succeeds — no resolver call', async () => {
    stubAlbum();
    stubResolve({ success: true, entity_id: 'RES1' });
    const { result } = mount();
    await act(async () => {
      await result.current.openCacheItem('deep_cuts', {
        name: 'Track',
        album_name: 'Dive Album',
        artist_name: 'DA',
        album_id: 'AL1',
        source: 'deezer',
      });
    });
    expect(resolveHits).toBe(0);
    expect(albumHits).toEqual([
      { source: 'deezer', id: 'AL1', search: '?name=Dive+Album&artist=DA' },
    ]);
    expect((youtubeCalls[0] as string[])[0]).toBe('discover_cache_AL1');
  });
});
