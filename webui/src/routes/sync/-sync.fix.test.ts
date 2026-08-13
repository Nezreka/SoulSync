/**
 * Fix core — differential where liftable (parseMusicBrainzMbid, the result
 * sort), literal pins + captured-fetch wire tests for the rest, with vanilla
 * anchors on the endpoint ladders (incl. live bug #7: the vanilla unmatch
 * ladder has no qobuz arm, so qobuz falls through to /api/youtube —
 * transcribed bug-compatible).
 */

import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { afterEach, describe, expect, it, vi } from 'vitest';

import type { FixTrack, FixSearchSource } from './-sync.fix';

import { extractFunction } from '../../test/vanilla-extract';
import {
  FIX_SEARCH_SOURCES,
  buildUpdateMatchBody,
  fetchOrganizePreference,
  fixApiPlatform,
  lookupMbRecording,
  orderedFixSearchSources,
  parseMusicBrainzMbid,
  postUnmatch,
  postUpdateMatch,
  searchFixTracks,
  setOrganizePreference,
  sortFixResults,
  unmatchApiBase,
} from './-sync.fix';
import { SYNC_SOURCES } from './-sync.sources';

const SHARED_HELPERS = readFileSync(resolve(process.cwd(), 'static/shared-helpers.js'), 'utf8');
const WISHLIST_TOOLS = readFileSync(resolve(process.cwd(), 'static/wishlist-tools.js'), 'utf8');

interface Call {
  url: string;
  method: string;
  body: unknown;
}
let calls: Call[] = [];
let responder: (url: string) => unknown = () => ({});

function stubFetch(status = 200): void {
  calls = [];
  vi.stubGlobal(
    'fetch',
    vi.fn(async (url: string, init?: RequestInit) => {
      calls.push({
        url,
        method: init?.method ?? 'GET',
        body: init?.body ? JSON.parse(init.body as string) : undefined,
      });
      return new Response(JSON.stringify(responder(url)), { status });
    }),
  );
}

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
  responder = () => ({});
});

describe('the search cascade (179-236)', () => {
  it('sources, order and endpoints match the vanilla, Discogs deliberately absent', () => {
    expect(FIX_SEARCH_SOURCES.map((s: FixSearchSource) => s.endpoint)).toEqual([
      '/api/spotify/search_tracks',
      '/api/deezer/search_tracks',
      '/api/itunes/search_tracks',
      '/api/musicbrainz/search_tracks',
    ]);
    expect(WISHLIST_TOOLS).toContain("endpoint: '/api/musicbrainz/search_tracks'");
  });

  it('active-first reorder (the label CONTAINS the key, case-insensitive)', () => {
    expect(orderedFixSearchSources('iTunes').map((s) => s.key)).toEqual([
      'itunes',
      'spotify',
      'deezer',
      'musicbrainz',
    ]);
    expect(orderedFixSearchSources('MusicBrainz').map((s) => s.key)[0]).toBe('musicbrainz');
    // Spotify active (index 0) keeps the default order — the vanilla's
    // `activeIdx > 0` guard.
    expect(orderedFixSearchSources('Spotify').map((s) => s.key)).toEqual([
      'spotify',
      'deezer',
      'itunes',
      'musicbrainz',
    ]);
    expect(orderedFixSearchSources('unknown').map((s) => s.key)[0]).toBe('spotify');
  });

  it('sortFixResults pushes live/remix/soundtrack variants below standard versions', () => {
    const sorted = sortFixResults([
      { name: 'Song (Live)' },
      { name: 'Song' },
      { name: 'Other', album: 'Greatest Hits' },
      { name: 'Plain', album: 'Album' },
    ] as FixTrack[]);
    expect(sorted.map((t) => t.name)).toEqual(['Song', 'Plain', 'Song (Live)', 'Other']);
  });
});

describe('parseMusicBrainzMbid (differential vs shared-helpers.js 4656-4675)', () => {
  const vanilla = (() => {
    const body = extractFunction('parseMusicBrainzMbid', SHARED_HELPERS);
    // eslint-disable-next-line @typescript-eslint/no-implied-eval
    return new Function(
      `const _MB_UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
       ${body}
       return parseMusicBrainzMbid;`,
    )() as (input: unknown) => string | null;
  })();

  const CASES = [
    'https://musicbrainz.org/recording/b1a9c0e5-d7f2-4c1a-9a2f-3e4b5c6d7e8f',
    'https://musicbrainz.org/recording/B1A9C0E5-D7F2-4C1A-9A2F-3E4B5C6D7E8F?utm=x#tab',
    'b1a9c0e5-d7f2-4c1a-9a2f-3e4b5c6d7e8f',
    '  b1a9c0e5-d7f2-4c1a-9a2f-3e4b5c6d7e8f  ',
    'https://musicbrainz.org/release/b1a9c0e5-d7f2-4c1a-9a2f-3e4b5c6d7e8f',
    'not-a-uuid',
    '',
    null,
  ];

  it('matches the vanilla over URLs, bare UUIDs, query junk and garbage', () => {
    for (const input of CASES) {
      expect(parseMusicBrainzMbid(input as never), String(input)).toBe(vanilla(input));
    }
    expect(parseMusicBrainzMbid('B1A9C0E5-D7F2-4C1A-9A2F-3E4B5C6D7E8F')).toBe(
      'b1a9c0e5-d7f2-4c1a-9a2f-3e4b5c6d7e8f',
    );
  });
});

