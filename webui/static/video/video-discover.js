/*
 * SoulSync — Video Discover page (isolated, in-app).
 *
 * A browse-everything page for TMDB titles you don't own yet: a cross-fading
 * trending hero, then a deep stack of Netflix-style rails (curated lists, every
 * genre, a few decades), each lazy-loaded on scroll. A filter bar (kind / genre /
 * year / sort) flips the whole page into a paged grid. Cards reuse the search
 * card look + owned/preview ribbon and open the detail page via the shared
 * soulsync:video-open-detail event. Self-contained IIFE, no globals.
 */
(function () {
    'use strict';

    var PAGE_ID = 'video-discover';
    var LIST_URL = '/api/video/discover/list';

    var state = {
        loaded: false, wired: false, mode: 'shelves',
        genres: { movie: [], show: [] },
        io: null,                       // IntersectionObserver for lazy rails
        hero: { items: [], idx: 0, timer: null },
        filter: { kind: 'movie', genre: '', decade: '', year: '', sort: 'popularity.desc', page: 1, busy: false },
    };

    function $(s, r) { return (r || document).querySelector(s); }
    function esc(s) {
        return String(s == null ? '' : s)
            .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
    }
    function hueOf(s) { var h = 0, t = String(s || ''); for (var i = 0; i < t.length; i++) h = (h * 31 + t.charCodeAt(i)) >>> 0; return h % 360; }

    // ── the rail stack (curated + genre + decade) ─────────────────────────────
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
    // Movie genres to surface as their own rails (resolved to ids once loaded).
    var GENRE_RAILS = ['Action', 'Adventure', 'Comedy', 'Drama', 'Science Fiction',
        'Thriller', 'Horror', 'Animation', 'Fantasy', 'Romance', 'Documentary', 'Crime'];
    var DECADE_RAILS = [
        { title: 'Best of the 2010s', q: 'kind=movie&decade=2010&sort=vote_average.desc' },
        { title: '2000s Favorites', q: 'kind=movie&decade=2000&sort=vote_average.desc' },
        { title: '’90s Classics', q: 'kind=movie&decade=1990&sort=vote_average.desc' },
        { title: 'Retro ’80s', q: 'kind=movie&decade=1980&sort=vote_average.desc' },
    ];

    function buildShelfList() {
        var shelves = CURATED.slice();
        var gmap = {};
        state.genres.movie.forEach(function (g) { gmap[(g.name || '').toLowerCase()] = g.id; });
        GENRE_RAILS.forEach(function (name) {
            var id = gmap[name.toLowerCase()];
            if (id != null) shelves.push({ title: name, q: 'kind=movie&genre=' + id + '&sort=popularity.desc' });
        });
        return shelves.concat(DECADE_RAILS);
    }

    // ── card (mirrors the search title card: owned ribbon + get button) ───────
    function card(it) {
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

    function hydrateGet(root) {
        // shows already on the watchlist paint their eye as "watched"
        if (window.VideoWatchlist) VideoWatchlist.hydrate(root);
    }

    // ── hero slideshow ────────────────────────────────────────────────────────
    function loadHero() {
        fetch('/api/video/discover/hero', { headers: { Accept: 'application/json' } })
            .then(function (r) { return r.ok ? r.json() : null; })
            .then(function (d) {
                state.hero.items = (d && d.items) || [];
                renderHero();
            })
            .catch(function () { /* hero is optional chrome */ });
    }

    function renderHero() {
        var host = $('[data-vdsc-hero]'); if (!host) return;
        var items = state.hero.items;
        if (!items.length) { host.classList.add('hidden'); return; }
        host.classList.remove('hidden');
        var slides = items.map(function (it, i) {
            return '<div class="vdsc-slide' + (i === 0 ? ' vdsc-slide--on' : '') + '" data-vdsc-slide="' + i + '" ' +
                'style="--vgm-h:' + hueOf(it.title) + ';' + (it.backdrop ? "background-image:url('" + esc(it.backdrop) + "')" : '') + '">' +
                '<div class="vdsc-slide-scrim"></div></div>';
        }).join('');
        var dots = items.map(function (it, i) {
            return '<button class="vdsc-dot' + (i === 0 ? ' vdsc-dot--on' : '') + '" type="button" ' +
                'data-vdsc-go="' + i + '" aria-label="Slide ' + (i + 1) + '"></button>';
        }).join('');
        host.innerHTML =
            '<div class="vdsc-slides" data-vdsc-slides>' + slides + '</div>' +
            '<div class="vdsc-hero-body" data-vdsc-hero-body></div>' +
            '<div class="vdsc-dots">' + dots + '</div>';
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
        body.style.setProperty('--vgm-h', hueOf(it.title));
        body.innerHTML =
            '<div class="vdsc-hero-eyebrow">' + (owned ? 'In your library' : 'Trending now') + '</div>' +
            '<h2 class="vdsc-hero-title">' + esc(it.title) + '</h2>' +
            '<div class="vdsc-hero-pills">' + pills.map(function (p) {
                return '<span class="vdsc-hero-pill">' + esc(p) + '</span>'; }).join('') + '</div>' +
            (it.overview ? '<p class="vdsc-hero-ov">' + esc(it.overview) + '</p>' : '') +
            '<button class="discog-submit-btn vdsc-hero-cta" type="button" data-vdsc-open ' +
            'data-vsr-open="' + it.kind + '" data-vsr-source="' + source + '" data-vsr-id="' + id + '">' +
            '<span class="discog-submit-text">View ' + (it.kind === 'movie' ? 'movie' : 'show') + ' →</span></button>';
    }

    function goHero(i) {
        var items = state.hero.items; if (!items.length) return;
        state.hero.idx = (i + items.length) % items.length;
        var slides = document.querySelectorAll('[data-vdsc-slide]');
        for (var s = 0; s < slides.length; s++)
            slides[s].classList.toggle('vdsc-slide--on', s === state.hero.idx);
        var dots = document.querySelectorAll('[data-vdsc-go]');
        for (var d = 0; d < dots.length; d++)
            dots[d].classList.toggle('vdsc-dot--on', d === state.hero.idx);
        paintHeroBody();
    }
    function startHeroTimer() {
        stopHeroTimer();
        if (window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches) return;
        if (state.hero.items.length < 2) return;
        state.hero.timer = setInterval(function () { goHero(state.hero.idx + 1); }, 6500);
    }
    function stopHeroTimer() { if (state.hero.timer) { clearInterval(state.hero.timer); state.hero.timer = null; } }

    // ── shelves (lazy rails) ──────────────────────────────────────────────────
    function renderShelves() {
        var host = $('[data-vdsc-shelves]'); if (!host) return;
        var shelves = buildShelfList();
        host.innerHTML = shelves.map(function (sh) {
            return '<section class="vdsc-shelf" data-vdsc-q="' + esc(sh.q) + '">' +
                '<div class="vdsc-shelf-head">' +
                    '<h3 class="vdsc-shelf-title">' + esc(sh.title) + '</h3>' +
                    '<div class="vdsc-shelf-nav">' +
                        '<button class="vdsc-arrow" type="button" data-vdsc-scroll="-1" aria-label="Scroll left">‹</button>' +
                        '<button class="vdsc-arrow" type="button" data-vdsc-scroll="1" aria-label="Scroll right">›</button>' +
                    '</div>' +
                '</div>' +
                '<div class="vdsc-rail" data-vdsc-rail>' +
                    '<div class="vdsc-skel">' + Array(7).join('<div class="vdsc-skel-card"></div>') + '</div>' +
                '</div>' +
            '</section>';
        }).join('');
        observeShelves();
    }

    function observeShelves() {
        if (state.io) state.io.disconnect();
        if (!('IntersectionObserver' in window)) {           // no IO → just load all
            var all = document.querySelectorAll('.vdsc-shelf');
            for (var i = 0; i < all.length; i++) fillShelf(all[i]);
            return;
        }
        state.io = new IntersectionObserver(function (entries) {
            entries.forEach(function (en) {
                if (en.isIntersecting) { state.io.unobserve(en.target); fillShelf(en.target); }
            });
        }, { rootMargin: '300px 0px' });
        var shelves = document.querySelectorAll('.vdsc-shelf');
        for (var j = 0; j < shelves.length; j++) state.io.observe(shelves[j]);
    }

    function fillShelf(shelf) {
        if (!shelf || shelf.getAttribute('data-vdsc-loaded')) return;
        shelf.setAttribute('data-vdsc-loaded', '1');
        var rail = $('[data-vdsc-rail]', shelf);
        var q = shelf.getAttribute('data-vdsc-q');
        fetch(LIST_URL + '?' + q, { headers: { Accept: 'application/json' } })
            .then(function (r) { return r.ok ? r.json() : null; })
            .then(function (d) {
                var items = (d && d.items) || [];
                if (!items.length) { shelf.remove(); return; }   // drop empty shelves
                if (rail) { rail.innerHTML = items.map(card).join(''); hydrateGet(rail); }
            })
            .catch(function () { shelf.remove(); });
    }

    // ── filter / grid mode ────────────────────────────────────────────────────
    function applyFilter() {
        state.mode = 'grid';
        state.filter.page = 1;
        $('[data-vdsc-shelves]').classList.add('hidden');
        $('[data-vdsc-grid-wrap]').classList.remove('hidden');
        $('[data-vdsc-grid]').innerHTML = '';
        loadGrid(true);
    }
    function clearFilter() {
        state.mode = 'shelves';
        $('[data-vdsc-grid-wrap]').classList.add('hidden');
        $('[data-vdsc-shelves]').classList.remove('hidden');
    }
    function gridQuery() {
        var f = state.filter;
        var p = ['kind=' + f.kind, 'sort=' + encodeURIComponent(f.sort), 'page=' + f.filterPage];
        if (f.genre) p.push('genre=' + f.genre);
        if (f.decade) p.push('decade=' + f.decade);
        if (f.year) p.push('year=' + encodeURIComponent(f.year));
        return p.join('&');
    }
    function loadGrid(reset) {
        var f = state.filter;
        if (f.busy) return;
        f.busy = true;
        f.filterPage = f.page;
        var more = $('[data-vdsc-more]'); if (more) { more.disabled = true; more.textContent = 'Loading…'; }
        var ld = $('[data-vdsc-grid-loading]'); if (ld && reset) ld.classList.remove('hidden');
        fetch(LIST_URL + '?' + gridQuery(), { headers: { Accept: 'application/json' } })
            .then(function (r) { return r.ok ? r.json() : null; })
            .then(function (d) {
                f.busy = false;
                if (ld) ld.classList.add('hidden');
                var items = (d && d.items) || [];
                var grid = $('[data-vdsc-grid]');
                if (grid) { grid.insertAdjacentHTML('beforeend', items.map(card).join('')); hydrateGet(grid); }
                var empty = $('[data-vdsc-grid-empty]');
                if (empty) empty.classList.toggle('hidden', !(reset && !items.length));
                if (more) {
                    more.textContent = 'Load more';
                    more.disabled = false;
                    more.classList.toggle('hidden', items.length < 18);   // a full page → likely more
                }
            })
            .catch(function () {
                f.busy = false;
                if (ld) ld.classList.add('hidden');
                if (more) { more.textContent = 'Load more'; more.disabled = false; }
            });
    }

    function rebuildGenreOptions() {
        var sel = $('[data-vdsc-f-genre]'); if (!sel) return;
        var list = state.genres[state.filter.kind] || [];
        sel.innerHTML = '<option value="">All genres</option>' + list.map(function (g) {
            return '<option value="' + g.id + '">' + esc(g.name) + '</option>';
        }).join('');
    }

    // ── wiring ────────────────────────────────────────────────────────────────
    function wire() {
        if (state.wired) return;
        state.wired = true;
        var page = $('[data-video-subpage="' + PAGE_ID + '"]'); if (!page) return;

        // one delegated click for the whole page: cards + hero CTA + arrows + dots
        page.addEventListener('click', function (e) {
            var open = e.target.closest('[data-vsr-open]');
            if (open) {
                if (e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;
                e.preventDefault();
                var kind = open.getAttribute('data-vsr-open');
                var id = parseInt(open.getAttribute('data-vsr-id'), 10);
                if (isNaN(id)) return;
                document.dispatchEvent(new CustomEvent('soulsync:video-open-detail', {
                    detail: { kind: kind, id: id, source: open.getAttribute('data-vsr-source') || 'tmdb' },
                }));
                return;
            }
            var arrow = e.target.closest('[data-vdsc-scroll]');
            if (arrow) {
                var rail = $('[data-vdsc-rail]', arrow.closest('.vdsc-shelf'));
                if (rail) rail.scrollBy({ left: parseInt(arrow.getAttribute('data-vdsc-scroll'), 10) * rail.clientWidth * 0.8, behavior: 'smooth' });
                return;
            }
            var dot = e.target.closest('[data-vdsc-go]');
            if (dot) { goHero(parseInt(dot.getAttribute('data-vdsc-go'), 10)); startHeroTimer(); return; }
        });

        // hero auto-advance pauses while hovered
        var hero = $('[data-vdsc-hero]');
        if (hero) {
            hero.addEventListener('mouseenter', stopHeroTimer);
            hero.addEventListener('mouseleave', startHeroTimer);
        }

        var kind = $('[data-vdsc-f-kind]');
        if (kind) kind.addEventListener('change', function () {
            state.filter.kind = kind.value === 'show' ? 'show' : 'movie';
            rebuildGenreOptions();
        });
        var genre = $('[data-vdsc-f-genre]');
        if (genre) genre.addEventListener('change', function () { state.filter.genre = genre.value; });
        var decade = $('[data-vdsc-f-decade]');
        if (decade) decade.addEventListener('change', function () { state.filter.decade = decade.value; });
        var sort = $('[data-vdsc-f-sort]');
        if (sort) sort.addEventListener('change', function () { state.filter.sort = sort.value; });

        var apply = $('[data-vdsc-apply]');
        if (apply) apply.addEventListener('click', applyFilter);
        var clear = $('[data-vdsc-clear]');
        if (clear) clear.addEventListener('click', clearFilter);
        var more = $('[data-vdsc-more]');
        if (more) more.addEventListener('click', function () { state.filter.page++; loadGrid(false); });
    }

    function loadGenres() {
        fetch('/api/video/discover/genres', { headers: { Accept: 'application/json' } })
            .then(function (r) { return r.ok ? r.json() : null; })
            .then(function (d) {
                state.genres = { movie: (d && d.movie) || [], show: (d && d.show) || [] };
                rebuildGenreOptions();
                renderShelves();          // genre rails need the id map
            })
            .catch(function () { renderShelves(); });
    }

    function load() {
        if (state.loaded) return;
        state.loaded = true;
        loadHero();
        loadGenres();
    }

    function onShown(e) { if (e && e.detail === PAGE_ID) { wire(); load(); startHeroTimer(); } }
    function onHidden() { stopHeroTimer(); }

    function init() {
        document.addEventListener('soulsync:video-page-shown', onShown);
        // pause the hero when leaving the page
        document.addEventListener('soulsync:video-page-shown', function (e) {
            if (e && e.detail !== PAGE_ID) onHidden();
        });
    }

    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init);
    else init();
})();
