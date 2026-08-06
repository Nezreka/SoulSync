/**
 * Api layer — every function is exercised against a captured fetch, pinning
 * URL, method and body as literals (the explorer api-test pattern). The
 * config-driven calls run with the real SYNC_SOURCES entries so the drift
 * table's paths are what actually goes on the wire.
 */

import { afterEach, describe, expect, it, vi } from 'vitest';

import {
  cancelSourceSync,
  deleteBeatportChart,
  deleteYouTubePlaylist,
  detectLbSeries,
  fetchAccountSyncStatus,
  fetchActiveProcesses,
  fetchDeezerArlPlaylists,
  fetchDeezerArlStatus,
  fetchDeezerLinkPlaylist,
  fetchExportConnectedSources,
  fetchMirroredPipelineStatus,
  fetchPlaylistExportStatus,
  fetchSourceDiscoveryStatus,
  clearMirroredDiscovery,
  deleteMirroredPlaylist,
  fetchMirroredPlaylists,
  fetchSourcePlaylists,
  patchMirroredCustomName,
  patchMirroredSourceRef,
  fetchSourcePlaylistsStates,
  fetchSourceState,
  fetchSourceSyncStatus,
  fetchSpotifyPlaylists,
  generatePlaylistM3u,
  parseITunesLinkUrl,
  parseSpotifyPublicUrl,
  parseYouTubeUrl,
  postMirrorPlaylist,
  postWishlistCleanup,
  postWishlistClear,
  runMirroredPipeline,
  startPlaylistExport,
  startSourceDiscovery,
  startSourceSync,
  updateSourcePhase,
} from './-sync.api';
import { buildMirrorPayload } from './-sync.import';
import { SYNC_SOURCES } from './-sync.sources';

interface Call {
  url: string;
  method: string;
  body: unknown;
  headers: Record<string, string> | undefined;
}

let calls: Call[] = [];

/** The pre-headers shape — for assertions that pin url/method/body exactly. */
function wire(c: Call): Omit<Call, 'headers'> {
  return { url: c.url, method: c.method, body: c.body };
}

