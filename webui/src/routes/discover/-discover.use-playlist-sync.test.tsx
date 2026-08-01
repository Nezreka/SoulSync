import { act, renderHook } from '@testing-library/react';
import { HttpResponse, http } from 'msw';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { server } from '@/test/msw';

import type { DiscoverMix } from './-discover.mixes';
import type { SyncToast } from './-discover.use-playlist-sync';

import { usePlaylistSync } from './-discover.use-playlist-sync';

const popular: DiscoverMix = {
  key: 'popular_picks',
  title: 'Popular Picks',
  subtitle: 's',
  syncKey: 'popular_picks',
};
const decade: DiscoverMix = {
  key: 'decade_1980',
  title: '1980s',
  subtitle: '1980s Classics',
  statusBase: 'decade-1980',
};

const tracks = [
  { track_name: 'Xtal', artist_name: 'Aphex Twin', album_name: 'SAW', duration_ms: 1000 },
];

let bridgeCalls: { id: string; name: string; tracks: unknown[] }[] = [];
let statusBody: Record<string, unknown> = { status: 'syncing', progress: {} };
let toasts: SyncToast[] = [];

function mount() {
  return renderHook(() => usePlaylistSync((t) => toasts.push(t)));
}

beforeEach(() => {
  vi.useFakeTimers();
  bridgeCalls = [];
  toasts = [];
  statusBody = { status: 'syncing', progress: {} };
  window.startDiscoverVirtualSync = (id, name, spotifyTracks) => {
    bridgeCalls.push({ id, name, tracks: spotifyTracks });
    return Promise.resolve();
  };
  server.use(
    http.get('/api/sync/status/:id', () => {
      const { httpStatus, ...body } = statusBody as { httpStatus?: number };
      return HttpResponse.json(body, { status: httpStatus ?? 200 });
    }),
  );
});

afterEach(() => {
  vi.useRealTimers();
  delete window.startDiscoverVirtualSync;
  server.resetHandlers();
});

describe('usePlaylistSync — resume', () => {
  it('resumes ONLY the syncs that answer active, silently skipping the rest', async () => {
    // release_radar mid-sync; weekly 404s; seasonal answers but finished.
    server.use(
      http.get('/api/sync/status/discover_release_radar', () =>
        HttpResponse.json({ status: 'syncing', progress: { total_tracks: 4 } }),
      ),
      // res.ok is checked BEFORE the body is read (328) — even a 404 whose
      // body claims 'syncing' must not resume.
      http.get('/api/sync/status/discover_discovery_weekly', () =>
        HttpResponse.json({ status: 'syncing' }, { status: 404 }),
      ),
      http.get('/api/sync/status/discover_seasonal_playlist', () =>
        HttpResponse.json({ status: 'finished' }),
      ),
    );
    const { result } = mount();
    await act(async () => result.current.resumeActiveSyncs());
    expect(result.current.syncingKeys).toEqual(['release-radar']);
    // 'starting' also counts — it is the FIRST state a sync reports (330).
    server.use(
      http.get('/api/sync/status/discover_discovery_weekly', () =>
        HttpResponse.json({ status: 'starting' }),
      ),
    );
    await act(async () => result.current.resumeActiveSyncs());
    expect(result.current.syncingKeys).toContain('discovery-weekly');
    expect(toasts).toEqual([]); // resuming is silent
  });
});

describe('usePlaylistSync', () => {
  it('refuses an empty sync with the per-family warning, engine untouched', () => {
    const { result } = mount();
    let out: SyncToast | null = null;
    act(() => {
      out = result.current.startMixSync(popular, []);
    });
    expect(out).toEqual({
      message: 'No tracks available for Popular Picks',
      level: 'warning',
    });
    act(() => {
      out = result.current.startMixSync(decade, undefined);
    });
    expect(out).toEqual({ message: 'No tracks available for this decade', level: 'warning' });
    expect(bridgeCalls).toHaveLength(0);
  });

  it('seeds the engine with the virtual playlist and shows progress at once', () => {
    const { result } = mount();
    act(() => {
      result.current.startMixSync(popular, tracks);
    });
    expect(bridgeCalls).toHaveLength(1);
    expect(bridgeCalls[0].id).toBe('discover_popular_picks');
    expect(bridgeCalls[0].name).toBe('Popular Picks');
    // toSyncTracks shape — artists as an ARRAY.
    expect(bridgeCalls[0].tracks[0]).toMatchObject({ name: 'Xtal' });
    // The status block is visible before the first poll answers (2768).
    expect(result.current.progressFor('popular-picks')).toMatchObject({ percentage: 0 });
    expect(result.current.syncingKeys).toEqual(['popular-picks']);
  });

  it('keys a decade sync by its own base, converted + flattened', () => {
    const { result } = mount();
    act(() => {
      result.current.startMixSync(decade, [{ track_name: 'T', artist_name: 'A', album_name: 'B' }]);
    });
    expect(bridgeCalls[0].id).toBe('discover_decade_1980');
    expect(bridgeCalls[0].name).toBe('1980s Classics');
    // decadeTrackToSpotify with flattenArtists — artists as STRINGS (2746-2749).
    expect((bridgeCalls[0].tracks[0] as { artists: unknown[] }).artists).toEqual(['A']);

    // The DECADE converter, not toSyncTracks: with a track_data_json that
    // lacks an album, only decadeTrackToSpotify falls back to the row's
    // top-level album fields (2739-2742); toSyncTracks spreads the json as-is.
    bridgeCalls = [];
    act(() => {
      result.current.startMixSync(decade, [
        {
          track_data_json: { name: 'J', artists: ['A'] },
          album_name: 'TopAlbum',
          album_cover_url: '/img/top.jpg',
        },
      ]);
    });
    expect(
      (bridgeCalls[0].tracks[0] as { album: { images: { url: string }[] } }).album.images,
    ).toEqual([{ url: '/img/top.jpg' }]);
    expect(result.current.progressFor('decade-1980')).toBeDefined();
  });

  it('polls to live progress, toasts on finish, and clears after the linger', async () => {
    const { result } = mount();
    act(() => {
      result.current.startMixSync(popular, tracks);
    });
    statusBody = {
      status: 'syncing',
      progress: { total_tracks: 4, matched_tracks: 2, failed_tracks: 1 },
    };
    await act(() => vi.advanceTimersByTimeAsync(600));
    expect(result.current.progressFor('popular-picks')).toMatchObject({
      matched: 2,
      failed: 1,
      pending: 1,
      percentage: 75,
    });

    // A non-OK poll is SKIPPED before its body is read — even one whose
    // body claims finished must not complete the sync.
    statusBody = { status: 'finished', httpStatus: 404 } as never;
    await act(() => vi.advanceTimersByTimeAsync(600));
    expect(toasts).toEqual([]);
    expect(result.current.progressFor('popular-picks')).toBeDefined();

    statusBody = {
      status: 'finished',
      progress: { total_tracks: 4, matched_tracks: 3, failed_tracks: 1 },
    };
    await act(() => vi.advanceTimersByTimeAsync(600));
    expect(toasts).toEqual([{ message: 'Popular Picks sync complete!', level: 'success' }]);
    // Finished → the poller stops; the block lingers 3s then clears (2815).
    expect(result.current.progressFor('popular-picks')).toBeDefined();
    await act(() => vi.advanceTimersByTimeAsync(3100));
    expect(result.current.progressFor('popular-picks')).toBeUndefined();
    const polls = toasts.length;
    await act(() => vi.advanceTimersByTimeAsync(2000));
    expect(toasts.length).toBe(polls); // no further toasts — poller really stopped
  });
});
