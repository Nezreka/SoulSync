
// ==================================================================================
// PLAYLIST EXPLORER — Visual Discovery Tree
// ==================================================================================

const _explorer = {
    initialized: false,
    mode: 'albums',
    artists: [],
    selectedAlbums: new Set(),
    expandedArtists: new Set(),
    building: false,
    playlistId: null,
    meta: null,
    _resizeTimer: null,
};

function initExplorer() {
    if (_explorer.initialized) return;
    _explorer.initialized = true;
    _explorer._playlists = [];
    _explorer._activeSource = null;

    _explorerLoadPlaylists();

    // Listen for discovery completion to auto-refresh playlist cards
    if (typeof socket !== 'undefined') {
        socket.on('discovery:progress', (data) => {
            if (!document.getElementById('playlist-explorer-page')?.classList.contains('active')) return;
            // Match mirrored playlist discovery events
            if (data.phase === 'discovered' || data.phase === 'sync_complete' || data.complete) {
                // Discovery finished — refresh playlists after brief delay for DB commit
                setTimeout(() => _explorerLoadPlaylists(), 1500);
            }
            // Live progress update on cards during discovery
            if (data.id && data.id.startsWith('mirrored_')) {
                const plId = parseInt(data.id.replace('mirrored_', ''));
                const card = document.querySelector(`.explorer-picker-card[data-id="${plId}"]`);
                if (card) {
                    const meta = card.querySelector('.explorer-picker-card-meta');
                    if (meta && data.progress != null) {
                        meta.innerHTML = `<span class="explorer-discovering-live">Discovering... ${Math.round(data.progress)}%</span>`;
                    }
                }
            }
        });
    }
}

function _explorerLoadPlaylists() {
    fetch('/api/mirrored-playlists')
        .then(r => r.json())
        .then(data => {
            const playlists = Array.isArray(data) ? data : (data.playlists || []);
            _explorer._playlists = playlists;

            if (playlists.length === 0) {
                const scroll = document.getElementById('explorer-picker-scroll');
                if (scroll) scroll.innerHTML = '<div class="explorer-picker-empty">No mirrored playlists found. Sync a playlist first.</div>';
                return;
            }

            // Group by source
            const groups = {};
            playlists.forEach(p => {
                const src = (p.source || 'other').toLowerCase();
                if (!groups[src]) groups[src] = [];
                groups[src].push(p);
            });

            // Render source tabs
            const tabsEl = document.getElementById('explorer-picker-tabs');
            if (tabsEl) {
                const sourceNames = { spotify: 'Spotify', tidal: 'Tidal', deezer: 'Deezer', youtube: 'YouTube', beatport: 'Beatport', file: 'File', other: 'Other' };
                const sources = Object.keys(groups);
                if (sources.length <= 1) {
                    tabsEl.style.display = 'none';
                } else {
                    tabsEl.innerHTML = sources.map((src, i) => {
                        const label = sourceNames[src] || src.charAt(0).toUpperCase() + src.slice(1);
                        const count = groups[src].length;
                        const isActive = _explorer._activeSource === src || (!_explorer._activeSource && i === 0);
                        return `<button class="explorer-picker-tab ${isActive ? 'active' : ''}" data-source="${src}" onclick="explorerSwitchPickerTab('${src}')">${label} <span class="explorer-picker-tab-count">${count}</span></button>`;
                    }).join('');
                }

                // Show active or first source
                const activeSource = _explorer._activeSource || sources[0];
                _explorer._activeSource = activeSource;
                explorerRenderPickerCards(activeSource);
            }
        })
        .catch(() => { });
}

function explorerSwitchPickerTab(source) {
    _explorer._activeSource = source;
    document.querySelectorAll('.explorer-picker-tab').forEach(t => t.classList.toggle('active', t.dataset.source === source));
    explorerRenderPickerCards(source);
}

function explorerRenderPickerCards(source) {
    const scroll = document.getElementById('explorer-picker-scroll');
    if (!scroll) return;

    const filtered = _explorer._playlists.filter(p => (p.source || 'other').toLowerCase() === source);
    scroll.innerHTML = filtered.map(p => {
        const img = p.image_url || '';
        const total = p.total_count || p.track_count || 0;
        const discovered = p.discovered_count || 0;
        const pct = total > 0 ? Math.round((discovered / total) * 100) : 0;
        const isReady = pct >= 50;
        const isActive = _explorer.playlistId === p.id;
        const isFullyDiscovered = pct === 100;
        const wasExplored = !!(p.explored_at || p.explored);
        const wishlisted = p.wishlisted_count || 0;
        const inLibrary = p.in_library_count || 0;

        // Status badge: checkmark if explored/in-library, star if ready, % if needs discovery
        let statusBadge = '';
        if (inLibrary > 0 && inLibrary >= total * 0.8) {
            statusBadge = '<div class="explorer-picker-card-badge downloaded" title="Most tracks in library">&#10003;</div>';
        } else if (wasExplored) {
            statusBadge = '<div class="explorer-picker-card-badge explored" title="Already explored">&#10003;</div>';
        } else if (wishlisted > 0) {
            statusBadge = '<div class="explorer-picker-card-badge wishlisted" title="Tracks wishlisted">&#9829;</div>';
        } else if (isFullyDiscovered) {
            statusBadge = '<div class="explorer-picker-card-badge ready" title="Ready to explore">&#9733;</div>';
        } else if (!isReady) {
            statusBadge = `<div class="explorer-picker-card-badge needs-discovery" title="Needs discovery (${pct}%)">${pct}%</div>`;
        }

        // Meta line with status indicators
        let metaHTML;
        const statusParts = [];
        if (inLibrary > 0) statusParts.push(`<span class="explorer-picker-in-library">${inLibrary} in library</span>`);
        if (wishlisted > 0) statusParts.push(`<span class="explorer-picker-wishlisted">${wishlisted} wishlisted</span>`);

        if (isFullyDiscovered) {
            metaHTML = `${total} tracks &middot; <span class="explorer-picker-discovered">Fully discovered</span>`;
        } else if (isReady) {
            metaHTML = `${total} tracks &middot; ${pct}% discovered`;
        } else {
            metaHTML = `${total} tracks &middot; <span class="explorer-picker-not-ready">${pct}% discovered</span>`;
        }
        if (statusParts.length > 0) {
            metaHTML += `<br>${statusParts.join(' &middot; ')}`;
        }

        // Discover button for undiscovered playlists (replaces redirect to Sync)
        const discoverBtn = !isReady ? `<button class="explorer-picker-discover-btn" onclick="event.stopPropagation(); explorerStartDiscovery(${p.id})" title="Start discovery">Discover</button>` : '';

        return `
            <div class="explorer-picker-card ${isActive ? 'active' : ''} ${!isReady ? 'not-ready' : ''} ${wasExplored ? 'explored' : ''}"
                 data-id="${p.id}"
                 onclick="${isReady ? `explorerSelectPlaylist(${p.id}, this)` : ''}">
                <div class="explorer-picker-card-art">
                    ${img ? `<img src="${img}" alt="" loading="lazy">` : '<div class="explorer-picker-card-art-placeholder">&#9835;</div>'}
                </div>
                <div class="explorer-picker-card-info">
                    <div class="explorer-picker-card-name-row">
                        <div class="explorer-picker-card-name">${p.name || 'Untitled'}</div>
                        ${statusBadge}
                    </div>
                    <div class="explorer-picker-card-meta">${metaHTML}</div>
                    ${discoverBtn ? `<div class="explorer-picker-card-actions">${discoverBtn}</div>` : ''}
                </div>
            </div>
        `;
    }).join('');
}

function explorerSelectPlaylist(id, el) {
    _explorer.playlistId = id;
    document.querySelectorAll('.explorer-picker-card').forEach(c => c.classList.remove('active'));
    if (el) el.classList.add('active');
    // Update hint text
    const hint = document.getElementById('explorer-build-hint');
    const pl = _explorer._playlists.find(p => p.id === id);
    if (hint && pl) hint.textContent = `Ready: ${pl.name}`;
    else if (hint) hint.textContent = '';
}

function explorerRedirectToDiscover(playlistId) {
    showToast('This playlist needs more tracks discovered before exploring. Redirecting to Sync...', 'info');
    navigateToPage('sync');
    setTimeout(() => {
        const mirroredBtn = document.querySelector('.sync-tab-button[data-tab="mirrored"]');
        if (mirroredBtn) mirroredBtn.click();
    }, 200);
}

async function explorerStartDiscovery(playlistId) {
    const card = document.querySelector(`.explorer-picker-card[data-id="${playlistId}"]`);
    const btn = card?.querySelector('.explorer-picker-discover-btn');
    if (btn) { btn.disabled = true; btn.textContent = 'Starting...'; }

    try {
        if (typeof discoverMirroredPlaylist === 'function') {
            await discoverMirroredPlaylist(playlistId);
            if (btn) { btn.disabled = false; btn.textContent = 'Open'; btn.title = 'Reopen discovery modal'; }

            // Poll for card updates while discovery is in progress
            _explorerStartDiscoveryPoller(playlistId);
        } else {
            explorerRedirectToDiscover(playlistId);
        }
    } catch (err) {
        showToast(`Discovery failed: ${err.message}`, 'error');
        if (btn) { btn.disabled = false; btn.textContent = 'Discover'; }
    }
}

function _explorerStartDiscoveryPoller(playlistId) {
    // Poll every 5s to refresh playlist cards until this playlist is ready
    if (_explorer._discoveryPoller) clearInterval(_explorer._discoveryPoller);
    _explorer._discoveryPoller = setInterval(async () => {
        // Stop polling if Explorer page isn't active
        if (!document.getElementById('playlist-explorer-page')?.classList.contains('active')) {
            clearInterval(_explorer._discoveryPoller);
            _explorer._discoveryPoller = null;
            return;
        }
        // Check if the mirrored playlist state shows discovery is done
        const tempHash = `mirrored_${playlistId}`;
        const state = youtubePlaylistStates[tempHash];
        const isDone = state && (state.phase === 'discovered' || state.phase === 'sync_complete');

        // Refresh cards from API
        await _explorerLoadPlaylists();

        // Stop polling once discovery is complete
        if (isDone) {
            clearInterval(_explorer._discoveryPoller);
            _explorer._discoveryPoller = null;
        }
    }, 5000);
}

function explorerSetMode(mode) {
    _explorer.mode = mode;
    document.querySelectorAll('.explorer-mode-btn').forEach(btn => {
        btn.classList.toggle('active', btn.dataset.mode === mode);
    });
}

async function explorerBuildTree() {
    const playlistId = _explorer.playlistId;
    if (!playlistId) {
        showToast('Select a playlist first', 'error');
        return;
    }
    if (_explorer.building) return;

    _explorer.building = true;
    _explorer.artists = [];
    _explorer.selectedAlbums.clear();
    _explorer.expandedArtists.clear();
    _explorer.playlistId = playlistId;

    const tree = document.getElementById('explorer-tree');
    const svg = document.getElementById('explorer-svg');
    const progress = document.getElementById('explorer-progress');
    const actionBar = document.getElementById('explorer-action-bar');
    const empty = document.getElementById('explorer-empty');
    const buildBtn = document.getElementById('explorer-build-btn');

    if (empty) empty.style.display = 'none';
    if (actionBar) actionBar.style.display = 'none';
    if (progress) progress.style.display = 'flex';
    if (buildBtn) { buildBtn.disabled = true; buildBtn.textContent = 'Building...'; }
    // Clear tree but preserve the SVG element (it lives inside the tree)
    tree.innerHTML = '<svg class="explorer-svg" id="explorer-svg"></svg>';
    _explorer._zoom = 1;
    tree.style.transform = '';

    try {
        const response = await fetch('/api/playlist-explorer/build-tree', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ playlist_id: parseInt(playlistId), mode: _explorer.mode })
        });

        if (!response.ok) {
            const err = await response.json();
            throw new Error(err.error || 'Failed to build tree');
        }

        // Stream NDJSON
        const reader = response.body.getReader();
        const decoder = new TextDecoder();
        let buffer = '';
        let artistCount = 0;
        let totalArtists = 0;

        while (true) {
            const { done, value } = await reader.read();
            if (done) break;
            buffer += decoder.decode(value, { stream: true });
            const lines = buffer.split('\n');
            buffer = lines.pop();

            for (const line of lines) {
                if (!line.trim()) continue;
                try {
                    const data = JSON.parse(line);

                    if (data.type === 'meta') {
                        _explorer.meta = data;
                        totalArtists = data.total_artists;
                        _explorerRenderRoot(data);
                    } else if (data.type === 'artist') {
                        artistCount++;
                        _explorer.artists.push(data);
                        _explorerRenderArtistNode(data, artistCount);

                        // Lines drawn after streaming completes (not during — flex reflow drifts positions)

                        // Update progress
                        const pct = Math.round((artistCount / totalArtists) * 100);
                        const fill = document.getElementById('explorer-progress-fill');
                        const text = document.getElementById('explorer-progress-text');
                        if (fill) fill.style.width = pct + '%';
                        if (text) text.textContent = `Discovering artists... ${artistCount} of ${totalArtists}`;
                    } else if (data.type === 'complete') {
                        // Done
                    }
                } catch (e) {
                    console.warn('Explorer: failed to parse NDJSON line', e);
                }
            }
        }

        // Tree built — show action bar, hide progress
        if (actionBar) actionBar.style.display = 'flex';
        if (progress) progress.style.display = 'none';
        _explorerUpdateCount();

        // Mark playlist as explored (server persists via explored_at; update local copy too)
        const exploredPl = _explorer._playlists.find(p => p.id === playlistId);
        if (exploredPl) {
            exploredPl.explored_at = new Date().toISOString();
            // Update card badge without full re-render
            const card = document.querySelector(`.explorer-picker-card[data-id="${playlistId}"]`);
            if (card) {
                card.classList.add('explored');
                const oldBadge = card.querySelector('.explorer-picker-card-badge');
                const badgeHTML = '<div class="explorer-picker-card-badge explored" title="Already explored">&#10003;</div>';
                if (oldBadge) {
                    oldBadge.outerHTML = badgeHTML;
                } else {
                    // Insert badge into the name row
                    const nameRow = card.querySelector('.explorer-picker-card-name-row');
                    if (nameRow) {
                        nameRow.insertAdjacentHTML('beforeend', badgeHTML);
                    }
                }
                // Remove discover button if present (no longer needed)
                const discoverBtn = card.querySelector('.explorer-picker-card-actions');
                if (discoverBtn) discoverBtn.remove();
            }
        }

        // Draw all connections now that the tree is stable
        setTimeout(() => _explorerRedrawAllConnections(true), 100);

    } catch (err) {
        showToast('Explorer: ' + err.message, 'error');
        if (empty) { empty.style.display = 'flex'; }
        if (progress) progress.style.display = 'none';
    } finally {
        _explorer.building = false;
        if (buildBtn) { buildBtn.disabled = false; buildBtn.textContent = 'Explore'; }
    }
}

