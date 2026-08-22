/**
 * The decisions sync history makes before anything is drawn.
 *
 * The re-sync branch is the one worth guarding hardest: both paths "succeed",
 * so picking wrong does the wrong thing silently rather than throwing.
 */

import { describe, expect, it } from 'vitest';

import {
  SYNC_HISTORY_DOWNLOAD_SOURCES,
  SYNC_HISTORY_SERVER_SOURCES,
  SYNC_HISTORY_SOURCE_ICONS,
  SYNC_HISTORY_SOURCE_LABELS,
  type SyncHistoryEntry,
  syncHistoryMeta,
  syncHistoryPageCount,
  syncHistoryProgress,
  syncHistoryResyncKind,
  syncHistoryResyncTracks,
  syncHistorySourceIcon,
  syncHistorySourceLabel,
  syncHistorySourceTabs,
  syncHistoryStats,
  syncHistoryVisibleEntries,
} from './-sync.history';

function entry(over: Partial<SyncHistoryEntry> = {}): SyncHistoryEntry {
  return { id: 1, playlist_name: 'Road Trip', source: 'spotify', ...over };
}

describe('which entries belong on this screen', () => {
  it('keeps playlist syncs and drops album, wishlist and redownload rows', () => {
    const rows = [
      entry({ id: 1, sync_type: 'playlist' }),
      entry({ id: 2, sync_type: 'album' }),
      entry({ id: 3, sync_type: 'wishlist' }),
    ];
    expect(syncHistoryVisibleEntries(rows).map((r) => r.id)).toEqual([1]);
  });

  it('keeps a row with NO sync_type — older rows predate the column', () => {
    // Dropping these would silently shorten everyone's history.
    expect(syncHistoryVisibleEntries([entry({ id: 9, sync_type: null })])).toHaveLength(1);
  });

  it('survives a missing entries array', () => {
    expect(syncHistoryVisibleEntries(null)).toEqual([]);
  });
});

describe('the source tabs', () => {
  it('puts All first and sorts the rest busiest-first', () => {
    const tabs = syncHistorySourceTabs({ tidal: 2, spotify: 10, deezer: 5 }, null);
    expect(tabs.map((t) => t.source)).toEqual([null, 'spotify', 'deezer', 'tidal']);
  });

  it('All counts the STATS, not the filtered page total', () => {
    // The page total moves with the active filter, so a strip built from it
    // would report on itself as you clicked around.
    const tabs = syncHistorySourceTabs({ spotify: 10, tidal: 2 }, 'tidal');
    expect(tabs[0]).toMatchObject({ source: null, count: 12, active: false });
  });

  it('marks exactly one tab active', () => {
    const tabs = syncHistorySourceTabs({ spotify: 1, tidal: 1 }, 'spotify');
    expect(tabs.filter((t) => t.active).map((t) => t.source)).toEqual(['spotify']);
  });

  it('gives an unknown source its raw id rather than blanking the tab', () => {
    expect(syncHistorySourceTabs({ napster: 3 }, null)[1].label).toBe('napster');
  });

  it('handles no stats at all', () => {
    expect(syncHistorySourceTabs(undefined, null)).toEqual([
      { source: null, label: 'All', count: 0, active: true },
    ]);
  });

  it('every labelled source has an icon, so no tab is left without a mark', () => {
    for (const source of Object.keys(SYNC_HISTORY_SOURCE_LABELS)) {
      expect(SYNC_HISTORY_SOURCE_ICONS[source]).toBeTruthy();
    }
  });

  it('names a known source and falls back to the raw id', () => {
    expect(syncHistorySourceLabel('spotify_public')).toBe('Spotify Public');
    expect(syncHistorySourceLabel('napster')).toBe('napster');
  });

  it('falls back to an inbox mark for a source with no icon', () => {
    expect(syncHistorySourceIcon('napster')).toBe('\u{1F4E5}');
    expect(syncHistorySourceIcon(null)).toBe('\u{1F4E5}');
  });
});

