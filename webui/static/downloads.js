// WING IT — Download without metadata discovery
// ==================================================================================

function _toggleWingItDropdown(btn, urlHash) {
    // Remove any existing dropdown
    const existing = document.querySelector('.wing-it-dropdown.visible');
    if (existing) { existing.classList.remove('visible'); setTimeout(() => existing.remove(), 150); return; }

    const wrap = btn.closest('.wing-it-wrap');
    if (!wrap) return;

    const dropdown = document.createElement('div');
    dropdown.className = 'wing-it-dropdown';
    dropdown.innerHTML = `
        <button class="wing-it-dropdown-item" data-action="download">
            <span class="wing-it-dropdown-icon">⬇️</span>
            <span class="wing-it-dropdown-label">Download</span>
            <span class="wing-it-dropdown-hint">Raw names</span>
        </button>
        <button class="wing-it-dropdown-item" data-action="sync">
            <span class="wing-it-dropdown-icon">🔄</span>
            <span class="wing-it-dropdown-label">Sync to Server</span>
            <span class="wing-it-dropdown-hint">Best-effort</span>
        </button>
    `;

    dropdown.querySelectorAll('.wing-it-dropdown-item').forEach(item => {
        item.addEventListener('click', () => {
            dropdown.classList.remove('visible');
            setTimeout(() => dropdown.remove(), 150);
            const action = item.dataset.action;
            if (action === 'download') {
                _wingItAction(urlHash, 'download');
            } else {
                _wingItAction(urlHash, 'sync');
            }
        });
    });

    // Flip dropdown direction if button is in the top portion of viewport
    const btnRect = btn.getBoundingClientRect();
    if (btnRect.top < 200) dropdown.classList.add('flip-down');

    wrap.appendChild(dropdown);
    requestAnimationFrame(() => dropdown.classList.add('visible'));

    // Close on outside click
    setTimeout(() => {
        const closeHandler = e => {
            if (!dropdown.contains(e.target) && e.target !== btn) {
                dropdown.classList.remove('visible');
                setTimeout(() => dropdown.remove(), 150);
                document.removeEventListener('click', closeHandler);
            }
        };
        document.addEventListener('click', closeHandler);
    }, 50);
}

function _wingItAction(urlHash, action) {
    if (urlHash) {
        // Called from a modal — use _wingItFromModal logic
        const state = listenbrainzPlaylistStates[urlHash] || youtubePlaylistStates[urlHash] || {};
        const tracks = state.tracks || state.rawTracks || state.playlist?.tracks || [];
        const name = state.playlistName || state.name || state.playlist?.name || 'Playlist';
        const isTidal = state.is_tidal_playlist;
        const isLB = state.is_listenbrainz_playlist;
        const isBeatport = state.is_beatport_playlist;
        const isDeezer = state.is_deezer_playlist;
        const source = isLB ? 'ListenBrainz' : isTidal ? 'Tidal' : isDeezer ? 'Deezer' : isBeatport ? 'Beatport' : 'YouTube';

        if (!tracks.length) {
            showToast('No tracks available for Wing It', 'error');
            return;
        }

        if (action === 'sync') {
            // Sync inline — keep modal open
            _wingItSyncFromModal(urlHash, tracks, name, isLB);
        } else {
            // Download — close modal, open download modal
            const modal = document.getElementById(`youtube-discovery-modal-${urlHash}`);
            if (modal) modal.remove();
            const overlay = document.getElementById(`youtube-discovery-overlay-${urlHash}`);
            if (overlay) overlay.remove();
            wingItDownload(tracks, name, source, null, true);
        }
    }
}

async function _wingItSyncFromModal(urlHash, tracks, name, isLB) {
    showToast('Starting Wing It sync...', 'info');
    updateYouTubeModalButtons(urlHash, 'syncing');

    try {
        const syncTracks = tracks.map((t, i) => {
            let artists = t.artists || [];
            if (!Array.isArray(artists)) artists = [{ name: String(artists) }];
            return {
                id: t.id || t.source_track_id || `wing_it_${i}`,
                name: t.name || t.track_name || 'Unknown',
                artists: artists.map(a => typeof a === 'string' ? { name: a } : a),
                album: typeof t.album === 'object' ? t.album : { name: t.album || t.album_name || '' },
                duration_ms: t.duration_ms || 0,
            };
        });

        const res = await fetch('/api/wing-it/sync', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ tracks: syncTracks, playlist_name: name })
        });
        const data = await res.json();

        if (data.error) {
            showToast(`Sync failed: ${data.error}`, 'error');
            updateYouTubeModalButtons(urlHash, 'discovered');
            return;
        }

        if (isLB) {
            const state = listenbrainzPlaylistStates[urlHash];
            if (state) state.syncPlaylistId = data.sync_playlist_id;
            startListenBrainzSyncPolling(urlHash, data.sync_playlist_id);
        } else {
            startYouTubeSyncPolling(urlHash, data.sync_playlist_id);
        }
    } catch (e) {
        showToast('Sync failed: ' + e.message, 'error');
        updateYouTubeModalButtons(urlHash, 'discovered');
    }
}

async function wingItDownload(tracks, playlistName, source = 'playlist', cardIdentifier = null, skipConfirm = false) {
    if (!tracks || tracks.length === 0) {
        showToast('No tracks to download', 'error');
        return;
    }

    if (!skipConfirm) {
        // Show choice: Download or Sync (for LB card button which doesn't have dropdown)
        const choice = await _showWingItChoiceDialog(tracks.length, source);
        if (!choice) return;

        if (choice === 'sync') {
            await _wingItSync(tracks, playlistName, source, cardIdentifier);
            return;
        }
    }

    // Normalize tracks to Spotify-compatible format
    const formattedTracks = tracks.map(t => {
        // Handle various artist formats
        let artists = [];
        if (t.artists) {
            if (Array.isArray(t.artists)) {
                artists = t.artists.map(a => typeof a === 'string' ? { name: a } : a);
            } else if (typeof t.artists === 'string') {
                artists = [{ name: t.artists }];
            }
        } else if (t.artist_name) {
            artists = [{ name: t.artist_name }];
        } else if (t.artist) {
            artists = [{ name: t.artist }];
        }
        if (artists.length === 0) artists = [{ name: 'Unknown' }];

        // Handle album
        let album = { name: '' };
        if (t.album) {
            album = typeof t.album === 'string' ? { name: t.album } : t.album;
        } else if (t.album_name) {
            album = { name: t.album_name };
        }

        return {
            id: t.id || t.source_track_id || `wing_it_${Date.now()}_${Math.random()}`,
            name: t.name || t.track_name || 'Unknown Track',
            artists: artists,
            duration_ms: t.duration_ms || 0,
            album: album,
        };
    });

    const virtualPlaylistId = `wing_it_${Date.now()}`;

    // Store wing_it flag BEFORE opening the modal
    youtubePlaylistStates[virtualPlaylistId] = {
        wing_it: true,
        tracks: formattedTracks,
    };

    await openDownloadMissingModalForYouTube(virtualPlaylistId, `⚡ ${playlistName}`, formattedTracks);

    // Pre-check the Force Download toggle
    setTimeout(() => {
        const forceToggle = document.getElementById(`force-download-all-${virtualPlaylistId}`);
        if (forceToggle && !forceToggle.checked) forceToggle.checked = true;
    }, 800);
}

function _showWingItChoiceDialog(trackCount, source) {
    return new Promise(resolve => {
        const overlay = document.createElement('div');
        overlay.className = 'modal-overlay';
        overlay.style.cssText = 'position:fixed;inset:0;background:rgba(0,0,0,0.7);z-index:10000;display:flex;align-items:center;justify-content:center;';
        const close = val => { overlay.remove(); resolve(val); };
        overlay.onclick = e => { if (e.target === overlay) close(null); };

        overlay.innerHTML = `
            <div class="smart-delete-modal">
                <div class="smart-delete-header">
                    <h3>⚡ Wing It</h3>
                    <button class="smart-delete-close">&times;</button>
                </div>
                <p class="smart-delete-desc">${trackCount} track${trackCount !== 1 ? 's' : ''} from ${source}. No metadata discovery — uses raw names. Failed tracks won't be added to wishlist.</p>
                <div class="smart-delete-options">
                    <button class="smart-delete-option" data-choice="download">
                        <div class="smart-delete-option-icon">⬇️</div>
                        <div class="smart-delete-option-info">
                            <div class="smart-delete-option-title" style="color:#4caf50">Download</div>
                            <div class="smart-delete-option-desc">Search and download each track using raw names.</div>
                        </div>
                    </button>
                    <button class="smart-delete-option" data-choice="sync">
                        <div class="smart-delete-option-icon">🔄</div>
                        <div class="smart-delete-option-info">
                            <div class="smart-delete-option-title" style="color:#64b5f6">Sync to Server</div>
                            <div class="smart-delete-option-desc">Mirror playlist and sync to your media server. Best-effort matching.</div>
                        </div>
                    </button>
                </div>
            </div>
        `;

        overlay.querySelectorAll('.smart-delete-option').forEach(btn => {
            btn.addEventListener('click', () => close(btn.dataset.choice));
        });
        overlay.querySelector('.smart-delete-close').addEventListener('click', () => close(null));
        const escH = e => { if (e.key === 'Escape') { document.removeEventListener('keydown', escH); close(null); } };
        document.addEventListener('keydown', escH);
        document.body.appendChild(overlay);
    });
}

async function _wingItSync(tracks, playlistName, source, cardIdentifier = null) {
    try {
        showToast('Syncing playlist to server...', 'info');

        // Format tracks for the sync endpoint
        const syncTracks = tracks.map((t, i) => {
            let artists = t.artists || [];
            if (!Array.isArray(artists)) artists = [{ name: String(artists) }];
            return {
                id: t.id || t.source_track_id || `wing_it_${i}`,
                name: t.name || t.track_name || 'Unknown',
                artists: artists.map(a => typeof a === 'string' ? { name: a } : a),
                album: typeof t.album === 'object' ? t.album : { name: t.album || t.album_name || '' },
                duration_ms: t.duration_ms || 0,
                artist_name: t.artist_name,
            };
        });

        const res = await fetch('/api/wing-it/sync', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ tracks: syncTracks, playlist_name: playlistName })
        });
        const data = await res.json();

        if (data.error) {
            showToast(`Sync failed: ${data.error}`, 'error');
            return;
        }

        // Show inline sync status on the card (same display as normal sync)
        const playlistId = cardIdentifier ? `discover-lb-playlist-${cardIdentifier}` : null;
        if (playlistId) {
            const statusDisplay = document.getElementById(`${playlistId}-sync-status`);
            if (statusDisplay) statusDisplay.style.display = 'block';
            // Disable sync/wing-it buttons during sync
            const syncBtn = document.getElementById(`${playlistId}-sync-btn`);
            if (syncBtn) { syncBtn.disabled = true; syncBtn.style.opacity = '0.5'; }
        }

        // Poll for sync progress — update inline display
        if (data.sync_playlist_id) {
            _pollWingItSyncProgress(data.sync_playlist_id, playlistName, playlistId);
        }

    } catch (e) {
        showToast('Sync failed: ' + e.message, 'error');
    }
}

function _pollWingItSyncProgress(syncPlaylistId, playlistName, cardPlaylistId) {
    const poll = setInterval(async () => {
        try {
            const res = await fetch(`/api/sync/status/${syncPlaylistId}`);
            const data = await res.json();

            // Update inline status display if we have a card
            if (cardPlaylistId && data.progress) {
                const p = data.progress;
                const total = p.total_tracks || p.total || 0;
                const matched = p.matched_tracks || p.matched || 0;
                const failed = p.failed_tracks || p.failed || 0;
                const totalEl = document.getElementById(`${cardPlaylistId}-sync-total`);
                const matchedEl = document.getElementById(`${cardPlaylistId}-sync-matched`);
                const failedEl = document.getElementById(`${cardPlaylistId}-sync-failed`);
                const pctEl = document.getElementById(`${cardPlaylistId}-sync-percentage`);
                if (totalEl) totalEl.textContent = total;
                if (matchedEl) matchedEl.textContent = matched;
                if (failedEl) failedEl.textContent = failed;
                if (pctEl) pctEl.textContent = total > 0 ? Math.round((matched / total) * 100) : 0;
            }

            if (data.status === 'finished' || data.status === 'complete' || data.status === 'error') {
                clearInterval(poll);
                const matched = data.progress?.matched_tracks || data.progress?.matched || 0;
                const total = data.progress?.total_tracks || data.progress?.total || 0;

                if (data.status === 'error') {
                    showToast(`Sync failed: ${data.error || 'Unknown error'}`, 'error');
                } else {
                    showToast(`⚡ Wing It sync complete — "${playlistName}" created on server (${matched}/${total} tracks matched)`, 'success');
                }

                // Update card status display to show completion
                if (cardPlaylistId) {
                    const statusLabel = document.querySelector(`#${cardPlaylistId}-sync-status .sync-status-label span:last-child`);
                    if (statusLabel) statusLabel.textContent = `Sync complete — ${matched}/${total} matched`;
                    const syncIcon = document.querySelector(`#${cardPlaylistId}-sync-status .sync-icon`);
                    if (syncIcon) syncIcon.textContent = '✓';
                }
            }
        } catch (e) { /* ignore poll errors */ }
    }, 2000);

    // Safety timeout
    setTimeout(() => clearInterval(poll), 180000);
}

async function _wingItFromModal(urlHash) {
    // Extract tracks from the discovery modal state — tracks can be in various locations
    const state = listenbrainzPlaylistStates[urlHash] || youtubePlaylistStates[urlHash] || {};
    const tracks = state.tracks || state.rawTracks || state.playlist?.tracks || [];
    const name = state.playlistName || state.name || state.playlist?.name || 'Playlist';
    const isTidal = state.is_tidal_playlist;
    const isLB = state.is_listenbrainz_playlist;
    const isBeatport = state.is_beatport_playlist;
    const isDeezer = state.is_deezer_playlist;
    const source = isLB ? 'ListenBrainz' : isTidal ? 'Tidal' : isDeezer ? 'Deezer' : isBeatport ? 'Beatport' : 'YouTube';

    if (!tracks.length) {
        showToast('No tracks available for Wing It', 'error');
        return;
    }

    const choice = await _showWingItChoiceDialog(tracks.length, source);
    if (!choice) return;

    if (choice === 'sync') {
        // Sync inline — keep modal open, show progress in modal
        showToast('Starting Wing It sync...', 'info');
        updateYouTubeModalButtons(urlHash, 'syncing');

        try {
            // Format and send sync request
            const syncTracks = tracks.map((t, i) => {
                let artists = t.artists || [];
                if (!Array.isArray(artists)) artists = [{ name: String(artists) }];
                return {
                    id: t.id || t.source_track_id || `wing_it_${i}`,
                    name: t.name || t.track_name || 'Unknown',
                    artists: artists.map(a => typeof a === 'string' ? { name: a } : a),
                    album: typeof t.album === 'object' ? t.album : { name: t.album || t.album_name || '' },
                    duration_ms: t.duration_ms || 0,
                };
            });

            const res = await fetch('/api/wing-it/sync', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ tracks: syncTracks, playlist_name: name })
            });
            const data = await res.json();

            if (data.error) {
                showToast(`Sync failed: ${data.error}`, 'error');
                updateYouTubeModalButtons(urlHash, 'discovered');
                return;
            }

            // Use the same sync polling as normal sync — works for any source
            if (isLB) {
                if (state) state.syncPlaylistId = data.sync_playlist_id;
                startListenBrainzSyncPolling(urlHash, data.sync_playlist_id);
            } else {
                startYouTubeSyncPolling(urlHash, data.sync_playlist_id);
            }
        } catch (e) {
            showToast('Sync failed: ' + e.message, 'error');
            updateYouTubeModalButtons(urlHash, 'discovered');
        }
        return;
    }

    // choice === 'download' — close modal and open download modal
    const modal = document.getElementById(`youtube-discovery-modal-${urlHash}`);
    if (modal) modal.remove();
    const overlay = document.getElementById(`youtube-discovery-overlay-${urlHash}`);
    if (overlay) overlay.remove();

    wingItDownload(tracks, name, source);
}

async function openDownloadMissingModalForYouTube(virtualPlaylistId, playlistName, spotifyTracks, artist = null, album = null) {
    showLoadingOverlay('Loading YouTube playlist...');
    // Check if a process is already active for this virtual playlist
    if (activeDownloadProcesses[virtualPlaylistId]) {
        console.log(`Modal for ${virtualPlaylistId} already exists. Showing it.`);
        const process = activeDownloadProcesses[virtualPlaylistId];
        if (process.modalElement) {
            if (process.status === 'complete') {
                showToast('Showing previous results. Close this modal to start a new analysis.', 'info');
            }
            process.modalElement.style.display = 'flex';
        }
        hideLoadingOverlay(); // Hide overlay when reopening existing modal
        return;
    }

    console.log(`📥 Opening Download Missing Tracks modal for YouTube playlist: ${virtualPlaylistId}`);

    // Create virtual playlist object for compatibility with existing modal logic
    const virtualPlaylist = {
        id: virtualPlaylistId,
        name: playlistName,
        track_count: spotifyTracks.length
    };

    // Store the tracks in the cache for the modal to use
    playlistTrackCache[virtualPlaylistId] = spotifyTracks;
    currentPlaylistTracks = spotifyTracks;
    currentModalPlaylistId = virtualPlaylistId;

    let modal = document.createElement('div');
    modal.id = `download-missing-modal-${virtualPlaylistId}`;
    modal.className = 'download-missing-modal';
    modal.style.display = 'none';
    document.body.appendChild(modal);

    // Register the new process in our global state tracker using the same structure as Spotify
    activeDownloadProcesses[virtualPlaylistId] = {
        status: 'idle',
        modalElement: modal,
        poller: null,
        batchId: null,
        playlist: virtualPlaylist,
        tracks: spotifyTracks,
        artist: artist,  // ✅ Store artist context
        album: album     // ✅ Store album context
    };

    // Generate hero section with dynamic source detection
    const source = virtualPlaylistId.startsWith('beatport_') ? 'Beatport' :
        virtualPlaylistId.startsWith('tidal_') ? 'Tidal' :
            virtualPlaylistId.startsWith('listenbrainz_') ? 'ListenBrainz' :
                virtualPlaylistId.startsWith('spotify_public_') ? 'Spotify' :
                    virtualPlaylistId.startsWith('spotify:') ? 'Spotify' :
                        virtualPlaylistId.startsWith('discover_') ? 'SoulSync' :
                            virtualPlaylistId.startsWith('seasonal_') ? 'SoulSync' :
                                virtualPlaylistId.startsWith('spotify_library_') ? 'SoulSync' :
                                    virtualPlaylistId.startsWith('build_playlist_') ? 'SoulSync' :
                                        virtualPlaylistId.startsWith('decade_') ? 'SoulSync' :
                                            virtualPlaylistId === 'build_playlist_custom' ? 'SoulSync' :
                                                'YouTube';

    // Store metadata for discover download sidebar (will be added when Begin Analysis is clicked)
    if (source === 'SoulSync' || virtualPlaylistId.startsWith('discover_lb_') || virtualPlaylistId.startsWith('listenbrainz_') || virtualPlaylistId.startsWith('wing_it_')) {
        // Extract image URL from album context or first track's album cover
        let imageUrl = null;
        if (album && album.images && album.images.length > 0) {
            imageUrl = album.images[0].url;
        } else if (spotifyTracks && spotifyTracks.length > 0) {
            const firstTrack = spotifyTracks[0];
            if (firstTrack.album && firstTrack.album.images && firstTrack.album.images.length > 0) {
                imageUrl = firstTrack.album.images[0].url;
            }
        }
        // Store in process for later use when Begin Analysis is clicked
        activeDownloadProcesses[virtualPlaylistId].discoverMetadata = {
            imageUrl: imageUrl,
            type: album ? 'album' : 'playlist'  // ✅ Use 'album' if album context provided
        };
    }

    // CRITICAL FIX: Use album context for discover_album playlists
    const isDiscoverAlbum = virtualPlaylistId.startsWith('discover_album_') || virtualPlaylistId.startsWith('discover_cache_') || virtualPlaylistId.startsWith('seasonal_album_') || virtualPlaylistId.startsWith('spotify_library_');
    const heroContext = isDiscoverAlbum && album && artist ? {
        type: 'album',
        artist: {
            name: artist.name,
            image_url: artist.image_url || null
        },
        album: {
            name: album.name,
            album_type: album.album_type || 'album',
            images: album.images || []
        },
        trackCount: spotifyTracks.length,
        playlistId: virtualPlaylistId
    } : {
        type: 'playlist',
        playlist: { name: playlistName, owner: source },
        trackCount: spotifyTracks.length,
        playlistId: virtualPlaylistId
    };

    // Use the exact same modal HTML structure as the existing Spotify modal
    modal.innerHTML = `
        <div class="download-missing-modal-content" data-context="playlist">
            <div class="download-missing-modal-header">
                ${generateDownloadModalHeroSection(heroContext)}
            </div>

            <div class="download-missing-modal-body">
                <div class="download-progress-section">
                    <div class="progress-item">
                        <div class="progress-label">
                            🔍 Library Analysis
                            <span id="analysis-progress-text-${virtualPlaylistId}">Ready to start</span>
                        </div>
                        <div class="progress-bar">
                            <div class="progress-fill analysis" id="analysis-progress-fill-${virtualPlaylistId}"></div>
                        </div>
                    </div>
                    <div class="progress-item">
                        <div class="progress-label">
                            ⏬ Downloads
                            <span id="download-progress-text-${virtualPlaylistId}">Waiting for analysis</span>
                        </div>
                        <div class="progress-bar">
                            <div class="progress-fill download" id="download-progress-fill-${virtualPlaylistId}"></div>
                        </div>
                    </div>
                </div>
                
                <div class="download-tracks-section">
                    <div class="download-tracks-header">
                        <h3 class="download-tracks-title">📋 Track Analysis & Download Status</h3>
                        <span class="track-selection-count" id="track-selection-count-${virtualPlaylistId}">${spotifyTracks.length} / ${spotifyTracks.length} tracks selected</span>
                    </div>
                    <div class="download-tracks-table-container">
                        <table class="download-tracks-table">
                            <thead>
                                <tr>
                                    <th class="track-select-header">
                                        <input type="checkbox" class="track-select-all"
                                               id="select-all-${virtualPlaylistId}" checked
                                               onchange="toggleAllTrackSelections('${virtualPlaylistId}', this.checked)">
                                    </th>
                                    <th>#</th>
                                    <th>Track</th>
                                    <th>Artist</th>
                                    <th>Duration</th>
                                    <th>Library Match</th>
                                    <th>Download Status</th>
                                    <th>Actions</th>
                                </tr>
                            </thead>
                            <tbody id="download-tracks-tbody-${virtualPlaylistId}">
                                ${spotifyTracks.map((track, index) => `
                                    <tr data-track-index="${index}">
                                        <td class="track-select-cell">
                                            <input type="checkbox" class="track-select-cb"
                                                   data-track-index="${index}" checked
                                                   onchange="updateTrackSelectionCount('${virtualPlaylistId}')">
                                        </td>
                                        <td class="track-number">${index + 1}</td>
                                        <td class="track-name" title="${escapeHtml(track.name)}">${escapeHtml(track.name)}</td>
                                        <td class="track-artist" title="${escapeHtml(formatArtists(track.artists))}">${escapeHtml(formatArtists(track.artists))}</td>
                                        <td class="track-duration">${formatDuration(track.duration_ms)}</td>
                                        <td class="track-match-status match-checking" id="match-${virtualPlaylistId}-${index}">🔍 Pending</td>
                                        <td class="track-download-status" id="download-${virtualPlaylistId}-${index}">-</td>
                                        <td class="track-actions" id="actions-${virtualPlaylistId}-${index}">-</td>
                                    </tr>
                                `).join('')}
                            </tbody>
                        </table>
                    </div>
                </div>
            </div>
            
            <div class="download-missing-modal-footer">
                <div class="download-phase-controls">
                    <div class="force-download-toggle-container" style="margin-bottom: 0px; display: flex; flex-direction: column; gap: 8px; align-items: flex-start;">
                        <label class="force-download-toggle">
                            <input type="checkbox" id="force-download-all-${virtualPlaylistId}">
                            <span>Force Download All</span>
                        </label>
                        ${isDiscoverAlbum ? '' : `<label class="force-download-toggle">
                            <input type="checkbox" id="playlist-folder-mode-${virtualPlaylistId}">
                            <span>Organize by Playlist (Downloads/Playlist/Artist - Track.ext)</span>
                        </label>`}
                    </div>
                    <button class="download-control-btn primary" id="begin-analysis-btn-${virtualPlaylistId}" onclick="startMissingTracksProcess('${virtualPlaylistId}')">
                        Begin Analysis
                    </button>
                    <button class="download-control-btn" id="add-to-wishlist-btn-${virtualPlaylistId}" onclick="addModalTracksToWishlist('${virtualPlaylistId}')" style="background-color: #9333ea; color: white;">
                        Add to Wishlist
                    </button>
                    <button class="download-control-btn danger" id="cancel-all-btn-${virtualPlaylistId}" onclick="cancelAllOperations('${virtualPlaylistId}')" style="display: none;">
                        Cancel All
                    </button>
                </div>
                <div class="modal-close-section">
                    <button class="download-control-btn export" onclick="exportPlaylistAsM3U('${virtualPlaylistId}')">
                        📋 Export as M3U
                    </button>
                    <button class="download-control-btn secondary" onclick="closeDownloadMissingModal('${virtualPlaylistId}')">Close</button>
                </div>
            </div>
        </div>
    `;

    applyProgressiveTrackRendering(virtualPlaylistId, spotifyTracks.length);
    modal.style.display = 'flex';
    hideLoadingOverlay();
}

function _navigateToArtistFromModal(artistId, artistName, imageUrl, source, playlistId) {
    if (!artistName) return;
    // Close the download modal
    if (playlistId) closeDownloadMissingModal(playlistId);
    navigateToArtistDetail(artistId || artistName, artistName, source || null);
}

