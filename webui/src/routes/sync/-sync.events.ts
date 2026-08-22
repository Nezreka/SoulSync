/**
 * The socket → React seam for the sync page.
 *
 * Same rule as the dashboard and tools arcs: the re-broadcast is dispatched
 * INSIDE the handler function, never at the socket binding, so every transport
 * that reaches the handler reaches React too. For logs that handler is
 * `updateLogsFromData` (api-monitor.js), which both the `tool:logs` socket
 * push (core.js 924) and the 3s `/api/logs` poll call — one seam covers both.
 */

import { useEffect } from 'react';

export const SYNC_LOGS_EVENT = 'ss:sync-logs';

export interface SyncLogsFrame {
  logs?: unknown;
}

export function useSyncLogsEvent(onFrame: (frame: SyncLogsFrame) => void): void {
  useEffect(() => {
    const handler = (event: Event) => {
      const detail = (event as CustomEvent<SyncLogsFrame>).detail;
      if (detail) onFrame(detail);
    };
    window.addEventListener(SYNC_LOGS_EVENT, handler);
    return () => window.removeEventListener(SYNC_LOGS_EVENT, handler);
  }, [onFrame]);
}

/**
 * Deezer playlist load progress.
 *
 * Resolving a playlist means one rate-limited request per unique album for
 * real track numbers — ~1,000 of them on a 1500-track playlist, so minutes
 * during which the only feedback was a button reading "Loading...". The
 * server emits `deezer:playlist_progress` and core.js re-broadcasts it here.
 *
 * Declared in this module rather than in whichever component happened to
 * consume it first: both the account tab and the paste-a-link tab listen for
 * the same frames, and a shared event that lives inside one component's file
 * is how the two quietly drift apart.
 */
export const DEEZER_PLAYLIST_PROGRESS_EVENT = 'ss:deezer-playlist-progress';

export interface DeezerPlaylistProgressFrame {
  playlist_id: string;
  done: number;
  total: number;
  /** 'release dates' or 'track numbers' — the passes over the albums. */
  phase: string;
}

/**
 * A human label for one frame, or null when it says nothing useful yet.
 * Pure so the wording is testable without rendering anything.
 */
export function deezerProgressLabel(
  frame: DeezerPlaylistProgressFrame | null | undefined,
  playlistId: string,
): string | null {
  if (!frame || String(frame.playlist_id) !== String(playlistId)) return null;
  if (!frame.total) return null;
  const pct = Math.min(100, Math.round((frame.done / frame.total) * 100));
  return `${frame.phase} ${frame.done}/${frame.total} (${pct}%)`;
}
