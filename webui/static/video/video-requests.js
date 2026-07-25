/*
 * SoulSync — Video Requests page (arr-parity P4): the in-app Overseerr.
 *
 * Members file requests from preview detail pages; this page is where they
 * live. Admins see everyone's with Approve / Deny (deny takes an optional
 * note); members see their own with Withdraw on pending ones. Approval IS
 * acquisition — the backend adds the title to the wishlist/watchlist and the
 * drain/RSS take over. The page then keeps telling the story: an approved
 * request shows "Acquiring…" until the title exists in the library, where it
 * flips to "In library" (the backend annotates rows with in_library).
 * Status tabs filter the list, resolved rows can be removed one-by-one or
 * cleared in bulk. Self-contained IIFE; styled by .vreq-* in video-side.css.
 */
(function () {
    'use strict';

    var PAGE_ID = 'video-requests';
    var state = { loaded: false, rows: [], counts: null, tab: 'all' };

    function $(s) { return document.querySelector(s); }
    function esc(s) {
        return String(s == null ? '' : s).replace(/&/g, '&amp;').replace(/</g, '&lt;')
            .replace(/>/g, '&gt;').replace(/"/g, '&quot;');
    }
    function toast(m, t) { if (typeof showToast === 'function') showToast(m, t); }
    function isAdmin() {
        // currentProfile is a top-level `let` in init.js — it is NOT a window
        // property, so it must be read by bare name; reading it off window is
        // always undefined and rendered everyone, admins included, as a member.
        var cp = (typeof currentProfile !== 'undefined') ? currentProfile : null;
        return !!(cp && (cp.is_admin || cp.id === 1));
    }

    var TABS = [
        { id: 'all', label: 'All' },
        { id: 'pending', label: 'Pending' },
        { id: 'approved', label: 'Approved' },
        { id: 'denied', label: 'Denied' }
    ];

    // Status chip: the request's life keeps progressing after approval —
    // "Acquiring…" while the drain works, "In library" once the title exists.
    function statusChip(r) {
        if (r.status === 'approved') {
            return r.in_library
                ? '<span class="vreq-st vreq-st--lib">In library</span>'
                : '<span class="vreq-st vreq-st--approved">Approved</span>' +
                  '<span class="vreq-st vreq-st--acq">Acquiring…</span>';
        }
        if (r.status === 'denied') return '<span class="vreq-st vreq-st--denied">Denied</span>';
        return '<span class="vreq-st vreq-st--pending">Pending</span>';
    }

    function row(r) {
        var poster = r.poster_url
            ? '<img class="vreq-poster" src="' + esc(r.poster_url) + '" alt="" loading="lazy" onerror="this.style.display=\'none\'">'
            : '<div class="vreq-poster vreq-poster--ph">' + (r.kind === 'movie' ? '🎬' : '📺') + '</div>';
        var subBits = [r.kind === 'movie' ? 'Movie' : 'Show', r.year,
                       r.requester_name ? 'by ' + r.requester_name : null,
                       (r.created_at || '').slice(0, 10)];
        if (r.kind === 'show' && r.monitor && r.monitor !== 'future') {
            subBits.push('monitor: ' + r.monitor);
        }
        var sub = subBits.filter(Boolean).join(' · ');
        var actions = '';
        if (r.status === 'pending') {
            actions = isAdmin()
                ? '<button class="vreq-btn vreq-btn--ok" type="button" data-vreq-approve="' + r.id + '">Approve</button>' +
                  '<button class="vreq-btn vreq-btn--no" type="button" data-vreq-deny="' + r.id + '">Deny</button>'
                : '<button class="vreq-btn" type="button" data-vreq-withdraw="' + r.id + '">Withdraw</button>';
        } else {
            // Resolved rows are history — removable without ceremony. The
            // approval's wishlist/watchlist entry stays either way.
            actions = '<button class="vreq-btn vreq-btn--x" type="button" data-vreq-remove="' + r.id + '" title="Remove from history">&times;</button>';
        }
        return '<div class="vreq-row" data-vreq-row="' + r.id + '">' + poster +
            '<div class="vreq-main">' +
                '<div class="vreq-title">' + esc(r.title) + ' ' + statusChip(r) + '</div>' +
                '<div class="vreq-sub">' + esc(sub) + '</div>' +
                (r.note ? '<div class="vreq-note">“' + esc(r.note) + '”</div>' : '') +
                (r.admin_response ? '<div class="vreq-note vreq-note--admin">Admin: ' + esc(r.admin_response) + '</div>' : '') +
                '<div class="vreq-denybox hidden" data-vreq-denybox="' + r.id + '">' +
                    '<input type="text" class="vreq-denyin" data-vreq-denyin="' + r.id + '" placeholder="Reason (optional)">' +
                    '<button class="vreq-btn vreq-btn--no" type="button" data-vreq-deny-go="' + r.id + '">Confirm deny</button>' +
                '</div>' +
            '</div>' +
            '<div class="vreq-actions">' + actions + '</div>' +
        '</div>';
    }

    function visibleRows() {
        if (state.tab === 'all') return state.rows;
        return state.rows.filter(function (r) { return r.status === state.tab; });
    }

    function emptyText() {
        if (state.tab === 'pending' || (state.tab === 'all' && !state.rows.length)) {
            return isAdmin()
                ? 'No requests yet — members can ask for titles from any preview page.'
                : 'Nothing requested yet — hit Request on any movie or show you want added.';
        }
        if (state.tab === 'approved') return 'Nothing approved yet.';
        if (state.tab === 'denied') return 'Nothing denied.';
        return 'Nothing here.';
    }

    function renderToolbar() {
        var host = $('[data-vreq-toolbar]');
        if (!host) return;
        var c = state.counts || {};
        var total = (c.pending || 0) + (c.approved || 0) + (c.denied || 0);
        var countFor = { all: total, pending: c.pending || 0, approved: c.approved || 0, denied: c.denied || 0 };
        var tabs = TABS.map(function (t) {
            return '<button type="button" class="vreq-tab' + (state.tab === t.id ? ' active' : '') + '" data-vreq-tab="' + t.id + '">' +
                t.label + (countFor[t.id] ? ' <span class="vreq-tab-n">' + countFor[t.id] + '</span>' : '') +
            '</button>';
        }).join('');
        var resolved = (c.approved || 0) + (c.denied || 0);
        var clear = resolved
            ? '<button type="button" class="vreq-btn vreq-btn--clear" data-vreq-clear>Clear resolved</button>'
            : '';
        host.innerHTML = '<div class="vreq-tabs">' + tabs + '</div>' + clear;
    }

    function render() {
        renderToolbar();
        var host = $('[data-vreq-list]');
        if (!host) return;
        var rows = visibleRows();
        host.innerHTML = rows.length
            ? rows.map(row).join('')
            : '<div class="vreq-empty">' + emptyText() + '</div>';
    }

    function setBadge(n) {
        var b = $('[data-video-requests-badge]');
        if (!b) return;
        b.textContent = n;
        b.classList.toggle('hidden', !n);
    }

    function load() {
        state.loaded = true;
        fetch('/api/video/requests', { headers: { 'Accept': 'application/json' } })
            .then(function (r) { return r.ok ? r.json() : null; })
            .then(function (d) {
                if (!d || !d.success) return;
                state.rows = d.requests || [];
                state.counts = d.counts || null;
                setBadge(d.pending || 0);
                render();
            })
            .catch(function () { /* keep last */ });
    }

    function act(url, body, okMsg, failBtn) {
        return fetch(url, { method: body === null ? 'DELETE' : 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: body === null ? undefined : JSON.stringify(body || {}) })
            .then(function (r) { return r.json().catch(function () { return null; }).then(function (j) { return { ok: r.ok, j: j }; }); })
            .then(function (res) {
                if (!res.ok || !res.j || !res.j.success) {
                    toast((res.j && res.j.error) || 'Action failed', 'error');
                    if (failBtn) { failBtn.disabled = false; failBtn.textContent = failBtn.dataset.vreqLabel || failBtn.textContent; }
                    return null;
                }
                if (typeof okMsg === 'function') okMsg = okMsg(res.j);
                if (okMsg) toast(okMsg, 'success');
                load();
                return res.j;
            })
            .catch(function () {
                toast('Action failed', 'error');
                if (failBtn) { failBtn.disabled = false; failBtn.textContent = failBtn.dataset.vreqLabel || failBtn.textContent; }
                return null;
            });
    }

    function clearResolved() {
        var go = function () {
            act('/api/video/requests/resolved', null, function (j) {
                return 'Cleared ' + (j.removed || 0) + ' resolved request(s)';
            });
        };
        if (typeof showConfirmDialog === 'function') {
            showConfirmDialog({
                title: 'Clear Resolved Requests',
                message: 'Remove all approved and denied requests from the list? Approved titles stay in acquisition — this only clears the history.',
                confirmText: 'Clear',
                destructive: false
            }).then(function (yes) { if (yes) go(); });
        } else {
            go();
        }
    }

    function wire() {
        var page = $('.vreq-page');
        if (!page || page._wired) return;
        page._wired = true;
        page.addEventListener('click', function (e) {
            var tab = e.target.closest('[data-vreq-tab]');
            if (tab) {
                state.tab = tab.getAttribute('data-vreq-tab');
                render();
                return;
            }
            if (e.target.closest('[data-vreq-clear]')) {
                clearResolved();
                return;
            }
            var ap = e.target.closest('[data-vreq-approve]');
            if (ap) {
                ap.disabled = true;
                ap.dataset.vreqLabel = ap.textContent;
                ap.textContent = 'Approving…';
                act('/api/video/requests/' + ap.getAttribute('data-vreq-approve') + '/approve', {},
                    function (j) {
                        if (j.kind === 'movie') return 'Approved — movie added to the wishlist';
                        return 'Approved — following the show' +
                            (j.wished ? ', ' + j.wished + ' episode(s) wishlisted' : '');
                    }, ap);
                return;
            }
            var dn = e.target.closest('[data-vreq-deny]');
            if (dn) {   // reveal the inline reason box (no blocking prompt)
                var box = $('[data-vreq-denybox="' + dn.getAttribute('data-vreq-deny') + '"]');
                if (box) box.classList.toggle('hidden');
                return;
            }
            var go = e.target.closest('[data-vreq-deny-go]');
            if (go) {
                var id = go.getAttribute('data-vreq-deny-go');
                var input = $('[data-vreq-denyin="' + id + '"]');
                go.disabled = true;
                go.dataset.vreqLabel = go.textContent;
                act('/api/video/requests/' + id + '/deny',
                    { response: input ? input.value.trim() : '' }, 'Request denied', go);
                return;
            }
            var wd = e.target.closest('[data-vreq-withdraw]');
            if (wd) {
                wd.disabled = true;
                wd.dataset.vreqLabel = wd.textContent;
                act('/api/video/requests/' + wd.getAttribute('data-vreq-withdraw'), null, 'Request withdrawn', wd);
                return;
            }
            var rm = e.target.closest('[data-vreq-remove]');
            if (rm) {
                rm.disabled = true;
                act('/api/video/requests/' + rm.getAttribute('data-vreq-remove'), null, 'Removed', rm);
            }
        });
    }

    function pollBadge() {
        fetch('/api/video/requests/counts', { headers: { 'Accept': 'application/json' } })
            .then(function (r) { return r.ok ? r.json() : null; })
            .then(function (d) { if (d && d.success) setBadge(d.pending || 0); })
            .catch(function () { /* non-critical */ });
    }

    function onPageShown(e) {
        if (!e || e.detail !== PAGE_ID) return;
        wire();
        load();
    }

    function init() {
        document.addEventListener('soulsync:video-page-shown', onPageShown);
        // badge without visiting the page — same lazy cadence as the other navs
        setTimeout(pollBadge, 4000);
        setInterval(pollBadge, 60000);
    }

    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init);
    else init();
})();
