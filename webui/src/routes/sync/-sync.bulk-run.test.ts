import { describe, expect, it, vi } from 'vitest';

import { bulkSummary, runInTurn, type BulkRunState } from './-sync.bulk-run';

const rows = [
  { id: 1, name: 'Road Trip' },
  { id: 2, name: 'Gym' },
  { id: 3, name: 'Sleep' },
];

function server(statuses: Record<number, string[]>) {
  const started: { id: number; skip: unknown }[] = [];
  const at: Record<number, number> = {};
  return {
    started,
    deps: {
      start: vi.fn(async (id: number, opts?: { skipWishlist?: boolean }) => {
        started.push({ id, skip: opts?.skipWishlist });
        return {};
      }),
      status: vi.fn(async (id: number) => {
        const list = statuses[id];
        const n = Math.min(at[id] ?? 0, list.length - 1);
        at[id] = (at[id] ?? 0) + 1;
        return { status: list[n] } as never;
      }),
      wait: async () => {},
      maxPolls: 20,
    },
  };
}

describe('syncing several playlists in turn (#2309)', () => {
  it('runs one after another, sync without the wishlist', async () => {
    const s = server({ 1: ['running', 'finished'], 2: ['finished'], 3: ['running', 'error'] });
    const seen: BulkRunState[] = [];
    const out = await runInTurn(
      'sync',
      rows,
      (st) => seen.push(st),
      () => false,
      s.deps as never,
    );
    expect(s.started).toEqual([
      { id: 1, skip: true },
      { id: 2, skip: true },
      { id: 3, skip: true },
    ]);
    expect(out.items.map((i) => i.state)).toEqual(['done', 'done', 'failed']);
    // never two at once
    for (const st of seen)
      expect(st.items.filter((i) => i.state === 'running').length).toBeLessThanOrEqual(1);
    expect(bulkSummary(out)).toBe('2 of 3 synced, 1 failed');
  });

  it('sync & download lets the wishlist do its work', async () => {
    const s = server({ 1: ['finished'], 2: ['finished'], 3: ['finished'] });
    await runInTurn(
      'download',
      rows,
      () => {},
      () => false,
      s.deps as never,
    );
    expect(s.started.every((x) => x.skip === false)).toBe(true);
  });

  it('stop finishes the one in progress and starts no more', async () => {
    const s = server({ 1: ['running', 'finished'], 2: ['finished'], 3: ['finished'] });
    let stop = false;
    const out = await runInTurn(
      'sync',
      rows,
      (st) => {
        if (st.items[0].state === 'running') stop = true;
      },
      () => stop,
      s.deps as never,
    );
    expect(s.started.map((x) => x.id)).toEqual([1]);
    expect(out.items.map((i) => i.state)).toEqual(['done', 'stopped', 'stopped']);
    expect(bulkSummary(out)).toBe('1 of 3 synced, 2 not started');
  });

  it('a playlist that is idle at once is not called done before it began', async () => {
    const s = server({
      1: ['idle', 'idle', 'running', 'finished'],
      2: ['finished'],
      3: ['finished'],
    });
    const out = await runInTurn(
      'sync',
      rows.slice(0, 1),
      () => {},
      () => false,
      s.deps as never,
    );
    expect(out.items[0].state).toBe('done');
    expect(s.deps.status.mock.calls.length).toBe(4);
  });

  it('one that will not start is a failure, and the rest still run', async () => {
    const s = server({ 1: ['finished'], 2: ['finished'], 3: ['finished'] });
    s.deps.start.mockImplementationOnce(async () => {
      throw new Error('Already running');
    });
    const out = await runInTurn(
      'sync',
      rows,
      () => {},
      () => false,
      s.deps as never,
    );
    expect(out.items.map((i) => i.state)).toEqual(['failed', 'done', 'done']);
    expect(out.items[0].note).toBe('Already running');
  });
});
