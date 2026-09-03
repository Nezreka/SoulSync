/**
 * The Sync band (dash-card data-card="sync") — Auto Sync and Recent Syncs
 * merged into one full-width section: one row per playlist carrying its art,
 * schedule (cadence + countdown), latest run result, ownership coverage, and
 * live pipeline state. Boulder's call: the two cards were the same system
 * explained twice.
 *
 * Inherits BOTH predecessors' behavior:
 * - schedule side: the board's state builder via the window seam
 *   (buildAutoSyncScheduleState), Manage/empty-CTA opening the real board
 *   modal, Run Now via the board's pipeline run path, a refresh loop only
 *   while a pipeline is in flight, minute countdown tick (render-only);
 * - history side: loadDashboardSyncHistory's 30s cycle with the app-locked
 *   guard and 401 → unlock-screen handling, row click → openSyncDetailModal,
 *   Listen resolving the playlist against the library, delete with the 200ms
 *   fade (manual rows only — a scheduled playlist's history belongs to the
 *   board), and the live "N syncing now" pill.
 *
 * The rows container keeps the #sync-history-cards id — the tour and helper
 * entries anchor to it, and pages-extra.js documents it as React-owned DOM.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';

import type { AutoSyncSeamState } from '../-dash.autosync';
import type { SyncProgressFrame } from '../-dash.events';
import type { SyncBandRow } from '../-dash.syncband';

import { fetchDashboardSyncHistory } from '../-dash.api';
import { autoSyncCardRows } from '../-dash.autosync';
import { useDashboardStatsEvent, useSyncProgressEvent } from '../-dash.events';
import { syncCardView } from '../-dash.library';
import { syncBandRows } from '../-dash.syncband';

type SeamPhase = 'loading' | 'ready' | 'error';
type LiveSync = {
  phase: string;
  progress: number;
  playlistId: string;
  playlistName: string;
  updatedAt: number;
};

function normName(value: string | null | undefined): string {
  return (value || '').trim().toLowerCase();
}

function liveSyncFromFrame(frame: SyncProgressFrame): LiveSync | null {
  const progress = frame.progress || {};
  const playlistId = frame.playlist_id != null ? String(frame.playlist_id) : '';
  const playlistName = frame.playlist_name || progress.playlist_name || '';
  const rawPct = progress.progress;
  const matched = progress.matched_tracks || 0;
  const failed = progress.failed_tracks || 0;
  const total = progress.total_tracks || 0;
  const computed = total > 0 ? Math.round(((matched + failed) / total) * 100) : 0;
  const pct = rawPct != null ? Math.round(rawPct) : computed;
  const step = progress.current_step || frame.status || 'Syncing';
  const track = progress.current_track || '';
  if (!playlistId && !playlistName) return null;
  return {
    playlistId,
    playlistName,
    progress: Math.min(100, Math.max(0, pct)),
    phase: track ? `${step} · ${track}` : step,
    updatedAt: Date.now(),
  };
}

function useSyncBand() {
  const [seam, setSeam] = useState<AutoSyncSeamState | null>(null);
  const [seamPhase, setSeamPhase] = useState<SeamPhase>('loading');
  const [entries, setEntries] = useState<ReturnType<typeof syncCardView>[] | null>(null);
  const [nowMs, setNowMs] = useState(() => Date.now());
  const [busyId, setBusyId] = useState<number | string | null>(null);
  const [fadingIds, setFadingIds] = useState<Set<number | string>>(new Set());
  const [liveSyncs, setLiveSyncs] = useState<Record<string, LiveSync>>({});
  const mountedRef = useRef(true);

  const loadSchedule = useCallback(async () => {
    // Same app-locked guard as the history cycle — every endpoint here is
    // auth-gated, and a locked tab shouldn't probe any of them.
    if (document.body.classList.contains('app-locked')) return;
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
      setSeamPhase('ready');
    } catch {
      if (mountedRef.current) setSeamPhase('error');
    }
  }, []);

  const loadHistory = useCallback(async () => {
    // The vanilla guard: don't poll the auth-gated endpoint while locked.
    if (document.body.classList.contains('app-locked')) return;
    const result = await fetchDashboardSyncHistory();
    if (result.status === 'unauthorized') {
      if (result.loginRequired && window.showLoginScreen) window.showLoginScreen();
      else window.showLaunchPinScreen?.();
      return;
    }
    if (result.status !== 'ok') return; // failures keep the previous rows
    if (!mountedRef.current) return;
    const now = Date.now();
    setEntries(result.entries.map((e) => syncCardView(e, now)));
  }, []);

  const loadAll = useCallback(() => {
    void loadSchedule();
    void loadHistory();
  }, [loadHistory, loadSchedule]);

  useEffect(() => {
    mountedRef.current = true;
    loadAll();
    const historyTimer = setInterval(() => void loadHistory(), 30000);
    const tick = window.setInterval(() => {
      if (mountedRef.current) setNowMs(Date.now());
    }, 60_000);
    const onFocus = () => loadAll();
    window.addEventListener('focus', onFocus);
    return () => {
      mountedRef.current = false;
      clearInterval(historyTimer);
      window.clearInterval(tick);
      window.removeEventListener('focus', onFocus);
    };
  }, [loadAll, loadHistory]);

  const scheduleRows = useMemo(() => (seam ? autoSyncCardRows(seam, nowMs) : []), [seam, nowMs]);
  const rows = useMemo(() => syncBandRows(scheduleRows, entries ?? []), [scheduleRows, entries]);

  const liveForRow = useCallback(
    (row: SyncBandRow): LiveSync | null => {
      const ids = [
        row.last?.playlistId,
        row.last?.id != null ? `history_${row.last.id}` : null,
        row.last?.id != null ? `resync_${row.last.id}` : null,
        row.schedule ? `auto_mirror_${row.schedule.key}` : null,
      ].filter(Boolean) as string[];
      for (const id of ids) {
        const live = liveSyncs[id];
        if (live) return live;
      }
      const rowName = normName(row.name);
      return (
        Object.values(liveSyncs).find((live) => normName(live.playlistName) === rowName) || null
      );
    },
    [liveSyncs],
  );

  useSyncProgressEvent(
    useCallback(
      (frame) => {
        const frames = Array.isArray(frame.syncs) ? frame.syncs : [frame];
        const terminals = frames.some((item) =>
          ['finished', 'complete', 'error', 'cancelled'].includes(item.status || ''),
        );
        setLiveSyncs((prev) => {
          const next = { ...prev };
          for (const item of frames) {
            const live = liveSyncFromFrame(item);
            if (!live) continue;
            const status = item.status || '';
            const key = live.playlistId || normName(live.playlistName);
            if (['finished', 'complete', 'error', 'cancelled'].includes(status)) {
              delete next[key];
            } else {
              next[key] = live;
            }
          }
          return next;
        });
        if (terminals) {
          setTimeout(() => {
            if (mountedRef.current) loadAll();
          }, 800);
        }
      },
      [loadAll],
    ),
  );

  useEffect(() => {
    if (!Object.keys(liveSyncs).length) return;
    const timer = window.setInterval(() => {
      const now = Date.now();
      let removed = false;
      setLiveSyncs((prev) => {
        const next = { ...prev };
        for (const [key, live] of Object.entries(next)) {
          if (now - live.updatedAt > 8000) {
            delete next[key];
            removed = true;
          }
        }
        return removed ? next : prev;
      });
      if (removed && mountedRef.current) loadAll();
    }, 2000);
    return () => window.clearInterval(timer);
  }, [liveSyncs, loadAll]);

  // While a pipeline runs its phase/progress must move — a short loop that
  // exists ONLY while a running row is present.
  const anyRunning = scheduleRows.some((r) => r.running) || Object.keys(liveSyncs).length > 0;
  useEffect(() => {
    if (!anyRunning) return;
    const h = window.setInterval(() => loadAll(), 4000);
    return () => window.clearInterval(h);
  }, [anyRunning, loadAll]);

  const runNow = useCallback(
    async (row: SyncBandRow) => {
      const sched = row.schedule;
      if (!sched) return;
      setBusyId(sched.automationId);
      try {
        // The board's run path — the ONLY one that registers the pipeline
        // progress state the board modal and this band's running UI read.
        const res = await fetch(`/api/mirrored-playlists/${sched.key}/pipeline/run`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({}),
        });
        const data = (await res.json().catch(() => ({}))) as { error?: string };
        if (!res.ok) window.showToast?.(data.error || `Could not run ${row.name}`, 'error');
        else window.showToast?.(`${row.name} pipeline started`, 'success');
      } catch {
        window.showToast?.(`Could not run ${row.name}`, 'error');
      } finally {
        if (mountedRef.current) setBusyId(null);
        setTimeout(() => {
          if (mountedRef.current) loadAll();
        }, 1500);
      }
    },
    [loadAll],
  );

  /** Sync again — a manual row re-triggered from its history entry's cached
   *  track list (GET /api/sync/history/<id> is documented "for re-trigger")
   *  through the same /api/sync/start the sync page uses. The snapshot is
   *  the tracks AS OF that run — honest for "retry this sync"; a playlist
   *  that changed upstream still wants the sync page (or a schedule). */
  const syncAgain = useCallback(
    async (row: SyncBandRow) => {
      const id = row.last?.id;
      if (id === undefined) return;
      setBusyId(`resync-${id}`);
      try {
        const res = await fetch(`/api/sync/history/${id}`);
        const data = (await res.json()) as {
          success?: boolean;
          entry?: {
            playlist_id?: string;
            playlist_name?: string;
            thumb_url?: string;
            tracks?: unknown[];
          };
        };
        const entry = data.success ? data.entry : null;
        const tracks = entry?.tracks;
        if (!entry || !Array.isArray(tracks) || tracks.length === 0) {
          window.showToast?.('No cached tracks for that run', 'error');
          return;
        }
        const startRes = await fetch('/api/sync/start', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            playlist_id: entry.playlist_id || `history_${id}`,
            playlist_name: entry.playlist_name || row.name,
            tracks,
            image_url: entry.thumb_url || '',
            sync_mode: '', // empty = the Settings default, like the sync page
          }),
        });
        const started = (await startRes.json().catch(() => ({}))) as {
          success?: boolean;
          error?: string;
        };
        if (!started.success) throw new Error(started.error || 'failed');
        window.showToast?.(`Sync started for "${row.name}"`, 'success');
      } catch {
        window.showToast?.(`Could not resync ${row.name}`, 'error');
      } finally {
        if (mountedRef.current) setBusyId(null);
        setTimeout(() => {
          if (mountedRef.current) loadAll();
        }, 1500);
      }
    },
    [loadAll],
  );

  /** Listen — the playlist resolved against the LIBRARY server-side. */
  const listen = useCallback(async (id: number | string, name: string) => {
    try {
      window.showToast?.(`Loading ${name}…`, 'info');
      const resp = await fetch(`/api/sync/history/${id}/play`);
      const data = (await resp.json()) as {
        success?: boolean;
        error?: string;
        name?: string;
        total?: number;
        tracks?: unknown[];
        queue_tracks?: unknown[];
      };
      if (!data.success) throw new Error(data.error || 'failed');
      const tracks = data.queue_tracks || data.tracks || [];
      if (!tracks.length) {
        window.showToast?.('That playlist has no usable track metadata', 'info');
        return;
      }
      const ownedCount = data.tracks?.length || 0;
      if (data.total && ownedCount < data.total) {
        window.showToast?.(
          `Queued ${data.total} tracks — preloading ${data.total - ownedCount} missing`,
          'info',
        );
      }
      await window.playTrackList?.(tracks, data.name || name);
    } catch {
      window.showToast?.('Could not load that playlist', 'error');
    }
  }, []);

  /** Delete a manual run's history entry — the 200ms fade, state-driven. */
  const removeEntry = useCallback(async (id: number | string) => {
    setFadingIds((prev) => new Set(prev).add(id));
    const unfade = () =>
      setFadingIds((prev) => {
        const next = new Set(prev);
        next.delete(id);
        return next;
      });
    try {
      const resp = await fetch(`/api/sync/history/${id}`, { method: 'DELETE' });
      if (resp.ok) {
        setTimeout(() => {
          setEntries((prev) => (prev ? prev.filter((v) => v.id !== id) : prev));
          unfade();
        }, 200);
      } else {
        unfade();
      }
    } catch {
      unfade();
    }
  }, []);

  return {
    seamPhase,
    entries,
    rows,
    busyId,
    fadingIds,
    runNow,
    syncAgain,
    listen,
    removeEntry,
    loadAll,
    liveForRow,
  };
}

