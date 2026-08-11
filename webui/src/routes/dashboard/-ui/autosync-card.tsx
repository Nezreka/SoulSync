/**
 * The Auto Sync card (dash-card data-card="autosync") — the mini,
 * read-and-trigger view of the Auto-Sync schedule board, beside Recent Syncs.
 *
 * Data: the same three endpoints the board loads (/api/mirrored-playlists,
 * /api/automations, /api/playlist-pipeline/history) fed through the board's
 * OWN state builder via the window seam (buildAutoSyncScheduleState,
 * auto-sync.js:471) so schedule semantics live in one place. The card renders
 * rich rows from -dash.autosync's pure core: source brand chip, ownership
 * coverage bar (in_library/total from the batched status counts), last-run
 * outcome + library delta from the run snapshots, live pipeline phase +
 * progress while a run is in flight, and a countdown to the next firing.
 *
 * Actions are deliberately thin: Run Now fires the row's automation
 * (/api/automations/<id>/run — the action IS the pipeline), Manage opens the
 * full board modal (openAutoSyncScheduleModal builds its own overlay on
 * document.body, so it works right here without navigating to the sync page).
 * Creating/editing schedules stays in the modal — this card never grows a
 * second implementation of the board.
 *
 * No steady-state HTTP poller (the request-flood rule): fetch on mount, on
 * window focus, after a Run Now, when the board modal closes — plus a short
 * refresh loop ONLY while a pipeline is running (progress has to move). The
 * minute tick is render-only (countdowns re-derive from cached state).
 */

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';

import type { AutoSyncCardRow, AutoSyncSeamState } from '../-dash.autosync';

import { autoSyncCardRows } from '../-dash.autosync';

type Phase = 'loading' | 'ready' | 'error';

function useAutoSyncCard() {
  const [phase, setPhase] = useState<Phase>('loading');
  const [seam, setSeam] = useState<AutoSyncSeamState | null>(null);
  const [nowMs, setNowMs] = useState(() => Date.now());
  const [runningId, setRunningId] = useState<number | string | null>(null);
  const mountedRef = useRef(true);

  const load = useCallback(async () => {
    try {
      const [playlistsRes, automationsRes, historyRes] = await Promise.all([
        fetch('/api/mirrored-playlists'),
        fetch('/api/automations'),
        fetch('/api/playlist-pipeline/history?limit=40'),
      ]);
      if (!playlistsRes.ok || !automationsRes.ok || !historyRes.ok) throw new Error('load failed');
      const playlists = (await playlistsRes.json()) as unknown[];
      const automations = (await automationsRes.json()) as unknown[];
      const history = (await historyRes.json()) as Record<string, unknown>;
      if (!Array.isArray(playlists) || !Array.isArray(automations)) throw new Error('bad shape');

      const build = window.buildAutoSyncScheduleState;
      if (typeof build !== 'function') throw new Error('board seam missing');
      const state = build(playlists, automations, history) as unknown as AutoSyncSeamState;

      if (!mountedRef.current) return;
      setSeam(state);
      setNowMs(Date.now());
      setPhase('ready');
    } catch {
      if (!mountedRef.current) return;
      setPhase('error');
    }
  }, []);

  useEffect(() => {
    mountedRef.current = true;
    void load();
    const onFocus = () => void load();
    window.addEventListener('focus', onFocus);
    // Countdown tick — render-only, no network.
    const tick = window.setInterval(() => {
      if (mountedRef.current) setNowMs(Date.now());
    }, 60_000);
    return () => {
      mountedRef.current = false;
      window.removeEventListener('focus', onFocus);
      window.clearInterval(tick);
    };
  }, [load]);

  const rows = useMemo(() => (seam ? autoSyncCardRows(seam, nowMs) : []), [seam, nowMs]);

  // While a pipeline is in flight its phase/progress must move — refresh on a
  // short loop that exists ONLY while a running row is present.
  const anyRunning = rows.some((r) => r.running);
  useEffect(() => {
    if (!anyRunning) return;
    const h = window.setInterval(() => void load(), 4000);
    return () => window.clearInterval(h);
  }, [anyRunning, load]);

  const runNow = useCallback(
    async (row: AutoSyncCardRow) => {
      setRunningId(row.automationId);
      try {
        const res = await fetch(`/api/automations/${row.automationId}/run`, { method: 'POST' });
        const data = (await res.json().catch(() => ({}))) as { error?: string };
        if (!res.ok) {
          window.showToast?.(data.error || `Could not run ${row.name}`, 'error');
        } else {
          window.showToast?.(`${row.name} pipeline started`, 'success');
        }
      } catch {
        window.showToast?.(`Could not run ${row.name}`, 'error');
      } finally {
        if (mountedRef.current) setRunningId(null);
        // The run registers its pipeline state async — refresh shortly after
        // (the running-row loop takes over from there).
        setTimeout(() => {
          if (mountedRef.current) void load();
        }, 1500);
      }
    },
    [load],
  );

  return { phase, rows, runningId, runNow, reload: load };
}

