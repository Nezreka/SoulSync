import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import {
  albumIsOwned,
  fetchFreshReleases,
  fetchRecentlyAdded,
  fileBadge,
  openArtistFromRail,
  openFreshRelease,
  relativeAge,
} from './-dash.content';

// The album fold itself is server-side now (get_recently_added_albums,
// pinned in tests/test_recently_added_albums.py) — these cover the mapping,
// the fallback ladder and the owned-or-modal click sequence.

// ── relativeAge / fileBadge ──────────────────────────────────────────────────

describe('relativeAge', () => {
  const now = Date.parse('2026-08-10T12:00:00Z');

  it.each([
    ['2026-08-10T11:59:30Z', 'just now'],
    ['2026-08-10T11:45:00Z', '15m ago'],
    ['2026-08-10T09:00:00Z', '3h ago'],
    ['2026-08-07T12:00:00Z', '3d ago'],
    ['2026-06-01T12:00:00Z', '2mo ago'],
  ])('%s -> %s', (iso, expected) => {
    expect(relativeAge(iso, now)).toBe(expected);
  });

  it('parses the naive UTC form SQLite CURRENT_TIMESTAMP stores', () => {
    expect(relativeAge('2026-08-10 09:00:00', now)).toBe('3h ago');
  });

  it('returns empty for garbage rather than NaN text', () => {
    expect(relativeAge('not a date', now)).toBe('');
  });
});

describe('fileBadge', () => {
  it('joins quality and source, degrading to whichever half exists', () => {
    expect(fileBadge('FLAC', 'soulseek')).toBe('FLAC · soulseek');
    expect(fileBadge('FLAC', '')).toBe('FLAC');
    expect(fileBadge('', 'tidal')).toBe('tidal');
    expect(fileBadge('', '')).toBe('');
  });
});

// ── albumIsOwned ─────────────────────────────────────────────────────────────

describe('albumIsOwned', () => {
  const owned = { owned: true, file_path: '/x.flac', track_id: 1, title: 'X' };

  it('every track owned -> the first owned entry (the play target)', () => {
    expect(albumIsOwned({ A: owned, B: { ...owned, track_id: 2 } }, ['A', 'B'])).toMatchObject({
      track_id: 1,
    });
  });

  it('ANY missing track -> null; playing over a gap would bury it', () => {
    expect(albumIsOwned({ A: owned, B: { owned: false } }, ['A', 'B'])).toBeNull();
  });

  it('owned but with no file_path cannot be played -> null', () => {
    expect(albumIsOwned({ A: { owned: true } }, ['A'])).toBeNull();
  });

  it('an empty album is not "owned"', () => {
    expect(albumIsOwned({}, [])).toBeNull();
  });
});

// ── fetchers + the click sequence ────────────────────────────────────────────

