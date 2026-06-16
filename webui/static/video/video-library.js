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

        // Contextual overlay control, injected inside the positioned poster box:
        //   airing show -> watchlist eye (monitor for new episodes)
        //   movie / ended show -> "get" download symbol (opens the detail modal)
        var ctrl = '';
        if (kind === 'movie') {
            ctrl = window.VideoGet ? VideoGet.btn({ kind: 'movie', source: 'library', openId: it.id, title: it.title }) : '';
        } else if (kind === 'show') {
            var airing = !window.VideoGet || VideoGet.isAiring(it.status);
            if (airing && it.tmdb_id && window.VideoWatchlist) {
                ctrl = VideoWatchlist.btn({
                    kind: 'show', tmdbId: it.tmdb_id, title: it.title,
                    poster: it.has_poster ? ('/api/video/poster/show/' + it.id) : '', libraryId: it.id
                });
            } else if (window.VideoGet) {
                ctrl = VideoGet.btn({ kind: 'show', source: 'library', openId: it.id, title: it.title });
            }
        }
        if (ctrl) img = img.replace(/<\/div>$/, ctrl + '</div>');

        var badge = '';
        if (kind === 'movie') {
            var rl = resLabel(it.resolution);
            if (rl) badge = '<div class="video-card-badge">' + rl + '</div>';
        }

        var meta = [];
        if (it.year) meta.push(String(it.year));
        if (kind === 'movie') meta.push(it.has_file ? 'Owned' : 'Wanted');
        else meta.push((it.owned_count || 0) + '/' + (it.episode_count || 0) + ' eps');

        // A REAL link (like the music artist cards) so reload / new-tab / Back all
        // work; the click handler intercepts plain left-clicks into SPA nav.
        var href = '/video-detail/library/' + kind + '/' + it.id;
        var hook = ' href="' + href + '" data-video-card-open="' + kind + '" data-video-card-id="' + it.id + '"';
        return '<a class="library-artist-card video-card--clickable"' + hook + '>' + img + badge +
            '<div class="library-artist-info">' +
            '<h3 class="library-artist-name" title="' + esc(it.title) + '">' + esc(it.title) + '</h3>' +
            '<div class="library-artist-stats"><span class="library-artist-stat">' +
            esc(meta.join(' · ')) + '</span></div></div></a>';
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
        // #watchlist: paint the follow eye on shows already on the watchlist.
        if (grid && kind === 'show' && window.VideoWatchlist) VideoWatchlist.hydrate(grid);
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
        // Card click → drill into the detail page (event-delegated on the grid).
        var grid = $('[data-video-lib-grid]');
        if (grid) {
            grid.addEventListener('click', function (e) {
                // Let modified clicks (new tab / window) use the real href.
                if (e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;
                var card = e.target.closest('[data-video-card-open]');
                if (!card || !grid.contains(card)) return;
                e.preventDefault();
                document.dispatchEvent(new CustomEvent('soulsync:video-open-detail', {
                    detail: { kind: card.getAttribute('data-video-card-open'),
                              id: parseInt(card.getAttribute('data-video-card-id'), 10), source: 'library' },
                }));
            });
        }
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

    // Gate the library/scan when no video server (Plex/Jellyfin) is connected —
    // nothing breaks, the user is just told what to do.
    function ensureServerBanner() {
        var content = document.querySelector('#video-library-page .library-content');
        if (!content) return null;
        var b = content.querySelector('[data-video-noserver]');
        if (!b) {
            b = document.createElement('div');
            b.setAttribute('data-video-noserver', '');
            b.className = 'video-noserver hidden';
            b.innerHTML = 'No video server connected. Go to <strong>Settings &rarr; Video Source</strong> ' +
                'and connect Plex or Jellyfin to scan and browse your video library.';
            content.insertBefore(b, content.firstChild);
        }
        return b;
    }
    function checkServer() {
        fetch('/api/video/server', { headers: { 'Accept': 'application/json' } })
            .then(function (r) { return r.ok ? r.json() : null; })
            .then(function (d) {
                var ok = !!(d && d.server), banner = ensureServerBanner();
                if (banner) banner.classList.toggle('hidden', ok);
                var scan = document.querySelector('[data-video-scan-mode]');
                if (scan) {
                    scan.disabled = !ok;
                    scan.style.opacity = ok ? '' : '0.45';
                    scan.title = ok ? 'Scan the media server' : 'Connect Plex or Jellyfin in Settings first';
                }
            })
            .catch(function () { /* ignore */ });
    }

    function onPageShown(e) {
        if (!e || e.detail !== PAGE_ID) return;
        checkServer();
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
