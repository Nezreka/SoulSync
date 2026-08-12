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
  /** The ownership meter: how many of their albums are in the library. */
  owned_album_count?: number;
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

/**
 * An album row.
 *
 * `in_library` is the ownership flag and it is TRACED: the your-albums handler
 * stamps it on every row (matching by spotify id, then by artist+album name)
 * and the status filter reads it. An earlier version of this interface invented
 * `owned`/`missing` booleans that the server never sends — every badge would
 * have rendered "missing".
 *
 * The remaining fields vary by which shelf produced the row, so they stay
 * optional; trace a specific shelf's handler before relying on one.
 */
export interface DiscoverAlbum {
  album_name?: string;
  artist_name?: string;
  /** Set by the your-albums handler. Drives the owned/missing badge. */
  in_library?: boolean;
  image_url?: string;
  album_cover_url?: string;
  spotify_album_id?: string;
  release_date?: string;
  year?: number;
  album_type?: string;
  total_tracks?: number;
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

export interface YourAlbumsStats {
  total: number;
  owned: number;
  missing: number;
}

/**
 * TRACED against the your-albums handler.
 *
 * The counts are NESTED under `stats`, not top-level. An earlier version of
 * this interface put `owned` and `missing` at the top level and omitted
 * `total` entirely — reading `data.owned` would have been undefined forever,
 * and the index-signature-free shape here is what stops that recurring.
 *
 * Note `total` and `stats.total` differ on purpose: `total` is the count AFTER
 * the status filter, `stats.total` is the unfiltered library total. The header
 * shows one and the pager uses the other.
 */
export interface YourAlbumsResponse extends DiscoverEnvelope {
  albums?: DiscoverAlbum[];
  /** Filtered total — what the current status filter matched. */
  total?: number;
  page?: number;
  per_page?: number;
  /**
   * The cache is mid-rebuild. The grid still renders what it has AND shows a
   * refreshing state — they are not mutually exclusive.
   */
  stale?: boolean;
  /** Unfiltered library counts, for the header. */
  stats?: YourAlbumsStats;
}

/**
 * your-albums/sources and your-artists/sources.
 *
 * TRACED: `{success, enabled, connected}`. `enabled` is the user's configured
 * source list (a comma-separated config value, split server-side); `connected`
 * is which of those are actually authenticated RIGHT NOW. A source can be
 * enabled but not connected, which is exactly the state the sources modal
 * exists to show.
 */
export interface SourcesResponse extends DiscoverEnvelope {
  enabled?: string[];
  connected?: string[];
}

// ── Seasonal ──────────────────────────────────────────────────────────────

/**
 * TRACED against the seasonal/current handler.
 *
 * Two distinct shapes come back and the difference matters:
 *   • NO current season -> {success, season: null, albums: [], playlist_available: false}
 *     and NOTHING else — no name, description or icon at all
 *   • a season -> all fields present
 *
 * So `season === null` is the real "there is no season" signal, not an empty
 * albums array. The vanilla's loader bails on that branch before ever showing
 * the section, which is why seasonal can be absent rather than empty.
 */
export interface SeasonalResponse extends DiscoverEnvelope {
  /** null when there is no current season — the branch that renders nothing. */
  season?: string | null;
  name?: string;
  description?: string;
  icon?: string;
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
