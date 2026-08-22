/**
 * The Library card (dash-card data-card="library") — the smart status card
 * with its five states and the two scan flows. Markup 1:1 from index.html
 * (artefact differential pins it, SVGs included).
 *
 * State machine: -dash.library.ts libraryCardView, fed by
 * - dbStats: fetchDatabaseStats on mount + ss:dashboard-db-stats pushes. The
 *   vanilla dashboard had NO db-stats interval (only the initial load and the
 *   socket) — none here either.
 * - the /status payload: ss:service-status + one mount fetch.
 * Until the first dbStats answer the card shows the markup's "Checking
 * status..." shell — the machine first runs when db stats arrive, exactly
 * when the vanilla first calls updateLibraryStatusCard.
 *
 * The scan flows are dashboardLibraryScan / dashboardLibraryDeepScan
 * transcribed: toggle-to-stop, start + toasts, the 2s progress poll writing
 * phase/bar/detail, the terminal statuses, the stats refetch. Scan start
 * CLEARS dbStats (the vanilla renders with null until the next payload).
 * The progress poll raw-fetches /api/database/update/status on purpose: the
 * vanilla keeps polling on a non-ok response but STOPS (and unsticks the
 * card) on a thrown fetch — the api layer's null-on-everything fetcher
 * cannot tell those apart.
 */

import { useCallback, useEffect, useRef, useState } from 'react';

import { fetchReviewQueueSummary } from '@/routes/active-downloads/-adl.api';

import type { ServiceStatusPayload } from '../-dash.api';
import type { DbStats, LibraryCardView } from '../-dash.library';

import {
  fetchDatabaseStats,
  fetchServiceStatus,
  startLibraryDeepScan,
  startLibraryScan,
  stopLibraryScan,
} from '../-dash.api';
import { useDashboardDbStatsEvent, useServiceStatusEvent } from '../-dash.events';
import { libraryCardView, publishDbStats } from '../-dash.library';

interface ScanProgress {
  phase: string;
  width: number;
  detail: string;
}

const IDLE_PROGRESS: ScanProgress = { phase: 'Scanning...', width: 0, detail: '0 / 0' };

