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
                // autoplay and the region are React (library-video-prefs.tsx)
            })
            .catch(function () { /* ignore */ });
    }

    // Video Preferences (autoplay, the region, Monitor Specials) is React and
    // saves itself: library-video-prefs.tsx (SETTINGS_PORT.md video pass V2c)

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

    // the Organization card's Video panel (templates, post-processing,
    // subtitles, Whisper, the Missing Subtitles job) is React and loads
    // itself: library-video-organization.tsx (SETTINGS_PORT.md video pass V2b)

    function onPageShown(e) {
        if (e && e.detail !== PAGE_ID) return;
        loadServer();
        loadConn();
        load();
        loadKeys();
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
            // (the video downloads config adds its own save too: video-downloads.ts,
            // and Video Preferences: video-prefs.ts)
            Promise.all([saveConn(true), save(true), saveKeys(true)])
                .catch(function () { toast('Some video settings could not be saved', 'error'); });
        }, true);
        document.addEventListener('soulsync:video-page-shown', onPageShown);
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }
})();
