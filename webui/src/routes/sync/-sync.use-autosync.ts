/**
 * The Auto-Sync controller — auto-sync.js 571-650 (load/refresh), 2051-2134
 * and 2237-2330 (the save / unschedule / run actions), 1315-1392 (bulk), and
 * 2338-2360 (the status poller).
 *
 * Every action in the vanilla ends with `await refreshAutoSyncScheduleModal()`,
 * which refetches all five endpoints and re-renders the whole modal. That is
 * kept: the board's numbers come from automation rows the server owns, and a
 * local optimistic edit would have to guess at `next_run`, which only the
 * scheduler knows.
 *
 * THE ONE-SCHEDULE-PER-PLAYLIST INVARIANT lives here too. The vanilla enforces
 * it by having each save path delete the opposing schedule first; a live bug
 * was that the bulk path never did (fixed in the vanilla, commit 37bec3bab).
 * The port routes all three through `dropOpposing`, so the bug cannot exist in
 * this codebase in the first place.
 *
 * `_autoSyncIsDragging` survives as a REF, not state. The poller reads it to
 * skip a tick mid-drag (2347) — a re-render during a drag would yank the card
 * out from under the cursor. It must never itself trigger a render, which is
 * exactly what a ref is for.
 */

import { useCallback, useEffect, useRef, useState } from 'react';

import type { AutoSyncWeeklyDraft } from './-ui/autosync-weekly';

import {
  createAutomation,
  deleteAutomation,
  fetchAutomations,
  fetchMirroredPlaylists,
  fetchPersonalizedKinds,
  fetchPersonalizedPlaylists,
  fetchPipelineHistory,
  patchMirroredPreferences,
  runAutomation,
  updateAutomation,
} from './-sync.api';
import {
  autoSyncBucketLabel,
  autoSyncCanSchedulePlaylist,
  autoSyncIntervalLabel,
  autoSyncEnrichDiscoveryRows,
  autoSyncExpandPersonalizedRows,
  autoSyncGeneratedCountMap,
  autoSyncNextHistoryLimit,
  autoSyncNormalizeHistoryFilter,
  autoSyncSavedToast,
  autoSyncSchedulePayload,
  autoSyncSourceLabel,
  autoSyncTriggerForHours,
  autoSyncWeeklyLabel,
  autoSyncWeeklyTrigger,
  buildAutoSyncScheduleState,
  type AutoSyncHistoryFilter,
  type AutoSyncScheduleState,
  type MirroredRow,
} from './-sync.autosync';

const EMPTY_STATE: AutoSyncScheduleState = {
  playlists: [],
  automations: [],
  playlistSchedules: {},
  weeklySchedules: {},
  automationPipelines: [],
  runHistory: [],
  runHistoryTotal: 0,
};

/** 2346-2349. */
const POLL_MS = 3000;

const toast = (message: string, kind: string) => {
  window.showToast?.(message, kind);
};

const confirm = async (title: string, message: string): Promise<boolean> =>
  (await window.showConfirmDialog?.({ title, message })) ?? false;

export interface UseAutoSyncOptions {
  /** Open state — the modal only loads and polls while it is true. */
  open: boolean;
  /** Injected so the relative labels are deterministic under test. */
  now?: () => number;
  /**
   * Runs a REAL mirrored playlist through the pipeline engine — 'Run now' on
   * anything that is not a synthetic personalized row (2336).
   *
   * Required, and injected rather than reached for on `window`, because
   * `runMirroredPlaylistPipeline` is defined in auto-sync.js ITSELF (2481) —
   * the file the flip deletes. A `window.runMirroredPlaylistPipeline?.()` call
   * would keep working right up until the flip and then silently stop, which
   * is the exact failure mode vanilla-seams.test.ts exists to prevent. The
   * page passes `useMirroredPipeline().run`, which is the same function
   * already ported in P5g.
   */
  runPipeline: (playlistId: number, playlistName: string) => void;
}

