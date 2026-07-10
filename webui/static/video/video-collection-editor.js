/*
 * SoulSync — Collection Studio.
 *
 * A full-bleed studio page (Overlay Studio chrome) for SoulSync-managed
 * movie/show collections:
 *   · Gallery — every managed collection with live sync state + quick actions.
 *   · Easy setup — Kometa-style preset packs (Genres/Decades/Franchises/…)
 *     expanded against the user's OWN library with real counts.
 *   · Editor — smart-rule or list/franchise builder with live owned preview,
 *     auto-generated collage posters, and sync controls.
 *
 * ISOLATION: self-contained IIFE under static/video/. Exposes only
 * window.VideoCollectionEditor = { open, close }. Talks to /api/video/collections.
 */
(function () {
    'use strict';

    var API = '/api/video/collections';
    var overlay = null;           // .vce-overlay root (lazily built, on body)
    var fieldCache = {};          // media_type -> {fields, suggestions}
    var presetCache = {};         // media_type -> packs[]
    var ed = null;                // editor state
    var gal = { tab: 'all', q: '', collections: null };
    var view = 'gallery';         // gallery | presets | picker | editor

    // ── tiny helpers ─────────────────────────────────────────────────────────
    function esc(s) {
        return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) {
            return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
        });
    }
    function h(tag, cls, html) {
        var e = document.createElement(tag);
        if (cls) e.className = cls;
        if (html != null) e.innerHTML = html;
        return e;
    }
    function api(path, opts) {
        opts = opts || {};
        opts.headers = Object.assign({ 'Content-Type': 'application/json', 'Accept': 'application/json' }, opts.headers || {});
        return fetch(API + path, opts).then(function (r) {
            return r.ok || r.status === 404 ? r.json() : r.json().catch(function () { return { ok: false, error: 'HTTP ' + r.status }; });
        });
    }
    var _t;
    function debounce(fn, ms) {
        return function () { clearTimeout(_t); _t = setTimeout(fn, ms); };
    }
    function memberPosterURL(mediaType, id) {
        return '/api/video/poster/' + (mediaType === 'show' ? 'show' : 'movie') + '/' + id + '?w=140';
    }
    function relTime(sqliteUtc) {
        // collection_sync.synced_at is SQLite datetime('now') = UTC.
        if (!sqliteUtc) return null;
        var d = new Date(String(sqliteUtc).replace(' ', 'T') + 'Z');
        if (isNaN(d)) return null;
        var s = Math.max(0, (Date.now() - d.getTime()) / 1000);
        if (s < 90) return 'just now';
        if (s < 3600) return Math.round(s / 60) + 'm ago';
        if (s < 86400) return Math.round(s / 3600) + 'h ago';
        return Math.round(s / 86400) + 'd ago';
    }
    function mediaWord(mt, cap) {
        var w = mt === 'show' ? 'shows' : 'movies';
        return cap ? w[0].toUpperCase() + w.slice(1) : w;
    }

    // Stroke-based icon set (stroke comes from CSS currentColor).
    var I = {
        brand: '<svg viewBox="0 0 24 24" fill="none" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="3" width="8" height="8" rx="2"/><rect x="13" y="3" width="8" height="8" rx="2"/><rect x="3" y="13" width="8" height="8" rx="2"/><rect x="13" y="13" width="8" height="8" rx="2"/></svg>',
        plus: '<svg viewBox="0 0 24 24" fill="none" stroke-width="2.2" stroke-linecap="round"><path d="M12 5v14M5 12h14"/></svg>',
        spark: '<svg viewBox="0 0 24 24" fill="none" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 3l1.9 5.1L19 10l-5.1 1.9L12 17l-1.9-5.1L5 10l5.1-1.9z"/><path d="M19 15l.9 2.1L22 18l-2.1.9L19 21l-.9-2.1L16 18l2.1-.9z"/></svg>',
        sync: '<svg viewBox="0 0 24 24" fill="none" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 12a9 9 0 1 1-2.6-6.4"/><path d="M21 3v6h-6"/></svg>',
        edit: '<svg viewBox="0 0 24 24" fill="none" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M17 3a2.8 2.8 0 1 1 4 4L7.5 20.5 2 22l1.5-5.5z"/></svg>',
        trash: '<svg viewBox="0 0 24 24" fill="none" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 6h18M8 6V4a1 1 0 0 1 1-1h6a1 1 0 0 1 1 1v2M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6"/></svg>',
        check: '<svg viewBox="0 0 24 24" fill="none" stroke-linecap="round" stroke-linejoin="round"><path d="M20 6L9 17l-5-5"/></svg>',
        search: '<svg viewBox="0 0 24 24" fill="none" stroke-width="2" stroke-linecap="round"><circle cx="11" cy="11" r="7"/><path d="M21 21l-4.3-4.3"/></svg>',
        image: '<svg viewBox="0 0 24 24" fill="none" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="3" width="18" height="18" rx="2"/><circle cx="8.5" cy="8.5" r="1.5"/><path d="M21 15l-5-5L5 21"/></svg>',
        back: '<svg viewBox="0 0 24 24" fill="none" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="M15 18l-6-6 6-6"/></svg>',
        copy: '<svg viewBox="0 0 24 24" fill="none" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="9" y="9" width="13" height="13" rx="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/></svg>',
        server: '<svg viewBox="0 0 24 24" fill="none" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="2" y="3" width="20" height="7" rx="2"/><rect x="2" y="14" width="20" height="7" rx="2"/><path d="M6 6.5h.01M6 17.5h.01"/></svg>',
        // preset pack icons
        genres: '<svg viewBox="0 0 24 24" fill="none" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M7 7h10M4 12h16M7 17h10"/></svg>',
        decades: '<svg viewBox="0 0 24 24" fill="none" stroke-width="2" stroke-linecap="round"><circle cx="12" cy="12" r="9"/><path d="M12 7v5l3.5 2"/></svg>',
        franchises: '<svg viewBox="0 0 24 24" fill="none" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M2 7l10-4 10 4-10 4z"/><path d="M6 9.2V15l6 2.6L18 15V9.2"/></svg>',
        studios: '<svg viewBox="0 0 24 24" fill="none" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M4 21V8l6-5 6 5v13"/><path d="M20 21V11l-4-3"/><path d="M2 21h20"/></svg>',
        networks: '<svg viewBox="0 0 24 24" fill="none" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="2" y="7" width="20" height="13" rx="2"/><path d="M8 2l4 5 4-5"/></svg>',
        directors: '<svg viewBox="0 0 24 24" fill="none" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="7" cy="16" r="4"/><circle cx="17" cy="16" r="4"/><path d="M11 16h2M3.5 13L9 4l3 2 3-2 5.5 9"/></svg>',
        essentials: '<svg viewBox="0 0 24 24" fill="none" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 2l2.5 6.5L21 9l-5 4.5L17.5 21 12 17l-5.5 4L8 13.5 3 9l6.5-.5z"/></svg>'
    };

    // ── open / close / shell ─────────────────────────────────────────────────
    function ensureOverlay() {
        if (overlay) return overlay;
        overlay = h('div', 'vce-overlay');
        document.body.appendChild(overlay);
        return overlay;
    }
    function open(collectionId) {
        ensureOverlay();
        document.body.classList.add('vdh-locked');
        requestAnimationFrame(function () { overlay.classList.add('vce-overlay--on'); });
        if (collectionId != null) loadEditor(collectionId); else showGallery();
    }
    function close() {
        if (!overlay) return;
        overlay.classList.remove('vce-overlay--on');
        document.body.classList.remove('vdh-locked');
        setTimeout(function () { if (overlay) overlay.innerHTML = ''; }, 240);
        ed = null;
        gal.collections = null;
        view = 'gallery';
    }

    function shell(actionsHTML) {
        overlay.innerHTML = '';
        var bar = h('div', 'vce-topbar',
            '<div class="vce-brand"><div class="vce-brand-mark">' + I.brand + '</div>' +
            '<div class="vce-brand-name">Collection <span>Studio</span></div></div>' +
            '<div class="vce-top-spacer"></div>' +
            '<div class="vce-top-actions" data-top-actions style="display:flex;gap:9px;align-items:center;">' + (actionsHTML || '') + '</div>');
        var x = h('button', 'vce-x', '&times;');
        x.type = 'button';
        x.setAttribute('aria-label', 'Close');
        x.addEventListener('click', close);
        bar.appendChild(x);
        overlay.appendChild(bar);
        var scroll = h('div', 'vce-scroll');
        var page = h('div', 'vce-page');
        scroll.appendChild(page);
        overlay.appendChild(scroll);
        return page;
    }
    function topBtn(label, icon, cls) {
        return '<button type="button" class="vce-btn ' + (cls || '') + '" data-top="' + label + '">' +
            (icon || '') + esc(label) + '</button>';
    }
    function wireTop(page, handlers) {
        overlay.querySelectorAll('[data-top]').forEach(function (b) {
            var fn = handlers[b.getAttribute('data-top')];
            if (fn) b.addEventListener('click', function (e) { fn(e.currentTarget); });
        });
    }

    // ── gallery ──────────────────────────────────────────────────────────────
    function showGallery() {
        view = 'gallery';
        var page = shell(
            topBtn('Easy setup', I.spark) +
            topBtn('On server', I.server) +
            topBtn('Sync all', I.sync) +
            topBtn('New collection', I.plus, 'vce-btn--primary'));
        wireTop(page, {
            'Easy setup': function () { showPresets(); },
            'On server': function () { showServer(); },
            'Sync all': syncAll,
            'New collection': function () { newCollection(); }
        });
        page.innerHTML = '<div class="vce-loading">Loading…</div>';
        api('', {}).then(function (d) {
            gal.collections = (d && d.collections) || [];
            if (view === 'gallery') renderGallery(page);
        });
    }

    function renderGallery(page) {
        var cols = gal.collections || [];
        page.innerHTML = '';

        if (!cols.length) {
            var hero = h('div', 'vce-hero',
                '<h2>Group your library into collections</h2>' +
                '<p>Build Plex/Jellyfin collections from smart rules or franchises — and start in one click ' +
                'with presets expanded from what you actually own. Franchise collections can even wishlist ' +
                'the entries you\'re missing.</p>');
            var cta = h('button', 'vce-btn vce-btn--primary', I.spark + 'Browse presets');
            cta.type = 'button';
            cta.addEventListener('click', function () { showPresets(); });
            var alt = h('button', 'vce-btn', I.plus + 'Start from scratch');
            alt.type = 'button';
            alt.addEventListener('click', function () { newCollection(); });
            hero.appendChild(cta);
            hero.appendChild(alt);
            page.appendChild(hero);
            return;
        }

        // stats
        var synced = cols.filter(function (c) { return c.synced_at; });
        var items = synced.reduce(function (n, c) { return n + (c.member_count || 0); }, 0);
        var last = synced.map(function (c) { return c.synced_at; }).sort().pop();
        page.appendChild(h('div', 'vce-stats',
            '<div class="vce-stat"><span class="vce-stat-n">' + cols.length + '</span><span class="vce-stat-l">Collections</span></div>' +
            '<div class="vce-stat"><span class="vce-stat-n">' + synced.length + '</span><span class="vce-stat-l">Synced</span></div>' +
            '<div class="vce-stat"><span class="vce-stat-n">' + items + '</span><span class="vce-stat-l">Items grouped</span></div>' +
            '<div class="vce-stat"><span class="vce-stat-n">' + esc(relTime(last) || '—') + '</span><span class="vce-stat-l">Last sync</span></div>'));

        // filters
        var filters = h('div', 'vce-filters');
        var tabs = h('div', 'vce-tabs');
        [['all', 'All'], ['movie', 'Movies'], ['show', 'Shows']].forEach(function (t) {
            var b = h('button', 'vce-tab' + (gal.tab === t[0] ? ' vce-tab--on' : ''), esc(t[1]));
            b.type = 'button';
            b.addEventListener('click', function () { gal.tab = t[0]; renderGallery(page); });
            tabs.appendChild(b);
        });
        filters.appendChild(tabs);
        var search = h('div', 'vce-search', I.search);
        var si = h('input', 'vce-input');
        si.placeholder = 'Search collections…';
        si.value = gal.q;
        si.addEventListener('input', function () {
            gal.q = si.value;
            paintCards(grid);
        });
        search.appendChild(si);
        filters.appendChild(search);
        page.appendChild(filters);

        var grid = h('div', 'vce-gallery');
        page.appendChild(grid);
        paintCards(grid);
        if (gal.q) { si.focus(); si.setSelectionRange(si.value.length, si.value.length); }
    }

    function paintCards(grid) {
        var q = (gal.q || '').trim().toLowerCase();
        var cols = (gal.collections || []).filter(function (c) {
            if (gal.tab !== 'all' && (c.media_type || 'movie') !== gal.tab) return false;
            if (q && (c.name || '').toLowerCase().indexOf(q) < 0) return false;
            return true;
        });
        grid.innerHTML = '';

        var add = h('button', 'vce-card vce-card--new', I.plus + '<span>New collection</span>');
        add.type = 'button';
        add.addEventListener('click', function () { newCollection(); });
        grid.appendChild(add);

        cols.forEach(function (c) { grid.appendChild(card(c)); });
    }

    function card(c) {
        var el = h('div', 'vce-card');
        var mono = esc((c.name || '?').slice(0, 2));
        var thumbStyle = c.poster_url ? ' style="background-image:url(\'' + esc(c.poster_url) + '\')"' : '';
        var count = (c.member_count == null) ? null : (c.member_count + ' item' + (c.member_count === 1 ? '' : 's'));
        var syncLine = c.synced_at
            ? '<span class="vce-dot vce-dot--ok"></span>Synced ' + esc(relTime(c.synced_at) || '')
            : '<span class="vce-dot"></span>Never synced';
        if (!c.enabled) syncLine = '<span class="vce-dot vce-dot--warn"></span>Paused (not in daily sync)';
        el.innerHTML =
            '<div class="vce-card-thumb"' + thumbStyle + '>' +
                (c.poster_url ? '' : '<div class="vce-card-mono">' + mono + '</div>') +
                '<div class="vce-card-badges">' +
                    '<span class="vce-chip">' + (c.kind === 'list' ? 'List' : 'Smart') + '</span>' +
                    (c.pinned ? '<span class="vce-chip">Pinned</span>' : '') +
                    (c.wishlist_missing ? '<span class="vce-chip vce-chip--warn">Wishlists</span>' : '') +
                '</div>' +
                '<button type="button" class="vce-toggle' + (c.enabled ? ' vce-toggle--on' : '') + '" data-act="toggle" ' +
                    'title="' + (c.enabled ? 'In daily sync — click to pause' : 'Paused — click to include in daily sync') + '"></button>' +
                '<div class="vce-card-acts">' +
                    '<button type="button" class="vce-mini" data-act="edit">' + I.edit + 'Edit</button>' +
                    '<button type="button" class="vce-mini" data-act="sync">' + I.sync + 'Sync</button>' +
                    '<button type="button" class="vce-mini vce-mini--danger" data-act="del" aria-label="Delete">' + I.trash + '</button>' +
                '</div>' +
            '</div>' +
            '<div class="vce-card-body">' +
                '<div class="vce-card-name">' + esc(c.name) + '</div>' +
                '<div class="vce-card-meta">' + mediaWord(c.media_type, true) + (count ? ' · ' + esc(count) : '') + '</div>' +
                '<div class="vce-card-sync">' + syncLine + '</div>' +
            '</div>';
        el.addEventListener('click', function () { loadEditor(c.id); });
        el.querySelector('[data-act="edit"]').addEventListener('click', function (e) {
            e.stopPropagation(); loadEditor(c.id);
        });
        el.querySelector('[data-act="sync"]').addEventListener('click', function (e) {
            e.stopPropagation(); syncOne(c.id, e.currentTarget, function () { refreshGallery(); });
        });
        el.querySelector('[data-act="del"]').addEventListener('click', function (e) {
            e.stopPropagation(); delCollection(c.id, c.name);
        });
        el.querySelector('[data-act="toggle"]').addEventListener('click', function (e) {
            e.stopPropagation();
            var to = !c.enabled;
            api('/' + c.id, { method: 'PUT', body: JSON.stringify({ enabled: to }) }).then(function (d) {
                if (d && d.ok !== false) { c.enabled = to; refreshGallery(); }
                else toast((d && d.error) || 'Update failed', true);
            });
        });
        return el;
    }

    function refreshGallery() {
        if (view !== 'gallery') return;
        api('', {}).then(function (d) {
            if (view !== 'gallery') return;
            gal.collections = (d && d.collections) || [];
            var page = overlay.querySelector('.vce-page');
            if (page) renderGallery(page);
        });
    }

    function syncAll(btn) {
        if (btn) { btn.disabled = true; btn.textContent = 'Syncing…'; }
        api('/sync', { method: 'POST' }).then(function (d) {
            if (d && d.ok) {
                var bits = [d.synced + '/' + d.total + ' synced'];
                if (d.added) bits.push('+' + d.added);
                if (d.removed) bits.push('-' + d.removed);
                if (d.wishlisted) bits.push(d.wishlisted + ' wishlisted');
                if (d.failed) bits.push(d.failed + ' failed');
                toast('Sync complete — ' + bits.join(' · '), !!d.failed);
            } else { toast((d && d.error) || 'Sync failed', true); }
            refreshGallery();
        }).catch(function () { toast('Sync failed', true); })
          .finally(function () { if (btn) { btn.disabled = false; btn.innerHTML = I.sync + 'Sync all'; } });
    }

    // ── preset browser (Easy setup) ──────────────────────────────────────────
    var presetMedia = 'movie';

    function showPresets(mediaType) {
        view = 'presets';
        if (mediaType) presetMedia = mediaType;
        var page = shell(topBtn('Back to gallery', I.back, 'vce-btn--ghost'));
        wireTop(page, { 'Back to gallery': function () { showGallery(); } });

        var head = h('div', 'vce-preshead', '<h2>Easy setup</h2>');
        var tabs = h('div', 'vce-tabs');
        [['movie', 'Movies'], ['show', 'Shows']].forEach(function (t) {
            var b = h('button', 'vce-tab' + (presetMedia === t[0] ? ' vce-tab--on' : ''), esc(t[1]));
            b.type = 'button';
            b.addEventListener('click', function () { showPresets(t[0]); });
            tabs.appendChild(b);
        });
        head.appendChild(tabs);
        page.appendChild(head);
        page.appendChild(h('p', 'vce-pressub',
            'Ready-made collection packs, built from what you actually own — pick a pack, tick what you want, done.'));

        var hostEl = h('div');
        hostEl.innerHTML = '<div class="vce-loading">Reading your library…</div>';
        page.appendChild(hostEl);

        var mt = presetMedia;
        loadPresets(mt).then(function (packs) {
            if (view !== 'presets' || presetMedia !== mt) return;   // stale response
            hostEl.innerHTML = '';
            var grid = h('div', 'vce-packs');
            packs.forEach(function (p) {
                var b = h('button', 'vce-pack',
                    '<div class="vce-pack-icon">' + (I[p.icon] || I.spark) + '</div>' +
                    '<div class="vce-pack-title">' + esc(p.title) + '</div>' +
                    '<div class="vce-pack-blurb">' + esc(p.blurb) + '</div>' +
                    '<div class="vce-pack-count">' + (p.available
                        ? p.available + ' collection' + (p.available === 1 ? '' : 's') + ' · ' + p.item_total + ' items'
                        : 'Nothing to build yet') + '</div>');
                b.type = 'button';
                b.disabled = !p.available;
                if (p.available) b.addEventListener('click', function () { showPicker(p); });
                grid.appendChild(b);
            });
            hostEl.appendChild(grid);
        });
    }

    function loadPresets(mt) {
        if (presetCache[mt]) return Promise.resolve(presetCache[mt]);
        return api('/presets?media_type=' + mt, {}).then(function (d) {
            presetCache[mt] = (d && d.packs) || [];
            return presetCache[mt];
        });
    }

    function showPicker(pack) {
        view = 'picker';
        var page = shell(topBtn('Back to packs', I.back, 'vce-btn--ghost'));
        wireTop(page, { 'Back to packs': function () { showPresets(); } });

        var picked = {};
        pack.entries.forEach(function (e) { if (e.suggested && !e.exists) picked[e.key] = true; });

        var top = h('div', 'vce-picker-top',
            '<h2>' + esc(pack.title) + ' — ' + mediaWord(pack.media_type) + '</h2>' +
            '<span class="vce-selsum" data-selsum></span>');
        page.appendChild(top);

        var quick = h('div', 'vce-quick');
        [['Suggested', function (e) { return e.suggested && !e.exists; }],
         ['All', function (e) { return !e.exists; }],
         ['None', function () { return false; }]].forEach(function (q) {
            var b = h('button', 'vce-link', esc(q[0]));
            b.type = 'button';
            b.addEventListener('click', function () {
                picked = {};
                pack.entries.forEach(function (e) { if (q[1](e)) picked[e.key] = true; });
                paintEntries();
            });
            quick.appendChild(b);
        });
        page.appendChild(quick);

        var list = h('div', 'vce-entries');
        page.appendChild(list);

        var foot = h('div', 'vce-picker-foot');
        var wl = null;
        if (pack.entries.some(function (e) { return e.wishlist_capable; })) {
            foot.innerHTML = '<label class="vce-wl"><input type="checkbox" checked data-wl> ' +
                'Wishlist the ' + esc(mediaWord(pack.media_type)) + ' I\'m missing from each series</label>';
            wl = foot.querySelector('[data-wl]');
        }
        foot.appendChild(h('div', 'vce-foot-spacer'));
        var createBtn = h('button', 'vce-btn vce-btn--primary', 'Create');
        createBtn.type = 'button';
        createBtn.addEventListener('click', function () {
            var keys = Object.keys(picked);
            if (!keys.length) return;
            createBtn.disabled = true;
            createBtn.textContent = 'Creating…';
            api('/presets/apply', { method: 'POST', body: JSON.stringify({
                media_type: pack.media_type, pack: pack.id, keys: keys,
                wishlist_missing: wl ? wl.checked : false
            }) }).then(function (d) {
                if (d && d.ok) {
                    delete presetCache[pack.media_type];   // exists-marks changed
                    var n = (d.created || []).length;
                    toast('Created ' + n + ' collection' + (n === 1 ? '' : 's') + ' — generating posters…');
                    showGallery();
                    // Collage posters render off-request; pick them up shortly.
                    setTimeout(refreshGallery, 6000);
                } else {
                    toast((d && d.error) || 'Could not create collections', true);
                    createBtn.disabled = false;
                    createBtn.textContent = 'Create';
                }
            }).catch(function () {
                toast('Could not create collections', true);
                createBtn.disabled = false;
                createBtn.textContent = 'Create';
            });
        });
        foot.appendChild(createBtn);
        page.appendChild(foot);

        function paintEntries() {
            list.innerHTML = '';
            pack.entries.forEach(function (e) {
                var on = !!picked[e.key];
                var row = h('label', 'vce-entry' + (on ? ' vce-entry--on' : '') + (e.exists ? ' vce-entry--exists' : ''),
                    '<span class="vce-cb">' + I.check + '</span>' +
                    '<span class="vce-entry-name">' + esc(e.name) + '</span>' +
                    (e.exists ? '<span class="vce-entry-added">Added</span>'
                              : '<span class="vce-entry-count">' + e.count + '</span>'));
                if (!e.exists) {
                    row.addEventListener('click', function () {
                        if (picked[e.key]) delete picked[e.key]; else picked[e.key] = true;
                        paintEntries();
                    });
                }
                list.appendChild(row);
            });
            var n = Object.keys(picked).length;
            var total = pack.entries.reduce(function (s, e) { return s + (picked[e.key] ? e.count : 0); }, 0);
            var sum = overlay.querySelector('[data-selsum]');
            if (sum) sum.textContent = n ? (n + ' selected · ' + total + ' items') : 'Nothing selected';
            createBtn.disabled = !n;
            createBtn.textContent = n ? ('Create ' + n + ' collection' + (n === 1 ? '' : 's')) : 'Create';
        }
        paintEntries();
    }

    // ── server-side collections (cleanup view) ──────────────────────────────
    function showServer() {
        view = 'server';
        var page = shell(topBtn('Back to gallery', I.back, 'vce-btn--ghost'));
        wireTop(page, { 'Back to gallery': function () { showGallery(); } });

        page.appendChild(h('div', 'vce-preshead', '<h2>Collections on your server</h2>'));
        page.appendChild(h('p', 'vce-servnote',
            'Everything that currently exists on the media server — including collections made by other tools ' +
            '(old Kometa runs, hand-made ones). Deleting here removes the collection from the server only; ' +
            'titles are never touched. Deleting a SoulSync-managed collection just recreates it on the next ' +
            'sync unless you also pause or delete its definition in the gallery.'));

        var hostEl = h('div');
        hostEl.innerHTML = '<div class="vce-loading">Reading the server…</div>';
        page.appendChild(hostEl);

        api('/server', {}).then(function (d) {
            if (view !== 'server') return;
            if (!d || d.ok === false) {
                hostEl.innerHTML = '<div class="vce-loading">' + esc((d && d.error) || 'Could not reach the server') + '</div>';
                return;
            }
            var cols = d.collections || [];
            if (!cols.length) {
                hostEl.innerHTML = '<div class="vce-loading">No collections on the server.</div>';
                return;
            }
            hostEl.innerHTML = '';
            var picked = {};

            var quick = h('div', 'vce-quick');
            var selects = [['Not SoulSync’s', function (c) { return !c.managed; }]];
            if (cols.some(function (c) { return c.kometa; })) {
                selects.push(['Kometa’s', function (c) { return c.kometa; }]);
            }
            selects.concat([
             ['All', function () { return true; }],
             ['None', function () { return false; }]]).forEach(function (q) {
                var b = h('button', 'vce-link', esc(q[0]));
                b.type = 'button';
                b.addEventListener('click', function () {
                    picked = {};
                    cols.forEach(function (c) { if (q[1](c)) picked[c.server_id] = true; });
                    paintRows();
                });
                quick.appendChild(b);
            });
            hostEl.appendChild(quick);

            var list = h('div', 'vce-entries vce-entries--rows');
            hostEl.appendChild(list);

            var foot = h('div', 'vce-picker-foot');
            foot.appendChild(h('span', 'vce-selsum', ''));
            foot.appendChild(h('div', 'vce-foot-spacer'));
            var delBtn = h('button', 'vce-btn vce-btn--danger', I.trash + 'Delete selected');
            delBtn.type = 'button';
            delBtn.addEventListener('click', function () {
                var ids = Object.keys(picked);
                if (!ids.length) return;
                var managedN = cols.filter(function (c) { return picked[c.server_id] && c.managed; }).length;
                var msg = 'Delete ' + ids.length + ' collection' + (ids.length === 1 ? '' : 's') + ' from the server?' +
                    (managedN ? ('\n\n' + managedN + ' of these are SoulSync-managed and will be recreated on the next sync.') : '') +
                    '\n\nTitles themselves are never touched.';
                if (!window.confirm(msg)) return;
                delBtn.disabled = true;
                delBtn.textContent = 'Deleting…';
                api('/server/delete', { method: 'POST', body: JSON.stringify({ ids: ids }) }).then(function (r) {
                    if (r && r.ok) {
                        var bits = [r.deleted + ' deleted'];
                        if (r.failed && r.failed.length) bits.push(r.failed.length + ' failed');
                        toast(bits.join(' · '), !!(r.failed && r.failed.length));
                    } else { toast((r && r.error) || 'Delete failed', true); }
                    showServer();   // re-read the server
                }).catch(function () {
                    toast('Delete failed', true);
                    delBtn.disabled = false;
                    delBtn.innerHTML = I.trash + 'Delete selected';
                });
            });
            foot.appendChild(delBtn);
            hostEl.appendChild(foot);

            function paintRows() {
                list.innerHTML = '';
                cols.forEach(function (c) {
                    var on = !!picked[c.server_id];
                    var row = h('label', 'vce-entry' + (on ? ' vce-entry--on' : ''),
                        '<span class="vce-cb">' + I.check + '</span>' +
                        '<span class="vce-entry-name">' + esc(c.name || '(unnamed)') + '</span>' +
                        (c.media_type ? '<span class="vce-tag">' + esc(mediaWord(c.media_type, true)) + '</span>' : '') +
                        (c.smart ? '<span class="vce-tag" title="Filter-based smart collection — not created by SoulSync">Smart</span>' : '') +
                        (c.kometa ? '<span class="vce-tag vce-tag--warn" title="Carries a Kometa/PMM label">Kometa</span>' : '') +
                        (c.managed ? '<span class="vce-tag vce-tag--ok" title="Managed by the definition “' + esc(c.definition_name || '') + '”">SoulSync</span>' : '') +
                        '<span class="vce-entry-count">' + (c.count || 0) + '</span>');
                    row.addEventListener('click', function () {
                        if (picked[c.server_id]) delete picked[c.server_id]; else picked[c.server_id] = true;
                        paintRows();
                    });
                    list.appendChild(row);
                });
                var n = Object.keys(picked).length;
                var sum = foot.querySelector('.vce-selsum');
                if (sum) sum.textContent = n ? (n + ' selected') : 'Nothing selected';
                delBtn.disabled = !n;
            }
            paintRows();
        });
    }

    // ── editor ───────────────────────────────────────────────────────────────
    function newCollection() {
        ed = { id: null, name: '', kind: 'smart', media_type: 'movie',
               definition: { match: 'all', rules: [] },
               summary: '', sort_order: 'release', sync_mode: 'sync',
               pinned: false, wishlist_missing: false, enabled: true, poster_url: '',
               dirty: false };
        renderEditor();
    }

    function loadEditor(id) {
        ensureOverlay();
        overlay.classList.add('vce-overlay--on');
        view = 'editor';
        var page = shell('');
        page.innerHTML = '<div class="vce-loading">Loading…</div>';
        api('/' + id, {}).then(function (d) {
            var c = d && d.collection;
            if (!c) { page.innerHTML = '<div class="vce-loading">Not found.</div>'; return; }
            ed = {
                id: c.id, name: c.name || '', kind: c.kind || 'smart',
                media_type: c.media_type || 'movie',
                definition: c.definition && Object.keys(c.definition).length ? c.definition : { match: 'all', rules: [] },
                summary: c.summary || '', sort_order: c.sort_order || 'release',
                sync_mode: c.sync_mode || 'sync', pinned: !!c.pinned,
                wishlist_missing: !!c.wishlist_missing, enabled: c.enabled == null ? true : !!c.enabled,
                poster_url: c.poster_url || '', dirty: false
            };
            renderEditor();
        });
    }

    function leaveEditor() {
        if (ed && ed.dirty && !window.confirm('Discard unsaved changes?')) return;
        showGallery();
    }

    function renderEditor() {
        view = 'editor';
        var page = shell(topBtn('Back to gallery', I.back, 'vce-btn--ghost'));
        wireTop(page, { 'Back to gallery': leaveEditor });
        page.innerHTML = '';
        var cols = h('div', 'vce-editor');

        // ── left: definition + builder
        var left = h('div');
        var defPanel = h('div', 'vce-panel',
            '<p class="vce-panel-t">' + (ed.id ? 'Edit collection' : 'New collection') + '</p>' +
            '<label class="vce-flabel">Name</label>' +
            '<input class="vce-input" data-f="name" value="' + esc(ed.name) + '" placeholder="e.g. 80s Action">' +
            '<div class="vce-row2" style="margin-top:13px">' +
                '<div><label class="vce-flabel">Library</label>' +
                    sel('media_type', ed.media_type, [['movie', 'Movies'], ['show', 'Shows']]) + '</div>' +
                '<div><label class="vce-flabel">Builder</label>' +
                    sel('kind', ed.kind, [['smart', 'Smart filter'], ['list', 'List / franchise']]) + '</div>' +
            '</div>');
        left.appendChild(defPanel);
        var builderPanel = h('div', 'vce-panel',
            '<p class="vce-panel-t">' + (ed.kind === 'list' ? 'Source' : 'Rules') + '</p>' +
            '<div class="vce-builder" data-builder></div>');
        left.appendChild(builderPanel);
        cols.appendChild(left);

        // ── right: preview + presentation + sync
        var right = h('div');
        right.appendChild(h('div', 'vce-panel',
            '<p class="vce-panel-t">Live preview</p>' +
            '<div class="vce-preview-count"><span class="vce-preview-n" data-pv-n>—</span>' +
            '<span class="vce-preview-l" data-pv-l>owned items</span>' +
            '<span class="vce-preview-miss" data-pv-miss></span></div>' +
            '<div class="vce-preview-grid" data-preview-grid></div>'));

        var poster = ed.poster_url;
        right.appendChild(h('div', 'vce-panel',
            '<p class="vce-panel-t">Presentation</p>' +
            '<div class="vce-poster-row">' +
                '<div class="vce-poster-thumb" data-poster' + (poster ? ' style="background-image:url(\'' + esc(poster) + '\')"' : '') + '>' +
                    (poster ? '' : '<div class="vce-card-mono">' + esc((ed.name || '?').slice(0, 2)) + '</div>') +
                '</div>' +
                '<div class="vce-poster-side">' +
                    '<button type="button" class="vce-btn" data-act="genposter"' + (ed.id ? '' : ' disabled title="Save first"') + '>' +
                        I.image + 'Generate collage</button>' +
                    '<label class="vce-flabel">or poster URL</label>' +
                    '<input class="vce-input" data-f="poster_url" value="' + esc(ed.poster_url) + '" placeholder="https://…">' +
                '</div>' +
            '</div>' +
            '<label class="vce-flabel">Summary</label>' +
            '<textarea class="vce-input" data-f="summary" rows="2" placeholder="Optional description shown on the server">' + esc(ed.summary) + '</textarea>' +
            '<div class="vce-row2" style="margin-top:2px">' +
                '<div><label class="vce-flabel">Sort</label>' +
                    sel('sort_order', ed.sort_order, [['release', 'Release date'], ['alpha', 'A → Z'], ['rating', 'Rating'], ['added', 'Date added'], ['custom', 'Custom']]) + '</div>' +
                '<div><label class="vce-flabel">Sync mode</label>' +
                    sel('sync_mode', ed.sync_mode, [['sync', 'Sync (add + remove)'], ['append', 'Append (add only)']]) + '</div>' +
            '</div>' +
            '<label class="vce-check"><input type="checkbox" data-f="pinned"' + (ed.pinned ? ' checked' : '') + '> Pin to server home</label>' +
            '<label class="vce-check" data-wishlist-row><input type="checkbox" data-f="wishlist_missing"' + (ed.wishlist_missing ? ' checked' : '') + '> Wishlist members I don\'t own</label>' +
            '<label class="vce-check"><input type="checkbox" data-f="enabled"' + (ed.enabled ? ' checked' : '') + '> Include in daily sync</label>'));
        cols.appendChild(right);
        page.appendChild(cols);

        // ── sticky actions
        var acts = h('div', 'vce-actions',
            '<button type="button" class="vce-btn vce-btn--primary" data-act="save">Save</button>' +
            '<button type="button" class="vce-btn" data-act="syncnow"' + (ed.id ? '' : ' disabled title="Save first"') + '>' + I.sync + 'Sync now</button>' +
            (ed.id ? '<button type="button" class="vce-btn" data-act="dup">' + I.copy + 'Duplicate</button>' : '') +
            (ed.id ? '<button type="button" class="vce-btn vce-btn--danger" data-act="del">' + I.trash + 'Delete</button>' : ''));
        page.appendChild(acts);

        // field bindings
        page.querySelectorAll('[data-f]').forEach(function (inp) {
            var f = inp.getAttribute('data-f');
            inp.addEventListener(inp.type === 'checkbox' ? 'change' : 'input', function () {
                ed[f] = inp.type === 'checkbox' ? inp.checked : inp.value;
                ed.dirty = true;
                if (f === 'media_type') {
                    if ((ed.definition.rules || []).length &&
                        !window.confirm('Switching library clears the current rules. Continue?')) {
                        ed.media_type = inp.value === 'movie' ? 'show' : 'movie';
                        inp.value = ed.media_type;
                        return;
                    }
                    ed.definition = { match: ed.definition.match || 'all', rules: [] };
                    renderBuilder(); schedulePreview();
                }
                if (f === 'kind') { renderBuilder(); schedulePreview(); }
            });
        });
        page.querySelector('[data-act="save"]').addEventListener('click', function (e) { save(e.currentTarget); });
        var syncBtn = page.querySelector('[data-act="syncnow"]');
        if (syncBtn) syncBtn.addEventListener('click', function (e) { if (ed.id) syncOne(ed.id, e.currentTarget); });
        var dupBtn = page.querySelector('[data-act="dup"]');
        if (dupBtn) dupBtn.addEventListener('click', function () {
            api('/' + ed.id + '/duplicate', { method: 'POST' }).then(function (d) {
                if (d && d.ok) { toast('Duplicated'); loadEditor(d.id); }
                else toast((d && d.error) || 'Duplicate failed', true);
            });
        });
        var delBtn = page.querySelector('[data-act="del"]');
        if (delBtn) delBtn.addEventListener('click', function () { delCollection(ed.id, ed.name, true); });
        var genBtn = page.querySelector('[data-act="genposter"]');
        if (genBtn) genBtn.addEventListener('click', function () {
            if (!ed.id) return;
            genBtn.disabled = true;
            genBtn.innerHTML = I.image + 'Rendering…';
            api('/' + ed.id + '/poster/generate', { method: 'POST' }).then(function (d) {
                if (d && d.ok) {
                    ed.poster_url = d.poster_url;
                    var thumb = overlay.querySelector('[data-poster]');
                    if (thumb) { thumb.style.backgroundImage = 'url("' + d.poster_url + '")'; thumb.innerHTML = ''; }
                    var urlInp = overlay.querySelector('[data-f="poster_url"]');
                    if (urlInp) urlInp.value = d.poster_url;
                    toast('Poster generated');
                } else { toast((d && d.error) || 'Poster generation failed', true); }
            }).finally(function () { genBtn.disabled = false; genBtn.innerHTML = I.image + 'Generate collage'; });
        });

        toggleWishlistRow();
        renderBuilder();
        schedulePreview();
    }

    function sel(field, val, opts) {
        return '<select class="vce-input" data-f="' + field + '">' +
            opts.map(function (o) {
                return '<option value="' + o[0] + '"' + (o[0] === val ? ' selected' : '') + '>' + esc(o[1]) + '</option>';
            }).join('') + '</select>';
    }

    function toggleWishlistRow() {
        var row = overlay.querySelector('[data-wishlist-row]');
        if (row) row.style.display = ed.kind === 'list' ? '' : 'none';
    }

    // ── builder (smart rules OR list source) ─────────────────────────────────
    function renderBuilder() {
        toggleWishlistRow();
        var host = overlay.querySelector('[data-builder]');
        if (!host) return;
        var title = host.parentNode.querySelector('.vce-panel-t');
        if (title) title.textContent = ed.kind === 'list' ? 'Source' : 'Rules';
        if (ed.kind === 'list') { renderListBuilder(host); return; }
        ensureFields(ed.media_type).then(function () { renderSmartBuilder(host); });
    }

    function ensureFields(mt) {
        if (fieldCache[mt]) return Promise.resolve(fieldCache[mt]);
        return api('/fields?media_type=' + mt, {}).then(function (d) {
            fieldCache[mt] = d || { fields: [], suggestions: {} };
            return fieldCache[mt];
        });
    }

    function renderSmartBuilder(host) {
        var meta = fieldCache[ed.media_type] || { fields: [], suggestions: {} };
        var def = ed.definition;
        def.rules = def.rules || [];
        host.innerHTML =
            '<div class="vce-match">Match <select class="vce-input vce-match-sel" data-match>' +
            '<option value="all"' + (def.match !== 'any' ? ' selected' : '') + '>all</option>' +
            '<option value="any"' + (def.match === 'any' ? ' selected' : '') + '>any</option>' +
            '</select> of these rules</div><div class="vce-rules" data-rules></div>' +
            '<button type="button" class="vce-addrule" data-addrule>' + I.plus + 'Add rule</button>' +
            '<div class="vce-sugg" data-sugg></div>';
        host.querySelector('[data-match]').addEventListener('change', function (e) {
            def.match = e.target.value; ed.dirty = true; schedulePreview();
        });
        host.querySelector('[data-addrule]').addEventListener('click', function () {
            def.rules.push({ field: meta.fields[0] ? meta.fields[0].field : 'year', op: '', value: '' });
            ed.dirty = true;
            paintRules(); schedulePreview();
        });
        paintRules();
        paintSuggestions();
    }

    // Quick-add genre chips from the library's own top genres.
    function paintSuggestions() {
        var hostEl = overlay.querySelector('[data-sugg]');
        if (!hostEl) return;
        var meta = fieldCache[ed.media_type] || { suggestions: {} };
        var genres = (meta.suggestions && meta.suggestions.genre) || [];
        if (!genres.length) { hostEl.innerHTML = ''; return; }
        var rule = (ed.definition.rules || []).filter(function (r) { return r.field === 'genre' && r.op !== 'not_in'; })[0];
        var active = rule ? (Array.isArray(rule.value) ? rule.value : String(rule.value || '').split(',').map(function (s) { return s.trim(); })) : [];
        hostEl.innerHTML = '<span class="vce-sugg-t">Quick add — your top genres</span>';
        genres.slice(0, 14).forEach(function (g) {
            var on = active.indexOf(g) >= 0;
            var chip = h('button', 'vce-sugg-chip' + (on ? ' vce-sugg-chip--on' : ''), esc(g));
            chip.type = 'button';
            chip.addEventListener('click', function () {
                var r = (ed.definition.rules || []).filter(function (x) { return x.field === 'genre' && x.op !== 'not_in'; })[0];
                if (!r) {
                    r = { field: 'genre', op: 'in', value: [] };
                    ed.definition.rules.push(r);
                }
                var vals = Array.isArray(r.value) ? r.value.slice() : String(r.value || '').split(',').map(function (s) { return s.trim(); }).filter(Boolean);
                var i = vals.indexOf(g);
                if (i >= 0) vals.splice(i, 1); else vals.push(g);
                r.value = vals;
                if (!vals.length) ed.definition.rules.splice(ed.definition.rules.indexOf(r), 1);
                ed.dirty = true;
                paintRules(); paintSuggestions(); schedulePreview();
            });
            hostEl.appendChild(chip);
        });
    }

    function paintRules() {
        var meta = fieldCache[ed.media_type] || { fields: [], suggestions: {} };
        var host = overlay.querySelector('[data-rules]');
        if (!host) return;
        host.innerHTML = '';
        ed.definition.rules.forEach(function (rule, i) {
            var spec = meta.fields.filter(function (f) { return f.field === rule.field; })[0] || meta.fields[0];
            if (!spec) return;
            if (!rule.op || spec.ops.indexOf(rule.op) < 0) rule.op = spec.ops[0];
            var row = h('div', 'vce-rule');
            row.appendChild(mkFieldSelect(meta, rule, i));
            row.appendChild(mkOpSelect(spec, rule, i));
            row.appendChild(mkValueInput(spec, rule, i));
            var rm = h('button', 'vce-rule-x', '&times;');
            rm.type = 'button';
            rm.setAttribute('aria-label', 'Remove rule');
            rm.addEventListener('click', function () {
                ed.definition.rules.splice(i, 1);
                ed.dirty = true;
                paintRules(); paintSuggestions(); schedulePreview();
            });
            row.appendChild(rm);
            host.appendChild(row);
        });
    }

    function mkFieldSelect(meta, rule, i) {
        var s = h('select', 'vce-input vce-rule-field');
        s.innerHTML = meta.fields.map(function (f) {
            return '<option value="' + f.field + '"' + (f.field === rule.field ? ' selected' : '') + '>' + esc(f.label) + '</option>';
        }).join('');
        s.addEventListener('change', function () {
            rule.field = s.value; rule.op = ''; rule.value = '';
            ed.dirty = true;
            paintRules(); paintSuggestions(); schedulePreview();
        });
        return s;
    }
    function mkOpSelect(spec, rule, i) {
        var s = h('select', 'vce-input vce-rule-op');
        s.innerHTML = spec.ops.map(function (o) {
            return '<option value="' + o + '"' + (o === rule.op ? ' selected' : '') + '>' + esc(OP_LABELS[o] || o) + '</option>';
        }).join('');
        s.addEventListener('change', function () { rule.op = s.value; ed.dirty = true; paintRules(); schedulePreview(); });
        return s;
    }
    var OP_LABELS = { is: 'is', is_not: 'is not', in: 'is any of', not_in: 'is none of', contains: 'contains',
        gte: '≥', lte: '≤', between: 'between', before: 'before', after: 'after', in_last_days: 'in last (days)', exists: 'exists' };

    function mkValueInput(spec, rule, i) {
        var wrap = h('span', 'vce-rule-val');
        if (rule.op === 'exists') { wrap.innerHTML = '<span class="vce-rule-noval">(no value)</span>'; return wrap; }
        if (rule.op === 'between') {
            var v = Array.isArray(rule.value) ? rule.value : ['', ''];
            wrap.innerHTML = '<input class="vce-input vce-vnum" placeholder="low" value="' + esc(v[0]) + '"> – <input class="vce-input vce-vnum" placeholder="high" value="' + esc(v[1]) + '">';
            var ins = wrap.querySelectorAll('input');
            function upd() { rule.value = [ins[0].value, ins[1].value]; ed.dirty = true; schedulePreview(); }
            ins[0].addEventListener('input', upd); ins[1].addEventListener('input', upd);
            return wrap;
        }
        var listOp = (rule.op === 'in' || rule.op === 'not_in');
        var ph = listOp ? 'comma separated' : (spec.type === 'number' ? 'number' : (spec.type === 'date' ? 'YYYY-MM-DD' : 'value'));
        var val = Array.isArray(rule.value) ? rule.value.join(', ') : (rule.value == null ? '' : rule.value);
        var inp = h('input', 'vce-input');
        inp.type = (spec.type === 'number' && !listOp) ? 'number' : 'text';
        inp.placeholder = ph;
        inp.value = val;
        var opts = spec.options || ((fieldCache[ed.media_type] || {}).suggestions || {})[rule.field];
        if (opts && opts.length) {
            var dlid = 'vce-dl-' + rule.field + '-' + i;
            inp.setAttribute('list', dlid);
            var dl = h('datalist'); dl.id = dlid;
            dl.innerHTML = opts.map(function (o) { return '<option value="' + esc(o) + '">'; }).join('');
            wrap.appendChild(dl);
        }
        inp.addEventListener('input', function () {
            rule.value = listOp ? inp.value.split(',').map(function (s) { return s.trim(); }).filter(Boolean) : inp.value;
            ed.dirty = true;
            if (rule.field === 'genre') paintSuggestions();
            schedulePreview();
        });
        wrap.appendChild(inp);
        return wrap;
    }

    function renderListBuilder(host) {
        var def = ed.definition;
        if (!def.source) def.source = 'tmdb_collection';
        var sources = [['tmdb_collection', 'TMDB franchise (collection id)'], ['tmdb_list', 'TMDB list'], ['trakt_list', 'Trakt list URL']];
        host.innerHTML =
            '<label class="vce-flabel">List source</label>' +
            '<select class="vce-input" data-source-sel>' +
                sources.map(function (o) { return '<option value="' + o[0] + '"' + (o[0] === def.source ? ' selected' : '') + '>' + esc(o[1]) + '</option>'; }).join('') +
            '</select>' +
            '<label class="vce-flabel">' + (def.source === 'tmdb_collection' ? 'TMDB collection id' : (def.source === 'trakt_list' ? 'Trakt list URL' : 'TMDB list id')) + '</label>' +
            '<input class="vce-input" data-listref placeholder="' + (def.source === 'tmdb_collection' ? 'e.g. 10 (Star Wars Collection)' : 'reference') + '" value="' + esc(def.collection_id || def.list_id || def.url || '') + '">' +
            '<p class="vce-note">Franchise members you own preview instantly. Full list membership (and wishlisting the ones you don\'t own) resolves on Sync.</p>';
        host.querySelector('[data-source-sel]').addEventListener('change', function (e) {
            def.source = e.target.value;
            ed.dirty = true;
            renderListBuilder(host);   // relabel the reference input
            schedulePreview();
        });
        host.querySelector('[data-listref]').addEventListener('input', function (e) {
            var v = e.target.value.trim();
            delete def.collection_id; delete def.list_id; delete def.url;
            if (def.source === 'tmdb_collection') def.collection_id = v ? parseInt(v, 10) : null;
            else if (def.source === 'trakt_list') def.url = v;
            else def.list_id = v;
            ed.dirty = true;
            schedulePreview();
        });
    }

    // ── live preview ─────────────────────────────────────────────────────────
    var schedulePreview = debounce(runPreview, 350);
    function runPreview() {
        var grid = overlay.querySelector('[data-preview-grid]');
        var nEl = overlay.querySelector('[data-pv-n]');
        var lEl = overlay.querySelector('[data-pv-l]');
        var missEl = overlay.querySelector('[data-pv-miss]');
        if (!grid || !nEl) return;
        lEl.textContent = 'previewing…';
        api('/preview', { method: 'POST', body: JSON.stringify({ media_type: ed.media_type, kind: ed.kind, definition: ed.definition }) })
            .then(function (d) {
                if (!d || d.ok === false) {
                    nEl.textContent = '—';
                    lEl.textContent = (d && d.error) ? d.error : 'add a rule to preview';
                    missEl.textContent = '';
                    grid.innerHTML = '';
                    return;
                }
                nEl.textContent = d.count;
                lEl.textContent = 'owned ' + mediaWord(ed.media_type) + ' match';
                missEl.textContent = d.missing_count ? ('+' + d.missing_count + ' missing') : '';
                grid.innerHTML = (d.sample || []).map(function (m) {
                    var img = m.has_poster ? '<img src="' + memberPosterURL(ed.media_type, m.id) + '" alt="" loading="lazy" onerror="this.style.display=\'none\'">' : '';
                    return '<div class="vce-pv" title="' + esc(m.title || '') + '">' + img + '<span class="vce-pv-fallback">' + esc((m.title || '?').slice(0, 2)) + '</span></div>';
                }).join('');
            })
            .catch(function () { lEl.textContent = 'preview failed'; });
    }

    // ── save / sync / delete ─────────────────────────────────────────────────
    function payload() {
        return {
            name: ed.name || 'Untitled collection', kind: ed.kind, media_type: ed.media_type,
            definition: ed.definition, summary: ed.summary, sort_order: ed.sort_order,
            sync_mode: ed.sync_mode, pinned: !!ed.pinned, wishlist_missing: !!ed.wishlist_missing,
            enabled: !!ed.enabled, poster_url: ed.poster_url
        };
    }
    function save(btn) {
        if (btn) { btn.disabled = true; btn.textContent = 'Saving…'; }
        var p = payload();
        var req = ed.id
            ? api('/' + ed.id, { method: 'PUT', body: JSON.stringify(p) }).then(function () { return { id: ed.id }; })
            : api('', { method: 'POST', body: JSON.stringify(p) });
        req.then(function (d) {
            if (d && (d.id || d.ok !== false)) {
                if (!ed.id && d.id) ed.id = d.id;
                ed.dirty = false;
                toast('Saved');
                renderEditor();   // re-render so Sync/Delete/Generate enable
            } else { toast((d && d.error) || 'Save failed', true); }
        }).catch(function () { toast('Save failed', true); })
          .finally(function () { if (btn) { btn.disabled = false; btn.textContent = 'Save'; } });
    }
    function syncOne(id, btn, done) {
        var lbl = btn ? btn.innerHTML : null;
        if (btn) { btn.disabled = true; btn.textContent = 'Syncing…'; }
        api('/' + id + '/sync', { method: 'POST' }).then(function (d) {
            if (d && d.ok) {
                var msg = d.skipped ? 'Already up to date'
                    : ('Synced' + (d.added ? ' +' + d.added : '') + (d.removed ? ' −' + d.removed : '') +
                       (d.total != null ? ' · ' + d.total + ' items' : '') +
                       (d.wishlisted ? ' · ' + d.wishlisted + ' wishlisted' : ''));
                toast(msg);
                if (done) done();
            } else { toast((d && d.error) || 'Sync failed', true); }
        }).catch(function () { toast('Sync failed', true); })
          .finally(function () { if (btn) { btn.disabled = false; if (lbl != null) btn.innerHTML = lbl; } });
    }
    function delCollection(id, name, backToGallery) {
        if (!window.confirm('Delete collection "' + (name || '') + '"? (The server collection is left in place.)')) return;
        api('/' + id, { method: 'DELETE' }).then(function () {
            toast('Deleted');
            if (ed) ed.dirty = false;
            if (backToGallery || view === 'gallery') showGallery(); else refreshGallery();
        });
    }

    function toast(msg, isErr) {
        var t = h('div', 'vce-toast' + (isErr ? ' vce-toast--err' : ''), esc(msg));
        overlay.appendChild(t);
        requestAnimationFrame(function () { t.classList.add('vce-toast--on'); });
        setTimeout(function () { t.classList.remove('vce-toast--on'); setTimeout(function () { if (t.parentNode) t.parentNode.removeChild(t); }, 250); }, 2600);
    }

    document.addEventListener('keydown', function (e) {
        if (e.key !== 'Escape' || !overlay || !overlay.classList.contains('vce-overlay--on')) return;
        if (view === 'editor') leaveEditor();
        else if (view === 'picker') showPresets();
        else if (view === 'presets' || view === 'server') showGallery();
        else close();
    });

    window.VideoCollectionEditor = { open: open, close: close };
})();