async function closeDownloadMissingModal(playlistId) {
    const process = activeDownloadProcesses[playlistId];
    if (!process) {
        // If somehow called without a process, try to find and remove the element
        const modal = document.getElementById(`download-missing-modal-${playlistId}`);
        if (modal && modal.parentElement) {
            modal.parentElement.removeChild(modal);
        }
        return;
    }

    // If the process is running, just hide the modal.
    // If it's idle, complete, or cancelled, perform a full cleanup.
    if (process.status === 'running') {
        console.log(`Hiding active download modal for playlist ${playlistId}.`);
        process.modalElement.style.display = 'none';

        // Track wishlist modal state changes
        if (playlistId === 'wishlist') {
            WishlistModalState.setUserClosed(); // User manually closed during processing
            console.log('📱 [Modal State] User manually closed wishlist modal during processing');
        }
    } else {
        console.log(`Closing and cleaning up download modal for playlist ${playlistId}.`);

        // Reset YouTube playlist phase to 'discovered' when modal is closed after completion
        if (playlistId.startsWith('youtube_')) {
            const urlHash = playlistId.replace('youtube_', '');
            updateYouTubeCardPhase(urlHash, 'discovered');
            // Also update mirrored playlist card if applicable
            if (urlHash.startsWith('mirrored_')) {
                updateMirroredCardPhase(urlHash, 'discovered');
            }

            // Update backend state to prevent rehydration issues on page refresh (similar to Tidal fix)
            try {
                const response = await fetch(`/api/youtube/update_phase/${urlHash}`, {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json'
                    },
                    body: JSON.stringify({
                        phase: 'discovered'
                    })
                });

                if (response.ok) {
                    console.log(`✅ [Modal Close] Updated backend phase for YouTube playlist ${urlHash} to 'discovered'`);
                } else {
                    console.warn(`⚠️ [Modal Close] Failed to update backend phase for YouTube playlist ${urlHash}`);
                }
            } catch (error) {
                console.error(`❌ [Modal Close] Error updating backend phase for YouTube playlist ${urlHash}:`, error);
            }
        }

        // Reset Beatport chart phase to 'discovered' when modal is closed
        if (playlistId.startsWith('beatport_')) {
            const urlHash = playlistId.replace('beatport_', '');
            const state = youtubePlaylistStates[urlHash];

            if (state && state.is_beatport_playlist) {
                console.log(`🧹 [Modal Close] Processing Beatport chart close: playlistId="${playlistId}", urlHash="${urlHash}"`);

                const chartHash = state.beatport_chart_hash || urlHash;

                // Reset to discovered phase (unless download actually started and completed)
                if (state.phase !== 'download_complete') {
                    updateBeatportCardPhase(chartHash, 'discovered');
                    state.phase = 'discovered';

                    // Update Beatport chart state
                    if (beatportChartStates[chartHash]) {
                        beatportChartStates[chartHash].phase = 'discovered';
                    }

                    // Update backend state
                    try {
                        await fetch(`/api/beatport/charts/update-phase/${chartHash}`, {
                            method: 'POST',
                            headers: { 'Content-Type': 'application/json' },
                            body: JSON.stringify({ phase: 'discovered' })
                        });
                        console.log(`✅ [Modal Close] Updated backend phase for Beatport chart ${chartHash} to 'discovered'`);
                    } catch (error) {
                        console.error(`❌ [Modal Close] Error updating backend phase for Beatport chart ${chartHash}:`, error);
                    }
                }
            }
        }

        // Enhanced Tidal playlist state management (based on GUI sync.py patterns)
        if (playlistId.startsWith('tidal_')) {
            const tidalPlaylistId = playlistId.replace('tidal_', '');

            console.log(`🧹 [Modal Close] Processing Tidal playlist close: playlistId="${playlistId}", tidalPlaylistId="${tidalPlaylistId}"`);
            console.log(`🧹 [Modal Close] Current Tidal state:`, tidalPlaylistStates[tidalPlaylistId]);

            // Clear download-specific state but preserve discovery results (like GUI closeEvent)
            if (tidalPlaylistStates[tidalPlaylistId]) {
                const currentPhase = tidalPlaylistStates[tidalPlaylistId].phase;
                console.log(`🧹 [Modal Close] Current phase before reset: ${currentPhase}`);

                // Preserve discovery data for future use (like GUI modal behavior)
                const preservedData = {
                    playlist: tidalPlaylistStates[tidalPlaylistId].playlist,
                    discovery_results: tidalPlaylistStates[tidalPlaylistId].discovery_results,
                    spotify_matches: tidalPlaylistStates[tidalPlaylistId].spotify_matches,
                    discovery_progress: tidalPlaylistStates[tidalPlaylistId].discovery_progress,
                    convertedSpotifyPlaylistId: tidalPlaylistStates[tidalPlaylistId].convertedSpotifyPlaylistId
                };

                // Clear download-specific state 
                delete tidalPlaylistStates[tidalPlaylistId].download_process_id;
                delete tidalPlaylistStates[tidalPlaylistId].phase;

                // Restore preserved data and set to discovered phase
                Object.assign(tidalPlaylistStates[tidalPlaylistId], preservedData);
                tidalPlaylistStates[tidalPlaylistId].phase = 'discovered';

                console.log(`🧹 [Modal Close] Reset Tidal playlist ${tidalPlaylistId} - cleared download state, preserved discovery data`);
                console.log(`🧹 [Modal Close] New phase after reset: ${tidalPlaylistStates[tidalPlaylistId].phase}`);
            } else {
                console.error(`❌ [Modal Close] No Tidal state found for playlistId: ${tidalPlaylistId}`);
            }

            updateTidalCardPhase(tidalPlaylistId, 'discovered');
            console.log(`🔄 [Modal Close] Reset Tidal playlist ${tidalPlaylistId} to discovered phase`);
            console.log(`📝 [Modal Close] Expected button text for discovered phase: "${getActionButtonText('discovered')}"`);

            // Update backend state to prevent rehydration issues on page refresh
            try {
                const response = await fetch(`/api/tidal/update_phase/${tidalPlaylistId}`, {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json'
                    },
                    body: JSON.stringify({
                        phase: 'discovered'
                    })
                });

                if (response.ok) {
                    console.log(`✅ [Modal Close] Updated backend phase for Tidal playlist ${tidalPlaylistId} to 'discovered'`);
                } else {
                    console.warn(`⚠️ [Modal Close] Failed to update backend phase for Tidal playlist ${tidalPlaylistId}`);
                }
            } catch (error) {
                console.error(`❌ [Modal Close] Error updating backend phase for Tidal playlist ${tidalPlaylistId}:`, error);
            }
        }

        // Reset ListenBrainz playlist phase to 'discovered' when modal is closed
        if (playlistId.startsWith('listenbrainz_')) {
            const playlistMbid = playlistId.replace('listenbrainz_', '');

            console.log(`🧹 [Modal Close] Processing ListenBrainz playlist close: playlistId="${playlistId}", mbid="${playlistMbid}"`);

            // Clear download-specific state but preserve discovery results
            if (listenbrainzPlaylistStates[playlistMbid]) {
                const currentPhase = listenbrainzPlaylistStates[playlistMbid].phase;
                console.log(`🧹 [Modal Close] Current phase before reset: ${currentPhase}`);

                // Reset to discovered phase (unless download actually completed successfully)
                if (currentPhase !== 'download_complete') {
                    // Clear download-specific fields
                    delete listenbrainzPlaylistStates[playlistMbid].download_process_id;
                    delete listenbrainzPlaylistStates[playlistMbid].convertedSpotifyPlaylistId;

                    // Set back to discovered
                    listenbrainzPlaylistStates[playlistMbid].phase = 'discovered';

                    // Update backend state
                    try {
                        await fetch(`/api/listenbrainz/update-phase/${playlistMbid}`, {
                            method: 'POST',
                            headers: { 'Content-Type': 'application/json' },
                            body: JSON.stringify({ phase: 'discovered' })
                        });
                        console.log(`✅ [Modal Close] Updated backend phase for ListenBrainz playlist ${playlistMbid} to 'discovered'`);
                    } catch (error) {
                        console.error(`❌ [Modal Close] Error updating backend phase for ListenBrainz playlist ${playlistMbid}:`, error);
                    }

                    console.log(`🔄 [Modal Close] Reset ListenBrainz playlist ${playlistMbid} to discovered phase`);
                }
            } else {
                console.error(`❌ [Modal Close] No ListenBrainz state found for mbid: ${playlistMbid}`);
            }
        }

        // Reset Spotify Public playlist phase to 'discovered' when modal is closed
        if (playlistId.startsWith('spotify_public_')) {
            const spUrlHash = playlistId.replace('spotify_public_', '');

            console.log(`🧹 [Modal Close] Processing Spotify Public playlist close: playlistId="${playlistId}", urlHash="${spUrlHash}"`);

            if (spotifyPublicPlaylistStates[spUrlHash]) {
                const currentPhase = spotifyPublicPlaylistStates[spUrlHash].phase;
                console.log(`🧹 [Modal Close] Current phase before reset: ${currentPhase}`);

                const preservedData = {
                    playlist: spotifyPublicPlaylistStates[spUrlHash].playlist,
                    discovery_results: spotifyPublicPlaylistStates[spUrlHash].discovery_results,
                    spotify_matches: spotifyPublicPlaylistStates[spUrlHash].spotify_matches,
                    discovery_progress: spotifyPublicPlaylistStates[spUrlHash].discovery_progress,
                    convertedSpotifyPlaylistId: spotifyPublicPlaylistStates[spUrlHash].convertedSpotifyPlaylistId
                };

                delete spotifyPublicPlaylistStates[spUrlHash].download_process_id;
                delete spotifyPublicPlaylistStates[spUrlHash].phase;

                Object.assign(spotifyPublicPlaylistStates[spUrlHash], preservedData);
                spotifyPublicPlaylistStates[spUrlHash].phase = 'discovered';

                console.log(`🧹 [Modal Close] Reset Spotify Public playlist ${spUrlHash} - cleared download state, preserved discovery data`);
            }

            updateSpotifyPublicCardPhase(spUrlHash, 'discovered');

            try {
                await fetch(`/api/spotify-public/update_phase/${spUrlHash}`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ phase: 'discovered' })
                });
                console.log(`✅ [Modal Close] Updated backend phase for Spotify Public playlist ${spUrlHash} to 'discovered'`);
            } catch (error) {
                console.error(`❌ [Modal Close] Error updating backend phase for Spotify Public playlist ${spUrlHash}:`, error);
            }
        }

        // Reset Deezer playlist phase to 'discovered' when modal is closed
        if (playlistId.startsWith('deezer_')) {
            const deezerPlaylistId = playlistId.replace('deezer_', '');

            console.log(`🧹 [Modal Close] Processing Deezer playlist close: playlistId="${playlistId}", deezerPlaylistId="${deezerPlaylistId}"`);

            if (deezerPlaylistStates[deezerPlaylistId]) {
                const currentPhase = deezerPlaylistStates[deezerPlaylistId].phase;
                console.log(`🧹 [Modal Close] Current phase before reset: ${currentPhase}`);

                const preservedData = {
                    playlist: deezerPlaylistStates[deezerPlaylistId].playlist,
                    discovery_results: deezerPlaylistStates[deezerPlaylistId].discovery_results,
                    spotify_matches: deezerPlaylistStates[deezerPlaylistId].spotify_matches,
                    discovery_progress: deezerPlaylistStates[deezerPlaylistId].discovery_progress,
                    convertedSpotifyPlaylistId: deezerPlaylistStates[deezerPlaylistId].convertedSpotifyPlaylistId
                };

                delete deezerPlaylistStates[deezerPlaylistId].download_process_id;
                delete deezerPlaylistStates[deezerPlaylistId].phase;

                Object.assign(deezerPlaylistStates[deezerPlaylistId], preservedData);
                deezerPlaylistStates[deezerPlaylistId].phase = 'discovered';

                console.log(`🧹 [Modal Close] Reset Deezer playlist ${deezerPlaylistId} - cleared download state, preserved discovery data`);
            }

            updateDeezerCardPhase(deezerPlaylistId, 'discovered');

            try {
                await fetch(`/api/deezer/update_phase/${deezerPlaylistId}`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ phase: 'discovered' })
                });
                console.log(`✅ [Modal Close] Updated backend phase for Deezer playlist ${deezerPlaylistId} to 'discovered'`);
            } catch (error) {
                console.error(`❌ [Modal Close] Error updating backend phase for Deezer playlist ${deezerPlaylistId}:`, error);
            }
        }

        // Clear wishlist modal state when modal is fully closed
        if (playlistId === 'wishlist') {
            WishlistModalState.clear(); // Clear all tracking since modal is fully closed
            console.log('📱 [Modal State] Cleared wishlist modal state on full close');
        }

        // Clean up artist download if this is an artist album playlist
        if (playlistId.startsWith('artist_album_')) {
            console.log(`🧹 [MODAL CLOSE] Cleaning up artist download for completed modal: ${playlistId}`);
            cleanupArtistDownload(playlistId);
            console.log(`✅ [MODAL CLOSE] Artist download cleanup completed for: ${playlistId}`);
        }

        // Clean up search download if this is an enhanced search playlist
        if (playlistId.startsWith('enhanced_search_')) {
            console.log(`🧹 [MODAL CLOSE] Cleaning up search download for completed modal: ${playlistId}`);
            cleanupSearchDownload(playlistId);
            console.log(`✅ [MODAL CLOSE] Search download cleanup completed for: ${playlistId}`);
        }

        // Clean up Beatport download if this is a beatport chart or release playlist
        if (playlistId.startsWith('beatport_chart_') || playlistId.startsWith('beatport_release_')) {
            console.log(`🧹 [MODAL CLOSE] Cleaning up Beatport download for completed modal: ${playlistId}`);
            cleanupBeatportDownload(playlistId);
            console.log(`✅ [MODAL CLOSE] Beatport download cleanup completed for: ${playlistId}`);
        }

        // Remove from discover download sidebar if this is a discover page download
        if (discoverDownloads && discoverDownloads[playlistId]) {
            console.log(`🧹 [MODAL CLOSE] Removing discover download bubble: ${playlistId}`);
            removeDiscoverDownload(playlistId);
            console.log(`✅ [MODAL CLOSE] Discover download bubble removed for: ${playlistId}`);
        }

        // Automatic cleanup and server operations after successful downloads
        await handlePostDownloadAutomation(playlistId, process);

        cleanupDownloadProcess(playlistId);
    }
}

/**
 * Extract unique album cover images from tracks
 */
function extractUniqueCoverImages(tracks, maxCovers = 20) {
    const uniqueCovers = new Set();
    const covers = [];

    for (const track of tracks) {
        if (covers.length >= maxCovers) break;

        let coverUrl = null;
        let spotifyData = track.spotify_data;

        // Parse spotify_data if it's a string
        if (typeof spotifyData === 'string') {
            try {
                spotifyData = JSON.parse(spotifyData);
            } catch (e) {
                continue;
            }
        }

        // Extract cover URL
        coverUrl = spotifyData?.album?.images?.[0]?.url;

        // Add to list if unique and valid
        if (coverUrl && !uniqueCovers.has(coverUrl)) {
            uniqueCovers.add(coverUrl);
            covers.push(coverUrl);
        }
    }

    return covers;
}

/**
 * Shuffle array using Fisher-Yates algorithm
 */
function shuffleArray(array) {
    const shuffled = [...array];
    for (let i = shuffled.length - 1; i > 0; i--) {
        const j = Math.floor(Math.random() * (i + 1));
        [shuffled[i], shuffled[j]] = [shuffled[j], shuffled[i]];
    }
    return shuffled;
}

/**
 * Generate mosaic grid background HTML with continuous scrolling rows
 */
function generateMosaicBackground(coverUrls) {
    // If less than 3 covers, use gradient fallback
    if (!coverUrls || coverUrls.length < 3) {
        return `
            <div class="wishlist-mosaic-fallback"></div>
            <div class="wishlist-mosaic-overlay"></div>
        `;
    }

    // Cap covers per row to 15 for GPU performance (avoids hundreds of tiles)
    if (coverUrls.length > 15) {
        coverUrls = coverUrls.slice(0, 15);
    }

    const rows = 4;
    let mosaicHTML = '<div class="wishlist-mosaic-background">';

    // Calculate scroll speed based on number of images
    // More images = longer duration to maintain consistent visual speed
    // Minimum 40s to prevent scrolling too fast
    const scrollSpeed = Math.max(40, coverUrls.length * 2);

    for (let row = 0; row < rows; row++) {
        const isEvenRow = row % 2 === 0;
        const direction = isEvenRow ? 'left' : 'right';

        // Randomize order for each row
        const shuffledCovers = shuffleArray(coverUrls);

        // Create row wrapper
        mosaicHTML += `<div class="wishlist-mosaic-row-wrapper">`;
        mosaicHTML += `<div class="wishlist-mosaic-row scroll-${direction}" style="--speed: ${scrollSpeed}s;">`;

        // Generate tiles - duplicate 2 times for smooth infinite scroll
        for (let duplicate = 0; duplicate < 2; duplicate++) {
            for (let i = 0; i < shuffledCovers.length; i++) {
                const coverUrl = shuffledCovers[i];
                mosaicHTML += `
                    <div class="wishlist-mosaic-tile">
                        <div class="wishlist-mosaic-image" style="background-image: url('${coverUrl}');"></div>
                    </div>
                `;
            }
        }

        mosaicHTML += '</div>'; // Close row
        mosaicHTML += '</div>'; // Close wrapper
    }

    mosaicHTML += '</div>';
    mosaicHTML += '<div class="wishlist-mosaic-overlay"></div>'; // Dark overlay for readability

    return mosaicHTML;
}

/**
 * Open wishlist overview modal showing category breakdown
 * This is the NEW entry point for wishlist from dashboard
 */
async function openWishlistOverviewModal() {
    try {
        showLoadingOverlay('Loading wishlist...');

        // Fetch wishlist stats
        const statsResponse = await fetch('/api/wishlist/stats');
        const statsData = await statsResponse.json();

        if (!statsResponse.ok) {
            throw new Error(statsData.error || 'Failed to fetch wishlist stats');
        }

        const { singles, albums, total } = statsData;

        if (total === 0) {
            hideLoadingOverlay();
            showToast('Wishlist is empty. No tracks to process.', 'info');
            return;
        }

        // Fetch album covers for mosaic backgrounds
        // Limit to 50 tracks per category (enough to get 20 unique covers while being efficient)
        const albumCoversPromise = fetch('/api/wishlist/tracks?category=albums&limit=50').then(r => r.json());
        const singleCoversPromise = fetch('/api/wishlist/tracks?category=singles&limit=50').then(r => r.json());

        const [albumTracksData, singleTracksData] = await Promise.all([albumCoversPromise, singleCoversPromise]);

        // Extract unique album covers (max 20 per category)
        const albumCovers = extractUniqueCoverImages(albumTracksData.tracks || [], 20);
        const singleCovers = extractUniqueCoverImages(singleTracksData.tracks || [], 20);

        // Create modal if it doesn't exist
        let modal = document.getElementById('wishlist-overview-modal');
        if (!modal) {
            modal = document.createElement('div');
            modal.id = 'wishlist-overview-modal';
            modal.className = 'modal-overlay';
            document.body.appendChild(modal);
        }

        // Fetch current cycle
        const cycleResponse = await fetch('/api/wishlist/cycle');
        const cycleData = await cycleResponse.json();
        const currentCycle = cycleData.cycle || 'albums';

        // Format countdown timer
        const nextRunSeconds = statsData.next_run_in_seconds || 0;
        const countdownText = formatCountdownTime(nextRunSeconds);
        const nextCycleText = currentCycle === 'albums' ? 'Albums/EPs' : 'Singles';

        modal.innerHTML = `
            <div class="modal-container playlist-modal">
                <div class="playlist-modal-header">
                    <div class="playlist-header-content">
                        <h2>🎵 Wishlist Overview</h2>
                        <div class="playlist-quick-info">
                            <span class="playlist-track-count">${total} Total Tracks</span>
                            <span class="playlist-owner" id="wishlist-next-auto-timer">Next Auto: ${nextCycleText}${countdownText ? ' in ' + countdownText : ''}</span>
                        </div>
                    </div>
                    <span class="playlist-modal-close" onclick="closeWishlistOverviewModal()">×</span>
                </div>

                <div class="playlist-modal-body">
                    <div class="wishlist-category-grid">
                        <!-- Albums/EPs Category -->
                        <div class="wishlist-category-card ${currentCycle === 'albums' ? 'next-in-queue' : ''}" data-category="albums" onclick="selectWishlistCategory('albums')">
                            ${generateMosaicBackground(albumCovers)}
                            <div class="wishlist-category-content">
                                <div class="wishlist-category-icon">💿</div>
                                <div class="wishlist-category-title">Albums / EPs</div>
                                <div class="wishlist-category-count">${albums} tracks</div>
                                ${currentCycle === 'albums' ? '<div class="wishlist-category-badge">Next in Queue</div>' : ''}
                            </div>
                        </div>

                        <!-- Singles Category -->
                        <div class="wishlist-category-card ${currentCycle === 'singles' ? 'next-in-queue' : ''}" data-category="singles" onclick="selectWishlistCategory('singles')">
                            ${generateMosaicBackground(singleCovers)}
                            <div class="wishlist-category-content">
                                <div class="wishlist-category-icon">🎵</div>
                                <div class="wishlist-category-title">Singles</div>
                                <div class="wishlist-category-count">${singles} tracks</div>
                                ${currentCycle === 'singles' ? '<div class="wishlist-category-badge">Next in Queue</div>' : ''}
                            </div>
                        </div>
                    </div>

                    <!-- Selected Category Track List (initially hidden) -->
                    <div id="wishlist-category-tracks" class="wishlist-category-tracks" style="display: none;">
                        <div class="wishlist-category-header">
                            <button class="wishlist-back-btn" onclick="backToCategories()">← Back</button>
                            <span id="wishlist-category-name" class="wishlist-category-name"></span>
                            <button class="wishlist-select-all-btn" id="wishlist-select-all-btn" onclick="toggleWishlistSelectAll()">Select All</button>
                        </div>
                        <div class="wishlist-batch-bar" id="wishlist-batch-bar" style="display: none;">
                            <span class="wishlist-batch-count" id="wishlist-batch-count">0 selected</span>
                            <button class="playlist-modal-btn playlist-modal-btn-secondary wishlist-batch-remove-btn"
                                    onclick="batchRemoveFromWishlist()">
                                Remove Selected
                            </button>
                        </div>
                        <div id="wishlist-tracks-list" class="playlist-tracks-scroll">
                            <div class="loading-indicator">Loading tracks...</div>
                        </div>
                    </div>
                </div>

                <div class="playlist-modal-footer">
                    <div class="playlist-modal-footer-left">
                        <button class="playlist-modal-btn playlist-modal-btn-danger" onclick="clearEntireWishlist()">
                            🗑️ Clear Wishlist
                        </button>
                        <button class="playlist-modal-btn playlist-modal-btn-warning" onclick="cleanupWishlistOverview()">
                            🧹 Cleanup Wishlist
                        </button>
                    </div>
                    <div class="playlist-modal-footer-right">
                        <button class="playlist-modal-btn playlist-modal-btn-secondary" onclick="closeWishlistOverviewModal()">Close</button>
                        <button id="wishlist-download-btn" class="playlist-modal-btn playlist-modal-btn-primary" style="display: none;" onclick="downloadSelectedCategory()">
                            Download Selection
                        </button>
                    </div>
                </div>
            </div>
        `;

        modal.style.display = 'flex';
        hideLoadingOverlay();

        // Start countdown timer update interval
        startWishlistCountdownTimer(currentCycle, nextRunSeconds);

    } catch (error) {
        console.error('Error opening wishlist overview:', error);
        showToast(`Failed to load wishlist: ${error.message}`, 'error');
        hideLoadingOverlay();
    }
}

function startWishlistCountdownTimer(currentCycle, initialSeconds) {
    // Clear any existing interval
    if (wishlistCountdownInterval) {
        clearInterval(wishlistCountdownInterval);
    }

    let remainingSeconds = initialSeconds;
    const nextCycleText = currentCycle === 'albums' ? 'Albums/EPs' : 'Singles';

    wishlistCountdownInterval = setInterval(async () => {
        remainingSeconds--;

        // Check if auto-processing has started (every 2 seconds to avoid overwhelming backend)
        if (remainingSeconds % 2 === 0 || remainingSeconds <= 0) {
            // Use WebSocket data if available, otherwise fall back to HTTP
            if (socketConnected && _lastWishlistStats) {
                const data = _lastWishlistStats;
                if (data.is_auto_processing) {
                    if (!_wishlistAutoProcessingNotified) {
                        navigateToPage('active-downloads');
                        showToast('Wishlist auto-processing started. View progress in Download Manager.', 'info');
                        _wishlistAutoProcessingNotified = true;
                    }
                    return;
                }
                if (remainingSeconds <= 0) {
                    remainingSeconds = data.next_run_in_seconds || 0;
                    const timerElement = document.getElementById('wishlist-next-auto-timer');
                    if (timerElement) {
                        const countdownText = formatCountdownTime(remainingSeconds);
                        timerElement.textContent = `Next Auto: ${nextCycleText}${countdownText ? ' in ' + countdownText : ''}`;
                    }
                }
            } else {
                try {
                    const response = await fetch('/api/wishlist/stats');
                    const data = await response.json();

                    // AUTO-CLOSE DETECTION: If auto-processing started, close modal and notify user (once)
                    if (data.is_auto_processing) {
                        if (!_wishlistAutoProcessingNotified) {
                            console.log('🤖 [Wishlist] Auto-processing detected, closing overview modal');
                            closeWishlistOverviewModal();
                            showToast('Wishlist auto-processing started. View progress in Download Manager.', 'info');
                            _wishlistAutoProcessingNotified = true;
                        }
                        return; // Exit interval
                    }

                    // Update remaining seconds if timer expired
                    if (remainingSeconds <= 0) {
                        remainingSeconds = data.next_run_in_seconds || 0;

                        // Also update cycle in case it changed
                        const newCycle = data.current_cycle || 'albums';
                        const newCycleText = newCycle === 'albums' ? 'Albums/EPs' : 'Singles';

                        const timerElement = document.getElementById('wishlist-next-auto-timer');
                        if (timerElement) {
                            const countdownText = formatCountdownTime(remainingSeconds);
                            timerElement.textContent = `Next Auto: ${newCycleText}${countdownText ? ' in ' + countdownText : ''}`;
                        }
                    }
                } catch (error) {
                    console.debug('Error updating wishlist countdown:', error);
                }
            } // end else (HTTP fallback)
        }

        // Always update the display countdown
        const timerElement = document.getElementById('wishlist-next-auto-timer');
        if (timerElement) {
            const countdownText = formatCountdownTime(remainingSeconds);
            timerElement.textContent = `Next Auto: ${nextCycleText}${countdownText ? ' in ' + countdownText : ''}`;
        }
    }, 1000); // Update every second
}

function closeWishlistOverviewModal() {
    console.log('🚪 closeWishlistOverviewModal() called');

    // Stop countdown timer
    if (wishlistCountdownInterval) {
        clearInterval(wishlistCountdownInterval);
        wishlistCountdownInterval = null;
    }

    const modal = document.getElementById('wishlist-overview-modal');
    console.log('Modal element:', modal);
    if (modal) {
        modal.style.display = 'none';
        console.log('Modal display set to none');
        // Also remove from DOM to ensure clean state
        modal.remove();
        console.log('Modal removed from DOM');
    } else {
        console.warn('Modal element not found');
    }
    window.selectedWishlistCategory = null;
    console.log('✅ Modal closed');
}

async function cleanupWishlistOverview() {
    console.log('🧹 cleanupWishlistOverview() called');

    if (!await showConfirmDialog({ title: 'Cleanup Wishlist', message: 'This will remove all tracks from the wishlist that already exist in your library. Continue?' })) {
        return;
    }

    try {
        showLoadingOverlay('Cleaning up wishlist...');

        const response = await fetch('/api/wishlist/cleanup', {
            method: 'POST'
        });

        const result = await response.json();

        if (result.success) {
            const removedCount = result.removed_count || 0;

            if (removedCount > 0) {
                showToast(`Cleanup complete! Removed ${removedCount} tracks that already exist in your library`, 'success');
            } else {
                showToast('No tracks needed to be removed', 'info');
            }

            // Check if wishlist is now empty
            const statsResponse = await fetch('/api/wishlist/stats');
            const statsData = await statsResponse.json();

            if (statsData.total === 0) {
                // Wishlist is empty, refresh the page to show empty state
                wishlistPageState.isInitialized = false;
                await initializeWishlistPage();
                await updateWishlistCount();
            } else {
                // Wishlist still has items, refresh the page to show updated counts
                wishlistPageState.isInitialized = false;
                await initializeWishlistPage();
            }
        } else {
            showToast(`Failed to cleanup wishlist: ${result.error || 'Unknown error'}`, 'error');
        }

        hideLoadingOverlay();

    } catch (error) {
        console.error('Error cleaning up wishlist:', error);
        showToast(`Failed to cleanup wishlist: ${error.message}`, 'error');
        hideLoadingOverlay();
    }
}

async function clearEntireWishlist() {
    console.log('🗑️ clearEntireWishlist() called');

    if (!await showConfirmDialog({ title: 'Clear Wishlist', message: 'WARNING: This will permanently delete ALL tracks from your wishlist.\n\nThis action cannot be undone.\n\nAre you sure you want to continue?', confirmText: 'Clear All', destructive: true })) {
        console.log('User cancelled confirmation');
        return;
    }

    console.log('User confirmed, proceeding with clear...');

    try {
        showLoadingOverlay('Clearing wishlist...');
        console.log('Loading overlay shown');

        const response = await fetch('/api/wishlist/clear', {
            method: 'POST'
        });
        console.log('API response received:', response.status);

        const result = await response.json();
        console.log('Clear wishlist response:', result);

        hideLoadingOverlay();
        console.log('Loading overlay hidden');

        if (result.success) {
            console.log('Clear was successful, showing toast...');
            showToast('Wishlist cleared successfully', 'success');

            console.log('Updating wishlist button count...');
            await updateWishlistCount();

            console.log('Refreshing wishlist page...');
            wishlistPageState.isInitialized = false;
            await initializeWishlistPage();
        } else {
            console.error('Clear failed:', result.error);
            showToast(`Failed to clear wishlist: ${result.error || 'Unknown error'}`, 'error');
        }

    } catch (error) {
        console.error('Error clearing wishlist:', error);
        hideLoadingOverlay();
        showToast(`Failed to clear wishlist: ${error.message}`, 'error');
    }
}

