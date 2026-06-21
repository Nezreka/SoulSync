/*
 * SoulSync — Video Import page (isolated).
 *
 * Mirrors the music Import page's job for the video side: a "Needs Attention" queue
 * of downloads that finished but couldn't be auto-placed (sample / wrong episode /
 * not-an-upgrade / corrupt / parse fail). Each one is resolved by HAND — pick the
 * right movie or show+episode (library/owned results float to the top, falling back
 * to a full TMDB search) and place it, or dismiss it.
 *
 * Reads /api/video/import/failed; resolves via /import/<id>/place + /dismiss; the
 * identity picker reuses /api/video/search. Polls every 5s while shown, like the
 * music page. Self-contained IIFE, no globals.
 */
(function () {
    'use strict';

    var PAGE_ID = 'video-import';
    var POLL_MS = 5000;
    var state = { loaded: false, items: [], resolve: null };
    var pollTimer = null;
    var searchTimer = null;

    function $(s, r) { return (r || document).querySelector(s); }
    function esc(s) {
        return String(s == null ? '' : s)
            .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
    }
    function basename(p) { return String(p || '').replace(/\\/g, '/').replace(/\/+$/, '').split('/').pop(); }
    function toast(msg, kind) { if (typeof showToast === 'function') showToast(msg, kind || 'info'); }
    function isShown() { return document.body.getAttribute('data-video-page') === PAGE_ID; }

    // ── needs-attention list ──────────────────────────────────────────────────
    function load() {
        fetch('/api/video/import/failed', { headers: { Accept: 'application/json' } })
            .then(function (r) { return r.ok ? r.json() : null; })
            .then(function (d) {
                state.loaded = true;
                state.items = (d && d.items) || [];
                render();
            })
            .catch(function () { state.loaded = true; render(); });
    }

    function card(it) {
        var scopeLabel = it.scope === 'episode' || it.kind === 'show' ? 'Episode' : 'Movie';
        var sub = [scopeLabel, it.year || null].filter(Boolean).join(' · ');
        return '<div class="vimp-card" data-vimp-card="' + esc(it.id) + '">' +
            '<div class="vimp-card-main">' +
                '<div class="vimp-card-title" title="' + esc(it.title || it.release_title) + '">' +
                    esc(it.title || it.release_title || 'Unknown') + '</div>' +
                '<div class="vimp-card-file" title="' + esc(it.file) + '">' + esc(basename(it.file) || '—') + '</div>' +
                '<div class="vimp-card-reason">' + esc(it.reason || 'Needs manual import') + '</div>' +
            '</div>' +
            '<div class="vimp-card-side">' +
                (sub ? '<span class="vimp-card-kind">' + esc(sub) + '</span>' : '') +
                '<div class="vimp-card-actions">' +
                    '<button class="vimp-btn vimp-btn--place" type="button" data-vimp-place="' + esc(it.id) + '">Place&hellip;</button>' +
                    '<button class="vimp-btn vimp-btn--dismiss" type="button" data-vimp-dismiss="' + esc(it.id) + '">Dismiss</button>' +
                '</div>' +
            '</div>' +
        '</div>';
    }

    function render() {
        var grid = $('[data-vimp-grid]');
        var loading = $('[data-vimp-loading]');
        var empty = $('[data-vimp-empty]');
        var count = $('[data-vimp-count]');
        if (!grid) return;
        if (loading) loading.classList.toggle('hidden', state.loaded);
        if (count) count.textContent = state.items.length ? String(state.items.length) : '';
        if (!state.loaded) { grid.innerHTML = ''; return; }
        if (!state.items.length) {
            grid.innerHTML = '';
            if (empty) empty.classList.remove('hidden');
            return;
        }
        if (empty) empty.classList.add('hidden');
        grid.innerHTML = state.items.map(card).join('');
    }

    function itemById(id) {
        for (var i = 0; i < state.items.length; i++)
            if (String(state.items[i].id) === String(id)) return state.items[i];
        return null;
    }

    // ── resolve modal ─────────────────────────────────────────────────────────
    function openResolve(item) {
        state.resolve = {
            item: item,
            kind: (item.scope === 'episode' || item.kind === 'show') ? 'episode' : 'movie',
            query: item.title || basename(item.file) || '',
            results: [], picked: null, season: item.season || '', episode: item.episode || '',
            searching: false,
        };
        ensureModal();
        renderModal();
        runSearch();
        var input = $('[data-vimp-q]');
        if (input) { input.value = state.resolve.query; input.focus(); }
    }

    function closeResolve() {
        state.resolve = null;
        var m = $('[data-vimp-modal]');
        if (m) m.remove();
    }

    function ensureModal() {
        if ($('[data-vimp-modal]')) return;
        var m = document.createElement('div');
        m.className = 'vimp-modal';
        m.setAttribute('data-vimp-modal', '');
        m.innerHTML =
            '<div class="vimp-modal-scrim" data-vimp-close></div>' +
            '<div class="vimp-modal-card" role="dialog" aria-label="Place file">' +
                '<div class="vimp-modal-head">' +
                    '<div class="vimp-modal-titles">' +
                        '<h2 class="vimp-modal-title">Place this file</h2>' +
                        '<div class="vimp-modal-file" data-vimp-modal-file></div>' +
                    '</div>' +
                    '<button class="vimp-modal-x" type="button" data-vimp-close aria-label="Close">&times;</button>' +
                '</div>' +
                '<div class="vimp-kindtabs" data-vimp-kindtabs>' +
                    '<button class="vimp-kindtab" type="button" data-vimp-kind="movie">Movie</button>' +
                    '<button class="vimp-kindtab" type="button" data-vimp-kind="episode">Episode</button>' +
                '</div>' +
                '<div class="vimp-search">' +
                    '<input type="text" class="vimp-search-input" data-vimp-q placeholder="Search your library &amp; TMDB&hellip;" autocomplete="off" spellcheck="false">' +
                '</div>' +
                '<div class="vimp-results" data-vimp-results></div>' +
                '<div class="vimp-ep" data-vimp-ep hidden>' +
                    '<label class="vimp-ep-field">Season <input type="number" min="0" data-vimp-season></label>' +
                    '<label class="vimp-ep-field">Episode <input type="number" min="0" data-vimp-episode></label>' +
                    '<label class="vimp-ep-field vimp-ep-field--wide">Title <input type="text" data-vimp-eptitle placeholder="optional"></label>' +
                '</div>' +
                '<div class="vimp-modal-foot">' +
                    '<button class="vimp-btn vimp-btn--ghost" type="button" data-vimp-close>Cancel</button>' +
                    '<button class="vimp-btn vimp-btn--place" type="button" data-vimp-confirm disabled>Place file</button>' +
                '</div>' +
            '</div>';
        document.body.appendChild(m);
    }

    function renderModal() {
        var r = state.resolve;
        if (!r) return;
        var fileEl = $('[data-vimp-modal-file]');
        if (fileEl) fileEl.textContent = basename(r.item.file) + ' — ' + (r.item.reason || '');
        var tabs = document.querySelectorAll('[data-vimp-kind]');
        for (var i = 0; i < tabs.length; i++)
            tabs[i].classList.toggle('vimp-kindtab--on', tabs[i].getAttribute('data-vimp-kind') === r.kind);
        var ep = $('[data-vimp-ep]');
        if (ep) ep.hidden = !(r.kind === 'episode' && r.picked);
        var sEl = $('[data-vimp-season]'); if (sEl && r.season !== '') sEl.value = r.season;
        var eEl = $('[data-vimp-episode]'); if (eEl && r.episode !== '') eEl.value = r.episode;
        renderResults();
        updateConfirm();
    }

    function renderResults() {
        var box = $('[data-vimp-results]');
        var r = state.resolve;
        if (!box || !r) return;
        if (r.searching) { box.innerHTML = '<div class="vimp-res-note">Searching&hellip;</div>'; return; }
        if (!r.results.length) { box.innerHTML = '<div class="vimp-res-note">No matches — try a different search.</div>'; return; }
        box.innerHTML = r.results.map(function (it, idx) {
            var on = r.picked && String(r.picked.media_id) === String(it.media_id);
            var meta = [it.year, it.owned ? 'In library' : null].filter(Boolean).join(' · ');
            var art = it.poster
                ? '<img class="vimp-res-img" src="' + esc(it.poster) + '" alt="" loading="lazy" onerror="this.style.visibility=\'hidden\'">'
                : '<div class="vimp-res-ph">' + (r.kind === 'episode' ? '📺' : '🎬') + '</div>';
            return '<button class="vimp-res' + (on ? ' vimp-res--on' : '') + (it.owned ? ' vimp-res--owned' : '') +
                '" type="button" data-vimp-pick="' + idx + '">' + art +
                '<span class="vimp-res-info"><span class="vimp-res-title">' + esc(it.title) + '</span>' +
                (meta ? '<span class="vimp-res-meta">' + esc(meta) + '</span>' : '') + '</span></button>';
        }).join('');
    }

    function updateConfirm() {
        var btn = $('[data-vimp-confirm]');
        var r = state.resolve;
        if (!btn || !r) return;
        var ok = !!r.picked && (r.kind === 'movie' ||
            (r.kind === 'episode' && r.season !== '' && r.episode !== ''));
        btn.disabled = !ok;
    }

    // Normalise a /api/video/search result into the picker's shape; keep only the
    // kind we're resolving (movies for 'movie', shows for 'episode'). Owned titles
    // (library_id present) are flagged so they can float to the top.
    function normResults(raw, kind) {
        var want = kind === 'episode' ? ['tv', 'show'] : ['movie'];
        var out = [];
        (raw || []).forEach(function (it) {
            var mt = String(it.media_type || it.type || (it.first_air_date ? 'tv' : 'movie')).toLowerCase();
            if (want.indexOf(mt) === -1) return;
            var date = it.year || it.release_date || it.first_air_date || '';
            out.push({
                media_id: it.tmdb_id != null ? it.tmdb_id : it.id,
                title: it.title || it.name || 'Unknown',
                year: String(date).slice(0, 4) || null,
                poster: it.poster_url || it.poster || (it.poster_path ? 'https://image.tmdb.org/t/p/w185' + it.poster_path : ''),
                owned: it.library_id != null,
            });
        });
        out.sort(function (a, b) { return (b.owned ? 1 : 0) - (a.owned ? 1 : 0); });   // library first
        return out;
    }

    function runSearch() {
        var r = state.resolve;
        if (!r) return;
        var q = (r.query || '').trim();
        if (!q) { r.results = []; r.searching = false; renderResults(); return; }
        r.searching = true; renderResults();
        fetch('/api/video/search?q=' + encodeURIComponent(q), { headers: { Accept: 'application/json' } })
            .then(function (res) { return res.ok ? res.json() : null; })
            .then(function (d) {
                if (!state.resolve || state.resolve !== r) return;
                r.searching = false;
                r.results = normResults((d && d.results) || [], r.kind);
                renderResults();
            })
            .catch(function () { if (state.resolve === r) { r.searching = false; renderResults(); } });
    }

    function place() {
        var r = state.resolve;
        if (!r || !r.picked) return;
        var body = {
            scope: r.kind, media_id: r.picked.media_id,
            title: r.picked.title, year: r.picked.year ? parseInt(r.picked.year, 10) : null,
        };
        if (r.kind === 'episode') {
            body.season = parseInt(r.season, 10);
            body.episode = parseInt(r.episode, 10);
            var t = $('[data-vimp-eptitle]'); if (t && t.value.trim()) body.episode_title = t.value.trim();
        }
        var btn = $('[data-vimp-confirm]'); if (btn) { btn.disabled = true; btn.textContent = 'Placing…'; }
        fetch('/api/video/import/' + r.item.id + '/place', {
            method: 'POST', headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
            body: JSON.stringify(body),
        }).then(function (res) { return res.ok ? res.json() : res.json().catch(function () { return null; }); })
            .then(function (d) {
                if (d && d.success) { toast('Placed “' + r.picked.title + '”', 'success'); closeResolve(); load(); }
                else { toast((d && d.error) || 'Couldn’t place the file', 'error');
                    if (btn) { btn.disabled = false; btn.textContent = 'Place file'; } }
            })
            .catch(function () { toast('Couldn’t place the file', 'error');
                if (btn) { btn.disabled = false; btn.textContent = 'Place file'; } });
    }

    function dismiss(id) {
        var it = itemById(id);
        var go = function (del) {
            fetch('/api/video/import/' + id + '/dismiss', {
                method: 'POST', headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
                body: JSON.stringify({ delete_file: !!del }),
            }).then(function (r) { return r.ok ? r.json() : null; })
                .then(function (d) { if (d && d.success) { toast('Dismissed', 'info'); load(); }
                    else toast('Couldn’t dismiss', 'error'); })
                .catch(function () { toast('Couldn’t dismiss', 'error'); });
        };
        if (typeof showConfirmDialog === 'function') {
            showConfirmDialog({
                title: 'Dismiss this import',
                message: 'Remove “' + ((it && it.title) || basename(it && it.file)) + '” from the list? ' +
                    'The file stays on disk unless you choose to delete it.',
                confirmText: 'Dismiss', cancelText: 'Cancel',
            }).then(function (ok) { if (ok) go(false); });
        } else if (window.confirm('Dismiss this import?')) { go(false); }
    }

    // ── events ────────────────────────────────────────────────────────────────
    function onGridClick(e) {
        var p = e.target.closest('[data-vimp-place]');
        if (p) { var it = itemById(p.getAttribute('data-vimp-place')); if (it) openResolve(it); return; }
        var d = e.target.closest('[data-vimp-dismiss]');
        if (d) { dismiss(d.getAttribute('data-vimp-dismiss')); return; }
    }

    function onModalClick(e) {
        if (e.target.closest('[data-vimp-close]')) { closeResolve(); return; }
        var k = e.target.closest('[data-vimp-kind]');
        if (k) { state.resolve.kind = k.getAttribute('data-vimp-kind'); state.resolve.picked = null;
            runSearch(); renderModal(); return; }
        var pk = e.target.closest('[data-vimp-pick]');
        if (pk) { var r = state.resolve;
            r.picked = r.results[parseInt(pk.getAttribute('data-vimp-pick'), 10)] || null;
            renderModal(); return; }
    }

    function onModalInput(e) {
        var r = state.resolve; if (!r) return;
        if (e.target.matches('[data-vimp-q]')) {
            r.query = e.target.value;
            clearTimeout(searchTimer); searchTimer = setTimeout(runSearch, 300); return;
        }
        if (e.target.matches('[data-vimp-season]')) { r.season = e.target.value; updateConfirm(); return; }
        if (e.target.matches('[data-vimp-episode]')) { r.episode = e.target.value; updateConfirm(); return; }
    }

    function startPoll() {
        if (pollTimer) return;
        pollTimer = setInterval(function () { if (isShown() && !state.resolve) load(); }, POLL_MS);
    }

    function onShown(e) {
        if (e && e.detail !== PAGE_ID) return;
        load();
        startPoll();
    }

    function init() {
        var grid = $('[data-vimp-grid]');
        if (grid) grid.addEventListener('click', onGridClick);
        var refresh = $('[data-vimp-refresh]');
        if (refresh) refresh.addEventListener('click', load);
        // The resolve modal is created on demand; delegate from the document.
        document.addEventListener('click', function (e) {
            if (state.resolve && e.target.closest('[data-vimp-modal]')) {
                if (e.target.closest('[data-vimp-confirm]')) { place(); return; }
                onModalClick(e);
            }
        });
        document.addEventListener('input', function (e) {
            if (state.resolve && e.target.closest('[data-vimp-modal]')) onModalInput(e);
        });
        document.addEventListener('soulsync:video-page-shown', onShown);
        if (isShown()) onShown({ detail: PAGE_ID });
    }

    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init);
    else init();
})();
