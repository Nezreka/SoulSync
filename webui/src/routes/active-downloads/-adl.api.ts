import { apiClient, readJson } from '@/app/api-client';

import type {
  AdlDeletedList,
  ClientOverview,
  ClientSlskdItem,
  ClientTorrentItem,
  ClientUsenetItem,
  AdlBatchHistoryEntry,
  AdlDownloadsResponse,
  AdlQuarantineEntry,
  AdlReviewSummary,
  AdlTaskDetail,
  AdlVerificationConfig,
} from './-adl.types';

/** Shape every mutating endpoint here answers with. */
export interface AdlResult {
  success?: boolean;
  error?: string;
}

// ── Reads ─────────────────────────────────────────────────────────────────

/**
 * The whole downloads list plus live batch summaries.
 *
 * The 300 cap is the vanilla's and matters downstream: it is exactly why the
 * page must NOT compute the nav badge from this array — see the controller.
 * A failure returns empty rather than throwing, because this runs on a 2s
 * poll and one bad response must not tear the page down.
 */
export async function fetchDownloads(): Promise<AdlDownloadsResponse> {
  try {
    const data = await readJson<AdlDownloadsResponse>(
      apiClient.get('downloads/all', { searchParams: { limit: 300 } }),
    );
    return data?.success ? data : {};
  } catch {
    return {};
  }
}

/**
 * Per-task detail for an expanded terminal row (#1156) — merges the live task
 * with its library_history record (source, quality, AcoustID verdict, file
 * path, expected-vs-downloaded). Null on any failure: the expansion degrades
 * to the fields the row already carries rather than erroring.
 */
export async function fetchTaskDetail(taskId: string): Promise<AdlTaskDetail | null> {
  try {
    const data = await readJson<{ success?: boolean; detail?: AdlTaskDetail }>(
      apiClient.get(`downloads/task/${encodeURIComponent(taskId)}/detail`),
    );
    return data?.success && data.detail ? data.detail : null;
  } catch {
    return null;
  }
}

/** Completed batches for the history rail — 7 days, 50 max, as the vanilla asked. */
export async function fetchBatchHistory(): Promise<AdlBatchHistoryEntry[]> {
  try {
    const data = await readJson<{ success?: boolean; history?: AdlBatchHistoryEntry[] }>(
      apiClient.get('downloads/batch-history', { searchParams: { days: 7, limit: 50 } }),
    );
    return data?.success ? (data.history ?? []) : [];
  } catch {
    return [];
  }
}

/**
 * Whether the unverified review queue can exist at all.
 *
 * Two ways it cannot: AcoustID is off (nothing ever gets a verification
 * status), or require_verified is on (unconfirmed tracks are quarantined
 * instead of imported unverified). The caller collapses to quarantine-only in
 * both cases. A failed fetch assumes ENABLED — hiding the queue because a
 * config read blipped would be worse than showing an empty one.
 */
export async function fetchVerificationConfig(): Promise<AdlVerificationConfig> {
  try {
    return await readJson<AdlVerificationConfig>(apiClient.get('verification/config'));
  } catch {
    return { acoustid_enabled: true };
  }
}

/**
 * Counts for the review badge.
 *
 * Separate from `fetchQuarantine` on purpose: that one reads every sidecar to
 * build the list, this is a listdir plus one indexed count, so it can ride a
 * poll without costing anything.
 */
export async function fetchReviewQueueSummary(): Promise<AdlReviewSummary | null> {
  try {
    const data = await readJson<{ success?: boolean } & Partial<AdlReviewSummary>>(
      apiClient.get('review-queue/summary'),
    );
    if (!data?.success) return null;
    return {
      quarantine: data.quarantine ?? 0,
      unverified: data.unverified ?? 0,
      total: data.total ?? 0,
    };
  } catch {
    // null, not zeroes. a blipped fetch must not redraw the badge as "nothing
    // to review" when there might be plenty.
    return null;
  }
}

export async function fetchQuarantine(): Promise<AdlQuarantineEntry[]> {
  try {
    const data = await readJson<{ success?: boolean; entries?: AdlQuarantineEntry[] }>(
      apiClient.get('quarantine/list'),
    );
    return data?.success && Array.isArray(data.entries) ? data.entries : [];
  } catch {
    return [];
  }
}

