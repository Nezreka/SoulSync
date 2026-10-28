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
    var TMDB_LOGO = '/static/img/brands/tmdb.svg';
    var TVDB_LOGO = '/static/img/brands/tvdb.svg';
    // Real media-server logos for the "Play on your server" watch tile (same
    // sources as the header server toggle).
    var SERVER_LOGOS = {
        Plex: '/static/img/brands/plex.png',
        Jellyfin: '/static/img/brands/jellyfin.png',
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
    function toast(msg, type) { if (typeof showToast === 'function') showToast(msg, type); }
    // Set a discog-style button's label without clobbering its icon/shimmer spans.
    function _btnLabel(btn, text) { var t = btn.querySelector('.discog-btn-text'); if (t) t.textContent = text; else btn.textContent = text; }
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
    // The season a show opens on: the LATEST real season (highest number), not specials/S1.
    // Falls back to specials only when that's all there is.
    // Specials are extras: they go after the real seasons in every season nav
    // (the library tab already lists them last), not first at "0%".
    function specialsLast(seasons) {
        if (!seasons || !seasons.length) return seasons;
        return seasons.filter(function (s) { return s.season_number !== 0; })
            .concat(seasons.filter(function (s) { return s.season_number === 0; }));
    }
    function defaultSeasonNum(seasons) {
        if (!seasons || !seasons.length) return null;
        var real = seasons.filter(function (s) { return (s.season_number || 0) > 0; });
        var pool = real.length ? real : seasons;
        return pool.reduce(function (best, s) {
            return (s.season_number > best.season_number) ? s : best;
        }, pool[0]).season_number;
    }
    // Continue Watching: land on the season you're actually IN (Netflix behavior)
    // when the show has a next-up episode; otherwise the usual latest season.
    function initialSeasonNum(d) {
        var nu = d && d.next_up;
        if (nu && (d.seasons || []).some(function (s) { return s.season_number === nu.season_number; })) {
            return nu.season_number;
        }
        return defaultSeasonNum(d && d.seasons);
    }
    // Thumbnail width, not the original. An unsized /api/video/poster/... asks
    // Plex for the full-size art - a ~2000x3000 poster decoded to ~24MB of
    // bitmap and then scaled into a 150px card. A twenty-season rail did that
    // twenty times over. Same helper the wishlist and watchlist grids use.
    function sizedArt(url, w) {
        if (!url) return url;
        if (url.indexOf('/api/video/poster/') !== -1 || url.indexOf('/api/video/backdrop/') !== -1) {
            return url + (url.indexOf('?') === -1 ? '?' : '&') + 'w=' + w;
        }
        if (url.indexOf('image.tmdb.org') !== -1) {
            var b = w <= 185 ? 185 : (w <= 342 ? 342 : (w <= 500 ? 500 : 780));
            return url.replace(/\/t\/p\/[^/]+\//, '/t/p/w' + b + '/');
        }
        return url;
    }

    function seasonArt(s) {
        // tmdb + youtube carry direct (already-proxied for yt) art urls on the payload.
        if (data && (data.source === 'tmdb' || data.source === 'youtube')) return s.poster_url || data.poster_url || '';
        return (s.has_poster && s.id != null) ? '/api/video/poster/season/' + s.id
            : (data && data.has_poster ? '/api/video/poster/show/' + data.id : '');
    }
    // Source-aware billboard art: library items proxy through /api/video; tmdb
    // (preview) + youtube items use the (proxied) image URLs in the payload.
    function bbBackdrop(d) {
        if (d.source === 'tmdb' || d.source === 'youtube') return d.backdrop_url || d.poster_url || '';
        var art = '/' + d.kind + '/' + d.id;
        return d.has_backdrop ? '/api/video/backdrop' + art : (d.has_poster ? '/api/video/poster' + art : '');
    }
    function bbPoster(d) {
        // The offscreen poster is canvas-sampled for the accent — must be
        // same-origin, so tmdb posters proxy; youtube urls are already proxied.
        if (d.source === 'tmdb') return d.poster_url ? proxied(d.poster_url) : '';
        if (d.source === 'youtube') return d.poster_url || '';
        return d.has_poster ? '/api/video/poster/' + d.kind + '/' + d.id : '';
    }
    function proxied(url) {
        return /^https:\/\/image\.tmdb\.org\//.test(url || '')
            ? '/api/video/img?u=' + encodeURIComponent(url) : (url || '');
    }
    function pct(s) { return s.episode_total ? Math.round(s.episode_owned / s.episode_total * 100) : 0; }
    // Interactive metadata chips (#1042): a genre opens Discover pre-filtered to
    // that genre + this title's kind; a keyword opens a video search for it.
    // tmdb preview items stay in-app all the same. Non-clickable for a bare
    // fallback (no data hook) so nothing breaks if the router is absent.
    function genreChip(name) {
        return '<button type="button" class="vd-genre" data-vd-genre="' + esc(name) + '">' + esc(name) + '</button>';
    }
    function kwChip(name) {
        return '<button type="button" class="vd-kw" data-vd-kw="' + esc(name) + '">' + esc(name) + '</button>';
    }

    // Per-provider "where to watch" links (#1042). TMDB only hands us ONE aggregate
    // /watch link per title, so every provider icon would otherwise share it. We map
    // the known services to a search on their OWN site for the title instead — not an
    // exact deep link (that needs a per-provider content id we don't have), but each
    // icon lands you on the right service. Unknown providers return '' and the caller
    // falls back to the TMDB aggregate page. Substring match so "Amazon Prime Video",
    // "Amazon Video", "Apple TV", "Apple TV Store", etc. all resolve.
    function providerSearchUrl(name, title) {
        var t = String(title || '').trim();
        if (!t) return '';
        var q = encodeURIComponent(t);
        var n = String(name || '').toLowerCase();
        if (n.indexOf('amazon') >= 0)      return 'https://www.amazon.com/s?k=' + q + '&i=instant-video';
        if (n.indexOf('apple') >= 0)       return 'https://tv.apple.com/search?term=' + q;
        if (n.indexOf('google play') >= 0) return 'https://play.google.com/store/search?q=' + q + '&c=movies';
        if (n.indexOf('youtube') >= 0)     return 'https://www.youtube.com/results?search_query=' + q;
        if (n.indexOf('netflix') >= 0)     return 'https://www.netflix.com/search?q=' + q;
        if (n.indexOf('disney') >= 0)      return 'https://www.disneyplus.com/search?q=' + q;
        if (n.indexOf('hulu') >= 0)        return 'https://www.hulu.com/search?q=' + q;
        if (n === 'max' || n.indexOf('hbo') >= 0) return 'https://play.max.com/search?q=' + q;
        if (n.indexOf('paramount') >= 0)   return 'https://www.paramountplus.com/search/?query=' + q;
        if (n.indexOf('peacock') >= 0)     return 'https://www.peacocktv.com/search?q=' + q;
        return '';
    }

    function badge(logo, fallback, title, url) {
        var inner = logo
            ? '<img src="' + logo + '" alt="' + fallback + '" onerror="this.parentNode.textContent=\'' + fallback + '\'">'
            : '<span style="font-size:9px;font-weight:700;">' + fallback + '</span>';
        return url
            ? '<a class="artist-hero-badge" title="' + title + '" href="' + url + '" target="_blank" rel="noopener noreferrer">' + inner + '</a>'
            : '<div class="artist-hero-badge" title="' + title + '">' + inner + '</div>';
    }
    function countLabel(n, one, many) {
        n = Number(n) || 0;
        return n + ' ' + (n === 1 ? one : many);
    }
    // one chip. mode is 'ok' (identity is there), 'missing' (a gap automation
    // will trip over) or 'neutral' (a count, not a verdict).
    function healthChip(label, value, mode) {
        return '<span class="vd-health-chip vd-health-chip--' + mode + '">' +
            '<span class="vd-health-k">' + esc(label) + '</span>' +
            '<span class="vd-health-v">' + esc(value || 'Missing') + '</span></span>';
    }
    // a missing id isn't news, it's a job. `fix` is the enrichment service key,
    // and the chip becomes the button that opens Manage on that service's match
    // search. that repair flow already existed in the manage panel; nothing on
    // the detail page pointed at it.
    function idChip(label, value, fix) {
        if (!value && fix) {
            return '<button class="vd-health-chip vd-health-chip--missing vd-health-chip--fix" type="button" ' +
                'data-vd-health-fix="' + esc(fix) + '" title="Find the right ' + esc(label) + ' match">' +
                '<span class="vd-health-k">' + esc(label) + '</span>' +
                '<span class="vd-health-v">Find\u2026</span></button>';
        }
        return healthChip(label, value, value ? 'ok' : 'missing');
    }
    // the band answers ONE question the hero can't: does automation have the ids
    // it needs to go find this thing. owned/wanted, coverage and format facts all
    // live in the meta line right above, so repeating them here just doubles the
    // noise the band exists to cut.
    function renderHealth(d) {
        var h = q('[data-vd-health]');
        if (!h) return;
        if (!d) { h.hidden = true; h.innerHTML = ''; return; }
        var chips = [];
        if (d.source !== 'youtube') {
            var libId = (d.source !== 'tmdb') ? d.id : d.library_id;
            var fixable = libId != null;
            if (!d.tmdb_id && fixable) chips.push(idChip('TMDB', d.tmdb_id, 'tmdb'));
            if (d.kind === 'show' && !d.tvdb_id && fixable) chips.push(idChip('TVDB', d.tvdb_id, 'tvdb'));
            if (!d.imdb_id && fixable) chips.push(idChip('IMDb', d.imdb_id, 'imdb'));
        }
        h.innerHTML = chips.join('');
        h.hidden = !chips.length;
    }
    // Text colour to put ON the sampled accent. Dark Matter's poster is pale, so
    // its accent sampled near-white and the Trailer button rendered white on
    // white — an accent lifted from artwork cannot assume white text just
    // because the page around it is dark. sRGB relative luminance, WCAG's.
    function accentFg(rgb) {
        var lin = [];
        for (var i = 0; i < 3; i++) {
            var v = rgb[i] / 255;
            lin.push(v <= 0.03928 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4));
        }
        var L = 0.2126 * lin[0] + 0.7152 * lin[1] + 0.0722 * lin[2];
        return L > 0.45 ? '#0b0b0f' : '#fff';
    }
    // The accent as TEXT on the dark page: a dark sampled accent (House of the
    // Dragon's oxblood) made "Returning Series" unreadable. Mix it toward white
    // until it clears 4.5:1 against the page's #0b0b0f.
    function accentText(rgb) {
        function lum(c) {
            var l = c.map(function (x) { var v = x / 255; return v <= 0.03928 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4); });
            return 0.2126 * l[0] + 0.7152 * l[1] + 0.0722 * l[2];
        }
        var bg = lum([11, 11, 15]), c = rgb.slice();
        for (var t = 0; t <= 10; t++) {
            c = rgb.map(function (x) { return Math.round(x + (255 - x) * t / 10); });
            if ((lum(c) + 0.05) / (bg + 0.05) >= 4.5) break;
        }
        return c.join(', ');
    }
    function resetAccent(r0) {
        ['--vd-accent-rgb', '--vd-accent-fg', '--vd-accent-text-rgb'].forEach(function (v) { r0.style.removeProperty(v); });
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
            if (best) {
                var r0 = root();
                if (r0) {
                    r0.style.setProperty('--vd-accent-rgb', best[0] + ', ' + best[1] + ', ' + best[2]);
                    r0.style.setProperty('--vd-accent-fg', accentFg(best));
                    r0.style.setProperty('--vd-accent-text-rgb', accentText(best));
                }
            }
        } catch (e) { /* tainted/no image — keep theme accent */ }
    }

    // ── billboard ─────────────────────────────────────────────────────────────
    // The hero clamps the synopsis to three lines; the rest used to be
    // unreachable. A More / Less toggle appears only when it overflows.
    function renderOverview(text) {
        var p = q('[data-vd-overview]');
        if (!p) return;
        p.textContent = text || '';
        p.classList.remove('vd-overview--open');
        var btn = p.nextElementSibling && p.nextElementSibling.matches('[data-vd-overview-more]')
            ? p.nextElementSibling : null;
        if (!btn) {
            btn = document.createElement('button');
            btn.type = 'button';
            btn.className = 'vd-overview-more';
            btn.setAttribute('data-vd-overview-more', '');
            p.insertAdjacentElement('afterend', btn);
        }
        btn.textContent = 'More';
        btn.setAttribute('aria-expanded', 'false');
        btn.hidden = true;
        requestAnimationFrame(function () { btn.hidden = !(p.scrollHeight > p.clientHeight + 2); });
    }

    function renderBillboard(d) {
        renderTabs();
        var epH = q('.vd-ep-heading');
        if (epH) epH.textContent = d.source === 'youtube' ? 'Videos' : 'Episodes';
        setText('[data-vd-title]', d.title);
        renderOverview(d.overview);

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
            // a youtube maxres thumbnail doesn't always exist; the layer under it shows then
            bg.style.backgroundImage = url
                ? "url('" + url + "')" + (d._backdrop_fallback ? ", url('" + d._backdrop_fallback + "')" : '') : '';
            bg.classList.toggle('vd-bb-bg--poster', !d.has_backdrop && !!d.has_poster);
            bg.classList.toggle('vd-bb-bg--empty', !d.has_backdrop && !d.has_poster);
            bg.classList.toggle('vd-bb-bg--ambient', !!d._ambient_backdrop);
        }
        var poster = q('[data-vd-poster]');
        var posterUrl = bbPoster(d);
        if (poster && posterUrl) {
            poster.onload = function () { applyAccent(poster); };
            poster.src = posterUrl;
        }
        var cover = q('[data-vd-cover]');
        if (cover) {
            var coverNeeded = !!posterUrl && (d.source === 'youtube' || (!d.logo && !d.has_backdrop));
            cover.hidden = !coverNeeded;
            cover.classList.toggle('vd-cover--channel', d.source === 'youtube' && d.kind === 'channel');
            // a playlist's cover is a video thumbnail: show it 16:9, not cropped to a poster
            cover.classList.toggle('vd-cover--wide', d.source === 'youtube' && d.kind === 'playlist');
            var bbContent = q('.vd-bb-content');
            if (bbContent) bbContent.classList.toggle('vd-bb-content--no-cover', !coverNeeded);
            if (bbContent) bbContent.classList.toggle('vd-bb-content--wide-cover', d.source === 'youtube' && d.kind === 'playlist');
            if (coverNeeded) {
                cover.src = sizedArt(posterUrl, d.source === 'youtube' ? 342 : 500);
                cover.alt = d.source === 'youtube' ? ((d.title || 'Channel') + ' avatar') : ((d.title || 'Title') + ' poster');
                cover.onerror = function () { cover.hidden = true; if (bbContent) bbContent.classList.add('vd-bb-content--no-cover'); };
            } else {
                cover.removeAttribute('src');
            }
        }

        var tl = q('[data-vd-tagline]');
        if (tl) { tl.textContent = d.tagline || ''; tl.hidden = !d.tagline; }

        var meta = [];
        if (d.source === 'youtube') {
            var isPl = d.kind === 'playlist';
            meta.push('<span class="vd-status vd-status--yt">' + (isPl ? 'Playlist' : 'YouTube') + '</span>');
            var yc = window.VideoYoutube;
            if (isPl) {
                // A playlist IS a known, fixed list, so its count is real (unlike a
                // channel's total). Owner shows as a genre tag below.
                if (d.video_count != null) meta.push('<span>' + esc(d.video_count) + ' videos</span>');
            } else {
                var subs = yc && yc.compactCount(d.subscriber_count); if (subs) meta.push('<span>' + subs + ' subscribers</span>');
                // NB: no "N videos" for a CHANNEL — YouTube doesn't expose a reliable total.
                var views = yc && yc.compactCount(d.view_count); if (views) meta.push('<span>' + views + ' views</span>');
                if (d.handle) meta.push('<span>' + esc(d.handle) + '</span>');
            }
            var mm = q('[data-vd-meta]'); if (mm) mm.innerHTML = meta.join('');
            renderActions(d);
            renderHealth(d);
            renderEssentials(d);
            var ll = q('[data-vd-links]'); if (ll) ll.innerHTML = '';
            // a youtube tag is not a movie genre: clicking "physics" opened Discover
            // on an empty genre. a playlist's owner opens its channel; tags just read.
            var gg = q('[data-vd-genres]');
            if (gg) gg.innerHTML = ytTagChips(d);
            renderRatings(d); renderAwards(d); renderCrewLine(d); renderNextEpisode(d); renderCast(d);
            return;
        }
        if (d.source === 'tmdb') {
            meta.push('<span class="vd-status vd-status--preview">Preview</span>');
        } else if (d.kind === 'show') {
            var sc = showCounts(d);
            var ownedPct = sc.aired ? Math.round(sc.owned / sc.aired * 100) : 0;
            meta.push('<span class="vd-match" title="' + sc.owned + ' of ' + sc.aired + ' aired episodes' +
                (sc.specials ? ', not counting ' + sc.specials + ' specials you don\u2019t have' : '') + '">' +
                (ownedPct === 100 ? 'Complete' : ownedPct + '% in library') + '</span>');
            // Continue Watching: how far through the show you are (server truth).
            if (d.watched) meta.push('<span class="vd-watched-tag" title="Every episode watched">✓ Watched</span>');
            else if (d.watched_episodes > 0) {
                // the same yardstick as the % beside it: real seasons, aired episodes
                var wn = sc.watched || d.watched_episodes;
                meta.push('<span class="vd-watched-tag" title="Episodes watched on your server">✓ ' +
                    wn + ' of ' + (sc.aired || d.episode_total) + ' watched</span>');
            }
        } else {
            meta.push(d.owned ? '<span class="vd-match">In library</span>'
                : '<span class="vd-status">Wanted</span>');
            if (d.watched) {
                var seen = d.last_viewed_at ? fmtDate(String(d.last_viewed_at).slice(0, 10)) : '';
                meta.push('<span class="vd-watched-tag" title="Watched on your server' + (seen ? ', last on ' + seen : '') + '">✓ Watched' +
                    (seen ? ' · ' + esc(seen) : '') + '</span>');
            }
            // your best copy's format facts (4K · HDR · Atmos · 5.1), like the streamers show
            if (d.owned && d.file) meta.push.apply(meta, formatBadges(d.file));
        }
        // The ratings ROW below carries IMDb / RT / Metacritic / Trakt / TVmaze.
        // Printing the TMDB score up here too put four numbers for one question
        // two lines apart, disagreeing with each other. Only show it when the row
        // below has nothing to say.
        if (d.rating && !hasRatingRow(d)) {
            meta.push('<span class="vd-score">★ ' + (Math.round(d.rating * 10) / 10) + '</span>');
        }
        if (d.year) meta.push('<span>' + esc(d.year) + '</span>');
        if (d.content_rating) meta.push('<span class="vd-meta-rating">' + esc(d.content_rating) + '</span>');
        if (d.kind === 'show') {
            // specials are extras, not a season: "6 Seasons" on a five-season show misled
            var sc2 = showCounts(d);
            var nSeasons = sc2.seasons || d.season_count;
            meta.push('<span>' + nSeasons + ' Season' + (nSeasons === 1 ? '' : 's') + '</span>');
            meta.push('<span>' + (sc2.episodes || d.episode_total) + ' Episodes</span>');
        }
        var rt = runtimeLabel(d.runtime_minutes);
        if (rt) meta.push('<span>' + esc(rt) + '</span>');
        if (d.kind === 'show' && d.status) meta.push('<span class="vd-status">' + esc(statusLabel(d.status)) + '</span>');
        if (d.network) meta.push('<span>' + esc(d.network) + '</span>');
        if (d.kind === 'movie' && d.studio) meta.push('<span>' + esc(d.studio) + '</span>');
        // Mediastinger (already enriched for overlays): don't leave before the credits end.
        if (d.mediastinger) {
            meta.push('<span class="vd-stinger" title="This title has an after-credits scene">' +
                '🎬 After-credits scene</span>');
        }
        var m = q('[data-vd-meta]'); if (m) m.innerHTML = meta.join('');

        renderActions(d);
        renderHealth(d);
        renderEssentials(d);
        renderTypeChip(d);

        // The IMDb / TMDB / TVDB / Letterboxd tiles moved into More → "Open on":
        // a row of four 30px logos was a whole hero line for links nobody needs
        // twice a visit.
        var l = q('[data-vd-links]');
        if (l) { l.innerHTML = ''; l.hidden = true; }
        var g = q('[data-vd-genres]');
        if (g) {
            g.innerHTML = (d.genres || []).slice(0, 6).map(function (gn) {
                return genreChip(gn);
            }).join('');
        }
        // Subtitle AVAILABILITY was a wall of up to 13 chips in the hero; it's one
        // line under Details now (renderFacts), next to the other facts.
        renderSubtitles(null);
        // Subtitle language overrides (Phase 3; video-subtitles.js): per-title
        // config section. Gates itself to library movie/show pages.
        if (window.VideoSubtitles) window.VideoSubtitles.mountOverrides(d.kind, d.id, currentSource);
        renderRatings(d);
        renderAwards(d);
        renderCrewLine(d);
        renderNextEpisode(d);
        renderCast(d);
    }

    // A show's numbers as a viewer reads them: real seasons only and aired
    // episodes only (a special, owned or not, never moves the %). Previews and
    // YouTube fall back to the payload's own totals.
    function showCounts(d) {
        var out = { seasons: 0, episodes: 0, aired: 0, owned: 0, specials: 0, watched: 0 };
        var today = new Date().toISOString().slice(0, 10);
        (d && d.seasons || []).forEach(function (s) {
            var special = s.season_number === 0;
            if (!special) out.seasons++;
            (s.episodes || []).forEach(function (ep) {
                if (!special && ep.watched) out.watched++;
                if (ep.owned) { if (!special) { out.owned++; out.episodes++; out.aired++; } return; }
                if (special) { out.specials++; return; }
                out.episodes++;
                if (!ep.air_date || String(ep.air_date).slice(0, 10) <= today) out.aired++;
            });
        });
        if (!out.episodes && d) { out.episodes = d.episode_total || 0; out.aired = d.episode_total || 0; out.owned = d.episode_owned || 0; }
        return out;
    }

    function essentialChip(label, value, tone) {
        if (value == null || value === '') return '';
        return '<span class="vd-essential' + (tone ? ' vd-essential--' + tone : '') + '">' +
            '<span class="vd-essential-k">' + esc(label) + '</span>' +
            '<span class="vd-essential-v">' + esc(value) + '</span></span>';
    }
    function renderEssentials(d) {
        var host = q('[data-vd-essentials]');
        if (!host) return;
        if (!d) { host.hidden = true; host.innerHTML = ''; return; }
        var chips = [], yc = window.VideoYoutube;
        if (d.source === 'youtube') {
            chips.push(essentialChip('Loaded', countLabel(d.episode_total, 'video', 'videos'), 'focus'));
            chips.push(essentialChip('Downloaded', countLabel(d.episode_owned, 'video', 'videos'), 'ok'));
            if (d.kind === 'channel') {
                var wished = ((d._channel && d._channel.videos) || []).filter(function (v) { return v.wished; }).length;
                if (wished) chips.push(essentialChip('Wishlisted', countLabel(wished, 'video', 'videos'), 'focus'));
                if (d.video_count && d.video_count > d.episode_total) chips.push(essentialChip('Catalog', 'Loading more', 'live'));
            } else if (d.video_count && d.video_count > d.episode_total) {
                chips.push(essentialChip('Shown', d.episode_total + ' of ' + d.video_count, 'live'));
            }
            var views = yc && yc.compactCount(d.view_count);
            if (views) chips.push(essentialChip('Views', views, ''));
        }
        // Movies and shows carry nothing here any more: "Library 74%" and
        // "Status Ended" repeated the meta line one row up, "Next S05E02" is on
        // the Resume button, and "Best copy" lives in the Your library tab with
        // every other copy. The row stays for YouTube and the preview type chip.
        host.innerHTML = chips.filter(Boolean).join('');
        host.hidden = !chips.length;
    }

    // Type chip on a show NOT in the library yet (TMDB preview page): which
    // folder its episodes will be filed in once grabbed -- TV or Anime. Shown
    // only when an Anime folder is set. The type is stored server-side when the
    // show is followed / wished / approved (TMDB's suggestion); an admin can
    // change it here. A show IN the library takes its type from its library
    // (or by hand in Manage), so this chip is for previews only.
    var TYPE_LABELS = { standard: 'TV', daily: 'Daily', anime: 'Anime' };
    function renderTypeChip(d) {
        var host = q('[data-vd-essentials]');
        if (!host || !d || d.kind !== 'show' || d.source !== 'tmdb' || !d.tmdb_id || d.owned) return;
        var admin = _canManualMatch();
        var url = '/api/video/detail/tmdb/show/' + encodeURIComponent(d.tmdb_id) + '/series-type';
        fetch(url + (admin ? '?suggest=1' : ''), { headers: { 'Accept': 'application/json' } })
            .then(function (r) { return r.ok ? r.json() : null; })
            .then(function (t) {
                if (!t || t.owned || !t.anime_folder || data !== d) return;
                var old = host.querySelector('[data-vd-type-chip]');
                if (old) old.remove();
                var chip = document.createElement('span');
                chip.className = 'vd-essential vd-essential--type';
                chip.setAttribute('data-vd-type-chip', '');
                chip.title = t.source === 'hint-user' ? 'Set by an admin'
                    : t.source === 'hint-tmdb' ? 'Suggested by TMDB' : 'Not set yet: files with TV';
                var k = document.createElement('span');
                k.className = 'vd-essential-k';
                k.textContent = 'Files as';
                chip.appendChild(k);
                if (admin) {
                    var sel = document.createElement('select');
                    sel.className = 'vd-essential-v vd-type-select';
                    sel.setAttribute('aria-label', 'File this show as');
                    ['standard', 'anime', 'daily'].forEach(function (v) {
                        var o = document.createElement('option');
                        o.value = v;
                        o.textContent = TYPE_LABELS[v];
                        if (v === t.series_type) o.selected = true;
                        sel.appendChild(o);
                    });
                    sel.addEventListener('change', function () {
                        fetch(url, { method: 'PUT',
                            headers: { 'Content-Type': 'application/json', 'Accept': 'application/json' },
                            body: JSON.stringify({ series_type: sel.value }) })
                            .then(function (r) { return r.json().then(function (j) { return { ok: r.ok, j: j }; }); })
                            .then(function (res) {
                                if (!res.ok || !res.j.success) throw new Error((res.j && res.j.error) || 'save failed');
                                chip.title = 'Set by an admin';
                                toast('Will file as ' + TYPE_LABELS[sel.value], 'success');
                            })
                            .catch(function (e) {
                                sel.value = t.series_type;
                                toast('Could not save: ' + e.message, 'error');
                            });
                    });
                    chip.appendChild(sel);
                } else {
                    var v = document.createElement('span');
                    v.className = 'vd-essential-v';
                    v.textContent = TYPE_LABELS[t.series_type] || 'TV';
                    chip.appendChild(v);
                }
                host.appendChild(chip);
                host.hidden = false;
            })
            .catch(function () { /* a chip, never an error */ });
    }
    // The awards backfill stores a FLAG ('oscar' / 'winner', the overlay keys),
    // and the hero printed it raw: a lone lowercase "winner" under the ratings.
    // A full OMDb sentence (older rows) passes through unchanged.
    function awardsLabel(s) {
        var k = String(s || '').trim().toLowerCase();
        if (k === 'oscar') return 'Oscar winner';
        if (k === 'winner') return 'Award winner';
        return String(s || '').trim();
    }
    // OMDb Awards line ("Won 2 Oscars. 154 wins & 87 nominations total.") — the
    // overlay system already fetches it; the detail page finally shows it.
    function renderAwards(d) {
        var host = q('[data-vd-awards]');
        if (!host) return;
        var s = (d && d.awards || '').trim();
        if (!s || /^n\/?a$/i.test(s)) { host.hidden = true; host.innerHTML = ''; return; }
        host.hidden = false;
        host.innerHTML = '<span class="vd-awards-ic">🏆</span>' + esc(awardsLabel(s));
    }

    // Subtitle availability (OpenSubtitles backfill, #video-enrichment): a chip row
    // of the languages subtitles EXIST in for this title, so you know before you grab
    // it. Hidden entirely when we have no data.
    var SUB_LANG_NAMES = {
        en: 'English', es: 'Spanish', fr: 'French', de: 'German', it: 'Italian',
        pt: 'Portuguese', 'pt-br': 'Portuguese (BR)', nl: 'Dutch', pl: 'Polish',
        ru: 'Russian', ja: 'Japanese', ko: 'Korean', zh: 'Chinese', 'zh-cn': 'Chinese',
        ar: 'Arabic', tr: 'Turkish', sv: 'Swedish', da: 'Danish', fi: 'Finnish',
        no: 'Norwegian', cs: 'Czech', el: 'Greek', he: 'Hebrew', hi: 'Hindi',
        hu: 'Hungarian', ro: 'Romanian', th: 'Thai', uk: 'Ukrainian', vi: 'Vietnamese',
        id: 'Indonesian',
        // OpenSubtitles' own codes
        ea: 'Spanish (Latin America)', 'az-zb': 'South Azerbaijani', 'zh-tw': 'Chinese (Traditional)',
        ze: 'Chinese (bilingual)', me: 'Montenegrin', pm: 'Portuguese (Mozambique)', sx: 'Santali'
    };
    SUB_LANG_NAMES['zh-cn'] = 'Chinese (Simplified)';
    // the hand maps cover the common codes; the browser names the rest, so a
    // Persian or Bulgarian subtitle reads "Persian", not "FA"
    var _langNames = null;
    function langLabel(code) {
        var c = String(code || '').toLowerCase();
        if (!c) return '';
        if (SUB_LANG_NAMES[c]) return SUB_LANG_NAMES[c];
        try {
            if (_langNames === null) _langNames = (typeof Intl !== 'undefined' && Intl.DisplayNames) ? new Intl.DisplayNames(['en'], { type: 'language' }) : false;
            var n = _langNames && _langNames.of(c);
            if (n && n.toLowerCase() !== c) return n;
        } catch (e) { /* not a code Intl knows */ }
        return c.toUpperCase();
    }
    function subLangLabel(code) { return langLabel(code); }
    function renderSubtitles(d) {
        var el = q('[data-vd-subs]');
        if (!el) return;
        var langs = (d && d.subtitle_langs) || [];
        if (!langs.length) { el.hidden = true; el.innerHTML = ''; return; }
        var shown = langs.slice(0, 12);
        var chips = shown.map(function (c) {
            return '<span class="vd-sub" title="' + esc(subLangLabel(c)) + '">' + esc(subLangLabel(c)) + '</span>';
        });
        if (langs.length > shown.length) {
            chips.push('<span class="vd-sub vd-sub--more">+' + (langs.length - shown.length) + '</span>');
        }
        el.innerHTML = '<span class="vd-sub-label">Subtitles</span>' + chips.join('');
        el.hidden = false;
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

    // Does the dedicated ratings row have anything in it? Drives whether the meta
    // line needs to carry the score itself.
    function hasRatingRow(d) {
        return !!(d.imdb_rating || d.rt_rating != null || d.metacritic != null ||
                  d.trakt_rating || d.tvmaze_rating);
    }

    function renderRatings(d) {
        var host = q('[data-vd-ratings]');
        if (!host) return;
        // each score opens its own site (library pages; previews stay in-app)
        var q1 = encodeURIComponent(String(d.title || ''));
        var linked = d.source !== 'tmdb';
        function chip(cls, inner, url, tip) {
            var attrs = 'class="vd-rt ' + cls + '"' + (tip ? ' title="' + esc(tip) + '"' : '');
            return (linked && url)
                ? '<a ' + attrs + ' href="' + esc(url) + '" target="_blank" rel="noopener noreferrer">' + inner + '</a>'
                : '<span ' + attrs + '>' + inner + '</span>';
        }
        var items = [];
        if (d.imdb_rating) {
            items.push(chip('vd-rt--imdb', '<span class="vd-rt-tag">IMDb</span>' + (Math.round(d.imdb_rating * 10) / 10),
                d.imdb_id ? 'https://www.imdb.com/title/' + d.imdb_id + '/' : '', 'IMDb rating'));
        }
        if (d.rt_rating != null) {
            var fresh = d.rt_rating >= 60;
            items.push(chip('vd-rt--rt', '<span class="vd-rt-ic">' + (fresh ? '🍅' : '🤢') + '</span>' + d.rt_rating + '%',
                'https://www.rottentomatoes.com/search?search=' + q1, 'Rotten Tomatoes'));
        }
        if (d.metacritic != null) {
            var cls = d.metacritic >= 61 ? 'good' : d.metacritic >= 40 ? 'mid' : 'bad';
            items.push(chip('vd-rt--mc vd-rt--mc-' + cls, '<span class="vd-rt-tag">MC</span>' + d.metacritic,
                'https://www.metacritic.com/search/' + q1 + '/', 'Metacritic'));
        }
        if (d.trakt_rating) {
            items.push(chip('vd-rt--trakt', '<span class="vd-rt-tag">Trakt</span>' + (Math.round(d.trakt_rating * 10) / 10),
                d.tmdb_id ? 'https://trakt.tv/search/tmdb/' + d.tmdb_id + '?id_type=' + (d.kind === 'movie' ? 'movie' : 'show') : '',
                d.trakt_votes ? d.trakt_votes + ' Trakt votes' : 'Trakt'));
        }
        if (d.tvmaze_rating) {
            items.push(chip('vd-rt--tvmaze', '<span class="vd-rt-tag">TVmaze</span>' + (Math.round(d.tvmaze_rating * 10) / 10),
                'https://www.tvmaze.com/search?q=' + q1, 'TVmaze'));
        }
        // AniList only scores anime; older title-search matches gave Arrow and
        // The Bear a stranger's score, so it shows on animation only
        if (d.anilist_score && (d.genres || []).indexOf('Animation') >= 0) {
            items.push(chip('vd-rt--anilist', '<span class="vd-rt-tag">AniList</span>' + d.anilist_score + '%',
                'https://anilist.co/search/anime?search=' + q1, 'AniList'));
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
                var cb = (p.tmdb_id && window.VideoGet)
                    ? VideoGet.cardButton({ kind: 'person', tmdbId: p.tmdb_id, title: p.name, poster: p.photo }) : '';
                return p.tmdb_id
                    ? '<a class="vd-cast-card vd-cast-card--link" href="/video-detail/tmdb/person/' + p.tmdb_id +
                      '" data-vd-person="' + p.tmdb_id + '">' + cb + inner + '</a>'
                    : '<div class="vd-cast-card">' + inner + '</div>';
            }).join('');
            if (window.VideoWatchlist) VideoWatchlist.hydrate(castHost);
        }
        refreshStrips();
    }

    // The hero carries ONE primary action and at most two companions; the rest
    // lives behind a single "More" menu, grouped. It used to put up to nine
    // same-weight buttons in three rows (Play beside Manage Poster beside
    // Wishlist Missing), plus a row of tiny link tiles and a wall of subtitle
    // chips. Every action still exists and keeps its data-vd-act, so the click
    // dispatcher below is unchanged; only where each one renders moved.
    //
    //   primary    Play/Resume on your server; else Get (movie you don't own,
    //              show you don't own) or Get Missing (owned show, no server
    //              link); a profile without download rights gets Request
    //   companions Trailer, and the follow toggle that is a STATE (watchlist
    //              for an airing show, wishlist for a movie you don't own)
    //   More       acquisition extras, library management, Open on…
    function moreGroup(label, items) {
        return items ? '<div class="vd-more-group"><div class="vd-more-label">' + esc(label) + '</div>' + items + '</div>' : '';
    }
    function moreItem(act, icon, label, title, extra) {
        return '<button class="vd-manage-btn" type="button" data-vd-act="' + act + '"' + (extra || '') +
            (title ? ' title="' + esc(title) + '"' : '') + '>' +
            '<span class="vd-manage-ic" aria-hidden="true">' + icon + '</span> ' + esc(label) + '</button>';
    }
    function moreLink(label, url) {
        return '<a class="vd-manage-btn vd-more-link" href="' + esc(url) + '" target="_blank" rel="noopener noreferrer">' +
            '<span class="vd-manage-ic" aria-hidden="true">↗</span> ' + esc(label) + '</a>';
    }
    function externalLinks(d) {
        if (d.source === 'tmdb') return '';   // preview items keep everything in-app
        var out = '';
        if (d.imdb_id) out += moreLink('IMDb', 'https://www.imdb.com/title/' + d.imdb_id + '/');
        if (d.tmdb_id) out += moreLink('TMDB', 'https://www.themoviedb.org/' + (d.kind === 'movie' ? 'movie' : 'tv') + '/' + d.tmdb_id);
        if (d.tvdb_id) out += moreLink('TVDB', 'https://thetvdb.com/?id=' + d.tvdb_id + '&tab=series');
        // Letterboxd is film-only; it deep-links by TMDB id (#1039, QT3496)
        if (d.kind === 'movie' && d.tmdb_id) out += moreLink('Letterboxd', 'https://letterboxd.com/tmdb/' + d.tmdb_id + '/');
        if (d.wikidata_url) out += moreLink('Official site', d.wikidata_url);
        return out;
    }
    function missingAiredCount(d) {
        var today = new Date().toISOString().slice(0, 10), n = 0;
        (d.seasons || []).forEach(function (s) {
            if (s.season_number === 0) return;
            (s.episodes || []).forEach(function (ep) {
                if (!ep.owned && (!ep.air_date || ep.air_date <= today)) n++;
            });
        });
        return n;
    }

    function renderActions(d) {
        var a = q('[data-vd-actions]');
        if (!a) return;
        if (d.source === 'youtube') {
            // Same watchlist button as shows/movies (consistency); it follows the
            // CHANNEL or the PLAYLIST depending on what this page is.
            var on = !!d.following;
            var isPl = d.kind === 'playlist';
            var ytUrl = isPl
                ? 'https://www.youtube.com/playlist?list=' + esc(d.id)
                : 'https://www.youtube.com/channel/' + esc(d.id);
            // the follow is the one action; settings (folder, quality, filters,
            // retention) used to be reachable only from the watchlist page's cog
            var ytLib = (window.VideoYoutube && VideoYoutube.openChannelSettings && _canManualMatch())
                ? moreItem('yt-settings', '⚙', (isPl ? 'Playlist' : 'Channel') + ' settings…',
                    'Folder name, quality, title filters and what to keep')
                : '';
            var ytMore = moreGroup('Manage', ytLib) + moreGroup('Open on', moreLink('YouTube', ytUrl));
            a.innerHTML =
                '<button class="library-artist-watchlist-btn vd-action-main' + (on ? ' watching' : '') + '" type="button" data-vd-act="' +
                    (isPl ? 'yt-pl-follow' : 'yt-follow') + '">' +
                    '<span class="watchlist-icon">' + (on ? '✓' : '＋') + '</span>' +
                    '<span class="watchlist-text">' + (on ? 'In Watchlist' : 'Watchlist') + '</span></button>' +
                '<div class="vd-more" data-vd-more>' +
                    '<button class="vd-manage-btn vd-more-btn" type="button" data-vd-act="more" ' +
                        'aria-haspopup="true" aria-expanded="false" title="More actions" aria-label="More actions">' +
                        '<span class="vd-manage-ic" aria-hidden="true">⋯</span> More</button>' +
                    '<div class="vd-more-menu" data-vd-more-menu hidden>' + ytMore + '</div>' +
                '</div>';
            return;
        }
        // The watchlist eye applies only to airing shows (movies + ended shows are
        // terminal — they get acquisition, not a "watch for new" follow).
        var isAiringShow = d.kind === 'show' && d.tmdb_id && (!window.VideoGet || VideoGet.isAiring(d.status));
        var watching = !!d._vw_watched;
        // Lazily resolve the real watched state once (airing library shows are on
        // by default), then re-render — see the new curated watchlist system.
        if (isAiringShow && !d._vw_checked && window.VideoWatchlist) {
            d._vw_checked = true;
            fetch('/api/video/watchlist/check', { method: 'POST', headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ kind: 'show', tmdb_ids: [d.tmdb_id] }) })
                .then(function (r) { return r.ok ? r.json() : null; })
                .then(function (res) {
                    if (res && res.results) { d._vw_watched = !!res.results[String(d.tmdb_id)]; if (data === d) renderActions(d); }
                }).catch(function () { /* keep default state */ });
        }
        // Requests (arr-parity P4): a profile WITHOUT download rights has no
        // acquisition path at all — give it the ask-an-admin button instead of
        // the Get/Watchlist controls (those APIs are gated for this profile).
        var _canDl = (typeof canDownload !== 'function') || canDownload();
        // an owned movie has nothing left to ask for; a show can still be
        // missing seasons. "Requested" once this profile has one waiting.
        var _ownedMovie = d.kind === 'movie' && ((d.source !== 'tmdb') || !!d.owned);
        var ownLibItem = (d.source !== 'tmdb') || d.owned;
        var haveShow = d.kind === 'show' && ((d.source !== 'tmdb') || !!d.owned);
        var missingEps = haveShow ? missingAiredCount(d) : 0;
        var primary = '', companions = '', getMore = '', libMore = '';

        // ── primary ──
        // Play on your media server (owned items; arrives with extras). The logo
        // IS the brand name — "Play on <logo>" (no redundant word).
        if (d.server && d.server.url) {
            var sv = esc(d.server.server || 'Server');
            var slogo = SERVER_LOGOS[d.server.server];
            // Continue Watching: shows deep-link the NEXT episode ("Resume S2 E4"),
            // an in-progress movie says Resume (the server resumes it itself).
            var snu = d.server.next_up;
            var href = (snu && d.server.episode_url) ? d.server.episode_url : d.server.url;
            var verb = 'Play', epTag = '', tip = 'Play on ' + sv;
            if (snu) {
                verb = snu.resume ? 'Resume' : 'Play';
                epTag = '<span class="vd-play-ep">S' + snu.season + ' E' + snu.episode + '</span>';
                tip = (snu.resume ? 'Resume' : 'Next up') + ' S' + snu.season + ' E' + snu.episode + ' on ' + sv;
            } else if (d.kind === 'movie' && !d.watched && (d.view_offset_ms || 0) > 0) {
                verb = 'Resume';
                var left = d.runtime_minutes
                    ? Math.max(1, Math.round(d.runtime_minutes - d.view_offset_ms / 60000)) : 0;
                tip = 'Resume on ' + sv + (left ? ' · ' + left + ' min left' : '');
            }
            var inner = slogo
                ? '<span class="vd-play-ic">▶</span><span>' + verb + ' on</span>' +
                  '<img class="vd-play-logo" src="' + esc(slogo) + '" alt="' + sv + '">' + epTag
                : '<span class="vd-play-ic">▶</span><span>' + verb + ' on ' + sv + '</span>' + epTag;
            primary = '<a class="vd-play-btn vd-action-main" href="' + esc(href) +
                '" target="_blank" rel="noopener" title="' + tip + '">' + inner + '</a>';
        }
        if (!_canDl && (d.kind === 'movie' || d.kind === 'show') && d.tmdb_id && !_ownedMovie) {
            if (!d._req_checked) {
                d._req_checked = true;
                fetch('/api/video/requests?status=pending', { headers: { 'Accept': 'application/json' } })
                    .then(function (r) { return r.ok ? r.json() : null; })
                    .then(function (res) {
                        var mine = !!(res && res.requests && res.requests.some(function (x) {
                            return x.kind === d.kind && String(x.tmdb_id) === String(d.tmdb_id);
                        }));
                        if (mine !== !!d._req_pending) { d._req_pending = mine; if (data === d) renderActions(d); }
                    }).catch(function () { /* keep the plain button */ });
            }
            var asked = !!d._req_pending;
            var reqBtn =
                '<button class="library-artist-watchlist-btn vd-action-main' + (asked ? ' watching' : '') +
                '" type="button" data-vd-act="request"' + (asked ? ' disabled' : '') + '>' +
                '<span class="watchlist-icon">' + (asked ? '✓' : '🙋') + '</span>' +
                '<span class="watchlist-text">' + (asked ? 'Requested' : 'Request') + '</span></button>';
            if (!primary) primary = reqBtn; else companions += reqBtn;
        }
        // Movies are terminal — no "watch for new" follow, so they get the shared
        // Get control (unowned → add/download, owned → re-download / upgrade). It
        // opens the same VideoGet modal the discover/search cards use.
        if (d.kind === 'movie' && window.VideoGet && _canDl) {
            var getMovie = '<button class="library-artist-watchlist-btn vd-action-main" type="button" data-vd-act="get">' +
                '<span class="watchlist-icon">⬇</span><span class="watchlist-text">Get</span></button>';
            if (!_ownedMovie && !primary) primary = getMovie;
            else getMore += moreItem('get', '⬇', _ownedMovie ? 'Download again or upgrade…' : 'Get…',
                _ownedMovie ? 'Search for a better copy, or grab this one again' : '');
            // TWO controls, like the shows' follow+get pair: Get always exists (the
            // modal offers download-now / manual search / grab), and the wishlist
            // is a separate TOGGLE showing membership. One button wearing three
            // states once made a wishlisted movie lose the whole download path.
            var wished = !!d._wl_wished;
            if (_ownedMovie) {
                getMore += moreItem('wishtoggle', wished ? '✓' : '＋',
                    wished ? 'In wishlist — remove' : 'Wishlist for an upgrade');
            } else {
                companions += '<button class="library-artist-watchlist-btn' + (wished ? ' watching' : '') + '" type="button" data-vd-act="wishtoggle">' +
                    '<span class="watchlist-icon">' + (wished ? '✓' : '＋') + '</span>' +
                    '<span class="watchlist-text">' + (wished ? 'In Wishlist' : 'Wishlist') + '</span></button>';
            }
            // Subtitle search (Phase 3): Bazarr-style manual subtitle search for an
            // owned library movie — the modal writes the .srt next to the file.
            if (currentSource === 'library' && d.owned && window.VideoSubtitles) {
                getMore += moreItem('subsearch', '💬', 'Search subtitles', 'Search subtitles — pick one yourself');
            }
            // Reflect wishlist membership (like the show watchlist eye): check once,
            // then re-render. Re-checked on soulsync:video-wishlist-changed.
            if (!d._wl_checked && d.tmdb_id) {
                d._wl_checked = true;
                fetch('/api/video/wishlist/check', { method: 'POST', headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ movie_ids: [d.tmdb_id] }) })
                    .then(function (r) { return r.ok ? r.json() : null; })
                    .then(function (res) {
                        var w = !!(res && res.movies && res.movies.some(function (m) { return String(m) === String(d.tmdb_id); }));
                        if (w !== !!d._wl_wished) { d._wl_wished = w; if (data === d) renderActions(d); }
                    }).catch(function () { /* keep default */ });
            }
        }
        // Shows: "Get Show" for one you don't have; "Get Missing" fills the gaps
        // in one you do. Either is the primary when nothing plays yet.
        if (d.kind === 'show' && window.VideoGet && _canDl) {
            var showGetLabel = haveShow ? 'Get Missing' : 'Get Show';
            var getShow = '<button class="discog-download-btn discog-btn-compact vd-action-main" type="button" data-vd-act="missing">' +
                '<span class="discog-btn-icon">⭳</span><span class="discog-btn-text">' + showGetLabel + '</span>' +
                '<span class="discog-btn-shimmer"></span></button>';
            if (!primary && (!haveShow || missingEps > 0)) primary = getShow;
            else getMore += moreItem('missing', '⭳', haveShow
                ? (missingEps ? 'Get ' + missingEps + ' missing episode' + (missingEps === 1 ? '' : 's') + '…' : 'Get episodes…')
                : 'Get show…');
        }
        if (d.trailer && d.trailer.key) {
            companions = '<button class="vd-trailer-btn" type="button" data-vd-act="trailer">' +
                '<span class="vd-trailer-ic">▶</span> Trailer</button>' + companions;
        }
        // Watchlist (follow an AIRING show to wishlist its new episodes) applies whether
        // the show is owned or a TMDB preview — the curated watchlist is keyed by
        // tmdb_id. Ended/cancelled shows are terminal (isAiringShow=false) → no button.
        if (isAiringShow && _canDl) {
            var showPoster = d.source !== 'tmdb' ? ('/api/video/poster/show/' + d.id) : proxied(d.poster_url);
            companions +=
                '<button class="library-artist-watchlist-btn vwl-btn vd-vwl-action' +
                (watching ? ' watching active' : '') + '" type="button"' +
                ' data-vwl-kind="show" data-vwl-id="' + esc(d.tmdb_id) + '"' +
                ' data-vwl-title="' + esc(d.title || '') + '"' +
                ' data-vwl-poster="' + esc(showPoster || '') + '"' +
                (d.source !== 'tmdb' && d.id ? ' data-vwl-libid="' + esc(d.id) + '"' : '') +
                ' title="' + (watching ? 'On watchlist' : 'Add to watchlist') + '"' +
                ' aria-label="' + (watching ? 'On watchlist' : 'Add to watchlist') + '">' +
                '<span class="watchlist-icon">' + (watching ? '✓' : '＋') + '</span>' +
                '<span class="watchlist-text">' + (watching ? 'In Watchlist' : 'Watchlist') + '</span></button>';
        }
        // Whole-show wishlist — every missing AIRED episode across every season in
        // one click (the season bar only covers the selected season). YouTube
        // channels have their own wishlist system (yt wish buttons), so skip them.
        if (d.kind === 'show' && window.VideoGrab && _canDl && (!haveShow || missingEps > 0)) {
            getMore += moreItem('wishlist-missing', '＋', 'Wishlist every missing episode',
                'Add every missing aired episode across all seasons to the wishlist');
        }
        // "I have this" — the auto-matcher keys library rows by tmdb_id and sometimes
        // whiffs (a title on disk with a NULL/wrong tmdb_id reads as "not in
        // library"). TMDB preview pages are never owned — owned ones redirect to the
        // library detail — so this is exactly the unowned case. Admin-gated
        // server-side like the other library mutations.
        if (d.source === 'tmdb' && (d.kind === 'movie' || d.kind === 'show') &&
                d.tmdb_id && _canManualMatch()) {
            libMore += moreItem('ihavethis', '✓', 'I have this…', 'Link this title to the copy already in your library');
        }
        // Library management — library rows only.
        var libRow = (d.source !== 'tmdb') || d.library_id != null;
        if (ownLibItem && libRow && (d.kind === 'movie' || d.kind === 'show') && window.VideoLibraryTab && currentSource === 'library') {
            libMore += moreItem('library-tab', '▤', 'Files and match', 'Your copies, where they live, and the match');
        }
        // Manage — the per-item metadata editor (edits write to the local row +
        // push to the item's own server). Library-sourced pages always have a row;
        // TMDB pages only when owned (library_id resolves it).
        if (ownLibItem && window.VideoManage && libRow) {
            libMore += moreItem('manage', '✎', 'Edit details', 'Edit metadata');
        }
        // Manage Poster — needs a server id + folder to push to, and a tmdb id
        // to fetch the alternates. Opens VideoPoster.
        if (ownLibItem && d.tmdb_id && window.VideoPoster) {
            libMore += moreItem('poster', '🖼', 'Change poster', 'Change poster');
        }
        // Synchronize — a deep scan scoped to THIS title: re-reads it from the
        // server and reconciles without waiting for a full library scan.
        var libShowId = (d.kind === 'show' && ownLibItem) ? (d.source !== 'tmdb' ? d.id : d.library_id) : null;
        if (libShowId != null) {
            libMore += moreItem('sync-show', '⟳', 'Sync with server',
                'Re-read this show from your server — picks up new or removed episodes',
                ' data-vd-sync-id="' + esc(libShowId) + '"');
        }
        var libMovieId = (d.kind === 'movie' && ownLibItem) ? (d.source !== 'tmdb' ? d.id : d.library_id) : null;
        if (libMovieId != null) {
            libMore += moreItem('sync-movie', '⟳', 'Sync with server',
                'Re-read this movie from your server - updates file, watch, and metadata state',
                ' data-vd-sync-id="' + esc(libMovieId) + '"');
        }
        // Watched toggle (the /watched API): local state + markPlayed/markUnplayed
        // pushed to the server. Library rows only.
        if (ownLibItem && (d.kind === 'movie' || d.kind === 'show') && libRow &&
                (d.owned || d.episode_owned || d.watched)) {
            libMore += moreItem('watched-toggle', d.watched ? '↺' : '✓', d.watched ? 'Mark unwatched' : 'Mark watched',
                d.watched ? 'Mark unwatched (clears played state on your server too)'
                          : 'Mark watched (marks played on your server too)');
        }
        // Manual "I have this" link state — library pages only. When this row
        // was linked by hand, the menu offers the unlink.
        var _mmLibId = (d.source !== 'tmdb' && (d.kind === 'movie' || d.kind === 'show') && d.id != null)
            ? d.id : null;
        if (_mmLibId != null && !d._mm_checked && _canManualMatch()) {
            d._mm_checked = true;
            fetch('/api/video/manual-match/status?kind=' + d.kind + '&library_id=' + _mmLibId,
                { headers: { 'Accept': 'application/json' } })
                .then(function (r) { return r.ok ? r.json() : null; })
                .then(function (res) {
                    if (res && res.tmdb_id) { d._mm_tmdb = res.tmdb_id; if (data === d) renderActions(d); }
                }).catch(function () { /* stay silent — no link, no menu item */ });
        }
        if (d._mm_tmdb) {
            libMore += moreItem('unlink', '🔗', 'Linked by hand — unlink',
                'This title was linked to your library by hand (I have this). Remove the link?');
        }
        var links = externalLinks(d);
        var more = moreGroup('Get', getMore) + moreGroup('Library', libMore) + moreGroup('Open on', links);
        var html = primary + companions;
        // One button instead of many. With a single item behind it the menu is
        // pure overhead, so that item just rides in the main row.
        if (more) {
            html += (moreCount(getMore + libMore) === 1 && !links) ? getMore + libMore :
                '<div class="vd-more" data-vd-more>' +
                    '<button class="vd-manage-btn vd-more-btn" type="button" data-vd-act="more" ' +
                        'aria-haspopup="true" aria-expanded="false" title="More actions" aria-label="More actions">' +
                        '<span class="vd-manage-ic" aria-hidden="true">⋯</span> More</button>' +
                    '<div class="vd-more-menu" data-vd-more-menu hidden>' + more + '</div>' +
                '</div>';
        }
        a.innerHTML = html;
    }

    function moreCount(html) { return (html.match(/<button/g) || []).length; }

    function toggleMoreMenu(btn) {
        var wrap = btn.closest('[data-vd-more]');
        var menu = wrap && wrap.querySelector('[data-vd-more-menu]');
        if (!menu) return;
        var open = menu.hidden;
        menu.hidden = !open;
        btn.setAttribute('aria-expanded', open ? 'true' : 'false');
    }
    function closeMoreMenu() {
        var menu = q('[data-vd-more-menu]');
        if (menu && !menu.hidden) {
            menu.hidden = true;
            var b = q('[data-vd-more] .vd-more-btn');
            if (b) b.setAttribute('aria-expanded', 'false');
        }
    }

    // ── "I have this" — manual library match ────────────────────────────────
    // The auto-matcher keys library rows by tmdb_id; when it whiffs, the user
    // points the TMDB id at the right library row themselves. After a link,
    // the detail reload hits the redirect and lands on the library page —
    // every surface (cards, rails, detail) then reads the title as owned.
    function _canManualMatch() {
        // Writes are admin-gated server-side (like the other library
        // mutations); fail open here the way canDownload does.
        try {
            if (typeof getCurrentProfileContext !== 'function') return true;
            var ctx = getCurrentProfileContext();
            return !ctx || !!ctx.isAdmin;
        } catch (e) { return true; }
    }

    var _vhtStyled = false;
    function _haveThisStyles() {
        if (_vhtStyled) return; _vhtStyled = true;
        var css =
            '.vht-overlay{position:fixed;inset:0;z-index:1200;display:flex;align-items:center;justify-content:center;' +
            'background:rgba(4,4,8,.72);backdrop-filter:blur(6px);animation:vd-fade .18s ease;}' +
            '.vht-modal{width:min(560px,94vw);max-height:86vh;display:flex;flex-direction:column;overflow:hidden;' +
            'background:#14141c;border:1px solid rgba(255,255,255,.1);border-radius:16px;' +
            'box-shadow:0 24px 80px rgba(0,0,0,.6);}' +
            '.vht-head{display:flex;align-items:flex-start;gap:12px;padding:18px 18px 0;}' +
            '.vht-title{font-size:17px;font-weight:700;}' +
            '.vht-sub{font-size:13px;opacity:.65;margin-top:4px;line-height:1.45;}' +
            '.vht-x{margin-left:auto;background:none;border:0;color:inherit;font-size:16px;cursor:pointer;opacity:.6;padding:4px;}' +
            '.vht-x:hover{opacity:1;}' +
            '.vht-search{margin:14px 18px 0;padding:10px 14px;font-size:14px;color:inherit;background:rgba(255,255,255,.06);' +
            'border:1px solid rgba(255,255,255,.14);border-radius:10px;outline:none;}' +
            '.vht-search:focus{border-color:rgba(255,255,255,.35);}' +
            '.vht-results{overflow-y:auto;padding:10px 12px 4px;min-height:120px;}' +
            '.vht-hint{padding:26px 10px;text-align:center;font-size:13px;opacity:.5;}' +
            '.vht-row{display:flex;align-items:center;gap:12px;width:100%;text-align:left;padding:8px;border:0;border-radius:10px;' +
            'background:none;color:inherit;cursor:pointer;}' +
            '.vht-row:hover{background:rgba(255,255,255,.06);}' +
            '.vht-row--sel{background:rgba(88,101,242,.18);outline:1px solid rgba(88,101,242,.55);}' +
            '.vht-poster{width:40px;height:60px;object-fit:cover;border-radius:6px;background:rgba(255,255,255,.06);flex:none;}' +
            '.vht-row-t{display:flex;flex-direction:column;gap:2px;min-width:0;}' +
            '.vht-row-title{font-size:14px;font-weight:600;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;}' +
            '.vht-row-sub{font-size:12px;opacity:.55;}' +
            '.vht-check{margin-left:auto;font-size:16px;color:#5865f2;opacity:0;flex:none;}' +
            '.vht-row--sel .vht-check{opacity:1;}' +
            '.vht-foot{display:flex;justify-content:flex-end;gap:10px;padding:14px 18px 18px;}' +
            '.vht-foot button{padding:9px 18px;font-size:14px;border-radius:10px;cursor:pointer;border:1px solid rgba(255,255,255,.14);' +
            'background:rgba(255,255,255,.06);color:inherit;}' +
            '.vht-foot .vht-link{background:#5865f2;border-color:#5865f2;color:#fff;font-weight:600;}' +
            '.vht-foot .vht-link:disabled{opacity:.4;cursor:default;}';
        var st = document.createElement('style');
        st.textContent = css;
        document.head.appendChild(st);
    }

    function closeHaveThisModal() {
        var ov = document.querySelector('.vht-overlay');
        if (ov) ov.remove();
        document.removeEventListener('keydown', _vhtKey);
    }
    function _vhtKey(e) {
        if (e.key === 'Escape') closeHaveThisModal();
    }

    function openHaveThisModal() {
        if (!data || data.source !== 'tmdb' || !data.tmdb_id ||
                (data.kind !== 'movie' && data.kind !== 'show')) return;
        _haveThisStyles();
        closeHaveThisModal();
        var kind = data.kind, tmdbId = data.tmdb_id, d = data;
        var kindNoun = kind === 'movie' ? 'movies' : 'shows';
        var ov = document.createElement('div');
        ov.className = 'vht-overlay';
        ov.innerHTML =
            '<div class="vht-modal" role="dialog" aria-modal="true" aria-label="I have this">' +
                '<div class="vht-head"><div>' +
                    '<div class="vht-title">I have this</div>' +
                    '<div class="vht-sub">SoulSync couldn\u2019t match <b>' + esc(d.title || '') + '</b> ' +
                    'to your library. Search below for the copy you actually own, then link them.</div>' +
                '</div><button class="vht-x" type="button" data-vht="close" aria-label="Close">\u2715</button></div>' +
                '<input class="vht-search" type="search" placeholder="Search your ' + kindNoun + '\u2026" ' +
                    'autocomplete="off" value="' + esc(d.title || '') + '">' +
                '<div class="vht-results" data-vht-results></div>' +
                '<div class="vht-foot"><button type="button" data-vht="close">Cancel</button>' +
                '<button class="vht-link" type="button" data-vht="link" disabled>Link selected</button></div>' +
            '</div>';
        document.body.appendChild(ov);
        document.addEventListener('keydown', _vhtKey);
        var input = ov.querySelector('.vht-search');
        var box = ov.querySelector('[data-vht-results]');
        var linkBtn = ov.querySelector('[data-vht="link"]');
        var selected = null, timer = null, seq = 0, found = {};

        function hint(t) { box.innerHTML = '<div class="vht-hint">' + t + '</div>'; }
        function search(qry, mySeq) {
            fetch('/api/video/manual-match/search?kind=' + kind + '&q=' + encodeURIComponent(qry),
                { headers: { 'Accept': 'application/json' } })
                .then(function (r) { return r.ok ? r.json() : null; })
                .then(function (res) {
                    if (mySeq !== seq || !document.body.contains(ov)) return;
                    selected = null; linkBtn.disabled = true;
                    var list = (res && res.results) || [];
                    found = {};
                    list.forEach(function (r) { found[String(r.id)] = r; });
                    if (!list.length) { hint('No matches in your library — try a shorter search.'); return; }
                    box.innerHTML = list.map(function (r) {
                        var note = (r.tmdb_id && String(r.tmdb_id) !== String(tmdbId))
                            ? 'auto-matched elsewhere' : (r.tmdb_id ? 'already matched' : 'unmatched');
                        return '<button class="vht-row" type="button" data-vht-pick="' + r.id + '">' +
                            '<img class="vht-poster" src="/api/video/poster/' + kind + '/' + r.id + '" alt="" loading="lazy" ' +
                                'onerror="this.style.visibility=\'hidden\'">' +
                            '<span class="vht-row-t"><span class="vht-row-title">' + esc(r.title || '') + '</span>' +
                            '<span class="vht-row-sub">' + esc(r.year || '\u2014') + ' \u00b7 ' + note + '</span></span>' +
                            '<span class="vht-check">\u2713</span></button>';
                    }).join('');
                })
                .catch(function () { if (mySeq === seq) hint('Search failed — try again.'); });
        }
        function kick() {
            var qry = input.value.trim();
            clearTimeout(timer);
            if (qry.length < 2) {
                selected = null; linkBtn.disabled = true;
                hint('Type at least 2 characters to search your ' + kindNoun + '.');
                return;
            }
            timer = setTimeout(function () { search(qry, ++seq); }, 300);
        }
        ov.addEventListener('click', function (e) {
            var c = e.target.closest('[data-vht]');
            if (c) {
                var w = c.getAttribute('data-vht');
                if (w === 'close') { closeHaveThisModal(); return; }
                if (w === 'link') { doLink(); return; }
            }
            var pick = e.target.closest('[data-vht-pick]');
            if (pick) {
                selected = pick.getAttribute('data-vht-pick');
                var rows = box.querySelectorAll('.vht-row');
                for (var i = 0; i < rows.length; i++)
                    rows[i].classList.toggle('vht-row--sel', rows[i] === pick);
                linkBtn.disabled = false;
                return;
            }
            if (e.target === ov) closeHaveThisModal();
        });
        function doLink() {
            if (!selected || linkBtn.disabled) return;
            var row = found[String(selected)] || {};
            // An unmatched copy IS this title: re-identify the library row itself
            // (locked, like Fix match). A side link alone made it read as owned
            // and nothing else — its artwork, details and upgrades stayed wrong.
            // A copy matched to another title is re-identified only when asked.
            if (row.tmdb_id && String(row.tmdb_id) !== String(tmdbId) && typeof showConfirmDialog === 'function') {
                showConfirmDialog({ title: 'Re-identify this copy?',
                    message: '\u201c' + (row.title || 'That copy') + '\u201d' + (row.year ? ' (' + row.year + ')' : '') +
                        ' is matched to a different title in your library. Re-identify it as \u201c' +
                        (d.title || 'this title') + '\u201d? Its artwork, details and upgrades follow, and a scan won\u2019t switch it back.',
                    confirmText: 'Re-identify', cancelText: 'Cancel' })
                    .then(function (ok) { if (ok) send(true); });
                return;
            }
            send(!row.tmdb_id || String(row.tmdb_id) !== String(tmdbId));
        }
        function send(reidentify) {
            linkBtn.disabled = true;
            linkBtn.textContent = 'Linking\u2026';
            fetch('/api/video/manual-match', {
                method: 'POST', headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ kind: kind, tmdb_id: tmdbId, library_id: parseInt(selected, 10),
                    reidentify: !!reidentify })
            })
                .then(function (r) { return r.json().then(function (j) { return { ok: r.ok, j: j }; }); })
                .then(function (res) {
                    if (res.ok && res.j && res.j.success) {
                        closeHaveThisModal();
                        toast(res.j.reidentified
                            ? 'Matched \u2014 your copy is now this title'
                            : 'Linked \u2014 this title now shows as in your library');
                        // The reload hits the new match (or the manual link):
                        // tmdb_detail answers with the redirect and we land on
                        // the library page.
                        if (kind === 'movie') loadMovie(currentId, 'tmdb');
                        else loadShow(currentId, 'tmdb');
                    } else {
                        linkBtn.disabled = false;
                        linkBtn.textContent = 'Link selected';
                        toast((res.j && res.j.error) || 'Couldn\u2019t link \u2014 is that title still in your library?', 'error');
                    }
                })
                .catch(function () {
                    linkBtn.disabled = false;
                    linkBtn.textContent = 'Link selected';
                    if (typeof showToast === 'function') showToast('Couldn\u2019t reach the server', 'error');
                });
        }
        input.addEventListener('input', kick);
        input.focus();
        try { input.select(); } catch (e) { /* noop */ }
        kick();   // the TMDB title is pre-filled — search it immediately
    }

    // Remove a hand-made link. The button lives in the More menu, which
    // closes on any pick, so a two-click arm never got its second click.
    // Ask with the SoulSync confirm modal instead.
    function unlinkManualMatch() {
        if (!data || data.source === 'tmdb' || !data._mm_tmdb) return;
        var d = data;
        var doUnlink = function () {
            fetch('/api/video/manual-match', {
                method: 'DELETE', headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ kind: d.kind, tmdb_id: d._mm_tmdb })
            })
                .then(function (r) { return r.ok ? r.json() : null; })
                .then(function (res) {
                    if (res && res.success) {
                        d._mm_tmdb = null;
                        toast('Manual link removed');
                        if (data === d) renderActions(d);
                    } else {
                        toast('Couldn\u2019t remove the link', 'error');
                    }
                })
                .catch(function () { toast('Couldn\u2019t reach the server', 'error'); });
        };
        if (typeof showConfirmDialog !== 'function') { doUnlink(); return; }
        showConfirmDialog({ title: 'Remove manual link',
            message: '\u201c' + (d.title || 'This title') + '\u201d was linked to your library by hand. ' +
                'Remove the link? The TMDB page will read as not in your library again.',
            confirmText: 'Unlink', cancelText: 'Cancel', destructive: true })
            .then(function (ok) { if (ok) doUnlink(); });
    }

    // Continue Watching: played/unplayed toggle → POST /detail/<kind>/<id>/watched
    // (local rows + server markPlayed), then mirror the result in-page.
    function toggleWatchedState(btn) {
        if (!data || btn.disabled) return;
        var kind = data.kind === 'movie' ? 'movie' : 'show';
        var libId = (data.source !== 'tmdb') ? data.id : data.library_id;
        if (libId == null) return;
        var target = !data.watched;
        btn.disabled = true;
        fetch(DETAIL_URL + kind + '/' + libId + '/watched', {
            method: 'POST', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ watched: target }) })
            .then(function (r) { return r.ok ? r.json() : null; })
            .then(function (res) {
                btn.disabled = false;
                if (!res || !res.ok) throw new Error();
                // Mirror what the backend did so the page reacts instantly.
                data.watched = target;
                if (kind === 'movie') {
                    data.play_count = target ? 1 : 0;
                    data.view_offset_ms = 0;
                } else {
                    data.watched_episodes = target ? data.episode_total : 0;
                    (data.seasons || []).forEach(function (s) {
                        (s.episodes || []).forEach(function (ep) {
                            ep.watched = target; ep.view_offset_ms = 0;
                        });
                    });
                    data.next_up = null;
                    if (data.server) { delete data.server.next_up; delete data.server.episode_url; }
                }
                renderBillboard(data);
                if (kind === 'show') renderEpisodes();
                if (typeof showToast === 'function') {
                    showToast((target ? 'Marked watched' : 'Marked unwatched') +
                        (res.pushed ? ' — synced to your server' : ''), 'success');
                }
            })
            .catch(function () {
                btn.disabled = false;
                if (typeof showToast === 'function') showToast('Could not update watched state', 'error');
            });
    }

    // Open the shared Get modal for the current item (movies use this in place of
    // the airing-show watchlist follow). The modal fetches its own details from the
    // kind/source/id, then offers Download + Add-to-Wishlist.
    function openGetModal(startDownload) {
        if (!window.VideoGet || !data) return;
        VideoGet.open({
            kind: data.kind,
            source: data.source || currentSource || 'library',
            id: (data.id != null) ? data.id : currentId,
            title: data.title || '',
            // "Get Missing" jumps straight to the season/episode grab view.
            startDownload: !!startDownload,
        });
    }

    // Wishlist toggle for the movie hero's dedicated button — quick add/remove
    // without opening the Get modal (mirrors the shows' watchlist toggle).
    function toggleMovieWishlist(btn) {
        if (!data || data.kind !== 'movie' || !data.tmdb_id) { openGetModal(); return; }
        var d = data;
        var wished = !!d._wl_wished;
        if (btn) btn.disabled = true;
        var done = function (nowWished, msg) {
            d._wl_wished = nowWished;
            if (data === d) renderActions(d);
            document.dispatchEvent(new CustomEvent('soulsync:video-wishlist-changed'));
            if (typeof showToast === 'function') showToast(msg, 'success');
        };
        var fail = function () {
            if (btn) btn.disabled = false;
            if (typeof showToast === 'function') showToast('Wishlist update failed', 'error');
        };
        var opts = { method: 'POST', headers: { 'Content-Type': 'application/json' } };
        if (wished) {
            opts.body = JSON.stringify({ scope: 'movie', tmdb_id: d.tmdb_id });
            fetch('/api/video/wishlist/remove', opts)
                .then(function (r) { return r.ok ? r.json() : null; })
                .then(function (res) { (res && res.success) ? done(false, 'Removed from wishlist') : fail(); })
                .catch(fail);
        } else {
            opts.body = JSON.stringify({ movie: { tmdb_id: d.tmdb_id, title: d.title,
                year: d.year, poster_url: d.poster_url || null,
                library_id: (d.source !== 'tmdb') ? (d.id != null ? d.id : currentId) : (d.library_id || null) } });
            fetch('/api/video/wishlist/add', opts)
                .then(function (r) { return r.ok ? r.json() : null; })
                .then(function (res) { (res && res.success) ? done(true, 'Added to wishlist') : fail(); })
                .catch(fail);
        }
    }

    // Manage — the per-item metadata editor (slide-over panel, own module).
    function openManagePanel() {
        if (!window.VideoManage || !data) return;
        var libId = (data.source !== 'tmdb') ? ((data.id != null) ? data.id : currentId) : (data.library_id || null);
        if (libId == null) return;
        VideoManage.open({ kind: data.kind, id: libId });
    }

    // Manage Poster — open the poster manager for this item (library id + tmdb id).
    function openPosterModal() {
        if (!window.VideoPoster || !data) return;
        var libId = (data.source !== 'tmdb') ? ((data.id != null) ? data.id : currentId) : (data.library_id || null);
        VideoPoster.open({
            kind: data.kind, tmdbId: data.tmdb_id || null, libraryId: libId,
            title: data.title || '', year: data.year || null,
        });
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
    function channelsLabel(n) {
        n = Number(n) || 0;
        if (n >= 8) return '7.1';
        if (n === 7) return '6.1';
        if (n === 6) return '5.1';
        if (n === 2) return 'Stereo';
        if (n === 1) return 'Mono';
        return n ? n + 'ch' : '';
    }
    // Netflix/Disney-style format badges for the hero meta: 4K · HDR10 · Atmos · 5.1
    function formatBadges(f) {
        if (!f) return [];
        var b = [];
        var res = mediaRes(f.resolution); if (res) b.push(res);
        if (f.dynamic_range) b.push(f.dynamic_range);
        if (f.atmos) b.push('Atmos');
        else { var ch = channelsLabel(f.audio_channels); if (ch && ch !== 'Stereo' && ch !== 'Mono') b.push(ch); }
        return b.map(function (t) { return '<span class="vd-fmt">' + esc(t) + '</span>'; });
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
        return [mediaRes(v.resolution), v.dynamic_range || '', prettyCodec(v.video_codec),
            v.audio_codec ? String(v.audio_codec).toUpperCase() : '',
            v.atmos ? 'Atmos' : channelsLabel(v.audio_channels), fmtBytes(v.size_bytes),
            v.release_source ? prettySource(v.release_source) : ''].filter(Boolean).join(' · ');
    }

    function detailCell(label, value) {
        if (value == null || value === '') return '';
        return '<div class="vd-detail-row"><span class="vd-detail-k">' + esc(label) +
            '</span><span class="vd-detail-v">' + esc(value) + '</span></div>';
    }
    function renderDetails(d) {
        // Released / runtime / studio / status used to sit in their own grid at
        // the top of the body, and budget / language / country in a second
        // "Details" block further down. They are one About section now
        // (renderFacts), so the page says each fact once, in one place.
        var host = q('[data-vd-details]');
        if (host) host.innerHTML = '';
    }    // ── per-title acquisition history (arr-parity P9) ─────────────────────────
    // Every grab/import/upgrade/failure this title has ever had, from the
    // permanent archive. Library-source pages only (the id keys the lookup).
    var _HIST_OUTCOME = {
        completed: ['Imported', 'vd-hist-chip--ok'],
        import_failed: ['Needs import', 'vd-hist-chip--warn'],
        failed: ['Failed', 'vd-hist-chip--bad'],
        cancelled: ['Cancelled', 'vd-hist-chip--mut'],
    };
    // The six states, in the order you'd read them: what you have, what is
    // being fetched, what is stuck, what nothing is chasing. queued/downloading
    // are a SUBSET of wanted (a wished episode mid-grab is still wished), so
    // they sit inside the wanted chip's own line rather than beside it.
    var _ACQ_STATES = [
        ['owned', 'Owned', 'vd-acq-chip--ok'],
        ['wanted', 'Wanted', 'vd-acq-chip--want'],
        ['queued', 'Queued', 'vd-acq-chip--live'],
        ['downloading', 'Downloading', 'vd-acq-chip--live'],
        ['failed', 'Failed', 'vd-acq-chip--bad'],
        ['ignored', 'Ignored', 'vd-acq-chip--mut'],
    ];
    function acqPanelHtml(state) {
        var c = (state && state.counts) || {};
        var total = Number(state && state.total) || 0;
        var owned = Number(c.owned) || 0;
        var wanted = Number(c.wanted) || 0;
        var live = (Number(c.queued) || 0) + (Number(c.downloading) || 0);
        var failed = Number(c.failed) || 0;
        var ignored = Number(c.ignored) || 0;
        var outstanding = total > 0 ? Math.max(0, total - owned) : 0;
        if (!failed && !live && !ignored && !outstanding) return '';
        var shown = _ACQ_STATES.filter(function (s) {
            if (s[0] === 'owned' && !owned) return false;
            if (s[0] === 'wanted' && !outstanding && wanted <= owned) return false;
            return (Number(c[s[0]]) || 0) > 0;
        });
        if (!shown.length) return '';
        var pctOwned = total > 0 ? Math.round(owned / total * 100) : 0;
        var headline = failed ? (failed + ' stuck')
            : live ? (live + ' moving')
            : outstanding ? (outstanding + ' missing')
            : ignored ? (ignored + ' ignored')
            : 'Needs attention';
        var sub = total > 0 ? (owned + ' of ' + total + ' in library') : 'Tracked by acquisition';
        var bar = total > 0 && owned < total
            ? '<div class="vd-acq-bar" title="' + owned + ' of ' + total + ' in library">' +
                '<span class="vd-acq-bar-fill" style="width:' + pctOwned + '%"></span></div>'
            : '';
        return '<div class="vd-acq-card"><div class="vd-acq-summary">' +
            '<span class="vd-acq-eyebrow">Needs attention</span>' +
            '<strong>' + esc(headline) + '</strong><span>' + esc(sub) + '</span></div>' +
            '<div class="vd-acq-chips">' + shown.map(function (s) {
                return '<span class="vd-acq-chip ' + s[2] + '">' +
                    '<span class="vd-acq-n">' + (Number(c[s[0]]) || 0) + '</span>' +
                    '<span class="vd-acq-k">' + esc(s[1]) + '</span></span>';
            }).join('') + '</div>' + bar +
            (live ? '<div class="vd-acq-note">Queued and downloading are part of wanted, not extra to it.</div>' : '') +
            '</div>';
    }
    function loadAcquisition(kind, id) {
        var section = q('[data-vd-acq-section]'), host = q('[data-vd-acq]');
        if (!section || !host) return;
        fetch('/api/video/detail/' + kind + '/' + id + '/acquisition',
              { headers: { 'Accept': 'application/json' } })
            .then(function (r) { return r.ok ? r.json() : null; })
            .then(function (d) {
                var html = (d && d.success) ? acqPanelHtml(d) : '';
                host.innerHTML = html;
                section.hidden = !html;
            })
            .catch(function () { section.hidden = true; });
    }

    function loadTitleHistory(kind, id) {
        var section = q('[data-vd-history-section]'), host = q('[data-vd-history]');
        if (!section || !host) return;
        fetch('/api/video/detail/' + kind + '/' + id + '/history', { headers: { 'Accept': 'application/json' } })
            .then(function (r) { return r.ok ? r.json() : null; })
            .then(function (d) {
                var rows = (d && d.history) || [];
                if (!rows.length) { section.hidden = true; return; }
                host.innerHTML = rows.map(function (h) {
                    var oc = _HIST_OUTCOME[h.outcome] || _HIST_OUTCOME.completed;
                    var se = (h.season_number != null && h.episode_number != null)
                        ? 'S' + String(h.season_number).padStart(2, '0') + 'E' + String(h.episode_number).padStart(2, '0') + ' · '
                        : '';
                    var bits = [h.quality_label, h.source,
                        h.size_bytes ? (Math.round(h.size_bytes / 1e8) / 10 + ' GB') : null,
                        (h.created_at || '').slice(0, 10)].filter(Boolean).join(' · ');
                    return '<div class="vd-hist-row">' +
                        '<span class="vd-hist-chip ' + oc[1] + '">' + oc[0] + '</span>' +
                        '<span class="vd-hist-main"><span class="vd-hist-rel">' + se +
                            esc(h.release_title || h.filename || '?') + '</span>' +
                        '<span class="vd-hist-sub">' + esc(bits) + '</span></span>' +
                        '</div>';
                }).join('');
                section.hidden = false;
            })
            .catch(function () { section.hidden = true; });
    }

    // ── live TMDB extras (trailer / where-to-watch / similar) ─────────────────
    function resetExtras() {
        var r0 = root(); if (r0) r0.classList.remove('vd-page--error');
        ['[data-vd-providers-section]', '[data-vd-similar-section]', '[data-vd-collection-section]',
         '[data-vd-next-ep]', '[data-vd-crew-line]', '[data-vd-season-overview]',
         '[data-vd-facts-section]', '[data-vd-videos-section]', '[data-vd-gallery-section]',
         '[data-vd-review-section]', '[data-vd-cast-all]', '[data-vd-history-section]', '[data-vd-health]',
         '[data-vd-acq-section]', '[data-vd-essentials]'].forEach(function (s) {
            var n = q(s); if (n) n.hidden = true;
        });
        // Clear any YouTube-channel playlists from the show DOM so they don't leak
        // onto the next movie/show you open (the section is reused across loads).
        ytResetPlaylists();
        // Same for the subtitles config section (Phase 3; video-subtitles.js).
        if (window.VideoSubtitles) window.VideoSubtitles.reset();
        galleryImages = [];
        stopBillboardTrailer();
        // the library tab belongs to the title it was opened for
        unmountLibraryTab();
    }

    // ── tabs: the page as it was, and Your library ───────────────────────────
    // A library movie/show gets two tabs, like an artist's Discography / Your
    // library on the music side. The second is a React island
    // (webui/src/features/video-library) mounted into [data-vd-library-panel];
    // the subtitle-language and acquisition-history sections are management
    // too, so they move into it (.vd-lib-extra, CSS). The tab you last used is
    // remembered per kind.
    var TAB_KEY = 'soulsync.vdTab.';
    var _libMounted = null;
    function tabbable() {
        return !!(data && currentSource === 'library' && data.source !== 'youtube' &&
                  (data.kind === 'movie' || data.kind === 'show') && data.id != null);
    }
    function savedTab(kind) {
        try { return localStorage.getItem(TAB_KEY + kind) === 'library' ? 'library' : 'main'; }
        catch (e) { return 'main'; }
    }
    function renderTabs() {
        var bar = q('[data-vd-tabs]'), body = q('.vd-body');
        if (!bar || !body) return;
        var on = tabbable();
        bar.hidden = !on;
        body.classList.toggle('vd-body--tabbed', on);
        setTab(on ? savedTab(data.kind) : 'main', true);
    }
    function setTab(tab, quiet) {
        var body = q('.vd-body'), panel = q('[data-vd-library-panel]');
        if (!body || !panel) return;
        if (tab === 'library' && !tabbable()) tab = 'main';
        body.classList.toggle('vd-body--library', tab === 'library');
        var btns = body.querySelectorAll('[data-vd-tab]');
        for (var i = 0; i < btns.length; i++) {
            var sel = btns[i].getAttribute('data-vd-tab') === tab;
            btns[i].setAttribute('aria-selected', sel ? 'true' : 'false');
            btns[i].classList.toggle('vd-tab--active', sel);
        }
        panel.hidden = tab !== 'library';
        if (!quiet && data) { try { localStorage.setItem(TAB_KEY + data.kind, tab); } catch (e) { /* private mode */ } }
        if (tab === 'library') mountLibraryTab();
    }
    function mountLibraryTab() {
        var panel = q('[data-vd-library-panel]');
        if (!panel || !tabbable()) return;
        if (!window.VideoLibraryTab) {
            // main.tsx is a deferred module: on a cold deep link the page can get
            // here first. it announces itself when it lands.
            document.addEventListener('soulsync:video-library-tab-ready', mountLibraryTab, { once: true });
            return;
        }
        window.VideoLibraryTab.mount(panel, { kind: data.kind, id: data.id });
        _libMounted = panel;
    }
    function unmountLibraryTab() {
        if (_libMounted && window.VideoLibraryTab) window.VideoLibraryTab.unmount(_libMounted);
        _libMounted = null;
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
        var cb = window.VideoGet ? VideoGet.cardButton({ kind: simKind, tmdbId: s.tmdb_id,
            title: s.title, poster: s.poster, status: s.status, source: 'tmdb' }) : '';
        // Ownership arrives stamped from the engine; wishlist state hydrates
        // after render (VideoWishState) — owned wins and skips the wish check.
        var owned = s.library_id != null;
        var chip = owned ? '<span class="vd-sim-wishchip vd-sim-wishchip--owned">In Library</span>' : '';
        return '<a class="vd-sim-card" href="/video-detail/tmdb/' + simKind + '/' + s.tmdb_id +
            '" data-vd-sim="' + simKind + '" data-vd-sim-id="' + s.tmdb_id + '"' +
            (owned ? ' data-vd-sim-owned="1"' : '') + '>' + chip + cb +
            poster + '<span class="vd-sim-title">' + esc(s.title) + '</span>' + yr + '</a>';
    }
    function renderRow(sectionSel, hostSel, items) {
        var sec = q(sectionSel), host = q(hostSel);
        if (!sec || !host) return;
        if (!items || !items.length) { sec.hidden = true; return; }
        sec.hidden = false;
        host.innerHTML = items.map(simCard).join('');
        if (window.VideoWatchlist) VideoWatchlist.hydrate(host);
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
            // Streaming providers (#1042: make every icon interactive, not a dead
            // badge). Each icon links to a SEARCH on that provider's own site for
            // this title (providerSearchUrl). TMDB only gives ONE aggregate /watch
            // link per title, so any provider we don't have a search URL for falls
            // back to that page, or a JustWatch search when TMDB has no link either.
            // (Drop a provider matching your server tile, e.g. Plex.)
            var link = ex.providers_link || '';
            var jwSearch = 'https://www.justwatch.com/us/search?q=' +
                encodeURIComponent(String(data && data.title || '').trim());
            var aggHref = link || jwSearch;
            var aggVia = link ? 'TMDB' : 'JustWatch';   // fallback tooltip must match the href
            var srvName = (ex.server && ex.server.server || '').toLowerCase();
            var provs = dedupeProviders(ex.providers || []).filter(function (p) {
                return (p.name || '').toLowerCase() !== srvName;
            });
            if (provs.length) {
                html += provs.map(function (p) {
                    var img = p.logo ? '<img src="' + esc(p.logo) + '" alt="' + esc(p.name) + '" loading="lazy">'
                        : '<span class="vd-prov-ph">' + esc((p.name || '?').charAt(0)) + '</span>';
                    var direct = providerSearchUrl(p.name, data && data.title);
                    var href = direct || aggHref;   // per-service search, else TMDB/JustWatch aggregate
                    var tip = direct ? 'Search ' + esc(p.name) + ' for this title'
                                     : 'Watch on ' + esc(p.name) + ' (via ' + aggVia + ')';
                    return '<a class="vd-prov vd-prov--badge" href="' + esc(href) +
                        '" target="_blank" rel="noopener" title="' + tip + '">' + img +
                        '<span class="vd-prov-name">' + esc(p.name) + '</span></a>';
                }).join('');
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
                if (window.VideoWatchlist) VideoWatchlist.hydrate(ch);
            } else { cs.hidden = true; }
        }

        // "More Like This" — recommendations (better-curated), falling back to similar.
        var more = (ex.recommendations && ex.recommendations.length) ? ex.recommendations : ex.similar;
        renderRow('[data-vd-similar-section]', '[data-vd-similar]', more);

        data.cast_full = ex.cast_full || null;
        renderCastAll(data);
        renderFacts(ex.facts, ex.keywords, ex.studios);
        renderVideos(ex.videos);
        renderGallery(ex.gallery);
        renderReview(ex.review);
        refreshStrips();
        maybeAutoplayBillboard();
    }

    function renderReview(review) {
        var sec = q('[data-vd-review-section]'), host = q('[data-vd-review]');
        if (!sec || !host) return;
        // a one-liner ("very good movie 9.5/10") isn't a review worth a section
        if (!review || !review.content || String(review.content).trim().length < 80) { sec.hidden = true; return; }
        sec.hidden = false;
        var rating = review.rating ? '<span class="vd-review-rating">★ ' + review.rating + '/10</span>' : '';
        var date = review.created ? '<span class="vd-review-date">' + esc(review.created) + '</span>' : '';
        var long = review.content.length > 420;
        host.innerHTML = '<div class="vd-review-head">' +
            '<span class="vd-review-author">' + esc(review.author) + '</span>' + rating + date + '</div>' +
            '<p class="vd-review-body" data-vd-review-body>' + esc(review.content) + '</p>' +
            (long ? '<button class="vd-review-more" type="button" data-vd-review-more>Read more</button>' : '');
    }

    // TMDB lists a service's ad tier and its resold channels as their own
    // providers ("Netflix Standard with Ads", "HBO Max Amazon Channel"); one
    // tile per service is enough
    function dedupeProviders(list) {
        var names = {};
        list.forEach(function (p) { names[String(p.name || '').toLowerCase()] = true; });
        return list.filter(function (p) {
            var n = String(p.name || '').toLowerCase();
            // the ad tier, and the same service resold as an amazon / apple / roku channel
            var base = n.replace(/\s+(standard |basic )?with ads$/, '')
                .replace(/\s+(amazon|apple tv|roku premium) channel$/, '');
            return base === n || !names[base];
        });
    }

    // The languages you grab subtitles in come first, then the rest by name.
    // Sorted by code, English hid behind "+20 more" on most films.
    function subLangOrder(codes, mine) {
        var want = (mine || []).map(function (c) { return String(c).toLowerCase(); });
        function rank(c) {
            var lc = String(c).toLowerCase(), i = want.indexOf(lc);
            if (i < 0) i = want.indexOf(lc.split('-')[0]);
            return i < 0 ? want.length : i;
        }
        return codes.slice().sort(function (a, b) {
            return (rank(a) - rank(b)) || subLangLabel(a).localeCompare(subLangLabel(b));
        });
    }
    var SUBS_VISIBLE = 4, _factsArgs = null;
    function subLangsHTML(codes) {
        var names = codes.map(subLangLabel);
        if (names.length <= SUBS_VISIBLE) return esc(names.join(', '));
        return '<span class="vd-subs-lead">' + esc(names.slice(0, SUBS_VISIBLE).join(', ')) + '</span>' +
            '<span class="vd-subs-rest" hidden>, ' + esc(names.slice(SUBS_VISIBLE).join(', ')) + '</span> ' +
            '<button type="button" class="vd-subs-more" data-vd-subs-more aria-expanded="false">+' +
            (names.length - SUBS_VISIBLE) + ' more</button>';
    }

    // ── facts / keywords ──────────────────────────────────────────────────────
    var LANGS = { en: 'English', es: 'Spanish', fr: 'French', de: 'German', it: 'Italian',
        ja: 'Japanese', ko: 'Korean', zh: 'Chinese', hi: 'Hindi', ru: 'Russian', pt: 'Portuguese',
        sv: 'Swedish', da: 'Danish', nl: 'Dutch', no: 'Norwegian', fi: 'Finnish', pl: 'Polish',
        tr: 'Turkish', ar: 'Arabic', he: 'Hebrew', th: 'Thai', cs: 'Czech' };
    function langName(c) { return LANGS[c] || langLabel(c); }
    function fmtMoney(n) {
        if (n >= 1e9) return (n / 1e9).toFixed(1).replace(/\.0$/, '') + 'B';
        if (n >= 1e6) return (n / 1e6).toFixed(1).replace(/\.0$/, '') + 'M';
        if (n >= 1e3) return Math.round(n / 1e3) + 'K';
        return String(n);
    }
    function titleFacts(d) {
        var rows = [];
        if (!d || d.source === 'youtube') return rows;
        if (d.kind === 'movie') {
            if (d.release_date) rows.push(['Released', fmtDate(d.release_date)]);
            if (d.digital_release_date && d.digital_release_date !== d.release_date) rows.push(['Digital release', fmtDate(d.digital_release_date)]);
            if (d.runtime_minutes) rows.push(['Runtime', runtimeLabel(d.runtime_minutes)]);
            if (d.content_rating) rows.push(['Rated', d.content_rating]);
            if (d.studio) rows.push(['Studio', d.studio]);
            if (d.status && d.status !== 'Released') rows.push(['Status', statusLabel(d.status)]);
        } else {
            var first = String(d.first_air_date || '').slice(0, 4), last = String(d.last_air_date || '').slice(0, 4);
            var ongoing = /return|continu|production|planned/i.test(String(d.status || ''));
            if (first) rows.push(['Aired', first + (ongoing ? ' – present' : (last && last !== first ? ' – ' + last : ''))]);
            if (d.network) rows.push(['Network', d.network]);
            if (d.status) rows.push(['Status', statusLabel(d.status)]);
            if (d.runtime_minutes) rows.push(['Episode length', runtimeLabel(d.runtime_minutes)]);
            if (d.content_rating) rows.push(['Rated', d.content_rating]);
        }
        return rows;
    }

    // Keywords past the first few fold behind "+N more": fifteen chips was a
    // wall the eye skipped entirely.
    var KW_VISIBLE = 10;

    function renderFacts(facts, keywords, studios) {
        var sec = q('[data-vd-facts-section]'), host = q('[data-vd-facts]'), kwh = q('[data-vd-keywords]');
        facts = facts || {}; keywords = keywords || []; studios = studios || [];
        _factsArgs = [facts, keywords, studios];
        var rows = titleFacts(data);
        // the studio chips below name every studio; a Studio fact repeated the first
        if (studios.length) rows = rows.filter(function (r) { return r[0] !== 'Studio'; });
        if (facts.budget) rows.push(['Budget', '$' + fmtMoney(facts.budget)]);
        if (facts.revenue) rows.push(['Box office', '$' + fmtMoney(facts.revenue)]);
        if (facts.original_language) rows.push(['Language', langName(facts.original_language)]);
        if (facts.countries && facts.countries.length) rows.push(['Country', facts.countries.join(', ')]);
        // where subtitles EXIST (OpenSubtitles backfill) — one fact, not a chip wall
        var subLangs = subLangOrder((data && data.subtitle_langs) || [], (prefs && prefs.subtitle_langs) || ['en']);
        if (subLangs.length) rows.push(['Subtitles exist in', subLangs, 'subs']);
        if (host) {
            host.innerHTML = rows.length ? '<div class="vd-detail-grid">' + rows.map(function (r) {
                var v = r[2] === 'subs' ? subLangsHTML(r[1]) : esc(r[1]);
                return '<div class="vd-detail-row' + (r[2] === 'subs' ? ' vd-detail-row--subs' : '') + '"><span class="vd-detail-k">' + esc(r[0]) +
                    '</span><span class="vd-detail-v">' + v + '</span></div>';
            }).join('') + '</div>' : '';
        }
        // the fact renders before prefs arrive the first time; re-order once they do
        if (subLangs.length && !prefs) {
            var d0 = data;
            loadPrefs(function () { if (data === d0) renderFacts.apply(null, _factsArgs); });
        }
        // Studio chips → each opens its Studio page (a collection of films). Logo when
        // TMDB has one, else the name; a data-vd-studio hook drives the click.
        var sth = q('[data-vd-studios]');
        if (sth) {
            sth.innerHTML = studios.map(function (s) {
                // logo beside the name: at chip size a tall or photographic
                // logo alone was an unreadable thumbnail
                var inner = (s.logo
                    ? '<img class="vd-studio-logo" src="' + esc(s.logo) + '" alt="" loading="lazy" onerror="this.remove()">'
                    : '') + '<span class="vd-studio-name">' + esc(s.name) + '</span>';
                return '<button type="button" class="vd-studio-chip" data-vd-studio="' + s.tmdb_id +
                    '" title="' + esc(s.name) + '">' + inner + '</button>';
            }).join('');
        }
        if (kwh) {
            kwh.classList.remove('vd-keywords--all');
            kwh.innerHTML = keywords.map(kwChip).join('') +
                (keywords.length > KW_VISIBLE
                    ? '<button type="button" class="vd-kw vd-kw--more" data-vd-kw-more>+' + (keywords.length - KW_VISIBLE) + ' more</button>'
                    : '');
        }
        if (sec) sec.hidden = !(rows.length || keywords.length || studios.length);
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
    var bbMsgHandler = null, bbRevealTimer = null;
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
        bb.classList.remove('vd-billboard--restoring');
        var wrap = document.createElement('div');
        wrap.className = 'vd-bb-trailer'; wrap.setAttribute('data-vd-bb-trailer', '');
        // start=5 skips the rating card and studio logos most trailers open on
        // (the MPAA green band behind a hero title); the Trailer button plays it all
        wrap.innerHTML = '<iframe allow="autoplay; encrypted-media" frameborder="0" ' +
            'src="https://www.youtube.com/embed/' + encodeURIComponent(key) +
            '?autoplay=1&mute=1&controls=0&modestbranding=1&rel=0&playsinline=1&enablejsapi=1&start=5"></iframe>' +
            '<div class="vd-bb-tctrls"><button class="vd-bb-tbtn" type="button" data-vd-bb-mute aria-label="Unmute">🔇</button>' +
            '<button class="vd-bb-tbtn" type="button" data-vd-bb-stop aria-label="Stop">✕</button></div>';
        bb.appendChild(wrap);
        var iframe = wrap.querySelector('iframe');
        // Ask YouTube to report playback state, then reveal ONLY once it's truly
        // PLAYING (state 1) — so the wipe doesn't fire over a black/buffering frame
        // — and restore the backdrop when it ENDS (state 0).
        function handshake() {
            try {
                iframe.contentWindow.postMessage(
                    '{"event":"listening","id":"vd-bb","channel":"widget"}', '*');
            } catch (e) { /* cross-origin not ready yet */ }
        }
        iframe.addEventListener('load', function () { handshake(); setTimeout(handshake, 500); });
        bbMsgHandler = function (e) {
            if (!iframe.contentWindow || e.source !== iframe.contentWindow) return;
            var msg; try { msg = JSON.parse(e.data); } catch (x) { return; }
            var st = null;
            if (msg && msg.event === 'infoDelivery' && msg.info && typeof msg.info.playerState === 'number') st = msg.info.playerState;
            else if (msg && msg.event === 'onStateChange' && typeof msg.info === 'number') st = msg.info;
            if (st === 1) revealBillboardTrailer(bb);
            else if (st === 0) restoreBillboard(bb);
        };
        window.addEventListener('message', bbMsgHandler);
        // Safety net: if YouTube never reports PLAYING (blocked handshake), reveal
        // anyway so a playing-but-hidden trailer doesn't sit behind the backdrop.
        bbRevealTimer = setTimeout(function () { revealBillboardTrailer(bb); }, 4500);
    }
    function revealBillboardTrailer(bb) {
        clearTimeout(bbRevealTimer); bbRevealTimer = null;
        bb.classList.remove('vd-billboard--restoring');
        bb.classList.add('vd-billboard--trailer');
    }
    function restoreBillboard(bb) {
        // Trailer finished → fade the original backdrop back in, then tear down.
        bb.classList.remove('vd-billboard--trailer');
        bb.classList.add('vd-billboard--restoring');
        setTimeout(function () {
            if (bb.classList.contains('vd-billboard--restoring')) stopBillboardTrailer();
        }, 950);
    }
    function stopBillboardTrailer() {
        clearTimeout(bbTrailerTimer); bbTrailerTimer = null;
        clearTimeout(bbRevealTimer); bbRevealTimer = null;
        if (bbMsgHandler) { window.removeEventListener('message', bbMsgHandler); bbMsgHandler = null; }
        var ws = document.querySelectorAll('[data-vd-bb-trailer]');
        for (var i = 0; i < ws.length; i++) ws[i].remove();
        var bbs = document.querySelectorAll('.vd-billboard--trailer, .vd-billboard--restoring');
        for (var j = 0; j < bbs.length; j++) bbs[j].classList.remove('vd-billboard--trailer', 'vd-billboard--restoring');
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
        // playlists are flat; channels have one switch (Grid/List) and year pills
        var yt = !!(data && data.source === 'youtube');
        host.hidden = yt;
        if (yt) { host.innerHTML = ''; return; }
        host.innerHTML = VIEWS.map(function (v) {
            return '<button class="vd-vt-btn' + (v.id === seasonView ? ' vd-vt-btn--active' : '') +
                '" type="button" data-vd-view="' + v.id + '" title="' + v.label + '">' +
                '<span class="vd-vt-ic">' + v.ic + '</span></button>';
        }).join('');
    }

    function renderSeasonNav() {
        var host = q('[data-vd-season-nav]');
        if (!host || !data || !data.seasons.length) { if (host) host.innerHTML = ''; return; }
        if (data.kind === 'playlist') { host.innerHTML = ''; return; }   // flat list — no season nav
        if (data.source === 'youtube') {   // channels: search + sort controls above the
            // year pills. Flat mode (search / most-viewed / longest) hides them.
            host.className = 'vd-season-nav vd-season-nav--yt vd-season-nav--pills';
            var nav = ytFlatMode() ? '' : pillsHTML();
            host.innerHTML = ytControlsHTML() + nav;
            return;
        }
        host.className = 'vd-season-nav vd-season-nav--' + seasonView;
        if (seasonView === 'rail') host.innerHTML = railHTML();
        else if (seasonView === 'timeline') host.innerHTML = timelineHTML();
        else if (seasonView === 'pills') host.innerHTML = pillsHTML();
        else host.innerHTML = dropdownHTML();
        // Keep the selected season (which now defaults to the LATEST) visible in the
        // horizontal nav — otherwise a show with many seasons opens scrolled to S1 with the
        // active card off-screen. block:'nearest' avoids yanking the page vertically.
        // Scrolls the STRIP only. scrollIntoView({block:'nearest'}) also scrolled
        // the page whenever the rail sat below the fold, so a show opened
        // already scrolled past its own title.
        requestAnimationFrame(function () { centerInStrip(host); refreshStrips(); });
    }

    // ── side-scrolling rows (cast, more like this, videos, photos, seasons) ──
    // They were bare horizontal scrollers: the row just ran off the right edge
    // with no sign there was more. Each gets a wrapper with edge fades and
    // prev / next buttons that show only when there is somewhere to go, and
    // move a page at a time. Touch keeps its native swipe (the buttons hide).
    var STRIPS = ['[data-vd-cast]', '[data-vd-similar]', '[data-vd-collection]', '[data-vd-videos]',
        '[data-vd-gallery]', '[data-vd-season-nav] .vd-rail'];
    function stripState(wrap, strip) {
        var max = strip.scrollWidth - strip.clientWidth;
        wrap.classList.toggle('vd-strip--start', strip.scrollLeft > 4);
        wrap.classList.toggle('vd-strip--end', strip.scrollLeft < max - 4);
    }
    function enhanceStrip(strip) {
        if (!strip) return;
        var wrap = strip.parentNode;
        if (!wrap.classList || !wrap.classList.contains('vd-strip')) {
            wrap = document.createElement('div');
            wrap.className = 'vd-strip';
            strip.parentNode.insertBefore(wrap, strip);
            wrap.appendChild(strip);
            ['prev', 'next'].forEach(function (dir) {
                var b = document.createElement('button');
                b.type = 'button';
                b.className = 'vd-strip-btn vd-strip-btn--' + dir;
                b.setAttribute('data-vd-strip', dir);
                b.setAttribute('aria-label', dir === 'prev' ? 'Scroll back' : 'Scroll forward');
                b.tabIndex = -1;
                b.textContent = dir === 'prev' ? '\u2039' : '\u203A';
                wrap.appendChild(b);
            });
        }
        if (!strip._vdStrip) {
            strip._vdStrip = true;
            strip.addEventListener('scroll', function () { stripState(wrap, strip); }, { passive: true });
        }
        stripState(wrap, strip);
    }
    function refreshStrips() {
        var r = root(); if (!r) return;
        STRIPS.forEach(function (sel) {
            var el = r.querySelector(sel);
            if (el) enhanceStrip(el);
        });
        // images that load late change the width; one more pass after they settle
        clearTimeout(refreshStrips._t);
        refreshStrips._t = setTimeout(function () {
            STRIPS.forEach(function (sel) {
                var el = root() && root().querySelector(sel);
                if (el && el.parentNode.classList.contains('vd-strip')) stripState(el.parentNode, el);
            });
        }, 600);
    }
    function scrollStrip(btn) {
        var wrap = btn.closest('.vd-strip');
        var strip = wrap && wrap.querySelector(':scope > :not(.vd-strip-btn)');
        if (!strip) return;
        var step = Math.max(200, strip.clientWidth * 0.85);
        strip.scrollBy({ left: btn.getAttribute('data-vd-strip') === 'prev' ? -step : step, behavior: 'smooth' });
    }

    function centerInStrip(host) {
        var active = host && host.querySelector('.vd-rcard--active, .vd-tseg--active, .vd-pill-btn--active');
        if (!active) return;
        var strip = active.parentNode;
        while (strip && strip !== host && strip.scrollWidth <= strip.clientWidth) strip = strip.parentNode;
        if (!strip || strip.scrollWidth <= strip.clientWidth) return;
        var a = active.getBoundingClientRect(), s = strip.getBoundingClientRect();
        strip.scrollLeft = Math.max(0, strip.scrollLeft + (a.left - s.left) - (s.width - a.width) / 2);
    }

    function railHTML() {
        return '<div class="vd-rail">' + data.seasons.map(function (s) {
            var art = seasonArt(s), p = pct(s);
            var on = s.season_number === selectedSeason ? ' vd-rcard--active' : '';
            // YouTube posters carry a lower-res fallback (maxres → original) so a
            // missing maxresdefault.jpg downgrades instead of vanishing.
            var fb = (s.poster_fallback && s.poster_fallback !== art) ? s.poster_fallback : '';
            var oe = fb
                ? 'var f=this.getAttribute(\'data-fb\');if(f){this.removeAttribute(\'data-fb\');this.src=f;}else{this.style.display=\'none\';}'
                : 'this.style.display=\'none\'';
            var img = art ? '<img class="vd-rcard-img" src="' + sizedArt(art, 342) + '" alt="" loading="lazy"' +
                (fb ? ' data-fb="' + esc(fb) + '"' : '') + ' onerror="' + oe + '">' : '';
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
    // A YouTube video as an "episode": still + title + date, a Wish toggle instead
    // of owned/missing, expand → full description (lazy).
    function ytEpisodeRow(ep) {
        var key = selectedSeason + '_' + ep.episode_number;
        var still = ep.still_url
            ? '<img class="vd-ep-still" src="' + esc(ep.still_url) + '" alt="" loading="lazy" onerror="this.style.display=\'none\'">'
            : '';
        var dur = ep.yt_duration ? '<span class="vd-ep-dur">' + esc(ep.yt_duration) + '</span>' : '';
        var meta = [];
        var yc0 = window.VideoYoutube;
        if (ep.view_count) { meta.push((yc0 ? yc0.compactCount(ep.view_count) : ep.view_count) + ' views'); }
        if (ep.like_count) { meta.push('👍 ' + (yc0 ? yc0.compactCount(ep.like_count) : ep.like_count)); }
        if (ep.dislike_count) { meta.push('👎 ' + (yc0 ? yc0.compactCount(ep.dislike_count) : ep.dislike_count)); }
        if (ep.air_date) meta.push(fmtDate(ep.air_date));
        // the id the downloader keys on, and the only way to check a video by hand
        // when a grab fails. data-vd-ext lets the root click handler pass it through.
        var vidChip = ep.youtube_id
            ? '<a class="vd-ep-vid" data-vd-ext target="_blank" rel="noopener" ' +
                'href="https://www.youtube.com/watch?v=' + encodeURIComponent(ep.youtube_id) + '" ' +
                'title="Open on YouTube">YouTube</a>'
            : '';
        var wished = !!ep.wished;
        // A profile without download rights can't grab or wishlist — it can
        // ASK for the video instead (the request flow). Owned videos keep
        // their treatment: nothing left to ask for.
        var noDl = (typeof canDownload === 'function') && !canDownload() && window.VideoRequests;
        var ch = (data && data._channel) || {};
        var reqBtn = (!ep.owned && noDl)
            ? '<div class="vd-ep-get">' + window.VideoRequests.cardButton({
                kind: 'youtube', youtubeId: ep.youtube_id, title: ep.title,
                channel: { youtube_id: ch.youtube_id || data.youtube_id || '',
                           title: ch.title || data.title || '' } }) + '</div>'
            : '';
        // Downloaded videos wear the SAME owned treatment as TV episodes (.vd-ep--owned
        // + badge) but KEEP the direct-download button: a server-side delete leaves the
        // ownership ledger intact, and re-grabbing is the sanctioned way back (Boulder).
        return '<div class="vd-ep vd-ep--yt' + (ep.owned ? ' vd-ep--owned' : '') +
            '" data-vd-ep-key="' + key + '" data-vd-yt-vid="' + esc(ep.youtube_id) + '">' +
            '<div class="vd-ep-thumb vd-ep-thumb--play" data-vd-yt-play="' + esc(ep.youtube_id) + '" title="Play video">' +
            still + '<span class="vd-ep-thumb-ic">▶</span>' + dur + '</div>' +
            '<div class="vd-ep-info"><div class="vd-ep-top"><span class="vd-ep-title">' +
            esc(ep.title || 'Untitled') + '</span>' +
            (meta.length ? '<span class="vd-ep-rt">' + esc(meta.join(' · ')) + '</span>' : '') +
            vidChip + '</div>' +
            (ep.overview ? '<p class="vd-ep-desc">' + esc(ep.overview) + '</p>' : '') + '</div>' +
            (ep.owned ? '<div class="vd-ep-get" data-vd-ep-get="' + esc(ep.youtube_id) + '">' +
                            '<span class="vd-ep-dl" data-vd-ep-dl></span>' +
                            '<div class="vd-ep-badge">Downloaded</div>' +
                            '<button class="vd-ep-getbtn vd-ep-grab" type="button" data-vd-yt-grab="' + esc(ep.youtube_id) +
                                '" title="Download again (e.g. after deleting it on the server)" aria-label="Download again">⭳</button>' +
                        '</div>'
                      : '<div class="vd-ep-get" data-vd-ep-get="' + esc(ep.youtube_id) + '">' +
                            '<span class="vd-ep-dl" data-vd-ep-dl></span>' +
                            (reqBtn ||
                            '<button class="vd-ep-getbtn vd-ep-grab" type="button" data-vd-yt-grab="' + esc(ep.youtube_id) +
                                '" title="Download this video now" aria-label="Download video">⭳</button>' +
                            '<button class="vd-ep-getbtn vd-ep-wish' + (wished ? ' vd-ep-wish--done' : '') +
                                '" type="button" data-vd-yt-wish="' + esc(ep.youtube_id) +
                                '" title="' + (wished ? 'Remove from wishlist' : 'Add this video to the wishlist') +
                                '" aria-label="Wishlist video">' + (wished ? '✓' : '＋') + '</button>') +
                        '</div>') +
            '<span class="vd-ep-chev" aria-hidden="true">⌄</span></div>' +
            '<div class="vd-ep-extra" data-vd-ep-panel="' + key + '" hidden></div>';
    }

    // ── channel videos as a grid (the default) ──
    // A channel is browsed like YouTube's own Videos tab: a grid of thumbnails,
    // title, views and date, and what you have. The wide TV-episode rows are
    // still one click away (List). The card keeps every hook the row has
    // (data-vd-ep-get / data-vd-ep-dl for live download status, data-vd-yt-grab,
    // data-vd-yt-wish, data-vd-yt-play), so grabs and progress behave the same.
    var YT_LAYOUT_KEY = 'soulsync.vdYtLayout';
    function ytLayout() {
        try { return localStorage.getItem(YT_LAYOUT_KEY) === 'list' ? 'list' : 'grid'; } catch (e) { return 'grid'; }
    }
    function setYtLayout(v) {
        try { localStorage.setItem(YT_LAYOUT_KEY, v === 'list' ? 'list' : 'grid'); } catch (e) { /* private mode */ }
        renderSeasonNav(); renderEpisodes();
    }
    function ytVideoCard(ep) {
        var id = ep.youtube_id || '';
        var yc0 = window.VideoYoutube;
        var meta = [];
        if (ep.view_count) meta.push((yc0 ? yc0.compactCount(ep.view_count) : ep.view_count) + ' views');
        if (ep.air_date) meta.push(fmtDate(ep.air_date));
        var likes = [ep.like_count ? (yc0 ? yc0.compactCount(ep.like_count) : ep.like_count) + ' likes' : '',
            ep.dislike_count ? (yc0 ? yc0.compactCount(ep.dislike_count) : ep.dislike_count) + ' dislikes' : '']
            .filter(Boolean).join(' · ');
        var still = ep.still_url
            ? '<img class="vd-ytc-img" src="' + esc(ep.still_url) + '" alt="" loading="lazy" onerror="this.style.display=\'none\'">'
            : '';
        var state = ep.owned ? '<span class="vd-ytc-state vd-ytc-state--owned">✓ Downloaded</span>'
            : (ep.wished ? '<span class="vd-ytc-state vd-ytc-state--wished">In wishlist</span>' : '');
        var noDl = (typeof canDownload === 'function') && !canDownload() && window.VideoRequests;
        var ch = (data && data._channel) || {};
        var actions;
        if (!ep.owned && noDl) {
            actions = window.VideoRequests.cardButton({ kind: 'youtube', youtubeId: id, title: ep.title,
                channel: { youtube_id: ch.youtube_id || data.youtube_id || '', title: ch.title || data.title || '' } });
        } else {
            actions = (!ep.owned
                    ? '<button class="vd-ep-getbtn vd-ep-grab vd-ytc-get" type="button" data-vd-yt-grab="' + esc(id) +
                        '" title="Download this video now" aria-label="Download video">⭳</button>'
                    : '') +
                '<span class="vd-pop">' +
                '<button class="vd-ep-getbtn vd-pop-btn" type="button" data-vd-pop aria-haspopup="menu" aria-expanded="false" ' +
                    'aria-label="More for this video" title="More">⋯</button>' +
                '<div class="vd-pop-menu" data-vd-pop-menu role="menu" hidden>' +
                (ep.owned
                    ? '<button class="vd-manage-btn vd-ep-grab" type="button" role="menuitem" data-vd-yt-grab="' + esc(id) +
                        '" title="Download again (e.g. after deleting it on the server)">⭳ Download again</button>'
                    : '<button class="vd-manage-btn vd-ytc-wish' + (ep.wished ? ' watching' : '') + '" type="button" role="menuitem" ' +
                        'data-vd-yt-wish="' + esc(id) + '"><span class="watchlist-icon">' + (ep.wished ? '✓' : '＋') + '</span> ' +
                        '<span class="watchlist-text">' + (ep.wished ? 'In Wishlist' : 'Wishlist') + '</span></button>') +
                '<a class="vd-manage-btn" role="menuitem" data-vd-ext target="_blank" rel="noopener" ' +
                    'href="https://www.youtube.com/watch?v=' + encodeURIComponent(id) + '">↗ Open on YouTube</a>' +
                '</div></span>';
        }
        return '<article class="vd-ytc' + (ep.owned ? ' vd-ytc--owned' : '') + (ep.wished ? ' vd-ytc--wished' : '') +
                '" data-vd-ytc="' + esc(id) + '">' +
            '<button class="vd-ytc-thumb" type="button" data-vd-yt-play="' + esc(id) + '" aria-label="Play ' + esc(ep.title || 'video') + '">' +
                still + '<span class="vd-ytc-play" aria-hidden="true">▶</span>' +
                (ep.yt_duration ? '<span class="vd-ep-dur">' + esc(ep.yt_duration) + '</span>' : '') + state +
            '</button>' +
            '<div class="vd-ytc-body">' +
                '<div class="vd-ytc-text">' +
                    '<div class="vd-ytc-title" title="' + esc(ep.title || '') + '">' + esc(ep.title || 'Untitled') + '</div>' +
                    '<div class="vd-ytc-meta"' + (likes ? ' title="' + esc(likes) + '"' : '') + '>' + esc(meta.join(' · ')) + '</div>' +
                '</div>' +
                '<div class="vd-ep-get vd-ytc-actions" data-vd-ep-get="' + esc(id) + '">' +
                    '<span class="vd-ep-dl" data-vd-ep-dl></span>' + actions +
                '</div>' +
            '</div>' +
        '</article>';
    }

    // The per-video wishlist toggle — the app-standard watchlist-button chrome
    // (icon + text, accent gradient) so it stops looking bespoke. iconOnly trims
    // the label for the tight playlist-section cards.
    function ytWishBtn(id, wished, iconOnly) {
        return '<button class="library-artist-watchlist-btn vd-yt-wishbtn' +
            (iconOnly ? ' vd-yt-wishbtn--icon' : '') + (wished ? ' watching' : '') +
            '" type="button" data-vd-yt-wish="' + esc(id) + '">' +
            '<span class="watchlist-icon">' + (wished ? '✓' : '＋') + '</span>' +
            (iconOnly ? '' : '<span class="watchlist-text">' + (wished ? 'In Wishlist' : 'Wishlist') + '</span>') +
            '</button>';
    }

    function episodeRow(ep) {
        if (data && data.source === 'youtube') return ytEpisodeRow(ep);
        var owned = ep.owned ? 'vd-ep--owned' : 'vd-ep--missing';
        // Continue Watching: watched check / in-progress bar / next-up highlight
        // (all server truth from the scan; a TMDB preview has none of it).
        var inProgress = !ep.watched && (ep.view_offset_ms || 0) > 0 && ep.runtime_minutes;
        var progPct = inProgress
            ? Math.max(2, Math.min(98, Math.round(ep.view_offset_ms / (ep.runtime_minutes * 60000) * 100)))
            : 0;
        var nu = data && data.next_up;
        var isNext = !!(nu && nu.season_number === selectedSeason &&
                        nu.episode_number === ep.episode_number);
        if (ep.watched) owned += ' vd-ep--watched';
        if (isNext) owned += ' vd-ep--next';
        var meta = [];
        var rt = runtimeLabel(ep.runtime_minutes); if (rt) meta.push(rt);
        if (ep.air_date) meta.push(ep.air_date);
        if (ep.owned && ep.resolution) meta.push(mediaRes(ep.resolution));
        var stillSrc = (data && data.source === 'tmdb')
            ? (ep.still_url || '')
            : (ep.has_still ? '/api/video/poster/episode/' + ep.id : '');
        var still = stillSrc
            ? '<img class="vd-ep-still" src="' + sizedArt(stillSrc, 342) + '" alt="" loading="lazy" onerror="this.style.display=\'none\'">'
            : '';
        if (ep.rating) meta.push('★ ' + (Math.round(ep.rating * 10) / 10));
        var key = selectedSeason + '_' + ep.episode_number;
        // Row + a sibling expand panel (guest stars etc. load lazily on open).
        var prog = inProgress
            ? '<span class="vd-ep-prog"><span class="vd-ep-prog-fill" style="width:' + progPct + '%"></span></span>'
            : '';
        var check = ep.watched ? '<span class="vd-ep-check" title="Watched">✓</span>' : '';
        var nextChip = isNext
            ? '<span class="vd-ep-next-chip">' + (inProgress ? 'Resume' : 'Next up') + '</span>'
            : '';
        // NEW = landed on the server in the last 7 days and not watched yet.
        var addedTs = ep.owned && !ep.watched && ep.added_at
            ? Date.parse(String(ep.added_at).replace(' ', 'T'))   // Safari chokes on the space form
            : NaN;
        var newChip = (addedTs && !isNaN(addedTs) && (Date.now() - addedTs) < 7 * 86400000)
            ? '<span class="vd-ep-new-chip" title="Recently added">New</span>'
            : '';
        return '<div class="vd-ep ' + owned + '" data-vd-ep-key="' + key + '">' +
            '<div class="vd-ep-index">' + (ep.episode_number != null ? ep.episode_number : '') + '</div>' +
            '<div class="vd-ep-thumb">' + still + '<span class="vd-ep-thumb-ic">▶</span>' + prog + '</div>' +
            '<div class="vd-ep-info"><div class="vd-ep-top">' + check + nextChip + newChip + '<span class="vd-ep-title">' +
            esc(ep.title || 'Episode ' + ep.episode_number) + '</span>' +
            (meta.length ? '<span class="vd-ep-rt">' + esc(meta.join(' · ')) + '</span>' : '') + '</div>' +
            (ep.overview ? '<p class="vd-ep-desc">' + esc(ep.overview) + '</p>' : '') + '</div>' +
            // Owned episodes keep the badge AND the actions: the acquisition
            // stack treats owned rows as upgrade candidates (upgrade-until-
            // cutoff), so re-download / manual search / wishlist must not
            // vanish once something is on disk.
            // A profile without download rights can't grab or wishlist — on a
            // TMDB preview or a library show it can ASK for a missing episode
            // instead (the request flow); owned episodes have nothing to ask.
            // Owned is the normal state, so it no longer wears a badge on every
            // row; only a gap does (Missing / Upcoming). Owned rows keep every
            // acquisition action (upgrade-until-cutoff treats them as upgrade
            // candidates), behind one ⋯ instead of four icon buttons.
            (epBadge(ep) +
             ((typeof canDownload === 'function' && !canDownload())
                ? ((!ep.owned && data && data.tmdb_id && window.VideoRequests)
                    ? '<div class="vd-ep-get">' + window.VideoRequests.cardButton({
                        kind: 'episode', tmdbId: data.tmdb_id, season: selectedSeason,
                        episode: ep.episode_number, title: data.title, year: data.year,
                        poster: data.poster_url }) + '</div>'
                    // watching isn't downloading: a view-only profile still marks it
                    : (epWatchedItem(ep) ? '<div class="vd-ep-get">' + epPop(ep, epWatchedItem(ep)) + '</div>' : ''))
                : '<div class="vd-ep-get" data-vd-ep-get="' + ep.episode_number + '">' +
                    '<span class="vd-ep-dl" data-vd-ep-dl></span>' +
                    (!ep.owned && epAired(ep)
                        ? '<button class="vd-ep-getbtn vd-ep-grab" type="button" data-vd-ep-grab="' + ep.episode_number +
                            '" title="Auto-search &amp; download this episode" aria-label="Get episode">⭳</button>'
                        : '') +
                    '<span class="vd-pop">' +
                    '<button class="vd-ep-getbtn vd-pop-btn" type="button" data-vd-pop aria-haspopup="menu" aria-expanded="false" ' +
                        'aria-label="More for episode ' + ep.episode_number + '" title="More">⋯</button>' +
                    '<div class="vd-pop-menu" data-vd-pop-menu role="menu" hidden>' +
                    epWatchedItem(ep) +
                    (ep.owned
                        ? '<button class="vd-manage-btn vd-ep-grab" type="button" role="menuitem" data-vd-ep-grab="' + ep.episode_number +
                            '" title="Search &amp; download again (upgrade)">⭳ Download again (upgrade)</button>'
                        : '') +
                    '<button class="vd-manage-btn vd-ep-search" type="button" role="menuitem" data-vd-ep-search="' + ep.episode_number +
                        '" title="Manual search — pick a release">⌕ Manual search</button>' +
                    '<button class="vd-manage-btn vd-ep-wish" type="button" role="menuitem" data-vd-ep-wish="' + ep.episode_number +
                        '" title="' + (ep.owned ? 'Wishlist for an upgrade' : 'Add this episode to the wishlist') + '">＋ ' +
                        (ep.owned ? 'Wishlist for an upgrade' : 'Add to wishlist') + '</button>' +
                    // Subtitle search (Phase 3): Bazarr-style manual subtitle
                    // search for an owned episode (needs a library file to
                    // write the .srt next to).
                    // (data.source is unset on library payloads, so this keyed on
                    // currentSource or it never showed)
                    ((ep.id != null && ep.owned && data && currentSource === 'library')
                        ? '<button class="vd-manage-btn vd-ep-sub" type="button" role="menuitem" data-vd-ep-subsearch="' + ep.episode_number +
                            '" title="Search subtitles — pick one yourself">💬 Search subtitles</button>'
                        : '') +
                    '</div></span>' +
                  '</div>')) +
            '<span class="vd-ep-chev" aria-hidden="true">⌄</span></div>' +
            '<div class="vd-ep-extra" data-vd-ep-panel="' + key + '"' + episodeReportAttrs(ep) + ' hidden></div>';
    }

    // Mark one episode watched or unwatched (library rows you have: the server
    // can only mark what it holds). The show-wide toggle in More was the only one.
    function epWatchedItem(ep) {
        // library payloads carry no source field; currentSource is the truth
        if (!data || currentSource !== 'library' || ep.id == null || !ep.owned) return '';
        return '<button class="vd-manage-btn vd-ep-watch" type="button" role="menuitem" data-vd-watched-kind="episode" ' +
            'data-vd-watched-id="' + esc(ep.id) + '" data-vd-watched-to="' + (ep.watched ? '0' : '1') + '">' +
            (ep.watched ? '\u21BA Mark unwatched' : '\u2713 Mark watched') + '</button>';
    }
    function epPop(ep, items) {
        return '<span class="vd-pop">' +
            '<button class="vd-ep-getbtn vd-pop-btn" type="button" data-vd-pop aria-haspopup="menu" aria-expanded="false" ' +
                'aria-label="More for episode ' + ep.episode_number + '" title="More">⋯</button>' +
            '<div class="vd-pop-menu" data-vd-pop-menu role="menu" hidden>' + items + '</div></span>';
    }
    function setPartWatched(btn) {
        if (!data || btn.disabled) return;
        var kind = btn.getAttribute('data-vd-watched-kind'), id = btn.getAttribute('data-vd-watched-id');
        var target = btn.getAttribute('data-vd-watched-to') === '1', d0 = data;
        btn.disabled = true;
        fetch(DETAIL_URL + kind + '/' + encodeURIComponent(id) + '/watched', {
            method: 'POST', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ watched: target }) })
            .then(function (r) { return r.ok ? r.json() : null; })
            .then(function (res) {
                btn.disabled = false;
                if (!res || !res.ok) throw new Error();
                if (data !== d0) return;
                // mirror the backend so the rows and the hero count move at once
                var n = 0, total = 0;
                (data.seasons || []).forEach(function (s) {
                    (s.episodes || []).forEach(function (ep) {
                        var hit = kind === 'episode' ? String(ep.id) === String(id)
                            : (String(s.id) === String(id) && ep.owned);
                        if (hit) { ep.watched = target; ep.view_offset_ms = 0; }
                        if (ep.watched) n++;
                        total++;
                    });
                });
                data.watched_episodes = n;
                data.watched = total > 0 && n >= total;
                closePops();
                renderBillboard(data);
                renderEpisodes();
                toast((target ? 'Marked watched' : 'Marked unwatched') + (res.pushed ? ' \u2014 synced to your server' : ''), 'success');
            })
            .catch(function () { btn.disabled = false; toast('Could not update watched state', 'error'); });
    }

    function epAired(ep) {
        return !ep.air_date || String(ep.air_date).slice(0, 10) <= new Date().toISOString().slice(0, 10);
    }
    function epBadge(ep) {
        // an owned row still emits the cell (empty), or its actions would sit one
        // grid column left of a missing row's and the action column would jump
        if (ep.owned) return ep.versions > 1 ? '<div class="vd-ep-badge vd-ep-badge--versions">×' + ep.versions + '</div>'
            : '<div class="vd-ep-badge vd-ep-badge--none" aria-hidden="true"></div>';
        return epAired(ep) ? '<div class="vd-ep-badge">Missing</div>'
            : '<div class="vd-ep-badge vd-ep-badge--soon">Upcoming</div>';
    }

    // library episodes can be reported (video-issues.js owns the modal); the
    // panel carries the id so the expanded view can offer it
    function episodeReportAttrs(ep) {
        if (!window.VideoIssues || !data || data.source === 'tmdb' || ep.id == null) return '';
        var name = (data.title || '') + ' S' + String(selectedSeason).padStart(2, '0') +
            'E' + String(ep.episode_number).padStart(2, '0');
        return ' data-vi-ep-id="' + esc(ep.id) + '" data-vi-ep-name="' + esc(name) + '"' +
            ' data-vi-ep-meta="' + esc(ep.title || '') + '"';
    }
    function episodeReportButton(panel) {
        var id = panel && panel.getAttribute('data-vi-ep-id');
        if (!id) return '';
        return '<button type="button" class="vi-ep-report" data-vi-report-episode="' + esc(id) + '"' +
            ' data-vi-name="' + esc(panel.getAttribute('data-vi-ep-name') || '') + '"' +
            ' data-vi-meta="' + esc(panel.getAttribute('data-vi-ep-meta') || '') + '">⚑ Report a problem</button>';
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
        // YouTube: the row carries the video id → fetch its full metadata.
        if (data && data.source === 'youtube') {
            var row = q('[data-vd-ep-key="' + key + '"]');
            var vid = row && row.getAttribute('data-vd-yt-vid');
            if (!vid) { panel.innerHTML = '<div class="vd-ep-extra-empty">No details.</div>'; return; }
            panel.innerHTML = '<div class="vd-ep-extra-empty">Loading…</div>';
            fetch('/api/video/youtube/video/' + encodeURIComponent(vid), { headers: { 'Accept': 'application/json' } })
                .then(function (r) { return r.ok ? r.json() : null; })
                .then(function (d) {
                    var v = (d && d.video) || {};
                    var yc = window.VideoYoutube, stats = [];
                    var lk = yc && yc.compactCount(v.like_count); if (lk) stats.push(lk + ' likes');
                    var vw = yc && yc.compactCount(v.view_count); if (vw) stats.push(vw + ' views');
                    var dearrow = v.dearrow_title
                        ? '<div class="vd-dearrow"><span class="vd-dearrow-tag">DeArrow</span>' +
                          esc(v.dearrow_title) + '</div>'
                        : '';
                    panel.innerHTML = '<div class="vd-ep-extra-body">' + dearrow +
                        (stats.length ? '<div class="vd-ep-extra-gh">' + esc(stats.join(' · ')) + '</div>' : '') +
                        '<p class="vd-ep-extra-ov">' + esc(v.description || 'No description.') + '</p></div>';
                })
                .catch(function () { panel.innerHTML = '<div class="vd-ep-extra-empty">No details.</div>'; });
            return;
        }
        var tmdb = data && data.tmdb_id;
        var parts = key.split('_');
        if (!tmdb) { panel.innerHTML = '<div class="vd-ep-extra-empty">No extra info.</div>' + episodeReportButton(panel); return; }
        panel.innerHTML = '<div class="vd-ep-extra-empty">Loading…</div>';
        fetch('/api/video/episode/' + tmdb + '/' + parts[0] + '/' + parts[1],
            { headers: { 'Accept': 'application/json' } })
            .then(function (r) { return r.ok ? r.json() : null; })
            .then(function (ex) { renderEpisodeExtra(panel, ex && !ex.error ? ex : {}, tmdb, parts[0], parts[1]); })
            .catch(function () { panel.innerHTML = ''; });
    }
    // External deep-links for one EPISODE (#1039). TMDB episode page is always
    // constructible from the show's tmdb id + season/episode; IMDb only when the
    // episode's own id is known. tmdb passthrough items keep everything in-app.
    function episodeLinks(showTmdb, season, episode, ex) {
        if (data && data.source === 'tmdb') return '';
        var links = [];
        // quiet text links in the panel's footer; a lone logo tile under the
        // guest stars read as a stray image
        function link(label, url) {
            return '<a class="vd-ep-link" href="' + esc(url) + '" target="_blank" rel="noopener noreferrer" ' +
                'title="' + label + ' episode">' + label + ' \u2197</a>';
        }
        if (showTmdb) {
            links.push(link('TMDB', 'https://www.themoviedb.org/tv/' + showTmdb + '/season/' + season + '/episode/' + episode));
        }
        if (ex && ex.imdb_id) {
            links.push(link('IMDb', 'https://www.imdb.com/title/' + ex.imdb_id + '/'));
        }
        return links.length
            ? '<div class="vd-ep-links">' + links.join('') + '</div>' : '';
    }
    // How many guest faces the expanded episode shows before folding the rest
    // away. One episode came back with fourteen, most of them a grey initial in a
    // circle, and they out-shouted the episode's own actions.
    var GUEST_VISIBLE = 8;

    function renderEpisodeExtra(panel, ex, showTmdb, season, episode) {
        // The row above already prints this description and TMDB hands back the
        // same string, so printing it again just doubled the panel's height.
        var owner = panel.previousElementSibling;
        var rowDesc = '';
        if (owner && owner.classList && owner.classList.contains('vd-ep')) {
            var dEl = owner.querySelector('.vd-ep-desc');
            rowDesc = dEl ? (dEl.textContent || '').trim() : '';
        }
        var body = '';
        if (ex.overview && ex.overview.trim() !== rowDesc) {
            body += '<p class="vd-ep-extra-ov">' + esc(ex.overview) + '</p>';
        }
        if (ex.guest_stars && ex.guest_stars.length) {
            body += '<div class="vd-ep-extra-gh">Guest stars</div><div class="vd-ep-guests">' +
                ex.guest_stars.map(function (g) {
                    var img = g.photo
                        ? '<img class="vd-guest-photo" src="' + esc(g.photo) + '" alt="" loading="lazy" onerror="this.style.visibility=\'hidden\'">'
                        : '<span class="vd-guest-photo vd-guest-photo--ph">' + esc((g.name || '?').charAt(0)) + '</span>';
                    var inner = img + '<span class="vd-guest-name">' + esc(g.name) + '</span>' +
                        (g.character ? '<span class="vd-guest-char">' + esc(g.character) + '</span>' : '');
                    return g.tmdb_id
                        ? '<a class="vd-guest" href="/video-detail/tmdb/person/' + g.tmdb_id + '" data-vd-person="' + g.tmdb_id + '">' + inner + '</a>'
                        : '<div class="vd-guest">' + inner + '</div>';
                }).join('') +
                (ex.guest_stars.length > GUEST_VISIBLE
                    ? '<button class="vd-guest-more" type="button" data-vd-guests-all>+' +
                      (ex.guest_stars.length - GUEST_VISIBLE) + ' more</button>'
                    : '') +
                '</div>';
        }
        var foot = episodeLinks(showTmdb, season, episode, ex) + episodeReportButton(panel);
        // The empty-state used to be unreachable: the old code built the wrapper
        // div into `html` first, so `html || fallback` could never take the
        // fallback and an episode with no extras opened into a blank box.
        panel.innerHTML =
            (ex.still_url
                ? '<img class="vd-ep-extra-still" src="' + esc(ex.still_url) + '" alt="" loading="lazy">'
                : '') +
            '<div class="vd-ep-extra-body">' +
                (body || '<div class="vd-ep-extra-empty">No extra info.</div>') +
                (foot ? '<div class="vd-ep-foot">' + foot + '</div>' : '') +
            '</div>';
    }

    function renderSeasonOverview() {
        var el = q('[data-vd-season-overview]');
        if (!el) return;
        var s = seasonByNum(selectedSeason);
        var ov = s && s.overview;
        el.textContent = ov || '';
        el.hidden = !ov;
    }

    // Season-level action bar. Acquisition (grab / manual search / wishlist)
    // only when something is missing; monitoring and stale-failure resets on any
    // library season, complete or not. Returns '' when there is nothing to offer.
    function seasonActionsHtml(season) {
        var isYt = !!(data && data.source === 'youtube');
        var seasonMissing = season.episodes.filter(function (e) { return !e.owned; });
        if (isYt && (ytFilter.q || ytFilter.state !== 'all' || ytFilter.duration !== 'all')) seasonMissing = [];   // a filtered view isn't "the season"
        // A profile without download rights gets no acquisition buttons here either —
        // the server 403s the grab/wishlist endpoints for it (same as the per-row buttons).
        var canDl = (typeof canDownload !== 'function') || canDownload();
        var canAcquire = !!(seasonMissing.length && canDl && (isYt || window.VideoGrab));
        // Monitoring and stale-failure resets matter on a COMPLETE season too, so a
        // library show always gets the bar. YouTube never does: it has no episode
        // rows to monitor, and a preview has no library row to act on at all.
        var seasonManage = !isYt && !!data && data.kind === 'show' && data.source !== 'tmdb';
        var monitored = (Number(season.episode_monitored) || 0) > 0;
        var ownedEps = season.episodes.filter(function (e) { return e.owned; });
        var seasonWatchedItem = (seasonManage && season.id != null && ownedEps.length)
            ? (function () {
                var all = ownedEps.every(function (e) { return e.watched; });
                return '<button class="discog-download-btn discog-btn-compact" type="button" role="menuitem" ' +
                    'data-vd-watched-kind="season" data-vd-watched-id="' + esc(season.id) + '" data-vd-watched-to="' + (all ? '0' : '1') + '">' +
                    '<span class="discog-btn-icon">' + (all ? '\u21BA' : '\u2713') + '</span>' +
                    '<span class="discog-btn-text">' + (all ? 'Mark season unwatched' : 'Mark season watched') + '</span></button>';
            })() : '';
        if (!canAcquire && !seasonManage) return '';
        // One visible action (Grab), the rest behind one menu. The bar used to be
        // five same-weight pills on every season.
        var scope = grabScope();
        var menu = seasonWatchedItem +
            (canAcquire && !isYt ?
            '<button class="discog-download-btn discog-btn-compact" type="button" role="menuitem" data-vd-season-search ' +
                'title="Manual search — pick releases for this season">' +
                '<span class="discog-btn-icon">⌕</span><span class="discog-btn-text">Manual search</span></button>' : '') +
            (canAcquire ?
            '<button class="discog-download-btn discog-btn-compact" type="button" role="menuitem" data-vd-season-wish ' +
                'title="Add every missing ' + (isYt ? 'video ' : 'episode ') + scope.what + ' to the wishlist">' +
                '<span class="discog-btn-icon">＋</span><span class="discog-btn-text">Wishlist ' + scope.word + '</span></button>' : '') +
            (seasonManage ?
            '<button class="discog-download-btn discog-btn-compact vd-season-mon' + (monitored ? ' vd-season-mon--on' : '') +
                '" type="button" role="menuitem" data-vd-season-monitor="' + (monitored ? '0' : '1') + '" ' +
                'title="' + (monitored ? 'Stop hunting this season' : 'Hunt missing episodes in this season again') + '">' +
                '<span class="discog-btn-icon">' + (monitored ? '◉' : '○') + '</span>' +
                '<span class="discog-btn-text">' + (monitored ? 'Monitored' : 'Unmonitored') + '</span></button>' : '') +
            (seasonManage && data.tmdb_id ?
            '<button class="discog-download-btn discog-btn-compact" type="button" role="menuitem" data-vd-season-clearfail ' +
                'title="Clear retry backoff on this season\u2019s stalled wishlist rows and search every source again">' +
                '<span class="discog-btn-icon">↺</span><span class="discog-btn-text">Clear failures</span></button>' : '');
        var status = canAcquire ? seasonMissing.length + ' missing'
            : (isYt ? '' : 'All ' + season.episodes.length + ' here');
        if (seasonManage && !monitored) status += (status ? ' · ' : '') + 'not monitored';
        return '<div class="vd-season-actions">' +
            '<span class="vd-season-actions-count">' + status + '</span>' +
            (canAcquire ?
            '<button class="discog-download-btn discog-btn-compact" type="button" data-vd-season-grab ' +
                'title="' + (isYt ? 'Download every missing video ' + scope.what
                                  : 'Auto-search &amp; download every missing episode in this season') + '">' +
                '<span class="discog-btn-icon">⭳</span><span class="discog-btn-text">Grab ' + scope.word + '</span>' +
                '<span class="discog-btn-shimmer"></span></button>' : '') +
            (menu ? '<span class="vd-pop">' +
                '<button class="vd-pop-btn" type="button" data-vd-pop aria-haspopup="menu" aria-expanded="false" ' +
                    'aria-label="More for this ' + (scope.word === 'all' ? 'playlist' : scope.word) + '" title="More">⋯</button>' +
                '<div class="vd-pop-menu" data-vd-pop-menu role="menu" hidden>' + menu + '</div></span>' : '') +
            '</div>';
    }

    function renderEpisodes() {
        renderSeasonOverview();
        var host = q('[data-vd-episodes]');
        if (!host) return;
        var season = seasonByNum(selectedSeason);
        if (!season) { host.innerHTML = ''; return; }
        var eps = missingOnly ? season.episodes.filter(function (e) { return !e.owned; }) : season.episodes;
        var emptyMsg = (data && data.source === 'youtube')
            ? (ytFilter.q || ytFilter.state !== 'all' || ytFilter.duration !== 'all' ? 'No videos match these filters.' : 'No videos here.')
            : 'No ' + (missingOnly ? 'missing ' : '') + 'episodes here. 🎉';
        var seasonBar = seasonActionsHtml(season);
        var grid = !!(data && data.source === 'youtube' && ytLayout() === 'grid');
        host.classList.toggle('vd-episodes--grid', grid);
        host.innerHTML = seasonBar +
            (eps.length
                ? (grid ? '<div class="vd-ytgrid">' + eps.map(ytVideoCard).join('') + '</div>' : eps.map(episodeRow).join(''))
                : '<div class="vd-ep-empty">' + emptyMsg + '</div>');
        host.classList.remove('vd-ep-anim'); void host.offsetWidth; host.classList.add('vd-ep-anim');
        applyDlStates();   // repaint any in-flight/finished grabs on the fresh rows
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
    // A channel's upload years are always pills. Years as poster cards were the
    // TV metaphor bleeding through, and a second view switch beside Grid/List
    // (both drawn with the same glyph) left two layout controls on one page.
    function restoreView() {
        if (data && data.source === 'youtube') { seasonView = 'pills'; return; }
        var v = null;
        try { v = localStorage.getItem(VIEW_KEY); } catch (e) { v = null; }
        seasonView = v || 'rail';
    }
    function setView(v) {
        seasonView = v; menuOpen = false;
        try { localStorage.setItem(VIEW_KEY, v); } catch (e) { /* ignore */ }
        renderViewToggle(); renderSeasonNav();
    }

    // A page that couldn't load says so calmly, explains, and offers a way
    // forward. It used to print "Playlist unavailable" as a 5em headline over
    // an empty Episodes section.
    var _retry = null;
    function showLoadError(noun, why, extUrl) {
        var r0 = root(); if (!r0) return;
        showLoading(false);
        r0.classList.add('vd-page--error');
        setText('[data-vd-title]', 'Couldn\u2019t open this ' + noun);
        var t = q('[data-vd-title]'); if (t) t.classList.remove('vd-title--logo');
        var logo = q('[data-vd-logo]'); if (logo) { logo.hidden = true; logo.removeAttribute('src'); }
        var cover = q('[data-vd-cover]'); if (cover) { cover.hidden = true; cover.removeAttribute('src'); }
        var bbc = q('.vd-bb-content'); if (bbc) bbc.classList.add('vd-bb-content--no-cover');
        var bg = q('[data-vd-backdrop]'); if (bg) { bg.style.backgroundImage = ''; bg.classList.add('vd-bb-bg--empty'); }
        ['[data-vd-meta]', '[data-vd-genres]', '[data-vd-links]', '[data-vd-crew-line]', '[data-vd-ratings]',
         '[data-vd-awards]', '[data-vd-essentials]', '[data-vd-health]', '[data-vd-tagline]'].forEach(function (sel) {
            var n = q(sel); if (n) { n.innerHTML = ''; if (sel !== '[data-vd-meta]' && sel !== '[data-vd-genres]') n.hidden = true; }
        });
        renderOverview(why === 'offline'
            ? 'SoulSync couldn\u2019t reach the server for this ' + noun + '. Check your connection, then try again.'
            : (noun === 'channel' || noun === 'playlist'
                ? 'YouTube didn\u2019t return this ' + noun + '. It may be private, deleted, or YouTube may be rate-limiting for a moment.'
                : 'This ' + noun + ' isn\u2019t in your library any more. It may have been removed on your media server.'));
        var a = q('[data-vd-actions]');
        if (a) {
            a.innerHTML = '<button class="library-artist-watchlist-btn vd-action-main" type="button" data-vd-act="retry-load">' +
                '<span class="watchlist-icon">\u21BB</span><span class="watchlist-text">Try again</span></button>' +
                (extUrl ? '<a class="vd-manage-btn vd-more-link vd-error-ext" href="' + esc(extUrl) + '" target="_blank" rel="noopener noreferrer">' +
                    '<span class="vd-manage-ic" aria-hidden="true">\u2197</span> Open on YouTube</a>' : '');
        }
        var tabs = q('[data-vd-tabs]'); if (tabs) tabs.hidden = true;
    }

    function showLoading(on) { var l = q('[data-vd-loading]'); if (l) l.hidden = !on; }

    // ── watchlist (new curated system; airing shows only) ─────────────────────
    function toggleWatchlist() {
        if (!data || data.kind !== 'show' || !data.tmdb_id) return;
        var watching = !!data._vw_watched;
        var apply = function () {
            var url = watching ? '/api/video/watchlist/remove' : '/api/video/watchlist/add';
            // On a TMDB preview, data.id is the tmdb id (NOT a library row) — don't send
            // a bogus library_id or library poster proxy; use the TMDB poster instead.
            var owned = data.source !== 'tmdb';
            var body = watching
                ? { kind: 'show', tmdb_id: data.tmdb_id }
                : { kind: 'show', tmdb_id: data.tmdb_id, title: data.title,
                    poster_url: owned ? ('/api/video/poster/show/' + data.id) : proxied(data.poster_url) };
            if (owned) body.library_id = data.id;
            fetch(url, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) })
                .then(function (r) { return r.json(); })
                .then(function (res) {
                    if (!res || !res.success) return;
                    data._vw_watched = !watching;
                    renderActions(data);
                    if (typeof showToast === 'function')
                        showToast(!watching ? 'Added to watchlist' : 'Removed from watchlist', !watching ? 'success' : 'info');
                    document.dispatchEvent(new CustomEvent('soulsync:video-watchlist-changed',
                        { detail: { kind: 'show', id: String(data.tmdb_id), watched: !watching } }));
                }).catch(function () { if (typeof showToast === 'function') showToast('Watchlist update failed', 'error'); });
        };
        if (watching && typeof showConfirmDialog === 'function') {
            showConfirmDialog({ title: 'Remove from Watchlist',
                message: 'Remove “' + (data.title || 'this show') + '” from your watchlist?',
                confirmText: 'Remove', cancelText: 'Cancel', destructive: true })
                .then(function (ok) { if (ok) apply(); });
        } else { apply(); }
    }

    // ── movie detail (flat) ───────────────────────────────────────────────────
    // ── live download status (a movie being grabbed shows progress here, and the
    //    chip jumps to the Downloads page) ─────────────────────────────────────
    var _dlWatch = { id: null, t: null };
    function stopMovieDownloadWatch() {
        if (_dlWatch.t) { clearTimeout(_dlWatch.t); _dlWatch.t = null; }
        _dlWatch.id = null;
        var c = q('[data-vd-dlchip]'); if (c) c.remove();
    }
    function renderMovieDownloadChip(dl) {
        var a = q('[data-vd-actions]'); if (!a) return;
        var chip = q('[data-vd-dlchip]');
        var show = dl && ['downloading', 'queued', 'searching', 'completed', 'failed'].indexOf(dl.status) > -1;
        if (!show) { if (chip) chip.remove(); return; }
        if (!chip) {
            chip = document.createElement('button');
            chip.type = 'button';
            chip.setAttribute('data-vd-dlchip', '');
            chip.title = 'Open the Downloads page';
            chip.addEventListener('click', function () {
                document.dispatchEvent(new CustomEvent('soulsync:video-navigate', { detail: 'video-downloads' }));
            });
            a.parentNode.insertBefore(chip, a);   // sits above the action buttons (renderActions won't wipe it)
        }
        var st = dl.status, pct = Math.max(0, Math.min(100, dl.progress || 0));
        if (st === 'completed') pct = 100;
        chip.className = 'vd-dlchip ' + (st === 'completed' ? 'is-done' : (st === 'failed' ? 'is-fail' : 'is-active'));
        var label = st === 'completed' ? 'Downloaded' : st === 'failed' ? 'Download failed'
            : st === 'searching' ? 'Finding a release…' : st === 'queued' ? 'Queued' : 'Downloading';
        var pctTxt = (st === 'downloading' || st === 'queued') ? ' · ' + pct + '%' : '';
        var ic = st === 'completed' ? '✓ ' : st === 'failed' ? '✕ ' : '⤓ ';
        chip.innerHTML =
            '<span class="vd-dlchip-bar" style="width:' + pct + '%"></span>' +
            '<span class="vd-dlchip-txt">' + ic + esc(label) + pctTxt + '<span class="vd-dlchip-go"> · Track ↗</span></span>';
    }
    function watchMovieDownload(id) {
        stopMovieDownloadWatch();
        _dlWatch.id = id;
        (function tick() {
            if (currentId !== id || currentKind !== 'movie') return;   // navigated away → stop
            fetch('/api/video/downloads/status?media_id=' + encodeURIComponent(id) +
                  '&media_source=' + encodeURIComponent(currentSource || 'library'),
                  { headers: { Accept: 'application/json' } })
                .then(function (r) { return r.ok ? r.json() : null; })
                .then(function (res) {
                    if (currentId !== id || currentKind !== 'movie') return;
                    var dl = res && res.download;
                    renderMovieDownloadChip(dl);
                    var active = dl && ['downloading', 'queued', 'searching'].indexOf(dl.status) > -1;
                    if (active) _dlWatch.t = setTimeout(tick, 1800);
                }).catch(function () { /* keep last state */ });
        })();
    }

    function loadMovie(id, source) {
        _retry = function () { loadMovie(id, source); };
        currentKind = 'movie'; currentSource = source || 'library';
        if (!root()) return;
        if (currentId !== id) artAttemptedFor = null;
        currentId = id;
        stopMovieDownloadWatch();   // clear any prior movie's chip
        showLoading(true);
        resetExtras();
        var dh = q('[data-vd-details]'); if (dh) dh.innerHTML = '';
        var r0 = root(); if (r0) resetAccent(r0);
        fetch(detailURL('movie', id, currentSource), { headers: { 'Accept': 'application/json' } })
            .then(function (r) { return r.ok ? r.json() : null; })
            .then(function (d) {
                showLoading(false);
                if (d && d.redirect) { reopen(d.redirect); return; }
                if (!d || d.error) { if (currentId === id) showLoadError('movie', 'gone'); return; }
                if (currentId !== id || currentKind !== 'movie') return;
                data = d;
                renderBillboard(d);
                renderDetails(d);
                // About carries the title's own facts straight away; TMDB's
                // (budget, keywords, studios) join when the extras land
                renderFacts(null, [], []);
                var sub = document.querySelector('.video-subpage[data-video-subpage="video-movie-detail"]');
                if (sub) sub.scrollTop = 0;
                if (currentSource === 'tmdb') {
                    renderExtras('movie', id, d);     // extras ship inside the tmdb payload
                } else {
                    maybeRefreshMovie(id);
                    loadExtras('movie', id);
                    loadTitleHistory('movie', id);    // acquisition history (P9)
                    loadAcquisition('movie', id);        // ...and where it stands now
                    watchMovieDownload(id);           // live download progress chip (if any)
                }
            })
            .catch(function () { showLoading(false); showLoadError('movie', 'offline'); });
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
        _retry = function () { loadShow(id, source); };
        currentKind = 'show'; currentSource = source || 'library';
        if (!root()) return;
        if (currentId !== id) artAttemptedFor = null;
        currentId = id;
        showLoading(true);
        resetExtras();
        showEpSyncing(false);
        ['[data-vd-episodes]', '[data-vd-season-nav]'].forEach(function (s) { var n = q(s); if (n) n.innerHTML = ''; });
        var r0 = root(); if (r0) resetAccent(r0);
        fetch(detailURL('show', id, currentSource), { headers: { 'Accept': 'application/json' } })
            .then(function (r) { return r.ok ? r.json() : null; })
            .then(function (d) {
                showLoading(false);
                if (d && d.redirect) { reopen(d.redirect); return; }
                if (!d || d.error) { if (currentId === id) showLoadError('show', 'gone'); return; }
                if (currentId !== id || currentKind !== 'show') return;
                d.seasons = specialsLast(d.seasons);
                data = d; menuOpen = false; missingOnly = false; restoreView();
                selectedSeason = initialSeasonNum(d);
                var mt = q('[data-vd-missing-toggle]');
                if (mt) { mt.hidden = !(d.seasons && d.seasons.length); mt.classList.remove('vd-missing-toggle--on'); }
                renderBillboard(d);
                renderFacts(null, [], []);
                renderViewToggle(); renderSeasonNav(); ensureSeasonEpisodes();
                startDlTracking();   // resume any in-flight grabs for this show
                var sub = document.querySelector('.video-subpage[data-video-subpage="video-show-detail"]');
                if (sub) sub.scrollTop = 0;
                if (currentSource === 'tmdb') {
                    renderExtras('show', id, d);
                } else {
                    maybeRefreshArt(id);
                    loadExtras('show', id);
                    loadTitleHistory('show', id);     // acquisition history (P9)
                    loadAcquisition('show', id);        // ...and where it stands now
                }
            })
            .catch(function () { showLoading(false); showLoadError('show', 'offline'); });
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

    function showEpSyncing(on, message) {
        var el = q('[data-vd-ep-syncing]');
        if (!el) return;
        var t = el.querySelector('span:last-child');
        // Reuse the indicator for channel date-fetching too; reset to the TMDB copy
        // when no message is passed so the two paths don't leak text into each other.
        if (on && t) t.textContent = message ||
            'Fetching the full episode list from TMDB… missing episodes will fill in shortly.';
        el.hidden = !on;
    }

    // Per-show Synchronize (server re-read). Synchronous on the backend (one
    // show reads in seconds); the response says exactly what changed.
    function syncMovieNow(btn) {
        var id = btn.getAttribute('data-vd-sync-id');
        if (!id || btn.disabled) return;
        btn.disabled = true;
        var orig = btn.innerHTML;
        btn.innerHTML = '<span class="vd-manage-ic">⟳</span> Syncing…';
        fetch('/api/video/detail/movie/' + encodeURIComponent(id) + '/sync', { method: 'POST' })
            .then(function (r) { return r.json().catch(function () { return { success: false, error: 'HTTP ' + r.status }; }); })
            .then(function (d) {
                btn.disabled = false;
                btn.innerHTML = orig;
                if (!d || !d.success) {
                    if (typeof showToast === 'function') showToast(d && d.error ? d.error : 'Sync failed', 'error');
                    return;
                }
                if (d.movie_removed) {
                    if (typeof showToast === 'function') showToast('"' + (d.title || 'Movie') + '" is no longer on your server - removed from the library', 'warning');
                    document.dispatchEvent(new CustomEvent('soulsync:video-navigate', { detail: 'video-library' }));
                    return;
                }
                var bits = [];
                if (d.files_added) bits.push('+' + d.files_added + ' file' + (d.files_added !== 1 ? 's' : ''));
                if (d.files_removed) bits.push('-' + d.files_removed + ' file' + (d.files_removed !== 1 ? 's' : ''));
                if (typeof showToast === 'function') {
                    var refreshBad = d.metadata_refresh && d.metadata_refresh !== 'ok';
                    if (refreshBad) {
                        showToast('Synchronized' + (bits.length ? ': ' + bits.join(', ') : '') +
                            ' - metadata refresh failed (' + d.metadata_refresh + ')', 'warning');
                    } else {
                        showToast('Synchronized' + (bits.length ? ': ' + bits.join(', ') : ' - no changes'), 'success');
                    }
                }
                var rid = parseInt(d.movie_id != null ? d.movie_id : id, 10);
                if (!isNaN(rid)) {
                    if (d.rekeyed) { currentId = rid; }
                    if (currentId === rid) loadMovie(rid, 'library');
                }
            })
            .catch(function () {
                btn.disabled = false;
                btn.innerHTML = orig;
                if (typeof showToast === 'function') showToast('Sync failed - could not reach the server', 'error');
            });
    }

    function syncShowNow(btn) {
        var id = btn.getAttribute('data-vd-sync-id');
        if (!id || btn.disabled) return;
        btn.disabled = true;
        var orig = btn.innerHTML;
        btn.innerHTML = '<span class="vd-manage-ic">⟳</span> Syncing…';
        fetch('/api/video/detail/show/' + encodeURIComponent(id) + '/sync', { method: 'POST' })
            .then(function (r) { return r.json().catch(function () { return { success: false, error: 'HTTP ' + r.status }; }); })
            .then(function (d) {
                btn.disabled = false;
                btn.innerHTML = orig;
                if (!d || !d.success) {
                    if (typeof showToast === 'function') showToast(d && d.error ? d.error : 'Sync failed', 'error');
                    return;
                }
                if (d.show_removed) {
                    if (typeof showToast === 'function') showToast('"' + (d.title || 'Show') + '" is no longer on your server — removed from the library', 'warning');
                    document.dispatchEvent(new CustomEvent('soulsync:video-navigate', { detail: 'video-library' }));
                    return;
                }
                var bits = [];
                if (d.episodes_added) bits.push('+' + d.episodes_added + ' episode' + (d.episodes_added !== 1 ? 's' : ''));
                if (d.episodes_removed) bits.push('−' + d.episodes_removed + ' episode' + (d.episodes_removed !== 1 ? 's' : ''));
                if (d.files_added) bits.push('+' + d.files_added + ' file' + (d.files_added !== 1 ? 's' : ''));
                if (d.files_removed) bits.push('−' + d.files_removed + ' file' + (d.files_removed !== 1 ? 's' : ''));
                if (typeof showToast === 'function') {
                    // a failed schedule refresh must be SAID, not silently absorbed —
                    // otherwise "no changes" reads as "everything's fine"
                    var refreshBad = d.schedule_refresh && d.schedule_refresh !== 'ok';
                    if (refreshBad) {
                        showToast('Synchronized' + (bits.length ? ': ' + bits.join(', ') : '') +
                            ' — episode schedule refresh failed (' + d.schedule_refresh + ')', 'warning');
                    } else {
                        showToast('Synchronized' + (bits.length ? ': ' + bits.join(', ') : ' — no changes'), 'success');
                    }
                }
                // a Plex re-key heals onto a NEW row id — reload THAT row
                var rid = parseInt(d.show_id != null ? d.show_id : id, 10);
                if (!isNaN(rid)) {
                    if (d.rekeyed) { currentId = rid; }
                    if (currentId === rid) reloadDetail(rid);
                }
            })
            .catch(function () {
                btn.disabled = false;
                btn.innerHTML = orig;
                if (typeof showToast === 'function') showToast('Sync failed — could not reach the server', 'error');
            });
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
                d.seasons = specialsLast(d.seasons);
                data = d;
                if (!seasonByNum(selectedSeason)) {
                    selectedSeason = initialSeasonNum(d);
                }
                renderBillboard(d); renderSeasonNav(); renderEpisodes();
            })
            .catch(function () { showEpSyncing(false); });
    }

    // ── YouTube channel (rendered through the show pipeline) ──────────────────
    // A channel = a "show": upload YEAR is a season, video an episode. Renders
    // into the SHOW container (currentKind='show') with d.kind='channel' +
    // d.source='youtube' driving every branch above. All TMDB-only sections
    // auto-hide on empty channel data.
    var ytVideoMap = {};   // youtube_id -> raw video (for wish add, main grid + playlists)
    var ytFilter = { q: '', sort: 'newest', state: 'all', duration: 'all' };   // channel search + sort + facets
    var ytSearchTimer = null;

    function ytProx(u) { return (window.VideoYoutube && u) ? VideoYoutube.img(u) : (u || ''); }
    // Upgrade an i.ytimg thumbnail to maxresdefault (1280×720) for the big rail
    // poster — hqdefault (480×360, often sqp-shrunk) looks soft cropped to 2:3.
    function ytHiRes(u) {
        var m = /\/vi\/([^/?]+)\//.exec(u || '');
        return m ? 'https://i.ytimg.com/vi/' + m[1] + '/maxresdefault.jpg' : (u || '');
    }

    function ytEpisodeOf(v, i) {
        // owned = ON DISK (a completed download in history); wished = queued.
        // The old owned:!!v.wished faked ownership from the wishlist flag.
        return { episode_number: i + 1, title: v.title, overview: v.description || '',
            air_date: v.published_at, owned: !!v.downloaded, wished: !!v.wished, has_still: false,
            still_url: ytProx(v.thumbnail_url), youtube_id: v.youtube_id,
            yt_duration: v.duration || '', view_count: v.view_count || 0,
            like_count: v.like_count || 0, dislike_count: v.dislike_count || 0 };
    }
    // how many of these videos are actually on disk. three builders used to
    // hardcode 0 here, which the season pills showed as "0 / N eps" and the
    // health band showed as "0 downloads" on a playlist you'd fully grabbed.
    function ytOwnedCount(vids) {
        return (vids || []).filter(function (v) { return v.downloaded; }).length;
    }
    function ytDurSecs(d) {
        if (!d) return 0;
        return String(d).split(':').reduce(function (acc, n) { return acc * 60 + (parseInt(n, 10) || 0); }, 0);
    }
    // Year-grouped seasons (the default "by year" view). asc → oldest first.
    function ytGroupByYear(videos, ch, asc) {
        var byYear = {};
        videos.forEach(function (v) {
            var yr = (v.published_at && /^\d{4}/.test(v.published_at)) ? parseInt(v.published_at.slice(0, 4), 10) : 0;
            (byYear[yr] = byYear[yr] || []).push(v);
        });
        var years = Object.keys(byYear).map(Number).sort(function (a, b) { return asc ? a - b : b - a; });
        return years.map(function (yr) {
            var vids = byYear[yr].slice().sort(function (a, b) {
                var x = a.published_at || '', y = b.published_at || '';
                return asc ? (x < y ? -1 : x > y ? 1 : 0) : (x > y ? -1 : x < y ? 1 : 0);
            });
            var thumb = '';
            for (var k = 0; k < vids.length; k++) { if (vids[k].thumbnail_url) { thumb = vids[k].thumbnail_url; break; } }
            var poster = thumb ? ytProx(ytHiRes(thumb)) : '';        // maxres for the rail card
            var eps = vids.map(ytEpisodeOf);
            var ownedN = eps.filter(function (e) { return e.owned; }).length;
            var label = yr ? String(yr) : (years.length === 1 ? 'All Videos' : 'Earlier videos');
            return { season_number: yr, title: label, poster_url: poster || ytProx(ch.avatar_url),
                poster_fallback: thumb ? ytProx(thumb) : '',         // ← if maxres 404s
                episode_owned: ownedN, episode_total: eps.length, episodes: eps };
        });
    }
    // A search OR a popularity/length sort collapses the year view into one flat,
    // sorted "results" list instead of per-year seasons.
    function ytFlatMode() { return !!ytFilter.q || ytFilter.state !== 'all' || ytFilter.duration !== 'all' || ytFilter.sort === 'views' || ytFilter.sort === 'longest'; }
    function ytVisibleVideos() {
        var all = (data && data._channel && data._channel.videos) || [];
        var q = (ytFilter.q || '').toLowerCase().trim();
        var vids = q ? all.filter(function (v) { return (v.title || '').toLowerCase().indexOf(q) >= 0; }) : all.slice();
        if (ytFilter.state === 'downloaded') vids = vids.filter(function (v) { return v.downloaded; });
        else if (ytFilter.state === 'missing') vids = vids.filter(function (v) { return !v.downloaded; });
        else if (ytFilter.state === 'wished') vids = vids.filter(function (v) { return v.wished; });
        if (ytFilter.duration !== 'all') {
            vids = vids.filter(function (v) {
                var s = ytDurSecs(v.duration);
                if (!s) return false;
                if (ytFilter.duration === 'short') return s < 60;
                if (ytFilter.duration === 'standard') return s >= 60 && s < 1200;
                return s >= 1200;
            });
        }
        if (ytFilter.sort === 'views') vids.sort(function (a, b) { return (b.view_count || 0) - (a.view_count || 0); });
        else if (ytFilter.sort === 'longest') vids.sort(function (a, b) { return ytDurSecs(b.duration) - ytDurSecs(a.duration); });
        else if (ytFilter.sort === 'oldest') vids.sort(function (a, b) { var x = a.published_at || '￿', y = b.published_at || '￿'; return x < y ? -1 : x > y ? 1 : 0; });
        else vids.sort(function (a, b) { var x = a.published_at || '', y = b.published_at || ''; return x > y ? -1 : x < y ? 1 : 0; });
        return vids;
    }
    function ytFlatSeason(vids) {
        var ch = (data && data._channel) || {};
        var title = ytFilter.q ? (vids.length + ' result' + (vids.length === 1 ? '' : 's'))
            : (ytFilter.sort === 'views' ? 'Most viewed' : 'Longest');
        return { season_number: -1, title: title, poster_url: ytProx(ch.avatar_url),
            episode_owned: ytOwnedCount(vids), episode_total: vids.length, episodes: vids.map(ytEpisodeOf) };
    }
    function ytRebuildMap() {
        ytVideoMap = {};
        ((data && data._channel && data._channel.videos) || []).forEach(function (v) { ytVideoMap[v.youtube_id] = v; });
    }
    // Re-derive data.seasons from the master list honouring the active filter/sort.
    // force=true always re-renders the grid (a filter change); else only when the
    // viewed season actually changed (so a streaming batch doesn't flicker).
    function ytRegroup(force) {
        if (!data || !data._channel) return;
        var prevSel = selectedSeason, prevObj = seasonByNum(prevSel);
        var prevEp = prevObj ? prevObj.episodes.length : -1;
        ytRebuildMap();
        if (ytFlatMode()) {
            data.seasons = [ytFlatSeason(ytVisibleVideos())];
            selectedSeason = -1;
        } else {
            data.seasons = ytGroupByYear(data._channel.videos.slice(), data._channel, ytFilter.sort === 'oldest');
            if (!seasonByNum(selectedSeason)) selectedSeason = data.seasons.length ? data.seasons[0].season_number : null;
        }
        data.season_count = data.seasons.length;
        data.episode_total = (data._channel.videos || []).length;
        // count off the MASTER list, not the seasons: a search filters the
        // seasons, and the band must not read as "your downloads vanished".
        data.episode_owned = ytOwnedCount(data._channel.videos);
        renderHealth(data);
        renderEssentials(data);
        renderSeasonNav();
        var nowObj = seasonByNum(selectedSeason);
        if (force || selectedSeason !== prevSel || !nowObj || nowObj.episodes.length !== prevEp) renderEpisodes();
        if (force) ytRefocusSearch();
    }
    function ytRefocusSearch() {
        var inp = q('[data-vd-yt-search]');
        if (inp && document.activeElement !== inp) { var v = inp.value; inp.focus(); inp.value = ''; inp.value = v; }
    }
    function ytFacetBtn(group, value, label) {
        var active = ytFilter[group] === value;
        return '<button class="vd-yt-facet' + (active ? ' vd-yt-facet--active' : '') + '" type="button" ' +
            'data-vd-yt-filter="' + group + '" data-vd-yt-filter-value="' + value + '">' + esc(label) + '</button>';
    }
    function ytControlsHTML() {
        var sorts = [['newest', 'Newest'], ['oldest', 'Oldest'], ['views', 'Most viewed'], ['longest', 'Longest']];
        var total = (data && data._channel && data._channel.videos && data._channel.videos.length) || 0;
        var filtered = ytVisibleVideos().length;
        var counts = total ? '<div class="vd-yt-counts"><strong>' + filtered + '</strong><span>of ' + total + ' loaded</span></div>' : '';
        return '<div class="vd-yt-controls">' +
            '<div class="vd-yt-search"><span class="vd-yt-search-ic">⌕</span>' +
            '<input class="vd-yt-search-in" type="text" placeholder="Search this channel…" value="' +
            esc(ytFilter.q) + '" data-vd-yt-search></div>' +
            '<select class="vd-yt-sort" data-vd-yt-sort>' + sorts.map(function (s) {
                return '<option value="' + s[0] + '"' + (ytFilter.sort === s[0] ? ' selected' : '') + '>' + esc(s[1]) + '</option>';
            }).join('') + '</select>' +
            '<div class="vd-yt-layout" role="group" aria-label="Layout">' +
                '<button type="button" class="vd-yt-layout-btn' + (ytLayout() === 'grid' ? ' vd-yt-layout-btn--on' : '') +
                    '" data-vd-yt-layout="grid" aria-pressed="' + (ytLayout() === 'grid') + '" title="Grid">▦</button>' +
                '<button type="button" class="vd-yt-layout-btn' + (ytLayout() === 'list' ? ' vd-yt-layout-btn--on' : '') +
                    '" data-vd-yt-layout="list" aria-pressed="' + (ytLayout() === 'list') + '" title="List">☰</button>' +
            '</div>' + counts + '</div>' +
            '<div class="vd-yt-facets" aria-label="Channel video filters">' +
                ytFacetBtn('state', 'all', 'All') +
                ytFacetBtn('state', 'missing', 'Not downloaded') +
                ytFacetBtn('state', 'downloaded', 'Downloaded') +
                ytFacetBtn('state', 'wished', 'Wishlisted') +
                '<span class="vd-yt-facet-sep"></span>' +
                ytFacetBtn('duration', 'all', 'Any length') +
                ytFacetBtn('duration', 'short', 'Shorts') +
                ytFacetBtn('duration', 'standard', 'Standard') +
                ytFacetBtn('duration', 'long', 'Long-form') +
            '</div>';
    }
    function ytToShow(resp) {
        var ch = resp.channel || {};
        ytVideoMap = {};
        (ch.videos || []).forEach(function (v) { ytVideoMap[v.youtube_id] = v; });
        var seasons = ytGroupByYear(ch.videos || [], ch, false);
        // no banner: the channel's most-viewed upload in full resolution reads as
        // channel art; the avatar blown up to 1400px was a blur
        var backdrop = ch.banner_url ? ytProx(ch.banner_url) : '', fallback = '';
        if (!backdrop) {
            var top = (ch.videos || []).filter(function (v) { return v.thumbnail_url; })
                .sort(function (a, b) { return (Number(b.view_count) || 0) - (Number(a.view_count) || 0); })[0];
            if (top) { backdrop = ytProx(ytHiRes(top.thumbnail_url)); fallback = ytProx(top.thumbnail_url); }
        }
        var ambient = !ch.banner_url && !!backdrop;
        if (!ambient) fallback = '';
        return { kind: 'channel', source: 'youtube', id: ch.youtube_id, title: ch.title || 'Channel',
            overview: ch.description || '', backdrop_url: backdrop, has_backdrop: !!backdrop,
            poster_url: ytProx(ch.avatar_url), has_poster: !!ch.avatar_url, genres: ch.tags || [], handle: ch.handle,
            subscriber_count: ch.subscriber_count, video_count: ch.video_count, view_count: ch.view_count,
            following: !!resp.following, _channel: ch, seasons: seasons, season_count: seasons.length,
            _ambient_backdrop: ambient, _backdrop_fallback: fallback,
            episode_total: (ch.videos || []).length, episode_owned: ytOwnedCount(ch.videos) };
    }

    // Stream the channel's FULL video catalog in batches via InnerTube (each page
    // fetched once, light on rate limits) and fold it into the year-seasons live:
    // each batch fills missing upload dates on the videos already shown AND appends
    // older ones, re-rendering after every batch. Replaces the old date-only re-poll
    // — this both DATES the recent videos and EXPANDS past the initial ~90 cap.
    var ytLoadAllToken = 0;
    function ytCancelLoad() { ytLoadAllToken++; }
    function ytLoadAllVideos(id, silent) {
        var token = ++ytLoadAllToken;
        var byId = {};
        ((data && data._channel && data._channel.videos) || []).forEach(function (v) { byId[v.youtube_id] = v; });
        var cont = null, MAX = 2000;   // safety ceiling for pathological channels
        function step() {
            if (token !== ytLoadAllToken || currentId !== id || currentSource !== 'youtube') return;
            // POST so the (huge) continuation token rides in the body, not the URL.
            fetch('/api/video/youtube/channel/' + encodeURIComponent(id) + '/videos', {
                method: 'POST', headers: { 'Content-Type': 'application/json', 'Accept': 'application/json' },
                body: JSON.stringify({ continuation: cont || null }),
            })
                .then(function (r) { return r.ok ? r.json() : null; })
                .then(function (resp) {
                    if (token !== ytLoadAllToken || currentId !== id || currentSource !== 'youtube') return;
                    if (!resp || !resp.success) { showEpSyncing(false); return; }
                    var changed = false;
                    (resp.videos || []).forEach(function (v) {
                        if (!v.youtube_id) return;
                        var ex = byId[v.youtube_id];
                        if (ex) {                                   // already shown → backfill missing fields
                            if (!ex.published_at && v.published_at) { ex.published_at = v.published_at; changed = true; }
                            if (!ex.duration && v.duration) { ex.duration = v.duration; changed = true; }
                            if (!ex.view_count && v.view_count) { ex.view_count = v.view_count; changed = true; }
                        } else {                                    // older video → add it
                            byId[v.youtube_id] = v; data._channel.videos.push(v); changed = true;
                        }
                    });
                    // Re-derive the view from the grown master, honouring the active
                    // filter/sort (only re-renders the grid if the viewed season moved).
                    if (changed) ytRegroup(false);
                    cont = resp.continuation;
                    if (cont && data._channel.videos.length < MAX) {
                        // Quiet when refreshing a remembered channel; only the first
                        // (cache-miss) load shows the "loading full history" banner.
                        if (!silent) showEpSyncing(true, 'Loading the channel’s full video history… ' +
                            data._channel.videos.length + ' videos so far.');
                        setTimeout(step, 120);
                    } else {
                        showEpSyncing(false);
                    }
                })
                .catch(function () { showEpSyncing(false); });
        }
        step();
    }

    function loadChannel(id) {
        _retry = function () { loadChannel(id); };
        currentKind = 'show'; currentSource = 'youtube';   // render into the show container
        if (currentId !== id) artAttemptedFor = null;
        currentId = id;
        if (!root()) return;
        ytFilter = { q: '', sort: 'newest', state: 'all', duration: 'all' };   // a fresh channel starts unfiltered
        ytCancelLoad();
        showLoading(true); resetExtras(); showEpSyncing(false);
        ['[data-vd-episodes]', '[data-vd-season-nav]'].forEach(function (s) { var n = q(s); if (n) n.innerHTML = ''; });
        var r0 = root(); if (r0) resetAccent(r0);
        ytResetPlaylists();
        fetch('/api/video/youtube/channel/' + encodeURIComponent(id) + '?limit=90', { headers: { 'Accept': 'application/json' } })
            .then(function (r) { return r.ok ? r.json() : null; })
            .then(function (resp) {
                showLoading(false);
                if (!resp || !resp.success) { showLoadError('channel', 'gone', 'https://www.youtube.com/channel/' + encodeURIComponent(id)); return; }
                if (currentId !== id) return;
                data = ytToShow(resp); menuOpen = false; missingOnly = false; restoreView();
                selectedSeason = data.seasons.length ? data.seasons[0].season_number : null;
                var mt = q('[data-vd-missing-toggle]');
                if (mt) { mt.hidden = true; mt.classList.remove('vd-missing-toggle--on'); }   // n/a for channels
                renderBillboard(data); renderViewToggle(); renderSeasonNav(); ensureSeasonEpisodes();
                var sub = document.querySelector('.video-subpage[data-video-subpage="video-show-detail"]');
                if (sub) sub.scrollTop = 0;
                ytLoadPlaylists(id);
                // Stream the rest of the catalog (and fill upload dates) in batches.
                // A remembered channel renders full from cache → refresh quietly.
                ytLoadAllVideos(id, !!resp.from_cache);
            })
            .catch(function () { showLoading(false); showLoadError('channel', 'offline', 'https://www.youtube.com/channel/' + encodeURIComponent(id)); });
    }

    // A playlist → a single FLAT season in the curator's order (a partial set, so
    // no year-grouping, no season nav, no catalog streaming).
    function ytPlaylistToShow(resp) {
        var pl = resp.playlist || {};
        var vids = pl.videos || [];
        ytVideoMap = {};
        vids.forEach(function (v) { ytVideoMap[v.youtube_id] = v; });
        var total = pl.video_count || vids.length;
        // YouTube throttles large-playlist listing for our client — be honest when partial.
        var note = total > vids.length ? 'Showing ' + vids.length + ' of ' + total + ' videos.' : '';
        var season = { season_number: 1, title: 'Videos', poster_url: ytProx(pl.thumbnail_url),
            episode_owned: ytOwnedCount(vids), episode_total: vids.length, episodes: vids.map(ytEpisodeOf) };
        return { kind: 'playlist', source: 'youtube', id: pl.playlist_id, title: pl.title || 'Playlist',
            overview: note, poster_url: ytProx(pl.thumbnail_url), has_poster: !!pl.thumbnail_url,
            backdrop_url: ytProx(ytHiRes(pl.thumbnail_url)), has_backdrop: !!pl.thumbnail_url,
            _backdrop_fallback: ytProx(pl.thumbnail_url),
            genres: pl.channel_title ? [pl.channel_title] : [], handle: null,
            subscriber_count: null, view_count: null, video_count: pl.video_count,
            following: !!resp.following, _playlist: pl, seasons: [season], season_count: 1,
            episode_total: vids.length, episode_owned: ytOwnedCount(vids) };
    }

    function loadPlaylist(id) {
        _retry = function () { loadPlaylist(id); };
        currentKind = 'show'; currentSource = 'youtube';
        if (currentId !== id) artAttemptedFor = null;
        currentId = id;
        if (!root()) return;
        ytFilter = { q: '', sort: 'newest', state: 'all', duration: 'all' };
        ytCancelLoad();
        showLoading(true); resetExtras(); showEpSyncing(false);
        ['[data-vd-episodes]', '[data-vd-season-nav]'].forEach(function (s) { var n = q(s); if (n) n.innerHTML = ''; });
        var r0 = root(); if (r0) resetAccent(r0);
        ytResetPlaylists();
        fetch('/api/video/youtube/playlist/' + encodeURIComponent(id), { headers: { 'Accept': 'application/json' } })
            .then(function (r) { return r.ok ? r.json() : null; })
            .then(function (resp) {
                showLoading(false);
                if (!resp || !resp.success || !resp.playlist) { showLoadError('playlist', 'gone', 'https://www.youtube.com/playlist?list=' + encodeURIComponent(id)); return; }
                if (currentId !== id) return;
                data = ytPlaylistToShow(resp); menuOpen = false; missingOnly = false;
                selectedSeason = 1;
                var mt = q('[data-vd-missing-toggle]'); if (mt) { mt.hidden = true; mt.classList.remove('vd-missing-toggle--on'); }
                renderBillboard(data); renderViewToggle(); renderSeasonNav(); renderEpisodes();
                var sub = document.querySelector('.video-subpage[data-video-subpage="video-show-detail"]');
                if (sub) sub.scrollTop = 0;
            })
            .catch(function () { showLoading(false); showLoadError('playlist', 'offline', 'https://www.youtube.com/playlist?list=' + encodeURIComponent(id)); });
    }

    function ytFindEp(id) {
        if (!data || !data.seasons) return null;
        for (var i = 0; i < data.seasons.length; i++) {
            var es = data.seasons[i].episodes;
            for (var j = 0; j < es.length; j++) if (es[j].youtube_id === id) return es[j];
        }
        return null;
    }

    function toggleYtWish(btn) {
        var yc = window.VideoYoutube; if (!yc) return;
        var id = btn.getAttribute('data-vd-yt-wish');
        // wished-state rides 'watching' on the pill buttons and 'vd-ep-wish--done'
        // on the episode-row buttons (the old 'vd-yt-wish--on' check matched
        // NEITHER — unwishing from a row silently re-added instead)
        var on = btn.classList.contains('watching') || btn.classList.contains('vd-ep-wish--done');
        btn.disabled = true;
        var setOn = function (val) {
            btn.disabled = false;
            if (ytVideoMap[id]) ytVideoMap[id].wished = val;
            var r0 = root(), btns = r0 ? r0.querySelectorAll('[data-vd-yt-wish="' + id + '"]') : [];
            for (var i = 0; i < btns.length; i++) {
                var ic = btns[i].querySelector('.watchlist-icon');
                var tx = btns[i].querySelector('.watchlist-text');
                if (ic || tx) {   // pill chrome (hero / playlist cards)
                    btns[i].classList.toggle('watching', val);
                    if (ic) ic.textContent = val ? '✓' : '＋';
                    if (tx) tx.textContent = val ? 'In Wishlist' : 'Wishlist';
                } else {          // episode-row getbtn chrome (TV parity)
                    btns[i].classList.toggle('vd-ep-wish--done', val);
                    btns[i].textContent = val ? '✓' : '＋';
                    btns[i].title = val ? 'Remove from wishlist' : 'Add this video to the wishlist';
                }
            }
            // No rail re-render: wished no longer drives the owned counts (real
            // downloads do), and the buttons above were already patched in place —
            // re-rendering here just refetched every rail poster per click.
            var ep = ytFindEp(id); if (ep) ep.wished = val;
            var cards = r0 ? r0.querySelectorAll('[data-vd-ytc="' + id + '"]') : [];
            for (var k = 0; k < cards.length; k++) {
                cards[k].classList.toggle('vd-ytc--wished', val);
                var st = cards[k].querySelector('.vd-ytc-state--wished');
                if (val && !st && !cards[k].classList.contains('vd-ytc--owned')) {
                    var thumb = cards[k].querySelector('.vd-ytc-thumb');
                    if (thumb) thumb.insertAdjacentHTML('beforeend', '<span class="vd-ytc-state vd-ytc-state--wished">In wishlist</span>');
                } else if (!val && st) { st.remove(); }
            }
            renderEssentials(data);
            document.dispatchEvent(new CustomEvent('soulsync:video-wishlist-changed'));
        };
        if (on) yc.removeWish('video', id).then(function (d) { setOn(!(d && d.success)); }).catch(function () { btn.disabled = false; });
        else {
            // Channel pages carry _channel; playlist pages carry _playlist — fall back
            // to the playlist's owner channel so + Wish works there too (was a 400).
            var ch = (data && data._channel) || {};
            if (!ch.youtube_id && data && data._playlist) {
                var pl = data._playlist;
                ch = { youtube_id: pl.channel_id || pl.playlist_id, title: pl.channel_title || pl.title || 'Playlist',
                       avatar_url: pl.thumbnail_url };
            }
            yc.addVideos({ youtube_id: ch.youtube_id, title: ch.title, avatar_url: ch.avatar_url },
                [ytVideoMap[id] || { youtube_id: id, title: '' }])
                .then(function (d) {
                    var ok = !!(d && d.success);
                    setOn(ok);
                    // A failed add must SAY so — the silent version read as a dead button.
                    if (typeof showToast === 'function') {
                        if (ok) showToast('Added to wishlist', 'success');
                        else showToast((d && d.error) || 'Couldn’t add to wishlist', 'error');
                    }
                })
                .catch(function () { btn.disabled = false; });
        }
    }

    // Hero Watchlist button on a PLAYLIST page → follow/unfollow the playlist.
    function toggleYtPlaylistFollowHero() {
        var yc = window.VideoYoutube; if (!yc || !data) return;
        var pl = data._playlist || {}, on = data.following;
        var bump = function () { document.dispatchEvent(new CustomEvent('soulsync:video-wishlist-changed')); };
        if (on) {
            yc.unfollowPlaylist(data.id).then(function () { data.following = false; renderActions(data); bump(); }).catch(function () { /* ignore */ });
        } else {
            yc.followPlaylist({ playlist_id: data.id, title: pl.title, thumbnail_url: pl.thumbnail_url, videos: pl.videos })
                .then(function (d) {
                    if (d && d.success) { data.following = true; renderActions(data);
                        if (typeof showToast === 'function') showToast('Added to watchlist', 'success'); }
                    bump();
                }).catch(function () { /* ignore */ });
        }
    }

    function toggleYtFollow() {
        var yc = window.VideoYoutube; if (!yc || !data) return;
        var ch = data._channel || {}, on = data.following;
        if (on) yc.unfollow(data.id).then(function () { data.following = false; renderActions(data);
            document.dispatchEvent(new CustomEvent('soulsync:video-wishlist-changed')); }).catch(function () { /* ignore */ });
        else yc.follow({ youtube_id: ch.youtube_id, title: ch.title, avatar_url: ch.avatar_url }).then(function (d) {
            if (d && d.success) {
                data.following = true; renderActions(data);   // toggle in place — no page reload
                if (typeof showToast === 'function') showToast('Added to watchlist', 'success');
                document.dispatchEvent(new CustomEvent('soulsync:video-wishlist-changed'));
            }
        }).catch(function () { /* ignore */ });
    }

    // playlists as collapsible rows below the episodes (channel-only section)
    function ytResetPlaylists() {
        // The playlist section lives ONLY in the show-detail DOM, but a movie/show
        // load runs with q() scoped to a DIFFERENT root — so query the show subpage
        // directly. Otherwise a channel's playlists leak onto the next show you open.
        var showRoot = document.querySelector('[data-video-detail="show"]');
        if (!showRoot) return;
        var sec = showRoot.querySelector('[data-vd-yt-pl-section]');
        var host = showRoot.querySelector('[data-vd-yt-playlists]');
        if (host) host.innerHTML = '';
        if (sec) sec.hidden = true;
    }
    function ytPlaylistRow(p) {
        var thumb = p.thumbnail_url
            ? '<img class="vc-pl-thumb" src="' + esc(ytProx(p.thumbnail_url)) + '" alt="" loading="lazy">'
            : '<span class="vc-pl-thumb vc-pl-thumb--none">▶</span>';
        var n = p.video_count != null ? p.video_count + ' video' + (p.video_count === 1 ? '' : 's') : '';
        var on = !!p.following;
        var watch = '<button class="library-artist-watchlist-btn vc-pl-watch' + (on ? ' watching' : '') + '" type="button" ' +
            'data-vd-yt-pl-watch="' + esc(p.playlist_id) + '" data-pl-title="' + esc(p.title) + '" data-pl-thumb="' + esc(p.thumbnail_url || '') + '">' +
            '<span class="watchlist-icon">' + (on ? '✓' : '＋') + '</span>' +
            '<span class="watchlist-text">' + (on ? 'In Watchlist' : 'Add to Watchlist') + '</span></button>';
        return '<div class="vc-pl vc-pl--collapsed" data-vc-pl="' + esc(p.playlist_id) + '">' +
            '<div class="vc-pl-hd" data-vd-yt-pl-toggle="' + esc(p.playlist_id) + '">' + thumb +
                '<div class="vc-pl-meta"><span class="vc-pl-title">' + esc(p.title) + '</span>' +
                (n ? '<span class="vc-pl-count">' + n + '</span>' : '') + '</div>' + watch +
                '<span class="vc-pl-chev" aria-hidden="true">▾</span></div>' +
            '<div class="vc-pl-vids" data-vd-yt-pl-vids="' + esc(p.playlist_id) + '"></div></div>';
    }
    function toggleYtPlaylistWatch(btn) {
        var yc = window.VideoYoutube; if (!yc) return;
        var pid = btn.getAttribute('data-vd-yt-pl-watch'), on = btn.classList.contains('watching');
        btn.disabled = true;
        var setBtn = function (s) {
            btn.classList.toggle('watching', s);
            var ic = btn.querySelector('.watchlist-icon'); if (ic) ic.textContent = s ? '✓' : '＋';
            var tx = btn.querySelector('.watchlist-text'); if (tx) tx.textContent = s ? 'In Watchlist' : 'Add to Watchlist';
            btn.disabled = false;
        };
        var bump = function () { document.dispatchEvent(new CustomEvent('soulsync:video-wishlist-changed')); };
        if (on) {
            yc.unfollowPlaylist(pid).then(function () { setBtn(false); bump(); }).catch(function () { btn.disabled = false; });
        } else {
            yc.followPlaylist({ playlist_id: pid, title: btn.getAttribute('data-pl-title'),
                                thumbnail_url: btn.getAttribute('data-pl-thumb') }).then(function (d) {
                if (d && d.success) { setBtn(true); bump(); if (typeof showToast === 'function') showToast('Added to watchlist', 'success'); }
                else btn.disabled = false;
            }).catch(function () { btn.disabled = false; });
        }
    }
    var PL_VISIBLE = 6;
    function ytLoadPlaylists(cid) {
        var sec = q('[data-vd-yt-pl-section]'), host = q('[data-vd-yt-playlists]');
        if (!host) return;
        fetch('/api/video/youtube/playlists/' + encodeURIComponent(cid), { headers: { Accept: 'application/json' } })
            .then(function (r) { return r.ok ? r.json() : null; })
            .then(function (d) {
                var pls = (d && d.playlists) || [];
                if (!pls.length || currentId !== cid) return;
                // the first few; the rest wait behind one button. thirty-odd
                // full-width rows, each with its own button, buried the page end
                host.classList.remove('vc-playlists--all');
                host.innerHTML = pls.map(ytPlaylistRow).join('') +
                    (pls.length > PL_VISIBLE
                        ? '<button type="button" class="vc-pl-all" data-vd-yt-pl-all>Show all ' + pls.length + ' playlists</button>'
                        : '');
                if (sec) sec.hidden = false;
            })
            .catch(function () { /* best-effort */ });
    }
    function ytPlVideoCard(v) {
        ytVideoMap[v.youtube_id] = v;
        var thumb = v.thumbnail_url
            ? '<img src="' + esc(ytProx(v.thumbnail_url)) + '" alt="" loading="lazy">' : '';
        return '<div class="vd-yt-plvid">' +
            '<div class="vd-yt-plvid-thumb" data-vd-yt-play="' + esc(v.youtube_id) + '" title="Play video">' +
                thumb + '<span class="vd-yt-plvid-play">▶</span></div>' +
            '<div class="vd-yt-plvid-title" title="' + esc(v.title) + '">' + esc(v.title || 'Untitled') + '</div>' +
            (v.downloaded ? '<div class="vd-ep-badge">Downloaded</div>'
                          : ytWishBtn(v.youtube_id, v.wished, true)) + '</div>';
    }
    function toggleYtPlaylist(el) {
        var pid = el.getAttribute('data-vd-yt-pl-toggle');
        var blk = el.closest('.vc-pl'); if (!blk) return;
        var opened = blk.classList.toggle('vc-pl--collapsed') === false;
        if (!opened) return;
        var host = q('[data-vd-yt-pl-vids="' + pid + '"]');
        if (!host || host.getAttribute('data-loaded')) return;
        host.setAttribute('data-loaded', '1');
        host.innerHTML = '<div class="vc-pl-loading">Loading…</div>';
        fetch('/api/video/youtube/playlist/' + encodeURIComponent(pid), { headers: { Accept: 'application/json' } })
            .then(function (r) { return r.ok ? r.json() : null; })
            .then(function (d) {
                var vids = (d && d.videos) || [];
                host.innerHTML = vids.length ? vids.map(ytPlVideoCard).join('') : '<div class="vc-pl-loading">No videos.</div>';
            })
            .catch(function () { host.removeAttribute('data-loaded'); host.innerHTML = ''; });
    }

    // ── events ────────────────────────────────────────────────────────────────
    function onOpen(e) {
        if (!e || !e.detail) return;
        stopDlTracking(); _dlReset();   // fresh download-tracking state per opened title
        var src = e.detail.source || 'library';
        if (e.detail.kind === 'movie') loadMovie(e.detail.id, src);
        else if (e.detail.kind === 'show') loadShow(e.detail.id, src);
        else if (e.detail.kind === 'channel') loadChannel(e.detail.id);
        else if (e.detail.kind === 'playlist') loadPlaylist(e.detail.id);
        // 'person' is handled by video-person.js (same event).
    }

    function modified(e) {
        return e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey;
    }

    // The small ⋯ menus on episode rows and the season bar. Inline (inside the
    // row) so the delegated handlers below act on their items exactly as they
    // did on the old buttons; one open at a time.
    function closePops(except) {
        var r = root(); if (!r) return;
        var open = r.querySelectorAll('[data-vd-pop-menu]:not([hidden])');
        for (var i = 0; i < open.length; i++) {
            if (except && open[i] === except) continue;
            open[i].hidden = true;
            var b = open[i].parentNode && open[i].parentNode.querySelector('[data-vd-pop]');
            if (b) b.setAttribute('aria-expanded', 'false');
            var row = open[i].closest('.vd-ep');
            if (row) row.classList.remove('vd-ep--menu');
        }
    }
    function togglePop(btn) {
        var menu = btn.parentNode && btn.parentNode.querySelector('[data-vd-pop-menu]');
        if (!menu) return;
        closePops(menu);
        menu.hidden = !menu.hidden;
        btn.setAttribute('aria-expanded', menu.hidden ? 'false' : 'true');
        var row = btn.closest('.vd-ep');
        if (row) row.classList.toggle('vd-ep--menu', !menu.hidden);
        if (!menu.hidden) {
            var first = menu.querySelector('button:not([disabled])');
            if (first) first.focus();
        }
    }

    function onClick(e) {
        var popBtn = e.target.closest('[data-vd-pop]');
        if (popBtn) { e.preventDefault(); e.stopPropagation(); togglePop(popBtn); return; }
        // any other click closes an open ⋯ menu (the item clicked still runs below)
        closePops();
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
        var studio = e.target.closest('[data-vd-studio]');
        if (studio && r.contains(studio)) {
            e.preventDefault();
            var stid = parseInt(studio.getAttribute('data-vd-studio'), 10);
            if (!isNaN(stid)) document.dispatchEvent(new CustomEvent('soulsync:video-open-detail',
                { detail: { kind: 'studio', id: stid, source: 'tmdb' } }));
            return;
        }
        var ytCh = e.target.closest('[data-vd-yt-channel]');
        if (ytCh && r.contains(ytCh)) {
            if (modified(e)) return;
            e.preventDefault();
            document.dispatchEvent(new CustomEvent('soulsync:video-open-detail',
                { detail: { kind: 'channel', source: 'youtube', id: ytCh.getAttribute('data-vd-yt-channel') } }));
            return;
        }
        var gchip = e.target.closest('[data-vd-genre]');
        if (gchip && r.contains(gchip)) {   // genre → Discover filtered to it (#1042)
            e.preventDefault();
            var gkind = (data && data.kind === 'show') ? 'show' : 'movie';
            document.dispatchEvent(new CustomEvent('soulsync:video-navigate', { detail: 'video-discover' }));
            document.dispatchEvent(new CustomEvent('soulsync:video-discover-browse',
                { detail: { genre: gchip.getAttribute('data-vd-genre'), kind: gkind } }));
            return;
        }
        var kchip = e.target.closest('[data-vd-kw]');
        if (kchip && r.contains(kchip)) {   // keyword → video search for it (#1042)
            e.preventDefault();
            document.dispatchEvent(new CustomEvent('soulsync:video-navigate', { detail: 'video-search' }));
            document.dispatchEvent(new CustomEvent('soulsync:video-search-query',
                { detail: { q: kchip.getAttribute('data-vd-kw'), source: 'keyword',
                    kind: (data && data.kind === 'show') ? 'show' : 'movie' } }));
            return;
        }
        var shot = e.target.closest('[data-vd-shot]');
        if (shot && r.contains(shot)) { openLightbox(parseInt(shot.getAttribute('data-vd-shot'), 10) || 0); return; }
        var vid = e.target.closest('[data-vd-video]');
        if (vid && r.contains(vid)) { openTrailer(vid.getAttribute('data-vd-video')); return; }
        var healthFix = e.target.closest('[data-vd-health-fix]');
        if (healthFix && r.contains(healthFix)) {
            e.preventDefault();
            var hfId = data ? ((data.source !== 'tmdb') ? data.id : data.library_id) : null;
            if (window.VideoManage && hfId != null) {
                VideoManage.open({ kind: data.kind, id: hfId,
                    focusMatch: healthFix.getAttribute('data-vd-health-fix') });
            }
            return;
        }
        var castAll = e.target.closest('[data-vd-cast-all]');
        if (castAll && r.contains(castAll)) { openCastModal(); return; }
        var plAll = e.target.closest('[data-vd-yt-pl-all]');
        if (plAll && r.contains(plAll)) {
            var plHost = plAll.closest('[data-vd-yt-playlists]');
            if (plHost) plHost.classList.add('vc-playlists--all');
            plAll.remove();
            return;
        }
        var stripBtn = e.target.closest('[data-vd-strip]');
        if (stripBtn && r.contains(stripBtn)) { e.preventDefault(); scrollStrip(stripBtn); return; }
        var kwMore = e.target.closest('[data-vd-kw-more]');
        if (kwMore && r.contains(kwMore)) {
            var kwWrap = kwMore.closest('[data-vd-keywords]');
            if (kwWrap) kwWrap.classList.add('vd-keywords--all');
            kwMore.remove();
            return;
        }
        var ovMore = e.target.closest('[data-vd-overview-more]');
        if (ovMore && r.contains(ovMore)) {
            var ov = q('[data-vd-overview]');
            if (ov) {
                var opened = ov.classList.toggle('vd-overview--open');
                ovMore.textContent = opened ? 'Less' : 'More';
                ovMore.setAttribute('aria-expanded', opened ? 'true' : 'false');
            }
            return;
        }
        var revMore = e.target.closest('[data-vd-review-more]');
        if (revMore && r.contains(revMore)) {
            var body = q('[data-vd-review-body]');
            if (body) { var open = body.classList.toggle('vd-review-body--open'); revMore.textContent = open ? 'Read less' : 'Read more'; }
            return;
        }
        // YouTube channel interactions (rendered in the show container)
        if (e.target.closest('[data-vd-ext]')) return;   // let watch links open
        var ytPlay = e.target.closest('[data-vd-yt-play]');   // play the video inline (reuses the trailer player)
        if (ytPlay && r.contains(ytPlay)) { e.preventDefault(); openTrailer(ytPlay.getAttribute('data-vd-yt-play')); return; }
        var subsMore = e.target.closest('[data-vd-subs-more]');
        if (subsMore && r.contains(subsMore)) {
            e.preventDefault();
            var rest = subsMore.parentNode.querySelector('.vd-subs-rest');
            if (rest) rest.hidden = false;
            subsMore.remove();
            return;
        }
        var ytLay = e.target.closest('[data-vd-yt-layout]');
        if (ytLay && r.contains(ytLay)) { e.preventDefault(); setYtLayout(ytLay.getAttribute('data-vd-yt-layout')); return; }
        var ytFacet = e.target.closest('[data-vd-yt-filter]');
        if (ytFacet && r.contains(ytFacet) && data && data.source === 'youtube') {
            e.preventDefault();
            ytFilter[ytFacet.getAttribute('data-vd-yt-filter')] = ytFacet.getAttribute('data-vd-yt-filter-value') || 'all';
            ytRegroup(true);
            return;
        }
        var ytWish = e.target.closest('[data-vd-yt-wish]');
        if (ytWish && r.contains(ytWish)) { e.preventDefault(); toggleYtWish(ytWish); return; }
        var ytPlW = e.target.closest('[data-vd-yt-pl-watch]');
        if (ytPlW && r.contains(ytPlW)) { e.preventDefault(); e.stopPropagation(); toggleYtPlaylistWatch(ytPlW); return; }
        var ytPl = e.target.closest('[data-vd-yt-pl-toggle]');
        if (ytPl && r.contains(ytPl)) { toggleYtPlaylist(ytPl); return; }
        // Inline acquisition — must win over the row-expand handler below since the
        // grab/wishlist buttons live inside the episode row.
        var ytGrab = e.target.closest('[data-vd-yt-grab]');
        if (ytGrab && r.contains(ytGrab)) { e.preventDefault(); ytGrabVideoInline(ytGrab); return; }
        var epGrab = e.target.closest('[data-vd-ep-grab]');
        if (epGrab && r.contains(epGrab)) { e.preventDefault(); e.stopPropagation(); grabEpisodeInline(epGrab); return; }
        var epSearch = e.target.closest('[data-vd-ep-search]');
        if (epSearch && r.contains(epSearch)) { e.preventDefault(); e.stopPropagation(); manualSearchEpisode(epSearch); return; }
        var epWish = e.target.closest('[data-vd-ep-wish]');
        if (epWish && r.contains(epWish)) { e.preventDefault(); e.stopPropagation(); wishEpisodeInline(epWish); return; }
        var partWatched = e.target.closest('[data-vd-watched-kind]');
        if (partWatched && r.contains(partWatched)) { e.preventDefault(); e.stopPropagation(); setPartWatched(partWatched); return; }
        var epSub = e.target.closest('[data-vd-ep-subsearch]');
        if (epSub && r.contains(epSub)) { e.preventDefault(); e.stopPropagation(); subSearchEpisode(epSub); return; }
        var seasonGrab = e.target.closest('[data-vd-season-grab]');
        if (seasonGrab && r.contains(seasonGrab)) {
            e.preventDefault();
            if (data && data.source === 'youtube') ytGrabSeasonInline(seasonGrab); else grabSeasonInline(seasonGrab);
            return;
        }
        var seasonMon = e.target.closest('[data-vd-season-monitor]');
        if (seasonMon && r.contains(seasonMon)) { e.preventDefault(); toggleSeasonMonitor(seasonMon); return; }
        var seasonClr = e.target.closest('[data-vd-season-clearfail]');
        if (seasonClr && r.contains(seasonClr)) { e.preventDefault(); clearSeasonFailures(seasonClr);
            return;
        }
        var seasonSearch = e.target.closest('[data-vd-season-search]');
        if (seasonSearch && r.contains(seasonSearch)) { e.preventDefault(); manualSearchSeason(); return; }
        var seasonWish = e.target.closest('[data-vd-season-wish]');
        if (seasonWish && r.contains(seasonWish)) {
            e.preventDefault();
            if (data && data.source === 'youtube') ytWishSeasonInline(seasonWish); else wishSeasonInline(seasonWish);
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
        var tabBtn = e.target.closest('[data-vd-tab]');
        if (tabBtn && r.contains(tabBtn)) { setTab(tabBtn.getAttribute('data-vd-tab')); return; }
        var act = e.target.closest('[data-vd-act]');
        if (act && r.contains(act)) {
            var which = act.getAttribute('data-vd-act');
            if (which === 'more') { toggleMoreMenu(act); return; }
            // Any real action closes the menu it was picked from.
            if (act.closest('[data-vd-more-menu]')) closeMoreMenu();
            if (which === 'watchlist') toggleWatchlist();
            else if (which === 'request') sendRequest(act);
            else if (which === 'wishtoggle') toggleMovieWishlist(act);
            else if (which === 'get') openGetModal();
            else if (which === 'subsearch') subSearchMovie();
            else if (which === 'missing') openGetModal(true);
            else if (which === 'wishlist-missing') wishlistAllMissing(act);
            else if (which === 'poster') openPosterModal();
            else if (which === 'manage') openManagePanel();
            else if (which === 'sync-show') syncShowNow(act);
            else if (which === 'sync-movie') syncMovieNow(act);
            else if (which === 'watched-toggle') toggleWatchedState(act);
            else if (which === 'ihavethis') openHaveThisModal();
            else if (which === 'unlink') unlinkManualMatch();
            else if (which === 'library-tab') {
                setTab('library');
                var bar = q('[data-vd-tabs]');
                if (bar && bar.scrollIntoView) bar.scrollIntoView({ behavior: 'smooth', block: 'start' });
            }
            else if (which === 'yt-follow') toggleYtFollow();
            else if (which === 'yt-settings' && data && window.VideoYoutube && VideoYoutube.openChannelSettings) {
                VideoYoutube.openChannelSettings(data.id, data.title, data.kind === 'playlist' ? 'playlist' : 'channel');
            }
            else if (which === 'yt-pl-follow') toggleYtPlaylistFollowHero();
            else if (which === 'trailer' && data && data.trailer) openTrailer(data.trailer.key);
            else if (which === 'retry-load' && _retry) _retry();
            return;
        }
        var guestAll = e.target.closest('[data-vd-guests-all]');
        if (guestAll && r.contains(guestAll)) {
            e.preventDefault();
            var gwrap = guestAll.closest('.vd-ep-guests');
            if (gwrap) gwrap.classList.add('vd-ep-guests--all');
            guestAll.remove();
            return;
        }
        if (!e.target.closest('[data-vd-more]')) closeMoreMenu();   // click-away
        var mt = e.target.closest('[data-vd-missing-toggle]');
        if (mt && r.contains(mt)) { toggleMissing(); return; }
        if (menuOpen && !e.target.closest('[data-vd-season-nav]')) { menuOpen = false; renderSeasonNav(); }
    }

    // Requests (P4): the no-download-rights acquisition path — ask an admin.
    // the flow (quota first, then the seasons + quality sheet, then the post)
    // lives in video-requests.js as VideoRequests.request, shared with the cards.
    function sendRequest(btn) {
        if (!data || !data.tmdb_id || btn.disabled) return;
        var d = data;
        if (!window.VideoRequests || !window.VideoRequests.request) {
            if (typeof showToast === 'function') showToast('Couldn’t send the request', 'error');
            return;
        }
        btn.disabled = true;
        window.VideoRequests.request({ kind: d.kind, tmdb_id: d.tmdb_id, title: d.title, year: d.year,
            poster_url: d.poster_url || d.poster || null })
            .then(function (res) {
                if (res && res.in_library) d.owned = true;
                else if (res && res.ok) d._req_pending = true;
                else { btn.disabled = false; return; }
                if (data === d) renderActions(d);
            });
    }

    function toggleMissing() {
        missingOnly = !missingOnly;
        var mt = q('[data-vd-missing-toggle]');
        if (mt) mt.classList.toggle('vd-missing-toggle--on', missingOnly);
        renderEpisodes();
    }

    // ── Inline acquisition (per-episode / per-season) — drives the headless VideoGrab ──
    // The video_wishlist episode row needs full context or it renders as an
    // art-less, un-matched orb: show poster_url + library_id, and per-episode
    // still_url / overview / season_poster_url / air_date (mirrors the get-modal's
    // submitWishlist exactly). A null poster was the "no context" bug.
    function _showPoster() {
        if (!data) return null;
        return (data.source !== 'tmdb' && data.has_poster)
            ? '/api/video/poster/show/' + data.id
            : (data.poster_url || data.poster || null);
    }
    function _showIdentity() {
        return { tmdb_id: data && data.tmdb_id, title: data && data.title, poster_url: _showPoster(),
            library_id: (data && (data.source !== 'tmdb' ? data.id : (data.library_id || null))) || null };
    }
    function _epMeta(en) {
        var s = seasonByNum(selectedSeason);
        var ep = (s && (s.episodes || []).filter(function (e) { return e.episode_number === en; })[0]) || {};
        var still = (data && data.source === 'tmdb')
            ? (ep.still_url || null)
            : (ep.has_still && ep.id != null ? '/api/video/poster/episode/' + ep.id : null);
        return { season_number: selectedSeason, episode_number: en, title: ep.title,
            air_date: ep.air_date, still_url: still, overview: ep.overview,
            season_poster_url: seasonArt(s) || null };
    }
    function _seasonMissing() {
        var s = seasonByNum(selectedSeason);
        return s ? (s.episodes || []).filter(function (e) { return !e.owned; }) : [];
    }
    function _grabParams(en, src) {
        // poster travels with the grab → the downloads page rows (and the season
        // group header, which borrows its first row's art) render real posters
        // instead of the placeholder TV orb. Same resolver the wishlist writes use.
        return { title: data.title, source: src, season: selectedSeason, episode: en,
            mediaId: (data.source !== 'tmdb' ? data.id : null), mediaSource: data.source, year: data.year,
            poster: _showPoster() };
    }
    // Optimistic per-episode state for the search phase — the grab has no row in
    // /downloads/active yet. The live tracker takes over once it appears.
    function _setEpSynthetic(en, state) {
        // TV keys are 'season_episode'; YouTube keys are the video id itself
        var key = (data && data.source === 'youtube') ? String(en) : selectedSeason + '_' + en;
        if (state === 'none') delete _dlActive[key];
        else if (!_dlDone[key]) _dlActive[key] = { status: state === 'grabbing' ? 'queued' : 'searching', progress: 0 };
        applyDlStates();
    }

    function grabEpisodeInline(btn) {
        if (!window.VideoGrab || !data) return;
        var en = parseInt(btn.getAttribute('data-vd-ep-grab'), 10);
        btn.disabled = true; _setEpSynthetic(en, 'searching'); startDlTracking();
        VideoGrab.pickSource()
            .then(function (src) { return VideoGrab.episode(_grabParams(en, src)); })
            .then(function (res) {
                if (res && res.ok) { _setEpSynthetic(en, 'grabbing'); startDlTracking(); }
                else {
                    _setEpSynthetic(en, 'none'); btn.disabled = false;
                    toast(res && res.error === 'no release found'
                        ? 'No release found for S' + selectedSeason + 'E' + en : 'Could not grab that episode', 'error');
                }
            });
    }
    function wishEpisodeInline(btn) {
        if (!window.VideoGrab || !data) return;
        if (!data.tmdb_id) { toast('Can’t wishlist — this show isn’t matched to TMDB yet', 'error'); return; }
        var en = parseInt(btn.getAttribute('data-vd-ep-wish'), 10);
        btn.disabled = true;
        VideoGrab.wishlistEpisodes(_showIdentity(), [_epMeta(en)]).then(function (ok) {
            if (ok) { btn.textContent = '✓'; btn.classList.add('vd-ep-wish--done'); toast('S' + selectedSeason + 'E' + en + ' added to wishlist', 'success'); }
            else { btn.disabled = false; toast('Could not add to wishlist', 'error'); }
        });
    }
    // ── YouTube: direct download (TV-parity grab — no search, the video IS the release) ──
    function _ytGrabBody(id) {
        var raw = ytVideoMap[id] || {};          // the raw catalog object (unproxied urls)
        var ep = ytFindEp(id) || raw;
        var ch = (data && data._channel) || {};
        return { video_id: id, channel_id: ch.youtube_id, channel_title: ch.title,
                 video_title: ep.title || raw.title,
                 published_at: ep.air_date || raw.published_at,
                 // RAW thumbnail only — ep.still_url is proxied for rendering and
                 // a relative /api/... url is useless on the download row
                 thumbnail_url: raw.thumbnail_url };
    }
    function _ytStartGrab(id) {
        return fetch('/api/video/youtube/download', {
            method: 'POST', headers: { 'Content-Type': 'application/json', 'Accept': 'application/json' },
            body: JSON.stringify(_ytGrabBody(id)),
        }).then(function (r) { return r.json().catch(function () { return null; }); });
    }
    function ytGrabVideoInline(btn) {
        var id = btn.getAttribute('data-vd-yt-grab');
        btn.disabled = true; _setEpSynthetic(id, 'grabbing'); startDlTracking();
        _ytStartGrab(id).then(function (d) {
            if (d && d.success) {
                toast(d.already ? 'Already downloading' : 'Download queued', 'success');
                document.dispatchEvent(new CustomEvent('soulsync:video-download-started'));
            } else {
                _setEpSynthetic(id, 'none'); btn.disabled = false;
                toast((d && d.error) || 'Could not start the download', 'error');
            }
        }).catch(function () { _setEpSynthetic(id, 'none'); btn.disabled = false; toast('Could not start the download', 'error'); });
    }
    // what one Grab covers: a show's season, a channel's upload year, or a whole playlist
    function grabScope() {
        if (data && data.kind === 'playlist') return { word: 'all', what: 'in this playlist' };
        if (data && data.source === 'youtube') return { word: 'year', what: 'in this year' };
        return { word: 'season', what: 'in this season' };
    }
    function ytTagChips(d) {
        var pl = d._playlist;
        if (d.kind === 'playlist' && pl && pl.channel_id) {
            return '<a class="vd-genre vd-genre--channel" href="/video-detail/youtube/channel/' + encodeURIComponent(pl.channel_id) +
                '" data-vd-yt-channel="' + esc(pl.channel_id) + '" title="Open the channel">' +
                '<span aria-hidden="true">\u25B6</span> ' + esc(pl.channel_title || 'Channel') + '</a>';
        }
        return (d.genres || []).slice(0, 8).map(function (t) {
            return '<span class="vd-genre vd-genre--tag">' + esc(t) + '</span>';
        }).join('');
    }
    function ytGrabSeasonInline(btn) {
        var missing = _seasonMissing();
        if (!missing.length) { toast('Nothing missing here', 'info'); return; }
        btn.disabled = true; _btnLabel(btn, 'Queueing…'); startDlTracking();
        var done = 0;
        var next = function (i) {
            if (i >= missing.length) {
                btn.disabled = false; _btnLabel(btn, 'Grab ' + grabScope().word);
                toast('Queued ' + done + ' of ' + missing.length + ' video' + (missing.length === 1 ? '' : 's'), done ? 'success' : 'info');
                if (done) document.dispatchEvent(new CustomEvent('soulsync:video-download-started'));
                startDlTracking();
                return;
            }
            var id = missing[i].youtube_id;
            _setEpSynthetic(id, 'grabbing');
            _ytStartGrab(id).then(function (d) {
                if (d && d.success) done++; else _setEpSynthetic(id, 'none');
                next(i + 1);
            }).catch(function () { _setEpSynthetic(id, 'none'); next(i + 1); });
        };
        next(0);
    }
    function ytWishSeasonInline(btn) {
        var yc = window.VideoYoutube; if (!yc) return;
        var missing = _seasonMissing().filter(function (e) { return !e.wished; });
        if (!missing.length) { toast('Everything here is already wishlisted or owned', 'info'); return; }
        var ch = (data && data._channel) || {};
        btn.disabled = true; _btnLabel(btn, 'Wishlisting…');
        yc.addVideos({ youtube_id: ch.youtube_id, title: ch.title, avatar_url: ch.avatar_url },
            missing.map(function (e) { return ytVideoMap[e.youtube_id] || { youtube_id: e.youtube_id, title: e.title }; }))
            .then(function (d) {
                var ok = !!(d && d.success);
                btn.disabled = false; _btnLabel(btn, 'Wishlist year');
                if (ok) {
                    missing.forEach(function (e) { e.wished = true; var v = ytFindEp(e.youtube_id); if (v) v.wished = true; });
                    renderEpisodes();
                    toast('Added ' + missing.length + ' video' + (missing.length === 1 ? '' : 's') + ' to wishlist', 'success');
                    document.dispatchEvent(new CustomEvent('soulsync:video-wishlist-changed'));
                } else { toast((d && d.error) || 'Could not add to wishlist', 'error'); }
            })
            .catch(function () { btn.disabled = false; _btnLabel(btn, 'Wishlist year'); toast('Could not add to wishlist', 'error'); });
    }

    // Season monitoring is per-episode in the schema, so the toggle flips every
    // episode of the season and the local copy follows without a page reload.
    function toggleSeasonMonitor(btn) {
        var season = seasonByNum(selectedSeason);
        if (!data || data.kind !== 'show' || !season) return;
        var libId = (data.source !== 'tmdb') ? data.id : data.library_id;
        if (libId == null) return;
        var want = btn.getAttribute('data-vd-season-monitor') === '1';
        btn.disabled = true;
        fetch('/api/video/detail/show/' + libId + '/season/' + season.season_number + '/monitor', {
            method: 'POST', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ monitored: want }),
        }).then(function (r) { return r.ok ? r.json() : null; })
          .then(function (res) {
              btn.disabled = false;
              if (!res || !res.success) { toast('Couldn\u2019t change monitoring', 'error'); return; }
              season.episode_monitored = want ? season.episodes.length : 0;
              season.episodes.forEach(function (e) { e.monitored = want; });
              renderEpisodes();
              toast(want ? 'Season monitored' : 'Season unmonitored', 'success');
          }).catch(function () { btn.disabled = false; toast('Couldn\u2019t change monitoring', 'error'); });
    }

    // Stalled rows back off for hours by design; this is the user saying "no,
    // try now, and try everything". The endpoint clears the backoff evidence
    // and re-searches every source for the season in one call.
    function clearSeasonFailures(btn) {
        var season = seasonByNum(selectedSeason);
        if (!data || !data.tmdb_id || !season) return;
        btn.disabled = true; _btnLabel(btn, 'Retrying\u2026');
        var done = function (msg, type) {
            btn.disabled = false; _btnLabel(btn, 'Clear failures');
            toast(msg, type);
        };
        fetch('/api/video/wishlist/retry', {
            method: 'POST', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ scope: 'season', tmdb_id: data.tmdb_id,
                                   season_number: season.season_number }),
        }).then(function (r) { return r.ok ? r.json() : null; })
          .then(function (res) {
              if (!res || !res.success) { done('Retry failed to start', 'error'); return; }
              var n = Number(res.reset) || 0;
              done(n ? 'Cleared ' + n + ' stalled row' + (n === 1 ? '' : 's') + ', searching again'
                     : 'Nothing stalled in this season', n ? 'success' : 'info');
              document.dispatchEvent(new CustomEvent('soulsync:video-wishlist-changed'));
          }).catch(function () { done('Retry failed to start', 'error'); });
    }

    function grabSeasonInline(btn) {
        if (!window.VideoGrab || !data) return;
        var missing = _seasonMissing();
        if (!missing.length) { toast('No missing episodes in this season', 'info'); return; }
        btn.disabled = true; _btnLabel(btn, 'Searching…'); startDlTracking();
        toast('Looking for a season pack…', 'info');
        // No pickSource() here: VideoGrab.season asks every configured source for a
        // pack before falling back to per-episode. Handing it one source would put
        // the old single-source behaviour back.
        VideoGrab.season({ title: data.title, season: selectedSeason,
            episodes: missing.map(function (e) { return e.episode_number; }),
            mediaId: (data.source !== 'tmdb' ? data.id : null), mediaSource: data.source, year: data.year,
            poster: _showPoster() },
            function (en, state) { _setEpSynthetic(en, state); }
        ).then(function (res) {
            btn.disabled = false; _btnLabel(btn, 'Grab season'); startDlTracking();
            if (res.pack) {
                toast('Season pack grabbed — episodes import as it finishes', 'success');
                return;
            }
            toast('Grabbing ' + res.grabbed + ' of ' + res.total + ' episode' + (res.total === 1 ? '' : 's'), res.grabbed ? 'success' : 'info');
        });
    }
    function wishSeasonInline(btn) {
        if (!window.VideoGrab || !data) return;
        if (!data.tmdb_id) { toast('Can’t wishlist — this show isn’t matched to TMDB yet', 'error'); return; }
        var missing = _seasonMissing();
        if (!missing.length) { toast('No missing episodes in this season', 'info'); return; }
        btn.disabled = true;
        var eps = missing.map(function (e) { return _epMeta(e.episode_number); });
        VideoGrab.wishlistEpisodes(_showIdentity(), eps).then(function (ok) {
            if (ok) { _btnLabel(btn, 'Wishlisted'); toast('Added ' + eps.length + ' episode' + (eps.length === 1 ? '' : 's') + ' to wishlist', 'success'); }
            else { btn.disabled = false; toast('Could not add to wishlist', 'error'); }
        });
    }

    // ── Whole-show wishlist ("Wishlist Missing" hero button) ──
    // Library shows ship every season's episodes with the detail payload; TMDB
    // previews load per-season on demand — fetch whatever the user never opened
    // so "all missing" really means ALL seasons, not just the browsed ones.
    function _ensureAllSeasonsLoaded() {
        if (!data || data.source !== 'tmdb') return Promise.resolve();
        var sid = data.id;
        var pending = (data.seasons || []).filter(function (s) {
            return !s._loaded && !(s.episodes && s.episodes.length);
        });
        return Promise.all(pending.map(function (s) {
            return fetch(TMDB_URL + 'show/' + sid + '/season/' + s.season_number, { headers: { 'Accept': 'application/json' } })
                .then(function (r) { return r.ok ? r.json() : null; })
                .then(function (se) {
                    s._loaded = true;
                    if (se && se.episodes) { s.episodes = se.episodes; s.episode_total = se.episodes.length; }
                })
                .catch(function () { s._loaded = true; });
        }));
    }
    // Missing = not owned AND already aired — un-aired episodes are the airing
    // watchlist's job, not a backlog wishlist's (mirrors the get-modal's epState).
    function _allMissingMetas() {
        var today = new Date().toISOString().slice(0, 10);
        var out = [];
        (data.seasons || []).forEach(function (s) {
            (s.episodes || []).forEach(function (ep) {
                if (ep.owned) return;
                if (ep.air_date && ep.air_date > today) return;
                var still = (data.source === 'tmdb')
                    ? (ep.still_url || null)
                    : (ep.has_still && ep.id != null ? '/api/video/poster/episode/' + ep.id : null);
                out.push({ season_number: s.season_number, episode_number: ep.episode_number,
                    title: ep.title, air_date: ep.air_date, still_url: still, overview: ep.overview,
                    season_poster_url: seasonArt(s) || null });
            });
        });
        return out;
    }
    function wishlistAllMissing(btn) {
        if (!window.VideoGrab || !data || data.kind !== 'show') return;
        if (!data.tmdb_id) { toast('Can’t wishlist — this show isn’t matched to TMDB yet', 'error'); return; }
        if (btn) { btn.disabled = true; _btnLabel(btn, 'Wishlisting…'); }
        var reset = function () { if (btn) { btn.disabled = false; _btnLabel(btn, 'Wishlist Missing'); } };
        _ensureAllSeasonsLoaded().then(function () {
            var eps = _allMissingMetas();
            if (!eps.length) { reset(); toast('Nothing missing — you have every aired episode', 'info'); return; }
            VideoGrab.wishlistEpisodes(_showIdentity(), eps).then(function (ok) {
                if (ok) {
                    if (btn) _btnLabel(btn, 'Wishlisted');
                    toast('Added ' + eps.length + ' missing episode' + (eps.length === 1 ? '' : 's') + ' to wishlist', 'success');
                    document.dispatchEvent(new CustomEvent('soulsync:video-wishlist-changed'));
                } else { reset(); toast('Could not add to wishlist', 'error'); }
            });
        }).catch(reset);
    }

    // Manual search — a dedicated modal that auto-searches every configured source
    // and lets you pick any release (single episode or season pack).
    function _openManualSearch(scope, en) {
        if (!window.VideoDownload || !window.VideoDownload.manualSearch || !data) return;
        VideoDownload.manualSearch({ title: data.title, scope: scope, season: selectedSeason, episode: en,
            mediaId: (data.source !== 'tmdb' ? data.id : null), mediaSource: data.source, year: data.year, poster: _showPoster() });
    }
    function manualSearchEpisode(btn) { _openManualSearch('episode', parseInt(btn.getAttribute('data-vd-ep-search'), 10)); }
    function manualSearchSeason() { _openManualSearch('season'); }

    // Subtitle manual search (Phase 3; video-subtitles.js): the button carries
    // the episode number; the library id comes from the loaded season payload.
    function subSearchEpisode(btn) {
        if (!window.VideoSubtitles || !data) return;
        var en = parseInt(btn.getAttribute('data-vd-ep-subsearch'), 10);
        var s = seasonByNum(selectedSeason);
        var ep = (s && (s.episodes || []).filter(function (e) { return e.episode_number === en; })[0]) || {};
        if (ep.id == null) { toast('That episode has no library copy to search subtitles for.', 'error'); return; }
        var label = 'S' + String(selectedSeason).padStart(2, '0') + 'E' + String(en).padStart(2, '0');
        window.VideoSubtitles.openManualSearch('episode', ep.id, ep.title || ('Episode ' + en), label,
            { defaultLang: window.VideoSubtitles.effectiveFirstLang('show', data.id) });
    }
    function subSearchMovie() {
        if (!window.VideoSubtitles || !data || data.kind !== 'movie') return;
        window.VideoSubtitles.openManualSearch('movie', data.id, data.title, '',
            { defaultLang: window.VideoSubtitles.effectiveFirstLang('movie', data.id) });
    }

    // ── Live download tracking on episode rows ──
    // Poll /downloads/active while viewing a show; match grabs to episode rows by
    // title+season+episode (the grab's search_ctx) and paint live status. States
    // are re-applied after every episode re-render so a season switch / filter
    // toggle doesn't lose them. Keyed 'season_episode' so all seasons persist.
    function _djson(u) { return fetch(u).then(function (r) { return r.ok ? r.json() : null; }).catch(function () { return null; }); }
    var _dlTimer = null, _dlActive = {}, _dlDone = {}, _dlPrev = {};
    function _dlReset() { _dlActive = {}; _dlDone = {}; _dlPrev = {}; }
    function _dlTrackable() { return !!(data && (data.kind === 'show' || data.kind === 'channel')); }
    // Adaptive cadence: 2.5s only while a download for THIS show is actually
    // in flight; 10s otherwise (still catches a fresh grab quickly) — and
    // nothing at all while the tab is hidden. A flat 2.5s forever was a
    // request every 2.5s for as long as any show page stayed open.
    var _DL_FAST_MS = 2500, _DL_IDLE_MS = 10000, _dlHasActive = false;
    function startDlTracking() {
        stopDlTracking();
        if (!_dlTrackable()) return;
        pollDl();
        _scheduleDl();
    }
    function _scheduleDl() {
        _dlTimer = setTimeout(function () {
            if (!document.hidden) pollDl();
            if (_dlTimer) _scheduleDl();
        }, _dlHasActive ? _DL_FAST_MS : _DL_IDLE_MS);
    }
    function stopDlTracking() { if (_dlTimer) { clearTimeout(_dlTimer); _dlTimer = null; } }
    function pollDl() {
        if (!_dlTrackable() || !root()) { stopDlTracking(); return; }
        var showTitle = data.title;
        var isYt = data.source === 'youtube';
        _djson('/api/video/downloads/active').then(function (d) {
            if (!_dlTrackable() || data.title !== showTitle) return;
            var cur = {};
            ((d && d.downloads) || []).forEach(function (dl) {
                var key;
                var ctx = dl.search_ctx;
                if (typeof ctx === 'string') { try { ctx = JSON.parse(ctx); } catch (e) { ctx = null; } }
                ctx = ctx || {};
                if (isYt) {
                    // channel page: match this channel's video downloads by VIDEO ID
                    if (dl.kind !== 'youtube' || !dl.media_id) return;
                    if (!ytVideoMap[dl.media_id] && !ytFindEp(dl.media_id)) return;
                    key = String(dl.media_id);
                } else {
                    if (String(ctx.title || dl.title) !== String(showTitle)) return;
                    if (ctx.season == null || ctx.episode == null) return;
                    key = ctx.season + '_' + ctx.episode;
                }
                cur[key] = dl;
                if (dl.status === 'completed') {
                    // YouTube: /downloads/active includes ~100 HISTORIC completed
                    // rows — painting those 'done' would stamp ✓ Downloaded on
                    // every recently-downloaded episode. Only a grab we actually
                    // watched run this session paints ✓ Downloaded; history
                    // speaks through ep.owned instead.
                    if (!isYt || _dlActive[key]) _dlDone[key] = 1;
                    delete _dlActive[key];
                }
                else if (dl.status === 'failed' || dl.status === 'cancelled') { delete _dlActive[key]; }
                else { _dlActive[key] = dl; }
            });
            // An active grab that vanished from the list finished + imported → done.
            Object.keys(_dlPrev).forEach(function (key) {
                if (!cur[key] && _dlActive[key]) { _dlDone[key] = 1; delete _dlActive[key]; }
            });
            _dlPrev = cur;
            _dlHasActive = Object.keys(cur).length > 0;   // drives the adaptive cadence
            applyDlStates();
            // Everything settled → stop the interval until the next grab.
            if (!Object.keys(_dlActive).length) stopDlTracking();
        });
    }
    function applyDlStates() {
        var host = q('[data-vd-episodes]'); if (!host) return;
        var isYt = !!(data && data.source === 'youtube');
        var boxes = host.querySelectorAll('[data-vd-ep-get]');
        for (var i = 0; i < boxes.length; i++) {
            var box = boxes[i];
            var key = isYt ? box.getAttribute('data-vd-ep-get')
                           : selectedSeason + '_' + box.getAttribute('data-vd-ep-get');
            var stEl = box.querySelector('[data-vd-ep-dl]');
            box.classList.remove('vd-ep-get--busy', 'vd-ep-get--done');
            if (_dlDone[key]) {
                box.classList.add('vd-ep-get--done');
                if (stEl) stEl.innerHTML = '<span class="vd-ep-dl-txt vd-ep-dl-txt--done">✓ Downloaded</span>';
                // The action buttons stay visible AND usable on a downloaded row
                // (grabEpisodeInline disabled the grab button for its in-flight
                // window; the grab is over, so give it back — e.g. for a re-grab).
                var doneBtns = box.querySelectorAll('.vd-ep-getbtn');
                for (var b = 0; b < doneBtns.length; b++) doneBtns[b].disabled = false;
            } else if (_dlActive[key]) {
                var dl = _dlActive[key], pct = Math.max(0, Math.min(100, dl.progress || 0));
                var label = dl.status === 'downloading' ? (pct + '%') : (dl.status === 'searching' ? 'Searching' : 'Queued');
                box.classList.add('vd-ep-get--busy');
                if (stEl) stEl.innerHTML = '<span class="vd-ep-dl-bar"><span style="width:' + pct + '%"></span></span>' +
                    '<span class="vd-ep-dl-txt">' + esc(label) + '</span>';
            } else if (stEl) { stEl.innerHTML = ''; }
        }
    }

    // The Your library tab (React) borrows the page's own flows rather than
    // re-implementing them: manual search, season grabs, subtitle search, the
    // manage panel. Season actions point the page at that season first, so the
    // Episodes tab and the live download states agree with what was grabbed.
    function _withSeason(n, fn) {
        if (!data || data.kind !== 'show' || !seasonByNum(n)) return;
        if (selectedSeason !== n) { selectedSeason = n; renderSeasonNav(); ensureSeasonEpisodes(); }
        fn(document.createElement('button'));
    }
    var detailBridge = {
        manualSearch: function (scope, season, episode) {
            if (!data || !window.VideoDownload || !VideoDownload.manualSearch) return;
            if (scope === 'movie') {
                VideoDownload.manualSearch({ title: data.title, scope: 'movie', year: data.year,
                    mediaId: data.id, mediaSource: data.source, poster: data.has_poster ? '/api/video/poster/movie/' + data.id : null });
                return;
            }
            if (season != null && selectedSeason !== season) { selectedSeason = season; renderSeasonNav(); ensureSeasonEpisodes(); }
            _openManualSearch(scope === 'episode' ? 'episode' : 'season', episode);
        },
        grabSeason: function (n) { _withSeason(n, grabSeasonInline); },
        wishlistSeason: function (n) { _withSeason(n, wishSeasonInline); },
        clearSeasonFailures: function (n) { _withSeason(n, clearSeasonFailures); },
        subtitleSearch: function (kind, id, title, label) {
            if (!window.VideoSubtitles || !data) return;
            window.VideoSubtitles.openManualSearch(kind, id, title, label,
                { defaultLang: window.VideoSubtitles.effectiveFirstLang(data.kind, data.id) });
        },
        openManage: function () { openManagePanel(); },
        reloadPage: function () {
            if (!data) return;
            if (data.kind === 'movie') loadMovie(currentId, currentSource);
            else if (data.kind === 'show') loadShow(currentId, currentSource);
        },
    };

    function init() {
        window.SoulSyncVideoDetail = detailBridge;
        document.addEventListener('soulsync:video-open-detail', onOpen);
        document.addEventListener('click', onClick);
        // Channel search (debounced) + sort — only act on the youtube controls.
        document.addEventListener('input', function (e) {
            var inp = e.target && e.target.closest && e.target.closest('[data-vd-yt-search]');
            if (!inp || !data || data.source !== 'youtube') return;
            var v = inp.value;
            if (ytSearchTimer) clearTimeout(ytSearchTimer);
            ytSearchTimer = setTimeout(function () { ytFilter.q = v; ytRegroup(true); }, 200);
        });
        document.addEventListener('change', function (e) {
            var sel = e.target && e.target.closest && e.target.closest('[data-vd-yt-sort]');
            if (!sel || !data || data.source !== 'youtube') return;
            ytFilter.sort = sel.value; ytRegroup(true);
        });
        // Kill the billboard trailer (audio!) when navigating to a non-detail page.
        document.addEventListener('soulsync:video-page-shown', function (e) {
            if (e && e.detail !== 'video-movie-detail' && e.detail !== 'video-show-detail') { stopBillboardTrailer(); stopDlTracking(); }
        });
        // A grab started anywhere (inline buttons or the get-modal) — pick up live
        // progress for this show's episode rows.
        document.addEventListener('soulsync:video-download-started', function () {
            if (data && data.kind === 'show') startDlTracking();
        });
        // Keep the movie Get button's "In Wishlist" state fresh — re-check whenever
        // the wishlist changes (e.g. after the Get modal adds/removes this movie).
        document.addEventListener('soulsync:video-wishlist-changed', function () {
            if (currentKind === 'movie' && data) { data._wl_checked = false; renderActions(data); }
        });
        // The profile can arrive (or switch) after this page rendered: canDownload()
        // answers "yes" until the profile is known, so the acquisition buttons may
        // have been built for the wrong profile — a no-download member saw grab +
        // wishlist buttons that the server then 403s, instead of the request
        // buttons. Re-render the permission-dependent UI when the profile lands,
        // but only when the permission outcome actually changed (avatar/name edits
        // fire this event too — they must not collapse open episode panels).
        var _lastCanDl = null;
        function onProfileChanged() {
            var canDl = (typeof canDownload === 'function') ? canDownload() : true;
            if (_lastCanDl === canDl) return;
            _lastCanDl = canDl;
            if (!data || !root()) return;
            renderActions(data);
            renderEpisodes();
        }
        window.addEventListener('ss:webui-profile-context-changed', onProfileChanged);
        // Metadata edited via the Manage panel → re-render the page from the DB
        // (title/genres/summary changed under us). Quiet events (toggles) skip it.
        document.addEventListener('soulsync:video-meta-changed', function (e) {
            var det = e && e.detail;
            if (!data || !det || det.quiet) return;
            var libId = (data.source !== 'tmdb') ? data.id : (data.library_id || null);
            if (det.kind !== data.kind || String(det.id) !== String(libId)) return;
            if (data.kind === 'movie') loadMovie(currentId, currentSource);
            else if (data.kind === 'show') loadShow(currentId, currentSource);
        });
        // Poster changed via the Poster Manager → bust the on-page poster/backdrop
        // (the proxy now serves the new art, but the browser cached the old one).
        document.addEventListener('soulsync:video-poster-changed', function (e) {
            var det = e && e.detail;
            if (!data || !det || det.kind !== data.kind) return;
            var libId = (data.source !== 'tmdb') ? data.id : (data.library_id || null);
            if (String(det.id) !== String(libId)) return;
            data.has_poster = true;
            var cb = '_cb=' + Date.now();
            var poster = q('[data-vd-poster]');
            if (poster) { poster.onload = function () { applyAccent(poster); }; poster.src = '/api/video/poster/' + data.kind + '/' + libId + '?' + cb; }
            var bg = q('[data-vd-backdrop]');
            if (bg && !data.has_backdrop) bg.style.backgroundImage = "url('" + (window.SoulSyncURL?.resolve('/api/video/poster/') || '/api/video/poster/') + data.kind + '/' + libId + '?' + cb + "')";
        });
        document.addEventListener('keydown', function (e) {
            if (e.key === 'Escape') { closePops(); closeTrailer(); closeLightbox(); closeCastModal(); }
            else if (lightboxOpen()) {
                if (e.key === 'ArrowLeft') lightboxStep(-1);
                else if (e.key === 'ArrowRight') lightboxStep(1);
            }
        });
    }

    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init);
    else init();
})();
