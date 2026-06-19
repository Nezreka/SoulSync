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
    var SLSKD_URL = '/api/video/downloads/slskd';
    var _videoQuality = null;
    var RES_LABEL = { '2160p': '4K (2160p)', '1080p': '1080p', '720p': '720p', '480p': '480p (SD)' };
    var SRC_LABEL = { 'bluray': 'BluRay', 'web-dl': 'WEB-DL', 'webrip': 'WEBRip', 'hdtv': 'HDTV' };

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
    var _videoMode = 'soulseek';
    var _videoHybrid = ['soulseek'];

    function loadDownloads() {
        fetch(DOWNLOADS_URL, { headers: { 'Accept': 'application/json' } })
            .then(function (r) { return r.ok ? r.json() : null; })
            .then(function (d) {
                if (!d) return;
                var dl = document.getElementById('video-download-path');
                if (dl && d.download_path != null) dl.value = d.download_path;
                var tr = document.getElementById('video-transfer-path');
                if (tr && d.transfer_path != null) tr.value = d.transfer_path;
                _videoMode = d.download_mode || 'soulseek';
                _videoHybrid = (d.hybrid_order && d.hybrid_order.length) ? d.hybrid_order : ['soulseek'];
                var ms = document.getElementById('video-download-mode');
                if (ms) ms.value = _videoMode;
                renderVideoHybrid();
                updateVideoSourceUI();
            })
            .catch(function () { /* ignore */ });
    }

    function saveDownloads(silent) {
        var dl = document.getElementById('video-download-path');
        var tr = document.getElementById('video-transfer-path');
        return fetch(DOWNLOADS_URL, {
            method: 'POST', headers: { 'Content-Type': 'application/json', 'Accept': 'application/json' },
            body: JSON.stringify({
                download_path: dl ? dl.value : '',
                transfer_path: tr ? tr.value : '',
                download_mode: _videoMode,
                hybrid_order: _videoHybrid,
            })
        }).then(function () { if (!silent) toast('Download folders saved', 'success'); })
          .catch(function () { /* ignore */ });
    }

    // Hybrid chain: enabled sources (ordered) + disabled ones appended. No
    // album-level/track-level distinction — that's a music-only concept.
    function renderVideoHybrid() {
        var host = document.getElementById('video-hybrid-rows');
        if (!host) return;
        var enabled = _videoHybrid.filter(function (s) { return VIDEO_SOURCES.indexOf(s) >= 0; });
        var disabled = VIDEO_SOURCES.filter(function (s) { return enabled.indexOf(s) < 0; });
        var rows = enabled.map(function (s, i) {
            return '<div class="vq-row">' +
                '<span class="vq-arrows">' +
                '<button type="button" class="vq-arrow" data-vh-move="' + s + '" data-dir="-1"' + (i === 0 ? ' disabled' : '') + '>▲</button>' +
                '<button type="button" class="vq-arrow" data-vh-move="' + s + '" data-dir="1"' + (i === enabled.length - 1 ? ' disabled' : '') + '>▼</button>' +
                '</span>' +
                '<span class="vq-row-name">' + SRC_DL_LABEL[s] + '</span>' +
                '<span class="vq-row-prio">' + (i + 1) + '</span>' +
                '<label class="vq-toggle"><input type="checkbox" data-vh-toggle="' + s + '" checked><span class="vq-toggle-track"></span></label>' +
                '</div>';
        });
        rows = rows.concat(disabled.map(function (s) {
            return '<div class="vq-row vq-row--off">' +
                '<span class="vq-arrows"><button type="button" class="vq-arrow" disabled>▲</button><button type="button" class="vq-arrow" disabled>▼</button></span>' +
                '<span class="vq-row-name">' + SRC_DL_LABEL[s] + '</span>' +
                '<span class="vq-row-prio"></span>' +
                '<label class="vq-toggle"><input type="checkbox" data-vh-toggle="' + s + '"><span class="vq-toggle-track"></span></label>' +
                '</div>';
        }));
        host.innerHTML = rows.join('');
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
        ['video-download-path', 'video-transfer-path'].forEach(function (id) {
            var el = document.getElementById(id);
            if (el && !el._vdWired) { el._vdWired = true; el.addEventListener('change', function () { saveDownloads(true); }); }
        });
    }

    // ── Video quality profile (resolution tiers + source/codec/HDR/size) ──
    function loadQuality() {
        fetch(QUALITY_URL, { headers: { 'Accept': 'application/json' } })
            .then(function (r) { return r.ok ? r.json() : null; })
            .then(function (d) { if (d) { _videoQuality = d; renderQuality(); } })
            .catch(function () { /* ignore */ });
    }

    function renderQuality() {
        var p = _videoQuality;
        if (!p) return;
        var resHost = document.getElementById('vq-resolution-rows');
        if (resHost) {
            var keys = Object.keys(p.resolutions).sort(function (a, b) {
                return p.resolutions[a].priority - p.resolutions[b].priority;
            });
            resHost.innerHTML = keys.map(function (k, i) {
                var r = p.resolutions[k];
                return '<div class="vq-row' + (r.enabled ? '' : ' vq-row--off') + '">' +
                    '<span class="vq-arrows">' +
                    '<button type="button" class="vq-arrow" data-vq-res-move="' + k + '" data-dir="-1"' + (i === 0 ? ' disabled' : '') + '>▲</button>' +
                    '<button type="button" class="vq-arrow" data-vq-res-move="' + k + '" data-dir="1"' + (i === keys.length - 1 ? ' disabled' : '') + '>▼</button>' +
                    '</span>' +
                    '<span class="vq-row-name">' + (RES_LABEL[k] || k) + '</span>' +
                    '<span class="vq-row-prio">' + (i + 1) + '</span>' +
                    '<label class="vq-toggle"><input type="checkbox" data-vq-res-toggle="' + k + '"' + (r.enabled ? ' checked' : '') + '><span class="vq-toggle-track"></span></label>' +
                    '</div>';
            }).join('');
        }
        var srcHost = document.getElementById('vq-source-rows');
        if (srcHost) {
            srcHost.innerHTML = p.source_priority.map(function (s, i) {
                return '<div class="vq-row">' +
                    '<span class="vq-arrows">' +
                    '<button type="button" class="vq-arrow" data-vq-src-move="' + s + '" data-dir="-1"' + (i === 0 ? ' disabled' : '') + '>▲</button>' +
                    '<button type="button" class="vq-arrow" data-vq-src-move="' + s + '" data-dir="1"' + (i === p.source_priority.length - 1 ? ' disabled' : '') + '>▼</button>' +
                    '</span>' +
                    '<span class="vq-row-name">' + (SRC_LABEL[s] || s) + '</span>' +
                    '<span class="vq-row-prio">' + (i + 1) + '</span>' +
                    '</div>';
            }).join('');
        }
        var seg = document.getElementById('vq-codec');
        if (seg) {
            Array.prototype.forEach.call(seg.querySelectorAll('[data-vq-codec]'), function (b) {
                b.classList.toggle('active', b.getAttribute('data-vq-codec') === p.codec);
            });
        }
        var hdr = document.getElementById('vq-prefer-hdr'); if (hdr) hdr.checked = !!p.prefer_hdr;
        var fb = document.getElementById('vq-fallback'); if (fb) fb.checked = p.fallback_enabled !== false;
        var sl = document.getElementById('vq-max-size'); if (sl) sl.value = p.max_size_gb || 0;
        var lab = document.getElementById('vq-size-label');
        if (lab) lab.textContent = p.max_size_gb ? (p.max_size_gb + ' GB') : 'No limit';
    }

    function moveRes(k, dir) {
        var p = _videoQuality; if (!p) return;
        var keys = Object.keys(p.resolutions).sort(function (a, b) {
            return p.resolutions[a].priority - p.resolutions[b].priority;
        });
        var i = keys.indexOf(k), j = i + dir;
        if (j < 0 || j >= keys.length) return;
        keys[i] = keys[j]; keys[j] = k;             // swap
        keys.forEach(function (key, idx) { p.resolutions[key].priority = idx + 1; });
        renderQuality(); saveQuality(true);
    }

    function moveSrc(s, dir) {
        var p = _videoQuality; if (!p) return;
        var arr = p.source_priority, i = arr.indexOf(s), j = i + dir;
        if (j < 0 || j >= arr.length) return;
        arr[i] = arr[j]; arr[j] = s;
        renderQuality(); saveQuality(true);
    }

    function saveQuality(silent) {
        if (!_videoQuality) return Promise.resolve();
        return fetch(QUALITY_URL, {
            method: 'POST', headers: { 'Content-Type': 'application/json', 'Accept': 'application/json' },
            body: JSON.stringify(_videoQuality)
        }).then(function (r) { return r.ok ? r.json() : null; })
          .then(function (d) { if (d) _videoQuality = d; if (!silent) toast('Quality profile saved', 'success'); })
          .catch(function () { /* ignore */ });
    }

    // Delegated handlers for the quality profile (rows re-render, so delegate).
    function wireQuality() {
        var sec = document.getElementById('vq-resolution-rows');
        if (!sec) return;
        var card = sec.closest('.settings-group');
        if (!card || card._vqWired) return;
        card._vqWired = true;
        card.addEventListener('click', function (e) {
            var rm = e.target.closest('[data-vq-res-move]');
            if (rm) { moveRes(rm.getAttribute('data-vq-res-move'), parseInt(rm.getAttribute('data-dir'), 10)); return; }
            var sm = e.target.closest('[data-vq-src-move]');
            if (sm) { moveSrc(sm.getAttribute('data-vq-src-move'), parseInt(sm.getAttribute('data-dir'), 10)); return; }
            var cd = e.target.closest('[data-vq-codec]');
            if (cd && _videoQuality) { _videoQuality.codec = cd.getAttribute('data-vq-codec'); renderQuality(); saveQuality(true); }
        });
        card.addEventListener('change', function (e) {
            if (!_videoQuality) return;
            var rt = e.target.closest('[data-vq-res-toggle]');
            if (rt) { _videoQuality.resolutions[rt.getAttribute('data-vq-res-toggle')].enabled = rt.checked; renderQuality(); saveQuality(true); return; }
            if (e.target.id === 'vq-prefer-hdr') { _videoQuality.prefer_hdr = e.target.checked; saveQuality(true); return; }
            if (e.target.id === 'vq-fallback') { _videoQuality.fallback_enabled = e.target.checked; saveQuality(true); return; }
            if (e.target.id === 'vq-max-size') { _videoQuality.max_size_gb = parseInt(e.target.value, 10) || 0; saveQuality(true); return; }
        });
        card.addEventListener('input', function (e) {
            if (e.target.id === 'vq-max-size' && _videoQuality) {
                var v = parseInt(e.target.value, 10) || 0;
                var lab = document.getElementById('vq-size-label');
                if (lab) lab.textContent = v ? (v + ' GB') : 'No limit';
            }
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

    function onPageShown(e) {
        if (e && e.detail !== PAGE_ID) return;
        loadServer();
        loadConn();
        load();
        loadKeys();
        loadDownloads();
        wireDownloads();
        loadQuality();
        wireQuality();
        loadSlskd();
        wireSlskd();
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
                         saveDownloads(true), saveQuality(true), saveSlskd(true)])
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
