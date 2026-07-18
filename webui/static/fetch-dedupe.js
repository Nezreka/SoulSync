/**
 * API GET dedupe — one wire request per identical burst.
 *
 * A single page load fires the same GET many times from components that don't
 * know about each other (issues/counts ×3, activity/feed ×3, dashboard ×2,
 * …). This wraps window.fetch so identical same-origin /api GETs within a
 * short window share ONE request; every consumer gets its own Response.clone()
 * (a Response body is single-use — sharing the raw Response would break the
 * second .json() call).
 *
 * Loaded FIRST (before the React bundle and every split module) so all
 * frameworks flow through it. Deliberately conservative — anything outside
 * these rules passes straight through untouched:
 *   • GET only (every fetch-based stream in the app is POST, except…)
 *   • never for streaming paths (…/stream — the similar-artists SSE-over-GET)
 *     or socket.io transport
 *   • same-origin /api/* + /status only
 *   • failures are not cached (next caller retries for real)
 */
(function () {
    'use strict';
    if (typeof window === 'undefined' || !window.fetch) return;

    var TTL_MS = 2500;
    var raw = window.fetch.bind(window);
    var entries = new Map();   // key → { promise, t }

    function dedupeKey(input, init) {
        try {
            var method = ((init && init.method) ||
                (input && typeof input === 'object' && input.method) || 'GET');
            if (String(method).toUpperCase() !== 'GET') return null;
            var url = typeof input === 'string' ? input
                : (input && typeof input === 'object' ? input.url : '') || '';
            if (!url) return null;
            if (url[0] !== '/') {
                var u = new URL(url, window.location.origin);
                if (u.origin !== window.location.origin) return null;
                url = u.pathname + u.search;
            }
            var path = url.split('?')[0];
            if (path !== '/status' && path.indexOf('/api/') !== 0) return null;
            if (path.indexOf('/socket.io') === 0) return null;
            // streaming responses must never be shared or delayed
            if (path.indexOf('/stream') !== -1) return null;
            var accept = '';
            if (init && init.headers) {
                if (typeof init.headers.get === 'function') accept = init.headers.get('Accept') || '';
                else accept = init.headers.Accept || init.headers.accept || '';
            }
            if (String(accept).indexOf('text/event-stream') !== -1) return null;
            // an aborted shared request would abort every consumer — opt out
            if ((init && init.signal) || (input && typeof input === 'object' && input.signal)) return null;
            return url;
        } catch (e) {
            return null;
        }
    }

    function sweep(now) {
        entries.forEach(function (v, k) {
            if (now - v.t > TTL_MS) entries.delete(k);
        });
    }

    window.fetch = function (input, init) {
        var key = dedupeKey(input, init);
        if (!key) return raw(input, init);
        var now = Date.now();
        var hit = entries.get(key);
        if (hit && (now - hit.t) < TTL_MS) {
            return hit.promise.then(function (res) { return res.clone(); });
        }
        var p = raw(input, init).then(function (res) {
            if (!res.ok) entries.delete(key);   // failures always retry for real
            return res;
        }, function (err) {
            entries.delete(key);
            throw err;
        });
        entries.set(key, { promise: p, t: now });
        if (entries.size > 64) sweep(now);
        // The cached original is NEVER consumed — every caller (first one
        // included) reads a clone, so later hits can keep cloning it.
        return p.then(function (res) { return res.clone(); });
    };

    // introspection hook for tests/debugging
    window._apiGetDedupe = { entries: entries, dedupeKey: dedupeKey, ttl: TTL_MS };
})();
