import { z } from 'zod';

/**
 * Coerce a raw search value to a string.
 *
 * TanStack JSON-parses search values, so an all-digits query arrives as a
 * NUMBER and a bare `z.string()` would throw SearchParamError and take the
 * route down. Only primitives are stringified — a hand-edited `?q[]=x` parses
 * to an object, which must read as absent rather than "[object Object]".
 */
function searchString(value: unknown): string | undefined {
  if (typeof value === 'string') return value;
  if (typeof value === 'number' || typeof value === 'boolean') return String(value);
  return undefined;
}

function searchNumber(value: unknown, fallback: number): number {
  const n = typeof value === 'number' ? value : Number.parseInt(String(value ?? ''), 10);
  return Number.isFinite(n) && n >= 1 ? n : fallback;
}

/** The four watchlist filter buttons. */
export const WATCHLIST_FILTERS = ['all', 'watched', 'unwatched', 'ignored'] as const;

/**
 * Page size. Fixed at 75 in libraryPageState and never exposed in the UI, so
 * it is a constant here rather than URL state.
 */
export const LIBRARY_PAGE_SIZE = 75;

/**
 * All five filters live in the URL.
 *
 * They were module-scoped `libraryPageState` fields in the vanilla page, lost
 * on reload and unshareable. Putting them in the URL only adds state that used
 * to be thrown away — the same call made for the wishlist's q/failing.
 */
export const librarySearchSchema = z.object({
  /** Free-text search. `search` in the API, `q` in the URL for consistency. */
  q: z
    .preprocess((v) => searchString(v) ?? '', z.string())
    .default('')
    .catch(''),
  /**
   * Alphabet selector; 'all' means no letter filter.
   *
   * Lowercased because the buttons carry lowercase values and the active-letter
   * highlight compares against them — a hand-typed `?letter=A` would otherwise
   * filter correctly (the backend compares with UPPER on both sides) while
   * highlighting nothing.
   */
  letter: z
    .preprocess((v) => (searchString(v) ?? 'all').toLowerCase(), z.string())
    .default('all')
    .catch('all'),
  /** 1-based. Anything unparseable or < 1 falls back to page 1. */
  page: z
    .preprocess((v) => searchNumber(v, 1), z.number())
    .default(1)
    .catch(1),
  watchlist: z
    .preprocess((v) => searchString(v) ?? 'all', z.string())
    .default('all')
    .catch('all'),
  /** Metadata source filter; '' means all sources and is NOT sent to the API. */
  source: z
    .preprocess((v) => searchString(v) ?? '', z.string())
    .default('')
    .catch(''),
});

export type LibrarySearch = z.infer<typeof librarySearchSchema>;

/**
 * One row from /api/library/artists.
 *
 * The provider id fields each drive one badge on the card; they are all
 * optional because an artist is only enriched by the providers that matched.
 */
export interface LibraryArtist {
  id: string | number;
  name: string;
  image_url?: string | null;
  track_count?: number;
  is_watched?: boolean;
  /** Provider ids — each present one adds a badge, in this declaration order. */
  spotify_artist_id?: string | null;
  musicbrainz_id?: string | null;
  deezer_id?: string | number | null;
  audiodb_id?: string | number | null;
  itunes_artist_id?: string | number | null;
  lastfm_url?: string | null;
  genius_url?: string | null;
  tidal_id?: string | number | null;
  qobuz_id?: string | number | null;
  discogs_id?: string | number | null;
  amazon_id?: string | null;
  soul_id?: string | null;
}

export interface LibraryPagination {
  page: number;
  limit: number;
  total_count: number;
  total_pages: number;
  has_prev: boolean;
  has_next: boolean;
}

export interface LibraryArtistsResponse {
  success?: boolean;
  error?: string;
  artists?: LibraryArtist[];
  pagination?: LibraryPagination;
}

/** A resolved badge, ready to render. `url` null means it is not a link. */
export interface ArtistBadge {
  key: string;
  logo: string;
  /** Two/three-letter text shown if the logo image fails to load. */
  fallback: string;
  title: string;
  url: string | null;
}

/** The "these never got matched" banner's payload (#1202). */
export interface UnmatchedSummary {
  success?: boolean;
  /** Tracks currently filed under Unknown Artist; 0 means no banner. */
  count: number;
  /** Library artist row holding the most of them, for the link. */
  artist_id: string | number | null;
}
