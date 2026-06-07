// ── Blocklist modal ──
// A proper artist/album/track blacklist. Search the active metadata source,
// block by ID (cross-source matched in the background so a ban survives a
// source switch), and manage existing bans. Opened from the Watchlist page.
// Distinct from the download-source blacklist.

let _blEntityType = 'artist';      // active tab: artist | album | track
let _blSearchSeq = 0;              // guards against out-of-order search results

function openBlocklistModal(initialType) {
    _blEntityType = ['artist', 'album', 'track'].includes(initialType) ? initialType : 'artist';
    let overlay = document.getElementById('blocklist-modal-overlay');
    if (!overlay) {
        overlay = document.createElement('div');
        overlay.id = 'blocklist-modal-overlay';
        overlay.className = 'modal-overlay blocklist-modal-overlay';
        overlay.innerHTML = `
            <div class="blocklist-modal">
                <div class="blocklist-modal-head">
                    <div>
                        <h2 class="blocklist-modal-title">Blocklist</h2>
                        <p class="blocklist-modal-sub">Block an artist, album, or track from ever being downloaded. Matched across all your metadata sources.</p>
                    </div>
                    <button class="blocklist-modal-close" onclick="closeBlocklistModal()" aria-label="Close">✕</button>
                </div>
                <div class="blocklist-tabs">
                    <button class="blocklist-tab" data-bl="artist" onclick="switchBlocklistTab('artist')">Artists</button>
                    <button class="blocklist-tab" data-bl="album" onclick="switchBlocklistTab('album')">Albums</button>
                    <button class="blocklist-tab" data-bl="track" onclick="switchBlocklistTab('track')">Tracks</button>
                </div>
                <div class="blocklist-search-row">
                    <input type="text" id="blocklist-search-input" class="blocklist-search-input"
                           placeholder="Search to block…" oninput="onBlocklistSearchInput()">
                    <div class="blocklist-search-spinner" id="blocklist-search-spinner"></div>
                </div>
                <div class="blocklist-search-results" id="blocklist-search-results"></div>
                <div class="blocklist-current-label">Currently blocked</div>
                <div class="blocklist-current" id="blocklist-current"></div>
            </div>`;
        overlay.addEventListener('click', (e) => { if (e.target === overlay) closeBlocklistModal(); });
        document.body.appendChild(overlay);
    }
    overlay.classList.remove('hidden');
    _blRefreshTabs();
    _blLoadCurrent();
    const input = document.getElementById('blocklist-search-input');
    if (input) { input.value = ''; input.focus(); }
    document.getElementById('blocklist-search-results').innerHTML = '';
}

function closeBlocklistModal() {
    const o = document.getElementById('blocklist-modal-overlay');
    if (o) o.classList.add('hidden');
}

function switchBlocklistTab(type) {
    if (type === _blEntityType) return;
    _blEntityType = type;
    _blRefreshTabs();
    const input = document.getElementById('blocklist-search-input');
    if (input) input.value = '';
    document.getElementById('blocklist-search-results').innerHTML = '';
    _blLoadCurrent();
}

function _blRefreshTabs() {
    document.querySelectorAll('.blocklist-tab').forEach(b =>
        b.classList.toggle('active', b.dataset.bl === _blEntityType));
    const input = document.getElementById('blocklist-search-input');
    if (input) input.placeholder = `Search ${_blEntityType}s to block…`;
}

let _blSearchTimer = null;
function onBlocklistSearchInput() {
    clearTimeout(_blSearchTimer);
    _blSearchTimer = setTimeout(_blRunSearch, 300);   // debounce
}

