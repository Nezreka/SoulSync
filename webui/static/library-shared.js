// Helpers that outlived the legacy Library page.
//
// The old vanilla-JS Library (`library.js`, plus the `#library-page` and
// `#artist-detail-page` markup) is gone: Library V2 is the Library now. A
// handful of things that happened to live in that file are used by pages that
// are still here, so they moved here instead of disappearing with it:
//
// - `_esc`            — HTML escaping, used across auto-sync, discover and
//                       pages-extra;
// - `playLibraryTrack`— starts playback of an owned file, called from search,
//                       downloads, stats and the React library;
// - `navigateToArtistDetail` — the one entry point every caller uses to open an
//                       artist; it now just navigates to the /artist-detail URL,
//                       which the React router resolves into the library;
// - Manual Library Match — reached from the Sync page and the Tools page, never
//                       from the library page itself.
//
// Nothing here should grow: new library behaviour belongs in the React app.

function _esc(s) { const d = document.createElement('div'); d.textContent = s; return d.innerHTML; }

/** Open an artist.
 *
 * Every caller (search results, global search, the media player, playlist sync,
 * label pages, similar-artist bubbles) goes through this one function, and it
 * has exactly one job left: navigate to the canonical /artist-detail/:source/:id
 * URL. The React router turns that into the library — owned artists open their
 * catalogue page, unowned ones open discovery — so nothing here needs to know
 * which of the two it is. `artistId` is opaque: a legacy library id or a
 * provider id, never parsed.
 */
function navigateToArtistDetail(artistId, artistName, sourceOverride = null, options = {}) {
    if (!artistId) return;
    if (typeof navigateToPage !== 'function') return;
    navigateToPage('artist-detail', {
        artistId,
        artistName: artistName || '',
        artistSource: sourceOverride || null,
        skipRouteChange: options.skipRouteChange === true,
    });
}

/** Hand a query from anywhere to the enhanced search on the Search page. */
function _handoffLibrarySearchToEnhancedSearch(query) {
    if (typeof navigateToPage !== 'function') return;
    navigateToPage('search');
    setTimeout(() => {
        const input = document.getElementById('enhanced-search-input');
        if (input && query) {
            input.value = query;
            input.dispatchEvent(new Event('input', { bubbles: true }));
        }
    }, 300);
}

async function playLibraryTrack(track, albumTitle, artistName) {
    if (!track.file_path) {
        showToast('No file available for this track', 'error');
        return;
    }

    // Library tracks have authoritative metadata in the SoulSync DB —
    // any title / artist / album the caller passes in is downstream of
    // whatever modal triggered playback and may carry noise like the
    // ``<source_id>||<display>`` filename prefix from a Prowlarr result.
    // When the caller has a track.id, fetch the canonical row from
    // resolve-track and overwrite the caller-supplied fields with the
    // DB values. Falls back silently to the caller-supplied values on
    // any error so we never lose the play action over a metadata fetch.
    if ((track.legacy_track_id || (track.id && !track.lib2_track_id)) &&
            (track.title || track.name) && (artistName || track.artist_name)) {
        try {
            const _dbResp = await fetch('/api/stats/resolve-track', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    title: track.title || track.name,
                    artist: artistName || track.artist_name || '',
                }),
            });
            const _dbData = await _dbResp.json();
            if (_dbData && _dbData.success && _dbData.track) {
                const _row = _dbData.track;
                track = {
                    ...track,
                    id: _row.id ?? track.id,
                    title: _row.title || track.title,
                    file_path: _row.file_path || track.file_path,
                    bitrate: _row.bitrate ?? track.bitrate,
                    artist_id: _row.artist_id ?? track.artist_id,
                    album_id: _row.album_id ?? track.album_id,
                    _stats_image: _row.image_url || _row.album_thumb_url || track._stats_image || null,
                };
                if (_row.album_title) albumTitle = _row.album_title;
                if (_row.artist_name) artistName = _row.artist_name;
            }
        } catch (_dbErr) {
            console.debug('library track DB refresh skipped:', _dbErr);
        }
    }

    try {
        // Stop any current playback first
        if (audioPlayer && !audioPlayer.paused) {
            audioPlayer.pause();
        }

        // Cover art travels with the track the caller already rendered; there
        // is no page-wide album cache to consult any more.
        const albumArt = track._stats_image || null;

        // Set track info in the media player UI
        setTrackInfo({
            title: track.title || 'Unknown Track',
            artist: artistName || 'Unknown Artist',
            album: albumTitle || 'Unknown Album',
            filename: track.file_path,
            is_library: true,
            image_url: albumArt,
            id: track.id,
            lib2_track_id: track.lib2_track_id || null,
            legacy_track_id: track.legacy_track_id || null,
            server_track_id: track.server_track_id || null,
            artist_id: track.artist_id,
            album_id: track.album_id,
            bitrate: track.bitrate,
            sample_rate: track.sample_rate
        });

        // Show loading state
        showLoadingAnimation();
        const loadingText = document.querySelector('.loading-text');
        if (loadingText) {
            loadingText.textContent = 'Loading library track...';
        }

        // POST to library play endpoint
        const response = await fetch('/api/library/play', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                file_path: track.file_path,
                title: track.title || '',
                artist: artistName || '',
                album: albumTitle || '',
                // Server song id so playback can stream via the media server
                // when the file isn't on SoulSync's disk (#809).
                track_id: track.server_track_id || track.legacy_track_id ||
                    (track.lib2_track_id ? null : (track.id || null)),
                lib2_track_id: track.lib2_track_id || null,
                legacy_track_id: track.legacy_track_id || null,
                server_track_id: track.server_track_id || null,
            })
        });

        const result = await response.json();
        if (!result.success) {
            // File not on disk — fall back to streaming from configured source
            console.warn('Library file not found, falling back to stream source');
            hideLoadingAnimation();
            const streamRes = await fetch('/api/enhanced-search/stream-track', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    track_name: track.title || '',
                    artist_name: artistName || '',
                    album_name: albumTitle || '',
                })
            });
            const streamData = await streamRes.json();
            if (streamData.success && streamData.result) {
                streamData.result.artist = artistName;
                streamData.result.title = track.title;
                streamData.result.album = albumTitle;
                streamData.result.image_url = track._stats_image || null;
                startStream(streamData.result);
                return;
            }
            throw new Error(result.error || 'Failed to start library playback');
        }

        // Re-apply repeat-one loop property
        if (audioPlayer) audioPlayer.loop = (npRepeatMode === 'one');
        // Stream state is already "ready" — start audio playback directly
        await startAudioPlayback();

    } catch (error) {
        console.error('Library playback error:', error);
        showToast(`Playback error: ${error.message}`, 'error');
        hideLoadingAnimation();
        clearTrack();
    }
}

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