async function selectWishlistCategory(category) {
    try {
        window.selectedWishlistCategory = category;

        const tracksList = document.getElementById('wishlist-tracks-list');
        const categoryTracksSection = document.getElementById('wishlist-category-tracks');
        const nebulaEl = document.getElementById('wishlist-nebula');
        const downloadBtn = document.getElementById('wishlist-download-btn');
        const categoryName = document.getElementById('wishlist-category-name');

        if (nebulaEl) nebulaEl.style.display = 'none';
        categoryTracksSection.style.display = 'block';
        downloadBtn.style.display = 'inline-block';
        categoryName.textContent = category === 'albums' ? 'Albums / EPs' : 'Singles';

        tracksList.innerHTML = '<div class="loading-indicator">Loading tracks...</div>';

        const _wlPageSize = window._wlNextLimit || 200;
        window._wlNextLimit = null;
        const response = await fetch(`/api/wishlist/tracks?category=${category}&limit=${_wlPageSize}`);
        const data = await response.json();

        if (!response.ok) throw new Error(data.error || 'Failed to fetch tracks');

        const tracks = data.tracks || [];
        const totalAvailable = data.total || tracks.length;
        window._wlCategory = category;
        window._wlOffset = tracks.length;
        window._wlTotal = totalAvailable;

        if (tracks.length === 0) {
            tracksList.innerHTML = '<div class="empty-state">No tracks in this category</div>';
            return;
        }

        // For Albums/EPs, group by album
        if (category === 'albums') {
            const albumGroups = {};

            tracks.forEach(track => {
                let spotifyData = track.spotify_data;
                if (typeof spotifyData === 'string') {
                    try {
                        spotifyData = JSON.parse(spotifyData);
                    } catch (e) {
                        spotifyData = null;
                    }
                }

                const rawAlbum = spotifyData?.album;
                const albumName = (typeof rawAlbum === 'string' ? rawAlbum : rawAlbum?.name) || 'Unknown Album';

                // Handle both object format {name: '...'} and sanitized string format
                let artistName = 'Unknown Artist';
                let artistId = null;
                if (spotifyData?.artists?.[0]?.name) {
                    // Object format from Spotify API
                    artistName = spotifyData.artists[0].name;
                    artistId = spotifyData.artists[0].id;
                } else if (spotifyData?.artists?.[0] && typeof spotifyData.artists[0] === 'string') {
                    // Sanitized string format
                    artistName = spotifyData.artists[0];
                } else if (Array.isArray(track.artists) && track.artists.length > 0) {
                    // Fallback to track.artists
                    if (typeof track.artists[0] === 'string') {
                        artistName = track.artists[0];
                    } else if (track.artists[0]?.name) {
                        artistName = track.artists[0].name;
                        artistId = track.artists[0].id;
                    }
                }

                const albumImage = spotifyData?.album?.images?.[0]?.url || '';

                // Use album ID if available, otherwise create unique key from album + artist
                // Sanitize the ID to remove all special characters that could break DOM IDs or CSS selectors
                const albumId = spotifyData?.album?.id || `${albumName}_${artistName}`
                    .replace(/[^a-zA-Z0-9\s_-]/g, '')  // Remove all special chars except spaces, underscores, hyphens
                    .replace(/\s+/g, '_')               // Replace spaces with underscores
                    .toLowerCase();

                if (!albumGroups[albumId]) {
                    albumGroups[albumId] = {
                        albumName,
                        artistName,
                        artistId,
                        albumImage,
                        tracks: []
                    };
                }

                const spotifyTrackId = track.spotify_track_id || track.id || '';

                albumGroups[albumId].tracks.push({
                    name: track.name || 'Unknown Track',
                    artistName,
                    trackNumber: spotifyData?.track_number || 0,
                    spotifyTrackId
                });
            });

            // Render album cards
            let albumsHTML = '<div class="wishlist-album-grid">';
            Object.entries(albumGroups).forEach(([albumId, albumData]) => {
                // Sort tracks by track number
                albumData.tracks.sort((a, b) => a.trackNumber - b.trackNumber);

                const tracksListHTML = albumData.tracks.map(track => `
                    <div class="wishlist-album-track wishlist-track-item">
                        <label class="wishlist-checkbox-wrapper">
                            <input type="checkbox" class="wishlist-select-cb"
                                   data-track-id="${track.spotifyTrackId}">
                            <span class="wishlist-checkbox-custom"></span>
                        </label>
                        <span class="wishlist-album-track-name">${track.name}</span>
                        <button class="wishlist-delete-btn wishlist-delete-btn-small" data-track-id="${track.spotifyTrackId}" title="Remove from wishlist">
                            🗑️
                        </button>
                    </div>
                `).join('');

                // Handle missing album images with a placeholder
                const albumImageStyle = albumData.albumImage
                    ? `background-image: url('${albumData.albumImage}')`
                    : `background: linear-gradient(135deg, rgba(30, 30, 30, 0.9) 0%, rgba(50, 50, 50, 0.9) 100%); display: flex; align-items: center; justify-content: center; font-size: 40px;`;
                const albumImageContent = albumData.albumImage ? '' : '<span style="opacity: 0.3;">💿</span>';

                albumsHTML += `
                    <div class="wishlist-album-card">
                        <div class="wishlist-album-header" data-album-id="${albumId}">
                            <label class="wishlist-checkbox-wrapper">
                                <input type="checkbox" class="wishlist-album-select-all-cb"
                                       data-album-id="${albumId}">
                                <span class="wishlist-checkbox-custom"></span>
                            </label>
                            <div class="wishlist-album-image" style="${albumImageStyle}">${albumImageContent}</div>
                            <div class="wishlist-album-info">
                                <div class="wishlist-album-name">${albumData.albumName}</div>
                                <div class="wishlist-album-artist">${albumData.artistName}</div>
                                <div class="wishlist-album-track-count">${albumData.tracks.length} track${albumData.tracks.length !== 1 ? 's' : ''}</div>
                            </div>
                            <button class="wishlist-delete-btn wishlist-delete-album-btn" data-album-id="${albumId}" title="Remove all tracks from album">
                                🗑️
                            </button>
                            <div class="wishlist-album-expand-icon" id="expand-icon-${albumId}">▼</div>
                        </div>
                        <div class="wishlist-album-tracks" id="tracks-${albumId}" style="display: none;">
                            ${tracksListHTML}
                        </div>
                    </div>
                `;
            });
            albumsHTML += '</div>';

            tracksList.innerHTML = albumsHTML;
            if (totalAvailable > tracks.length) {
                tracksList.insertAdjacentHTML('beforeend',
                    `<button class="wishlist-load-more-btn" onclick="loadMoreWishlistTracks()">Load More (${tracks.length} of ${totalAvailable})</button>`);
            }
            _attachWishlistDelegation(tracksList);

        } else {
            // For Singles, show list with album images
            let tracksHTML = '';
            tracks.forEach((track, index) => {
                const trackName = track.name || 'Unknown Track';

                let spotifyData = track.spotify_data;
                if (typeof spotifyData === 'string') {
                    try {
                        spotifyData = JSON.parse(spotifyData);
                    } catch (e) {
                        spotifyData = null;
                    }
                }

                let artistName = 'Unknown Artist';
                if (spotifyData?.artists?.[0]?.name) {
                    artistName = spotifyData.artists[0].name;
                } else if (Array.isArray(track.artists) && track.artists.length > 0) {
                    if (typeof track.artists[0] === 'string') {
                        artistName = track.artists[0];
                    } else if (track.artists[0]?.name) {
                        artistName = track.artists[0].name;
                    }
                }

                let albumName = 'Unknown Album';
                if (spotifyData?.album?.name) {
                    albumName = spotifyData.album.name;
                } else if (typeof track.album === 'string') {
                    albumName = track.album;
                } else if (track.album?.name) {
                    albumName = track.album.name;
                }

                const albumImage = spotifyData?.album?.images?.[0]?.url || '';
                const spotifyTrackId = track.spotify_track_id || track.id || '';

                tracksHTML += `
                    <div class="playlist-track-item-with-image wishlist-track-item">
                        <label class="wishlist-checkbox-wrapper">
                            <input type="checkbox" class="wishlist-select-cb"
                                   data-track-id="${spotifyTrackId}">
                            <span class="wishlist-checkbox-custom"></span>
                        </label>
                        <div class="playlist-track-image" style="background-image: url('${albumImage}')"></div>
                        <div class="playlist-track-info">
                            <div class="playlist-track-name">${trackName}</div>
                            <div class="playlist-track-artist">${artistName} • ${albumName}</div>
                        </div>
                        <button class="wishlist-delete-btn" data-track-id="${spotifyTrackId}" title="Remove from wishlist">
                            🗑️
                        </button>
                    </div>
                `;
            });

            tracksList.innerHTML = tracksHTML;
            if (totalAvailable > tracks.length) {
                tracksList.insertAdjacentHTML('beforeend',
                    `<button class="wishlist-load-more-btn" onclick="loadMoreWishlistTracks()">Load More (${tracks.length} of ${totalAvailable})</button>`);
            }
            _attachWishlistDelegation(tracksList);
        }

    } catch (error) {
        console.error('Error loading category tracks:', error);
        showToast(`Failed to load tracks: ${error.message}`, 'error');
    }
}

async function loadMoreWishlistTracks() {
    const btn = document.querySelector('.wishlist-load-more-btn');
    if (btn) { btn.textContent = 'Loading...'; btn.disabled = true; }
    // Increase page size and reload
    window._wlOffset = (window._wlOffset || 200) + 200;
    // Override the page size for this reload
    window._wlNextLimit = window._wlOffset;
    selectWishlistCategory(window._wlCategory);
}

function _attachWishlistDelegation(container) {
    // Single click handler for all wishlist album/track interactions
    container.addEventListener('click', (e) => {
        const target = e.target;

        // Skip checkbox wrapper clicks — handled by change listener
        if (target.closest('.wishlist-checkbox-wrapper')) return;

        // Album header click (expand/collapse)
        const header = target.closest('.wishlist-album-header');
        if (header && !target.closest('.wishlist-delete-album-btn')) {
            toggleAlbumTracks(header.dataset.albumId);
            return;
        }

        // Album delete button
        const albumDelBtn = target.closest('.wishlist-delete-album-btn');
        if (albumDelBtn) {
            e.stopPropagation();
            removeAlbumFromWishlist(albumDelBtn.dataset.albumId, e);
            return;
        }

        // Track delete button
        const trackDelBtn = target.closest('.wishlist-delete-btn');
        if (trackDelBtn && trackDelBtn.dataset.trackId) {
            e.stopPropagation();
            removeTrackFromWishlist(trackDelBtn.dataset.trackId, e);
            return;
        }
    });

    // Separate change handler for checkboxes (more reliable than click for inputs)
    container.addEventListener('change', (e) => {
        const target = e.target;
        if (target.classList.contains('wishlist-album-select-all-cb')) {
            toggleWishlistAlbumSelection(target.dataset.albumId, target.checked);
        } else if (target.classList.contains('wishlist-select-cb')) {
            updateWishlistBatchBar();
        }
    });
}

function backToCategories() {
    _nebulaBack();
}

function toggleAlbumTracks(albumId) {
    const tracksElement = document.getElementById(`tracks-${albumId}`);
    const expandIcon = document.getElementById(`expand-icon-${albumId}`);

    if (tracksElement.style.display === 'none') {
        tracksElement.style.display = 'block';
        expandIcon.textContent = '▲';
    } else {
        tracksElement.style.display = 'none';
        expandIcon.textContent = '▼';
    }
}

/**
 * Get all checked wishlist track checkboxes
 */
function getCheckedWishlistTracks() {
    return Array.from(document.querySelectorAll('.wishlist-select-cb:checked'));
}

/**
 * Toggle select all / deselect all tracks in the current wishlist category
 */
function toggleWishlistSelectAll() {
    const allCheckboxes = document.querySelectorAll('.wishlist-select-cb');
    const albumCheckboxes = document.querySelectorAll('.wishlist-album-select-all-cb');
    const btn = document.getElementById('wishlist-select-all-btn');
    const allChecked = allCheckboxes.length > 0 && Array.from(allCheckboxes).every(cb => cb.checked);

    const newState = !allChecked;

    allCheckboxes.forEach(cb => { cb.checked = newState; });
    albumCheckboxes.forEach(cb => { cb.checked = newState; });

    // Expand all albums when selecting all
    if (newState) {
        document.querySelectorAll('.wishlist-album-tracks').forEach(el => {
            el.style.display = 'block';
        });
        document.querySelectorAll('[id^="expand-icon-"]').forEach(icon => {
            icon.textContent = '▲';
        });
    }

    if (btn) btn.textContent = newState ? 'Deselect All' : 'Select All';
    updateWishlistBatchBar();
}

/**
 * Update the wishlist batch action bar based on checkbox selection
 */
function updateWishlistBatchBar() {
    const checked = getCheckedWishlistTracks();
    const bar = document.getElementById('wishlist-batch-bar');
    const countEl = document.getElementById('wishlist-batch-count');

    if (!bar || !countEl) return;

    if (checked.length > 0) {
        bar.style.display = 'flex';
        countEl.textContent = `${checked.length} selected`;
    } else {
        bar.style.display = 'none';
    }

    // Sync the Select All button text
    const btn = document.getElementById('wishlist-select-all-btn');
    if (btn) {
        const allCheckboxes = document.querySelectorAll('.wishlist-select-cb');
        const allChecked = allCheckboxes.length > 0 && Array.from(allCheckboxes).every(cb => cb.checked);
        btn.textContent = allChecked ? 'Deselect All' : 'Select All';
    }
}

/**
 * Toggle all track checkboxes within an album when album header checkbox is clicked
 */
function toggleWishlistAlbumSelection(albumId, checked) {
    const tracksContainer = document.getElementById(`tracks-${albumId}`);
    if (tracksContainer) {
        // Expand the album tracks if selecting
        if (checked) {
            tracksContainer.style.display = 'block';
            const expandIcon = document.getElementById(`expand-icon-${albumId}`);
            if (expandIcon) expandIcon.textContent = '▲';
        }
        tracksContainer.querySelectorAll('.wishlist-select-cb').forEach(cb => {
            cb.checked = checked;
        });
    }
    updateWishlistBatchBar();
}

/**
 * Batch remove selected tracks from wishlist
 */
async function batchRemoveFromWishlist() {
    const checked = getCheckedWishlistTracks();
    if (checked.length === 0) return;

    const count = checked.length;
    const confirmed = await showConfirmationModal(
        'Remove Tracks',
        `Remove ${count} track${count !== 1 ? 's' : ''} from your wishlist?`,
        '🗑️'
    );

    if (!confirmed) return;

    const trackIds = checked.map(cb => cb.getAttribute('data-track-id'));

    try {
        const response = await fetch('/api/wishlist/remove-batch', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ spotify_track_ids: trackIds })
        });

        const data = await response.json();

        if (data.success) {
            showToast(`Removed ${data.removed} track(s) from wishlist`, 'success');

            // Reload the current category to refresh the list
            if (window.selectedWishlistCategory) {
                await selectWishlistCategory(window.selectedWishlistCategory);
            }

            // Update wishlist count in sidebar
            await updateWishlistCount();
        } else {
            showToast(`Failed to remove tracks: ${data.error}`, 'error');
        }
    } catch (error) {
        console.error('Error batch removing from wishlist:', error);
        showToast('Failed to remove tracks from wishlist', 'error');
    }
}

function showConfirmationModal(title, message, icon = '⚠️') {
    return new Promise((resolve) => {
        // Create modal if it doesn't exist
        let modal = document.getElementById('confirmation-modal-overlay');
        if (!modal) {
            modal = document.createElement('div');
            modal.id = 'confirmation-modal-overlay';
            modal.className = 'confirmation-modal-overlay';
            document.body.appendChild(modal);
        }

        // Set modal content
        modal.innerHTML = `
            <div class="confirmation-modal">
                <div class="confirmation-modal-icon">${icon}</div>
                <div class="confirmation-modal-title">${title}</div>
                <div class="confirmation-modal-message">${message}</div>
                <div class="confirmation-modal-buttons">
                    <button class="confirmation-modal-btn confirmation-modal-btn-cancel" id="confirm-cancel">Cancel</button>
                    <button class="confirmation-modal-btn confirmation-modal-btn-confirm" id="confirm-yes">Yes, Remove</button>
                </div>
            </div>
        `;

        // Show modal with animation
        setTimeout(() => {
            modal.classList.add('show');
        }, 10);

        // Escape key handler - defined outside so we can remove it
        const handleEscape = (e) => {
            if (e.key === 'Escape') {
                handleCancel();
            }
        };

        // Handle button clicks
        const handleCancel = () => {
            document.removeEventListener('keydown', handleEscape);
            modal.classList.remove('show');
            setTimeout(() => {
                modal.remove();
            }, 200);
            resolve(false);
        };

        const handleConfirm = () => {
            document.removeEventListener('keydown', handleEscape);
            modal.classList.remove('show');
            setTimeout(() => {
                modal.remove();
            }, 200);
            resolve(true);
        };

        document.getElementById('confirm-cancel').addEventListener('click', handleCancel);
        document.getElementById('confirm-yes').addEventListener('click', handleConfirm);

        // Close on overlay click
        modal.addEventListener('click', (e) => {
            if (e.target === modal) {
                handleCancel();
            }
        });

        // Add Escape key listener
        document.addEventListener('keydown', handleEscape);
    });
}

async function removeTrackFromWishlist(spotifyTrackId, event) {
    // Stop event propagation to prevent triggering parent click handlers
    if (event) {
        event.stopPropagation();
    }

    const confirmed = await showConfirmationModal(
        'Remove Track',
        'Are you sure you want to remove this track from your wishlist?',
        '🗑️'
    );

    if (!confirmed) {
        return;
    }

    try {
        const response = await fetch('/api/wishlist/remove-track', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ spotify_track_id: spotifyTrackId })
        });

        const data = await response.json();

        if (data.success) {
            showToast('Track removed from wishlist', 'success');

            // Reload the current category to refresh the list
            if (window.selectedWishlistCategory) {
                await selectWishlistCategory(window.selectedWishlistCategory);
            }

            // Update wishlist count in sidebar
            await updateWishlistCount();
        } else {
            showToast(`Failed to remove track: ${data.error}`, 'error');
        }
    } catch (error) {
        console.error('Error removing track from wishlist:', error);
        showToast('Failed to remove track from wishlist', 'error');
    }
}

async function removeAlbumFromWishlist(albumId, event) {
    // Stop event propagation to prevent triggering parent click handlers
    if (event) {
        event.stopPropagation();
    }

    const confirmed = await showConfirmationModal(
        'Remove Album',
        'Are you sure you want to remove all tracks from this album from your wishlist?',
        '💿'
    );

    if (!confirmed) {
        return;
    }

    try {
        const response = await fetch('/api/wishlist/remove-album', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ album_id: albumId })
        });

        const data = await response.json();

        if (data.success) {
            showToast(`Removed ${data.removed_count} track(s) from wishlist`, 'success');

            // Reload the current category to refresh the list
            if (window.selectedWishlistCategory) {
                await selectWishlistCategory(window.selectedWishlistCategory);
            }

            // Update wishlist count in sidebar
            await updateWishlistCount();
        } else {
            showToast(`Failed to remove album: ${data.error}`, 'error');
        }
    } catch (error) {
        console.error('Error removing album from wishlist:', error);
        showToast('Failed to remove album from wishlist', 'error');
    }
}

async function downloadSelectedCategory() {
    const category = window.selectedWishlistCategory;
    if (!category) {
        showToast('No category selected', 'error');
        return;
    }

    // Collect checked track IDs
    const checkedBoxes = document.querySelectorAll('.wishlist-select-cb:checked');
    const selectedTrackIds = new Set(Array.from(checkedBoxes).map(cb => cb.dataset.trackId).filter(Boolean));

    await openDownloadMissingWishlistModal(category, selectedTrackIds.size > 0 ? selectedTrackIds : null);
}

async function openDownloadMissingWishlistModal(category = null, selectedTrackIds = null) {
    showLoadingOverlay('Loading wishlist...');
    const playlistId = "wishlist"; // Use a consistent ID for wishlist

    // Check if a process is already active for the wishlist
    if (activeDownloadProcesses[playlistId]) {
        console.log(`Modal for wishlist already exists. Showing it.`);
        const process = activeDownloadProcesses[playlistId];
        if (process.modalElement) {
            // Show helpful message if it's a completed process
            if (process.status === 'complete') {
                showToast('Showing previous results. Close this modal to start a new analysis.', 'info');
            }
            process.modalElement.style.display = 'flex';
            WishlistModalState.setVisible(); // Track that modal is now visible
        }
        hideLoadingOverlay(); // Always hide overlay before returning
        return; // Don't create a new one
    }

    console.log(`📥 Opening Download Missing Tracks modal for wishlist${category ? ' (' + category + ')' : ''}`);

    // Store category in global state for when process starts
    window.currentWishlistCategory = category;

    // Fetch actual wishlist tracks from the server
    let tracks;
    try {
        // Build API URL with optional category filter
        const apiUrl = category ? `/api/wishlist/tracks?category=${category}` : '/api/wishlist/tracks';

        const response = await fetch('/api/wishlist/count');
        const countData = await response.json();
        if (countData.count === 0) {
            showToast('Wishlist is empty. No tracks to download.', 'info');
            hideLoadingOverlay();
            return;
        }

        // Fetch the actual wishlist tracks for display (filtered by category if specified)
        const tracksResponse = await fetch(apiUrl);
        if (!tracksResponse.ok) {
            throw new Error('Failed to fetch wishlist tracks');
        }
        const tracksData = await tracksResponse.json();
        tracks = tracksData.tracks || [];

        // Filter to only selected tracks if user made a selection
        if (selectedTrackIds && selectedTrackIds.size > 0) {
            tracks = tracks.filter(t => selectedTrackIds.has(t.id) || selectedTrackIds.has(t.spotify_track_id));
            console.log(`📥 Filtered to ${tracks.length} selected tracks (from ${tracksData.tracks?.length || 0} total)`);
        }

    } catch (error) {
        showToast(`Failed to fetch wishlist data: ${error.message}`, 'error');
        hideLoadingOverlay();
        return;
    }

    currentPlaylistTracks = tracks;
    currentModalPlaylistId = playlistId;

    let modal = document.createElement('div');
    modal.id = `download-missing-modal-${playlistId}`; // Unique ID
    modal.className = 'download-missing-modal'; // Use class for styling
    modal.style.display = 'none'; // Start hidden
    document.body.appendChild(modal);

    // Register the new process in our global state tracker
    activeDownloadProcesses[playlistId] = {
        status: 'idle', // idle, running, complete, cancelled
        modalElement: modal,
        poller: null,
        batchId: null,
        playlist: { id: playlistId, name: "Wishlist" }, // Create a pseudo-playlist object
        tracks: tracks
    };

    // Generate hero section for wishlist context
    const heroContext = {
        type: 'wishlist',
        trackCount: tracks.length,
        playlistId: playlistId
    };

    modal.innerHTML = `
        <div class="download-missing-modal-content" data-context="wishlist">
            <div class="download-missing-modal-header">
                ${generateDownloadModalHeroSection(heroContext)}
            </div>

            <div class="download-missing-modal-body">
                <div class="download-progress-section">
                    <div class="progress-item">
                        <div class="progress-label">
                            🔍 Library Analysis
                            <span id="analysis-progress-text-${playlistId}">Ready to start</span>
                        </div>
                        <div class="progress-bar">
                            <div class="progress-fill analysis" id="analysis-progress-fill-${playlistId}"></div>
                        </div>
                    </div>
                    <div class="progress-item">
                        <div class="progress-label">
                            ⏬ Downloads
                            <span id="download-progress-text-${playlistId}">Waiting for analysis</span>
                        </div>
                        <div class="progress-bar">
                            <div class="progress-fill download" id="download-progress-fill-${playlistId}"></div>
                        </div>
                    </div>
                </div>
                
                <div class="download-tracks-section">
                    <div class="download-tracks-header">
                        <h3 class="download-tracks-title">📋 Track Analysis & Download Status</h3>
                    </div>
                    <div class="download-tracks-table-container">
                        <table class="download-tracks-table">
                            <thead>
                                <tr>
                                    <th>#</th>
                                    <th>Track</th>
                                    <th>Artist</th>
                                    <th>Library Match</th>
                                    <th>Download Status</th>
                                    <th>Actions</th>
                                </tr>
                            </thead>
                            <tbody id="download-tracks-tbody-${playlistId}">
                                ${tracks.map((track, index) => `
                                    <tr data-track-index="${index}">
                                        <td class="track-number">${index + 1}</td>
                                        <td class="track-name" title="${escapeHtml(track.name)}">${escapeHtml(track.name)}</td>
                                        <td class="track-artist" title="${escapeHtml(formatArtists(track.artists))}">${escapeHtml(formatArtists(track.artists))}</td>
                                        <td class="track-match-status match-checking" id="match-${playlistId}-${index}">🔍 Pending</td>
                                        <td class="track-download-status" id="download-${playlistId}-${index}">-</td>
                                        <td class="track-actions" id="actions-${playlistId}-${index}">-</td>
                                    </tr>
                                `).join('')}
                            </tbody>
                        </table>
                    </div>
                </div>
            </div>
            
            <div class="download-missing-modal-footer">
                <div class="download-phase-controls">
                    <div class="force-download-toggle-container" style="margin-bottom: 0px;">
                        <label class="force-download-toggle">
                            <input type="checkbox" id="force-download-all-${playlistId}">
                            <span>Force Download All</span>
                        </label>
                    </div>
                    <button class="download-control-btn primary" id="begin-analysis-btn-${playlistId}" onclick="startWishlistMissingTracksProcess('${playlistId}')">
                        Begin Analysis
                    </button>
                    <button class="download-control-btn danger" id="cancel-all-btn-${playlistId}" onclick="cancelAllOperations('${playlistId}')" style="display: none;">
                        Cancel All
                    </button>
                    <button class="download-control-btn secondary" id="cleanup-wishlist-btn-${playlistId}" onclick="cleanupWishlist('${playlistId}')" style="margin-left: 10px;">
                        🧹 Cleanup Wishlist
                    </button>
                    <button class="download-control-btn danger" id="clear-wishlist-btn-${playlistId}" onclick="clearWishlist('${playlistId}')" style="margin-left: 10px;">
                        🗑️ Clear Wishlist
                    </button>
                </div>
                <div class="modal-close-section">
                    <button class="download-control-btn secondary" onclick="closeDownloadMissingModal('${playlistId}')">Close</button>
                </div>
            </div>
        </div>
    `;

    applyProgressiveTrackRendering(playlistId, tracks.length);
    modal.style.display = 'flex';
    hideLoadingOverlay();
    WishlistModalState.setVisible(); // Track that new wishlist modal is now visible
}

async function startWishlistMissingTracksProcess(playlistId) {
    const process = activeDownloadProcesses[playlistId];
    if (!process) return;

    console.log(`🚀 Kicking off wishlist missing tracks process`);
    try {
        process.status = 'running';
        // Note: Wishlist processes don't affect sync page refresh button state
        document.getElementById(`begin-analysis-btn-${playlistId}`).style.display = 'none';
        document.getElementById(`cancel-all-btn-${playlistId}`).style.display = 'inline-block';

        // Check if force download toggle is enabled
        const forceDownloadCheckbox = document.getElementById(`force-download-all-${playlistId}`);
        const forceDownloadAll = forceDownloadCheckbox ? forceDownloadCheckbox.checked : false;

        // Hide the force download toggle during processing
        const forceToggleContainer = forceDownloadCheckbox ? forceDownloadCheckbox.closest('.force-download-toggle-container') : null;
        if (forceToggleContainer) {
            forceToggleContainer.style.display = 'none';
        }

        // Extract track IDs from what the user is currently seeing in the modal
        // This prevents race conditions where wishlist changes between modal open and analysis start
        const trackIds = process.tracks ? process.tracks.map(t => t.spotify_track_id || t.id).filter(id => id) : null;
        console.log(`🎯 [Wishlist] Sending ${trackIds ? trackIds.length : 'all'} specific track IDs to prevent race condition`);

        const response = await fetch('/api/wishlist/download_missing', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                force_download_all: forceDownloadAll,
                category: window.currentWishlistCategory,  // Keep for backward compat
                track_ids: trackIds  // NEW: Send exact tracks to process
            })
        });

        const data = await response.json();
        if (!data.success) {
            // Special handling for auto-processing conflict
            if (response.status === 409) {
                console.log('🤖 [Wishlist] Auto-processing is running, redirecting to download manager');
                showToast('Wishlist auto-processing is already running. Opening Download Manager...', 'info');

                // Close wishlist modal and show download manager
                const wishlistModal = document.getElementById('download-modal-wishlist');
                if (wishlistModal) {
                    wishlistModal.remove();
                }
                delete activeDownloadProcesses[playlistId];

                // Open download manager to show active batch
                setTimeout(() => {
                    const downloadManager = document.getElementById('download-manager-modal');
                    if (downloadManager) {
                        downloadManager.style.display = 'flex';
                    } else {
                        openDownloadManagerModal();
                    }
                }, 300);
                return;
            }
            // Special handling for rate limit
            if (response.status === 429) {
                throw new Error(`${data.error} Try closing some other download processes first.`);
            }
            throw new Error(data.error);
        }

        process.batchId = data.batch_id;
        console.log(`✅ Wishlist process started successfully. Batch ID: ${data.batch_id}`);

        // Start polling for updates
        startModalDownloadPolling(playlistId);

    } catch (error) {
        console.error('Error starting wishlist missing tracks process:', error);
        showToast(`Error: ${error.message}`, 'error');

        // Reset UI state on error
        process.status = 'idle';
        // Note: Wishlist processes don't affect sync page refresh button state
        document.getElementById(`begin-analysis-btn-${playlistId}`).style.display = 'inline-block';
        document.getElementById(`cancel-all-btn-${playlistId}`).style.display = 'none';

        // Show the force download toggle again
        const forceToggleContainer = document.querySelector(`#force-download-all-${playlistId}`)?.closest('.force-download-toggle-container');
        if (forceToggleContainer) {
            forceToggleContainer.style.display = 'flex';
        }
    }
}

