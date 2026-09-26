/*
 * SoulSync — Video Issues (the music Issues standard, video-scoped).
 *
 * Page: status/category/item-type filters, a text search, a mine/everyone
 * toggle for admins, stat cards and issue cards with "load more". A card opens
 * the thread modal: item header, the conversation (comments + quiet status
 * events), a reply box, and one primary action, the category's fix tool.
 *
 * Reporting: window.VideoIssues.openReport({entityType, entityId, name, meta}),
 * wired from the Manage sidebar and the expanded episode row. Identity is the
 * session. The nav badge shows the OPEN count to admins and a member's unread
 * updates to members.
 */
(function () {
    'use strict';

    var API = '/api/video/issues';
    var PAGE_SIZE = 50;
    var BADGE_REFRESH_MS = 60000;
    var state = {
        status: 'open', category: 'all', entity: 'all', text: '', scope: 'everyone',
        categories: [], issues: [], total: 0, loadingMore: false, listSeq: 0,
        picked: new Set(),   // admin bulk select: ticked issue ids
    };

    function $(sel) { return document.querySelector(sel); }
    function esc(s) {
        return String(s == null ? '' : s)
            .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
    }
    function toast(msg, type) { if (typeof showToast === 'function') showToast(msg, type); }
    function confirmDlg(o) {
        return (typeof showConfirmDialog === 'function') ? showConfirmDialog(o) : Promise.resolve(false);
    }
    function isAdmin() {
        return !(typeof currentProfile !== 'undefined' && currentProfile && !currentProfile.is_admin);
    }
    function myProfileId() {
        return (typeof currentProfile !== 'undefined' && currentProfile) ? currentProfile.id : null;
    }
    function jget(url) { return fetch(url).then(function (r) { return r.ok ? r.json() : null; }); }
    function jsend(url, body, method) {
        return fetch(url, { method: method || 'POST', headers: { 'Content-Type': 'application/json' },
            body: body == null ? undefined : JSON.stringify(body) })
            .then(function (r) {
                return r.json().catch(function () { return {}; })
                    .then(function (b) { return { ok: r.ok, status: r.status, body: b || {} }; });
            });
    }

    // Every vi modal closes on Escape (self-cleaning listener).
    function escClosable(ov) {
        function onKey(e) {
            // the app confirm sits above us; let it have Escape
            var cm = document.getElementById('confirm-modal-overlay');
            if (cm && !cm.classList.contains('hidden')) return;
            if (e.key === 'Escape') { e.stopPropagation(); ov.remove(); }
        }
        document.addEventListener('keydown', onKey, true);
        var obs = new MutationObserver(function () {
            if (!document.body.contains(ov)) {
                document.removeEventListener('keydown', onKey, true);
                obs.disconnect();
            }
        });
        obs.observe(document.body, { childList: true });
    }

    // calls back once the overlay a fix tool opened has gone away. tools
    // don't take a close callback, so watch the body for their overlay.
    function whenToolCloses(selector, done) {
        var seen = false, finished = false;
        var obs = new MutationObserver(check);
        function check() {
            var open = document.querySelector(selector);
            if (open) { seen = true; return; }
            if (seen && !finished) { finished = true; obs.disconnect(); done(); }
        }
        obs.observe(document.body, { childList: true });
        // the tool may fetch before it mounts; give up quietly if it never shows
        setTimeout(function () { if (!seen && !finished) { finished = true; obs.disconnect(); } }, 15000);
        check();
    }

    // sqlite stamps come back as 'YYYY-MM-DD HH:MM:SS' in utc with no zone;
    // Date() reads that shape as local time, so pin it to utc
    function parseDate(s) {
        if (!s) return null;
        var t = String(s).trim();
        var m = /^(\d{4}-\d{2}-\d{2})[ T](\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?)$/.exec(t);
        var d = new Date(m ? m[1] + 'T' + m[2] + 'Z' : t);
        return isNaN(d.getTime()) ? null : d;
    }
    function fullDate(s) {
        var d = parseDate(s);
        return d ? d.toLocaleString(undefined, { month: 'short', day: 'numeric', year: 'numeric',
            hour: '2-digit', minute: '2-digit' }) : '';
    }
    function ago(s) {
        var d = parseDate(s);
        if (!d) return '';
        var sec = Math.max(0, Math.round((Date.now() - d.getTime()) / 1000));
        if (sec < 60) return 'just now';
        var min = Math.round(sec / 60);
        if (min < 60) return min + 'm ago';
        var hr = Math.round(min / 60);
        if (hr < 24) return hr + 'h ago';
        var day = Math.round(hr / 24);
        if (day < 7) return day + 'd ago';
        return d.toLocaleDateString(undefined, { month: 'short', day: 'numeric', year: 'numeric' });
    }
    function followersLine(list) {
        var names = (list || []).map(function (f) { return String(f.follower_name || '').trim(); })
            .filter(Boolean);
        if (!names.length) return '';
        if (names.length === 1) return names[0] + ' hit this too';
        var n = names.length - 1;
        return names[0] + ' and ' + n + (n === 1 ? ' other' : ' others') + ' hit this too';
    }
    function isActive(status) { return status === 'open' || status === 'in_progress'; }

    var STATUS_META = {
        open: ['Open', 'vi-st--open'], in_progress: ['In Progress', 'vi-st--progress'],
        resolved: ['Resolved', 'vi-st--resolved'], dismissed: ['Dismissed', 'vi-st--dismissed'],
    };
    var CATEGORY_ICONS = {
        wrong_match: '🎯', wrong_metadata: '📝', wrong_poster: '🖼', bad_quality: '📉',
        audio_issue: '🔇', subtitle_issue: '💬', playback_issue: '⏯', missing_content: '🧩',
        duplicate: '👯', other: '⚑',
    };
    var ENTITY_LABELS = { movie: 'Movie', show: 'Show', episode: 'Episode' };

    function catLabel(key) {
        var c = state.categories.filter(function (x) { return x.key === key; })[0];
        return c ? c.label : String(key || '').replace(/_/g, ' ');
    }

    function loadCategories() {
        if (state.categories.length) return Promise.resolve();
        return jget(API + '/categories').then(function (d) {
            state.categories = (d && d.categories) || [];
            var sel = $('[data-vi-filter-category]');
            if (sel && sel.options.length <= 1) {
                state.categories.forEach(function (c) {
                    var o = document.createElement('option');
                    o.value = c.key; o.textContent = c.label;
                    sel.appendChild(o);
                });
            }
        });
    }

    // what an issue is about, in words and in library terms
    function itemName(i, snap) {
        if (i.entity_type === 'episode') {
            return [snap.show_title, snap.code].filter(Boolean).join(' ') +
                (snap.title ? ' · ' + snap.title : '') || ('Episode #' + i.entity_id);
        }
        return snap.title || snap.show_title || ((ENTITY_LABELS[i.entity_type] || 'Item') + ' #' + i.entity_id);
    }
    // the detail page an issue points at: episodes live on their show's page
    function itemTarget(i, snap) {
        if (i.entity_type === 'episode') {
            var sid = parseInt(snap.show_id, 10);
            return isNaN(sid) ? null : { kind: 'show', id: sid };
        }
        var id = parseInt(i.entity_id, 10);
        return isNaN(id) ? null : { kind: i.entity_type, id: id };
    }
    function openItem(target) {
        document.dispatchEvent(new CustomEvent('soulsync:video-open-detail', {
            detail: { kind: target.kind, id: target.id, source: 'library' } }));
    }

    // ── badge ────────────────────────────────────────────────────────────────
    // admins see what's open; members see their own reports with news
    function refreshBadge() {
        jget(API + '/counts').then(function (d) {
            var b = document.getElementById('video-issues-nav-badge');
            if (!b || !d || !d.counts) return;
            var n = isAdmin() ? (d.counts.open || 0) : (d.counts.updates || 0);
            b.textContent = n;
            b.classList.toggle('hidden', n === 0);
        });
    }

    // ── page ─────────────────────────────────────────────────────────────────
    function onIssuesPage() {
        var host = $('[data-video-subpage="video-issues"]');
        return !!(host && !host.hidden);
    }

    function loadPage() {
        var sub = $('[data-vi-subtitle]');
        if (sub) {
            sub.textContent = isAdmin() ? 'Problems people reported on library items'
                : 'Problems you reported and where they stand';
        }
        ensureRefineControls();
        loadCategories().then(loadStats).then(function () { loadList(true); });
    }

    // the extra filters are built here so index.html keeps its simple toolbar
    function ensureRefineControls() {
        var bar = $('.vi-toolbar');
        if (!bar || bar.querySelector('[data-vi-filter-entity]')) {
            var scopeBox = bar && bar.querySelector('[data-vi-scope]');
            if (scopeBox) scopeBox.hidden = !isAdmin();
            return;
        }
        var ent = document.createElement('select');
        ent.className = 'library-source-filter-select';
        ent.setAttribute('data-vi-filter-entity', '');
        ent.setAttribute('aria-label', 'Item type');
        ent.innerHTML = '<option value="all">All items</option><option value="movie">Movies</option>' +
            '<option value="show">Shows</option><option value="episode">Episodes</option>';
        ent.addEventListener('change', function () { state.entity = ent.value; loadList(true); });
        var txt = document.createElement('input');
        txt.type = 'search';
        txt.className = 'vi-search';
        txt.placeholder = 'Search titles, items, people…';
        txt.setAttribute('aria-label', 'Search issues');
        txt.setAttribute('data-vi-search', '');
        txt.addEventListener('input', function () { state.text = txt.value; renderList(); });
        var scope = document.createElement('div');
        scope.className = 'vi-scope';
        scope.setAttribute('data-vi-scope', '');
        scope.setAttribute('role', 'group');
        scope.setAttribute('aria-label', 'Whose issues');
        scope.innerHTML = '<button type="button" data-vi-scope-btn="everyone" aria-pressed="true">Everyone</button>' +
            '<button type="button" data-vi-scope-btn="mine" aria-pressed="false">Mine</button>';
        scope.hidden = !isAdmin();
        scope.addEventListener('click', function (e) {
            var b = e.target.closest('[data-vi-scope-btn]');
            if (!b) return;
            state.scope = b.getAttribute('data-vi-scope-btn');
            scope.querySelectorAll('[data-vi-scope-btn]').forEach(function (x) {
                x.setAttribute('aria-pressed', String(x === b));
            });
            renderList();
        });
        bar.insertBefore(txt, bar.firstChild);
        bar.appendChild(ent);
        bar.appendChild(scope);
    }

    function loadStats() {
        return jget(API + '/counts').then(function (d) {
            var host = $('[data-vi-stats]');
            if (!host || !d || !d.counts) return;
            var c = d.counts;
            host.innerHTML = [['open', 'Open'], ['in_progress', 'In Progress'],
                              ['resolved', 'Resolved'], ['dismissed', 'Dismissed'],
                              ['total', 'Total']].map(function (s) {
                return '<div class="vi-stat vi-stat--' + s[0] + '"><div class="vi-stat-n">' +
                    (c[s[0]] || 0) + '</div><div class="vi-stat-l">' + s[1] + '</div></div>';
            }).join('');
        });
    }

    function listUrl(offset) {
        var q = new URLSearchParams();
        q.set('limit', String(PAGE_SIZE));
        if (offset) q.set('offset', String(offset));
        if (state.status !== 'all') q.set('status', state.status);
        if (state.category !== 'all') q.set('category', state.category);
        if (state.entity !== 'all') q.set('entity_type', state.entity);
        return API + '?' + q.toString();
    }

    function loadList(reset) {
        var host = $('[data-vi-list]');
        if (!host) return;
        var seq = ++state.listSeq;
        var offset = reset ? 0 : state.issues.length;
        if (!reset) { state.loadingMore = true; renderMore(); }
        jget(listUrl(offset)).then(function (d) {
            if (seq !== state.listSeq) return;   // a newer filter change won
            state.loadingMore = false;
            if (!d || !d.success) {
                if (reset) host.innerHTML = '<div class="repair-empty">Couldn’t load issues</div>';
                else toast('Couldn’t load more issues', 'error');
                renderMore();
                return;
            }
            var got = d.issues || [];
            if (reset) state.picked = new Set();
            state.issues = reset ? got : state.issues.concat(got);
            state.total = typeof d.total === 'number' ? d.total : state.issues.length;
            // a short page is the end, whatever total says (it ignores the category)
            state.hasMore = got.length >= PAGE_SIZE && state.issues.length < state.total;
            renderList();
        });
        refreshBadge();
    }

    function matchesText(i, needle) {
        if (!needle) return true;
        var snap = i.snapshot_data || {};
        return [i.title, i.description, i.reporter_name, snap.title, snap.show_title,
                catLabel(i.category)].filter(Boolean).join(' ').toLowerCase().indexOf(needle) !== -1;
    }

    function renderList() {
        var host = $('[data-vi-list]');
        if (!host) return;
        var needle = state.text.trim().toLowerCase();
        var me = myProfileId();
        var rows = state.issues.filter(function (i) {
            return (state.scope === 'everyone' || i.profile_id === me) && matchesText(i, needle);
        });
        state.visible = rows;
        // a ticked row that's filtered away isn't ticked any more
        var shown = {};
        rows.forEach(function (i) { shown[i.id] = true; });
        state.picked.forEach(function (id) { if (!shown[id]) state.picked.delete(id); });
        if (!rows.length) {
            var filtered = needle || state.scope === 'mine' || state.status !== 'open' ||
                state.category !== 'all' || state.entity !== 'all';
            host.innerHTML = '<div class="repair-empty-state">' +
                '<div class="repair-empty-icon">✅</div>' +
                '<div class="repair-empty-title">No issues here</div>' +
                '<div class="repair-empty-text">' + (filtered
                    ? 'Nothing matches these filters.'
                    : 'Nothing open. Reports land here when someone flags a problem on an item.') +
                '</div></div>';
        } else {
            host.innerHTML = rows.map(rowHTML).join('');
        }
        host.classList.toggle('vi-list--picking', state.picked.size > 0);
        renderMore();
        renderBulk();
    }

    // an admin's row: a tick on the art corner, then the card
    function rowHTML(i) {
        if (!isAdmin()) return cardHTML(i);
        var on = state.picked.has(i.id);
        return '<div class="vi-row' + (on ? ' vi-row--on' : '') + '">' +
            '<input type="checkbox" class="vi-pick" data-vi-pick="' + i.id + '"' + (on ? ' checked' : '') +
            ' aria-label="Select ' + esc(i.title) + '">' + cardHTML(i) + '</div>';
    }

    // ── bulk bar: "3 selected · Resolve · Close · Priority ▾ · Delete" ──────
    function renderBulk() {
        var host = $('[data-vi-list]');
        if (!host) return;
        var bar = host.parentNode.querySelector('[data-vi-bulk]');
        var n = state.picked.size;
        if (!isAdmin() || !n) { if (bar) bar.remove(); return; }
        var total = (state.visible || []).length;
        if (!bar) {
            bar = document.createElement('div');
            bar.className = 'vi-bulk';
            bar.setAttribute('data-vi-bulk', '');
            bar.setAttribute('role', 'toolbar');
            bar.setAttribute('aria-label', 'Selected issues');
            host.parentNode.insertBefore(bar, host.nextSibling);
        }
        bar.innerHTML = '<span class="vi-bulk-count" aria-live="polite">' + n + ' selected</span>' +
            (n < total ? '<button type="button" class="vi-bulk-quiet" data-vi-bulk-all>Select all ' + total + '</button>' : '') +
            '<span class="vbb-spacer"></span>' +
            '<button type="button" class="vi-bulk-btn" data-vi-bulk-status="resolved">Resolve</button>' +
            '<button type="button" class="vi-bulk-btn" data-vi-bulk-status="dismissed" title="Close without a change">Close</button>' +
            '<span class="vi-menu"><button type="button" class="vi-bulk-btn" data-vi-bulk-prio aria-haspopup="menu" aria-expanded="false">Priority ▾</button>' +
                '<span class="vi-menu-pop vi-bulk-pop" role="menu" aria-label="Priority" hidden>' +
                ['low', 'normal', 'high'].map(function (p) {
                    return '<button type="button" role="menuitem" class="vi-menu-item" data-vi-bulk-set="' + p + '">' +
                        p.charAt(0).toUpperCase() + p.slice(1) + '</button>';
                }).join('') + '</span></span>' +
            '<button type="button" class="vi-bulk-btn vi-bulk-danger" data-vi-bulk-del>Delete</button>' +
            '<button type="button" class="vi-bulk-x" data-vi-bulk-clear aria-label="Clear selection">×</button>';
    }

    function bulkWords(change, r) {
        var verb = change.delete ? 'Deleted' : change.priority ? 'Set ' + change.priority + ' priority on'
            : change.status === 'resolved' ? 'Resolved' : 'Closed';
        var head = verb + ' ' + (r.done || 0);
        return r.failed ? head + ', ' + r.failed + ' didn’t change' : head;
    }

    function runBulk(change) {
        var ids = Array.from(state.picked);
        if (!ids.length) return;
        var bar = $('[data-vi-bulk]');
        if (bar) bar.querySelectorAll('button').forEach(function (b) { b.disabled = true; });
        var body = { ids: ids };
        Object.keys(change).forEach(function (k) { body[k] = change[k]; });
        jsend(API + '/bulk', body).then(function (r) {
            if (r.ok && r.body.success) {
                toast(bulkWords(change, r.body), r.body.failed ? 'warning' : 'success');
                state.picked = new Set();
                loadStats();
                loadList(true);
            } else {
                toast((r.body && r.body.error) || 'Couldn’t change them', 'error');
                renderBulk();
            }
        });
    }

    function onBulkClick(e) {
        if (e.target.closest('[data-vi-bulk-all]')) {
            (state.visible || []).forEach(function (i) { state.picked.add(i.id); });
            renderList();
            return true;
        }
        if (e.target.closest('[data-vi-bulk-clear]')) { state.picked = new Set(); renderList(); return true; }
        var st = e.target.closest('[data-vi-bulk-status]');
        if (st) { runBulk({ status: st.getAttribute('data-vi-bulk-status') }); return true; }
        var trig = e.target.closest('[data-vi-bulk-prio]');
        if (trig) {
            var pop = trig.parentNode.querySelector('.vi-bulk-pop');
            pop.hidden = !pop.hidden;
            trig.setAttribute('aria-expanded', String(!pop.hidden));
            if (!pop.hidden) { var first = pop.querySelector('button'); if (first) first.focus(); }
            return true;
        }
        var set = e.target.closest('[data-vi-bulk-set]');
        if (set) { runBulk({ priority: set.getAttribute('data-vi-bulk-set') }); return true; }
        if (e.target.closest('[data-vi-bulk-del]')) {
            var n = state.picked.size;
            confirmDlg({ title: n === 1 ? 'Delete this issue?' : 'Delete ' + n + ' issues?',
                message: 'The reports and their threads are removed for good.',
                confirmText: 'Delete', destructive: true })
                .then(function (yes) { if (yes) runBulk({ delete: true }); });
            return true;
        }
        return false;
    }

    function renderMore() {
        var host = $('[data-vi-list]');
        if (!host) return;
        var more = host.parentNode.querySelector('[data-vi-more]');
        if (!more) {
            more = document.createElement('div');
            more.className = 'vi-more';
            more.setAttribute('data-vi-more', '');
            host.parentNode.insertBefore(more, host.nextSibling);
        }
        if (!state.hasMore) { more.hidden = true; more.innerHTML = ''; return; }
        more.hidden = false;
        more.innerHTML = '<span class="vi-more-count">Showing ' + state.issues.length + ' of ' +
            state.total + '</span><button type="button" class="vi-more-btn" data-vi-more-btn' +
            (state.loadingMore ? ' disabled>Loading…' : '>Load more') + '</button>';
    }

    function cardHTML(i) {
        var snap = i.snapshot_data || {};
        var st = STATUS_META[i.status] || [i.status, ''];
        var icon = CATEGORY_ICONS[i.category] || '⚑';
        var thumb = snap.poster
            ? '<img class="vi-card-thumb" src="' + esc(snap.poster) + '" alt="" loading="lazy" ' +
              'onerror="this.outerHTML=\'<div class=&quot;vi-card-thumb vi-card-thumb--ph&quot;>' +
              icon + '</div>\'">'
            : '<div class="vi-card-thumb vi-card-thumb--ph">' + icon + '</div>';
        var unread = i.reporter_unread && i.profile_id === myProfileId();
        return '<div class="vi-card" data-vi-open="' + i.id + '" role="button" tabindex="0">' + thumb +
            '<div class="vi-card-mid">' +
                '<div class="vi-card-title">' + esc(i.title) +
                    (unread ? ' <span class="vi-new" title="New reply or status change">New</span>' : '') +
                '</div>' +
                '<div class="vi-card-entity">' + esc(ENTITY_LABELS[i.entity_type] || i.entity_type) +
                    ' · ' + esc(itemName(i, snap)) + (snap.year ? ' (' + esc(snap.year) + ')' : '') + '</div>' +
                (i.description ? '<div class="vi-card-desc">' + esc(i.description) + '</div>' : '') +
                '<div class="vi-card-foot" title="' + esc(fullDate(i.created_at)) + '">' +
                    esc(catLabel(i.category)) + ' · ' + esc(ago(i.created_at)) +
                    (isAdmin() && i.reporter_name ? ' · by ' + esc(i.reporter_name) : '') + '</div>' +
            '</div>' +
            '<div class="vi-card-right"><span class="vi-st ' + st[1] + '">' + st[0] + '</span>' +
                '<span class="vi-prio vi-prio--' + esc(i.priority) + '" title="' + esc(i.priority) + ' priority"></span></div>' +
        '</div>';
    }

    // ── fix tools ────────────────────────────────────────────────────────────
    // each returns true when it opened a tool in place (the thread comes back
    // with a "did that fix it?" offer when it closes), false when it sent the
    // admin somewhere to finish the job
    function runFix(i, snap, reopen) {
        var fix = i.fix_action || {};
        var target = itemTarget(i, snap);
        function sendTo(hint) {
            if (!target) { toast('This report didn’t keep a link to the item', 'warning'); return; }
            if (hint) toast(hint, 'info');
            openItem(target);
        }
        switch (fix.id) {
            case 'fix_match':
                if (window.VideoManage && target) {
                    VideoManage.open({ kind: target.kind, id: target.id, focusMatch: 'tmdb' });
                    whenToolCloses('.vmg-overlay', reopen);
                    return;
                }
                sendTo('Open Manage there and fix the match');
                return;
            case 'pick_art':
                if (window.VideoPoster && snap.tmdb_id && target) {
                    VideoPoster.open({ kind: target.kind, tmdbId: snap.tmdb_id, libraryId: target.id,
                        title: snap.show_title || snap.title || '', year: snap.year || null });
                    whenToolCloses('.vpm-overlay', reopen);
                    return;
                }
                sendTo('Pick the new poster from the item’s page');
                return;
            case 'upgrade':
                if (window.VideoDownload && VideoDownload.manualSearch && i.entity_type === 'movie') {
                    VideoDownload.manualSearch({ title: snap.title || '', scope: 'movie', year: snap.year || null,
                        mediaId: target && target.id, mediaSource: 'library', poster: snap.poster || null });
                    whenToolCloses('.vms-overlay', reopen);
                    return;
                }
                if (window.VideoDownload && VideoDownload.manualSearch && i.entity_type === 'episode' &&
                        snap.season_number != null && snap.episode_number != null) {
                    VideoDownload.manualSearch({ title: snap.show_title || '', scope: 'episode',
                        season: snap.season_number, episode: snap.episode_number,
                        mediaId: target && target.id, mediaSource: 'library', year: snap.year || null,
                        poster: snap.poster || null });
                    whenToolCloses('.vms-overlay', reopen);
                    return;
                }
                sendTo('Search for a better copy from the item’s page');
                return;
            case 'wishlist_missing':
                sendTo('Use Wishlist missing on the show’s page');
                return;
            case 'find_duplicates':
                toast('Run the duplicate scan in Library Maintenance, then resolve this', 'info');
                document.dispatchEvent(new CustomEvent('soulsync:video-navigate', { detail: 'video-tools' }));
                return;
            default:
                sendTo();
        }
    }

    // ── detail modal ─────────────────────────────────────────────────────────
    function openDetail(id, opts) {
        opts = opts || {};
        jget(API + '/' + id).then(function (d) {
            if (!d || !d.success) { toast('Couldn’t load the issue', 'error'); return; }
            // opening your own report clears its unread flag server side
            refreshBadge();
            renderDetail(d.issue, opts);
        });
    }

    function commentHTML(author, when, body, emptyText) {
        return '<li class="vi-c">' +
            '<span class="vi-c-avatar" aria-hidden="true">' + esc((String(author).trim()[0] || '?').toUpperCase()) + '</span>' +
            '<div class="vi-c-main"><div class="vi-c-head"><span class="vi-c-author">' + esc(author) + '</span>' +
                '<span class="vi-c-when" title="' + esc(fullDate(when)) + '">' + esc(ago(when)) + '</span></div>' +
                (body ? '<div class="vi-c-body">' + esc(body) + '</div>'
                      : '<div class="vi-c-body vi-c-body--empty">' + esc(emptyText || '') + '</div>') +
            '</div></li>';
    }

    function menuHTML(i, admin, mine) {
        var items = [];
        if (admin) {
            if (i.status === 'open') items.push(['status', 'in_progress', 'Mark in progress']);
            if (isActive(i.status)) items.push(['status', 'dismissed', 'Dismiss']);
            else items.push(['status', 'open', 'Reopen']);
            items.push(['head', '', 'Priority']);
            ['low', 'normal', 'high'].forEach(function (p) {
                items.push(['priority', p, p.charAt(0).toUpperCase() + p.slice(1),
                    (i.priority || 'normal') === p]);
            });
            items.push(['delete', '', 'Delete issue']);
        } else if (mine) {
            // the reporter can fix their own words while it's still being looked at
            if (isActive(i.status)) items.push(['edit', '', 'Edit']);
            if (i.status === 'open') items.push(['delete', '', 'Withdraw report']);
        }
        if (!items.length) return '';
        return '<div class="vi-menu">' +
            '<button type="button" class="vi-menu-trigger" data-vi-menu aria-label="More actions" ' +
                'aria-haspopup="menu" aria-expanded="false">⋯</button>' +
            '<div class="vi-menu-pop" role="menu" hidden>' + items.map(function (it) {
                if (it[0] === 'head') return '<div class="vi-menu-head">' + esc(it[2]) + '</div>';
                if (it[0] === 'edit') {
                    return '<button type="button" role="menuitem" class="vi-menu-item" data-vi-edit>' + esc(it[2]) + '</button>';
                }
                if (it[0] === 'delete') {
                    return '<button type="button" role="menuitem" class="vi-menu-item vi-menu-item--danger" data-vi-del>' +
                        esc(it[2]) + '</button>';
                }
                var attr = it[0] === 'status' ? 'data-vi-act="' + it[1] + '"' : 'data-vi-prio="' + it[1] + '"';
                return '<button type="button" role="' + (it[0] === 'priority' ? 'menuitemradio' : 'menuitem') +
                    '"' + (it[0] === 'priority' ? ' aria-checked="' + !!it[3] + '"' : '') +
                    ' class="vi-menu-item" ' + attr + '><span>' + esc(it[2]) + '</span>' +
                    (it[3] ? '<span aria-hidden="true">✓</span>' : '') + '</button>';
            }).join('') + '</div></div>';
    }

    function renderDetail(i, opts) {
        var snap = i.snapshot_data || {};
        var admin = isAdmin();
        var mine = i.profile_id === myProfileId();
        var st = STATUS_META[i.status] || [i.status, ''];
        var icon = CATEGORY_ICONS[i.category] || '⚑';
        var target = itemTarget(i, snap);
        var reporter = i.reporter_name || (mine ? 'You' : '');
        var eyebrow = [catLabel(i.category), reporter, ago(i.created_at)].filter(Boolean).join(' · ');
        var fix = admin && i.fix_action && isActive(i.status) ? i.fix_action : null;
        var comments = i.comments || [];
        var legacy = i.admin_response && !comments.some(function (c) {
            return c.kind !== 'event' && String(c.body).trim() === String(i.admin_response).trim();
        });
        var files = (snap.files || []).map(function (f) {
            return '<div class="vi-file">' + esc([f.resolution, f.video_codec,
                f.size_bytes ? (f.size_bytes / 1073741824).toFixed(1) + ' GB' : null]
                .filter(Boolean).join(' · ')) + '</div>';
        }).join('');
        var followers = followersLine(i.followers);
        var prio = i.priority && i.priority !== 'normal'
            ? '<span class="vi-th-prio"><span class="vi-prio vi-prio--' + esc(i.priority) + '"></span>' +
              esc(i.priority) + ' priority</span>' : '';

        var ov = document.createElement('div');
        ov.className = 'vi-overlay';
        ov.innerHTML = '<div class="vi-modal vi-modal--thread" role="dialog" aria-modal="true" aria-label="Issue #' + i.id + '">' +
            '<div class="vi-modal-head">Issue #' + i.id +
                '<button class="vmg-close" data-vi-close aria-label="Close">×</button></div>' +
            '<div class="vi-modal-body">' +
                '<div class="vi-th-hero">' +
                    (snap.poster
                        ? '<img class="vi-th-art" src="' + esc(snap.poster) + '" alt="" onerror="this.style.visibility=\'hidden\'">'
                        : '<div class="vi-th-art vi-card-thumb--ph">' + icon + '</div>') +
                    '<div class="vi-th-info">' +
                        '<div class="vi-th-eyebrow" title="' + esc(fullDate(i.created_at)) + '">' + esc(eyebrow) + '</div>' +
                        '<div class="vi-th-title">' + esc(i.title) + '</div>' +
                        '<div class="vi-th-item"><span class="vi-th-type">' + esc(ENTITY_LABELS[i.entity_type] || i.entity_type) +
                            '</span>' + esc(itemName(i, snap)) + (snap.year ? ' (' + esc(snap.year) + ')' : '') + '</div>' +
                        '<div class="vi-th-links" data-vi-links>' +
                            (target ? '<button type="button" class="vi-link" data-vi-view>View item →</button>' : '') +
                        '</div>' +
                    '</div>' +
                    '<div class="vi-th-side"><span class="vi-st ' + st[1] + '">' + st[0] + '</span>' + prio + '</div>' +
                '</div>' +
                (followers ? '<div class="vi-th-followers">' + esc(followers) + '</div>' : '') +
                (opts.offerResolve && admin && isActive(i.status)
                    ? '<div class="vi-offer" role="status"><span>Did that fix it?</span>' +
                        '<span class="vi-offer-actions"><button type="button" class="vi-quiet" data-vi-offer-no>Not yet</button>' +
                        '<button type="button" class="vi-btn-primary" data-vi-act="resolved">Mark resolved</button></span></div>'
                    : '') +
                '<ol class="vi-thread" aria-label="Thread">' +
                    commentHTML(reporter || 'Reporter', i.created_at, i.description || '', 'No details given') +
                    (legacy ? commentHTML('Admin', i.updated_at, i.admin_response) : '') +
                    comments.map(function (c) {
                        if (c.kind === 'event') {
                            return '<li class="vi-ev"><span class="vi-ev-dot" aria-hidden="true"></span>' +
                                '<span><strong>' + esc(c.author_name || 'Someone') + '</strong> ' + esc(c.body) + '</span>' +
                                '<span class="vi-c-when" title="' + esc(fullDate(c.created_at)) + '">' + esc(ago(c.created_at)) + '</span></li>';
                        }
                        return commentHTML(c.author_name || 'Someone', c.created_at, c.body);
                    }).join('') +
                '</ol>' +
                '<textarea class="vi-response vi-reply" data-vi-reply maxlength="4000" aria-label="Reply" placeholder="' +
                    (admin ? 'Reply to the reporter…' : 'Add a reply…') + '"></textarea>' +
                (files ? '<details class="vi-snap"><summary>As it was when reported</summary>' + files + '</details>' : '') +
            '</div>' +
            '<div class="vi-modal-foot">' +
                menuHTML(i, admin, mine) +
                (admin && isActive(i.status) ? '<button type="button" class="vi-quiet" data-vi-act="resolved">Resolve</button>' : '') +
                '<span class="vbb-spacer"></span>' +
                '<button type="button" class="' + (fix ? 'vmg-btn-ghost' : 'vi-btn-primary') + '" data-vi-send disabled>Send reply</button>' +
                (fix ? '<button type="button" class="vi-btn-primary" data-vi-fix>' + esc(fix.label) + '</button>' : '') +
            '</div>' +
        '</div>';
        document.body.appendChild(ov);
        escClosable(ov);

        var reply = ov.querySelector('[data-vi-reply]');
        var send = ov.querySelector('[data-vi-send]');
        reply.addEventListener('input', function () { send.disabled = !reply.value.trim(); });
        reply.addEventListener('keydown', function (e) {
            if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) { e.preventDefault(); sendReply(); }
        });

        // admins act on the item: say so when it's gone from the library
        if (admin && target) {
            jget('/api/video/detail/' + target.kind + '/' + target.id).then(function (item) {
                if (item || !document.body.contains(ov)) return;
                var links = ov.querySelector('[data-vi-links]');
                if (links) links.innerHTML = '<span class="vi-removed">Item removed from the library</span>';
                var fb = ov.querySelector('[data-vi-fix]');
                if (fb) { fb.disabled = true; fb.title = 'The item is no longer in the library'; }
            });
        }

        function reload(extra) { ov.remove(); openDetail(i.id, extra); if (onIssuesPage()) { loadStats(); loadList(true); } }

        function sendReply() {
            var body = reply.value.trim();
            if (!body) return;
            send.disabled = true;
            jsend(API + '/' + i.id + '/comments', { body: body }).then(function (r) {
                if (r.ok && r.body.success) reload();
                else { send.disabled = false; toast((r.body && r.body.error) || 'Couldn’t send the reply', 'error'); }
            });
        }

        function update(body, done) {
            jsend(API + '/' + i.id, body, 'PUT').then(function (r) {
                if (r.ok && r.body.success) { if (done) toast(done, 'success'); reload(); }
                else toast((r.body && r.body.error) || 'Update failed', 'error');
            });
        }

        // title and details become fields, with save and cancel
        function startEdit() {
            if (ov.querySelector('.vi-own-edit')) return;
            var titleEl = ov.querySelector('.vi-th-title');
            titleEl.hidden = true;
            var form = document.createElement('form');
            form.className = 'vi-own-edit';
            form.setAttribute('aria-label', 'Edit your report');
            form.innerHTML = '<label class="vi-own-label">Title<input class="vmg-input" data-vi-edit-title maxlength="200"></label>' +
                '<label class="vi-own-label">Details<textarea class="vi-response" data-vi-edit-desc maxlength="2000" rows="4"></textarea></label>' +
                '<div class="vi-own-actions"><button type="button" class="vi-quiet" data-vi-edit-cancel>Cancel</button>' +
                '<button type="submit" class="vi-btn-primary" data-vi-edit-save>Save</button></div>';
            var t = form.querySelector('[data-vi-edit-title]');
            var d = form.querySelector('[data-vi-edit-desc]');
            var save = form.querySelector('[data-vi-edit-save]');
            t.value = i.title || '';
            d.value = i.description || '';
            t.addEventListener('input', function () { save.disabled = !t.value.trim(); });
            form.addEventListener('submit', function (ev) {
                ev.preventDefault();
                if (!t.value.trim()) return;
                save.disabled = true;
                jsend(API + '/' + i.id, { title: t.value.trim(), description: d.value.trim() }, 'PUT').then(function (r) {
                    if (r.ok && r.body.success) { toast('Saved', 'success'); reload(); }
                    else { save.disabled = false; toast((r.body && r.body.error) || 'Couldn’t save it', 'error'); }
                });
            });
            ov.querySelector('.vi-th-hero').insertAdjacentElement('afterend', form);
            t.focus();
        }

        function closeMenu() {
            var pop = ov.querySelector('.vi-menu-pop');
            var trig = ov.querySelector('[data-vi-menu]');
            if (pop) pop.hidden = true;
            if (trig) trig.setAttribute('aria-expanded', 'false');
        }

        ov.addEventListener('click', function (e) {
            if (e.target === ov || e.target.closest('[data-vi-close]')) { ov.remove(); return; }
            var trig = e.target.closest('[data-vi-menu]');
            if (trig) {
                var pop = ov.querySelector('.vi-menu-pop');
                pop.hidden = !pop.hidden;
                trig.setAttribute('aria-expanded', String(!pop.hidden));
                return;
            }
            if (!e.target.closest('.vi-menu')) closeMenu();
            if (e.target.closest('[data-vi-view]')) {
                ov.remove();
                openItem(target);
                return;
            }
            if (e.target.closest('[data-vi-offer-no]')) {
                var offer = ov.querySelector('.vi-offer');
                if (offer) offer.remove();
                return;
            }
            if (e.target.closest('[data-vi-send]')) { sendReply(); return; }
            if (e.target.closest('[data-vi-edit]')) { closeMenu(); startEdit(); return; }
            if (e.target.closest('[data-vi-edit-cancel]')) {
                var f = ov.querySelector('.vi-own-edit');
                if (f) f.remove();
                ov.querySelector('.vi-th-title').hidden = false;
                return;
            }
            if (e.target.closest('[data-vi-fix]')) {
                ov.remove();
                runFix(i, snap, function () { openDetail(i.id, { offerResolve: true }); });
                return;
            }
            var act = e.target.closest('[data-vi-act]');
            if (act) {
                closeMenu();
                var status = act.getAttribute('data-vi-act');
                update({ status: status }, status === 'resolved' ? 'Marked resolved' : null);
                return;
            }
            var pr = e.target.closest('[data-vi-prio]');
            if (pr) {
                closeMenu();
                if (pr.getAttribute('data-vi-prio') !== (i.priority || 'normal')) {
                    update({ priority: pr.getAttribute('data-vi-prio') });
                }
                return;
            }
            if (e.target.closest('[data-vi-del]')) {
                closeMenu();
                confirmDlg({ title: admin ? 'Delete this issue?' : 'Withdraw this report?',
                    message: admin ? 'The report and its thread are removed for good.'
                        : 'Your report is removed for good.',
                    confirmText: admin ? 'Delete' : 'Withdraw', destructive: true })
                    .then(function (yes) {
                        if (!yes) return;
                        jsend(API + '/' + i.id, null, 'DELETE').then(function (r) {
                            if (r.ok && r.body.success) {
                                toast('Issue removed', 'info'); ov.remove();
                                if (onIssuesPage()) loadPage(); else refreshBadge();
                            } else toast('Couldn’t remove the issue', 'error');
                        });
                    });
            }
        });
    }

    // ── report modal (window.VideoIssues.openReport) ─────────────────────────
    function openReport(opts) {
        opts = opts || {};
        var entityType = opts.entityType, entityId = opts.entityId;
        if (!entityType || entityId == null) return;
        var admin = isAdmin();
        loadCategories().then(function () {
            var cats = state.categories.filter(function (c) {
                return c.applies.indexOf(entityType) !== -1;
            });
            var ov = document.createElement('div');
            ov.className = 'vi-overlay';
            ov.innerHTML = '<div class="vi-modal" role="dialog" aria-modal="true" aria-label="Report a problem">' +
                '<div class="vi-modal-head">Report a problem' +
                    '<button class="vmg-close" data-vi-close aria-label="Close">×</button></div>' +
                '<div class="vi-modal-body">' +
                    '<div class="vi-report-entity">' + esc(opts.name || '') +
                        (opts.meta ? '<span class="vi-hero-sub"> · ' + esc(opts.meta) + '</span>' : '') + '</div>' +
                    '<div class="vi-sect"><div class="vi-sect-h">What’s wrong?</div>' +
                        '<div class="vi-cats">' + cats.map(function (c) {
                            return '<button class="vi-cat" type="button" data-vi-cat="' + esc(c.key) + '">' +
                                (CATEGORY_ICONS[c.key] || '⚑') + ' ' + esc(c.label) + '</button>';
                        }).join('') + '</div></div>' +
                    '<div class="vi-sect"><div class="vi-sect-h">Title</div>' +
                        '<input class="vmg-input" data-vi-title maxlength="200" aria-label="Title"></div>' +
                    '<div class="vi-sect"><div class="vi-sect-h">Details</div>' +
                        '<textarea class="vi-response" data-vi-desc maxlength="2000" aria-label="Details" ' +
                        'placeholder="Anything that helps pin it down…"></textarea></div>' +
                    // members don't pick priority; the admin sets it when triaging
                    (admin ? '<div class="vi-sect"><div class="vi-sect-h">Priority</div>' +
                        '<div class="vi-prios">' + ['low', 'normal', 'high'].map(function (p) {
                            return '<button class="vi-cat vi-prio-btn' + (p === 'normal' ? ' vi-cat--on' : '') +
                                '" type="button" data-vi-priority="' + p + '">' + p + '</button>';
                        }).join('') + '</div></div>' : '') +
                '</div>' +
                '<div class="vi-modal-foot"><span class="vbb-spacer"></span>' +
                    '<button class="vi-btn-primary" data-vi-submit disabled>Report issue</button></div>' +
            '</div>';
            document.body.appendChild(ov);
            escClosable(ov);
            var picked = { cat: null, priority: 'normal' };

            function paintOk() {
                var t = ov.querySelector('[data-vi-title]');
                ov.querySelector('[data-vi-submit]').disabled = !(picked.cat && t && t.value.trim());
            }
            ov.addEventListener('input', paintOk);
            ov.addEventListener('click', function (e) {
                if (e.target === ov || e.target.closest('[data-vi-close]')) { ov.remove(); return; }
                var cat = e.target.closest('[data-vi-cat]');
                if (cat) {
                    picked.cat = cat.getAttribute('data-vi-cat');
                    ov.querySelectorAll('[data-vi-cat]').forEach(function (b) {
                        b.classList.toggle('vi-cat--on', b === cat);
                    });
                    var t = ov.querySelector('[data-vi-title]');
                    if (t && !t.getAttribute('data-touched')) {
                        t.value = catLabel(picked.cat) + ': ' + (opts.name || '');
                    }
                    paintOk();
                    return;
                }
                var pr = e.target.closest('[data-vi-priority]');
                if (pr) {
                    picked.priority = pr.getAttribute('data-vi-priority');
                    ov.querySelectorAll('[data-vi-priority]').forEach(function (b) {
                        b.classList.toggle('vi-cat--on', b === pr);
                    });
                    return;
                }
                var submit = e.target.closest('[data-vi-submit]');
                if (submit) {
                    var t2 = ov.querySelector('[data-vi-title]');
                    var dsc = ov.querySelector('[data-vi-desc]');
                    var body = { entity_type: entityType, entity_id: entityId,
                        category: picked.cat, title: t2 ? t2.value.trim() : '',
                        description: dsc ? dsc.value : '' };
                    if (admin) body.priority = picked.priority;
                    submit.disabled = true;
                    jsend(API, body).then(function (r) {
                        if (r.ok && r.body.success) {
                            // one open report per item and problem: a repeat
                            // follows the first one instead of filing a twin
                            if (r.body.already) toast('You already reported this. It’s still open.', 'info');
                            else if (r.body.merged) toast('Someone already reported this. You’ll hear when it’s fixed.', 'info');
                            else toast('Issue reported', 'success');
                            ov.remove(); refreshBadge();
                            if (onIssuesPage()) loadPage();
                        } else {
                            submit.disabled = false;
                            toast((r.body && r.body.error) || 'Couldn’t report the issue', 'error');
                        }
                    });
                }
            });
            var ti = ov.querySelector('[data-vi-title]');
            if (ti) ti.addEventListener('keydown', function () { ti.setAttribute('data-touched', '1'); });
        });
    }

    // ── wiring ───────────────────────────────────────────────────────────────
    function init() {
        var st = $('[data-vi-filter-status]');
        if (st) st.addEventListener('change', function () { state.status = st.value; loadList(true); });
        var ct = $('[data-vi-filter-category]');
        if (ct) ct.addEventListener('change', function () { state.category = ct.value; loadList(true); });
        document.addEventListener('change', function (e) {
            var pick = e.target.closest && e.target.closest('[data-vi-pick]');
            if (!pick) return;
            var id = parseInt(pick.getAttribute('data-vi-pick'), 10);
            if (pick.checked) state.picked.add(id); else state.picked.delete(id);
            renderList();
            var again = document.querySelector('[data-vi-pick="' + id + '"]');
            if (again) again.focus();
        });
        document.addEventListener('keydown', function (e) {
            if (e.key !== 'Escape') return;
            var pop = document.querySelector('.vi-bulk-pop:not([hidden])');
            if (pop) { pop.hidden = true; var t = document.querySelector('[data-vi-bulk-prio]'); if (t) { t.setAttribute('aria-expanded', 'false'); t.focus(); } }
        });
        document.addEventListener('click', function (e) {
            if (e.target.closest('[data-vi-bulk]')) { onBulkClick(e); return; }
            var openPop = document.querySelector('.vi-bulk-pop:not([hidden])');
            if (openPop) { openPop.hidden = true; }
            if (e.target.closest('[data-vi-pick]')) return;
            var more = e.target.closest('[data-vi-more-btn]');
            if (more) { if (!state.loadingMore) loadList(false); return; }
            // the report entry on an expanded episode row (video-detail.js)
            var ep = e.target.closest('[data-vi-report-episode]');
            if (ep) {
                openReport({ entityType: 'episode', entityId: parseInt(ep.getAttribute('data-vi-report-episode'), 10),
                    name: ep.getAttribute('data-vi-name') || 'Episode', meta: ep.getAttribute('data-vi-meta') || '' });
                return;
            }
            var card = e.target.closest('[data-vi-open]');
            if (card) openDetail(parseInt(card.getAttribute('data-vi-open'), 10));
        });
        document.addEventListener('keydown', function (e) {
            if (e.key !== 'Enter' && e.key !== ' ') return;
            var card = e.target.closest && e.target.closest('[data-vi-open]');
            if (card) { e.preventDefault(); openDetail(parseInt(card.getAttribute('data-vi-open'), 10)); }
        });
        document.addEventListener('soulsync:video-page-shown', function (e) {
            if (e.detail === 'video-issues') loadPage();
            else refreshBadge();   // keep the nav badge honest wherever the user is
        });
        // a push about one of our issues (the shell re-dispatches the socket's
        // 'profile:notify'): refresh straight away instead of waiting for the poll
        window.addEventListener('soulsync:profile-notify', function (e) {
            var link = e && e.detail && e.detail.link;
            if (link && link !== 'video-issues') return;
            refreshBadge();
            if (onIssuesPage()) { loadStats(); loadList(true); }
        });
        setInterval(function () { if (!document.hidden) refreshBadge(); }, BADGE_REFRESH_MS);
        refreshBadge();
    }

    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init);
    else init();

    window.VideoIssues = { openReport: openReport, refreshBadge: refreshBadge };
})();
