/**
 * The two server-conditional cards and the backup manager.
 *
 * Media Server Scan shows for Plex only; the Metadata Updater shows for Plex
 * AND Jellyfin (Navidrome supports neither image upload nor a manual scan
 * trigger). Both are hidden with an inline `display:none` in the markup and
 * revealed by the media-server check, so that is what is reproduced here.
 *
 * IMPORTANT — the media scan carries the FIXED behaviour from the bugfix PR,
 * not the original:
 *
 *   - the completion path re-enables the button. The vanilla websocket handler
 *     looked up `media-scan-btn`, which is the CLASS; the id is
 *     `media-scan-button`, so it never re-enabled anything and the button died
 *     after one click.
 *   - the poll is BOUNDED. The vanilla incremented its counter after an early
 *     return, so on a socket-connected client the counter never advanced, the
 *     clearInterval was unreachable and one 2s timer leaked per click.
 *
 * Here the poll counts every tick and the hook clears it on unmount, so neither
 * can come back.
 *
 * P6 HANDOFF: the vanilla `updateMediaScanFromData` (media-player.js) is still
 * bound to `scan:media` and writes straight into #media-scan-phase-label /
 * -progress-bar / -status — ids this component now owns. Until P6 rewires the
 * socket, both can write the same nodes. It is not harmful (they agree on the
 * text) but it must be severed with the rest of the socket wiring, not left.
 */

import { useCallback, useEffect, useRef, useState } from 'react';

import type { BackupEntry, BackupListResponse, MetadataUpdateState } from '../-tools.types';

import {
  createBackup,
  deleteBackup as deleteBackupRequest,
  fetchActiveMediaServer,
  fetchBackups,
  fetchMediaScanStatus,
  fetchMetadataUpdateStatus,
  backupDownloadUrl,
  requestMediaScan,
  restoreBackup as restoreBackupRequest,
  startMetadataUpdate,
  stopMetadataUpdate,
} from '../-tools.api';
import { backupSummary, backupTimestamp } from '../-tools.core';
import { usePolledStatus } from '../-tools.polling';
import { ToolCard, ToolProgress } from './tool-card';

/**
 * Matches the vanilla's call ARITY, not just its arguments: most call sites pass
 * two args and the media-scan ones pass an explicit duration. Always passing a
 * third `undefined` would be invisible at runtime but is a real difference in
 * what the shell's showToast receives.
 */
function toast(message: string, type = 'info', duration?: number) {
  if (duration === undefined) window.showToast?.(message, type);
  else window.showToast?.(message, type, duration);
}

/** Shared by both conditional cards; null until the check resolves. */
function useActiveMediaServer(): string | null {
  const [server, setServer] = useState<string | null>(null);
  useEffect(() => {
    let cancelled = false;
    void fetchActiveMediaServer().then((next) => {
      if (!cancelled) setServer(next);
    });
    return () => {
      cancelled = true;
    };
  }, []);
  return server;
}

// ── Media Server Scan (Plex only) ────────────────────────────────────────────

const SCAN_POLL_MS = 2000;
const SCAN_MAX_POLLS = 150; // 5 minutes
const IDLE_STATUS_COLOR = '#b3b3b3';
const ERROR_STATUS_COLOR = '#f44336';
const ACTIVE_STATUS_COLOR = 'rgb(var(--accent-rgb))';

