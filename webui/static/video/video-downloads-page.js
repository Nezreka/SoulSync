/*
 * SoulSync — Video Downloads page.
 *
 * Reuses the music downloads page's .adl-* layout + look (full-width, segmented
 * filter pills, compact rows, status dots) for visual parity, driven by video data
 * via data-vdpg-* hooks. Filter tabs, per-row cancel + retry, cancel-all, clear.
 * Rows are created ONCE and patched in place so progress glides and nothing blinks.
 */
(function () {
    'use strict';

    var URL_ACTIVE = '/api/video/downloads/active';
    var URL_CLEAR = '/api/video/downloads/clear';
    var URL_CANCEL = '/api/video/downloads/cancel';
    var URL_RETRY = '/api/video/downloads/retry';
    var _timer = null, _wired = false, _filter = 'all';
    var _cards = {};

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
    // status -> { label, cls } where cls is the music .adl-row-/.adl-status-dot class
    var STATUS = {
        downloading: { label: 'Downloading', cls: 'active' },
        queued: { label: 'Queued', cls: 'queued' },
        completed: { label: 'Completed', cls: 'completed' },
        failed: { label: 'Failed', cls: 'failed' },
        cancelled: { label: 'Cancelled', cls: 'cancelled' }
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

    var X_SVG = '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>';
    var R_SVG = '<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><polyline points="23 4 23 10 17 10"/><path d="M20.49 15a9 9 0 1 1-2.12-9.36L23 10"/></svg>';
    var OPEN_SVG = '<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.1" stroke-linecap="round" stroke-linejoin="round"><path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6"/><polyline points="15 3 21 3 21 9"/><line x1="10" y1="14" x2="21" y2="3"/></svg>';

    function makeCard(d) {
        var el = document.createElement('div');
        el.className = 'adl-row';
        el.setAttribute('data-dl-id', d.id);
        el.innerHTML =
            '<div class="adl-row-art adl-row-art-empty vdpg-art" data-f="ic"></div>' +
            '<div class="adl-row-info">' +
                '<div class="adl-row-title" data-f="name"></div>' +
                '<div class="adl-row-meta" data-f="meta"></div>' +
                '<div class="adl-row-error" data-f="error" style="display:none"></div>' +
                '<div class="vdpg-prog" data-f="bar" style="display:none"><div class="vdpg-prog-fill" data-f="fill"></div></div>' +
            '</div>' +
            '<div class="adl-row-status" data-f="status"><span class="adl-status-dot" data-f="dot"></span><span data-f="label"></span></div>' +
            '<div class="vdpg-rowact" data-f="actions"></div>';
        return el;
    }

    function patchCard(el, d) {
        var info = STATUS[d.status] || STATUS.downloading;
        var cls = info.cls, active = isActive(d.status);
        var pct = Math.max(0, Math.min(100, d.progress || 0));
        var q = function (f) { return el.querySelector('[data-f="' + f + '"]'); };

        var want = 'adl-row adl-row-' + cls;
        if (el.className !== want) el.className = want;

        // poster art tile (falls back to the kind emoji)
        var ic = q('ic');
        if (d.poster_url) {
            if (ic._p !== d.poster_url) { ic._p = d.poster_url; ic.style.backgroundImage = "url('" + d.poster_url + "')"; }
            ic.classList.add('vdpg-has-poster'); ic.textContent = '';
        } else {
            ic.classList.remove('vdpg-has-poster'); if (ic._p) { ic.style.backgroundImage = ''; ic._p = null; }
            var icon = KIND_ICON[(d.kind || '').toLowerCase()] || '🎬';
            if (ic.textContent !== icon) ic.textContent = icon;
        }

        var name = (d.title || d.release_title || 'Download') + (d.year ? '  (' + d.year + ')' : '');
        var nm = q('name'); if (nm.textContent !== name) nm.textContent = name;

        // meta: quality chip + a context line (release / size·user·pct / dest)
        var ctx;
        if (d.status === 'completed' && d.dest_path) ctx = '→ ' + d.dest_path;
        else if (active) ctx = [fmtSize(d.size_bytes), d.username ? ('👤 ' + d.username) : '', Math.round(pct) + '%'].filter(Boolean).join('  ·  ');
        else ctx = (d.release_title && d.release_title !== (d.title || '')) ? d.release_title : fmtSize(d.size_bytes);
        var chip = d.quality_label ? '<span class="vdpg-qchip">' + esc(d.quality_label) + '</span>' : '';
        var metaHTML = chip + '<span class="vdpg-mctx' + (d.status === 'completed' && d.dest_path ? ' vdpg-dest' : '') + '">' + esc(ctx) + '</span>';
        var mt = q('meta'); if (mt.innerHTML !== metaHTML) mt.innerHTML = metaHTML;

        var err = q('error');
        var errTxt = isFail(d.status) && d.error ? d.error : '';
        if (err.textContent !== errTxt) err.textContent = errTxt;
        err.style.display = errTxt ? '' : 'none';

        var bar = q('bar');
        bar.style.display = active ? '' : 'none';
        if (active) q('fill').style.width = pct + '%';

        var st = q('status'); var stWant = 'adl-row-status ' + cls;
        if (st.className !== stWant) st.className = stWant;
        var dot = q('dot'); var dotWant = 'adl-status-dot ' + cls;
        if (dot.className !== dotWant) dot.className = dotWant;
        var lab = q('label'); if (lab.textContent !== info.label) lab.textContent = info.label;

        var act = q('actions');
        var openBtn = d.media_id ? '<button class="vdpg-open" type="button" data-vdpg-open="' + esc(d.media_id) +
            '" data-kind="' + esc(d.kind || 'movie') + '" data-source="' + esc(d.media_source || 'library') +
            '" title="Open ' + (d.kind === 'movie' ? 'movie' : 'show') + ' page">' + OPEN_SVG + '</button>' : '';
        var stateBtn = active
            ? '<button class="adl-row-cancel" type="button" data-vdpg-cancel="' + d.id + '" title="Cancel">' + X_SVG + '</button>'
            : isFail(d.status)
                ? '<button class="vdpg-row-retry" type="button" data-vdpg-retry="' + d.id + '" title="Retry">' + R_SVG + '</button>'
                : '';
        var actHTML = openBtn + stateBtn;
        if (act.innerHTML !== actHTML) act.innerHTML = actHTML;
    }

    function render(list) {
        var host = document.querySelector('[data-vdpg-list]'); if (!host) return;
        list = list || [];
        var empty = host.querySelector('[data-vdpg-empty]');

        var counts = { all: list.length, active: 0, completed: 0, failed: 0 };
        list.forEach(function (d) {
            if (isActive(d.status)) counts.active++;
            else if (d.status === 'completed') counts.completed++;
            else counts.failed++;
        });
        var cancelAll = document.querySelector('[data-vdpg-cancel-all]'); if (cancelAll) cancelAll.style.display = counts.active ? '' : 'none';
        var clearBtn = document.querySelector('[data-vdpg-clear]'); if (clearBtn) clearBtn.style.display = (counts.completed + counts.failed) ? '' : 'none';
        var sub = document.querySelector('[data-vdpg-sub]');
        if (sub) {
            var parts = [];
            if (counts.active) parts.push(counts.active + ' active');
            if (counts.completed) parts.push(counts.completed + ' done');
            if (counts.failed) parts.push(counts.failed + ' failed');
            sub.textContent = parts.join('  ·  ');
        }

        var seen = {}, shown = 0;
        list.forEach(function (d) {
            seen[d.id] = true;
            var el = _cards[d.id] || (_cards[d.id] = makeCard(d));
            patchCard(el, d);
            var vis = matches(d.status);
            el.style.display = vis ? '' : 'none';
            if (vis) shown++;
            host.appendChild(el);   // keep order = server order (active first); no re-anim
        });
        Object.keys(_cards).forEach(function (id) {
            if (!seen[id]) { var e = _cards[id]; if (e && e.parentNode) e.parentNode.removeChild(e); delete _cards[id]; }
        });

        if (empty) {
            host.appendChild(empty);   // keep the empty element last
            empty.style.display = shown === 0 ? '' : 'none';
            empty.textContent = !list.length ? "No downloads yet. Hit Grab on a search result and it'll show up here."
                : 'Nothing ' + (_filter === 'all' ? 'here' : _filter) + ' right now.';
        }
    }

    function setFilter(f) {
        _filter = f;
        Array.prototype.forEach.call(document.querySelectorAll('[data-vdpg-filter]'), function (b) {
            b.classList.toggle('active', b.getAttribute('data-vdpg-filter') === f);
        });
        getJSON(URL_ACTIVE).then(function (d) { if (d) render(d.downloads || []); });
    }

    function anyActive() { return !!document.querySelector('.adl-row.adl-row-active, .adl-row.adl-row-queued'); }

    function poll() { getJSON(URL_ACTIVE).then(function (d) { if (d) render(d.downloads || []); schedule(); }); }
    function schedule() { if (_timer) clearTimeout(_timer); _timer = setTimeout(poll, anyActive() ? 1500 : 6000); }
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
            var op = e.target.closest('[data-vdpg-open]');
            if (op) {
                var kind = op.getAttribute('data-kind') === 'movie' ? 'movie' : 'show';
                var id = op.getAttribute('data-vdpg-open');
                document.dispatchEvent(new CustomEvent('soulsync:video-open-detail', {
                    detail: { kind: kind, id: parseInt(id, 10) || id, source: op.getAttribute('data-source') || 'library' }
                }));
                return;
            }
            var c = e.target.closest('[data-vdpg-cancel]');
            if (c) { c.disabled = true; c.classList.add('adl-row-cancel-pending'); postJSON(URL_CANCEL, { id: +c.getAttribute('data-vdpg-cancel') }).then(function () { poll(); }); return; }
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
