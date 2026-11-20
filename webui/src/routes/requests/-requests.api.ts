import { queryOptions, type QueryClient } from '@tanstack/react-query';

import { apiClient, readJson } from '@/app/api-client';

import type {
  AcquisitionMode,
  AcquisitionModes,
  AwaitingMedia,
  AwaitingMusicGroup,
  AwaitingQueue,
  AwaitingVideoTitle,
  MusicRequestBadgeCounts,
  MusicRequestCounts,
  MusicRequestList,
  MusicRequestListResponse,
  RequestQuota,
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

/** null when there is no limit (or the payload doesn't carry a usable one). */
export function normalizeQuota(raw: Partial<RequestQuota> | null | undefined): RequestQuota | null {
  if (!raw) return null;
  const limit = Number(raw.limit) || 0;
  if (limit <= 0) return null;
  const used = Math.max(0, Number(raw.used) || 0);
  const remaining =
    raw.remaining == null ? Math.max(0, limit - used) : Math.max(0, Number(raw.remaining) || 0);
  return { limit, days: Number(raw.days) || 7, used, remaining };
}

const ACQUISITION_KINDS = ['music', 'movie', 'tv', 'audiobook'] as const;

/** every kind automatic unless the payload says manual (#2199). */
export function normalizeModes(
  raw: Partial<AcquisitionModes> | null | undefined,
): AcquisitionModes {
  const out = {} as AcquisitionModes;
  for (const kind of ACQUISITION_KINDS) out[kind] = raw?.[kind] === 'manual' ? 'manual' : 'auto';
  return out;
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
    pendingVideos: payload.pending_videos ?? [],
    videoHistory: payload.video_history ?? [],
    pendingMedia: payload.pending_media ?? [],
    mediaHistory: payload.media_history ?? [],
    counts: normalizeCounts(payload.counts),
    asksFirst: payload.asks_first === true,
    quota: normalizeQuota(payload.quota),
    acquisition: normalizeModes(payload.acquisition),
    awaiting: Number(payload.awaiting) || 0,
  };
}

export async function fetchMusicRequestQuota(profileId: number): Promise<RequestQuota | null> {
  const payload = await readJson<Ok & { quota?: Partial<RequestQuota> | null }>(
    apiClient.get('requests/music/quota', { headers: headersFor(profileId) }),
  );
  assertOk(payload, 'Failed to load your request limit');
  return normalizeQuota(payload.quota);
}

