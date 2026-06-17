/*
 * SoulSync — Video Wishlist page (isolated).
 *
 * The curated 'get this' list, split by a Movies / TV tab. Movies render as a
 * poster grid; TV groups into collapsible show → season → episode rows with
 * wanted/done roll-ups and a remove (✕) at every level. Server-paged + searchable
 * like the other pages. Reads /api/video/wishlist; removes via /wishlist/remove.
 * Self-contained IIFE, no globals.
 */
(function () {
    'use strict';

    var PAGE_ID = 'video-wishlist';
    var LIMIT = 60;
    var state = { loaded: false, tab: 'movie', search: '', sort: 'added', page: 1,
                  counts: { movie: 0, show: 0, episode: 0 }, ytChannel: 0, ytVideo: 0,
                  showData: {}, showInfo: {} };

    var searchTimer = null;

    function $(s, r) { return (r || document).querySelector(s); }
    function esc(s) {
        return String(s == null ? '' : s)
            .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
    }
    function hueOf(s) { var h = 0, t = String(s || ''); for (var i = 0; i < t.length; i++) h = (h * 31 + t.charCodeAt(i)) >>> 0; return h % 360; }
    var MO = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
    function fmtDate(iso) {
        var p = String(iso || '').split('-');
        if (p.length < 3) return '';
        return MO[(+p[1] || 1) - 1] + ' ' + (+p[2] || 1);
    }

    var STATUS = {
        wanted: ['Wanted', 'vwsh-st--wanted'], searching: ['Searching', 'vwsh-st--searching'],
        downloading: ['Downloading', 'vwsh-st--downloading'], downloaded: ['Done', 'vwsh-st--done'],
        failed: ['Failed', 'vwsh-st--failed'],
    };
    function statusPill(status) {
        var s = STATUS[status] || STATUS.wanted;
        return '<span class="vwsh-st ' + s[1] + '">' + s[0] + '</span>';
    }
    function rmBtn(scope, attrs) {
        return '<button class="vwsh-rm" type="button" title="Remove" aria-label="Remove" ' +
            'data-vwsh-rm="' + scope + '"' + attrs + '>&times;</button>';
    }

    // ── movie card ────────────────────────────────────────────────────────────
    function movieCard(it) {
        var owned = it.library_id != null;
        var art = it.poster_url
            ? '<img class="vwsh-movie-img" src="' + esc(it.poster_url) + '" alt="" loading="lazy" ' +
              'onerror="this.style.display=\'none\'">'
            : '<div class="vwsh-movie-ph">🎬</div>';
        var meta = [it.year, owned ? 'In library' : null].filter(Boolean).join(' · ');
        return '<div class="vwsh-movie" data-vwsh-open-movie="' + esc(it.tmdb_id) +
            '" data-vwsh-src="' + (owned ? 'library' : 'tmdb') + '" data-vwsh-id="' + esc(owned ? it.library_id : it.tmdb_id) + '">' +
            '<div class="vwsh-movie-art">' + art + '<div class="vwsh-movie-scrim"></div>' +
            statusPill(it.status) + rmBtn('movie', ' data-tmdb="' + esc(it.tmdb_id) + '"') + '</div>' +
            '<div class="vwsh-movie-info"><span class="vwsh-movie-title" title="' + esc(it.title) + '">' +
            esc(it.title) + '</span>' + (meta ? '<span class="vwsh-movie-meta">' + esc(meta) + '</span>' : '') +
            '</div></div>';
    }

    // ── show orb (the "Nebula": show→artist, season→album, episode→track) ─────
    function initials(s) {
        var w = String(s || '').replace(/[^A-Za-z0-9 ]/g, '').split(' ').filter(Boolean);
        var i = w.slice(0, 2).map(function (x) { return x[0]; }).join('');
        return (i || String(s || '?').slice(0, 2)).toUpperCase();
    }
    function orbSize(n) { return n >= 10 ? 'orb-lg' : n >= 4 ? 'orb-md' : 'orb-sm'; }

    function nebulaOrb(sh, idx) {
        // Source-aware: a TMDB show opens its show page; a YouTube channel (source
        // 'youtube') opens the in-app channel page. YEAR is the "season", video the
        // "episode" — the data is already shaped that way, so the same render runs.
        var yt = sh.source === 'youtube';
        var src = sh.library_id != null ? 'library' : 'tmdb';
        var openId = sh.library_id != null ? sh.library_id : sh.tmdb_id;
        var openAttrs = yt
            ? 'data-vwsh-open-channel data-yt="' + esc(sh.youtube_id) + '"'
            : 'data-vwsh-open-show data-vwsh-src="' + src + '" data-vwsh-id="' + esc(openId) + '"';
        var hue = hueOf(sh.title);
        var total = sh.wanted || 0;
        var img = sh.poster_url
            ? '<img class="wl-orb-img" src="' + esc(sh.poster_url) + '" alt="" ' +
              'onerror="this.outerHTML=\'<div class=&quot;wl-orb-initials&quot;>' + esc(initials(sh.title)) + '</div>\'">'
            : '<div class="wl-orb-initials">' + esc(initials(sh.title)) + '</div>';
        // Episodes are shown grouped under a clickable season header (header →
        // show/channel page); each episode card SELECTS it (drives the info bar).
        // Season = a poster panel on the LEFT with the episode grid to its RIGHT.
        var seasons = (sh.seasons || []).map(function (se) {
            var n = se.episodes.length;
            var posterUrl = se.poster_url || sh.poster_url || null;
            var thumb = posterUrl ? '<img src="' + esc(posterUrl) + '" alt="">' : '<span class="vwsh-szn-ph">📺</span>';
            var cards = (se.episodes || []).map(function (e) { return epCard(sh, se, e); }).join('');
            var sName = yt ? (se.season_number ? se.season_number : 'Undated') : ('Season ' + se.season_number);
            var sRm = yt
                ? 'data-vwsh-rm="yt-season" data-tmdb="' + esc(sh.tmdb_id) + '" data-s="' + se.season_number + '"'
                : 'data-vwsh-rm="season" data-tmdb="' + esc(sh.tmdb_id) + '" data-s="' + se.season_number + '"';
            return '<div class="vwsh-szn">' +
                '<div class="vwsh-szn-side" ' + openAttrs + ' title="' + (yt ? 'Open channel page' : 'Open show page') + '">' +
                    '<div class="vwsh-szn-poster">' + thumb + '</div>' +
                    '<div class="vwsh-szn-name">' + esc(sName) + '</div>' +
                    '<div class="vwsh-szn-count">' + n + (yt ? ' video' : ' episode') + (n === 1 ? '' : 's') + '</div>' +
                    '<div class="vwsh-szn-go">' + (yt ? 'View channel' : 'View show') + ' &rarr;</div>' +
                    '<button class="vwsh-szn-rm" type="button" ' + sRm + ' title="Remove">&#10005;</button>' +
                '</div>' +
                '<div class="vwsh-ep-grid">' + cards + '</div>' +
            '</div>';
        }).join('');
        var eps = total + (yt ? ' video' : ' episode') + (total === 1 ? '' : 's');
        // --orb-hue on the GROUP so the music orb styles + my cinematic-expand
        // backdrop (--vwsh-poster) both resolve; poster bleeds in only when expanded.
        var gstyle = 'animation-delay:' + Math.min(idx * 45, 700) + 'ms;--orb-hue:' + hue +
            (sh.poster_url ? ";--vwsh-poster:url('" + esc(sh.poster_url) + "')" : '');
        var prog = total ? Math.max(0, Math.min(1, (sh.done || 0) / total)) : 0;   // #4 acquisition progress
        // Header is a 3-column row that FLANKS the poster: synopsis (left) · poster
        // (middle) · cast (right). When collapsed (or no data) the side columns are
        // empty → hidden → just the centered bubble, so the nebula grid is unchanged.
        var showRm = yt
            ? 'data-vwsh-rm="yt-channel" data-yt="' + esc(sh.youtube_id) + '"'
            : 'data-vwsh-rm="show" data-tmdb="' + esc(sh.tmdb_id) + '"';
        return '<div class="wl-orb-group" data-vwsh-group data-vwsh-tmdb="' + esc(sh.tmdb_id) + '" ' +
            'data-vwsh-source="' + (yt ? 'youtube' : 'tmdb') + '" style="' + gstyle + '">' +
            '<button class="wl-orb-remove" type="button" ' + showRm + ' title="Remove">&#10005;</button>' +
            '<div class="vwsh-xhead">' +
                '<div class="vwsh-info-syn" data-vwsh-syn></div>' +
                '<div class="vwsh-xhead-mid">' +
                    '<div class="wl-orb-tooltip">' + esc(sh.title) + '<br><span>' + eps + '</span></div>' +
                    '<div class="wl-orb ' + orbSize(total) + '" data-vwsh-orb style="--vwsh-prog:' + prog + '">' +
                        '<div class="wl-orb-glow"></div>' + img + '<div class="wl-orb-ring"></div>' +
                        '<div class="vwsh-prog"></div>' +
                    '</div>' +
                    '<div class="wl-orb-label" ' + openAttrs + ' title="' + esc(sh.title) + '">' + esc(sh.title) + '</div>' +
                    '<div class="wl-orb-meta">' + eps + (sh.done ? ' · ' + sh.done + ' done' : '') + '</div>' +
                '</div>' +
                '<div class="vwsh-info-cast" data-vwsh-cast></div>' +
            '</div>' +
            '<div class="wl-orb-expanded"><div class="vwsh-seasons">' + seasons + '</div></div>' +
        '</div>';
    }

    // ── info bar: synopsis + clickable cast, contextual to the selected episode ─
    function castBubbles(arr) {
        return (arr || []).slice(0, 8).map(function (c) {
            var photo = c.photo
                ? '<img src="' + esc(c.photo) + '" alt="" loading="lazy" onerror="this.parentNode.classList.add(\'vwsh-cast--ph\')">'
                : '';
            return '<button class="vwsh-cast' + (c.photo ? '' : ' vwsh-cast--ph') + '" type="button" ' +
                'data-vwsh-open-person data-id="' + esc(c.tmdb_id) + '" ' +
                'title="' + esc(c.name) + (c.character ? ' — ' + esc(c.character) : '') + '">' +
                '<span class="vwsh-cast-img"><span class="vwsh-cast-ini">' + esc(initials(c.name)) + '</span>' + photo + '</span>' +
                '<span class="vwsh-cast-name">' + esc(c.name) + '</span></button>';
        }).join('');
    }
    function findEpisode(tmdb, sNum, eNum) {
        var sh = state.showData[tmdb], ep = null;
        if (sh) (sh.seasons || []).forEach(function (se) {
            if (se.season_number === sNum) (se.episodes || []).forEach(function (x) {
                if (x.episode_number === eNum) { ep = x; ep.season_number = sNum; }   // episodes don't carry their season #
            });
        });
        return ep;
    }
    // sel = a selected episode object (episode synopsis + guest cast), or null (show synopsis + show cast).
    function renderInfoBar(group, tmdb, sel) {
        var synEl = group && group.querySelector('[data-vwsh-syn]');
        var castEl = group && group.querySelector('[data-vwsh-cast]');
        if (!synEl || !castEl) return;
        // YouTube: a video carries its own description; there's no cast and no
        // tmdb episode endpoint, so just paint the selected video's synopsis.
        if (group.getAttribute('data-vwsh-source') === 'youtube') {
            if (sel) {
                var yd = fmtDate(sel.air_date);
                synEl.innerHTML = (yd ? '<span class="vwsh-info-eyebrow">' + esc(yd) + '</span>' : '') +
                    esc(sel.overview || 'No description for this video.');
            } else { synEl.innerHTML = ''; }
            castEl.innerHTML = '';
            return;
        }
        var si = state.showInfo[tmdb] || {};
        var eyebrow, overview, castArr;
        if (sel) {
            eyebrow = 'S' + sel.season_number + ' · E' + sel.episode_number;
            overview = sel.overview || 'No synopsis for this episode.';
            // Episode cast = its guest stars (episode-specific) THEN the show regulars,
            // deduped — most episodes have no guest stars, so show the regulars too.
            var seen = {};
            castArr = (sel._guests || []).concat(si.cast || []).filter(function (c) {
                var k = c.tmdb_id || c.name; if (seen[k]) return false; seen[k] = 1; return true;
            });
        } else {
            eyebrow = ''; overview = si.overview || ''; castArr = si.cast || [];
        }
        // Side columns are independent so each hides (:empty) when it has nothing.
        synEl.innerHTML = (eyebrow || overview)
            ? ((eyebrow ? '<span class="vwsh-info-eyebrow">' + esc(eyebrow) + '</span>' : '') + esc(overview)) : '';
        castEl.innerHTML = castArr.length ? castBubbles(castArr) : '';
        // lazily fetch the episode's guest stars, then re-render if still selected
        if (sel && sel._guests === undefined) {
            sel._guests = null;
            fetch('/api/video/episode/' + tmdb + '/' + sel.season_number + '/' + sel.episode_number, { headers: { Accept: 'application/json' } })
                .then(function (r) { return r.ok ? r.json() : null; })
                .then(function (d) {
                    sel._guests = (d && d.guest_stars) || [];
                    var cur = group.querySelector('.vwsh-epc--sel');
                    if (cur && +cur.getAttribute('data-s') === sel.season_number && +cur.getAttribute('data-e') === sel.episode_number)
                        renderInfoBar(group, tmdb, sel);
                })
                .catch(function () { sel._guests = []; });
        }
    }
    // Lazily load the show's synopsis + cast when its orb first expands.
    function loadShowInfo(group) {
        if (!group) return;
        var tmdb = parseInt(group.getAttribute('data-vwsh-tmdb'), 10);
        renderInfoBar(group, tmdb, null);   // paint the View-show button immediately
        if (group.getAttribute('data-vwsh-source') === 'youtube') return;   // no tmdb detail for channels
        if (group.getAttribute('data-vwsh-info-loaded')) return;
        group.setAttribute('data-vwsh-info-loaded', '1');
        var sh = state.showData[tmdb]; if (!sh) return;
        var url = sh.library_id != null ? '/api/video/detail/show/' + sh.library_id : '/api/video/tmdb/show/' + tmdb;
        fetch(url, { headers: { Accept: 'application/json' } })
            .then(function (r) { return r.ok ? r.json() : null; })
            .then(function (d) {
                if (!d) return;
                state.showInfo[tmdb] = { overview: d.overview || '', cast: d.cast || [] };
                if (!group.querySelector('.vwsh-epc--sel')) renderInfoBar(group, tmdb, null);   // unless an episode is selected
            })
            .catch(function () { /* best-effort */ });
    }

    // A single episode card. Clicking it SELECTS the episode (drives the info bar);
    // the "View show" button in the info bar is what navigates.
    function epCard(sh, se, e) {
        var yt = sh.source === 'youtube';
        var t = e.title || (yt ? 'Untitled' : ('Episode ' + e.episode_number));
        var st = STATUS[e.status] ? e.status : 'wanted';
        var date = fmtDate(e.air_date);
        // TMDB shows the SxEx label; a YouTube video shows just its upload date.
        var metaTxt = yt ? (date || 'Video') : ('S' + se.season_number + '·E' + e.episode_number + (date ? ' · ' + esc(date) : ''));
        var thumb = e.still_url
            ? '<span class="vwsh-epc-thumb"><img src="' + esc(e.still_url) + '" alt="" loading="lazy" ' +
              'onerror="this.parentNode.classList.add(\'vwsh-epc-thumb--none\')"></span>'
            : '<span class="vwsh-epc-thumb vwsh-epc-thumb--none"></span>';
        var rm = yt
            ? 'data-vwsh-rm="yt-video" data-id="' + esc(e.source_id) + '"'
            : 'data-vwsh-rm="episode" data-tmdb="' + esc(sh.tmdb_id) + '" data-s="' + se.season_number + '" data-e="' + e.episode_number + '"';
        return '<div class="vwsh-epc" data-vwsh-ep data-tmdb="' + esc(sh.tmdb_id) + '" data-s="' + se.season_number + '" data-e="' + e.episode_number + '"' +
            (yt ? ' data-src-id="' + esc(e.source_id) + '"' : '') + '>' + thumb +
            '<div class="vwsh-epc-body">' +
                '<div class="vwsh-epc-title" title="' + esc(t) + '">' + esc(t) + '</div>' +
                '<div class="vwsh-epc-meta"><span class="vwsh-ep-dot vwsh-ep-dot--' + st + '"></span>' + (yt ? esc(metaTxt) : metaTxt) + '</div>' +
            '</div>' +
            '<button class="vwsh-epc-rm" type="button" ' + rm + ' title="Remove">&#10005;</button>' +
        '</div>';
    }

    function render(items) {
        var grid = $('[data-vwsh-grid]'); if (!grid) return;
        // YouTube uses the SAME nebula as TV (channel=show, year=season, video=episode).
        var nebula = state.tab === 'show' || state.tab === 'youtube';
        grid.classList.toggle('wl-nebula-field', nebula);
        grid.classList.toggle('vwsh-nebula', nebula);   // video-only scope so music wl-* is untouched
        grid.classList.toggle('vwsh-grid--movies', state.tab === 'movie');
        state.showData = {};
        if (nebula) items.forEach(function (sh) { state.showData[sh.tmdb_id] = sh; });   // for the episode area
        grid.innerHTML = nebula
            ? items.map(function (sh, i) { return nebulaOrb(sh, i); }).join('')
            : items.map(movieCard).join('');
    }

    // ── counts / badges / pager ───────────────────────────────────────────────
    function setCounts(counts) {
        state.counts = { movie: (counts && counts.movie) || 0, show: (counts && counts.show) || 0,
                         episode: (counts && counts.episode) || 0 };
        var cm = $('[data-vwsh-count-movie]'); if (cm) cm.textContent = state.counts.movie;
        var cs = $('[data-vwsh-count-show]'); if (cs) cs.textContent = state.counts.show;
        updateBadges(counts && counts.total != null ? counts.total : (state.counts.movie + state.counts.episode));
        updateSub();
    }
    function setYtCounts(counts) {
        state.ytChannel = (counts && counts.channel) || 0;
        state.ytVideo = (counts && counts.video) || 0;
        var cy = $('[data-vwsh-count-youtube]'); if (cy) cy.textContent = state.ytVideo;
        updateSub();
    }
    // Keep the YouTube tab badge fresh without switching to the tab.
    function refreshYtCount() {
        fetch('/api/video/youtube/channels', { headers: { Accept: 'application/json' } })
            .then(function (r) { return r.ok ? r.json() : null; })
            .then(function (d) { if (d && d.counts) setYtCounts(d.counts); })
            .catch(function () { /* ignore */ });
    }
    function updateSub() {
        var el = $('[data-vwsh-sub]'); if (!el) return;
        var c = state.counts;
        if (state.tab === 'youtube') {
            el.textContent = state.ytChannel + ' channel' + (state.ytChannel === 1 ? '' : 's') +
                ' · ' + state.ytVideo + ' video' + (state.ytVideo === 1 ? '' : 's');
            return;
        }
        el.textContent = state.tab === 'show'
            ? c.show + ' show' + (c.show === 1 ? '' : 's') + ' · ' + c.episode + ' episode' + (c.episode === 1 ? '' : 's')
            : c.movie + ' movie' + (c.movie === 1 ? '' : 's');
    }
    function updateBadges(total) {
        var n = total || 0;
        ['[data-video-wishlist-badge]', '[data-video-badge="wishlist"]'].forEach(function (sel) {
            document.querySelectorAll(sel).forEach(function (b) {
                b.textContent = n; b.classList.toggle('hidden', !n);
            });
        });
    }
    function refreshBadge() {
        fetch('/api/video/wishlist/counts', { headers: { Accept: 'application/json' } })
            .then(function (r) { return r.ok ? r.json() : null; })
            .then(function (d) { if (d && d.success) updateBadges(d.total); })
            .catch(function () { /* ignore */ });
    }
    function updatePagination(p) {
        var box = $('[data-vwsh-pagination]'), prev = $('[data-vwsh-prev]'),
            next = $('[data-vwsh-next]'), info = $('[data-vwsh-pageinfo]');
        if (!box) return;
        if (!p || p.total_pages <= 1) { box.classList.add('hidden'); return; }
        if (prev) prev.disabled = !p.has_prev;
        if (next) next.disabled = !p.has_next;
        if (info) info.textContent = 'Page ' + p.page + ' of ' + p.total_pages;
        box.classList.remove('hidden');
    }
    function updateEmpty(total) {
        var empty = $('[data-vwsh-empty]'); if (empty) empty.classList.toggle('hidden', total > 0);
        var et = $('[data-vwsh-empty-title]');
        if (et && total === 0) {
            et.textContent = state.search ? 'No matches'
                : state.tab === 'movie' ? 'No movies on your wishlist yet'
                : state.tab === 'show' ? 'No TV episodes on your wishlist yet'
                : 'No channels followed yet — paste a channel link on the Search page';
        }
    }

    function loadYoutube() {
        var ld = $('[data-vwsh-loading]'); if (ld) ld.classList.remove('hidden');
        var params = new URLSearchParams({ search: state.search, sort: state.sort, page: state.page, limit: LIMIT });
        fetch('/api/video/youtube/wishlist?' + params.toString(), { headers: { Accept: 'application/json' } })
            .then(function (r) { return r.ok ? r.json() : null; })
            .then(function (d) {
                if (ld) ld.classList.add('hidden');
                if (!d || !d.success) { render([]); updatePagination(null); updateEmpty(0); return; }
                setYtCounts(d.counts);
                var p = d.pagination || { page: 1, total_pages: 1, total_count: (d.items || []).length };
                state.page = p.page;
                render(d.items || []);
                updatePagination(p);
                updateEmpty(p.total_count);
            })
            .catch(function () { if (ld) ld.classList.add('hidden'); render([]); updatePagination(null); updateEmpty(0); });
    }

    function load() {
        state.loaded = true;
        if (state.tab === 'youtube') { loadYoutube(); return; }
        var ld = $('[data-vwsh-loading]'); if (ld) ld.classList.remove('hidden');
        var params = new URLSearchParams({ kind: state.tab, search: state.search, sort: state.sort, page: state.page, limit: LIMIT });
        fetch('/api/video/wishlist?' + params.toString(), { headers: { Accept: 'application/json' } })
            .then(function (r) { return r.ok ? r.json() : null; })
            .then(function (d) {
                if (ld) ld.classList.add('hidden');
                if (!d || !d.success) { render([]); updatePagination(null); updateEmpty(0); return; }
                setCounts(d.counts);
                var p = d.pagination || { page: 1, total_pages: 1, total_count: (d.items || []).length };
                state.page = p.page;
                render(d.items || []);
                updatePagination(p);
                updateEmpty(p.total_count);
                maybeBackfillArt(d.items || []);
            })
            .catch(function () { if (ld) ld.classList.add('hidden'); render([]); updatePagination(null); updateEmpty(0); });
    }

    // Rows added before art-capture have no episode still / season poster — fetch
    // them once (cheap: one tmdb_season call per show/season server-side), reload.
    var artBackfilled = false;
    function maybeBackfillArt(items) {
        if (state.tab !== 'show' || artBackfilled) return;
        var missing = (items || []).some(function (sh) {
            return (sh.seasons || []).some(function (se) {
                return !se.poster_url || (se.episodes || []).some(function (e) { return !e.still_url || !e.overview; });
            });
        });
        if (!missing) return;
        artBackfilled = true;
        fetch('/api/video/wishlist/backfill-art', { method: 'POST', headers: { Accept: 'application/json' } })
            .then(function (r) { return r.ok ? r.json() : null; })
            .then(function (res) { if (res && res.updated > 0) load(); })
            .catch(function () { /* best-effort */ });
    }

    function setTab(tab) {
        if (tab !== 'movie' && tab !== 'show' && tab !== 'youtube') return;
        state.tab = tab; state.page = 1; state.search = '';
        var si = $('[data-vwsh-search]'); if (si) si.value = '';
        var tabs = document.querySelectorAll('[data-vwsh-tab]');
        for (var i = 0; i < tabs.length; i++)
            tabs[i].classList.toggle('vwsh-tab--on', tabs[i].getAttribute('data-vwsh-tab') === tab);
        load();
    }

    // ── remove (TMDB scopes via /wishlist/remove; YouTube scopes via youtube) ──
    function doRemove(btn) {
        var scope = btn.getAttribute('data-vwsh-rm');
        btn.disabled = true;
        var after = function () {
            if (typeof showToast === 'function') showToast('Removed from wishlist', 'info');
            load();
        };
        var afterYt = function () { after(); document.dispatchEvent(new CustomEvent('soulsync:video-wishlist-changed')); };
        var fail = function () { btn.disabled = false; };

        if (scope === 'yt-video') {
            VideoYoutube.removeWish('video', btn.getAttribute('data-id')).then(afterYt).catch(fail); return;
        }
        if (scope === 'yt-channel') {   // remove the channel's videos AND unfollow it
            var cid = btn.getAttribute('data-yt');
            VideoYoutube.unfollow(cid).then(function () { return VideoYoutube.removeWish('channel', cid); })
                .then(afterYt).catch(fail); return;
        }
        if (scope === 'yt-season') {    // a "year" = remove every wished video in it
            var sh = state.showData[parseInt(btn.getAttribute('data-tmdb'), 10)];
            var yr = parseInt(btn.getAttribute('data-s'), 10), ids = [];
            if (sh) (sh.seasons || []).forEach(function (se) {
                if (se.season_number === yr) (se.episodes || []).forEach(function (ep) { if (ep.source_id) ids.push(ep.source_id); });
            });
            if (!ids.length) { fail(); return; }
            Promise.all(ids.map(function (id) { return VideoYoutube.removeWish('video', id); })).then(afterYt).catch(fail);
            return;
        }

        var body = { scope: scope, tmdb_id: parseInt(btn.getAttribute('data-tmdb'), 10) };
        if (btn.hasAttribute('data-s')) body.season_number = parseInt(btn.getAttribute('data-s'), 10);
        if (btn.hasAttribute('data-e')) body.episode_number = parseInt(btn.getAttribute('data-e'), 10);
        fetch('/api/video/wishlist/remove', { method: 'POST', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(body) })
            .then(function (r) { return r.ok ? r.json() : null; })
            .then(function (d) { if (!d || !d.success) { fail(); return; } after(); })
            .catch(fail);
    }

    function onGridClick(e) {
        var rm = e.target.closest('[data-vwsh-rm]');
        if (rm) { e.preventDefault(); e.stopPropagation(); doRemove(rm); return; }
        if (e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;
        var openCh = e.target.closest('[data-vwsh-open-channel]');
        if (openCh) {   // YouTube channel → in-app channel page
            document.dispatchEvent(new CustomEvent('soulsync:video-open-detail', {
                detail: { kind: 'channel', source: 'youtube', id: openCh.getAttribute('data-yt') } }));
            return;
        }
        var open = e.target.closest('[data-vwsh-open-show], [data-vwsh-open-movie], [data-vwsh-open-person]');
        if (open) {
            var person = open.hasAttribute('data-vwsh-open-person');
            document.dispatchEvent(new CustomEvent('soulsync:video-open-detail', {
                detail: { kind: person ? 'person' : (open.hasAttribute('data-vwsh-open-show') ? 'show' : 'movie'),
                          id: parseInt(person ? open.getAttribute('data-id') : open.getAttribute('data-vwsh-id'), 10),
                          source: person ? 'tmdb' : (open.getAttribute('data-vwsh-src') || 'tmdb') },
            }));
            return;
        }
        var epc = e.target.closest('[data-vwsh-ep]');
        if (epc) {   // episode → SELECT it (drives the info bar); click again to deselect
            var eg = epc.closest('.wl-orb-group');
            var etmdb = parseInt(epc.getAttribute('data-tmdb'), 10);
            var eWasSel = epc.classList.contains('vwsh-epc--sel');
            if (eg) { var es = eg.querySelectorAll('.vwsh-epc'); for (var j = 0; j < es.length; j++) es[j].classList.remove('vwsh-epc--sel'); }
            if (eWasSel) { renderInfoBar(eg, etmdb, null); return; }   // back to show synopsis + cast
            epc.classList.add('vwsh-epc--sel');
            renderInfoBar(eg, etmdb, findEpisode(etmdb, parseInt(epc.getAttribute('data-s'), 10), parseInt(epc.getAttribute('data-e'), 10)));
            return;
        }
        var orb = e.target.closest('[data-vwsh-orb]');
        if (orb) {   // show → reveal seasons + lazily load the synopsis/cast info bar
            var g = orb.closest('.wl-orb-group');
            if (g && g.classList.toggle('expanded')) loadShowInfo(g);
        }
    }

    function wire() {
        var tabs = document.querySelectorAll('[data-vwsh-tab]');
        for (var i = 0; i < tabs.length; i++) (function (b) {
            b.addEventListener('click', function () { setTab(b.getAttribute('data-vwsh-tab')); });
        })(tabs[i]);
        var grid = $('[data-vwsh-grid]'); if (grid) grid.addEventListener('click', onGridClick);
        var search = $('[data-vwsh-search]');
        if (search) search.addEventListener('input', function () {
            if (searchTimer) clearTimeout(searchTimer);
            searchTimer = setTimeout(function () { state.search = search.value.trim(); state.page = 1; load(); }, 250);
        });
        var sortSel = $('[data-vwsh-sort]');
        if (sortSel) sortSel.addEventListener('change', function () { state.sort = sortSel.value; state.page = 1; load(); });
        var prev = $('[data-vwsh-prev]');
        if (prev) prev.addEventListener('click', function () { if (state.page > 1) { state.page--; load(); } });
        var next = $('[data-vwsh-next]');
        if (next) next.addEventListener('click', function () { state.page++; load(); });
        // Adds elsewhere (the get-modal) refresh the badge + page if visible.
        document.addEventListener('soulsync:video-wishlist-changed', function () {
            var g = $('[data-vwsh-grid]');
            if (g && g.offsetParent !== null) load(); else { refreshBadge(); refreshYtCount(); }
        });
    }

    function onShown(e) { if (e && e.detail === PAGE_ID) { state.page = 1; load(); refreshYtCount(); } }

    function init() {
        wire();
        document.addEventListener('soulsync:video-page-shown', onShown);
        refreshBadge();
        refreshYtCount();
    }

    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init);
    else init();
})();
