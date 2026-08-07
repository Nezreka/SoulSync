/**
 * Differential tests for the Auto-Sync controller — auto-sync.js 602-650,
 * 1315-1392, 2051-2134, 2237-2336 and 2338-2360.
 */

import { act, renderHook, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import * as api from './-sync.api';
import { useAutoSync } from './-sync.use-autosync';

const NOW = Date.UTC(2026, 0, 1, 12, 0, 0);

const jsonRes = (body: unknown, ok = true) =>
  ({ ok, status: ok ? 200 : 500, json: async () => body }) as Response;

interface Stubs {
  playlists: Record<string, unknown>[];
  automations: unknown;
  history: unknown;
}

function stubApi(over: Partial<Stubs> = {}) {
  const s: Stubs = {
    playlists: [{ id: 1, name: 'Late Night', source: 'spotify', track_count: 10 }],
    automations: [],
    history: { history: [], total: 0 },
    ...over,
  };
  vi.spyOn(api, 'fetchMirroredPlaylists').mockResolvedValue(s.playlists);
  vi.spyOn(api, 'fetchAutomations').mockResolvedValue(jsonRes(s.automations));
  vi.spyOn(api, 'fetchPipelineHistory').mockResolvedValue(jsonRes(s.history));
  vi.spyOn(api, 'fetchPersonalizedKinds').mockResolvedValue(null);
  vi.spyOn(api, 'fetchPersonalizedPlaylists').mockResolvedValue(null);
  vi.spyOn(api, 'createAutomation').mockResolvedValue(jsonRes({ success: true }));
  vi.spyOn(api, 'updateAutomation').mockResolvedValue(jsonRes({ success: true }));
  vi.spyOn(api, 'deleteAutomation').mockResolvedValue(jsonRes({ success: true }));
  vi.spyOn(api, 'runAutomation').mockResolvedValue(jsonRes({ success: true }));
  vi.spyOn(api, 'patchMirroredPreferences').mockResolvedValue(jsonRes({ success: true }));
  return s;
}

const hourlyAutomation = (playlistId: number, automationId = 100) => ({
  id: automationId,
  name: 'Auto-Sync: Late Night',
  action_type: 'playlist_pipeline',
  action_config: { playlist_id: playlistId },
  trigger_type: 'schedule',
  trigger_config: { interval: 24, unit: 'hours' },
  owned_by: 'auto_sync',
  enabled: true,
});

const weeklyAutomation = (playlistId: number, automationId = 200) => ({
  ...hourlyAutomation(playlistId, automationId),
  trigger_type: 'weekly_time',
  trigger_config: { time: '09:00', days: ['mon'], tz: 'UTC' },
});

async function mount(over: Partial<Stubs> = {}) {
  stubApi(over);
  const hook = renderHook(() => useAutoSync({ open: true, now: () => NOW }));
  await waitFor(() => {
    expect(hook.result.current.loading).toBe(false);
  });
  return hook;
}

beforeEach(() => {
  window.showToast = vi.fn();
  window.showConfirmDialog = vi.fn(async () => true);
});

afterEach(() => {
  vi.restoreAllMocks();
  delete window.showToast;
  delete window.showConfirmDialog;
  delete window.runMirroredPlaylistPipeline;
});

describe('loading (602-650)', () => {
  it('builds the board state from all three required endpoints', async () => {
    const { result } = await mount({ automations: [hourlyAutomation(1)] });
    expect(result.current.loadError).toBeNull();
    expect(result.current.state.playlists).toHaveLength(1);
    expect(result.current.state.playlistSchedules['1']?.hours).toBe(24);
  });

  it('surfaces a load failure as the whole-modal error', async () => {
    stubApi();
    vi.spyOn(api, 'fetchAutomations').mockResolvedValue(jsonRes({ error: 'nope' }, false));
    const { result } = renderHook(() => useAutoSync({ open: true, now: () => NOW }));
    await waitFor(() => {
      expect(result.current.loadError).toBe('nope');
    });
  });

  it('uses the endpoint-specific message for each failure', async () => {
    stubApi();
    vi.spyOn(api, 'fetchPipelineHistory').mockResolvedValue(jsonRes({}, false));
    const { result } = renderHook(() => useAutoSync({ open: true, now: () => NOW }));
    await waitFor(() => {
      expect(result.current.loadError).toBe('Failed to load pipeline run history');
    });
  });

  it('does NOT load at all while closed', () => {
    stubApi();
    renderHook(() => useAutoSync({ open: false, now: () => NOW }));
    expect(api.fetchAutomations).not.toHaveBeenCalled();
  });

  it('clears a previous error once a later load succeeds', async () => {
    stubApi();
    vi.spyOn(api, 'fetchAutomations').mockResolvedValueOnce(jsonRes({ error: 'nope' }, false));
    const { result } = renderHook(() => useAutoSync({ open: true, now: () => NOW }));
    await waitFor(() => {
      expect(result.current.loadError).toBe('nope');
    });
    await act(async () => {
      await result.current.refresh();
    });
    expect(result.current.loadError).toBeNull();
  });

  it('survives the personalized enrichment THROWING (620-636)', async () => {
    stubApi();
    // A kinds body that parses but blows up inside the enrichment.
    vi.spyOn(api, 'fetchPersonalizedKinds').mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => {
        throw new Error('bad json');
      },
    } as unknown as Response);
    const { result } = renderHook(() => useAutoSync({ open: true, now: () => NOW }));
    await waitFor(() => {
      expect(result.current.loading).toBe(false);
    });
    expect(result.current.loadError).toBeNull();
    expect(result.current.state.playlists).toHaveLength(1);
  });

  it('survives the personalized endpoints returning nothing usable', async () => {
    stubApi();
    vi.spyOn(api, 'fetchPersonalizedKinds').mockResolvedValue(
      jsonRes({ success: true, kinds: null }),
    );
    const { result } = renderHook(() => useAutoSync({ open: true, now: () => NOW }));
    await waitFor(() => {
      expect(result.current.loading).toBe(false);
    });
    // Best-effort: the board still loaded.
    expect(result.current.loadError).toBeNull();
    expect(result.current.state.playlists).toHaveLength(1);
  });
});

