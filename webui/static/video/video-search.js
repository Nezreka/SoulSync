/*
 * SoulSync — Video Search page (isolated, in-app).
 *
 * Debounced multi-search via /api/video/search (movies / shows / people from
 * TMDB). Movie/show results link to the OWNED library detail when we already
 * have them (library_id), otherwise to the TMDB-backed detail. People open the
 * in-app person page. Everything stays inside SoulSync — no external links.
 *
 * Reuses the library card classes (.library-artist-card). Self-contained IIFE,
 * no globals, event-delegated, no inline handlers. Talks only to /api/video/*.
 */
(function () {
    'use strict';

    var PAGE_ID = 'video-search';
    var SEARCH_URL = '/api/video/search';
    var GROUPS = [
        { kind: 'movie', label: 'Movies', icon: '🎬' },
        { kind: 'show', label: 'TV Shows', icon: '📺' },
        { kind: 'person', label: 'People', icon: '👤' },
    ];

    var lastQuery = '';
    var reqSeq = 0;            // guards against out-of-order responses
    var timer = null;
    var wired = false;

    function $(sel) { return document.querySelector(sel); }
    function esc(s) {
        return String(s == null ? '' : s)
            .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
    }
    function show(sel, on) { var n = $(sel); if (n) n.classList.toggle('hidden', !on); }

    // movie/show card — mirrors the library card, plus an owned/preview ribbon.
    function titleCard(it) {
        var fallback = it.kind === 'movie' ? '🎬' : '📺';
        var img = it.poster
            ? '<div class="library-artist-image"><img src="' + esc(it.poster) + '" alt="" loading="lazy" ' +
              'onerror="this.parentNode.innerHTML=\'<div class=&quot;library-artist-image-fallback&quot;>' + fallback + '</div>\'"></div>'
            : '<div class="library-artist-image"><div class="library-artist-image-fallback">' + fallback + '</div></div>';
        var owned = it.library_id != null;
        var ribbon = owned
            ? '<div class="vsr-ribbon vsr-ribbon--owned">In Library</div>'
            : '<div class="vsr-ribbon vsr-ribbon--preview">Preview</div>';
        var meta = [];
        if (it.year) meta.push(String(it.year));
        if (it.rating) meta.push('★ ' + (Math.round(it.rating * 10) / 10));
        // Owned → real library detail; otherwise the TMDB-backed (preview) detail.
        var source = owned ? 'library' : 'tmdb';
        var id = owned ? it.library_id : it.tmdb_id;
        var href = '/video-detail/' + source + '/' + it.kind + '/' + id;
        return '<a class="library-artist-card video-card--clickable vsr-card" href="' + href + '" ' +
            'data-vsr-open="' + it.kind + '" data-vsr-source="' + source + '" data-vsr-id="' + id + '">' +
            img + ribbon +
            '<div class="library-artist-info">' +
            '<h3 class="library-artist-name" title="' + esc(it.title) + '">' + esc(it.title) + '</h3>' +
            '<div class="library-artist-stats"><span class="library-artist-stat">' +
            esc(meta.join(' · ')) + '</span></div></div></a>';
    }

    function personCard(it) {
        var img = it.poster
            ? '<div class="library-artist-image vsr-person-img"><img src="' + esc(it.poster) + '" alt="" loading="lazy" ' +
              'onerror="this.parentNode.innerHTML=\'<div class=&quot;library-artist-image-fallback&quot;>👤</div>\'"></div>'
            : '<div class="library-artist-image vsr-person-img"><div class="library-artist-image-fallback">👤</div></div>';
        var sub = it.known_for ? it.known_for : (it.department || '');
        return '<a class="library-artist-card video-card--clickable vsr-card vsr-card--person" href="#" ' +
            'data-vsr-open="person" data-vsr-id="' + it.tmdb_id + '">' +
            img +
            '<div class="library-artist-info">' +
            '<h3 class="library-artist-name" title="' + esc(it.title) + '">' + esc(it.title) + '</h3>' +
            '<div class="library-artist-stats"><span class="library-artist-stat">' +
            esc(sub) + '</span></div></div></a>';
    }

    function render(results) {
        var host = $('[data-video-search-results]');
        if (!host) return;
        var any = results && results.length;
        show('[data-video-search-hint]', false);
        show('[data-video-search-empty]', !any);
        if (!any) { host.innerHTML = ''; return; }

        var html = '';
        GROUPS.forEach(function (g) {
            var items = results.filter(function (r) { return r.kind === g.kind; });
            if (!items.length) return;
            html += '<div class="vsr-group"><h2 class="vsr-group-title">' +
                '<span class="vsr-group-ic" aria-hidden="true">' + g.icon + '</span>' + g.label +
                '<span class="vsr-group-count">' + items.length + '</span></h2>' +
                '<div class="vsr-grid">' +
                items.map(g.kind === 'person' ? personCard : titleCard).join('') +
                '</div></div>';
        });
        host.innerHTML = html;
    }

    function runSearch(q) {
        var seq = ++reqSeq;
        show('[data-video-search-loading]', true);
        fetch(SEARCH_URL + '?q=' + encodeURIComponent(q), { headers: { 'Accept': 'application/json' } })
            .then(function (r) { return r.ok ? r.json() : null; })
            .then(function (d) {
                if (seq !== reqSeq) return;           // a newer query superseded this one
                show('[data-video-search-loading]', false);
                render(d && d.results ? d.results : []);
            })
            .catch(function () {
                if (seq !== reqSeq) return;
                show('[data-video-search-loading]', false);
                render([]);
            });
    }

    function onInput(val) {
        var q = (val || '').trim();
        lastQuery = q;
        if (timer) clearTimeout(timer);
        if (!q) {
            reqSeq++;                                 // cancel any in-flight render
            show('[data-video-search-loading]', false);
            show('[data-video-search-empty]', false);
            show('[data-video-search-hint]', true);
            var host = $('[data-video-search-results]'); if (host) host.innerHTML = '';
            return;
        }
        timer = setTimeout(function () { runSearch(q); }, 320);
    }

    function openCard(card) {
        var kind = card.getAttribute('data-vsr-open');
        var id = parseInt(card.getAttribute('data-vsr-id'), 10);
        if (isNaN(id)) return;
        if (kind === 'person') {
            document.dispatchEvent(new CustomEvent('soulsync:video-open-detail',
                { detail: { kind: 'person', id: id, source: 'tmdb' } }));
        } else {
            document.dispatchEvent(new CustomEvent('soulsync:video-open-detail',
                { detail: { kind: kind, id: id, source: card.getAttribute('data-vsr-source') || 'tmdb' } }));
        }
    }

    function wire() {
        if (wired) return;
        wired = true;
        var input = $('[data-video-search-input]');
        if (input) input.addEventListener('input', function () { onInput(input.value); });

        var results = $('[data-video-search-results]');
        if (results) {
            results.addEventListener('click', function (e) {
                if (e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;
                var card = e.target.closest('[data-vsr-open]');
                if (!card || !results.contains(card)) return;
                e.preventDefault();
                openCard(card);
            });
        }
    }

    function onPageShown(e) {
        if (!e || e.detail !== PAGE_ID) return;
        wire();
        var input = $('[data-video-search-input]');
        if (input) { try { input.focus(); } catch (err) { /* ignore */ } }
    }

    function init() {
        document.addEventListener('soulsync:video-page-shown', onPageShown);
    }

    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init);
    else init();
})();
