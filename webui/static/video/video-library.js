/*
 * SoulSync — Video Library page (isolated).
 *
 * Reuses the music library's look + paging model: server-side search / A–Z /
 * sort / owned-wanted filter / pagination (75 per page) via /api/video/library,
 * rendering one page of music-style .library-artist-card cards (with a poster
 * proxy + resolution badge). Cards are NOT clickable yet. IIFE, no globals.
 */
(function () {
    'use strict';

    var PAGE_ID = 'video-library';
    var LIBRARY_URL = '/api/video/library';
    var LETTERS = 'abcdefghijklmnopqrstuvwxyz'.split('');

    var state = { tab: 'movies', search: '', letter: 'all', sort: 'title',
                  status: 'all', page: 1, limit: 75, loaded: false };
    var searchTimer = null;

    function $(sel) { return document.querySelector(sel); }

    function esc(s) {
        return String(s == null ? '' : s)
            .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
    }

    function resLabel(res) {
        if (!res) return '';
        res = String(res).toLowerCase();
        if (res.indexOf('2160') > -1 || res === '4k') return '4K';
        if (res.indexOf('1080') > -1) return '1080p';
        if (res.indexOf('720') > -1) return '720p';
        if (res.indexOf('480') > -1 || res.indexOf('576') > -1) return 'SD';
        return res.toUpperCase();
    }

    function buildAlphabet() {
        var host = $('[data-video-lib-alphabet]');
        if (!host || host.childNodes.length) return;
        var defs = [['all', 'All']].concat(
            LETTERS.map(function (l) { return [l, l.toUpperCase()]; }), [['#', '#']]);
        for (var i = 0; i < defs.length; i++) {
            var b = document.createElement('button');
            b.className = 'alphabet-btn' + (defs[i][0] === 'all' ? ' active' : '');
            b.setAttribute('data-letter', defs[i][0]);
            b.textContent = defs[i][1];
            (function (letter, btn) {
                btn.addEventListener('click', function () {
                    state.letter = letter;
                    var all = host.querySelectorAll('.alphabet-btn');
                    for (var j = 0; j < all.length; j++) all[j].classList.toggle('active', all[j] === btn);
                    reload();
                });
            })(defs[i][0], b);
            host.appendChild(b);
        }
    }

    function cardHTML(it, kind) {
        var fallback = kind === 'movie' ? '🎬' : '📺';
        var img = it.has_poster
            ? '<div class="library-artist-image"><img src="/api/video/poster/' + kind + '/' + it.id +
              '" alt="" loading="lazy" onerror="this.parentNode.innerHTML=\'<div class=&quot;library-artist-image-fallback&quot;>' +
              fallback + '</div>\'"></div>'
            : '<div class="library-artist-image"><div class="library-artist-image-fallback">' + fallback + '</div></div>';

        var badge = '';
        if (kind === 'movie') {
            var rl = resLabel(it.resolution);
            if (rl) badge = '<div class="video-card-badge">' + rl + '</div>';
        }

        var meta = [];
        if (it.year) meta.push(String(it.year));
        if (kind === 'movie') meta.push(it.has_file ? 'Owned' : 'Wanted');
        else meta.push((it.owned_count || 0) + '/' + (it.episode_count || 0) + ' eps');

        return '<div class="library-artist-card">' + img + badge +
            '<div class="library-artist-info">' +
            '<h3 class="library-artist-name" title="' + esc(it.title) + '">' + esc(it.title) + '</h3>' +
            '<div class="library-artist-stats"><span class="library-artist-stat">' +
            esc(meta.join(' · ')) + '</span></div></div></div>';
    }

    function showLoading(on) {
        var l = $('[data-video-lib-loading]');
        if (l) l.classList.toggle('hidden', !on);
    }

    function renderItems(items, kind) {
        var grid = $('[data-video-lib-grid]');
        var empty = $('[data-video-lib-empty]');
        if (grid) grid.innerHTML = items.map(function (it) { return cardHTML(it, kind); }).join('');
        if (empty) empty.classList.toggle('hidden', items.length > 0);
    }

    function updatePagination(p) {
        var box = $('[data-video-lib-pagination]');
        var prev = $('[data-video-lib-prev]');
        var next = $('[data-video-lib-next]');
        var info = $('[data-video-lib-pageinfo]');
        if (!box) return;
        if (!p) { box.classList.add('hidden'); return; }
        if (prev) prev.disabled = !p.has_prev;
        if (next) next.disabled = !p.has_next;
        if (info) info.textContent = 'Page ' + p.page + ' of ' + p.total_pages;
        box.classList.toggle('hidden', p.total_pages <= 1);
    }

    function setCount(n) {
        var c = $('[data-video-lib-count]');
        var lbl = $('[data-video-lib-count-label]');
        if (c) c.textContent = n;
        if (lbl) lbl.textContent = state.tab === 'movies' ? 'Movies' : 'Shows';
    }

    function load() {
        state.loaded = true;
        showLoading(true);
        var apiKind = state.tab === 'movies' ? 'movies' : 'shows';   // query param (plural)
        var cardKind = state.tab === 'movies' ? 'movie' : 'show';    // card + poster URL (singular)
        var params = new URLSearchParams({
            kind: apiKind, search: state.search, letter: state.letter, sort: state.sort,
            status: state.status, page: state.page, limit: state.limit });
        fetch(LIBRARY_URL + '?' + params.toString(), { headers: { 'Accept': 'application/json' } })
            .then(function (r) { return r.ok ? r.json() : null; })
            .then(function (d) {
                showLoading(false);
                if (!d || d.error) { renderItems([], cardKind); updatePagination(null); setCount(0); return; }
                renderItems(d.items || [], cardKind);
                updatePagination(d.pagination);
                setCount(d.pagination ? d.pagination.total_count : 0);
            })
            .catch(function () { showLoading(false); renderItems([], cardKind); updatePagination(null); setCount(0); });
    }

    function reload() { state.page = 1; load(); }

    function wire() {
        var tabs = document.querySelectorAll('[data-video-lib-tab]');
        for (var i = 0; i < tabs.length; i++) {
            (function (tab) {
                tab.addEventListener('click', function () {
                    state.tab = tab.getAttribute('data-video-lib-tab');
                    var all = document.querySelectorAll('[data-video-lib-tab]');
                    for (var j = 0; j < all.length; j++) all[j].classList.toggle('active', all[j] === tab);
                    var s = $('[data-video-lib-search]');
                    if (s) s.placeholder = 'Search ' + (state.tab === 'movies' ? 'movies' : 'shows') + '...';
                    reload();
                });
            })(tabs[i]);
        }
        var search = $('[data-video-lib-search]');
        if (search) {
            search.addEventListener('input', function () {
                state.search = search.value || '';
                if (searchTimer) clearTimeout(searchTimer);
                searchTimer = setTimeout(reload, 300);
            });
        }
        var sort = $('[data-video-lib-sort]');
        if (sort) sort.addEventListener('change', function () { state.sort = sort.value; reload(); });
        var status = $('[data-video-lib-status]');
        if (status) status.addEventListener('change', function () { state.status = status.value; reload(); });
        var prev = $('[data-video-lib-prev]');
        if (prev) prev.addEventListener('click', function () {
            if (state.page > 1) { state.page--; load(); }
        });
        var next = $('[data-video-lib-next]');
        if (next) next.addEventListener('click', function () { state.page++; load(); });
    }

    function onScanDone() { if (state.loaded) load(); }

    function onPageShown(e) {
        if (!e || e.detail !== PAGE_ID) return;
        if (!state.loaded) load();
    }

    function init() {
        buildAlphabet();
        wire();
        document.addEventListener('soulsync:video-page-shown', onPageShown);
        document.addEventListener('soulsync:video-scan-done', onScanDone);
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }
})();
