// ============================================================================
// LABEL DETAIL PAGE
// ----------------------------------------------------------------------------
// A record label's catalog as an acquisition surface: a newest-first flat grid
// of the SAME .release-card/.album-card the artist-detail discography uses, so
// it matches the app. Clicking a release opens the standard "get this album"
// download modal (like clicking an album in search); a secondary button jumps
// to that artist's page. Owned/missing overlay + All/Missing/Owned filter.
//
// Purely additive + self-contained: it calls the /api/labels/* blueprint, the
// shared /api/enhanced-search/library-check (ownership), and the shared album
// detail + download-modal helpers. Reached via navigateToLabelDetail(id, name).
// ============================================================================

(function () {
    'use strict';

    const PAGE_SIZE = 60;

    let _wired = false;
    let _current = { id: null, name: '', watching: false, backlog: false };
    let _all = [];
    let _owned = new Set();
    let _checked = new Set();
    let _byKey = new Map();        // key -> release (for click lookup)
    let _page = 0;
    let _hasMore = false;
    let _loading = false;
    let _filter = 'all';
    let _sort = 'newest';
    let _returnTo = 'search';
    let _sentinelObserver = null;
    let _reqToken = 0;

    function _esc(s) {
        if (typeof escapeHtml === 'function') return escapeHtml(s == null ? '' : String(s));
        const d = document.createElement('div');
        d.textContent = s == null ? '' : String(s);
        return d.innerHTML;
    }

    const _key = (r) => `${(r.artist || '').toLowerCase()}||${(r.album || '').toLowerCase()}`;

    // Cover art at RELEASE scope (art lives there far more often than at
    // release-group scope). Routed through the app's /api/image-proxy so the
    // browser fetches it SAME-ORIGIN — Cover Art Archive isn't reliably
    // reachable client-side (ERR_CONNECTION_RESET), but the server can fetch +
    // disk-cache it. Lazy loading means only visible covers get proxied.
    function _coverUrl(rel) {
        const rid = rel && rel.release_id;
        if (!rid) return '';
        const caa = `https://coverartarchive.org/release/${encodeURIComponent(rid)}/front-250`;
        return `/api/image-proxy?url=${encodeURIComponent(caa)}`;
    }

    function _injectStyles() {
        if (document.getElementById('label-detail-styles')) return;
        const css = `
        #label-detail-page .label-detail-container { padding: 22px 28px 60px; max-width: 1500px; margin: 0 auto; }
        #label-detail-page .label-detail-back { background: rgba(255,255,255,0.06); color: var(--text-secondary,#9aa0aa);
            border: 1px solid rgba(255,255,255,0.08); border-radius: 8px; padding: 7px 14px; cursor: pointer;
            font-size: 13px; margin-bottom: 20px; }
        #label-detail-page .label-detail-back:hover { background: rgba(255,255,255,0.12); color: #fff; }
        #label-detail-page .label-detail-hero { display: flex; align-items: center; gap: 22px; flex-wrap: wrap; }
        #label-detail-page .label-detail-hero-art { width: 104px; height: 104px; border-radius: 16px; flex: 0 0 auto;
            display: flex; align-items: center; justify-content: center; font-size: 50px;
            background: linear-gradient(135deg, rgba(var(--accent-rgb,29,185,84),0.22), rgba(255,255,255,0.05));
            border: 1px solid rgba(255,255,255,0.08); }
        #label-detail-page .label-detail-hero-main { flex: 1 1 260px; min-width: 0; }
        #label-detail-page .label-detail-eyebrow { text-transform: uppercase; letter-spacing: .12em; font-size: 11px;
            font-weight: 700; color: rgb(var(--accent-light-rgb,52,211,120)); margin-bottom: 6px; }
        #label-detail-page .label-detail-name { font-size: 32px; font-weight: 800; margin: 0; color: var(--text-primary,#fff);
            line-height: 1.1; overflow: hidden; text-overflow: ellipsis; }
        #label-detail-page .label-detail-meta { color: var(--text-secondary,#9aa0aa); font-size: 13px; margin-top: 8px; }
        #label-detail-page .label-detail-hero-actions { display: flex; flex-direction: column; gap: 12px; align-items: flex-end; }
        #label-detail-page .label-detail-backlog { display: flex; align-items: center; gap: 8px; }
        #label-detail-page .label-detail-backlog-label { font-size: 12px; color: var(--text-secondary,#8a909a); }
        #label-detail-page .label-detail-seg { display: inline-flex; background: rgba(255,255,255,0.05);
            border: 1px solid rgba(255,255,255,0.08); border-radius: 999px; padding: 2px; }
        #label-detail-page .label-detail-seg button { border: none; background: transparent; color: var(--text-secondary,#9aa0aa);
            font-size: 12px; padding: 4px 12px; border-radius: 999px; cursor: pointer; }
        #label-detail-page .label-detail-seg button.active { background: rgb(var(--accent-rgb,29,185,84)); color: #fff; }
        #label-detail-page .label-detail-toolbar { display: flex; align-items: center; justify-content: space-between;
            gap: 14px; margin: 26px 0 18px; flex-wrap: wrap; border-top: 1px solid rgba(255,255,255,0.06); padding-top: 20px; }
        #label-detail-page .label-detail-filters { display: flex; gap: 8px; }
        #label-detail-page .label-detail-filters button { background: rgba(255,255,255,0.05); color: var(--text-secondary,#9aa0aa);
            border: 1px solid rgba(255,255,255,0.08); border-radius: 999px; padding: 7px 16px; cursor: pointer; font-size: 13px; font-weight: 600; }
        #label-detail-page .label-detail-filters button:hover { color: #fff; }
        #label-detail-page .label-detail-filters button.active { background: rgba(var(--accent-rgb,29,185,84),0.9); color: #fff; border-color: transparent; }
        #label-detail-page .label-detail-filters button span { opacity: .7; font-size: 12px; margin-left: 2px; }
        #label-detail-page .label-detail-sort { background: rgba(255,255,255,0.05); color: var(--text-primary,#eaecef);
            border: 1px solid rgba(255,255,255,0.08); border-radius: 8px; padding: 7px 12px; font-size: 13px; cursor: pointer; }
        #label-detail-page .label-detail-status { color: var(--text-secondary,#9aa0aa); padding: 40px 0; text-align: center; font-size: 15px; }
        #label-detail-page .label-release-grid { display: grid; grid-template-columns: repeat(auto-fill,minmax(170px,1fr)); gap: 20px; align-items: start; }
        /* The .album-card square/overlay treatment is scoped to #artist-detail-page
           in style.css; bring the SAME container override here so label cards are
           full-bleed squares with the info pinned over the art (not the base
           .release-card 300px stacked layout). .album-card-image/-content are
           global, so they overlay correctly once the container is fixed. */
        #label-detail-page .release-card.album-card { background: rgba(18,18,18,1); backdrop-filter: none;
            border: 1px solid rgba(255,255,255,0.06); border-radius: 14px; padding: 0; display: block;
            height: auto; aspect-ratio: 1; overflow: hidden; box-shadow: 0 4px 16px rgba(0,0,0,0.3); }
        #label-detail-page .release-card.album-card:hover { transform: translateY(-5px) scale(1.02);
            border-color: rgba(var(--accent-rgb,29,185,84),0.25);
            box-shadow: 0 12px 40px rgba(0,0,0,0.5), 0 0 24px rgba(var(--accent-rgb,29,185,84),0.12); }
        #label-detail-page .release-card.album-card.missing { opacity: 1; }
        /* the go-to-artist chip (top-LEFT so it never collides with the
           top-right Owned/Missing completion badge) */
        #label-detail-page .album-card .label-card-artist-btn { position: absolute; top: 8px; left: 8px; z-index: 4;
            width: 28px; height: 28px; border-radius: 50%; border: none; cursor: pointer; font-size: 13px;
            background: rgba(0,0,0,0.55); color: #fff; opacity: 0; transition: opacity .15s; display: flex;
            align-items: center; justify-content: center; }
        #label-detail-page .album-card:hover .label-card-artist-btn { opacity: 1; }
        #label-detail-page .album-card .label-card-artist-btn:hover { background: rgb(var(--accent-rgb,29,185,84)); }
        #label-detail-page .album-card .album-card-year .lc-artist { color: rgba(255,255,255,0.85); }
        @media (max-width: 640px) {
            #label-detail-page .label-detail-container { padding: 16px; }
            #label-detail-page .label-detail-hero-actions { align-items: stretch; width: 100%; }
            #label-detail-page .label-release-grid { grid-template-columns: repeat(auto-fill,minmax(120px,1fr)); gap: 14px; }
            #label-detail-page .album-card .label-card-artist-btn { opacity: 1; }
        }`;
        const style = document.createElement('style');
        style.id = 'label-detail-styles';
        style.textContent = css;
        document.head.appendChild(style);
    }

    function _ensureObservers() {
        if (!_sentinelObserver && 'IntersectionObserver' in window) {
            _sentinelObserver = new IntersectionObserver((entries) => {
                if (entries.some(e => e.isIntersecting) && _hasMore && !_loading) _fetchPage();
            }, { rootMargin: '400px' });
            const sentinel = document.getElementById('label-detail-sentinel');
            if (sentinel) _sentinelObserver.observe(sentinel);
        }
    }

    // ---- watchlist + backlog (standard app watchlist button) -----------------
    function _setWatchState(watching) {
        _current.watching = !!watching;
        const btn = document.getElementById('label-detail-watch-btn');
        const backlog = document.getElementById('label-detail-backlog');
        if (btn) {
            btn.hidden = false;
            btn.classList.toggle('watching', !!watching);
            const txt = btn.querySelector('.watchlist-text');
            if (txt) txt.textContent = watching ? 'Watching...' : 'Add to Watchlist';
        }
        if (backlog) backlog.hidden = !watching;
    }

    function _setBacklogState(backlog) {
        _current.backlog = !!backlog;
        document.querySelectorAll('#label-detail-backlog .label-detail-seg button').forEach(b => {
            b.classList.toggle('active', b.getAttribute('data-backlog') === (backlog ? '1' : '0'));
        });
    }

    async function _toggleWatch() {
        if (!_current.id) return;
        const btn = document.getElementById('label-detail-watch-btn');
        const txt = btn && btn.querySelector('.watchlist-text');
        if (txt) txt.textContent = 'Loading...';
        try {
            const url = _current.watching ? '/api/labels/watchlist/remove' : '/api/labels/watchlist/add';
            const body = _current.watching
                ? { musicbrainz_label_id: _current.id }
                : { musicbrainz_label_id: _current.id, label_name: _current.name };
            const d = await fetch(url, { method: 'POST', headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(body) }).then(r => r.json()).catch(() => ({}));
            if (d && d.success) {
                _setWatchState(!_current.watching);
                if (typeof updateWatchlistButtonCount === 'function') {
                    try { updateWatchlistButtonCount(); } catch (e) { /* non-fatal */ }
                }
            } else {
                _setWatchState(_current.watching);   // restore label
            }
        } catch (e) {
            _setWatchState(_current.watching);
            if (typeof showToast === 'function') showToast('Could not update watchlist', 'error');
        }
    }

    async function _setBacklog(backlog) {
        if (!_current.id || _current.backlog === backlog) return;
        _setBacklogState(backlog);
        try {
            const d = await fetch('/api/labels/watchlist/backlog', {
                method: 'POST', headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ musicbrainz_label_id: _current.id, backlog }),
            }).then(r => r.json()).catch(() => ({}));
            if (!d || !d.success) _setBacklogState(!backlog);
        } catch (e) { _setBacklogState(!backlog); }
    }

    // ---- data ----------------------------------------------------------------
    async function _fetchPage() {
        if (_loading || !_current.id) return;
        _loading = true;
        const token = _reqToken;
        const moreEl = document.getElementById('label-detail-more');
        if (_page > 0 && moreEl) moreEl.classList.remove('hidden');
        try {
            const next = _page + 1;
            const url = `/api/labels/${encodeURIComponent(_current.id)}/catalog?page=${next}&page_size=${PAGE_SIZE}`
                + (_current.name ? `&name=${encodeURIComponent(_current.name)}` : '');
            const data = await fetch(url).then(r => r.json()).catch(() => ({}));
            if (token !== _reqToken) return;

            if (_page === 0) {
                const resolvedName = (data.label && data.label.name) || _current.name || 'Label';
                _current.name = resolvedName;
                const nameEl = document.getElementById('label-detail-name');
                if (nameEl) nameEl.textContent = resolvedName;
                const metaEl = document.getElementById('label-detail-meta');
                if (metaEl) {
                    metaEl.textContent = `${data.total || 0} release${data.total === 1 ? '' : 's'} · `
                        + `${data.artist_count || 0} artist${data.artist_count === 1 ? '' : 's'}`;
                }
                _setWatchState(!!data.is_watching);
                _setBacklogState(!!data.backlog);
                const toolbar = document.getElementById('label-detail-toolbar');
                if (toolbar) toolbar.hidden = false;
            }

            const batch = (data && data.releases) || [];
            batch.forEach(r => _byKey.set(_key(r), r));
            _all = _all.concat(batch);
            _page = next;
            _hasMore = !!(data && data.has_more);

            const loadingEl = document.getElementById('label-detail-loading');
            if (loadingEl) loadingEl.classList.add('hidden');

            _render();
            _checkOwnership(batch);
        } catch (e) {
            const loadingEl = document.getElementById('label-detail-loading');
            if (loadingEl) loadingEl.textContent = 'Could not load this label’s catalog.';
        } finally {
            _loading = false;
            if (moreEl) moreEl.classList.add('hidden');
        }
    }

    async function _checkOwnership(batch) {
        const fresh = (batch || []).filter(r => !_checked.has(_key(r)));
        if (!fresh.length) return;
        fresh.forEach(r => _checked.add(_key(r)));
        const token = _reqToken;
        try {
            const resp = await fetch('/api/enhanced-search/library-check', {
                method: 'POST', headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ albums: fresh.map(r => ({ name: r.album, artist: r.artist })), tracks: [] }),
            }).then(r => r.json()).catch(() => ({}));
            if (token !== _reqToken) return;
            const owned = (resp && resp.albums) || [];
            fresh.forEach((r, i) => { if (owned[i]) _owned.add(_key(r)); });
            _render();
        } catch (e) { /* ownership is a nicety */ }
    }

    // ---- render (reuses .release-card/.album-card markup) --------------------
    function _visible() {
        let rows = _all.slice();
        if (_filter === 'owned') rows = rows.filter(r => _owned.has(_key(r)));
        else if (_filter === 'missing') rows = rows.filter(r => !_owned.has(_key(r)));
        if (_sort === 'oldest') rows = rows.slice().reverse();
        else if (_sort === 'artist') rows = rows.slice().sort((a, b) =>
            (a.artist || '').localeCompare(b.artist || '') || (b.year || '').localeCompare(a.year || ''));
        return rows;
    }

    function _updateCounts() {
        const total = _all.length;
        const owned = _all.filter(r => _owned.has(_key(r))).length;
        const set = (id, n) => { const el = document.getElementById(id); if (el) el.textContent = n; };
        set('lf-count-all', total);
        set('lf-count-owned', owned);
        set('lf-count-missing', total - owned);
    }

    function _cardHtml(rel) {
        const cover = _coverUrl(rel);
        const isOwned = _owned.has(_key(rel));
        const checked = _checked.has(_key(rel));
        const overlay = isOwned
            ? '<div class="completion-overlay completed"><span class="completion-status">✓ Owned</span></div>'
            : (checked ? '<div class="completion-overlay missing"><span class="completion-status">Missing</span></div>' : '');
        const artistBtn = rel.artist_id
            ? '<button class="label-card-artist-btn" title="Go to artist" data-role="artist">👤</button>' : '';
        return `
            <div class="release-card album-card" data-key="${_esc(_key(rel))}" data-album-type="${_esc(rel.primary_type || 'album')}">
                <div class="album-card-image"${cover ? ` data-bg-src="${_esc(cover)}"` : ''}>
                    ${overlay}
                    ${artistBtn}
                </div>
                <div class="album-card-content">
                    <div class="album-card-name">${_esc(rel.album)}</div>
                    <div class="album-card-year"><span class="lc-artist">${_esc(rel.artist)}</span>${rel.year ? ' · ' + _esc(rel.year) : ''}</div>
                </div>
            </div>`;
    }

    function _render() {
        _updateCounts();
        const grid = document.getElementById('label-detail-grid');
        const empty = document.getElementById('label-detail-empty');
        if (!grid) return;
        const rows = _visible();
        if (!rows.length) {
            grid.innerHTML = '';
            if (empty) {
                empty.textContent = _all.length
                    ? `No ${_filter === 'owned' ? 'owned' : 'missing'} releases in this label.`
                    : 'No releases to show.';
                empty.classList.remove('hidden');
            }
            return;
        }
        if (empty) empty.classList.add('hidden');
        grid.innerHTML = rows.map(_cardHtml).join('');
        // Lazy background covers via the app's shared IntersectionObserver.
        if (typeof observeLazyBackgrounds === 'function') {
            observeLazyBackgrounds(grid);
        } else {
            grid.querySelectorAll('.album-card-image[data-bg-src]').forEach(el => {
                el.style.backgroundImage = `url("${el.dataset.bgSrc}")`;
            });
        }
    }

    function _onGridClick(e) {
        const card = e.target.closest('.release-card');
        if (!card) return;
        const rel = _byKey.get(card.getAttribute('data-key'));
        if (!rel) return;
        if (e.target.closest('[data-role="artist"]')) {
            e.stopPropagation();
            if (rel.artist_id && typeof navigateToArtistDetail === 'function') {
                navigateToArtistDetail(rel.artist_id, rel.artist, 'musicbrainz');
            }
            return;
        }
        _openReleaseModal(rel);
    }

    // Open the standard "get this album" download modal, resolving the release's
    // reliable art + tracklist in one MusicBrainz album-detail call.
    async function _openReleaseModal(rel) {
        if (typeof openDownloadMissingModalForArtistAlbum !== 'function') {
            if (typeof _handoffLibrarySearchToEnhancedSearch === 'function') {
                _handoffLibrarySearchToEnhancedSearch(`${rel.artist} ${rel.album}`);
            }
            return;
        }
        const rgid = rel.release_group_id;
        if (typeof showLoadingOverlay === 'function') showLoadingOverlay('Loading album...');
        try {
            const url = `/api/spotify/album/${encodeURIComponent(rgid)}?source=musicbrainz`
                + `&name=${encodeURIComponent(rel.album)}&artist=${encodeURIComponent(rel.artist)}`;
            const albumData = await fetch(url).then(r => r.json()).catch(() => ({}));
            const tracks = (albumData && albumData.tracks) || [];
            if (!tracks.length) {
                if (typeof showToast === 'function') showToast('No tracks found for this release', 'error');
                return;
            }
            const albumObj = {
                name: albumData.name || rel.album,
                id: rgid,
                album_type: rel.primary_type || 'album',
                images: albumData.images || [],
                image_url: (albumData.images && albumData.images[0] && albumData.images[0].url) || _coverUrl(rel),
                release_date: albumData.release_date || (rel.year ? rel.year + '-01-01' : ''),
                total_tracks: tracks.length,
                artists: [{ name: rel.artist }],
                source: 'musicbrainz',
            };
            const artistObj = { id: rel.artist_id || '', name: rel.artist, image_url: '', source: 'musicbrainz' };
            const enriched = tracks.map(t => Object.assign({}, t, { source: 'musicbrainz', album: albumObj }));
            openDownloadMissingModalForArtistAlbum(
                `mb_album_${rgid}`, `[${rel.artist}] ${albumObj.name}`, enriched, albumObj, artistObj, false, 'artist_album');
        } catch (e) {
            if (typeof showToast === 'function') showToast('Could not open this release', 'error');
        } finally {
            if (typeof hideLoadingOverlay === 'function') hideLoadingOverlay();
        }
    }

    // ---- public hooks --------------------------------------------------------
    window.initializeLabelDetailPage = function initializeLabelDetailPage() {
        _injectStyles();
        _ensureObservers();
        if (_wired) return;
        const back = document.getElementById('label-detail-back-btn');
        if (back) back.addEventListener('click', () => {
            if (typeof navigateToPage === 'function') navigateToPage(_returnTo || 'search');
            else history.back();
        });
        const watch = document.getElementById('label-detail-watch-btn');
        if (watch) watch.addEventListener('click', _toggleWatch);
        document.querySelectorAll('#label-detail-backlog .label-detail-seg button').forEach(b => {
            b.addEventListener('click', () => _setBacklog(b.getAttribute('data-backlog') === '1'));
        });
        document.querySelectorAll('#label-detail-filters button').forEach(b => {
            b.addEventListener('click', () => {
                _filter = b.getAttribute('data-lf');
                document.querySelectorAll('#label-detail-filters button').forEach(x => x.classList.toggle('active', x === b));
                _render();
            });
        });
        const sort = document.getElementById('label-detail-sort');
        if (sort) sort.addEventListener('change', () => { _sort = sort.value; _render(); });
        const grid = document.getElementById('label-detail-grid');
        if (grid) grid.addEventListener('click', _onGridClick);
        _wired = true;
    };

    window.loadLabelDetailData = function loadLabelDetailData(labelId, labelName) {
        if (!labelId) return;
        _reqToken += 1;
        _current = { id: String(labelId), name: labelName || '', watching: false, backlog: false };
        _all = []; _owned = new Set(); _checked = new Set(); _byKey = new Map();
        _page = 0; _hasMore = false; _loading = false; _filter = 'all'; _sort = 'newest';
        if (typeof window._labelDetailReturnTo === 'string' && window._labelDetailReturnTo) {
            _returnTo = window._labelDetailReturnTo;
        }

        const nameEl = document.getElementById('label-detail-name');
        if (nameEl) nameEl.textContent = labelName || 'Label';
        const metaEl = document.getElementById('label-detail-meta');
        if (metaEl) metaEl.textContent = '';
        const grid = document.getElementById('label-detail-grid');
        if (grid) grid.innerHTML = '';
        const empty = document.getElementById('label-detail-empty');
        if (empty) empty.classList.add('hidden');
        const toolbar = document.getElementById('label-detail-toolbar');
        if (toolbar) toolbar.hidden = true;
        const loadingEl = document.getElementById('label-detail-loading');
        if (loadingEl) { loadingEl.textContent = 'Loading label catalog…'; loadingEl.classList.remove('hidden'); }
        const watch = document.getElementById('label-detail-watch-btn');
        if (watch) watch.hidden = true;
        const sortSel = document.getElementById('label-detail-sort');
        if (sortSel) sortSel.value = 'newest';
        document.querySelectorAll('#label-detail-filters button').forEach(x =>
            x.classList.toggle('active', x.getAttribute('data-lf') === 'all'));

        _fetchPage();
    };
})();