describe('the endpoint ladders', () => {
  it('fixApiPlatform: mirrored→youtube, the link pair hyphenates, qobuz passes through (408)', () => {
    expect(fixApiPlatform('mirrored')).toBe('youtube');
    expect(fixApiPlatform('spotify_public')).toBe('spotify-public');
    expect(fixApiPlatform('itunes_link')).toBe('itunes-link');
    expect(fixApiPlatform('tidal')).toBe('tidal');
    expect(fixApiPlatform('qobuz')).toBe('qobuz');
  });

  it('unmatchApiBase: NO qobuz arm — it falls to /api/youtube (live bug #7, 656-662)', () => {
    expect(unmatchApiBase('tidal')).toBe('/api/tidal');
    expect(unmatchApiBase('spotify_public')).toBe('/api/spotify-public');
    expect(unmatchApiBase('listenbrainz')).toBe('/api/listenbrainz');
    expect(unmatchApiBase('qobuz')).toBe('/api/youtube');
    expect(unmatchApiBase('mirrored')).toBe('/api/youtube');
    expect(unmatchApiBase('youtube')).toBe('/api/youtube');
    // The vanilla ladder really has no qobuz arm.
    expect(WISHLIST_TOOLS).not.toContain("platform === 'qobuz'");
  });
});

describe('wire calls', () => {
  it('searchFixTracks GETs track/artist/limit and unwraps tracks', async () => {
    responder = () => ({ tracks: [{ id: 't1' }] });
    stubFetch();
    const tracks = await searchFixTracks('/api/spotify/search_tracks', 'HUMBLE.', 'Kendrick');
    expect(calls[0].url).toBe('/api/spotify/search_tracks?track=HUMBLE.&artist=Kendrick&limit=50');
    expect(tracks).toEqual([{ id: 't1' }]);
  });

  it('lookupMbRecording: found / missing (404) / no-id (200) are distinct', async () => {
    responder = () => ({ id: 'mb1', name: 'X' });
    stubFetch();
    expect(await lookupMbRecording('b1a9c0e5-d7f2-4c1a-9a2f-3e4b5c6d7e8f')).toEqual({
      kind: 'found',
      track: { id: 'mb1', name: 'X' },
    });
    expect(calls[0].url).toBe('/api/musicbrainz/recording/b1a9c0e5-d7f2-4c1a-9a2f-3e4b5c6d7e8f');
    responder = () => ({ name: 'no id here' });
    stubFetch();
    expect(await lookupMbRecording('b1a9c0e5-d7f2-4c1a-9a2f-3e4b5c6d7e8f')).toEqual({
      kind: 'no-id',
    });
    responder = () => ({});
    stubFetch(404);
    expect(await lookupMbRecording('b1a9c0e5-d7f2-4c1a-9a2f-3e4b5c6d7e8f')).toEqual({
      kind: 'missing',
    });
  });

  it('buildUpdateMatchBody + postUpdateMatch ship the #843 body to the platform endpoint', async () => {
    stubFetch();
    const body = buildUpdateMatchBody('mirrored_5', 3, 'Src', 'Artist', {
      id: 'sp1',
      name: 'Fixed',
      artists: ['A'],
      album: 'Alb',
      duration_ms: 1000,
    });
    expect(body).toEqual({
      identifier: 'mirrored_5',
      track_index: 3,
      original_name: 'Src',
      original_artist: 'Artist',
      spotify_track: {
        id: 'sp1',
        name: 'Fixed',
        artists: ['A'],
        album: 'Alb',
        duration_ms: 1000,
        image_url: null,
      },
    });
    await postUpdateMatch(SYNC_SOURCES.mirrored, body);
    expect(calls[0]).toMatchObject({
      url: '/api/youtube/discovery/update_match',
      method: 'POST',
      body,
    });
  });

  it('postUnmatch posts identifier + track_index to the ladder base', async () => {
    stubFetch();
    await postUnmatch(SYNC_SOURCES.spotify_public, 'h4sh', 2);
    expect(calls[0]).toMatchObject({
      url: '/api/spotify-public/discovery/unmatch',
      method: 'POST',
      body: { identifier: 'h4sh', track_index: 2 },
    });
  });

  it('organize preference: resolve reads, resolve+PATCH writes, false on not-found', async () => {
    responder = (url) =>
      url.includes('/resolve')
        ? { found: true, playlist: { id: 9, organize_by_playlist: true } }
        : {};
    stubFetch();
    // The ref is the BARE hash — the vanilla strips the spotify_public_
    // prefix before the wire request (normalizePlaylistOrganizeRef).
    expect(await fetchOrganizePreference('h', 'spotify_public')).toBe(true);
    expect(calls[0].url).toBe('/api/mirrored-playlists/resolve?ref=h&source=spotify_public');
    expect(await setOrganizePreference('h', 'spotify_public', true)).toBe(true);
    expect(calls[2]).toMatchObject({
      url: '/api/mirrored-playlists/9/preferences',
      method: 'PATCH',
      body: { organize_by_playlist: true },
    });
    responder = () => ({ found: false });
    stubFetch();
    expect(await setOrganizePreference('x', 'spotify_public', true)).toBe(false);
    expect(calls).toHaveLength(1); // no PATCH without a mirror row
  });
});
