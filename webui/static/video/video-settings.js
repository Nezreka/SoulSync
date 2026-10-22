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
        'remux-2160p': 'Remux · 4K', 'bluray-2160p': 'BluRay · 4K', 'web-2160p': 'WEB-DL · 4K',
        'webrip-2160p': 'WEBRip · 4K', 'hdtv-2160p': 'HDTV · 4K',
        'remux-1080p': 'Remux · 1080p', 'bluray-1080p': 'BluRay · 1080p', 'web-1080p': 'WEB-DL · 1080p',
        'webrip-1080p': 'WEBRip · 1080p', 'hdtv-1080p': 'HDTV · 1080p',
        'bluray-720p': 'BluRay · 720p', 'web-720p': 'WEB-DL · 720p', 'webrip-720p': 'WEBRip · 720p',
        'hdtv-720p': 'HDTV · 720p',
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
    // what the note under each server's fields says. a half-filled connection
    // (url but no key, or key but no url) gets told what's still missing, so a
    // save that only stored half of it doesn't read as done.
    function connNote(server, hasUrl, hasSecret, inherited) {
        var name = server === 'plex' ? 'Plex' : 'Jellyfin';
        var secret = server === 'plex' ? 'token' : 'API key';
        if (!hasUrl && !hasSecret) return 'Not connected — add a server URL and ' + secret + '.';
        if (!hasSecret) return 'Add the ' + secret + ' to finish this connection.';
        if (!hasUrl) return 'Add the server URL to finish this connection.';
        if (inherited) return 'Inherited from your Music ' + name + ' connection — edit to use a different server for video.';
        return 'Custom video connection.';
    }
    // never overwrite the field the user is typing in. a save fires on change, so
    // this refresh can land while they're already filling in the next field.
    function setConnValue(name, value) {
        var el = connEl(name);
        if (el && el !== document.activeElement) el.value = value;
    }
    function loadConn() {
        return fetch(CONN_URL, { headers: { 'Accept': 'application/json' } })
            .then(function (r) { return r.ok ? r.json() : null; })
            .then(function (d) {
                if (!d) return;
                var p = d.plex || {}, j = d.jellyfin || {};
                setConnValue('plex-url', p.base_url || '');
                setConnValue('plex-token', p.has_token ? p.token : '');
                setConnValue('jellyfin-url', j.base_url || '');
                setConnValue('jellyfin-key', j.has_key ? j.api_key : '');
                note('plex', connNote('plex', !!p.base_url, !!p.has_token, !!p.inherited));
                note('jellyfin', connNote('jellyfin', !!j.base_url, !!j.has_key, !!j.inherited));
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
        }).then(function () {
            if (!silent) toast('Connection saved', 'success');
            return loadConn();       // refresh the notes once the save has landed
        }).catch(function () { if (!silent) toast('Could not save connection', 'error'); });
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
        var n = document.querySelector('[data-video-lib-save-status]');
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

    // The TV libraries other than the main pick, each with a Scan and an Anime
    // tick. ``tvMore`` / ``anime`` are the saved lists of titles.
    var _tvAll = [], _tvMore = [], _anime = [];

    function fillTvMore() {
        var row = document.querySelector('[data-video-lib-tv-more-row]');
        var box = document.querySelector('[data-video-lib-tv-more]');
        if (!row || !box) return;
        var t = document.querySelector('[data-video-lib-select="tv"]');
        var main = t ? t.value : '';
        box.textContent = '';
        var shown = 0;
        for (var i = 0; i < _tvAll.length; i++) {
            var title = _tvAll[i].title;
            if (!title || title === main) continue;
            shown++;
            var item = document.createElement('div');
            item.className = 'video-library-tv-more-item';
            var name = document.createElement('span');
            name.textContent = title;
            item.appendChild(name);
            [['scan', 'Scan', _tvMore], ['anime', 'Anime', _anime]].forEach(function (k) {
                var lab = document.createElement('label');
                lab.className = 'checkbox-label';
                var box2 = document.createElement('input');
                box2.type = 'checkbox';
                box2.setAttribute('data-video-lib-tv-' + k[0], title);
                box2.checked = k[2].indexOf(title) !== -1;
                box2.addEventListener('change', onTvMoreChange);
                lab.appendChild(box2);
                lab.appendChild(document.createTextNode(' ' + k[1]));
                item.appendChild(lab);
            });
            box.appendChild(item);
        }
        row.hidden = shown === 0;
    }

    function ticked(kind) {
        var out = [];
        var boxes = document.querySelectorAll('[data-video-lib-tv-' + kind + ']');
        for (var i = 0; i < boxes.length; i++) {
            if (boxes[i].checked) out.push(boxes[i].getAttribute('data-video-lib-tv-' + kind));
        }
        return out;
    }

    function onTvMoreChange(e) {
        // a library ticked Anime is one you want scanned: tick Scan with it
        var b = e && e.target;
        if (b && b.checked && b.hasAttribute('data-video-lib-tv-anime')) {
            var title = b.getAttribute('data-video-lib-tv-anime');
            var scans = document.querySelectorAll('[data-video-lib-tv-scan]');
            for (var i = 0; i < scans.length; i++) {
                if (scans[i].getAttribute('data-video-lib-tv-scan') === title) scans[i].checked = true;
            }
        }
        save();
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
                _tvAll = d.tv || [];
                _tvMore = sel.tv_more || [];
                _anime = sel.anime || [];
                fillTvMore();
                if (!(d.movies || []).length && !(d.tv || []).length) {
                    status('No movie or TV libraries found. Check the server connection and that the user above can see them.');
                } else if (!sel.movies && !sel.tv) {
                    status('Pick a library or the scan has nothing to read.');
                } else {
                    status('');
                }
            })
            .catch(function () { status('Could not load libraries'); });
    }

    function save(silent) {
        var m = document.querySelector('[data-video-lib-select="movies"]');
        var t = document.querySelector('[data-video-lib-select="tv"]');
        var body = { movies: m ? m.value : '', tv: t ? t.value : '' };
        if (document.querySelector('[data-video-lib-tv-more]')) {
            // the main TV pick is always scanned; keep an Anime mark on it if it had one
            var main = body.tv;
            body.tv_more = ticked('scan');
            body.anime = ticked('anime').concat(main && _anime.indexOf(main) !== -1 ? [main] : []);
            _tvMore = body.tv_more;
            _anime = body.anime;
        }
        status('Saving…');
        return fetch(URL, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json', 'Accept': 'application/json' },
            body: JSON.stringify(body)
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
                var sdl = document.getElementById('subdl-api-key');
                if (sdl && d.subdl_api_key != null) sdl.value = d.subdl_api_key;
                var sso = document.getElementById('subsource-api-key');
                if (sso && d.subsource_api_key != null) sso.value = d.subsource_api_key;
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
    // per indexer seed rules, keyed by prowlarr indexer id. blank field = inherit
    // the global goal, 0 = exempt this tracker from it. keep those different.
    var _videoSeedRules = {};
    var _videoSeedIndexers = null;      // null = not loaded yet

    function _seedRuleVal(id, field) {
        var r = _videoSeedRules[String(id)] || {};
        return (r[field] === undefined || r[field] === null) ? '' : r[field];
    }

    function seedIndexerRow(idx) {
        var e = function (v) {
            return String(v == null ? '' : v).replace(/[&<>"']/g, function (c) {
                return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
            });
        };
        var num = function (field, ph, step) {
            return '<input type="number" min="0" step="' + step + '" placeholder="' + ph + '" ' +
                'data-vseed-idx="' + e(idx.id) + '" data-vseed-field="' + field + '" ' +
                'value="' + e(_seedRuleVal(idx.id, field)) + '" style="max-width:78px;">';
        };
        return '<div class="ind-indexer-card" style="gap:8px; flex-wrap:wrap;">' +
            '<span class="ind-indexer-card-id">#' + e(idx.id) + '</span>' +
            '<span class="ind-indexer-card-name">' + e(idx.name) + '</span>' +
            (idx.privacy ? '<span class="ind-indexer-card-privacy">' + e(idx.privacy) + '</span>' : '') +
            '<span style="margin-left:auto; display:flex; gap:6px; align-items:center;">' +
            '<span class="settings-hint">Ratio</span>' + num('ratio', 'inherit', '0.1') +
            '<span class="settings-hint">Hours</span>' + num('hours', 'inherit', '1') +
            '<span class="settings-hint">Pack hrs</span>' + num('pack_hours', 'same', '1') +
            '</span></div>';
    }

    function renderSeedIndexers() {
        var host = document.getElementById('video-seed-idx-rows');
        if (!host) return;
        if (_videoSeedIndexers === null) return;
        if (!_videoSeedIndexers.length) {
            host.innerHTML = '<em class="settings-hint">No torrent indexers in Prowlarr. ' +
                'Usenet never seeds, so only torrent indexers show here.</em>';
            return;
        }
        host.innerHTML = _videoSeedIndexers.map(seedIndexerRow).join('');
    }

    function loadSeedIndexers() {
        var host = document.getElementById('video-seed-idx-rows');
        if (host) host.innerHTML = '<em class="settings-hint">Loading…</em>';
        fetch('/api/prowlarr/indexers', { headers: { 'Accept': 'application/json' } })
            .then(function (r) { return r.ok ? r.json() : null; })
            .then(function (d) {
                if (!d || !d.success) {
                    if (host) host.innerHTML = '<em class="settings-hint">Prowlarr isn\'t configured yet ' +
                        '(Settings → Indexers &amp; Downloaders).</em>';
                    return;
                }
                // usenet has no seeding, so don't offer rules that can never fire
                _videoSeedIndexers = (d.indexers || []).filter(function (i) { return i.protocol !== 'usenet'; });
                renderSeedIndexers();
            })
            .catch(function () {
                if (host) host.innerHTML = '<em class="settings-hint">Couldn\'t reach Prowlarr.</em>';
            });
    }

    function wireSeedIndexers() {
        var host = document.getElementById('video-seed-idx-rows');
        if (!host || host._vseedWired) return;
        host._vseedWired = true;
        host.addEventListener('change', function (e) {
            var id = e.target && e.target.getAttribute('data-vseed-idx');
            if (!id) return;
            var field = e.target.getAttribute('data-vseed-field');
            var raw = String(e.target.value || '').trim();
            var rule = _videoSeedRules[id] || (_videoSeedRules[id] = {});
            if (raw === '') { delete rule[field]; }          // blank = inherit
            else { rule[field] = field === 'ratio' ? parseFloat(raw) : parseInt(raw, 10); }
            if (!Object.keys(rule).length) delete _videoSeedRules[id];
            saveDownloads(true);
        });
        var btn = document.getElementById('video-seed-idx-refresh');
        if (btn && !btn._vseedWired) { btn._vseedWired = true; btn.addEventListener('click', loadSeedIndexers); }
    }

    function addVideoPathRow(host, value) {
        var row = document.createElement('div');
        row.style.cssText = 'display:flex;gap:8px;align-items:center;margin-bottom:8px';
        var input = document.createElement('input');
        input.type = 'text';
        input.value = value || '';
        input.placeholder = host.dataset.videoExtraKind === 'movies' ? '/media/movies2' : '/media/tv2';
        input.setAttribute('aria-label', host.dataset.videoExtraKind === 'movies' ? 'Additional movie library path' : 'Additional TV library path');
        input.style.cssText = 'flex:1;min-width:0';
        input.addEventListener('change', function () { saveDownloads(true); });
        var remove = document.createElement('button');
        remove.type = 'button';
        remove.className = 'test-button';
        remove.textContent = 'Remove';
        remove.setAttribute('aria-label', 'Remove library path');
        remove.addEventListener('click', function () { row.remove(); saveDownloads(true); });
        if (host.closest('[aria-busy="true"]')) {
            [input, remove].forEach(function (control) {
                control.setAttribute('data-library-disabled', 'false');
                control.disabled = true;
            });
        }
        row.appendChild(input);
        row.appendChild(remove);
        host.appendChild(row);
        return input;
    }

    function loadVideoPaths(kind, paths) {
        var host = document.getElementById('video-' + kind + '-additional-paths');
        if (!host) return;
        host.replaceChildren();
        (Array.isArray(paths) ? paths : []).forEach(function (path) { addVideoPathRow(host, path); });
        host.dataset.loaded = 'true';
    }

    function collectVideoPaths(kind) {
        var host = document.getElementById('video-' + kind + '-additional-paths');
        if (!host || host.dataset.loaded !== 'true') return undefined;
        return Array.from(host.querySelectorAll('input')).map(function (input) { return input.value.trim(); }).filter(Boolean);
    }

    function loadDownloads() {
        return fetch(DOWNLOADS_URL, { headers: { 'Accept': 'application/json' } })
            .then(function (r) { return r.ok ? r.json() : null; })
            .then(function (d) {
                if (!d) return false;
                var setP = function (id, v) { var el = document.getElementById(id); if (el && v != null) el.value = v; };
                setP('video-download-path', d.download_path);
                setP('video-movies-path', d.movies_path);
                setP('video-tv-path', d.tv_path);
                setP('video-anime-path', d.anime_path);
                loadVideoPaths('movies', d.movies_additional_paths);
                loadVideoPaths('tv', d.tv_additional_paths);
                setP('video-youtube-path', d.youtube_path);
                _videoMode = d.download_mode || 'soulseek';
                _videoHybrid = (d.hybrid_order && d.hybrid_order.length) ? d.hybrid_order : ['soulseek'];
                var ms = document.getElementById('video-download-mode');
                if (ms) ms.value = _videoMode;
                setP('video-seed-ratio', d.seed_ratio_goal != null ? d.seed_ratio_goal : 0);
                setP('video-seed-hours', d.seed_time_goal_hours != null ? d.seed_time_goal_hours : 0);
                var srd = document.getElementById('video-seed-remove-data');
                if (srd) srd.checked = d.seed_remove_data !== false;
                var smd = document.getElementById('video-seed-mode');
                if (smd) smd.value = d.seed_mode || 'soulsync';
                _videoSeedRules = d.seed_overrides || {};
                renderSeedIndexers();
                wireSeedIndexers();
                renderVideoHybrid();
                updateVideoSourceUI();
                return true;
            })
            .catch(function () { return false; });
    }

    function saveDownloads(silent) {
        var val = function (id) { var el = document.getElementById(id); return el ? el.value : ''; };
        return fetch(DOWNLOADS_URL, {
            method: 'POST', headers: { 'Content-Type': 'application/json', 'Accept': 'application/json' },
            body: JSON.stringify({
                download_path: val('video-download-path'),
                movies_path: val('video-movies-path'),
                tv_path: val('video-tv-path'),
                anime_path: val('video-anime-path'),
                movies_additional_paths: collectVideoPaths('movies'),
                tv_additional_paths: collectVideoPaths('tv'),
                youtube_path: val('video-youtube-path'),
                // download_mode / hybrid_order are NOT sent from here any more.
                // The shared download-chain widget owns them and writes them
                // straight to this endpoint. _videoMode/_videoHybrid only refresh
                // when this file loads, so sending them meant that changing a
                // folder path or a seed ratio re-posted a stale chain and silently
                // undid whatever the widget had just saved. The endpoint only
                // persists keys that are present, so leaving them out is safe.
                seed_ratio_goal: parseFloat(val('video-seed-ratio')) || 0,
                seed_time_goal_hours: parseInt(val('video-seed-hours'), 10) || 0,
                seed_remove_data: !!(document.getElementById('video-seed-remove-data') || {}).checked,
                seed_mode: val('video-seed-mode') || 'soulsync',
                seed_overrides: _videoSeedRules,
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

    // nothing calls this any more, on purpose. the Sources tab's Soulseek card
    // is the only editor now, and these hidden fields hold a stale copy. left
    // here so the shape of the retired form stays readable, do not re-wire it.
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
            // retired form: the fields are hidden, so a change event can only come
            // from code, and any write from here races the music panel's own save
            if (el && !el._vsWired) { el._vsWired = true; }
        });
    }

    function wireDownloads() {
        document.querySelectorAll('[data-video-add-path]').forEach(function (button) {
            if (button._vdWired) return;
            button._vdWired = true;
            button.addEventListener('click', function () {
                var host = document.getElementById('video-' + button.dataset.videoAddPath + '-additional-paths');
                if (host && host.dataset.loaded === 'true') addVideoPathRow(host, '').focus();
            });
        });
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
        ['video-download-path', 'video-movies-path', 'video-tv-path', 'video-anime-path', 'video-youtube-path'].forEach(function (id) {
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

        // Swarm health floor. Absent from a profile stored before the gate existed
        // means "use the default", not "no floor" — same rule the normalizer applies,
        // so the slider never shows 0 for an install that will actually enforce 1.
        var sd = document.getElementById('vq-min-seeders');
        var seeders = (p.min_seeders === undefined || p.min_seeders === null) ? 1 : p.min_seeders;
        if (sd) sd.value = seeders;
        _vqSeedersLabel(seeders);
    }

    function _vqSeedersLabel(n) {
        var el = document.getElementById('vq-seeders-label');
        if (!el) return;
        n = parseInt(n, 10) || 0;
        el.textContent = n <= 0 ? 'No minimum — grab dead swarms too'
            : ('At least ' + n + ' seeder' + (n === 1 ? '' : 's'));
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
            if (e.target.id === 'vq-min-seeders') { _videoQuality.min_seeders = parseInt(e.target.value, 10) || 0; saveQuality(true); return; }
        });
        card.addEventListener('input', function (e) {
            if (e.target.id === 'vq-movie-size') { _vqSizeLabel('vq-movie-label', parseInt(e.target.value, 10) || 0); return; }
            if (e.target.id === 'vq-episode-size') { _vqSizeLabel('vq-episode-label', parseInt(e.target.value, 10) || 0); return; }
            if (e.target.id === 'vq-min-seeders') { _vqSeedersLabel(e.target.value); return; }
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
                subdl_api_key: (document.getElementById('subdl-api-key') || {}).value || '',
                subsource_api_key: (document.getElementById('subsource-api-key') || {}).value || '',
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
    var TEST_NAMES = { tmdb: 'TMDB', tvdb: 'TVDB', omdb: 'OMDb', fanart: 'fanart.tv',
        opensubtitles: 'OpenSubtitles', subdl: 'SubDL', subsource: 'SubSource', trakt: 'Trakt',
        mdblist: 'MDBList', tvmaze: 'TVmaze', anilist: 'AniList', wikidata: 'Wikidata',
        ryd: 'Return YouTube Dislike', sponsorblock: 'SponsorBlock', dearrow: 'DeArrow' };
    function _testOne(svc) {
        return fetch('/api/video/enrichment/' + svc + '/test',
            { method: 'POST', headers: { 'Accept': 'application/json' } })
            .then(function (r) { return r.json(); })
            .catch(function () { return { success: false, error: 'request failed' }; });
    }
    // the keyless cards hold several services behind one button ("tvmaze,anilist,wikidata").
    // only the ones switched on get tested, a service you turned off isn't broken.
    function testConnection(spec) {
        var svcs = String(spec || '').split(',').filter(Boolean);
        if (svcs.length > 1) {
            svcs = svcs.filter(function (s) {
                var box = document.getElementById('video-' + s + '-enabled');
                return !box || box.checked;
            });
            if (!svcs.length) { toast('Nothing to test, all of these are switched off', 'info'); return; }
        }
        var names = svcs.map(function (s) { return TEST_NAMES[s] || s.toUpperCase(); });
        toast('Testing ' + names.join(', ') + '…', 'info');
        saveKeys(true).then(function () {
            return Promise.all(svcs.map(_testOne));
        }).then(function (results) {
            if (results.length === 1) {
                var res = results[0];
                if (res && res.success) toast(res.message || (names[0] + ' connection successful'), 'success');
                else toast(names[0] + ' connection failed: ' + ((res && res.error) || 'unknown'), 'error');
                return;
            }
            var bad = [];
            results.forEach(function (res, i) {
                if (!(res && res.success)) bad.push(names[i] + ': ' + ((res && res.error) || 'unknown'));
            });
            if (!bad.length) toast(names.join(', ') + ' all reachable', 'success');
            else toast('Failed: ' + bad.join(' · '), 'error');
        }).catch(function () { toast('Failed to test ' + names.join(', '), 'error'); });
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
    // The backend serves `subtitle_provider_order` as a JSON list of provider
    // ids (parsed by core.video.subtitles.parse_provider_order); the fallback
    // below covers only garbage/missing values, defaulting to OpenSubtitles.
    var DEFAULT_SUBTITLE_PROVIDER_ORDER = ['opensubtitles'];
    function subtitleProviderOrder() {
        var raw = _videoOrg && _videoOrg.subtitle_provider_order;
        if (typeof raw === 'string') {
            try { raw = JSON.parse(raw); } catch (e) { raw = null; }
        }
        var list = [];
        if (Array.isArray(raw)) {
            raw.forEach(function (p) {
                p = String(p == null ? '' : p);
                if (p && list.indexOf(p) === -1) list.push(p);
            });
        }
        return list.length ? list : DEFAULT_SUBTITLE_PROVIDER_ORDER.slice();
    }
    // Subtitle provider chain: editable, mirroring the download-chain widget's
    // look and behaviour (dlchain-* CSS): pool tiles + ordered steps (rank,
    // role, grip, arrows, remove) + the dashed slot. Network providers are
    // reorderable; Whisper is pinned last by the backend (_iter_configured)
    // and renders as a locked step when it is enabled, downloaded and the
    // engine is present.
    var SUBCHAIN_NETWORK_IDS = ['opensubtitles', 'subdl', 'subsource', 'embedded'];  // hardcoded known providers
    var SUBCHAIN_BACKGROUND_IDS = ['whisper'];     // pinned last, never reorderable
    var SUBCHAIN_META = {
        opensubtitles: { name: 'OpenSubtitles', emoji: '\u{1F4AC}' },
        subdl: { name: 'SubDL', emoji: '\u{1F4E5}' },
        subsource: { name: 'SubSource', emoji: '\u{1F310}' },
        embedded: { name: 'Embedded', emoji: '\u{1F4BD}' },
        whisper: { name: 'Whisper (local)', emoji: '\u{1F399}' }
    };
    var _subchainOrder = [];   // network provider ids, top-first
    var _subchainDragging = null;
    function subchainMeta(id) {
        // Unknown ids show as themselves and never break the widget — a
        // parallel branch may add providers the hardcoded list predates.
        return SUBCHAIN_META[id] || { name: String(id), emoji: '\u{1F4AC}' };
    }
    function _subchainWhisperActive() {
        var st = _whisperStatus;
        return !!(st && st.enabled && st.downloaded && st.engine);
    }
    // The stored order minus background-only providers. Whisper is managed
    // by its own toggle below and pinned last by the backend even when the
    // stored order predates it, so it never lives in the reorderable chain.
    function subchainLoad() {
        _subchainOrder = subtitleProviderOrder().filter(function (id) {
            return SUBCHAIN_BACKGROUND_IDS.indexOf(id) === -1;
        });
        renderSubchain();
    }
    function _subchainTile(id) {
        var m = subchainMeta(id);
        var name = escA(m.name);
        return '<button type="button" class="dlchain-tile" draggable="true" data-src="' + id + '" '
            + 'data-action="add" title="Add ' + name + ' to the chain">'
            + '<span class="dlchain-tile-art"><span class="emoji-icon">' + m.emoji + '</span></span>'
            + '<span class="dlchain-tile-name">' + name + '</span></button>';
    }
    function _subchainStep(id, position, total, locked) {
        var m = subchainMeta(id);
        var name = escA(m.name);
        var role = locked ? 'Last resort' : (position === 1 ? 'Tried first' : 'Fallback ' + (position - 1));
        var html = '<div class="dlchain-step" data-src="' + id + '" '
            + 'title="' + name + ' - ' + role + '" aria-label="' + name + ', ' + role + '"'
            + (locked ? '' : ' draggable="true"') + '>'
            + (locked ? '' : '<span class="dlchain-grip" aria-hidden="true">&#x283F;</span>')
            + '<span class="dlchain-step-rank">' + position + '</span>'
            + '<span class="dlchain-step-art"><span class="emoji-icon">' + m.emoji + '</span></span>'
            + '<span class="dlchain-step-text">'
            + '<span class="dlchain-step-name">' + name + '</span>'
            + '<span class="dlchain-step-role">' + role + '</span></span>';
        if (!locked) {
            html += '<span class="dlchain-step-move">'
                + '<button type="button" class="dlchain-move" title="Move ' + name + ' up" aria-label="Move ' + name + ' up"'
                + (position === 1 ? ' disabled' : '') + ' data-action="up">&#9650;</button>'
                + '<button type="button" class="dlchain-move" title="Move ' + name + ' down" aria-label="Move ' + name + ' down"'
                + (position === total ? ' disabled' : '') + ' data-action="down">&#9660;</button>'
                + '</span>'
                + '<button type="button" class="dlchain-btn" title="Remove ' + name + ' from the chain" '
                + 'aria-label="Remove ' + name + ' from the chain" data-action="remove">&times;</button>';
        }
        return html + '</div>';
    }
    function renderSubchain() {
        var pool = document.getElementById('subchain-pool');
        var list = document.getElementById('subchain-list');
        if (!pool || !list) return;
        var order = _subchainOrder.slice();
        var available = SUBCHAIN_NETWORK_IDS.filter(function (id) { return order.indexOf(id) === -1; });
        pool.innerHTML = available.length
            ? available.map(_subchainTile).join('')
            : '<div class="dlchain-empty">Every provider is in the chain.</div>';
        var steps = order.map(function (id, i) { return _subchainStep(id, i + 1, order.length, false); });
        if (_subchainWhisperActive()) steps.push(_subchainStep('whisper', order.length + 1, order.length + 1, true));
        var lastName = order.length ? escA(subchainMeta(order[order.length - 1]).name) : '';
        var slotTitle = order.length
            ? 'Drag a provider here \u2014 tried after ' + lastName
            : 'Drag a provider here \u2014 this one is tried first';
        var slotHint = order.length
            ? 'It only runs if everything above it came up empty.'
            : 'Subtitles need at least one provider.';
        list.innerHTML = steps.join('<div class="dlchain-connector"></div>')
            + (steps.length ? '<div class="dlchain-connector"></div>' : '')
            + '<div class="dlchain-slot' + (order.length ? '' : ' first') + '" id="subchain-slot">'
            + '<span class="dlchain-slot-title">' + slotTitle + '</span>'
            + '<span class="dlchain-slot-hint">' + slotHint + '</span>'
            + '</div>';
        var hint = document.getElementById('subchain-mode-hint');
        if (hint) {
            hint.textContent = order.length > 1 ? 'chain \u2014 ' + order.length + ' providers'
                : order.length === 1 ? 'single provider' : 'nothing selected';
        }
        _subchainWireDrag();
        _subchainWireClicks();
    }
    function _subchainCommit() {
        renderSubchain();
        // Video-side settings save immediately (no page-wide save button).
        saveOrganization(false);
    }
    function subchainAdd(id) {
        if (_subchainOrder.indexOf(id) === -1) { _subchainOrder.push(id); _subchainCommit(); }
    }
    function subchainMove(id, delta) {
        var i = _subchainOrder.indexOf(id);
        var j = i + delta;
        if (i < 0 || j < 0 || j >= _subchainOrder.length) return;
        var t = _subchainOrder[i]; _subchainOrder[i] = _subchainOrder[j]; _subchainOrder[j] = t;
        _subchainCommit();
    }
    function subchainRemove(id) {
        // An empty chain means no subtitles can ever be found, with nothing
        // on screen saying why — same guard as the download chain.
        if (_subchainOrder.length <= 1) {
            if (typeof toast === 'function') toast('Keep at least one provider \u2014 subtitles need somewhere to come from', 'info');
            return;
        }
        _subchainOrder = _subchainOrder.filter(function (x) { return x !== id; });
        _subchainCommit();
    }
    // Clicks are delegated from the widget container — this module stays
    // isolated (no globals exported), so the buttons carry data-action and
    // the provider id rides on the closest [data-src] ancestor. Attached
    // once: renderSubchain only rewrites the pool/list innerHTML, never the
    // container itself.
    var _subchainClicksWired = false;
    function _subchainWireClicks() {
        if (_subchainClicksWired) return;
        var widget = document.getElementById('subchain-widget');
        if (!widget) return;
        _subchainClicksWired = true;
        widget.addEventListener('click', function (e) {
            var btn = e.target && e.target.closest ? e.target.closest('[data-action]') : null;
            if (!btn || !widget.contains(btn)) return;
            var holder = btn.closest('[data-src]');
            var id = holder ? holder.dataset.src : null;
            if (!id) return;
            var action = btn.getAttribute('data-action');
            if (action === 'add') subchainAdd(id);
            else if (action === 'up') subchainMove(id, -1);
            else if (action === 'down') subchainMove(id, 1);
            else if (action === 'remove') subchainRemove(id);
        });
    }
    var _subchainDragWired = false;
    function _subchainWireDrag() {
        var pool = document.getElementById('subchain-pool');
        var list = document.getElementById('subchain-list');
        if (!pool || !list) return;
        // Pool tiles and chain steps are both draggable and both carry
        // data-src. The pinned Whisper step has no draggable attribute and
        // is skipped here and as a drop target.
        document.querySelectorAll('#subchain-widget [data-src]').forEach(function (item) {
            if (!item.hasAttribute('draggable')) return;
            item.addEventListener('dragstart', function (e) {
                _subchainDragging = item.dataset.src;
                e.dataTransfer.effectAllowed = 'move';
                e.dataTransfer.setData('text/plain', item.dataset.src);
                item.classList.add('dragging');
            });
            item.addEventListener('dragend', function () {
                _subchainDragging = null;
                item.classList.remove('dragging');
                document.querySelectorAll('#subchain-widget .drag-over').forEach(function (el) {
                    el.classList.remove('drag-over');
                });
            });
        });
        // Dropping ON a step inserts before it — that is how you reorder.
        list.querySelectorAll('.dlchain-step[draggable]').forEach(function (step) {
            step.addEventListener('dragover', function (e) {
                e.preventDefault(); e.dataTransfer.dropEffect = 'move';
                step.classList.add('drag-over');
            });
            step.addEventListener('dragleave', function () { step.classList.remove('drag-over'); });
            step.addEventListener('drop', function (e) {
                e.preventDefault(); e.stopPropagation();
                step.classList.remove('drag-over');
                var src = e.dataTransfer.getData('text/plain') || _subchainDragging;
                var target = step.dataset.src;
                if (!src || src === target) return;
                _subchainOrder = _subchainOrder.filter(function (x) { return x !== src; });
                var at = _subchainOrder.indexOf(target);
                _subchainOrder.splice(at < 0 ? _subchainOrder.length : at, 0, src);
                _subchainCommit();
            });
        });
        // The slot at the end: always append.
        var slot = document.getElementById('subchain-slot');
        if (slot) {
            slot.addEventListener('dragover', function (e) {
                e.preventDefault(); e.dataTransfer.dropEffect = 'move';
                slot.classList.add('drag-over');
            });
            slot.addEventListener('dragleave', function () { slot.classList.remove('drag-over'); });
            slot.addEventListener('drop', function (e) {
                e.preventDefault(); e.stopPropagation();
                slot.classList.remove('drag-over');
                var src = e.dataTransfer.getData('text/plain') || _subchainDragging;
                if (!src) return;
                _subchainOrder = _subchainOrder.filter(function (x) { return x !== src; });
                _subchainOrder.push(src);
                _subchainCommit();
            });
        }
        // Container-level handlers attach ONCE: pool/list are persistent
        // nodes (only their innerHTML is rewritten per render), so wiring
        // them on every render would stack duplicate drop handlers.
        if (_subchainDragWired) return;
        _subchainDragWired = true;
        // Anywhere else in the chain column appends too, so a slightly-off
        // drop still does the obvious thing instead of nothing.
        list.addEventListener('dragover', function (e) { e.preventDefault(); list.classList.add('drag-over'); });
        list.addEventListener('dragleave', function () { list.classList.remove('drag-over'); });
        list.addEventListener('drop', function (e) {
            e.preventDefault();
            list.classList.remove('drag-over');
            var src = e.dataTransfer.getData('text/plain') || _subchainDragging;
            if (!src || _subchainOrder.indexOf(src) !== -1) return;
            _subchainOrder.push(src);
            _subchainCommit();
        });
        // Dragging a step back to the pool removes it.
        pool.addEventListener('dragover', function (e) { e.preventDefault(); pool.classList.add('drag-over'); });
        pool.addEventListener('dragleave', function () { pool.classList.remove('drag-over'); });
        pool.addEventListener('drop', function (e) {
            e.preventDefault();
            pool.classList.remove('drag-over');
            var src = e.dataTransfer.getData('text/plain') || _subchainDragging;
            if (src && _subchainOrder.indexOf(src) !== -1) subchainRemove(src);
        });
    }
    // Light validation for the languages field: 2-3 letter codes, same
    // separators the backend's parse_langs accepts. Invalid input shows a
    // hint and reverts to the last saved value instead of saving.
    var _voLangsLastGood = 'en';
    function validateSubLangs() {
        var el = document.getElementById('vo-sub-langs');
        var err = document.getElementById('vo-sub-langs-error');
        if (!el) return true;
        var toks = String(el.value || '').split(/[,\s;]+/).filter(function (t) { return t; });
        var bad = null;
        for (var i = 0; i < toks.length; i++) {
            if (!/^[A-Za-z]{2,3}$/.test(toks[i])) { bad = toks[i]; break; }
        }
        if (bad !== null) {
            if (err) {
                err.textContent = 'Not a language code: "' + bad + '". Use 2-3 letters per code (e.g. en, es).';
                err.hidden = false;
            }
            return false;
        }
        if (err) { err.hidden = true; err.textContent = ''; }
        _voLangsLastGood = el.value;
        return true;
    }
    // ── Whisper local transcription (Phase 4) ────────────────────────────
    var WHISPER_URL = '/api/video/subtitles/whisper';
    var _whisperStatus = null;   // last status payload
    var _whisperPoll = null;     // polling timer while a download runs
    // Echoed download-state fields: the org blob is fully replaced on save,
    // so these must round-trip (same echo pattern as subtitle_provider_order)
    // or a settings save would wipe the "downloaded" state.
    var _whisperEcho = { whisper_model_downloaded: false, whisper_model_path: '',
        whisper_model_size_bytes: 0, whisper_model_downloaded_at: '' };
    function _whisperFmtBytes(n) {
        n = Number(n) || 0;
        if (n >= 1e9) return (n / 1e9).toFixed(1) + ' GB';
        if (n >= 1e6) return Math.round(n / 1e6) + ' MB';
        if (n >= 1e3) return Math.round(n / 1e3) + ' KB';
        return n + ' B';
    }
    function _whisperStopPoll() {
        if (_whisperPoll) { clearInterval(_whisperPoll); _whisperPoll = null; }
    }
    function _whisperStartPoll() {
        if (_whisperPoll) return;
        _whisperPoll = setInterval(function () { loadWhisperStatus(); }, 2000);
    }
    function loadWhisperStatus() {
        return fetch(WHISPER_URL + '/status', { headers: { 'Accept': 'application/json' } })
            .then(function (r) { return r.ok ? r.json() : null; })
            .then(function (d) {
                if (d && !d.error) {
                    _whisperStatus = d;
                    renderWhisperStatus();
                    renderSubchain();
                }
                return !!d;
            })
            .catch(function () { return false; });
    }
    function renderWhisperStatus() {
        var st = _whisperStatus;
        var picker = document.getElementById('vo-whisper-model');
        var btn = document.getElementById('vo-whisper-dl');
        var toggle = document.getElementById('vo-whisper-enabled');
        var statusEl = document.getElementById('vo-whisper-status');
        var errEl = document.getElementById('vo-whisper-error');
        var prog = document.getElementById('vo-whisper-progress');
        var bar = document.getElementById('vo-whisper-progress-bar');
        if (!st || !picker || !btn) return;
        // Keep the echoed state fresh (a download may have finished in another
        // tab, or just now via the poller).
        _whisperEcho.whisper_model_downloaded = !!st.downloaded;
        _whisperEcho.whisper_model_size_bytes = st.size_bytes || 0;
        _whisperEcho.whisper_model_downloaded_at = st.downloaded_at || '';
        // Picker options carry the real snapshot sizes from the backend.
        if (!picker._whisperSized && st.models) {
            Array.prototype.forEach.call(picker.options, function (opt) {
                var meta = st.models[opt.value];
                if (meta) opt.textContent = meta.label + ' — ' + meta.blurb;
            });
            picker._whisperSized = true;
        }
        var dl = st.download || {};
        var downloading = dl.state === 'downloading';
        if (errEl) {
            if (dl.state === 'error' && dl.error) {
                errEl.textContent = 'Download failed: ' + dl.error;
                errEl.hidden = false;
            } else { errEl.hidden = true; errEl.textContent = ''; }
        }
        if (prog && bar) {
            prog.hidden = !downloading;
            if (downloading && dl.total_bytes > 0) {
                bar.style.width = Math.min(100, Math.round(
                    dl.downloaded_bytes / dl.total_bytes * 100)) + '%';
            } else { bar.style.width = '0%'; }
        }
        var picked = picker.value;
        // "Ready" = the persisted downloaded flag AND the file on disk, for
        // the model the settings currently name. Changing the picker to a
        // model that isn't downloaded flips the button to
        // "Download <new> model" (downloading replaces the old one).
        var pickedReady = !!st.downloaded && picked === st.model;
        if (downloading) {
            var pct = dl.total_bytes > 0
                ? Math.round(dl.downloaded_bytes / dl.total_bytes * 100) : 0;
            btn.textContent = 'Downloading ' + (dl.model || picked) + '… ' + pct + '%';
            btn.disabled = true;
        } else if (pickedReady) {
            btn.textContent = '✓ ' + picked + ' downloaded';
            btn.disabled = true;
        } else {
            btn.textContent = 'Download ' + picked + ' model';
            btn.disabled = false;
        }
        if (statusEl) {
            var bits = [];
            if (st.downloaded) {
                bits.push('Downloaded: ' + st.model + ' (' +
                    _whisperFmtBytes(st.size_bytes) + ')' +
                    (st.downloaded_at ? ' on ' + String(st.downloaded_at).slice(0, 10) : '') + '.');
            } else {
                bits.push('No model downloaded yet.');
            }
            if (st.disk_model && st.disk_model !== picked) {
                bits.push('Note: ' + st.disk_model + ' is on disk — downloading ' +
                    picked + ' replaces it.');
            }
            if (!st.engine) {
                bits.push('Transcription engine missing on the server: ' +
                    (st.engine_hint || 'install faster-whisper') + '.');
            }
            statusEl.textContent = bits.join(' ');
        }
        // The toggle is only meaningful when the picked model is downloaded
        // and the engine is present; it stays disabled (never force-unchecked,
        // so a later settings save doesn't clobber the saved intent).
        if (toggle) {
            var usable = pickedReady && !!st.engine;
            toggle.disabled = !usable;
            toggle.title = usable ? ''
                : 'Download a model first (and install faster-whisper on the server).';
        }
        if (downloading) _whisperStartPoll(); else _whisperStopPoll();
    }
    function startWhisperDownload() {
        var picker = document.getElementById('vo-whisper-model');
        var errEl = document.getElementById('vo-whisper-error');
        var model = picker ? picker.value : 'small';
        if (errEl) { errEl.hidden = true; errEl.textContent = ''; }
        // Persist the picked model first so the status/download state names
        // the model actually being downloaded.
        saveOrganization(true).then(function () {
            return fetch(WHISPER_URL + '/download', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json', 'Accept': 'application/json' },
                body: JSON.stringify({ model: model })
            }).then(function (r) {
                return r.json().then(function (d) { return { ok: r.ok, status: r.status, body: d || {} }; });
            });
        }).then(function (res) {
            if (!res) return;
            if (!res.ok) {
                if (errEl) {
                    errEl.textContent = 'Could not start download: ' + (res.body.error || res.status);
                    errEl.hidden = false;
                }
                return;
            }
            loadWhisperStatus();
        }).catch(function () {
            if (errEl) { errEl.textContent = 'Could not start download (network error).'; errEl.hidden = false; }
        });
    }
    // ── Missing Subtitles job (lives on Tools; this is the signpost) ───────
    var MISSING_SUBS_JOB = 'missing_subtitles';
    function renderMissingSubsJob(job) {
        var el = document.getElementById('vo-sub-job-status');
        if (!el) return;
        if (!job) { el.textContent = ''; return; }
        var parts = [job.enabled ? 'On' : 'Off'];
        if (job.enabled) {
            var h = job.interval_hours || 168;
            parts.push(h % 24 === 0 ? 'every ' + (h / 24) + ' day' + (h === 24 ? '' : 's')
                : 'every ' + h + 'h');
        }
        parts.push(job.settings && job.settings.dry_run ? 'dry run: findings only'
            : 'queues what\u2019s missing');
        var last = job.last_run;
        if (last && last.finished_at) {
            parts.push('last run ' + String(last.finished_at).slice(0, 10) +
                (last.auto_fixed ? ', queued ' + last.auto_fixed : ''));
        } else {
            parts.push('never run');
        }
        if (job.pending_findings_count) parts.push(job.pending_findings_count + ' to review');
        el.textContent = parts.join(' \u00b7 ');
    }
    function loadMissingSubsJob() {
        return fetch('/api/video/repair/jobs', { headers: { 'Accept': 'application/json' } })
            .then(function (r) { return r.ok ? r.json() : null; })
            .then(function (d) {
                var jobs = (d && d.jobs) || [];
                renderMissingSubsJob(jobs.filter(function (j) {
                    return j.job_id === MISSING_SUBS_JOB; })[0] || null);
            })
            .catch(function () { /* status is best-effort */ });
    }
    // Jump to Tools and bring the job's card into view (cards render async).
    function openMissingSubsJob() {
        document.dispatchEvent(new CustomEvent('soulsync:video-navigate',
            { detail: { page: 'video-tools' } }));
        var tries = 0;
        var t = setInterval(function () {
            var card = document.querySelector('.repair-job-card[data-job-id="' + MISSING_SUBS_JOB + '"]');
            if (card || ++tries > 40) {
                clearInterval(t);
                if (!card) return;
                card.scrollIntoView({ behavior: 'smooth', block: 'center' });
                // a brief accent ring so the eye lands on the right card
                card.style.transition = 'box-shadow .35s ease';
                card.style.boxShadow = '0 0 0 2px rgba(var(--accent-rgb), .85)';
                setTimeout(function () { card.style.boxShadow = ''; }, 2200);
            }
        }, 100);
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
        _voLangsLastGood = _videoOrg.subtitle_langs || 'en';
        subchainLoad();
        set('vo-sub-min-score', _videoOrg.subtitle_min_score == null ? 24 : _videoOrg.subtitle_min_score);
        set('vo-sub-daily-quota', _videoOrg.subtitle_daily_quota == null ? 20 : _videoOrg.subtitle_daily_quota);
        set('vo-sub-upgrade-delta', _videoOrg.subtitle_upgrade_min_delta == null ? 15 : _videoOrg.subtitle_upgrade_min_delta);
        set('vo-sub-worker-interval', _videoOrg.subtitle_worker_interval_min == null ? 30 : _videoOrg.subtitle_worker_interval_min);
        set('vo-sub-worker-batch', _videoOrg.subtitle_worker_batch == null ? 10 : _videoOrg.subtitle_worker_batch);
        set('vo-sub-hi-forced', _videoOrg.subtitle_hi_forced || 'off');
        chk('vo-sub-forced', _videoOrg.subtitle_forced);
        chk('vo-sub-use-embedded', _videoOrg.subtitle_use_embedded !== false);
        chk('vo-sub-ignore-pgs', _videoOrg.subtitle_ignore_pgs);
        chk('vo-sub-ignore-vobsub', _videoOrg.subtitle_ignore_vobsub);
        chk('vo-sub-ignore-ass', _videoOrg.subtitle_ignore_ass);
        syncEmbeddedIgnores();
        chk('vo-sub-autosync', _videoOrg.subtitle_autosync);
        set('vo-whisper-model', _videoOrg.whisper_model || 'small');
        chk('vo-whisper-enabled', _videoOrg.whisper_enabled);
        _whisperEcho.whisper_model_downloaded = !!_videoOrg.whisper_model_downloaded;
        _whisperEcho.whisper_model_path = _videoOrg.whisper_model_path || '';
        _whisperEcho.whisper_model_size_bytes = _videoOrg.whisper_model_size_bytes || 0;
        _whisperEcho.whisper_model_downloaded_at = _videoOrg.whisper_model_downloaded_at || '';
        loadWhisperStatus();
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
        return fetch(ORG_URL, { headers: { 'Accept': 'application/json' } })
            .then(function (r) { return r.ok ? r.json() : null; })
            .then(function (d) { if (!d) return false; _videoOrg = d; fillOrg(); return true; })
            .catch(function () { return false; });
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
            // the editable chain; the backend normalises via parse_provider_order
            subtitle_provider_order: _subchainOrder.slice(),
            // advanced tuning knobs — clamped like the backend normalize()
            subtitle_min_score: _voClampNum(val('vo-sub-min-score'), 0, 200, 24),
            subtitle_daily_quota: _voClampInt(val('vo-sub-daily-quota'), 1, 1000, 20),
            subtitle_upgrade_min_delta: _voClampNum(val('vo-sub-upgrade-delta'), 0, 200, 15),
            subtitle_worker_interval_min: _voClampInt(val('vo-sub-worker-interval'), 5, 1440, 30),
            subtitle_worker_batch: _voClampInt(val('vo-sub-worker-batch'), 1, 200, 10),
            subtitle_hi_forced: val('vo-sub-hi-forced') || 'off',
            subtitle_forced: on('vo-sub-forced'),
            subtitle_use_embedded: on('vo-sub-use-embedded'),
            subtitle_ignore_pgs: on('vo-sub-ignore-pgs'),
            subtitle_ignore_vobsub: on('vo-sub-ignore-vobsub'),
            subtitle_ignore_ass: on('vo-sub-ignore-ass'),
            subtitle_autosync: on('vo-sub-autosync'),
            whisper_enabled: on('vo-whisper-enabled'),
            whisper_model: val('vo-whisper-model'),
            // echoed download state — the blob is fully replaced on save, so
            // these must round-trip or a save wipes the "downloaded" state
            whisper_model_downloaded: _whisperEcho.whisper_model_downloaded,
            whisper_model_path: _whisperEcho.whisper_model_path,
            whisper_model_size_bytes: _whisperEcho.whisper_model_size_bytes,
            whisper_model_downloaded_at: _whisperEcho.whisper_model_downloaded_at,
            recycle_deletes: on('vo-recycle'),
            recycle_keep_days: val('vo-recycle-days'),
            recycle_path: val('vo-recycle-path'),
            min_free_disk_gb: val('vo-min-free'),
            youtube_follow_count: val('vo-yt-follow-count'),
            youtube_sponsorblock: val('vo-sponsorblock'),
            youtube_embed_subs: on('vo-yt-subs')
        };
    }
    // the ignore switches only mean something while embedded subs count
    function syncEmbeddedIgnores() {
        var use = document.getElementById('vo-sub-use-embedded');
        var off = !!(use && !use.checked);
        ['vo-sub-ignore-pgs', 'vo-sub-ignore-vobsub', 'vo-sub-ignore-ass'].forEach(function (id) {
            var el = document.getElementById(id);
            if (!el) return;
            el.disabled = off;
            var row = el.closest('label');
            if (row) row.style.opacity = off ? '0.45' : '';
        });
    }
    // Clamp helpers mirroring the backend normalize() bounds, so the UI never
    // posts a value the server would silently clamp to something else.
    function _voClampNum(v, lo, hi, fb) {
        var n = parseFloat(v);
        if (!isFinite(n)) return fb;
        return Math.min(hi, Math.max(lo, n));
    }
    function _voClampInt(v, lo, hi, fb) {
        var n = parseInt(v, 10);
        if (!isFinite(n)) return fb;
        return Math.min(hi, Math.max(lo, n));
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
            'vo-subs-dl', 'vo-recycle', 'vo-recycle-days', 'vo-recycle-path',
            'vo-sponsorblock', 'vo-yt-subs', 'vo-min-free', 'vo-yt-follow-count',
            'vo-sub-min-score', 'vo-sub-daily-quota', 'vo-sub-upgrade-delta',
            'vo-sub-worker-interval', 'vo-sub-worker-batch', 'vo-sub-hi-forced',
            'vo-sub-autosync', 'vo-sub-forced', 'vo-sub-use-embedded', 'vo-sub-ignore-pgs',
            'vo-sub-ignore-vobsub', 'vo-sub-ignore-ass'].forEach(function (id) {
            var el = document.getElementById(id);
            if (el) el.addEventListener('change', function () { saveOrganization(false); });
        });
        var useEmb = document.getElementById('vo-sub-use-embedded');
        if (useEmb) useEmb.addEventListener('change', syncEmbeddedIgnores);
        var langsEl = document.getElementById('vo-sub-langs');
        if (langsEl) langsEl.addEventListener('change', function () {
            if (validateSubLangs()) { saveOrganization(false); }
            else { langsEl.value = _voLangsLastGood; }
        });
        var wModel = document.getElementById('vo-whisper-model');
        if (wModel) wModel.addEventListener('change', function () { renderWhisperStatus(); });
        var wDl = document.getElementById('vo-whisper-dl');
        if (wDl) wDl.addEventListener('click', startWhisperDownload);
        var wTog = document.getElementById('vo-whisper-enabled');
        if (wTog) wTog.addEventListener('change', function () {
            // Refresh the chain afterwards: toggling Whisper pins or unpins
            // its last-resort step.
            saveOrganization(false).then(function () { loadWhisperStatus(); });
        });
        var jobBtn = document.getElementById('vo-sub-open-job');
        if (jobBtn) jobBtn.addEventListener('click', openMissingSubsJob);
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

    // Both media sides share these Library panels. Load once per document,
    // deduplicate requests, and never replace edits on a tab round-trip.
    var _libraryLoaded = false;
    var _libraryLoading = null;
    function loadSharedLibrary() {
        if (_libraryLoaded || _libraryLoading) return _libraryLoading;
        var panels = ['folders-video-panel', 'organization-video-panel'].map(function (id) {
            return document.getElementById(id);
        }).filter(Boolean);
        panels.forEach(function (panel) {
            panel.setAttribute('aria-busy', 'true');
            panel.querySelectorAll('input, select, textarea, button:not(.stg-library-retry)').forEach(function (input) {
                if (!input.hasAttribute('data-library-disabled')) input.setAttribute('data-library-disabled', String(input.disabled));
                input.disabled = true;
            });
            var status = panel.querySelector('.stg-library-load-status');
            if (!status) {
                status = document.createElement('p');
                status.className = 'stg-library-load-status settings-hint';
                status.setAttribute('role', 'status');
                panel.prepend(status);
            }
            status.textContent = 'Loading saved video settings...';
        });
        wireDownloads();
        wireOrganization();
        _libraryLoading = Promise.all([loadDownloads(), loadOrganization()]).then(function (results) {
            _libraryLoaded = results.every(Boolean);
            panels.forEach(function (panel) {
                panel.setAttribute('aria-busy', 'false');
                var status = panel.querySelector('.stg-library-load-status');
                if (_libraryLoaded) {
                    panel.querySelectorAll('[data-library-disabled]').forEach(function (input) {
                        input.disabled = input.getAttribute('data-library-disabled') === 'true';
                        input.removeAttribute('data-library-disabled');
                    });
                    status.remove();
                } else {
                    status.textContent = 'Could not load saved video settings. ';
                    var retry = document.createElement('button');
                    retry.type = 'button'; retry.className = 'stg-library-retry'; retry.textContent = 'Retry';
                    retry.addEventListener('click', loadSharedLibrary);
                    status.append(retry);
                }
            });
            if (typeof window.refreshLibrarySummaries === 'function') window.refreshLibrarySummaries();
        }).finally(function () { _libraryLoading = null; });
        return _libraryLoading;
    }

    function onPageShown(e) {
        if (e && e.detail !== PAGE_ID) return;
        loadServer();
        loadConn();
        load();
        loadKeys();
        loadSharedLibrary();
        // The job status rides the video page lifecycle, NOT the shared
        // library load: that load has an exact fetch contract (and a
        // fail/retry path) the extra status fetch would break.
        loadMissingSubsJob();
        loadImportLists();
        loadNotify();
        loadQuality();
        wireQuality();
        loadYtQuality();
        wireYtQuality();
        loadSlskd();
        wireSlskd();
    }

    function init() {
        // Save the moment a library is picked — same behaviour as the music
        // 'Music Library' selector above (which saves on change, no button).
        // NB: wrap each handler so the DOM Event isn't passed as the function's
        // first arg (which is our `silent` flag — it would suppress the toast).
        var selects = document.querySelectorAll('select[data-video-lib-select]');
        for (var i = 0; i < selects.length; i++) {
            selects[i].addEventListener('change', function () { fillTvMore(); save(); });
        }
        // Enrichment keys save on blur/change (turns the workers on).
        ['tmdb-api-key', 'tvdb-api-key', 'omdb-api-key',
            'fanart-api-key', 'opensubtitles-api-key', 'subdl-api-key',
            'subsource-api-key', 'trakt-api-key',
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
            // saveSlskd is NOT in this list any more. Its form is retired - the
            // shared Sources tab's Soulseek card is the single editor now - but
            // the fields are still in the DOM holding whatever they loaded with.
            // Posting them here overwrote slskd settings that had just been
            // changed from the music side: edit the URL in Sources, autosave
            // stores it, then this button writes the stale copy back over it.
            // The music panel saves through the main settings save.
            Promise.all([saveConn(true), save(true), saveKeys(true), savePrefs(true),
                         saveDownloads(true), saveQuality(true), saveYtQuality(true)])
                .then(function () { toast('Settings saved', 'success'); })
                .catch(function () { toast('Some settings could not be saved', 'error'); });
        }, true);
        document.addEventListener('soulsync:video-page-shown', onPageShown);
        document.addEventListener('soulsync:library-settings-shown', loadSharedLibrary);
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }
})();
