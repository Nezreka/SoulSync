/**
 * The three long-running cards in "Database & Scanning" that share a
 * progress-section shape: Database Updater, Import IDs from File Tags, and
 * Duplicate Cleaner.
 *
 * Every label and threshold below is transcribed from the vanilla UI updaters
 * (`updateDbProgressUI`, `updateReconcileIdsProgressUI`,
 * `updateDuplicateCleanProgressUI`). The button text is load-bearing in the
 * vanilla — it doubles as the state machine, and one handler still branches on
 * it — so the exact strings matter.
 */

import { useCallback, useEffect, useRef, useState } from 'react';

import type {
  DatabaseStats,
  DbRefreshType,
  DbUpdateState,
  DuplicateCleanState,
  ReconcileIdsState,
} from '../-tools.types';

import {
  fetchDatabaseStats,
  fetchDbUpdateStatus,
  fetchDuplicateCleanStatus,
  fetchReconcileIdsStatus,
  startDatabaseUpdate,
  startDuplicateClean,
  startReconcileIds,
  stopDatabaseUpdate,
  stopDuplicateClean,
} from '../-tools.api';
import { formatFreedSpace } from '../-tools.core';
import { usePolledStatus, useSafetyPoll } from '../-tools.polling';
import { ToolCard, ToolProgress } from './tool-card';

const ACCENT_BAR = 'rgb(var(--accent-rgb))';
const ERROR_BAR = '#ff4444';

function toast(message: string, type = 'info') {
  window.showToast?.(message, type);
}

const isRunning = (state: { status?: string }) => state.status === 'running';

// ── Database Updater ─────────────────────────────────────────────────────────

/**
 * `handleDbUpdateButtonClick` + `updateDbProgressUI` + `updateDbUpdaterCardInfo`.
 *
 * The button deliberately stays ENABLED while it reads "Starting…" so a wedged
 * start is still clickable — a second click falls through to the stop branch,
 * so there is no double-start risk (#859).
 */
