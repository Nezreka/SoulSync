/*
 * SoulSync — headless video acquisition helper (VideoGrab).
 *
 * Powers the TV detail page's inline per-episode / per-season buttons. Runs the
 * SAME backend contract the download modal uses — search/start → poll →
 * pick the best accepted release → /downloads/grab — but with NO results UI:
 * it auto-grabs the best hit and resolves, leaving the caller to render its own
 * row status.
 *
 * The modal keeps its own interactive, results-streaming grab; both hit the same
 * endpoints. Kept separate for now so wiring the detail page can't regress the
 * working modal — a later cleanup could fold the modal onto this core.
 *
 *   VideoGrab.episode({ title, source, season, episode, mediaId, mediaSource, year, poster })
 *       → Promise<{ ok:boolean, id?:string, error?:string }>
 *   VideoGrab.season({ title, source, season, episodes:[num...], mediaId, mediaSource, year, poster }, onEp)
 *       // onEp(episodeNumber, 'searching' | 'grabbing' | 'none') fired per episode
 *       → Promise<{ grabbed:number, total:number }>
 *   VideoGrab.wishlistEpisodes(show, episodes) → Promise<boolean>
 *   VideoGrab.pickSource() → Promise<string>
 */
(function () {
    'use strict';

    function postJSON(url, body) {
        return fetch(url, { method: 'POST', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(body) }).then(function (r) { return r.ok ? r.json() : null; })
            .catch(function () { return null; });
    }
    function getJSON(url) {
        return fetch(url).then(function (r) { return r.ok ? r.json() : null; }).catch(function () { return null; });
    }

    // First configured download source — mirrors the modal's sourcesFromConfig.
    var _srcCache;
    function pickSource() {
        if (_srcCache !== undefined) return Promise.resolve(_srcCache);
        return getJSON('/api/video/downloads/config').then(function (c) {
            c = c || {};
            var list;
            if (c.download_mode === 'hybrid' && Array.isArray(c.hybrid_order) && c.hybrid_order.length) list = c.hybrid_order;
            else if (c.download_mode) list = [c.download_mode];
            else list = ['soulseek'];
            _srcCache = list[0] || 'soulseek';
            return _srcCache;
        });
    }

    // search/start → poll (until the results plateau) → return the accepted rows.
    // Mirrors the modal's _pollSearch heuristic, minus the DOM streaming.
    function runSearch(params) {
        return postJSON('/api/video/downloads/search/start', params).then(function (d) {
            if (!d || d.error) return [];
            if (!d.id) return d.results || [];   // immediate / mock
            return pollSearch(d.id, params, d.poll_ms);
        });
    }
    function pollSearch(id, params, pollMs) {
        return new Promise(function (resolve) {
            var started = Date.now(), lastN = -1, stable = 0;
            var MAX_MS = Math.min(80000, pollMs || 60000);
            function tick() {
                var qs = '?id=' + encodeURIComponent(id) + '&scope=' + encodeURIComponent(params.scope || 'episode') +
                    '&title=' + encodeURIComponent(params.title || '') +
                    (params.season != null ? '&season=' + params.season : '') +
                    (params.episode != null ? '&episode=' + params.episode : '');
                getJSON('/api/video/downloads/search/poll' + qs).then(function (d) {
                    var rows = (d && d.results) || [];
                    if (rows.length === lastN) { stable++; } else { stable = 0; lastN = rows.length; }
                    var elapsed = Date.now() - started;
                    var done = elapsed >= MAX_MS || rows.length >= 25 || (rows.length > 0 && elapsed > 20000 && stable >= 6);
                    if (done) resolve(rows);
                    else setTimeout(tick, 1500);
                });
            }
            tick();
        });
    }

    function bestRow(rows) {
        for (var i = 0; i < rows.length; i++) {
            if (rows[i] && rows[i].accepted && rows[i].username) return rows[i];
        }
        return null;
    }

    // Auto-grab one episode: search → pick the best accepted release → grab it.
    function episode(opts) {
        opts = opts || {};
        var params = { scope: 'episode', title: opts.title, season: opts.season,
            episode: opts.episode, source: opts.source || 'soulseek' };
        return runSearch(params).then(function (rows) {
            var best = bestRow(rows);
            if (!best) return { ok: false, error: 'no release found' };
            // The other accepted hits become the auto-retry pool (same as the modal).
            var pool = rows.filter(function (x) { return x.accepted && x.username && x.filename !== best.filename; })
                .map(function (x) {
                    return { username: x.username, filename: x.filename, size_bytes: x.size_bytes,
                        quality_label: x.quality_label, title: x.title };
                });
            var payload = {
                kind: 'show', title: opts.title, release_title: best.title,
                source: 'soulseek', username: best.username, filename: best.filename,
                size_bytes: best.size_bytes, quality_label: best.quality_label,
                media_id: opts.mediaId, media_source: opts.mediaSource, year: opts.year, poster_url: opts.poster,
                candidates: pool,
                search_ctx: { scope: 'episode', title: opts.title, year: opts.year,
                    season: opts.season, episode: opts.episode }
            };
            return postJSON('/api/video/downloads/grab', payload).then(function (res) {
                if (res && res.ok) {
                    document.dispatchEvent(new CustomEvent('soulsync:video-download-started'));
                    return { ok: true, id: res.id };
                }
                return { ok: false, error: (res && res.error) || 'grab failed' };
            });
        });
    }

    // Auto-grab every listed (missing) episode in a season, 3 at a time.
    function season(opts, onEp) {
        opts = opts || {};
        var eps = (opts.episodes || []).slice().sort(function (a, b) { return a - b; });
        var total = eps.length;
        if (!total) return Promise.resolve({ grabbed: 0, total: 0 });
        var idx = 0, active = 0, grabbed = 0, MAX = 3;
        return new Promise(function (resolve) {
            function launch(en) {
                active++;
                if (onEp) onEp(en, 'searching');
                episode({ title: opts.title, source: opts.source, season: opts.season, episode: en,
                    mediaId: opts.mediaId, mediaSource: opts.mediaSource, year: opts.year, poster: opts.poster })
                    .then(function (r) {
                        active--;
                        if (r.ok) { grabbed++; if (onEp) onEp(en, 'grabbing'); }
                        else if (onEp) { onEp(en, 'none'); }
                        if (idx >= eps.length && active === 0) resolve({ grabbed: grabbed, total: total });
                        else pump();
                    });
            }
            function pump() { while (active < MAX && idx < eps.length) launch(eps[idx++]); }
            pump();
        });
    }

    // Wishlist a set of episodes (episode = 1 item, season = N items).
    function wishlistEpisodes(show, episodes) {
        if (!show || !show.tmdb_id || !episodes || !episodes.length) return Promise.resolve(false);
        return postJSON('/api/video/wishlist/add', { show: show, episodes: episodes }).then(function (d) {
            var ok = !!(d && d.success);
            if (ok) document.dispatchEvent(new CustomEvent('soulsync:video-wishlist-changed'));
            return ok;
        });
    }

    window.VideoGrab = { episode: episode, season: season,
        wishlistEpisodes: wishlistEpisodes, pickSource: pickSource };
})();