async function _blRunSearch() {
    const input = document.getElementById('blocklist-search-input');
    const box = document.getElementById('blocklist-search-results');
    const spinner = document.getElementById('blocklist-search-spinner');
    const q = (input.value || '').trim();
    if (!q) { box.innerHTML = ''; return; }
    const seq = ++_blSearchSeq;
    spinner.classList.add('spinning');
    try {
        const res = await fetch(`/api/blocklist/search?type=${_blEntityType}&q=${encodeURIComponent(q)}`);
        const data = await res.json();
        if (seq !== _blSearchSeq) return;             // a newer search superseded this
        if (!data.success) throw new Error(data.error || 'Search failed');
        const results = data.results || [];
        if (!results.length) {
            box.innerHTML = '<div class="blocklist-empty">No matches.</div>';
            return;
        }
        box.innerHTML = results.map(r => {
            const img = r.image
                ? `<img class="blocklist-row-img${_blEntityType === 'artist' ? ' artist' : ''}" src="${escapeHtml(r.image)}" alt="" loading="lazy" onerror="this.style.visibility='hidden'">`
                : `<div class="blocklist-row-img${_blEntityType === 'artist' ? ' artist' : ''} placeholder">🎵</div>`;
            const payload = encodeURIComponent(JSON.stringify({
                name: r.name || 'Unknown', source: r.provider || data.source,
                source_id: r.id, parent_name: r.extra || ''
            }));
            return `<div class="blocklist-row">
                ${img}
                <div class="blocklist-row-info">
                    <div class="blocklist-row-name">${escapeHtml(r.name || 'Unknown')}</div>
                    ${r.extra ? `<div class="blocklist-row-extra">${escapeHtml(r.extra)}</div>` : ''}
                </div>
                <button class="blocklist-block-btn" onclick="blockFromSearch('${payload}')">Block</button>
            </div>`;
        }).join('');
    } catch (e) {
        if (seq === _blSearchSeq) box.innerHTML = `<div class="blocklist-empty">Couldn't search: ${escapeHtml(e.message)}</div>`;
    } finally {
        if (seq === _blSearchSeq) spinner.classList.remove('spinning');
    }
}

async function blockFromSearch(payloadEnc) {
    let p;
    try { p = JSON.parse(decodeURIComponent(payloadEnc)); } catch (e) { return; }
    try {
        const res = await fetch('/api/blocklist', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ entity_type: _blEntityType, ...p }),
        });
        const data = await res.json();
        if (data.success) {
            showToast(`Blocked ${_blEntityType}: ${p.name}`, 'success');
            const input = document.getElementById('blocklist-search-input');
            if (input) input.value = '';
            document.getElementById('blocklist-search-results').innerHTML = '';
            _blLoadCurrent();
        } else {
            showToast(data.error || 'Failed to block', 'error');
        }
    } catch (e) {
        showToast('Error blocking item', 'error');
    }
}

async function _blLoadCurrent() {
    const box = document.getElementById('blocklist-current');
    box.innerHTML = '<div class="blocklist-empty">Loading…</div>';
    try {
        const res = await fetch(`/api/blocklist?entity_type=${_blEntityType}`);
        const data = await res.json();
        if (!data.success) throw new Error(data.error || 'Failed to load');
        const entries = data.entries || [];
        if (!entries.length) {
            box.innerHTML = `<div class="blocklist-empty">No blocked ${_blEntityType}s yet.</div>`;
            return;
        }
        box.innerHTML = entries.map(e => {
            const sources = ['spotify_id', 'itunes_id', 'deezer_id', 'musicbrainz_id']
                .filter(k => e[k]).length;
            const matchTag = e.match_status === 'matched' || sources >= 2
                ? `<span class="blocklist-match matched" title="Matched across ${sources} sources">${sources}★</span>`
                : `<span class="blocklist-match pending" title="Matching other sources…">●</span>`;
            return `<div class="blocklist-current-row">
                <div class="blocklist-row-info">
                    <div class="blocklist-row-name">${escapeHtml(e.name)}</div>
                    ${e.parent_name ? `<div class="blocklist-row-extra">${escapeHtml(e.parent_name)}</div>` : ''}
                </div>
                ${matchTag}
                <button class="blocklist-unblock-btn" onclick="unblockEntry(${e.id})" title="Remove">✕</button>
            </div>`;
        }).join('');
    } catch (e) {
        box.innerHTML = `<div class="blocklist-empty">Couldn't load: ${escapeHtml(e.message)}</div>`;
    }
}

async function unblockEntry(id) {
    try {
        const res = await fetch(`/api/blocklist/${id}`, { method: 'DELETE' });
        const data = await res.json();
        if (data.success) { showToast('Removed from blocklist', 'success'); _blLoadCurrent(); }
        else showToast(data.error || 'Failed to remove', 'error');
    } catch (e) {
        showToast('Error removing entry', 'error');
    }
}
