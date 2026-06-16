/*
 * SoulSync — Video watchlist button (shared).
 *
 * One source of truth so every TV-show poster and person card gets the SAME
 * "add to watchlist" control + behaviour — the video mirror of the music
 * ya-watchlist-btn (eye icon, top-right, `.active` = watched).
 *
 * Renderers call `VideoWatchlist.btn({kind, tmdbId, title, poster, libraryId})`
 * to emit the markup. A single delegated click handler toggles add/remove and
 * flips the visual; `VideoWatchlist.hydrate(root)` batch-checks watched state
 * after a render. Self-contained — inert until a `.vwl-btn` exists in the DOM.
 *
 * kind is 'show' or 'person' ONLY (movies + episodes are wishlist, not watch).
 */
(function () {
    'use strict';

    // Client-side cache of watched tmdb_ids per kind (so freshly-rendered cards
    // can paint correctly before a hydrate round-trip returns).
    var WATCHED = { show: {}, person: {} };

    function esc(s) {
        return String(s == null ? '' : s)
            .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
    }
    function eyeSvg(on) {
        return '<svg width="15" height="15" viewBox="0 0 24 24" fill="' + (on ? 'currentColor' : 'none') +
            '" stroke="currentColor" stroke-width="2"><path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"/>' +
            '<circle cx="12" cy="12" r="3"/></svg>';
    }
    function toast(msg, type) { if (typeof showToast === 'function') showToast(msg, type); }

    // Build the button markup. Returns '' for invalid input (so it can be
    // string-concatenated unconditionally by callers).
    function btn(opts) {
        if (!opts || !opts.tmdbId) return '';
        if (opts.kind !== 'show' && opts.kind !== 'person') return '';
        var on = !!WATCHED[opts.kind][opts.tmdbId];
        return '<button type="button" class="vwl-btn' + (on ? ' active' : '') + '"' +
            ' data-vwl-kind="' + opts.kind + '" data-vwl-id="' + esc(opts.tmdbId) + '"' +
            ' data-vwl-title="' + esc(opts.title || '') + '"' +
            ' data-vwl-poster="' + esc(opts.poster || '') + '"' +
            (opts.libraryId ? ' data-vwl-libid="' + esc(opts.libraryId) + '"' : '') +
            ' title="' + (on ? 'On watchlist' : 'Add to watchlist') + '"' +
            ' aria-label="' + (on ? 'On watchlist' : 'Add to watchlist') + '">' + eyeSvg(on) + '</button>';
    }

    function paint(b, on) {
        b.classList.toggle('active', on);
        b.title = on ? 'On watchlist' : 'Add to watchlist';
        b.setAttribute('aria-label', b.title);
        var svg = b.querySelector('svg');
        if (svg) svg.setAttribute('fill', on ? 'currentColor' : 'none');
    }

    // Reflect a (kind, id) across EVERY matching button in the DOM — the same
    // show can appear in the library, similar rail, search, etc. at once.
    function syncAll(kind, id, on) {
        if (on) WATCHED[kind][id] = true; else delete WATCHED[kind][id];
        var nodes = document.querySelectorAll('.vwl-btn[data-vwl-kind="' + kind + '"][data-vwl-id="' + id + '"]');
        for (var i = 0; i < nodes.length; i++) paint(nodes[i], on);
        // Let interested pages (e.g. the Watchlist page) react — e.g. drop a card
        // when it's un-followed, or refresh counts.
        document.dispatchEvent(new CustomEvent('soulsync:video-watchlist-changed', {
            detail: { kind: kind, id: id, watched: on }
        }));
    }

    function toggle(b) {
        var kind = b.getAttribute('data-vwl-kind'), id = b.getAttribute('data-vwl-id');
        if (!kind || !id || b.disabled) return;
        var on = b.classList.contains('active');
        b.disabled = true;
        var done = function () { b.disabled = false; };
        if (on) {
            // Removal is confirmed (the standard music yes/no dialog) — the eye is
            // small + easy to mis-click and the card vanishes on the watchlist
            // page. Adding stays instant.
            var name = b.getAttribute('data-vwl-title') || '';
            var label = name ? '“' + name + '”' : (kind === 'person' ? 'this person' : 'this show');
            var doRemove = function () {
                fetch('/api/video/watchlist/remove', {
                    method: 'POST', headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ kind: kind, tmdb_id: Number(id) })
                }).then(function (r) { return r.json(); }).then(function (d) {
                    if (d && d.success) { syncAll(kind, id, false); toast('Removed from watchlist', 'info'); }
                }).catch(function () { toast('Watchlist update failed', 'error'); }).then(done);
            };
            var confirm = (typeof showConfirmDialog === 'function')
                ? showConfirmDialog({
                    title: 'Remove from Watchlist',
                    message: 'Remove ' + label + ' from your watchlist?',
                    confirmText: 'Remove', cancelText: 'Cancel', destructive: true })
                : Promise.resolve(true);
            Promise.resolve(confirm).then(function (ok) {
                if (ok) doRemove();
                else done();   // cancelled — re-enable, no change
            });
        } else {
            var body = {
                kind: kind, tmdb_id: Number(id),
                title: b.getAttribute('data-vwl-title') || '',
                poster_url: b.getAttribute('data-vwl-poster') || ''
            };
            var lib = b.getAttribute('data-vwl-libid');
            if (lib) body.library_id = Number(lib);
            fetch('/api/video/watchlist/add', {
                method: 'POST', headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(body)
            }).then(function (r) { return r.json(); }).then(function (d) {
                if (d && d.success) { syncAll(kind, id, true); toast('Added to watchlist', 'success'); }
                else { toast((d && d.error) || 'Could not add', 'error'); }
            }).catch(function () { toast('Watchlist update failed', 'error'); }).then(done);
        }
    }

    // Batch-check watched state for every un-painted button under `root`.
    function hydrate(root) {
        root = root || document;
        ['show', 'person'].forEach(function (kind) {
            var nodes = root.querySelectorAll('.vwl-btn[data-vwl-kind="' + kind + '"]');
            if (!nodes.length) return;
            var ids = [], seen = {};
            for (var i = 0; i < nodes.length; i++) {
                var id = nodes[i].getAttribute('data-vwl-id');
                if (id && !seen[id]) { seen[id] = 1; ids.push(Number(id)); }
            }
            if (!ids.length) return;
            fetch('/api/video/watchlist/check', {
                method: 'POST', headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ kind: kind, tmdb_ids: ids })
            }).then(function (r) { return r.ok ? r.json() : null; }).then(function (d) {
                if (!d || !d.results) return;
                for (var k in d.results) { if (d.results[k]) WATCHED[kind][k] = true; }
                var ns = root.querySelectorAll('.vwl-btn[data-vwl-kind="' + kind + '"]');
                for (var j = 0; j < ns.length; j++) {
                    var bid = ns[j].getAttribute('data-vwl-id');
                    if (bid && d.results[bid]) paint(ns[j], true);
                }
            }).catch(function () { /* non-critical */ });
        });
    }

    // One capture-phase handler for the whole document: a watchlist button lives
    // inside a card <a>, so we must stop the click before it navigates.
    document.addEventListener('click', function (e) {
        var b = e.target.closest && e.target.closest('.vwl-btn');
        if (!b) return;
        e.preventDefault();
        e.stopPropagation();
        toggle(b);
    }, true);

    window.VideoWatchlist = { btn: btn, hydrate: hydrate, _watched: WATCHED };
})();
