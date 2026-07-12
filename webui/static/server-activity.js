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
    function card(s) {
        var st = s.stream || {}, method = st.method || 'Direct Play';
        var mCls = method === 'Transcode' ? 'tc' : (method === 'Direct Stream' ? 'ds' : 'ok');
        var artUrl = img(s.art || s.thumb);
        var poster = s.thumb ? img(s.thumb) : '';
        var stateIc = s.state === 'paused' ? '❚❚' : (s.state === 'buffering' ? '◌' : '▶');
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
        return '<div class="sact-card sact-st-' + esc(s.state) + '">' +
            (artUrl ? '<div class="sact-art" style="background-image:url(\'' + artUrl + '\')"></div>' : '') +
            '<div class="sact-scrim"></div>' +
            '<div class="sact-row">' +
                (poster
                    ? '<div class="sact-poster"><img src="' + poster + '" alt="" loading="lazy" onerror="this.style.display=\'none\'"></div>'
                    : '<div class="sact-poster sact-poster--none">' + (TYPE_IC[s.media_type] || '🎬') + '</div>') +
                '<div class="sact-info">' +
                    '<div class="sact-title" title="' + esc(s.title) + '">' + esc(s.title) + '</div>' +
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
            '<div class="sact-prog"><div class="sact-prog-fill" style="width:' + (s.progress_pct || 0) + '%"></div></div>' +
            '<div class="sact-time"><span>' + stateIc + ' ' + fmtTime(s.offset_ms) + '</span>' +
                '<span>' + fmtTime(s.duration_ms) + '</span></div>' +
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

    function renderActivity(d) {
        var body = _body(); if (!body) return;
        if (!d || d.ok === false) { body.innerHTML = _noServer(d); return; }
        var sub = drawer.querySelector('[data-sact-server]');
        if (sub) sub.textContent = (d.server && d.server.name)
            ? (d.server.name + (d.server.version ? ' · ' + d.server.version : '')) : '';
        if (!(d.sessions && d.sessions.length)) {
            body.innerHTML = summaryBar(d) + '<div class="sact-empty"><div class="sact-empty-ic">🌙</div>' +
                '<div class="sact-empty-t">Nothing playing right now</div>' +
                '<div class="sact-empty-s">Active streams show up here the moment someone hits play.</div></div>';
            return;
        }
        body.innerHTML = summaryBar(d) + '<div class="sact-list">' + d.sessions.map(card).join('') + '</div>';
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

    function setTab(t) {
        tab = t;
        if (drawer) drawer.querySelectorAll('[data-sact-tab]').forEach(function (b) {
            b.classList.toggle('sact-tab--on', b.getAttribute('data-sact-tab') === t);
        });
        var body = _body();
        if (body) body.innerHTML = '<div class="sact-empty"><div class="sact-empty-ic">…</div>' +
            '<div class="sact-empty-t">Loading…</div></div>';
        if (t === 'activity') { refresh(); startPoll(); }
        else { stopPoll(); loadHistory(); }
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
            '</div>' +
            '<div class="sact-body" data-sact-body></div>';
        document.body.appendChild(drawer);
        drawer.addEventListener('click', function (e) {
            if (e.target.closest('[data-sact-close]')) { close(); return; }
            var tb = e.target.closest('[data-sact-tab]');
            if (tb) { setTab(tb.getAttribute('data-sact-tab')); return; }
        });
        document.addEventListener('keydown', function (e) { if (e.key === 'Escape' && isOpen) close(); });
    }

    function open() {
        if (!drawer) build();
        isOpen = true;
        if (_scrim()) _scrim().classList.add('visible');
        requestAnimationFrame(function () { drawer.classList.add('visible'); });
        setTab('activity');
    }
    function close() {
        isOpen = false;
        if (drawer) drawer.classList.remove('visible');
        if (_scrim()) _scrim().classList.remove('visible');
        stopPoll();
    }
    function toggle() { isOpen ? close() : open(); }

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

    window.ServerActivity = { toggle: toggle, open: open, close: close, refresh: refresh };
    if (document.readyState === 'loading')
        document.addEventListener('DOMContentLoaded', startBadgePoll);
    else startBadgePoll();
})();