describe('saving an hourly schedule (2051-2110)', () => {
  it('POSTs a fresh schedule with the owned-by stamp', async () => {
    const { result } = await mount();
    await act(async () => {
      await result.current.saveHourly(1, 24);
    });
    expect(api.createAutomation).toHaveBeenCalledTimes(1);
    const payload = (api.createAutomation as ReturnType<typeof vi.fn>).mock.calls[0][0] as Record<
      string,
      unknown
    >;
    expect(payload.name).toBe('Auto-Sync: Late Night');
    expect(payload.trigger_type).toBe('schedule');
    expect(payload.owned_by).toBe('auto_sync');
    expect(payload.group_name).toBe('Playlist Auto-Sync');
    expect(payload.then_actions).toEqual([]);
    expect(window.showToast).toHaveBeenCalledWith('Late Night scheduled every 1d', 'success');
  });

  it('PUTs when the playlist already has an hourly schedule', async () => {
    const { result } = await mount({ automations: [hourlyAutomation(1, 55)] });
    await act(async () => {
      await result.current.saveHourly(1, 8);
    });
    expect(api.updateAutomation).toHaveBeenCalledWith(55, expect.anything());
    expect(api.createAutomation).not.toHaveBeenCalled();
  });

  it('DROPS an existing weekly schedule first — the invariant (2059-2067)', async () => {
    const { result } = await mount({ automations: [weeklyAutomation(1, 77)] });
    await act(async () => {
      await result.current.saveHourly(1, 24);
    });
    expect(api.deleteAutomation).toHaveBeenCalledWith(77);
    // ...and then creates, because no HOURLY row existed.
    expect(api.createAutomation).toHaveBeenCalledTimes(1);
  });

  it('refuses a source Auto-Sync cannot refresh', async () => {
    const { result } = await mount({
      playlists: [{ id: 1, name: 'Mixtape', source: 'file' }],
    });
    await act(async () => {
      await result.current.saveHourly(1, 24);
    });
    expect(api.createAutomation).not.toHaveBeenCalled();
    expect(window.showToast).toHaveBeenCalledWith(
      'That playlist source cannot be refreshed by Auto-Sync.',
      'info',
    );
  });

  it('reports a write failure without throwing', async () => {
    const { result } = await mount();
    vi.spyOn(api, 'createAutomation').mockResolvedValue(jsonRes({ error: 'boom' }, false));
    await act(async () => {
      await result.current.saveHourly(1, 24);
    });
    expect(window.showToast).toHaveBeenCalledWith('Error: boom', 'error');
  });
});

