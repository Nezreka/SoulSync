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
 *   - a 'soulsync:video-scan-start' event {detail:{mode, media_type}} (the React
 *     tools page's Library Scan card sends this)
 *
 * Emits 'soulsync:video-scan-progress' / 'soulsync:video-scan-done' so the
 * Library page, the Dashboard and the tools card can follow along. it never
 * writes into the tools card: that card is React, and reads its own status.
 * Self-contained IIFE, no globals.
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
                emit('soulsync:video-scan-done', { state: 'error', error: 'Could not reach server' });
            });
    }

    function start(mode, mediaType) {
        if (scanning) return;
        scanning = true;
        mediaType = mediaType || 'all';      // 'all' | 'movie' | 'show'
        var pending = { state: 'scanning', phase: 'starting', mode: mode,
                        media_type: mediaType, movies: 0, shows: 0, percent: 0 };
        emit('soulsync:video-scan-progress', pending);
        fetch(REQUEST_URL, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json', 'Accept': 'application/json' },
            body: JSON.stringify({ mode: mode, media_type: mediaType })
        })
            .then(function () { setTimeout(poll, 600); })
            .catch(function () {
                scanning = false;
                emit('soulsync:video-scan-done', { state: 'error' });
            });
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
        document.addEventListener('soulsync:video-scan-start', function (e) {
            start((e.detail && e.detail.mode) || 'full', (e.detail && e.detail.media_type) || 'all');
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
