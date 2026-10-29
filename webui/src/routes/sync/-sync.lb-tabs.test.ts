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
  resolveLbMirrorTarget,
  postLbCacheRefresh,
  postSsdRefresh,
  soulsyncSyntheticId,
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

  it('fetchSsdRecords: every kind AND variant, in tab order and grouped, built rows merged in', async () => {
    responder = (url) =>
      url.includes('/kinds')
        ? {
            success: true,
            kinds: [
              { kind: 'hidden_gems', name_template: 'Hidden Gems', description: 'Obscure picks' },
              { kind: 'listening_mix', description: 'From what you play' },
              {
                kind: 'time_machine',
                name_template: 'Time Machine — {variant}',
                requires_variant: true,
                variants: ['1980s', '1990s'],
              },
              {
                kind: 'daily_mix',
                name_template: 'Daily Mix {variant}',
                requires_variant: true,
                variants: ['1', '2'],
              },
            ],
          }
        : {
            success: true,
            playlists: [
              { kind: 'listening_mix', variant: '', track_count: 30 },
              { kind: 'daily_mix', variant: '1', name: 'Daily Mix 1', track_count: 40 },
              { kind: 'daily_mix', variant: '6', name: 'Daily Mix 6', track_count: 40 },
            ],
          };
    stubFetch();
    const { records } = await fetchSsdRecords();
    // daily mixes first, then the singles, then the big variant families
    expect(records.map((r) => `${r.kind}|${r.variant}`)).toEqual([
      'daily_mix|1',
      'daily_mix|2',
      'daily_mix|6', // built yesterday, not listed today: kept in its family
      'listening_mix|',
      'hidden_gems|',
      'time_machine|1980s',
      'time_machine|1990s',
    ]);
    expect(records.map((r) => r.group)).toEqual([
      'Daily Mix',
      'Daily Mix',
      'Daily Mix',
      'Discovery playlists',
      'Discovery playlists',
      'Time Machine',
      'Time Machine',
    ]);
    expect(records[0]).toMatchObject({ track_count: 40, name: 'Daily Mix 1' });
    expect(records[0]._never_generated).toBeUndefined();
    expect(records[1]).toMatchObject({
      name: 'Daily Mix 2',
      track_count: 0,
      _never_generated: true,
    });
    expect(records[4]).toMatchObject({ name: 'Hidden Gems', description: 'Obscure picks' });
    expect(records[5]).toMatchObject({ name: 'Time Machine — 1980s', _never_generated: true });

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

describe('resolveLbMirrorTarget (10975-11004) — direct', () => {
  it('routes a Last.fm Radio PREFIX to lastfm, and only the prefix', () => {
    expect(resolveLbMirrorTarget('m', 'Last.fm Radio: Boards of Canada', undefined).source).toBe(
      'lastfm',
    );
    // Contains the word but is not the prefix — stays listenbrainz.
    expect(resolveLbMirrorTarget('m', 'Radio Ga Ga Mix', undefined).source).toBe('listenbrainz');
  });

  it('defaults the name when the title is empty (10975)', () => {
    expect(resolveLbMirrorTarget('m', '', undefined).name).toBe('ListenBrainz Playlist');
  });

  it('ignores series fields unless matched is true', () => {
    const unmatched = resolveLbMirrorTarget('m', 'Weekly Jams', undefined, {
      matched: false,
      series_id: 'ignored',
      canonical_name: 'Ignored',
    });
    expect(unmatched.sourcePlaylistId).toBe('m');
    expect(unmatched.name).toBe('Weekly Jams');
  });

  it('takes only the series fields it was actually sent', () => {
    const partial = resolveLbMirrorTarget('m', 'Weekly Jams for user', undefined, {
      matched: true,
      canonical_name: 'Weekly Jams',
    });
    expect(partial.name).toBe('Weekly Jams');
    expect(partial.sourcePlaylistId).toBe('m');
    expect(partial.source).toBe('listenbrainz');
  });

  it('owner: the creator wins, else a fallback chosen from the RESOLVED source', () => {
    expect(resolveLbMirrorTarget('m', 'Weekly Jams', 'troi').owner).toBe('troi');
    expect(resolveLbMirrorTarget('m', 'Weekly Jams', undefined).owner).toBe('ListenBrainz');
    expect(resolveLbMirrorTarget('m', 'Last.fm Radio: x', undefined).owner).toBe('Last.fm');
  });
});
