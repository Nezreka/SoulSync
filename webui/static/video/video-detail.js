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
    // Real media-server logos for the "Play on your server" watch tile (same
    // sources as the header server toggle).
    var SERVER_LOGOS = {
        Plex: 'https://www.plex.tv/wp-content/themes/plex/assets/img/plex-logo.svg',
        Jellyfin: 'https://cdn.jsdelivr.net/gh/homarr-labs/dashboard-icons/png/jellyfin.png',
    };
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
    var currentSource = 'library';  // 'library' (video.db) or 'tmdb' (live preview)
    var artAttemptedFor = null;     // lazy art refresh runs once per detail view

    var TMDB_URL = '/api/video/tmdb/';
    function detailURL(kind, id, source) {
        return source === 'tmdb' ? TMDB_URL + kind + '/' + id : DETAIL_URL + kind + '/' + id;
    }

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
        if (data && data.source === 'tmdb') return s.poster_url || data.poster_url || '';
        return (s.has_poster && s.id != null) ? '/api/video/poster/season/' + s.id
            : (data && data.has_poster ? '/api/video/poster/show/' + data.id : '');
    }
    // Source-aware billboard art: library items proxy through /api/video; tmdb
    // (preview) items use the direct image URLs in the payload.
    function bbBackdrop(d) {
        if (d.source === 'tmdb') return d.backdrop_url || d.poster_url || '';
        var art = '/' + d.kind + '/' + d.id;
        return d.has_backdrop ? '/api/video/backdrop' + art : (d.has_poster ? '/api/video/poster' + art : '');
    }
    function bbPoster(d) {
        // The offscreen poster is canvas-sampled for the accent — must be
        // same-origin, so tmdb (preview) posters go through our image proxy.
        if (d.source === 'tmdb') return d.poster_url ? proxied(d.poster_url) : '';
        return d.has_poster ? '/api/video/poster/' + d.kind + '/' + d.id : '';
    }
    function proxied(url) {
        return /^https:\/\/image\.tmdb\.org\//.test(url || '')
            ? '/api/video/img?u=' + encodeURIComponent(url) : (url || '');
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

        var bg = q('[data-vd-backdrop]');
        if (bg) {
            var url = bbBackdrop(d);
            bg.style.backgroundImage = url ? "url('" + url + "')" : '';
            bg.classList.toggle('vd-bb-bg--poster', !d.has_backdrop && !!d.has_poster);
            bg.classList.toggle('vd-bb-bg--empty', !d.has_backdrop && !d.has_poster);
        }
        var poster = q('[data-vd-poster]');
        var posterUrl = bbPoster(d);
        if (poster && posterUrl) {
            poster.onload = function () { applyAccent(poster); };
            poster.src = posterUrl;
        }

        var tl = q('[data-vd-tagline]');
        if (tl) { tl.textContent = d.tagline || ''; tl.hidden = !d.tagline; }

        var meta = [];
        if (d.source === 'tmdb') {
            meta.push('<span class="vd-status vd-status--preview">Preview</span>');
        } else if (d.kind === 'show') {
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
        if (l && d.source === 'tmdb') {
            l.innerHTML = '';                     // preview items keep everything in-app
        } else if (l) {
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
        renderRatings(d);
        renderCrewLine(d);
        renderNextEpisode(d);
        renderCast(d);
    }

    // "Directed by …" (movie) / "Created by …" (show) surfaced in the hero.
    // A crew member's name, clickable → person page when we have a TMDB id.
    function personName(c) {
        return c.tmdb_id
            ? '<a class="vd-person-link" href="/video-detail/tmdb/person/' + c.tmdb_id +
              '" data-vd-person="' + c.tmdb_id + '">' + esc(c.name) + '</a>'
            : esc(c.name);
    }

    function renderCrewLine(d) {
        var el = q('[data-vd-crew-line]');
        if (!el) return;
        var key = d.kind === 'movie' ? 'Director' : 'Creator';
        var people = (d.crew || []).filter(function (c) { return c.job === key; }).slice(0, 3);
        if (!people.length) { el.hidden = true; el.innerHTML = ''; return; }
        var label = (d.kind === 'movie' ? 'Director' : 'Creator') + (people.length > 1 ? 's' : '');
        el.innerHTML = '<span class="vd-crew-line-k">' + label + '</span> ' +
            people.map(personName).join(', ');
        el.hidden = false;
    }

    function fmtDate(s) {
        if (!s) return '';
        var p = String(s).split('-');
        if (p.length < 3) return s;
        var months = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
        return (months[parseInt(p[1], 10) - 1] || '') + ' ' + parseInt(p[2], 10) + ', ' + p[0];
    }

    // "Next episode" banner for continuing shows (data.next_episode arrives w/ extras).
    function renderNextEpisode(d) {
        var el = q('[data-vd-next-ep]');
        if (!el) return;
        var ne = d.next_episode;
        if (d.kind !== 'show' || !ne || !ne.air_date) { el.hidden = true; el.innerHTML = ''; return; }
        var code = 'S' + ne.season_number + ' · E' + ne.episode_number;
        el.innerHTML = '<span class="vd-next-ep-badge">▸ Next Episode</span>' +
            '<span class="vd-next-ep-code">' + esc(code) + '</span>' +
            (ne.name ? '<span class="vd-next-ep-name">' + esc(ne.name) + '</span>' : '') +
            '<span class="vd-next-ep-when">' + esc(fmtDate(ne.air_date)) + '</span>';
        el.hidden = false;
    }

    function renderRatings(d) {
        var host = q('[data-vd-ratings]');
        if (!host) return;
        var items = [];
        if (d.imdb_rating) {
            items.push('<span class="vd-rt vd-rt--imdb"><span class="vd-rt-tag">IMDb</span>' +
                (Math.round(d.imdb_rating * 10) / 10) + '</span>');
        }
        if (d.rt_rating != null) {
            var fresh = d.rt_rating >= 60;
            items.push('<span class="vd-rt vd-rt--rt"><span class="vd-rt-ic">' +
                (fresh ? '🍅' : '🤢') + '</span>' + d.rt_rating + '%</span>');
        }
        if (d.metacritic != null) {
            var cls = d.metacritic >= 61 ? 'good' : d.metacritic >= 40 ? 'mid' : 'bad';
            items.push('<span class="vd-rt vd-rt--mc vd-rt--mc-' + cls + '">' +
                '<span class="vd-rt-tag">MC</span>' + d.metacritic + '</span>');
        }
        host.innerHTML = items.join('');
        host.hidden = !items.length;
    }

    function renderCast(d) {
        var section = q('[data-vd-cast-section]');
        if (!section) return;
        var cast = d.cast || [], crew = d.crew || [];
        if (!cast.length && !crew.length) { section.hidden = true; return; }
        section.hidden = false;

        var crewHost = q('[data-vd-crew]');
        if (crewHost) {
            // Group crew by job (Creator / Director / Writer …) → "Job: A, B" with
            // each name clickable → person page.
            var byJob = {};
            crew.forEach(function (c) { (byJob[c.job || 'Crew'] = byJob[c.job || 'Crew'] || []).push(c); });
            crewHost.innerHTML = Object.keys(byJob).map(function (job) {
                return '<span class="vd-crew-item"><span class="vd-crew-job">' + esc(job) +
                    (byJob[job].length > 1 ? 's' : '') + '</span> ' +
                    byJob[job].map(personName).join(', ') + '</span>';
            }).join('');
        }
        var castHost = q('[data-vd-cast]');
        if (castHost) {
            castHost.innerHTML = cast.map(function (p) {
                var img = p.photo
                    ? '<img class="vd-cast-photo" src="' + esc(p.photo) + '" alt="" loading="lazy" onerror="this.style.visibility=\'hidden\'">'
                    : '<span class="vd-cast-photo vd-cast-photo--ph">' + esc((p.name || '?').charAt(0)) + '</span>';
                var inner = img +
                    '<span class="vd-cast-name">' + esc(p.name) + '</span>' +
                    (p.character ? '<span class="vd-cast-char">' + esc(p.character) + '</span>' : '');
                // Clickable → in-app person page when we have a TMDB person id.
                return p.tmdb_id
                    ? '<a class="vd-cast-card vd-cast-card--link" href="/video-detail/tmdb/person/' + p.tmdb_id +
                      '" data-vd-person="' + p.tmdb_id + '">' + inner + '</a>'
                    : '<div class="vd-cast-card">' + inner + '</div>';
            }).join('');
        }
    }

    function renderActions(d) {
        var a = q('[data-vd-actions]');
        if (!a) return;
        var watching = !!d.monitored;
        var html = '';
        // Primary CTA: play it on your media server (owned items; arrives with
        // extras). The logo IS the brand name — "Play on <logo>" (no redundant word).
        if (d.server && d.server.url) {
            var sv = esc(d.server.server || 'Server');
            var slogo = SERVER_LOGOS[d.server.server];
            var inner = slogo
                ? '<span class="vd-play-ic">▶</span><span>Play on</span>' +
                  '<img class="vd-play-logo" src="' + esc(slogo) + '" alt="' + sv + '">'
                : '<span class="vd-play-ic">▶</span><span>Play on ' + sv + '</span>';
            html += '<a class="vd-play-btn" href="' + esc(d.server.url) +
                '" target="_blank" rel="noopener" title="Play on ' + sv + '">' + inner + '</a>';
        }
        if (d.trailer && d.trailer.key) {
            html += '<button class="vd-trailer-btn" type="button" data-vd-act="trailer">' +
                '<span class="vd-trailer-ic">▶</span> Trailer</button>';
        }
        // Preview (tmdb, un-owned) items have no library row to monitor — acquisition
        // (add-to-watchlist / get-missing) lands with the downloads phase.
        if (d.source === 'tmdb') { a.innerHTML = html; return; }
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

    function mediaRes(r) {
        if (!r) return '';
        r = String(r).toLowerCase();
        if (r.indexOf('2160') > -1 || r === '4k') return '4K';
        if (r.indexOf('1080') > -1) return '1080p';
        if (r.indexOf('720') > -1) return '720p';
        if (r.indexOf('480') > -1 || r.indexOf('576') > -1) return 'SD';
        return r.toUpperCase();
    }
    function prettyCodec(c) {
        if (!c) return '';
        var l = String(c).toLowerCase();
        if (l.indexOf('hevc') > -1 || l.indexOf('265') > -1) return 'HEVC';
        if (l.indexOf('264') > -1 || l === 'avc') return 'H.264';
        if (l.indexOf('av1') > -1) return 'AV1';
        if (l.indexOf('vp9') > -1) return 'VP9';
        return String(c).toUpperCase();
    }
    function prettySource(s) {
        var map = { bluray: 'Blu-ray', 'web-dl': 'WEB-DL', webdl: 'WEB-DL', webrip: 'WEBRip',
            hdtv: 'HDTV', youtube: 'YouTube', dvd: 'DVD', remux: 'Remux' };
        return map[String(s || '').toLowerCase()] || String(s || '');
    }
    function fmtBytes(n) {
        if (!n) return '';
        var gb = n / 1073741824;
        return gb >= 1 ? (Math.round(gb * 10) / 10) + ' GB' : Math.round(n / 1048576) + ' MB';
    }
    function fileSummary(v) {
        return [mediaRes(v.resolution), prettyCodec(v.video_codec),
            v.audio_codec ? String(v.audio_codec).toUpperCase() : '', fmtBytes(v.size_bytes),
            v.release_source ? prettySource(v.release_source) : ''].filter(Boolean).join(' · ');
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
        // Your media — the technical specs we scanned (Plex-grade).
        var f = d.file;
        if (f) {
            if (f.resolution) rows.push(['Quality', mediaRes(f.resolution)]);
            if (f.video_codec) rows.push(['Video', prettyCodec(f.video_codec)]);
            if (f.audio_codec) rows.push(['Audio', String(f.audio_codec).toUpperCase()]);
            if (f.release_source) rows.push(['Source', prettySource(f.release_source)]);
            if (f.size_bytes) rows.push(['Size', fmtBytes(f.size_bytes)]);
        }
        var html = rows.length
            ? '<div class="vd-detail-grid">' + rows.map(function (r) {
                return '<div class="vd-detail-row"><span class="vd-detail-k">' + esc(r[0]) +
                    '</span><span class="vd-detail-v">' + esc(r[1]) + '</span></div>';
            }).join('') + '</div>'
            : '';
        // Multiple versions / editions you own.
        var files = d.files || [];
        if (files.length > 1) {
            html += '<div class="vd-versions"><div class="vd-versions-h">' + files.length + ' versions</div>' +
                files.map(function (v) {
                    return '<div class="vd-version">' + esc(fileSummary(v)) + '</div>';
                }).join('') + '</div>';
        }
        host.innerHTML = html;
    }

    // ── live TMDB extras (trailer / where-to-watch / similar) ─────────────────
    function resetExtras() {
        ['[data-vd-providers-section]', '[data-vd-similar-section]', '[data-vd-collection-section]',
         '[data-vd-next-ep]', '[data-vd-crew-line]', '[data-vd-season-overview]',
         '[data-vd-facts-section]', '[data-vd-videos-section]', '[data-vd-gallery-section]',
         '[data-vd-review-section]', '[data-vd-cast-all]'].forEach(function (s) {
            var n = q(s); if (n) n.hidden = true;
        });
        galleryImages = [];
        stopBillboardTrailer();
    }
    function loadExtras(kind, id) {
        fetch(DETAIL_URL + kind + '/' + id + '/extras', { headers: { 'Accept': 'application/json' } })
            .then(function (r) { return r.ok ? r.json() : null; })
            .then(function (ex) { if (ex) renderExtras(kind, id, ex); })
            .catch(function () { /* best-effort */ });
    }
    function simCard(s) {
        var poster = s.poster
            ? '<img class="vd-sim-poster" src="' + esc(s.poster) + '" alt="" loading="lazy">'
            : '<span class="vd-sim-poster vd-sim-poster--ph">🎬</span>';
        var simKind = s.kind === 'movie' ? 'movie' : 'show';
        var yr = s.year ? '<span class="vd-sim-year">' + esc(s.year) + '</span>' : '';
        return '<a class="vd-sim-card" href="/video-detail/tmdb/' + simKind + '/' + s.tmdb_id +
            '" data-vd-sim="' + simKind + '" data-vd-sim-id="' + s.tmdb_id + '">' +
            poster + '<span class="vd-sim-title">' + esc(s.title) + '</span>' + yr + '</a>';
    }
    function renderRow(sectionSel, hostSel, items) {
        var sec = q(sectionSel), host = q(hostSel);
        if (!sec || !host) return;
        if (!items || !items.length) { sec.hidden = true; return; }
        sec.hidden = false;
        host.innerHTML = items.map(simCard).join('');
    }

    function renderExtras(kind, id, ex) {
        if (!data || data.id !== id || currentKind !== kind) return;
        data.trailer = ex.trailer || null;
        data.server = ex.server || null;
        data.next_episode = ex.next_episode || null;
        renderActions(data);
        renderNextEpisode(data);

        var ps = q('[data-vd-providers-section]'), ph = q('[data-vd-providers]');
        if (ps && ph) {
            var html = '';
            // If it's on your media server, that's the best place to watch — lead
            // with a "Play on Plex/Jellyfin" tile that deep-links to the item.
            if (ex.server && ex.server.url) {
                var sv = esc(ex.server.server || 'Server');
                var slogo = SERVER_LOGOS[ex.server.server];
                var sicon = slogo
                    ? '<span class="vd-prov-ph vd-prov-server-logo"><img src="' + esc(slogo) + '" alt="' + sv +
                      '" onerror="this.parentNode.textContent=\'▶\'"></span>'
                    : '<span class="vd-prov-ph vd-prov-play">▶</span>';
                html += '<a class="vd-prov vd-prov--server" href="' + esc(ex.server.url) +
                    '" target="_blank" rel="noopener" title="Play on ' + sv + '">' +
                    sicon + '<span class="vd-prov-name">Play on ' + sv + '</span></a>';
            }
            // Streaming providers: TMDB only gives ONE aggregate 'where to watch'
            // link (not per-provider), so showing N identical links is misleading.
            // Render the logos as availability BADGES, then a single link to the
            // watch page. (Drop a provider matching your server tile, e.g. Plex.)
            var link = ex.providers_link || '';
            var srvName = (ex.server && ex.server.server || '').toLowerCase();
            var provs = (ex.providers || []).filter(function (p) {
                return (p.name || '').toLowerCase() !== srvName;
            });
            if (provs.length) {
                html += provs.map(function (p) {
                    var img = p.logo ? '<img src="' + esc(p.logo) + '" alt="' + esc(p.name) + '" loading="lazy">'
                        : '<span class="vd-prov-ph">' + esc((p.name || '?').charAt(0)) + '</span>';
                    return '<div class="vd-prov vd-prov--badge" title="' + esc(p.name) + '">' + img +
                        '<span class="vd-prov-name">' + esc(p.name) + '</span></div>';
                }).join('');
                if (link) {
                    html += '<a class="vd-prov vd-prov--more" href="' + esc(link) +
                        '" target="_blank" rel="noopener" title="See where to watch (JustWatch)">' +
                        '<span class="vd-prov-ph vd-prov-more-ic">↗</span>' +
                        '<span class="vd-prov-name">Where to watch</span></a>';
                }
            }
            ps.hidden = !html;
            ph.innerHTML = html;
            if (!ps.hidden) {
                loadPrefs(function (p) {
                    var h = ps.querySelector('.vd-section-h');
                    if (h) h.textContent = 'Where to Watch' + (p && p.watch_region ? ' · ' + p.watch_region : '');
                });
            }
        }
        // Franchise / collection (movies) — the other films in the set.
        var cs = q('[data-vd-collection-section]'), ch = q('[data-vd-collection]'), ct = q('[data-vd-collection-title]');
        var coll = ex.collection;
        if (cs && ch) {
            if (coll && coll.items && coll.items.length) {
                cs.hidden = false;
                if (ct) ct.textContent = coll.name || 'Collection';
                ch.innerHTML = coll.items.map(simCard).join('');
            } else { cs.hidden = true; }
        }

        // "More Like This" — recommendations (better-curated), falling back to similar.
        var more = (ex.recommendations && ex.recommendations.length) ? ex.recommendations : ex.similar;
        renderRow('[data-vd-similar-section]', '[data-vd-similar]', more);

        data.cast_full = ex.cast_full || null;
        renderCastAll(data);
        renderFacts(ex.facts, ex.keywords);
        renderVideos(ex.videos);
        renderGallery(ex.gallery);
        renderReview(ex.review);
        maybeAutoplayBillboard();
    }

    function renderReview(review) {
        var sec = q('[data-vd-review-section]'), host = q('[data-vd-review]');
        if (!sec || !host) return;
        if (!review || !review.content) { sec.hidden = true; return; }
        sec.hidden = false;
        var rating = review.rating ? '<span class="vd-review-rating">★ ' + review.rating + '/10</span>' : '';
        var date = review.created ? '<span class="vd-review-date">' + esc(review.created) + '</span>' : '';
        var long = review.content.length > 420;
        host.innerHTML = '<div class="vd-review-head">' +
            '<span class="vd-review-author">' + esc(review.author) + '</span>' + rating + date + '</div>' +
            '<p class="vd-review-body" data-vd-review-body>' + esc(review.content) + '</p>' +
            (long ? '<button class="vd-review-more" type="button" data-vd-review-more>Read more</button>' : '');
    }

    // ── facts / keywords ──────────────────────────────────────────────────────
    var LANGS = { en: 'English', es: 'Spanish', fr: 'French', de: 'German', it: 'Italian',
        ja: 'Japanese', ko: 'Korean', zh: 'Chinese', hi: 'Hindi', ru: 'Russian', pt: 'Portuguese',
        sv: 'Swedish', da: 'Danish', nl: 'Dutch', no: 'Norwegian', fi: 'Finnish', pl: 'Polish',
        tr: 'Turkish', ar: 'Arabic', he: 'Hebrew', th: 'Thai', cs: 'Czech' };
    function langName(c) { return LANGS[c] || String(c || '').toUpperCase(); }
    function fmtMoney(n) {
        if (n >= 1e9) return (n / 1e9).toFixed(1).replace(/\.0$/, '') + 'B';
        if (n >= 1e6) return (n / 1e6).toFixed(1).replace(/\.0$/, '') + 'M';
        if (n >= 1e3) return Math.round(n / 1e3) + 'K';
        return String(n);
    }
    function renderFacts(facts, keywords) {
        var sec = q('[data-vd-facts-section]'), host = q('[data-vd-facts]'), kwh = q('[data-vd-keywords]');
        facts = facts || {}; keywords = keywords || [];
        var rows = [];
        if (facts.budget) rows.push(['Budget', '$' + fmtMoney(facts.budget)]);
        if (facts.revenue) rows.push(['Box office', '$' + fmtMoney(facts.revenue)]);
        if (facts.original_language) rows.push(['Language', langName(facts.original_language)]);
        if (facts.countries && facts.countries.length) rows.push(['Country', facts.countries.join(', ')]);
        if (host) {
            host.innerHTML = rows.length ? '<div class="vd-detail-grid">' + rows.map(function (r) {
                return '<div class="vd-detail-row"><span class="vd-detail-k">' + esc(r[0]) +
                    '</span><span class="vd-detail-v">' + esc(r[1]) + '</span></div>';
            }).join('') + '</div>' : '';
        }
        if (kwh) {
            kwh.innerHTML = keywords.map(function (k) { return '<span class="vd-kw">' + esc(k) + '</span>'; }).join('');
        }
        if (sec) sec.hidden = !(rows.length || keywords.length);
    }

    // ── videos (all trailers/teasers/clips) ───────────────────────────────────
    function renderVideos(videos) {
        var sec = q('[data-vd-videos-section]'), host = q('[data-vd-videos]');
        if (!sec || !host) return;
        videos = videos || [];
        if (!videos.length) { sec.hidden = true; return; }
        sec.hidden = false;
        host.innerHTML = videos.map(function (v) {
            var thumb = 'https://img.youtube.com/vi/' + encodeURIComponent(v.key) + '/mqdefault.jpg';
            return '<button class="vd-video-card" type="button" data-vd-video="' + esc(v.key) + '">' +
                '<span class="vd-video-thumb"><img src="' + thumb + '" alt="" loading="lazy">' +
                '<span class="vd-video-play">▶</span></span>' +
                '<span class="vd-video-name">' + esc(v.name || v.type) + '</span>' +
                '<span class="vd-video-type">' + esc(v.type) + '</span></button>';
        }).join('');
    }

    // ── photos gallery + lightbox ─────────────────────────────────────────────
    var galleryImages = [], lightboxIdx = 0;
    function renderGallery(gallery) {
        var sec = q('[data-vd-gallery-section]'), host = q('[data-vd-gallery]');
        if (!sec || !host) return;
        var imgs = (gallery && gallery.backdrops) ? gallery.backdrops : [];
        galleryImages = imgs.map(function (g) { return g.full; });
        if (!imgs.length) { sec.hidden = true; return; }
        sec.hidden = false;
        host.innerHTML = imgs.map(function (g, i) {
            return '<button class="vd-shot" type="button" data-vd-shot="' + i + '">' +
                '<img src="' + esc(g.thumb) + '" alt="" loading="lazy"></button>';
        }).join('');
    }
    function openLightbox(idx) {
        if (!galleryImages.length) return;
        lightboxIdx = idx;
        var ov = document.getElementById('vd-lightbox');
        if (!ov) {
            ov = document.createElement('div'); ov.id = 'vd-lightbox'; ov.className = 'vd-lightbox';
            ov.addEventListener('click', function (e) {
                if (e.target.closest('[data-vd-lb-prev]')) lightboxStep(-1);
                else if (e.target.closest('[data-vd-lb-next]')) lightboxStep(1);
                else if (e.target === ov || e.target.closest('[data-vd-lb-close]')) closeLightbox();
            });
            document.body.appendChild(ov);
        }
        renderLightbox();
        ov.classList.add('vd-lightbox--open');
    }
    function renderLightbox() {
        var ov = document.getElementById('vd-lightbox'); if (!ov) return;
        ov.innerHTML = '<button class="vd-lb-close" type="button" data-vd-lb-close aria-label="Close">&times;</button>' +
            '<button class="vd-lb-nav vd-lb-prev" type="button" data-vd-lb-prev aria-label="Previous">&lsaquo;</button>' +
            '<img class="vd-lb-img" src="' + esc(galleryImages[lightboxIdx]) + '" alt="">' +
            '<button class="vd-lb-nav vd-lb-next" type="button" data-vd-lb-next aria-label="Next">&rsaquo;</button>' +
            '<div class="vd-lb-count">' + (lightboxIdx + 1) + ' / ' + galleryImages.length + '</div>';
    }
    function lightboxStep(dir) {
        if (!galleryImages.length) return;
        lightboxIdx = (lightboxIdx + dir + galleryImages.length) % galleryImages.length;
        renderLightbox();
    }
    function closeLightbox() {
        var ov = document.getElementById('vd-lightbox');
        if (ov) { ov.classList.remove('vd-lightbox--open'); ov.innerHTML = ''; }
    }
    function lightboxOpen() {
        var ov = document.getElementById('vd-lightbox');
        return ov && ov.classList.contains('vd-lightbox--open');
    }

    // ── full cast modal ───────────────────────────────────────────────────────
    function renderCastAll(d) {
        var btn = q('[data-vd-cast-all]');
        if (!btn) return;
        var n = (d.cast_full || []).length;
        btn.hidden = n === 0;
        if (n) btn.textContent = 'View all ' + n;
    }
    function castModalCard(p) {
        var img = p.photo
            ? '<img class="vd-cm-photo" src="' + esc(p.photo) + '" alt="" loading="lazy" onerror="this.style.visibility=\'hidden\'">'
            : '<span class="vd-cm-photo vd-cm-photo--ph">' + esc((p.name || '?').charAt(0)) + '</span>';
        var eps = p.episode_count ? '<span class="vd-cm-eps">' + p.episode_count + ' eps</span>' : '';
        var inner = img + '<span class="vd-cm-name">' + esc(p.name) + '</span>' +
            (p.character ? '<span class="vd-cm-char">' + esc(p.character) + '</span>' : '') + eps;
        return p.tmdb_id
            ? '<a class="vd-cm-card" href="/video-detail/tmdb/person/' + p.tmdb_id + '" data-vd-person="' + p.tmdb_id + '">' + inner + '</a>'
            : '<div class="vd-cm-card">' + inner + '</div>';
    }
    function openCastModal() {
        var cast = (data && data.cast_full) || [];
        if (!cast.length) return;
        var ov = document.getElementById('vd-cast-modal');
        if (!ov) {
            ov = document.createElement('div'); ov.id = 'vd-cast-modal'; ov.className = 'vd-cast-modal';
            ov.addEventListener('click', function (e) {
                var card = e.target.closest('[data-vd-person]');
                if (card) {
                    if (modified(e)) return;
                    e.preventDefault();
                    var pid = parseInt(card.getAttribute('data-vd-person'), 10);
                    closeCastModal();
                    if (!isNaN(pid)) document.dispatchEvent(new CustomEvent('soulsync:video-open-detail',
                        { detail: { kind: 'person', id: pid, source: 'tmdb' } }));
                    return;
                }
                if (e.target === ov || e.target.closest('[data-vd-cm-close]')) closeCastModal();
            });
            document.body.appendChild(ov);
        }
        ov.innerHTML = '<div class="vd-cm-box"><div class="vd-cm-head"><h3>Cast</h3>' +
            '<button class="vd-cm-close" type="button" data-vd-cm-close aria-label="Close">&times;</button></div>' +
            '<div class="vd-cm-grid">' + cast.map(castModalCard).join('') + '</div></div>';
        ov.classList.add('vd-cast-modal--open');
    }
    function closeCastModal() {
        var ov = document.getElementById('vd-cast-modal');
        if (ov) { ov.classList.remove('vd-cast-modal--open'); ov.innerHTML = ''; }
    }

    // ── trailer modal (YouTube embed) ─────────────────────────────────────────
    function openTrailer(key) {
        if (!key) return;
        stopBillboardTrailer();             // don't double up audio with the billboard
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

    // ── billboard autoplay trailer (opt-in setting) ───────────────────────────
    var prefs = null, bbTrailerTimer = null, bbMuted = true;
    function loadPrefs(cb) {
        if (prefs) { cb(prefs); return; }
        fetch('/api/video/prefs', { headers: { 'Accept': 'application/json' } })
            .then(function (r) { return r.ok ? r.json() : null; })
            .then(function (d) { prefs = d || {}; cb(prefs); })
            .catch(function () { prefs = {}; cb(prefs); });
    }
    function maybeAutoplayBillboard() {
        stopBillboardTrailer();
        if (!data || !data.trailer || !data.trailer.key) return;
        var key = data.trailer.key, id = currentId, kind = currentKind;
        loadPrefs(function (p) {
            if (!p || !p.billboard_autoplay || currentId !== id || currentKind !== kind) return;
            bbTrailerTimer = setTimeout(function () {
                if (currentId === id && currentKind === kind) startBillboardTrailer(key);
            }, 2600);
        });
    }
    function startBillboardTrailer(key) {
        var bb = q('.vd-billboard'); if (!bb || bb.querySelector('[data-vd-bb-trailer]')) return;
        bbMuted = true;
        var wrap = document.createElement('div');
        wrap.className = 'vd-bb-trailer'; wrap.setAttribute('data-vd-bb-trailer', '');
        wrap.innerHTML = '<iframe allow="autoplay; encrypted-media" frameborder="0" ' +
            'src="https://www.youtube.com/embed/' + encodeURIComponent(key) +
            '?autoplay=1&mute=1&controls=0&modestbranding=1&rel=0&playsinline=1&enablejsapi=1"></iframe>' +
            '<div class="vd-bb-tctrls"><button class="vd-bb-tbtn" type="button" data-vd-bb-mute aria-label="Unmute">🔇</button>' +
            '<button class="vd-bb-tbtn" type="button" data-vd-bb-stop aria-label="Stop">✕</button></div>';
        bb.appendChild(wrap);
        bb.classList.add('vd-billboard--trailer');
    }
    function stopBillboardTrailer() {
        clearTimeout(bbTrailerTimer); bbTrailerTimer = null;
        var ws = document.querySelectorAll('[data-vd-bb-trailer]');
        for (var i = 0; i < ws.length; i++) ws[i].remove();
        var bbs = document.querySelectorAll('.vd-billboard--trailer');
        for (var j = 0; j < bbs.length; j++) bbs[j].classList.remove('vd-billboard--trailer');
    }
    function toggleBillboardMute(btn) {
        bbMuted = !bbMuted;
        var iframe = document.querySelector('[data-vd-bb-trailer] iframe');
        if (iframe && iframe.contentWindow) {
            iframe.contentWindow.postMessage(JSON.stringify(
                { event: 'command', func: bbMuted ? 'mute' : 'unMute', args: [] }), '*');
        }
        btn.textContent = bbMuted ? '🔇' : '🔊';
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
        var stillSrc = (data && data.source === 'tmdb')
            ? (ep.still_url || '')
            : (ep.has_still ? '/api/video/poster/episode/' + ep.id : '');
        var still = stillSrc
            ? '<img class="vd-ep-still" src="' + stillSrc + '" alt="" loading="lazy" onerror="this.style.display=\'none\'">'
            : '';
        if (ep.rating) meta.push('★ ' + (Math.round(ep.rating * 10) / 10));
        var key = selectedSeason + '_' + ep.episode_number;
        // Row + a sibling expand panel (guest stars etc. load lazily on open).
        return '<div class="vd-ep ' + owned + '" data-vd-ep-key="' + key + '">' +
            '<div class="vd-ep-index">' + (ep.episode_number != null ? ep.episode_number : '') + '</div>' +
            '<div class="vd-ep-thumb">' + still + '<span class="vd-ep-thumb-ic">▶</span></div>' +
            '<div class="vd-ep-info"><div class="vd-ep-top"><span class="vd-ep-title">' +
            esc(ep.title || 'Episode ' + ep.episode_number) + '</span>' +
            (meta.length ? '<span class="vd-ep-rt">' + esc(meta.join(' · ')) + '</span>' : '') + '</div>' +
            (ep.overview ? '<p class="vd-ep-desc">' + esc(ep.overview) + '</p>' : '') + '</div>' +
            '<div class="vd-ep-badge">' + (ep.owned ? 'Owned' : 'Missing') + '</div>' +
            '<span class="vd-ep-chev" aria-hidden="true">⌄</span></div>' +
            '<div class="vd-ep-extra" data-vd-ep-panel="' + key + '" hidden></div>';
    }

    function toggleEpisode(row) {
        var key = row.getAttribute('data-vd-ep-key');
        var panel = q('[data-vd-ep-panel="' + key + '"]');
        if (!panel) return;
        panel.hidden = !panel.hidden;
        row.classList.toggle('vd-ep--open', !panel.hidden);
        if (!panel.hidden && !panel.getAttribute('data-loaded')) {
            panel.setAttribute('data-loaded', '1');
            loadEpisodeExtra(key, panel);
        }
    }
    function loadEpisodeExtra(key, panel) {
        var tmdb = data && data.tmdb_id;
        var parts = key.split('_');
        if (!tmdb) { panel.innerHTML = '<div class="vd-ep-extra-empty">No extra info.</div>'; return; }
        panel.innerHTML = '<div class="vd-ep-extra-empty">Loading…</div>';
        fetch('/api/video/episode/' + tmdb + '/' + parts[0] + '/' + parts[1],
            { headers: { 'Accept': 'application/json' } })
            .then(function (r) { return r.ok ? r.json() : null; })
            .then(function (ex) { renderEpisodeExtra(panel, ex && !ex.error ? ex : {}); })
            .catch(function () { panel.innerHTML = ''; });
    }
    function renderEpisodeExtra(panel, ex) {
        var html = '';
        if (ex.still_url) {
            html += '<img class="vd-ep-extra-still" src="' + esc(ex.still_url) + '" alt="" loading="lazy">';
        }
        html += '<div class="vd-ep-extra-body">';
        if (ex.overview) html += '<p class="vd-ep-extra-ov">' + esc(ex.overview) + '</p>';
        if (ex.guest_stars && ex.guest_stars.length) {
            html += '<div class="vd-ep-extra-gh">Guest stars</div><div class="vd-ep-guests">' +
                ex.guest_stars.map(function (g) {
                    var img = g.photo
                        ? '<img class="vd-guest-photo" src="' + esc(g.photo) + '" alt="" loading="lazy" onerror="this.style.visibility=\'hidden\'">'
                        : '<span class="vd-guest-photo vd-guest-photo--ph">' + esc((g.name || '?').charAt(0)) + '</span>';
                    var inner = img + '<span class="vd-guest-name">' + esc(g.name) + '</span>' +
                        (g.character ? '<span class="vd-guest-char">' + esc(g.character) + '</span>' : '');
                    return g.tmdb_id
                        ? '<a class="vd-guest" href="/video-detail/tmdb/person/' + g.tmdb_id + '" data-vd-person="' + g.tmdb_id + '">' + inner + '</a>'
                        : '<div class="vd-guest">' + inner + '</div>';
                }).join('') + '</div>';
        }
        html += '</div>';
        panel.innerHTML = html || '<div class="vd-ep-extra-empty">No extra info.</div>';
    }

    function renderSeasonOverview() {
        var el = q('[data-vd-season-overview]');
        if (!el) return;
        var s = seasonByNum(selectedSeason);
        var ov = s && s.overview;
        el.textContent = ov || '';
        el.hidden = !ov;
    }

    function renderEpisodes() {
        renderSeasonOverview();
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
        renderSeasonNav(); ensureSeasonEpisodes();
    }

    // tmdb (preview) shows carry season counts but load episodes lazily per season.
    function ensureSeasonEpisodes() {
        var season = seasonByNum(selectedSeason);
        if (data && data.source === 'tmdb' && season && !season._loaded &&
            !(season.episodes && season.episodes.length)) {
            loadTmdbSeason(season);
        } else {
            renderEpisodes();
        }
    }
    function loadTmdbSeason(season) {
        var host = q('[data-vd-episodes]');
        if (host) host.innerHTML = '<div class="vd-ep-empty">Loading episodes…</div>';
        var sid = data.id, sn = season.season_number;
        fetch(TMDB_URL + 'show/' + sid + '/season/' + sn, { headers: { 'Accept': 'application/json' } })
            .then(function (r) { return r.ok ? r.json() : null; })
            .then(function (se) {
                season._loaded = true;
                if (se && se.episodes) {
                    season.episodes = se.episodes;
                    season.episode_total = se.episodes.length;
                    if (se.overview) season.overview = se.overview;
                }
                if (currentId === sid && selectedSeason === sn) { renderSeasonNav(); renderEpisodes(); }
            })
            .catch(function () {
                season._loaded = true;
                if (currentId === sid && selectedSeason === sn) renderEpisodes();
            });
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
    function loadMovie(id, source) {
        currentKind = 'movie'; currentSource = source || 'library';
        if (!root()) return;
        if (currentId !== id) artAttemptedFor = null;
        currentId = id;
        showLoading(true);
        resetExtras();
        var dh = q('[data-vd-details]'); if (dh) dh.innerHTML = '';
        var r0 = root(); if (r0) r0.style.removeProperty('--vd-accent-rgb');
        fetch(detailURL('movie', id, currentSource), { headers: { 'Accept': 'application/json' } })
            .then(function (r) { return r.ok ? r.json() : null; })
            .then(function (d) {
                showLoading(false);
                if (d && d.redirect) { reopen(d.redirect); return; }
                if (!d || d.error) { setText('[data-vd-title]', 'Not found'); return; }
                if (currentId !== id || currentKind !== 'movie') return;
                data = d;
                renderBillboard(d);
                renderDetails(d);
                var sub = document.querySelector('.video-subpage[data-video-subpage="video-movie-detail"]');
                if (sub) sub.scrollTop = 0;
                if (currentSource === 'tmdb') {
                    renderExtras('movie', id, d);     // extras ship inside the tmdb payload
                } else {
                    maybeRefreshMovie(id);
                    loadExtras('movie', id);
                }
            })
            .catch(function () { showLoading(false); setText('[data-vd-title]', 'Could not load movie'); });
    }

    // An owned title reached via a tmdb URL → bounce to the real library detail.
    // _replace so it REPLACES the tmdb history entry (which would redirect again on
    // Back) instead of pushing a new layer — otherwise Back loops on the redirect.
    function reopen(rd) {
        document.dispatchEvent(new CustomEvent('soulsync:video-open-detail',
            { detail: { kind: rd.kind, id: rd.id, source: rd.source || 'library', _replace: true } }));
    }

    // Lazy: backfill a movie's cast/genres/art from TMDB on view if missing.
    function maybeRefreshMovie(id) {
        if (artAttemptedFor === id || !data || data.id !== id) return;
        var needs = !(data.cast && data.cast.length) || !(data.genres && data.genres.length)
            || !data.has_backdrop || !data.logo || (data.imdb_id && !data.imdb_rating);
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
                            if (d && !d.error && currentId === id) {
                                // Keep the live extras (server/trailer/next-ep) the
                                // detail payload lacks — else Play/Trailer vanish.
                                var prev = data || {};
                                d.server = prev.server || null;
                                d.trailer = prev.trailer || null;
                                d.next_episode = prev.next_episode || null;
                                data = d; renderBillboard(d); renderDetails(d);
                            }
                        });
                }
            }).catch(function () { /* best-effort */ });
    }

    function loadShow(id, source) {
        currentKind = 'show'; currentSource = source || 'library';
        if (!root()) return;
        if (currentId !== id) artAttemptedFor = null;
        currentId = id;
        showLoading(true);
        resetExtras();
        showEpSyncing(false);
        ['[data-vd-episodes]', '[data-vd-season-nav]'].forEach(function (s) { var n = q(s); if (n) n.innerHTML = ''; });
        var r0 = root(); if (r0) r0.style.removeProperty('--vd-accent-rgb');
        fetch(detailURL('show', id, currentSource), { headers: { 'Accept': 'application/json' } })
            .then(function (r) { return r.ok ? r.json() : null; })
            .then(function (d) {
                showLoading(false);
                if (d && d.redirect) { reopen(d.redirect); return; }
                if (!d || d.error) { setText('[data-vd-title]', 'Not found'); return; }
                if (currentId !== id || currentKind !== 'show') return;
                data = d; menuOpen = false; missingOnly = false;
                selectedSeason = d.seasons && d.seasons.length ? d.seasons[0].season_number : null;
                var mt = q('[data-vd-missing-toggle]');
                if (mt) { mt.hidden = !(d.seasons && d.seasons.length); mt.classList.remove('vd-missing-toggle--on'); }
                renderBillboard(d);
                renderViewToggle(); renderSeasonNav(); ensureSeasonEpisodes();
                var sub = document.querySelector('.video-subpage[data-video-subpage="video-show-detail"]');
                if (sub) sub.scrollTop = 0;
                if (currentSource === 'tmdb') {
                    renderExtras('show', id, d);
                } else {
                    maybeRefreshArt(id);
                    loadExtras('show', id);
                }
            })
            .catch(function () { showLoading(false); setText('[data-vd-title]', 'Could not load show'); });
    }

    // Lazy art: if any season lacks a poster, pull it from TMDB on view and cache
    // it (once per show), then re-render. Sidesteps "already matched, never re-runs".
    function maybeRefreshArt(id) {
        if (artAttemptedFor === id || !data || data.id !== id) return;
        // Trigger if the full episode list hasn't been pulled yet (so missing
        // episodes show up), or any art is still missing.
        var needs = !data.episodes_synced || !data.logo
            || (data.seasons || []).some(function (s) { return !s.has_poster; })
            || (data.imdb_id && !data.imdb_rating);
        if (!needs) return;
        artAttemptedFor = id;
        // The full episode list (owned + missing) is being pulled from TMDB — this
        // can take a while, so show the user it's happening instead of a silent gap.
        if (!data.episodes_synced) showEpSyncing(true);
        fetch(DETAIL_URL + 'show/' + id + '/refresh-art',
            { method: 'POST', headers: { 'Accept': 'application/json' } })
            .then(function (r) { return r.ok ? r.json() : null; })
            .then(function (res) {
                if (res && res.ok && currentId === id) reloadDetail(id);
                else showEpSyncing(false);
            })
            .catch(function () { showEpSyncing(false); });
    }

    function showEpSyncing(on) {
        var el = q('[data-vd-ep-syncing]');
        if (el) el.hidden = !on;
    }

    function reloadDetail(id) {
        fetch(DETAIL_URL + 'show/' + id, { headers: { 'Accept': 'application/json' } })
            .then(function (r) { return r.ok ? r.json() : null; })
            .then(function (d) {
                showEpSyncing(false);
                if (!d || d.error || currentId !== id) return;
                // Carry over the live extras (server / trailer / next-episode) the
                // show_detail payload doesn't include, so the Play & Trailer buttons
                // (and the next-ep banner) don't vanish on reload.
                var prev = data || {};
                d.server = prev.server || null;
                d.trailer = prev.trailer || null;
                d.next_episode = prev.next_episode || null;
                data = d;
                if (!seasonByNum(selectedSeason)) {
                    selectedSeason = d.seasons && d.seasons.length ? d.seasons[0].season_number : null;
                }
                renderBillboard(d); renderSeasonNav(); renderEpisodes();
            })
            .catch(function () { showEpSyncing(false); });
    }

    // ── events ────────────────────────────────────────────────────────────────
    function onOpen(e) {
        if (!e || !e.detail) return;
        var src = e.detail.source || 'library';
        if (e.detail.kind === 'movie') loadMovie(e.detail.id, src);
        else if (e.detail.kind === 'show') loadShow(e.detail.id, src);
        // 'person' is handled by video-person.js (same event).
    }

    function modified(e) {
        return e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey;
    }

    function onClick(e) {
        var muteBtn = e.target.closest('[data-vd-bb-mute]');
        if (muteBtn) { toggleBillboardMute(muteBtn); return; }
        var stopBtn = e.target.closest('[data-vd-bb-stop]');
        if (stopBtn) { stopBillboardTrailer(); return; }
        var r = root(); if (!r) return;
        // In-app drill-ins (real <a> links → modified clicks open new tabs).
        var sim = e.target.closest('[data-vd-sim]');
        if (sim && r.contains(sim)) {
            if (modified(e)) return;
            e.preventDefault();
            var sid = parseInt(sim.getAttribute('data-vd-sim-id'), 10);
            if (!isNaN(sid)) document.dispatchEvent(new CustomEvent('soulsync:video-open-detail',
                { detail: { kind: sim.getAttribute('data-vd-sim'), id: sid, source: 'tmdb' } }));
            return;
        }
        var person = e.target.closest('[data-vd-person]');
        if (person && r.contains(person)) {
            if (modified(e)) return;
            e.preventDefault();
            var pid = parseInt(person.getAttribute('data-vd-person'), 10);
            if (!isNaN(pid)) document.dispatchEvent(new CustomEvent('soulsync:video-open-detail',
                { detail: { kind: 'person', id: pid, source: 'tmdb' } }));
            return;
        }
        var shot = e.target.closest('[data-vd-shot]');
        if (shot && r.contains(shot)) { openLightbox(parseInt(shot.getAttribute('data-vd-shot'), 10) || 0); return; }
        var vid = e.target.closest('[data-vd-video]');
        if (vid && r.contains(vid)) { openTrailer(vid.getAttribute('data-vd-video')); return; }
        var castAll = e.target.closest('[data-vd-cast-all]');
        if (castAll && r.contains(castAll)) { openCastModal(); return; }
        var revMore = e.target.closest('[data-vd-review-more]');
        if (revMore && r.contains(revMore)) {
            var body = q('[data-vd-review-body]');
            if (body) { var open = body.classList.toggle('vd-review-body--open'); revMore.textContent = open ? 'Read less' : 'Read more'; }
            return;
        }
        var epRow = e.target.closest('[data-vd-ep-key]');
        if (epRow && r.contains(epRow)) { toggleEpisode(epRow); return; }
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
        // Kill the billboard trailer (audio!) when navigating to a non-detail page.
        document.addEventListener('soulsync:video-page-shown', function (e) {
            if (e && e.detail !== 'video-movie-detail' && e.detail !== 'video-show-detail') stopBillboardTrailer();
        });
        document.addEventListener('keydown', function (e) {
            if (e.key === 'Escape') { closeTrailer(); closeLightbox(); closeCastModal(); }
            else if (lightboxOpen()) {
                if (e.key === 'ArrowLeft') lightboxStep(-1);
                else if (e.key === 'ArrowRight') lightboxStep(1);
            }
        });
    }

    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init);
    else init();
})();
