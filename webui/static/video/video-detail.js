/*
 * SoulSync — Video detail page (isolated, NETFLIX-style — deliberately NOT the
 * music/Spotify layout).
 *
 * A cinematic billboard (full-bleed backdrop, content bottom-left) with a
 * per-show accent sampled from the poster, and a SEASON selector with four
 * switchable views — poster rail / timeline / pills / dropdown — plus a
 * "Missing only" episode filter. Opened by a card via soulsync:video-open-detail;
 * video-side.js navigates, this loads + renders.
 *
 * Self-contained IIFE, no globals, event-delegated, no inline handlers. Talks
 * only to /api/video/* — the music side is never touched.
 */
(function () {
    'use strict';

    var DETAIL_URL = '/api/video/detail/';
    var TMDB_LOGO = 'https://www.themoviedb.org/assets/2/v4/logos/v2/blue_square_2-d537fb228cf3ded904ef09b136fe3fec72548ebc1fea3fbbd1ad9e36364db38b.svg';
    var TVDB_LOGO = 'https://www.svgrepo.com/show/443500/brand-tvdb.svg';
    var VIEW_KEY = 'soulsync_vd_season_view';
    var VIEWS = [
        { id: 'rail', label: 'Rail', ic: '▦' },
        { id: 'timeline', label: 'Timeline', ic: '▭' },
        { id: 'pills', label: 'Tabs', ic: '◉' },
        { id: 'dropdown', label: 'List', ic: '▾' },
    ];

    var data = null;
    var selectedSeason = null;
    var seasonView = 'rail';
    var menuOpen = false;
    var missingOnly = false;
    var currentId = null;
    var currentKind = 'show';
    var artAttemptedFor = null;     // lazy art refresh runs once per detail view

    try { var sv = localStorage.getItem(VIEW_KEY); if (sv) seasonView = sv; } catch (e) { /* ignore */ }

    function esc(s) {
        return String(s == null ? '' : s)
            .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
    }
    function root() { return document.querySelector('[data-video-detail="' + currentKind + '"]'); }
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
    function seasonArt(s) {
        return (s.has_poster && s.id != null) ? '/api/video/poster/season/' + s.id
            : (data && data.has_poster ? '/api/video/poster/show/' + data.id : '');
    }
    function pct(s) { return s.episode_total ? Math.round(s.episode_owned / s.episode_total * 100) : 0; }

    function badge(logo, fallback, title, url) {
        var inner = logo
            ? '<img src="' + logo + '" alt="' + fallback + '" onerror="this.parentNode.textContent=\'' + fallback + '\'">'
            : '<span style="font-size:9px;font-weight:700;">' + fallback + '</span>';
        return url
            ? '<a class="artist-hero-badge" title="' + title + '" href="' + url + '" target="_blank" rel="noopener noreferrer">' + inner + '</a>'
            : '<div class="artist-hero-badge" title="' + title + '">' + inner + '</div>';
    }

    // ── accent extraction (poster → dominant vibrant colour) ──────────────────
    function applyAccent(img) {
        try {
            var w = 24, h = 24, c = document.createElement('canvas'); c.width = w; c.height = h;
            var ctx = c.getContext('2d'); ctx.drawImage(img, 0, 0, w, h);
            var px = ctx.getImageData(0, 0, w, h).data;
            var best = null, bestScore = -1, fr = 0, fg = 0, fb = 0, n = 0;
            for (var i = 0; i < px.length; i += 4) {
                var r = px[i], g = px[i + 1], b = px[i + 2], a = px[i + 3];
                if (a < 128) continue;
                var mx = Math.max(r, g, b), mn = Math.min(r, g, b), light = (mx + mn) / 2;
                fr += r; fg += g; fb += b; n++;
                if (light < 35 || light > 225) continue;
                var sat = mx === 0 ? 0 : (mx - mn) / mx, score = sat * (mx / 255);
                if (score > bestScore) { bestScore = score; best = [r, g, b]; }
            }
            if (!best && n) best = [Math.round(fr / n), Math.round(fg / n), Math.round(fb / n)];
            if (best) { var r0 = root(); if (r0) r0.style.setProperty('--vd-accent-rgb', best[0] + ', ' + best[1] + ', ' + best[2]); }
        } catch (e) { /* tainted/no image — keep theme accent */ }
    }

    // ── billboard ─────────────────────────────────────────────────────────────
    function renderBillboard(d) {
        setText('[data-vd-title]', d.title);
        setText('[data-vd-overview]', d.overview);

        // Clearlogo replaces the text title when available (Netflix/Plex feel).
        var logo = q('[data-vd-logo]');
        var titleEl = q('[data-vd-title]');
        if (logo) {
            if (d.logo) {
                logo.src = d.logo; logo.alt = d.title || ''; logo.hidden = false;
                logo.onerror = function () { logo.hidden = true; if (titleEl) titleEl.classList.remove('vd-title--logo'); };
                if (titleEl) titleEl.classList.add('vd-title--logo');
            } else {
                logo.hidden = true; logo.removeAttribute('src');
                if (titleEl) titleEl.classList.remove('vd-title--logo');
            }
        }

        var art = '/' + d.kind + '/' + d.id;
        var bg = q('[data-vd-backdrop]');
        if (bg) {
            var url = d.has_backdrop ? '/api/video/backdrop' + art
                : (d.has_poster ? '/api/video/poster' + art : '');
            bg.style.backgroundImage = url ? "url('" + url + "')" : '';
            bg.classList.toggle('vd-bb-bg--poster', !d.has_backdrop && !!d.has_poster);
            bg.classList.toggle('vd-bb-bg--empty', !d.has_backdrop && !d.has_poster);
        }
        var poster = q('[data-vd-poster]');
        if (poster && d.has_poster) {
            poster.onload = function () { applyAccent(poster); };
            poster.src = '/api/video/poster' + art;
        }

        var tl = q('[data-vd-tagline]');
        if (tl) { tl.textContent = d.tagline || ''; tl.hidden = !d.tagline; }

        var meta = [];
        if (d.kind === 'show') {
            var ownedPct = d.episode_total ? Math.round(d.episode_owned / d.episode_total * 100) : 0;
            meta.push('<span class="vd-match">' + ownedPct + '% in library</span>');
        } else {
            meta.push(d.owned ? '<span class="vd-match">In library</span>'
                : '<span class="vd-status">Wanted</span>');
        }
        if (d.rating) meta.push('<span class="vd-score">★ ' + (Math.round(d.rating * 10) / 10) + '</span>');
        if (d.year) meta.push('<span>' + esc(d.year) + '</span>');
        if (d.content_rating) meta.push('<span class="vd-meta-rating">' + esc(d.content_rating) + '</span>');
        if (d.kind === 'show') {
            meta.push('<span>' + d.season_count + ' Season' + (d.season_count === 1 ? '' : 's') + '</span>');
            meta.push('<span>' + d.episode_total + ' Episodes</span>');
        }
        var rt = runtimeLabel(d.runtime_minutes);
        if (rt) meta.push('<span>' + esc(rt) + '</span>');
        if (d.kind === 'show' && d.status) meta.push('<span class="vd-status">' + esc(statusLabel(d.status)) + '</span>');
        if (d.network) meta.push('<span>' + esc(d.network) + '</span>');
        if (d.kind === 'movie' && d.studio) meta.push('<span>' + esc(d.studio) + '</span>');
        var m = q('[data-vd-meta]'); if (m) m.innerHTML = meta.join('');

        renderActions(d);

        var l = q('[data-vd-links]');
        if (l) {
            var badges = [];
            if (d.imdb_id) badges.push(badge('', 'IMDb', 'IMDb', 'https://www.imdb.com/title/' + d.imdb_id + '/'));
            if (d.tmdb_id) badges.push(badge(TMDB_LOGO, 'TMDB', 'TMDB',
                'https://www.themoviedb.org/' + (d.kind === 'movie' ? 'movie' : 'tv') + '/' + d.tmdb_id));
            if (d.tvdb_id) badges.push(badge(TVDB_LOGO, 'TVDB', 'TVDB', 'https://thetvdb.com/?id=' + d.tvdb_id + '&tab=series'));
            l.innerHTML = badges.join('');
        }
        var g = q('[data-vd-genres]');
        if (g) {
            g.innerHTML = (d.genres || []).slice(0, 6).map(function (gn) {
                return '<span class="vd-genre">' + esc(gn) + '</span>';
            }).join('');
        }
        renderCast(d);
    }

    function renderCast(d) {
        var section = q('[data-vd-cast-section]');
        if (!section) return;
        var cast = d.cast || [], crew = d.crew || [];
        if (!cast.length && !crew.length) { section.hidden = true; return; }
        section.hidden = false;

        var crewHost = q('[data-vd-crew]');
        if (crewHost) {
            // Group crew by job (Creator / Director / Writer …) → "Job: A, B".
            var byJob = {};
            crew.forEach(function (c) { (byJob[c.job || 'Crew'] = byJob[c.job || 'Crew'] || []).push(c.name); });
            crewHost.innerHTML = Object.keys(byJob).map(function (job) {
                return '<span class="vd-crew-item"><span class="vd-crew-job">' + esc(job) +
                    (byJob[job].length > 1 ? 's' : '') + '</span> ' + esc(byJob[job].join(', ')) + '</span>';
            }).join('');
        }
        var castHost = q('[data-vd-cast]');
        if (castHost) {
            castHost.innerHTML = cast.map(function (p) {
                var img = p.photo
                    ? '<img class="vd-cast-photo" src="' + esc(p.photo) + '" alt="" loading="lazy" onerror="this.style.visibility=\'hidden\'">'
                    : '<span class="vd-cast-photo vd-cast-photo--ph">' + esc((p.name || '?').charAt(0)) + '</span>';
                return '<div class="vd-cast-card">' + img +
                    '<span class="vd-cast-name">' + esc(p.name) + '</span>' +
                    (p.character ? '<span class="vd-cast-char">' + esc(p.character) + '</span>' : '') +
                    '</div>';
            }).join('');
        }
    }

    function renderActions(d) {
        var a = q('[data-vd-actions]');
        if (!a) return;
        var watching = !!d.monitored;
        var html = '';
        if (d.trailer && d.trailer.key) {
            html += '<button class="vd-trailer-btn" type="button" data-vd-act="trailer">' +
                '<span class="vd-trailer-ic">▶</span> Trailer</button>';
        }
        html +=
            '<button class="library-artist-watchlist-btn' + (watching ? ' watching' : '') +
            '" type="button" data-vd-act="watchlist">' +
            '<span class="watchlist-icon">' + (watching ? '✓' : '＋') + '</span>' +
            '<span class="watchlist-text">' + (watching ? 'In Watchlist' : 'Watchlist') + '</span></button>';
        if (d.kind === 'show') {     // "Get Missing" filters the episode list (show-only)
            html += '<button class="discog-download-btn discog-btn-compact" type="button" data-vd-act="missing">' +
                '<span class="discog-btn-icon">⭳</span><span class="discog-btn-text">Get Missing</span>' +
                '<span class="discog-btn-shimmer"></span></button>';
        }
        a.innerHTML = html;
    }

    function renderDetails(d) {
        var host = q('[data-vd-details]');
        if (!host) return;
        var rows = [];
        if (d.release_date) rows.push(['Released', d.release_date]);
        if (d.runtime_minutes) rows.push(['Runtime', runtimeLabel(d.runtime_minutes)]);
        if (d.studio) rows.push(['Studio', d.studio]);
        if (d.status) rows.push(['Status', statusLabel(d.status)]);
        if (d.rating_critic) rows.push(['Critic score', Math.round(d.rating_critic) + '%']);
        if (d.file && d.file.resolution) rows.push(['Quality', String(d.file.resolution).toUpperCase()]);
        host.innerHTML = rows.length
            ? '<div class="vd-detail-grid">' + rows.map(function (r) {
                return '<div class="vd-detail-row"><span class="vd-detail-k">' + esc(r[0]) +
                    '</span><span class="vd-detail-v">' + esc(r[1]) + '</span></div>';
            }).join('') + '</div>'
            : '';
    }

    // ── live TMDB extras (trailer / where-to-watch / similar) ─────────────────
    function resetExtras() {
        ['[data-vd-providers-section]', '[data-vd-similar-section]'].forEach(function (s) {
            var n = q(s); if (n) n.hidden = true;
        });
    }
    function loadExtras(kind, id) {
        fetch(DETAIL_URL + kind + '/' + id + '/extras', { headers: { 'Accept': 'application/json' } })
            .then(function (r) { return r.ok ? r.json() : null; })
            .then(function (ex) { if (ex) renderExtras(kind, id, ex); })
            .catch(function () { /* best-effort */ });
    }
    function renderExtras(kind, id, ex) {
        if (!data || data.id !== id || currentKind !== kind) return;
        data.trailer = ex.trailer || null;
        renderActions(data);

        var ps = q('[data-vd-providers-section]'), ph = q('[data-vd-providers]');
        if (ps && ph) {
            if (ex.providers && ex.providers.length) {
                ps.hidden = false;
                ph.innerHTML = ex.providers.map(function (p) {
                    var img = p.logo ? '<img src="' + esc(p.logo) + '" alt="' + esc(p.name) + '" loading="lazy">'
                        : '<span class="vd-prov-ph">' + esc((p.name || '?').charAt(0)) + '</span>';
                    return '<div class="vd-prov" title="' + esc(p.name) + '">' + img +
                        '<span class="vd-prov-name">' + esc(p.name) + '</span></div>';
                }).join('');
            } else { ps.hidden = true; }
        }
        var ss = q('[data-vd-similar-section]'), sh = q('[data-vd-similar]');
        if (ss && sh) {
            if (ex.similar && ex.similar.length) {
                ss.hidden = false;
                sh.innerHTML = ex.similar.map(function (s) {
                    var poster = s.poster
                        ? '<img class="vd-sim-poster" src="' + esc(s.poster) + '" alt="" loading="lazy">'
                        : '<span class="vd-sim-poster vd-sim-poster--ph">🎬</span>';
                    var url = 'https://www.themoviedb.org/' + (s.kind === 'movie' ? 'movie' : 'tv') + '/' + s.tmdb_id;
                    return '<a class="vd-sim-card" href="' + url + '" target="_blank" rel="noopener">' +
                        poster + '<span class="vd-sim-title">' + esc(s.title) + '</span></a>';
                }).join('');
            } else { ss.hidden = true; }
        }
    }

    // ── trailer modal (YouTube embed) ─────────────────────────────────────────
    function openTrailer(key) {
        if (!key) return;
        var ov = document.getElementById('vd-trailer-overlay');
        if (!ov) {
            ov = document.createElement('div');
            ov.id = 'vd-trailer-overlay';
            ov.className = 'vd-trailer-overlay';
            ov.addEventListener('click', function (e) {
                if (e.target === ov || e.target.closest('[data-vd-trailer-close]')) closeTrailer();
            });
            document.body.appendChild(ov);
        }
        ov.innerHTML = '<div class="vd-trailer-box">' +
            '<button class="vd-trailer-close" type="button" data-vd-trailer-close aria-label="Close">&times;</button>' +
            '<iframe src="https://www.youtube.com/embed/' + encodeURIComponent(key) +
            '?autoplay=1&rel=0" allow="autoplay; encrypted-media; fullscreen" allowfullscreen></iframe></div>';
        ov.classList.add('vd-trailer-overlay--open');
    }
    function closeTrailer() {
        var ov = document.getElementById('vd-trailer-overlay');
        if (ov) { ov.classList.remove('vd-trailer-overlay--open'); ov.innerHTML = ''; }
    }

    // ── season selector (4 views) ─────────────────────────────────────────────
    function renderViewToggle() {
        var host = q('[data-vd-view-toggle]');
        if (!host) return;
        host.innerHTML = VIEWS.map(function (v) {
            return '<button class="vd-vt-btn' + (v.id === seasonView ? ' vd-vt-btn--active' : '') +
                '" type="button" data-vd-view="' + v.id + '" title="' + v.label + '">' +
                '<span class="vd-vt-ic">' + v.ic + '</span></button>';
        }).join('');
    }

    function renderSeasonNav() {
        var host = q('[data-vd-season-nav]');
        if (!host || !data || !data.seasons.length) { if (host) host.innerHTML = ''; return; }
        host.className = 'vd-season-nav vd-season-nav--' + seasonView;
        if (seasonView === 'rail') host.innerHTML = railHTML();
        else if (seasonView === 'timeline') host.innerHTML = timelineHTML();
        else if (seasonView === 'pills') host.innerHTML = pillsHTML();
        else host.innerHTML = dropdownHTML();
    }

    function railHTML() {
        return '<div class="vd-rail">' + data.seasons.map(function (s) {
            var art = seasonArt(s), p = pct(s);
            var on = s.season_number === selectedSeason ? ' vd-rcard--active' : '';
            var img = art ? '<img class="vd-rcard-img" src="' + art + '" alt="" loading="lazy" onerror="this.style.display=\'none\'">' : '';
            return '<button class="vd-rcard' + on + '" type="button" data-vd-season="' + s.season_number + '">' +
                '<div class="vd-rcard-art">' + img + '<div class="vd-rcard-fb">📺</div>' +
                '<div class="vd-rcard-grad"></div><div class="vd-rcard-pct">' + p + '%</div></div>' +
                '<div class="vd-rcard-info"><span class="vd-rcard-name">' + esc(s.title) + '</span>' +
                '<span class="vd-rcard-sub">' + s.episode_owned + ' / ' + s.episode_total + ' eps</span>' +
                '<span class="vd-rcard-bar"><span style="width:' + p + '%"></span></span></div></button>';
        }).join('') + '</div>';
    }

    function timelineHTML() {
        var total = data.seasons.reduce(function (a, s) { return a + Math.max(1, s.episode_total); }, 0) || 1;
        return '<div class="vd-timeline">' + data.seasons.map(function (s) {
            var p = pct(s), grow = Math.max(1, s.episode_total);
            var on = s.season_number === selectedSeason ? ' vd-tseg--active' : '';
            return '<button class="vd-tseg' + on + '" type="button" data-vd-season="' + s.season_number + '" ' +
                'style="flex:' + grow + ' 1 0">' +
                '<span class="vd-tseg-fill" style="width:' + p + '%"></span>' +
                '<span class="vd-tseg-label"><span class="vd-tseg-name">' + esc(s.title) + '</span>' +
                '<span class="vd-tseg-meta">' + s.episode_owned + '/' + s.episode_total + '</span></span></button>';
        }).join('') + '</div>';
    }

    function pillsHTML() {
        return '<div class="vd-pills">' + data.seasons.map(function (s) {
            var on = s.season_number === selectedSeason ? ' vd-pill-btn--active' : '';
            return '<button class="vd-pill-btn' + on + '" type="button" data-vd-season="' + s.season_number + '">' +
                esc(s.title) + '<span class="vd-pill-meta">' + s.episode_owned + '/' + s.episode_total + '</span></button>';
        }).join('') + '</div>';
    }

    function dropdownHTML() {
        var cur = seasonByNum(selectedSeason);
        return '<div class="vd-season-select">' +
            '<button class="vd-ss-btn" type="button" data-vd-ss-toggle>' +
            '<span>' + esc(cur ? cur.title : 'Season') + '</span><span class="vd-ss-caret">▾</span></button>' +
            '<div class="vd-ss-menu' + (menuOpen ? ' vd-ss-menu--open' : '') + '">' +
            data.seasons.map(function (s) {
                var on = s.season_number === selectedSeason ? ' vd-ss-opt--active' : '';
                return '<button class="vd-ss-opt' + on + '" type="button" data-vd-season="' + s.season_number + '">' +
                    esc(s.title) + '<span class="vd-ss-opt-meta">' + s.episode_owned + '/' + s.episode_total + '</span></button>';
            }).join('') + '</div></div>';
    }

    // ── episodes ──────────────────────────────────────────────────────────────
    function episodeRow(ep) {
        var owned = ep.owned ? 'vd-ep--owned' : 'vd-ep--missing';
        var meta = [];
        var rt = runtimeLabel(ep.runtime_minutes); if (rt) meta.push(rt);
        if (ep.air_date) meta.push(ep.air_date);
        var still = ep.has_still
            ? '<img class="vd-ep-still" src="/api/video/poster/episode/' + ep.id + '" alt="" loading="lazy" onerror="this.style.display=\'none\'">'
            : '';
        return '<div class="vd-ep ' + owned + '">' +
            '<div class="vd-ep-index">' + (ep.episode_number != null ? ep.episode_number : '') + '</div>' +
            '<div class="vd-ep-thumb">' + still + '<span class="vd-ep-thumb-ic">▶</span></div>' +
            '<div class="vd-ep-info"><div class="vd-ep-top"><span class="vd-ep-title">' +
            esc(ep.title || 'Episode ' + ep.episode_number) + '</span>' +
            (meta.length ? '<span class="vd-ep-rt">' + esc(meta.join(' · ')) + '</span>' : '') + '</div>' +
            (ep.overview ? '<p class="vd-ep-desc">' + esc(ep.overview) + '</p>' : '') + '</div>' +
            '<div class="vd-ep-badge">' + (ep.owned ? 'Owned' : 'Missing') + '</div></div>';
    }

    function renderEpisodes() {
        var host = q('[data-vd-episodes]');
        if (!host) return;
        var season = seasonByNum(selectedSeason);
        if (!season) { host.innerHTML = ''; return; }
        var eps = missingOnly ? season.episodes.filter(function (e) { return !e.owned; }) : season.episodes;
        host.innerHTML = eps.length
            ? eps.map(episodeRow).join('')
            : '<div class="vd-ep-empty">No ' + (missingOnly ? 'missing ' : '') + 'episodes here. 🎉</div>';
        host.classList.remove('vd-ep-anim'); void host.offsetWidth; host.classList.add('vd-ep-anim');
    }

    function selectSeason(n) {
        selectedSeason = n; menuOpen = false;
        renderSeasonNav(); renderEpisodes();
    }
    function setView(v) {
        seasonView = v; menuOpen = false;
        try { localStorage.setItem(VIEW_KEY, v); } catch (e) { /* ignore */ }
        renderViewToggle(); renderSeasonNav();
    }

    function showLoading(on) { var l = q('[data-vd-loading]'); if (l) l.hidden = !on; }

    // ── watchlist (real monitor toggle) ───────────────────────────────────────
    function toggleWatchlist() {
        if (!data) return;
        var next = data.monitored ? 0 : 1;
        fetch('/api/video/monitor', {
            method: 'POST', headers: { 'Content-Type': 'application/json', 'Accept': 'application/json' },
            body: JSON.stringify({ kind: data.kind, id: data.id, monitored: next }),
        }).then(function (r) { return r.ok ? r.json() : null; })
            .then(function (res) {
                if (res && !res.error) { data.monitored = !!next; renderActions(data); }
            }).catch(function () { /* ignore */ });
    }

    // ── movie detail (flat) ───────────────────────────────────────────────────
    function loadMovie(id) {
        currentKind = 'movie';
        if (!root()) return;
        if (currentId !== id) artAttemptedFor = null;
        currentId = id;
        showLoading(true);
        resetExtras();
        var dh = q('[data-vd-details]'); if (dh) dh.innerHTML = '';
        var r0 = root(); if (r0) r0.style.removeProperty('--vd-accent-rgb');
        fetch(DETAIL_URL + 'movie/' + id, { headers: { 'Accept': 'application/json' } })
            .then(function (r) { return r.ok ? r.json() : null; })
            .then(function (d) {
                showLoading(false);
                if (!d || d.error) { setText('[data-vd-title]', 'Not found'); return; }
                data = d;
                renderBillboard(d);
                renderDetails(d);
                var sub = document.querySelector('.video-subpage[data-video-subpage="video-movie-detail"]');
                if (sub) sub.scrollTop = 0;
                maybeRefreshMovie(id);
                loadExtras('movie', id);
            })
            .catch(function () { showLoading(false); setText('[data-vd-title]', 'Could not load movie'); });
    }

    // Lazy: backfill a movie's cast/genres/art from TMDB on view if missing.
    function maybeRefreshMovie(id) {
        if (artAttemptedFor === id || !data || data.id !== id) return;
        var needs = !(data.cast && data.cast.length) || !(data.genres && data.genres.length)
            || !data.has_backdrop || !data.logo;
        if (!needs) return;
        artAttemptedFor = id;
        fetch(DETAIL_URL + 'movie/' + id + '/refresh-art',
            { method: 'POST', headers: { 'Accept': 'application/json' } })
            .then(function (r) { return r.ok ? r.json() : null; })
            .then(function (res) {
                if (res && res.ok && currentId === id && currentKind === 'movie') {
                    fetch(DETAIL_URL + 'movie/' + id, { headers: { 'Accept': 'application/json' } })
                        .then(function (r) { return r.ok ? r.json() : null; })
                        .then(function (d) {
                            if (d && !d.error && currentId === id) { data = d; renderBillboard(d); renderDetails(d); }
                        });
                }
            }).catch(function () { /* best-effort */ });
    }

    function loadShow(id) {
        currentKind = 'show';
        if (!root()) return;
        if (currentId !== id) artAttemptedFor = null;
        currentId = id;
        showLoading(true);
        resetExtras();
        ['[data-vd-episodes]', '[data-vd-season-nav]'].forEach(function (s) { var n = q(s); if (n) n.innerHTML = ''; });
        var r0 = root(); if (r0) r0.style.removeProperty('--vd-accent-rgb');
        fetch(DETAIL_URL + 'show/' + id, { headers: { 'Accept': 'application/json' } })
            .then(function (r) { return r.ok ? r.json() : null; })
            .then(function (d) {
                showLoading(false);
                if (!d || d.error) { setText('[data-vd-title]', 'Not found'); return; }
                data = d; menuOpen = false; missingOnly = false;
                selectedSeason = d.seasons && d.seasons.length ? d.seasons[0].season_number : null;
                var mt = q('[data-vd-missing-toggle]');
                if (mt) { mt.hidden = !(d.seasons && d.seasons.length); mt.classList.remove('vd-missing-toggle--on'); }
                renderBillboard(d);
                renderViewToggle(); renderSeasonNav(); renderEpisodes();
                var sub = document.querySelector('.video-subpage[data-video-subpage="video-show-detail"]');
                if (sub) sub.scrollTop = 0;
                maybeRefreshArt(id);
                loadExtras('show', id);
            })
            .catch(function () { showLoading(false); setText('[data-vd-title]', 'Could not load show'); });
    }

    // Lazy art: if any season lacks a poster, pull it from TMDB on view and cache
    // it (once per show), then re-render. Sidesteps "already matched, never re-runs".
    function maybeRefreshArt(id) {
        if (artAttemptedFor === id || !data || data.id !== id) return;
        var needs = !data.logo || (data.seasons || []).some(function (s) { return !s.has_poster; });
        if (!needs) return;
        artAttemptedFor = id;
        fetch(DETAIL_URL + 'show/' + id + '/refresh-art',
            { method: 'POST', headers: { 'Accept': 'application/json' } })
            .then(function (r) { return r.ok ? r.json() : null; })
            .then(function (res) { if (res && res.ok && currentId === id) reloadDetail(id); })
            .catch(function () { /* best-effort */ });
    }

    function reloadDetail(id) {
        fetch(DETAIL_URL + 'show/' + id, { headers: { 'Accept': 'application/json' } })
            .then(function (r) { return r.ok ? r.json() : null; })
            .then(function (d) {
                if (!d || d.error || currentId !== id) return;
                data = d;
                if (!seasonByNum(selectedSeason)) {
                    selectedSeason = d.seasons && d.seasons.length ? d.seasons[0].season_number : null;
                }
                renderBillboard(d); renderSeasonNav(); renderEpisodes();
            })
            .catch(function () { /* ignore */ });
    }

    // ── events ────────────────────────────────────────────────────────────────
    function onOpen(e) {
        if (!e || !e.detail) return;
        if (e.detail.kind === 'movie') loadMovie(e.detail.id);
        else if (e.detail.kind === 'show') loadShow(e.detail.id);
    }

    function onClick(e) {
        var r = root(); if (!r) return;
        var seasonBtn = e.target.closest('[data-vd-season]');
        if (seasonBtn && r.contains(seasonBtn)) { selectSeason(parseInt(seasonBtn.getAttribute('data-vd-season'), 10)); return; }
        var viewBtn = e.target.closest('[data-vd-view]');
        if (viewBtn && r.contains(viewBtn)) { setView(viewBtn.getAttribute('data-vd-view')); return; }
        var ssToggle = e.target.closest('[data-vd-ss-toggle]');
        if (ssToggle && r.contains(ssToggle)) { menuOpen = !menuOpen; renderSeasonNav(); return; }
        var act = e.target.closest('[data-vd-act]');
        if (act && r.contains(act)) {
            var which = act.getAttribute('data-vd-act');
            if (which === 'watchlist') toggleWatchlist();
            else if (which === 'missing') toggleMissing();
            else if (which === 'trailer' && data && data.trailer) openTrailer(data.trailer.key);
            return;
        }
        var mt = e.target.closest('[data-vd-missing-toggle]');
        if (mt && r.contains(mt)) { toggleMissing(); return; }
        if (menuOpen && !e.target.closest('[data-vd-season-nav]')) { menuOpen = false; renderSeasonNav(); }
    }

    function toggleMissing() {
        missingOnly = !missingOnly;
        var mt = q('[data-vd-missing-toggle]');
        if (mt) mt.classList.toggle('vd-missing-toggle--on', missingOnly);
        renderEpisodes();
    }

    function init() {
        document.addEventListener('soulsync:video-open-detail', onOpen);
        document.addEventListener('click', onClick);
        document.addEventListener('keydown', function (e) {
            if (e.key === 'Escape') closeTrailer();
        });
    }

    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init);
    else init();
})();
