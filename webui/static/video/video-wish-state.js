/*
 * SoulSync — Video wishlist state hydration (shared).
 *
 * Cards for un-owned titles render a "Preview" ribbon and the Discover hero
 * renders a "+ Wishlist" CTA — but neither knew whether the title was ALREADY
 * wishlisted, so wishlisted items looked identical to new ones and "adding"
 * them again gave no meaningful feedback.
 *
 * `VideoWishState.hydrate(root)` batch-checks every un-owned tmdb card + hero
 * CTA under `root` against /api/video/wishlist/check (one POST for the lot:
 * movies by id, shows by any-episode membership) and repaints:
 *   · card ribbon  "Preview" → "Wishlisted" (.vsr-ribbon--wish)
 *   · hero button  "+ Wishlist" → "✓ In Wishlist" (.vdsc-hero-add--on)
 *
 * Invoked from VideoWatchlist.hydrate (which every card surface already
 * calls), and re-runs itself on soulsync:video-wishlist-changed so an add or
 * remove repaints every visible card live. Self-contained IIFE.
 */
(function () {
    'use strict';

    // Client cache so repeat hydrates repaint instantly (and freshly-added
    // items can paint before the next round-trip lands).
    var WISHED = { movie: {}, show: {} };

    function collect(root) {
        var movies = {}, shows = {};
        // Un-owned tmdb cards (owned cards already wear the In Library ribbon).
        var cards = root.querySelectorAll('.vsr-card:not(.vsr-card--owned)[data-vsr-source="tmdb"]');
        for (var i = 0; i < cards.length; i++) {
            var kind = cards[i].getAttribute('data-vsr-open');
            var id = parseInt(cards[i].getAttribute('data-vsr-id'), 10);
            if (!id) continue;
            if (kind === 'movie') movies[id] = 1;
            else if (kind === 'show') shows[id] = 1;
        }
        // Discover hero CTA(s).
        var heroes = root.querySelectorAll('[data-vdsc-hero-add]');
        for (var j = 0; j < heroes.length; j++) {
            var hk = heroes[j].getAttribute('data-kind');
            var hid = parseInt(heroes[j].getAttribute('data-tmdb'), 10);
            if (!hid) continue;
            if (hk === 'movie') movies[hid] = 1;
            else if (hk === 'show') shows[hid] = 1;
        }
        // Detail-page "More like this" cards (compact tmdb cards, no ribbon —
        // they get a corner chip instead). Owned cards already wear In Library.
        var sims = root.querySelectorAll('.vd-sim-card[data-vd-sim]:not([data-vd-sim-owned])');
        for (var k = 0; k < sims.length; k++) {
            var sk = sims[k].getAttribute('data-vd-sim');
            var sid = parseInt(sims[k].getAttribute('data-vd-sim-id'), 10);
            if (!sid) continue;
            if (sk === 'movie') movies[sid] = 1;
            else if (sk === 'show') shows[sid] = 1;
        }
        return { movies: Object.keys(movies), shows: Object.keys(shows) };
    }

    function paint(root) {
        var cards = root.querySelectorAll('.vsr-card:not(.vsr-card--owned)[data-vsr-source="tmdb"]');
        for (var i = 0; i < cards.length; i++) {
            var kind = cards[i].getAttribute('data-vsr-open');
            var id = cards[i].getAttribute('data-vsr-id');
            var on = !!(WISHED[kind] && WISHED[kind][id]);
            var ribbon = cards[i].querySelector('.vsr-ribbon--preview, .vsr-ribbon--wish');
            if (ribbon) {
                ribbon.classList.toggle('vsr-ribbon--wish', on);
                ribbon.classList.toggle('vsr-ribbon--preview', !on);
                ribbon.textContent = on ? 'Wishlisted' : 'Preview';
            }
        }
        var heroes = root.querySelectorAll('[data-vdsc-hero-add]');
        for (var j = 0; j < heroes.length; j++) {
            var hk = heroes[j].getAttribute('data-kind');
            var hid = heroes[j].getAttribute('data-tmdb');
            var hon = !!(WISHED[hk] && WISHED[hk][hid]);
            heroes[j].classList.toggle('vdsc-hero-add--on', hon);
            heroes[j].innerHTML = hon
                ? '<span aria-hidden="true">✓</span> In Wishlist'
                : '<span aria-hidden="true">＋</span> Wishlist';
            heroes[j].title = hon ? 'Already on your wishlist — open to manage' : 'Add to wishlist';
        }
        var sims = root.querySelectorAll('.vd-sim-card[data-vd-sim]:not([data-vd-sim-owned])');
        for (var k = 0; k < sims.length; k++) {
            var sk = sims[k].getAttribute('data-vd-sim');
            var sid = sims[k].getAttribute('data-vd-sim-id');
            var son = !!(WISHED[sk] && WISHED[sk][sid]);
            var chip = sims[k].querySelector('.vd-sim-wishchip');
            if (son && !chip) {
                sims[k].insertAdjacentHTML('afterbegin', '<span class="vd-sim-wishchip">Wishlisted</span>');
            } else if (!son && chip) {
                chip.remove();
            }
        }
    }

    function hydrate(root) {
        root = root || document;
        var want = collect(root);
        if (!want.movies.length && !want.shows.length) return;
        fetch('/api/video/wishlist/check', {
            method: 'POST', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ movie_ids: want.movies, shows: want.shows }),
        }).then(function (r) { return r.ok ? r.json() : null; }).then(function (d) {
            if (!d || !d.success) return;
            (d.movies || []).forEach(function (id) { WISHED.movie[id] = 1; });
            // A show counts as wishlisted when ANY of its episodes are.
            var byShow = d.by_show || {};
            want.shows.forEach(function (id) {
                if ((byShow[id] || []).length) WISHED.show[id] = 1;
                else delete WISHED.show[id];
            });
            want.movies.forEach(function (id) {
                if ((d.movies || []).indexOf(Number(id)) === -1) delete WISHED.movie[id];
            });
            paint(root);
        }).catch(function () { /* a missed badge is non-critical */ });
    }

    // Live repaint: any add/remove anywhere (get modal, detail page, wishlist
    // page) re-checks what's on screen. Debounced — bulk adds fire in bursts.
    var _t;
    document.addEventListener('soulsync:video-wishlist-changed', function () {
        clearTimeout(_t);
        _t = setTimeout(function () { hydrate(document); }, 400);
    });

    window.VideoWishState = { hydrate: hydrate };
})();
