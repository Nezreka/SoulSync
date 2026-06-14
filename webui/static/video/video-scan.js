/*
 * SoulSync — shared VIDEO scan controller (isolated).
 *
 * One place triggers + polls library scans for every surface (Library page,
 * Tools page, Dashboard card), so nothing duplicates the fetch/poll logic. It
 * reuses the music CSS/markup — it just targets /api/video/* and updates the
 * video DOM.
 *
 * Triggers:
 *   - click on [data-video-scan-mode="full|incremental|deep"] (or [data-video-scan] = full)
 *   - click on [data-video-scan-run], reading the mode from [data-video-scan-select]
 *   - a 'soulsync:video-scan-start' event {detail:{mode}}
 *
 * Updates the Tools progress widgets ([data-video-scan-phase|bar|detail]) and
 * emits 'soulsync:video-scan-progress' / 'soulsync:video-scan-done' so the
 * Library page and Dashboard can react too. Self-contained IIFE, no globals.
 */
(function () {
    'use strict';

    var REQUEST_URL = '/api/video/scan/request';
    var STATUS_URL = '/api/video/scan/status';
    var scanning = false;

    function emit(name, detail) {
        document.dispatchEvent(new CustomEvent(name, { detail: detail }));
    }

    function setText(sel, text) {
        var n = document.querySelector(sel);
        if (n) n.textContent = text;
    }

    function setBar(pct) {
        var bar = document.querySelector('[data-video-scan-bar]');
        if (bar) bar.style.width = pct + '%';
    }

    function counts(s) {
        return (s.movies || 0) + ' movies, ' + (s.shows || 0) + ' shows';
    }

    function formatSize(bytes) {
        bytes = Number(bytes) || 0;
        var mb = bytes / 1048576;
        if (mb >= 1048576) return (mb / 1048576).toFixed(2) + ' TB';
        if (mb >= 1024) return (mb / 1024).toFixed(2) + ' GB';
        return mb.toFixed(1) + ' MB';
    }

    // Populate the Tools card stat grid (Movies/Shows/Episodes/Size) from the
    // same dashboard endpoint — reuses existing data, no new API.
    function loadToolStats() {
        if (!document.querySelector('[data-video-scan-stat]')) return;
        fetch('/api/video/dashboard', { headers: { 'Accept': 'application/json' } })
            .then(function (r) { return r.ok ? r.json() : null; })
            .then(function (d) {
                if (!d || d.error) return;
                var lib = d.library || {};
                var set = function (k, v) {
                    var n = document.querySelector('[data-video-scan-stat="' + k + '"]');
                    if (n) n.textContent = v;
                };
                set('movies', lib.movies || 0);
                set('shows', lib.shows || 0);
                set('episodes', lib.episodes || 0);
                set('size', formatSize(lib.size_bytes));
                // Server-prefixed title, like music ("Plex Database Updater").
                var titleEl = document.querySelector('[data-video-scan-title]');
                if (titleEl && d.server) {
                    titleEl.textContent =
                        d.server.charAt(0).toUpperCase() + d.server.slice(1) + ' Library Scan';
                }
            })
            .catch(function () { /* leave defaults */ });
    }

    function setRunLabel(text) {
        var run = document.querySelector('[data-video-scan-run]');
        if (run) run.textContent = text;
    }

    function reflectProgress(s) {
        var phase = (s.phase || 'scanning');
        setText('[data-video-scan-phase]', phase.charAt(0).toUpperCase() + phase.slice(1));
        var detail = counts(s);
        if (s.percent != null) detail += ' · ' + s.percent + '%';
        setText('[data-video-scan-detail]', detail);
        // Real percentage when we know the total; otherwise a full bar as a
        // "working" indicator.
        setBar(s.percent != null ? s.percent : 100);
        setRunLabel('Cancel');                   // button doubles as cancel while scanning
    }

    function reflectDone(s) {
        setRunLabel('Scan Library');
        if (s.state === 'error') {
            setText('[data-video-scan-phase]', 'Failed');
            setText('[data-video-scan-detail]', s.error || 'Scan failed');
            setBar(0);
        } else if (s.state === 'cancelled') {
            setText('[data-video-scan-phase]', 'Cancelled');
            setText('[data-video-scan-detail]', counts(s) + ' (cancelled)');
            setBar(0);
            loadToolStats();
        } else {
            setText('[data-video-scan-phase]', 'Complete');
            setText('[data-video-scan-detail]',
                counts(s) + (s.removed ? ', ' + s.removed + ' removed' : ''));
            setBar(100);
            loadToolStats();
            try { setText('[data-video-scan-last]', new Date().toLocaleString()); } catch (e) { /* ignore */ }
        }
    }

    function poll() {
        fetch(STATUS_URL, { headers: { 'Accept': 'application/json' } })
            .then(function (r) { return r.json(); })
            .then(function (s) {
                if (s && s.state === 'scanning') {
                    reflectProgress(s);
                    emit('soulsync:video-scan-progress', s);
                    setTimeout(poll, 1500);
                } else {
                    scanning = false;
                    reflectDone(s || { state: 'idle' });
                    emit('soulsync:video-scan-done', s || { state: 'idle' });
                }
            })
            .catch(function () {
                scanning = false;
                reflectDone({ state: 'error', error: 'Could not reach server' });
                emit('soulsync:video-scan-done', { state: 'error' });
            });
    }

    function start(mode) {
        if (scanning) return;
        scanning = true;
        var pending = { state: 'scanning', phase: 'starting', mode: mode, movies: 0, shows: 0, percent: 0 };
        reflectProgress(pending);
        emit('soulsync:video-scan-progress', pending);
        fetch(REQUEST_URL, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json', 'Accept': 'application/json' },
            body: JSON.stringify({ mode: mode })
        })
            .then(function () { setTimeout(poll, 600); })
            .catch(function () {
                scanning = false;
                reflectDone({ state: 'error' });
                emit('soulsync:video-scan-done', { state: 'error' });
            });
    }

    function stop() {
        setText('[data-video-scan-phase]', 'Cancelling…');
        fetch('/api/video/scan/stop', { method: 'POST', headers: { 'Accept': 'application/json' } })
            .catch(function () { /* poll will reconcile */ });
    }

    // Rehydrate after a page refresh: the scan keeps running server-side, so if
    // one is in progress, restore the UI (Cancel button, moving bar, phase) and
    // resume polling — parity with the music tools.
    function resumeIfScanning() {
        if (scanning) return;
        fetch(STATUS_URL, { headers: { 'Accept': 'application/json' } })
            .then(function (r) { return r.json(); })
            .then(function (s) {
                if (s && s.state === 'scanning' && !scanning) {
                    scanning = true;
                    reflectProgress(s);
                    emit('soulsync:video-scan-progress', s);
                    setTimeout(poll, 1200);
                }
            })
            .catch(function () { /* ignore */ });
    }

    function init() {
        var triggers = document.querySelectorAll('[data-video-scan-mode],[data-video-scan]');
        for (var i = 0; i < triggers.length; i++) {
            (function (el) {
                el.addEventListener('click', function (e) {
                    e.preventDefault();
                    start(el.getAttribute('data-video-scan-mode') || 'full');
                });
            })(triggers[i]);
        }
        var run = document.querySelector('[data-video-scan-run]');
        if (run) {
            run.addEventListener('click', function (e) {
                e.preventDefault();
                if (scanning) { stop(); return; }   // button doubles as Cancel mid-scan
                var sel = document.querySelector('[data-video-scan-select]');
                start(sel ? sel.value : 'full');
            });
        }
        document.addEventListener('soulsync:video-scan-start', function (e) {
            start((e.detail && e.detail.mode) || 'full');
        });
        document.addEventListener('soulsync:video-page-shown', function (e) {
            if (e && e.detail === 'video-tools') { loadToolStats(); resumeIfScanning(); }
        });
        // On load: if a scan is already running (page was refreshed mid-scan),
        // restore the live state instead of showing Idle.
        resumeIfScanning();
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }
})();