async function startMissingTracksProcess(playlistId) {
    const process = activeDownloadProcesses[playlistId];
    if (!process) return;

    console.log(`🚀 Kicking off unified missing tracks process for playlist: ${playlistId}`);
    try {
        process.status = 'running';
        updatePlaylistCardUI(playlistId);
        updateRefreshButtonState();

        // Set album to downloading status if this is an artist album
        if (playlistId.startsWith('artist_album_')) {
            // Format: artist_album_{artist.id}_{album.id}
            const parts = playlistId.split('_');
            if (parts.length >= 4) {
                const albumId = parts.slice(3).join('_'); // In case album ID has underscores
                const totalTracks = process.tracks ? process.tracks.length : 0;
                setAlbumDownloadingStatus(albumId, 0, totalTracks);
                console.log(`🔄 Set album ${albumId} to downloading status (0/${totalTracks} tracks)`);
                console.log(`🔍 Virtual playlist ID: ${playlistId} → Album ID: ${albumId}`);
            }
        }

        // Update YouTube playlist phase to 'downloading' if this is a YouTube playlist
        if (playlistId.startsWith('youtube_')) {
            const urlHash = playlistId.replace('youtube_', '');
            updateYouTubeCardPhase(urlHash, 'downloading');
            // Also update mirrored playlist card if applicable
            if (urlHash.startsWith('mirrored_')) {
                updateMirroredCardPhase(urlHash, 'downloading');
            }
        }

        // Update Tidal playlist phase to 'downloading' if this is a Tidal playlist
        if (playlistId.startsWith('tidal_')) {
            const tidalPlaylistId = playlistId.replace('tidal_', '');
            if (tidalPlaylistStates[tidalPlaylistId]) {
                tidalPlaylistStates[tidalPlaylistId].phase = 'downloading';
                updateTidalCardPhase(tidalPlaylistId, 'downloading');
                console.log(`🔄 Updated Tidal playlist ${tidalPlaylistId} to downloading phase`);
            }
        }

        // Update Beatport chart phase to 'downloading' if this is a Beatport chart
        if (playlistId.startsWith('beatport_')) {
            const urlHash = playlistId.replace('beatport_', '');
            const state = youtubePlaylistStates[urlHash];

            if (state && state.is_beatport_playlist) {
                const chartHash = state.beatport_chart_hash || urlHash;

                // Update frontend states
                state.phase = 'downloading';
                if (beatportChartStates[chartHash]) {
                    beatportChartStates[chartHash].phase = 'downloading';
                }

                // Update card UI
                updateBeatportCardPhase(chartHash, 'downloading');

                // Update backend state
                try {
                    fetch(`/api/beatport/charts/update-phase/${chartHash}`, {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ phase: 'downloading' })
                    });
                } catch (error) {
                    console.warn('⚠️ Error updating backend Beatport phase to downloading:', error);
                }

                console.log(`🔄 Updated Beatport chart ${chartHash} to downloading phase`);
            }
        }

        // Update Spotify Public playlist phase to 'downloading' if this is a Spotify Public playlist
        if (playlistId.startsWith('spotify_public_')) {
            const urlHash = playlistId.replace('spotify_public_', '');
            if (spotifyPublicPlaylistStates[urlHash]) {
                spotifyPublicPlaylistStates[urlHash].phase = 'downloading';
                spotifyPublicPlaylistStates[urlHash].convertedSpotifyPlaylistId = playlistId;
                updateSpotifyPublicCardPhase(urlHash, 'downloading');

                try {
                    fetch(`/api/spotify-public/update_phase/${urlHash}`, {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ phase: 'downloading', converted_spotify_playlist_id: playlistId })
                    });
                } catch (error) {
                    console.warn('Error updating backend Spotify Public phase to downloading:', error);
                }

                console.log(`🔄 Updated Spotify Public playlist ${urlHash} to downloading phase`);
            }
        }

        // Update Deezer playlist phase to 'downloading' if this is a Deezer playlist
        if (playlistId.startsWith('deezer_')) {
            const deezerPlaylistId = playlistId.replace('deezer_', '');
            if (deezerPlaylistStates[deezerPlaylistId]) {
                deezerPlaylistStates[deezerPlaylistId].phase = 'downloading';
                deezerPlaylistStates[deezerPlaylistId].convertedSpotifyPlaylistId = playlistId;
                updateDeezerCardPhase(deezerPlaylistId, 'downloading');

                try {
                    fetch(`/api/deezer/update_phase/${deezerPlaylistId}`, {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ phase: 'downloading', converted_spotify_playlist_id: playlistId })
                    });
                } catch (error) {
                    console.warn('Error updating backend Deezer phase to downloading:', error);
                }

                console.log(`🔄 Updated Deezer playlist ${deezerPlaylistId} to downloading phase`);
            }
        }

        // Update ListenBrainz playlist phase to 'downloading' if this is a ListenBrainz playlist
        if (playlistId.startsWith('listenbrainz_')) {
            const playlistMbid = playlistId.replace('listenbrainz_', '');
            const state = listenbrainzPlaylistStates[playlistMbid];

            if (state) {
                // Update frontend state
                state.phase = 'downloading';

                // Update backend state
                try {
                    fetch(`/api/listenbrainz/update-phase/${playlistMbid}`, {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ phase: 'downloading' })
                    });
                } catch (error) {
                    console.warn('⚠️ Error updating backend ListenBrainz phase to downloading:', error);
                }

                console.log(`🔄 Updated ListenBrainz playlist ${playlistMbid} to downloading phase`);
            }
        }
        document.getElementById(`begin-analysis-btn-${playlistId}`).style.display = 'none';
        document.getElementById(`cancel-all-btn-${playlistId}`).style.display = 'inline-block';

        // Hide wishlist button if it exists (only for non-wishlist modals)
        const wishlistBtn = document.getElementById(`add-to-wishlist-btn-${playlistId}`);
        if (wishlistBtn) {
            wishlistBtn.style.display = 'none';
        }

        // Add to discover download sidebar if this is a discover page download
        if (process.discoverMetadata) {
            const playlistName = process.playlist.name;
            const imageUrl = process.discoverMetadata.imageUrl;
            const type = process.discoverMetadata.type;
            addDiscoverDownload(playlistId, playlistName, type, imageUrl);
            console.log(`📥 [BEGIN ANALYSIS] Added discover download: ${playlistName}`);
        }

        // Check if force download toggle is enabled
        const forceDownloadCheckbox = document.getElementById(`force-download-all-${playlistId}`);
        const forceDownloadAll = forceDownloadCheckbox ? forceDownloadCheckbox.checked : false;

        // Check if playlist folder mode toggle is enabled (only for sync page playlists)
        const playlistFolderModeCheckbox = document.getElementById(`playlist-folder-mode-${playlistId}`);
        const playlistFolderMode = playlistFolderModeCheckbox ? playlistFolderModeCheckbox.checked : false;

        // Hide the force download toggle during processing
        const forceToggleContainer = forceDownloadCheckbox ? forceDownloadCheckbox.closest('.force-download-toggle-container') : null;
        if (forceToggleContainer) {
            forceToggleContainer.style.display = 'none';
        }

        // Filter tracks based on checkbox selection (if checkboxes exist in this modal)
        const tbody = document.getElementById(`download-tracks-tbody-${playlistId}`);
        let selectedTracks = process.tracks;
        if (tbody) {
            const allCbs = tbody.querySelectorAll('.track-select-cb');
            if (allCbs.length > 0) {
                // Checkboxes exist — filter to only checked tracks
                const checkedCbs = tbody.querySelectorAll('.track-select-cb:checked');
                const selectedIndices = new Set([...checkedCbs].map(cb => parseInt(cb.dataset.trackIndex)));
                console.log(`🔲 [Track Selection] Total checkboxes: ${allCbs.length}, Checked: ${checkedCbs.length}`);
                console.log(`🔲 [Track Selection] Checked indices:`, [...selectedIndices]);
                console.log(`🔲 [Track Selection] process.tracks has ${process.tracks.length} items, first: "${process.tracks[0]?.name}", last: "${process.tracks[process.tracks.length - 1]?.name}"`);
                // Stamp each selected track with its original table index so the backend
                // maps status updates back to the correct modal row
                selectedTracks = process.tracks
                    .map((track, i) => ({ ...track, _original_index: i }))
                    .filter(track => selectedIndices.has(track._original_index));
                console.log(`🔲 [Track Selection] Filtered to ${selectedTracks.length} tracks:`, selectedTracks.map(t => `[${t._original_index}] ${t.name}`));
                // Disable checkboxes once analysis starts
                allCbs.forEach(cb => { cb.disabled = true; });
            }
        }
        const selectAllCb = document.getElementById(`select-all-${playlistId}`);
        if (selectAllCb) selectAllCb.disabled = true;

        // Prepare request body - add album/artist context for artist album downloads
        const wingItState = youtubePlaylistStates[playlistId] || {};
        const isWingIt = wingItState.wing_it || false;
        const requestBody = {
            tracks: selectedTracks,
            force_download_all: forceDownloadAll || isWingIt,
            wing_it: isWingIt,
        };

        // If this is an artist album download, use album name and include full context
        // Match 'artist_album_', 'enhanced_search_album_', 'discover_album_', and 'seasonal_album_' prefixes
        // Note: 'enhanced_search_track_' is excluded — single track search results use singles context
        const _isAlbumContext = playlistId.startsWith('artist_album_') || playlistId.startsWith('enhanced_search_album_') || playlistId.startsWith('discover_album_') || playlistId.startsWith('seasonal_album_') || playlistId.startsWith('spotify_library_') || playlistId.startsWith('issue_download_') || playlistId.startsWith('library_redownload_') || playlistId.startsWith('beatport_release_');
        const _isSearchTrack = playlistId.startsWith('enhanced_search_track_') || playlistId.startsWith('gsearch_track_');
        if (_isAlbumContext || _isSearchTrack) {
            requestBody.playlist_name = process.album?.name || process.playlist.name;
            requestBody.is_album_download = _isAlbumContext; // false for single track search results
            requestBody.album_context = process.album;   // Full Spotify album object
            requestBody.artist_context = process.artist; // Full Spotify artist object
            console.log(`🎵 [${_isAlbumContext ? 'Album' : 'Single Track'}] Sending context: ${process.album?.name} by ${process.artist?.name}`);
        } else {
            // For playlists/wishlists, use the virtual playlist name
            requestBody.playlist_name = process.playlist.name;
            // Add playlist folder mode flag for sync page playlists
            requestBody.playlist_folder_mode = playlistFolderMode;
            if (playlistFolderMode) {
                console.log(`📁 [Playlist Folder] Enabled for playlist: ${process.playlist.name}`);
            }
        }

        const response = await fetch(`/api/playlists/${playlistId}/start-missing-process`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(requestBody)
        });

        const data = await response.json();
        if (!data.success) {
            // Special handling for rate limit
            if (response.status === 429) {
                throw new Error(`${data.error} Try closing some other download processes first.`);
            }
            throw new Error(data.error);
        }

        process.batchId = data.batch_id;

        // Update Beatport backend state with download_process_id now that we have the batchId
        if (playlistId.startsWith('beatport_')) {
            const urlHash = playlistId.replace('beatport_', '');
            const state = youtubePlaylistStates[urlHash];
            if (state && state.is_beatport_playlist) {
                const chartHash = state.beatport_chart_hash || urlHash;
                try {
                    fetch(`/api/beatport/charts/update-phase/${chartHash}`, {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({
                            phase: 'downloading',
                            download_process_id: data.batch_id
                        })
                    });
                    console.log(`🔄 Updated Beatport backend with download_process_id: ${data.batch_id}`);
                } catch (error) {
                    console.warn('⚠️ Error updating Beatport backend with download_process_id:', error);
                }
            }
        }

        // Update ListenBrainz backend state with download_process_id and convertedSpotifyPlaylistId
        if (playlistId.startsWith('listenbrainz_')) {
            const playlistMbid = playlistId.replace('listenbrainz_', '');
            const state = listenbrainzPlaylistStates[playlistMbid];
            if (state) {
                // Store in frontend state
                state.download_process_id = data.batch_id;
                state.convertedSpotifyPlaylistId = playlistId;

                // Update backend state
                try {
                    fetch(`/api/listenbrainz/update-phase/${playlistMbid}`, {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({
                            phase: 'downloading',
                            download_process_id: data.batch_id,
                            converted_spotify_playlist_id: playlistId
                        })
                    });
                    console.log(`🔄 Updated ListenBrainz backend with download_process_id: ${data.batch_id}`);
                } catch (error) {
                    console.warn('⚠️ Error updating ListenBrainz backend with download_process_id:', error);
                }
            }
        }

        startModalDownloadPolling(playlistId);
    } catch (error) {
        showToast(`Failed to start process: ${error.message}`, 'error');
        process.status = 'cancelled';

        // Reset button states on error
        const beginBtn = document.getElementById(`begin-analysis-btn-${playlistId}`);
        const cancelBtn = document.getElementById(`cancel-all-btn-${playlistId}`);
        const wishlistBtn = document.getElementById(`add-to-wishlist-btn-${playlistId}`);
        if (beginBtn) beginBtn.style.display = 'inline-block';
        if (cancelBtn) cancelBtn.style.display = 'none';
        if (wishlistBtn) wishlistBtn.style.display = 'inline-block';

        // Show the force download toggle again
        const forceToggleContainer = document.querySelector(`#force-download-all-${playlistId}`)?.closest('.force-download-toggle-container');
        if (forceToggleContainer) {
            forceToggleContainer.style.display = 'flex';
        }

        cleanupDownloadProcess(playlistId);
    }
}


function updateTrackAnalysisResults(playlistId, results) {
    // Update match results for all rows (tracks are now pre-populated)
    for (const result of results) {
        const matchElement = document.getElementById(`match-${playlistId}-${result.track_index}`);
        if (matchElement) {
            matchElement.textContent = result.found ? '✅ Found' : '❌ Missing';
            matchElement.className = `track-match-status ${result.found ? 'match-found' : 'match-missing'}`;
        }
    }
}



// ============================================================================
// GLOBAL BATCHED POLLING SYSTEM - Optimized for multiple concurrent modals
// ============================================================================

let globalDownloadStatusPoller = null;
let globalPollingFailureCount = 0; // Track consecutive failures for exponential backoff
let globalPollingBaseInterval = 2000; // Base polling interval in ms - MATCHES sync.py exactly

function startGlobalDownloadPolling() {
    // Always run HTTP polling as a fallback — WebSocket connections can silently
    // stop delivering messages (room subscription lost, server emit error, proxy
    // timeout) without triggering a disconnect event. The 2-second poll is cheap
    // (single batched request) and ensures modals never go stale.
    if (globalDownloadStatusPoller) {
        console.debug('🔄 [Global Polling] Already running, skipping start');
        return; // Prevent duplicate pollers
    }

    console.log('🔄 [Global Polling] Starting batched download status polling');

    globalDownloadStatusPoller = setInterval(async () => {
        if (document.hidden) return; // Skip polling when tab is not visible
        // Get all active processes that need polling
        const activeBatchIds = [];
        const batchToPlaylistMap = {};
        let hasOpenWishlistModal = false;

        Object.entries(activeDownloadProcesses).forEach(([playlistId, process]) => {
            // Include running AND recently-completed batches — ensures late task
            // status updates still reach the modal so rows don't freeze mid-download
            if (process.batchId && (process.status === 'running' || process.status === 'complete')) {
                activeBatchIds.push(process.batchId);
                batchToPlaylistMap[process.batchId] = playlistId;
            }

            // Check if there's an open wishlist modal (visible and idle/waiting)
            if (playlistId === 'wishlist' && process.modalElement &&
                process.modalElement.style.display === 'flex' &&
                (!process.batchId || process.status !== 'running')) {
                hasOpenWishlistModal = true;
            }
        });

        // Special handling for open wishlist modal - check for new auto-processing
        if (hasOpenWishlistModal) {
            try {
                const response = await fetch('/api/active-processes');
                if (response.ok) {
                    const data = await response.json();
                    const processes = data.active_processes || [];
                    const serverWishlistProcess = processes.find(p => p.playlist_id === 'wishlist');

                    if (serverWishlistProcess) {
                        console.log('🔄 [Global Polling] Detected auto-processing for open wishlist modal - rehydrating');
                        await rehydrateModal(serverWishlistProcess, false); // false = not user-requested
                    }
                }
            } catch (error) {
                console.debug('⚠️ [Global Polling] Failed to check for wishlist auto-processing:', error);
            }
        }

        if (activeBatchIds.length === 0) {
            console.debug('📊 [Global Polling] No active processes, continuing polling');
            return;
        }

        try {
            // Single batched API call for all active processes
            const queryParams = activeBatchIds.map(id => `batch_ids=${id}`).join('&');
            const response = await fetch(`/api/download_status/batch?${queryParams}`);

            if (!response.ok) {
                throw new Error(`HTTP ${response.status}: ${response.statusText}`);
            }

            const data = await response.json();
            console.debug(`📊 [Global Polling] Received batched update for ${Object.keys(data.batches).length} processes`);

            // Process each batch's status data using existing logic
            Object.entries(data.batches).forEach(([batchId, statusData]) => {
                const playlistId = batchToPlaylistMap[batchId];
                if (!playlistId || statusData.error) {
                    if (statusData.error) {
                        console.error(`❌ [Global Polling] Error for batch ${batchId}:`, statusData.error);
                    }
                    return;
                }

                // Use existing modal update logic - zero changes needed!
                processModalStatusUpdate(playlistId, statusData);
            });

            // ENHANCED: Reset failure count on successful polling
            globalPollingFailureCount = 0;

        } catch (error) {
            console.error('❌ [Global Polling] Batched request failed:', error);

            // ENHANCED: Implement exponential backoff on failure
            globalPollingFailureCount++;

            if (globalPollingFailureCount >= 5) {
                console.error(`🚨 [Global Polling] ${globalPollingFailureCount} consecutive failures, continuing with backoff`);
                // Don't stop polling - just continue with exponential backoff
            }

            // Exponential backoff: increase interval temporarily
            const backoffInterval = Math.min(globalPollingBaseInterval * Math.pow(2, globalPollingFailureCount - 1), 8000);
            console.warn(`⚠️ [Global Polling] Failure ${globalPollingFailureCount}/5, backing off to ${backoffInterval}ms`);

            // Temporarily adjust the polling interval
            if (globalDownloadStatusPoller) {
                clearInterval(globalDownloadStatusPoller);
                globalDownloadStatusPoller = null;

                // Restart with backoff interval
                setTimeout(() => {
                    if (Object.keys(activeDownloadProcesses).length > 0) {
                        startGlobalDownloadPollingWithInterval(backoffInterval);
                    }
                }, backoffInterval);
            }
        }
    }, globalPollingBaseInterval); // Use base interval initially
}

function startGlobalDownloadPollingWithInterval(interval) {
    if (globalDownloadStatusPoller) {
        console.debug('🔄 [Global Polling] Already running, skipping start with interval');
        return;
    }

    console.log(`🔄 [Global Polling] Starting with interval ${interval}ms`);

    // Use the exact same logic as startGlobalDownloadPolling but with custom interval
    globalDownloadStatusPoller = setInterval(async () => {
        const activeBatchIds = [];
        const batchToPlaylistMap = {};
        let hasOpenWishlistModal = false;

        Object.entries(activeDownloadProcesses).forEach(([playlistId, process]) => {
            if (process.batchId && (process.status === 'running' || process.status === 'complete')) {
                activeBatchIds.push(process.batchId);
                batchToPlaylistMap[process.batchId] = playlistId;
            }

            // Check if there's an open wishlist modal (visible and idle/waiting)
            if (playlistId === 'wishlist' && process.modalElement &&
                process.modalElement.style.display === 'flex' &&
                (!process.batchId || process.status !== 'running')) {
                hasOpenWishlistModal = true;
            }
        });

        // Special handling for open wishlist modal - check for new auto-processing
        if (hasOpenWishlistModal) {
            try {
                const response = await fetch('/api/active-processes');
                if (response.ok) {
                    const data = await response.json();
                    const processes = data.active_processes || [];
                    const serverWishlistProcess = processes.find(p => p.playlist_id === 'wishlist');

                    if (serverWishlistProcess) {
                        console.log('🔄 [Global Polling] Detected auto-processing for open wishlist modal - rehydrating');
                        await rehydrateModal(serverWishlistProcess, false); // false = not user-requested
                    }
                }
            } catch (error) {
                console.debug('⚠️ [Global Polling] Failed to check for wishlist auto-processing:', error);
            }
        }

        if (activeBatchIds.length === 0) {
            console.debug('📊 [Global Polling] No active processes, continuing polling');
            return;
        }

        try {
            const queryParams = activeBatchIds.map(id => `batch_ids=${id}`).join('&');
            const response = await fetch(`/api/download_status/batch?${queryParams}`);

            if (!response.ok) {
                throw new Error(`HTTP ${response.status}: ${response.statusText}`);
            }

            const data = await response.json();
            console.debug(`📊 [Global Polling] Received batched update for ${Object.keys(data.batches).length} processes`);

            Object.entries(data.batches).forEach(([batchId, statusData]) => {
                const playlistId = batchToPlaylistMap[batchId];
                if (!playlistId || statusData.error) {
                    if (statusData.error) {
                        console.error(`❌ [Global Polling] Error for batch ${batchId}:`, statusData.error);
                    }
                    return;
                }
                processModalStatusUpdate(playlistId, statusData);
            });

            // Success - reset to normal interval if we were backing off
            globalPollingFailureCount = 0;
            if (interval !== globalPollingBaseInterval) {
                console.log('✅ [Global Polling] Recovered from backoff, returning to normal interval');
                clearInterval(globalDownloadStatusPoller);
                globalDownloadStatusPoller = null;
                startGlobalDownloadPolling(); // Restart with normal interval
            }

        } catch (error) {
            console.error('❌ [Global Polling] Request failed:', error);
            globalPollingFailureCount++;

            if (globalPollingFailureCount >= 5) {
                console.error(`🚨 [Global Polling] Too many failures, continuing with backoff`);
                // Don't stop polling - just continue with exponential backoff
            }
        }
    }, interval);
}

function stopGlobalDownloadPolling() {
    if (globalDownloadStatusPoller) {
        console.log('🛑 [Global Polling] Stopping batched download status polling');
        clearInterval(globalDownloadStatusPoller);
        globalDownloadStatusPoller = null;
    }
}

// --- Error tooltip for failed/cancelled downloads (fixed-position, escapes overflow) ---
function _getErrorTooltipPopup() {
    let el = document.getElementById('error-tooltip-popup');
    if (!el) {
        el = document.createElement('div');
        el.id = 'error-tooltip-popup';
        document.body.appendChild(el);
    }
    return el;
}

function _hideErrorTooltip() {
    const popup = document.getElementById('error-tooltip-popup');
    if (popup) popup.classList.remove('visible');
}

function _ensureErrorTooltipListeners(statusEl) {
    if (statusEl._errorTooltipBound) return;
    statusEl._errorTooltipBound = true;
    statusEl.addEventListener('mouseenter', function () {
        const msg = this.dataset.errorMsg;
        if (!msg || !this.offsetParent) return; // skip if element is hidden
        const popup = _getErrorTooltipPopup();
        popup.textContent = msg;
        popup.classList.add('visible');
        const rect = this.getBoundingClientRect();
        const popupRect = popup.getBoundingClientRect();
        let left = rect.left + rect.width / 2 - popupRect.width / 2;
        let top = rect.top - popupRect.height - 10;
        // Keep within viewport
        if (left < 8) left = 8;
        if (left + popupRect.width > window.innerWidth - 8) left = window.innerWidth - 8 - popupRect.width;
        if (top < 8) { top = rect.bottom + 10; } // flip below if no room above
        popup.style.left = left + 'px';
        popup.style.top = top + 'px';
    });
    statusEl.addEventListener('mouseleave', _hideErrorTooltip);

    // Dismiss tooltip when the scrollable modal body scrolls
    const scrollParent = statusEl.closest('.download-missing-modal-body');
    if (scrollParent && !scrollParent._errorTooltipScrollBound) {
        scrollParent._errorTooltipScrollBound = true;
        scrollParent.addEventListener('scroll', _hideErrorTooltip, { passive: true });
    }
}

function _ensureCandidatesClickListener(statusEl) {
    if (statusEl._candidatesClickBound) return;
    statusEl._candidatesClickBound = true;
    statusEl.addEventListener('click', function (e) {
        e.stopPropagation();
        _hideErrorTooltip();
        const taskId = this.dataset.taskId;
        if (taskId) showCandidatesModal(taskId);
    });
}

async function showCandidatesModal(taskId) {
    try {
        const resp = await fetch(`/api/downloads/task/${encodeURIComponent(taskId)}/candidates`);
        if (!resp.ok) { console.error('Failed to fetch candidates:', resp.status); return; }
        const data = await resp.json();
        _renderCandidatesModal(data);
    } catch (err) {
        console.error('Error fetching candidates:', err);
    }
}

// Format helpers used by both auto-candidates and manual-search rendering.
function _candidatesFmtSize(bytes) {
    if (!bytes) return '-';
    const units = ['B', 'KB', 'MB', 'GB'];
    let s = bytes, u = 0;
    while (s >= 1024 && u < units.length - 1) { s /= 1024; u++; }
    return `${s.toFixed(1)} ${units[u]}`;
}

function _candidatesFmtDur(ms) {
    if (!ms) return '-';
    const sec = Math.floor(ms / 1000);
    return `${Math.floor(sec / 60)}:${(sec % 60).toString().padStart(2, '0')}`;
}

// Build a single <tr> for the candidates table. ``rowClass`` lets the
// manual-search renderer distinguish its rows from the auto-candidates
// rows (different click binding scope). ``showSourceBadge`` adds a small
// per-row source pill — used in hybrid "All sources" mode where the user
// otherwise can't tell which source a row came from.
function _renderCandidateRow(c, index, rowClass, showSourceBadge) {
    const shortFile = c.filename ? c.filename.split(/[/\\]/).pop() : '-';
    const qBadge = c.quality
        ? `<span class="candidates-quality-badge candidates-quality-${c.quality.toLowerCase()}">${c.quality.toUpperCase()}</span>`
        : '';
    const sourceBadge = (showSourceBadge && c.source)
        ? `<span class="candidates-source-badge" title="Source">${escapeHtml(c.source)}</span> `
        : '';
    return `<tr class="${rowClass}">
        <td class="candidates-col-index">${index + 1}</td>
        <td class="candidates-col-file" title="${escapeHtml(c.filename || '')}">${sourceBadge}${escapeHtml(shortFile)}</td>
        <td class="candidates-col-quality">${qBadge}${c.bitrate ? ` ${c.bitrate}kbps` : ''}</td>
        <td class="candidates-col-size">${_candidatesFmtSize(c.size)}</td>
        <td class="candidates-col-duration">${_candidatesFmtDur(c.duration)}</td>
        <td class="candidates-col-user" title="Queue: ${c.queue_length || 0} | Slots: ${c.free_upload_slots || 0}">${escapeHtml(c.username || '-')}</td>
        <td class="candidates-col-action"><button class="candidates-download-btn" data-index="${index}" title="Download this file">⬇</button></td>
    </tr>`;
}

function _renderCandidatesModal(data) {
    let overlay = document.getElementById('candidates-modal-overlay');
    if (overlay) overlay.remove();

    const trackName = data.track_info?.name || 'Unknown Track';
    const trackArtist = data.track_info?.artist || 'Unknown Artist';
    const candidates = data.candidates || [];
    const errorMsg = data.error_message || '';
    const downloadMode = data.download_mode || 'soulseek';
    const availableSources = Array.isArray(data.available_sources) ? data.available_sources : [];
    // Hybrid mode shows the dropdown; everything else implies a single source.
    const isHybrid = downloadMode === 'hybrid';

    let tableRows = '';
    if (candidates.length === 0) {
        tableRows = `<tr><td colspan="7" style="text-align:center; color: rgba(255,255,255,0.5); padding: 30px;">
            No candidates were found during search.</td></tr>`;
    } else {
        // Auto-candidates only show source badges in hybrid mode (where the
        // user can't infer source from the dropdown).
        candidates.forEach((c, i) => {
            tableRows += _renderCandidateRow(c, i, 'candidates-row-auto', isHybrid);
        });
    }

    // ----- Manual search bar -----
    let sourceControl;
    if (isHybrid && availableSources.length > 0) {
        const optionsHtml = ['<option value="all">All sources</option>']
            .concat(availableSources.map(s =>
                `<option value="${escapeHtml(s.id)}">${escapeHtml(s.label)}</option>`
            ))
            .join('');
        sourceControl = `<select class="candidates-manual-source" id="candidates-manual-source">${optionsHtml}</select>`;
    } else {
        // Single-source mode — render a small static label, not a dropdown.
        const onlySrc = availableSources[0];
        const label = onlySrc ? onlySrc.label : (downloadMode || 'configured source');
        sourceControl = `<span class="candidates-manual-source-label">Searching ${escapeHtml(label)}</span>`;
    }

    const manualSearchHtml = `
        <div class="candidates-manual-search">
            <div class="candidates-manual-search-header">Manual search</div>
            <div class="candidates-manual-search-controls">
                <input type="text"
                       class="candidates-manual-search-input"
                       id="candidates-manual-search-input"
                       placeholder="Search for a different track..."
                       maxlength="200" />
                ${sourceControl}
                <button class="candidates-manual-search-btn"
                        id="candidates-manual-search-btn"
                        disabled>Search</button>
            </div>
            <div class="candidates-manual-search-hint" id="candidates-manual-search-hint">Type at least 2 characters</div>
            <div class="candidates-manual-search-results" id="candidates-manual-search-results"></div>
        </div>`;

    overlay = document.createElement('div');
    overlay.id = 'candidates-modal-overlay';
    overlay.className = 'candidates-modal-overlay';
    overlay.onclick = (e) => { if (e.target === overlay) closeCandidatesModal(); };
    overlay.innerHTML = `
        <div class="candidates-modal">
            <div class="candidates-modal-header">
                <div>
                    <h2 class="candidates-modal-title">Search Results</h2>
                    <div class="candidates-modal-subtitle">${escapeHtml(trackName)} — ${escapeHtml(trackArtist)}</div>
                </div>
                <button class="candidates-modal-close" onclick="closeCandidatesModal()">&#x2715;</button>
            </div>
            <div class="candidates-modal-body">
                ${errorMsg ? `<div class="candidates-error-summary">${escapeHtml(errorMsg)}</div>` : ''}
                ${manualSearchHtml}
                <div class="candidates-auto-section">
                    <div class="candidates-count">${candidates.length} candidate${candidates.length !== 1 ? 's' : ''} found${candidates.length > 0 ? ' but none passed filters' : ''}</div>
                    <div class="candidates-table-wrapper">
                        <table class="candidates-table">
                            <thead><tr>
                                <th>#</th><th>File</th><th>Quality</th><th>Size</th><th>Duration</th><th>User</th><th></th>
                            </tr></thead>
                            <tbody>${tableRows}</tbody>
                        </table>
                    </div>
                </div>
            </div>
        </div>`;

    document.body.appendChild(overlay);
    requestAnimationFrame(() => overlay.classList.add('visible'));

    // Bind auto-candidate download buttons (existing behavior, scoped to
    // .candidates-row-auto so manual-search rows don't double-trigger).
    overlay.querySelectorAll('.candidates-row-auto .candidates-download-btn').forEach(btn => {
        btn.addEventListener('click', () => {
            const idx = parseInt(btn.dataset.index);
            const c = candidates[idx];
            if (c) downloadCandidate(data.task_id, c, trackName);
        });
    });

    // Wire manual search controls.
    _wireManualSearch(overlay, data.task_id, trackName, isHybrid);
}

