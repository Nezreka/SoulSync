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
    var trendingCache = null;  // null = not fetched; [] = fetched/empty
    var lastChannel = null;    // resolved YouTube channel awaiting a Follow

    function $(sel) { return document.querySelector(sel); }
    function esc(s) {
        return String(s == null ? '' : s)
            .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
    }
    function show(sel, on) { var n = $(sel); if (n) n.classList.toggle('hidden', !on); }

    // Netflix-style poster card with owned/preview ribbon + hover affordance.
    function titleCard(it) {
        var fallback = it.kind === 'movie' ? '🎬' : '📺';
        var img = it.poster
            ? '<img src="' + esc(it.poster) + '" alt="" loading="lazy" ' +
              'onerror="this.outerHTML=\'<div class=&quot;vsr-poster-ph&quot;>' + fallback + '</div>\'">'
            : '<div class="vsr-poster-ph">' + fallback + '</div>';
        var owned = it.library_id != null;
        var ribbon = owned
            ? '<span class="vsr-ribbon vsr-ribbon--owned">In Library</span>'
            : '<span class="vsr-ribbon vsr-ribbon--preview">Preview</span>';
        var rating = it.rating
            ? '<span class="vsr-rating">★ ' + (Math.round(it.rating * 10) / 10) + '</span>' : '';
        // Owned → real library detail; otherwise the TMDB-backed (preview) detail.
        var source = owned ? 'library' : 'tmdb';
        var id = owned ? it.library_id : it.tmdb_id;
        var href = '/video-detail/' + source + '/' + it.kind + '/' + id;
        var sub = [it.year, it.kind === 'movie' ? 'Movie' : 'TV'].filter(Boolean).join(' · ');
        var cb = window.VideoGet ? VideoGet.cardButton({ kind: it.kind, tmdbId: it.tmdb_id,
            libraryId: it.library_id, title: it.title, poster: it.poster, status: it.status, source: source }) : '';
        return '<a class="vsr-card" href="' + href + '" ' +
            'data-vsr-open="' + it.kind + '" data-vsr-source="' + source + '" data-vsr-id="' + id + '">' + cb +
            '<div class="vsr-poster">' + img + ribbon + rating +
            '<span class="vsr-peek" aria-hidden="true">i</span></div>' +
            '<div class="vsr-info"><span class="vsr-name" title="' + esc(it.title) + '">' + esc(it.title) +
            '</span><span class="vsr-sub">' + esc(sub) + '</span></div></a>';
    }

    function personCard(it) {
        var img = it.poster
            ? '<img src="' + esc(it.poster) + '" alt="" loading="lazy" ' +
              'onerror="this.outerHTML=\'<div class=&quot;vsr-poster-ph&quot;>👤</div>\'">'
            : '<div class="vsr-poster-ph">👤</div>';
        var sub = it.known_for ? it.known_for : (it.department || '');
        var cb = window.VideoGet ? VideoGet.cardButton({ kind: 'person', tmdbId: it.tmdb_id,
            title: it.title, poster: it.poster }) : '';
        return '<a class="vsr-card vsr-card--person" href="#" ' +
            'data-vsr-open="person" data-vsr-id="' + it.tmdb_id + '">' + cb +
            '<div class="vsr-poster">' + img + '</div>' +
            '<div class="vsr-info vsr-info--center"><span class="vsr-name" title="' + esc(it.title) + '">' +
            esc(it.title) + '</span><span class="vsr-sub">' + esc(sub) + '</span></div></a>';
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
        if (window.VideoWatchlist) VideoWatchlist.hydrate(host);
    }

    // Idle state: a "Trending this week" rail so the page isn't a blank box.
    function renderTrending() {
        var host = $('[data-video-search-results]');
        if (!host || !trendingCache || !trendingCache.length) return;
        show('[data-video-search-hint]', false);
        show('[data-video-search-empty]', false);
        host.innerHTML = '<div class="vsr-group"><h2 class="vsr-group-title">' +
            '<span class="vsr-group-ic" aria-hidden="true">🔥</span>Trending this week</h2>' +
            '<div class="vsr-grid">' + trendingCache.map(titleCard).join('') + '</div></div>';
        if (window.VideoWatchlist) VideoWatchlist.hydrate(host);
    }
    function loadTrending() {
        if (trendingCache !== null) { if (!lastQuery) renderTrending(); return; }
        fetch('/api/video/trending', { headers: { 'Accept': 'application/json' } })
            .then(function (r) { return r.ok ? r.json() : null; })
            .then(function (d) {
                trendingCache = (d && d.results) ? d.results : [];
                if (!lastQuery) renderTrending();
            })
            .catch(function () { trendingCache = []; });
    }
    function showIdle() {
        if (trendingCache && trendingCache.length) { renderTrending(); return; }
        show('[data-video-search-empty]', false);
        show('[data-video-search-hint]', true);
        var host = $('[data-video-search-results]'); if (host) host.innerHTML = '';
        loadTrending();
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

    // A pasted YouTube channel link → resolve + render a Follow chip instead of
    // a normal title search (the obscure-channel entry point).
    function runChannel(ref) {
        var seq = ++reqSeq;
        show('[data-video-search-loading]', true);
        VideoYoutube.resolve(ref).then(function (d) {
            if (seq !== reqSeq) return;
            show('[data-video-search-loading]', false);
            show('[data-video-search-hint]', false);
            var host = $('[data-video-search-results]'); if (!host) return;
            if (!d || !d.success || !d.channel) {
                show('[data-video-search-empty]', false);
                host.innerHTML = '<div class="vsr-group"><div class="vyt-miss">' +
                    'Couldn’t read that channel. Paste a channel link like ' +
                    '<code>youtube.com/@handle</code>.</div></div>';
                return;
            }
            lastChannel = d.channel;
            show('[data-video-search-empty]', false);
            host.innerHTML = '<div class="vsr-group"><h2 class="vsr-group-title">' +
                '<span class="vsr-group-ic" aria-hidden="true">▶</span>YouTube channel</h2>' +
                '<div class="vyt-search">' + VideoYoutube.searchCard(d.channel, d.following) + '</div></div>';
        }).catch(function () {
            if (seq !== reqSeq) return;
            show('[data-video-search-loading]', false);
        });
    }

    function onInput(val) {
        var q = (val || '').trim();
        lastQuery = q;
        if (timer) clearTimeout(timer);
        if (!q) {
            reqSeq++;                                 // cancel any in-flight render
            show('[data-video-search-loading]', false);
            showIdle();                               // back to the trending rail
            return;
        }
        if (window.VideoYoutube && VideoYoutube.isChannelRef(q)) {
            timer = setTimeout(function () { runChannel(q); }, 360);
            return;
        }
        timer = setTimeout(function () { runSearch(q); }, 320);
    }

    // Follow / un-follow the resolved channel chip.
    function toggleFollow(btn) {
        if (!lastChannel) return;
        var on = btn.classList.contains('vyt-follow--on');
        btn.disabled = true;
        var done = function () { btn.disabled = false; document.dispatchEvent(new CustomEvent('soulsync:video-wishlist-changed')); };
        if (on) {
            VideoYoutube.unfollow(lastChannel.youtube_id).then(function () {
                btn.classList.remove('vyt-follow--on'); btn.innerHTML = '+ Follow'; done();
            }).catch(function () { btn.disabled = false; });
        } else {
            VideoYoutube.follow(lastChannel).then(function (d) {
                if (d && d.success) {
                    btn.classList.add('vyt-follow--on'); btn.innerHTML = '✓ Following';
                    if (typeof showToast === 'function')
                        showToast('Following ' + lastChannel.title + ' · ' + (d.added_videos || 0) + ' videos added', 'success');
                }
                done();
            }).catch(function () { btn.disabled = false; });
        }
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
                var fb = e.target.closest('[data-vyt-follow]');
                if (fb && results.contains(fb)) { e.preventDefault(); toggleFollow(fb); return; }
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
        if (!lastQuery) loadTrending();               // fill the idle page
    }

    function init() {
        document.addEventListener('soulsync:video-page-shown', onPageShown);
    }

    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init);
    else init();
})();
