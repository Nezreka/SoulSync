import { queryOptions } from '@tanstack/react-query';

import { apiClient, readJson } from '@/app/api-client';

import { LIBRARY_V2_QUERY_KEY } from './-library-v2.api';

/** #2046: reviewed and locked albums and artists. */

export type LockEntity = 'album' | 'artist';
export type LockScope = 'tags' | 'artwork' | 'metadata' | 'files' | 'match' | 'upgrades';

export interface LockSkip {
  scope: LockScope;
  what: string;
  first_at: string | null;
  last_at: string | null;
  times: number;
}

export interface LockState {
  entity_type: LockEntity;
  entity_id: number;
  locked: boolean;
  scopes: LockScope[];
  locked_at: string | null;
  locked_by_profile_id: number | null;
  /** a file of the album changed after it was reviewed */
  changed_since_review: boolean;
  /** the single-field locks that were there before the padlock */
  field_locks: { artwork: boolean; genres: boolean; edition: boolean };
  scope_options: { scope: LockScope; label: string }[];
  /** what automation was kept from doing here, newest first */
  skips: LockSkip[];
  can_lock: boolean;
  /** artists only: how many of their albums with files are reviewed */
  review?: { albums: number; reviewed: number };
}

export interface ArtistReview {
  albums: number;
  reviewed: number;
  reviewed_album_ids: number[];
  changed_album_ids: number[];
  next_album_id: number | null;
}

export interface LockedIds {
  albums: number[];
  artists: number[];
}

type Ok = { success: boolean; error?: string };

async function ok<T extends Ok>(request: ReturnType<typeof apiClient.get>, fallback: string) {
  const payload = await readJson<T>(request as never);
  if (!payload.success) throw new Error(payload.error || fallback);
  return payload;
}

const path = (entity: LockEntity, id: number) => `library/v2/${entity}s/${id}/lock`;

export const LOCK_QUERY_KEY = [...LIBRARY_V2_QUERY_KEY, 'review-lock'] as const;

export async function fetchLock(entity: LockEntity, id: number): Promise<LockState> {
  const payload = await ok<Ok & { lock: LockState }>(
    apiClient.get(path(entity, id)),
    'Could not read the lock',
  );
  return payload.lock;
}

export async function saveLock(
  entity: LockEntity,
  id: number,
  input: { scopes?: LockScope[]; review?: boolean; albums?: boolean } = {},
): Promise<LockState> {
  const payload = await ok<Ok & { lock: LockState }>(
    apiClient.put(path(entity, id), { json: input }),
    'Could not lock it',
  );
  return payload.lock;
}

export async function removeLock(entity: LockEntity, id: number): Promise<LockState> {
  const payload = await ok<Ok & { lock: LockState }>(
    apiClient.delete(path(entity, id)),
    'Could not unlock it',
  );
  return payload.lock;
}

export async function fetchLockedIds(): Promise<LockedIds> {
  const payload = await ok<Ok & LockedIds>(
    apiClient.get('library/v2/locks'),
    'Could not read locks',
  );
  return { albums: payload.albums ?? [], artists: payload.artists ?? [] };
}

export async function fetchArtistReview(artistId: number, after?: number): Promise<ArtistReview> {
  const query = after ? `?after=${after}` : '';
  return ok<Ok & ArtistReview>(
    apiClient.get(`library/v2/artists/${artistId}/review${query}`),
    'Could not read the review progress',
  );
}

export async function setLocksBulk(
  entity: LockEntity,
  ids: number[],
  locked: boolean,
): Promise<number> {
  const payload = await ok<Ok & { updated: number }>(
    apiClient.post('library/v2/locks', { json: { entity_type: entity, ids, locked } }),
    'Could not change the locks',
  );
  return payload.updated ?? 0;
}

export function lockQueryOptions(entity: LockEntity, id: number, enabled = true) {
  return queryOptions({
    queryKey: [...LOCK_QUERY_KEY, entity, id],
    queryFn: () => fetchLock(entity, id),
    enabled: enabled && id > 0,
    staleTime: 15_000,
  });
}

/** every locked id in one request: what the padlocks on a page of cards read */
export function lockedIdsQueryOptions() {
  return queryOptions({
    queryKey: [...LOCK_QUERY_KEY, 'ids'],
    queryFn: fetchLockedIds,
    staleTime: 30_000,
  });
}

export function artistReviewQueryOptions(artistId: number) {
  return queryOptions({
    queryKey: [...LOCK_QUERY_KEY, 'artist-review', artistId],
    queryFn: () => fetchArtistReview(artistId),
    enabled: artistId > 0,
    staleTime: 15_000,
  });
}

/** what each switch on the sheet promises, in plain words */
export const SCOPE_COPY: Record<LockScope, { album: string; artist: string }> = {
  tags: {
    album: 'Titles, artists, numbers, genres, lyrics and ReplayGain inside the files',
    artist: 'Artist names written into the files',
  },
  artwork: {
    album: 'The cover, in the library and in the files',
    artist: 'The artist photo',
  },
  metadata: {
    album: 'Title, year, genres, type and ids the library holds',
    artist: 'Name, genres, bio and ids the library holds',
  },
  files: {
    album: 'Renaming and moving the files',
    artist: 'The artist folder and what is written into it',
  },
  match: {
    album: 'Which release and edition it is matched to',
    artist: 'Which artist it is matched to on each source',
  },
  upgrades: {
    album: 'Replacing a file with a better copy',
    artist: '',
  },
};

/** the switches an artist's sheet shows: its files live on its albums */
export const ARTIST_SCOPES: LockScope[] = ['artwork', 'metadata', 'files', 'match'];
