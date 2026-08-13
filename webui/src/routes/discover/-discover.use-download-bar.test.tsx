import { act, renderHook, waitFor } from '@testing-library/react';
import { HttpResponse, http } from 'msw';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { server } from '@/test/msw';

import { resetDownloadStoreForTests, useDownloadBar } from './-discover.use-download-bar';

let syncStatus: Record<string, { status: number; body?: Record<string, unknown> }> = {};
let snapshots: unknown[] = [];
let hydrateBody: Record<string, unknown> = { success: true, downloads: {} };

function stub() {
  snapshots = [];
  server.use(
    http.get('/api/sync/status/:id', ({ params }) => {
      const entry = syncStatus[String(params.id)] ?? { status: 404 };
      return HttpResponse.json(entry.body ?? {}, { status: entry.status });
    }),
    http.post('/api/discover_downloads/snapshot', async ({ request }) => {
      snapshots.push(await request.json());
      return HttpResponse.json({ success: true });
    }),
    http.get('/api/discover_downloads/hydrate', () => HttpResponse.json(hydrateBody)),
  );
}

beforeEach(() => {
  vi.useFakeTimers();
  resetDownloadStoreForTests();
  syncStatus = {};
  hydrateBody = { success: true, downloads: {} };
  stub();
});

afterEach(() => {
  resetDownloadStoreForTests();
  vi.useRealTimers();
  server.resetHandlers();
  delete window.discoverDownloadProcess;
});