export function DbUpdaterCard() {
  const [refreshType, setRefreshType] = useState<DbRefreshType>('incremental');
  const [stats, setStats] = useState<DatabaseStats | null>(null);
  const [starting, setStarting] = useState(false);

  const loadStats = useCallback(async () => {
    const next = await fetchDatabaseStats();
    if (next) setStats(next);
  }, []);

  // `initializeToolsPage` hydrated these on load — without it the card renders
  // a confident 0 artists / 0.00 MB over a real library until a run finishes.
  useEffect(() => {
    void loadStats();
  }, [loadStats]);

  const poll = usePolledStatus<DbUpdateState>({
    fetcher: fetchDbUpdateStatus,
    isRunning,
    intervalMs: 1000,
    onState: (state) => {
      setStarting(false);
      // Final stats refresh once the run lands, exactly as the vanilla does.
      if (state.status === 'finished' || state.status === 'error') {
        setTimeout(() => void loadStats(), 500);
      }
    },
  });

  const safety = useSafetyPoll<DbUpdateState>(fetchDbUpdateStatus, isRunning, () =>
    setStarting(false),
  );

  const state = poll.state;
  const running = Boolean(state && isRunning(state));
  // 'Starting…' also counts as busy for the label, but the button stays live.
  const buttonLabel = running ? 'Stop Update' : starting ? 'Starting...' : 'Update Database';

  const onClick = useCallback(async () => {
    if (buttonLabel === 'Update Database') {
      if (refreshType === 'full') {
        const ok = await window.showConfirmDialog?.({
          title: 'Full Refresh',
          message:
            'This will clear and rebuild the database for the active server. It can take a long time.\n\nAre you sure you want to proceed?',
          confirmText: 'Proceed',
        });
        if (!ok) return;
      } else if (refreshType === 'deep') {
        const ok = await window.showConfirmDialog?.({
          title: 'Deep Scan',
          message:
            'A deep scan re-checks every track in your media server library against the database.\n\n• Adds any new tracks that were missed\n• Removes tracks no longer on your server\n• Preserves all existing metadata and enrichment data\n\nThis may take a while for large libraries. Proceed?',
          confirmText: 'Proceed',
        });
        if (!ok) return;
      }
      setStarting(true);
      const result = await startDatabaseUpdate(refreshType);
      if (!result.ok) {
        setStarting(false);
        toast(`Error: ${result.error}`, 'error');
        return;
      }
      toast('Database update started!', 'success');
      await poll.refresh();
      poll.arm();
      safety.arm();
      return;
    }
    // "Stop Update" — and the "Starting…" fall-through that makes a wedged
    // start cancellable.
    const ok = await stopDatabaseUpdate();
    toast(ok ? 'Stop request sent.' : 'Failed to send stop request.', ok ? 'info' : 'error');
  }, [buttonLabel, poll, refreshType, safety]);

  const processed = state?.processed ?? 0;
  const total = state?.total ?? 0;
  const percent = state?.progress ?? 0;

  let phase = 'Idle';
  let details = '';
  let barPercent = 0;
  let barColor: string | undefined;

  if (running) {
    phase = state?.phase || 'Processing...';
    details = `${processed} / ${total} artists (${percent.toFixed(1)}%)`;
    barPercent = percent;
  } else if (state?.status === 'error') {
    phase = `Error: ${state.error_message}`;
    barColor = ERROR_BAR;
  } else if (state) {
    phase = state.phase || 'Idle';
    barColor = ACCENT_BAR;
    // finished keeps a full bar; every other terminal state clears it, so a
    // completed run doesn't leave a frozen partial bar behind (#859).
    if (state.status === 'finished') barPercent = 100;
  }

  const serverName = stats?.server_source
    ? stats.server_source.charAt(0).toUpperCase() + stats.server_source.slice(1)
    : null;

  return (
    <ToolCard
      id="db-updater-card"
      title={serverName ? `${serverName} Database Updater` : 'Database Updater'}
      helpTool="db-updater"
      info={
        <>
          Last Full Refresh:{' '}
          <span id="db-last-refresh">
            {/* Naive LOCAL time from `datetime.now()` — parsed as local, which
                is already correct. Do NOT normalise this one to UTC. */}
            {stats?.last_full_refresh
              ? new Date(stats.last_full_refresh).toLocaleString()
              : 'Never'}
          </span>
        </>
      }
      stats={[
        {
          label: 'Artists:',
          valueId: 'db-stat-artists',
          value: (stats?.artists ?? 0).toLocaleString(),
        },
        {
          label: 'Albums:',
          valueId: 'db-stat-albums',
          value: (stats?.albums ?? 0).toLocaleString(),
        },
        {
          label: 'Tracks:',
          valueId: 'db-stat-tracks',
          value: (stats?.tracks ?? 0).toLocaleString(),
        },
        {
          label: 'Size:',
          valueId: 'db-stat-size',
          value: `${(stats?.database_size_mb ?? 0).toFixed(2)} MB`,
        },
      ]}
      controls={
        <>
          <select
            id="db-refresh-type"
            value={refreshType}
            disabled={running}
            onChange={(event) => setRefreshType(event.target.value as DbRefreshType)}
          >
            <option value="incremental">Incremental Update</option>
            <option value="full">Full Refresh</option>
            <option value="deep">Deep Scan</option>
          </select>
          <button type="button" id="db-update-button" onClick={() => void onClick()}>
            {buttonLabel}
          </button>
        </>
      }
      progress={
        <ToolProgress
          phase={phase}
          details={details}
          percent={barPercent}
          barColor={barColor}
          phaseId="db-phase-label"
          barId="db-progress-bar"
          detailsId="db-progress-label"
        />
      }
    />
  );
}

// ── Import IDs from File Tags ────────────────────────────────────────────────

