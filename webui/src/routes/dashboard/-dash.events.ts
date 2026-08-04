/**
 * The socket → React seam for the dashboard.
 *
 * core.js/enrichment.js/api-monitor.js re-broadcast their frames as window
 * CustomEvents, dispatched INSIDE the handler functions (never the socket
 * bindings) so every transport that calls a handler — the socket, the 10s HTTP
 * fallback pollers, a modal replay — reaches React too. Same seam and same
 * rule as the tools arc; pinned by tests/test_dashboard_seam.py.
 *
 * Provider statuses arrive on ONE canonical channel, `ss:enrich-status`, as
 * `{ id, data }`. The vanilla's per-provider socket names are a fifth provider
 * registry whose spelling disagrees with the other four (hyphenated for eight
 * providers, bare for the rest) — React deliberately does not inherit it.
 */

import { useEffect } from 'react';

import type { ActivityItem, ServiceStatusPayload, SystemStats } from './-dash.api';
import type { ProviderStatusPayload } from './-dash.types';

// The repair pill rides the seam the tools arc already built.
export { useRepairStatusEvent } from '../tools/-tools.events';

export const ENRICH_STATUS_EVENT = 'ss:enrich-status';
export const DASHBOARD_STATS_EVENT = 'ss:dashboard-stats';
export const DASHBOARD_ACTIVITY_EVENT = 'ss:dashboard-activity';
export const DASHBOARD_TOAST_EVENT = 'ss:dashboard-toast';
export const DASHBOARD_DB_STATS_EVENT = 'ss:dashboard-db-stats';
export const DASHBOARD_WISHLIST_COUNT_EVENT = 'ss:dashboard-wishlist-count';
export const WATCHLIST_COUNT_EVENT = 'ss:watchlist-count';
export const SERVICE_STATUS_EVENT = 'ss:service-status';
export const RATE_MONITOR_EVENT = 'ss:rate-monitor';

/** The canonical provider ids `ss:enrich-status` carries. */
export type EnrichEventId =
  | 'musicbrainz'
  | 'audiodb'
  | 'discogs'
  | 'deezer'
  | 'jiosaavn'
  | 'spotify'
  | 'itunes'
  | 'lastfm'
  | 'genius'
  | 'bandcamp'
  | 'tidal'
  | 'qobuz'
  | 'amazon'
  | 'similar_artists'
  | 'hydrabase'
  | 'soulid';

export interface EnrichStatusFrame {
  id: EnrichEventId;
  data: ProviderStatusPayload;
}

function useShellEvent<T>(name: string, onFrame: (frame: T) => void): void {
  useEffect(() => {
    const handler = (event: Event) => {
      const detail = (event as CustomEvent<T>).detail;
      if (detail) onFrame(detail);
    };
    window.addEventListener(name, handler);
    return () => window.removeEventListener(name, handler);
  }, [name, onFrame]);
}

export function useEnrichStatusEvent(onFrame: (frame: EnrichStatusFrame) => void): void {
  useShellEvent(ENRICH_STATUS_EVENT, onFrame);
}

export function useDashboardStatsEvent(onFrame: (frame: SystemStats) => void): void {
  useShellEvent(DASHBOARD_STATS_EVENT, onFrame);
}

export function useDashboardActivityEvent(
  onFrame: (frame: { activities?: ActivityItem[] }) => void,
): void {
  useShellEvent(DASHBOARD_ACTIVITY_EVENT, onFrame);
}

export function useDashboardToastEvent(onFrame: (frame: ActivityItem) => void): void {
  useShellEvent(DASHBOARD_TOAST_EVENT, onFrame);
}

export function useDashboardDbStatsEvent(onFrame: (frame: Record<string, unknown>) => void): void {
  useShellEvent(DASHBOARD_DB_STATS_EVENT, onFrame);
}

export function useDashboardWishlistCountEvent(onFrame: (frame: { count?: number }) => void): void {
  useShellEvent(DASHBOARD_WISHLIST_COUNT_EVENT, onFrame);
}

export function useWatchlistCountEvent(
  onFrame: (frame: { count?: number; next_run_in_seconds?: number | null }) => void,
): void {
  useShellEvent(WATCHLIST_COUNT_EVENT, onFrame);
}

export function useServiceStatusEvent(onFrame: (frame: ServiceStatusPayload) => void): void {
  useShellEvent(SERVICE_STATUS_EVENT, onFrame);
}

export function useRateMonitorEvent(onFrame: (frame: Record<string, unknown>) => void): void {
  useShellEvent(RATE_MONITOR_EVENT, onFrame);
}
