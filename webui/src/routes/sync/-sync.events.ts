/**
 * The socket → React seam for the sync page.
 *
 * Same rule as the dashboard and tools arcs: the re-broadcast is dispatched
 * INSIDE the handler function, never at the socket binding, so every transport
 * that reaches the handler reaches React too. For logs that handler is
 * `updateLogsFromData` (api-monitor.js), which both the `tool:logs` socket
 * push (core.js 885) and the 3s `/api/logs` poll call — one seam covers both.
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
