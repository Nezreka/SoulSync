// ── Download Origins modal ──
// "What did the watchlist / playlist syncs download?" One modal, two tabs,
// opened from the Watchlist page (watchlist tab) and the Sync page (playlists
// tab). Entries come from library_history rows stamped with origin provenance
// at the import chokepoint (core/downloads/origin.py). Delete removes the
// file on disk, the library track row, and the history entries.

let _originModalEl = null;
let _originActiveTab = 'watchlist';
let _originEntries = [];
let _originSelected = new Set();

function openDownloadOriginsModal(tab) {
    _originActiveTab = tab === 'playlist' ? 'playlist' : 'watchlist';
    _originSelected = new Set();
    if (!_originModalEl) {
        _originModalEl = document.createElement('div');
        _originModalEl.className = 'modal-overlay origin-modal-overlay';
        _originModalEl.innerHTML = `
            <div class="origin-modal">
                <div class="origin-modal-head">
                    <div>
                        <h2 class="origin-modal-title">Download Origins</h2>
                        <p class="origin-modal-sub">What your watchlist and playlist syncs have downloaded.</p>
                    </div>
                    <button class="origin-modal-close" onclick="closeDownloadOriginsModal()" aria-label="Close">✕</button>
                </div>
                <div class="origin-modal-tabs">
                    <button class="origin-tab" data-tab="watchlist" onclick="switchDownloadOriginTab('watchlist')">
                        Watchlist <span class="origin-tab-count" id="origin-count-watchlist"></span>
                    </button>
                    <button class="origin-tab" data-tab="playlist" onclick="switchDownloadOriginTab('playlist')">
                        Playlists <span class="origin-tab-count" id="origin-count-playlist"></span>
                    </button>
                    <div class="origin-toolbar">
                        <label class="origin-select-all">
                            <input type="checkbox" id="origin-select-all" onchange="toggleAllOriginEntries(this.checked)"> All
                        </label>
                        <button class="origin-delete-btn" id="origin-delete-selected"
                                onclick="deleteSelectedOriginEntries()" disabled>Delete Selected</button>
                    </div>
                </div>
                <div class="origin-modal-body" id="origin-modal-body"></div>
            </div>`;
        _originModalEl.addEventListener('click', (e) => {
            if (e.target === _originModalEl) closeDownloadOriginsModal();
        });
        document.body.appendChild(_originModalEl);
    }
    _originModalEl.classList.remove('hidden');
    _refreshOriginTabs();
    _loadOriginEntries();
}

function closeDownloadOriginsModal() {
    if (_originModalEl) _originModalEl.classList.add('hidden');
}

function switchDownloadOriginTab(tab) {
    if (tab === _originActiveTab) return;
    _originActiveTab = tab;
    _originSelected = new Set();
    _refreshOriginTabs();
    _loadOriginEntries();
}

function _refreshOriginTabs() {
    _originModalEl.querySelectorAll('.origin-tab').forEach(btn => {
        btn.classList.toggle('active', btn.dataset.tab === _originActiveTab);
    });
    const selAll = document.getElementById('origin-select-all');
    if (selAll) selAll.checked = false;
    _updateOriginDeleteButton();
}

async function _loadOriginEntries() {
    const body = document.getElementById('origin-modal-body');
    body.innerHTML = '<div class="origin-modal-loading">Loading…</div>';
    try {
        const resp = await fetch(`/api/download-origins?origin=${_originActiveTab}&limit=500`);
        const data = await resp.json();
        if (!data.success) throw new Error(data.error || 'Failed to load');
        _originEntries = data.entries || [];
        const countEl = document.getElementById(`origin-count-${_originActiveTab}`);
        if (countEl) countEl.textContent = data.total ? `(${data.total})` : '';
        _renderOriginEntries();
    } catch (err) {
        body.innerHTML = `<div class="origin-modal-empty">Couldn't load: ${escapeHtml(err.message)}</div>`;
    }
}

