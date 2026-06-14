/*
 * SoulSync — Video Library page (isolated).
 *
 * Reuses the music library's exact look (.library-artist-card grid,
 * .alphabet-selector, .library-search-input) — only the data is new
 * (/api/video/library) and posters come from the video poster proxy. Movies/
 * Shows tabs, a search box, and an A–Z selector filter client-side. Cards are
 * NOT clickable yet (divs, not links). Self-contained IIFE, no globals.
 */
(function () {
    'use strict';

    var PAGE_ID = 'video-library';
    var LIBRARY_URL = '/api/video/library';
    var LETTERS = 'abcdefghijklmnopqrstuvwxyz'.split('');

    var state = { tab: 'movies', data: null, loading: false, search: '', letter: 'all' };

    function $(sel) { return document.querySelector(sel); }

    function esc(s) {
        return String(s == null ? '' : s)
            .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
    }

    // First letter for the A–Z buckets, ignoring a leading article.
    function letterOf(title) {
        var t = String(title || '').toLowerCase().replace(/^(the|a|an)\s+/, '').trim();
        var c = t.charAt(0);
        return /[a-z]/.test(c) ? c : '#';
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
                    render();
                });
            })(defs[i][0], b);
            host.appendChild(b);
        }
    }

    function items() {
        return (state.data && state.data[state.tab]) || [];
    }

    function filtered() {
        var search = state.search.toLowerCase();
        return items().filter(function (it) {
            if (search && String(it.title || '').toLowerCase().indexOf(search) === -1) return false;
            if (state.letter !== 'all' && letterOf(it.title) !== state.letter) return false;
            return true;
        });
    }

    function cardHTML(it, kind) {
        var fallbackEmoji = kind === 'movie' ? '🎬' : '📺';
        var img = it.has_poster
            ? '<div class="library-artist-image"><img src="/api/video/poster/' + kind + '/' + it.id +
              '" alt="" loading="lazy" onerror="this.parentNode.innerHTML=\'<div class=&quot;library-artist-image-fallback&quot;>' +
              fallbackEmoji + '</div>\'"></div>'
            : '<div class="library-artist-image"><div class="library-artist-image-fallback">' + fallbackEmoji + '</div></div>';

        var meta = [];
        if (it.year) meta.push(String(it.year));
        if (kind === 'movie') meta.push(it.has_file ? 'Owned' : 'Wanted');
        else meta.push((it.owned_count || 0) + '/' + (it.episode_count || 0) + ' eps');

        return '<div class="library-artist-card">' + img +
            '<div class="library-artist-info">' +
            '<h3 class="library-artist-name" title="' + esc(it.title) + '">' + esc(it.title) + '</h3>' +
            '<div class="library-artist-stats"><span class="library-artist-stat">' +
            esc(meta.join(' · ')) + '</span></div></div></div>';
    }

    function render() {
        var grid = $('[data-video-lib-grid]');
        var empty = $('[data-video-lib-empty]');
        var loading = $('[data-video-lib-loading]');
        var count = $('[data-video-lib-count]');
        var countLabel = $('[data-video-lib-count-label]');
        var search = $('[data-video-lib-search]');
        if (!grid) return;

        if (loading) loading.classList.toggle('hidden', !state.loading);
        if (search) search.placeholder = 'Search ' + (state.tab === 'movies' ? 'movies' : 'shows') + '...';
        if (countLabel) countLabel.textContent = state.tab === 'movies' ? 'Movies' : 'Shows';
        if (count) count.textContent = items().length;

        if (state.loading) { grid.innerHTML = ''; if (empty) empty.classList.add('hidden'); return; }

        var kind = state.tab === 'movies' ? 'movie' : 'show';
        var list = filtered();
        grid.innerHTML = list.map(function (it) { return cardHTML(it, kind); }).join('');

        if (empty) {
            empty.classList.toggle('hidden', list.length > 0);
            var t = $('[data-video-lib-empty-title]');
            if (t) t.textContent = items().length === 0 ? 'Nothing here yet'
                : 'No ' + (state.tab === 'movies' ? 'movies' : 'shows') + ' match';
        }
    }

    function load(force) {
        if (state.data && !force) { render(); return; }
        state.loading = true;
        render();
        fetch(LIBRARY_URL, { headers: { 'Accept': 'application/json' } })
            .then(function (r) { return r.ok ? r.json() : null; })
            .then(function (d) {
                state.loading = false;
                state.data = (d && !d.error) ? d : { movies: [], shows: [] };
                render();
            })
            .catch(function () { state.loading = false; state.data = { movies: [], shows: [] }; render(); });
    }

    function wire() {
        var tabs = document.querySelectorAll('[data-video-lib-tab]');
        for (var i = 0; i < tabs.length; i++) {
            (function (tab) {
                tab.addEventListener('click', function () {
                    state.tab = tab.getAttribute('data-video-lib-tab');
                    var all = document.querySelectorAll('[data-video-lib-tab]');
                    for (var j = 0; j < all.length; j++) all[j].classList.toggle('active', all[j] === tab);
                    render();
                });
            })(tabs[i]);
        }
        var search = $('[data-video-lib-search]');
        if (search) {
            search.addEventListener('input', function () { state.search = search.value || ''; render(); });
        }
    }

    function onScanProgress(e) {
        var s = e.detail || {};
        if (s.state !== 'scanning') return;
        var label = $('[data-video-scan-label]');
        if (label) label.textContent = 'Scanning…';
    }

    function onScanDone() {
        var label = $('[data-video-scan-label]');
        if (label) label.textContent = 'Scan';
        load(true);
    }

    function onPageShown(e) {
        if (!e || e.detail !== PAGE_ID) return;
        load(false);
    }

    function init() {
        buildAlphabet();
        wire();
        document.addEventListener('soulsync:video-page-shown', onPageShown);
        document.addEventListener('soulsync:video-scan-progress', onScanProgress);
        document.addEventListener('soulsync:video-scan-done', onScanDone);
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }
})();
