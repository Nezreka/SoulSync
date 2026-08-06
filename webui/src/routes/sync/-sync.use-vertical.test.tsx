/**
 * The vertical controller hook — fake timers + dispatched ss:* frames +
 * captured fetch, exercising the lifecycle the vanilla pollers implement:
 * event-driven updates, the HTTP backstop at the source's cadence, optimistic
 * discovery start with revert, the sync poll to both terminal shapes, and the
 * gated close-reset with its backend phase write.
 */

import { act, renderHook } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { SYNC_SOURCES } from './-sync.sources';
import {
  SYNC_DISCOVERY_EVENT,
  fetchAndHydrateState,
  useSourceVertical,
} from './-sync.use-vertical';

interface Call {
  url: string;
  method: string;
  body: unknown;
}

let calls: Call[] = [];
let responder: (url: string) => unknown = () => ({});

function stubFetch(): void {
  calls = [];
  vi.stubGlobal(
    'fetch',
    vi.fn(async (url: string, init?: RequestInit) => {
      calls.push({
        url,
        method: init?.method ?? 'GET',
        body: init?.body ? JSON.parse(init.body as string) : undefined,
      });
      return new Response(JSON.stringify(responder(url)));
    }),
  );
}

function frame(detail: Record<string, unknown>): void {
  window.dispatchEvent(new CustomEvent(SYNC_DISCOVERY_EVENT, { detail }));
}

beforeEach(() => {
  vi.useFakeTimers();
  responder = () => ({});
  stubFetch();
});

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe('discovery via frames', () => {
  it('applies frames for seeded playlists only, and completion stops the backstop', async () => {
    const { result } = renderHook(() => useSourceVertical(SYNC_SOURCES.tidal));
    act(() => result.current.seed('77', { name: 'My Tidal List' }));

    await act(async () => {
      await result.current.startDiscovery('77');
    });
    expect(result.current.states['77'].phase).toBe('discovering');
    expect(calls[0]).toMatchObject({ url: '/api/tidal/discovery/start/77', method: 'POST' });

    act(() =>
      frame({
        id: '77',
        phase: 'discovering',
        progress: 50,
        spotify_matches: 1,
        spotify_total: 2,
        results: [{ tidal_track: { name: 'A', artists: ['X'] }, spotify_data: { name: 'A2' } }],
      }),
    );
    expect(result.current.states['77'].discoveryProgress).toBe(50);
    expect(result.current.states['77'].rows[0].status_class).toBe('found');

    // A frame for a playlist this source never seeded is ignored.
    act(() => frame({ id: 'mirrored_5', progress: 10 }));
    expect(result.current.states['mirrored_5']).toBeUndefined();

    // A colliding id from ANOTHER platform is ignored (rooms are not
    // platform-namespaced; web_server stamps the frame's platform).
    act(() => frame({ id: '77', platform: 'deezer', progress: 99 }));
    expect(result.current.states['77'].discoveryProgress).toBe(50);
    // The matching platform is accepted.
    act(() => frame({ id: '77', platform: 'tidal', progress: 75 }));
    expect(result.current.states['77'].discoveryProgress).toBe(75);

    // Completion frame → phase discovered and the backstop poller dies.
    act(() => frame({ id: '77', complete: true, phase: 'discovered' }));
    expect(result.current.states['77'].phase).toBe('discovered');
    calls = [];
    await act(async () => {
      await vi.advanceTimersByTimeAsync(3000);
    });
    expect(calls).toEqual([]); // no status polls after completion
  });

  it('the backstop polls at the source cadence and applies payloads', async () => {
    responder = (url) =>
      url.includes('/discovery/status/') ? { phase: 'discovering', progress: 30, results: [] } : {};
    const { result } = renderHook(() => useSourceVertical(SYNC_SOURCES.beatport));
    act(() => result.current.seed('ch4rt', { name: 'Top 100' }));
    await act(async () => {
      await result.current.startDiscovery('ch4rt', { name: 'Top 100' });
    });
    // The start POST wrapped the chart per config.
    expect(calls[0].body).toEqual({ chart_data: { name: 'Top 100' } });

    calls = [];
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1999);
    });
    expect(calls).toHaveLength(0); // beatport polls at 2000ms, not 1000
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1);
    });
    expect(calls[0].url).toBe('/api/beatport/discovery/status/ch4rt');
    expect(result.current.states['ch4rt'].discoveryProgress).toBe(30);
  });

  it('a failed start reverts to fresh (the beatport/LB revert)', async () => {
    responder = () => ({ error: 'nope' });
    const { result } = renderHook(() => useSourceVertical(SYNC_SOURCES.listenbrainz));
    act(() => result.current.seed('mbid-1'));
    await act(async () => {
      await result.current.startDiscovery('mbid-1', { title: 'Weekly Jams' });
    });
    expect(result.current.states['mbid-1'].phase).toBe('fresh');
    // LB sends the playlist body verbatim.
    expect(calls[0].body).toEqual({ title: 'Weekly Jams' });
  });
});

