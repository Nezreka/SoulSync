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
  [key: string]: unknown;
}

/** Non-fatal: the page still renders, but the vanilla page toasted a warning. */
export interface ProviderError {
  state?: string;
  error?: string;
  source?: string;
  status_code?: number;
}

export interface ArtistDetailResponse {
  success?: boolean;
  error?: string;
  artist?: ArtistInfo;
  discography?: Discography;
  enrichment_coverage?: Record<string, unknown>;
  provider_error?: ProviderError;
}
