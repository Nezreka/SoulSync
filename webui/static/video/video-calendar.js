/*
 * SoulSync — Video Calendar (isolated): the Week Grid.
 *
 * A real 7-column week (TODAY first). Every upcoming episode for your owned
 * shows is shown — no "+N more" — each with its air time, sorted earliest →
 * latest per day (untimed streaming drops sink to the bottom). The "vibe" lives
 * ON the grid: each cell softly breathes a glow in its show's colour. Cells open
 * the show detail. Self-contained IIFE, fetches only /api/video/calendar.
 */
(function () {
    'use strict';

    var PAGE_ID = 'video-calendar';
    var URL = '/api/video/calendar';
    var COLS = 7;
    var WD = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'];
    var WD_FULL = ['Sunday', 'Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday'];
    var MO = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
    var MO_FULL = ['January', 'February', 'March', 'April', 'May', 'June', 'July', 'August',
        'September', 'October', 'November', 'December'];
    var state = { loaded: false, eps: {}, data: null, offset: 0, filter: 'all', view: 'compact', scope: 'watchlist',
        movieTypes: { cinema: true, available: true } };

    function $(s) { return document.querySelector(s); }
    function esc(s) {
        return String(s == null ? '' : s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
    }
    function parseISO(s) { var p = (s || '').split('-'); return new Date(+p[0], (+p[1] || 1) - 1, +p[2] || 1); }
    function isoOf(d) {
        return d.getFullYear() + '-' + ('0' + (d.getMonth() + 1)).slice(-2) + '-' + ('0' + d.getDate()).slice(-2);
    }
    function showHue(title) {
        var h = 0, s = title || '';
        for (var i = 0; i < s.length; i++) h = (h * 31 + s.charCodeAt(i)) >>> 0;
        return h % 360;
    }
    // TVDB air time → minutes-from-midnight (null if absent). Handles "21:00",
    // "21:00:00", "9:00 PM", "8:00pm".
    function airMins(s) {
        if (!s) return null;
        s = String(s).trim();
        var m = s.match(/^(\d{1,2}):(\d{2})/);
        if (!m) return null;
        var h = +m[1], mi = +m[2];
        if (/pm/i.test(s) && h < 12) h += 12;
        if (/am/i.test(s) && h === 12) h = 0;
        if (h > 23 || mi > 59) return null;
        if (h === 0 && mi === 0) return null;   // 00:00 = TVDB streaming placeholder, not a real slot
        return h * 60 + mi;
    }
    function fmtMins(mins) {
        if (mins == null) return '';
        var h = (mins / 60) | 0, mi = mins % 60, ap = h >= 12 ? 'PM' : 'AM', hh = h % 12 || 12;
        return hh + ':' + ('0' + mi).slice(-2) + ' ' + ap;
    }

    function showLoading(on) { var el = $('[data-video-cal-loading]'); if (el) el.classList.toggle('hidden', !on); }
    function showEmpty(on) {
        var el = $('[data-video-cal-empty]'); if (el) el.classList.toggle('hidden', !on);
        var g = $('[data-video-cal-grid]'); if (g) g.classList.toggle('hidden', !!on);
    }
    function setSub(d) {
        var el = $('[data-video-cal-sub]'); if (!el) return;
        var a = parseISO(d.start), b = parseISO(d.end);
        var range = MO[a.getMonth()] + ' ' + a.getDate() + ' – ' + MO[b.getMonth()] + ' ' + b.getDate();
        var eps = d.episodes || [], n = eps.length;
        var owned = 0; for (var i = 0; i < n; i++) if (eps[i].has_file) owned++;
        var miss = n - owned;
        var mv = (d.movies || []).length;
        var parts = [range];
        if (n) {
            parts.push(n + (n === 1 ? ' episode' : ' episodes'));
            if (owned) parts.push(owned + ' in library');
            if (miss) parts.push(miss + ' missing');
        } else if (!mv) { parts.push('nothing scheduled'); }
        if (mv) parts.push(mv + ' movie release' + (mv === 1 ? '' : 's'));
        el.textContent = parts.join('  ·  ');
    }

    function epCell(ep, idx) {
        var hue = showHue(ep.show_title || '');
        var art = ep.has_still ? ('/api/video/poster/episode/' + ep.id + '?w=500')
            : (ep.show_has_backdrop ? ('/api/video/backdrop/show/' + ep.show_id + '?w=500') : '');
        var img = art ? '<img class="vcal-cell-img" src="' + art + '" alt="" loading="lazy" decoding="async" ' +
            'onload="this.classList.add(\'vcal-loaded\')" onerror="this.style.display=\'none\'">' : '';
        var se = 'S' + ep.season_number + ' · E' + ep.episode_number;
        var epTitle = ep.title || '';   // no redundant "Episode N" when untitled
        var tl = fmtMins(airMins(ep.airs_time));
        var time = tl
            ? '<span class="vcal-time"><span class="vcal-time-dot"></span>' + tl + '</span>'
            : '<span class="vcal-time vcal-time--none">Anytime</span>';
        var flag = ep.has_file ? '<span class="vcal-flag" title="In your library">✓</span>' : '';
        var meta = '<span class="vcal-se">' + se + '</span>' + (epTitle ? '<span class="vcal-ep">' + esc(epTitle) + '</span>' : '');
        return '<a class="vcal-cell' + (ep.has_file ? ' vcal-cell--owned' : '') + '" style="--vcal-h:' + hue + ';--i:' + (idx % 24) + '" ' +
            'href="/video-detail/library/show/' + ep.show_id + '" ' +
            'data-cal-ep="' + ep.id + '" ' +
            'title="' + esc(ep.show_title) + (epTitle ? ' — ' + esc(epTitle) : '') + (tl ? ' · ' + tl : '') + '">' +
            '<span class="vcal-glow" aria-hidden="true"></span>' +
            '<span class="vcal-card">' +
                '<span class="vcal-art">' + img + '<span class="vcal-art-scrim"></span>' + flag + '</span>' +
                '<span class="vcal-info">' +
                    time +
                    '<span class="vcal-show">' + esc(ep.show_title) + '</span>' +
                    '<span class="vcal-meta">' + meta + '</span>' +
                '</span>' +
            '</span>' +
            '</a>';
    }

    // Movie release event card — lives on the dedicated "Movies" rail. Typed:
    // 'cinema' (theatrical premiere) or 'available' (home release, the date the
    // wishlist drain can actually act on).
    var MOVIE_TYPE = {
        cinema: { chip: '🎬 In Cinemas', cls: 'vcal-mv--cinema' },
        available: { chip: '🏠 Home Release', cls: 'vcal-mv--home' }
    };
    function movieCell(m, idx) {
        var t = MOVIE_TYPE[m.type] || MOVIE_TYPE.available;
        var hue = showHue(m.title || '');
        var img = m.poster_url ? '<img class="vcal-cell-img" src="' + esc(m.poster_url) + '" alt="" loading="lazy" decoding="async" ' +
            'onload="this.classList.add(\'vcal-loaded\')" onerror="this.style.display=\'none\'">' : '';
        var flag = m.owned ? '<span class="vcal-flag" title="In your library">✓</span>' : '';
        var href = m.owned && m.library_id
            ? '/video-detail/library/movie/' + m.library_id
            : '/video-detail/tmdb/movie/' + m.tmdb_id;
        return '<a class="vcal-cell vcal-mv ' + t.cls + (m.owned ? ' vcal-cell--owned' : '') + '" ' +
            'style="--vcal-h:' + hue + ';--i:' + (idx % 24) + '" href="' + href + '" ' +
            'data-cal-movie="' + idx + '" ' +
            'title="' + esc(m.title) + (m.year ? ' (' + m.year + ')' : '') + '">' +
            '<span class="vcal-glow" aria-hidden="true"></span>' +
            '<span class="vcal-card">' +
                '<span class="vcal-art">' + img + '<span class="vcal-art-scrim"></span>' + flag + '</span>' +
                '<span class="vcal-info">' +
                    '<span class="vcal-time vcal-mv-chip">' + t.chip + '</span>' +
                    '<span class="vcal-show">' + esc(m.title) + '</span>' +
                    (m.year ? '<span class="vcal-meta"><span class="vcal-se">' + m.year + '</span></span>' : '') +
                '</span>' +
            '</span>' +
            '</a>';
    }
    function filterMovies(movies) {
        var out = (movies || []).filter(function (m) { return state.movieTypes[m.type] !== false; });
        if (state.filter === 'owned') return out.filter(function (m) { return m.owned; });
        if (state.filter === 'missing') return out.filter(function (m) { return !m.owned; });
        return out;
    }

    // Time-of-day bands — the rows of the guide. A real shared time axis so
    // you can scan ACROSS the week ("what's on in Prime Time") instead of
    // reading seven disconnected stacks. Untimed streaming drops land in
    // "Anytime". Each card still carries its exact time.
    var BANDS = [
        { key: 'morning', label: 'Morning', range: '5a–12p', lo: 300, hi: 720 },
        { key: 'afternoon', label: 'Afternoon', range: '12–5p', lo: 720, hi: 1020 },
        { key: 'prime', label: 'Prime Time', range: '5–9p', lo: 1020, hi: 1260 },
        { key: 'late', label: 'Late Night', range: '9p–5a', lo: 1260, hi: 1740 },  // wraps past midnight
        { key: 'anytime', label: 'Anytime', range: 'Streaming', lo: null, hi: null }
    ];
    function bandKeyFor(mins) {
        if (mins == null) return 'anytime';
        // Late night wraps: 21:00–04:59 (treat early-AM as +24h for the range test).
        var m = mins < 300 ? mins + 1440 : mins;
        for (var i = 0; i < BANDS.length; i++) {
            var b = BANDS[i];
            if (b.lo == null) continue;
            if (m >= b.lo && m < b.hi) return b.key;
        }
        return 'anytime';
    }

    function renderGrid(d) {
        var cols = $('[data-video-cal-cols]'); if (!cols) return;
        var start = parseISO(d.start);
        var days = [];
        for (var i = 0; i < COLS; i++) { var dt = new Date(start); dt.setDate(start.getDate() + i); days.push(dt); }

        // grid[bandKey][dayIndex] = [episodes]
        var grid = {};
        BANDS.forEach(function (b) { grid[b.key] = []; for (var i = 0; i < COLS; i++) grid[b.key].push([]); });
        var dayCount = [];
        for (var i = 0; i < COLS; i++) dayCount.push(0);
        (d.episodes || []).forEach(function (ep) {
            for (var i = 0; i < COLS; i++) {
                if (ep.air_date === isoOf(days[i])) {
                    grid[bandKeyFor(airMins(ep.airs_time))][i].push(ep);
                    dayCount[i]++;
                    break;
                }
            }
        });
        // Movie releases get their own rail (they carry a date, not a time slot).
        var movieByDay = []; for (var i = 0; i < COLS; i++) movieByDay.push([]);
        (d.movies || []).forEach(function (m) {
            for (var i = 0; i < COLS; i++) {
                if (m.date === isoOf(days[i])) { movieByDay[i].push(m); dayCount[i]++; break; }
            }
        });
        var byTime = function (a, b) {
            var ma = airMins(a.airs_time), mb = airMins(b.airs_time);
            if (ma == null && mb == null) return 0;
            if (ma == null) return 1; if (mb == null) return -1;
            return ma - mb;
        };

        // header row: corner + 7 day heads
        var html = '<div class="vcal-guide-corner"></div>';
        for (var i = 0; i < COLS; i++) {
            var dt = days[i], di = isoOf(dt), today = di === d.today, past = di < d.today;
            html += '<div class="vcal-dayhead' + (today ? ' vcal-dayhead--today' : (past ? ' vcal-dayhead--past' : '')) + '">' +
                '<span class="vcal-dayhead-wd">' + (today ? 'Today' : WD_FULL[dt.getDay()]) + '</span>' +
                '<span class="vcal-dayhead-date">' + WD[dt.getDay()] + ' ' + dt.getDate() + '</span>' +
                (dayCount[i] ? '<span class="vcal-dayhead-n">' + dayCount[i] + '</span>' : '') +
                '</div>';
        }

        // one row per active band (skip bands empty across the whole week)
        var stagger = 0;
        // "Now" cue — the band the wall-clock is currently in. Only the today
        // column carries it (other weeks have no today column), so it lights up
        // the live time-of-day intersection like a TV guide.
        var nowD = new Date();
        var nowBand = bandKeyFor(nowD.getHours() * 60 + nowD.getMinutes());
        // "Movies" rail first — releases lead the guide when the week has any.
        var anyMovies = false;
        for (var mi = 0; mi < COLS; mi++) if (movieByDay[mi].length) { anyMovies = true; break; }
        if (anyMovies) {
            html += '<div class="vcal-rail vcal-rail--movies">' +
                '<span class="vcal-rail-label">Movies</span>' +
                '<small>Releases</small></div>';
            for (var mj = 0; mj < COLS; mj++) {
                var mday = isoOf(days[mj]); var mtoday = mday === d.today; var mpast = mday < d.today;
                var minner = movieByDay[mj].length
                    ? movieByDay[mj].map(function (m) { return movieCell(m, stagger++); }).join('')
                    : '<span class="vcal-slot-dot" aria-hidden="true"></span>';
                html += '<div class="vcal-slot' + (mtoday ? ' vcal-slot--today' : (mpast ? ' vcal-slot--past' : '')) +
                    (movieByDay[mj].length ? '' : ' vcal-slot--empty') + '">' + minner + '</div>';
            }
        }
        BANDS.forEach(function (b) {
            var anyEps = false;
            for (var i = 0; i < COLS; i++) if (grid[b.key][i].length) { anyEps = true; break; }
            if (!anyEps) return;
            html += '<div class="vcal-rail vcal-rail--' + b.key + '">' +
                '<span class="vcal-rail-label">' + b.label + '</span>' +
                '<small>' + b.range + '</small></div>';
            for (var i = 0; i < COLS; i++) {
                var cell = grid[b.key][i].slice().sort(byTime);
                var slotDay = isoOf(days[i]); var today = slotDay === d.today; var past = slotDay < d.today;
                var isNow = today && b.key === nowBand;
                var inner = cell.length
                    ? cell.map(function (ep) { return epCell(ep, stagger++); }).join('')
                    : '<span class="vcal-slot-dot" aria-hidden="true"></span>';
                html += '<div class="vcal-slot' + (today ? ' vcal-slot--today' : (past ? ' vcal-slot--past' : '')) +
                    (isNow ? ' vcal-slot--now' : '') +
                    (cell.length ? '' : ' vcal-slot--empty') + '">' + inner + '</div>';
            }
        });
        cols.innerHTML = html;
    }

    // ── agenda view — one chronological list, day by day ─────────────────────
    // Same data, linear shape: the scannable "what's coming" list (and the
    // mobile-first answer to the 7-column grid). Movies lead each day.
    function renderAgenda(d) {
        var cols = $('[data-video-cal-cols]'); if (!cols) return;
        var start = parseISO(d.start);
        var byTime = function (a, b) {
            var ma = airMins(a.airs_time), mb = airMins(b.airs_time);
            if (ma == null && mb == null) return 0;
            if (ma == null) return 1; if (mb == null) return -1;
            return ma - mb;
        };
        var html = '';
        for (var i = 0; i < COLS; i++) {
            var dt = new Date(start); dt.setDate(start.getDate() + i);
            var iso = isoOf(dt), today = iso === d.today, past = iso < d.today;
            var eps = (d.episodes || []).filter(function (e) { return e.air_date === iso; }).sort(byTime);
            var movies = (d.movies || []).filter(function (m) { return m.date === iso; });
            if (!eps.length && !movies.length) continue;
            var label = (today ? 'Today' : WD_FULL[dt.getDay()]) + ' · ' + MO[dt.getMonth()] + ' ' + dt.getDate();
            html += '<div class="vcal-ag-day' + (today ? ' vcal-ag-day--today' : (past ? ' vcal-ag-day--past' : '')) + '">' +
                '<div class="vcal-ag-head"><span class="vcal-ag-head-label">' + label + '</span>' +
                '<span class="vcal-ag-head-n">' + (eps.length + movies.length) + '</span></div>';
            movies.forEach(function (m) {
                var t = MOVIE_TYPE[m.type] || MOVIE_TYPE.available;
                var idx = (state.movieEvents || []).indexOf(m);
                html += '<a class="vcal-ag-row vcal-ag-row--movie" data-cal-movie="' + idx + '" ' +
                    'href="' + (m.owned && m.library_id ? '/video-detail/library/movie/' + m.library_id
                        : '/video-detail/tmdb/movie/' + m.tmdb_id) + '">' +
                    '<span class="vcal-ag-time vcal-mv-chip">' + t.chip + '</span>' +
                    '<span class="vcal-ag-main"><span class="vcal-ag-title">' + esc(m.title) + '</span>' +
                    (m.year ? '<span class="vcal-ag-sub">' + m.year + '</span>' : '') + '</span>' +
                    (m.owned ? '<span class="vcal-flag" title="In your library">✓</span>' : '') +
                    '</a>';
            });
            eps.forEach(function (ep) {
                var tl = fmtMins(airMins(ep.airs_time));
                var se = 'S' + ep.season_number + ' · E' + ep.episode_number;
                html += '<a class="vcal-ag-row" data-cal-ep="' + ep.id + '" ' +
                    'href="/video-detail/library/show/' + ep.show_id + '" ' +
                    'style="--vcal-h:' + showHue(ep.show_title || '') + '">' +
                    '<span class="vcal-ag-time' + (tl ? '' : ' vcal-time--none') + '">' + (tl || 'Anytime') + '</span>' +
                    '<span class="vcal-ag-main"><span class="vcal-ag-title">' + esc(ep.show_title) + '</span>' +
                    '<span class="vcal-ag-sub">' + se + (ep.title ? ' · ' + esc(ep.title) : '') + '</span></span>' +
                    (ep.has_file ? '<span class="vcal-flag" title="In your library">✓</span>' : '') +
                    '</a>';
            });
            html += '</div>';
        }
        cols.innerHTML = html;
    }

    // ── featured "next up" billboard ──────────────────────────────────────────
    // Air datetime in ms. No airs_time → treat as "anytime today" (end of day)
    // so streaming/undated shows stay featured all day instead of expiring at 00:00.
    function epDT(ep) {
        var base = parseISO(ep.air_date).getTime();
        var mins = airMins(ep.airs_time);
        return base + (mins != null ? mins * 60000 : (23 * 60 + 59) * 60000);
    }
    // Up to 3 "next up" episodes, soonest first. On the CURRENT week this is
    // time-of-day aware: the next episodes that haven't finished airing yet
    // (90-min grace so a show that's on right now stays up), advancing as the
    // day goes. Once the whole week has aired it falls back to the most recent.
    // Other weeks just feature the first few of the week.
    function featuredList(d) {
        var eps = (d.episodes || []).slice().sort(function (a, b) { return epDT(a) - epDT(b); });
        if (!eps.length) return [];
        if (state.offset === 0) {
            var now = new Date().getTime(), GRACE = 90 * 60000;
            var up = eps.filter(function (ep) { return epDT(ep) + GRACE >= now; });
            var pool = up.length ? up : eps.slice().reverse();
            return pool.slice(0, 3);
        }
        return eps.slice(0, 3);
    }
    function whenLabel(ep, today) {
        var mins = airMins(ep.airs_time);
        var diff = Math.round((parseISO(ep.air_date) - parseISO(today)) / 86400000);
        var day = diff === 0 ? ((mins != null && mins >= 17 * 60) ? 'Tonight' : 'Today')
            : diff === 1 ? 'Tomorrow' : WD_FULL[parseISO(ep.air_date).getDay()];
        return day + (mins != null ? ', ' + fmtMins(mins) : '');
    }
    // One billboard panel (used solo, or as one slice of the diagonal multi-hero)
    function heroPanel(ep, d, opts) {
        var multi = opts && opts.multi, lead = opts && opts.lead;
        var hue = showHue(ep.show_title || '');
        var bg = ep.show_has_backdrop ? ('/api/video/backdrop/show/' + ep.show_id + '?w=1280') : '';
        var se = 'S' + ep.season_number + ' · E' + ep.episode_number;
        var epTitle = ep.title || '';
        var owned = ep.has_file ? '<span class="vcal-bb-badge">✓ In your library</span>' : '';
        var cls = multi ? ('vcal-bb-panel' + (lead ? ' vcal-bb-panel--lead' : '')) : 'vcal-bb';
        return '<div class="' + cls + '" style="--vcal-h:' + hue + '" data-cal-ep="' + ep.id + '" role="button" tabindex="0">' +
                (bg ? '<div class="vcal-bb-bg" style="background-image:url(\'' + bg + '\')"></div>' : '') +
                '<div class="vcal-bb-scrim"></div>' +
                '<div class="vcal-bb-content">' +
                    '<div class="vcal-bb-eyebrow"><span class="vcal-bb-dot"></span>' +
                        (state.offset === 0 ? 'NEXT UP' : 'FEATURED') + ' · ' + esc(whenLabel(ep, d.today)) + '</div>' +
                    '<h2 class="vcal-bb-title">' + esc(ep.show_title) + '</h2>' +
                    '<div class="vcal-bb-sub"><span class="vcal-bb-se">' + se + '</span>' + (epTitle ? ' · ' + esc(epTitle) : '') + '</div>' +
                    '<div class="vcal-bb-actions"><span class="vcal-bb-btn">View details</span>' + owned + '</div>' +
                '</div>' +
            '</div>';
    }
    function renderHero(d) {
        var host = $('[data-video-cal-hero]'); if (!host) return;
        var list = featuredList(d);
        if (!list.length) { host.innerHTML = ''; return; }
        if (list.length === 1) { host.innerHTML = heroPanel(list[0], d, { multi: false }); return; }
        // 2-3 episodes → diagonal split panels; the soonest leads, hovering any
        // expands it full-width and collapses the rest.
        var panels = list.map(function (ep, i) { return heroPanel(ep, d, { multi: true, lead: i === 0 }); }).join('');
        host.innerHTML = '<div class="vcal-bb-multi" data-count="' + list.length + '">' + panels + '</div>';
    }

    function filterEps(eps) {
        if (state.filter === 'owned') return eps.filter(function (e) { return e.has_file; });
        if (state.filter === 'missing') return eps.filter(function (e) { return !e.has_file; });
        return eps;
    }
    function updateChrome() {
        var t = $('[data-video-cal-title]');
        if (t) t.textContent = state.offset === 0 ? 'This Week'
            : state.offset === 1 ? 'Next Week' : state.offset === -1 ? 'Last Week'
                : state.offset > 0 ? 'In ' + state.offset + ' weeks' : Math.abs(state.offset) + ' weeks ago';
        var today = $('[data-video-cal-today]');
        if (today) today.disabled = state.offset === 0;
        var fbs = document.querySelectorAll('[data-video-cal-filter]');
        for (var i = 0; i < fbs.length; i++)
            fbs[i].classList.toggle('vcal-filter-btn--on', fbs[i].getAttribute('data-video-cal-filter') === state.filter);
        var mbs = document.querySelectorAll('[data-video-cal-movietype]');
        for (var j = 0; j < mbs.length; j++)
            mbs[j].classList.toggle('vcal-filter-btn--on',
                state.movieTypes[mbs[j].getAttribute('data-video-cal-movietype')] !== false);
    }
    // Render from the cached payload + the active filter (no refetch).
    function render() {
        var d = state.data;
        updateChrome();
        var hero = $('[data-video-cal-hero]'), cols = $('[data-video-cal-cols]');
        if (!d) { showEmpty(true); if (hero) hero.innerHTML = ''; if (cols) cols.innerHTML = ''; return; }
        var eps = filterEps(d.episodes || []);
        var movies = filterMovies(d.movies || []);
        state.movieEvents = movies;   // click lookup for data-cal-movie
        var mwrap = $('[data-video-cal-movies-wrap]');
        if (mwrap) mwrap.classList.toggle('hidden', !(d.movies || []).length);
        var view = { episodes: eps, movies: movies, total: eps.length,
            today: d.today, start: d.start, end: d.end, days: d.days };
        var has = eps.length > 0 || movies.length > 0;
        showEmpty(!has);
        if (has) {
            renderHero(view);
            if (state.view === 'agenda') renderAgenda(view); else renderGrid(view);
            // Card entrance only on a fresh load/week-change — NOT on filter
            // re-renders (those would re-stagger every click and feel busy).
            if (state.fresh && cols) {
                cols.classList.add('vcal-animate-in');
                setTimeout(function () { cols.classList.remove('vcal-animate-in'); }, 900);
            }
        } else { if (hero) hero.innerHTML = ''; if (cols) cols.innerHTML = ''; }
        if (has && state.scrollToNow) scrollToNow();
        state.scrollToNow = false;
        state.fresh = false;
        var grid = $('[data-video-cal-grid]'); if (grid) grid.classList.remove('vcal-fading');
        setSub(view);
    }
    // Scroll the band the wall-clock is currently in into view, so opening the
    // calendar lands on "now" instead of pre-dawn. Current week only.
    function scrollToNow() {
        if (state.offset !== 0) return;
        var cols = $('[data-video-cal-cols]'); if (!cols) return;
        var now = new Date();
        var nowKey = bandKeyFor(now.getHours() * 60 + now.getMinutes());
        var idx = 0; for (var k = 0; k < BANDS.length; k++) if (BANDS[k].key === nowKey) { idx = k; break; }
        var smooth = !(window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches);
        requestAnimationFrame(function () {
            var el = cols.querySelector('.vcal-slot--now') || cols.querySelector('.vcal-rail--' + nowKey);
            // now band has nothing all week → nearest rendered band, scanning down then up
            for (var i = idx; i < BANDS.length && !el; i++) el = cols.querySelector('.vcal-rail--' + BANDS[i].key);
            for (var j = idx - 1; j >= 0 && !el; j--) el = cols.querySelector('.vcal-rail--' + BANDS[j].key);
            if (el && el.scrollIntoView) el.scrollIntoView({ behavior: smooth ? 'smooth' : 'auto', block: 'center' });
        });
    }
    function load() {
        state.loaded = true; state.fresh = true;
        // Crossfade the grid out while the next week loads (only when we already
        // have something on screen — first load uses the entrance instead).
        var grid = $('[data-video-cal-grid]'); if (grid && state.data) grid.classList.add('vcal-fading');
        showEmpty(false); showLoading(true);
        // Show the full calendar week (Sun→Sat) that contains the target day, like
        // Sonarr — so "This Week" includes days already aired earlier this week, not
        // just today-forward. Snap the start back to that week's Sunday.
        var base = new Date(); base.setHours(0, 0, 0, 0);
        base.setDate(base.getDate() + state.offset * 7);   // a day inside the target week
        base.setDate(base.getDate() - base.getDay());       // → that week's Sunday (getDay 0=Sun)
        fetch(URL + '?days=7&start=' + isoOf(base) + '&scope=' + (state.scope || 'watchlist'),
            { headers: { 'Accept': 'application/json' } })
            .then(function (r) { return r.ok ? r.json() : null; })
            .then(function (d) {
                showLoading(false);
                if (!d || d.error) { state.data = null; render(); return; }
                state.data = d;
                state.eps = {};
                (d.episodes || []).forEach(function (e) { state.eps[e.id] = e; });
                render();
                refreshAddMissing();   // surface the catch-up button for aired-missing eps
            })
            .catch(function () { showLoading(false); state.data = null; render(); });
    }

    // ── "Add missing to wishlist" (catch-up for the auto-promoter) ────────────
    // Targets already-AIRED, MISSING episodes in this week that aren't yet on the
    // wishlist. Upcoming episodes are left alone (the calendar promotes them once
    // they air). Mostly a no-op on the current/future weeks; useful on past ones.
    function airedMissing() {
        var d = state.data; if (!d) return [];
        var today = d.today;
        return (d.episodes || []).filter(function (e) {
            return !e.has_file && e.show_tmdb_id && e.air_date && e.air_date < today;
        });
    }
    function refreshAddMissing() {
        var btn = $('[data-video-cal-addmissing]'); if (!btn) return;
        state.addMissing = [];
        btn.classList.add('hidden');
        var cand = airedMissing();
        if (!cand.length) return;
        var ids = []; cand.forEach(function (e) { if (ids.indexOf(e.show_tmdb_id) < 0) ids.push(e.show_tmdb_id); });
        fetch('/api/video/wishlist/check', { method: 'POST', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ shows: ids }) })
            .then(function (r) { return r.ok ? r.json() : null; })
            .then(function (res) {
                var by = (res && res.by_show) || {};
                var fresh = cand.filter(function (e) {
                    var have = by[String(e.show_tmdb_id)] || [];
                    return have.indexOf(e.season_number + '_' + e.episode_number) < 0;
                });
                state.addMissing = fresh;
                var lbl = $('[data-video-cal-addmissing-label]');
                if (lbl) lbl.textContent = 'Add ' + fresh.length + ' missing to wishlist';
                btn.classList.toggle('hidden', !fresh.length);
            })
            .catch(function () { /* leave hidden */ });
    }
    function addMissing() {
        var eps = state.addMissing || []; if (!eps.length) return;
        var btn = $('[data-video-cal-addmissing]'); if (btn) btn.disabled = true;
        var byShow = {};
        eps.forEach(function (e) {
            var g = byShow[e.show_tmdb_id] || (byShow[e.show_tmdb_id] = {
                tmdb_id: e.show_tmdb_id, title: e.show_title,
                poster_url: e.show_has_poster ? ('/api/video/poster/show/' + e.show_id) : null,
                library_id: e.show_id, episodes: [] });
            g.episodes.push({ season_number: e.season_number, episode_number: e.episode_number,
                title: e.title, air_date: e.air_date });
        });
        var posts = Object.keys(byShow).map(function (k) {
            var g = byShow[k];
            return fetch('/api/video/wishlist/add', { method: 'POST', headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ show: { tmdb_id: g.tmdb_id, title: g.title, poster_url: g.poster_url,
                    library_id: g.library_id }, episodes: g.episodes }) }).then(function (r) { return r.ok ? r.json() : null; });
        });
        Promise.all(posts).then(function () {
            if (typeof showToast === 'function') showToast('Added ' + eps.length + ' missing episode' + (eps.length === 1 ? '' : 's') + ' to wishlist', 'success');
            document.dispatchEvent(new CustomEvent('soulsync:video-wishlist-changed'));
            if (btn) btn.disabled = false;
            refreshAddMissing();   // recompute → button hides what's now queued
        }).catch(function () { if (btn) btn.disabled = false; });
    }

    function openFrom(target) {
        var mv = target.closest('[data-cal-movie]');
        if (mv) {
            var m = (state.movieEvents || [])[+mv.getAttribute('data-cal-movie')];
            if (m) document.dispatchEvent(new CustomEvent('soulsync:video-open-detail', {
                detail: m.owned && m.library_id
                    ? { kind: 'movie', id: m.library_id, source: 'library' }
                    : { kind: 'movie', id: m.tmdb_id, source: 'tmdb' },
            }));
            return true;
        }
        var el = target.closest('[data-cal-ep]');
        if (!el) return false;
        var ep = state.eps[el.getAttribute('data-cal-ep')];
        if (ep) openModal(ep);
        return true;
    }

    // ── iCal subscribe (feed already served at /api/video/calendar.ics) ──────
    function openIcsModal() {
        var url = window.location.origin + '/api/video/calendar.ics?scope=' + (state.scope || 'watchlist');
        var ov = document.createElement('div');
        ov.className = 'vcm-overlay vcal-ics-overlay';
        ov.innerHTML =
            '<div class="vcm-modal vcal-ics-modal" role="dialog" aria-modal="true" aria-label="Subscribe to calendar">' +
                '<button class="vcm-close" type="button" data-vcm-close aria-label="Close">×</button>' +
                '<div class="vcal-ics-body">' +
                    '<h3 class="vcal-ics-title">📅 Subscribe to your calendar</h3>' +
                    '<p class="vcal-ics-sub">Add this feed to Google Calendar, Apple Calendar or Outlook ' +
                        '("add calendar from URL") and upcoming episodes + movie releases show up there, ' +
                        'kept in sync automatically.</p>' +
                    '<div class="vcal-ics-row">' +
                        '<input class="vcal-ics-url" type="text" readonly value="' + esc(url) + '" data-vcal-ics-url>' +
                        '<button class="vcm-btn vcm-btn--primary" type="button" data-vcal-ics-copy>Copy</button>' +
                    '</div>' +
                    '<p class="vcal-ics-note">Covers the next 14 days (add <code>?days=60</code> for more). ' +
                        'Airing episodes carry their air time; movie releases are all-day events.</p>' +
                '</div>' +
            '</div>';
        document.body.appendChild(ov);
        requestAnimationFrame(function () { ov.classList.add('vcm-open'); });
        function close() {
            ov.classList.remove('vcm-open');
            document.removeEventListener('keydown', onKey);
            setTimeout(function () { if (ov.parentNode) ov.parentNode.removeChild(ov); }, 220);
        }
        function onKey(e) { if (e.key === 'Escape') close(); }
        document.addEventListener('keydown', onKey);
        ov.addEventListener('click', function (e) {
            if (e.target === ov || e.target.closest('[data-vcm-close]')) { close(); return; }
            if (e.target.closest('[data-vcal-ics-copy]')) {
                var inp = ov.querySelector('[data-vcal-ics-url]');
                inp.select();
                var done = function () { if (typeof showToast === 'function') showToast('Feed URL copied', 'success'); };
                if (navigator.clipboard && navigator.clipboard.writeText) {
                    navigator.clipboard.writeText(inp.value).then(done).catch(function () { document.execCommand('copy'); done(); });
                } else { document.execCommand('copy'); done(); }
            }
        });
    }
    // Cursor-following 3D tilt (delegated, one element at a time).
    function wireTilt(container, sel, deg) {
        if (!container) return;
        var last = null;
        container.addEventListener('mousemove', function (e) {
            var c = e.target.closest(sel);
            if (c !== last && last) { last.style.removeProperty('--rx'); last.style.removeProperty('--ry'); }
            last = c;
            if (!c) return;
            var r = c.getBoundingClientRect();
            var px = (e.clientX - r.left) / r.width - 0.5, py = (e.clientY - r.top) / r.height - 0.5;
            c.style.setProperty('--ry', (px * deg).toFixed(2) + 'deg');
            c.style.setProperty('--rx', (-py * deg).toFixed(2) + 'deg');
        });
        container.addEventListener('mouseleave', function () {
            if (last) { last.style.removeProperty('--rx'); last.style.removeProperty('--ry'); last = null; }
        });
    }
    function wire() {
        var cols = $('[data-video-cal-cols]');
        if (cols) cols.addEventListener('click', function (e) {
            if (e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;
            if (e.target.closest('[data-cal-ep],[data-cal-movie]') && cols.contains(e.target)) { e.preventDefault(); openFrom(e.target); }
        });
        var hero = $('[data-video-cal-hero]');
        if (hero) {
            hero.addEventListener('click', function (e) { if (openFrom(e.target)) e.preventDefault(); });
            hero.addEventListener('keydown', function (e) {
                if ((e.key === 'Enter' || e.key === ' ') && e.target.closest('[data-cal-ep]')) { e.preventDefault(); openFrom(e.target); }
            });
            wireTilt(hero, '.vcal-bb', 3);
        }
        wireTilt(cols, '.vcal-cell', 7);

        var prev = $('[data-video-cal-prev]'); if (prev) prev.addEventListener('click', function () { state.offset--; load(); });
        var next = $('[data-video-cal-next]'); if (next) next.addEventListener('click', function () { state.offset++; load(); });
        var today = $('[data-video-cal-today]');
        if (today) today.addEventListener('click', function () {
            state.scrollToNow = true;
            if (state.offset !== 0) { state.offset = 0; load(); } else scrollToNow();
        });
        var fbs = document.querySelectorAll('[data-video-cal-filter]');
        for (var i = 0; i < fbs.length; i++) (function (b) {
            b.addEventListener('click', function () { state.filter = b.getAttribute('data-video-cal-filter'); render(); });
        })(fbs[i]);
        var vbs = document.querySelectorAll('[data-video-cal-view]');
        for (var k = 0; k < vbs.length; k++) (function (b) {
            b.addEventListener('click', function () { setView(b.getAttribute('data-video-cal-view')); });
        })(vbs[k]);
        var sbs = document.querySelectorAll('[data-video-cal-scope]');
        for (var j = 0; j < sbs.length; j++) (function (b) {
            b.addEventListener('click', function () { setScope(b.getAttribute('data-video-cal-scope')); });
        })(sbs[j]);
        var addBtn = $('[data-video-cal-addmissing]');
        if (addBtn) addBtn.addEventListener('click', addMissing);
        var icsBtn = $('[data-video-cal-ical]');
        if (icsBtn) icsBtn.addEventListener('click', openIcsModal);
        var mbs = document.querySelectorAll('[data-video-cal-movietype]');
        for (var q = 0; q < mbs.length; q++) (function (b) {
            b.addEventListener('click', function () {
                var t = b.getAttribute('data-video-cal-movietype');
                state.movieTypes[t] = state.movieTypes[t] === false;   // toggle
                try { localStorage.setItem('vcalMovieTypes', JSON.stringify(state.movieTypes)); } catch (e) { /* private mode */ }
                render();
            });
        })(mbs[q]);

        // Keyboard nav for a page users live in: ← / → step weeks, T jumps to
        // today. Only when the calendar is the visible page, no modal is open,
        // and the user isn't typing in a field.
        document.addEventListener('keydown', function (e) {
            if (modalEl || e.metaKey || e.ctrlKey || e.altKey) return;
            var tag = (e.target && e.target.tagName) || '';
            if (/^(INPUT|TEXTAREA|SELECT)$/.test(tag) || (e.target && e.target.isContentEditable)) return;
            var g = $('[data-video-cal-cols]');
            if (!g || g.offsetParent === null) return;   // calendar page not visible
            if (e.key === 'ArrowLeft') { e.preventDefault(); state.offset--; load(); }
            else if (e.key === 'ArrowRight') { e.preventDefault(); state.offset++; load(); }
            else if (e.key === 't' || e.key === 'T') { if (state.offset !== 0) { e.preventDefault(); state.offset = 0; load(); } }
        });
    }

    // ── episode modal ─────────────────────────────────────────────────────────
    var modalEl = null, modalKeyHandler = null;
    function fmtFullDate(iso) { var d = parseISO(iso); return WD_FULL[d.getDay()] + ', ' + MO_FULL[d.getMonth()] + ' ' + d.getDate(); }

    function closeModal() {
        if (!modalEl) return;
        modalEl.classList.remove('vcm-open');
        document.body.style.removeProperty('overflow');
        if (modalKeyHandler) { document.removeEventListener('keydown', modalKeyHandler); modalKeyHandler = null; }
        var el = modalEl; modalEl = null;
        setTimeout(function () { if (el && el.parentNode) el.parentNode.removeChild(el); }, 220);
    }

    function openModal(ep) {
        closeModal();
        var hue = showHue(ep.show_title || '');
        var backdrop = ep.show_has_backdrop ? ('/api/video/backdrop/show/' + ep.show_id + '?w=1000') : '';
        var still = ep.has_still ? ('/api/video/poster/episode/' + ep.id + '?w=600')
            : (ep.show_has_backdrop ? ('/api/video/backdrop/show/' + ep.show_id + '?w=600') : '');
        var se = 'S' + ep.season_number + ' · E' + ep.episode_number;
        var epTitle = ep.title || ('Episode ' + ep.episode_number);
        var tl = fmtMins(airMins(ep.airs_time));
        var when = fmtFullDate(ep.air_date) + ' · ' + (tl || 'Anytime');
        var owned = ep.has_file
            ? '<span class="vcm-badge vcm-badge--have">✓ In your library</span>'
            : '<span class="vcm-badge vcm-badge--miss">Not in library</span>';
        var tags = [owned];
        if (ep.runtime_minutes) tags.push('<span class="vcm-tag">' + ep.runtime_minutes + ' min</span>');
        if (ep.rating) tags.push('<span class="vcm-tag vcm-tag--star">★ ' + (Math.round(ep.rating * 10) / 10) + '</span>');
        var eyebrow = [ep.network, ep.show_year, ep.show_status].filter(Boolean).map(esc).join(' · ');

        // Any missing episode can be wishlisted — including UPCOMING ones (pre-order from the
        // calendar). The drain safely skips a wished episode until its air date arrives, so
        // wishing an unaired one no longer sends it hunting for a release that can't exist yet.
        var wishable = !ep.has_file && !!ep.show_tmdb_id;
        var upcoming = wishable && ep.air_date && state.data && ep.air_date > state.data.today;

        var ov = document.createElement('div');
        ov.className = 'vcm-overlay'; ov.setAttribute('data-vcm', '');
        ov.style.setProperty('--vcm-h', hue);
        ov.innerHTML =
            '<div class="vcm-modal" role="dialog" aria-modal="true">' +
                '<button class="vcm-close" type="button" data-vcm-close aria-label="Close">×</button>' +
                '<div class="vcm-hero">' +
                    (backdrop ? '<div class="vcm-hero-bg" style="background-image:url(\'' + backdrop + '\')"></div>' : '') +
                    '<div class="vcm-hero-scrim"></div>' +
                    '<div class="vcm-hero-content">' +
                        '<div class="vcm-eyebrow" data-vcm-eyebrow>' + eyebrow + '</div>' +
                        '<h2 class="vcm-show">' + esc(ep.show_title) + '</h2>' +
                        '<div class="vcm-genres" data-vcm-genres></div>' +
                    '</div>' +
                '</div>' +
                '<div class="vcm-body">' +
                    '<div class="vcm-ep">' +
                        '<div class="vcm-ep-still">' +
                            (still ? '<img src="' + still + '" alt="" decoding="async" ' +
                                'onload="this.classList.add(\'vcm-loaded\')" onerror="this.style.display=\'none\'">' : '') +
                            '<span class="vcm-ep-fb">▶</span></div>' +
                        '<div class="vcm-ep-main">' +
                            '<div class="vcm-ep-se">' + se + '</div>' +
                            '<h3 class="vcm-ep-title">' + esc(epTitle) + '</h3>' +
                            '<div class="vcm-when">' + esc(when) + '</div>' +
                            '<div class="vcm-tags">' + tags.join('') + '</div>' +
                            (ep.overview ? '<p class="vcm-ep-ov">' + esc(ep.overview) + '</p>'
                                : '<p class="vcm-ep-ov vcm-ep-ov--none">No episode synopsis yet.</p>') +
                        '</div>' +
                    '</div>' +
                    '<div class="vcm-about" data-vcm-about hidden>' +
                        '<h4 class="vcm-about-h">About the show</h4>' +
                        '<p class="vcm-about-ov" data-vcm-show-ov></p>' +
                    '</div>' +
                '</div>' +
                '<div class="vcm-actions">' +
                    '<button class="vcm-btn vcm-btn--ghost" type="button" data-vcm-close>Close</button>' +
                    (wishable ? '<button class="vcm-btn vcm-btn--ghost vcm-btn--wish" type="button" data-vcm-wish disabled' +
                        (upcoming ? ' title="Grabs automatically once it airs"' : '') + '>＋ Wishlist episode</button>' : '') +
                    '<button class="vcm-btn vcm-btn--primary" type="button" data-vcm-open>Open full show page →</button>' +
                '</div>' +
            '</div>';

        document.body.appendChild(ov);
        document.body.style.overflow = 'hidden';
        modalEl = ov;
        requestAnimationFrame(function () { ov.classList.add('vcm-open'); });

        // Enable the wish button only once we know it isn't already queued.
        if (wishable) {
            fetch('/api/video/wishlist/check', { method: 'POST', headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ shows: [ep.show_tmdb_id] }) })
                .then(function (r) { return r.ok ? r.json() : null; })
                .then(function (res) {
                    var btn = ov.querySelector('[data-vcm-wish]'); if (!btn) return;
                    var have = ((res && res.by_show) || {})[String(ep.show_tmdb_id)] || [];
                    if (have.indexOf(ep.season_number + '_' + ep.episode_number) > -1) {
                        btn.textContent = '✓ On wishlist';
                    } else { btn.disabled = false; }
                })
                .catch(function () { /* stays disabled */ });
        }

        function wishThis(btn) {
            btn.disabled = true;
            // identical payload shape to addMissing() — the write-parity contract
            // (library_id + poster proxy path) keeps the wishlist row fully art'd
            fetch('/api/video/wishlist/add', { method: 'POST', headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ show: { tmdb_id: ep.show_tmdb_id, title: ep.show_title,
                    poster_url: ep.show_has_poster ? ('/api/video/poster/show/' + ep.show_id) : null,
                    library_id: ep.show_id },
                    episodes: [{ season_number: ep.season_number, episode_number: ep.episode_number,
                        title: ep.title, air_date: ep.air_date }] }) })
                .then(function (r) { return r.ok ? r.json() : null; })
                .then(function (res) {
                    if (!res || res.error) {
                        btn.disabled = false;
                        if (typeof showToast === 'function') showToast((res && res.error) || 'Could not add to wishlist', 'error');
                        return;
                    }
                    btn.textContent = '✓ On wishlist';
                    if (typeof showToast === 'function') showToast('Added S' + ep.season_number + 'E' + ep.episode_number + ' to wishlist', 'success');
                    document.dispatchEvent(new CustomEvent('soulsync:video-wishlist-changed'));
                    refreshAddMissing();   // the bulk catch-up count just changed
                })
                .catch(function () { btn.disabled = false; if (typeof showToast === 'function') showToast('Could not add to wishlist', 'error'); });
        }

        ov.addEventListener('click', function (e) {
            if (e.target === ov || e.target.closest('[data-vcm-close]')) { closeModal(); return; }
            var wbtn = e.target.closest('[data-vcm-wish]');
            if (wbtn) { if (!wbtn.disabled) wishThis(wbtn); return; }
            if (e.target.closest('[data-vcm-open]')) {
                closeModal();
                document.dispatchEvent(new CustomEvent('soulsync:video-open-detail', {
                    detail: { kind: 'show', id: ep.show_id, source: 'library' },
                }));
            }
        });
        modalKeyHandler = function (e) { if (e.key === 'Escape') closeModal(); };
        document.addEventListener('keydown', modalKeyHandler);

        enrichModal(ep);
    }

    // Lazy-fill show overview + genres + a fuller eyebrow from the show detail.
    function enrichModal(ep) {
        var sid = ep.show_id;
        fetch('/api/video/detail/show/' + sid, { headers: { 'Accept': 'application/json' } })
            .then(function (r) { return r.ok ? r.json() : null; })
            .then(function (d) {
                if (!d || !modalEl) return;
                var g = modalEl.querySelector('[data-vcm-genres]');
                if (g && d.genres && d.genres.length) {
                    g.innerHTML = d.genres.slice(0, 4).map(function (x) {
                        return '<span class="vcm-genre">' + esc(x) + '</span>';
                    }).join('');
                }
                var eb = modalEl.querySelector('[data-vcm-eyebrow]');
                if (eb) {
                    var parts = [d.network || ep.network, d.year || ep.show_year, d.status || ep.show_status,
                        d.content_rating].filter(Boolean).map(esc);
                    if (parts.length) eb.textContent = parts.join('  ·  ');
                }
                var about = modalEl.querySelector('[data-vcm-about]');
                var ovEl = modalEl.querySelector('[data-vcm-show-ov]');
                if (about && ovEl && d.overview && d.overview !== ep.overview) {
                    ovEl.textContent = d.overview;
                    about.hidden = false;
                }
            })
            .catch(function () { /* modal already shows payload data */ });
    }

    // View switcher (cards ↔ compact ↔ agenda). Cards/compact is a pure CSS
    // class flip; crossing the agenda boundary rebuilds the list from the
    // cached payload (still no refetch). Remembers the choice.
    function setView(v) {
        var wasAgenda = state.view === 'agenda';
        state.view = v;
        try { localStorage.setItem('vcalView', v); } catch (e) { /* private mode */ }
        applyView();
        if (state.data && (wasAgenda || v === 'agenda')) render();
    }
    function applyView() {
        var grid = $('[data-video-cal-grid]');
        if (grid) {
            grid.classList.toggle('vcal-view--compact', state.view === 'compact');
            grid.classList.toggle('vcal-view--agenda', state.view === 'agenda');
        }
        var vbs = document.querySelectorAll('[data-video-cal-view]');
        for (var i = 0; i < vbs.length; i++)
            vbs[i].classList.toggle('vcal-view-btn--on', vbs[i].getAttribute('data-video-cal-view') === state.view);
    }

    // Source switcher (watchlist ↔ all library). Unlike the filter, this changes
    // WHICH shows the server returns, so it refetches. Remembers the choice.
    function setScope(sc) {
        if (sc !== 'watchlist' && sc !== 'all') return;
        if (sc === state.scope) return;
        state.scope = sc;
        try { localStorage.setItem('vcalScope', sc); } catch (e) { /* private mode */ }
        applyScope();
        load();
    }
    function applyScope() {
        var sbs = document.querySelectorAll('[data-video-cal-scope]');
        for (var i = 0; i < sbs.length; i++)
            sbs[i].classList.toggle('vcal-filter-btn--on', sbs[i].getAttribute('data-video-cal-scope') === state.scope);
    }

    function onPageShown(e) {
        if (!e || e.detail !== PAGE_ID) return;
        // Re-fetch on first show, OR when the day has rolled over since we last
        // loaded — otherwise the cached "today" stays frozen on whatever day the
        // calendar was first opened (e.g. it keeps saying Thursday into Friday)
        // until a full page reload.
        var rolledOver = state.data && state.data.today && state.data.today !== isoOf(new Date());
        if (!state.loaded || rolledOver) {
            if (rolledOver) state.offset = 0;   // land back on the real current week
            state.scrollToNow = true;
            load();
        } else {
            scrollToNow();  // already loaded, same day → just re-land on "now"
        }
    }

    function init() {
        wire();
        try { var sv = localStorage.getItem('vcalView'); if (sv) state.view = sv; } catch (e) { /* private mode */ }
        try { var sc = localStorage.getItem('vcalScope'); if (sc === 'watchlist' || sc === 'all') state.scope = sc; } catch (e) { /* private mode */ }
        try {
            var mt = JSON.parse(localStorage.getItem('vcalMovieTypes') || 'null');
            if (mt && typeof mt === 'object') state.movieTypes = { cinema: mt.cinema !== false, available: mt.available !== false };
        } catch (e) { /* private mode / bad json */ }
        applyView();
        applyScope();
        document.addEventListener('soulsync:video-page-shown', onPageShown);
    }

    // Exposed so other video pages (the dashboard's Upcoming preview) can open the
    // exact same episode modal instead of duplicating it. openModal builds its own
    // overlay on document.body and only closes over calendar-internal helpers +ep,
    // so it works standalone from any page once this module has loaded.
    window.VideoCalendar = { openEpisode: openModal, showHue: showHue };

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }
})();