function openBoard(reload: () => void) {
  void window.openAutoSyncScheduleModal?.();
  const watch = window.setInterval(() => {
    if (!document.getElementById('auto-sync-schedule-modal')) {
      window.clearInterval(watch);
      reload();
    }
  }, 1000);
}

function RowArt({ row }: { row: SyncBandRow }) {
  const [thumbFailed, setThumbFailed] = useState(false);
  const [logoFailed, setLogoFailed] = useState(false);
  const glyph = (row.name || '?').charAt(0).toUpperCase();
  return (
    <div className="syncband-art">
      {row.thumbUrl && !thumbFailed ? (
        <img src={row.thumbUrl} alt="" loading="lazy" onError={() => setThumbFailed(true)} />
      ) : (
        <span className="syncband-art-glyph">{glyph}</span>
      )}
      {row.logo && !logoFailed ? (
        <img
          className={`syncband-art-badge syncband-art-badge--${row.sourceKey}`}
          src={row.logo}
          alt=""
          title={row.sourceLabel}
          onError={() => setLogoFailed(true)}
        />
      ) : null}
    </div>
  );
}

/** Exported for tests: the row is where the click behaviour lives. */
export function Row({
  row,
  busy,
  fading,
  live,
  onRun,
  onSyncAgain,
  onListen,
  onRemove,
}: {
  row: SyncBandRow;
  busy: boolean;
  fading: boolean;
  live: LiveSync | null;
  onRun: (row: SyncBandRow) => void;
  onSyncAgain: (row: SyncBandRow) => void;
  onListen: (id: number | string, name: string) => void;
  onRemove: (id: number | string) => void;
}) {
  const sched = row.schedule;
  const running = sched?.running ?? live;
  const classes = ['syncband-row'];
  if (running) classes.push('syncband-row--live');
  if (sched && !sched.enabled) classes.push('syncband-row--off');

  const lastId = row.last?.id;
  // A scheduled row can always open ITS PLAYLIST, run or no run. It used to
  // open only the sync-detail modal, keyed on a history entry, so a playlist
  // with no run in the fetched window was simply inert: clicking Discover
  // Weekly did nothing while Release Radar opened (Boulder, Aug 2026). The
  // schedule's board key IS the mirrored playlist id.
  const playlistId = sched ? Number(sched.key) : NaN;
  const openPlaylist = Number.isFinite(playlistId) && playlistId > 0;
  const clickable = lastId !== undefined || openPlaylist;

  const onOpen = () => {
    // The run detail is the better answer when there is one: it shows what that
    // sync actually did. The playlist is the fallback, not the preference.
    if (lastId !== undefined) void window.openSyncDetailModal?.(Number(lastId));
    else if (openPlaylist) void window.openMirroredPlaylistModal?.(playlistId);
  };

  return (
    <div
      className={classes.join(' ')}
      style={fading ? { opacity: 0, transform: 'scale(0.97)' } : undefined}
      onClick={clickable ? onOpen : undefined}
      role={clickable ? 'button' : undefined}
    >
      <RowArt row={row} />

      <div className="syncband-main">
        <span className="syncband-name" title={row.name}>
          {row.name}
        </span>
        <span className="syncband-sub">
          {sched ? sched.cadence : row.sourceLabel}
          {sched && row.sourceLabel ? ` · ${row.sourceLabel}` : ''}
          {!sched && row.last?.typeLabel ? ` · ${row.last.typeLabel}` : ''}
        </span>
      </div>

      <div className="syncband-result">
        {running ? (
          <span className="syncband-phase" title={running.phase}>
            {running.phase}
          </span>
        ) : row.last ? (
          <>
            {/* No download chip. A successful download is ALREADY on this row —
                it is what made the coverage bar fill, so "⬇ 8" sitting beside
                "8/10 in library" read as the same fact stated twice. The bar is
                the better of the two: it carries the total as well as the win.
                The failure chip stays because nothing else on the row conveys
                it, and it is the only number here you would act on. (Same
                reasoning retired the "matched" chip earlier.) */}
            {row.last.failed > 0 ? (
              <span className="syncband-chip syncband-chip--fail">✗ {row.last.failed}</span>
            ) : null}
            <span className="syncband-ago">{row.last.timeStr}</span>
          </>
        ) : (
          <span className="syncband-ago syncband-ago--none">no runs yet</span>
        )}
      </div>

      <div className="syncband-owned">
        {running ? (
          <>
            <span className="syncband-owned-bar">
              <span
                className="syncband-owned-fill syncband-owned-fill--live"
                style={{ width: `${running.progress}%` }}
              ></span>
            </span>
            <span className="syncband-owned-text">{running.progress}%</span>
          </>
        ) : row.coverage ? (
          <>
            <span
              className="syncband-owned-bar"
              title={
                row.last
                  ? `${row.coverage.inLibrary} of ${row.coverage.total} tracks matched at the last sync`
                  : `${row.coverage.inLibrary} of ${row.coverage.total} tracks in your library`
              }
            >
              <span
                className="syncband-owned-fill"
                style={{ width: `${row.coverage.pct}%` }}
              ></span>
            </span>
            <span className="syncband-owned-text">
              {row.coverage.inLibrary}/{row.coverage.total} in library
            </span>
          </>
        ) : null}
      </div>

      <div className="syncband-when">
        {running ? (
          <span className="syncband-live-pill">running</span>
        ) : sched ? (
          sched.enabled ? (
            <span className="syncband-next">{sched.nextRun ?? ''}</span>
          ) : (
            <span className="syncband-paused">paused</span>
          )
        ) : (
          <span className="syncband-manual">manual</span>
        )}
      </div>

      <div className="syncband-actions" onClick={(e) => e.stopPropagation()}>
        {sched && !running ? (
          <button
            type="button"
            className="syncband-btn"
            disabled={busy}
            title="Run this pipeline now"
            onClick={() => onRun(row)}
          >
            {busy ? '…' : 'Run'}
          </button>
        ) : null}
        {row.kind === 'manual' && lastId !== undefined && row.last?.typeLabel !== 'album' ? (
          <button
            type="button"
            className="syncband-btn"
            disabled={busy}
            title="Sync this playlist again from its last track list"
            onClick={() => onSyncAgain(row)}
          >
            {busy ? '…' : 'Sync'}
          </button>
        ) : null}
        {lastId !== undefined ? (
          <button
            type="button"
            className="syncband-btn"
            title="Listen — play this playlist from your library"
            onClick={() => onListen(lastId, row.name)}
          >
            ▶
          </button>
        ) : null}
        {row.kind === 'manual' && lastId !== undefined ? (
          <button
            type="button"
            className="syncband-btn syncband-btn--x"
            title="Remove from history"
            onClick={() => onRemove(lastId)}
          >
            ×
          </button>
        ) : null}
      </div>
    </div>
  );
}

