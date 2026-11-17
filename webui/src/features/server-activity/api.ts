/**
 * The server activity routes (scrap/CONTRACT.md). Every one is admin only
 * (#2155) except the artwork proxy. Errors come back as values, not throws,
 * so the views can say what went wrong in words.
 */

import type {
  ActivityPayload,
  ControlResult,
  HistoryGroupDetail,
  HistoryPayload,
  ServerActivitySettings,
  StatsPayload,
} from '@/shell/server-activity-types';

import { appURL } from '@/platform/url-base';

const BASE = '/api/server-activity';

export type Result<T> = { ok: true; data: T } | { ok: false; error: string; status: number };

async function request<T>(path: string, init?: RequestInit): Promise<Result<T>> {
  let response: Response;
  try {
    response = await fetch(appURL(path), {
      ...init,
      headers: {
        Accept: 'application/json',
        ...(init?.body ? { 'Content-Type': 'application/json' } : {}),
      },
    });
  } catch (error) {
    if ((error as Error)?.name === 'AbortError') throw error;
    return { ok: false, error: "Couldn't reach SoulSync.", status: 0 };
  }
  let body: unknown = null;
  try {
    body = await response.json();
  } catch {
    body = null;
  }
  const payload = (body ?? {}) as { error?: string; ok?: boolean };
  if (!response.ok) {
    const fallback =
      response.status === 403 ? 'Server Activity is for admins.' : 'Something went wrong.';
    return { ok: false, error: payload.error || fallback, status: response.status };
  }
  return { ok: true, data: body as T };
}

const post = (body: unknown): RequestInit => ({ method: 'POST', body: JSON.stringify(body) });

export function imageUrl(path: string | null | undefined): string {
  return path ? appURL(`${BASE}/image?path=${encodeURIComponent(path)}`) : '';
}

/** avatars come as ready-made urls; only the url base is ours to add */
export function avatarUrl(url: string | null | undefined): string {
  return url ? appURL(url) : '';
}

export function getActivity(signal?: AbortSignal) {
  return request<ActivityPayload>(BASE, { signal });
}

export interface HistoryQuery {
  kind?: string | null;
  user?: string | null;
  method?: string | null;
  server?: string | null;
  days?: number | null;
  q?: string | null;
  page?: number;
  per_page?: number;
  tz?: string;
}

export function historyParams(query: HistoryQuery): string {
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(query)) {
    if (value === null || value === undefined || value === '') continue;
    params.set(key, String(value));
  }
  const qs = params.toString();
  return qs ? `?${qs}` : '';
}

export function getHistory(query: HistoryQuery, signal?: AbortSignal) {
  return request<HistoryPayload>(`${BASE}/history${historyParams(query)}`, { signal });
}

export function getHistoryGroup(groupKey: string, signal?: AbortSignal) {
  return request<HistoryGroupDetail>(`${BASE}/history/${encodeURIComponent(groupKey)}`, {
    signal,
  });
}

export function getStats(days: number, tz: string | undefined, signal?: AbortSignal) {
  return request<StatsPayload>(`${BASE}/stats${historyParams({ days, tz })}`, { signal });
}

export function stopStream(sessionKey: string, message: string) {
  return request<ControlResult>(`${BASE}/stop`, post({ session_key: sessionKey, message }));
}

export function sendMessage(sessionKey: string, message: string) {
  return request<ControlResult>(`${BASE}/message`, post({ session_key: sessionKey, message }));
}

export function getSettings(signal?: AbortSignal) {
  return request<{ ok: boolean; settings: ServerActivitySettings }>(`${BASE}/settings`, {
    signal,
  });
}

export function saveSettings(patch: Partial<ServerActivitySettings>) {
  return request<{ ok: boolean; settings: ServerActivitySettings; error?: string }>(
    `${BASE}/settings`,
    post(patch),
  );
}

export function clearPlays() {
  return request<{ ok: boolean; removed: number }>(`${BASE}/plays/clear`, post({}));
}
