import { queryOptions, type QueryClient } from '@tanstack/react-query';
import { HTTPError } from 'ky';

import { apiClient, readJson } from '@/app/api-client';

import type {
  ListeningStatsStatus,
  StatsCachedPayload,
  StatsDbStoragePayload,
  StatsLibraryDiskUsagePayload,
  StatsRange,
  StatsResolveTrackPayload,
  StatsStreamTrackPayload,
} from './-stats.types';

import { EMPTY_STATS_PAYLOAD } from './-stats.helpers';

export const STATS_QUERY_KEY = ['stats'] as const;

const NO_STATS_YET_PATTERNS = [
  /not synced/i,
  /no listening stats/i,
  /no cached stats/i,
  /cache miss/i,
  /stats cache.*(missing|empty|not found)/i,
] as const;

function isNoStatsYetMessage(message: string | undefined): boolean {
  if (!message) return false;
  return NO_STATS_YET_PATTERNS.some((pattern) => pattern.test(message));
}

function getEmptyStatsPayload(): StatsCachedPayload {
  return {
    success: true,
    ...EMPTY_STATS_PAYLOAD,
  };
}

export async function fetchStatsCached(range: StatsRange): Promise<StatsCachedPayload> {
  try {
    const payload = await readJson<StatsCachedPayload>(
      apiClient.get('stats/cached', {
        searchParams: { range },
      }),
    );
    if (!payload.success) {
      if (isNoStatsYetMessage(payload.error)) {
        return getEmptyStatsPayload();
      }
      throw new Error(payload.error || 'Failed to load listening stats');
    }
    return payload;
  } catch (error) {
    if (error instanceof HTTPError && isNoStatsYetMessage(error.message)) {
      return getEmptyStatsPayload();
    }
    throw error;
  }
}

export async function fetchListeningStatsStatus(): Promise<ListeningStatsStatus> {
  return await readJson<ListeningStatsStatus>(apiClient.get('listening-stats/status'));
}

export async function fetchStatsDbStorage(): Promise<StatsDbStoragePayload> {
  const payload = await readJson<StatsDbStoragePayload>(apiClient.get('stats/db-storage'));
  if (!payload.success) {
    throw new Error(payload.error || 'Failed to load database storage');
  }
  return payload;
}

export async function fetchStatsLibraryDiskUsage(): Promise<StatsLibraryDiskUsagePayload> {
  const payload = await readJson<StatsLibraryDiskUsagePayload>(
    apiClient.get('stats/library-disk-usage'),
  );
  if (!payload.success) {
    throw new Error(payload.error || 'Failed to load library disk usage');
  }
  return payload;
}

export async function triggerListeningStatsSync(): Promise<void> {
  const payload = await readJson<{ success: boolean; error?: string }>(
    apiClient.post('listening-stats/sync'),
  );
  if (!payload.success) {
    throw new Error(payload.error || 'Sync failed');
  }
}

export async function resolveStatsTrack(
  title: string,
  artist: string,
): Promise<StatsResolveTrackPayload['track'] | null> {
  const payload = await readJson<StatsResolveTrackPayload>(
    apiClient.post('stats/resolve-track', {
      json: { title, artist },
    }),
  );
  if (!payload.success) return null;
  return payload.track ?? null;
}

export async function streamStatsTrack(
  title: string,
  artist: string,
  album: string,
): Promise<Record<string, unknown> | null> {
  const payload = await readJson<StatsStreamTrackPayload>(
    apiClient.post('enhanced-search/stream-track', {
      json: {
        track_name: title,
        artist_name: artist,
        album_name: album,
        duration_ms: 0,
      },
    }),
  );
  if (!payload.success) {
    throw new Error(payload.error || 'Track not found in library or any source');
  }
  return payload.result ?? null;
}

export function statsCachedQueryOptions(range: StatsRange) {
  return queryOptions({
    queryKey: [...STATS_QUERY_KEY, 'cached', range],
    queryFn: () => fetchStatsCached(range),
  });
}

export function listeningStatsStatusQueryOptions() {
  return queryOptions({
    queryKey: [...STATS_QUERY_KEY, 'listening-status'],
    queryFn: fetchListeningStatsStatus,
  });
}

export function statsDbStorageQueryOptions() {
  return queryOptions({
    queryKey: [...STATS_QUERY_KEY, 'db-storage'],
    queryFn: fetchStatsDbStorage,
  });
}

export function statsLibraryDiskUsageQueryOptions() {
  return queryOptions({
    queryKey: [...STATS_QUERY_KEY, 'library-disk-usage'],
    queryFn: fetchStatsLibraryDiskUsage,
  });
}

export function invalidateStatsQueries(queryClient: QueryClient) {
  return queryClient.invalidateQueries({ queryKey: STATS_QUERY_KEY });
}