function stubFetch(response: unknown = {}): void {
  calls = [];
  vi.stubGlobal(
    'fetch',
    vi.fn(async (url: string, init?: RequestInit) => {
      calls.push({
        url,
        method: init?.method ?? 'GET',
        body: init?.body ? JSON.parse(init.body as string) : undefined,
        headers: init?.headers as Record<string, string> | undefined,
      });
      return new Response(JSON.stringify(response));
    }),
  );
}

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe('config-driven vertical calls', () => {
  it('startSourceDiscovery sends nothing for a standard vertical', async () => {
    stubFetch({});
    await startSourceDiscovery(SYNC_SOURCES.tidal, '77');
    expect(calls.map(wire)).toEqual([
      { url: '/api/tidal/discovery/start/77', method: 'POST', body: undefined },
    ]);
  });

  it('startSourceDiscovery wraps the beatport chart and passes the LB playlist verbatim', async () => {
    stubFetch({});
    await startSourceDiscovery(SYNC_SOURCES.beatport, 'h4sh', { name: 'Top 100' });
    expect(wire(calls[0])).toEqual({
      url: '/api/beatport/discovery/start/h4sh',
      method: 'POST',
      body: { chart_data: { name: 'Top 100' } },
    });
    await startSourceDiscovery(SYNC_SOURCES.listenbrainz, 'mbid-1', { title: 'Weekly Jams' });
    expect(wire(calls[1])).toEqual({
      url: '/api/listenbrainz/discovery/start/mbid-1',
      method: 'POST',
      body: { title: 'Weekly Jams' },
    });
  });

  it('discovery/sync status + start + cancel hit the config paths', async () => {
    stubFetch({ phase: 'discovering' });
    await fetchSourceDiscoveryStatus(SYNC_SOURCES.qobuz, '9');
    await startSourceSync(SYNC_SOURCES.deezer, '5');
    await cancelSourceSync(SYNC_SOURCES.listenbrainz, 'mbid-2');
    await fetchSourceSyncStatus(SYNC_SOURCES.beatport, 'h');
    expect(calls.map((c) => `${c.method} ${c.url}`)).toEqual([
      'GET /api/qobuz/discovery/status/9',
      'POST /api/deezer/sync/start/5',
      // The borrowed youtube cancel — LB has none of its own.
      'POST /api/youtube/sync/cancel/mbid-2',
      'GET /api/beatport/sync/status/h',
    ]);
  });

  it('updateSourcePhase posts the phase with rider fields, hyphen drift included', async () => {
    stubFetch({});
    await updateSourcePhase(SYNC_SOURCES.beatport, 'h', { phase: 'fresh', reset: true });
    await updateSourcePhase(SYNC_SOURCES.tidal, '3', { phase: 'discovered' });
    expect(calls.map(wire)).toEqual([
      {
        url: '/api/beatport/charts/update-phase/h',
        method: 'POST',
        body: { phase: 'fresh', reset: true },
      },
      { url: '/api/tidal/update_phase/3', method: 'POST', body: { phase: 'discovered' } },
    ]);
  });

  it('fetchSourceState uses the state path and returns {} where none exists', async () => {
    stubFetch({ phase: 'discovered' });
    await fetchSourceState(SYNC_SOURCES.itunes_link, 'h');
    expect(calls[0].url).toBe('/api/itunes-link/state/h');
    const none = await fetchSourceState(
      { ...SYNC_SOURCES.tidal, api: { ...SYNC_SOURCES.tidal.api, state: null } },
      'x',
    );
    expect(none).toEqual({});
    expect(calls).toHaveLength(1); // no second wire call
  });

  it('fetchSourcePlaylistsStates reads the bulk endpoint or returns {}', async () => {
    stubFetch({ states: [] });
    await fetchSourcePlaylistsStates(SYNC_SOURCES.spotify_public);
    expect(calls[0].url).toBe('/api/spotify-public/playlists/states');
    const none = await fetchSourcePlaylistsStates({
      ...SYNC_SOURCES.youtube,
      api: { ...SYNC_SOURCES.youtube.api, playlistsStates: null },
    });
    expect(none).toEqual({});
  });
});

describe('page-level endpoints', () => {
  it('fetchActiveProcesses tolerates a failing endpoint', async () => {
    calls = [];
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => new Response('nope', { status: 500 })),
    );
    expect(await fetchActiveProcesses()).toEqual({ active_processes: [] });
  });

  it('playlist lists accept bare arrays and envelopes', async () => {
    stubFetch([{ id: 'a' }]);
    expect(await fetchSpotifyPlaylists()).toEqual([{ id: 'a' }]);
    stubFetch({ playlists: [{ id: 'b' }] });
    expect(await fetchDeezerArlPlaylists()).toEqual([{ id: 'b' }]);
    stubFetch({ playlists: [{ id: 'c' }] });
    expect(await fetchSourcePlaylists('tidal')).toEqual([{ id: 'c' }]);
    expect(calls[0].url).toBe('/api/tidal/playlists');
  });

  it('account sync status + arl status hit the account endpoints', async () => {
    stubFetch({ status: 'syncing' });
    await fetchAccountSyncStatus('deezer_arl_9');
    await fetchDeezerArlStatus();
    expect(calls.map((c) => c.url)).toEqual([
      '/api/sync/status/deezer_arl_9',
      '/api/deezer/arl-status',
    ]);
  });

  it('postMirrorPlaylist ships a buildMirrorPayload body verbatim', async () => {
    stubFetch({ success: true });
    const payload = buildMirrorPayload('tidal', 5, 'List', [{ name: 'X', artists: ['A'] }]);
    await postMirrorPlaylist(payload);
    expect(wire(calls[0])).toEqual({ url: '/api/mirror-playlist', method: 'POST', body: payload });
  });

  it('generatePlaylistM3u posts the documented body shape', async () => {
    stubFetch({});
    await generatePlaylistM3u({
      playlist_name: 'P',
      tracks: [{ name: 'X', artist: 'A', duration_ms: 1000 }],
      context_type: 'playlist',
      save_to_disk: true,
    });
    expect(calls[0].url).toBe('/api/generate-playlist-m3u');
    expect(calls[0].method).toBe('POST');
    expect(calls[0].body).toEqual({
      playlist_name: 'P',
      tracks: [{ name: 'X', artist: 'A', duration_ms: 1000 }],
      context_type: 'playlist',
      save_to_disk: true,
    });
  });

  it('wishlist maintenance + LB series detection', async () => {
    stubFetch({});
    await postWishlistCleanup();
    await postWishlistClear();
    await detectLbSeries('Weekly Jams — week of 2026');
    expect(calls.map((c) => `${c.method} ${c.url}`)).toEqual([
      'POST /api/wishlist/cleanup',
      'POST /api/wishlist/clear',
      'GET /api/listenbrainz/series-detect?title=Weekly%20Jams%20%E2%80%94%20week%20of%202026',
    ]);
  });
});

