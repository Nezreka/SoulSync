import { act, renderHook, waitFor } from '@testing-library/react';
import { HttpResponse, http } from 'msw';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { server } from '@/test/msw';

import type { AdlDownload } from './-adl.types';

import { useAdlDownloads } from './-adl.use-downloads';

const row = (over: Partial<AdlDownload> = {}): AdlDownload =>
  ({
    task_id: 't1',
    title: 'Track',
    artist: 'Artist',
    album: 'Album',
    artwork: '',
    status: 'completed',
    progress: 100,
    error: null,
    verification_status: null,
    batch_id: '',
    batch_name: '',
    batch_source: '',
    playlist_id: '',
    track_index: 0,
    batch_total: 1,
    timestamp: 0,
    priority: 0,
    quality: '',
    is_persistent_history: false,
    ...over,
  }) as AdlDownload;

function stubDownloads(downloads: unknown[] = [], batches: unknown[] = []) {
  let calls = 0;
  server.use(
    http.get('/api/downloads/all', () => {
      calls += 1;
      return HttpResponse.json({ success: true, downloads, batches });
    }),
  );
  return () => calls;
}

function stubHistory(history: unknown[] = []) {
  let calls = 0;
  server.use(
    http.get('/api/downloads/batch-history', () => {
      calls += 1;
      return HttpResponse.json({ success: true, history });
    }),
  );
  return () => calls;
}

beforeEach(() => {
  stubDownloads();
  stubHistory();
});

afterEach(() => {
  vi.useRealTimers();
});

async function mounted(opts: Parameters<typeof useAdlDownloads>[0] = {}) {
  const view = renderHook(() => useAdlDownloads(opts));
  await waitFor(() => expect(view.result.current.state.loaded).toBe(true));
  return view;
}

describe('polling', () => {
  it('fetches downloads and history on mount', async () => {
    const downloads = stubDownloads([row()]);
    const history = stubHistory([{ playlist_name: 'X' }]);
    const { result } = await mounted();

    expect(downloads()).toBeGreaterThanOrEqual(1);
    await waitFor(() => expect(history()).toBeGreaterThanOrEqual(1));
    expect(result.current.state.downloads).toHaveLength(1);
  });

  it('polls downloads on the 2s cadence', async () => {
    const downloads = stubDownloads();
    await mounted();
    const initial = downloads();

    await act(async () => {
      await new Promise((r) => setTimeout(r, 2100));
    });

    expect(downloads()).toBeGreaterThan(initial);
  });

  it('asks for a quarantine refresh every 7th poll, not every poll', async () => {
    // ~15s in real time. Anything more eager hammers the filesystem scan the
    // quarantine list does.
    vi.useFakeTimers();
    const onQuarantineRefresh = vi.fn();
    const view = renderHook(() => useAdlDownloads({ onQuarantineRefresh }));

    await act(async () => {
      await vi.advanceTimersByTimeAsync(2000 * 6);
    });
    expect(onQuarantineRefresh).not.toHaveBeenCalled();

    await act(async () => {
      await vi.advanceTimersByTimeAsync(2000);
    });
    expect(onQuarantineRefresh).toHaveBeenCalledTimes(1);

    await act(async () => {
      await vi.advanceTimersByTimeAsync(2000 * 7);
    });
    expect(onQuarantineRefresh).toHaveBeenCalledTimes(2);

    view.unmount();
  });

  it('stops both timers on unmount', async () => {
    const downloads = stubDownloads();
    const view = await mounted();
    view.unmount();
    const after = downloads();

    await new Promise((r) => setTimeout(r, 2200));

    expect(downloads()).toBe(after);
  });

  it('keeps the current list when a poll fails', async () => {
    stubDownloads([row({ task_id: 'keep-me' })]);
    const { result } = await mounted();
    expect(result.current.state.downloads).toHaveLength(1);

    server.use(http.get('/api/downloads/all', () => HttpResponse.error()));
    await act(async () => {
      await result.current.refresh();
    });

    // Not blanked — one bad response must not empty a working page.
    expect(result.current.state.downloads).toHaveLength(1);
  });
});

describe('the nav badge is deliberately not touched', () => {
  it('never writes #dl-nav-badge', async () => {
    // /api/downloads/all is capped at 300, so counting it would UNDER-report a
    // large queue. The WebSocket status push owns that number.
    const badge = document.createElement('span');
    badge.id = 'dl-nav-badge';
    badge.className = 'hidden';
    badge.textContent = '';
    document.body.appendChild(badge);
    try {
      stubDownloads([row({ status: 'downloading' }), row({ status: 'queued', task_id: 't2' })]);
      await mounted();
      expect(badge.textContent).toBe('');
      expect(badge.className).toBe('hidden');
    } finally {
      badge.remove();
    }
  });
});

