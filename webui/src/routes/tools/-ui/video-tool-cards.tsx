/**
 * the video side's tool cards, on the music tools chassis.
 *
 * these were vanilla cards inside the video tools subpage (video-scan.js,
 * video-server-scan.js and the tail of video-repair.js). same endpoints, same
 * confirmations, same toasts. two things worth knowing:
 *
 * - the library scan is STARTED through video-scan.js ('soulsync:video-scan-
 *   start'), which is the one scan controller the library page and dashboard
 *   also follow. this card never writes the data-video-scan-* nodes that
 *   file still looks for: a vanilla writer inside a react tree is the trap
 *   the music tools port hit. it reads status on its own poll instead.
 * - studios, backups and rename are admin-only on the server, so they are
 *   not shown to anyone else rather than shown and left to 403.
 */

import { useCallback, useEffect, useRef, useState } from 'react';

import type {
  RenamePreview,
  VideoBackupList,
  VideoLibraryStats,
  VideoMediaTarget,
  VideoScanMode,
  VideoScanStatus,
} from '../-video-tools.api';

import { timeAgo } from '../-tools.core';
import {
  applyRenames,
  backupSizeLabel,
  cancelVideoRestore,
  createVideoBackup,
  fetchRenamePreviewStatus,
  fetchServerScanStatus,
  fetchVideoBackups,
  fetchVideoLibraryStats,
  fetchVideoScanStatus,
  formatLibrarySize,
  mediaTargetLabel,
  requestServerScan,
  scanCountsLabel,
  serverLabel,
  stageVideoRestore,
  startRenamePreview,
  stopVideoScan,
  videoBackupDownloadUrl,
} from '../-video-tools.api';
import { ToolCard, ToolProgress } from './tool-card';

function toast(message: string, type = 'info') {
  window.showToast?.(message, type);
}

const SCAN_POLL_MS = 1500;
const SERVER_POLL_MS = 2000;
const RENAME_POLL_MS = 1500;

function useLibraryStats(refreshToken: number): VideoLibraryStats | null {
  const [stats, setStats] = useState<VideoLibraryStats | null>(null);
  useEffect(() => {
    let cancelled = false;
    void fetchVideoLibraryStats().then((next) => {
      if (!cancelled && next) setStats(next);
    });
    return () => {
      cancelled = true;
    };
  }, [refreshToken]);
  return stats;
}

// ── Library scan ─────────────────────────────────────────────────────────────

function isScanning(status: VideoScanStatus | null): boolean {
  return status?.state === 'scanning';
}

/** the phase line and the detail line, from one status frame. */
export function videoScanProgressText(status: VideoScanStatus | null): {
  phase: string;
  details: string;
  percent: number;
} {
  if (!status || status.state === 'idle') return { phase: 'Idle', details: '', percent: 0 };
  if (status.state === 'scanning') {
    const phase = status.phase || 'scanning';
    const pct = status.percent;
    return {
      phase: phase.charAt(0).toUpperCase() + phase.slice(1),
      details: scanCountsLabel(status) + (pct != null ? ` · ${pct}%` : ''),
      // a real percentage when we know the total, else a full bar as "working"
      percent: pct != null ? pct : 100,
    };
  }
  if (status.state === 'error') {
    return { phase: 'Failed', details: status.error || 'Scan failed', percent: 0 };
  }
  if (status.state === 'cancelled') {
    return { phase: 'Cancelled', details: `${scanCountsLabel(status)} (cancelled)`, percent: 0 };
  }
  return {
    phase: 'Complete',
    details: scanCountsLabel(status) + (status.removed ? `, ${status.removed} removed` : ''),
    percent: 100,
  };
}

