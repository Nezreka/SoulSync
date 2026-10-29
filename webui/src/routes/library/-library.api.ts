import { keepPreviousData, queryOptions } from '@tanstack/react-query';

import { apiClient, readJson } from '@/app/api-client';

import type { LibraryAlbumTrack } from './-library.helpers';

import {
  LIBRARY_PAGE_SIZE,
  type LibraryAlbumsResponse,
  type LibraryArtistsResponse,
  type LibrarySearch,
  type LibrarySong,
  type LibrarySongsResponse,
  type UnmatchedSummary,
} from './-library.types';

export const LIBRARY_QUERY_KEY = ['library'] as const;

interface SuccessResponse {
  success?: boolean;
  error?: string;
}

/**
 * Add / remove straight from a card badge.
 *
 * Same two endpoints the vanilla card handler used; `artistId` is the
 * source-matched id (see watchlistArtistId), which is what the watchlist is
 * keyed on — not the library's own row id.
 */
export async function setArtistWatchlisted(
  artistId: string,
  artistName: string,
  watch: boolean,
): Promise<void> {
  const payload = await readJson<SuccessResponse>(
    watch
      ? apiClient.post('watchlist/add', { json: { artist_id: artistId, artist_name: artistName } })
      : apiClient.post('watchlist/remove', { json: { artist_id: artistId } }),
  );
  if (!payload.success) throw new Error(payload.error || 'Watchlist update failed');
}

/**
 * The artist grid. ONE endpoint backs the whole list page.
 *
 * `source_filter` is omitted when empty, matching the vanilla loader — it only
 * `set()` the param when a source was chosen, and the backend defaults it to
 * '' anyway, so sending an empty one would be noise in the query key too.
 */
export function libraryArtistsQueryOptions(profileId: number, search: LibrarySearch) {
  const params: Record<string, string | number> = {
    search: search.q,
    letter: search.letter,
    page: search.page,
    limit: LIBRARY_PAGE_SIZE,
    watchlist: search.watchlist,
  };
  if (search.source) params.source_filter = search.source;
  if (search.quality) params.quality = search.quality;
  // '' is the default order and stays in the key so the default view and an
  // explicitly sorted view never share a cache entry.
  params.sort = search.sort;
  // The default sort isn't sent on the wire: the backend treats a missing
  // sort the same as ''.
  const requestParams = { ...params };
  if (!requestParams.sort) delete requestParams.sort;

  return queryOptions({
    // Every filter is part of the key: changing any of them is a different
    // result set, and paging back should hit the cache rather than refetch.
    queryKey: [...LIBRARY_QUERY_KEY, 'artists', profileId, params] as const,
    queryFn: () =>
      readJson<LibraryArtistsResponse>(
        apiClient.get('library/artists', { searchParams: requestParams }),
      ),
  });
}

/**
 * The album grid, the library's other view.
 *
 * The watchlist filter is not sent: it belongs to an artist, and the page
 * hides it in this view. The source filter IS — an album has provider ids of
 * its own, on its own columns.
 */
export function libraryAlbumsQueryOptions(profileId: number, search: LibrarySearch) {
  const params: Record<string, string | number> = {
    search: search.q,
    letter: search.letter,
    page: search.page,
    limit: LIBRARY_PAGE_SIZE,
  };
  if (search.source) params.source_filter = search.source;
  // '' is the default order and stays in the key so the default view and an
  // explicitly sorted view never share a cache entry.
  params.sort = search.sort;
  // The default sort isn't sent on the wire: the backend treats a missing
  // sort the same as ''.
  const requestParams = { ...params };
  if (!requestParams.sort) delete requestParams.sort;

  return queryOptions({
    queryKey: [...LIBRARY_QUERY_KEY, 'albums', profileId, params] as const,
    queryFn: () =>
      readJson<LibraryAlbumsResponse>(
        apiClient.get('library/albums', { searchParams: requestParams }),
      ),
  });
}

/**
 * The owned tracks of one album, for the card's play button.
 *
 * Not a query: it is fetched on click, for one album, and caching a tracklist
 * the page never displays would only hold memory.
 */
export async function loadAlbumTracks(albumId: string | number): Promise<LibraryAlbumTrack[]> {
  const payload = await readJson<{
    success?: boolean;
    error?: string;
    tracks?: LibraryAlbumTrack[];
  }>(apiClient.get(`library/albums/${albumId}/tracks`));
  if (!payload.success) throw new Error(payload.error || 'Could not load the album');
  return payload.tracks ?? [];
}

/**
 * How many tracks landed under "Unknown Artist" because their tags were
 * unreadable on import (#1202).
 *
 * Its own query rather than a field on the artists response: the grid refetches
 * on every letter, search and page change, and this number does not move with
 * any of them. Failure is not worth surfacing — a missing banner is strictly
 * better than an error where a banner would go — so the caller treats a
 * rejected query as "nothing to report".
 */
export function libraryUnmatchedQueryOptions() {
  return queryOptions({
    queryKey: [...LIBRARY_QUERY_KEY, 'unmatched'] as const,
    queryFn: () => readJson<UnmatchedSummary>(apiClient.get('library/unmatched-summary')),
    staleTime: 60_000,
  });
}

/** The songs endpoint's params for a view of the library (#1906). */
function songsParams(search: LibrarySearch, limit: number, page = search.page) {
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
 * The Songs view (#1906), and the strip of song matches above an artist or
 * album search (which asks for a few, ignoring that view's letter and page).
 * The previous page stays on screen while the next one loads, so typing a
 * search never blanks the list.
 */
export function librarySongsQueryOptions(
  profileId: number,
  search: LibrarySearch,
  limit: number = LIBRARY_PAGE_SIZE,
) {
  const params = songsParams(search, limit);
  return queryOptions({
    queryKey: [...LIBRARY_QUERY_KEY, 'songs', profileId, params] as const,
    queryFn: () =>
      readJson<LibrarySongsResponse>(apiClient.get('library/songs', { searchParams: params })),
    placeholderData: keepPreviousData,
  });
}

/** Most songs Play all / Shuffle queue at once. */
export const PLAY_ALL_LIMIT = 500;

/** The first PLAY_ALL_LIMIT songs of what the view shows, in its order. */
export async function fetchSongsToPlay(search: LibrarySearch): Promise<LibrarySong[]> {
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
