/*
 * SoulSync — Video side shell controller.
 *
 * ISOLATION CONTRACT: the music side never imports or references anything here.
 * This file is a self-contained IIFE (no globals) wired entirely via
 * addEventListener (no inline onclick), so it cannot affect the music side and
 * a merge can't touch it. It only drives shared SHELL behaviour:
 *   - the Music ↔ Video header toggle (+ remembers the side in localStorage)
 *   - showing/hiding the video sidebar nav vs the music nav (CSS does the work
 *     off body[data-side]; this just flips the attribute)
 *   - a placeholder content host for the video pages (real pages land later)
 *
 * The actual video domain (data model, services, pages, DB) lives elsewhere and
 * is built on top of this shell.
 */
(function () {
    'use strict';

    var SIDE_KEY = 'soulsync_side';
    var MUSIC_SUBTITLE = 'Music Sync & Manager';
    var VIDEO_SUBTITLE = 'Movies, TV & YouTube';
    var DEFAULT_VIDEO_PAGE = 'video-dashboard';

    // The video sidebar pages. Pages flagged shared: true are "same as music"
    // (Import / Issues / Help) — wired to reuse the music pages in a later step;
    // for now every page renders the placeholder.
    var VIDEO_PAGES = [
        { id: 'video-dashboard', label: 'Dashboard' },
        { id: 'video-search', label: 'Search' },
        { id: 'video-discover', label: 'Discover' },
        { id: 'video-library', label: 'Library' },
        { id: 'video-calendar', label: 'Calendar' },
        { id: 'video-import', label: 'Import', shared: true },
        { id: 'video-settings', label: 'Settings' },
        { id: 'video-issues', label: 'Issues', shared: true },
        { id: 'video-help', label: 'Help & Docs', shared: true },
    ];

    function readSide() {
        try {
            return localStorage.getItem(SIDE_KEY) === 'video' ? 'video' : 'music';
        } catch (e) {
            return 'music';
        }
    }

    function persistSide(side) {
        try { localStorage.setItem(SIDE_KEY, side); } catch (e) { /* ignore */ }
    }

    function pageMeta(pageId) {
        for (var i = 0; i < VIDEO_PAGES.length; i++) {
            if (VIDEO_PAGES[i].id === pageId) return VIDEO_PAGES[i];
        }
        return VIDEO_PAGES[0];
    }

    function showVideoPage(pageId) {
        var meta = pageMeta(pageId);
        var navButtons = document.querySelectorAll('.video-nav .nav-button[data-video-page]');
        for (var i = 0; i < navButtons.length; i++) {
            navButtons[i].classList.toggle(
                'active', navButtons[i].getAttribute('data-video-page') === meta.id);
        }
        var host = document.getElementById('video-page-host');
        if (!host) return;
        // Built from our own static constants only — no user input.
        var shell = document.createElement('div');
        shell.className = 'page-shell video-placeholder';
        var h2 = document.createElement('h2');
        h2.className = 'header-title';
        var span = document.createElement('span');
        span.textContent = 'Video · ' + meta.label;
        h2.appendChild(span);
        var note = document.createElement('p');
        note.className = 'video-placeholder-note';
        note.textContent = 'The ' + meta.label + ' page for the video side is coming soon.';
        host.textContent = '';
        host.appendChild(h2);
        host.appendChild(note);
    }

    function applySide(side) {
        document.body.setAttribute('data-side', side);
        var subtitle = document.querySelector('.sidebar-header .app-subtitle');
        if (subtitle) subtitle.textContent = side === 'video' ? VIDEO_SUBTITLE : MUSIC_SUBTITLE;
        var toggleButtons = document.querySelectorAll('.side-toggle-btn');
        for (var i = 0; i < toggleButtons.length; i++) {
            toggleButtons[i].classList.toggle(
                'active', toggleButtons[i].getAttribute('data-side-target') === side);
        }
        if (side === 'video') {
            var active = document.querySelector('.video-nav .nav-button.active');
            showVideoPage(active ? active.getAttribute('data-video-page') : DEFAULT_VIDEO_PAGE);
        }
    }

    function switchSide(side) {
        if (side !== 'music' && side !== 'video') return;
        persistSide(side);
        applySide(side);
    }

    function init() {
        var toggleButtons = document.querySelectorAll('.side-toggle-btn');
        for (var i = 0; i < toggleButtons.length; i++) {
            (function (btn) {
                btn.addEventListener('click', function () {
                    switchSide(btn.getAttribute('data-side-target'));
                });
            })(toggleButtons[i]);
        }

        var navButtons = document.querySelectorAll('.video-nav .nav-button[data-video-page]');
        for (var j = 0; j < navButtons.length; j++) {
            (function (btn) {
                btn.addEventListener('click', function (e) {
                    e.preventDefault();
                    showVideoPage(btn.getAttribute('data-video-page'));
                });
            })(navButtons[j]);
        }

        var defaultNav = document.querySelector(
            '.video-nav .nav-button[data-video-page="' + DEFAULT_VIDEO_PAGE + '"]');
        if (defaultNav) defaultNav.classList.add('active');

        applySide(readSide());
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }
})();