export function VideoLibraryScanCard() {
  const [statsToken, setStatsToken] = useState(0);
  const stats = useLibraryStats(statsToken);
  const [status, setStatus] = useState<VideoScanStatus | null>(null);
  const [mode, setMode] = useState<VideoScanMode>('incremental');
  const [target, setTarget] = useState<VideoMediaTarget>('all');
  const [cancelling, setCancelling] = useState(false);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const wasScanning = useRef(false);

  const poll = useCallback(async () => {
    const next = await fetchVideoScanStatus();
    if (!next) return;
    setStatus(next);
    if (isScanning(next)) {
      wasScanning.current = true;
      timer.current = setTimeout(() => void poll(), SCAN_POLL_MS);
      return;
    }
    setCancelling(false);
    if (wasScanning.current) {
      // a scan just landed: the counts on the card are stale now
      wasScanning.current = false;
      setStatsToken((token) => token + 1);
    }
  }, []);

  useEffect(() => {
    void poll();
    // video-scan.js announces scans started anywhere (library page, dashboard)
    const onProgress = () => {
      if (timer.current) return;
      timer.current = setTimeout(() => {
        timer.current = null;
        void poll();
      }, 200);
    };
    document.addEventListener('soulsync:video-scan-progress', onProgress);
    document.addEventListener('soulsync:video-scan-done', onProgress);
    return () => {
      document.removeEventListener('soulsync:video-scan-progress', onProgress);
      document.removeEventListener('soulsync:video-scan-done', onProgress);
      if (timer.current) clearTimeout(timer.current);
      timer.current = null;
    };
  }, [poll]);

  const scanning = isScanning(status);

  const onRun = useCallback(() => {
    if (scanning) {
      setCancelling(true);
      void stopVideoScan();
      return;
    }
    // optimistic, so the button flips at once; the poll confirms
    setStatus({ state: 'scanning', phase: 'starting', media_type: target, percent: 0 });
    document.dispatchEvent(
      new CustomEvent('soulsync:video-scan-start', { detail: { mode, media_type: target } }),
    );
    if (timer.current) clearTimeout(timer.current);
    timer.current = setTimeout(() => {
      timer.current = null;
      void poll();
    }, 800);
  }, [mode, poll, scanning, target]);

  const progress = videoScanProgressText(status);
  const server = serverLabel(stats?.server);
  const lastScan = status?.last_scan_at ? timeAgo(status.last_scan_at) : 'Never';

  return (
    <ToolCard
      id="video-library-scan-card"
      title={server ? `${server} Library Scan` : 'Library Scan'}
      helpTool="video-scan"
      info={`Last full scan: ${lastScan}`}
      stats={[
        { label: 'Movies:', value: (stats?.movies ?? 0).toLocaleString() },
        { label: 'Shows:', value: (stats?.shows ?? 0).toLocaleString() },
        { label: 'Episodes:', value: (stats?.episodes ?? 0).toLocaleString() },
        { label: 'Size:', value: formatLibrarySize(stats?.sizeBytes ?? 0) },
      ]}
      controls={
        <>
          <select
            aria-label="Which library to scan"
            title="Movies and TV are separate libraries, scan one or both"
            value={target}
            disabled={scanning}
            onChange={(event) => setTarget(event.target.value as VideoMediaTarget)}
          >
            <option value="all">All Libraries</option>
            <option value="movie">Movies Only</option>
            <option value="show">TV Shows Only</option>
          </select>
          <select
            aria-label="Scan type"
            value={mode}
            disabled={scanning}
            onChange={(event) => setMode(event.target.value as VideoScanMode)}
          >
            <option value="incremental">Incremental Update</option>
            <option value="full">Full Refresh</option>
            <option value="deep">Deep Scan</option>
          </select>
          <button type="button" disabled={cancelling} onClick={onRun}>
            {scanning ? (cancelling ? 'Cancelling…' : 'Cancel') : 'Scan Library'}
          </button>
        </>
      }
      progress={
        <ToolProgress
          phase={cancelling ? 'Cancelling…' : progress.phase}
          details={progress.details}
          percent={progress.percent}
          phaseId="video-scan-phase"
          barId="video-scan-bar"
          detailsId="video-scan-detail"
        />
      }
    />
  );
}

// ── Server scan ──────────────────────────────────────────────────────────────

