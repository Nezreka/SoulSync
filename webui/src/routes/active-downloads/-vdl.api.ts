/**
 * The video downloads data adapter.
 *
 * One function per `/api/video/downloads/*` endpoint (the
 * `api/video/downloads.py` blueprint — read here, never changed). Reads never
 * throw: they run on a 2s poll and one bad response must not tear the page
 * down, so each swallows and returns an honest empty value. Mutations return
 * the server's payload (or null on transport failure) and let the caller
 * decide what the user sees.
 */

import { apiClient, readJson } from '@/app/api-client';

import type {
  VdlBlockEntry,
  VdlDownload,
  VdlHistoryCounts,
  VdlHistoryItem,
  VdlHistoryTab,
  VdlMeta,
  VdlRecycleBin,
  VdlYtMeta,
} from './-vdl.types';

/* ── reads (never throw) ───────────────────────────────────────────────── */

/** The whole active-downloads list. A failure keeps the old list, not []. */
export async function fetchVideoDownloads(): Promise<{ downloads?: VdlDownload[] }> {
  try {
    const data = await readJson<{ downloads?: VdlDownload[] }>(
      apiClient.get('video/downloads/active'),
    );
    return data && Array.isArray(data.downloads) ? { downloads: data.downloads } : {};
  } catch {
    return {};
  }
}

/**
 * Lazy TMDB detail for an expanded card. Null on any failure: the drawer
 * degrades to the download facts rather than erroring.
 */
