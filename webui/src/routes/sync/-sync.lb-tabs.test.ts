/**
 * The small-tab data layer — literal pins transcribed from
 * sync-listenbrainz.js / sync-lastfm.js / sync-soulsync-discovery.js
 * (file:line cited per describe), wire calls against a captured fetch.
 */

import { afterEach, describe, expect, it, vi } from 'vitest';

import type { SsdTrack } from './-sync.lb-tabs';

import {
  LB_EMPTY_MESSAGES,
  LB_SUB_TABS,
  buildSsdMirrorPayload,
  buildSsdMirrorTracks,
  fetchLastfmRadios,
  fetchLbCategories,
  fetchLbPlaylistTracks,
  fetchSsdRecords,
  lbCardProgressLine,
  postLbCacheRefresh,
  postSsdRefresh,
  soulsyncSyntheticId,
  ssdStaleness,
  unwrapJspfPlaylist,
} from './-sync.lb-tabs';

interface Call {
  url: string;
  method: string;
}
let calls: Call[] = [];
let responder: (url: string) => unknown = () => ({});
let failer: ((url: string) => boolean) | null = null;

function stubFetch(status = 200): void {
  calls = [];
  vi.stubGlobal(
    'fetch',
    vi.fn(async (url: string, init?: RequestInit) => {
      calls.push({ url, method: init?.method ?? 'GET' });
      if (failer?.(url)) throw new Error('network down');
      return new Response(JSON.stringify(responder(url)), { status });
    }),
  );
}

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
  responder = () => ({});
  failer = null;
});

describe('unwrapJspfPlaylist (sync-listenbrainz.js 89-105)', () => {
  const DEFAULTS = { title: 'ListenBrainz Playlist', creator: 'ListenBrainz' };

  it('unwraps the JSPF envelope: mbid from the identifier URL, count fallbacks in order', () => {
    expect(
      unwrapJspfPlaylist(
        {
          playlist: {
            identifier: 'https://listenbrainz.org/playlist/mbid-123',
            title: 'Weekly Jams',
            creator: 'troi-bot',
            annotation: { track_count: 25 },
          },
        },
        DEFAULTS,
      ),
    ).toEqual({ mbid: 'mbid-123', title: 'Weekly Jams', creator: 'troi-bot', count: 25 });
    // track_count beats annotation beats track array length.
    expect(
      unwrapJspfPlaylist(
        { playlist: { identifier: 'x/m1', track_count: 7, annotation: { track_count: 2 } } },
        DEFAULTS,
      ).count,
    ).toBe(7);
    expect(
      unwrapJspfPlaylist({ playlist: { identifier: 'x/m1', track: [{}, {}] } }, DEFAULTS).count,
    ).toBe(2);
  });

  it('the lastfm variant does NOT fall back to inner.name (sync-lastfm.js 68)', () => {
    expect(
      unwrapJspfPlaylist(
        { playlist: { identifier: 'x/m9', name: 'named-not-titled' } },
        { title: 'Last.fm Radio', creator: 'Last.fm' },
        { nameFallback: false },
      ).title,
    ).toBe('Last.fm Radio');
  });

  it('bare (non-JSPF) entries and defaults', () => {
    expect(unwrapJspfPlaylist({ id: 'm2', name: 'Bare' }, DEFAULTS)).toEqual({
      mbid: 'm2',
      title: 'Bare',
      creator: 'ListenBrainz',
      count: 0,
    });
    expect(unwrapJspfPlaylist({}, { title: 'Last.fm Radio', creator: 'Last.fm' })).toEqual({
      mbid: '',
      title: 'Last.fm Radio',
      creator: 'Last.fm',
      count: 0,
    });
  });

  it('sub-tab list + empty copy match the vanilla (80-84, index.html data-lb-type)', () => {
    expect(LB_SUB_TABS.map((t) => t.type)).toEqual([
      'created_for_user',
      'user_created',
      'collaborative',
    ]);
    expect(LB_EMPTY_MESSAGES.user_created).toBe(
      "You haven't created any ListenBrainz playlists yet.",
    );
    expect(LB_EMPTY_MESSAGES.collaborative).toBe('No collaborative playlists.');
  });
});

