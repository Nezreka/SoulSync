/*
 * SoulSync — Video Wishlist page (isolated).
 *
 * The curated 'get this' list, split by a Movies / TV tab. Movies render as a
 * poster grid; TV groups into collapsible show → season → episode rows with
 * wanted/done roll-ups and a remove (✕) at every level. Server-paged + searchable
 * like the other pages. Reads /api/video/wishlist; removes via /wishlist/remove.
 * Self-contained IIFE, no globals.
 */
(function () {
    'use strict';

    var PAGE_ID = 'video-wishlist';
    var LIMIT = 60;
    var state = { loaded: false, tab: 'movie', search: '', sort: 'added', page: 1,
                  counts: { movie: 0, show: 0, episode: 0 } };
    var searchTimer = null;

    function $(s, r) { return (r || document).querySelector(s); }
    function esc(s) {
        return String(s == null ? '' : s)
            .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
    }
    function hueOf(s) { var h = 0, t = String(s || ''); for (var i = 0; i < t.length; i++) h = (h * 31 + t.charCodeAt(i)) >>> 0; return h % 360; }
    var MO = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
    function fmtDate(iso) {
        var p = String(iso || '').split('-');
        if (p.length < 3) return '';
        return MO[(+p[1] || 1) - 1] + ' ' + (+p[2] || 1);
    }

    var STATUS = {
        wanted: ['Wanted', 'vwsh-st--wanted'], searching: ['Searching', 'vwsh-st--searching'],
        downloading: ['Downloading', 'vwsh-st--downloading'], downloaded: ['Done', 'vwsh-st--done'],
        failed: ['Failed', 'vwsh-st--failed'],
    };
    function statusPill(status) {
        var s = STATUS[status] || STATUS.wanted;
        return '<span class="vwsh-st ' + s[1] + '">' + s[0] + '</span>';
    }
    function rmBtn(scope, attrs) {
        return '<button class="vwsh-rm" type="button" title="Remove" aria-label="Remove" ' +
            'data-vwsh-rm="' + scope + '"' + attrs + '>&times;</button>';
    }

    // ── movie card ────────────────────────────────────────────────────────────
    function movieCard(it) {
        var owned = it.library_id != null;
        var art = it.poster_url
            ? '<img class="vwsh-movie-img" src="' + esc(it.poster_url) + '" alt="" loading="lazy" ' +
              'onerror="this.style.display=\'none\'">'
            : '<div class="vwsh-movie-ph">🎬</div>';
        var meta = [it.year, owned ? 'In library' : null].filter(Boolean).join(' · ');
        return '<div class="vwsh-movie" data-vwsh-open-movie="' + esc(it.tmdb_id) +
            '" data-vwsh-src="' + (owned ? 'library' : 'tmdb') + '" data-vwsh-id="' + esc(owned ? it.library_id : it.tmdb_id) + '">' +
            '<div class="vwsh-movie-art">' + art + '<div class="vwsh-movie-scrim"></div>' +
            statusPill(it.status) + rmBtn('movie', ' data-tmdb="' + esc(it.tmdb_id) + '"') + '</div>' +
            '<div class="vwsh-movie-info"><span class="vwsh-movie-title" title="' + esc(it.title) + '">' +
            esc(it.title) + '</span>' + (meta ? '<span class="vwsh-movie-meta">' + esc(meta) + '</span>' : '') +
            '</div></div>';
    }

    // ── show orb (the "Nebula": show→artist, season→album, episode→track) ─────
    function initials(s) {
        var w = String(s || '').replace(/[^A-Za-z0-9 ]/g, '').split(' ').filter(Boolean);
        var i = w.slice(0, 2).map(function (x) { return x[0]; }).join('');
        return (i || String(s || '?').slice(0, 2)).toUpperCase();
    }
    function orbSize(n) { return n >= 10 ? 'orb-lg' : n >= 4 ? 'orb-md' : 'orb-sm'; }

    function nebulaOrb(sh, idx) {
        var src = sh.library_id != null ? 'library' : 'tmdb';
        var openId = sh.library_id != null ? sh.library_id : sh.tmdb_id;
        var hue = hueOf(sh.title);
        var total = sh.wanted || 0;
        var img = sh.poster_url
            ? '<img class="wl-orb-img" src="' + esc(sh.poster_url) + '" alt="" ' +
              'onerror="this.outerHTML=\'<div class=&quot;wl-orb-initials&quot;>' + esc(initials(sh.title)) + '</div>\'">'
            : '<div class="wl-orb-initials">' + esc(initials(sh.title)) + '</div>';
        // Season tiles are SELECTORS now — picking one renders its episodes full-
        // width below (so episode cards get room), instead of cramming inside.
        var tiles = (sh.seasons || []).map(function (se) {
            var n = se.episodes.length;
            var posterUrl = se.poster_url || sh.poster_url || null;
            var inner = posterUrl ? '<img src="' + esc(posterUrl) + '" alt="">' : '<div class="wl-album-tile-fallback">📺</div>';
            return '<div class="wl-album-tile" data-vwsh-tile data-tmdb="' + esc(sh.tmdb_id) + '" data-vwsh-season="' + se.season_number + '">' +
                '<div class="wl-album-tile-art">' + inner + '<span class="vwsh-season-tag">S' + se.season_number + '</span></div>' +
                '<div class="wl-album-tile-info">' +
                    '<div class="wl-album-tile-name">Season ' + se.season_number + '</div>' +
                    '<div class="wl-album-tile-count">' + n + ' episode' + (n === 1 ? '' : 's') + '</div>' +
                '</div>' +
                '<span class="wl-album-tile-badge">' + n + ' ep</span>' +
                '<button class="wl-album-tile-remove" type="button" data-vwsh-rm="season" ' +
                'data-tmdb="' + esc(sh.tmdb_id) + '" data-s="' + se.season_number + '" title="Remove season">&#10005;</button>' +
            '</div>';
        }).join('');
        var eps = total + ' episode' + (total === 1 ? '' : 's');
        // --orb-hue on the GROUP so the music orb styles + my cinematic-expand
        // backdrop (--vwsh-poster) both resolve; poster bleeds in only when expanded.
        var gstyle = 'animation-delay:' + Math.min(idx * 45, 700) + 'ms;--orb-hue:' + hue +
            (sh.poster_url ? ";--vwsh-poster:url('" + esc(sh.poster_url) + "')" : '');
        var prog = total ? Math.max(0, Math.min(1, (sh.done || 0) / total)) : 0;   // #4 acquisition progress
        return '<div class="wl-orb-group" data-vwsh-group style="' + gstyle + '">' +
            '<button class="wl-orb-remove" type="button" data-vwsh-rm="show" data-tmdb="' + esc(sh.tmdb_id) + '" title="Remove show">&#10005;</button>' +
            '<div class="wl-orb-tooltip">' + esc(sh.title) + '<br><span>' + eps + '</span></div>' +
            '<div class="wl-orb ' + orbSize(total) + '" data-vwsh-orb style="--vwsh-prog:' + prog + '">' +
                '<div class="wl-orb-glow"></div>' + img + '<div class="wl-orb-ring"></div>' +
                '<div class="vwsh-prog"></div>' +
            '</div>' +
            '<div class="wl-orb-label" data-vwsh-open-show data-vwsh-src="' + src + '" data-vwsh-id="' + esc(openId) + '" title="' + esc(sh.title) + '">' + esc(sh.title) + '</div>' +
            '<div class="wl-orb-meta">' + eps + (sh.done ? ' · ' + sh.done + ' done' : '') + '</div>' +
            '<div class="wl-orb-expanded"><div class="wl-album-fan">' + tiles + '</div>' +
                '<div class="vwsh-ep-area" data-vwsh-ep-area></div></div>' +
        '</div>';
    }

    // A single episode as a 2-line card (still + title that can wrap + meta line).
    function epCard(tmdb, se, e) {
        var t = e.title || ('Episode ' + e.episode_number);
        var st = STATUS[e.status] ? e.status : 'wanted';
        var date = fmtDate(e.air_date);
        var thumb = e.still_url
            ? '<span class="vwsh-epc-thumb"><img src="' + esc(e.still_url) + '" alt="" loading="lazy" ' +
              'onerror="this.parentNode.classList.add(\'vwsh-epc-thumb--none\')"></span>'
            : '<span class="vwsh-epc-thumb vwsh-epc-thumb--none"></span>';
        return '<div class="vwsh-epc">' + thumb +
            '<div class="vwsh-epc-body">' +
                '<div class="vwsh-epc-title" title="' + esc(t) + '">' + esc(t) + '</div>' +
                '<div class="vwsh-epc-meta"><span class="vwsh-ep-dot vwsh-ep-dot--' + st + '"></span>' +
                'S' + se.season_number + '·E' + e.episode_number + (date ? ' · ' + esc(date) : '') + '</div>' +
            '</div>' +
            '<button class="vwsh-epc-rm" type="button" data-vwsh-rm="episode" ' +
            'data-tmdb="' + esc(tmdb) + '" data-s="' + se.season_number + '" data-e="' + e.episode_number + '" title="Remove">&#10005;</button>' +
        '</div>';
    }
    function renderEpisodeArea(group, tmdb, seasonNum) {
        var area = group && group.querySelector('[data-vwsh-ep-area]'); if (!area) return;
        var sh = state.showData[tmdb]; var se = null;
        if (sh) (sh.seasons || []).forEach(function (x) { if (x.season_number === seasonNum) se = x; });
        if (!se) { area.innerHTML = ''; return; }
        area.innerHTML = '<div class="vwsh-ep-grid">' +
            (se.episodes || []).map(function (e) { return epCard(tmdb, se, e); }).join('') + '</div>';
    }

    function render(items) {
        var grid = $('[data-vwsh-grid]'); if (!grid) return;
        var shows = state.tab === 'show';
        grid.classList.toggle('wl-nebula-field', shows);
        grid.classList.toggle('vwsh-nebula', shows);   // video-only scope so music wl-* is untouched
        grid.classList.toggle('vwsh-grid--movies', !shows);
        state.showData = {};
        if (shows) items.forEach(function (sh) { state.showData[sh.tmdb_id] = sh; });   // for the episode area
        grid.innerHTML = shows
            ? items.map(function (sh, i) { return nebulaOrb(sh, i); }).join('')
            : items.map(movieCard).join('');
    }

    // ── counts / badges / pager ───────────────────────────────────────────────
    function setCounts(counts) {
        state.counts = { movie: (counts && counts.movie) || 0, show: (counts && counts.show) || 0,
                         episode: (counts && counts.episode) || 0 };
        var cm = $('[data-vwsh-count-movie]'); if (cm) cm.textContent = state.counts.movie;
        var cs = $('[data-vwsh-count-show]'); if (cs) cs.textContent = state.counts.show;
        updateBadges(counts && counts.total != null ? counts.total : (state.counts.movie + state.counts.episode));
        updateSub();
    }
    function updateSub() {
        var el = $('[data-vwsh-sub]'); if (!el) return;
        var c = state.counts;
        el.textContent = state.tab === 'show'
            ? c.show + ' show' + (c.show === 1 ? '' : 's') + ' · ' + c.episode + ' episode' + (c.episode === 1 ? '' : 's')
            : c.movie + ' movie' + (c.movie === 1 ? '' : 's');
    }
    function updateBadges(total) {
        var n = total || 0;
        ['[data-video-wishlist-badge]', '[data-video-badge="wishlist"]'].forEach(function (sel) {
            document.querySelectorAll(sel).forEach(function (b) {
                b.textContent = n; b.classList.toggle('hidden', !n);
            });
        });
    }
    function refreshBadge() {
        fetch('/api/video/wishlist/counts', { headers: { Accept: 'application/json' } })
            .then(function (r) { return r.ok ? r.json() : null; })
            .then(function (d) { if (d && d.success) updateBadges(d.total); })
            .catch(function () { /* ignore */ });
    }
    function updatePagination(p) {
        var box = $('[data-vwsh-pagination]'), prev = $('[data-vwsh-prev]'),
            next = $('[data-vwsh-next]'), info = $('[data-vwsh-pageinfo]');
        if (!box) return;
        if (!p || p.total_pages <= 1) { box.classList.add('hidden'); return; }
        if (prev) prev.disabled = !p.has_prev;
        if (next) next.disabled = !p.has_next;
        if (info) info.textContent = 'Page ' + p.page + ' of ' + p.total_pages;
        box.classList.remove('hidden');
    }
    function updateEmpty(total) {
        var empty = $('[data-vwsh-empty]'); if (empty) empty.classList.toggle('hidden', total > 0);
        var et = $('[data-vwsh-empty-title]');
        if (et && total === 0) {
            et.textContent = state.search ? 'No matches'
                : (state.tab === 'movie' ? 'No movies on your wishlist yet' : 'No TV episodes on your wishlist yet');
        }
    }

    function load() {
        state.loaded = true;
        var ld = $('[data-vwsh-loading]'); if (ld) ld.classList.remove('hidden');
        var params = new URLSearchParams({ kind: state.tab, search: state.search, sort: state.sort, page: state.page, limit: LIMIT });
        fetch('/api/video/wishlist?' + params.toString(), { headers: { Accept: 'application/json' } })
            .then(function (r) { return r.ok ? r.json() : null; })
            .then(function (d) {
                if (ld) ld.classList.add('hidden');
                if (!d || !d.success) { render([]); updatePagination(null); updateEmpty(0); return; }
                setCounts(d.counts);
                var p = d.pagination || { page: 1, total_pages: 1, total_count: (d.items || []).length };
                state.page = p.page;
                render(d.items || []);
                updatePagination(p);
                updateEmpty(p.total_count);
                maybeBackfillArt(d.items || []);
            })
            .catch(function () { if (ld) ld.classList.add('hidden'); render([]); updatePagination(null); updateEmpty(0); });
    }

    // Rows added before art-capture have no episode still / season poster — fetch
    // them once (cheap: one tmdb_season call per show/season server-side), reload.
    var artBackfilled = false;
    function maybeBackfillArt(items) {
        if (state.tab !== 'show' || artBackfilled) return;
        var missing = (items || []).some(function (sh) {
            return (sh.seasons || []).some(function (se) {
                return !se.poster_url || (se.episodes || []).some(function (e) { return !e.still_url; });
            });
        });
        if (!missing) return;
        artBackfilled = true;
        fetch('/api/video/wishlist/backfill-art', { method: 'POST', headers: { Accept: 'application/json' } })
            .then(function (r) { return r.ok ? r.json() : null; })
            .then(function (res) { if (res && res.updated > 0) load(); })
            .catch(function () { /* best-effort */ });
    }

    function setTab(tab) {
        if (tab !== 'movie' && tab !== 'show') return;
        state.tab = tab; state.page = 1; state.search = '';
        var si = $('[data-vwsh-search]'); if (si) si.value = '';
        var tabs = document.querySelectorAll('[data-vwsh-tab]');
        for (var i = 0; i < tabs.length; i++)
            tabs[i].classList.toggle('vwsh-tab--on', tabs[i].getAttribute('data-vwsh-tab') === tab);
        load();
    }

    // ── remove ────────────────────────────────────────────────────────────────
    function doRemove(btn) {
        var scope = btn.getAttribute('data-vwsh-rm');
        var body = { scope: scope, tmdb_id: parseInt(btn.getAttribute('data-tmdb'), 10) };
        if (btn.hasAttribute('data-s')) body.season_number = parseInt(btn.getAttribute('data-s'), 10);
        if (btn.hasAttribute('data-e')) body.episode_number = parseInt(btn.getAttribute('data-e'), 10);
        btn.disabled = true;
        fetch('/api/video/wishlist/remove', { method: 'POST', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(body) })
            .then(function (r) { return r.ok ? r.json() : null; })
            .then(function (d) {
                if (!d || !d.success) { btn.disabled = false; return; }
                if (typeof showToast === 'function') showToast('Removed from wishlist', 'info');
                load();   // re-render + counts + pager stay correct
            })
            .catch(function () { btn.disabled = false; });
    }

    function onGridClick(e) {
        var rm = e.target.closest('[data-vwsh-rm]');
        if (rm) { e.preventDefault(); e.stopPropagation(); doRemove(rm); return; }
        if (e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;
        var open = e.target.closest('[data-vwsh-open-show], [data-vwsh-open-movie]');
        if (open) {
            document.dispatchEvent(new CustomEvent('soulsync:video-open-detail', {
                detail: { kind: open.hasAttribute('data-vwsh-open-show') ? 'show' : 'movie',
                          id: parseInt(open.getAttribute('data-vwsh-id'), 10),
                          source: open.getAttribute('data-vwsh-src') || 'tmdb' },
            }));
            return;
        }
        var tile = e.target.closest('[data-vwsh-tile]');
        if (tile) {   // season → render its episodes full-width below (single-select)
            var group = tile.closest('.wl-orb-group');
            var wasSel = tile.classList.contains('vwsh-tile--sel');
            if (group) { var all = group.querySelectorAll('.wl-album-tile'); for (var i = 0; i < all.length; i++) all[i].classList.remove('vwsh-tile--sel'); }
            if (wasSel) { var a = group && group.querySelector('[data-vwsh-ep-area]'); if (a) a.innerHTML = ''; return; }
            tile.classList.add('vwsh-tile--sel');
            renderEpisodeArea(group, parseInt(tile.getAttribute('data-tmdb'), 10), parseInt(tile.getAttribute('data-vwsh-season'), 10));
            return;
        }
        var orb = e.target.closest('[data-vwsh-orb]');
        if (orb) { var g = orb.closest('.wl-orb-group'); if (g) g.classList.toggle('expanded'); }   // show → seasons
    }

    function wire() {
        var tabs = document.querySelectorAll('[data-vwsh-tab]');
        for (var i = 0; i < tabs.length; i++) (function (b) {
            b.addEventListener('click', function () { setTab(b.getAttribute('data-vwsh-tab')); });
        })(tabs[i]);
        var grid = $('[data-vwsh-grid]'); if (grid) grid.addEventListener('click', onGridClick);
        var search = $('[data-vwsh-search]');
        if (search) search.addEventListener('input', function () {
            if (searchTimer) clearTimeout(searchTimer);
            searchTimer = setTimeout(function () { state.search = search.value.trim(); state.page = 1; load(); }, 250);
        });
        var sortSel = $('[data-vwsh-sort]');
        if (sortSel) sortSel.addEventListener('change', function () { state.sort = sortSel.value; state.page = 1; load(); });
        var prev = $('[data-vwsh-prev]');
        if (prev) prev.addEventListener('click', function () { if (state.page > 1) { state.page--; load(); } });
        var next = $('[data-vwsh-next]');
        if (next) next.addEventListener('click', function () { state.page++; load(); });
        // Adds elsewhere (the get-modal) refresh the badge + page if visible.
        document.addEventListener('soulsync:video-wishlist-changed', function () {
            var g = $('[data-vwsh-grid]');
            if (g && g.offsetParent !== null) load(); else refreshBadge();
        });
    }

    function onShown(e) { if (e && e.detail === PAGE_ID) { state.page = 1; load(); } }

    function init() {
        wire();
        document.addEventListener('soulsync:video-page-shown', onShown);
        refreshBadge();
    }

    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init);
    else init();
})();
