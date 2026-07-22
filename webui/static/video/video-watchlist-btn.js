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
    var WATCHED = { show: {}, person: {}, studio: {} };

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
        if (opts.kind !== 'show' && opts.kind !== 'person' && opts.kind !== 'studio') return '';
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
            var label = name ? '“' + name + '”'
                : (kind === 'person' ? 'this person' : kind === 'studio' ? 'this studio' : 'this show');
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
        } else if (kind === 'show') {
            // Follow-time monitor policy (arr-parity P2): shows ask WHAT to
            // monitor — future-only (the classic follow) or a back-catalog
            // slice wished right now. Person/studio follows stay one-click.
            done();                        // menu owns the flow from here
            followMenu(b);
        } else {
            follow(b, null, done);
        }
    }

    function follow(b, monitor, done) {
        var kind = b.getAttribute('data-vwl-kind'), id = b.getAttribute('data-vwl-id');
        var body = {
            kind: kind, tmdb_id: Number(id),
            title: b.getAttribute('data-vwl-title') || '',
            poster_url: b.getAttribute('data-vwl-poster') || ''
        };
        var lib = b.getAttribute('data-vwl-libid');
        if (lib) body.library_id = Number(lib);
        if (monitor && monitor !== 'future') body.monitor = monitor;
        fetch('/api/video/watchlist/add', {
            method: 'POST', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(body)
        }).then(function (r) { return r.json(); }).then(function (d) {
            if (d && d.success) {
                syncAll(kind, id, true);
                var n = d.wished || 0;
                toast(n ? 'Following — ' + n + ' episode' + (n === 1 ? '' : 's') + ' added to the wishlist'
                        : 'Added to watchlist', 'success');
                if (n) document.dispatchEvent(new CustomEvent('soulsync:video-wishlist-changed'));
            } else { toast((d && d.error) || 'Could not add', 'error'); }
        }).catch(function () { toast('Watchlist update failed', 'error'); }).then(done || function () {});
    }

    var MONITOR_OPTIONS = [
        ['future', 'Follow — new episodes as they air'],
        ['all', 'Follow + wishlist the entire back catalog'],
        ['latest_season', 'Follow + wishlist the latest season'],
        ['first_season', 'Follow + wishlist the first season'],
        ['pilot', 'Follow + wishlist just the pilot'],
    ];

    function followMenu(b) {
        var old = document.querySelector('.vwl-menu');
        if (old) old.remove();
        var m = document.createElement('div');
        m.className = 'vwl-menu';
        m.setAttribute('role', 'menu');
        m.innerHTML = MONITOR_OPTIONS.map(function (o, i) {
            return '<button type="button" class="vwl-menu-it' + (i === 0 ? ' vwl-menu-it--default' : '') +
                '" role="menuitem" data-vwl-monitor="' + o[0] + '">' + o[1] + '</button>';
        }).join('');
        document.body.appendChild(m);
        var r = b.getBoundingClientRect();
        m.style.top = Math.min(window.innerHeight - m.offsetHeight - 8, r.bottom + 6) + 'px';
        m.style.left = Math.max(8, Math.min(window.innerWidth - m.offsetWidth - 8, r.right - m.offsetWidth)) + 'px';
        function closeMenu() {
            m.remove();
            document.removeEventListener('click', closer, true);
            document.removeEventListener('keydown', onKey, true);
        }
        function closer(e) { if (!m.contains(e.target)) closeMenu(); }
        function onKey(e) { if (e.key === 'Escape') closeMenu(); }
        setTimeout(function () {
            document.addEventListener('click', closer, true);
            document.addEventListener('keydown', onKey, true);
        }, 0);
        m.addEventListener('click', function (e) {
            var it = e.target.closest('[data-vwl-monitor]');
            if (!it) return;
            e.preventDefault();
            e.stopPropagation();
            var monitor = it.getAttribute('data-vwl-monitor');
            closeMenu();
            b.disabled = true;
            follow(b, monitor, function () { b.disabled = false; });
        });
    }

    // Batch-check watched state for every un-painted button under `root`.
    function hydrate(root) {
        root = root || document;
        // Piggyback: every card surface already calls this after a render, so
        // it's the one hook that reaches ALL cards — hydrate wishlist ribbons
        // in the same pass (separate module; one batched POST of its own).
        if (window.VideoWishState) VideoWishState.hydrate(root);
        ['show', 'person', 'studio'].forEach(function (kind) {
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
