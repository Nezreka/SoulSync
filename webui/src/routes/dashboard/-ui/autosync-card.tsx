/**
 * The Auto Sync card (dash-card data-card="autosync") — the mini,
 * read-and-trigger view of the Auto-Sync schedule board, beside Recent Syncs.
 *
 * Data: the same three endpoints the board loads (/api/mirrored-playlists,
 * /api/automations, /api/playlist-pipeline/history) fed through the board's
 * OWN state builder via the window seam (buildAutoSyncScheduleState,
 * auto-sync.js:471) so schedule semantics live in one place. The card renders
 * compact rows from -dash.autosync's pure core.
 *
 * Actions are deliberately thin: Run Now fires the row's automation
 * (/api/automations/<id>/run — the action IS the pipeline), Manage opens the
 * full board modal (openAutoSyncScheduleModal builds its own overlay on
 * document.body, so it works right here without navigating to the sync page).
 * Creating/editing schedules stays in the modal — this card never grows a
 * second implementation of the board.
 *
 * No poller (the request-flood rule): fetch on mount, after a Run Now, and
 * when the board modal closes (ss:autosync-board-closed would be ideal but
 * doesn't exist — the modal's close re-render seam is its removal, so the
 * card simply refetches on window focus, the cheap idiom the vanilla
 * dashboard used for db stats).
 */

import { useCallback, useEffect, useRef, useState } from 'react';

import type { AutoSyncCardRow, AutoSyncSeamState } from '../-dash.autosync';

import { autoSyncCardRows } from '../-dash.autosync';

type Phase = 'loading' | 'ready' | 'error';

function useAutoSyncCard() {
  const [phase, setPhase] = useState<Phase>('loading');
  const [rows, setRows] = useState<AutoSyncCardRow[]>([]);
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
      setRows(autoSyncCardRows(state, Date.now()));
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
    return () => {
      mountedRef.current = false;
      window.removeEventListener('focus', onFocus);
    };
  }, [load]);

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
        // The run lands in history/next_run async — refresh shortly after.
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
        <button
          type="button"
          className="autosync-manage-btn"
          onClick={() => openBoard(reload)}
        >
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
              <div
                key={`${row.key}-${row.automationId}`}
                className={row.enabled ? 'autosync-row' : 'autosync-row autosync-row--off'}
              >
                <span
                  className={`autosync-health autosync-health--${row.health}`}
                  title={
                    row.health === 'good'
                      ? 'Last run completed'
                      : row.health === 'bad'
                        ? 'Last run failed'
                        : 'No runs recorded yet'
                  }
                ></span>
                <span className="autosync-row-main">
                  <span className="autosync-row-name" title={row.name}>
                    {row.name}
                  </span>
                  <span className="autosync-row-meta">
                    {row.source ? <span className="autosync-row-source">{row.source}</span> : null}
                    <span>{row.cadence}</span>
                    {row.enabled && row.nextRun ? (
                      <span className="autosync-row-next">{row.nextRun}</span>
                    ) : null}
                    {!row.enabled ? <span className="autosync-row-next">paused</span> : null}
                  </span>
                </span>
                <button
                  type="button"
                  className="autosync-run-btn"
                  disabled={runningId !== null}
                  onClick={() => void runNow(row)}
                  title="Run this pipeline now"
                >
                  {runningId === row.automationId ? '…' : 'Run'}
                </button>
              </div>
            ))}
          </div>
        )}
      </div>
    </article>
  );
}