/** `handleReconcileIdsButtonClick` + `updateReconcileIdsProgressUI`. */
export function ReconcileIdsCard() {
  const [busy, setBusy] = useState(false);
  // The completion toast fires only if we SAW the run in progress — otherwise
  // simply opening the page after a finished scan would announce it again.
  const wasRunningRef = useRef(false);
  const [justFinished, setJustFinished] = useState(false);

  const poll = usePolledStatus<ReconcileIdsState>({
    fetcher: fetchReconcileIdsStatus,
    isRunning,
    intervalMs: 1500,
    onState: (state) => {
      if (isRunning(state)) {
        wasRunningRef.current = true;
        setBusy(true);
        return;
      }
      setBusy(false);
      if (state.status === 'done' && wasRunningRef.current && !justFinished) {
        setJustFinished(true);
        toast(
          `Tag import complete — ${state.ids_filled} IDs filled` +
            (state.conflicts ? `, ${state.conflicts} conflicts skipped` : ''),
          'success',
        );
      }
    },
  });

  const state = poll.state;
  const running = Boolean(state && isRunning(state));

  const onClick = useCallback(async () => {
    // The vanilla guards on the button text; the equivalent here is the busy
    // flag, which covers the same "already running" case.
    if (running || busy) return;
    const ok = await window.showConfirmDialog?.({
      title: 'Import IDs from File Tags',
      message:
        'Scan every library file for embedded provider IDs and fill any that are missing in the database?\n\nEach file is read once. Existing matches are never overwritten. This can take a while on large libraries.',
      confirmText: 'Scan Library',
    });
    if (!ok) return;
    setBusy(true);
    const result = await startReconcileIds();
    if (!result.ok) {
      setBusy(false);
      toast(`Error: ${result.error}`, 'error');
      return;
    }
    toast('Tag import started!', 'success');
    setJustFinished(false);
    await poll.refresh();
    poll.arm();
  }, [busy, poll, running]);

  const processed = state?.processed ?? 0;
  const total = state?.total ?? 0;
  const percent = total > 0 ? (processed / total) * 100 : 0;

  let phase = 'Ready to scan';
  let details = '0 / 0 files scanned (0.0%)';
  let barPercent = 0;

  if (running) {
    phase = state?.current ? `Scanning: ${state.current}` : 'Scanning…';
    details = `${processed} / ${total} files scanned (${percent.toFixed(1)}%)`;
    barPercent = percent;
  } else if (state?.status === 'done') {
    const filled = state.ids_filled ?? 0;
    const updated = state.entities_updated ?? 0;
    phase = `Done — filled ${filled} ID${filled === 1 ? '' : 's'} across ${updated} row${updated === 1 ? '' : 's'}`;
    details = `${processed} / ${total} files scanned (100%)`;
    barPercent = 100;
  }

  return (
    <ToolCard
      id="reconcile-ids-card"
      title="Import IDs from File Tags"
      helpTool="reconcile-ids"
      info={
        <>
          Read provider IDs (Spotify, MusicBrainz, iTunes, Deezer&hellip;) already embedded in your
          files and fill them into the database &mdash; lets enrichment workers skip redundant API
          lookups. Only fills blanks; never overwrites an existing match.
        </>
      }
      stats={[
        { label: 'IDs Filled:', valueId: 'reconcile-stat-filled', value: state?.ids_filled ?? 0 },
        {
          label: 'Rows Updated:',
          valueId: 'reconcile-stat-updated',
          value: state?.entities_updated ?? 0,
        },
        { label: 'Conflicts:', valueId: 'reconcile-stat-conflicts', value: state?.conflicts ?? 0 },
        {
          label: 'Unreadable:',
          valueId: 'reconcile-stat-unreadable',
          value: state?.unreadable ?? 0,
        },
      ]}
      controls={
        <button
          type="button"
          id="reconcile-ids-button"
          disabled={running || busy}
          onClick={() => void onClick()}
        >
          {running || busy ? 'Scanning…' : 'Scan Library'}
        </button>
      }
      progress={
        <ToolProgress
          phase={phase}
          details={details}
          percent={barPercent}
          barColor={running ? undefined : ACCENT_BAR}
          phaseId="reconcile-phase-label"
          barId="reconcile-progress-bar"
          detailsId="reconcile-progress-label"
        />
      }
    />
  );
}