function _explorerRenderRoot(meta) {
    const tree = document.getElementById('explorer-tree');
    const rootHtml = `
        <div class="explorer-tier explorer-tier-root">
            <div class="explorer-node explorer-node-root" id="explorer-root">
                <div class="explorer-node-glow"></div>
                ${meta.playlist_image
            ? `<img class="explorer-node-img" src="${meta.playlist_image}" alt="">`
            : '<div class="explorer-node-img-placeholder">&#9835;</div>'
        }
                <div class="explorer-node-label">
                    <div class="explorer-node-label-sub">SOURCE</div>
                    <div class="explorer-node-label-main">${meta.playlist_name}</div>
                    <div class="explorer-node-label-meta">${meta.total_tracks} tracks &middot; ${meta.total_artists} artists</div>
                </div>
            </div>
        </div>
        <div class="explorer-artist-tiers" id="explorer-artist-tiers"></div>
    `;
    tree.insertAdjacentHTML('afterbegin', rootHtml);
    _explorer._artistRowSizes = []; // Track row capacities: [2, 3, 4, ...]
    _explorer._artistCount = 0;
    _explorer._currentRowIndex = 0;
}

function _explorerGetOrCreateRow() {
    const container = document.getElementById('explorer-artist-tiers');
    if (!container) return null;

    // Determine row sizes: 2, 3, 4, 5... (tree shape)
    const rowCapacity = _explorer._currentRowIndex + 2;
    const existingRows = container.querySelectorAll('.explorer-tier-artists');
    let currentRow = existingRows[existingRows.length - 1];

    if (!currentRow || currentRow.children.length >= (_explorer._currentRowIndex + 2)) {
        // Need a new row
        _explorer._currentRowIndex = existingRows.length;
        const newRow = document.createElement('div');
        newRow.className = 'explorer-tier explorer-tier-artists';
        container.appendChild(newRow);
        return newRow;
    }
    return currentRow;
}

function _explorerRenderArtistNode(artist, index) {
    const row = _explorerGetOrCreateRow();
    if (!row) return;

    _explorer._artistCount++;
    const albumCount = artist.albums ? artist.albums.length : 0;
    const safeKey = (artist.name || '').replace(/[^a-zA-Z0-9]/g, '_');
    const hasError = !!artist.error;

    const html = `
        <div class="explorer-branch" id="explorer-branch-${safeKey}" style="--enter-delay: ${(index % 5) * 0.1}s">
            <div class="explorer-node explorer-node-artist ${hasError ? 'error' : ''}"
                 id="explorer-node-${safeKey}" data-key="${safeKey}"
                 onclick="${hasError || albumCount === 0 ? '' : `explorerToggleArtist('${safeKey}')`}">
                ${artist.image_url
            ? `<img class="explorer-node-img" src="${artist.image_url}" alt="" loading="lazy">`
            : ''
        }
                <div class="explorer-node-label">
                    <div class="explorer-node-label-main">${artist.name || 'Unknown'}</div>
                    <div class="explorer-node-label-meta">${hasError ? 'Not found' : albumCount + ' album' + (albumCount !== 1 ? 's' : '')}</div>
                </div>
                ${!hasError && albumCount > 0 ? '<div class="explorer-node-expand-hint">&#9662;</div>' : ''}
                ${hasError ? '<div class="explorer-node-error-ring"></div>' : ''}
            </div>
            <div class="explorer-children" id="explorer-children-${safeKey}"></div>
        </div>
    `;
    row.insertAdjacentHTML('beforeend', html);
}

function explorerToggleArtist(key) {
    const children = document.getElementById(`explorer-children-${key}`);
    const node = document.getElementById(`explorer-node-${key}`);
    if (!children || !node) return;

    const isExpanded = _explorer.expandedArtists.has(key);
    if (isExpanded) {
        _explorer.expandedArtists.delete(key);
        children.innerHTML = '';
        node.classList.remove('expanded');
    } else {
        _explorer.expandedArtists.add(key);
        node.classList.add('expanded');

        const artist = _explorer.artists.find(a => (a.name || '').replace(/[^a-zA-Z0-9]/g, '_') === key);
        if (artist && artist.albums) {
            const albumsHtml = artist.albums.map((album, i) => {
                const id = album.spotify_id || `${key}_${i}`;
                const selected = _explorer.selectedAlbums.has(id);
                const owned = album.owned;
                const inPlaylist = album.in_playlist;

                const typeLabel = album.album_type === 'single' ? 'Single' : album.album_type === 'ep' ? 'EP' : 'Album';
                return `
                    <div class="explorer-branch" style="--enter-delay: ${i * 0.06}s">
                        <div class="explorer-node explorer-node-album ${selected ? 'selected' : ''} ${owned ? 'owned' : ''} ${inPlaylist ? 'in-playlist' : ''}"
                             data-id="${id}" data-key="${id}"
                             onclick="explorerToggleAlbum('${id}'); event.stopPropagation();"
                             title="${(album.title || '').replace(/"/g, '&quot;')}\n${album.year || ''} · ${typeLabel} · ${album.track_count || '?'} tracks${owned ? '\n✓ Already in library' : ''}${inPlaylist ? '\n♫ Track from this playlist' : ''}\nClick to select · Double-click for tracklist">
                            ${album.image_url
                        ? `<img class="explorer-node-img" src="${album.image_url}" alt="" loading="lazy">`
                        : ''
                    }
                            <div class="explorer-node-label">
                                <div class="explorer-node-label-main">${album.title || 'Unknown'}</div>
                                <div class="explorer-node-label-meta">${album.year || ''} &middot; ${album.track_count || '?'} tracks</div>
                            </div>
                            <div class="explorer-node-select ${selected ? 'active' : ''}">
                                <svg viewBox="0 0 20 20"><polyline points="4 10 8 14 16 6" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"/></svg>
                            </div>
                            ${owned ? '<div class="explorer-node-badge-float owned">Owned</div>' : ''}
                            ${inPlaylist ? '<div class="explorer-node-badge-float playlist">♫</div>' : ''}
                        </div>
                        <div class="explorer-children" id="explorer-tracks-${id}"></div>
                    </div>
                `;
            }).join('');
            children.innerHTML = albumsHtml;
        }
    }

    // Redraw SVG after DOM settles
    requestAnimationFrame(() => setTimeout(() => _explorerRedrawAllConnections(), 50));
}

async function explorerExpandAlbumTracks(spotifyAlbumId, nodeKey) {
    if (!spotifyAlbumId) return;
    const tracksContainer = document.getElementById(`explorer-tracks-${nodeKey}`);
    if (!tracksContainer) return;

    // Toggle: if already has content, collapse
    if (tracksContainer.innerHTML) {
        tracksContainer.innerHTML = '';
        requestAnimationFrame(() => setTimeout(() => _explorerRedrawAllConnections(), 50));
        return;
    }

    try {
        const response = await fetch(`/api/playlist-explorer/album-tracks/${spotifyAlbumId}`);
        const data = await response.json();
        if (!data.success || !data.tracks) return;

        const tracksHtml = data.tracks.map((t, i) => `
            <div class="explorer-branch" style="--enter-delay: ${i * 0.03}s">
                <div class="explorer-node explorer-node-track">
                    <div class="explorer-node-label">
                        <div class="explorer-node-label-main">${t.track_number}. ${t.name}</div>
                        <div class="explorer-node-label-meta">${_formatDuration(t.duration_ms)}</div>
                    </div>
                </div>
            </div>
        `).join('');
        tracksContainer.innerHTML = tracksHtml;
        requestAnimationFrame(() => setTimeout(() => _explorerRedrawAllConnections(), 50));
    } catch (e) {
        console.error('Failed to load album tracks:', e);
    }
}

function _formatDuration(ms) {
    if (!ms) return '';
    const m = Math.floor(ms / 60000);
    const s = Math.floor((ms % 60000) / 1000);
    return `${m}:${s.toString().padStart(2, '0')}`;
}

// Track double-click vs single-click on album nodes
let _explorerClickTimer = null;
let _explorerLastClickId = null;

function explorerToggleAlbum(id) {
    // Double-click detection: expand tracks
    if (_explorerLastClickId === id && _explorerClickTimer) {
        clearTimeout(_explorerClickTimer);
        _explorerClickTimer = null;
        _explorerLastClickId = null;
        // Double-click — expand tracks
        const node = document.querySelector(`.explorer-node-album[data-id="${id}"]`);
        const spotifyId = id.includes('_') ? '' : id; // Only real IDs, not fallback keys
        explorerExpandAlbumTracks(spotifyId, id);
        return;
    }

    _explorerLastClickId = id;
    _explorerClickTimer = setTimeout(() => {
        _explorerClickTimer = null;
        _explorerLastClickId = null;

        // Single click — toggle selection
        if (_explorer.selectedAlbums.has(id)) {
            _explorer.selectedAlbums.delete(id);
        } else {
            _explorer.selectedAlbums.add(id);
        }

        const node = document.querySelector(`.explorer-node-album[data-id="${id}"]`);
        if (node) {
            const isSelected = _explorer.selectedAlbums.has(id);
            node.classList.toggle('selected', isSelected);
            const check = node.querySelector('.explorer-node-select');
            if (check) check.classList.toggle('active', isSelected);
        }

        _explorerUpdateCount();
    }, 250);

    _explorerUpdateCount();
}

function explorerSelectAll() {
    _explorer.artists.forEach(a => {
        (a.albums || []).forEach(album => {
            if (album.spotify_id && !album.owned) _explorer.selectedAlbums.add(album.spotify_id);
        });
    });
    _explorerRefreshAllCards();
    _explorerUpdateCount();
}

function explorerDeselectAll() {
    _explorer.selectedAlbums.clear();
    _explorerRefreshAllCards();
    _explorerUpdateCount();
}

function _explorerRefreshAllCards() {
    document.querySelectorAll('.explorer-node-album').forEach(node => {
        const id = node.dataset.id;
        const selected = _explorer.selectedAlbums.has(id);
        node.classList.toggle('selected', selected);
        const check = node.querySelector('.explorer-node-select');
        if (check) check.classList.toggle('active', selected);
    });
}

function _explorerUpdateCount() {
    const el = document.getElementById('explorer-selection-count');
    const count = _explorer.selectedAlbums.size;
    if (el) el.textContent = `${count} album${count !== 1 ? 's' : ''} selected`;
    _explorerRefreshArtistIndicators();
}

function _explorerRefreshArtistIndicators() {
    // For each artist, check if any of their albums are selected — add visual indicator
    _explorer.artists.forEach(artist => {
        const key = (artist.name || '').replace(/[^a-zA-Z0-9]/g, '_');
        const node = document.getElementById(`explorer-node-${key}`);
        if (!node) return;
        const hasSelected = (artist.albums || []).some(a => a.spotify_id && _explorer.selectedAlbums.has(a.spotify_id));
        node.classList.toggle('has-selection', hasSelected);
    });
}

