/*
 * SoulSync — Video Issues (the music Issues standard, video-scoped).
 *
 * Page: status/category filters + stat cards + issue cards; clicking a card
 * opens the detail modal (snapshot hero, info bar, description, admin response,
 * lifecycle actions). Reporting: window.VideoIssues.openReport({entityType,
 * entityId, name, meta}) — wired from the Manage sidebar. The nav badge shows
 * the OPEN count. Admin = profile 1 (the video side's convention); non-admins
 * see and withdraw only their own reports.
 */
(function () {
    'use strict';

    var API = '/api/video/issues';
    var state = { status: 'open', category: 'all', categories: [], issues: [] };

    function $(sel) { return document.querySelector(sel); }
    function esc(s) {
        return String(s == null ? '' : s)
            .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
    }
    function toast(msg, type) { if (typeof showToast === 'function') showToast(msg, type); }
    function confirmDlg(o) {
        return (typeof showConfirmDialog === 'function') ? showConfirmDialog(o) : Promise.resolve(true);
    }
    function isAdmin() {
        return !(typeof currentProfile !== 'undefined' && currentProfile && !currentProfile.is_admin);
    }
    function jget(url) { return fetch(url).then(function (r) { return r.ok ? r.json() : null; }); }
    function jsend(url, body, method) {
        return fetch(url, { method: method || 'POST', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(body || {}) })
            .then(function (r) { return r.json().then(function (b) { return { ok: r.ok, body: b }; }); });
    }

    var STATUS_META = {
        open: ['Open', 'vi-st--open'], in_progress: ['In Progress', 'vi-st--progress'],
        resolved: ['Resolved', 'vi-st--resolved'], dismissed: ['Dismissed', 'vi-st--dismissed'],
    };
    var CATEGORY_ICONS = {
        wrong_match: '🎯', wrong_metadata: '📝', wrong_poster: '🖼', bad_quality: '📉',
        audio_issue: '🔇', subtitle_issue: '💬', playback_issue: '⏯', missing_content: '🧩',
        duplicate: '👯', other: '⚑',
    };

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

    // ── badge (OPEN count, like music's #issues-nav-badge) ───────────────────
    function refreshBadge() {
        jget(API + '/counts').then(function (d) {
            var b = document.getElementById('video-issues-nav-badge');
            if (!b || !d || !d.counts) return;
            var n = d.counts.open || 0;
            b.textContent = n;
            b.classList.toggle('hidden', n === 0);
        });
    }

    // ── page ─────────────────────────────────────────────────────────────────
    function loadPage() {
        var sub = $('[data-vi-subtitle]');
        if (sub) {
            sub.textContent = isAdmin() ? 'Problems reported on library items — triage and resolve'
                : 'Your reported problems and their status';
        }
        loadCategories().then(loadStats).then(loadList);
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

    function loadList() {
        var host = $('[data-vi-list]');
        if (!host) return;
        var q = new URLSearchParams();
        if (state.status !== 'all') q.set('status', state.status);
        if (state.category !== 'all') q.set('category', state.category);
        jget(API + '?' + q.toString()).then(function (d) {
            if (!d || !d.success) { host.innerHTML = '<div class="repair-empty">Couldn’t load issues</div>'; return; }
            state.issues = d.issues || [];
            if (!state.issues.length) {
                host.innerHTML = '<div class="repair-empty-state">' +
                    '<div class="repair-empty-icon">✅</div>' +
                    '<div class="repair-empty-title">No issues here</div>' +
                    '<div class="repair-empty-text">' + (state.status === 'open'
                        ? 'Nothing open — reports land here when someone flags a problem on an item.'
                        : 'Nothing matches these filters.') + '</div></div>';
                return;
            }
            host.innerHTML = state.issues.map(cardHTML).join('');
        });
        refreshBadge();
    }

    function cardHTML(i) {
        var snap = i.snapshot_data || {};
        var st = STATUS_META[i.status] || [i.status, ''];
        var thumb = snap.poster
            ? '<img class="vi-card-thumb" src="' + esc(snap.poster) + '" alt="" loading="lazy" ' +
              'onerror="this.outerHTML=\'<div class=&quot;vi-card-thumb vi-card-thumb--ph&quot;>' +
              (CATEGORY_ICONS[i.category] || '⚑') + '</div>\'">'
            : '<div class="vi-card-thumb vi-card-thumb--ph">' + (CATEGORY_ICONS[i.category] || '⚑') + '</div>';
        var entity = snap.title || snap.show_title || ('item #' + i.entity_id);
        if (i.entity_type === 'episode' && snap.code) entity = (snap.show_title || '') + ' ' + snap.code;
        return '<div class="vi-card" data-vi-open="' + i.id + '">' + thumb +
            '<div class="vi-card-mid">' +
                '<div class="vi-card-title">' + (CATEGORY_ICONS[i.category] || '⚑') + ' ' +
                    esc(i.title) + (i.admin_response ? ' <span title="Has an admin response">💬</span>' : '') + '</div>' +
                '<div class="vi-card-entity">' + esc(i.entity_type) + ' · ' + esc(entity) +
                    (snap.year ? ' (' + snap.year + ')' : '') + '</div>' +
                (i.description ? '<div class="vi-card-desc">' + esc(i.description) + '</div>' : '') +
                '<div class="vi-card-foot">' + esc(String(i.created_at || '').slice(0, 16)) +
                    (isAdmin() && i.reporter_name ? ' · by ' + esc(i.reporter_name) : '') + '</div>' +
            '</div>' +
            '<div class="vi-card-right"><span class="vi-st ' + st[1] + '">' + st[0] + '</span>' +
                '<span class="vi-prio vi-prio--' + esc(i.priority) + '" title="' + esc(i.priority) + '"></span></div>' +
        '</div>';
    }

    // ── detail modal ─────────────────────────────────────────────────────────
    function openDetail(id) {
        jget(API + '/' + id).then(function (d) {
            if (!d || !d.success) { toast('Couldn’t load the issue', 'error'); return; }
            var i = d.issue, snap = i.snapshot_data || {};
            var st = STATUS_META[i.status] || [i.status, ''];
            var hero = (snap.poster
                ? '<img class="vi-hero-art" src="' + esc(snap.poster) + '" alt="" onerror="this.style.visibility=\'hidden\'">'
                : '<div class="vi-hero-art vi-card-thumb--ph">' + (CATEGORY_ICONS[i.category] || '⚑') + '</div>') +
                '<div class="vi-hero-info">' +
                    '<div class="vi-hero-title">' + esc(snap.title || snap.show_title || 'Item #' + i.entity_id) +
                        (snap.year ? ' <span class="vi-hero-year">(' + snap.year + ')</span>' : '') + '</div>' +
                    '<div class="vi-hero-sub">' + esc([i.entity_type, snap.code, snap.network,
                        snap.content_rating].filter(Boolean).join(' · ')) + '</div>' +
                    ((snap.genres || []).length ? '<div class="vi-hero-tags">' + snap.genres.slice(0, 5)
                        .map(function (g) { return '<span class="vi-tag">' + esc(g) + '</span>'; }).join('') + '</div>' : '') +
                '</div>';
            var files = (snap.files || []).map(function (f) {
                return '<div class="vi-file">' + esc([f.resolution, f.video_codec,
                    f.size_bytes ? (f.size_bytes / 1073741824).toFixed(1) + ' GB' : null]
                    .filter(Boolean).join(' · ')) + '</div>';
            }).join('');
            var admin = isAdmin();
            var response = admin
                ? '<textarea class="vi-response" data-vi-response placeholder="Admin response — the reporter sees this…">' +
                  esc(i.admin_response || '') + '</textarea>'
                : (i.admin_response ? '<div class="vi-response-ro">' + esc(i.admin_response) + '</div>' : '');
            var actions = [];
            if (admin) {
                if (i.status === 'open') actions.push('<button class="vmg-btn-ghost" data-vi-act="in_progress">Mark In Progress</button>');
                if (i.status === 'open' || i.status === 'in_progress') {
                    actions.push('<button class="vi-btn-primary" data-vi-act="resolved">Resolve</button>');
                    actions.push('<button class="vmg-btn-ghost" data-vi-act="dismissed">Dismiss</button>');
                }
                if (i.status === 'resolved' || i.status === 'dismissed') {
                    actions.push('<button class="vmg-btn-ghost" data-vi-act="open">Reopen</button>');
                }
                actions.push('<button class="vmg-btn-ghost vi-btn-danger" data-vi-del>Delete</button>');
            } else if (i.status === 'open') {
                actions.push('<button class="vmg-btn-ghost vi-btn-danger" data-vi-del>Withdraw</button>');
            }
            var ov = document.createElement('div');
            ov.className = 'vi-overlay';
            ov.innerHTML = '<div class="vi-modal" role="dialog" aria-modal="true">' +
                '<div class="vi-modal-head">Issue #' + i.id +
                    '<button class="vmg-close" data-vi-close aria-label="Close">×</button></div>' +
                '<div class="vi-modal-body">' +
                    '<div class="vi-hero">' + hero + '</div>' +
                    '<div class="vi-infobar"><span class="vi-prio vi-prio--' + esc(i.priority) + '"></span>' +
                        '<span class="vi-st ' + st[1] + '">' + st[0] + '</span>' +
                        '<span class="vi-infocat">' + esc(catLabel(i.category)) + '</span>' +
                        '<span class="vi-infodates">Reported ' + esc(String(i.created_at || '').slice(0, 10)) +
                            (i.resolved_at ? ' · Resolved ' + esc(String(i.resolved_at).slice(0, 10)) : '') +
                            (admin && i.reporter_name ? ' · by ' + esc(i.reporter_name) : '') + '</span></div>' +
                    '<div class="vi-sect"><div class="vi-sect-h">Issue</div>' +
                        '<div class="vi-issue-title">' + esc(i.title) + '</div>' +
                        (i.description ? '<p class="vi-issue-desc">' + esc(i.description) + '</p>' : '') + '</div>' +
                    (files ? '<div class="vi-sect"><div class="vi-sect-h">Files</div>' + files + '</div>' : '') +
                    (response ? '<div class="vi-sect"><div class="vi-sect-h">Admin response</div>' + response + '</div>' : '') +
                '</div>' +
                '<div class="vi-modal-foot">' +
                    (i.entity_type !== 'episode'
                        ? '<button class="vmg-btn-ghost" data-vi-view>View item →</button>' : '') +
                    '<span class="vbb-spacer"></span>' + actions.join('') + '</div>' +
            '</div>';
            document.body.appendChild(ov);
            ov.addEventListener('click', function (e) {
                if (e.target === ov || e.target.closest('[data-vi-close]')) { ov.remove(); return; }
                if (e.target.closest('[data-vi-view]')) {
                    ov.remove();
                    document.dispatchEvent(new CustomEvent('soulsync:video-open-detail', {
                        detail: { kind: i.entity_type, id: parseInt(i.entity_id, 10), source: 'library' } }));
                    return;
                }
                var act = e.target.closest('[data-vi-act]');
                if (act) {
                    var ta = ov.querySelector('[data-vi-response]');
                    jsend(API + '/' + i.id, { status: act.getAttribute('data-vi-act'),
                        admin_response: ta ? ta.value : undefined }, 'PUT')
                        .then(function (r) {
                            if (r.ok && r.body.success) { toast('Issue updated', 'success'); ov.remove(); loadPage(); }
                            else toast('Update failed', 'error');
                        });
                    return;
                }
                if (e.target.closest('[data-vi-del]')) {
                    confirmDlg({ title: admin ? 'Delete this issue?' : 'Withdraw this issue?',
                        message: admin ? 'The report is removed permanently.'
                            : 'Your report is removed permanently.',
                        confirmText: admin ? 'Delete' : 'Withdraw', destructive: true })
                        .then(function (yes) {
                            if (!yes) return;
                            jsend(API + '/' + i.id, null, 'DELETE').then(function (r) {
                                if (r.ok && r.body.success) { toast('Issue removed', 'info'); ov.remove(); loadPage(); }
                                else toast('Couldn’t remove the issue', 'error');
                            });
                        });
                }
            });
        });
    }

    // ── report modal (window.VideoIssues.openReport) ─────────────────────────
    function openReport(opts) {
        opts = opts || {};
        var entityType = opts.entityType, entityId = opts.entityId;
        if (!entityType || entityId == null) return;
        loadCategories().then(function () {
            var cats = state.categories.filter(function (c) {
                return c.applies.indexOf(entityType) !== -1;
            });
            var ov = document.createElement('div');
            ov.className = 'vi-overlay';
            ov.innerHTML = '<div class="vi-modal" role="dialog" aria-modal="true">' +
                '<div class="vi-modal-head">Report an issue' +
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
                        '<input class="vmg-input" data-vi-title maxlength="200"></div>' +
                    '<div class="vi-sect"><div class="vi-sect-h">Details</div>' +
                        '<textarea class="vi-response" data-vi-desc maxlength="2000" ' +
                        'placeholder="Anything that helps pin it down…"></textarea></div>' +
                    '<div class="vi-sect"><div class="vi-sect-h">Priority</div>' +
                        '<div class="vi-prios">' + ['low', 'normal', 'high'].map(function (p) {
                            return '<button class="vi-cat vi-prio-btn' + (p === 'normal' ? ' vi-cat--on' : '') +
                                '" type="button" data-vi-priority="' + p + '">' + p + '</button>';
                        }).join('') + '</div></div>' +
                '</div>' +
                '<div class="vi-modal-foot"><span class="vbb-spacer"></span>' +
                    '<button class="vi-btn-primary" data-vi-submit disabled>Report issue</button></div>' +
            '</div>';
            document.body.appendChild(ov);
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
                if (e.target.closest('[data-vi-submit]')) {
                    var t2 = ov.querySelector('[data-vi-title]');
                    var dsc = ov.querySelector('[data-vi-desc]');
                    jsend(API, { entity_type: entityType, entity_id: entityId,
                        category: picked.cat, title: t2 ? t2.value.trim() : '',
                        description: dsc ? dsc.value : '', priority: picked.priority })
                        .then(function (r) {
                            if (r.ok && r.body.success) {
                                toast('Issue reported successfully', 'success');
                                ov.remove(); refreshBadge();
                            } else {
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
        if (st) st.addEventListener('change', function () { state.status = st.value; loadList(); });
        var ct = $('[data-vi-filter-category]');
        if (ct) ct.addEventListener('change', function () { state.category = ct.value; loadList(); });
        document.addEventListener('click', function (e) {
            var card = e.target.closest('[data-vi-open]');
            if (card) openDetail(parseInt(card.getAttribute('data-vi-open'), 10));
        });
        document.addEventListener('soulsync:video-page-shown', function (e) {
            if (e.detail === 'video-issues') loadPage();
            else refreshBadge();   // keep the nav badge honest wherever the user is
        });
        refreshBadge();
    }

    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init);
    else init();

    window.VideoIssues = { openReport: openReport, refreshBadge: refreshBadge };
})();
