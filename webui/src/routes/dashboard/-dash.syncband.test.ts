/** The merged Sync band's row assembly. */

import { describe, expect, it } from 'vitest';

import type { AutoSyncCardRow } from './-dash.autosync';
import type { SyncCardView } from './-dash.library';

import { syncBandRows } from './-dash.syncband';

function sched(over: Partial<AutoSyncCardRow>): AutoSyncCardRow {
  return {
    key: '7',
    automationId: 101,
    name: 'Discover Weekly',
    sourceKey: 'spotify',
    source: 'Spotify',
    logo: '/static/img/brands/spotify.png',
    imageUrl: null,
    cadence: 'Every 6 hours',
    enabled: true,
    nextRun: 'in 1h',
    coverage: { inLibrary: 41, total: 50, pct: 82 },
    lastRun: null,
    running: null,
    ...over,
  };
}

function view(over: Partial<SyncCardView>): SyncCardView {
  return {
    id: 900,
    playlistId: 'spotify-playlist-900',
    healthClass: 'health-good',
    sourceLabel: 'Spotify',
    timeStr: '2h ago',
    name: 'Discover Weekly',
    pct: 94,
    counts: '47/50 matched',
    thumbUrl: '/thumbs/dw.jpg',
    found: 47,
    total: 50,
    downloaded: 3,
    failed: 0,
    typeLabel: 'playlist',
    ...over,
  };
}

describe('syncBandRows', () => {
  it('adopts the newest history run onto its schedule and buckets the rest as manual', () => {
    const rows = syncBandRows(
      [sched({})],
      [
        view({ id: 901, name: 'discover weekly', timeStr: '2h ago' }), // newest — claimed (case-insensitive)
        view({ id: 900, name: 'Discover Weekly', timeStr: '9h ago' }), // older run — collapsed
        view({ id: 800, name: 'Random Album', sourceLabel: 'Wishlist', thumbUrl: null }),
      ],
    );
    expect(rows.map((r) => [r.kind, r.name])).toEqual([
      ['scheduled', 'Discover Weekly'],
      ['manual', 'Random Album'],
    ]);
    expect(rows[0].last?.id).toBe(901);
    // The run's matched count IS the row's completeness number.
    expect(rows[0].coverage).toEqual({ inLibrary: 47, total: 50, pct: 94 });
    // No playlist art → the claimed run's thumb backs the row.
    expect(rows[0].thumbUrl).toBe('/thumbs/dw.jpg');
    expect(rows[1].sourceLabel).toBe('Wishlist');
    expect(rows[1].thumbUrl).toBeNull();
  });

  it('prefers the playlist cover over the run thumb and survives no history', () => {
    const rows = syncBandRows([sched({ imageUrl: '/covers/dw.png' })], []);
    expect(rows[0].thumbUrl).toBe('/covers/dw.png');
    expect(rows[0].last).toBeNull();
    // Never-run schedules fall back to the db owned join.
    expect(rows[0].coverage).toEqual({ inLibrary: 41, total: 50, pct: 82 });
  });

  it('running leads, then newest run first across kinds, never-run schedules last', () => {
    const rows = syncBandRows(
      [
        sched({ key: '1', automationId: 1, name: 'A idle' }), // no runs yet
        sched({
          key: '2',
          automationId: 2,
          name: 'B live',
          running: { phase: 'Syncing...', progress: 40 },
        }),
        sched({ key: '3', automationId: 3, name: 'C synced' }), // claims history idx 1
      ],
      [
        view({ id: 10, name: 'Zed Manual' }), // newest run of all
        view({ id: 12, name: 'C synced' }),
        view({ id: 11, name: 'Ann Manual' }),
      ],
    );
    expect(rows.map((r) => r.name)).toEqual([
      'B live', // running always leads
      'Zed Manual', // newest run
      'C synced', // scheduled, second-newest run — interleaved with manuals
      'Ann Manual',
      'A idle', // never ran — trails
    ]);
  });

  it('a twin schedule (hourly + weekly) claims one run each without duplicating manuals', () => {
    const rows = syncBandRows(
      [
        sched({ key: '7', automationId: 101 }),
        sched({ key: '7', automationId: 202, cadence: 'Mon @ 09:00' }),
      ],
      [view({ id: 901 }), view({ id: 900, timeStr: '9h ago' })],
    );
    expect(rows).toHaveLength(2);
    expect(rows[0].last?.id).toBe(901);
    expect(rows[1].last?.id).toBe(900);
    expect(rows.every((r) => r.kind === 'scheduled')).toBe(true);
  });
});
