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

    var state = {
        loaded: false, wired: false, mode: 'shelves',
        genres: { movie: [], show: [] }, taste: { movie: [], show: [] },
        io: null,
        hero: { items: [], idx: 0, timer: null },
        cat: { title: '', q: '', page: 1, paginates: true, busy: false, hasMore: false },
        sel: { kind: 'movie', genre: '', decade: '', providers: '', sort: 'popularity.desc' },   // Browse panel
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
    var CURATED = [
        { title: 'Trending This Week', q: 'key=trending' },
        { title: 'Popular Movies', q: 'key=popular_movies' },
        { title: 'Popular Shows', q: 'key=popular_shows' },
        { title: 'In Theaters Now', q: 'key=now_playing' },
        { title: 'Coming Soon', q: 'key=upcoming_movies' },
        { title: 'On The Air', q: 'key=on_the_air' },
        { title: 'Top Rated Movies', q: 'key=top_movies' },
        { title: 'Top Rated Shows', q: 'key=top_shows' },
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
        { title: 'Best of the 2010s', q: 'kind=movie&decade=2010&sort=vote_average.desc&lang=en' },
        { title: '2000s Favorites', q: 'kind=movie&decade=2000&sort=vote_average.desc&lang=en' },
        { title: '’90s Classics', q: 'kind=movie&decade=1990&sort=vote_average.desc&lang=en' },
        { title: 'Retro ’80s', q: 'kind=movie&decade=1980&sort=vote_average.desc&lang=en' },
    ];
    // Dedicated foreign-language rails so non-English titles live HERE rather than
    // leaking into the general genre/decade rails (which are pinned to lang=en).
    var FOREIGN_RAILS = [
        { title: 'Korean Cinema', q: 'kind=movie&sort=popularity.desc&lang=ko' },
        { title: 'Japanese Films', q: 'kind=movie&sort=popularity.desc&lang=ja' },
        { title: 'Spanish-Language', q: 'kind=movie&sort=popularity.desc&lang=es' },
        { title: 'French Cinema', q: 'kind=movie&sort=popularity.desc&lang=fr' },
        { title: 'Hindi Cinema', q: 'kind=movie&sort=popularity.desc&lang=hi' },
    ];

    function buildShelfList() {
        var out = [], used = {};
        var gm = idMap(state.genres.movie), gs = idMap(state.genres.show);
        // personalized first — seeded from what you actually own
        (state.taste.movie || []).slice(0, 3).forEach(function (name) {
            var id = gm[name.toLowerCase()];
            if (id != null) { out.push({ title: 'Because you like ' + name, q: 'kind=movie&genre=' + id + '&sort=popularity.desc&lang=en' }); used['m:' + name.toLowerCase()] = 1; }
        });
        (state.taste.show || []).slice(0, 2).forEach(function (name) {
            var id = gs[name.toLowerCase()];
            if (id != null) { out.push({ title: 'More ' + name + ' shows', q: 'kind=show&genre=' + id + '&sort=popularity.desc&lang=en' }); }
        });
        out = out.concat(CURATED);
        GENRE_RAILS.forEach(function (name) {
            var id = gm[name.toLowerCase()];
            if (id != null && !used['m:' + name.toLowerCase()])
                out.push({ title: name, q: 'kind=movie&genre=' + id + '&sort=popularity.desc&lang=en' });
        });
        return out.concat(DECADE_RAILS).concat(FOREIGN_RAILS);
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
        var source = owned ? 'library' : 'tmdb';
        var id = owned ? it.library_id : it.tmdb_id;
        var href = '/video-detail/' + source + '/' + it.kind + '/' + id;
        var sub = [it.year, it.kind === 'movie' ? 'Movie' : 'TV'].filter(Boolean).join(' · ');
        var cb = window.VideoGet ? VideoGet.cardButton({ kind: it.kind, tmdbId: it.tmdb_id,
            libraryId: it.library_id, title: it.title, poster: it.poster, status: it.status, source: source }) : '';
        return '<a class="vsr-card' + (owned ? ' vsr-card--owned' : '') + '" href="' + href + '" ' +
            'data-vsr-open="' + it.kind + '" data-vsr-source="' + source + '" data-vsr-id="' + id +
            '" style="--vgm-h:' + hueOf(it.title) + '">' + cb +
            '<div class="vsr-poster">' + img + ribbon + rating +
            '<span class="vsr-peek" aria-hidden="true">i</span></div>' +
            '<div class="vsr-info"><span class="vsr-name" title="' + esc(it.title) + '">' + esc(it.title) +
            '</span><span class="vsr-sub">' + esc(sub) + '</span></div></a>';
    }
    function hydrateGet(root) { if (window.VideoWatchlist) VideoWatchlist.hydrate(root); }

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

    // ── "More like <owned title>" rails (pre-filled, prepended above the stack) ─
    function loadMoreLike() {
        fetch('/api/video/discover/morelike', { headers: { Accept: 'application/json' } })
            .then(function (r) { return r.ok ? r.json() : null; })
            .then(function (d) {
                var rails = (d && d.rails) || [];
                var host = $('[data-vdsc-shelves]');
                if (!rails.length || !host) return;
                var html = rails.map(function (rl) {
                    return '<section class="vdsc-shelf vdsc-shelf--in vdsc-shelf--ml" data-vdsc-loaded="1">' +
                        '<div class="vdsc-shelf-head">' +
                            '<h3 class="vdsc-shelf-title">' + esc(rl.title) + '</h3>' +
                            '<div class="vdsc-shelf-nav">' +
                                '<button class="vdsc-arrow" type="button" data-vdsc-scroll="-1" aria-label="Scroll left">‹</button>' +
                                '<button class="vdsc-arrow" type="button" data-vdsc-scroll="1" aria-label="Scroll right">›</button>' +
                            '</div>' +
                        '</div>' +
                        '<div class="vdsc-rail" data-vdsc-rail>' + (rl.items || []).map(card).join('') + '</div>' +
                    '</section>';
                }).join('');
                host.insertAdjacentHTML('afterbegin', html);
                hydrateGet(host);
            })
            .catch(function () { /* personalization is best-effort */ });
    }

    // ── "What am I missing?" gap rails — franchises you've started but not finished,
    //    and more from the directors/creators you own the most (prepended on top). ──
    function loadGaps() {
        fetch('/api/video/discover/gaps', { headers: { Accept: 'application/json' } })
            .then(function (r) { return r.ok ? r.json() : null; })
            .then(function (d) {
                var rails = (d && d.rails) || [];
                var host = $('[data-vdsc-shelves]');
                if (!rails.length || !host) return;
                var html = rails.map(function (rl) {
                    return '<section class="vdsc-shelf vdsc-shelf--in vdsc-shelf--gap" data-vdsc-loaded="1">' +
                        '<div class="vdsc-shelf-head">' +
                            '<h3 class="vdsc-shelf-title">' + esc(rl.title) + '</h3>' +
                            '<div class="vdsc-shelf-nav">' +
                                '<button class="vdsc-arrow" type="button" data-vdsc-scroll="-1" aria-label="Scroll left">‹</button>' +
                                '<button class="vdsc-arrow" type="button" data-vdsc-scroll="1" aria-label="Scroll right">›</button>' +
                            '</div>' +
                        '</div>' +
                        '<div class="vdsc-rail" data-vdsc-rail>' + (rl.items || []).map(card).join('') + '</div>' +
                    '</section>';
                }).join('');
                host.insertAdjacentHTML('afterbegin', html);
                hydrateGet(host);
            })
            .catch(function () { /* gaps are best-effort */ });
    }

    // ── "Recommended for you" — one wall blended from across your library, prepended on top ─
    function loadForYou() {
        fetch('/api/video/discover/foryou', { headers: { Accept: 'application/json' } })
            .then(function (r) { return r.ok ? r.json() : null; })
            .then(function (d) {
                var items = (d && d.items) || [];
                var host = $('[data-vdsc-shelves]');
                if (items.length < 6 || !host) return;
                var html = '<section class="vdsc-shelf vdsc-shelf--in vdsc-shelf--foryou" data-vdsc-loaded="1">' +
                    '<div class="vdsc-shelf-head">' +
                        '<h3 class="vdsc-shelf-title">Recommended for you</h3>' +
                        '<div class="vdsc-shelf-nav">' +
                            '<button class="vdsc-arrow" type="button" data-vdsc-scroll="-1" aria-label="Scroll left">‹</button>' +
                            '<button class="vdsc-arrow" type="button" data-vdsc-scroll="1" aria-label="Scroll right">›</button>' +
                        '</div>' +
                    '</div>' +
                    '<div class="vdsc-rail" data-vdsc-rail>' + items.map(card).join('') + '</div>' +
                '</section>';
                host.insertAdjacentHTML('afterbegin', html);
                hydrateGet(host);
            })
            .catch(function () { /* best-effort */ });
    }

    // ── shelves (lazy rails) ──────────────────────────────────────────────────
    function renderShelves() {
        var host = $('[data-vdsc-shelves]'); if (!host) return;
        host.innerHTML = buildShelfList().map(function (sh) {
            return '<section class="vdsc-shelf" data-vdsc-q="' + esc(sh.q) + '" data-vdsc-title="' + esc(sh.title) + '">' +
                '<div class="vdsc-shelf-head">' +
                    '<h3 class="vdsc-shelf-title">' + esc(sh.title) + '</h3>' +
                    '<div class="vdsc-shelf-nav">' +
                        '<button class="vdsc-seeall" type="button" data-vdsc-seeall>See all</button>' +
                        '<button class="vdsc-arrow" type="button" data-vdsc-scroll="-1" aria-label="Scroll left">‹</button>' +
                        '<button class="vdsc-arrow" type="button" data-vdsc-scroll="1" aria-label="Scroll right">›</button>' +
                    '</div>' +
                '</div>' +
                '<div class="vdsc-rail" data-vdsc-rail>' +
                    '<div class="vdsc-skel">' + Array(8).join('<div class="vdsc-skel-card"></div>') + '</div>' +
                '</div>' +
            '</section>';
        }).join('');
        observeShelves();
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
    function fillShelf(shelf) {
        if (!shelf || shelf.getAttribute('data-vdsc-loaded')) return;
        shelf.setAttribute('data-vdsc-loaded', '1');
        var rail = $('[data-vdsc-rail]', shelf);
        // Normal: 2 pages (~40 items). Hiding owned: let the backend page DEEPER and drop
        // owned server-side, so a huge library's rail still fills instead of CSS-hiding to ~nothing.
        var q = shelf.getAttribute('data-vdsc-q') + (isHideOwned() ? '&hide_owned=1' : '&pages=2');
        cachedFetch(LIST_URL + '?' + q)
            .then(function (d) {
                var items = (d && d.items) || [];
                if (!items.length) { shelf.remove(); return; }    // drop empty shelves
                if (rail) { rail.innerHTML = items.map(card).join(''); hydrateGet(rail); }
                shelf.classList.add('vdsc-shelf--in');            // reveal
            })
            .catch(function () { shelf.remove(); });
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
                // The rails are now server-filtered for owned (and page deeper), so rebuild
                // them with the new state instead of just CSS-hiding cards in place.
                renderShelves();
                loadMoreLike();
                loadGaps();
                loadForYou();
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
        Promise.all([jget('/api/video/discover/genres'), jget('/api/video/discover/taste')])
            .then(function (res) {
                var g = res[0] || {}, t = res[1] || {};
                state.genres = { movie: g.movie || [], show: g.show || [] };
                state.taste = { movie: t.movie || [], show: t.show || [] };
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
