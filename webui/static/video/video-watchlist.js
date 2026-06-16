/*
 * SoulSync — Video Watchlist page (isolated).
 *
 * The shows + people you follow, split by a Shows / People tab switcher. Reads
 * /api/video/watchlist; cards reuse the shared VideoWatchlist eye-button (here it
 * reads as "watched" and un-follows on click). v1 is membership only — the
 * monitoring/discovery engine that turns follows into downloads comes later.
 */
(function () {
    'use strict';

    var PAGE_ID = 'video-watchlist';
    var state = { loaded: false, tab: 'show', data: { show: [], person: [] } };

    function $(s, r) { return (r || document).querySelector(s); }
    function esc(s) {
        return String(s == null ? '' : s)
            .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
    }
    function wlBtn(opts) { return (window.VideoWatchlist) ? VideoWatchlist.btn(opts) : ''; }

    function cardHTML(it, kind) {
        var href = kind === 'person'
            ? '/video-detail/tmdb/person/' + it.tmdb_id
            : (it.library_id ? '/video-detail/library/show/' + it.library_id
                : '/video-detail/tmdb/show/' + it.tmdb_id);
        var ph = kind === 'person' ? '👤' : '📺';   // 👤 / 📺
        var art = it.poster_url
            ? '<img class="vwlp-card-img" src="' + esc(it.poster_url) + '" alt="" loading="lazy" ' +
              'onload="this.classList.add(\'vwlp-loaded\')" onerror="this.style.display=\'none\'">'
            : '<div class="vwlp-card-ph">' + ph + '</div>';
        var btn = wlBtn({ kind: kind, tmdbId: it.tmdb_id, title: it.title,
                          poster: it.poster_url, libraryId: it.library_id });
        return '<a class="vwlp-card' + (kind === 'person' ? ' vwlp-card--person' : '') + '" href="' + href + '" ' +
            'data-vwlp-card="' + kind + '" data-vwlp-id="' + esc(it.tmdb_id) + '">' +
            '<div class="vwlp-card-art">' + art + '<div class="vwlp-card-scrim"></div>' + btn + '</div>' +
            '<div class="vwlp-card-info"><span class="vwlp-card-title" title="' + esc(it.title) + '">' +
            esc(it.title) + '</span></div></a>';
    }

    function updateEmpty() {
        var n = state.data[state.tab].length;
        ['show', 'person'].forEach(function (k) {
            var g = $('[data-vwlp-grid="' + k + '"]');
            if (g) g.classList.toggle('hidden', k !== state.tab || state.data[k].length === 0);
        });
        var empty = $('[data-vwlp-empty]');
        if (empty) empty.classList.toggle('hidden', n > 0);
        var et = $('[data-vwlp-empty-title]');
        if (et && n === 0) et.textContent = state.tab === 'show'
            ? 'No shows on your watchlist yet' : 'No people on your watchlist yet';
    }

    function setTab(tab) {
        state.tab = tab;
        var tabs = document.querySelectorAll('[data-vwlp-tab]');
        for (var i = 0; i < tabs.length; i++)
            tabs[i].classList.toggle('vwlp-tab--on', tabs[i].getAttribute('data-vwlp-tab') === tab);
        updateEmpty();
    }

    function render() {
        // Seed the shared cache so every button paints "watched" with no flash
        // (everything on this page is, by definition, followed).
        if (window.VideoWatchlist) {
            state.data.show.forEach(function (it) { VideoWatchlist._watched.show[it.tmdb_id] = true; });
            state.data.person.forEach(function (it) { VideoWatchlist._watched.person[it.tmdb_id] = true; });
        }
        var sg = $('[data-vwlp-grid="show"]'), pg = $('[data-vwlp-grid="person"]');
        if (sg) sg.innerHTML = state.data.show.map(function (it) { return cardHTML(it, 'show'); }).join('');
        if (pg) pg.innerHTML = state.data.person.map(function (it) { return cardHTML(it, 'person'); }).join('');
        var cs = $('[data-vwlp-count-show]'); if (cs) cs.textContent = state.data.show.length;
        var cp = $('[data-vwlp-count-person]'); if (cp) cp.textContent = state.data.person.length;
        setTab(state.tab);
    }

    function load() {
        state.loaded = true;
        var ld = $('[data-vwlp-loading]'); if (ld) ld.classList.remove('hidden');
        fetch('/api/video/watchlist', { headers: { Accept: 'application/json' } })
            .then(function (r) { return r.ok ? r.json() : null; })
            .then(function (d) {
                if (ld) ld.classList.add('hidden');
                state.data = (d && d.success)
                    ? { show: d.shows || [], person: d.people || [] }
                    : { show: [], person: [] };
                render();
            })
            .catch(function () { if (ld) ld.classList.add('hidden'); state.data = { show: [], person: [] }; render(); });
    }

    // When an item is un-followed (here or anywhere), drop its card + fix counts.
    function onChanged(e) {
        var det = (e && e.detail) || {};
        if (det.watched) return;   // additions are picked up on next page load
        var kind = det.kind, id = String(det.id);
        if (!state.data[kind]) return;
        state.data[kind] = state.data[kind].filter(function (it) { return String(it.tmdb_id) !== id; });
        var card = document.querySelector('.vwlp-card[data-vwlp-card="' + kind + '"][data-vwlp-id="' + id + '"]');
        if (card && card.parentNode) card.parentNode.removeChild(card);
        var c = $('[data-vwlp-count-' + kind + ']'); if (c) c.textContent = state.data[kind].length;
        updateEmpty();
    }

    function wire() {
        var tabs = document.querySelectorAll('[data-vwlp-tab]');
        for (var i = 0; i < tabs.length; i++) (function (b) {
            b.addEventListener('click', function () { setTab(b.getAttribute('data-vwlp-tab')); });
        })(tabs[i]);
        document.addEventListener('soulsync:video-watchlist-changed', onChanged);
    }

    function onShown(e) { if (e && e.detail === PAGE_ID) load(); }   // reload each visit → stays fresh

    function init() {
        wire();
        document.addEventListener('soulsync:video-page-shown', onShown);
    }

    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init);
    else init();
})();
