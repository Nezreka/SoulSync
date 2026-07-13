import { queryOptions, type QueryClient } from '@tanstack/react-query';

import { apiClient, readJson } from '@/app/api-client';

import type {
  ConfigUpdateResponse,
  KindsResponse,
  PlaylistDetailResponse,
  PlaylistsResponse,
  RefreshResponse,
} from './-playlists.types';

export const PLAYLISTS_QUERY_KEY = ['personalized'] as const;

export async function fetchKinds(): Promise<KindsResponse> {
  return await readJson<KindsResponse>(apiClient.get('personalized/kinds'));
}

export async function fetchPlaylists(): Promise<PlaylistsResponse> {
  return await readJson<PlaylistsResponse>(apiClient.get('personalized/playlists'));
}

export async function fetchPlaylistDetail(
  kind: string,
  variant: string = '',
): Promise<PlaylistDetailResponse> {
  const path = variant
    ? `personalized/playlist/${encodeURIComponent(kind)}/${encodeURIComponent(variant)}`
    : `personalized/playlist/${encodeURIComponent(kind)}`;
  return await readJson<PlaylistDetailResponse>(apiClient.get(path));
}

export async function updatePlaylistConfig(
  kind: string,
  variant: string = '',
  config: Record<string, unknown>,
): Promise<ConfigUpdateResponse> {
  const path = variant
    ? `personalized/playlist/${encodeURIComponent(kind)}/${encodeURIComponent(variant)}/config`
    : `personalized/playlist/${encodeURIComponent(kind)}/config`;
  return await readJson<ConfigUpdateResponse>(apiClient.put(path, { json: config }));
}

export async function refreshPlaylist(
  kind: string,
  variant: string = '',
): Promise<RefreshResponse> {
  const path = variant
    ? `personalized/playlist/${encodeURIComponent(kind)}/${encodeURIComponent(variant)}/refresh`
    : `personalized/playlist/${encodeURIComponent(kind)}/refresh`;
  return await readJson<RefreshResponse>(apiClient.post(path));
}

export async function activatePlaylist(
  kind: string,
  variant: string = '',
  refreshIntervalHours: number = 24,
): Promise<RefreshResponse> {
  const path = variant
    ? `personalized/playlist/${encodeURIComponent(kind)}/${encodeURIComponent(variant)}/activate`
    : `personalized/playlist/${encodeURIComponent(kind)}/activate`;
  return await readJson<RefreshResponse>(
    apiClient.post(path, { json: { refresh_interval_hours: refreshIntervalHours } }),
  );
}

export async function toggleAutoRefresh(
  kind: string,
  variant: string = '',
  autoRefresh?: boolean,
  refreshIntervalHours?: number,
): Promise<ConfigUpdateResponse> {
  const path = variant
    ? `personalized/playlist/${encodeURIComponent(kind)}/${encodeURIComponent(variant)}/auto-refresh`
    : `personalized/playlist/${encodeURIComponent(kind)}/auto-refresh`;
  const body: Record<string, unknown> = {};
  if (autoRefresh !== undefined) body.auto_refresh = autoRefresh;
  if (refreshIntervalHours !== undefined) body.refresh_interval_hours = refreshIntervalHours;
  return await readJson<ConfigUpdateResponse>(apiClient.put(path, { json: body }));
}

export async function deletePlaylist(
  kind: string,
  variant: string = '',
): Promise<{ success: boolean }> {
  const path = variant
    ? `personalized/playlist/${encodeURIComponent(kind)}/${encodeURIComponent(variant)}`
    : `personalized/playlist/${encodeURIComponent(kind)}`;
  return await readJson<{ success: boolean }>(apiClient.delete(path));
}

export function kindsQueryOptions() {
  return queryOptions({
    queryKey: [...PLAYLISTS_QUERY_KEY, 'kinds'],
    queryFn: fetchKinds,
    staleTime: 60_000,
  });
}

export function playlistsQueryOptions() {
  return queryOptions({
    queryKey: [...PLAYLISTS_QUERY_KEY, 'list'],
    queryFn: fetchPlaylists,
    staleTime: 10_000,
  });
}

export function playlistDetailQueryOptions(kind: string, variant: string = '') {
  return queryOptions({
    queryKey: [...PLAYLISTS_QUERY_KEY, 'detail', kind, variant],
    queryFn: () => fetchPlaylistDetail(kind, variant),
    staleTime: 10_000,
  });
}

export function invalidatePlaylistsQueries(queryClient: QueryClient) {
  return queryClient.invalidateQueries({ queryKey: PLAYLISTS_QUERY_KEY });
}
