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
        ov.innerHTML =
            '<div class="vgm-modal" role="dialog" aria-modal="true">' +
                '<button class="vgm-close" type="button" data-vgm-close aria-label="Close">&times;</button>' +
                '<div class="vgm-hero" data-vgm-hero>' +
                    '<div class="vgm-hero-scrim"></div>' +
                    '<div class="vgm-hero-content">' +
                        '<div class="vgm-eyebrow" data-vgm-eyebrow></div>' +
                        '<h2 class="vgm-title" data-vgm-title>' + esc(o.title || 'Loading…') + '</h2>' +
                        '<div class="vgm-meta" data-vgm-meta></div>' +
                        '<div class="vgm-genres" data-vgm-genres></div>' +
                    '</div>' +
                '</div>' +
                '<div class="vgm-body">' +
                    '<p class="vgm-overview" data-vgm-overview>Loading details…</p>' +
                    '<div class="vgm-eps" data-vgm-eps hidden></div>' +
                    '<div class="vgm-follow" data-vgm-follow hidden></div>' +
                '</div>' +
                '<div class="vgm-actions">' +
                    '<span class="vgm-sel-count" data-vgm-count></span>' +
                    '<button class="vgm-btn vgm-btn--text" type="button" data-vgm-open>Full page &rarr;</button>' +
                    '<button class="vgm-btn vgm-btn--primary" type="button" data-vgm-wishlist>+ Add to Wishlist</button>' +
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
                sh.parentNode.classList.toggle('vgm-season--open');
                return;
            }
            if (e.target.closest('[data-vgm-wishlist]')) {
                // v1: visual only — everything funnels through the wishlist; the
                // real write + download-from-wishlist come later.
                var n = modalState ? modalState.sel.size : 0;
                var aw = ov.querySelector('[data-vgm-add-watch]');
                var follow = (aw && aw.checked) ? ' + Watchlist' : '';
                toast((modalState ? (n + ' episode' + (n === 1 ? '' : 's') + ' — ') : '') +
                    'Wishlist' + follow + ' coming soon', 'info');
            }
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
        if (modalState && modalState.kind === 'show') {
            if (cnt) cnt.textContent = n + ' episode' + (n === 1 ? '' : 's') + ' selected';
            if (add) { add.textContent = '+ Add ' + n + ' to Wishlist'; add.disabled = n === 0; }
        } else {
            if (cnt) cnt.textContent = '';
            if (add) { add.textContent = '+ Add to Wishlist'; add.disabled = false; }
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

    function renderEpisodes(d) {
        var wrap = modalEl.querySelector('[data-vgm-eps]'); if (!wrap) return;
        if (!d.seasons || !d.seasons.length) { wrap.hidden = true; return; }
        var today = isoToday();
        modalState = { kind: 'show', sel: new Set() };
        var totalMissing = 0;
        var html = '<div class="vgm-eps-head"><span class="vgm-eps-h">Episodes</span>' +
            '<label class="vgm-eps-toggle"><input type="checkbox" data-vgm-missing-only checked>' +
            '<span>Missing only</span></label></div><div class="vgm-eps-list">';
        d.seasons.forEach(function (s) {
            var eps = s.episodes || [];
            var missing = 0;
            eps.forEach(function (e) { if (epState(e, today) === 'missing') { missing++; modalState.sel.add(s.season_number + '_' + e.episode_number); } });
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
        if (totalMissing === 0) {
            html += '<div class="vgm-eps-allowned">✓ You have every episode. Turn off “Missing only” to re-download.</div>';
        }
        wrap.innerHTML = html;
        wrap.classList.add('vgm-eps--missing-only');
        wrap.hidden = false;
        // initial season-checkbox states reflect the (missing-only) selection
        d.seasons.forEach(function (s) { syncSeasonCheck(modalEl, s.season_number); });
    }

    function fill(d, o) {
        var q = function (s) { return modalEl.querySelector(s); };
        var id = o.id;
        var hero = q('[data-vgm-hero]');
        var bg = (o.source !== 'tmdb' && d.has_backdrop)
            ? '/api/video/backdrop/' + o.kind + '/' + id + '?w=1280'
            : (d.backdrop_url || d.backdrop || '');
        if (hero && bg) hero.style.backgroundImage = "url('" + bg + "')";

        var t = q('[data-vgm-title]'); if (t && d.title) t.textContent = d.title;
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

        if (o.kind === 'show') { renderEpisodes(d); maybeFollow(d); }   // selector + follow offer
        updateFooter();
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

    window.VideoGet = { btn: btn, isAiring: isAiring, open: openModal, cardButton: cardButton };
})();
