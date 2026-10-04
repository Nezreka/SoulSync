/**
 * Focused tests for the Overview's data model.
 *
 * The model joins mirrored-playlist rows with Auto-Sync automations. These
 * tests lock the three behaviors that screenshots alone cannot prove:
 * tolerant automations parsing (array or { automations }), running playlists
 * staying out of the unscheduled tray, and the 48h timeline window split.
 */
import { renderHook, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { useOverviewModel } from './use-overview-model';

function playlist(id: number, overrides: Record<string, unknown> = {}) {
  return {
    id,
    name: `Playlist ${id}`,
    source: 'spotify',
    track_count: 10,
    total_count: 10,
    discovered_count: 10,
    ...overrides,
  };
}

function automation(playlistId: number, nextRun: string) {
  return {
    id: 1000 + playlistId,
    action_type: 'playlist_pipeline',
    owned_by: 'auto_sync',
    trigger_type: 'schedule',
    trigger_config: { interval: 24, unit: 'hours' },
    action_config: { playlist_id: playlistId },
    next_run: nextRun,
    enabled: true,
  };
}

function hoursFromNow(h: number): string {
  return new Date(Date.now() + h * 3600 * 1000).toISOString();
}

function mockFetch(playlists: unknown[], automationsPayload: unknown) {
  vi.stubGlobal(
    'fetch',
    vi.fn(async (url: string) => {
      if (String(url).includes('/api/mirrored-playlists')) {
        return new Response(JSON.stringify(playlists));
      }
      if (String(url).includes('/api/automations')) {
        return new Response(JSON.stringify(automationsPayload));
      }
      throw new Error(`unexpected fetch: ${url}`);
    }),
  );
}

beforeEach(() => {
  vi.unstubAllGlobals();
});

describe('useOverviewModel', () => {
  it('parses a raw automations array', async () => {
    mockFetch([playlist(1)], [automation(1, hoursFromNow(2))]);
    const { result } = renderHook(() => useOverviewModel(true));
    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(result.current.scheduled).toHaveLength(1);
    expect(result.current.scheduled[0].row.id).toBe(1);
    expect(result.current.unscheduled).toHaveLength(0);
  });

  it('parses the { automations: [...] } envelope like useCardSchedules does', async () => {
    mockFetch([playlist(1)], { automations: [automation(1, hoursFromNow(2))] });
    const { result } = renderHook(() => useOverviewModel(true));
    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(result.current.scheduled).toHaveLength(1);
    expect(result.current.scheduled[0].row.id).toBe(1);
  });

  it('keeps a running playlist out of the unscheduled tray', async () => {
    // No automation, but a pipeline in flight: it belongs in Now Syncing,
    // not in the "drag me to schedule" tray.
    mockFetch([playlist(1, { pipeline_state: { status: 'running' } }), playlist(2)], []);
    const { result } = renderHook(() => useOverviewModel(true));
    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(result.current.running.map((r) => r.id)).toEqual([1]);
    expect(result.current.unscheduled.map((r) => r.id)).toEqual([2]);
  });

  it('splits the 48h window from later runs', async () => {
    mockFetch(
      [playlist(1), playlist(2)],
      [automation(1, hoursFromNow(2)), automation(2, hoursFromNow(72))],
    );
    const { result } = renderHook(() => useOverviewModel(true));
    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(result.current.scheduled.map((s) => s.row.id)).toEqual([1]);
    expect(result.current.scheduledLater).toBe(1);
  });

  it('flags a discovery gap as needing attention, but never a running playlist', async () => {
    mockFetch(
      [
        playlist(1, { discovered_count: 6 }), // gap: 6 of 10 discovered
        playlist(2, { pipeline_state: { status: 'running' }, discovered_count: 3 }),
      ],
      [],
    );
    const { result } = renderHook(() => useOverviewModel(true));
    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(result.current.attention.map((r) => r.id)).toEqual([1]);
  });

  it('does not fetch while inactive', async () => {
    const fetchSpy = vi.fn(async () => new Response('[]'));
    vi.stubGlobal('fetch', fetchSpy);
    renderHook(() => useOverviewModel(false));
    await new Promise((r) => setTimeout(r, 50));
    expect(fetchSpy).not.toHaveBeenCalled();
  });
});
