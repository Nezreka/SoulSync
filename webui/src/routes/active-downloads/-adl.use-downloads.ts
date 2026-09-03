import { useCallback, useEffect, useMemo, useRef, useState } from 'react';

import type { RateSample } from './-adl.helpers';
import type { AdlBatch, AdlBatchHistoryEntry, AdlDownload, AdlFilter } from './-adl.types';

import { fetchBatchHistory, fetchDownloads } from './-adl.api';
import {
  ADL_BATCH_HISTORY_POLL_MS,
  ADL_FILTER_STATUSES,
  ADL_POLL_MS,
  ADL_QUARANTINE_EVERY_N_POLLS,
  ADL_REVIEW_STATUSES,
  BATCH_FADE_SECONDS,
} from './-adl.types';

export interface AdlDownloadsState {
  downloads: AdlDownload[];
  batches: AdlBatch[];
  batchHistory: AdlBatchHistoryEntry[];
  filter: AdlFilter;
  /** When set, the list shows only this batch. */
  filterBatchId: string | null;
  /** False until the first fetch settles, so the empty state does not flash. */
  loaded: boolean;
}

export interface AdlDownloadsController {
  state: AdlDownloadsState;
  /** Rows after the batch filter and the status filter, in server order. */
  visible: AdlDownload[];
  counts: {
    active: number;
    queued: number;
    failed: number;
    total: number;
    completedOrFailed: number;
  };
  hasRunningWork: boolean;
  setFilter: (filter: AdlFilter) => void;
  toggleBatchFilter: (batchId: string) => void;
  refresh: () => Promise<void>;
  refreshHistory: () => Promise<void>;
  /** Batches still worth a card, oldest terminal ones already faded out. */
  visibleBatches: AdlBatch[];
  /** 0..1 opacity for a terminal batch mid-fade; 1 for everything else. */
  batchOpacity: (batchId: string, phase: string) => number;
  /** ETA sample store, keyed by batch — handed to batchEta by the panel. */
  rateSamplesFor: (batchId: string) => RateSample[];
}

const TERMINAL_PHASES = ['complete', 'cancelled', 'error'];

function isTerminal(phase: string): boolean {
  return TERMINAL_PHASES.includes(phase);
}

/**
 * The downloads list, its batches, and the timers that keep them fresh.
 *
 * Three cadences, all the vanilla's: downloads every 2s, batch history every
 * 60s, and a quarantine refresh on every 7th downloads poll (~15s) so entries
 * created mid-batch appear without a click. The vanilla's pollers checked
 * `currentPage` and cleared themselves; a React route unmounts instead, so the
 * effect cleanup is what stops them.
 *
 * It deliberately does NOT touch the nav badge. `/api/downloads/all` is capped
 * at 300 rows, so counting this array would UNDER-report on a big queue — the
 * WebSocket status push owns that number and already maintains it from the
 * real server-side count. The vanilla has a comment saying exactly this, and
 * `_adlUpdateBadge` was left in place but called from nowhere.
 */