describe('lbCardProgressLine (_refreshOneLbSyncCard 253-283)', () => {
  it('fresh hides; discovery counters render with the pct fallback', () => {
    expect(
      lbCardProgressLine({
        phase: 'fresh',
        spotifyTotal: 5,
        spotifyMatches: 1,
        discoveryProgress: 10,
      }),
    ).toBe(null);
    expect(
      lbCardProgressLine({
        phase: 'discovered',
        spotifyTotal: 10,
        spotifyMatches: 7,
        discoveryProgress: 0,
      }),
    ).toBe('♪ 10 / ✓ 7 / ✗ 3 / 70%');
    // Total 0 → the discovery progress stands in for the percent.
    expect(
      lbCardProgressLine({
        phase: 'discovering',
        spotifyTotal: 0,
        spotifyMatches: 0,
        discoveryProgress: 40,
      }),
    ).toBe('♪ 0 / ✓ 0 / ✗ 0 / 40%');
  });

  it('syncing reads lastSyncProgress; failed falls back to total-matched; percent is matched/total', () => {
    expect(
      lbCardProgressLine({
        phase: 'syncing',
        spotifyTotal: 99,
        spotifyMatches: 99,
        discoveryProgress: 99,
        lastSyncProgress: { total_tracks: 10, matched_tracks: 4 },
      }),
    ).toBe('♪ 10 / ✓ 4 / ✗ 6 / 40%');
    expect(
      lbCardProgressLine({
        phase: 'sync_complete',
        spotifyTotal: 0,
        spotifyMatches: 0,
        discoveryProgress: 0,
        lastSyncProgress: { total_tracks: 10, matched_tracks: 4, failed_tracks: 1 },
      }),
    ).toBe('♪ 10 / ✓ 4 / ✗ 1 / 40%');
  });
});

describe('the LB wire calls', () => {
  it('fetchLbCategories: three parallel category GETs + the auth detection (33-47)', async () => {
    responder = (url) =>
      url.includes('created-for')
        ? { success: true, playlists: [{ a: 1 }] }
        : { success: true, playlists: [] };
    stubFetch();
    const categories = await fetchLbCategories();
    expect(calls.map((c) => c.url).sort()).toEqual([
      '/api/discover/listenbrainz/collaborative',
      '/api/discover/listenbrainz/created-for',
      '/api/discover/listenbrainz/user-playlists',
    ]);
    expect(categories.unauthenticated).toBe(false);
    expect(categories.playlists.created_for_user).toEqual([{ a: 1 }]);

    responder = (url) =>
      url.includes('created-for')
        ? { success: false, error: 'ListenBrainz NOT AUTHENTICATED' }
        : { success: true, playlists: [] };
    stubFetch();
    expect((await fetchLbCategories()).unauthenticated).toBe(true);
  });

  it('fetchLastfmRadios surfaces the error field (sync-lastfm.js 36-39)', async () => {
    responder = () => ({ success: false, error: 'boom' });
    stubFetch();
    expect(await fetchLastfmRadios()).toEqual({ playlists: [], error: 'boom' });
    responder = () => ({ success: true, playlists: [{ x: 1 }] });
    stubFetch();
    expect(await fetchLastfmRadios()).toEqual({ playlists: [{ x: 1 }] });
  });

  it('postLbCacheRefresh POSTs and swallows failures (348-354)', async () => {
    failer = () => true;
    stubFetch();
    await postLbCacheRefresh(); // must not throw
    expect(calls[0]).toEqual({ url: '/api/discover/listenbrainz/refresh', method: 'POST' });
  });

  it('fetchLbPlaylistTracks maps the track shape and throws on non-ok (180-194)', async () => {
    responder = () => ({
      tracks: [{ track_name: 'T', artist_name: 'A', recording_mbid: 'r1', duration_ms: 100 }],
    });
    stubFetch();
    expect(await fetchLbPlaylistTracks('mbid 1')).toEqual([
      {
        track_name: 'T',
        artist_name: 'A',
        album_name: '',
        duration_ms: 100,
        mbid: 'r1',
        release_mbid: '',
        album_cover_url: '',
      },
    ]);
    expect(calls[0].url).toBe('/api/discover/listenbrainz/playlist/mbid%201');
    stubFetch(500);
    await expect(fetchLbPlaylistTracks('m')).rejects.toThrow(
      'Failed to load playlist tracks (500)',
    );
  });
});

