/*
 * SoulSync — Download VIEW renderer (movie / TV show / YouTube).
 *
 * NOT its own modal: it renders the direct-download content INTO a container the
 * caller owns — the get-modal swaps its detail body for this view (with a Back
 * button) when you click "Download", and a future YouTube trigger can reuse it.
 *
 * v1 is VISUAL scaffolding: it shows the quality TARGET (read from the Settings →
 * Downloads profile), judges any copy you ALREADY own against that target (real —
 * via /downloads/evaluate), and lists each attached source with a per-source
 * "Search" affordance. The searches are stubs — no backend yet (engine phase).
 *
 * VideoDownload.render(containerEl, { kind, id, source, isYt, file }). Self-contained.
 */
(function () {
    'use strict';

    function esc(s) {
        return String(s == null ? '' : s)
            .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
    }
    function toast(msg, type) { if (typeof showToast === 'function') showToast(msg, type); }
    function resLabel(res) {
        if (!res) return '';
        res = String(res).toLowerCase();
        if (res.indexOf('2160') > -1 || res === '4k') return '4K';
        if (res.indexOf('1080') > -1) return '1080p';
        if (res.indexOf('720') > -1) return '720p';
        if (res.indexOf('480') > -1 || res.indexOf('576') > -1) return 'SD';
        return res.toUpperCase();
    }
    var CUT_LABEL = { '2160p': '4K', '1080p': '1080p', '720p': '720p', '480p': 'SD' };
    var SRC_META = {
        soulseek: { name: 'Soulseek', emoji: '🎵' },
        torrent: { name: 'Torrent', emoji: '🧲' },
        usenet: { name: 'Usenet', emoji: '📰' },
        youtube: { name: 'YouTube', emoji: '▶' }
    };

    function getJSON(url) {
        return fetch(url, { headers: { Accept: 'application/json' } })
            .then(function (r) { return r.ok ? r.json() : null; }).catch(function () { return null; });
    }
    function postJSON(url, body) {
        return fetch(url, { method: 'POST', headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
            body: JSON.stringify(body) }).then(function (r) { return r.ok ? r.json() : null; }).catch(function () { return null; });
    }

    function contentHTML() {
        return '<div class="vdl-section">' +
                '<div class="vdl-sec-label">Quality target</div>' +
                '<div class="vdl-chips" data-vdl-target><span class="vdl-chip vdl-chip--ghost">Loading…</span></div>' +
            '</div>' +
            '<div class="vdl-owned" data-vdl-owned hidden></div>' +
            '<div class="vdl-section">' +
                '<div class="vdl-sec-head">' +
                    '<div class="vdl-sec-label">Sources</div>' +
                    '<button class="vdl-search-all" type="button" data-vdl-search-all>⌕ Search all</button>' +
                '</div>' +
                '<div class="vdl-sources" data-vdl-sources><div class="vdl-src-empty">Loading sources…</div></div>' +
            '</div>';
    }

    function onClick(e) {
        var container = e.currentTarget;
        var sb = e.target.closest('[data-vdl-search]');
        if (sb) { stubSearch(container, sb.getAttribute('data-vdl-search')); return; }
        if (e.target.closest('[data-vdl-search-all]')) { stubSearch(container, '*'); }
    }

    // Render the download view into `container`. Re-callable (resets each time).
    function render(container, opts) {
        if (!container) return;
        opts = opts || {};
        container.innerHTML = contentHTML();
        if (!container._vdlWired) { container._vdlWired = true; container.addEventListener('click', onClick); }

        var isYt = !!opts.isYt;
        getJSON(isYt ? '/api/video/downloads/youtube-quality' : '/api/video/downloads/quality')
            .then(function (p) { if (container.isConnected && p) renderTarget(container, p, isYt); });
        if (isYt) {
            renderSources(container, ['youtube']);
        } else {
            getJSON('/api/video/downloads/config').then(function (c) {
                if (container.isConnected) renderSources(container, sourcesFromConfig(c));
            });
            if (opts.file) renderOwned(container, opts.file);
        }
    }

    function sourcesFromConfig(c) {
        c = c || {};
        if (c.download_mode === 'hybrid' && Array.isArray(c.hybrid_order) && c.hybrid_order.length) return c.hybrid_order;
        if (c.download_mode) return [c.download_mode];
        return ['soulseek'];
    }

    function chip(text, mod) { return '<span class="vdl-chip' + (mod ? ' vdl-chip--' + mod : '') + '">' + esc(text) + '</span>'; }

    function renderTarget(container, p, isYt) {
        var box = container.querySelector('[data-vdl-target]'); if (!box) return;
        var chips = [];
        if (isYt) {
            chips.push(chip('Up to ' + (p.max_resolution === 'best' ? 'best' : (p.max_resolution || '1080p'))));
            if (p.video_codec && p.video_codec !== 'any') chips.push(chip('Prefer ' + p.video_codec.toUpperCase()));
            if (p.container) chips.push(chip(p.container.toUpperCase()));
            if (p.prefer_60fps) chips.push(chip('60fps'));
            chips.push(chip(p.allow_hdr ? 'HDR ok' : 'SDR'));
        } else {
            chips.push(chip(p.cutoff_resolution ? 'Stop at ' + (CUT_LABEL[p.cutoff_resolution] || p.cutoff_resolution) : 'Always upgrade'));
            if (p.prefer_codec && p.prefer_codec !== 'any') chips.push(chip('Prefer ' + (p.prefer_codec === 'hevc' ? 'HEVC' : p.prefer_codec.toUpperCase())));
            if (p.prefer_hdr === 'prefer') chips.push(chip('Prefer HDR'));
            else if (p.prefer_hdr === 'require') chips.push(chip('HDR required', 'req'));
            if (Array.isArray(p.rejects) && p.rejects.length) chips.push(chip('Reject ' + p.rejects.join(', '), 'rej'));
            if (p.max_movie_gb) chips.push(chip('Movie ≤ ' + p.max_movie_gb + ' GB'));
            if (p.max_episode_gb) chips.push(chip('Episode ≤ ' + p.max_episode_gb + ' GB'));
        }
        box.innerHTML = chips.join('');
    }

    // Owned copy → "In your library · 720p · BluRay · X265" + a verdict against the
    // quality target (real: /downloads/evaluate). meets → reassuring; else upgrade.
    function renderOwned(container, file) {
        var box = container.querySelector('[data-vdl-owned]'); if (!box) return;
        var bits = [resLabel(file.resolution), file.release_source, (file.video_codec || '').toUpperCase()].filter(Boolean);
        box.innerHTML =
            '<div class="vdl-owned-row">' +
                '<span class="vdl-owned-ic">✓</span>' +
                '<span class="vdl-owned-txt"><strong>In your library</strong>' + (bits.length ? ' · ' + esc(bits.join(' · ')) : '') + '</span>' +
                '<span class="vdl-verdict vdl-verdict--pending" data-vdl-verdict>checking…</span>' +
            '</div>' +
            '<div class="vdl-reasons" data-vdl-reasons></div>';
        box.hidden = false;
        postJSON('/api/video/downloads/evaluate', { file: file }).then(function (v) {
            if (!container.isConnected || !v) return;
            var badge = box.querySelector('[data-vdl-verdict]');
            if (badge) {
                badge.classList.remove('vdl-verdict--pending');
                badge.classList.add(v.meets ? 'vdl-verdict--ok' : 'vdl-verdict--up');
                badge.textContent = v.meets ? 'Meets your target' : 'Eligible for upgrade';
            }
            var rs = box.querySelector('[data-vdl-reasons]');
            if (rs && v.reasons && v.reasons.length) {
                rs.innerHTML = v.reasons.map(function (r) {
                    return '<div class="vdl-reason vdl-reason--' + (r.ok ? 'ok' : 'no') + '">' +
                        (r.ok ? '✓' : '↑') + ' ' + esc(r.text) + '</div>';
                }).join('');
            }
        });
    }

    function renderSources(container, list) {
        var box = container.querySelector('[data-vdl-sources]'); if (!box) return;
        list = (list || []).filter(function (s) { return SRC_META[s]; });
        if (!list.length) {
            box.innerHTML = '<div class="vdl-src-empty">No download source configured — pick one on Settings → Downloads.</div>';
            return;
        }
        box.innerHTML = list.map(function (s) {
            var m = SRC_META[s];
            return '<div class="vdl-src" data-vdl-src="' + s + '">' +
                '<span class="vdl-src-icon"><span class="vdl-src-emoji">' + m.emoji + '</span></span>' +
                '<span class="vdl-src-main">' +
                    '<span class="vdl-src-name">' + esc(m.name) + '</span>' +
                    '<span class="vdl-src-meta"><span class="vdl-src-dot"></span>' +
                        '<span class="vdl-src-status" data-vdl-status>Ready</span></span>' +
                '</span>' +
                '<button class="vdl-src-search" type="button" data-vdl-search="' + s + '">⌕ Search</button>' +
                '</div>';
        }).join('');
    }

    // Scaffold: a satisfying faux-scan (animated) that resolves to "coming soon".
    // No backend yet — this is the motion the real engine will drive.
    function scanRow(row, i) {
        if (row._scanning) return;
        row._scanning = true;
        var st = row.querySelector('[data-vdl-status]');
        var btn = row.querySelector('[data-vdl-search]');
        row.classList.add('vdl-src--scanning');
        if (btn) btn.disabled = true;
        if (st) { st.textContent = 'Searching'; st.className = 'vdl-src-status vdl-src-status--scanning'; }
        setTimeout(function () {
            if (!row.isConnected) { row._scanning = false; return; }
            row.classList.remove('vdl-src--scanning');
            row._scanning = false;
            if (btn) btn.disabled = false;
            var s = row.querySelector('[data-vdl-status]');
            if (s) { s.textContent = 'Search engine coming soon'; s.className = 'vdl-src-status vdl-src-status--soon'; }
        }, 1300 + i * 280);   // staggered finish so a "search all" ripples
    }

    function stubSearch(container, which) {
        var sel = which === '*' ? '[data-vdl-src]' : '[data-vdl-src="' + which + '"]';
        var rows = container.querySelectorAll(sel);
        for (var i = 0; i < rows.length; i++) scanRow(rows[i], i);
        toast('Automatic search isn’t wired up yet — coming soon', 'info');
    }

    window.VideoDownload = { render: render };
})();
