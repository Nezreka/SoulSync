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

    // Captured at SCRIPT-EVAL time — before music's router boots (on
    // DOMContentLoaded) and may rewrite an unknown /video-detail/... URL to
    // /dashboard. This is the real path the user reloaded/deep-linked.
    var BOOT_PATH = window.location.pathname;

    var SIDE_KEY = 'soulsync_side';
    var MUSIC_SUBTITLE = 'Music Sync & Manager';
    var VIDEO_SUBTITLE = 'Movies, TV & YouTube';
    var DEFAULT_VIDEO_PAGE = 'video-dashboard';

    // ── URL routing — REAL links, mirroring music's /artist-detail/<source>/<id>.
    // A drill-in is a genuine <a href="/video-detail/<source>/<kind>/<id>"> so
    // reload / Back / Forward / open-in-new-tab all work. ``source`` is 'library'
    // (a video.db id) today; 'tmdb' (a search result not yet in the library) later.
    var DETAIL_BASE = '/video-detail/';
    var DETAIL_PAGES = { 'video-show-detail': 1, 'video-movie-detail': 1, 'video-person-detail': 1 };

    function buildDetailPath(source, kind, id) {
        return DETAIL_BASE + encodeURIComponent(source || 'library') + '/' + kind + '/' + encodeURIComponent(id);
    }
    function parseDetailPath(pathname) {
        if (!pathname || pathname.indexOf(DETAIL_BASE) !== 0) return null;
        var p = pathname.slice(DETAIL_BASE.length).split('/').filter(Boolean);
        if (p.length < 3) return null;
        var kind = p[1];
        if (kind === 'channel') {   // YouTube channel ids are strings (UC…), not numeric
            return { source: decodeURIComponent(p[0]), kind: kind, id: decodeURIComponent(p[2]) };
        }
        var id = parseInt(p[2], 10);
        if ((kind !== 'movie' && kind !== 'show' && kind !== 'person') || isNaN(id)) return null;
        return { source: decodeURIComponent(p[0]), kind: kind, id: id };
    }
    // Restore a detail from the URL (popstate / initial load) WITHOUT re-pushing.
    function restoreDetail(r) {
        if (readSide() !== 'video') { persistSide('video'); applySide('video'); }
        document.dispatchEvent(new CustomEvent('soulsync:video-open-detail',
            { detail: { kind: r.kind, id: r.id, source: r.source, _restore: true } }));
    }

    // ── Smart back button (mirrors music's artist-detail) ─────────────────────
    // Browser history does the real (multi-layer) navigation; this stack only
    // tracks WHERE EACH detail layer was opened FROM, so the button can label
    // itself ("← Back to Search" / "← Back to The Bear") and so backing out of
    // the first layer returns to the right page — not always the library. Each
    // entry: {type:'page', pageId} or {type:'detail', label}. The pushed history
    // state carries its layer depth so browser Back stays in sync too.
    var _backStack = [];

    function detailTitleOf(pageId) {
        if (pageId === 'video-person-detail') {
            var n = document.querySelector('[data-video-person] [data-vp-name]');
            return n ? (n.textContent || '').trim() : '';
        }
        var host = pageId === 'video-movie-detail' ? '[data-video-detail="movie"]' : '[data-video-detail="show"]';
        var t = document.querySelector(host + ' [data-vd-title]');
        return t ? (t.textContent || '').trim() : '';
    }
    function currentOrigin() {
        var page = document.body.getAttribute('data-video-page');
        if (DETAIL_PAGES[page]) return { type: 'detail', label: detailTitleOf(page) };
        return { type: 'page', pageId: page };
    }
    function backLabelText() {
        var top = _backStack[_backStack.length - 1];
        if (!top) return 'Back';
        if (top.type === 'detail') return top.label ? ('Back to ' + top.label) : 'Back';
        return 'Back to ' + pageMeta(top.pageId).label;
    }
    function updateBackLabels() {
        var text = backLabelText();
        var labels = document.querySelectorAll('[data-vd-back-label]');
        for (var i = 0; i < labels.length; i++) labels[i].textContent = text;
    }
    function backFallback() {
        // No browser history to pop (deep link) — go to the recorded first origin.
        var dest = (_backStack[0] && _backStack[0].type === 'page') ? _backStack[0].pageId : 'video-library';
        _backStack = [];
        navigate(dest);
        updateBackLabels();
    }

    function onPopState() {
        var r = parseDetailPath(window.location.pathname);
        if (r) {
            // Synced to the layer depth stamped on the history entry (handles both
            // our back button and the browser's Back).
            var st = window.history.state;
            var layer = (st && st.videoDetail && st.videoDetail.layer) || _backStack.length;
            if (_backStack.length > layer) _backStack.length = layer;
            restoreDetail(r);
            updateBackLabels();
            return;
        }
        // Left the detail URL — return to where the chain started, not always library.
        if (document.body.getAttribute('data-side') === 'video' &&
            DETAIL_PAGES[document.body.getAttribute('data-video-page')]) {
            var dest = (_backStack[0] && _backStack[0].type === 'page') ? _backStack[0].pageId : 'video-library';
            _backStack = [];
            navigate(dest);
            updateBackLabels();
        }
    }

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
        { id: 'video-person-detail', label: 'Person' },
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
        // Moving off a detail via the sidebar/nav drops the detail URL so a reload
        // doesn't re-open it (detail pages keep their own URL via pushState).
        if (!DETAIL_PAGES[pageId] && parseDetailPath(window.location.pathname)) {
            try { history.replaceState(null, '', '/'); } catch (e) { /* ignore */ }
        }
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
        if (side === 'music' && parseDetailPath(window.location.pathname)) {
            try { history.replaceState(null, '', '/'); } catch (e) { /* ignore */ }
        }
    }

    function init() {
        // Deep-linked detail path captured at eval time (music may already have
        // rewritten window.location to /dashboard by now).
        var bootDetail = parseDetailPath(BOOT_PATH);

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
                    _backStack = [];                 // sidebar nav is a fresh entry point
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
                    _backStack = [];                 // in-page jump is a fresh entry point
                    navigate(el.getAttribute('data-video-goto'));
                });
            })(gotos[k]);
        }

        // Drill-in: a card fires soulsync:video-open-detail {kind, id, source}. We
        // navigate to the matching detail subpage (video-detail.js loads the data)
        // and push a real URL — unless we're restoring from the URL (_restore).
        document.addEventListener('soulsync:video-open-detail', function (e) {
            var d = e && e.detail; if (!d) return;
            // Capture the origin BEFORE navigating away from the current page.
            var origin = (d._restore || d._replace) ? null : currentOrigin();
            if (d.kind === 'movie') navigate('video-movie-detail');
            else if (d.kind === 'show') navigate('video-show-detail');
            else if (d.kind === 'person') navigate('video-person-detail');
            else if (d.kind === 'channel') navigate('video-show-detail');   // channels reuse the show detail page
            else return;
            if (d._replace) {
                // A redirect (e.g. a tmdb link that's actually owned) → replace the
                // entry being redirected FROM, keeping its layer + origin, so Back
                // doesn't bounce back onto the redirecting URL.
                var rstate = { videoDetail: { kind: d.kind, id: d.id, source: d.source || 'library',
                                              layer: _backStack.length } };
                try { history.replaceState(rstate, '', buildDetailPath(d.source, d.kind, d.id)); } catch (e2) { /* ignore */ }
            } else if (!d._restore) {
                _backStack.push(origin);
                var state = { videoDetail: { kind: d.kind, id: d.id, source: d.source || 'library',
                                             layer: _backStack.length } };
                var path = buildDetailPath(d.source, d.kind, d.id);
                if (window.location.pathname !== path) history.pushState(state, '', path);
                else history.replaceState(state, '', path);
            }
            updateBackLabels();
        });

        // The detail back button is real browser Back (so it unwinds the whole
        // drill-in chain, layer by layer); only when there's no in-app history to
        // pop does it fall back to the recorded first origin.
        document.addEventListener('click', function (e) {
            var back = e.target.closest('[data-video-detail-back]');
            if (!back) return;
            e.preventDefault();
            if (window.history.length > 1 && window.history.state && window.history.state.videoDetail) {
                window.history.back();
            } else {
                backFallback();
            }
        });

        window.addEventListener('popstate', onPopState);

        var defaultNav = document.querySelector(
            '.video-nav .nav-button[data-video-page="' + DEFAULT_VIDEO_PAGE + '"]');
        if (defaultNav) defaultNav.classList.add('active');

        applySide(bootDetail ? 'video' : readSide());

        // Deep link / reload straight to a detail URL → restore it. Deferred to a
        // macrotask so EVERY script's DOMContentLoaded handler has registered
        // first (video-detail.js loads after us and must be listening for the
        // open-detail event), and so it lands AFTER music's initial routing — then
        // we re-assert the real URL it clobbered.
        if (bootDetail) {
            var bootPath = buildDetailPath(bootDetail.source, bootDetail.kind, bootDetail.id);
            var reassert = function () {
                // Re-assert the URL only while we're still showing this detail (so a
                // late async music redirect can't strand us on /dashboard) — never
                // fights real navigation away.
                if (DETAIL_PAGES[document.body.getAttribute('data-video-page')] &&
                    window.location.pathname !== bootPath) {
                    try { history.replaceState({ videoDetail: bootDetail }, '', bootPath); } catch (e) { /* ignore */ }
                }
            };
            setTimeout(function () {
                reassert();
                restoreDetail(bootDetail);
                [120, 350, 700].forEach(function (ms) { setTimeout(reassert, ms); });
            }, 0);
        }
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }
})();
