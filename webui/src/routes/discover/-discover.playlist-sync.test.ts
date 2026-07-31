import { describe, expect, it } from 'vitest';

import {
  PLAYLIST_DISPLAY_NAMES,
  RESUMABLE_SYNCS,
  SYNC_BUTTON_IDLE,
  SYNC_BUTTON_RUNNING,
  SYNC_POLL_MS,
  SYNC_STATUS_HIDE_MS,
  SYNC_TRACK_SOURCES,
  noTracksToast,
  playlistDisplayName,
  resumeStatusUrl,
  syncBubbleImage,
  syncButtonId,
  syncButtonState,
  syncCompleteToast,
  syncCompletedId,
  syncIdPrefix,
  syncIsActive,
  syncIsFinished,
  syncPollAlwaysRuns,
  syncProgress,
  syncStatusId,
  toSyncTracks,
  virtualPlaylistId,
  PLAYLIST_DOWNLOAD_NO_TRACKS,
  canOpenPlaylistDownload,
  playlistDownloadFailed,
} from './-discover.playlist-sync';

describe('the eight playlist types', () => {
  it('each has a track source', () => {
    expect(Object.keys(SYNC_TRACK_SOURCES)).toHaveLength(8);
  });

  it('DIVERGENCE: each also has a display name — the vanilla omits one', () => {
    // The vanilla's map (duplicated at 11396 and 11459) lists seven, so
    // finishing a listening_mix sync toasts the raw key:
    // "listening_mix sync complete!".
    for (const type of Object.keys(SYNC_TRACK_SOURCES)) {
      expect(PLAYLIST_DISPLAY_NAMES[type as keyof typeof PLAYLIST_DISPLAY_NAMES]).toBeTruthy();
    }
    expect(PLAYLIST_DISPLAY_NAMES.listening_mix).toBe('Your Listening Mix');
    expect(syncCompleteToast('listening_mix')).toBe('Your Listening Mix sync complete!');
  });

  it('keeps the other seven names verbatim', () => {
    expect(playlistDisplayName('release_radar')).toBe('Fresh Tape');
    expect(playlistDisplayName('discovery_weekly')).toBe('The Archives');
    expect(playlistDisplayName('seasonal_playlist')).toBe('Seasonal Mix');
    expect(playlistDisplayName('popular_picks')).toBe('Popular Picks');
    expect(playlistDisplayName('hidden_gems')).toBe('Hidden Gems');
    expect(playlistDisplayName('discovery_shuffle')).toBe('Discovery Shuffle');
    expect(playlistDisplayName('build_playlist')).toBe('Custom Playlist');
  });

  it('falls back to the raw key for an unknown type', () => {
    expect(playlistDisplayName('nonsense')).toBe('nonsense');
    expect(syncCompleteToast('nonsense')).toBe('nonsense sync complete!');
  });

  it('names the playlist in the no-tracks warning', () => {
    expect(noTracksToast('Fresh Tape')).toBe('No tracks available for Fresh Tape');
  });
});

describe('the id convention', () => {
  it('turns underscores into hyphens', () => {
    // Shared with mixStatusBase, which is what lets a running sync's progress
    // land on the mix modal's elements.
    expect(syncIdPrefix('release_radar')).toBe('release-radar');
    expect(syncIdPrefix('seasonal_playlist')).toBe('seasonal-playlist');
  });

  it('replaces EVERY underscore, not just the first', () => {
    expect(syncIdPrefix('a_b_c')).toBe('a-b-c');
  });

  it('builds the element ids', () => {
    expect(syncStatusId('release_radar')).toBe('release-radar-sync-status');
    expect(syncButtonId('release_radar')).toBe('release-radar-sync-btn');
    expect(syncCompletedId('hidden_gems')).toBe('hidden-gems-sync-completed');
  });

  it('prefixes the virtual playlist id with discover_, keeping underscores', () => {
    // The DOM ids hyphenate; the playlist id does NOT.
    expect(virtualPlaylistId('release_radar')).toBe('discover_release_radar');
  });
});

describe('converting tracks for the sync API', () => {
  it('uses track_data_json whole when present', () => {
    const out = toSyncTracks([
      { track_data_json: { id: 'x', name: 'J', artists: [{ name: 'A' }], duration_ms: 5 } },
    ]);
    expect(out[0]).toMatchObject({ id: 'x', name: 'J', duration_ms: 5 });
  });

  it('builds a minimal object from the flat columns otherwise', () => {
    const out = toSyncTracks([
      {
        spotify_track_id: 't1',
        track_name: 'Xtal',
        artist_name: 'Aphex Twin',
        album_name: 'SAW',
        album_cover_url: '/c.jpg',
        duration_ms: 300000,
      },
    ]);
    expect(out[0]).toEqual({
      id: 't1',
      name: 'Xtal',
      artists: ['Aphex Twin'],
      album: { name: 'SAW', images: [{ url: '/c.jpg' }] },
      duration_ms: 300000,
    });
  });

  it('FLATTENS artists to strings, which the matcher requires', () => {
    // An array of objects silently matches nothing.
    const out = toSyncTracks([{ track_data_json: { artists: [{ name: 'A' }, { name: 'B' }] } }]);
    expect(out[0].artists).toEqual(['A', 'B']);
  });

  it('leaves already-flat artist strings alone', () => {
    expect(toSyncTracks([{ track_data_json: { artists: ['A'] } }])[0].artists).toEqual(['A']);
  });

  it('gives an art-less track an EMPTY image array, not a null entry', () => {
    const out = toSyncTracks([{ track_name: 'n' }]);
    expect(out[0].album?.images).toEqual([]);
  });

  it('defaults a missing duration to zero', () => {
    expect(toSyncTracks([{ track_name: 'n' }])[0].duration_ms).toBe(0);
  });

  it('does not mutate the caller’s track_data_json', () => {
    const json = { artists: [{ name: 'A' }] };
    toSyncTracks([{ track_data_json: json }]);
    expect(json.artists).toEqual([{ name: 'A' }]);
  });
});