function explorerAddToWishlist() {
    if (_explorer.selectedAlbums.size === 0) {
        showToast('No albums selected', 'error');
        return;
    }

    // Group selected albums by artist with full metadata
    const artistSections = [];
    for (const artist of _explorer.artists) {
        const artistId = artist.artist_id || artist.spotify_id;
        if (!artist.albums) continue;
        const selected = artist.albums.filter(a => a.spotify_id && _explorer.selectedAlbums.has(a.spotify_id));
        if (selected.length === 0) continue;
        artistSections.push({ artistId, name: artist.name, image: artist.image_url, albums: selected });
    }

    if (artistSections.length === 0) { showToast('No valid albums selected', 'error'); return; }

    // Build confirmation modal (mirrors discog-modal pattern)
    const overlay = document.createElement('div');
    overlay.className = 'discog-modal-overlay';
    overlay.id = 'explorer-wishlist-overlay';

    const totalAlbums = artistSections.reduce((s, a) => s + a.albums.length, 0);
    const totalTracks = artistSections.reduce((s, a) => s + a.albums.reduce((t, al) => t + (al.track_count || 0), 0), 0);

    let cardsHtml = '';
    artistSections.forEach(section => {
        cardsHtml += `<div class="discog-section-header">${_esc(section.name)}</div>`;
        section.albums.forEach((album, i) => {
            const year = album.year || '';
            const typeLabel = album.album_type === 'single' ? 'Single' : album.album_type === 'ep' ? 'EP' : 'Album';
            cardsHtml += `
                <label class="discog-card ${album.owned ? 'owned' : ''}" data-type="${album.album_type || 'album'}" data-artist-id="${section.artistId}" style="animation-delay:${i * 0.03}s">
                    <input type="checkbox" class="discog-card-cb" data-album-id="${album.spotify_id}" data-tracks="${album.track_count || 0}" ${!album.owned ? 'checked' : ''} onchange="_explorerWishlistUpdateCount()">
                    <div class="discog-card-art">
                        ${album.image_url ? `<img src="${album.image_url}" alt="" loading="lazy">` : '<div class="discog-card-art-placeholder">&#9835;</div>'}
                        ${album.owned ? '<span class="discog-card-status">&#10003;</span>' : ''}
                    </div>
                    <div class="discog-card-info">
                        <div class="discog-card-title">${_esc(album.title || 'Unknown')}</div>
                        <div class="discog-card-meta">${year}${year ? ' · ' : ''}${typeLabel} · ${album.track_count || '?'} tracks</div>
                    </div>
                    <div class="discog-card-check"></div>
                </label>
            `;
        });
    });

    overlay.innerHTML = `
        <div class="discog-modal">
            <div class="discog-modal-hero" style="background-image:url('${artistSections[0]?.image || ''}')">
                <div class="discog-modal-hero-overlay"></div>
                <div class="discog-modal-hero-content">
                    <h2 class="discog-modal-title">Add to Wishlist</h2>
                    <p class="discog-modal-artist">${artistSections.length} artist${artistSections.length !== 1 ? 's' : ''} · ${totalAlbums} releases</p>
                </div>
                <button class="discog-modal-close" onclick="document.getElementById('explorer-wishlist-overlay')?.remove()">&times;</button>
            </div>
            <div class="discog-filter-bar">
                <div class="discog-filters">
                    <button class="discog-filter active" data-type="album" onclick="_explorerWishlistToggleFilter(this)">Albums</button>
                    <button class="discog-filter active" data-type="ep" onclick="_explorerWishlistToggleFilter(this)">EPs</button>
                    <button class="discog-filter active" data-type="single" onclick="_explorerWishlistToggleFilter(this)">Singles</button>
                </div>
                <div class="discog-select-actions">
                    <button class="discog-select-btn" onclick="document.querySelectorAll('#explorer-wishlist-overlay .discog-card-cb').forEach(c=>{c.checked=true}); _explorerWishlistUpdateCount()">Select All</button>
                    <button class="discog-select-btn" onclick="document.querySelectorAll('#explorer-wishlist-overlay .discog-card-cb').forEach(c=>{c.checked=false}); _explorerWishlistUpdateCount()">Deselect</button>
                </div>
            </div>
            <div class="discog-grid" id="explorer-wishlist-grid">${cardsHtml}</div>
            <div class="discog-progress" id="explorer-wishlist-progress" style="display:none;"></div>
            <div class="discog-footer" id="explorer-wishlist-footer">
                <div class="discog-footer-info" id="explorer-wishlist-info"></div>
                <div class="discog-footer-actions">
                    <button class="discog-cancel-btn" onclick="document.getElementById('explorer-wishlist-overlay')?.remove()">Cancel</button>
                    <button class="discog-submit-btn" id="explorer-wishlist-submit">
                        <span class="discog-submit-icon">&#11015;</span>
                        <span id="explorer-wishlist-submit-text">Add to Wishlist</span>
                    </button>
                </div>
            </div>
        </div>
    `;

    document.body.appendChild(overlay);
    requestAnimationFrame(() => overlay.classList.add('visible'));
    _explorerWishlistUpdateCount();

    document.getElementById('explorer-wishlist-submit')?.addEventListener('click', () => _explorerWishlistSubmit(artistSections));
}

function _explorerWishlistToggleFilter(btn) {
    btn.classList.toggle('active');
    const type = btn.dataset.type;
    // Scoped to explorer wishlist modal only
    document.querySelectorAll(`#explorer-wishlist-overlay .discog-card[data-type="${type}"]`).forEach(card => {
        card.style.display = btn.classList.contains('active') ? '' : 'none';
    });
    _explorerWishlistUpdateCount();
}

function _explorerWishlistUpdateCount() {
    const checked = document.querySelectorAll('#explorer-wishlist-overlay .discog-card-cb:checked');
    let releases = 0, tracks = 0;
    checked.forEach(cb => {
        if (cb.closest('.discog-card').style.display !== 'none') {
            releases++;
            tracks += parseInt(cb.dataset.tracks) || 0;
        }
    });
    const info = document.getElementById('explorer-wishlist-info');
    const btn = document.getElementById('explorer-wishlist-submit-text');
    if (info) info.textContent = `${releases} release${releases !== 1 ? 's' : ''} · ${tracks} tracks`;
    if (btn) btn.textContent = releases > 0 ? `Add ${releases} to Wishlist` : 'Select releases';
    const submitBtn = document.getElementById('explorer-wishlist-submit');
    if (submitBtn) submitBtn.disabled = releases === 0;
}

async function _explorerWishlistSubmit(artistSections) {
    const grid = document.getElementById('explorer-wishlist-grid');
    const progress = document.getElementById('explorer-wishlist-progress');
    const filterBar = document.querySelector('#explorer-wishlist-overlay .discog-filter-bar');
    const submitBtn = document.getElementById('explorer-wishlist-submit');

    // Collect checked albums grouped by artist
    const byArtist = {};
    document.querySelectorAll('#explorer-wishlist-overlay .discog-card-cb:checked').forEach(cb => {
        if (cb.closest('.discog-card').style.display === 'none') return;
        const card = cb.closest('.discog-card');
        const artistId = card.dataset.artistId;
        const albumId = cb.dataset.albumId;
        const title = card.querySelector('.discog-card-title')?.textContent || '';
        const img = card.querySelector('.discog-card-art img')?.src || '';
        if (!byArtist[artistId]) byArtist[artistId] = { albums: [], name: '' };
        byArtist[artistId].albums.push({ id: albumId, title, img, tracks: parseInt(cb.dataset.tracks) || 0 });
    });

    // Fill in artist names
    artistSections.forEach(s => { if (byArtist[s.artistId]) byArtist[s.artistId].name = s.name; });

    // Switch to progress view
    if (grid) grid.style.display = 'none';
    if (filterBar) filterBar.style.display = 'none';
    if (submitBtn) submitBtn.style.display = 'none';
    if (progress) {
        progress.style.display = '';
        progress.innerHTML = '';
        for (const [artistId, data] of Object.entries(byArtist)) {
            data.albums.forEach(album => {
                const item = document.createElement('div');
                item.className = 'discog-progress-item active';
                item.id = `explorer-prog-${album.id}`;
                item.innerHTML = `
                    <div class="discog-prog-art">${album.img ? `<img src="${album.img}">` : '&#9835;'}</div>
                    <div class="discog-prog-info">
                        <div class="discog-prog-title">${_esc(album.title)}</div>
                        <div class="discog-prog-status">Waiting...</div>
                    </div>
                    <div class="discog-prog-icon"><div class="discog-spinner"></div></div>
                `;
                progress.appendChild(item);
            });
        }
    }

    const info = document.getElementById('explorer-wishlist-info');
    if (info) info.textContent = 'Processing...';

    let totalAdded = 0;

    for (const [artistId, data] of Object.entries(byArtist)) {
        // Sort by track count descending (deluxe editions first) BEFORE extracting IDs
        data.albums.sort((a, b) => b.tracks - a.tracks);
        // Per-album metadata so the backend can resolve each album through its
        // own source even when the explorer doesn't carry per-album source info.
        const albumsPayload = data.albums.map(a => ({
            id: a.id,
            name: a.title || '',
            artist_name: data.name,
            source: null,
        }));

        try {
            const response = await fetch(`/api/artist/${artistId}/download-discography`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    albums: albumsPayload,
                    artist_name: data.name,
                })
            });

            const reader = response.body.getReader();
            const decoder = new TextDecoder();
            let buffer = '';
            while (true) {
                const { done, value } = await reader.read();
                if (done) break;
                buffer += decoder.decode(value, { stream: true });
                const lines = buffer.split('\n');
                buffer = lines.pop();
                for (const line of lines) {
                    if (!line.trim()) continue;
                    try {
                        const result = JSON.parse(line);
                        if (result.status === 'complete') continue; // Summary line, skip
                        const item = document.getElementById(`explorer-prog-${result.album_id}`);
                        if (item) {
                            const statusEl = item.querySelector('.discog-prog-status');
                            const iconEl = item.querySelector('.discog-prog-icon');
                            if (result.status === 'done') {
                                const added = result.tracks_added || 0;
                                const skipped = result.tracks_skipped || 0;
                                totalAdded += added;
                                if (statusEl) statusEl.textContent = `Added ${added} track${added !== 1 ? 's' : ''}${skipped > 0 ? `, ${skipped} skipped` : ''}`;
                                if (iconEl) iconEl.innerHTML = '<span style="color:#4CAF50">&#10003;</span>';
                                item.classList.remove('active');
                                item.classList.add('done');
                            } else if (result.status === 'error') {
                                if (statusEl) statusEl.textContent = result.message || 'Error';
                                if (iconEl) iconEl.innerHTML = '<span style="color:#ff4757">&#10007;</span>';
                                item.classList.remove('active');
                                item.classList.add('error');
                            }
                        }
                    } catch (e) { }
                }
            }
        } catch (e) {
            console.error(`Explorer wishlist: failed for ${data.name}:`, e);
        }
    }

    if (info) info.textContent = `Done — ${totalAdded} tracks added to wishlist`;
    // Change cancel button label to "Close"
    const cancelBtn = document.querySelector('#explorer-wishlist-overlay .discog-cancel-btn');
    if (cancelBtn) cancelBtn.textContent = 'Close';
    showToast(`Added ${totalAdded} tracks to wishlist`, 'success');

    // Mark albums as added on the tree
    _explorer.selectedAlbums.forEach(id => {
        const node = document.querySelector(`.explorer-node-album[data-id="${id}"]`);
        if (node) { node.classList.add('added'); node.classList.remove('selected'); }
    });
    _explorer.selectedAlbums.clear();
    _explorerUpdateCount();
    _explorerRefreshArtistIndicators();
}

function _explorerEnsureDefs() {
    const svg = document.getElementById('explorer-svg');
    if (!svg || svg.querySelector('defs')) return;
    // Read accent color from CSS custom property
    const accentRgb = getComputedStyle(document.documentElement).getPropertyValue('--accent-rgb').trim() || '100,200,255';
    const defs = document.createElementNS('http://www.w3.org/2000/svg', 'defs');
    defs.innerHTML = `
        <linearGradient id="explorer-grad-root" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stop-color="rgba(${accentRgb}, 0.25)"/>
            <stop offset="100%" stop-color="rgba(${accentRgb}, 0.06)"/>
        </linearGradient>
        <linearGradient id="explorer-grad-album" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stop-color="rgba(${accentRgb}, 0.15)"/>
            <stop offset="100%" stop-color="rgba(${accentRgb}, 0.04)"/>
        </linearGradient>
    `;
    svg.appendChild(defs);
}

function _explorerDrawConnectionToArtist(artistIndex) {
    // Incremental: draw ONLY this artist's connection. Don't clear existing.
    // Flex reflow within the current row may shift siblings, but the visual drift
    // is minor and gets corrected by the final redraw after streaming completes.
    const svg = document.getElementById('explorer-svg');
    const root = document.getElementById('explorer-root');
    if (!svg || !root) return;

    _explorerEnsureDefs();
    _explorerSizeSvg();

    const artistNodes = document.querySelectorAll('.explorer-node-artist');
    const artistNode = artistNodes[artistIndex];
    if (!artistNode) return;

    const rc = _explorerGetPos(root);
    const ac = _explorerGetPos(artistNode);
    _explorerDrawCurve(svg, rc.cx, rc.bottom, ac.cx, ac.top, 'root', true);
}

function _explorerRedrawAllConnections(animate = false) {
    const svg = document.getElementById('explorer-svg');
    const root = document.getElementById('explorer-root');
    if (!svg || !root) return;

    _explorerEnsureDefs();
    _explorerSizeSvg();

    // Clear existing lines but keep defs
    svg.querySelectorAll('path').forEach(p => p.remove());

    const rc = _explorerGetPos(root);

    document.querySelectorAll('.explorer-node-artist').forEach(artistNode => {
        const ac = _explorerGetPos(artistNode);
        _explorerDrawCurve(svg, rc.cx, rc.bottom, ac.cx, ac.top, 'root', animate);

        if (artistNode.classList.contains('expanded')) {
            const branch = artistNode.closest('.explorer-branch');
            if (!branch) return;
            branch.querySelectorAll(':scope > .explorer-children > .explorer-branch > .explorer-node-album').forEach(albumNode => {
                const alc = _explorerGetPos(albumNode);
                _explorerDrawCurve(svg, ac.cx, ac.bottom, alc.cx, alc.top, 'album', animate);

                const albumBranch = albumNode.closest('.explorer-branch');
                if (albumBranch) {
                    albumBranch.querySelectorAll(':scope > .explorer-children > .explorer-branch > .explorer-node-track').forEach(trackNode => {
                        const tc = _explorerGetPos(trackNode);
                        _explorerDrawCurve(svg, alc.cx, alc.bottom, tc.cx, tc.top, 'track', animate);
                    });
                }
            });
        }
    });
}

