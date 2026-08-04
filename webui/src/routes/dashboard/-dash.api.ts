/**
 * Dashboard api layer — raw fetch, one function per vanilla fetch site, each
 * parity-commented with the function it mirrors.
 *
 * Error behaviour is per-endpoint and mirrors the original: the vanilla
 * pollers swallow failures and keep the previous UI, so most fetchers here
 * resolve null/[] rather than throwing.
 */

import type { ProviderStatusPayload } from './-dash.types';

// Two dashboard endpoints already have faithful clients in the tools route
// (the repair orb and the library stats card read the SAME endpoints the tools
// page did). Re-exported rather than duplicated so there is exactly one
// definition to drift.
export { fetchDatabaseStats, fetchRepairStatus } from '../tools/-tools.api';

// ── Provider status: the status-all bundle ───────────────────────────────────

/**
 * The 13 ids `_enrichmentStatusFetch` is called with (enrichment.js). Discogs
 * and SoulID are absent on purpose — they are websocket-only, with no fetcher
 * and no bundle entry. Hydrabase and Repair have their own endpoints.
 */
export const BUNDLE_PROVIDER_IDS = [
  'musicbrainz',
  'audiodb',
  'deezer',
  'jiosaavn',
  'spotify',
  'itunes',
  'lastfm',
  'genius',
  'bandcamp',
  'tidal',
  'qobuz',
  'amazon',
  'similar_artists',
] as const;

export type BundleProviderId = (typeof BUNDLE_PROVIDER_IDS)[number];

/**
 * `_enrichmentStatusFetch` (enrichment.js:12-31), collapsed for a single
 * consumer. The vanilla kept a 3-second-TTL shared bundle because THIRTEEN
 * independent 10s pollers each called it; React has one hydrate, so the TTL
 * cache is dead weight — but the FALLBACK is load-bearing and preserved: any
 * id the bundle misses, or whose payload carries `error`, is re-fetched from
 * its per-service endpoint.
 */
export async function fetchAllProviderStatuses(
  ids: readonly BundleProviderId[] = BUNDLE_PROVIDER_IDS,
): Promise<Partial<Record<BundleProviderId, ProviderStatusPayload>>> {
  let services: Record<string, ProviderStatusPayload & { error?: unknown }> = {};
  try {
    const response = await fetch('/api/enrichment/status-all');
    if (response.ok) {
      const bundle = (await response.json()) as { services?: typeof services };
      services = bundle.services || {};
    }
  } catch {
    // fall through — every id takes the per-service fallback
  }

  const out: Partial<Record<BundleProviderId, ProviderStatusPayload>> = {};
  await Promise.all(
    ids.map(async (id) => {
      const payload = services[id];
      if (payload && !payload.error) {
        out[id] = payload;
        return;
      }
      try {
        const response = await fetch(`/api/enrichment/${id}/status`);
        if (response.ok) out[id] = (await response.json()) as ProviderStatusPayload;
      } catch {
        // absent from the result — the pill keeps its previous state
      }
    }),
  );
  return out;
}

/** The per-service fallback, exposed for single-pill refreshes after a toggle
 *  (`update<X>Status()` re-fetches its own provider on completion). */
export async function fetchProviderStatus(id: string): Promise<ProviderStatusPayload | null> {
  try {
    const response = await fetch(`/api/enrichment/${id}/status`);
    if (!response.ok) return null;
    return (await response.json()) as ProviderStatusPayload;
  } catch {
    return null;
  }
}

/**
 * `toggle<X>Enrichment` — POST /api/enrichment/<id>/pause | /resume. The
 * per-provider toggle QUIRKS (Spotify reading the error body's rate_limited,
 * Discogs toasting on success, Bandcamp refusing while no-auth) live with the
 * pill interactions in P4; this is just the transport.
 */
export async function setProviderRunning(id: string, run: boolean): Promise<Response> {
  return fetch(`/api/enrichment/${id}/${run ? 'resume' : 'pause'}`, { method: 'POST' });
}

// ── Hydrabase — its own worker endpoints, never the bundle ───────────────────