describe('parse + delete endpoints', () => {
  it('the three URL parsers post {url}', async () => {
    stubFetch({ url_hash: 'h' });
    await parseYouTubeUrl('https://youtube.com/playlist?list=x');
    await parseSpotifyPublicUrl('https://open.spotify.com/playlist/abc');
    await parseITunesLinkUrl('https://music.apple.com/us/album/x/1');
    expect(calls.map((c) => `${c.method} ${c.url}`)).toEqual([
      'POST /api/youtube/parse',
      'POST /api/spotify/parse-public',
      'POST /api/itunes-link/parse',
    ]);
    expect(calls[0].body).toEqual({ url: 'https://youtube.com/playlist?list=x' });
  });

  it('deezer link fetch + the two deletes', async () => {
    stubFetch({});
    await fetchDeezerLinkPlaylist('908622995');
    await deleteYouTubePlaylist('h4sh');
    await deleteBeatportChart('ch4rt');
    expect(calls.map((c) => `${c.method} ${c.url}`)).toEqual([
      'GET /api/deezer/playlist/908622995',
      'DELETE /api/youtube/delete/h4sh',
      'DELETE /api/beatport/charts/delete/ch4rt',
    ]);
  });
});

describe('JSON bodies carry Content-Type', () => {
  // Flask's request.get_json() yields nothing without this header, so a
  // missing one makes the LB discovery start 400 with 'Playlist data
  // required' (web_server.py 34966-34970). The old helper recorded only
  // {url, method, body}, so every header was unpinned.
  it('every POST that sends a body sets application/json', async () => {
    stubFetch({});
    await startSourceDiscovery(SYNC_SOURCES.listenbrainz, 'mbid-1', {
      playlist: { name: 'X', tracks: [] },
    });
    await startSourceDiscovery(SYNC_SOURCES.beatport, 'ch4rt', { name: 'c' });
    await updateSourcePhase(SYNC_SOURCES.tidal, '9', { phase: 'discovered' });
    await postMirrorPlaylist(buildMirrorPayload('tidal', 5, 'L', []));
    for (const call of calls) {
      expect(call.body, `${call.url} sent a body`).toBeDefined();
      expect(call.headers, `${call.url} must set Content-Type`).toMatchObject({
        'Content-Type': 'application/json',
      });
    }
  });

  it('a bodyless POST sends no Content-Type (the vanilla sends none either)', async () => {
    stubFetch({});
    await startSourceSync(SYNC_SOURCES.tidal, '9');
    expect(calls[0].body).toBeUndefined();
    expect(calls[0].headers).toBeUndefined();
  });
});

