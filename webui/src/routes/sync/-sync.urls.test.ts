/**
 * URL helpers — differential against the live sync-services.js. The history
 * list ops run the REAL saveUrlHistory/removeUrlHistoryEntry against a stubbed
 * localStorage; the id extractors run against the REAL extractITunesLinkId and
 * the _isUrlAlreadyLoaded branches (stubbed source arrays).
 */

import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { describe, expect, it } from 'vitest';

import type { UrlHistoryEntry } from './-sync.urls';

import { extractFunction } from '../../test/vanilla-extract';
import {
  URL_HISTORY_MAX,
  URL_HISTORY_SOURCES,
  extractDeezerPlaylistId,
  extractITunesLinkId,
  extractSpotifyPublicId,
  historyWithEntry,
  historyWithout,
  pillDisplayName,
} from './-sync.urls';

const SYNC_SERVICES = readFileSync(resolve(process.cwd(), 'static/sync-services.js'), 'utf8');

describe('URL_HISTORY_SOURCES stays anchored to the vanilla', () => {
  it('keys and cap match the live declarations', () => {
    expect(URL_HISTORY_MAX).toBe(10);
    expect(SYNC_SERVICES).toContain('const URL_HISTORY_MAX = 10;');
    for (const [source, cfg] of Object.entries(URL_HISTORY_SOURCES)) {
      expect(SYNC_SERVICES, source).toContain(`key: '${cfg.key}'`);
    }
    expect(Object.keys(URL_HISTORY_SOURCES)).toEqual([
      'youtube',
      'deezer',
      'spotify-public',
      'itunes-link',
    ]);
  });
});

describe('history list ops (differential vs saveUrlHistory/removeUrlHistoryEntry)', () => {
  /**
   * Run the REAL vanilla pair against a map-backed localStorage and a frozen
   * clock, then compare the stored JSON with the pure list ops.
   */
  const bodies = ['getUrlHistory', 'saveUrlHistory', 'removeUrlHistoryEntry']
    .map((n) => extractFunction(n, SYNC_SERVICES))
    .join('\n');
  // eslint-disable-next-line @typescript-eslint/no-implied-eval
  const makeVanillaStore = new Function(
    'now',
    `
    const store = new Map();
    const localStorage = {
      getItem: (k) => (store.has(k) ? store.get(k) : null),
      setItem: (k, v) => store.set(k, v),
    };
    const Date = { now: () => now };
    const renderUrlHistory = () => {};
    const URL_HISTORY_MAX = 10;
    const URL_HISTORY_SOURCES = {
      youtube: { key: 'soulsync-url-history-youtube' },
      deezer: { key: 'soulsync-url-history-deezer' },
      'spotify-public': { key: 'soulsync-url-history-spotify-public' },
      'itunes-link': { key: 'soulsync-url-history-itunes-link' },
    };
    ${bodies}
    return { save: saveUrlHistory, remove: removeUrlHistoryEntry, get: getUrlHistory };
  `,
  ) as (now: number) => {
    save: (source: string, url: string, name?: string) => void;
    remove: (source: string, url: string) => void;
    get: (source: string) => UrlHistoryEntry[];
  };

  it('add + dedupe + cap + remove match the vanilla step for step', () => {
    const NOW = 1700000000000;
    const vanilla = makeVanillaStore(NOW);
    let ours: UrlHistoryEntry[] = [];

    // 12 adds overflow the cap; one is a duplicate that must move to front.
    for (let i = 0; i < 12; i++) {
      const url = `https://example.com/${i}`;
      vanilla.save('youtube', url, `List ${i}`);
      ours = historyWithEntry(ours, url, `List ${i}`, NOW);
    }
    vanilla.save('youtube', 'https://example.com/5', 'List 5 again');
    ours = historyWithEntry(ours, 'https://example.com/5', 'List 5 again', NOW);
    expect(ours).toEqual(vanilla.get('youtube'));
    expect(ours).toHaveLength(10);
    expect(ours[0].name).toBe('List 5 again');

    vanilla.remove('youtube', 'https://example.com/11');
    ours = historyWithout(ours, 'https://example.com/11');
    expect(ours).toEqual(vanilla.get('youtube'));

    // Name falls back to the url.
    vanilla.save('deezer', 'https://deezer.com/playlist/1', '');
    expect(historyWithEntry([], 'https://deezer.com/playlist/1', '', NOW)).toEqual(
      vanilla.get('deezer'),
    );
  });

  it('pins the pill truncation as literals', () => {
    expect(pillDisplayName('short')).toBe('short');
    expect(pillDisplayName('x'.repeat(30))).toBe('x'.repeat(30));
    expect(pillDisplayName('y'.repeat(31))).toBe('y'.repeat(28) + '...');
  });
});

