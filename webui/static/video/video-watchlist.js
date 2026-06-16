/*
 * SoulSync — Video Watchlist page (isolated).
 *
 * The shows + people you follow, split by a Shows / People tab switcher.
 * Server-paged + searchable like the library (only a page of cards/posters
 * renders at once). Reads /api/video/watchlist?kind=&search=&page=&limit=.
 * Cards reuse the shared VideoWatchlist eye-button (reads as "watched" here;
 * un-follows on click, with a confirm).
 */
(function () {
    'use strict';

    var PAGE_ID = 'video-watchlist';
    var LIMIT = 60;
    var state = { loaded: false, tab: 'show', search: '', sort: 'default', page: 1,
                  counts: { show: 0, person: 0 } };
    var searchTimer = null;

    function $(s, r) { return (r || document).querySelector(s); }
    function esc(s) {
        return String(s == null ? '' : s)
            .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
    }
    function wlBtn(opts) { return (window.VideoWatchlist) ? VideoWatchlist.btn(opts) : ''; }

    function statusPill(status) {
        var s = String(status == null ? '' : status).trim().toLowerCase();
        if (!s) return '';
        if (['ended', 'canceled', 'cancelled', 'completed'].indexOf(s) >= 0)
            return '<span class="vwlp-pill vwlp-pill--ended">Ended</span>';
        if (s.indexOf('return') >= 0 || s === 'continuing')
            return '<span class="vwlp-pill vwlp-pill--airing">Airing</span>';
        if (s === 'upcoming' || s.indexOf('production') >= 0 || s.indexOf('planned') >= 0 || s === 'pilot')
            return '<span class="vwlp-pill vwlp-pill--soon">Upcoming</span>';
        return '<span class="vwlp-pill">' + esc(status) + '</span>';
    }

    function cardHTML(it, kind) {
        // SPA open target: library shows open by library id ('library' source);
        // people + un-owned shows open by tmdb id ('tmdb').
        var source = (kind === 'show' && it.library_id) ? 'library' : 'tmdb';
        var openId = source === 'library' ? it.library_id : it.tmdb_id;
        var href = '/video-detail/' + source + '/' + kind + '/' + openId;
        var ph = kind === 'person' ? '👤' : '📺';   // 👤 / 📺
        var art = it.poster_url
            ? '<img class="vwlp-card-img" src="' + esc(it.poster_url) + '" alt="" loading="lazy" ' +
              'onload="this.classList.add(\'vwlp-loaded\')" onerror="this.style.display=\'none\'">'
            : '<div class="vwlp-card-ph">' + ph + '</div>';
        var btn = wlBtn({ kind: kind, tmdbId: it.tmdb_id, title: it.title,
                          poster: it.poster_url, libraryId: it.library_id });
        var pill = kind === 'show' ? statusPill(it.status) : '';
        var meta = (kind === 'show' && it.episode_count)
            ? '<span class="vwlp-card-meta">' + (it.owned_count || 0) + '/' + it.episode_count + ' eps</span>' : '';
        return '<a class="vwlp-card' + (kind === 'person' ? ' vwlp-card--person' : '') + '" href="' + href + '" ' +
            'data-vwlp-open="' + kind + '" data-vwlp-source="' + source + '" data-vwlp-openid="' + esc(openId) + '">' +
            '<div class="vwlp-card-art">' + art + '<div class="vwlp-card-scrim"></div>' + pill + btn + '</div>' +
            '<div class="vwlp-card-info"><span class="vwlp-card-title" title="' + esc(it.title) + '">' +
            esc(it.title) + '</span>' + meta + '</div></a>';
    }

    function setCounts(counts) {
        state.counts = { show: (counts && counts.show) || 0, person: (counts && counts.person) || 0 };
        var cs = $('[data-vwlp-count-show]'); if (cs) cs.textContent = state.counts.show;
        var cp = $('[data-vwlp-count-person]'); if (cp) cp.textContent = state.counts.person;
    }

    function updatePagination(p) {
        var box = $('[data-vwlp-pagination]'), prev = $('[data-vwlp-prev]'),
            next = $('[data-vwlp-next]'), info = $('[data-vwlp-pageinfo]');
        if (!box) return;
        if (!p || p.total_pages <= 1) { box.classList.add('hidden'); return; }
        if (prev) prev.disabled = !p.has_prev;
        if (next) next.disabled = !p.has_next;
        if (info) info.textContent = 'Page ' + p.page + ' of ' + p.total_pages;
        box.classList.remove('hidden');
    }

    function updateEmpty(total) {
        var empty = $('[data-vwlp-empty]');
        if (empty) empty.classList.toggle('hidden', total > 0);
        var et = $('[data-vwlp-empty-title]');
        if (et && total === 0) {
            et.textContent = state.search
                ? 'No matches'
                : (state.tab === 'show' ? 'No shows on your watchlist yet' : 'No people on your watchlist yet');
        }
    }

    function render(items) {
        // Everything on this page is watched — seed the shared cache so the eyes
        // paint "watched" with no flash.
        if (window.VideoWatchlist) {
            items.forEach(function (it) { VideoWatchlist._watched[state.tab][it.tmdb_id] = true; });
        }
        var grid = $('[data-vwlp-grid]');
        if (grid) {
            grid.innerHTML = items.map(function (it) { return cardHTML(it, state.tab); }).join('');
            if (window.VideoWatchlist) VideoWatchlist.hydrate(grid);
        }
    }

    function load() {
        state.loaded = true;
        var ld = $('[data-vwlp-loading]'); if (ld) ld.classList.remove('hidden');
        var params = new URLSearchParams({
            kind: state.tab, search: state.search, sort: state.sort, page: state.page, limit: LIMIT });
        fetch('/api/video/watchlist?' + params.toString(), { headers: { Accept: 'application/json' } })
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
        if (tab !== 'show' && tab !== 'person') return;
        state.tab = tab; state.page = 1;
        var tabs = document.querySelectorAll('[data-vwlp-tab]');
        for (var i = 0; i < tabs.length; i++)
            tabs[i].classList.toggle('vwlp-tab--on', tabs[i].getAttribute('data-vwlp-tab') === tab);
        load();
    }

    // A removal anywhere → if we're showing the watchlist, reload the page so the
    // un-followed card drops and counts/pagination stay correct.
    function onChanged() {
        var grid = $('[data-vwlp-grid]');
        if (state.loaded && grid && grid.offsetParent !== null) load();
    }

    function wire() {
        var tabs = document.querySelectorAll('[data-vwlp-tab]');
        for (var i = 0; i < tabs.length; i++) (function (b) {
            b.addEventListener('click', function () { setTab(b.getAttribute('data-vwlp-tab')); });
        })(tabs[i]);

        var grid = $('[data-vwlp-grid]');
        if (grid) grid.addEventListener('click', onGridClick);

        var search = $('[data-vwlp-search]');
        if (search) search.addEventListener('input', function () {
            if (searchTimer) clearTimeout(searchTimer);
            searchTimer = setTimeout(function () {
                state.search = search.value.trim(); state.page = 1; load();
            }, 250);
        });

        var sortSel = $('[data-vwlp-sort]');
        if (sortSel) sortSel.addEventListener('change', function () {
            state.sort = sortSel.value; state.page = 1; load();
        });

        var prev = $('[data-vwlp-prev]');
        if (prev) prev.addEventListener('click', function () { if (state.page > 1) { state.page--; load(); } });
        var next = $('[data-vwlp-next]');
        if (next) next.addEventListener('click', function () { state.page++; load(); });

        document.addEventListener('soulsync:video-watchlist-changed', onChanged);
    }

    // Intercept card clicks → in-app SPA navigation (a bare <a href> would do a
    // FULL page reload). The eye button's capture-phase handler already stops its
    // own clicks from reaching here. Mirrors video-library.js.
    function onGridClick(e) {
        if (e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;
        var card = e.target.closest('[data-vwlp-open]');
        if (!card) return;
        e.preventDefault();
        document.dispatchEvent(new CustomEvent('soulsync:video-open-detail', {
            detail: {
                kind: card.getAttribute('data-vwlp-open'),
                id: parseInt(card.getAttribute('data-vwlp-openid'), 10),
                source: card.getAttribute('data-vwlp-source') || 'library',
            },
        }));
    }

    function onShown(e) { if (e && e.detail === PAGE_ID) { state.page = 1; load(); } }

    function init() {
        wire();
        document.addEventListener('soulsync:video-page-shown', onShown);
    }

    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init);
    else init();
})();
