/*
 * SoulSync — Video Studio page (isolated, in-app).
 *
 * Drill-in for a production company / studio (from a search result or a title's
 * studio chip). A studio isn't a movie or a show — it's a *collection of films* —
 * so it gets its own layout: a logo/about header + a paged film grid (newest
 * first) where owned copies are marked and every card links back into SoulSync
 * (the owned library detail when we have it, else the TMDB-backed preview). No
 * external links.
 *
 * Opened by soulsync:video-open-detail {kind:'studio', id, source:'tmdb'};
 * video-side.js navigates to the studio subpage and this loads + renders.
 * Self-contained IIFE, no globals, event-delegated.
 */
(function () {
    'use strict';

    var DETAIL_URL = '/api/video/studio/';          // + id
    var MOVIES_URL = '/api/video/studio/';          // + id + '/movies'

    var currentId = null;       // studio being viewed (also the request guard)
    var studio = null;          // detail payload
    var sort = 'primary_release_date.desc';
    var page = 0;               // last page loaded
    var totalPages = 0;
    var loadingMore = false;
    var observer = null;

    function esc(s) {
        return String(s == null ? '' : s)
            .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
    }
    function root() { return document.querySelector('[data-video-studio]'); }
    function q(sel) { var r = root(); return r ? r.querySelector(sel) : null; }
    function setText(sel, t) { var n = q(sel); if (n) n.textContent = t || ''; }
    function showLoading(on) { var l = q('[data-vst-loading]'); if (l) l.hidden = !on; }

    // A film in the studio's catalog → the same poster card the search/person grids
    // use, so hover/owned-ribbon/get-button all behave identically.
    function filmCard(m) {
        var img = m.poster
            ? '<img src="' + esc(m.poster) + '" alt="" loading="lazy" ' +
              'onerror="this.outerHTML=\'<div class=&quot;vsr-poster-ph&quot;>🎬</div>\'">'
            : '<div class="vsr-poster-ph">🎬</div>';
        var owned = m.library_id != null;
        var ribbon = owned ? '<span class="vsr-ribbon vsr-ribbon--owned">In Library</span>'
            : '<span class="vsr-ribbon vsr-ribbon--preview">Preview</span>';
        var rating = m.rating
            ? '<span class="vsr-rating">★ ' + (Math.round(m.rating * 10) / 10) + '</span>' : '';
        var source = owned ? 'library' : 'tmdb';
        var id = owned ? m.library_id : m.tmdb_id;
        var href = '/video-detail/' + source + '/movie/' + id;
        var cb = window.VideoGet ? VideoGet.cardButton({ kind: 'movie', tmdbId: m.tmdb_id,
            libraryId: m.library_id, title: m.title, poster: m.poster, status: m.status, source: source }) : '';
        return '<a class="vsr-card" href="' + href + '" ' +
            'data-vst-open="movie" data-vst-source="' + source + '" data-vst-cid="' + id + '">' + cb +
            '<div class="vsr-poster">' + img + ribbon + rating +
            '<span class="vsr-peek" aria-hidden="true">i</span></div>' +
            '<div class="vsr-info"><span class="vsr-name" title="' + esc(m.title) + '">' + esc(m.title) +
            '</span><span class="vsr-sub">' + esc(m.year || '') + '</span></div></a>';
    }

    function renderHeader(d) {
        setText('[data-vst-name]', d.name || 'Studio');
        var logo = q('[data-vst-logo]'), ph = q('[data-vst-logo-ph]');
        if (logo && ph) {
            if (d.logo) {
                logo.src = d.logo; logo.hidden = false; ph.hidden = true;
                logo.onerror = function () { logo.hidden = true; ph.hidden = false; };
            } else { logo.hidden = true; ph.hidden = false; }
        }
        var meta = [];
        if (d.headquarters) meta.push(esc(d.headquarters));
        if (d.origin_country) meta.push(esc(d.origin_country));
        var m = q('[data-vst-meta]');
        if (m) m.innerHTML = meta.map(function (x) { return '<span>' + x + '</span>'; }).join('');
        var about = q('[data-vst-about]');
        if (about) {
            if (d.description) { about.textContent = d.description; about.hidden = false; }
            else { about.hidden = true; about.textContent = ''; }
        }
        var acts = q('[data-vst-actions]');
        if (acts) {
            // Homepage is the studio's own site — the one intentional external link,
            // opened in a new tab with rel=noopener (matches the person page's homepage chip).
            acts.innerHTML = d.homepage
                ? '<a class="vst-site" href="' + esc(d.homepage) + '" target="_blank" rel="noopener noreferrer">Official site ↗</a>'
                : '';
        }
    }

    function setCount(total) {
        var n = q('[data-vst-count]');
        if (n) n.textContent = total ? ('· ' + total) : '';
    }

    function appendFilms(list) {
        var grid = q('[data-vst-films]');
        if (!grid) return;
        grid.insertAdjacentHTML('beforeend', (list || []).map(filmCard).join(''));
        if (window.VideoWatchlist) VideoWatchlist.hydrate(grid);
    }

    function fetchPage(nextPage) {
        var id = currentId;
        loadingMore = true;
        var more = q('[data-vst-more]'); if (more && nextPage > 1) more.hidden = false;
        return fetch(MOVIES_URL + id + '/movies?page=' + nextPage + '&sort=' + encodeURIComponent(sort),
            { headers: { 'Accept': 'application/json' } })
            .then(function (r) { return r.ok ? r.json() : null; })
            .then(function (d) {
                if (currentId !== id) return;                      // navigated away mid-flight
                if (more) more.hidden = true;
                loadingMore = false;
                if (!d || !d.success) return;
                page = d.page || nextPage;
                totalPages = d.total_pages || 0;
                setCount(d.total_results || 0);
                appendFilms(d.results || []);
                var empty = q('[data-vst-films-empty]');
                if (empty) empty.hidden = !(page === 1 && (!d.results || !d.results.length));
                observeSentinel();                                 // (re)arm for the next page
            })
            .catch(function () { loadingMore = false; if (more) more.hidden = true; });
    }

    function observeSentinel() {
        var sentinel = q('[data-vst-sentinel]');
        if (!sentinel) return;
        var hasMore = totalPages && page < totalPages;
        sentinel.hidden = !hasMore;
        if (!observer) {
            observer = new IntersectionObserver(function (entries) {
                if (entries[0].isIntersecting && !loadingMore && totalPages && page < totalPages) {
                    fetchPage(page + 1);
                }
            }, { rootMargin: '600px 0px' });
        }
        observer.disconnect();
        if (hasMore) observer.observe(sentinel);
    }

    function resetGrid() {
        var grid = q('[data-vst-films]'); if (grid) grid.innerHTML = '';
        page = 0; totalPages = 0; loadingMore = false;
        if (observer) observer.disconnect();
        var empty = q('[data-vst-films-empty]'); if (empty) empty.hidden = true;
    }

    function load(id) {
        if (!root()) return;
        currentId = id;
        studio = null;
        sort = 'primary_release_date.desc';
        var sel = q('[data-vst-sort]'); if (sel) sel.value = sort;
        setText('[data-vst-name]', '');
        var m = q('[data-vst-meta]'); if (m) m.innerHTML = '';
        var about = q('[data-vst-about]'); if (about) { about.hidden = true; about.textContent = ''; }
        var acts = q('[data-vst-actions]'); if (acts) acts.innerHTML = '';
        setCount(0);
        resetGrid();
        showLoading(true);
        fetch(DETAIL_URL + id, { headers: { 'Accept': 'application/json' } })
            .then(function (r) { return r.ok ? r.json() : null; })
            .then(function (d) {
                showLoading(false);
                if (currentId !== id) return;
                if (!d || !d.success || !d.studio) { setText('[data-vst-name]', 'Not found'); return; }
                studio = d.studio;
                renderHeader(d.studio);
                // The detail call already carries the first page of films — paint it
                // without a second round-trip, then let scroll drive the rest.
                var mv = d.movies || {};
                page = mv.page || 1;
                totalPages = mv.total_pages || 0;
                setCount(mv.total_results || 0);
                appendFilms(mv.results || []);
                var empty = q('[data-vst-films-empty]');
                if (empty) empty.hidden = !(!mv.results || !mv.results.length);
                observeSentinel();
            })
            .catch(function () { showLoading(false); if (currentId === id) setText('[data-vst-name]', 'Could not load'); });
    }

    function reSort() {
        if (!currentId) return;
        resetGrid();
        fetchPage(1);
    }

    function onOpen(e) {
        if (!e || !e.detail || e.detail.kind !== 'studio') return;
        load(e.detail.id);
    }

    function onClick(e) {
        var r = root(); if (!r) return;
        var card = e.target.closest('[data-vst-open]');
        if (card && r.contains(card)) {
            if (e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;
            e.preventDefault();
            var id = parseInt(card.getAttribute('data-vst-cid'), 10);
            if (isNaN(id)) return;
            document.dispatchEvent(new CustomEvent('soulsync:video-open-detail', {
                detail: { kind: card.getAttribute('data-vst-open'), id: id,
                          source: card.getAttribute('data-vst-source') || 'tmdb' },
            }));
        }
    }

    function init() {
        document.addEventListener('soulsync:video-open-detail', onOpen);
        document.addEventListener('click', onClick);
        var sel = document.querySelector('[data-vst-sort]');
        if (sel) sel.addEventListener('change', function () { sort = sel.value; reSort(); });
    }

    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init);
    else init();
})();
