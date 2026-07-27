import { queryOptions } from '@tanstack/react-query';

import { apiClient, readJson } from '@/app/api-client';

import {
  LIBRARY_PAGE_SIZE,
  type LibraryArtistsResponse,
  type LibrarySearch,
} from './-library.types';

export const LIBRARY_QUERY_KEY = ['library'] as const;

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

  return queryOptions({
    // Every filter is part of the key: changing any of them is a different
    // result set, and paging back should hit the cache rather than refetch.
    queryKey: [...LIBRARY_QUERY_KEY, 'artists', profileId, params] as const,
    queryFn: () =>
      readJson<LibraryArtistsResponse>(apiClient.get('library/artists', { searchParams: params })),
  });
}
