/*
 * SoulSync — Video Tools page (isolated).
 *
 * The scan buttons (data-video-scan-mode) are wired by video-scan.js; this
 * module just reflects scan progress/result into the tool card's status line
 * and disables the buttons while a scan runs. Self-contained IIFE, no globals.
 */
(function () {
    'use strict';

    function statusNode() {
        return document.querySelector('[data-video-tools-scan-status]');
    }

    function setStatus(text) {
        var n = statusNode();
        if (n) n.textContent = text;
    }

    function setButtonsDisabled(disabled) {
        var btns = document.querySelectorAll(
            '[data-video-subpage="video-tools"] [data-video-scan-mode]');
        for (var i = 0; i < btns.length; i++) btns[i].disabled = disabled;
    }

    function onProgress(e) {
        var s = e.detail || {};
        if (s.state !== 'scanning') return;
        setButtonsDisabled(true);
        setStatus((s.phase || 'Scanning') + '… '
            + (s.movies || 0) + ' movies, ' + (s.shows || 0) + ' shows');
    }

    function onDone(e) {
        var s = e.detail || {};
        setButtonsDisabled(false);
        if (s.state === 'error') {
            setStatus('Failed' + (s.error ? ': ' + s.error : ''));
        } else {
            var msg = 'Done — ' + (s.movies || 0) + ' movies, ' + (s.shows || 0) + ' shows';
            if (s.removed) msg += ', ' + s.removed + ' removed';
            setStatus(msg);
        }
    }

    function init() {
        document.addEventListener('soulsync:video-scan-progress', onProgress);
        document.addEventListener('soulsync:video-scan-done', onDone);
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }
})();
