/*
 * SoulSync — Video detail page (isolated).
 *
 * Drill-in TV-show detail: hero (backdrop + poster + title + badges + stats) and
 * the seasons → episodes tree (season = album, episode = track — inspired by the
 * music artist page). Opened by a card via the soulsync:video-open-detail event;
 * video-side.js handles the navigation, this loads + renders the data.
 *
 * Self-contained IIFE, no globals, event-delegated, no inline handlers. Talks
 * only to /api/video/* — the music side is never touched.
 */
(function () {
    'use strict';

    var DETAIL_URL = '/api/video/detail/';
    var loaded = { show: null };   // remember the last show id we rendered

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

    // ── hero ────────────────────────────────────────────────────────────────
    function renderHero(d) {
        setText('[data-vd-title]', d.title);
        setText('[data-vd-overview]', d.overview);

        var backdrop = q('[data-vd-backdrop]');
        if (backdrop) {
            backdrop.style.backgroundImage = d.has_backdrop
                ? "url('/api/video/backdrop/show/" + d.id + "')"
                : (d.has_poster ? "url('/api/video/poster/show/" + d.id + "')" : '');
            backdrop.classList.toggle('vd-backdrop--empty', !d.has_backdrop && !d.has_poster);
        }

        var poster = q('[data-vd-poster]');
        var fallback = q('[data-vd-poster-fallback]');
        if (poster && fallback) {
            if (d.has_poster) {
                poster.src = '/api/video/poster/show/' + d.id;
                poster.style.display = '';
                fallback.style.display = 'none';
                poster.onerror = function () { poster.style.display = 'none'; fallback.style.display = ''; };
            } else {
                poster.style.display = 'none';
                fallback.style.display = '';
            }
        }

        var badges = [];
        if (d.year) badges.push(pill(d.year));
        if (d.content_rating) badges.push(pill(d.content_rating, 'vd-pill--rating'));
        if (d.status) badges.push(pill(d.status === 'continuing' ? 'Continuing'
            : d.status === 'ended' ? 'Ended' : d.status));
        if (d.network) badges.push(pill(d.network));
        var rt = runtimeLabel(d.runtime_minutes);
        if (rt) badges.push(pill(rt));
        var b = q('[data-vd-badges]');
        if (b) b.innerHTML = badges.join('');

        // Stat tiles — seasons / episodes / owned coverage.
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

    // ── seasons → episodes tree ───────────────────────────────────────────────
    function episodeRow(ep) {
        var num = ep.episode_number != null ? ('E' + ep.episode_number) : '';
        var owned = ep.owned ? 'vd-ep--owned' : 'vd-ep--missing';
        var meta = [];
        if (ep.air_date) meta.push(ep.air_date);
        var rt = runtimeLabel(ep.runtime_minutes);
        if (rt) meta.push(rt);
        return '<div class="vd-ep ' + owned + '">' +
            '<span class="vd-ep-num">' + esc(num) + '</span>' +
            '<span class="vd-ep-body"><span class="vd-ep-title">' + esc(ep.title || 'Episode ' + ep.episode_number) + '</span>' +
            (meta.length ? '<span class="vd-ep-meta">' + esc(meta.join(' · ')) + '</span>' : '') +
            '</span>' +
            '<span class="vd-ep-state">' + (ep.owned ? 'Owned' : 'Missing') + '</span>' +
            '</div>';
    }

    function seasonBlock(season, idx) {
        var pct = season.episode_total ? Math.round(season.episode_owned / season.episode_total * 100) : 0;
        var open = idx === 0 ? ' vd-season--open' : '';
        return '<div class="vd-season' + open + '" data-vd-season="' + season.season_number + '">' +
            '<button class="vd-season-head" type="button" data-vd-season-toggle>' +
            '<span class="vd-season-name">' + esc(season.title) + '</span>' +
            '<span class="vd-season-meta">' + season.episode_owned + ' / ' + season.episode_total + ' · ' + pct + '%</span>' +
            '<span class="vd-season-bar"><span class="vd-season-bar-fill" style="width:' + pct + '%"></span></span>' +
            '<span class="vd-season-caret" aria-hidden="true">▾</span>' +
            '</button>' +
            '<div class="vd-season-eps">' + season.episodes.map(episodeRow).join('') + '</div>' +
            '</div>';
    }

    function renderSeasons(d) {
        var host = q('[data-vd-seasons]');
        if (!host) return;
        if (!d.seasons || !d.seasons.length) {
            host.innerHTML = '<div class="vd-empty">No seasons found for this show yet.</div>';
            return;
        }
        host.innerHTML = d.seasons.map(seasonBlock).join('');
    }

    function showLoading(on) {
        var l = q('[data-vd-loading]');
        if (l) l.hidden = !on;
    }

    function loadShow(id) {
        if (!root()) return;
        loaded.show = id;
        showLoading(true);
        var seasons = q('[data-vd-seasons]');
        if (seasons) seasons.innerHTML = '';
        fetch(DETAIL_URL + 'show/' + id, { headers: { 'Accept': 'application/json' } })
            .then(function (r) { return r.ok ? r.json() : null; })
            .then(function (d) {
                showLoading(false);
                if (!d || d.error) { setText('[data-vd-title]', 'Not found'); return; }
                renderHero(d);
                renderSeasons(d);
            })
            .catch(function () { showLoading(false); setText('[data-vd-title]', 'Could not load show'); });
    }

    // ── events ────────────────────────────────────────────────────────────────
    function onOpen(e) {
        if (!e || !e.detail || e.detail.kind !== 'show') return;
        loadShow(e.detail.id);
    }

    function onClick(e) {
        var r = root();
        if (!r) return;
        var toggle = e.target.closest('[data-vd-season-toggle]');
        if (toggle && r.contains(toggle)) {
            var block = toggle.closest('.vd-season');
            if (block) block.classList.toggle('vd-season--open');
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