describe('the download-bar bubble art', () => {
  it('comes from the FIRST track only', () => {
    expect(
      syncBubbleImage([
        { album: { images: [{ url: '/first.jpg' }] } },
        { album: { images: [{ url: '/second.jpg' }] } },
      ]),
    ).toBe('/first.jpg');
  });

  it('is null when the first track has no art, without checking the rest', () => {
    expect(
      syncBubbleImage([{ album: { images: [] } }, { album: { images: [{ url: '/x' }] } }]),
    ).toBeNull();
    expect(syncBubbleImage([{}])).toBeNull();
    expect(syncBubbleImage([])).toBeNull();
  });
});

describe('progress arithmetic', () => {
  it('counts failed tracks as PROCESSED, not pending', () => {
    // A failed track is finished. Counting it as pending makes a sync where
    // everything fails sit at 0% forever.
    const p = syncProgress({ total_tracks: 10, matched_tracks: 6, failed_tracks: 4 });
    expect([p.processed, p.pending, p.percentage]).toEqual([10, 0, 100]);
  });

  it('reports a partial sync', () => {
    const p = syncProgress({ total_tracks: 10, matched_tracks: 2, failed_tracks: 1 });
    expect([p.processed, p.pending, p.percentage]).toEqual([3, 7, 30]);
  });

  it('rounds rather than truncating', () => {
    expect(syncProgress({ total_tracks: 3, matched_tracks: 2 }).percentage).toBe(67);
  });

  it('avoids dividing by zero before the total is known', () => {
    expect(syncProgress({}).percentage).toBe(0);
    expect(syncProgress(undefined).percentage).toBe(0);
    expect(syncProgress({ total_tracks: 0, matched_tracks: 0 }).pending).toBe(0);
  });

  it('ends only on "finished"', () => {
    expect(syncIsFinished('finished')).toBe(true);
    expect(syncIsFinished('complete')).toBe(false); //  the download bar's word, not this one's
    expect(syncIsFinished('syncing')).toBe(false);
    expect(syncIsFinished(undefined)).toBe(false);
  });

  it('polls twice a second and hides the status three seconds after', () => {
    expect(SYNC_POLL_MS).toBe(500);
    expect(SYNC_STATUS_HIDE_MS).toBe(3000);
  });

  it('polls even when the socket is connected, unlike the download bar', () => {
    // There are no dedicated websocket events for discovery progress (11410),
    // so the socket handler is an accelerator and the poll is the source of
    // truth. Gating this would freeze the numbers for socket users.
    expect(syncPollAlwaysRuns()).toBe(true);
  });
});

describe('resuming after a refresh', () => {
  it('probes exactly the three syncs the vanilla probes', () => {
    expect(RESUMABLE_SYNCS).toEqual(['release_radar', 'discovery_weekly', 'seasonal_playlist']);
  });

  it('builds the discover_-prefixed status url', () => {
    expect(resumeStatusUrl('release_radar')).toBe('/api/sync/status/discover_release_radar');
  });

  it('treats "starting" as active, not just "syncing"', () => {
    // 'starting' is the first state a sync reports; missing it means a sync
    // begun seconds before a reload is never resumed.
    expect(syncIsActive('starting')).toBe(true);
    expect(syncIsActive('syncing')).toBe(true);
    expect(syncIsActive('finished')).toBe(false);
    expect(syncIsActive(undefined)).toBe(false);
  });

  it('moves all three button styles together', () => {
    expect(syncButtonState(true)).toEqual(SYNC_BUTTON_RUNNING);
    expect(syncButtonState(false)).toEqual(SYNC_BUTTON_IDLE);
    expect(SYNC_BUTTON_RUNNING.cursor).toBe('not-allowed');
    expect(SYNC_BUTTON_IDLE.cursor).toBe('pointer');
    expect(SYNC_BUTTON_RUNNING.disabled).toBe(true);
    expect(SYNC_BUTTON_IDLE.disabled).toBe(false);
  });
});

describe('the whole-playlist download action', () => {
  it('shares the sync path’s guard and warning', () => {
    // An empty mix is nothing to do, not an error.
    expect(canOpenPlaylistDownload([{}])).toBe(true);
    expect(canOpenPlaylistDownload([])).toBe(false);
    expect(canOpenPlaylistDownload(null)).toBe(false);
    expect(PLAYLIST_DOWNLOAD_NO_TRACKS('Fresh Tape')).toBe('No tracks available for Fresh Tape');
  });

  it('reports a failure with the underlying message', () => {
    expect(playlistDownloadFailed('boom')).toBe('Failed to open download modal: boom');
  });

  it('addresses the SAME virtual playlist id as the sync', () => {
    // The download modal and the sync both address one playlist, and the
    // download bar keys its bubble on that id.
    expect(virtualPlaylistId('release_radar')).toBe('discover_release_radar');
  });
});