export function MediaScanCard() {
  const server = useActiveMediaServer();
  const [busy, setBusy] = useState(false);
  const [lastScan, setLastScan] = useState('Never');
  const [status, setStatus] = useState({ text: 'Idle', color: IDLE_STATUS_COLOR });
  const [phase, setPhase] = useState('Ready to scan');
  const [details, setDetails] = useState('Waiting for scan request');
  const [percent, setPercent] = useState(0);

  const countdownRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const clearTimers = useCallback(() => {
    if (countdownRef.current) {
      clearInterval(countdownRef.current);
      countdownRef.current = null;
    }
    if (pollRef.current) {
      clearInterval(pollRef.current);
      pollRef.current = null;
    }
  }, []);

  // Unmount is what actually bounds these now — the vanilla's own clearInterval
  // was unreachable on a socket-connected client.
  useEffect(() => clearTimers, [clearTimers]);

  const finish = useCallback(() => {
    clearTimers();
    setBusy(false);
    setPhase('Scan completed successfully');
    setPercent(0);
    setDetails('Ready for next scan');
    setStatus({ text: 'Idle', color: IDLE_STATUS_COLOR });
    toast('✅ Media scan completed', 'success', 3000);
  }, [clearTimers]);

  const startPolling = useCallback(() => {
    let polls = 0;
    pollRef.current = setInterval(() => {
      polls += 1;
      if (polls > SCAN_MAX_POLLS) {
        clearTimers();
        setBusy(false);
        setPhase('Scan completed');
        setPercent(0);
        setDetails('Ready for next scan');
        setStatus({ text: 'Idle', color: IDLE_STATUS_COLOR });
        toast('✅ Media scan completed', 'success', 3000);
        return;
      }
      void fetchMediaScanStatus().then((state) => {
        if (!state) return; // a blip is not a result
        if (state.is_scanning) {
          setPhase('Media server scanning...');
          setDetails(state.progress_message || 'Scan in progress');
        } else if (state.status === 'idle') {
          finish();
        }
      });
    }, SCAN_POLL_MS);
  }, [clearTimers, finish]);

  const onClick = useCallback(async () => {
    setBusy(true);
    setPhase('Requesting scan...');
    setPercent(30);
    setDetails('Sending scan request to Plex');
    setStatus({ text: 'Scanning...', color: ACTIVE_STATUS_COLOR });

    try {
      const result = await requestMediaScan('Manual scan triggered from dashboard');
      if (!result.success) {
        toast(`❌ Scan request failed: ${result.error}`, 'error', 5000);
        setBusy(false);
        setPhase('Scan failed');
        setPercent(0);
        setDetails(result.error || 'Unknown error');
        setStatus({ text: 'Error', color: ERROR_STATUS_COLOR });
        return;
      }

      const delaySeconds = result.scan_info?.delay_seconds || 60;
      setLastScan(new Date().toLocaleTimeString());
      setPhase('Scan scheduled...');
      setPercent(0);

      let remaining = delaySeconds;
      countdownRef.current = setInterval(() => {
        remaining -= 1;
        setPercent(((delaySeconds - remaining) / delaySeconds) * 100);
        setDetails(remaining > 0 ? `Starting scan in ${remaining}s...` : 'Scan starting now...');
        if (remaining <= 0) {
          if (countdownRef.current) {
            clearInterval(countdownRef.current);
            countdownRef.current = null;
          }
          setPhase('Scan in progress...');
          setPercent(100);
          setDetails('Media server is scanning library...');
          toast('📡 Media scan started', 'success', 3000);
          startPolling();
        }
      }, 1000);
    } catch (error) {
      toast('❌ Failed to request media scan', 'error', 3000);
      setBusy(false);
      setPhase('Error');
      setPercent(0);
      setDetails(error instanceof Error ? error.message : String(error));
      setStatus({ text: 'Error', color: ERROR_STATUS_COLOR });
    }
  }, [startPolling]);

  return (
    <ToolCard
      id="media-scan-card"
      title="Media Server Scan"
      helpTool="media-scan"
      hidden={server !== 'plex'}
      info="Manually trigger Plex media library scan for music"
      stats={[
        { label: 'Last Scan:', valueId: 'media-scan-last-time', value: lastScan },
        {
          label: 'Status:',
          valueId: 'media-scan-status',
          value: status.text,
          valueStyle: { color: status.color },
        },
      ]}
      controls={
        <button
          type="button"
          id="media-scan-button"
          className="media-scan-btn"
          disabled={busy}
          onClick={() => void onClick()}
        >
          <span className="scan-icon">&#128225;</span>
          <span className="scan-text">Scan Library</span>
        </button>
      }
      progress={
        <ToolProgress
          phase={phase}
          details={details}
          percent={percent}
          phaseId="media-scan-phase-label"
          barId="media-scan-progress-bar"
          detailsId="media-scan-progress-label"
        />
      }
    />
  );
}

// ── Metadata Updater (Plex + Jellyfin) ───────────────────────────────────────

const JELLYFIN_DESCRIPTION =
  'Download and upload high-quality artist images from Spotify to your Jellyfin server for artists without photos.';
const PLEX_DESCRIPTION =
  'Download and upload high-quality artist images from Spotify to your Plex server for artists without photos.';
const DEFAULT_DESCRIPTION = 'Updates artist photos, genres, and album art from Spotify.';

