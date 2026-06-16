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
    var state = { loaded: false, tab: 'movie', search: '', page: 1, counts: { movie: 0, show: 0 } };
    var searchTimer = null;

    function $(s, r) { return (r || document).querySelector(s); }
    function esc(s) {
        return String(s == null ? '' : s)
            .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
    }
    function hueOf(s) { var h = 0, t = String(s || ''); for (var i = 0; i < t.length; i++) h = (h * 31 + t.charCodeAt(i)) >>> 0; return h % 360; }

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

    // ── show reel (poster + per-season film strips of episode cells) ──────────
    function reelCard(sh) {
        var src = sh.library_id != null ? 'library' : 'tmdb';
        var openId = sh.library_id != null ? sh.library_id : sh.tmdb_id;
        var hook = ' data-vwsh-open-show="1" data-vwsh-src="' + src + '" data-vwsh-id="' + esc(openId) + '"';
        var poster = sh.poster_url
            ? '<img class="vwsh-reel-img" src="' + esc(sh.poster_url) + '" alt="" loading="lazy" onerror="this.style.display=\'none\'">'
            : '<div class="vwsh-reel-ph">📺</div>';
        var done = sh.done ? ' · ' + sh.done + ' done' : '';
        var strips = (sh.seasons || []).map(function (se) {
            var cells = (se.episodes || []).map(function (e) {
                var st = STATUS[e.status] ? e.status : 'wanted';
                var t = e.title || ('Episode ' + e.episode_number);
                return '<div class="vwsh-cell vwsh-cell--' + st + '" title="S' + se.season_number + '·E' + e.episode_number + ' — ' + esc(t) + '">' +
                    '<span class="vwsh-cell-num">E' + e.episode_number + '</span>' +
                    '<span class="vwsh-cell-title">' + esc(t) + '</span>' +
                    rmBtn('episode', ' data-tmdb="' + esc(sh.tmdb_id) + '" data-s="' + se.season_number + '" data-e="' + e.episode_number + '"') +
                    '</div>';
            }).join('');
            return '<div class="vwsh-strip">' +
                '<div class="vwsh-strip-label">S' + se.season_number +
                    rmBtn('season', ' data-tmdb="' + esc(sh.tmdb_id) + '" data-s="' + se.season_number + '"') +
                '</div>' +
                '<div class="vwsh-strip-track">' + cells + '</div>' +
            '</div>';
        }).join('');
        return '<div class="vwsh-reel" style="--vwsh-h:' + hueOf(sh.title) + '">' +
            '<div class="vwsh-reel-poster"' + hook + '>' + poster + '</div>' +
            '<div class="vwsh-reel-body">' +
                '<div class="vwsh-reel-head">' +
                    '<div class="vwsh-reel-titles"' + hook + '>' +
                        '<span class="vwsh-reel-title" title="' + esc(sh.title) + '">' + esc(sh.title) + '</span>' +
                        '<span class="vwsh-reel-meta">' + sh.wanted + ' wanted' + done + '</span>' +
                    '</div>' +
                    rmBtn('show', ' data-tmdb="' + esc(sh.tmdb_id) + '"') +
                '</div>' +
                '<div class="vwsh-strips">' + strips + '</div>' +
            '</div>' +
        '</div>';
    }

    function render(items) {
        var grid = $('[data-vwsh-grid]'); if (!grid) return;
        var shows = state.tab === 'show';
        grid.classList.toggle('vwsh-grid--shows', shows);
        grid.classList.toggle('vwsh-grid--movies', !shows);
        grid.innerHTML = items.map(shows ? reelCard : movieCard).join('');
    }

    // ── counts / badges / pager ───────────────────────────────────────────────
    function setCounts(counts) {
        state.counts = { movie: (counts && counts.movie) || 0, show: (counts && counts.show) || 0 };
        var cm = $('[data-vwsh-count-movie]'); if (cm) cm.textContent = state.counts.movie;
        var cs = $('[data-vwsh-count-show]'); if (cs) cs.textContent = state.counts.show;
        updateBadges(counts && counts.total != null ? counts.total : (state.counts.movie + state.counts.show));
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
        var params = new URLSearchParams({ kind: state.tab, search: state.search, page: state.page, limit: LIMIT });
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
            })
            .catch(function () { if (ld) ld.classList.add('hidden'); render([]); updatePagination(null); updateEmpty(0); });
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
        }
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
