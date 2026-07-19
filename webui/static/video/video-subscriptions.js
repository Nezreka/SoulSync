/*
 * SoulSync — Import ytdl-sub / Kometa subscriptions (video, isolated).
 *
 * Opened by [data-vwlp-import] on the watchlist Channels tab. Upload or paste a
 * subscriptions.yml → preview (parse, instant) → Import (background: resolve +
 * follow each channel/playlist, apply the show name) with a live progress bar.
 * Talks only to /api/video/youtube/subscriptions/*. Self-contained IIFE.
 */
(function () {
    'use strict';

    var PREVIEW = '/api/video/youtube/subscriptions/preview';
    var IMPORT = '/api/video/youtube/subscriptions/import';
    var STATUS = '/api/video/youtube/subscriptions/import/status';

    var el = null, poll = null, text = '';

    function esc(s) {
        return String(s == null ? '' : s)
            .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
    }
    function post(url, body) {
        return fetch(url, { method: 'POST', headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
            body: JSON.stringify(body || {}) }).then(function (r) { return r.ok ? r.json() : r.json().then(function (b) { throw b; }); });
    }

    function close() {
        if (poll) { clearInterval(poll); poll = null; }
        if (el) { el.remove(); el = null; }
    }

    function open() {
        if (el) return;
        el = document.createElement('div');
        el.className = 'vsub-overlay';
        el.innerHTML =
            '<div class="vsub-modal" role="dialog" aria-modal="true" aria-label="Import subscriptions">' +
                '<div class="vsub-head">' +
                    '<div><h2 class="vsub-title">Import subscriptions</h2>' +
                    '<p class="vsub-sub">Bring your ytdl-sub / Kometa channels &amp; playlists into SoulSync.</p></div>' +
                    '<button class="vsub-x" type="button" data-vsub-close aria-label="Close">&times;</button>' +
                '</div>' +
                '<div class="vsub-body" data-vsub-body></div>' +
            '</div>';
        document.body.appendChild(el);
        el.addEventListener('click', function (e) {
            if (e.target === el || e.target.closest('[data-vsub-close]')) { close(); return; }
            if (e.target.closest('[data-vsub-pick]')) { el.querySelector('[data-vsub-file]').click(); return; }
            if (e.target.closest('[data-vsub-import]')) { startImport(); return; }
        });
        renderDrop();
    }

    // ── step 1: drop / paste ──────────────────────────────────────────────────
    function renderDrop() {
        var b = el.querySelector('[data-vsub-body]');
        b.innerHTML =
            '<div class="vsub-drop" data-vsub-drop>' +
                '<input type="file" data-vsub-file accept=".yml,.yaml,.txt,text/yaml" hidden>' +
                '<div class="vsub-drop-ic">📄</div>' +
                '<div class="vsub-drop-t">Drop your subscriptions file here</div>' +
                '<button class="vsub-btn" type="button" data-vsub-pick>Choose file</button>' +
                '<div class="vsub-drop-hint">or paste its contents below</div>' +
            '</div>' +
            '<textarea class="vsub-paste" data-vsub-paste rows="6" placeholder="music_videos:&#10;  overrides:&#10;    tv_show_name: ...&#10;    url: https://youtube.com/@..."></textarea>' +
            '<div class="vsub-foot"><span class="vsub-note" data-vsub-note></span></div>';
        var file = b.querySelector('[data-vsub-file]');
        file.addEventListener('change', function () {
            if (!file.files || !file.files[0]) return;
            var fr = new FileReader();
            fr.onload = function () { preview(String(fr.result || '')); };
            fr.readAsText(file.files[0]);
        });
        var drop = b.querySelector('[data-vsub-drop]');
        ['dragover', 'dragenter'].forEach(function (ev) {
            drop.addEventListener(ev, function (e) { e.preventDefault(); drop.classList.add('vsub-drop--on'); });
        });
        ['dragleave', 'drop'].forEach(function (ev) {
            drop.addEventListener(ev, function (e) { e.preventDefault(); drop.classList.remove('vsub-drop--on'); });
        });
        drop.addEventListener('drop', function (e) {
            var f = e.dataTransfer && e.dataTransfer.files && e.dataTransfer.files[0];
            if (!f) return;
            var fr = new FileReader(); fr.onload = function () { preview(String(fr.result || '')); }; fr.readAsText(f);
        });
        var paste = b.querySelector('[data-vsub-paste]');
        var t = null;
        paste.addEventListener('input', function () {
            if (t) clearTimeout(t);
            t = setTimeout(function () { if (paste.value.trim().length > 20) preview(paste.value); }, 400);
        });
    }

    // ── step 2: preview (parse) ───────────────────────────────────────────────
    function preview(raw) {
        text = raw;
        post(PREVIEW, { text: raw }).then(function (d) {
            if (!d.count) { note('No subscriptions found — is this a ytdl-sub / Kometa file?'); return; }
            renderPreview(d.subscriptions);
        }).catch(function () { note('Could not read that file.'); });
    }
    function note(msg) {
        var n = el && el.querySelector('[data-vsub-note]');
        if (n) n.textContent = msg || '';
    }
    function renderPreview(subs) {
        var b = el.querySelector('[data-vsub-body]');
        var rows = subs.map(function (s) {
            var isPl = /[?&]list=|\/playlist/.test(s.url || '');
            var presets = s.presets || [];
            var has = function (name) { return presets.some(function (p) { return String(p).indexOf(name) >= 0; }); };
            var tags = '';
            if (!isPl && has('best_video_quality')) tags += '<span class="vsub-tag">best quality</span>';
            if (!isPl && has('only_recent_videos')) tags += '<span class="vsub-tag">keep recent</span>';
            return '<div class="vsub-row"><span class="vsub-row-ic">' + (isPl ? '🎞️' : '▶') + '</span>' +
                '<span class="vsub-row-name" title="' + esc(s.url) + '">' + esc(s.show_name || s.name) + '</span>' +
                tags +
                '<span class="vsub-row-kind">' + (isPl ? 'playlist' : 'channel') + '</span></div>';
        }).join('');
        b.innerHTML =
            '<div class="vsub-count"><strong>' + subs.length + '</strong> subscription' + (subs.length === 1 ? '' : 's') + ' found</div>' +
            '<div class="vsub-list">' + rows + '</div>' +
            '<div class="vsub-foot">' +
                '<button class="vsub-btn vsub-btn--ghost" type="button" data-vsub-close>Cancel</button>' +
                '<button class="vsub-btn vsub-btn--go" type="button" data-vsub-import>Import ' + subs.length + '</button>' +
            '</div>' +
            '<p class="vsub-fineprint">Each is resolved and followed — channels get the show name applied; ' +
                'a real YouTube lookup runs per item, so a big list takes a minute.</p>';
    }

    // ── step 3: import (background) + progress ────────────────────────────────
    function startImport() {
        post(IMPORT, { text: text }).then(function (d) {
            if (!d.started) { note(d.error || 'Could not start the import.'); return; }
            renderProgress(d.total);
            poll = setInterval(tick, 700);
        }).catch(function (e) { note((e && e.error) || 'Could not start the import.'); });
    }
    function renderProgress(total) {
        var b = el.querySelector('[data-vsub-body]');
        b.innerHTML =
            '<div class="vsub-prog"><div class="vsub-prog-bar"><span data-vsub-fill style="width:0%"></span></div>' +
                '<div class="vsub-prog-line"><span data-vsub-count>0 / ' + total + '</span>' +
                '<span data-vsub-cur></span></div></div>' +
            '<div class="vsub-tally" data-vsub-tally></div>' +
            '<div class="vsub-foot"><button class="vsub-btn vsub-btn--go" type="button" data-vsub-close disabled data-vsub-done>Working…</button></div>';
    }
    function tick() {
        fetch(STATUS, { headers: { Accept: 'application/json' } }).then(function (r) { return r.json(); }).then(function (st) {
            if (!el) return;
            var total = st.total || 1, done = st.done || 0;
            var fill = el.querySelector('[data-vsub-fill]'); if (fill) fill.style.width = Math.round(100 * done / total) + '%';
            var c = el.querySelector('[data-vsub-count]'); if (c) c.textContent = done + ' / ' + total;
            var cur = el.querySelector('[data-vsub-cur]'); if (cur) cur.textContent = st.current ? ('Following ' + st.current + '…') : '';
            var ty = el.querySelector('[data-vsub-tally]');
            if (ty) ty.innerHTML =
                '<span class="vsub-chip vsub-chip--ok">✓ ' + (st.followed || 0) + ' followed</span>' +
                (st.skipped ? '<span class="vsub-chip">already had ' + st.skipped + '</span>' : '') +
                (st.failed ? '<span class="vsub-chip vsub-chip--bad">' + st.failed + ' failed</span>' : '');
            if (st.finished) {
                clearInterval(poll); poll = null;
                if (cur) cur.textContent = 'Done';
                var btn = el.querySelector('[data-vsub-done]');
                if (btn) { btn.disabled = false; btn.textContent = 'Done'; }
                if (typeof showToast === 'function')
                    showToast('Imported ' + (st.followed || 0) + ' subscription' + ((st.followed === 1) ? '' : 's'), 'success');
                document.dispatchEvent(new CustomEvent('soulsync:video-wishlist-changed'));
            }
        }).catch(function () { /* keep polling */ });
    }

    document.addEventListener('click', function (e) {
        if (e.target.closest('[data-vwlp-import]')) { e.preventDefault(); open(); }
    });
})();
