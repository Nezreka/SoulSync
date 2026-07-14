/*
 * SoulSync — Video Downloads page.
 *
 * Reuses the music downloads page's .adl-* layout + look (full-width, segmented
 * filter pills, compact rows, status dots) for visual parity, driven by video data
 * via data-vdpg-* hooks. Filter tabs, per-row cancel + retry, cancel-all, clear.
 * Rows are created ONCE and patched in place so progress glides and nothing blinks.
 */
(function () {
    'use strict';

    var URL_ACTIVE = '/api/video/downloads/active';
    var URL_CLEAR = '/api/video/downloads/clear';
    var URL_CANCEL = '/api/video/downloads/cancel';
    var URL_RETRY = '/api/video/downloads/retry';
    var _timer = null, _wired = false, _filter = 'all', _loaded = false;
    var _cards = {};
    var _expanded = {};   // id -> true while a card's detail drawer is open (survives re-patches)
    var _groups = {};     // group key -> parent element (same-show+season episode batches)
    var _gcollapse = {};  // group key -> true while collapsed
    var _meta = {};       // id -> TMDB detail (overview/cast) once lazily fetched (null = in flight)

    function esc(s) {
        return String(s == null ? '' : s)
            .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
    }
    function toast(m, t) { if (typeof showToast === 'function') showToast(m, t); }
    function setDownloadsBadge(n) {
        var b = document.querySelector('[data-video-downloads-badge]');
        if (!b) return;
        if (n > 0) { b.textContent = n > 99 ? '99+' : n; b.classList.remove('hidden'); }
        else { b.classList.add('hidden'); }
    }
    function getJSON(u) {
        return fetch(u, { headers: { Accept: 'application/json' } })
            .then(function (r) { return r.ok ? r.json() : null; }).catch(function () { return null; });
    }
    function postJSON(u, b) {
        return fetch(u, { method: 'POST', headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
            body: JSON.stringify(b || {}) }).then(function (r) { return r.ok ? r.json() : null; }).catch(function () { return null; });
    }

    var KIND_ICON = { movie: '🎬', show: '📺', episode: '📺', season: '📺', series: '📺', youtube: '▶️' };
    // collapse the kinds into the three colour groups (Cinema palette in CSS): movie / tv / youtube
    function dlType(kind) {
        var k = (kind || '').toLowerCase();
        return k === 'youtube' ? 'youtube' : (k === 'movie' ? 'movie' : 'tv');
    }
    // status -> { label, cls } where cls is the music .adl-row-/.adl-status-dot class
    var STATUS = {
        downloading: { label: 'Downloading', cls: 'active' },
        queued: { label: 'Queued', cls: 'queued' },
        searching: { label: 'Searching', cls: 'active' },     // retrying — finding another release
        importing: { label: 'Importing', cls: 'active' },     // post-processing → moving into library
        completed: { label: 'Completed', cls: 'completed' },
        failed: { label: 'Failed', cls: 'failed' },
        import_failed: { label: 'Import failed', cls: 'failed' },
        cancelled: { label: 'Cancelled', cls: 'cancelled' }
    };
    function isActive(s) { return s === 'downloading' || s === 'queued' || s === 'searching' || s === 'importing'; }
    function isFail(s) { return s === 'failed' || s === 'cancelled' || s === 'import_failed'; }
    function matches(s) {
        return _filter === 'all' || (_filter === 'active' && isActive(s)) ||
            (_filter === 'completed' && s === 'completed') || (_filter === 'failed' && isFail(s));
    }
    function fmtSize(bytes) {
        var gb = (bytes || 0) / (1024 * 1024 * 1024);
        return gb >= 0.1 ? (Math.round(gb * 10) / 10) + ' GB' : Math.round((bytes || 0) / (1024 * 1024)) + ' MB';
    }
    function fmtElapsed(ms) {
        var s = Math.floor((ms || 0) / 1000);
        var m = Math.floor(s / 60); s = s % 60;
        return m ? (m + 'm' + (s < 10 ? '0' : '') + s + 's') : (s + 's');
    }

    var X_SVG = '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>';
    var IMP_SVG = '<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.1" stroke-linecap="round" stroke-linejoin="round"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/></svg>';
    var BAN_SVG = '<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.1" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="9"/><line x1="5.6" y1="5.6" x2="18.4" y2="18.4"/></svg>';
    var URL_BLOCKLIST = '/api/video/downloads/blocklist';
    // the Import page is an admin tool (videoPageAllowed in video-side.js) — same gate here
    // so non-admins never see a button that would just bounce them to the dashboard.
    function canImport() {
        var cp = (typeof currentProfile !== 'undefined') ? currentProfile : null;
        return !cp || !!cp.is_admin || cp.id === 1;
    }
    var R_SVG = '<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><polyline points="23 4 23 10 17 10"/><path d="M20.49 15a9 9 0 1 1-2.12-9.36L23 10"/></svg>';
    var OPEN_SVG = '<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.1" stroke-linecap="round" stroke-linejoin="round"><path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6"/><polyline points="15 3 21 3 21 9"/><line x1="10" y1="14" x2="21" y2="3"/></svg>';

    // Downloads of the same show + season collapse into one parent card (≥2 members).
    function groupKey(d) {
        if ((d.kind || '').toLowerCase() !== 'show') return null;
        var c = parseCtx(d);
        if (c.season == null || c.episode == null) return null;
        return 'g:' + (d.media_id || d.title || '?') + ':s' + c.season;
    }

    function makeGroup(key) {
        var el = document.createElement('div');
        el.className = 'vdpg-group';
        el.setAttribute('data-vdpg-group', key);
        el.innerHTML =
            '<div class="vdpg-group-head" data-vdpg-group-toggle="' + esc(key) + '">' +
                '<span class="vdpg-group-caret" data-f="caret">▾</span>' +
                '<div class="vdpg-group-art" data-f="art">📺</div>' +
                '<div class="vdpg-group-info">' +
                    '<div class="vdpg-group-title" data-f="title"></div>' +
                    '<div class="vdpg-group-sub" data-f="sub"></div>' +
                    '<div class="vdpg-prog vdpg-group-prog" data-f="bar" style="display:none"><div class="vdpg-prog-fill" data-f="fill"></div></div>' +
                '</div>' +
                '<div class="vdpg-group-status" data-f="status"></div>' +
                '<div class="vdpg-group-act" data-f="act"></div>' +
            '</div>' +
            '<div class="vdpg-group-body" data-vdpg-group-body></div>';
        return el;
    }

    function patchGroup(el, members, key) {
        var first = members[0], c0 = parseCtx(first);
        var total = members.length, done = 0, act = 0, fail = 0, pctSum = 0;
        members.forEach(function (d) {
            if (d.status === 'completed') { done++; pctSum += 100; }
            else if (isActive(d.status)) { act++; pctSum += Math.max(0, Math.min(100, d.progress || 0)); }
            else { fail++; }
        });
        var t = el.querySelector('[data-f="title"]'); if (t) t.textContent = first.title || 'Show';
        var seasonTxt = (c0.season != null) ? ('Season ' + c0.season) : 'Episodes';
        var sub = el.querySelector('[data-f="sub"]');
        if (sub) sub.textContent = seasonTxt + '  ·  ' + done + '/' + total + ' done' + (fail ? '  ·  ' + fail + ' failed' : '');
        var bar = el.querySelector('[data-f="bar"]'), fill = el.querySelector('[data-f="fill"]');
        if (bar) { if (act) { bar.style.display = ''; if (fill) fill.style.width = Math.round(pctSum / total) + '%'; } else { bar.style.display = 'none'; } }
        var status = el.querySelector('[data-f="status"]');
        if (status) status.textContent = act ? (act + ' active') : (done === total ? '✓ done' : (fail + ' failed'));
        var art = el.querySelector('[data-f="art"]');
        if (art && first.poster_url && art._p !== first.poster_url) {
            art._p = first.poster_url; art.style.backgroundImage = "url('" + first.poster_url + "')";
            art.classList.add('vdpg-has-poster'); art.textContent = '';
        }
        var act2 = el.querySelector('[data-f="act"]');
        if (act2) {
            var wantAct = act ? '<button class="adl-row-cancel" type="button" data-vdpg-group-cancel="' + esc(key) + '" title="Cancel all in this batch">' + X_SVG + '</button>' : '';
            if (act2.innerHTML !== wantAct) act2.innerHTML = wantAct;
        }
        var collapsed = !!_gcollapse[key];
        el.classList.toggle('vdpg-group--collapsed', collapsed);
        var caret = el.querySelector('[data-f="caret"]'); if (caret) caret.textContent = collapsed ? '▸' : '▾';
    }

    function makeCard(d) {
        var el = document.createElement('div');
        el.className = 'vdpg-card adl-row';
        el.setAttribute('data-dl-id', d.id);
        el.innerHTML =
            '<div class="vdpg-artwrap"><div class="adl-row-art adl-row-art-empty vdpg-art" data-f="ic"></div>' +
                '<span class="vdpg-tbadge" data-f="tbadge"></span></div>' +
            '<div class="adl-row-info">' +
                '<div class="adl-row-title" data-f="name"></div>' +
                '<div class="adl-row-meta" data-f="meta"></div>' +
                '<div class="adl-row-error" data-f="error" style="display:none"></div>' +
                '<div class="vdpg-prog" data-f="bar" style="display:none"><div class="vdpg-prog-fill" data-f="fill"></div></div>' +
            '</div>' +
            '<div class="adl-row-status" data-f="status"><span class="adl-status-dot" data-f="dot"></span><span data-f="label"></span></div>' +
            '<div class="vdpg-rowact" data-f="actions"></div>' +
            '<div class="vdpg-drawer" data-f="drawer" hidden></div>';
        return el;
    }

    function patchCard(el, d) {
        el._d = d;   // remember the row data so the expand toggle can re-render its drawer
        var info = STATUS[d.status] || STATUS.downloading;
        var cls = info.cls, active = isActive(d.status);
        var showBar = active;   // downloading/queued/searching/importing all get a bar
        // Import sub-phases: 'merging' (ffmpeg, no %) vs 'moving' (cross-drive copy, real %).
        var importing = d.status === 'importing';
        var moving = importing && d.import_phase === 'moving';
        var iprog = Math.max(0, Math.min(100, d.import_progress || 0));
        // queued/searching/merging (and moving until the first % lands) → indeterminate shimmer.
        var indet = d.status === 'queued' || d.status === 'searching' || (importing && !(moving && iprog > 0));
        var pct = Math.max(0, Math.min(100, d.progress || 0));
        // Track when this card entered the import phase so we can show an elapsed timer.
        if (importing) { if (!el._importStart) el._importStart = Date.now(); }
        else el._importStart = 0;
        var q = function (f) { return el.querySelector('[data-f="' + f + '"]'); };

        var vt = dlType(d.kind);
        if (el.getAttribute('data-vtype') !== vt) el.setAttribute('data-vtype', vt);
        var want = 'vdpg-card adl-row adl-row-' + cls;
        if (el.className !== want) el.className = want;

        // type badge on the art corner (so you can tell movie / TV / youtube even with a poster)
        var tb = q('tbadge');
        if (tb) { var tbi = KIND_ICON[(d.kind || '').toLowerCase()] || '🎬'; if (tb.textContent !== tbi) tb.textContent = tbi; }

        // poster art tile (falls back to the kind emoji)
        var ic = q('ic');
        if (d.poster_url) {
            if (ic._p !== d.poster_url) { ic._p = d.poster_url; ic.style.backgroundImage = "url('" + d.poster_url + "')"; }
            ic.classList.add('vdpg-has-poster'); ic.textContent = '';
        } else {
            ic.classList.remove('vdpg-has-poster'); if (ic._p) { ic.style.backgroundImage = ''; ic._p = null; }
            var icon = KIND_ICON[(d.kind || '').toLowerCase()] || '🎬';
            if (ic.textContent !== icon) ic.textContent = icon;
        }

        var name = (d.title || d.release_title || 'Download') + (d.year ? '  (' + d.year + ')' : '');
        // Episode downloads all share the show title — disambiguate with SxxExx so a
        // whole season's grabs aren't 14 identical rows (the season-pack case).
        var _nctx = parseCtx(d);
        if (_nctx && _nctx.season != null && _nctx.episode != null) {
            name = (d.title || d.release_title || 'Episode') + ' · S' + pad2(_nctx.season) + 'E' + pad2(_nctx.episode);
        }
        var nm = q('name'); if (nm.textContent !== name) nm.textContent = name;

        // meta: quality chip + a context line (release / size·user·pct / dest)
        var ctx;
        if (d.status === 'completed' && d.dest_path) ctx = '→ ' + d.dest_path;
        else if (d.status === 'searching') ctx = 'Trying another release…';
        else if (importing) {
            if (d.import_phase === 'merging') ctx = 'Merging video + audio…';
            else if (moving && iprog > 0) ctx = 'Moving into your library…  ' + Math.round(iprog) + '%';
            else ctx = 'Moving into your library…';
            var el_ms = el._importStart ? (Date.now() - el._importStart) : 0;
            if (el_ms > 2000) ctx += '  ·  ' + fmtElapsed(el_ms);
        }
        else if (d.status === 'queued') ctx = 'Waiting for a free slot…';
        else if (showBar) ctx = [fmtSize(d.size_bytes), d.username ? ('👤 ' + d.username) : '', Math.round(pct) + '%'].filter(Boolean).join('  ·  ');
        else ctx = (d.release_title && d.release_title !== (d.title || '')) ? d.release_title : fmtSize(d.size_bytes);
        // YouTube rows all read as bare video titles — say whose channel it is right
        // on the collapsed row (the drawer already knows, but you shouldn't have to open it).
        var ytChName = dlType(d.kind) === 'youtube' ? (_nctx.channel || _nctx.channel_title) : null;
        var chip = d.quality_label ? '<span class="vdpg-qchip">' + esc(d.quality_label) + '</span>' : '';
        // completed-but-below-cutoff grabs keep their wishlist row (upgrade-until-cutoff)
        // — surface that state so "done" and "done, still hunting a better copy" differ.
        var upchip = (d.status === 'completed' && d.upgrade_watch)
            ? '<span class="vdpg-upchip" title="Below your quality cutoff — still watching for a better copy">⇪ upgrade watch</span>' : '';
        var metaHTML = chip + upchip +
            (ytChName ? '<span class="vdpg-mchan">' + esc(ytChName) + '</span>' : '') +
            '<span class="vdpg-mctx' + (d.status === 'completed' && d.dest_path ? ' vdpg-dest' : '') + '">' + esc(ctx) + '</span>';
        var mt = q('meta'); if (mt.innerHTML !== metaHTML) mt.innerHTML = metaHTML;

        var err = q('error');
        var errTxt = isFail(d.status) && d.error ? d.error : '';
        if (err.textContent !== errTxt) err.textContent = errTxt;
        err.style.display = errTxt ? '' : 'none';

        var bar = q('bar');
        bar.style.display = showBar ? '' : 'none';
        if (showBar) {
            bar.classList.toggle('vdpg-prog-indet', indet);
            q('fill').style.width = indet ? '100%' : (moving ? iprog : pct) + '%';
        }

        var st = q('status'); var stWant = 'adl-row-status ' + cls;
        if (st.className !== stWant) st.className = stWant;
        var dot = q('dot'); var dotWant = 'adl-status-dot ' + cls;
        if (dot.className !== dotWant) dot.className = dotWant;
        var labelTxt = info.label + (d.attempts > 1 ? ' · ' + d.attempts + 'x' : '');
        var lab = q('label'); if (lab.textContent !== labelTxt) lab.textContent = labelTxt;

        var act = q('actions');
        // youtube videos aren't TMDB titles. Open the in-app CHANNEL page (channels-as-shows)
        // when we know the channel id; fall back to the YouTube video link otherwise. (The old
        // open-show button ran parseInt on the video id → opened a random library show.)
        var ytCh = dlType(d.kind) === 'youtube' ? parseCtx(d).channel_id : null;
        var openBtn = !d.media_id ? ''
            : (dlType(d.kind) === 'youtube'
                ? (ytCh
                    ? '<button class="vdpg-open" type="button" data-vdpg-open-channel="' + esc(ytCh) + '" title="Open channel">' + OPEN_SVG + '</button>'
                    : '<a class="vdpg-open" href="https://www.youtube.com/watch?v=' + encodeURIComponent(d.media_id) +
                      '" target="_blank" rel="noopener" title="Open on YouTube">' + OPEN_SVG + '</a>')
                : '<button class="vdpg-open" type="button" data-vdpg-open="' + esc(d.media_id) +
                  '" data-kind="' + esc(d.kind || 'movie') + '" data-source="' + esc(d.media_source || 'library') +
                  '" title="Open ' + (d.kind === 'movie' ? 'movie' : 'show') + ' page">' + OPEN_SVG + '</button>');
        var stateBtn = active
            ? '<button class="adl-row-cancel" type="button" data-vdpg-cancel="' + d.id + '" title="Cancel">' + X_SVG + '</button>'
            : isFail(d.status)
                ? '<button class="vdpg-row-retry" type="button" data-vdpg-retry="' + d.id + '" title="Retry">' + R_SVG + '</button>'
                : '';
        // the file downloaded fine but couldn't be placed — retrying the grab won't
        // help; hand it to the Import page where it can be filed by hand (admin tool).
        var importBtn = (d.status === 'import_failed' && canImport())
            ? '<button class="vdpg-row-retry vdpg-row-import" type="button" data-vdpg-import title="Manual Import — file it by hand on the Import page">' + IMP_SVG + '</button>'
            : '';
        // Block this exact release (slskd rows only — YT has no release identity).
        // failed → block + auto-retry with another candidate; import_failed → block
        // (the file itself may still be manually importable, so no auto-retry).
        var blockBtn = ((d.status === 'failed' || d.status === 'import_failed') &&
                        dlType(d.kind) !== 'youtube' && d.username && d.filename)
            ? '<button class="vdpg-row-retry vdpg-row-block" type="button" data-vdpg-block="' + d.id +
              '" data-was="' + esc(d.status) + '" title="' +
              (d.status === 'failed' ? 'Block this release and retry with another' : 'Block this release') +
              '">' + BAN_SVG + '</button>'
            : '';
        var actHTML = openBtn + importBtn + blockBtn + stateBtn;
        if (act.innerHTML !== actHTML) act.innerHTML = actHTML;

        renderDrawer(el, d);   // keep the expand drawer in sync (and open across re-patches)
    }

    // ── expand drawer ─────────────────────────────────────────────────────────────
    function parseCtx(d) {   // the download's search_ctx (peer/season/episode/channel/…)
        try { return d.search_ctx ? (typeof d.search_ctx === 'string' ? JSON.parse(d.search_ctx) : d.search_ctx) : {}; }
        catch (e) { return {}; }
    }
    function fact(k, v) {
        return v ? '<div class="vdpg-f"><span class="vdpg-fk">' + esc(k) + '</span><span class="vdpg-fv">' + esc(v) + '</span></div>' : '';
    }
    function fmtRuntime(m) {
        m = parseInt(m, 10); if (!m) return '';
        var h = Math.floor(m / 60), mm = m % 60;
        return h ? (h + 'h' + (mm ? ' ' + mm + 'm' : '')) : (mm + 'm');
    }
    function fmtViews(n) {
        n = +n || 0;
        return n >= 1e6 ? (Math.round(n / 1e5) / 10 + 'M') : n >= 1e3 ? (Math.round(n / 100) / 10 + 'K') : String(n);
    }
    function fmtSpeed(bps) {
        bps = +bps || 0; if (!bps) return '';
        return bps >= 1e6 ? (Math.round(bps / 1e5) / 10 + ' MB/s') : Math.max(1, Math.round(bps / 1e3)) + ' KB/s';
    }
    function pad2(n) { n = parseInt(n, 10) || 0; return (n < 10 ? '0' : '') + n; }
    function castHTMLOf(meta) {
        var cast = (meta.cast || []).slice(0, 8);
        return cast.length ? '<div class="vdpg-dr-st">Cast</div><div class="vdpg-dr-cast">' + cast.map(function (c) {
            var pic = c.photo
                ? '<span class="vdpg-cast-pic" style="background-image:url(\'' + esc(c.photo) + '\')"></span>'
                : '<span class="vdpg-cast-pic vdpg-cast-none">' + esc((c.name || '?').charAt(0)) + '</span>';
            return '<div class="vdpg-cast">' + pic + '<span class="vdpg-cast-nm">' + esc(c.name) +
                '</span>' + (c.character ? '<span class="vdpg-cast-ch">' + esc(c.character) + '</span>' : '') + '</div>';
        }).join('') + '</div>' : '';
    }

    function drawerHTML(d, meta) {
        var isYt = dlType(d.kind) === 'youtube', ctx = parseCtx(d);
        var loading = meta === null;
        meta = meta || {};
        var back = '', head = '', lead = '', extra = '';

        if (isYt) {
            // big thumbnail + channel · duration · views · upload date, then the description
            var thumb = meta.thumbnail_url || d.poster_url;
            var yb = [];
            if (ctx.channel || ctx.channel_title) yb.push(esc(ctx.channel || ctx.channel_title));
            if (meta.duration) yb.push(esc(meta.duration));
            if (meta.view_count) yb.push(fmtViews(meta.view_count) + ' views');
            if (ctx.published_at) yb.push(esc(String(ctx.published_at).slice(0, 10)));
            head = '<div class="vdpg-dr-head">' +
                (thumb ? '<div class="vdpg-dr-ytthumb" style="background-image:url(\'' + esc(thumb) + '\')"></div>' : '') +
                '<div class="vdpg-dr-title">' + esc(d.title || meta.title || 'Video') + '</div>' +
                (yb.length ? '<div class="vdpg-dr-metaline">' + yb.join('  ·  ') + '</div>' : '') + '</div>';
            lead = ctx.description ? '<p class="vdpg-dr-syn">' + esc(ctx.description) + '</p>' : '';
        } else {
            back = meta.backdrop_url
                ? '<div class="vdpg-dr-back" style="background-image:url(\'' + esc(meta.backdrop_url) + '\')"></div>' : '';
            var titleHTML = meta.logo
                ? '<img class="vdpg-dr-logo" src="' + esc(meta.logo) + '" alt="' + esc(meta.title || d.title || '') + '">'
                : '<div class="vdpg-dr-title">' + esc(meta.title || d.title || 'Download') + '</div>';
            var bits = [];
            if (meta.year || d.year) bits.push(esc(meta.year || d.year));
            if (meta.rating) bits.push('⭐ ' + (Math.round(meta.rating * 10) / 10));
            var rt = fmtRuntime(meta.runtime_minutes); if (rt) bits.push(rt);
            if (meta.network || meta.studio) bits.push(esc(meta.network || meta.studio));
            if (meta.status) bits.push(esc(meta.status));
            var tagline = meta.tagline ? '<div class="vdpg-dr-tagline">' + esc(meta.tagline) + '</div>' : '';
            head = '<div class="vdpg-dr-head">' + titleHTML +
                (bits.length ? '<div class="vdpg-dr-metaline">' + bits.join('  ·  ') + '</div>' : '') + tagline + '</div>';

            var ep = meta.episode;
            if (ep) {   // the SPECIFIC episode: still + SxE · air date + episode title + its own synopsis
                lead = '<div class="vdpg-dr-ep">' +
                    (ep.still_url ? '<div class="vdpg-dr-epstill" style="background-image:url(\'' + esc(ep.still_url) + '\')"></div>' : '') +
                    '<div class="vdpg-dr-epbody"><div class="vdpg-dr-epnum">S' + pad2(ep.season) + 'E' + pad2(ep.episode) +
                    (ep.air_date ? '   ·   ' + esc(ep.air_date) : '') + '</div>' +
                    '<div class="vdpg-dr-eptitle">' + esc(ep.title || '') + '</div>' +
                    (ep.overview ? '<p class="vdpg-dr-epov">' + esc(ep.overview) + '</p>' : '') + '</div></div>';
            } else {
                lead = loading ? '<p class="vdpg-dr-syn vdpg-dr-muted">Loading…</p>'
                    : (meta.overview ? '<p class="vdpg-dr-syn">' + esc(meta.overview) + '</p>'
                        : '<p class="vdpg-dr-syn vdpg-dr-muted">No synopsis available.</p>');
            }

            var genres = (meta.genres || []).slice(0, 4).join('  ·  ');
            var watch = '';
            if (meta.trailer_url) watch += '<a class="vdpg-dr-btn vdpg-dr-trailer" href="' + esc(meta.trailer_url) + '" target="_blank" rel="noopener">▶ Trailer</a>';
            var provs = meta.providers || [];
            if (provs.length) watch += '<span class="vdpg-dr-provs"><span class="vdpg-dr-provs-t">Watch on</span>' + provs.map(function (p) {
                return p.logo ? '<img class="vdpg-prov" src="' + esc(p.logo) + '" alt="' + esc(p.name || '') + '" title="' + esc(p.name || '') + '">'
                    : '<span class="vdpg-prov vdpg-prov-txt">' + esc(p.name || '') + '</span>';
            }).join('') + '</span>';
            extra = (genres ? '<div class="vdpg-dr-genres">' + esc(genres) + '</div>' : '') +
                (watch ? '<div class="vdpg-dr-watch">' + watch + '</div>' : '') + castHTMLOf(meta);
        }

        // download facts (only the fields that exist render)
        var facts = '';
        facts += fact('Status', (STATUS[d.status] || {}).label);
        if (isYt) { facts += fact('Channel', ctx.channel || ctx.channel_title); facts += fact('Quality', d.quality_label); }
        else {
            facts += fact(dlType(d.kind) === 'movie' ? 'Director' : 'Creator', meta.director);
            facts += fact('Quality target', d.quality_label);
            facts += fact('Release', d.release_title);
            facts += fact('Format', [d.resolution, d.source, d.codec].filter(Boolean).join(' · '));
            facts += fact('Source', d.username ? ('👤 ' + d.username) : '');
            if (ctx.peer) {   // the chosen source's availability snapshot at grab time
                var p = ctx.peer, av = [];
                if (p.slots != null) av.push(p.slots > 0 ? '✓ free slot' : 'no free slot');
                if (p.queue != null) av.push('queue ' + p.queue);
                var sp = fmtSpeed(p.speed); if (sp) av.push(sp);
                facts += fact('Availability', av.join('   ·   '));
            }
        }
        facts += fact('Size', d.size_bytes ? fmtSize(d.size_bytes) : '');
        facts += fact('Attempts', d.attempts > 1 ? (d.attempts + 'x') : '');
        if (d.status === 'completed' && d.upgrade_watch) facts += fact('Upgrade', 'Below cutoff — still on the wishlist, watching for a better copy');
        if (d.dest_path) facts += '<div class="vdpg-f vdpg-f-wide"><span class="vdpg-fk">Path</span>' +
            '<span class="vdpg-fv vdpg-mono">' + esc(d.dest_path) + '</span>' +
            '<button class="vdpg-copy" type="button" data-vdpg-copy="' + esc(d.dest_path) + '" title="Copy path">⧉</button></div>';
        if (isFail(d.status) && d.error) facts += '<div class="vdpg-f vdpg-f-wide vdpg-f-err"><span class="vdpg-fk">Error</span><span class="vdpg-fv">' + esc(d.error) + '</span></div>';

        var btns = [];
        if (d.media_id && !isYt) btns.push('<button class="vdpg-dr-btn" type="button" data-vdpg-open="' + esc(d.media_id) +
            '" data-kind="' + esc(d.kind || 'movie') + '" data-source="' + esc(d.media_source || 'library') + '">Open in library</button>');
        var ytCh = meta.channel_id || ctx.channel_id;
        if (isYt && ytCh) btns.push('<button class="vdpg-dr-btn" type="button" data-vdpg-open-channel="' + esc(ytCh) + '">Open channel</button>');
        if (isYt && d.media_id) btns.push('<a class="vdpg-dr-btn" href="https://www.youtube.com/watch?v=' + encodeURIComponent(d.media_id) + '" target="_blank" rel="noopener">Open on YouTube</a>');
        if (isActive(d.status)) btns.push('<button class="vdpg-dr-btn vdpg-dr-danger" type="button" data-vdpg-cancel="' + d.id + '">Cancel</button>');
        else if (isFail(d.status)) {
            if (d.status === 'import_failed' && canImport()) btns.push('<button class="vdpg-dr-btn vdpg-dr-accent" type="button" data-vdpg-import>Manual Import</button>');
            btns.push('<button class="vdpg-dr-btn vdpg-dr-accent" type="button" data-vdpg-retry="' + d.id + '">Retry</button>');
        }
        var actions = btns.length ? '<div class="vdpg-dr-actions">' + btns.join('') + '</div>' : '';

        return back + '<div class="vdpg-dr-body">' + head + lead + extra +
            '<div class="vdpg-dr-st">Download</div><div class="vdpg-dr-facts">' + facts + '</div>' +
            actions + '</div>';
    }

    function metaURL(d) {
        var t = dlType(d.kind);
        if (t === 'youtube') return '/api/video/downloads/yt-meta/' + encodeURIComponent(d.media_id);
        if (d.media_source === 'library') return null;   // owned re-grab: media_id isn't a tmdb id
        var url = '/api/video/downloads/meta/' + (t === 'movie' ? 'movie' : 'show') + '/' + encodeURIComponent(d.media_id);
        if (d.kind === 'episode') {
            var c = parseCtx(d);
            if (c.season != null && c.episode != null) url += '?season=' + encodeURIComponent(c.season) + '&episode=' + encodeURIComponent(c.episode);
        }
        return url;
    }
    function renderDrawer(el, d) {
        var dr = el.querySelector('[data-f="drawer"]'); if (!dr) return;
        var open = !!_expanded[d.id];
        el.classList.toggle('vdpg-card-open', open);
        dr.hidden = !open;
        if (!open) { dr.innerHTML = ''; return; }
        // kick off the lazy detail fetch (TMDB for movie/TV, cached metadata for youtube) so the
        // first paint already shows 'Loading…' rather than 'no synopsis' flashing before content.
        if (_meta[d.id] === undefined && d.media_id) {
            var url = metaURL(d);
            if (!url) { _meta[d.id] = {}; }
            else {
                _meta[d.id] = null;
                getJSON(url).then(function (m) {
                    _meta[d.id] = m || {};
                    if (_expanded[d.id]) { var dr2 = el.querySelector('[data-f="drawer"]'); if (dr2) dr2.innerHTML = drawerHTML(d, _meta[d.id]); }
                });
            }
        }
        dr.innerHTML = drawerHTML(d, _meta[d.id]);
    }

    function render(list) {
        var host = document.querySelector('[data-vdpg-list]'); if (!host) return;
        list = list || [];
        _loaded = true;
        var skel = host.querySelector('[data-vdpg-skel]'); if (skel) skel.remove();
        var empty = host.querySelector('[data-vdpg-empty]');

        var counts = { all: list.length, active: 0, completed: 0, failed: 0, retryable: 0 };
        list.forEach(function (d) {
            if (isActive(d.status)) counts.active++;
            else if (d.status === 'completed') counts.completed++;
            else {
                counts.failed++;
                // only real grab failures with a known release can be re-grabbed
                // (cancelled = deliberate; import_failed = Manual Import; YT rows have no slskd source)
                if (d.status === 'failed' && d.username && d.filename) counts.retryable++;
            }
        });
        setDownloadsBadge(counts.active);   // sidebar live count (this page's poll keeps it fresh)
        var cancelAll = document.querySelector('[data-vdpg-cancel-all]'); if (cancelAll) cancelAll.style.display = counts.active ? '' : 'none';
        var retryAll = document.querySelector('[data-vdpg-retry-all]');
        if (retryAll) retryAll.style.display = counts.retryable >= 2 ? '' : 'none';
        var clearBtn = document.querySelector('[data-vdpg-clear]'); if (clearBtn) clearBtn.style.display = (counts.completed + counts.failed) ? '' : 'none';
        var sub = document.querySelector('[data-vdpg-sub]');
        if (sub) {
            var parts = [];
            if (counts.active) parts.push(counts.active + ' active');
            if (counts.completed) parts.push(counts.completed + ' done');
            if (counts.failed) parts.push(counts.failed + ' failed');
            sub.textContent = parts.join('  ·  ');
        }

        // Group same-show+season episode batches (≥2) into one parent card; keep
        // everything else standalone. Server order preserved — a group sits where its
        // first member appears.
        var gcount = {};
        list.forEach(function (d) { var k = groupKey(d); if (k) gcount[k] = (gcount[k] || 0) + 1; });
        var order = [], gidx = {};
        list.forEach(function (d) {
            var k = groupKey(d);
            if (k && gcount[k] >= 2) {
                if (gidx[k] == null) { gidx[k] = order.length; order.push({ group: k, members: [d] }); }
                else { order[gidx[k]].members.push(d); }
            } else { order.push({ single: d }); }
        });

        var seen = {}, seenG = {}, shown = 0;
        order.forEach(function (u) {
            if (u.single) {
                var d = u.single; seen[d.id] = true;
                var el = _cards[d.id] || (_cards[d.id] = makeCard(d));
                patchCard(el, d);
                var vis = matches(d.status); el.style.display = vis ? '' : 'none'; if (vis) shown++;
                host.appendChild(el);   // server order (active first); no re-anim
                return;
            }
            seenG[u.group] = true;
            var g = _groups[u.group] || (_groups[u.group] = makeGroup(u.group));
            host.appendChild(g);
            var body = g.querySelector('[data-vdpg-group-body]');
            var visN = 0;
            u.members.forEach(function (d) {
                seen[d.id] = true;
                var el = _cards[d.id] || (_cards[d.id] = makeCard(d));
                patchCard(el, d);
                var vis = matches(d.status); el.style.display = vis ? '' : 'none'; if (vis) { visN++; shown++; }
                body.appendChild(el);
            });
            patchGroup(g, u.members, u.group);
            g.style.display = visN ? '' : 'none';
        });
        Object.keys(_cards).forEach(function (id) {
            if (!seen[id]) { var e = _cards[id]; if (e && e.parentNode) e.parentNode.removeChild(e); delete _cards[id]; }
        });
        Object.keys(_groups).forEach(function (k) {
            if (!seenG[k]) { var e = _groups[k]; if (e && e.parentNode) e.parentNode.removeChild(e); delete _groups[k]; }
        });

        if (empty) {
            host.appendChild(empty);   // keep the empty element last
            empty.style.display = shown === 0 ? '' : 'none';
            empty.innerHTML = !list.length
                ? '<div class="vdpg-empty-ic">📥</div>' +
                  '<div class="vdpg-empty-t">No downloads yet</div>' +
                  '<div class="vdpg-empty-s">Hit <strong>Grab</strong> on a search result — or add something to the wishlist and let the automations fetch it — and it\'ll show up here.</div>'
                : '<div class="vdpg-empty-ic">' + (_filter === 'failed' ? '🎉' : '📂') + '</div>' +
                  '<div class="vdpg-empty-t">Nothing ' + (_filter === 'all' ? 'here' : esc(_filter)) + ' right now</div>' +
                  '<div class="vdpg-empty-s">' + (_filter === 'failed' ? 'No failures — everything went through clean.' : 'Switch filters to see the rest of the queue.') + '</div>';
        }
    }

    function setFilter(f) {
        _filter = f;
        Array.prototype.forEach.call(document.querySelectorAll('[data-vdpg-filter]'), function (b) {
            b.classList.toggle('active', b.getAttribute('data-vdpg-filter') === f);
        });
        getJSON(URL_ACTIVE).then(function (d) { if (d) render(d.downloads || []); });
    }

    function anyActive() { return !!document.querySelector('.adl-row.adl-row-active, .adl-row.adl-row-queued'); }

    // Only poll while the Downloads page is actually on screen — not in the background
    // and not after switching to the music side (where the page-change event never fires).
    function _onPage() {
        return document.body.getAttribute('data-side') === 'video' &&
            !!document.querySelector('[data-video-subpage="video-downloads"]:not([hidden])');
    }
    function poll() {
        if (!_onPage()) { stop(); return; }
        getJSON(URL_ACTIVE).then(function (d) { if (d) render(d.downloads || []); schedule(); });
    }
    function schedule() { if (_timer) clearTimeout(_timer); _timer = setTimeout(poll, anyActive() ? 2000 : 6000); }
    // First visit paints shimmer placeholder rows until the first poll answers —
    // no flash of "No downloads yet" while the request is in flight.
    function showSkeleton() {
        var host = document.querySelector('[data-vdpg-list]');
        if (!host || _loaded || host.querySelector('[data-vdpg-skel]')) return;
        var empty = host.querySelector('[data-vdpg-empty]'); if (empty) empty.style.display = 'none';
        var sk = document.createElement('div');
        sk.setAttribute('data-vdpg-skel', '');
        sk.innerHTML = new Array(4).join(
            '<div class="vdpg-skel-row"><div class="vdpg-skel-art"></div>' +
            '<div class="vdpg-skel-lines"><div class="vdpg-skel-line"></div><div class="vdpg-skel-line vdpg-skel-line--short"></div></div></div>');
        host.appendChild(sk);
    }
    function start() { wire(); showSkeleton(); if (_timer) clearTimeout(_timer); poll(); }
    function stop() { if (_timer) { clearTimeout(_timer); _timer = null; } }

    function wire() {
        if (_wired) return; _wired = true;
        var clearBtn = document.querySelector('[data-vdpg-clear]');
        if (clearBtn) clearBtn.addEventListener('click', function () {
            postJSON(URL_CLEAR, {}).then(function () { toast('Cleared finished downloads', 'success'); poll(); });
        });
        var cancelAll = document.querySelector('[data-vdpg-cancel-all]');
        if (cancelAll) cancelAll.addEventListener('click', function () {
            getJSON(URL_ACTIVE).then(function (d) {
                var ids = ((d && d.downloads) || []).filter(function (x) { return isActive(x.status); }).map(function (x) { return x.id; });
                Promise.all(ids.map(function (id) { return postJSON(URL_CANCEL, { id: id }); }))
                    .then(function () { toast('Cancelled ' + ids.length + ' download' + (ids.length === 1 ? '' : 's'), 'info'); poll(); });
            });
        });
        var retryAll = document.querySelector('[data-vdpg-retry-all]');
        if (retryAll) retryAll.addEventListener('click', function () {
            retryAll.disabled = true;
            getJSON(URL_ACTIVE).then(function (d) {
                // same rule as the count in render(): real grab failures with a known release
                var ids = ((d && d.downloads) || []).filter(function (x) {
                    return x.status === 'failed' && x.username && x.filename;
                }).map(function (x) { return x.id; });
                Promise.all(ids.map(function (id) { return postJSON(URL_RETRY, { id: id }); }))
                    .then(function (rs) {
                        var ok = rs.filter(function (r) { return r && r.ok; }).length;
                        toast(ok ? ('Retrying ' + ok + ' download' + (ok === 1 ? '' : 's')) : 'Nothing could be retried', ok ? 'info' : 'error');
                        retryAll.disabled = false; poll();
                    });
            });
        });
        var pills = document.querySelector('[data-vdpg-pills]');
        if (pills) pills.addEventListener('click', function (e) {
            var b = e.target.closest('[data-vdpg-filter]'); if (b) setFilter(b.getAttribute('data-vdpg-filter'));
        });
        var list = document.querySelector('[data-vdpg-list]');
        if (list) list.addEventListener('click', function (e) {
            // Group header: cancel the whole batch, or collapse/expand it.
            var gcan = e.target.closest('[data-vdpg-group-cancel]');
            if (gcan) {
                var gbody = gcan.closest('.vdpg-group').querySelector('[data-vdpg-group-body]');
                var ids = [];
                Array.prototype.forEach.call(gbody.querySelectorAll('.adl-row[data-dl-id]'), function (c) {
                    if (c.classList.contains('adl-row-active') || c.classList.contains('adl-row-queued')) ids.push(+c.getAttribute('data-dl-id'));
                });
                gcan.disabled = true;
                Promise.all(ids.map(function (id) { return postJSON(URL_CANCEL, { id: id }); }))
                    .then(function () { toast('Cancelled ' + ids.length + ' download' + (ids.length === 1 ? '' : 's'), 'info'); poll(); });
                return;
            }
            var gtog = e.target.closest('[data-vdpg-group-toggle]');
            if (gtog) {
                var gk = gtog.getAttribute('data-vdpg-group-toggle');
                _gcollapse[gk] = !_gcollapse[gk];
                var gel = gtog.closest('.vdpg-group');
                if (gel) {
                    gel.classList.toggle('vdpg-group--collapsed', _gcollapse[gk]);
                    var cr = gel.querySelector('[data-f="caret"]'); if (cr) cr.textContent = _gcollapse[gk] ? '▸' : '▾';
                }
                return;
            }
            var op = e.target.closest('[data-vdpg-open]');
            if (op) {
                var kind = op.getAttribute('data-kind') === 'movie' ? 'movie' : 'show';
                var id = op.getAttribute('data-vdpg-open');
                document.dispatchEvent(new CustomEvent('soulsync:video-open-detail', {
                    detail: { kind: kind, id: parseInt(id, 10) || id, source: op.getAttribute('data-source') || 'library' }
                }));
                return;
            }
            var och = e.target.closest('[data-vdpg-open-channel]');
            if (och) {
                document.dispatchEvent(new CustomEvent('soulsync:video-open-detail', {
                    detail: { kind: 'channel', source: 'youtube', id: och.getAttribute('data-vdpg-open-channel') }
                }));
                return;
            }
            if (e.target.closest('[data-vdpg-import]')) {
                document.dispatchEvent(new CustomEvent('soulsync:video-navigate', { detail: 'video-import' }));
                return;
            }
            var cp = e.target.closest('[data-vdpg-copy]');
            if (cp) {
                var path = cp.getAttribute('data-vdpg-copy');
                if (navigator.clipboard) navigator.clipboard.writeText(path).then(function () { toast('Path copied', 'success'); }, function () {});
                else toast('Copy not supported here', 'info');
                return;
            }
            var bk = e.target.closest('[data-vdpg-block]');
            if (bk) {
                bk.disabled = true;
                var bid = +bk.getAttribute('data-vdpg-block');
                var wasFailed = bk.getAttribute('data-was') === 'failed';
                postJSON(URL_BLOCKLIST, { download_id: bid }).then(function (res) {
                    if (!(res && res.success)) { toast((res && res.error) || 'Could not block that release', 'error'); poll(); return; }
                    if (wasFailed) {
                        postJSON(URL_RETRY, { id: bid }).then(function (r2) {
                            toast(r2 && r2.ok ? 'Release blocked — retrying with another' : 'Release blocked', 'success');
                            poll();
                        });
                    } else { toast('Release blocked', 'success'); poll(); }
                });
                return;
            }
            var c = e.target.closest('[data-vdpg-cancel]');
            if (c) { c.disabled = true; c.classList.add('adl-row-cancel-pending'); postJSON(URL_CANCEL, { id: +c.getAttribute('data-vdpg-cancel') }).then(function () { poll(); }); return; }
            var r = e.target.closest('[data-vdpg-retry]');
            if (r) { r.disabled = true; postJSON(URL_RETRY, { id: +r.getAttribute('data-vdpg-retry') }).then(function (res) {
                if (res && res.ok) toast('Retrying', 'info'); else toast((res && res.error) || 'Retry failed', 'error'); poll(); }); return; }
            // click anywhere on the row (but not the drawer body or a control) → toggle the detail drawer
            if (e.target.closest('[data-f="drawer"]') || e.target.closest('button, a')) return;
            var card = e.target.closest('.adl-row[data-dl-id]');
            if (card && card._d) {
                var cid = card.getAttribute('data-dl-id');
                _expanded[cid] = !_expanded[cid];
                renderDrawer(card, card._d);
            }
        });
    }

    // ── Blocklist modal (reuses the history modal's .vdh-* styling) ────────────
    var _blkEl = null;
    function blkClose() { if (_blkEl) { _blkEl.remove(); _blkEl = null; } }
    function blkRow(it) {
        var media = [it.title, it.season_number != null && it.episode_number != null
            ? 'S' + String(it.season_number).padStart(2, '0') + 'E' + String(it.episode_number).padStart(2, '0') : '']
            .filter(Boolean).join(' ');
        return '<div class="vdh-row" data-id="' + esc(it.id) + '">' +
            '<div class="vdh-row-main">' +
                '<div class="vdh-row-info">' +
                    '<div class="vdh-row-title">' + esc(it.release_title || it.filename || '?') + '</div>' +
                    '<div class="vdh-row-sub">' + esc([media, it.username, it.reason].filter(Boolean).join('  ·  ')) + '</div>' +
                '</div>' +
                '<button class="vdh-redl" type="button" data-vblk-remove="' + esc(it.id) +
                    '" title="Unblock — this release becomes pickable again">Unblock</button>' +
            '</div></div>';
    }
    function blkLoad() {
        if (!_blkEl) return;
        var body = _blkEl.querySelector('[data-vblk-body]');
        getJSON(URL_BLOCKLIST).then(function (d) {
            var items = (d && d.items) || [];
            var clr = _blkEl.querySelector('[data-vblk-clear]');
            if (clr) clr.style.display = items.length ? '' : 'none';
            var sub = _blkEl.querySelector('[data-vblk-sub]');
            if (sub) sub.textContent = items.length ? items.length + ' blocked release' + (items.length === 1 ? '' : 's') : '';
            body.innerHTML = items.length ? items.map(blkRow).join('') :
                '<div class="vdh-empty">Nothing blocked. Releases land here automatically when a ' +
                'downloaded file turns out to be a sample, corrupt or fake — and you can block one ' +
                'yourself from any failed download. Blocked releases are never picked again.</div>';
        });
    }
    function blkOpen() {
        if (_blkEl) return;
        _blkEl = document.createElement('div');
        _blkEl.className = 'vdh-overlay';
        _blkEl.innerHTML =
            '<div class="vdh-modal" role="dialog" aria-modal="true" aria-label="Blocked releases">' +
                '<div class="vdh-head">' +
                    '<div class="vdh-head-titles">' +
                        '<h2 class="vdh-title">Blocked Releases</h2>' +
                        '<p class="vdh-sub" data-vblk-sub></p>' +
                    '</div>' +
                    '<button class="adl-clear-btn" type="button" data-vblk-clear style="display:none">Clear All</button>' +
                    '<button class="vdh-close" type="button" data-vblk-close aria-label="Close">&times;</button>' +
                '</div>' +
                '<div class="vdh-list" data-vblk-body><div class="vdh-empty">Loading…</div></div>' +
            '</div>';
        document.body.appendChild(_blkEl);
        _blkEl.addEventListener('click', function (e) {
            if (e.target === _blkEl || e.target.closest('[data-vblk-close]')) { blkClose(); return; }
            var rm = e.target.closest('[data-vblk-remove]');
            if (rm) {
                rm.disabled = true;
                fetch(URL_BLOCKLIST + '/' + rm.getAttribute('data-vblk-remove'), { method: 'DELETE' })
                    .then(function () { blkLoad(); });
                return;
            }
            if (e.target.closest('[data-vblk-clear]')) {
                var doClear = function () { postJSON(URL_BLOCKLIST + '/clear', {}).then(function () { blkLoad(); }); };
                if (typeof showConfirmDialog === 'function') {
                    showConfirmDialog({ title: 'Clear the blocklist?',
                        message: 'Every blocked release becomes pickable again on future searches.',
                        confirmText: 'Clear all', destructive: true }).then(function (ok) { if (ok) doClear(); });
                } else doClear();
                return;
            }
        });
        blkLoad();
    }
    document.addEventListener('click', function (e) {
        if (e.target.closest('[data-vblk-open]')) blkOpen();
    });

    document.addEventListener('soulsync:video-page-shown', function (e) {
        if (e.detail === 'video-downloads') start(); else stop();
    });
    document.addEventListener('soulsync:video-download-started', function () {
        if (document.querySelector('[data-video-subpage="video-downloads"]:not([hidden])')) setTimeout(poll, 350);
    });

    // Keep the sidebar Downloads badge live even when you're NOT on the page (the on-page
    // poll already refreshes it, so skip the fetch then). Only runs on the video side.
    var _badgeTimer = null;
    function badgePoll() {
        var onVideo = document.body.getAttribute('data-side') === 'video';
        if (onVideo && !_onPage() && !document.hidden) {
            getJSON(URL_ACTIVE).then(function (d) {
                if (d) setDownloadsBadge((d.downloads || []).filter(function (x) { return isActive(x.status); }).length);
                scheduleBadgePoll();
            });
        } else { scheduleBadgePoll(); }
    }
    function scheduleBadgePoll() { if (_badgeTimer) clearTimeout(_badgeTimer); _badgeTimer = setTimeout(badgePoll, 8000); }
    document.addEventListener('soulsync:video-wishlist-changed', function () { setTimeout(badgePoll, 200); });
    scheduleBadgePoll();

    window._vdpgAnyActive = anyActive;
})();
