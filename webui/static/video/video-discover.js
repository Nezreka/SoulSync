/*
 * SoulSync — Video Discover page (isolated, in-app).
 *
 * A browse-everything page for TMDB titles you don't own yet: a cross-fading
 * trending hero, personalized "because you like…" rails, then a deep stack of
 * Netflix-style genre/decade/curated rails — each lazy-loaded on scroll. Every
 * rail has a "See all" that opens it as a paged grid (Load more). A filter bar
 * (kind / genre / decade / sort) opens an arbitrary grid; a "Hide owned" toggle
 * drops titles already in your library. Cards reuse the search card + owned
 * ribbon and open detail via the shared soulsync:video-open-detail event.
 * Self-contained IIFE, no globals.
 */
(function () {
    'use strict';

    var PAGE_ID = 'video-discover';
    var LIST_URL = '/api/video/discover/list';
    var NOW_YEAR = new Date().getFullYear();   // for the 'NEW' (just-released) card flag

    var state = {
        loaded: false, wired: false, mode: 'shelves',
        genres: { movie: [], show: [] }, taste: { movie: [], show: [] },
        myProviders: [],   // saved streaming services -> 'On your streaming services' rail
        io: null,
        hero: { items: [], idx: 0, timer: null },
        cat: { title: '', q: '', page: 1, paginates: true, busy: false, hasMore: false },
        sel: { kind: 'movie', genre: '', decade: '', providers: '', lang: '', sort: 'popularity.desc' },   // Browse panel
    };
    var AUTO = (typeof IntersectionObserver !== 'undefined');   // infinite-scroll capable
    var sentinelVisible = false;
    var listCache = {};   // session cache of /discover/list responses, keyed by URL

    function $(s, r) { return (r || document).querySelector(s); }
    function esc(s) {
        return String(s == null ? '' : s)
            .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
    }
    function hueOf(s) { var h = 0, t = String(s || ''); for (var i = 0; i < t.length; i++) h = (h * 31 + t.charCodeAt(i)) >>> 0; return h % 360; }
    function idMap(list) { var m = {}; (list || []).forEach(function (g) { m[(g.name || '').toLowerCase()] = g.id; }); return m; }
    // Session-memoized GET → JSON for the list endpoint, so revisiting Discover /
    // paging / reopening a category is instant and doesn't re-hit TMDB. (Ownership
    // is re-stamped server-side; caching for a session is fine.)
    function cachedFetch(url) {
        if (listCache[url]) return Promise.resolve(listCache[url]);
        return fetch(url, { headers: { Accept: 'application/json' } })
            .then(function (r) { return r.ok ? r.json() : null; })
            .then(function (d) { if (d) listCache[url] = d; return d; });
    }

    // ── the rail stack (personalized + curated + genre + decade) ──────────────
    // The iconic ranked "Top 10 today" row — daily TMDB trending, rendered with rank numbers.
    var TOP10 = { title: 'Top 10 Today', q: 'key=trending_today&lang=any', ranked: true };
    var CURATED = [
        { title: 'Trending This Week', q: 'key=trending' },
        { title: 'Popular Movies', q: 'key=popular_movies' },
        { title: 'Popular Shows', q: 'key=popular_shows' },
        { title: 'On The Air', q: 'key=on_the_air' },
        { title: 'Top Rated Movies', q: 'key=top_movies' },
        { title: 'Top Rated Shows', q: 'key=top_shows' },
    ];
    // New & noteworthy — date-windowed "new" + the theatrical pipeline.
    var NEW_RAILS = [
        { title: 'New Movies This Month', q: 'kind=movie&release_window=last_30&sort=popularity.desc' },
        { title: 'Fresh on TV', q: 'kind=show&release_window=last_90&sort=popularity.desc' },
        { title: 'In Theaters Now', q: 'key=now_playing' },
        { title: 'Coming Soon', q: 'key=upcoming_movies' },
    ];
    // Mood rails — genre AND-combos + a vote floor so they read as a vibe, not a dump.
    var MOOD_RAILS = [
        { title: 'Feel-Good Favorites', q: 'kind=movie&genre=35,10751&sort=popularity.desc' },      // Comedy + Family
        { title: 'Edge of Your Seat', q: 'kind=movie&genre=53&sort=popularity.desc&vote_count_min=300' }, // Thriller
        { title: 'Mind-Bending', q: 'kind=movie&genre=878,9648&sort=vote_average.desc&vote_count_min=300' }, // Sci-Fi + Mystery
        { title: 'Date Night', q: 'kind=movie&genre=10749,35&sort=popularity.desc' },                // Romance + Comedy
        { title: 'Tearjerkers', q: 'kind=movie&genre=18&sort=vote_average.desc&vote_count_min=500' }, // Drama
        { title: 'Laugh Out Loud', q: 'kind=movie&genre=35&sort=vote_average.desc&vote_count_min=400' }, // Comedy
    ];
    // From the studios — TMDB company ids (stable). A wrong/empty one just drops its rail.
    var STUDIO_RAILS = [
        { title: 'Pixar', q: 'kind=movie&companies=3&sort=primary_release_date.desc' },
        { title: 'Studio Ghibli', q: 'kind=movie&companies=10342&sort=vote_average.desc' },
        { title: 'A24', q: 'kind=movie&companies=41077&sort=primary_release_date.desc' },
        { title: 'Marvel Studios', q: 'kind=movie&companies=420&sort=primary_release_date.desc' },
        { title: 'DreamWorks Animation', q: 'kind=movie&companies=521&sort=popularity.desc' },
    ];
    // Something different — runtime + family slices.
    var DIFFERENT_RAILS = [
        { title: 'Quick Watches (under 90 min)', q: 'kind=movie&max_runtime=90&sort=popularity.desc&vote_count_min=200' },
        { title: 'Epics (3 hrs+)', q: 'kind=movie&min_runtime=180&sort=vote_average.desc&vote_count_min=200' },
        { title: 'Family Movie Night', q: 'kind=movie&genre=10751&sort=popularity.desc' },
    ];
    var GENRE_RAILS = ['Action', 'Adventure', 'Comedy', 'Drama', 'Science Fiction',
        'Thriller', 'Horror', 'Animation', 'Fantasy', 'Romance', 'Documentary', 'Crime'];
    // A thematic colour per genre so the chips read intentional, not random.
    var GENRE_COLORS = {
        'action': '239, 68, 68', 'adventure': '34, 197, 94', 'animation': '168, 85, 247',
        'comedy': '245, 197, 24', 'crime': '120, 113, 108', 'documentary': '20, 184, 166',
        'drama': '96, 165, 250', 'family': '74, 222, 128', 'fantasy': '192, 132, 252',
        'history': '202, 138, 4', 'horror': '220, 38, 38', 'music': '236, 72, 153',
        'mystery': '99, 102, 241', 'romance': '244, 114, 182', 'science fiction': '34, 211, 238',
        'sci-fi & fantasy': '34, 211, 238', 'tv movie': '148, 163, 184', 'thriller': '100, 116, 139',
        'war': '161, 98, 7', 'war & politics': '161, 98, 7', 'western': '180, 130, 80',
        'kids': '74, 222, 128', 'reality': '251, 146, 60', 'soap': '244, 114, 182',
        'talk': '148, 163, 184', 'news': '148, 163, 184',
    };
    var DECADE_RAILS = [
        { title: 'Best of the 2010s', q: 'kind=movie&decade=2010&sort=vote_average.desc' },
        { title: '2000s Favorites', q: 'kind=movie&decade=2000&sort=vote_average.desc' },
        { title: '’90s Classics', q: 'kind=movie&decade=1990&sort=vote_average.desc' },
        { title: 'Retro ’80s', q: 'kind=movie&decade=1980&sort=vote_average.desc' },
    ];
    // Hidden gems — highly-rated titles that aren't blockbusters (vote_average.desc, with the
    // backend's vote_count floor keeping out single-vote noise). Subject to the language filter.
    var GEM_RAILS = [
        { title: 'Hidden Gems', q: 'kind=movie&sort=vote_average.desc' },
        { title: 'Critically Acclaimed Shows', q: 'kind=show&sort=vote_average.desc' },
    ];
    // Dedicated foreign-language rails so non-English titles live HERE rather than
    // leaking into the general genre/decade rails (which respect your language preference).
    var FOREIGN_RAILS = [
        { title: 'Korean Cinema', q: 'kind=movie&sort=popularity.desc&lang=ko' },
        { title: 'Japanese Films', q: 'kind=movie&sort=popularity.desc&lang=ja' },
        { title: 'Spanish-Language', q: 'kind=movie&sort=popularity.desc&lang=es' },
        { title: 'French Cinema', q: 'kind=movie&sort=popularity.desc&lang=fr' },
        { title: 'Hindi Cinema', q: 'kind=movie&sort=popularity.desc&lang=hi' },
    ];

    // The page is organised into a FIXED, authored sequence of groups (each with a header).
    // Static rails are placed into their group here; the async personalized rows fill their
    // group's body when they arrive — so the on-screen order is stable no matter which fetch
    // returns first. The order of the returned array IS the order on screen.
    function buildSections() {
        var gm = idMap(state.genres.movie), gs = idMap(state.genres.show), used = {};
        var taste = [];
        (state.taste.movie || []).slice(0, 3).forEach(function (name) {
            var id = gm[name.toLowerCase()];
            if (id != null) { taste.push({ title: 'Because you like ' + name, q: 'kind=movie&genre=' + id + '&sort=popularity.desc' }); used['m:' + name.toLowerCase()] = 1; }
        });
        (state.taste.show || []).slice(0, 2).forEach(function (name) {
            var id = gs[name.toLowerCase()];
            if (id != null) taste.push({ title: 'More ' + name + ' shows', q: 'kind=show&genre=' + id + '&sort=popularity.desc' });
        });
        var foryou = [];
        if (state.myProviders && state.myProviders.length) {
            foryou.push({ title: 'On your streaming services',
                q: 'kind=movie&providers=' + state.myProviders.join(',') + '&sort=popularity.desc' });
        }
        var genre = [];
        GENRE_RAILS.forEach(function (name) {
            var id = gm[name.toLowerCase()];
            if (id != null && !used['m:' + name.toLowerCase()])
                genre.push({ title: name, q: 'kind=movie&genre=' + id + '&sort=popularity.desc' });
        });
        // foryou/collection/taste are also fed by async loaders (loadForYou/loadGaps/loadMoreLike) —
        // keep those group ids. Order of this array IS the on-screen order.
        return [
            { id: 'foryou', label: 'For you', rails: foryou },
            { id: 'topten', label: 'Top 10 today', rails: [TOP10] },
            { id: 'new', label: 'New & noteworthy', rails: NEW_RAILS },
            { id: 'collection', label: 'Finish your collection', rails: [] },
            { id: 'taste', label: 'More of what you like', rails: taste },
            { id: 'popular', label: 'Trending & popular', rails: CURATED },
            { id: 'mood', label: 'By mood', rails: MOOD_RAILS },
            { id: 'studios', label: 'From the studios', rails: STUDIO_RAILS },
            { id: 'genre', label: 'Browse by genre', rails: genre },
            { id: 'different', label: 'Something different', rails: DIFFERENT_RAILS.concat(GEM_RAILS).concat(DECADE_RAILS).concat(FOREIGN_RAILS) },
        ];
    }

    // ── card (mirrors the search title card: owned ribbon + get button) ───────
    function card(it) {
        var fallback = it.kind === 'movie' ? '🎬' : '📺';
        var img = it.poster
            ? '<img src="' + esc(it.poster) + '" alt="" loading="lazy" decoding="async" ' +
              'onerror="this.outerHTML=\'<div class=&quot;vsr-poster-ph&quot;>' + fallback + '</div>\'">'
            : '<div class="vsr-poster-ph">' + fallback + '</div>';
        var owned = it.library_id != null;
        var ribbon = owned
            ? '<span class="vsr-ribbon vsr-ribbon--owned">In Library</span>'
            : '<span class="vsr-ribbon vsr-ribbon--preview">Preview</span>';
        var rating = it.rating
            ? '<span class="vsr-rating">★ ' + (Math.round(it.rating * 10) / 10) + '</span>' : '';
        // 'NEW' flag for un-owned current-year releases — a quick "just out" signal.
        var fresh = (!owned && it.year && Number(it.year) >= NOW_YEAR)
            ? '<span class="vsr-new">NEW</span>' : '';
        var source = owned ? 'library' : 'tmdb';
        var id = owned ? it.library_id : it.tmdb_id;
        var href = '/video-detail/' + source + '/' + it.kind + '/' + id;
        var sub = [it.year, it.kind === 'movie' ? 'Movie' : 'TV'].filter(Boolean).join(' · ');
        var cb = window.VideoGet ? VideoGet.cardButton({ kind: it.kind, tmdbId: it.tmdb_id,
            libraryId: it.library_id, title: it.title, poster: it.poster, status: it.status, source: source }) : '';
        // 'Not interested' — un-owned cards only (you can't be uninterested in what you own).
        var notInt = (!owned && it.tmdb_id) ? '<button class="vsr-notint" type="button" title="Not interested" ' +
            'aria-label="Not interested" data-ig-kind="' + it.kind + '" data-ig-id="' + it.tmdb_id + '" ' +
            'data-ig-title="' + esc(it.title || '') + '" data-ig-year="' + (it.year || '') + '" ' +
            'data-ig-poster="' + esc(it.poster || '') + '">✕</button>' : '';
        return '<a class="vsr-card' + (owned ? ' vsr-card--owned' : '') + '" href="' + href + '" ' +
            'data-vsr-open="' + it.kind + '" data-vsr-source="' + source + '" data-vsr-id="' + id +
            '" style="--vgm-h:' + hueOf(it.title) + '">' + cb + notInt +
            '<div class="vsr-poster">' + img + ribbon + rating + fresh +
            '<span class="vsr-peek" aria-hidden="true">i</span></div>' +
            '<div class="vsr-info"><span class="vsr-name" title="' + esc(it.title) + '">' + esc(it.title) +
            '</span><span class="vsr-sub">' + esc(sub) + '</span></div></a>';
    }
    // A ranked card (Top 10): a big outlined rank numeral beside the normal poster.
    function rankedCard(it, rank) {
        return '<div class="vsr-ranked"><span class="vsr-rank" aria-hidden="true">' + rank + '</span>' + card(it) + '</div>';
    }
    function hydrateGet(root) { if (window.VideoWatchlist) VideoWatchlist.hydrate(root); }

    // ── 'Not interested' + ignore-list management ─────────────────────────────
    function postIgnore(payload) {
        return fetch('/api/video/discover/ignore', {
            method: 'POST', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload),
        }).then(function (r) { return r.ok ? r.json() : null; });
    }
    function wireNotInterested() {
        if (state._notIntWired) return; state._notIntWired = true;
        document.addEventListener('click', function (e) {
            var b = e.target.closest('.vsr-notint'); if (!b) return;
            e.preventDefault(); e.stopPropagation();
            postIgnore({ action: 'add', kind: b.getAttribute('data-ig-kind'),
                tmdb_id: parseInt(b.getAttribute('data-ig-id'), 10),
                title: b.getAttribute('data-ig-title'),
                year: parseInt(b.getAttribute('data-ig-year'), 10) || null,
                poster: b.getAttribute('data-ig-poster') || null });
            var card = b.closest('.vsr-card');
            if (card) { card.style.transition = 'opacity .2s, transform .2s'; card.style.opacity = '0'; card.style.transform = 'scale(.92)'; setTimeout(function () { card.remove(); }, 200); }
        }, true);
    }
    function igRowHtml(it, btnLabel) {
        return '<button class="vdsc-ig-res" type="button" data-ig-kind="' + it.kind + '" data-ig-id="' + it.tmdb_id +
            '" data-ig-title="' + esc(it.title || '') + '" data-ig-year="' + (it.year || '') + '" data-ig-poster="' + esc(it.poster || '') + '">' +
            (it.poster ? '<img src="' + esc(it.poster) + '" alt="">' : '<span class="vdsc-ig-ph">' + (it.kind === 'movie' ? '🎬' : '📺') + '</span>') +
            '<span class="vdsc-ig-rn">' + esc(it.title || 'Untitled') + (it.year ? ' (' + it.year + ')' : '') + '</span>' +
            '<span class="vdsc-ig-add">' + btnLabel + '</span></button>';
    }
    function renderIgnoreList(ov) {
        var box = ov.querySelector('[data-ig-list]');
        fetch('/api/video/discover/ignore', { headers: { Accept: 'application/json' } })
            .then(function (r) { return r.ok ? r.json() : null; })
            .then(function (d) {
                var items = (d && d.items) || [];
                if (!items.length) {
                    box.innerHTML = '<div class="vdsc-ig-empty"><div class="vdsc-ig-empty-ic">🙈</div>' +
                        '<div>Nothing hidden yet.</div><div class="vdsc-ig-empty-sub">Hover any Discover card and hit ✕, or search above to hide a title.</div></div>';
                    return;
                }
                box.innerHTML = '<div class="vdsc-ig-count">' + items.length + ' hidden</div><div class="vdsc-ig-grid">' +
                    items.map(function (it) {
                        return '<div class="vdsc-ig-card" data-ig-kind="' + it.kind + '" data-ig-id="' + it.tmdb_id + '">' +
                            (it.poster ? '<img class="vdsc-ig-poster" src="' + esc(it.poster) + '" alt="">'
                                       : '<div class="vdsc-ig-poster vdsc-ig-ph">' + (it.kind === 'movie' ? '🎬' : '📺') + '</div>') +
                            '<div class="vdsc-ig-meta"><span class="vdsc-ig-name">' + esc(it.title || 'Untitled') + '</span>' +
                            (it.year ? '<span class="vdsc-ig-yr">' + it.year + '</span>' : '') + '</div>' +
                            '<button class="vdsc-ig-remove" type="button">Un-hide</button></div>';
                    }).join('') + '</div>';
            }).catch(function () { box.innerHTML = '<div class="vdsc-ig-empty">Could not load the list.</div>'; });
    }
    function openIgnoreModal() {
        var ex = document.getElementById('vdsc-ignore-modal'); if (ex) ex.remove();
        var ov = document.createElement('div');
        ov.id = 'vdsc-ignore-modal';
        ov.className = 'vdsc-ig-overlay';
        ov.innerHTML =
            '<div class="vdsc-ig-modal" role="dialog" aria-label="Not interested list">' +
                '<div class="vdsc-ig-head"><div><div class="vdsc-ig-title">Not Interested</div>' +
                    '<div class="vdsc-ig-sub">Titles hidden from Discover — they won\'t show in any rail or recommendation.</div></div>' +
                    '<button class="vdsc-ig-close" type="button" aria-label="Close">✕</button></div>' +
                '<div class="vdsc-ig-search"><input type="text" class="vdsc-ig-input" placeholder="Search a movie or show to hide…">' +
                    '<div class="vdsc-ig-results" data-ig-results></div></div>' +
                '<div class="vdsc-ig-list" data-ig-list><div class="vdsc-ig-empty">Loading…</div></div>' +
            '</div>';
        document.body.appendChild(ov);
        requestAnimationFrame(function () { ov.classList.add('vdsc-ig-in'); });
        var close = function () { ov.classList.remove('vdsc-ig-in'); setTimeout(function () { ov.remove(); }, 180); };
        ov.addEventListener('click', function (e) { if (e.target === ov || e.target.closest('.vdsc-ig-close')) close(); });
        document.addEventListener('keydown', function esc(e) { if (e.key === 'Escape') { close(); document.removeEventListener('keydown', esc); } });

        renderIgnoreList(ov);

        var input = ov.querySelector('.vdsc-ig-input');
        var resBox = ov.querySelector('[data-ig-results]');
        var t = null;
        input.addEventListener('input', function () {
            clearTimeout(t);
            var q = input.value.trim();
            if (q.length < 2) { resBox.innerHTML = ''; return; }
            t = setTimeout(function () {
                fetch('/api/video/search?q=' + encodeURIComponent(q), { headers: { Accept: 'application/json' } })
                    .then(function (r) { return r.ok ? r.json() : null; })
                    .then(function (d) {
                        var items = ((d && d.results) || []).filter(function (x) {
                            return x.tmdb_id && (x.kind === 'movie' || x.kind === 'show');
                        }).slice(0, 8);
                        resBox.innerHTML = items.length ? items.map(function (x) { return igRowHtml(x, '+ Hide'); }).join('')
                            : '<div class="vdsc-ig-nores">No matches</div>';
                    }).catch(function () { resBox.innerHTML = ''; });
            }, 300);
        });
        resBox.addEventListener('click', function (e) {
            var b = e.target.closest('.vdsc-ig-res'); if (!b) return;
            postIgnore({ action: 'add', kind: b.getAttribute('data-ig-kind'),
                tmdb_id: parseInt(b.getAttribute('data-ig-id'), 10), title: b.getAttribute('data-ig-title'),
                year: parseInt(b.getAttribute('data-ig-year'), 10) || null, poster: b.getAttribute('data-ig-poster') || null })
                .then(function () { input.value = ''; resBox.innerHTML = ''; renderIgnoreList(ov); });
        });
        ov.querySelector('[data-ig-list]').addEventListener('click', function (e) {
            var b = e.target.closest('.vdsc-ig-remove'); if (!b) return;
            var c = b.closest('.vdsc-ig-card');
            postIgnore({ action: 'remove', kind: c.getAttribute('data-ig-kind'),
                tmdb_id: parseInt(c.getAttribute('data-ig-id'), 10) })
                .then(function () { c.style.opacity = '0'; c.style.transform = 'scale(.9)';
                    setTimeout(function () { renderIgnoreList(ov); }, 160); });
        });
    }

    // ── hero slideshow ────────────────────────────────────────────────────────
    function loadHero() {
        fetch('/api/video/discover/hero', { headers: { Accept: 'application/json' } })
            .then(function (r) { return r.ok ? r.json() : null; })
            .then(function (d) { state.hero.items = (d && d.items) || []; renderHero(); })
            .catch(function () { /* hero is optional chrome */ });
    }
    function renderHero() {
        var host = $('[data-vdsc-hero]'); if (!host) return;
        var items = state.hero.items;
        if (!items.length) { host.classList.add('hidden'); return; }
        host.classList.remove('hidden');
        host.innerHTML =
            '<div class="vdsc-slides">' + items.map(function (it, i) {
                return '<div class="vdsc-slide' + (i === 0 ? ' vdsc-slide--on' : '') + '" data-vdsc-slide="' + i + '" ' +
                    'style="--vgm-h:' + hueOf(it.title) + ';' + (it.backdrop ? "background-image:url('" + esc(it.backdrop) + "')" : '') + '">' +
                    '<div class="vdsc-slide-scrim"></div></div>';
            }).join('') + '</div>' +
            '<div class="vdsc-hero-body" data-vdsc-hero-body></div>' +
            '<div class="vdsc-dots">' + items.map(function (it, i) {
                return '<button class="vdsc-dot' + (i === 0 ? ' vdsc-dot--on' : '') + '" type="button" data-vdsc-go="' + i + '" aria-label="Slide ' + (i + 1) + '"></button>';
            }).join('') + '</div>';
        state.hero.idx = 0;
        paintHeroBody();
        startHeroTimer();
    }
    function paintHeroBody() {
        var body = $('[data-vdsc-hero-body]'); if (!body) return;
        var it = state.hero.items[state.hero.idx]; if (!it) return;
        var owned = it.library_id != null;
        var source = owned ? 'library' : 'tmdb';
        var id = owned ? it.library_id : it.tmdb_id;
        var pills = [it.kind === 'movie' ? 'Movie' : 'TV', it.year,
            it.rating ? '★ ' + (Math.round(it.rating * 10) / 10) : null,
            owned ? 'In Library' : null].filter(Boolean);
        var hue = hueOf(it.title);
        body.style.setProperty('--vgm-h', hue);
        var pageEl = $('[data-vdsc-page]'); if (pageEl) pageEl.style.setProperty('--vdsc-amb', hue);   // ambient bleed
        body.innerHTML =
            '<div class="vdsc-hero-eyebrow">' + (owned ? 'In your library' : 'Trending now') + '</div>' +
            '<h2 class="vdsc-hero-title">' + esc(it.title) + '</h2>' +
            '<div class="vdsc-hero-pills">' + pills.map(function (p) {
                return '<span class="vdsc-hero-pill">' + esc(p) + '</span>'; }).join('') + '</div>' +
            (it.overview ? '<p class="vdsc-hero-ov">' + esc(it.overview) + '</p>' : '') +
            '<div class="vdsc-hero-actions">' +
            '<button class="discog-submit-btn vdsc-hero-cta" type="button" ' +
            'data-vsr-open="' + it.kind + '" data-vsr-source="' + source + '" data-vsr-id="' + id + '">' +
            '<span class="discog-submit-text">View ' + (it.kind === 'movie' ? 'movie' : 'show') + ' →</span></button>' +
            '<button class="vdsc-hero-trailer" type="button" data-vdsc-trailer ' +
            'data-kind="' + it.kind + '" data-tmdb="' + it.tmdb_id + '" data-title="' + esc(it.title) + '">' +
            '<span class="vdsc-tr-ic" aria-hidden="true">▶</span> Trailer</button>' +
            '</div>';
    }
    function goHero(i) {
        var items = state.hero.items; if (!items.length) return;
        state.hero.idx = (i + items.length) % items.length;
        var slides = document.querySelectorAll('[data-vdsc-slide]');
        for (var s = 0; s < slides.length; s++) slides[s].classList.toggle('vdsc-slide--on', s === state.hero.idx);
        var dots = document.querySelectorAll('[data-vdsc-go]');
        for (var d = 0; d < dots.length; d++) dots[d].classList.toggle('vdsc-dot--on', d === state.hero.idx);
        paintHeroBody();
    }
    function startHeroTimer() {
        stopHeroTimer();
        if (window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches) return;
        if (state.hero.items.length < 2) return;
        state.hero.timer = setInterval(function () { goHero(state.hero.idx + 1); }, 6500);
    }
    function stopHeroTimer() { if (state.hero.timer) { clearInterval(state.hero.timer); state.hero.timer = null; } }

    // ── trailer lightbox (in-app YouTube embed) ───────────────────────────────
    var trailerEl = null, trKey = null;
    function closeTrailer() {
        if (!trailerEl) return;
        var el = trailerEl; trailerEl = null;
        el.classList.remove('vdsc-tr-open');
        document.body.style.removeProperty('overflow');
        if (trKey) { document.removeEventListener('keydown', trKey); trKey = null; }
        setTimeout(function () { if (el.parentNode) el.parentNode.removeChild(el); }, 200);
        startHeroTimer();
    }
    function openTrailer(kind, tmdbId, title) {
        closeTrailer();
        stopHeroTimer();
        var ov = document.createElement('div');
        ov.className = 'vdsc-tr-overlay';
        ov.innerHTML =
            '<div class="vdsc-tr-box" role="dialog" aria-modal="true">' +
                '<button class="vdsc-tr-close" type="button" data-vdsc-tr-close aria-label="Close">&times;</button>' +
                '<div class="vdsc-tr-frame" data-vdsc-tr-frame><div class="loading-spinner"></div></div>' +
                '<div class="vdsc-tr-title">' + esc(title || '') + '</div>' +
            '</div>';
        document.body.appendChild(ov);
        document.body.style.overflow = 'hidden';
        trailerEl = ov;
        requestAnimationFrame(function () { ov.classList.add('vdsc-tr-open'); });
        ov.addEventListener('click', function (e) {
            if (e.target === ov || e.target.closest('[data-vdsc-tr-close]')) closeTrailer();
        });
        trKey = function (e) { if (e.key === 'Escape') closeTrailer(); };
        document.addEventListener('keydown', trKey);
        fetch('/api/video/discover/trailer?kind=' + encodeURIComponent(kind) + '&tmdb_id=' + encodeURIComponent(tmdbId),
              { headers: { Accept: 'application/json' } })
            .then(function (r) { return r.ok ? r.json() : null; })
            .then(function (d) {
                if (!trailerEl) return;
                var frame = trailerEl.querySelector('[data-vdsc-tr-frame]');
                var key = d && d.trailer && d.trailer.key;
                if (!key) { if (frame) frame.innerHTML = '<div class="vdsc-tr-none">No trailer available</div>'; return; }
                if (frame) frame.innerHTML = '<iframe src="https://www.youtube.com/embed/' + encodeURIComponent(key) +
                    '?autoplay=1&rel=0" title="Trailer" frameborder="0" ' +
                    'allow="autoplay; encrypted-media; picture-in-picture" allowfullscreen></iframe>';
            })
            .catch(function () {
                var frame = trailerEl && trailerEl.querySelector('[data-vdsc-tr-frame]');
                if (frame) frame.innerHTML = '<div class="vdsc-tr-none">Couldn’t load trailer</div>';
            });
    }

    // ── "More like <owned title>" rails → the "More of what you like" group (after taste rails) ─
    function loadMoreLike() {
        var gen = state.railGen || 0;
        fetch('/api/video/discover/morelike', { headers: { Accept: 'application/json' } })
            .then(function (r) { return r.ok ? r.json() : null; })
            .then(function (d) {
                if (gen !== (state.railGen || 0)) return;   // a newer rebuild superseded this
                var rails = (d && d.rails) || [];
                var body = $('[data-group-body="taste"]');
                if (!rails.length || !body) return;
                var html = rails.map(function (rl) {
                    return filledShelfHtml(rl.title, (rl.items || []).map(card).join(''), 'vdsc-shelf--ml');
                }).join('');
                body.insertAdjacentHTML('beforeend', html);
                revealGroup('taste');
                staggerWithin(body);
                hydrateGet(body);
            })
            .catch(function () { /* personalization is best-effort */ });
    }

    // ── "What am I missing?" gaps — unfinished franchises + more from directors you own most.
    //    Fills the "Finish your collection" group (async-only; hidden until these arrive). ──
    function loadGaps() {
        var gen = state.railGen || 0;
        fetch('/api/video/discover/gaps', { headers: { Accept: 'application/json' } })
            .then(function (r) { return r.ok ? r.json() : null; })
            .then(function (d) {
                if (gen !== (state.railGen || 0)) return;   // a newer rebuild superseded this
                var rails = (d && d.rails) || [];
                var body = $('[data-group-body="collection"]');
                if (!rails.length || !body) return;
                var html = rails.map(function (rl) {
                    return filledShelfHtml(rl.title, (rl.items || []).map(card).join(''), 'vdsc-shelf--gap');
                }).join('');
                body.insertAdjacentHTML('beforeend', html);
                revealGroup('collection');
                staggerWithin(body);
                hydrateGet(body);
            })
            .catch(function () { /* gaps are best-effort */ });
    }

    // ── "Recommended for you" — one blended wall at the top of the "For you" group ─
    function loadForYou() {
        var gen = state.railGen || 0;
        fetch('/api/video/discover/foryou', { headers: { Accept: 'application/json' } })
            .then(function (r) { return r.ok ? r.json() : null; })
            .then(function (d) {
                if (gen !== (state.railGen || 0)) return;   // a newer rebuild superseded this
                var items = (d && d.items) || [];
                var body = $('[data-group-body="foryou"]');
                if (items.length < 6 || !body) return;
                // afterbegin: sits above the (static) "On your streaming services" rail in the group.
                body.insertAdjacentHTML('afterbegin',
                    filledShelfHtml('Recommended for you', items.map(card).join(''), 'vdsc-shelf--foryou'));
                revealGroup('foryou');
                staggerWithin(body);
                hydrateGet(body);
            })
            .catch(function () { /* best-effort */ });
    }

    // Rebuild the whole rail stack (genre/curated shelves + the prepended personalized rows).
    // Each rebuild bumps railGen; in-flight personalized loaders from a superseded rebuild check
    // it before prepending, so rapid re-toggles can't stack duplicate "Recommended for you" rows.
    function reloadRails() {
        state.railGen = (state.railGen || 0) + 1;
        renderShelves();
        loadMoreLike();
        loadGaps();
        loadForYou();
    }
    // Coalesce rapid chip toggles (e.g. picking 3 services) into a single rebuild.
    var _reloadTimer;
    function reloadRailsSoon() {
        clearTimeout(_reloadTimer);
        _reloadTimer = setTimeout(reloadRails, 350);
    }

    // ── shelves (lazy rails) ──────────────────────────────────────────────────
    function shelfNav(seeAll) {
        return '<div class="vdsc-shelf-nav">' +
            (seeAll ? '<button class="vdsc-seeall" type="button" data-vdsc-seeall>See all</button>' : '') +
            '<button class="vdsc-arrow" type="button" data-vdsc-scroll="-1" aria-label="Scroll left">‹</button>' +
            '<button class="vdsc-arrow" type="button" data-vdsc-scroll="1" aria-label="Scroll right">›</button>' +
        '</div>';
    }
    // A lazy rail (skeleton until scrolled into view, then filled by fillShelf).
    function lazyShelfHtml(sh) {
        var rankCls = sh.ranked ? ' vdsc-shelf--ranked' : '';
        var rankAttr = sh.ranked ? ' data-vdsc-ranked="1"' : '';
        return '<section class="vdsc-shelf' + rankCls + '"' + rankAttr + ' data-vdsc-q="' + esc(sh.q) + '" data-vdsc-title="' + esc(sh.title) + '">' +
            '<div class="vdsc-shelf-head"><h3 class="vdsc-shelf-title">' + esc(sh.title) + '</h3>' + shelfNav(true) + '</div>' +
            '<div class="vdsc-rail" data-vdsc-rail>' +
                '<div class="vdsc-skel">' + Array(8).join('<div class="vdsc-skel-card"></div>') + '</div>' +
            '</div>' +
        '</section>';
    }
    // A pre-filled rail (async personalized rows already carry their items — no skeleton).
    function filledShelfHtml(title, itemsHtml, cls) {
        return '<section class="vdsc-shelf vdsc-shelf--in ' + cls + '" data-vdsc-loaded="1">' +
            '<div class="vdsc-shelf-head"><h3 class="vdsc-shelf-title">' + esc(title) + '</h3>' + shelfNav(false) + '</div>' +
            '<div class="vdsc-rail" data-vdsc-rail>' + itemsHtml + '</div>' +
        '</section>';
    }
    function renderShelves() {
        var host = $('[data-vdsc-shelves]'); if (!host) return;
        var sections = buildSections();
        // A sticky "jump to section" bar (Netflix/Disney pattern) — the page is a deep
        // stack of rails, so quick navigation makes it far easier to browse. Chips for
        // async-only groups (foryou/collection/taste) start hidden, revealed when filled;
        // chips for groups that prune away hide in lockstep (see reveal/pruneGroup).
        var nav = '<nav class="vdsc-jumpnav" data-vdsc-jumpnav aria-label="Jump to section">' +
            sections.map(function (sec) {
                var hideCls = sec.rails.length ? '' : ' hidden';
                return '<button class="vdsc-jump' + hideCls + '" type="button" data-jump="' +
                    sec.id + '">' + esc(sec.label) + '</button>';
            }).join('') + '</nav>';
        host.innerHTML = nav + sections.map(function (sec) {
            var rails = sec.rails.map(lazyShelfHtml).join('');
            // Groups with no static rails (async-only, e.g. gaps) start hidden — revealed when filled.
            var emptyCls = sec.rails.length ? '' : ' vdsc-group--empty';
            return '<section class="vdsc-group' + emptyCls + '" data-group="' + sec.id + '">' +
                '<h2 class="vdsc-group-head">' + esc(sec.label) + '</h2>' +
                '<div class="vdsc-group-body" data-group-body="' + sec.id + '">' + rails + '</div>' +
            '</section>';
        }).join('');
        wireJumpNav(host);
        observeShelves();
    }
    // Smooth-scroll to a group when its jump chip is clicked (delegated, wired once).
    function wireJumpNav(host) {
        var nav = $('[data-vdsc-jumpnav]', host);
        if (!nav || nav.getAttribute('data-wired')) return;
        nav.setAttribute('data-wired', '1');
        nav.addEventListener('click', function (e) {
            var btn = e.target.closest('[data-jump]'); if (!btn) return;
            var g = $('[data-group="' + btn.getAttribute('data-jump') + '"]');
            if (g) { try { g.scrollIntoView({ behavior: 'smooth', block: 'start' }); } catch (x) { g.scrollIntoView(); } }
        });
    }
    // Reveal a group's header once an async loader has put rails in it (+ its jump chip).
    function revealGroup(id) {
        var g = $('[data-group="' + id + '"]');
        if (g) g.classList.remove('vdsc-group--empty');
        var chip = $('[data-jump="' + id + '"]');
        if (chip) chip.classList.remove('hidden');
    }
    // Hide a group whose body has no shelves left (every rail failed / returned nothing) + its chip.
    function pruneGroup(g) {
        if (g && !g.querySelector('.vdsc-shelf')) {
            g.classList.add('vdsc-group--empty');
            var id = g.getAttribute('data-group');
            var chip = id && $('[data-jump="' + id + '"]');
            if (chip) chip.classList.add('hidden');
        }
    }
    function observeShelves() {
        if (state.io) state.io.disconnect();
        var shelves = document.querySelectorAll('.vdsc-shelf');
        if (!('IntersectionObserver' in window)) {
            for (var i = 0; i < shelves.length; i++) fillShelf(shelves[i]);
            return;
        }
        state.io = new IntersectionObserver(function (entries) {
            entries.forEach(function (en) {
                if (en.isIntersecting) { state.io.unobserve(en.target); fillShelf(en.target); }
            });
        }, { rootMargin: '400px 0px' });
        for (var j = 0; j < shelves.length; j++) state.io.observe(shelves[j]);
    }
    function isHideOwned() {
        var h = $('[data-vdsc-hideowned]');
        return !!(h && h.checked);
    }
    // Tag each card with its index so the CSS cascade reveals them one-by-one (not all at once).
    // Capped so a long rail's tail doesn't lag too far behind the head.
    function stagger(rail) {
        if (!rail) return;
        var c = rail.children;
        for (var i = 0; i < c.length; i++) c[i].style.setProperty('--i', i < 14 ? i : 14);
    }
    // Stagger every not-yet-tagged filled rail under a host (used by the prepended
    // personalized rows, which render their cards inline rather than via fillShelf).
    function staggerWithin(host) {
        if (!host) return;
        var rails = host.querySelectorAll('.vdsc-shelf--in .vdsc-rail');
        for (var i = 0; i < rails.length; i++) {
            if (!rails[i].getAttribute('data-stg')) { rails[i].setAttribute('data-stg', '1'); stagger(rails[i]); }
        }
    }
    function fillShelf(shelf) {
        if (!shelf || shelf.getAttribute('data-vdsc-loaded')) return;
        shelf.setAttribute('data-vdsc-loaded', '1');
        var rail = $('[data-vdsc-rail]', shelf);
        var grp = shelf.closest('.vdsc-group');
        var ranked = shelf.getAttribute('data-vdsc-ranked') === '1';
        var hideOwned = isHideOwned();
        // Normal: 2 pages (~40 items). Hiding owned: let the backend page DEEPER and drop
        // owned server-side, so a huge library's rail still fills instead of CSS-hiding to ~nothing.
        // Ranked (Top 10): a single fixed chart — never paged, capped at 10. It must ALSO honour
        // hide-owned at fetch time: the global `.vdsc-hide-owned .vsr-card--owned{display:none}`
        // rule would otherwise hide an owned card while its rank numeral stayed, leaving a gap.
        var q = shelf.getAttribute('data-vdsc-q') +
            (ranked ? (hideOwned ? '&hide_owned=1' : '')
                    : (hideOwned ? '&hide_owned=1' : '&pages=2'));
        cachedFetch(LIST_URL + '?' + q)
            .then(function (d) {
                var items = (d && d.items) || [];
                if (ranked) items = items.slice(0, 10);
                if (!items.length) { shelf.remove(); pruneGroup(grp); return; }   // drop empty shelves
                if (rail) {
                    rail.innerHTML = ranked
                        ? items.map(function (it, i) { return rankedCard(it, i + 1); }).join('')
                        : items.map(card).join('');
                    stagger(rail); hydrateGet(rail);
                }
                shelf.classList.add('vdsc-shelf--in');            // reveal (cards cascade via --i)
            })
            .catch(function () { shelf.remove(); pruneGroup(grp); });
    }

    // ── category / filter grid (paged) ────────────────────────────────────────
    function openCategory(title, q) {
        state.mode = 'grid';
        state.cat = { title: title, q: q, page: 1, paginates: !/key=trending/.test(q), busy: false, hasMore: false };
        $('[data-vdsc-shelves]').classList.add('hidden');
        var hero = $('[data-vdsc-hero]'); if (hero) hero.classList.add('hidden');
        var wrap = $('[data-vdsc-grid-wrap]'); wrap.classList.remove('hidden');
        var ttl = $('[data-vdsc-grid-title]'); if (ttl) ttl.textContent = title;
        $('[data-vdsc-grid]').innerHTML = '';
        try { wrap.scrollIntoView({ behavior: 'smooth', block: 'start' }); } catch (e) { /* ignore */ }
        loadGrid(true);
    }
    function closeCategory() {
        state.mode = 'shelves';
        $('[data-vdsc-grid-wrap]').classList.add('hidden');
        $('[data-vdsc-shelves]').classList.remove('hidden');
        if (state.hero.items.length) { var h = $('[data-vdsc-hero]'); if (h) h.classList.remove('hidden'); }
    }
    function loadGrid(reset) {
        var c = state.cat;
        if (c.busy) return;
        c.busy = true;
        if (!reset) c.page++;                                   // advance to the next page here
        var more = $('[data-vdsc-more]'); if (more && !reset) { more.disabled = true; more.textContent = 'Loading…'; }
        var ld = reset ? $('[data-vdsc-grid-loading]') : $('[data-vdsc-more-loading]');
        if (ld) ld.classList.remove('hidden');
        cachedFetch(LIST_URL + '?' + c.q + '&page=' + c.page)
            .then(function (d) {
                c.busy = false;
                if (ld) ld.classList.add('hidden');
                var items = (d && d.items) || [];
                var grid = $('[data-vdsc-grid]');
                if (grid) { grid.insertAdjacentHTML('beforeend', items.map(card).join('')); hydrateGet(grid); }
                var empty = $('[data-vdsc-grid-empty]');
                if (empty) empty.classList.toggle('hidden', !(reset && !items.length));
                c.hasMore = c.paginates && items.length >= 18;
                // Button is always the reliable control; the sentinel auto-loads on top.
                if (more) { more.textContent = 'Load more'; more.disabled = false; more.classList.toggle('hidden', !c.hasMore); }
                maybeAutoLoad();                                // keep filling while the sentinel stays in view
            })
            .catch(function () {
                c.busy = false;
                if (ld) ld.classList.add('hidden');
                if (more) { more.textContent = 'Load more'; more.disabled = false; }
            });
    }
    // Self-correcting infinite scroll: if the sentinel is still on-screen after a
    // load (short page), pull the next one. rAF lets the observer update first so
    // a fully-cached category doesn't load every page at once.
    function maybeAutoLoad() {
        if (!(state.mode === 'grid' && sentinelVisible && state.cat.hasMore && !state.cat.busy)) return;
        requestAnimationFrame(function () {
            if (state.mode === 'grid' && sentinelVisible && state.cat.hasMore && !state.cat.busy) loadGrid(false);
        });
    }
    function activeChipText(chipset) {
        var c = $('[data-vdsc-chipset="' + chipset + '"] .vdsc-chip--on');
        return (c && c.getAttribute('data-val')) ? c.textContent : '';
    }
    function applyFilter() {
        var s = state.sel;
        var q = ['kind=' + s.kind, 'sort=' + encodeURIComponent(s.sort)];
        var bits = [s.kind === 'show' ? 'Shows' : 'Movies'];
        if (s.genre) { q.push('genre=' + s.genre); var gn = genreName(s.kind, s.genre); if (gn) bits.push(gn); }
        if (s.providers) { q.push('providers=' + s.providers); var pn = activeChipText('providers'); if (pn) bits.push('on ' + pn); }
        if (s.decade) { q.push('decade=' + s.decade); bits.push(s.decade + 's'); }
        // Browse-all is a self-contained search: always send a language so it never silently
        // inherits the rail language preference. 'any' = show every language; a code filters.
        q.push('lang=' + (s.lang || 'any'));
        if (s.lang) { var ln = activeChipText('lang'); if (ln) bits.push(ln); }
        openCategory(bits.join(' · '), q.join('&'));
    }
    function genreName(kind, id) {
        var list = state.genres[kind] || [];
        for (var i = 0; i < list.length; i++) if (String(list[i].id) === String(id)) return list[i].name;
        return '';
    }
    function renderGenreChips() {
        var box = $('[data-vdsc-chipset="genre"]'); if (!box) return;
        box.innerHTML = '<button class="vdsc-chip vdsc-chip--reset vdsc-chip--on" type="button" data-val="">All genres</button>' +
            (state.genres[state.sel.kind] || []).map(function (g) {
                var c = GENRE_COLORS[(g.name || '').toLowerCase()];
                return '<button class="vdsc-chip" type="button" data-val="' + g.id + '"' +
                    (c ? ' style="--c: ' + c + '"' : '') + '>' + esc(g.name) + '</button>';
            }).join('');
        state.sel.genre = '';
    }
    function setActive(box, el, selector, onClass) {
        var all = box.querySelectorAll(selector);
        for (var i = 0; i < all.length; i++) all[i].classList.toggle(onClass, all[i] === el);
    }

    // ── wiring ────────────────────────────────────────────────────────────────
    function wire() {
        if (state.wired) return;
        state.wired = true;
        var page = $('[data-video-subpage="' + PAGE_ID + '"]'); if (!page) return;

        page.addEventListener('click', function (e) {
            var open = e.target.closest('[data-vsr-open]');
            if (open) {
                if (e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;
                e.preventDefault();
                var id = parseInt(open.getAttribute('data-vsr-id'), 10);
                if (isNaN(id)) return;
                document.dispatchEvent(new CustomEvent('soulsync:video-open-detail', {
                    detail: { kind: open.getAttribute('data-vsr-open'), id: id,
                              source: open.getAttribute('data-vsr-source') || 'tmdb' },
                }));
                return;
            }
            var trbtn = e.target.closest('[data-vdsc-trailer]');
            if (trbtn) {
                openTrailer(trbtn.getAttribute('data-kind'), trbtn.getAttribute('data-tmdb'), trbtn.getAttribute('data-title'));
                return;
            }
            var seeall = e.target.closest('[data-vdsc-seeall]');
            if (seeall) {
                var shelf = seeall.closest('.vdsc-shelf');
                if (shelf) openCategory(shelf.getAttribute('data-vdsc-title'), shelf.getAttribute('data-vdsc-q'));
                return;
            }
            var seg = e.target.closest('.vdsc-seg-btn');
            if (seg) {
                var sbox = seg.closest('[data-vdsc-seg]');
                var which = sbox.getAttribute('data-vdsc-seg');
                setActive(sbox, seg, '.vdsc-seg-btn', 'vdsc-seg-btn--on');
                state.sel[which] = seg.getAttribute('data-val');
                if (which === 'kind') renderGenreChips();   // genres differ by kind
                return;
            }
            var chip = e.target.closest('.vdsc-chip');
            if (chip) {
                var cbox = chip.closest('[data-vdsc-chipset]');
                setActive(cbox, chip, '.vdsc-chip', 'vdsc-chip--on');
                state.sel[cbox.getAttribute('data-vdsc-chipset')] = chip.getAttribute('data-val');
                return;
            }
            var arrow = e.target.closest('[data-vdsc-scroll]');
            if (arrow) {
                var rail = $('[data-vdsc-rail]', arrow.closest('.vdsc-shelf'));
                if (rail) rail.scrollBy({ left: parseInt(arrow.getAttribute('data-vdsc-scroll'), 10) * rail.clientWidth * 0.85, behavior: 'smooth' });
                return;
            }
            var dot = e.target.closest('[data-vdsc-go]');
            if (dot) { goHero(parseInt(dot.getAttribute('data-vdsc-go'), 10)); startHeroTimer(); }
        });

        var hero = $('[data-vdsc-hero]');
        if (hero) { hero.addEventListener('mouseenter', stopHeroTimer); hero.addEventListener('mouseleave', startHeroTimer); }

        var igBtn = $('[data-vdsc-ignore-open]'); if (igBtn && !igBtn._wired) { igBtn._wired = 1; igBtn.addEventListener('click', openIgnoreModal); }
        wireNotInterested();
        var apply = $('[data-vdsc-apply]'); if (apply) apply.addEventListener('click', applyFilter);
        var clear = $('[data-vdsc-clear]'); if (clear) clear.addEventListener('click', closeCategory);
        var more = $('[data-vdsc-more]'); if (more) more.addEventListener('click', function () { loadGrid(false); });

        var hide = $('[data-vdsc-hideowned]');
        if (hide) {
            try { if (localStorage.getItem('vdsc_hideowned') === '1') hide.checked = true; } catch (e) { /* ignore */ }
            page.classList.toggle('vdsc-hide-owned', hide.checked);
            hide.addEventListener('change', function () {
                page.classList.toggle('vdsc-hide-owned', hide.checked);
                try { localStorage.setItem('vdsc_hideowned', hide.checked ? '1' : '0'); } catch (e) { /* ignore */ }
                reloadRails();   // rails are server-filtered now (owned + paged deeper) — rebuild
            });
        }
        // Rail language preference — multi-select chips, persisted server-side; rebuilds the rails.
        var langWrap = $('[data-vdsc-langs]');
        if (langWrap) {
            fetch('/api/video/discover/languages', { headers: { Accept: 'application/json' } })
                .then(function (r) { return r.ok ? r.json() : null; })
                .then(function (d) {
                    var set = {}; ((d && d.languages) || ['en']).forEach(function (c) { set[c] = 1; });
                    langWrap.querySelectorAll('.vdsc-lang').forEach(function (b) {
                        b.classList.toggle('vdsc-lang--on', !!set[b.getAttribute('data-lang')]);
                    });
                }).catch(function () { /* default chip state stands */ });
            langWrap.addEventListener('click', function (e) {
                var btn = e.target.closest('.vdsc-lang'); if (!btn) return;
                btn.classList.toggle('vdsc-lang--on');
                var langs = Array.prototype.map.call(langWrap.querySelectorAll('.vdsc-lang--on'),
                    function (b) { return b.getAttribute('data-lang'); });
                if (!langs.length) { btn.classList.add('vdsc-lang--on'); return; }   // never allow empty
                fetch('/api/video/discover/languages', {
                    method: 'POST', headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ languages: langs }),
                }).then(function () { reloadRailsSoon(); }).catch(function () { /* ignore */ });
            });
        }
        // 'My streaming services' preference — multi-select; drives the 'On your services' rail.
        var provWrap = $('[data-vdsc-myprov]');
        if (provWrap && !provWrap._wired) {
            provWrap._wired = 1;
            fetch('/api/video/discover/providers-pref', { headers: { Accept: 'application/json' } })
                .then(function (r) { return r.ok ? r.json() : null; })
                .then(function (d) {
                    var on = {}; ((d && d.providers) || []).forEach(function (c) { on[String(c)] = 1; });
                    state.myProviders = (d && d.providers) || [];
                    provWrap.querySelectorAll('.vdsc-lang').forEach(function (b) {
                        b.classList.toggle('vdsc-lang--on', !!on[b.getAttribute('data-prov')]);
                    });
                }).catch(function () { /* chips stay off */ });
            provWrap.addEventListener('click', function (e) {
                var btn = e.target.closest('.vdsc-lang'); if (!btn) return;
                btn.classList.toggle('vdsc-lang--on');
                var provs = Array.prototype.map.call(provWrap.querySelectorAll('.vdsc-lang--on'),
                    function (b) { return b.getAttribute('data-prov'); });
                state.myProviders = provs;
                fetch('/api/video/discover/providers-pref', {
                    method: 'POST', headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ providers: provs }),
                }).then(function () { reloadRailsSoon(); }).catch(function () { /* ignore */ });
            });
        }
        // Infinite scroll: a sentinel near the grid bottom pulls the next page.
        var sentinel = $('[data-vdsc-sentinel]');
        if (sentinel && AUTO) {
            new IntersectionObserver(function (entries) {
                sentinelVisible = entries[0].isIntersecting;
                maybeAutoLoad();
            }, { rootMargin: '600px 0px' }).observe(sentinel);
        }

        // Hero keyboard nav (←/→) when Discover is the visible view.
        document.addEventListener('keydown', function (e) {
            if (e.key !== 'ArrowLeft' && e.key !== 'ArrowRight') return;
            if (trailerEl || state.mode !== 'shelves' || !state.hero.items.length) return;
            if (!page || page.offsetParent === null) return;                 // Discover not visible
            var tag = e.target && e.target.tagName;
            if (tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT') return;
            e.preventDefault();
            goHero(state.hero.idx + (e.key === 'ArrowRight' ? 1 : -1));
            startHeroTimer();
        });
    }

    function loadMeta() {
        var jget = function (u) { return fetch(u, { headers: { Accept: 'application/json' } })
            .then(function (r) { return r.ok ? r.json() : null; }).catch(function () { return null; }); };
        Promise.all([jget('/api/video/discover/genres'), jget('/api/video/discover/taste'),
                     jget('/api/video/discover/providers-pref')])
            .then(function (res) {
                var g = res[0] || {}, t = res[1] || {};
                state.genres = { movie: g.movie || [], show: g.show || [] };
                state.taste = { movie: t.movie || [], show: t.show || [] };
                state.myProviders = (res[2] && res[2].providers) || [];
                // Genres are a static TMDB endpoint — empty means TMDB isn't set up.
                if (!state.genres.movie.length && !state.genres.show.length) { showEmpty(); return; }
                renderGenreChips();
                renderShelves();
                loadMoreLike();   // prepend personalized 'More like…' rails when ready
                loadGaps();       // prepend 'what am I missing' (franchise + person) gap rails
                loadForYou();     // prepend the blended 'Recommended for you' wall (sits on top)
            });
    }
    function showEmpty() {
        var e = $('[data-vdsc-empty]'); if (e) e.classList.remove('hidden');
        var b = $('[data-video-subpage="' + PAGE_ID + '"] .vdsc-browse'); if (b) b.classList.add('hidden');
        var sh = $('[data-vdsc-shelves]'); if (sh) sh.classList.add('hidden');
        var h = $('[data-vdsc-hero]'); if (h) h.classList.add('hidden');
    }
    function load() {
        if (state.loaded) return;
        state.loaded = true;
        loadHero();
        loadMeta();
    }

    function onShown(e) {
        if (!e) return;
        if (e.detail === PAGE_ID) { wire(); load(); startHeroTimer(); }
        else stopHeroTimer();   // left the page → stop the slideshow
    }
    function init() { document.addEventListener('soulsync:video-page-shown', onShown); }

    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init);
    else init();
})();