export function useAutoSync({ open, now = () => Date.now(), runPipeline }: UseAutoSyncOptions) {
  const [state, setState] = useState<AutoSyncScheduleState>(EMPTY_STATE);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [historyFilter, setHistoryFilter] = useState<AutoSyncHistoryFilter>('all');
  const [historyLimit, setHistoryLimit] = useState(50);
  const [clock, setClock] = useState(now);

  const dragging = useRef(false);
  // Read by the poller so a tick fired between renders still sees live data.
  const stateRef = useRef(state);
  stateRef.current = state;
  /**
   * `now` is a prop, so a caller writing `now={() => Date.now()}` inline hands
   * us a NEW function every render. Holding it in a ref keeps it out of
   * `refresh`'s dependency list — otherwise refresh changes identity on every
   * render, the load effect re-fires, that sets state, and the whole thing
   * loops forever, refetching five endpoints each time. Found by the tests.
   */
  const nowRef = useRef(now);
  nowRef.current = now;

  /** 602-650. */
  const refresh = useCallback(async () => {
    try {
      const [playlistRows, automationRes, historyRes, kindsRes, genRes] = await Promise.all([
        // Already parsed, and already throws its own 'Failed to load mirrored
        // playlists' — the check the vanilla does inline at 616.
        fetchMirroredPlaylists(),
        fetchAutomations(),
        fetchPipelineHistory(historyLimit),
        fetchPersonalizedKinds(),
        fetchPersonalizedPlaylists(),
      ]);
      const playlists = playlistRows as MirroredRow[];
      const automations = (await automationRes.json()) as { error?: string };
      const historyData = (await historyRes.json()) as { error?: string };
      if (!automationRes.ok || automations.error) {
        throw new Error(automations.error || 'Failed to load automations');
      }
      if (!historyRes.ok || historyData.error) {
        throw new Error(historyData.error || 'Failed to load pipeline run history');
      }

      // 620-636. Best-effort: a kinds failure must never break the board, so
      // the whole enrichment sits inside its own try.
      let allPlaylists = playlists;
      try {
        const kindsData =
          kindsRes && kindsRes.ok ? ((await kindsRes.json()) as Record<string, unknown>) : null;
        if (kindsData?.success && Array.isArray(kindsData.kinds)) {
          const genData = genRes && genRes.ok ? await genRes.json() : null;
          const genCounts = autoSyncGeneratedCountMap(genData);
          const enriched = autoSyncEnrichDiscoveryRows(playlists, kindsData.kinds);
          allPlaylists = [
            ...enriched,
            ...autoSyncExpandPersonalizedRows(kindsData.kinds, enriched, genCounts),
          ];
        }
      } catch {
        /* personalized kinds optional */
      }

      setState(
        buildAutoSyncScheduleState(allPlaylists, automations as never, historyData as never),
      );
      setClock(nowRef.current());
      setLoadError(null);
    } catch (err) {
      setLoadError(err instanceof Error ? err.message : String(err));
    } finally {
      setLoading(false);
    }
  }, [historyLimit]);

  useEffect(() => {
    if (!open) return;
    setLoading(true);
    void refresh();
  }, [open, refresh]);

  /**
   * 2338-2360. Polls ONLY while something is running, and skips a tick while a
   * drag is in flight. The vanilla starts and stops the interval imperatively
   * after every render; here the effect's dependency is the answer to the same
   * question, so it starts and stops itself.
   */
  const hasRunning = state.playlists.some((p) => p.pipeline_state?.status === 'running');
  useEffect(() => {
    if (!open || !hasRunning) return;
    const id = setInterval(() => {
      if (dragging.current) return;
      void refresh();
    }, POLL_MS);
    return () => {
      clearInterval(id);
    };
  }, [open, hasRunning, refresh]);

  const findPlaylist = (playlistId: number | string) =>
    stateRef.current.playlists.find(
      (p) => parseInt(String(p.id), 10) === parseInt(String(playlistId), 10),
    );

  /**
   * The invariant, in one place. See the file header — the vanilla enforced it
   * by copy-paste and one of the three copies was missing.
   */
  const dropOpposing = async (playlistId: number | string, keep: 'hourly' | 'weekly') => {
    const opposing =
      keep === 'hourly'
        ? stateRef.current.weeklySchedules[String(playlistId)]
        : stateRef.current.playlistSchedules[String(playlistId)];
    if (!opposing) return;
    try {
      await deleteAutomation(opposing.automation_id);
    } catch {
      /* best-effort cleanup */
    }
  };

  /** Shared by every save path: PUT when a row exists, POST when it does not. */
  const writeSchedule = async (
    playlist: MirroredRow,
    playlistId: number | string,
    trigger: { trigger_type: string; trigger_config: unknown },
    existingId: number | string | undefined,
  ) => {
    const payload = autoSyncSchedulePayload(playlist, playlistId, trigger);
    const res = existingId
      ? await updateAutomation(existingId, payload)
      : await createAutomation(payload);
    const data = (await res.json()) as { error?: string };
    if (!res.ok || data.error) throw new Error(data.error || 'Failed to save Auto-Sync schedule');
    return data;
  };

  /** 2051-2110. */
  const saveHourly = useCallback(
    async (playlistId: number, hours: number) => {
      const playlist = findPlaylist(playlistId);
      if (!playlist) return;
      if (!autoSyncCanSchedulePlaylist(playlist)) {
        toast('That playlist source cannot be refreshed by Auto-Sync.', 'info');
        return;
      }
      await dropOpposing(playlistId, 'hourly');
      try {
        await writeSchedule(
          playlist,
          playlistId,
          {
            trigger_type: 'schedule',
            trigger_config: autoSyncTriggerForHours(hours),
          },
          stateRef.current.playlistSchedules[String(playlistId)]?.automation_id,
        );
        toast(
          autoSyncSavedToast(playlist.name || '', 'hourly', autoSyncBucketLabel(hours)),
          'success',
        );
        await refresh();
      } catch (err) {
        toast(`Error: ${err instanceof Error ? err.message : String(err)}`, 'error');
      }
    },
    [refresh],
  );

  /** 2237-2300. */
  const saveWeekly = useCallback(
    async (draft: AutoSyncWeeklyDraft) => {
      const playlist = findPlaylist(draft.playlistId);
      if (!playlist) return;
      if (!autoSyncCanSchedulePlaylist(playlist)) {
        toast('That playlist source cannot be refreshed by Auto-Sync.', 'info');
        return;
      }
      const triggerConfig = autoSyncWeeklyTrigger(draft);
      // 2249-2252: refused here as well as in the editor, because a drop reaches
      // this without passing through the editor at all.
      if (!triggerConfig.days.length) {
        toast('Pick at least one day for the weekly schedule.', 'error');
        return;
      }
      await dropOpposing(draft.playlistId, 'weekly');
      try {
        await writeSchedule(
          playlist,
          draft.playlistId,
          { trigger_type: 'weekly_time', trigger_config: triggerConfig },
          stateRef.current.weeklySchedules[String(draft.playlistId)]?.automation_id,
        );
        toast(
          autoSyncSavedToast(playlist.name || '', 'weekly', autoSyncWeeklyLabel(triggerConfig)),
          'success',
        );
        await refresh();
      } catch (err) {
        toast(`Error: ${err instanceof Error ? err.message : String(err)}`, 'error');
      }
    },
    [refresh],
  );

  const unscheduleOne = useCallback(
    async (playlistId: number, kind: 'hourly' | 'weekly') => {
      const map =
        kind === 'hourly' ? stateRef.current.playlistSchedules : stateRef.current.weeklySchedules;
      const schedule = map[String(playlistId)];
      if (!schedule) return;
      const playlist = findPlaylist(playlistId);
      const ok = await confirm(
        kind === 'hourly' ? 'Remove Auto-Sync' : 'Remove Weekly Schedule',
        kind === 'hourly'
          ? `Remove Auto-Sync schedule for "${playlist?.name || 'this playlist'}"?`
          : `Remove weekly schedule for "${playlist?.name || 'this playlist'}"?`,
      );
      if (!ok) return;
      try {
        const res = await deleteAutomation(schedule.automation_id);
        const data = (await res.json()) as { error?: string };
        if (!res.ok || data.error) {
          throw new Error(data.error || 'Failed to remove Auto-Sync schedule');
        }
        toast(
          kind === 'hourly' ? 'Auto-Sync schedule removed' : 'Weekly schedule removed',
          'success',
        );
        await refresh();
      } catch (err) {
        toast(`Error: ${err instanceof Error ? err.message : String(err)}`, 'error');
      }
    },
    [refresh],
  );

  /**
   * 2307-2336. A synthetic personalized row has no mirrored pipeline to run,
   * so it runs its own scheduled automation instead — and cannot run at all
   * until it HAS one.
   */
  const runNow = useCallback(
    async (playlistId: number) => {
      const playlist = findPlaylist(playlistId);
      if (!playlist) return;
      if (playlist._personalized) {
        const sched =
          stateRef.current.playlistSchedules[String(playlistId)] ||
          stateRef.current.weeklySchedules[String(playlistId)];
        if (!sched?.automation_id) {
          toast('Schedule it first, then Run now.', 'info');
          return;
        }
        try {
          const res = await runAutomation(sched.automation_id);
          const data = (await res.json()) as { error?: string };
          if (!res.ok || data.error) throw new Error(data.error || 'Failed to run');
          toast(`Running ${playlist.name}…`, 'success');
        } catch (err) {
          toast(`Error: ${err instanceof Error ? err.message : String(err)}`, 'error');
        }
        return;
      }
      // A real mirrored playlist goes to the PORTED pipeline controller, which
      // the page injects — see UseAutoSyncOptions.runPipeline for why this is
      // not a window lookup.
      runPipeline(playlistId, playlist.name || `Playlist #${playlistId}`);
    },
    [runPipeline],
  );

  /** 1933-1949. */
  const setOrganize = useCallback(
    async (playlistId: number, enabled: boolean) => {
      try {
        const res = await patchMirroredPreferences(playlistId, { organize_by_playlist: enabled });
        const data = (await res.json()) as { error?: string };
        if (!res.ok || data.error) throw new Error(data.error || 'Failed to update preference');
        setState((prev) => ({
          ...prev,
          playlists: prev.playlists.map((p) =>
            parseInt(String(p.id), 10) === playlistId ? { ...p, organize_by_playlist: enabled } : p,
          ),
        }));
        toast(
          enabled
            ? 'Auto-Sync will use playlist folders'
            : 'Auto-Sync will use standard download layout',
          'success',
        );
      } catch (err) {
        toast(`Error: ${err instanceof Error ? err.message : String(err)}`, 'error');
        await refresh();
      }
    },
    [refresh],
  );

  /** 1315-1338, now with the invariant the vanilla's bulk path was missing. */
  const bulkSchedule = useCallback(
    async (source: string, hours: number) => {
      const targets = stateRef.current.playlists.filter(
        (p) => p.source === source && autoSyncCanSchedulePlaylist(p),
      );
      if (!targets.length) {
        toast(`No schedulable ${autoSyncSourceLabel(source)} playlists`, 'info');
        return;
      }
      const label = autoSyncSourceLabel(source);
      const ok = await confirm(
        `Schedule ${targets.length} ${label} playlist${targets.length === 1 ? '' : 's'}`,
        // 1325: the INTERVAL label with its leading 'Every ' stripped, not the
        // short bucket label — 'Every 12 hours.', not 'Every 12h.'. The success
        // toast below really does use the short form, so the two differ.
        `Every ${autoSyncIntervalLabel(hours)
          .toLowerCase()
          .replace(/^every /, '')}. Existing schedules in this source will be updated.`,
      );
      if (!ok) return;
      let done = 0;
      let failed = 0;
      for (const playlist of targets) {
        try {
          await dropOpposing(playlist.id as number, 'hourly');
          await writeSchedule(
            playlist,
            playlist.id as number,
            { trigger_type: 'schedule', trigger_config: autoSyncTriggerForHours(hours) },
            stateRef.current.playlistSchedules[String(playlist.id)]?.automation_id,
          );
          done += 1;
        } catch {
          failed += 1;
        }
      }
      toast(
        `Scheduled ${done} ${label} playlist${done === 1 ? '' : 's'} at ${autoSyncBucketLabel(hours)}${
          failed ? ` (${failed} failed)` : ''
        }`,
        failed ? 'warning' : 'success',
      );
      await refresh();
    },
    [refresh],
  );

  /** 1340-1367, seeing BOTH schedule kinds. */
  const bulkUnschedule = useCallback(
    async (source: string) => {
      const schedulesFor = (p: MirroredRow) =>
        [
          stateRef.current.playlistSchedules[String(p.id)],
          stateRef.current.weeklySchedules[String(p.id)],
        ].filter(Boolean);
      const targets = stateRef.current.playlists.filter(
        (p) => p.source === source && schedulesFor(p).length,
      );
      const label = autoSyncSourceLabel(source);
      if (!targets.length) {
        toast(`No scheduled ${label} playlists to unschedule`, 'info');
        return;
      }
      const ok = await confirm(
        `Unschedule ${targets.length} ${label} playlist${targets.length === 1 ? '' : 's'}`,
        'Removes the Auto-Sync schedules, hourly and weekly. Mirrored playlists themselves stay.',
      );
      if (!ok) return;
      let done = 0;
      let failed = 0;
      for (const playlist of targets) {
        for (const schedule of schedulesFor(playlist)) {
          try {
            const res = await deleteAutomation(schedule.automation_id);
            if (!res.ok) throw new Error(`HTTP ${res.status}`);
            done += 1;
          } catch {
            failed += 1;
          }
        }
      }
      toast(
        `Removed ${done} schedule${done === 1 ? '' : 's'}${failed ? ` (${failed} failed)` : ''}`,
        failed ? 'warning' : 'success',
      );
      await refresh();
    },
    [refresh],
  );

  return {
    state,
    loading,
    loadError,
    now: clock,
    historyFilter,
    setHistoryFilter: (f: AutoSyncHistoryFilter) => {
      setHistoryFilter(autoSyncNormalizeHistoryFilter(f));
    },
    loadMoreHistory: () => {
      setHistoryLimit((l) => autoSyncNextHistoryLimit(l));
    },
    refresh,
    saveHourly,
    saveWeekly,
    unscheduleHourly: (id: number) => unscheduleOne(id, 'hourly'),
    unscheduleWeekly: (id: number) => unscheduleOne(id, 'weekly'),
    runNow,
    setOrganize,
    bulkSchedule,
    bulkUnschedule,
    /** The poller's mid-drag skip (2347). */
    setDragging: (value: boolean) => {
      dragging.current = value;
    },
  };
}
