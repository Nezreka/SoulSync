/*
 * SoulSync — Video Downloads page.
 *
 * Every grab from the video side lands here. Filter tabs (All / Active / Completed /
 * Failed), per-row cancel + retry, cancel-all and clear-finished — the depth of the
 * music downloads page. Cards are created ONCE and patched in place (no innerHTML
 * churn) so progress bars glide and nothing re-animates each tick.
 */
(function () {
    'use strict';

    var URL_ACTIVE = '/api/video/downloads/active';
    var URL_CLEAR = '/api/video/downloads/clear';
    var URL_CANCEL = '/api/video/downloads/cancel';
    var URL_RETRY = '/api/video/downloads/retry';
    var _timer = null, _wired = false, _filter = 'all';
    var _cards = {};   // id -> card element (kept across polls for in-place updates)

    function esc(s) {
        return String(s == null ? '' : s)
            .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
    }
    function toast(m, t) { if (typeof showToast === 'function') showToast(m, t); }
    function getJSON(u) {
        return fetch(u, { headers: { Accept: 'application/json' } })
            .then(function (r) { return r.ok ? r.json() : null; }).catch(function () { return null; });
    }
    function postJSON(u, b) {
        return fetch(u, { method: 'POST', headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
            body: JSON.stringify(b || {}) }).then(function (r) { return r.ok ? r.json() : null; }).catch(function () { return null; });
    }

    var KIND_ICON = { movie: '🎬', show: '📺', episode: '📺', season: '📺', series: '📺', youtube: '▶️' };
    var STATUS = {
        downloading: { label: 'Downloading', st: 'dl' },
        queued: { label: 'Queued', st: 'q' },
        completed: { label: 'Completed', st: 'ok' },
        failed: { label: 'Failed', st: 'fail' },
        cancelled: { label: 'Cancelled', st: 'cancel' }
    };
    function isActive(s) { return s === 'downloading' || s === 'queued'; }
    function isFail(s) { return s === 'failed' || s === 'cancelled'; }
    function matches(s) {
        return _filter === 'all' || (_filter === 'active' && isActive(s)) ||
            (_filter === 'completed' && s === 'completed') || (_filter === 'failed' && isFail(s));
    }

    function fmtSize(bytes) {
        var gb = (bytes || 0) / (1024 * 1024 * 1024);
        return gb >= 0.1 ? (Math.round(gb * 10) / 10) + ' GB' : Math.round((bytes || 0) / (1024 * 1024)) + ' MB';
    }
    function ago(ts) {
        if (!ts) return '';
        var t = Date.parse(String(ts).replace(' ', 'T') + 'Z');
        if (isNaN(t)) return '';
        var s = Math.max(0, Math.round((Date.now() - t) / 1000));
        if (s < 60) return s + 's ago';
        if (s < 3600) return Math.round(s / 60) + 'm ago';
        if (s < 86400) return Math.round(s / 3600) + 'h ago';
        return Math.round(s / 86400) + 'd ago';
    }

    function makeCard(d) {
        var el = document.createElement('div');
        el.className = 'vdpg-item';
        el.setAttribute('data-dl-id', d.id);
        el.innerHTML =
            '<div class="vdpg-ic" data-f="ic"></div>' +
            '<div class="vdpg-body">' +
                '<div class="vdpg-row1"><span class="vdpg-name" data-f="name"></span>' +
                    '<span class="vdpg-pill" data-f="pill"></span></div>' +
                '<div class="vdpg-rel" data-f="rel"></div>' +
                '<div class="vdpg-bar" data-f="bar"><div class="vdpg-bar-fill" data-f="fill"></div></div>' +
                '<div class="vdpg-meta" data-f="meta"></div>' +
            '</div>' +
            '<div class="vdpg-actions" data-f="actions"></div>';
        return el;
    }

    function patchCard(el, d) {
        var info = STATUS[d.status] || STATUS.downloading;
        var active = isActive(d.status);
        var pct = Math.max(0, Math.min(100, d.progress || 0));
        var q = function (f) { return el.querySelector('[data-f="' + f + '"]'); };

        if (el.getAttribute('data-st') !== info.st) el.setAttribute('data-st', info.st);
        var ic = q('ic'); var icon = KIND_ICON[(d.kind || '').toLowerCase()] || '🎬';
        if (ic.textContent !== icon) ic.textContent = icon;
        var name = d.title || d.release_title || 'Download';
        var nm = q('name'); if (nm.textContent !== name) nm.textContent = name;
        var pill = q('pill');
        if (pill.textContent !== info.label) pill.textContent = info.label;
        if (pill.getAttribute('data-st') !== info.st) pill.setAttribute('data-st', info.st);

        var rel = q('rel');
        var relTxt = (d.release_title && d.release_title !== name) ? d.release_title : '';
        if (rel.textContent !== relTxt) rel.textContent = relTxt;

        q('bar').style.display = active ? '' : 'none';
        if (active) q('fill').style.width = pct + '%';

        var meta = [fmtSize(d.size_bytes)];
        if (d.username) meta.push('👤 ' + d.username);
        if (active) meta.push(Math.round(pct) + '%');
        if (d.status === 'completed' && d.dest_path) meta.push('→ ' + d.dest_path);
        else if (isFail(d.status) && d.error) meta.push(d.error);
        else if (d.created_at) meta.push(ago(d.created_at));
        var html = meta.map(function (m, i) {
            var cls = (i === 0) ? 'vdpg-m' : (d.status === 'completed' && /^→/.test(m)) ? 'vdpg-m vdpg-dest'
                : (isFail(d.status) && m === d.error) ? 'vdpg-m vdpg-err' : 'vdpg-m';
            return '<span class="' + cls + '">' + esc(m) + '</span>';
        }).join('');
        var mt = q('meta'); if (mt.innerHTML !== html) mt.innerHTML = html;

        var act = q('actions');
        var actHTML = active
            ? '<button class="vdpg-act vdpg-act--cancel" type="button" data-vdpg-cancel="' + d.id + '" title="Cancel">✕</button>'
            : isFail(d.status)
                ? '<button class="vdpg-act vdpg-act--retry" type="button" data-vdpg-retry="' + d.id + '" title="Retry">↻</button>'
                : '';
        if (act.innerHTML !== actHTML) act.innerHTML = actHTML;
    }

    function render(list) {
        var host = document.querySelector('[data-vdpg-list]'); if (!host) return;
        list = list || [];

        var counts = { all: list.length, active: 0, completed: 0, failed: 0 };
        list.forEach(function (d) {
            if (isActive(d.status)) counts.active++;
            else if (d.status === 'completed') counts.completed++;
            else counts.failed++;
        });
        ['all', 'active', 'completed', 'failed'].forEach(function (k) {
            var n = document.querySelector('[data-vdpg-n="' + k + '"]'); if (n) n.textContent = counts[k];
        });
        var cancelAll = document.querySelector('[data-vdpg-cancel-all]'); if (cancelAll) cancelAll.hidden = counts.active === 0;
        var clearBtn = document.querySelector('[data-vdpg-clear]'); if (clearBtn) clearBtn.hidden = (counts.completed + counts.failed) === 0;
        var sub = document.querySelector('[data-vdpg-sub]');
        if (sub) sub.textContent = list.length ? (counts.active + ' active · ' + counts.completed + ' done · ' + counts.failed + ' failed')
            : "Everything you've grabbed from the video side";

        if (!list.length) {
            _cards = {};
            host.innerHTML = '<div class="vdpg-empty"><div class="vdpg-empty-ic">⤓</div>' +
                '<div class="vdpg-empty-t">No downloads yet</div>' +
                '<div class="vdpg-empty-s">Hit Grab on a search result and it\'ll show up here.</div></div>';
            return;
        }
        var empty = host.querySelector('.vdpg-empty'); if (empty) empty.remove();

        var seen = {}, shown = 0;
        list.forEach(function (d, i) {
            seen[d.id] = true;
            var el = _cards[d.id] || (_cards[d.id] = makeCard(d));
            patchCard(el, d);
            var vis = matches(d.status);
            el.style.display = vis ? '' : 'none';
            if (vis) shown++;
            var atPos = host.children[i];
            if (atPos !== el) host.insertBefore(el, atPos || null);
        });
        Object.keys(_cards).forEach(function (id) {
            if (!seen[id]) { var el = _cards[id]; if (el && el.parentNode) el.parentNode.removeChild(el); delete _cards[id]; }
        });

        var fe = host.querySelector('.vdpg-filter-empty');
        if (shown === 0) {
            if (!fe) { fe = document.createElement('div'); fe.className = 'vdpg-filter-empty'; host.appendChild(fe); }
            fe.textContent = 'Nothing ' + (_filter === 'all' ? 'here' : _filter) + ' right now.';
        } else if (fe) { fe.remove(); }
    }

    function setFilter(f) {
        _filter = f;
        Array.prototype.forEach.call(document.querySelectorAll('[data-vdpg-filter]'), function (b) {
            b.classList.toggle('vdpg-pill-f--on', b.getAttribute('data-vdpg-filter') === f);
        });
        getJSON(URL_ACTIVE).then(function (d) { if (d) render(d.downloads || []); });
    }

    function anyActive() { return !!document.querySelector('.vdpg-item[data-st="dl"], .vdpg-item[data-st="q"]'); }

    function poll() {
        getJSON(URL_ACTIVE).then(function (d) { if (d) render(d.downloads || []); schedule(); });
    }
    function schedule() {
        if (_timer) clearTimeout(_timer);
        _timer = setTimeout(poll, anyActive() ? 1500 : 6000);
    }
    function start() { wire(); if (_timer) clearTimeout(_timer); poll(); }
    function stop() { if (_timer) { clearTimeout(_timer); _timer = null; } }

    function wire() {
        if (_wired) return; _wired = true;
        var clearBtn = document.querySelector('[data-vdpg-clear]');
        if (clearBtn) clearBtn.addEventListener('click', function () {
            postJSON(URL_CLEAR, {}).then(function () { toast('Cleared finished downloads', 'success'); poll(); });
        });
        var cancelAll = document.querySelector('[data-vdpg-cancel-all]');
        if (cancelAll) cancelAll.addEventListener('click', function () {
            getJSON(URL_ACTIVE).then(function (d) {
                var ids = ((d && d.downloads) || []).filter(function (x) { return isActive(x.status); }).map(function (x) { return x.id; });
                Promise.all(ids.map(function (id) { return postJSON(URL_CANCEL, { id: id }); }))
                    .then(function () { toast('Cancelled ' + ids.length + ' download' + (ids.length === 1 ? '' : 's'), 'info'); poll(); });
            });
        });
        var pills = document.querySelector('[data-vdpg-pills]');
        if (pills) pills.addEventListener('click', function (e) {
            var b = e.target.closest('[data-vdpg-filter]'); if (b) setFilter(b.getAttribute('data-vdpg-filter'));
        });
        var list = document.querySelector('[data-vdpg-list]');
        if (list) list.addEventListener('click', function (e) {
            var c = e.target.closest('[data-vdpg-cancel]');
            if (c) { c.disabled = true; postJSON(URL_CANCEL, { id: +c.getAttribute('data-vdpg-cancel') }).then(function () { poll(); }); return; }
            var r = e.target.closest('[data-vdpg-retry]');
            if (r) { r.disabled = true; postJSON(URL_RETRY, { id: +r.getAttribute('data-vdpg-retry') }).then(function (res) {
                if (res && res.ok) toast('Retrying', 'info'); else toast((res && res.error) || 'Retry failed', 'error'); poll(); }); }
        });
    }

    document.addEventListener('soulsync:video-page-shown', function (e) {
        if (e.detail === 'video-downloads') start(); else stop();
    });
    document.addEventListener('soulsync:video-download-started', function () {
        if (document.querySelector('[data-video-subpage="video-downloads"]:not([hidden])')) setTimeout(poll, 350);
    });
    window._vdpgAnyActive = anyActive;
})();