export function useLibraryCard() {
  const [dbStats, setDbStats] = useState<DbStats | null>(null);
  const [dbStatsSeen, setDbStatsSeen] = useState(false);
  const [status, setStatus] = useState<ServiceStatusPayload | null>(null);
  const [scanning, setScanning] = useState(false);
  const [progress, setProgress] = useState<ScanProgress>(IDLE_PROGRESS);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const scanningRef = useRef(false);
  const mountedRef = useRef(true);

  const setScanningBoth = useCallback((value: boolean) => {
    scanningRef.current = value;
    if (mountedRef.current) setScanning(value);
  }, []);

  const applyDbStats = useCallback((stats: DbStats | null) => {
    if (!mountedRef.current) return;
    setDbStats(stats);
    setDbStatsSeen(true);
    // The header's status strip shows these same numbers; publishing here
    // covers every arrival path (initial fetch, socket push, post-scan
    // refresh) without a second /api/database/stats call.
    publishDbStats(stats);
  }, []);

  useDashboardDbStatsEvent(useCallback((frame) => applyDbStats(frame as DbStats), [applyDbStats]));
  useServiceStatusEvent(
    useCallback((payload) => {
      if (mountedRef.current) setStatus(payload);
    }, []),
  );

  useEffect(() => {
    mountedRef.current = true;
    void fetchDatabaseStats().then((stats) => {
      if (mountedRef.current && stats) applyDbStats(stats as DbStats);
    });
    void fetchServiceStatus().then((payload) => {
      if (mountedRef.current && payload) setStatus(payload);
    });
    return () => {
      mountedRef.current = false;
      if (pollRef.current) clearInterval(pollRef.current);
    };
  }, [applyDbStats]);

  const refreshStats = useCallback(async () => {
    // The vanilla's post-scan refresh: GET /api/database/stats, apply if ok.
    try {
      const stats = await fetchDatabaseStats();
      if (stats) applyDbStats(stats as DbStats);
    } catch {
      // swallowed, like the vanilla's empty catch
    }
  }, [applyDbStats]);

  /** The shared 2s progress poll; the phase default and terminal toasts are
   *  the two flows' only differences, passed as literals. */
  const startPoll = useCallback(
    (phaseDefault: string, completeToast: string, errorPrefix: string) => {
      if (pollRef.current) clearInterval(pollRef.current);
      pollRef.current = setInterval(async () => {
        try {
          const statusResp = await fetch('/api/database/update/status');
          if (!statusResp.ok) return; // keep polling, like the vanilla
          const scanStatus = (await statusResp.json()) as {
            status?: string;
            phase?: string;
            progress?: number;
            processed?: number;
            total?: number;
            error_message?: string;
          };

          if (mountedRef.current) {
            setProgress((prev) => ({
              phase: scanStatus.phase || phaseDefault,
              width: scanStatus.progress || 0,
              detail:
                scanStatus.processed !== undefined
                  ? `${scanStatus.processed} / ${scanStatus.total || '?'}`
                  : prev.detail,
            }));
          }

          if (
            scanStatus.status === 'completed' ||
            scanStatus.status === 'finished' ||
            scanStatus.status === 'error' ||
            scanStatus.status === 'idle'
          ) {
            if (pollRef.current) clearInterval(pollRef.current);
            pollRef.current = null;
            setScanningBoth(false);

            if (scanStatus.status === 'completed' || scanStatus.status === 'finished') {
              window.showToast?.(completeToast, 'success');
            } else if (scanStatus.status === 'error') {
              window.showToast?.(
                `${errorPrefix}: ${scanStatus.error_message || 'Unknown'}`,
                'error',
              );
            }
            await refreshStats();
          }
        } catch {
          // A THROWN fetch stops the poll and unsticks the card (vanilla).
          if (pollRef.current) clearInterval(pollRef.current);
          pollRef.current = null;
          setScanningBoth(false);
        }
      }, 2000);
    },
    [refreshStats, setScanningBoth],
  );

  /** dashboardLibraryScan (wishlist-tools.js:7312), verbatim flow. */
  const scan = useCallback(
    async (fullRefresh: boolean) => {
      // If already scanning, stop it
      if (scanningRef.current) {
        try {
          await stopLibraryScan();
          if (pollRef.current) {
            clearInterval(pollRef.current);
            pollRef.current = null;
          }
          setScanningBoth(false);
          window.showToast?.('Library scan stopped', 'info');
          await refreshStats();
        } catch {
          window.showToast?.('Failed to stop scan', 'error');
        }
        return;
      }

      try {
        setScanningBoth(true);
        applyDbStats(null); // the vanilla renders the scanning state with null stats
        setProgress(IDLE_PROGRESS);

        const response = await startLibraryScan(fullRefresh);
        const data = (await response.json()) as { success?: boolean; error?: string };
        if (!data.success) {
          setScanningBoth(false);
          window.showToast?.(data.error || 'Failed to start scan', 'error');
          return;
        }

        window.showToast?.('Library scan started', 'success');
        startPoll('Scanning...', 'Library scan complete', 'Scan error');
      } catch (error) {
        setScanningBoth(false);
        window.showToast?.(`Scan failed: ${(error as Error).message}`, 'error');
      }
    },
    [applyDbStats, refreshStats, setScanningBoth, startPoll],
  );

  /** dashboardLibraryDeepScan (wishlist-tools.js:7400), verbatim flow —
   *  incl. the confirm dialog and the failed-start stats refetch the
   *  incremental flow does NOT do. */
  const deepScan = useCallback(async () => {
    if (scanningRef.current) {
      window.showToast?.('A scan is already running', 'warning');
      return;
    }

    const confirmed = await window.showConfirmDialog?.({
      title: 'Deep Scan Library',
      message:
        'A deep scan re-checks every track in your media server library.\n\n' +
        '• Adds any new tracks that were missed\n' +
        '• Removes tracks no longer on your server\n' +
        '• Preserves all existing metadata and enrichment data\n\n' +
        'This may take a while for large libraries. Continue?',
    });
    if (!confirmed) return;

    try {
      setScanningBoth(true);
      applyDbStats(null);
      setProgress({ ...IDLE_PROGRESS, phase: 'Deep scanning...' });

      const response = await startLibraryDeepScan();
      const data = (await response.json()) as { success?: boolean; error?: string };
      if (!data.success) {
        setScanningBoth(false);
        window.showToast?.(data.error || 'Failed to start deep scan', 'error');
        await refreshStats();
        return;
      }

      window.showToast?.('Deep scan started — this may take a while', 'success');
      startPoll('Deep scanning...', 'Deep scan complete', 'Deep scan error');
    } catch (error) {
      setScanningBoth(false);
      window.showToast?.(`Deep scan failed: ${(error as Error).message}`, 'error');
    }
  }, [applyDbStats, refreshStats, setScanningBoth, startPoll]);

  return { dbStats, dbStatsSeen, status, scanning, progress, scan, deepScan };
}