function openBoard(reload: () => void) {
  void window.openAutoSyncScheduleModal?.();
  // The modal is vanilla and has no close event — watch for its overlay to
  // leave the DOM and refetch, so schedule edits show up on the card.
  const watch = window.setInterval(() => {
    if (!document.getElementById('auto-sync-schedule-modal')) {
      window.clearInterval(watch);
      reload();
    }
  }, 1000);
}

function SourceChip({ row }: { row: AutoSyncCardRow }) {
  const [failed, setFailed] = useState(false);
  const glyph = (row.source || row.name || '?').charAt(0).toUpperCase();
  return (
    <span className="autosync-chip" title={row.source || undefined}>
      {row.logo && !failed ? (
        <img
          className={`autosync-chip-logo autosync-chip-logo--${row.sourceKey}`}
          src={row.logo}
          alt=""
          onError={() => setFailed(true)}
        />
      ) : (
        <span className="autosync-chip-glyph">{glyph}</span>
      )}
    </span>
  );
}

function Row({
  row,
  busy,
  onRun,
}: {
  row: AutoSyncCardRow;
  busy: boolean;
  onRun: (row: AutoSyncCardRow) => void;
}) {
  const classes = ['autosync-row'];
  if (!row.enabled) classes.push('autosync-row--off');
  if (row.running) classes.push('autosync-row--live');

  return (
    <div className={classes.join(' ')}>
      <SourceChip row={row} />
      <span className="autosync-row-main">
        <span className="autosync-row-top">
          <span className="autosync-row-name" title={row.name}>
            {row.name}
          </span>
          {row.running ? (
            <span className="autosync-row-live-pill">running</span>
          ) : row.enabled && row.nextRun ? (
            <span className="autosync-row-next">{row.nextRun}</span>
          ) : !row.enabled ? (
            <span className="autosync-row-paused">paused</span>
          ) : null}
        </span>
        {row.running ? (
          <span className="autosync-row-meta autosync-row-phase" title={row.running.phase}>
            {row.running.phase}
          </span>
        ) : (
          <span className="autosync-row-meta">
            <span>{row.cadence}</span>
            {row.lastRun && row.lastRun.ago ? (
              <span
                className={
                  row.lastRun.ok ? 'autosync-row-last' : 'autosync-row-last autosync-row-last--bad'
                }
                title={row.lastRun.ok ? 'Last run completed' : 'Last run failed'}
              >
                {row.lastRun.ok ? '' : '⚠ '}
                {row.lastRun.delta ? `${row.lastRun.delta} · ` : ''}
                {row.lastRun.ago}
              </span>
            ) : null}
          </span>
        )}
        {row.running ? (
          <span className="autosync-cov">
            <span className="autosync-cov-bar">
              <span
                className="autosync-cov-fill autosync-cov-fill--live"
                style={{ width: `${row.running.progress}%` }}
              ></span>
            </span>
            <span className="autosync-cov-text">{row.running.progress}%</span>
          </span>
        ) : row.coverage ? (
          <span
            className="autosync-cov"
            title={`${row.coverage.inLibrary} of ${row.coverage.total} tracks in your library`}
          >
            <span className="autosync-cov-bar">
              <span className="autosync-cov-fill" style={{ width: `${row.coverage.pct}%` }}></span>
            </span>
            <span className="autosync-cov-text">
              {row.coverage.inLibrary}/{row.coverage.total} owned
            </span>
          </span>
        ) : null}
      </span>
      {!row.running ? (
        <button
          type="button"
          className="autosync-run-btn"
          disabled={busy}
          onClick={() => onRun(row)}
          title="Run this pipeline now"
        >
          {busy ? '…' : 'Run'}
        </button>
      ) : null}
    </div>
  );
}

export function AutoSyncCard() {
  const { phase, rows, runningId, runNow, reload } = useAutoSyncCard();
  const scheduled = rows.filter((r) => r.enabled).length;

  return (
    <article className="dash-card" data-card="autosync">
      <header className="dash-card__head">
        <h3 className="dash-card__title">
          Auto Sync
          {phase === 'ready' && rows.length > 0 ? (
            <span className="autosync-count-pill">{scheduled} scheduled</span>
          ) : null}
        </h3>
        <p className="dash-card__sub">Playlists that keep themselves in sync.</p>
        <button type="button" className="autosync-manage-btn" onClick={() => openBoard(reload)}>
          Manage
        </button>
      </header>
      <div className="dash-card__body">
        {phase === 'loading' ? (
          <div className="autosync-empty">Loading schedules…</div>
        ) : phase === 'error' ? (
          <div className="autosync-empty">Could not load the Auto-Sync schedule.</div>
        ) : rows.length === 0 ? (
          <div className="autosync-empty">
            <strong>No playlists on a schedule yet</strong>
            <span>
              Auto-Sync refreshes a playlist, hunts its missing tracks, and pushes it to your
              server — on a schedule you set.
            </span>
            <button type="button" className="autosync-empty-cta" onClick={() => openBoard(reload)}>
              Set one up
            </button>
          </div>
        ) : (
          <div className="autosync-rows">
            {rows.map((row) => (
              <Row
                key={`${row.key}-${row.automationId}`}
                row={row}
                busy={runningId !== null && runningId === row.automationId}
                onRun={(r) => void runNow(r)}
              />
            ))}
          </div>
        )}
      </div>
    </article>
  );
}
