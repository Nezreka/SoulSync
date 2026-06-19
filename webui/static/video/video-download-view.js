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
        if (opts.kind === 'show') { renderShow(container, opts); return; }
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

    // ── TV show download view ─────────────────────────────────────────────────
    // A season→episode picker (everything you're missing pre-ticked), each season
    // and episode searchable inline across your sources, plus a bulk "Search N
    // selected". Searches are stubs (faux-scan) — same motion the engine will drive.
    function isoToday() {
        var n = new Date();
        return n.getFullYear() + '-' + ('0' + (n.getMonth() + 1)).slice(-2) + '-' + ('0' + n.getDate()).slice(-2);
    }
    function epState(e, today) {
        if (e && e.owned) return 'owned';
        if (e && e.air_date && e.air_date > today) return 'upcoming';
        return 'missing';
    }

    function renderShow(container, opts) {
        var d = opts.detail || {};
        var st = container._dl = {
            sel: new Set(), today: isoToday(),
            tvId: opts.tvId || d.tmdb_id || null, source: opts.source || 'library',
            sources: ['soulseek'], epMeta: {}
        };
        container.innerHTML =
            '<div class="vdl-section"><div class="vdl-sec-label">Quality target</div>' +
                '<div class="vdl-chips" data-vdl-target><span class="vdl-chip vdl-chip--ghost">Loading…</span></div></div>' +
            '<div class="vdl-show-bar">' +
                '<label class="vdl-allchk"><input type="checkbox" data-vdl-all><span class="vdl-allchk-txt">All</span></label>' +
                '<span class="vdl-show-summary" data-vdl-summary>Loading episodes…</span>' +
                '<button class="vdl-search-all vdl-search-sel" type="button" data-vdl-search-sel disabled>' +
                    '⌕ Search <span data-vdl-selcount>0</span> selected</button>' +
            '</div>' +
            '<div class="vdl-seasons" data-vdl-seasons></div>';

        if (!container._dlShowWired) {
            container._dlShowWired = true;
            container.addEventListener('click', onShowClick);
            container.addEventListener('change', onShowChange);
        }
        getJSON('/api/video/downloads/quality').then(function (p) { if (container.isConnected && p) renderTarget(container, p, false); });
        getJSON('/api/video/downloads/config').then(function (c) { if (container.isConnected) st.sources = sourcesFromConfig(c); });
        buildSeasons(container, d, st);
    }

    function buildSeasons(container, d, st) {
        var host = container.querySelector('[data-vdl-seasons]'); if (!host) return;
        var seasons = (d.seasons || []).slice();
        if (!seasons.length) { host.innerHTML = '<div class="vdl-src-empty">No season information available.</div>'; updateShowBar(container); return; }
        host.innerHTML = seasons.map(function (s) { return seasonShellHTML(s); }).join('');
        seasons.forEach(function (s) {
            var card = host.querySelector('.vdl-season[data-vdl-season="' + s.season_number + '"]');
            var eps = s.episodes || [];
            if (eps.length) { fillSeason(container, card, s.season_number, eps, st); }
            else if ((s.episode_total || 0) > 0 && st.tvId) { fetchSeason(container, card, s.season_number, st); }
            else { var b = card.querySelector('.vdl-season-eps'); if (b) b.innerHTML = '<div class="vdl-season-empty">No episodes.</div>'; }
        });
        updateShowBar(container);
    }

    function seasonShellHTML(s) {
        var sn = s.season_number;
        var total = (s.episodes && s.episodes.length) || s.episode_total || 0;
        return '<div class="vdl-season" data-vdl-season="' + sn + '">' +
            '<div class="vdl-season-head" data-vdl-season-toggle>' +
                '<input type="checkbox" class="vdl-season-cb" data-vdl-season-all="' + sn + '">' +
                '<span class="vdl-season-name">' + esc(s.title || ('Season ' + sn)) + '</span>' +
                '<span class="vdl-season-meta" data-vdl-season-meta>' + total + ' eps</span>' +
                '<button class="vdl-season-search" type="button" data-vdl-season-search="' + sn + '" title="Search this season">⌕</button>' +
                '<span class="vdl-season-chev" aria-hidden="true">⌄</span>' +
            '</div>' +
            '<div class="vdl-season-body"><div class="vdl-season-eps"><div class="vdl-season-empty">Loading…</div></div></div>' +
        '</div>';
    }

    function fillSeason(container, card, sn, eps, st) {
        if (!card) return;
        var body = card.querySelector('.vdl-season-eps'); if (!body) return;
        var missing = 0;
        eps.forEach(function (e) {
            var es = epState(e, st.today);
            st.epMeta[sn + '_' + e.episode_number] = { state: es };
            if (es === 'missing') { missing++; st.sel.add(sn + '_' + e.episode_number); }
        });
        body.innerHTML = eps.map(function (e) { return epRowHTML(sn, e, st); }).join('');
        var meta = card.querySelector('[data-vdl-season-meta]');
        if (meta) meta.textContent = eps.length + ' eps' + (missing ? ' · ' + missing + ' missing' : '');
        card.setAttribute('data-loaded', '1');
        syncSeason(container, sn);
    }

    function fetchSeason(container, card, sn, st) {
        if (!card || !st.tvId) return;
        fetch('/api/video/tmdb/show/' + st.tvId + '/season/' + sn, { headers: { Accept: 'application/json' } })
            .then(function (r) { return r.ok ? r.json() : null; })
            .then(function (data) {
                if (!container.isConnected) return;
                var eps = (data && data.episodes) || [];
                var b = card.querySelector('.vdl-season-eps');
                if (!eps.length) { if (b) b.innerHTML = '<div class="vdl-season-empty">No episode info.</div>'; return; }
                fillSeason(container, card, sn, eps, st);
                updateShowBar(container);
            })
            .catch(function () { var b = card.querySelector('.vdl-season-eps'); if (b) b.innerHTML = '<div class="vdl-season-empty">Couldn’t load.</div>'; });
    }

    function epRowHTML(sn, e, st) {
        var key = sn + '_' + e.episode_number;
        var es = (st.epMeta[key] && st.epMeta[key].state) || epState(e, st.today);
        var lock = (es === 'upcoming');
        var ctrl = lock
            ? '<span class="vdl-ep-lock" title="Hasn\'t aired yet">◷</span>'
            : '<input type="checkbox" class="vdl-ep-cb" data-vdl-ep-cb="' + key + '"' + (st.sel.has(key) ? ' checked' : '') + '>';
        var badge = es === 'owned' ? '<span class="vdl-ep-badge vdl-ep-badge--owned">In library</span>'
            : es === 'upcoming' ? '<span class="vdl-ep-badge vdl-ep-badge--soon">Upcoming</span>'
            : '<span class="vdl-ep-badge vdl-ep-badge--missing">Missing</span>';
        return '<div class="vdl-ep vdl-ep--' + es + '" data-vdl-ep="' + key + '">' +
            '<div class="vdl-ep-head"' + (lock ? '' : ' data-vdl-ep-toggle') + '>' +
                '<span class="vdl-ep-cell">' + ctrl + '</span>' +
                '<span class="vdl-ep-num">E' + (e.episode_number != null ? e.episode_number : '') + '</span>' +
                '<span class="vdl-ep-title">' + esc(e.title || ('Episode ' + e.episode_number)) + '</span>' +
                '<span class="vdl-ep-status" data-vdl-ep-status></span>' +
                badge +
                (lock ? '' : '<span class="vdl-ep-chev" aria-hidden="true">⌄</span>') +
            '</div>' +
            '<div class="vdl-ep-search" data-vdl-ep-search></div>' +
        '</div>';
    }

    function onShowClick(e) {
        var container = e.currentTarget; if (!container._dl) return;
        var srch = e.target.closest('[data-vdl-search]');
        if (srch) { var row = srch.closest('.vdl-src'); if (row) scanRow(row, 0); toast('Automatic search isn’t wired up yet — coming soon', 'info'); return; }
        var ss = e.target.closest('[data-vdl-season-search]');
        if (ss) {
            var sc = container.querySelector('.vdl-season[data-vdl-season="' + ss.getAttribute('data-vdl-season-search') + '"]');
            if (sc) sc.classList.add('vdl-season--open');   // reveal the rows being scanned
            searchScope(container, sc); return;
        }
        if (e.target.closest('[data-vdl-search-sel]')) { searchScope(container, container.querySelector('[data-vdl-seasons]')); return; }
        var sh = e.target.closest('[data-vdl-season-toggle]');
        if (sh && !e.target.closest('.vdl-season-cb') && !e.target.closest('[data-vdl-season-search]')) {
            sh.closest('.vdl-season').classList.toggle('vdl-season--open'); return;
        }
        var eh = e.target.closest('[data-vdl-ep-toggle]');
        if (eh && !e.target.closest('.vdl-ep-cell')) { toggleEp(container, eh.closest('.vdl-ep')); }
    }

    function onShowChange(e) {
        var container = e.currentTarget; var st = container._dl; if (!st) return;
        var ec = e.target.closest('[data-vdl-ep-cb]');
        if (ec) {
            var k = ec.getAttribute('data-vdl-ep-cb');
            if (ec.checked) st.sel.add(k); else st.sel.delete(k);
            syncSeason(container, k.split('_')[0]); updateShowBar(container); return;
        }
        var sa = e.target.closest('[data-vdl-season-all]');
        if (sa) { setSeasonSel(container, sa.getAttribute('data-vdl-season-all'), sa.checked); updateShowBar(container); return; }
        if (e.target.closest('[data-vdl-all]')) { setAllSel(container, e.target.checked); updateShowBar(container); }
    }

    function toggleEp(container, epEl) {
        if (!epEl) return;
        var open = !epEl.classList.contains('vdl-ep--open');
        epEl.classList.toggle('vdl-ep--open', open);
        if (open && !epEl.getAttribute('data-srcbuilt')) buildEpSearch(container, epEl);
    }

    function buildEpSearch(container, epEl) {
        epEl.setAttribute('data-srcbuilt', '1');
        var panel = epEl.querySelector('[data-vdl-ep-search]'); if (!panel) return;
        var srcs = (container._dl.sources || []).filter(function (s) { return SRC_META[s]; });
        if (!srcs.length) { panel.innerHTML = '<div class="vdl-ep-srcs"><div class="vdl-src-empty">No source configured.</div></div>'; return; }
        panel.innerHTML = '<div class="vdl-ep-srcs">' + srcs.map(function (s) {
            var m = SRC_META[s];
            return '<div class="vdl-src vdl-src--mini" data-vdl-src="' + s + '">' +
                '<span class="vdl-src-icon"><span class="vdl-src-emoji">' + m.emoji + '</span></span>' +
                '<span class="vdl-src-main"><span class="vdl-src-name">' + esc(m.name) + '</span>' +
                    '<span class="vdl-src-meta"><span class="vdl-src-dot"></span><span class="vdl-src-status" data-vdl-status>Ready</span></span></span>' +
                '<button class="vdl-src-search" type="button" data-vdl-search="' + s + '">⌕ Search</button>' +
                '</div>';
        }).join('') + '</div>';
    }

    function setSeasonSel(container, sn, on) {
        var st = container._dl;
        var cbs = container.querySelectorAll('.vdl-season[data-vdl-season="' + sn + '"] .vdl-ep-cb');
        for (var i = 0; i < cbs.length; i++) {
            cbs[i].checked = on;
            var k = cbs[i].getAttribute('data-vdl-ep-cb');
            if (on) st.sel.add(k); else st.sel.delete(k);
        }
        syncSeason(container, sn);
    }

    function setAllSel(container, on) {
        var cards = container.querySelectorAll('.vdl-season');
        for (var i = 0; i < cards.length; i++) setSeasonSel(container, cards[i].getAttribute('data-vdl-season'), on);
    }

    function syncSeason(container, sn) {
        var card = container.querySelector('.vdl-season[data-vdl-season="' + sn + '"]'); if (!card) return;
        var all = card.querySelector('[data-vdl-season-all]'); if (!all) return;
        var cbs = card.querySelectorAll('.vdl-ep-cb'), checked = 0;
        for (var i = 0; i < cbs.length; i++) if (cbs[i].checked) checked++;
        all.checked = cbs.length > 0 && checked === cbs.length;
        all.indeterminate = checked > 0 && checked < cbs.length;
        all.disabled = cbs.length === 0;
    }

    function updateShowBar(container) {
        var st = container._dl; if (!st) return;
        var n = st.sel.size;
        var cnt = container.querySelector('[data-vdl-selcount]'); if (cnt) cnt.textContent = n;
        var btn = container.querySelector('[data-vdl-search-sel]'); if (btn) btn.disabled = n === 0;
        var sum = container.querySelector('[data-vdl-summary]');
        if (sum) {
            var seasons = container.querySelectorAll('.vdl-season').length;
            var owned = 0, missing = 0, total = 0;
            for (var k in st.epMeta) { total++; if (st.epMeta[k].state === 'owned') owned++; else if (st.epMeta[k].state === 'missing') missing++; }
            sum.textContent = seasons + ' season' + (seasons === 1 ? '' : 's') + ' · ' + total + ' episodes · ' +
                owned + ' in library · ' + missing + ' missing';
        }
        var master = container.querySelector('[data-vdl-all]');
        if (master) {
            var all = container.querySelectorAll('.vdl-ep-cb'), c = 0;
            for (var i = 0; i < all.length; i++) if (all[i].checked) c++;
            master.checked = all.length > 0 && c === all.length;
            master.indeterminate = c > 0 && c < all.length;
        }
    }

    // Scan every SELECTED episode within a scope (a season card or the whole list).
    function searchScope(container, scopeEl) {
        if (!scopeEl || !container._dl) return;
        var st = container._dl, eps = scopeEl.querySelectorAll('.vdl-ep'), picked = [];
        for (var i = 0; i < eps.length; i++) { var k = eps[i].getAttribute('data-vdl-ep'); if (k && st.sel.has(k)) picked.push(eps[i]); }
        if (!picked.length) { toast('Select at least one episode', 'info'); return; }
        picked.forEach(function (epEl, i) { scanEp(epEl, i); });
        toast('Automatic search isn’t wired up yet — coming soon', 'info');
    }

    function scanEp(epEl, i) {
        if (epEl._scanning) return;
        epEl._scanning = true;
        epEl.classList.add('vdl-ep--scanning');
        var s = epEl.querySelector('[data-vdl-ep-status]');
        if (s) { s.textContent = 'Searching'; s.className = 'vdl-ep-status vdl-ep-status--scanning'; }
        setTimeout(function () {
            if (!epEl.isConnected) { epEl._scanning = false; return; }
            epEl.classList.remove('vdl-ep--scanning');
            epEl._scanning = false;
            var x = epEl.querySelector('[data-vdl-ep-status]');
            if (x) { x.textContent = 'Coming soon'; x.className = 'vdl-ep-status vdl-ep-status--soon'; }
        }, 1100 + i * 180);
    }

    window.VideoDownload = { render: render };
})();
