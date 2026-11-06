/*
 * SoulSync — Download VIEW renderer (movie / TV show / YouTube).
 *
 * NOT its own modal: it renders the direct-download content INTO a container the
 * caller owns — the get-modal swaps its detail body for this view (with a Back
 * button) when you click "Download", and a future YouTube trigger can reuse it.
 *
 * Shows the quality TARGET (read from the Settings → Downloads profile), judges
 * any copy you ALREADY own against that target (via /downloads/evaluate), and
 * lists each attached source with a per-source Search that's fully live:
 * /downloads/search/start + poll, ranked against the profile, with Grab
 * (/downloads/grab) per release. (An earlier version of this header called the
 * searches stubs — long since real.)
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
        // The body is parsed on a FAILURE too. Every one of these endpoints puts
        // the reason in {ok:false, error:"..."} - "Only 2.5 GB free on the
        // temporary/working drive (C:\\Users\\...) - under your 5 GB minimum",
        // "Missing the release's download URL", "Set the library folder for this
        // type" - and `r.ok ? r.json() : null` threw all of it away, so five
        // different refusals arrived at the user as one bare "Grab failed" with
        // nothing to act on. Callers already read res.error; they were just
        // never given one.
        return fetch(url, { method: 'POST', headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
            body: JSON.stringify(body) }).then(function (r) {
                return r.json().then(function (d) {
                    if (r.ok) return d;
                    // keep whatever the server said; only invent a message when
                    // it sent none, so the user still learns something happened
                    return Object.assign({ ok: false, error: 'Request failed (HTTP ' + r.status + ')' }, d || {});
                }).catch(function () {
                    // not JSON at all (a proxy error page, an empty 502)
                    return r.ok ? null : { ok: false, error: 'Request failed (HTTP ' + r.status + ')' };
                });
            }).catch(function () { return null; });
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
                        '<button class="vdl-search-all vdl-auto-all" type="button" data-vdl-auto-best title="Search every source, then download the single best release for your quality profile">AUTO</button>' +
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
                '<button class="vdl-src-search" type="button" data-vdl-search="' + s + '" title="Search and pick a release yourself">MANUAL</button>' +
                '<button class="vdl-src-auto" type="button" data-vdl-auto="' + s + '" title="Search and auto-grab the best release for your quality profile">AUTO</button>' +
            '</span>' +
            '</div>';
    }
    function srcBlockHTML(s, mini) {
        return '<div class="vdl-src-block" data-vdl-src-block="' + s + '">' +
            srcRowHTML(s, mini) +
            '<div class="vdl-results" data-vdl-results-for="' + s + '" hidden></div>' +
        '</div>';
    }

    function _movieSearch(container, block, auto, onSettled) {
        var o = container._opts || {};
        var s = block.getAttribute('data-vdl-src-block');
        var resultsEl = block.querySelector('[data-vdl-results-for="' + s + '"]');
        var statusRow = block.querySelector('.vdl-src');
        // In per-source auto mode, grab this source's best when it settles; an
        // optional onSettled lets a coordinator (header Auto) wait for every source.
        var onDone = (auto || onSettled) ? function () {
            if (auto) _autoPick(resultsEl, statusRow);
            if (onSettled) onSettled(resultsEl);
        } : null;
        searchInto(container, resultsEl,
            { scope: 'movie', title: o.title || '', year: o.year || null, source: s },
            [statusRow], onDone);
    }

    // Header "Auto": search EVERY source, then download the single best release
    // across all of them (NOT one per source). Each source's results still show so
    // the pick is transparent; the winner gets the auto ring + live tracker.
    function _autoBest(container) {
        var blocks = Array.prototype.slice.call(container.querySelectorAll('[data-vdl-src-block]'));
        if (!blocks.length) return;
        var hb = container.querySelector('[data-vdl-auto-best]');
        if (hb) hb.disabled = true;
        var pending = blocks.length, done = false;
        blocks.forEach(function (block) {
            _movieSearch(container, block, false, function () {
                if (done) return;
                if (--pending > 0) return;
                done = true;
                _grabBestAcross(container, blocks, hb);
            });
        });
    }

    function _grabBestAcross(container, blocks, hb) {
        var best = null, bestPanel = null, bestIdx = -1;
        blocks.forEach(function (block) {
            var s = block.getAttribute('data-vdl-src-block');
            var panel = block.querySelector('[data-vdl-results-for="' + s + '"]');
            var rows = (panel && panel._rows) || [];
            for (var i = 0; i < rows.length; i++) {
                var r = rows[i];
                if (!(r.accepted && r.username)) continue;   // grabbable (Soulseek) + meets profile
                var avail = r.peers || r.seeders || 0;
                var bAvail = best ? (best.peers || best.seeders || 0) : -1;
                if (!best || (r.score || 0) > (best.score || 0) ||
                    ((r.score || 0) === (best.score || 0) && avail > bAvail)) {
                    best = r; bestPanel = panel; bestIdx = i;
                }
            }
        });
        if (hb) hb.disabled = false;
        if (!best) { toast('Auto: no release met your quality profile on any source', 'error'); return; }
        var card = bestPanel.querySelector('[data-vdl-card="' + bestIdx + '"]');
        if (card) {
            card.classList.add('vdl-res--auto');
            if (card.scrollIntoView) card.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
        }
        var gbtn = card && card.querySelector('[data-vdl-grab]');
        if (gbtn) { gbtn.disabled = true; gbtn.classList.add('vdl-res-grab--busy'); }
        toast('Auto-picked best across sources: ' + (best.quality_label || best.title || 'release'), 'info');
        sendGrab(buildGrabPayload(bestPanel, best)).then(function (res) {
            if (res && res.ok) {
                toast('Sent to Downloads', 'success');
                beginTracking(card, res.id);
                document.dispatchEvent(new CustomEvent('soulsync:video-download-started'));
            } else {
                if (gbtn) { gbtn.disabled = false; gbtn.classList.remove('vdl-res-grab--busy'); }
                toast((res && res.error) || 'Auto: couldn’t start the download', 'error');
            }
        });
    }

    function onClick(e) {
        var container = e.currentTarget;
        var grab = e.target.closest('[data-vdl-grab]');
        if (grab) { doGrab(grab); return; }
        if (e.target.closest('[data-vdl-auto-best]')) { _autoBest(container); return; }
        var ab = e.target.closest('[data-vdl-auto]');
        if (ab) { _movieSearch(container, ab.closest('[data-vdl-src-block]'), true); return; }
        var sb = e.target.closest('[data-vdl-search]');
        if (sb) { _movieSearch(container, sb.closest('[data-vdl-src-block]')); return; }
    }

    // Render the download view into `container`. Re-callable (resets each time).
    function render(container, opts) {
        if (!container) return;
        opts = opts || {};
        // a show reads its title off container._dl: an earlier movie's _opts left on a
        // reused modal would name that movie in this show's searches and grabs
        if (opts.kind === 'show') { container._opts = null; renderShow(container, opts); return; }
        container._opts = opts;
        container.innerHTML = contentHTML();
        if (!container._vdlWired) { container._vdlWired = true; container.addEventListener('click', onClick); }

        var isYt = !!opts.isYt;
        if (isYt) {
            getJSON('/api/video/downloads/youtube-quality')
                .then(function (p) { if (container.isConnected && p) renderTarget(container, p, true); });
        } else {
            loadTitleProfile(container, function () { return container._opts || {}; },
                opts.kind === 'show' ? 'show' : 'movie',
                function (p, info) { renderTarget(container, p, false, info); });
        }
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

    // ── the title's quality profile (picked here, kept on the title) ──────────
    // Seadog's request: a new title had no assignment, so Get / Manual search
    // judged it by the Default with no way to pick "Movie 4K". The pick becomes
    // the title's own profile: this search and grab, its wishlist rows, and its
    // library row when it lands (POST /downloads/quality/choose).
    function titleQS(o, kind) {
        o = o || {};
        var q = ['kind=' + encodeURIComponent(kind || 'movie')];
        var id = o.id || o.mediaId, src = o.source || o.mediaSource, tmdb = o.tmdbId || o.tvId;
        if (id != null && id !== '') q.push('media_id=' + encodeURIComponent(id));
        if (src) q.push('media_source=' + encodeURIComponent(src));
        if (tmdb != null && tmdb !== '') q.push('tmdb_id=' + encodeURIComponent(tmdb));
        return q.join('&');
    }
    function titleBody(o, kind, profileId) {
        o = o || {};
        return { kind: kind || 'movie', media_id: o.id || o.mediaId || null,
            media_source: o.source || o.mediaSource || null, tmdb_id: o.tmdbId || o.tvId || null,
            profile_id: profileId };
    }
    function profilePickerHTML(info) {
        if (!info || !info.profiles || info.profiles.length < 2) return '';
        var kindDefault = null;
        info.profiles.forEach(function (p) { if (p.id === info.kind_default_id) kindDefault = p; });
        var cur = info.assigned ? info.profile_id : 0;
        var opts = info.profiles.map(function (p) {
            var label = p.id === 0
                ? (kindDefault && kindDefault.id !== 0 ? 'Default (' + kindDefault.name + ')' : 'Default')
                : p.name;
            return '<option value="' + p.id + '"' + (p.id === cur ? ' selected' : '') + '>' + esc(label) + '</option>';
        }).join('');
        return '<label class="vdl-prof" title="The quality profile this title is searched, grabbed and upgraded by">' +
            '<span class="vdl-prof-lab">Profile</span>' +
            '<select class="vdl-prof-sel" data-vdl-profile>' + opts + '</select></label>';
    }
    // fetch the title's profile, render it, and keep the picker's change wired.
    // onChanged runs after a pick is saved (the overlay re-runs its searches)
    function loadTitleProfile(host, getIdent, kind, render, onChanged) {
        var url = '/api/video/downloads/quality/for?' + titleQS(getIdent(), kind);
        getJSON(url).then(function (info) {
            if (!host.isConnected) return;
            if (info && info.profile) { render(info.profile, info); return; }
            // an older server without /quality/for: the classic Default, no picker
            getJSON('/api/video/downloads/quality').then(function (p) { if (host.isConnected && p) render(p, null); });
        });
        if (host._vdlProfWired) return;
        host._vdlProfWired = true;
        host.addEventListener('change', function (e) {
            var sel = e.target.closest && e.target.closest('[data-vdl-profile]');
            if (!sel) return;
            var pid = parseInt(sel.value, 10) || 0;
            sel.disabled = true;
            postJSON('/api/video/downloads/quality/choose', titleBody(getIdent(), kind, pid)).then(function (res) {
                sel.disabled = false;
                if (!res || res.success === false) {
                    toast((res && res.error) || 'Could not change the quality profile', 'error');
                    loadTitleProfile(host, getIdent, kind, render, onChanged);
                    return;
                }
                toast('Quality profile saved for this title', 'success');
                loadTitleProfile(host, getIdent, kind, render, onChanged);
                if (onChanged) onChanged();
            });
        });
    }

    function renderTarget(container, p, isYt, info) {
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
        box.innerHTML = (isYt ? '' : profilePickerHTML(info)) + chips.join('');
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
        resultsEl._render = { params: params, live: live, done: done, totalFiles: totalFiles };
        resultsEl.classList.toggle('vdl-res-noanim', !!live);   // live re-renders → no per-card blink
        var okN = rows.filter(function (r) { return r.accepted; }).length;
        var badge = !live ? '<span class="vdl-res-demo">demo data</span>'
            : done ? '<span class="vdl-res-live">● live</span>'
            : '<span class="vdl-res-searching"><span class="vdl-res-spin vdl-res-spin--sm"></span>searching…</span>';
        // the toolbar is built once per search and kept, so typing in it never
        // loses focus to a re-render; only the head and the cards are redrawn
        var list = resultsEl.querySelector('[data-vdl-res-list]');
        if (!list || !resultsEl.querySelector('[data-vdl-rf]')) {
            resultsEl.innerHTML = '<div class="vdl-res-head" data-vdl-res-head></div>' + _rfBarHTML() +
                '<div class="vdl-res-list" data-vdl-res-list></div>';
            list = resultsEl.querySelector('[data-vdl-res-list]');
            _rfWire(resultsEl.querySelector('[data-vdl-rf]'));
        }
        _rfSync(resultsEl.querySelector('[data-vdl-rf]'));
        var shown = window.VideoSearchFilters
            ? window.VideoSearchFilters.view(rows, _rf)
            : rows.map(function (row, index) { return { row: row, index: index }; });
        var narrowed = shown.length !== rows.length;
        resultsEl.querySelector('[data-vdl-res-head]').innerHTML =
            '<strong>' + rows.length + '</strong> result' + (rows.length === 1 ? '' : 's') +
            ' · <span class="vdl-res-okn">' + okN + ' meet your profile</span>' +
            (narrowed ? ' · <span class="vdl-res-shown">' + shown.length + ' match your filter</span>' : '') + badge;
        list.innerHTML = shown.length
            ? shown.map(function (it) { return resultCardHTML(it.row, it.index); }).join('')
            : '<div class="vdl-res-empty">No release matches your filter. <button type="button" class="vdl-rf-link" data-vdl-rf-clear>Show all</button></div>';
        // a card that was grabbed keeps its live tracker through a re-sort
        Object.keys(resultsEl._grabbed || {}).forEach(function (idx) {
            var card = list.querySelector('[data-vdl-card="' + idx + '"]');
            if (card && !card.querySelector('[data-vdl-track]')) beginTracking(card, resultsEl._grabbed[idx]);
        });
    }

    // ── result filters + sorting (SeadogsBooty) ──────────────────────────────
    // One state for every results panel in the modal, so a filter picked on one
    // episode applies to the next search too. The sort and the picked saved
    // filter are remembered on this browser; the filters themselves are saved
    // per profile on the server. Display only: Auto and the wishlist never
    // read any of it.
    var RF_KEY = 'ss.video.search.view';
    var _rfSaved = null;           // this profile's saved filters, once fetched
    var _rf = (function () {
        var st = { text: '', sort: 'rank', dir: 'desc', filter: null, filterName: '' };
        try {
            var saved = JSON.parse(localStorage.getItem(RF_KEY) || 'null');
            if (saved && typeof saved === 'object') {
                if (saved.sort) st.sort = saved.sort;
                if (saved.dir === 'asc' || saved.dir === 'desc') st.dir = saved.dir;
                if (saved.filter && Array.isArray(saved.filter.conditions)) st.filter = saved.filter;
                st.filterName = saved.filterName || '';
            }
        } catch (e) { /* private window: defaults */ }
        return st;
    })();
    // the direction a sort starts in: smallest, newest and A-Z first, else highest first
    var RF_ASC_FIRST = { size_gb: 1, age_days: 1, indexer: 1 };

    function _rfRemember() {
        try {
            localStorage.setItem(RF_KEY, JSON.stringify({ sort: _rf.sort, dir: _rf.dir,
                filter: _rf.filter, filterName: _rf.filterName }));
        } catch (e) { /* not stored, still applied */ }
    }
    function _rfApplyAll() {
        _rfRemember();
        document.querySelectorAll('.vdl-results').forEach(function (el) {
            if (el._rows && el._render && el.querySelector('[data-vdl-rf]')) {
                var r = el._render;
                renderResults(el, r.params, el._rows, r.live, r.done, r.totalFiles);
            }
        });
    }
    function _rfLoadSaved() {
        if (_rfSaved) return Promise.resolve(_rfSaved);
        return getJSON('/api/video/search-filters').then(function (d) {
            _rfSaved = (d && d.filters) || [];
            document.querySelectorAll('[data-vdl-rf]').forEach(_rfSync);
            return _rfSaved;
        });
    }
    function _rfPutSaved(list) {
        return fetch('/api/video/search-filters', { method: 'PUT',
            headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
            body: JSON.stringify({ filters: list }) })
            .then(function (r) { return r.ok ? r.json() : null; })
            .then(function (d) {
                if (!d) { toast('Could not save the filter', 'error'); return null; }
                _rfSaved = d.filters || [];
                return _rfSaved;
            }).catch(function () { toast('Could not save the filter', 'error'); return null; });
    }

    function _rfBarHTML() {
        var F = window.VideoSearchFilters;
        var sorts = F ? F.SORTS : { rank: 'Best match' };
        return '<div class="vdl-rf" data-vdl-rf>' +
            '<div class="vdl-rf-row">' +
                '<input type="search" class="vdl-rf-q" data-vdl-rf-q placeholder="Filter by name, group, indexer…" aria-label="Filter results">' +
                '<select class="vdl-rf-sel" data-vdl-rf-saved aria-label="Saved filter"></select>' +
                '<select class="vdl-rf-sel" data-vdl-rf-sort aria-label="Sort by">' +
                    Object.keys(sorts).map(function (k) { return '<option value="' + k + '">Sort: ' + esc(sorts[k]) + '</option>'; }).join('') +
                '</select>' +
                '<button type="button" class="vdl-rf-btn vdl-rf-dir" data-vdl-rf-dir aria-label="Reverse the order"></button>' +
                '<button type="button" class="vdl-rf-btn" data-vdl-rf-edit aria-expanded="false">Filters</button>' +
            '</div>' +
            '<div class="vdl-rf-builder" data-vdl-rf-builder hidden></div>' +
        '</div>';
    }

    // bring a toolbar's controls in line with the shared state (another panel
    // may have changed it); never touches the box someone is typing in
    function _rfSync(bar) {
        if (!bar) return;
        var q = bar.querySelector('[data-vdl-rf-q]');
        if (q && document.activeElement !== q && q.value !== _rf.text) q.value = _rf.text;
        var sort = bar.querySelector('[data-vdl-rf-sort]'); if (sort) sort.value = _rf.sort;
        var dir = bar.querySelector('[data-vdl-rf-dir]');
        if (dir) {
            dir.textContent = _rf.dir === 'asc' ? '↑' : '↓';
            dir.title = _rf.sort === 'rank' ? 'Best match first or last' : (_rf.dir === 'asc' ? 'Lowest first' : 'Highest first');
        }
        var sel = bar.querySelector('[data-vdl-rf-saved]');
        if (sel) {
            var names = (_rfSaved || []).map(function (f) { return f.name; });
            var custom = _rf.filter && !(_rf.filterName && names.indexOf(_rf.filterName) !== -1);
            sel.innerHTML = '<option value="">All releases</option>' +
                names.map(function (n) { return '<option value="' + esc(n) + '">' + esc(n) + '</option>'; }).join('') +
                (custom ? '<option value="__custom">Unsaved filter</option>' : '') +
                '<option value="__new">New filter…</option>';
            sel.value = custom ? '__custom' : (_rf.filter ? _rf.filterName : '');
        }
        var edit = bar.querySelector('[data-vdl-rf-edit]');
        if (edit) {
            var n = _rf.filter ? (_rf.filter.conditions || []).length : 0;
            edit.textContent = n ? 'Filters · ' + n : 'Filters';
            edit.classList.toggle('is-on', !!n);
        }
    }

    function _rfCondHTML(c) {
        var F = window.VideoSearchFilters;
        var field = F.FIELDS[c.field] ? c.field : 'title';
        var ops = F.opsFor(field);
        var op = ops.indexOf(c.op) !== -1 ? c.op : ops[0];
        var kind = F.FIELDS[field].kind;
        var input = kind === 'bool'
            ? '<select class="vdl-rf-sel" data-rf-value><option value="yes"' + (c.value === 'no' ? '' : ' selected') + '>yes</option>' +
              '<option value="no"' + (c.value === 'no' ? ' selected' : '') + '>no</option></select>'
            : '<input class="vdl-rf-in" data-rf-value type="' + (kind === 'number' ? 'number' : 'text') + '" step="any"' +
              ' value="' + esc(c.value || '') + '" placeholder="' + esc(F.FIELDS[field].hint || '') + '">';
        return '<div class="vdl-rf-cond" data-rf-cond>' +
            '<select class="vdl-rf-sel" data-rf-field>' + Object.keys(F.FIELDS).map(function (k) {
                return '<option value="' + k + '"' + (k === field ? ' selected' : '') + '>' + esc(F.FIELDS[k].label) + '</option>';
            }).join('') + '</select>' +
            '<select class="vdl-rf-sel" data-rf-op>' + ops.map(function (o) {
                return '<option value="' + o + '"' + (o === op ? ' selected' : '') + '>' + esc(F.OPS[o].label) + '</option>';
            }).join('') + '</select>' +
            input +
            '<button type="button" class="vdl-rf-x" data-rf-remove aria-label="Remove condition">×</button>' +
        '</div>';
    }

    function _rfOpenBuilder(bar, draft) {
        var box = bar.querySelector('[data-vdl-rf-builder]');
        if (!box || !window.VideoSearchFilters) return;
        draft = draft || { name: '', match: 'all', conditions: [] };
        if (!draft.conditions.length) draft.conditions = [{ field: 'title', op: 'contains', value: '' }];
        var isSaved = draft.name && (_rfSaved || []).some(function (f) { return f.name === draft.name; });
        box.innerHTML =
            '<div class="vdl-rf-match">Show releases matching ' +
                '<select class="vdl-rf-sel" data-rf-match><option value="all"' + (draft.match === 'any' ? '' : ' selected') + '>all</option>' +
                '<option value="any"' + (draft.match === 'any' ? ' selected' : '') + '>any</option></select> of these</div>' +
            '<div class="vdl-rf-conds" data-rf-conds>' + draft.conditions.map(_rfCondHTML).join('') + '</div>' +
            '<button type="button" class="vdl-rf-link" data-rf-add>+ Add a condition</button>' +
            '<div class="vdl-rf-foot">' +
                '<input class="vdl-rf-in vdl-rf-name" data-rf-name maxlength="60" placeholder="Name it to save it" value="' + esc(draft.name || '') + '">' +
                '<button type="button" class="vdl-rf-btn is-primary" data-rf-apply>Apply</button>' +
                '<button type="button" class="vdl-rf-btn" data-rf-save>Save</button>' +
                (isSaved ? '<button type="button" class="vdl-rf-btn is-danger" data-rf-delete>Delete</button>' : '') +
                '<button type="button" class="vdl-rf-btn" data-rf-close>Close</button>' +
            '</div>';
        box.hidden = false;
        var edit = bar.querySelector('[data-vdl-rf-edit]'); if (edit) edit.setAttribute('aria-expanded', 'true');
    }
    function _rfCloseBuilder(bar) {
        var box = bar.querySelector('[data-vdl-rf-builder]');
        if (box) { box.hidden = true; box.innerHTML = ''; }
        var edit = bar.querySelector('[data-vdl-rf-edit]'); if (edit) edit.setAttribute('aria-expanded', 'false');
    }
    function _rfReadBuilder(bar) {
        var box = bar.querySelector('[data-vdl-rf-builder]');
        var conds = [];
        box.querySelectorAll('[data-rf-cond]').forEach(function (row) {
            var v = row.querySelector('[data-rf-value]');
            conds.push({ field: row.querySelector('[data-rf-field]').value, op: row.querySelector('[data-rf-op]').value,
                value: v ? String(v.value || '').trim() : '' });
        });
        var match = box.querySelector('[data-rf-match]');
        var name = box.querySelector('[data-rf-name]');
        return { name: name ? name.value.trim() : '', match: match && match.value === 'any' ? 'any' : 'all',
            conditions: conds.filter(function (c) {
                var kind = (window.VideoSearchFilters.FIELDS[c.field] || {}).kind;
                return kind === 'bool' || c.value !== '';
            }) };
    }

    document.addEventListener('click', function (e) {
        if (e.target && e.target.closest && e.target.closest('[data-vdl-rf-clear]')) {
            _rf.text = ''; _rf.filter = null; _rf.filterName = '';
            _rfApplyAll();
        }
    });

    function _rfWire(bar) {
        if (!bar || bar._rfWired) return;
        bar._rfWired = true;
        _rfLoadSaved();
        var qTimer = null;
        bar.addEventListener('input', function (e) {
            if (e.target.matches('[data-vdl-rf-q]')) {
                var text = e.target.value;
                clearTimeout(qTimer);
                qTimer = setTimeout(function () { _rf.text = text; _rfApplyAll(); }, 160);
            }
        });
        bar.addEventListener('change', function (e) {
            var t = e.target;
            if (t.matches('[data-vdl-rf-sort]')) {
                _rf.sort = t.value;
                _rf.dir = RF_ASC_FIRST[t.value] ? 'asc' : 'desc';
                _rfApplyAll();
            } else if (t.matches('[data-vdl-rf-saved]')) {
                if (t.value === '__new') { _rfOpenBuilder(bar, null); _rfSync(bar); return; }
                if (t.value === '__custom') return;
                var f = (_rfSaved || []).filter(function (x) { return x.name === t.value; })[0];
                _rf.filter = f ? { match: f.match, conditions: f.conditions } : null;
                _rf.filterName = f ? f.name : '';
                _rfCloseBuilder(bar);
                _rfApplyAll();
            } else if (t.matches('[data-rf-field]')) {
                // a new field brings the operators and the input that fit it
                var row = t.closest('[data-rf-cond]');
                var tmp = document.createElement('div');
                tmp.innerHTML = _rfCondHTML({ field: t.value, op: '', value: '' });
                row.replaceWith(tmp.firstChild);
            }
        });
        bar.addEventListener('click', function (e) {
            var t = e.target;
            if (t.closest('[data-vdl-rf-dir]')) { _rf.dir = _rf.dir === 'asc' ? 'desc' : 'asc'; _rfApplyAll(); return; }
            if (t.closest('[data-vdl-rf-edit]')) {
                var box = bar.querySelector('[data-vdl-rf-builder]');
                if (box && !box.hidden) { _rfCloseBuilder(bar); return; }
                _rfOpenBuilder(bar, _rf.filter ? { name: _rf.filterName, match: _rf.filter.match,
                    conditions: _rf.filter.conditions.map(function (c) { return Object.assign({}, c); }) } : null);
                return;
            }
            if (t.closest('[data-rf-add]')) {
                var conds = bar.querySelector('[data-rf-conds]');
                var tmp = document.createElement('div');
                tmp.innerHTML = _rfCondHTML({ field: 'title', op: 'contains', value: '' });
                conds.appendChild(tmp.firstChild);
                return;
            }
            if (t.closest('[data-rf-remove]')) { t.closest('[data-rf-cond]').remove(); return; }
            if (t.closest('[data-rf-close]')) { _rfCloseBuilder(bar); return; }
            if (t.closest('[data-rf-apply]')) {
                var d = _rfReadBuilder(bar);
                _rf.filter = d.conditions.length ? { match: d.match, conditions: d.conditions } : null;
                _rf.filterName = d.conditions.length ? d.name : '';
                _rfApplyAll();
                return;
            }
            if (t.closest('[data-rf-save]')) {
                var draft = _rfReadBuilder(bar);
                if (!draft.name) { toast('Give the filter a name to save it', 'error'); return; }
                if (!draft.conditions.length) { toast('Add a condition with a value first', 'error'); return; }
                var list = (_rfSaved || []).filter(function (f) { return f.name.toLowerCase() !== draft.name.toLowerCase(); });
                list.push(draft);
                _rfPutSaved(list).then(function (saved) {
                    if (!saved) return;
                    _rf.filter = { match: draft.match, conditions: draft.conditions };
                    _rf.filterName = draft.name;
                    toast('Saved "' + draft.name + '"', 'success');
                    _rfCloseBuilder(bar);
                    _rfApplyAll();
                });
                return;
            }
            if (t.closest('[data-rf-delete]')) {
                var gone = _rfReadBuilder(bar).name;
                _rfPutSaved((_rfSaved || []).filter(function (f) { return f.name !== gone; })).then(function (saved) {
                    if (!saved) return;
                    if (_rf.filterName === gone) { _rf.filter = null; _rf.filterName = ''; }
                    _rfCloseBuilder(bar);
                    _rfApplyAll();
                });
            }
        });
    }

    // Start a search; for Soulseek, stream results in (poll like the music side —
    // results trickle in over ~30s, so a single short wait misses them).
    // the title this panel searches for, so the server judges results by ITS
    // quality profile and asks the indexers by its ids (the wishlist search's
    // way). episode, season and series searches sent neither, so they were
    // judged by the Default profile on a title-only search.
    function withIdentity(container, resultsEl, params) {
        if (params.media_id != null || params.tmdb_id != null) return params;
        var host = resultsEl.closest('[data-vgm-dl-content]');
        var o = (host && host._opts) || (container && (container._opts || container._dl)) || {};
        var extra = {};
        var id = o.id || o.mediaId;
        if (id) { extra.media_id = id; extra.media_source = o.source || o.mediaSource || 'library'; }
        var tmdb = o.tmdbId || o.tvId;
        if (tmdb) extra.tmdb_id = tmdb;
        return Object.keys(extra).length ? Object.assign({}, params, extra) : params;
    }

    function searchInto(container, resultsEl, params, triggerRows, onDone) {
        if (!resultsEl) return;
        params = withIdentity(container, resultsEl, params);
        triggerRows = (triggerRows || []).filter(Boolean);
        if (resultsEl._poll) { clearTimeout(resultsEl._poll); resultsEl._poll = null; }
        resultsEl._rows = null;   // drop any prior search's rows so Auto can't grab a stale hit
        resultsEl._grabbed = null;   // a new search's cards are new cards
        _setScanning(triggerRows, true);
        resultsEl.hidden = false;
        resultsEl.classList.remove('vdl-res-noanim');
        resultsEl.innerHTML = '<div class="vdl-res-loading"><span class="vdl-res-spin"></span>Searching ' + esc(scopeWord(params.scope)) + '…</div>';
        // every way a search can end calls onDone. the error and closed-panel
        // exits used to return without it, so Grab season's per-episode promise
        // never settled: the row sat on Searching forever, nothing reached the
        // client and the pump never freed its slot (SeadogsBooty, MobLand S1)
        resultsEl._error = null;
        postJSON('/api/video/downloads/search/start', params).then(function (d) {
            if (!resultsEl.isConnected) { _setScanning(triggerRows, false); if (onDone) onDone(); return; }
            if (d && d.error) {
                _setScanning(triggerRows, false);
                resultsEl._error = d.error;
                resultsEl.innerHTML = '<div class="vdl-res-empty vdl-res-err">⚠ ' + esc(d.error) + '</div>';
                if (onDone) onDone();
                return;
            }
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
            if (!resultsEl.isConnected) { _setScanning(triggerRows, false); if (onDone) onDone(); return; }
            var qs = '?id=' + encodeURIComponent(id) + '&scope=' + encodeURIComponent(params.scope || 'movie') +
                '&title=' + encodeURIComponent(params.title || '') +
                (params.season != null ? '&season=' + params.season : '') +
                (params.episode != null ? '&episode=' + params.episode : '') +
                (params.media_id != null ? '&media_id=' + encodeURIComponent(params.media_id) +
                    '&media_source=' + encodeURIComponent(params.media_source || '') : '') +
                (params.tmdb_id != null ? '&tmdb_id=' + encodeURIComponent(params.tmdb_id) : '');
            getJSON('/api/video/downloads/search/poll' + qs).then(function (d) {
                if (!resultsEl.isConnected) { _setScanning(triggerRows, false); if (onDone) onDone(); return; }
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
        // The DOWNLOAD source this panel searched...
        var src = p.source || 'soulseek';
        // ...except a torrent search returns Prowlarr AND EXT.to rows together,
        // and they are not grabbed the same way: an EXT.to row carries an
        // info_url and no magnet yet, so it has to reach the backend labelled
        // 'extto' or the magnet is never resolved.
        // Keyed on indexer_id, NOT on r.source: a result row's `source` is the
        // RELEASE source parsed out of the filename - "BluRay", "WEB-DL" - and
        // sending that as the download source makes every grab fail with
        // "Unsupported download source".
        if (String(r.indexer_id || '').toLowerCase() === 'extto') src = 'extto';
        var container = panel.closest('[data-vgm-dl-content]');
        var o = (container && (container._opts || container._dl)) || {};
        var payload = {
            kind: p.scope || 'movie', title: p.title || '', release_title: r.title,
            source: src, size_bytes: r.size_bytes, quality_label: r.quality_label,
            media_id: o.id || o.mediaId, media_source: o.source || o.mediaSource,
            year: o.year, poster_url: o.poster,
            search_ctx: { scope: p.scope || 'movie', title: p.title || '', year: o.year,
                season: p.season != null ? p.season : null, episode: p.episode != null ? p.episode : null }
        };
        if (src === 'soulseek') {
            // the other accepted (live slskd) hits become the auto-retry pool
            payload.username = r.username;
            payload.filename = r.filename;
            payload.candidates = (panel._rows || []).filter(function (x) { return x.accepted && x.username && x.filename !== r.filename; })
                .map(function (x) { return { username: x.username, filename: x.filename, size_bytes: x.size_bytes,
                    quality_label: x.quality_label, title: x.title }; });
        } else {
            // torrent / usenet — hand the magnet/NZB carriers to the backend client (no slskd requery)
            payload.download_url = r.download_url;
            payload.magnet_uri = r.magnet_uri;   // #1139 fallback if the .torrent fetch fails
            payload.protocol = r.protocol;
            payload.indexer_id = r.indexer_id;
            payload.guid = r.guid;
            payload.username = r.username;          // indexer name (display only)
            payload.filename = r.filename || r.title;
            payload.candidates = [];
            // What EXT.to resolves its magnet FROM, at grab time, for the one
            // release actually picked. Harmless on a Prowlarr row: undefined.
            payload.info_url = r.info_url;
            payload.magnet_id = r.magnet_id;
        }
        return payload;
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
                var _ep = panel.closest && panel.closest('.vdl-ep');
                if (_ep) epTrack(_ep, res.id);   // also light the collapsed episode row
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
                var _ep = panel.closest && panel.closest('.vdl-ep');
                if (_ep) epTrack(_ep, res.id);   // also light the collapsed episode row
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
    function _pk2(n) { n = String(n); return n.length < 2 ? '0' + n : n; }
    function _baseName(p) { p = String(p || '').replace(/\\/g, '/'); return p.slice(p.lastIndexOf('/') + 1); }
    function _gb(b) { return (Math.round(((b || 0) / (1024 * 1024 * 1024)) * 10) / 10) + ' GB'; }

    // Season/complete pack — an expandable card listing the folder's episode files,
    // with one [ GET PACK ] that fans them all out into per-episode downloads.
    function _packCardHTML(r, i) {
        var files = (r.files || []).slice().sort(function (a, b) {
            return String(a.filename).localeCompare(String(b.filename));
        });
        var rows = files.map(function (f) {
            var m = /[Ss](\d{1,2})[\s._-]*[Ee](\d{1,2})/.exec(_baseName(f.filename));
            var tag = m ? ('S' + _pk2(m[1]) + 'E' + _pk2(m[2])) : '·';
            return '<div class="vdl-pack-ep">' +
                '<span class="vdl-pack-ep-tag">' + tag + '</span>' +
                '<span class="vdl-pack-ep-name" title="' + esc(_baseName(f.filename)) + '">' + esc(_baseName(f.filename)) + '</span>' +
                '<span class="vdl-pack-ep-sz">' + _gb(f.size_bytes) + '</span></div>';
        }).join('');
        var getBtn = r.username
            ? '<button class="vdl-res-grab vdl-pack-get' + (r.accepted ? '' : ' vdl-res-grab--override') + '" type="button" data-vdl-grab-pack="' + i +
                '" title="' + (r.accepted ? 'Download every episode in this pack' : 'Below your quality profile — download anyway') + '">[ GET PACK ]</button>'
            : '';
        return '<div class="vdl-res vdl-pack' + (r.accepted ? ' vdl-res--ok' : ' vdl-res--rejected') + '" data-vdl-card="' + i + '">' +
            '<div class="vdl-pack-head" data-vdl-pack-toggle="' + i + '">' +
                '<span class="vdl-pack-caret" aria-hidden="true">▸</span>' +
                '<span class="vdl-r-q vdl-r-q--' + resKind(r.resolution) + '">' + esc(RES_LABEL[r.resolution] || r.resolution || '?') + '</span>' +
                '<span class="vdl-r-title" title="' + esc(r.title) + '">' + esc(r.title) + '</span>' +
                '<span class="vdl-pack-meta">' + r.file_count + ' ep' + (r.file_count === 1 ? '' : 's') + ' · ' + _gb(r.folder_size_bytes) + '</span>' +
                getBtn +
            '</div>' +
            '<div class="vdl-pack-list">' + rows + '</div>' +
        '</div>';
    }

    // Is this result a whole season rather than one episode? Soulseek says so by
    // handing over the folder's file list; a torrent/usenet release only has its
    // NAME, so 'S03' with no episode number is the tell. Without this a torrent
    // season pack rendered as an ordinary release row with nothing marking it as a
    // pack — you could grab it, but nothing said what you were grabbing.
    function _isSeasonPack(r) {
        if (r && r.files && r.files.length > 1) return true;
        var t = String((r && (r.title || r.filename)) || '');
        if (/[Ss]\d{1,2}[\s._-]*[Ee]\d{1,3}/.test(t)) return false;   // names an episode
        if (/\b\d{4}[\s._-]\d{2}[\s._-]\d{2}\b/.test(t)) return false;  // a dated daily
        // The season token must start a word: bare /S\d/ also matches the '5' in an
        // audio tag like 'DTS5.1', which would file a MOVIE as a season pack.
        return /[Ss]eason[\s._-]*\d{1,2}|(?:^|[\s._\-[(])[Ss]\d{1,2}(?![\dEe])/.test(t);
    }

    function resultCardHTML(r, i) {
        if (_isSeasonPack(r)) return _packCardHTML(r, i);
        // Flat / brutalist release card: a bracketed quality block + release name on
        // line 1, an UPPERCASE dot-separated spec line, then the verdict + a hard
        // [ GET ] button. Monospace, sharp, no chrome. .vdl-res stays a column so the
        // live download tracker docks under it on grab.
        var meta = [SRC_LABEL[r.source] || r.source || ''];
        if (r.codec) meta.push(String(r.codec).toUpperCase());
        if (r.audio) meta.push(String(r.audio).toUpperCase().replace('-', ' '));
        if (r.hdr) meta.push(String(r.hdr).toUpperCase());
        if (r.repack) meta.push('REPACK');
        meta.push(r.username ? r.username + (r.peers > 1 ? ' (' + r.peers + ')' : '') : (r.seeders || 0) + ' SEED');
        if (r.group) meta.push(r.group);
        meta.push(r.size_gb + ' GB');
        var formatNames = Array.isArray(r.formats) ? r.formats.filter(Boolean) : [];
        var formatNote = formatNames.length
            ? '<div class="vdl-r-formats" title="' + esc(formatNames.join(', ')) + '">' +
                '<span>Formats</span> ' + esc(formatNames.join(' · ')) +
                (r.format_score ? ' <b>' + (r.format_score > 0 ? '+' : '') + esc(r.format_score) + '</b>' : '') +
              '</div>'
            : (r.format_score ? '<div class="vdl-r-formats"><span>Format score</span> ' +
                (r.format_score > 0 ? '+' : '') + esc(r.format_score) + '</div>' : '');
        var isBest = i === 0 && r.accepted;
        // the verdict is the short answer, the note says why, once. the verdict
        // used to shout the whole reason in caps and the note repeat it
        var verdict = r.accepted
            ? '<span class="vdl-r-verdict vdl-r-verdict--ok">&#10003; MEETS PROFILE</span>'
            : '<span class="vdl-r-verdict vdl-r-verdict--no" title="' + esc(r.rejected || '') + '">&#10007; BELOW YOUR PROFILE</span>';
        var why = r.rejected ? String(r.rejected) : '';
        var note = !r.accepted && why
            ? '<div class="vdl-r-note vdl-r-note--warn">' + esc(why.charAt(0).toUpperCase() + why.slice(1)) + '</div>'
            : (isBest ? '<div class="vdl-r-note vdl-r-note--best">Best ranked match for this source</div>' : '');
        // Manual pick = the user overrides the quality profile. Auto-grab still only
        // takes accepted releases; here we let them GET a below-profile one anyway.
        var grab = r.username
            ? '<button class="vdl-res-grab' + (r.accepted ? '' : ' vdl-res-grab--override') + '" type="button" data-vdl-grab="' + i +
                '" title="' + (r.accepted ? 'Download this release' : 'Below your quality profile — download anyway') + '">' + (r.accepted ? '[ GET ]' : '[ GET ANYWAY ]') + '</button>'
            : '';
        return '<div class="vdl-res' + (r.accepted ? ' vdl-res--ok' : ' vdl-res--rejected') + (isBest ? ' vdl-res--best' : '') + '" data-vdl-card="' + i + '">' +
            '<div class="vdl-res-main">' +
                '<div class="vdl-r-l1">' +
                    '<span class="vdl-r-q vdl-r-q--' + resKind(r.resolution) + '">' + esc(RES_LABEL[r.resolution] || r.resolution || '?') + '</span>' +
                    '<span class="vdl-r-title" title="' + esc(r.title) + '">' + esc(r.title) + '</span>' +
                    (isBest ? '<span class="vdl-r-best">Best match</span>' : '') +
                '</div>' +
                '<div class="vdl-r-l2">' + esc(meta.filter(Boolean).join('  ·  ')) + '</div>' +
                formatNote +
                '<div class="vdl-r-l3">' + verdict + grab + '</div>' +
                note +
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
        var panel = card.closest('.vdl-results');
        if (panel) { panel._grabbed = panel._grabbed || {}; panel._grabbed[card.getAttribute('data-vdl-card')] = dlId; }
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

    // a failed grab the monitor is replacing: its row says so until the next
    // release is taken, then the chain carries on from it
    var RETRYING_RE = /trying the next release/i;
    var CHAIN_WAIT_MS = 5 * 60 * 1000;

    function _chainLabel(c) {
        var st = c.status, pct = Math.max(0, Math.min(100, c.progress || 0));
        if (st === 'completed') return 'Downloaded';
        if (st === 'failed' || st === 'cancelled') return TRACK_LABEL[st] || 'Failed';
        if (st === 'downloading') return 'Downloading ' + pct + '%';
        return TRACK_LABEL[st] || 'Queued';
    }
    function _chainKind(st) {
        return st === 'completed' ? 'done' : (st === 'failed' || st === 'cancelled' ? 'fail' : 'active');
    }

    // What happened after this card's release failed (SeadogsBooty): every later
    // grab for the same episode or movie, in order, under the card, and a badge
    // on the result card of each release it took instead.
    function _renderChain(card, foot, chain) {
        var box = foot.querySelector('[data-vdl-track-chain]');
        if (!chain.length) { if (box) box.remove(); return; }
        if (!box) {
            box = document.createElement('div');
            box.className = 'vdl-res-track-chain';
            box.setAttribute('data-vdl-track-chain', '');
            foot.appendChild(box);
        }
        box.innerHTML = '<div class="vdl-res-track-chain-head">Then tried</div>' + chain.map(function (c) {
            var kind = _chainKind(c.status);
            var ic = kind === 'done' ? '✓' : (kind === 'fail' ? '✕' : '↻');
            return '<div class="vdl-res-track-step vdl-res-track-step--' + kind + '">' +
                '<span class="vdl-res-track-step-ic">' + ic + '</span>' +
                '<span class="vdl-res-track-step-rel" title="' + esc(c.release_title || c.filename || '') + '">' +
                    esc(c.release_title || c.filename || 'Another release') + '</span>' +
                '<span class="vdl-res-track-step-st">' + esc(_chainLabel(c)) + '</span></div>';
        }).join('');
        var panel = card.closest('.vdl-results');
        var rows = (panel && panel._rows) || [];
        chain.forEach(function (c) {
            var name = c.release_title || c.filename;
            for (var i = 0; i < rows.length; i++) {
                var r = rows[i];
                if (!name || (r.title !== name && r.filename !== name)) continue;
                var other = panel.querySelector('[data-vdl-card="' + i + '"]');
                if (!other || other === card) continue;
                var kind = _chainKind(c.status);
                other.classList.add('vdl-res--chain');
                var l1 = other.querySelector('.vdl-r-l1');
                var badge = other.querySelector('[data-vdl-chain-badge]');
                if (!badge && l1) {
                    badge = document.createElement('span');
                    badge.setAttribute('data-vdl-chain-badge', '');
                    l1.appendChild(badge);
                }
                if (badge) {
                    badge.className = 'vdl-r-chain vdl-r-chain--' + kind;
                    badge.textContent = kind === 'done' ? 'Downloaded instead'
                        : (kind === 'fail' ? 'Also failed' : 'Grabbed instead');
                }
            }
        });
    }

    function _trackPoll(card, foot, dlId) {
        if (foot._t) { clearTimeout(foot._t); foot._t = null; }
        function tick() {
            if (!card.isConnected) return;   // modal closed → stop
            getJSON('/api/video/downloads/status?id=' + encodeURIComponent(dlId) + '&chain=1').then(function (d) {
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
                var chain = (d && d.chain) || [];
                var retrying = st === 'failed' && RETRYING_RE.test(dl.error || '');
                if (stEl && retrying && !chain.length) {
                    stEl.innerHTML = '<span class="vdl-res-track-spin"></span>✕ Failed · finding the next release…';
                }
                _renderChain(card, foot, chain);
                var last = chain.length ? chain[chain.length - 1] : null;
                if (!active) {
                    // keep following while the replacement is still on its way
                    if (!foot._chainSince) foot._chainSince = Date.now();
                    var waiting = retrying && !last && Date.now() - foot._chainSince < CHAIN_WAIT_MS;
                    if ((last && !(last.status in TRACK_DONE)) || waiting) active = true;
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
        loadTitleProfile(container, function () { return container._dl || {}; }, 'show',
            function (p, info) { renderTarget(container, p, false, info); });
        getJSON('/api/video/downloads/config').then(function (c) {
            if (!container.isConnected) return;
            st.sources = sourcesFromConfig(c);
            // Any episode expanded BEFORE the config arrived built its source rows from the
            // fallback list (soulseek only) and cached that via data-srcbuilt — rebuild those
            // so every configured source (e.g. torrent) actually shows.
            Array.prototype.slice.call(container.querySelectorAll('.vdl-ep[data-srcbuilt]')).forEach(function (ep) {
                ep.removeAttribute('data-srcbuilt');
                if (ep.classList.contains('vdl-ep--open')) buildEpSearch(container, ep);
            });
        });
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
                '<button class="vdl-season-grab" type="button" data-vdl-season-grab="' + sn + '" title="Auto-grab every missing episode in this season, one at a time">Grab season</button>' +
                '<button class="vdl-season-search" type="button" data-vdl-season-search="' + sn + '" title="Search this season as a pack">⌕</button>' +
                '<span class="vdl-season-chev" aria-hidden="true">⌄</span>' +
            '</div>' +
            // one inner wrapper so the body can open on grid rows (see video-side.css)
            '<div class="vdl-season-body"><div class="vdl-season-inner">' +
                '<div class="vdl-results vdl-results--season" data-vdl-season-results hidden></div>' +
                '<div class="vdl-season-eps"><div class="vdl-season-empty">Loading…</div></div>' +
            '</div></div>' +
        '</div>';
    }

    function fillSeason(container, card, sn, eps, st) {
        if (!card) return;
        var body = card.querySelector('.vdl-season-eps'); if (!body) return;
        var missing = 0;
        eps.forEach(function (e) {
            var es = epState(e, st.today);
            st.epMeta[sn + '_' + e.episode_number] = { state: es };
            // specials start unticked: grabbing a whole show shouldn't pull in
            // every extra and bonus clip. one click on the season ticks them (SeadogsBooty)
            if (es === 'missing') { missing++; if (sn !== 0) st.sel.add(sn + '_' + e.episode_number); }
        });
        body.innerHTML = eps.map(function (e) { return epRowHTML(sn, e, st); }).join('');
        var meta = card.querySelector('[data-vdl-season-meta]');
        if (meta) meta.textContent = eps.length + ' eps' + (missing ? ' · ' + missing + ' missing' : '');
        card.setAttribute('data-loaded', '1');
        syncSeason(container, sn);
        resumeEpisodeTracking(container, st);   // light up any in-flight episode rows
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
        if (_handlePackClick(e)) return;
        var grab = e.target.closest('[data-vdl-grab]');
        if (grab) { doGrab(grab); return; }
        // Episode-scope Manual search (a source row inside an expanded episode).
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
        // Episode-scope Auto (search this source, then auto-grab the best) — mirrors the
        // movie per-source Auto, scoped to the episode.
        var au = e.target.closest('[data-vdl-auto]');
        if (au) {
            var epA = au.closest('.vdl-ep'); if (!epA) return;
            var pa = (epA.getAttribute('data-vdl-ep') || '').split('_');
            var sa = au.getAttribute('data-vdl-auto');
            var resA = au.closest('[data-vdl-src-block]').querySelector('[data-vdl-results-for="' + sa + '"]');
            var rowA = au.closest('.vdl-src');
            searchInto(container, resA,
                { scope: 'episode', title: st.title, season: +pa[0], episode: +pa[1], source: sa },
                [rowA], function () { _autoPick(resA, rowA); });
            return;
        }
        // Grab whole season — auto-grab every MISSING episode individually (episode-level).
        var sg = e.target.closest('[data-vdl-season-grab]');
        if (sg) { grabSeason(container, st, +sg.getAttribute('data-vdl-season-grab')); return; }
        // Season-scope search → season PACK.
        var ss = e.target.closest('[data-vdl-season-search]');
        if (ss) {
            var sn = ss.getAttribute('data-vdl-season-search');
            var sc = container.querySelector('.vdl-season[data-vdl-season="' + sn + '"]');
            if (sc) sc.classList.add('vdl-season--open');
            var seasonEl = sc && sc.querySelector('[data-vdl-season-results]');
            var packSrc = _packSource(st);
            if (!packSrc) { _noSourceInto(seasonEl); return; }
            searchInto(container, seasonEl,
                { scope: 'season', title: st.title, season: +sn, source: packSrc }, [ss]);
            return;
        }
        // Whole-show search → complete-series pack.
        if (e.target.closest('[data-vdl-search-show]')) {
            var showEl = container.querySelector('[data-vdl-show-results]');
            var showSrc = _packSource(st);
            if (!showSrc) { _noSourceInto(showEl); return; }
            searchInto(container, showEl,
                { scope: 'series', title: st.title, season_end: st.maxSeason || 5, source: showSrc },
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

    // ── per-episode live tracking ──────────────────────────────────────────────
    // Every episode ROW carries its own live download status (Searching → Downloading
    // % → Downloaded / Failed), so season grabs aren't headless and the modal shows
    // exactly what each episode is doing — matching the inline movie tracker.
    function _epEl(container, sn, en) {
        return container.querySelector('.vdl-ep[data-vdl-ep="' + sn + '_' + en + '"]');
    }

    function epStatusRender(epEl, dl) {
        var stEl = epEl.querySelector('[data-vdl-ep-status]'); if (!stEl) return;
        var st = dl.status, pct = Math.max(0, Math.min(100, dl.progress || 0));
        if (st === 'completed') pct = 100;
        epEl.classList.remove('vdl-ep--dl-active', 'vdl-ep--dl-done', 'vdl-ep--dl-fail');
        if (st === 'completed') {
            epEl.classList.add('vdl-ep--dl-done');
            stEl.innerHTML = '<span class="vdl-ep-dl vdl-ep-dl--done">&#10003; Downloaded</span>';
        } else if (st === 'failed' || st === 'cancelled') {
            epEl.classList.add('vdl-ep--dl-fail');
            stEl.innerHTML = '<span class="vdl-ep-dl vdl-ep-dl--fail">&#10007; ' + esc(TRACK_LABEL[st] || 'Failed') + '</span>';
        } else {
            epEl.classList.add('vdl-ep--dl-active');
            var label = st === 'downloading' ? ('Downloading ' + pct + '%')
                : (st === 'searching' ? 'Searching…' : (TRACK_LABEL[st] || 'Queued'));
            stEl.innerHTML = '<span class="vdl-ep-dl vdl-ep-dl--active">' +
                '<span class="vdl-ep-dl-bar"><span style="width:' + pct + '%"></span></span>' + esc(label) + '</span>';
        }
    }

    function epSearching(epEl) {
        if (!epEl) return;
        epEl.classList.remove('vdl-ep--dl-done', 'vdl-ep--dl-fail');
        epEl.classList.add('vdl-ep--dl-active');
        var s = epEl.querySelector('[data-vdl-ep-status]');
        if (s) s.innerHTML = '<span class="vdl-ep-dl vdl-ep-dl--active"><span class="vdl-ep-dl-spin"></span>Searching…</span>';
    }
    // why, when there is one: a refused grab or a search error used to read as
    // a plain "No release found" and looked like an empty search
    function epNoRelease(epEl, reason) {
        if (!epEl) return;
        epEl.classList.remove('vdl-ep--dl-active');
        var s = epEl.querySelector('[data-vdl-ep-status]');
        if (s) s.innerHTML = reason
            ? '<span class="vdl-ep-dl vdl-ep-dl--none" title="' + esc(reason) + '">Not grabbed: ' + esc(reason) + '</span>'
            : '<span class="vdl-ep-dl vdl-ep-dl--none">No release found</span>';
    }

    // Poll one episode's download by id and paint its row until it finishes. Robust to
    // the modal closing (stops) and to a newer grab on the same row (id guard).
    function epTrack(epEl, dlId) {
        if (!epEl || dlId == null) return;
        if (epEl._eptimer) { clearTimeout(epEl._eptimer); epEl._eptimer = null; }
        epEl._epdl = dlId;
        var since = Date.now();
        (function tick() {
            if (!epEl.isConnected || epEl._epdl !== dlId) return;
            getJSON('/api/video/downloads/status?id=' + encodeURIComponent(dlId) + '&chain=1').then(function (d) {
                if (!epEl.isConnected || epEl._epdl !== dlId) return;
                var dl = d && d.download;
                if (!dl) { epEl._eptimer = setTimeout(tick, 2200); return; }
                // a failed release the monitor replaced: the row shows the newest
                // attempt, not the first failure (SeadogsBooty)
                var chain = (d && d.chain) || [];
                var shown = chain.length ? chain[chain.length - 1] : dl;
                var retrying = dl.status === 'failed' && RETRYING_RE.test(dl.error || '') && !chain.length;
                if (retrying && Date.now() - since < CHAIN_WAIT_MS) {
                    epSearching(epEl);
                    epEl._eptimer = setTimeout(tick, 2500);
                    return;
                }
                epStatusRender(epEl, shown);
                if (!(shown.status in TRACK_DONE)) epEl._eptimer = setTimeout(tick, 2000);
            });
        })();
    }

    // Pick the best accepted+grabbable hit in a results panel and grab it. Returns
    // {ok, id} — the id is what the episode row tracks. (Same payload as a manual grab.)
    function _pickAndGrab(panel) {
        var rows = (panel && panel._rows) || [];
        var best = null;
        for (var i = 0; i < rows.length; i++) {
            if (rows[i].accepted && rows[i].username) { best = rows[i]; break; }
        }
        if (!best) return Promise.resolve({ ok: false });
        return sendGrab(buildGrabPayload(panel, best)).then(function (res) {
            return (res && res.ok) ? { ok: true, id: res.id }
                : { ok: false, error: (res && res.error) || 'the download client refused it' };
        });
    }

    // ── Grab whole season (episode level) ──────────────────────────────────────
    // Run the per-episode auto-grab for every MISSING episode, throttled — each takes
    // the same path a manual per-episode Auto would, and each ROW shows live status.
    function ensureScratch(container) {
        var s = container.querySelector('[data-vdl-grab-scratch]');
        if (!s) {
            s = document.createElement('div');
            s.setAttribute('data-vdl-grab-scratch', '');
            s.style.display = 'none';
            container.appendChild(s);
        }
        return s;
    }

    function autoGrabEpisode(container, st, sn, en, src, willRetry) {
        // Resolves once the search SETTLES (throttle the searches, not the grabs); the
        // row then tracks the live download itself. willRetry: a search that ERRORED
        // (an indexer timeout, not "nothing found") is left on Searching, the caller
        // tries it again
        return new Promise(function (resolve) {
            var epEl = _epEl(container, sn, en);
            epSearching(epEl);
            var panel = document.createElement('div');
            panel.className = 'vdl-results vdl-res-noanim';
            ensureScratch(container).appendChild(panel);
            searchInto(container, panel,
                { scope: 'episode', title: st.title, season: sn, episode: en, source: src },
                [], function () {
                    _pickAndGrab(panel).then(function (r) {
                        if (r.ok) {
                            epTrack(epEl, r.id);
                            document.dispatchEvent(new CustomEvent('soulsync:video-download-started'));
                        } else {
                            r = Object.assign({}, r, { errored: !!panel._error });
                            if (!(r.errored && willRetry)) epNoRelease(epEl, r.error || panel._error);
                        }
                        resolve(r);
                    });
                });
        });
    }

    // where a season or whole-show PACK search goes. packs live on usenet and
    // torrent indexers, so those first in the user's order, then whatever else
    // is set up. these searches used to send no source at all, and the server
    // answered with its placeholder releases marked DEMO DATA (SeadogsBooty)
    function _packSource(st) {
        var list = (st.sources || []).filter(function (s) { return SRC_META[s]; });
        for (var i = 0; i < list.length; i++) {
            if (list[i] === 'usenet' || list[i] === 'torrent') return list[i];
        }
        return list[0] || null;
    }

    function _noSourceInto(el) {
        if (!el) return;
        el.hidden = false;
        el.innerHTML = '<div class="vdl-res-empty">No download source configured — pick one on Settings → Downloads.</div>';
    }

    function grabSeason(container, st, sn) {
        // EVERY configured source, not just the first. Asking only sources[0] meant a
        // show that Soulseek had but Prowlarr didn't was reported as unavailable.
        var sources = (st.sources || []).filter(function (s) { return SRC_META[s]; });
        if (!sources.length) { toast('No download source configured', 'error'); return; }
        var eps = [];
        for (var k in st.epMeta) {
            if (k.indexOf(sn + '_') !== 0) continue;
            if (st.epMeta[k].state === 'missing') eps.push(+k.split('_')[1]);
        }
        eps.sort(function (a, b) { return a - b; });
        if (!eps.length) { toast('No missing episodes in this season', 'info'); return; }
        // make sure the season is open so the user sees the rows light up
        var card = container.querySelector('.vdl-season[data-vdl-season="' + sn + '"]');
        if (card) card.classList.add('vdl-season--open');
        eps.forEach(function (en) { epSearching(_epEl(container, sn, en)); });   // immediate feedback
        var btn = container.querySelector('[data-vdl-season-grab="' + sn + '"]');
        if (btn) { btn.disabled = true; btn.textContent = 'Searching…'; }

        function perEpisode() {
            if (btn) btn.textContent = 'Grabbing…';
            toast('Grabbing ' + eps.length + ' episode' + (eps.length > 1 ? 's' : '') +
                ' — each row shows live status', 'info');
            // queue entries are [episode, tries]. an episode whose search errored
            // (Prowlarr took past its 75s, an indexer refused) goes to the back for
            // one more try, and from then on one search runs at a time: three at
            // once is what backs Prowlarr up past its timeout on a slow indexer,
            // and every episode after the first few failed (SeadogsBooty, Dexter S1)
            var queue = eps.map(function (en) { return [en, 0]; });
            var n = 0, active = 0, MAX = 3;
            function finished() { return !queue.length && !active; }
            function pump() {
                while (active < MAX && queue.length) {
                    var item = queue.shift();
                    active++;
                    // Round-robin the sources so one dead indexer can't sink the season.
                    var src = sources[n++ % sources.length];
                    (function (en, tries) {
                        autoGrabEpisode(container, st, sn, en, src, tries === 0).then(function (r) {
                            active--;
                            if (r && !r.ok && r.errored && tries === 0) {
                                MAX = 1;
                                queue.push([en, 1]);
                            }
                            if (finished()) { if (btn) { btn.disabled = false; btn.textContent = 'Grab season'; } }
                            else pump();
                        });
                    })(item[0], item[1]);
                }
            }
            pump();
        }

        // one episode at a time, as the button says. a whole-missing season
        // used to try a season pack first, so The Office S1 went to sabnzbd as
        // one pack and all six rows shared its progress (SeadogsBooty). the
        // season's search button is the way to grab a pack on purpose
        perEpisode();
    }

    // On (re)open, resume live tracking for any episodes of THIS show already in flight,
    // so closing/reopening the modal never loses the per-episode progress.
    function resumeEpisodeTracking(container, st) {
        getJSON('/api/video/downloads/active').then(function (d) {
            ((d && d.downloads) || []).forEach(function (dl) {
                var ctx = dl.search_ctx;
                if (typeof ctx === 'string') { try { ctx = JSON.parse(ctx); } catch (e) { ctx = null; } }
                ctx = ctx || {};
                if (String(ctx.title || dl.title) !== String(st.title)) return;
                if (ctx.season == null || ctx.episode == null) return;
                var epEl = _epEl(container, ctx.season, ctx.episode);
                if (epEl && epEl._epdl == null) { epStatusRender(epEl, dl); epTrack(epEl, dl.id); }
            });
        });
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

    // ── Manual (interactive) search — a dedicated modal that auto-searches EVERY
    //    configured source and lets you pick any release (single or season pack).
    //    Reuses searchInto / renderResults / buildGrabPayload so results + grab
    //    behave and look exactly like the get-modal's download view.
    //    opts: { title, scope:'episode'|'season'|'series', season, episode,
    //            mediaId, mediaSource, year, poster }
    function _grabPack(panel, r) {
        var o = ((panel.closest('[data-vgm-dl-content]') || {})._opts) || {};
        var p = panel._search || {};
        // Soulseek lists a pack's files BEFORE you download them, so it can fan out at
        // grab time into one row per episode. A torrent can't — its files don't exist
        // until it finishes — so it goes through the ordinary grab as ONE row scoped
        // to the season, and the monitor maps the finished folder to episodes.
        if (r.files && r.files.length > 1 && r.username && (p.source || 'soulseek') === 'soulseek') {
            return postJSON('/api/video/downloads/grab-pack', {
                username: r.username, files: r.files,
                title: p.title || o.title || '', quality_label: r.quality_label,
                media_id: o.id || o.mediaId, media_source: o.source || o.mediaSource,
                year: o.year, poster_url: o.poster
            });
        }
        var payload = buildGrabPayload(panel, r);
        payload.kind = 'show';
        payload.search_ctx = payload.search_ctx || {};
        payload.search_ctx.scope = 'season';        // what makes the monitor map the folder
        if (payload.search_ctx.season == null && p.season != null) payload.search_ctx.season = p.season;
        payload.search_ctx.episode = null;          // a pack names no single episode
        return sendGrab(payload).then(function (res) {
            // grab-pack answers {started, skipped}; the normal grab answers {id}. Give
            // the caller one shape so the button doesn't have to know which ran.
            if (res && res.ok) return { ok: true, started: 1, skipped: 0, id: res.id };
            return res;
        });
    }
    // Shared expandable-pack interactions (toggle + [ GET PACK ]) — used by the
    // manual-search modal AND the get-modal's season search. Returns true if handled.
    function _handlePackClick(e) {
        // Check GET PACK first — it lives inside the toggle header, so matching the
        // toggle first would swallow the grab click (just expand/collapse).
        var gp = e.target.closest('[data-vdl-grab-pack]');
        if (!gp) {
            var tog = e.target.closest('[data-vdl-pack-toggle]');
            if (tog) { var card = tog.closest('.vdl-pack'); if (card) card.classList.toggle('vdl-pack--open'); return true; }
            return false;
        }
        var panel = gp.closest('.vdl-results');
        var r = panel && panel._rows && panel._rows[parseInt(gp.getAttribute('data-vdl-grab-pack'), 10)];
        if (panel && r) {
            gp.disabled = true; gp.textContent = '[ … ]';
            _grabPack(panel, r).then(function (res) {
                if (res && res.ok) {
                    gp.textContent = '[ GRABBING ' + res.started + ' ]'; gp.classList.add('vms-grabbed');
                    document.dispatchEvent(new CustomEvent('soulsync:video-download-started'));
                    toast('Grabbing ' + res.started + ' episode' + (res.started === 1 ? '' : 's') +
                        (res.skipped ? ' · ' + res.skipped + ' skipped' : ''), 'success');
                } else { gp.disabled = false; gp.textContent = '[ GET PACK ]'; toast((res && res.error) || 'Pack grab failed', 'error'); }
            });
        }
        return true;
    }

    function _closeManual(ov) {
        if (ov && ov.parentNode) ov.parentNode.removeChild(ov);
        if (!document.querySelector('.vms-overlay, .vgm-overlay')) document.body.style.overflow = '';
    }
    function manualSearch(opts) {
        opts = opts || {};
        var scope = opts.scope || (opts.episode != null ? 'episode' : 'season');
        var scopeLabel = scope === 'episode'
            ? ('S' + opts.season + 'E' + opts.episode)
            : scope === 'movie'
                ? (opts.year ? String(opts.year) : 'Movie')
                : (scope === 'series' ? 'Complete series' : ('Season ' + opts.season));
        var ov = document.createElement('div');
        ov.className = 'vms-overlay';
        ov.innerHTML =
            '<div class="vms-modal">' +
                '<button class="vms-close" type="button" data-vms-close aria-label="Close">&times;</button>' +
                '<div class="vms-head">' +
                    '<div class="vms-eyebrow">Manual search</div>' +
                    '<div class="vms-title">' + esc(opts.title || '') + '</div>' +
                    '<div class="vms-scope">' + esc(scopeLabel) + '</div>' +
                    '<div class="vms-prof" data-vms-prof></div>' +
                    '<div class="vms-sub" data-vms-sub>Searching your sources…</div>' +
                '</div>' +
                '<div class="vms-body" data-vms-body data-vgm-dl-content></div>' +
            '</div>';
        document.body.appendChild(ov);
        document.body.style.overflow = 'hidden';
        var body = ov.querySelector('[data-vms-body]');
        // buildGrabPayload reads media context off the nearest [data-vgm-dl-content]._opts
        body._opts = { id: opts.mediaId, mediaId: opts.mediaId, source: opts.mediaSource,
            mediaSource: opts.mediaSource, tmdbId: opts.tmdbId || null, year: opts.year, poster: opts.poster };

        ov.addEventListener('click', function (e) {
            if (e.target === ov || e.target.closest('[data-vms-close]')) { _closeManual(ov); return; }
            if (_handlePackClick(e)) return;
            var gb = e.target.closest('[data-vdl-grab]');
            if (gb) {
                var panel = gb.closest('.vdl-results');
                var r = panel && panel._rows && panel._rows[parseInt(gb.getAttribute('data-vdl-grab'), 10)];
                if (!panel || !r) return;
                gb.disabled = true; gb.textContent = '[ … ]';
                sendGrab(buildGrabPayload(panel, r)).then(function (res) {
                    if (res && res.ok) {
                        gb.textContent = '[ GRABBED ]'; gb.classList.add('vms-grabbed');
                        document.dispatchEvent(new CustomEvent('soulsync:video-download-started'));
                        toast('Download started — track it on the episode row', 'success');
                    } else { gb.disabled = false; gb.textContent = '[ GET ]'; toast((res && res.error) || 'Grab failed', 'error'); }
                });
            }
        });
        var onKey = function (e) { if (e.key === 'Escape') { _closeManual(ov); document.removeEventListener('keydown', onKey); } };
        document.addEventListener('keydown', onKey);

        var profHost = ov.querySelector('[data-vms-prof]');
        loadTitleProfile(profHost, function () { return body._opts || {}; },
            scope === 'movie' ? 'movie' : 'show',
            function (p, info) {
                var picker = profilePickerHTML(info);
                profHost.innerHTML = picker;
                profHost.hidden = !picker;
            },
            function () { runAll(); });   // a new profile re-judges every result

        function runAll() {
        getJSON('/api/video/downloads/config').then(function (c) {
            // Search every configured source that returns REAL, grabbable results. Soulseek
            // (slskd), Torrent + Usenet (Prowlarr) are all wired end-to-end now — live search
            // + source-aware grab — so honour the user's configured sources/hybrid order.
            var REAL = { soulseek: 1, torrent: 1, usenet: 1 };
            var srcs = sourcesFromConfig(c).filter(function (s) { return SRC_META[s] && REAL[s]; });
            if (!srcs.length) srcs = ['soulseek'];
            var sub = ov.querySelector('[data-vms-sub]');
            if (sub) sub.textContent = 'Searching ' + srcs.map(function (s) { return SRC_META[s].name; }).join(' · ') + '…';
            body.innerHTML = srcs.map(function (s) {
                return '<div class="vms-src" data-vms-src="' + s + '">' +
                    '<div class="vms-src-head">' + (SRC_META[s].emoji || '') + ' ' + esc(SRC_META[s].name) + '</div>' +
                    '<div class="vdl-results" data-vms-results-for="' + s + '"></div>' +
                '</div>';
            }).join('');
            srcs.forEach(function (s) {
                var res = body.querySelector('[data-vms-results-for="' + s + '"]');
                searchInto(ov, res, { scope: scope, title: opts.title, season: opts.season,
                    year: opts.year || null,   // movies search title+year (same as the get-modal)
                    episode: (scope === 'episode' ? opts.episode : null), source: s }, []);
            });
        });
        }
        runAll();
    }

    window.VideoDownload = { render: render, manualSearch: manualSearch };
})();