// ── Deleted-files manager (the music recycle bin) ─────────────────────────

export async function fetchDeletedFiles(): Promise<AdlDeletedList | null> {
  try {
    const data = await readJson<{ success?: boolean } & Partial<AdlDeletedList>>(
      apiClient.get('deleted-files'),
    );
    if (!data?.success) return null;
    return {
      entries: Array.isArray(data.entries) ? data.entries : [],
      total_size: data.total_size ?? 0,
      count: data.count ?? 0,
      keep_days: data.keep_days ?? 0,
    };
  } catch {
    return null;
  }
}

export interface DeletedActionResult extends AdlResult {
  restored?: string[];
  purged?: string[];
  errors?: { id: string; error: string }[];
}

export function restoreDeletedFiles(ids: string[]): Promise<DeletedActionResult> {
  return readJson<DeletedActionResult>(apiClient.post('deleted-files/restore', { json: { ids } }));
}

export function purgeDeletedFiles(
  ids: string[] | null,
  all = false,
): Promise<DeletedActionResult> {
  return readJson<DeletedActionResult>(
    apiClient.post('deleted-files/purge', { json: all ? { all: true } : { ids: ids ?? [] } }),
  );
}

export function setDeletedRetention(days: number): Promise<AdlResult & { keep_days?: number }> {
  return readJson<AdlResult & { keep_days?: number }>(
    apiClient.post('deleted-files/retention', { json: { days } }),
  );
}

// ── Downloads mutations ───────────────────────────────────────────────────

export interface ClearCompletedResult extends AdlResult {
  cleared?: number;
  total_cleared?: number;
}

export function clearCompleted(): Promise<ClearCompletedResult> {
  return readJson<ClearCompletedResult>(apiClient.post('downloads/clear-completed'));
}

export interface CancelTaskResult extends AdlResult {
  task_info?: { track_name?: string };
}

/**
 * Cancel one queued/active track.
 *
 * Coordinates are (playlist_id, track_index) — NOT task_id. This is the same
 * atomic endpoint the download modals use, which is what frees the worker slot
 * properly; cancelling by task id alone leaves the slot held.
 */
export function cancelTask(playlistId: string, trackIndex: number): Promise<CancelTaskResult> {
  return readJson<CancelTaskResult>(
    apiClient.post('downloads/cancel_task_v2', {
      json: { playlist_id: playlistId, track_index: trackIndex },
    }),
  );
}

export interface CancelBatchResult extends AdlResult {
  cancelled_tasks?: number;
}

/** Note the path: batches cancel through /api/playlists/, not /api/downloads/. */
export function cancelBatch(batchId: string): Promise<CancelBatchResult> {
  return readJson<CancelBatchResult>(
    apiClient.post(`playlists/${encodeURIComponent(batchId)}/cancel_batch`),
  );
}

// ── Verification (imported-but-unconfirmed) ───────────────────────────────

export function verificationPlay(historyId: string): Promise<AdlResult> {
  return readJson<AdlResult>(apiClient.post(`verification/${encodeURIComponent(historyId)}/play`));
}

export interface CompareStreamResult extends AdlResult {
  /** A search result the media player can stream, shaped like a download row. */
  result?: unknown;
}

export function verificationCompareStream(historyId: string): Promise<CompareStreamResult> {
  return readJson<CompareStreamResult>(
    apiClient.post(`verification/${encodeURIComponent(historyId)}/compare-stream`),
  );
}

export interface EntryResult extends AdlResult {
  entry?: unknown;
}

export function verificationEntry(historyId: string): Promise<EntryResult> {
  return readJson<EntryResult>(
    apiClient.get(`verification/${encodeURIComponent(historyId)}/entry`),
  );
}

export function verificationApprove(historyId: string): Promise<AdlResult> {
  return readJson<AdlResult>(
    apiClient.post(`verification/${encodeURIComponent(historyId)}/approve`),
  );
}