/** admin: every waiting request in one go (or just one profile's). */
export async function approveAllMusicRequests(
  profileId: number,
  onlyProfileId?: number,
): Promise<number> {
  const payload = await readJson<Ok & { approved?: number }>(
    apiClient.post('requests/music/approve-all', {
      headers: headersFor(profileId),
      json: onlyProfileId == null ? {} : { profile_id: onlyProfileId },
    }),
  );
  assertOk(payload, 'Could not approve those requests');
  return Number(payload.approved) || 0;
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
  body: { profile_id: number; key: string; response?: string; acquisition?: AcquisitionMode },
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

/** remove a finished video request from history (own, or any as admin). */
export async function deleteMusicVideoRequest(profileId: number, requestId: number): Promise<void> {
  const payload = await readJson<Ok>(
    apiClient.delete(`requests/music/videos/${requestId}`, { headers: headersFor(profileId) }),
  );
  assertOk(payload, 'Could not remove that request');
}

export async function markMusicRequestsSeen(profileId: number): Promise<void> {
  const payload = await readJson<Ok>(
    apiClient.post('requests/music/seen', { headers: headersFor(profileId) }),
  );
  assertOk(payload, 'Could not mark requests seen');
}

export interface FileMusicVideoRequestBody {
  video_id: string;
  url: string;
  title: string;
  channel?: string;
  thumbnail_url?: string;
}

/**
 * A profile that asks first files a video request (never a download). The
 * server answers {success, id}, {success:false, already:true} for a duplicate
 * pending ask, or {success:false, in_library:true} when it's already saved.
 */
export async function fileMusicVideoRequest(
  profileId: number,
  body: FileMusicVideoRequestBody,
): Promise<{ id?: number; already?: boolean }> {
  const payload = await readJson<Ok & { id?: number; already?: boolean }>(
    apiClient.post('requests/music/videos', { headers: headersFor(profileId), json: body }),
  );
  assertOk(payload, 'Could not request that video');
  return { id: payload.id, already: payload.already };
}

/** admin: approving starts the download to the music library. */
export async function approveMusicVideoRequest(
  profileId: number,
  requestId: number,
): Promise<void> {
  const payload = await readJson<Ok>(
    apiClient.post(`requests/music/videos/${requestId}/approve`, {
      headers: headersFor(profileId),
    }),
  );
  assertOk(payload, 'Could not approve that request');
}

export async function declineMusicVideoRequest(
  profileId: number,
  requestId: number,
  response?: string,
): Promise<void> {
  const payload = await readJson<Ok>(
    apiClient.post(`requests/music/videos/${requestId}/decline`, {
      headers: headersFor(profileId),
      json: response ? { response } : {},
    }),
  );
  assertOk(payload, 'Could not decline that request');
}

/** a member takes back their own waiting video request. */
export async function withdrawMusicVideoRequest(
  profileId: number,
  requestId: number,
): Promise<void> {
  const payload = await readJson<Ok>(
    apiClient.post(`requests/music/videos/${requestId}/withdraw`, {
      headers: headersFor(profileId),
    }),
  );
  assertOk(payload, 'Could not withdraw that request');
}

/** an audiobook or episode ask (#2188): approving puts the book on their
 *  wishlist, or starts the episode. */
export async function approveMediaRequest(
  profileId: number,
  requestId: number,
  acquisition?: AcquisitionMode,
): Promise<void> {
  const payload = await readJson<Ok>(
    apiClient.post(`requests/media/${requestId}/approve`, {
      headers: headersFor(profileId),
      ...(acquisition ? { json: { acquisition } } : {}),
    }),
  );
  assertOk(payload, 'Could not approve that request');
}

export async function declineMediaRequest(
  profileId: number,
  requestId: number,
  response?: string,
): Promise<void> {
  const payload = await readJson<Ok>(
    apiClient.post(`requests/media/${requestId}/decline`, {
      headers: headersFor(profileId),
      json: response ? { response } : {},
    }),
  );
  assertOk(payload, 'Could not decline that request');
}

export async function withdrawMediaRequest(profileId: number, requestId: number): Promise<void> {
  const payload = await readJson<Ok>(
    apiClient.post(`requests/media/${requestId}/withdraw`, { headers: headersFor(profileId) }),
  );
  assertOk(payload, 'Could not withdraw that request');
}

export async function deleteMediaRequest(profileId: number, requestId: number): Promise<void> {
  const payload = await readJson<Ok>(
    apiClient.delete(`requests/media/${requestId}`, { headers: headersFor(profileId) }),
  );
  assertOk(payload, 'Could not remove that request');
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

// ── approved requests waiting for a release pick (#2199) ──────────────────

export const AWAITING_QUERY_KEY = [...REQUESTS_QUERY_KEY, 'awaiting'] as const;

export async function fetchAcquisitionModes(profileId: number): Promise<AcquisitionModes> {
  const payload = await readJson<Ok & { modes?: Partial<AcquisitionModes> }>(
    apiClient.get('requests/acquisition', { headers: headersFor(profileId) }),
  );
  assertOk(payload, 'Could not load the approval settings');
  return normalizeModes(payload.modes);
}

/** admin: save how approved requests download, for the kinds that changed. */
export async function saveAcquisitionModes(
  profileId: number,
  modes: Partial<AcquisitionModes>,
): Promise<AcquisitionModes> {
  const payload = await readJson<Ok & { modes?: Partial<AcquisitionModes> }>(
    apiClient.put('requests/acquisition', { headers: headersFor(profileId), json: { modes } }),
  );
  assertOk(payload, 'Could not save the approval settings');
  return normalizeModes(payload.modes);
}

/**
 * the pick queue: music and book asks from the music side, movies and TV
 * from the video side. a side that refuses (a moderator without video, a
 * side that's off) just adds nothing.
 */
export async function fetchAwaitingQueue(profileId: number): Promise<AwaitingQueue> {
  const [main, video] = await Promise.allSettled([
    readJson<Ok & { music?: AwaitingMusicGroup[]; media?: AwaitingMedia[] }>(
      apiClient.get('requests/awaiting', { headers: headersFor(profileId) }),
    ),
    readJson<Ok & { titles?: AwaitingVideoTitle[] }>(
      apiClient.get('video/requests/awaiting', { headers: headersFor(profileId) }),
    ),
  ]);
  const music = main.status === 'fulfilled' && main.value.success ? main.value : null;
  const titles = video.status === 'fulfilled' && video.value.success ? video.value.titles : null;
  return {
    music: Array.isArray(music?.music) ? music.music : [],
    media: Array.isArray(music?.media) ? music.media : [],
    video: Array.isArray(titles) ? titles : [],
  };
}

/** a held music ask goes to the automatic pass after all. */
export async function releaseMusicRequestToAuto(
  profileId: number,
  body: { profile_id: number; key: string },
): Promise<void> {
  const payload = await readJson<Ok>(
    apiClient.post('requests/music/awaiting/auto', { headers: headersFor(profileId), json: body }),
  );
  assertOk(payload, 'Could not hand that request to the automatic download');
}

/** a release was grabbed for a held book: the request moves on. */
export async function markMediaRequestPicked(profileId: number, requestId: number): Promise<void> {
  const payload = await readJson<Ok>(
    apiClient.post(`requests/media/${requestId}/picked`, { headers: headersFor(profileId) }),
  );
  assertOk(payload, 'The release is on its way, but the request didn’t update');
}

export async function releaseMediaRequestToAuto(
  profileId: number,
  requestId: number,
): Promise<void> {
  const payload = await readJson<Ok>(
    apiClient.post(`requests/media/${requestId}/auto`, { headers: headersFor(profileId) }),
  );
  assertOk(payload, 'Could not hand that request to the automatic download');
}

export async function releaseVideoRequestToAuto(
  profileId: number,
  requestId: number,
): Promise<void> {
  const payload = await readJson<Ok>(
    apiClient.post(`video/requests/${requestId}/auto`, { headers: headersFor(profileId) }),
  );
  assertOk(payload, 'Could not hand that request to the automatic download');
}