describe('mirrored playlist endpoints', () => {
  it('the list unwraps an array and throws the backend error otherwise (500-524)', async () => {
    stubFetch([{ id: 1, name: 'M' }]);
    await expect(fetchMirroredPlaylists()).resolves.toEqual([{ id: 1, name: 'M' }]);
    expect(calls[0]).toMatchObject({ url: '/api/mirrored-playlists', method: 'GET' });
    // the vanilla does `if (playlists.error) throw` on the parsed body (508)
    stubFetch({ error: 'nope' });
    await expect(fetchMirroredPlaylists()).rejects.toThrow('nope');
  });

  it('clear-discovery POSTs to the per-playlist path (1175)', async () => {
    stubFetch({ success: true, cleared: 12 });
    await expect(clearMirroredDiscovery(7)).resolves.toEqual({ success: true, cleared: 12 });
    expect(calls[0]).toMatchObject({
      url: '/api/mirrored-playlists/7/clear-discovery',
      method: 'POST',
    });
  });

  it('delete uses DELETE on the bare resource (2023)', async () => {
    stubFetch({ success: true });
    await deleteMirroredPlaylist(7);
    expect(calls[0]).toMatchObject({ url: '/api/mirrored-playlists/7', method: 'DELETE' });
  });

  it('rename PATCHes custom_name with a JSON header, and throws on !ok (auto-sync.js 2389)', async () => {
    stubFetch({});
    await patchMirroredCustomName(7, 'My Alias');
    expect(calls[0]).toMatchObject({
      url: '/api/mirrored-playlists/7/custom-name',
      method: 'PATCH',
      body: { custom_name: 'My Alias' },
      headers: { 'Content-Type': 'application/json' },
    });
    // a blank name clears the alias — it must still be sent
    stubFetch({});
    await patchMirroredCustomName(7, '');
    expect(calls[0].body).toEqual({ custom_name: '' });

    // The two guard arms, isolated. A 409 WITH an error body fires both, so
    // it proves neither on its own.
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => new Response(JSON.stringify({ error: 'taken' }), { status: 409 })),
    );
    await expect(patchMirroredCustomName(7, 'X')).rejects.toThrow('taken');
    // 200 + an error body — only the data.error arm can catch this.
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => new Response(JSON.stringify({ error: 'soft' }))),
    );
    await expect(patchMirroredCustomName(7, 'X')).rejects.toThrow('soft');
    // non-ok with NO error body — only the !response.ok arm catches it, and
    // the message falls back (auto-sync.js 2396).
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => new Response(JSON.stringify({}), { status: 500 })),
    );
    await expect(patchMirroredCustomName(7, 'X')).rejects.toThrow('Failed to update name');
  });
});

describe('Auto-Sync pipeline endpoints (auto-sync.js 2467-2497)', () => {
  it('run POSTs an empty JSON body and returns the state', async () => {
    stubFetch({ state: { status: 'running', progress: 0 } });
    await expect(runMirroredPipeline(7)).resolves.toEqual({
      state: { status: 'running', progress: 0 },
    });
    expect(calls[0]).toMatchObject({
      url: '/api/mirrored-playlists/7/pipeline/run',
      method: 'POST',
      body: {},
      headers: { 'Content-Type': 'application/json' },
    });
  });

  it('status GETs, and the whole body IS the state', async () => {
    stubFetch({ status: 'finished', progress: 100 });
    await expect(fetchMirroredPipelineStatus(7)).resolves.toEqual({
      status: 'finished',
      progress: 100,
    });
    expect(calls[0]).toMatchObject({
      url: '/api/mirrored-playlists/7/pipeline/status',
      method: 'GET',
    });
  });

  it('an EMPTY body is accepted — the vanilla reads text before parsing', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => new Response('')),
    );
    await expect(fetchMirroredPipelineStatus(7)).resolves.toEqual({});
  });

  it('a 404 with an unparseable body blames a stale server', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => new Response('<!doctype html>', { status: 404 })),
    );
    await expect(runMirroredPipeline(7)).rejects.toThrow(
      'Auto-Sync endpoint not found. Restart the SoulSync server so the new backend routes load.',
    );
  });

  it('each endpoint carries its own fallback message', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => new Response('{}', { status: 500 })),
    );
    await expect(runMirroredPipeline(7)).rejects.toThrow('Failed to start Auto-Sync');
    await expect(fetchMirroredPipelineStatus(7)).rejects.toThrow('Failed to read Auto-Sync status');
  });

  it('a 200 body carrying an error still throws it', async () => {
    stubFetch({ error: 'no source ref' });
    await expect(runMirroredPipeline(7)).rejects.toThrow('no source ref');
  });
});

