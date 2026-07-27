import { queryOptions } from '@tanstack/react-query';

import { apiClient, readJson } from '@/app/api-client';

import type {
  AutomationsListResponse,
  AutomationsMasterState,
  AutomationsProgressResponse,
} from './-automations.types';

export const AUTOMATIONS_QUERY_KEY = ['automations'] as const;

// /api/automations resolves the profile from the Flask SESSION, so no profile
// header is sent — but the profile still keys the query: automations are
// per-profile rows, and switching profiles must not serve the previous
// profile's list out of the cache.

export function automationsListQueryOptions(profileId: number) {
  return queryOptions({
    queryKey: [...AUTOMATIONS_QUERY_KEY, 'list', profileId] as const,
    queryFn: () => readJson<AutomationsListResponse>(apiClient.get('automations')),
  });
}

/**
 * The per-side global pause. Not profile-scoped: it lives in the engine, gates
 * every automation on that side for everyone, and is admin-only to change.
 */
export function automationsMasterQueryOptions() {
  return queryOptions({
    queryKey: [...AUTOMATIONS_QUERY_KEY, 'master'] as const,
    queryFn: () => readJson<AutomationsMasterState>(apiClient.get('automations/master')),
  });
}

/**
 * In-flight run progress. The vanilla page fetched this once after rendering
 * to catch up on runs already going when the page opened; live updates arrive
 * over the socket.
 */
export function automationsProgressQueryOptions() {
  return queryOptions({
    queryKey: [...AUTOMATIONS_QUERY_KEY, 'progress'] as const,
    queryFn: () => readJson<AutomationsProgressResponse>(apiClient.get('automations/progress')),
  });
}
