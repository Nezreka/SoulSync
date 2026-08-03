// Manual Library Match — link source tracks (wishlist + sync history) to
// library tracks so they stop re-downloading. Moved VERBATIM out of
// library.js when the library/artist-detail pages went React: this tool is
// opened from still-vanilla markup (index.html sync-history + tools buttons),
// so it stays a classic script. Self-contained: module-scope _mlm* state,
// its own _mlmEsc escaper, inline handlers bound by name.

// ── Manual Library Match ──────────────────────────────────────────────────────

let _mlmOverlay = null;
let _mlmSelectedSource = null;
let _mlmSelectedLibrary = null;
let _mlmSourceTimer = null;
let _mlmLibraryTimer = null;

function openManualLibraryMatchTool(prefill) {
    if (_mlmOverlay) _mlmOverlay.remove();

    const overlay = document.createElement('div');
    overlay.className = 'modal-overlay';
    overlay.id = 'mlm-overlay';
    overlay.onclick = (e) => { if (e.target === overlay) _mlmClose(); };

    overlay.innerHTML = `
        <div class="playlist-modal mlm-modal">
            <div class="playlist-modal-header">
                <div class="playlist-header-content">
                    <h2>Manual Library Match</h2>
                    <div class="playlist-quick-info">
                        <span class="playlist-owner">Link source tracks to library tracks to stop re-downloads</span>
                    </div>
                </div>
                <span class="playlist-modal-close" onclick="_mlmClose()">&times;</span>
            </div>

            <div class="mlm-modal-body">
                <div class="mlm-panels">
                    <div class="mlm-panel source">
                        <div class="server-col-header">
                            <span class="server-col-icon">📋</span>
                            Source Track
                        </div>
                        <div class="mlm-panel-search-wrap">
                            <input class="mlm-search" id="mlm-source-search" placeholder="Search wishlist &amp; sync history&hellip;" oninput="_mlmSourceDebounce(this.value)">
                        </div>
                        <div class="server-col-scroll" id="mlm-source-results"><p class="mlm-hint">Type to search</p></div>
                    </div>
                    <div class="mlm-panel library">
                        <div class="server-col-header">
                            <span class="server-col-icon">🎵</span>
                            Library Track
                        </div>
                        <div class="mlm-panel-search-wrap">
                            <input class="mlm-search" id="mlm-library-search" placeholder="Search your library&hellip;" oninput="_mlmLibraryDebounce(this.value)">
                        </div>
                        <div class="server-col-scroll" id="mlm-library-results"><p class="mlm-hint">Type to search</p></div>
                    </div>
                </div>

                <div class="mlm-existing-section">
                    <div class="server-col-header mlm-matches-header">
                        Existing Matches
                        <span class="server-col-count" id="mlm-match-count"></span>
                    </div>
                    <div class="mlm-matches-wrap" id="mlm-matches-list"><p class="mlm-hint">Loading&hellip;</p></div>
                </div>
            </div>

            <div class="playlist-modal-footer">
                <div class="playlist-modal-footer-left">
                    <span id="mlm-status" class="mlm-status-msg"></span>
                </div>
                <div class="playlist-modal-footer-right">
                    <button class="playlist-modal-btn playlist-modal-btn-secondary" onclick="_mlmClose()">Cancel</button>
                    <button class="playlist-modal-btn playlist-modal-btn-primary" id="mlm-save-btn" disabled onclick="_mlmSaveMatch()">Save Match</button>
                </div>
            </div>
        </div>
    `;

    document.body.appendChild(overlay);
    _mlmOverlay = overlay;
    _mlmSelectedSource = null;
    _mlmSelectedLibrary = null;
    _mlmUpdateSaveBtn();
    _mlmLoadMatches();

    if (prefill) {
        const src = document.getElementById('mlm-source-search');
        if (src) { src.value = prefill; _mlmSourceSearch(prefill); }
    }
}