describe('sync lifecycle', () => {
  it('start captures the sync id, polls to the boolean-complete terminal, lands on sync_complete', async () => {
    let syncDone = false;
    responder = (url) => {
      if (url.includes('/sync/start/')) return { sync_playlist_id: 'sp1' };
      if (url.includes('/sync/status/'))
        return syncDone
          ? { complete: true, converted_spotify_playlist_id: 'tidal_77' }
          : {
              status: 'syncing',
              progress: { total_tracks: 4, matched_tracks: 2, failed_tracks: 0 },
            };
      return {};
    };
    const { result } = renderHook(() => useSourceVertical(SYNC_SOURCES.tidal));
    act(() => result.current.seed('77'));
    await act(async () => {
      await result.current.startSync('77');
    });
    expect(result.current.states['77'].phase).toBe('syncing');
    expect(result.current.states['77'].syncPlaylistId).toBe('sp1');

    await act(async () => {
      await vi.advanceTimersByTimeAsync(1000);
    });
    expect(result.current.states['77'].lastSyncProgress?.matched_tracks).toBe(2);

    syncDone = true;
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1000);
    });
    expect(result.current.states['77'].phase).toBe('sync_complete');
    expect(result.current.states['77'].convertedSpotifyPlaylistId).toBe('tidal_77');

    // Terminal → poller stopped.
    calls = [];
    await act(async () => {
      await vi.advanceTimersByTimeAsync(3000);
    });
    expect(calls.filter((c) => c.url.includes('/sync/status/'))).toEqual([]);
  });

  it('cancel POSTs the config endpoint (LB borrows youtube) and reverts to discovered', async () => {
    const { result } = renderHook(() => useSourceVertical(SYNC_SOURCES.listenbrainz));
    act(() => result.current.seed('mbid-1'));
    await act(async () => {
      await result.current.cancelSync('mbid-1');
    });
    expect(calls[0]).toMatchObject({ url: '/api/youtube/sync/cancel/mbid-1', method: 'POST' });
    expect(result.current.states['mbid-1'].phase).toBe('discovered');
  });

  it('a FAILED cancel leaves the phase alone (cancelTidalSync 1129-1132)', async () => {
    responder = () => ({ error: 'engine says no' });
    const { result } = renderHook(() => useSourceVertical(SYNC_SOURCES.tidal));
    act(() => result.current.seed('77'));
    act(() => result.current.hydrate('77', { phase: 'syncing' }));
    await act(async () => {
      await result.current.cancelSync('77');
    });
    expect(result.current.states['77'].phase).toBe('syncing');
  });
});

describe('closeModalReset (the unified seven blocks)', () => {
  it('gated: only sync_complete/download_complete reset, and the phase write rides the drift endpoint', async () => {
    const { result } = renderHook(() => useSourceVertical(SYNC_SOURCES.beatport));
    act(() => result.current.seed('ch4rt'));
    act(() =>
      result.current.hydrate('ch4rt', {
        phase: 'download_complete',
        discovery_results: [],
        download_process_id: 'batch-1',
      }),
    );
    await act(async () => {
      await result.current.closeModalReset('ch4rt');
    });
    expect(result.current.states['ch4rt'].phase).toBe('discovered');
    expect(result.current.states['ch4rt'].downloadProcessId).toBeUndefined();
    // The HYPHEN endpoint, from the config.
    expect(calls[0]).toMatchObject({
      url: '/api/beatport/charts/update-phase/ch4rt',
      method: 'POST',
      body: { phase: 'discovered' },
    });

    // A discovering playlist is untouched by a close.
    calls = [];
    act(() => result.current.hydrate('ch4rt', { phase: 'discovering' }));
    await act(async () => {
      await result.current.closeModalReset('ch4rt');
    });
    expect(result.current.states['ch4rt'].phase).toBe('discovering');
    expect(calls).toEqual([]);
  });
});

describe('hydration', () => {
  it('fromBackendState through hydrate, and fetchAndHydrateState round-trips the state endpoint', async () => {
    responder = (url) =>
      url.includes('/state/')
        ? { phase: 'discovered', discovery_results: [{ qobuz_track: { name: 'A', artists: [] } }] }
        : {};
    const { result } = renderHook(() => useSourceVertical(SYNC_SOURCES.qobuz));
    await act(async () => {
      await fetchAndHydrateState(SYNC_SOURCES.qobuz, '9', result.current.hydrate);
    });
    expect(calls[0].url).toBe('/api/qobuz/state/9');
    expect(result.current.states['9'].phase).toBe('discovered');
    expect(result.current.states['9'].rows).toHaveLength(1);
  });
});

