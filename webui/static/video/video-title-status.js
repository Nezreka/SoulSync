/*
 * SoulSync: where a movie or show stands (#2160), the one status every video
 * poster and detail page shows.
 *
 * the status comes from /api/video/title-status (core/video/title_status.py),
 * the same rule the seerr api answers with: In Library, Partial, Downloading,
 * Searching, Failed, Approved / Wishlisted / Watchlisted (on the way),
 * Requested (waiting for an admin). it is global: a title someone else asked
 * for says Requested for everyone.
 *
 *   hydrate(root)   one POST for every poster under root (search, discover,
 *                   person, studio, more like this), cached 20s, then paints
 *                   the corner ribbon (.vsr-ribbon) or chip (.vd-sim-wishchip)
 *                   and hides the hover Request button where asking would be a
 *                   duplicate. while anything on screen is downloading it
 *                   checks again every 15s, so the badge follows the download.
 *   fetchOne()      the detail page's block (who asked, seasons, progress).
 *   blockHtml()     that block's markup.
 *
 * VideoWatchlist.hydrate (the hook every card surface already calls) runs
 * hydrate. styled by .vts-* and the .vsr-ribbon variants in video-side.css.
 */
(function () {
    'use strict';

    var TTL = 20000;          // a poster's status is good for this long
    var LIVE_MS = 15000;      // re-check while something on screen is moving
    var cache = {};           // 'movie:603' -> {at, v} (v null: nothing to say)

    function esc(s) {
        return String(s == null ? '' : s).replace(/&/g, '&amp;').replace(/</g, '&lt;')
            .replace(/>/g, '&gt;').replace(/"/g, '&quot;');
    }

    // ── the words and the look, one table for posters and the detail page ──
    // variant names reuse the ribbons the cards already had (owned, wish, req)
    function badgeFor(s) {
        if (!s || !s.status) return null;
        switch (s.status) {
            case 'available': return { variant: 'owned', label: 'In Library' };
            case 'partial': return { variant: 'partial', label: 'Partial' };
            case 'downloading':
                return { variant: 'active', label: s.progress > 0 && s.progress < 100
                    ? 'Downloading ' + Math.round(s.progress) + '%' : 'Downloading' };
            case 'searching': return { variant: 'active', label: 'Searching' };
            case 'failed': return { variant: 'failed', label: 'Failed' };
            case 'pending': return { variant: 'req', label: 'Requested' };
            case 'processing':
                if (s.reason === 'approved') return { variant: 'wish', label: 'Approved' };
                if (s.reason === 'followed') return { variant: 'wish', label: 'Watchlisted' };
                if (s.reason === 'wished') return { variant: 'wish', label: 'Wishlisted' };
                return { variant: 'wish', label: 'On the way' };
        }
        return null;
    }
    function isLive(s) { return !!s && (s.status === 'downloading' || s.status === 'searching'); }
    // the hover Request button would only file a duplicate
    function blocksAsk(s) {
        return !!s && (s.request === 'mine' || s.request === 'on_the_way' ||
            s.request === 'available' || s.request === 'others');
    }

    // ── which titles are on screen ─────────────────────────────────────────
    // every poster card says what it is with data-vts-kind / data-vts-tmdb
    // (cardAttrs), owned or not: an owned show can still be Partial. the
    // detail page's "more like this" cards and the discover hero keep their own.
    function cardAttrs(kind, tmdbId, owned) {
        var n = parseInt(tmdbId, 10);
        if ((kind !== 'movie' && kind !== 'show') || !(n > 0)) return '';
        return ' data-vts-kind="' + kind + '" data-vts-tmdb="' + n + '"' + (owned ? ' data-vts-owned="1"' : '');
    }
    function cardTarget(el) {
        if (el.hasAttribute('data-vts-kind')) {
            return { kind: el.getAttribute('data-vts-kind'), id: el.getAttribute('data-vts-tmdb') };
        }
        if (el.classList.contains('vd-sim-card')) {
            return { kind: el.getAttribute('data-vd-sim'), id: el.getAttribute('data-vd-sim-id') };
        }
        return { kind: el.getAttribute('data-kind'), id: el.getAttribute('data-tmdb') };
    }
    var TARGETS = '[data-vts-kind], .vd-sim-card[data-vd-sim], [data-vdsc-hero-add]';
    function targets(root) {
        var out = [];
        var nodes = root.querySelectorAll(TARGETS);
        for (var i = 0; i < nodes.length; i++) {
            var t = cardTarget(nodes[i]);
            var n = parseInt(t.id, 10);
            if ((t.kind === 'movie' || t.kind === 'show') && n > 0) out.push({ el: nodes[i], key: t.kind + ':' + n });
        }
        return out;
    }

    // ── painting ───────────────────────────────────────────────────────────
    function paintRibbon(card, s) {
        var ribbon = card.querySelector('.vsr-ribbon:not(.vsr-ribbon--yt)');
        if (!ribbon) return;
        var b = badgeFor(s);
        if (!b) {
            var owned = card.hasAttribute('data-vts-owned');
            b = owned ? { variant: 'owned', label: 'In Library' } : { variant: 'preview', label: 'Preview' };
            ribbon.removeAttribute('data-vts');
        } else {
            ribbon.setAttribute('data-vts', s.status);
        }
        ribbon.className = 'vsr-ribbon vsr-ribbon--' + b.variant;
        ribbon.textContent = b.label;
    }
    function paintChip(card, s) {
        var b = badgeFor(s);
        if (!b && card.hasAttribute('data-vd-sim-owned')) b = { variant: 'owned', label: 'In Library' };
        var chip = card.querySelector('.vd-sim-wishchip');
        if (!b) { if (chip) chip.remove(); return; }
        if (!chip) {
            card.insertAdjacentHTML('afterbegin', '<span class="vd-sim-wishchip"></span>');
            chip = card.querySelector('.vd-sim-wishchip');
        }
        chip.className = 'vd-sim-wishchip vd-sim-wishchip--' + b.variant;
        chip.textContent = b.label;
    }
    function paintHero(btn, s) {
        var on = !!(s && s.wished);
        btn.classList.toggle('vdsc-hero-add--on', on);
        btn.innerHTML = on
            ? '<span aria-hidden="true">✓</span> In Wishlist'
            : '<span aria-hidden="true">＋</span> Wishlist';
        btn.title = on ? 'Already on your wishlist, open to manage' : 'Add to wishlist';
    }
    function paintAsk(card, s) {
        var btn = card.querySelector('[data-vreq-card]');
        if (!btn) return;
        var block = blocksAsk(s);
        if (block) btn.setAttribute('data-vts-block', '1'); else btn.removeAttribute('data-vts-block');
        btn.hidden = block;
    }
    function paint(root) {
        var live = false;
        targets(root).forEach(function (t) {
            var hit = cache[t.key];
            if (!hit) return;
            var s = hit.v;
            if (isLive(s)) live = true;
            if (t.el.hasAttribute('data-vdsc-hero-add')) { paintHero(t.el, s); return; }
            if (t.el.classList.contains('vd-sim-card')) paintChip(t.el, s);
            else paintRibbon(t.el, s);
            paintAsk(t.el, s);
        });
        return live;
    }

    // ── fetching ───────────────────────────────────────────────────────────
    var liveTimer = null;
    function hydrate(root) {
        root = root || document;
        var now = Date.now(), movies = {}, shows = {};
        targets(root).forEach(function (t) {
            var hit = cache[t.key];
            if (hit && now - hit.at < TTL) return;
            var parts = t.key.split(':');
            (parts[0] === 'movie' ? movies : shows)[parts[1]] = 1;
        });
        var want = { movies: Object.keys(movies).map(Number), shows: Object.keys(shows).map(Number) };
        if (!want.movies.length && !want.shows.length) {
            if (paint(root)) scheduleLive();
            return Promise.resolve();
        }
        return fetch('/api/video/title-status', {
            method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(want),
        }).then(function (r) { return r.ok ? r.json() : null; }).then(function (d) {
            if (!d || !d.success) return;
            var got = d.statuses || {}, at = Date.now();
            want.movies.forEach(function (id) { cache['movie:' + id] = { at: at, v: got['movie:' + id] || null }; });
            want.shows.forEach(function (id) { cache['show:' + id] = { at: at, v: got['show:' + id] || null }; });
            if (paint(root)) scheduleLive();
        }).catch(function () { /* a missed badge is non-critical */ });
    }
    // a download on screen: look again soon, only at what's moving
    function scheduleLive() {
        if (liveTimer) return;
        liveTimer = setTimeout(function () {
            liveTimer = null;
            if (document.hidden) { scheduleLive(); return; }
            Object.keys(cache).forEach(function (k) { if (isLive(cache[k].v)) delete cache[k]; });
            hydrate(document);
        }, LIVE_MS);
    }
    function invalidate() { cache = {}; }

    function fetchOne(kind, tmdbId) {
        return fetch('/api/video/title-status/' + encodeURIComponent(kind) + '/' + encodeURIComponent(tmdbId),
            { headers: { 'Accept': 'application/json' } })
            .then(function (r) { return r.ok ? r.json() : null; })
            .then(function (d) { return (d && d.success) ? d : null; })
            .catch(function () { return null; });
    }

    // ── the detail page's block ────────────────────────────────────────────
    function ago(ts) {
        if (!ts) return '';
        var t = Date.parse(String(ts).replace(' ', 'T') + (/[zZ]|[+-]\d\d:?\d\d$/.test(ts) ? '' : 'Z'));
        if (!isFinite(t)) return '';
        var mins = Math.max(0, Math.round((Date.now() - t) / 60000));
        if (mins < 2) return 'just now';
        if (mins < 60) return mins + ' minutes ago';
        var hrs = Math.round(mins / 60);
        if (hrs < 24) return hrs === 1 ? 'an hour ago' : hrs + ' hours ago';
        var days = Math.round(hrs / 24);
        if (days < 30) return days === 1 ? 'yesterday' : days + ' days ago';
        var months = Math.round(days / 30);
        return months <= 1 ? 'a month ago' : months + ' months ago';
    }
    function names(list) {
        if (list.length <= 1) return list.join('');
        if (list.length === 2) return list[0] + ' and ' + list[1];
        return list[0] + ', ' + list[1] + ' and ' + (list.length - 2) + ' more';
    }
    // who asked: names for admins (the server only sends them to admins),
    // "you" for your own, nobody else's name for a member
    function askedLine(reqs) {
        if (!reqs || !reqs.length) return '';
        var when = ago(reqs[0].created_at);
        var parts = [], unnamed = 0;
        if (reqs.some(function (r) { return r.mine; })) parts.push('you');
        reqs.forEach(function (r) {
            if (r.mine) return;
            if (!r.who) { unnamed++; return; }
            if (parts.indexOf(r.who) === -1) parts.push(r.who);
        });
        if (unnamed) parts.push(parts.length ? 'others' : 'someone');
        return 'Requested by ' + names(parts) + (when ? ' · ' + when : '');
    }
    var HEADLINES = {
        available: 'In your library',
        partial: 'Partly here',
        downloading: 'Downloading',
        searching: 'Searching for a release',
        failed: 'Couldn’t get it yet',
        pending: 'Requested, waiting for approval',
    };
    function headline(s) {
        if (s.status === 'processing') {
            return { approved: 'Approved, on the way', wished: 'On the wishlist, on the way',
                followed: 'On the watchlist, new episodes come in by themselves' }[s.reason] || 'On the way';
        }
        return HEADLINES[s.status] || '';
    }
    function seasonPill(se) {
        var tail = se.status === 'available' ? '✓'
            : se.status === 'partial' ? (se.owned + '/' + se.aired)
                : 'on the way';
        return '<span class="vts-season vts-season--' + esc(se.status) + '" title="Season ' + esc(se.season) +
            (se.status === 'partial' ? ': ' + se.owned + ' of ' + se.aired + ' aired episodes' : '') + '">' +
            'S' + esc(se.season) + ' <span class="vts-season-tail">' + esc(tail) + '</span></span>';
    }
    // null when there's nothing worth saying (an owned movie nobody asked for)
    function blockHtml(s) {
        if (!s) return null;
        var reqs = s.requests || [];
        if (!s.status && !reqs.length) return null;
        if (s.status === 'available' && !reqs.length) return null;
        var b = badgeFor(s) || { variant: 'req', label: 'Requested' };
        var title = s.status ? headline(s) : 'Requested';
        var subs = [];
        if (s.status === 'partial' && s.episodes && s.episodes.aired) {
            subs.push(s.episodes.owned + ' of ' + s.episodes.aired + ' aired episodes here');
        }
        var asked = askedLine(reqs);
        if (asked) subs.push(asked);
        var mine = reqs.some(function (r) { return r.mine; });
        var pct = s.status === 'downloading' && s.progress > 0 ? Math.min(100, Math.round(s.progress)) : 0;
        if (pct) title += ' · ' + pct + '%';
        var seasons = (s.seasons || []).filter(function (se) { return se.status !== 'available'; }).length
            ? s.seasons : [];
        return '<div class="vts-block vts-block--' + b.variant + '" role="status">' +
            '<span class="vts-dot" aria-hidden="true"></span>' +
            '<div class="vts-main">' +
                '<div class="vts-title">' + esc(title) + '</div>' +
                (subs.length ? '<div class="vts-sub">' + esc(subs.join(' · ')) +
                    (mine ? ' <button type="button" class="vts-link" data-vts-goto-requests>Your requests</button>' : '') +
                    '</div>' : '') +
                (pct ? '<div class="vts-bar" aria-hidden="true"><span class="vts-bar-fill" style="width:' + pct + '%"></span></div>' : '') +
                (seasons.length ? '<div class="vts-seasons">' + seasons.map(seasonPill).join('') + '</div>' : '') +
            '</div></div>';
    }

    // "Your requests" opens the requests page, where a member's own asks live
    document.addEventListener('click', function (e) {
        var go = e.target.closest && e.target.closest('[data-vts-goto-requests]');
        if (!go) return;
        e.preventDefault();
        document.dispatchEvent(new CustomEvent('soulsync:video-navigate', { detail: 'video-requests' }));
    });

    // anything that changes a status, anywhere: forget and repaint what's on screen
    var changeT;
    function onChange() {
        clearTimeout(changeT);
        changeT = setTimeout(function () { invalidate(); hydrate(document); }, 400);
    }
    ['soulsync:video-wishlist-changed', 'soulsync:video-requests-changed',
        'soulsync:video-download-started'].forEach(function (ev) {
        document.addEventListener(ev, onChange);
    });

    window.VideoTitleStatus = {
        hydrate: hydrate, fetchOne: fetchOne, blockHtml: blockHtml, badgeFor: badgeFor, cardAttrs: cardAttrs,
        invalidate: invalidate, isLive: isLive,
    };
})();
