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
        cat: { title: '', q: '', page: 1, paginates: true, busy: false },
        sel: { kind: 'movie', genre: '', decade: '', sort: 'popularity.desc' },   // Browse panel
    };

    function $(s, r) { return (r || document).querySelector(s); }
    function esc(s) {
        return String(s == null ? '' : s)
            .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
    }
    function hueOf(s) { var h = 0, t = String(s || ''); for (var i = 0; i < t.length; i++) h = (h * 31 + t.charCodeAt(i)) >>> 0; return h % 360; }
    function idMap(list) { var m = {}; (list || []).forEach(function (g) { m[(g.name || '').toLowerCase()] = g.id; }); return m; }

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
    var DECADE_RAILS = [
        { title: 'Best of the 2010s', q: 'kind=movie&decade=2010&sort=vote_average.desc' },
        { title: '2000s Favorites', q: 'kind=movie&decade=2000&sort=vote_average.desc' },
        { title: '’90s Classics', q: 'kind=movie&decade=1990&sort=vote_average.desc' },
        { title: 'Retro ’80s', q: 'kind=movie&decade=1980&sort=vote_average.desc' },
    ];

    function buildShelfList() {
        var out = [], used = {};
        var gm = idMap(state.genres.movie), gs = idMap(state.genres.show);
        // personalized first — seeded from what you actually own
        (state.taste.movie || []).slice(0, 3).forEach(function (name) {
            var id = gm[name.toLowerCase()];
            if (id != null) { out.push({ title: 'Because you like ' + name, q: 'kind=movie&genre=' + id + '&sort=popularity.desc' }); used['m:' + name.toLowerCase()] = 1; }
        });
        (state.taste.show || []).slice(0, 2).forEach(function (name) {
            var id = gs[name.toLowerCase()];
            if (id != null) { out.push({ title: 'More ' + name + ' shows', q: 'kind=show&genre=' + id + '&sort=popularity.desc' }); }
        });
        out = out.concat(CURATED);
        GENRE_RAILS.forEach(function (name) {
            var id = gm[name.toLowerCase()];
            if (id != null && !used['m:' + name.toLowerCase()])
                out.push({ title: name, q: 'kind=movie&genre=' + id + '&sort=popularity.desc' });
        });
        return out.concat(DECADE_RAILS);
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
            '<button class="discog-submit-btn vdsc-hero-cta" type="button" ' +
            'data-vsr-open="' + it.kind + '" data-vsr-source="' + source + '" data-vsr-id="' + id + '">' +
            '<span class="discog-submit-text">View ' + (it.kind === 'movie' ? 'movie' : 'show') + ' →</span></button>';
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
    function fillShelf(shelf) {
        if (!shelf || shelf.getAttribute('data-vdsc-loaded')) return;
        shelf.setAttribute('data-vdsc-loaded', '1');
        var rail = $('[data-vdsc-rail]', shelf);
        // 2 pages (~40 items) so a rail still looks full after 'Hide owned'.
        fetch(LIST_URL + '?' + shelf.getAttribute('data-vdsc-q') + '&pages=2', { headers: { Accept: 'application/json' } })
            .then(function (r) { return r.ok ? r.json() : null; })
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
        state.cat = { title: title, q: q, page: 1, paginates: !/key=trending/.test(q), busy: false };
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
        var more = $('[data-vdsc-more]'); if (more) { more.disabled = true; more.textContent = 'Loading…'; }
        var ld = $('[data-vdsc-grid-loading]'); if (ld && reset) ld.classList.remove('hidden');
        fetch(LIST_URL + '?' + c.q + '&page=' + c.page, { headers: { Accept: 'application/json' } })
            .then(function (r) { return r.ok ? r.json() : null; })
            .then(function (d) {
                c.busy = false;
                if (ld) ld.classList.add('hidden');
                var items = (d && d.items) || [];
                var grid = $('[data-vdsc-grid]');
                if (grid) { grid.insertAdjacentHTML('beforeend', items.map(card).join('')); hydrateGet(grid); }
                var empty = $('[data-vdsc-grid-empty]');
                if (empty) empty.classList.toggle('hidden', !(reset && !items.length));
                if (more) {
                    more.textContent = 'Load more';
                    more.disabled = false;
                    more.classList.toggle('hidden', !c.paginates || items.length < 18);
                }
            })
            .catch(function () {
                c.busy = false;
                if (ld) ld.classList.add('hidden');
                if (more) { more.textContent = 'Load more'; more.disabled = false; }
            });
    }
    function applyFilter() {
        var s = state.sel;
        var q = ['kind=' + s.kind, 'sort=' + encodeURIComponent(s.sort)];
        var bits = [s.kind === 'show' ? 'Shows' : 'Movies'];
        if (s.genre) { q.push('genre=' + s.genre); var gn = genreName(s.kind, s.genre); if (gn) bits.push(gn); }
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
        box.innerHTML = '<button class="vdsc-chip vdsc-chip--on" type="button" data-val="">All genres</button>' +
            (state.genres[state.sel.kind] || []).map(function (g) {
                return '<button class="vdsc-chip" type="button" data-val="' + g.id + '">' + esc(g.name) + '</button>';
            }).join('');
        state.sel.genre = '';
    }
    function setActive(box, el, selector, onClass) {
        var all = box.querySelectorAll(selector);
        for (var i = 0; i < all.length; i++) all[i].classList.toggle(onClass, all[i] === el);
    }
    // Slide the segmented-control highlight under the active button.
    function moveSeg(box) {
        if (!box) return;
        var on = box.querySelector('.vdsc-seg-btn--on');
        if (!on || !on.offsetWidth) return;            // hidden / not laid out yet
        box.style.setProperty('--seg-x', on.offsetLeft + 'px');
        box.style.setProperty('--seg-w', on.offsetWidth + 'px');
    }
    function positionSegs() {
        var segs = document.querySelectorAll('[data-video-subpage="' + PAGE_ID + '"] .vdsc-seg');
        for (var i = 0; i < segs.length; i++) moveSeg(segs[i]);
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
                moveSeg(sbox);
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
        var more = $('[data-vdsc-more]'); if (more) more.addEventListener('click', function () { state.cat.page++; loadGrid(false); });

        var hide = $('[data-vdsc-hideowned]');
        if (hide) {
            try { if (localStorage.getItem('vdsc_hideowned') === '1') hide.checked = true; } catch (e) { /* ignore */ }
            page.classList.toggle('vdsc-hide-owned', hide.checked);
            hide.addEventListener('change', function () {
                page.classList.toggle('vdsc-hide-owned', hide.checked);
                try { localStorage.setItem('vdsc_hideowned', hide.checked ? '1' : '0'); } catch (e) { /* ignore */ }
            });
        }
        window.addEventListener('resize', positionSegs);
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
                requestAnimationFrame(positionSegs);
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
        if (e.detail === PAGE_ID) { wire(); load(); startHeroTimer(); requestAnimationFrame(positionSegs); }
        else stopHeroTimer();   // left the page → stop the slideshow
    }
    function init() { document.addEventListener('soulsync:video-page-shown', onShown); }

    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init);
    else init();
})();
