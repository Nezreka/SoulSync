import { useEffect, useRef, useState } from 'react';

import type { WatchlistScanStatusResponse } from './-watchlist.types';

/** The window event core.js re-broadcasts `scan:watchlist` on. */
export const WATCHLIST_SCAN_EVENT = 'ss:watchlist-scan';

/** A live scan frame. Everything past `status` is only present while scanning. */
export interface WatchlistScanFrame extends WatchlistScanStatusResponse {
  current_artist_name?: string | null;
  current_artist_image_url?: string | null;
  current_album?: string | null;
  current_album_image_url?: string | null;
  current_track_name?: string | null;
  current_phase?: string | null;
  current_artist_index?: number | null;
  total_artists?: number | null;
  /** Source-matching phase: every artist is looked up on every other
   *  metadata provider before the artist loop starts. For a few hundred
   *  artists that is the longest part of a scan, and the artist counter has
   *  not begun, so these are the only numbers that move during it (#1240). */
  matching_source?: string | null;
  matching_source_number?: number | null;
  matching_source_total?: number | null;
  matching_artists_done?: number | null;
  matching_artists_total?: number | null;
  tracks_found_this_scan?: number | null;
  tracks_added_this_scan?: number | null;
  recent_wishlist_additions?: {
    track_name?: string | null;
    artist_name?: string | null;
    album_image_url?: string | null;
  }[];
  scan_track_events?: {
    track_name?: string | null;
    artist_name?: string | null;
    album_name?: string | null;
    album_image_url?: string | null;
    status?: string | null;
  }[];
}

/**
 * Human-readable scan phase.
 *
 * Ported verbatim from `_wlPrettyPhase`, including the numeric
 * `checking_album_N_of_M` form and the underscore-to-space fallback for phases
 * the map does not know.
 */
export function prettyScanPhase(phase: string | null | undefined): string {
  if (!phase) return 'Working…';

  const match = /^checking_album_(\d+)_of_(\d+)$/.exec(phase);
  if (match) return `Checking album ${match[1]} of ${match[2]}`;

  const map: Record<string, string> = {
    starting: 'Starting…',
    fetching_discography: 'Fetching releases…',
    scanning_labels: 'Scanning record labels…',
    populating_discovery_pool: 'Populating discovery…',
    updating_listenbrainz: 'Updating ListenBrainz…',
    matching_sources: 'Matching artists to sources…',
  };
  return map[phase] || phase.replace(/_/g, ' ');
}

/** Just the fields the progress helpers read, so callers (and tests) do not
 *  have to build a whole frame. */
export type ScanProgressFields = Pick<
  WatchlistScanFrame,
  | 'current_artist_index'
  | 'total_artists'
  | 'current_phase'
  | 'matching_source'
  | 'matching_artists_done'
  | 'matching_artists_total'
>;

/** True while the scan is matching artists to sources, before the artist loop. */
function isMatchingSources(frame: ScanProgressFields): boolean {
  return frame.current_phase === 'matching_sources';
}

/** The matching phase owns the first tenth of the bar; artists own the rest.
 *  Splitting it this way is what stops the bar jumping BACKWARDS when the
 *  artist loop finally starts at index 1. */
const MATCHING_BAND = 10;

/** "3 / 40 artists", or '' when the total is not known yet. */
export function scanProgressText(frame: ScanProgressFields): string {
  // During source matching the artist counter has not started. Reporting it
  // would read '0 / 379 artists' for the whole phase, which is exactly what
  // made a working scan look like a hung one.
  if (isMatchingSources(frame)) {
    const matched = frame.matching_artists_total || 0;
    if (matched) {
      const done = Math.min(frame.matching_artists_done || 0, matched);
      const source = frame.matching_source;
      return source
        ? `Matching ${done} / ${matched} artists to ${source}`
        : `Matching ${done} / ${matched} artists`;
    }
  }
  const total = frame.total_artists || 0;
  if (!total) return '';
  const index = Math.min((frame.current_artist_index || 0) + 1, total);
  return `${index} / ${total} artists`;
}

