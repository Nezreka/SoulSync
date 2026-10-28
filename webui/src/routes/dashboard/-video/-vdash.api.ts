/**
 * The video dashboard's reads. Every band hydrates on its own, so one slow
 * endpoint never holds the page, and a failed one renders nothing rather
 * than a broken band.
 */

import { queryOptions } from '@tanstack/react-query';

import { apiClient, readJson } from '@/app/api-client';

import type {
  ActiveDownload,
  ContinueItem,
  Coverage,
  ScanStatus,
  VideoDashboardStats,
} from './-vdash.model';

import { DL_POLL_ACTIVE_MS, DL_POLL_IDLE_MS, inFlight } from './-vdash.model';

export const VDASH_KEY = ['video-dashboard'] as const;

export function statsQuery() {
  return queryOptions({
    queryKey: [...VDASH_KEY, 'stats'] as const,
    queryFn: () =>
      readJson<VideoDashboardStats>(apiClient.get('video/dashboard', { timeout: 30_000 })),
    refetchOnWindowFocus: true,
  });
}

/** the one band that changes while you are not looking (you watched
 * something on the TV), so it re-reads on focus */
export function continueQuery() {
  return queryOptions({
    queryKey: [...VDASH_KEY, 'continue'] as const,
    queryFn: async () =>
      (
        await readJson<{ items?: ContinueItem[] }>(
          apiClient.get('video/dashboard/continue-watching'),
        )
      ).items ?? [],
    refetchOnWindowFocus: true,
  });
}

/** fast while something moves, slow when idle. the downloads page is the
 * primary surface, so this never polls faster than it does. */
export function downloadsQuery() {
  return queryOptions({
    queryKey: [...VDASH_KEY, 'downloads'] as const,
    queryFn: async () =>
      (await readJson<{ downloads?: ActiveDownload[] }>(apiClient.get('video/downloads/active')))
        .downloads ?? [],
    refetchInterval: (q) => (inFlight(q.state.data).length ? DL_POLL_ACTIVE_MS : DL_POLL_IDLE_MS),
  });
}

export function scanQuery() {
  return queryOptions({
    queryKey: [...VDASH_KEY, 'scan'] as const,
    queryFn: () => readJson<ScanStatus>(apiClient.get('video/scan/status')),
  });
}

/** open issues, for everyone */
export function issuesQuery() {
  return queryOptions({
    queryKey: [...VDASH_KEY, 'issues'] as const,
    queryFn: async () =>
      (await readJson<{ counts?: { open?: number } }>(apiClient.get('video/issues/counts'))).counts
        ?.open ?? 0,
    retry: false,
  });
}

/** pending maintenance findings. admin only: a 403 just leaves the tile out */
export function findingsQuery(isAdmin: boolean) {
  return queryOptions({
    queryKey: [...VDASH_KEY, 'findings', isAdmin] as const,
    queryFn: async () =>
      isAdmin
        ? ((await readJson<{ pending?: number }>(apiClient.get('video/repair/findings/counts')))
            .pending ?? 0)
        : 0,
    retry: false,
  });
}

export function coverageQuery() {
  return queryOptions({
    queryKey: [...VDASH_KEY, 'coverage'] as const,
    queryFn: () => readJson<Coverage>(apiClient.get('video/enrichment/coverage')),
    staleTime: 10 * 60_000,
  });
}
