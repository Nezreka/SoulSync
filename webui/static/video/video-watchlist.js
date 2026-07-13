/*
 * SoulSync — Video Watchlist page (isolated).
 *
 * The shows + people you follow, split by a Shows / People tab switcher.
 * Server-paged + searchable like the library (only a page of cards/posters
 * renders at once). Reads /api/video/watchlist?kind=&search=&page=&limit=.
 * Cards reuse the shared VideoWatchlist eye-button (reads as "watched" here;
 * un-follows on click, with a confirm).
 */
(function () {
    'use strict';

    var PAGE_ID = 'video-watchlist';
    var LIMIT = 60;
    var state = { loaded: false, tab: 'show', search: '', sort: 'default', page: 1,
                  counts: { show: 0, person: 0, studio: 0 }, channelCount: 0 };
    var searchTimer = null;

    // A followed YouTube channel card. Clicking it opens the in-app channel page
    // (like any show/movie); the ✕ unfollows.
    function channelCard(ch) {
        var av = window.VideoYoutube ? VideoYoutube.avatar(ch, 'vyt-wcard-avatar') : '';
        var n = ch.video_count || 0;   // remembered catalog size (fills in as enriched)
        var meta = n > 0 ? (n + ' video' + (n === 1 ? '' : 's')) : 'Channel';
        return '<div class="vyt-wcard" data-vyt-open-channel="' + esc(ch.youtube_id) + '" title="Open channel">' +
            '<div class="vyt-wcard-art">' + av + '</div>' +
            '<button class="vyt-wcard-cog" type="button" data-vyt-wsettings="' + esc(ch.youtube_id) +
                '" data-kind="channel" data-title="' + esc(ch.title) + '" title="Channel settings">&#9881;</button>' +
            '<button class="vyt-wcard-unfollow" type="button" data-vyt-wunfollow="' + esc(ch.youtube_id) +
                '" title="Unfollow">&#10005;</button>' +
            '<div class="vyt-wcard-info"><span class="vyt-wcard-title" title="' + esc(ch.title) + '">' +
                esc(ch.title) + '</span><span class="vyt-wcard-meta">' + esc(meta) + '</span></div></div>';
    }

    // Followed playlists sit beside channels in the same grid; the ✕ unfollows.
    function playlistCard(pl) {
        var av = window.VideoYoutube
            ? VideoYoutube.avatar({ poster_url: pl.poster_url, title: pl.title }, 'vyt-wcard-avatar') : '';
        return '<div class="vyt-wcard vyt-wcard--pl" data-vyt-open-playlist="' + esc(pl.playlist_id) + '" title="Open playlist">' +
            '<div class="vyt-wcard-art">' + av + '<span class="vyt-wcard-pl-ic" aria-hidden="true">▤</span></div>' +
            '<button class="vyt-wcard-cog" type="button" data-vyt-wsettings="' + esc(pl.playlist_id) +
                '" data-kind="playlist" data-title="' + esc(pl.title) + '" title="Playlist settings">&#9881;</button>' +
            '<button class="vyt-wcard-unfollow" type="button" data-vyt-wunfollow-playlist="' + esc(pl.playlist_id) +
                '" title="Unfollow">&#10005;</button>' +
            '<div class="vyt-wcard-info"><span class="vyt-wcard-title" title="' + esc(pl.title) + '">' +
                esc(pl.title) + '</span><span class="vyt-wcard-meta">' +
                (pl.video_count > 0 ? esc(pl.video_count + ' video' + (pl.video_count === 1 ? '' : 's')) : 'Playlist') +
            '</span></div></div>';
    }

    // A followed studio card — a bright logo tile (studio logos are dark artwork) with a
    // back-catalog cog + the shared follow eye; opens the studio detail page.
    function studioCard(it) {
        var logo = it.poster_url
            ? '<img class="vwlp-studio-logo" src="' + esc(it.poster_url) + '" alt="" loading="lazy" onerror="this.style.display=\'none\'">'
            : '<span class="vwlp-studio-ph">&#127902;</span>';
        var eye = wlBtn({ kind: 'studio', tmdbId: it.tmdb_id, title: it.title, poster: it.poster_url });
        var cog = '<button type="button" data-vwlp-ssettings="' + esc(it.tmdb_id) + '" data-title="' + esc(it.title) + '" ' +
            'title="Back-catalog settings" aria-label="Back-catalog settings" class="vwlp-studio-cog">&#9881;</button>';
        return '<a class="vwlp-card vwlp-card--studio" href="/video-detail/tmdb/studio/' + esc(it.tmdb_id) + '" ' +
            'data-vwlp-open="studio" data-vwlp-source="tmdb" data-vwlp-openid="' + esc(it.tmdb_id) + '">' +
            '<div class="vwlp-studio-logo-wrap">' + logo + cog + eye + '</div>' +
            '<div class="vwlp-card-info"><span class="vwlp-card-title" title="' + esc(it.title) + '">' +
            esc(it.title) + '</span></div></a>';
    }

    function $(s, r) { return (r || document).querySelector(s); }
    function esc(s) {
        return String(s == null ? '' : s)
            .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
    }
    function wlBtn(opts) { return (window.VideoWatchlist) ? VideoWatchlist.btn(opts) : ''; }

    function statusPill(status) {
        var s = String(status == null ? '' : status).trim().toLowerCase();
        if (!s) return '';
        if (['ended', 'canceled', 'cancelled', 'completed'].indexOf(s) >= 0)
            return '<span class="vwlp-pill vwlp-pill--ended">Ended</span>';
        if (s.indexOf('return') >= 0 || s === 'continuing')
            return '<span class="vwlp-pill vwlp-pill--airing">Airing</span>';
        if (s === 'upcoming' || s.indexOf('production') >= 0 || s.indexOf('planned') >= 0 || s === 'pilot')
            return '<span class="vwlp-pill vwlp-pill--soon">Upcoming</span>';
        return '<span class="vwlp-pill">' + esc(status) + '</span>';
    }

    function cardHTML(it, kind) {
        // SPA open target: library shows open by library id ('library' source);
        // people + un-owned shows open by tmdb id ('tmdb').
        var source = (kind === 'show' && it.library_id) ? 'library' : 'tmdb';
        var openId = source === 'library' ? it.library_id : it.tmdb_id;
        var href = '/video-detail/' + source + '/' + kind + '/' + openId;
        var ph = kind === 'person' ? '👤' : '📺';   // 👤 / 📺
        var art = it.poster_url
            ? '<img class="vwlp-card-img" src="' + esc(it.poster_url) + '" alt="" loading="lazy" ' +
              'onload="this.classList.add(\'vwlp-loaded\')" onerror="this.style.display=\'none\'">'
            : '<div class="vwlp-card-ph">' + ph + '</div>';
        var btn = window.VideoGet
            ? VideoGet.cardButton({ kind: kind, tmdbId: it.tmdb_id, libraryId: it.library_id,
                title: it.title, poster: it.poster_url, status: it.status,
                source: it.library_id ? 'library' : 'tmdb' })
            : wlBtn({ kind: kind, tmdbId: it.tmdb_id, title: it.title, poster: it.poster_url, libraryId: it.library_id });
        var pill = kind === 'show' ? statusPill(it.status) : '';
        var meta = (kind === 'show' && it.episode_count)
            ? '<span class="vwlp-card-meta">' + (it.owned_count || 0) + '/' + it.episode_count + ' eps</span>' : '';
        // People get a settings cog (like followed channels) to set their back-catalog window.
        var cog = kind === 'person'
            ? '<button type="button" data-vwlp-psettings="' + it.tmdb_id + '" data-title="' + esc(it.title) + '" ' +
              'title="Back-catalog settings" aria-label="Back-catalog settings" ' +
              'style="position:absolute;top:8px;right:8px;z-index:4;width:30px;height:30px;border:none;border-radius:9px;' +
              'background:rgba(0,0,0,.55);color:#fff;font-size:15px;line-height:1;cursor:pointer;display:flex;' +
              'align-items:center;justify-content:center;backdrop-filter:blur(4px);">&#9881;</button>'
            : '';
        return '<a class="vwlp-card' + (kind === 'person' ? ' vwlp-card--person' : '') + '" href="' + href + '" ' +
            'data-vwlp-open="' + kind + '" data-vwlp-source="' + source + '" data-vwlp-openid="' + esc(openId) + '">' +
            '<div class="vwlp-card-art">' + art + '<div class="vwlp-card-scrim"></div>' + pill + cog + btn + '</div>' +
            '<div class="vwlp-card-info"><span class="vwlp-card-title" title="' + esc(it.title) + '">' +
            esc(it.title) + '</span>' + meta + '</div></a>';
    }

    function updateNavBadge(counts) {
        var b = $('[data-video-watchlist-badge]'); if (!b) return;
        var n = counts ? ((counts.show || 0) + (counts.person || 0) + (counts.studio || 0)) : 0;
        b.textContent = n;
        b.classList.toggle('hidden', !n);
    }
    function refreshBadge() {
        fetch('/api/video/watchlist/counts', { headers: { Accept: 'application/json' } })
            .then(function (r) { return r.ok ? r.json() : null; })
            .then(function (d) { if (d && d.success) updateNavBadge(d); })
            .catch(function () { /* ignore */ });
    }

    function setCounts(counts) {
        state.counts = { show: (counts && counts.show) || 0, person: (counts && counts.person) || 0,
                         studio: (counts && counts.studio) || 0 };
        var cs = $('[data-vwlp-count-show]'); if (cs) cs.textContent = state.counts.show;
        var cp = $('[data-vwlp-count-person]'); if (cp) cp.textContent = state.counts.person;
        var cst = $('[data-vwlp-count-studio]'); if (cst) cst.textContent = state.counts.studio;
        updateNavBadge(state.counts);
    }

    function updatePagination(p) {
        var box = $('[data-vwlp-pagination]'), prev = $('[data-vwlp-prev]'),
            next = $('[data-vwlp-next]'), info = $('[data-vwlp-pageinfo]');
        if (!box) return;
        if (!p || p.total_pages <= 1) { box.classList.add('hidden'); return; }
        if (prev) prev.disabled = !p.has_prev;
        if (next) next.disabled = !p.has_next;
        if (info) info.textContent = 'Page ' + p.page + ' of ' + p.total_pages;
        box.classList.remove('hidden');
    }

    function updateEmpty(total) {
        var empty = $('[data-vwlp-empty]');
        if (empty) empty.classList.toggle('hidden', total > 0);
        var et = $('[data-vwlp-empty-title]');
        if (et && total === 0) {
            et.textContent = state.search ? 'No matches'
                : state.tab === 'show' ? 'No shows on your watchlist yet'
                : state.tab === 'person' ? 'No people on your watchlist yet'
                : state.tab === 'studio' ? 'No studios followed yet — follow one from its studio page'
                : 'No channels followed yet — paste a channel link on the Search page';
        }
    }

    function render(items) {
        var grid = $('[data-vwlp-grid]');
        if (state.tab === 'channel') {
            grid.classList.add('vyt-wgrid');
            grid.innerHTML = items.map(function (it) { return it.playlist_id ? playlistCard(it) : channelCard(it); }).join('');
            return;
        }
        grid.classList.remove('vyt-wgrid');
        // Everything on this page is watched — seed the shared cache so the eyes
        // paint "watched" with no flash.
        if (window.VideoWatchlist) {
            items.forEach(function (it) { VideoWatchlist._watched[state.tab][it.tmdb_id] = true; });
        }
        if (grid) {
            var renderer = state.tab === 'studio'
                ? studioCard : function (it) { return cardHTML(it, state.tab); };
            grid.innerHTML = items.map(renderer).join('');
            if (window.VideoWatchlist) VideoWatchlist.hydrate(grid);
        }
    }

    // Followed YouTube channels live on their own endpoint, not /watchlist.
    function loadChannels() {
        var ld = $('[data-vwlp-loading]'); if (ld) ld.classList.remove('hidden');
        fetch('/api/video/youtube/channels', { headers: { Accept: 'application/json' } })
            .then(function (r) { return r.ok ? r.json() : null; })
            .then(function (d) {
                if (ld) ld.classList.add('hidden');
                var all = ((d && d.channels) || []).concat((d && d.playlists) || []);   // channels + playlists
                state.channelCount = all.length;
                var cc = $('[data-vwlp-count-channel]'); if (cc) cc.textContent = all.length;
                render(all);
                updatePagination(null);
                updateEmpty(all.length);
            })
            .catch(function () { if (ld) ld.classList.add('hidden'); render([]); updateEmpty(0); });
    }

    function load() {
        state.loaded = true;
        if (state.tab === 'channel') { loadChannels(); return; }
        var ld = $('[data-vwlp-loading]'); if (ld) ld.classList.remove('hidden');
        var params = new URLSearchParams({
            kind: state.tab, search: state.search, sort: state.sort, page: state.page, limit: LIMIT });
        fetch('/api/video/watchlist?' + params.toString(), { headers: { Accept: 'application/json' } })
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
            })
            .catch(function () { if (ld) ld.classList.add('hidden'); render([]); updatePagination(null); updateEmpty(0); });
    }

    function setTab(tab) {
        if (tab !== 'show' && tab !== 'person' && tab !== 'channel' && tab !== 'studio') return;
        state.tab = tab; state.page = 1;
        var tabs = document.querySelectorAll('[data-vwlp-tab]');
        for (var i = 0; i < tabs.length; i++)
            tabs[i].classList.toggle('vwlp-tab--on', tabs[i].getAttribute('data-vwlp-tab') === tab);
        // the subscription-import button is channel-specific (follows are channels/playlists)
        var imp = document.querySelector('[data-vwlp-import]');
        if (imp) imp.hidden = tab !== 'channel';
        // studio-family picker is studio-specific
        var fam = document.querySelector('[data-vwlp-families]');
        if (fam) fam.hidden = tab !== 'studio';
        load();
    }

    // A removal anywhere → if we're showing the watchlist, reload the page so the
    // un-followed card drops and counts/pagination stay correct.
    function onChanged() {
        var grid = $('[data-vwlp-grid]');
        if (grid && grid.offsetParent !== null) load();   // visible → reload (refreshes badge via setCounts)
        else refreshBadge();                              // not visible → keep the nav badge current
    }

    // ── per-follow back-catalog settings modal (person + studio; mirrors the channel cog) ──
    // kind: 'person' | 'studio' — same window control, kind-specific copy + endpoint.
    var _LB_NOUN = { person: { fallback: 'Person', films: 'their films', you: 'them' },
                     studio: { fallback: 'Studio', films: "this studio's films", you: 'it' } };
    function openLookbackSettings(kind, tmdbId, title) {
        fetch('/api/video/watchlist/' + kind + '/' + tmdbId + '/settings', { headers: { Accept: 'application/json' } })
            .then(function (r) { return r.ok ? r.json() : null; })
            .then(function (d) {
                if (!d || !d.success) { if (typeof showToast === 'function') showToast('Could not load settings', 'error'); return; }
                renderLookbackSettings(kind, tmdbId, title, d.settings || {});
            })
            .catch(function () { if (typeof showToast === 'function') showToast('Could not load settings', 'error'); });
    }

    function renderLookbackSettings(kind, tmdbId, title, s) {
        var noun = _LB_NOUN[kind] || _LB_NOUN.person;
        var OPTS = [{ l: 'Forward only', v: 0 }, { l: 'Last 1 year', v: 1 }, { l: 'Last 2 years', v: 2 },
                    { l: 'Last 3 years', v: 3 }, { l: 'Last 5 years', v: 5 }, { l: 'Last 10 years', v: 10 },
                    { l: 'Everything', v: -1 }];
        var sel = (s.lookback_years == null) ? 0 : parseInt(s.lookback_years, 10);
        var followed = String(s.date_added || '').slice(0, 10);
        function optHTML(o) {
            var on = o.v === sel;
            return '<button type="button" data-pset-v="' + o.v + '" style="text-align:left;padding:11px 13px;border-radius:10px;' +
                'cursor:pointer;font-size:13px;font-weight:700;color:#fff;border:1px solid ' +
                (on ? 'rgb(var(--accent-rgb))' : 'rgba(255,255,255,.12)') + ';background:' +
                (on ? 'rgba(var(--accent-rgb),.18)' : 'rgba(255,255,255,.03)') + ';">' + o.l + '</button>';
        }
        var ov = document.createElement('div');
        ov.style.cssText = 'position:fixed;inset:0;z-index:9999;background:rgba(0,0,0,.6);display:flex;' +
            'align-items:center;justify-content:center;padding:20px;';
        ov.innerHTML =
            '<div style="width:min(430px,100%);background:#15161c;border:1px solid rgba(255,255,255,.1);' +
                'border-radius:16px;padding:22px;box-shadow:0 24px 60px rgba(0,0,0,.5);">' +
                '<div style="font-size:11px;font-weight:800;letter-spacing:.05em;text-transform:uppercase;color:rgba(255,255,255,.45);">Back-catalog window</div>' +
                '<h3 style="margin:4px 0 2px;font-size:19px;color:#fff;">' + esc(title || noun.fallback) + '</h3>' +
                '<p style="margin:0 0 16px;font-size:12.5px;line-height:1.55;color:rgba(255,255,255,.55);">' +
                    'How far back to wishlist ' + noun.films + '. New &amp; upcoming films are always followed either way' +
                    (followed ? ' — you followed ' + noun.you + ' on <b>' + esc(followed) + '</b>.' : '.') + '</p>' +
                '<div data-pset-opts style="display:grid;grid-template-columns:1fr 1fr;gap:8px;">' +
                    OPTS.map(optHTML).join('') + '</div>' +
                '<div style="display:flex;justify-content:flex-end;gap:8px;margin-top:20px;">' +
                    '<button type="button" data-pset-cancel style="padding:9px 16px;border-radius:10px;border:1px solid rgba(255,255,255,.14);background:transparent;color:rgba(255,255,255,.8);font-weight:700;cursor:pointer;">Cancel</button>' +
                    '<button type="button" data-pset-save style="padding:9px 18px;border-radius:10px;border:none;background:rgb(var(--accent-rgb));color:#fff;font-weight:800;cursor:pointer;">Save</button>' +
                '</div></div>';
        document.body.appendChild(ov);
        function close() { if (ov.parentNode) ov.parentNode.removeChild(ov); document.removeEventListener('keydown', onKey); }
        function onKey(e) { if (e.key === 'Escape') close(); }
        document.addEventListener('keydown', onKey);
        ov.addEventListener('click', function (e) {
            if (e.target === ov || e.target.closest('[data-pset-cancel]')) { close(); return; }
            var opt = e.target.closest('[data-pset-v]');
            if (opt) {
                sel = parseInt(opt.getAttribute('data-pset-v'), 10);
                Array.prototype.forEach.call(ov.querySelectorAll('[data-pset-v]'), function (b) {
                    var on = parseInt(b.getAttribute('data-pset-v'), 10) === sel;
                    b.style.border = '1px solid ' + (on ? 'rgb(var(--accent-rgb))' : 'rgba(255,255,255,.12)');
                    b.style.background = on ? 'rgba(var(--accent-rgb),.18)' : 'rgba(255,255,255,.03)';
                });
                return;
            }
            var save = e.target.closest('[data-pset-save]');
            if (save) {
                save.disabled = true;
                fetch('/api/video/watchlist/' + kind + '/' + tmdbId + '/settings', {
                    method: 'POST', headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ lookback_years: sel }) })
                    .then(function (r) { return r.ok ? r.json() : null; })
                    .then(function (res) {
                        if (res && res.success) {
                            if (typeof showToast === 'function') showToast('Saved — the next scan applies it', 'success');
                            close();
                        } else { save.disabled = false; if (typeof showToast === 'function') showToast('Could not save', 'error'); }
                    })
                    .catch(function () { save.disabled = false; if (typeof showToast === 'function') showToast('Could not save', 'error'); });
            }
        });
    }

    // ── studio families picker (curated groups; members are followed individually) ──────
    // A family is pure convenience: following it just adds each member as its own studio
    // follow, and every member has its own toggle — you can follow only Pixar and skip the
    // rest of Disney. Nothing here bundles or forces the whole group.
    function openStudioFamilies() {
        fetch('/api/video/studio/presets', { headers: { Accept: 'application/json' } })
            .then(function (r) { return r.ok ? r.json() : null; })
            .then(function (d) {
                if (!d || !d.success) { if (typeof showToast === 'function') showToast('Could not load studio families', 'error'); return; }
                renderStudioFamilies(d.presets || []);
            })
            .catch(function () { if (typeof showToast === 'function') showToast('Could not load studio families', 'error'); });
    }

    function renderStudioFamilies(presets) {
        // followed-state lives in the member objects; we mutate + re-render on each toggle.
        function memberHTML(fam, m) {
            var on = !!m.followed;
            var logo = m.logo
                ? '<img src="' + esc(m.logo) + '" alt="" style="max-width:100%;max-height:26px;object-fit:contain;" onerror="this.replaceWith(document.createTextNode(\'' + esc(m.name).replace(/'/g, '') + '\'))">'
                : esc(m.name);
            return '<button type="button" data-fam-mem="' + esc(fam.id) + ':' + esc(m.tmdb_id) + '" ' +
                'title="' + esc(m.name) + (on ? ' — following (click to unfollow)' : ' — click to follow') + '" ' +
                'style="position:relative;display:flex;align-items:center;justify-content:center;min-height:52px;padding:8px 12px;' +
                'border-radius:11px;cursor:pointer;border:1.5px solid ' + (on ? 'rgb(var(--accent-rgb))' : 'rgba(255,255,255,.12)') +
                ';background:' + (on ? 'linear-gradient(160deg,#fafafe,#d9dbe6)' : 'rgba(255,255,255,.04)') + ';">' +
                (on ? '' : '') + logo +
                (on ? '<span style="position:absolute;top:4px;right:5px;font-size:11px;font-weight:900;color:#16a34a;">✓</span>' : '') +
                '</button>';
        }
        function familyHTML(fam) {
            var total = fam.members.length;
            var followedN = fam.members.filter(function (m) { return m.followed; }).length;
            var allOn = followedN === total;
            return '<div style="margin-bottom:22px;">' +
                '<div style="display:flex;align-items:center;justify-content:space-between;gap:10px;margin-bottom:4px;">' +
                    '<h3 style="margin:0;font-size:17px;color:#fff;">' + esc(fam.name) +
                        '<span style="font-size:12px;font-weight:600;color:rgba(255,255,255,.4);margin-left:8px;">' +
                        followedN + '/' + total + ' followed</span></h3>' +
                    '<button type="button" data-fam-all="' + esc(fam.id) + '" ' +
                        'style="padding:7px 14px;border-radius:9px;border:none;cursor:pointer;font-weight:800;font-size:12.5px;' +
                        'background:' + (allOn ? 'rgba(255,255,255,.08)' : 'rgb(var(--accent-rgb))') + ';color:#fff;">' +
                        (allOn ? 'Following all' : 'Follow all') + '</button>' +
                '</div>' +
                '<p style="margin:0 0 10px;font-size:12px;color:rgba(255,255,255,.5);">' + esc(fam.blurb || '') + '</p>' +
                '<div style="display:grid;grid-template-columns:repeat(auto-fill,minmax(120px,1fr));gap:8px;">' +
                    fam.members.map(function (m) { return memberHTML(fam, m); }).join('') +
                '</div></div>';
        }
        var ov = document.createElement('div');
        ov.style.cssText = 'position:fixed;inset:0;z-index:9999;background:rgba(0,0,0,.62);display:flex;' +
            'align-items:center;justify-content:center;padding:20px;';
        function body() {
            return '<div style="width:min(640px,100%);max-height:86vh;overflow:auto;background:#15161c;' +
                'border:1px solid rgba(255,255,255,.1);border-radius:16px;padding:24px;box-shadow:0 24px 60px rgba(0,0,0,.5);">' +
                '<div style="font-size:11px;font-weight:800;letter-spacing:.05em;text-transform:uppercase;color:rgba(255,255,255,.45);">Studio families</div>' +
                '<h2 style="margin:4px 0 2px;font-size:21px;color:#fff;">Follow a family — or just the parts you want</h2>' +
                '<p style="margin:0 0 20px;font-size:12.5px;color:rgba(255,255,255,.55);">Each studio is followed on its own. Follow the whole family, or tick only the ones you care about (just Pixar, skip the rest).</p>' +
                '<div data-fam-list>' + presets.map(familyHTML).join('') + '</div>' +
                '<div style="display:flex;justify-content:flex-end;margin-top:6px;">' +
                    '<button type="button" data-fam-close style="padding:9px 18px;border-radius:10px;border:1px solid rgba(255,255,255,.14);background:transparent;color:rgba(255,255,255,.85);font-weight:700;cursor:pointer;">Done</button>' +
                '</div></div>';
        }
        ov.innerHTML = body();
        document.body.appendChild(ov);
        function close() { if (ov.parentNode) ov.parentNode.removeChild(ov); document.removeEventListener('keydown', onKey); }
        function onKey(e) { if (e.key === 'Escape') close(); }
        document.addEventListener('keydown', onKey);
        function rerender() { ov.innerHTML = body(); }
        function findMember(key) {
            var parts = String(key).split(':'), famId = parts[0], mid = parseInt(parts[1], 10);
            var fam = presets.filter(function (f) { return f.id === famId; })[0];
            if (!fam) return null;
            var m = fam.members.filter(function (x) { return x.tmdb_id === mid; })[0];
            return m ? { fam: fam, m: m } : null;
        }
        function follow(m, on) {
            var url = on ? '/api/video/watchlist/add' : '/api/video/watchlist/remove';
            var b = on ? { kind: 'studio', tmdb_id: m.tmdb_id, title: m.name, poster_url: m.logo || null }
                : { kind: 'studio', tmdb_id: m.tmdb_id };
            return fetch(url, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(b) })
                .then(function (r) { return r.ok ? r.json() : null; })
                .then(function (res) {
                    if (!res || res.success === false) throw new Error('failed');
                    m.followed = on;
                    document.dispatchEvent(new CustomEvent('soulsync:video-watchlist-changed',
                        { detail: { kind: 'studio', id: String(m.tmdb_id), watched: on, _silent: true } }));
                });
        }
        ov.addEventListener('click', function (e) {
            if (e.target === ov || e.target.closest('[data-fam-close]')) { close(); return; }
            var memBtn = e.target.closest('[data-fam-mem]');
            if (memBtn) {
                var hit = findMember(memBtn.getAttribute('data-fam-mem')); if (!hit) return;
                follow(hit.m, !hit.m.followed).then(rerender)
                    .catch(function () { if (typeof showToast === 'function') showToast('Update failed', 'error'); });
                return;
            }
            var allBtn = e.target.closest('[data-fam-all]');
            if (allBtn) {
                var fam = presets.filter(function (f) { return f.id === allBtn.getAttribute('data-fam-all'); })[0];
                if (!fam) return;
                var pending = fam.members.filter(function (m) { return !m.followed; });
                if (!pending.length) return;   // already all following
                allBtn.disabled = true;
                Promise.all(pending.map(function (m) { return follow(m, true).catch(function () {}); }))
                    .then(function () {
                        rerender();
                        if (typeof showToast === 'function') showToast('Following ' + esc(fam.name), 'success');
                    });
                return;
            }
        });
    }

    function wire() {
        var tabs = document.querySelectorAll('[data-vwlp-tab]');
        for (var i = 0; i < tabs.length; i++) (function (b) {
            b.addEventListener('click', function () { setTab(b.getAttribute('data-vwlp-tab')); });
        })(tabs[i]);

        var grid = $('[data-vwlp-grid]');
        if (grid) grid.addEventListener('click', onGridClick);

        var search = $('[data-vwlp-search]');
        if (search) search.addEventListener('input', function () {
            if (searchTimer) clearTimeout(searchTimer);
            searchTimer = setTimeout(function () {
                state.search = search.value.trim(); state.page = 1; load();
            }, 250);
        });

        var sortSel = $('[data-vwlp-sort]');
        if (sortSel) sortSel.addEventListener('change', function () {
            state.sort = sortSel.value; state.page = 1; load();
        });

        var prev = $('[data-vwlp-prev]');
        if (prev) prev.addEventListener('click', function () { if (state.page > 1) { state.page--; load(); } });
        var next = $('[data-vwlp-next]');
        if (next) next.addEventListener('click', function () { state.page++; load(); });

        var famBtn = document.querySelector('[data-vwlp-families]');
        if (famBtn) famBtn.addEventListener('click', function () { openStudioFamilies(); });

        document.addEventListener('soulsync:video-watchlist-changed', onChanged);
        // Following a channel fires the wishlist-changed event — keep the
        // Channels tab + badge current too.
        document.addEventListener('soulsync:video-wishlist-changed', function () {
            if (state.tab === 'channel') { var g = $('[data-vwlp-grid]'); if (g && g.offsetParent !== null) { load(); return; } }
            refreshChannelCount();
        });
    }

    function refreshChannelCount() {
        fetch('/api/video/youtube/channels', { headers: { Accept: 'application/json' } })
            .then(function (r) { return r.ok ? r.json() : null; })
            .then(function (d) {
                var n = (d && d.channels) ? d.channels.length : 0;
                state.channelCount = n;
                var cc = $('[data-vwlp-count-channel]'); if (cc) cc.textContent = n;
            })
            .catch(function () { /* ignore */ });
    }

    // Intercept card clicks → in-app SPA navigation (a bare <a href> would do a
    // FULL page reload). The eye button's capture-phase handler already stops its
    // own clicks from reaching here. Mirrors video-library.js.
    function onGridClick(e) {
        var pcog = e.target.closest('[data-vwlp-psettings]');
        if (pcog) {
            e.preventDefault(); e.stopPropagation();
            openLookbackSettings('person', pcog.getAttribute('data-vwlp-psettings'), pcog.getAttribute('data-title'));
            return;
        }
        var scog = e.target.closest('[data-vwlp-ssettings]');
        if (scog) {
            e.preventDefault(); e.stopPropagation();
            openLookbackSettings('studio', scog.getAttribute('data-vwlp-ssettings'), scog.getAttribute('data-title'));
            return;
        }
        var cog = e.target.closest('[data-vyt-wsettings]');
        if (cog && window.VideoYoutube && VideoYoutube.openChannelSettings) {
            e.preventDefault(); e.stopPropagation();
            VideoYoutube.openChannelSettings(cog.getAttribute('data-vyt-wsettings'),
                cog.getAttribute('data-title'), cog.getAttribute('data-kind') || 'channel');
            return;
        }
        var unf = e.target.closest('[data-vyt-wunfollow]');
        if (unf && window.VideoYoutube) {
            e.preventDefault(); e.stopPropagation();
            unf.disabled = true;
            VideoYoutube.unfollow(unf.getAttribute('data-vyt-wunfollow')).then(function () {
                if (typeof showToast === 'function') showToast('Unfollowed', 'info');
                load();
            }).catch(function () { unf.disabled = false; });
            return;
        }
        var punf = e.target.closest('[data-vyt-wunfollow-playlist]');
        if (punf && window.VideoYoutube) {
            e.preventDefault(); e.stopPropagation();
            punf.disabled = true;
            VideoYoutube.unfollowPlaylist(punf.getAttribute('data-vyt-wunfollow-playlist')).then(function () {
                if (typeof showToast === 'function') showToast('Unfollowed', 'info');
                load();
            }).catch(function () { punf.disabled = false; });
            return;
        }
        if (e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;
        var ch = e.target.closest('[data-vyt-open-channel]');
        if (ch) {
            e.preventDefault();
            document.dispatchEvent(new CustomEvent('soulsync:video-open-detail', {
                detail: { kind: 'channel', source: 'youtube', id: ch.getAttribute('data-vyt-open-channel') } }));
            return;
        }
        var pl = e.target.closest('[data-vyt-open-playlist]');
        if (pl) {
            e.preventDefault();
            document.dispatchEvent(new CustomEvent('soulsync:video-open-detail', {
                detail: { kind: 'playlist', source: 'youtube', id: pl.getAttribute('data-vyt-open-playlist') } }));
            return;
        }
        var card = e.target.closest('[data-vwlp-open]');
        if (!card) return;
        e.preventDefault();
        document.dispatchEvent(new CustomEvent('soulsync:video-open-detail', {
            detail: {
                kind: card.getAttribute('data-vwlp-open'),
                id: parseInt(card.getAttribute('data-vwlp-openid'), 10),
                source: card.getAttribute('data-vwlp-source') || 'library',
            },
        }));
    }

    function onShown(e) { if (e && e.detail === PAGE_ID) { state.page = 1; load(); refreshChannelCount(); } }

    function init() {
        wire();
        document.addEventListener('soulsync:video-page-shown', onShown);
        refreshBadge();   // seed the nav badge on boot
        refreshChannelCount();
    }

    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init);
    else init();
})();
