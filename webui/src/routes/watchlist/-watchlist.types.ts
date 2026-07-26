import { z } from 'zod';

// ---------------------------------------------------------------------------
// Search params
// ---------------------------------------------------------------------------

export const WATCHLIST_TAB_VALUES = ['artists', 'labels'] as const;
export type WatchlistTab = (typeof WATCHLIST_TAB_VALUES)[number];

// Values match the vanilla <select id="watchlist-sort-select"> exactly, so a
// bookmarked sort keeps meaning the same thing either side of the migration.
//
// The vanilla page re-sorted the DOM in place with no URL state, so a reload
// always dropped you back on name-asc. Putting sort in the URL is the one
// behaviour change here, and it only ever adds state that was previously lost.
export const WATCHLIST_SORT_VALUES = [
  'name-asc',
  'name-desc',
  'scan-oldest',
  'scan-newest',
  'added-newest',
] as const;
export type WatchlistSort = (typeof WATCHLIST_SORT_VALUES)[number];

/**
 * Coerce a raw search value to a string.
 *
 * TanStack JSON-parses search values, so an all-digits filter ("311", "702")
 * arrives as a NUMBER and a bare `z.string()` would throw SearchParamError and
 * take the whole route down with it — the same trap the artist-detail route
 * documents.
 *
 * Only primitives are stringified: a hand-edited `?q[]=x` parses to an object,
 * and a bare `String(v)` would turn that into the literal filter text
 * "[object Object]" rather than treating it as absent.
 */
function searchString(value: unknown): string | undefined {
  if (typeof value === 'string') return value;
  if (typeof value === 'number' || typeof value === 'boolean') return String(value);
  return undefined;
}

export const watchlistSearchSchema = z.object({
  tab: z.enum(WATCHLIST_TAB_VALUES).default('artists').catch('artists'),
  sort: z.enum(WATCHLIST_SORT_VALUES).default('name-asc').catch('name-asc'),
  q: z
    .preprocess((v) => searchString(v) ?? '', z.string())
    .default('')
    .catch(''),
  // Which artist's config/detail modal is open, by primary source id. These are
  // provider ids (Spotify/iTunes/Deezer/...), never numeric row ids, so they
  // stay strings and are NOT coerced to number.
  configId: z.preprocess(searchString, z.string().optional()).catch(undefined),
  detailId: z.preprocess(searchString, z.string().optional()).catch(undefined),
  settings: z.boolean().default(false).catch(false),
});

export type WatchlistSearch = z.infer<typeof watchlistSearchSchema>;

// ---------------------------------------------------------------------------
// Artists
// ---------------------------------------------------------------------------

/** One row of `/api/watchlist/artists`. Mirrors the server payload exactly. */
export interface WatchlistArtist {
  id: number;
  artist_name: string;
  date_added: string | null;
  last_scan_timestamp: string | null;
  created_at: string | null;
  updated_at: string | null;
  /** Cached during watchlist scans; null until the artist has been scanned. */
  image_url: string | null;

  spotify_artist_id: string | null;
  itunes_artist_id: string | null;
  deezer_artist_id: string | null;
  discogs_artist_id: string | null;
  musicbrainz_artist_id: string | null;
  amazon_artist_id: string | null;

  include_albums: boolean;
  include_eps: boolean;
  include_singles: boolean;
  include_live: boolean;
  include_remixes: boolean;
  include_acoustic: boolean;
  include_compilations: boolean;
  // NOTE: no `include_instrumentals` here. The global config carries one and
  // the per-artist config endpoint accepts one, but the list payload does not
  // return it — do not add it to this type without adding it server-side.
}

export interface WatchlistArtistsResponse {
  success: boolean;
  artists?: WatchlistArtist[];
  error?: string;
}

export interface WatchlistCountResponse {
  success: boolean;
  count?: number;
  /** Seconds until the `scan_watchlist` system automation next fires; 0 = never. */
  next_run_in_seconds?: number;
  error?: string;
}

/** The provider columns a card can badge, in the order the vanilla page picked
 *  a primary id. Order is load-bearing: it decides `artistPrimaryId`. */
export const WATCHLIST_SOURCE_KEYS = [
  'spotify_artist_id',
  'itunes_artist_id',
  'deezer_artist_id',
  'discogs_artist_id',
  'musicbrainz_artist_id',
  'amazon_artist_id',
] as const;

export type WatchlistSourceKey = (typeof WATCHLIST_SOURCE_KEYS)[number];

// ---------------------------------------------------------------------------
// Scan
// ---------------------------------------------------------------------------

export const WATCHLIST_SCAN_STATUS_VALUES = [
  'idle',
  'scanning',
  'completed',
  'cancelled',
  'error',
] as const;
export type WatchlistScanStatus = (typeof WATCHLIST_SCAN_STATUS_VALUES)[number];

export interface WatchlistScanSummary {
  total_artists?: number;
  new_tracks_found?: number;
  tracks_added_to_wishlist?: number;
}

/** `/api/watchlist/scan/status` spreads the server's scan state into the
 *  response body rather than nesting it, so these live at the top level. */
export interface WatchlistScanStatusResponse {
  success: boolean;
  status?: string;
  started_at?: string | null;
  completed_at?: string | null;
  summary?: WatchlistScanSummary;
  error?: string | null;
  cancel_requested?: boolean;
}

// ---------------------------------------------------------------------------
// Global config
// ---------------------------------------------------------------------------

export interface WatchlistGlobalConfig {
  global_override_enabled: boolean;
  include_albums: boolean;
  include_eps: boolean;
  include_singles: boolean;
  include_live: boolean;
  include_remixes: boolean;
  include_acoustic: boolean;
  include_compilations: boolean;
  include_instrumentals: boolean;
  exclude_terms: string;
}

export interface WatchlistGlobalConfigResponse {
  success: boolean;
  config?: WatchlistGlobalConfig;
  error?: string;
}

// ---------------------------------------------------------------------------
// Labels
// ---------------------------------------------------------------------------

export interface WatchlistLabel {
  id: number;
  musicbrainz_label_id: string;
  discogs_label_id: string | null;
  label_name: string;
  source: string;
  backlog: boolean;
  date_added: string | null;
  last_scan_timestamp: string | null;
}

/** `/api/labels/watchlist` is the odd one out: it returns a bare `{labels}`
 *  with NO `success` key, and answers `{labels: []}` on its own failures. */
export interface WatchlistLabelsResponse {
  labels?: WatchlistLabel[];
}