export function VideoServerScanCard() {
  const [statsToken] = useState(0);
  const stats = useLibraryStats(statsToken);
  const [target, setTarget] = useState<VideoMediaTarget>('all');
  const [scanning, setScanning] = useState(false);
  const [phase, setPhase] = useState('Idle');
  const [details, setDetails] = useState('');
  const [percent, setPercent] = useState(0);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);

  const done = useCallback((nextPhase: string, nextDetails: string) => {
    if (timer.current) clearTimeout(timer.current);
    timer.current = null;
    setScanning(false);
    setPhase(nextPhase);
    setDetails(nextDetails);
    setPercent(nextPhase === 'Complete' ? 100 : 0);
  }, []);

  // the server reports no percentage, so the bar is a full "working" bar
  // while it scans and settles when it says it's done
  const poll = useCallback(
    async (mediaType: VideoMediaTarget) => {
      const status = await fetchServerScanStatus(mediaType);
      if (!status || status.scanning === false) {
        done('Complete', `${mediaTargetLabel(mediaType)} indexed`);
        return;
      }
      if (status.scanning === null) {
        done('Scan started', "Server can't report progress");
        return;
      }
      setPhase('Scanning…');
      setDetails(`Server is indexing ${mediaTargetLabel(mediaType)}…`);
      setPercent(100);
      timer.current = setTimeout(() => void poll(mediaType), SERVER_POLL_MS);
    },
    [done],
  );

  // already mid-scan when the page opened: pick it back up
  useEffect(() => {
    let cancelled = false;
    void fetchServerScanStatus('all').then((status) => {
      if (cancelled || !status || status.scanning !== true) return;
      setScanning(true);
      setPhase('Scanning…');
      setPercent(100);
      timer.current = setTimeout(() => void poll('all'), 1200);
    });
    return () => {
      cancelled = true;
      if (timer.current) clearTimeout(timer.current);
      timer.current = null;
    };
  }, [poll]);

  const start = useCallback(async () => {
    if (scanning) return;
    const mediaType = target;
    setScanning(true);
    setPhase('Starting…');
    setDetails(`Asking the server to scan ${mediaTargetLabel(mediaType)}…`);
    setPercent(100);
    const result = await requestServerScan(mediaType);
    if (result && result.ok === false) {
      done('Failed', result.error || 'Server scan failed');
      return;
    }
    timer.current = setTimeout(() => void poll(mediaType), 800);
  }, [done, poll, scanning, target]);

  const server = serverLabel(stats?.server);

  return (
    <ToolCard
      id="video-server-scan-card"
      title={server ? `${server} Server Scan` : 'Server Scan'}
      helpTool="video-server-scan"
      info="Tell the media server to scan its folders for newly-downloaded movies and episodes, then a Library Scan pulls them into SoulSync."
      controls={
        <>
          <select
            aria-label="Which library the server should scan"
            title="Movies and TV are separate server libraries, scan one or both"
            value={target}
            disabled={scanning}
            onChange={(event) => setTarget(event.target.value as VideoMediaTarget)}
          >
            <option value="all">All Libraries</option>
            <option value="movie">Movies Only</option>
            <option value="show">TV Shows Only</option>
          </select>
          <button type="button" disabled={scanning} onClick={() => void start()}>
            {scanning ? 'Scanning…' : 'Scan Server'}
          </button>
        </>
      }
      progress={
        <ToolProgress
          phase={phase}
          details={details}
          percent={percent}
          phaseId="video-srvscan-phase"
          barId="video-srvscan-bar"
          detailsId="video-srvscan-detail"
        />
      }
    />
  );
}

// ── Backups ──────────────────────────────────────────────────────────────────