export function MetadataUpdaterCard() {
  const server = useActiveMediaServer();
  const [interval, setIntervalDays] = useState('30');
  const [stopping, setStopping] = useState(false);
  const announcedRef = useRef(false);
  // The vanilla's `stopping` branch touches ONLY the button and the phase line:
  // the progress label, the bar and the disabled select all keep whatever the
  // running branch last set. Holding the last running values reproduces that.
  const lastProgressRef = useRef({ details: '0 / 0 artists (0.0%)', percent: 0 });

  const poll = usePolledStatus<MetadataUpdateState>({
    fetcher: fetchMetadataUpdateStatus,
    isRunning: (state) => state.status === 'running',
    intervalMs: 1000,
    onState: (state) => {
      if (state.status === 'running') {
        announcedRef.current = false;
        setStopping(false);
        return;
      }
      if (state.status === 'stopping') return;
      setStopping(false);
      if (state.status === 'completed' && !announcedRef.current) {
        announcedRef.current = true;
        toast(
          `Metadata update completed: ${state.successful} artists updated, ${state.failed} failed`,
          'success',
        );
      }
    },
  });

  const state = poll.state;
  const running = state?.status === 'running';
  const isStopping = stopping || state?.status === 'stopping';

  const onClick = useCallback(async () => {
    if (!running && !isStopping) {
      try {
        await startMetadataUpdate(Number.parseInt(interval, 10));
        toast('Metadata update started!', 'success');
        announcedRef.current = false;
        await poll.refresh();
        poll.arm();
      } catch (error) {
        toast(`Error: ${error instanceof Error ? error.message : String(error)}`, 'error');
      }
      return;
    }
    setStopping(true);
    try {
      await stopMetadataUpdate();
    } catch {
      setStopping(false);
    }
  }, [interval, isStopping, poll, running]);

  let phase = 'Current Artist: Not running';
  let details = '0 / 0 artists (0.0%)';
  let percent = 0;

  if (running) {
    phase = `Current Artist: ${state?.current_artist || 'Processing...'}`;
    const pct = state?.percentage || 0;
    details = `${state?.processed || 0} / ${state?.total || 0} artists (${pct.toFixed(1)}%)`;
    percent = pct;
    lastProgressRef.current = { details, percent };
  } else if (state?.status === 'stopping') {
    phase = 'Current Artist: Stopping...';
    // Frozen at the last running values — the vanilla never rewrites them here.
    details = lastProgressRef.current.details;
    percent = lastProgressRef.current.percent;
  } else if (state?.status === 'completed') {
    phase = 'Current Artist: Completed';
    details = `Completed: ${state.processed || 0} processed, ${state.successful || 0} successful, ${state.failed || 0} failed`;
    percent = 100;
  } else if (state?.status === 'error') {
    phase = 'Current Artist: Error occurred';
    details = state.error || 'Unknown error';
  }

  const serverName = server ? server.charAt(0).toUpperCase() + server.slice(1) : null;
  const description = !server
    ? DEFAULT_DESCRIPTION
    : server === 'jellyfin'
      ? JELLYFIN_DESCRIPTION
      : PLEX_DESCRIPTION;

  return (
    <ToolCard
      id="metadata-updater-card"
      title={serverName ? `${serverName} Metadata Updater` : 'Metadata Updater'}
      helpTool="metadata-updater"
      hidden={server !== null && server !== 'plex' && server !== 'jellyfin'}
      info={description}
      infoClassName="metadata-updater-description tool-card-info"
      controls={
        <>
          <select
            id="metadata-refresh-interval"
            value={interval}
            disabled={running || isStopping}
            onChange={(event) => setIntervalDays(event.target.value)}
          >
            <option value="180">6 months</option>
            <option value="90">3 months</option>
            <option value="30">1 month</option>
            <option value="14">2 weeks</option>
            <option value="7">1 week</option>
            <option value="0">Full refresh</option>
          </select>
          <button
            type="button"
            id="metadata-update-button"
            disabled={isStopping}
            onClick={() => void onClick()}
          >
            {running ? 'Stop Update' : isStopping ? 'Stopping...' : 'Begin Update'}
          </button>
        </>
      }
      progress={
        <ToolProgress
          phase={phase}
          details={details}
          percent={percent}
          phaseId="metadata-phase-label"
          barId="metadata-progress-bar"
          detailsId="metadata-progress-label"
        />
      }
    />
  );
}

// ── Backup Manager ───────────────────────────────────────────────────────────