export async function fetchVideoMeta(url: string): Promise<VdlMeta | null> {
  try {
    const path = url.replace(/^\//, '').replace(/^api\//, '');
    const data = await readJson<VdlMeta>(apiClient.get(path));
    return data && typeof data === 'object' ? data : null;
  } catch {
    return null;
  }
}

/** Cached YouTube detail for an expanded YouTube card. Null on any failure. */
export async function fetchVideoYtMeta(videoId: string | number): Promise<VdlYtMeta | null> {
  try {
    const data = await readJson<VdlYtMeta>(
      apiClient.get(`video/downloads/yt-meta/${encodeURIComponent(String(videoId))}`),
    );
    return data && typeof data === 'object' ? data : null;
  } catch {
    return null;
  }
}

/** The recycle bin. Null on failure (the pane shows "couldn't load" instead). */
export async function fetchVideoRecycle(): Promise<VdlRecycleBin | null> {
  try {
    const data = await readJson<{ items?: VdlRecycleBin['items'] } & Partial<VdlRecycleBin>>(
      apiClient.get('video/downloads/recycle'),
    );
    if (!data || !Array.isArray(data.items)) return null;
    return {
      items: data.items,
      recycle_enabled: data.recycle_enabled,
      keep_days: data.keep_days,
    };
  } catch {
    return null;
  }
}

/** The release blocklist. [] on failure — the pane still renders. */
export async function fetchVideoBlocklist(): Promise<VdlBlockEntry[]> {
  try {
    const data = await readJson<{ items?: VdlBlockEntry[] }>(
      apiClient.get('video/downloads/blocklist'),
    );
    return Array.isArray(data?.items) ? data.items : [];
  } catch {
    return [];
  }
}

export interface VdlHistoryPage {
  items: VdlHistoryItem[];
  counts: VdlHistoryCounts;
  totalPages: number;
}

/** One page of the permanent download history. Never throws. */
export async function fetchVideoHistory(
  tab: VdlHistoryTab,
  search: string,
  page: number,
  limit = 40,
): Promise<VdlHistoryPage> {
  const empty: VdlHistoryPage = { items: [], counts: {}, totalPages: 1 };
  try {
    const params: Record<string, string | number> = { page, limit, search };
    if (tab !== 'all') params.kind = tab;
    const data = await readJson<{
      success?: boolean;
      items?: VdlHistoryItem[];
      counts?: VdlHistoryCounts;
      pagination?: { total_pages?: number };
    }>(apiClient.get('video/downloads/history', { searchParams: params }));
    if (!data?.success) return empty;
    return {
      items: Array.isArray(data.items) ? data.items : [],
      counts: data.counts ?? {},
      totalPages: data.pagination?.total_pages ?? 1,
    };
  } catch {
    return empty;
  }
}

/** Cheap counts call for the header's history badge. Null on failure. */
export async function fetchVideoHistoryCount(): Promise<number | null> {
  try {
    const data = await readJson<{ success?: boolean; counts?: VdlHistoryCounts }>(
      apiClient.get('video/downloads/history', { searchParams: { page: 1, limit: 1 } }),
    );
    return data?.success ? (data.counts?.total ?? 0) : null;
  } catch {
    return null;
  }
}

/* ── mutations (null = transport failure) ──────────────────────────────── */

export interface VdlResult {
  ok?: boolean;
  success?: boolean;
  error?: string;
  [key: string]: unknown;
}

async function post(path: string, json: Record<string, unknown>): Promise<VdlResult | null> {
  try {
    return await readJson<VdlResult>(apiClient.post(path, { json }));
  } catch {
    return null;
  }
}

/** Cancel one download by its video_downloads id. */
export function cancelVideoDownload(id: number): Promise<VdlResult | null> {
  return post('video/downloads/cancel', { id });
}

/** Stop running wishlist searches (search-all, the hourly drain) so they don't keep grabbing. */
export function stopVideoWishlistSearches(): Promise<VdlResult | null> {
  return post('video/wishlist/search-stop', {});
}

/**
 * Retry a download. `next: true` retries with a DIFFERENT release (the
 * block-and-retry path); without it the same release is re-grabbed.
 */
export function retryVideoDownload(id: number, next = false): Promise<VdlResult | null> {
  return post('video/downloads/retry', next ? { id, next: true } : { id });
}

/** Clear finished downloads. */
export function clearVideoDownloads(): Promise<VdlResult | null> {
  return post('video/downloads/clear', {});
}

/** Clear only failed and cancelled downloads -- completed rows stay. */
export function clearFailedVideoDownloads(): Promise<VdlResult | null> {
  return post('video/downloads/clear', { scope: 'failed' });
}

/** Take one failed or cancelled download off the list, no retry. */
export function removeVideoDownload(id: number): Promise<VdlResult | null> {
  return post('video/downloads/remove', { id });
}

/** Block one exact release (slskd rows only — YouTube has no release identity). */
export function blockVideoRelease(downloadId: number): Promise<VdlResult | null> {
  return post('video/downloads/blocklist', { download_id: downloadId });
}

/** Block one exact release from a history row. */
export function blockVideoHistoryRelease(historyId: number | string): Promise<VdlResult | null> {
  return post('video/downloads/blocklist', { history_id: Number(historyId) });
}

/** Block a whole uploader from a history row. */
export function blockVideoUploader(historyId: number | string): Promise<VdlResult | null> {
  return post('video/downloads/blocklist', { history_id: Number(historyId), scope: 'source' });
}

/** Remove one entry from the blocklist. */
export async function unblockVideoRelease(id: number | string): Promise<VdlResult | null> {
  try {
    return await readJson<VdlResult>(
      apiClient.delete(`video/downloads/blocklist/${encodeURIComponent(String(id))}`),
    );
  } catch {
    return null;
  }
}

/** Clear the whole blocklist. */
export function clearVideoBlocklist(): Promise<VdlResult | null> {
  return post('video/downloads/blocklist/clear', {});
}

/** Restore one recycle-bin entry. */
export function restoreVideoRecycleEntry(
  trashDir: string | null | undefined,
  name: string,
): Promise<VdlResult | null> {
  return post('video/downloads/recycle/restore', { trash_dir: trashDir ?? null, name });
}

/** Restore everything in the recycle bin. */
export function restoreAllVideoRecycle(): Promise<VdlResult | null> {
  return post('video/downloads/recycle/restore', { all: true });
}

/** Purge one recycle-bin entry permanently. */
export function purgeVideoRecycleEntry(
  trashDir: string | null | undefined,
  name: string,
): Promise<VdlResult | null> {
  return post('video/downloads/recycle/purge', { trash_dir: trashDir ?? null, name });
}

/** Empty the whole recycle bin permanently. */
export function emptyVideoRecycle(): Promise<VdlResult | null> {
  return post('video/downloads/recycle/purge', { all: true });
}

/** Forget one history grab so the scans re-add + re-grab it. */
export async function deleteVideoHistory(id: number | string): Promise<VdlResult | null> {
  try {
    return await readJson<VdlResult>(
      apiClient.delete(`video/downloads/history/${encodeURIComponent(String(id))}`),
    );
  } catch {
    return null;
  }
}

/** Clear the whole download history. */
export function clearVideoHistory(): Promise<VdlResult | null> {
  return post('video/downloads/history/clear', {});
}
