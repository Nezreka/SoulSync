/**
 * The URL-import data layer, pinned literal-by-literal against the vanilla
 * parse heads (sync-services.js 2706/6611/7633/8806) and the url-history
 * region (8654-8800).
 */

import { afterEach, describe, expect, it, vi } from 'vitest';

import {
  asString,
  checkNoteCounts,
  deezerAlreadyLoaded,
  deezerInputResult,
  deezerMirrorTracks,
  itunesLinkAlreadyLoaded,
  itunesLinkCardIcon,
  itunesLinkMirrorTracks,
  itunesLinkTypeBadge,
  itunesLinkUrlError,
  readUrlHistory,
  slashTextCounts,
  slashTextProgressLine,
  spotifyPublicAlreadyLoaded,
  spotifyPublicCardIcon,
  spotifyPublicMirrorTracks,
  spotifyPublicTypeBadge,
  spotifyPublicUrlError,
  writeUrlHistory,
  youtubeAlreadyLoaded,
  youtubeMirrorTracks,
  youtubeUrlError,
  ytActionButtonText,
  ytProgressVisible,
} from './-sync.url-tabs';

afterEach(() => {
  localStorage.clear();
  vi.restoreAllMocks();
});

describe('asString', () => {
  it('coerces strings and numbers only — objects fall to the fallback', () => {
    expect(asString('x')).toBe('x');
    expect(asString(42)).toBe('42');
    expect(asString({ nested: true })).toBe('');
    expect(asString(undefined, 'fb')).toBe('fb');
    expect(asString(null)).toBe('');
  });
});

describe('input validation toasts (verbatim)', () => {
  it('youtube (8810-8819)', () => {
    expect(youtubeUrlError('')).toBe('Please enter a YouTube playlist URL');
    expect(youtubeUrlError('https://example.com/x')).toBe(
      'Please enter a valid YouTube playlist URL',
    );
    expect(youtubeUrlError('https://youtube.com/playlist?list=PL1')).toBeNull();
    expect(youtubeUrlError('https://music.youtube.com/playlist?list=PL1')).toBeNull();
  });

  it('deezer (2710-2728) — url, locale url, raw numeric, junk', () => {
    expect(deezerInputResult('')).toEqual({
      ok: false,
      error: 'Please paste a Deezer playlist URL',
    });
    expect(deezerInputResult('https://spotify.com/x')).toEqual({
      ok: false,
      error: 'Invalid Deezer playlist URL. Expected format: deezer.com/playlist/{id}',
    });
    expect(deezerInputResult('https://www.deezer.com/playlist/123')).toEqual({
      ok: true,
      id: '123',
    });
    expect(deezerInputResult('https://www.deezer.com/en/playlist/456')).toEqual({
      ok: true,
      id: '456',
    });
    expect(deezerInputResult('789')).toEqual({ ok: true, id: '789' });
  });

  it('spotify-public (6615-6625) — urls + URIs', () => {
    expect(spotifyPublicUrlError('')).toBe('Please enter a Spotify URL');
    // A link this page CAN read, just not here, now names the tab that wants
    // it rather than describing the field the user is standing in.
    expect(spotifyPublicUrlError('https://deezer.com/playlist/1')).toBe(
      'That is a Deezer link. Paste it on the Deezer Link tab to load it.',
    );
    // Anything unrecognisable still gets the tab's own message, verbatim.
    expect(spotifyPublicUrlError('https://example.com/whatever')).toBe(
      'Please enter a valid Spotify playlist or album URL',
    );
    expect(spotifyPublicUrlError('https://open.spotify.com/playlist/abc')).toBeNull();
    expect(spotifyPublicUrlError('https://open.spotify.com/album/abc')).toBeNull();
    expect(spotifyPublicUrlError('spotify:playlist:abc')).toBeNull();
    expect(spotifyPublicUrlError('spotify:album:abc')).toBeNull();
  });

  it("itunes (7637-7650) — keeps the vanilla's 'a iTunes' typo, case-insensitive hosts", () => {
    expect(itunesLinkUrlError('')).toBe('Please enter a iTunes URL');
    expect(itunesLinkUrlError('https://example.com')).toBe(
      'Please enter a valid iTunes or Apple Music URL',
    );
    expect(itunesLinkUrlError('https://MUSIC.APPLE.COM/us/album/x/123')).toBeNull();
    expect(itunesLinkUrlError('itunes:track:123')).toBeNull();
    expect(itunesLinkUrlError('applemusic:playlist:pl.abc')).toBeNull();
  });
});