describe('filtering', () => {
  const rows = [
    row({ task_id: 'a', status: 'downloading' }),
    row({ task_id: 'b', status: 'queued' }),
    row({ task_id: 'c', status: 'completed' }),
    row({ task_id: 'd', status: 'failed' }),
    row({ task_id: 'e', status: 'cancelled' }),
    row({ task_id: 'f', status: 'skipped' }),
    row({ task_id: 'g', status: 'already_owned' }),
    row({ task_id: 'h', status: 'not_found' }),
  ];

  it('shows everything under all', async () => {
    stubDownloads(rows);
    const { result } = await mounted();
    expect(result.current.visible).toHaveLength(8);
  });

  it('puts cancelled under Failed, not its own bucket', async () => {
    // The filter sets and the status→class map disagree here on purpose;
    // deriving one from the other loses every cancelled row.
    stubDownloads(rows);
    const { result } = await mounted();

    act(() => result.current.setFilter('failed'));

    expect(result.current.visible.map((d) => d.task_id).sort()).toEqual(['d', 'e', 'h']);
  });

  it('treats skipped and already_owned as completed', async () => {
    stubDownloads(rows);
    const { result } = await mounted();
    act(() => result.current.setFilter('completed'));
    expect(result.current.visible.map((d) => d.task_id).sort()).toEqual(['c', 'f', 'g']);
  });

  it('shows only review rows under unverified', async () => {
    stubDownloads([
      row({ task_id: 'u1', status: 'completed', verification_status: 'unverified' }),
      row({ task_id: 'u2', status: 'completed', verification_status: 'force_imported' }),
      row({ task_id: 'ok', status: 'completed', verification_status: 'verified' }),
      // Right verification status, wrong status — must not appear.
      row({ task_id: 'live', status: 'downloading', verification_status: 'unverified' }),
    ]);
    const { result } = await mounted();

    act(() => result.current.setFilter('unverified'));

    expect(result.current.visible.map((d) => d.task_id).sort()).toEqual(['u1', 'u2']);
  });

  it('narrows to one batch, and toggles back off', async () => {
    stubDownloads([row({ task_id: 'x', batch_id: 'b1' }), row({ task_id: 'y', batch_id: 'b2' })]);
    const { result } = await mounted();

    act(() => result.current.toggleBatchFilter('b1'));
    expect(result.current.visible.map((d) => d.task_id)).toEqual(['x']);

    act(() => result.current.toggleBatchFilter('b1'));
    expect(result.current.visible).toHaveLength(2);
  });

  it('applies the batch filter and the status filter together', async () => {
    stubDownloads([
      row({ task_id: 'x', batch_id: 'b1', status: 'completed' }),
      row({ task_id: 'y', batch_id: 'b1', status: 'downloading' }),
      row({ task_id: 'z', batch_id: 'b2', status: 'completed' }),
    ]);
    const { result } = await mounted();

    act(() => result.current.toggleBatchFilter('b1'));
    act(() => result.current.setFilter('completed'));

    expect(result.current.visible.map((d) => d.task_id)).toEqual(['x']);
  });
});

describe('header counts', () => {
  it('counts the whole list, not the filtered view', async () => {
    // Switching to Completed must not make "2 active" vanish from the header.
    stubDownloads([
      row({ task_id: 'a', status: 'downloading' }),
      row({ task_id: 'b', status: 'searching' }),
      row({ task_id: 'c', status: 'queued' }),
      row({ task_id: 'd', status: 'completed' }),
    ]);
    const { result } = await mounted();

    act(() => result.current.setFilter('completed'));

    expect(result.current.counts).toEqual({
      active: 2,
      queued: 1,
      failed: 0,
      total: 4,
      completedOrFailed: 1,
    });
  });

  it('counts failed rows toward Clear Completed', async () => {
    // clear-completed also wipes the persisted history tail, so after a
    // restart the list can be all completed/failed and the button must show.
    stubDownloads([
      row({ task_id: 'a', status: 'failed' }),
      row({ task_id: 'b', status: 'cancelled' }),
    ]);
    const { result } = await mounted();
    expect(result.current.counts.completedOrFailed).toBe(2);
  });

  it('reports running work only while something is cancellable', async () => {
    stubDownloads([row({ status: 'completed' })]);
    const first = await mounted();
    expect(first.result.current.hasRunningWork).toBe(false);
    first.unmount();

    stubDownloads([row({ status: 'queued' })]);
    const second = await mounted();
    expect(second.result.current.hasRunningWork).toBe(true);
  });
});

describe('batch cards', () => {
  const batch = (over: Record<string, unknown> = {}) => ({
    batch_id: 'b1',
    playlist_id: 'p1',
    batch_name: 'Batch',
    source_page: 'wishlist',
    phase: 'downloading',
    total: 10,
    completed: 2,
    failed: 0,
    active: 1,
    queued: 7,
    ...over,
  });

  it('keeps running batches and drops long-finished ones', async () => {
    vi.useFakeTimers();
    stubDownloads([], [batch({ phase: 'complete' })]);
    const view = renderHook(() => useAdlDownloads());
    await act(async () => {
      await vi.advanceTimersByTimeAsync(10);
    });
    expect(view.result.current.visibleBatches).toHaveLength(1);

    // Past the fade window.
    await act(async () => {
      await vi.advanceTimersByTimeAsync(16000);
    });
    expect(view.result.current.visibleBatches).toHaveLength(0);
    view.unmount();
  });

  it('fades a finished batch only after 60% of the window', async () => {
    vi.useFakeTimers();
    stubDownloads([], [batch({ phase: 'complete' })]);
    const view = renderHook(() => useAdlDownloads());
    await act(async () => {
      await vi.advanceTimersByTimeAsync(10);
    });

    expect(view.result.current.batchOpacity('b1', 'complete')).toBe(1);

    await act(async () => {
      await vi.advanceTimersByTimeAsync(10_000);
    });
    const mid = view.result.current.batchOpacity('b1', 'complete');
    expect(mid).toBeGreaterThan(0);
    expect(mid).toBeLessThan(1);
    view.unmount();
  });

  it('never fades a running batch', async () => {
    stubDownloads([], [batch()]);
    const { result } = await mounted();
    expect(result.current.batchOpacity('b1', 'downloading')).toBe(1);
  });

  it('gives each batch its own ETA sample array, stable across calls', async () => {
    const { result } = await mounted();
    const a1 = result.current.rateSamplesFor('b1');
    const a2 = result.current.rateSamplesFor('b1');
    const b = result.current.rateSamplesFor('b2');
    expect(a1).toBe(a2);
    expect(a1).not.toBe(b);
  });
});
