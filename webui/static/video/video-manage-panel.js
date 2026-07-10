/*
 * SoulSync — Manage panel (per-item metadata editor).
 *
 *   VideoManage.open({kind, id})  — from "Manage" on a movie/show detail page.
 *
 * A right-hand slide-over: title / sort title / year / content rating / genres /
 * tagline / summary, plus watched + monitored toggles and a poster shortcut.
 * Saving PUTs /api/video/detail/<kind>/<id>/metadata — the edit is written
 * locally, pushed to Plex/Jellyfin (with the server's own field locks set) and
 * LOCKED here: scans and metadata refreshes won't overwrite it. Locked fields
 * wear a small badge; clicking it releases the field back to the server.
 * Self-contained (own styles), mirrors the poster-manager module pattern.
 */
(function () {
    'use strict';

    function esc(s) {
        return String(s == null ? '' : s)
            .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
    }
    function toast(msg, type) { if (typeof showToast === 'function') showToast(msg, type); }
    function confirmDlg(opts) {
        if (typeof showConfirmDialog === 'function') return showConfirmDialog(opts);
        return Promise.resolve(true);   // headless fallback (never window.confirm)
    }

    var RATING_HINTS = {
        movie: ['G', 'PG', 'PG-13', 'R', 'NC-17', 'NR'],
        show: ['TV-Y', 'TV-Y7', 'TV-G', 'TV-PG', 'TV-14', 'TV-MA'],
    };
    var LOCK_TIP = 'Yours — scans and metadata refreshes won’t change it. Click to release.';

    // ── one-time styles ──────────────────────────────────────────────────────
    function ensureStyles() {
        if (document.getElementById('vmg-styles')) return;
        var A = 'var(--accent-rgb, 88 101 242)';
        var css =
            '.vmg-overlay{position:fixed;inset:0;z-index:9100;background:rgba(5,5,8,.55);backdrop-filter:blur(4px);' +
                'opacity:0;transition:opacity .22s ease;}' +
            '.vmg-overlay.vmg-open{opacity:1;}' +
            '.vmg-panel{position:absolute;top:0;right:0;bottom:0;width:min(430px,calc(100vw - 20px));display:flex;' +
                'flex-direction:column;background:#101015;border-left:1px solid rgba(255,255,255,.09);' +
                'box-shadow:-40px 0 110px rgba(0,0,0,.6);transform:translateX(26px);opacity:.6;' +
                'transition:transform .26s cubic-bezier(.2,.7,.2,1),opacity .2s ease;}' +
            '.vmg-open .vmg-panel{transform:none;opacity:1;}' +
            // header
            '.vmg-head{padding:22px 24px 16px;border-bottom:1px solid rgba(255,255,255,.07);position:relative;}' +
            '.vmg-kick{display:flex;align-items:center;gap:8px;font-size:11px;font-weight:800;text-transform:uppercase;' +
                'letter-spacing:.09em;color:rgb(' + A + ');}' +
            '.vmg-kick-dot{width:7px;height:7px;border-radius:50%;background:rgb(' + A + ');box-shadow:0 0 10px rgb(' + A + ');}' +
            '.vmg-title{font-size:20px;font-weight:900;letter-spacing:-.02em;color:#fff;margin:8px 0 2px;' +
                'white-space:nowrap;overflow:hidden;text-overflow:ellipsis;padding-right:150px;}' +
            '.vmg-sub{font-size:12.5px;color:rgba(255,255,255,.5);line-height:1.5;}' +
            '.vmg-close{position:absolute;top:18px;right:18px;width:34px;height:34px;border-radius:50%;' +
                'border:1px solid rgba(255,255,255,.16);background:rgba(0,0,0,.4);color:#fff;font-size:20px;line-height:1;' +
                'cursor:pointer;display:flex;align-items:center;justify-content:center;transition:all .15s;}' +
            '.vmg-close:hover{background:rgba(0,0,0,.7);border-color:rgba(255,255,255,.36);}' +
            // body
            '.vmg-body{flex:1;overflow-y:auto;padding:18px 24px 22px;display:flex;flex-direction:column;gap:16px;}' +
            '.vmg-body::-webkit-scrollbar{width:8px;}.vmg-body::-webkit-scrollbar-thumb{background:rgba(255,255,255,.12);border-radius:4px;}' +
            '.vmg-sect{font-size:11px;font-weight:800;text-transform:uppercase;letter-spacing:.07em;' +
                'color:rgba(255,255,255,.42);margin:6px 0 -8px;}' +
            '.vmg-field{display:flex;flex-direction:column;gap:6px;min-width:0;}' +
            '.vmg-label{display:flex;align-items:center;gap:8px;font-size:12px;font-weight:700;color:rgba(255,255,255,.6);}' +
            '.vmg-row2{display:grid;grid-template-columns:1fr 1fr;gap:12px;}' +
            '.vmg-input,.vmg-area{width:100%;box-sizing:border-box;padding:10px 12px;border-radius:10px;font-size:13.5px;' +
                'font-family:inherit;background:rgba(255,255,255,.05);border:1px solid rgba(255,255,255,.1);color:#eef1f7;' +
                'outline:none;transition:border .15s,box-shadow .15s;}' +
            '.vmg-input:focus,.vmg-area:focus{border-color:rgba(' + A + ',.6);box-shadow:0 0 0 3px rgba(' + A + ',.14);}' +
            '.vmg-area{resize:vertical;min-height:104px;line-height:1.55;}' +
            // lock badge
            '.vmg-lock{display:inline-flex;align-items:center;gap:5px;padding:2px 9px;border-radius:999px;cursor:pointer;' +
                'font-size:9.5px;font-weight:800;text-transform:uppercase;letter-spacing:.05em;border:1px solid rgba(' + A + ',.45);' +
                'background:rgba(' + A + ',.16);color:rgb(' + A + ');transition:all .13s;}' +
            '.vmg-lock:hover{background:rgba(' + A + ',.3);}' +
            '.vmg-lock svg{width:9px;height:11px;fill:currentColor;}' +
            // genres
            '.vmg-chips{display:flex;flex-wrap:wrap;gap:7px;align-items:center;padding:9px 10px;border-radius:10px;' +
                'background:rgba(255,255,255,.05);border:1px solid rgba(255,255,255,.1);min-height:22px;}' +
            '.vmg-chips:focus-within{border-color:rgba(' + A + ',.6);box-shadow:0 0 0 3px rgba(' + A + ',.14);}' +
            '.vmg-chip{display:inline-flex;align-items:center;gap:6px;padding:3px 10px;border-radius:999px;font-size:12px;' +
                'font-weight:700;background:rgba(' + A + ',.16);border:1px solid rgba(' + A + ',.4);color:#eef1f7;}' +
            '.vmg-chip button{all:unset;cursor:pointer;font-size:13px;line-height:1;color:rgba(255,255,255,.55);}' +
            '.vmg-chip button:hover{color:#fff;}' +
            '.vmg-chip-in{flex:1;min-width:90px;background:none;border:none;outline:none;color:#eef1f7;' +
                'font-size:12.5px;font-family:inherit;padding:3px 2px;}' +
            // poster + toggles
            '.vmg-poster-row{display:flex;align-items:center;gap:14px;padding:12px;border-radius:12px;' +
                'background:rgba(255,255,255,.035);border:1px solid rgba(255,255,255,.07);}' +
            '.vmg-poster-img{width:52px;aspect-ratio:2/3;border-radius:7px;object-fit:cover;background:#1b1b22;flex:0 0 auto;}' +
            '.vmg-poster-txt{flex:1;min-width:0;font-size:12.5px;color:rgba(255,255,255,.55);line-height:1.45;}' +
            '.vmg-btn-ghost{padding:8px 14px;border-radius:10px;font-size:12.5px;font-weight:700;cursor:pointer;' +
                'background:rgba(255,255,255,.07);border:1px solid rgba(255,255,255,.14);color:#eef1f7;transition:all .13s;white-space:nowrap;}' +
            '.vmg-btn-ghost:hover{background:rgba(255,255,255,.13);}' +
            '.vmg-toggles{display:grid;grid-template-columns:1fr 1fr;gap:12px;}' +
            '.vmg-toggle{display:flex;align-items:center;justify-content:space-between;gap:10px;padding:11px 13px;' +
                'border-radius:12px;background:rgba(255,255,255,.035);border:1px solid rgba(255,255,255,.07);cursor:pointer;' +
                'font-size:13px;font-weight:700;color:#eef1f7;transition:border .13s;}' +
            '.vmg-toggle:hover{border-color:rgba(255,255,255,.16);}' +
            '.vmg-sw{position:relative;width:34px;height:19px;border-radius:999px;background:rgba(255,255,255,.14);' +
                'transition:background .16s;flex:0 0 auto;}' +
            '.vmg-sw::after{content:"";position:absolute;top:2px;left:2px;width:15px;height:15px;border-radius:50%;' +
                'background:#fff;transition:transform .16s;}' +
            '.vmg-toggle--on .vmg-sw{background:rgb(' + A + ');}' +
            '.vmg-toggle--on .vmg-sw::after{transform:translateX(15px);}' +
            // save (in the header, clear of the app's floating bell/help orbs)
            '.vmg-hint{font-size:11.5px;color:rgba(255,255,255,.4);line-height:1.5;' +
                'padding:2px 0 30px;}' +
            '.vmg-save{position:absolute;top:18px;right:60px;padding:8px 18px;border-radius:999px;' +
                'font-size:12.5px;font-weight:800;cursor:pointer;border:none;' +
                'background:rgb(' + A + ');color:#fff;box-shadow:0 6px 18px rgba(' + A + ',.35);transition:all .15s;}' +
            '.vmg-save:hover:not(:disabled){filter:brightness(1.12);}' +
            '.vmg-save:disabled{opacity:.38;cursor:default;box-shadow:none;}' +
            '@media (prefers-reduced-motion: reduce){.vmg-overlay,.vmg-panel{transition:none;}}';
        var el = document.createElement('style');
        el.id = 'vmg-styles';
        el.textContent = css;
        document.head.appendChild(el);
    }

    // ── state ────────────────────────────────────────────────────────────────
    var state = null;   // {kind, id, data, genres[], locked{}, overlay, saving}

    function lockSvg() {
        return '<svg viewBox="0 0 10 12"><path d="M5 0a3 3 0 0 0-3 3v2H1v7h8V5H8V3a3 3 0 0 0-3-3zm0 1.4c.9 0 1.6.7 1.6 1.6v2H3.4V3c0-.9.7-1.6 1.6-1.6z"/></svg>';
    }

    function lockBadge(field) {
        if (!state || state.locked.indexOf(field) === -1) return '';
        return '<span class="vmg-lock" data-vmg-release="' + esc(field) + '" title="' + esc(LOCK_TIP) + '">' +
            lockSvg() + 'yours</span>';
    }

    function fieldHtml(field, label, control) {
        return '<div class="vmg-field"><div class="vmg-label"><span>' + esc(label) + '</span>' +
            lockBadge(field) + '</div>' + control + '</div>';
    }

    function inputHtml(field, value, extra) {
        return '<input class="vmg-input" data-vmg-f="' + esc(field) + '" value="' + esc(value == null ? '' : value) +
            '"' + (extra || '') + '>';
    }

    // ── panel ────────────────────────────────────────────────────────────────
    function bodyHtml(d) {
        var isShow = d.kind === 'show';
        var brandField = isShow ? 'network' : 'studio';
        var ratings = RATING_HINTS[d.kind] || [];
        var dl = '<datalist id="vmg-ratings">' + ratings.map(function (r) {
            return '<option value="' + esc(r) + '">';
        }).join('') + '</datalist>';
        var posterSrc = d.has_poster ? '/api/video/poster/' + d.kind + '/' + d.id : '';
        return (
            '<div class="vmg-sect">Identity</div>' +
            fieldHtml('title', 'Title', inputHtml('title', d.title)) +
            fieldHtml('sort_title', 'Sort title', inputHtml('sort_title', d.sort_title,
                ' placeholder="derived from title"')) +
            '<div class="vmg-row2">' +
                fieldHtml('year', 'Year', inputHtml('year', d.year, ' inputmode="numeric"')) +
                fieldHtml('content_rating', 'Content rating',
                    inputHtml('content_rating', d.content_rating, ' list="vmg-ratings"')) + dl +
            '</div>' +
            fieldHtml(brandField, isShow ? 'Network' : 'Studio', inputHtml(brandField, d[brandField])) +
            fieldHtml('genres', 'Genres',
                '<div class="vmg-chips" data-vmg-chips>' +
                    '<input class="vmg-chip-in" data-vmg-chip-in list="vmg-genre-dl" placeholder="Add genre…">' +
                '</div><datalist id="vmg-genre-dl"></datalist>') +
            '<div class="vmg-sect">Story</div>' +
            fieldHtml('tagline', 'Tagline', inputHtml('tagline', d.tagline)) +
            fieldHtml('overview', 'Summary',
                '<textarea class="vmg-area" data-vmg-f="overview">' + esc(d.overview) + '</textarea>') +
            '<div class="vmg-sect">Artwork &amp; state</div>' +
            '<div class="vmg-poster-row">' +
                (posterSrc ? '<img class="vmg-poster-img" src="' + esc(posterSrc) + '" alt="">'
                           : '<div class="vmg-poster-img"></div>') +
                '<div class="vmg-poster-txt">Posters flow through the Poster Manager — picked art is pushed to the server and kept.</div>' +
                (d.tmdb_id && window.VideoPoster
                    ? '<button class="vmg-btn-ghost" type="button" data-vmg-poster>Change…</button>' : '') +
            '</div>' +
            '<div class="vmg-toggles">' +
                '<div class="vmg-toggle' + (d.watched ? ' vmg-toggle--on' : '') + '" data-vmg-watched role="switch" ' +
                    'aria-checked="' + (d.watched ? 'true' : 'false') + '" tabindex="0"><span>Watched</span><span class="vmg-sw"></span></div>' +
                '<div class="vmg-toggle' + (d.monitored ? ' vmg-toggle--on' : '') + '" data-vmg-monitored role="switch" ' +
                    'aria-checked="' + (d.monitored ? 'true' : 'false') + '" tabindex="0"><span>Monitored</span><span class="vmg-sw"></span></div>' +
            '</div>'
        );
    }

    function panelHtml(d) {
        return (
            '<div class="vmg-panel" role="dialog" aria-modal="true" aria-label="Manage metadata">' +
                '<div class="vmg-head">' +
                    '<div class="vmg-kick"><span class="vmg-kick-dot"></span>Manage</div>' +
                    '<div class="vmg-title">' + esc(d.title) + '</div>' +
                    '<div class="vmg-sub">Edits are saved here, pushed to your server, and locked against scans.</div>' +
                    // Save lives in the header — the app's notification/help orbs
                    // float over the bottom-right corner (z 999999, by design),
                    // so a footer button there would sit underneath them.
                    '<button class="vmg-save" type="button" data-vmg-save disabled>Save</button>' +
                    '<button class="vmg-close" type="button" data-vmg-close aria-label="Close">×</button>' +
                '</div>' +
                '<div class="vmg-body">' + bodyHtml(d) +
                    '<div class="vmg-hint">Locked fields wear a badge — click it to hand one back to the server.</div>' +
                '</div>' +
            '</div>'
        );
    }

    // ── genres chips ─────────────────────────────────────────────────────────
    function renderChips() {
        var wrap = state.overlay.querySelector('[data-vmg-chips]');
        if (!wrap) return;
        var input = wrap.querySelector('[data-vmg-chip-in]');
        wrap.querySelectorAll('.vmg-chip').forEach(function (c) { c.remove(); });
        state.genres.forEach(function (g, i) {
            var chip = document.createElement('span');
            chip.className = 'vmg-chip';
            chip.innerHTML = esc(g) + '<button type="button" aria-label="Remove ' + esc(g) + '" data-vmg-chip-rm="' + i + '">×</button>';
            wrap.insertBefore(chip, input);
        });
    }

    function addGenre(raw) {
        var g = String(raw || '').trim();
        if (!g) return;
        var dupe = state.genres.some(function (x) { return x.toLowerCase() === g.toLowerCase(); });
        if (!dupe) { state.genres.push(g); renderChips(); markDirty(); }
    }

    function loadGenreSuggestions(kind) {
        fetch('/api/video/collections/fields?media_type=' + encodeURIComponent(kind))
            .then(function (r) { return r.ok ? r.json() : null; })
            .then(function (res) {
                var names = (res && res.suggestions && res.suggestions.genre) || [];
                var dl = state && state.overlay && state.overlay.querySelector('#vmg-genre-dl');
                if (dl) dl.innerHTML = names.map(function (n) { return '<option value="' + esc(n) + '">'; }).join('');
            }).catch(function () { /* suggestions are a nicety */ });
    }

    // ── dirty tracking + save ────────────────────────────────────────────────
    function currentValues() {
        var vals = {};
        state.overlay.querySelectorAll('[data-vmg-f]').forEach(function (el) {
            vals[el.getAttribute('data-vmg-f')] = el.value.trim();
        });
        vals.genres = state.genres.slice();
        return vals;
    }

    function dirtyChanges() {
        var d = state.data, vals = currentValues(), changes = {};
        Object.keys(vals).forEach(function (f) {
            if (f === 'genres') {
                var was = (d.genres || []).slice().sort().join(' ');
                var now = vals.genres.slice().sort().join(' ');
                if (was !== now) changes.genres = vals.genres;
            } else if (f === 'year') {
                var wasY = d.year == null ? '' : String(d.year);
                if (vals.year !== wasY) changes.year = vals.year;
            } else {
                var wasV = d[f] == null ? '' : String(d[f]);
                if (vals[f] !== wasV) changes[f] = vals[f];
            }
        });
        return changes;
    }

    function markDirty() {
        var btn = state.overlay.querySelector('[data-vmg-save]');
        if (btn) btn.disabled = Object.keys(dirtyChanges()).length === 0 || state.saving;
    }

    function save() {
        var changes = dirtyChanges();
        if (!Object.keys(changes).length || state.saving) return;
        if ('title' in changes && !changes.title) { toast('Title can’t be empty', 'error'); return; }
        state.saving = true;
        var btn = state.overlay.querySelector('[data-vmg-save]');
        if (btn) { btn.disabled = true; btn.textContent = 'Saving…'; }
        fetch('/api/video/detail/' + state.kind + '/' + state.id + '/metadata', {
            method: 'PUT', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ changes: changes }),
        }).then(function (r) { return r.json().then(function (b) { return { ok: r.ok, body: b }; }); })
        .then(function (res) {
            if (!res.ok) throw new Error((res.body && res.body.error) || 'save failed');
            toast(res.body.pushed ? 'Saved & pushed to your server'
                                  : 'Saved — server not reached, will hold locally', 'success');
            document.dispatchEvent(new CustomEvent('soulsync:video-meta-changed', {
                detail: { kind: state.kind, id: state.id },
            }));
            close(true);
        }).catch(function (e) {
            if (state) {
                state.saving = false;
                if (btn) { btn.textContent = 'Save'; }
                markDirty();
            }
            toast(e && e.message ? e.message : 'Save failed', 'error');
        });
    }

    function releaseLock(field) {
        var labels = { sort_title: 'Sort title', content_rating: 'Content rating', overview: 'Summary' };
        var label = labels[field] || (field.charAt(0).toUpperCase() + field.slice(1));
        confirmDlg({
            title: 'Release ' + label + '?',
            message: 'This hands the field back to your media server — the next library scan re-adopts the server’s value.',
            confirmText: 'Release', cancelText: 'Keep mine',
        }).then(function (yes) {
            if (!yes || !state) return;
            fetch('/api/video/detail/' + state.kind + '/' + state.id + '/lock', {
                method: 'POST', headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ field: field, locked: false }),
            }).then(function (r) { return r.ok ? r.json() : null; })
            .then(function (res) {
                if (!res || !state) { toast('Couldn’t release the lock', 'error'); return; }
                state.locked = res.locked || [];
                var badge = state.overlay.querySelector('[data-vmg-release="' + field + '"]');
                if (badge) badge.remove();
                toast(label + ' released — next scan takes the server’s value', 'info');
                document.dispatchEvent(new CustomEvent('soulsync:video-meta-changed', {
                    detail: { kind: state.kind, id: state.id },
                }));
            }).catch(function () { toast('Couldn’t release the lock', 'error'); });
        });
    }

    function toggle(which, el) {
        var url = which === 'watched'
            ? '/api/video/detail/' + state.kind + '/' + state.id + '/watched'
            : '/api/video/monitor';
        var on = !el.classList.contains('vmg-toggle--on');
        var body = which === 'watched'
            ? { watched: on }
            : { kind: state.kind, id: state.id, monitored: on };
        el.classList.toggle('vmg-toggle--on', on);
        el.setAttribute('aria-checked', on ? 'true' : 'false');
        fetch(url, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) })
            .then(function (r) {
                if (!r.ok) throw new Error();
                state.data[which] = on;
                document.dispatchEvent(new CustomEvent('soulsync:video-meta-changed', {
                    detail: { kind: state.kind, id: state.id, quiet: true },
                }));
            })
            .catch(function () {
                el.classList.toggle('vmg-toggle--on', !on);
                el.setAttribute('aria-checked', !on ? 'true' : 'false');
                toast('Couldn’t update ' + which, 'error');
            });
    }

    // ── open / close ─────────────────────────────────────────────────────────
    function close(force) {
        if (!state) return;
        var finish = function () {
            var ov = state && state.overlay;
            state = null;
            if (!ov) return;
            ov.classList.remove('vmg-open');
            setTimeout(function () { ov.remove(); }, 230);
            document.removeEventListener('keydown', onKey, true);
        };
        if (!force && Object.keys(dirtyChanges()).length) {
            confirmDlg({
                title: 'Discard changes?', message: 'You have unsaved edits.',
                confirmText: 'Discard', cancelText: 'Keep editing', destructive: true,
            }).then(function (yes) { if (yes) finish(); });
            return;
        }
        finish();
    }

    function onKey(e) {
        if (e.key === 'Escape' && state) { e.stopPropagation(); close(); }
    }

    function wire() {
        var ov = state.overlay;
        ov.addEventListener('click', function (e) {
            if (e.target === ov) { close(); return; }
            if (e.target.closest('[data-vmg-close]')) { close(); return; }
            if (e.target.closest('[data-vmg-save]')) { save(); return; }
            var rel = e.target.closest('[data-vmg-release]');
            if (rel) { releaseLock(rel.getAttribute('data-vmg-release')); return; }
            var rm = e.target.closest('[data-vmg-chip-rm]');
            if (rm) {
                state.genres.splice(parseInt(rm.getAttribute('data-vmg-chip-rm'), 10), 1);
                renderChips(); markDirty(); return;
            }
            if (e.target.closest('[data-vmg-poster]')) {
                if (window.VideoPoster) {
                    VideoPoster.open({ kind: state.kind, tmdbId: state.data.tmdb_id, libraryId: state.id,
                        title: state.data.title || '', year: state.data.year || null });
                }
                return;
            }
            var tw = e.target.closest('[data-vmg-watched]');
            if (tw) { toggle('watched', tw); return; }
            var tm = e.target.closest('[data-vmg-monitored]');
            if (tm) { toggle('monitored', tm); return; }
            var chips = e.target.closest('[data-vmg-chips]');
            if (chips) { var ci = chips.querySelector('[data-vmg-chip-in]'); if (ci) ci.focus(); }
        });
        ov.addEventListener('input', function (e) {
            if (e.target.closest('[data-vmg-f]')) markDirty();
        });
        ov.addEventListener('keydown', function (e) {
            var ci = e.target.closest('[data-vmg-chip-in]');
            if (ci) {
                if (e.key === 'Enter' || e.key === ',') { e.preventDefault(); addGenre(ci.value); ci.value = ''; }
                else if (e.key === 'Backspace' && !ci.value && state.genres.length) {
                    state.genres.pop(); renderChips(); markDirty();
                }
                return;
            }
            var sw = e.target.closest('[data-vmg-watched],[data-vmg-monitored]');
            if (sw && (e.key === 'Enter' || e.key === ' ')) {
                e.preventDefault();
                toggle(sw.hasAttribute('data-vmg-watched') ? 'watched' : 'monitored', sw);
            }
        });
        // Genre picked from the datalist (input fires without a key event on click).
        var ci = ov.querySelector('[data-vmg-chip-in]');
        if (ci) {
            ci.addEventListener('change', function () { addGenre(ci.value); ci.value = ''; });
        }
        document.addEventListener('keydown', onKey, true);
    }

    function open(opts) {
        if (!opts || !opts.kind || opts.id == null) return;
        if (state) close(true);
        ensureStyles();
        fetch('/api/video/detail/' + encodeURIComponent(opts.kind) + '/' + encodeURIComponent(opts.id))
            .then(function (r) { return r.ok ? r.json() : null; })
            .then(function (d) {
                if (!d) { toast('Couldn’t load item', 'error'); return; }
                var ov = document.createElement('div');
                ov.className = 'vmg-overlay';
                ov.innerHTML = panelHtml(d);
                document.body.appendChild(ov);
                state = { kind: d.kind, id: d.id, data: d, saving: false,
                    genres: (d.genres || []).slice(), locked: (d.locked_fields || []).slice(),
                    overlay: ov };
                renderChips();
                wire();
                loadGenreSuggestions(d.kind);
                requestAnimationFrame(function () { ov.classList.add('vmg-open'); });
            })
            .catch(function () { toast('Couldn’t load item', 'error'); });
    }

    window.VideoManage = { open: open };
})();