/** `updateHydrabaseStatus` — swallows errors silently, like the original. */
export async function fetchHydrabaseStatus(): Promise<ProviderStatusPayload | null> {
  try {
    const response = await fetch('/api/hydrabase-worker/status');
    if (!response.ok) return null;
    return (await response.json()) as ProviderStatusPayload;
  } catch {
    return null;
  }
}

export async function setHydrabaseRunning(run: boolean): Promise<Response> {
  return fetch(`/api/hydrabase-worker/${run ? 'resume' : 'pause'}`, { method: 'POST' });
}

// ── Dev mode (Hydrabase orb visibility) ──────────────────────────────────────

/**
 * GET /api/dev-mode — the Hydrabase orb (and the vanilla sidebar's
 * #hydrabase-nav) are hidden unless dev mode is active. The vanilla checks
 * this during the settings security load (settings.js) and shows
 * #hydrabase-button-container; the React header does its own check on mount
 * and listens for ss:dev-mode afterwards. Errors keep it hidden, like the
 * original's catch.
 */
export async function fetchDevMode(): Promise<boolean> {
  try {
    const response = await fetch('/api/dev-mode');
    const data = (await response.json()) as { enabled?: boolean };
    return data.enabled === true;
  } catch {
    return false;
  }
}

// ── Service status (/status) ─────────────────────────────────────────────────

/** The subset of the `/status` payload the dashboard reads. Deliberately
 *  loose — `fetchAndUpdateServiceStatus` hands the raw object around and the
 *  service-card presentation (P5) reads deep into it. */
export interface ServiceStatusPayload {
  metadata_source?: Record<string, unknown>;
  media_server?: { connected?: boolean; type?: string } & Record<string, unknown>;
  soulseek?: Record<string, unknown>;
  spotify?: Record<string, unknown>;
  enrichment?: Record<string, unknown>;
  active_media_server?: string;
  active_downloads?: number;
  [key: string]: unknown;
}

/** `fetchAndUpdateServiceStatus` (shared-helpers.js:4007) — GET /status. The
 *  vanilla caches this as `_lastStatusPayload` for the library card's
 *  five-state machine; the React page holds it in state instead. */
export async function fetchServiceStatus(): Promise<ServiceStatusPayload | null> {
  try {
    const response = await fetch('/status');
    if (!response.ok) return null;
    return (await response.json()) as ServiceStatusPayload;
  } catch {
    return null;
  }
}

// ── System stats / activity / toasts / counts ────────────────────────────────

export interface SystemStats {
  active_downloads?: string | number;
  finished_downloads?: string | number;
  download_speed?: string;
  active_syncs?: string | number;
  uptime?: string;
  memory_usage?: string;
  process_memory?: string;
}

/** `fetchAndUpdateSystemStats` (api-monitor.js:922) — GET /api/system/stats. */
export async function fetchSystemStats(): Promise<SystemStats | null> {
  try {
    const response = await fetch('/api/system/stats');
    if (!response.ok) return null;
    return (await response.json()) as SystemStats;
  } catch {
    return null;
  }
}

export interface ActivityItem {
  icon?: string;
  title?: string;
  subtitle?: string;
  /** Unix epoch SECONDS. `time` is a human label ("Now") that does not parse
   *  as a date — the epoch drives relative formatting. */
  timestamp?: number;
  time?: string;
}

/** `fetchAndUpdateActivityFeed` (api-monitor.js:961) — GET /api/activity/feed.
 *  The vanilla's leftover `console.log` of every payload is deliberately NOT
 *  carried over. */
export async function fetchActivityFeed(): Promise<ActivityItem[]> {
  try {
    const response = await fetch('/api/activity/feed');
    if (!response.ok) return [];
    const data = (await response.json()) as { activities?: ActivityItem[] };
    return data.activities || [];
  } catch {
    return [];
  }
}

/** `checkForActivityToasts` (api-monitor.js:1061) — GET /api/activity/toasts.
 *  Silent on every failure, "to avoid spam" per the original. */
export async function fetchActivityToasts(): Promise<ActivityItem[]> {
  try {
    const response = await fetch('/api/activity/toasts');
    if (!response.ok) return [];
    const data = (await response.json()) as { toasts?: ActivityItem[] };
    return data.toasts || [];
  } catch {
    return [];
  }
}

export interface WatchlistCount {
  count: number;
  next_run_in_seconds?: number | null;
}

