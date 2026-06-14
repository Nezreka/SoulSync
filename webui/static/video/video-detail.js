/*
 * SoulSync — Video detail page (isolated).
 *
 * Drill-in TV-show detail: a hero (contained glass card with the backdrop blurred
 * inside it + overlay), then a poster-art SEASON grid (season = album) whose
 * selected season renders its episodes below (episode = track). Inspired by the
 * music artist page. Opened by a card via soulsync:video-open-detail; video-side.js
 * navigates, this loads + renders.
 *
 * Self-contained IIFE, no globals, event-delegated, no inline handlers. Talks
 * only to /api/video/* — the music side is never touched.
 */
(function () {
    'use strict';

    var DETAIL_URL = '/api/video/detail/';
    var data = null;          // last loaded show payload
    var selectedSeason = null;

    function esc(s) {
        return String(s == null ? '' : s)
            .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
    }

    function root() { return document.querySelector('[data-video-detail="show"]'); }
    function q(sel) { var r = root(); return r ? r.querySelector(sel) : null; }
    function setText(sel, text) { var n = q(sel); if (n) n.textContent = text || ''; }

    function pill(label, cls) {
        return '<span class="vd-pill' + (cls ? ' ' + cls : '') + '">' + esc(label) + '</span>';
    }
    function runtimeLabel(mins) {
        if (!mins) return '';
        var h = Math.floor(mins / 60), m = mins % 60;
        return h ? (h + 'h' + (m ? ' ' + m + 'm' : '')) : (m + 'm');
    }
    function statusLabel(s) {
        return s === 'continuing' ? 'Continuing' : s === 'ended' ? 'Ended'
            : s === 'upcoming' ? 'Upcoming' : (s || '');
    }

    // ── hero ────────────────────────────────────────────────────────────────
    function renderHero(d) {
        setText('[data-vd-title]', d.title);
        setText('[data-vd-overview]', d.overview);

        var backdrop = q('[data-vd-backdrop]');
        if (backdrop) {
            var bg = d.has_backdrop ? '/api/video/backdrop/show/' + d.id
                : (d.has_poster ? '/api/video/poster/show/' + d.id : '');
            backdrop.style.backgroundImage = bg ? "url('" + bg + "')" : '';
        }

        var poster = q('[data-vd-poster]');
        var fallback = q('[data-vd-poster-fallback]');
        if (poster && fallback) {
            if (d.has_poster) {
                poster.src = '/api/video/poster/show/' + d.id;
                poster.style.display = ''; fallback.style.display = 'none';
                poster.onerror = function () { poster.style.display = 'none'; fallback.style.display = ''; };
            } else { poster.style.display = 'none'; fallback.style.display = ''; }
        }

        var badges = [];
        if (d.year) badges.push(pill(d.year));
        if (d.content_rating) badges.push(pill(d.content_rating, 'vd-pill--rating'));
        if (d.status) badges.push(pill(statusLabel(d.status)));
        if (d.network) badges.push(pill(d.network));
        var rt = runtimeLabel(d.runtime_minutes);
        if (rt) badges.push(pill(rt));
        var b = q('[data-vd-badges]'); if (b) b.innerHTML = badges.join('');

        // External-link badges (real, useful) — mirrors the artist hero's service row.
        var links = [];
        if (d.imdb_id) links.push(['IMDb', 'https://www.imdb.com/title/' + d.imdb_id + '/', 'vd-link--imdb']);
        if (d.tmdb_id) links.push(['TMDB', 'https://www.themoviedb.org/tv/' + d.tmdb_id, 'vd-link--tmdb']);
        if (d.tvdb_id) links.push(['TVDB', 'https://thetvdb.com/?id=' + d.tvdb_id + '&tab=series', 'vd-link--tvdb']);
        var a = q('[data-vd-actions]');
        if (a) {
            a.innerHTML = links.map(function (l) {
                return '<a class="vd-link ' + l[2] + '" href="' + l[1] + '" target="_blank" rel="noopener">' +
                    esc(l[0]) + '</a>';
            }).join('');
        }

        var ownedPct = d.episode_total ? Math.round(d.episode_owned / d.episode_total * 100) : 0;
        var stats = [
            ['Seasons', d.season_count],
            ['Episodes', d.episode_total],
            ['Owned', d.episode_owned + ' / ' + d.episode_total],
            ['Collected', ownedPct + '%'],
        ];
        var s = q('[data-vd-stats]');
        if (s) {
            s.innerHTML = stats.map(function (st) {
                return '<div class="vd-stat"><span class="vd-stat-num">' + esc(st[1]) +
                    '</span><span class="vd-stat-label">' + esc(st[0]) + '</span></div>';
            }).join('');
        }
    }

    // ── season poster-card grid ───────────────────────────────────────────────
    function seasonArt(season) {
        // Real per-season poster when the scan captured one; else the show poster.
        return season.has_poster ? '/api/video/poster/season/' + season.id
            : (data && data.has_poster ? '/api/video/poster/show/' + data.id : '');
    }

    function seasonCard(season) {
        var pct = season.episode_total ? Math.round(season.episode_owned / season.episode_total * 100) : 0;
        var art = seasonArt(season);
        var sel = season.season_number === selectedSeason ? ' vd-scard--active' : '';
        var img = art
            ? '<img class="vd-scard-img" src="' + art + '" alt="" loading="lazy" ' +
              'onerror="this.style.display=\'none\'">'
            : '';
        return '<button class="vd-scard' + sel + '" type="button" data-vd-season="' + season.season_number + '">' +
            '<div class="vd-scard-art">' + img +
            '<div class="vd-scard-fallback">📺</div>' +
            '<div class="vd-scard-grad"></div>' +
            '<div class="vd-scard-pct">' + pct + '%</div>' +
            '</div>' +
            '<div class="vd-scard-info">' +
            '<span class="vd-scard-name">' + esc(season.title) + '</span>' +
            '<span class="vd-scard-sub">' + season.episode_owned + ' / ' + season.episode_total + ' eps</span>' +
            '<span class="vd-scard-bar"><span class="vd-scard-bar-fill" style="width:' + pct + '%"></span></span>' +
            '</div></button>';
    }

    function renderSeasons(d) {
        var title = q('[data-vd-seasons-title]');
        var host = q('[data-vd-seasons]');
        if (!host) return;
        if (!d.seasons || !d.seasons.length) {
            if (title) title.hidden = true;
            host.innerHTML = '';
            var ep0 = q('[data-vd-episodes]'); if (ep0) ep0.innerHTML = '';
            return;
        }
        if (title) title.hidden = false;
        host.innerHTML = d.seasons.map(seasonCard).join('');
        renderEpisodes(selectedSeason);
    }

    // ── episodes panel (selected season) ──────────────────────────────────────
    function episodeRow(ep) {
        var num = ep.episode_number != null ? ('E' + ep.episode_number) : '';
        var owned = ep.owned ? 'vd-ep--owned' : 'vd-ep--missing';
        var meta = [];
        if (ep.air_date) meta.push(ep.air_date);
        var rt = runtimeLabel(ep.runtime_minutes);
        if (rt) meta.push(rt);
        return '<div class="vd-ep ' + owned + '">' +
            '<span class="vd-ep-num">' + esc(num) + '</span>' +
            '<span class="vd-ep-body"><span class="vd-ep-title">' +
            esc(ep.title || 'Episode ' + ep.episode_number) + '</span>' +
            (ep.overview ? '<span class="vd-ep-overview">' + esc(ep.overview) + '</span>' : '') +
            (meta.length ? '<span class="vd-ep-meta">' + esc(meta.join(' · ')) + '</span>' : '') +
            '</span>' +
            '<span class="vd-ep-state">' + (ep.owned ? 'Owned' : 'Missing') + '</span>' +
            '</div>';
    }

    function renderEpisodes(seasonNumber) {
        var host = q('[data-vd-episodes]');
        if (!host || !data) return;
        var season = null;
        for (var i = 0; i < data.seasons.length; i++) {
            if (data.seasons[i].season_number === seasonNumber) { season = data.seasons[i]; break; }
        }
        if (!season) { host.innerHTML = ''; return; }
        host.innerHTML =
            '<div class="vd-ep-head"><span class="vd-ep-head-name">' + esc(season.title) + '</span>' +
            '<span class="vd-ep-head-meta">' + season.episode_owned + ' / ' + season.episode_total +
            ' owned</span></div>' +
            '<div class="vd-ep-list">' + season.episodes.map(episodeRow).join('') + '</div>';
    }

    function selectSeason(num) {
        selectedSeason = num;
        var r = root(); if (!r) return;
        var cards = r.querySelectorAll('[data-vd-season]');
        for (var i = 0; i < cards.length; i++) {
            cards[i].classList.toggle('vd-scard--active',
                parseInt(cards[i].getAttribute('data-vd-season'), 10) === num);
        }
        renderEpisodes(num);
    }

    function showLoading(on) { var l = q('[data-vd-loading]'); if (l) l.hidden = !on; }

    function loadShow(id) {
        if (!root()) return;
        showLoading(true);
        ['[data-vd-seasons]', '[data-vd-episodes]'].forEach(function (sel) {
            var n = q(sel); if (n) n.innerHTML = '';
        });
        fetch(DETAIL_URL + 'show/' + id, { headers: { 'Accept': 'application/json' } })
            .then(function (r) { return r.ok ? r.json() : null; })
            .then(function (d) {
                showLoading(false);
                if (!d || d.error) { setText('[data-vd-title]', 'Not found'); return; }
                data = d;
                selectedSeason = d.seasons && d.seasons.length ? d.seasons[0].season_number : null;
                renderHero(d);
                renderSeasons(d);
                var r = root(); if (r) r.scrollTop = 0;
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
        var card = e.target.closest('[data-vd-season]');
        if (card && r.contains(card)) {
            selectSeason(parseInt(card.getAttribute('data-vd-season'), 10));
        }
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
