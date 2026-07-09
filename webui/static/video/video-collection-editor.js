/*
 * SoulSync — Collection Studio.
 *
 * A full-bleed pseudo-page (like the Overlay Studio) for building SoulSync-
 * managed movie/show collections: a smart-filter rule builder or a list/franchise
 * source, with a live owned-items preview, that syncs to Plex/Jellyfin.
 *
 * ISOLATION: self-contained IIFE under static/video/. Exposes only
 * window.VideoCollectionEditor = { open, close }. Talks to /api/video/collections.
 */
(function () {
    'use strict';

    var API = '/api/video/collections';
    var overlay = null;          // the .vce-overlay root (lazily built, on body)
    var fieldCache = {};         // media_type -> {fields, suggestions}
    var ed = null;               // current editor state

    // ── tiny helpers ──────────────────────────────────────────────────────────
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
        return fetch(API + path, opts).then(function (r) { return r.ok || r.status === 404 ? r.json() : r.json().catch(function () { return { ok: false, error: 'HTTP ' + r.status }; }); });
    }
    var _t;
    function debounce(fn, ms) {
        return function () { clearTimeout(_t); _t = setTimeout(fn, ms); };
    }
    function posterURL(mediaType, id) {
        return '/api/video/poster/' + (mediaType === 'show' ? 'show' : 'movie') + '/' + id + '?w=140';
    }

    // ── open / close ──────────────────────────────────────────────────────────
    function ensureOverlay() {
        if (overlay) return overlay;
        overlay = h('div', 'vce-overlay');
        document.body.appendChild(overlay);
        overlay.addEventListener('click', function (e) {
            if (e.target === overlay) close();
        });
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
    }

    function shell(title, sub) {
        overlay.innerHTML = '';
        var wrap = h('div', 'vce-shell');
        var head = h('div', 'vce-head');
        head.innerHTML =
            '<div class="vce-head-titles"><h1 class="vce-title">' + esc(title) + '</h1>' +
            (sub ? '<p class="vce-sub">' + esc(sub) + '</p>' : '') + '</div>';
        var x = h('button', 'vce-close', '&times;');
        x.type = 'button';
        x.setAttribute('aria-label', 'Close');
        x.addEventListener('click', close);
        head.appendChild(x);
        wrap.appendChild(head);
        var body = h('div', 'vce-body');
        wrap.appendChild(body);
        overlay.appendChild(wrap);
        return body;
    }

    // ── gallery ───────────────────────────────────────────────────────────────
    function showGallery() {
        var body = shell('Collection Studio', 'Build smart or list-based collections and sync them to your server');
        body.innerHTML = '<div class="vce-loading">Loading…</div>';
        api('', {}).then(function (d) {
            var cols = (d && d.collections) || [];
            body.innerHTML = '';
            var grid = h('div', 'vce-gallery');

            var add = h('button', 'vce-card vce-card--new',
                '<div class="vce-card-plus">+</div><div class="vce-card-newlabel">New collection</div>');
            add.type = 'button';
            add.addEventListener('click', function () { newCollection(); });
            grid.appendChild(add);

            cols.forEach(function (c) {
                var card = h('div', 'vce-card');
                var kindLabel = c.kind === 'list' ? 'List' : 'Smart';
                var count = (c.member_count == null) ? 'not synced' : (c.member_count + ' item' + (c.member_count === 1 ? '' : 's'));
                card.innerHTML =
                    '<div class="vce-card-thumb"' + (c.poster_url ? ' style="background-image:url(\'' + esc(c.poster_url) + '\')"' : '') + '>' +
                        '<span class="vce-card-kind">' + kindLabel + '</span>' +
                        (c.enabled ? '' : '<span class="vce-card-off">disabled</span>') +
                    '</div>' +
                    '<div class="vce-card-name">' + esc(c.name) + '</div>' +
                    '<div class="vce-card-meta">' + esc((c.media_type === 'show' ? 'Shows' : 'Movies')) + ' · ' + esc(count) + '</div>' +
                    '<div class="vce-card-actions">' +
                        '<button type="button" class="vce-mini" data-act="edit">Edit</button>' +
                        '<button type="button" class="vce-mini" data-act="sync">Sync</button>' +
                        '<button type="button" class="vce-mini vce-mini--danger" data-act="del">Delete</button>' +
                    '</div>';
                card.querySelector('[data-act="edit"]').addEventListener('click', function () { loadEditor(c.id); });
                card.querySelector('[data-act="sync"]').addEventListener('click', function (e) { syncOne(c.id, e.target); });
                card.querySelector('[data-act="del"]').addEventListener('click', function () { delCollection(c.id, c.name); });
                grid.appendChild(card);
            });
            body.appendChild(grid);
        });
    }

    function newCollection() {
        ed = { id: null, name: '', kind: 'smart', media_type: 'movie',
               definition: { match: 'all', rules: [] },
               summary: '', sort_order: 'release', sync_mode: 'sync',
               pinned: false, wishlist_missing: false, enabled: true, poster_url: '' };
        renderEditor();
    }

    function loadEditor(id) {
        ensureOverlay();
        overlay.classList.add('vce-overlay--on');
        var body = shell('Collection', 'Loading…');
        body.innerHTML = '<div class="vce-loading">Loading…</div>';
        api('/' + id, {}).then(function (d) {
            var c = d && d.collection;
            if (!c) { body.innerHTML = '<div class="vce-loading">Not found.</div>'; return; }
            ed = {
                id: c.id, name: c.name || '', kind: c.kind || 'smart',
                media_type: c.media_type || 'movie',
                definition: c.definition && Object.keys(c.definition).length ? c.definition : { match: 'all', rules: [] },
                summary: c.summary || '', sort_order: c.sort_order || 'release',
                sync_mode: c.sync_mode || 'sync', pinned: !!c.pinned,
                wishlist_missing: !!c.wishlist_missing, enabled: c.enabled == null ? true : !!c.enabled,
                poster_url: c.poster_url || ''
            };
            renderEditor();
        });
    }

    // ── editor ────────────────────────────────────────────────────────────────
    function renderEditor() {
        var body = shell(ed.id ? 'Edit collection' : 'New collection',
                         'Smart rules or a list source, previewed live against your library');
        body.innerHTML = '';
        var cols = h('div', 'vce-editor');

        // left: builder
        var left = h('div', 'vce-build');
        left.innerHTML =
            '<label class="vce-flabel">Name</label>' +
            '<input class="vce-input" data-f="name" value="' + esc(ed.name) + '" placeholder="e.g. 80s Action">' +
            '<div class="vce-row2">' +
                '<div><label class="vce-flabel">Library</label>' +
                    sel('media_type', ed.media_type, [['movie', 'Movies'], ['show', 'Shows']]) + '</div>' +
                '<div><label class="vce-flabel">Builder</label>' +
                    sel('kind', ed.kind, [['smart', 'Smart filter'], ['list', 'List / franchise']]) + '</div>' +
            '</div>' +
            '<div class="vce-builder" data-builder></div>';
        cols.appendChild(left);

        // right: preview + settings
        var right = h('div', 'vce-side');
        right.innerHTML =
            '<div class="vce-preview" data-preview><div class="vce-preview-count">Preview</div>' +
            '<div class="vce-preview-grid" data-preview-grid></div></div>' +
            '<div class="vce-settings">' +
                '<label class="vce-flabel">Summary</label>' +
                '<textarea class="vce-input" data-f="summary" rows="2" placeholder="Optional description">' + esc(ed.summary) + '</textarea>' +
                '<div class="vce-row2">' +
                    '<div><label class="vce-flabel">Sort</label>' +
                        sel('sort_order', ed.sort_order, [['release', 'Release date'], ['alpha', 'A → Z'], ['rating', 'Rating'], ['added', 'Date added'], ['custom', 'Custom']]) + '</div>' +
                    '<div><label class="vce-flabel">Sync mode</label>' +
                        sel('sync_mode', ed.sync_mode, [['sync', 'Sync (add + remove)'], ['append', 'Append (add only)']]) + '</div>' +
                '</div>' +
                '<label class="vce-flabel">Poster URL</label>' +
                '<input class="vce-input" data-f="poster_url" value="' + esc(ed.poster_url) + '" placeholder="Optional image URL">' +
                '<label class="vce-check"><input type="checkbox" data-f="pinned"' + (ed.pinned ? ' checked' : '') + '> Pin to home</label>' +
                '<label class="vce-check" data-wishlist-row><input type="checkbox" data-f="wishlist_missing"' + (ed.wishlist_missing ? ' checked' : '') + '> Wishlist members I don\'t own</label>' +
                '<label class="vce-check"><input type="checkbox" data-f="enabled"' + (ed.enabled ? ' checked' : '') + '> Include in daily sync</label>' +
            '</div>' +
            '<div class="vce-actions">' +
                '<button type="button" class="vce-btn vce-btn--primary" data-act="save">Save</button>' +
                '<button type="button" class="vce-btn" data-act="sync"' + (ed.id ? '' : ' disabled title="Save first"') + '>Sync now</button>' +
                (ed.id ? '<button type="button" class="vce-btn vce-btn--danger" data-act="del">Delete</button>' : '') +
                '<button type="button" class="vce-btn vce-btn--ghost" data-act="back">Back</button>' +
            '</div>';
        cols.appendChild(right);
        body.appendChild(cols);

        // wire simple field bindings
        cols.querySelectorAll('[data-f]').forEach(function (inp) {
            var f = inp.getAttribute('data-f');
            inp.addEventListener(inp.type === 'checkbox' ? 'change' : 'input', function () {
                ed[f] = inp.type === 'checkbox' ? inp.checked : inp.value;
                if (f === 'media_type') { ed.definition = { match: ed.definition.match || 'all', rules: [] }; renderBuilder(); schedulePreview(); }
                if (f === 'kind') { renderBuilder(); schedulePreview(); }
                if (f === 'wishlist_missing') { /* no-op */ }
            });
        });
        cols.querySelector('[data-act="save"]').addEventListener('click', function (e) { save(e.target); });
        cols.querySelector('[data-act="back"]').addEventListener('click', showGallery);
        var syncBtn = cols.querySelector('[data-act="sync"]');
        if (syncBtn) syncBtn.addEventListener('click', function (e) { if (ed.id) syncOne(ed.id, e.target); });
        var delBtn = cols.querySelector('[data-act="del"]');
        if (delBtn) delBtn.addEventListener('click', function () { delCollection(ed.id, ed.name, true); });

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

    // ── builder (smart rules OR list source) ──────────────────────────────────
    function renderBuilder() {
        toggleWishlistRow();
        var host = overlay.querySelector('[data-builder]');
        if (!host) return;
        if (ed.kind === 'list') { renderListBuilder(host); return; }
        // smart
        ensureFields(ed.media_type).then(function () { renderSmartBuilder(host); });
    }

    function ensureFields(mt) {
        if (fieldCache[mt]) return Promise.resolve(fieldCache[mt]);
        return api('/fields?media_type=' + mt, {}).then(function (d) { fieldCache[mt] = d || { fields: [], suggestions: {} }; return fieldCache[mt]; });
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
            '<button type="button" class="vce-addrule" data-addrule>+ Add rule</button>';
        host.querySelector('[data-match]').addEventListener('change', function (e) { def.match = e.target.value; schedulePreview(); });
        host.querySelector('[data-addrule]').addEventListener('click', function () {
            def.rules.push({ field: meta.fields[0] ? meta.fields[0].field : 'year', op: '', value: '' });
            paintRules(); schedulePreview();
        });
        paintRules();
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
            rm.addEventListener('click', function () { ed.definition.rules.splice(i, 1); paintRules(); schedulePreview(); });
            row.appendChild(rm);
            host.appendChild(row);
        });
    }

    function mkFieldSelect(meta, rule, i) {
        var s = h('select', 'vce-input vce-rule-field');
        s.innerHTML = meta.fields.map(function (f) {
            return '<option value="' + f.field + '"' + (f.field === rule.field ? ' selected' : '') + '>' + esc(f.label) + '</option>';
        }).join('');
        s.addEventListener('change', function () { rule.field = s.value; rule.op = ''; rule.value = ''; paintRules(); schedulePreview(); });
        return s;
    }
    function mkOpSelect(spec, rule, i) {
        var s = h('select', 'vce-input vce-rule-op');
        s.innerHTML = spec.ops.map(function (o) {
            return '<option value="' + o + '"' + (o === rule.op ? ' selected' : '') + '>' + esc(OP_LABELS[o] || o) + '</option>';
        }).join('');
        s.addEventListener('change', function () { rule.op = s.value; paintRules(); schedulePreview(); });
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
            function upd() { rule.value = [ins[0].value, ins[1].value]; schedulePreview(); }
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
        // datalist of suggestions/options
        var opts = spec.options || (fieldCache[ed.media_type].suggestions && fieldCache[ed.media_type].suggestions[rule.field]);
        if (opts && opts.length) {
            var dlid = 'vce-dl-' + rule.field + '-' + i;
            inp.setAttribute('list', dlid);
            var dl = h('datalist'); dl.id = dlid;
            dl.innerHTML = opts.map(function (o) { return '<option value="' + esc(o) + '">'; }).join('');
            wrap.appendChild(dl);
        }
        inp.addEventListener('input', function () { rule.value = inp.value; schedulePreview(); });
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
            renderListBuilder(host);   // relabel the reference input
            schedulePreview();
        });
        host.querySelector('[data-listref]').addEventListener('input', function (e) {
            var v = e.target.value.trim();
            delete def.collection_id; delete def.list_id; delete def.url;
            if (def.source === 'tmdb_collection') def.collection_id = v ? parseInt(v, 10) : null;
            else if (def.source === 'trakt_list') def.url = v;
            else def.list_id = v;
            schedulePreview();
        });
    }

    // ── live preview ──────────────────────────────────────────────────────────
    var schedulePreview = debounce(runPreview, 350);
    function runPreview() {
        var grid = overlay.querySelector('[data-preview-grid]');
        var countEl = overlay.querySelector('.vce-preview-count');
        if (!grid || !countEl) return;
        countEl.textContent = 'Previewing…';
        api('/preview', { method: 'POST', body: JSON.stringify({ media_type: ed.media_type, kind: ed.kind, definition: ed.definition }) })
            .then(function (d) {
                if (!d || d.ok === false) { countEl.textContent = (d && d.error) ? d.error : 'Add a rule to preview'; grid.innerHTML = ''; return; }
                var extra = d.missing_count ? (' · ' + d.missing_count + ' missing') : '';
                countEl.textContent = d.count + ' item' + (d.count === 1 ? '' : 's') + ' match' + extra;
                grid.innerHTML = (d.sample || []).map(function (m) {
                    var img = m.has_poster ? '<img src="' + posterURL(ed.media_type, m.id) + '" alt="" loading="lazy" onerror="this.style.display=\'none\'">' : '';
                    return '<div class="vce-pv" title="' + esc(m.title || '') + '">' + img + '<span class="vce-pv-fallback">' + esc((m.title || '?').slice(0, 2)) + '</span></div>';
                }).join('');
            })
            .catch(function () { countEl.textContent = 'Preview failed'; });
    }

    // ── save / sync / delete ──────────────────────────────────────────────────
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
                toast('Saved');
                renderEditor();   // re-render so Sync/Delete enable
            } else { toast((d && d.error) || 'Save failed', true); }
        }).catch(function () { toast('Save failed', true); })
          .finally(function () { if (btn) { btn.disabled = false; btn.textContent = 'Save'; } });
    }
    function syncOne(id, btn) {
        var lbl = btn ? btn.textContent : null;
        if (btn) { btn.disabled = true; btn.textContent = 'Syncing…'; }
        api('/' + id + '/sync', { method: 'POST' }).then(function (d) {
            if (d && d.ok) {
                var msg = d.skipped ? 'Up to date' : ('Synced' + (d.added ? ' +' + d.added : '') + (d.removed ? ' -' + d.removed : ''));
                toast(msg);
            } else { toast((d && d.error) || 'Sync failed', true); }
        }).catch(function () { toast('Sync failed', true); })
          .finally(function () { if (btn) { btn.disabled = false; btn.textContent = lbl || 'Sync'; } });
    }
    function delCollection(id, name, backToGallery) {
        if (!window.confirm('Delete collection "' + (name || '') + '"? (The server collection is left in place.)')) return;
        api('/' + id, { method: 'DELETE' }).then(function () {
            toast('Deleted');
            showGallery();
        });
    }

    function toast(msg, isErr) {
        var t = h('div', 'vce-toast' + (isErr ? ' vce-toast--err' : ''), esc(msg));
        overlay.appendChild(t);
        requestAnimationFrame(function () { t.classList.add('vce-toast--on'); });
        setTimeout(function () { t.classList.remove('vce-toast--on'); setTimeout(function () { if (t.parentNode) t.parentNode.removeChild(t); }, 250); }, 1800);
    }

    document.addEventListener('keydown', function (e) {
        if (e.key === 'Escape' && overlay && overlay.classList.contains('vce-overlay--on')) close();
    });

    window.VideoCollectionEditor = { open: open, close: close };
})();
