/* SoulSync Service Worker — image cache + lightweight shell cache.
 *
 * Strategy:
 *
 *   - **Images** (cover art / artist photos from CDNs + the local
 *     /api/image-proxy endpoint): cache-first. Once an album cover is
 *     fetched, every future page load serves it instantly from
 *     CacheStorage with no network round-trip. Cover art is the
 *     heaviest asset on Library and Discover; this is the single
 *     biggest perceived-performance win.
 *
 *   - **Static assets** (/static/*.js, /static/*.css, /static/*.png):
 *     stale-while-revalidate. Serve from cache instantly, refresh in
 *     the background. Combined with the existing ?v=static_v cache
 *     bust, deploys still ship live — a new query string means a
 *     different cache entry, the old one ages out naturally.
 *
 *   - **Everything else** (HTML, /api/*, etc.): no caching. Pass
 *     through to the network. We deliberately do NOT cache HTML or
 *     API responses — both are user-specific or change frequently
 *     enough that staleness would hurt more than it helps.
 *
 * Cache versioning: bump CACHE_VERSION when changing strategies or
 * cache shapes. The activate handler clears any cache whose name
 * doesn't match the current version, so old entries don't accumulate.
 */

const CACHE_VERSION = 'v1';
const IMAGE_CACHE = `soulsync-images-${CACHE_VERSION}`;
const STATIC_CACHE = `soulsync-static-${CACHE_VERSION}`;
const VALID_CACHES = new Set([IMAGE_CACHE, STATIC_CACHE]);

// Image hosts we cache. Local /api/image-proxy is treated as an image
// (see _isImageRequest below) so the proxy endpoint piggybacks on the
// same strategy without needing to be listed here.
const IMAGE_HOSTS = [
    'i.scdn.co',           // Spotify
    'lastfm.freetls.fastly.net', 'lastfm-img2.akamaized.net',
    'mosaic.scdn.co',
    'is1-ssl.mzstatic.com', 'is2-ssl.mzstatic.com',
    'is3-ssl.mzstatic.com', 'is4-ssl.mzstatic.com',
    'is5-ssl.mzstatic.com',  // Apple
    'cdns-images.dzcdn.net', 'e-cdns-images.dzcdn.net',  // Deezer
    'i.discogs.com', 'st.discogs.com',  // Discogs
    'coverartarchive.org',  // MusicBrainz Cover Art Archive
    'i.ytimg.com',  // YouTube thumbnails
];

function _isImageRequest(request) {
    if (request.method !== 'GET') return false;
    const url = new URL(request.url);
    // Local image proxy
    if (url.pathname.startsWith('/api/image-proxy')) return true;
    // Known CDN hosts
    if (IMAGE_HOSTS.includes(url.hostname)) return true;
    // Last-resort: file extension hint (covers misc CDNs we missed)
    if (/\.(png|jpe?g|webp|gif|svg)(\?|$)/i.test(url.pathname)) {
        // Only if same-origin or known image host; refuse arbitrary
        // third-party domains so we don't accidentally cache trackers.
        if (url.origin === self.location.origin) return true;
    }
    return false;
}

function _isStaticAsset(request) {
    if (request.method !== 'GET') return false;
    const url = new URL(request.url);
    if (url.origin !== self.location.origin) return false;
    return url.pathname.startsWith('/static/');
}

self.addEventListener('install', (event) => {
    // Skip waiting so a freshly-installed SW takes control on the next
    // navigation instead of needing all tabs to close first. Combined
    // with clients.claim() in activate, deploys propagate quickly.
    self.skipWaiting();
});

self.addEventListener('activate', (event) => {
    // Wipe any caches whose name doesn't match the current version, then
    // claim all open clients so this SW starts handling their fetches
    // immediately (otherwise they'd keep using the previous SW until
    // navigation).
    event.waitUntil(
        caches.keys().then((names) => Promise.all(
            names.map((name) => VALID_CACHES.has(name) ? null : caches.delete(name))
        )).then(() => self.clients.claim())
    );
});

self.addEventListener('fetch', (event) => {
    const request = event.request;

    if (_isImageRequest(request)) {
        event.respondWith(_cacheFirst(request, IMAGE_CACHE));
        return;
    }

    if (_isStaticAsset(request)) {
        event.respondWith(_staleWhileRevalidate(request, STATIC_CACHE));
        return;
    }

    // HTML / API / everything else: pass through, no caching.
    // Do NOT call event.respondWith() — let the browser handle it
    // normally. This is intentional: HTML and API responses are
    // user-specific or change too often for SW caching to help.
});


// ── strategies ───────────────────────────────────────────────────────

async function _cacheFirst(request, cacheName) {
    try {
        const cache = await caches.open(cacheName);
        const hit = await cache.match(request);
        if (hit) return hit;

        const response = await fetch(request);
        // Only cache successful, opaque-OK responses. Don't cache 404s
        // / 500s — would pin a bad placeholder for the lifetime of the
        // cache version.
        if (response && (response.ok || response.type === 'opaque')) {
            // Clone before .put — body is consumed otherwise.
            cache.put(request, response.clone()).catch(() => { /* quota / disk full */ });
        }
        return response;
    } catch (err) {
        // Network failure with no cache hit — let the browser surface
        // its standard offline / error UI (returning Response.error()
        // is equivalent to letting the fetch reject naturally).
        return Response.error();
    }
}

async function _staleWhileRevalidate(request, cacheName) {
    try {
        const cache = await caches.open(cacheName);
        const hit = await cache.match(request);

        // Kick off a background refresh regardless of cache hit so the
        // next load picks up any deploy. Failure here is silent — we
        // already have a cached copy to serve (or are about to fetch).
        const networkPromise = fetch(request).then((response) => {
            if (response && response.ok) {
                cache.put(request, response.clone()).catch(() => {});
            }
            return response;
        }).catch(() => null);

        // Serve cached immediately if we have it; otherwise wait on the
        // network and fall back to Response.error() if THAT also failed.
        // Important: must await networkPromise here — returning the
        // Promise directly would let respondWith resolve to null when
        // the fetch rejects, which throws TypeError in the browser.
        if (hit) return hit;
        const networkResponse = await networkPromise;
        return networkResponse || Response.error();
    } catch (err) {
        return Response.error();
    }
}
