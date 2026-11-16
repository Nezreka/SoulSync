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
    var SLSKD_URL = '/api/video/downloads/slskd';
    // the quality ladder's labels: React (video-quality.tsx)

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

    // Monitor Specials lives in Library > Video Preferences but is stored with
    // the video downloads config (the server saves only the keys sent), so it
    // posts on its own and the Usenet panel's save never re-posts it.
    function saveMonitorSpecials() {
        var el = document.getElementById('video-monitor-specials');
        if (!el) return Promise.resolve();
        return fetch(DOWNLOADS_URL, {
            method: 'POST', headers: { 'Content-Type': 'application/json', 'Accept': 'application/json' },
            body: JSON.stringify({ monitor_specials: !!el.checked })
        }).then(function () { toast(el.checked ? 'Specials monitored' : 'Specials unmonitored', 'success'); })
          .catch(function () { toast('Could not save Monitor Specials', 'error'); });
    }

    // ── the video downloads config: the Folders card's Video panel (the folders,
    //    4K, More libraries, the drive map, the extra folders, the Docker lines)
    //    and the seeding, usenet and retry blocks in the Sources tab are React
    //    (src/features/settings/tabs/library-video-folders.tsx,
    //    video-source-blocks.tsx, video-downloads.ts; SETTINGS_PORT.md video
    //    pass V2a). The retired video source picker and its hybrid list went
    //    with them: nothing showed them. ──

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

    // ── Video Quality, Custom Formats and YouTube Quality: React (src/features/
    //    settings/tabs/video-quality.tsx, SETTINGS_PORT.md video pass V1) ──

    // the escA every row builder here uses: of the two this file declared, this
    // is the one that won (a later declaration replaces an earlier one), kept
    // as it was when the quality code around it went
    function escA(s) { return String(s == null ? '' : s).replace(/&/g, '&amp;').replace(/"/g, '&quot;').replace(/</g, '&lt;'); }

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
        resolution: '1080p', source: 'Bluray', codec: 'HEVC', edition: '', audiolangs: 'EN', sublangs: 'EN+ES',
        tmdbid: '603', imdbid: 'tt0133093' };
    // a title with a colon, so the colon setting shows in the preview
    var _ORG_EP_EG = { series: 'Dexter: Resurrection', season: '01', seasonraw: '1', episode: '01',
        episodetitle: 'A Beating Heart', year: '2025', quality: 'WEBDL-1080p', resolution: '1080p',
        source: 'WEBDL', codec: 'H264', audiolangs: 'EN', sublangs: 'EN', tvdbid: '448003',
        tmdbid: '259909', imdbid: 'tt31846306' };
    var _ORG_YT_EG = { channel: 'Veritasium', title: 'How Electricity Actually Works', year: '2024',
        date: '2024-03-15', month: '03', day: '15', sxe: 's2024e0315', videoid: 'oI_X2cMHNe0' };

    // mirror of core/video/organization.replace_colons
    function _orgColons(v, mode) {
        var s = String(v == null ? '' : v);
        if (s.indexOf(':') === -1) return s;
        if (mode === 'dash') return s.split(':').join('-');
        if (mode === 'space_dash_space') return s.replace(/\s*:\s*/g, ' - ');
        if (mode === 'smart') return s.replace(/\s*:(?:\s+|$)/g, ' - ').split(':').join('-');
        return s;
    }
    function _orgSanitize(v) {
        return String(v == null ? '' : v).replace(/[\\/:*?"<>|\x00-\x1f]/g, '')
            .replace(/\s+/g, ' ').trim().replace(/[ .]+$/, '');
    }
    // Client-side mirror of core/video/organization.render_template + _tidy_component
    // (kept in lockstep) so the preview matches what actually lands on disk.
    function _orgRender(tmpl, vals) {
        var cm = document.getElementById('vo-colon-replacement');
        var mode = cm ? cm.value : 'delete';
        var clean = function (v) { return _orgSanitize(_orgColons(v, mode)); };
        var keys = Object.keys(vals).sort(function (a, b) { return b.length - a.length; });
        var out = String(tmpl || '');
        keys.forEach(function (k) { out = out.split('${' + k + '}').join(clean(vals[k])); });
        keys.forEach(function (k) { out = out.split('$' + k).join(clean(vals[k])); });
        return out.split('/').map(function (p) {
            p = p.replace(/\{\s*(?:[A-Za-z]+-)?\s*\}/g, '');   // '{imdb-}' / '{}' from an empty id
            p = p.replace(/\s+-\s+(?=(\s|$))/g, ' ').replace(/\(\s*\)/g, '').replace(/\[\s*\]/g, '');
            p = p.replace(/\((\d{4})\)(?:\s*\(\d{4}\))+/g, '($1)');
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
    // Jump to Tools on this job: the React tools page opens the job's card
    // (advanced view) and lights it up from ?job=.
    function openMissingSubsJob() {
        document.dispatchEvent(new CustomEvent('soulsync:video-navigate',
            { detail: { page: 'video-tools', job: MISSING_SUBS_JOB } }));
    }
    function fillOrg() {
        if (!_videoOrg) return;
        var set = function (id, v) { var el = document.getElementById(id); if (el) el.value = v; };
        var chk = function (id, v) { var el = document.getElementById(id); if (el) el.checked = !!v; };
        set('vo-movie-template', _videoOrg.movie_template || '');
        set('vo-episode-template', _videoOrg.episode_template || '');
        set('vo-youtube-template', _videoOrg.youtube_template || '');
        set('vo-transfer-mode', _videoOrg.transfer_mode || 'copy');
        set('vo-colon-replacement', _videoOrg.colon_replacement || 'delete');
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
            colon_replacement: val('vo-colon-replacement') || 'delete',
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
        var colonSel = document.getElementById('vo-colon-replacement');
        if (colonSel) colonSel.addEventListener('change', renderOrgPreview);
        ['vo-colon-replacement', 'vo-transfer-mode', 'vo-verify', 'vo-replace', 'vo-subs', 'vo-artwork', 'vo-nfo',
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
        // (the Folders card's Video panel is React and loads itself: video-downloads.ts)
        var panels = ['organization-video-panel'].map(function (id) {
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
        wireOrganization();
        _libraryLoading = Promise.all([loadOrganization()]).then(function (results) {
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
        // Video Quality and YouTube Quality load themselves (video-quality.tsx)
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
        var specials = document.getElementById('video-monitor-specials');
        if (specials) specials.addEventListener('change', function () { saveMonitorSpecials(); });
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
        // The shared "Save Settings" button. On the video side it flushes the
        // video settings here (capture phase), then lets the click through to the
        // button's own handler, which saves the rest of the page exactly as it
        // does on the music side - and shows the one toast.
        //
        // It used to stop the click here. That was right while the video side
        // could only show video settings; once Sources, Downloads, Library and
        // Quality became shared it meant every music field on those tabs, and all
        // of System (Security & Access included), was editable on the video side
        // and could never be saved there - while this toasted "Settings saved".
        // saveSettings is safe to run from here: it keeps the stored
        // active_media_server on the video side, so the video side still can't
        // change the music server. Music side: this does nothing.
        document.addEventListener('click', function (e) {
            if (document.body.getAttribute('data-side') !== 'video') return;
            if (!e.target.closest('#save-settings')) return;
            // saveSlskd is NOT in this list any more. Its form is retired - the
            // shared Sources tab's Soulseek card is the single editor now - but
            // the fields are still in the DOM holding whatever they loaded with.
            // Posting them here overwrote slskd settings that had just been
            // changed from the music side: edit the URL in Sources, autosave
            // stores it, then this button writes the stale copy back over it.
            // The music panel saves through the main settings save.
            // (Video Quality and YouTube Quality add their own saves: video-shown.ts)
            // (the video downloads config adds its own save too: video-downloads.ts)
            Promise.all([saveConn(true), save(true), saveKeys(true), savePrefs(true)])
                .catch(function () { toast('Some video settings could not be saved', 'error'); });
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