/** Progress bar width as a percentage. 0 when the total is unknown. */
export function scanProgressPercent(frame: ScanProgressFields): number {
  if (isMatchingSources(frame)) {
    const matched = frame.matching_artists_total || 0;
    if (!matched) return 0;
    const done = Math.min(frame.matching_artists_done || 0, matched);
    return Math.round((MATCHING_BAND * done) / matched);
  }
  const total = frame.total_artists || 0;
  if (!total) return 0;
  const index = Math.min((frame.current_artist_index || 0) + 1, total);
  return MATCHING_BAND + Math.round(((100 - MATCHING_BAND) * index) / total);
}

/**
 * The album line under the artist name.
 *
 * Falls back to a phase-dependent placeholder rather than going blank between
 * albums, which is what the vanilla deck did.
 */
export function scanAlbumLine(
  frame: Pick<WatchlistScanFrame, 'current_album' | 'current_phase'>,
): string {
  if (frame.current_album) return frame.current_album;
  return frame.current_phase === 'fetching_discography'
    ? 'Fetching releases…'
    : 'Looking for new releases…';
}

/** The completion sentence under a finished scan. */
export function scanCompletionMessage(summary: {
  total_artists?: number;
  successful_scans?: number;
  new_tracks_found?: number;
  tracks_added_to_wishlist?: number;
}): string {
  const total = summary.total_artists || 0;
  const successful = summary.successful_scans || 0;
  const newTracks = summary.new_tracks_found || 0;
  const added = summary.tracks_added_to_wishlist || 0;

  let message = `Scan completed: ${successful}/${total} artists scanned`;
  if (newTracks > 0) {
    message += `, found ${newTracks} new track${newTracks !== 1 ? 's' : ''}`;
    if (added > 0) message += `, added ${added} to wishlist`;
  } else {
    message += ', no new tracks found';
  }
  return message;
}

/** A socket frame seen this recently means the socket is alive. */
const SOCKET_FRESH_MS = 6000;

export interface LiveScan {
  frame: WatchlistScanFrame | null;
  /** True when no socket frame has arrived recently, so HTTP polling is needed. */
  needsPolling: boolean;
}

/**
 * Subscribe to live scan frames.
 *
 * The vanilla page treats the socket as primary and polls only when it is down
 * (`pollWatchlistScanStatus` returns immediately if `socketConnected`). That
 * flag is module-scoped in core.js and unreadable from here, so freshness is
 * inferred instead: a frame seen within SOCKET_FRESH_MS means the socket is
 * delivering and the caller should not also poll.
 */
export function useLiveWatchlistScan(): LiveScan {
  const [frame, setFrame] = useState<WatchlistScanFrame | null>(null);
  const [needsPolling, setNeedsPolling] = useState(true);
  const lastEventAt = useRef<number>(0);

  useEffect(() => {
    const onScan = (event: Event) => {
      const detail = (event as CustomEvent<WatchlistScanFrame>).detail;
      if (!detail) return;
      lastEventAt.current = Date.now();
      setFrame(detail);
      setNeedsPolling(false);
    };

    window.addEventListener(WATCHLIST_SCAN_EVENT, onScan);
    return () => window.removeEventListener(WATCHLIST_SCAN_EVENT, onScan);
  }, []);

  // Re-arm polling if the socket goes quiet mid-scan, so a dropped connection
  // does not freeze the deck on its last frame.
  useEffect(() => {
    const timer = setInterval(() => {
      if (Date.now() - lastEventAt.current > SOCKET_FRESH_MS) setNeedsPolling(true);
    }, SOCKET_FRESH_MS);
    return () => clearInterval(timer);
  }, []);

  return { frame, needsPolling };
}

/**
 * Seconds remaining until the next automatic scan, ticking down locally.
 *
 * The server sends the figure once per page load; the vanilla page ran a 1s
 * interval over it. The interval is cleared on unmount — leaking it was a real
 * bug class in the vanilla page, which cleared the timer from loadPageData.
 */
export function useCountdown(initialSeconds: number): number {
  const [seconds, setSeconds] = useState(initialSeconds);

  useEffect(() => {
    setSeconds(initialSeconds);
    if (initialSeconds <= 0) return;

    const timer = setInterval(() => {
      setSeconds((previous) => (previous > 0 ? previous - 1 : 0));
    }, 1000);
    return () => clearInterval(timer);
  }, [initialSeconds]);

  return seconds;
}
