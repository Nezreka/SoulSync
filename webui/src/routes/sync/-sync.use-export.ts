import { appURL } from '@/platform/url-base';
/**
 * The export job controller — _startPlaylistExport / _pollPlaylistExport /
 * _setExportStatus (stats-automations.js 731-819) as one hook.
 *
 * The vanilla's status "store" is the DOM: _setExportStatus finds the card's
 * .card-meta and injects a .export-status-span into it, and its auto-hide timer
 * closes over THAT span element. Here the statuses are state keyed by playlist
 * id and the tab renders them; the auto-hide is a timer that clears the entry.
 *
 * Status timers and responses belong to a revision. Superseded jobs cannot
 * erase newer progress, download a stale result or emit a stale success toast.
 *
 * DECLARED DIVERGENCE: in the vanilla, refreshing the list re-renders the cards
 * and destroys the span, so 'Update list' silently wipes a live export status.
 * Here the status outlives a refresh. Wiping it would also blank a job that is
 * still polling, which is worse than the vanilla's accident.
 */

import { useCallback, useEffect, useRef, useState } from 'react';

import type { ExportMode, ExportStatusLine } from './-sync.export';

import { fetchPlaylistExportStatus, startPlaylistExport } from './-sync.api';
import {
  EXPORT_POLL_MS,
  EXPORT_POLL_RETRY_MS,
  EXPORT_START_ERROR_STATUS,
  EXPORT_STARTING_STATUS,
  exportPollOutcome,
  exportStartOutcome,
} from './-sync.export';

export interface ExportController {
  /** The live status line per mirrored-playlist id. */
  statuses: Record<number, ExportStatusLine>;
  /** Paint a status directly (the gated-choice nudge, 699). */
  paint: (playlistId: number, status: ExportStatusLine) => void;
  /** POST the job and poll it to a terminal phase. */
  start: (playlistId: number, mode: ExportMode, backfill: boolean) => Promise<void>;
}

export function useExportJobs(): ExportController {
  const [statuses, setStatuses] = useState<Record<number, ExportStatusLine>>({});

  // Every PENDING timer this hook owns, so unmount can clear the lot. The
  // vanilla has no teardown at all — its timers die with the page. Handles are
  // discarded as they fire, so a long export cannot accumulate one entry per
  // second for its whole run.
  const timers = useRef(new Set<ReturnType<typeof setTimeout>>());
  const alive = useRef(true);
  const operations = useRef<Record<number, number>>({});
  const paints = useRef<Record<number, number>>({});

  useEffect(() => {
    alive.current = true;
    const pending = timers.current;
    return () => {
      alive.current = false;
      pending.forEach(clearTimeout);
      pending.clear();
      operations.current = {};
    };
  }, []);

  const later = useCallback((fn: () => void, ms: number) => {
    const handle = setTimeout(() => {
      timers.current.delete(handle);
      fn();
    }, ms);
    timers.current.add(handle);
  }, []);

  const clear = useCallback((playlistId: number) => {
    setStatuses((current) => {
      if (!(playlistId in current)) return current;
      const next = { ...current };
      delete next[playlistId];
      return next;
    });
  }, []);

  const paint = useCallback(
    (playlistId: number, status: ExportStatusLine) => {
      const revision = (paints.current[playlistId] ?? 0) + 1;
      paints.current[playlistId] = revision;
      setStatuses((current) => ({ ...current, [playlistId]: status }));
      if (status.autoHideMs) {
        later(() => {
          if (alive.current && paints.current[playlistId] === revision) clear(playlistId);
        }, status.autoHideMs);
      }
    },
    [later, clear],
  );

  /**
   * One poll tick, scheduling itself by NAME — the vanilla's recursive
   * setTimeout (803), not an interval, so a slow response can never stack
   * ticks. A named function expression can recurse without a ref, which keeps
   * this out of the render pass entirely.
   */
  const poll = useCallback(
    async function tick(
      jobId: string,
      playlistId: number,
      mode: ExportMode,
      operation: number,
    ): Promise<void> {
      if (!alive.current || operations.current[playlistId] !== operation) return;
      try {
        const data = await fetchPlaylistExportStatus(jobId);
        if (!alive.current || operations.current[playlistId] !== operation) return;
        const outcome = exportPollOutcome(data.job || {}, mode, jobId);
        // The .jspf hand-off goes FIRST — the vanilla navigates before it
        // paints the "Downloaded" line (784-785).
        if (outcome.downloadUrl) window.location.href = appURL(outcome.downloadUrl);
        if (outcome.status) paint(playlistId, outcome.status);
        if (outcome.toast) window.showToast?.(outcome.toast.message, outcome.toast.type);
        if (outcome.terminal) return;
        later(() => void tick(jobId, playlistId, mode, operation), EXPORT_POLL_MS);
      } catch {
        // Connectivity trouble is distinct from an export failure.
        if (!alive.current || operations.current[playlistId] !== operation) return;
        paint(playlistId, { text: 'Reconnecting to export status...', color: '#f59e0b' });
        later(() => void tick(jobId, playlistId, mode, operation), EXPORT_POLL_RETRY_MS);
      }
    },
    [paint, later],
  );

  const start = useCallback(
    async (playlistId: number, mode: ExportMode, backfill: boolean) => {
      const operation = (operations.current[playlistId] ?? 0) + 1;
      operations.current[playlistId] = operation;
      paint(playlistId, EXPORT_STARTING_STATUS);
      try {
        const data = await startPlaylistExport(playlistId, mode, backfill);
        if (!alive.current || operations.current[playlistId] !== operation) return;
        const outcome = exportStartOutcome(data);
        if (outcome.status) paint(playlistId, outcome.status);
        // Fire-and-forget, as the vanilla does (750) — the poll owns its own
        // errors, and awaiting it would keep `start` pending for the whole job.
        if (outcome.jobId) void poll(outcome.jobId, playlistId, mode, operation);
      } catch {
        if (!alive.current || operations.current[playlistId] !== operation) return;
        paint(playlistId, EXPORT_START_ERROR_STATUS);
      }
    },
    [paint, poll],
  );

  return { statuses, paint, start };
}