describe('SoulSync Discovery (sync-soulsync-discovery.js)', () => {
  it('soulsyncSyntheticId collapses the empty variant (124-129)', () => {
    expect(soulsyncSyntheticId('hidden_gems')).toBe('ssd_hidden_gems');
    expect(soulsyncSyntheticId('time_machine', '1990s')).toBe('ssd_time_machine_1990s');
  });

  it('ssdStaleness: never-generated / stale / ready with the vanilla colors (86-90)', () => {
    expect(ssdStaleness({ kind: 'k', _never_generated: true, is_stale: true })).toEqual({
      text: 'Tap “Refresh & Mirror” to generate',
      color: '#facc15',
    });
    expect(ssdStaleness({ kind: 'k', is_stale: true })).toEqual({
      text: 'Stale — refresh to regenerate',
      color: '#facc15',
    });
    expect(ssdStaleness({ kind: 'k' })).toEqual({ text: 'Ready', color: '#14b8a6' });
  });

  it('fetchSsdRecords: existing + synthetic never-generated SINGLETONS; kinds failure tolerated (33-57)', async () => {
    responder = (url) =>
      url.includes('/kinds')
        ? {
            success: true,
            kinds: [
              { kind: 'hidden_gems', name_template: 'Hidden Gems' },
              { kind: 'listening_mix' }, // already generated → not synthesized
              { kind: 'time_machine', requires_variant: true }, // variant kinds excluded
            ],
          }
        : { success: true, playlists: [{ kind: 'listening_mix', variant: '', track_count: 30 }] };
    stubFetch();
    const { records } = await fetchSsdRecords();
    expect(records).toEqual([
      { kind: 'listening_mix', variant: '', track_count: 30 },
      {
        kind: 'hidden_gems',
        variant: '',
        name: 'Hidden Gems',
        track_count: 0,
        is_stale: true,
        _never_generated: true,
      },
    ]);

    // A kinds failure must never sink the tab (49-52).
    failer = (url) => url.includes('/kinds');
    responder = () => ({ success: true, playlists: [{ kind: 'listening_mix' }] });
    stubFetch();
    expect((await fetchSsdRecords()).records).toEqual([{ kind: 'listening_mix' }]);
  });

  it('buildSsdMirrorTracks: provider precedence, extra_data as a JSON STRING, null without an id (170-202)', () => {
    const tracks: SsdTrack[] = [
      {
        track_name: 'T',
        artist_name: 'A',
        album_name: 'Alb',
        duration_ms: 100,
        album_cover_url: 'http://img/1',
        spotify_track_id: 'sp1',
        deezer_track_id: 'dz-ignored', // spotify wins the precedence ladder
      },
      { track_name: 'NoId', artist_name: 'B' },
    ];
    const mirrored = buildSsdMirrorTracks(tracks);
    expect(mirrored[0].source_track_id).toBe('sp1');
    const extra = JSON.parse(mirrored[0].extra_data as string);
    expect(extra).toEqual({
      discovered: true,
      provider: 'spotify',
      confidence: 1.0,
      matched_data: {
        id: 'sp1',
        name: 'T',
        artists: [{ name: 'A' }],
        album: { name: 'Alb', images: [{ url: 'http://img/1', height: 600, width: 600 }] },
        duration_ms: 100,
        image_url: 'http://img/1',
        source: 'spotify',
      },
    });
    expect(mirrored[1].extra_data).toBe(null);
    expect(mirrored[1].source_track_id).toBe('');
  });

  it('buildSsdMirrorPayload carries the auto-sync description + SoulSync owner (217-228)', () => {
    const payload = buildSsdMirrorPayload('time_machine', '1990s', 'Time Machine — 1990s', []);
    expect(payload.source).toBe('soulsync_discovery');
    expect(payload.source_playlist_id).toBe('ssd_time_machine_1990s');
    expect(payload.description).toBe(
      'Personalized time_machine · 1990s — regenerates on Auto-Sync refresh.',
    );
    expect(payload.owner).toBe('SoulSync');
    expect(buildSsdMirrorPayload('hidden_gems', '', 'HG', []).description).toBe(
      'Personalized hidden_gems — regenerates on Auto-Sync refresh.',
    );
  });

  it('postSsdRefresh hits the variant or singleton refresh URL (144-149)', async () => {
    responder = () => ({ success: true });
    stubFetch();
    await postSsdRefresh('hidden_gems', '');
    await postSsdRefresh('time_machine', '1990s');
    expect(calls.map((c) => `${c.method} ${c.url}`)).toEqual([
      'POST /api/personalized/playlist/hidden_gems/refresh',
      'POST /api/personalized/playlist/time_machine/1990s/refresh',
    ]);
  });
});