describe('saving a weekly schedule (2237-2300)', () => {
  const draft = { playlistId: 1, time: '09:00', days: ['mon', 'fri'], tz: 'UTC' };

  it('POSTs a weekly trigger and drops an existing hourly one', async () => {
    const { result } = await mount({ automations: [hourlyAutomation(1, 33)] });
    await act(async () => {
      await result.current.saveWeekly(draft);
    });
    expect(api.deleteAutomation).toHaveBeenCalledWith(33);
    const payload = (api.createAutomation as ReturnType<typeof vi.fn>).mock.calls[0][0] as Record<
      string,
      unknown
    >;
    expect(payload.trigger_type).toBe('weekly_time');
    expect(payload.trigger_config).toMatchObject({ time: '09:00', days: ['mon', 'fri'] });
    expect(window.showToast).toHaveBeenCalledWith(
      'Late Night scheduled mon, fri @ 09:00',
      'success',
    );
  });

  it('REFUSES an empty day set even though a drop cannot produce one (2249)', async () => {
    const { result } = await mount();
    await act(async () => {
      await result.current.saveWeekly({ ...draft, days: [] });
    });
    expect(api.createAutomation).not.toHaveBeenCalled();
    expect(window.showToast).toHaveBeenCalledWith(
      'Pick at least one day for the weekly schedule.',
      'error',
    );
  });
});

describe('unscheduling (2120-2134, 2291-2305)', () => {
  it('confirms, deletes and reports', async () => {
    const { result } = await mount({ automations: [hourlyAutomation(1, 44)] });
    await act(async () => {
      await result.current.unscheduleHourly(1);
    });
    expect(window.showConfirmDialog).toHaveBeenCalledWith({
      title: 'Remove Auto-Sync',
      message: 'Remove Auto-Sync schedule for "Late Night"?',
    });
    expect(api.deleteAutomation).toHaveBeenCalledWith(44);
    expect(window.showToast).toHaveBeenCalledWith('Auto-Sync schedule removed', 'success');
  });

  it('does nothing when the user declines', async () => {
    window.showConfirmDialog = vi.fn(async () => false);
    const { result } = await mount({ automations: [hourlyAutomation(1, 44)] });
    await act(async () => {
      await result.current.unscheduleHourly(1);
    });
    expect(api.deleteAutomation).not.toHaveBeenCalled();
  });

  it('does nothing at all when there is no schedule', async () => {
    const { result } = await mount();
    await act(async () => {
      await result.current.unscheduleHourly(1);
    });
    expect(window.showConfirmDialog).not.toHaveBeenCalled();
    expect(api.deleteAutomation).not.toHaveBeenCalled();
  });

  it('uses the weekly wording and map for the weekly path', async () => {
    const { result } = await mount({ automations: [weeklyAutomation(1, 66)] });
    await act(async () => {
      await result.current.unscheduleWeekly(1);
    });
    expect(window.showConfirmDialog).toHaveBeenCalledWith({
      title: 'Remove Weekly Schedule',
      message: 'Remove weekly schedule for "Late Night"?',
    });
    expect(api.deleteAutomation).toHaveBeenCalledWith(66);
    expect(window.showToast).toHaveBeenCalledWith('Weekly schedule removed', 'success');
  });
});

