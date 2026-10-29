import { keepPreviousData, queryOptions } from '@tanstack/react-query';

import { apiClient, readJson } from '@/app/api-client';

import {
  SONGS_PAGE_SIZE,
  type LibrarySong,
  type LibrarySongsResponse,
  type SongsSearch,
} from './-library-songs.types';

export const LIBRARY_SONGS_QUERY_KEY = ['library', 'songs'] as const;

/** The songs endpoint's params for a view of the library (#1906). */
function songsParams(search: SongsSearch, limit: number, page = search.page) {
  const params: Record<string, string | number> = {
    search: search.q,
    letter: search.letter,
    page,
    limit,
  };
  const sort = { recent: 'recent', artist: 'artist', album: 'album', year_desc: 'year' }[
    search.sort as 'recent' | 'artist' | 'album' | 'year_desc'
  ];
  if (sort) params.sort = sort;
  if (search.dupes) params.dupes = '1';
  if (search.quality) params.quality = search.quality;
  return params;
}

/**
 * The Songs tab (#1906), and the strip of song matches above an artist or
 * album search (which asks for a few, ignoring that view's letter and page).
 * The previous page stays on screen while the next one loads, so typing a
 * search never blanks the list.
 */
export function librarySongsQueryOptions(
  profileId: number,
  search: SongsSearch,
  limit: number = SONGS_PAGE_SIZE,
) {
  const params = songsParams(search, limit);
  return queryOptions({
    queryKey: [...LIBRARY_SONGS_QUERY_KEY, profileId, params] as const,
    queryFn: () =>
      readJson<LibrarySongsResponse>(apiClient.get('library/songs', { searchParams: params })),
    placeholderData: keepPreviousData,
  });
}

/** Most songs Play all / Shuffle queue at once. */
export const PLAY_ALL_LIMIT = 500;

/** The first PLAY_ALL_LIMIT songs of what the view shows, in its order. */
export async function fetchSongsToPlay(search: SongsSearch): Promise<LibrarySong[]> {
  const payload = await readJson<LibrarySongsResponse>(
    apiClient.get('library/songs', { searchParams: songsParams(search, PLAY_ALL_LIMIT, 1) }),
  );
  if (payload.success === false) throw new Error(payload.error || 'Failed to load songs');
  return payload.songs ?? [];
}

/** Every copy of one song, best file first (the "2 copies" comparison). */
export async function fetchSongCopies(trackId: string | number): Promise<LibrarySong[]> {
  const payload = await readJson<{ success?: boolean; error?: string; copies?: LibrarySong[] }>(
    apiClient.get(`library/songs/${encodeURIComponent(String(trackId))}/copies`),
  );
  if (payload.success === false) throw new Error(payload.error || "Couldn't load the copies");
  return payload.copies ?? [];
}