function _explorerSizeSvg() {
    const svg = document.getElementById('explorer-svg');
    const tree = document.getElementById('explorer-tree');
    if (!svg || !tree) return;
    // SVG is inside the tree. Use scrollWidth/scrollHeight which are unscaled.
    // Add padding to ensure lines near edges aren't clipped.
    const w = Math.max(tree.scrollWidth, tree.offsetWidth) + 40;
    const h = Math.max(tree.scrollHeight, tree.offsetHeight) + 40;
    svg.setAttribute('width', w);
    svg.setAttribute('height', h);
    svg.setAttribute('viewBox', `0 0 ${w} ${h}`);
}

function _explorerGetPos(el) {
    // SVG is inside the tree — positions are relative to tree, unscaled
    const tree = document.getElementById('explorer-tree');
    if (!tree) return { cx: 0, top: 0, bottom: 0 };
    const tRect = tree.getBoundingClientRect();
    const r = el.getBoundingClientRect();
    const scale = _explorer._zoom || 1;
    // getBoundingClientRect returns scaled coords; divide by scale to get unscaled tree-space coords
    return {
        cx: (r.left + r.width / 2 - tRect.left) / scale,
        top: (r.top - tRect.top) / scale,
        bottom: (r.bottom - tRect.top) / scale,
    };
}

function _explorerDrawCurve(svg, x1, y1, x2, y2, type, animate) {
    const path = document.createElementNS('http://www.w3.org/2000/svg', 'path');
    const midY = y1 + (y2 - y1) * 0.45;
    path.setAttribute('d', `M ${x1} ${y1} C ${x1} ${midY}, ${x2} ${midY}, ${x2} ${y2}`);

    if (type === 'root') {
        path.setAttribute('stroke', 'url(#explorer-grad-root)');
        path.setAttribute('stroke-width', '1.5');
    } else if (type === 'album') {
        path.setAttribute('stroke', 'url(#explorer-grad-album)');
        path.setAttribute('stroke-width', '1');
    } else {
        path.setAttribute('stroke', 'rgba(255,255,255,0.05)');
        path.setAttribute('stroke-width', '0.8');
    }
    path.setAttribute('fill', 'none');

    svg.appendChild(path);

    if (animate) {
        const len = path.getTotalLength();
        path.setAttribute('class', 'explorer-line explorer-line-animated');
        path.style.strokeDasharray = len;
        path.style.strokeDashoffset = len;
    } else {
        path.setAttribute('class', 'explorer-line');
    }
}

// ── Zoom & Pan ──
_explorer._zoom = 1;
_explorer._panX = 0;
_explorer._panY = 0;
_explorer._isPanning = false;
_explorer._panStartX = 0;
_explorer._panStartY = 0;
_explorer._panStartScrollX = 0;
_explorer._panStartScrollY = 0;

function _explorerApplyTransform() {
    const tree = document.getElementById('explorer-tree');
    if (tree) {
        tree.style.transform = `scale(${_explorer._zoom})`;
        tree.style.transformOrigin = 'top center';
    }
    _explorerSizeSvg();
    requestAnimationFrame(() => _explorerRedrawAllConnections());
}

function explorerZoom(delta) {
    _explorer._zoom = Math.max(0.2, Math.min(3, _explorer._zoom + delta));
    _explorerApplyTransform();
}

function explorerFitToView() {
    const viewport = document.getElementById('explorer-viewport');
    const tree = document.getElementById('explorer-tree');
    if (!viewport || !tree) return;

    // Reset zoom to measure natural size
    _explorer._zoom = 1;
    tree.style.transform = 'scale(1)';

    requestAnimationFrame(() => {
        const treeW = tree.scrollWidth;
        const treeH = tree.scrollHeight;
        const vpW = viewport.clientWidth - 40;
        const vpH = viewport.clientHeight - 40;

        if (treeW > 0 && treeH > 0) {
            _explorer._zoom = Math.min(vpW / treeW, vpH / treeH, 1.5);
            _explorer._zoom = Math.max(0.2, Math.min(3, _explorer._zoom));
        }

        _explorerApplyTransform();
        viewport.scrollTop = 0;
        viewport.scrollLeft = Math.max(0, (tree.scrollWidth * _explorer._zoom - vpW) / 2);
    });
}

// Scroll wheel zoom (no modifier needed inside viewport).
// IMPORTANT: attach to the viewport element, NOT document. A non-passive wheel
// listener on document disables the browser's compositor (async) scrolling for
// the ENTIRE app — every wheel/trackpad scroll then runs through the main thread.
// Scoping it to the viewport keeps zoom working while the rest of the app keeps
// smooth compositor scrolling.
(function attachExplorerWheelZoom() {
    const viewport = document.getElementById('explorer-viewport');
    if (!viewport) return;
    viewport.addEventListener('wheel', (e) => {
        const page = document.getElementById('playlist-explorer-page');
        if (!page || !page.classList.contains('active')) return;

        e.preventDefault();
        const step = e.deltaY > 0 ? -0.08 : 0.08;
        explorerZoom(step);
    }, { passive: false });
})();

// Middle-click / right-click drag to pan
document.addEventListener('mousedown', (e) => {
    const viewport = document.getElementById('explorer-viewport');
    if (!viewport || !viewport.contains(e.target)) return;
    // Middle click (button 1) or right click (button 2)
    if (e.button !== 1 && e.button !== 2) return;

    e.preventDefault();
    _explorer._isPanning = true;
    _explorer._panStartX = e.clientX;
    _explorer._panStartY = e.clientY;
    _explorer._panStartScrollX = viewport.scrollLeft;
    _explorer._panStartScrollY = viewport.scrollTop;
    viewport.style.cursor = 'grabbing';
});

document.addEventListener('mousemove', (e) => {
    if (!_explorer._isPanning) return;
    const viewport = document.getElementById('explorer-viewport');
    if (!viewport) return;
    const dx = e.clientX - _explorer._panStartX;
    const dy = e.clientY - _explorer._panStartY;
    viewport.scrollLeft = _explorer._panStartScrollX - dx;
    viewport.scrollTop = _explorer._panStartScrollY - dy;
});

document.addEventListener('mouseup', (e) => {
    if (!_explorer._isPanning) return;
    _explorer._isPanning = false;
    const viewport = document.getElementById('explorer-viewport');
    if (viewport) viewport.style.cursor = '';
});

// Suppress context menu on right-click inside viewport (for panning)
document.addEventListener('contextmenu', (e) => {
    const viewport = document.getElementById('explorer-viewport');
    if (viewport && viewport.contains(e.target)) {
        e.preventDefault();
    }
});

// Debounced redraw on resize
window.addEventListener('resize', () => {
    if (_explorer.artists.length === 0) return;
    clearTimeout(_explorer._resizeTimer);
    _explorer._resizeTimer = setTimeout(() => _explorerRedrawAllConnections(), 150);
});


// ==================================================================================
// DASHBOARD — Recent Syncs Section
// ==================================================================================

// ==================================================================================
// SERVER PLAYLIST MANAGER — Sync Page Server Tab
// ==================================================================================

let _serverPlaylists = [];
let _serverEditorState = { playlistId: null, playlistName: '', tracks: [] };