describe('Run now (2307-2336)', () => {
  it('hands a real mirrored playlist to the vanilla engine', async () => {
    window.runMirroredPlaylistPipeline = vi.fn();
    const { result } = await mount();
    await act(async () => {
      await result.current.runNow(1);
    });
    expect(window.runMirroredPlaylistPipeline).toHaveBeenCalledWith(1, 'Late Night');
    expect(api.runAutomation).not.toHaveBeenCalled();
  });

  it('runs a synthetic personalized row through ITS OWN automation', async () => {
    window.runMirroredPlaylistPipeline = vi.fn();
    const { result } = await mount({
      playlists: [
        { id: 5, name: 'Discovery Mix', source: 'soulsync_discovery', _personalized: true },
      ],
      automations: [hourlyAutomation(5, 88)],
    });
    await act(async () => {
      await result.current.runNow(5);
    });
    expect(api.runAutomation).toHaveBeenCalledWith(88);
    expect(window.showToast).toHaveBeenCalledWith('Running Discovery Mix…', 'success');
    // ...and NOT also through the vanilla engine.
    expect(window.runMirroredPlaylistPipeline).not.toHaveBeenCalled();
  });

  it('tells the user to schedule an unscheduled personalized row first', async () => {
    window.runMirroredPlaylistPipeline = vi.fn();
    const { result } = await mount({
      playlists: [
        { id: 5, name: 'Discovery Mix', source: 'soulsync_discovery', _personalized: true },
      ],
    });
    await act(async () => {
      await result.current.runNow(5);
    });
    expect(api.runAutomation).not.toHaveBeenCalled();
    expect(window.runMirroredPlaylistPipeline).not.toHaveBeenCalled();
    expect(window.showToast).toHaveBeenCalledWith('Schedule it first, then Run now.', 'info');
  });
});

describe('the organize toggle (1933-1949)', () => {
  it('PATCHes and updates the row in place', async () => {
    const { result } = await mount();
    await act(async () => {
      await result.current.setOrganize(1, true);
    });
    expect(api.patchMirroredPreferences).toHaveBeenCalledWith(1, { organize_by_playlist: true });
    expect(result.current.state.playlists[0].organize_by_playlist).toBe(true);
    expect(window.showToast).toHaveBeenCalledWith('Auto-Sync will use playlist folders', 'success');
  });

  it('has its own wording for turning it off', async () => {
    const { result } = await mount();
    await act(async () => {
      await result.current.setOrganize(1, false);
    });
    expect(window.showToast).toHaveBeenCalledWith(
      'Auto-Sync will use standard download layout',
      'success',
    );
  });

  it('re-reads from the server when the write fails', async () => {
    const { result } = await mount();
    vi.spyOn(api, 'patchMirroredPreferences').mockResolvedValue(jsonRes({ error: 'no' }, false));
    const before = (api.fetchAutomations as ReturnType<typeof vi.fn>).mock.calls.length;
    await act(async () => {
      await result.current.setOrganize(1, true);
    });
    expect(window.showToast).toHaveBeenCalledWith('Error: no', 'error');
    expect((api.fetchAutomations as ReturnType<typeof vi.fn>).mock.calls.length).toBe(before + 1);
  });
});

describe('bulk scheduling (1315-1338)', () => {
  const twoSpotify = [
    { id: 1, name: 'A', source: 'spotify' },
    { id: 2, name: 'B', source: 'spotify' },
    { id: 3, name: 'C', source: 'tidal' },
  ];

  it('schedules every schedulable playlist in the source, and no others', async () => {
    const { result } = await mount({ playlists: twoSpotify });
    await act(async () => {
      await result.current.bulkSchedule('spotify', 12);
    });
    expect(api.createAutomation).toHaveBeenCalledTimes(2);
    expect(window.showToast).toHaveBeenCalledWith(
      'Scheduled 2 Spotify playlists at 12h',
      'success',
    );
  });

  it('DROPS a weekly schedule per playlist — the bug the vanilla had (1368)', async () => {
    const { result } = await mount({
      playlists: twoSpotify,
      automations: [weeklyAutomation(1, 91), weeklyAutomation(2, 92)],
    });
    await act(async () => {
      await result.current.bulkSchedule('spotify', 12);
    });
    expect(api.deleteAutomation).toHaveBeenCalledWith(91);
    expect(api.deleteAutomation).toHaveBeenCalledWith(92);
  });

  it('skips non-schedulable sources inside the chosen source', async () => {
    const { result } = await mount({
      playlists: [{ id: 1, name: 'A', source: 'file' }],
    });
    await act(async () => {
      await result.current.bulkSchedule('file', 12);
    });
    expect(api.createAutomation).not.toHaveBeenCalled();
    expect(window.showToast).toHaveBeenCalledWith('No schedulable File Imports playlists', 'info');
  });

  it('counts failures separately and warns', async () => {
    const { result } = await mount({ playlists: twoSpotify });
    vi.spyOn(api, 'createAutomation')
      .mockResolvedValueOnce(jsonRes({ success: true }))
      .mockResolvedValueOnce(jsonRes({ error: 'no' }, false));
    await act(async () => {
      await result.current.bulkSchedule('spotify', 12);
    });
    expect(window.showToast).toHaveBeenCalledWith(
      'Scheduled 1 Spotify playlist at 12h (1 failed)',
      'warning',
    );
  });

  it('stops when the user declines the confirm', async () => {
    window.showConfirmDialog = vi.fn(async () => false);
    const { result } = await mount({ playlists: twoSpotify });
    await act(async () => {
      await result.current.bulkSchedule('spotify', 12);
    });
    expect(api.createAutomation).not.toHaveBeenCalled();
  });
});

