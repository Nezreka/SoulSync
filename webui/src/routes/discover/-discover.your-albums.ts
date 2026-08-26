/**
 * Your Albums — grid, paging and query logic.
 *
 * Transcribed from `loadYourAlbumsGrid` (1426), `_renderYourAlbumsGrid` (1454),
 * `_renderYourAlbumsPagination` (1483) and the page steppers (1498-1504), read
 * end to end before any of this was written.
 */

import { browserSafeImageUrl } from '@/platform/artwork-thumb';

import { YOUR_ALBUMS_PAGE_SIZE } from './-discover.types';

/** Placeholder when an album has no cover — line 1463. */
export const ALBUM_COVER_FALLBACK = '/static/placeholder-album.png';

/** Grid copy. Note it differs from the SECTION empty copy — this is the inner grid. */
export const YOUR_ALBUMS_EMPTY = 'No albums found';
export const YOUR_ALBUMS_ERROR = 'Failed to load albums';

/** Defaults the vanilla reads off its own controls when they are unset. */
export const YOUR_ALBUMS_DEFAULT_STATUS = 'all';
export const YOUR_ALBUMS_DEFAULT_SORT = 'artist_name';

export interface YourAlbumsQueryInput {
  page: number;
  search?: string;
  status?: string;
  sort?: string;
}

/**
 * Build the query the grid sends.
 *
 * `search` is set ONLY when non-empty (line 1435) — sending `search=` would
 * make the server filter on an empty string rather than skip filtering.
 * status/sort always ride along with their defaults.
 */
export function yourAlbumsQuery(input: YourAlbumsQueryInput): Record<string, string> {
  const params: Record<string, string> = {
    page: String(input.page),
    per_page: String(YOUR_ALBUMS_PAGE_SIZE),
    sort: input.sort || YOUR_ALBUMS_DEFAULT_SORT,
    status: input.status || YOUR_ALBUMS_DEFAULT_STATUS,
  };
  const search = (input.search || '').trim();
  if (search) params.search = search;
  return params;
}

/**
 * Ownership badge.
 *
 * Keyed off `in_library`, which is what the handler actually stamps on each
 * album (it compares spotify ids, then falls back to artist+album name). An
 * earlier version of the album type invented `owned`/`missing` fields that the
 * server never sends — every badge would have rendered "missing".
 */
export interface AlbumBadge {
  className: 'owned' | 'missing';
  icon: string;
}

export function albumBadge(album: { in_library?: boolean } | null | undefined): AlbumBadge {
  return album?.in_library
    ? { className: 'owned', icon: '✓' }
    : { className: 'missing', icon: '↓' };
}

/** Cover URL with the vanilla's fallback. */
export function albumCover(album: { image_url?: string } | null | undefined): string {
  return album?.image_url ? browserSafeImageUrl(album.image_url) : ALBUM_COVER_FALLBACK;
}

/** `${total} albums · ${owned} owned · ${missing} missing` — lines 1443-1444. */
export function yourAlbumsSubtitle(
  stats: { total?: number; owned?: number; missing?: number } | null | undefined,
): string | null {
  if (!stats) return null;
  return `${stats.total ?? 0} albums · ${stats.owned ?? 0} owned · ${stats.missing ?? 0} missing`;
}

export interface Pagination {
  /** The whole pager is hidden when everything fits on one page. */
  visible: boolean;
  totalPages: number;
  /** 1-based, inclusive — "start–end of total". */
  start: number;
  end: number;
  prevDisabled: boolean;
  nextDisabled: boolean;
  label: string;
}

/**
 * Pager state — lines 1483-1496.
 *
 * Hidden entirely when `total <= pageSize`: with one page there is nothing to
 * navigate, and an all-disabled pager is noise.
 */
export function yourAlbumsPagination(total: number, page: number): Pagination {
  const size = YOUR_ALBUMS_PAGE_SIZE;
  const totalPages = Math.ceil(total / size);
  const start = (page - 1) * size + 1;
  const end = Math.min(page * size, total);
  return {
    visible: total > size,
    totalPages,
    start,
    end,
    prevDisabled: page <= 1,
    nextDisabled: page >= totalPages,
    label: `${start}–${end} of ${total}`,
  };
}

/**
 * Page steppers — both bounds-checked, and both REFUSE rather than clamp.
 *
 * `_yourAlbumsPrevPage` only decrements when page > 1; `_yourAlbumsNextPage`
 * only increments while page < totalPages. Returning the unchanged page is how
 * the caller knows not to refetch.
 */
export function yourAlbumsPrevPage(page: number): number {
  return page > 1 ? page - 1 : page;
}

export function yourAlbumsNextPage(page: number, total: number): number {
  const totalPages = Math.ceil(total / YOUR_ALBUMS_PAGE_SIZE);
  return page < totalPages ? page + 1 : page;
}
