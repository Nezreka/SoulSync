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
    var DOWNLOADS_URL = '/api/video/downloads/config';
    var SLSKD_URL = '/api/video/downloads/slskd';
    // the quality ladder's labels: React (video-quality.tsx)

    // the Server Connection (the server, its credentials, the Jellyfin user,
    // the libraries the scan reads) is React: connections-video-server.tsx,
    // video-server.ts (SETTINGS_PORT.md video pass V3b)

    // the enrichment keys and switches (the API Configuration cards) are React:
    // connections-video-api.tsx, video-keys.ts (SETTINGS_PORT.md video pass V3a)

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

    function toast(msg, type) {
        if (typeof showToast === 'function') showToast(msg, type);  // shared shell helper
    }

    // the Organization card's Video panel (templates, post-processing,
    // subtitles, Whisper, the Missing Subtitles job) is React and loads
    // itself: library-video-organization.tsx (SETTINGS_PORT.md video pass V2b)

    function onPageShown(e) {
        if (e && e.detail !== PAGE_ID) return;
        loadImportLists();
        loadNotify();
        // Video Quality and YouTube Quality load themselves (video-quality.tsx)
        loadSlskd();
        wireSlskd();
    }

    function init() {
        // the shared Save Settings button on the video side: every video
        // section is React and adds its own save to it (video-shown.ts)
        document.addEventListener('soulsync:video-page-shown', onPageShown);
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }
})();