export function useAdlDownloads({
  onQuarantineRefresh,
}: {
  /** Fired every 7th poll so the verification controller can reload. */
  onQuarantineRefresh?: () => void;
} = {}): AdlDownloadsController {
  const [state, setState] = useState<AdlDownloadsState>({
    downloads: [],
    batches: [],
    batchHistory: [],
    filter: 'all',
    filterBatchId: null,
    loaded: false,
  });

  const pollCountRef = useRef(0);
  const quarantineRef = useRef(onQuarantineRefresh);
  quarantineRef.current = onQuarantineRefresh;

  /** batch_id → when it was FIRST seen terminal, for the fade-out. */
  const completedAtRef = useRef<Record<string, number>>({});
  /** batch_id → progress samples, for the client-side ETA. */
  const rateSamplesRef = useRef<Record<string, RateSample[]>>({});

  const refresh = useCallback(async () => {
    const data = await fetchDownloads();
    setState((prev) => ({
      ...prev,
      // A failed poll returns {} — keep what is on screen rather than blanking
      // the page for one bad response.
      downloads: data.downloads ?? prev.downloads,
      batches: data.batches ?? prev.batches,
      loaded: true,
    }));
  }, []);

  const refreshHistory = useCallback(async () => {
    const history = await fetchBatchHistory();
    setState((prev) => ({ ...prev, batchHistory: history }));
  }, []);

  useEffect(() => {
    void refresh();
    void refreshHistory();

    const downloadsTimer = setInterval(() => {
      // A backgrounded tab kept pulling the full 300-row payload every 2s
      // (and the quarantine tick behind it runs a server-side filesystem
      // scan) — the dashboard cards hidden-gate their polls, this one never
      // did (perf sweep, Aug 2026). Skipped ticks catch up on the first
      // tick after the tab is visible again.
      if (document.hidden) return;
      void refresh();
      pollCountRef.current += 1;
      if (pollCountRef.current % ADL_QUARANTINE_EVERY_N_POLLS === 0) {
        quarantineRef.current?.();
      }
    }, ADL_POLL_MS);

    const historyTimer = setInterval(() => {
      if (document.hidden) return;
      void refreshHistory();
    }, ADL_BATCH_HISTORY_POLL_MS);

    return () => {
      clearInterval(downloadsTimer);
      clearInterval(historyTimer);
    };
  }, [refresh, refreshHistory]);

  const setFilter = useCallback((filter: AdlFilter) => {
    setState((prev) => ({ ...prev, filter }));
  }, []);

  /** Clicking the active batch's filter button clears it — it is a toggle. */
  const toggleBatchFilter = useCallback((batchId: string) => {
    setState((prev) => ({
      ...prev,
      filterBatchId: prev.filterBatchId === batchId ? null : batchId,
    }));
  }, []);

  /**
   * Rows for the list.
   *
   * Batch filter first, then status — the order matters for the counts, which
   * are computed from the UNFILTERED set below.
   */
  const visible = useMemo(() => {
    let rows = state.downloads;
    if (state.filterBatchId) {
      rows = rows.filter((d) => d.batch_id === state.filterBatchId);
    }
    if (state.filter === 'unverified') {
      return rows.filter(
        (d) =>
          ADL_FILTER_STATUSES.completed.includes(d.status) &&
          ADL_REVIEW_STATUSES.includes(String(d.verification_status)),
      );
    }
    const statuses = ADL_FILTER_STATUSES[state.filter];
    return statuses ? rows.filter((d) => statuses.includes(d.status)) : rows;
  }, [state.downloads, state.filter, state.filterBatchId]);

  /**
   * Header counts.
   *
   * From the whole list, NOT the filtered view — the header reports what is
   * happening overall, so switching to Completed must not make "3 active"
   * disappear.
   *
   * `completedOrFailed` drives the Clear Completed button, and counts failed
   * rows too: clear-completed wipes the persisted history tail as well, so
   * after a restart the list can be entirely completed/failed rows and the
   * button has to stay reachable.
   */
  const counts = useMemo(() => {
    const active = state.downloads.filter((d) =>
      ADL_FILTER_STATUSES.active.includes(d.status),
    ).length;
    const queued = state.downloads.filter((d) =>
      ADL_FILTER_STATUSES.queued.includes(d.status),
    ).length;
    const failed = state.downloads.filter((d) =>
      ADL_FILTER_STATUSES.failed.includes(d.status),
    ).length;
    const completedOrFailed = state.downloads.filter(
      (d) =>
        ADL_FILTER_STATUSES.completed.includes(d.status) ||
        ADL_FILTER_STATUSES.failed.includes(d.status),
    ).length;
    return { active, queued, failed, total: state.downloads.length, completedOrFailed };
  }, [state.downloads]);

  /** Cancel All only appears when there is something cancellable. */
  const hasRunningWork = useMemo(
    () =>
      state.downloads.some(
        (d) =>
          ADL_FILTER_STATUSES.active.includes(d.status) ||
          ADL_FILTER_STATUSES.queued.includes(d.status),
      ),
    [state.downloads],
  );

  /**
   * Batch cards, minus terminal ones that have been finished long enough.
   *
   * A batch that comes back to life has its timestamp cleared, so a retried
   * batch does not inherit a fade that was already in progress.
   */
  const visibleBatches = useMemo(() => {
    const now = Date.now();
    return state.batches.filter((batch) => {
      if (!isTerminal(batch.phase)) {
        delete completedAtRef.current[batch.batch_id];
        return true;
      }
      completedAtRef.current[batch.batch_id] ??= now;
      const elapsed = (now - completedAtRef.current[batch.batch_id]) / 1000;
      return elapsed < BATCH_FADE_SECONDS;
    });
  }, [state.batches]);

  /** Fade begins at 60% of the window, matching the vanilla's easing. */
  const batchOpacity = useCallback((batchId: string, phase: string) => {
    if (!isTerminal(phase)) return 1;
    const since = completedAtRef.current[batchId];
    if (!since) return 1;
    const elapsed = (Date.now() - since) / 1000;
    const fadeStart = BATCH_FADE_SECONDS * 0.6;
    if (elapsed <= fadeStart) return 1;
    const progress = Math.min(1, (elapsed - fadeStart) / (BATCH_FADE_SECONDS - fadeStart));
    return 1 - progress;
  }, []);

  const rateSamplesFor = useCallback((batchId: string) => {
    rateSamplesRef.current[batchId] ??= [];
    return rateSamplesRef.current[batchId];
  }, []);

  return {
    state,
    visible,
    counts,
    hasRunningWork,
    setFilter,
    toggleBatchFilter,
    refresh,
    refreshHistory,
    visibleBatches,
    batchOpacity,
    rateSamplesFor,
  };
}