// Manual-search wiring — input/button/dropdown. Kept separate from
// _renderCandidatesModal so the existing render path stays readable and
// any future refactor can lift this into its own module.
function _wireManualSearch(overlay, taskId, trackName, isHybrid) {
    const input = overlay.querySelector('#candidates-manual-search-input');
    const button = overlay.querySelector('#candidates-manual-search-btn');
    const hint = overlay.querySelector('#candidates-manual-search-hint');
    const resultsContainer = overlay.querySelector('#candidates-manual-search-results');
    const sourceSelect = overlay.querySelector('#candidates-manual-source');
    if (!input || !button || !resultsContainer) return;

    // Aggregated results across all source streams for the current query.
    // Cleared at the start of each new search.
    let currentResults = [];
    let inFlight = false;
    let abortController = null;

    const updateButtonState = () => {
        const q = (input.value || '').trim();
        const tooShort = q.length < 2;
        button.disabled = tooShort || inFlight;
        if (hint) {
            if (tooShort) {
                hint.textContent = 'Type at least 2 characters';
                hint.style.display = '';
            } else {
                hint.style.display = 'none';
            }
        }
    };

    const _renderTableShell = (query) => {
        resultsContainer.innerHTML = `
            <div class="candidates-manual-search-status" id="candidates-manual-search-status">Searching...</div>
            <div class="candidates-table-wrapper" style="display: none;" id="candidates-manual-table-wrapper">
                <table class="candidates-table">
                    <thead><tr>
                        <th>#</th><th>File</th><th>Quality</th><th>Size</th><th>Duration</th><th>User</th><th></th>
                    </tr></thead>
                    <tbody id="candidates-manual-tbody"></tbody>
                </table>
            </div>`;
    };

    const _appendRows = (newCandidates, query) => {
        if (!newCandidates || newCandidates.length === 0) return;
        const startIdx = currentResults.length;
        currentResults = currentResults.concat(newCandidates);

        const wrapper = resultsContainer.querySelector('#candidates-manual-table-wrapper');
        const tbody = resultsContainer.querySelector('#candidates-manual-tbody');
        const statusEl = resultsContainer.querySelector('#candidates-manual-search-status');
        if (!tbody || !wrapper) return;

        let rowsHtml = '';
        newCandidates.forEach((c, i) => {
            rowsHtml += _renderCandidateRow(c, startIdx + i, 'candidates-row-manual', isHybrid);
        });
        tbody.insertAdjacentHTML('beforeend', rowsHtml);
        wrapper.style.display = '';
        if (statusEl) {
            statusEl.textContent = `${currentResults.length} result${currentResults.length !== 1 ? 's' : ''} so far...`;
        }

        // Wire newly-appended buttons
        tbody.querySelectorAll('.candidates-download-btn').forEach(btn => {
            if (btn._candidatesWired) return;
            btn._candidatesWired = true;
            btn.addEventListener('click', () => {
                const idx = parseInt(btn.dataset.index);
                const c = currentResults[idx];
                if (c) downloadCandidate(taskId, c, trackName);
            });
        });
    };

    const _setStatus = (text) => {
        const statusEl = resultsContainer.querySelector('#candidates-manual-search-status');
        if (statusEl) statusEl.textContent = text;
    };

    const _setError = (msg) => {
        resultsContainer.innerHTML = `<div class="candidates-manual-search-error">${escapeHtml(msg)}</div>`;
    };

    const runSearch = async () => {
        const q = (input.value || '').trim();
        if (q.length < 2 || inFlight) return;

        if (abortController) {
            try { abortController.abort(); } catch (_) { }
        }
        abortController = new AbortController();

        const source = sourceSelect ? sourceSelect.value : 'all';
        inFlight = true;
        button.disabled = true;
        const originalLabel = button.textContent;
        button.textContent = 'Searching...';
        currentResults = [];
        _renderTableShell(q);

        try {
            const resp = await fetch(`/api/downloads/task/${encodeURIComponent(taskId)}/manual-search`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ query: q, source: source }),
                signal: abortController.signal,
            });
            if (!resp.ok) {
                let errMsg = 'Search failed';
                try {
                    const payload = await resp.json();
                    if (payload && payload.error) errMsg = payload.error;
                } catch (_) { }
                _setError(errMsg);
                return;
            }

            const reader = resp.body.getReader();
            const decoder = new TextDecoder();
            let buffer = '';
            const errors = [];

            while (true) {
                const { value, done } = await reader.read();
                if (done) break;
                buffer += decoder.decode(value, { stream: true });

                let lineEnd;
                while ((lineEnd = buffer.indexOf('\n')) >= 0) {
                    const line = buffer.slice(0, lineEnd).trim();
                    buffer = buffer.slice(lineEnd + 1);
                    if (!line) continue;
                    let msg;
                    try { msg = JSON.parse(line); } catch (_) { continue; }

                    if (msg.type === 'source_results') {
                        _appendRows(msg.candidates || [], q);
                    } else if (msg.type === 'source_error') {
                        errors.push(`${msg.source}: ${msg.error}`);
                    } else if (msg.type === 'done') {
                        if (currentResults.length === 0) {
                            const errorNote = errors.length
                                ? `<div class="candidates-manual-search-empty-note">${errors.length} source${errors.length !== 1 ? 's' : ''} failed</div>`
                                : '';
                            resultsContainer.innerHTML = `
                                <div class="candidates-manual-search-empty">No manual search results for "${escapeHtml(q)}"</div>
                                ${errorNote}`;
                        } else {
                            _setStatus(`${currentResults.length} result${currentResults.length !== 1 ? 's' : ''}`);
                        }
                    }
                }
            }
        } catch (err) {
            if (err.name === 'AbortError') return;
            console.error('Manual search failed:', err);
            _setError('Search request failed');
        } finally {
            inFlight = false;
            button.textContent = originalLabel;
            updateButtonState();
        }
    };

    input.addEventListener('input', updateButtonState);
    input.addEventListener('keydown', (e) => {
        if (e.key === 'Enter' && !button.disabled) {
            e.preventDefault();
            runSearch();
        }
    });
    button.addEventListener('click', runSearch);

    updateButtonState();
}

async function downloadCandidate(taskId, candidate, trackName) {
    if (!await showConfirmDialog({ title: 'Download File', message: `Download this file as "${trackName}"?\n\n${candidate.filename?.split(/[/\\]/).pop() || 'Unknown file'}\nfrom ${candidate.username || 'Unknown user'}`, confirmText: 'Download' })) return;
    try {
        const resp = await fetch(`/api/downloads/task/${encodeURIComponent(taskId)}/download-candidate`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(candidate)
        });
        const result = await resp.json();
        if (result.success) {
            closeCandidatesModal();
            showToast(result.message || 'Download initiated', 'success');
        } else {
            showToast(`Failed: ${result.error}`, 'error');
        }
    } catch (err) {
        console.error('Error initiating manual download:', err);
        showToast('Failed to initiate download', 'error');
    }
}

function closeCandidatesModal() {
    const overlay = document.getElementById('candidates-modal-overlay');
    if (overlay) {
        overlay.classList.remove('visible');
        setTimeout(() => overlay.remove(), 300);
    }
}

function processModalStatusUpdate(playlistId, data) {
    // This function contains ALL the existing polling logic from startModalDownloadPolling
    // Extracted so it can be called from both individual and batched polling
    const process = activeDownloadProcesses[playlistId];
    if (!process) {
        console.debug(`⚠️ [Status Update] No process found for ${playlistId}, skipping update`);
        return;
    }

    if (data.error) {
        console.error(`❌ [Status Update] Error for ${playlistId}: ${data.error}`);
        return;
    }

    // ENHANCED: Validate response data to prevent UI corruption
    if (!data || typeof data !== 'object') {
        console.error(`❌ [Status Update] Invalid data for ${playlistId}:`, data);
        return;
    }

    // ENHANCED: Validate task data structure
    if (data.tasks && !Array.isArray(data.tasks)) {
        console.error(`❌ [Status Update] Invalid tasks data for ${playlistId} - not an array:`, data.tasks);
        return;
    }

    console.debug(`📊 [Status Update] Processing update for ${playlistId}: phase=${data.phase}, tasks=${(data.tasks || []).length}`);

    // Note: Wishlist modal visibility is now managed by handleWishlistButtonClick() only
    // Auto-show logic has been simplified to prevent conflicts

    if (data.phase === 'analysis') {
        const progress = data.analysis_progress;
        const percent = progress.total > 0 ? (progress.processed / progress.total) * 100 : 0;
        document.getElementById(`analysis-progress-fill-${playlistId}`).style.width = `${percent}%`;
        document.getElementById(`analysis-progress-text-${playlistId}`).textContent =
            `${progress.processed}/${progress.total} tracks analyzed`;
        if (data.analysis_results) {
            updateTrackAnalysisResults(playlistId, data.analysis_results);
            // Update stats when we first get analysis results
            const foundCount = data.analysis_results.filter(r => r.found).length;
            const missingCount = data.analysis_results.filter(r => !r.found).length;
            document.getElementById(`stat-found-${playlistId}`).textContent = foundCount;
            document.getElementById(`stat-missing-${playlistId}`).textContent = missingCount;

            // Auto-save M3U file for playlists after analysis
            autoSavePlaylistM3U(playlistId);
        }
    } else if (data.phase === 'downloading' || data.phase === 'complete' || data.phase === 'error') {
        console.debug(`📊 [Status Update] Processing ${data.phase} phase for playlistId: ${playlistId}, tasks: ${(data.tasks || []).length}`);

        if (document.getElementById(`analysis-progress-fill-${playlistId}`).style.width !== '100%') {
            document.getElementById(`analysis-progress-fill-${playlistId}`).style.width = '100%';
            document.getElementById(`analysis-progress-text-${playlistId}`).textContent = 'Analysis complete!';
            if (data.analysis_results) {
                updateTrackAnalysisResults(playlistId, data.analysis_results);
                const foundCount = data.analysis_results.filter(r => r.found).length;
                const missingCount = data.analysis_results.filter(r => !r.found).length;
                document.getElementById(`stat-found-${playlistId}`).textContent = foundCount;
                document.getElementById(`stat-missing-${playlistId}`).textContent = missingCount;
            }
        }
        const missingTracks = (data.analysis_results || []).filter(r => !r.found);
        const missingCount = missingTracks.length;
        let completedCount = 0;
        let failedOrCancelledCount = 0;
        let notFoundCount = 0;

        // Verify modal exists before processing tasks
        const modal = document.getElementById(`download-missing-modal-${playlistId}`);
        if (!modal) {
            console.error(`❌ [Status Update] Modal not found: download-missing-modal-${playlistId}`);
            return;
        }

        // Update download progress text immediately when entering downloading phase
        // This handles the case where tasks array is empty or still being populated
        const downloadProgressText = document.getElementById(`download-progress-text-${playlistId}`);
        if (data.phase === 'downloading' && missingCount > 0 && (!data.tasks || data.tasks.length === 0)) {
            // No tasks yet, but we're in downloading phase with missing tracks
            if (downloadProgressText) {
                downloadProgressText.textContent = 'Preparing downloads...';
                console.log(`📥 [Download Phase] Preparing ${missingCount} downloads...`);
            }
        }

        (data.tasks || []).forEach(task => {
            const row = document.querySelector(`#download-missing-modal-${CSS.escape(playlistId)} tr[data-track-index="${task.track_index}"]`);
            if (!row) {
                console.debug(`❌ [Status Update] Row not found for playlistId: ${playlistId}, track_index: ${task.track_index}`);
                return;
            }

            // V2 SYSTEM: Check for persistent cancel state from backend
            const isV2Task = task.playlist_id !== undefined; // V2 tasks have playlist_id
            const cancelRequested = task.cancel_requested || false;
            const uiState = task.ui_state || 'normal';

            // Legacy protection for old system compatibility
            if (row.dataset.locallyCancelled === 'true' && !isV2Task) {
                failedOrCancelledCount++;
                return; // Only skip for legacy system tasks
            }

            // Mark row with V2 system info
            if (isV2Task) {
                row.dataset.useV2System = 'true';
                row.dataset.cancelRequested = cancelRequested.toString();
                row.dataset.uiState = uiState;
            }

            row.dataset.taskId = task.task_id;
            const statusEl = document.getElementById(`download-${playlistId}-${task.track_index}`);
            const actionsEl = document.getElementById(`actions-${playlistId}-${task.track_index}`);

            let statusText = '';
            // V2 SYSTEM: Handle UI state override for cancelling tasks
            if (isV2Task && uiState === 'cancelling' && task.status !== 'cancelled') {
                statusText = '🔄 Cancelling...';
            } else {
                switch (task.status) {
                    case 'pending': statusText = '⏸️ Pending'; break;
                    case 'searching': statusText = '🔍 Searching...'; break;
                    case 'downloading': statusText = `⏬ Downloading... ${Math.round(task.progress || 0)}%`; break;
                    case 'post_processing': statusText = '⌛ Processing...'; break;
                    case 'completed': statusText = '✅ Completed'; completedCount++; break;
                    case 'not_found': statusText = '🔇 Not Found'; notFoundCount++; break;
                    case 'failed': statusText = '❌ Failed'; failedOrCancelledCount++; break;
                    case 'cancelled': statusText = '🚫 Cancelled'; failedOrCancelledCount++; break;
                    default: statusText = `⚪ ${task.status}`; break;
                }
            }

            if (statusEl) {
                statusEl.classList.remove('has-error-tooltip');
                statusEl.removeAttribute('title');
                statusEl.removeAttribute('data-error-msg');
                statusEl.textContent = statusText;

                if ((task.status === 'failed' || task.status === 'cancelled' || task.status === 'not_found') && task.error_message) {
                    statusEl.classList.add('has-error-tooltip');
                    statusEl.dataset.errorMsg = task.error_message;
                    _ensureErrorTooltipListeners(statusEl);
                }
                // Make not_found / failed / cancelled cells clickable to open
                // the candidates modal. Always bind — even when no auto-search
                // candidates were cached — because the modal carries the manual
                // search bar, which is the user's recourse for empty results.
                if (task.status === 'not_found' || task.status === 'failed' || task.status === 'cancelled') {
                    statusEl.classList.add('has-candidates');
                    statusEl.dataset.taskId = task.task_id;
                    _ensureCandidatesClickListener(statusEl);
                }
                console.debug(`✅ [Status Update] Updated track ${task.track_index} to: ${statusText}${isV2Task ? ' (V2)' : ''}`);
            } else {
                console.warn(`❌ [Status Update] Status element not found: download-${playlistId}-${task.track_index}`);
            }

            // V2 SYSTEM: Smart button management with persistent state awareness
            if (actionsEl && !['completed', 'failed', 'cancelled', 'not_found', 'post_processing'].includes(task.status)) {
                // Check if we're in a cancelling state
                if (isV2Task && uiState === 'cancelling') {
                    actionsEl.innerHTML = '<span style="color: #666;">Cancelling...</span>';
                } else {
                    // Create V2 cancel button for all active tasks
                    const onclickHandler = isV2Task ? 'cancelTrackDownloadV2' : 'cancelTrackDownload';
                    actionsEl.innerHTML = `<button class="cancel-track-btn" title="Cancel this download" onclick="${onclickHandler}('${playlistId}', ${task.track_index})">×</button>`;
                }
            } else if (actionsEl && ['completed', 'failed', 'cancelled', 'not_found', 'post_processing'].includes(task.status)) {
                actionsEl.innerHTML = '-'; // No actions available for terminal or processing states
            }
        });

        // ENHANCED: Validate worker counts from server data
        const serverActiveWorkers = data.active_count || 0;
        const maxWorkers = data.max_concurrent || 3;

        // V2 SYSTEM: Simplified worker counting - backend is authoritative
        // Count active tasks, excluding locally cancelled legacy tasks only
        const clientActiveWorkers = (data.tasks || []).filter(task => {
            const row = document.querySelector(`tr[data-track-index="${task.track_index}"]`);
            const isLegacyCancelled = row && row.dataset.locallyCancelled === 'true' && !row.dataset.useV2System;
            return ['searching', 'downloading', 'queued'].includes(task.status) && !isLegacyCancelled;
        }).length;

        // Log discrepancies for debugging
        if (serverActiveWorkers !== clientActiveWorkers) {
            console.warn(`🔍 [Worker Validation] ${playlistId}: server reports ${serverActiveWorkers} active, client sees ${clientActiveWorkers} active tasks`);

            // If server reports 0 but client sees active tasks, this might indicate ghost workers were fixed
            if (serverActiveWorkers === 0 && clientActiveWorkers > 0) {
                console.warn(`🚨 [Worker Validation] Server reports 0 workers but client sees ${clientActiveWorkers} active tasks - potential UI desync`);
            }
        }

        console.debug(`📊 [Worker Status] ${playlistId}: ${serverActiveWorkers}/${maxWorkers} active workers, ${clientActiveWorkers} client-side active tasks`);

        const totalFinished = completedCount + failedOrCancelledCount + notFoundCount;
        const progressPercent = missingCount > 0 ? (totalFinished / missingCount) * 100 : 0;
        document.getElementById(`download-progress-fill-${playlistId}`).style.width = `${progressPercent}%`;
        document.getElementById(`download-progress-text-${playlistId}`).textContent = `${completedCount}/${missingCount} completed (${progressPercent.toFixed(0)}%)`;
        document.getElementById(`stat-downloaded-${playlistId}`).textContent = completedCount;

        // Auto-save M3U file once when all downloads finish (not on every poll cycle).
        // Previously this fired on EVERY 2-second poll when completedCount > 0, flooding
        // the server with heavyweight M3U generation requests that exhausted Flask threads
        // and caused the batch status endpoint to hang — killing the poller.

        // CLIENT-SIDE COMPLETION: Only complete when ALL task rows in the UI reflect a terminal state.
        // Using totalFinished (derived from DOM updates in THIS render pass) prevents premature
        // completion when the server sends phase='complete' before all rows have been updated.
        const allTracksFinished = totalFinished >= missingCount && missingCount > 0 && totalFinished > 0;
        // Extra guard: require the server to also report no active tasks
        const serverHasActiveWork = (data.tasks || []).some(t =>
            ['downloading', 'searching', 'queued', 'pending', 'post_processing'].includes(t.status));
        if (allTracksFinished && !serverHasActiveWork && process.status !== 'complete') {
            console.log(`🎯 [Client Completion] All ${totalFinished}/${missingCount} tracks finished - completing modal locally`);

            // Hide cancel button and mark as complete
            document.getElementById(`cancel-all-btn-${playlistId}`).style.display = 'none';
            process.status = 'complete';
            updatePlaylistCardUI(playlistId);

            // Save M3U once on completion (not during progress polling)
            if (completedCount > 0) {
                autoSavePlaylistM3U(playlistId);
            }

            // Show the force download toggle again
            const forceToggleContainer = document.querySelector(`#force-download-all-${playlistId}`)?.closest('.force-download-toggle-container');
            if (forceToggleContainer) {
                forceToggleContainer.style.display = 'flex';
            }

            // Set album to downloaded status if this is an artist album
            if (playlistId.startsWith('artist_album_')) {
                const parts = playlistId.split('_');
                if (parts.length >= 4) {
                    const albumId = parts.slice(3).join('_');
                    setTimeout(() => setAlbumDownloadedStatus(albumId), 500); // Small delay to ensure UI updates
                }
            }

            // Update mirrored playlist card phase on client-side completion
            if (playlistId.startsWith('youtube_')) {
                const urlHash = playlistId.replace('youtube_', '');
                if (urlHash.startsWith('mirrored_')) {
                    updateMirroredCardPhase(urlHash, 'download_complete');
                }
            }

            // Auto-save final M3U file for playlists
            autoSavePlaylistM3U(playlistId);

            // Show completion message
            let completionParts = [`${completedCount} downloaded`];
            if (notFoundCount > 0) completionParts.push(`${notFoundCount} not found`);
            if (failedOrCancelledCount > 0) completionParts.push(`${failedOrCancelledCount} failed`);
            const completionMessage = `Download complete! ${completionParts.join(', ')}.`;
            showToast(completionMessage, 'success');

            // Refresh server playlists tab so it reflects newly synced tracks
            if (typeof loadServerPlaylists === 'function') {
                setTimeout(() => loadServerPlaylists(), 2000);
            }

            // Auto-close wishlist modal when completed (for auto-processing)
            if (playlistId === 'wishlist') {
                console.log('🔄 [Auto-Wishlist] Auto-closing completed wishlist modal to enable next cycle');
                setTimeout(() => {
                    closeDownloadMissingModal(playlistId);
                }, 3000); // 3-second delay to show completion message
            }

            // Check if any other processes still need polling
            checkAndCleanupGlobalPolling();

            return; // Skip waiting for backend signal
        }

        // FIXED: Only trigger completion logic when backend actually reports batch as complete
        // Don't assume completion based on task counts - let backend determine when truly complete
        if (data.phase === 'complete' || data.phase === 'error') {
            // Enhanced check for background auto-processing for wishlist
            const isWishlist = (playlistId === 'wishlist');
            const isModalHidden = (process.modalElement && process.modalElement.style.display === 'none');
            const isAutoInitiated = data.auto_initiated || false; // Server indicates if batch was auto-started
            const isBackgroundWishlist = isWishlist && (isModalHidden || isAutoInitiated);

            // Note: Auto-show logic removed - wishlist modal visibility managed by user interaction only

            if (data.phase === 'cancelled') {
                if (process.status !== 'cancelled') {
                    process.status = 'cancelled';

                    // Reset YouTube playlist phase to 'discovered' if this is a YouTube playlist on cancel
                    if (playlistId.startsWith('youtube_')) {
                        const urlHash = playlistId.replace('youtube_', '');
                        updateYouTubeCardPhase(urlHash, 'discovered');
                        if (urlHash.startsWith('mirrored_')) {
                            updateMirroredCardPhase(urlHash, 'discovered');
                        }
                    }

                    showToast(`Process cancelled for ${process.playlist.name}.`, 'info');
                }
            } else if (data.phase === 'error') {
                if (process.status !== 'complete') {
                    process.status = 'complete';
                    updatePlaylistCardUI(playlistId); // Update card to show ready for review

                    // Reset YouTube playlist phase to 'discovered' if this is a YouTube playlist on error
                    if (playlistId.startsWith('youtube_')) {
                        const urlHash = playlistId.replace('youtube_', '');
                        updateYouTubeCardPhase(urlHash, 'discovered');
                        if (urlHash.startsWith('mirrored_')) {
                            updateMirroredCardPhase(urlHash, 'discovered');
                        }
                    }

                    showToast(`Process for ${process.playlist.name} failed!`, 'error');
                }
            } else {
                if (process.status !== 'complete') {
                    process.status = 'complete';
                    updatePlaylistCardUI(playlistId); // Update card to show ready for review

                    // Update YouTube playlist phase to 'download_complete' if this is a YouTube playlist
                    if (playlistId.startsWith('youtube_')) {
                        const urlHash = playlistId.replace('youtube_', '');
                        updateYouTubeCardPhase(urlHash, 'download_complete');
                        if (urlHash.startsWith('mirrored_')) {
                            updateMirroredCardPhase(urlHash, 'download_complete');
                        }
                    }

                    // Update Tidal playlist phase to 'download_complete' if this is a Tidal playlist
                    if (playlistId.startsWith('tidal_')) {
                        const tidalPlaylistId = playlistId.replace('tidal_', '');
                        if (tidalPlaylistStates[tidalPlaylistId]) {
                            tidalPlaylistStates[tidalPlaylistId].phase = 'download_complete';
                            // Store the download process ID for potential modal rehydration
                            tidalPlaylistStates[tidalPlaylistId].download_process_id = process.batchId;
                            updateTidalCardPhase(tidalPlaylistId, 'download_complete');
                            console.log(`✅ [Status Complete] Updated Tidal playlist ${tidalPlaylistId} to download_complete phase`);
                        }
                    }

                    // Update Beatport chart phase to 'download_complete' if this is a Beatport chart
                    if (playlistId.startsWith('beatport_')) {
                        const urlHash = playlistId.replace('beatport_', '');
                        const state = youtubePlaylistStates[urlHash];

                        if (state && state.is_beatport_playlist) {
                            const chartHash = state.beatport_chart_hash || urlHash;

                            // Update frontend states
                            state.phase = 'download_complete';
                            state.download_process_id = process.batchId;
                            if (beatportChartStates[chartHash]) {
                                beatportChartStates[chartHash].phase = 'download_complete';
                            }

                            // Update card UI
                            updateBeatportCardPhase(chartHash, 'download_complete');

                            // Update backend state
                            try {
                                fetch(`/api/beatport/charts/update-phase/${chartHash}`, {
                                    method: 'POST',
                                    headers: { 'Content-Type': 'application/json' },
                                    body: JSON.stringify({
                                        phase: 'download_complete',
                                        download_process_id: process.batchId
                                    })
                                });
                            } catch (error) {
                                console.warn('⚠️ Error updating backend Beatport phase to download_complete:', error);
                            }

                            console.log(`✅ [Status Complete] Updated Beatport chart ${chartHash} to download_complete phase`);
                        }
                    }

                    // Handle background wishlist processing completion specially
                    if (isBackgroundWishlist) {
                        console.log(`🎉 Background wishlist processing complete: ${completedCount} downloaded, ${notFoundCount} not found, ${failedOrCancelledCount} failed`);

                        // Reset modal to idle state to prevent "complete" phase disruption
                        setTimeout(() => {
                            resetWishlistModalToIdleState();
                            // Server-side auto-processing will handle next cycle automatically
                        }, 500);

                        return; // Skip normal completion handling
                    }

                    // Show completion summary with wishlist stats (matching sync.py behavior)
                    let completionMessage = `Process complete for ${process.playlist.name}!`;
                    let messageType = 'success';

                    // Check for wishlist summary from backend (added when failed/cancelled tracks are processed)
                    if (data.wishlist_summary) {
                        const summary = data.wishlist_summary;
                        let summaryParts = [`Downloaded: ${completedCount}`];
                        if (notFoundCount > 0) summaryParts.push(`Not Found: ${notFoundCount}`);
                        if (failedOrCancelledCount > 0) summaryParts.push(`Failed: ${failedOrCancelledCount}`);
                        completionMessage = `Download process complete! ${summaryParts.join(', ')}.`;

                        if (summary.tracks_added > 0) {
                            completionMessage += ` Added ${summary.tracks_added} failed track${summary.tracks_added !== 1 ? 's' : ''} to wishlist for automatic retry.`;
                        } else if (summary.total_failed > 0) {
                            completionMessage += ` ${summary.total_failed} track${summary.total_failed !== 1 ? 's' : ''} could not be added to wishlist.`;
                            messageType = 'warning';
                        }
                    }

                    showToast(completionMessage, messageType);
                }
            }

            document.getElementById(`cancel-all-btn-${playlistId}`).style.display = 'none';

            // Mark process as complete and trigger cleanup check
            process.status = 'complete';
            updatePlaylistCardUI(playlistId);

            // Check if any other processes still need polling
            checkAndCleanupGlobalPolling();
        }
    }
}

function checkAndCleanupGlobalPolling() {
    // Check if any processes still need polling
    const hasActivePolling = Object.values(activeDownloadProcesses)
        .some(p => p.batchId && p.status === 'running');

    if (!hasActivePolling) {
        console.debug('🧹 [Cleanup] No more active processes, continuing polling');
        // Keep polling active - no need to stop
    }
}

// LEGACY FUNCTION: Keep for backward compatibility, but now uses global polling
function startModalDownloadPolling(playlistId) {
    const process = activeDownloadProcesses[playlistId];
    if (!process || !process.batchId) return;

    console.log(`🔄 [Legacy Polling] Starting polling for ${playlistId}, delegating to global poller`);

    // Clear any existing individual poller (cleanup)
    if (process.poller) {
        clearInterval(process.poller);
        process.poller = null;
    }

    // Mark process as running to be picked up by global poller
    process.status = 'running';

    // Start global polling if not already running
    startGlobalDownloadPolling();

    // Create dummy poller for backward compatibility with cleanup functions
    ensureLegacyCompatibility(playlistId);
}

// For backward compatibility with cleanup functions that expect process.poller
// Creates a dummy poller that will be cleaned up by the existing cleanup logic
function createLegacyPoller(playlistId) {
    const process = activeDownloadProcesses[playlistId];
    if (!process) return;

    // Create a dummy interval that just checks if the process is still active
    // This ensures existing cleanup logic that calls clearInterval(process.poller) works
    process.poller = setInterval(() => {
        // This dummy poller doesn't do anything - global poller handles updates
        if (!activeDownloadProcesses[playlistId] || process.status === 'complete') {
            clearInterval(process.poller);
            process.poller = null;
            return;
        }
    }, 5000); // Very infrequent check, just for cleanup compatibility
}

// Call this to create the legacy poller after starting global polling
function ensureLegacyCompatibility(playlistId) {
    const process = activeDownloadProcesses[playlistId];
    if (process && !process.poller) {
        createLegacyPoller(playlistId);
    }
}
async function updateModalWithLiveDownloadProgress() {
    try {
        if (!currentDownloadBatchId) return;

        // Fetch live download data from the downloads API
        const response = await fetch('/api/downloads/status');
        const downloadData = await response.json();

        if (downloadData.error) return;

        // Get all active and finished downloads
        const allDownloads = { ...(downloadData.active || {}), ...(downloadData.finished || {}) };

        // Update modal tracks that have active downloads
        const modalRows = document.querySelectorAll('.download-missing-modal tr[data-track-index]');

        for (const row of modalRows) {
            const taskId = row.dataset.taskId;
            if (!taskId) continue;

            // Find corresponding download by checking if filename/title matches
            const trackName = row.querySelector('.track-name')?.textContent?.trim();
            if (!trackName) continue;

            // Search for matching download
            for (const [downloadId, downloadInfo] of Object.entries(allDownloads)) {
                // Extract display title from filename (handle YouTube encoding)
                let downloadTitle = '';
                if (downloadInfo.filename) {
                    if ((downloadInfo.username === 'youtube' || downloadInfo.username === 'tidal' || downloadInfo.username === 'qobuz' || downloadInfo.username === 'hifi') && downloadInfo.filename.includes('||')) {
                        const parts = downloadInfo.filename.split('||');
                        downloadTitle = parts[1] || parts[0];
                    } else {
                        downloadTitle = downloadInfo.filename.split(/[\\/]/).pop();
                    }
                }

                // Simple matching - could be improved with better logic
                if (downloadTitle && trackName && (
                    downloadTitle.toLowerCase().includes(trackName.toLowerCase()) ||
                    trackName.toLowerCase().includes(downloadTitle.toLowerCase())
                )) {
                    // Update the track with live download progress
                    const statusElement = row.querySelector('.track-download-status');
                    const progress = downloadInfo.percentComplete || 0;
                    const state = downloadInfo.state || '';

                    if (statusElement && state.includes('InProgress') && progress > 0) {
                        statusElement.textContent = `⏬ Downloading... ${Math.round(progress)}%`;
                        statusElement.className = 'track-download-status download-downloading';
                    } else if (statusElement && (state.includes('Completed') || state.includes('Succeeded'))) {
                        statusElement.textContent = '✅ Completed';
                        statusElement.className = 'track-download-status download-complete';
                    }

                    break; // Found a match, stop searching
                }
            }
        }

    } catch (error) {
        // Silent fail - don't spam console during normal operation
    }
}

function toggleAllTrackSelections(playlistId, checked) {
    const tbody = document.getElementById(`download-tracks-tbody-${playlistId}`);
    if (!tbody) return;
    const checkboxes = tbody.querySelectorAll('.track-select-cb');
    checkboxes.forEach(cb => { cb.checked = checked; });
    updateTrackSelectionCount(playlistId);
}

function updateTrackSelectionCount(playlistId) {
    const tbody = document.getElementById(`download-tracks-tbody-${playlistId}`);
    if (!tbody) return;
    const allCbs = tbody.querySelectorAll('.track-select-cb');
    const checkedCbs = tbody.querySelectorAll('.track-select-cb:checked');
    const total = allCbs.length;
    const selected = checkedCbs.length;

    // Update selection count label
    const countLabel = document.getElementById(`track-selection-count-${playlistId}`);
    if (countLabel) {
        countLabel.textContent = `${selected} / ${total} tracks selected`;
    }

    // Update select-all checkbox state
    const selectAll = document.getElementById(`select-all-${playlistId}`);
    if (selectAll) {
        selectAll.checked = selected === total;
        selectAll.indeterminate = selected > 0 && selected < total;
    }

    // Update row dimming
    allCbs.forEach(cb => {
        const row = cb.closest('tr');
        if (row) row.classList.toggle('track-deselected', !cb.checked);
    });

    // Disable Begin Analysis and Add to Wishlist buttons when 0 selected
    const beginBtn = document.getElementById(`begin-analysis-btn-${playlistId}`);
    if (beginBtn) {
        beginBtn.disabled = selected === 0;
    }
    const wishlistBtn = document.getElementById(`add-to-wishlist-btn-${playlistId}`);
    if (wishlistBtn) {
        wishlistBtn.disabled = selected === 0;
    }
}

