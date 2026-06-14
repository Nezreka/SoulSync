/*
 * SoulSync — Video detail page (isolated, NETFLIX-style — deliberately NOT the
 * music/Spotify layout).
 *
 * A cinematic billboard (full-bleed backdrop, content anchored bottom-left), a
 * per-show accent colour sampled from the poster, a custom season dropdown, and
 * rich episode rows that fade in on season change. Opened by a card via
 * soulsync:video-open-detail; video-side.js navigates, this loads + renders.
 *
 * Self-contained IIFE, no globals, event-delegated, no inline handlers. Talks
 * only to /api/video/* — the music side is never touched.
 */
(function () {
    'use strict';

    var DETAIL_URL = '/api/video/detail/';
    var TMDB_LOGO = 'https://www.themoviedb.org/assets/2/v4/logos/v2/blue_square_2-d537fb228cf3ded904ef09b136fe3fec72548ebc1fea3fbbd1ad9e36364db38b.svg';
    var TVDB_LOGO = 'https://www.svgrepo.com/show/443500/brand-tvdb.svg';
    var data = null;
    var selectedSeason = null;
    var menuOpen = false;

    // Mirrors the music artist-hero badge: logo img with a short text fallback.
    function badge(logo, fallback, title, url) {
        var inner = logo
            ? '<img src="' + logo + '" alt="' + fallback + '" onerror="this.parentNode.textContent=\'' + fallback + '\'">'
            : '<span style="font-size:9px;font-weight:700;">' + fallback + '</span>';
        return url
            ? '<a class="artist-hero-badge" title="' + title + '" href="' + url + '" target="_blank" rel="noopener noreferrer">' + inner + '</a>'
            : '<div class="artist-hero-badge" title="' + title + '">' + inner + '</div>';
    }

    function esc(s) {
        return String(s == null ? '' : s)
            .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
    }
    function root() { return document.querySelector('[data-video-detail="show"]'); }
    function q(sel) { var r = root(); return r ? r.querySelector(sel) : null; }
    function setText(sel, t) { var n = q(sel); if (n) n.textContent = t || ''; }
    function runtimeLabel(m) {
        if (!m) return '';
        var h = Math.floor(m / 60), mm = m % 60;
        return h ? (h + 'h' + (mm ? ' ' + mm + 'm' : '')) : (mm + 'm');
    }
    function statusLabel(s) {
        return s === 'continuing' ? 'Continuing' : s === 'ended' ? 'Ended'
            : s === 'upcoming' ? 'Upcoming' : (s || '');
    }
    function seasonByNum(n) {
        if (!data) return null;
        for (var i = 0; i < data.seasons.length; i++) if (data.seasons[i].season_number === n) return data.seasons[i];
        return null;
    }

    // ── accent extraction (poster → dominant vibrant colour) ──────────────────
    function applyAccent(img) {
        try {
            var w = 24, h = 24;
            var c = document.createElement('canvas'); c.width = w; c.height = h;
            var ctx = c.getContext('2d');
            ctx.drawImage(img, 0, 0, w, h);
            var px = ctx.getImageData(0, 0, w, h).data;
            var best = null, bestScore = -1, fr = 0, fg = 0, fb = 0, n = 0;
            for (var i = 0; i < px.length; i += 4) {
                var r = px[i], g = px[i + 1], b = px[i + 2], a = px[i + 3];
                if (a < 128) continue;
                var mx = Math.max(r, g, b), mn = Math.min(r, g, b);
                var light = (mx + mn) / 2;
                fr += r; fg += g; fb += b; n++;
                if (light < 35 || light > 225) continue;          // skip near-black/white
                var sat = mx === 0 ? 0 : (mx - mn) / mx;
                var score = sat * (mx / 255);                      // vibrant + bright
                if (score > bestScore) { bestScore = score; best = [r, g, b]; }
            }
            if (!best && n) best = [Math.round(fr / n), Math.round(fg / n), Math.round(fb / n)];
            if (best) {
                var r0 = root();
                if (r0) r0.style.setProperty('--vd-accent-rgb', best[0] + ', ' + best[1] + ', ' + best[2]);
            }
        } catch (e) { /* tainted/no image — keep theme accent */ }
    }

    // ── billboard ─────────────────────────────────────────────────────────────
    function renderBillboard(d) {
        setText('[data-vd-title]', d.title);
        setText('[data-vd-overview]', d.overview);

        var bg = q('[data-vd-backdrop]');
        if (bg) {
            var url = d.has_backdrop ? '/api/video/backdrop/show/' + d.id
                : (d.has_poster ? '/api/video/poster/show/' + d.id : '');
            bg.style.backgroundImage = url ? "url('" + url + "')" : '';
            bg.classList.toggle('vd-bb-bg--poster', !d.has_backdrop && !!d.has_poster);
            bg.classList.toggle('vd-bb-bg--empty', !d.has_backdrop && !d.has_poster);
        }

        // offscreen poster → accent colour
        var poster = q('[data-vd-poster]');
        if (poster && d.has_poster) {
            poster.onload = function () { applyAccent(poster); };
            poster.src = '/api/video/poster/show/' + d.id;
        }

        // meta row (Netflix style): owned% · year · rating · seasons · runtime · status
        var ownedPct = d.episode_total ? Math.round(d.episode_owned / d.episode_total * 100) : 0;
        var meta = [];
        meta.push('<span class="vd-match">' + ownedPct + '% in library</span>');
        if (d.year) meta.push('<span>' + esc(d.year) + '</span>');
        if (d.content_rating) meta.push('<span class="vd-meta-rating">' + esc(d.content_rating) + '</span>');
        meta.push('<span>' + d.season_count + ' Season' + (d.season_count === 1 ? '' : 's') + '</span>');
        meta.push('<span>' + d.episode_total + ' Episodes</span>');
        var rt = runtimeLabel(d.runtime_minutes);
        if (rt) meta.push('<span>' + esc(rt) + '</span>');
        if (d.status) meta.push('<span class="vd-status">' + esc(statusLabel(d.status)) + '</span>');
        if (d.network) meta.push('<span>' + esc(d.network) + '</span>');
        var m = q('[data-vd-meta]'); if (m) m.innerHTML = meta.join('');

        // Action buttons — reuse the EXACT music artist hero button styles.
        var a = q('[data-vd-actions]');
        if (a) {
            a.innerHTML =
                '<button class="library-artist-watchlist-btn" type="button" data-vd-act="watchlist">' +
                '<span class="watchlist-icon">＋</span><span class="watchlist-text">Watchlist</span></button>' +
                '<button class="discog-download-btn discog-btn-compact" type="button" data-vd-act="download">' +
                '<span class="discog-btn-icon">⭳</span><span class="discog-btn-text">Get Missing</span>' +
                '<span class="discog-btn-shimmer"></span></button>';
        }

        // External-source links as artist-hero-badge chips (logo + text fallback).
        var l = q('[data-vd-links]');
        if (l) {
            var badges = [];
            if (d.imdb_id) badges.push(badge('', 'IMDb', 'IMDb', 'https://www.imdb.com/title/' + d.imdb_id + '/'));
            if (d.tmdb_id) badges.push(badge(TMDB_LOGO, 'TMDB', 'TMDB', 'https://www.themoviedb.org/tv/' + d.tmdb_id));
            if (d.tvdb_id) badges.push(badge(TVDB_LOGO, 'TVDB', 'TVDB', 'https://thetvdb.com/?id=' + d.tvdb_id + '&tab=series'));
            l.innerHTML = badges.join('');
        }

        var g = q('[data-vd-genres]');
        if (g) g.innerHTML = '';                     // genres land with "capture everything"
    }

    // ── season dropdown ───────────────────────────────────────────────────────
    function renderSeasonSelect() {
        var host = q('[data-vd-season-select]');
        if (!host || !data) return;
        var cur = seasonByNum(selectedSeason);
        host.innerHTML =
            '<button class="vd-ss-btn" type="button" data-vd-ss-toggle>' +
            '<span>' + esc(cur ? cur.title : 'Season') + '</span><span class="vd-ss-caret">▾</span></button>' +
            '<div class="vd-ss-menu' + (menuOpen ? ' vd-ss-menu--open' : '') + '">' +
            data.seasons.map(function (s) {
                var on = s.season_number === selectedSeason ? ' vd-ss-opt--active' : '';
                return '<button class="vd-ss-opt' + on + '" type="button" data-vd-ss-pick="' + s.season_number + '">' +
                    esc(s.title) + '<span class="vd-ss-opt-meta">' + s.episode_owned + '/' + s.episode_total + '</span></button>';
            }).join('') + '</div>';
    }

    // ── episodes ──────────────────────────────────────────────────────────────
    function episodeRow(ep) {
        var owned = ep.owned ? 'vd-ep--owned' : 'vd-ep--missing';
        var meta = [];
        var rt = runtimeLabel(ep.runtime_minutes);
        if (rt) meta.push(rt);
        if (ep.air_date) meta.push(ep.air_date);
        return '<div class="vd-ep ' + owned + '">' +
            '<div class="vd-ep-index">' + (ep.episode_number != null ? ep.episode_number : '') + '</div>' +
            '<div class="vd-ep-thumb"><span class="vd-ep-thumb-ic">▶</span></div>' +
            '<div class="vd-ep-info">' +
            '<div class="vd-ep-top"><span class="vd-ep-title">' +
            esc(ep.title || 'Episode ' + ep.episode_number) + '</span>' +
            (meta.length ? '<span class="vd-ep-rt">' + esc(meta.join(' · ')) + '</span>' : '') + '</div>' +
            (ep.overview ? '<p class="vd-ep-desc">' + esc(ep.overview) + '</p>' : '') +
            '</div>' +
            '<div class="vd-ep-badge">' + (ep.owned ? 'Owned' : 'Missing') + '</div>' +
            '</div>';
    }

    function renderEpisodes() {
        var host = q('[data-vd-episodes]');
        if (!host) return;
        var season = seasonByNum(selectedSeason);
        if (!season) { host.innerHTML = ''; return; }
        host.innerHTML = season.episodes.map(episodeRow).join('');
        // re-trigger the fade/slide-in animation
        host.classList.remove('vd-ep-anim');
        void host.offsetWidth;
        host.classList.add('vd-ep-anim');
    }

    function selectSeason(n) {
        selectedSeason = n;
        menuOpen = false;
        renderSeasonSelect();
        renderEpisodes();
    }

    function showLoading(on) { var l = q('[data-vd-loading]'); if (l) l.hidden = !on; }

    function loadShow(id) {
        if (!root()) return;
        showLoading(true);
        var ep = q('[data-vd-episodes]'); if (ep) ep.innerHTML = '';
        var ss = q('[data-vd-season-select]'); if (ss) ss.innerHTML = '';
        var r0 = root(); if (r0) r0.style.removeProperty('--vd-accent-rgb');
        fetch(DETAIL_URL + 'show/' + id, { headers: { 'Accept': 'application/json' } })
            .then(function (r) { return r.ok ? r.json() : null; })
            .then(function (d) {
                showLoading(false);
                if (!d || d.error) { setText('[data-vd-title]', 'Not found'); return; }
                data = d;
                menuOpen = false;
                selectedSeason = d.seasons && d.seasons.length ? d.seasons[0].season_number : null;
                renderBillboard(d);
                renderSeasonSelect();
                renderEpisodes();
                var sub = document.querySelector('.video-subpage[data-video-subpage="video-show-detail"]');
                if (sub) sub.scrollTop = 0;
            })
            .catch(function () { showLoading(false); setText('[data-vd-title]', 'Could not load show'); });
    }

    // ── events ────────────────────────────────────────────────────────────────
    function onOpen(e) {
        if (!e || !e.detail || e.detail.kind !== 'show') return;
        loadShow(e.detail.id);
    }
    function onClick(e) {
        var r = root(); if (!r) return;
        var toggle = e.target.closest('[data-vd-ss-toggle]');
        if (toggle && r.contains(toggle)) { menuOpen = !menuOpen; renderSeasonSelect(); return; }
        var pick = e.target.closest('[data-vd-ss-pick]');
        if (pick && r.contains(pick)) { selectSeason(parseInt(pick.getAttribute('data-vd-ss-pick'), 10)); return; }
        // click-away closes the menu
        if (menuOpen && !e.target.closest('[data-vd-season-select]')) { menuOpen = false; renderSeasonSelect(); }
    }

    function init() {
        document.addEventListener('soulsync:video-open-detail', onOpen);
        document.addEventListener('click', onClick);
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }
})();
