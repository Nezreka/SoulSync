// ============================================================================
// LABEL DETAIL PAGE
// ----------------------------------------------------------------------------
// A record label's catalog — distinct albums grouped by their REAL artist,
// monitored like the artist watchlist. Purely additive + self-contained: it
// only calls the /api/labels/* blueprint (Labels P2a) and owns its own DOM +
// scoped styles. No dependency on library.js internals, so it can't disturb
// the artist-detail flow it visually resembles.
//
// Reached via navigateToLabelDetail(id, name) (init.js); the MBID also rides
// the URL query so a reload / browser-back re-resolves it.
// ============================================================================

(function () {
    'use strict';

    let _wired = false;
    let _current = { id: null, name: '', watching: false };

    function _esc(s) {
        if (typeof escapeHtml === 'function') return escapeHtml(s == null ? '' : String(s));
        const d = document.createElement('div');
        d.textContent = s == null ? '' : String(s);
        return d.innerHTML;
    }

    function _injectStyles() {
        if (document.getElementById('label-detail-styles')) return;
        const css = `
        #label-detail-page .label-detail-container { padding: 24px 28px; max-width: 1400px; margin: 0 auto; }
        #label-detail-page .label-detail-back {
            background: rgba(255,255,255,0.06); color: var(--text-secondary,#9aa0aa);
            border: 1px solid rgba(255,255,255,0.08); border-radius: 8px;
            padding: 7px 14px; cursor: pointer; font-size: 13px; margin-bottom: 18px; }
        #label-detail-page .label-detail-back:hover { background: rgba(255,255,255,0.12); color: #fff; }
        #label-detail-page .label-detail-title-row { display: flex; align-items: center; gap: 14px; flex-wrap: wrap; }
        #label-detail-page .label-detail-icon { font-size: 34px; }
        #label-detail-page .label-detail-name { font-size: 30px; font-weight: 800; margin: 0; color: var(--text-primary,#fff); }
        #label-detail-page .label-detail-follow-btn {
            margin-left: auto; padding: 9px 20px; border-radius: 999px; cursor: pointer;
            font-size: 14px; font-weight: 700; border: none;
            background: linear-gradient(135deg,#1db954,#12833b); color: #fff; transition: filter .15s, background .15s; }
        #label-detail-page .label-detail-follow-btn:hover { filter: brightness(1.08); }
        #label-detail-page .label-detail-follow-btn.following {
            background: rgba(255,255,255,0.08); color: var(--text-secondary,#cfd3da); border: 1px solid rgba(255,255,255,0.16); }
        #label-detail-page .label-detail-meta { color: var(--text-secondary,#9aa0aa); font-size: 13px; margin-top: 8px; }
        #label-detail-page .label-detail-status { color: var(--text-secondary,#9aa0aa); padding: 40px 0; text-align: center; font-size: 15px; }
        #label-detail-page .label-artist-group { margin-top: 30px; }
        #label-detail-page .label-artist-group-head { display: flex; align-items: baseline; gap: 10px; margin-bottom: 14px; }
        #label-detail-page .label-artist-group-name { font-size: 19px; font-weight: 700; color: var(--text-primary,#fff); margin: 0; }
        #label-detail-page .label-artist-group-count { font-size: 12px; color: var(--text-secondary,#8a909a); }
        #label-detail-page .label-release-grid {
            display: grid; grid-template-columns: repeat(auto-fill, minmax(150px,1fr)); gap: 16px; }
        #label-detail-page .label-release-card { display: flex; flex-direction: column; gap: 8px; }
        #label-detail-page .label-release-cover {
            width: 100%; aspect-ratio: 1/1; border-radius: 8px; object-fit: cover;
            background: rgba(255,255,255,0.05); display: flex; align-items: center; justify-content: center; font-size: 40px; }
        #label-detail-page .label-release-title { font-size: 13px; font-weight: 600; color: var(--text-primary,#eaecef); line-height: 1.3;
            display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden; }
        #label-detail-page .label-release-year { font-size: 12px; color: var(--text-secondary,#8a909a); }
        @media (max-width: 640px) {
            #label-detail-page .label-detail-container { padding: 16px; }
            #label-detail-page .label-release-grid { grid-template-columns: repeat(auto-fill, minmax(110px,1fr)); gap: 12px; }
        }`;
        const style = document.createElement('style');
        style.id = 'label-detail-styles';
        style.textContent = css;
        document.head.appendChild(style);
    }

    function _setFollowState(watching) {
        _current.watching = !!watching;
        const btn = document.getElementById('label-detail-follow-btn');
        if (!btn) return;
        btn.hidden = false;
        btn.textContent = watching ? 'Following' : 'Follow';
        btn.classList.toggle('following', !!watching);
    }

    async function _toggleFollow() {
        if (!_current.id) return;
        const btn = document.getElementById('label-detail-follow-btn');
        if (btn) btn.disabled = true;
        try {
            if (_current.watching) {
                const r = await fetch('/api/labels/watchlist/remove', {
                    method: 'POST', headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ musicbrainz_label_id: _current.id }),
                });
                const d = await r.json().catch(() => ({}));
                if (d && d.success) _setFollowState(false);
            } else {
                const r = await fetch('/api/labels/watchlist/add', {
                    method: 'POST', headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ musicbrainz_label_id: _current.id, label_name: _current.name }),
                });
                const d = await r.json().catch(() => ({}));
                if (d && d.success) _setFollowState(true);
            }
            if (typeof updateWatchlistButtonCount === 'function') {
                try { updateWatchlistButtonCount(); } catch (e) { /* non-fatal */ }
            }
        } catch (e) {
            if (typeof showToast === 'function') showToast('Could not update label watchlist', 'error');
        } finally {
            if (btn) btn.disabled = false;
        }
    }

    function _releaseCardHtml(rel) {
        const rgid = rel.release_group_id || '';
        const cover = rgid
            ? `https://coverartarchive.org/release-group/${encodeURIComponent(rgid)}/front-250`
            : '';
        const placeholder = `<div class="label-release-cover">💿</div>`;
        const escFallback = placeholder.replace(/"/g, '&quot;');
        const img = cover
            ? `<img src="${_esc(cover)}" class="label-release-cover" alt="${_esc(rel.album)}" loading="lazy" onerror="this.outerHTML='${escFallback}'">`
            : placeholder;
        return `
            <div class="label-release-card">
                ${img}
                <div class="label-release-title">${_esc(rel.album)}</div>
                <div class="label-release-year">${_esc(rel.year || '—')}</div>
            </div>`;
    }

    function _renderGroups(groups) {
        const host = document.getElementById('label-detail-groups');
        const empty = document.getElementById('label-detail-empty');
        if (!host) return;
        host.innerHTML = '';
        if (!groups || !groups.length) {
            if (empty) empty.classList.remove('hidden');
            return;
        }
        if (empty) empty.classList.add('hidden');
        const frag = document.createDocumentFragment();
        groups.forEach(g => {
            const section = document.createElement('div');
            section.className = 'label-artist-group';
            const releases = g.releases || [];
            section.innerHTML = `
                <div class="label-artist-group-head">
                    <h3 class="label-artist-group-name">${_esc(g.artist)}</h3>
                    <span class="label-artist-group-count">${releases.length} release${releases.length === 1 ? '' : 's'}</span>
                </div>
                <div class="label-release-grid">${releases.map(_releaseCardHtml).join('')}</div>`;
            frag.appendChild(section);
        });
        host.appendChild(frag);
    }

    // --- public (global) hooks called by init.js loadPageData('label-detail') ---

    window.initializeLabelDetailPage = function initializeLabelDetailPage() {
        _injectStyles();
        if (_wired) return;
        const btn = document.getElementById('label-detail-follow-btn');
        if (btn) btn.addEventListener('click', _toggleFollow);
        _wired = true;
    };

    window.loadLabelDetailData = async function loadLabelDetailData(labelId, labelName) {
        if (!labelId) return;
        _current = { id: String(labelId), name: labelName || '', watching: false };

        const nameEl = document.getElementById('label-detail-name');
        const metaEl = document.getElementById('label-detail-meta');
        const loadingEl = document.getElementById('label-detail-loading');
        const emptyEl = document.getElementById('label-detail-empty');
        const groupsEl = document.getElementById('label-detail-groups');
        const btn = document.getElementById('label-detail-follow-btn');

        if (nameEl) nameEl.textContent = labelName || 'Label';
        if (metaEl) metaEl.textContent = '';
        if (emptyEl) emptyEl.classList.add('hidden');
        if (groupsEl) groupsEl.innerHTML = '';
        if (loadingEl) loadingEl.classList.remove('hidden');
        if (btn) btn.hidden = true;

        try {
            const url = `/api/labels/${encodeURIComponent(labelId)}/catalog`
                + (labelName ? `?name=${encodeURIComponent(labelName)}` : '');
            const r = await fetch(url);
            const data = await r.json().catch(() => ({}));
            if (String(_current.id) !== String(labelId)) return;  // navigated away

            const resolvedName = (data.label && data.label.name) || labelName || 'Label';
            _current.name = resolvedName;
            if (nameEl) nameEl.textContent = resolvedName;
            if (metaEl) {
                const n = data.release_count || (data.groups || []).reduce((a, g) => a + (g.releases || []).length, 0);
                metaEl.textContent = `${n} album${n === 1 ? '' : 's'} across ${(data.groups || []).length} artist${(data.groups || []).length === 1 ? '' : 's'}`;
            }
            _renderGroups(data.groups || []);
            _setFollowState(!!data.is_watching);
        } catch (e) {
            if (loadingEl) loadingEl.textContent = 'Could not load this label’s catalog.';
            return;
        } finally {
            if (loadingEl && String(_current.id) === String(labelId)) loadingEl.classList.add('hidden');
        }
    };
})();
