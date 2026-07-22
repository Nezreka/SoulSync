/*
 * SoulSync — Video Requests page (arr-parity P4): the in-app Overseerr.
 *
 * Members file requests from preview detail pages; this page is where they
 * live. Admins see everyone's with Approve / Deny (deny takes an optional
 * note); members see their own with Withdraw on pending ones. Approval IS
 * acquisition — the backend adds the title to the wishlist/watchlist and the
 * drain/RSS take over. Self-contained IIFE; styled by .vreq-* in video-side.css.
 */
(function () {
    'use strict';

    var PAGE_ID = 'video-requests';
    var state = { loaded: false, rows: [] };

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

    var STATUS = { pending: ['Pending', 'vreq-st--pending'], approved: ['Approved', 'vreq-st--approved'],
                   denied: ['Denied', 'vreq-st--denied'] };

    function row(r) {
        var st = STATUS[r.status] || STATUS.pending;
        var poster = r.poster_url
            ? '<img class="vreq-poster" src="' + esc(r.poster_url) + '" alt="" loading="lazy" onerror="this.style.display=\'none\'">'
            : '<div class="vreq-poster vreq-poster--ph">' + (r.kind === 'movie' ? '🎬' : '📺') + '</div>';
        var sub = [r.kind === 'movie' ? 'Movie' : 'Show', r.year,
                   r.requester_name ? 'by ' + r.requester_name : null,
                   (r.created_at || '').slice(0, 10)].filter(Boolean).join(' · ');
        var actions = '';
        if (r.status === 'pending') {
            actions = isAdmin()
                ? '<button class="vreq-btn vreq-btn--ok" type="button" data-vreq-approve="' + r.id + '">Approve</button>' +
                  '<button class="vreq-btn vreq-btn--no" type="button" data-vreq-deny="' + r.id + '">Deny</button>'
                : '<button class="vreq-btn" type="button" data-vreq-withdraw="' + r.id + '">Withdraw</button>';
        }
        return '<div class="vreq-row" data-vreq-row="' + r.id + '">' + poster +
            '<div class="vreq-main">' +
                '<div class="vreq-title">' + esc(r.title) + ' <span class="vreq-st ' + st[1] + '">' + st[0] + '</span></div>' +
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

    function render() {
        var host = $('[data-vreq-list]');
        if (!host) return;
        host.innerHTML = state.rows.length
            ? state.rows.map(row).join('')
            : '<div class="vreq-empty">' + (isAdmin()
                ? 'No requests yet — members can ask for titles from any preview page.'
                : 'Nothing requested yet — hit Request on any movie or show you want added.') + '</div>';
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
                setBadge(d.pending || 0);
                render();
            })
            .catch(function () { /* keep last */ });
    }

    function act(url, body, okMsg) {
        return fetch(url, { method: body === null ? 'DELETE' : 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: body === null ? undefined : JSON.stringify(body || {}) })
            .then(function (r) { return r.json().catch(function () { return null; }).then(function (j) { return { ok: r.ok, j: j }; }); })
            .then(function (res) {
                if (!res.ok || !res.j || !res.j.success) {
                    toast((res.j && res.j.error) || 'Action failed', 'error');
                    return false;
                }
                if (okMsg) toast(okMsg, 'success');
                load();
                return true;
            })
            .catch(function () { toast('Action failed', 'error'); return false; });
    }

    function wire() {
        var host = $('[data-vreq-list]');
        if (!host || host._wired) return;
        host._wired = true;
        host.addEventListener('click', function (e) {
            var ap = e.target.closest('[data-vreq-approve]');
            if (ap) {
                ap.disabled = true;
                act('/api/video/requests/' + ap.getAttribute('data-vreq-approve') + '/approve', {},
                    'Approved — added to acquisition');
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
                act('/api/video/requests/' + id + '/deny',
                    { response: input ? input.value.trim() : '' }, 'Request denied');
                return;
            }
            var wd = e.target.closest('[data-vreq-withdraw]');
            if (wd) {
                wd.disabled = true;
                act('/api/video/requests/' + wd.getAttribute('data-vreq-withdraw'), null, 'Request withdrawn');
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
