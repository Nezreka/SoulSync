import { beforeEach, describe, expect, it, vi } from 'vitest';

import type { StationSnapshot, StationTrack } from './-discover.stations';

import {
  fetchStationSnapshot,
  stationAcquisitionNote,
  stationScopeCopy,
  stationSelectionOf,
  stationSnapshotUrl,
  stationSubtitle,
  stationSyncCopy,
  stationSyncKey,
  stationTitle,
  stationVirtualId,
  STATIONS_URL,
  STATION_NOTHING_SELECTED,
  STATION_NO_BRIDGE,
} from './-discover.stations';

/**
 * The station contract.
 *
 * A station's download and sync act on a FINITE snapshot, and their identity
 * carries the snapshot revision — so a refreshed preview is a different
 * operation, and retrying the same one addresses the same destination playlist
 * instead of creating a second.
 */

const track = (over: Partial<StationTrack> = {}): StationTrack => ({
  id: '1',
  library_track_id: 1,
  track_id: '1',
  track_name: 'Aerodynamic',
  artist_name: 'Daft Punk',
  album_name: 'Discovery',
  duration_ms: 212000,
  has_file_path: true,
  available: true,
  owned: true,
  ...over,
});

const snapshot = (over: Partial<StationSnapshot> = {}): StationSnapshot => ({
  snapshot_id: '7-r1',
  revision: 1,
  station: { artist_id: 7, name: 'Daft Punk', image_url: '' },
  tracks: [track(), track({ id: '2', track_id: '2', track_name: 'Digital Love' })],
  counts: { returned: 2, available: 2, unavailable: 0 },
  actions: ['play', 'download', 'sync'],
  status: 'ok',
  ...over,
});

describe('the refusals are named, not inline strings', () => {
  it('says what is missing when a bridge or a selection is', () => {
    // a missing global bridge must produce a visible, recoverable error -
    // never a button that silently does nothing
    expect(STATION_NO_BRIDGE).toContain('not available');
    expect(STATION_NOTHING_SELECTED).toBe('Select at least one track first');
    expect(STATIONS_URL).toBe('/api/discover/stations');
  });
});

describe('operation identity', () => {
  it('carries the station AND the snapshot revision', () => {
    expect(stationSyncKey(snapshot())).toBe('station_7_r1');
    expect(stationSyncKey(snapshot({ revision: 4 }))).toBe('station_7_r4');
  });

  it('never shares a key space with a daily mix', () => {
    expect(stationVirtualId(snapshot())).toBe('discover_station_7_r1');
    expect(stationVirtualId(snapshot())).not.toContain('daily_mix');
  });

  it('is stable, so a retry is idempotent', () => {
    expect(stationVirtualId(snapshot())).toBe(stationVirtualId(snapshot()));
  });

  it('changes when the preview is refreshed to a new revision', () => {
    expect(stationVirtualId(snapshot({ revision: 2 }))).not.toBe(
      stationVirtualId(snapshot({ revision: 1 })),
    );
  });
});

describe('copy', () => {
  it('states the FINITE scope rather than promising endless radio', () => {
    expect(stationScopeCopy(snapshot())).toBe('2 tracks from your library');
    expect(stationSyncCopy(snapshot())).toBe('Sync these 2');
    expect(stationTitle(snapshot())).toBe('Daft Punk Station');
  });

  it('handles a one-track station without a plural', () => {
    expect(stationScopeCopy(snapshot({ tracks: [track()] }))).toBe('1 track from your library');
  });
});

describe('subtitles are claims, so they are verified', () => {
  it('says "With" only for artists the library can play', () => {
    expect(stationSubtitle({ artist_id: 1, name: 'A', image_url: '', with: ['Justice'] })).toBe(
      'With Justice and more',
    );
  });

  it('drops to a weaker label for artists it cannot play', () => {
    expect(
      stationSubtitle({ artist_id: 1, name: 'A', image_url: '', with: [], related: ['SebastiAn'] }),
    ).toBe('Related artists: SebastiAn');
  });

  it('claims nothing when it knows nothing', () => {
    expect(stationSubtitle({ artist_id: 1, name: 'A', image_url: '', with: [] })).toBe(
      'Artist radio from your library',
    );
  });
});

describe('acquisition honesty', () => {
  it('says everything is already here rather than queueing redundant work', () => {
    expect(stationAcquisitionNote([track(), track({ id: '2' })])).toBe(
      'Everything selected is already in your library.',
    );
  });

  it('counts what is referenced but missing from disk', () => {
    // a file path is evidence of a library reference, not proof of a file
    expect(stationAcquisitionNote([track(), track({ id: '2', available: false })])).toBe(
      '1 of 2 selected are missing from disk.',
    );
  });

  it('says nothing about an empty selection', () => {
    expect(stationAcquisitionNote([])).toBe('');
  });
});

describe('selection', () => {
  it('resolves indices against the open snapshot only', () => {
    expect(stationSelectionOf(snapshot(), [1]).map((t) => t.track_name)).toEqual(['Digital Love']);
    expect(stationSelectionOf(snapshot(), [9])).toEqual([]);
    expect(stationSelectionOf(null, [0])).toEqual([]);
  });
});

describe('the snapshot request', () => {
  beforeEach(() => vi.unstubAllGlobals());

  it('asks for a new revision only when refresh is explicit', () => {
    expect(stationSnapshotUrl(7)).toBe('/api/discover/stations/7/snapshot');
    expect(stationSnapshotUrl(7, true)).toBe('/api/discover/stations/7/snapshot?refresh=1');
  });

  it('is a POST and unwraps the snapshot', async () => {
    const fetchMock = vi.fn(async () => ({
      ok: true,
      status: 200,
      json: async () => ({ success: true, snapshot: snapshot() }),
    }));
    vi.stubGlobal('fetch', fetchMock);
    const out = await fetchStationSnapshot(7);
    expect(out.snapshot_id).toBe('7-r1');
    expect(fetchMock).toHaveBeenCalledWith('/api/discover/stations/7/snapshot', {
      method: 'POST',
    });
  });

  it('THROWS with the server reason rather than resolving something empty', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => ({
        ok: false,
        status: 500,
        json: async () => ({ success: false, error: 'db gone' }),
      })),
    );
    await expect(fetchStationSnapshot(7)).rejects.toThrow('db gone');
  });
});
