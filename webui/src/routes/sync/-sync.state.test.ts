/**
 * State reducers — fixture tests over the lifecycle the P0 read mapped:
 * fresh → discovering (frames) → discovered → syncing → sync_complete →
 * downloading → close-reset back to discovered. The reducers are transcription
 * of vanilla behaviour (file:line in each doc comment), pinned with literals.
 */

import { describe, expect, it } from 'vitest';

import { SYNC_SOURCES } from './-sync.sources';
import {
  applyDiscovery,
  applySyncStarted,
  applySyncStatus,
  freshSourceState,
  fromBackendState,
  resetAfterModalClose,
  syncPercent,
} from './-sync.state';

describe('freshSourceState', () => {
  it('keys the registry with the fake-hash prefix (bare for LB/beatport/youtube)', () => {
    expect(freshSourceState(SYNC_SOURCES.tidal, '77').fakeHash).toBe('tidal_77');
    expect(freshSourceState(SYNC_SOURCES.spotify_public, 'h4sh').fakeHash).toBe(
      'spotifypublic_h4sh',
    );
    expect(freshSourceState(SYNC_SOURCES.listenbrainz, 'mbid-1').fakeHash).toBe('mbid-1');
    const fresh = freshSourceState(SYNC_SOURCES.tidal, '77');
    expect(fresh.phase).toBe('fresh');
    expect(fresh.rows).toEqual([]);
    expect(fresh.spotifyTotal).toBe(0);
  });
});

describe('applyDiscovery', () => {
  const base = freshSourceState(SYNC_SOURCES.tidal, '77');

  it('applies a progress frame: rows transformed, counters updated, phase echoed', () => {
    const next = applyDiscovery(base, SYNC_SOURCES.tidal, {
      phase: 'discovering',
      progress: 40,
      spotify_matches: 2,
      spotify_total: 5,
      results: [
        { tidal_track: { name: 'A', artists: ['X'] }, spotify_data: { name: 'A2' } },
        { tidal_track: { name: 'B', artists: ['Y'] } },
      ],
    });
    expect(next.phase).toBe('discovering');
    expect(next.discoveryProgress).toBe(40);
    expect(next.spotifyMatches).toBe(2);
    expect(next.spotifyTotal).toBe(5);
    expect(next.rows.map((r) => r.status_class)).toEqual(['found', 'not-found']);
    expect(next.rawResults).toHaveLength(2);
  });

  it('completes on the complete flag OR the discovered phase (the LB poll rule)', () => {
    expect(applyDiscovery(base, SYNC_SOURCES.tidal, { complete: true }).phase).toBe('discovered');
    expect(applyDiscovery(base, SYNC_SOURCES.tidal, { phase: 'discovered' }).phase).toBe(
      'discovered',
    );
    // A payload without results keeps the rows it has.
    const withRows = applyDiscovery(base, SYNC_SOURCES.tidal, {
      results: [{ tidal_track: { name: 'A', artists: [] } }],
    });
    const completed = applyDiscovery(withRows, SYNC_SOURCES.tidal, { complete: true });
    expect(completed.rows).toHaveLength(1);
  });

  it('uses the Qobuz found-variant through the config', () => {
    const q = applyDiscovery(freshSourceState(SYNC_SOURCES.qobuz, '9'), SYNC_SOURCES.qobuz, {
      results: [{ qobuz_track: { name: 'A', artists: [] }, status: 'Found' }],
    });
    expect(q.rows[0].status_class).toBe('found');
  });
});