export function VideoBackupsCard() {
  const [list, setList] = useState<VideoBackupList | null>(null);
  const [creating, setCreating] = useState(false);

  const load = useCallback(async () => {
    const next = await fetchVideoBackups();
    if (next) setList(next);
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const create = useCallback(async () => {
    setCreating(true);
    try {
      const result = await createVideoBackup();
      if (result?.success) {
        toast('Backup created', 'success');
        void load();
      } else {
        toast(result?.error || 'Backup failed', 'error');
      }
    } catch {
      toast('Backup failed', 'error');
    } finally {
      setCreating(false);
    }
  }, [load]);

  const restore = useCallback(
    async (name: string) => {
      const confirmed = await window.showConfirmDialog?.({
        title: 'Restore this backup?',
        message:
          'On the next restart, the video database is swapped for this backup. Your current database is set aside (kept), so this can be undone.',
        confirmText: 'Stage restore',
        destructive: true,
      });
      if (!confirmed) return;
      const result = await stageVideoRestore(name);
      if (result?.success) {
        toast('Restore staged. Restart SoulSync to apply', 'success');
        void load();
      } else {
        toast(result?.error || 'Could not stage the restore', 'error');
      }
    },
    [load],
  );

  const cancelRestore = useCallback(async () => {
    await cancelVideoRestore();
    void load();
  }, [load]);

  return (
    <ToolCard
      id="video-backups-card"
      title="Backups"
      helpTool="video-backups"
      info="Verified snapshots of the video database (wishlist, watchlist, history, settings). The nightly automation keeps a rolling set. Restore stages a swap for the next restart, and your current database is set aside, never deleted."
      controls={
        <button type="button" disabled={creating} onClick={() => void create()}>
          {creating ? 'Backing up…' : 'Back up now'}
        </button>
      }
    >
      {list?.pending_restore ? (
        <div className="vbk-pending">
          A restore is staged. It applies on the next restart.
          <button type="button" className="vbk-cancel" onClick={() => void cancelRestore()}>
            Cancel
          </button>
        </div>
      ) : null}
      <div className="vbk-list">
        {list === null ? null : list.backups.length ? (
          list.backups.map((backup) => (
            <div className="vbk-row" key={backup.name}>
              <span className="vbk-name">
                {(backup.created_at || '').replace('T', ' ')} · {backupSizeLabel(backup.size_bytes)}
              </span>
              <a
                className="vbk-btn"
                href={videoBackupDownloadUrl(backup.name)}
                title="Download this backup"
                aria-label="Download this backup"
              >
                ⭳
              </a>
              <button
                type="button"
                className="vbk-btn vbk-btn--restore"
                onClick={() => void restore(backup.name)}
              >
                Restore
              </button>
            </div>
          ))
        ) : (
          <div className="vbk-empty">
            No backups yet. The nightly automation makes them, or hit "Back up now".
          </div>
        )}
      </div>
    </ToolCard>
  );
}

// ── Mass rename ──────────────────────────────────────────────────────────────

export function VideoMassRenameCard() {
  const [preview, setPreview] = useState<RenamePreview | null>(null);
  const [scanning, setScanning] = useState(false);
  const [applying, setApplying] = useState(false);
  const [doneNote, setDoneNote] = useState<string | null>(null);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(
    () => () => {
      if (timer.current) clearTimeout(timer.current);
    },
    [],
  );

  const settle = useCallback((result: RenamePreview) => {
    setScanning(false);
    setPreview(result);
    if (!result.success) toast('Rename preview failed', 'error');
  }, []);

  // the preview runs on a server worker (resolving every path is minutes on a
  // big library), so it's started and then polled for a live count
  const poll = useCallback(async () => {
    const result = await fetchRenamePreviewStatus();
    if (result.success && !result.ready) {
      setPreview(result);
      timer.current = setTimeout(() => void poll(), RENAME_POLL_MS);
      return;
    }
    settle(result);
  }, [settle]);

  const runPreview = useCallback(async () => {
    setDoneNote(null);
    setScanning(true);
    setPreview(null);
    const result = await startRenamePreview();
    if (result.success && !result.ready) {
      setPreview(result);
      timer.current = setTimeout(() => void poll(), RENAME_POLL_MS);
      return;
    }
    settle(result);
  }, [poll, settle]);

  const apply = useCallback(async () => {
    const confirmed = await window.showConfirmDialog?.({
      title: 'Rename these files on disk?',
      message:
        'Every previewed file is moved to its template name. Sidecars travel along; occupied destinations are skipped, never overwritten.',
      confirmText: 'Rename',
      destructive: true,
    });
    if (!confirmed) return;
    setApplying(true);
    try {
      const result = await applyRenames();
      if (result?.success) {
        toast(
          `${result.renamed ?? 0} file(s) renamed${result.skipped ? `, ${result.skipped} skipped` : ''}`,
          'success',
        );
        setPreview(null);
        setDoneNote('Done. Run a library scan so the server re-adopts the names.');
      } else {
        toast(result?.error || 'Rename failed', 'error');
      }
    } catch {
      toast('Rename failed', 'error');
    } finally {
      setApplying(false);
    }
  }, []);

  const entries = preview?.success && preview.ready !== false ? preview.entries || [] : [];
  const ready = Boolean(preview?.success && preview.ready !== false);

  return (
    <ToolCard
      id="video-rename-card"
      title="Mass Rename"
      helpTool="video-rename"
      info="Re-render every file name to your current naming templates (Settings → Organization). Preview first: nothing moves until you apply. Subtitles and sidecars travel with their file, and the server re-adopts the names on its next scan."
      controls={
        <>
          <button type="button" disabled={scanning} onClick={() => void runPreview()}>
            {scanning ? 'Scanning…' : 'Preview'}
          </button>
          {ready && entries.length ? (
            <button type="button" disabled={applying} onClick={() => void apply()}>
              {applying ? 'Renaming…' : 'Apply renames'}
            </button>
          ) : null}
        </>
      }
    >
      <div className="vbk-list">
        {doneNote ? <div className="vbk-empty">{doneNote}</div> : null}
        {scanning && preview?.success && !preview.ready ? (
          <div className="vbk-empty">
            Scanning your library…
            {preview.total ? ` (${preview.done || 0} / ${preview.total})` : ''}
          </div>
        ) : scanning ? (
          <div className="vbk-empty">Scanning your library…</div>
        ) : null}
        {preview && !preview.success && !scanning ? (
          <div className="vbk-empty">
            Preview failed{preview.error ? `: ${preview.error}` : ''}. Check the app log for
            details.
          </div>
        ) : null}
        {ready && !scanning ? (
          entries.length ? (
            <>
              <div className="vbk-row">
                <span className="vbk-name">
                  <strong>{entries.length} file(s)</strong> differ from your templates
                  {preview?.unresolved ? ` · ${preview.unresolved} unresolved path(s) skipped` : ''}
                </span>
              </div>
              {entries.slice(0, 8).map((entry) => (
                <div className="vbk-row" key={`${entry.current}->${entry.proposed}`}>
                  <span className="vbk-name" title={`${entry.current} → ${entry.proposed}`}>
                    {entry.title}
                  </span>
                </div>
              ))}
              {entries.length > 8 ? (
                <div className="vbk-empty">…and {entries.length - 8} more</div>
              ) : null}
            </>
          ) : preview?.unresolved ? (
            // empty can mean "already tidy" OR "couldn't resolve a single
            // path": say which, or the second reads as a no-op
            <div className="vbk-empty">
              No renames: none of your {preview.unresolved} file path(s) could be resolved to a file
              on disk (check Settings → Organization library paths).
            </div>
          ) : (
            <div className="vbk-empty">Everything already matches your naming templates.</div>
          )
        ) : null}
      </div>
    </ToolCard>
  );
}

// ── Studios ──────────────────────────────────────────────────────────────────

/**
 * the two visual editors, launchable from here as well as the dashboard. the
 * buttons carry the same data attributes the dashboard's launchers do, and
 * video-side.js opens the editor from a document-level click handler, so
 * there is exactly one way each studio opens.
 */
export function VideoStudioCards() {
  return (
    <>
      <ToolCard
        id="video-overlay-studio-card"
        title="Overlay Studio"
        helpTool="video-overlay-studio"
        variant="launcher"
        info="Design poster overlays (ratings, resolution, awards…) in a visual editor and apply them to your whole library: filtered targeting, nightly re-apply, clean-art restore."
        controls={
          <button type="button" data-video-overlay-studio>
            Open Overlay Studio
          </button>
        }
      />
      <ToolCard
        id="video-collection-studio-card"
        title="Collection Studio"
        helpTool="video-collection-studio"
        variant="launcher"
        info="Build living collections: charts, franchises, universes, seasonal windows, smart rules, with generated posters, server sync and wishlist tie-ins."
        controls={
          <button type="button" data-video-collection-studio>
            Open Collection Studio
          </button>
        }
      />
    </>
  );
}
