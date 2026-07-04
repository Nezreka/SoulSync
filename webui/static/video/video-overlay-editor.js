/*
 * SoulSync — Overlay Studio (Artwork Studio): the visual overlay-template editor.
 *
 * A full-bleed design tool for authoring poster overlays. Phase 1a: a gallery of
 * saved templates + a canvas editor (palette · stage · layers) that can add text
 * layers, drag/select/reorder them, and save/load the design. Compositing a
 * template onto real posters (the "apply" pipeline) is a separate later module.
 *
 * COORDINATE MODEL (the whole "works on every poster" trick): every layer stores
 * a normalized anchor-point position — x,y in [0..1] of the stage — plus a
 * 9-point `anchor` and sizes as fractions of the stage. Rendering multiplies by
 * the real poster dimensions, so one template fits any resolution.
 *
 * Self-contained IIFE; styled by video-overlay-editor.css. window.VideoOverlayEditor.
 */
(function () {
    'use strict';

    function esc(s) {
        return String(s == null ? '' : s)
            .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
    }
    function toast(m, t) { if (typeof showToast === 'function') showToast(m, t); }
    function uid() { return 'l' + Math.random().toString(36).slice(2, 9); }

    // ── bundled font set (browser preview uses real fallback stacks now; exact
    // woff2 bundling lands with the apply pipeline for pixel parity). No system
    // free-entry — users pick from this curated list only. ─────────────────────
    var FONTS = [
        { id: 'Inter', label: 'Inter', stack: "'Inter',system-ui,-apple-system,'Segoe UI',Roboto,sans-serif" },
        { id: 'Archivo', label: 'Archivo Black', stack: "'Archivo Black','Arial Black',system-ui,sans-serif" },
        { id: 'Oswald', label: 'Oswald', stack: "'Oswald','Bebas Neue','Haettenschweiler','Impact',sans-serif" },
        { id: 'Bebas', label: 'Bebas Neue', stack: "'Bebas Neue','Oswald','Impact',sans-serif" },
        { id: 'Anton', label: 'Anton', stack: "'Anton','Impact','Arial Black',sans-serif" },
        { id: 'RobotoCondensed', label: 'Roboto Condensed', stack: "'Roboto Condensed','Arial Narrow',sans-serif" },
        { id: 'Montserrat', label: 'Montserrat', stack: "'Montserrat',system-ui,sans-serif" },
        { id: 'Georgia', label: 'Georgia', stack: "Georgia,'Times New Roman',serif" },
    ];
    function fontStack(id) { for (var i = 0; i < FONTS.length; i++) if (FONTS[i].id === id) return FONTS[i].stack; return FONTS[0].stack; }

    // ── dynamic-badge fields: each maps sample data → the badge's display text.
    // `fmt` returns null when there's no value (the editor then shows a placeholder;
    // at apply time such a badge would simply not render). `opts`/`num`/text drive
    // the sample-data editor controls. ─────────────────────────────────────────
    function up(v) { return String(v).toUpperCase(); }
    var FIELDS = {
        resolution: { label: 'Resolution', cat: 'Quality', opts: ['2160p', '1080p', '720p', '480p'], fmt: function (v) {
            if (!v) return null; var s = String(v).toLowerCase();
            if (s.indexOf('2160') > -1 || s === '4k') return '4K';
            if (s.indexOf('1080') > -1) return '1080p';
            if (s.indexOf('720') > -1) return '720p';
            if (s.indexOf('480') > -1 || s.indexOf('576') > -1) return 'SD'; return up(v); } },
        hdr: { label: 'HDR / DV', cat: 'Quality', opts: ['HDR', 'HDR10+', 'Dolby Vision', ''], fmt: function (v) { return v ? up(v) : null; } },
        video_codec: { label: 'Video codec', cat: 'Quality', opts: ['hevc', 'h264', 'av1', 'vp9'], fmt: function (v) {
            if (!v) return null; var s = String(v).toLowerCase();
            if (s.indexOf('hevc') > -1 || s.indexOf('265') > -1) return 'HEVC';
            if (s.indexOf('264') > -1 || s === 'avc') return 'H.264';
            if (s.indexOf('av1') > -1) return 'AV1'; if (s.indexOf('vp9') > -1) return 'VP9'; return up(v); } },
        audio_codec: { label: 'Audio codec', cat: 'Quality', opts: ['atmos', 'truehd', 'dts-hd', 'dts', 'ac3', 'aac'], fmt: function (v) {
            if (!v) return null; var s = String(v).toLowerCase();
            if (s.indexOf('atmos') > -1) return 'ATMOS'; if (s.indexOf('truehd') > -1) return 'TrueHD'; return up(v); } },
        source: { label: 'Source', cat: 'Quality', opts: ['bluray', 'web-dl', 'webrip', 'hdtv', 'remux', 'dvd'], fmt: function (v) {
            if (!v) return null; var m = { bluray: 'BluRay', 'web-dl': 'WEB-DL', webdl: 'WEB-DL', webrip: 'WEBRip', hdtv: 'HDTV', remux: 'REMUX', dvd: 'DVD' };
            return m[String(v).toLowerCase()] || up(v); } },
        imdb: { label: 'IMDb rating', cat: 'Ratings', num: true, fmt: function (v) { return v == null ? null : 'IMDb ' + (Math.round(v * 10) / 10); } },
        rt: { label: 'Rotten Tomatoes', cat: 'Ratings', num: true, fmt: function (v) { return v == null ? null : 'RT ' + v + '%'; } },
        metacritic: { label: 'Metacritic', cat: 'Ratings', num: true, fmt: function (v) { return v == null ? null : 'MC ' + v; } },
        tmdb: { label: 'TMDB rating', cat: 'Ratings', num: true, fmt: function (v) { return v == null ? null : 'TMDB ' + (Math.round(v * 10) / 10); } },
        content_rating: { label: 'Content rating', cat: 'Details', opts: ['G', 'PG', 'PG-13', 'R', 'NC-17', 'TV-Y', 'TV-PG', 'TV-14', 'TV-MA'], fmt: function (v) { return v ? up(v) : null; } },
        status: { label: 'Status', cat: 'Details', opts: ['Returning', 'Ended', 'Released', 'Upcoming', 'Canceled'], fmt: function (v) {
            if (!v) return null; var s = String(v).toLowerCase();
            if (s.indexOf('cancel') > -1) return 'Canceled'; if (s.indexOf('end') > -1) return 'Ended';
            if (s.indexOf('continu') > -1 || s.indexOf('return') > -1) return 'Returning';
            if (s.indexOf('releas') > -1) return 'Released';
            if (s.indexOf('upcom') > -1 || s.indexOf('announc') > -1 || s.indexOf('production') > -1) return 'Upcoming'; return up(v); } },
        year: { label: 'Year', cat: 'Details', num: true, fmt: function (v) { return v == null ? null : String(v); } },
        runtime: { label: 'Runtime', cat: 'Details', num: true, fmt: function (v) {
            if (v == null) return null; var h = Math.floor(v / 60), m = v % 60; return h ? (h + 'h' + (m ? ' ' + m + 'm' : '')) : (m + 'm'); } },
        season_count: { label: 'Seasons', cat: 'Details', num: true, fmt: function (v) { return v == null ? null : v + ' Season' + (v == 1 ? '' : 's'); } },
        episode_count: { label: 'Episodes', cat: 'Details', num: true, fmt: function (v) { return v == null ? null : v + ' Episodes'; } },
        title: { label: 'Title', cat: 'Details', text: true, fmt: function (v) { return v ? String(v) : null; } },
        network: { label: 'Network', cat: 'Details', text: true, fmt: function (v) { return v ? String(v) : null; } },
        studio: { label: 'Studio', cat: 'Details', text: true, fmt: function (v) { return v ? String(v) : null; } },
    };
    var FIELD_ORDER = ['resolution', 'hdr', 'video_codec', 'audio_codec', 'source', 'imdb', 'rt', 'metacritic', 'tmdb',
        'content_rating', 'status', 'year', 'runtime', 'season_count', 'episode_count', 'title', 'network', 'studio'];
    var FIELD_CATS = ['Quality', 'Ratings', 'Details'];

    function defaultSample() {
        return { resolution: '2160p', hdr: 'HDR', video_codec: 'hevc', audio_codec: 'atmos', source: 'bluray',
            imdb: 8.4, rt: 92, metacritic: 81, tmdb: 8.1, content_rating: 'PG-13', status: 'Returning',
            year: 2021, runtime: 148, season_count: 4, episode_count: 62, title: 'Example Title', network: 'HBO', studio: 'A24' };
    }
    // real values win; nulls fall back to the defaults so no badge previews blank.
    function mergeSample(real) {
        var d = defaultSample(); if (!real) return d;
        FIELD_ORDER.forEach(function (k) { if (real[k] != null && real[k] !== '') d[k] = real[k]; });
        d.logo_url = real.logo_url || null;   // logo art (not a text field) for logo layers
        return d;
    }
    function resolveBinding(b) {
        var f = FIELDS[b.field]; if (!f) return '?';
        var out = f.fmt(ed.sample ? ed.sample[b.field] : null);
        return out == null ? '[' + f.label + ']' : out;
    }

    // ── 9-point anchors: fraction of the ELEMENT that pins to (x,y). ───────────
    var ANCHORS = {
        'top-left': [0, 0], 'top-center': [0.5, 0], 'top-right': [1, 0],
        'mid-left': [0, 0.5], 'center': [0.5, 0.5], 'mid-right': [1, 0.5],
        'bottom-left': [0, 1], 'bottom-center': [0.5, 1], 'bottom-right': [1, 1],
    };
    function anchorFrac(a) { return ANCHORS[a] || ANCHORS.center; }

    // ── SVG icons ──────────────────────────────────────────────────────────────
    var I = {
        brand: '<svg viewBox="0 0 24 24" fill="none" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="3" width="18" height="18" rx="2"/><path d="M3 15l5-5 4 4"/><circle cx="8.5" cy="8.5" r="1.5"/><path d="M14 13l3-3 4 4"/></svg>',
        text: '<svg viewBox="0 0 24 24" fill="none" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M4 7V5h16v2"/><path d="M12 5v14"/><path d="M9 19h6"/></svg>',
        eye: '<svg viewBox="0 0 24 24" fill="none" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7-10-7-10-7Z"/><circle cx="12" cy="12" r="3"/></svg>',
        eyeOff: '<svg viewBox="0 0 24 24" fill="none" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M9.9 4.2A10.9 10.9 0 0 1 12 4c6.5 0 10 7 10 7a15 15 0 0 1-2.9 3.6"/><path d="M6.6 6.6A15 15 0 0 0 2 11s3.5 7 10 7a10.9 10.9 0 0 0 3.6-.6"/><path d="M3 3l18 18"/></svg>',
        trash: '<svg viewBox="0 0 24 24" fill="none" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 6h18M8 6V4h8v2m-9 0v14a1 1 0 0 0 1 1h6a1 1 0 0 0 1-1V6"/></svg>',
        grip: '<svg viewBox="0 0 24 24" fill="currentColor"><circle cx="9" cy="6" r="1.6"/><circle cx="15" cy="6" r="1.6"/><circle cx="9" cy="12" r="1.6"/><circle cx="15" cy="12" r="1.6"/><circle cx="9" cy="18" r="1.6"/><circle cx="15" cy="18" r="1.6"/></svg>',
        copy: '<svg viewBox="0 0 24 24" fill="none" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="9" y="9" width="12" height="12" rx="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/></svg>',
        back: '<svg viewBox="0 0 24 24" fill="none" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="M15 18l-6-6 6-6"/></svg>',
        save: '<svg viewBox="0 0 24 24" fill="none" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M19 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11l5 5v11a2 2 0 0 1-2 2Z"/><path d="M17 21v-8H7v8M7 3v5h8"/></svg>',
        badge: '<svg viewBox="0 0 24 24" fill="none" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="7" width="18" height="10" rx="3"/><path d="M7 12h2m3 0h5"/></svg>',
        star: '<svg viewBox="0 0 24 24" fill="none" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 3l2.6 5.3 5.9.9-4.3 4.1 1 5.8-5.2-2.7-5.2 2.7 1-5.8L3.5 9.2l5.9-.9L12 3Z"/></svg>',
        info: '<svg viewBox="0 0 24 24" fill="none" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="9"/><path d="M12 8h.01M11 12h1v4h1"/></svg>',
        chev: '<svg viewBox="0 0 24 24" fill="none" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="M6 9l6 6 6-6"/></svg>',
        undo: '<svg viewBox="0 0 24 24" fill="none" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M9 14L4 9l5-5"/><path d="M4 9h11a5 5 0 0 1 0 10h-1"/></svg>',
        redo: '<svg viewBox="0 0 24 24" fill="none" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M15 14l5-5-5-5"/><path d="M20 9H9a5 5 0 0 0 0 10h1"/></svg>',
        dupe: '<svg viewBox="0 0 24 24" fill="none" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="9" y="9" width="12" height="12" rx="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/></svg>',
        apply: '<svg viewBox="0 0 24 24" fill="none" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 3l2 4 4 .5-3 3 .8 4.2L12 16l-3.8 1.7.8-4.2-3-3 4-.5 2-4Z"/><path d="M5 20h14"/></svg>',
        help: '<svg viewBox="0 0 24 24" fill="none" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="9"/><path d="M9.5 9a2.5 2.5 0 1 1 3.5 2.3c-.8.4-1 .9-1 1.7"/><path d="M12 17h.01"/></svg>',
        dice: '<svg viewBox="0 0 24 24" fill="none" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="3" width="18" height="18" rx="4"/><circle cx="8.5" cy="8.5" r="1.2" fill="currentColor" stroke="none"/><circle cx="15.5" cy="15.5" r="1.2" fill="currentColor" stroke="none"/><circle cx="12" cy="12" r="1.2" fill="currentColor" stroke="none"/></svg>',
        poster: '<svg viewBox="0 0 24 24" fill="none" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="4" y="3" width="16" height="18" rx="2"/><path d="M4 15l4-4 3 3 3-3 6 6"/></svg>',
        image: '<svg viewBox="0 0 24 24" fill="none" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="3" width="18" height="18" rx="2"/><circle cx="8.5" cy="8.5" r="1.5"/><path d="M21 15l-5-5L5 21"/></svg>',
        logo: '<svg viewBox="0 0 24 24" fill="none" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M4 7h16M4 7v10a1 1 0 0 0 1 1h14a1 1 0 0 0 1-1V7M9 12h6"/></svg>',
        shape: '<svg viewBox="0 0 24 24" fill="none" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="6" width="18" height="12" rx="2"/></svg>',
        scrim: '<svg viewBox="0 0 24 24" fill="none" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="3" width="18" height="18" rx="2"/><path d="M3 14h18" opacity=".5"/><path d="M3 17h18" opacity=".8"/></svg>',
    };
    function catIcon(cat) { return cat === 'Ratings' ? I.star : cat === 'Quality' ? I.badge : I.info; }

    // ── overlay + view state ────────────────────────────────────────────────────
    var overlay = null;         // the .voe-overlay root
    var ed = null;              // editor state (null while in gallery)
    var resizeBound = null;

    function ensureOverlay() {
        if (overlay) return overlay;
        overlay = document.createElement('div');
        overlay.className = 'voe-overlay';
        document.body.appendChild(overlay);
        return overlay;
    }

    function open(templateId) {
        ensureOverlay();
        document.body.classList.add('vdh-locked');
        requestAnimationFrame(function () { overlay.classList.add('voe-overlay--on'); });
        if (templateId != null) loadTemplate(templateId);
        else showGallery();
    }

    function hardClose() {
        if (!overlay) return;
        closePop();
        overlay.classList.remove('voe-overlay--on');
        document.body.classList.remove('vdh-locked');
        if (resizeBound) { window.removeEventListener('resize', resizeBound); resizeBound = null; }
        ed = null;
        setTimeout(function () { if (overlay) overlay.innerHTML = ''; }, 260);
    }

    // Leaving the editor auto-saves (it's the user's own template — no data loss).
    function close() {
        if (ed && ed.dirty) { saveTemplate().finally(hardClose); return; }
        hardClose();
    }

    // ── API helpers ─────────────────────────────────────────────────────────────
    function api(method, url, body) {
        return fetch(url, {
            method: method, headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
            body: body ? JSON.stringify(body) : undefined,
        }).then(function (r) { return r.ok ? r.json() : Promise.reject(r); });
    }

    // ── GALLERY ─────────────────────────────────────────────────────────────────
    function showGallery() {
        ed = null;
        overlay.innerHTML =
            '<div class="voe-topbar">' +
                '<div class="voe-brand"><span class="voe-brand-mark">' + I.brand + '</span>' +
                    '<span class="voe-brand-name">Overlay Studio</span></div>' +
                '<div class="voe-top-spacer"></div>' +
                '<button class="voe-btn voe-btn--primary" data-voe-apply-open>' + I.apply + ' Apply to library</button>' +
                '<button class="voe-x" data-voe-close aria-label="Close">&times;</button>' +
            '</div>' +
            '<div class="voe-gallery"><div class="voe-gallery-inner">' +
                '<div class="voe-gallery-head">' +
                    '<div class="voe-gallery-kick">Artwork Studio</div>' +
                    '<h1 class="voe-gallery-title">Overlay Templates</h1>' +
                    '<p class="voe-gallery-sub">Design badge &amp; artwork overlays on a canvas — resolution, ratings, logos, text — then reuse them across your library. Positions are relative, so one template fits every poster.</p>' +
                '</div>' +
                '<div class="voe-grid" data-voe-grid><div class="voe-gallery-empty">Loading…</div></div>' +
            '</div></div>';
        overlay.querySelector('[data-voe-close]').addEventListener('click', close);
        overlay.querySelector('[data-voe-apply-open]').addEventListener('click', openApplyDialog);
        loadGallery();
    }

    // ── apply overlays to the library ────────────────────────────────────────────
    var applyPollTimer = null;
    function openApplyDialog() {
        api('GET', '/api/video/overlays/assignments').then(function (d) {
            renderApplyDialog(d || { assignments: {}, templates: [], applied: 0 });
        }).catch(function () { toast('Could not load apply settings', 'error'); });
    }
    function scopeRow(label, scope, assign, templates) {
        var cur = (assign && assign.template_id) || '';
        var opts = '<option value="">— None —</option>' + templates.map(function (t) {
            return '<option value="' + t.id + '"' + (String(t.id) === String(cur) ? ' selected' : '') + '>' + esc(t.name) + '</option>';
        }).join('');
        var on = !!(assign && assign.enabled && assign.template_id);
        return '<div class="voe-apply-row"><div class="voe-apply-row-l">' + label + '</div>' +
            '<select class="voe-input" data-apply-tpl="' + scope + '">' + opts + '</select>' +
            '<button class="voe-toggle' + (on ? ' voe-toggle--on' : '') + '" data-apply-en="' + scope + '"></button></div>';
    }
    function renderApplyDialog(d) {
        var templates = d.templates || [], a = d.assignments || {};
        var back = document.createElement('div');
        back.className = 'voe-confirm-back';
        back.innerHTML = '<div class="voe-apply-modal">' +
            '<div class="voe-apply-t">Apply overlays</div>' +
            '<div class="voe-apply-sub">Pick a template for each library and burn it onto every poster. Runs from a clean copy each time and pushes to your server; originals are backed up so you can remove them anytime.</div>' +
            scopeRow('Movies', 'movie', a.movie, templates) +
            scopeRow('TV Shows', 'show', a.show, templates) +
            '<div class="voe-apply-applied" data-apply-count>' + (d.applied || 0) + ' item' + (d.applied === 1 ? '' : 's') + ' currently overlaid</div>' +
            '<div class="voe-apply-prog" data-apply-prog hidden><div class="voe-apply-bar"><div class="voe-apply-bar-fill" data-apply-fill></div></div>' +
            '<div class="voe-apply-prog-txt" data-apply-progtxt></div></div>' +
            '<div class="voe-apply-foot">' +
                '<button class="voe-btn voe-btn--ghost" data-apply-remove title="Restore each poster from its backup">Remove overlays</button>' +
                '<button class="voe-btn voe-btn--ghost" data-apply-reset title="Re-pull the clean TMDB poster and push it (wipes Kometa overlays too)">Reset to originals</button>' +
                '<div class="voe-spacer"></div>' +
                '<button class="voe-btn" data-apply-cancel>Close</button>' +
                '<button class="voe-btn voe-btn--primary" data-apply-run>' + I.apply + ' Apply now</button>' +
            '</div></div>';
        document.body.appendChild(back);
        requestAnimationFrame(function () { back.classList.add('voe-confirm-back--on'); });
        function done() {
            if (applyPollTimer) { clearInterval(applyPollTimer); applyPollTimer = null; }
            back.classList.remove('voe-confirm-back--on'); setTimeout(function () { back.remove(); }, 180);
        }
        back.querySelectorAll('[data-apply-tpl]').forEach(function (sel) {
            sel.addEventListener('change', function () { saveAssign(back, sel.getAttribute('data-apply-tpl')); });
        });
        back.querySelectorAll('[data-apply-en]').forEach(function (btn) {
            btn.addEventListener('click', function () { btn.classList.toggle('voe-toggle--on'); saveAssign(back, btn.getAttribute('data-apply-en')); });
        });
        back.querySelector('[data-apply-cancel]').addEventListener('click', done);
        back.addEventListener('click', function (e) { if (e.target === back) done(); });
        back.querySelector('[data-apply-run]').addEventListener('click', function () { startApply(back, {}); });
        back.querySelector('[data-apply-remove]').addEventListener('click', function () {
            confirmDialog('Remove all overlays?', 'Every overlaid poster is restored from its backup on the server. Your templates are kept.', 'Remove', function () { startApply(back, { remove: true }); });
        });
        back.querySelector('[data-apply-reset]').addEventListener('click', function () {
            confirmDialog('Reset posters to originals?', 'Re-pulls each title\'s clean TMDB poster and pushes it to your server — this also wipes overlays burned in by other tools (e.g. Kometa). Your templates are kept.', 'Reset', function () { startApply(back, { reset: true }); });
        });
    }
    function saveAssign(back, scope) {
        var sel = back.querySelector('[data-apply-tpl="' + scope + '"]');
        var en = back.querySelector('[data-apply-en="' + scope + '"]');
        var tid = sel.value ? parseInt(sel.value, 10) : null;
        var enabled = en.classList.contains('voe-toggle--on') && !!tid;
        api('PUT', '/api/video/overlays/assignments', { scope: scope, template_id: tid, enabled: enabled })
            .catch(function () { toast('Could not save assignment', 'error'); });
    }
    function _applyBtns(back) { return back.querySelectorAll('[data-apply-run],[data-apply-remove],[data-apply-reset]'); }
    function startApply(back, opts) {
        opts = opts || {};
        _applyBtns(back).forEach(function (b) { b.disabled = true; });
        var prog = back.querySelector('[data-apply-prog]'); prog.hidden = false;
        setApplyProg(back, { phase: 'starting', done: 0, total: 0 });
        api('POST', '/api/video/overlays/apply', { scope: 'both', remove: !!opts.remove, reset: !!opts.reset })
            .then(function (r) {
                if (!r || !r.ok) { toast((r && r.error) || 'Could not start', 'error'); _applyBtns(back).forEach(function (b) { b.disabled = false; }); return; }
                applyPollTimer = setInterval(function () { pollApply(back); }, 700);
            })
            .catch(function () { toast('Could not start', 'error'); _applyBtns(back).forEach(function (b) { b.disabled = false; }); });
    }
    function _modeVerb(mode, ing) {
        if (mode === 'remove') return ing ? 'Removing' : 'Removed overlays';
        if (mode === 'reset') return ing ? 'Resetting' : 'Reset posters';
        return ing ? 'Applying' : 'Overlays applied';
    }
    function pollApply(back) {
        api('GET', '/api/video/overlays/apply/status').then(function (s) {
            if (!document.body.contains(back)) { if (applyPollTimer) { clearInterval(applyPollTimer); applyPollTimer = null; } return; }
            setApplyProg(back, s);
            if (!s.running && s.phase !== 'starting') {
                clearInterval(applyPollTimer); applyPollTimer = null;
                _applyBtns(back).forEach(function (b) { b.disabled = false; });
                if (s.phase === 'error') toast('Failed: ' + (s.error || ''), 'error');
                else toast(_modeVerb(s.mode, false) + ' · ' + (s.applied || 0) + ' done' +
                    (s.mode === 'apply' ? ', ' + (s.skipped || 0) + ' unchanged' : '') + (s.failed ? ', ' + s.failed + ' failed' : ''), 'success');
                document.dispatchEvent(new CustomEvent('soulsync:video-overlays-applied'));
            }
        }).catch(function () { /* keep polling */ });
    }
    function setApplyProg(back, s) {
        var fill = back.querySelector('[data-apply-fill]'), txt = back.querySelector('[data-apply-progtxt]');
        var pct = s.total ? Math.round((s.done / s.total) * 100) : (s.phase === 'done' ? 100 : 5);
        if (fill) fill.style.width = pct + '%';
        if (txt) {
            if (s.phase === 'running' || s.phase === 'starting') txt.textContent = _modeVerb(s.mode, true) + '… ' + (s.done || 0) + ' / ' + (s.total || '…') + (s.title ? ' · ' + s.title : '');
            else if (s.phase === 'done') txt.textContent = 'Done — ' + (s.applied || 0) + ' applied, ' + (s.skipped || 0) + ' unchanged' + (s.failed ? ', ' + s.failed + ' failed' : '');
            else if (s.phase === 'error') txt.textContent = 'Failed.';
        }
    }

    function loadGallery() {
        api('GET', '/api/video/overlays/templates').then(function (d) {
            renderGallery((d && d.templates) || []);
        }).catch(function () { renderGallery([]); });
    }

    function renderGallery(templates) {
        var grid = overlay && overlay.querySelector('[data-voe-grid]');
        if (!grid) return;
        var cards = ['<div class="voe-card voe-card--new" data-voe-new>' +
            '<div class="voe-card-canvas"><span class="voe-plus">+</span><span class="voe-new-label">New template</span></div></div>'];
        templates.forEach(function (t) {
            var when = t.updated_at ? String(t.updated_at).slice(0, 10) : '';
            var n = t.layer_count || 0;
            cards.push(
                '<div class="voe-card" data-voe-open="' + t.id + '">' +
                    '<div class="voe-card-canvas">' +
                        '<span class="voe-card-empty-ic">🎬</span>' +
                        '<img class="voe-card-thumb" src="/api/video/overlays/templates/' + t.id + '/thumb?v=' + encodeURIComponent(t.updated_at || '') + '"' +
                        ' alt="" loading="lazy" onload="this.classList.add(\'voe-card-thumb--on\')" onerror="this.remove()">' +
                        '<div class="voe-card-actions">' +
                            '<div class="voe-card-act" data-voe-dupe="' + t.id + '" title="Duplicate">' + I.copy + '</div>' +
                            '<div class="voe-card-act voe-card-act--danger" data-voe-del="' + t.id + '" data-voe-delname="' + esc(t.name) + '" title="Delete">' + I.trash + '</div>' +
                        '</div>' +
                    '</div>' +
                    '<div class="voe-card-meta"><div class="voe-card-name">' + esc(t.name) + '</div>' +
                        '<div class="voe-card-info">' + n + ' layer' + (n === 1 ? '' : 's') + (when ? ' · ' + when : '') + '</div></div>' +
                '</div>');
        });
        grid.innerHTML = cards.join('');
        grid.querySelector('[data-voe-new]').addEventListener('click', openStarterPicker);
        grid.querySelectorAll('[data-voe-open]').forEach(function (c) {
            c.addEventListener('click', function (e) {
                if (e.target.closest('[data-voe-dupe],[data-voe-del]')) return;
                loadTemplate(c.getAttribute('data-voe-open'));
            });
        });
        grid.querySelectorAll('[data-voe-dupe]').forEach(function (b) {
            b.addEventListener('click', function (e) {
                e.stopPropagation();
                api('POST', '/api/video/overlays/templates/' + b.getAttribute('data-voe-dupe') + '/duplicate')
                    .then(function () { loadGallery(); toast('Template duplicated', 'success'); })
                    .catch(function () { toast('Could not duplicate', 'error'); });
            });
        });
        grid.querySelectorAll('[data-voe-del]').forEach(function (b) {
            b.addEventListener('click', function (e) {
                e.stopPropagation();
                confirmDialog('Delete “' + b.getAttribute('data-voe-delname') + '”?',
                    'This template will be permanently removed. This cannot be undone.', 'Delete', function () {
                        api('DELETE', '/api/video/overlays/templates/' + b.getAttribute('data-voe-del'))
                            .then(function () { loadGallery(); }).catch(function () { toast('Could not delete', 'error'); });
                    });
            });
        });
    }

    function createTemplate(name, def) {
        api('POST', '/api/video/overlays/templates',
            { name: name || 'Untitled template', definition: def || { version: 1, canvas: { aspect: '2:3' }, layers: [] } })
            .then(function (d) { if (d && d.id) loadTemplate(d.id); })
            .catch(function () { toast('Could not create template', 'error'); });
    }

    // ── starter templates (skip the blank-canvas cold start) ────────────────────
    function _def(layers) { return { version: 1, canvas: { aspect: '2:3' }, layers: layers }; }
    function _badge(field, anchor, x, y, size) {
        var l = defaultLayer('badge', x, y, field);
        l.anchor = anchor; if (size) l.size = size; return l;
    }
    function STARTERS() {
        return [
            { name: 'Blank', desc: 'Start from an empty poster.', icon: '📄', build: function () { return _def([]); } },
            { name: 'Quality corner', desc: 'Resolution + audio badges, top-right.', icon: '🏷️',
                build: function () { return _def([_badge('resolution', 'top-right', 0.95, 0.05), _badge('audio_codec', 'top-right', 0.95, 0.14, 0.038)]); } },
            { name: 'Ratings bar', desc: 'IMDb + Rotten Tomatoes, bottom-left.', icon: '⭐',
                build: function () { return _def([_badge('imdb', 'bottom-left', 0.05, 0.95), _badge('rt', 'bottom-left', 0.30, 0.95)]); } },
            { name: 'The works', desc: 'Scrim, title logo, quality + rating.', icon: '✨',
                build: function () {
                    var scrim = defaultLayer('scrim');
                    var logo = defaultLayer('logo', 0.5, 0.82); logo.anchor = 'bottom-center'; logo.w = 0.6;
                    return _def([scrim, logo, _badge('resolution', 'top-right', 0.95, 0.05), _badge('imdb', 'bottom-left', 0.05, 0.7)]);
                } },
        ];
    }
    function openStarterPicker() {
        var back = document.createElement('div');
        back.className = 'voe-confirm-back';
        back.innerHTML = '<div class="voe-starter-modal"><div class="voe-starter-h">New overlay template</div>' +
            '<div class="voe-starter-sub">Start from a preset or a blank poster — you can change anything after.</div>' +
            '<div class="voe-starter-grid">' + STARTERS().map(function (s, i) {
                return '<button class="voe-starter-card" data-starter="' + i + '"><span class="voe-starter-ic">' + s.icon + '</span>' +
                    '<span class="voe-starter-name">' + esc(s.name) + '</span><span class="voe-starter-desc">' + esc(s.desc) + '</span></button>';
            }).join('') + '</div>' +
            '<div class="voe-confirm-row"><button class="voe-btn" data-starter-cancel>Cancel</button></div></div>';
        document.body.appendChild(back);
        requestAnimationFrame(function () { back.classList.add('voe-confirm-back--on'); });
        function done() { back.classList.remove('voe-confirm-back--on'); setTimeout(function () { back.remove(); }, 180); }
        var list = STARTERS();
        back.addEventListener('click', function (e) {
            if (e.target === back || e.target.closest('[data-starter-cancel]')) { done(); return; }
            var c = e.target.closest('[data-starter]');
            if (c) { var s = list[parseInt(c.getAttribute('data-starter'), 10)]; done(); createTemplate(s.name === 'Blank' ? 'Untitled template' : s.name, s.build()); }
        });
    }

    var _SHORTCUTS = [
        ['Drag on the poster', 'Move a layer'],
        ['Corner handle', 'Resize (scale)'],
        ['Top handle', 'Rotate — hold Shift to snap 15°'],
        ['Double-click text', 'Edit the text inline'],
        ['Arrow keys', 'Nudge 1px — Shift for 10px'],
        ['Delete / Backspace', 'Remove the selected layer'],
        ['Ctrl / ⌘ + D', 'Duplicate'],
        ['Ctrl / ⌘ + Z', 'Undo — add Shift to redo'],
        ['Ctrl / ⌘ + S', 'Save'],
    ];
    function openShortcuts() {
        var back = document.createElement('div');
        back.className = 'voe-confirm-back';
        back.innerHTML = '<div class="voe-shortcuts-modal"><div class="voe-starter-h">Keyboard &amp; canvas</div>' +
            '<div class="voe-shortcuts-list">' + _SHORTCUTS.map(function (s) {
                return '<div class="voe-shortcut-row"><span class="voe-shortcut-k">' + esc(s[0]) + '</span>' +
                    '<span class="voe-shortcut-d">' + esc(s[1]) + '</span></div>';
            }).join('') + '</div>' +
            '<div class="voe-confirm-row"><button class="voe-btn voe-btn--primary" data-sc-close>Got it</button></div></div>';
        document.body.appendChild(back);
        requestAnimationFrame(function () { back.classList.add('voe-confirm-back--on'); });
        function done() { back.classList.remove('voe-confirm-back--on'); setTimeout(function () { back.remove(); }, 180); }
        back.addEventListener('click', function (e) { if (e.target === back || e.target.closest('[data-sc-close]')) done(); });
    }

    function loadTemplate(id) {
        api('GET', '/api/video/overlays/templates/' + id).then(function (t) {
            var def = t.definition || {};
            ed = {
                id: t.id, name: t.name || 'Untitled template',
                layers: (def.layers || []).map(normalizeLayer),
                selected: null, dirty: false,
                stage: null, W: 0, H: 0,
                sample: defaultSample(), previewTitle: null, bg: null,
                history: [], histPos: -1,
            };
            renderEditor();
        }).catch(function () { toast('Could not open template', 'error'); showGallery(); });
    }

    // Fill in defaults for any layer loaded from storage (forward-compatible).
    function normalizeLayer(l) {
        l = l || {};
        l.id = l.id || uid();
        l.type = l.type || 'text';
        l.anchor = l.anchor || 'center';
        if (typeof l.x !== 'number') l.x = 0.5;
        if (typeof l.y !== 'number') l.y = 0.5;
        l.hidden = !!l.hidden;
        if (typeof l.opacity !== 'number') l.opacity = 1;
        if (typeof l.rotation !== 'number') l.rotation = 0;
        if (l.type === 'image') {
            if (typeof l.w !== 'number') l.w = 0.4;
            l.src = l.src || '';
        }
        if (l.type === 'shape') {
            if (typeof l.w !== 'number') l.w = 0.5;
            if (typeof l.h !== 'number') l.h = 0.12;
            if (typeof l.radius !== 'number') l.radius = 0.02;
            l.fill = l.fill || {};
            l.fill.grad = !!l.fill.grad;
            l.fill.c1 = l.fill.c1 || '#000000'; if (typeof l.fill.a1 !== 'number') l.fill.a1 = 0.72;
            l.fill.c2 = l.fill.c2 || '#000000'; if (typeof l.fill.a2 !== 'number') l.fill.a2 = 0;
            if (typeof l.fill.dir !== 'number') l.fill.dir = 180;
        }
        if (l.type === 'text') {
            if (l.text == null) l.text = 'Text';
            if (typeof l.size !== 'number') l.size = 0.06;
            l.color = l.color || '#ffffff';
            l.font = l.font || 'Inter';
            if (typeof l.weight !== 'number') l.weight = 800;
            l.align = l.align || 'center';
            if (typeof l.shadow !== 'boolean') l.shadow = true;
            l.bg = l.bg || {};
            l.bg.enabled = !!l.bg.enabled;
            l.bg.color = l.bg.color || '#000000';
            if (typeof l.bg.opacity !== 'number') l.bg.opacity = 0.6;
            if (typeof l.bg.radius !== 'number') l.bg.radius = 0.014;
            if (typeof l.bg.padX !== 'number') l.bg.padX = 0.022;
            if (typeof l.bg.padY !== 'number') l.bg.padY = 0.012;
        }
        return l;
    }

    // ── EDITOR shell ────────────────────────────────────────────────────────────
    function renderEditor() {
        overlay.innerHTML =
            '<div class="voe-topbar">' +
                '<button class="voe-btn voe-btn--ghost" data-voe-back>' + I.back + ' Studio</button>' +
                '<div class="voe-brand" style="margin-left:2px"><span class="voe-brand-mark">' + I.brand + '</span></div>' +
                '<input class="voe-name-input" data-voe-name value="' + esc(ed.name) + '" spellcheck="false">' +
                '<button class="voe-btn voe-btn--ghost voe-icon-btn" data-voe-undo title="Undo (Ctrl+Z)">' + I.undo + '</button>' +
                '<button class="voe-btn voe-btn--ghost voe-icon-btn" data-voe-redo title="Redo (Ctrl+Shift+Z)">' + I.redo + '</button>' +
                '<div class="voe-top-spacer"></div>' +
                '<button class="voe-btn voe-btn--ghost voe-icon-btn" data-voe-help title="Keyboard shortcuts">' + I.help + '</button>' +
                '<span class="voe-save-state" data-voe-savestate></span>' +
                '<button class="voe-btn voe-btn--primary" data-voe-save>' + I.save + ' Save</button>' +
                '<button class="voe-x" data-voe-close aria-label="Close">&times;</button>' +
            '</div>' +
            '<div class="voe-editor">' +
                '<div class="voe-palette">' + paletteHTML() + '</div>' +
                '<div class="voe-canvas-wrap" data-voe-canvaswrap>' +
                    '<div class="voe-canvas-bar">' +
                        '<button class="voe-btn" data-voe-preview>' + I.poster + ' <span data-voe-previewname>Sample poster</span> ' + I.chev + '</button>' +
                        '<button class="voe-btn voe-icon-btn" data-voe-random title="Surprise me — a random title">' + I.dice + '</button>' +
                        '<button class="voe-btn" data-voe-sampledata>' + I.info + ' Sample data ' + I.chev + '</button>' +
                    '</div>' +
                    '<div class="voe-stage" data-voe-stage>' +
                        '<div class="voe-stage-ph" data-voe-ph>Drag elements from the left onto the poster.<br>This background is just a preview — only the overlay is saved.</div>' +
                        '<div class="voe-guide voe-guide--v" data-voe-gv></div>' +
                        '<div class="voe-guide voe-guide--h" data-voe-gh></div>' +
                        '<div class="voe-drop-hint">Drop to add</div>' +
                    '</div>' +
                '</div>' +
                '<div class="voe-side">' +
                    '<div class="voe-side-layers">' +
                        '<div class="voe-side-h"><span>Layers</span><span class="voe-side-count" data-voe-count></span></div>' +
                        '<div class="voe-layers" data-voe-layers></div>' +
                    '</div>' +
                    '<div class="voe-inspector" data-voe-inspector></div>' +
                '</div>' +
            '</div>';

        overlay.querySelector('[data-voe-back]').addEventListener('click', function () { if (ed && ed.dirty) saveTemplate().finally(showGallery); else showGallery(); });
        overlay.querySelector('[data-voe-close]').addEventListener('click', close);
        overlay.querySelector('[data-voe-save]').addEventListener('click', function () { saveTemplate(); });
        var nameInput = overlay.querySelector('[data-voe-name]');
        nameInput.addEventListener('input', function () { ed.name = nameInput.value; markDirty(); });

        wirePalette();
        var stage = overlay.querySelector('[data-voe-stage]');
        stage.addEventListener('pointerdown', onStagePointerDown);
        overlay.querySelector('[data-voe-preview]').addEventListener('click', function (e) { openPreviewPop(e.currentTarget); });
        overlay.querySelector('[data-voe-random]').addEventListener('click', loadRandomPreview);
        overlay.querySelector('[data-voe-sampledata]').addEventListener('click', function (e) { openSamplePop(e.currentTarget); });
        overlay.querySelector('[data-voe-undo]').addEventListener('click', undo);
        overlay.querySelector('[data-voe-redo]').addEventListener('click', redo);
        overlay.querySelector('[data-voe-help]').addEventListener('click', openShortcuts);

        resizeBound = function () { measureStage(); relayoutAll(); };
        window.addEventListener('resize', resizeBound);

        measureStage();
        applyStageBg();
        renderStageLayers();
        renderLayersPanel();
        renderInspector();
        updateSaveState();
        updatePreviewName();
        seedHistory();
    }

    function applyStageBg() {
        if (!ed.stage) return;
        ed.stage.style.backgroundImage = ed.bg ? "url('" + ed.bg + "')" : '';
        var ph = ed.stage.querySelector('[data-voe-ph]');
        if (ph) ph.style.display = (ed.bg || ed.layers.length) ? 'none' : '';
    }
    function updatePreviewName() {
        var n = overlay && overlay.querySelector('[data-voe-previewname]');
        if (n) n.textContent = ed.previewTitle ? ed.previewTitle.title : 'Sample poster';
    }

    function measureStage() {
        var stage = overlay && overlay.querySelector('[data-voe-stage]');
        if (!stage) return;
        var r = stage.getBoundingClientRect();
        ed.stage = stage; ed.W = r.width; ed.H = r.height;
    }

    // ── palette (Text + dynamic data badges grouped by category) ────────────────
    function paletteHTML() {
        var html = '<div class="voe-pal-section"><div class="voe-pal-h">Basics</div><div class="voe-pal-grid">' +
            palItem('text', 'Text', I.text, '') + '</div></div>';
        FIELD_CATS.forEach(function (cat) {
            var items = FIELD_ORDER.filter(function (k) { return FIELDS[k].cat === cat; });
            html += '<div class="voe-pal-section"><div class="voe-pal-h">' + cat + '</div><div class="voe-pal-grid">' +
                items.map(function (k) { return palItem('badge', FIELDS[k].label, catIcon(cat), k); }).join('') + '</div></div>';
        });
        html += '<div class="voe-pal-section"><div class="voe-pal-h">Artwork</div><div class="voe-pal-grid">' +
            palItem('logo', 'Title Logo', I.logo, '') + palItem('image', 'Image', I.image, '') + '</div></div>';
        html += '<div class="voe-pal-section"><div class="voe-pal-h">Shapes</div><div class="voe-pal-grid">' +
            palItem('shape', 'Rectangle', I.shape, '') + palItem('scrim', 'Scrim', I.scrim, '') + '</div></div>';
        return html;
    }
    function palItem(kind, label, icon, field) {
        var head = '<span class="voe-pal-ic">' + icon + '</span>';
        // For a data badge, preview the actual value it produces (e.g. "4K", "IMDb 8.4")
        // instead of a generic icon — you see exactly what you'll drop in.
        if (kind === 'badge' && field && FIELDS[field]) {
            var v = FIELDS[field].fmt(((ed && ed.sample) ? ed.sample : defaultSample())[field]);
            if (v) head = '<span class="voe-pal-chip">' + esc(v) + '</span>';
        }
        return '<div class="voe-pal-item" data-voe-add="' + kind + '" data-field="' + esc(field || '') + '" title="' + esc(label) + '">' +
            head + '<span class="voe-pal-label">' + esc(label) + '</span></div>';
    }
    function wirePalette() {
        overlay.querySelectorAll('[data-voe-add]').forEach(function (it) {
            it.addEventListener('pointerdown', function (e) { startPaletteDrag(e, it); });
        });
    }

    // ── add / create layers ─────────────────────────────────────────────────────
    // All layers are text under the hood; a `binding` makes one a dynamic badge, so
    // every text/pill style control applies to badges too.
    function defaultLayer(kind, x, y, field) {
        var base = { id: uid(), type: 'text', anchor: 'center', x: x, y: y, hidden: false, opacity: 1,
            text: 'New Text', size: 0.06, color: '#ffffff', font: 'Inter', weight: 800, align: 'center', shadow: true,
            bg: { enabled: false, color: '#000000', opacity: 0.6, radius: 0.014, padX: 0.022, padY: 0.012 } };
        if (kind === 'badge' && field && FIELDS[field]) {
            base.binding = { field: field };
            base.name = FIELDS[field].label;
            base.size = 0.045; base.shadow = false;
            base.bg = { enabled: true, color: '#000000', opacity: 0.72, radius: 0.022, padX: 0.032, padY: 0.017 };
            return base;
        }
        if (kind === 'logo') {
            return { id: uid(), type: 'image', name: 'Title Logo', logo: true, src: '', anchor: 'center',
                x: x, y: y, w: 0.55, hidden: false, opacity: 1 };
        }
        if (kind === 'image') {
            return { id: uid(), type: 'image', name: 'Image', src: '', anchor: 'center',
                x: x, y: y, w: 0.4, hidden: false, opacity: 1 };
        }
        if (kind === 'scrim') {
            return { id: uid(), type: 'shape', name: 'Scrim', anchor: 'bottom-center', x: 0.5, y: 1,
                w: 1, h: 0.42, radius: 0, opacity: 1, hidden: false,
                fill: { grad: true, c1: '#000000', a1: 0, c2: '#000000', a2: 0.85, dir: 180 } };
        }
        if (kind === 'shape') {
            return { id: uid(), type: 'shape', name: 'Rectangle', anchor: 'center', x: x, y: y,
                w: 0.5, h: 0.12, radius: 0.02, opacity: 1, hidden: false,
                fill: { grad: false, c1: '#000000', a1: 0.72, c2: '#000000', a2: 0, dir: 180 } };
        }
        base.name = 'Text';
        return base;
    }
    function addLayer(kind, x, y, field) {
        var l = defaultLayer(kind, x, y, field);
        ed.layers.push(l);              // paint order: last = front
        ed.selected = l.id;
        markDirty();
        renderStageLayers();
        renderLayersPanel();
        renderInspector();
        var node = ed.stage && ed.stage.querySelector('.voe-layer[data-voe-layer="' + l.id + '"]');
        if (node) { node.classList.add('voe-layer--pop'); setTimeout(function () { node.classList.remove('voe-layer--pop'); }, 260); }
        return l;
    }

    // ── palette drag → drop onto the stage ──────────────────────────────────────
    function startPaletteDrag(e, item) {
        e.preventDefault();
        var kind = item.getAttribute('data-voe-add');
        var field = item.getAttribute('data-field') || '';
        var startX = e.clientX, startY = e.clientY, dragging = false, ghost = null;
        var stage = ed.stage;

        function move(ev) {
            if (!dragging && Math.abs(ev.clientX - startX) + Math.abs(ev.clientY - startY) > 5) {
                dragging = true; item.classList.add('voe-dragging');
                ghost = document.createElement('div');
                ghost.textContent = item.querySelector('.voe-pal-label') ? item.querySelector('.voe-pal-label').textContent : kind;
                ghost.style.cssText = 'position:fixed;z-index:9500;pointer-events:none;padding:6px 12px;border-radius:8px;' +
                    'background:rgba(var(--accent-rgb,88,101,242),.9);color:#fff;font-size:12px;font-weight:700;box-shadow:0 8px 20px rgba(0,0,0,.5);';
                document.body.appendChild(ghost);
            }
            if (!dragging) return;
            ghost.style.left = (ev.clientX + 12) + 'px';
            ghost.style.top = (ev.clientY + 12) + 'px';
            var over = overStage(ev);
            stage.classList.toggle('voe-stage--dropping', over);
        }
        function up(ev) {
            document.removeEventListener('pointermove', move);
            document.removeEventListener('pointerup', up);
            item.classList.remove('voe-dragging');
            stage.classList.remove('voe-stage--dropping');
            if (ghost) ghost.remove();
            if (!dragging) { addLayer(kind, 0.5, 0.5, field); return; }   // a click → add centered
            if (overStage(ev)) {
                var r = stage.getBoundingClientRect();
                addLayer(kind, clamp01((ev.clientX - r.left) / r.width), clamp01((ev.clientY - r.top) / r.height), field);
            }
        }
        document.addEventListener('pointermove', move);
        document.addEventListener('pointerup', up);
    }
    function overStage(ev) {
        var r = ed.stage.getBoundingClientRect();
        return ev.clientX >= r.left && ev.clientX <= r.right && ev.clientY >= r.top && ev.clientY <= r.bottom;
    }
    function clamp01(v) { return Math.max(0, Math.min(1, v)); }

    // ── render layers onto the stage ────────────────────────────────────────────
    function renderStageLayers() {
        var stage = ed.stage; if (!stage) return;
        // wipe existing layer nodes (keep placeholder + drop hint)
        stage.querySelectorAll('.voe-layer').forEach(function (n) { n.remove(); });
        var ph = stage.querySelector('[data-voe-ph]');
        if (ph) ph.style.display = (ed.bg || ed.layers.length) ? 'none' : '';
        ed.layers.forEach(function (l) {
            var el = document.createElement('div');
            el.className = 'voe-layer voe-layer--' + l.type + (l.id === ed.selected ? ' voe-layer--sel' : '') + (l.hidden ? ' voe-layer--hidden' : '');
            el.setAttribute('data-voe-layer', l.id);
            styleLayerEl(el, l);
            stage.appendChild(el);
            layoutLayer(el, l);
        });
        updateSelBox();
    }

    function hexToRgba(hex, a) {
        var h = String(hex || '#000000').replace('#', '');
        if (h.length === 3) h = h[0] + h[0] + h[1] + h[1] + h[2] + h[2];
        var n = parseInt(h, 16);
        if (isNaN(n)) return 'rgba(0,0,0,' + a + ')';
        return 'rgba(' + ((n >> 16) & 255) + ',' + ((n >> 8) & 255) + ',' + (n & 255) + ',' + a + ')';
    }

    function fillCss(f) {
        if (f.grad) return 'linear-gradient(' + f.dir + 'deg,' + hexToRgba(f.c1, f.a1) + ',' + hexToRgba(f.c2, f.a2) + ')';
        return hexToRgba(f.c1, f.a1);
    }
    // uploaded images are stored as asset:// refs; serve them same-origin for display
    function srcUrl(s) { return !s ? '' : (s.indexOf('asset://') === 0 ? '/api/video/overlays/asset/' + s.slice(8) : s); }
    function imgPlaceholder(l) {
        var h = l.w * ed.W * (l.logo ? 0.34 : 0.62);
        return '<div class="voe-img-ph" style="height:' + h + 'px">' + (l.logo ? 'LOGO' : 'IMAGE') + '</div>';
    }

    function styleLayerEl(el, l) {
        el.style.opacity = (l.opacity != null ? l.opacity : 1);
        el.style.transform = l.rotation ? 'rotate(' + l.rotation + 'deg)' : '';
        el.style.transformOrigin = 'center';
        if (l.type === 'image') {
            el.style.width = (l.w * ed.W) + 'px'; el.style.height = 'auto';
            var src = l.logo ? (ed.sample && ed.sample.logo_url) : l.src;
            if (src) {
                el.innerHTML = '<img src="' + esc(srcUrl(src)) + '" style="width:100%;display:block" draggable="false">';
                var img = el.querySelector('img');
                img.onload = function () { layoutLayer(el, l); };
                img.onerror = function () { el.innerHTML = imgPlaceholder(l); layoutLayer(el, l); };
            } else {
                el.innerHTML = imgPlaceholder(l);
            }
            return;
        }
        if (l.type === 'shape') {
            el.style.width = (l.w * ed.W) + 'px';
            el.style.height = (l.h * ed.H) + 'px';
            el.style.background = fillCss(l.fill);
            el.style.borderRadius = (l.radius * ed.H) + 'px';
            return;
        }
        if (l.type === 'text') {
            el.classList.add('voe-layer-text');
            el.textContent = l.binding ? resolveBinding(l.binding) : (l.text || '');
            el.style.color = l.color;
            el.style.fontFamily = fontStack(l.font);
            el.style.fontWeight = l.weight;
            el.style.fontSize = (l.size * ed.H) + 'px';
            el.style.textAlign = l.align || 'center';
            el.style.textShadow = l.shadow ? '0 0.12em 0.3em rgba(0,0,0,.55)' : 'none';
            var bg = l.bg || {};
            if (bg.enabled) {
                el.style.background = hexToRgba(bg.color, bg.opacity);
                el.style.padding = (bg.padY * ed.H) + 'px ' + (bg.padX * ed.H) + 'px';
                el.style.borderRadius = (bg.radius * ed.H) + 'px';
            } else {
                el.style.background = 'none'; el.style.padding = '0'; el.style.borderRadius = '3px';
            }
        }
    }

    // Position by the anchor model: (x,y) is the fraction of the stage where the
    // element's own anchor point sits.
    function layoutLayer(el, l) {
        var W = ed.W, H = ed.H;
        var ew = el.offsetWidth, eh = el.offsetHeight;
        var af = anchorFrac(l.anchor);
        el.style.left = (l.x * W - af[0] * ew) + 'px';
        el.style.top = (l.y * H - af[1] * eh) + 'px';
    }
    function relayoutAll() {
        if (!ed || !ed.stage) return;
        ed.stage.querySelectorAll('.voe-layer').forEach(function (el) {
            var l = layerById(el.getAttribute('data-voe-layer'));
            if (l) { styleLayerEl(el, l); layoutLayer(el, l); }
        });
    }
    function layerById(id) { for (var i = 0; i < ed.layers.length; i++) if (ed.layers[i].id === id) return ed.layers[i]; return null; }

    // ── select + drag a layer on the stage ──────────────────────────────────────
    function onStagePointerDown(e) {
        var node = e.target.closest('.voe-layer');
        if (!node) { select(null); return; }
        var l = layerById(node.getAttribute('data-voe-layer'));
        if (!l) return;
        select(l.id);
        if (node.getAttribute('contenteditable') === 'true') return;   // editing text, don't drag
        e.preventDefault();
        var r = ed.stage.getBoundingClientRect();
        var startX = l.x, startY = l.y, px = e.clientX, py = e.clientY, moved = false;

        function move(ev) {
            moved = true;
            var s = applySnap(clamp01(startX + (ev.clientX - px) / r.width),
                              clamp01(startY + (ev.clientY - py) / r.height));
            l.x = s.x; l.y = s.y;
            layoutLayer(node, l);
            showGuides(s.gx, s.gy);
            syncInspectorPos(l);
            updateSelBox();
        }
        function up() {
            document.removeEventListener('pointermove', move);
            document.removeEventListener('pointerup', up);
            hideGuides();
            if (moved) markDirty();
        }
        document.addEventListener('pointermove', move);
        document.addEventListener('pointerup', up);
    }

    // double-click a text layer to edit its text inline
    function enableInlineEdit(node, l) {
        node.setAttribute('contenteditable', 'true');
        node.style.cursor = 'text';
        node.focus();
        document.execCommand && document.getSelection && (function () {
            var range = document.createRange(); range.selectNodeContents(node);
            var sel = document.getSelection(); sel.removeAllRanges(); sel.addRange(range);
        })();
        function commit() {
            node.removeAttribute('contenteditable'); node.style.cursor = '';
            var txt = node.textContent.replace(/\s+$/, '');
            if (txt !== l.text) { l.text = txt || ' '; markDirty(); renderLayersPanel(); renderInspector(); }
            layoutLayer(node, l);
            node.removeEventListener('blur', commit);
            node.removeEventListener('keydown', key);
        }
        function key(ev) {
            if (ev.key === 'Enter' && !ev.shiftKey) { ev.preventDefault(); node.blur(); }
            if (ev.key === 'Escape') { node.textContent = l.text; node.blur(); }
        }
        node.addEventListener('blur', commit);
        node.addEventListener('keydown', key);
    }

    function select(id) {
        if (ed.selected === id) return;
        ed.selected = id;
        if (ed.stage) ed.stage.querySelectorAll('.voe-layer').forEach(function (el) {
            el.classList.toggle('voe-layer--sel', el.getAttribute('data-voe-layer') === id);
        });
        syncLayersPanelSelection();
        renderInspector();
        updateSelBox();
    }

    // update just one layer's node in place (no full re-render → keeps inspector focus)
    function refreshLayer(id) {
        if (!ed.stage) return;
        var node = ed.stage.querySelector('.voe-layer[data-voe-layer="' + id + '"]');
        var l = layerById(id);
        if (node && l) { styleLayerEl(node, l); layoutLayer(node, l); }
        if (id === ed.selected) updateSelBox();
    }
    function updateRowName(id) {
        var row = overlay && overlay.querySelector('[data-voe-row="' + id + '"] .voe-lr-name');
        var l = layerById(id);
        if (row && l) row.textContent = layerName(l);
    }

    // ── selection frame: corner-drag resize + a rotate handle ───────────────────
    function updateSelBox() {
        var stage = ed.stage; if (!stage) return;
        var old = stage.querySelector('.voe-selbox'); if (old) old.remove();
        var l = ed.selected ? layerById(ed.selected) : null;
        if (!l || l.hidden) return;
        var node = stage.querySelector('.voe-layer[data-voe-layer="' + l.id + '"]'); if (!node) return;
        var box = document.createElement('div');
        box.className = 'voe-selbox';
        box.style.left = node.offsetLeft + 'px';
        box.style.top = node.offsetTop + 'px';
        box.style.width = node.offsetWidth + 'px';
        box.style.height = node.offsetHeight + 'px';
        box.style.transform = l.rotation ? 'rotate(' + l.rotation + 'deg)' : '';
        box.innerHTML =
            '<span class="voe-sel-h voe-sel-h--tl" data-h="c"></span>' +
            '<span class="voe-sel-h voe-sel-h--tr" data-h="c"></span>' +
            '<span class="voe-sel-h voe-sel-h--br" data-h="c"></span>' +
            '<span class="voe-sel-h voe-sel-h--bl" data-h="c"></span>' +
            '<span class="voe-sel-rot-line"></span>' +
            '<span class="voe-sel-rot" data-h="rot" title="Rotate (Shift = snap 15°)"></span>';
        stage.appendChild(box);
        box.querySelectorAll('[data-h]').forEach(function (h) {
            h.addEventListener('pointerdown', function (e) {
                e.preventDefault(); e.stopPropagation();
                if (h.getAttribute('data-h') === 'rot') startHandleRotate(e, l, node);
                else startHandleResize(e, l, node);
            });
        });
    }
    function _layerCenter(node) { return { cx: node.offsetLeft + node.offsetWidth / 2, cy: node.offsetTop + node.offsetHeight / 2 }; }
    function _stagePt(ev) { var r = ed.stage.getBoundingClientRect(); return { x: ev.clientX - r.left, y: ev.clientY - r.top }; }

    // Corner handles do uniform scale-from-centre (rotation-agnostic): the metric
    // scales with the pointer's distance from the element's centre.
    function startHandleResize(e, l, node) {
        var c = _layerCenter(node), p0 = _stagePt(e);
        var d0 = Math.max(4, Math.hypot(p0.x - c.cx, p0.y - c.cy));
        var base = { size: l.size, w: l.w, h: l.h }, moved = false;
        function move(ev) {
            moved = true;
            var p = _stagePt(ev), scale = Math.max(0.05, Math.hypot(p.x - c.cx, p.y - c.cy) / d0);
            if (l.type === 'text') l.size = Math.max(0.008, base.size * scale);
            else if (l.type === 'image') l.w = clamp01(base.w * scale);
            else if (l.type === 'shape') { l.w = clamp01(base.w * scale); l.h = clamp01(base.h * scale); }
            refreshLayer(l.id); updateSelBox(); syncInspectorSize(l);
        }
        function up() { document.removeEventListener('pointermove', move); document.removeEventListener('pointerup', up); if (moved) markDirty(); }
        document.addEventListener('pointermove', move); document.addEventListener('pointerup', up);
    }
    function startHandleRotate(e, l, node) {
        var c = _layerCenter(node), p0 = _stagePt(e);
        var a0 = Math.atan2(p0.y - c.cy, p0.x - c.cx), baseRot = l.rotation || 0, moved = false;
        function move(ev) {
            moved = true;
            var p = _stagePt(ev), a = Math.atan2(p.y - c.cy, p.x - c.cx);
            var deg = baseRot + (a - a0) * 180 / Math.PI;
            if (ev.shiftKey) deg = Math.round(deg / 15) * 15;
            l.rotation = Math.round(deg) % 360;
            refreshLayer(l.id); updateSelBox();
            var ri = overlay && overlay.querySelector('[data-insp="rotation"]');
            if (ri && document.activeElement !== ri) ri.value = l.rotation;
        }
        function up() { document.removeEventListener('pointermove', move); document.removeEventListener('pointerup', up); if (moved) markDirty(); }
        document.addEventListener('pointermove', move); document.addEventListener('pointerup', up);
    }
    function syncInspectorSize(l) {
        var box = overlay && overlay.querySelector('[data-voe-inspector]'); if (!box) return;
        function set(key, val) { var i = box.querySelector('[data-insp="' + key + '"]'); if (i && document.activeElement !== i) i.value = pct(val); }
        if (l.type === 'text') set('size', l.size);
        else if (l.type === 'image') set('w', l.w);
        else if (l.type === 'shape') { set('w', l.w); set('h', l.h); }
    }

    // Snap the selected layer's box to a stage edge/centre (uses the measured box).
    function alignSelected(dir) {
        var l = ed.selected ? layerById(ed.selected) : null; if (!l) return;
        var node = ed.stage.querySelector('.voe-layer[data-voe-layer="' + l.id + '"]'); if (!node) return;
        var ew = node.offsetWidth, eh = node.offsetHeight, W = ed.W, H = ed.H;
        var af = anchorFrac(l.anchor), ax = af[0], ay = af[1];
        if (dir === 'left') l.x = ax * ew / W;
        else if (dir === 'hcenter') l.x = 0.5 - ew / (2 * W) + ax * ew / W;
        else if (dir === 'right') l.x = 1 - ew / W + ax * ew / W;
        else if (dir === 'top') l.y = ay * eh / H;
        else if (dir === 'vmiddle') l.y = 0.5 - eh / (2 * H) + ay * eh / H;
        else if (dir === 'bottom') l.y = 1 - eh / H + ay * eh / H;
        refreshLayer(l.id); syncInspectorPos(l); updateSelBox(); markDirty();
    }

    // Change a layer's anchor WITHOUT moving it on screen: recompute x,y so the new
    // anchor point maps to the element's current pixel box.
    function changeAnchor(l, na) {
        var node = ed.stage.querySelector('.voe-layer[data-voe-layer="' + l.id + '"]');
        if (node) {
            var ew = node.offsetWidth, eh = node.offsetHeight;
            var o = anchorFrac(l.anchor), n = anchorFrac(na);
            var tlx = l.x * ed.W - o[0] * ew, tly = l.y * ed.H - o[1] * eh;
            l.x = clamp01((tlx + n[0] * ew) / ed.W);
            l.y = clamp01((tly + n[1] * eh) / ed.H);
        }
        l.anchor = na;
        refreshLayer(l.id); markDirty();
    }

    // ── layers panel (scene list) ───────────────────────────────────────────────
    function layerIcon(l) {
        if (l.binding) return catIcon((FIELDS[l.binding.field] || {}).cat);
        if (l.type === 'image') return l.logo ? I.logo : I.image;
        if (l.type === 'shape') return I.shape;
        return I.text;
    }
    function layerName(l) {
        if (l.name) return l.name;
        if (l.binding) return (FIELDS[l.binding.field] || {}).label;
        if (l.type === 'image') return l.logo ? 'Logo' : 'Image';
        if (l.type === 'shape') return 'Shape';
        return l.text || 'Text';
    }

    function renderLayersPanel() {
        var box = overlay && overlay.querySelector('[data-voe-layers]');
        var count = overlay && overlay.querySelector('[data-voe-count]');
        if (!box) return;
        if (count) count.textContent = ed.layers.length ? ed.layers.length : '';
        if (!ed.layers.length) {
            box.innerHTML = '<div class="voe-layers-empty">No layers yet.<br>Drag an element from the left to begin.</div>';
            return;
        }
        // front (last painted) at the top of the list
        var rows = [];
        for (var i = ed.layers.length - 1; i >= 0; i--) {
            var l = ed.layers[i];
            rows.push(
                '<div class="voe-layer-row' + (l.id === ed.selected ? ' voe-layer-row--sel' : '') + '" data-voe-row="' + l.id + '">' +
                    '<span class="voe-lr-grip" data-voe-grip title="Drag to reorder">' + I.grip + '</span>' +
                    '<span class="voe-lr-ic">' + layerIcon(l) + '</span>' +
                    '<span class="voe-lr-name">' + esc(layerName(l)) + '</span>' +
                    '<button class="voe-lr-btn' + (l.hidden ? ' voe-lr-btn--off' : '') + '" data-voe-vis title="Show/Hide">' + (l.hidden ? I.eyeOff : I.eye) + '</button>' +
                    '<button class="voe-lr-btn" data-voe-dupelayer title="Duplicate (Ctrl+D)">' + I.dupe + '</button>' +
                    '<button class="voe-lr-btn" data-voe-rmlayer title="Delete layer">' + I.trash + '</button>' +
                '</div>');
        }
        box.innerHTML = rows.join('');
        box.querySelectorAll('[data-voe-row]').forEach(function (row) {
            var id = row.getAttribute('data-voe-row');
            row.addEventListener('click', function (e) {
                if (e.target.closest('[data-voe-vis],[data-voe-rmlayer],[data-voe-dupelayer],[data-voe-grip]')) return;
                select(id);
            });
            row.querySelector('[data-voe-vis]').addEventListener('click', function (e) { e.stopPropagation(); toggleHidden(id); });
            row.querySelector('[data-voe-dupelayer]').addEventListener('click', function (e) { e.stopPropagation(); duplicateLayer(id); });
            row.querySelector('[data-voe-rmlayer]').addEventListener('click', function (e) { e.stopPropagation(); removeLayer(id); });
            row.querySelector('[data-voe-grip]').addEventListener('pointerdown', function (e) { startRowReorder(e, id); });
        });
    }
    function syncLayersPanelSelection() {
        if (!overlay) return;
        overlay.querySelectorAll('[data-voe-row]').forEach(function (r) {
            r.classList.toggle('voe-layer-row--sel', r.getAttribute('data-voe-row') === ed.selected);
        });
    }

    function toggleHidden(id) {
        var l = layerById(id); if (!l) return;
        l.hidden = !l.hidden; markDirty();
        renderStageLayers(); renderLayersPanel();
    }
    function removeLayer(id) {
        ed.layers = ed.layers.filter(function (l) { return l.id !== id; });
        if (ed.selected === id) ed.selected = null;
        markDirty(); renderStageLayers(); renderLayersPanel(); renderInspector();
    }

    // drag-to-reorder rows → changes z-order (paint order)
    function startRowReorder(e, id) {
        e.preventDefault(); e.stopPropagation();
        var box = overlay.querySelector('[data-voe-layers]');
        var srcRow = box.querySelector('[data-voe-row="' + id + '"]');
        srcRow.classList.add('voe-layer-row--dragging');
        var targetId = null, before = false;

        function move(ev) {
            var rows = box.querySelectorAll('[data-voe-row]');
            box.querySelectorAll('.voe-layer-row--dropbefore,.voe-layer-row--dropafter')
                .forEach(function (r) { r.classList.remove('voe-layer-row--dropbefore', 'voe-layer-row--dropafter'); });
            targetId = null;
            for (var i = 0; i < rows.length; i++) {
                var rr = rows[i].getBoundingClientRect();
                if (ev.clientY < rr.top || ev.clientY > rr.bottom) continue;
                var rid = rows[i].getAttribute('data-voe-row');
                if (rid === id) return;
                before = ev.clientY < rr.top + rr.height / 2;
                targetId = rid;
                rows[i].classList.add(before ? 'voe-layer-row--dropbefore' : 'voe-layer-row--dropafter');
                return;
            }
        }
        function up() {
            document.removeEventListener('pointermove', move);
            document.removeEventListener('pointerup', up);
            box.querySelectorAll('.voe-layer-row--dropbefore,.voe-layer-row--dropafter,.voe-layer-row--dragging')
                .forEach(function (r) { r.classList.remove('voe-layer-row--dropbefore', 'voe-layer-row--dropafter', 'voe-layer-row--dragging'); });
            if (targetId) reorder(id, targetId, before);
        }
        document.addEventListener('pointermove', move);
        document.addEventListener('pointerup', up);
    }
    // Panel is front-first (reversed). Moving row `id` to visually before/after
    // `targetId` maps to an array splice in paint order.
    function reorder(id, targetId, before) {
        var arr = ed.layers;
        var from = arr.findIndex(function (l) { return l.id === id; });
        if (from < 0) return;
        var moved = arr.splice(from, 1)[0];
        var to = arr.findIndex(function (l) { return l.id === targetId; });
        if (to < 0) { arr.push(moved); }
        else {
            // visual "before" = higher z = later in array (front). Insert after target
            // for "before", before target for "after".
            var insert = before ? to + 1 : to;
            arr.splice(insert, 0, moved);
        }
        markDirty(); renderStageLayers(); renderLayersPanel();
    }

    // ── inspector (selected-layer properties) ───────────────────────────────────
    var ANCHOR_ORDER = ['top-left', 'top-center', 'top-right', 'mid-left', 'center', 'mid-right',
        'bottom-left', 'bottom-center', 'bottom-right'];
    var WEIGHTS = [[400, 'Regular'], [600, 'Semibold'], [700, 'Bold'], [800, 'Extrabold'], [900, 'Black']];

    function pct(frac) { return Math.round(frac * 1000) / 10; }
    function field(label, control) {
        return '<div class="voe-field"><div class="voe-field-l">' + label + '</div><div class="voe-field-c">' + control + '</div></div>';
    }
    function row2(a, b) { return '<div class="voe-row2">' + a + b + '</div>'; }
    function inspSection(title, body) {
        return '<div class="voe-insp-sec"><button class="voe-insp-sec-h" data-voe-sectoggle type="button">' +
            title + '<span class="voe-insp-chev">' + I.chev + '</span></button><div class="voe-insp-body">' + body + '</div></div>';
    }
    function numInput(key, val, unit) {
        return '<input class="voe-input voe-input--num" type="number" step="0.1" data-insp="' + key + '" value="' + val + '">' +
            (unit ? '<span class="voe-unit">' + unit + '</span>' : '');
    }
    function sliderInput(key, val) {
        return '<input class="voe-slider" type="range" min="0" max="100" data-insp="' + key + '" value="' + val + '">' +
            '<span class="voe-unit" data-insp-val="' + key + '">' + val + '%</span>';
    }
    function fontSelect(cur) {
        return '<select class="voe-input" data-inspsel="font">' + FONTS.map(function (f) {
            return '<option value="' + f.id + '"' + (f.id === cur ? ' selected' : '') + '>' + esc(f.label) + '</option>';
        }).join('') + '</select>';
    }
    function weightSelect(cur) {
        return '<select class="voe-input" data-inspsel="weight">' + WEIGHTS.map(function (w) {
            return '<option value="' + w[0] + '"' + (w[0] === cur ? ' selected' : '') + '>' + w[1] + '</option>';
        }).join('') + '</select>';
    }
    function alignSeg(cur) {
        return '<div class="voe-seg" data-inspseg="align">' + ['left', 'center', 'right'].map(function (a) {
            return '<button class="voe-seg-btn' + (a === cur ? ' voe-seg-btn--on' : '') + '" data-val="' + a + '" title="' + a + '">' +
                a.charAt(0).toUpperCase() + '</button>';
        }).join('') + '</div>';
    }
    function colorField(key, val) {
        return '<div class="voe-color"><input type="color" class="voe-color-sw" data-inspcolor="' + key + '" value="' + esc(val) + '">' +
            '<input class="voe-input" data-insphex="' + key + '" value="' + esc(val) + '" spellcheck="false"></div>';
    }
    function toggle(key, on) { return '<button class="voe-toggle' + (on ? ' voe-toggle--on' : '') + '" data-insptoggle="' + key + '"></button>'; }
    function dataFieldSelect(cur) {
        return '<select class="voe-input" data-inspbind>' + FIELD_CATS.map(function (cat) {
            return '<optgroup label="' + cat + '">' + FIELD_ORDER.filter(function (k) { return FIELDS[k].cat === cat; })
                .map(function (k) { return '<option value="' + k + '"' + (k === cur ? ' selected' : '') + '>' + esc(FIELDS[k].label) + '</option>'; }).join('') + '</optgroup>';
        }).join('') + '</select>';
    }
    function anchorGrid(l) {
        return field('Anchor', '<div class="voe-anchor-grid">' + ANCHOR_ORDER.map(function (a) {
            return '<div class="voe-anchor-cell' + (a === l.anchor ? ' voe-anchor-cell--on' : '') + '" data-anchor="' + a + '" title="' + a + '"></div>';
        }).join('') + '</div>');
    }
    var ALIGN_ICONS = {
        left: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M4 4v16"/><path d="M4 9h11M4 15h7"/></svg>',
        hcenter: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M12 4v16"/><path d="M6 9h12M8 15h8"/></svg>',
        right: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M20 4v16"/><path d="M9 9h11M13 15h7"/></svg>',
        top: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M4 4h16"/><path d="M9 4v11M15 4v7"/></svg>',
        vmiddle: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M4 12h16"/><path d="M9 6v12M15 8v8"/></svg>',
        bottom: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M4 20h16"/><path d="M9 9h0M9 9v11M15 13v7"/></svg>',
    };
    function _alignSeg(dirs) {
        return '<div class="voe-seg" data-voe-alignbar>' + dirs.map(function (d) {
            return '<button class="voe-seg-btn" data-voe-align="' + d + '" title="Align ' + d + '">' + ALIGN_ICONS[d] + '</button>';
        }).join('') + '</div>';
    }
    function alignBar() {
        return '<div style="display:flex;gap:6px">' + _alignSeg(['left', 'hcenter', 'right']) + _alignSeg(['top', 'vmiddle', 'bottom']) + '</div>';
    }

    function renderInspector() {
        var box = overlay && overlay.querySelector('[data-voe-inspector]');
        if (!box) return;
        var l = ed.selected ? layerById(ed.selected) : null;
        if (!l) {
            box.innerHTML = '<div class="voe-insp-empty">Select a layer to edit its position, size &amp; style.</div>';
            return;
        }
        var sizeCtrl = '';
        if (l.type === 'text') sizeCtrl = field('Size', numInput('size', pct(l.size), '%'));
        else if (l.type === 'image') sizeCtrl = field('Width', numInput('w', pct(l.w), '%'));
        else if (l.type === 'shape') sizeCtrl = row2(field('Width', numInput('w', pct(l.w), '%')), field('Height', numInput('h', pct(l.h), '%')));
        var html = inspSection('Transform',
            anchorGrid(l) +
            field('Align', alignBar()) +
            row2(field('X', numInput('x', pct(l.x), '%')), field('Y', numInput('y', pct(l.y), '%'))) +
            sizeCtrl +
            row2(field('Rotate', numInput('rotation', l.rotation || 0, '°')),
                 field('Opacity', sliderInput('opacity', Math.round(l.opacity * 100)))));
        if (l.type === 'text') {
            if (l.binding) {
                html += inspSection('Data',
                    field('Field', dataFieldSelect(l.binding.field)) +
                    field('Shows', '<span class="voe-data-preview">' + esc(resolveBinding(l.binding)) + '</span>'));
            }
            html += inspSection(l.binding ? 'Style' : 'Text',
                (l.binding ? '' : field('Text', '<textarea class="voe-input voe-textarea" data-insptext>' + esc(l.text) + '</textarea>')) +
                field('Font', fontSelect(l.font)) +
                field('Weight', weightSelect(l.weight)) +
                field('Align', alignSeg(l.align)) +
                field('Color', colorField('color', l.color)) +
                field('Shadow', toggle('shadow', l.shadow)));
            html += inspSection('Background',
                field('Pill', toggle('bgEnabled', l.bg.enabled)) +
                (l.bg.enabled
                    ? field('Color', colorField('bgColor', l.bg.color)) +
                      field('Fill', sliderInput('bgOpacity', Math.round(l.bg.opacity * 100))) +
                      field('Radius', numInput('bgRadius', pct(l.bg.radius), '%')) +
                      row2(field('Pad X', numInput('bgPadX', pct(l.bg.padX), '%')), field('Pad Y', numInput('bgPadY', pct(l.bg.padY), '%')))
                    : ''));
        }
        if (l.type === 'image') {
            html += inspSection('Source', l.logo
                ? '<div class="voe-insp-hint">Uses the previewed title’s logo. Pick a title under “Preview poster” to see it.</div>'
                : field('URL', '<input class="voe-input" data-inspsrc placeholder="https://…" value="' + esc(l.src || '') + '">') +
                  field('Upload', '<button class="voe-btn" data-inspupload style="width:100%;justify-content:center">Choose image…</button>'));
        }
        if (l.type === 'shape') {
            html += inspSection('Fill',
                field('Gradient', toggle('fillGrad', l.fill.grad)) +
                field('Color', colorField('fillC1', l.fill.c1)) +
                field('Opacity', sliderInput('fillA1', Math.round(l.fill.a1 * 100))) +
                (l.fill.grad
                    ? field('Color 2', colorField('fillC2', l.fill.c2)) +
                      field('Opacity 2', sliderInput('fillA2', Math.round(l.fill.a2 * 100))) +
                      field('Angle', numInput('fillDir', l.fill.dir, '°'))
                    : '') +
                field('Radius', numInput('radius', pct(l.radius), '%')));
        }
        box.innerHTML = html;
        wireInspector(l);
    }

    function setNum(l, key, num) {
        if (isNaN(num)) return;
        if (key === 'x') l.x = clamp01(num / 100);
        else if (key === 'y') l.y = clamp01(num / 100);
        else if (key === 'size') l.size = Math.max(0.005, num / 100);
        else if (key === 'rotation') l.rotation = ((Math.round(num) % 360) + 360) % 360;
        else if (key === 'opacity') l.opacity = clamp01(num / 100);
        else if (key === 'bgOpacity') l.bg.opacity = clamp01(num / 100);
        else if (key === 'bgRadius') l.bg.radius = Math.max(0, num / 100);
        else if (key === 'bgPadX') l.bg.padX = Math.max(0, num / 100);
        else if (key === 'bgPadY') l.bg.padY = Math.max(0, num / 100);
        else if (key === 'w') l.w = Math.max(0.02, num / 100);
        else if (key === 'h') l.h = Math.max(0.02, num / 100);
        else if (key === 'radius') l.radius = Math.max(0, num / 100);
        else if (key === 'fillA1') l.fill.a1 = clamp01(num / 100);
        else if (key === 'fillA2') l.fill.a2 = clamp01(num / 100);
        else if (key === 'fillDir') l.fill.dir = num;
    }
    function setColor(l, key, val) {
        if (key === 'color') l.color = val;
        else if (key === 'bgColor') l.bg.color = val;
        else if (key === 'fillC1') l.fill.c1 = val;
        else if (key === 'fillC2') l.fill.c2 = val;
    }

    function wireInspector(l) {
        var _ib = overlay.querySelector('[data-voe-inspector]');
        if (_ib) _ib.querySelectorAll('[data-voe-sectoggle]').forEach(function (h) {
            h.addEventListener('click', function () { h.parentNode.classList.toggle('voe-sec-collapsed'); });
        });
        var box = overlay.querySelector('[data-voe-inspector]');
        box.querySelectorAll('[data-insp]').forEach(function (inp) {
            var key = inp.getAttribute('data-insp');
            inp.addEventListener('input', function () {
                setNum(l, key, parseFloat(inp.value));
                var out = box.querySelector('[data-insp-val="' + key + '"]');
                if (out) out.textContent = Math.round(parseFloat(inp.value)) + '%';
                refreshLayer(l.id); markDirty();
            });
        });
        var ta = box.querySelector('[data-insptext]');
        if (ta) ta.addEventListener('input', function () {
            l.text = ta.value; refreshLayer(l.id); updateRowName(l.id); markDirty();
        });
        var bind = box.querySelector('[data-inspbind]');
        if (bind) bind.addEventListener('change', function () {
            l.binding.field = bind.value; l.name = FIELDS[bind.value].label;
            refreshLayer(l.id); updateRowName(l.id); markDirty(); renderInspector();
        });
        var srcInp = box.querySelector('[data-inspsrc]');
        if (srcInp) srcInp.addEventListener('input', function () { l.src = srcInp.value.trim(); refreshLayer(l.id); markDirty(); });
        var upBtn = box.querySelector('[data-inspupload]');
        if (upBtn) upBtn.addEventListener('click', function () {
            var fi = document.createElement('input'); fi.type = 'file'; fi.accept = 'image/*';
            fi.addEventListener('change', function () {
                var file = fi.files && fi.files[0]; if (!file) return;
                upBtn.disabled = true; upBtn.textContent = 'Uploading…';
                var fd = new FormData(); fd.append('file', file);
                fetch('/api/video/overlays/upload', { method: 'POST', body: fd })
                    .then(function (r) { return r.json(); })
                    .then(function (d) {
                        upBtn.disabled = false; upBtn.textContent = 'Choose image…';
                        if (!d || !d.ok) { toast((d && d.error) || 'Upload failed', 'error'); return; }
                        l.src = d.src; if (srcInp) srcInp.value = d.src; refreshLayer(l.id); markDirty();
                    })
                    .catch(function () { upBtn.disabled = false; upBtn.textContent = 'Choose image…'; toast('Upload failed', 'error'); });
            });
            fi.click();
        });
        box.querySelectorAll('[data-inspsel]').forEach(function (sel) {
            var key = sel.getAttribute('data-inspsel');
            sel.addEventListener('change', function () {
                if (key === 'weight') l.weight = parseInt(sel.value, 10); else l[key] = sel.value;
                refreshLayer(l.id); markDirty();
            });
        });
        box.querySelectorAll('[data-voe-alignbar]').forEach(function (bar) {
            bar.addEventListener('click', function (e) {
                var b = e.target.closest('[data-voe-align]'); if (b) alignSelected(b.getAttribute('data-voe-align'));
            });
        });
        var seg = box.querySelector('[data-inspseg="align"]');
        if (seg) seg.addEventListener('click', function (e) {
            var b = e.target.closest('[data-val]'); if (!b) return;
            l.align = b.getAttribute('data-val');
            seg.querySelectorAll('.voe-seg-btn').forEach(function (x) { x.classList.toggle('voe-seg-btn--on', x === b); });
            refreshLayer(l.id); markDirty();
        });
        box.querySelectorAll('[data-inspcolor]').forEach(function (c) {
            var key = c.getAttribute('data-inspcolor');
            c.addEventListener('input', function () {
                setColor(l, key, c.value);
                var hex = box.querySelector('[data-insphex="' + key + '"]'); if (hex) hex.value = c.value;
                refreshLayer(l.id); markDirty();
            });
        });
        box.querySelectorAll('[data-insphex]').forEach(function (h) {
            var key = h.getAttribute('data-insphex');
            h.addEventListener('change', function () {
                var v = h.value.trim(); if (!/^#?[0-9a-fA-F]{3}([0-9a-fA-F]{3})?$/.test(v)) return;
                if (v[0] !== '#') v = '#' + v;
                setColor(l, key, v);
                var sw = box.querySelector('[data-inspcolor="' + key + '"]'); if (sw) sw.value = v;
                refreshLayer(l.id); markDirty();
            });
        });
        box.querySelectorAll('[data-insptoggle]').forEach(function (t) {
            var key = t.getAttribute('data-insptoggle');
            t.addEventListener('click', function () {
                if (key === 'shadow') l.shadow = !l.shadow;
                else if (key === 'bgEnabled') l.bg.enabled = !l.bg.enabled;
                else if (key === 'fillGrad') l.fill.grad = !l.fill.grad;
                t.classList.toggle('voe-toggle--on');
                refreshLayer(l.id); markDirty();
                if (key === 'bgEnabled' || key === 'fillGrad') renderInspector();   // reveal/hide sub-fields
            });
        });
        box.querySelectorAll('[data-anchor]').forEach(function (cell) {
            cell.addEventListener('click', function () {
                changeAnchor(l, cell.getAttribute('data-anchor'));
                renderInspector();   // anchor change moves x,y → refresh fields + active cell
            });
        });
    }

    // snap the dragged anchor point to the stage's edges/centre (0, .5, 1) and
    // return which axes snapped so we can flash guide lines.
    function applySnap(nx, ny) {
        var TH = 6, gx = null, gy = null;
        [0, 0.5, 1].forEach(function (t) {
            if (Math.abs(nx * ed.W - t * ed.W) < TH) { nx = t; gx = t; }
            if (Math.abs(ny * ed.H - t * ed.H) < TH) { ny = t; gy = t; }
        });
        return { x: nx, y: ny, gx: gx, gy: gy };
    }
    function showGuides(gx, gy) {
        var gv = ed.stage.querySelector('[data-voe-gv]'), gh = ed.stage.querySelector('[data-voe-gh]');
        if (gv) { if (gx == null) gv.style.display = 'none'; else { gv.style.display = 'block'; gv.style.left = (gx * ed.W) + 'px'; } }
        if (gh) { if (gy == null) gh.style.display = 'none'; else { gh.style.display = 'block'; gh.style.top = (gy * ed.H) + 'px'; } }
    }
    function hideGuides() { showGuides(null, null); }

    // keep the inspector's X/Y fields live while dragging on the stage
    function syncInspectorPos(l) {
        var box = overlay && overlay.querySelector('[data-voe-inspector]');
        if (!box) return;
        var xi = box.querySelector('[data-insp="x"]'), yi = box.querySelector('[data-insp="y"]');
        if (xi && document.activeElement !== xi) xi.value = pct(l.x);
        if (yi && document.activeElement !== yi) yi.value = pct(l.y);
    }

    // ── history (undo / redo) ────────────────────────────────────────────────────
    // Snapshots of the layer list. Records are debounced so a burst of edits (a
    // drag, a slider sweep) collapses into one undo step.
    var histTimer = null;
    function cloneLayers() { return JSON.parse(JSON.stringify(ed.layers)); }
    function seedHistory() { ed.history = [cloneLayers()]; ed.histPos = 0; updateUndoRedo(); }
    function recordHistory() {
        if (!ed) return;
        if (histTimer) { clearTimeout(histTimer); histTimer = null; }
        ed.history = ed.history.slice(0, ed.histPos + 1);
        ed.history.push(cloneLayers());
        if (ed.history.length > 60) ed.history.shift();
        ed.histPos = ed.history.length - 1;
        updateUndoRedo();
    }
    function scheduleRecord() { if (histTimer) clearTimeout(histTimer); histTimer = setTimeout(recordHistory, 350); }
    function flushHistory() { if (histTimer) { clearTimeout(histTimer); histTimer = null; recordHistory(); } }
    function restoreHistory() {
        ed.layers = JSON.parse(JSON.stringify(ed.history[ed.histPos]));
        if (ed.selected && !layerById(ed.selected)) ed.selected = null;
        ed.dirty = true; updateSaveState();
        renderStageLayers(); renderLayersPanel(); renderInspector(); updateUndoRedo();
    }
    function undo() { flushHistory(); if (ed && ed.histPos > 0) { ed.histPos--; restoreHistory(); } }
    function redo() { if (ed && ed.histPos < ed.history.length - 1) { ed.histPos++; restoreHistory(); } }
    function updateUndoRedo() {
        var u = overlay && overlay.querySelector('[data-voe-undo]'), r = overlay && overlay.querySelector('[data-voe-redo]');
        if (u) u.disabled = !(ed && ed.histPos > 0);
        if (r) r.disabled = !(ed && ed.history && ed.histPos < ed.history.length - 1);
    }

    function duplicateLayer(id) {
        var l = layerById(id); if (!l) return;
        var copy = JSON.parse(JSON.stringify(l));
        copy.id = uid();
        copy.x = clamp01(copy.x + 0.03); copy.y = clamp01(copy.y + 0.03);
        var idx = ed.layers.indexOf(l);
        ed.layers.splice(idx + 1, 0, copy);
        ed.selected = copy.id;
        markDirty(); renderStageLayers(); renderLayersPanel(); renderInspector();
    }

    // ── dirty + save ────────────────────────────────────────────────────────────
    function markDirty() { if (ed) { ed.dirty = true; updateSaveState(); scheduleRecord(); } }
    function updateSaveState() {
        var s = overlay && overlay.querySelector('[data-voe-savestate]');
        var btn = overlay && overlay.querySelector('[data-voe-save]');
        if (s) { s.textContent = ed.dirty ? 'Unsaved changes' : 'All changes saved'; s.classList.toggle('voe-save-state--dirty', ed.dirty); }
        if (btn) btn.disabled = !ed.dirty;
    }
    function definition() {
        return { version: 1, canvas: { aspect: '2:3' }, layers: ed.layers };
    }
    function saveTemplate() {
        if (!ed) return Promise.resolve();
        return api('PUT', '/api/video/overlays/templates/' + ed.id, { name: ed.name, definition: definition() })
            .then(function () { ed.dirty = false; updateSaveState(); })
            .catch(function () { toast('Could not save template', 'error'); });
    }

    // ── preview poster + sample data (dynamic-badge preview) ────────────────────
    function refreshBoundLayers() {
        ed.layers.forEach(function (l) { if (l.binding || (l.type === 'image' && l.logo)) refreshLayer(l.id); });
        if (ed.selected) { var s = layerById(ed.selected); if (s && s.binding) renderInspector(); }
    }

    var openPop = null;
    function closePop() { if (openPop) { openPop.close(); openPop = null; } }
    function popover(anchor, html) {
        closePop();
        var el = document.createElement('div');
        el.className = 'voe-pop';
        el.innerHTML = html;
        document.body.appendChild(el);
        var r = anchor.getBoundingClientRect();
        el.style.left = Math.max(12, Math.min(r.left, window.innerWidth - el.offsetWidth - 12)) + 'px';
        el.style.top = (r.bottom + 8) + 'px';
        requestAnimationFrame(function () { el.classList.add('voe-pop--on'); });
        function outside(e) { if (!el.contains(e.target) && !anchor.contains(e.target)) closePop(); }
        setTimeout(function () { document.addEventListener('pointerdown', outside); }, 0);
        openPop = { el: el, close: function () {
            document.removeEventListener('pointerdown', outside);
            el.classList.remove('voe-pop--on'); setTimeout(function () { if (el.parentNode) el.remove(); }, 160);
        } };
        return el;
    }

    function openPreviewPop(anchor) {
        var el = popover(anchor,
            '<div class="voe-pop-h">Preview poster</div>' +
            '<div class="voe-pop-note">Pick a real title to preview against — it also loads that title’s real values into your badges. Preview only, never saved.</div>' +
            '<div class="voe-pop-search"><input class="voe-input" data-pop-search placeholder="Search your library…" autocomplete="off"></div>' +
            '<div class="voe-pop-clear"><button class="voe-btn" data-pop-random style="width:100%;justify-content:center">' + I.dice + ' Surprise me</button></div>' +
            (ed.bg ? '<div class="voe-pop-clear" style="margin-top:0"><button class="voe-btn" data-pop-blank style="width:100%;justify-content:center">Use blank poster</button></div>' : '') +
            '<div class="voe-pop-body" data-pop-results><div class="voe-pop-empty">Type to search your movies &amp; shows.</div></div>');
        var input = el.querySelector('[data-pop-search]');
        var blank = el.querySelector('[data-pop-blank]');
        el.querySelector('[data-pop-random]').addEventListener('click', function () { closePop(); loadRandomPreview(); });
        if (blank) blank.addEventListener('click', function () { ed.bg = null; ed.previewTitle = null; applyStageBg(); updatePreviewName(); closePop(); });
        var t = null;
        input.addEventListener('input', function () { clearTimeout(t); var q = input.value.trim(); t = setTimeout(function () { previewSearch(q, el); }, 240); });
        setTimeout(function () { input.focus(); }, 40);
    }
    function previewSearch(q, el) {
        var box = el.querySelector('[data-pop-results]');
        if (q.length < 2) { box.innerHTML = '<div class="voe-pop-empty">Type to search your movies &amp; shows.</div>'; return; }
        box.innerHTML = '<div class="voe-pop-empty">Searching…</div>';
        function one(kind) {
            return api('GET', '/api/video/library?kind=' + kind + '&search=' + encodeURIComponent(q) + '&limit=8')
                .then(function (d) { return (d && d.items) || []; }).catch(function () { return []; });
        }
        Promise.all([one('movies'), one('shows')]).then(function (r) {
            var rows = [];
            (r[0] || []).forEach(function (m) { rows.push({ kind: 'movie', id: m.id, title: m.title, year: m.year, hasPoster: m.has_poster, tmdbId: m.tmdb_id }); });
            (r[1] || []).forEach(function (s) { rows.push({ kind: 'show', id: s.id, title: s.title, year: s.year, hasPoster: s.has_poster, tmdbId: s.tmdb_id }); });
            if (!rows.length) { box.innerHTML = '<div class="voe-pop-empty">No matches in your library.</div>'; return; }
            box.innerHTML = rows.map(function (it) {
                var thumb = it.hasPoster ? '/api/video/poster/' + it.kind + '/' + it.id + '?w=60' : '';
                return '<div class="voe-pop-result" data-pick="' + esc(JSON.stringify(it)) + '">' +
                    (thumb ? '<img src="' + esc(thumb) + '" alt="">' : '<img alt="">') +
                    '<div style="min-width:0"><div class="voe-pop-result-t">' + esc(it.title) + '</div>' +
                    '<div class="voe-pop-result-m">' + (it.kind === 'show' ? 'TV' : 'Movie') + (it.year ? ' · ' + esc(it.year) : '') + '</div></div></div>';
            }).join('');
            box.querySelectorAll('[data-pick]').forEach(function (row) {
                row.addEventListener('click', function () { setPreviewTitle(JSON.parse(row.getAttribute('data-pick'))); });
            });
        });
    }
    // "Surprise me" — drop a random owned title into the preview.
    function loadRandomPreview() {
        api('GET', '/api/video/overlays/preview/random').then(function (d) {
            var it = d && d.item;
            if (!it) { toast('No library titles to preview yet', 'info'); return; }
            setPreviewTitle({ kind: it.kind, id: it.id, title: it.title, tmdbId: it.tmdb_id });
            toast('Previewing “' + (it.title || 'a title') + '”', 'success');
        }).catch(function () { toast('Could not load a random title', 'error'); });
    }

    function setPreviewTitle(it) {
        ed.previewTitle = { kind: it.kind, id: it.id, title: it.title };
        // Prefer the CLEAN TMDB poster for the preview — the server copy may already
        // carry another tool's burned-in overlays (Kometa), which you'd design over.
        ed.bg = '/api/video/poster/' + it.kind + '/' + it.id;
        applyStageBg(); updatePreviewName(); closePop();
        if (it.tmdbId) {
            api('GET', '/api/video/poster/options/' + it.kind + '/' + it.tmdbId).then(function (d) {
                var posters = (d && d.posters) || [];
                if (posters.length && ed.previewTitle && ed.previewTitle.id === it.id) { ed.bg = posters[0].full; applyStageBg(); }
            }).catch(function () { /* keep the server proxy fallback */ });
        }
        api('GET', '/api/video/overlays/sample/' + it.kind + '/' + it.id).then(function (d) {
            if (d && d.sample) { ed.sample = mergeSample(d.sample); refreshBoundLayers(); }
        }).catch(function () { /* keep defaults */ });
    }

    function openSamplePop(anchor) {
        var used = [];
        ed.layers.forEach(function (l) { if (l.binding && used.indexOf(l.binding.field) === -1) used.push(l.binding.field); });
        var rest = FIELD_ORDER.filter(function (k) { return used.indexOf(k) === -1; });
        function group(title, keys) {
            if (!keys.length) return '';
            return '<div class="voe-pop-grp">' + title + '</div>' + keys.map(sampleRow).join('');
        }
        var body = (used.length ? group('In this template', used) : '') + group(used.length ? 'Other fields' : 'All fields', rest);
        var el = popover(anchor,
            '<div class="voe-pop-h">Sample data</div>' +
            '<div class="voe-pop-note">Tweak values to preview how badges react in different cases. Preview only — never changes your library or the template.</div>' +
            '<div class="voe-pop-body">' + body + '</div>');
        el.querySelectorAll('[data-sfield]').forEach(function (inp) {
            var key = inp.getAttribute('data-sfield');
            inp.addEventListener('input', function () {
                var f = FIELDS[key], v = inp.value;
                ed.sample[key] = f.num ? (v === '' ? null : parseFloat(v)) : v;
                refreshBoundLayers();
            });
        });
    }
    function sampleRow(key) {
        var f = FIELDS[key], v = ed.sample[key];
        var ctrl;
        if (f.opts) {
            ctrl = '<select class="voe-input" data-sfield="' + key + '">' + f.opts.map(function (o) {
                var lbl = o === '' ? '—' : (f.fmt(o) || o);
                return '<option value="' + esc(o) + '"' + (String(o) === String(v == null ? '' : v) ? ' selected' : '') + '>' + esc(lbl) + '</option>';
            }).join('') + '</select>';
        } else if (f.num) {
            ctrl = '<input class="voe-input voe-input--num" type="number" step="any" data-sfield="' + key + '" value="' + (v == null ? '' : v) + '">';
        } else {
            ctrl = '<input class="voe-input" data-sfield="' + key + '" value="' + esc(v == null ? '' : v) + '">';
        }
        return '<div class="voe-pop-row"><div class="voe-pop-row-l">' + esc(f.label) + '</div><div class="voe-pop-row-c">' + ctrl + '</div></div>';
    }

    // ── confirm dialog ──────────────────────────────────────────────────────────
    function confirmDialog(title, msg, action, onYes) {
        var back = document.createElement('div');
        back.className = 'voe-confirm-back';
        back.innerHTML = '<div class="voe-confirm"><div class="voe-confirm-t">' + esc(title) + '</div>' +
            '<div class="voe-confirm-m">' + esc(msg) + '</div>' +
            '<div class="voe-confirm-row"><button class="voe-btn" data-no>Cancel</button>' +
            '<button class="voe-btn voe-btn--danger" data-yes>' + esc(action) + '</button></div></div>';
        document.body.appendChild(back);
        requestAnimationFrame(function () { back.classList.add('voe-confirm-back--on'); });
        function done() { back.classList.remove('voe-confirm-back--on'); setTimeout(function () { back.remove(); }, 180); }
        back.addEventListener('click', function (e) {
            if (e.target === back || e.target.closest('[data-no]')) done();
            else if (e.target.closest('[data-yes]')) { done(); onYes(); }
        });
    }

    // double-click on stage text → inline edit (bound at overlay level)
    document.addEventListener('dblclick', function (e) {
        if (!ed) return;
        var node = e.target.closest('.voe-layer-text');
        if (!node || !overlay.contains(node)) return;
        var l = layerById(node.getAttribute('data-voe-layer'));
        if (l) enableInlineEdit(node, l);
    });
    // Editor keyboard shortcuts (nudge / delete / duplicate / undo / redo / save).
    function isTyping(t) {
        if (!t) return false;
        var tag = t.tagName;
        return tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT' || t.getAttribute('contenteditable') === 'true';
    }
    document.addEventListener('keydown', function (e) {
        if (!ed || !overlay || !overlay.classList.contains('voe-overlay--on')) return;
        if (isTyping(e.target)) return;
        var meta = e.ctrlKey || e.metaKey;
        if (meta && (e.key === 'z' || e.key === 'Z')) { e.preventDefault(); if (e.shiftKey) redo(); else undo(); return; }
        if (meta && (e.key === 'y' || e.key === 'Y')) { e.preventDefault(); redo(); return; }
        if (meta && (e.key === 's' || e.key === 'S')) { e.preventDefault(); saveTemplate(); return; }
        if (meta && (e.key === 'd' || e.key === 'D')) { e.preventDefault(); if (ed.selected) duplicateLayer(ed.selected); return; }
        if ((e.key === 'Delete' || e.key === 'Backspace') && ed.selected) { e.preventDefault(); removeLayer(ed.selected); return; }
        if (e.key.indexOf('Arrow') === 0 && ed.selected) {
            e.preventDefault();
            var l = layerById(ed.selected); if (!l) return;
            var step = e.shiftKey ? 10 : 1;
            if (e.key === 'ArrowLeft') l.x = clamp01(l.x - step / ed.W);
            else if (e.key === 'ArrowRight') l.x = clamp01(l.x + step / ed.W);
            else if (e.key === 'ArrowUp') l.y = clamp01(l.y - step / ed.H);
            else if (e.key === 'ArrowDown') l.y = clamp01(l.y + step / ed.H);
            refreshLayer(l.id); syncInspectorPos(l); markDirty();
        }
    });

    // Esc closes (unless editing text / a confirm is up)
    document.addEventListener('keydown', function (e) {
        if (e.key !== 'Escape' || !overlay || !overlay.classList.contains('voe-overlay--on')) return;
        if (openPop) { closePop(); return; }
        if (document.querySelector('.voe-confirm-back')) return;
        if (document.activeElement && document.activeElement.getAttribute('contenteditable') === 'true') return;
        if (document.activeElement && document.activeElement.classList.contains('voe-name-input')) return;
        close();
    });

    window.VideoOverlayEditor = { open: open, close: close };
})();
