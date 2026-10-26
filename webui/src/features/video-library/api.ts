/** the library tab's server calls (api/video/library_manage.py). */

import type { MatchCandidate, TitleKind, TitleLibrary } from './types';

async function send<T>(url: string, init?: RequestInit): Promise<T> {
  const response = await fetch(url, {
    ...init,
    headers: {
      Accept: 'application/json',
      ...(init?.body ? { 'Content-Type': 'application/json' } : {}),
    },
  });
  let data: (T & { error?: string; success?: boolean }) | null = null;
  try {
    data = (await response.json()) as T & { error?: string; success?: boolean };
  } catch {
    data = null;
  }
  if (!response.ok || !data || data.error || data.success === false) {
    throw new Error(data?.error || `Request failed (${response.status})`);
  }
  return data;
}

export function fetchTitleLibrary(
  kind: TitleKind,
  id: number,
  season?: number | null,
): Promise<TitleLibrary> {
  const q = season != null ? `?season=${encodeURIComponent(season)}` : '';
  return send<TitleLibrary>(`/api/video/detail/${kind}/${id}/library${q}`);
}

export interface RenamePlan {
  current: string;
  expected: string;
  conforms: boolean;
}

export function fetchRenamePlan(fileId: number): Promise<RenamePlan> {
  return send<RenamePlan>(`/api/video/files/${fileId}/rename-plan`);
}

export interface FileActionResult {
  success: boolean;
  action?: string;
  message?: string;
  recycled?: boolean;
  remaining?: number;
}

export function renameFile(fileId: number): Promise<FileActionResult> {
  return send<FileActionResult>(`/api/video/files/${fileId}/rename`, {
    method: 'POST',
    body: '{}',
  });
}

export function deleteFile(fileId: number): Promise<FileActionResult> {
  return send<FileActionResult>(`/api/video/files/${fileId}/delete`, {
    method: 'POST',
    body: '{}',
  });
}

export async function searchMatches(
  kind: TitleKind,
  id: number,
  query: string,
): Promise<MatchCandidate[]> {
  const res = await send<{ results: MatchCandidate[] }>(
    `/api/video/detail/${kind}/${id}/identify/search?q=${encodeURIComponent(query)}`,
  );
  return res.results ?? [];
}

export interface IdentifyResult {
  success: boolean;
  tmdb_id: number;
  title: string;
  year: number | null;
  renamed: boolean;
}

export function identifyTitle(
  kind: TitleKind,
  id: number,
  tmdbId: number,
): Promise<IdentifyResult> {
  return send<IdentifyResult>(`/api/video/detail/${kind}/${id}/identify`, {
    method: 'POST',
    body: JSON.stringify({ tmdb_id: tmdbId }),
  });
}

export function releaseIdentity(kind: TitleKind, id: number): Promise<{ success: boolean }> {
  return send<{ success: boolean }>(`/api/video/detail/${kind}/${id}/identify/release`, {
    method: 'POST',
    body: '{}',
  });
}

export function setSeasonMonitored(
  showId: number,
  season: number,
  monitored: boolean,
): Promise<{ success: boolean }> {
  return send<{ success: boolean }>(`/api/video/detail/show/${showId}/season/${season}/monitor`, {
    method: 'POST',
    body: JSON.stringify({ monitored }),
  });
}
