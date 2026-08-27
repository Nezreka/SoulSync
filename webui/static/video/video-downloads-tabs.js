/*
 * SoulSync — Video Downloads page: the Review and Clients panes.
 *
 * Brings the video side up to the music downloads standard (Aug 27):
 * Review = the browsable recycle bin (restore / purge, the same manager the
 * music side got Aug 25) + the release blocklist inline. Clients = the
 * shared /api/clients adapters (torrent / usenet / slskd) in one pane.
 * Markup hangs off data-vdpg-pane hooks; the view switch lives in
 * video-downloads-page.js.
 */
(function () {
    'use strict';

    function esc(s) {
        return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) {
            return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
        });
    }
    function toast(m, t) { if (typeof showToast === 'function') showToast(m, t); }
    function getJSON(u) {
        return fetch(u).then(function (r) { return r.ok ? r.json() : null; }).catch(function () { return null; });
    }
    function sendJSON(u, method, b) {
        return fetch(u, { method: method, headers: { 'Content-Type': 'application/json' },
            body: b === undefined ? undefined : JSON.stringify(b || {}) })
            .then(function (r) { return r.ok ? r.json() : null; }).catch(function () { return null; });
    }
    function fmtSize(bytes) {
        var b = Number(bytes) || 0;
        if (b <= 0) return '';
        var u = ['B', 'KB', 'MB', 'GB', 'TB'], i = 0;
        while (b >= 1024 && i < u.length - 1) { b /= 1024; i += 1; }
        return b.toFixed(b >= 10 || i === 0 ? 0 : 1) + ' ' + u[i];
    }
    function fmtAge(sec) {
        if (sec == null) return 'age unknown';
        var s = Number(sec);
        if (s < 3600) return Math.max(1, Math.round(s / 60)) + 'm ago';
        if (s < 86400) return Math.round(s / 3600) + 'h ago';
        return Math.round(s / 86400) + 'd ago';
    }
    function fmtSpeed(bps) {
        var t = fmtSize(bps);
        return t ? t + '/s' : '';
    }
    function confirmDlg(opts) {
        if (typeof showConfirmDialog === 'function') return showConfirmDialog(opts);
        return Promise.resolve(true);
    }

    /* ── Review pane: recycle bin + blocklist ────────────────────────────── */

    var _sub = 'recycle';           // 'recycle' | 'blocklist'
    var _binCache = null;
    var _blkCache = null;

    function reviewHost() { return document.querySelector('[data-vdpg-pane="review"]'); }

    function renderReview() {
        var host = reviewHost(); if (!host) return;
        var bin = _binCache || { items: [] };
        var blk = _blkCache || [];
        var binN = (bin.items || []).length;
        var h = '<div class="adl-batch-filter-banner">' +
            '<button type="button" class="adl-pill' + (_sub === 'recycle' ? ' active' : '') + '" data-vrev-sub="recycle">🗑 Recycle bin (' + binN + ')</button>' +
            '<button type="button" class="adl-pill' + (_sub === 'blocklist' ? ' active' : '') + '" data-vrev-sub="blocklist">🚫 Blocklist (' + blk.length + ')</button>' +
            '<span class="verif-banner-spacer"></span>';
        if (_sub === 'recycle' && binN > 0) {
            h += '<button type="button" class="adl-filter-banner-clear" data-vrev-restore-all>↩ Restore all</button>' +
                 '<button type="button" class="adl-filter-banner-clear verif-bulk-danger" data-vrev-empty>🗑 Empty bin</button>';
        }
        if (_sub === 'blocklist' && blk.length > 0) {
            h += '<button type="button" class="adl-filter-banner-clear verif-bulk-danger" data-vrev-blk-clear>Clear all</button>';
        }
        h += '</div>';

        if (_sub === 'recycle') {
            h += '<div class="adl-review-explainer">files removed by upgrades, retention and dismissed imports land here instead of dying' +
                 (bin.recycle_enabled === false ? ' — recycling is currently OFF in organization settings, deletes are permanent' : '') +
                 '. restore puts a file back exactly where it was; entries older than the tracker restore into _restored for a rescan. ' +
                 'auto-purge after ' + (bin.keep_days != null ? bin.keep_days : 7) + ' days.</div>';
            if (!binN) {
                h += '<div class="adl-empty">the bin is empty</div>';
            } else {
                h += '<div class="adl-list">';
                (bin.items || []).forEach(function (e, i) {
                    h += '<div class="adl-row adl-row-completed" data-vrev-entry="' + i + '">' +
                        '<div class="adl-row-info">' +
                        '<div class="adl-row-title">' + esc(e.name.replace(/^\d{8}_\d{6}_(\(\d+\)_)?/, '')) + '</div>' +
                        '<div class="adl-row-meta">' + esc(e.original_path || 'original location unknown — restores into _restored') + '</div>' +
                        (e.reason ? '<div class="adl-row-batch">' + esc(e.reason) + '</div>' : '') +
                        '</div>' +
                        '<span class="adl-quality-chip">' + (fmtSize(e.size) || '—') + '</span>' +
                        '<span class="verif-time">' + esc(fmtAge(e.age_seconds)) + '</span>' +
                        '<button type="button" class="verif-act verif-act-ok verif-act-labeled" data-vrev-restore="' + i + '" title="Put this file back">↩<span class="verif-act-label">Restore</span></button>' +
                        '<button type="button" class="verif-act verif-act-del verif-act-labeled" data-vrev-purge="' + i + '" title="Delete permanently">🗑<span class="verif-act-label">Delete</span></button>' +
                        '</div>';
                });
                h += '</div>';
            }
        } else {
            h += '<div class="adl-review-explainer">exact releases (and blocked uploaders) the search will never pick again. remove one to make it eligible.</div>';
            if (!blk.length) {
                h += '<div class="adl-empty">nothing blocked</div>';
            } else {
                h += '<div class="adl-list">';
                blk.forEach(function (b) {
                    var what = b.release_title || b.filename || b.username || 'Unknown release';
                    var scope = b.filename ? '' : ' <span class="adl-quality-chip" title="Every file from this uploader is skipped">whole uploader</span>';
                    h += '<div class="adl-row adl-row-failed">' +
                        '<div class="adl-row-info">' +
                        '<div class="adl-row-title">' + esc(what) + scope + '</div>' +
                        '<div class="adl-row-meta">' + esc([b.title, b.username].filter(Boolean).join(' · ')) + '</div>' +
                        (b.reason ? '<div class="adl-row-error">' + esc(b.reason) + '</div>' : '') +
                        '</div>' +
                        '<button type="button" class="verif-act verif-act-labeled" data-vrev-unblock="' + b.id + '" title="Allow this release again">✕<span class="verif-act-label">Unblock</span></button>' +
                        '</div>';
                });
                h += '</div>';
            }
        }
        host.innerHTML = h;
    }

    function updateBadge() {
        var el = document.querySelector('[data-vdpg-review-n]');
        if (!el) return;
        var n = ((_binCache && _binCache.items) || []).length;
        el.textContent = String(n);
        // style.display, not [hidden]: .adl-pill-badge is inline-flex and CSS wins
        el.style.display = n ? '' : 'none';
    }

    function loadReview() {
        Promise.all([
            getJSON('/api/video/downloads/recycle'),
            getJSON('/api/video/downloads/blocklist'),
        ]).then(function (rs) {
            _binCache = rs[0] || { items: [] };
            _blkCache = (rs[1] && rs[1].items) || [];
            renderReview();
            updateBadge();
        });
    }

    function wireReview() {
        var host = reviewHost(); if (!host) return;
        host.addEventListener('click', function (ev) {
            var t = ev.target.closest ? ev.target.closest('button') : null;
            if (!t) return;
            var entries = (_binCache && _binCache.items) || [];
            if (t.hasAttribute('data-vrev-sub')) {
                _sub = t.getAttribute('data-vrev-sub');
                renderReview();
            } else if (t.hasAttribute('data-vrev-restore')) {
                var e = entries[Number(t.getAttribute('data-vrev-restore'))];
                if (!e) return;
                sendJSON('/api/video/downloads/recycle/restore', 'POST', { trash_dir: e.trash_dir, name: e.name })
                    .then(function (r) {
                        toast(r && r.success ? 'Restored to ' + (r.restored_to || 'library') : ((r && r.error) || 'Restore failed'), r && r.success ? 'success' : 'error');
                        loadReview();
                    });
            } else if (t.hasAttribute('data-vrev-purge')) {
                var p = entries[Number(t.getAttribute('data-vrev-purge'))];
                if (!p) return;
                confirmDlg({ title: 'Delete permanently', message: 'Delete "' + p.name + '" from the bin for good?', confirmText: 'Delete', destructive: true })
                    .then(function (ok) {
                        if (!ok) return;
                        sendJSON('/api/video/downloads/recycle/purge', 'POST', { trash_dir: p.trash_dir, name: p.name })
                            .then(function (r) {
                                toast(r && r.success ? 'Deleted' : ((r && r.error) || 'Delete failed'), r && r.success ? 'info' : 'error');
                                loadReview();
                            });
                    });
            } else if (t.hasAttribute('data-vrev-restore-all')) {
                sendJSON('/api/video/downloads/recycle/restore', 'POST', { all: true }).then(function (r) {
                    toast(r ? ('Restored ' + (r.restored || 0) + ' file(s)' + (r.failed ? ', ' + r.failed + ' failed' : '')) : 'Restore failed', 'success');
                    loadReview();
                });
            } else if (t.hasAttribute('data-vrev-empty')) {
                confirmDlg({ title: 'Empty bin', message: 'Permanently delete everything in the recycle bin?', confirmText: 'Empty bin', destructive: true })
                    .then(function (ok) {
                        if (!ok) return;
                        sendJSON('/api/video/downloads/recycle/purge', 'POST', { all: true }).then(function (r) {
                            toast(r ? ('Deleted ' + (r.purged || 0) + ' file(s)') : 'Failed', 'info');
                            loadReview();
                        });
                    });
            } else if (t.hasAttribute('data-vrev-unblock')) {
                sendJSON('/api/video/downloads/blocklist/' + t.getAttribute('data-vrev-unblock'), 'DELETE')
                    .then(function () { loadReview(); });
            } else if (t.hasAttribute('data-vrev-blk-clear')) {
                confirmDlg({ title: 'Clear blocklist', message: 'Every blocked release becomes pickable again. Clear it?', confirmText: 'Clear', destructive: true })
                    .then(function (ok) {
                        if (!ok) return;
                        sendJSON('/api/video/downloads/blocklist/clear', 'POST', {}).then(function () { loadReview(); });
                    });
            }
        });
    }

    /* ── Clients pane: the shared torrent / usenet / slskd adapters ──────── */

    var _ctab = 'torrent';
    var _cdata = {};                 // tab -> overview payload
    var _ctimer = null;

    function clientsHost() { return document.querySelector('[data-vdpg-pane="clients"]'); }

    /** Same on-page test the downloads poller uses. */
    function onDownloadsPage() {
        return document.body.getAttribute('data-side') === 'video' &&
            !!document.querySelector('[data-video-subpage="video-downloads"]:not([hidden])');
    }

    function healthOf(d) {
        if (!d) return 'unknown';
        if (d.configured === false) return 'off';
        if (d.connected === false) return 'bad';
        return 'ok';
    }
    var HEALTH_TEXT = { ok: 'connected', bad: 'unreachable', off: 'not configured', unknown: 'checking…' };

    function clientRow(it, tab) {
        var name = it.name || (it.filename ? it.filename.split(/[\\/]/).pop() : '') || 'Unknown';
        var pct = Math.max(0, Math.min(100, Number(it.progress) || 0));
        var bits = [fmtSize(it.size), fmtSpeed(it.download_speed != null ? it.download_speed : it.speed)].filter(Boolean).join(' · ');
        var mine = it.soulsync && it.soulsync.title ? '<span class="adl-quality-chip" title="Dispatched by SoulSync">' + esc(it.soulsync.title) + '</span>' : '';
        var actions = '';
        if (tab !== 'slskd') {
            actions = '<button type="button" class="verif-act" data-vcl-act="pause" data-vcl-id="' + esc(it.id) + '" title="Pause">⏸</button>' +
                '<button type="button" class="verif-act" data-vcl-act="resume" data-vcl-id="' + esc(it.id) + '" title="Resume">▶</button>' +
                '<button type="button" class="verif-act verif-act-del" data-vcl-act="remove" data-vcl-id="' + esc(it.id) + '" title="Remove from the client">🗑</button>';
        }
        return '<div class="adl-row' + (it.error ? ' adl-row-failed' : '') + '">' +
            '<div class="adl-row-info">' +
            '<div class="adl-row-title">' + esc(name) + ' ' + mine + '</div>' +
            '<div class="adl-row-meta">' + esc([it.state, bits].filter(Boolean).join(' · ')) + '</div>' +
            (it.error ? '<div class="adl-row-error">' + esc(it.error) + '</div>' : '') +
            '</div>' +
            '<span class="adl-quality-chip">' + Math.round(pct) + '%</span>' + actions +
            (pct > 0 && pct < 100 ? '<div class="adl-row-progress"><div class="adl-row-progress-fill" style="width:' + pct + '%"></div></div>' : '') +
            '</div>';
    }

    function renderClients() {
        var host = clientsHost(); if (!host) return;
        var tabs = [['torrent', '🧲 Torrents'], ['usenet', '📰 Usenet'], ['slskd', '🎧 Soulseek']];
        var h = '<div class="adl-batch-filter-banner">';
        tabs.forEach(function (t) {
            var d = _cdata[t[0]];
            var hl = healthOf(d);
            var n = d && d.items ? d.items.length : null;
            h += '<button type="button" class="adl-pill' + (_ctab === t[0] ? ' active' : '') + '" data-vcl-tab="' + t[0] + '" title="' + HEALTH_TEXT[hl] + '">' +
                '<span class="adl-client-dot adl-client-dot-' + (hl === 'ok' ? 'ok' : hl === 'bad' ? 'bad' : 'off') + '"></span> ' + t[1] +
                (n !== null && hl === 'ok' ? ' (' + n + ')' : '') + '</button>';
        });
        var cur = _cdata[_ctab];
        var health = healthOf(cur);
        h += '<span class="verif-banner-spacer"></span>' +
             '<span class="adl-client-health adl-client-health-' + health + '">' + HEALTH_TEXT[health] + '</span></div>';

        if (health !== 'ok') {
            h += '<div class="adl-empty">' + (health === 'off'
                ? 'nothing set up — configure a client in Settings and it shows up here'
                : 'can’t reach the client' + (cur && cur.error ? ' — ' + esc(cur.error) : '')) + '</div>';
        } else if (!cur.items || !cur.items.length) {
            h += '<div class="adl-empty">no transfers right now — all quiet</div>';
        } else {
            h += '<div class="adl-list">';
            cur.items.forEach(function (it) { h += clientRow(it, _ctab); });
            h += '</div>';
        }
        host.innerHTML = h;
    }

    function loadClients() {
        ['torrent', 'usenet', 'slskd'].forEach(function (tab) {
            getJSON('/api/clients/' + tab).then(function (d) {
                _cdata[tab] = d || _cdata[tab];
                renderClients();
            });
        });
    }

    function wireClients() {
        var host = clientsHost(); if (!host) return;
        host.addEventListener('click', function (ev) {
            var t = ev.target.closest ? ev.target.closest('button') : null;
            if (!t) return;
            if (t.hasAttribute('data-vcl-tab')) {
                _ctab = t.getAttribute('data-vcl-tab');
                renderClients();
            } else if (t.hasAttribute('data-vcl-act')) {
                var act = t.getAttribute('data-vcl-act');
                var id = t.getAttribute('data-vcl-id');
                var run = function () {
                    sendJSON('/api/clients/' + _ctab + '/action', 'POST', { id: id, action: act, delete_files: false })
                        .then(function (r) {
                            if (!(r && r.success)) toast((r && r.error) || act + ' failed', 'error');
                            loadClients();
                        });
                };
                if (act === 'remove') {
                    confirmDlg({ title: 'Remove from client', message: 'Remove this item from the download client? Downloaded data stays on disk.', confirmText: 'Remove', destructive: true })
                        .then(function (ok) { if (ok) run(); });
                } else run();
            }
        });
    }

    /* ── pane lifecycle, driven by the page's view switch ────────────────── */

    var _wired = false;

    window.VideoDownloadsTabs = {
        /** The page calls this whenever the view changes. */
        onView: function (view) {
            if (!_wired) { wireReview(); wireClients(); _wired = true; }
            if (_ctimer) { clearInterval(_ctimer); _ctimer = null; }
            if (view === 'review') {
                loadReview();
            } else if (view === 'clients') {
                loadClients();
                _ctimer = setInterval(function () {
                    // hidden tab OR navigated away entirely: the page's own
                    // poller has this discipline and this one didn't, so the
                    // clients pane kept hitting /api/clients every 10s forever
                    // once you left the downloads page (perf sweep rule).
                    if (document.hidden) return;
                    if (!onDownloadsPage()) {
                        clearInterval(_ctimer); _ctimer = null;
                        return;
                    }
                    loadClients();
                }, 10000);
            }
        },
        /** Page open: fetch the bin count once so the Review badge is honest
            without polling a filesystem walk every 2s. */
        refreshBadge: function () {
            getJSON('/api/video/downloads/recycle').then(function (d) {
                if (d) { _binCache = d; updateBadge(); }
            });
        },
    };
})();
