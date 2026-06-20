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
        return '<div class="vdl-active" data-vdl-active hidden></div>' +
            '<div class="vdl-section">' +
                '<div class="vdl-sec-label">Quality target</div>' +
                '<div class="vdl-chips" data-vdl-target><span class="vdl-chip vdl-chip--ghost">Loading…</span></div>' +
            '</div>' +
            '<div class="vdl-owned" data-vdl-owned hidden></div>' +
            '<div class="vdl-section">' +
                '<div class="vdl-sec-head">' +
                    '<div class="vdl-sec-label">Sources</div>' +
                    '<span class="vdl-src-actions vdl-src-actions--head">' +
                        '<button class="vdl-search-all" type="button" data-vdl-search-all title="Search every source — pick releases yourself">' +
                            '<span class="vdl-btn-ic" aria-hidden="true">⌕</span><span>Manual all</span></button>' +
                        '<button class="vdl-search-all vdl-auto-all" type="button" data-vdl-auto-all title="Search every source and auto-grab the best release on each">' +
                            '<span class="vdl-btn-ic vdl-btn-ic--auto" aria-hidden="true">✦</span><span>Auto all</span></button>' +
                    '</span>' +
                '</div>' +
                '<div class="vdl-sources" data-vdl-sources><div class="vdl-src-empty">Loading sources…</div></div>' +
            '</div>';
    }

    // One source = a row + its OWN results panel (so each source shows its own hits).
    function srcRowHTML(s, mini) {
        var m = SRC_META[s];
        return '<div class="vdl-src' + (mini ? ' vdl-src--mini' : '') + '" data-vdl-src="' + s + '">' +
            '<span class="vdl-src-icon"><span class="vdl-src-emoji">' + m.emoji + '</span></span>' +
            '<span class="vdl-src-main"><span class="vdl-src-name">' + esc(m.name) + '</span>' +
                '<span class="vdl-src-meta"><span class="vdl-src-status" data-vdl-status>Ready</span></span></span>' +
            '<span class="vdl-src-actions">' +
                '<button class="vdl-src-search" type="button" data-vdl-search="' + s + '" title="Search and pick a release yourself">' +
                    '<span class="vdl-btn-ic" aria-hidden="true">⌕</span><span>Manual</span></button>' +
                '<button class="vdl-src-auto" type="button" data-vdl-auto="' + s + '" title="Search and auto-grab the best release for your quality profile">' +
                    '<span class="vdl-btn-ic vdl-btn-ic--auto" aria-hidden="true">✦</span><span>Auto</span></button>' +
            '</span>' +
            '</div>';
    }
    function srcBlockHTML(s, mini) {
        return '<div class="vdl-src-block" data-vdl-src-block="' + s + '">' +
            srcRowHTML(s, mini) +
            '<div class="vdl-results" data-vdl-results-for="' + s + '" hidden></div>' +
        '</div>';
    }

    function _movieSearch(container, block, auto) {
        var o = container._opts || {};
        var s = block.getAttribute('data-vdl-src-block');
        var resultsEl = block.querySelector('[data-vdl-results-for="' + s + '"]');
        var statusRow = block.querySelector('.vdl-src');
        // In auto mode, when the search settles we grab the best accepted release.
        var onDone = auto ? function () { _autoPick(resultsEl, statusRow); } : null;
        searchInto(container, resultsEl,
            { scope: 'movie', title: o.title || '', year: o.year || null, source: s },
            [statusRow], onDone);
    }

    function onClick(e) {
        var container = e.currentTarget;
        var grab = e.target.closest('[data-vdl-grab]');
        if (grab) { doGrab(grab); return; }
        var ab = e.target.closest('[data-vdl-auto]');
        if (ab) { _movieSearch(container, ab.closest('[data-vdl-src-block]'), true); return; }
        var sb = e.target.closest('[data-vdl-search]');
        if (sb) { _movieSearch(container, sb.closest('[data-vdl-src-block]')); return; }
        if (e.target.closest('[data-vdl-auto-all]')) {
            Array.prototype.forEach.call(container.querySelectorAll('[data-vdl-src-block]'),
                function (block) { _movieSearch(container, block, true); });
            return;
        }
        if (e.target.closest('[data-vdl-search-all]')) {
            Array.prototype.forEach.call(container.querySelectorAll('[data-vdl-src-block]'),
                function (block) { _movieSearch(container, block); });
        }
    }

    // Render the download view into `container`. Re-callable (resets each time).
    function render(container, opts) {
        if (!container) return;
        opts = opts || {};
        if (opts.kind === 'show') { renderShow(container, opts); return; }
        container._opts = opts;
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
        // Resume tracking: if this title already has a download in flight (e.g. the
        // user grabbed it, closed the modal, and re-opened), show a live banner.
        watchActiveDownload(container, opts);
    }

    // Poll for an active/just-finished download of THIS title (by media identity) and
    // surface a live banner at the top of the view — so re-opening the modal knows a
    // download is already running. Suppressed while a result card is already tracking
    // it inline (fresh-grab case), to avoid a duplicate indicator.
    function watchActiveDownload(container, opts) {
        var box = container.querySelector('[data-vdl-active]'); if (!box) return;
        var mediaId = (opts.id != null ? opts.id : opts.mediaId);
        if (mediaId == null) { box.hidden = true; return; }
        var mediaSource = opts.source || opts.mediaSource || 'library';
        if (container._activeT) { clearTimeout(container._activeT); container._activeT = null; }
        (function tick() {
            if (!container.isConnected) return;   // modal closed → stop
            getJSON('/api/video/downloads/status?media_id=' + encodeURIComponent(mediaId) +
                    '&media_source=' + encodeURIComponent(mediaSource)).then(function (d) {
                if (!container.isConnected) return;
                var dl = d && d.download;
                var inlineTracker = !!container.querySelector('[data-vdl-track]');   // a card is already tracking
                renderActiveBanner(box, (dl && !inlineTracker) ? dl : null);
                var active = dl && ['downloading', 'queued', 'searching'].indexOf(dl.status) > -1;
                if (active) container._activeT = setTimeout(tick, 1800);
            });
        })();
    }

    function renderActiveBanner(box, dl) {
        var show = dl && ['downloading', 'queued', 'searching', 'completed', 'failed'].indexOf(dl.status) > -1;
        if (!show) { box.hidden = true; box.innerHTML = ''; box._wired = false; return; }
        box.hidden = false;
        var st = dl.status, pct = Math.max(0, Math.min(100, dl.progress || 0));
        if (st === 'completed') pct = 100;
        box.className = 'vdl-active vdl-active--' + (st === 'completed' ? 'done' : (st === 'failed' ? 'fail' : 'active'));
        var label = st === 'completed' ? 'Downloaded' : st === 'failed' ? 'Download failed'
            : st === 'searching' ? 'Finding a release…' : st === 'queued' ? 'Queued' : 'Downloading';
        var ic = st === 'completed' ? '✓' : st === 'failed' ? '✕' : '⤓';
        var pctTxt = (st === 'downloading' || st === 'queued') ? pct + '%' : '';
        box.innerHTML =
            '<div class="vdl-active-fill" style="width:' + pct + '%"></div>' +
            '<div class="vdl-active-row">' +
                '<span class="vdl-active-ic">' + ic + '</span>' +
                '<span class="vdl-active-txt"><strong>' + esc(label) + '</strong>' +
                    (dl.release_title ? '<span class="vdl-active-rel"> · ' + esc(dl.release_title) + '</span>' : '') + '</span>' +
                '<span class="vdl-active-pct">' + pctTxt + '</span>' +
                '<button class="vdl-active-go" type="button" data-vdl-active-go>Track on Downloads ↗</button>' +
            '</div>';
        if (!box._wired) {
            box._wired = true;
            box.addEventListener('click', function (e) {
                if (e.target.closest('[data-vdl-active-go]')) gotoDownloads();
            });
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
        box.innerHTML = list.map(function (s) { return srcBlockHTML(s, false); }).join('');
    }

    // ── search + results ──────────────────────────────────────────────────────
    var SRC_LABEL = { remux: 'Remux', bluray: 'BluRay', 'web-dl': 'WEB-DL', webrip: 'WEBRip',
        hdtv: 'HDTV', dvd: 'DVD', cam: 'CAM', screener: 'Screener', workprint: 'Workprint' };
    var RES_LABEL = { '2160p': '4K', '1080p': '1080p', '720p': '720p', '480p': 'SD' };

    function _setScanning(rows, on) {
        rows.forEach(function (row) {
            if (!row) return;
            if (row.matches && row.matches('button')) { row.disabled = on; row.classList.toggle('vdl-btn--busy', on); return; }
            row.classList.toggle('vdl-src--scanning', on);
            var b = row.querySelector('[data-vdl-search]'); if (b) b.disabled = on;
            var a = row.querySelector('[data-vdl-auto]'); if (a) a.disabled = on;
            var s = row.querySelector('[data-vdl-status]');
            if (s) { s.textContent = on ? 'Searching' : 'Ready'; s.className = 'vdl-src-status' + (on ? ' vdl-src-status--scanning' : ''); }
        });
    }

    // Render the result cards (shared by the immediate mock path and live polling).
    function renderResults(resultsEl, params, rows, live, done, totalFiles) {
        rows = rows || [];
        if (!rows.length) {
            if (!done) {
                resultsEl.innerHTML = '<div class="vdl-res-loading"><span class="vdl-res-spin"></span>Searching ' + esc(scopeWord(params.scope)) + '…</div>';
            } else if (totalFiles > 0) {
                resultsEl.innerHTML = '<div class="vdl-res-empty">Soulseek returned ' + totalFiles + ' file' + (totalFiles === 1 ? '' : 's') +
                    ', but none are video releases — likely audio/other for this title. Try a different title or source.</div>';
            } else {
                resultsEl.innerHTML = '<div class="vdl-res-empty">No matching releases found.</div>';
            }
            return;
        }
        resultsEl._rows = rows; resultsEl._search = params;   // for the Grab button
        resultsEl.classList.toggle('vdl-res-noanim', !!live);   // live re-renders → no per-card blink
        var okN = rows.filter(function (r) { return r.accepted; }).length;
        var badge = !live ? '<span class="vdl-res-demo">demo data</span>'
            : done ? '<span class="vdl-res-live">● live</span>'
            : '<span class="vdl-res-searching"><span class="vdl-res-spin vdl-res-spin--sm"></span>searching…</span>';
        resultsEl.innerHTML =
            '<div class="vdl-res-head"><strong>' + rows.length + '</strong> result' + (rows.length === 1 ? '' : 's') +
                ' · <span class="vdl-res-okn">' + okN + ' meet your profile</span>' + badge + '</div>' +
            rows.map(resultCardHTML).join('');
    }

    // Start a search; for Soulseek, stream results in (poll like the music side —
    // results trickle in over ~30s, so a single short wait misses them).
    function searchInto(container, resultsEl, params, triggerRows, onDone) {
        if (!resultsEl) return;
        triggerRows = (triggerRows || []).filter(Boolean);
        if (resultsEl._poll) { clearTimeout(resultsEl._poll); resultsEl._poll = null; }
        resultsEl._rows = null;   // drop any prior search's rows so Auto can't grab a stale hit
        _setScanning(triggerRows, true);
        resultsEl.hidden = false;
        resultsEl.classList.remove('vdl-res-noanim');
        resultsEl.innerHTML = '<div class="vdl-res-loading"><span class="vdl-res-spin"></span>Searching ' + esc(scopeWord(params.scope)) + '…</div>';
        postJSON('/api/video/downloads/search/start', params).then(function (d) {
            if (!resultsEl.isConnected) { _setScanning(triggerRows, false); return; }
            if (d && d.error) { _setScanning(triggerRows, false); resultsEl.innerHTML = '<div class="vdl-res-empty vdl-res-err">⚠ ' + esc(d.error) + '</div>'; return; }
            if (!d || !d.id) {   // mock / immediate
                _setScanning(triggerRows, false);
                renderResults(resultsEl, params, d ? d.results : [], !!(d && d.live), true);
                if (onDone) onDone();
                return;
            }
            _pollSearch(resultsEl, params, d.id, triggerRows, d.poll_ms, onDone);
        });
    }

    function _pollSearch(resultsEl, params, id, triggerRows, pollMs, onDone) {
        // slskd keeps searching for the whole search_timeout (~60s) and results
        // trickle in over ~50s — poll that long (the music side does), streaming
        // results as they arrive. Stop early only once results clearly plateau.
        var started = Date.now(), lastN = -1, stable = 0, total = 0;
        var MAX_MS = Math.min(80000, pollMs || 60000);
        function tick() {
            if (!resultsEl.isConnected) { _setScanning(triggerRows, false); return; }
            var qs = '?id=' + encodeURIComponent(id) + '&scope=' + encodeURIComponent(params.scope || 'movie') +
                '&title=' + encodeURIComponent(params.title || '') +
                (params.season != null ? '&season=' + params.season : '') +
                (params.episode != null ? '&episode=' + params.episode : '');
            getJSON('/api/video/downloads/search/poll' + qs).then(function (d) {
                if (!resultsEl.isConnected) { _setScanning(triggerRows, false); return; }
                var rows = (d && d.results) || [];
                total = (d && d.total_files) || total;
                if (rows.length === lastN) { stable++; } else { stable = 0; lastN = rows.length; }
                var elapsed = Date.now() - started;
                // done = full timeout, OR plenty of results, OR results plateaued after ≥20s.
                var done = elapsed >= MAX_MS || rows.length >= 25 || (rows.length > 0 && elapsed > 20000 && stable >= 6);
                renderResults(resultsEl, params, rows, true, done, total);
                if (done) { _setScanning(triggerRows, false); resultsEl._poll = null; if (onDone) onDone(); }
                else { resultsEl._poll = setTimeout(tick, 1500); }
            });
        }
        tick();
    }

    // Build the /grab payload for a chosen release row `r` in `panel` (the results
    // element). Shared by the manual grab button and the auto-pick path so both
    // send an identical request (incl. the auto-retry candidate pool).
    function buildGrabPayload(panel, r) {
        var p = panel._search || {};
        var container = panel.closest('[data-vgm-dl-content]');
        var o = (container && (container._opts || container._dl)) || {};
        // the other accepted (live slskd) hits become the auto-retry pool
        var pool = (panel._rows || []).filter(function (x) { return x.accepted && x.username && x.filename !== r.filename; })
            .map(function (x) { return { username: x.username, filename: x.filename, size_bytes: x.size_bytes,
                quality_label: x.quality_label, title: x.title }; });
        return {
            kind: p.scope || 'movie', title: p.title || '', release_title: r.title,
            source: 'soulseek', username: r.username, filename: r.filename,
            size_bytes: r.size_bytes, quality_label: r.quality_label,
            media_id: o.id || o.mediaId, media_source: o.source || o.mediaSource,
            year: o.year, poster_url: o.poster,
            candidates: pool,
            search_ctx: { scope: p.scope || 'movie', title: p.title || '', year: o.year,
                season: p.season != null ? p.season : null, episode: p.episode != null ? p.episode : null }
        };
    }

    function sendGrab(payload) { return postJSON('/api/video/downloads/grab', payload); }

    // Grab → start a real download (Soulseek only for now), then it lives on the
    // Downloads page. Reads the card's row + the panel's search context.
    function doGrab(btn) {
        var panel = btn.closest('.vdl-results'); if (!panel || !panel._rows) return;
        var card = btn.closest('.vdl-res');
        var r = panel._rows[parseInt(btn.getAttribute('data-vdl-grab'), 10)]; if (!r) return;
        btn.disabled = true; btn.classList.add('vdl-res-grab--busy');
        sendGrab(buildGrabPayload(panel, r)).then(function (res) {
            if (res && res.ok) {
                toast('Sent to Downloads', 'success');
                beginTracking(card, res.id);   // selected card → live tracker + Track button
                document.dispatchEvent(new CustomEvent('soulsync:video-download-started'));
            } else {
                btn.disabled = false; btn.classList.remove('vdl-res-grab--busy');
                toast((res && res.error) || 'Couldn’t start the download', 'error');
            }
        });
    }

    // Auto-pick: after an auto-search settles, grab the BEST grabbable release.
    // Results arrive already ranked best-first (server sorts by accepted → score →
    // availability), so the first accepted hit with an uploader is the best pick.
    function _autoPick(panel, statusRow) {
        if (!panel || !panel.isConnected) return;
        var rows = panel._rows || [];
        var best = null, bestIdx = -1;
        for (var i = 0; i < rows.length; i++) {
            if (rows[i].accepted && rows[i].username) { best = rows[i]; bestIdx = i; break; }
        }
        var statusEl = statusRow && statusRow.querySelector('[data-vdl-status]');
        if (!best) {
            if (statusEl) { statusEl.textContent = 'No match'; statusEl.className = 'vdl-src-status vdl-src-status--none'; }
            toast('Auto: no release met your quality profile', 'error');
            return;
        }
        // Spotlight the card we're auto-grabbing so the choice is obvious, and
        // scroll it into view (it may be below the fold among many results).
        var card = panel.querySelector('[data-vdl-card="' + bestIdx + '"]');
        if (card) {
            card.classList.add('vdl-res--auto');
            if (card.scrollIntoView) card.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
        }
        var gbtn = card && card.querySelector('[data-vdl-grab]');
        if (gbtn) { gbtn.disabled = true; gbtn.classList.add('vdl-res-grab--busy'); }
        if (statusEl) { statusEl.textContent = 'Auto-grabbing'; statusEl.className = 'vdl-src-status vdl-src-status--scanning'; }
        toast('Auto-picked best: ' + (best.quality_label || best.title || 'release'), 'info');
        sendGrab(buildGrabPayload(panel, best)).then(function (res) {
            if (res && res.ok) {
                if (statusEl) { statusEl.textContent = 'Sent'; statusEl.className = 'vdl-src-status vdl-src-status--done'; }
                toast('Sent to Downloads', 'success');
                beginTracking(card, res.id);   // chosen card → live tracker + Track button
                document.dispatchEvent(new CustomEvent('soulsync:video-download-started'));
            } else {
                if (gbtn) { gbtn.disabled = false; gbtn.classList.remove('vdl-res-grab--busy'); }
                if (statusEl) { statusEl.textContent = 'Ready'; statusEl.className = 'vdl-src-status'; }
                toast((res && res.error) || 'Auto: couldn’t start the download', 'error');
            }
        });
    }

    function scopeWord(s) {
        return s === 'season' ? 'for the season pack' : s === 'series' ? 'for the full series'
            : s === 'episode' ? 'this episode' : 'for the movie';
    }

    function resKind(res) {
        return res === '2160p' ? '4k' : res === '1080p' ? '1080' : res === '720p' ? '720' : 'sd';
    }

    // Availability differs by source: slskd has a peer (uploader); torrent/usenet seeders.
    function resAvailHTML(r) {
        if (r.username) {
            return '<span class="vdl-res-stat vdl-res-seed"><span class="vdl-res-ico">👤</span>' + esc(r.username) +
                (r.peers > 1 ? ' · ' + r.peers + ' peers' : '') + (r.slots ? ' · ' + r.slots + ' slots' : '') + '</span>';
        }
        return '<span class="vdl-res-stat vdl-res-seed">▲ ' + (r.seeders || 0) + ' seeders</span>';
    }

    // Result card: a colour-coded resolution badge anchors the left; the headline is
    // a plain-English quality summary with the verdict pill; the raw release name is
    // demoted to a mono one-liner; a stat strip (size / uploader / group) sits below.
    // The card is a column so a live download tracker can drop in under it on grab.
    function resultCardHTML(r, i) {
        // Flat release-list row (Radarr/Prowlarr-style): a small quality tag leads,
        // the RELEASE NAME is the hero, dense inline meta below, size + verdict + Get
        // on the right. The outer .vdl-res stays a column so the live tracker docks.
        var sub = [];
        if (r.codec) sub.push(String(r.codec).toUpperCase());
        if (r.audio) sub.push(String(r.audio).toUpperCase().replace('-', ' '));
        if (r.hdr) sub.push(String(r.hdr).toUpperCase());
        if (r.repack) sub.push('REPACK');
        sub.push(r.username
            ? '👤 ' + r.username + (r.peers > 1 ? ' (' + r.peers + ')' : '')
            : (r.seeders || 0) + ' seeders');
        if (r.group) sub.push(r.group);
        var flag = r.accepted
            ? '<span class="vdl-flag vdl-flag--ok" title="Meets your quality profile">✓</span>'
            : '<span class="vdl-flag vdl-flag--no" title="' + esc(r.rejected || 'Filtered out') + '">✕</span>';
        var grab = (r.accepted && r.username)
            ? '<button class="vdl-res-grab" type="button" data-vdl-grab="' + i + '" title="Download this release">' +
                '<span class="vdl-res-grab-ic" aria-hidden="true">⤓</span><span>Get</span></button>'
            : '';
        var srcWord = SRC_LABEL[r.source] || r.source || '';
        return '<div class="vdl-res' + (r.accepted ? ' vdl-res--ok' : ' vdl-res--rejected') + '" data-vdl-card="' + i + '">' +
            '<div class="vdl-res-main">' +
                '<div class="vdl-q">' +
                    '<span class="vdl-q-res vdl-q-res--' + resKind(r.resolution) + '">' + esc(RES_LABEL[r.resolution] || r.resolution || '?') + '</span>' +
                    (srcWord ? '<span class="vdl-q-src">' + esc(srcWord) + '</span>' : '') +
                '</div>' +
                '<div class="vdl-info">' +
                    '<div class="vdl-info-title" title="' + esc(r.title) + '">' + esc(r.title) + '</div>' +
                    '<div class="vdl-info-sub">' + esc(sub.join('  ·  ')) + '</div>' +
                '</div>' +
                '<div class="vdl-size">' + esc(String(r.size_gb)) + '<span class="vdl-size-u">GB</span></div>' +
                flag + grab +
            '</div>' +
        '</div>';
    }

    // States the result-card tracker shows while a grabbed release downloads.
    var TRACK_LABEL = { downloading: 'Downloading', queued: 'Queued',
        searching: 'Finding another release…', completed: 'Downloaded', failed: 'Failed', cancelled: 'Cancelled' };
    var TRACK_DONE = { completed: 1, failed: 1, cancelled: 1 };

    // Close the modal (if any) and jump to the Downloads page.
    function gotoDownloads() {
        if (window.VideoGet && VideoGet.close) VideoGet.close();
        document.dispatchEvent(new CustomEvent('soulsync:video-navigate', { detail: 'video-downloads' }));
    }

    // After a grab, turn the chosen card into a live tracker: a progress bar that
    // follows the real download + a button that jumps to the Downloads page. Polls
    // /downloads/status?id= until the download reaches a terminal state.
    function beginTracking(card, dlId) {
        if (!card) return;
        card.classList.add('vdl-res--grabbed');
        var gb = card.querySelector('[data-vdl-grab]'); if (gb) gb.remove();
        var main = card.querySelector('.vdl-res-main') || card;
        var foot = card.querySelector('[data-vdl-track]');
        if (!foot) {
            foot = document.createElement('div');
            foot.className = 'vdl-res-track vdl-res-track--active';
            foot.setAttribute('data-vdl-track', '');
            foot.innerHTML =
                '<div class="vdl-res-track-head">' +
                    '<span class="vdl-res-track-state" data-vdl-track-state><span class="vdl-res-track-spin"></span>Starting…</span>' +
                    '<span class="vdl-res-track-pct" data-vdl-track-pct></span>' +
                    '<button class="vdl-res-track-go" type="button" data-vdl-track-go>Track on Downloads ↗</button>' +
                '</div>' +
                '<div class="vdl-res-track-bar"><span class="vdl-res-track-fill" data-vdl-track-fill></span></div>';
            if (main.nextSibling) card.insertBefore(foot, main.nextSibling); else card.appendChild(foot);
            var go = foot.querySelector('[data-vdl-track-go]');
            if (go) go.addEventListener('click', gotoDownloads);
        }
        _trackPoll(card, foot, dlId);
    }

    function _trackPoll(card, foot, dlId) {
        if (foot._t) { clearTimeout(foot._t); foot._t = null; }
        function tick() {
            if (!card.isConnected) return;   // modal closed → stop
            getJSON('/api/video/downloads/status?id=' + encodeURIComponent(dlId)).then(function (d) {
                if (!card.isConnected) return;
                var dl = d && d.download;
                if (!dl) { foot._t = setTimeout(tick, 2000); return; }
                var st = dl.status;
                var pct = Math.max(0, Math.min(100, dl.progress || 0));
                if (st === 'completed') pct = 100;
                var active = !(st in TRACK_DONE);
                foot.className = 'vdl-res-track vdl-res-track--' +
                    (st === 'completed' ? 'done' : (st === 'failed' || st === 'cancelled' ? 'fail' : 'active'));
                var fill = foot.querySelector('[data-vdl-track-fill]'); if (fill) fill.style.width = pct + '%';
                var pctEl = foot.querySelector('[data-vdl-track-pct]');
                if (pctEl) pctEl.textContent = (st === 'downloading' || st === 'queued') ? pct + '%' : '';
                var stEl = foot.querySelector('[data-vdl-track-state]');
                if (stEl) {
                    var spin = (st === 'downloading' || st === 'queued' || st === 'searching')
                        ? '<span class="vdl-res-track-spin"></span>' : '';
                    var ic = st === 'completed' ? '✓ ' : (st === 'failed' || st === 'cancelled' ? '✕ ' : '');
                    stEl.innerHTML = spin + ic + esc(TRACK_LABEL[st] || 'Downloading');
                }
                if (active) foot._t = setTimeout(tick, 1700);
            });
        }
        tick();
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
        var maxSeason = 0;
        (d.seasons || []).forEach(function (s) { if ((s.season_number || 0) > maxSeason) maxSeason = s.season_number; });
        var st = container._dl = {
            sel: new Set(), today: isoToday(),
            tvId: opts.tvId || d.tmdb_id || null, source: opts.source || 'library',
            sources: ['soulseek'], epMeta: {},
            title: d.title || opts.title || '', maxSeason: maxSeason,
            mediaId: opts.id, mediaSource: opts.source, poster: opts.poster || null, year: d.year || null
        };
        container.innerHTML =
            '<div class="vdl-section"><div class="vdl-sec-label">Quality target</div>' +
                '<div class="vdl-chips" data-vdl-target><span class="vdl-chip vdl-chip--ghost">Loading…</span></div></div>' +
            '<div class="vdl-show-bar">' +
                '<label class="vdl-allchk"><input type="checkbox" data-vdl-all><span class="vdl-allchk-txt">All</span></label>' +
                '<span class="vdl-show-summary" data-vdl-summary>Loading episodes…</span>' +
                '<button class="vdl-search-all" type="button" data-vdl-search-show>⌕ Search whole show</button>' +
            '</div>' +
            '<div class="vdl-results" data-vdl-show-results hidden></div>' +
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
            '<div class="vdl-season-body">' +
                '<div class="vdl-results vdl-results--season" data-vdl-season-results hidden></div>' +
                '<div class="vdl-season-eps"><div class="vdl-season-empty">Loading…</div></div>' +
            '</div>' +
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
        var container = e.currentTarget; var st = container._dl; if (!st) return;
        var grab = e.target.closest('[data-vdl-grab]');
        if (grab) { doGrab(grab); return; }
        // Episode-scope search (a source row inside an expanded episode → its own panel).
        var srch = e.target.closest('[data-vdl-search]');
        if (srch) {
            var epEl = srch.closest('.vdl-ep'); if (!epEl) return;
            var parts = (epEl.getAttribute('data-vdl-ep') || '').split('_');
            var s = srch.getAttribute('data-vdl-search');
            var block = srch.closest('[data-vdl-src-block]');
            searchInto(container, block.querySelector('[data-vdl-results-for="' + s + '"]'),
                { scope: 'episode', title: st.title, season: +parts[0], episode: +parts[1], source: s },
                [srch.closest('.vdl-src')]);
            return;
        }
        // Season-scope search → season PACK.
        var ss = e.target.closest('[data-vdl-season-search]');
        if (ss) {
            var sn = ss.getAttribute('data-vdl-season-search');
            var sc = container.querySelector('.vdl-season[data-vdl-season="' + sn + '"]');
            if (sc) sc.classList.add('vdl-season--open');
            searchInto(container, sc && sc.querySelector('[data-vdl-season-results]'),
                { scope: 'season', title: st.title, season: +sn }, [ss]);
            return;
        }
        // Whole-show search → complete-series pack.
        if (e.target.closest('[data-vdl-search-show]')) {
            searchInto(container, container.querySelector('[data-vdl-show-results]'),
                { scope: 'series', title: st.title, season_end: st.maxSeason || 5 },
                [e.target.closest('[data-vdl-search-show]')]);
            return;
        }
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
        panel.innerHTML = '<div class="vdl-ep-srcs">' + srcs.map(function (s) { return srcBlockHTML(s, true); }).join('') + '</div>';
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

    window.VideoDownload = { render: render };
})();