describe('sync lifecycle', () => {
  const discovered = {
    ...freshSourceState(SYNC_SOURCES.tidal, '77'),
    phase: 'discovered',
  };

  it('applySyncStarted captures either id spelling (beatport answers sync_id)', () => {
    expect(applySyncStarted(discovered, { sync_playlist_id: 'sp1' }).syncPlaylistId).toBe('sp1');
    expect(applySyncStarted(discovered, { sync_id: 'sp2' }).syncPlaylistId).toBe('sp2');
    expect(applySyncStarted(discovered, { sync_playlist_id: 'sp1' }).phase).toBe('syncing');
  });

  it('finished → sync_complete (capturing a late converted id); error/cancelled → REVERT to discovered', () => {
    const syncing = applySyncStarted(discovered, { sync_playlist_id: 'sp1' });
    const done = applySyncStatus(syncing, {
      status: 'finished',
      converted_spotify_playlist_id: 'tidal_77',
    });
    expect(done.phase).toBe('sync_complete');
    expect(done.convertedSpotifyPlaylistId).toBe('tidal_77');
    expect(applySyncStatus(syncing, { status: 'error' }).phase).toBe('discovered');
    expect(applySyncStatus(syncing, { sync_status: 'cancelled' }).phase).toBe('discovered');
  });

  it("the HTTP poll's boolean complete finishes too (tidal poll 1078 vs socket 'finished')", () => {
    const syncing = applySyncStarted(discovered, { sync_playlist_id: 'sp1' });
    expect(applySyncStatus(syncing, { complete: true }).phase).toBe('sync_complete');
  });

  it('in-flight progress lands in lastSyncProgress for the card re-render restore', () => {
    const syncing = applySyncStatus(discovered, {
      status: 'syncing',
      progress: { total_tracks: 10, matched_tracks: 4, failed_tracks: 1 },
    });
    expect(syncing.phase).toBe('syncing');
    expect(syncing.lastSyncProgress).toEqual({
      total_tracks: 10,
      matched_tracks: 4,
      failed_tracks: 1,
    });
    // An empty payload changes nothing.
    expect(applySyncStatus(syncing, {})).toBe(syncing);
  });

  it('syncPercent: processed vs matched formulas, total 0 guards', () => {
    const progress = { total_tracks: 10, matched_tracks: 4, failed_tracks: 1 };
    expect(syncPercent('processed', progress)).toBe(50);
    expect(syncPercent('matched', progress)).toBe(40);
    expect(syncPercent('processed', { total_tracks: 0 })).toBe(0);
    expect(syncPercent('processed', undefined)).toBe(0);
  });
});

describe('resetAfterModalClose (the seven blocks unified)', () => {
  it('preserves the discovery outcome, drops the download/sync linkage, lands on discovered', () => {
    const full = {
      ...freshSourceState(SYNC_SOURCES.deezer, '5'),
      phase: 'download_complete',
      playlist: { name: 'P' },
      rawResults: [{ deezer_track: { name: 'A', artists: [] } }],
      rows: [{ index: 0 } as never],
      discoveryProgress: 100,
      spotifyMatches: 3,
      spotifyTotal: 3,
      convertedSpotifyPlaylistId: 'deezer_5',
      downloadProcessId: 'batch-9',
      syncPlaylistId: 'sp1',
      lastSyncProgress: { total_tracks: 3 },
    };
    const reset = resetAfterModalClose(full);
    expect(reset.phase).toBe('discovered');
    expect(reset.downloadProcessId).toBeUndefined();
    expect(reset.syncPlaylistId).toBeUndefined();
    expect(reset.lastSyncProgress).toBeUndefined();
    // Preserved exactly as the vanilla blocks preserve them (10237-10455).
    expect(reset.playlist).toEqual({ name: 'P' });
    expect(reset.rawResults).toHaveLength(1);
    expect(reset.discoveryProgress).toBe(100);
    expect(reset.spotifyMatches).toBe(3);
    expect(reset.convertedSpotifyPlaylistId).toBe('deezer_5');
  });
});

describe('fromBackendState', () => {
  it('accepts snake, camel, and the mixed dual-key reality', () => {
    const state = fromBackendState(SYNC_SOURCES.tidal, '77', {
      phase: 'downloading',
      playlist: { name: 'P', tracks: [{}, {}] },
      discovery_results: [{ tidal_track: { name: 'A', artists: [] }, spotify_data: {} }],
      discovery_progress: 100,
      spotify_matches: 1,
      convertedSpotifyPlaylistId: 'tidal_77', // camel
      download_process_id: 'batch-1', // snake — the mixed reality (864-949)
    });
    expect(state.phase).toBe('downloading');
    expect(state.rows[0].status_class).toBe('found');
    expect(state.convertedSpotifyPlaylistId).toBe('tidal_77');
    expect(state.downloadProcessId).toBe('batch-1');
    // spotify_total absent → falls back to the playlist's track count.
    expect(state.spotifyTotal).toBe(2);
  });

  it('defaults an empty backend payload to a fresh shape', () => {
    const state = fromBackendState(SYNC_SOURCES.qobuz, '9', {});
    expect(state.phase).toBe('fresh');
    expect(state.rows).toEqual([]);
    expect(state.fakeHash).toBe('qobuz_9');
  });
});