async function loadServerPlaylists() {
    const container = document.getElementById('server-playlist-container');
    const editor = document.getElementById('server-editor');
    const btn = document.getElementById('server-refresh-btn');

    if (editor) editor.style.display = 'none';
    if (container) container.style.display = '';
    if (btn) { btn.disabled = true; btn.textContent = '🔄 Loading...'; }

    // Show skeleton loader
    if (container) {
        container.innerHTML = `<div class="server-pl-grid">${Array.from({ length: 6 }, (_, i) => `
            <div class="server-pl-card server-pl-skeleton" style="animation-delay: ${i * 0.06}s">
                <div class="server-pl-card-top">
                    <div class="skeleton-box" style="width:44px;height:44px;border-radius:12px"></div>
                    <div class="skeleton-box" style="width:28px;height:28px;border-radius:8px"></div>
                </div>
                <div class="server-pl-card-body">
                    <div class="skeleton-box" style="width:${60 + Math.random() * 30}%;height:14px;border-radius:4px;margin-bottom:8px"></div>
                    <div class="skeleton-box" style="width:40%;height:11px;border-radius:4px"></div>
                </div>
                <div class="server-pl-card-footer" style="border-top:1px solid rgba(255,255,255,0.05);padding-top:12px">
                    <div class="skeleton-box" style="width:60px;height:10px;border-radius:3px"></div>
                </div>
            </div>`).join('')}</div>`;
    }

    try {
        // Fetch server playlists, mirrored playlists, and sync history names in parallel
        const [serverRes, mirroredRes, historyNamesRes] = await Promise.all([
            fetch('/api/server/playlists'),
            fetch('/api/mirrored-playlists'),
            fetch('/api/sync/history/names'),
        ]);
        const data = await serverRes.json();
        let mirroredAll = [];
        try { mirroredAll = await mirroredRes.json(); } catch (_) { }
        if (!Array.isArray(mirroredAll)) mirroredAll = [];
        let historyNames = [];
        try { historyNames = await historyNamesRes.json(); } catch (_) { }
        if (!Array.isArray(historyNames)) historyNames = [];

        if (!data.success || !data.playlists) {
            if (container) container.innerHTML = `<div class="playlist-placeholder">${data.error || 'Could not load server playlists'}</div>`;
            return;
        }

        // Separate synced vs non-synced playlists
        const mirroredNames = new Set(mirroredAll.map(p => p.name.trim().toLowerCase()));
        const syncedNames = new Set(historyNames.map(n => n.trim().toLowerCase()));
        const synced = [];
        const unsynced = [];
        for (const pl of data.playlists) {
            const key = pl.name.trim().toLowerCase();
            if (mirroredNames.has(key) || syncedNames.has(key)) {
                pl._synced = true;
                synced.push(pl);
            } else {
                pl._synced = false;
                unsynced.push(pl);
            }
        }

        _serverPlaylists = [...synced, ...unsynced];
        const title = document.getElementById('server-tab-title');
        const serverName = data.server_type ? data.server_type.charAt(0).toUpperCase() + data.server_type.slice(1) : '';
        if (title) title.textContent = `Server Playlists (${serverName})`;

        if (synced.length === 0 && unsynced.length === 0) {
            if (container) container.innerHTML = '<div class="playlist-placeholder">No playlists found on your media server.</div>';
            return;
        }

        // Server type icon SVG
        const serverIcons = {
            plex: '<svg viewBox="0 0 24 24" width="18" height="18" fill="currentColor"><path d="M11.643 0H4.68l7.679 12L4.68 24h6.963L19.32 12z"/></svg>',
            jellyfin: '<svg viewBox="0 0 24 24" width="18" height="18" fill="currentColor"><path d="M12 2C8.5 2 6 5.1 6 9c0 2.4 1.2 5.5 3.3 8.7.7 1 1.5 2 2.2 2.9.2.3.4.3.5.4.1 0 .3-.1.5-.4.7-.9 1.5-1.9 2.2-2.9C16.8 14.5 18 11.4 18 9c0-3.9-2.5-7-6-7z"/></svg>',
            navidrome: '<svg viewBox="0 0 24 24" width="18" height="18" fill="currentColor"><path d="M12 2L2 7l10 5 10-5-10-5zM2 17l10 5 10-5M2 12l10 5 10-5"/></svg>'
        };
        const sIcon = serverIcons[data.server_type] || serverIcons.plex;

        function _renderPlCard(pl, i, isSynced) {
            const hue = (i * 37 + 200) % 360;
            const safeName = _esc(pl.name).replace(/'/g, "\\'");
            const cardClass = isSynced ? 'server-pl-card' : 'server-pl-card server-pl-unsynced';
            const action = isSynced ? 'Open Editor' : 'View Tracks';
            return `
            <div class="${cardClass}" onclick="openServerPlaylistEditor('${pl.id}', '${safeName}')" style="animation-delay: ${i * 0.04}s; --card-hue: ${hue}">
                <div class="server-pl-card-glow"></div>
                <div class="server-pl-card-top">
                    <div class="server-pl-card-icon-wrap">
                        <div class="server-pl-card-bars">
                            <span></span><span></span><span></span><span></span>
                        </div>
                    </div>
                    <div class="server-pl-card-badge">${sIcon}</div>
                </div>
                <div class="server-pl-card-body">
                    <div class="server-pl-card-name">${_esc(pl.name)}</div>
                    <div class="server-pl-card-meta">
                        <span class="server-pl-track-count">${pl.track_count}</span> tracks
                        ${isSynced ? '<span class="server-pl-synced-badge">Synced</span>' : ''}
                    </div>
                </div>
                <div class="server-pl-card-footer">
                    <span class="server-pl-card-action">${action}</span>
                    <span class="server-pl-card-arrow">
                        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><path d="M5 12h14M12 5l7 7-7 7"/></svg>
                    </span>
                </div>
            </div>`;
        }

        let html = '';

        if (synced.length > 0) {
            html += `<div class="server-pl-section">
                <div class="server-pl-section-header">
                    <span class="server-pl-section-icon">&#128279;</span>
                    <span class="server-pl-section-title">Synced Playlists</span>
                    <span class="server-pl-section-count">${synced.length}</span>
                </div>
                <div class="server-pl-grid">${synced.map((pl, i) => _renderPlCard(pl, i, true)).join('')}</div>
            </div>`;
        }

        if (unsynced.length > 0) {
            html += `<div class="server-pl-section server-pl-section-unsynced">
                <div class="server-pl-section-header">
                    <span class="server-pl-section-icon">&#127925;</span>
                    <span class="server-pl-section-title">Other Server Playlists</span>
                    <span class="server-pl-section-count">${unsynced.length}</span>
                </div>
                <div class="server-pl-grid">${unsynced.map((pl, i) => _renderPlCard(pl, i + synced.length, false)).join('')}</div>
            </div>`;
        }

        container.innerHTML = html;

    } catch (e) {
        if (container) container.innerHTML = `<div class="playlist-placeholder">Error: ${e.message}</div>`;
    } finally {
        if (btn) { btn.disabled = false; btn.textContent = '🔄 Refresh'; }
    }
}

async function openServerPlaylistEditor(playlistId, playlistName) {
    // Step 1: Look up mirrored playlists by name
    let mirroredPlaylists = [];
    try {
        const res = await fetch('/api/mirrored-playlists');
        const all = await res.json();
        mirroredPlaylists = (Array.isArray(all) ? all : []).filter(p =>
            p.name.trim().toLowerCase() === playlistName.trim().toLowerCase()
        );
    } catch (e) {
        console.error('Failed to fetch mirrored playlists:', e);
    }

    if (mirroredPlaylists.length === 1) {
        // Single match — go straight to compare
        _openServerCompareView(playlistId, playlistName, mirroredPlaylists[0]);
    } else if (mirroredPlaylists.length === 0) {
        // No match — server-only view
        _openServerCompareView(playlistId, playlistName, null);
    } else {
        // Multiple — disambiguation
        _showServerDisambig(playlistId, playlistName, mirroredPlaylists);
    }
}

// ── Disambiguation ──

function _showServerDisambig(playlistId, playlistName, candidates) {
    const overlay = document.getElementById('server-disambig-overlay');
    const list = document.getElementById('server-disambig-list');
    const subtitle = document.getElementById('server-disambig-subtitle');
    if (!overlay || !list) return;

    if (subtitle) subtitle.textContent = `"${playlistName}" was found on ${candidates.length} sources. Which one do you want to compare against?`;

    const sourceIcons = { spotify: '🟢', tidal: '🌊', youtube: '▶️', beatport: '🎛️', deezer: '🟣', file: '📄' };

    list.innerHTML = candidates.map((p, i) => {
        const icon = sourceIcons[p.source] || '📋';
        const ago = timeAgo(p.mirrored_at || p.updated_at);
        return `
        <div class="server-disambig-card" onclick="selectDisambigPlaylist('${playlistId}', '${_esc(playlistName).replace(/'/g, "\\'")}', ${p.id})" style="animation-delay: ${i * 0.06}s">
            <div class="server-disambig-icon">${icon}</div>
            <div class="server-disambig-info">
                <div class="server-disambig-name">${_esc(p.name)}</div>
                <div class="server-disambig-details">
                    <span class="source-badge">${_esc(p.source)}</span>
                    <span>${p.track_count || 0} tracks</span>
                    ${p.owner ? `<span>by ${_esc(p.owner)}</span>` : ''}
                    <span>Mirrored ${ago}</span>
                </div>
            </div>
            <div class="server-disambig-arrow">
                <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M5 12h14M12 5l7 7-7 7"/></svg>
            </div>
        </div>`;
    }).join('');

    overlay.classList.remove('hidden');
    requestAnimationFrame(() => overlay.classList.add('visible'));

    // Escape key + click backdrop to close
    overlay.onclick = e => { if (e.target === overlay) closeServerDisambig(); };
    window._disambigEsc = e => { if (e.key === 'Escape') closeServerDisambig(); };
    document.addEventListener('keydown', window._disambigEsc);
}

function closeServerDisambig() {
    const overlay = document.getElementById('server-disambig-overlay');
    if (overlay) {
        overlay.classList.remove('visible');
        setTimeout(() => overlay.classList.add('hidden'), 250);
    }
    if (window._disambigEsc) { document.removeEventListener('keydown', window._disambigEsc); window._disambigEsc = null; }
}

async function selectDisambigPlaylist(playlistId, playlistName, mirroredId) {
    closeServerDisambig();
    try {
        const res = await fetch(`/api/mirrored-playlists/${mirroredId}`);
        const mirrored = await res.json();
        _openServerCompareView(playlistId, playlistName, mirrored);
    } catch (e) {
        showToast('Failed to load mirrored playlist: ' + e.message, 'error');
    }
}

// ── Compare View ──

async function _openServerCompareView(playlistId, playlistName, mirroredPlaylist) {
    const container = document.getElementById('server-playlist-container');
    const editor = document.getElementById('server-editor');
    if (!editor) return;

    if (container) container.style.display = 'none';
    editor.style.display = '';

    const nameEl = document.getElementById('server-editor-name');
    const metaEl = document.getElementById('server-editor-meta');
    const banner = document.getElementById('server-no-source-banner');
    const sourceScroll = document.getElementById('server-col-source-scroll');
    const serverScroll = document.getElementById('server-col-server-scroll');

    if (nameEl) nameEl.textContent = playlistName;
    if (metaEl) metaEl.textContent = 'Loading comparison...';
    if (banner) banner.style.display = 'none';
    if (sourceScroll) sourceScroll.innerHTML = '<div style="text-align:center;padding:30px;color:rgba(255,255,255,0.2);font-size:12px">Loading...</div>';
    if (serverScroll) serverScroll.innerHTML = '<div style="text-align:center;padding:30px;color:rgba(255,255,255,0.2);font-size:12px">Loading...</div>';

    // Store state
    _serverEditorState = {
        playlistId,
        playlistName,
        mirroredPlaylist,
        tracks: [],
    };

    // Build API URL
    let url = `/api/server/playlist/${playlistId}/tracks?name=${encodeURIComponent(playlistName)}`;
    if (mirroredPlaylist && mirroredPlaylist.id) {
        url += `&mirrored_playlist_id=${mirroredPlaylist.id}`;
    }

    try {
        const response = await fetch(url);
        const data = await response.json();
        if (!data.success) {
            if (metaEl) metaEl.textContent = data.error || 'Failed to load';
            return;
        }

        _serverEditorState.tracks = data.tracks || [];
        _serverEditorState.serverType = data.server_type;
        // Order status: the columns render in SOURCE order, so a same-tracks-but-
        // reordered server playlist looks in-sync. order_status flags that drift;
        // server_order is the server's ACTUAL sequence for the read-only view.
        _serverEditorState.orderStatus = data.order_status || null;
        _serverEditorState.serverOrder = data.server_order || [];

        const tracks = _serverEditorState.tracks;
        const serverLabel = data.server_type ? data.server_type.charAt(0).toUpperCase() + data.server_type.slice(1) : 'Server';

        // Header metadata
        if (metaEl) metaEl.textContent = `${serverLabel} · ${data.server_track_count || 0} server tracks · ${data.source_track_count || 0} source tracks`;

        // Show no-source banner if needed
        if (!mirroredPlaylist && banner) {
            banner.style.display = '';
        }

        // Stats, filter counts, footer
        _updateCompareStats(tracks);

        // Column headers
        const sourceLabel = mirroredPlaylist ? (mirroredPlaylist.source || 'source').charAt(0).toUpperCase() + (mirroredPlaylist.source || 'source').slice(1) : 'Source';
        const sourceIconMap = { spotify: '🟢', tidal: '🌊', youtube: '▶️', beatport: '🎛️', deezer: '🟣', file: '📄' };
        const serverIconMap = { plex: '🟠', jellyfin: '🟣', navidrome: '🔵' };

        const srcIconEl = document.getElementById('server-col-source-icon');
        const srcLabelEl = document.getElementById('server-col-source-label');
        const srcCountEl = document.getElementById('server-col-source-count');
        const svrIconEl = document.getElementById('server-col-server-icon');
        const svrLabelEl = document.getElementById('server-col-server-label');
        const svrCountEl = document.getElementById('server-col-server-count');

        if (srcIconEl) srcIconEl.textContent = mirroredPlaylist ? (sourceIconMap[mirroredPlaylist.source] || '📋') : '📋';
        if (srcLabelEl) srcLabelEl.textContent = sourceLabel;
        if (srcCountEl) srcCountEl.textContent = `${data.source_track_count || 0} tracks`;
        if (svrIconEl) svrIconEl.textContent = serverIconMap[data.server_type] || '💻';
        if (svrLabelEl) svrLabelEl.textContent = serverLabel;
        if (svrCountEl) {
            const os = _serverEditorState.orderStatus;
            if (os && os.out_of_order) {
                // Accurate membership but different order than the source. Read-only:
                // source order is the source of truth; the badge opens the real order.
                svrCountEl.innerHTML = `${data.server_track_count || 0} tracks ` +
                    `<button type="button" class="server-order-badge" onclick="_showServerOrder()" ` +
                    `title="These tracks match the source, but the playlist is in a different order on ${serverLabel}. Click to view the actual server order.">` +
                    `&#9888; out of order</button>`;
            } else {
                svrCountEl.textContent = `${data.server_track_count || 0} tracks`;
            }
        }

        // Render columns — re-applying the active filter pill so the rows always
        // agree with it (a reload used to reset the rows to "all" while the
        // Missing pill stayed selected — #1005).
        _renderCompareColumns(tracks);
        _applyServerEditorFilter(_activeServerEditorFilter());

        // Scroll linking
        _setupScrollLinking();

    } catch (e) {
        if (metaEl) metaEl.textContent = 'Error: ' + e.message;
    }
}

function _updateCompareStats(tracks) {
    const matched = tracks.filter(t => t.match_status === 'matched').length;
    const missing = tracks.filter(t => t.match_status === 'missing').length;
    const extra = tracks.filter(t => t.match_status === 'extra').length;

    const statsEl = document.getElementById('server-editor-stats');
    if (statsEl) {
        statsEl.innerHTML = `
            <div class="server-editor-stat"><div class="server-editor-stat-num matched">${matched}</div><div class="server-editor-stat-label">Matched</div></div>
            <div class="server-editor-stat"><div class="server-editor-stat-num missing">${missing}</div><div class="server-editor-stat-label">Missing</div></div>
            ${extra > 0 ? `<div class="server-editor-stat"><div class="server-editor-stat-num extra">${extra}</div><div class="server-editor-stat-label">Extra</div></div>` : ''}
        `;
    }

    const editor = document.getElementById('server-editor');
    if (editor) {
        editor.querySelectorAll('.discog-filter').forEach(btn => {
            const f = btn.dataset.filter;
            if (f === 'all') btn.textContent = `All (${tracks.length})`;
            else if (f === 'matched') btn.textContent = `Matched (${matched})`;
            else if (f === 'missing') btn.textContent = `Missing (${missing})`;
            else if (f === 'extra') btn.textContent = `Extra (${extra})`;
        });
    }

    const footer = document.getElementById('server-editor-footer');
    if (footer) footer.textContent = `${matched}/${matched + missing} matched${extra > 0 ? ` · ${extra} extra on server` : ''}`;
}

// Read-only view of the server playlist's ACTUAL order. Source order is the source
// of truth; this just lets the user SEE how the server currently differs (no editing).
function _showServerOrder() {
    const esc = typeof _esc === 'function' ? _esc : s => s;
    const order = (_serverEditorState && _serverEditorState.serverOrder) || [];
    const serverType = (_serverEditorState && _serverEditorState.serverType) || 'server';
    const serverLabel = serverType.charAt(0).toUpperCase() + serverType.slice(1);

    document.getElementById('server-order-modal')?.remove();
    const rows = order.map((t, i) => {
        const art = t.thumb
            ? `<img class="server-order-art" src="${esc(t.thumb)}" alt="" loading="lazy" onerror="this.outerHTML='<div class=&quot;server-order-art server-order-art-ph&quot;>&#9835;</div>'">`
            : `<div class="server-order-art server-order-art-ph">&#9835;</div>`;
        return `
        <div class="server-order-row">
            <span class="server-order-num">${i + 1}</span>
            ${art}
            <div class="server-order-meta">
                <span class="server-order-title">${esc(t.title || 'Unknown')}</span>
                <span class="server-order-artist">${esc(t.artist || '')}</span>
            </div>
        </div>`;
    }).join('');

    // Align actions — reorder the server playlist to the source order. Two choices
    // for server-only "extra" tracks. Order-only: never adds the missing tracks
    // (that's the normal sync's job). Supported where reorder is implemented.
    const canAlign = serverType === 'navidrome' || serverType === 'plex' || serverType === 'jellyfin';
    const alignFoot = canAlign ? `
            <div class="server-order-foot">
                <div class="server-order-foot-label">Align this playlist to the source order</div>
                <div class="server-order-actions">
                    <button type="button" class="server-align-btn" onclick="_alignPlaylist(false)">
                        <span class="server-align-btn-t">Mirror source</span>
                        <span class="server-align-btn-d">reorder to match the source &middot; remove server-only tracks</span>
                    </button>
                    <button type="button" class="server-align-btn" onclick="_alignPlaylist(true)">
                        <span class="server-align-btn-t">Keep extras</span>
                        <span class="server-align-btn-d">reorder to match the source &middot; keep server-only tracks at the end</span>
                    </button>
                </div>
                <div class="server-order-foot-note">Missing tracks aren't added here &mdash; run a normal sync for those.</div>
            </div>` : '';

    const overlay = document.createElement('div');
    overlay.id = 'server-order-modal';
    overlay.className = 'server-order-overlay';
    overlay.innerHTML = `
        <div class="server-order-dialog" onclick="event.stopPropagation()">
            <div class="server-order-head">
                <div>
                    <div class="server-order-h1">${esc(serverLabel)} playlist order</div>
                    <div class="server-order-sub">the actual order on your server · source order stays the source of truth</div>
                </div>
                <button type="button" class="server-order-close" onclick="document.getElementById('server-order-modal').remove()">&times;</button>
            </div>
            <div class="server-order-list">${rows || '<div class="server-order-empty">No server tracks.</div>'}</div>
            ${alignFoot}
        </div>`;
    overlay.onclick = () => overlay.remove();
    document.body.appendChild(overlay);
}

// Align the server playlist's ORDER to the source (the "Align playlists" action).
// Sends the matched server-track ids in SOURCE order + the extras choice; the
// backend validates they're all in the playlist and rewrites. Order-only.
async function _alignPlaylist(keepExtras) {
    const st = _serverEditorState;
    if (!st || !st.playlistId) return;
    const matchedIds = (Array.isArray(st.tracks) ? st.tracks : [])
        .filter(t => t.match_status === 'matched' && t.server_track && t.server_track.id != null)
        .map(t => String(t.server_track.id));
    if (!matchedIds.length) {
        if (typeof showToast === 'function') showToast('Nothing to align', 'warning');
        return;
    }
    try {
        const resp = await fetch(`/api/server/playlist/${st.playlistId}/align`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                playlist_name: st.playlistName || '',
                matched_ids: matchedIds,
                keep_extras: !!keepExtras,
            }),
        });
        const data = await resp.json();
        if (data && data.success) {
            if (typeof showToast === 'function') showToast(`Playlist order aligned (${data.track_count} tracks)`, 'success');
            document.getElementById('server-order-modal')?.remove();
            _serverEditorRefresh();
        } else {
            if (typeof showToast === 'function') showToast((data && data.error) || 'Align failed', 'error');
        }
    } catch (e) {
        if (typeof showToast === 'function') showToast('Align failed: ' + e.message, 'error');
    }
}

