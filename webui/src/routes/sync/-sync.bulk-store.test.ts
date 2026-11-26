import { afterEach, describe, expect, it, vi } from 'vitest';

import {
  BULK_DONE_PILL_MS,
  bulkPillLabel,
  dismissBulkRun,
  getBulkState,
  hideBulkPanel,
  openBulkPanel,
  resetBulkStoreForTests,
  startBulkRun,
  stopBulkRun,
} from './-sync.bulk-store';
import { flush, gatedServer } from './__fixtures__/bulk-server';

const rows = [
  { id: 1, name: 'Road Trip' },
  { id: 2, name: 'Gym' },
  { id: 3, name: 'Sleep' },
];

afterEach(() => {
  resetBulkStoreForTests();
  vi.useRealTimers();
});

describe('the bulk run store (#2320)', () => {
  it('shows progress as each playlist ends, then how it went', async () => {
    const s = gatedServer([2]);
    expect(startBulkRun('sync', rows, s.deps as never)).toBe(true);
    await flush();
    expect(getBulkState().open).toBe(true);
    expect(bulkPillLabel(getBulkState().run!)).toBe('Syncing 1 of 3');
    await s.release(1);
    expect(bulkPillLabel(getBulkState().run!)).toBe('Syncing 2 of 3');
    await s.release(2);
    await s.release(3);
    const { run, justFinished, finishedCount } = getBulkState();
    expect(run!.running).toBe(false);
    expect(justFinished).toBe(true);
    expect(finishedCount).toBe(1);
    expect(bulkPillLabel(run!)).toBe('Done · 2 synced, 1 failed');
  });

  it('runs one at a time: a second start while running does nothing', async () => {
    const s = gatedServer();
    startBulkRun('download', rows, s.deps as never);
    await flush();
    expect(startBulkRun('sync', rows, s.deps as never)).toBe(false);
    expect(getBulkState().run!.mode).toBe('download');
  });

  it('stop lets the one in progress finish and starts no more', async () => {
    const s = gatedServer();
    startBulkRun('sync', rows, s.deps as never);
    await flush();
    stopBulkRun();
    expect(getBulkState().run!.stopping).toBe(true);
    await s.release(1);
    const run = getBulkState().run!;
    expect(run.running).toBe(false);
    expect(run.items.map((i) => i.state)).toEqual(['done', 'stopped', 'stopped']);
    expect(s.deps.start).toHaveBeenCalledTimes(1);
    expect(bulkPillLabel(run)).toBe('Done · 1 synced, 2 not started');
  });

  it('hiding the panel keeps the run, and it can be opened again', async () => {
    const s = gatedServer();
    startBulkRun('sync', rows, s.deps as never);
    await flush();
    hideBulkPanel();
    expect(getBulkState().open).toBe(false);
    expect(getBulkState().run!.running).toBe(true);
    // closing while it runs only hides, never drops the run
    openBulkPanel();
    dismissBulkRun();
    expect(getBulkState().open).toBe(false);
    expect(getBulkState().run).not.toBeNull();
    openBulkPanel();
    expect(getBulkState().open).toBe(true);
  });

  it('the done pill goes after a moment; close clears a finished run', async () => {
    vi.useFakeTimers();
    const s = gatedServer();
    startBulkRun('sync', rows.slice(0, 1), s.deps as never);
    await flush();
    await s.release(1);
    expect(getBulkState().justFinished).toBe(true);
    vi.advanceTimersByTime(BULK_DONE_PILL_MS);
    expect(getBulkState().justFinished).toBe(false);
    dismissBulkRun();
    expect(getBulkState().run).toBeNull();
  });
});