describe('id extractors (differential vs extractITunesLinkId + _isUrlAlreadyLoaded branches)', () => {
  type ItunesVanilla = { extractITunesLinkId: (u: string) => { type: string; id: string } | null };
  const itunesVanilla = lift<ItunesVanilla>(['extractITunesLinkId']);

  function lift<T>(names: string[]): T {
    const body = names.map((n) => extractFunction(n, SYNC_SERVICES)).join('\n');
    // eslint-disable-next-line @typescript-eslint/no-implied-eval
    return new Function(`${body}\nreturn { ${names.join(', ')} };`)() as T;
  }

  const ITUNES_URLS = [
    'itunes:album:12345',
    'applemusic:track:999',
    'APPLEMUSIC:PLAYLIST:pl.abc-123',
    'https://music.apple.com/us/album/damn/1440881047',
    'https://music.apple.com/us/album/damn/1440881047?i=1440881134',
    'https://itunes.apple.com/song/humble/1440881134',
    'https://music.apple.com/us/song/1440881134',
    'https://music.apple.com/us/playlist/todays-hits/pl.f4d106fed2bd41149aaacabb233eb5eb',
    'https://music.apple.com/us/album/not-numeric/abc',
    'not a url at all',
    '',
  ];

  it('extractITunesLinkId matches the vanilla over every scheme', () => {
    for (const u of ITUNES_URLS) {
      expect(extractITunesLinkId(u), u).toEqual(itunesVanilla.extractITunesLinkId(u));
    }
  });

  it('pins the iTunes shapes as literals', () => {
    expect(extractITunesLinkId('itunes:album:12345')).toEqual({ type: 'album', id: '12345' });
    expect(
      extractITunesLinkId('https://music.apple.com/us/album/damn/1440881047?i=1440881134'),
    ).toEqual({ type: 'track', id: '1440881134' }); // ?i= track param beats the album path
    expect(
      extractITunesLinkId(
        'https://music.apple.com/us/playlist/x/pl.f4d106fed2bd41149aaacabb233eb5eb',
      ),
    ).toEqual({ type: 'playlist', id: 'pl.f4d106fed2bd41149aaacabb233eb5eb' });
    expect(extractITunesLinkId('nonsense')).toBe(null);
  });

  /**
   * The deezer/spotify-public regexes only exist inline in _isUrlAlreadyLoaded
   * (and the parse heads). Run that REAL function with stubbed source arrays
   * and check the port's extractors agree on which URLs count as loaded.
   */
  const alreadyLoadedBody = [
    extractFunction('_isUrlAlreadyLoaded', SYNC_SERVICES),
    extractFunction('extractITunesLinkId', SYNC_SERVICES),
  ].join('\n');
  // eslint-disable-next-line @typescript-eslint/no-implied-eval
  const isLoadedVanilla = new Function(
    'source',
    'url',
    'arrays',
    `
    const deezerPlaylists = arrays.deezer;
    const spotifyPublicPlaylists = arrays.spotifyPublic;
    const itunesLinkPlaylists = arrays.itunes;
    const document = { getElementById: () => null };
    ${alreadyLoadedBody}
    return _isUrlAlreadyLoaded(source, url);
  `,
  ) as (source: string, url: string, arrays: Record<string, unknown[]>) => boolean;

  const ARRAYS = {
    deezer: [{ id: 908622995 }],
    spotifyPublic: [{ id: '37i9dQZF1DXcBWIGoYBM5M', url: 'https://short.link/x' }],
    itunes: [{ id: '1440881047', type: 'album', url: 'https://short.link/y' }],
  };

  it('deezer branch agrees with extractDeezerPlaylistId', () => {
    const CASES = [
      'https://www.deezer.com/en/playlist/908622995',
      'https://deezer.com/playlist/908622995',
      '908622995',
      'https://deezer.com/playlist/1111',
      'garbage',
    ];
    for (const u of CASES) {
      const id = extractDeezerPlaylistId(u);
      const ourVerdict = Boolean(id && ARRAYS.deezer.find((p) => String(p.id) === String(id)));
      expect(ourVerdict, u).toBe(isLoadedVanilla('deezer', u, ARRAYS));
    }
    expect(extractDeezerPlaylistId('https://www.deezer.com/en/playlist/908622995')).toBe(
      '908622995',
    );
    expect(extractDeezerPlaylistId('garbage')).toBe(null);
  });

  it('spotify-public branch agrees with extractSpotifyPublicId', () => {
    const CASES = [
      'https://open.spotify.com/playlist/37i9dQZF1DXcBWIGoYBM5M',
      'https://open.spotify.com/album/37i9dQZF1DXcBWIGoYBM5M?si=x',
      'https://open.spotify.com/playlist/other',
      'https://short.link/x', // url fallback
      'garbage',
    ];
    for (const u of CASES) {
      const parsed = extractSpotifyPublicId(u);
      const ourVerdict = Boolean(
        (parsed && ARRAYS.spotifyPublic.some((p) => p.id === parsed.id)) ||
        ARRAYS.spotifyPublic.some((p) => p.url === u),
      );
      expect(ourVerdict, u).toBe(isLoadedVanilla('spotify-public', u, ARRAYS));
    }
    expect(extractSpotifyPublicId('https://open.spotify.com/album/abc123')).toEqual({
      type: 'album',
      id: 'abc123',
    });
  });
});

describe('gaps the PR review proved unpinned', () => {
  it('an Apple playlist id MUST start with pl. (sync-services.js 8788)', () => {
    expect(extractITunesLinkId('https://music.apple.com/us/playlist/x/pl.abc123')).toEqual({
      type: 'playlist',
      id: 'pl.abc123',
    });
    // Without the pl. anchor this would match any trailing path segment.
    expect(extractITunesLinkId('https://music.apple.com/us/playlist/x/notpl')).toBeNull();
  });

  it('the four history-pill icons are user-visible and verbatim (8655-8658)', () => {
    expect(URL_HISTORY_SOURCES.youtube.icon).toBe('▶');
    expect(URL_HISTORY_SOURCES.deezer.icon).toBe('🎵');
    expect(URL_HISTORY_SOURCES['spotify-public'].icon).toBe('🎧');
    expect(URL_HISTORY_SOURCES['itunes-link'].icon).toBe('♪');
  });
});
