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

    // Zeroed placeholder until the video DB is wired. Keys match the markup's
    // data-video-stat attributes.
    var STUB_STATS = {
        'active-downloads': '0',
        'finished-downloads': '0',
        'download-speed': '0 KB/s',
        'disk-usage': '--',
        'uptime': '0m',
        'memory': '--',
        'movies': '0',
        'shows': '0',
        'episodes': '0',
        'library-size': '--'
    };

    function applyStats(stats) {
        var nodes = document.querySelectorAll('[data-video-stat]');
        for (var i = 0; i < nodes.length; i++) {
            var key = nodes[i].getAttribute('data-video-stat');
            if (Object.prototype.hasOwnProperty.call(stats, key)) {
                nodes[i].textContent = stats[key];
            }
        }
    }

    function loadStats() {
        // TODO(video.db): replace with fetch('/api/video/dashboard') -> applyStats(json).
        applyStats(STUB_STATS);
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

    document.addEventListener('soulsync:video-page-shown', onPageShown);
})();
