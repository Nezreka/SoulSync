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
    var state = { loaded: false, eps: {}, data: null, offset: 0, filter: 'all' };

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
        var parts = [range];
        if (n) {
            parts.push(n + (n === 1 ? ' episode' : ' episodes'));
            if (owned) parts.push(owned + ' in library');
            if (miss) parts.push(miss + ' missing');
        } else { parts.push('nothing scheduled'); }
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
        return '<a class="vcal-cell" style="--vcal-h:' + hue + ';--i:' + (idx % 24) + '" ' +
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
        var byTime = function (a, b) {
            var ma = airMins(a.airs_time), mb = airMins(b.airs_time);
            if (ma == null && mb == null) return 0;
            if (ma == null) return 1; if (mb == null) return -1;
            return ma - mb;
        };

        // header row: corner + 7 day heads
        var html = '<div class="vcal-guide-corner"></div>';
        for (var i = 0; i < COLS; i++) {
            var dt = days[i], today = isoOf(dt) === d.today;
            html += '<div class="vcal-dayhead' + (today ? ' vcal-dayhead--today' : '') + '">' +
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
        BANDS.forEach(function (b) {
            var anyEps = false;
            for (var i = 0; i < COLS; i++) if (grid[b.key][i].length) { anyEps = true; break; }
            if (!anyEps) return;
            html += '<div class="vcal-rail vcal-rail--' + b.key + '">' +
                '<span class="vcal-rail-label">' + b.label + '</span>' +
                '<small>' + b.range + '</small></div>';
            for (var i = 0; i < COLS; i++) {
                var cell = grid[b.key][i].slice().sort(byTime);
                var today = isoOf(days[i]) === d.today;
                var isNow = today && b.key === nowBand;
                var inner = cell.length
                    ? cell.map(function (ep) { return epCell(ep, stagger++); }).join('')
                    : '<span class="vcal-slot-dot" aria-hidden="true"></span>';
                html += '<div class="vcal-slot' + (today ? ' vcal-slot--today' : '') +
                    (isNow ? ' vcal-slot--now' : '') +
                    (cell.length ? '' : ' vcal-slot--empty') + '">' + inner + '</div>';
            }
        });
        cols.innerHTML = html;
    }

    // ── featured "next up" billboard ──────────────────────────────────────────
    function featured(d) {
        var eps = (d.episodes || []).slice();
        eps.sort(function (a, b) {
            if (a.air_date !== b.air_date) return a.air_date < b.air_date ? -1 : 1;
            var ma = airMins(a.airs_time), mb = airMins(b.airs_time);
            if (ma == null && mb == null) return 0;
            if (ma == null) return 1; if (mb == null) return -1;
            return ma - mb;
        });
        return eps[0] || null;
    }
    function whenLabel(ep, today) {
        var mins = airMins(ep.airs_time);
        var diff = Math.round((parseISO(ep.air_date) - parseISO(today)) / 86400000);
        var day = diff === 0 ? ((mins != null && mins >= 17 * 60) ? 'Tonight' : 'Today')
            : diff === 1 ? 'Tomorrow' : WD_FULL[parseISO(ep.air_date).getDay()];
        return day + (mins != null ? ', ' + fmtMins(mins) : '');
    }
    function renderHero(d) {
        var host = $('[data-video-cal-hero]'); if (!host) return;
        var ep = featured(d);
        if (!ep) { host.innerHTML = ''; return; }
        var hue = showHue(ep.show_title || '');
        var bg = ep.show_has_backdrop ? ('/api/video/backdrop/show/' + ep.show_id + '?w=1280') : '';
        var se = 'S' + ep.season_number + ' · E' + ep.episode_number;
        var epTitle = ep.title || '';
        var owned = ep.has_file ? '<span class="vcal-bb-badge">✓ In your library</span>' : '';
        host.innerHTML =
            '<div class="vcal-bb" style="--vcal-h:' + hue + '" data-cal-ep="' + ep.id + '" role="button" tabindex="0">' +
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
    }
    // Render from the cached payload + the active filter (no refetch).
    function render() {
        var d = state.data;
        updateChrome();
        var hero = $('[data-video-cal-hero]'), cols = $('[data-video-cal-cols]');
        if (!d) { showEmpty(true); if (hero) hero.innerHTML = ''; if (cols) cols.innerHTML = ''; return; }
        var eps = filterEps(d.episodes || []);
        var view = { episodes: eps, total: eps.length, today: d.today, start: d.start, end: d.end, days: d.days };
        var has = eps.length > 0;
        showEmpty(!has);
        if (has) {
            renderHero(view); renderGrid(view);
            // Card entrance only on a fresh load/week-change — NOT on filter
            // re-renders (those would re-stagger every click and feel busy).
            if (state.fresh && cols) {
                cols.classList.add('vcal-animate-in');
                setTimeout(function () { cols.classList.remove('vcal-animate-in'); }, 900);
            }
        } else { if (hero) hero.innerHTML = ''; if (cols) cols.innerHTML = ''; }
        state.fresh = false;
        var grid = $('[data-video-cal-grid]'); if (grid) grid.classList.remove('vcal-fading');
        setSub(view);
    }
    function load() {
        state.loaded = true; state.fresh = true;
        // Crossfade the grid out while the next week loads (only when we already
        // have something on screen — first load uses the entrance instead).
        var grid = $('[data-video-cal-grid]'); if (grid && state.data) grid.classList.add('vcal-fading');
        showEmpty(false); showLoading(true);
        var base = new Date(); base.setHours(0, 0, 0, 0); base.setDate(base.getDate() + state.offset * 7);
        fetch(URL + '?days=7&start=' + isoOf(base), { headers: { 'Accept': 'application/json' } })
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
        var el = target.closest('[data-cal-ep]');
        if (!el) return false;
        var ep = state.eps[el.getAttribute('data-cal-ep')];
        if (ep) openModal(ep);
        return true;
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
            if (e.target.closest('[data-cal-ep]') && cols.contains(e.target)) { e.preventDefault(); openFrom(e.target); }
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
        if (today) today.addEventListener('click', function () { if (state.offset !== 0) { state.offset = 0; load(); } });
        var fbs = document.querySelectorAll('[data-video-cal-filter]');
        for (var i = 0; i < fbs.length; i++) (function (b) {
            b.addEventListener('click', function () { state.filter = b.getAttribute('data-video-cal-filter'); render(); });
        })(fbs[i]);
        var addBtn = $('[data-video-cal-addmissing]');
        if (addBtn) addBtn.addEventListener('click', addMissing);

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
                    '<button class="vcm-btn vcm-btn--primary" type="button" data-vcm-open>Open full show page →</button>' +
                '</div>' +
            '</div>';

        document.body.appendChild(ov);
        document.body.style.overflow = 'hidden';
        modalEl = ov;
        requestAnimationFrame(function () { ov.classList.add('vcm-open'); });

        ov.addEventListener('click', function (e) {
            if (e.target === ov || e.target.closest('[data-vcm-close]')) { closeModal(); return; }
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

    function onPageShown(e) { if (e && e.detail === PAGE_ID && !state.loaded) load(); }

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
