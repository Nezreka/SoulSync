/**
 * A playlist's sync interval, on its card.
 *
 * The point of these is that the card and the Auto-Sync board must never
 * disagree about what a playlist is scheduled for — they read the same
 * automations through the same pure builder — and that a WEEKLY schedule is
 * never silently converted into an hourly one.
 */

import { act, renderHook, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import {
  CARD_SCHEDULE_OPTIONS,
  cardScheduleLabel,
  cardSchedulesFrom,
  useCardSchedules,
} from './-sync.card-schedule';

/** An owned hourly schedule automation, as /api/automations returns one. */
function hourly(playlistId: number, hours: number, id = 100 + playlistId) {
  return {
    id,
    // `Auto-Sync:` prefix is what marks the row as the board's own; without
    // it the schedule belongs to someone else and must not be touched.
    name: `Auto-Sync: playlist ${playlistId}`,
    enabled: true,
    trigger_type: 'schedule',
    trigger_config: { interval: hours, unit: 'hours' },
    action_type: 'playlist_pipeline',
    action_config: { playlist_id: playlistId },
  };
}

let calls: { url: string; method: string; body?: unknown }[] = [];

beforeEach(() => {
  calls = [];
  vi.stubGlobal('showToast', vi.fn());
  vi.stubGlobal(
    'fetch',
    vi.fn(async (url: string, init?: RequestInit) => {
      calls.push({
        url,
        method: init?.method ?? 'GET',
        body: init?.body ? JSON.parse(init.body as string) : undefined,
      });
      if (url === '/api/automations' && (init?.method ?? 'GET') === 'GET') {
        return new Response(JSON.stringify({ automations: [hourly(7, 6)] }));
      }
      return new Response(JSON.stringify({ success: true }));
    }),
  );
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('reading schedules', () => {
  it('reads an interval off an owned automation', () => {
    expect(cardSchedulesFrom([hourly(7, 6)])).toEqual({
      '7': { hours: 6, automationId: 107, weekly: false, nextRun: null },
    });
  });

  it('is empty for an empty or malformed payload rather than throwing', () => {
    expect(cardSchedulesFrom([])).toEqual({});
    expect(cardSchedulesFrom(undefined as unknown as unknown[])).toEqual({});
  });

  it('labels what the card shows, including the weekly case', () => {
    expect(cardScheduleLabel(undefined)).toBe('Not scheduled');
    expect(cardScheduleLabel({ hours: 6, automationId: 1, weekly: false })).toContain('6');
    expect(cardScheduleLabel({ hours: null, automationId: 1, weekly: true })).toBe('Weekly');
  });

  it('carries the next run when a clock is given', () => {
    // The board showed both. The cadence alone answers a weaker question: how
    // often, but not whether anything is about to happen.
    const now = Date.UTC(2026, 0, 15, 12, 0, 0);
    const soon = new Date(now + 3 * 3600_000).toISOString();
    expect(
      cardScheduleLabel({ hours: 6, automationId: 1, weekly: false, nextRun: soon }, now),
    ).toBe('Every 6 hours · next in 3h');
  });

  it('says only the cadence when there is no next run to report', () => {
    const now = Date.UTC(2026, 0, 15, 12, 0, 0);
    expect(cardScheduleLabel({ hours: 6, automationId: 1, weekly: false }, now)).toBe(
      'Every 6 hours',
    );
  });

  it('never appends a next run to "Not scheduled"', () => {
    expect(cardScheduleLabel(undefined, Date.now())).toBe('Not scheduled');
  });

  it('offers "Not scheduled" first, so turning it off is one click', () => {
    expect(CARD_SCHEDULE_OPTIONS[0]).toEqual({ value: '', label: 'Not scheduled' });
    expect(CARD_SCHEDULE_OPTIONS.length).toBeGreaterThan(1);
  });
});

describe('the controller', () => {
  it('loads TWO endpoints in parallel, not the modal\u2019s five', async () => {
    // Cards need this on every visit, so the budget matters: the schedules and
    // the run history the failure signal reads, fetched together so the second
    // costs no added latency. The modal loads five.
    const { result } = renderHook(() => useCardSchedules());
    await waitFor(() => expect(result.current.loaded).toBe(true));
    expect(calls.map((c) => c.url).sort()).toEqual([
      '/api/automations',
      '/api/playlist-pipeline/history?limit=100',
    ]);
    expect(result.current.schedules['7']?.hours).toBe(6);
  });

  it('still renders when the history is unavailable', async () => {
    // Best-effort decoration: no history means no failure glyph, which is
    // exactly how it looked to anyone who never opened the Auto-Sync modal.
    vi.stubGlobal(
      'fetch',
      vi.fn(async (url: string) => {
        if (String(url).startsWith('/api/playlist-pipeline')) throw new Error('offline');
        return new Response(JSON.stringify({ automations: [hourly(7, 6)] }));
      }),
    );
    const { result } = renderHook(() => useCardSchedules());
    await waitFor(() => expect(result.current.loaded).toBe(true));
    expect(result.current.schedules['7']?.hours).toBe(6);
    expect(result.current.history).toEqual([]);
  });

  it('marks itself loaded even when the read fails, so cards still render', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => {
        throw new Error('offline');
      }),
    );
    const { result } = renderHook(() => useCardSchedules());
    await waitFor(() => expect(result.current.loaded).toBe(true));
    expect(result.current.schedules).toEqual({});
  });

  it('updates an existing hourly schedule in place', async () => {
    const { result } = renderHook(() => useCardSchedules());
    await waitFor(() => expect(result.current.loaded).toBe(true));
    await act(async () => {
      await result.current.set({ id: 7, name: 'Mix', source: 'spotify' }, 12);
    });
    const write = calls.find((c) => c.method === 'PUT');
    expect(write).toBeDefined();
    expect(calls.some((c) => c.method === 'POST')).toBe(false);
  });

  it('creates one when the playlist has none', async () => {
    const { result } = renderHook(() => useCardSchedules());
    await waitFor(() => expect(result.current.loaded).toBe(true));
    await act(async () => {
      await result.current.set({ id: 99, name: 'New', source: 'spotify' }, 4);
    });
    expect(calls.some((c) => c.method === 'POST')).toBe(true);
  });

  it('unschedules by DELETE, and does nothing when there was no schedule', async () => {
    const { result } = renderHook(() => useCardSchedules());
    await waitFor(() => expect(result.current.loaded).toBe(true));

    await act(async () => {
      await result.current.set({ id: 99, name: 'Never scheduled', source: 'spotify' }, null);
    });
    expect(calls.some((c) => c.method === 'DELETE')).toBe(false);

    await act(async () => {
      await result.current.set({ id: 7, name: 'Mix', source: 'spotify' }, null);
    });
    expect(calls.some((c) => c.method === 'DELETE')).toBe(true);
  });

  it('refuses a source Auto-Sync cannot refresh, and writes nothing', async () => {
    const { result } = renderHook(() => useCardSchedules());
    await waitFor(() => expect(result.current.loaded).toBe(true));
    const before = calls.length;
    await act(async () => {
      await result.current.set({ id: 3, name: 'From a file', source: 'file' }, 6);
    });
    expect(calls.length).toBe(before);
  });

  it('ignores a row with no id rather than guessing one', async () => {
    // Guessing would schedule the wrong playlist.
    const { result } = renderHook(() => useCardSchedules());
    await waitFor(() => expect(result.current.loaded).toBe(true));
    const before = calls.length;
    await act(async () => {
      await result.current.set({ name: 'No id', source: 'spotify' }, 6);
    });
    expect(calls.length).toBe(before);
  });

  it('re-reads after a write, so the card shows what was actually saved', async () => {
    const { result } = renderHook(() => useCardSchedules());
    await waitFor(() => expect(result.current.loaded).toBe(true));
    const readsBefore = calls.filter((c) => c.url === '/api/automations' && c.method === 'GET');
    await act(async () => {
      await result.current.set({ id: 7, name: 'Mix', source: 'spotify' }, 24);
    });
    const readsAfter = calls.filter((c) => c.url === '/api/automations' && c.method === 'GET');
    expect(readsAfter.length).toBeGreaterThan(readsBefore.length);
  });
});