async function cancelAllOperations(playlistId) {
    const process = activeDownloadProcesses[playlistId];
    if (!process) return;

    // Prevent multiple cancel all operations
    if (process.cancellingAll) {
        console.log(`⚠️ Cancel All already in progress for ${playlistId}`);
        return;
    }
    process.cancellingAll = true;

    console.log(`🚫 Cancel All clicked for playlist ${playlistId} - closing modal and cleaning up server`);

    showToast('Cancelling all operations and closing modal...', 'info');

    // Mark process as complete immediately so polling stops
    process.status = 'complete';

    // Stop any active polling
    if (process.poller) {
        clearInterval(process.poller);
        process.poller = null;
    }

    // Tell server to stop starting new downloads and clean up the batch
    if (process.batchId) {
        try {
            // Cancel the batch (stops new downloads from starting)
            const cancelResponse = await fetch(`/api/playlists/${process.batchId}/cancel_batch`, {
                method: 'POST'
            });
            if (cancelResponse.ok) {
                const cancelData = await cancelResponse.json();
                console.log(`✅ Server stopped new downloads for batch ${process.batchId}`);
            }
        } catch (error) {
            console.warn('Error during server batch cancel:', error);
        }
    }

    // Close the modal immediately - this will handle cleanup
    closeDownloadMissingModal(playlistId);

    showToast('Modal closed. Active downloads will finish in background.', 'success');
}

function resetToInitialState() {
    // Reset UI
    document.getElementById('begin-analysis-btn').style.display = 'inline-block';
    document.getElementById('start-downloads-btn').style.display = 'none';
    document.getElementById('cancel-all-btn').style.display = 'none';

    // Reset progress bars
    document.getElementById('analysis-progress-fill').style.width = '0%';
    document.getElementById('download-progress-fill').style.width = '0%';
    document.getElementById('analysis-progress-text').textContent = 'Ready to start';
    document.getElementById('download-progress-text').textContent = 'Waiting for analysis';

    // Reset stats
    document.getElementById('stat-found').textContent = '-';
    document.getElementById('stat-missing').textContent = '-';
    document.getElementById('stat-downloaded').textContent = '0';

    // Reset track table
    const tbody = document.getElementById('download-tracks-tbody');
    if (tbody) {
        const rows = tbody.querySelectorAll('tr');
        rows.forEach((row, index) => {
            const matchElement = row.querySelector('.track-match-status');
            const downloadElement = row.querySelector('.track-download-status');
            const actionsElement = row.querySelector('.track-actions');

            if (matchElement) {
                matchElement.textContent = '🔍 Pending';
                matchElement.className = 'track-match-status match-checking';
            }
            if (downloadElement) {
                downloadElement.textContent = '-';
                downloadElement.className = 'track-download-status';
            }
            if (actionsElement) {
                actionsElement.textContent = '-';
            }
        });
    }

    // Reset state
    activeAnalysisTaskId = null;
    analysisResults = [];
    missingTracks = [];
}

// ===============================
// NEW ATOMIC CANCEL SYSTEM V2
// ===============================

async function cancelTrackDownloadV2(playlistId, trackIndex) {
    /**
     * NEW ATOMIC CANCEL SYSTEM V2
     * 
     * - No optimistic UI updates
     * - Single API call handles everything atomically
     * - Backend is single source of truth for all state
     * - No race conditions or dual state management
     */
    const process = activeDownloadProcesses[playlistId];
    if (!process) {
        console.warn(`❌ [Cancel V2] No process found for playlist: ${playlistId}`);
        return;
    }

    const row = document.querySelector(`#download-missing-modal-${CSS.escape(playlistId)} tr[data-track-index="${trackIndex}"]`);
    if (!row) {
        console.warn(`❌ [Cancel V2] No row found for track index: ${trackIndex}`);
        return;
    }

    // Check if already in cancelling state
    const statusEl = document.getElementById(`download-${playlistId}-${trackIndex}`);
    const currentStatus = statusEl ? statusEl.textContent : '';

    if (currentStatus.includes('Cancelling') || currentStatus.includes('Cancelled')) {
        console.log(`⚠️ [Cancel V2] Task already being cancelled or cancelled: ${currentStatus}`);
        return;
    }

    console.log(`🎯 [Cancel V2] Starting atomic cancel: playlist=${playlistId}, track=${trackIndex}`);

    // V2 SYSTEM: Set temporary UI state - will be confirmed by server
    row.dataset.uiState = 'cancelling';

    // Show loading state only - no optimistic "cancelled" state
    if (statusEl) {
        statusEl.textContent = '🔄 Cancelling...';
    }

    // Disable the cancel button to prevent double-clicks
    const actionsEl = document.getElementById(`actions-${playlistId}-${trackIndex}`);
    if (actionsEl) {
        actionsEl.innerHTML = '<span style="color: #666;">Cancelling...</span>';
    }

    try {
        const response = await fetch('/api/downloads/cancel_task_v2', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                playlist_id: playlistId,
                track_index: trackIndex
            })
        });

        const data = await response.json();

        if (data.success) {
            console.log(`✅ [Cancel V2] Successfully cancelled: ${data.task_info.track_name}`);
            showToast(`Cancelled "${data.task_info.track_name}" and added to wishlist.`, 'success');

            // Let the status polling system update the UI with server truth
            // No manual UI updates - backend is authoritative

        } else {
            console.error(`❌ [Cancel V2] Cancel failed: ${data.error}`);
            showToast(`Cancel failed: ${data.error}`, 'error');

            // Reset UI to previous state on failure
            row.dataset.uiState = 'normal'; // Reset UI state
            if (statusEl) {
                statusEl.textContent = '❌ Cancel Failed';
            }
            if (actionsEl) {
                actionsEl.innerHTML = `<button class="cancel-track-btn" title="Cancel this download" onclick="cancelTrackDownloadV2('${playlistId}', ${trackIndex})">×</button>`;
            }
        }

    } catch (error) {
        console.error(`❌ [Cancel V2] Network/API error:`, error);
        showToast(`Cancel request failed: ${error.message}`, 'error');

        // Reset UI on network error
        row.dataset.uiState = 'normal'; // Reset UI state
        if (statusEl) {
            statusEl.textContent = '❌ Cancel Failed';
        }
        if (actionsEl) {
            actionsEl.innerHTML = `<button class="cancel-track-btn" title="Cancel this download" onclick="cancelTrackDownloadV2('${playlistId}', ${trackIndex})">×</button>`;
        }
    }
}

// ===============================
// LEGACY CANCEL SYSTEM (OLD)
// ===============================

async function cancelTrackDownload(playlistId, trackIndex) {
    const process = activeDownloadProcesses[playlistId];
    if (!process) return;

    const row = document.querySelector(`#download-missing-modal-${CSS.escape(playlistId)} tr[data-track-index="${trackIndex}"]`);
    if (!row) return;

    // Prevent double cancellation
    if (row.dataset.locallyCancelled === 'true') {
        return; // Already cancelled locally
    }

    const taskId = row.dataset.taskId;
    if (!taskId) {
        showToast('Task not started yet, cannot cancel.', 'warning');
        return;
    }

    // UI update for immediate feedback - mark as cancelled FIRST to prevent race conditions
    row.dataset.locallyCancelled = 'true';
    document.getElementById(`download-${playlistId}-${trackIndex}`).textContent = '🚫 Cancelling...';
    document.getElementById(`actions-${playlistId}-${trackIndex}`).innerHTML = '-';

    try {
        const response = await fetch('/api/downloads/cancel_task', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ task_id: taskId })
        });
        const data = await response.json();
        if (data.success) {
            // Update final UI state after successful cancellation
            document.getElementById(`download-${playlistId}-${trackIndex}`).textContent = '🚫 Cancelled';
            showToast('Download cancelled and added to wishlist.', 'info');
        } else {
            throw new Error(data.error);
        }
    } catch (error) {
        // Reset UI state if cancellation failed
        row.dataset.locallyCancelled = 'false';
        document.getElementById(`download-${playlistId}-${trackIndex}`).textContent = '❌ Cancel Failed';
        showToast(`Could not cancel task: ${error.message}`, 'error');
    }
}

// Find and REPLACE the old startPlaylistSyncFromModal function
async function startPlaylistSync(playlistId) {
    const startTime = Date.now();
    console.log(`🚀 [${new Date().toTimeString().split(' ')[0]}] Starting sync for playlist: ${playlistId}`);
    const playlist = spotifyPlaylists.find(p => p.id === playlistId);
    if (!playlist) {
        console.error(`❌ Could not find playlist data for ID: ${playlistId}`);
        showToast('Could not find playlist data.', 'error');
        return;
    }
    console.log(`✅ Found playlist: ${playlist.name} with ${playlist.track_count || 'unknown'} tracks`);

    // Check if already syncing to prevent duplicate syncs
    if (activeSyncPollers[playlistId]) {
        showToast('Sync already in progress for this playlist', 'warning');
        return;
    }

    // Update button state immediately for user feedback
    const syncBtn = document.getElementById(`sync-btn-${playlistId}`);
    if (syncBtn) {
        syncBtn.disabled = true;
        syncBtn.textContent = '⏳ Syncing...';
    }

    // Ensure we have the full track list before starting
    let tracks = playlistTrackCache[playlistId];
    if (!tracks) {
        const trackFetchStart = Date.now();
        console.log(`🔄 [${new Date().toTimeString().split(' ')[0]}] Cache miss - fetching tracks for playlist ${playlistId}`);
        try {
            // Use the right endpoint based on playlist source
            const fetchUrl = playlistId.startsWith('deezer_arl_')
                ? `/api/deezer/arl-playlist/${playlistId.replace('deezer_arl_', '')}`
                : `/api/spotify/playlist/${playlistId}`;
            const response = await fetch(fetchUrl);
            const fullPlaylist = await response.json();
            if (fullPlaylist.error) throw new Error(fullPlaylist.error);
            tracks = fullPlaylist.tracks;
            playlistTrackCache[playlistId] = tracks; // Cache it
            const trackFetchTime = Date.now() - trackFetchStart;
            console.log(`✅ [${new Date().toTimeString().split(' ')[0]}] Fetched and cached ${tracks.length} tracks (took ${trackFetchTime}ms)`);
        } catch (error) {
            console.error(`❌ Failed to fetch tracks:`, error);
            showToast(`Failed to fetch tracks for sync: ${error.message}`, 'error');
            return;
        }
    } else {
        console.log(`✅ [${new Date().toTimeString().split(' ')[0]}] Using cached tracks: ${tracks.length} tracks`);
    }

    // DON'T close the modal - let it show live progress like the GUI

    try {
        const syncStartTime = Date.now();
        console.log(`🔄 [${new Date().toTimeString().split(' ')[0]}] Making API call to /api/sync/start with ${tracks.length} tracks`);
        const response = await fetch('/api/sync/start', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                playlist_id: playlist.id,
                playlist_name: playlist.name,
                tracks: tracks, // Send the full track list
                image_url: playlist.image_url || ''
            })
        });

        const syncRequestTime = Date.now() - syncStartTime;
        console.log(`📡 [${new Date().toTimeString().split(' ')[0]}] API response status: ${response.status} (took ${syncRequestTime}ms)`);
        const data = await response.json();
        console.log(`📡 [${new Date().toTimeString().split(' ')[0]}] API response data:`, data);

        if (!data.success) throw new Error(data.error);

        const totalTime = Date.now() - startTime;
        console.log(`✅ [${new Date().toTimeString().split(' ')[0]}] Sync started successfully for "${playlist.name}" (total time: ${totalTime}ms)`);
        showToast(`Sync started for "${playlist.name}"`, 'success');

        // Show initial sync state in modal if open
        const modal = document.getElementById('playlist-details-modal') || document.getElementById('deezer-arl-playlist-details-modal');
        if (modal && modal.style.display !== 'none') {
            const statusDisplay = document.getElementById(`modal-sync-status-${playlist.id}`);
            if (statusDisplay) {
                statusDisplay.style.display = 'flex';
                console.log(`📊 [${new Date().toTimeString().split(' ')[0]}] Showing modal sync status for ${playlist.id}`);
            }
        }

        updateCardToSyncing(playlist.id, 0); // Initial state
        startSyncPolling(playlist.id);

    } catch (error) {
        console.error(`❌ Failed to start sync:`, error);
        showToast(`Failed to start sync: ${error.message}`, 'error');
        updateCardToDefault(playlist.id);
    }
}

// Add these new helper functions to script.js

function startSyncPolling(playlistId) {
    // Clear any existing poller for this playlist
    if (activeSyncPollers[playlistId]) {
        clearInterval(activeSyncPollers[playlistId]);
    }

    // Phase 5: Subscribe via WebSocket
    if (socketConnected) {
        socket.emit('sync:subscribe', { playlist_ids: [playlistId] });
        _syncProgressCallbacks[playlistId] = (data) => {
            if (data.status === 'syncing') {
                const progress = data.progress;
                updateCardToSyncing(playlistId, progress.progress, progress);
                updateModalSyncProgress(playlistId, progress);
            } else if (data.status === 'finished' || data.status === 'error' || data.status === 'cancelled') {
                stopSyncPolling(playlistId);
                updateCardToDefault(playlistId, data);
                closePlaylistDetailsModal();
            }
        };
    }

    // Start a new poller that checks every 2 seconds
    console.log(`🔄 Starting sync polling for playlist: ${playlistId}`);
    activeSyncPollers[playlistId] = setInterval(async () => {
        // Always poll — no dedicated WebSocket events for discovery progress
        try {
            console.log(`📊 Polling sync status for: ${playlistId}`);
            const response = await fetch(`/api/sync/status/${playlistId}`);
            const state = await response.json();
            console.log(`📊 Poll response:`, state);

            if (state.status === 'syncing') {
                const progress = state.progress;
                console.log(`📊 Sync progress:`, progress);
                console.log(`   📊 Progress values: ${progress.progress}% | Total: ${progress.total_tracks} | Matched: ${progress.matched_tracks} | Failed: ${progress.failed_tracks}`);
                console.log(`   📊 Current step: "${progress.current_step}" | Current track: "${progress.current_track}"`);

                // Use the actual progress percentage from the sync service
                updateCardToSyncing(playlistId, progress.progress, progress);
                // Also update the modal if it's open
                updateModalSyncProgress(playlistId, progress);
            } else if (state.status === 'finished' || state.status === 'error' || state.status === 'cancelled') {
                console.log(`🏁 Sync completed with status: ${state.status}`);
                stopSyncPolling(playlistId);
                updateCardToDefault(playlistId, state);
                // Also update the modal if it's open
                closePlaylistDetailsModal(); closeDeezerArlPlaylistDetailsModal(); // Close modal on completion/error
            }
        } catch (error) {
            console.error(`❌ Error polling sync status for ${playlistId}:`, error);
            stopSyncPolling(playlistId);
            updateCardToDefault(playlistId, { status: 'error', error: 'Polling failed' });
        }
    }, 2000); // Poll every 2 seconds
    updateRefreshButtonState();
}

function stopSyncPolling(playlistId) {
    if (activeSyncPollers[playlistId]) {
        clearInterval(activeSyncPollers[playlistId]);
        delete activeSyncPollers[playlistId];
    }
    // Phase 5: Unsubscribe and clean up callback
    if (_syncProgressCallbacks[playlistId]) {
        if (socketConnected) socket.emit('sync:unsubscribe', { playlist_ids: [playlistId] });
        delete _syncProgressCallbacks[playlistId];
    }
    updateRefreshButtonState();
}

// Sync sidebar visibility helpers
function showSyncSidebar() {
    const sidebar = document.querySelector('.sync-sidebar');
    const contentArea = document.querySelector('.sync-content-area');
    if (sidebar && contentArea && window.innerWidth > 1300) {
        sidebar.style.display = '';
        contentArea.style.gridTemplateColumns = '2.5fr 0.75fr';
    }
}

function hideSyncSidebar() {
    const sidebar = document.querySelector('.sync-sidebar');
    const contentArea = document.querySelector('.sync-content-area');
    if (sidebar && contentArea) {
        sidebar.style.display = 'none';
        contentArea.style.gridTemplateColumns = '1fr';
    }
}

// Sequential Sync Functions
function startSequentialSync() {
    // Initialize manager if needed
    if (!sequentialSyncManager) {
        sequentialSyncManager = new SequentialSyncManager();
    }

    // Check if already running - if so, cancel
    if (sequentialSyncManager.isRunning) {
        sequentialSyncManager.cancel();
        return;
    }

    // Validate selection
    if (selectedPlaylists.size === 0) {
        showToast('No playlists selected for sync', 'error');
        return;
    }

    // Get playlist order from DOM to maintain display order
    const playlistCards = document.querySelectorAll('.playlist-card');
    const orderedPlaylistIds = [];

    playlistCards.forEach(card => {
        const playlistId = card.dataset.playlistId;
        if (selectedPlaylists.has(playlistId)) {
            orderedPlaylistIds.push(playlistId);
        }
    });

    console.log(`🚀 Starting sequential sync for ${orderedPlaylistIds.length} playlists`);

    // Show sidebar for sync progress
    showSyncSidebar();

    // Start sequential sync
    sequentialSyncManager.start(orderedPlaylistIds);

    // Disable playlist selection during sync
    disablePlaylistSelection(true);
}

function disablePlaylistSelection(disabled) {
    const checkboxes = document.querySelectorAll('.playlist-checkbox');
    checkboxes.forEach(checkbox => {
        checkbox.disabled = disabled;
    });
}

function hasActiveOperations() {
    const hasActiveSyncs = Object.keys(activeSyncPollers).length > 0;
    // Only check non-wishlist download processes for sync page refresh button
    const hasActiveDownloads = Object.entries(activeDownloadProcesses)
        .filter(([playlistId, process]) => playlistId !== 'wishlist') // Exclude wishlist
        .some(([_, process]) => process.status === 'running');
    const hasSequentialSync = sequentialSyncManager && sequentialSyncManager.isRunning;
    return hasActiveSyncs || hasActiveDownloads || hasSequentialSync;
}


function updateRefreshButtonState() {
    const refreshBtn = document.getElementById('spotify-refresh-btn');
    if (!refreshBtn) return;

    if (hasActiveOperations()) {
        refreshBtn.disabled = true;
        // Provide context-specific text
        const hasActiveSyncs = Object.keys(activeSyncPollers).length > 0;
        const hasSequentialSync = sequentialSyncManager && sequentialSyncManager.isRunning;
        if (hasActiveSyncs || hasSequentialSync) {
            refreshBtn.textContent = '🔄 Syncing...';
        } else {
            refreshBtn.textContent = '📥 Downloading...';
        }
    } else {
        refreshBtn.disabled = false;
        refreshBtn.textContent = '🔄 Refresh';
    }
}

function updateCardToSyncing(playlistId, percent, progress = null) {
    const card = document.querySelector(`.playlist-card[data-playlist-id="${playlistId}"]`);
    if (!card) return;

    const progressBar = card.querySelector('.sync-progress-indicator');
    progressBar.style.display = 'block';

    let progressText = 'Starting...';
    let actualPercent = percent || 0;

    if (progress) {
        // Create detailed progress text like the GUI
        const matched = progress.matched_tracks || 0;
        const failed = progress.failed_tracks || 0;
        const total = progress.total_tracks || 0;
        const currentStep = progress.current_step || 'Processing';

        // Calculate actual progress as processed/total, not just successful/total
        if (total > 0) {
            const processed = matched + failed;
            actualPercent = Math.round((processed / total) * 100);
            progressText = `${currentStep}: ${processed}/${total} (${matched} matched, ${failed} failed)`;
        } else {
            progressText = currentStep;
        }

        // If there's a current track being processed, show it
        if (progress.current_track) {
            progressText += ` - ${progress.current_track}`;
        }
    }

    // Build live status counter HTML (same as modal)
    let statusCounterHTML = '';
    if (progress && progress.total_tracks > 0) {
        const matched = progress.matched_tracks || 0;
        const failed = progress.failed_tracks || 0;
        const total = progress.total_tracks || 0;
        const processed = matched + failed;
        const percentage = total > 0 ? Math.round((processed / total) * 100) : 0;

        statusCounterHTML = `
            <div class="playlist-card-sync-status">
                <span class="sync-stat total-tracks">♪ ${total}</span>
                <span class="sync-separator">/</span>
                <span class="sync-stat matched-tracks">✓ ${matched}</span>
                <span class="sync-separator">/</span>
                <span class="sync-stat failed-tracks">✗ ${failed}</span>
                <span class="sync-stat percentage">(${percentage}%)</span>
            </div>
        `;
    }

    progressBar.innerHTML = `
        ${statusCounterHTML}
        <div class="progress-bar-sync">
            <div class="progress-fill-sync" style="width: ${actualPercent}%;"></div>
        </div>
        <div class="progress-text-sync">${progressText}</div>
    `;
}

function updateCardToDefault(playlistId, finalState = null) {
    const card = document.querySelector(`.playlist-card[data-playlist-id="${playlistId}"]`);
    if (!card) return;

    const progressBar = card.querySelector('.sync-progress-indicator');
    progressBar.style.display = 'none';
    progressBar.innerHTML = '';

    const statusEl = card.querySelector('.playlist-card-status');
    if (finalState) {
        if (finalState.status === 'finished') {
            statusEl.textContent = `Synced: Just now`;
            statusEl.className = 'playlist-card-status status-synced';

            // Check if any tracks were added to wishlist
            const wishlistCount = finalState.progress?.wishlist_added_count || finalState.result?.wishlist_added_count || 0;
            const unmatchedTracks = finalState.progress?.unmatched_tracks || finalState.result?.unmatched_tracks || [];
            const playlistName = card.querySelector('.playlist-card-name').textContent;

            if (wishlistCount > 0 && unmatchedTracks.length > 0) {
                const trackList = unmatchedTracks.map(t => `${t.artist} - ${t.name}`).join(', ');
                showToast(`Sync complete for "${playlistName}". ${wishlistCount} not found in library: ${trackList}`, 'warning');
            } else if (wishlistCount > 0) {
                showToast(`Sync complete for "${playlistName}". Added ${wishlistCount} missing track${wishlistCount > 1 ? 's' : ''} to wishlist.`, 'success');
            } else {
                showToast(`Sync complete for "${playlistName}"`, 'success');
            }
        } else {
            statusEl.textContent = `Sync Failed`;
            statusEl.className = 'playlist-card-status status-needs-sync'; // Or a new error class
            showToast(`Sync failed: ${finalState.error || 'Unknown error'}`, 'error');
        }
    }
}

// Update the modal's sync progress display (matches GUI functionality)
function updateModalSyncProgress(playlistId, progress) {
    const modal = document.getElementById('playlist-details-modal') || document.getElementById('deezer-arl-playlist-details-modal');
    if (modal && modal.style.display !== 'none') {
        console.log(`📊 Updating modal sync progress for ${playlistId}:`, progress);

        // Show sync status display
        const statusDisplay = document.getElementById(`modal-sync-status-${playlistId}`);
        if (statusDisplay) {
            statusDisplay.style.display = 'flex';

            // Update counters (matching GUI exactly)
            const totalEl = document.getElementById(`modal-total-${playlistId}`);
            const matchedEl = document.getElementById(`modal-matched-${playlistId}`);
            const failedEl = document.getElementById(`modal-failed-${playlistId}`);
            const percentageEl = document.getElementById(`modal-percentage-${playlistId}`);

            const total = progress.total_tracks || 0;
            const matched = progress.matched_tracks || 0;
            const failed = progress.failed_tracks || 0;

            if (totalEl) totalEl.textContent = total;
            if (matchedEl) matchedEl.textContent = matched;
            if (failedEl) failedEl.textContent = failed;

            // Calculate percentage like GUI
            if (total > 0) {
                const processed = matched + failed;
                const percentage = Math.round((processed / total) * 100);
                if (percentageEl) percentageEl.textContent = percentage;
            }

            console.log(`📊 Modal updated: ♪ ${total} / ✓ ${matched} / ✗ ${failed} (${Math.round((matched + failed) / total * 100)}%)`);
        } else {
            console.warn(`❌ Modal sync status display not found for ${playlistId}`);
        }
    } else {
        console.log(`📊 Modal not open for ${playlistId}, skipping update`);
    }
}


// Raw Soulseek file search (used by the 'Soulseek (raw files)' source picker option).
async function performDownloadsSearch() {
    const query = document.getElementById('downloads-search-input').value.trim();
    if (!query) {
        showToast('Please enter a search term', 'error');
        return;
    }

    // --- UI Element References ---
    const searchInput = document.getElementById('downloads-search-input');
    const searchButton = document.getElementById('downloads-search-btn');
    const cancelButton = document.getElementById('downloads-cancel-btn');
    const statusText = document.getElementById('search-status-text');
    const spinner = document.querySelector('.spinner-animation');
    const dots = document.querySelector('.dots-animation');

    // --- Start a new AbortController for this search ---
    searchAbortController = new AbortController();

    try {
        // --- 1. Update UI to "Searching" State ---
        searchInput.disabled = true;
        searchButton.disabled = true;
        cancelButton.classList.remove('hidden');
        spinner.classList.remove('hidden');
        dots.classList.remove('hidden');
        statusText.textContent = `Searching for '${query}'...`;
        displayDownloadsResults([]); // Clear previous results

        // --- 2. Perform the Fetch Request ---
        const response = await fetch('/api/search', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ query }),
            signal: searchAbortController.signal // Link fetch to the AbortController
        });

        const data = await response.json();

        if (data.error) {
            throw new Error(data.error);
        }

        const results = data.results || [];
        allSearchResults = results;
        resetFilters();
        applyFiltersAndSort();

        // --- 3. Update UI with Success State ---
        if (results.length === 0) {
            statusText.textContent = `No results found for '${query}'`;
            showToast('No results found', 'error');
        } else {
            document.getElementById('filters-container').classList.remove('hidden');

            // Count albums and singles like the GUI app
            let totalAlbums = 0;
            let totalTracks = 0;

            results.forEach(result => {
                if (result.result_type === 'album') {
                    totalAlbums++;
                } else {
                    totalTracks++;
                }
            });

            statusText.textContent = `✨ Found ${results.length} results • ${totalAlbums} albums, ${totalTracks} singles`;
            showToast(`Found ${results.length} results`, 'success');
        }

    } catch (error) {
        // --- 4. Handle Errors, Including Cancellation ---
        if (error.name === 'AbortError') {
            // This specific error is thrown when the user clicks "Cancel"
            statusText.textContent = 'Search was cancelled.';
            showToast('Search cancelled', 'info');
            displayDownloadsResults([]); // Clear any partial results
        } else {
            console.error('Search failed:', error);
            statusText.textContent = `Search failed: ${error.message}`;
            showToast('Search failed', 'error');
        }
    } finally {
        // --- 5. Clean Up UI Regardless of Outcome ---
        searchInput.disabled = false;
        searchButton.disabled = false;
        cancelButton.classList.add('hidden');
        spinner.classList.add('hidden');
        dots.classList.add('hidden');
        searchAbortController = null; // Clear the controller
    }
}

