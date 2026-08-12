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

function SettingsLink() {
  return (
    <span
      className="link"
      onClick={() => void window.navigateToPage?.('settings')}
    >
      Settings
    </span>
  );
}

export function LibraryCard() {
  const { dbStats, dbStatsSeen, status, scanning, progress, scan, deepScan } = useLibraryCard();
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
