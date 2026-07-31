/**
 * Shapes the discover endpoints actually return.
 *
 * Traced from the handlers in web_server.py (and core/discovery/hero.py), not
 * inferred from the consuming JS — the vanilla reads these defensively with
 * long `||` chains, which hides which fields are genuinely optional and which
 * are always present. Where a field is optional here, the server really can
 * omit it.
 */

/** Every discover endpoint answers inside this envelope. */
export interface DiscoverEnvelope {
  success?: boolean;
  error?: string;
}

// ── Hero ──────────────────────────────────────────────────────────────────

export interface DiscoverHeroArtist {
  name: string;
  id?: string;
  image_url?: string;
  logo_url?: string;
  genres?: string[];
  followers?: number;
  popularity?: number;
  /** Present when the artist is already on the watchlist — drives the toggle. */
  in_watchlist?: boolean;
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