export function SyncBand() {
  const {
    seamPhase,
    entries,
    rows,
    busyId,
    fadingIds,
    runNow,
    syncAgain,
    listen,
    removeEntry,
    loadAll,
    liveForRow,
  } = useSyncBand();

  const scheduled = rows.filter((r) => r.schedule?.enabled).length;
  const [activeSyncs, setActiveSyncs] = useState(0);
  useDashboardStatsEvent(
    useCallback((data) => {
      if (typeof data.active_syncs === 'number') setActiveSyncs(data.active_syncs);
    }, []),
  );

  const loaded = seamPhase !== 'loading' || entries !== null;

  return (
    <article className="dash-card" data-card="sync">
      <header className="dash-card__head">
        <h3 className="dash-card__title">
          Sync
          {scheduled > 0 ? (
            <span className="autosync-count-pill">{scheduled} scheduled</span>
          ) : null}
          {activeSyncs > 0 ? (
            <span className="dash-syncs-live">{activeSyncs} syncing now</span>
          ) : null}
        </h3>
        <p className="dash-card__sub">
          Your playlists — what&apos;s scheduled, what ran, what you own.
        </p>
        <button type="button" className="autosync-manage-btn" onClick={() => openBoard(loadAll)}>
          Manage
        </button>
      </header>
      <div className="dash-card__body">
        <div className="syncband-rows" id="sync-history-cards">
          {!loaded ? null : rows.length === 0 ? (
            <div className="autosync-empty">
              <strong>Nothing syncing yet</strong>
              <span>
                Auto-Sync refreshes a playlist, hunts its missing tracks, and pushes it to your
                server — on a schedule you set.
              </span>
              <button
                type="button"
                className="autosync-empty-cta"
                onClick={() => openBoard(loadAll)}
              >
                Set one up
              </button>
            </div>
          ) : (
            rows.map((row) => (
              <Row
                key={row.rowKey}
                row={row}
                busy={
                  busyId !== null &&
                  (busyId === row.schedule?.automationId ||
                    (row.last?.id !== undefined && busyId === `resync-${row.last.id}`))
                }
                fading={row.last?.id !== undefined && fadingIds.has(row.last.id)}
                live={liveForRow(row)}
                onRun={(r) => void runNow(r)}
                onSyncAgain={(r) => void syncAgain(r)}
                onListen={(id, name) => void listen(id, name)}
                onRemove={(id) => void removeEntry(id)}
              />
            ))
          )}
        </div>
      </div>
    </article>
  );
}