function _formatDurationMs(ms) {
    if (!ms) return '';
    const s = Math.round(ms / 1000);
    return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`;
}

function _renderCompareColumns(tracks) {
    const sourceScroll = document.getElementById('server-col-source-scroll');
    const serverScroll = document.getElementById('server-col-server-scroll');
    if (!sourceScroll || !serverScroll) return;

    let sourceHTML = '';
    let serverHTML = '';

    tracks.forEach((t, i) => {
        const src = t.source_track;
        const svr = t.server_track;
        const status = t.match_status;
        const pairId = `pair-${i}`;

        // ── Source (left) column ──
        if (src) {
            const dur = _formatDurationMs(src.duration_ms);
            sourceHTML += `
            <div class="server-track-item ${status}" data-pair-id="${pairId}" data-index="${i}" data-status="${status}"
                 onclick="_compareTrackClick('source', ${i})">
                <div class="server-track-num">${src.position != null ? src.position : i + 1}</div>
                <div class="server-track-art">
                    ${src.image_url ? `<img src="${src.image_url}" alt="" loading="lazy">` : '<div class="server-track-art-empty"></div>'}
                </div>
                <div class="server-track-info">
                    <div class="server-track-title">${_esc(src.name)}</div>
                    <div class="server-track-artist">${_esc(src.artist || '')}</div>
                </div>
                <div class="server-track-duration">${dur}</div>
                <div class="server-track-status-dot"></div>
            </div>`;
        } else {
            // Extra track — no source
            sourceHTML += `
            <div class="server-track-item extra-gap" data-pair-id="${pairId}" data-index="${i}" data-status="${status}">
                <div class="server-track-empty-slot extra">
                    <span class="empty-slot-label">No source track</span>
                </div>
            </div>`;
        }

        // ── Server (right) column ──
        if (svr) {
            const dur = _formatDurationMs(svr.duration);
            const conf = t.confidence != null ? t.confidence : null;
            let confBadge = '';
            if (status === 'matched' && conf != null) {
                const pct = Math.round(conf * 100);
                const cls = pct >= 100 ? 'exact' : pct >= 90 ? 'high' : 'fuzzy';
                confBadge = `<span class="server-track-conf ${cls}" title="Title similarity">${pct}%</span>`;
            }
            serverHTML += `
            <div class="server-track-item ${status}" data-pair-id="${pairId}" data-index="${i}" data-status="${status}"
                 onclick="_compareTrackClick('server', ${i})">
                <div class="server-track-num">${i + 1}</div>
                <div class="server-track-art">
                    ${svr.thumb ? `<img src="${svr.thumb}" alt="" loading="lazy">` : '<div class="server-track-art-empty"></div>'}
                </div>
                <div class="server-track-info">
                    <div class="server-track-title">${_esc(svr.title)}</div>
                    <div class="server-track-artist">${_esc(svr.artist || '')}</div>
                </div>
                ${confBadge}
                <div class="server-track-duration">${dur}</div>
                <div class="server-track-actions">
                    ${status === 'matched' ? `<button class="server-track-swap-btn" onclick="event.stopPropagation(); serverSearchReplace(${i}, 'replace')" title="Swap for different version">
                        <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M7 16V4m0 0L3 8m4-4l4 4M17 8v12m0 0l4-4m-4 4l-4-4"/></svg>
                    </button>` : ''}
                    <button class="server-track-remove-btn" onclick="event.stopPropagation(); _serverRemoveTrack(${i}, '${svr.id}')" title="Remove from playlist">
                        <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round"><path d="M18 6L6 18M6 6l12 12"/></svg>
                    </button>
                </div>
                <div class="server-track-status-dot"></div>
            </div>`;
        } else {
            // Missing on server — clickable empty slot
            const hint = src ? `${src.artist || ''} — ${src.name}` : '';
            serverHTML += `
            <div class="server-track-item empty-slot-wrap" data-pair-id="${pairId}" data-index="${i}" data-status="${status}"
                 onclick="serverSearchReplace(${i}, 'add')">
                <div class="server-track-empty-slot missing">
                    <div class="empty-slot-icon">
                        <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><circle cx="11" cy="11" r="8"/><path d="m21 21-4.35-4.35"/></svg>
                    </div>
                    <span class="empty-slot-label">Find &amp; add</span>
                    <span class="empty-slot-hint">${_esc(hint)}</span>
                </div>
            </div>`;
        }
    });

    sourceScroll.innerHTML = sourceHTML;
    serverScroll.innerHTML = serverHTML;
}

function _setupScrollLinking() {
    const sourceScroll = document.getElementById('server-col-source-scroll');
    const serverScroll = document.getElementById('server-col-server-scroll');
    if (!sourceScroll || !serverScroll) return;

    // Remove old listeners to prevent accumulation on refresh
    if (window._serverScrollAC) window._serverScrollAC.abort();
    window._serverScrollAC = new AbortController();
    const signal = window._serverScrollAC.signal;

    let syncing = false;

    const syncScroll = (from, to) => {
        if (syncing) return;
        syncing = true;
        const maxFrom = from.scrollHeight - from.clientHeight;
        const maxTo = to.scrollHeight - to.clientHeight;
        if (maxFrom > 0 && maxTo > 0) {
            to.scrollTop = (from.scrollTop / maxFrom) * maxTo;
        }
        requestAnimationFrame(() => { syncing = false; });
    };

    sourceScroll.addEventListener('scroll', () => syncScroll(sourceScroll, serverScroll), { signal });
    serverScroll.addEventListener('scroll', () => syncScroll(serverScroll, sourceScroll), { signal });
}

function _compareTrackClick(side, index) {
    const otherSide = side === 'source' ? 'server' : 'source';
    const otherScroll = document.getElementById(`server-col-${otherSide}-scroll`);
    const pairId = `pair-${index}`;

    // Clear previous highlights
    document.querySelectorAll('.server-track-item.highlighted').forEach(el => el.classList.remove('highlighted'));

    // Highlight both paired items
    document.querySelectorAll(`[data-pair-id="${pairId}"]`).forEach(el => el.classList.add('highlighted'));

    // Scroll the OTHER column to show the paired item
    const target = otherScroll?.querySelector(`[data-pair-id="${pairId}"]`);
    if (target) {
        target.scrollIntoView({ behavior: 'smooth', block: 'center' });
    }
}

function _serverEditorRefresh() {
    _openServerCompareView(_serverEditorState.playlistId, _serverEditorState.playlistName, _serverEditorState.mirroredPlaylist);
}

/**
 * Export the currently-open server playlist as an M3U file. Takes the tracks
 * physically present ON the server (matched + extra) and reuses the shared M3U
 * writer, which resolves each to its real library file path (+ the configured
 * entry_base_path prefix) so media servers like Music Assistant can read it.
 * force:true bypasses the auto-save "m3u_export.enabled" gate — this is a manual
 * on-demand export.
 */
async function exportServerPlaylistM3U() {
    const st = _serverEditorState;
    const btn = document.getElementById('server-editor-export-btn');
    const tracks = (st && Array.isArray(st.tracks) ? st.tracks : [])
        .filter(t => t.server_track)
        .map(t => ({
            name: t.server_track.title,
            artist: t.server_track.artist || '',
            duration_ms: t.server_track.duration || 0,
        }));
    if (!tracks.length) {
        showToast('No server tracks to export', 'warning');
        return;
    }
    const orig = btn ? btn.textContent : '';
    if (btn) { btn.disabled = true; btn.textContent = '⏳ Exporting…'; }
    try {
        const res = await fetch('/api/generate-playlist-m3u', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                playlist_name: st.playlistName || 'Playlist',
                tracks,
                context_type: 'playlist',
                save_to_disk: true,
                force: true,
            }),
        });
        const data = await res.json();
        if (!res.ok || data.success === false) {
            throw new Error(data.error || 'Export failed');
        }
        // Download the .m3u to the browser (same as the other Export-as-M3U
        // buttons) — force=true also saved it server-side for media servers.
        const name = (st.playlistName || 'Playlist').replace(/[/\\?%*:|"<>]/g, '-');
        const blob = new Blob([data.m3u_content || ''], { type: 'audio/x-mpegurl;charset=utf-8' });
        const url = URL.createObjectURL(blob);
        const link = document.createElement('a');
        link.href = url;
        link.download = `${name}.m3u`;
        document.body.appendChild(link);
        link.click();
        document.body.removeChild(link);
        URL.revokeObjectURL(url);
        // `found` = server tracks resolved to a real library file path; any not in
        // SoulSync's library are skipped (can't write a path for them).
        const found = data.stats && data.stats.found != null ? data.stats.found : tracks.length;
        const note = found < tracks.length ? ` (${found}/${tracks.length} in library)` : ` (${found} tracks)`;
        showToast(`Exported M3U: ${st.playlistName}${note}`, 'success');
    } catch (e) {
        showToast(`M3U export failed: ${e.message}`, 'error');
    } finally {
        if (btn) { btn.disabled = false; btn.textContent = orig || '📋 Export M3U'; }
    }
}

function serverEditorBack() {
    const container = document.getElementById('server-playlist-container');
    const editor = document.getElementById('server-editor');
    if (editor) editor.style.display = 'none';
    if (container) container.style.display = '';
}

function _serverEditorFilter(btn, filter) {
    btn.closest('.server-editor-filters').querySelectorAll('.discog-filter').forEach(b => b.classList.remove('active'));
    btn.classList.add('active');
    _applyServerEditorFilter(filter);
}

// Show/hide rows for the given filter. Split out of the click handler because a
// re-render must re-apply it (#1005: reloading the compare view rebuilt both
// columns fully visible while the "Missing" pill stayed active — pill said
// Missing, columns showed everything).
function _applyServerEditorFilter(filter) {
    ['server-col-source-scroll', 'server-col-server-scroll'].forEach(colId => {
        document.querySelectorAll(`#${colId} .server-track-item`).forEach(item => {
            const status = item.dataset.status;
            item.style.display = (filter === 'all' || status === filter) ? '' : 'none';
        });
    });
}

function _activeServerEditorFilter() {
    const btn = document.querySelector('#server-editor .server-editor-filters .discog-filter.active[data-filter]');
    return btn ? btn.dataset.filter : 'all';
}

// Repaint stats + both columns from _serverEditorState.tracks, keeping the
// active filter applied and each column's scroll position (an in-place row
// patch must not throw the user back to the top of a 2k-track list).
function _rerenderCompare() {
    const tracks = _serverEditorState.tracks || [];
    const sourceScroll = document.getElementById('server-col-source-scroll');
    const serverScroll = document.getElementById('server-col-server-scroll');
    const keep = [sourceScroll?.scrollTop || 0, serverScroll?.scrollTop || 0];
    _updateCompareStats(tracks);
    _renderCompareColumns(tracks);
    _applyServerEditorFilter(_activeServerEditorFilter());
    if (sourceScroll) sourceScroll.scrollTop = keep[0];
    if (serverScroll) serverScroll.scrollTop = keep[1];
}