describe('the download store', () => {
  it('publishes the window contract at module load', () => {
    expect(window.addDiscoverDownload).toBeTypeOf('function');
    expect(window.removeDiscoverDownload).toBeTypeOf('function');
    expect(window.updateDiscoverDownloadBar).toBeTypeOf('function');
    expect(window.hydrateDiscoverDownloadsFromSnapshot).toBeTypeOf('function');
    expect(window.discoverDownloads).toBeDefined();
  });

  it('adds through the GLOBAL and the hook sees it — one store', async () => {
    const { result } = renderHook(() => useDownloadBar());
    act(() => {
      window.addDiscoverDownload!('discover_x', 'Mix X', 'popular_picks', '/img/x.jpg');
    });
    expect(result.current.state['discover_x']).toMatchObject({
      name: 'Mix X',
      status: 'in_progress',
    });
    expect((window.discoverDownloads as Record<string, unknown>)['discover_x']).toBeDefined();
  });

  it('debounces the snapshot at 1s and never writes an EMPTY state', async () => {
    const { result } = renderHook(() => useDownloadBar());
    act(() => result.current.add('discover_x', 'Mix X', 'popular_picks'));
    expect(snapshots).toHaveLength(0);
    // Not just deferred — DEBOUNCED: still nothing well after the microtasks.
    await act(() => vi.advanceTimersByTimeAsync(500));
    expect(snapshots).toHaveLength(0);
    await act(() => vi.advanceTimersByTimeAsync(600));
    expect(snapshots).toHaveLength(1);
    expect(
      (snapshots[0] as { downloads: Record<string, unknown> }).downloads['discover_x'],
    ).toMatchObject({ name: 'Mix X', status: 'in_progress' });
    // Removing the last download leaves NO empty write (12208).
    act(() => result.current.remove('discover_x'));
    await act(() => vi.advanceTimersByTimeAsync(1100));
    expect(snapshots).toHaveLength(1);
  });

  it('completes from the sync poll, auto-removing after 30s', async () => {
    syncStatus['discover_x'] = { status: 200, body: { status: 'syncing' } };
    const { result } = renderHook(() => useDownloadBar());
    act(() => result.current.add('discover_x', 'Mix X', 'popular_picks'));
    await act(() => vi.advanceTimersByTimeAsync(2100));
    expect(result.current.state['discover_x'].status).toBe('in_progress');
    syncStatus['discover_x'] = { status: 200, body: { status: 'complete' } };
    await act(() => vi.advanceTimersByTimeAsync(2100));
    expect(result.current.state['discover_x'].status).toBe('completed');
    await act(() => vi.advanceTimersByTimeAsync(30100));
    expect(result.current.state['discover_x']).toBeUndefined();
  });

  it('auto-remove spares an entry RE-ADDED during the 30s linger', async () => {
    syncStatus['discover_x'] = { status: 200, body: { status: 'complete' } };
    const { result } = renderHook(() => useDownloadBar());
    act(() => result.current.add('discover_x', 'Mix X', 'popular_picks'));
    await act(() => vi.advanceTimersByTimeAsync(2100));
    expect(result.current.state['discover_x'].status).toBe('completed');
    // The user re-downloads the same playlist inside the linger window —
    // shouldAutoRemove's status guard is what keeps the fresh entry alive.
    syncStatus['discover_x'] = { status: 200, body: { status: 'syncing' } };
    act(() => result.current.add('discover_x', 'Mix X', 'popular_picks'));
    await act(() => vi.advanceTimersByTimeAsync(30100));
    expect(result.current.state['discover_x']).toBeDefined();
    expect(result.current.state['discover_x'].status).toBe('in_progress');
  });

  it('REMOVES after five consecutive 404s — the sync never started', async () => {
    const { result } = renderHook(() => useDownloadBar());
    act(() => result.current.add('discover_x', 'Mix X', 'popular_picks'));
    await act(() => vi.advanceTimersByTimeAsync(4 * 2000 + 100));
    expect(result.current.state['discover_x']).toBeDefined();
    await act(() => vi.advanceTimersByTimeAsync(2100));
    expect(result.current.state['discover_x']).toBeUndefined();
  });

  it('a found poll RESETS the 404 counter', async () => {
    const { result } = renderHook(() => useDownloadBar());
    act(() => result.current.add('discover_x', 'Mix X', 'popular_picks'));
    await act(() => vi.advanceTimersByTimeAsync(4 * 2000 + 100)); // four misses
    syncStatus['discover_x'] = { status: 200, body: { status: 'syncing' } };
    await act(() => vi.advanceTimersByTimeAsync(2100)); // reset
    delete syncStatus['discover_x'];
    await act(() => vi.advanceTimersByTimeAsync(4 * 2000 + 100)); // four again
    expect(result.current.state['discover_x']).toBeDefined();
  });

  it('an active modal PROCESS outranks the sync poll entirely', async () => {
    // 404s would remove it — but the process short-circuits the poll.
    window.discoverDownloadProcess = () => ({ status: 'running' });
    const { result } = renderHook(() => useDownloadBar());
    act(() => result.current.add('discover_x', 'Mix X', 'popular_picks'));
    await act(() => vi.advanceTimersByTimeAsync(6 * 2000 + 100));
    expect(result.current.state['discover_x'].status).toBe('in_progress');
    window.discoverDownloadProcess = () => ({ status: 'complete' });
    await act(() => vi.advanceTimersByTimeAsync(2100));
    expect(result.current.state['discover_x'].status).toBe('completed');
  });

  it('hydrates from the backend, restarting monitors ONLY for in-progress', async () => {
    hydrateBody = {
      success: true,
      downloads: {
        discover_a: { name: 'A', type: 't', status: 'in_progress', startTime: '2026-07-01' },
        discover_b: { name: 'B', type: 't', status: 'completed', startTime: '2026-07-01' },
      },
    };
    syncStatus['discover_a'] = { status: 200, body: { status: 'complete' } };
    const { result } = renderHook(() => useDownloadBar());
    await act(() => result.current.hydrate());
    expect(Object.keys(result.current.state).sort()).toEqual(['discover_a', 'discover_b']);
    // discover_a's monitor runs and completes it; discover_b has NO monitor.
    await act(() => vi.advanceTimersByTimeAsync(2100));
    expect(result.current.state['discover_a'].status).toBe('completed');
    // If b HAD a monitor, its endpoint's 404s would remove it after five
    // polls — surviving well past that proves no monitor was started.
    await act(() => vi.advanceTimersByTimeAsync(6 * 2000 + 200));
    expect(result.current.state['discover_b']).toBeDefined();
  });
});

describe('opening a bubble', () => {
  it('reopens a live modal element directly', async () => {
    window.discoverDownloadProcess = () => ({ status: 'running', modalElement: {} });
    const reopen = vi.fn(() => true);
    window.reopenActiveDownloadModal = reopen;
    const { result } = renderHook(() => useDownloadBar());
    const out = await result.current.openBubble('discover_x');
    expect(out).toBeNull();
    expect(reopen).toHaveBeenCalledWith('discover_x');
    delete window.reopenActiveDownloadModal;
  });

  it('falls through rehydrate to the status toast when no modal comes back', async () => {
    const rehydrate = vi.fn(() => Promise.resolve(false));
    window.rehydrateDiscoverDownloadModal = rehydrate;
    const { result } = renderHook(() => useDownloadBar());
    act(() => result.current.add('discover_x', 'Mix X', 'popular_picks'));
    const out = await result.current.openBubble('discover_x');
    expect(rehydrate).toHaveBeenCalledWith('discover_x');
    // Named by name and status — the user can SEE the bubble exists (11842).
    expect(out).toEqual({ toast: 'Download: Mix X - in_progress' });
    delete window.rehydrateDiscoverDownloadModal;
  });
});