export function BackupManagerCard() {
  const [data, setData] = useState<BackupListResponse | null>(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    const next = await fetchBackups();
    if (next) setData(next);
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const onBackupNow = useCallback(async () => {
    setBusy(true);
    try {
      const result = await createBackup();
      if (result.success) {
        toast(`Database backed up (${result.size_mb} MB)`, 'success');
        await load();
      } else {
        toast(`Backup failed: ${result.error}`, 'error');
      }
    } catch {
      toast('Backup request failed', 'error');
    }
    setBusy(false);
  }, [load]);

  /** `restoreBackup` — recurses once with force after a version mismatch. */
  const onRestore = useCallback(
    async (filename: string, force = false) => {
      if (!force) {
        const ok = await window.showConfirmDialog?.({
          title: 'Restore Backup',
          message: `Restore database from "${filename}"?\n\nA safety backup of the current database will be created first.`,
          confirmText: 'Restore',
        });
        if (!ok) return;
      }
      try {
        const result = await restoreBackupRequest(filename, force);
        if (result.success) {
          let message = `Database restored from ${result.restored_from} (${result.artist_count} artists). Safety backup: ${result.safety_backup}`;
          if (result.version_warning) message += `\n⚠️ ${result.version_warning}`;
          toast(message, 'success');
          await load();
        } else if (result.version_mismatch) {
          const confirmed = await window.showConfirmDialog?.({
            title: 'Version Mismatch',
            message: `This backup was created on SoulSync v${result.backup_version}, but you're running v${result.current_version}.\n\nRestoring an older backup may cause issues if the database schema has changed. A safety backup will be created first.\n\nProceed anyway?`,
            confirmText: 'Restore Anyway',
            destructive: true,
          });
          if (confirmed) await onRestore(filename, true);
        } else {
          toast(`Restore failed: ${result.error}`, 'error');
        }
      } catch {
        toast('Restore request failed', 'error');
      }
    },
    [load],
  );

  const onDelete = useCallback(
    async (filename: string) => {
      const ok = await window.showConfirmDialog?.({
        title: 'Delete Backup',
        message: `Delete backup "${filename}"? This cannot be undone.`,
        confirmText: 'Delete',
        destructive: true,
      });
      if (!ok) return;
      try {
        const result = await deleteBackupRequest(filename);
        if (result.success) {
          toast(`Backup deleted: ${result.deleted}`, 'success');
          await load();
        } else {
          toast(`Delete failed: ${result.error}`, 'error');
        }
      } catch {
        toast('Delete request failed', 'error');
      }
    },
    [load],
  );

  const backups: BackupEntry[] = data?.backups || [];
  const summary = backupSummary(backups);

  return (
    <ToolCard
      id="backup-manager-card"
      title="Backup Manager"
      helpTool="backup-manager"
      info="Create, download, restore and manage database backups"
      stats={[
        { label: 'Last Backup:', valueId: 'backup-stat-last', value: summary.lastBackup },
        { label: 'Backups:', valueId: 'backup-stat-count', value: data?.count ?? 0 },
        { label: 'Latest Size:', valueId: 'backup-stat-latest-size', value: summary.latestSize },
        {
          label: 'DB Size:',
          valueId: 'backup-stat-db-size',
          value: data ? `${data.db_size_mb} MB` : '—',
        },
      ]}
      controls={
        <button
          type="button"
          id="backup-now-button"
          disabled={busy}
          onClick={() => void onBackupNow()}
        >
          {busy ? 'Backing up...' : 'Backup Now'}
        </button>
      }
    >
      <div id="backup-list-container" className="backup-list-container">
        {backups.map((backup) => {
          const date = backupTimestamp(backup.created);
          const dateStr = `${date.toLocaleDateString(undefined, {
            month: 'short',
            day: 'numeric',
            year: 'numeric',
          })} ${date.toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit' })}`;
          return (
            <div className="backup-list-item" key={backup.filename}>
              <div className="backup-list-info">
                <span className="backup-list-date">{dateStr}</span>
                <span className="backup-list-size">{backup.size_mb} MB</span>
                {backup.version ? (
                  <span className="backup-list-version">v{backup.version}</span>
                ) : null}
              </div>
              <div className="backup-list-actions">
                <a
                  className="backup-dl-btn"
                  href={backupDownloadUrl(backup.filename)}
                  download={backup.filename}
                  title="Download"
                >
                  DL
                </a>
                <button
                  type="button"
                  className="backup-restore-btn"
                  title="Restore"
                  onClick={() => void onRestore(backup.filename)}
                >
                  Restore
                </button>
                <button
                  type="button"
                  className="backup-delete-btn"
                  title="Delete"
                  onClick={() => void onDelete(backup.filename)}
                >
                  Del
                </button>
              </div>
            </div>
          );
        })}
      </div>
    </ToolCard>
  );
}