export function verificationDelete(historyId: string): Promise<AdlResult> {
  return readJson<AdlResult>(
    apiClient.post(`verification/${encodeURIComponent(historyId)}/delete`),
  );
}

export interface CleanOrphansResult extends AdlResult {
  removed?: number;
  checked?: number;
}

export function verificationCleanOrphans(): Promise<CleanOrphansResult> {
  return readJson<CleanOrphansResult>(apiClient.post('verification/clean-orphans'));
}

// ── Quarantine (never imported) ───────────────────────────────────────────

export function quarantinePlay(id: string): Promise<AdlResult> {
  return readJson<AdlResult>(apiClient.post(`quarantine/${encodeURIComponent(id)}/play`));
}

export function quarantineCompareStream(id: string): Promise<CompareStreamResult> {
  return readJson<CompareStreamResult>(
    apiClient.post(`quarantine/${encodeURIComponent(id)}/compare-stream`),
  );
}

export function quarantineEntry(id: string): Promise<EntryResult> {
  return readJson<EntryResult>(apiClient.get(`quarantine/${encodeURIComponent(id)}/entry`));
}

export interface QuarantineApproveResult extends AdlResult {
  removed_siblings?: unknown[];
}

/**
 * Approve and re-import one quarantined file.
 *
 * `remove_siblings: true` is not optional dressing — a quarantined track
 * usually has several rejected candidates alongside it, and approving one
 * without clearing the rest leaves duplicates behind in the queue.
 */
export function quarantineApprove(id: string): Promise<QuarantineApproveResult> {
  return readJson<QuarantineApproveResult>(
    apiClient.post(`quarantine/${encodeURIComponent(id)}/approve`, {
      json: { remove_siblings: true },
    }),
  );
}

/** For legacy sidecars with no embedded context — the only option they have. */
export function quarantineRecover(id: string): Promise<AdlResult> {
  return readJson<AdlResult>(apiClient.post(`quarantine/${encodeURIComponent(id)}/recover`));
}

export function quarantineDelete(id: string): Promise<AdlResult> {
  return readJson<AdlResult>(apiClient.delete(`quarantine/${encodeURIComponent(id)}`));
}

export function quarantineClear(): Promise<AdlResult> {
  return readJson<AdlResult>(apiClient.post('quarantine/clear'));
}

// ── The Clients tab (external download clients) ───────────────────────────

async function fetchClientOverview<T>(path: string): Promise<ClientOverview<T> | null> {
  try {
    const data = await readJson<{ success?: boolean } & Partial<ClientOverview<T>>>(
      apiClient.get(path),
    );
    if (!data?.success) return null;
    return {
      configured: Boolean(data.configured),
      connected: Boolean(data.connected),
      type: data.type,
      error: data.error,
      items: Array.isArray(data.items) ? data.items : [],
    };
  } catch {
    return null;
  }
}

export function fetchTorrentClient(): Promise<ClientOverview<ClientTorrentItem> | null> {
  return fetchClientOverview('clients/torrent');
}

export function fetchUsenetClient(): Promise<ClientOverview<ClientUsenetItem> | null> {
  return fetchClientOverview('clients/usenet');
}

export function fetchSlskdClient(): Promise<ClientOverview<ClientSlskdItem> | null> {
  return fetchClientOverview('clients/slskd');
}

export type ClientAction = 'pause' | 'resume' | 'remove';

export function torrentClientAction(
  id: string,
  action: ClientAction,
  deleteFiles = false,
): Promise<AdlResult> {
  return readJson<AdlResult>(
    apiClient.post('clients/torrent/action', {
      json: { id, action, delete_files: deleteFiles },
    }),
  );
}

export function usenetClientAction(
  id: string,
  action: ClientAction,
  deleteFiles = false,
): Promise<AdlResult> {
  return readJson<AdlResult>(
    apiClient.post('clients/usenet/action', {
      json: { id, action, delete_files: deleteFiles },
    }),
  );
}

export function slskdClientCancel(
  id: string,
  username: string,
  remove = false,
): Promise<AdlResult> {
  return readJson<AdlResult>(
    apiClient.post('clients/slskd/action', { json: { id, username, action: 'cancel', remove } }),
  );
}
