import { z } from 'zod';

/**
 * Coerce a raw search value to a string.
 *
 * TanStack JSON-parses search values, so an all-digits filter arrives as a
 * NUMBER and a bare `z.string()` would throw SearchParamError and take the
 * route down. Only primitives are stringified — a hand-edited `?q[]=x` parses
 * to an object, which must read as absent rather than "[object Object]".
 */
function searchString(value: unknown): string | undefined {
  if (typeof value === 'string') return value;
  if (typeof value === 'number' || typeof value === 'boolean') return String(value);
  return undefined;
}

export const wishlistSearchSchema = z.object({
  // The vanilla filter and the Failing chip were transient DOM state, lost on
  // reload. Putting them in the URL only ever adds state that was thrown away.
  q: z
    .preprocess((v) => searchString(v) ?? '', z.string())
    .default('')
    .catch(''),
  failing: z.boolean().default(false).catch(false),
});

export type WishlistSearch = z.infer<typeof wishlistSearchSchema>;

/**
 * A track is "failing" once it has burned this many wishlist cycles without
 * landing (#liveleak-failing-hub). retry_count / last_attempted /
 * failure_reason were always in the API; the page just never showed them.
 */
export const WL_FAILING_ATTEMPTS = 3;

/** One row from /api/wishlist/tracks. `spotify_data` may arrive as a JSON string. */
export interface WishlistTrackRow {
  id?: string | number;
  spotify_track_id?: string | null;
  spotify_data?: unknown;
  /** Library-v2 provenance. May arrive as a JSON string, like `spotify_data`. */
  source_info?: unknown;
  retry_count?: number | string | null;
  last_attempted?: string | null;
  failure_reason?: string | null;
}

export interface WishlistTracksResponse {
  success?: boolean;
  tracks?: WishlistTrackRow[];
  /** artist name -> local Library-v2 artwork URL, used to seed the orb art map. */
  artist_images?: Record<string, string>;
  /**
   * artist name -> provider CDN photo. Painted while the local build for that
   * artist is still cold, the same role `remote_image_url` plays on the
   * Library v2 pages.
   */
  artist_images_fallback?: Record<string, string>;
  /** Rows stored for this profile, before any sanitizing/deduping. */
  stored_rows?: number;
  /** stored_rows minus what `tracks` contains — 0 when nothing was dropped. */
  hidden_rows?: number;
  duplicates_found?: number;
  error?: string;
}

export interface WishlistStatsResponse {
  singles?: number;
  albums?: number;
  total?: number;
  next_run_in_seconds?: number;
  is_auto_processing?: boolean;
}

export interface WishlistCycleResponse {
  cycle?: string;
}

/** A wishlist track after `spotify_data` has been unpacked. */
export interface ParsedWishlistTrack {
  track: string;
  artist: string;
  album: string;
  /** Primary cover — the local Library-v2 artwork endpoint when we have one. */
  image: string;
  /**
   * The CDN cover to paint if `image` fails to load. A cold artwork endpoint
   * 404s while the server builds the cover in the background, and an `<img>`
   * cannot read the `X-Artwork-Pending` header that says so.
   */
  imageFallback: string;
  type: 'album' | 'single';
  id: string;
  retry: number;
  failing: boolean;
  lastTried: string;
  failReason: string;
  /**
   * This row is a QUALITY UPGRADE of a track the library already has, not a
   * missing track. A production wishlist was 343 of 611 rows of these after a
   * profile change, which read as "my wishlist exploded with duplicates" —
   * they are neither missing nor duplicates, and the list has to say so.
   */
  upgrade: boolean;
  /** The quality of the file already on disk, for an upgrade row. */
  currentQuality: string;
}

export interface WishlistAlbumGroup {
  name: string;
  image: string;
  /** CDN cover to paint if `image` (the local artwork endpoint) 404s cold. */
  imageFallback: string;
  tracks: ParsedWishlistTrack[];
}

export interface WishlistArtistGroup {
  name: string;
  albums: WishlistAlbumGroup[];
  singles: ParsedWishlistTrack[];
  /** Album tracks + singles. Drives orb size and the meta line. */
  total: number;
  /** Tracks at or past the failing threshold; drives the warning dot + filter. */
  failingCount: number;
}
