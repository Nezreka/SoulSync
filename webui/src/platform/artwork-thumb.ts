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
const IMAGE_PROXY_PREFIX = '/api/image-proxy';

const COVER_ART_HOST_RE = /^(?:https?:)?\/\/coverartarchive\.org\//i;
const ARCHIVE_IMAGE_HOST_RE = /^(?:https?:)?\/\/(?:[^/]+\.)?archive\.org\//i;

export function serviceWorkerControlsPage(): boolean {
  return typeof navigator !== 'undefined' && Boolean(navigator.serviceWorker?.controller);
}

export function imageProxyUrl(url: string): string {
  return `${IMAGE_PROXY_PREFIX}?url=${encodeURIComponent(url)}`;
}

export function isCoverArtArchiveUrl(url: string): boolean {
  return COVER_ART_HOST_RE.test(url) || ARCHIVE_IMAGE_HOST_RE.test(url);
}

export function browserSafeImageUrl<T extends string | null | undefined>(
  url: T,
  serviceWorkerControlled = serviceWorkerControlsPage(),
): T | string {
  if (!url) return url;
  if (url.startsWith('/') || url.startsWith(IMAGE_PROXY_PREFIX)) return url;
  if (serviceWorkerControlled || !isCoverArtArchiveUrl(url)) return url;
  return imageProxyUrl(url);
}

export function thumb<T extends string | null | undefined>(
  url: T,
  variant: ThumbVariant,
  serviceWorkerControlled = serviceWorkerControlsPage(),
): T | string {
  const safeUrl = browserSafeImageUrl(url, serviceWorkerControlled);
  if (!safeUrl) return safeUrl;
  if (!safeUrl.startsWith(CACHE_PREFIX)) return safeUrl;
  // Already carries a query (a variant, or anything else) — leave it alone
  // rather than producing a second `?`.
  if (safeUrl.includes('?')) return safeUrl;
  return `${safeUrl}?v=${variant}`;
}
