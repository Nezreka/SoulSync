import { queryOptions } from '@tanstack/react-query';

import { apiClient, readJson } from '@/app/api-client';

import type {
  AutomationBlocks,
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

// ── mutations ───────────────────────────────────────────────────────────────
//
// All of these mirror the vanilla handlers verb-for-verb. The endpoints answer
// `{error}` on failure with a 200, so an HTTP-level check alone is not enough —
// every one asserts on the payload as the vanilla code did.

interface MutationResponse {
  success?: boolean;
  error?: string;
  /** bulk-toggle / group report how many rows changed. */
  updated?: number;
}

function assertOk(payload: MutationResponse, fallback: string): MutationResponse {
  if (payload.error) throw new Error(payload.error);
  if (payload.success === false) throw new Error(fallback);
  return payload;
}

export async function toggleAutomation(id: number): Promise<void> {
  assertOk(
    await readJson<MutationResponse>(apiClient.post(`automations/${id}/toggle`)),
    'Could not toggle the automation',
  );
}

export async function runAutomation(id: number): Promise<void> {
  assertOk(
    await readJson<MutationResponse>(apiClient.post(`automations/${id}/run`)),
    'Could not start the automation',
  );
}

export async function duplicateAutomation(id: number): Promise<void> {
  assertOk(
    await readJson<MutationResponse>(apiClient.post(`automations/${id}/duplicate`)),
    'Could not duplicate the automation',
  );
}

export async function deleteAutomation(id: number): Promise<void> {
  assertOk(
    await readJson<MutationResponse>(apiClient.delete(`automations/${id}`)),
    'Could not delete the automation',
  );
}

/** Enable/disable every automation in a group in one call. Returns how many changed. */
export async function bulkToggleAutomations(ids: number[], enabled: boolean): Promise<number> {
  const payload = assertOk(
    await readJson<MutationResponse>(
      apiClient.post('automations/bulk-toggle', { json: { automation_ids: ids, enabled } }),
    ),
    'Could not update those automations',
  );
  return payload.updated ?? 0;
}

/**
 * The per-side master pause. Admin-only server-side (403 otherwise), and NOT
 * profile-scoped — it silences the whole side for everyone.
 */
export async function setAutomationsMaster(
  side: 'music' | 'video',
  enabled: boolean,
): Promise<void> {
  assertOk(
    await readJson<MutationResponse>(
      apiClient.post('automations/master', { json: { side, enabled } }),
    ),
    'Could not update the master switch',
  );
}

/**
 * Move one automation into a group (or out of it with null).
 *
 * Note the endpoint: PUT /api/automations/<id> with just {group_name}. The
 * PLURAL /api/automations/group below is a different route used for bulk
 * regrouping — mixing them up silently regroups the wrong set.
 */
export async function assignAutomationGroup(id: number, groupName: string | null): Promise<void> {
  assertOk(
    await readJson<MutationResponse>(
      apiClient.put(`automations/${id}`, { json: { group_name: groupName || null } }),
    ),
    'Could not move the automation',
  );
}

/**
 * Change WHEN an automation runs, without opening the builder.
 *
 * A partial PUT is enough: `update_automation` writes only the keys it is
 * given, and — importantly — nulls `next_run` whenever the trigger shape
 * changes, so the scheduler recomputes the slot instead of keeping the
 * leftover timestamp from the old interval. Getting that for free is why this
 * goes through the same endpoint the builder uses rather than a new one.
 */
export async function updateAutomationTrigger(
  id: number,
  triggerConfig: Record<string, unknown>,
): Promise<void> {
  assertOk(
    await readJson<MutationResponse>(
      apiClient.put(`automations/${id}`, { json: { trigger_config: triggerConfig } }),
    ),
    'Could not change the schedule',
  );
}

/**
 * Bulk-set the group on many automations. Used for rename (same ids, new name)
 * and for dissolving a group (same ids, null). Returns how many changed.
 */
export async function regroupAutomations(ids: number[], groupName: string | null): Promise<number> {
  const payload = assertOk(
    await readJson<MutationResponse>(
      apiClient.put('automations/group', {
        json: { automation_ids: ids, group_name: groupName },
      }),
    ),
    'Could not update the group',
  );
  return payload.updated ?? 0;
}

/**
 * Block definitions — the builder's palette, and the label source for any
 * trigger/action type not in the static maps.
 *
 * Scoped to 'music' server-side, so the video-only types never appear here.
 * Long-lived: these change only when the app ships new block types.
 */
export function automationBlocksQueryOptions() {
  return queryOptions({
    queryKey: [...AUTOMATIONS_QUERY_KEY, 'blocks'] as const,
    queryFn: () => readJson<AutomationBlocks>(apiClient.get('automations/blocks')),
    staleTime: Infinity,
  });
}