describe('bulk unscheduling (1340-1367)', () => {
  it('removes BOTH kinds of schedule', async () => {
    const { result } = await mount({
      playlists: [
        { id: 1, name: 'A', source: 'spotify' },
        { id: 2, name: 'B', source: 'spotify' },
      ],
      automations: [hourlyAutomation(1, 11), weeklyAutomation(2, 22)],
    });
    await act(async () => {
      await result.current.bulkUnschedule('spotify');
    });
    expect(api.deleteAutomation).toHaveBeenCalledWith(11);
    expect(api.deleteAutomation).toHaveBeenCalledWith(22);
    expect(window.showToast).toHaveBeenCalledWith('Removed 2 schedules', 'success');
  });

  it('says there is nothing to do when nothing is scheduled', async () => {
    const { result } = await mount();
    await act(async () => {
      await result.current.bulkUnschedule('spotify');
    });
    expect(api.deleteAutomation).not.toHaveBeenCalled();
    expect(window.showToast).toHaveBeenCalledWith(
      'No scheduled Spotify playlists to unschedule',
      'info',
    );
  });

  it('warns the user that weekly schedules go too', async () => {
    const { result } = await mount({ automations: [weeklyAutomation(1, 22)] });
    await act(async () => {
      await result.current.bulkUnschedule('spotify');
    });
    expect(window.showConfirmDialog).toHaveBeenCalledWith({
      title: 'Unschedule 1 Spotify playlist',
      message:
        'Removes the Auto-Sync schedules, hourly and weekly. Mirrored playlists themselves stay.',
    });
  });
});

