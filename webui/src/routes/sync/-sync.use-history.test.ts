/**
 * Loading sync history, and re-running one row.
 *
 * The re-sync is the modal's only live machinery, so the things pinned here are
 * the ones that fail quietly: polling a screen that has closed, sending a
 * download-type row down the server path, and a synthetic id that could collide
 * with the run it came from.
 */

import { act, renderHook, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import * as api from './-sync.api';
import { useSyncHistory } from './-sync.use-history';

const ENTRY = { id: 7, playlist_name: 'Road Trip', source: 'spotify', sync_type: 'playlist' };

function stubList(over: Record<string, unknown> = {}) {
  vi.spyOn(api, 'fetchSyncHistory').mockResolvedValue({
    entries: [ENTRY],
    stats: { spotify: 3 },
    total: 1,
    ...over,
  });
}

beforeEach(() => {
  vi.restoreAllMocks();
  stubList();
});

afterEach(() => {
  vi.useRealTimers();
  vi.restoreAllMocks();
});

describe('loading the list', () => {
  it('does not fetch while the modal is closed', () => {
    renderHook(() => useSyncHistory({ active: false }));
    expect(api.fetchSyncHistory).not.toHaveBeenCalled();
  });

  it('loads when it opens, and keeps only playlist rows', async () => {
    vi.spyOn(api, 'fetchSyncHistory').mockResolvedValue({
      entries: [ENTRY, { id: 8, sync_type: 'album' }],
      stats: {},
      total: 2,
    });
    const { result } = renderHook(() => useSyncHistory({ active: true }));
    await waitFor(() => expect(result.current.entries).toHaveLength(1));
    expect(result.current.entries[0].id).toBe(7);
  });

  it('picking a source resets to page 1, so you do not land past the end', async () => {
    const { result } = renderHook(() => useSyncHistory({ active: true }));
    await waitFor(() => expect(result.current.entries).toHaveLength(1));
    act(() => result.current.goToPage(3));
    await waitFor(() => expect(result.current.page).toBe(3));
    act(() => result.current.selectSource('tidal'));
    await waitFor(() => expect(result.current.page).toBe(1));
    expect(result.current.source).toBe('tidal');
  });

  it('refuses to page below 1', async () => {
    const { result } = renderHook(() => useSyncHistory({ active: true }));
    await waitFor(() => expect(result.current.entries).toHaveLength(1));
    act(() => result.current.goToPage(0));
    expect(result.current.page).toBe(1);
  });

  it('reports a failed load instead of showing an empty history', async () => {
    vi.spyOn(api, 'fetchSyncHistory').mockRejectedValue(new Error('offline'));
    const { result } = renderHook(() => useSyncHistory({ active: true }));
    await waitFor(() => expect(result.current.error).toBe('Error loading sync history'));
  });

  it('reopening starts at the top', async () => {
    const { result, rerender } = renderHook((active: boolean) => useSyncHistory({ active }), {
      initialProps: true,
    });
    await waitFor(() => expect(result.current.entries).toHaveLength(1));
    act(() => result.current.selectSource('tidal'));
    await waitFor(() => expect(result.current.source).toBe('tidal'));
    rerender(false);
    await waitFor(() => expect(result.current.source).toBeNull());
  });
});

describe('deleting a row', () => {
  it('drops it locally rather than refetching the page', async () => {
    // A refetch pulls the next page's first row up under the cursor mid-click.
    vi.spyOn(api, 'deleteSyncHistoryEntry').mockResolvedValue({ success: true });
    const { result } = renderHook(() => useSyncHistory({ active: true }));
    await waitFor(() => expect(result.current.entries).toHaveLength(1));
    const before = (api.fetchSyncHistory as ReturnType<typeof vi.fn>).mock.calls.length;
    await act(async () => {
      await result.current.remove(7);
    });
    expect(result.current.entries).toHaveLength(0);
    expect(result.current.total).toBe(0);
    expect((api.fetchSyncHistory as ReturnType<typeof vi.fn>).mock.calls.length).toBe(before);
  });

  it('keeps the row and says so when the server refuses', async () => {
    const toast = vi.fn();
    vi.spyOn(api, 'deleteSyncHistoryEntry').mockResolvedValue({ success: false });
    const { result } = renderHook(() => useSyncHistory({ active: true, toast }));
    await waitFor(() => expect(result.current.entries).toHaveLength(1));
    await act(async () => {
      await result.current.remove(7);
    });
    expect(result.current.entries).toHaveLength(1);
    expect(toast).toHaveBeenCalledWith('Failed to delete entry', 'error');
  });
});

describe('re-syncing a row', () => {
  it('sends a download-type row to the download modal and starts NO sync', async () => {
    // Both paths "succeed", so picking wrong does the wrong thing silently.
    const openDownloadModal = vi.fn();
    vi.spyOn(api, 'fetchSyncHistoryEntry').mockResolvedValue({
      success: true,
      entry: { id: 7, source: 'discover' },
    });
    const start = vi.spyOn(api, 'startSync');
    const { result } = renderHook(() => useSyncHistory({ active: true, openDownloadModal }));
    await act(async () => {
      await result.current.resync(7);
    });
    expect(openDownloadModal).toHaveBeenCalledTimes(1);
    expect(start).not.toHaveBeenCalled();
  });

  it('starts a server sync under an id that cannot collide with the original', async () => {
    vi.spyOn(api, 'fetchSyncHistoryEntry').mockResolvedValue({
      success: true,
      entry: { id: 7, playlist_name: 'Road Trip', source: 'spotify', tracks: [{ name: 'A' }] },
    });
    const start = vi.spyOn(api, 'startSync').mockResolvedValue({ success: true });
    const { result } = renderHook(() => useSyncHistory({ active: true, now: () => 1_700_000 }));
    await act(async () => {
      await result.current.resync(7);
    });
    expect(start).toHaveBeenCalledWith({
      playlist_id: 'resync_7_1700000',
      playlist_name: 'Road Trip',
      tracks: [
        {
          id: '',
          name: 'A',
          artists: ['Unknown Artist'],
          album: '',
          duration_ms: 0,
          popularity: 0,
        },
      ],
    });
    expect(result.current.resyncs[7]).toBeTruthy();
  });

  it('clears the row when the start is refused, rather than leaving it "Syncing…"', async () => {
    const toast = vi.fn();
    vi.spyOn(api, 'fetchSyncHistoryEntry').mockResolvedValue({
      success: true,
      entry: { id: 7, source: 'spotify' },
    });
    vi.spyOn(api, 'startSync').mockResolvedValue({ success: false, error: 'busy' });
    const { result } = renderHook(() => useSyncHistory({ active: true, toast }));
    await act(async () => {
      await result.current.resync(7);
    });
    expect(result.current.resyncs[7]).toBeUndefined();
    expect(toast).toHaveBeenCalledWith('Sync failed: busy', 'error');
  });

  it('says so when the entry itself will not load', async () => {
    const toast = vi.fn();
    vi.spyOn(api, 'fetchSyncHistoryEntry').mockResolvedValue({ success: false });
    const { result } = renderHook(() => useSyncHistory({ active: true, toast }));
    await act(async () => {
      await result.current.resync(7);
    });
    expect(toast).toHaveBeenCalledWith('Failed to load sync data', 'error');
  });
});

describe('the poll', () => {
  it('advances the row, then clears it when the run finishes', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const toast = vi.fn();
    vi.spyOn(api, 'fetchSyncHistoryEntry').mockResolvedValue({
      success: true,
      entry: { id: 7, source: 'spotify' },
    });
    vi.spyOn(api, 'startSync').mockResolvedValue({ success: true });
    const status = vi.spyOn(api, 'fetchAccountSyncStatus').mockResolvedValue({
      status: 'syncing',
      progress: { matched_tracks: 1, failed_tracks: 1, total_tracks: 4 },
    } as never);

    const { result } = renderHook(() => useSyncHistory({ active: true, toast }));
    await act(async () => {
      await result.current.resync(7);
    });

    await act(async () => {
      await vi.advanceTimersByTimeAsync(2000);
    });
    expect(result.current.resyncs[7].progress.percent).toBe(50);

    status.mockResolvedValue({
      status: 'finished',
      progress: { matched_tracks: 4, total_tracks: 4, synced_tracks: 4 },
    } as never);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(2000);
    });
    expect(toast).toHaveBeenCalledWith('Re-sync complete: 4/4 matched', 'success');

    // It lingers so the result can be read, then collapses.
    expect(result.current.resyncs[7]).toBeTruthy();
    await act(async () => {
      await vi.advanceTimersByTimeAsync(5000);
    });
    expect(result.current.resyncs[7]).toBeUndefined();
  });

  it('STOPS when the hook unmounts — the vanilla polled a screen that was gone', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    vi.spyOn(api, 'fetchSyncHistoryEntry').mockResolvedValue({
      success: true,
      entry: { id: 7, source: 'spotify' },
    });
    vi.spyOn(api, 'startSync').mockResolvedValue({ success: true });
    const status = vi
      .spyOn(api, 'fetchAccountSyncStatus')
      .mockResolvedValue({ status: 'syncing', progress: {} } as never);

    const { result, unmount } = renderHook(() => useSyncHistory({ active: true }));
    await act(async () => {
      await result.current.resync(7);
    });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(2000);
    });
    const afterOneTick = status.mock.calls.length;
    expect(afterOneTick).toBeGreaterThan(0);

    unmount();
    await act(async () => {
      await vi.advanceTimersByTimeAsync(10_000);
    });
    expect(status.mock.calls.length).toBe(afterOneTick);
  });

  it('cancel asks the server and says "Cancelling…" without tearing the row down', async () => {
    // Tearing it down here would hide a cancel the server declined.
    vi.spyOn(api, 'fetchSyncHistoryEntry').mockResolvedValue({
      success: true,
      entry: { id: 7, source: 'spotify' },
    });
    vi.spyOn(api, 'startSync').mockResolvedValue({ success: true });
    const cancelCall = vi.spyOn(api, 'cancelSync').mockResolvedValue({ success: true });
    const { result } = renderHook(() => useSyncHistory({ active: true, now: () => 5 }));
    await act(async () => {
      await result.current.resync(7);
    });
    await act(async () => {
      await result.current.cancel(7);
    });
    expect(cancelCall).toHaveBeenCalledWith('resync_7_5');
    expect(result.current.resyncs[7].progress.step).toBe('Cancelling…');
  });

  it('cancel on a row that is not running does nothing', async () => {
    const cancelCall = vi.spyOn(api, 'cancelSync');
    const { result } = renderHook(() => useSyncHistory({ active: true }));
    await act(async () => {
      await result.current.cancel(99);
    });
    expect(cancelCall).not.toHaveBeenCalled();
  });
});
