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
    };

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
        loadGallery();
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
                        (t.thumbnail ? '<img src="' + esc(t.thumbnail) + '" alt="">' : '<span class="voe-card-empty-ic">🎬</span>') +
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
        grid.querySelector('[data-voe-new]').addEventListener('click', createTemplate);
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

    function createTemplate() {
        var def = { version: 1, canvas: { aspect: '2:3' }, layers: [] };
        api('POST', '/api/video/overlays/templates', { name: 'Untitled template', definition: def })
            .then(function (d) { if (d && d.id) loadTemplate(d.id); })
            .catch(function () { toast('Could not create template', 'error'); });
    }

    function loadTemplate(id) {
        api('GET', '/api/video/overlays/templates/' + id).then(function (t) {
            var def = t.definition || {};
            ed = {
                id: t.id, name: t.name || 'Untitled template',
                layers: (def.layers || []).map(normalizeLayer),
                selected: null, dirty: false,
                stage: null, W: 0, H: 0,
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
        if (l.type === 'text') {
            if (l.text == null) l.text = 'Text';
            if (typeof l.size !== 'number') l.size = 0.06;
            l.color = l.color || '#ffffff';
            l.font = l.font || 'Inter';
            if (typeof l.weight !== 'number') l.weight = 800;
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
                '<div class="voe-top-spacer"></div>' +
                '<span class="voe-save-state" data-voe-savestate></span>' +
                '<button class="voe-btn voe-btn--primary" data-voe-save>' + I.save + ' Save</button>' +
                '<button class="voe-x" data-voe-close aria-label="Close">&times;</button>' +
            '</div>' +
            '<div class="voe-editor">' +
                '<div class="voe-palette">' + paletteHTML() + '</div>' +
                '<div class="voe-canvas-wrap" data-voe-canvaswrap>' +
                    '<div class="voe-stage" data-voe-stage>' +
                        '<div class="voe-stage-ph" data-voe-ph>Drag elements from the left onto the poster.<br>This background is just a preview — only the overlay is saved.</div>' +
                        '<div class="voe-drop-hint">Drop to add</div>' +
                    '</div>' +
                '</div>' +
                '<div class="voe-side">' +
                    '<div class="voe-side-h"><span>Layers</span><span class="voe-side-count" data-voe-count></span></div>' +
                    '<div class="voe-layers" data-voe-layers></div>' +
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

        resizeBound = function () { measureStage(); relayoutAll(); };
        window.addEventListener('resize', resizeBound);

        measureStage();
        renderStageLayers();
        renderLayersPanel();
        updateSaveState();
    }

    function measureStage() {
        var stage = overlay && overlay.querySelector('[data-voe-stage]');
        if (!stage) return;
        var r = stage.getBoundingClientRect();
        ed.stage = stage; ed.W = r.width; ed.H = r.height;
    }

    // ── palette (1a: Text only; grows in later phases) ──────────────────────────
    function paletteHTML() {
        return '<div class="voe-pal-section"><div class="voe-pal-h">Basics</div><div class="voe-pal-grid">' +
            palItem('text', 'Text', I.text) +
            '</div></div>';
    }
    function palItem(kind, label, icon) {
        return '<div class="voe-pal-item" data-voe-add="' + kind + '" title="' + esc(label) + '">' +
            '<span class="voe-pal-ic">' + icon + '</span><span class="voe-pal-label">' + esc(label) + '</span></div>';
    }
    function wirePalette() {
        overlay.querySelectorAll('[data-voe-add]').forEach(function (it) {
            it.addEventListener('pointerdown', function (e) { startPaletteDrag(e, it); });
        });
    }

    // ── add / create layers ─────────────────────────────────────────────────────
    function defaultLayer(kind, x, y) {
        var base = { id: uid(), type: kind, anchor: 'center', x: x, y: y, hidden: false };
        if (kind === 'text') {
            base.name = 'Text'; base.text = 'New Text'; base.size = 0.06;
            base.color = '#ffffff'; base.font = 'Inter'; base.weight = 800;
        }
        return base;
    }
    function addLayer(kind, x, y) {
        var l = defaultLayer(kind, x, y);
        ed.layers.push(l);              // paint order: last = front
        ed.selected = l.id;
        markDirty();
        renderStageLayers();
        renderLayersPanel();
        return l;
    }

    // ── palette drag → drop onto the stage ──────────────────────────────────────
    function startPaletteDrag(e, item) {
        e.preventDefault();
        var kind = item.getAttribute('data-voe-add');
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
            if (!dragging) { addLayer(kind, 0.5, 0.5); return; }   // a click → add centered
            if (overStage(ev)) {
                var r = stage.getBoundingClientRect();
                addLayer(kind, clamp01((ev.clientX - r.left) / r.width), clamp01((ev.clientY - r.top) / r.height));
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
        if (ph) ph.style.display = ed.layers.length ? 'none' : '';
        ed.layers.forEach(function (l) {
            var el = document.createElement('div');
            el.className = 'voe-layer voe-layer--' + l.type + (l.id === ed.selected ? ' voe-layer--sel' : '') + (l.hidden ? ' voe-layer--hidden' : '');
            el.setAttribute('data-voe-layer', l.id);
            styleLayerEl(el, l);
            stage.appendChild(el);
            layoutLayer(el, l);
        });
    }

    function styleLayerEl(el, l) {
        if (l.type === 'text') {
            el.classList.add('voe-layer-text');
            el.textContent = l.text || '';
            el.style.color = l.color;
            el.style.fontFamily = fontStack(l.font);
            el.style.fontWeight = l.weight;
            el.style.fontSize = (l.size * ed.H) + 'px';
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
            l.x = clamp01(startX + (ev.clientX - px) / r.width);
            l.y = clamp01(startY + (ev.clientY - py) / r.height);
            layoutLayer(node, l);
        }
        function up() {
            document.removeEventListener('pointermove', move);
            document.removeEventListener('pointerup', up);
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
            if (txt !== l.text) { l.text = txt || ' '; markDirty(); renderLayersPanel(); }
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
    }

    // ── layers panel (scene list) ───────────────────────────────────────────────
    function layerIcon(l) { return l.type === 'text' ? I.text : I.text; }
    function layerName(l) { return l.name || (l.type === 'text' ? (l.text || 'Text') : l.type); }

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
                    '<button class="voe-lr-btn" data-voe-rmlayer title="Delete layer">' + I.trash + '</button>' +
                '</div>');
        }
        box.innerHTML = rows.join('');
        box.querySelectorAll('[data-voe-row]').forEach(function (row) {
            var id = row.getAttribute('data-voe-row');
            row.addEventListener('click', function (e) {
                if (e.target.closest('[data-voe-vis],[data-voe-rmlayer],[data-voe-grip]')) return;
                select(id);
            });
            row.querySelector('[data-voe-vis]').addEventListener('click', function (e) { e.stopPropagation(); toggleHidden(id); });
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
        markDirty(); renderStageLayers(); renderLayersPanel();
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

    // ── dirty + save ────────────────────────────────────────────────────────────
    function markDirty() { if (ed) { ed.dirty = true; updateSaveState(); } }
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
    // Esc closes (unless editing text / a confirm is up)
    document.addEventListener('keydown', function (e) {
        if (e.key !== 'Escape' || !overlay || !overlay.classList.contains('voe-overlay--on')) return;
        if (document.querySelector('.voe-confirm-back')) return;
        if (document.activeElement && document.activeElement.getAttribute('contenteditable') === 'true') return;
        if (document.activeElement && document.activeElement.classList.contains('voe-name-input')) return;
        close();
    });

    window.VideoOverlayEditor = { open: open, close: close };
})();