function _mlmClose() {
    if (_mlmOverlay) { _mlmOverlay.remove(); _mlmOverlay = null; }
    _mlmSelectedSource = null;
    _mlmSelectedLibrary = null;
}

function _mlmSourceDebounce(q) {
    clearTimeout(_mlmSourceTimer);
    _mlmSourceTimer = setTimeout(() => _mlmSourceSearch(q), 300);
}
function _mlmLibraryDebounce(q) {
    clearTimeout(_mlmLibraryTimer);
    _mlmLibraryTimer = setTimeout(() => _mlmLibrarySearch(q), 300);
}

async function _mlmSourceSearch(q) {
    const el = document.getElementById('mlm-source-results');
    if (!el) return;
    if (!q.trim()) { el.innerHTML = '<p class="mlm-hint">Type to search</p>'; return; }
    el.innerHTML = '<p class="mlm-hint">Searching&hellip;</p>';
    try {
        const res = await fetch(`/api/manual-library-matches/source-search?q=${encodeURIComponent(q)}&limit=15`);
        const data = await res.json();
        _mlmRenderSourceResults(data.tracks || []);
    } catch (e) { el.innerHTML = '<p class="mlm-hint mlm-error">Search failed</p>'; }
}

async function _mlmLibrarySearch(q) {
    const el = document.getElementById('mlm-library-results');
    if (!el) return;
    if (!q.trim()) { el.innerHTML = '<p class="mlm-hint">Type to search</p>'; return; }
    el.innerHTML = '<p class="mlm-hint">Searching&hellip;</p>';
    try {
        const res = await fetch(`/api/manual-library-matches/library-search?q=${encodeURIComponent(q)}&limit=15`);
        const data = await res.json();
        _mlmRenderLibraryResults(data.tracks || []);
    } catch (e) { el.innerHTML = '<p class="mlm-hint mlm-error">Search failed</p>'; }
}