function _renderOriginEntries() {
    const body = document.getElementById('origin-modal-body');
    if (!_originEntries.length) {
        const what = _originActiveTab === 'watchlist'
            ? 'No watchlist-triggered downloads recorded yet. New watchlist downloads will appear here.'
            : 'No playlist-triggered downloads recorded yet. New playlist sync downloads will appear here.';
        body.innerHTML = `<div class="origin-modal-empty">${what}</div>`;
        return;
    }
    const ctxLabel = _originActiveTab === 'watchlist' ? 'Watchlist artist' : 'Playlist';

    const entryRow = (e) => {
        const checked = _originSelected.has(e.id) ? 'checked' : '';
        const thumb = e.thumb_url
            ? `<img class="library-history-thumb" src="${escapeHtml(e.thumb_url)}" alt="" loading="lazy"
                    onerror="this.outerHTML='<div class=\\'library-history-thumb-placeholder\\'>🎵</div>'">`
            : '<div class="library-history-thumb-placeholder">🎵</div>';
        const fname = (e.file_path || '').split(/[\\/]/).pop();
        return `<div class="library-history-entry origin-entry" data-id="${e.id}">
            <input type="checkbox" class="origin-entry-check" ${checked}
                   onchange="toggleOriginEntry(${e.id}, this.checked)">
            ${thumb}
            <div class="library-history-entry-content">
                <div class="library-history-entry-row1">
                    <div class="library-history-entry-text">
                        <div class="library-history-entry-title">${escapeHtml(e.title || 'Unknown')}</div>
                        <div class="library-history-entry-meta">${escapeHtml(e.artist_name || '')}${e.album_name ? ' — ' + escapeHtml(e.album_name) : ''}</div>
                    </div>
                    ${e.quality ? `<span class="library-history-badge">${escapeHtml(e.quality)}</span>` : ''}
                    <div class="library-history-entry-time">${escapeHtml(_originFormatTime(e.created_at))}</div>
                    <button class="lh-audit-btn origin-row-delete" title="Delete this file + entry"
                            onclick="deleteSelectedOriginEntries(${e.id})">Delete</button>
                </div>
                ${fname ? `<div class="library-history-entry-source"><span class="lh-prov-label">File:</span> ${escapeHtml(fname)}</div>` : ''}
            </div>
        </div>`;
    };

    // #831: group entries by what triggered them (watchlist artist / playlist
    // name) instead of a flat list with a per-row badge. Entries arrive
    // newest-first, so groups order themselves by their newest download.
    const groups = new Map();
    for (const e of _originEntries) {
        const key = e.origin_context || '—';
        if (!groups.has(key)) groups.set(key, []);
        groups.get(key).push(e);
    }

    body.innerHTML = Array.from(groups.entries()).map(([ctx, entries]) => `
        <div class="origin-group">
            <button type="button" class="origin-group-header" onclick="toggleOriginGroup(this)" title="${ctxLabel}">
                <span class="origin-group-caret">▾</span>
                <span class="origin-group-name">${escapeHtml(ctx)}</span>
                <span class="origin-group-count">${entries.length} track${entries.length !== 1 ? 's' : ''}</span>
            </button>
            <div class="origin-group-body">${entries.map(entryRow).join('')}</div>
        </div>`).join('');
    _updateOriginDeleteButton();
}

function toggleOriginGroup(btn) {
    const bodyEl = btn.parentElement.querySelector('.origin-group-body');
    const caret = btn.querySelector('.origin-group-caret');
    if (!bodyEl) return;
    const open = bodyEl.style.display !== 'none';
    bodyEl.style.display = open ? 'none' : '';
    if (caret) caret.textContent = open ? '▸' : '▾';
}

function toggleOriginEntry(id, on) {
    if (on) _originSelected.add(id); else _originSelected.delete(id);
    _updateOriginDeleteButton();
}

function toggleAllOriginEntries(on) {
    _originSelected = on ? new Set(_originEntries.map(e => e.id)) : new Set();
    _originModalEl.querySelectorAll('.origin-entry-check').forEach(cb => { cb.checked = on; });
    _updateOriginDeleteButton();
}

function _updateOriginDeleteButton() {
    const btn = document.getElementById('origin-delete-selected');
    if (!btn) return;
    btn.disabled = _originSelected.size === 0;
    btn.textContent = _originSelected.size ? `Delete Selected (${_originSelected.size})` : 'Delete Selected';
}

async function deleteSelectedOriginEntries(singleId) {
    const ids = singleId !== undefined ? [singleId] : [..._originSelected];
    if (!ids.length) return;
    const what = ids.length === 1 ? 'this track' : `these ${ids.length} tracks`;
    if (!confirm(`Delete ${what}? This removes the audio file(s) from disk and the library entry.`)) return;
    try {
        const resp = await fetch('/api/download-origins/delete', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ ids, delete_files: true }),
        });
        const data = await resp.json();
        if (!data.success) throw new Error(data.error || 'Delete failed');
        let msg = `Removed ${data.removed} entr${data.removed === 1 ? 'y' : 'ies'}`;
        if (data.files_deleted) msg += `, deleted ${data.files_deleted} file(s)`;
        if (data.files_missing) msg += ` (${data.files_missing} already gone)`;
        showToast(msg, data.errors && data.errors.length ? 'warning' : 'success');
        if (data.errors && data.errors.length) console.warn('Origin delete errors:', data.errors);
        _originSelected = new Set();
        _loadOriginEntries();
    } catch (err) {
        showToast(`Delete failed: ${err.message}`, 'error');
    }
}

function _originFormatTime(ts) {
    if (!ts) return '';
    try {
        // SQLite CURRENT_TIMESTAMP is UTC without a zone marker.
        const d = new Date(String(ts).includes('T') ? ts : ts.replace(' ', 'T') + 'Z');
        if (isNaN(d.getTime())) return ts;
        return d.toLocaleString(undefined, {
            month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit',
        });
    } catch (e) {
        return ts;
    }
}