// ── Track Search / Replace ──

async function serverSearchReplace(trackIndex, mode) {
    const track = _serverEditorState.tracks[trackIndex];
    if (!track) return;

    const src = track.source_track || {};
    const svr = track.server_track || {};
    // Search by track name only first (more reliable than "artist trackname" blob)
    const searchQuery = src.name ? src.name.trim() : (svr.title || '').trim();
    const contextArtist = src.artist || svr.artist || '';
    const contextName = src.name || svr.title || '';
    // Pass the source artist as a relevance hint so an exact title+artist match
    // ranks to the top of the library search instead of being buried under
    // same-title tracks by other artists (#: "bad guy" by Billie Eilish).
    _serverEditorState.searchArtist = contextArtist;

    const existing = document.getElementById('server-search-overlay');
    if (existing) existing.remove();

    const overlay = document.createElement('div');
    overlay.id = 'server-search-overlay';
    overlay.className = 'server-search-overlay';
    overlay.innerHTML = `
        <div class="server-search-popover" id="server-search-popover">
            <div class="server-search-header">
                <div>
                    <div class="server-search-title">${mode === 'replace' ? 'Swap Track' : 'Add Track to Server'}</div>
                    ${contextName ? `<div class="server-search-context">
                        <span class="server-search-context-label">Source:</span>
                        <span class="server-search-context-artist">${_esc(contextArtist)}</span>
                        <span class="server-search-context-sep">—</span>
                        <span class="server-search-context-name">${_esc(contextName)}</span>
                    </div>` : ''}
                </div>
                <button class="server-search-close" onclick="document.getElementById('server-search-overlay')?.remove()">&times;</button>
            </div>
            <div class="server-search-input-wrap">
                <div class="server-search-input-icon">
                    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><circle cx="11" cy="11" r="8"/><path d="m21 21-4.35-4.35"/></svg>
                </div>
                <input type="text" class="server-search-input" id="server-search-input" value="${_esc(searchQuery)}" placeholder="Search by track name, artist, or album..." onkeydown="if(event.key==='Enter') _serverSearchExecute()">
            </div>
            <div class="server-search-results-header" id="server-search-results-header"></div>
            <div class="server-search-results" id="server-search-results">
                <div class="server-search-hint">
                    <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" style="margin-bottom:6px;opacity:0.4"><circle cx="11" cy="11" r="8"/><path d="m21 21-4.35-4.35"/></svg>
                    <br>Searching...
                </div>
            </div>
        </div>
    `;
    // Click overlay background or press Escape to close
    overlay.addEventListener('click', e => { if (e.target === overlay) overlay.remove(); });
    overlay._escHandler = e => { if (e.key === 'Escape') overlay.remove(); };
    document.addEventListener('keydown', overlay._escHandler);
    // Clean up Escape listener when overlay is removed
    const obs = new MutationObserver(() => {
        if (!document.body.contains(overlay)) { document.removeEventListener('keydown', overlay._escHandler); obs.disconnect(); }
    });
    obs.observe(document.body, { childList: true });

    const popover = overlay.querySelector('.server-search-popover');
    popover.dataset.trackIndex = trackIndex;
    popover.dataset.mode = mode;

    document.body.appendChild(overlay);
    requestAnimationFrame(() => overlay.classList.add('visible'));
    document.getElementById('server-search-input')?.focus();
    document.getElementById('server-search-input')?.select();

    _serverSearchExecute();
}

async function _serverSearchExecute() {
    const input = document.getElementById('server-search-input');
    const results = document.getElementById('server-search-results');
    const resultsHeader = document.getElementById('server-search-results-header');
    const popover = document.getElementById('server-search-popover');
    if (!input || !results || !popover) return;

    const query = input.value.trim();
    if (!query) {
        results.innerHTML = '<div class="server-search-hint">Type a search query</div>';
        if (resultsHeader) resultsHeader.textContent = '';
        return;
    }

    results.innerHTML = '<div class="server-search-hint"><div class="server-search-spinner"></div>Searching library...</div>';
    if (resultsHeader) resultsHeader.textContent = '';

    try {
        const artistHint = (_serverEditorState && _serverEditorState.searchArtist) || '';
        const response = await fetch(`/api/library/search-tracks?q=${encodeURIComponent(query)}&limit=20`
            + (artistHint ? `&artist=${encodeURIComponent(artistHint)}` : ''));
        const data = await response.json();

        if (!data.success || !data.tracks || data.tracks.length === 0) {
            results.innerHTML = `<div class="server-search-hint">
                <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" style="margin-bottom:6px;opacity:0.3"><path d="M9.172 14.828a4 4 0 015.656 0M9 10h.01M15 10h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z"/></svg>
                <br>No results found<br><span style="font-size:10px;opacity:0.5">Try different keywords or a shorter query</span>
            </div>`;
            return;
        }

        const trackIndex = parseInt(popover.dataset.trackIndex);
        const mode = popover.dataset.mode;

        // Kept so a Select can patch the compare row in place (no full reload)
        _serverEditorState._searchResults = data.tracks;

        if (resultsHeader) resultsHeader.textContent = `${data.tracks.length} result${data.tracks.length !== 1 ? 's' : ''}`;

        results.innerHTML = data.tracks.map((t, i) => {
            const ext = (t.file_path || '').split('.').pop().toUpperCase();
            const format = ['FLAC', 'MP3', 'OPUS', 'OGG', 'M4A', 'AAC', 'WAV'].includes(ext) ? (ext === 'M4A' ? 'AAC' : ext) : '';
            const dur = _formatDurationMs(t.duration);
            const bitrateStr = t.bitrate ? `${t.bitrate}k` : '';
            return `
                <div class="server-search-result" onclick="_serverSelectTrack(${trackIndex}, '${mode}', '${t.id}', this)" style="animation-delay:${i * 0.03}s">
                    <div class="server-search-result-art">
                        ${t.album_thumb_url ? `<img src="${t.album_thumb_url}" alt="" loading="lazy">` : '<div class="server-search-result-art-empty"></div>'}
                    </div>
                    <div class="server-search-result-info">
                        <div class="server-search-result-title">${_esc(t.title)}</div>
                        <div class="server-search-result-meta">${_esc(t.artist_name)}${t.album_title ? ` · ${_esc(t.album_title)}` : ''}</div>
                    </div>
                    <div class="server-search-result-details">
                        ${format ? `<span class="server-search-format">${format}</span>` : ''}
                        ${bitrateStr ? `<span class="server-search-bitrate">${bitrateStr}</span>` : ''}
                        ${dur ? `<span class="server-search-dur">${dur}</span>` : ''}
                    </div>
                    <button class="server-search-select-btn">Select</button>
                </div>
            `;
        }).join('');

    } catch (e) {
        results.innerHTML = `<div class="server-search-hint">Error: ${e.message}</div>`;
    }
}

async function _serverSelectTrack(trackIndex, mode, newTrackId, el) {
    const track = _serverEditorState.tracks[trackIndex];
    if (!track) return;

    const btn = el.querySelector('.server-search-select-btn');
    if (btn) { btn.disabled = true; btn.textContent = '...'; }

    try {
        let response;
        if (mode === 'replace') {
            response = await fetch(`/api/server/playlist/${_serverEditorState.playlistId}/replace-track`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    old_track_id: track.server_track?.id,
                    new_track_id: newTrackId,
                    playlist_name: _serverEditorState.playlistName,
                })
            });
        } else {
            // Calculate the server-side position for this track
            // Count how many server tracks exist before this index
            let serverPos = 0;
            for (let k = 0; k < trackIndex; k++) {
                if (_serverEditorState.tracks[k]?.server_track) serverPos++;
            }
            // source_track carries source_track_id (Spotify ID) when this
            // came from a mirrored playlist — the backend uses it to
            // persist the Find & Add selection as a permanent match
            // override so future syncs auto-pair without user action.
            const srcTrack = track.source_track || {};
            response = await fetch(`/api/server/playlist/${_serverEditorState.playlistId}/add-track`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    track_id: newTrackId,
                    playlist_name: _serverEditorState.playlistName,
                    position: serverPos,
                    source_track_id: srcTrack.source_track_id || '',
                    source_title: srcTrack.name || '',
                    source_artist: srcTrack.artist || '',
                    // Provider of the source track, so the durable manual match
                    // (#787) records the right source. Retrieval is source-agnostic.
                    source: srcTrack.source || _serverEditorState.mirroredPlaylist?.source || '',
                })
            });
        }

        const data = await response.json();
        if (data.success) {
            showToast(data.message || 'Track updated', 'success');
            document.getElementById('server-search-overlay')?.remove();
            // Update playlist ID if server recreated it (Plex deletes+recreates)
            if (data.new_playlist_id) _serverEditorState.playlistId = data.new_playlist_id;

            // Patch the pair in place instead of re-fetching + re-matching the
            // whole playlist (#1005: one Find & Add threw a 2k-track compare
            // back to "Loading comparison..."). The server call above already
            // succeeded; the picked library track IS the server track (same id
            // space). The next full open recomputes order-status etc.
            const picked = (_serverEditorState._searchResults || [])
                .find(x => String(x.id) === String(newTrackId));
            if (picked) {
                track.server_track = {
                    id: String(newTrackId),
                    title: picked.title || '',
                    artist: picked.artist_name || '',
                    album: picked.album_title || '',
                    duration: picked.duration || 0,
                    thumb: picked.album_thumb_url || '',
                };
                track.match_status = 'matched';
                track.confidence = 1.0;
                track.override = true;
                // Link case: the picked track may already sit in the playlist as
                // an "extra" row (the backend links instead of duplicating) —
                // drop that row or the track shows twice until the next full load.
                const dupIdx = _serverEditorState.tracks.findIndex(p =>
                    p !== track && !p.source_track && p.server_track &&
                    String(p.server_track.id) === String(newTrackId));
                if (dupIdx >= 0) _serverEditorState.tracks.splice(dupIdx, 1);
                _rerenderCompare();
            } else {
                // couldn't identify the picked track locally — full reload fallback
                _openServerCompareView(_serverEditorState.playlistId, _serverEditorState.playlistName, _serverEditorState.mirroredPlaylist);
            }
        } else {
            showToast(data.error || 'Failed to update track', 'error');
            if (btn) { btn.disabled = false; btn.textContent = 'Select'; }
        }
    } catch (e) {
        showToast('Error: ' + e.message, 'error');
        if (btn) { btn.disabled = false; btn.textContent = 'Select'; }
    }
}

async function _serverRemoveTrack(trackIndex, serverTrackId) {
    if (!serverTrackId) return;

    const track = _serverEditorState.tracks[trackIndex];
    const trackTitle = track?.server_track?.title || 'this track';

    if (!await showConfirmDialog({ title: 'Remove Track', message: `Remove "${trackTitle}" from this playlist?`, confirmText: 'Remove', destructive: true })) return;

    try {
        const response = await fetch(`/api/server/playlist/${_serverEditorState.playlistId}/remove-track`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                track_id: serverTrackId,
                playlist_name: _serverEditorState.playlistName,
            })
        });

        const data = await response.json();
        if (data.success) {
            showToast(data.message || 'Track removed', 'success');
            _serverEditorState.playlistId = data.new_playlist_id || _serverEditorState.playlistId;
            // Patch in place (#1005 — same as Find & Add): a matched pair loses
            // its server side and becomes missing; an extra row disappears.
            if (track && track.source_track) {
                track.server_track = null;
                track.match_status = 'missing';
                track.confidence = 0.0;
            } else {
                _serverEditorState.tracks.splice(trackIndex, 1);
            }
            _rerenderCompare();
        } else {
            showToast(data.error || 'Failed to remove track', 'error');
        }
    } catch (e) {
        showToast('Error: ' + e.message, 'error');
    }
}


// Auto-refresh sync cards every 30 seconds when on dashboard
setInterval(() => {
    if (typeof currentPage !== 'undefined' && currentPage === 'dashboard') {
        loadDashboardSyncHistory();
    }
}, 30000);

