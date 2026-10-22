/*
 * SoulSync — Video Wishlist nav badge (isolated).
 *
 * The page itself is React now: the wishlist route's Movies / TV / YouTube tabs
 * (webui/src/routes/wishlist, reached from the video sidebar via
 * gotoUnifiedWishlist in video-side.js). What stays here is the part that runs
 * on EVERY page: the sidebar badge, which counts the whole video wishlist.
 * Self-contained IIFE, no globals.
 */
(function () {
    'use strict';

    // The nav badge is the WHOLE wishlist (movies + episodes + YouTube videos).
    // /wishlist/counts is the single source of truth for that total: computing it
    // from partial per-tab counts is how it once 'switched' to the TV-only number.
    function updateBadges(total) {
        var n = total || 0;
        ['[data-video-wishlist-badge]', '[data-video-badge="wishlist"]'].forEach(function (sel) {
            document.querySelectorAll(sel).forEach(function (b) {
                b.textContent = n; b.classList.toggle('hidden', !n);
            });
        });
    }
    function refreshBadge() {
        fetch('/api/video/wishlist/counts', { headers: { Accept: 'application/json' } })
            .then(function (r) { return r.ok ? r.json() : null; })
            .then(function (d) { if (d && d.success) updateBadges(d.total); })
            .catch(function () { /* ignore */ });
    }

    // The badge also has to track wishlist changes that happen SERVER-SIDE — when a
    // download finishes it removes its item from the wishlist, and that fires no frontend
    // 'changed' event. So poll the authoritative count: quicker while downloads are active
    // (the downloads page exposes _vdpgAnyActive), slower when idle, paused when hidden.
    function scheduleBadgePoll() {
        var active = (typeof window._vdpgAnyActive === 'function') && window._vdpgAnyActive();
        if (scheduleBadgePoll._t) clearTimeout(scheduleBadgePoll._t);
        scheduleBadgePoll._t = setTimeout(function () {
            if (!document.hidden) refreshBadge();
            scheduleBadgePoll();
        }, active ? 8000 : 30000);
    }

    function init() {
        // Adds and removes anywhere (the get modal, the wishlist page, a detail
        // page) announce themselves; the badge follows at once.
        document.addEventListener('soulsync:video-wishlist-changed', refreshBadge);
        refreshBadge();
        scheduleBadgePoll();
    }

    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init);
    else init();
})();
