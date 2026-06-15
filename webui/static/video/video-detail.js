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
        if (d.source === 'tmdb') return d.poster_url || '';
        return d.has_poster ? '/api/video/poster/' + d.kind + '/' + d.id : '';
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
        ['[data-vd-providers-section]', '[data-vd-similar-section]', '[data-vd-collection-section]',
         '[data-vd-next-ep]', '[data-vd-crew-line]', '[data-vd-season-overview]'].forEach(function (s) {
            var n = q(s); if (n) n.hidden = true;
        });
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
            // Streaming providers (JustWatch via TMDB) link to the where-to-watch
            // page. (TMDB only gives one aggregate link, so they share it.) Drop a
            // provider that's the same service as your server tile (e.g. Plex), so
            // it isn't listed twice.
            var link = ex.providers_link || '';
            var srvName = (ex.server && ex.server.server || '').toLowerCase();
            var provs = (ex.providers || []).filter(function (p) {
                return (p.name || '').toLowerCase() !== srvName;
            });
            if (provs.length) {
                html += provs.map(function (p) {
                    var img = p.logo ? '<img src="' + esc(p.logo) + '" alt="' + esc(p.name) + '" loading="lazy">'
                        : '<span class="vd-prov-ph">' + esc((p.name || '?').charAt(0)) + '</span>';
                    var inner = img + '<span class="vd-prov-name">' + esc(p.name) + '</span>';
                    return link
                        ? '<a class="vd-prov" href="' + esc(link) + '" target="_blank" rel="noopener" title="Where to watch — ' + esc(p.name) + '">' + inner + '</a>'
                        : '<div class="vd-prov" title="' + esc(p.name) + '">' + inner + '</div>';
                }).join('');
            }
            ps.hidden = !html;
            ph.innerHTML = html;
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
        var stillSrc = (data && data.source === 'tmdb')
            ? (ep.still_url || '')
            : (ep.has_still ? '/api/video/poster/episode/' + ep.id : '');
        var still = stillSrc
            ? '<img class="vd-ep-still" src="' + stillSrc + '" alt="" loading="lazy" onerror="this.style.display=\'none\'">'
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
    function reopen(rd) {
        document.dispatchEvent(new CustomEvent('soulsync:video-open-detail',
            { detail: { kind: rd.kind, id: rd.id, source: rd.source || 'library' } }));
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
                            if (d && !d.error && currentId === id) { data = d; renderBillboard(d); renderDetails(d); }
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
