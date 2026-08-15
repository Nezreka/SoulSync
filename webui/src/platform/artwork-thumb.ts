/**
 * Ask the server for a right-sized copy of a cached image.
 *
 * SoulSync caches remote artwork and serves it from `/api/image-cache/<key>`.
 * Adding `?v=grid` asks for a resized copy instead of the full-size original —
 * the thing a 5,567-album library actually needs, because a wall of 200px tiles
 * was being filled with 1400px CDN masters (#1141).
 *
 * Callers do NOT need to know whether thumbnails are switched on. The server
 * ignores the parameter when the setting is off and serves the original, so a
 * call site can always ask and the single Settings toggle stays authoritative.
 * That also means no config has to be plumbed into the browser.
 */

export type ThumbVariant = 'grid' | 'card' | 'hero';

/** Only our own cache endpoint understands `?v=`. */
const CACHE_PREFIX = '/api/image-cache/';

export function thumb<T extends string | null | undefined>(
  url: T,
  variant: ThumbVariant,
): T | string {
  if (!url) return url;
  if (!url.startsWith(CACHE_PREFIX)) return url;
  // Already carries a query (a variant, or anything else) — leave it alone
  // rather than producing a second `?`.
  if (url.includes('?')) return url;
  return `${url}?v=${variant}`;
}
