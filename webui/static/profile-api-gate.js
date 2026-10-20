/**
 * profile api gate: don't ask the server for what this profile can't have.
 *
 * a member's first page load used to fire a dozen api calls the server then
 * refused: data calls while the picker or sign-in screen was still up (401),
 * settings for a non-admin, video for a music-only profile, a page's api the
 * profile isn't given (403). the page rendered fine, but crowdsec / fail2ban
 * read a burst of 4xx paths from one ip as a scanner and banned the member
 * (#1763).
 *
 * this wraps window.fetch:
 *   - until init.js opens it (a profile picked and unlocked, or the setup
 *     wizard took over), a call the server would 401 for "no profile" waits.
 *     the open list comes from the server (window.SOULSYNC_PROFILE_GATE), so
 *     the picker and sign-in flows can't hang on a stale copy
 *   - once open, a call the server would refuse this profile gets the same
 *     403 locally, so the caller's error path runs and nothing hits the wire.
 *     these mirror admin_only, the video blueprint's side + admin gates, and
 *     page_denied
 * the server stays the authority, this only saves the round trip.
 */
(function () {
    'use strict';
    if (typeof window === 'undefined' || !window.fetch) return;

    var raw = window.fetch;
    var rules = window.SOULSYNC_PROFILE_GATE || null;

    // _video_perm_gate's "admin for ANY method" list (api/video/__init__.py)
    var VIDEO_ADMIN_PREFIXES = [
        '/api/video/overlays', '/api/video/import', '/api/video/collections',
        '/api/video/repair', '/api/video/server-config', '/api/video/jellyfin',
        '/api/video/libraries', '/api/video/organization', '/api/video/downloads/slskd',
        '/api/video/enrichment/config', '/api/video/enrichment/priority',
        '/api/video/notifications', '/api/video/backups',
    ];

    function under(path, prefix) {
        return path === prefix || path.indexOf(prefix + '/') === 0;
    }

    function isAdmin(profile) {
        return !!(profile.is_admin || profile.id === 1);
    }

    function sides(profile) {
        if (isAdmin(profile)) return 'both';
        var s = profile.allowed_sides;
        return (s === 'video' || s === 'both') ? s : 'music';
    }

    // no_profile_request_is_blocked + is_open_profile_path (core/security/session_profile.py)
    function blockedWithoutProfile(path, method) {
        if (!rules) return false;
        var data = (rules.data_prefixes || []).some(function (p) { return path.indexOf(p) === 0; });
        if (!data) return false;
        if (path.indexOf('/api/v1/') === 0 && path.indexOf('/api/v1/api-keys-internal') !== 0) return false;
        if (method === 'GET' && (rules.open_get || []).indexOf(path) !== -1) return false;
        if (method === 'POST' && (rules.open_post || []).indexOf(path) !== -1) return false;
        if (path.indexOf('/api/invite/') === 0 && (method === 'GET' || method === 'POST')) return false;
        if (method === 'GET' && /^\/api\/profiles\/\d+\/avatar$/.test(path)) return false;
        return true;
    }

    // core/permissions.py page_denied
    function pageDenied(path, profile) {
        if (isAdmin(profile) || !Array.isArray(profile.allowed_pages) || !rules) return false;
        var pairs = rules.page_apis || [];
        for (var i = 0; i < pairs.length; i++) {
            if (under(path, pairs[i][0])) return profile.allowed_pages.indexOf(pairs[i][1]) === -1;
        }
        return false;
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
        if (pageDenied(path, profile)) return 'page_not_allowed';
        return '';
    }

    function requestOf(input, init) {
        try {
            var isRequest = typeof Request !== 'undefined' && input instanceof Request;
            var text = isRequest ? input.url : (input instanceof URL ? input.href : String(input));
            var url = new URL(text, window.location.href);
            if (url.origin !== window.location.origin) return null;
            var path = url.pathname;
            // behind a reverse-proxy sub-path the browser path carries the mount
            if (window.SoulSyncURL && window.SoulSyncURL.strip) path = window.SoulSyncURL.strip(path);
            var method = ((init && init.method) || (isRequest ? input.method : '') || 'GET').toUpperCase();
            return { path: path, method: method };
        } catch (e) {
            return null;
        }
    }

    function refused(message) {
        return new Response(JSON.stringify({ success: false, error: message }), {
            status: 403, headers: { 'Content-Type': 'application/json' },
        });
    }

    var opened = false;
    var profile = null;
    var waiting = [];

    window.fetch = function (input, init) {
        var self = this;
        var args = arguments;
        var req = requestOf(input, init);
        if (!req || req.path.indexOf('/api/') !== 0 && !blockedWithoutProfile(req.path, req.method)) {
            return raw.apply(self, args);
        }
        var decide = function () {
            var why = refusal(req.path, profile);
            return why ? Promise.resolve(refused(why)) : raw.apply(self, args);
        };
        if (opened || !blockedWithoutProfile(req.path, req.method)) return decide();
        return new Promise(function (resolve) { waiting.push(resolve); }).then(decide);
    };

    window.SoulSyncProfileGate = {
        // who the calls are judged as. null: couldn't tell, nothing is refused
        setProfile: function (next) {
            profile = next || null;
        },
        // the app is in: held calls go out, judged by the current profile
        open: function () {
            opened = true;
            var queued = waiting;
            waiting = [];
            queued.forEach(function (resolve) { resolve(); });
        },
        refusal: refusal,
        blockedWithoutProfile: blockedWithoutProfile,
    };
})();
