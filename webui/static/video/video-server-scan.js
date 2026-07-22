/* Video Tools — Server Scan card.
 *
 * DISTINCT from video-scan.js (the Library Scan, where SoulSync READS the server
 * into video.db). This one tells the media server (Plex/Jellyfin) to rescan its
 * OWN folders so newly-downloaded files get indexed — the manual twin of the
 * post-download "Scan Video Server" automation.
 *
 * Backend:
 *   POST /api/video/scan/server          {media_type} -> trigger ({ok, sections} | {ok:false,error})
 *   GET  /api/video/scan/server/status   ?media_type   -> {scanning: true|false|null}
 *
 * Self-contained IIFE, no globals; mirrors the music/library-scan live-status UX.
 * media_type targets Movies / TV / both, like the Library Scan card.
 */
(function () {
    var TRIGGER_URL = '/api/video/scan/server';
    var STATUS_URL = '/api/video/scan/server/status';
    var scanning = false;
    var pollTimer = null;

    function $(sel) { return document.querySelector(sel); }
    function setText(sel, t) { var n = $(sel); if (n) n.textContent = t; }
    function setBar(p) { var b = $('[data-video-srvscan-bar]'); if (b) b.style.width = p + '%'; }
    function setRunLabel(t) { var r = $('[data-video-srvscan-run]'); if (r) r.textContent = t; }

    function targetValue() {
        var t = $('[data-video-srvscan-target]');
        return t ? t.value : 'all';
    }
    function targetLabel(mt) {
        return mt === 'movie' ? 'Movies' : (mt === 'show' ? 'TV' : 'Movies + TV');
    }

    function stopPoll() { if (pollTimer) { clearTimeout(pollTimer); pollTimer = null; } }

    function done(phase, detail) {
        scanning = false;
        stopPoll();
        setRunLabel('Scan Server');
        setText('[data-video-srvscan-phase]', phase);
        setText('[data-video-srvscan-detail]', detail || '');
        setBar(phase === 'Complete' ? 100 : 0);
    }

    // The server scan has no percentage (Plex doesn't report one), so the bar is a
    // full-width "working" indicator while scanning, then settles on complete.
    function poll(mt) {
        fetch(STATUS_URL + '?media_type=' + encodeURIComponent(mt), { headers: { 'Accept': 'application/json' } })
            .then(function (r) { return r.json(); })
            .then(function (s) {
                if (!s || s.scanning === false) { done('Complete', targetLabel(mt) + ' indexed'); return; }
                if (s.scanning === null) { done('Scan started', "Server can't report progress"); return; }
                setText('[data-video-srvscan-phase]', 'Scanning…');
                setText('[data-video-srvscan-detail]', 'Server is indexing ' + targetLabel(mt) + '…');
                setBar(100);
                pollTimer = setTimeout(function () { poll(mt); }, 2000);
            })
            .catch(function () { done('Idle', ''); });
    }

    function start() {
        if (scanning) return;
        var mt = targetValue();
        scanning = true;
        setRunLabel('Scanning…');
        setText('[data-video-srvscan-phase]', 'Starting…');
        setText('[data-video-srvscan-detail]', 'Asking the server to scan ' + targetLabel(mt) + '…');
        setBar(100);
        fetch(TRIGGER_URL, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json', 'Accept': 'application/json' },
            body: JSON.stringify({ media_type: mt })
        })
            .then(function (r) { return r.json(); })
            .then(function (res) {
                if (res && res.ok === false) { done('Failed', res.error || 'Server scan failed'); return; }
                pollTimer = setTimeout(function () { poll(mt); }, 800);
            })
            .catch(function () { done('Failed', 'Could not reach server'); });
    }

    // Server-prefixed title to match the Library Scan card ("Plex Server Scan").
    function loadTitle() {
        var titleEl = $('[data-video-srvscan-title]');
        if (!titleEl) return;
        fetch('/api/video/dashboard', { headers: { 'Accept': 'application/json' } })
            .then(function (r) { return r.ok ? r.json() : null; })
            .then(function (d) {
                if (d && d.server) {
                    titleEl.textContent = d.server.charAt(0).toUpperCase() + d.server.slice(1) + ' Server Scan';
                }
            })
            .catch(function () { /* leave default */ });
    }

    // If the server is already mid-scan when the page opens, restore the live UI.
    function resumeIfScanning() {
        if (scanning) return;
        var mt = targetValue();
        fetch(STATUS_URL + '?media_type=' + encodeURIComponent(mt), { headers: { 'Accept': 'application/json' } })
            .then(function (r) { return r.json(); })
            .then(function (s) {
                if (s && s.scanning === true && !scanning) {
                    scanning = true;
                    setRunLabel('Scanning…');
                    setText('[data-video-srvscan-phase]', 'Scanning…');
                    setBar(100);
                    pollTimer = setTimeout(function () { poll(mt); }, 1200);
                }
            })
            .catch(function () { /* ignore */ });
    }

    function init() {
        var run = $('[data-video-srvscan-run]');
        if (run) {
            run.addEventListener('click', function (e) { e.preventDefault(); start(); });
        }
        document.addEventListener('soulsync:video-page-shown', function (e) {
            if (e && e.detail === 'video-tools') { loadTitle(); resumeIfScanning(); }
        });
        loadTitle();
        resumeIfScanning();
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }
})();