describe('the chips on a row', () => {
  it('says "In progress" for a run with no completed_at, not a row of zeroes', () => {
    expect(syncHistoryStats(entry())).toEqual([{ kind: 'pending', label: 'In progress' }]);
  });

  it('reports found, downloaded and failed when there are any', () => {
    const stats = syncHistoryStats(
      entry({ completed_at: 'x', tracks_found: 12, tracks_downloaded: 3, tracks_failed: 1 }),
    );
    expect(stats.map((s) => s.label)).toEqual(['12 found', '3 downloaded', '1 failed']);
  });

  it('omits a zero rather than printing "0 failed" on a clean run', () => {
    const stats = syncHistoryStats(entry({ completed_at: 'x', tracks_found: 4, tracks_failed: 0 }));
    expect(stats.map((s) => s.kind)).toEqual(['found']);
  });

  it('falls back to the library count when a finished run reported nothing', () => {
    // "0 found" on an already-complete playlist reads as a failure; it was a
    // no-op.
    expect(syncHistoryStats(entry({ completed_at: 'x', total_tracks: 140 }))[0].label).toBe(
      '140 in library',
    );
  });
});

describe('the meta line', () => {
  it('joins artist and album', () => {
    expect(syncHistoryMeta(entry({ artist_name: 'Air', album_name: 'Moon Safari' }))).toBe(
      'Air — Moon Safari',
    );
  });

  it('uses whichever half exists', () => {
    expect(syncHistoryMeta(entry({ artist_name: 'Air' }))).toBe('Air');
  });

  it('falls back to the sync type when there is neither', () => {
    expect(syncHistoryMeta(entry({ sync_type: 'playlist' }))).toBe('playlist');
  });
});

describe('pagination', () => {
  it('rounds a partial page up', () => {
    expect(syncHistoryPageCount(41, 20)).toBe(3);
  });

  it('is 0 for an empty history, so no strip is drawn', () => {
    expect(syncHistoryPageCount(0, 20)).toBe(0);
  });

  it('refuses to divide by a zero limit', () => {
    expect(syncHistoryPageCount(41, 0)).toBe(0);
  });
});

describe('which re-sync path an entry takes', () => {
  it('sends a Discover row to the download modal', () => {
    expect(syncHistoryResyncKind(entry({ source: 'discover' }))).toBe('download');
  });

  it('sends a Spotify row to the server sync', () => {
    expect(syncHistoryResyncKind(entry({ source: 'spotify' }))).toBe('server');
  });

  it('is_album_download OVERRIDES a server source', () => {
    // Otherwise a Spotify album download starts a playlist sync against an
    // album's tracks — which succeeds, at the wrong thing.
    expect(syncHistoryResyncKind(entry({ source: 'spotify', is_album_download: true }))).toBe(
      'download',
    );
  });

  it('sends an UNRECOGNISED source to the server path, as the vanilla always has', () => {
    // The vanilla computed isServerSync and never branched on it, so anything
    // not in the download set fell through here. Narrowing it would break
    // re-sync for every source added since.
    expect(syncHistoryResyncKind(entry({ source: 'napster' }))).toBe('server');
    expect(syncHistoryResyncKind(entry({ source: null }))).toBe('server');
  });

  it('the two source sets do not overlap', () => {
    for (const source of SYNC_HISTORY_DOWNLOAD_SOURCES) {
      expect(SYNC_HISTORY_SERVER_SOURCES.has(source)).toBe(false);
    }
  });
});

