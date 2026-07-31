/**
 * Shapes the discover endpoints actually return.
 *
 * ── VERIFICATION STATUS — read before building UI on these ─────────────────
 *
 * `DiscoverHeroArtist` is TRACED field-for-field against core/discovery/hero.py.
 *
 * The others are NOT yet traced to that standard. They were written from the
 * consuming JS, which reads everything through long `||` chains and therefore
 * hides which fields genuinely exist. That already produced one real error: the
 * hero interface invented `name`, `id`, `logo_url`, `followers` and
 * `in_watchlist`, and the `[key: string]: unknown` index signature meant tsc
 * never objected.
 *
 * So: before building the UI for a section, trace ITS response shape against
 * the handler and correct the interface here first. Several handlers return
 * `jsonify(result)` rather than a literal, so the shape lives in the function
 * that builds `result` — follow it rather than guessing.
 */

/** Every discover endpoint answers inside this envelope. */
export interface DiscoverEnvelope {
  success?: boolean;
  error?: string;
}

// ── Hero ──────────────────────────────────────────────────────────────────

/**
 * A hero artist, field-for-field as core/discovery/hero.py emits it.
 *
 * TRACED, not inferred. An earlier version of this interface invented `name`,
 * `id`, `logo_url`, `followers` and `in_watchlist` — none of which the server
 * sends. The index signature at the bottom meant tsc never complained, so the
 * mistake was invisible until the field names were checked against the handler.
 *
 * The two construction sites differ, which is why so much is optional:
 *   • the watchlist FALLBACK branch adds `is_watchlist: true` and only
 *     `image_url`
 *   • the normal branch adds `musicbrainz_artist_id`, and adds `genres` /
 *     `popularity` ONLY when a cached image exists
 */
export interface DiscoverHeroArtist {
  /** The id for the ACTIVE source — already resolved server-side. */
  artist_id: string | null;
  artist_name: string;
  /** Per-source ids, present regardless of which one `artist_id` came from. */
  spotify_artist_id?: string | null;
  itunes_artist_id?: string | null;
  musicbrainz_artist_id?: string | null;
  occurrence_count?: number;
  similarity_rank?: number;
  source?: string;
  /** Only on the watchlist-fallback branch. */
  is_watchlist?: boolean;
  /** Conditional: present only when a cached image was found. */
  image_url?: string;
  genres?: string[];
  popularity?: number;
  [key: string]: unknown;
}

export interface DiscoverHeroResponse extends DiscoverEnvelope {
  artists: DiscoverHeroArtist[];
  /** Which metadata source produced these (spotify | deezer | musicbrainz). */
  source?: string;
  /**
   * Set to 'watchlist' when the active source returned nothing and the server
   * filled from the watchlist instead. The UI explains the difference, so this
   * must survive the port.
   */
  fallback?: string;
}

// ── Albums / artists / tracks ─────────────────────────────────────────────

export interface DiscoverAlbum {
  name?: string;
  album_name?: string;
  artist?: string;
  artist_name?: string;
  album_cover_url?: string;
  image_url?: string;
  release_date?: string;
  year?: number;
  album_type?: string;
  total_tracks?: number;
  /** Ownership, when the endpoint joins against the library. */
  owned?: boolean;
  missing?: boolean;
  [key: string]: unknown;
}

export interface DiscoverArtist {
  name: string;
  id?: string;
  image_url?: string;
  genres?: string[];
  /** Artists from YOUR library that caused this recommendation to surface. */
  because?: string[];
  /** How many of your artists point at this one — the zero-`because` fallback. */
  occurrence_count?: number;
  /** Why it surfaced: genre | obscure | consensus | explore. */
  why?: string;
  in_watchlist?: boolean;
  [key: string]: unknown;
}

/**
 * A track row.
 *
 * Two shapes reach the UI: enriched rows carry a nested Spotify-shaped
 * `track_data_json`, while decade/Spotify rows carry the fields at the top
 * level. `normalizeTrack` in -discover.helpers.ts flattens both.
 */
export interface DiscoverTrack {
  track_data_json?: Record<string, unknown> | null;
  track_name?: string;
  artist_name?: string;
  album_name?: string;
  album_cover_url?: string;
  duration_ms?: number;
  spotify_track_id?: string;
  name?: string;
  [key: string]: unknown;
}

// ── Your Albums (the one paginated shelf) ─────────────────────────────────

export interface YourAlbumsResponse extends DiscoverEnvelope {
  albums?: DiscoverAlbum[];
  page?: number;
  per_page?: number;
  /** Totals for the header counts. */
  owned?: number;
  missing?: number;
  /**
   * The cache is mid-rebuild. The grid still renders what it has AND shows a
   * refreshing state — they are not mutually exclusive.
   */
  stale?: boolean;
  stats?: Record<string, unknown>;
}

/** your-albums/sources and your-artists/sources share this shape. */
export interface SourcesResponse extends DiscoverEnvelope {
  /** Sources the user has switched on. */
  enabled?: string[];
  /** Sources that are actually authenticated and reachable right now. */
  connected?: string[];
}

// ── Seasonal ──────────────────────────────────────────────────────────────

export interface SeasonalResponse extends DiscoverEnvelope {
  name?: string;
  description?: string;
  icon?: string;
  season?: string;
  albums?: DiscoverAlbum[];
  /** False when the season has albums but no generated playlist to sync. */
  playlist_available?: boolean;
}

// ── Page-level constants ──────────────────────────────────────────────────

/**
 * The your-albums grid page size.
 *
 * 48 in the vanilla, and it is load-bearing rather than cosmetic: the grid is
 * 6-up at the widest breakpoint, so 48 fills exactly 8 rows and the "load
 * more" boundary never lands mid-row.
 */
export const YOUR_ALBUMS_PAGE_SIZE = 48;

/** The shuffle shelf asks for a fixed 50; the vanilla hard-coded it inline. */
export const DISCOVERY_SHUFFLE_LIMIT = 50;