// ── Duplicate Cleaner ────────────────────────────────────────────────────────

/** `handleDuplicateCleanButtonClick` + `updateDuplicateCleanProgressUI`. */
export function DuplicateCleanerCard() {
  const [starting, setStarting] = useState(false);
  const announcedRef = useRef(false);

  const poll = usePolledStatus<DuplicateCleanState>({
    fetcher: fetchDuplicateCleanStatus,
    isRunning,
    intervalMs: 1000,
    onState: (state) => {
      setStarting(false);
      if (isRunning(state)) {
        announcedRef.current = false;
        return;
      }
      if (state.status === 'finished' && !announcedRef.current) {
        announcedRef.current = true;
        // The toast uses ONE decimal for MB; the stat row uses two.
        const freed = formatFreedSpace(state.space_freed_mb || 0, 1);
        toast(`Cleaning complete! ${state.deleted} files removed, ${freed} freed`, 'success');
      }
    },
  });

  const state = poll.state;
  const running = Boolean(state && isRunning(state));

  const onClick = useCallback(async () => {
    if (running) {
      const ok = await stopDuplicateClean();
      toast(ok ? 'Stop request sent.' : 'Failed to send stop request.', ok ? 'info' : 'error');
      return;
    }
    setStarting(true);
    const result = await startDuplicateClean();
    if (!result.ok) {
      setStarting(false);
      toast(`Error: ${result.error}`, 'error');
      return;
    }
    toast('Duplicate cleaner started!', 'success');
    await poll.refresh();
    poll.arm();
  }, [poll, running]);

  const percent = state?.progress ?? 0;

  let phase = 'Ready to scan';
  let details = '0 files scanned (0.0%)';
  let barPercent = 0;
  let barColor: string | undefined;

  if (running) {
    phase = state?.phase || 'Scanning...';
    details = `${state?.files_scanned ?? 0} / ${state?.total_files ?? 0} files scanned (${percent.toFixed(1)}%)`;
    barPercent = percent;
  } else if (state?.status === 'error') {
    phase = `Error: ${state.error_message}`;
    barColor = ERROR_BAR;
  } else if (state) {
    phase = state.phase || 'Ready to scan';
    barColor = ACCENT_BAR;
  }

  return (
    <ToolCard
      id="duplicate-cleaner-card"
      title="Duplicate Cleaner"
      helpTool="duplicate-cleaner"
      info="Detect and remove duplicate tracks in output folder"
      stats={[
        {
          label: 'Files Scanned:',
          valueId: 'duplicate-stat-scanned',
          value: state?.files_scanned ?? 0,
        },
        {
          label: 'Duplicates Found:',
          valueId: 'duplicate-stat-found',
          value: state?.duplicates_found ?? 0,
        },
        { label: 'Deleted:', valueId: 'duplicate-stat-deleted', value: state?.deleted ?? 0 },
        {
          label: 'Space Freed:',
          valueId: 'duplicate-stat-space',
          value: formatFreedSpace(state?.space_freed_mb ?? 0, 2),
        },
      ]}
      controls={
        <button
          type="button"
          id="duplicate-clean-button"
          disabled={starting}
          onClick={() => void onClick()}
        >
          {running ? 'Stop Cleaning' : starting ? 'Starting...' : 'Clean Duplicates'}
        </button>
      }
      progress={
        <ToolProgress
          phase={phase}
          details={details}
          percent={barPercent}
          barColor={barColor}
          phaseId="duplicate-phase-label"
          barId="duplicate-progress-bar"
          detailsId="duplicate-progress-label"
        />
      }
    />
  );
}
