/*
 * SoulSync — universal Download modal (movie / TV show / YouTube).
 *
 * Opened from the get-modal's "Download" button (and, later, the YouTube card).
 * v1 is VISUAL scaffolding for the direct-download flow: it shows the quality
 * TARGET (read from the Settings → Downloads profile you configured), judges any
 * copy you ALREADY own against that target (real — via /downloads/evaluate), and
 * lists each attached download source with a per-source "Search" affordance. The
 * searches themselves are stubs — no backend wiring yet (that's the engine phase).
 *
 * VideoDownload.open({ kind, id, source, title, thumb }). Self-contained.
 */
(function () {
    'use strict';

    function esc(s) {
        return String(s == null ? '' : s)
            .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
    }
    function toast(msg, type) { if (typeof showToast === 'function') showToast(msg, type); }
    function hueOf(s) { var h = 0, t = String(s || ''); for (var i = 0; i < t.length; i++) h = (h * 31 + t.charCodeAt(i)) >>> 0; return h % 360; }
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

    var modalEl = null, keyHandler = null;

    function closeModal() {
        if (!modalEl) return;
        modalEl.classList.remove('vdl-open');
        document.body.style.removeProperty('overflow');
        if (keyHandler) { document.removeEventListener('keydown', keyHandler); keyHandler = null; }
        var el = modalEl; modalEl = null;
        setTimeout(function () { if (el && el.parentNode) el.parentNode.removeChild(el); }, 220);
    }

    function shell(o) {
        return '<div class="vdl-modal" role="dialog" aria-modal="true">' +
            '<button class="vdl-close" type="button" data-vdl-close aria-label="Close">&times;</button>' +
            '<div class="vdl-hero" data-vdl-hero>' +
                '<div class="vdl-hero-scrim"></div>' +
                '<img class="vdl-poster" data-vdl-poster alt="" hidden>' +
                '<div class="vdl-hero-content">' +
                    '<div class="vdl-eyebrow">⤓ Download</div>' +
                    '<h2 class="vdl-title" data-vdl-title>' + esc(o.title || 'Loading…') + '</h2>' +
                    '<div class="vdl-meta" data-vdl-meta></div>' +
                '</div>' +
            '</div>' +
            '<div class="vdl-body">' +
                '<div class="vdl-section">' +
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
                    '<div class="vdl-foot-note">Automatic searching arrives with the download engine — this is the layout it\'ll drive.</div>' +
                '</div>' +
            '</div>' +
        '</div>';
    }

    function open(o) {
        if (!o) return;
        closeModal();
        var ov = document.createElement('div');
        ov.className = 'vdl-overlay';
        ov.style.setProperty('--vdl-h', hueOf(o.title || ''));
        ov.innerHTML = shell(o);
        document.body.appendChild(ov);
        document.body.style.overflow = 'hidden';
        modalEl = ov;
        requestAnimationFrame(function () { ov.classList.add('vdl-open'); });

        ov.addEventListener('click', function (e) {
            if (e.target === ov || e.target.closest('[data-vdl-close]')) { closeModal(); return; }
            var sb = e.target.closest('[data-vdl-search]');
            if (sb) { stubSearch(sb.getAttribute('data-vdl-search')); return; }
            if (e.target.closest('[data-vdl-search-all]')) { stubSearch('*'); return; }
        });
        keyHandler = function (e) { if (e.key === 'Escape') closeModal(); };
        document.addEventListener('keydown', keyHandler);

        load(o);
    }

    function load(o) {
        var isYt = o.kind === 'youtube';
        if (isYt) {
            fillYt(o);
            renderSources(['youtube']);
        } else {
            var url = (o.source === 'tmdb')
                ? '/api/video/tmdb/' + o.kind + '/' + o.id
                : '/api/video/detail/' + o.kind + '/' + o.id;
            getJSON(url).then(function (d) { if (modalEl && d) fillDetail(d, o); });
            getJSON('/api/video/downloads/config').then(function (c) { if (modalEl) renderSources(sourcesFromConfig(c)); });
        }
        var qurl = isYt ? '/api/video/downloads/youtube-quality' : '/api/video/downloads/quality';
        getJSON(qurl).then(function (p) { if (modalEl && p) renderTarget(p, isYt); });
    }

    function sourcesFromConfig(c) {
        c = c || {};
        if (c.download_mode === 'hybrid' && Array.isArray(c.hybrid_order) && c.hybrid_order.length) return c.hybrid_order;
        if (c.download_mode) return [c.download_mode];
        return ['soulseek'];
    }

    function fillDetail(d, o) {
        var q = function (s) { return modalEl.querySelector(s); };
        if (d.title) { var t = q('[data-vdl-title]'); if (t) t.textContent = d.title; modalEl.style.setProperty('--vdl-h', hueOf(d.title)); }
        var hero = q('[data-vdl-hero]');
        var bg = (o.source !== 'tmdb' && d.has_backdrop)
            ? '/api/video/backdrop/' + o.kind + '/' + o.id + '?w=1280'
            : (d.backdrop_url || d.backdrop || '');
        if (hero && bg) hero.style.backgroundImage = "url('" + bg + "')";
        var poster = q('[data-vdl-poster]');
        var pUrl = (o.source !== 'tmdb' && d.has_poster)
            ? '/api/video/poster/' + o.kind + '/' + o.id
            : (d.poster_url || d.poster || '');
        if (poster && pUrl) {
            poster.onload = function () { if (hero) hero.classList.add('vdl-has-poster'); poster.hidden = false; };
            poster.onerror = function () { poster.hidden = true; };
            poster.src = pUrl;
        }
        var meta = [];
        if (d.year) meta.push(d.year);
        if (d.runtime_minutes) meta.push(d.runtime_minutes + ' min');
        meta.push(o.kind === 'show' ? 'TV series' : 'Movie');
        var mt = q('[data-vdl-meta]'); if (mt) mt.textContent = meta.filter(Boolean).join('  ·  ');
        if (o.kind === 'movie' && d.owned && d.file) renderOwned(d.file);
    }

    function fillYt(o) {
        var t = modalEl.querySelector('[data-vdl-title]'); if (t && o.title) t.textContent = o.title;
        var hero = modalEl.querySelector('[data-vdl-hero]');
        if (hero && o.thumb) hero.style.backgroundImage = "url('" + o.thumb + "')";
        var mt = modalEl.querySelector('[data-vdl-meta]'); if (mt) mt.textContent = 'YouTube';
    }

    function chip(text, mod) { return '<span class="vdl-chip' + (mod ? ' vdl-chip--' + mod : '') + '">' + esc(text) + '</span>'; }

    function renderTarget(p, isYt) {
        var box = modalEl.querySelector('[data-vdl-target]'); if (!box) return;
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
    function renderOwned(file) {
        var box = modalEl.querySelector('[data-vdl-owned]'); if (!box) return;
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
            if (!modalEl || !v) return;
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

    function renderSources(list) {
        var box = modalEl.querySelector('[data-vdl-sources]'); if (!box) return;
        list = (list || []).filter(function (s) { return SRC_META[s]; });
        if (!list.length) {
            box.innerHTML = '<div class="vdl-src-empty">No download source configured — pick one on Settings → Downloads.</div>';
            return;
        }
        box.innerHTML = list.map(function (s) {
            var m = SRC_META[s];
            return '<div class="vdl-src" data-vdl-src="' + s + '">' +
                '<span class="vdl-src-emoji">' + m.emoji + '</span>' +
                '<span class="vdl-src-name">' + esc(m.name) + '</span>' +
                '<span class="vdl-src-status" data-vdl-status>Ready</span>' +
                '<button class="vdl-src-search" type="button" data-vdl-search="' + s + '">⌕ Search</button>' +
                '</div>';
        }).join('');
    }

    // Scaffold: flip the targeted row(s) to a "coming soon" state. No backend yet.
    function stubSearch(which) {
        if (!modalEl) return;
        var sel = which === '*' ? '[data-vdl-src]' : '[data-vdl-src="' + which + '"]';
        var rows = modalEl.querySelectorAll(sel);
        for (var i = 0; i < rows.length; i++) {
            var st = rows[i].querySelector('[data-vdl-status]');
            if (st) { st.textContent = 'Search engine coming soon'; st.className = 'vdl-src-status vdl-src-status--soon'; }
        }
        toast('Automatic search isn’t wired up yet — coming soon', 'info');
    }

    window.VideoDownload = { open: open };
})();
