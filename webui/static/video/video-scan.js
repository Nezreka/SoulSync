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

    function reflectProgress(s) {
        var phase = (s.phase || 'scanning');
        setText('[data-video-scan-phase]', phase.charAt(0).toUpperCase() + phase.slice(1));
        setText('[data-video-scan-detail]', counts(s));
        setBar(100);
    }

    function reflectDone(s) {
        var run = document.querySelector('[data-video-scan-run]');
        if (run) run.disabled = false;
        if (s.state === 'error') {
            setText('[data-video-scan-phase]', 'Failed');
            setText('[data-video-scan-detail]', s.error || 'Scan failed');
            setBar(0);
        } else {
            setText('[data-video-scan-phase]', 'Complete');
            setText('[data-video-scan-detail]',
                counts(s) + (s.removed ? ', ' + s.removed + ' removed' : ''));
            setBar(100);
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
        var run = document.querySelector('[data-video-scan-run]');
        if (run) run.disabled = true;
        var pending = { state: 'scanning', phase: 'starting', mode: mode, movies: 0, shows: 0 };
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
                var sel = document.querySelector('[data-video-scan-select]');
                start(sel ? sel.value : 'full');
            });
        }
        document.addEventListener('soulsync:video-scan-start', function (e) {
            start((e.detail && e.detail.mode) || 'full');
        });
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }
})();
