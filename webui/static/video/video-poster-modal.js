/*
 * SoulSync — Video Poster Manager (full-screen modal).
 *
 * Two entry points, one engine:
 *   VideoPoster.open({kind, tmdbId, libraryId, title, year})  — focused version,
 *       from "Manage Poster" on a detail page (item known → straight to the grid).
 *   VideoPoster.openSearch()  — the FULL manager, a two-pane workspace from the
 *       dashboard: browse/search your library on the left, artwork on the right.
 *
 * Picking a poster POSTs /api/video/poster/set: the server writes poster.jpg into
 * the item folder (server picks it up on scan), points the local DB at it, and
 * best-effort pushes straight to Plex/Jellyfin. Options come from TMDB
 * (/api/video/poster/options/<kind>/<tmdb_id>). Self-contained (own styles).
 */
(function () {
    'use strict';

    function esc(s) {
        return String(s == null ? '' : s)
            .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
    }
    function toast(msg, type) { if (typeof showToast === 'function') showToast(msg, type); }
    function kindSingular(k) { return (k === 'shows' || k === 'show') ? 'show' : 'movie'; }
    function langLabel(l) { return !l ? 'Textless' : String(l).toUpperCase(); }

    // ── one-time styles (kept off the shared 4k-line CSS; mirrors the get-modal) ─
    function ensureStyles() {
        if (document.getElementById('vpm-styles')) return;
        var A = 'var(--accent-rgb, 88 101 242)';
        var css =
            '.vpm-overlay{position:fixed;inset:0;z-index:9200;display:flex;align-items:center;justify-content:center;' +
                'padding:24px;background:rgba(5,5,8,.74);backdrop-filter:blur(10px);opacity:0;transition:opacity .22s ease;}' +
            '.vpm-overlay.vpm-open{opacity:1;}' +
            '.vpm-modal{position:relative;display:flex;flex-direction:column;background:#101015;' +
                'border:1px solid rgba(255,255,255,.08);border-radius:22px;box-shadow:0 50px 130px rgba(0,0,0,.72);' +
                'transform:translateY(16px) scale(.985);transition:transform .26s cubic-bezier(.2,.7,.2,1);overflow:hidden;}' +
            '.vpm-open .vpm-modal{transform:none;}' +
            '.vpm-modal--focused{width:min(820px,96vw);max-height:92vh;}' +
            '.vpm-modal--full{width:min(1160px,96vw);height:min(820px,92vh);flex-direction:row;}' +
            '.vpm-close{position:absolute;top:15px;right:15px;z-index:6;width:36px;height:36px;border-radius:50%;' +
                'border:1px solid rgba(255,255,255,.18);background:rgba(0,0,0,.45);color:#fff;font-size:22px;line-height:1;' +
                'cursor:pointer;backdrop-filter:blur(6px);display:flex;align-items:center;justify-content:center;transition:all .15s;}' +
            '.vpm-close:hover{background:rgba(0,0,0,.72);border-color:rgba(255,255,255,.4);}' +
            // left rail (full mode)
            '.vpm-rail{width:340px;flex:0 0 340px;display:flex;flex-direction:column;background:#0c0c11;' +
                'border-right:1px solid rgba(255,255,255,.07);}' +
            '.vpm-brand{padding:24px 22px 16px;}' +
            '.vpm-brand-kick{display:flex;align-items:center;gap:8px;font-size:11px;font-weight:800;text-transform:uppercase;' +
                'letter-spacing:.09em;color:rgb(' + A + ');}' +
            '.vpm-brand-dot{width:7px;height:7px;border-radius:50%;background:rgb(' + A + ');box-shadow:0 0 10px rgb(' + A + ');}' +
            '.vpm-brand-title{font-size:22px;font-weight:900;letter-spacing:-.02em;margin:9px 0 3px;color:#fff;}' +
            '.vpm-brand-sub{font-size:12.5px;color:rgba(255,255,255,.5);line-height:1.5;}' +
            '.vpm-search{position:relative;padding:0 18px 14px;}' +
            '.vpm-search svg{position:absolute;left:31px;top:50%;transform:translateY(-50%);width:16px;height:16px;' +
                'stroke:rgba(255,255,255,.35);pointer-events:none;}' +
            '.vpm-search-input{width:100%;box-sizing:border-box;padding:12px 14px 12px 38px;border-radius:12px;font-size:14px;' +
                'font-family:inherit;background:rgba(255,255,255,.05);border:1px solid rgba(255,255,255,.1);color:#eef1f7;' +
                'outline:none;transition:border .15s,box-shadow .15s;}' +
            '.vpm-search-input:focus{border-color:rgba(' + A + ',.6);box-shadow:0 0 0 3px rgba(' + A + ',.14);}' +
            '.vpm-results{flex:1;overflow-y:auto;padding:0 12px 14px;display:flex;flex-direction:column;gap:4px;}' +
            '.vpm-results::-webkit-scrollbar{width:8px;}.vpm-results::-webkit-scrollbar-thumb{background:rgba(255,255,255,.12);border-radius:4px;}' +
            '.vpm-result{display:flex;align-items:center;gap:12px;padding:8px;border-radius:11px;cursor:pointer;' +
                'border:1px solid transparent;transition:background .12s,border .12s;}' +
            '.vpm-result:hover{background:rgba(255,255,255,.05);}' +
            '.vpm-result--active{background:rgba(' + A + ',.14);border-color:rgba(' + A + ',.4);}' +
            '.vpm-result-img{width:38px;height:57px;border-radius:6px;object-fit:cover;background:#1b1b22;flex:0 0 auto;}' +
            '.vpm-result-txt{min-width:0;flex:1;}' +
            '.vpm-result-title{font-size:13.5px;color:#eef1f7;font-weight:700;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;}' +
            '.vpm-result-meta{font-size:11.5px;color:rgba(255,255,255,.45);margin-top:2px;display:flex;align-items:center;gap:6px;}' +
            '.vpm-tag{display:inline-block;padding:1px 7px;border-radius:20px;font-size:9.5px;font-weight:800;text-transform:uppercase;' +
                'letter-spacing:.05em;background:rgba(255,255,255,.08);color:rgba(255,255,255,.62);}' +
            '.vpm-tag--show{background:rgba(' + A + ',.2);color:rgb(' + A + ');}' +
            // main pane
            '.vpm-main{flex:1;display:flex;flex-direction:column;min-width:0;overflow:hidden;}' +
            '.vpm-empty{flex:1;display:flex;flex-direction:column;align-items:center;justify-content:center;gap:14px;' +
                'padding:40px;text-align:center;color:rgba(255,255,255,.4);}' +
            '.vpm-empty svg{width:46px;height:46px;stroke:rgba(255,255,255,.18);}' +
            '.vpm-empty-t{font-size:15px;font-weight:700;color:rgba(255,255,255,.66);}' +
            '.vpm-empty-s{font-size:13px;max-width:320px;line-height:1.55;}' +
            // hero
            '.vpm-hero{position:relative;flex:0 0 auto;min-height:190px;display:flex;align-items:flex-end;' +
                'background-size:cover;background-position:center 25%;background-color:#16161d;}' +
            '.vpm-hero-scrim{position:absolute;inset:0;background:linear-gradient(0deg,#101015 6%,rgba(16,16,21,.45) 60%,rgba(16,16,21,.05)),' +
                'radial-gradient(130% 120% at 0% 100%,rgba(' + A + ',.34),transparent 60%);}' +
            '.vpm-hero-poster{position:relative;z-index:2;width:92px;aspect-ratio:2/3;object-fit:cover;border-radius:10px;' +
                'margin:0 0 -30px 26px;background:#16161d;border:1px solid rgba(255,255,255,.16);' +
                'box-shadow:0 14px 34px rgba(0,0,0,.6);}' +
            '.vpm-hero-body{position:relative;z-index:2;padding:22px 26px 18px 18px;min-width:0;}' +
            '.vpm-hero-eyebrow{font-size:11px;font-weight:800;text-transform:uppercase;letter-spacing:.06em;color:rgba(255,255,255,.6);}' +
            '.vpm-hero-title{font-size:24px;font-weight:900;letter-spacing:-.025em;line-height:1.08;margin:6px 0 0;color:#fff;' +
                'text-shadow:0 2px 16px rgba(0,0,0,.55);}' +
            '.vpm-hero-hint{font-size:12.5px;color:rgba(255,255,255,.55);margin-top:8px;}' +
            // panel
            '.vpm-panel{flex:1;overflow-y:auto;padding:34px 26px 18px;}' +
            '.vpm-panel::-webkit-scrollbar{width:9px;}.vpm-panel::-webkit-scrollbar-thumb{background:rgba(255,255,255,.14);border-radius:5px;}' +
            '.vpm-panel-head{display:flex;align-items:center;gap:12px;flex-wrap:wrap;margin-bottom:16px;}' +
            '.vpm-panel-title{font-size:13px;font-weight:800;text-transform:uppercase;letter-spacing:.05em;color:rgba(255,255,255,.7);}' +
            '.vpm-panel-count{color:rgba(255,255,255,.4);font-weight:700;}' +
            '.vpm-langs{display:flex;gap:6px;margin-left:auto;flex-wrap:wrap;}' +
            '.vpm-lang-chip{padding:4px 11px;border-radius:999px;font-size:11px;font-weight:700;cursor:pointer;' +
                'background:rgba(255,255,255,.06);border:1px solid rgba(255,255,255,.1);color:rgba(255,255,255,.6);transition:all .12s;}' +
            '.vpm-lang-chip:hover{color:#fff;border-color:rgba(255,255,255,.24);}' +
            '.vpm-lang-chip--on{background:rgba(' + A + ',.2);border-color:rgba(' + A + ',.5);color:#fff;}' +
            '.vpm-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(126px,1fr));gap:14px;}' +
            '.vpm-poster{position:relative;aspect-ratio:2/3;border-radius:11px;overflow:hidden;cursor:pointer;' +
                'border:2px solid transparent;background:#1b1b22;transition:transform .14s,border-color .12s,box-shadow .14s;}' +
            '.vpm-poster:hover{transform:translateY(-3px);box-shadow:0 14px 30px rgba(0,0,0,.5);}' +
            '.vpm-poster img{width:100%;height:100%;object-fit:cover;display:block;}' +
            '.vpm-poster--sel{border-color:rgb(' + A + ');box-shadow:0 0 0 4px rgba(' + A + ',.28),0 14px 30px rgba(0,0,0,.5);}' +
            '.vpm-poster--sel::after{content:"\\2713";position:absolute;top:7px;right:7px;width:24px;height:24px;border-radius:50%;' +
                'background:rgb(' + A + ');color:#fff;font-size:14px;font-weight:900;display:flex;align-items:center;justify-content:center;' +
                'box-shadow:0 2px 8px rgba(0,0,0,.5);}' +
            '.vpm-lang{position:absolute;bottom:0;left:0;right:0;padding:14px 7px 4px;font-size:9.5px;font-weight:800;' +
                'text-transform:uppercase;letter-spacing:.05em;color:#dfe3ee;background:linear-gradient(transparent,rgba(0,0,0,.78));text-align:right;}' +
            '.vpm-poster[hidden]{display:none;}' +
            '.vpm-sk{aspect-ratio:2/3;border-radius:11px;background:linear-gradient(100deg,#1b1b22 30%,#25252f 50%,#1b1b22 70%);' +
                'background-size:220% 100%;animation:vpm-sh 1.15s ease-in-out infinite;}' +
            '@keyframes vpm-sh{0%{background-position:180% 0;}100%{background-position:-40% 0;}}' +
            '.vpm-note{padding:34px 10px;text-align:center;color:rgba(255,255,255,.42);font-size:13.5px;grid-column:1/-1;}' +
            // footer
            '.vpm-footer{flex:0 0 auto;display:flex;align-items:center;gap:14px;padding:14px 26px;' +
                'border-top:1px solid rgba(255,255,255,.07);background:rgba(0,0,0,.22);}' +
            '.vpm-footer[hidden]{display:none;}' +
            '.vpm-fp{display:flex;align-items:center;gap:11px;min-width:0;}' +
            '.vpm-fp-img{width:34px;height:51px;border-radius:5px;object-fit:cover;background:#1b1b22;border:1px solid rgba(255,255,255,.14);}' +
            '.vpm-fp-img[hidden]{display:none;}' +
            '.vpm-fp-txt{font-size:12.5px;color:rgba(255,255,255,.55);}' +
            '.vpm-fp-txt b{color:#fff;font-weight:800;display:block;font-size:13px;}' +
            '.vpm-status{margin-left:auto;font-size:12.5px;color:rgba(255,255,255,.55);}' +
            '.vpm-apply{padding:11px 24px;border:none;border-radius:12px;font-size:13.5px;font-weight:800;cursor:pointer;' +
                'font-family:inherit;background:rgb(' + A + ');color:#fff;box-shadow:0 8px 24px rgba(' + A + ',.4);transition:all .15s;}' +
            '.vpm-apply:hover:not(:disabled){filter:brightness(1.08);transform:translateY(-1px);}' +
            '.vpm-apply:disabled{opacity:.4;cursor:default;box-shadow:none;}' +
            '@media (max-width:820px){.vpm-modal--full{flex-direction:column;height:92vh;}' +
                '.vpm-rail{width:100%;flex:0 0 auto;max-height:44%;border-right:none;border-bottom:1px solid rgba(255,255,255,.07);}}';
        var st = document.createElement('style');
        st.id = 'vpm-styles';
        st.textContent = css;
        document.head.appendChild(st);
    }

    // ── modal shell ────────────────────────────────────────────────────────────
    var overlay = null, keyHandler = null, mode = 'search';
    var state = null;   // { kind, tmdbId, libraryId, title, year, hasPoster, posters, lang, selected }

    function close() {
        if (!overlay) return;
        overlay.classList.remove('vpm-open');
        document.body.style.removeProperty('overflow');
        if (keyHandler) { document.removeEventListener('keydown', keyHandler); keyHandler = null; }
        var el = overlay; overlay = null; state = null;
        setTimeout(function () { if (el && el.parentNode) el.parentNode.removeChild(el); }, 240);
    }

    function build(m) {
        ensureStyles();
        close();
        mode = m;
        var ov = document.createElement('div');
        ov.className = 'vpm-overlay';
        var railHTML =
            '<div class="vpm-rail">' +
                '<div class="vpm-brand">' +
                    '<div class="vpm-brand-kick"><span class="vpm-brand-dot"></span> Artwork Studio</div>' +
                    '<div class="vpm-brand-title">Poster Manager</div>' +
                    '<div class="vpm-brand-sub">Search your library, then swap in fresh cover art — pushed straight to your media server.</div>' +
                '</div>' +
                '<div class="vpm-search">' +
                    '<svg viewBox="0 0 24 24" fill="none" stroke-width="2" stroke-linecap="round"><circle cx="11" cy="11" r="7"/><path d="m21 21-4.3-4.3"/></svg>' +
                    '<input class="vpm-search-input" data-vpm-search type="text" placeholder="Search movies &amp; shows…" autocomplete="off">' +
                '</div>' +
                '<div class="vpm-results" data-vpm-results>' + emptyRail() + '</div>' +
            '</div>';
        ov.innerHTML =
            '<div class="vpm-modal ' + (m === 'search' ? 'vpm-modal--full' : 'vpm-modal--focused') + '" role="dialog" aria-modal="true">' +
                '<button class="vpm-close" type="button" data-vpm-close aria-label="Close">&times;</button>' +
                (m === 'search' ? railHTML : '') +
                '<div class="vpm-main" data-vpm-main>' + (m === 'search' ? emptyMain() : '') + '</div>' +
            '</div>';
        document.body.appendChild(ov);
        document.body.style.overflow = 'hidden';
        overlay = ov;
        requestAnimationFrame(function () { ov.classList.add('vpm-open'); });

        ov.addEventListener('click', function (e) {
            if (e.target === ov || e.target.closest('[data-vpm-close]')) { close(); return; }
            var res = e.target.closest('[data-vpm-result]');
            if (res) { pickItem(JSON.parse(res.getAttribute('data-vpm-result')), res); return; }
            var chip = e.target.closest('[data-vpm-lang]');
            if (chip) { setLang(chip.getAttribute('data-vpm-lang')); return; }
            var p = e.target.closest('[data-vpm-poster]');
            if (p) { selectPoster(p); return; }
            if (e.target.closest('[data-vpm-apply]')) { apply(); }
        });
        if (m === 'search') {
            var input = ov.querySelector('[data-vpm-search]');
            var t = null;
            input.addEventListener('input', function () {
                clearTimeout(t);
                var q = input.value.trim();
                t = setTimeout(function () { runSearch(q); }, 240);
            });
            setTimeout(function () { input.focus(); }, 80);
        }
        keyHandler = function (e) { if (e.key === 'Escape') close(); };
        document.addEventListener('keydown', keyHandler);
        return ov;
    }

    function emptyRail() {
        return '<div class="vpm-empty" style="padding:30px 20px;"><div class="vpm-empty-s">Start typing to find something in your library.</div></div>';
    }
    function emptyMain() {
        return '<div class="vpm-empty">' +
            '<svg viewBox="0 0 24 24" fill="none" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round">' +
                '<rect x="3" y="3" width="18" height="18" rx="2"/><path d="M3 15l5-5 4 4 3-3 6 6"/><circle cx="8.5" cy="8.5" r="1.5"/></svg>' +
            '<div><div class="vpm-empty-t">Pick something to re-poster</div>' +
            '<div class="vpm-empty-s">Search on the left and choose a movie or show to see its available cover art.</div></div>' +
            '</div>';
    }

    // ── search (rail) ──────────────────────────────────────────────────────────
    function libSearch(kind, q) {
        return fetch('/api/video/library?kind=' + kind + '&search=' + encodeURIComponent(q) + '&limit=14',
            { headers: { Accept: 'application/json' } })
            .then(function (r) { return r.ok ? r.json() : null; })
            .then(function (d) { return (d && d.items) || []; })
            .catch(function () { return []; });
    }

    function runSearch(q) {
        if (!overlay) return;
        var box = overlay.querySelector('[data-vpm-results]');
        if (!box) return;
        if (q.length < 2) { box.innerHTML = emptyRail(); return; }
        box.innerHTML = '<div class="vpm-note" style="padding:24px;">Searching…</div>';
        Promise.all([libSearch('movies', q), libSearch('shows', q)]).then(function (r) {
            if (!overlay) return;
            var rows = [];
            (r[0] || []).forEach(function (m) { rows.push(mkItem('movie', m)); });
            (r[1] || []).forEach(function (s) { rows.push(mkItem('show', s)); });
            rows.sort(function (a, b) { return String(a.title).localeCompare(String(b.title)); });
            if (!rows.length) { box.innerHTML = '<div class="vpm-note" style="padding:24px;">No matches in your library.</div>'; return; }
            box.innerHTML = rows.map(renderResult).join('');
        });
    }

    function mkItem(kind, it) {
        return { kind: kind, id: it.id, tmdbId: it.tmdb_id || null, title: it.title || '',
            year: it.year || null, hasPoster: !!it.has_poster };
    }

    function renderResult(it) {
        var thumb = it.hasPoster ? '/api/video/poster/' + it.kind + '/' + it.id + '?w=80' : '';
        return '<div class="vpm-result" data-vpm-result="' + esc(JSON.stringify(it)) + '">' +
            (thumb ? '<img class="vpm-result-img" src="' + esc(thumb) + '" alt="" loading="lazy">'
                   : '<div class="vpm-result-img"></div>') +
            '<div class="vpm-result-txt">' +
                '<div class="vpm-result-title">' + esc(it.title) + '</div>' +
                '<div class="vpm-result-meta">' +
                    '<span class="vpm-tag' + (it.kind === 'show' ? ' vpm-tag--show' : '') + '">' + (it.kind === 'show' ? 'TV' : 'Movie') + '</span>' +
                    (it.year ? '<span>' + esc(it.year) + '</span>' : '') +
                '</div>' +
            '</div></div>';
    }

    function pickItem(it, rowEl) {
        if (!it.tmdbId) { toast("No TMDB match on that item — can't fetch alternate posters", 'info'); return; }
        if (overlay) {
            var prev = overlay.querySelector('.vpm-result--active');
            if (prev) prev.classList.remove('vpm-result--active');
            if (rowEl) rowEl.classList.add('vpm-result--active');
        }
        state = { kind: it.kind, tmdbId: it.tmdbId, libraryId: it.id, title: it.title, year: it.year,
            hasPoster: it.hasPoster !== false, posters: null, lang: 'all', selected: null };
        renderMain();
        loadPosters();
    }

    // ── main pane (hero + poster grid + footer) ────────────────────────────────
    function renderMain() {
        var main = overlay && overlay.querySelector('[data-vpm-main]');
        if (!main || !state) return;
        var kind = state.kind, id = state.libraryId;
        var backdrop = (id != null) ? "url('/api/video/backdrop/" + kind + '/' + id + "?w=780')" : '';
        var curPoster = (state.hasPoster && id != null) ? '/api/video/poster/' + kind + '/' + id + '?w=110' : '';
        main.innerHTML =
            '<div class="vpm-hero" data-vpm-hero style="' + (backdrop ? 'background-image:' + backdrop + ';' : '') + '">' +
                '<div class="vpm-hero-scrim"></div>' +
                (curPoster
                    ? '<img class="vpm-hero-poster" data-vpm-cur src="' + esc(curPoster) + '" alt="" onerror="this.style.display=\'none\'">'
                    : '') +
                '<div class="vpm-hero-body">' +
                    '<div class="vpm-hero-eyebrow">' + (kind === 'show' ? 'TV Show' : 'Movie') +
                        (state.year ? '  ·  ' + esc(state.year) : '') + '</div>' +
                    '<h2 class="vpm-hero-title">' + esc(state.title) + '</h2>' +
                    '<div class="vpm-hero-hint">Choose a new poster below — it replaces the current one everywhere.</div>' +
                '</div>' +
            '</div>' +
            '<div class="vpm-panel">' +
                '<div class="vpm-panel-head">' +
                    '<span class="vpm-panel-title">Posters <span class="vpm-panel-count" data-vpm-count></span></span>' +
                    '<span class="vpm-langs" data-vpm-langs></span>' +
                '</div>' +
                '<div class="vpm-grid" data-vpm-grid>' + skeletons() + '</div>' +
            '</div>' +
            '<div class="vpm-footer" data-vpm-footer hidden>' +
                '<div class="vpm-fp">' +
                    '<img class="vpm-fp-img" data-vpm-fp-img alt="" hidden>' +
                    '<div class="vpm-fp-txt" data-vpm-fp-txt>Select a poster to apply</div>' +
                '</div>' +
                '<span class="vpm-status" data-vpm-status></span>' +
                '<button class="vpm-apply" type="button" data-vpm-apply disabled>Apply Poster</button>' +
            '</div>';
    }

    function skeletons() {
        var s = '';
        for (var i = 0; i < 10; i++) s += '<div class="vpm-sk"></div>';
        return s;
    }

    function loadPosters() {
        if (!state) return;
        fetch('/api/video/poster/options/' + state.kind + '/' + state.tmdbId, { headers: { Accept: 'application/json' } })
            .then(function (r) { return r.ok ? r.json() : null; })
            .then(function (d) {
                if (!overlay || !state) return;
                state.posters = (d && d.posters) || [];
                renderPosters();
            })
            .catch(function () {
                var grid = overlay && overlay.querySelector('[data-vpm-grid]');
                if (grid) grid.innerHTML = '<div class="vpm-note">Couldn’t load posters. Try again.</div>';
            });
    }

    function renderPosters() {
        var grid = overlay && overlay.querySelector('[data-vpm-grid]');
        var count = overlay && overlay.querySelector('[data-vpm-count]');
        var langs = overlay && overlay.querySelector('[data-vpm-langs]');
        if (!grid || !state) return;
        var posters = state.posters || [];
        if (!posters.length) {
            grid.innerHTML = '<div class="vpm-note">No alternate posters found on TMDB for this title.</div>';
            if (count) count.textContent = '';
            return;
        }
        if (count) count.textContent = posters.length;
        // language filter chips (only when there's real variety)
        if (langs) {
            var present = [];
            posters.forEach(function (p) { var k = p.lang || ''; if (present.indexOf(k) === -1) present.push(k); });
            if (present.length > 1) {
                var chips = ['<span class="vpm-lang-chip' + (state.lang === 'all' ? ' vpm-lang-chip--on' : '') +
                    '" data-vpm-lang="all">All</span>'];
                // en first, textless next, then the rest
                present.sort(function (a, b) {
                    return (a === 'en' ? 0 : a === '' ? 1 : 2) - (b === 'en' ? 0 : b === '' ? 1 : 2);
                });
                present.forEach(function (l) {
                    chips.push('<span class="vpm-lang-chip' + (state.lang === (l || 'null') ? ' vpm-lang-chip--on' : '') +
                        '" data-vpm-lang="' + (l || 'null') + '">' + langLabel(l) + '</span>');
                });
                langs.innerHTML = chips.join('');
            } else { langs.innerHTML = ''; }
        }
        var want = state.lang;
        grid.innerHTML = posters.map(function (p) {
            var lk = p.lang || 'null';
            var hide = (want !== 'all' && want !== lk);
            var selCls = (state.selected === p.full) ? ' vpm-poster--sel' : '';
            return '<div class="vpm-poster' + selCls + '" data-vpm-poster data-vpm-full="' + esc(p.full) + '"' +
                (hide ? ' hidden' : '') + '>' +
                '<img src="' + esc(p.thumb) + '" alt="" loading="lazy">' +
                (p.lang && p.lang !== 'en' ? '<span class="vpm-lang">' + esc(p.lang) + '</span>' : '') +
                '</div>';
        }).join('');
    }

    function setLang(l) {
        if (!state) return;
        state.lang = l;
        renderPosters();
    }

    function selectPoster(el) {
        if (!state || !overlay) return;
        var prev = overlay.querySelector('.vpm-poster--sel');
        if (prev) prev.classList.remove('vpm-poster--sel');
        el.classList.add('vpm-poster--sel');
        state.selected = el.getAttribute('data-vpm-full');
        var footer = overlay.querySelector('[data-vpm-footer]');
        if (footer) footer.hidden = false;
        var img = overlay.querySelector('[data-vpm-fp-img]');
        var thumb = el.querySelector('img');
        if (img && thumb) { img.src = thumb.src; img.hidden = false; }
        var txt = overlay.querySelector('[data-vpm-fp-txt]');
        if (txt) txt.innerHTML = '<b>New poster</b>ready to apply';
        var btn = overlay.querySelector('[data-vpm-apply]');
        if (btn) btn.disabled = false;
        setStatus('');
    }

    function setStatus(msg) {
        var s = overlay && overlay.querySelector('[data-vpm-status]');
        if (s) s.textContent = msg || '';
    }

    function apply() {
        if (!state || !state.selected) return;
        var btn = overlay.querySelector('[data-vpm-apply]');
        if (btn) btn.disabled = true;
        setStatus('Applying…');
        var chosen = state.selected;
        fetch('/api/video/poster/set', {
            method: 'POST', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ kind: state.kind, id: state.libraryId, poster_url: chosen }),
        })
            .then(function (r) { return r.json().then(function (d) { return { ok: r.ok, d: d }; }); })
            .then(function (res) {
                if (!res.ok || !res.d || !res.d.ok) {
                    setStatus('');
                    if (btn) btn.disabled = false;
                    toast((res.d && res.d.error) || 'Could not change poster', 'error');
                    return;
                }
                toast('Poster updated' + (res.d.pushed_server ? '' : ' — server will pick it up on next scan'), 'success');
                document.dispatchEvent(new CustomEvent('soulsync:video-poster-changed', {
                    detail: { kind: state.kind, id: state.libraryId, poster_url: chosen },
                }));
                if (mode === 'search') {
                    // full manager: stay open so they can re-poster more. Reflect the
                    // change on the hero + the rail thumbnail; clear the selection.
                    state.hasPoster = true;
                    var cb = Date.now();
                    var base = '/api/video/poster/' + state.kind + '/' + state.libraryId;
                    var cur = overlay.querySelector('[data-vpm-cur]');
                    if (cur) { cur.src = base + '?w=110&_cb=' + cb; cur.style.display = ''; }
                    var rowImg = overlay.querySelector('.vpm-result--active .vpm-result-img');
                    if (rowImg && rowImg.tagName === 'IMG') rowImg.src = base + '?w=80&_cb=' + cb;
                    state.selected = null;
                    var footer = overlay.querySelector('[data-vpm-footer]');
                    if (footer) footer.hidden = true;
                    renderPosters();
                    setStatus('Applied ✓');
                } else {
                    close();
                }
            })
            .catch(function () {
                setStatus('');
                if (btn) btn.disabled = false;
                toast('Could not change poster', 'error');
            });
    }

    // ── public API ─────────────────────────────────────────────────────────────
    // Focused entry (detail page): item already known → straight to the grid.
    function open(opts) {
        if (!opts || !opts.kind) return;
        var kind = kindSingular(opts.kind);
        if (!opts.tmdbId) { toast("No TMDB match — can't fetch alternate posters", 'info'); return; }
        build('direct');
        state = { kind: kind, tmdbId: opts.tmdbId, libraryId: (opts.libraryId != null ? opts.libraryId : null),
            title: opts.title || '', year: opts.year || null,
            hasPoster: opts.libraryId != null, posters: null, lang: 'all', selected: null };
        renderMain();
        loadPosters();
    }
    // Full manager entry (dashboard quick action).
    function openSearch() { build('search'); }

    window.VideoPoster = { open: open, openSearch: openSearch, close: close };
})();