function _mlmEsc(str) {
    return String(str || '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}

function _mlmRenderSourceResults(tracks) {
    const el = document.getElementById('mlm-source-results');
    if (!el) return;
    if (!tracks.length) { el.innerHTML = '<p class="mlm-hint">No results</p>'; return; }
    el.innerHTML = tracks.map((t, i) => {
        const sel = _mlmSelectedSource && _mlmSelectedSource.source_track_id === t.source_track_id ? 'mlm-row-selected' : '';
        return `<div class="mlm-result-row ${sel}" data-idx="${i}" onclick="_mlmSelectSource(${i})">
            <div class="mlm-row-title">${_mlmEsc(t.title || '—')}</div>
            <div class="mlm-row-sub">${_mlmEsc(t.artist || '')}${t.album ? ' · ' + _mlmEsc(t.album) : ''}</div>
            <div class="mlm-row-ctx">${_mlmEsc(t.context || t.source || '')}</div>
        </div>`;
    }).join('');
    el._mlmTracks = tracks;
}

function _mlmRenderLibraryResults(tracks) {
    const el = document.getElementById('mlm-library-results');
    if (!el) return;
    if (!tracks.length) { el.innerHTML = '<p class="mlm-hint">No results</p>'; return; }
    el.innerHTML = tracks.map((t, i) => {
        const sel = _mlmSelectedLibrary && _mlmSelectedLibrary.id === t.id ? 'mlm-row-selected' : '';
        const path = t.file_path ? t.file_path.split(/[/\\]/).pop() : '';
        return `<div class="mlm-result-row ${sel}" data-idx="${i}" onclick="_mlmSelectLibrary(${i})">
            <div class="mlm-row-title">${_mlmEsc(t.title || '—')}</div>
            <div class="mlm-row-sub">${_mlmEsc(t.artist_name || '')}${t.album_title ? ' · ' + _mlmEsc(t.album_title) : ''}</div>
            <div class="mlm-row-ctx">${_mlmEsc(path)}${t.bitrate ? ' · ' + t.bitrate + 'kbps' : ''}</div>
        </div>`;
    }).join('');
    el._mlmTracks = tracks;
}

function _mlmSelectSource(idx) {
    const el = document.getElementById('mlm-source-results');
    if (!el || !el._mlmTracks) return;
    _mlmSelectedSource = el._mlmTracks[idx];
    el.querySelectorAll('.mlm-result-row').forEach((r, i) => r.classList.toggle('mlm-row-selected', i === idx));
    _mlmUpdateSaveBtn();
}

function _mlmSelectLibrary(idx) {
    const el = document.getElementById('mlm-library-results');
    if (!el || !el._mlmTracks) return;
    _mlmSelectedLibrary = el._mlmTracks[idx];
    el.querySelectorAll('.mlm-result-row').forEach((r, i) => r.classList.toggle('mlm-row-selected', i === idx));
    _mlmUpdateSaveBtn();
}

function _mlmUpdateSaveBtn() {
    const btn = document.getElementById('mlm-save-btn');
    if (btn) btn.disabled = !(_mlmSelectedSource && _mlmSelectedLibrary);
}

async function _mlmSaveMatch() {
    if (!_mlmSelectedSource || !_mlmSelectedLibrary) return;
    const status = document.getElementById('mlm-status');
    if (status) status.textContent = 'Saving…';
    try {
        const body = {
            source: _mlmSelectedSource.source,
            source_track_id: _mlmSelectedSource.source_track_id,
            library_track_id: _mlmSelectedLibrary.id,
            source_title: _mlmSelectedSource.title || '',
            source_artist: _mlmSelectedSource.artist || '',
            source_album: _mlmSelectedSource.album || '',
            source_context_json: '',
            server_source: '',
        };
        const res = await fetch('/api/manual-library-matches', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(body),
        });
        const data = await res.json();
        if (data.success) {
            if (status) status.textContent = 'Saved!';
            _mlmSelectedSource = null;
            _mlmSelectedLibrary = null;
            _mlmUpdateSaveBtn();
            await _mlmLoadMatches();
            setTimeout(() => { if (status) status.textContent = ''; }, 2000);
        } else {
            if (status) status.textContent = 'Error: ' + (data.error || 'unknown');
        }
    } catch (e) {
        if (status) status.textContent = 'Network error';
    }
}

async function _mlmLoadMatches() {
    const el = document.getElementById('mlm-matches-list');
    if (!el) return;
    try {
        const res = await fetch('/api/manual-library-matches');
        const data = await res.json();
        const matches = data.matches || [];
        const countEl = document.getElementById('mlm-match-count');
        if (countEl) countEl.textContent = matches.length;
        if (!matches.length) {
            el.innerHTML = '<p class="mlm-hint">No matches saved yet</p>';
            return;
        }
        el.innerHTML = `<table class="mlm-matches-table">
            <thead><tr><th>Source Track</th><th>Library Track</th><th>Source</th><th></th></tr></thead>
            <tbody>${matches.map(m => `<tr>
                <td><div class="mlm-row-title">${_mlmEsc(m.source_title || m.source_track_id)}</div><div class="mlm-row-sub">${_mlmEsc(m.source_artist || '')}</div></td>
                <td><div class="mlm-row-title">${_mlmEsc(m.library_title || String(m.library_track_id))}</div><div class="mlm-row-sub">${_mlmEsc(m.library_artist || '')}</div></td>
                <td><span class="mlm-source-badge">${_mlmEsc(m.source)}</span></td>
                <td><button class="mlm-remove-btn" onclick="_mlmDeleteMatch(${m.id})" title="Remove match">&#x2715;</button></td>
            </tr>`).join('')}</tbody>
        </table>`;
    } catch (e) {
        el.innerHTML = '<p class="mlm-hint mlm-error">Failed to load matches</p>';
    }
}

async function _mlmDeleteMatch(id) {
    try {
        await fetch(`/api/manual-library-matches/${id}`, { method: 'DELETE' });
        await _mlmLoadMatches();
    } catch (e) {
        if (typeof showToast === 'function') showToast('Failed to remove match', 'error');
    }
}