async function loadDashboardSyncHistory() {
    // Don't poll the auth-gated sync-history endpoint while the app is locked —
    // it would 401 every 30s cycle (the result is discarded anyway). Resumes
    // automatically on unlock (init.js removes 'app-locked').
    if (document.body.classList.contains('app-locked')) return;
    const container = document.getElementById('sync-history-cards');
    if (!container) return;

    try {
        const response = await fetch('/api/sync/history?limit=10');
        if (response.status === 401) {
            // Session lapsed (e.g. the server restarted) while this tab still
            // believed it was unlocked, so the guard above couldn't fire. Surface
            // the correct unlock screen — both add 'app-locked', which stops the
            // poll until the user re-authenticates (same as a fresh page load).
            const info = await response.json().catch(() => ({}));
            if (info.login_required && typeof showLoginScreen === 'function') {
                showLoginScreen();
            } else if (typeof showLaunchPinScreen === 'function') {
                showLaunchPinScreen();
            }
            return;
        }
        if (!response.ok) return;

        const data = await response.json();
        // Filter to only show playlist syncs — not album downloads or wishlist processing
        const entries = (data.entries || []).filter(e => e.sync_type === 'playlist' || !e.sync_type);

        if (entries.length === 0) {
            container.innerHTML = '<div class="sync-history-empty">No syncs yet</div>';
            return;
        }

        container.innerHTML = entries.map((entry, cardIndex) => {
            const found = entry.tracks_found || 0;
            const total = entry.total_tracks || 0;
            const downloaded = entry.tracks_downloaded || 0;
            const failed = entry.tracks_failed || 0;
            const pct = total > 0 ? Math.round((found / total) * 100) : 0;

            // Health color
            let healthClass = 'health-good';
            if (pct < 50) healthClass = 'health-bad';
            else if (pct < 80) healthClass = 'health-warn';

            // Source badge
            const sourceLabels = { spotify: 'Spotify', tidal: 'Tidal', deezer: 'Deezer', youtube: 'YouTube', beatport: 'Beatport', wishlist: 'Wishlist' };
            const sourceLabel = sourceLabels[entry.source] || entry.source || 'Unknown';

            // Time
            const timeStr = entry.started_at ? _relativeTime(entry.started_at) : '';

            // Name
            const name = entry.artist_name
                ? `${entry.artist_name} — ${entry.album_name || entry.playlist_name}`
                : entry.playlist_name || 'Unknown';

            return `
                <div class="sync-history-card ${healthClass}" onclick="openSyncDetailModal(${entry.id})" style="animation-delay: ${cardIndex * 0.05}s">
                    <button class="sync-card-delete" onclick="event.stopPropagation(); deleteSyncHistoryCard(${entry.id}, this)" title="Remove">&times;</button>
                    <div class="sync-card-thumb">
                        ${entry.thumb_url ? `<img src="${entry.thumb_url}" alt="" loading="lazy">` : '<div class="sync-card-thumb-placeholder">&#9835;</div>'}
                    </div>
                    <div class="sync-card-info">
                        <div class="sync-card-name">${typeof _esc === 'function' ? _esc(name) : name}</div>
                        <div class="sync-card-meta">
                            <span class="sync-card-source">${sourceLabel}</span>
                            <span class="sync-card-time">${timeStr}</span>
                        </div>
                    </div>
                    <div class="sync-card-stats">
                        <div class="sync-card-pct">${pct}%</div>
                        <div class="sync-card-bar">
                            <div class="sync-card-bar-fill" style="width: ${pct}%"></div>
                        </div>
                        <div class="sync-card-counts">${found}/${total} matched${downloaded > 0 ? ` · ${downloaded} ⬇` : ''}${failed > 0 ? ` · ${failed} ✗` : ''}</div>
                    </div>
                </div>
            `;
        }).join('');

    } catch (e) {
        console.warn('Failed to load sync history for dashboard:', e);
    }
}

function _relativeTime(dateStr) {
    try {
        const d = new Date(dateStr);
        const now = new Date();
        const diffMs = now - d;
        const mins = Math.floor(diffMs / 60000);
        if (mins < 1) return 'just now';
        if (mins < 60) return `${mins}m ago`;
        const hrs = Math.floor(mins / 60);
        if (hrs < 24) return `${hrs}h ago`;
        const days = Math.floor(hrs / 24);
        if (days < 7) return `${days}d ago`;
        return d.toLocaleDateString();
    } catch (e) { return ''; }
}

// Re-add a synced unmatched track to the wishlist from the sync-detail modal, with
// the same context the original sync used (resolved server-side from the entry).
async function _readdSyncWishlist(entryId, index, el) {
    if (el && el.dataset.busy) return;
    if (el) { el.dataset.busy = '1'; el.classList.add('is-busy'); }
    try {
        const resp = await fetch(`/api/sync/history/${entryId}/track/${index}/wishlist`, { method: 'POST' });
        const data = await resp.json();
        if (data && data.success) {
            if (el) {
                el.classList.remove('is-busy');
                el.classList.add('is-done');
                el.disabled = true;
                el.innerHTML = data.added ? '&#10003; Re-added' : '&#10003; On wishlist';
            }
            if (typeof showToast === 'function') {
                showToast(
                    data.added ? `Re-added "${data.name}" to wishlist` : `"${data.name}" is already on the wishlist`,
                    data.added ? 'success' : 'info',
                );
            }
        } else {
            if (el) { delete el.dataset.busy; el.classList.remove('is-busy'); }
            if (typeof showToast === 'function') showToast((data && data.error) || 'Could not re-add to wishlist', 'error');
        }
    } catch (e) {
        if (el) { delete el.dataset.busy; el.classList.remove('is-busy'); }
        if (typeof showToast === 'function') showToast('Could not re-add to wishlist: ' + e.message, 'error');
    }
}

async function openSyncDetailModal(entryId) {
    try {
        showLoadingOverlay('Loading sync details...');
        const response = await fetch(`/api/sync/history/${entryId}`);
        const data = await response.json();
        hideLoadingOverlay();

        if (!data.success || !data.entry) {
            showToast('Could not load sync details', 'error');
            return;
        }

        const entry = data.entry;
        const trackResults = entry.track_results || [];
        const name = entry.artist_name
            ? `${entry.artist_name} — ${entry.album_name || entry.playlist_name}`
            : entry.playlist_name || 'Unknown';

        // Build modal
        const overlay = document.createElement('div');
        overlay.className = 'discog-modal-overlay';
        overlay.id = 'sync-detail-overlay';

        const found = entry.tracks_found || 0;
        const total = entry.total_tracks || 0;
        const downloaded = entry.tracks_downloaded || 0;

        let trackRowsHtml = '';
        if (trackResults.length > 0) {
            trackRowsHtml = trackResults.map((t, i) => {
                const statusIcon = t.status === 'found' ? '✅' : '❌';
                const statusClass = t.status === 'found' ? 'matched' : 'unmatched';
                const confPct = Math.round((t.confidence || 0) * 100);
                const confClass = confPct >= 80 ? 'conf-high' : confPct >= 50 ? 'conf-mid' : 'conf-low';
                let dlIcon = '';
                if (t.download_status === 'completed') dlIcon = '✅';
                else if (t.download_status === 'failed') dlIcon = '❌';
                else if (t.download_status === 'not_found') dlIcon = '🔇';
                else if (t.download_status === 'cancelled') dlIcon = '🚫';

                let dlDisplay = dlIcon;
                if (!dlDisplay && t.download_status === 'wishlist') {
                    // Wing-it fallback stubs (no real metadata) were never actually
                    // wishlisted by the sync — show them as plain, non-clickable.
                    const isWingIt = String(t.source_track_id || '').startsWith('wing_it_');
                    if (isWingIt) {
                        dlDisplay = `<span class="sync-dl-unmatched" title="Couldn't be resolved to real metadata (wing-it fallback), so it was never added to the wishlist">Unmatched</span>`;
                    } else {
                        // Clickable: re-add this exact track to the wishlist with the
                        // same context the sync originally used.
                        dlDisplay = `<button type="button" class="sync-dl-wishlist sync-dl-wishlist-btn" `
                            + `onclick="_readdSyncWishlist(${entryId}, ${i}, this)" `
                            + `title="Re-add to wishlist with the original sync context">&rarr; Wishlist</button>`;
                    }
                }

                return `
                    <tr class="sync-detail-row ${statusClass}">
                        <td class="sync-detail-num">${i + 1}</td>
                        <td class="sync-detail-art">
                            ${t.image_url ? `<img src="${t.image_url}" alt="" loading="lazy">` : '<div class="sync-detail-art-empty"></div>'}
                        </td>
                        <td class="sync-detail-track">${_esc(t.name || '')}</td>
                        <td class="sync-detail-artist">${_esc(t.artist || '')}</td>
                        <td class="sync-detail-album">${_esc(t.album || '')}</td>
                        <td class="sync-detail-status">${statusIcon}</td>
                        <td class="sync-detail-conf"><span class="conf-badge ${confClass}">${confPct}%</span></td>
                        <td class="sync-detail-dl">${dlDisplay}</td>
                    </tr>
                `;
            }).join('');
        } else {
            // Fallback to tracks_json if no track_results (old syncs before data caching)
            const tracks = entry.tracks || [];
            const esc = typeof _esc === 'function' ? _esc : s => s;
            trackRowsHtml = `
                <tr><td colspan="8" class="sync-detail-notice">
                    <div class="sync-detail-notice-text">Per-track match data not available for this sync.<br>Re-sync this playlist to see detailed match results.</div>
                </td></tr>
            ` + tracks.map((t, i) => {
                const artists = t.artists || [];
                const artistName = artists.length > 0 ? (typeof artists[0] === 'string' ? artists[0] : artists[0]?.name || '') : '';
                const albumName = typeof t.album === 'object' ? (t.album?.name || '') : (t.album || '');
                return `
                    <tr class="sync-detail-row no-data">
                        <td class="sync-detail-num">${i + 1}</td>
                        <td class="sync-detail-art"></td>
                        <td class="sync-detail-track">${esc(t.name || '')}</td>
                        <td class="sync-detail-artist">${esc(artistName)}</td>
                        <td class="sync-detail-album">${esc(albumName)}</td>
                        <td class="sync-detail-status" colspan="3"></td>
                    </tr>
                `;
            }).join('');
        }

        // Count stats for filter bar
        const matchedCount = trackResults.filter(t => t.status === 'found').length;
        const unmatchedCount = trackResults.filter(t => t.status !== 'found').length;
        const downloadedCount = trackResults.filter(t => t.download_status === 'completed').length;

        overlay.innerHTML = `
            <div class="discog-modal">
                <div class="discog-modal-hero" ${entry.thumb_url ? `style="background-image:url('${entry.thumb_url}')"` : ''}>
                    <div class="discog-modal-hero-overlay"></div>
                    <div class="discog-modal-hero-content">
                        <h2 class="discog-modal-title">Sync Details</h2>
                        <p class="discog-modal-artist">${_esc(name)}</p>
                    </div>
                    <button class="discog-modal-close" onclick="document.getElementById('sync-detail-overlay')?.remove()">&times;</button>
                </div>
                <div class="discog-filter-bar">
                    <div class="discog-filters">
                        <button class="discog-filter active" data-filter="all" onclick="_syncDetailFilter(this, 'all')">All (${total})</button>
                        <button class="discog-filter" data-filter="matched" onclick="_syncDetailFilter(this, 'matched')">Matched (${matchedCount})</button>
                        <button class="discog-filter" data-filter="unmatched" onclick="_syncDetailFilter(this, 'unmatched')">Unmatched (${unmatchedCount})</button>
                        ${downloadedCount > 0 ? `<button class="discog-filter" data-filter="downloaded" onclick="_syncDetailFilter(this, 'downloaded')">Downloaded (${downloadedCount})</button>` : ''}
                    </div>
                </div>
                <div class="sync-detail-table-wrap">
                    <table class="sync-detail-table">
                        <thead>
                            <tr>
                                <th>#</th>
                                <th></th>
                                <th>Track</th>
                                <th>Artist</th>
                                <th>Album</th>
                                <th>Match</th>
                                <th>Conf.</th>
                                <th>Status</th>
                            </tr>
                        </thead>
                        <tbody id="sync-detail-tbody">
                            ${trackRowsHtml}
                        </tbody>
                    </table>
                </div>
                <div class="discog-footer">
                    <div class="discog-footer-info">${found} matched · ${downloaded} downloaded · ${total} total</div>
                    <div class="discog-footer-actions">
                        <button class="discog-cancel-btn" onclick="document.getElementById('sync-detail-overlay')?.remove()">Close</button>
                    </div>
                </div>
            </div>
        `;

        document.body.appendChild(overlay);
        requestAnimationFrame(() => overlay.classList.add('visible'));

    } catch (e) {
        hideLoadingOverlay();
        showToast('Failed to load sync details', 'error');
    }
}

async function deleteSyncHistoryCard(entryId, btnEl) {
    try {
        const card = btnEl.closest('.sync-history-card');
        if (card) {
            card.style.opacity = '0';
            card.style.transform = 'scale(0.9)';
        }
        const resp = await fetch(`/api/sync/history/${entryId}`, { method: 'DELETE' });
        if (resp.ok) {
            setTimeout(() => { if (card) card.remove(); }, 200);
        }
    } catch (e) {
        console.warn('Failed to delete sync entry:', e);
    }
}

function _syncDetailFilter(btn, filter) {
    // Update active button
    btn.closest('.discog-filters').querySelectorAll('.discog-filter').forEach(b => b.classList.remove('active'));
    btn.classList.add('active');

    // Filter rows
    document.querySelectorAll('#sync-detail-tbody .sync-detail-row').forEach(row => {
        if (filter === 'all') {
            row.style.display = '';
        } else if (filter === 'matched') {
            row.style.display = row.classList.contains('matched') ? '' : 'none';
        } else if (filter === 'unmatched') {
            row.style.display = row.classList.contains('unmatched') ? '' : 'none';
        } else if (filter === 'downloaded') {
            const dlCell = row.querySelector('.sync-detail-dl');
            row.style.display = dlCell && dlCell.textContent.trim() === '✅' ? '' : 'none';
        }
    });
}

// The Downloads page lived here: the list, the batch panel, the batch
// history and the verification/quarantine review queue — about 1,700
// lines. It is React's now (webui/src/routes/active-downloads/).
//
// _adlOpenBatchModal moved to core.js as window.openDownloadBatchModal
// rather than being deleted: it reads activeDownloadProcesses,
// rehydrateModal and WishlistModalState, which are script-scoped there.