describe('discovery completion announcement', () => {
  function toastSpy(): ReturnType<typeof vi.fn> {
    const spy = vi.fn();
    vi.stubGlobal('showToast', spy);
    (window as unknown as Record<string, unknown>).showToast = spy;
    return spy;
  }

  it('raises the source toast once, no matter how many completions arrive', () => {
    const spy = toastSpy();
    const { result } = renderHook(() => useSourceVertical(SYNC_SOURCES.youtube));
    act(() => result.current.seed('h1'));
    act(() => result.current.resumeDiscovery('h1'));

    act(() => frame({ id: 'h1', platform: 'youtube', complete: true, spotify_matches: 4 }));
    expect(spy).toHaveBeenCalledWith('Discovery complete!', 'success');
    // The vanilla's socket AND always-on poll can both reach the block (9233,
    // 9281); the port announces once per run.
    act(() => frame({ id: 'h1', platform: 'youtube', complete: true, spotify_matches: 4 }));
    expect(spy).toHaveBeenCalledTimes(1);
  });

  it('announces again after a NEW discovery run starts', () => {
    const spy = toastSpy();
    const { result } = renderHook(() => useSourceVertical(SYNC_SOURCES.youtube));
    act(() => result.current.seed('h1'));
    act(() => result.current.resumeDiscovery('h1'));
    act(() => frame({ id: 'h1', platform: 'youtube', complete: true }));
    act(() => result.current.resumeDiscovery('h1'));
    act(() => frame({ id: 'h1', platform: 'youtube', complete: true }));
    expect(spy).toHaveBeenCalledTimes(2);
  });

  it('stays silent for the six verticals the vanilla never toasts', () => {
    const spy = toastSpy();
    const { result } = renderHook(() => useSourceVertical(SYNC_SOURCES.tidal));
    act(() => result.current.seed('42'));
    act(() => frame({ id: '42', platform: 'tidal', complete: true }));
    expect(spy).not.toHaveBeenCalled();
  });

  it('stays silent when discovery parks on phase error', () => {
    const spy = toastSpy();
    const { result } = renderHook(() => useSourceVertical(SYNC_SOURCES.youtube));
    act(() => result.current.seed('h1'));
    act(() => frame({ id: 'h1', platform: 'youtube', phase: 'error' }));
    expect(spy).not.toHaveBeenCalled();
  });

  it('stays silent when the BACKSTOP POLL sees phase error, not only frames', async () => {
    const spy = toastSpy();
    responder = () => ({ phase: 'error' });
    const { result } = renderHook(() => useSourceVertical(SYNC_SOURCES.youtube));
    act(() => result.current.seed('h1'));
    act(() => result.current.resumeDiscovery('h1'));
    await act(async () => {
      await vi.advanceTimersByTimeAsync(SYNC_SOURCES.youtube.discovery.pollMs);
    });
    expect(spy).not.toHaveBeenCalled();
  });

  it('reports the #815 retry result and consumes the baseline', () => {
    const spy = toastSpy();
    const { result } = renderHook(() => useSourceVertical(SYNC_SOURCES.mirrored));
    act(() => result.current.seed('mirrored_3'));
    act(() =>
      result.current.patchState('mirrored_3', (s) => ({
        ...s,
        retryDiscovery: { matchesBefore: 7, retryCount: 5 },
      })),
    );
    act(() => frame({ id: 'mirrored_3', platform: 'youtube', complete: true, spotify_matches: 9 }));
    expect(spy).toHaveBeenCalledWith(
      'Retry complete: 2 of 5 newly found, 3 still not found',
      'success',
    );
    expect(result.current.states.mirrored_3.retryDiscovery).toBeUndefined();

    // The baseline is gone, so the NEXT run reports plainly (9198).
    act(() => result.current.resumeDiscovery('mirrored_3'));
    act(() => frame({ id: 'mirrored_3', platform: 'youtube', complete: true }));
    expect(spy).toHaveBeenLastCalledWith('Discovery complete!', 'success');
  });

  it('fires the source hook — how the LB mirror is reached (11075/11170)', () => {
    toastSpy();
    const onDiscoveryComplete = vi.fn();
    const { result } = renderHook(() =>
      useSourceVertical(SYNC_SOURCES.listenbrainz, { onDiscoveryComplete }),
    );
    act(() => result.current.seed('mbid'));
    // LB's completion also accepts phase 'discovered' (11062).
    act(() => frame({ id: 'mbid', platform: 'listenbrainz', phase: 'discovered' }));
    expect(onDiscoveryComplete).toHaveBeenCalledWith('mbid');
    expect(onDiscoveryComplete).toHaveBeenCalledTimes(1);
  });

  it('announces from the HTTP backstop too, not only the frame path', async () => {
    const spy = toastSpy();
    responder = () => ({ complete: true, spotify_matches: 2 });
    const { result } = renderHook(() => useSourceVertical(SYNC_SOURCES.youtube));
    act(() => result.current.seed('h1'));
    act(() => result.current.resumeDiscovery('h1'));
    await act(async () => {
      await vi.advanceTimersByTimeAsync(SYNC_SOURCES.youtube.discovery.pollMs);
    });
    expect(spy).toHaveBeenCalledWith('Discovery complete!', 'success');
  });
});