function displayDownloadsResults(results) {
    const resultsArea = document.getElementById('search-results-area');
    if (!resultsArea) return;

    if (!results.length) {
        resultsArea.innerHTML = '<div class="search-results-placeholder"><p>No search results found.</p></div>';
        return;
    }

    let html = '';
    results.forEach((result, index) => {
        const isAlbum = result.result_type === 'album';

        if (isAlbum) {
            const trackCount = result.tracks ? result.tracks.length : 0;
            const totalSize = result.total_size ? `${(result.total_size / 1024 / 1024).toFixed(1)} MB` : 'Unknown size';

            // Generate individual track items
            let trackListHtml = '';
            if (result.tracks && result.tracks.length > 0) {
                // Detect disc boundaries from track number resets for multi-disc albums
                let currentDisc = 1;
                let lastTrackNum = 0;
                let discBreaks = new Set();
                result.tracks.forEach((track, trackIndex) => {
                    const tn = track.track_number || 0;
                    if (trackIndex > 0 && tn > 0 && tn <= lastTrackNum) {
                        currentDisc++;
                        discBreaks.add(trackIndex);
                    }
                    if (tn > 0) lastTrackNum = tn;
                });
                const isMultiDisc = discBreaks.size > 0;
                if (isMultiDisc) {
                    trackListHtml += `<div class="disc-separator" style="padding: 6px 12px; font-weight: 600; font-size: 0.85em; color: var(--text-secondary, #aaa); border-bottom: 1px solid var(--border-color, #333); margin-bottom: 4px;">Disc 1</div>`;
                }
                let discNum = 1;
                result.tracks.forEach((track, trackIndex) => {
                    if (discBreaks.has(trackIndex)) {
                        discNum++;
                        trackListHtml += `<div class="disc-separator" style="padding: 6px 12px; font-weight: 600; font-size: 0.85em; color: var(--text-secondary, #aaa); border-bottom: 1px solid var(--border-color, #333); margin: 8px 0 4px 0;">Disc ${discNum}</div>`;
                    }
                    const trackSize = track.size ? `${(track.size / 1024 / 1024).toFixed(1)} MB` : 'Unknown size';
                    const trackBitrate = track.bitrate ? `${track.bitrate}kbps` : '';
                    trackListHtml += `
                        <div class="track-item">
                            <div class="track-item-info">
                                <div class="track-item-title">${escapeHtml(track.title || `Track ${trackIndex + 1}`)}</div>
                                <div class="track-item-details">
                                    ${track.track_number ? `${track.track_number}. ` : ''}${escapeHtml(track.artist || result.artist || 'Unknown Artist')} • ${trackSize} • ${escapeHtml(track.quality || 'Unknown')} ${trackBitrate}
                                </div>
                            </div>
                            <div class="track-item-actions">
                                <button onclick="streamAlbumTrack(${index}, ${trackIndex})" class="track-stream-btn">Stream ▶</button>
                                <button onclick="downloadAlbumTrack(${index}, ${trackIndex})" class="track-download-btn">Download ⬇</button>
                                <button onclick="matchedDownloadAlbumTrack(${index}, ${trackIndex})" class="track-matched-btn" title="Matched Download">Matched Download 🎯</button>
                            </div>
                        </div>
                    `;
                });
            }

            html += `
                <div class="album-result-card" data-album-index="${index}">
                    <div class="album-card-header" onclick="toggleAlbumExpansion(${index})">
                        <div class="album-expand-indicator">▶</div>
                        <div class="album-icon">💿</div>
                        <div class="album-info">
                            <div class="album-title">${escapeHtml(result.album_title || result.title || 'Unknown Album')}</div>
                            <div class="album-artist">by ${escapeHtml(result.artist || 'Unknown Artist')}</div>
                            <div class="album-details">
                                ${trackCount} tracks • ${totalSize} • ${escapeHtml(result.quality || 'Mixed')}
                            </div>
                            <div class="album-uploader">Shared by ${escapeHtml(result.username || 'Unknown')}</div>
                        </div>
                        <div class="album-actions" onclick="event.stopPropagation()">
                            <button onclick="downloadAlbum(${index})" class="album-download-btn">⬇ Download Album</button>
                            <button onclick="matchedDownloadAlbum(${index})" class="album-matched-btn" title="Matched Album Download">Matched Album🎯</button>
                        </div>
                    </div>
                    <div class="album-track-list" style="display: none;">
                        ${trackListHtml}
                    </div>
                </div>
            `;
        } else {
            const sizeText = result.size ? `${(result.size / 1024 / 1024).toFixed(1)} MB` : 'Unknown size';
            const bitrateText = result.bitrate ? `${result.bitrate}kbps` : '';
            html += `
                <div class="track-result-card">
                    <div class="track-icon">🎵</div>
                    <div class="track-info">
                        <div class="track-title">${escapeHtml(result.title || 'Unknown Title')}</div>
                        <div class="track-artist">by ${escapeHtml(result.artist || 'Unknown Artist')}</div>
                        <div class="track-details">
                            ${sizeText} • ${escapeHtml(result.quality || 'Unknown')} ${bitrateText}
                        </div>
                        <div class="track-uploader">Shared by ${escapeHtml(result.username || 'Unknown')}</div>
                    </div>
                    <div class="track-actions">
                        <button onclick="streamTrack(${index})" class="track-stream-btn" title="Stream Track">Stream ▶</button>
                        <button onclick="downloadTrack(${index})" class="track-download-btn" title="Download">Download ⬇</button>
                        <button onclick="matchedDownloadTrack(${index})" class="track-matched-btn" title="Matched Download">Matched Download🎯</button>
                    </div>
                </div>
            `;
        }
    });

    resultsArea.innerHTML = html;
    // Store results globally for download functions
    window.currentSearchResults = results;
}

async function downloadTrack(index) {
    const results = window.currentSearchResults;
    if (!results || !results[index]) return;

    const track = results[index];

    try {
        const response = await fetch('/api/download', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(track)
        });

        const data = await response.json();

        if (data.success) {
            showToast(`Download started: ${track.title}`, 'success');
        } else {
            showToast(`Download failed: ${data.error}`, 'error');
        }
    } catch (error) {
        console.error('Download error:', error);
        showToast('Failed to start download', 'error');
    }
}

async function downloadAlbum(index) {
    const results = window.currentSearchResults;
    if (!results || !results[index]) return;

    const album = results[index];

    try {
        const response = await fetch('/api/download', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(album)
        });

        const data = await response.json();

        if (data.success) {
            showToast(data.message, 'success');
        } else {
            showToast(`Album download failed: ${data.error}`, 'error');
        }
    } catch (error) {
        console.error('Album download error:', error);
        showToast('Failed to start album download', 'error');
    }
}

// Matched download functions
function matchedDownloadTrack(index) {
    const results = window.currentSearchResults;
    if (!results || !results[index]) return;

    const track = results[index];
    console.log('🎯 Starting matched download for single track:', track);

    // Open matching modal for single track
    openMatchingModal(track, false, null);
}

function matchedDownloadAlbum(index) {
    const results = window.currentSearchResults;
    if (!results || !results[index]) return;

    const album = results[index];
    console.log('🎯 Starting matched download for album:', album);

    // Open matching modal for album download
    openMatchingModal(album, true, album);
}

function matchedDownloadAlbumTrack(albumIndex, trackIndex) {
    const results = window.currentSearchResults;
    if (!results || !results[albumIndex]) return;

    const album = results[albumIndex];
    if (!album.tracks || !album.tracks[trackIndex]) return;

    const track = album.tracks[trackIndex];

    // Ensure track has necessary properties from parent album
    track.username = album.username;
    track.artist = track.artist || album.artist;
    track.album = album.album_title || album.title;

    console.log('🎯 Starting matched download for album track:', track);

    // Open matching modal for single track (from album context)
    openMatchingModal(track, false, null);
}

function toggleAlbumExpansion(albumIndex) {
    const albumCard = document.querySelector(`[data-album-index="${albumIndex}"]`);
    if (!albumCard) return;

    const trackList = albumCard.querySelector('.album-track-list');
    const indicator = albumCard.querySelector('.album-expand-indicator');

    if (trackList.style.display === 'none' || !trackList.style.display) {
        // Expand
        trackList.style.display = 'block';
        indicator.textContent = '▼';
        albumCard.classList.add('expanded');
    } else {
        // Collapse
        trackList.style.display = 'none';
        indicator.textContent = '▶';
        albumCard.classList.remove('expanded');
    }
}

async function downloadAlbumTrack(albumIndex, trackIndex) {
    const results = window.currentSearchResults;
    if (!results || !results[albumIndex] || !results[albumIndex].tracks || !results[albumIndex].tracks[trackIndex]) return;

    const track = results[albumIndex].tracks[trackIndex];

    try {
        const response = await fetch('/api/download', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                ...track,
                result_type: 'track'
            })
        });

        const data = await response.json();

        if (data.success) {
            showToast(`Download started: ${track.title}`, 'success');
        } else {
            showToast(`Track download failed: ${data.error}`, 'error');
        }
    } catch (error) {
        console.error('Track download error:', error);
        showToast('Failed to start track download', 'error');
    }
}

// ===============================
// STREAMING WRAPPER FUNCTIONS
// ===============================

async function streamTrack(index) {
    // Stream a single track from search results
    try {
        console.log(`🎵 streamTrack called with index: ${index}`);
        console.log(`🎵 window.currentSearchResults:`, window.currentSearchResults);

        if (!window.currentSearchResults || !window.currentSearchResults[index]) {
            console.error(`❌ No search results or invalid index. Results length: ${window.currentSearchResults ? window.currentSearchResults.length : 'undefined'}`);
            showToast('Track not found', 'error');
            return;
        }

        const result = window.currentSearchResults[index];
        console.log(`🎵 Streaming track:`, result);

        // Check for unsupported formats before streaming (streaming sources use encoded filenames, skip check)
        const isStreamingSource = result.username === 'youtube' || result.username === 'tidal' || result.username === 'qobuz' || result.username === 'hifi';
        if (!isStreamingSource && result.filename) {
            const format = getFileExtension(result.filename);
            console.log(`🎵 [STREAM CHECK] File: ${result.filename}, Extension: ${format}`);

            const isSupported = isAudioFormatSupported(result.filename);
            console.log(`🎵 [STREAM CHECK] Format ${format} supported: ${isSupported}`);

            if (!isSupported) {
                showToast(`Sorry, ${format.toUpperCase()} format is not supported in your browser. Try downloading instead.`, 'error');
                return;
            }
        }

        await startStream(result);

    } catch (error) {
        console.error('Track streaming error:', error);
        showToast('Failed to start track stream', 'error');
    }
}


async function streamAlbumTrack(albumIndex, trackIndex) {
    // Stream a specific track from an album
    try {
        console.log(`🎵 streamAlbumTrack called with albumIndex: ${albumIndex}, trackIndex: ${trackIndex}`);
        console.log(`🎵 window.currentSearchResults:`, window.currentSearchResults);

        if (!window.currentSearchResults || !window.currentSearchResults[albumIndex]) {
            console.error(`❌ No search results or invalid album index. Results length: ${window.currentSearchResults ? window.currentSearchResults.length : 'undefined'}`);
            showToast('Album not found', 'error');
            return;
        }

        const album = window.currentSearchResults[albumIndex];
        console.log(`🎵 Album data:`, album);

        // Surgical Fix: Handle YouTube/Tidal results which are "flat" (no tracks array)
        if (album.username === 'youtube' || album.username === 'tidal' || album.username === 'qobuz' || album.username === 'hifi') {
            // For YouTube/Tidal results, the "album" is actually the track itself
            const track = album;
            const trackData = {
                ...track,
                username: track.username,
                filename: track.filename,
                artist: track.artist,
                album: track.title, // Use title as album name for player
                title: track.title
            };
            console.log(`🎵 Streaming YouTube track directly:`, trackData);
            await startStream(trackData);
            return;
        }

        if (!album.tracks || !album.tracks[trackIndex]) {
            console.error(`❌ No tracks in album or invalid track index. Tracks length: ${album.tracks ? album.tracks.length : 'undefined'}`);
            showToast('Track not found in album', 'error');
            return;
        }

        const track = album.tracks[trackIndex];
        console.log(`🎵 Streaming album track:`, track);

        // Ensure album tracks have required fields
        const trackData = {
            ...track,
            username: track.username || album.username,
            filename: track.filename || track.path,
            artist: track.artist || album.artist,
            album: track.album || album.title || album.album
        };

        console.log(`🎵 Enhanced track data:`, trackData);

        // Check for unsupported formats before streaming (streaming sources use encoded filenames, skip check)
        const isStreamingSource2 = trackData.username === 'youtube' || trackData.username === 'tidal' || trackData.username === 'qobuz' || trackData.username === 'hifi';
        if (!isStreamingSource2 && trackData.filename && !isAudioFormatSupported(trackData.filename)) {
            const format = getFileExtension(trackData.filename);
            showToast(`Sorry, ${format.toUpperCase()} format is not supported in web browsers. Try downloading instead.`, 'error');
            return;
        }

        await startStream(trackData);

    } catch (error) {
        console.error('Album track streaming error:', error);
        showToast('Failed to start track stream', 'error');
    }
}

async function loadArtistsData() {
    try {
        const response = await fetch(API.artists);
        const data = await response.json();

        const artistsGrid = document.getElementById('artists-grid');
        if (data.artists && data.artists.length) {
            artistsGrid.innerHTML = data.artists.map(artist => `
                <div class="artist-card">
                    <div class="artist-image">
                        ${artist.image ?
                    `<img src="${artist.image}" alt="${escapeHtml(artist.name)}" />` :
                    '<div class="artist-placeholder">🎵</div>'
                }
                    </div>
                    <div class="artist-info">
                        <div class="artist-name">${escapeHtml(artist.name)}</div>
                        <div class="artist-albums">${artist.album_count || 0} albums</div>
                    </div>
                </div>
            `).join('');
        } else {
            artistsGrid.innerHTML = '<div class="no-artists">No artists found</div>';
        }
    } catch (error) {
        console.error('Error loading artists data:', error);
        document.getElementById('artists-grid').innerHTML = '<div class="error">Error loading artists</div>';
    }
}

// ===============================
// UTILITY FUNCTIONS
// ===============================

function showLoadingOverlay(message = 'Loading...') {
    const overlay = document.getElementById('loading-overlay');
    const messageElement = overlay.querySelector('.loading-message');
    messageElement.textContent = message;
    overlay.classList.remove('hidden');
}

function hideLoadingOverlay() {
    document.getElementById('loading-overlay').classList.add('hidden');
}

// ==================================================================================
// NOTIFICATION SYSTEM — Compact toasts + bell button + notification history panel
// ==================================================================================

const _notifState = {
    history: [],
    unreadCount: 0,
    panelOpen: false,
    currentToast: null,
    toastTimer: null,
    maxHistory: 50,
};
const _recentToastKeys = new Map();

const _notifIcons = { success: '✓', error: '✕', warning: '⚠', info: 'ℹ' };

function showToast(message, type = 'success', helpSection = null) {
    const toastKey = `${type}:${message}`;
    const now = Date.now();

    // Deduplication — suppress identical toasts within 5 seconds
    if (_recentToastKeys.has(toastKey) && now - _recentToastKeys.get(toastKey) < 5000) return;
    _recentToastKeys.set(toastKey, now);
    for (const [k, t] of _recentToastKeys) { if (now - t > 10000) _recentToastKeys.delete(k); }

    // Add to notification history
    const entry = { id: now + Math.random(), message, type, helpSection, timestamp: now, read: false };
    _notifState.history.unshift(entry);
    if (_notifState.history.length > _notifState.maxHistory) _notifState.history.pop();
    _notifState.unreadCount++;
    _updateNotifBadge();

    // Show compact toast — dismiss current if showing
    const container = document.getElementById('toast-container');
    if (!container) return;

    if (_notifState.currentToast && container.contains(_notifState.currentToast)) {
        _notifState.currentToast.classList.add('toast-exit');
        const old = _notifState.currentToast;
        setTimeout(() => { if (container.contains(old)) container.removeChild(old); }, 200);
    }
    if (_notifState.toastTimer) clearTimeout(_notifState.toastTimer);

    const icon = _notifIcons[type] || 'ℹ';
    const toast = document.createElement('div');
    toast.className = `toast-compact toast-${type}`;
    toast.innerHTML = `<span class="toast-compact-icon">${icon}</span><span class="toast-compact-msg">${_escToast(message)}</span>`;
    if (helpSection) {
        const link = document.createElement('span');
        link.className = 'toast-compact-link';
        link.textContent = 'Learn more →';
        link.onclick = e => { e.stopPropagation(); if (typeof navigateToDocsSection === 'function') navigateToDocsSection(helpSection); };
        toast.appendChild(link);
    }
    toast.onclick = () => { toast.classList.add('toast-exit'); setTimeout(() => { if (container.contains(toast)) container.removeChild(toast); }, 200); };

    container.appendChild(toast);
    requestAnimationFrame(() => toast.classList.add('toast-enter'));
    _notifState.currentToast = toast;

    _notifState.toastTimer = setTimeout(() => {
        if (container.contains(toast)) {
            toast.classList.add('toast-exit');
            setTimeout(() => { if (container.contains(toast)) container.removeChild(toast); }, 300);
        }
        _notifState.currentToast = null;
    }, helpSection ? 5000 : 3500);
}

function _escToast(s) { const d = document.createElement('div'); d.textContent = s; return d.innerHTML; }
function _escAttr(s) { return _escToast(s).replace(/'/g, "\\'").replace(/\n/g, ' ').replace(/\r/g, ''); }

function _updateNotifBadge() {
    const badge = document.getElementById('notif-bell-badge');
    if (badge) {
        badge.textContent = _notifState.unreadCount > 99 ? '99+' : _notifState.unreadCount;
        badge.style.display = _notifState.unreadCount > 0 ? '' : 'none';
    }
}

function toggleNotifPanel() {
    if (_notifState.panelOpen) {
        _closeNotifPanel();
    } else {
        _openNotifPanel();
    }
}

function _openNotifPanel() {
    _closeNotifPanel(); // Remove existing

    _notifState.panelOpen = true;
    _notifState.unreadCount = 0;
    _notifState.history.forEach(e => e.read = true);
    _updateNotifBadge();

    const btn = document.getElementById('notif-bell-btn');
    const panel = document.createElement('div');
    panel.id = 'notif-panel';
    panel.className = 'notif-panel';

    const entries = _notifState.history;

    panel.innerHTML = `
        <div class="notif-panel-header">
            <span class="notif-panel-title">Notifications</span>
            ${entries.length > 0 ? '<button class="notif-panel-clear" onclick="_clearNotifHistory()">Clear All</button>' : ''}
        </div>
        <div class="notif-panel-body">
            ${entries.length === 0 ? '<div class="notif-panel-empty">No notifications yet</div>' :
            entries.map(e => {
                const icon = _notifIcons[e.type] || 'ℹ';
                const ago = _notifTimeAgo(e.timestamp);
                const unreadDot = e.read ? '' : '<span class="notif-entry-unread"></span>';
                const learnMore = e.helpSection ? `<span class="notif-entry-link" onclick="event.stopPropagation(); _closeNotifPanel(); navigateToDocsSection('${e.helpSection}')">Learn more →</span>` : '';
                return `
                    <div class="notif-entry notif-entry-${e.type}">
                        ${unreadDot}
                        <span class="notif-entry-icon notif-icon-${e.type}">${icon}</span>
                        <div class="notif-entry-body">
                            <div class="notif-entry-msg">${_escToast(e.message)}</div>
                            <div class="notif-entry-meta">${ago}${learnMore}</div>
                        </div>
                    </div>`;
            }).join('')}
        </div>
    `;

    document.body.appendChild(panel);

    // Position above the bell button
    if (btn) {
        const rect = btn.getBoundingClientRect();
        panel.style.right = (window.innerWidth - rect.right) + 'px';
        panel.style.bottom = (window.innerHeight - rect.top + 8) + 'px';
    }

    requestAnimationFrame(() => panel.classList.add('visible'));

    // Close on outside click
    setTimeout(() => {
        const closeHandler = e => {
            if (!panel.contains(e.target) && e.target.id !== 'notif-bell-btn') {
                _closeNotifPanel();
                document.removeEventListener('click', closeHandler);
            }
        };
        document.addEventListener('click', closeHandler);
    }, 100);
}

function _closeNotifPanel() {
    _notifState.panelOpen = false;
    const panel = document.getElementById('notif-panel');
    if (panel) {
        panel.classList.remove('visible');
        setTimeout(() => panel.remove(), 200);
    }
}

function _clearNotifHistory() {
    _notifState.history = [];
    _notifState.unreadCount = 0;
    _updateNotifBadge();
    _closeNotifPanel();
}

function _notifTimeAgo(ts) {
    const s = Math.floor((Date.now() - ts) / 1000);
    if (s < 5) return 'just now';
    if (s < 60) return `${s}s ago`;
    const m = Math.floor(s / 60);
    if (m < 60) return `${m}m ago`;
    const h = Math.floor(m / 60);
    if (h < 24) return `${h}h ago`;
    return `${Math.floor(h / 24)}d ago`;
}

// ==================================================================================
// Music video download handler — defined at top level so both enhanced and global search can use it
function _downloadMusicVideo(cardEl, video) {
    if (cardEl.classList.contains('downloading') || cardEl.classList.contains('completed')) return;
    cardEl.classList.add('downloading');
    cardEl.onclick = null;

    const playBtn = cardEl.querySelector('.enh-video-play');
    const progressRing = cardEl.querySelector('.enh-video-progress-ring');
    const progressBar = cardEl.querySelector('.enh-video-progress-bar');
    const doneIcon = cardEl.querySelector('.enh-video-done');
    const errorIcon = cardEl.querySelector('.enh-video-error');

    if (playBtn) playBtn.classList.add('hidden');
    if (progressRing) progressRing.classList.remove('hidden');

    fetch('/api/music-video/download', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ video_id: video.video_id, url: video.url, title: video.title, channel: video.channel }),
    }).then(res => {
        if (!res.ok) throw new Error('Download request failed');
        const circumference = 97.4;
        const pollInterval = setInterval(async () => {
            try {
                const statusRes = await fetch(`/api/music-video/status/${video.video_id}`);
                const status = await statusRes.json();
                if (progressBar && status.progress > 0) {
                    progressBar.style.strokeDashoffset = circumference - (status.progress / 100) * circumference;
                }
                if (status.status === 'completed') {
                    clearInterval(pollInterval);
                    cardEl.classList.remove('downloading');
                    cardEl.classList.add('completed');
                    if (progressRing) progressRing.classList.add('hidden');
                    if (doneIcon) doneIcon.classList.remove('hidden');
                } else if (status.status === 'error') {
                    clearInterval(pollInterval);
                    cardEl.classList.remove('downloading');
                    cardEl.classList.add('errored');
                    if (progressRing) progressRing.classList.add('hidden');
                    if (errorIcon) errorIcon.classList.remove('hidden');
                    cardEl.onclick = () => _downloadMusicVideo(cardEl, video);
                }
            } catch (e) { }
        }, 500);
    }).catch(e => {
        cardEl.classList.remove('downloading');
        if (progressRing) progressRing.classList.add('hidden');
        if (playBtn) playBtn.classList.remove('hidden');
        if (errorIcon) errorIcon.classList.remove('hidden');
        cardEl.onclick = () => _downloadMusicVideo(cardEl, video);
    });
}

// Global search video click — decodes base64 video data and delegates to _downloadMusicVideo
function _gsClickVideo(cardEl) {
    try {
        const encoded = cardEl.dataset.video;
        const video = JSON.parse(decodeURIComponent(escape(atob(encoded))));
        _downloadMusicVideo(cardEl, video);
    } catch (e) {
        console.error('Failed to parse video data:', e);
    }
}

// GLOBAL SEARCH BAR — Spotlight-style search from anywhere
// ==================================================================================

// Popover-only state. Query/source/cache/config all live in `_gsController`
// (shared with the Search page via createSearchController in shared-helpers.js).
const _gsState = {
    active: false,
    _lastInteraction: 0,
    debounceTimer: null,
};

// Shared source-picker controller — built on DOM-ready in `_doInit`.
let _gsController = null;

(function initGlobalSearch() {
    // Defer init until DOM is ready
    const _doInit = () => {
        const bar = document.getElementById('gsearch-bar');
        const input = document.getElementById('gsearch-input');
        const results = document.getElementById('gsearch-results');
        if (!input || !bar || !results) return;

        // Build the stable results-panel structure up front so the controller
        // has a sourceRow element to render into on its first _notify().
        results.innerHTML = `
            <div class="gsearch-source-row" id="gsearch-source-row"></div>
            <div class="gsearch-fallback-banner hidden" id="gsearch-fallback-banner"></div>
            <div id="gsearch-body"></div>
        `;

        _gsController = createSearchController({
            sourceRowElement: document.getElementById('gsearch-source-row'),
            iconClassPrefix: 'gsearch',
            onStateChange: _gsRenderFromState,
            onSoulseekSelected: (query) => _gsNavigateToSearchPage(query, 'soulseek'),
            onUnconfiguredClick: (src) => {
                _gsDeactivate();
                openSettingsForSource(src);
            },
        });

        bar.addEventListener('click', () => input.focus());

        input.addEventListener('focus', () => {
            bar.classList.add('active');
            const aura = document.getElementById('gsearch-aura');
            if (aura) aura.classList.add('active');
            _gsState.active = true;
            const shortcut = document.getElementById('gsearch-shortcut');
            if (shortcut) shortcut.style.display = 'none';
            // Always redraw on focus so the source icon row is current
            // (cache dots, active state, etc.). init() is a no-op after the
            // first call — safe to invoke on every focus.
            _gsController.init().then(() => _gsRenderFromState(_gsController.state));
        });

        // No blur handler — closing is handled by click-outside and Escape only
        // This prevents tab switching and result clicks from closing the panel

        const clearBtn = document.getElementById('gsearch-clear');

        input.addEventListener('input', () => {
            const q = input.value.trim();
            if (clearBtn) clearBtn.style.display = q.length > 0 ? '' : 'none';
            if (_gsState.debounceTimer) clearTimeout(_gsState.debounceTimer);
            if (q.length < 2) { _gsHideResults(); return; }
            _gsState.debounceTimer = setTimeout(() => _gsController.submitQuery(q), 300);
        });

        if (clearBtn) {
            clearBtn.addEventListener('click', e => {
                e.stopPropagation();
                input.value = '';
                clearBtn.style.display = 'none';
                // Drop cache so the next search starts clean, but don't
                // auto-fire a fetch for an empty query.
                if (_gsController) {
                    _gsController.state.query = '';
                    _gsController.state.sources = {};
                    _gsController.state.fallbacks = {};
                    _gsController.state.loadingSources = new Set();
                    _gsController.renderSourceRow();
                }
                _gsHideResults();
                input.focus();
            });
        }

        input.addEventListener('keydown', e => {
            if (e.key === 'Enter') {
                e.preventDefault();
                if (_gsState.debounceTimer) clearTimeout(_gsState.debounceTimer);
                const q = input.value.trim();
                if (q.length >= 2) _gsController.submitQuery(q);
            } else if (e.key === 'Escape') {
                _gsDeactivate();
                input.blur();
            }
        });

        // Keyboard shortcuts
        document.addEventListener('keydown', e => {
            if ((e.ctrlKey || e.metaKey) && e.key === 'k') { e.preventDefault(); input.focus(); return; }
            if (e.key === '/' && !['INPUT', 'TEXTAREA', 'SELECT'].includes(document.activeElement?.tagName)) { e.preventDefault(); input.focus(); }
        });

        // Click outside to close — uses delayed check because tab clicks replace DOM
        document.addEventListener('click', e => {
            if (!_gsState.active) return;
            // Skip if click was recent interaction with search system (within 100ms of a switch)
            if (_gsState._lastInteraction && Date.now() - _gsState._lastInteraction < 200) return;
            setTimeout(() => {
                if (!_gsState.active) return;
                const freshBar = document.getElementById('gsearch-bar');
                const freshResults = document.getElementById('gsearch-results');
                const target = e.target;
                if (freshBar?.contains(target) || freshResults?.contains(target)) return;
                _gsDeactivate();
            }, 100);
        });

        // Collapse on sidebar navigation + hide on downloads page
        document.addEventListener('click', e => {
            if (e.target.closest('.sidebar-link, .nav-item, .back-btn')) {
                if (_gsState.active) _gsDeactivate();
                // Check after navigation which page we're on
                setTimeout(_gsUpdateVisibility, 200);
            }
        });
    };
    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', () => { _doInit(); _gsUpdateVisibility(); });
    else { _doInit(); setTimeout(_gsUpdateVisibility, 500); }
})();

function _gsUpdateVisibility() {
    const bar = document.getElementById('gsearch-bar');
    const aura = document.getElementById('gsearch-aura');
    if (!bar) return;
    // Hide on the Search page where the unified search already exists. Accept the
    // legacy 'downloads' id for callers that predate the page rename.
    const onSearchPage = typeof currentPage !== 'undefined' && (currentPage === 'search' || currentPage === 'downloads');
    bar.style.display = onSearchPage ? 'none' : '';
    if (aura) aura.classList.toggle('hidden', onSearchPage);
    if (onSearchPage && _gsState.active) _gsDeactivate();
}

function _gsDeactivate() {
    const bar = document.getElementById('gsearch-bar');
    const aura = document.getElementById('gsearch-aura');
    const shortcut = document.getElementById('gsearch-shortcut');
    if (bar) bar.classList.remove('active');
    if (aura) aura.classList.remove('active');
    if (shortcut) shortcut.style.display = '';
    _gsState.active = false;
    _gsHideResults();
}

function _gsHideResults() {
    const r = document.getElementById('gsearch-results');
    if (r) r.classList.remove('visible');
}

function _gsShowResults() {
    const r = document.getElementById('gsearch-results');
    if (r && r.innerHTML.trim()) r.classList.add('visible');
}

function _gsNavigateToSearchPage(query, src) {
    _gsDeactivate();
    if (typeof navigateToPage !== 'function') return;
    navigateToPage('search');
    // After the page mounts, mirror the query into whichever input drives the
    // requested source. Soulseek goes through the basic-search file flow, not
    // the enhanced metadata flow — without this branch the Search page would
    // run /api/enhanced-search instead of /api/search and the user would get
    // metadata results when they clicked the Soulseek icon.
    setTimeout(() => {
        if (src === 'soulseek') {
            const basicInput = document.getElementById('downloads-search-input');
            if (basicInput && query) basicInput.value = query;

            // Sync the Search page controller's state.query to the widget's
            // query BEFORE clicking the Soulseek icon. Otherwise the icon
            // click fires onSoulseekSelected(state.query) where state.query
            // is whatever the user last typed on /search (often stale), and
            // the callback would overwrite basicInput.value with that stale
            // value before running performDownloadsSearch.
            if (typeof _searchPageController !== 'undefined' && _searchPageController) {
                _searchPageController.state.query = query || '';
            }

            const soulseekIcon = document.querySelector('#enh-source-row [data-source="soulseek"]');
            if (soulseekIcon) {
                soulseekIcon.click();
                return;
            }
            // Fallback: controller hasn't initialized yet (slow /api/settings
            // fetches at first /search visit). Run the search directly + swap
            // sections so the user still gets results. Icon row will catch up
            // visually on the next render.
            const basicSection = document.getElementById('basic-search-section');
            const enhancedSection = document.getElementById('enhanced-search-section');
            if (basicSection) basicSection.classList.add('active');
            if (enhancedSection) enhancedSection.classList.remove('active');
            if (basicInput && basicInput.value && typeof performDownloadsSearch === 'function') {
                performDownloadsSearch();
            }
            return;
        }
        const input = document.getElementById('enhanced-search-input');
        if (input && query) {
            input.value = query;
            input.dispatchEvent(new Event('input', { bubbles: true }));
        }
    }, 300);
}

