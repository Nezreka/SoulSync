/*
 * Video-side Service Status — the sidebar dots (data-video-only section) + the switch modal.
 *
 * Mirrors the music service-switch look (reuses the .ss-* modal classes) but the video side has
 * its OWN sources: TMDB/TVDB metadata (required, status-only), the video media server (Plex/
 * Jellyfin — switchable), and the video download preference (soulseek/torrent/usenet + a hybrid
 * order — switchable). Reads /api/video/service-status; writes /api/video/server and
 * /api/video/downloads/config. The music side is untouched.
 */
(function () {
    'use strict';
    var API = '/api/video';
    var SOURCES = ['soulseek', 'torrent', 'usenet'];
    var SRC_LABEL = { soulseek: 'Soulseek', torrent: 'Torrent', usenet: 'Usenet' };
    var SRV_LABEL = { plex: 'Plex', jellyfin: 'Jellyfin' };

    function esc(s) {
        return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) {
            return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
        });
    }
    function onVideoSide() { return document.body.getAttribute('data-side') === 'video'; }
    function isAdmin() {
        var ctx = (typeof getCurrentProfileContext === 'function') ? getCurrentProfileContext() : null;
        return !ctx || ctx.isAdmin !== false;   // fail-open to match the shell's admin gating
    }
    function toast(m, err) { if (typeof showToast === 'function') showToast(m, err ? 'error' : 'success'); }

    // ── sidebar dots ────────────────────────────────────────────────────────────
    function setDot(indId, nameId, ok, name) {
        var ind = document.getElementById(indId);
        if (ind) {
            var dot = ind.querySelector('.status-dot');
            if (dot) dot.className = 'status-dot ' + (ok ? 'connected' : 'disconnected');
            ind.setAttribute('data-status-ready', ok ? 'true' : 'false');
        }
        var nm = document.getElementById(nameId);
        if (nm && name != null) nm.textContent = name;
    }
    function applyStatus(d) {
        if (!d) return;
        var m = d.metadata || {}, s = d.server || {}, dl = d.download || {};
        setDot('video-metadata-indicator', 'video-metadata-name', !!m.configured,
            m.configured ? 'TMDB / TVDB' : 'TMDB / TVDB — add keys');
        setDot('video-server-indicator', 'video-server-name', !!s.configured, s.name || 'No server');
        setDot('video-download-indicator', 'video-download-name', !!dl.configured, dl.name || 'Downloads');
    }
    function fetchStatus() {
        return fetch(API + '/service-status', { headers: { Accept: 'application/json' } })
            .then(function (r) { return r.ok ? r.json() : null; })
            .then(function (d) { if (onVideoSide()) applyStatus(d); return d; })
            .catch(function () { return null; });
    }
    var pollTimer = null;
    function startPolling() {
        if (pollTimer) return;
        if (onVideoSide()) fetchStatus();
        pollTimer = setInterval(function () { if (onVideoSide()) fetchStatus(); }, 5000);
        // Refresh the instant the shell flips to the video side (don't wait up to 5s).
        try {
            new MutationObserver(function () { if (onVideoSide()) fetchStatus(); })
                .observe(document.body, { attributes: true, attributeFilter: ['data-side'] });
        } catch (e) { /* MutationObserver is always available in target browsers */ }
    }

    // ── the switch modal (reuses the .ss-* styling from service-switch.css) ──────
    var _tab = 'server', _data = null;
    var _TABS = [
        { id: 'metadata', name: 'Metadata', emoji: '🎬' },
        { id: 'server', name: 'Media Server', emoji: '🖥️' },
        { id: 'download', name: 'Downloads', emoji: '⬇️' }
    ];

    function ensureOverlay() {
        var o = document.getElementById('video-service-switch-overlay');
        if (o) return o;
        o = document.createElement('div');
        o.id = 'video-service-switch-overlay';
        o.className = 'modal-overlay ss-overlay hidden';
        o.innerHTML =
            '<div class="ss-modal" role="dialog" aria-modal="true" aria-label="Video Sources" tabindex="-1">' +
                '<div class="ss-topbar">' +
                    '<div class="ss-topbar-icon"><img src="/static/trans2.png" alt="SoulSync" class="ss-topbar-logo"></div>' +
                    '<div class="ss-topbar-titles">' +
                        '<h3 class="ss-topbar-title">Video Sources</h3>' +
                        '<div class="ss-topbar-sub">What the video side uses for metadata, server, and downloads</div>' +
                    '</div>' +
                    '<button class="ss-icon-btn ss-icon-btn--close" title="Close" onclick="closeVideoServiceSwitchModal()">&times;</button>' +
                '</div>' +
                '<div class="ss-body"><div class="ss-rail" id="vss-rail"></div><div class="ss-panel" id="vss-panel"></div></div>' +
            '</div>';
        document.body.appendChild(o);
        o.addEventListener('click', function (e) { if (e.target === o) closeVideoServiceSwitchModal(); });
        return o;
    }

    function load() {
        var panel = document.getElementById('vss-panel');
        if (panel) panel.innerHTML = '<div class="ss-empty">Loading…</div>';
        return fetch(API + '/service-status', { headers: { Accept: 'application/json' } })
            .then(function (r) { return r.ok ? r.json() : null; })
            .then(function (d) { _data = d || {}; render(); applyStatus(d); })
            .catch(function () {
                var p = document.getElementById('vss-panel');
                if (p) p.innerHTML = '<div class="ss-empty">Couldn\'t load video status.</div>';
            });
    }

    function render() { renderRail(); renderPanel(); }

    function renderRail() {
        var rail = document.getElementById('vss-rail');
        if (!rail) return;
        var cur = {
            metadata: (_data.metadata || {}).name || 'TMDB / TVDB',
            server: (_data.server || {}).name || 'No server',
            download: (_data.download || {}).name || 'Soulseek'
        };
        rail.innerHTML = _TABS.map(function (t) {
            return '<button class="ss-tab' + (t.id === _tab ? ' active' : '') + '" onclick="_vssTab(\'' + t.id + '\')">' +
                '<span class="ss-tab-emoji">' + t.emoji + '</span>' +
                '<span class="ss-tab-text"><span class="ss-tab-cat">' + t.name + '</span>' +
                '<span class="ss-tab-cur">' + esc(cur[t.id]) + '</span></span></button>';
        }).join('');
    }

    function card(label, emoji, active, locked, onclick, badge) {
        return '<button class="ss-card' + (active ? ' active' : '') + (locked ? ' ss-card--locked' : '') + '" ' +
            (onclick && !locked ? 'onclick="' + onclick + '"' : 'disabled') + '>' +
            '<span class="ss-card-emoji">' + emoji + '</span>' +
            '<span class="ss-card-label">' + esc(label) + '</span>' +
            (badge ? '<span class="ss-card-badge">' + esc(badge) + '</span>' : '') +
            (active ? '<span class="ss-card-check">✓</span>' : '') + '</button>';
    }

    function renderPanel() {
        var panel = document.getElementById('vss-panel');
        if (!panel) return;
        if (_tab === 'metadata') panel.innerHTML = panelMetadata();
        else if (_tab === 'server') panel.innerHTML = panelServer();
        else { panel.innerHTML = panelDownload(); wireHybridDrag(); }
    }

    function panelMetadata() {
        var m = _data.metadata || {};
        return '<div class="ss-grid">' +
            card('TMDB', '🎬', !!m.tmdb, true, null, m.tmdb ? 'Set' : 'Missing') +
            card('TVDB', '📺', !!m.tvdb, true, null, m.tvdb ? 'Set' : 'Missing') +
            '</div>' +
            '<div class="ss-hint">TMDB &amp; TVDB are <strong>required</strong> and can\'t be swapped &mdash; the video side matches and enriches everything from them. Set the keys in <strong>Settings &rarr; Connections</strong>.</div>';
    }

    function panelServer() {
        var s = _data.server || {};
        var out = '<div class="ss-grid">';
        ['plex', 'jellyfin'].forEach(function (srv) {
            var configured = !!s[srv];
            out += card(SRV_LABEL[srv], srv === 'plex' ? '🟠' : '🟣', s.active === srv, !configured,
                configured ? "_vssSetServer('" + srv + "')" : null, configured ? null : 'Not set up');
        });
        out += '</div>';
        if (!s.plex && !s.jellyfin) {
            out += '<div class="ss-hint">No video server configured yet &mdash; add Plex or Jellyfin in <strong>Settings &rarr; Connections</strong>.</div>';
        }
        return out;
    }

    function panelDownload() {
        var d = _data.download || {};
        var mode = d.mode || 'soulseek';
        var hybrid = mode === 'hybrid';
        var toggle = '<div class="ss-seg">' +
            '<button class="ss-seg-btn' + (!hybrid ? ' active' : '') + '" onclick="_vssMode(\'single\')">Single source</button>' +
            '<button class="ss-seg-btn' + (hybrid ? ' active' : '') + '" onclick="_vssMode(\'hybrid\')">Hybrid</button></div>';
        if (!hybrid) {
            var cards = '<div class="ss-grid">' + SOURCES.map(function (src) {
                return card(SRC_LABEL[src], '⬇️', mode === src, false, "_vssSetSource('" + src + "')");
            }).join('') + '</div>';
            return toggle + cards;
        }
        var order = (d.hybrid_order && d.hybrid_order.length) ? d.hybrid_order : SOURCES.slice();
        var rows = order.map(function (src, i) {
            return '<div class="ss-hybrid-item" draggable="true" data-src="' + src + '">' +
                '<span class="ss-hybrid-rank">' + (i + 1) + '</span>' +
                '<span class="ss-card-emoji">⬇️</span>' +
                '<span class="ss-hybrid-name">' + esc(SRC_LABEL[src] || src) + '</span></div>';
        }).join('');
        return toggle +
            '<div class="ss-hint">Drag to set priority &mdash; the first source that has the file wins.</div>' +
            '<div class="ss-hybrid-list" id="vss-hybrid-list">' + rows + '</div>';
    }

    // Drag-to-reorder the hybrid chain (mirrors the music modal's wiring).
    function wireHybridDrag() {
        var list = document.getElementById('vss-hybrid-list');
        if (!list) return;
        list.querySelectorAll('.ss-hybrid-item').forEach(function (item) {
            item.addEventListener('dragstart', function (e) {
                e.dataTransfer.effectAllowed = 'move';
                e.dataTransfer.setData('text/plain', item.dataset.src);
                item.classList.add('dragging');
            });
            item.addEventListener('dragend', function () { item.classList.remove('dragging'); });
            item.addEventListener('dragover', function (e) { e.preventDefault(); });
            item.addEventListener('drop', function (e) {
                e.preventDefault();
                var dragged = e.dataTransfer.getData('text/plain');
                if (dragged && dragged !== item.dataset.src) reorder(dragged, item.dataset.src);
            });
        });
    }
    function reorder(draggedId, targetId) {
        var order = ((_data.download || {}).hybrid_order || []).slice();
        if (!order.length) order = SOURCES.slice();
        var from = order.indexOf(draggedId);
        if (from < 0) return;
        order.splice(from, 1);
        var to = order.indexOf(targetId);
        order.splice(to < 0 ? order.length : to, 0, draggedId);
        saveDownload({ download_mode: 'hybrid', hybrid_order: order }).then(load);
    }

    // ── actions ──────────────────────────────────────────────────────────────────
    function guardAdmin() {
        if (isAdmin()) return true;
        toast('Only an admin can change sources', true);
        return false;
    }
    function saveServer(srv) {
        return fetch(API + '/server', {
            method: 'POST', headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
            body: JSON.stringify({ server: srv })
        }).then(function (r) { return r.json(); });
    }
    function saveDownload(patch) {
        return fetch(API + '/downloads/config', {
            method: 'POST', headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
            body: JSON.stringify(patch)
        }).then(function (r) { return r.json(); });
    }

    window._vssTab = function (t) { _tab = t; render(); };
    window._vssSetServer = function (srv) {
        if (!guardAdmin()) return;
        saveServer(srv).then(function (res) {
            if (res && (res.status === 'saved' || res.server)) { toast('Video server set to ' + (SRV_LABEL[srv] || srv)); load(); }
            else toast((res && res.error) || 'Could not switch server', true);
        }).catch(function () { toast('Could not switch server', true); });
    };
    window._vssSetSource = function (src) {
        if (!guardAdmin()) return;
        saveDownload({ download_mode: src }).then(function () {
            toast('Download source set to ' + (SRC_LABEL[src] || src)); load();
        }).catch(function () { toast('Could not save', true); });
    };
    window._vssMode = function (m) {
        if (!guardAdmin()) return;
        if (m === 'hybrid') {
            var order = ((_data.download || {}).hybrid_order || []).slice();
            if (!order.length) order = SOURCES.slice();
            saveDownload({ download_mode: 'hybrid', hybrid_order: order }).then(load);
        } else {
            var first = ((_data.download || {}).hybrid_order || [])[0] || 'soulseek';
            saveDownload({ download_mode: first }).then(load);
        }
    };

    window.openVideoServiceSwitchModal = function (tab) {
        if (!guardAdmin()) return;
        _tab = (tab === 'metadata' || tab === 'server' || tab === 'download') ? tab : 'server';
        ensureOverlay().classList.remove('hidden');
        load();
    };
    window.closeVideoServiceSwitchModal = function () {
        var o = document.getElementById('video-service-switch-overlay');
        if (o) o.classList.add('hidden');
    };
    document.addEventListener('keydown', function (e) {
        if (e.key === 'Escape') {
            var o = document.getElementById('video-service-switch-overlay');
            if (o && !o.classList.contains('hidden')) closeVideoServiceSwitchModal();
        }
    });

    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', startPolling);
    else startPolling();
})();