describe('already-loaded dedupe (_isUrlAlreadyLoaded 8744-8774)', () => {
  it('deezer compares String(id)', () => {
    expect(deezerAlreadyLoaded([{ id: 123 }], '123')).toBe(true);
    expect(deezerAlreadyLoaded([{ id: 123 }], '124')).toBe(false);
  });

  it('spotify-public: extracted id first, raw url fallback', () => {
    const loaded = [{ id: 'abc123', url: 'spotify:playlist:xyz' }];
    expect(spotifyPublicAlreadyLoaded(loaded, 'https://open.spotify.com/playlist/abc123')).toBe(
      true,
    );
    expect(spotifyPublicAlreadyLoaded(loaded, 'spotify:playlist:xyz')).toBe(true);
    expect(spotifyPublicAlreadyLoaded(loaded, 'https://open.spotify.com/playlist/other1')).toBe(
      false,
    );
  });

  it('itunes: id AND type must match, raw url fallback', () => {
    const loaded = [{ id: '99', type: 'album', url: 'itunes:album:99' }];
    expect(itunesLinkAlreadyLoaded(loaded, 'https://music.apple.com/us/album/x/99')).toBe(true);
    expect(itunesLinkAlreadyLoaded(loaded, 'https://music.apple.com/us/song/x/99')).toBe(false);
    expect(itunesLinkAlreadyLoaded(loaded, 'itunes:album:99')).toBe(true);
  });

  it('youtube: url list (replaces the vanilla DOM data-url scan)', () => {
    expect(
      youtubeAlreadyLoaded(
        ['https://youtube.com/playlist?list=A'],
        'https://youtube.com/playlist?list=A',
      ),
    ).toBe(true);
    expect(youtubeAlreadyLoaded([], 'https://youtube.com/playlist?list=A')).toBe(false);
  });
});

describe('auto-mirror track projections (exact field expressions)', () => {
  it('deezer (2756-2760): first artist raw, string-only album', () => {
    expect(
      deezerMirrorTracks([
        {
          name: 'Song',
          artists: ['Artist A', 'Artist B'],
          album: 'The Album',
          duration_ms: 1000,
          id: 7,
        },
        { artists: 'Solo', album: { name: 'ObjAlbum' } },
      ]),
    ).toEqual([
      {
        track_name: 'Song',
        artist_name: 'Artist A',
        album_name: 'The Album',
        duration_ms: 1000,
        source_track_id: 7,
      },
      { track_name: '', artist_name: 'Solo', album_name: '', duration_ms: 0, source_track_id: '' },
    ]);
  });

  it('spotify-public (6669-6675): joined artist NAMES, object album', () => {
    expect(
      spotifyPublicMirrorTracks([
        {
          name: 'S',
          artists: [{ name: 'A' }, { name: 'B' }],
          album: { name: 'Al' },
          duration_ms: 5,
          id: 'x',
        },
        { artists: 'not-an-array' },
      ]),
    ).toEqual([
      {
        track_name: 'S',
        artist_name: 'A, B',
        album_name: 'Al',
        duration_ms: 5,
        source_track_id: 'x',
      },
      { track_name: '', artist_name: '', album_name: '', duration_ms: 0, source_track_id: '' },
    ]);
  });

  it('itunes (7694-7700): hardened object-or-string artist map with filter(Boolean)', () => {
    expect(
      itunesLinkMirrorTracks([{ name: 'S', artists: [{ name: 'A' }, 'B', null, { name: '' }] }])[0]
        .artist_name,
    ).toBe('A, B');
  });

  it("youtube (8861-8864): t.title fallback, t.artist fallback, album ALWAYS ''", () => {
    expect(
      youtubeMirrorTracks([
        { title: 'From Title', artist: 'Solo', album: 'Ignored', duration_ms: 9, id: 'v1' },
        { name: 'Named', artists: ['First', 'Second'] },
      ]),
    ).toEqual([
      {
        track_name: 'From Title',
        artist_name: 'Solo',
        album_name: '',
        duration_ms: 9,
        source_track_id: 'v1',
      },
      {
        track_name: 'Named',
        artist_name: 'First',
        album_name: '',
        duration_ms: 0,
        source_track_id: '',
      },
    ]);
  });
});