/** The markup's pre-data shell — shown until the first dbStats payload. */
const CHECKING: LibraryCardView = {
  cardClass: 'library-status-card',
  title: 'Library',
  subtitle: 'Checking status...',
  scanVisible: false,
  scanScanning: false,
  scanLabel: 'Quick Scan',
  deepVisible: false,
  statsVisible: false,
  stats: null,
  progressVisible: false,
  message: null,
};

/** How often the dashboard re-checks the review queue. */
const REVIEW_POLL_MS = 30000;

/**
 * How many downloads are sitting waiting on a human.
 *
 * There was no way to know without opening the downloads page and clicking
 * into the tab, so people had files waiting for days. TheHomeGuy asked for
 * exactly this. Slower poll than the downloads page uses, nothing here moves
 * fast and the dashboard is already busy.
 */
function useReviewCount(): number | null {
  const [count, setCount] = useState<number | null>(null);

  useEffect(() => {
    let live = true;
    const pull = async () => {
      const summary = await fetchReviewQueueSummary();
      // null means the fetch failed. leave the last number up rather than
      // claiming there is nothing to review.
      if (live && summary) setCount(summary.total);
    };
    void pull();
    const timer = setInterval(() => void pull(), REVIEW_POLL_MS);
    return () => {
      live = false;
      clearInterval(timer);
    };
  }, []);

  return count;
}

function SettingsLink() {
  return (
    <span className="link" onClick={() => void window.navigateToPage?.('settings')}>
      Settings
    </span>
  );
}

/** One-click db backup — the same POST the Tools backup manager makes, with
 *  the strip's own confirm + toasts. Never window.confirm (house rule). */
async function backupNow(): Promise<void> {
  const confirmed = await window.showConfirmDialog?.({
    title: 'Back Up Database',
    message:
      'Creates a snapshot of the SoulSync database (library, wishlist, history, enrichment).\n\n' +
      'Backups are managed on the Tools page. Continue?',
  });
  if (!confirmed) return;
  window.showToast?.('Backup started...', 'info');
  try {
    const response = await fetch('/api/database/backup', { method: 'POST' });
    const data = (await response.json()) as { success?: boolean; error?: string };
    if (data.success) window.showToast?.('Database backup created', 'success');
    else window.showToast?.(data.error || 'Backup failed', 'error');
  } catch (error) {
    window.showToast?.(`Backup failed: ${(error as Error).message}`, 'error');
  }
}

