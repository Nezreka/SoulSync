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

    // Fallback only — shown if the API call fails. (uptime/memory aren't in the
    // video payload yet; they stay at their markup defaults for now.)
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

    function loadStats() {
        fetch(DASHBOARD_URL, { headers: { 'Accept': 'application/json' } })
            .then(function (r) { return r.ok ? r.json() : null; })
            .then(function (d) {
                if (d && !d.error) {
                    applyStats(flatten(d));
                    applyBadges(d);
                } else {
                    applyStats(FALLBACK_STATS);
                }
            })
            .catch(function () { applyStats(FALLBACK_STATS); });
    }

    // Service "Test" buttons are inert until the video services exist. Mark them
    // honestly rather than firing a no-op that looks broken.
    var testWired = false;
    function wireTestButtons() {
        if (testWired) return;
        testWired = true;
        var buttons = document.querySelectorAll('[data-video-test]');
        for (var i = 0; i < buttons.length; i++) {
            (function (btn) {
                btn.disabled = true;
                btn.title = 'Coming soon — video services not configured yet';
            })(buttons[i]);
        }
    }

    function onPageShown(e) {
        if (!e || e.detail !== DASHBOARD_ID) return;
        wireTestButtons();
        loadStats();
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

    document.addEventListener('soulsync:video-page-shown', onPageShown);
    document.addEventListener('soulsync:video-scan-progress', onDashScanProgress);
    document.addEventListener('soulsync:video-scan-done', onDashScanDone);
})();
