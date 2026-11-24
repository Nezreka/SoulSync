/**
 * Player offline downloads API helpers & browser file download trigger.
 * Logs every download to `/api/player/downloads` so admins can audit usage
 * across all profiles and devices, and supports deletion sync.
 */

import { queryOptions } from '@tanstack/react-query';

import { apiClient } from '@/app/api-client';
import { appURL } from '@/platform/url-base';

export type DownloadItemType =
  | 'movie'
  | 'episode'
  | 'youtube'
  | 'podcast'
  | 'audiobook'
  | 'track'
  | 'album';

export interface PlayerDownloadRow {
  id: number;
  profile_id: number;
  profile_name?: string;
  device_id: string;
  device_name: string;
  item_type: DownloadItemType;
  item_id: string;
  title: string;
  subtitle?: string | null;
  artwork?: string | null;
  quality: string;
  size_bytes: number;
  status: 'downloaded' | 'removed';
  downloaded_at: string;
  removed_at?: string | null;
}

export interface MyDownloadsResponse {
  success: boolean;
  allow_downloads: boolean;
  max_items: number;
  is_admin: boolean;
  downloads: PlayerDownloadRow[];
}

export interface AdminUserSummary {
  id: number;
  name: string;
  is_admin: boolean;
  avatar_color?: string | null;
  allow_downloads: boolean;
  max_items: number;
  active_downloads: number;
  removed_downloads: number;
  active_bytes: number;
  active_devices: number;
  last_download_at?: string | null;
}

export interface AdminUsersOverview {
  success: boolean;
  users: AdminUserSummary[];
  downloads: PlayerDownloadRow[];
  totals: {
    active_downloads: number;
    removed_downloads: number;
    active_bytes: number;
  };
}

export function myDownloadsQuery(includeRemoved = false) {
  return queryOptions({
    queryKey: ['player', 'downloads', includeRemoved],
    queryFn: () =>
      apiClient
        .get('player/downloads', {
          searchParams: includeRemoved ? { include_removed: '1' } : {},
        })
        .json<MyDownloadsResponse>(),
    staleTime: 30_000,
  });
}

export function adminUsersQuery(filters?: {
  profileId?: number | null;
  itemType?: string | null;
  status?: string | null;
}) {
  return queryOptions({
    queryKey: ['player', 'admin', 'users', filters ?? {}],
    queryFn: () =>
      apiClient
        .get('player/admin/users', {
          searchParams: {
            ...(filters?.profileId ? { profile_id: String(filters.profileId) } : {}),
            ...(filters?.itemType ? { item_type: filters.itemType } : {}),
            ...(filters?.status ? { status: filters.status } : {}),
          },
        })
        .json<AdminUsersOverview>(),
    staleTime: 15_000,
  });
}

export async function setAdminUserDownloadPerm(
  profileId: number,
  allowDownloads: boolean,
  maxItems = 0,
) {
  return apiClient
    .put(`player/admin/users/${profileId}/downloads`, {
      json: { allow_downloads: allowDownloads, max_items: maxItems },
    })
    .json<{ success: boolean; allow_downloads: boolean; max_items: number }>();
}

export async function markDownloadRemoved(id: number) {
  return apiClient
    .post('player/downloads/removed', { json: { id } })
    .json<{ success: boolean; updated: number }>();
}

function browserDeviceName(): string {
  if (typeof navigator === 'undefined') return 'Web Browser';
  const ua = navigator.userAgent;
  if (ua.includes('Edg/')) return 'Microsoft Edge (Desktop)';
  if (ua.includes('Chrome/')) return 'Chrome (Desktop)';
  if (ua.includes('Firefox/')) return 'Firefox (Desktop)';
  if (ua.includes('Safari/')) return 'Safari (Desktop)';
  return 'Web Browser';
}

export async function triggerBrowserDownload(opts: {
  itemType: DownloadItemType;
  itemId: string;
  title: string;
  subtitle?: string | null;
  artwork?: string | null;
  directUrl?: string;
}): Promise<{ ok: boolean; error?: string }> {
  // a podcast episode has no player stream: only its own enclosure url can be saved
  if (opts.itemType === 'podcast' && !opts.directUrl)
    return { ok: false, error: "This episode can't be downloaded from here." };
  try {
    const res = await apiClient.post('player/downloads', {
      json: {
        item_type: opts.itemType,
        item_id: opts.itemId,
        title: opts.title,
        subtitle: opts.subtitle ?? null,
        artwork: opts.artwork ?? null,
        device_id: 'web',
        device_name: browserDeviceName(),
        quality: 'original',
        size_bytes: 0,
      },
      throwHttpErrors: false,
    });
    if (!res.ok) {
      const body = (await res.json().catch(() => ({}))) as { message?: string };
      return { ok: false, error: body.message || 'Downloads are disabled for your account.' };
    }
    const streamHref =
      opts.directUrl ||
      appURL(`/api/player/stream/${opts.itemType}/${encodeURIComponent(opts.itemId)}`);
    const a = document.createElement('a');
    a.href = streamHref;
    a.download = opts.title.replace(/[^\w\s.-]+/g, '').trim() || `${opts.itemType}-${opts.itemId}`;
    document.body.appendChild(a);
    a.click();
    a.remove();
    return { ok: true };
  } catch (e) {
    return { ok: false, error: e instanceof Error ? e.message : 'Download failed' };
  }
}

export function formatBytes(bytes: number): string {
  if (!bytes || bytes <= 0) return '—';
  const units = ['B', 'KB', 'MB', 'GB', 'TB'];
  let n = bytes;
  let i = 0;
  while (n >= 1024 && i < units.length - 1) {
    n /= 1024;
    i++;
  }
  return `${n >= 100 || i === 0 ? Math.round(n) : n.toFixed(1)} ${units[i]}`;
}
