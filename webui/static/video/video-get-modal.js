/*
 * SoulSync — Video "get" button + detail/download modal (shared).
 *
 * The terminal-content counterpart to the watchlist eye: movies and ENDED shows
 * can't be "watched for new episodes," so instead of an eye they get a download
 * symbol that opens a rich detail modal — the future home of "Add to Wishlist"
 * / "Download". v1 is VISUAL ONLY: the modal renders real detail data, but the
 * action buttons are stubs (no backend wiring yet).
 *
 * Renderers call VideoGet.btn({kind, source, openId, title}); VideoGet.isAiring()
 * is the shared status test that decides eye-vs-get. Self-contained.
 */
(function () {
    'use strict';

    function esc(s) {
        return String(s == null ? '' : s)
            .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
    }
    function toast(msg, type) { if (typeof showToast === 'function') showToast(msg, type); }
    // A stable per-title hue so each modal glows in its own colour (the "vibe").
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

    // A show is "airing" (eye) unless its status says it's finished (get-symbol).
    function isAiring(status) {
        var s = String(status == null ? '' : status).trim().toLowerCase();
        if (!s) return false;   // unknown status → treat as terminal (get), not watch
        return ['ended', 'canceled', 'cancelled', 'completed'].indexOf(s) === -1;
    }

    function dlSvg() {
        return '<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" ' +
            'stroke-width="2" stroke-linecap="round" stroke-linejoin="round">' +
            '<path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><path d="M7 10l5 5 5-5"/><path d="M12 15V3"/></svg>';
    }

    function btn(opts) {
        if (!opts || !opts.openId) return '';
        var kind = opts.kind === 'movie' ? 'movie' : 'show';
        return '<button type="button" class="vget-btn"' +
            ' data-vget-kind="' + kind + '" data-vget-source="' + esc(opts.source || 'library') + '"' +
            ' data-vget-id="' + esc(opts.openId) + '" data-vget-title="' + esc(opts.title || '') + '"' +
            ' title="Get this ' + kind + '" aria-label="Get this ' + kind + '">' + dlSvg() + '</button>';
    }

    // ── modal ─────────────────────────────────────────────────────────────────
    var modalEl = null, keyHandler = null;

    function closeModal() {
        if (!modalEl) return;
        modalEl.classList.remove('vgm-open');
        document.body.style.removeProperty('overflow');
        if (keyHandler) { document.removeEventListener('keydown', keyHandler); keyHandler = null; }
        var el = modalEl; modalEl = null;
        setTimeout(function () { if (el && el.parentNode) el.parentNode.removeChild(el); }, 220);
    }

    // ── download view (in-place, same modal) ──────────────────────────────────
    // Clicking "Download" swaps the detail body for the download view (target +
    // owned verdict + per-source search) without leaving the modal; Back restores
    // it. Selection-only sections collapse; the all-related movie bits stay.
    var DL_HIDE = ['[data-vgm-eps]', '[data-vgm-next]', '[data-vgm-follow]'];

    function setDownloadMode(ov, on) {
        DL_HIDE.forEach(function (sel) {
            var el = ov.querySelector(sel); if (!el) return;
            if (on) { el.setAttribute('data-vgm-washidden', el.hidden ? '1' : '0'); el.hidden = true; }
            else { el.hidden = el.getAttribute('data-vgm-washidden') === '1'; el.removeAttribute('data-vgm-washidden'); }
        });
        var act = ov.querySelector('.vgm-actions'); if (act) act.hidden = on;
        ov.classList.toggle('vgm-mode-download', on);
    }

    function enterDownload(ov, o) {
        var dl = ov.querySelector('[data-vgm-dl]');
        var content = ov.querySelector('[data-vgm-dl-content]');
        if (!dl || !content || !window.VideoDownload) { toast('Download module not loaded', 'error'); return; }
        if (o.kind === 'show') {
            // Shows get a wider modal + a season/episode picker (not the movie layout).
            VideoDownload.render(content, { kind: 'show', id: o.id, source: o.source || 'library',
                detail: (modalState && modalState._detail) || null, tvId: (modalState && modalState._tvId) || null,
                poster: (modalState && modalState.poster) || null });
            ov.classList.add('vgm-mode-dl-show');
        } else {
            var file = (modalState && modalState.kind === 'movie' && modalState.owned) ? (modalState.file || null) : null;
            VideoDownload.render(content, { kind: o.kind, id: o.id, source: o.source || 'library', isYt: false, file: file,
                title: (modalState && modalState.title) || o.title || '', year: (modalState && modalState.year) || null,
                poster: (modalState && modalState.poster) || null });
        }
        setDownloadMode(ov, true);
        dl.hidden = false;
        var modal = ov.querySelector('.vgm-modal'); if (modal) modal.scrollTop = 0;
    }

    function exitDownload(ov) {
        var dl = ov.querySelector('[data-vgm-dl]'); if (dl) dl.hidden = true;
        setDownloadMode(ov, false);
        ov.classList.remove('vgm-mode-dl-show');
    }

    // ── wishlist / watchlist writes ───────────────────────────────────────────
    function postJSON(url, body) {
        return fetch(url, { method: 'POST', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(body) }).then(function (r) { return r.ok ? r.json() : null; });
    }
    // Build the wishlist payload from the modal's selection and POST it; for a
    // show, optionally also follow it (the "Add to watchlist" tick).
    function submitWishlist(ov) {
        if (!modalState || !modalState.tmdbId) { toast('Missing title info', 'error'); return; }
        var btn = ov.querySelector('[data-vgm-wishlist]');
        if (btn) btn.disabled = true;
        var fail = function () { if (btn) btn.disabled = false; toast('Could not add to wishlist', 'error'); };
        var done = function (msg) {
            toast(msg, 'success');
            document.dispatchEvent(new CustomEvent('soulsync:video-wishlist-changed'));
            closeModal();
        };

        if (modalState.kind === 'movie') {
            postJSON('/api/video/wishlist/add', { movie: { tmdb_id: modalState.tmdbId, title: modalState.title,
                year: modalState.year, poster_url: modalState.poster, library_id: modalState.libraryId } })
                .then(function (d) { if (d && d.success) done(modalState.owned ? 'Queued for re-download' : 'Added to wishlist'); else fail(); })
                .catch(fail);
            return;
        }

        // show: turn the selected "S_E" keys into episode rows
        var eps = [];
        modalState.sel.forEach(function (key) {
            var p = key.split('_'), m = (modalState.epMeta || {})[key] || {};
            eps.push({ season_number: parseInt(p[0], 10), episode_number: parseInt(p[1], 10),
                title: m.title, air_date: m.air_date, still_url: m.still, overview: m.overview,
                season_poster_url: (modalState.seasonPoster || {})[p[0]] });
        });
        if (!eps.length) { if (btn) btn.disabled = false; toast('Select at least one episode', 'info'); return; }

        var aw = ov.querySelector('[data-vgm-add-watch]');
        var alsoWatch = !!(aw && aw.checked);
        if (alsoWatch) {
            postJSON('/api/video/watchlist/add', { kind: 'show', tmdb_id: modalState.tmdbId, title: modalState.title,
                poster_url: modalState.poster, library_id: modalState.libraryId })
                .then(function () { document.dispatchEvent(new CustomEvent('soulsync:video-watchlist-changed')); })
                .catch(function () { /* best-effort */ });
        }
        postJSON('/api/video/wishlist/add', { show: { tmdb_id: modalState.tmdbId, title: modalState.title,
            poster_url: modalState.poster, library_id: modalState.libraryId }, episodes: eps })
            .then(function (d) {
                if (!d || !d.success) return fail();
                done(eps.length + ' episode' + (eps.length === 1 ? '' : 's') + ' added' + (alsoWatch ? ' · now watching' : ''));
            })
            .catch(fail);
    }

    var modalState = null;   // { kind, sel:Set<key> } for the open show modal

    function isoToday() {
        var n = new Date();
        return n.getFullYear() + '-' + ('0' + (n.getMonth() + 1)).slice(-2) + '-' + ('0' + n.getDate()).slice(-2);
    }
    var MO = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
    function fmtDate(iso) {
        var p = String(iso || '').split('-');
        if (p.length < 3) return '';
        return MO[(+p[1] || 1) - 1] + ' ' + (+p[2] || 1) + ', ' + p[0];
    }
    // owned (have it) | upcoming (airs in the future) | missing (aired, not owned)
    function epState(e, today) {
        if (e.owned) return 'owned';
        if (e.air_date && e.air_date > today) return 'upcoming';
        return 'missing';
    }

    function openModal(o) {
        closeModal();
        var ov = document.createElement('div');
        ov.className = 'vgm-overlay';
        ov.style.setProperty('--vgm-h', hueOf(o.title || ''));   // vibe colour (refined in fill)
        ov.innerHTML =
            '<div class="vgm-modal" role="dialog" aria-modal="true">' +
                '<button class="vgm-close" type="button" data-vgm-close aria-label="Close">&times;</button>' +
                '<div class="vgm-hero" data-vgm-hero>' +
                    '<div class="vgm-hero-scrim"></div>' +
                    '<img class="vgm-poster" data-vgm-poster alt="" hidden>' +
                    '<div class="vgm-hero-content">' +
                        '<div class="vgm-eyebrow" data-vgm-eyebrow></div>' +
                        '<h2 class="vgm-title" data-vgm-title>' + esc(o.title || 'Loading…') + '</h2>' +
                        '<div class="vgm-meta" data-vgm-meta></div>' +
                        '<div class="vgm-genres" data-vgm-genres></div>' +
                    '</div>' +
                '</div>' +
                '<div class="vgm-body">' +
                    '<div class="vgm-ratings" data-vgm-ratings hidden></div>' +
                    '<p class="vgm-overview" data-vgm-overview>Loading details…</p>' +
                    '<div class="vgm-owned" data-vgm-owned hidden></div>' +
                    '<div class="vgm-next" data-vgm-next hidden></div>' +
                    '<div class="vgm-eps" data-vgm-eps hidden></div>' +
                    '<div class="vgm-follow" data-vgm-follow hidden></div>' +
                    '<div class="vgm-dl" data-vgm-dl hidden>' +
                        '<button class="vgm-back" type="button" data-vgm-back>&larr; Back to details</button>' +
                        '<div class="vgm-dl-content" data-vgm-dl-content></div>' +
                    '</div>' +
                '</div>' +
                '<div class="vgm-actions">' +
                    '<span class="vgm-sel-count" data-vgm-count></span>' +
                    '<button class="discog-cancel-btn" type="button" data-vgm-open>Full page &rarr;</button>' +
                    '<button class="discog-cancel-btn vgm-download-btn" type="button" data-vgm-download>' +
                        '<span class="vgm-download-ic">⤓</span> Download</button>' +
                    '<button class="discog-submit-btn" type="button" data-vgm-wishlist>' +
                        '<span class="discog-submit-icon">⬇</span>' +
                        '<span class="discog-submit-text" data-vgm-add-label>+ Add to Wishlist</span>' +
                    '</button>' +
                '</div>' +
            '</div>';
        document.body.appendChild(ov);
        document.body.style.overflow = 'hidden';
        modalEl = ov;
        modalState = null;
        requestAnimationFrame(function () { ov.classList.add('vgm-open'); });

        ov.addEventListener('click', function (e) {
            if (e.target === ov || e.target.closest('[data-vgm-close]')) { closeModal(); return; }
            if (e.target.closest('[data-vgm-open]')) {
                closeModal();
                document.dispatchEvent(new CustomEvent('soulsync:video-open-detail', {
                    detail: { kind: o.kind, id: parseInt(o.id, 10), source: o.source || 'library' },
                }));
                return;
            }
            // Collapse/expand a season (ignore clicks on its checkbox).
            var sh = e.target.closest('[data-vgm-season-toggle]');
            if (sh && !e.target.closest('.vgm-season-check')) {
                var seasonEl = sh.parentNode;
                var opening = !seasonEl.classList.contains('vgm-season--open');
                seasonEl.classList.toggle('vgm-season--open');
                // First expand of an un-owned (tmdb) season → fetch its episodes.
                if (opening && seasonEl.getAttribute('data-vgm-lazy') === '1' &&
                    !seasonEl.getAttribute('data-vgm-loaded')) {
                    loadSeason(seasonEl);
                }
                return;
            }
            if (e.target.closest('[data-vgm-download]')) { enterDownload(ov, o); return; }
            if (e.target.closest('[data-vgm-back]')) { exitDownload(ov); return; }
            if (e.target.closest('[data-vgm-wishlist]')) { submitWishlist(ov); }
        });
        ov.addEventListener('change', function (e) {
            var sa = e.target.closest('[data-vgm-season-all]');
            if (sa) {  // season select-all → toggle its currently-actionable episodes
                var sn = sa.getAttribute('data-vgm-season-all');
                sa.indeterminate = false;
                seasonBoxes(ov, sn).forEach(function (b) {
                    b.checked = sa.checked;
                    toggleSel(b.getAttribute('data-vgm-ep'), sa.checked);
                });
                updateFooter();
                return;
            }
            var ep = e.target.closest('.vgm-ep-cb');
            if (ep) {
                toggleSel(ep.getAttribute('data-vgm-ep'), ep.checked);
                syncSeasonCheck(ov, ep.getAttribute('data-vgm-ep').split('_')[0]);
                updateFooter();
                return;
            }
            if (e.target.closest('[data-vgm-missing-only]')) {
                var wrap = ov.querySelector('[data-vgm-eps]');
                if (wrap) wrap.classList.toggle('vgm-eps--missing-only', e.target.checked);
                // the actionable set changed → re-derive every season checkbox
                var sas = ov.querySelectorAll('[data-vgm-season-all]');
                for (var k = 0; k < sas.length; k++) syncSeasonCheck(ov, sas[k].getAttribute('data-vgm-season-all'));
            }
        });
        keyHandler = function (e) { if (e.key === 'Escape') closeModal(); };
        document.addEventListener('keydown', keyHandler);

        var url = (o.source === 'tmdb')
            ? '/api/video/tmdb/' + o.kind + '/' + o.id
            : '/api/video/detail/' + o.kind + '/' + o.id;
        fetch(url, { headers: { Accept: 'application/json' } })
            .then(function (r) { return r.ok ? r.json() : null; })
            .then(function (d) { if (modalEl && d) fill(d, o); })
            .catch(function () { /* keep the title-only shell */ });
    }

    function toggleSel(key, on) { if (!modalState) return; if (on) modalState.sel.add(key); else modalState.sel.delete(key); }

    function missingOnlyOn(ov) { var m = ov.querySelector('[data-vgm-missing-only]'); return !m || m.checked; }
    // Episode checkboxes for a season that the season-select-all should act on:
    // when "Missing only" is on, owned episodes are hidden, so exclude them.
    function seasonBoxes(ov, sn) {
        var skipOwned = missingOnlyOn(ov);
        var all = ov.querySelectorAll('.vgm-ep-cb[data-vgm-ep^="' + sn + '_"]'), out = [];
        for (var i = 0; i < all.length; i++) {
            if (skipOwned && all[i].closest('.vgm-ep--owned')) continue;
            out.push(all[i]);
        }
        return out;
    }
    function syncSeasonCheck(ov, sn) {
        var all = ov.querySelector('[data-vgm-season-all="' + sn + '"]'); if (!all) return;
        var boxes = seasonBoxes(ov, sn);
        var checked = 0;
        for (var i = 0; i < boxes.length; i++) if (boxes[i].checked) checked++;
        all.checked = boxes.length > 0 && checked === boxes.length;
        all.indeterminate = checked > 0 && checked < boxes.length;
        all.disabled = boxes.length === 0;
    }

    function updateFooter() {
        if (!modalEl) return;
        var n = modalState ? modalState.sel.size : 0;
        var cnt = modalEl.querySelector('[data-vgm-count]');
        var add = modalEl.querySelector('[data-vgm-wishlist]');
        var addLbl = modalEl.querySelector('[data-vgm-add-label]');   // set label only (keep the icon)
        if (modalState && modalState.kind === 'show') {
            if (cnt) cnt.textContent = n + ' episode' + (n === 1 ? '' : 's') + ' selected';
            if (addLbl) addLbl.textContent = '+ Add ' + n + ' to Wishlist';
            if (add) add.disabled = n === 0;
        } else if (modalState && modalState.kind === 'movie' && modalState.owned) {
            // Already owned — the action is a re-grab, not a new want.
            if (cnt) cnt.textContent = '';
            if (addLbl) addLbl.textContent = 'Re-download';
            if (add) add.disabled = false;
        } else {
            if (cnt) cnt.textContent = '';
            if (addLbl) addLbl.textContent = '+ Add to Wishlist';
            if (add) add.disabled = false;
        }
    }

    function epRow(snum, e, today) {
        var st = epState(e, today);
        var key = snum + '_' + e.episode_number;
        var date = e.air_date ? fmtDate(e.air_date) : '';
        // missing-aired -> pre-checked; owned -> selectable for re-download (not
        // pre-checked); upcoming -> locked (can't grab what hasn't aired).
        var ctrl = (st === 'upcoming')
            ? '<span class="vgm-ep-lock">◷</span>'
            : '<input type="checkbox" class="vgm-ep-cb" data-vgm-ep="' + esc(key) + '"' + (st === 'missing' ? ' checked' : '') + '>';
        var badge = st === 'owned' ? '<span class="vgm-ep-badge vgm-ep-badge--owned">In library</span>'
            : st === 'upcoming' ? '<span class="vgm-ep-badge vgm-ep-badge--soon">' + (date || 'Upcoming') + '</span>'
            : '<span class="vgm-ep-badge vgm-ep-badge--missing">Missing</span>';
        return '<label class="vgm-ep vgm-ep--' + st + '">' +
            '<span class="vgm-ep-ctrl">' + ctrl + '</span>' +
            '<span class="vgm-ep-num">E' + (e.episode_number != null ? e.episode_number : '') + '</span>' +
            '<span class="vgm-ep-title">' + esc(e.title || ('Episode ' + e.episode_number)) + '</span>' +
            (date && st !== 'upcoming' ? '<span class="vgm-ep-date">' + esc(date) + '</span>' : '') +
            badge + '</label>';
    }

    function renderEpisodes(d, o) {
        var wrap = modalEl.querySelector('[data-vgm-eps]'); if (!wrap) return;
        if (!d.seasons || !d.seasons.length) { wrap.hidden = true; return; }
        var today = isoToday();
        // For an un-owned (tmdb) show the episodes aren't shipped with the detail —
        // they load per-season on expand (like the full detail page). Stash the tv
        // id so the lazy loader can fetch them.
        var tvId = (o && o.source === 'tmdb') ? parseInt(o.id, 10) : null;
        modalState = { kind: 'show', sel: new Set(), tvId: tvId, today: today, epMeta: {}, seasonPoster: {} };
        var totalMissing = 0, anyLazy = false;
        var html = '<div class="vgm-eps-head"><span class="vgm-eps-h">Episodes</span>' +
            '<label class="vgm-eps-toggle"><input type="checkbox" data-vgm-missing-only checked>' +
            '<span>Missing only</span></label></div><div class="vgm-eps-list">';
        d.seasons.forEach(function (s) {
            // Capture this season's poster (owned → proxy by season id, tmdb → direct).
            modalState.seasonPoster[String(s.season_number)] = (o && o.source === 'library')
                ? (s.has_poster && s.id != null ? '/api/video/poster/season/' + s.id : null)
                : (s.poster_url || null);
            var eps = s.episodes || [];
            // Lazy: a tmdb season with a known count but no episodes shipped yet.
            if (!eps.length && (s.episode_total || 0) > 0 && tvId) {
                anyLazy = true;
                totalMissing += s.episode_total;   // all wanted → never "all owned"
                html += '<div class="vgm-season" data-vgm-lazy="1" data-vgm-season="' + s.season_number + '">' +
                    '<div class="vgm-season-head" data-vgm-season-toggle>' +
                        '<span class="vgm-season-check"><input type="checkbox" data-vgm-season-all="' + s.season_number + '" disabled></span>' +
                        '<span class="vgm-season-name">' + esc(s.title || ('Season ' + s.season_number)) + '</span>' +
                        '<span class="vgm-season-meta">' + s.episode_total + ' eps</span>' +
                        '<span class="vgm-season-chev" aria-hidden="true">⌄</span>' +
                    '</div>' +
                    '<div class="vgm-season-eps"><div class="vgm-season-loading">Expand to load episodes…</div></div>' +
                    '</div>';
                return;
            }
            var missing = 0;
            eps.forEach(function (e) {
                modalState.epMeta[s.season_number + '_' + e.episode_number] = { title: e.title, air_date: e.air_date,
                    overview: e.overview, still: e.has_still ? ('/api/video/poster/episode/' + e.id) : null };
                if (epState(e, today) === 'missing') { missing++; modalState.sel.add(s.season_number + '_' + e.episode_number); }
            });
            totalMissing += missing;
            // Fully-owned seasons are hidden while "Missing only" is on (turn it off
            // to re-download); otherwise an owned season expanded to nothing.
            html += '<div class="vgm-season' + (missing ? '' : ' vgm-season--owned') + '">' +
                '<div class="vgm-season-head" data-vgm-season-toggle>' +
                    '<span class="vgm-season-check"><input type="checkbox" data-vgm-season-all="' + s.season_number + '"' + (missing && missing === eps.length ? ' checked' : '') + '></span>' +
                    '<span class="vgm-season-name">' + esc(s.title || ('Season ' + s.season_number)) + '</span>' +
                    '<span class="vgm-season-meta">' + (missing ? missing + ' missing · ' : 'owned · ') + eps.length + ' eps</span>' +
                    '<span class="vgm-season-chev" aria-hidden="true">⌄</span>' +
                '</div>' +
                '<div class="vgm-season-eps">' + eps.map(function (e) { return epRow(s.season_number, e, today); }).join('') + '</div>' +
                '</div>';
        });
        html += '</div>';
        // When you own everything, the missing-only view would be blank — say so.
        // (Skipped for lazy/un-owned shows: nothing is owned, episodes load on expand.)
        if (totalMissing === 0 && !anyLazy) {
            html += '<div class="vgm-eps-allowned">✓ You have every episode. Turn off “Missing only” to re-download.</div>';
        }
        wrap.innerHTML = html;
        wrap.classList.add('vgm-eps--missing-only');
        wrap.hidden = false;
        // initial season-checkbox states reflect the (missing-only) selection
        d.seasons.forEach(function (s) { syncSeasonCheck(modalEl, s.season_number); });
        // Prefetch the first real season of an un-owned show so its first expand
        // is instant (and its missing episodes pre-count in the footer).
        if (anyLazy) prefetchFirstSeason(wrap);
    }

    function prefetchFirstSeason(wrap) {
        var lazies = wrap.querySelectorAll('.vgm-season[data-vgm-lazy="1"]');
        var pick = null, pickN = Infinity;
        for (var i = 0; i < lazies.length; i++) {
            var n = parseInt(lazies[i].getAttribute('data-vgm-season'), 10);
            if (n >= 1 && n < pickN) { pickN = n; pick = lazies[i]; }   // prefer Season 1 over Specials
        }
        if (!pick && lazies.length) pick = lazies[0];
        if (pick) loadSeason(pick);
    }

    // Fetch a tmdb season's episodes the first time it's expanded, render them,
    // and pre-select the missing-aired ones (everything un-owned that has aired).
    function loadSeason(seasonEl) {
        if (!modalState || !modalState.tvId || !seasonEl) return;
        var sn = parseInt(seasonEl.getAttribute('data-vgm-season'), 10);
        seasonEl.setAttribute('data-vgm-loaded', '1');   // guard double-fetch
        var body = seasonEl.querySelector('.vgm-season-eps');
        if (body) body.innerHTML = '<div class="vgm-season-loading">Loading episodes…</div>';
        fetch('/api/video/tmdb/show/' + modalState.tvId + '/season/' + sn, { headers: { Accept: 'application/json' } })
            .then(function (r) { return r.ok ? r.json() : null; })
            .then(function (data) {
                if (!modalEl || !seasonEl.parentNode) return;
                var eps = (data && data.episodes) || [];
                var b = seasonEl.querySelector('.vgm-season-eps');
                if (!eps.length) { if (b) b.innerHTML = '<div class="vgm-season-loading">No episode info available.</div>'; return; }
                var today = modalState.today;
                if (b) b.innerHTML = eps.map(function (e) { return epRow(sn, e, today); }).join('');
                modalState.epMeta = modalState.epMeta || {};
                eps.forEach(function (e) {
                    modalState.epMeta[sn + '_' + e.episode_number] = { title: e.title, air_date: e.air_date,
                        overview: e.overview, still: e.still_url || null };
                    if (epState(e, today) === 'missing') modalState.sel.add(sn + '_' + e.episode_number);
                });
                var all = seasonEl.querySelector('[data-vgm-season-all]');
                if (all) all.disabled = false;
                syncSeasonCheck(modalEl, sn);
                updateFooter();
            })
            .catch(function () {
                var b = seasonEl.querySelector('.vgm-season-eps');
                if (b) b.innerHTML = '<div class="vgm-season-loading">Couldn’t load episodes — tap to retry.</div>';
                seasonEl.removeAttribute('data-vgm-loaded');   // allow a retry on next expand
            });
    }

    function fill(d, o) {
        var q = function (s) { return modalEl.querySelector(s); };
        var id = o.id;
        var hero = q('[data-vgm-hero]');
        var bg = (o.source !== 'tmdb' && d.has_backdrop)
            ? '/api/video/backdrop/' + o.kind + '/' + id + '?w=1280'
            : (d.backdrop_url || d.backdrop || '');
        if (hero && bg) hero.style.backgroundImage = "url('" + bg + "')";

        // Poster thumbnail floating over the hero (the premium "card" feel).
        var poster = q('[data-vgm-poster]');
        var pUrl = (o.source !== 'tmdb' && d.has_poster)
            ? '/api/video/poster/' + o.kind + '/' + id
            : (d.poster_url || d.poster || '');
        if (poster && pUrl) {
            poster.onload = function () { if (hero) hero.classList.add('vgm-has-poster'); poster.hidden = false; };
            poster.onerror = function () { poster.hidden = true; if (hero) hero.classList.remove('vgm-has-poster'); };
            poster.src = pUrl;
        }

        var t = q('[data-vgm-title]'); if (t && d.title) t.textContent = d.title;
        if (d.title) modalEl.style.setProperty('--vgm-h', hueOf(d.title));   // refine vibe from the real title
        var eyebrow = [d.network, d.studio, d.year, d.status, d.content_rating].filter(Boolean).map(esc).join('  ·  ');
        var eb = q('[data-vgm-eyebrow]'); if (eb) eb.textContent = eyebrow;

        var meta = [];
        if (d.runtime_minutes) meta.push(d.runtime_minutes + ' min');
        if (d.rating) meta.push('★ ' + (Math.round(d.rating * 10) / 10));
        if (d.tagline) meta.push('“' + d.tagline + '”');
        var mt = q('[data-vgm-meta]'); if (mt) mt.innerHTML = meta.map(function (x) {
            return '<span class="vgm-meta-item">' + esc(x) + '</span>';
        }).join('');

        var g = q('[data-vgm-genres]');
        if (g && d.genres && d.genres.length) g.innerHTML = d.genres.slice(0, 5).map(function (x) {
            return '<span class="vgm-genre">' + esc(x) + '</span>';
        }).join('');

        var ov = q('[data-vgm-overview]');
        if (ov) {
            if (d.overview) { ov.textContent = d.overview; ov.classList.remove('vgm-overview--none'); }
            else { ov.textContent = 'No synopsis available yet.'; ov.classList.add('vgm-overview--none'); }
        }

        renderRatings(d);   // IMDb / Rotten Tomatoes / Metacritic chips (both kinds)
        var libId = (o.source === 'library') ? parseInt(o.id, 10) : (d.library_id || null);
        if (o.kind === 'show') {
            modalState = modalState || { kind: 'show', sel: new Set(), epMeta: {} };
            renderEpisodes(d, o); renderNext(d); maybeFollow(d);   // selector + next-up + follow offer
            if (modalState) {   // identity for the wishlist/watchlist writes
                modalState.tmdbId = d.tmdb_id; modalState.title = d.title;
                modalState.poster = pUrl || null; modalState.libraryId = libId;
                modalState._detail = d;   // feeds the show download tree (seasons/episodes)
                modalState._tvId = (o.source === 'tmdb') ? parseInt(o.id, 10) : (d.tmdb_id || null);
            }
        } else {
            modalState = { kind: 'movie', owned: !!d.owned, tmdbId: d.tmdb_id, title: d.title,
                year: d.year || null, poster: pUrl || null, libraryId: libId,
                file: d.file || null };   // owned → re-download; file feeds the download verdict
            renderOwned(d);
        }
        updateFooter();
    }

    // Ratings strip: branded chips for whichever scores the payload carries.
    function renderRatings(d) {
        var box = modalEl && modalEl.querySelector('[data-vgm-ratings]'); if (!box) return;
        function chip(cls, src, val) {
            return '<span class="vgm-rating vgm-rating--' + cls + '">' +
                '<span class="vgm-rating-src">' + src + '</span>' +
                '<span class="vgm-rating-val">' + esc(val) + '</span></span>';
        }
        var chips = [];
        if (d.imdb_rating) chips.push(chip('imdb', 'IMDb', (Math.round(d.imdb_rating * 10) / 10)));
        if (d.rt_rating) chips.push(chip('rt', 'Rotten Tomatoes', d.rt_rating + '%'));
        if (d.metacritic) chips.push(chip('mc', 'Metacritic', d.metacritic));
        if (!chips.length) { box.hidden = true; return; }
        box.innerHTML = chips.join('');
        box.hidden = false;
    }

    // Owned movie → an "in your library" note (with the best version's quality)
    // instead of pretending it's a fresh wishlist want.
    function renderOwned(d) {
        var box = modalEl && modalEl.querySelector('[data-vgm-owned]'); if (!box) return;
        if (!d.owned) { box.hidden = true; return; }
        var f = d.file || {};
        var bits = [resLabel(f.resolution), f.quality, (f.audio_codec || '').toUpperCase()].filter(Boolean);
        box.innerHTML = '<span class="vgm-owned-ic">✓</span>' +
            '<span class="vgm-owned-txt"><strong>In your library</strong>' +
            (bits.length ? ' · ' + esc(bits.join(' · ')) : '') + '</span>';
        box.hidden = false;
    }

    // Airing show → the soonest not-yet-aired episode, so "what's next" is right
    // up front (ties the modal to the watchlist's whole reason for existing).
    function renderNext(d) {
        var box = modalEl && modalEl.querySelector('[data-vgm-next]'); if (!box) return;
        if (!isAiring(d.status)) { box.hidden = true; return; }
        var today = isoToday(), best = null;
        (d.seasons || []).forEach(function (s) {
            (s.episodes || []).forEach(function (e) {
                if (e.air_date && e.air_date > today && (!best || e.air_date < best.air_date))
                    best = { s: s.season_number, e: e.episode_number, air_date: e.air_date, title: e.title };
            });
        });
        // Un-owned (tmdb) shows ship no episodes — fall back to the payload's
        // next_episode stub from the TMDB extras.
        if (!best && d.next_episode && d.next_episode.episode_number != null) {
            var ne = d.next_episode;
            best = { s: ne.season_number, e: ne.episode_number, air_date: ne.air_date, title: ne.name };
        }
        if (!best) { box.hidden = true; return; }
        box.innerHTML = '<span class="vgm-next-ic">▶</span>' +
            '<span class="vgm-next-txt"><strong>Next episode</strong> · S' + best.s + ' · E' + best.e +
            (best.title ? ' — ' + esc(best.title) : '') + '</span>' +
            (best.air_date ? '<span class="vgm-next-date">' + esc(fmtDate(best.air_date)) + '</span>' : '');
        box.hidden = false;
    }

    // Airing show you don't follow yet → offer to start watching it (default on),
    // so grabbing episodes also keeps you current. Hidden for ended shows (can't
    // "watch" for new) and shows you already follow.
    function maybeFollow(d) {
        var fw = modalEl.querySelector('[data-vgm-follow]'); if (!fw) return;
        if (!d.tmdb_id || !isAiring(d.status)) { fw.hidden = true; return; }
        fetch('/api/video/watchlist/check', { method: 'POST', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ kind: 'show', tmdb_ids: [d.tmdb_id] }) })
            .then(function (r) { return r.ok ? r.json() : null; })
            .then(function (res) {
                if (!modalEl) return;
                if (res && res.results && res.results[String(d.tmdb_id)]) { fw.hidden = true; return; }
                fw.innerHTML = '<label class="vgm-follow-row"><input type="checkbox" data-vgm-add-watch checked>' +
                    '<span class="vgm-follow-txt"><strong>Add to watchlist</strong> — automatically get new episodes as they air</span></label>';
                fw.hidden = false;
            }).catch(function () { /* skip the row */ });
    }

    // One capture-phase handler — the get button sits inside a card <a>.
    document.addEventListener('click', function (e) {
        var b = e.target.closest && e.target.closest('.vget-btn');
        if (!b) return;
        e.preventDefault();
        e.stopPropagation();
        openModal({
            kind: b.getAttribute('data-vget-kind'),
            source: b.getAttribute('data-vget-source') || 'library',
            id: b.getAttribute('data-vget-id'),
            title: b.getAttribute('data-vget-title') || '',
        });
    }, true);

    // The control group for any card, so every surface stays consistent:
    //   person          → eye (watchlist)
    //   movie           → get (download/wishlist)
    //   airing show     → eye + get   (follow new episodes AND grab the back catalog)
    //   ended show      → get
    // Returns a positioned wrapper holding 0–2 buttons.
    function cardButton(o) {
        if (!o || !o.kind) return '';
        var parts = [];
        if (o.kind === 'person') {
            if (window.VideoWatchlist) parts.push(VideoWatchlist.btn({ kind: 'person', tmdbId: o.tmdbId, title: o.title, poster: o.poster }));
        } else if (o.kind === 'show') {
            var airing = !o.status ? true : isAiring(o.status);
            if (airing && o.tmdbId && window.VideoWatchlist) {
                parts.push(VideoWatchlist.btn({ kind: 'show', tmdbId: o.tmdbId, title: o.title,
                                                poster: o.poster, libraryId: o.libraryId }));
            }
            // every show can be acquired (missing/back-catalog episodes)
            parts.push(btn({ kind: 'show', source: o.source || 'tmdb', openId: o.libraryId || o.tmdbId, title: o.title }));
        } else if (o.kind === 'movie') {
            parts.push(btn({ kind: 'movie', source: o.source || 'tmdb', openId: o.libraryId || o.tmdbId, title: o.title }));
        }
        parts = parts.filter(Boolean);
        return parts.length ? '<span class="vcard-ctrls">' + parts.join('') + '</span>' : '';
    }

    window.VideoGet = { btn: btn, isAiring: isAiring, open: openModal, close: closeModal, cardButton: cardButton };
})();
