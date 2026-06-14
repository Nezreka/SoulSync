/*
 * SoulSync — Video Library page (isolated).
 *
 * Same isolation contract as the other video modules: self-contained IIFE, no
 * globals, no inline handlers, lives under static/video/. Listens for the
 * 'soulsync:video-page-shown' event (dispatched by video-side.js) and, when the
 * Library page is shown, fetches /api/video/library and renders it. The Scan
 * button asks the server to re-read the media server (source of truth) via
 * /api/video/scan/request and polls /api/video/scan/status.
 */
(function () {
    'use strict';

    var PAGE_ID = 'video-library';
    var LIBRARY_URL = '/api/video/library';
    var SCAN_REQUEST_URL = '/api/video/scan/request';
    var SCAN_STATUS_URL = '/api/video/scan/status';

    var state = { tab: 'movies', data: null, loading: false, scanning: false };

    function $(sel) { return document.querySelector(sel); }

    function el(tag, cls, text) {
        var n = document.createElement(tag);
        if (cls) n.className = cls;
        if (text != null) n.textContent = text;
        return n;
    }

    function poster(node, title, url) {
        var p = el('div', 'video-card-poster');
        if (url && /^https?:\/\//.test(url)) {
            var img = el('img');
            img.alt = '';
            img.loading = 'lazy';
            img.src = url;
            p.appendChild(img);
        } else {
            p.appendChild(el('span', 'video-card-poster-letter',
                (title || '?').charAt(0).toUpperCase()));
        }
        node.appendChild(p);
    }

    function movieCard(m) {
        var card = el('div', 'video-card');
        poster(card, m.title, m.poster_url);
        card.appendChild(el('div', 'video-card-title', m.title || 'Untitled'));
        var meta = [];
        if (m.year) meta.push(String(m.year));
        meta.push(m.has_file ? 'Owned' : 'Wanted');
        card.appendChild(el('div', 'video-card-meta', meta.join(' · ')));
        return card;
    }

    function showCard(s) {
        var card = el('div', 'video-card');
        poster(card, s.title, s.poster_url);
        card.appendChild(el('div', 'video-card-title', s.title || 'Untitled'));
        var meta = [];
        if (s.year) meta.push(String(s.year));
        meta.push((s.owned_count || 0) + '/' + (s.episode_count || 0) + ' eps');
        card.appendChild(el('div', 'video-card-meta', meta.join(' · ')));
        return card;
    }

    function render() {
        var grid = $('[data-video-lib-grid]');
        var empty = $('[data-video-lib-empty]');
        var count = $('[data-video-lib-count]');
        if (!grid) return;
        grid.textContent = '';
        var items = state.data ? (state.data[state.tab] || []) : [];
        var maker = state.tab === 'movies' ? movieCard : showCard;
        for (var i = 0; i < items.length; i++) grid.appendChild(maker(items[i]));
        if (empty) empty.hidden = items.length > 0;
        if (count) {
            count.textContent = state.loading ? 'Loading…'
                : items.length + ' ' + state.tab;
        }
    }

    function loadLibrary(force) {
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
            .catch(function () {
                state.loading = false;
                state.data = { movies: [], shows: [] };
                render();
            });
    }

    function setScanLabel(text) {
        var label = $('[data-video-scan-label]');
        if (label) label.textContent = text;
    }

    function pollScan() {
        fetch(SCAN_STATUS_URL, { headers: { 'Accept': 'application/json' } })
            .then(function (r) { return r.json(); })
            .then(function (s) {
                if (s && s.state === 'scanning') {
                    setScanLabel((s.phase || 'Scanning') + '… '
                        + (s.movies || 0) + 'm ' + (s.shows || 0) + 's');
                    setTimeout(pollScan, 1500);
                } else {
                    state.scanning = false;
                    var btn = $('[data-video-scan]');
                    if (btn) btn.disabled = false;
                    setScanLabel('Scan Library');
                    loadLibrary(true);
                }
            })
            .catch(function () {
                state.scanning = false;
                var btn = $('[data-video-scan]');
                if (btn) btn.disabled = false;
                setScanLabel('Scan Library');
            });
    }

    function startScan() {
        if (state.scanning) return;
        state.scanning = true;
        var btn = $('[data-video-scan]');
        if (btn) btn.disabled = true;
        setScanLabel('Starting…');
        fetch(SCAN_REQUEST_URL, { method: 'POST', headers: { 'Accept': 'application/json' } })
            .then(function () { setTimeout(pollScan, 600); })
            .catch(function () {
                state.scanning = false;
                if (btn) btn.disabled = false;
                setScanLabel('Scan Library');
            });
    }

    function wire() {
        var tabs = document.querySelectorAll('[data-video-lib-tab]');
        for (var i = 0; i < tabs.length; i++) {
            (function (tab) {
                tab.addEventListener('click', function () {
                    state.tab = tab.getAttribute('data-video-lib-tab');
                    var all = document.querySelectorAll('[data-video-lib-tab]');
                    for (var j = 0; j < all.length; j++) {
                        all[j].classList.toggle('active', all[j] === tab);
                    }
                    render();
                });
            })(tabs[i]);
        }
        var scan = document.querySelector('[data-video-scan]');
        if (scan) scan.addEventListener('click', startScan);
    }

    function onPageShown(e) {
        if (!e || e.detail !== PAGE_ID) return;
        loadLibrary(false);
    }

    function init() {
        wire();
        document.addEventListener('soulsync:video-page-shown', onPageShown);
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }
})();