describe('the status poller (2338-2360)', () => {
  it('does NOT poll while nothing is running', async () => {
    vi.useFakeTimers();
    stubApi();
    const { result } = renderHook(() => useAutoSync({ open: true, now: () => NOW }));
    await vi.waitFor(() => {
      expect(result.current.loading).toBe(false);
    });
    const before = (api.fetchAutomations as ReturnType<typeof vi.fn>).mock.calls.length;
    await act(async () => {
      await vi.advanceTimersByTimeAsync(10_000);
    });
    expect((api.fetchAutomations as ReturnType<typeof vi.fn>).mock.calls.length).toBe(before);
    vi.useRealTimers();
  });

  it('polls every 3s while a pipeline is running', async () => {
    vi.useFakeTimers();
    stubApi({
      playlists: [{ id: 1, name: 'A', source: 'spotify', pipeline_state: { status: 'running' } }],
    });
    const { result } = renderHook(() => useAutoSync({ open: true, now: () => NOW }));
    await vi.waitFor(() => {
      expect(result.current.loading).toBe(false);
    });
    const before = (api.fetchAutomations as ReturnType<typeof vi.fn>).mock.calls.length;
    await act(async () => {
      await vi.advanceTimersByTimeAsync(3000);
    });
    expect((api.fetchAutomations as ReturnType<typeof vi.fn>).mock.calls.length).toBe(before + 1);
    vi.useRealTimers();
  });

  it('does not poll while the modal is CLOSED, even with work running', async () => {
    vi.useFakeTimers();
    stubApi({
      playlists: [{ id: 1, name: 'A', source: 'spotify', pipeline_state: { status: 'running' } }],
    });
    renderHook(() => useAutoSync({ open: false, now: () => NOW }));
    await act(async () => {
      await vi.advanceTimersByTimeAsync(10_000);
    });
    expect(api.fetchAutomations).not.toHaveBeenCalled();
    vi.useRealTimers();
  });

  it('STOPS polling when the modal closes with work still running', async () => {
    // The reachable case: the hook stays mounted and keeps its loaded state,
    // so `hasRunning` is still true — only `open` has changed.
    vi.useFakeTimers();
    stubApi({
      playlists: [{ id: 1, name: 'A', source: 'spotify', pipeline_state: { status: 'running' } }],
    });
    const { result, rerender } = renderHook(({ open }) => useAutoSync({ open, now: () => NOW }), {
      initialProps: { open: true },
    });
    await vi.waitFor(() => {
      expect(result.current.loading).toBe(false);
    });
    rerender({ open: false });
    const before = (api.fetchAutomations as ReturnType<typeof vi.fn>).mock.calls.length;
    await act(async () => {
      await vi.advanceTimersByTimeAsync(30_000);
    });
    expect((api.fetchAutomations as ReturnType<typeof vi.fn>).mock.calls.length).toBe(before);
    vi.useRealTimers();
  });

  it('STOPS polling once unmounted', async () => {
    vi.useFakeTimers();
    stubApi({
      playlists: [{ id: 1, name: 'A', source: 'spotify', pipeline_state: { status: 'running' } }],
    });
    const { result, unmount } = renderHook(() => useAutoSync({ open: true, now: () => NOW }));
    await vi.waitFor(() => {
      expect(result.current.loading).toBe(false);
    });
    unmount();
    const before = (api.fetchAutomations as ReturnType<typeof vi.fn>).mock.calls.length;
    await act(async () => {
      await vi.advanceTimersByTimeAsync(30_000);
    });
    expect((api.fetchAutomations as ReturnType<typeof vi.fn>).mock.calls.length).toBe(before);
    vi.useRealTimers();
  });

  it('SKIPS a tick mid-drag, so a re-render cannot yank the card (2347)', async () => {
    vi.useFakeTimers();
    stubApi({
      playlists: [{ id: 1, name: 'A', source: 'spotify', pipeline_state: { status: 'running' } }],
    });
    const { result } = renderHook(() => useAutoSync({ open: true, now: () => NOW }));
    await vi.waitFor(() => {
      expect(result.current.loading).toBe(false);
    });
    act(() => {
      result.current.setDragging(true);
    });
    const before = (api.fetchAutomations as ReturnType<typeof vi.fn>).mock.calls.length;
    await act(async () => {
      await vi.advanceTimersByTimeAsync(9000);
    });
    expect((api.fetchAutomations as ReturnType<typeof vi.fn>).mock.calls.length).toBe(before);

    // ...and resumes once the drag ends.
    act(() => {
      result.current.setDragging(false);
    });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(3000);
    });
    expect((api.fetchAutomations as ReturnType<typeof vi.fn>).mock.calls.length).toBe(before + 1);
    vi.useRealTimers();
  });
});

describe('history paging and filtering (1246-1254)', () => {
  it('normalizes the filter it is handed', async () => {
    const { result } = await mount();
    act(() => {
      result.current.setHistoryFilter('nonsense' as never);
    });
    expect(result.current.historyFilter).toBe('all');
    act(() => {
      result.current.setHistoryFilter('error');
    });
    expect(result.current.historyFilter).toBe('error');
  });

  it('refetches with a larger window on load-more', async () => {
    const { result } = await mount();
    expect(api.fetchPipelineHistory).toHaveBeenLastCalledWith(50);
    act(() => {
      result.current.loadMoreHistory();
    });
    await waitFor(() => {
      expect(api.fetchPipelineHistory).toHaveBeenLastCalledWith(100);
    });
  });
});