describe('card presentation', () => {
  it('spotify-public badge/icon (6736-6740): album grey 💿, playlist Spotify green 🎵', () => {
    expect(spotifyPublicCardIcon({ type: 'album' })).toBe('💿');
    expect(spotifyPublicCardIcon({ type: 'playlist' })).toBe('🎵');
    expect(spotifyPublicTypeBadge({ type: 'album' })).toEqual({ text: 'Album', color: '#b3b3b3' });
    expect(spotifyPublicTypeBadge({ type: 'playlist' })).toEqual({
      text: 'Playlist',
      color: '#1DB954',
    });
  });

  it('itunes badge/icon (7762-7766): track ♪-less 🎵 + Apple pink, playlist ♫', () => {
    expect(itunesLinkCardIcon({ type: 'album' })).toBe('💿');
    expect(itunesLinkCardIcon({ type: 'playlist' })).toBe('♫');
    expect(itunesLinkCardIcon({ type: 'track' })).toBe('🎵');
    expect(itunesLinkTypeBadge({ type: 'album' })).toEqual({ text: 'Album', color: '#b3b3b3' });
    expect(itunesLinkTypeBadge({ type: 'playlist' })).toEqual({
      text: 'Playlist',
      color: '#fa586a',
    });
    expect(itunesLinkTypeBadge({ type: 'track' })).toEqual({ text: 'Track', color: '#fa586a' });
  });

  it("youtube button map drift (8982-9037): 'Start Discovery' / 'View Details' ×2 / 'View Results'", () => {
    expect(ytActionButtonText('fresh')).toBe('Start Discovery');
    expect(ytActionButtonText('discovering')).toBe('View Progress');
    expect(ytActionButtonText('discovered')).toBe('View Details');
    expect(ytActionButtonText('syncing')).toBe('View Progress');
    expect(ytActionButtonText('sync_complete')).toBe('View Details');
    expect(ytActionButtonText('downloading')).toBe('View Downloads');
    expect(ytActionButtonText('download_complete')).toBe('View Results');
  });

  it('youtube progress visibility: only while work runs', () => {
    expect(ytProgressVisible('discovering')).toBe(true);
    expect(ytProgressVisible('syncing')).toBe(true);
    expect(ytProgressVisible('downloading')).toBe(true);
    expect(ytProgressVisible('fresh')).toBe(false);
    expect(ytProgressVisible('discovered')).toBe(false);
    expect(ytProgressVisible('sync_complete')).toBe(false);
    expect(ytProgressVisible('download_complete')).toBe(false);
  });
});

describe('card progress lines', () => {
  it('slash-text (updateYouTubeCardProgress 9120-9125): failed = total - matches', () => {
    expect(slashTextProgressLine({ spotify_total: 10, spotify_matches: 7 })).toBe(
      '♪ 10 / ✓ 7 / ✗ 3 / 70%',
    );
    expect(slashTextProgressLine({})).toBe('♪ 0 / ✓ 0 / ✗ 0 / 0%');
  });

  it('check-note counts: null (no paint) when total is 0', () => {
    expect(checkNoteCounts({ spotify_total: 5, spotify_matches: 2 })).toEqual({
      total: 5,
      matches: 2,
    });
    expect(checkNoteCounts({ spotify_total: 0, spotify_matches: 0 })).toBeNull();
    expect(checkNoteCounts({})).toBeNull();
  });
});

describe('url-history storage (getUrlHistory 8662-8669)', () => {
  it('round-trips per-source keys and survives corrupt JSON', () => {
    writeUrlHistory('deezer', [{ url: 'u1', name: 'n1', ts: 1 }]);
    expect(localStorage.getItem('soulsync-url-history-deezer')).toBe(
      '[{"url":"u1","name":"n1","ts":1}]',
    );
    expect(readUrlHistory('deezer')).toEqual([{ url: 'u1', name: 'n1', ts: 1 }]);
    expect(readUrlHistory('youtube')).toEqual([]);
    localStorage.setItem('soulsync-url-history-youtube', 'not-json');
    expect(readUrlHistory('youtube')).toEqual([]);
    expect(readUrlHistory('unknown-source')).toEqual([]);
  });
});

describe('slashTextCounts — one arithmetic for the card bar and the legacy string', () => {
  it('failed is total-matches and percent is matches/total', () => {
    expect(slashTextCounts({ spotify_total: 10, spotify_matches: 7 })).toEqual({
      total: 10,
      matches: 7,
      failed: 3,
      percentage: 70,
    });
  });

  it('missing counters default to zero rather than NaN', () => {
    expect(slashTextCounts({})).toEqual({ total: 0, matches: 0, failed: 0, percentage: 0 });
  });

  it('the string is built FROM it, so the two can never disagree', () => {
    // The card renders the numbers and the vanilla string renders the text;
    // splitting them was only safe because both read this one function.
    const input = { spotify_total: 9, spotify_matches: 5 };
    const c = slashTextCounts(input);
    expect(slashTextProgressLine(input)).toBe(
      `♪ ${c.total} / ✓ ${c.matches} / ✗ ${c.failed} / ${c.percentage}%`,
    );
  });
});