// Re-render the results body + fallback banner whenever the controller's
// state changes (cache hit, fetch settle, query reset). The icon row itself
// is rendered by the controller into `#gsearch-source-row`.
function _gsRenderFromState(state) {
    const results = document.getElementById('gsearch-results');
    const body = document.getElementById('gsearch-body');
    if (!results || !body) return;

    // Fallback banner — independent of body content.
    const banner = document.getElementById('gsearch-fallback-banner');
    const activeSrc = state.activeSource;
    const actual = state.fallbacks[activeSrc];
    if (banner) {
        if (actual && actual !== activeSrc) {
            const clicked = (SOURCE_LABELS[activeSrc] || {}).text || activeSrc;
            const served = (SOURCE_LABELS[actual] || {}).text || actual;
            banner.textContent = `${clicked} unavailable — showing ${served}.`;
            banner.classList.remove('hidden');
        } else {
            banner.classList.add('hidden');
        }
    }

    // Soulseek has its own dedicated handler (navigate to /search); there's
    // nothing to render in the popover.
    if (activeSrc === 'soulseek') return;

    const cached = state.sources[activeSrc];
    const isLoading = state.loadingSources.has(activeSrc);
    const query = state.query;

    // No query yet — prompt.
    if (!query) {
        body.innerHTML = '<div class="gsearch-empty">Type to search…</div>';
        results.classList.add('visible');
        return;
    }

    // In-flight, nothing cached yet — loading state.
    if (isLoading && !cached) {
        const info = SOURCE_LABELS[activeSrc];
        body.innerHTML = `<div class="gsearch-loading"><div class="server-search-spinner"></div>Searching ${_escToast((info && info.text) || activeSrc)}...</div>`;
        results.classList.add('visible');
        return;
    }

    // No cache, not loading — source switch before fetch fired (e.g. empty query).
    if (!cached) {
        body.innerHTML = '<div class="gsearch-empty">Click the source above to search.</div>';
        results.classList.add('visible');
        return;
    }

    // Music Videos — video grid instead of regular sections.
    if (activeSrc === 'youtube_videos') {
        const videos = cached.videos || [];
        let h = `<div class="gsearch-results-header"><span class="gsearch-results-title">Results</span><span class="gsearch-results-count">${videos.length} videos</span></div>`;
        h += '<div class="gsearch-results-body">';
        if (videos.length === 0) {
            h += `<div class="gsearch-empty">No music videos found for "${_escToast(query)}"</div>`;
        } else {
            h += '<div class="gsearch-section-header">🎬 Music Videos</div>';
            h += '<div class="enh-video-grid">';
            h += videos.map(v => {
                const dur = v.duration ? `${Math.floor(v.duration / 60)}:${String(v.duration % 60).padStart(2, '0')}` : '';
                const views = v.view_count >= 1000000 ? `${(v.view_count / 1000000).toFixed(1)}M` : v.view_count >= 1000 ? `${(v.view_count / 1000).toFixed(1)}K` : (v.view_count || '');
                const vJson = btoa(unescape(encodeURIComponent(JSON.stringify(v))));
                return `<div class="enh-video-card" data-video-id="${v.video_id}" data-video="${vJson}" onclick="_gsClickVideo(this)">
                    <div class="enh-video-thumb"><img src="${v.thumbnail}" alt="" loading="lazy" onerror="this.style.display='none'"><div class="enh-video-play">▶</div>
                    <div class="enh-video-progress-ring hidden"><svg viewBox="0 0 36 36"><circle class="enh-video-progress-bg" cx="18" cy="18" r="15.5" fill="none" stroke="rgba(255,255,255,0.15)" stroke-width="3"/><circle class="enh-video-progress-bar" cx="18" cy="18" r="15.5" fill="none" stroke="rgb(var(--accent-rgb))" stroke-width="3" stroke-dasharray="97.4" stroke-dashoffset="97.4" stroke-linecap="round" transform="rotate(-90 18 18)"/></svg></div>
                    <div class="enh-video-done hidden">✓</div><div class="enh-video-error hidden">✗</div>
                    ${dur ? `<span class="enh-video-duration">${dur}</span>` : ''}</div>
                    <div class="enh-video-info"><div class="enh-video-title">${_escToast(v.title)}</div><div class="enh-video-channel">${_escToast(v.channel)}${views ? ` · ${views} views` : ''}</div></div>
                </div>`;
            }).join('');
            h += '</div>';
        }
        h += '</div>';
        body.innerHTML = h;
        results.classList.add('visible');
        return;
    }

    // Standard metadata source — library + artists + albums + singles + tracks.
    const dbArtists = cached.db_artists || [];
    const artists = cached.artists || [];
    const allAlbums = cached.albums || [];
    const albums = allAlbums.filter(a => !a.album_type || a.album_type === 'album' || a.album_type === 'compilation');
    const singles = allAlbums.filter(a => a.album_type === 'single' || a.album_type === 'ep');
    const tracks = cached.tracks || [];
    const total = dbArtists.length + artists.length + albums.length + singles.length + tracks.length;

    if (total === 0) {
        body.innerHTML = `<div class="gsearch-empty">No results for "${_escToast(query)}"<br><span style="font-size:10px;opacity:0.5">Try different keywords or check spelling</span></div>`;
        results.classList.add('visible');
        return;
    }

    const srcLabel = (SOURCE_LABELS[activeSrc] || {}).text || activeSrc || '';

    let h = '';
    h += `<div class="gsearch-results-header"><span class="gsearch-results-title">Results</span><span class="gsearch-results-count">${total} items</span></div>`;
    h += '<div class="gsearch-results-body">';

    if (dbArtists.length) {
        h += '<div class="gsearch-section-header">📚 In Your Library</div><div class="gsearch-grid">';
        h += dbArtists.map(a => `<div class="gsearch-item" onclick="_gsClickArtist('${a.id}', '${_escAttr(a.name)}', true)"><div class="gsearch-item-art">${a.image_url ? `<img src="${a.image_url}" loading="lazy" onerror="this.parentElement.textContent='🎤'">` : '🎤'}</div><div class="gsearch-item-info"><div class="gsearch-item-title">${_escToast(a.name)}</div><div class="gsearch-item-sub">Library</div></div></div>`).join('');
        h += '</div>';
    }

    if (artists.length) {
        h += `<div class="gsearch-section-header">🎤 Artists <span class="gsearch-source-badge">${srcLabel}</span></div><div class="gsearch-grid" id="gsearch-artists-grid">`;
        h += artists.map(a => `<div class="gsearch-item" onclick="_gsClickArtist('${a.id}', '${_escAttr(a.name)}', false)" ${!a.image_url ? `data-artist-id="${a.id}" data-needs-image="true" data-artist-name="${_escAttr(a.name)}"` : ''}><div class="gsearch-item-art">${a.image_url ? `<img src="${a.image_url}" loading="lazy" onerror="this.parentElement.textContent='🎤'">` : '🎤'}</div><div class="gsearch-item-info"><div class="gsearch-item-title">${_escToast(a.name)}</div></div></div>`).join('');
        h += '</div>';
    }

    if (albums.length) {
        h += `<div class="gsearch-section-header">💿 Albums <span class="gsearch-source-badge">${srcLabel}</span></div><div class="gsearch-grid">`;
        h += albums.map(a => {
            const ar = a.artist || (a.artists ? a.artists.join(', ') : '');
            const yr = a.release_date ? a.release_date.substring(0, 4) : '';
            const img = (a.image_url || '').replace(/'/g, "\\'");
            return `<div class="gsearch-item" onclick="_gsClickAlbum('${a.id}', '${_escAttr(a.name)}', '${_escAttr(ar)}', '${img}', '${activeSrc}')"><div class="gsearch-item-art">${a.image_url ? `<img src="${a.image_url}" loading="lazy" onerror="this.parentElement.textContent='💿'">` : '💿'}</div><div class="gsearch-item-info"><div class="gsearch-item-title">${_escToast(a.name)}</div><div class="gsearch-item-sub">${_escToast(ar)}${yr ? ` · ${yr}` : ''}</div></div></div>`;
        }).join('');
        h += '</div>';
    }

    if (singles.length) {
        h += `<div class="gsearch-section-header">🎶 Singles & EPs <span class="gsearch-source-badge">${srcLabel}</span></div><div class="gsearch-grid">`;
        h += singles.map(a => {
            const ar = a.artist || (a.artists ? a.artists.join(', ') : '');
            const img = (a.image_url || '').replace(/'/g, "\\'");
            return `<div class="gsearch-item" onclick="_gsClickAlbum('${a.id}', '${_escAttr(a.name)}', '${_escAttr(ar)}', '${img}', '${activeSrc}')"><div class="gsearch-item-art">${a.image_url ? `<img src="${a.image_url}" loading="lazy" onerror="this.parentElement.textContent='🎶'">` : '🎶'}</div><div class="gsearch-item-info"><div class="gsearch-item-title">${_escToast(a.name)}</div><div class="gsearch-item-sub">${_escToast(ar)}</div></div></div>`;
        }).join('');
        h += '</div>';
    }

    if (tracks.length) {
        h += `<div class="gsearch-section-header">🎵 Tracks <span class="gsearch-source-badge">${srcLabel}</span></div><div class="gsearch-track-list">`;
        h += tracks.map(t => {
            const ar = t.artist || (t.artists ? t.artists.join(', ') : '');
            const dur = t.duration_ms ? `${Math.floor(t.duration_ms / 60000)}:${String(Math.floor((t.duration_ms % 60000) / 1000)).padStart(2, '0')}` : '';
            return `<div class="gsearch-track" onclick="_gsClickTrack('${_escAttr(ar)}', '${_escAttr(t.name)}', '${_escAttr(t.album || '')}', '${_escAttr(t.id || '')}', '${_escAttr(t.image_url || '')}', ${t.duration_ms || 0})"><div class="gsearch-item-art" style="width:32px;height:32px;border-radius:6px">${t.image_url ? `<img src="${t.image_url}" loading="lazy" onerror="this.parentElement.textContent='🎵'">` : '🎵'}</div><div class="gsearch-item-info"><div class="gsearch-item-title">${_escToast(t.name)}</div><div class="gsearch-item-sub">${_escToast(ar)}${t.album ? ` · ${_escToast(t.album)}` : ''}</div></div><div class="gsearch-track-dur">${dur}</div><button class="gsearch-play-btn" onclick="event.stopPropagation(); _gsPlayTrack('${_escAttr(t.name)}', '${_escAttr(ar)}', '${_escAttr(t.album || '')}')" title="Stream">▶</button></div>`;
        }).join('');
        h += '</div>';
    }

    h += '</div>';
    body.innerHTML = h;
    results.classList.add('visible');

    // Lazy load artist images for sources that don't provide them (iTunes/Deezer).
    _gsLazyLoadArtistImages();

    // Library ownership check — adds "In Library" badges + swaps play buttons.
    // Idempotent enough to run on every render with a cache hit; the old flow
    // also fired it on both cache-hit and fetch-settle.
    setTimeout(() => _gsLibraryCheck(), 200);
}

async function _gsLazyLoadArtistImages() {
    const grid = document.getElementById('gsearch-artists-grid');
    if (!grid) return;
    const cards = grid.querySelectorAll('[data-needs-image="true"]');
    if (cards.length === 0) return;
    const activeSrc = (_gsController && _gsController.state.activeSource) || 'spotify';

    for (const card of cards) {
        const artistId = card.dataset.artistId;
        if (!artistId) continue;
        try {
            // Pass the artist name so MusicBrainz lookups (which have no
            // artist art) can resolve the image by name on a fallback source.
            const params = new URLSearchParams({ source: activeSrc });
            if (card.dataset.artistName) params.set('name', card.dataset.artistName);
            const res = await fetch(`/api/artist/${artistId}/image?${params}`);
            const data = await res.json();
            if (data.success && data.image_url) {
                const artDiv = card.querySelector('.gsearch-item-art');
                if (artDiv) artDiv.innerHTML = `<img src="${data.image_url}" loading="lazy" onerror="this.parentElement.textContent='🎤'">`;
                card.removeAttribute('data-needs-image');
            }
        } catch (e) { /* ignore */ }
    }
}

function _gsClickArtist(id, name, isLibrary) {
    _gsDeactivate();
    const activeSource = _gsController && _gsController.state.activeSource;
    const source = isLibrary ? null : (activeSource || null);
    navigateToArtistDetail(id, name, source);
}

async function _gsClickAlbum(albumId, albumName, artistName, imageUrl, source) {
    _gsDeactivate();
    // Same flow as handleEnhancedSearchAlbumClick — fetch album, open download modal
    showLoadingOverlay('Loading album...');
    try {
        const params = new URLSearchParams({ name: albumName, artist: artistName });
        if (source && source !== 'spotify') params.set('source', source);
        const response = await fetch(`/api/spotify/album/${albumId}?${params}`);
        if (!response.ok) throw new Error(`Failed to load album: ${response.status}`);
        const albumData = await response.json();

        if (!albumData || !albumData.tracks || albumData.tracks.length === 0) {
            hideLoadingOverlay();
            showToast(`No tracks available for "${albumName}"`, 'warning');
            return;
        }

        const enrichedTracks = albumData.tracks.map(t => ({
            ...t,
            album: { name: albumData.name, id: albumData.id, album_type: albumData.album_type || 'album', images: albumData.images || [], release_date: albumData.release_date, total_tracks: albumData.total_tracks }
        }));

        const virtualPlaylistId = `enhanced_search_album_${albumId}`;
        const firstArtist = (albumData.artists || [])[0] || {};
        const artistObj = { id: firstArtist.id || '', name: firstArtist.name || artistName, source: source || '' };
        const albumObj = { name: albumData.name, id: albumData.id, album_type: albumData.album_type || 'album', images: albumData.images || [], release_date: albumData.release_date, total_tracks: albumData.total_tracks, artists: albumData.artists || [{ name: artistName }] };

        await openDownloadMissingModalForArtistAlbum(virtualPlaylistId, `[${artistName}] ${albumData.name}`, enrichedTracks, albumObj, artistObj, false);

        // Register download bubble (same pattern as enhanced search)
        registerSearchDownload(
            {
                id: albumData.id,
                name: albumData.name,
                artist: artistName,
                image_url: albumData.images?.[0]?.url || imageUrl || null,
                images: albumData.images || []
            },
            'album',
            virtualPlaylistId,
            artistName
        );

    } catch (e) {
        hideLoadingOverlay();
        showToast('Failed to load album: ' + e.message, 'error');
    }
}

async function _gsClickTrack(artistName, trackName, albumName, trackId, imageUrl, durationMs) {
    _gsDeactivate();

    // Build enriched track + open download modal directly (same as enhanced search)
    const virtualPlaylistId = `gsearch_track_${trackId || (artistName + '_' + trackName).replace(/\s/g, '_')}`;
    const enrichedTrack = {
        id: trackId || '',
        name: trackName,
        artists: [artistName],
        album: { name: albumName || '', id: null, album_type: 'single', images: imageUrl ? [{ url: imageUrl }] : [], total_tracks: 1 },
        duration_ms: durationMs || 0,
        image_url: imageUrl || '',
    };
    const albumObject = {
        name: albumName || '', id: null, album_type: 'single',
        images: imageUrl ? [{ url: imageUrl }] : [],
        artists: [{ name: artistName }], total_tracks: 1,
    };
    const artistObject = { id: null, name: artistName };
    const playlistName = `${artistName} - ${trackName}`;

    try {
        showLoadingOverlay('Loading track...');
        await openDownloadMissingModalForArtistAlbum(
            virtualPlaylistId, playlistName, [enrichedTrack], albumObject, artistObject, false
        );

        // Register download bubble (same pattern as enhanced search)
        registerSearchDownload(
            {
                id: trackId || '',
                name: trackName,
                artist: artistName,
                image_url: imageUrl || null,
                images: imageUrl ? [{ url: imageUrl }] : []
            },
            'track',
            virtualPlaylistId,
            artistName
        );
    } catch (e) {
        console.error('Error opening track download:', e);
        // Fallback: navigate to the unified Search page
        navigateToPage('search');
        setTimeout(() => {
            const input = document.getElementById('enhanced-search-input');
            if (input) { input.value = `${artistName} ${trackName}`.trim(); input.dispatchEvent(new Event('input')); }
        }, 300);
    } finally {
        hideLoadingOverlay();
    }
}

async function _gsPlayTrack(trackName, artistName, albumName) {
    try {
        showToast('Searching for stream...', 'info');
        const res = await fetch('/api/enhanced-search/stream-track', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ track_name: trackName, artist_name: artistName, album_name: albumName })
        });
        const data = await res.json();
        if (data.success && data.result) {
            if (typeof startStream === 'function') {
                startStream(data.result);
            } else {
                showToast('Streaming not available', 'error');
            }
        } else {
            showToast(data.error || 'No stream found', 'error');
        }
    } catch (e) {
        showToast('Stream failed: ' + e.message, 'error');
    }
}

// Async library check for global search results — adds badges + swaps play buttons
async function _gsLibraryCheck() {
    try {
        if (!_gsController) return;
        const src = _gsController.state.sources[_gsController.state.activeSource] || {};
        const allAlbums = src.albums || [];
        const albums = allAlbums.filter(a => !a.album_type || a.album_type === 'album' || a.album_type === 'compilation');
        const singles = allAlbums.filter(a => a.album_type === 'single' || a.album_type === 'ep');
        const tracks = src.tracks || [];
        if (!allAlbums.length && !tracks.length) return;

        const res = await fetch('/api/enhanced-search/library-check', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                albums: allAlbums.map(a => ({ name: a.name, artist: a.artist || (a.artists ? a.artists.join(', ') : '') })),
                tracks: tracks.map(t => ({ name: t.name, artist: t.artist || (t.artists ? t.artists.join(', ') : '') })),
            })
        });
        const checkData = await res.json();

        // Add "In Library" badges to albums — match by index against allAlbums order
        const albumResults = checkData.albums || [];
        let albumIdx = 0;
        // Albums section
        document.querySelectorAll('#gsearch-results .gsearch-results-body').forEach(body => {
            // Find all gsearch-item elements and tag ones that are albums
            const sections = body.querySelectorAll('.gsearch-section-header');
            sections.forEach(header => {
                const text = header.textContent;
                const isAlbumSection = text.includes('Albums') || text.includes('Singles');
                if (!isAlbumSection) return;
                const grid = header.nextElementSibling;
                if (!grid) return;
                const items = grid.querySelectorAll('.gsearch-item');
                items.forEach(item => {
                    if (albumIdx < albumResults.length && albumResults[albumIdx]) {
                        if (!item.querySelector('.gsearch-item-badge')) {
                            const badge = document.createElement('span');
                            badge.className = 'gsearch-item-badge';
                            badge.textContent = 'In Library';
                            item.appendChild(badge);
                        }
                    }
                    albumIdx++;
                });
            });
        });

        // Tag tracks + swap play buttons for library playback
        const trackResults = checkData.tracks || [];
        const trackEls = document.querySelectorAll('#gsearch-results .gsearch-track');
        trackEls.forEach((el, i) => {
            const tr = trackResults[i];
            if (tr && tr.in_library) {
                // Add badge
                if (!el.querySelector('.gsearch-item-badge')) {
                    const badge = document.createElement('span');
                    badge.className = 'gsearch-item-badge';
                    badge.textContent = 'In Library';
                    badge.style.marginRight = '4px';
                    el.querySelector('.gsearch-track-dur')?.before(badge);
                }

                // Swap play button to library playback
                if (tr.file_path) {
                    const playBtn = el.querySelector('.gsearch-play-btn');
                    if (playBtn) {
                        const newBtn = playBtn.cloneNode(true);
                        newBtn.removeAttribute('onclick');
                        newBtn.title = 'Play from library';
                        newBtn.style.background = 'rgba(76,175,80,0.15)';
                        newBtn.style.color = '#4caf50';
                        newBtn.addEventListener('click', e => {
                            e.stopPropagation();
                            playLibraryTrack(
                                { id: tr.track_id, title: tr.title, file_path: tr.file_path, _stats_image: tr.album_thumb_url || null },
                                tr.album_title || '',
                                tr.artist_name || ''
                            );
                        });
                        playBtn.replaceWith(newBtn);
                    }
                }
            } else if (tr && tr.in_wishlist) {
                if (!el.querySelector('.gsearch-item-badge')) {
                    const badge = document.createElement('span');
                    badge.className = 'gsearch-item-badge gsearch-wishlist-badge';
                    badge.textContent = 'In Wishlist';
                    badge.style.marginRight = '4px';
                    el.querySelector('.gsearch-track-dur')?.before(badge);
                }
            }
        });
    } catch (e) {
        // Non-critical
    }
}

function escapeHtml(text) {
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}

/**
 * Escape a value for safe use inside a single-quoted JS string literal
 * within a double-quoted HTML attribute (e.g. onclick="fn('${val}')").
 *
 * Layer 1 (JS): escape \ and ' so the JS string parses correctly.
 * Layer 2 (HTML): escape &, ", <, > so the HTML attribute parses correctly.
 * The browser applies these in reverse: HTML-decode first, then JS-execute.
 */
function escapeForInlineJs(str) {
    if (str == null) return '';
    return String(str)
        .replace(/\\/g, '\\\\')   // JS: literal backslash
        .replace(/'/g, "\\'")     // JS: single quote
        .replace(/&/g, '&amp;')   // HTML: ampersand
        .replace(/"/g, '&quot;')  // HTML: double quote
        .replace(/</g, '&lt;')    // HTML: less-than
        .replace(/>/g, '&gt;');   // HTML: greater-than
}

function formatArtists(artists) {
    if (!artists || !Array.isArray(artists)) {
        return 'Unknown Artist';
    }

    // Handle both string arrays and object arrays with 'name' property
    const artistNames = artists.map(artist => {
        let artistName;
        if (typeof artist === 'string') {
            artistName = artist;
        } else if (artist && typeof artist === 'object' && artist.name) {
            artistName = artist.name;
        } else {
            artistName = 'Unknown Artist';
        }

        // Clean featured artists from the name
        return cleanArtistName(artistName);
    });

    return artistNames.join(', ') || 'Unknown Artist';
}

async function checkForUpdates() {
    try {
        const res = await fetch('/api/update-check');
        if (!res.ok) return;
        const data = await res.json();
        const btn = document.querySelector('.version-button');
        if (!btn) return;
        if (data.update_available) {
            const dismissed = localStorage.getItem('soulsync-update-dismissed');
            if (dismissed !== data.latest_sha) {
                // Add glow class
                btn.classList.add('update-available');
                // Add UPDATE badge if not already present
                if (!btn.querySelector('.update-badge')) {
                    const badge = document.createElement('span');
                    badge.className = 'update-badge';
                    badge.textContent = 'UPDATE';
                    btn.appendChild(badge);
                }
                // Show toast on first detection (not if already notified this session)
                const notified = sessionStorage.getItem('soulsync-update-notified');
                if (notified !== data.latest_sha) {
                    sessionStorage.setItem('soulsync-update-notified', data.latest_sha);
                    showToast(data.is_docker
                        ? 'A new SoulSync update has been pushed to the repo — Docker image will be updated soon!'
                        : 'A new SoulSync update is available!', 'info');
                }
            }
        } else {
            btn.classList.remove('update-available');
            const badge = btn.querySelector('.update-badge');
            if (badge) badge.remove();
        }
    } catch (e) {
        console.debug('Update check failed:', e);
    }
}

async function showVersionInfo() {
    // Check update status before dismissing so we can pass it to the modal
    let updateInfo = null;
    const btn = document.querySelector('.version-button');
    const hadUpdate = btn && btn.classList.contains('update-available');

    // Dismiss update glow when user opens the modal
    if (hadUpdate) {
        btn.classList.remove('update-available');
        const badge = btn.querySelector('.update-badge');
        if (badge) badge.remove();
        try {
            const updateRes = await fetch('/api/update-check');
            if (updateRes.ok) {
                updateInfo = await updateRes.json();
                if (updateInfo.latest_sha) {
                    localStorage.setItem('soulsync-update-dismissed', updateInfo.latest_sha);
                }
            }
        } catch (e) { /* ignore */ }
    }

    // Build version data straight from helper.js — single source of truth.
    // No backend round-trip; the changelog content is shipped in the same
    // bundle the browser already loaded.
    const version = (typeof _getCurrentVersion === 'function')
        ? _getCurrentVersion()
        : (btn ? btn.textContent.trim().replace('v', '') : '');
    const sections = (typeof VERSION_MODAL_SECTIONS !== 'undefined')
        ? VERSION_MODAL_SECTIONS
        : [];
    const versionData = {
        version,
        title: "What's New in SoulSync",
        subtitle: version ? `Version ${version} — Latest Changes` : 'Latest Changes',
        sections,
    };

    try {
        populateVersionModal(versionData, hadUpdate ? updateInfo : null);
        const modalOverlay = document.getElementById('version-modal-overlay');
        if (modalOverlay) modalOverlay.classList.remove('hidden');
    } catch (error) {
        console.error('Error showing version info:', error);
        showToast('Failed to load version information', 'error');
    }
}

function closeVersionModal() {
    const modalOverlay = document.getElementById('version-modal-overlay');
    modalOverlay.classList.add('hidden');
    console.log('Version modal closed');
}

function populateVersionModal(versionData, updateInfo) {
    const container = document.getElementById('version-content-container');
    if (!container) {
        console.error('Version content container not found');
        return;
    }

    // Update header with dynamic data
    const titleElement = document.querySelector('.version-modal-title');
    const subtitleElement = document.querySelector('.version-modal-subtitle');

    if (titleElement) titleElement.textContent = versionData.title;
    if (subtitleElement) subtitleElement.textContent = versionData.subtitle;

    // Clear existing content
    container.innerHTML = '';

    // Show update banner if an update was available when modal was opened
    if (updateInfo && updateInfo.update_available) {
        const banner = document.createElement('div');
        banner.className = 'version-update-banner';
        const isDocker = updateInfo.is_docker;
        banner.innerHTML = `
            <div class="version-update-banner-icon">&#x2B06;</div>
            <div class="version-update-banner-text">
                <strong>${isDocker ? 'Repo update detected' : 'New update available'}</strong>
                <span>${isDocker
                ? 'A new update has been pushed to the repo. The Docker image will be updated soon — no action needed yet.'
                : `Your version: ${updateInfo.current_sha || 'unknown'} &rarr; Latest: ${updateInfo.latest_sha || 'unknown'}`}</span>
            </div>
        `;
        container.appendChild(banner);
    }

    // Create sections
    versionData.sections.forEach(section => {
        const sectionDiv = document.createElement('div');
        sectionDiv.className = 'version-feature-section';

        // Section title
        const titleDiv = document.createElement('div');
        titleDiv.className = 'version-section-title';
        titleDiv.textContent = section.title;
        sectionDiv.appendChild(titleDiv);

        // Section description
        const descDiv = document.createElement('div');
        descDiv.className = 'version-section-description';
        descDiv.textContent = section.description;
        sectionDiv.appendChild(descDiv);

        // Features list
        const featuresList = document.createElement('ul');
        featuresList.className = 'version-feature-list';

        section.features.forEach(feature => {
            const featureItem = document.createElement('li');
            featureItem.className = 'version-feature-item';
            featureItem.textContent = feature;
            featuresList.appendChild(featureItem);
        });

        sectionDiv.appendChild(featuresList);

        // Usage note (if present)
        if (section.usage_note) {
            const usageDiv = document.createElement('div');
            usageDiv.className = 'version-usage-note';
            usageDiv.textContent = `💡 ${section.usage_note}`;
            sectionDiv.appendChild(usageDiv);
        }

        container.appendChild(sectionDiv);
    });

    console.log('Version modal content populated');
}

// ===============================
// ADDITIONAL STYLES FOR SEARCH RESULTS
// ===============================

// Add dynamic styles for search results (since they're created dynamically)
const additionalStyles = `
<style>
.search-result-item {
    background: rgba(255, 255, 255, 0.02);
    border: 1px solid rgba(255, 255, 255, 0.05);
    border-radius: 8px;
    padding: 16px;
    margin-bottom: 12px;
    cursor: pointer;
    transition: all 0.2s ease;
}

.search-result-item:hover {
    background: rgba(255, 255, 255, 0.05);
    border-color: rgba(29, 185, 84, 0.2);
}

.result-header {
    display: flex;
    justify-content: space-between;
    align-items: flex-start;
    margin-bottom: 8px;
}

.result-info {
    flex: 1;
    min-width: 0;
}

.result-title {
    font-size: 14px;
    font-weight: 600;
    color: #ffffff;
    margin-bottom: 4px;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
}

.result-artist {
    font-size: 12px;
    color: #b3b3b3;
    margin-bottom: 2px;
}

.result-album {
    font-size: 11px;
    color: #888888;
}

.result-actions {
    display: flex;
    gap: 8px;
    flex-shrink: 0;
}

.stream-button, .download-button {
    padding: 6px 12px;
    border: none;
    border-radius: 6px;
    font-size: 12px;
    font-weight: 500;
    cursor: pointer;
    transition: all 0.2s ease;
}

.stream-button {
    background: rgba(var(--accent-rgb), 0.1);
    color: rgb(var(--accent-light-rgb));
    border: 1px solid rgba(var(--accent-rgb), 0.3);
}

.stream-button:hover {
    background: rgba(29, 185, 84, 0.2);
    border-color: rgba(29, 185, 84, 0.5);
}

.download-button {
    background: rgba(255, 255, 255, 0.05);
    color: rgba(255, 255, 255, 0.8);
    border: 1px solid rgba(255, 255, 255, 0.2);
}

.download-button:hover {
    background: rgba(255, 255, 255, 0.1);
    color: #ffffff;
}

.result-details {
    display: flex;
    gap: 16px;
    font-size: 11px;
    color: rgba(255, 255, 255, 0.6);
}

.result-quality {
    color: rgb(var(--accent-light-rgb));
    font-weight: 500;
}

.no-results, .no-artists, .error {
    text-align: center;
    color: rgba(255, 255, 255, 0.5);
    padding: 40px 20px;
    font-size: 14px;
}

.artist-card {
    background: rgba(255, 255, 255, 0.02);
    border: 1px solid rgba(255, 255, 255, 0.05);
    border-radius: 12px;
    padding: 16px;
    text-align: center;
    cursor: pointer;
    transition: all 0.2s ease;
}

.artist-card:hover {
    background: rgba(255, 255, 255, 0.05);
    border-color: rgba(29, 185, 84, 0.2);
}

.artist-image {
    width: 120px;
    height: 120px;
    margin: 0 auto 12px auto;
    border-radius: 8px;
    overflow: hidden;
    background: rgba(255, 255, 255, 0.05);
}

.artist-image img {
    width: 100%;
    height: 100%;
    object-fit: cover;
}

.artist-placeholder {
    width: 100%;
    height: 100%;
    display: flex;
    align-items: center;
    justify-content: center;
    font-size: 32px;
    color: rgba(255, 255, 255, 0.3);
}

.artist-name {
    font-size: 14px;
    font-weight: 600;
    color: #ffffff;
    margin-bottom: 4px;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
}

.artist-albums {
    font-size: 12px;
    color: #b3b3b3;
}
</style>
`;

// Inject additional styles
document.head.insertAdjacentHTML('beforeend', additionalStyles);

// ============================================================================

