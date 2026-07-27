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
  retry_count?: number | string | null;
  last_attempted?: string | null;
  failure_reason?: string | null;
}

export interface WishlistTracksResponse {
  success?: boolean;
  tracks?: WishlistTrackRow[];
  /** artist name -> library photo, used to seed the orb art map. */
  artist_images?: Record<string, string>;
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
  image: string;
  type: 'album' | 'single';
  id: string;
  retry: number;
  failing: boolean;
  lastTried: string;
  failReason: string;
}

export interface WishlistAlbumGroup {
  name: string;
  image: string;
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
