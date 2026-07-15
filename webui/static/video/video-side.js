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
    var DETAIL_PAGES = { 'video-show-detail': 1, 'video-movie-detail': 1, 'video-person-detail': 1,
        'video-studio-detail': 1 };

    function buildDetailPath(source, kind, id) {
        return DETAIL_BASE + encodeURIComponent(source || 'library') + '/' + kind + '/' + encodeURIComponent(id);
    }
    function parseDetailPath(pathname) {
        if (!pathname || pathname.indexOf(DETAIL_BASE) !== 0) return null;
        var p = pathname.slice(DETAIL_BASE.length).split('/').filter(Boolean);
        if (p.length < 3) return null;
        var kind = p[1];
        if (kind === 'channel' || kind === 'playlist') {   // YouTube ids are strings (UC… / PL…), not numeric
            return { source: decodeURIComponent(p[0]), kind: kind, id: decodeURIComponent(p[2]) };
        }
        var id = parseInt(p[2], 10);
        if ((kind !== 'movie' && kind !== 'show' && kind !== 'person' && kind !== 'studio') || isNaN(id)) return null;
        return { source: decodeURIComponent(p[0]), kind: kind, id: id };
    }
    // Restore a detail from the URL (popstate / initial load) WITHOUT re-pushing.
    function restoreDetail(r) {
        if (readSide() !== 'video') { persistSide('video'); applySide('video'); }
        document.dispatchEvent(new CustomEvent('soulsync:video-open-detail',
            { detail: { kind: r.kind, id: r.id, source: r.source, _restore: true } }));
    }

    // ── Top-level pages get a real URL too: '/' + pageId (e.g. /video-search) ──
    // Mirrors music's '/<page>' and our /video-detail/ scheme so every sidebar
    // option is deep-linkable (reload / Back / Forward / new-tab). Only a single
    // segment whose id is a real (non-detail) video page counts; detail URLs are
    // matched by parseDetailPath above and never collide.
    function isPageId(pageId) {
        if (!pageId || DETAIL_PAGES[pageId]) return false;
        for (var i = 0; i < VIDEO_PAGES.length; i++) {
            if (VIDEO_PAGES[i].id === pageId) return true;
        }
        return false;
    }
    function buildPagePath(pageId) { return '/' + pageId; }
    function parsePagePath(pathname) {
        if (!pathname) return null;
        var seg = pathname.replace(/^\/+|\/+$/g, '');
        if (!seg || seg.indexOf('/') >= 0) return null;   // single segment only
        return isPageId(seg) ? seg : null;
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
        if (pageId === 'video-studio-detail') {
            var s = document.querySelector('[data-video-studio] [data-vst-name]');
            return s ? (s.textContent || '').trim() : '';
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
        var path = window.location.pathname;
        var r = parseDetailPath(path);
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
        var pg = parsePagePath(path);
        if (pg) {
            // Back/Forward landed on a video page URL → ensure the video side, show
            // the page; the URL is already correct so don't re-push it.
            if (readSide() !== 'video') { persistSide('video'); applySide('video'); }
            navigate(pg, 'restore');
            _backStack = [];
            updateBackLabels();
            return;
        }
        // The URL is no longer a video path (Back crossed back into music). Hand the
        // side back so music's own router can show the page for this URL.
        if (document.body.getAttribute('data-side') === 'video') {
            persistSide('music');
            applySide('music');
            _backStack = [];
        }
    }

    // The video sidebar pages. Pages flagged shared: true are "same as music":
    // Help reuses the music docs page (SHARED_PAGES below); Issues has its own
    // video subpage. Import is not yet built and renders the placeholder.
    var VIDEO_PAGES = [
        { id: 'video-dashboard', label: 'Dashboard' },
        { id: 'video-search', label: 'Search' },
        { id: 'video-discover', label: 'Discover' },
        { id: 'video-library', label: 'Library' },
        { id: 'video-watchlist', label: 'Watchlist' },
        { id: 'video-wishlist', label: 'Wishlist' },
        { id: 'video-downloads', label: 'Downloads' },
        { id: 'video-calendar', label: 'Calendar' },
        { id: 'video-automations', label: 'Automations' },
        { id: 'video-tools', label: 'Tools' },
        { id: 'video-import', label: 'Import' },
        { id: 'video-settings', label: 'Settings' },
        { id: 'video-issues', label: 'Issues', shared: true },
        { id: 'video-help', label: 'Help & Docs', shared: true },
        // Drill-in detail pages — reachable from cards, not the sidebar nav.
        { id: 'video-show-detail', label: 'Show' },
        { id: 'video-movie-detail', label: 'Movie' },
        { id: 'video-person-detail', label: 'Person' },
        { id: 'video-studio-detail', label: 'Studio' },
    ];

    // "Shared" video pages reuse the REAL music page (shown identically on the
    // video side) instead of a video subpage: video page id -> music page id. CSS
    // reveals the music page; we trigger its loader once shown. Help & Docs is the
    // same docs page as the music side (it'll host video + music docs together);
    // Settings is the shared, per-side-filtered settings page.
    var SHARED_PAGES = { 'video-settings': 'settings', 'video-help': 'help' };

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

    // Per-page scroll memory: leaving a page remembers where you were, coming
    // back restores it (browse deep into the library grid → open a movie → Back
    // lands on the same row). Detail pages always REopen at the top — the same
    // subpage element is reused for every title, so a remembered offset would
    // open the NEXT movie mid-scroll. .main-content is the app's one scroller.
    var _scrollMemo = {};

    // Show one video page: reveal its built .video-subpage if one exists, else
    // fall back to the placeholder slot. Then announce it so per-page data
    // modules (e.g. video-dashboard.js) can populate themselves — they listen
    // for this event instead of being called directly, keeping each isolated.
    function showPage(pageId) {
        var meta = pageMeta(pageId);
        var scroller = document.querySelector('.main-content');
        var prevPage = document.body.getAttribute('data-video-page');
        // (only while ON the video side — after a music detour the scroller holds
        // the music page's offset, which must not overwrite the video memo)
        if (scroller && prevPage && prevPage !== meta.id &&
                document.body.getAttribute('data-side') === 'video') {
            _scrollMemo[prevPage] = scroller.scrollTop;
        }
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
        if (scroller && prevPage !== meta.id) {
            scroller.scrollTop = DETAIL_PAGES[meta.id] ? 0 : (_scrollMemo[meta.id] || 0);
        }
        document.dispatchEvent(new CustomEvent('soulsync:video-page-shown', { detail: meta.id }));
    }

    // Show a page + keep the URL in sync. Detail pages own their /video-detail/
    // URL (pushed by the open-detail handler); every other page deep-links to
    // '/' + pageId. mode: undefined = push a new history entry (user nav),
    // 'replace' = swap the current entry, 'restore' = URL already correct (popstate
    // / boot) so don't touch history.
    // Same permission model as the sidebar gating (init.js): admin-only control
    // pages + per-profile allowed_pages. Guards direct-URL access, not just the nav.
    // Per-profile side access — mirrors init.js's profileAllowedSides(), local
    // so this module keeps working if load order ever changes.
    function sideAllowed(side) {
        var cp = (typeof currentProfile !== 'undefined') ? currentProfile : null;
        if (!cp || cp.is_admin || cp.id === 1) return true;
        var s = (cp.allowed_sides === 'video' || cp.allowed_sides === 'both') ? cp.allowed_sides : 'music';
        return s === 'both' || s === side;
    }
    function videoPageAllowed(pageId) {
        if (!sideAllowed('video')) return false;   // whole side is off for this profile
        var cp = (typeof currentProfile !== 'undefined') ? currentProfile : null;
        if (!cp || cp.is_admin || cp.id === 1) return true;
        if (pageId === 'video-help' || pageId === 'video-issues' || DETAIL_PAGES[pageId]) return true;
        if (['video-import', 'video-settings', 'video-automations'].indexOf(pageId) > -1) return false;
        var ap = cp.allowed_pages;
        return !ap || ap.indexOf(pageId) > -1;
    }
    function navigate(pageId, mode) {
        // Music-only profile: there is no video page to fall back to — punt the
        // whole navigation back to the music side instead.
        if (!sideAllowed('video')) { switchSide('music', { force: true }); return; }
        if (!videoPageAllowed(pageId)) { pageId = DEFAULT_VIDEO_PAGE; mode = 'replace'; }
        setActiveNav(pageId);
        showPage(pageId);
        if (DETAIL_PAGES[pageId] || mode === 'restore') return;
        var path = buildPagePath(pageId);
        try {
            if (mode === 'replace' || window.location.pathname === path) {
                history.replaceState({ videoPage: pageId }, '', path);
            } else {
                history.pushState({ videoPage: pageId }, '', path);
            }
        } catch (e) { /* ignore */ }
    }

    // Flip the shell chrome to a side (data-side drives the CSS). Does NOT navigate
    // — callers decide which page to show and whether to push a URL.
    function applySide(side) {
        document.body.setAttribute('data-side', side);
        var subtitle = document.querySelector('.sidebar-header .app-subtitle');
        if (subtitle) subtitle.textContent = side === 'video' ? VIDEO_SUBTITLE : MUSIC_SUBTITLE;
        var toggleButtons = document.querySelectorAll('.side-toggle-btn');
        for (var i = 0; i < toggleButtons.length; i++) {
            toggleButtons[i].classList.toggle(
                'active', toggleButtons[i].getAttribute('data-side-target') === side);
        }
        // Global (music) search is hidden on the video side — recompute its
        // visibility/active state the moment the side flips.
        if (typeof _gsUpdateVisibility === 'function') _gsUpdateVisibility();
    }

    function switchSide(side, opts) {
        if (side !== 'music' && side !== 'video') return;
        // Per-profile side access: a profile locked to one side can't switch
        // away from it. `force` is the profile-apply path (init.js) moving the
        // user ONTO their allowed side — that's the one caller that must win.
        if (!(opts && opts.force) && !sideAllowed(side)) return;
        persistSide(side);
        applySide(side);
        if (side === 'video') {
            // Land on the last-active video page and give it a real URL.
            var active = document.querySelector('.video-nav .nav-button.active');
            navigate(active ? active.getAttribute('data-video-page') : DEFAULT_VIDEO_PAGE);
        } else if (parseDetailPath(window.location.pathname) || parsePagePath(window.location.pathname)) {
            // Back to music from a video URL → drop it so a reload stays on music.
            try { history.replaceState(null, '', '/'); } catch (e) { /* ignore */ }
        }
    }
    // init.js's profile-apply uses this to land single-side profiles on their side.
    window._switchAppSide = switchSide;

    function init() {
        // Deep-linked detail OR page path captured at eval time (music may already
        // have rewritten window.location to /dashboard by now).
        var bootDetail = parseDetailPath(BOOT_PATH);
        var bootPage = bootDetail ? null : parsePagePath(BOOT_PATH);

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
                    // Let ⌘/Ctrl/middle-click open the real href (/video-<page>) in a
                    // new tab, exactly like the music nav + the video cards.
                    if (e.metaKey || e.ctrlKey || e.shiftKey || e.button === 1) return;
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

        // Jump to a top-level video page from anywhere (e.g. a "Track on Downloads"
        // button in the download modal / a detail page's live-progress chip).
        document.addEventListener('soulsync:video-navigate', function (e) {
            var pageId = e && e.detail && (e.detail.page || e.detail);
            if (typeof pageId === 'string') navigate(pageId);
        });

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
            else if (d.kind === 'studio') navigate('video-studio-detail');
            else if (d.kind === 'channel') navigate('video-show-detail');   // channels reuse the show detail page
            else if (d.kind === 'playlist') navigate('video-show-detail');   // playlists too (flat list)
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

        var bootSide = (bootDetail || bootPage) ? 'video' : readSide();
        // Side access: if the profile is already known at boot (fast path),
        // land straight on the allowed side. When the profile loads later, the
        // profile-apply in init.js force-switches — this just avoids the flash.
        if (!sideAllowed(bootSide)) bootSide = bootSide === 'video' ? 'music' : 'video';
        applySide(bootSide);

        // On the video side without a detail deep link, show the initial page (the
        // page-path deep link if any, else the last-active/default nav). Deferred to
        // a macrotask so every page module's listeners are registered first. Only a
        // genuine page deep link re-asserts its URL against music's boot clobber;
        // the plain last-side-was-video case leaves the URL untouched (as before).
        if (bootSide === 'video' && !bootDetail) {
            var activeBtn = document.querySelector('.video-nav .nav-button.active');
            var initialPage = bootPage || (activeBtn && activeBtn.getAttribute('data-video-page')) || DEFAULT_VIDEO_PAGE;
            setActiveNav(initialPage);
            var pagePath = buildPagePath(initialPage);
            var reassertPage = function () {
                if (bootPage && document.body.getAttribute('data-side') === 'video' &&
                    !DETAIL_PAGES[document.body.getAttribute('data-video-page')] &&
                    window.location.pathname !== pagePath) {
                    try { history.replaceState({ videoPage: initialPage }, '', pagePath); } catch (e) { /* ignore */ }
                }
            };
            setTimeout(function () {
                reassertPage();
                navigate(initialPage, 'restore');
                if (bootPage) [120, 350, 700].forEach(function (ms) { setTimeout(reassertPage, ms); });
            }, 0);
        }

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

    // Public: open a video detail page from ANYWHERE (music or video side) —
    // e.g. the app-wide Server Activity drawer clicking through to what's playing.
    // Ensures the video side is active first (the open-detail handler only
    // navigates; it doesn't flip data-side), then drills in.
    window.SoulSyncVideo = window.SoulSyncVideo || {};
    window.SoulSyncVideo.openDetail = function (detail) {
        if (!detail || !detail.kind) return;
        if (document.body.getAttribute('data-side') !== 'video') {
            persistSide('video'); applySide('video');
        }
        document.dispatchEvent(new CustomEvent('soulsync:video-open-detail', { detail: detail }));
    };

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }
})();
