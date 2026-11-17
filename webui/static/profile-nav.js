/**
 * which sidebar pages a profile sees, and where it lands (#2154, #2156).
 *
 * the profile used to arrive over fetch after the sidebar had painted, so a
 * member saw the whole admin nav for a beat and then watched it shrink. the
 * server now renders the signed-in profile into the page
 * (window.SOULSYNC_BOOT_PROFILE), and this file loads right under the
 * sidebar so the nav is cut down before first paint. with no profile known
 * yet (picker, sign-in) html.nav-pending keeps the items hidden until
 * init.js applies the real one.
 *
 * the landing page lives here too, so the music shell and the video side
 * pick it the same way: their home page, else a sensible first page they can
 * open. a member never lands on a page they were refused.
 */
(function () {
    'use strict';

    // old rows still carry retired page ids
    var ALIASES = { downloads: 'search', artists: 'search', 'library-v2': 'library' };

    // where a member with no home page lands, in order. discover first: it is
    // what a request profile is for. then the sidebar's own order.
    var MUSIC_LANDING = ['discover', 'dashboard', 'sync', 'podcasts', 'audiobooks', 'search', 'watchlist',
        'wishlist', 'automations', 'active-downloads', 'library', 'stats', 'playlist-explorer', 'import'];
    var VIDEO_LANDING = ['video-discover', 'video-dashboard', 'video-search', 'video-library',
        'video-watchlist', 'video-wishlist', 'video-downloads', 'video-calendar', 'video-tools'];
    var VIDEO_ADMIN_ONLY = ['video-import', 'video-settings', 'video-automations'];

    function normalizePageId(pageId) {
        return ALIASES[pageId] || pageId;
    }

    function normalizePageList(pageIds) {
        return Array.isArray(pageIds) ? pageIds.map(normalizePageId) : pageIds;
    }

    function isRoot(profile) {
        return !!profile && (profile.id === 1 || profile.id === '1');
    }

    function canDownload(profile) {
        if (!profile || isRoot(profile)) return true;
        return profile.can_download !== false && profile.can_download !== 0;
    }

    // the server's permission map (#2166, src/shell/my-permissions.ts), once
    // it's in for this profile. null before that: the columns answer
    function livePerms(profile) {
        var p = window.SoulSyncPerms;
        if (!p || !p.live()) return null;
        var got = p.get();
        return got && String(got.profileId) === String(profile.id) ? p : null;
    }

    /** the Requests pages: admins and moderators answer them, see-everyone's
     *  reads them, and a profile that asks first makes them. a profile that
     *  can't ask for anything there has nothing to open. */
    function requestsVisible(profile, side) {
        if (!profile || profile.is_admin || isRoot(profile)) return true;
        var p = livePerms(profile);
        if (!p) return !canDownload(profile);
        if (p.on('requests.moderate') || p.on('requests.see_all')) return true;
        if (side === 'video') return p.mode('movies') === 'ask' || p.mode('tv') === 'ask';
        return p.mode('music') === 'ask';
    }

    function allowedSides(profile) {
        if (!profile || profile.is_admin || isRoot(profile)) return 'both';
        var s = profile.allowed_sides;
        return (s === 'video' || s === 'both') ? s : 'music';
    }

    /** whether a sidebar button shows for this profile. pageId is data-page
     *  (music) or data-video-page (video). the server is the real gate, this
     *  only keeps the nav honest. */
    function navButtonVisible(profile, pageId, video) {
        if (!profile) return true;
        if (video) {
            if (pageId === 'video-help' || pageId === 'video-issues') return true;
            if (VIDEO_ADMIN_ONLY.indexOf(pageId) > -1) return !!profile.is_admin;
            // admins answer requests, profiles without download rights make them
            if (pageId === 'video-requests') return requestsVisible(profile, 'video');
            if (isRoot(profile)) return true;
            var vap = profile.allowed_pages;
            return !vap || vap.indexOf(pageId) > -1;
        }
        if (pageId === 'settings') return !!profile.is_admin;
        if (pageId === 'requests') return requestsVisible(profile, 'music');
        if (pageId === 'help' || pageId === 'issues') return true;
        if (isRoot(profile)) return true;
        var ap = normalizePageList(profile.allowed_pages);
        return !ap || ap.indexOf(pageId) > -1;
    }

    function itemShown(el) {
        return el.classList.contains('nav-button') && !el.classList.contains('nav-feature-hidden')
            && el.style.display !== 'none';
    }

    /** a section title with nothing left under it goes too */
    function refreshSectionLabels(root) {
        var labels = root.querySelectorAll('.sidebar-nav .nav-section-label');
        for (var i = 0; i < labels.length; i++) {
            var el = labels[i].nextElementSibling;
            var any = false;
            while (el && !el.classList.contains('nav-section-label')) {
                if (itemShown(el)) { any = true; break; }
                el = el.nextElementSibling;
            }
            labels[i].classList.toggle('nav-feature-hidden', !any);
        }
    }

    /** cut the sidebar down to what this profile may open. safe to run again. */
    function apply(profile, root) {
        root = root || document;
        var html = root.documentElement;
        if (!profile) return;
        var buttons = root.querySelectorAll('.sidebar-nav .nav-button');
        for (var i = 0; i < buttons.length; i++) {
            var btn = buttons[i];
            var music = btn.getAttribute('data-page');
            var video = btn.getAttribute('data-video-page');
            if (music === 'hydrabase') continue; // the dev mode toggle owns it
            if (!music && !video) continue;
            btn.style.display = navButtonVisible(profile, music || video, !music) ? '' : 'none';
        }
        // the label hide rule only bites under data-features-off
        if (html && !html.hasAttribute('data-features-off')) html.setAttribute('data-features-off', '');
        refreshSectionLabels(root);
        var sides = allowedSides(profile);
        if (html) {
            html.classList.toggle('side-locked', sides !== 'both');
            html.classList.remove('nav-pending');
        }
        // a one-side profile is on that side from the first frame
        if (sides !== 'both' && root.body) root.body.setAttribute('data-side', sides);
    }

    /** pages the profile chose to hide (#1909): a preference, so it only
     *  steers the fallback, never an explicit home page */
    function featureHiddenPages() {
        try {
            var f = window.SoulSyncFeatures && window.SoulSyncFeatures.get && window.SoulSyncFeatures.get();
            if (f && Array.isArray(f.hiddenPages)) return f.hiddenPages;
        } catch (e) { /* fall back to the cache */ }
        try {
            var c = JSON.parse(localStorage.getItem('ss_features') || 'null');
            if (c && Array.isArray(c.pages)) return c.pages;
        } catch (e2) { /* no storage */ }
        return [];
    }

    /**
     * the page a profile lands on.
     *   home      their own home page, wins whenever they can open it
     *   isAdmin   admins keep landing on adminPage, as they always have
     *   order     the fallback order, first page they can open and haven't hidden
     *   allowed   (pageId) => bool, the side's permission rule
     *   hidden    page ids the profile hid
     *   fallback  the page every profile can open (help)
     */
    function landingPage(opts) {
        var allowed = opts.allowed || function () { return true; };
        var hidden = opts.hidden || [];
        if (opts.home && allowed(opts.home)) return opts.home;
        if (opts.isAdmin) return opts.adminPage;
        var order = opts.order || [];
        var i;
        for (i = 0; i < order.length; i++) {
            if (hidden.indexOf(order[i]) === -1 && allowed(order[i])) return order[i];
        }
        // everything they can open is hidden: still better than nothing
        for (i = 0; i < order.length; i++) {
            if (allowed(order[i])) return order[i];
        }
        return opts.fallback;
    }

    window.SoulSyncProfileNav = {
        MUSIC_LANDING: MUSIC_LANDING,
        VIDEO_LANDING: VIDEO_LANDING,
        normalizePageId: normalizePageId,
        normalizePageList: normalizePageList,
        allowedSides: allowedSides,
        navButtonVisible: navButtonVisible,
        requestsVisible: requestsVisible,
        refreshSectionLabels: refreshSectionLabels,
        apply: apply,
        featureHiddenPages: featureHiddenPages,
        landingPage: landingPage,
    };

    // first paint: the profile the server rendered in, if it knows one
    if (window.SOULSYNC_BOOT_PROFILE) apply(window.SOULSYNC_BOOT_PROFILE);
})();
