// ============================================================================
// LABEL DETAIL PAGE
// ----------------------------------------------------------------------------
// A record label's catalog as an acquisition surface (option A): a newest-first
// flat release grid with an owned/missing overlay, All/Missing/Owned filter +
// sort, lazy cover art, and infinite scroll — built to FEEL like the artist
// detail page, not a spreadsheet dump.
//
// Purely additive + self-contained: it only calls the /api/labels/* blueprint
// (+ the shared /api/enhanced-search/library-check for ownership) and owns its
// own DOM + scoped styles. Reached via navigateToLabelDetail(id, name).
// ============================================================================

(function () {
    'use strict';

    const PAGE_SIZE = 60;

    let _wired = false;
    let _current = { id: null, name: '', watching: false, backlog: false };
    let _all = [];                 // releases loaded so far (newest-first from API)
    let _owned = new Set();        // "artist||album" keys known owned
    let _checked = new Set();      // keys we've run the library-check for
    let _coverLoaded = new Set();  // cover URLs already fetched (so re-renders don't re-fetch)
    let _page = 0;
    let _hasMore = false;
    let _loading = false;
    let _filter = 'all';           // all | missing | owned
    let _sort = 'newest';          // newest | oldest | artist
    let _returnTo = 'search';
    let _coverObserver = null;
    let _sentinelObserver = null;
    let _reqToken = 0;             // guards against a stale label's responses

    function _esc(s) {
        if (typeof escapeHtml === 'function') return escapeHtml(s == null ? '' : String(s));
        const d = document.createElement('div');
        d.textContent = s == null ? '' : String(s);
        return d.innerHTML;
    }

    const _key = (r) => `${(r.artist || '').toLowerCase()}||${(r.album || '').toLowerCase()}`;

    function _injectStyles() {
        if (document.getElementById('label-detail-styles')) return;
        const css = `
        #label-detail-page .label-detail-container { padding: 22px 28px 60px; max-width: 1500px; margin: 0 auto; }
        #label-detail-page .label-detail-back { background: rgba(255,255,255,0.06); color: var(--text-secondary,#9aa0aa);
            border: 1px solid rgba(255,255,255,0.08); border-radius: 8px; padding: 7px 14px; cursor: pointer;
            font-size: 13px; margin-bottom: 20px; }
        #label-detail-page .label-detail-back:hover { background: rgba(255,255,255,0.12); color: #fff; }
        #label-detail-page .label-detail-hero { display: flex; align-items: center; gap: 22px; flex-wrap: wrap; }
        #label-detail-page .label-detail-hero-art { width: 108px; height: 108px; border-radius: 16px; flex: 0 0 auto;
            display: flex; align-items: center; justify-content: center; font-size: 52px;
            background: linear-gradient(135deg, rgba(29,185,84,0.22), rgba(255,255,255,0.05));
            border: 1px solid rgba(255,255,255,0.08); }
        #label-detail-page .label-detail-hero-main { flex: 1 1 260px; min-width: 0; }
        #label-detail-page .label-detail-eyebrow { text-transform: uppercase; letter-spacing: .12em; font-size: 11px;
            font-weight: 700; color: #1db954; margin-bottom: 6px; }
        #label-detail-page .label-detail-name { font-size: 34px; font-weight: 800; margin: 0; color: var(--text-primary,#fff);
            line-height: 1.1; overflow: hidden; text-overflow: ellipsis; }
        #label-detail-page .label-detail-meta { color: var(--text-secondary,#9aa0aa); font-size: 13px; margin-top: 8px; }
        #label-detail-page .label-detail-hero-actions { display: flex; flex-direction: column; gap: 12px; align-items: flex-end; }
        #label-detail-page .label-detail-watch-btn { padding: 11px 22px; border-radius: 999px; cursor: pointer;
            font-size: 14px; font-weight: 700; border: none; white-space: nowrap;
            background: linear-gradient(135deg,#1db954,#12833b); color: #fff; transition: filter .15s, background .15s; }
        #label-detail-page .label-detail-watch-btn:hover { filter: brightness(1.08); }
        #label-detail-page .label-detail-watch-btn.watching { background: rgba(255,255,255,0.08);
            color: var(--text-secondary,#cfd3da); border: 1px solid rgba(255,255,255,0.16); }
        #label-detail-page .label-detail-watch-btn.watching:hover { background: rgba(220,60,60,0.16); color: #ff8080; border-color: rgba(220,60,60,0.4); }
        #label-detail-page .label-detail-backlog { display: flex; align-items: center; gap: 8px; }
        #label-detail-page .label-detail-backlog-label { font-size: 12px; color: var(--text-secondary,#8a909a); }
        #label-detail-page .label-detail-seg { display: inline-flex; background: rgba(255,255,255,0.05);
            border: 1px solid rgba(255,255,255,0.08); border-radius: 999px; padding: 2px; }
        #label-detail-page .label-detail-seg button { border: none; background: transparent; color: var(--text-secondary,#9aa0aa);
            font-size: 12px; padding: 4px 12px; border-radius: 999px; cursor: pointer; }
        #label-detail-page .label-detail-seg button.active { background: #1db954; color: #fff; }
        #label-detail-page .label-detail-toolbar { display: flex; align-items: center; justify-content: space-between;
            gap: 14px; margin: 26px 0 18px; flex-wrap: wrap; border-top: 1px solid rgba(255,255,255,0.06); padding-top: 20px; }
        #label-detail-page .label-detail-filters { display: flex; gap: 8px; }
        #label-detail-page .label-detail-filters button { background: rgba(255,255,255,0.05); color: var(--text-secondary,#9aa0aa);
            border: 1px solid rgba(255,255,255,0.08); border-radius: 999px; padding: 7px 16px; cursor: pointer; font-size: 13px; font-weight: 600; }
        #label-detail-page .label-detail-filters button:hover { color: #fff; }
        #label-detail-page .label-detail-filters button.active { background: linear-gradient(135deg,#1db954,#12833b); color: #fff; border-color: transparent; }
        #label-detail-page .label-detail-filters button span { opacity: .7; font-size: 12px; margin-left: 2px; }
        #label-detail-page .label-detail-sort { background: rgba(255,255,255,0.05); color: var(--text-primary,#eaecef);
            border: 1px solid rgba(255,255,255,0.08); border-radius: 8px; padding: 7px 12px; font-size: 13px; cursor: pointer; }
        #label-detail-page .label-detail-status { color: var(--text-secondary,#9aa0aa); padding: 40px 0; text-align: center; font-size: 15px; }
        #label-detail-page .label-release-grid { display: grid; grid-template-columns: repeat(auto-fill,minmax(160px,1fr)); gap: 18px; }
        #label-detail-page .label-release-card { position: relative; }
        #label-detail-page .label-release-card.clickable { cursor: pointer; }
        #label-detail-page .label-release-coverwrap { position: relative; width: 100%; aspect-ratio: 1/1; border-radius: 10px;
            overflow: hidden; background: rgba(255,255,255,0.05); }
        #label-detail-page .label-release-cover { width: 100%; height: 100%; object-fit: cover; display: block; }
        #label-detail-page .label-release-coverwrap.ph::after { content: '💿'; position: absolute; inset: 0;
            display: flex; align-items: center; justify-content: center; font-size: 42px; opacity: .5; }
        #label-detail-page .label-release-card.clickable:hover .label-release-coverwrap { outline: 2px solid rgba(29,185,84,0.6); outline-offset: 2px; }
        #label-detail-page .label-owned-badge { position: absolute; top: 8px; left: 8px; font-size: 11px; font-weight: 700;
            padding: 3px 9px; border-radius: 999px; background: rgba(29,185,84,0.92); color: #fff; box-shadow: 0 2px 8px rgba(0,0,0,.4); }
        #label-detail-page .label-missing-badge { position: absolute; top: 8px; left: 8px; font-size: 11px; font-weight: 700;
            padding: 3px 9px; border-radius: 999px; background: rgba(0,0,0,0.6); color: #cfd3da; }
        #label-detail-page .label-release-title { font-size: 13px; font-weight: 600; color: var(--text-primary,#eaecef);
            margin-top: 8px; line-height: 1.3; display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden; }
        #label-detail-page .label-release-sub { font-size: 12px; color: var(--text-secondary,#8a909a); margin-top: 2px;
            overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
        @media (max-width: 640px) {
            #label-detail-page .label-detail-container { padding: 16px; }
            #label-detail-page .label-detail-hero-actions { align-items: stretch; width: 100%; }
            #label-detail-page .label-release-grid { grid-template-columns: repeat(auto-fill,minmax(108px,1fr)); gap: 12px; }
        }`;
        const style = document.createElement('style');
        style.id = 'label-detail-styles';
        style.textContent = css;
        document.head.appendChild(style);
    }

    // ---- observers -----------------------------------------------------------
    function _ensureObservers() {
        if (!_coverObserver && 'IntersectionObserver' in window) {
            _coverObserver = new IntersectionObserver((entries) => {
                entries.forEach(e => {
                    if (!e.isIntersecting) return;
                    const img = e.target;
                    const src = img.dataset.src;
                    if (src) { img.src = src; img.removeAttribute('data-src'); _coverLoaded.add(src); }
                    _coverObserver.unobserve(img);
                });
            }, { rootMargin: '300px' });
        }
        if (!_sentinelObserver && 'IntersectionObserver' in window) {
            _sentinelObserver = new IntersectionObserver((entries) => {
                if (entries.some(e => e.isIntersecting) && _hasMore && !_loading) _fetchPage();
            }, { rootMargin: '400px' });
            const sentinel = document.getElementById('label-detail-sentinel');
            if (sentinel) _sentinelObserver.observe(sentinel);
        }
    }

    // ---- watchlist + backlog -------------------------------------------------
    function _setWatchState(watching) {
        _current.watching = !!watching;
        const btn = document.getElementById('label-detail-watch-btn');
        const backlog = document.getElementById('label-detail-backlog');
        if (btn) {
            btn.hidden = false;
            btn.textContent = watching ? 'Remove from Watchlist' : 'Add to Watchlist';
            btn.classList.toggle('watching', !!watching);
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
        if (btn) btn.disabled = true;
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
            }
        } catch (e) {
            if (typeof showToast === 'function') showToast('Could not update watchlist', 'error');
        } finally {
            if (btn) btn.disabled = false;
        }
    }

    async function _setBacklog(backlog) {
        if (!_current.id || _current.backlog === backlog) return;
        _setBacklogState(backlog);   // optimistic
        try {
            const d = await fetch('/api/labels/watchlist/backlog', {
                method: 'POST', headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ musicbrainz_label_id: _current.id, backlog }),
            }).then(r => r.json()).catch(() => ({}));
            if (!d || !d.success) _setBacklogState(!backlog);   // revert on failure
        } catch (e) {
            _setBacklogState(!backlog);
        }
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
            if (token !== _reqToken) return;   // a newer label load superseded us

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
        } catch (e) { /* ownership is a nicety — never break the page */ }
    }

    // ---- render --------------------------------------------------------------
    function _visible() {
        let rows = _all.slice();
        if (_filter === 'owned') rows = rows.filter(r => _owned.has(_key(r)));
        else if (_filter === 'missing') rows = rows.filter(r => !_owned.has(_key(r)));
        if (_sort === 'oldest') rows = rows.slice().reverse();
        else if (_sort === 'artist') rows = rows.slice().sort((a, b) =>
            (a.artist || '').localeCompare(b.artist || '') || (b.year || '').localeCompare(a.year || ''));
        // 'newest' = API order (already newest-first)
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
        const rgid = rel.release_group_id || '';
        const cover = rgid ? `https://coverartarchive.org/release-group/${encodeURIComponent(rgid)}/front-250` : '';
        const isOwned = _owned.has(_key(rel));
        const checked = _checked.has(_key(rel));
        const badge = isOwned
            ? '<div class="label-owned-badge">✓ In Library</div>'
            : (checked ? '<div class="label-missing-badge">Missing</div>' : '');
        const clickable = rel.artist_id ? ' clickable' : '';
        // Already-loaded covers get a real src (browser cache, no re-observe);
        // new ones get data-src for the IntersectionObserver to lazy-load.
        const srcAttr = cover
            ? (_coverLoaded.has(cover) ? `src="${_esc(cover)}"` : `data-src="${_esc(cover)}"`)
            : '';
        const imgEl = cover
            ? `<img class="label-release-cover" alt="" ${srcAttr} onerror="this.style.display='none';this.parentElement.classList.add('ph');">`
            : '';
        return `
            <div class="label-release-card${clickable}" data-artist-id="${_esc(rel.artist_id || '')}"
                 data-artist="${_esc(rel.artist || '')}" data-album="${_esc(rel.album || '')}">
                <div class="label-release-coverwrap${cover ? '' : ' ph'}">
                    ${imgEl}
                    ${badge}
                </div>
                <div class="label-release-title">${_esc(rel.album)}</div>
                <div class="label-release-sub">${_esc(rel.artist)}${rel.year ? ' · ' + _esc(rel.year) : ''}</div>
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
        // lazy-load covers now in the DOM
        if (_coverObserver) {
            grid.querySelectorAll('img.label-release-cover[data-src]').forEach(img => _coverObserver.observe(img));
        } else {
            grid.querySelectorAll('img.label-release-cover[data-src]').forEach(img => {
                img.src = img.dataset.src; _coverLoaded.add(img.dataset.src); img.removeAttribute('data-src');
            });
        }
    }

    function _onGridClick(e) {
        const card = e.target.closest('.label-release-card.clickable');
        if (!card) return;
        const artistId = card.getAttribute('data-artist-id');
        const artist = card.getAttribute('data-artist');
        if (artistId && typeof navigateToArtistDetail === 'function') {
            navigateToArtistDetail(artistId, artist, 'musicbrainz');
        }
    }

    // ---- public hooks (called by init.js loadPageData('label-detail')) -------
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
                document.querySelectorAll('#label-detail-filters button').forEach(x =>
                    x.classList.toggle('active', x === b));
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
        _all = []; _owned = new Set(); _checked = new Set(); _coverLoaded = new Set();
        _page = 0; _hasMore = false; _loading = false; _filter = 'all'; _sort = 'newest';
        if (typeof window._labelDetailReturnTo === 'string' && window._labelDetailReturnTo) {
            _returnTo = window._labelDetailReturnTo;
        }

        // reset chrome
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
