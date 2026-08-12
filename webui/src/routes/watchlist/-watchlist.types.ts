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
// Per-artist config
// ---------------------------------------------------------------------------

/** The providers an artist can be matched against, in the order the linking
 *  panel lists them. Amazon appears here but NOT in the metadata-source picker,
 *  matching the vanilla modal. */
export const WATCHLIST_PROVIDERS = [
  'spotify',
  'itunes',
  'deezer',
  'discogs',
  'musicbrainz',
  'amazon',
] as const;

export type WatchlistProvider = (typeof WATCHLIST_PROVIDERS)[number];

/** Sources selectable as a per-artist metadata override. No Amazon. */
export const WATCHLIST_METADATA_SOURCES = [
  'spotify',
  'deezer',
  'itunes',
  'discogs',
  'musicbrainz',
] as const;

export type WatchlistMetadataSource = (typeof WATCHLIST_METADATA_SOURCES)[number];

export interface WatchlistArtistConfig {
  include_albums: boolean;
  include_eps: boolean;
  include_singles: boolean;
  include_live: boolean;
  include_remixes: boolean;
  include_acoustic: boolean;
  include_compilations: boolean;
  include_instrumentals: boolean;
  last_scan_timestamp?: string | null;
  date_added?: string | null;
  /** null = use the global window. */
  lookback_days: number | null;
  /** null = follow the global metadata source. */
  preferred_metadata_source: WatchlistMetadataSource | null;
  /** Absent on older rows, where it means true. */
  auto_download: boolean;
  /** null = "Use default" rather than the first profile in the list. */
  quality_profile_id: number | null;
}

export interface WatchlistArtistInfo {
  id: string;
  name: string;
  image_url: string | null;
  followers: number;
  popularity: number;
  genres: string[];
  banner_url?: string | null;
  summary?: string | null;
  style?: string | null;
  mood?: string | null;
  label?: string | null;
}

export interface WatchlistRecentRelease {
  album_name: string;
  release_date: string | null;
  album_cover_url: string | null;
  track_count: number | null;
}

export interface QualityProfileSummary {
  id: number;
  name?: string;
  is_default?: boolean;
}

export interface WatchlistArtistConfigResponse {
  success: boolean;
  error?: string;
  config?: WatchlistArtistConfig;
  artist?: WatchlistArtistInfo;
  recent_releases?: WatchlistRecentRelease[];
  spotify_artist_id?: string | null;
  itunes_artist_id?: string | null;
  deezer_artist_id?: string | null;
  discogs_artist_id?: string | null;
  amazon_artist_id?: string | null;
  musicbrainz_artist_id?: string | null;
  /** The name as stored on the watchlist row, which can differ from the
   *  provider's current name — the linking panel compares against this. */
  watchlist_name?: string | null;
  global_metadata_source?: string;
  /** Providers that can actually serve right now (server-computed). The
   *  discography link must not pin one that's missing from this list — the
   *  artist page treats a pinned source as exclusive and 503s when it can't
   *  serve. Optional so an older backend simply keeps every source eligible. */
  available_sources?: string[];
  quality_profiles?: QualityProfileSummary[];
}

/** One hit from /api/library/search-service. */
export interface ProviderSearchResult {
  id: string;
  name: string;
  image?: string | null;
  extra?: string | null;
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
