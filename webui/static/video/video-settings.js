/*
 * SoulSync — Video settings additions (isolated).
 *
 * The video side shows the real music settings page; this module only drives the
 * VIDEO-specific bits added to it — for now, the Movies/TV library mapping
 * (which server library the scan reads). It populates the dropdowns from
 * /api/video/libraries when the Settings page is shown on the video side and
 * saves the choice back. Self-contained IIFE, no globals, no inline handlers.
 */
(function () {
    'use strict';

    var PAGE_ID = 'video-settings';
    var URL = '/api/video/libraries';
    var CONFIG_URL = '/api/video/enrichment/config';
    var SERVER_URL = '/api/video/server';
    var CONN_URL = '/api/video/server-config';
    var DOWNLOADS_URL = '/api/video/downloads/config';
    var QUALITY_URL = '/api/video/downloads/quality';
    var YT_QUALITY_URL = '/api/video/downloads/youtube-quality';
    var SLSKD_URL = '/api/video/downloads/slskd';
    var _videoQuality = null;
    var _videoYtQuality = null;
    // Pretty labels for the source×resolution quality ladder (keys come from the backend).
    var TIER_LABEL = {
        'remux-2160p': 'Remux · 4K', 'bluray-2160p': 'BluRay · 4K', 'web-2160p': 'WEB · 4K',
        'remux-1080p': 'Remux · 1080p', 'bluray-1080p': 'BluRay · 1080p', 'web-1080p': 'WEB-DL · 1080p',
        'webrip-1080p': 'WEBRip · 1080p', 'hdtv-1080p': 'HDTV · 1080p',
        'bluray-720p': 'BluRay · 720p', 'web-720p': 'WEB-DL · 720p', 'hdtv-720p': 'HDTV · 720p',
        'dvd': 'DVD', 'sdtv': 'SDTV'
    };
    var REJECT_LABEL = {
        'cam': 'CAM / TS', 'screener': 'Screener', 'workprint': 'Workprint', '3d': '3D', 'x264': 'x264 / AVC'
    };
    var REJECT_ORDER = ['cam', 'screener', 'workprint', '3d', 'x264'];

    function esc(s) {
        return String(s == null ? '' : s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
    }

    // ── Server Connection ───────────────────────────────────────────────────
    // Mirrors the MUSIC server picker (toggle = select + configure), scoped to
    // Plex/Jellyfin. Clicking a toggle reveals that server's creds AND sets it as
    // the active video server. Creds are video's own (video.db), pre-filled from
    // music; the picker writes only to /api/video/* — never the music config.
    function connEl(name) { return document.querySelector('[data-video-conn="' + name + '"]'); }
    function note(server, text) {
        var n = document.querySelector('[data-video-conn-note="' + server + '"]');
        if (n) n.textContent = text || '';
    }
    function showServerConfig(server) {
        var btns = document.querySelectorAll('[data-video-server-toggle]');
        for (var i = 0; i < btns.length; i++) {
            btns[i].classList.toggle('active', btns[i].getAttribute('data-video-server-toggle') === server);
        }
        var cfgs = document.querySelectorAll('[data-video-server-config]');
        for (var j = 0; j < cfgs.length; j++) {
            cfgs[j].classList.toggle('hidden', cfgs[j].getAttribute('data-video-server-config') !== server);
        }
    }
    // Which server's config to show on load: the explicit pick, else the
    // configured one, else Plex (so there's always a panel to fill in).
    function loadServer() {
        fetch(SERVER_URL, { headers: { 'Accept': 'application/json' } })
            .then(function (r) { return r.ok ? r.json() : null; })
            .then(function (d) {
                d = d || {};
                var active = d.server || (d.jellyfin && !d.plex ? 'jellyfin' : 'plex');
                showServerConfig(active);
                if (active === 'jellyfin') loadJellyfinUsers();
            })
            .catch(function () { showServerConfig('plex'); });
    }
    function loadConn() {
        fetch(CONN_URL, { headers: { 'Accept': 'application/json' } })
            .then(function (r) { return r.ok ? r.json() : null; })
            .then(function (d) {
                if (!d) return;
                var p = d.plex || {}, j = d.jellyfin || {};
                var pu = connEl('plex-url'); if (pu) pu.value = p.base_url || '';
                var pt = connEl('plex-token'); if (pt) pt.value = p.has_token ? p.token : '';
                var ju = connEl('jellyfin-url'); if (ju) ju.value = j.base_url || '';
                var jk = connEl('jellyfin-key'); if (jk) jk.value = j.has_key ? j.api_key : '';
                note('plex', p.base_url
                    ? (p.inherited ? 'Inherited from your Music Plex connection — edit to use a different server for video.'
                                   : 'Custom video connection.')
                    : 'Not connected — add a server URL and token.');
                note('jellyfin', j.base_url
                    ? (j.inherited ? 'Inherited from your Music Jellyfin connection — edit to use a different server for video.'
                                   : 'Custom video connection.')
                    : 'Not connected — add a server URL and API key.');
            })
            .catch(function () { /* ignore */ });
    }
    function saveConn(silent) {
        var pu = connEl('plex-url'), pt = connEl('plex-token');
        var ju = connEl('jellyfin-url'), jk = connEl('jellyfin-key');
        return fetch(CONN_URL, {
            method: 'POST', headers: { 'Content-Type': 'application/json', 'Accept': 'application/json' },
            body: JSON.stringify({
                plex: { base_url: pu ? pu.value : '', token: pt ? pt.value : '' },
                jellyfin: { base_url: ju ? ju.value : '', api_key: jk ? jk.value : '' }
            })
        }).then(function () { loadConn(); if (!silent) toast('Connection saved', 'success'); })
          .catch(function () { if (!silent) toast('Could not save connection', 'error'); });
    }
    // Toggle click: reveal that server's config immediately (like the music
    // toggle) and persist it as the active video server pick.
    function pickServer(server) {
        showServerConfig(server);
        if (server === 'jellyfin') loadJellyfinUsers();
        fetch(SERVER_URL, {
            method: 'POST', headers: { 'Content-Type': 'application/json', 'Accept': 'application/json' },
            body: JSON.stringify({ server: server })
        }).then(function () {
            load();
            toast('Video server set to ' + (server === 'plex' ? 'Plex' : 'Jellyfin'), 'success');
        }).catch(function () { /* ignore */ });
    }
    function testConn(server) {
        var name = server === 'plex' ? 'Plex' : 'Jellyfin';
        toast('Testing ' + name + ' connection…', 'info');
        saveConn(true).then(function () {
            return fetch(CONN_URL + '/test', {
                method: 'POST', headers: { 'Content-Type': 'application/json', 'Accept': 'application/json' },
                body: JSON.stringify({ server: server })
            });
        }).then(function (r) { return r.json(); }).then(function (res) {
            if (res && res.success) {
                toast(res.message || (name + ' connection successful'), 'success');
                if (server === 'jellyfin') { loadJellyfinUsers(); load(); }  // user + libraries
            } else {
                toast(name + ' connection failed: ' + ((res && res.error) || 'unknown'), 'error');
            }
        }).catch(function () { toast('Failed to test ' + name + ' connection', 'error'); });
    }

    // ── Jellyfin user picker (mirrors music: pick a user, then its libraries) ──
    function loadJellyfinUsers() {
        var wrap = document.querySelector('[data-video-jellyfin-user-wrap]');
        var sel = document.querySelector('[data-video-jellyfin-user]');
        if (!wrap || !sel) return;
        fetch('/api/video/jellyfin/users', { headers: { 'Accept': 'application/json' } })
            .then(function (r) { return r.ok ? r.json() : null; })
            .then(function (d) {
                if (!d || !d.success || !d.users || !d.users.length) { wrap.style.display = 'none'; return; }
                sel.textContent = '';
                var none = document.createElement('option');
                none.value = ''; none.textContent = 'Select User';
                sel.appendChild(none);
                d.users.forEach(function (u) {
                    var o = document.createElement('option');
                    o.value = u.id;
                    o.textContent = u.name + (u.admin ? ' (admin)' : '');
                    if (u.id === d.selected) o.selected = true;
                    sel.appendChild(o);
                });
                wrap.style.display = 'block';
            })
            .catch(function () { wrap.style.display = 'none'; });
    }
    function selectJellyfinUser(id) {
        fetch('/api/video/jellyfin/user', {
            method: 'POST', headers: { 'Content-Type': 'application/json', 'Accept': 'application/json' },
            body: JSON.stringify({ user: id })
        }).then(function () {
            load();  // refresh libraries for the user
            if (id) toast('Jellyfin user updated', 'success');
        }).catch(function () { /* ignore */ });
    }

    function status(text) {
        var n = document.querySelector('[data-video-lib-status]');
        if (n) n.textContent = text || '';
    }

    function fill(select, items, selected) {
        if (!select) return;
        select.textContent = '';
        var none = document.createElement('option');
        none.value = '';
        none.textContent = '— None —';
        select.appendChild(none);
        for (var i = 0; i < items.length; i++) {
            var opt = document.createElement('option');
            opt.value = items[i].title;
            opt.textContent = items[i].title;
            if (items[i].title === selected) opt.selected = true;
            select.appendChild(opt);
        }
    }

    function load() {
        status('Loading…');
        fetch(URL, { headers: { 'Accept': 'application/json' } })
            .then(function (r) { return r.ok ? r.json() : null; })
            .then(function (d) {
                if (!d || d.error) { status('Could not load libraries'); return; }
                var sel = d.selected || {};
                fill(document.querySelector('[data-video-lib-select="movies"]'), d.movies || [], sel.movies);
                fill(document.querySelector('[data-video-lib-select="tv"]'), d.tv || [], sel.tv);
                status('');
            })
            .catch(function () { status('Could not load libraries'); });
    }

    function save(silent) {
        var m = document.querySelector('[data-video-lib-select="movies"]');
        var t = document.querySelector('[data-video-lib-select="tv"]');
        status('Saving…');
        return fetch(URL, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json', 'Accept': 'application/json' },
            body: JSON.stringify({ movies: m ? m.value : '', tv: t ? t.value : '' })
        })
            .then(function (r) { return r.json(); })
            .then(function () { status('Saved'); if (!silent) toast('Library selection saved', 'success'); })
            .catch(function () { status('Save failed'); if (!silent) toast('Could not save libraries', 'error'); });
    }

    // ── Enrichment API keys (TMDB / TVDB) ───────────────────────────────────
    function loadKeys() {
        fetch(CONFIG_URL, { headers: { 'Accept': 'application/json' } })
            .then(function (r) { return r.ok ? r.json() : null; })
            .then(function (d) {
                if (!d) return;
                var t = document.getElementById('tmdb-api-key');
                var v = document.getElementById('tvdb-api-key');
                var o = document.getElementById('omdb-api-key');
                if (t && d.tmdb_api_key != null) t.value = d.tmdb_api_key;
                if (v && d.tvdb_api_key != null) v.value = d.tvdb_api_key;
                if (o && d.omdb_api_key != null) o.value = d.omdb_api_key;
                var fa = document.getElementById('fanart-api-key');
                if (fa && d.fanart_api_key != null) fa.value = d.fanart_api_key;
                var sub = document.getElementById('opensubtitles-api-key');
                if (sub && d.opensubtitles_api_key != null) sub.value = d.opensubtitles_api_key;
                var trakt = document.getElementById('trakt-api-key');
                if (trakt && d.trakt_api_key != null) trakt.value = d.trakt_api_key;
                var mdbl = document.getElementById('mdblist-api-key');
                if (mdbl && d.mdblist_api_key != null) mdbl.value = d.mdblist_api_key;
                var ryd = document.getElementById('video-ryd-enabled');
                if (ryd && d.ryd_enabled != null) ryd.checked = !!d.ryd_enabled;
                var sb = document.getElementById('video-sponsorblock-enabled');
                if (sb && d.sponsorblock_enabled != null) sb.checked = !!d.sponsorblock_enabled;
                var dea = document.getElementById('video-dearrow-enabled');
                if (dea && d.dearrow_enabled != null) dea.checked = !!d.dearrow_enabled;
                var tvm = document.getElementById('video-tvmaze-enabled');
                if (tvm && d.tvmaze_enabled != null) tvm.checked = !!d.tvmaze_enabled;
                var anl = document.getElementById('video-anilist-enabled');
                if (anl && d.anilist_enabled != null) anl.checked = !!d.anilist_enabled;
                var wkd = document.getElementById('video-wikidata-enabled');
                if (wkd && d.wikidata_enabled != null) wkd.checked = !!d.wikidata_enabled;
                var ap = document.getElementById('video-billboard-autoplay');
                if (ap && d.billboard_autoplay != null) ap.checked = !!d.billboard_autoplay;
                var wr = document.getElementById('video-watch-region');
                if (wr && d.watch_region) wr.value = d.watch_region;
            })
            .catch(function () { /* ignore */ });
    }

    function savePrefs(silent) {
        var ap = document.getElementById('video-billboard-autoplay');
        var wr = document.getElementById('video-watch-region');
        return fetch(CONFIG_URL, {
            method: 'POST', headers: { 'Content-Type': 'application/json', 'Accept': 'application/json' },
            body: JSON.stringify({
                billboard_autoplay: ap ? ap.checked : true,
                watch_region: wr ? wr.value : 'US',
            })
        }).then(function () { if (!silent) toast('Preferences saved', 'success'); })
          .catch(function () { /* ignore */ });
    }

    // ── Downloads tab: folders + source mode + hybrid chain ──
    var VIDEO_SOURCES = ['soulseek', 'torrent', 'usenet'];
    var SRC_DL_LABEL = { soulseek: 'Soulseek', torrent: 'Torrent', usenet: 'Usenet' };
    var SRC_DL_EMOJI = { soulseek: '🎵', torrent: '🧲', usenet: '📰' };
    var _videoMode = 'soulseek';
    var _videoHybrid = ['soulseek'];

    function loadDownloads() {
        fetch(DOWNLOADS_URL, { headers: { 'Accept': 'application/json' } })
            .then(function (r) { return r.ok ? r.json() : null; })
            .then(function (d) {
                if (!d) return;
                var setP = function (id, v) { var el = document.getElementById(id); if (el && v != null) el.value = v; };
                setP('video-download-path', d.download_path);
                setP('video-movies-path', d.movies_path);
                setP('video-tv-path', d.tv_path);
                setP('video-youtube-path', d.youtube_path);
                _videoMode = d.download_mode || 'soulseek';
                _videoHybrid = (d.hybrid_order && d.hybrid_order.length) ? d.hybrid_order : ['soulseek'];
                var ms = document.getElementById('video-download-mode');
                if (ms) ms.value = _videoMode;
                setP('video-seed-ratio', d.seed_ratio_goal != null ? d.seed_ratio_goal : 0);
                setP('video-seed-hours', d.seed_time_goal_hours != null ? d.seed_time_goal_hours : 0);
                var srd = document.getElementById('video-seed-remove-data');
                if (srd) srd.checked = d.seed_remove_data !== false;
                renderVideoHybrid();
                updateVideoSourceUI();
            })
            .catch(function () { /* ignore */ });
    }

    function saveDownloads(silent) {
        var val = function (id) { var el = document.getElementById(id); return el ? el.value : ''; };
        return fetch(DOWNLOADS_URL, {
            method: 'POST', headers: { 'Content-Type': 'application/json', 'Accept': 'application/json' },
            body: JSON.stringify({
                download_path: val('video-download-path'),
                movies_path: val('video-movies-path'),
                tv_path: val('video-tv-path'),
                youtube_path: val('video-youtube-path'),
                download_mode: _videoMode,
                hybrid_order: _videoHybrid,
                seed_ratio_goal: parseFloat(val('video-seed-ratio')) || 0,
                seed_time_goal_hours: parseInt(val('video-seed-hours'), 10) || 0,
                seed_remove_data: !!(document.getElementById('video-seed-remove-data') || {}).checked,
            })
        }).then(function () { if (!silent) toast('Download folders saved', 'success'); })
          .catch(function () { /* ignore */ });
    }

    // ── import lists editor (arr-parity P6) ─────────────────────────────────
    var IMPLIST_URL = DOWNLOADS_URL + '/import-lists';
    var _vqImpLists = [];

    function loadImportLists() {
        fetch(IMPLIST_URL, { headers: { 'Accept': 'application/json' } })
            .then(function (r) { return r.ok ? r.json() : null; })
            .then(function (d) {
                if (!d) return;
                _vqImpLists = d.lists || [];
                renderImportLists();
                wireImportLists();
            })
            .catch(function () { /* ignore */ });
    }

    function _sel(options, value) {
        return options.map(function (o) {
            return '<option value="' + o[0] + '"' + (o[0] === value ? ' selected' : '') + '>' + o[1] + '</option>';
        }).join('');
    }

    function renderImportLists() {
        var host = document.getElementById('vq-implist-rows');
        if (!host) return;
        host.innerHTML = _vqImpLists.map(function (l) {
            return '<div class="vq-fmt-row vq-implist-row" data-vq-implist="' + l.id + '">' +
                '<input class="vq-fmt-in" data-vq-il-f="name" value="' + escA(l.name) + '" placeholder="Name">' +
                '<select class="vq-fmt-in" data-vq-il-f="source">' +
                    _sel([['tmdb_list', 'TMDB list'], ['tmdb_chart', 'TMDB chart'],
                          ['imdb_list', 'IMDb list'], ['plex_watchlist', 'Plex Watchlist']], l.source) + '</select>' +
                '<input class="vq-fmt-in" data-vq-il-f="ref" value="' + escA(l.ref) + '" placeholder="list id / chart / ls…">' +
                '<select class="vq-fmt-in" data-vq-il-f="media">' +
                    _sel([['both', 'Both'], ['movie', 'Movies'], ['show', 'Shows']], l.media) + '</select>' +
                '<select class="vq-fmt-in" data-vq-il-f="monitor" title="Shows: what to wish when followed">' +
                    _sel([['future', 'Future eps'], ['all', 'All aired'], ['latest_season', 'Latest season'],
                          ['first_season', 'First season'], ['pilot', 'Pilot']], l.monitor) + '</select>' +
                '<label class="vq-il-on" title="Enabled"><input type="checkbox" data-vq-il-f="enabled"' + (l.enabled ? ' checked' : '') + '></label>' +
                '<button class="vq-fmt-del" type="button" data-vq-implist-del="' + l.id + '" title="Delete list">✕</button>' +
                '</div>';
        }).join('') || '<div class="settings-hint" style="padding:6px 0;">No import lists yet.</div>';
    }

    function _impListFromRow(row) {
        var val = function (k) {
            var el = row.querySelector('[data-vq-il-f="' + k + '"]');
            return el ? (el.type === 'checkbox' ? el.checked : el.value) : '';
        };
        return { id: parseInt(row.getAttribute('data-vq-implist'), 10),
                 name: val('name'), source: val('source'), ref: val('ref'),
                 media: val('media'), monitor: val('monitor'), enabled: val('enabled') };
    }

    function wireImportLists() {
        var host = document.getElementById('vq-implist-rows');
        if (!host || host._vqWired) return;
        host._vqWired = true;
        host.addEventListener('change', function (e) {
            var row = e.target.closest('[data-vq-implist]');
            if (!row) return;
            fetch(IMPLIST_URL, { method: 'POST', headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(_impListFromRow(row)) })
                .then(function (r) { return r.ok ? r.json() : null; })
                .then(function (res) {
                    if (!res || !res.success) { toast('A list needs a valid source + ref', 'error'); return; }
                    for (var i = 0; i < _vqImpLists.length; i++) {
                        if (_vqImpLists[i].id === res.id) _vqImpLists[i] = res;
                    }
                })
                .catch(function () { toast('Couldn’t save the list', 'error'); });
        });
        host.addEventListener('click', function (e) {
            var del = e.target.closest('[data-vq-implist-del]');
            if (!del) return;
            fetch(IMPLIST_URL + '/' + del.getAttribute('data-vq-implist-del'), { method: 'DELETE' })
                .then(function (r) { return r.ok ? r.json() : null; })
                .then(function (res) {
                    if (!res || !res.success) throw new Error();
                    _vqImpLists = _vqImpLists.filter(function (l) { return String(l.id) !== del.getAttribute('data-vq-implist-del'); });
                    renderImportLists();
                })
                .catch(function () { toast('Couldn’t delete the list', 'error'); });
        });
        var add = document.querySelector('[data-vq-implist-add]');
        if (add && !add._vqWired) {
            add._vqWired = true;
            add.addEventListener('click', function () {
                fetch(IMPLIST_URL, { method: 'POST', headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ name: 'New list', source: 'tmdb_chart', ref: 'trending_movies' }) })
                    .then(function (r) { return r.ok ? r.json() : null; })
                    .then(function (res) {
                        if (!res || !res.success) throw new Error();
                        _vqImpLists.push(res);
                        renderImportLists();
                    })
                    .catch(function () { toast('Couldn’t add a list', 'error'); });
            });
        }
    }

    // ── notification connections (arr-parity P11) ────────────────────────────
    var NOTIFY_URL = '/api/video/notifications';
    var _vqNotify = [];
    var _vqNotifyEvents = [];
    var _NOTIFY_EVENT_LABEL = {
        video_download_completed: 'Imported', video_upgrade_completed: 'Upgraded',
        video_import_failed: 'Import failed', video_download_failed: 'Failed',
        video_wishlist_item_added: 'Wishlisted', video_watchlist_added: 'Followed',
    };

    function loadNotify() {
        fetch(NOTIFY_URL, { headers: { 'Accept': 'application/json' } })
            .then(function (r) { return r.ok ? r.json() : null; })
            .then(function (d) {
                if (!d) return;
                _vqNotify = d.connections || [];
                _vqNotifyEvents = d.events || [];
                renderNotify();
                wireNotify();
            })
            .catch(function () { /* non-admins get a 403 — section stays empty */ });
    }

    function renderNotify() {
        var host = document.getElementById('vq-notify-rows');
        if (!host) return;
        host.innerHTML = _vqNotify.map(function (c) {
            var tg = c.type === 'telegram';
            var evs = _vqNotifyEvents.map(function (ev) {
                return '<label class="vq-nt-ev"><input type="checkbox" data-vq-nt-ev="' + ev + '"' +
                    (c.events.indexOf(ev) > -1 ? ' checked' : '') + '>' +
                    (_NOTIFY_EVENT_LABEL[ev] || ev) + '</label>';
            }).join('');
            return '<div class="vq-nt-block" data-vq-notify="' + c.id + '">' +
                '<div class="vq-fmt-row vq-nt-row">' +
                    '<input class="vq-fmt-in" data-vq-nt-f="name" value="' + escA(c.name) + '" placeholder="Name">' +
                    '<select class="vq-fmt-in" data-vq-nt-f="type">' +
                        _sel([['discord', 'Discord'], ['webhook', 'Webhook'], ['telegram', 'Telegram']], c.type) + '</select>' +
                    '<input class="vq-fmt-in" data-vq-nt-f="url" value="' + escA(c.url) + '" placeholder="Webhook URL"' + (tg ? ' style="display:none"' : '') + '>' +
                    '<input class="vq-fmt-in" data-vq-nt-f="token" value="' + escA(c.token) + '" placeholder="Bot token"' + (tg ? '' : ' style="display:none"') + '>' +
                    '<input class="vq-fmt-in" data-vq-nt-f="chat_id" value="' + escA(c.chat_id) + '" placeholder="Chat id"' + (tg ? '' : ' style="display:none"') + '>' +
                    '<button class="test-button vq-nt-test" type="button" data-vq-notify-test="' + c.id + '">Test</button>' +
                    '<label class="vq-il-on" title="Enabled"><input type="checkbox" data-vq-nt-f="enabled"' + (c.enabled ? ' checked' : '') + '></label>' +
                    '<button class="vq-fmt-del" type="button" data-vq-notify-del="' + c.id + '" title="Delete">✕</button>' +
                '</div>' +
                '<div class="vq-nt-events">' + evs + '</div>' +
                '</div>';
        }).join('') || '<div class="settings-hint" style="padding:6px 0;">No connections yet.</div>';
    }

    function _notifyFromBlock(block) {
        var val = function (k) {
            var el = block.querySelector('[data-vq-nt-f="' + k + '"]');
            return el ? (el.type === 'checkbox' ? el.checked : el.value) : '';
        };
        var events = [];
        Array.prototype.forEach.call(block.querySelectorAll('[data-vq-nt-ev]'), function (cb) {
            if (cb.checked) events.push(cb.getAttribute('data-vq-nt-ev'));
        });
        return { id: parseInt(block.getAttribute('data-vq-notify'), 10),
                 name: val('name'), type: val('type'), url: val('url'),
                 token: val('token'), chat_id: val('chat_id'),
                 enabled: val('enabled'), events: events };
    }

    function wireNotify() {
        var host = document.getElementById('vq-notify-rows');
        if (!host || host._vqWired) return;
        host._vqWired = true;
        host.addEventListener('change', function (e) {
            var block = e.target.closest('[data-vq-notify]');
            if (!block) return;
            var conn = _notifyFromBlock(block);
            fetch(NOTIFY_URL, { method: 'POST', headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(conn) })
                .then(function (r) { return r.ok ? r.json() : null; })
                .then(function (res) {
                    if (!res || !res.success) { toast('Connection needs a valid target (URL or token + chat id)', 'error'); return; }
                    for (var i = 0; i < _vqNotify.length; i++) {
                        if (_vqNotify[i].id === res.id) _vqNotify[i] = res;
                    }
                    if (e.target.getAttribute('data-vq-nt-f') === 'type') renderNotify();   // swap target inputs
                })
                .catch(function () { toast('Couldn’t save the connection', 'error'); });
        });
        host.addEventListener('click', function (e) {
            var del = e.target.closest('[data-vq-notify-del]');
            if (del) {
                fetch(NOTIFY_URL + '/' + del.getAttribute('data-vq-notify-del'), { method: 'DELETE' })
                    .then(function (r) { return r.ok ? r.json() : null; })
                    .then(function (res) {
                        if (!res || !res.success) throw new Error();
                        _vqNotify = _vqNotify.filter(function (c) { return String(c.id) !== del.getAttribute('data-vq-notify-del'); });
                        renderNotify();
                    })
                    .catch(function () { toast('Couldn’t delete the connection', 'error'); });
                return;
            }
            var tb = e.target.closest('[data-vq-notify-test]');
            if (tb) {
                var block = tb.closest('[data-vq-notify]');
                tb.disabled = true;
                fetch(NOTIFY_URL + '/test', { method: 'POST', headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(_notifyFromBlock(block)) })
                    .then(function (r) { return r.json().catch(function () { return null; }).then(function (j) { return { ok: r.ok, j: j }; }); })
                    .then(function (res) {
                        tb.disabled = false;
                        if (res.ok && res.j && res.j.success) toast('Test sent — check the channel', 'success');
                        else toast((res.j && res.j.error) || 'Test failed', 'error');
                    })
                    .catch(function () { tb.disabled = false; toast('Test failed', 'error'); });
            }
        });
        var add = document.querySelector('[data-vq-notify-add]');
        if (add && !add._vqWired) {
            add._vqWired = true;
            add.addEventListener('click', function () {
                fetch(NOTIFY_URL, { method: 'POST', headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ name: 'Discord', type: 'discord',
                                           url: 'https://discord.com/api/webhooks/REPLACE-ME' }) })
                    .then(function (r) { return r.ok ? r.json() : null; })
                    .then(function (res) {
                        if (!res || !res.success) throw new Error();
                        _vqNotify.push(res);
                        renderNotify();
                    })
                    .catch(function () { toast('Couldn’t add a connection', 'error'); });
            });
        }
    }

    // Hybrid chain — reuses music's .hybrid-source-item markup/CSS for visual
    // parity. Enabled sources (ordered, numbered) first, disabled ones appended.
    // No album-level/track-level badge — that's a music-only concept.
    function renderVideoHybrid() {
        var host = document.getElementById('video-hybrid-rows');
        if (!host) return;
        var enabled = _videoHybrid.filter(function (s) { return VIDEO_SOURCES.indexOf(s) >= 0; });
        var disabled = VIDEO_SOURCES.filter(function (s) { return enabled.indexOf(s) < 0; });
        var visual = enabled.concat(disabled);
        host.innerHTML = visual.map(function (s) {
            var on = enabled.indexOf(s) >= 0;
            var i = enabled.indexOf(s);
            return '<div class="hybrid-source-item' + (on ? '' : ' disabled') + '">' +
                '<span class="hybrid-source-arrows">' +
                '<button type="button" class="hybrid-arrow-btn" data-vh-move="' + s + '" data-dir="-1"' + ((!on || i === 0) ? ' disabled' : '') + ' title="Move up">▲</button>' +
                '<button type="button" class="hybrid-arrow-btn" data-vh-move="' + s + '" data-dir="1"' + ((!on || i === enabled.length - 1) ? ' disabled' : '') + ' title="Move down">▼</button>' +
                '</span>' +
                '<span class="hybrid-source-icon emoji-icon">' + (SRC_DL_EMOJI[s] || '') + '</span>' +
                '<span class="hybrid-source-name">' + SRC_DL_LABEL[s] + '</span>' +
                '<span class="hybrid-source-priority">' + (on ? (i + 1) : '') + '</span>' +
                '<label class="hybrid-source-toggle"><input type="checkbox" data-vh-toggle="' + s + '"' + (on ? ' checked' : '') + '><span class="toggle-track"></span></label>' +
                '</div>';
        }).join('');
    }

    function moveVH(s, dir) {
        var i = _videoHybrid.indexOf(s), j = i + dir;
        if (i < 0 || j < 0 || j >= _videoHybrid.length) return;
        _videoHybrid[i] = _videoHybrid[j]; _videoHybrid[j] = s;
        renderVideoHybrid(); saveDownloads(true);
    }

    function toggleVH(s, on) {
        if (on) {
            if (_videoHybrid.indexOf(s) < 0) _videoHybrid.push(s);
        } else {
            if (_videoHybrid.length <= 1) { renderVideoHybrid(); return; }  // keep at least one
            _videoHybrid = _videoHybrid.filter(function (x) { return x !== s; });
        }
        renderVideoHybrid(); saveDownloads(true);
    }

    function soulseekActive() {
        return _videoMode === 'soulseek' ||
            (_videoMode === 'hybrid' && _videoHybrid.indexOf('soulseek') >= 0);
    }

    function updateVideoSourceUI() {
        var hc = document.getElementById('video-hybrid-container');
        if (hc) hc.style.display = _videoMode === 'hybrid' ? 'block' : 'none';
        // slskd connection only matters when soulseek is in play.
        var sc = document.getElementById('video-slskd-container');
        if (sc) sc.style.display = soulseekActive() ? 'block' : 'none';
    }

    // ── Shared slskd connection (writes the app-wide soulseek.* — affects Music too) ──
    function _byId(id) { return document.getElementById(id); }
    function loadSlskd() {
        fetch(SLSKD_URL, { headers: { 'Accept': 'application/json' } })
            .then(function (r) { return r.ok ? r.json() : null; })
            .then(function (d) {
                if (!d) return;
                if (_byId('video-slskd-url')) _byId('video-slskd-url').value = d.slskd_url || '';
                if (_byId('video-slskd-api-key')) _byId('video-slskd-api-key').value = d.api_key || '';
                if (_byId('video-slskd-search-timeout')) _byId('video-slskd-search-timeout').value = d.search_timeout != null ? d.search_timeout : 60;
                if (_byId('video-slskd-search-timeout-buffer')) _byId('video-slskd-search-timeout-buffer').value = d.search_timeout_buffer != null ? d.search_timeout_buffer : 15;
                if (_byId('video-slskd-search-min-delay')) _byId('video-slskd-search-min-delay').value = d.search_min_delay_seconds != null ? d.search_min_delay_seconds : 0;
                if (_byId('video-slskd-min-peer-speed')) _byId('video-slskd-min-peer-speed').value = d.min_peer_upload_speed != null ? d.min_peer_upload_speed : 0;
                if (_byId('video-slskd-max-peer-queue')) _byId('video-slskd-max-peer-queue').value = d.max_peer_queue != null ? d.max_peer_queue : 0;
                // config stores seconds; UI shows minutes.
                if (_byId('video-slskd-download-timeout')) _byId('video-slskd-download-timeout').value = Math.round((d.download_timeout != null ? d.download_timeout : 600) / 60);
                if (_byId('video-slskd-auto-clear')) _byId('video-slskd-auto-clear').checked = d.auto_clear_searches !== false;
            })
            .catch(function () { /* ignore */ });
    }

    function _num(id, dflt) { var el = _byId(id); var v = el ? parseInt(el.value, 10) : NaN; return Number.isFinite(v) ? v : dflt; }

    function saveSlskd(silent) {
        var url = _byId('video-slskd-url');
        if (!url) return Promise.resolve();   // section not in DOM
        return fetch(SLSKD_URL, {
            method: 'POST', headers: { 'Content-Type': 'application/json', 'Accept': 'application/json' },
            body: JSON.stringify({
                slskd_url: url.value,
                api_key: _byId('video-slskd-api-key') ? _byId('video-slskd-api-key').value : '',
                search_timeout: _num('video-slskd-search-timeout', 60),
                search_timeout_buffer: _num('video-slskd-search-timeout-buffer', 15),
                search_min_delay_seconds: _num('video-slskd-search-min-delay', 0),
                min_peer_upload_speed: _num('video-slskd-min-peer-speed', 0),
                max_peer_queue: _num('video-slskd-max-peer-queue', 0),
                download_timeout: _num('video-slskd-download-timeout', 10) * 60,   // minutes → seconds
                auto_clear_searches: _byId('video-slskd-auto-clear') ? _byId('video-slskd-auto-clear').checked : true,
            })
        }).then(function () { if (!silent) toast('slskd settings saved (shared with Music)', 'success'); })
          .catch(function () { /* ignore */ });
    }

    function wireSlskd() {
        var ids = ['video-slskd-url', 'video-slskd-api-key', 'video-slskd-search-timeout',
            'video-slskd-search-timeout-buffer', 'video-slskd-search-min-delay',
            'video-slskd-min-peer-speed', 'video-slskd-max-peer-queue',
            'video-slskd-download-timeout', 'video-slskd-auto-clear'];
        ids.forEach(function (id) {
            var el = _byId(id);
            if (el && !el._vsWired) { el._vsWired = true; el.addEventListener('change', function () { saveSlskd(true); }); }
        });
    }

    function wireDownloads() {
        var ms = document.getElementById('video-download-mode');
        if (ms && !ms._vdWired) {
            ms._vdWired = true;
            ms.addEventListener('change', function () {
                _videoMode = ms.value; updateVideoSourceUI(); saveDownloads(true);
            });
        }
        var host = document.getElementById('video-hybrid-rows');
        if (host && !host._vdWired) {
            host._vdWired = true;
            host.addEventListener('click', function (e) {
                var mv = e.target.closest('[data-vh-move]');
                if (mv) moveVH(mv.getAttribute('data-vh-move'), parseInt(mv.getAttribute('data-dir'), 10));
            });
            host.addEventListener('change', function (e) {
                var tg = e.target.closest('[data-vh-toggle]');
                if (tg) toggleVH(tg.getAttribute('data-vh-toggle'), tg.checked);
            });
        }
        // Folder inputs save on change too.
        ['video-download-path', 'video-movies-path', 'video-tv-path', 'video-youtube-path'].forEach(function (id) {
            var el = document.getElementById(id);
            if (el && !el._vdWired) { el._vdWired = true; el.addEventListener('change', function () { saveDownloads(true); }); }
        });
    }

    // ── Video quality profile (resolution tiers + source/codec/HDR/size) ──
    // ── named profiles (per-title assignment; arr-parity P2) ─────────────────
    // The editor edits ONE profile at a time; the bar above it picks which.
    // id 0 = Default (the classic single profile), >=1 = named profiles.
    var _vqProfiles = [];
    var _vqSelectedId = 0;

    function _vqSelected() {
        for (var i = 0; i < _vqProfiles.length; i++) {
            if (_vqProfiles[i].id === _vqSelectedId) return _vqProfiles[i];
        }
        return _vqProfiles[0] || null;
    }

    function renderProfileBar() {
        var sel = document.querySelector('[data-vq-profile-select]');
        if (!sel) return;
        sel.innerHTML = _vqProfiles.map(function (p) {
            return '<option value="' + p.id + '"' + (p.id === _vqSelectedId ? ' selected' : '') + '>' +
                String(p.name).replace(/&/g, '&amp;').replace(/</g, '&lt;') + '</option>';
        }).join('');
        var named = _vqSelectedId > 0;
        var nameIn = document.querySelector('[data-vq-profile-name]');
        if (nameIn) {
            nameIn.classList.toggle('hidden', !named);
            if (named) nameIn.value = (_vqSelected() || {}).name || '';
        }
        var del = document.querySelector('[data-vq-profile-delete]');
        if (del) del.classList.toggle('hidden', !named);
    }

    function loadQuality() {
        fetch(QUALITY_URL + '/profiles', { headers: { 'Accept': 'application/json' } })
            .then(function (r) { return r.ok ? r.json() : null; })
            .then(function (d) {
                if (!d || !Array.isArray(d.profiles) || !d.profiles.length) return;
                _vqProfiles = d.profiles;
                var cur = _vqSelected();
                _vqSelectedId = cur ? cur.id : 0;
                _videoQuality = (cur || d.profiles[0]).profile;
                renderProfileBar();
                renderQuality();
                wireProfileBar();
                loadFormats();
            })
            .catch(function () { /* ignore */ });
    }

    // ── custom formats (arr-parity P3) ───────────────────────────────────────
    var _vqFormats = [];

    function loadFormats() {
        fetch(QUALITY_URL + '/formats', { headers: { 'Accept': 'application/json' } })
            .then(function (r) { return r.ok ? r.json() : null; })
            .then(function (d) {
                if (!d) return;
                _vqFormats = d.formats || [];
                renderFormats();
                wireFormats();
            })
            .catch(function () { /* ignore */ });
    }

    function renderFormats() {
        var host = document.getElementById('vq-format-rows');
        if (!host) return;
        var overrides = (_videoQuality && _videoQuality.format_scores) || {};
        host.innerHTML = _vqFormats.map(function (f) {
            var ov = overrides[String(f.id)];
            return '<div class="vq-fmt-row" data-vq-fmt="' + f.id + '">' +
                '<input class="vq-fmt-in" data-vq-fmt-f="name" value="' + escA(f.name) + '" placeholder="Name">' +
                '<input class="vq-fmt-in" data-vq-fmt-f="include" value="' + escA((f.include || []).join(', ')) + '" placeholder="match: term, /regex/">' +
                '<input class="vq-fmt-in" data-vq-fmt-f="exclude" value="' + escA((f.exclude || []).join(', ')) + '" placeholder="never: term, /regex/">' +
                '<input class="vq-fmt-in vq-fmt-num" data-vq-fmt-f="score" type="number" value="' + f.score + '" title="Default score">' +
                '<input class="vq-fmt-in vq-fmt-num" data-vq-fmt-f="override" type="number" value="' + (ov == null ? '' : ov) + '" placeholder="—" title="Score for the selected profile (blank = default)">' +
                '<button class="vq-fmt-del" type="button" data-vq-fmt-del="' + f.id + '" title="Delete format">✕</button>' +
                '</div>';
        }).join('') || '<div class="settings-hint" style="padding:6px 0;">No custom formats yet — releases rank purely by the ladder + tie-breakers.</div>';
        var minIn = document.getElementById('vq-min-format-score');
        if (minIn) minIn.value = (_videoQuality && _videoQuality.min_format_score) || 0;
    }

    function escA(s) { return String(s == null ? '' : s).replace(/&/g, '&amp;').replace(/"/g, '&quot;').replace(/</g, '&lt;'); }

    function _fmtFromRow(row) {
        var val = function (k) { var el = row.querySelector('[data-vq-fmt-f="' + k + '"]'); return el ? el.value : ''; };
        var split = function (s) { return s.split(',').map(function (x) { return x.trim(); }).filter(Boolean); };
        return { id: parseInt(row.getAttribute('data-vq-fmt'), 10),
                 name: val('name'), include: split(val('include')), exclude: split(val('exclude')),
                 score: parseInt(val('score'), 10) || 0 };
    }

    function wireFormats() {
        var host = document.getElementById('vq-format-rows');
        if (!host || host._vqWired) return;
        host._vqWired = true;
        host.addEventListener('change', function (e) {
            var row = e.target.closest('[data-vq-fmt]');
            if (!row) return;
            if (e.target.matches('[data-vq-fmt-f="override"]')) {
                // per-profile score override lives on the SELECTED profile
                if (!_videoQuality) return;
                var fs = _videoQuality.format_scores || (_videoQuality.format_scores = {});
                var v = e.target.value.trim();
                if (v === '') delete fs[row.getAttribute('data-vq-fmt')];
                else fs[row.getAttribute('data-vq-fmt')] = parseInt(v, 10) || 0;
                saveQuality(true);
                return;
            }
            var f = _fmtFromRow(row);
            fetch(QUALITY_URL + '/formats', {
                method: 'POST', headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(f)
            }).then(function (r) { return r.ok ? r.json() : null; })
              .then(function (res) {
                  if (!res || !res.success) { toast('A format needs a name and at least one term', 'error'); return; }
                  for (var i = 0; i < _vqFormats.length; i++) {
                      if (_vqFormats[i].id === res.id) { _vqFormats[i] = { id: res.id, name: res.name, include: res.include, exclude: res.exclude, score: res.score }; }
                  }
              })
              .catch(function () { toast('Couldn’t save the format', 'error'); });
        });
        host.addEventListener('click', function (e) {
            var del = e.target.closest('[data-vq-fmt-del]');
            if (!del) return;
            var fid = del.getAttribute('data-vq-fmt-del');
            fetch(QUALITY_URL + '/formats/' + fid, { method: 'DELETE' })
                .then(function (r) { return r.ok ? r.json() : null; })
                .then(function (res) {
                    if (!res || !res.success) throw new Error();
                    _vqFormats = _vqFormats.filter(function (f) { return String(f.id) !== fid; });
                    renderFormats();
                })
                .catch(function () { toast('Couldn’t delete the format', 'error'); });
        });
        var add = document.querySelector('[data-vq-format-add]');
        if (add && !add._vqWired) {
            add._vqWired = true;
            add.addEventListener('click', function () {
                fetch(QUALITY_URL + '/formats', {
                    method: 'POST', headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ name: 'New format', include: ['REPLACE-ME'], score: 10 })
                }).then(function (r) { return r.ok ? r.json() : null; })
                  .then(function (res) {
                      if (!res || !res.success) throw new Error();
                      _vqFormats.push({ id: res.id, name: res.name, include: res.include, exclude: res.exclude, score: res.score });
                      renderFormats();
                  })
                  .catch(function () { toast('Couldn’t add a format', 'error'); });
            });
        }
        var minIn = document.getElementById('vq-min-format-score');
        if (minIn && !minIn._vqWired) {
            minIn._vqWired = true;
            minIn.addEventListener('change', function () {
                if (!_videoQuality) return;
                _videoQuality.min_format_score = parseInt(minIn.value, 10) || 0;
                saveQuality(true);
            });
        }
    }

    function wireProfileBar() {
        var sel = document.querySelector('[data-vq-profile-select]');
        if (!sel || sel._vqWired) return;
        sel._vqWired = true;
        sel.addEventListener('change', function () {
            _vqSelectedId = parseInt(sel.value, 10) || 0;
            var cur = _vqSelected();
            if (cur) { _videoQuality = cur.profile; renderProfileBar(); renderQuality(); renderFormats(); }
        });
        var nameIn = document.querySelector('[data-vq-profile-name]');
        if (nameIn) nameIn.addEventListener('change', function () { saveQuality(true); });
        var nb = document.querySelector('[data-vq-profile-new]');
        if (nb) nb.addEventListener('click', function () {
            fetch(QUALITY_URL + '/profiles', {
                method: 'POST', headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ name: 'New profile', profile: _videoQuality })
            }).then(function (r) { return r.ok ? r.json() : null; })
              .then(function (res) {
                  if (!res || !res.success) throw new Error();
                  _vqSelectedId = res.id;
                  toast('Profile created — rename it, tweak it, then assign it from a title’s Manage panel', 'success');
                  loadQuality();
              })
              .catch(function () { toast('Couldn’t create the profile', 'error'); });
        });
        var db = document.querySelector('[data-vq-profile-delete]');
        if (db) db.addEventListener('click', function () {
            if (_vqSelectedId <= 0) return;
            var doDelete = function () {
                fetch(QUALITY_URL + '/profiles/' + _vqSelectedId, { method: 'DELETE' })
                    .then(function (r) { return r.ok ? r.json() : null; })
                    .then(function (res) {
                        if (!res || !res.success) throw new Error();
                        _vqSelectedId = 0;
                        toast('Profile deleted — titles using it fall back to Default', 'success');
                        loadQuality();
                    })
                    .catch(function () { toast('Couldn’t delete the profile', 'error'); });
            };
            if (typeof showConfirmDialog === 'function') {
                showConfirmDialog({
                    title: 'Delete this quality profile?',
                    message: 'Titles assigned to it will use the Default profile instead.',
                    confirmText: 'Delete', destructive: true,
                }).then(function (ok) { if (ok) doDelete(); });
            } else { doDelete(); }
        });
    }

    function _vqSizeLabel(id, v) {
        var lab = document.getElementById(id);
        if (lab) lab.textContent = v ? (v + ' GB') : 'No limit';
    }

    function _vqSeg(id, attr, value) {
        var seg = document.getElementById(id);
        if (!seg) return;
        Array.prototype.forEach.call(seg.querySelectorAll('[' + attr + ']'), function (b) {
            b.classList.toggle('active', b.getAttribute(attr) === value);
        });
    }

    function renderQuality() {
        var p = _videoQuality;
        if (!p) return;
        var tiers = Array.isArray(p.tiers) ? p.tiers : [];

        // Quality ladder — ranked, toggleable (same .hybrid-source-item styling as Download Source).
        var host = document.getElementById('vq-tier-rows');
        if (host) {
            host.innerHTML = tiers.map(function (t, i) {
                return '<div class="hybrid-source-item' + (t.enabled ? '' : ' disabled') + '">' +
                    '<span class="hybrid-source-arrows">' +
                    '<button type="button" class="hybrid-arrow-btn" data-vq-tier-move="' + t.key + '" data-dir="-1"' + (i === 0 ? ' disabled' : '') + ' title="Move up">▲</button>' +
                    '<button type="button" class="hybrid-arrow-btn" data-vq-tier-move="' + t.key + '" data-dir="1"' + (i === tiers.length - 1 ? ' disabled' : '') + ' title="Move down">▼</button>' +
                    '</span>' +
                    '<span class="hybrid-source-name">' + (TIER_LABEL[t.key] || t.key) + '</span>' +
                    '<span class="hybrid-source-priority">' + (i + 1) + '</span>' +
                    '<label class="hybrid-source-toggle"><input type="checkbox" data-vq-tier-toggle="' + t.key + '"' + (t.enabled ? ' checked' : '') + '><span class="toggle-track"></span></label>' +
                    '</div>';
            }).join('');
        }

        // Cutoff — a loose resolution target (static <option>s); "" = always upgrade.
        var cut = document.getElementById('vq-cutoff');
        if (cut) cut.value = (p.cutoff_resolution == null ? '1080p' : p.cutoff_resolution);

        // Hard rejects — toggle chips (on = blocked).
        var rj = document.getElementById('vq-rejects');
        if (rj) {
            var set = Array.isArray(p.rejects) ? p.rejects : [];
            rj.innerHTML = REJECT_ORDER.map(function (k) {
                var on = set.indexOf(k) !== -1;
                return '<button type="button" class="vq-chip' + (on ? ' on' : '') + '" data-vq-reject="' + k + '">' + (REJECT_LABEL[k] || k) + '</button>';
            }).join('');
        }

        // Soft preferences.
        _vqSeg('vq-codec', 'data-vq-codec', p.prefer_codec);
        _vqSeg('vq-hdr', 'data-vq-hdr', p.prefer_hdr);
        _vqSeg('vq-audio', 'data-vq-audio', p.prefer_audio);
        var rep = document.getElementById('vq-prefer-repack'); if (rep) rep.checked = !!p.prefer_repack;

        // Size guard — split by runtime so a movie and an episode aren't judged the same.
        var mv = document.getElementById('vq-movie-size'); if (mv) mv.value = p.max_movie_gb || 0;
        var ep = document.getElementById('vq-episode-size'); if (ep) ep.value = p.max_episode_gb || 0;
        _vqSizeLabel('vq-movie-label', p.max_movie_gb || 0);
        _vqSizeLabel('vq-episode-label', p.max_episode_gb || 0);
    }

    function moveTier(k, dir) {
        var p = _videoQuality; if (!p || !Array.isArray(p.tiers)) return;
        var arr = p.tiers, i = -1;
        for (var n = 0; n < arr.length; n++) { if (arr[n].key === k) { i = n; break; } }
        var j = i + dir;
        if (i < 0 || j < 0 || j >= arr.length) return;
        var tmp = arr[i]; arr[i] = arr[j]; arr[j] = tmp;   // swap
        renderQuality(); saveQuality(true);
    }

    function toggleReject(k) {
        var p = _videoQuality; if (!p) return;
        if (!Array.isArray(p.rejects)) p.rejects = [];
        var i = p.rejects.indexOf(k);
        if (i === -1) p.rejects.push(k); else p.rejects.splice(i, 1);
        renderQuality(); saveQuality(true);
    }

    function saveQuality(silent) {
        if (!_videoQuality) return Promise.resolve();
        if (_vqSelectedId > 0) {
            // a NAMED profile — routed through the profiles endpoint (P2)
            var nameIn = document.querySelector('[data-vq-profile-name]');
            var cur = _vqSelected() || {};
            return fetch(QUALITY_URL + '/profiles', {
                method: 'POST', headers: { 'Content-Type': 'application/json', 'Accept': 'application/json' },
                body: JSON.stringify({ id: _vqSelectedId,
                                       name: (nameIn && nameIn.value.trim()) || cur.name || 'Unnamed profile',
                                       profile: _videoQuality })
            }).then(function (r) { return r.ok ? r.json() : null; })
              .then(function (d) {
                  if (d && d.profile) { _videoQuality = d.profile; cur.name = d.name; cur.profile = d.profile; }
                  renderProfileBar();
                  if (!silent) toast('Quality profile saved', 'success');
              })
              .catch(function () { /* ignore */ });
        }
        return fetch(QUALITY_URL, {
            method: 'POST', headers: { 'Content-Type': 'application/json', 'Accept': 'application/json' },
            body: JSON.stringify(_videoQuality)
        }).then(function (r) { return r.ok ? r.json() : null; })
          .then(function (d) { if (d) _videoQuality = d; if (!silent) toast('Quality profile saved', 'success'); })
          .catch(function () { /* ignore */ });
    }

    // Delegated handlers for the quality profile (rows re-render, so delegate).
    function wireQuality() {
        var sec = document.getElementById('vq-tier-rows');
        if (!sec) return;
        var card = sec.closest('.settings-group');
        if (!card || card._vqWired) return;
        card._vqWired = true;
        card.addEventListener('click', function (e) {
            var tm = e.target.closest('[data-vq-tier-move]');
            if (tm) { moveTier(tm.getAttribute('data-vq-tier-move'), parseInt(tm.getAttribute('data-dir'), 10)); return; }
            var rj = e.target.closest('[data-vq-reject]');
            if (rj) { toggleReject(rj.getAttribute('data-vq-reject')); return; }
            if (!_videoQuality) return;
            var cd = e.target.closest('[data-vq-codec]');
            if (cd) { _videoQuality.prefer_codec = cd.getAttribute('data-vq-codec'); renderQuality(); saveQuality(true); return; }
            var hd = e.target.closest('[data-vq-hdr]');
            if (hd) { _videoQuality.prefer_hdr = hd.getAttribute('data-vq-hdr'); renderQuality(); saveQuality(true); return; }
            var au = e.target.closest('[data-vq-audio]');
            if (au) { _videoQuality.prefer_audio = au.getAttribute('data-vq-audio'); renderQuality(); saveQuality(true); return; }
        });
        card.addEventListener('change', function (e) {
            if (!_videoQuality) return;
            var tt = e.target.closest('[data-vq-tier-toggle]');
            if (tt) {
                var key = tt.getAttribute('data-vq-tier-toggle');
                var arr = _videoQuality.tiers || [];
                for (var n = 0; n < arr.length; n++) { if (arr[n].key === key) { arr[n].enabled = tt.checked; break; } }
                renderQuality(); saveQuality(true); return;
            }
            if (e.target.id === 'vq-cutoff') { _videoQuality.cutoff_resolution = e.target.value; saveQuality(true); return; }
            if (e.target.id === 'vq-prefer-repack') { _videoQuality.prefer_repack = e.target.checked; saveQuality(true); return; }
            if (e.target.id === 'vq-movie-size') { _videoQuality.max_movie_gb = parseInt(e.target.value, 10) || 0; saveQuality(true); return; }
            if (e.target.id === 'vq-episode-size') { _videoQuality.max_episode_gb = parseInt(e.target.value, 10) || 0; saveQuality(true); return; }
        });
        card.addEventListener('input', function (e) {
            if (e.target.id === 'vq-movie-size') { _vqSizeLabel('vq-movie-label', parseInt(e.target.value, 10) || 0); return; }
            if (e.target.id === 'vq-episode-size') { _vqSizeLabel('vq-episode-label', parseInt(e.target.value, 10) || 0); return; }
        });
    }

    // ── YouTube quality (separate, smaller yt-dlp profile) ────────────────────
    function loadYtQuality() {
        fetch(YT_QUALITY_URL, { headers: { 'Accept': 'application/json' } })
            .then(function (r) { return r.ok ? r.json() : null; })
            .then(function (d) { if (d) { _videoYtQuality = d; renderYtQuality(); } })
            .catch(function () { /* ignore */ });
    }

    function renderYtQuality() {
        var p = _videoYtQuality;
        if (!p) return;
        var res = document.getElementById('yq-resolution'); if (res) res.value = p.max_resolution || '1080p';
        _vqSeg('yq-codec', 'data-yq-codec', p.video_codec);
        _vqSeg('yq-container', 'data-yq-container', p.container);
        var fps = document.getElementById('yq-60fps'); if (fps) fps.checked = !!p.prefer_60fps;
        var hdr = document.getElementById('yq-hdr'); if (hdr) hdr.checked = !!p.allow_hdr;
    }

    function saveYtQuality(silent) {
        if (!_videoYtQuality) return Promise.resolve();
        return fetch(YT_QUALITY_URL, {
            method: 'POST', headers: { 'Content-Type': 'application/json', 'Accept': 'application/json' },
            body: JSON.stringify(_videoYtQuality)
        }).then(function (r) { return r.ok ? r.json() : null; })
          .then(function (d) { if (d) _videoYtQuality = d; if (!silent) toast('YouTube quality saved', 'success'); })
          .catch(function () { /* ignore */ });
    }

    function wireYtQuality() {
        var seg = document.getElementById('yq-codec');
        if (!seg) return;
        var card = seg.closest('.settings-group');
        if (!card || card._yqWired) return;
        card._yqWired = true;
        card.addEventListener('click', function (e) {
            if (!_videoYtQuality) return;
            var cd = e.target.closest('[data-yq-codec]');
            if (cd) { _videoYtQuality.video_codec = cd.getAttribute('data-yq-codec'); renderYtQuality(); saveYtQuality(true); return; }
            var ct = e.target.closest('[data-yq-container]');
            if (ct) { _videoYtQuality.container = ct.getAttribute('data-yq-container'); renderYtQuality(); saveYtQuality(true); return; }
        });
        card.addEventListener('change', function (e) {
            if (!_videoYtQuality) return;
            if (e.target.id === 'yq-resolution') { _videoYtQuality.max_resolution = e.target.value; saveYtQuality(true); return; }
            if (e.target.id === 'yq-60fps') { _videoYtQuality.prefer_60fps = e.target.checked; saveYtQuality(true); return; }
            if (e.target.id === 'yq-hdr') { _videoYtQuality.allow_hdr = e.target.checked; saveYtQuality(true); return; }
        });
    }

    function saveKeys(silent) {
        var t = document.getElementById('tmdb-api-key');
        var v = document.getElementById('tvdb-api-key');
        var o = document.getElementById('omdb-api-key');
        var fa = document.getElementById('fanart-api-key');
        var sub = document.getElementById('opensubtitles-api-key');
        var trakt = document.getElementById('trakt-api-key');
        var ryd = document.getElementById('video-ryd-enabled');
        var sb = document.getElementById('video-sponsorblock-enabled');
        var dea = document.getElementById('video-dearrow-enabled');
        var tvm = document.getElementById('video-tvmaze-enabled');
        var anl = document.getElementById('video-anilist-enabled');
        var wkd = document.getElementById('video-wikidata-enabled');
        return fetch(CONFIG_URL, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json', 'Accept': 'application/json' },
            body: JSON.stringify({
                tmdb_api_key: t ? t.value : '', tvdb_api_key: v ? v.value : '',
                omdb_api_key: o ? o.value : '',
                fanart_api_key: fa ? fa.value : '',
                opensubtitles_api_key: sub ? sub.value : '',
                trakt_api_key: trakt ? trakt.value : '',
                mdblist_api_key: (document.getElementById('mdblist-api-key') || {}).value || '',
                ryd_enabled: ryd ? ryd.checked : true,
                sponsorblock_enabled: sb ? sb.checked : true,
                dearrow_enabled: dea ? dea.checked : true,
                tvmaze_enabled: tvm ? tvm.checked : true,
                anilist_enabled: anl ? anl.checked : false,
                wikidata_enabled: wkd ? wkd.checked : true,
            })
        }).then(function () { if (!silent) toast('API keys saved', 'success'); })
          .catch(function () { /* ignore */ });
    }

    function toast(msg, type) {
        if (typeof showToast === 'function') showToast(msg, type);  // shared shell helper
    }

    // Mirrors music's testConnection(): save the key, then hit the test
    // endpoint, then toast the result. Isolated -> /api/video/enrichment/<svc>/test.
    function testConnection(svc) {
        var name = svc.toUpperCase();
        toast('Testing ' + name + ' connection…', 'info');
        saveKeys(true).then(function () {
            return fetch('/api/video/enrichment/' + svc + '/test',
                { method: 'POST', headers: { 'Accept': 'application/json' } });
        }).then(function (r) { return r.json(); }).then(function (res) {
            if (res && res.success) toast(res.message || (name + ' connection successful'), 'success');
            else toast(name + ' connection failed: ' + ((res && res.error) || 'unknown'), 'error');
        }).catch(function () { toast('Failed to test ' + name + ' connection', 'error'); });
    }

    // ── Library Organization (naming templates + post-process toggles) ───────
    var ORG_URL = '/api/video/organization';
    var _videoOrg = null;
    // Sample values for the live preview (mirrors what the importer feeds the engine).
    var _ORG_MOVIE_EG = { title: 'The Matrix', titlefirst: 'T', year: '1999', quality: 'Bluray-1080p',
        resolution: '1080p', source: 'Bluray', codec: 'HEVC', edition: '', tmdbid: '603', imdbid: 'tt0133093' };
    var _ORG_EP_EG = { series: 'Breaking Bad', season: '01', seasonraw: '1', episode: '01', episodetitle: 'Pilot',
        year: '2008', quality: 'WEBDL-1080p', resolution: '1080p', source: 'WEBDL', codec: 'H264', tvdbid: '81189' };
    var _ORG_YT_EG = { channel: 'Veritasium', title: 'How Electricity Actually Works', year: '2024',
        date: '2024-03-15', month: '03', day: '15', sxe: 's2024e0315', videoid: 'oI_X2cMHNe0' };

    function _orgSanitize(v) {
        return String(v == null ? '' : v).replace(/[\\/:*?"<>|\x00-\x1f]/g, '')
            .replace(/\s+/g, ' ').trim().replace(/[ .]+$/, '');
    }
    // Client-side mirror of core/video/organization.render_template + _tidy_component
    // (kept in lockstep) so the preview matches what actually lands on disk.
    function _orgRender(tmpl, vals) {
        var keys = Object.keys(vals).sort(function (a, b) { return b.length - a.length; });
        var out = String(tmpl || '');
        keys.forEach(function (k) { out = out.split('${' + k + '}').join(_orgSanitize(vals[k])); });
        keys.forEach(function (k) { out = out.split('$' + k).join(_orgSanitize(vals[k])); });
        return out.split('/').map(function (p) {
            p = p.replace(/\s+-\s+(?=(\s|$))/g, ' ').replace(/\(\s*\)/g, '').replace(/\[\s*\]/g, '');
            p = p.replace(/\s+/g, ' ').trim().replace(/^-+|-+$/g, '').trim().replace(/[ .]+$/, '');
            return p;
        }).filter(function (p) { return p !== ''; }).join('/');
    }
    function renderOrgPreview() {
        var mt = document.getElementById('vo-movie-template');
        var et = document.getElementById('vo-episode-template');
        var yt = document.getElementById('vo-youtube-template');
        var mp = document.getElementById('vo-movie-preview');
        var ep = document.getElementById('vo-episode-preview');
        var yp = document.getElementById('vo-youtube-preview');
        if (mp && mt) mp.textContent = _orgRender(mt.value || mt.placeholder, _ORG_MOVIE_EG) + '.mkv';
        if (ep && et) ep.textContent = _orgRender(et.value || et.placeholder, _ORG_EP_EG) + '.mkv';
        if (yp && yt) yp.textContent = _orgRender(yt.value || yt.placeholder, _ORG_YT_EG) + '.mp4';
    }
    function fillOrg() {
        if (!_videoOrg) return;
        var set = function (id, v) { var el = document.getElementById(id); if (el) el.value = v; };
        var chk = function (id, v) { var el = document.getElementById(id); if (el) el.checked = !!v; };
        set('vo-movie-template', _videoOrg.movie_template || '');
        set('vo-episode-template', _videoOrg.episode_template || '');
        set('vo-youtube-template', _videoOrg.youtube_template || '');
        set('vo-transfer-mode', _videoOrg.transfer_mode || 'copy');
        chk('vo-verify', _videoOrg.verify_with_ffprobe);
        chk('vo-replace', _videoOrg.replace_existing);
        chk('vo-subs', _videoOrg.carry_subtitles);
        chk('vo-artwork', _videoOrg.save_artwork);
        chk('vo-nfo', _videoOrg.write_nfo);
        chk('vo-subs-dl', _videoOrg.download_subtitles);
        set('vo-sub-langs', _videoOrg.subtitle_langs || 'en');
        chk('vo-recycle', _videoOrg.recycle_deletes);
        set('vo-recycle-days', _videoOrg.recycle_keep_days || 7);
        set('vo-recycle-path', _videoOrg.recycle_path || '');
        set('vo-min-free', _videoOrg.min_free_disk_gb || 0);
        set('vo-yt-follow-count', _videoOrg.youtube_follow_count == null ? 5 : _videoOrg.youtube_follow_count);
        set('vo-sponsorblock', _videoOrg.youtube_sponsorblock || 'off');
        chk('vo-yt-subs', _videoOrg.youtube_embed_subs);
        renderOrgPreview();
    }
    function loadOrganization() {
        fetch(ORG_URL, { headers: { 'Accept': 'application/json' } })
            .then(function (r) { return r.ok ? r.json() : null; })
            .then(function (d) { if (d) { _videoOrg = d; fillOrg(); } })
            .catch(function () { /* ignore */ });
    }
    function collectOrg() {
        var val = function (id) { var el = document.getElementById(id); return el ? el.value : ''; };
        var on = function (id) { var el = document.getElementById(id); return !!(el && el.checked); };
        return {
            movie_template: val('vo-movie-template'),
            episode_template: val('vo-episode-template'),
            youtube_template: val('vo-youtube-template'),
            transfer_mode: val('vo-transfer-mode'),
            verify_with_ffprobe: on('vo-verify'),
            replace_existing: on('vo-replace'),
            carry_subtitles: on('vo-subs'),
            save_artwork: on('vo-artwork'),
            write_nfo: on('vo-nfo'),
            download_subtitles: on('vo-subs-dl'),
            subtitle_langs: val('vo-sub-langs'),
            recycle_deletes: on('vo-recycle'),
            recycle_keep_days: val('vo-recycle-days'),
            recycle_path: val('vo-recycle-path'),
            min_free_disk_gb: val('vo-min-free'),
            youtube_follow_count: val('vo-yt-follow-count'),
            youtube_sponsorblock: val('vo-sponsorblock'),
            youtube_embed_subs: on('vo-yt-subs')
        };
    }
    function saveOrganization(silent) {
        return fetch(ORG_URL, {
            method: 'POST', headers: { 'Content-Type': 'application/json', 'Accept': 'application/json' },
            body: JSON.stringify(collectOrg())
        }).then(function (r) { return r.ok ? r.json() : null; })
          .then(function (d) { if (d) { _videoOrg = d; } if (!silent) toast('Library organization saved', 'success'); })
          .catch(function () { /* ignore */ });
    }
    function wireOrganization() {
        var anchor = document.getElementById('vo-movie-template');
        if (!anchor) return;
        var card = anchor.closest('.settings-group');
        if (!card || card._voWired) return;
        card._voWired = true;
        ['vo-movie-template', 'vo-episode-template', 'vo-youtube-template'].forEach(function (id) {
            var el = document.getElementById(id);
            if (!el) return;
            el.addEventListener('input', renderOrgPreview);          // live preview while typing
            el.addEventListener('change', function () { saveOrganization(false); });
        });
        ['vo-transfer-mode', 'vo-verify', 'vo-replace', 'vo-subs', 'vo-artwork', 'vo-nfo',
            'vo-subs-dl', 'vo-sub-langs', 'vo-recycle', 'vo-recycle-days', 'vo-recycle-path',
            'vo-sponsorblock', 'vo-yt-subs', 'vo-min-free', 'vo-yt-follow-count'].forEach(function (id) {
            var el = document.getElementById(id);
            if (el) el.addEventListener('change', function () { saveOrganization(false); });
        });
        var reset = document.getElementById('vo-reset');
        if (reset) reset.addEventListener('click', function () {
            // POST blank templates + standard toggles; the backend normalises to the
            // Radarr/Sonarr defaults and echoes them back.
            fetch(ORG_URL, {
                method: 'POST', headers: { 'Content-Type': 'application/json', 'Accept': 'application/json' },
                body: JSON.stringify({ movie_template: '', episode_template: '', youtube_template: '',
                    transfer_mode: 'copy', verify_with_ffprobe: true, replace_existing: true,
                    carry_subtitles: true, save_artwork: false, write_nfo: false,
                    download_subtitles: false, subtitle_langs: 'en',
                    recycle_deletes: true, recycle_keep_days: 7, recycle_path: '',
                    youtube_sponsorblock: 'off', youtube_embed_subs: false, min_free_disk_gb: 0,
                    youtube_follow_count: 5 })
            }).then(function (r) { return r.ok ? r.json() : null; })
              .then(function (d) { if (d) { _videoOrg = d; fillOrg(); toast('Reset to the standard layout', 'success'); } });
        });
    }

    function onPageShown(e) {
        if (e && e.detail !== PAGE_ID) return;
        loadServer();
        loadConn();
        load();
        loadKeys();
        loadDownloads();
        loadImportLists();
        loadNotify();
        wireDownloads();
        loadQuality();
        wireQuality();
        loadYtQuality();
        wireYtQuality();
        loadSlskd();
        wireSlskd();
        loadOrganization();
        wireOrganization();
    }

    function init() {
        // Save the moment a library is picked — same behaviour as the music
        // 'Music Library' selector above (which saves on change, no button).
        // NB: wrap each handler so the DOM Event isn't passed as the function's
        // first arg (which is our `silent` flag — it would suppress the toast).
        var selects = document.querySelectorAll('[data-video-lib-select]');
        for (var i = 0; i < selects.length; i++) {
            selects[i].addEventListener('change', function () { save(); });
        }
        // Enrichment keys save on blur/change (turns the workers on).
        ['tmdb-api-key', 'tvdb-api-key', 'omdb-api-key',
            'fanart-api-key', 'opensubtitles-api-key', 'trakt-api-key',
            'video-ryd-enabled', 'video-sponsorblock-enabled', 'video-dearrow-enabled',
            'video-tvmaze-enabled', 'video-anilist-enabled',
            'video-wikidata-enabled'].forEach(function (id) {
            var el = document.getElementById(id);
            if (el) el.addEventListener('change', function () { saveKeys(); });
        });
        var autoplay = document.getElementById('video-billboard-autoplay');
        if (autoplay) autoplay.addEventListener('change', function () { savePrefs(); });
        var region = document.getElementById('video-watch-region');
        if (region) region.addEventListener('change', function () { savePrefs(); });
        // Server toggle (Plex/Jellyfin) — select + reveal that server's config.
        var toggles = document.querySelectorAll('[data-video-server-toggle]');
        for (var t = 0; t < toggles.length; t++) {
            (function (b) {
                b.addEventListener('click', function () {
                    pickServer(b.getAttribute('data-video-server-toggle'));
                });
            })(toggles[t]);
        }
        // Server Connection (video's own creds) — save on change, test on click.
        var connInputs = document.querySelectorAll('[data-video-conn]');
        for (var c = 0; c < connInputs.length; c++) {
            connInputs[c].addEventListener('change', function () { saveConn(); });
        }
        var connTests = document.querySelectorAll('[data-video-conn-test]');
        for (var d = 0; d < connTests.length; d++) {
            (function (b) {
                b.addEventListener('click', function () {
                    testConn(b.getAttribute('data-video-conn-test'));
                });
            })(connTests[d]);
        }
        // Jellyfin user pick → store it + refresh that user's libraries.
        var userSel = document.querySelector('[data-video-jellyfin-user]');
        if (userSel) userSel.addEventListener('change', function () { selectJellyfinUser(userSel.value); });
        // Per-connection Test buttons (same behaviour as music's testConnection).
        var testBtns = document.querySelectorAll('[data-video-test-service]');
        for (var k = 0; k < testBtns.length; k++) {
            (function (b) {
                b.addEventListener('click', function () {
                    testConnection(b.getAttribute('data-video-test-service'));
                });
            })(testBtns[k]);
        }
        // The shared "Save Settings" button belongs to MUSIC (it runs music's
        // saveSettings, which would fire a music-config write from the video page).
        // On the video side we intercept it (capture phase, before music's bubble
        // listener), block music's handler, flush all video settings, and toast —
        // so the button is real here and can't reach into music. Music side: this
        // does nothing, so its behaviour is unchanged.
        document.addEventListener('click', function (e) {
            if (document.body.getAttribute('data-side') !== 'video') return;
            if (!e.target.closest('#save-settings')) return;
            e.preventDefault();
            e.stopImmediatePropagation();
            Promise.all([saveConn(true), save(true), saveKeys(true), savePrefs(true),
                         saveDownloads(true), saveQuality(true), saveYtQuality(true), saveSlskd(true)])
                .then(function () { toast('Settings saved', 'success'); })
                .catch(function () { toast('Some settings could not be saved', 'error'); });
        }, true);
        document.addEventListener('soulsync:video-page-shown', onPageShown);
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }
})();
