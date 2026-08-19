import { useEffect, useRef } from 'react';

/**
 * Keep a dashboard band current without making a hidden tab do work.
 *
 * The bands had two different problems and this fixes both:
 *
 * - Recently Added loaded ONCE on mount and never again, so it was stale from
 *   the moment you finished a download until you reloaded the page.
 * - Recently Played polled every 60s but skipped while the tab was hidden,
 *   with no catch-up on return. Listening in another tab and coming back
 *   meant waiting up to a full interval, which reads as "it only updates if I
 *   refresh" — which is exactly what it was reported as.
 *
 * The catch-up is the important half. A poller that pauses while hidden is
 * correct (a background tab should not hammer the server), but pausing
 * without refetching on the way back just moves the staleness to the moment
 * the user is actually looking.
 *
 * WHAT THIS CANNOT FIX: a play only reaches listening_history when the web
 * player records it (immediate) or the listening-stats worker polls the media
 * server (every 30 minutes by default, `listening_stats.poll_interval`).
 * Plays made in Plex/Jellyfin are bounded by that worker, not by this hook —
 * no amount of front-end polling surfaces a row that is not in the table yet.
 */

export interface LiveRefreshOptions {
  /** Steady-state poll while the tab is visible. */
  intervalMs: number;
  /** Refetch the moment the tab becomes visible again. Default true. */
  refreshOnVisible?: boolean;
  /** Skip everything (e.g. a band that has nothing to show). */
  enabled?: boolean;
}

export function useLiveRefresh(
  load: () => void | Promise<void>,
  { intervalMs, refreshOnVisible = true, enabled = true }: LiveRefreshOptions,
): void {
  // The callback is almost always redefined every render. Holding it in a ref
  // keeps the timer from being torn down and rebuilt on each one, which would
  // reset the interval forever and, at a short interval, never fire.
  const loadRef = useRef(load);
  loadRef.current = load;

  useEffect(() => {
    if (!enabled) return;

    let cancelled = false;
    const run = () => {
      if (cancelled) return;
      void loadRef.current();
    };

    run();

    const tick = () => {
      // A hidden tab does no work — the steady-state poller rule.
      if (typeof document !== 'undefined' && document.hidden) return;
      run();
    };
    const timer = setInterval(tick, intervalMs);

    const onVisible = () => {
      if (typeof document !== 'undefined' && document.hidden) return;
      run();
    };

    if (refreshOnVisible && typeof document !== 'undefined') {
      document.addEventListener('visibilitychange', onVisible);
      window.addEventListener('focus', onVisible);
    }

    return () => {
      cancelled = true;
      clearInterval(timer);
      if (refreshOnVisible && typeof document !== 'undefined') {
        document.removeEventListener('visibilitychange', onVisible);
        window.removeEventListener('focus', onVisible);
      }
    };
  }, [intervalMs, refreshOnVisible, enabled]);
}
