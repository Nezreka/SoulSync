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
        { id: 'video-watchlist', label: 'Watchlist' },
        { id: 'video-wishlist', label: 'Wishlist' },
        { id: 'video-downloads', label: 'Downloads' },
        { id: 'video-calendar', label: 'Calendar' },
        { id: 'video-tools', label: 'Tools' },
        { id: 'video-import', label: 'Import', shared: true },
        { id: 'video-settings', label: 'Settings' },
        { id: 'video-issues', label: 'Issues', shared: true },
        { id: 'video-help', label: 'Help & Docs', shared: true },
        // Drill-in detail pages — reachable from cards, not the sidebar nav.
        { id: 'video-show-detail', label: 'Show' },
        { id: 'video-movie-detail', label: 'Movie' },
    ];

    // "Shared" video pages reuse the REAL music page (shown identically on the
    // video side for now) instead of a video subpage: video page id -> music
    // page id. CSS reveals the music page; we trigger its loader once shown.
    var SHARED_PAGES = { 'video-settings': 'settings' };

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

    function setActiveNav(pageId) {
        var navButtons = document.querySelectorAll('.video-nav .nav-button[data-video-page]');
        for (var i = 0; i < navButtons.length; i++) {
            navButtons[i].classList.toggle(
                'active', navButtons[i].getAttribute('data-video-page') === pageId);
        }
    }

    function renderPlaceholder(slot, meta) {
        // Built from our own static constants only — no user input.
        var h2 = document.createElement('h2');
        h2.className = 'header-title';
        var span = document.createElement('span');
        span.textContent = 'Video · ' + meta.label;
        h2.appendChild(span);
        var note = document.createElement('p');
        note.className = 'video-placeholder-note';
        note.textContent = 'The ' + meta.label + ' page for the video side is coming soon.';
        slot.textContent = '';
        slot.appendChild(h2);
        slot.appendChild(note);
    }

    // Show one video page: reveal its built .video-subpage if one exists, else
    // fall back to the placeholder slot. Then announce it so per-page data
    // modules (e.g. video-dashboard.js) can populate themselves — they listen
    // for this event instead of being called directly, keeping each isolated.
    function showPage(pageId) {
        var meta = pageMeta(pageId);
        // Drives the CSS that reveals shared music pages (e.g. Settings) and
        // hides the video host for them.
        document.body.setAttribute('data-video-page', meta.id);

        var sharedMusicId = SHARED_PAGES[meta.id];
        if (sharedMusicId) {
            // The real music page is shown by CSS; load its data the same way a
            // music-side navigation would. (loadPageData is a shared global.)
            if (typeof loadPageData === 'function') loadPageData(sharedMusicId);
            document.dispatchEvent(new CustomEvent('soulsync:video-page-shown', { detail: meta.id }));
            return;
        }

        var host = document.getElementById('video-page-host');
        if (!host) return;
        var matched = null;
        var subpages = host.querySelectorAll('.video-subpage');
        for (var i = 0; i < subpages.length; i++) {
            var isMatch = subpages[i].getAttribute('data-video-subpage') === meta.id;
            subpages[i].hidden = !isMatch;
            if (isMatch) matched = subpages[i];
        }
        var slot = document.getElementById('video-placeholder-slot');
        if (slot) {
            slot.hidden = !!matched;
            if (!matched) renderPlaceholder(slot, meta);
        }
        document.dispatchEvent(new CustomEvent('soulsync:video-page-shown', { detail: meta.id }));
    }

    function navigate(pageId) {
        setActiveNav(pageId);
        showPage(pageId);
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
            navigate(active ? active.getAttribute('data-video-page') : DEFAULT_VIDEO_PAGE);
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
                    navigate(btn.getAttribute('data-video-page'));
                });
            })(navButtons[j]);
        }

        // In-page jumps (e.g. dashboard Quick Action tiles) navigate the same
        // way as the sidebar nav, via data-video-goto. No inline onclick.
        var gotos = document.querySelectorAll('[data-video-goto]');
        for (var k = 0; k < gotos.length; k++) {
            (function (el) {
                el.addEventListener('click', function (e) {
                    e.preventDefault();
                    navigate(el.getAttribute('data-video-goto'));
                });
            })(gotos[k]);
        }

        // Drill-in: a card fires soulsync:video-open-detail {kind, id}. We just
        // navigate to the matching detail subpage; video-detail.js (listening to
        // the same event) loads the data. Keeps the two concerns decoupled.
        document.addEventListener('soulsync:video-open-detail', function (e) {
            var kind = e && e.detail && e.detail.kind;
            if (kind === 'movie') navigate('video-movie-detail');
            else if (kind === 'show') navigate('video-show-detail');
        });

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
