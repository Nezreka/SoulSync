/*
 * SoulSync — shared VIDEO scan controller (isolated).
 *
 * One place that triggers + polls library scans, so the Library page, the Tools
 * page, and the Dashboard card don't each duplicate the fetch/poll logic. Other
 * modules stay decoupled: they just listen for the events below.
 *
 * Wires any element with data-video-scan-mode="full|incremental|deep" (or
 * data-video-scan, = full) to start a scan on click. Also starts on a
 * 'soulsync:video-scan-start' event {detail:{mode}}.
 *
 * Emits:
 *   soulsync:video-scan-progress {detail: <status>}  (repeatedly while scanning)
 *   soulsync:video-scan-done     {detail: <status>}  (once, on finish/error)
 *
 * Self-contained IIFE, no globals, no inline handlers, under static/video/.
 */
(function () {
    'use strict';

    var REQUEST_URL = '/api/video/scan/request';
    var STATUS_URL = '/api/video/scan/status';
    var scanning = false;

    function emit(name, detail) {
        document.dispatchEvent(new CustomEvent(name, { detail: detail }));
    }

    function poll() {
        fetch(STATUS_URL, { headers: { 'Accept': 'application/json' } })
            .then(function (r) { return r.json(); })
            .then(function (s) {
                if (s && s.state === 'scanning') {
                    emit('soulsync:video-scan-progress', s);
                    setTimeout(poll, 1500);
                } else {
                    scanning = false;
                    emit('soulsync:video-scan-done', s || { state: 'idle' });
                }
            })
            .catch(function () {
                scanning = false;
                emit('soulsync:video-scan-done', { state: 'error' });
            });
    }

    function start(mode) {
        if (scanning) return;
        scanning = true;
        emit('soulsync:video-scan-progress',
            { state: 'scanning', phase: 'starting', mode: mode, movies: 0, shows: 0 });
        fetch(REQUEST_URL, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json', 'Accept': 'application/json' },
            body: JSON.stringify({ mode: mode })
        })
            .then(function () { setTimeout(poll, 600); })
            .catch(function () {
                scanning = false;
                emit('soulsync:video-scan-done', { state: 'error' });
            });
    }

    function wire() {
        var els = document.querySelectorAll('[data-video-scan-mode],[data-video-scan]');
        for (var i = 0; i < els.length; i++) {
            (function (el) {
                el.addEventListener('click', function (e) {
                    e.preventDefault();
                    start(el.getAttribute('data-video-scan-mode') || 'full');
                });
            })(els[i]);
        }
    }

    function init() {
        wire();
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
