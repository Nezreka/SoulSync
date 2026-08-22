/**
 * The small-tab data layer — literal pins transcribed from
 * sync-listenbrainz.js / sync-lastfm.js / sync-soulsync-discovery.js
 * (file:line cited per describe), wire calls against a captured fetch.
 */

import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { afterEach, describe, expect, it, vi } from 'vitest';

import type { LbProgressInput, SsdTrack } from './-sync.lb-tabs';

import { extractFunction } from '../../test/vanilla-extract';
import {
  LB_EMPTY_MESSAGES,
  LB_SUB_TABS,
  buildLbMirrorTracks,
  buildSsdMirrorPayload,
  buildSsdMirrorTracks,
  fetchLastfmRadios,
  fetchLbCategories,
  fetchLbPlaylistTracks,
  fetchSsdRecords,
  lbCoverageCounts,
  mirrorLbAfterDiscovery,
  resolveLbMirrorTarget,
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

describe('the LB card numbers (_refreshOneLbSyncCard 253-283)', () => {
  /*
   * These were written against lbCardProgressLine, the vanilla's "♪ T / ✓ M /
   * ✗ F / P%" string. The cards render the shared coverage bar now, so that
   * string had no caller left and pinning it pinned dead code. The PARITY that
   * mattered was never the punctuation — it was the four numbers, which is
   * what these assert.
   */
  it('fresh hides; discovery counters carry the pct fallback', () => {
    expect(
      lbCoverageCounts({
        phase: 'fresh',
        spotifyTotal: 5,
        spotifyMatches: 1,
        discoveryProgress: 10,
      }),
    ).toBeNull();
    expect(
      lbCoverageCounts({
        phase: 'discovered',
        spotifyTotal: 10,
        spotifyMatches: 7,
        discoveryProgress: 0,
      }),
    ).toEqual({ total: 10, matched: 7, failed: 3, percentage: 70 });
    // Total 0 → the discovery progress stands in for the percent.
    expect(
      lbCoverageCounts({
        phase: 'discovering',
        spotifyTotal: 0,
        spotifyMatches: 0,
        discoveryProgress: 40,
      }),
    ).toEqual({ total: 0, matched: 0, failed: 0, percentage: 40 });
  });

  it('syncing reads lastSyncProgress; failed falls back to total-matched; percent is matched/total', () => {
    expect(
      lbCoverageCounts({
        phase: 'syncing',
        spotifyTotal: 99,
        spotifyMatches: 99,
        discoveryProgress: 99,
        lastSyncProgress: { total_tracks: 10, matched_tracks: 4 },
      }),
    ).toEqual({ total: 10, matched: 4, failed: 6, percentage: 40 });
    expect(
      lbCoverageCounts({
        phase: 'sync_complete',
        spotifyTotal: 0,
        spotifyMatches: 0,
        discoveryProgress: 0,
        lastSyncProgress: { total_tracks: 10, matched_tracks: 4, failed_tracks: 1 },
      }),
    ).toEqual({ total: 10, matched: 4, failed: 1, percentage: 40 });
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

/* ── The post-discovery LB mirror (differential vs 10928-11020) ───────────── */

describe('mirrorLbAfterDiscovery (differential vs _mirrorListenBrainzAfterDiscovery)', () => {
  const SYNC_SERVICES = readFileSync(resolve(process.cwd(), 'static/sync-services.js'), 'utf8');
  type MirrorVanilla = { _mirrorListenBrainzAfterDiscovery: (mbid: string) => Promise<void> };
  // eslint-disable-next-line @typescript-eslint/no-implied-eval
  const mirrorVanilla = new Function(
    `${extractFunction('_mirrorListenBrainzAfterDiscovery', SYNC_SERVICES)}
     return { _mirrorListenBrainzAfterDiscovery };`,
  )() as MirrorVanilla;

  interface MirrorCall {
    source: string;
    sourcePlaylistId: string;
    name: string;
    tracks: unknown[];
    meta: Record<string, unknown>;
  }

  /** Run the vanilla with its globals faked, and report what it mirrored. */
  async function runVanilla(
    mbid: string,
    state: Record<string, unknown> | undefined,
    series: unknown,
  ): Promise<MirrorCall | null> {
    let call: MirrorCall | null = null;
    const g = globalThis as Record<string, unknown>;
    g.listenbrainzPlaylistStates = state ? { [mbid]: state } : {};
    g.mirrorPlaylist = (
      source: string,
      sourcePlaylistId: string,
      name: string,
      tracks: unknown[],
      meta: Record<string, unknown>,
    ) => {
      call = { source, sourcePlaylistId, name, tracks, meta };
    };
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => new Response(JSON.stringify(series), { status: 200 })),
    );
    await mirrorVanilla._mirrorListenBrainzAfterDiscovery(mbid);
    return call;
  }

  /** Run the port and report the payload it POSTed to /api/mirror-playlist. */
  async function runPort(
    mbid: string,
    state: Record<string, unknown> | undefined,
    series: unknown,
  ): Promise<MirrorCall | null> {
    let body: Record<string, unknown> | null = null;
    vi.stubGlobal(
      'fetch',
      vi.fn(async (url: string, init?: RequestInit) => {
        if (String(url).startsWith('/api/mirror-playlist')) {
          body = JSON.parse(init?.body as string) as Record<string, unknown>;
          return new Response('{}', { status: 200 });
        }
        return new Response(JSON.stringify(series), { status: 200 });
      }),
    );
    await mirrorLbAfterDiscovery(
      mbid,
      state
        ? {
            playlist: state.playlist as Record<string, unknown>,
            rawResults: state.discovery_results as unknown[],
          }
        : undefined,
    );
    // Let the fire-and-forget POST settle.
    await new Promise((r) => setTimeout(r, 0));
    if (!body) return null;
    const b = body as Record<string, unknown>;
    return {
      source: b.source as string,
      sourcePlaylistId: b.source_playlist_id as string,
      name: b.name as string,
      tracks: b.tracks as unknown[],
      meta: {
        owner: b.owner,
        description: b.description,
        image_url: b.image_url,
      },
    };
  }

  const RESULTS = [
    // Object artists + object album with images — the fully-populated row.
    {
      confidence: 0.91,
      spotify_data: {
        id: 't1',
        name: 'Alright',
        artists: [{ name: 'Kendrick Lamar' }, { name: 'Pharrell' }],
        album: { name: 'To Pimp a Butterfly', images: [{ url: 'http://img/1' }] },
        duration_ms: 219000,
        source: 'spotify',
      },
    },
    // String artists array + string album + top-level image_url fallback.
    {
      spotify_data: {
        id: 't2',
        name: 'Redbone',
        artists: ['Childish Gambino'],
        album: 'Awaken, My Love!',
        image_url: 'http://img/2',
        duration_ms: 326000,
      },
    },
    // Bare-string artists, no album, no image at all.
    { spotify_data: { id: 't3', name: 'Nights', artists: 'Frank Ocean' } },
    // A NON-spotify provider — extra_data.provider follows spotify_data.source
    // (10957), it is not hardcoded.
    {
      confidence: 0.4,
      spotify_data: { id: 't4', name: 'Teardrop', artists: ['Massive Attack'], source: 'deezer' },
    },
    // Unmatched rows: no spotify_data, and spotify_data without an id.
    { spotify_track: 'unmatched' },
    { spotify_data: { name: 'no id here' } },
  ];

  const PLAYLIST = {
    name: 'Weekly Jams for user',
    tracks: [],
    description: '5 tracks from Weekly Jams for user',
    source: 'listenbrainz',
  };

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  const CASES: { label: string; state: Record<string, unknown>; series: unknown }[] = [
    {
      label: 'a plain LB playlist, no series match',
      state: { playlist: PLAYLIST, discovery_results: RESULTS },
      series: { matched: false },
    },
    {
      label: 'a matched rotating series (id + name + source all rewritten)',
      state: { playlist: PLAYLIST, discovery_results: RESULTS },
      series: {
        matched: true,
        source: 'listenbrainz',
        series_id: 'lb_weekly_jams_boulder',
        canonical_name: 'Weekly Jams',
      },
    },
    {
      label: 'a Last.fm Radio title routes to source lastfm',
      state: {
        playlist: { ...PLAYLIST, name: 'Last.fm Radio: Boards of Canada' },
        discovery_results: RESULTS,
      },
      series: { matched: false },
    },
    {
      label: 'a Last.fm Radio whose series match rewrites the source back',
      state: {
        playlist: { ...PLAYLIST, name: 'Last.fm Radio: Boards of Canada' },
        discovery_results: RESULTS,
      },
      series: { matched: true, series_id: 'lb_x' },
    },
    {
      label: 'a title merely CONTAINING Radio is not a Last.fm radio (10976)',
      state: {
        playlist: { ...PLAYLIST, name: 'Radio Ga Ga Mix' },
        discovery_results: RESULTS,
      },
      series: { matched: false },
    },
    {
      label: 'series fields are ignored unless matched is true (10990)',
      state: { playlist: PLAYLIST, discovery_results: RESULTS },
      series: {
        matched: false,
        series_id: 'lb_ignored',
        canonical_name: 'Ignored',
        source: 'lastfm',
      },
    },
    {
      label: 'a matched series rewrites a Last.fm radio back to listenbrainz (10991)',
      state: {
        playlist: { ...PLAYLIST, name: 'Last.fm Radio: Boards of Canada' },
        discovery_results: RESULTS,
      },
      series: {
        matched: true,
        source: 'listenbrainz',
        series_id: 'lb_series',
        canonical_name: 'Rolled Up',
      },
    },
    {
      label: 'a creator on the playlist wins over the owner fallback',
      state: {
        playlist: { ...PLAYLIST, creator: 'troi', image_url: 'http://cover' },
        discovery_results: RESULTS,
      },
      series: { matched: false },
    },
    {
      label: 'a partial series match keeps the fields it did not send',
      state: { playlist: PLAYLIST, discovery_results: RESULTS },
      series: { matched: true, canonical_name: 'Weekly Jams' },
    },
  ];

  it.each(CASES)('mirrors identically: $label', async ({ state, series }) => {
    const theirs = await runVanilla('mbid-1', state, series);
    const ours = await runPort('mbid-1', state, series);
    expect(theirs).not.toBeNull();
    expect(ours).toEqual(theirs);
  });

  const SKIPS: { label: string; state: Record<string, unknown> | undefined }[] = [
    { label: 'no state at all (10931)', state: undefined },
    { label: 'a state with no playlist (10931)', state: { discovery_results: RESULTS } },
    { label: 'no results (10935)', state: { playlist: PLAYLIST, discovery_results: [] } },
    {
      label: 'results but none matched (10964)',
      state: { playlist: PLAYLIST, discovery_results: [{ spotify_track: 'x' }] },
    },
  ];

  it.each(SKIPS)('mirrors nothing when there is $label', async ({ state }) => {
    expect(await runVanilla('mbid-1', state, { matched: false })).toBeNull();
    expect(await runPort('mbid-1', state, { matched: false })).toBeNull();
  });

  it('still mirrors when the series lookup fails outright (11000-11002)', async () => {
    let body: Record<string, unknown> | null = null;
    vi.stubGlobal(
      'fetch',
      vi.fn(async (url: string, init?: RequestInit) => {
        if (String(url).startsWith('/api/mirror-playlist')) {
          body = JSON.parse(init?.body as string) as Record<string, unknown>;
          return new Response('{}', { status: 200 });
        }
        throw new Error('series endpoint down');
      }),
    );
    await mirrorLbAfterDiscovery('mbid-1', {
      playlist: PLAYLIST,
      rawResults: RESULTS,
    });
    await new Promise((r) => setTimeout(r, 0));
    expect(body).not.toBeNull();
    // Falls through to the per-playlist mbid rather than dropping the mirror.
    expect((body as unknown as Record<string, unknown>).source_playlist_id).toBe('mbid-1');
  });

  it('sends only the matched rows, with extra_data as a JSON string (10955)', () => {
    const tracks = buildLbMirrorTracks(RESULTS);
    expect(tracks).toHaveLength(4);
    expect(JSON.parse(String(tracks[0].extra_data))).toEqual({
      discovered: true,
      provider: 'spotify',
      confidence: 0.91,
      matched_data: RESULTS[0].spotify_data,
    });
    // The confidence default is 1.0 when the row carries none (10958).
    expect(JSON.parse(String(tracks[1].extra_data)).confidence).toBe(1.0);
    // provider defaults to 'spotify' when spotify_data.source is absent (10957)
    // but FOLLOWS spotify_data.source when it carries one.
    expect(JSON.parse(String(tracks[1].extra_data)).provider).toBe('spotify');
    expect(JSON.parse(String(tracks[3].extra_data)).provider).toBe('deezer');
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

/** LbProgressInput requires all four counters; these tests each care about a
 *  couple at a time, so the rest default to the zeroes a fresh card carries. */
function lbInput(patch: Partial<LbProgressInput> & { phase: string }): LbProgressInput {
  return { spotifyTotal: 0, spotifyMatches: 0, discoveryProgress: 0, ...patch };
}

describe('lbCoverageCounts — the numbers behind the card bar', () => {
  it('sync phases use matched/total, NOT (matched+failed)/total like other sources', () => {
    // The parity that must not drift (sync-listenbrainz.js 271). Every other
    // vertical counts failures as processed; ListenBrainz does not, in either
    // phase, and the shared coverage bar renders whatever this returns.
    expect(
      lbCoverageCounts(
        lbInput({
          phase: 'syncing',
          lastSyncProgress: { total_tracks: 10, matched_tracks: 6, failed_tracks: 2 },
        }),
      ),
    ).toEqual({ total: 10, matched: 6, failed: 2, percentage: 60 });
  });

  it('falls back to total-matched when the sync payload omits failures', () => {
    expect(
      lbCoverageCounts(
        lbInput({
          phase: 'sync_complete',
          lastSyncProgress: { total_tracks: 8, matched_tracks: 5 },
        }),
      ),
    ).toEqual({ total: 8, matched: 5, failed: 3, percentage: 63 });
  });

  it('accepts the spotify_* aliases the payload sometimes uses instead', () => {
    expect(
      lbCoverageCounts(
        lbInput({
          phase: 'syncing',
          lastSyncProgress: { spotify_total: 4, spotify_matches: 1 },
        }),
      ),
    ).toMatchObject({ total: 4, matched: 1, percentage: 25 });
  });

  it('non-sync phases read the discovery counters', () => {
    expect(
      lbCoverageCounts(lbInput({ phase: 'discovered', spotifyTotal: 5, spotifyMatches: 4 })),
    ).toEqual({ total: 5, matched: 4, failed: 1, percentage: 80 });
  });

  it('a zero total falls back to the discovery progress, not to 0%', () => {
    expect(
      lbCoverageCounts(lbInput({ phase: 'discovering', spotifyTotal: 0, discoveryProgress: 42 })),
    ).toMatchObject({ total: 0, percentage: 42 });
  });

  it('fresh returns null so the card hides the element entirely', () => {
    expect(lbCoverageCounts(lbInput({ phase: 'fresh' }))).toBeNull();
  });
});
