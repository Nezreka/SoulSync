/*
 * SoulSync — Video dashboard data layer.
 *
 * ISOLATION CONTRACT: like video-side.js this is a self-contained IIFE (no
 * globals, no inline handlers, lives under static/video/ which the script-split
 * integrity scan does not touch). It NEVER references music code, and music
 * never references it.
 *
 * It owns only the *data* of the video dashboard — the markup lives in
 * index.html and reuses music's .dash-card CSS for an identical look. It learns
 * when the dashboard becomes visible by listening for the
 * 'soulsync:video-page-shown' event that video-side.js dispatches (so the two
 * modules stay decoupled — no direct calls between them).
 *
 * TODO(video.db): STUB_STATS below is a placeholder. Once the video database +
 * its /api/video/dashboard endpoint exist, replace loadStats() with a fetch and
 * feed the same shape through applyStats(). Nothing else here needs to change.
 */
(function () {
    'use strict';

    var DASHBOARD_ID = 'video-dashboard';

    var DASHBOARD_URL = '/api/video/dashboard';

    // System stats (uptime + memory) come from the SAME endpoint the music
    // dashboard uses — it's one machine, so these figures are identical on both
    // sides. Polled on the dashboard's 10s cadence for parity with music's push
    // loop. (Reached over HTTP, not music's socket, so the isolation contract
    // holds — no music-code reference.)
    var SYSTEM_STATS_URL = '/api/system/stats';
    var systemPollTimer = null;

    // Fallback only — shown if the /api/video/dashboard call fails. (uptime/memory
    // are NOT here — they come from the shared /api/system/stats via loadSystemStats.)
    var FALLBACK_STATS = {
        'active-downloads': '0',
        'finished-downloads': '0',
        'download-speed': '0 KB/s',
        'disk-usage': '--',
        'movies': '0',
        'shows': '0',
        'episodes': '0',
        'library-size': '--'
    };

    function formatBytes(n) {
        n = Number(n) || 0;
        if (n <= 0) return '0 B';
        var units = ['B', 'KB', 'MB', 'GB', 'TB', 'PB'];
        var i = Math.floor(Math.log(n) / Math.log(1024));
        if (i >= units.length) i = units.length - 1;
        return (n / Math.pow(1024, i)).toFixed(i === 0 ? 0 : 1) + ' ' + units[i];
    }

    function formatSpeed(bps) {
        return formatBytes(bps) + '/s';
    }

    // Map the API payload onto the flat data-video-stat keys in the markup.
    function flatten(d) {
        var lib = d.library || {}, dl = d.downloads || {};
        return {
            'active-downloads': String(dl.active != null ? dl.active : 0),
            'finished-downloads': String(dl.finished != null ? dl.finished : 0),
            'download-speed': formatSpeed(dl.speed_bps),
            'disk-usage': formatBytes(lib.size_bytes),
            'movies': String(lib.movies != null ? lib.movies : 0),
            'shows': String(lib.shows != null ? lib.shows : 0),
            'episodes': String(lib.episodes != null ? lib.episodes : 0),
            'library-size': formatBytes(lib.size_bytes)
        };
    }

    function applyStats(stats) {
        var nodes = document.querySelectorAll('[data-video-stat]');
        for (var i = 0; i < nodes.length; i++) {
            var key = nodes[i].getAttribute('data-video-stat');
            if (Object.prototype.hasOwnProperty.call(stats, key)) {
                nodes[i].textContent = stats[key];
            }
        }
    }

    function applyBadges(d) {
        var nodes = document.querySelectorAll('[data-video-badge]');
        for (var i = 0; i < nodes.length; i++) {
            var key = nodes[i].getAttribute('data-video-badge');
            if (d[key] != null) nodes[i].textContent = String(d[key]);
        }
    }

    function _esc(s) {
        return String(s == null ? '' : s).replace(/[&<>"]/g, function (c) {
            return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c];
        });
    }
    // Render the "Recently Added" tiles (poster + title), newest first, each linking
    // to its detail page.
    function applyRecent(items) {
        var host = document.querySelector('[data-video-recent]');
        if (!host) return;
        items = items || [];
        if (!items.length) {
            host.innerHTML = '<div class="video-recent-empty">Nothing added yet.</div>';
            return;
        }
        host.innerHTML = items.map(function (it) {
            var href = '/video-detail/library/' + it.kind + '/' + it.id;
            var poster = '/api/video/poster/' + it.kind + '/' + it.id + '?w=160';
            return '<a class="video-recent-item" href="' + href + '" title="' + _esc(it.title) + '"' +
                ' data-video-card-open="' + _esc(it.kind) + '" data-video-card-id="' + it.id + '">' +
                '<div class="video-recent-poster"><img src="' + poster + '" alt="" loading="lazy" ' +
                'onerror="this.closest(\'.video-recent-poster\').classList.add(\'is-empty\')"></div>' +
                '<div class="video-recent-title">' + _esc(it.title) + '</div>' +
                (it.year ? '<div class="video-recent-year">' + _esc(it.year) + '</div>' : '') +
                '</a>';
        }).join('');
    }

    // ── Upcoming (calendar preview) — mini-billboards for the next few episodes ──
    var CALENDAR_URL = '/api/video/calendar?days=21&scope=watchlist';
    var _WD = ['Sunday', 'Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday'];
    function _parseISO(s) { var p = String(s || '').split('-'); return new Date(+p[0], (+p[1] || 1) - 1, +p[2] || 1); }
    function _whenLabel(airDate, today) {
        var diff = Math.round((_parseISO(airDate) - _parseISO(today)) / 86400000);
        if (diff <= 0) return 'Today';
        if (diff === 1) return 'Tomorrow';
        if (diff < 7) return _WD[_parseISO(airDate).getDay()];
        return _parseISO(airDate).toLocaleDateString(undefined, { month: 'short', day: 'numeric' });
    }
    function loadUpcoming() {
        var host = document.querySelector('[data-video-upcoming]');
        if (!host) return;
        fetch(CALENDAR_URL, { headers: { 'Accept': 'application/json' } })
            .then(function (r) { return r.ok ? r.json() : null; })
            .then(function (d) {
                if (!d || d.error) { host.innerHTML = '<p class="video-empty-note">Couldn\'t load the calendar.</p>'; return; }
                var eps = (d.episodes || []).slice().sort(function (a, b) {
                    return a.air_date < b.air_date ? -1 : a.air_date > b.air_date ? 1
                        : (a.episode_number || 0) - (b.episode_number || 0);
                }).slice(0, 4);
                if (!eps.length) { host.innerHTML = '<p class="video-empty-note">Nothing airing soon — follow some shows on the Watchlist.</p>'; return; }
                host.innerHTML = eps.map(function (ep) {
                    var bg = ep.show_has_backdrop ? '/api/video/backdrop/show/' + ep.show_id + '?w=640' : '';
                    var se = 'S' + ep.season_number + ' · E' + ep.episode_number;
                    var owned = ep.has_file ? '<span class="vup-owned">✓ Owned</span>' : '';
                    return '<a class="vup-row" href="/video-detail/library/show/' + ep.show_id + '"' +
                        ' data-video-card-open="show" data-video-card-id="' + ep.show_id + '" title="' + _esc(ep.show_title) + '">' +
                        (bg ? '<div class="vup-bg" style="background-image:url(\'' + bg + '\')"></div>' : '') +
                        '<div class="vup-scrim"></div>' +
                        '<div class="vup-content">' +
                            '<div class="vup-when"><span class="vup-dot"></span>' + _esc(_whenLabel(ep.air_date, d.today)) + owned + '</div>' +
                            '<div class="vup-title">' + _esc(ep.show_title) + '</div>' +
                            '<div class="vup-sub">' + se + (ep.title ? ' · ' + _esc(ep.title) : '') + '</div>' +
                        '</div></a>';
                }).join('');
            })
            .catch(function () { host.innerHTML = '<p class="video-empty-note">Couldn\'t load the calendar.</p>'; });
    }

    function loadStats() {
        fetch(DASHBOARD_URL, { headers: { 'Accept': 'application/json' } })
            .then(function (r) { return r.ok ? r.json() : null; })
            .then(function (d) {
                if (d && !d.error) {
                    applyStats(flatten(d));
                    applyBadges(d);
                    applyRecent(d.recent);
                } else {
                    applyStats(FALLBACK_STATS);
                }
            })
            .catch(function () { applyStats(FALLBACK_STATS); });
    }

    // True when the video dashboard subpage is the one currently shown (subpages
    // toggle via the `hidden` attribute in video-side.js).
    function dashboardVisible() {
        var el = document.querySelector('.video-subpage[data-video-subpage="' + DASHBOARD_ID + '"]');
        return !!el && !el.hidden;
    }

    // Pull the shared system stats and reflect uptime + memory on the cards. The
    // rest of the dashboard's figures (video downloads, library) come from
    // /api/video/dashboard via loadStats(); only the machine-level numbers are
    // shared. applyStats only touches the keys we pass, so nothing else is clobbered.
    function loadSystemStats() {
        fetch(SYSTEM_STATS_URL, { headers: { 'Accept': 'application/json' } })
            .then(function (r) { return r.ok ? r.json() : null; })
            .then(function (d) {
                if (!d) return;
                applyStats({
                    'uptime': d.uptime != null ? String(d.uptime) : '--',
                    'memory': d.memory_usage != null ? String(d.memory_usage) : '--'
                });
            })
            .catch(function () { /* keep last-known values on a transient failure */ });
    }

    // Keep the system figures live while the dashboard is open — one 10s timer
    // that no-ops cheaply whenever the dashboard isn't the visible page.
    function startSystemStatsPolling() {
        if (systemPollTimer) return;
        systemPollTimer = setInterval(function () {
            if (dashboardVisible()) loadSystemStats();
        }, 10000);
    }

    function onPageShown(e) {
        if (!e || e.detail !== DASHBOARD_ID) return;
        loadStats();
        loadUpcoming();
        loadSystemStats();          // immediate fill (memory/uptime)
        startSystemStatsPolling();  // then keep it live
    }

    // ── Library card: live scan progress (parity with the music dashboard) ──
    // The scan buttons (data-video-scan-mode) are wired by video-scan.js; here
    // we reflect progress on the card and hydrate if a scan is already running
    // (video-scan.js re-emits the progress event on load).
    function dashButtons() {
        return document.querySelectorAll(
            '.video-subpage[data-video-subpage="video-dashboard"] [data-video-scan-mode]');
    }

    function onDashScanProgress(e) {
        var s = e.detail || {};
        if (s.state !== 'scanning') return;
        var prog = document.querySelector('[data-video-dash-progress]');
        if (prog) prog.classList.remove('hidden');
        var phase = (s.phase || 'scanning');
        var phaseEl = document.querySelector('[data-video-dash-phase]');
        if (phaseEl) phaseEl.textContent = phase.charAt(0).toUpperCase() + phase.slice(1);
        var bar = document.querySelector('[data-video-dash-bar]');
        if (bar) bar.style.width = (s.percent != null ? s.percent : 100) + '%';
        var detail = document.querySelector('[data-video-dash-detail]');
        if (detail) {
            detail.textContent = (s.movies || 0) + ' movies, ' + (s.shows || 0) + ' shows'
                + (s.percent != null ? ' · ' + s.percent + '%' : '');
        }
        var btns = dashButtons();
        for (var i = 0; i < btns.length; i++) btns[i].disabled = true;
    }

    function onDashScanDone() {
        var prog = document.querySelector('[data-video-dash-progress]');
        if (prog) prog.classList.add('hidden');
        var btns = dashButtons();
        for (var i = 0; i < btns.length; i++) btns[i].disabled = false;
        loadStats();
    }

    // Poster Manager quick-action tile → open the full-screen poster picker (its
    // own self-contained module). Delegated so it survives dashboard re-renders.
    document.addEventListener('click', function (e) {
        var t = e.target.closest && e.target.closest('[data-video-poster-manager]');
        if (!t) return;
        e.preventDefault();
        if (window.VideoPoster) VideoPoster.openSearch();
    });

    // Overlay Studio launcher → the full-bleed overlay-template editor.
    document.addEventListener('click', function (e) {
        var t = e.target.closest && e.target.closest('[data-video-overlay-studio]');
        if (!t) return;
        e.preventDefault();
        // Overlay Studio is admin-only (defense in depth behind the hidden launcher).
        if (typeof currentProfile !== 'undefined' && currentProfile && !currentProfile.is_admin) return;
        if (window.VideoOverlayEditor) VideoOverlayEditor.open();
    });

    // Recently Added tiles → SPA detail navigation (same contract as the library
    // grid): plain left-click routes in-app; modified clicks use the real href.
    document.addEventListener('click', function (e) {
        if (e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;
        var card = e.target.closest && e.target.closest(
            '[data-video-recent] [data-video-card-open], [data-video-upcoming] [data-video-card-open]');
        if (!card) return;
        e.preventDefault();
        document.dispatchEvent(new CustomEvent('soulsync:video-open-detail', {
            detail: { kind: card.getAttribute('data-video-card-open'),
                      id: parseInt(card.getAttribute('data-video-card-id'), 10), source: 'library' },
        }));
    });

    document.addEventListener('soulsync:video-page-shown', onPageShown);
    document.addEventListener('soulsync:video-scan-progress', onDashScanProgress);
    document.addEventListener('soulsync:video-scan-done', onDashScanDone);
})();
