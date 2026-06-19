/*
 * SoulSync — Video Downloads page.
 *
 * Every grab from the video side lands here. Polls /downloads/active while the page
 * is open and reflects live status. Cards are created ONCE and patched in place
 * (no innerHTML churn) so progress bars glide and nothing re-animates each tick —
 * the smooth, music-downloads feel.
 */
(function () {
    'use strict';

    var URL_ACTIVE = '/api/video/downloads/active';
    var URL_CLEAR = '/api/video/downloads/clear';
    var _timer = null, _wired = false;
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

    var KIND_ICON = { movie: '🎬', show: '📺', episode: '📺', season: '📺', series: '📺', youtube: '▶️' };
    var STATUS = {
        downloading: { label: 'Downloading', st: 'dl' },
        queued: { label: 'Queued', st: 'q' },
        completed: { label: 'Completed', st: 'ok' },
        failed: { label: 'Failed', st: 'fail' }
    };

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
            '</div>';
        return el;
    }

    function patchCard(el, d) {
        var info = STATUS[d.status] || STATUS.downloading;
        var active = d.status === 'downloading' || d.status === 'queued';
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
        if (rel.textContent !== relTxt) { rel.textContent = relTxt; rel.style.display = relTxt ? '' : 'none'; }

        var bar = q('bar');
        bar.style.display = active ? '' : 'none';
        if (active) q('fill').style.width = pct + '%';   // CSS transition glides it

        // meta line — patched only when its text changes (no churn)
        var meta = [fmtSize(d.size_bytes)];
        if (d.username) meta.push('👤 ' + d.username);
        if (active) meta.push(Math.round(pct) + '%');
        if (d.status === 'completed' && d.dest_path) meta.push('→ ' + d.dest_path);
        else if (d.status === 'failed' && d.error) meta.push(d.error);
        else if (d.created_at) meta.push(ago(d.created_at));
        var html = meta.map(function (m, i) {
            var cls = (i === 0) ? 'vdpg-m' : (d.status === 'completed' && /^→/.test(m)) ? 'vdpg-m vdpg-dest'
                : (d.status === 'failed' && m === d.error) ? 'vdpg-m vdpg-err' : 'vdpg-m';
            return '<span class="' + cls + '">' + esc(m) + '</span>';
        }).join('');
        var mt = q('meta'); if (mt.innerHTML !== html) mt.innerHTML = html;
    }

    function render(list) {
        var host = document.querySelector('[data-vdpg-list]'); if (!host) return;
        list = list || [];

        // header counts + clear button
        var finished = list.filter(function (d) { return d.status === 'completed' || d.status === 'failed'; }).length;
        var active = list.length - finished;
        var clearBtn = document.querySelector('[data-vdpg-clear]'); if (clearBtn) clearBtn.hidden = finished === 0;
        var sub = document.querySelector('[data-vdpg-sub]');
        if (sub) sub.textContent = list.length ? (active + ' active · ' + finished + ' finished')
            : "Everything you've grabbed from the video side";

        // empty state
        var empty = host.querySelector('.vdpg-empty');
        if (!list.length) {
            _cards = {};
            host.innerHTML = '<div class="vdpg-empty"><div class="vdpg-empty-ic">⤓</div>' +
                '<div class="vdpg-empty-t">No downloads yet</div>' +
                '<div class="vdpg-empty-s">Hit Grab on a search result and it\'ll show up here.</div></div>';
            return;
        }
        if (empty) empty.remove();

        // reconcile: create/patch in order, then drop stale — no full re-render
        var seen = {};
        list.forEach(function (d, i) {
            seen[d.id] = true;
            var el = _cards[d.id];
            if (!el) { el = _cards[d.id] = makeCard(d); }
            patchCard(el, d);
            var atPos = host.children[i];
            if (atPos !== el) host.insertBefore(el, atPos || null);   // place without re-animating
        });
        Object.keys(_cards).forEach(function (id) {
            if (!seen[id]) { var el = _cards[id]; if (el && el.parentNode) el.parentNode.removeChild(el); delete _cards[id]; }
        });
    }

    function anyActive() { return !!document.querySelector('.vdpg-item[data-st="dl"], .vdpg-item[data-st="q"]'); }

    function poll() {
        getJSON(URL_ACTIVE).then(function (d) {
            if (d) render(d.downloads || []);
            schedule();
        });
    }
    function schedule() {
        if (_timer) clearTimeout(_timer);
        // brisk while something's moving, relaxed when idle — and never a hard blink.
        _timer = setTimeout(poll, anyActive() ? 1500 : 6000);
    }
    function start() { wire(); if (_timer) clearTimeout(_timer); poll(); }
    function stop() { if (_timer) { clearTimeout(_timer); _timer = null; } }

    function wire() {
        if (_wired) return; _wired = true;
        var clearBtn = document.querySelector('[data-vdpg-clear]');
        if (clearBtn) clearBtn.addEventListener('click', function () {
            fetch(URL_CLEAR, { method: 'POST', headers: { Accept: 'application/json' } })
                .then(function () { toast('Cleared finished downloads', 'success'); poll(); })
                .catch(function () { /* ignore */ });
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