describe('flattening stored tracks for /api/sync/start', () => {
  it('reads artists stored as objects', () => {
    const [t] = syncHistoryResyncTracks([{ name: 'A', artists: [{ name: 'Air' }] }]);
    expect(t.artists).toEqual(['Air']);
  });

  it('reads artists stored as plain strings', () => {
    expect(syncHistoryResyncTracks([{ artists: ['Air', 'Boards'] }])[0].artists).toEqual([
      'Air',
      'Boards',
    ]);
  });

  it('wraps a bare non-array artist into a list', () => {
    expect(syncHistoryResyncTracks([{ artists: 'Air' }])[0].artists).toEqual(['Air']);
  });

  it('names an unknown artist rather than sending an empty list', () => {
    expect(syncHistoryResyncTracks([{ name: 'A' }])[0].artists).toEqual(['Unknown Artist']);
  });

  it('reads an album stored as an object and as a string', () => {
    expect(syncHistoryResyncTracks([{ album: { name: 'Moon Safari' } }])[0].album).toBe(
      'Moon Safari',
    );
    expect(syncHistoryResyncTracks([{ album: 'Moon Safari' }])[0].album).toBe('Moon Safari');
  });

  it('defaults the numbers rather than sending null through', () => {
    const [t] = syncHistoryResyncTracks([{}]);
    expect(t).toMatchObject({ id: '', name: '', duration_ms: 0, popularity: 0, album: '' });
  });

  it('survives no tracks at all', () => {
    expect(syncHistoryResyncTracks(null)).toEqual([]);
  });

  it('NEVER produces "[object Object]" from an unexpected object', () => {
    // String(someObject) yields "[object Object]", which the matcher would then
    // dutifully search Soulseek for. Empty is visibly missing; that is a
    // plausible-looking wrong answer.
    const [t] = syncHistoryResyncTracks([
      { id: { nested: 1 }, name: { nested: 1 }, album: { no_name_key: 1 }, artists: [{ x: 1 }] },
    ]);
    expect(t.id).toBe('');
    expect(t.name).toBe('');
    expect(t.album).toBe('');
    expect(t.artists).toEqual(['']);
  });

  it('keeps a numeric id, which some sources store unquoted', () => {
    expect(syncHistoryResyncTracks([{ id: 4081 }])[0].id).toBe('4081');
  });

  it('falls back to Unknown Artist for an empty string, not just a missing key', () => {
    expect(syncHistoryResyncTracks([{ artists: '' }])[0].artists).toEqual(['Unknown Artist']);
  });
});

describe('reading one poll of the sync status', () => {
  it('counts failed tracks as processed', () => {
    // A bar that only advanced on success sits still through a run that is
    // making steady progress at finding nothing.
    const p = syncHistoryProgress({
      status: 'syncing',
      progress: { matched_tracks: 3, failed_tracks: 2, total_tracks: 10 },
    });
    expect(p).toMatchObject({ percent: 50, phase: 'running' });
  });

  it('appends the current track to the step when there is one', () => {
    const p = syncHistoryProgress({
      status: 'syncing',
      progress: { current_step: 'Matching', current_track: 'Sexy Boy' },
    });
    expect(p.step).toBe('Matching — Sexy Boy');
  });

  it('does not leave a dangling dash when there is no current track', () => {
    expect(
      syncHistoryProgress({ status: 'syncing', progress: { current_step: 'Matching' } }).step,
    ).toBe('Matching');
  });

  it('never divides by a zero total', () => {
    expect(syncHistoryProgress({ status: 'starting', progress: {} }).percent).toBe(0);
  });

  it('reports a finished run at 100% with its tally', () => {
    const p = syncHistoryProgress({
      status: 'finished',
      progress: { matched_tracks: 9, total_tracks: 10, synced_tracks: 9 },
    });
    expect(p.phase).toBe('finished');
    expect(p.percent).toBe(100);
    expect(p.step).toBe('Sync complete — 9/10 matched, 9 synced');
  });

  it('reads a finished run whose numbers arrived under `result`', () => {
    const p = syncHistoryProgress({ status: 'finished', result: { matched_tracks: 4 } });
    expect(p.matched).toBe(4);
  });

  it('distinguishes cancelled from error, and quotes the error', () => {
    expect(syncHistoryProgress({ status: 'cancelled' })).toMatchObject({
      phase: 'cancelled',
      step: 'Sync cancelled',
    });
    expect(syncHistoryProgress({ status: 'error', error: 'slskd down' }).step).toBe(
      'Sync error: slskd down',
    );
  });

  it('names an unknown error rather than printing undefined', () => {
    expect(syncHistoryProgress({ status: 'error' }).step).toBe('Sync error: Unknown');
  });

  it('survives a garbage payload', () => {
    expect(syncHistoryProgress(null).phase).toBe('running');
  });
});
