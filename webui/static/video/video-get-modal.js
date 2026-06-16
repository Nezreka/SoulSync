/*
 * SoulSync — Video "get" button + detail/download modal (shared).
 *
 * The terminal-content counterpart to the watchlist eye: movies and ENDED shows
 * can't be "watched for new episodes," so instead of an eye they get a download
 * symbol that opens a rich detail modal — the future home of "Add to Wishlist"
 * / "Download". v1 is VISUAL ONLY: the modal renders real detail data, but the
 * action buttons are stubs (no backend wiring yet).
 *
 * Renderers call VideoGet.btn({kind, source, openId, title}); VideoGet.isAiring()
 * is the shared status test that decides eye-vs-get. Self-contained.
 */
(function () {
    'use strict';

    function esc(s) {
        return String(s == null ? '' : s)
            .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
    }
    function toast(msg, type) { if (typeof showToast === 'function') showToast(msg, type); }

    // A show is "airing" (eye) unless its status says it's finished (get-symbol).
    function isAiring(status) {
        var s = String(status == null ? '' : status).trim().toLowerCase();
        if (!s) return false;   // unknown status → treat as terminal (get), not watch
        return ['ended', 'canceled', 'cancelled', 'completed'].indexOf(s) === -1;
    }

    function dlSvg() {
        return '<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" ' +
            'stroke-width="2" stroke-linecap="round" stroke-linejoin="round">' +
            '<path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><path d="M7 10l5 5 5-5"/><path d="M12 15V3"/></svg>';
    }

    function btn(opts) {
        if (!opts || !opts.openId) return '';
        var kind = opts.kind === 'movie' ? 'movie' : 'show';
        return '<button type="button" class="vget-btn"' +
            ' data-vget-kind="' + kind + '" data-vget-source="' + esc(opts.source || 'library') + '"' +
            ' data-vget-id="' + esc(opts.openId) + '" data-vget-title="' + esc(opts.title || '') + '"' +
            ' title="Get this ' + kind + '" aria-label="Get this ' + kind + '">' + dlSvg() + '</button>';
    }

    // ── modal ─────────────────────────────────────────────────────────────────
    var modalEl = null, keyHandler = null;

    function closeModal() {
        if (!modalEl) return;
        modalEl.classList.remove('vgm-open');
        document.body.style.removeProperty('overflow');
        if (keyHandler) { document.removeEventListener('keydown', keyHandler); keyHandler = null; }
        var el = modalEl; modalEl = null;
        setTimeout(function () { if (el && el.parentNode) el.parentNode.removeChild(el); }, 220);
    }

    function openModal(o) {
        closeModal();
        var ov = document.createElement('div');
        ov.className = 'vgm-overlay';
        ov.innerHTML =
            '<div class="vgm-modal" role="dialog" aria-modal="true">' +
                '<button class="vgm-close" type="button" data-vgm-close aria-label="Close">&times;</button>' +
                '<div class="vgm-hero" data-vgm-hero>' +
                    '<div class="vgm-hero-scrim"></div>' +
                    '<div class="vgm-hero-content">' +
                        '<div class="vgm-eyebrow" data-vgm-eyebrow></div>' +
                        '<h2 class="vgm-title" data-vgm-title>' + esc(o.title || 'Loading…') + '</h2>' +
                        '<div class="vgm-meta" data-vgm-meta></div>' +
                        '<div class="vgm-genres" data-vgm-genres></div>' +
                    '</div>' +
                '</div>' +
                '<div class="vgm-body">' +
                    '<p class="vgm-overview" data-vgm-overview>Loading details…</p>' +
                '</div>' +
                '<div class="vgm-actions">' +
                    '<button class="vgm-btn vgm-btn--ghost" type="button" data-vgm-open>Open full page &rarr;</button>' +
                    '<button class="vgm-btn vgm-btn--primary" type="button" data-vgm-wishlist>+ Add to Wishlist</button>' +
                '</div>' +
            '</div>';
        document.body.appendChild(ov);
        document.body.style.overflow = 'hidden';
        modalEl = ov;
        requestAnimationFrame(function () { ov.classList.add('vgm-open'); });

        ov.addEventListener('click', function (e) {
            if (e.target === ov || e.target.closest('[data-vgm-close]')) { closeModal(); return; }
            if (e.target.closest('[data-vgm-open]')) {
                closeModal();
                document.dispatchEvent(new CustomEvent('soulsync:video-open-detail', {
                    detail: { kind: o.kind, id: parseInt(o.id, 10), source: o.source || 'library' },
                }));
                return;
            }
            if (e.target.closest('[data-vgm-wishlist]')) {
                // v1: visual only — real wishlist population is a later phase.
                toast('Wishlist coming soon', 'info');
            }
        });
        keyHandler = function (e) { if (e.key === 'Escape') closeModal(); };
        document.addEventListener('keydown', keyHandler);

        var url = (o.source === 'tmdb')
            ? '/api/video/tmdb/' + o.kind + '/' + o.id
            : '/api/video/detail/' + o.kind + '/' + o.id;
        fetch(url, { headers: { Accept: 'application/json' } })
            .then(function (r) { return r.ok ? r.json() : null; })
            .then(function (d) { if (modalEl && d) fill(d, o); })
            .catch(function () { /* keep the title-only shell */ });
    }

    function fill(d, o) {
        var q = function (s) { return modalEl.querySelector(s); };
        var id = o.id;
        // backdrop (library art routes; tmdb payloads carry urls)
        var hero = q('[data-vgm-hero]');
        var bg = (o.source !== 'tmdb' && d.has_backdrop)
            ? '/api/video/backdrop/' + o.kind + '/' + id + '?w=1280'
            : (d.backdrop_url || d.backdrop || '');
        if (hero && bg) hero.style.backgroundImage = "url('" + bg + "')";

        var t = q('[data-vgm-title]'); if (t && d.title) t.textContent = d.title;
        var eyebrow = [d.network, d.studio, d.year, d.status, d.content_rating].filter(Boolean).map(esc).join('  ·  ');
        var eb = q('[data-vgm-eyebrow]'); if (eb) eb.textContent = eyebrow;

        var meta = [];
        if (d.runtime_minutes) meta.push(d.runtime_minutes + ' min');
        if (d.rating) meta.push('★ ' + (Math.round(d.rating * 10) / 10));
        if (d.tagline) meta.push('“' + d.tagline + '”');
        var mt = q('[data-vgm-meta]'); if (mt) mt.innerHTML = meta.map(function (x) {
            return '<span class="vgm-meta-item">' + esc(x) + '</span>';
        }).join('');

        var g = q('[data-vgm-genres]');
        if (g && d.genres && d.genres.length) g.innerHTML = d.genres.slice(0, 5).map(function (x) {
            return '<span class="vgm-genre">' + esc(x) + '</span>';
        }).join('');

        var ov = q('[data-vgm-overview]');
        if (ov) {
            if (d.overview) { ov.textContent = d.overview; ov.classList.remove('vgm-overview--none'); }
            else { ov.textContent = 'No synopsis available yet.'; ov.classList.add('vgm-overview--none'); }
        }
    }

    // One capture-phase handler — the get button sits inside a card <a>.
    document.addEventListener('click', function (e) {
        var b = e.target.closest && e.target.closest('.vget-btn');
        if (!b) return;
        e.preventDefault();
        e.stopPropagation();
        openModal({
            kind: b.getAttribute('data-vget-kind'),
            source: b.getAttribute('data-vget-source') || 'library',
            id: b.getAttribute('data-vget-id'),
            title: b.getAttribute('data-vget-title') || '',
        });
    }, true);

    // Pick the right overlay control for any card: person → eye; movie → get;
    // show → eye when airing (or status unknown = still followable), get when
    // confirmed ended. One helper so every surface stays consistent.
    function cardButton(o) {
        if (!o || !o.kind) return '';
        if (o.kind === 'person') {
            return window.VideoWatchlist
                ? VideoWatchlist.btn({ kind: 'person', tmdbId: o.tmdbId, title: o.title, poster: o.poster }) : '';
        }
        if (o.kind === 'show') {
            var airing = !o.status ? true : isAiring(o.status);
            if (airing && o.tmdbId && window.VideoWatchlist) {
                return VideoWatchlist.btn({ kind: 'show', tmdbId: o.tmdbId, title: o.title,
                                            poster: o.poster, libraryId: o.libraryId });
            }
            return btn({ kind: 'show', source: o.source || 'tmdb', openId: o.libraryId || o.tmdbId, title: o.title });
        }
        if (o.kind === 'movie') {
            return btn({ kind: 'movie', source: o.source || 'tmdb', openId: o.libraryId || o.tmdbId, title: o.title });
        }
        return '';
    }

    window.VideoGet = { btn: btn, isAiring: isAiring, open: openModal, cardButton: cardButton };
})();
