/**
 * profile api gate: don't ask the server for what this profile can't have.
 *
 * a member's first page load used to fire a dozen api calls the server then
 * refused (settings is admin-only, a music-only profile gets no video api).
 * the page rendered fine, but crowdsec / fail2ban read a burst of 4xx paths
 * from one ip as a scanner and banned the member (#1763).
 *
 * this wraps window.fetch and answers those calls locally with the same 403
 * the server would give, so every caller's error path runs unchanged and
 * nothing hits the wire. the rules mirror the server gates (admin_only, and
 * _video_perm_gate's side check + admin-for-any-method prefixes). the server
 * stays the authority, this only saves the round trip.
 *
 * until init.js knows the profile, gated calls wait for it. setProfile(null)
 * means "couldn't tell", and lets everything through as before.
 */
(function () {
    'use strict';
    if (typeof window === 'undefined' || !window.fetch) return;

    var raw = window.fetch;

    // _video_perm_gate's "admin for ANY method" list (api/video/__init__.py)
    var VIDEO_ADMIN_PREFIXES = [
        '/api/video/overlays', '/api/video/import', '/api/video/collections',
        '/api/video/repair', '/api/video/server-config', '/api/video/jellyfin',
        '/api/video/libraries', '/api/video/organization', '/api/video/downloads/slskd',
        '/api/video/enrichment/config', '/api/video/enrichment/priority',
        '/api/video/notifications', '/api/video/backups',
    ];

    function under(path, prefix) {
        return path === prefix || path.indexOf(prefix + '/') === 0 || path.indexOf(prefix + '?') === 0;
    }

    function isAdmin(profile) {
        return !!(profile.is_admin || profile.id === 1);
    }

    function sides(profile) {
        if (isAdmin(profile)) return 'both';
        var s = profile.allowed_sides;
        return (s === 'video' || s === 'both') ? s : 'music';
    }

    // why the server would refuse this path for this profile, or '' if it wouldn't
    function refusal(path, profile) {
        if (!profile) return '';
        var admin = isAdmin(profile);
        if (under(path, '/api/video')) {
            if (sides(profile) === 'music') return 'Video access is disabled for this profile.';
            if (!admin && VIDEO_ADMIN_PREFIXES.some(function (p) { return under(path, p); })) return 'Admin only.';
            return '';
        }
        if (!admin && under(path, '/api/settings')) return 'Admin access required';
        return '';
    }

    function pathOf(input) {
        try {
            var text = (typeof Request !== 'undefined' && input instanceof Request) ? input.url
                : (input instanceof URL ? input.href : String(input));
            var url = new URL(text, window.location.href);
            if (url.origin !== window.location.origin) return '';
            var path = url.pathname;
            // behind a reverse-proxy sub-path the browser path carries the mount
            if (window.SoulSyncURL && window.SoulSyncURL.strip) path = window.SoulSyncURL.strip(path);
            return path;
        } catch (e) {
            return '';
        }
    }

    function gated(path) {
        return under(path, '/api/video') || under(path, '/api/settings');
    }

    function refused(message) {
        return new Response(JSON.stringify({ success: false, error: message }), {
            status: 403, headers: { 'Content-Type': 'application/json' },
        });
    }

    var known = false;
    var profile = null;
    var waiting = [];

    window.fetch = function (input, init) {
        var self = this;
        var args = arguments;
        var path = pathOf(input);
        if (!path || !gated(path)) return raw.apply(self, args);
        var decide = function () {
            var why = refusal(path, profile);
            return why ? Promise.resolve(refused(why)) : raw.apply(self, args);
        };
        if (known) return decide();
        return new Promise(function (resolve) { waiting.push(resolve); }).then(decide);
    };

    window.SoulSyncProfileGate = {
        setProfile: function (next) {
            profile = next || null;
            known = true;
            var queued = waiting;
            waiting = [];
            queued.forEach(function (resolve) { resolve(); });
        },
        refusal: refusal,
    };
})();
