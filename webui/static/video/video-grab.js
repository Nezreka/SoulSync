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

    // Every configured download source, in order — mirrors the modal's
    // sourcesFromConfig. pickSource() keeps returning just the first for callers
    // that genuinely want one, but a season should ask them all: a show Soulseek
    // has and Prowlarr doesn't was previously reported as unavailable without
    // Soulseek ever being asked.
    var _srcListCache;
    function allSources() {
        if (_srcListCache !== undefined) return Promise.resolve(_srcListCache);
        return getJSON('/api/video/downloads/config').then(function (c) {
            c = c || {};
            var list;
            if (c.download_mode === 'hybrid' && Array.isArray(c.hybrid_order) && c.hybrid_order.length) list = c.hybrid_order;
            else if (c.download_mode) list = [c.download_mode];
            else list = ['soulseek'];
            _srcListCache = list.length ? list : ['soulseek'];
            return _srcListCache;
        });
    }
    function pickSource() {
        return allSources().then(function (l) { return l[0] || 'soulseek'; });
    }

    // search/start → poll (until the results plateau) → return the accepted rows.
    // Mirrors the modal's _pollSearch heuristic, minus the DOM streaming.
    function runSearch(params) {
        return postJSON('/api/video/downloads/search/start', params).then(function (d) {
            if (!d || d.error) {
                // an errored search (Prowlarr past its timeout) is not "nothing
                // found": the season loop tries it again
                var none = [];
                none.error = (d && d.error) || 'search failed';
                return none;
            }
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
        var src = opts.source || 'soulseek';
        var params = { scope: 'episode', title: opts.title, season: opts.season,
            episode: opts.episode, source: src };
        return runSearch(params).then(function (rows) {
            var best = bestRow(rows);
            if (!best) return { ok: false, error: rows.error || 'no release found', errored: !!rows.error };
            var payload = {
                kind: 'show', title: opts.title, release_title: best.title,
                source: src, size_bytes: best.size_bytes, quality_label: best.quality_label,
                media_id: opts.mediaId, media_source: opts.mediaSource, year: opts.year, poster_url: opts.poster,
                search_ctx: { scope: 'episode', title: opts.title, year: opts.year,
                    season: opts.season, episode: opts.episode }
            };
            if (src === 'soulseek') {
                // The other accepted hits become the auto-retry pool (same as the modal).
                payload.username = best.username; payload.filename = best.filename;
                payload.candidates = rows.filter(function (x) { return x.accepted && x.username && x.filename !== best.filename; })
                    .map(function (x) { return { username: x.username, filename: x.filename, size_bytes: x.size_bytes,
                        quality_label: x.quality_label, title: x.title }; });
            } else {
                // torrent / usenet — the magnet/NZB carriers the backend hands to the client
                payload.download_url = best.download_url; payload.protocol = best.protocol;
            payload.magnet_uri = best.magnet_uri;   // #1139 fallback if the .torrent fetch fails
                payload.indexer_id = best.indexer_id; payload.guid = best.guid;
                payload.username = best.username; payload.filename = best.filename || best.title;
                payload.candidates = [];
            }
            return postJSON('/api/video/downloads/grab', payload).then(function (res) {
                if (res && res.ok) {
                    document.dispatchEvent(new CustomEvent('soulsync:video-download-started'));
                    return { ok: true, id: res.id };
                }
                return { ok: false, error: (res && res.error) || 'grab failed' };
            });
        });
    }

    // Auto-grab a season: every missing episode on its own, as the button says.
    // a whole-missing season used to try a season pack first, so all its rows
    // shared one pack's progress (SeadogsBooty). a pack is the season search's job
    function season(opts, onEp) {
        opts = opts || {};
        var eps = (opts.episodes || []).slice().sort(function (a, b) { return a - b; });
        var total = eps.length;
        if (!total) return Promise.resolve({ grabbed: 0, total: 0 });
        return allSources().then(function (sources) {
            if (opts.source && sources.indexOf(opts.source) === -1) sources = [opts.source].concat(sources);
            if (onEp) eps.forEach(function (en) { onEp(en, 'searching'); });
            return perEpisode(opts, eps, total, sources, onEp);
        });
    }

    // queue entries are [episode, tries]. an episode whose search errored goes to
    // the back for one more try and the loop drops to one search at a time: three
    // at once backs Prowlarr up past its timeout on a slow indexer (SeadogsBooty)
    function perEpisode(opts, eps, total, sources, onEp) {
        var queue = eps.map(function (en) { return [en, 0]; });
        var n = 0, active = 0, grabbed = 0, MAX = 3;
        return new Promise(function (resolve) {
            function launch(en, tries) {
                active++;
                if (onEp) onEp(en, 'searching');
                // Round-robin the sources so one dead indexer can't sink a season.
                episode({ title: opts.title, source: sources[n++ % sources.length],
                    season: opts.season, episode: en,
                    mediaId: opts.mediaId, mediaSource: opts.mediaSource, year: opts.year, poster: opts.poster })
                    .then(function (r) {
                        active--;
                        if (r.ok) { grabbed++; if (onEp) onEp(en, 'grabbing'); }
                        else if (r.errored && tries === 0) { MAX = 1; queue.push([en, 1]); }
                        else if (onEp) { onEp(en, 'none'); }
                        if (!queue.length && active === 0) resolve({ grabbed: grabbed, total: total });
                        else pump();
                    });
            }
            function pump() {
                while (active < MAX && queue.length) { var item = queue.shift(); launch(item[0], item[1]); }
            }
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
