import { queryOptions } from '@tanstack/react-query';

import { apiClient, readJson } from '@/app/api-client';

import type {
  WatchlistArtist,
  WatchlistArtistsResponse,
  WatchlistCountResponse,
  WatchlistGlobalConfig,
  WatchlistGlobalConfigResponse,
  WatchlistLabel,
  WatchlistLabelsResponse,
  WatchlistScanStatusResponse,
} from './-watchlist.types';

export const WATCHLIST_QUERY_KEY = ['watchlist'] as const;

// The watchlist endpoints resolve the profile from the Flask SESSION
// (`get_current_profile_id` reads `g.profile_id`, set from the session cookie in
// `_set_profile_context`) — unlike /api/issues/*, which reads an X-Profile-Id
// header. So no profile header is sent here; sending one would be ignored.
//
// The profile id still belongs in every query key: switching profiles must not
// serve the previous profile's watchlist out of the cache.

export function watchlistCountQueryOptions(profileId: number) {
  return queryOptions({
    queryKey: [...WATCHLIST_QUERY_KEY, 'count', profileId] as const,
    queryFn: () => fetchWatchlistCount(),
  });
}

export function watchlistArtistsQueryOptions(profileId: number) {
  return queryOptions({
    queryKey: [...WATCHLIST_QUERY_KEY, 'artists', profileId] as const,
    queryFn: () => fetchWatchlistArtists(),
  });
}

export function watchlistScanStatusQueryOptions(profileId: number) {
  return queryOptions({
    queryKey: [...WATCHLIST_QUERY_KEY, 'scan-status', profileId] as const,
    queryFn: () => fetchWatchlistScanStatus(),
  });
}

export function watchlistGlobalConfigQueryOptions(profileId: number) {
  return queryOptions({
    queryKey: [...WATCHLIST_QUERY_KEY, 'global-config', profileId] as const,
    queryFn: () => fetchWatchlistGlobalConfig(),
  });
}

export function watchlistLabelsQueryOptions(profileId: number) {
  return queryOptions({
    queryKey: [...WATCHLIST_QUERY_KEY, 'labels', profileId] as const,
    queryFn: () => fetchWatchlistLabels(),
  });
}

// ---------------------------------------------------------------------------
// Reads
// ---------------------------------------------------------------------------

export interface WatchlistCount {
  count: number;
  nextRunInSeconds: number;
}

export async function fetchWatchlistCount(): Promise<WatchlistCount> {
  const payload = await readJson<WatchlistCountResponse>(apiClient.get('watchlist/count'));
  if (!payload.success) {
    throw new Error(payload.error || 'Failed to load watchlist count');
  }
  return {
    count: payload.count ?? 0,
    nextRunInSeconds: payload.next_run_in_seconds ?? 0,
  };
}

export async function fetchWatchlistArtists(): Promise<WatchlistArtist[]> {
  const payload = await readJson<WatchlistArtistsResponse>(apiClient.get('watchlist/artists'));
  if (!payload.success) {
    throw new Error(payload.error || 'Failed to load watchlist');
  }
  return payload.artists ?? [];
}

export async function fetchWatchlistScanStatus(): Promise<WatchlistScanStatusResponse> {
  const payload = await readJson<WatchlistScanStatusResponse>(
    apiClient.get('watchlist/scan/status'),
  );
  if (!payload.success) {
    throw new Error(payload.error || 'Failed to load scan status');
  }
  return payload;
}

export async function fetchWatchlistGlobalConfig(): Promise<WatchlistGlobalConfig | null> {
  // The vanilla page caught this one and carried on with the banner hidden —
  // a missing global config must not take the whole page down with it.
  try {
    const payload = await readJson<WatchlistGlobalConfigResponse>(
      apiClient.get('watchlist/global-config'),
    );
    return payload.success ? (payload.config ?? null) : null;
  } catch {
    return null;
  }
}

export async function fetchWatchlistLabels(): Promise<WatchlistLabel[]> {
  // This endpoint has no `success` key and answers `{labels: []}` on its own
  // errors, so there is nothing to check beyond the shape.
  const payload = await readJson<WatchlistLabelsResponse>(apiClient.get('labels/watchlist'));
  return payload.labels ?? [];
}

// ---------------------------------------------------------------------------
// Writes
// ---------------------------------------------------------------------------

interface SuccessResponse {
  success?: boolean;
  error?: string;
}

function assertSuccess(payload: SuccessResponse, fallback: string): void {
  if (payload.success === false) {
    throw new Error(payload.error || fallback);
  }
}

export async function removeWatchlistArtist(artistId: string): Promise<void> {
  const payload = await readJson<SuccessResponse>(
    apiClient.post('watchlist/remove', { json: { artist_id: artistId } }),
  );
  assertSuccess(payload, 'Failed to remove artist from watchlist');
}

export async function removeWatchlistArtistsBatch(artistIds: string[]): Promise<void> {
  const payload = await readJson<SuccessResponse>(
    apiClient.post('watchlist/remove-batch', { json: { artist_ids: artistIds } }),
  );
  assertSuccess(payload, 'Failed to remove artists from watchlist');
}

export async function startWatchlistScan(): Promise<void> {
  const payload = await readJson<SuccessResponse>(apiClient.post('watchlist/scan'));
  assertSuccess(payload, 'Failed to start watchlist scan');
}

export async function cancelWatchlistScan(): Promise<void> {
  const payload = await readJson<SuccessResponse>(apiClient.post('watchlist/scan/cancel'));
  assertSuccess(payload, 'Failed to cancel watchlist scan');
}

export async function saveWatchlistGlobalConfig(
  config: WatchlistGlobalConfig,
): Promise<WatchlistGlobalConfig> {
  const payload = await readJson<WatchlistGlobalConfigResponse>(
    apiClient.post('watchlist/global-config', { json: config }),
  );
  if (!payload.success) {
    // The server rejects a config with every release type off; surface that
    // message rather than a generic one, it is the only way the user learns why.
    throw new Error(payload.error || 'Failed to save global watchlist settings');
  }
  return payload.config ?? config;
}

export async function setWatchlistLabelBacklog(mbid: string, backlog: boolean): Promise<void> {
  const payload = await readJson<SuccessResponse>(
    apiClient.post('labels/watchlist/backlog', {
      json: { musicbrainz_label_id: mbid, backlog },
    }),
  );
  assertSuccess(payload, 'Failed to update label backlog');
}

export async function removeWatchlistLabel(mbid: string): Promise<void> {
  const payload = await readJson<SuccessResponse>(
    apiClient.post('labels/watchlist/remove', { json: { musicbrainz_label_id: mbid } }),
  );
  assertSuccess(payload, 'Failed to unfollow label');
}
