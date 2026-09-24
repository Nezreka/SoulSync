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

import type { RefObject } from 'react';

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

const MORE_ICON = (
  <svg width="16" height="16" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true">
    <circle cx="5" cy="12" r="1.8" />
    <circle cx="12" cy="12" r="1.8" />
    <circle cx="19" cy="12" r="1.8" />
  </svg>
);

/** Closes an open menu on an outside click or Escape. */
function useDismiss(open: boolean, close: () => void, ref: RefObject<HTMLElement | null>) {
  useEffect(() => {
    if (!open) return undefined;
    const onDown = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) close();
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') close();
    };
    document.addEventListener('mousedown', onDown);
    document.addEventListener('keydown', onKey);
    return () => {
      document.removeEventListener('mousedown', onDown);
      document.removeEventListener('keydown', onKey);
    };
  }, [open, close, ref]);
}

/**
 * The library, as the hero's status line: whose library, how fresh, one
 * primary action (Scan) and everything else behind "⋯". The page links that
 * only repeated the sidebar (wishlist, downloads, discover, sync) are gone.
 * Every id the state machine and the scan flows write into is kept; the menu
 * stays mounted (just hidden) so those ids always resolve.
 */
export function LibraryCard() {
  const { dbStats, dbStatsSeen, status, scanning, progress, scan, deepScan } = useLibraryCard();
  const reviewCount = useReviewCount();
  const view = dbStatsSeen ? libraryCardView(dbStats, status, scanning, new Date()) : CHECKING;
  const [menuOpen, setMenuOpen] = useState(false);
  const menuRef = useRef<HTMLDivElement | null>(null);
  const closeMenu = useCallback(() => setMenuOpen(false), []);
  useDismiss(menuOpen, closeMenu, menuRef);
  const reviewWaiting = reviewCount !== null && reviewCount > 0;

  const pick = (action: () => void) => () => {
    setMenuOpen(false);
    action();
  };

  return (
    <div className="dash-hero-library" data-card="library">
      <div className={view.cardClass} id="library-status-card">
        <div className="library-status-header">
          <div className="library-status-info">
            <span className="library-status-dot" aria-hidden="true"></span>
            <h4 className="library-status-title" id="library-status-title">
              {view.title}
            </h4>
            <p className="library-status-subtitle" id="library-status-subtitle">
              {view.subtitle}
            </p>
          </div>
          <div className="library-status-actions" id="library-status-actions" ref={menuRef}>
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
              type="button"
              className={
                reviewWaiting
                  ? 'library-status-more library-status-more--attention'
                  : 'library-status-more'
              }
              aria-label="More library actions"
              aria-haspopup="menu"
              aria-expanded={menuOpen}
              onClick={() => setMenuOpen((open) => !open)}
            >
              {MORE_ICON}
            </button>
            <div className="library-status-menu" role="menu" hidden={!menuOpen}>
              <button
                role="menuitem"
                className="library-status-menu-item"
                id="library-status-deep-btn"
                style={view.deepVisible ? undefined : { display: 'none' }}
                onClick={pick(() => void deepScan())}
              >
                Deep scan
                <span className="library-status-menu-hint">re-read every file</span>
              </button>
              <button
                role="menuitem"
                className="library-status-menu-item"
                id="library-status-browse-btn"
                onClick={pick(() => void window.navigateToPage?.('library'))}
              >
                Browse library
              </button>
              <button
                role="menuitem"
                className="library-status-menu-item"
                id="library-status-verify-btn"
                title="Open the enrichment manager's Verify Matches repair flow"
                onClick={pick(() => window.openEnrichmentManager?.())}
              >
                Verify matches
              </button>
              <button
                role="menuitem"
                className="library-status-menu-item"
                id="library-status-repair-btn"
                title="Open the Tools maintenance center"
                onClick={pick(() => void window.navigateToPage?.('tools'))}
              >
                Repair
              </button>
              <button
                role="menuitem"
                className="library-status-menu-item"
                id="library-status-backup-btn"
                title="Back up the SoulSync database now"
                onClick={pick(() => void backupNow())}
              >
                Back up now
              </button>
              <button
                role="menuitem"
                className={
                  reviewWaiting
                    ? 'library-status-menu-item library-status-btn-attention'
                    : 'library-status-menu-item'
                }
                id="library-status-review-btn"
                title={
                  reviewWaiting
                    ? `${reviewCount} downloads waiting for you to look at them`
                    : 'Downloads waiting for review'
                }
                onClick={pick(() => void window.navigateToPage?.('active-downloads'))}
              >
                Review downloads
                {reviewWaiting ? (
                  <span className="library-status-btn-badge">{reviewCount}</span>
                ) : null}
              </button>
            </div>
          </div>
        </div>
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
  );
}