export function LibraryCard() {
  const { dbStats, dbStatsSeen, status, scanning, progress, scan, deepScan } = useLibraryCard();
  const reviewCount = useReviewCount();
  const view = dbStatsSeen ? libraryCardView(dbStats, status, scanning, new Date()) : CHECKING;

  return (
    // A full-width STRIP in the stats band's language, not a tall card: four
    // numbers and two buttons were rattling around a card whose height the
    // Services card set. The outer head went with the box — the inner
    // library-status-card already carries its own title/subtitle/actions.
    <article className="dash-card dash-card--strip" data-card="library">
      <div className="dash-card__body">
        <div className={view.cardClass} id="library-status-card">
          <div className="library-status-glow"></div>
          <div className="library-status-header">
            <div className="library-status-icon" id="library-status-icon">
              <svg
                width="24"
                height="24"
                viewBox="0 0 24 24"
                fill="none"
                stroke="currentColor"
                strokeWidth="1.8"
                strokeLinecap="round"
                strokeLinejoin="round"
              >
                <path d="M4 19.5A2.5 2.5 0 0 1 6.5 17H20" />
                <path d="M6.5 2H20v20H6.5A2.5 2.5 0 0 1 4 19.5v-15A2.5 2.5 0 0 1 6.5 2z" />
                <line x1="9" y1="7" x2="16" y2="7" />
                <line x1="9" y1="11" x2="14" y2="11" />
              </svg>
            </div>
            <div className="library-status-info">
              <h4 className="library-status-title" id="library-status-title">
                {view.title}
              </h4>
              <p className="library-status-subtitle" id="library-status-subtitle">
                {view.subtitle}
              </p>
            </div>
            <div className="library-status-actions" id="library-status-actions">
              <button
                className={view.scanScanning ? 'library-status-btn scanning' : 'library-status-btn'}
                id="library-status-scan-btn"
                style={view.scanVisible ? undefined : { display: 'none' }}
                onClick={() => void scan(false)}
              >
                <svg
                  width="14"
                  height="14"
                  viewBox="0 0 24 24"
                  fill="none"
                  stroke="currentColor"
                  strokeWidth="2.5"
                >
                  <polyline points="23 4 23 10 17 10" />
                  <path d="M20.49 15a9 9 0 1 1-2.12-9.36L23 10" />
                </svg>
                <span id="library-status-scan-label">{view.scanLabel}</span>
              </button>
              <button
                className="library-status-btn library-status-btn-secondary"
                id="library-status-deep-btn"
                style={view.deepVisible ? undefined : { display: 'none' }}
                onClick={() => void deepScan()}
              >
                <svg
                  width="13"
                  height="13"
                  viewBox="0 0 24 24"
                  fill="none"
                  stroke="currentColor"
                  strokeWidth="2"
                >
                  <circle cx="11" cy="11" r="8" />
                  <line x1="21" y1="21" x2="16.65" y2="16.65" />
                  <line x1="11" y1="8" x2="11" y2="14" />
                  <line x1="8" y1="11" x2="14" y2="11" />
                </svg>
                Deep Scan
              </button>
              {/* The strip went purely operational — these are the rest of the
                  library's verbs (Boulder picked all four): go there, check
                  the matches, repair it, back it up. */}
              <button
                className="library-status-btn library-status-btn-secondary"
                id="library-status-browse-btn"
                onClick={() => void window.navigateToPage?.('library')}
              >
                <svg
                  width="13"
                  height="13"
                  viewBox="0 0 24 24"
                  fill="none"
                  stroke="currentColor"
                  strokeWidth="2"
                >
                  <path d="M3 6h18M3 12h18M3 18h18" />
                </svg>
                Browse
              </button>
              <button
                className="library-status-btn library-status-btn-secondary"
                id="library-status-verify-btn"
                title="Open the enrichment manager's Verify Matches repair flow"
                onClick={() => window.openEnrichmentManager?.()}
              >
                <svg
                  width="13"
                  height="13"
                  viewBox="0 0 24 24"
                  fill="none"
                  stroke="currentColor"
                  strokeWidth="2"
                >
                  <path d="M22 11.08V12a10 10 0 1 1-5.93-9.14" />
                  <polyline points="22 4 12 14.01 9 11.01" />
                </svg>
                Verify Matches
              </button>
              <button
                className="library-status-btn library-status-btn-secondary"
                id="library-status-repair-btn"
                title="Open the Tools maintenance center"
                onClick={() => void window.navigateToPage?.('tools')}
              >
                <svg
                  width="13"
                  height="13"
                  viewBox="0 0 24 24"
                  fill="none"
                  stroke="currentColor"
                  strokeWidth="2"
                >
                  <path d="M14.7 6.3a1 1 0 0 0 0 1.4l1.6 1.6a1 1 0 0 0 1.4 0l3.77-3.77a6 6 0 0 1-7.94 7.94l-6.91 6.91a2.12 2.12 0 0 1-3-3l6.91-6.91a6 6 0 0 1 7.94-7.94l-3.76 3.76z" />
                </svg>
                Repair
              </button>
              <button
                className="library-status-btn library-status-btn-secondary"
                id="library-status-backup-btn"
                title="Back up the SoulSync database now"
                onClick={() => void backupNow()}
              >
                <svg
                  width="13"
                  height="13"
                  viewBox="0 0 24 24"
                  fill="none"
                  stroke="currentColor"
                  strokeWidth="2"
                >
                  <ellipse cx="12" cy="5" rx="9" ry="3" />
                  <path d="M21 12c0 1.66-4 3-9 3s-9-1.34-9-3" />
                  <path d="M3 5v14c0 1.66 4 3 9 3s9-1.34 9-3V5" />
                </svg>
                Backup
              </button>
              {/* everything above acts ON the library. everything below just
                  takes you somewhere. the divider is so ten buttons don't read
                  as one wall. */}
              <span className="library-status-divider" aria-hidden="true" />
              <button
                className={
                  reviewCount
                    ? 'library-status-btn library-status-btn-secondary library-status-btn-attention'
                    : 'library-status-btn library-status-btn-secondary'
                }
                id="library-status-review-btn"
                title={
                  reviewCount
                    ? `${reviewCount} downloaded file${reviewCount === 1 ? '' : 's'} waiting on you to approve or delete`
                    : 'Downloads that failed verification, or imported without a hard match'
                }
                onClick={() => void window.navigateToPage?.('active-downloads')}
              >
                <svg
                  width="13"
                  height="13"
                  viewBox="0 0 24 24"
                  fill="none"
                  stroke="currentColor"
                  strokeWidth="2"
                >
                  <path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z" />
                </svg>
                Review
                {reviewCount ? (
                  <span className="library-status-btn-badge">{reviewCount}</span>
                ) : null}
              </button>
              <button
                className="library-status-btn library-status-btn-secondary"
                id="library-status-wishlist-btn"
                title="Tracks SoulSync is still trying to find"
                onClick={() => void window.navigateToPage?.('wishlist')}
              >
                <svg
                  width="13"
                  height="13"
                  viewBox="0 0 24 24"
                  fill="none"
                  stroke="currentColor"
                  strokeWidth="2"
                >
                  <path d="M20.8 4.6a5.5 5.5 0 0 0-7.8 0L12 5.7l-1-1.1a5.5 5.5 0 0 0-7.8 7.8l1 1.1L12 21.2l7.8-7.7 1-1.1a5.5 5.5 0 0 0 0-7.8z" />
                </svg>
                Wishlist
              </button>
              <button
                className="library-status-btn library-status-btn-secondary"
                id="library-status-downloads-btn"
                title="Active and queued downloads"
                onClick={() => void window.navigateToPage?.('active-downloads')}
              >
                <svg
                  width="13"
                  height="13"
                  viewBox="0 0 24 24"
                  fill="none"
                  stroke="currentColor"
                  strokeWidth="2"
                >
                  <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4" />
                  <polyline points="7 10 12 15 17 10" />
                  <line x1="12" y1="15" x2="12" y2="3" />
                </svg>
                Downloads
              </button>
              <button
                className="library-status-btn library-status-btn-secondary"
                id="library-status-discover-btn"
                title="Find music you don't have yet"
                onClick={() => void window.navigateToPage?.('discover')}
              >
                <svg
                  width="13"
                  height="13"
                  viewBox="0 0 24 24"
                  fill="none"
                  stroke="currentColor"
                  strokeWidth="2"
                >
                  <circle cx="12" cy="12" r="10" />
                  <polygon points="16.24 7.76 14.12 14.12 7.76 16.24 9.88 9.88 16.24 7.76" />
                </svg>
                Discover
              </button>
              <button
                className="library-status-btn library-status-btn-secondary"
                id="library-status-sync-btn"
                title="Playlists and their sync schedules"
                onClick={() => void window.navigateToPage?.('sync')}
              >
                <svg
                  width="13"
                  height="13"
                  viewBox="0 0 24 24"
                  fill="none"
                  stroke="currentColor"
                  strokeWidth="2"
                >
                  <polyline points="23 4 23 10 17 10" />
                  <polyline points="1 20 1 14 7 14" />
                  <path d="M3.51 9a9 9 0 0 1 14.85-3.36L23 10M1 14l4.64 4.36A9 9 0 0 0 20.49 15" />
                </svg>
                Sync
              </button>
            </div>
          </div>
          {/* The four-stat row lived here until the header's hello strip
              took tracks/artists; albums + db size moved into the subtitle.
              The strip is now purely operational: status, scan buttons,
              progress. */}
          <div
            className="library-status-progress"
            id="library-status-progress"
            style={view.progressVisible ? undefined : { display: 'none' }}
          >
            <div className="library-status-phase" id="library-status-phase">
              {progress.phase}
            </div>
            <div className="library-status-bar">
              <div
                className="library-status-bar-fill"
                id="library-status-bar-fill"
                style={{ width: `${progress.width}%` }}
              ></div>
            </div>
            <div className="library-status-progress-detail" id="library-status-progress-detail">
              {progress.detail}
            </div>
          </div>
          <div
            className="library-status-message"
            id="library-status-message"
            style={view.message ? undefined : { display: 'none' }}
          >
            {view.message?.kind === 'no-server' ? (
              <>
                SoulSync needs a media server to manage your library. Go to <SettingsLink /> to
                connect Plex, Jellyfin, or Navidrome.
              </>
            ) : view.message?.kind === 'disconnected' ? (
              <>
                Your {view.message.serverName} server is configured but not responding. Check that
                it&apos;s running and the connection details are correct in <SettingsLink />.
              </>
            ) : view.message?.kind === 'empty' ? (
              <>
                Your server is connected but SoulSync hasn&apos;t imported your library yet. Click{' '}
                <strong>Scan Now</strong> to pull your artists, albums, and tracks into SoulSync.
              </>
            ) : null}
          </div>
        </div>
      </div>
    </article>
  );
}
