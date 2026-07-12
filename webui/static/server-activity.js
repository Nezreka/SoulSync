/*
 * SoulSync — Live Server Activity (app-wide, music + video).
 *
 * A Tautulli-style live view of every active Plex stream: who's playing what,
 * direct play vs transcode (with the codec line), bandwidth, and progress.
 * Opened by the floating activity button (next to the notifications bell);
 * slides a right-side drawer. Polls /api/server-activity every 3s while open,
 * and a light 20s tick keeps the button badge current from anywhere.
 * Self-contained IIFE exposing window.ServerActivity. No framework.
 */
(function () {
    'use strict';

    var URL = '/api/server-activity';
    var HIST = '/api/server-activity/history';
    var IMG = '/api/server-activity/image?path=';
    var drawer = null, isOpen = false, poll = null, badgePoll = null, tab = 'activity';

    function esc(s) {
        return String(s == null ? '' : s)
            .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
    }
    function getJSON(u) {
        return fetch(u, { headers: { Accept: 'application/json' } })
            .then(function (r) { return r.ok ? r.json() : null; }).catch(function () { return null; });
    }
    function img(path) { return path ? IMG + encodeURIComponent(path) : ''; }
    function mbps(kbps) { return kbps ? (kbps / 1000).toFixed(1) + ' Mbps' : ''; }
    function fmtTime(ms) {
        var t = Math.max(0, Math.floor((ms || 0) / 1000));
        var h = Math.floor(t / 3600), m = Math.floor((t % 3600) / 60), s = t % 60;
        var mm = (h && m < 10 ? '0' : '') + m, ss = (s < 10 ? '0' : '') + s;
        return (h ? h + ':' : '') + mm + ':' + ss;
    }
    function initials(name) {
        var p = String(name || '?').trim().split(/\s+/);
        return ((p[0] || '?')[0] + (p.length > 1 ? p[p.length - 1][0] : '')).toUpperCase();
    }
    var TYPE_IC = { movie: '🎬', episode: '📺', track: '🎵', clip: '🎞️' };
    function ago(epoch) {
        if (!epoch) return '';
        var s = Math.max(0, Math.floor(Date.now() / 1000 - epoch));
        if (s < 60) return 'just now';
        if (s < 3600) return Math.floor(s / 60) + 'm ago';
        if (s < 86400) return Math.floor(s / 3600) + 'h ago';
        if (s < 604800) return Math.floor(s / 86400) + 'd ago';
        return Math.floor(s / 604800) + 'w ago';
    }

    // ── one activity card ─────────────────────────────────────────────────────
    function actKey(s) { return s.session_key || (s.user + '|' + s.title); }
    function stateIcon(state) { return state === 'paused' ? '❚❚' : (state === 'buffering' ? '◌' : '▶'); }
    function card(s) {
        var st = s.stream || {}, method = st.method || 'Direct Play';
        var mCls = method === 'Transcode' ? 'tc' : (method === 'Direct Stream' ? 'ds' : 'ok');
        var artUrl = img(s.art || s.thumb);
        var poster = s.thumb ? img(s.thumb) : '';
        // transcode codec detail line (Tautulli signature)
        var xline = '';
        if (method !== 'Direct Play') {
            var bits = [];
            if (st.video) bits.push('Video ' + esc(st.video));
            if (st.audio && /→/.test(st.audio)) bits.push('Audio ' + esc(st.audio));
            if (st.throttled) bits.push('throttled');
            if (st.hw) bits.push('HW');
            if (bits.length) xline = '<div class="sact-xline">' + bits.join(' &middot; ') + '</div>';
        }
        var tags = '';
        if (st.resolution) tags += '<span class="sact-tag">' + esc(st.resolution) + '</span>';
        if (s.bandwidth_kbps) tags += '<span class="sact-tag">' + mbps(s.bandwidth_kbps) + '</span>';
        if (s.location) tags += '<span class="sact-tag sact-tag--' + esc(s.location) + '">' + esc(s.location.toUpperCase()) + '</span>';
        var stop = s.session_key
            ? '<button class="sact-stop" type="button" data-sact-stop="' + esc(s.session_key) +
              '" data-sact-title="' + esc(s.title) + '" title="Stop this stream">' +
              '<svg width="13" height="13" viewBox="0 0 24 24" fill="currentColor"><rect x="5" y="5" width="14" height="14" rx="2.5"/></svg></button>'
            : '';
        // a live equalizer glyph for music (CSS-animated; paused via the card state)
        var eq = (s.media_type === 'track')
            ? '<span class="sact-eq" aria-hidden="true"><i></i><i></i><i></i><i></i></span>' : '';
        var pct = s.progress_pct || 0;
        var remain = s.duration_ms ? ('-' + fmtTime(Math.max(0, s.duration_ms - s.offset_ms))) : '';
        var lc = s.link ? ' sact-card--link' : '';
        var la = s.link ? ' data-link-kind="' + esc(s.link.kind) + '" data-link-id="' + esc(s.link.id) +
            '" data-link-source="' + esc(s.link.source) + '"' : '';
        var openIc = s.link ? '<span class="sact-open" title="Open in SoulSync"></span>' : '';
        return '<div class="sact-card sact-st-' + esc(s.state) + lc + '" data-key="' + esc(actKey(s)) + '"' + la + '>' +
            (artUrl ? '<div class="sact-art" style="background-image:url(\'' + artUrl + '\')"></div>' : '') +
            '<div class="sact-scrim"></div>' + stop +
            '<div class="sact-row">' +
                (poster
                    ? '<div class="sact-poster"><img src="' + poster + '" alt="" loading="lazy" onerror="this.style.display=\'none\'">' + eq + '</div>'
                    : '<div class="sact-poster sact-poster--none">' + (TYPE_IC[s.media_type] || '🎬') + eq + '</div>') +
                '<div class="sact-info">' +
                    '<div class="sact-title" title="' + esc(s.title) + '">' + esc(s.title) + openIc + '</div>' +
                    (s.subtitle ? '<div class="sact-sub">' + esc(s.subtitle) + '</div>' : '') +
                    '<div class="sact-meta"><span class="sact-ava">' + esc(initials(s.user)) + '</span>' +
                        '<span class="sact-uname">' + esc(s.user) + '</span>' +
                        (s.player && (s.player.product || s.player.device)
                            ? '<span class="sact-dot">&middot;</span><span class="sact-dev">' + esc(s.player.product || s.player.device) + '</span>' : '') +
                    '</div>' +
                    '<div class="sact-badges"><span class="sact-badge sact-badge--' + mCls + '">' + esc(method) + '</span>' + tags + '</div>' +
                    xline +
                '</div>' +
            '</div>' +
            '<div class="sact-prog"><div class="sact-prog-fill" data-sact-fill style="width:' + pct + '%"><span class="sact-head-dot"></span></div></div>' +
            '<div class="sact-time"><span class="sact-elapsed" data-sact-elapsed>' + stateIcon(s.state) + ' ' + fmtTime(s.offset_ms) + '</span>' +
                '<span class="sact-remain" data-sact-remain>' + remain + '</span></div>' +
        '</div>';
    }

    function summaryBar(d) {
        var sm = d.summary || {};
        var chips = '<span class="sact-chip sact-chip--hero"><strong>' + (sm.streams || 0) + '</strong> ' +
            ((sm.streams === 1) ? 'stream' : 'streams') + '</span>';
        if (sm.transcodes) chips += '<span class="sact-chip sact-chip--tc"><strong>' + sm.transcodes + '</strong> transcoding</span>';
        if (sm.total_bandwidth_kbps) chips += '<span class="sact-chip">' + mbps(sm.total_bandwidth_kbps) + '</span>';
        if (sm.wan) chips += '<span class="sact-chip">' + sm.wan + ' remote</span>';
        return '<div class="sact-summary">' + chips + '</div>';
    }

    function _body() { return drawer && drawer.querySelector('[data-sact-body]'); }
    function _noServer(d) {
        return '<div class="sact-empty"><div class="sact-empty-ic">🔌</div>' +
            '<div class="sact-empty-t">' + esc((d && d.message) || 'Server unavailable') + '</div>' +
            '<div class="sact-empty-s">Set your Plex server in Settings to see live activity.</div></div>';
    }

    var _actData = null, _polledAt = 0, _actKeys = '';
    function _cardMap() {
        var m = {}, list = drawer && drawer.querySelector('[data-sact-list]');
        if (list) Array.prototype.forEach.call(list.querySelectorAll('.sact-card'), function (el) {
            m[el.getAttribute('data-key')] = el;
        });
        return m;
    }
    function renderActivity(d) {
        var body = _body(); if (!body) return;
        if (!d || d.ok === false) { body.innerHTML = _noServer(d); _actData = null; _actKeys = ''; return; }
        var sub = drawer.querySelector('[data-sact-server]');
        if (sub) sub.textContent = (d.server && d.server.name)
            ? (d.server.name + (d.server.version ? ' · ' + d.server.version : '')) : '';
        var sessions = d.sessions || [];
        _actData = d; _polledAt = Date.now();
        if (!sessions.length) {
            _actKeys = '';
            body.innerHTML = summaryBar(d) + '<div class="sact-empty"><div class="sact-empty-ic">🌙</div>' +
                '<div class="sact-empty-t">Nothing playing right now</div>' +
                '<div class="sact-empty-s">Active streams show up here the moment someone hits play.</div></div>';
            return;
        }
        var keys = sessions.map(actKey).join('§');
        // Same streams as last poll → DON'T rebuild the DOM (no art re-decode / flicker);
        // just refresh the summary + per-card state, and let the ticker glide the bars.
        if (keys === _actKeys && drawer.querySelector('[data-sact-list]')) {
            var sm = body.querySelector('[data-sact-summary]'); if (sm) sm.innerHTML = summaryBar(d);
            var map = _cardMap();
            sessions.forEach(function (s) {
                var el = map[actKey(s)]; if (!el) return;
                // Keep the link modifier — dropping it here is what made the card
                // stop being clickable ~1 tick after it first rendered.
                el.className = 'sact-card sact-st-' + s.state + (s.link ? ' sact-card--link' : '');
                var mb = el.querySelector('.sact-badge');
                if (mb) { var m = (s.stream || {}).method || 'Direct Play';
                    mb.className = 'sact-badge sact-badge--' + (m === 'Transcode' ? 'tc' : (m === 'Direct Stream' ? 'ds' : 'ok'));
                    mb.textContent = m; }
            });
            liveTick();
            return;
        }
        _actKeys = keys;
        body.innerHTML = '<div data-sact-summary>' + summaryBar(d) + '</div>' +
            '<div class="sact-list sact-enter" data-sact-list>' + sessions.map(card).join('') + '</div>';
        liveTick();
    }

    // Smoothly advance the progress bar + times between the 3s polls, from the
    // last poll's offset + wall-clock elapsed (playing only). This is what makes
    // it feel LIVE instead of stepping every few seconds.
    function liveTick() {
        if (!isOpen || tab !== 'activity' || !_actData) return;
        var now = Date.now(), map = _cardMap();
        (_actData.sessions || []).forEach(function (s) {
            var el = map[actKey(s)]; if (!el || !s.duration_ms) return;
            var live = s.offset_ms + (s.state === 'playing' ? (now - _polledAt) : 0);
            if (live > s.duration_ms) live = s.duration_ms;
            var pct = 100 * live / s.duration_ms;
            var fill = el.querySelector('[data-sact-fill]'); if (fill) fill.style.width = pct.toFixed(2) + '%';
            var ee = el.querySelector('[data-sact-elapsed]'); if (ee) ee.textContent = stateIcon(s.state) + ' ' + fmtTime(live);
            var rr = el.querySelector('[data-sact-remain]'); if (rr) rr.textContent = '-' + fmtTime(Math.max(0, s.duration_ms - live));
        });
    }

    // ── history tab ───────────────────────────────────────────────────────────
    function historyRow(h) {
        var poster = h.thumb ? img(h.thumb) : '';
        return '<div class="sact-hrow">' +
            (poster
                ? '<div class="sact-hthumb"><img src="' + poster + '" alt="" loading="lazy" onerror="this.style.display=\'none\'"></div>'
                : '<div class="sact-hthumb sact-hthumb--none">' + (TYPE_IC[h.media_type] || '🎬') + '</div>') +
            '<div class="sact-hinfo">' +
                '<div class="sact-htitle" title="' + esc(h.title) + '">' + esc(h.title) + '</div>' +
                (h.subtitle ? '<div class="sact-hsub">' + esc(h.subtitle) + '</div>' : '') +
                '<div class="sact-hmeta"><span class="sact-ava">' + esc(initials(h.user)) + '</span>' +
                    '<span class="sact-uname">' + esc(h.user) + '</span>' +
                    (h.device ? '<span class="sact-dot">&middot;</span><span class="sact-dev">' + esc(h.device) + '</span>' : '') +
                '</div>' +
            '</div>' +
            '<div class="sact-hwhen">' + esc(ago(h.viewed_epoch)) + '</div></div>';
    }
    function renderHistory(d) {
        var body = _body(); if (!body) return;
        if (!d || d.ok === false) { body.innerHTML = _noServer(d); return; }
        var rows = d.history || [];
        if (!rows.length) {
            body.innerHTML = '<div class="sact-empty"><div class="sact-empty-ic">🕓</div>' +
                '<div class="sact-empty-t">No history yet</div>' +
                '<div class="sact-empty-s">Finished streams show up here.</div></div>';
            return;
        }
        body.innerHTML = '<div class="sact-hlist">' + rows.map(historyRow).join('') + '</div>';
    }
    function loadHistory() {
        getJSON(HIST + '?limit=50').then(function (d) { if (isOpen && tab === 'history') renderHistory(d); });
    }

    // ── stats tab (beat the Tautulli/Plex dashboard glance) ───────────────────
    var STATS = '/api/server-activity/stats';
    function graph(series) {
        var s = series || [];
        var max = Math.max.apply(null, s.map(function (p) { return p.plays; }).concat([1]));
        var W = 416, H = 82, n = s.length || 1, gap = 4, bw = (W - (n - 1) * gap) / n;
        var bars = s.map(function (p, i) {
            var h = Math.max(p.plays ? 4 : 2, Math.round((p.plays / max) * (H - 10)));
            var x = i * (bw + gap), y = H - h;
            var day = p.date.slice(5);
            var peak = (p.plays === max && p.plays > 0) ? ' sact-bar--peak' : '';
            var empty = p.plays ? '' : ' sact-bar--empty';
            return '<rect x="' + x.toFixed(1) + '" y="' + y + '" width="' + bw.toFixed(1) + '" height="' + h +
                '" rx="2.5" class="sact-bar' + peak + empty + '"><title>' + esc(day) + ': ' + p.plays + ' plays</title></rect>';
        }).join('');
        var first = (s[0] && s[0].date.slice(5)) || '', last = (s[n - 1] && s[n - 1].date.slice(5)) || '';
        return '<svg class="sact-graph" viewBox="0 0 ' + W + ' ' + H + '" preserveAspectRatio="none">' +
                '<defs><linearGradient id="sactBar" x1="0" y1="0" x2="0" y2="1">' +
                    '<stop offset="0" stop-color="#4ade80"/><stop offset="1" stop-color="#22c55e" stop-opacity="0.45"/>' +
                '</linearGradient></defs>' + bars + '</svg>' +
            '<div class="sact-graph-x"><span>' + esc(first) + '</span><span>peak ' + max + '</span><span>' + esc(last) + '</span></div>';
    }
    function rankList(items, nameKey, cls) {
        var max = Math.max.apply(null, items.map(function (i) { return i.plays; }).concat([1]));
        return '<div class="sact-rank">' + items.map(function (it) {
            var av = (cls === 'user') ? '<span class="sact-ava">' + esc(initials(it[nameKey])) + '</span>' : '';
            return '<div class="sact-rank-row">' + av +
                '<span class="sact-rank-name" title="' + esc(it[nameKey]) + '">' + esc(it[nameKey]) + '</span>' +
                '<span class="sact-rank-bar"><span style="width:' + Math.round(100 * it.plays / max) + '%"></span></span>' +
                '<span class="sact-rank-n">' + it.plays + '</span></div>';
        }).join('') + '</div>';
    }
    function contentRow(c) {
        var poster = c.thumb ? img(c.thumb) : '';
        return '<div class="sact-cw">' +
            (poster ? '<div class="sact-cw-th"><img src="' + poster + '" alt="" loading="lazy" onerror="this.style.display=\'none\'"></div>'
                : '<div class="sact-cw-th sact-cw-th--none">' + (TYPE_IC[c.media_type] || '🎬') + '</div>') +
            '<div class="sact-cw-t" title="' + esc(c.title) + '">' + esc(c.title) + '</div>' +
            '<div class="sact-cw-n">' + c.plays + '</div></div>';
    }
    function section(title, inner) {
        return '<div class="sact-sec"><div class="sact-sec-h">' + esc(title) + '</div>' + inner + '</div>';
    }
    function renderStats(d) {
        var body = _body(); if (!body) return;
        if (!d || d.ok === false) { body.innerHTML = _noServer(d); return; }
        var html = '<div class="sact-summary">' +
            '<span class="sact-chip sact-chip--hero"><strong>' + (d.total_plays || 0) + '</strong> plays</span>' +
            '<span class="sact-chip"><strong>' + (d.unique_users || 0) + '</strong> users</span>' +
            '<span class="sact-chip">last ' + (d.days || 30) + ' days</span></div>';
        html += section('Plays over time', graph(d.series));
        if ((d.top_content || []).length)
            html += section('Most watched', '<div class="sact-cwlist">' + d.top_content.map(contentRow).join('') + '</div>');
        if ((d.top_users || []).length)
            html += section('Most active users', rankList(d.top_users, 'user', 'user'));
        if ((d.top_devices || []).length)
            html += section('Top devices', rankList(d.top_devices, 'device', 'device'));
        if (!(d.total_plays)) html = '<div class="sact-empty"><div class="sact-empty-ic">📊</div>' +
            '<div class="sact-empty-t">No plays in the last ' + (d.days || 30) + ' days</div></div>';
        body.innerHTML = html;
    }
    function loadStats() {
        getJSON(STATS).then(function (d) { if (isOpen && tab === 'stats') renderStats(d); });
    }

    function setBadge(n) {
        var b = document.getElementById('activity-float-badge');
        var btn = document.getElementById('activity-float-btn');
        if (!b || !btn) return;
        if (n > 0) { b.textContent = n > 99 ? '99+' : n; b.style.display = ''; btn.classList.add('activity-live'); }
        else { b.style.display = 'none'; btn.classList.remove('activity-live'); }
    }

    function refresh() {
        return getJSON(URL).then(function (d) {
            if (d) setBadge((d.summary && d.summary.streams) || 0);
            if (isOpen && tab === 'activity') renderActivity(d);
            return d;
        });
    }
    function startPoll() { stopPoll(); poll = setInterval(refresh, 3000); }   // live cadence
    function stopPoll() { if (poll) { clearInterval(poll); poll = null; } }

    // Live feed: prefer the WebSocket push (one upstream poll shared across every
    // open drawer — matters when multiple profiles are watching at once) and fall
    // back to the 3s HTTP poll when there's no socket. A watchdog re-arms HTTP if
    // the socket is connected but pushes stop landing.
    var _sockLive = false, _watchdog = null, _lastPush = 0;
    function onSocket(d) {
        _lastPush = Date.now();
        if (d) setBadge((d.summary && d.summary.streams) || 0);
        if (isOpen && tab === 'activity') renderActivity(d);
    }
    function startLive() {
        stopLive();
        refresh();   // instant HTTP paint — don't wait up to 3s for the first push
        var s = window.SoulSyncActivitySocket;
        if (s && s.isConnected()) {
            s.subscribe(); _sockLive = true; _lastPush = Date.now();
            _watchdog = setInterval(function () {
                if (_sockLive && Date.now() - _lastPush > 9000) { _sockLive = false; startPoll(); }
            }, 3000);
        } else {
            startPoll();
        }
    }
    function stopLive() {
        stopPoll();
        if (_watchdog) { clearInterval(_watchdog); _watchdog = null; }
        var s = window.SoulSyncActivitySocket;
        if (_sockLive && s) s.unsubscribe();
        _sockLive = false;
    }

    function setTab(t) {
        tab = t;
        if (drawer) drawer.querySelectorAll('[data-sact-tab]').forEach(function (b) {
            b.classList.toggle('sact-tab--on', b.getAttribute('data-sact-tab') === t);
        });
        var body = _body();
        if (body) body.innerHTML = '<div class="sact-empty"><div class="sact-empty-ic">…</div>' +
            '<div class="sact-empty-t">Loading…</div></div>';
        if (t === 'activity') { startLive(); }
        else if (t === 'history') { stopLive(); loadHistory(); }
        else { stopLive(); loadStats(); }
    }

    // ── drawer open/close ─────────────────────────────────────────────────────
    function build() {
        drawer = document.createElement('div');
        drawer.className = 'sact-drawer';
        drawer.innerHTML =
            '<div class="sact-head">' +
                '<div class="sact-head-t"><span class="sact-live-dot"></span>Server Activity' +
                    '<span class="sact-server" data-sact-server></span></div>' +
                '<button class="sact-x" type="button" data-sact-close aria-label="Close">&times;</button>' +
            '</div>' +
            '<div class="sact-tabs">' +
                '<button class="sact-tab sact-tab--on" type="button" data-sact-tab="activity">Activity</button>' +
                '<button class="sact-tab" type="button" data-sact-tab="history">History</button>' +
                '<button class="sact-tab" type="button" data-sact-tab="stats">Stats</button>' +
            '</div>' +
            '<div class="sact-body" data-sact-body></div>';
        document.body.appendChild(drawer);
        drawer.addEventListener('click', function (e) {
            if (e.target.closest('[data-sact-close]')) { close(); return; }
            var tb = e.target.closest('[data-sact-tab]');
            if (tb) { setTab(tb.getAttribute('data-sact-tab')); return; }
            var sb = e.target.closest('[data-sact-stop]');
            if (sb) { openStop(sb.getAttribute('data-sact-stop'), sb.getAttribute('data-sact-title')); return; }
            // Click a card → jump to that movie/show's page inside SoulSync.
            var lk = e.target.closest('.sact-card--link');
            if (lk) {
                var id = lk.getAttribute('data-link-id');
                close();
                if (window.SoulSyncVideo && window.SoulSyncVideo.openDetail)
                    window.SoulSyncVideo.openDetail({ kind: lk.getAttribute('data-link-kind'),
                        id: parseInt(id, 10) || id, source: lk.getAttribute('data-link-source') || 'library' });
                return;
            }
        });
        document.addEventListener('keydown', function (e) { if (e.key === 'Escape' && isOpen) close(); });
    }

    var ticker = null;
    function open() {
        if (!drawer) build();
        isOpen = true;
        if (_scrim()) _scrim().classList.add('visible');
        requestAnimationFrame(function () { drawer.classList.add('visible'); });
        setTab('activity');
        if (ticker) clearInterval(ticker);
        ticker = setInterval(liveTick, 500);   // glide the progress bars between polls
    }
    function close() {
        isOpen = false;
        if (drawer) drawer.classList.remove('visible');
        if (_scrim()) _scrim().classList.remove('visible');
        stopLive();
        if (ticker) { clearInterval(ticker); ticker = null; }
    }
    function toggle() { isOpen ? close() : open(); }

    // ── stop a stream (admin, with a message) ─────────────────────────────────
    function toast(m, t) { if (typeof showToast === 'function') showToast(m, t); }
    function openStop(key, title) {
        var ov = document.createElement('div');
        ov.className = 'sact-stop-ov';
        ov.innerHTML =
            '<div class="sact-stop-modal">' +
                '<div class="sact-stop-h">Stop stream</div>' +
                '<div class="sact-stop-sub">' + esc(title || 'this stream') + '</div>' +
                '<label class="sact-stop-lbl">Message shown to the viewer</label>' +
                '<textarea class="sact-stop-msg" rows="2">The server administrator ended this stream.</textarea>' +
                '<div class="sact-stop-foot">' +
                    '<button class="sact-stop-btn" type="button" data-stop-cancel>Cancel</button>' +
                    '<button class="sact-stop-btn sact-stop-btn--go" type="button" data-stop-go>Stop stream</button>' +
                '</div>' +
            '</div>';
        document.body.appendChild(ov);
        function shut() { ov.remove(); }
        ov.addEventListener('click', function (e) {
            if (e.target === ov || e.target.closest('[data-stop-cancel]')) { shut(); return; }
            if (e.target.closest('[data-stop-go]')) {
                var msg = ov.querySelector('.sact-stop-msg').value;
                var go = ov.querySelector('[data-stop-go]'); go.disabled = true; go.textContent = 'Stopping…';
                fetch('/api/server-activity/stop', {
                    method: 'POST', headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
                    body: JSON.stringify({ session_key: key, message: msg })
                }).then(function (r) { return r.json().then(function (b) { return { ok: r.ok, b: b }; }); })
                  .then(function (res) {
                      shut();
                      if (res.ok && res.b.ok) { toast('Stream stopped', 'success'); refresh(); }
                      else { toast((res.b && res.b.error) || 'Could not stop the stream', 'error'); }
                  }).catch(function () { shut(); toast('Could not stop the stream', 'error'); });
            }
        });
    }

    var _sc = null;
    function _scrim() {
        if (!_sc) {
            _sc = document.createElement('div');
            _sc.className = 'sact-scrim-bg';
            _sc.addEventListener('click', close);
            document.body.appendChild(_sc);
        }
        return _sc;
    }

    // light background tick so the badge is live from any page (cheap: sessions()
    // is fast; 20s is plenty for an ambient indicator)
    function startBadgePoll() {
        if (badgePoll) return;
        refresh();
        badgePoll = setInterval(function () { if (!isOpen) refresh(); }, 20000);
    }

    window.ServerActivity = {
        toggle: toggle, open: open, close: close, refresh: refresh,
        _onSocket: onSocket, _wantsLive: function () { return isOpen && tab === 'activity'; }
    };
    if (document.readyState === 'loading')
        document.addEventListener('DOMContentLoaded', startBadgePoll);
    else startBadgePoll();
})();
