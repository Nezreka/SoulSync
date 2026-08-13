import { z } from 'zod';

/**
 * URL contract for /artist-detail/$source/$id.
 *
 * `source` is a PATH param, not a search param: 'library' means the id is the
 * local DB primary key, anything else ('spotify', 'deezer', ...) means the id
 * belongs to that metadata source and the backend synthesizes a response from
 * it. buildArtistDetailPath() normalizes an absent source to the literal
 * 'library', so the path always carries one.
 *
 * `name` travels in the query string because some sources (Bandcamp) have no
 * numeric-ID lookup at all. The preprocess is load-bearing: TanStack
 * JSON-parses search values, so an all-digits artist name ("311", "702")
 * arrives as a NUMBER and a bare z.string() throws SearchParamError, killing
 * the route. That bug has been fixed once already — keep the coercion.
 */
export const artistDetailSearchSchema = z.object({
  name: z
    .preprocess((v) => (v == null ? '' : String(v)), z.string())
    .optional()
    .default(''),
});

export type ArtistDetailSearch = z.infer<typeof artistDetailSearchSchema>;

/** 'library' addresses the local DB; anything else is a metadata source. */
export const LIBRARY_SOURCE = 'library';

export function normalizeSource(source: string): string | null {
  const value = source.trim().toLowerCase();
  return !value || value === LIBRARY_SOURCE ? null : value;
}

/**
 * One release in a discography bucket.
 *
 * `owned` is TRI-STATE and the distinction matters:
 *   true  — confirmed in the library
 *   false — confirmed absent
 *   null  — still being checked; the completion stream fills these in
 * A source-only artist has no library to check against, so the loader coerces
 * every null to false before render (there is nothing to stream).
 */
export interface DiscographyRelease {
  id?: string | number;
  /** The artist page uses `title`; the download modal's shape uses `name`.
   *  Both are declared because _classifyReleaseContent reads either. */
  title?: string;
  name?: string;
  album_type?: string;
  release_date?: string;
  year?: number | string | null;
  image_url?: string | null;
  track_count?: number;
  owned?: boolean | null;
  owned_track_count?: number;
  total_track_count?: number;
  [key: string]: unknown;
}

export type DiscographyBucket = 'albums' | 'eps' | 'singles';

export interface Discography {
  albums?: DiscographyRelease[];
  eps?: DiscographyRelease[];
  singles?: DiscographyRelease[];
  /** Which metadata source produced this list; drives album-track lookups. */
  source?: string | null;
  [key: string]: unknown;
}

export interface ArtistInfo {
  id?: string | number;
  name?: string;
  image_url?: string | null;
  /** Absent => source-only artist: no library record, no ownership, no Enhanced view. */
  server_source?: string | null;
  source?: string | null;
  musicbrainz_id?: string | null;
  spotify_artist_id?: string | null;
  /** Enhanced-view meta panel image; distinct from image_url. */
  thumb_url?: string | null;
  genres?: string[];
  /** Last.fm extras. `lastfm_tags` may arrive as a JSON STRING, not an array. */
  lastfm_bio?: string | null;
  lastfm_listeners?: number | null;
  lastfm_playcount?: number | null;
  lastfm_tags?: string | string[] | null;
  [key: string]: unknown;
}

/** Non-fatal: the page still renders, but the vanilla page toasted a warning. */
export interface ProviderError {
  state?: string;
  error?: string;
  source?: string;
  status_code?: number;
}

/**
 * Present only when the artist has a spotify_artist_id (web_server.py:9765).
 * It is the CANONICAL Spotify identity, and the watchlist is keyed on it —
 * an artist enriched from Deezer still gets watched under their Spotify id.
 */
export interface SpotifyArtistIdentity {
  spotify_artist_id?: string | null;
  spotify_artist_name?: string | null;
  artist_image?: string | null;
}

export interface ArtistDetailResponse {
  success?: boolean;
  error?: string;
  artist?: ArtistInfo;
  spotify_artist?: SpotifyArtistIdentity;
  discography?: Discography;
  enrichment_coverage?: Record<string, unknown>;
  provider_error?: ProviderError;
}

/**
 * A top-tracks row. The metadata-source pass returns full track objects (the
 * download action needs `artists`/`album`); the Last.fm pass returns little
 * more than a name and a playcount, which is why every field is optional.
 */
export interface ArtistDetailTrack {
  id?: string | number;
  name?: string;
  playcount?: number | string;
  artists?: { id?: string | number; name?: string }[];
  album?: { name?: string; album_type?: string; [key: string]: unknown };
  [key: string]: unknown;
}
