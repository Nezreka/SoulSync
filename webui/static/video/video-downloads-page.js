/*
 * SoulSync — Video Downloads page.
 *
 * Every grab from the video side lands here. Polls /downloads/active while the page
 * is open and renders live status: a progress bar while downloading, then the file's
 * final library destination when it completes. Self-contained; isolated .vdpg-* styling.
 */
(function () {
    'use strict';

    var URL_ACTIVE = '/api/video/downloads/active';
    var URL_CLEAR = '/api/video/downloads/clear';
    var _timer = null, _wired = false;

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
        downloading: { label: 'Downloading', cls: 'dl' },
        queued: { label: 'Queued', cls: 'q' },
        completed: { label: 'Completed', cls: 'ok' },
        failed: { label: 'Failed', cls: 'fail' }
    };

    function fmtGB(bytes) {
        var gb = (bytes || 0) / (1024 * 1024 * 1024);
        return gb >= 0.1 ? (Math.round(gb * 10) / 10) + ' GB' : Math.round((bytes || 0) / (1024 * 1024)) + ' MB';
    }

    function itemHTML(d) {
        var st = STATUS[d.status] || STATUS.downloading;
        var icon = KIND_ICON[(d.kind || '').toLowerCase()] || '🎬';
        var pct = Math.max(0, Math.min(100, d.progress || 0));
        var name = d.title || d.release_title || 'Download';
        var bar = (d.status === 'downloading' || d.status === 'queued')
            ? '<div class="vdpg-bar"><div class="vdpg-bar-fill" style="width:' + pct + '%"></div></div>' +
              '<div class="vdpg-pct">' + (Math.round(pct)) + '%</div>'
            : '';
        var meta = [];
        meta.push('<span>' + fmtGB(d.size_bytes) + '</span>');
        if (d.username) meta.push('<span>👤 ' + esc(d.username) + '</span>');
        if (d.status === 'completed' && d.dest_path) meta.push('<span class="vdpg-dest">→ ' + esc(d.dest_path) + '</span>');
        if (d.status === 'failed' && d.error) meta.push('<span class="vdpg-err">' + esc(d.error) + '</span>');
        return '<div class="vdpg-item vdpg-item--' + st.cls + '">' +
            '<div class="vdpg-ic">' + icon + '</div>' +
            '<div class="vdpg-body">' +
                '<div class="vdpg-row1"><span class="vdpg-name">' + esc(name) + '</span>' +
                    '<span class="vdpg-pill vdpg-pill--' + st.cls + '">' + st.label + '</span></div>' +
                (d.release_title && d.release_title !== name ? '<div class="vdpg-rel" title="' + esc(d.release_title) + '">' + esc(d.release_title) + '</div>' : '') +
                bar +
                '<div class="vdpg-meta">' + meta.join('') + '</div>' +
            '</div>' +
        '</div>';
    }

    function render(list) {
        var host = document.querySelector('[data-vdpg-list]'); if (!host) return;
        list = list || [];
        var clearBtn = document.querySelector('[data-vdpg-clear]');
        var finished = list.filter(function (d) { return d.status === 'completed' || d.status === 'failed'; }).length;
        if (clearBtn) clearBtn.hidden = finished === 0;
        var active = list.filter(function (d) { return d.status === 'downloading' || d.status === 'queued'; }).length;
        var sub = document.querySelector('[data-vdpg-sub]');
        if (sub) sub.textContent = list.length
            ? (active + ' active · ' + finished + ' finished')
            : "Everything you've grabbed from the video side";
        if (!list.length) {
            host.innerHTML = '<div class="vdpg-empty"><div class="vdpg-empty-ic">⤓</div>' +
                '<div class="vdpg-empty-t">No downloads yet</div>' +
                '<div class="vdpg-empty-s">Hit Grab on a search result and it\'ll show up here.</div></div>';
            return;
        }
        host.innerHTML = list.map(itemHTML).join('');
    }

    function load() {
        getJSON(URL_ACTIVE).then(function (d) { if (d) render(d.downloads || []); });
    }

    function anyActive() {
        return !!document.querySelector('.vdpg-item--dl, .vdpg-item--q');
    }

    function start() {
        load();
        if (_timer) clearInterval(_timer);
        // Poll while open; the monitor moves files server-side regardless.
        _timer = setInterval(function () { load(); }, 2500);
        wire();
    }
    function stop() { if (_timer) { clearInterval(_timer); _timer = null; } }

    function wire() {
        if (_wired) return; _wired = true;
        var clearBtn = document.querySelector('[data-vdpg-clear]');
        if (clearBtn) clearBtn.addEventListener('click', function () {
            fetch(URL_CLEAR, { method: 'POST', headers: { Accept: 'application/json' } })
                .then(function () { toast('Cleared finished downloads', 'success'); load(); })
                .catch(function () { /* ignore */ });
        });
    }

    document.addEventListener('soulsync:video-page-shown', function (e) {
        if (e.detail === 'video-downloads') start(); else stop();
    });
    // A fresh grab elsewhere → if the page is open, reflect it quickly.
    document.addEventListener('soulsync:video-download-started', function () {
        if (document.querySelector('[data-video-subpage="video-downloads"]:not([hidden])')) setTimeout(load, 400);
    });
    // Keep polling lively only while something is in flight (handled by the 2.5s timer);
    // anyActive() is exposed for future adaptive intervals.
    window._vdpgAnyActive = anyActive;
})();