/** `updateWatchlistButtonCount` (api-monitor.js:1180) — GET /api/watchlist/count.
 *  The payload is only trusted when `success` is set, as in the original. */
export async function fetchWatchlistCount(): Promise<WatchlistCount | null> {
  try {
    const response = await fetch('/api/watchlist/count');
    const data = (await response.json()) as {
      success?: boolean;
      count?: number;
      next_run_in_seconds?: number | null;
    };
    if (!data.success) return null;
    return { count: data.count || 0, next_run_in_seconds: data.next_run_in_seconds };
  } catch {
    return null;
  }
}

/** `updateWishlistCount` (wishlist-tools.js:7608) — GET /api/wishlist/count.
 *  No success flag on this one; `count || 0`, exactly as the original reads it. */
export async function fetchWishlistCount(): Promise<number | null> {
  try {
    const response = await fetch('/api/wishlist/count');
    if (!response.ok) return null;
    const data = (await response.json()) as { count?: number };
    return data.count || 0;
  } catch {
    return null;
  }
}

// ── Recent syncs ─────────────────────────────────────────────────────────────

export interface SyncHistoryEntry {
  id?: number | string;
  sync_type?: string | null;
  source?: string;
  playlist_name?: string;
  artist?: string;
  album?: string;
  tracks_found?: number;
  total_tracks?: number;
  tracks_downloaded?: number;
  tracks_failed?: number;
  [key: string]: unknown;
}

export type SyncHistoryResult =
  | { status: 'ok'; entries: SyncHistoryEntry[] }
  | { status: 'unauthorized'; loginRequired: boolean }
  | { status: 'error' };

/**
 * `loadDashboardSyncHistory` (pages-extra.js) — GET /api/sync/history?limit=10.
 *
 * A 401 is a real state, not an error: the session lapsed while the tab
 * believed it was unlocked, and the caller must surface the correct unlock
 * screen (`loginRequired` picks login vs launch-PIN). The playlist filter is
 * the vanilla's: keep `sync_type === 'playlist'` OR entries with no sync_type,
 * excluding album downloads and wishlist runs.
 */
export async function fetchDashboardSyncHistory(): Promise<SyncHistoryResult> {
  try {
    const response = await fetch('/api/sync/history?limit=10');
    if (response.status === 401) {
      const info = (await response.json().catch(() => ({}))) as { login_required?: boolean };
      return { status: 'unauthorized', loginRequired: Boolean(info.login_required) };
    }
    if (!response.ok) return { status: 'error' };
    const data = (await response.json()) as { entries?: SyncHistoryEntry[] };
    const entries = (data.entries || []).filter(
      (entry) => entry.sync_type === 'playlist' || !entry.sync_type,
    );
    return { status: 'ok', entries };
  } catch {
    return { status: 'error' };
  }
}

// ── Library scans ────────────────────────────────────────────────────────────

export interface LibraryScanStatus {
  status?: string;
  phase?: string;
  progress?: number;
  processed?: number;
  total?: number;
  error_message?: string;
}

/** `dashboardLibraryScan` — POST /api/database/update { full_refresh }. */
export async function startLibraryScan(fullRefresh: boolean): Promise<Response> {
  return fetch('/api/database/update', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ full_refresh: fullRefresh }),
  });
}

/** `dashboardLibraryDeepScan` — POST /api/database/update { deep_scan: true }. */
export async function startLibraryDeepScan(): Promise<Response> {
  return fetch('/api/database/update', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ deep_scan: true }),
  });
}

/** The scan button's toggle half: POST /api/database/update/stop. */
export async function stopLibraryScan(): Promise<Response> {
  return fetch('/api/database/update/stop', { method: 'POST' });
}

/** The 2s progress poll — terminal when status is
 *  completed | finished | error | idle. */
export async function fetchLibraryScanStatus(): Promise<LibraryScanStatus | null> {
  try {
    const response = await fetch('/api/database/update/status');
    if (!response.ok) return null;
    return (await response.json()) as LibraryScanStatus;
  } catch {
    return null;
  }
}

/** The terminal-state test, verbatim from both scan pollers. */
export function isLibraryScanTerminal(status: string | undefined): boolean {
  return status === 'completed' || status === 'finished' || status === 'error' || status === 'idle';
}