describe('the source-ref PATCH (auto-sync.js 2423-2432)', () => {
  it('sends the trimmed ref with a JSON header', async () => {
    stubFetch({});
    await patchMirroredSourceRef(7, 'https://open.spotify.com/playlist/x');
    expect(calls[0]).toMatchObject({
      url: '/api/mirrored-playlists/7/source-ref',
      method: 'PATCH',
      body: { source_ref: 'https://open.spotify.com/playlist/x' },
      headers: { 'Content-Type': 'application/json' },
    });
  });

  it('throws on either guard arm, with the backend message when there is one', async () => {
    // 200 + an error body — only the data.error arm catches this.
    stubFetch({ error: 'not a playlist url' });
    await expect(patchMirroredSourceRef(7, 'x')).rejects.toThrow('not a playlist url');
    // non-ok with NO error body — only the !response.ok arm, message falls back.
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => new Response('{}', { status: 500 })),
    );
    await expect(patchMirroredSourceRef(7, 'x')).rejects.toThrow(
      'Failed to update source reference',
    );
  });
});

describe('playlist export endpoints (#903, 715-760)', () => {
  it('the connection probe returns the connected list, and null when it fails', async () => {
    stubFetch({ connected: ['spotify', 'deezer'] });
    await expect(fetchExportConnectedSources()).resolves.toEqual(['spotify', 'deezer']);
    expect(calls[0]).toMatchObject({ url: '/api/discover/your-albums/sources', method: 'GET' });
    // A body with no `connected` key is an empty list, NOT a failed probe.
    stubFetch({});
    await expect(fetchExportConnectedSources()).resolves.toEqual([]);
    // A thrown fetch is the vanilla's `.catch(() => {})` — null gates nothing.
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => {
        throw new Error('offline');
      }),
    );
    await expect(fetchExportConnectedSources()).resolves.toBeNull();
  });

  it('spotify/deezer POST the service endpoint with {backfill}', async () => {
    stubFetch({ success: true, job_id: 'j1' });
    await expect(startPlaylistExport(7, 'spotify', true)).resolves.toEqual({
      success: true,
      job_id: 'j1',
    });
    expect(calls[0]).toMatchObject({
      url: '/api/playlists/7/export/service/spotify',
      method: 'POST',
      body: { backfill: true },
      headers: { 'Content-Type': 'application/json' },
    });
    stubFetch({});
    await startPlaylistExport(7, 'deezer', false);
    expect(wire(calls[0])).toEqual({
      url: '/api/playlists/7/export/service/deezer',
      method: 'POST',
      body: { backfill: false },
    });
  });

  it('push/download POST the ListenBrainz endpoint with {mode} — backfill never rides along', async () => {
    stubFetch({});
    await startPlaylistExport(7, 'push', true);
    expect(wire(calls[0])).toEqual({
      url: '/api/playlists/7/export/listenbrainz',
      method: 'POST',
      body: { mode: 'push' },
    });
    stubFetch({});
    await startPlaylistExport(7, 'download', false);
    expect(wire(calls[0])).toEqual({
      url: '/api/playlists/7/export/listenbrainz',
      method: 'POST',
      body: { mode: 'download' },
    });
  });

  it('the status poll GETs the job by id', async () => {
    stubFetch({ job: { phase: 'done' } });
    await expect(fetchPlaylistExportStatus('j1')).resolves.toEqual({ job: { phase: 'done' } });
    expect(calls[0]).toMatchObject({ url: '/api/playlists/export/status/j1', method: 'GET' });
  });
});
