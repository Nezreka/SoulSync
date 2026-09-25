import { queryOptions, type QueryClient } from '@tanstack/react-query';

import { apiClient, readJson } from '@/app/api-client';

import type {
  MusicRequestBadgeCounts,
  MusicRequestCounts,
  MusicRequestList,
  MusicRequestListResponse,
} from './-requests.types';

export const REQUESTS_QUERY_KEY = ['music-requests'] as const;

type Ok = { success: boolean; error?: string };

function headersFor(profileId: number): Headers {
  const headers = new Headers();
  headers.set('X-Profile-Id', String(profileId || 1));
  return headers;
}

function assertOk(payload: Ok, fallback: string) {
  if (!payload.success) throw new Error(payload.error || fallback);
}

export function normalizeCounts(raw: Partial<MusicRequestCounts> | undefined): MusicRequestCounts {
  return {
    pending: Number(raw?.pending) || 0,
    approved: Number(raw?.approved) || 0,
    available: Number(raw?.available) || 0,
    declined: Number(raw?.declined) || 0,
    removed: Number(raw?.removed) || 0,
  };
}

export async function fetchMusicRequests(profileId: number): Promise<MusicRequestList> {
  const payload = await readJson<MusicRequestListResponse>(
    apiClient.get('requests/music', {
      headers: headersFor(profileId),
      searchParams: { status: 'all' },
    }),
  );
  assertOk(payload, 'Failed to load requests');
  return {
    pending: payload.pending ?? [],
    history: payload.history ?? [],
    counts: normalizeCounts(payload.counts),
    asksFirst: payload.asks_first === true,
  };
}

export async function fetchMusicRequestCounts(profileId: number): Promise<MusicRequestBadgeCounts> {
  const payload = await readJson<Ok & Partial<MusicRequestBadgeCounts>>(
    apiClient.get('requests/music/counts', { headers: headersFor(profileId) }),
  );
  assertOk(payload, 'Failed to load request counts');
  return { pending: Number(payload.pending) || 0, updates: Number(payload.updates) || 0 };
}

export async function approveMusicRequest(
  profileId: number,
  body: { profile_id: number; key: string; response?: string },
): Promise<void> {
  const payload = await readJson<Ok>(
    apiClient.post('requests/music/approve', { headers: headersFor(profileId), json: body }),
  );
  assertOk(payload, 'Could not approve that request');
}

export async function declineMusicRequest(
  profileId: number,
  body: { profile_id: number; key: string; response?: string },
): Promise<void> {
  const payload = await readJson<Ok>(
    apiClient.post('requests/music/decline', { headers: headersFor(profileId), json: body }),
  );
  assertOk(payload, 'Could not decline that request');
}

export async function withdrawMusicRequest(profileId: number, key: string): Promise<void> {
  const payload = await readJson<Ok>(
    apiClient.post('requests/music/withdraw', { headers: headersFor(profileId), json: { key } }),
  );
  assertOk(payload, 'Could not withdraw that request');
}

export async function deleteMusicRequest(profileId: number, requestId: number): Promise<void> {
  const payload = await readJson<Ok>(
    apiClient.delete(`requests/music/${requestId}`, { headers: headersFor(profileId) }),
  );
  assertOk(payload, 'Could not remove that request');
}

export async function markMusicRequestsSeen(profileId: number): Promise<void> {
  const payload = await readJson<Ok>(
    apiClient.post('requests/music/seen', { headers: headersFor(profileId) }),
  );
  assertOk(payload, 'Could not mark requests seen');
}

export function musicRequestsQueryOptions(profileId: number) {
  return queryOptions({
    queryKey: [...REQUESTS_QUERY_KEY, 'list', profileId],
    queryFn: () => fetchMusicRequests(profileId),
  });
}

export function invalidateMusicRequests(queryClient: QueryClient) {
  return queryClient.invalidateQueries({ queryKey: REQUESTS_QUERY_KEY });
}