describe('fetchers', () => {
  const fetchMock = vi.fn();
  beforeEach(() => {
    fetchMock.mockReset();
    vi.stubGlobal('fetch', fetchMock);
  });
  afterEach(() => {
    vi.unstubAllGlobals();
    delete window.openDownloadMissingModalForYouTube;
  });

  const ok = (body: unknown) =>
    Promise.resolve({ ok: true, json: () => Promise.resolve(body) } as Response);

  it('fetchRecentlyAdded maps the server fold', async () => {
    fetchMock.mockReturnValueOnce(
      ok({
        albums: [
          {
            artist_name: 'Ado',
            album_name: 'Kyougen',
            thumb_url: 'k.jpg',
            added_at: '2026-08-10 09:00:00',
            track_count: 12,
            quality: 'FLAC',
            download_source: 'soulseek',
            play_title: 'Vivarium',
            play_file_path: '/music/ado/vivarium.flac',
          },
        ],
      }),
    );
    const cards = await fetchRecentlyAdded();
    expect(fetchMock).toHaveBeenCalledWith('/api/library/recently-added?limit=20');
    expect(cards[0]).toMatchObject({
      albumName: 'Kyougen',
      quality: 'FLAC',
      source: 'soulseek',
      playFilePath: '/music/ado/vivarium.flac',
    });
  });

  it('fetchRecentlyAdded returns [] on a down endpoint instead of throwing', async () => {
    fetchMock.mockReturnValueOnce(Promise.reject(new Error('down')));
    await expect(fetchRecentlyAdded()).resolves.toEqual([]);
  });

  it('fetchFreshReleases prefers the watchlist rows and keeps the album ids', async () => {
    fetchMock.mockReturnValueOnce(
      ok({
        releases: [
          {
            album_name: 'New One',
            artist_name: 'Watched',
            spotify_artist_id: 'sp1',
            album_spotify_id: 'alb1',
            source: 'spotify',
          },
        ],
      }),
    );
    const releases = await fetchFreshReleases();
    expect(releases[0]).toMatchObject({
      albumName: 'New One',
      fromDiscover: false,
      albumSpotifyId: 'alb1',
      sourceProvider: 'spotify',
    });
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it('an EMPTY watchlist falls back to the discover feed, marked as such', async () => {
    fetchMock
      .mockReturnValueOnce(ok({ releases: [] }))
      .mockReturnValueOnce(ok({ albums: [{ album_name: 'Similar', artist_name: 'Adjacent' }] }));
    const releases = await fetchFreshReleases();
    expect(fetchMock).toHaveBeenNthCalledWith(2, '/api/discover/recent-releases');
    expect(releases[0].fromDiscover).toBe(true);
  });

  it('openFreshRelease: not owned -> the standard download-missing modal', async () => {
    const modal = vi.fn();
    window.openDownloadMissingModalForYouTube = modal;
    fetchMock
      .mockReturnValueOnce(
        ok({
          id: 'alb1',
          name: 'New One',
          image_url: 'cover.jpg',
          tracks: [{ id: 't1', name: 'Song A' }],
        }),
      )
      .mockReturnValueOnce(ok({ owned_tracks: { 'Song A': { owned: false } } }));
    await openFreshRelease({
      albumName: 'New One',
      artistName: 'Watched',
      cover: '',
      releaseDate: '2026-08-01',
      trackCount: 1,
      spotifyArtistId: 'sp1',
      itunesArtistId: '',
      deezerArtistId: 'dz-artist',
      albumSpotifyId: 'alb1',
      albumItunesId: '',
      albumDeezerId: '',
      sourceProvider: 'spotify',
      owned: false,
      fromDiscover: false,
    });
    expect(fetchMock.mock.calls[0][0]).toContain('/api/discover/album/spotify/alb1');
    expect(modal).toHaveBeenCalledWith(
      'discover_album_alb1',
      'New One',
      // Track artists are NAME STRINGS and each track carries the full album —
      // the modal's other callers (discover) all hand it this shape, and thin
      // objects here rendered the modal header context-less.
      expect.arrayContaining([
        expect.objectContaining({
          name: 'Song A',
          artists: ['Watched'],
          album: expect.objectContaining({ name: 'New One', images: [{ url: 'cover.jpg' }] }),
        }),
      ]),
      expect.objectContaining({
        name: 'Watched',
        source: 'spotify',
        spotify_artist_id: 'sp1',
        deezer_artist_id: 'dz-artist',
      }),
      expect.objectContaining({ name: 'New One', album_type: 'album' }),
    );
  });

  it('openFreshRelease: no album id -> toast, no modal, no crash', async () => {
    const toast = vi.fn();
    vi.stubGlobal('showToast', toast);
    window.showToast = toast as never;
    await openFreshRelease({
      albumName: 'X',
      artistName: 'Y',
      cover: '',
      releaseDate: '',
      trackCount: 0,
      spotifyArtistId: '',
      itunesArtistId: '',
      deezerArtistId: '',
      albumSpotifyId: '',
      albumItunesId: '',
      albumDeezerId: '',
      sourceProvider: 'spotify',
      owned: false,
      fromDiscover: false,
    });
    expect(toast).toHaveBeenCalledWith(expect.stringContaining('No spotify album ID'), 'error');
    expect(fetchMock).not.toHaveBeenCalled();
  });
});

describe('openArtistFromRail', () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    delete (window as { navigateToPage?: unknown }).navigateToPage;
  });

  it('a known library id jumps straight to the artist page — no fetch', async () => {
    const nav = vi.fn();
    window.navigateToPage = nav as never;
    const fetchSpy = vi.fn();
    vi.stubGlobal('fetch', fetchSpy);
    await openArtistFromRail({ name: 'Aphex Twin', libraryArtistId: 'art_1' });
    expect(nav).toHaveBeenCalledWith('artist-detail', {
      artistId: 'art_1',
      artistName: 'Aphex Twin',
    });
    expect(fetchSpy).not.toHaveBeenCalled();
  });

  it('resolves an exact library name match (case-insensitive)', async () => {
    const nav = vi.fn();
    window.navigateToPage = nav as never;
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => ({
        json: async () => ({ artists: [{ id: 7, name: 'Aphex Twin' }] }),
      })),
    );
    await openArtistFromRail({ name: 'aphex twin' });
    expect(nav).toHaveBeenCalledWith('artist-detail', { artistId: 7, artistName: 'Aphex Twin' });
  });

  it('falls to the provider id when the library misses', async () => {
    const nav = vi.fn();
    window.navigateToPage = nav as never;
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => ({ json: async () => ({ artists: [] }) })),
    );
    await openArtistFromRail({ name: 'Fresh Face', spotifyArtistId: 'sp9' });
    expect(nav).toHaveBeenCalledWith('artist-detail', {
      artistId: 'sp9',
      artistSource: 'spotify',
      artistName: 'Fresh Face',
      forceReload: true,
    });
  });
});
