// TIDAL PLAYLIST MANAGEMENT (YouTube-style cards with Tidal colors)
// ===================================================================

async function loadTidalPlaylists() {
    const container = document.getElementById('tidal-playlist-container');
    const refreshBtn = document.getElementById('tidal-refresh-btn');

    container.innerHTML = `<div class="playlist-placeholder">🔄 Loading Tidal playlists...</div>`;
    refreshBtn.disabled = true;
    refreshBtn.textContent = '🔄 Loading...';

    try {
        const response = await fetch('/api/tidal/playlists');
        if (!response.ok) {
            const error = await response.json();
            throw new Error(error.error || 'Failed to fetch Tidal playlists');
        }

        tidalPlaylists = await response.json();
        renderTidalPlaylists();
        tidalPlaylistsLoaded = true;

        console.log(`🎵 Loaded ${tidalPlaylists.length} Tidal playlists`);

        // Auto-mirror Tidal playlists: fetch tracks in background then mirror
        // Cards render instantly from metadata; tracks load per-playlist without blocking UI
        for (const p of tidalPlaylists) {
            // Skip if already have tracks from a previous load
            if (p.tracks && p.tracks.length > 0) {
                mirrorPlaylist('tidal', p.id, p.name, p.tracks.map(t => ({
                    track_name: t.name || '', artist_name: Array.isArray(t.artists) ? t.artists[0] : (t.artists || ''),
                    album_name: typeof t.album === 'string' ? t.album : '', duration_ms: t.duration_ms || 0,
                    source_track_id: t.id || ''
                })), { owner: p.owner, image_url: p.image_url, description: p.description });
                continue;
            }
            // Fetch tracks on-demand for this playlist
            try {
                const fullResp = await fetch(`/api/tidal/playlist/${p.id}`);
                if (fullResp.ok) {
                    const fullData = await fullResp.json();
                    if (fullData.tracks && fullData.tracks.length > 0) {
                        p.tracks = fullData.tracks;
                        p.track_count = fullData.tracks.length;
                        // Update card track count in UI
                        const countEl = document.querySelector(`#tidal-card-${p.id} .playlist-card-track-count`);
                        if (countEl) countEl.textContent = `${fullData.tracks.length} tracks`;
                        // Mirror with full track data
                        mirrorPlaylist('tidal', p.id, p.name, fullData.tracks.map(t => ({
                            track_name: t.name || '', artist_name: Array.isArray(t.artists) ? t.artists[0] : (t.artists || ''),
                            album_name: typeof t.album === 'string' ? t.album : '', duration_ms: t.duration_ms || 0,
                            source_track_id: t.id || ''
                        })), { owner: p.owner, image_url: p.image_url, description: p.description });
                    }
                }
            } catch (e) {
                console.warn(`Failed to fetch tracks for Tidal playlist ${p.name}: ${e.message}`);
            }
        }

        // Load and apply saved discovery states from backend (like YouTube)
        await loadTidalPlaylistStatesFromBackend();

    } catch (error) {
        container.innerHTML = `<div class="playlist-placeholder">❌ Error: ${error.message}</div>`;
        showToast(`Error loading Tidal playlists: ${error.message}`, 'error');
    } finally {
        refreshBtn.disabled = false;
        refreshBtn.textContent = '🔄 Refresh';
    }
}

function renderTidalPlaylists() {
    const container = document.getElementById('tidal-playlist-container');
    if (tidalPlaylists.length === 0) {
        container.innerHTML = `<div class="playlist-placeholder">No Tidal playlists found.</div>`;
        return;
    }

    container.innerHTML = tidalPlaylists.map(p => {
        // Initialize state if not exists (fresh state like sync.py)
        if (!tidalPlaylistStates[p.id]) {
            tidalPlaylistStates[p.id] = {
                phase: 'fresh',
                playlist: p
            };
        }

        return createTidalCard(p);
    }).join('');

    // Add click handlers to cards
    tidalPlaylists.forEach(p => {
        const card = document.getElementById(`tidal-card-${p.id}`);
        if (card) {
            card.addEventListener('click', () => handleTidalCardClick(p.id));
        }
    });
}

function createTidalCard(playlist) {
    const state = tidalPlaylistStates[playlist.id];
    const phase = state.phase;

    // Get phase-specific button text (like YouTube cards)
    let buttonText = getActionButtonText(phase);
    let phaseText = getPhaseText(phase);
    let phaseColor = getPhaseColor(phase);

    return `
        <div class="youtube-playlist-card tidal-playlist-card" id="tidal-card-${playlist.id}">
            <div class="playlist-card-icon">🎵</div>
            <div class="playlist-card-content">
                <div class="playlist-card-name">${escapeHtml(playlist.name)}</div>
                <div class="playlist-card-info">
                    <span class="playlist-card-track-count">${playlist.track_count} tracks</span>
                    <span class="playlist-card-phase-text" style="color: ${phaseColor};">${phaseText}</span>
                </div>
            </div>
            <div class="playlist-card-progress ${phase === 'fresh' ? 'hidden' : ''}">
                <!-- Progress will be dynamically updated based on phase -->
            </div>
            <button class="playlist-card-action-btn">${buttonText}</button>
        </div>
    `;
}

async function handleTidalCardClick(playlistId) {
    // Robust state validation
    const state = tidalPlaylistStates[playlistId];
    if (!state) {
        console.error(`❌ [Card Click] No state found for Tidal playlist: ${playlistId}`);
        showToast('Playlist state not found - try refreshing the page', 'error');
        return;
    }

    // Validate required state data
    if (!state.playlist) {
        console.error(`❌ [Card Click] No playlist data found for Tidal playlist: ${playlistId}`);
        showToast('Playlist data missing - try refreshing the page', 'error');
        return;
    }

    // Validate phase
    if (!state.phase) {
        console.warn(`⚠️ [Card Click] No phase set for Tidal playlist ${playlistId} - defaulting to 'fresh'`);
        state.phase = 'fresh';
    }

    console.log(`🎵 [Card Click] Tidal card clicked: ${playlistId}, Phase: ${state.phase}`);

    if (state.phase === 'fresh') {
        // Fetch tracks if not yet loaded (metadata-only listing doesn't include them)
        if (!state.playlist.tracks || state.playlist.tracks.length === 0) {
            console.log(`🎵 Fetching tracks for Tidal playlist: ${state.playlist.name}`);
            showLoadingOverlay(`Loading ${state.playlist.name}...`);
            try {
                const resp = await fetch(`/api/tidal/playlist/${playlistId}`);
                if (resp.ok) {
                    const fullData = await resp.json();
                    if (fullData.tracks && fullData.tracks.length > 0) {
                        // Convert to Track-like objects for the discovery modal
                        state.playlist.tracks = fullData.tracks.map(t => ({
                            id: t.id, name: t.name, artists: t.artists || [],
                            album: t.album || '', duration_ms: t.duration_ms || 0,
                            track_number: t.track_number || 0
                        }));
                        // Update card count
                        const countEl = document.querySelector(`#tidal-card-${playlistId} .playlist-card-track-count`);
                        if (countEl) countEl.textContent = `${state.playlist.tracks.length} tracks`;
                    }
                }
            } catch (e) {
                console.error(`Failed to fetch Tidal playlist tracks: ${e}`);
                hideLoadingOverlay();
            }
        }

        if (!state.playlist.tracks || state.playlist.tracks.length === 0) {
            hideLoadingOverlay();
            showToast('Could not load tracks for this playlist', 'error');
            return;
        }

        hideLoadingOverlay();
        console.log(`🎵 Ready with ${state.playlist.tracks.length} Tidal tracks for discovery`);

        // Open discovery modal - phase will be updated when discovery actually starts
        openTidalDiscoveryModal(playlistId, state.playlist);

    } else if (state.phase === 'discovering' || state.phase === 'discovered' || state.phase === 'syncing' || state.phase === 'sync_complete') {
        // Reopen existing modal with preserved discovery results (like GUI sync.py)
        console.log(`🎵 [Card Click] Opening Tidal discovery modal for ${state.phase} phase`);

        // Validate that we have discovery results to show
        if (state.phase === 'discovered' && (!state.discovery_results || state.discovery_results.length === 0)) {
            console.warn(`⚠️ [Card Click] Discovered phase but no discovery results found - attempting to reload from backend`);

            // Try to fetch from backend as fallback
            try {
                const stateResponse = await fetch(`/api/tidal/state/${playlistId}`);
                if (stateResponse.ok) {
                    const fullState = await stateResponse.json();
                    if (fullState.discovery_results) {
                        // Merge backend state with current state
                        state.discovery_results = fullState.discovery_results;
                        state.spotify_matches = fullState.spotify_matches || state.spotify_matches;
                        state.discovery_progress = fullState.discovery_progress || state.discovery_progress;
                        tidalPlaylistStates[playlistId] = { ...tidalPlaylistStates[playlistId], ...state };
                        console.log(`✅ [Card Click] Restored ${fullState.discovery_results.length} discovery results from backend`);
                    }
                }
            } catch (error) {
                console.error(`❌ [Card Click] Failed to fetch discovery results from backend: ${error}`);
            }
        }

        openTidalDiscoveryModal(playlistId, state.playlist);
    } else if (state.phase === 'downloading' || state.phase === 'download_complete') {
        // Open download modal if we have the converted playlist ID
        if (state.convertedSpotifyPlaylistId) {
            console.log(`🔍 [Card Click] Opening download modal for Tidal playlist: ${state.playlist.name} (phase: ${state.phase})`);
            // Check if modal already exists, if not create it
            if (activeDownloadProcesses[state.convertedSpotifyPlaylistId]) {
                const process = activeDownloadProcesses[state.convertedSpotifyPlaylistId];
                if (process.modalElement) {
                    console.log(`📱 [Card Click] Showing existing download modal for ${state.phase} phase`);
                    process.modalElement.style.display = 'flex';
                } else {
                    console.warn(`⚠️ [Card Click] Download process exists but modal element missing - rehydrating`);
                    await rehydrateTidalDownloadModal(playlistId, state);
                }
            } else {
                // Need to create the download modal - fetch the discovery results
                console.log(`🔧 [Card Click] Rehydrating Tidal download modal for ${state.phase} phase`);
                await rehydrateTidalDownloadModal(playlistId, state);
            }
        } else {
            console.error('❌ [Card Click] No converted Spotify playlist ID found for Tidal download modal');
            console.log('📊 [Card Click] Available state data:', Object.keys(state));

            // Fallback: try to open discovery modal if we have discovery results
            if (state.discovery_results && state.discovery_results.length > 0) {
                console.log(`🔄 [Card Click] Fallback: Opening discovery modal with ${state.discovery_results.length} results`);
                openTidalDiscoveryModal(playlistId, state.playlist);
            } else {
                showToast('Unable to open download modal - missing playlist data', 'error');
            }
        }
    }
}

async function rehydrateTidalDownloadModal(playlistId, state) {
    try {
        // Robust state validation for rehydration
        if (!state || !state.playlist) {
            console.error(`❌ [Rehydration] Invalid state data for Tidal playlist: ${playlistId}`);
            showToast('Cannot open download modal - invalid playlist data', 'error');
            return;
        }

        console.log(`💧 [Rehydration] Rehydrating Tidal download modal for: ${state.playlist.name}`);

        // Get discovery results from backend if not already loaded
        if (!state.discovery_results) {
            console.log(`🔍 Fetching discovery results from backend for Tidal playlist: ${playlistId}`);
            const stateResponse = await fetch(`/api/tidal/state/${playlistId}`);
            if (stateResponse.ok) {
                const fullState = await stateResponse.json();
                state.discovery_results = fullState.discovery_results;
                state.convertedSpotifyPlaylistId = fullState.converted_spotify_playlist_id;
                state.download_process_id = fullState.download_process_id;
                console.log(`✅ Loaded ${fullState.discovery_results?.length || 0} discovery results from backend`);
            } else {
                console.error('❌ Failed to fetch Tidal discovery results from backend');
                showToast('Error loading playlist data', 'error');
                return;
            }
        }

        // Extract Spotify tracks from discovery results
        const spotifyTracks = [];
        for (const result of state.discovery_results) {
            if (result.spotify_data) {
                spotifyTracks.push(result.spotify_data);
            }
        }

        if (spotifyTracks.length === 0) {
            console.error('❌ No Spotify tracks found for download modal');
            showToast('No Spotify matches found for download', 'error');
            return;
        }

        const virtualPlaylistId = state.convertedSpotifyPlaylistId;
        const playlistName = state.playlist.name;

        // Create the download modal
        await openDownloadMissingModalForTidal(virtualPlaylistId, playlistName, spotifyTracks);

        // If we have a download process ID, set up the modal for the running state
        if (state.download_process_id) {
            const process = activeDownloadProcesses[virtualPlaylistId];
            if (process) {
                process.status = state.phase === 'download_complete' ? 'complete' : 'running';
                process.batchId = state.download_process_id;

                // Update UI based on phase
                const beginBtn = document.getElementById(`begin-analysis-btn-${virtualPlaylistId}`);
                const cancelBtn = document.getElementById(`cancel-all-btn-${virtualPlaylistId}`);

                if (state.phase === 'downloading') {
                    if (beginBtn) beginBtn.style.display = 'none';
                    if (cancelBtn) cancelBtn.style.display = 'inline-block';

                    // Start polling for live updates
                    startModalDownloadPolling(virtualPlaylistId);
                    console.log(`🔄 Started polling for active Tidal download: ${state.download_process_id}`);
                } else if (state.phase === 'download_complete') {
                    if (beginBtn) beginBtn.style.display = 'none';
                    if (cancelBtn) cancelBtn.style.display = 'none';
                    console.log(`✅ Showing completed Tidal download results: ${state.download_process_id}`);

                    // For completed downloads, fetch the final results once to populate the modal
                    try {
                        const response = await fetch(`/api/playlists/${state.download_process_id}/download_status`);
                        if (response.ok) {
                            const data = await response.json();
                            if (data.phase === 'complete' && data.tasks) {
                                console.log(`📊 [Rehydration] Loading ${data.tasks.length} completed tasks for modal display`);
                                // Process the completed tasks to update modal display
                                updateCompletedModalResults(virtualPlaylistId, data);
                            } else {
                                console.warn(`⚠️ [Rehydration] Unexpected data from download_status: phase=${data.phase}, tasks=${data.tasks?.length || 0}`);
                            }
                        } else {
                            console.error(`❌ [Rehydration] Failed to fetch download status: ${response.status} ${response.statusText}`);
                        }
                    } catch (error) {
                        console.error(`❌ [Rehydration] Error fetching final results for completed download: ${error}`);
                        // Show a user-friendly message but still allow modal to open
                        showToast('Could not load download results - modal may show incomplete data', 'warning', 3000);
                    }
                }
            }
        }

        console.log(`✅ Successfully rehydrated Tidal download modal for: ${state.playlist.name}`);

    } catch (error) {
        console.error(`❌ Error rehydrating Tidal download modal:`, error);
        showToast('Error opening download modal', 'error');
    }
}

function updateCompletedModalResults(playlistId, downloadData) {
    /**
     * Update a completed download modal with final results
     * This reuses the existing status polling logic but applies it once for completed state
     */
    console.log(`📊 [Completed Results] Updating modal ${playlistId} with final download results`);

    // Validate input data
    if (!downloadData || !downloadData.tasks) {
        console.error(`❌ [Completed Results] Invalid download data for playlist ${playlistId}:`, downloadData);
        return;
    }

    try {
        // Update analysis progress to 100%
        const analysisProgressFill = document.getElementById(`analysis-progress-fill-${playlistId}`);
        const analysisProgressText = document.getElementById(`analysis-progress-text-${playlistId}`);
        if (analysisProgressFill) analysisProgressFill.style.width = '100%';
        if (analysisProgressText) analysisProgressText.textContent = 'Analysis complete!';

        // Update analysis results and stats
        if (downloadData.analysis_results) {
            updateTrackAnalysisResults(playlistId, downloadData.analysis_results);
            const foundCount = downloadData.analysis_results.filter(r => r.found).length;
            const missingCount = downloadData.analysis_results.filter(r => !r.found).length;

            const statFound = document.getElementById(`stat-found-${playlistId}`);
            const statMissing = document.getElementById(`stat-missing-${playlistId}`);
            if (statFound) statFound.textContent = foundCount;
            if (statMissing) statMissing.textContent = missingCount;
        }

        // Process completed tasks to update individual track statuses
        const missingTracks = (downloadData.analysis_results || []).filter(r => !r.found);
        let completedCount = 0;
        let failedOrCancelledCount = 0;
        let notFoundCount = 0;

        (downloadData.tasks || []).forEach(task => {
            const row = document.querySelector(`#download-missing-modal-${CSS.escape(playlistId)} tr[data-track-index="${task.track_index}"]`);
            if (!row) return;

            row.dataset.taskId = task.task_id;
            const statusEl = document.getElementById(`download-${playlistId}-${task.track_index}`);
            const actionsEl = document.getElementById(`actions-${playlistId}-${task.track_index}`);

            let statusText = '';
            switch (task.status) {
                case 'pending': statusText = '⏸️ Pending'; break;
                case 'searching': statusText = '🔍 Searching...'; break;
                case 'downloading': statusText = `⏬ Downloading... ${Math.round(task.progress || 0)}%`; break;
                case 'post_processing': statusText = '⌛ Processing...'; break; // NEW VERIFICATION WORKFLOW
                case 'completed': statusText = '✅ Completed'; completedCount++; break;
                case 'not_found': statusText = '🔇 Not Found'; notFoundCount++; break;
                case 'failed': statusText = '❌ Failed'; failedOrCancelledCount++; break;
                case 'cancelled': statusText = '🚫 Cancelled'; failedOrCancelledCount++; break;
                default: statusText = `⚪ ${task.status}`; break;
            }

            if (statusEl) {
                statusEl.textContent = statusText;
                if ((task.status === 'failed' || task.status === 'cancelled' || task.status === 'not_found') && task.error_message) {
                    statusEl.classList.add('has-error-tooltip');
                    statusEl.dataset.errorMsg = task.error_message;
                    _ensureErrorTooltipListeners(statusEl);
                }
                if (task.status === 'not_found' && task.has_candidates) {
                    statusEl.classList.add('has-candidates');
                    statusEl.dataset.taskId = task.task_id;
                    _ensureCandidatesClickListener(statusEl);
                }
            }
            if (actionsEl) actionsEl.innerHTML = '-'; // Remove action buttons for completed tasks
        });

        // Update download progress to final state
        const totalFinished = completedCount + failedOrCancelledCount + notFoundCount;
        const missingCount = missingTracks.length;
        const progressPercent = missingCount > 0 ? (totalFinished / missingCount) * 100 : 100;

        const downloadProgressFill = document.getElementById(`download-progress-fill-${playlistId}`);
        const downloadProgressText = document.getElementById(`download-progress-text-${playlistId}`);
        const statDownloaded = document.getElementById(`stat-downloaded-${playlistId}`);

        if (downloadProgressFill) downloadProgressFill.style.width = `${progressPercent}%`;
        if (downloadProgressText) downloadProgressText.textContent = `${completedCount}/${missingCount} completed (${progressPercent.toFixed(0)}%)`;
        if (statDownloaded) statDownloaded.textContent = completedCount;

        console.log(`✅ [Completed Results] Updated modal with ${completedCount} completed, ${notFoundCount} not found, ${failedOrCancelledCount} failed tasks`);

    } catch (error) {
        console.error(`❌ [Completed Results] Error updating completed modal results:`, error);
    }
}

function updateTidalCardPhase(playlistId, phase) {
    const state = tidalPlaylistStates[playlistId];
    if (!state) return;

    state.phase = phase;

    // Re-render the card with new phase
    const card = document.getElementById(`tidal-card-${playlistId}`);
    if (card) {
        const oldButtonText = card.querySelector('.playlist-card-action-btn')?.textContent || 'unknown';
        const newCardHtml = createTidalCard(state.playlist);
        card.outerHTML = newCardHtml;

        // Verify the card was actually updated
        const updatedCard = document.getElementById(`tidal-card-${playlistId}`);
        const newButtonText = updatedCard?.querySelector('.playlist-card-action-btn')?.textContent || 'unknown';

        console.log(`🔄 [Card Update] Re-rendered Tidal card ${playlistId}:`);
        console.log(`   📊 Phase: ${phase}`);
        console.log(`   🔘 Button text: "${oldButtonText}" → "${newButtonText}"`);
        console.log(`   ✅ Expected: "${getActionButtonText(phase)}"`);

        if (newButtonText !== getActionButtonText(phase)) {
            console.error(`❌ [Card Update] Button text mismatch! Expected "${getActionButtonText(phase)}", got "${newButtonText}"`);
        }

        // Re-attach click handler
        const newCard = document.getElementById(`tidal-card-${playlistId}`);
        if (newCard) {
            newCard.addEventListener('click', () => handleTidalCardClick(playlistId));
            console.debug(`🔗 [Card Update] Reattached click handler for Tidal card: ${playlistId}`);
        } else {
            console.error(`❌ [Card Update] Failed to find new card after rendering: tidal-card-${playlistId}`);
        }

        // If we have sync progress and we're in sync/sync_complete phase, restore it
        if ((phase === 'syncing' || phase === 'sync_complete') && state.lastSyncProgress) {
            setTimeout(() => {
                updateTidalCardSyncProgress(playlistId, state.lastSyncProgress);
            }, 0);
        }
    }

    console.log(`🎵 Updated Tidal card phase: ${playlistId} -> ${phase}`);
}

async function openTidalDiscoveryModal(playlistId, playlistData) {
    console.log(`🎵 Opening Tidal discovery modal (reusing YouTube modal): ${playlistData.name}`);

    // Create a fake YouTube-style urlHash for the modal system
    const fakeUrlHash = `tidal_${playlistId}`;

    // Get current Tidal card state to check if discovery is already done or in progress
    const tidalCardState = tidalPlaylistStates[playlistId];
    const isAlreadyDiscovered = tidalCardState && (tidalCardState.phase === 'discovered' || tidalCardState.phase === 'syncing' || tidalCardState.phase === 'sync_complete');
    const isCurrentlyDiscovering = tidalCardState && tidalCardState.phase === 'discovering';

    // Prepare discovery results in the correct format for modal
    let transformedResults = [];
    let actualMatches = 0;
    if (isAlreadyDiscovered && tidalCardState.discovery_results) {
        transformedResults = tidalCardState.discovery_results.map((result, index) => {
            // Check multiple status formats
            const isFound = result.status === 'found' ||
                result.status === '✅ Found' ||
                result.status_class === 'found' ||
                result.spotify_data ||
                result.spotify_track;
            if (isFound) actualMatches++;

            return {
                index: index,
                yt_track: result.tidal_track ? result.tidal_track.name : 'Unknown',
                yt_artist: result.tidal_track ? (result.tidal_track.artists ? result.tidal_track.artists.join(', ') : 'Unknown') : 'Unknown',
                status: isFound ? '✅ Found' : '❌ Not Found',
                status_class: isFound ? 'found' : 'not-found',
                spotify_track: result.spotify_data ? result.spotify_data.name : (result.spotify_track || '-'),
                spotify_artist: result.spotify_data && result.spotify_data.artists ?
                    (Array.isArray(result.spotify_data.artists)
                        ? result.spotify_data.artists
                            .map(a => (typeof a === 'object' && a !== null) ? (a.name || '') : a)
                            .filter(Boolean)
                            .join(', ') || '-'
                        : result.spotify_data.artists)
                    : (result.spotify_artist || '-'),
                spotify_album: result.spotify_data ? (typeof result.spotify_data.album === 'object' ? result.spotify_data.album.name : result.spotify_data.album) : (result.spotify_album || '-'),
                spotify_data: result.spotify_data, // Pass through spotify_data
                spotify_id: result.spotify_id, // Pass through spotify_id
                manual_match: result.manual_match // Pass through manual match flag
            };
        });
        console.log(`🎵 Tidal modal: Calculated ${actualMatches} matches from ${transformedResults.length} results`);
    }

    // Create YouTube-compatible state structure  
    const modalPhase = tidalCardState ? tidalCardState.phase : 'fresh';
    youtubePlaylistStates[fakeUrlHash] = {
        phase: modalPhase,
        playlist: {
            name: playlistData.name,
            tracks: playlistData.tracks
        },
        is_tidal_playlist: true,  // Flag to identify this as Tidal
        tidal_playlist_id: playlistId,
        discovery_progress: isAlreadyDiscovered ? 100 : 0,
        spotify_matches: isAlreadyDiscovered ? actualMatches : 0, // Backend format (snake_case)
        spotifyMatches: isAlreadyDiscovered ? actualMatches : 0, // Frontend format (camelCase) - for button logic
        spotify_total: playlistData.tracks.length,
        discovery_results: transformedResults,
        discoveryResults: transformedResults, // Both formats for compatibility
        discoveryProgress: isAlreadyDiscovered ? 100 : 0 // Frontend format for modal progress display
    };

    // Only start discovery if not already discovered AND not currently discovering
    if (!isAlreadyDiscovered && !isCurrentlyDiscovering) {
        // Start Tidal discovery process automatically (like sync.py)
        try {
            console.log(`🔍 Starting Tidal discovery for: ${playlistData.name}`);

            const response = await fetch(`/api/tidal/discovery/start/${playlistId}`, {
                method: 'POST'
            });

            const result = await response.json();

            if (result.error) {
                console.error('❌ Error starting Tidal discovery:', result.error);
                showToast(`Error starting discovery: ${result.error}`, 'error');
                return;
            }

            console.log('✅ Tidal discovery started, beginning polling...');

            // Update phase to discovering now that backend discovery is actually started
            tidalPlaylistStates[playlistId].phase = 'discovering';
            updateTidalCardPhase(playlistId, 'discovering');

            // Update modal phase to match
            youtubePlaylistStates[fakeUrlHash].phase = 'discovering';

            // Start polling for progress
            startTidalDiscoveryPolling(fakeUrlHash, playlistId);

        } catch (error) {
            console.error('❌ Error starting Tidal discovery:', error);
            showToast(`Error starting discovery: ${error.message}`, 'error');
        }
    } else if (isCurrentlyDiscovering) {
        // Resume polling if discovery is already in progress (like YouTube)
        console.log(`🔄 Resuming Tidal discovery polling for: ${playlistData.name}`);
        startTidalDiscoveryPolling(fakeUrlHash, playlistId);
    } else if (tidalCardState && tidalCardState.phase === 'syncing') {
        // Resume sync polling if sync is in progress
        console.log(`🔄 Resuming Tidal sync polling for: ${playlistData.name}`);
        startTidalSyncPolling(fakeUrlHash);
    } else {
        console.log('✅ Using existing results - no need to re-discover');
    }

    // Reuse YouTube discovery modal (exact sync.py pattern)
    openYouTubeDiscoveryModal(fakeUrlHash);
}

function startTidalDiscoveryPolling(fakeUrlHash, playlistId) {
    console.log(`🔄 Starting Tidal discovery polling for: ${playlistId}`);

    // Stop any existing polling
    if (activeYouTubePollers[fakeUrlHash]) {
        clearInterval(activeYouTubePollers[fakeUrlHash]);
    }

    // Phase 5: Subscribe via WebSocket
    if (socketConnected) {
        socket.emit('discovery:subscribe', { ids: [playlistId] });
        _discoveryProgressCallbacks[playlistId] = (data) => {
            if (data.error) {
                if (activeYouTubePollers[fakeUrlHash]) { clearInterval(activeYouTubePollers[fakeUrlHash]); delete activeYouTubePollers[fakeUrlHash]; }
                socket.emit('discovery:unsubscribe', { ids: [playlistId] }); delete _discoveryProgressCallbacks[playlistId];
                return;
            }
            // Transform to YouTube modal format
            const transformed = {
                progress: data.progress, spotify_matches: data.spotify_matches, spotify_total: data.spotify_total,
                complete: data.complete,
                results: (data.results || []).map((r, i) => {
                    const isWingIt = r.wing_it_fallback || r.status_class === 'wing-it';
                    const isFound = !isWingIt && (r.status === 'found' || r.status === '✅ Found' || r.status_class === 'found' || r.spotify_data || r.spotify_track);
                    return {
                        index: i, yt_track: r.tidal_track ? r.tidal_track.name : 'Unknown',
                        yt_artist: r.tidal_track ? (r.tidal_track.artists ? r.tidal_track.artists.join(', ') : 'Unknown') : 'Unknown',
                        status: isWingIt ? '🎯 Wing It' : (isFound ? '✅ Found' : '❌ Not Found'),
                        status_class: isWingIt ? 'wing-it' : (isFound ? 'found' : 'not-found'),
                        spotify_track: r.spotify_data ? r.spotify_data.name : (r.spotify_track || '-'),
                        spotify_artist: r.spotify_data && r.spotify_data.artists
                            ? (Array.isArray(r.spotify_data.artists)
                                ? (r.spotify_data.artists
                                    .map(a => (typeof a === 'object' && a !== null) ? (a.name || '') : a)
                                    .filter(Boolean)
                                    .join(', ') || '-')
                                : r.spotify_data.artists)
                            : (r.spotify_artist || '-'),
                        spotify_album: r.spotify_data ? (typeof r.spotify_data.album === 'object' ? r.spotify_data.album.name : r.spotify_data.album) : (r.spotify_album || '-'),
                        spotify_data: r.spotify_data, spotify_id: r.spotify_id, manual_match: r.manual_match,
                        wing_it_fallback: isWingIt
                    };
                })
            };
            const st = youtubePlaylistStates[fakeUrlHash];
            if (st) {
                st.discovery_progress = data.progress; st.discoveryProgress = data.progress;
                st.spotify_matches = data.spotify_matches; st.spotifyMatches = data.spotify_matches;
                st.discovery_results = data.results; st.discoveryResults = transformed.results;
                st.phase = data.phase;
                updateYouTubeDiscoveryModal(fakeUrlHash, transformed);
            }
            if (tidalPlaylistStates[playlistId]) {
                tidalPlaylistStates[playlistId].phase = data.phase;
                tidalPlaylistStates[playlistId].discovery_results = data.results;
                tidalPlaylistStates[playlistId].spotify_matches = data.spotify_matches;
                tidalPlaylistStates[playlistId].discovery_progress = data.progress;
                updateTidalCardPhase(playlistId, data.phase);
            }
            updateTidalCardProgress(playlistId, data);
            if (data.complete) {
                if (activeYouTubePollers[fakeUrlHash]) { clearInterval(activeYouTubePollers[fakeUrlHash]); delete activeYouTubePollers[fakeUrlHash]; }
                socket.emit('discovery:unsubscribe', { ids: [playlistId] }); delete _discoveryProgressCallbacks[playlistId];
            }
        };
    }

    const pollInterval = setInterval(async () => {
        // Always poll — no dedicated WebSocket events for discovery progress
        try {
            const response = await fetch(`/api/tidal/discovery/status/${playlistId}`);
            const status = await response.json();

            if (status.error) {
                console.error('❌ Error polling Tidal discovery status:', status.error);
                clearInterval(pollInterval);
                delete activeYouTubePollers[fakeUrlHash];
                return;
            }

            // Transform Tidal results to YouTube modal format first
            const transformedStatus = {
                progress: status.progress,
                spotify_matches: status.spotify_matches,
                spotify_total: status.spotify_total,
                complete: status.complete,
                results: status.results.map((result, index) => {
                    const isFound = result.status === 'found' ||
                        result.status === '✅ Found' ||
                        result.status_class === 'found' ||
                        result.spotify_data ||
                        result.spotify_track;

                    return {
                        index: index,
                        yt_track: result.tidal_track ? result.tidal_track.name : 'Unknown',
                        yt_artist: result.tidal_track ? (result.tidal_track.artists ? result.tidal_track.artists.join(', ') : 'Unknown') : 'Unknown',
                        status: isFound ? '✅ Found' : '❌ Not Found',
                        status_class: isFound ? 'found' : 'not-found',
                        spotify_track: result.spotify_data ? result.spotify_data.name : (result.spotify_track || '-'),
                        spotify_artist: result.spotify_data && result.spotify_data.artists
                            ? (Array.isArray(result.spotify_data.artists)
                                ? (result.spotify_data.artists
                                    .map(a => (typeof a === 'object' && a !== null) ? (a.name || '') : a)
                                    .filter(Boolean)
                                    .join(', ') || '-')
                                : result.spotify_data.artists)
                            : (result.spotify_artist || '-'),
                        spotify_album: result.spotify_data ? (typeof result.spotify_data.album === 'object' ? result.spotify_data.album.name : result.spotify_data.album) : (result.spotify_album || '-'),
                        spotify_data: result.spotify_data, // Pass through
                        spotify_id: result.spotify_id, // Pass through
                        manual_match: result.manual_match // Pass through
                    };
                })
            };

            // Update fake YouTube state with Tidal discovery results
            const state = youtubePlaylistStates[fakeUrlHash];
            if (state) {
                state.discovery_progress = status.progress; // Backend format
                state.discoveryProgress = status.progress; // Frontend format - for modal progress display
                state.spotify_matches = status.spotify_matches; // Backend format
                state.spotifyMatches = status.spotify_matches; // Frontend format - for button logic
                state.discovery_results = status.results; // Backend format
                state.discoveryResults = transformedStatus.results; // Frontend format - for button logic  
                state.phase = status.phase;

                // Update modal with transformed data (reuse YouTube modal update logic)
                updateYouTubeDiscoveryModal(fakeUrlHash, transformedStatus);

                // Update Tidal card phase and save discovery results FIRST
                if (tidalPlaylistStates[playlistId]) {
                    tidalPlaylistStates[playlistId].phase = status.phase;
                    tidalPlaylistStates[playlistId].discovery_results = status.results;
                    tidalPlaylistStates[playlistId].spotify_matches = status.spotify_matches;
                    tidalPlaylistStates[playlistId].discovery_progress = status.progress;
                    updateTidalCardPhase(playlistId, status.phase);
                }

                // Update Tidal card progress AFTER phase update to avoid being overwritten
                updateTidalCardProgress(playlistId, status);

                console.log(`🔄 Tidal discovery progress: ${status.progress}% (${status.spotify_matches}/${status.spotify_total} found)`);
            }

            // Stop polling when complete
            if (status.complete) {
                console.log(`✅ Tidal discovery complete: ${status.spotify_matches}/${status.spotify_total} tracks found`);
                clearInterval(pollInterval);
                delete activeYouTubePollers[fakeUrlHash];
            }

        } catch (error) {
            console.error('❌ Error polling Tidal discovery:', error);
            clearInterval(pollInterval);
            delete activeYouTubePollers[fakeUrlHash];
        }
    }, 1000); // Poll every second like YouTube

    // Store poller reference (reuse YouTube poller storage)
    activeYouTubePollers[fakeUrlHash] = pollInterval;
}

async function loadTidalPlaylistStatesFromBackend() {
    // Load all stored Tidal playlist discovery states from backend (similar to YouTube hydration)
    try {
        console.log('🎵 Loading Tidal playlist states from backend...');

        const response = await fetch('/api/tidal/playlists/states');
        if (!response.ok) {
            const error = await response.json();
            throw new Error(error.error || 'Failed to fetch Tidal playlist states');
        }

        const data = await response.json();
        const states = data.states || [];

        console.log(`🎵 Found ${states.length} stored Tidal playlist states in backend`);

        if (states.length === 0) {
            console.log('🎵 No Tidal playlist states to hydrate');
            return;
        }

        // Apply states to existing playlist cards
        for (const stateInfo of states) {
            await applyTidalPlaylistState(stateInfo);
        }

        // Rehydrate download modals for Tidal playlists in downloading/download_complete phases
        for (const stateInfo of states) {
            if ((stateInfo.phase === 'downloading' || stateInfo.phase === 'download_complete') &&
                stateInfo.converted_spotify_playlist_id && stateInfo.download_process_id) {

                const convertedPlaylistId = stateInfo.converted_spotify_playlist_id;

                if (!activeDownloadProcesses[convertedPlaylistId]) {
                    console.log(`💧 Rehydrating download modal for Tidal playlist: ${stateInfo.playlist_id}`);
                    try {
                        // Get the playlist data
                        const playlistData = tidalPlaylists.find(p => p.id === stateInfo.playlist_id);
                        if (!playlistData) {
                            console.warn(`⚠️ Playlist data not found for rehydration: ${stateInfo.playlist_id}`);
                            continue;
                        }

                        // Create the download modal using the Tidal-specific function
                        const spotifyTracks = tidalPlaylistStates[stateInfo.playlist_id]?.discovery_results
                            ?.filter(result => result.spotify_data)
                            ?.map(result => result.spotify_data) || [];

                        if (spotifyTracks.length > 0) {
                            await openDownloadMissingModalForTidal(
                                convertedPlaylistId,
                                playlistData.name,
                                spotifyTracks
                            );

                            // Set the modal to running state with the correct batch ID
                            const process = activeDownloadProcesses[convertedPlaylistId];
                            if (process) {
                                process.status = 'running';
                                process.batchId = stateInfo.download_process_id;

                                // Update UI to running state
                                const beginBtn = document.getElementById(`begin-analysis-btn-${convertedPlaylistId}`);
                                const cancelBtn = document.getElementById(`cancel-all-btn-${convertedPlaylistId}`);
                                if (beginBtn) beginBtn.style.display = 'none';
                                if (cancelBtn) cancelBtn.style.display = 'inline-block';

                                // Start polling for this process
                                startModalDownloadPolling(convertedPlaylistId);

                                console.log(`✅ Rehydrated Tidal download modal for batch ${stateInfo.download_process_id}`);
                            }
                        } else {
                            console.warn(`⚠️ No Spotify tracks found for Tidal playlist rehydration: ${stateInfo.playlist_id}`);
                        }
                    } catch (error) {
                        console.error(`❌ Error rehydrating Tidal download modal for ${stateInfo.playlist_id}:`, error);
                    }
                }
            }
        }

        console.log('✅ Tidal playlist states loaded and applied');

    } catch (error) {
        console.error('❌ Error loading Tidal playlist states:', error);
    }
}

async function applyTidalPlaylistState(stateInfo) {
    const { playlist_id, phase, discovery_progress, spotify_matches, discovery_results, converted_spotify_playlist_id, download_process_id } = stateInfo;

    try {
        console.log(`🎵 Applying saved state for Tidal playlist: ${playlist_id}, Phase: ${phase}`);

        // Find the playlist data from the loaded playlists
        const playlistData = tidalPlaylists.find(p => p.id === playlist_id);
        if (!playlistData) {
            console.warn(`⚠️ Playlist data not found for state ${playlist_id} - skipping`);
            return;
        }

        // Update local state
        if (!tidalPlaylistStates[playlist_id]) {
            // Initialize state if it doesn't exist
            tidalPlaylistStates[playlist_id] = {
                playlist: playlistData,
                phase: 'fresh'
            };
        }

        // Update with backend state
        tidalPlaylistStates[playlist_id].phase = phase;
        tidalPlaylistStates[playlist_id].discovery_progress = discovery_progress;
        tidalPlaylistStates[playlist_id].spotify_matches = spotify_matches;
        tidalPlaylistStates[playlist_id].discovery_results = discovery_results;
        tidalPlaylistStates[playlist_id].convertedSpotifyPlaylistId = converted_spotify_playlist_id;
        tidalPlaylistStates[playlist_id].download_process_id = download_process_id;
        tidalPlaylistStates[playlist_id].playlist = playlistData; // Ensure playlist data is set

        // Fetch full discovery results for non-fresh playlists (matching YouTube pattern)
        if (phase !== 'fresh' && phase !== 'discovering') {
            try {
                console.log(`🔍 Fetching full discovery results for Tidal playlist: ${playlistData.name}`);
                const stateResponse = await fetch(`/api/tidal/state/${playlist_id}`);
                if (stateResponse.ok) {
                    const fullState = await stateResponse.json();
                    console.log(`📋 Retrieved full Tidal state with ${fullState.discovery_results?.length || 0} discovery results`);

                    // Store full discovery results in local state (matching YouTube pattern)
                    if (fullState.discovery_results && tidalPlaylistStates[playlist_id]) {
                        tidalPlaylistStates[playlist_id].discovery_results = fullState.discovery_results;
                        tidalPlaylistStates[playlist_id].discovery_progress = fullState.discovery_progress;
                        tidalPlaylistStates[playlist_id].spotify_matches = fullState.spotify_matches;
                        tidalPlaylistStates[playlist_id].convertedSpotifyPlaylistId = fullState.converted_spotify_playlist_id;
                        tidalPlaylistStates[playlist_id].download_process_id = fullState.download_process_id;
                        console.log(`✅ Restored ${fullState.discovery_results.length} discovery results for Tidal playlist: ${playlistData.name}`);
                    }
                } else {
                    console.warn(`⚠️ Could not fetch full discovery results for Tidal playlist: ${playlistData.name}`);
                }
            } catch (error) {
                console.warn(`⚠️ Error fetching full discovery results for Tidal playlist ${playlistData.name}:`, error.message);
            }
        }

        // Update the card UI to reflect the saved state
        updateTidalCardPhase(playlist_id, phase);

        // Update card progress if we have discovery results
        if (phase === 'discovered' && tidalPlaylistStates[playlist_id]) {
            const progressInfo = {
                spotify_total: playlistData.track_count || playlistData.tracks?.length || 0,
                spotify_matches: tidalPlaylistStates[playlist_id].spotify_matches || 0
            };
            updateTidalCardProgress(playlist_id, progressInfo);
        }

        // Handle active polling resumption (matching YouTube/Beatport pattern)
        if (phase === 'discovering') {
            console.log(`🔍 Resuming discovery polling for Tidal: ${playlistData.name}`);
            const fakeUrlHash = `tidal_${playlist_id}`;
            startTidalDiscoveryPolling(fakeUrlHash, playlist_id);
        } else if (phase === 'syncing') {
            console.log(`🔄 Resuming sync polling for Tidal: ${playlistData.name}`);
            const fakeUrlHash = `tidal_${playlist_id}`;
            startTidalSyncPolling(fakeUrlHash);
        }

        console.log(`✅ Applied saved state for Tidal playlist: ${playlist_id} -> ${phase}`);

    } catch (error) {
        console.error(`❌ Error applying Tidal playlist state for ${playlist_id}:`, error);
    }
}

function updateTidalCardProgress(playlistId, progress) {
    const state = tidalPlaylistStates[playlistId];
    if (!state) return;

    const card = document.getElementById(`tidal-card-${playlistId}`);
    if (!card) return;

    const progressElement = card.querySelector('.playlist-card-progress');
    if (!progressElement) return;

    const total = progress.spotify_total || 0;
    const matches = progress.spotify_matches || 0;
    const failed = total - matches;
    const percentage = total > 0 ? Math.round((matches / total) * 100) : 0;

    progressElement.textContent = `♪ ${total} / ✓ ${matches} / ✗ ${failed} / ${percentage}%`;
    progressElement.classList.remove('hidden'); // Show progress during discovery

    console.log('🎵 Updated Tidal card progress:', playlistId, `${matches}/${total} (${percentage}%)`);
}

// ===============================
// TIDAL SYNC FUNCTIONALITY
// ===============================

async function startTidalPlaylistSync(urlHash) {
    try {
        console.log('🎵 Starting Tidal playlist sync:', urlHash);

        const state = youtubePlaylistStates[urlHash];
        if (!state || !state.is_tidal_playlist) {
            console.error('❌ Invalid Tidal playlist state for sync');
            return;
        }

        const playlistId = state.tidal_playlist_id;
        const response = await fetch(`/api/tidal/sync/start/${playlistId}`, {
            method: 'POST'
        });

        const result = await response.json();

        if (result.error) {
            showToast(`Error starting sync: ${result.error}`, 'error');
            return;
        }

        // Capture sync_playlist_id for WebSocket subscription
        const syncPlaylistId = result.sync_playlist_id;
        if (state) state.syncPlaylistId = syncPlaylistId;

        // Update card and modal to syncing phase
        updateTidalCardPhase(playlistId, 'syncing');

        // Update modal buttons if modal is open
        updateTidalModalButtons(urlHash, 'syncing');

        // Start sync polling
        startTidalSyncPolling(urlHash, syncPlaylistId);

        showToast('Tidal playlist sync started!', 'success');

    } catch (error) {
        console.error('❌ Error starting Tidal sync:', error);
        showToast(`Error starting sync: ${error.message}`, 'error');
    }
}

function startTidalSyncPolling(urlHash, syncPlaylistId) {
    // Stop any existing polling
    if (activeYouTubePollers[urlHash]) {
        clearInterval(activeYouTubePollers[urlHash]);
    }

    const state = youtubePlaylistStates[urlHash];
    const playlistId = state.tidal_playlist_id;

    // Resolve syncPlaylistId from argument or stored state
    syncPlaylistId = syncPlaylistId || (state && state.syncPlaylistId);

    // Phase 6: Subscribe via WebSocket
    if (socketConnected && syncPlaylistId) {
        socket.emit('sync:subscribe', { playlist_ids: [syncPlaylistId] });
        _syncProgressCallbacks[syncPlaylistId] = (data) => {
            const progress = data.progress || {};
            updateTidalCardSyncProgress(playlistId, progress);
            updateTidalModalSyncProgress(urlHash, progress);

            if (data.status === 'finished') {
                if (activeYouTubePollers[urlHash]) { clearInterval(activeYouTubePollers[urlHash]); delete activeYouTubePollers[urlHash]; }
                socket.emit('sync:unsubscribe', { playlist_ids: [syncPlaylistId] });
                delete _syncProgressCallbacks[syncPlaylistId];
                if (tidalPlaylistStates[playlistId]) tidalPlaylistStates[playlistId].phase = 'sync_complete';
                if (youtubePlaylistStates[urlHash]) youtubePlaylistStates[urlHash].phase = 'sync_complete';
                updateTidalCardPhase(playlistId, 'sync_complete');
                updateTidalModalButtons(urlHash, 'sync_complete');
                showToast('Tidal playlist sync complete!', 'success');
            } else if (data.status === 'error' || data.status === 'cancelled') {
                if (activeYouTubePollers[urlHash]) { clearInterval(activeYouTubePollers[urlHash]); delete activeYouTubePollers[urlHash]; }
                socket.emit('sync:unsubscribe', { playlist_ids: [syncPlaylistId] });
                delete _syncProgressCallbacks[syncPlaylistId];
                if (tidalPlaylistStates[playlistId]) tidalPlaylistStates[playlistId].phase = 'discovered';
                if (youtubePlaylistStates[urlHash]) youtubePlaylistStates[urlHash].phase = 'discovered';
                updateTidalCardPhase(playlistId, 'discovered');
                updateTidalModalButtons(urlHash, 'discovered');
                showToast(`Sync failed: ${data.error || 'Unknown error'}`, 'error');
            }
        };
    }

    // Define the polling function (HTTP fallback)
    const pollFunction = async () => {
        if (socketConnected) return; // Phase 6: WS handles updates
        try {
            const response = await fetch(`/api/tidal/sync/status/${playlistId}`);
            const status = await response.json();

            if (status.error) {
                console.error('❌ Error polling Tidal sync status:', status.error);
                clearInterval(pollInterval);
                delete activeYouTubePollers[urlHash];
                return;
            }

            updateTidalCardSyncProgress(playlistId, status.progress);
            updateTidalModalSyncProgress(urlHash, status.progress);

            if (status.complete) {
                clearInterval(pollInterval);
                delete activeYouTubePollers[urlHash];
                if (tidalPlaylistStates[playlistId]) tidalPlaylistStates[playlistId].phase = 'sync_complete';
                if (youtubePlaylistStates[urlHash]) youtubePlaylistStates[urlHash].phase = 'sync_complete';
                updateTidalCardPhase(playlistId, 'sync_complete');
                updateTidalModalButtons(urlHash, 'sync_complete');
                showToast('Tidal playlist sync complete!', 'success');
            } else if (status.sync_status === 'error') {
                clearInterval(pollInterval);
                delete activeYouTubePollers[urlHash];
                if (tidalPlaylistStates[playlistId]) tidalPlaylistStates[playlistId].phase = 'discovered';
                if (youtubePlaylistStates[urlHash]) youtubePlaylistStates[urlHash].phase = 'discovered';
                updateTidalCardPhase(playlistId, 'discovered');
                updateTidalModalButtons(urlHash, 'discovered');
                showToast(`Sync failed: ${status.error || 'Unknown error'}`, 'error');
            }
        } catch (error) {
            console.error('❌ Error polling Tidal sync:', error);
            if (activeYouTubePollers[urlHash]) {
                clearInterval(activeYouTubePollers[urlHash]);
                delete activeYouTubePollers[urlHash];
            }
        }
    };

    // Run immediately to get current status (skip if WS active)
    if (!socketConnected) pollFunction();

    // Then continue polling at regular intervals
    const pollInterval = setInterval(pollFunction, 1000);
    activeYouTubePollers[urlHash] = pollInterval;
}

async function cancelTidalSync(urlHash) {
    try {
        console.log('❌ Cancelling Tidal sync:', urlHash);

        const state = youtubePlaylistStates[urlHash];
        if (!state || !state.is_tidal_playlist) {
            console.error('❌ Invalid Tidal playlist state');
            return;
        }

        const playlistId = state.tidal_playlist_id;
        const response = await fetch(`/api/tidal/sync/cancel/${playlistId}`, {
            method: 'POST'
        });

        const result = await response.json();

        if (result.error) {
            showToast(`Error cancelling sync: ${result.error}`, 'error');
            return;
        }

        // Stop polling
        if (activeYouTubePollers[urlHash]) {
            clearInterval(activeYouTubePollers[urlHash]);
            delete activeYouTubePollers[urlHash];
        }

        // Phase 6: Clean up WS subscription
        const syncId = state && state.syncPlaylistId;
        if (syncId && _syncProgressCallbacks[syncId]) {
            if (socketConnected) socket.emit('sync:unsubscribe', { playlist_ids: [syncId] });
            delete _syncProgressCallbacks[syncId];
        }

        // Revert to discovered phase
        updateTidalCardPhase(playlistId, 'discovered');
        updateTidalModalButtons(urlHash, 'discovered');

        showToast('Tidal sync cancelled', 'info');

    } catch (error) {
        console.error('❌ Error cancelling Tidal sync:', error);
        showToast(`Error cancelling sync: ${error.message}`, 'error');
    }
}

function updateTidalCardSyncProgress(playlistId, progress) {
    const state = tidalPlaylistStates[playlistId];
    if (!state || !state.playlist || !progress) return;

    // Save the progress for later restoration
    state.lastSyncProgress = progress;

    const card = document.getElementById(`tidal-card-${playlistId}`);
    if (!card) return;

    const progressElement = card.querySelector('.playlist-card-progress');

    // Build clean status counter HTML exactly like YouTube cards
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

    // Only update if we have valid sync progress, otherwise preserve existing discovery results
    if (statusCounterHTML) {
        progressElement.innerHTML = statusCounterHTML;
    }

    console.log(`🎵 Updated Tidal card sync progress: ♪ ${progress?.total_tracks || 0} / ✓ ${progress?.matched_tracks || 0} / ✗ ${progress?.failed_tracks || 0}`);
}

function updateTidalModalSyncProgress(urlHash, progress) {
    const statusDisplay = document.getElementById(`tidal-sync-status-${urlHash}`);
    if (!statusDisplay || !progress) return;

    console.log(`📊 Updating Tidal modal sync progress for ${urlHash}:`, progress);

    // Update individual counters exactly like YouTube sync
    const totalEl = document.getElementById(`tidal-total-${urlHash}`);
    const matchedEl = document.getElementById(`tidal-matched-${urlHash}`);
    const failedEl = document.getElementById(`tidal-failed-${urlHash}`);
    const percentageEl = document.getElementById(`tidal-percentage-${urlHash}`);

    const total = progress.total_tracks || 0;
    const matched = progress.matched_tracks || 0;
    const failed = progress.failed_tracks || 0;

    if (totalEl) totalEl.textContent = total;
    if (matchedEl) matchedEl.textContent = matched;
    if (failedEl) failedEl.textContent = failed;

    // Calculate percentage like YouTube sync
    if (total > 0) {
        const processed = matched + failed;
        const percentage = Math.round((processed / total) * 100);
        if (percentageEl) percentageEl.textContent = percentage;
    }

    console.log(`📊 Tidal modal updated: ♪ ${total} / ✓ ${matched} / ✗ ${failed} (${Math.round((matched + failed) / total * 100)}%)`);
}

function updateTidalModalButtons(urlHash, phase) {
    const modal = document.getElementById(`youtube-discovery-modal-${urlHash}`);
    if (!modal) return;

    const footerLeft = modal.querySelector('.modal-footer-left');
    if (footerLeft) {
        footerLeft.innerHTML = getModalActionButtons(urlHash, phase);
    }
}

async function startTidalDownloadMissing(urlHash) {
    try {
        console.log('🔍 Starting download missing tracks for Tidal playlist:', urlHash);

        const state = youtubePlaylistStates[urlHash];
        if (!state || !state.is_tidal_playlist) {
            console.error('❌ Invalid Tidal playlist state for download');
            return;
        }

        // Tidal reuses youtubePlaylistStates infrastructure, so get results from there
        const discoveryResults = state.discoveryResults || state.discovery_results;

        if (!discoveryResults) {
            showToast('No discovery results available for download', 'error');
            return;
        }

        // Convert Tidal discovery results to Spotify tracks format (same as YouTube)
        const spotifyTracks = [];
        for (const result of discoveryResults) {
            if (result.spotify_data) {
                spotifyTracks.push(result.spotify_data);
            } else if (result.spotify_track && result.status_class === 'found') {
                // Build from individual fields (automatic discovery format)
                // Convert album to proper object format for wishlist compatibility
                const albumData = result.spotify_album || 'Unknown Album';
                const albumObject = typeof albumData === 'object' && albumData !== null
                    ? albumData
                    : {
                        name: typeof albumData === 'string' ? albumData : 'Unknown Album',
                        album_type: 'album',
                        images: []
                    };

                spotifyTracks.push({
                    id: result.spotify_id || 'unknown',
                    name: result.spotify_track || 'Unknown Track',
                    artists: result.spotify_artist ? [result.spotify_artist] : ['Unknown Artist'],
                    album: albumObject,
                    duration_ms: 0
                });
            }
        }

        if (spotifyTracks.length === 0) {
            showToast('No Spotify matches found for download', 'error');
            return;
        }

        // Create a virtual playlist for the download system
        const virtualPlaylistId = `tidal_${state.tidal_playlist_id}`;
        const playlistName = state.playlist.name;

        // Store reference for card navigation (same as YouTube)
        state.convertedSpotifyPlaylistId = virtualPlaylistId;

        // Close the discovery modal if it's open (same as YouTube)
        const discoveryModal = document.getElementById(`youtube-discovery-modal-${urlHash}`);
        if (discoveryModal) {
            discoveryModal.classList.add('hidden');
            console.log('🔄 Closed Tidal discovery modal to show download modal');
        }

        // Open download missing tracks modal for Tidal playlist
        await openDownloadMissingModalForTidal(virtualPlaylistId, playlistName, spotifyTracks);

        // Phase will change to 'downloading' when user clicks "Begin Analysis" button

    } catch (error) {
        console.error('❌ Error starting download missing tracks:', error);
        showToast(`Error starting downloads: ${error.message}`, 'error');
    }
}

async function openDownloadMissingModalForTidal(virtualPlaylistId, playlistName, spotifyTracks) {
    showLoadingOverlay('Loading Tidal playlist...');
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
        return;
    }

    console.log(`📥 Opening Download Missing Tracks modal for Tidal playlist: ${virtualPlaylistId}`);

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
        tracks: spotifyTracks
    };

    // Generate hero section with dynamic source detection (same as YouTube/Beatport)
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

    const heroContext = {
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
                        <label class="force-download-toggle">
                            <input type="checkbox" id="playlist-folder-mode-${virtualPlaylistId}">
                            <span>Organize by Playlist (Downloads/Playlist/Artist - Track.ext)</span>
                        </label>
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
                    <button class="download-control-btn secondary" onclick="closeDownloadMissingModal('${virtualPlaylistId}')">Close</button>
                </div>
            </div>
        </div>
    `;

    applyProgressiveTrackRendering(virtualPlaylistId, spotifyTracks.length);
    modal.style.display = 'flex';
    hideLoadingOverlay();
}


// ===================================================================
// DEEZER ARL PLAYLIST MANAGEMENT (Spotify-identical pattern)
// ===================================================================

async function loadDeezerArlPlaylists() {
    const container = document.getElementById('deezer-arl-playlist-container');
    const refreshBtn = document.getElementById('deezer-arl-refresh-btn');

    container.innerHTML = `<div class="playlist-placeholder">🔄 Loading playlists...</div>`;
    refreshBtn.disabled = true;
    refreshBtn.textContent = '🔄 Loading...';

    try {
        const response = await fetch('/api/deezer/arl-playlists');
        if (!response.ok) {
            const error = await response.json();
            throw new Error(error.error || 'Failed to fetch Deezer playlists');
        }
        deezerArlPlaylists = await response.json();
        renderDeezerArlPlaylists();
        deezerArlPlaylistsLoaded = true;

        // Check for active syncs or downloads and rehydrate UI
        await checkForActiveProcesses();
        for (const p of deezerArlPlaylists) {
            const arlId = `deezer_arl_${p.id}`;
            try {
                const syncResp = await fetch(`/api/sync/status/${arlId}`);
                if (syncResp.ok) {
                    const syncState = await syncResp.json();
                    if (syncState.status === 'syncing') {
                        // Re-attach sync polling and update card UI
                        if (!spotifyPlaylists.find(sp => sp.id === arlId)) {
                            spotifyPlaylists.push({ id: arlId, name: p.name, track_count: p.track_count || 0, image_url: p.image_url || '', owner: p.owner || '' });
                        }
                        updateCardToSyncing(arlId, syncState.progress?.progress || 0, syncState.progress);
                        startSyncPolling(arlId);
                        console.log(`🔄 Rehydrated active sync for Deezer ARL playlist: ${p.name}`);
                    }
                }
            } catch (e) { /* No active sync — normal */ }
        }

    } catch (error) {
        container.innerHTML = `<div class="playlist-placeholder">❌ Error: ${error.message}</div>`;
        showToast(`Error loading Deezer playlists: ${error.message}`, 'error');
    } finally {
        refreshBtn.disabled = false;
        refreshBtn.textContent = '🔄 Refresh';
    }
}

function renderDeezerArlPlaylists() {
    const container = document.getElementById('deezer-arl-playlist-container');
    if (deezerArlPlaylists.length === 0) {
        container.innerHTML = `<div class="playlist-placeholder">No Deezer playlists found.</div>`;
        return;
    }

    container.innerHTML = deezerArlPlaylists.map(p => {
        const arlId = `deezer_arl_${p.id}`;
        let statusClass = 'status-never-synced';
        if (p.sync_status && p.sync_status.startsWith('Synced')) statusClass = 'status-synced';

        return `
        <div class="playlist-card deezer-arl-playlist-card" data-playlist-id="${arlId}">
            <div class="playlist-card-main">
                <div class="playlist-card-content">
                    <div class="playlist-card-name">${escapeHtml(p.name)}</div>
                    <div class="playlist-card-info">
                        <span>${p.track_count} tracks</span> •
                        <span class="playlist-card-status ${statusClass}">${p.sync_status || 'Never Synced'}</span>
                    </div>
                    <div class="sync-progress-indicator" id="progress-${arlId}"></div>
                </div>
                <div class="playlist-card-actions">
                    <button id="action-btn-${arlId}" onclick="openDeezerArlPlaylistDetailsModal(event, '${p.id}')">Sync / Download</button>
                    <button id="progress-btn-${arlId}" class="view-progress-btn hidden" onclick="handleDeezerArlViewProgressClick(event, '${p.id}')">
                        View Progress
                    </button>
                </div>
            </div>
        </div>
        `;
    }).join('');
}

function handleDeezerArlViewProgressClick(event, playlistId) {
    event.stopPropagation();
    const arlPlaylistId = `deezer_arl_${playlistId}`;
    const process = activeDownloadProcesses[arlPlaylistId];
    if (process && process.modalElement) {
        process.modalElement.style.display = 'flex';
    }
}

async function openDeezerArlPlaylistDetailsModal(event, playlistId) {
    event.stopPropagation();

    const playlist = deezerArlPlaylists.find(p => String(p.id) === String(playlistId));
    if (!playlist) return;

    const arlPlaylistId = `deezer_arl_${playlistId}`;
    showLoadingOverlay(`Loading playlist: ${playlist.name}...`);

    try {
        if (playlistTrackCache[arlPlaylistId]) {
            const fullPlaylist = { ...playlist, id: arlPlaylistId, tracks: playlistTrackCache[arlPlaylistId] };
            showDeezerArlPlaylistDetailsModal(fullPlaylist, playlistId);
        } else {
            const response = await fetch(`/api/deezer/arl-playlist/${playlistId}`);
            const fullPlaylist = await response.json();
            if (fullPlaylist.error) throw new Error(fullPlaylist.error);

            playlistTrackCache[arlPlaylistId] = fullPlaylist.tracks;

            // Auto-mirror
            mirrorPlaylist('deezer', playlistId, fullPlaylist.name, fullPlaylist.tracks.map(t => ({
                track_name: t.name,
                artist_name: (t.artists && t.artists[0]) ? (typeof t.artists[0] === 'object' ? t.artists[0].name : t.artists[0]) : '',
                album_name: t.album ? (typeof t.album === 'object' ? t.album.name : t.album) : '',
                duration_ms: t.duration_ms || 0,
                source_track_id: t.id || ''
            })), { description: fullPlaylist.description, owner: fullPlaylist.owner, image_url: fullPlaylist.image_url });

            showDeezerArlPlaylistDetailsModal({ ...fullPlaylist, id: arlPlaylistId }, playlistId);
        }
    } catch (error) {
        showToast(`Error: ${error.message}`, 'error');
    } finally {
        hideLoadingOverlay();
    }
}

function showDeezerArlPlaylistDetailsModal(playlist, originalDeezerPlaylistId) {
    let modal = document.getElementById('deezer-arl-playlist-details-modal');
    if (!modal) {
        modal = document.createElement('div');
        modal.id = 'deezer-arl-playlist-details-modal';
        modal.className = 'modal-overlay';
        document.body.appendChild(modal);
    }

    const playlistId = playlist.id;
    const activeProcess = activeDownloadProcesses[playlistId];
    const hasCompletedProcess = activeProcess && activeProcess.status === 'complete';
    const isSyncing = !!activeSyncPollers[playlistId];

    modal.innerHTML = `
        <div class="modal-container playlist-modal">
            <div class="playlist-modal-header">
                <div class="playlist-header-content">
                    <h2>${escapeHtml(playlist.name)}</h2>
                    <div class="playlist-quick-info">
                        <span class="playlist-track-count">${playlist.track_count || (playlist.tracks ? playlist.tracks.length : 0)} tracks</span>
                        <span class="playlist-owner">by ${escapeHtml(playlist.owner || '')}</span>
                    </div>
                    <div class="playlist-modal-sync-status" id="modal-sync-status-${playlistId}" style="display: none;">
                        <span class="sync-stat total-tracks">♪ <span id="modal-total-${playlistId}">0</span></span>
                        <span class="sync-separator">/</span>
                        <span class="sync-stat matched-tracks">✓ <span id="modal-matched-${playlistId}">0</span></span>
                        <span class="sync-separator">/</span>
                        <span class="sync-stat failed-tracks">✗ <span id="modal-failed-${playlistId}">0</span></span>
                        <span class="sync-stat percentage">(<span id="modal-percentage-${playlistId}">0</span>%)</span>
                    </div>
                </div>
                <span class="playlist-modal-close" onclick="closeDeezerArlPlaylistDetailsModal()">&times;</span>
            </div>

            <div class="playlist-modal-body">
                ${playlist.description ? `<div class="playlist-description">${escapeHtml(playlist.description)}</div>` : ''}

                <div class="playlist-tracks-container">
                    <div class="playlist-tracks-list">
                        ${(playlist.tracks || []).map((track, index) => `
                            <div class="playlist-track-item">
                                <span class="playlist-track-number">${index + 1}</span>
                                <div class="playlist-track-info">
                                    <div class="playlist-track-name">${escapeHtml(track.name)}</div>
                                    <div class="playlist-track-artists">${formatArtists(track.artists)}</div>
                                </div>
                                <div class="playlist-track-duration">${formatDuration(track.duration_ms)}</div>
                            </div>
                        `).join('')}
                    </div>
                </div>
            </div>

            <div class="playlist-modal-footer">
                <button class="playlist-modal-btn playlist-modal-btn-secondary" onclick="closeDeezerArlPlaylistDetailsModal()">Close</button>
                <button class="playlist-modal-btn playlist-modal-btn-tertiary" onclick="closeDeezerArlPlaylistDetailsModal(); openDownloadMissingModal('${playlistId}')">
                    ${hasCompletedProcess ? '📊 View Download Results' : '📥 Download Missing Tracks'}
                </button>
                <button id="sync-btn-${playlistId}" class="playlist-modal-btn playlist-modal-btn-primary" onclick="startPlaylistSync('${playlistId}')" ${isSyncing ? 'disabled' : ''} ${_isSoulsyncStandalone ? 'style="display:none"' : ''}>${isSyncing ? '⏳ Syncing...' : 'Sync Playlist'}</button>
            </div>
        </div>
    `;

    // Store playlist in spotifyPlaylists-compatible format for openDownloadMissingModal
    if (!spotifyPlaylists.find(p => p.id === playlistId)) {
        spotifyPlaylists.push({
            id: playlistId,
            name: playlist.name,
            track_count: playlist.tracks ? playlist.tracks.length : 0,
            image_url: playlist.image_url || '',
            owner: playlist.owner || '',
        });
    }

    modal.style.display = 'flex';
}

function closeDeezerArlPlaylistDetailsModal() {
    const modal = document.getElementById('deezer-arl-playlist-details-modal');
    if (modal) modal.style.display = 'none';
}

function updateDeezerArlPlaylistCardUI(playlistId) {
    const arlPlaylistId = `deezer_arl_${playlistId}`;
    const process = activeDownloadProcesses[arlPlaylistId];
    const progressBtn = document.getElementById(`progress-btn-${arlPlaylistId}`);
    const actionBtn = document.getElementById(`action-btn-${arlPlaylistId}`);
    const card = document.querySelector(`.playlist-card[data-playlist-id="${arlPlaylistId}"]`);

    if (!progressBtn || !actionBtn) return;

    if (process && process.status === 'running') {
        progressBtn.classList.remove('hidden');
        progressBtn.textContent = 'View Progress';
        progressBtn.style.backgroundColor = '';
        actionBtn.textContent = '📥 Downloading...';
        actionBtn.disabled = true;
        if (card) card.classList.remove('download-complete');
    } else if (process && process.status === 'complete') {
        progressBtn.classList.remove('hidden');
        progressBtn.textContent = '📋 View Results';
        progressBtn.style.backgroundColor = '#28a745';
        progressBtn.style.color = 'white';
        actionBtn.textContent = '✅ Ready for Review';
        actionBtn.disabled = false;
        if (card) card.classList.add('download-complete');
    } else {
        progressBtn.classList.add('hidden');
        progressBtn.style.backgroundColor = '';
        progressBtn.style.color = '';
        actionBtn.textContent = 'Sync / Download';
        actionBtn.disabled = false;
        if (card) card.classList.remove('download-complete');
    }
}


// ===================================================================
// DEEZER PLAYLIST MANAGEMENT (URL-input like YouTube, reuses YouTube modal)
// ===================================================================

async function loadDeezerPlaylist() {
    const urlInput = document.getElementById('deezer-url-input');
    if (!urlInput) return;

    const rawUrl = urlInput.value.trim();
    if (!rawUrl) {
        showToast('Please paste a Deezer playlist URL', 'error');
        return;
    }

    // Extract playlist ID from URL
    // Supports: deezer.com/playlist/{id}, deezer.com/{locale}/playlist/{id}, or raw numeric ID
    let playlistId = null;
    const urlMatch = rawUrl.match(/deezer\.com\/(?:[a-z]{2}\/)?playlist\/(\d+)/i);
    if (urlMatch) {
        playlistId = urlMatch[1];
    } else if (/^\d+$/.test(rawUrl)) {
        playlistId = rawUrl;
    }

    if (!playlistId) {
        showToast('Invalid Deezer playlist URL. Expected format: deezer.com/playlist/{id}', 'error');
        return;
    }

    // Check if already loaded
    if (deezerPlaylists.find(p => String(p.id) === String(playlistId))) {
        showToast('This playlist is already loaded', 'info');
        urlInput.value = '';
        return;
    }

    const parseBtn = document.getElementById('deezer-parse-btn');
    if (parseBtn) {
        parseBtn.disabled = true;
        parseBtn.textContent = 'Loading...';
    }

    try {
        const response = await fetch(`/api/deezer/playlist/${playlistId}`);
        if (!response.ok) {
            const error = await response.json();
            throw new Error(error.error || 'Failed to fetch Deezer playlist');
        }

        const playlist = await response.json();
        deezerPlaylists.push(playlist);

        // Auto-mirror Deezer playlist
        if (playlist.tracks && playlist.tracks.length > 0) {
            mirrorPlaylist('deezer', playlist.id, playlist.name, playlist.tracks.map(t => ({
                track_name: t.name || '', artist_name: Array.isArray(t.artists) ? t.artists[0] : (t.artists || ''),
                album_name: typeof t.album === 'string' ? t.album : '', duration_ms: t.duration_ms || 0,
                source_track_id: t.id || ''
            })), { owner: playlist.owner, image_url: playlist.image_url, description: rawUrl });
        }

        // Save to URL history
        saveUrlHistory('deezer', rawUrl, playlist.name);

        renderDeezerPlaylists();
        await loadDeezerPlaylistStatesFromBackend();

        urlInput.value = '';
        showToast(`Deezer playlist loaded: ${playlist.name} (${playlist.track_count || playlist.tracks.length} tracks)`, 'success');
        console.log(`🎵 Loaded Deezer playlist: ${playlist.name}`);

    } catch (error) {
        showToast(`Error loading Deezer playlist: ${error.message}`, 'error');
    } finally {
        if (parseBtn) {
            parseBtn.disabled = false;
            parseBtn.textContent = 'Load Playlist';
        }
    }
}

function renderDeezerPlaylists() {
    const container = document.getElementById('deezer-playlist-container');
    if (deezerPlaylists.length === 0) {
        container.innerHTML = `<div class="playlist-placeholder">Paste a Deezer playlist URL above to get started.</div>`;
        return;
    }

    container.innerHTML = deezerPlaylists.map(p => {
        if (!deezerPlaylistStates[p.id]) {
            deezerPlaylistStates[p.id] = {
                phase: 'fresh',
                playlist: p
            };
        }
        return createDeezerCard(p);
    }).join('');

    // Add click handlers to cards
    deezerPlaylists.forEach(p => {
        const card = document.getElementById(`deezer-card-${p.id}`);
        if (card) {
            card.addEventListener('click', () => handleDeezerCardClick(p.id));
        }
    });
}

function createDeezerCard(playlist) {
    const state = deezerPlaylistStates[playlist.id];
    const phase = state.phase;

    let buttonText = getActionButtonText(phase);
    let phaseText = getPhaseText(phase);
    let phaseColor = getPhaseColor(phase);

    return `
        <div class="youtube-playlist-card deezer-playlist-card" id="deezer-card-${playlist.id}">
            <div class="playlist-card-icon">🎵</div>
            <div class="playlist-card-content">
                <div class="playlist-card-name">${escapeHtml(playlist.name)}</div>
                <div class="playlist-card-info">
                    <span class="playlist-card-track-count">${playlist.track_count || playlist.tracks.length} tracks</span>
                    <span class="playlist-card-phase-text" style="color: ${phaseColor};">${phaseText}</span>
                </div>
            </div>
            <div class="playlist-card-progress ${phase === 'fresh' ? 'hidden' : ''}">
                <!-- Progress will be dynamically updated based on phase -->
            </div>
            <button class="playlist-card-action-btn">${buttonText}</button>
        </div>
    `;
}

async function handleDeezerCardClick(playlistId) {
    const state = deezerPlaylistStates[playlistId];
    if (!state) {
        console.error(`No state found for Deezer playlist: ${playlistId}`);
        showToast('Playlist state not found - try refreshing the page', 'error');
        return;
    }

    if (!state.playlist) {
        console.error(`No playlist data found for Deezer playlist: ${playlistId}`);
        showToast('Playlist data missing - try refreshing the page', 'error');
        return;
    }

    if (!state.phase) {
        state.phase = 'fresh';
    }

    console.log(`🎵 [Card Click] Deezer card clicked: ${playlistId}, Phase: ${state.phase}`);

    if (state.phase === 'fresh') {
        console.log(`🎵 Using pre-loaded Deezer playlist data for: ${state.playlist.name}`);
        openDeezerDiscoveryModal(playlistId, state.playlist);

    } else if (state.phase === 'discovering' || state.phase === 'discovered' || state.phase === 'syncing' || state.phase === 'sync_complete') {
        console.log(`🎵 [Card Click] Opening Deezer discovery modal for ${state.phase} phase`);

        if (state.phase === 'discovered' && (!state.discovery_results || state.discovery_results.length === 0)) {
            try {
                const stateResponse = await fetch(`/api/deezer/state/${playlistId}`);
                if (stateResponse.ok) {
                    const fullState = await stateResponse.json();
                    if (fullState.discovery_results) {
                        state.discovery_results = fullState.discovery_results;
                        state.spotify_matches = fullState.spotify_matches || state.spotify_matches;
                        state.discovery_progress = fullState.discovery_progress || state.discovery_progress;
                        deezerPlaylistStates[playlistId] = { ...deezerPlaylistStates[playlistId], ...state };
                        console.log(`Restored ${fullState.discovery_results.length} discovery results from backend`);
                    }
                }
            } catch (error) {
                console.error(`Failed to fetch discovery results from backend: ${error}`);
            }
        }

        openDeezerDiscoveryModal(playlistId, state.playlist);
    } else if (state.phase === 'downloading' || state.phase === 'download_complete') {
        if (state.convertedSpotifyPlaylistId) {
            if (activeDownloadProcesses[state.convertedSpotifyPlaylistId]) {
                const process = activeDownloadProcesses[state.convertedSpotifyPlaylistId];
                if (process.modalElement) {
                    process.modalElement.style.display = 'flex';
                } else {
                    await rehydrateDeezerDownloadModal(playlistId, state);
                }
            } else {
                await rehydrateDeezerDownloadModal(playlistId, state);
            }
        } else {
            if (state.discovery_results && state.discovery_results.length > 0) {
                openDeezerDiscoveryModal(playlistId, state.playlist);
            } else {
                showToast('Unable to open download modal - missing playlist data', 'error');
            }
        }
    }
}

async function rehydrateDeezerDownloadModal(playlistId, state) {
    try {
        if (!state || !state.playlist) {
            showToast('Cannot open download modal - invalid playlist data', 'error');
            return;
        }

        const spotifyTracks = state.discovery_results
            ?.filter(result => result.spotify_data)
            ?.map(result => result.spotify_data) || [];

        if (spotifyTracks.length > 0) {
            const virtualPlaylistId = state.convertedSpotifyPlaylistId || `deezer_${playlistId}`;
            await openDownloadMissingModalForTidal(virtualPlaylistId, state.playlist.name, spotifyTracks);

            if (state.download_process_id) {
                const process = activeDownloadProcesses[virtualPlaylistId];
                if (process) {
                    process.status = 'running';
                    process.batchId = state.download_process_id;
                    const beginBtn = document.getElementById(`begin-analysis-btn-${virtualPlaylistId}`);
                    const cancelBtn = document.getElementById(`cancel-all-btn-${virtualPlaylistId}`);
                    if (beginBtn) beginBtn.style.display = 'none';
                    if (cancelBtn) cancelBtn.style.display = 'inline-block';
                    startModalDownloadPolling(virtualPlaylistId);
                }
            }
        } else {
            showToast('No Spotify tracks found for download', 'error');
        }
    } catch (error) {
        console.error(`Error rehydrating Deezer download modal: ${error}`);
    }
}

async function openDeezerDiscoveryModal(playlistId, playlistData) {
    console.log(`🎵 Opening Deezer discovery modal (reusing YouTube modal): ${playlistData.name}`);

    const fakeUrlHash = `deezer_${playlistId}`;

    const deezerCardState = deezerPlaylistStates[playlistId];
    const isAlreadyDiscovered = deezerCardState && (deezerCardState.phase === 'discovered' || deezerCardState.phase === 'syncing' || deezerCardState.phase === 'sync_complete');
    const isCurrentlyDiscovering = deezerCardState && deezerCardState.phase === 'discovering';

    let transformedResults = [];
    let actualMatches = 0;
    if (isAlreadyDiscovered && deezerCardState.discovery_results) {
        transformedResults = deezerCardState.discovery_results.map((result, index) => {
            const isFound = result.status === 'found' ||
                result.status === '✅ Found' ||
                result.status_class === 'found' ||
                result.spotify_data ||
                result.spotify_track;
            if (isFound) actualMatches++;

            return {
                index: index,
                yt_track: result.deezer_track ? result.deezer_track.name : 'Unknown',
                yt_artist: result.deezer_track ? (result.deezer_track.artists ? result.deezer_track.artists.join(', ') : 'Unknown') : 'Unknown',
                status: isFound ? '✅ Found' : '❌ Not Found',
                status_class: isFound ? 'found' : 'not-found',
                spotify_track: result.spotify_data ? result.spotify_data.name : (result.spotify_track || '-'),
                spotify_artist: result.spotify_data && result.spotify_data.artists ?
                    (Array.isArray(result.spotify_data.artists)
                        ? result.spotify_data.artists
                            .map(a => (typeof a === 'object' && a !== null) ? (a.name || '') : a)
                            .filter(Boolean)
                            .join(', ') || '-'
                        : result.spotify_data.artists)
                    : (result.spotify_artist || '-'),
                spotify_album: result.spotify_data ? (typeof result.spotify_data.album === 'object' ? result.spotify_data.album.name : result.spotify_data.album) : (result.spotify_album || '-'),
                spotify_data: result.spotify_data,
                spotify_id: result.spotify_id,
                manual_match: result.manual_match
            };
        });
        console.log(`🎵 Deezer modal: Calculated ${actualMatches} matches from ${transformedResults.length} results`);
    }

    const modalPhase = deezerCardState ? deezerCardState.phase : 'fresh';
    youtubePlaylistStates[fakeUrlHash] = {
        phase: modalPhase,
        playlist: {
            name: playlistData.name,
            tracks: playlistData.tracks
        },
        is_deezer_playlist: true,
        deezer_playlist_id: playlistId,
        discovery_progress: isAlreadyDiscovered ? 100 : 0,
        spotify_matches: isAlreadyDiscovered ? actualMatches : 0,
        spotifyMatches: isAlreadyDiscovered ? actualMatches : 0,
        spotify_total: playlistData.tracks.length,
        discovery_results: transformedResults,
        discoveryResults: transformedResults,
        discoveryProgress: isAlreadyDiscovered ? 100 : 0
    };

    if (!isAlreadyDiscovered && !isCurrentlyDiscovering) {
        try {
            console.log(`🔍 Starting Deezer discovery for: ${playlistData.name}`);

            const response = await fetch(`/api/deezer/discovery/start/${playlistId}`, {
                method: 'POST'
            });

            const result = await response.json();

            if (result.error) {
                console.error('Error starting Deezer discovery:', result.error);
                showToast(`Error starting discovery: ${result.error}`, 'error');
                return;
            }

            console.log('Deezer discovery started, beginning polling...');

            deezerPlaylistStates[playlistId].phase = 'discovering';
            updateDeezerCardPhase(playlistId, 'discovering');
            youtubePlaylistStates[fakeUrlHash].phase = 'discovering';

            startDeezerDiscoveryPolling(fakeUrlHash, playlistId);

        } catch (error) {
            console.error('Error starting Deezer discovery:', error);
            showToast(`Error starting discovery: ${error.message}`, 'error');
        }
    } else if (isCurrentlyDiscovering) {
        console.log(`🔄 Resuming Deezer discovery polling for: ${playlistData.name}`);
        startDeezerDiscoveryPolling(fakeUrlHash, playlistId);
    } else if (deezerCardState && deezerCardState.phase === 'syncing') {
        console.log(`🔄 Resuming Deezer sync polling for: ${playlistData.name}`);
        startDeezerSyncPolling(fakeUrlHash);
    } else {
        console.log('Using existing results - no need to re-discover');
    }

    openYouTubeDiscoveryModal(fakeUrlHash);
}

function startDeezerDiscoveryPolling(fakeUrlHash, playlistId) {
    console.log(`🔄 Starting Deezer discovery polling for: ${playlistId}`);

    if (activeYouTubePollers[fakeUrlHash]) {
        clearInterval(activeYouTubePollers[fakeUrlHash]);
    }

    // WebSocket subscription
    if (socketConnected) {
        socket.emit('discovery:subscribe', { ids: [playlistId] });
        _discoveryProgressCallbacks[playlistId] = (data) => {
            if (data.error) {
                if (activeYouTubePollers[fakeUrlHash]) { clearInterval(activeYouTubePollers[fakeUrlHash]); delete activeYouTubePollers[fakeUrlHash]; }
                socket.emit('discovery:unsubscribe', { ids: [playlistId] }); delete _discoveryProgressCallbacks[playlistId];
                return;
            }
            const transformed = {
                progress: data.progress, spotify_matches: data.spotify_matches, spotify_total: data.spotify_total,
                complete: data.complete,
                results: (data.results || []).map((r, i) => {
                    const isWingIt = r.wing_it_fallback || r.status_class === 'wing-it';
                    const isFound = !isWingIt && (r.status === 'found' || r.status === '✅ Found' || r.status_class === 'found' || r.spotify_data || r.spotify_track);
                    return {
                        index: i, yt_track: r.deezer_track ? r.deezer_track.name : 'Unknown',
                        yt_artist: r.deezer_track ? (r.deezer_track.artists ? r.deezer_track.artists.join(', ') : 'Unknown') : 'Unknown',
                        status: isWingIt ? '🎯 Wing It' : (isFound ? '✅ Found' : '❌ Not Found'),
                        status_class: isWingIt ? 'wing-it' : (isFound ? 'found' : 'not-found'),
                        spotify_track: r.spotify_data ? r.spotify_data.name : (r.spotify_track || '-'),
                        spotify_artist: r.spotify_data && r.spotify_data.artists
                            ? (Array.isArray(r.spotify_data.artists)
                                ? (r.spotify_data.artists
                                    .map(a => (typeof a === 'object' && a !== null) ? (a.name || '') : a)
                                    .filter(Boolean)
                                    .join(', ') || '-')
                                : r.spotify_data.artists)
                            : (r.spotify_artist || '-'),
                        spotify_album: r.spotify_data ? (typeof r.spotify_data.album === 'object' ? r.spotify_data.album.name : r.spotify_data.album) : (r.spotify_album || '-'),
                        spotify_data: r.spotify_data, spotify_id: r.spotify_id, manual_match: r.manual_match,
                        wing_it_fallback: isWingIt
                    };
                })
            };
            const st = youtubePlaylistStates[fakeUrlHash];
            if (st) {
                st.discovery_progress = data.progress; st.discoveryProgress = data.progress;
                st.spotify_matches = data.spotify_matches; st.spotifyMatches = data.spotify_matches;
                st.discovery_results = data.results; st.discoveryResults = transformed.results;
                st.phase = data.phase;
                updateYouTubeDiscoveryModal(fakeUrlHash, transformed);
            }
            if (deezerPlaylistStates[playlistId]) {
                deezerPlaylistStates[playlistId].phase = data.phase;
                deezerPlaylistStates[playlistId].discovery_results = data.results;
                deezerPlaylistStates[playlistId].spotify_matches = data.spotify_matches;
                deezerPlaylistStates[playlistId].discovery_progress = data.progress;
                updateDeezerCardPhase(playlistId, data.phase);
            }
            updateDeezerCardProgress(playlistId, data);
            if (data.complete) {
                if (activeYouTubePollers[fakeUrlHash]) { clearInterval(activeYouTubePollers[fakeUrlHash]); delete activeYouTubePollers[fakeUrlHash]; }
                socket.emit('discovery:unsubscribe', { ids: [playlistId] }); delete _discoveryProgressCallbacks[playlistId];
            }
        };
    }

    const pollInterval = setInterval(async () => {
        if (socketConnected) return;
        try {
            const response = await fetch(`/api/deezer/discovery/status/${playlistId}`);
            const status = await response.json();

            if (status.error) {
                console.error('Error polling Deezer discovery status:', status.error);
                clearInterval(pollInterval);
                delete activeYouTubePollers[fakeUrlHash];
                return;
            }

            const transformedStatus = {
                progress: status.progress,
                spotify_matches: status.spotify_matches,
                spotify_total: status.spotify_total,
                complete: status.complete,
                results: status.results.map((result, index) => {
                    const isFound = result.status === 'found' ||
                        result.status === '✅ Found' ||
                        result.status_class === 'found' ||
                        result.spotify_data ||
                        result.spotify_track;

                    return {
                        index: index,
                        yt_track: result.deezer_track ? result.deezer_track.name : 'Unknown',
                        yt_artist: result.deezer_track ? (result.deezer_track.artists ? result.deezer_track.artists.join(', ') : 'Unknown') : 'Unknown',
                        status: isFound ? '✅ Found' : '❌ Not Found',
                        status_class: isFound ? 'found' : 'not-found',
                        spotify_track: result.spotify_data ? result.spotify_data.name : (result.spotify_track || '-'),
                        spotify_artist: result.spotify_data && result.spotify_data.artists
                            ? (Array.isArray(result.spotify_data.artists)
                                ? (result.spotify_data.artists
                                    .map(a => (typeof a === 'object' && a !== null) ? (a.name || '') : a)
                                    .filter(Boolean)
                                    .join(', ') || '-')
                                : result.spotify_data.artists)
                            : (result.spotify_artist || '-'),
                        spotify_album: result.spotify_data ? (typeof result.spotify_data.album === 'object' ? result.spotify_data.album.name : result.spotify_data.album) : (result.spotify_album || '-'),
                        spotify_data: result.spotify_data,
                        spotify_id: result.spotify_id,
                        manual_match: result.manual_match
                    };
                })
            };

            const state = youtubePlaylistStates[fakeUrlHash];
            if (state) {
                state.discovery_progress = status.progress;
                state.discoveryProgress = status.progress;
                state.spotify_matches = status.spotify_matches;
                state.spotifyMatches = status.spotify_matches;
                state.discovery_results = status.results;
                state.discoveryResults = transformedStatus.results;
                state.phase = status.phase;

                updateYouTubeDiscoveryModal(fakeUrlHash, transformedStatus);

                if (deezerPlaylistStates[playlistId]) {
                    deezerPlaylistStates[playlistId].phase = status.phase;
                    deezerPlaylistStates[playlistId].discovery_results = status.results;
                    deezerPlaylistStates[playlistId].spotify_matches = status.spotify_matches;
                    deezerPlaylistStates[playlistId].discovery_progress = status.progress;
                    updateDeezerCardPhase(playlistId, status.phase);
                }

                updateDeezerCardProgress(playlistId, status);

                console.log(`🔄 Deezer discovery progress: ${status.progress}% (${status.spotify_matches}/${status.spotify_total} found)`);
            }

            if (status.complete) {
                console.log(`Deezer discovery complete: ${status.spotify_matches}/${status.spotify_total} tracks found`);
                clearInterval(pollInterval);
                delete activeYouTubePollers[fakeUrlHash];
            }

        } catch (error) {
            console.error('Error polling Deezer discovery:', error);
            clearInterval(pollInterval);
            delete activeYouTubePollers[fakeUrlHash];
        }
    }, 1000);

    activeYouTubePollers[fakeUrlHash] = pollInterval;
}

async function loadDeezerPlaylistStatesFromBackend() {
    try {
        console.log('🎵 Loading Deezer playlist states from backend...');

        const response = await fetch('/api/deezer/playlists/states');
        if (!response.ok) {
            const error = await response.json();
            throw new Error(error.error || 'Failed to fetch Deezer playlist states');
        }

        const data = await response.json();
        const states = data.states || [];

        console.log(`🎵 Found ${states.length} stored Deezer playlist states in backend`);

        if (states.length === 0) return;

        for (const stateInfo of states) {
            await applyDeezerPlaylistState(stateInfo);
        }

        // Rehydrate download modals for Deezer playlists in downloading/download_complete phases
        for (const stateInfo of states) {
            if ((stateInfo.phase === 'downloading' || stateInfo.phase === 'download_complete') &&
                stateInfo.converted_spotify_playlist_id && stateInfo.download_process_id) {

                const convertedPlaylistId = stateInfo.converted_spotify_playlist_id;

                if (!activeDownloadProcesses[convertedPlaylistId]) {
                    console.log(`Rehydrating download modal for Deezer playlist: ${stateInfo.playlist_id}`);
                    try {
                        const playlistData = deezerPlaylists.find(p => String(p.id) === String(stateInfo.playlist_id));
                        if (!playlistData) continue;

                        const spotifyTracks = deezerPlaylistStates[stateInfo.playlist_id]?.discovery_results
                            ?.filter(result => result.spotify_data)
                            ?.map(result => result.spotify_data) || [];

                        if (spotifyTracks.length > 0) {
                            await openDownloadMissingModalForTidal(
                                convertedPlaylistId,
                                playlistData.name,
                                spotifyTracks
                            );

                            const process = activeDownloadProcesses[convertedPlaylistId];
                            if (process) {
                                process.status = 'running';
                                process.batchId = stateInfo.download_process_id;
                                const beginBtn = document.getElementById(`begin-analysis-btn-${convertedPlaylistId}`);
                                const cancelBtn = document.getElementById(`cancel-all-btn-${convertedPlaylistId}`);
                                if (beginBtn) beginBtn.style.display = 'none';
                                if (cancelBtn) cancelBtn.style.display = 'inline-block';
                                startModalDownloadPolling(convertedPlaylistId);
                            }
                        }
                    } catch (error) {
                        console.error(`Error rehydrating Deezer download modal for ${stateInfo.playlist_id}:`, error);
                    }
                }
            }
        }

        console.log('Deezer playlist states loaded and applied');

    } catch (error) {
        console.error('Error loading Deezer playlist states:', error);
    }
}

async function applyDeezerPlaylistState(stateInfo) {
    const { playlist_id, phase, discovery_progress, spotify_matches, discovery_results, converted_spotify_playlist_id, download_process_id } = stateInfo;

    try {
        console.log(`🎵 Applying saved state for Deezer playlist: ${playlist_id}, Phase: ${phase}`);

        const playlistData = deezerPlaylists.find(p => String(p.id) === String(playlist_id));
        if (!playlistData) {
            console.warn(`Playlist data not found for state ${playlist_id} - skipping`);
            return;
        }

        if (!deezerPlaylistStates[playlist_id]) {
            deezerPlaylistStates[playlist_id] = {
                playlist: playlistData,
                phase: 'fresh'
            };
        }

        deezerPlaylistStates[playlist_id].phase = phase;
        deezerPlaylistStates[playlist_id].discovery_progress = discovery_progress;
        deezerPlaylistStates[playlist_id].spotify_matches = spotify_matches;
        deezerPlaylistStates[playlist_id].discovery_results = discovery_results;
        deezerPlaylistStates[playlist_id].convertedSpotifyPlaylistId = converted_spotify_playlist_id;
        deezerPlaylistStates[playlist_id].download_process_id = download_process_id;
        deezerPlaylistStates[playlist_id].playlist = playlistData;

        if (phase !== 'fresh' && phase !== 'discovering') {
            try {
                const stateResponse = await fetch(`/api/deezer/state/${playlist_id}`);
                if (stateResponse.ok) {
                    const fullState = await stateResponse.json();
                    if (fullState.discovery_results && deezerPlaylistStates[playlist_id]) {
                        deezerPlaylistStates[playlist_id].discovery_results = fullState.discovery_results;
                        deezerPlaylistStates[playlist_id].discovery_progress = fullState.discovery_progress;
                        deezerPlaylistStates[playlist_id].spotify_matches = fullState.spotify_matches;
                        deezerPlaylistStates[playlist_id].convertedSpotifyPlaylistId = fullState.converted_spotify_playlist_id;
                        deezerPlaylistStates[playlist_id].download_process_id = fullState.download_process_id;
                    }
                }
            } catch (error) {
                console.warn(`Error fetching full discovery results for Deezer playlist ${playlistData.name}:`, error.message);
            }
        }

        updateDeezerCardPhase(playlist_id, phase);

        if (phase === 'discovered' && deezerPlaylistStates[playlist_id]) {
            const progressInfo = {
                spotify_total: playlistData.track_count || playlistData.tracks?.length || 0,
                spotify_matches: deezerPlaylistStates[playlist_id].spotify_matches || 0
            };
            updateDeezerCardProgress(playlist_id, progressInfo);
        }

        if (phase === 'discovering') {
            const fakeUrlHash = `deezer_${playlist_id}`;
            startDeezerDiscoveryPolling(fakeUrlHash, playlist_id);
        } else if (phase === 'syncing') {
            const fakeUrlHash = `deezer_${playlist_id}`;
            startDeezerSyncPolling(fakeUrlHash);
        }

    } catch (error) {
        console.error(`Error applying Deezer playlist state for ${playlist_id}:`, error);
    }
}

function updateDeezerCardPhase(playlistId, phase) {
    const state = deezerPlaylistStates[playlistId];
    if (!state) return;

    state.phase = phase;

    const card = document.getElementById(`deezer-card-${playlistId}`);
    if (card) {
        const newCardHtml = createDeezerCard(state.playlist);
        card.outerHTML = newCardHtml;

        const newCard = document.getElementById(`deezer-card-${playlistId}`);
        if (newCard) {
            newCard.addEventListener('click', () => handleDeezerCardClick(playlistId));
        }

        if ((phase === 'syncing' || phase === 'sync_complete') && state.lastSyncProgress) {
            setTimeout(() => {
                updateDeezerCardSyncProgress(playlistId, state.lastSyncProgress);
            }, 0);
        }
    }
}

function updateDeezerCardProgress(playlistId, progress) {
    const state = deezerPlaylistStates[playlistId];
    if (!state) return;

    const card = document.getElementById(`deezer-card-${playlistId}`);
    if (!card) return;

    const progressElement = card.querySelector('.playlist-card-progress');
    if (!progressElement) return;

    progressElement.classList.remove('hidden');

    const total = progress.spotify_total || 0;
    const matches = progress.spotify_matches || 0;

    if (total > 0) {
        progressElement.innerHTML = `
            <div class="playlist-card-sync-status">
                <span class="sync-stat matched-tracks">✓ ${matches}</span>
                <span class="sync-separator">/</span>
                <span class="sync-stat total-tracks">♪ ${total}</span>
            </div>
        `;
    }
}

// ===============================
// DEEZER SYNC FUNCTIONALITY
// ===============================

async function startDeezerPlaylistSync(urlHash) {
    try {
        console.log('🎵 Starting Deezer playlist sync:', urlHash);

        const state = youtubePlaylistStates[urlHash];
        if (!state || !state.is_deezer_playlist) {
            console.error('Invalid Deezer playlist state for sync');
            return;
        }

        const playlistId = state.deezer_playlist_id;
        const response = await fetch(`/api/deezer/sync/start/${playlistId}`, {
            method: 'POST'
        });

        const result = await response.json();

        if (result.error) {
            showToast(`Error starting sync: ${result.error}`, 'error');
            return;
        }

        const syncPlaylistId = result.sync_playlist_id;
        if (state) state.syncPlaylistId = syncPlaylistId;

        updateDeezerCardPhase(playlistId, 'syncing');
        updateDeezerModalButtons(urlHash, 'syncing');

        startDeezerSyncPolling(urlHash, syncPlaylistId);

        showToast('Deezer playlist sync started!', 'success');

    } catch (error) {
        console.error('Error starting Deezer sync:', error);
        showToast(`Error starting sync: ${error.message}`, 'error');
    }
}

function startDeezerSyncPolling(urlHash, syncPlaylistId) {
    if (activeYouTubePollers[urlHash]) {
        clearInterval(activeYouTubePollers[urlHash]);
    }

    const state = youtubePlaylistStates[urlHash];
    const playlistId = state.deezer_playlist_id;

    syncPlaylistId = syncPlaylistId || (state && state.syncPlaylistId);

    // WebSocket subscription
    if (socketConnected && syncPlaylistId) {
        socket.emit('sync:subscribe', { playlist_ids: [syncPlaylistId] });
        _syncProgressCallbacks[syncPlaylistId] = (data) => {
            const progress = data.progress || {};
            updateDeezerCardSyncProgress(playlistId, progress);
            updateDeezerModalSyncProgress(urlHash, progress);

            if (data.status === 'finished') {
                if (activeYouTubePollers[urlHash]) { clearInterval(activeYouTubePollers[urlHash]); delete activeYouTubePollers[urlHash]; }
                socket.emit('sync:unsubscribe', { playlist_ids: [syncPlaylistId] });
                delete _syncProgressCallbacks[syncPlaylistId];
                if (deezerPlaylistStates[playlistId]) deezerPlaylistStates[playlistId].phase = 'sync_complete';
                if (youtubePlaylistStates[urlHash]) youtubePlaylistStates[urlHash].phase = 'sync_complete';
                updateDeezerCardPhase(playlistId, 'sync_complete');
                updateDeezerModalButtons(urlHash, 'sync_complete');
                showToast('Deezer playlist sync complete!', 'success');
            } else if (data.status === 'error' || data.status === 'cancelled') {
                if (activeYouTubePollers[urlHash]) { clearInterval(activeYouTubePollers[urlHash]); delete activeYouTubePollers[urlHash]; }
                socket.emit('sync:unsubscribe', { playlist_ids: [syncPlaylistId] });
                delete _syncProgressCallbacks[syncPlaylistId];
                if (deezerPlaylistStates[playlistId]) deezerPlaylistStates[playlistId].phase = 'discovered';
                if (youtubePlaylistStates[urlHash]) youtubePlaylistStates[urlHash].phase = 'discovered';
                updateDeezerCardPhase(playlistId, 'discovered');
                updateDeezerModalButtons(urlHash, 'discovered');
                showToast(`Sync failed: ${data.error || 'Unknown error'}`, 'error');
            }
        };
    }

    const pollFunction = async () => {
        if (socketConnected) return;
        try {
            const response = await fetch(`/api/deezer/sync/status/${playlistId}`);
            const status = await response.json();

            if (status.error) {
                console.error('Error polling Deezer sync status:', status.error);
                clearInterval(pollInterval);
                delete activeYouTubePollers[urlHash];
                return;
            }

            updateDeezerCardSyncProgress(playlistId, status.progress);
            updateDeezerModalSyncProgress(urlHash, status.progress);

            if (status.complete) {
                clearInterval(pollInterval);
                delete activeYouTubePollers[urlHash];
                if (deezerPlaylistStates[playlistId]) deezerPlaylistStates[playlistId].phase = 'sync_complete';
                if (youtubePlaylistStates[urlHash]) youtubePlaylistStates[urlHash].phase = 'sync_complete';
                updateDeezerCardPhase(playlistId, 'sync_complete');
                updateDeezerModalButtons(urlHash, 'sync_complete');
                showToast('Deezer playlist sync complete!', 'success');
            } else if (status.sync_status === 'error') {
                clearInterval(pollInterval);
                delete activeYouTubePollers[urlHash];
                if (deezerPlaylistStates[playlistId]) deezerPlaylistStates[playlistId].phase = 'discovered';
                if (youtubePlaylistStates[urlHash]) youtubePlaylistStates[urlHash].phase = 'discovered';
                updateDeezerCardPhase(playlistId, 'discovered');
                updateDeezerModalButtons(urlHash, 'discovered');
                showToast(`Sync failed: ${status.error || 'Unknown error'}`, 'error');
            }
        } catch (error) {
            console.error('Error polling Deezer sync:', error);
            if (activeYouTubePollers[urlHash]) {
                clearInterval(activeYouTubePollers[urlHash]);
                delete activeYouTubePollers[urlHash];
            }
        }
    };

    if (!socketConnected) pollFunction();

    const pollInterval = setInterval(pollFunction, 1000);
    activeYouTubePollers[urlHash] = pollInterval;
}

async function cancelDeezerSync(urlHash) {
    try {
        console.log('Cancelling Deezer sync:', urlHash);

        const state = youtubePlaylistStates[urlHash];
        if (!state || !state.is_deezer_playlist) {
            console.error('Invalid Deezer playlist state');
            return;
        }

        const playlistId = state.deezer_playlist_id;
        const response = await fetch(`/api/deezer/sync/cancel/${playlistId}`, {
            method: 'POST'
        });

        const result = await response.json();

        if (result.error) {
            showToast(`Error cancelling sync: ${result.error}`, 'error');
            return;
        }

        if (activeYouTubePollers[urlHash]) {
            clearInterval(activeYouTubePollers[urlHash]);
            delete activeYouTubePollers[urlHash];
        }

        const syncId = state && state.syncPlaylistId;
        if (syncId && _syncProgressCallbacks[syncId]) {
            if (socketConnected) socket.emit('sync:unsubscribe', { playlist_ids: [syncId] });
            delete _syncProgressCallbacks[syncId];
        }

        updateDeezerCardPhase(playlistId, 'discovered');
        updateDeezerModalButtons(urlHash, 'discovered');

        showToast('Deezer sync cancelled', 'info');

    } catch (error) {
        console.error('Error cancelling Deezer sync:', error);
        showToast(`Error cancelling sync: ${error.message}`, 'error');
    }
}

function updateDeezerCardSyncProgress(playlistId, progress) {
    const state = deezerPlaylistStates[playlistId];
    if (!state || !state.playlist || !progress) return;

    state.lastSyncProgress = progress;

    const card = document.getElementById(`deezer-card-${playlistId}`);
    if (!card) return;

    const progressElement = card.querySelector('.playlist-card-progress');

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

    if (statusCounterHTML) {
        progressElement.innerHTML = statusCounterHTML;
    }
}

function updateDeezerModalSyncProgress(urlHash, progress) {
    const statusDisplay = document.getElementById(`deezer-sync-status-${urlHash}`);
    if (!statusDisplay || !progress) return;

    const totalEl = document.getElementById(`deezer-total-${urlHash}`);
    const matchedEl = document.getElementById(`deezer-matched-${urlHash}`);
    const failedEl = document.getElementById(`deezer-failed-${urlHash}`);
    const percentageEl = document.getElementById(`deezer-percentage-${urlHash}`);

    const total = progress.total_tracks || 0;
    const matched = progress.matched_tracks || 0;
    const failed = progress.failed_tracks || 0;

    if (totalEl) totalEl.textContent = total;
    if (matchedEl) matchedEl.textContent = matched;
    if (failedEl) failedEl.textContent = failed;

    if (total > 0) {
        const processed = matched + failed;
        const percentage = Math.round((processed / total) * 100);
        if (percentageEl) percentageEl.textContent = percentage;
    }
}

function updateDeezerModalButtons(urlHash, phase) {
    const modal = document.getElementById(`youtube-discovery-modal-${urlHash}`);
    if (!modal) return;

    const footerLeft = modal.querySelector('.modal-footer-left');
    if (footerLeft) {
        footerLeft.innerHTML = getModalActionButtons(urlHash, phase);
    }
}

async function startDeezerDownloadMissing(urlHash) {
    try {
        console.log('🔍 Starting download missing tracks for Deezer playlist:', urlHash);

        const state = youtubePlaylistStates[urlHash];
        if (!state || !state.is_deezer_playlist) {
            console.error('Invalid Deezer playlist state for download');
            return;
        }

        const discoveryResults = state.discoveryResults || state.discovery_results;

        if (!discoveryResults) {
            showToast('No discovery results available for download', 'error');
            return;
        }

        const spotifyTracks = [];
        for (const result of discoveryResults) {
            if (result.spotify_data) {
                spotifyTracks.push(result.spotify_data);
            } else if (result.spotify_track && result.status_class === 'found') {
                const albumData = result.spotify_album || 'Unknown Album';
                const albumObject = typeof albumData === 'object' && albumData !== null
                    ? albumData
                    : {
                        name: typeof albumData === 'string' ? albumData : 'Unknown Album',
                        album_type: 'album',
                        images: []
                    };

                spotifyTracks.push({
                    id: result.spotify_id || 'unknown',
                    name: result.spotify_track || 'Unknown Track',
                    artists: result.spotify_artist ? [result.spotify_artist] : ['Unknown Artist'],
                    album: albumObject,
                    duration_ms: 0
                });
            }
        }

        if (spotifyTracks.length === 0) {
            showToast('No Spotify matches found for download', 'error');
            return;
        }

        const virtualPlaylistId = `deezer_${state.deezer_playlist_id}`;
        const playlistName = state.playlist.name;

        state.convertedSpotifyPlaylistId = virtualPlaylistId;

        const discoveryModal = document.getElementById(`youtube-discovery-modal-${urlHash}`);
        if (discoveryModal) {
            discoveryModal.classList.add('hidden');
        }

        await openDownloadMissingModalForTidal(virtualPlaylistId, playlistName, spotifyTracks);

    } catch (error) {
        console.error('Error starting download missing tracks:', error);
        showToast(`Error starting downloads: ${error.message}`, 'error');
    }
}


// ===============================
// SYNC PAGE FUNCTIONALITY (REDESIGNED)
// ===============================

/**
 * Navigate to the Sync page and activate a specific tab.
 * Works from any page. If already on the sync page, just switches the tab.
 * @param {string} tabId - Tab data-tab value (e.g. 'discover', 'spotify', 'mirrored')
 * @param {object} [opts] - Options
 * @param {string} [opts.highlight] - Element ID to scroll to and briefly highlight
 * @param {string} [opts.autoSync] - Discover playlist type to auto-trigger sync on
 * @param {boolean} [opts.forceDownload] - Pass force_download_all when auto-syncing
 */
function navigateToSyncTab(tabId, opts) {
    window._pendingSyncTabAction = { tabId, ...(opts || {}) };
    if (typeof currentPage !== 'undefined' && currentPage === 'sync') {
        _applySyncTabAction();
    } else {
        navigateToPage('sync');
    }
}

function _applySyncTabAction() {
    const action = window._pendingSyncTabAction;
    if (!action) return;
    window._pendingSyncTabAction = null;
    const tabId = action.tabId;

    // Click the target tab button to trigger normal tab-switch logic
    const btn = document.querySelector(`.sync-tab-button[data-tab="${tabId}"]`);
    if (btn && !btn.classList.contains('active')) {
        btn.click();
    }

    // Wait for lazy-loaded content, then highlight / auto-sync
    const apply = () => {
        if (action.highlight) {
            const el = document.getElementById(action.highlight);
            if (el) {
                el.scrollIntoView({ behavior: 'smooth', block: 'center' });
                el.classList.add('discover-sync-card-highlight');
                setTimeout(() => el.classList.remove('discover-sync-card-highlight'), 2500);
            }
        }
        if (action.autoSync) {
            syncDiscoverPlaylistFromTab(action.autoSync, action.autoSyncName || action.autoSync);
        }
    };
    // Wait for lazy-loaded content to appear before applying
    let attempts = 0;
    const maxAttempts = 20; // 20 * 200ms = 4s max
    const waitAndApply = () => {
        const ready = !action.highlight || document.getElementById(action.highlight);
        if (ready || attempts >= maxAttempts) {
            apply();
        } else {
            attempts++;
            setTimeout(waitAndApply, 200);
        }
    };
    setTimeout(waitAndApply, 200);
}

function initializeSyncPage() {
    // Logic for tab switching
    const tabButtons = document.querySelectorAll('.sync-tab-button');
    const syncSidebar = document.querySelector('.sync-sidebar');
    const syncContentArea = document.querySelector('.sync-content-area');

    tabButtons.forEach(button => {
        button.addEventListener('click', () => {
            const tabId = button.dataset.tab;
            const previousActiveTab = document.querySelector('.sync-tab-button.active');
            const previousTabId = previousActiveTab ? previousActiveTab.dataset.tab : null;

            // Update button active state
            tabButtons.forEach(btn => btn.classList.remove('active'));
            button.classList.add('active');

            // Update content active state
            document.querySelectorAll('.sync-tab-content').forEach(content => {
                content.classList.remove('active');
            });
            document.getElementById(`${tabId}-tab-content`).classList.add('active');

            // Show/hide sidebar based on active tab (skip on mobile where sidebar is always hidden)
            if (syncSidebar && syncContentArea) {
                const isMobile = window.innerWidth <= 1300;
                // Sidebar always hidden by default — shown only when sync is active
                syncSidebar.style.display = 'none';
                syncContentArea.style.gridTemplateColumns = '1fr';
            }

            // Auto-load Deezer ARL playlists on first tab activation
            if (tabId === 'deezer' && !deezerArlPlaylistsLoaded) {
                // Check ARL status first
                fetch('/api/deezer/arl-status').then(r => r.json()).then(data => {
                    const container = document.getElementById('deezer-arl-playlist-container');
                    if (data.authenticated) {
                        loadDeezerArlPlaylists();
                    } else if (container) {
                        container.innerHTML = `<div class="playlist-placeholder">Deezer ARL not configured. Add your ARL token in Settings &gt; Downloads to see your playlists here.</div>`;
                    }
                }).catch(() => { });
            }

            // Auto-load mirrored playlists on first tab activation
            if (tabId === 'mirrored' && !mirroredPlaylistsLoaded) {
                loadMirroredPlaylists();
            }

            // Auto-load SoulSync Discover playlists on first tab activation
            if (tabId === 'discover' && !discoverSyncPlaylistsLoaded) {
                loadDiscoverSyncPlaylists();
            }

            // Auto-load server playlists on first tab activation
            if (tabId === 'server' && !window._serverPlaylistsLoaded) {
                window._serverPlaylistsLoaded = true;
                loadServerPlaylists();
            }

            if (previousTabId === 'beatport' && tabId !== 'beatport') {
                cleanupBeatportContent();
            }

            // Lazily load Beatport content the first time the Beatport tab is opened
            if (tabId === 'beatport') {
                ensureBeatportContentLoaded();
            }
        });
    });

    // If the Beatport tab is already active when Sync initializes, load it now.
    const activeBeatportTab = document.querySelector('.sync-tab-button.active[data-tab="beatport"]');
    if (activeBeatportTab) {
        ensureBeatportContentLoaded();
    }

    // Logic for the Spotify refresh button
    const refreshBtn = document.getElementById('spotify-refresh-btn');
    if (refreshBtn) {
        // Remove any old listeners to be safe, then add the new one
        refreshBtn.removeEventListener('click', loadSpotifyPlaylists);
        refreshBtn.addEventListener('click', loadSpotifyPlaylists);
    }

    // Logic for the Tidal refresh button
    const tidalRefreshBtn = document.getElementById('tidal-refresh-btn');
    if (tidalRefreshBtn) {
        tidalRefreshBtn.removeEventListener('click', loadTidalPlaylists);
        tidalRefreshBtn.addEventListener('click', loadTidalPlaylists);
    }

    // Logic for the Deezer ARL refresh button
    const deezerArlRefreshBtn = document.getElementById('deezer-arl-refresh-btn');
    if (deezerArlRefreshBtn) {
        deezerArlRefreshBtn.removeEventListener('click', loadDeezerArlPlaylists);
        deezerArlRefreshBtn.addEventListener('click', loadDeezerArlPlaylists);
    }

    // Logic for the Deezer Link parse button
    const deezerParseBtn = document.getElementById('deezer-parse-btn');
    if (deezerParseBtn) {
        deezerParseBtn.addEventListener('click', loadDeezerPlaylist);
    }
    // Also allow Enter key in the Deezer input
    const deezerUrlInput = document.getElementById('deezer-url-input');
    if (deezerUrlInput) {
        deezerUrlInput.addEventListener('keypress', (e) => {
            if (e.key === 'Enter') loadDeezerPlaylist();
        });
    }

    // Logic for the Mirrored refresh button
    const mirroredRefreshBtn = document.getElementById('mirrored-refresh-btn');
    if (mirroredRefreshBtn) {
        mirroredRefreshBtn.addEventListener('click', loadMirroredPlaylists);
    }

    // Initialize import file tab
    _initImportFileTab();

    // Logic for the Beatport clear button
    const beatportClearBtn = document.getElementById('beatport-clear-btn');
    if (beatportClearBtn) {
        beatportClearBtn.addEventListener('click', clearBeatportPlaylists);
        // Set initial clear button state
        updateBeatportClearButtonState();
    }

    // Logic for Beatport nested tabs
    const beatportTabButtons = document.querySelectorAll('.beatport-tab-button');
    beatportTabButtons.forEach(button => {
        button.addEventListener('click', () => {
            const tabId = button.dataset.beatportTab;

            // Update button active state
            beatportTabButtons.forEach(btn => btn.classList.remove('active'));
            button.classList.add('active');

            // Update content active state
            document.querySelectorAll('.beatport-tab-content').forEach(content => {
                content.classList.remove('active');
            });
            document.getElementById(`beatport-${tabId}-content`).classList.add('active');

            // Initialize rebuild content lazily when the rebuild tab is selected
            if (tabId === 'rebuild') {
                ensureBeatportContentLoaded();
            }
        });
    });

    // Logic for Homepage Genre Explorer card
    const genreExplorerCard = document.querySelector('[data-action="show-genres"]');
    if (genreExplorerCard) {
        genreExplorerCard.addEventListener('click', () => {
            console.log('🎵 Genre Explorer card clicked');
            showBeatportSubView('genres');
            loadBeatportGenres();
        });
    }

    // Setup homepage chart handlers (following genre page pattern to prevent duplicates)
    setupHomepageChartTypeHandlers();

    // Load homepage chart collections automatically (disabled since Browse Charts tab is hidden)
    // loadDJChartsInline();
    // loadFeaturedChartsInline();

    // Logic for Beatport breadcrumb back buttons
    const beatportBackButtons = document.querySelectorAll('.breadcrumb-back');
    beatportBackButtons.forEach(button => {
        button.addEventListener('click', () => {
            // Handle different back button types
            if (button.id === 'genre-detail-back') {
                showBeatportGenresView();
            } else if (button.id === 'genre-charts-list-back') {
                showBeatportGenreDetailViewFromBack();
            } else {
                showBeatportMainView();
            }
        });
    });

    // Logic for Beatport chart items
    const beatportChartItems = document.querySelectorAll('.beatport-chart-item');
    beatportChartItems.forEach(item => {
        item.addEventListener('click', () => {
            const chartType = item.dataset.chartType;
            const chartId = item.dataset.chartId;
            const chartName = item.dataset.chartName;
            const chartEndpoint = item.dataset.chartEndpoint;
            handleBeatportChartClick(chartType, chartId, chartName, chartEndpoint);
        });
    });

    // Logic for Beatport genre items
    const beatportGenreItems = document.querySelectorAll('.beatport-genre-item');
    beatportGenreItems.forEach(item => {
        item.addEventListener('click', () => {
            const genreSlug = item.dataset.genreSlug;
            const genreId = item.dataset.genreId;
            handleBeatportGenreClick(genreSlug, genreId);
        });
    });

    // Logic for Rebuild page Top 10 containers - Beatport Top 10
    const beatportTop10Container = document.getElementById('beatport-top10-list');
    if (beatportTop10Container) {
        beatportTop10Container.addEventListener('click', () => {
            console.log('🎵 Beatport Top 10 container clicked on rebuild page');
            handleRebuildBeatportTop10Click();
        });
    }

    // Logic for Rebuild page Top 10 containers - Hype Top 10
    const beatportHype10Container = document.getElementById('beatport-hype10-list');
    if (beatportHype10Container) {
        beatportHype10Container.addEventListener('click', () => {
            console.log('🔥 Hype Top 10 container clicked on rebuild page');
            handleRebuildHypeTop10Click();
        });
    }

    // Logic for Rebuild page Hero Slider - individual slide click handlers will be set up in populateBeatportSlider
    // Container-level click handler removed to allow individual slide clicks like top 10 releases

    // Logic for the Start Sync button
    const startSyncBtn = document.getElementById('start-sync-btn');
    if (startSyncBtn) {
        startSyncBtn.addEventListener('click', startSequentialSync);
    }

    // Logic for the YouTube parse button
    const youtubeParseBtn = document.getElementById('youtube-parse-btn');
    if (youtubeParseBtn) {
        youtubeParseBtn.addEventListener('click', parseYouTubePlaylist);
    }

    // Logic for YouTube URL input (Enter key support)
    const youtubeUrlInput = document.getElementById('youtube-url-input');
    if (youtubeUrlInput) {
        youtubeUrlInput.addEventListener('keypress', (e) => {
            if (e.key === 'Enter') {
                parseYouTubePlaylist();
            }
        });
    }

    // Logic for Spotify Public parse button
    const spotifyPublicParseBtn = document.getElementById('spotify-public-parse-btn');
    if (spotifyPublicParseBtn) {
        spotifyPublicParseBtn.addEventListener('click', parseSpotifyPublicUrl);
    }

    // Logic for Spotify Public URL input (Enter key support)
    const spotifyPublicUrlInput = document.getElementById('spotify-public-url-input');
    if (spotifyPublicUrlInput) {
        spotifyPublicUrlInput.addEventListener('keypress', (e) => {
            if (e.key === 'Enter') {
                parseSpotifyPublicUrl();
            }
        });
    }

    // Logic for Beatport Top 100 button
    const beatportTop100Btn = document.getElementById('beatport-top100-btn');
    if (beatportTop100Btn) {
        beatportTop100Btn.addEventListener('click', handleBeatportTop100Click);
    }

    // Logic for Hype Top 100 button
    const hypeTop100Btn = document.getElementById('hype-top100-btn');
    if (hypeTop100Btn) {
        hypeTop100Btn.addEventListener('click', handleHypeTop100Click);
    }

    // Initialize live log viewer
    initializeLiveLogViewer();
}


// --- Event Handlers ---

// --- Find and REPLACE the existing handleDbUpdateButtonClick function ---

async function handleDbUpdateButtonClick() {
    const button = document.getElementById('db-update-button');
    const currentAction = button.textContent;

    if (currentAction === 'Update Database') {
        const refreshSelect = document.getElementById('db-refresh-type');
        const isFullRefresh = refreshSelect.value === 'full';

        if (isFullRefresh) {
            // Replicates the QMessageBox confirmation from the GUI
            const confirmed = await showConfirmDialog({ title: 'Full Refresh', message: 'This will clear and rebuild the database for the active server. It can take a long time.\n\nAre you sure you want to proceed?', confirmText: 'Proceed' });
            if (!confirmed) return;
        }

        try {
            button.disabled = true;
            button.textContent = 'Starting...';
            const response = await fetch('/api/database/update', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ full_refresh: isFullRefresh })
            });

            if (response.ok) {
                showToast('Database update started!', 'success');
                // Start polling immediately to get live status
                checkAndUpdateDbProgress();
            } else {
                const errorData = await response.json();
                showToast(`Error: ${errorData.error}`, 'error');
                button.disabled = false;
                button.textContent = 'Update Database';
            }
        } catch (error) {
            showToast('Failed to start update process.', 'error');
            button.disabled = false;
            button.textContent = 'Update Database';
        }

    } else { // "Stop Update"
        try {
            const response = await fetch('/api/database/update/stop', { method: 'POST' });
            if (response.ok) {
                showToast('Stop request sent.', 'info');
            } else {
                showToast('Failed to send stop request.', 'error');
            }
        } catch (error) {
            showToast('Error sending stop request.', 'error');
        }
    }
}

async function handleWishlistButtonClick() {
    try {
        const playlistId = 'wishlist';

        console.log('🎵 [Wishlist Button] User clicked wishlist button - checking server state first');

        // STEP 1: Always check server state first to detect any active wishlist processes
        const response = await fetch('/api/active-processes');
        if (!response.ok) {
            throw new Error(`Failed to fetch active processes: ${response.status}`);
        }

        const data = await response.json();
        const processes = data.active_processes || [];
        const serverWishlistProcess = processes.find(p => p.playlist_id === playlistId);

        // STEP 2: Handle active server process - show current state immediately
        if (serverWishlistProcess) {
            console.log('🎯 [Wishlist Button] Server has active wishlist process:', {
                batch_id: serverWishlistProcess.batch_id,
                phase: serverWishlistProcess.phase,
                auto_initiated: serverWishlistProcess.auto_initiated,
                should_show: serverWishlistProcess.should_show_modal
            });

            // Clear any user-closed state since user explicitly requested to see modal
            WishlistModalState.clearUserClosed();

            // Check if we need to create/sync the frontend modal
            const clientWishlistProcess = activeDownloadProcesses[playlistId];
            const needsRehydration = !clientWishlistProcess ||
                clientWishlistProcess.batchId !== serverWishlistProcess.batch_id ||
                !clientWishlistProcess.modalElement ||
                !document.body.contains(clientWishlistProcess.modalElement);

            if (needsRehydration) {
                console.log('🔄 [Wishlist Button] Frontend modal needs sync/creation');
                await rehydrateModal(serverWishlistProcess, true); // user-requested = true
            } else {
                console.log('✅ [Wishlist Button] Frontend modal already synced, showing existing modal');
                clientWishlistProcess.modalElement.style.display = 'flex';
                WishlistModalState.setVisible();
            }
            return;
        }

        // STEP 3: No active server process - check wishlist count and create fresh modal
        console.log('📭 [Wishlist Button] No active server process, checking wishlist content');

        const countResponse = await fetch('/api/wishlist/count');
        if (!countResponse.ok) {
            throw new Error(`Failed to fetch wishlist count: ${countResponse.status}`);
        }

        const countData = await countResponse.json();
        if (countData.count === 0) {
            showToast('Wishlist is empty. No tracks to download.', 'info');
            return;
        }

        // STEP 4: Open wishlist overview modal (NEW - category selection)
        console.log(`🆕 [Wishlist Button] Opening wishlist overview for ${countData.count} tracks`);
        await openWishlistOverviewModal();

    } catch (error) {
        console.error('❌ [Wishlist Button] Error handling wishlist button click:', error);
        showToast(`Error opening wishlist: ${error.message}`, 'error');
    }
}

async function cleanupWishlist(playlistId) {
    try {
        // Show information dialog
        const confirmed = await showConfirmDialog({
            title: 'Cleanup Wishlist',
            message: 'This will check all wishlist tracks against your music library and automatically remove any tracks that already exist in your database.\n\nThis is a safe operation that only removes tracks you already have. Continue with cleanup?'
        });

        if (!confirmed) {
            return;
        }

        // Disable the cleanup button during the operation
        const cleanupBtn = document.getElementById(`cleanup-wishlist-btn-${playlistId}`);
        if (cleanupBtn) {
            cleanupBtn.disabled = true;
            cleanupBtn.textContent = '🧹 Cleaning...';
        }

        const response = await fetch('/api/wishlist/cleanup', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            }
        });

        const result = await response.json();

        if (result.success) {
            const removedCount = result.removed_count || 0;
            const processedCount = result.processed_count || 0;

            if (removedCount > 0) {
                showToast(`Wishlist cleanup completed: ${removedCount} tracks removed (${processedCount} checked)`, 'success');

                // Refresh the modal content to show updated state
                setTimeout(() => {
                    openDownloadMissingWishlistModal();
                }, 500);

                // Update the wishlist count in the main dashboard
                await updateWishlistCount();
            } else {
                showToast(`Wishlist cleanup completed: No tracks to remove (${processedCount} checked)`, 'info');
            }
        } else {
            showToast(`Error cleaning wishlist: ${result.error}`, 'error');
        }

    } catch (error) {
        console.error('Error cleaning wishlist:', error);
        showToast(`Error cleaning wishlist: ${error.message}`, 'error');
    } finally {
        // Re-enable the cleanup button
        const cleanupBtn = document.getElementById(`cleanup-wishlist-btn-${playlistId}`);
        if (cleanupBtn) {
            cleanupBtn.disabled = false;
            cleanupBtn.textContent = '🧹 Cleanup Wishlist';
        }
    }
}

async function clearWishlist(playlistId) {
    try {
        // Show confirmation dialog
        const confirmed = await showConfirmDialog({
            title: 'Clear Wishlist',
            message: 'Are you sure you want to clear the entire wishlist?\n\nThis will permanently remove all failed tracks from the wishlist. This action cannot be undone.',
            confirmText: 'Clear All',
            destructive: true
        });

        if (!confirmed) {
            return;
        }

        // Disable the clear button during the operation
        const clearBtn = document.getElementById(`clear-wishlist-btn-${playlistId}`);
        if (clearBtn) {
            clearBtn.disabled = true;
            clearBtn.textContent = 'Clearing...';
        }

        // Call the clear API endpoint
        const response = await fetch('/api/wishlist/clear', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            }
        });

        const result = await response.json();

        if (result.success) {
            showToast('Wishlist cleared successfully', 'success');

            // Close the modal since there are no more tracks
            closeDownloadMissingModal(playlistId);

            // Update the wishlist count in the main dashboard
            await updateWishlistCount();

        } else {
            showToast(`Failed to clear wishlist: ${result.error || 'Unknown error'}`, 'error');
        }

    } catch (error) {
        console.error('Error clearing wishlist:', error);
        showToast(`Error clearing wishlist: ${error.message}`, 'error');
    } finally {
        // Re-enable the clear button
        const clearBtn = document.getElementById(`clear-wishlist-btn-${playlistId}`);
        if (clearBtn) {
            clearBtn.disabled = false;
            clearBtn.textContent = '🗑️ Clear Wishlist';
        }
    }
}


// ===============================
// BEATPORT CHARTS FUNCTIONALITY
// ===============================

function updateBeatportClearButtonState() {
    const clearBtn = document.getElementById('beatport-clear-btn');
    if (!clearBtn) return;

    // Check if any Beatport cards are in active states
    const activeCharts = Object.values(beatportChartStates).filter(state =>
        state.phase === 'discovering' || state.phase === 'syncing' || state.phase === 'downloading'
    );

    const hasActiveCharts = activeCharts.length > 0;
    const hasAnyCharts = Object.keys(beatportChartStates).length > 0;

    if (!hasAnyCharts) {
        // No charts at all
        clearBtn.disabled = true;
        clearBtn.textContent = '🗑️ Clear';
        clearBtn.style.opacity = '0.5';
        clearBtn.style.cursor = 'not-allowed';
        clearBtn.title = 'No Beatport charts to clear';
    } else if (hasActiveCharts) {
        // Has charts but some are active
        clearBtn.disabled = true;
        clearBtn.textContent = '🚫 Clear Blocked';
        clearBtn.style.opacity = '0.6';
        clearBtn.style.cursor = 'not-allowed';
        const activeNames = activeCharts.map(state => state.chart?.name || 'Unknown').join(', ');
        clearBtn.title = `Cannot clear: ${activeCharts.length} chart(s) are currently active: ${activeNames}`;
    } else {
        // Has charts and none are active
        clearBtn.disabled = false;
        clearBtn.textContent = '🗑️ Clear';
        clearBtn.style.opacity = '1';
        clearBtn.style.cursor = 'pointer';
        clearBtn.title = 'Clear all Beatport charts';
    }
}

async function clearBeatportPlaylists() {
    const container = document.getElementById('beatport-playlist-container');
    const clearBtn = document.getElementById('beatport-clear-btn');

    if (Object.keys(beatportChartStates).length === 0) {
        showToast('No Beatport playlists to clear', 'info');
        return;
    }

    // Check if any Beatport cards are in active states (discovering, syncing, or downloading)
    const activeCharts = Object.values(beatportChartStates).filter(state =>
        state.phase === 'discovering' || state.phase === 'syncing' || state.phase === 'downloading'
    );

    if (activeCharts.length > 0) {
        const activeNames = activeCharts.map(state => state.chart?.name || 'Unknown').join(', ');
        showToast(`Cannot clear: ${activeCharts.length} chart(s) are currently discovering, syncing, or downloading: ${activeNames}`, 'warning');
        return;
    }

    // Show loading state
    clearBtn.disabled = true;
    clearBtn.textContent = '🗑️ Clearing...';

    try {
        // Clear all Beatport chart states
        Object.keys(beatportChartStates).forEach(chartHash => {
            // Close any open modals for this chart
            const modal = document.getElementById(`youtube-discovery-modal-${chartHash}`);
            if (modal) {
                modal.remove();
            }

            // Remove from YouTube states (since Beatport reuses that infrastructure)
            if (youtubePlaylistStates[chartHash]) {
                // Clean up any active download processes for this Beatport chart
                const ytState = youtubePlaylistStates[chartHash];
                if (ytState.is_beatport_playlist && ytState.convertedSpotifyPlaylistId) {
                    const downloadProcess = activeDownloadProcesses[ytState.convertedSpotifyPlaylistId];
                    if (downloadProcess) {
                        console.log(`🗑️ Cleaning up download process for Beatport chart: ${chartHash}`);
                        if (downloadProcess.modalElement) {
                            downloadProcess.modalElement.remove();
                        }
                        delete activeDownloadProcesses[ytState.convertedSpotifyPlaylistId];
                    }
                }

                delete youtubePlaylistStates[chartHash];
            }
        });

        // Clear Beatport states
        const chartHashesToClear = Object.keys(beatportChartStates);
        beatportChartStates = {};

        // Clear backend state for all charts
        for (const chartHash of chartHashesToClear) {
            try {
                await fetch(`/api/beatport/charts/delete/${chartHash}`, {
                    method: 'DELETE'
                });
                console.log(`🗑️ Deleted backend state for Beatport chart: ${chartHash}`);
            } catch (error) {
                console.warn(`⚠️ Error deleting backend state for chart ${chartHash}:`, error);
            }
        }

        // Reset container to placeholder
        container.innerHTML = `
            <div class="playlist-placeholder">Your created Beatport playlists will appear here.</div>
        `;

        console.log(`🗑️ Cleared ${chartHashesToClear.length} Beatport charts from frontend and backend`);
        showToast('Cleared all Beatport playlists', 'success');

        // Update clear button state after clearing all charts
        updateBeatportClearButtonState();

    } catch (error) {
        console.error('Error clearing Beatport playlists:', error);
        showToast(`Error clearing playlists: ${error.message}`, 'error');
    } finally {
        clearBtn.disabled = false;
        clearBtn.textContent = '🗑️ Clear';
    }
}

function handleBeatportCategoryClick(category) {
    console.log(`🎵 Beatport category clicked: ${category}`);

    // Only handle genres category now - homepage has direct chart buttons
    switch (category) {
        case 'genres':
            showBeatportSubView('genres');
            loadBeatportGenres(); // Load genres dynamically
            break;
        default:
            showToast(`Unknown category: ${category}`, 'error');
    }
}

async function loadBeatportGenres() {
    console.log('🔍 Loading Beatport genres dynamically...');

    const genreGrid = document.querySelector('#beatport-genres-view .beatport-genre-grid');
    if (!genreGrid) {
        console.error('❌ Could not find genre grid element');
        return;
    }

    // Show loading state
    genreGrid.innerHTML = `
        <div class="genre-loading-placeholder">
            <div class="loading-spinner"></div>
            <p>🔍 Discovering current Beatport genres...</p>
        </div>
    `;

    try {
        // First, fetch genres quickly without images
        console.log('🚀 Fetching genres without images for fast loading...');
        const fastResponse = await fetch('/api/beatport/genres');
        if (!fastResponse.ok) {
            throw new Error(`API returned ${fastResponse.status}: ${fastResponse.statusText}`);
        }

        const fastData = await fastResponse.json();
        const genres = fastData.genres || [];

        if (genres.length === 0) {
            genreGrid.innerHTML = `
                <div class="genre-error-placeholder">
                    <p>⚠️ No genres available</p>
                    <button onclick="loadBeatportGenres()" class="refresh-genres-btn">🔄 Retry</button>
                </div>
            `;
            return;
        }

        // Generate genre cards dynamically (without images first)
        const genreCardsHTML = genres.map(genre => `
            <div class="beatport-genre-item"
                 data-genre-slug="${genre.slug}"
                 data-genre-id="${genre.id}"
                 data-genre-name="${genre.name}">
                <div class="genre-icon">🎵</div>
                <h3>${genre.name}</h3>
                <span class="track-count">Top 100</span>
            </div>
        `).join('');

        genreGrid.innerHTML = genreCardsHTML;

        // Add click handlers to dynamically created genre items
        const genreItems = genreGrid.querySelectorAll('.beatport-genre-item');
        genreItems.forEach(item => {
            item.addEventListener('click', () => {
                const genreSlug = item.dataset.genreSlug;
                const genreId = item.dataset.genreId;
                const genreName = item.dataset.genreName;
                handleBeatportGenreClick(genreSlug, genreId, genreName);
            });
        });

        console.log(`✅ Loaded ${genres.length} Beatport genres dynamically (fast mode)`);
        showToast(`Loaded ${genres.length} current Beatport genres`, 'success');

        // Now fetch images progressively in the background if there are many genres
        if (genres.length > 10) {
            console.log('🖼️ Loading genre images progressively...');
            loadGenreImagesProgressively(genres);
        }

    } catch (error) {
        console.error('❌ Error loading Beatport genres:', error);
        genreGrid.innerHTML = `
            <div class="genre-error-placeholder">
                <p>❌ Failed to load genres: ${error.message}</p>
                <button onclick="loadBeatportGenres()" class="refresh-genres-btn">🔄 Retry</button>
            </div>
        `;
        showToast(`Error loading Beatport genres: ${error.message}`, 'error');
    }
}

async function loadGenreImagesProgressively(genres) {
    // Load genre images with 2 concurrent workers for faster loading

    const imageQueue = [...genres]; // Create a copy for processing
    let imagesLoaded = 0;
    const maxWorkers = 2;

    console.log(`🖼️ Starting progressive image loading with ${maxWorkers} workers for ${imageQueue.length} genres`);

    // Function to process a single image
    async function processImage(genre) {
        try {
            // Fetch individual genre image from backend
            const response = await fetch(`/api/beatport/genre-image/${genre.slug}/${genre.id}`);

            if (response.ok) {
                const data = await response.json();

                if (data.success && data.image_url) {
                    // Find the genre item in the DOM
                    const genreItem = document.querySelector(
                        `[data-genre-slug="${genre.slug}"][data-genre-id="${genre.id}"]`
                    );

                    if (genreItem) {
                        const iconElement = genreItem.querySelector('.genre-icon');
                        if (iconElement) {
                            // Create new image element with smooth transition
                            const imageDiv = document.createElement('div');
                            imageDiv.className = 'genre-image';
                            imageDiv.style.backgroundImage = `url('${data.image_url}')`;
                            imageDiv.style.opacity = '0';
                            imageDiv.style.transition = 'opacity 0.3s ease';

                            // Replace icon with image
                            iconElement.replaceWith(imageDiv);

                            // Trigger fade-in animation
                            setTimeout(() => {
                                imageDiv.style.opacity = '1';
                            }, 50);

                            imagesLoaded++;
                            console.log(`🖼️ [${imagesLoaded}/${imageQueue.length}] Loaded image for ${genre.name}`);
                        }
                    }
                }
            }
        } catch (error) {
            console.warn(`⚠️ Failed to load image for ${genre.name}:`, error);
        }
    }

    // Worker function that processes images from the queue
    async function imageWorker(workerId) {
        while (imageQueue.length > 0) {
            const genre = imageQueue.shift(); // Take next image from queue
            if (genre) {
                await processImage(genre);

                // Small delay between requests to be respectful (500ms per worker = ~2 images per second total)
                await new Promise(resolve => setTimeout(resolve, 500));
            }
        }
        console.log(`✅ Worker ${workerId} finished`);
    }

    // Start the workers
    const workers = [];
    for (let i = 0; i < maxWorkers; i++) {
        workers.push(imageWorker(i + 1));
    }

    // Wait for all workers to complete
    await Promise.all(workers);

    console.log(`✅ Progressive image loading complete: ${imagesLoaded}/${genres.length} images loaded`);
}

function setupHomepageChartTypeHandlers() {
    console.log('🔧 Setting up homepage chart type handlers...');

    // Select all homepage chart type cards (following genre page pattern)
    const chartTypeCards = document.querySelectorAll('.homepage-main-charts-section .genre-chart-type-card[data-chart-type], .homepage-releases-section .genre-chart-type-card[data-chart-type], .homepage-hype-section .genre-chart-type-card[data-chart-type]');

    chartTypeCards.forEach(card => {
        // Remove existing listeners by cloning (following genre page pattern)
        card.replaceWith(card.cloneNode(true));
    });

    // Re-select after cloning to ensure clean event listeners (following genre page pattern)
    const newChartTypeCards = document.querySelectorAll('.homepage-main-charts-section .genre-chart-type-card[data-chart-type], .homepage-releases-section .genre-chart-type-card[data-chart-type], .homepage-hype-section .genre-chart-type-card[data-chart-type]');

    newChartTypeCards.forEach(card => {
        card.addEventListener('click', () => {
            const chartType = card.dataset.chartType;
            const chartEndpoint = card.dataset.chartEndpoint;
            const chartName = card.querySelector('.chart-type-info h3').textContent;
            console.log(`🔥 Homepage chart clicked: ${chartName} (${chartType})`);
            handleHomepageChartTypeClick(chartType, chartEndpoint, chartName);
        });
    });

    console.log(`✅ Setup ${newChartTypeCards.length} homepage chart handlers`);
}

async function handleHomepageChartTypeClick(chartType, chartEndpoint, chartName) {
    console.log(`🔥 Homepage chart type clicked: ${chartType} (${chartName})`);

    // Map chart types to API endpoints and create descriptive names (following genre page pattern)
    const chartTypeMap = {
        'top-10': {
            endpoint: `/api/beatport/top-100`,  // Use top-100 endpoint and limit to 10
            name: `Beatport Top 10`,
            limit: 10
        },
        'top-100': {
            endpoint: `/api/beatport/top-100`,
            name: `Beatport Top 100`,
            limit: 100
        },
        'releases-top-10': {
            endpoint: `/api/beatport/homepage/top-10-releases`,  // Working route
            name: `Top 10 Releases`,
            limit: 10
        },
        'releases-top-100': {
            endpoint: `/api/beatport/top-100-releases`,
            name: `Top 100 Releases`,
            limit: 100
        },
        'latest-releases': {
            endpoint: `/api/beatport/homepage/new-releases`,  // Use new-releases as fallback for now
            name: `Latest Releases`,
            limit: 50
        },
        'hype-top-10': {
            endpoint: `/api/beatport/hype-top-100`,  // Use hype-100 endpoint and limit to 10
            name: `Hype Top 10`,
            limit: 10
        },
        'hype-top-100': {
            endpoint: `/api/beatport/hype-top-100`,
            name: `Hype Top 100`,
            limit: 100
        },
        'hype-picks': {
            endpoint: `/api/beatport/homepage/hype-picks`,  // Working route
            name: `Hype Picks`,
            limit: 50
        }
    };

    const chartConfig = chartTypeMap[chartType];
    if (!chartConfig) {
        console.error(`❌ Unknown homepage chart type: ${chartType}`);
        showToast(`Unknown chart type: ${chartType}`, 'error');
        return;
    }

    try {
        showToast(`Loading ${chartConfig.name}...`, 'info');
        showLoadingOverlay(`Loading ${chartConfig.name}...`);

        const response = await fetch(`${chartConfig.endpoint}?limit=${chartConfig.limit}`);
        if (!response.ok) {
            throw new Error(`Failed to fetch ${chartConfig.name}: ${response.status}`);
        }

        const data = await response.json();
        if (!data.success || !data.tracks || data.tracks.length === 0) {
            throw new Error(`No tracks found in ${chartConfig.name}`);
        }

        console.log(`✅ Fetched ${data.tracks.length} tracks from ${chartConfig.name}`);
        hideLoadingOverlay();
        openBeatportChartAsDownloadModal(data.tracks, chartConfig.name, null);

    } catch (error) {
        console.error(`❌ Error loading ${chartConfig.name}:`, error);
        hideLoadingOverlay();
        showToast(`Error loading ${chartConfig.name}: ${error.message}`, 'error');
    }
}



async function openBeatportDiscoveryModal(chartHash, chartData) {
    console.log(`🎵 Opening Beatport discovery modal (reusing YouTube modal): ${chartData.name}`);

    // Create YouTube-style state entry for this Beatport chart
    const beatportState = {
        phase: 'fresh',
        playlist: {
            name: chartData.name,
            tracks: chartData.tracks,
            description: `${chartData.track_count} tracks from ${chartData.name}`,
            source: 'beatport'
        },
        is_beatport_playlist: true,
        beatport_chart_type: chartData.chart_type,
        beatport_chart_hash: chartHash  // Link to Beatport card state
    };

    // Store in YouTube playlist states (reusing the infrastructure)
    youtubePlaylistStates[chartHash] = beatportState;

    // Start discovery automatically (like Tidal does)
    try {
        console.log(`🔍 Starting Beatport discovery for: ${chartData.name}`);

        // Update card phase to discovering immediately
        updateBeatportCardPhase(chartHash, 'discovering');

        // Call the discovery start endpoint with chart data
        const response = await fetch(`/api/beatport/discovery/start/${chartHash}`, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            },
            body: JSON.stringify({
                chart_data: chartData
            })
        });

        const result = await response.json();
        if (result.success) {
            // Update state to discovering
            youtubePlaylistStates[chartHash].phase = 'discovering';

            // Start polling for progress
            startBeatportDiscoveryPolling(chartHash);

            console.log(`✅ Started Beatport discovery for: ${chartData.name}`);
        } else {
            console.error('❌ Error starting Beatport discovery:', result.error);
            showToast(`Error starting discovery: ${result.error}`, 'error');
            // Revert card phase on error
            updateBeatportCardPhase(chartHash, 'fresh');
        }
    } catch (error) {
        console.error('❌ Error starting Beatport discovery:', error);
        showToast(`Error starting discovery: ${error.message}`, 'error');
        // Revert card phase on error
        updateBeatportCardPhase(chartHash, 'fresh');
    }

    // Open the existing YouTube discovery modal infrastructure
    openYouTubeDiscoveryModal(chartHash);

    console.log(`✅ Beatport discovery modal opened for ${chartData.name} with ${chartData.tracks.length} tracks`);
}

function startBeatportDiscoveryPolling(urlHash) {
    console.log(`🔄 Starting Beatport discovery polling for: ${urlHash}`);

    // Stop any existing polling (reuse YouTube polling infrastructure)
    if (activeYouTubePollers[urlHash]) {
        clearInterval(activeYouTubePollers[urlHash]);
    }

    // Phase 5: Subscribe via WebSocket
    if (socketConnected) {
        socket.emit('discovery:subscribe', { ids: [urlHash] });
        _discoveryProgressCallbacks[urlHash] = (data) => {
            if (data.error) {
                if (activeYouTubePollers[urlHash]) { clearInterval(activeYouTubePollers[urlHash]); delete activeYouTubePollers[urlHash]; }
                socket.emit('discovery:unsubscribe', { ids: [urlHash] }); delete _discoveryProgressCallbacks[urlHash];
                return;
            }
            if (youtubePlaylistStates[urlHash]) {
                const transformed = {
                    progress: data.progress || 0, spotify_matches: data.spotify_matches || 0, spotify_total: data.spotify_total || 0,
                    results: (data.results || []).map((r, i) => ({
                        index: r.index !== undefined ? r.index : i,
                        yt_track: r.beatport_track ? r.beatport_track.title : 'Unknown',
                        yt_artist: r.beatport_track ? r.beatport_track.artist : 'Unknown',
                        status: (r.status === 'found' || r.status === '✅ Found' || r.status_class === 'found') ? '✅ Found' : (r.status === 'error' ? '❌ Error' : '❌ Not Found'),
                        status_class: r.status_class || ((r.status === 'found' || r.status === '✅ Found') ? 'found' : (r.status === 'error' ? 'error' : 'not-found')),
                        spotify_track: r.spotify_data ? r.spotify_data.name : (r.spotify_track || '-'),
                        spotify_artist: r.spotify_data && r.spotify_data.artists ? r.spotify_data.artists.map(a => a.name || a).join(', ') : (r.spotify_artist || '-'),
                        spotify_album: r.spotify_data ? (typeof r.spotify_data.album === 'object' ? r.spotify_data.album.name : r.spotify_data.album) : (r.spotify_album || '-'),
                        spotify_data: r.spotify_data, spotify_id: r.spotify_id, manual_match: r.manual_match
                    }))
                };
                const st = youtubePlaylistStates[urlHash];
                st.discovery_progress = data.progress; st.discoveryProgress = data.progress;
                st.spotify_matches = data.spotify_matches; st.spotifyMatches = data.spotify_matches;
                st.discovery_results = data.results; st.discoveryResults = transformed.results;
                st.phase = data.phase || 'discovering';
                const chartHash = st.beatport_chart_hash || urlHash;
                updateBeatportCardPhase(chartHash, data.phase || 'discovering');
                updateBeatportCardProgress(chartHash, { spotify_total: data.spotify_total || 0, spotify_matches: data.spotify_matches || 0, failed: (data.spotify_total || 0) - (data.spotify_matches || 0) });
                if (beatportChartStates[chartHash]) beatportChartStates[chartHash].phase = data.phase || 'discovering';
                updateYouTubeDiscoveryModal(urlHash, transformed);
            }
            if (data.phase === 'discovered' || data.phase === 'error') {
                if (activeYouTubePollers[urlHash]) { clearInterval(activeYouTubePollers[urlHash]); delete activeYouTubePollers[urlHash]; }
                socket.emit('discovery:unsubscribe', { ids: [urlHash] }); delete _discoveryProgressCallbacks[urlHash];
            }
        };
    }

    const pollInterval = setInterval(async () => {
        // Always poll — no dedicated WebSocket events for discovery progress
        try {
            const response = await fetch(`/api/beatport/discovery/status/${urlHash}`);
            const status = await response.json();

            if (status.error) {
                console.error('❌ Error polling Beatport discovery status:', status.error);
                clearInterval(pollInterval);
                delete activeYouTubePollers[urlHash];
                return;
            }

            // Update state and modal (reuse YouTube infrastructure like Tidal)
            if (youtubePlaylistStates[urlHash]) {
                // Transform Beatport results to YouTube modal format (like Tidal does)
                const transformedStatus = {
                    progress: status.progress || 0,
                    spotify_matches: status.spotify_matches || 0,
                    spotify_total: status.spotify_total || 0,
                    results: (status.results || []).map((result, index) => ({
                        index: result.index !== undefined ? result.index : index,
                        yt_track: result.beatport_track ? result.beatport_track.title : 'Unknown',
                        yt_artist: result.beatport_track ? result.beatport_track.artist : 'Unknown',
                        status: result.status === 'found' || result.status === '✅ Found' || result.status_class === 'found' ? '✅ Found' : (result.status === 'error' ? '❌ Error' : '❌ Not Found'),
                        status_class: result.status_class || (result.status === 'found' || result.status === '✅ Found' ? 'found' : (result.status === 'error' ? 'error' : 'not-found')),
                        spotify_track: result.spotify_data ? result.spotify_data.name : (result.spotify_track || '-'),
                        spotify_artist: result.spotify_data && result.spotify_data.artists ?
                            result.spotify_data.artists.map(a => a.name || a).join(', ') : (result.spotify_artist || '-'),
                        spotify_album: result.spotify_data ? (typeof result.spotify_data.album === 'object' ? result.spotify_data.album.name : result.spotify_data.album) : (result.spotify_album || '-'),
                        spotify_data: result.spotify_data, // Pass through
                        spotify_id: result.spotify_id, // Pass through
                        manual_match: result.manual_match // Pass through
                    }))
                };

                // Update state with both backend and frontend formats (like Tidal)
                const state = youtubePlaylistStates[urlHash];
                state.discovery_progress = status.progress; // Backend format
                state.discoveryProgress = status.progress; // Frontend format - for modal progress display
                state.spotify_matches = status.spotify_matches; // Backend format
                state.spotifyMatches = status.spotify_matches; // Frontend format - for button logic
                state.discovery_results = status.results; // Backend format
                state.discoveryResults = transformedStatus.results; // Frontend format - for button logic
                state.phase = status.phase || 'discovering';

                // Update Beatport card phase and progress
                const chartHash = state.beatport_chart_hash || urlHash;
                updateBeatportCardPhase(chartHash, status.phase || 'discovering');
                updateBeatportCardProgress(chartHash, {
                    spotify_total: status.spotify_total || 0,
                    spotify_matches: status.spotify_matches || 0,
                    failed: (status.spotify_total || 0) - (status.spotify_matches || 0)
                });

                // Sync with backend Beatport chart state
                if (beatportChartStates[chartHash]) {
                    beatportChartStates[chartHash].phase = status.phase || 'discovering';
                }

                // Update modal display with transformed data
                updateYouTubeDiscoveryModal(urlHash, transformedStatus);
            }

            // Stop polling when discovery is complete
            if (status.phase === 'discovered' || status.phase === 'error') {
                console.log(`✅ Beatport discovery polling complete for: ${urlHash}`);
                clearInterval(pollInterval);
                delete activeYouTubePollers[urlHash];
            }

        } catch (error) {
            console.error('❌ Error polling Beatport discovery:', error);
            clearInterval(pollInterval);
            delete activeYouTubePollers[urlHash];
        }
    }, 2000); // Poll every 2 seconds like Tidal

    // Store the interval so we can clean it up later
    activeYouTubePollers[urlHash] = pollInterval;
}

function showBeatportSubView(viewType) {
    // Hide main category view
    const mainView = document.getElementById('beatport-main-view');
    if (mainView) {
        mainView.classList.remove('active');
    }

    // Hide all sub-views
    document.querySelectorAll('.beatport-sub-view').forEach(view => {
        view.classList.remove('active');
    });

    // Show the requested sub-view
    const targetView = document.getElementById(`beatport-${viewType}-view`);
    if (targetView) {
        targetView.classList.add('active');
        console.log(`🎵 Showing Beatport ${viewType} view`);
    } else {
        console.error(`🎵 Could not find view: beatport-${viewType}-view`);
    }
}

function showBeatportMainView() {
    // Hide all sub-views
    document.querySelectorAll('.beatport-sub-view').forEach(view => {
        view.classList.remove('active');
    });

    // Show main category view
    const mainView = document.getElementById('beatport-main-view');
    if (mainView) {
        mainView.classList.add('active');
        console.log('🎵 Showing Beatport main view');
    }
}

// ===============================
// REBUILD PAGE TOP 10 FUNCTIONALITY
// ===============================

// Global variable to store rebuild page track data for reuse
let rebuildPageTrackData = {
    beatport_top10: null,
    hype_top10: null
    // hero_slider removed - now uses individual slide click handlers
};

async function handleRebuildBeatportTop10Click() {
    console.log('🎵 Handling Beatport Top 10 click on rebuild page');

    // Use the existing chart creation pattern from Browse Charts EXACTLY
    await handleRebuildChartClick('beatport_top10', 'Beatport Top 10', 'rebuild_beatport_top10');
}

async function handleRebuildHypeTop10Click() {
    console.log('🔥 Handling Hype Top 10 click on rebuild page');

    // Use the existing chart creation pattern from Browse Charts EXACTLY
    await handleRebuildChartClick('hype_top10', 'Hype Top 10', 'rebuild_hype_top10');
}

// Hero slider now uses individual slide click handlers instead of container-level clicking
// The old handleRebuildHeroSliderClick function has been removed in favor of individual release discovery

async function handleRebuildChartClick(trackDataKey, chartName, chartType) {
    if (_beatportModalOpening) return;
    _beatportModalOpening = true;
    setTimeout(() => { _beatportModalOpening = false; }, 2000);

    try {
        // Get basic track data from DOM
        const trackData = await getRebuildPageTrackData(trackDataKey);
        if (!trackData || trackData.length === 0) {
            throw new Error(`No track data found for ${chartName}`);
        }

        console.log(`✅ Got ${trackData.length} tracks from ${chartName}, enriching one-by-one...`);
        showLoadingOverlay(`Fetching track metadata... (0/${trackData.length})`);

        const enrichedTracks = await _enrichTracksWithProgress(trackData, chartName);

        console.log(`✅ Enriched ${enrichedTracks.length} tracks`);
        hideLoadingOverlay();
        openBeatportChartAsDownloadModal(enrichedTracks, chartName, null);

    } catch (error) {
        hideLoadingOverlay();
        console.error(`❌ Error handling ${chartName} click:`, error);
        showToast(`Error loading ${chartName}: ${error.message}`, 'error');
    }
}

async function getRebuildPageTrackData(trackDataKey) {
    // First check if we have cached data from when the rebuild page was loaded
    if (rebuildPageTrackData[trackDataKey]) {
        console.log(`📦 Using cached ${trackDataKey} data`);
        return rebuildPageTrackData[trackDataKey];
    }

    // If no cached data, extract from DOM (fallback)
    console.log(`🔍 Extracting ${trackDataKey} data from rebuild page DOM`);

    let containerSelector, cardSelector;
    if (trackDataKey === 'beatport_top10') {
        containerSelector = '#beatport-top10-list';
        cardSelector = '.beatport-top10-card[data-url]';
    } else if (trackDataKey === 'hype_top10') {
        containerSelector = '#beatport-hype10-list';
        cardSelector = '.beatport-hype10-card[data-url]';
    } else {
        throw new Error(`Unknown track data key: ${trackDataKey}`);
    }

    const container = document.querySelector(containerSelector);
    if (!container) {
        throw new Error(`Container ${containerSelector} not found`);
    }

    const trackCards = container.querySelectorAll(cardSelector);
    if (trackCards.length === 0) {
        throw new Error(`No track cards found in ${containerSelector}`);
    }

    // Extract track data from DOM cards
    const tracks = Array.from(trackCards).map(card => {
        const title = card.querySelector('.beatport-top10-card-title, .beatport-hype10-card-title')?.textContent?.trim() || 'Unknown Title';
        const artist = card.querySelector('.beatport-top10-card-artist, .beatport-hype10-card-artist')?.textContent?.trim() || 'Unknown Artist';
        const label = card.querySelector('.beatport-top10-card-label, .beatport-hype10-card-label')?.textContent?.trim() || 'Unknown Label';
        const url = card.getAttribute('data-url') || '';
        const rank = card.querySelector('.beatport-top10-card-rank, .beatport-hype10-card-rank')?.textContent?.trim() || '';

        return {
            title: title,
            artist: artist,
            label: label,
            url: url,
            rank: rank
        };
    });

    console.log(`📋 Extracted ${tracks.length} tracks from ${containerSelector}`);

    // Cache for future use
    rebuildPageTrackData[trackDataKey] = tracks;

    return tracks;
}

// getHeroSliderTrackData function removed - hero slider now uses individual slide click handlers
// Each slide will create its own discovery modal using handleBeatportReleaseCardClick

// Hook into the loadBeatportTop10Lists function to cache track data
const originalLoadBeatportTop10Lists = window.loadBeatportTop10Lists;
if (originalLoadBeatportTop10Lists) {
    window.loadBeatportTop10Lists = async function () {
        const result = await originalLoadBeatportTop10Lists.apply(this, arguments);

        // If the load was successful, we can potentially cache the track data
        // But for now, we'll rely on DOM extraction as it's more reliable

        return result;
    };
}

// ===============================
// BEATPORT CHART FUNCTIONALITY
// ===============================

function createBeatportCard(chartData) {
    const state = beatportChartStates[chartData.hash];
    const phase = state ? state.phase : 'fresh';

    let buttonText = getActionButtonText(phase);
    let phaseText = getPhaseText(phase);
    let phaseColor = getPhaseColor(phase);

    return `
        <div class="youtube-playlist-card" id="beatport-card-${chartData.hash}">
            <div class="playlist-card-icon">🎧</div>
            <div class="playlist-card-content">
                <div class="playlist-card-name">${escapeHtml(chartData.name)}</div>
                <div class="playlist-card-info">
                    <span class="playlist-card-track-count">${chartData.track_count} tracks</span>
                    <span class="playlist-card-phase-text" style="color: ${phaseColor};">${phaseText}</span>
                </div>
            </div>
            <div class="playlist-card-progress ${phase === 'fresh' ? 'hidden' : ''}">
                <!-- Progress will be dynamically updated based on phase -->
            </div>
            <button class="playlist-card-action-btn">${buttonText}</button>
        </div>
    `;
}

function addBeatportCardToContainer(chartData) {
    const container = document.getElementById('beatport-playlist-container');

    // Remove placeholder if it exists
    const placeholder = container.querySelector('.playlist-placeholder');
    if (placeholder) {
        placeholder.remove();
    }

    // Check if card already exists
    const existingCard = document.getElementById(`beatport-card-${chartData.hash}`);
    if (existingCard) {
        console.log(`Card already exists for ${chartData.name}, updating instead`);
        return;
    }

    // Create and add the card
    const cardHtml = createBeatportCard(chartData);
    container.insertAdjacentHTML('beforeend', cardHtml);

    // Initialize state
    beatportChartStates[chartData.hash] = {
        phase: 'fresh',
        chart: chartData,
        cardElement: document.getElementById(`beatport-card-${chartData.hash}`)
    };

    // Add click handler
    const card = document.getElementById(`beatport-card-${chartData.hash}`);
    if (card) {
        card.addEventListener('click', async () => await handleBeatportCardClick(chartData.hash));
    }

    console.log(`🃏 Created Beatport card: ${chartData.name}`);

    // Auto-mirror this Beatport chart
    if (chartData.tracks && chartData.tracks.length > 0) {
        mirrorPlaylist('beatport', chartData.hash, chartData.name, chartData.tracks.map(t => ({
            track_name: t.name || t.title || '', artist_name: Array.isArray(t.artists) ? t.artists[0] : (t.artist || ''),
            album_name: t.album || '', duration_ms: t.duration_ms || 0,
            source_track_id: t.id || '', image_url: t.image_url || null
        })));
    }

    // Update clear button state after creating card
    updateBeatportClearButtonState();
}

async function handleBeatportCardClick(chartHash) {
    const state = beatportChartStates[chartHash];
    if (!state) {
        console.error(`❌ [Card Click] No state found for Beatport chart: ${chartHash}`);
        showToast('Chart state not found - try refreshing the page', 'error');
        return;
    }

    if (!state.chart) {
        console.error(`❌ [Card Click] No chart data found for Beatport chart: ${chartHash}`);
        showToast('Chart data missing - try refreshing the page', 'error');
        return;
    }

    console.log(`🎧 [Card Click] Beatport card clicked: ${chartHash}, Phase: ${state.phase}`);

    if (state.phase === 'fresh') {
        // Open discovery modal and start discovery
        openBeatportDiscoveryModal(chartHash, state.chart);
    } else if (state.phase === 'discovering' || state.phase === 'discovered' || state.phase === 'syncing' || state.phase === 'sync_complete') {
        // Reopen existing modal with preserved discovery results
        console.log(`🎧 [Card Click] Opening Beatport discovery modal for ${state.phase} phase`);

        // Check if we have the required state data
        const ytState = youtubePlaylistStates[chartHash];
        if (!ytState || !ytState.playlist) {
            console.log(`🔍 [Card Click] Missing playlist data for ${state.phase} phase, fetching from backend...`);

            try {
                // Fetch the full state from backend
                const stateResponse = await fetch(`/api/beatport/charts/status/${chartHash}`);
                if (stateResponse.ok) {
                    const fullState = await stateResponse.json();

                    // Restore the missing playlist data
                    if (fullState.chart_data) {
                        if (!youtubePlaylistStates[chartHash]) {
                            youtubePlaylistStates[chartHash] = {};
                        }
                        youtubePlaylistStates[chartHash].playlist = fullState.chart_data;
                        youtubePlaylistStates[chartHash].is_beatport_playlist = true;
                        youtubePlaylistStates[chartHash].beatport_chart_hash = chartHash;

                        // Also restore discovery results if available
                        if (fullState.discovery_results) {
                            youtubePlaylistStates[chartHash].discovery_results = fullState.discovery_results;
                            console.log(`🔄 [Hydration] Restored ${fullState.discovery_results.length} discovery results`);
                            console.log(`🔄 [Hydration] First result:`, fullState.discovery_results[0]);
                        }

                        // Restore discovery progress state
                        if (fullState.discovery_progress !== undefined) {
                            youtubePlaylistStates[chartHash].discovery_progress = fullState.discovery_progress;
                        }
                        if (fullState.spotify_matches !== undefined) {
                            youtubePlaylistStates[chartHash].spotify_matches = fullState.spotify_matches;
                            console.log(`🔄 [Hydration] Restored spotify_matches: ${fullState.spotify_matches}`);
                        }
                        if (fullState.spotify_total !== undefined) {
                            youtubePlaylistStates[chartHash].spotify_total = fullState.spotify_total;
                        }

                        console.log(`✅ [Card Click] Restored playlist data for ${state.phase} phase`);
                    }
                } else {
                    console.error(`❌ [Card Click] Failed to fetch state for chart: ${chartHash}`);
                    showToast('Error loading chart data', 'error');
                    return;
                }
            } catch (error) {
                console.error(`❌ [Card Click] Error fetching chart state:`, error);
                showToast('Error loading chart data', 'error');
                return;
            }
        }

        openYouTubeDiscoveryModal(chartHash);

        // If still in discovering phase, start polling for live updates
        if (state.phase === 'discovering') {
            console.log(`🔄 [Card Click] Starting discovery polling for ${state.phase} phase`);

            // Let the polling handle all modal updates to avoid data structure mismatches
            console.log(`📊 [Card Click] Starting polling - it will update modal with current progress`);

            startBeatportDiscoveryPolling(chartHash);
        }
    } else if (state.phase === 'downloading' || state.phase === 'download_complete') {
        // Open download modal if we have the converted playlist ID (following YouTube/Tidal pattern)
        const ytState = youtubePlaylistStates[chartHash];
        if (ytState && ytState.is_beatport_playlist && ytState.convertedSpotifyPlaylistId) {
            console.log(`📥 [Card Click] Opening download modal for Beatport chart: ${ytState.playlist.name} (phase: ${state.phase})`);

            // Check if modal already exists, if not create it (like Tidal implementation)
            if (activeDownloadProcesses[ytState.convertedSpotifyPlaylistId]) {
                const process = activeDownloadProcesses[ytState.convertedSpotifyPlaylistId];
                if (process.modalElement) {
                    console.log(`📱 [Card Click] Showing existing download modal for ${state.phase} phase`);
                    process.modalElement.style.display = 'flex';
                } else {
                    console.warn(`⚠️ [Card Click] Download process exists but modal element missing - rehydrating`);
                    await rehydrateBeatportDownloadModal(chartHash, ytState);
                }
            } else {
                // Need to create the download modal - fetch the discovery results if needed
                console.log(`🔧 [Card Click] Rehydrating Beatport download modal for ${state.phase} phase`);
                await rehydrateBeatportDownloadModal(chartHash, ytState);
            }
        } else {
            console.error('❌ [Card Click] No converted Spotify playlist ID found for Beatport download modal');
            console.log('📊 [Card Click] Available state data:', Object.keys(ytState || {}));

            // Fallback: try to open discovery modal if we have discovery results
            if (ytState && ytState.discovery_results && ytState.discovery_results.length > 0) {
                console.log(`🔄 [Card Click] Fallback: Opening discovery modal with ${ytState.discovery_results.length} results`);
                openYouTubeDiscoveryModal(chartHash);
            } else {
                showToast('Unable to open download modal - missing playlist data', 'error');
            }
        }
    }
}

async function rehydrateBeatportDownloadModal(chartHash, ytState) {
    try {
        console.log(`💧 [Rehydration] Attempting fallback rehydration for Beatport chart: ${chartHash}`);

        // This function is only called as a fallback when the modal wasn't created during backend loading
        // In most cases, the modal should already exist from loadBeatportChartsFromBackend()

        if (!ytState || !ytState.playlist || !ytState.convertedSpotifyPlaylistId) {
            console.error(`❌ [Rehydration] Invalid state data for Beatport chart: ${chartHash}`);
            showToast('Cannot open download modal - invalid playlist data', 'error');
            return;
        }

        // Get discovery results from backend if not already loaded
        if (!ytState.discovery_results) {
            console.log(`🔍 Fetching discovery results from backend for Beatport chart: ${chartHash}`);
            const stateResponse = await fetch(`/api/beatport/charts/status/${chartHash}`);
            if (stateResponse.ok) {
                const fullState = await stateResponse.json();
                ytState.discovery_results = fullState.discovery_results;
                ytState.download_process_id = fullState.download_process_id;
                console.log(`✅ Loaded ${fullState.discovery_results?.length || 0} discovery results from backend`);
            } else {
                console.error('❌ Failed to fetch Beatport discovery results from backend');
                showToast('Error loading playlist data', 'error');
                return;
            }
        }

        // Extract Spotify tracks from discovery results
        const spotifyTracks = ytState.discovery_results
            .filter(result => result.spotify_data)
            .map(result => {
                const track = result.spotify_data;
                // Ensure artists is an array of strings
                if (track.artists && Array.isArray(track.artists)) {
                    track.artists = track.artists.map(artist =>
                        typeof artist === 'string' ? artist : (artist.name || artist)
                    );
                } else if (track.artists && typeof track.artists === 'string') {
                    track.artists = [track.artists];
                } else {
                    track.artists = ['Unknown Artist'];
                }
                return {
                    id: track.id,
                    name: track.name,
                    artists: track.artists,
                    album: track.album || 'Unknown Album',
                    duration_ms: track.duration_ms || 0,
                    external_urls: track.external_urls || {}
                };
            });

        if (spotifyTracks.length === 0) {
            console.error('❌ No Spotify tracks found for download modal');
            showToast('No Spotify matches found for download', 'error');
            return;
        }

        const virtualPlaylistId = ytState.convertedSpotifyPlaylistId;
        const playlistName = ytState.playlist.name;

        // Create the download modal
        await openDownloadMissingModalForYouTube(virtualPlaylistId, playlistName, spotifyTracks);

        // Set up the modal for the running state if we have a download process ID
        if (ytState.download_process_id) {
            const process = activeDownloadProcesses[virtualPlaylistId];
            if (process) {
                process.status = 'running';
                process.batchId = ytState.download_process_id;

                // Update UI to reflect running state
                const beginBtn = document.getElementById(`begin-analysis-btn-${virtualPlaylistId}`);
                const cancelBtn = document.getElementById(`cancel-all-btn-${virtualPlaylistId}`);
                if (beginBtn) beginBtn.style.display = 'none';
                if (cancelBtn) cancelBtn.style.display = 'inline-block';

                // Start polling for this process
                startModalDownloadPolling(virtualPlaylistId);

                console.log(`✅ [Rehydration] Fallback modal rehydrated for running download process`);
            }
        }

    } catch (error) {
        console.error(`❌ [Rehydration] Error in fallback rehydration for Beatport chart:`, error);
        showToast('Error opening download modal', 'error');
        hideLoadingOverlay();
    }
}

function updateBeatportCardPhase(chartHash, phase) {
    const state = beatportChartStates[chartHash];
    if (!state) return;

    state.phase = phase;

    // Re-render the card with new phase
    const card = document.getElementById(`beatport-card-${chartHash}`);
    if (card) {
        const newCardHtml = createBeatportCard(state.chart);
        card.outerHTML = newCardHtml;

        // Re-attach click handler
        const newCard = document.getElementById(`beatport-card-${chartHash}`);
        if (newCard) {
            newCard.addEventListener('click', async () => await handleBeatportCardClick(chartHash));
            state.cardElement = newCard;
        }
    }

    // Update clear button state after phase change
    updateBeatportClearButtonState();
}

function updateBeatportCardProgress(chartHash, progress) {
    const state = beatportChartStates[chartHash];
    if (!state) return;

    const card = document.getElementById(`beatport-card-${chartHash}`);
    if (!card) return;

    const progressElement = card.querySelector('.playlist-card-progress');
    if (!progressElement) return;

    const { spotify_total, spotify_matches, failed } = progress;
    const percentage = spotify_total > 0 ? Math.round((spotify_matches / spotify_total) * 100) : 0;

    progressElement.textContent = `♪ ${spotify_total} / ✓ ${spotify_matches} / ✗ ${failed} / ${percentage}%`;
    progressElement.classList.remove('hidden');

    console.log('🎧 Updated Beatport card progress:', chartHash, `${spotify_matches}/${spotify_total} (${percentage}%)`);
}

function switchToBeatportPlaylistsTab() {
    // Switch from "Browse Charts" to "My Playlists" tab
    const browseTab = document.querySelector('.beatport-tab-button[data-beatport-tab="browse"]');
    const playlistsTab = document.querySelector('.beatport-tab-button[data-beatport-tab="playlists"]');
    const browseContent = document.getElementById('beatport-browse-content');
    const playlistsContent = document.getElementById('beatport-playlists-content');

    if (browseTab && playlistsTab && browseContent && playlistsContent) {
        // Update tab buttons
        browseTab.classList.remove('active');
        playlistsTab.classList.add('active');

        // Update tab content
        browseContent.classList.remove('active');
        playlistsContent.classList.add('active');

        console.log('🔄 Switched to Beatport "My Playlists" tab');
    }
}

// ===============================
// BEATPORT SYNC FUNCTIONALITY
// ===============================

async function startBeatportPlaylistSync(urlHash) {
    try {
        console.log('🎧 Starting Beatport playlist sync:', urlHash);

        const state = youtubePlaylistStates[urlHash];
        if (!state || !state.is_beatport_playlist) {
            console.error('❌ Invalid Beatport playlist state for sync');
            showToast('Invalid Beatport playlist state', 'error');
            return;
        }

        // Call Beatport sync endpoint
        const response = await fetch(`/api/beatport/sync/start/${urlHash}`, {
            method: 'POST'
        });

        const result = await response.json();

        if (result.error) {
            showToast(`Error starting sync: ${result.error}`, 'error');
            return;
        }

        // Capture sync_playlist_id for WebSocket subscription (Beatport returns sync_id)
        const syncPlaylistId = result.sync_id || result.sync_playlist_id;
        if (state) state.syncPlaylistId = syncPlaylistId;

        // Update state to syncing
        state.phase = 'syncing';
        updateBeatportCardPhase(state.beatport_chart_hash || urlHash, 'syncing');

        // Update modal buttons and start polling
        updateBeatportModalButtons(urlHash, 'syncing');
        startBeatportSyncPolling(urlHash, syncPlaylistId);

        showToast('Starting Beatport playlist sync...', 'success');

    } catch (error) {
        console.error('❌ Error starting Beatport sync:', error);
        showToast(`Error starting sync: ${error.message}`, 'error');
    }
}

function startBeatportSyncPolling(urlHash, syncPlaylistId) {
    // Stop any existing polling (reuse activeYouTubePollers for Beatport)
    if (activeYouTubePollers[urlHash]) {
        clearInterval(activeYouTubePollers[urlHash]);
    }

    // Resolve syncPlaylistId from argument or stored state
    const bpState = youtubePlaylistStates[urlHash];
    syncPlaylistId = syncPlaylistId || (bpState && bpState.syncPlaylistId);

    // Phase 6: Subscribe via WebSocket
    if (socketConnected && syncPlaylistId) {
        socket.emit('sync:subscribe', { playlist_ids: [syncPlaylistId] });
        _syncProgressCallbacks[syncPlaylistId] = (data) => {
            const progress = data.progress || {};
            updateBeatportModalSyncProgress(urlHash, progress);

            if (data.status === 'finished' || data.status === 'error' || data.status === 'cancelled') {
                if (activeYouTubePollers[urlHash]) { clearInterval(activeYouTubePollers[urlHash]); delete activeYouTubePollers[urlHash]; }
                socket.emit('sync:unsubscribe', { playlist_ids: [syncPlaylistId] });
                delete _syncProgressCallbacks[syncPlaylistId];

                const state = youtubePlaylistStates[urlHash];
                if (state) {
                    const chartHash = state.beatport_chart_hash || urlHash;
                    if (data.status === 'finished') {
                        state.phase = 'sync_complete';
                        updateBeatportCardPhase(chartHash, 'sync_complete');
                        updateBeatportModalButtons(urlHash, 'sync_complete');
                        if (beatportChartStates[chartHash]) beatportChartStates[chartHash].phase = 'sync_complete';
                    } else {
                        state.phase = 'discovered';
                        updateBeatportCardPhase(chartHash, 'discovered');
                        if (beatportChartStates[chartHash]) beatportChartStates[chartHash].phase = 'discovered';
                    }
                }
            }
        };
    }

    // Define the polling function (HTTP fallback)
    const pollFunction = async () => {
        if (socketConnected) return; // Phase 6: WS handles updates
        try {
            const response = await fetch(`/api/beatport/sync/status/${urlHash}`);
            const status = await response.json();

            if (status.error) {
                console.error('❌ Error polling Beatport sync:', status.error);
                clearInterval(pollInterval);
                delete activeYouTubePollers[urlHash];
                return;
            }

            updateBeatportModalSyncProgress(urlHash, status.progress);

            if (status.complete || status.status === 'error') {
                const state = youtubePlaylistStates[urlHash];
                if (state) {
                    const chartHash = state.beatport_chart_hash || urlHash;
                    if (status.complete) {
                        state.phase = 'sync_complete';
                        state.convertedSpotifyPlaylistId = status.converted_spotify_playlist_id;
                        updateBeatportCardPhase(chartHash, 'sync_complete');
                        updateBeatportModalButtons(urlHash, 'sync_complete');
                        if (beatportChartStates[chartHash]) beatportChartStates[chartHash].phase = 'sync_complete';
                    } else {
                        state.phase = 'discovered';
                        updateBeatportCardPhase(chartHash, 'discovered');
                        if (beatportChartStates[chartHash]) beatportChartStates[chartHash].phase = 'discovered';
                    }
                }
                clearInterval(pollInterval);
                delete activeYouTubePollers[urlHash];
            }
        } catch (error) {
            console.error('❌ Error polling Beatport sync:', error);
            if (activeYouTubePollers[urlHash]) {
                clearInterval(activeYouTubePollers[urlHash]);
                delete activeYouTubePollers[urlHash];
            }
        }
    };

    // Run immediately to get current status (skip if WS active)
    if (!socketConnected) pollFunction();

    // Then continue polling at regular intervals
    const pollInterval = setInterval(pollFunction, 2000);
    activeYouTubePollers[urlHash] = pollInterval;
}

async function cancelBeatportSync(urlHash) {
    try {
        console.log('❌ Cancelling Beatport sync:', urlHash);

        const state = youtubePlaylistStates[urlHash];
        if (!state || !state.is_beatport_playlist) {
            console.error('❌ Invalid Beatport playlist state');
            return;
        }

        const response = await fetch(`/api/beatport/sync/cancel/${urlHash}`, {
            method: 'POST'
        });

        const result = await response.json();

        if (result.error) {
            showToast(`Error cancelling sync: ${result.error}`, 'error');
            return;
        }

        // Stop polling
        if (activeYouTubePollers[urlHash]) {
            clearInterval(activeYouTubePollers[urlHash]);
            delete activeYouTubePollers[urlHash];
        }

        // Phase 6: Clean up WS subscription
        const bpSyncId = state && state.syncPlaylistId;
        if (bpSyncId && _syncProgressCallbacks[bpSyncId]) {
            if (socketConnected) socket.emit('sync:unsubscribe', { playlist_ids: [bpSyncId] });
            delete _syncProgressCallbacks[bpSyncId];
        }

        // Revert to discovered phase
        const chartHash = state.beatport_chart_hash || urlHash;
        state.phase = 'discovered';
        updateBeatportCardPhase(chartHash, 'discovered');
        updateBeatportModalButtons(urlHash, 'discovered');

        // Sync with backend Beatport chart state
        if (beatportChartStates[chartHash]) {
            beatportChartStates[chartHash].phase = 'discovered';
        }

        showToast('Beatport sync cancelled', 'info');

    } catch (error) {
        console.error('❌ Error cancelling Beatport sync:', error);
        showToast(`Error cancelling sync: ${error.message}`, 'error');
    }
}

function updateBeatportModalSyncProgress(urlHash, progress) {
    const statusDisplay = document.getElementById(`beatport-sync-status-${urlHash}`);
    if (!statusDisplay || !progress) return;

    console.log(`📊 Updating Beatport modal sync progress for ${urlHash}:`, progress);

    // Update individual counters with Beatport-specific IDs
    const totalEl = document.getElementById(`beatport-total-${urlHash}`);
    const matchedEl = document.getElementById(`beatport-matched-${urlHash}`);
    const failedEl = document.getElementById(`beatport-failed-${urlHash}`);
    const percentageEl = document.getElementById(`beatport-percentage-${urlHash}`);

    const total = progress.total_tracks || 0;
    const matched = progress.matched_tracks || 0;
    const failed = progress.failed_tracks || 0;
    const percentage = total > 0 ? Math.round((matched / total) * 100) : 0;

    if (totalEl) totalEl.textContent = total;
    if (matchedEl) matchedEl.textContent = matched;
    if (failedEl) failedEl.textContent = failed;
    if (percentageEl) percentageEl.textContent = percentage;
}

function updateBeatportModalButtons(urlHash, phase) {
    const modal = document.getElementById(`youtube-discovery-modal-${urlHash}`);
    if (!modal) return;

    const footerLeft = modal.querySelector('.modal-footer-left');
    if (footerLeft) {
        footerLeft.innerHTML = getModalActionButtons(urlHash, phase);
    }
}

async function startBeatportDownloadMissing(urlHash) {
    try {
        console.log('🔍 Starting download missing tracks for Beatport chart:', urlHash);

        const state = youtubePlaylistStates[urlHash];
        // Support both camelCase and snake_case
        const discoveryResults = state?.discoveryResults || state?.discovery_results;

        if (!state || !discoveryResults) {
            showToast('No discovery results available for download', 'error');
            return;
        }

        if (!state.is_beatport_playlist) {
            console.error('❌ State is not a Beatport playlist');
            showToast('Invalid Beatport chart state', 'error');
            return;
        }

        // Convert Beatport discovery results to Spotify tracks format (like Tidal does)
        console.log(`🔍 Total discovery results: ${discoveryResults.length}`);
        console.log(`🔍 First result (full object):`, JSON.stringify(discoveryResults[0], null, 2));
        console.log(`🔍 Second result (full object):`, JSON.stringify(discoveryResults[1], null, 2));
        console.log(`🔍 Results with spotify_data:`, discoveryResults.filter(r => r.spotify_data).length);
        console.log(`🔍 Results with spotify_id:`, discoveryResults.filter(r => r.spotify_id).length);

        const spotifyTracks = discoveryResults
            .filter(result => {
                // Accept if has spotify_data OR if has spotify_track (from automatic discovery)
                return result.spotify_data || (result.spotify_track && result.status_class === 'found');
            })
            .map(result => {
                // Use spotify_data if available, otherwise build from individual fields
                let track;
                if (result.spotify_data) {
                    track = result.spotify_data;
                } else {
                    // Build from individual fields (automatic discovery format)
                    // Convert album to proper object format for wishlist compatibility
                    const albumData = result.spotify_album || 'Unknown Album';
                    const albumObject = typeof albumData === 'object' && albumData !== null
                        ? albumData
                        : {
                            name: typeof albumData === 'string' ? albumData : 'Unknown Album',
                            album_type: 'album',
                            images: []
                        };

                    track = {
                        id: result.spotify_id || 'unknown',
                        name: result.spotify_track || 'Unknown Track',
                        artists: result.spotify_artist ? [result.spotify_artist] : ['Unknown Artist'],
                        album: albumObject,
                        duration_ms: 0
                    };
                }

                // Ensure artists is an array of strings
                if (track.artists && Array.isArray(track.artists)) {
                    track.artists = track.artists.map(artist =>
                        typeof artist === 'string' ? artist : (artist.name || artist)
                    );
                } else if (track.artists && typeof track.artists === 'string') {
                    track.artists = [track.artists];
                } else {
                    track.artists = ['Unknown Artist'];
                }

                // Ensure album is an object (in case it was converted back to string somehow)
                const albumForReturn = typeof track.album === 'object' && track.album !== null
                    ? track.album
                    : {
                        name: typeof track.album === 'string' ? track.album : 'Unknown Album',
                        album_type: 'album',
                        images: []
                    };

                return {
                    id: track.id,
                    name: track.name,
                    artists: track.artists,
                    album: albumForReturn,
                    duration_ms: track.duration_ms || 0,
                    external_urls: track.external_urls || {}
                };
            });

        if (spotifyTracks.length === 0) {
            showToast('No Spotify matches found for download', 'error');
            return;
        }

        console.log(`🎧 Found ${spotifyTracks.length} Spotify tracks for Beatport download`);

        // Create a virtual playlist for the download system
        const virtualPlaylistId = `beatport_${urlHash}`;
        const playlistName = state.playlist.name;

        // Store reference for card navigation (but don't change phase yet)
        state.convertedSpotifyPlaylistId = virtualPlaylistId;

        // Store converted playlist ID in backend but keep current phase
        const chartHash = state.beatport_chart_hash || urlHash;
        if (beatportChartStates[chartHash]) {
            try {
                await fetch(`/api/beatport/charts/update-phase/${chartHash}`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        phase: state.phase, // Keep current phase (should be 'discovered')
                        converted_spotify_playlist_id: virtualPlaylistId
                    })
                });
                console.log('✅ Updated backend with Beatport converted playlist ID (phase unchanged)');
            } catch (error) {
                console.warn('⚠️ Error updating backend Beatport state:', error);
            }
        }

        // Close the discovery modal if it's open
        const discoveryModal = document.getElementById(`youtube-discovery-modal-${urlHash}`);
        if (discoveryModal) {
            discoveryModal.classList.add('hidden');
            console.log('🔄 Closed Beatport discovery modal to show download modal');
        }

        // DON'T update card phase here - let the download modal handle phase changes when "Begin Analysis" is clicked

        // Open download missing tracks modal using the same system as YouTube/Tidal
        await openDownloadMissingModalForYouTube(virtualPlaylistId, playlistName, spotifyTracks);

        console.log(`✅ Opened download modal for Beatport chart: ${state.playlist.name}`);

    } catch (error) {
        console.error('❌ Error starting Beatport download missing tracks:', error);
        showToast(`Error starting downloads: ${error.message}`, 'error');
    }
}

async function handleBeatportChartClick(chartType, chartId, chartName, chartEndpoint) {
    console.log(`🎵 Beatport chart clicked: ${chartType} - ${chartId} - ${chartName}`);

    try {
        showToast(`Loading ${chartName}...`, 'info');
        showLoadingOverlay(`Loading ${chartName}...`);

        const response = await fetch(`${chartEndpoint}?limit=100`);
        if (!response.ok) {
            throw new Error(`Failed to fetch ${chartName}: ${response.status}`);
        }

        const data = await response.json();
        if (!data.success || !data.tracks || data.tracks.length === 0) {
            throw new Error(`No tracks found in ${chartName}`);
        }

        console.log(`✅ Fetched ${data.tracks.length} tracks from ${chartName}`);
        hideLoadingOverlay();
        openBeatportChartAsDownloadModal(data.tracks, chartName, null);

    } catch (error) {
        console.error(`❌ Error handling Beatport chart click:`, error);
        hideLoadingOverlay();
        showToast(`Error loading ${chartName || chartId}: ${error.message}`, 'error');
    }
}

function handleBeatportGenreClick(genreSlug, genreId, genreName) {
    console.log(`🎵 Beatport genre clicked: ${genreName} (${genreSlug}/${genreId}) - SHOWING GENRE DETAIL VIEW`);
    console.log(`📝 Debug: Parameters received - Slug: ${genreSlug}, ID: ${genreId}, Name: ${genreName}`);

    // Navigate to genre detail view with proper parameters
    showBeatportGenreDetailView(genreSlug, genreId, genreName);
}

function showBeatportGenreDetailView(genreSlug, genreId, genreName) {
    console.log(`🎯 Showing genre detail view for: ${genreName}`);
    console.log(`📝 Debug: Function called with - Slug: ${genreSlug}, ID: ${genreId}, Name: ${genreName}`);

    // Hide all other beatport views
    document.querySelectorAll('.beatport-sub-view').forEach(view => {
        view.classList.remove('active');
    });
    const mainView = document.getElementById('beatport-main-view');
    if (mainView) {
        mainView.classList.remove('active');
    }

    // Show genre detail view
    const genreDetailView = document.getElementById('beatport-genre-detail-view');
    if (genreDetailView) {
        genreDetailView.classList.add('active');
        console.log(`📝 Debug: Genre detail view element found and activated`);

        // Update view content
        const titleElement = document.getElementById('genre-detail-title');
        const breadcrumbElement = document.getElementById('genre-detail-breadcrumb');

        console.log(`📝 Debug: Title element found: ${!!titleElement}, Breadcrumb element found: ${!!breadcrumbElement}`);

        if (titleElement) {
            titleElement.textContent = genreName;
            console.log(`📝 Debug: Updated title to: ${genreName}`);
        }
        if (breadcrumbElement) {
            breadcrumbElement.textContent = `Browse Charts > Genre Explorer > ${genreName} Charts`;
            console.log(`📝 Debug: Updated breadcrumb`);
        }

        // Update chart type titles with genre name
        const chartTitles = [
            'genre-top-10-title',
            'genre-top-100-title',
            'genre-releases-top-10-title',
            'genre-releases-top-100-title',
            'genre-staff-picks-title',
            'genre-latest-releases-title',
            'genre-new-charts-title'
        ];

        chartTitles.forEach(titleId => {
            const element = document.getElementById(titleId);
            if (element) {
                console.log(`📝 Debug: Found chart title element: ${titleId}`);
            } else {
                console.log(`📝 Debug: Missing chart title element: ${titleId}`);
            }
        });

        document.getElementById('genre-top-10-title').textContent = `Top 10 ${genreName}`;
        document.getElementById('genre-top-100-title').textContent = `Top 100 ${genreName}`;
        document.getElementById('genre-releases-top-10-title').textContent = `Top 10 ${genreName} Releases`;
        document.getElementById('genre-releases-top-100-title').textContent = `Top 100 ${genreName} Releases`;
        document.getElementById('genre-staff-picks-title').textContent = `${genreName} Staff Picks`;
        document.getElementById('genre-latest-releases-title').textContent = `Latest ${genreName} Releases`;

        // Update Hype section titles
        document.getElementById('genre-hype-top-10-title').textContent = `${genreName} Hype Top 10`;
        document.getElementById('genre-hype-top-100-title').textContent = `${genreName} Hype Top 100`;
        document.getElementById('genre-hype-picks-title').textContent = `${genreName} Hype Picks`;

        // Load new charts directly (no expansion needed)
        console.log(`🔄 Auto-loading new charts for ${genreName}...`);
        loadNewChartsInline(genreSlug, genreId, genreName);

        // Store current genre data for chart type handlers
        genreDetailView.dataset.genreSlug = genreSlug;
        genreDetailView.dataset.genreId = genreId;
        genreDetailView.dataset.genreName = genreName;

        // Add click handlers to chart type cards
        setupGenreChartTypeHandlers();

        console.log(`✅ Genre detail view shown for ${genreName}`);
    } else {
        console.error('❌ Genre detail view element not found');
    }
}

function setupGenreChartTypeHandlers() {
    const chartTypeCards = document.querySelectorAll('#beatport-genre-detail-view .genre-chart-type-card');

    chartTypeCards.forEach(card => {
        // Remove existing listeners
        card.replaceWith(card.cloneNode(true));
    });

    // Re-select after cloning
    const newChartTypeCards = document.querySelectorAll('#beatport-genre-detail-view .genre-chart-type-card');

    newChartTypeCards.forEach(card => {
        card.addEventListener('click', () => {
            const chartType = card.dataset.chartType;
            const genreDetailView = document.getElementById('beatport-genre-detail-view');
            const genreSlug = genreDetailView.dataset.genreSlug;
            const genreId = genreDetailView.dataset.genreId;
            const genreName = genreDetailView.dataset.genreName;

            // All chart types now go directly to discovery modal
            handleGenreChartTypeClick(genreSlug, genreId, genreName, chartType);
        });
    });
}

function showBeatportGenresView() {
    // Hide genre detail view and show genres view
    document.querySelectorAll('.beatport-sub-view').forEach(view => {
        view.classList.remove('active');
    });

    const genresView = document.getElementById('beatport-genres-view');
    if (genresView) {
        genresView.classList.add('active');
    }
}

async function toggleNewChartsExpansion(genreSlug, genreId, genreName) {
    console.log(`📈 Toggling new charts expansion for: ${genreName}`);

    const expandedContent = document.getElementById('new-charts-expanded');
    const expandIndicator = document.getElementById('expand-indicator');
    const chartsCount = document.getElementById('new-charts-count');

    if (!expandedContent || !expandIndicator) {
        console.error('❌ New charts expansion elements not found');
        return;
    }

    // Check if already expanded
    const isExpanded = expandedContent.style.display !== 'none';

    if (isExpanded) {
        // Collapse
        expandedContent.style.display = 'none';
        expandIndicator.classList.remove('expanded');
        console.log('📉 Collapsed new charts section');
    } else {
        // Expand and load charts
        expandedContent.style.display = 'block';
        expandIndicator.classList.add('expanded');

        // Load charts if not already loaded
        await loadNewChartsInline(genreSlug, genreId, genreName);
        console.log('📈 Expanded new charts section');
    }
}

async function loadNewChartsInline(genreSlug, genreId, genreName) {
    const chartsGrid = document.getElementById('new-charts-grid');
    const loadingInline = document.getElementById('charts-loading-inline');

    if (!chartsGrid || !loadingInline) {
        console.error('❌ Inline charts elements not found');
        return;
    }

    // Show loading state
    loadingInline.style.display = 'block';
    chartsGrid.style.display = 'none';
    chartsGrid.innerHTML = '';

    try {
        console.log(`🔍 Loading inline charts for ${genreName}...`);

        // Fetch charts from the new-charts endpoint
        const response = await fetch(`/api/beatport/genre/${genreSlug}/${genreId}/new-charts?limit=20`);
        if (!response.ok) {
            throw new Error(`Failed to fetch charts: ${response.status}`);
        }

        const data = await response.json();
        if (!data.success || !data.tracks || data.tracks.length === 0) {
            // Show empty state
            chartsGrid.innerHTML = `
                <div class="new-charts-empty">
                    <h4>No Charts Available</h4>
                    <p>No curated charts found for ${genreName} at the moment.</p>
                </div>
            `;
        } else {
            // Populate charts grid
            const chartsHTML = data.tracks.map((chart, index) => {
                const chartName = chart.title || 'Untitled Chart';
                const artistName = chart.artist || 'Various Artists';
                const chartUrl = chart.url || '';

                return `
                    <div class="new-chart-item" data-chart-url="${chartUrl}" data-chart-name="${chartName}" data-chart-artist="${artistName}">
                        <div class="new-chart-header">
                            <div class="new-chart-icon">📈</div>
                            <div class="new-chart-title">
                                <h5>${chartName}</h5>
                                <p class="new-chart-artist">by ${artistName}</p>
                            </div>
                        </div>
                        <div class="new-chart-description">
                            Curated ${genreName} chart collection
                        </div>
                        <div class="new-chart-footer">
                            <div class="new-chart-type">Chart</div>
                            <div class="new-chart-action">Explore →</div>
                        </div>
                    </div>
                `;
            }).join('');

            chartsGrid.innerHTML = chartsHTML;

            // Add click handlers to chart items
            setupNewChartItemHandlers(genreSlug, genreId, genreName);
        }

        // Hide loading and show grid
        loadingInline.style.display = 'none';
        chartsGrid.style.display = 'grid';

        console.log(`✅ Loaded ${data.tracks?.length || 0} inline charts for ${genreName}`);
        showToast(`Found ${data.tracks?.length || 0} chart collections`, 'success');

    } catch (error) {
        console.error(`❌ Error loading inline charts for ${genreName}:`, error);

        // Show error state
        chartsGrid.innerHTML = `
            <div class="new-charts-empty">
                <h4>Error Loading Charts</h4>
                <p>Unable to load chart collections for ${genreName}.</p>
            </div>
        `;

        loadingInline.style.display = 'none';
        chartsGrid.style.display = 'grid';

        showToast(`Error loading charts: ${error.message}`, 'error');
    }
}

async function loadDJChartsInline() {
    const chartsGrid = document.getElementById('dj-charts-grid');
    const loadingInline = document.getElementById('dj-charts-loading-inline');

    if (!chartsGrid || !loadingInline) {
        console.error('❌ DJ charts elements not found');
        return;
    }

    // Show loading state
    loadingInline.style.display = 'block';
    chartsGrid.style.display = 'none';
    chartsGrid.innerHTML = '';

    try {
        console.log('🔍 Loading DJ charts...');

        // Fetch charts from the dj-charts-improved endpoint
        const response = await fetch('/api/beatport/dj-charts-improved?limit=20');
        if (!response.ok) {
            throw new Error(`Failed to fetch DJ charts: ${response.status}`);
        }

        const data = await response.json();
        if (!data.success || !data.charts || data.charts.length === 0) {
            // Show empty state
            chartsGrid.innerHTML = `
                <div class="new-charts-empty">
                    <h4>No DJ Charts Available</h4>
                    <p>No DJ curated charts found at the moment.</p>
                </div>
            `;
            loadingInline.style.display = 'none';
            chartsGrid.style.display = 'grid';
            return;
        }

        // Create chart items using New Charts structure
        const chartsHTML = data.charts.map(chart => {
            const chartName = chart.name || chart.title || 'Untitled Chart';
            const artistName = chart.artist || chart.curator || 'Various Artists';
            const chartUrl = chart.url || chart.chart_url || '';

            return `
                <div class="new-chart-item" data-chart-url="${chartUrl}" data-chart-name="${chartName}" data-chart-artist="${artistName}">
                    <div class="new-chart-header">
                        <div class="new-chart-icon">🎧</div>
                        <div class="new-chart-title">
                            <h5>${chartName}</h5>
                            <p class="new-chart-artist">by ${artistName}</p>
                        </div>
                    </div>
                    <div class="new-chart-description">
                        DJ curated chart collection
                    </div>
                    <div class="new-chart-footer">
                        <div class="new-chart-type">DJ Chart</div>
                        <div class="new-chart-action">Explore →</div>
                    </div>
                </div>
            `;
        }).join('');

        chartsGrid.innerHTML = chartsHTML;

        // Hide loading, show content
        loadingInline.style.display = 'none';
        chartsGrid.style.display = 'grid';

        // Setup click handlers for chart items
        setupDJChartItemHandlers();

        console.log(`✅ Loaded ${data.charts.length} DJ charts`);

    } catch (error) {
        console.error('❌ Error loading DJ charts:', error);

        // Show error state
        chartsGrid.innerHTML = `
            <div class="new-charts-empty">
                <h4>Error Loading DJ Charts</h4>
                <p>Unable to load DJ chart collections.</p>
            </div>
        `;

        loadingInline.style.display = 'none';
        chartsGrid.style.display = 'grid';

        showToast(`Error loading DJ charts: ${error.message}`, 'error');
    }
}

async function loadFeaturedChartsInline() {
    const chartsGrid = document.getElementById('featured-charts-grid');
    const loadingInline = document.getElementById('featured-charts-loading-inline');

    if (!chartsGrid || !loadingInline) {
        console.error('❌ Featured charts elements not found');
        return;
    }

    // Show loading state
    loadingInline.style.display = 'block';
    chartsGrid.style.display = 'none';
    chartsGrid.innerHTML = '';

    try {
        console.log('🔍 Loading Featured charts...');

        // Fetch charts from the homepage/featured-charts endpoint
        const response = await fetch('/api/beatport/homepage/featured-charts?limit=20');
        if (!response.ok) {
            throw new Error(`Failed to fetch Featured charts: ${response.status}`);
        }

        const data = await response.json();
        if (!data.success || !data.tracks || data.tracks.length === 0) {
            // Show empty state
            chartsGrid.innerHTML = `
                <div class="new-charts-empty">
                    <h4>No Featured Charts Available</h4>
                    <p>No featured curated charts found at the moment.</p>
                </div>
            `;
            loadingInline.style.display = 'none';
            chartsGrid.style.display = 'grid';
            return;
        }

        // Create chart items using New Charts structure
        const chartsHTML = data.tracks.map(chart => {
            const chartName = chart.name || chart.title || 'Untitled Chart';
            const artistName = chart.artist || chart.curator || 'Various Artists';
            const chartUrl = chart.url || chart.chart_url || '';

            return `
                <div class="new-chart-item" data-chart-url="${chartUrl}" data-chart-name="${chartName}" data-chart-artist="${artistName}">
                    <div class="new-chart-header">
                        <div class="new-chart-icon">⭐</div>
                        <div class="new-chart-title">
                            <h5>${chartName}</h5>
                            <p class="new-chart-artist">by ${artistName}</p>
                        </div>
                    </div>
                    <div class="new-chart-description">
                        Editor curated chart collection
                    </div>
                    <div class="new-chart-footer">
                        <div class="new-chart-type">Featured Chart</div>
                        <div class="new-chart-action">Explore →</div>
                    </div>
                </div>
            `;
        }).join('');

        chartsGrid.innerHTML = chartsHTML;

        // Hide loading, show content
        loadingInline.style.display = 'none';
        chartsGrid.style.display = 'grid';

        // Setup click handlers for chart items
        setupFeaturedChartItemHandlers();

        console.log(`✅ Loaded ${data.tracks.length} Featured charts`);

    } catch (error) {
        console.error('❌ Error loading Featured charts:', error);

        // Show error state
        chartsGrid.innerHTML = `
            <div class="new-charts-empty">
                <h4>Error Loading Featured Charts</h4>
                <p>Unable to load featured chart collections.</p>
            </div>
        `;

        loadingInline.style.display = 'none';
        chartsGrid.style.display = 'grid';

        showToast(`Error loading Featured charts: ${error.message}`, 'error');
    }
}

function setupDJChartItemHandlers() {
    const chartItems = document.querySelectorAll('#dj-charts-grid .new-chart-item');

    chartItems.forEach(item => {
        item.addEventListener('click', async () => {
            const chartName = item.dataset.chartName;
            const chartUrl = item.dataset.chartUrl;

            console.log(`🎧 DJ Chart clicked: ${chartName}`);

            try {
                showToast(`Loading ${chartName}...`, 'info');
                showLoadingOverlay(`Scraping ${chartName}...`);

                const response = await fetch('/api/beatport/chart/extract', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ chart_url: chartUrl, chart_name: chartName, limit: 100, enrich: false })
                });

                if (!response.ok) {
                    throw new Error(`Failed to extract chart tracks: ${response.status}`);
                }

                const data = await response.json();
                if (!data.success || !data.tracks || data.tracks.length === 0) {
                    throw new Error('No tracks found in chart');
                }

                console.log(`✅ Extracted ${data.tracks.length} raw tracks from DJ chart, enriching...`);
                const enrichedTracks = await _enrichTracksWithProgress(data.tracks, chartName);

                hideLoadingOverlay();
                openBeatportChartAsDownloadModal(enrichedTracks, chartName, null);

            } catch (error) {
                console.error('❌ Error extracting DJ chart tracks:', error);
                hideLoadingOverlay();
                showToast(`Error loading chart: ${error.message}`, 'error');
            }
        });
    });
}

function setupFeaturedChartItemHandlers() {
    const chartItems = document.querySelectorAll('#featured-charts-grid .new-chart-item');

    chartItems.forEach(item => {
        item.addEventListener('click', async () => {
            const chartName = item.dataset.chartName;
            const chartUrl = item.dataset.chartUrl;

            console.log(`⭐ Featured Chart clicked: ${chartName}`);

            try {
                showToast(`Loading ${chartName}...`, 'info');
                showLoadingOverlay(`Scraping ${chartName}...`);

                const response = await fetch('/api/beatport/chart/extract', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ chart_url: chartUrl, chart_name: chartName, limit: 100, enrich: false })
                });

                if (!response.ok) {
                    throw new Error(`Failed to extract chart tracks: ${response.status}`);
                }

                const data = await response.json();
                if (!data.success || !data.tracks || data.tracks.length === 0) {
                    throw new Error('No tracks found in chart');
                }

                console.log(`✅ Extracted ${data.tracks.length} raw tracks from Featured chart, enriching...`);
                const enrichedTracks = await _enrichTracksWithProgress(data.tracks, chartName);

                hideLoadingOverlay();
                openBeatportChartAsDownloadModal(enrichedTracks, chartName, null);

            } catch (error) {
                console.error('❌ Error extracting Featured chart tracks:', error);
                hideLoadingOverlay();
                showToast(`Error loading chart: ${error.message}`, 'error');
            }
        });
    });
}

function setupNewChartItemHandlers(genreSlug, genreId, genreName) {
    const chartItems = document.querySelectorAll('#new-charts-grid .new-chart-item');

    chartItems.forEach(item => {
        item.addEventListener('click', async () => {
            const chartName = item.dataset.chartName;
            const chartArtist = item.dataset.chartArtist;
            const chartUrl = item.dataset.chartUrl;

            console.log(`🎵 Chart clicked: ${chartName} by ${chartArtist}`);

            const fullChartName = `${chartName} (${genreName})`;

            try {
                showToast(`Loading ${chartName}...`, 'info');
                showLoadingOverlay(`Scraping ${chartName}...`);

                const response = await fetch('/api/beatport/chart/extract', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ chart_url: chartUrl, chart_name: chartName, limit: 100, enrich: false })
                });

                if (!response.ok) {
                    throw new Error(`Failed to fetch chart content: ${response.status}`);
                }

                const data = await response.json();
                if (!data.success || !data.tracks || data.tracks.length === 0) {
                    throw new Error('No tracks found in chart');
                }

                console.log(`✅ Extracted ${data.tracks.length} raw tracks from ${fullChartName}, enriching...`);
                const enrichedTracks = await _enrichTracksWithProgress(data.tracks, fullChartName);

                hideLoadingOverlay();
                openBeatportChartAsDownloadModal(enrichedTracks, fullChartName, null);

            } catch (error) {
                console.error(`❌ Error loading chart: ${error.message}`);
                hideLoadingOverlay();
                showToast(`Error loading chart: ${error.message}`, 'error');
            }
        });
    });
}

function showBeatportGenreDetailViewFromBack() {
    // Show genre detail view (used by charts list back button)
    document.querySelectorAll('.beatport-sub-view').forEach(view => {
        view.classList.remove('active');
    });

    const genreDetailView = document.getElementById('beatport-genre-detail-view');
    if (genreDetailView) {
        genreDetailView.classList.add('active');
    }
}

async function showBeatportGenreChartsListView(genreSlug, genreId, genreName) {
    console.log(`📈 Showing charts list for: ${genreName}`);

    // Hide all other beatport views
    document.querySelectorAll('.beatport-sub-view').forEach(view => {
        view.classList.remove('active');
    });
    const mainView = document.getElementById('beatport-main-view');
    if (mainView) {
        mainView.classList.remove('active');
    }

    // Show charts list view
    const chartsListView = document.getElementById('beatport-genre-charts-list-view');
    if (chartsListView) {
        chartsListView.classList.add('active');

        // Update view content
        document.getElementById('genre-charts-list-title').textContent = `New ${genreName} Charts`;
        document.getElementById('genre-charts-list-breadcrumb').textContent = `Browse Charts > Genre Explorer > ${genreName} Charts > New Charts`;

        // Store current genre data for individual chart handlers
        chartsListView.dataset.genreSlug = genreSlug;
        chartsListView.dataset.genreId = genreId;
        chartsListView.dataset.genreName = genreName;

        // Load charts for this genre
        await loadGenreChartsList(genreSlug, genreId, genreName);

        console.log(`✅ Charts list view shown for ${genreName}`);
    } else {
        console.error('❌ Charts list view element not found');
    }
}

async function loadGenreChartsList(genreSlug, genreId, genreName) {
    const chartsGrid = document.getElementById('genre-charts-grid');
    const loadingPlaceholder = document.getElementById('charts-loading-placeholder');

    if (!chartsGrid || !loadingPlaceholder) {
        console.error('❌ Charts grid or loading placeholder not found');
        return;
    }

    // Show loading state
    loadingPlaceholder.style.display = 'block';
    chartsGrid.style.display = 'none';
    chartsGrid.innerHTML = '';

    try {
        console.log(`🔍 Loading charts for ${genreName}...`);

        // Fetch charts from the new-charts endpoint
        const response = await fetch(`/api/beatport/genre/${genreSlug}/${genreId}/new-charts?limit=50`);
        if (!response.ok) {
            throw new Error(`Failed to fetch charts: ${response.status}`);
        }

        const data = await response.json();
        if (!data.success || !data.tracks || data.tracks.length === 0) {
            // Show empty state
            chartsGrid.innerHTML = `
                <div class="genre-charts-empty">
                    <h3>No Charts Available</h3>
                    <p>No curated charts found for ${genreName} at the moment.<br>Check back later for new DJ and artist chart collections.</p>
                </div>
            `;
        } else {
            // Populate charts grid
            const chartsHTML = data.tracks.map((chart, index) => {
                const chartName = chart.title || 'Untitled Chart';
                const artistName = chart.artist || 'Various Artists';
                const chartUrl = chart.url || '';

                // Extract chart ID from URL for click handling
                const chartId = chartUrl.split('/').pop() || `chart_${index}`;

                return `
                    <div class="genre-chart-item" data-chart-url="${chartUrl}" data-chart-name="${chartName}" data-chart-artist="${artistName}">
                        <div class="chart-item-header">
                            <div class="chart-item-icon">📈</div>
                            <div class="chart-item-title">
                                <h4>${chartName}</h4>
                                <p class="chart-item-artist">by ${artistName}</p>
                            </div>
                        </div>
                        <div class="chart-item-description">
                            Curated chart collection featuring ${genreName} tracks
                        </div>
                        <div class="chart-item-footer">
                            <div class="chart-item-type">Chart</div>
                            <div class="chart-item-action">Click to explore →</div>
                        </div>
                    </div>
                `;
            }).join('');

            chartsGrid.innerHTML = chartsHTML;

            // Add click handlers to chart items
            setupGenreChartItemHandlers(genreSlug, genreId, genreName);
        }

        // Hide loading and show grid
        loadingPlaceholder.style.display = 'none';
        chartsGrid.style.display = 'grid';

        console.log(`✅ Loaded ${data.tracks?.length || 0} charts for ${genreName}`);
        showToast(`Found ${data.tracks?.length || 0} chart collections`, 'success');

    } catch (error) {
        console.error(`❌ Error loading charts for ${genreName}:`, error);

        // Show error state
        chartsGrid.innerHTML = `
            <div class="genre-charts-empty">
                <h3>Error Loading Charts</h3>
                <p>Unable to load chart collections for ${genreName}.<br>Please try again later.</p>
            </div>
        `;

        loadingPlaceholder.style.display = 'none';
        chartsGrid.style.display = 'grid';

        showToast(`Error loading charts: ${error.message}`, 'error');
    }
}

function setupGenreChartItemHandlers(genreSlug, genreId, genreName) {
    const chartItems = document.querySelectorAll('#genre-charts-grid .genre-chart-item');

    chartItems.forEach(item => {
        item.addEventListener('click', async () => {
            const chartName = item.dataset.chartName;
            const chartArtist = item.dataset.chartArtist;
            const chartUrl = item.dataset.chartUrl;

            console.log(`🎵 Chart clicked: ${chartName} by ${chartArtist}`);

            const fullChartName = `${chartName} (${genreName})`;

            try {
                showToast(`Loading ${chartName}...`, 'info');
                showLoadingOverlay(`Scraping ${chartName}...`);

                const response = await fetch('/api/beatport/chart/extract', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ chart_url: chartUrl, chart_name: chartName, limit: 100, enrich: false })
                });

                if (!response.ok) {
                    throw new Error(`Failed to fetch chart content: ${response.status}`);
                }

                const data = await response.json();
                if (!data.success || !data.tracks || data.tracks.length === 0) {
                    throw new Error('No tracks found in chart');
                }

                console.log(`✅ Extracted ${data.tracks.length} raw tracks from ${fullChartName}, enriching...`);
                const enrichedTracks = await _enrichTracksWithProgress(data.tracks, fullChartName);

                hideLoadingOverlay();
                openBeatportChartAsDownloadModal(enrichedTracks, fullChartName, null);

            } catch (error) {
                console.error(`❌ Error loading chart: ${error.message}`);
                hideLoadingOverlay();
                showToast(`Error loading chart: ${error.message}`, 'error');
            }
        });
    });
}

async function handleGenreChartTypeClick(genreSlug, genreId, genreName, chartType) {
    console.log(`🎯 Genre chart type clicked: ${chartType} for ${genreName} (${genreSlug}/${genreId})`);

    // Map chart types to API endpoints and create descriptive names
    const chartTypeMap = {
        'top-10': {
            endpoint: `/api/beatport/genre/${genreSlug}/${genreId}/top-10`,
            name: `Top 10 ${genreName}`,
            limit: 10
        },
        'top-100': {
            endpoint: `/api/beatport/genre/${genreSlug}/${genreId}/tracks`,
            name: `Top 100 ${genreName}`,
            limit: 100
        },
        'releases-top-10': {
            endpoint: `/api/beatport/genre/${genreSlug}/${genreId}/releases-top-10`,
            name: `Top 10 ${genreName} Releases`,
            limit: 10
        },
        'releases-top-100': {
            endpoint: `/api/beatport/genre/${genreSlug}/${genreId}/releases-top-100`,
            name: `Top 100 ${genreName} Releases`,
            limit: 100
        },
        'staff-picks': {
            endpoint: `/api/beatport/genre/${genreSlug}/${genreId}/staff-picks`,
            name: `${genreName} Staff Picks`,
            limit: 50
        },
        'latest-releases': {
            endpoint: `/api/beatport/genre/${genreSlug}/${genreId}/latest-releases`,
            name: `Latest ${genreName} Releases`,
            limit: 50
        },
        'hype-top-10': {
            endpoint: `/api/beatport/genre/${genreSlug}/${genreId}/hype-top-10`,
            name: `${genreName} Hype Top 10`,
            limit: 10
        },
        'hype-top-100': {
            endpoint: `/api/beatport/genre/${genreSlug}/${genreId}/hype-top-100`,
            name: `${genreName} Hype Top 100`,
            limit: 100
        },
        'hype-picks': {
            endpoint: `/api/beatport/genre/${genreSlug}/${genreId}/hype-picks`,
            name: `${genreName} Hype Picks`,
            limit: 50
        },
        'new-charts': {
            endpoint: `/api/beatport/genre/${genreSlug}/${genreId}/new-charts`,
            name: `New ${genreName} Charts`,
            limit: 100
        }
    };

    const chartConfig = chartTypeMap[chartType];
    if (!chartConfig) {
        console.error(`❌ Unknown chart type: ${chartType}`);
        showToast(`Unknown chart type: ${chartType}`, 'error');
        return;
    }

    try {
        showToast(`Loading ${chartConfig.name}...`, 'info');
        showLoadingOverlay(`Loading ${chartConfig.name}...`);

        const response = await fetch(`${chartConfig.endpoint}?limit=${chartConfig.limit}`);
        if (!response.ok) {
            throw new Error(`Failed to fetch ${chartConfig.name}: ${response.status}`);
        }

        const data = await response.json();
        if (!data.success || !data.tracks || data.tracks.length === 0) {
            throw new Error(`No tracks found in ${chartConfig.name}`);
        }

        console.log(`✅ Fetched ${data.tracks.length} tracks from ${chartConfig.name}`);
        hideLoadingOverlay();
        openBeatportChartAsDownloadModal(data.tracks, chartConfig.name, null);

    } catch (error) {
        console.error(`❌ Error loading ${chartConfig.name}:`, error);
        hideLoadingOverlay();
        showToast(`Error loading ${chartConfig.name}: ${error.message}`, 'error');
    }
}

// ===============================
// SPOTIFY PUBLIC LINK FUNCTIONALITY
// ===============================

let spotifyPublicPlaylists = []; // Array of loaded Spotify public playlist objects
let spotifyPublicPlaylistStates = {}; // Key: url_hash, Value: state dict

async function parseSpotifyPublicUrl() {
    const urlInput = document.getElementById('spotify-public-url-input');
    const url = urlInput.value.trim();

    if (!url) {
        showToast('Please enter a Spotify URL', 'error');
        return;
    }

    // Basic URL validation
    if (!url.includes('open.spotify.com/playlist') && !url.includes('open.spotify.com/album') &&
        !url.startsWith('spotify:playlist:') && !url.startsWith('spotify:album:')) {
        showToast('Please enter a valid Spotify playlist or album URL', 'error');
        return;
    }

    // Check if already loaded
    if (_isUrlAlreadyLoaded('spotify-public', url)) {
        showToast('This playlist is already loaded', 'info');
        urlInput.value = '';
        return;
    }

    const parseBtn = document.getElementById('spotify-public-parse-btn');
    if (parseBtn) {
        parseBtn.disabled = true;
        parseBtn.textContent = 'Loading...';
    }

    try {
        console.log('🎵 Parsing public Spotify URL:', url);

        const response = await fetch('/api/spotify/parse-public', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ url })
        });

        const result = await response.json();

        if (result.error) {
            showToast(`Error: ${result.error}`, 'error');
            return;
        }

        // Check if already loaded
        if (spotifyPublicPlaylists.find(p => String(p.url_hash) === String(result.url_hash))) {
            showToast('This playlist is already loaded', 'info');
            urlInput.value = '';
            return;
        }

        console.log(`✅ Spotify ${result.type} parsed: ${result.name} (${result.track_count} tracks)`);

        spotifyPublicPlaylists.push(result);

        // Auto-mirror
        if (result.tracks && result.tracks.length > 0) {
            mirrorPlaylist('spotify_public', result.url_hash, result.name, result.tracks.map(t => ({
                track_name: t.name || '',
                artist_name: Array.isArray(t.artists) ? t.artists.map(a => a.name).join(', ') : '',
                album_name: t.album?.name || '',
                duration_ms: t.duration_ms || 0,
                source_track_id: t.id || ''
            })), { owner: result.subtitle || '', image_url: '', description: result.url || '' });
        }

        // Save to URL history
        saveUrlHistory('spotify-public', url, result.name);

        renderSpotifyPublicPlaylists();
        await loadSpotifyPublicPlaylistStatesFromBackend();

        urlInput.value = '';
        showToast(`Loaded: ${result.name} (${result.track_count} tracks)`, 'success');
        console.log(`🎵 Loaded Spotify playlist: ${result.name}`);

    } catch (error) {
        console.error('❌ Error parsing Spotify URL:', error);
        showToast(`Error parsing Spotify URL: ${error.message}`, 'error');
    } finally {
        if (parseBtn) {
            parseBtn.disabled = false;
            parseBtn.textContent = 'Load';
        }
    }
}

function renderSpotifyPublicPlaylists() {
    const container = document.getElementById('spotify-public-playlist-container');
    if (spotifyPublicPlaylists.length === 0) {
        container.innerHTML = `<div class="playlist-placeholder">Paste a Spotify playlist or album URL above to load tracks without needing Spotify API credentials.</div>`;
        return;
    }

    container.innerHTML = spotifyPublicPlaylists.map(p => {
        if (!spotifyPublicPlaylistStates[p.url_hash]) {
            spotifyPublicPlaylistStates[p.url_hash] = {
                phase: 'fresh',
                playlist: p
            };
        }
        return createSpotifyPublicCard(p);
    }).join('');

    // Add click handlers to cards
    spotifyPublicPlaylists.forEach(p => {
        const card = document.getElementById(`spotify-public-card-${p.url_hash}`);
        if (card) {
            card.addEventListener('click', () => handleSpotifyPublicCardClick(p.url_hash));
        }
    });
}

function createSpotifyPublicCard(playlist) {
    const state = spotifyPublicPlaylistStates[playlist.url_hash];
    const phase = state ? state.phase : 'fresh';
    const isAlbum = playlist.type === 'album';

    let buttonText = getActionButtonText(phase);
    let phaseText = getPhaseText(phase);
    let phaseColor = getPhaseColor(phase);

    return `
        <div class="youtube-playlist-card spotify-public-card" id="spotify-public-card-${playlist.url_hash}">
            <div class="playlist-card-icon">${isAlbum ? '💿' : '🎵'}</div>
            <div class="playlist-card-content">
                <div class="playlist-card-name">${escapeHtml(playlist.name)}</div>
                <div class="playlist-card-info">
                    <span class="playlist-card-type-badge" style="color: ${isAlbum ? '#b3b3b3' : '#1DB954'}; font-size: 0.75em; text-transform: uppercase; letter-spacing: 0.5px;">${isAlbum ? 'Album' : 'Playlist'}</span>
                    <span class="playlist-card-track-count">${playlist.track_count || playlist.tracks.length} tracks</span>
                    <span class="playlist-card-phase-text" style="color: ${phaseColor};">${phaseText}</span>
                </div>
            </div>
            <div class="playlist-card-progress ${phase === 'fresh' ? 'hidden' : ''}">
                <!-- Progress will be dynamically updated based on phase -->
            </div>
            <button class="playlist-card-action-btn">${buttonText}</button>
        </div>
    `;
}

async function handleSpotifyPublicCardClick(urlHash) {
    const state = spotifyPublicPlaylistStates[urlHash];
    if (!state) {
        console.error(`No state found for Spotify public playlist: ${urlHash}`);
        showToast('Playlist state not found - try refreshing the page', 'error');
        return;
    }

    if (!state.playlist) {
        console.error(`No playlist data found for Spotify public playlist: ${urlHash}`);
        showToast('Playlist data missing - try refreshing the page', 'error');
        return;
    }

    if (!state.phase) {
        state.phase = 'fresh';
    }

    console.log(`🎵 [Card Click] Spotify public card clicked: ${urlHash}, Phase: ${state.phase}`);

    if (state.phase === 'fresh') {
        console.log(`🎵 Using pre-loaded Spotify public playlist data for: ${state.playlist.name}`);
        openSpotifyPublicDiscoveryModal(urlHash, state.playlist);

    } else if (state.phase === 'discovering' || state.phase === 'discovered' || state.phase === 'syncing' || state.phase === 'sync_complete') {
        console.log(`🎵 [Card Click] Opening Spotify public discovery modal for ${state.phase} phase`);

        if (state.phase === 'discovered' && (!state.discovery_results || state.discovery_results.length === 0)) {
            try {
                const stateResponse = await fetch(`/api/spotify-public/state/${urlHash}`);
                if (stateResponse.ok) {
                    const fullState = await stateResponse.json();
                    if (fullState.discovery_results) {
                        state.discovery_results = fullState.discovery_results;
                        state.spotify_matches = fullState.spotify_matches || state.spotify_matches;
                        state.discovery_progress = fullState.discovery_progress || state.discovery_progress;
                        spotifyPublicPlaylistStates[urlHash] = { ...spotifyPublicPlaylistStates[urlHash], ...state };
                        console.log(`Restored ${fullState.discovery_results.length} discovery results from backend`);
                    }
                }
            } catch (error) {
                console.error(`Failed to fetch discovery results from backend: ${error}`);
            }
        }

        openSpotifyPublicDiscoveryModal(urlHash, state.playlist);
    } else if (state.phase === 'downloading' || state.phase === 'download_complete') {
        if (state.convertedSpotifyPlaylistId) {
            if (activeDownloadProcesses[state.convertedSpotifyPlaylistId]) {
                const process = activeDownloadProcesses[state.convertedSpotifyPlaylistId];
                if (process.modalElement) {
                    process.modalElement.style.display = 'flex';
                } else {
                    await rehydrateSpotifyPublicDownloadModal(urlHash, state);
                }
            } else {
                await rehydrateSpotifyPublicDownloadModal(urlHash, state);
            }
        } else {
            if (state.discovery_results && state.discovery_results.length > 0) {
                openSpotifyPublicDiscoveryModal(urlHash, state.playlist);
            } else {
                showToast('Unable to open download modal - missing playlist data', 'error');
            }
        }
    }
}

async function rehydrateSpotifyPublicDownloadModal(urlHash, state) {
    try {
        if (!state || !state.playlist) {
            showToast('Cannot open download modal - invalid playlist data', 'error');
            return;
        }

        const spotifyTracks = state.discovery_results
            ?.filter(result => result.spotify_data)
            ?.map(result => result.spotify_data) || [];

        if (spotifyTracks.length > 0) {
            const virtualPlaylistId = state.convertedSpotifyPlaylistId || `spotify_public_${urlHash}`;
            await openDownloadMissingModalForTidal(virtualPlaylistId, state.playlist.name, spotifyTracks);

            if (state.download_process_id) {
                const process = activeDownloadProcesses[virtualPlaylistId];
                if (process) {
                    process.status = 'running';
                    process.batchId = state.download_process_id;
                    const beginBtn = document.getElementById(`begin-analysis-btn-${virtualPlaylistId}`);
                    const cancelBtn = document.getElementById(`cancel-all-btn-${virtualPlaylistId}`);
                    if (beginBtn) beginBtn.style.display = 'none';
                    if (cancelBtn) cancelBtn.style.display = 'inline-block';
                    startModalDownloadPolling(virtualPlaylistId);
                }
            }
        } else {
            showToast('No Spotify tracks found for download', 'error');
        }
    } catch (error) {
        console.error(`Error rehydrating Spotify public download modal: ${error}`);
    }
}

async function openSpotifyPublicDiscoveryModal(urlHash, playlistData) {
    console.log(`🎵 Opening Spotify public discovery modal (reusing YouTube modal): ${playlistData.name}`);

    const fakeUrlHash = `spotifypublic_${urlHash}`;

    const cardState = spotifyPublicPlaylistStates[urlHash];
    const isAlreadyDiscovered = cardState && (cardState.phase === 'discovered' || cardState.phase === 'syncing' || cardState.phase === 'sync_complete');
    const isCurrentlyDiscovering = cardState && cardState.phase === 'discovering';

    let transformedResults = [];
    let actualMatches = 0;
    if (isAlreadyDiscovered && cardState.discovery_results) {
        transformedResults = cardState.discovery_results.map((result, index) => {
            const isFound = result.status === 'found' ||
                result.status === '✅ Found' ||
                result.status_class === 'found' ||
                result.spotify_data ||
                result.spotify_track;
            if (isFound) actualMatches++;

            return {
                index: index,
                yt_track: result.spotify_public_track ? result.spotify_public_track.name : 'Unknown',
                yt_artist: result.spotify_public_track ? (result.spotify_public_track.artists ? result.spotify_public_track.artists.join(', ') : 'Unknown') : 'Unknown',
                status: isFound ? '✅ Found' : '❌ Not Found',
                status_class: isFound ? 'found' : 'not-found',
                spotify_track: result.spotify_data ? result.spotify_data.name : (result.spotify_track || '-'),
                spotify_artist: result.spotify_data && result.spotify_data.artists ?
                    (Array.isArray(result.spotify_data.artists)
                        ? result.spotify_data.artists
                            .map(a => (typeof a === 'object' && a !== null) ? (a.name || '') : a)
                            .filter(Boolean)
                            .join(', ') || '-'
                        : result.spotify_data.artists)
                    : (result.spotify_artist || '-'),
                spotify_album: result.spotify_data ? (typeof result.spotify_data.album === 'object' ? result.spotify_data.album.name : result.spotify_data.album) : (result.spotify_album || '-'),
                spotify_data: result.spotify_data,
                spotify_id: result.spotify_id,
                manual_match: result.manual_match
            };
        });
        console.log(`🎵 Spotify public modal: Calculated ${actualMatches} matches from ${transformedResults.length} results`);
    }

    // Normalize artist objects to strings for the discovery modal table
    const normalizedTracks = playlistData.tracks.map(t => ({
        ...t,
        artists: Array.isArray(t.artists)
            ? t.artists.map(a => typeof a === 'object' ? a.name : a)
            : t.artists
    }));

    const modalPhase = cardState ? cardState.phase : 'fresh';
    youtubePlaylistStates[fakeUrlHash] = {
        phase: modalPhase,
        playlist: {
            name: playlistData.name,
            tracks: normalizedTracks
        },
        is_spotify_public_playlist: true,
        spotify_public_playlist_id: urlHash,
        discovery_progress: isAlreadyDiscovered ? 100 : 0,
        spotify_matches: isAlreadyDiscovered ? actualMatches : 0,
        spotifyMatches: isAlreadyDiscovered ? actualMatches : 0,
        spotify_total: playlistData.tracks.length,
        discovery_results: transformedResults,
        discoveryResults: transformedResults,
        discoveryProgress: isAlreadyDiscovered ? 100 : 0
    };

    if (!isAlreadyDiscovered && !isCurrentlyDiscovering) {
        try {
            console.log(`🔍 Starting Spotify public discovery for: ${playlistData.name}`);

            const response = await fetch(`/api/spotify-public/discovery/start/${urlHash}`, {
                method: 'POST'
            });

            const result = await response.json();

            if (result.error) {
                console.error('Error starting Spotify public discovery:', result.error);
                showToast(`Error starting discovery: ${result.error}`, 'error');
                return;
            }

            console.log('Spotify public discovery started, beginning polling...');

            spotifyPublicPlaylistStates[urlHash].phase = 'discovering';
            updateSpotifyPublicCardPhase(urlHash, 'discovering');
            youtubePlaylistStates[fakeUrlHash].phase = 'discovering';

            startSpotifyPublicDiscoveryPolling(fakeUrlHash, urlHash);

        } catch (error) {
            console.error('Error starting Spotify public discovery:', error);
            showToast(`Error starting discovery: ${error.message}`, 'error');
        }
    } else if (isCurrentlyDiscovering) {
        console.log(`🔄 Resuming Spotify public discovery polling for: ${playlistData.name}`);
        startSpotifyPublicDiscoveryPolling(fakeUrlHash, urlHash);
    } else if (cardState && cardState.phase === 'syncing') {
        console.log(`🔄 Resuming Spotify public sync polling for: ${playlistData.name}`);
        startSpotifyPublicSyncPolling(fakeUrlHash);
    } else {
        console.log('Using existing results - no need to re-discover');
    }

    openYouTubeDiscoveryModal(fakeUrlHash);
}

function startSpotifyPublicDiscoveryPolling(fakeUrlHash, urlHash) {
    console.log(`🔄 Starting Spotify public discovery polling for: ${urlHash}`);

    if (activeYouTubePollers[fakeUrlHash]) {
        clearInterval(activeYouTubePollers[fakeUrlHash]);
    }

    // WebSocket subscription
    if (socketConnected) {
        socket.emit('discovery:subscribe', { ids: [urlHash] });
        _discoveryProgressCallbacks[urlHash] = (data) => {
            if (data.error) {
                if (activeYouTubePollers[fakeUrlHash]) { clearInterval(activeYouTubePollers[fakeUrlHash]); delete activeYouTubePollers[fakeUrlHash]; }
                socket.emit('discovery:unsubscribe', { ids: [urlHash] }); delete _discoveryProgressCallbacks[urlHash];
                return;
            }
            const transformed = {
                progress: data.progress, spotify_matches: data.spotify_matches, spotify_total: data.spotify_total,
                complete: data.complete,
                results: (data.results || []).map((r, i) => {
                    const isWingIt = r.wing_it_fallback || r.status_class === 'wing-it';
                    const isFound = !isWingIt && (r.status === 'found' || r.status === '✅ Found' || r.status_class === 'found' || r.spotify_data || r.spotify_track);
                    return {
                        index: i, yt_track: r.spotify_public_track ? r.spotify_public_track.name : 'Unknown',
                        yt_artist: r.spotify_public_track ? (r.spotify_public_track.artists ? r.spotify_public_track.artists.join(', ') : 'Unknown') : 'Unknown',
                        status: isWingIt ? '🎯 Wing It' : (isFound ? '✅ Found' : '❌ Not Found'),
                        status_class: isWingIt ? 'wing-it' : (isFound ? 'found' : 'not-found'),
                        spotify_track: r.spotify_data ? r.spotify_data.name : (r.spotify_track || '-'),
                        spotify_artist: r.spotify_data && r.spotify_data.artists
                            ? (Array.isArray(r.spotify_data.artists)
                                ? (r.spotify_data.artists
                                    .map(a => (typeof a === 'object' && a !== null) ? (a.name || '') : a)
                                    .filter(Boolean)
                                    .join(', ') || '-')
                                : r.spotify_data.artists)
                            : (r.spotify_artist || '-'),
                        spotify_album: r.spotify_data ? (typeof r.spotify_data.album === 'object' ? r.spotify_data.album.name : r.spotify_data.album) : (r.spotify_album || '-'),
                        spotify_data: r.spotify_data, spotify_id: r.spotify_id, manual_match: r.manual_match,
                        wing_it_fallback: isWingIt
                    };
                })
            };
            const st = youtubePlaylistStates[fakeUrlHash];
            if (st) {
                st.discovery_progress = data.progress; st.discoveryProgress = data.progress;
                st.spotify_matches = data.spotify_matches; st.spotifyMatches = data.spotify_matches;
                st.discovery_results = data.results; st.discoveryResults = transformed.results;
                st.phase = data.phase;
                updateYouTubeDiscoveryModal(fakeUrlHash, transformed);
            }
            if (spotifyPublicPlaylistStates[urlHash]) {
                spotifyPublicPlaylistStates[urlHash].phase = data.phase;
                spotifyPublicPlaylistStates[urlHash].discovery_results = data.results;
                spotifyPublicPlaylistStates[urlHash].spotify_matches = data.spotify_matches;
                spotifyPublicPlaylistStates[urlHash].discovery_progress = data.progress;
                updateSpotifyPublicCardPhase(urlHash, data.phase);
            }
            updateSpotifyPublicCardProgress(urlHash, data);
            if (data.complete) {
                if (activeYouTubePollers[fakeUrlHash]) { clearInterval(activeYouTubePollers[fakeUrlHash]); delete activeYouTubePollers[fakeUrlHash]; }
                socket.emit('discovery:unsubscribe', { ids: [urlHash] }); delete _discoveryProgressCallbacks[urlHash];
            }
        };
    }

    const pollInterval = setInterval(async () => {
        if (socketConnected) return;
        try {
            const response = await fetch(`/api/spotify-public/discovery/status/${urlHash}`);
            const status = await response.json();

            if (status.error) {
                console.error('Error polling Spotify public discovery status:', status.error);
                clearInterval(pollInterval);
                delete activeYouTubePollers[fakeUrlHash];
                return;
            }

            const transformedStatus = {
                progress: status.progress,
                spotify_matches: status.spotify_matches,
                spotify_total: status.spotify_total,
                complete: status.complete,
                results: status.results.map((result, index) => {
                    const isFound = result.status === 'found' ||
                        result.status === '✅ Found' ||
                        result.status_class === 'found' ||
                        result.spotify_data ||
                        result.spotify_track;

                    return {
                        index: index,
                        yt_track: result.spotify_public_track ? result.spotify_public_track.name : 'Unknown',
                        yt_artist: result.spotify_public_track ? (result.spotify_public_track.artists ? result.spotify_public_track.artists.join(', ') : 'Unknown') : 'Unknown',
                        status: isFound ? '✅ Found' : '❌ Not Found',
                        status_class: isFound ? 'found' : 'not-found',
                        spotify_track: result.spotify_data ? result.spotify_data.name : (result.spotify_track || '-'),
                        spotify_artist: result.spotify_data && result.spotify_data.artists
                            ? (Array.isArray(result.spotify_data.artists)
                                ? (result.spotify_data.artists
                                    .map(a => (typeof a === 'object' && a !== null) ? (a.name || '') : a)
                                    .filter(Boolean)
                                    .join(', ') || '-')
                                : result.spotify_data.artists)
                            : (result.spotify_artist || '-'),
                        spotify_album: result.spotify_data ? (typeof result.spotify_data.album === 'object' ? result.spotify_data.album.name : result.spotify_data.album) : (result.spotify_album || '-'),
                        spotify_data: result.spotify_data,
                        spotify_id: result.spotify_id,
                        manual_match: result.manual_match
                    };
                })
            };

            const state = youtubePlaylistStates[fakeUrlHash];
            if (state) {
                state.discovery_progress = status.progress;
                state.discoveryProgress = status.progress;
                state.spotify_matches = status.spotify_matches;
                state.spotifyMatches = status.spotify_matches;
                state.discovery_results = status.results;
                state.discoveryResults = transformedStatus.results;
                state.phase = status.phase;

                updateYouTubeDiscoveryModal(fakeUrlHash, transformedStatus);

                if (spotifyPublicPlaylistStates[urlHash]) {
                    spotifyPublicPlaylistStates[urlHash].phase = status.phase;
                    spotifyPublicPlaylistStates[urlHash].discovery_results = status.results;
                    spotifyPublicPlaylistStates[urlHash].spotify_matches = status.spotify_matches;
                    spotifyPublicPlaylistStates[urlHash].discovery_progress = status.progress;
                    updateSpotifyPublicCardPhase(urlHash, status.phase);
                }

                updateSpotifyPublicCardProgress(urlHash, status);

                console.log(`🔄 Spotify public discovery progress: ${status.progress}% (${status.spotify_matches}/${status.spotify_total} found)`);
            }

            if (status.complete) {
                console.log(`Spotify public discovery complete: ${status.spotify_matches}/${status.spotify_total} tracks found`);
                clearInterval(pollInterval);
                delete activeYouTubePollers[fakeUrlHash];
            }

        } catch (error) {
            console.error('Error polling Spotify public discovery:', error);
            clearInterval(pollInterval);
            delete activeYouTubePollers[fakeUrlHash];
        }
    }, 1000);

    activeYouTubePollers[fakeUrlHash] = pollInterval;
}

async function loadSpotifyPublicPlaylistStatesFromBackend() {
    try {
        console.log('🎵 Loading Spotify public playlist states from backend...');

        const response = await fetch('/api/spotify-public/playlists/states');
        if (!response.ok) {
            const error = await response.json();
            throw new Error(error.error || 'Failed to fetch Spotify public playlist states');
        }

        const data = await response.json();
        const states = data.states || [];

        console.log(`🎵 Found ${states.length} stored Spotify public playlist states in backend`);

        if (states.length === 0) return;

        for (const stateInfo of states) {
            await applySpotifyPublicPlaylistState(stateInfo);
        }

        // Rehydrate download modals for playlists in downloading/download_complete phases
        for (const stateInfo of states) {
            if ((stateInfo.phase === 'downloading' || stateInfo.phase === 'download_complete') &&
                stateInfo.converted_spotify_playlist_id && stateInfo.download_process_id) {

                const convertedPlaylistId = stateInfo.converted_spotify_playlist_id;

                if (!activeDownloadProcesses[convertedPlaylistId]) {
                    console.log(`Rehydrating download modal for Spotify public playlist: ${stateInfo.playlist_id}`);
                    try {
                        const playlistData = spotifyPublicPlaylists.find(p => String(p.url_hash) === String(stateInfo.playlist_id));
                        if (!playlistData) continue;

                        const spotifyTracks = spotifyPublicPlaylistStates[stateInfo.playlist_id]?.discovery_results
                            ?.filter(result => result.spotify_data)
                            ?.map(result => result.spotify_data) || [];

                        if (spotifyTracks.length > 0) {
                            await openDownloadMissingModalForTidal(
                                convertedPlaylistId,
                                playlistData.name,
                                spotifyTracks
                            );

                            const process = activeDownloadProcesses[convertedPlaylistId];
                            if (process) {
                                process.status = 'running';
                                process.batchId = stateInfo.download_process_id;
                                const beginBtn = document.getElementById(`begin-analysis-btn-${convertedPlaylistId}`);
                                const cancelBtn = document.getElementById(`cancel-all-btn-${convertedPlaylistId}`);
                                if (beginBtn) beginBtn.style.display = 'none';
                                if (cancelBtn) cancelBtn.style.display = 'inline-block';
                                startModalDownloadPolling(convertedPlaylistId);
                            }
                        }
                    } catch (error) {
                        console.error(`Error rehydrating Spotify public download modal for ${stateInfo.playlist_id}:`, error);
                    }
                }
            }
        }

        console.log('Spotify public playlist states loaded and applied');

    } catch (error) {
        console.error('Error loading Spotify public playlist states:', error);
    }
}

async function applySpotifyPublicPlaylistState(stateInfo) {
    const { playlist_id, phase, discovery_progress, spotify_matches, discovery_results, converted_spotify_playlist_id, download_process_id } = stateInfo;

    try {
        console.log(`🎵 Applying saved state for Spotify public playlist: ${playlist_id}, Phase: ${phase}`);

        const playlistData = spotifyPublicPlaylists.find(p => String(p.url_hash) === String(playlist_id));
        if (!playlistData) {
            console.warn(`Playlist data not found for state ${playlist_id} - skipping`);
            return;
        }

        if (!spotifyPublicPlaylistStates[playlist_id]) {
            spotifyPublicPlaylistStates[playlist_id] = {
                playlist: playlistData,
                phase: 'fresh'
            };
        }

        spotifyPublicPlaylistStates[playlist_id].phase = phase;
        spotifyPublicPlaylistStates[playlist_id].discovery_progress = discovery_progress;
        spotifyPublicPlaylistStates[playlist_id].spotify_matches = spotify_matches;
        spotifyPublicPlaylistStates[playlist_id].discovery_results = discovery_results;
        spotifyPublicPlaylistStates[playlist_id].convertedSpotifyPlaylistId = converted_spotify_playlist_id;
        spotifyPublicPlaylistStates[playlist_id].download_process_id = download_process_id;
        spotifyPublicPlaylistStates[playlist_id].playlist = playlistData;

        if (phase !== 'fresh' && phase !== 'discovering') {
            try {
                const stateResponse = await fetch(`/api/spotify-public/state/${playlist_id}`);
                if (stateResponse.ok) {
                    const fullState = await stateResponse.json();
                    if (fullState.discovery_results && spotifyPublicPlaylistStates[playlist_id]) {
                        spotifyPublicPlaylistStates[playlist_id].discovery_results = fullState.discovery_results;
                        spotifyPublicPlaylistStates[playlist_id].discovery_progress = fullState.discovery_progress;
                        spotifyPublicPlaylistStates[playlist_id].spotify_matches = fullState.spotify_matches;
                        spotifyPublicPlaylistStates[playlist_id].convertedSpotifyPlaylistId = fullState.converted_spotify_playlist_id;
                        spotifyPublicPlaylistStates[playlist_id].download_process_id = fullState.download_process_id;
                    }
                }
            } catch (error) {
                console.warn(`Error fetching full discovery results for Spotify public playlist ${playlistData.name}:`, error.message);
            }
        }

        updateSpotifyPublicCardPhase(playlist_id, phase);

        if (phase === 'discovered' && spotifyPublicPlaylistStates[playlist_id]) {
            const progressInfo = {
                spotify_total: playlistData.track_count || playlistData.tracks?.length || 0,
                spotify_matches: spotifyPublicPlaylistStates[playlist_id].spotify_matches || 0
            };
            updateSpotifyPublicCardProgress(playlist_id, progressInfo);
        }

        if (phase === 'discovering') {
            const fakeUrlHash = `spotifypublic_${playlist_id}`;
            startSpotifyPublicDiscoveryPolling(fakeUrlHash, playlist_id);
        } else if (phase === 'syncing') {
            const fakeUrlHash = `spotifypublic_${playlist_id}`;
            startSpotifyPublicSyncPolling(fakeUrlHash);
        }

    } catch (error) {
        console.error(`Error applying Spotify public playlist state for ${playlist_id}:`, error);
    }
}

function updateSpotifyPublicCardPhase(urlHash, phase) {
    const state = spotifyPublicPlaylistStates[urlHash];
    if (!state) return;

    state.phase = phase;

    const card = document.getElementById(`spotify-public-card-${urlHash}`);
    if (card) {
        const newCardHtml = createSpotifyPublicCard(state.playlist);
        card.outerHTML = newCardHtml;

        const newCard = document.getElementById(`spotify-public-card-${urlHash}`);
        if (newCard) {
            newCard.addEventListener('click', () => handleSpotifyPublicCardClick(urlHash));
        }

        if ((phase === 'syncing' || phase === 'sync_complete') && state.lastSyncProgress) {
            setTimeout(() => {
                updateSpotifyPublicCardSyncProgress(urlHash, state.lastSyncProgress);
            }, 0);
        }
    }
}

function updateSpotifyPublicCardProgress(urlHash, progress) {
    const state = spotifyPublicPlaylistStates[urlHash];
    if (!state) return;

    const card = document.getElementById(`spotify-public-card-${urlHash}`);
    if (!card) return;

    const progressElement = card.querySelector('.playlist-card-progress');
    if (!progressElement) return;

    progressElement.classList.remove('hidden');

    const total = progress.spotify_total || 0;
    const matches = progress.spotify_matches || 0;

    if (total > 0) {
        progressElement.innerHTML = `
            <div class="playlist-card-sync-status">
                <span class="sync-stat matched-tracks">✓ ${matches}</span>
                <span class="sync-separator">/</span>
                <span class="sync-stat total-tracks">♪ ${total}</span>
            </div>
        `;
    }
}

// ===============================
// SPOTIFY PUBLIC SYNC FUNCTIONALITY
// ===============================

async function startSpotifyPublicPlaylistSync(urlHash) {
    try {
        console.log('🎵 Starting Spotify public playlist sync:', urlHash);

        const state = youtubePlaylistStates[urlHash];
        if (!state || !state.is_spotify_public_playlist) {
            console.error('Invalid Spotify public playlist state for sync');
            return;
        }

        const playlistId = state.spotify_public_playlist_id;
        const response = await fetch(`/api/spotify-public/sync/start/${playlistId}`, {
            method: 'POST'
        });

        const result = await response.json();

        if (result.error) {
            showToast(`Error starting sync: ${result.error}`, 'error');
            return;
        }

        const syncPlaylistId = result.sync_playlist_id;
        if (state) state.syncPlaylistId = syncPlaylistId;

        updateSpotifyPublicCardPhase(playlistId, 'syncing');
        updateSpotifyPublicModalButtons(urlHash, 'syncing');

        startSpotifyPublicSyncPolling(urlHash, syncPlaylistId);

        showToast('Spotify public playlist sync started!', 'success');

    } catch (error) {
        console.error('Error starting Spotify public sync:', error);
        showToast(`Error starting sync: ${error.message}`, 'error');
    }
}

function startSpotifyPublicSyncPolling(urlHash, syncPlaylistId) {
    if (activeYouTubePollers[urlHash]) {
        clearInterval(activeYouTubePollers[urlHash]);
    }

    const state = youtubePlaylistStates[urlHash];
    const playlistId = state.spotify_public_playlist_id;

    syncPlaylistId = syncPlaylistId || (state && state.syncPlaylistId);

    // WebSocket subscription
    if (socketConnected && syncPlaylistId) {
        socket.emit('sync:subscribe', { playlist_ids: [syncPlaylistId] });
        _syncProgressCallbacks[syncPlaylistId] = (data) => {
            const progress = data.progress || {};
            updateSpotifyPublicCardSyncProgress(playlistId, progress);
            updateSpotifyPublicModalSyncProgress(urlHash, progress);

            if (data.status === 'finished') {
                if (activeYouTubePollers[urlHash]) { clearInterval(activeYouTubePollers[urlHash]); delete activeYouTubePollers[urlHash]; }
                socket.emit('sync:unsubscribe', { playlist_ids: [syncPlaylistId] });
                delete _syncProgressCallbacks[syncPlaylistId];
                if (spotifyPublicPlaylistStates[playlistId]) spotifyPublicPlaylistStates[playlistId].phase = 'sync_complete';
                if (youtubePlaylistStates[urlHash]) youtubePlaylistStates[urlHash].phase = 'sync_complete';
                updateSpotifyPublicCardPhase(playlistId, 'sync_complete');
                updateSpotifyPublicModalButtons(urlHash, 'sync_complete');
                showToast('Spotify public playlist sync complete!', 'success');
            } else if (data.status === 'error' || data.status === 'cancelled') {
                if (activeYouTubePollers[urlHash]) { clearInterval(activeYouTubePollers[urlHash]); delete activeYouTubePollers[urlHash]; }
                socket.emit('sync:unsubscribe', { playlist_ids: [syncPlaylistId] });
                delete _syncProgressCallbacks[syncPlaylistId];
                if (spotifyPublicPlaylistStates[playlistId]) spotifyPublicPlaylistStates[playlistId].phase = 'discovered';
                if (youtubePlaylistStates[urlHash]) youtubePlaylistStates[urlHash].phase = 'discovered';
                updateSpotifyPublicCardPhase(playlistId, 'discovered');
                updateSpotifyPublicModalButtons(urlHash, 'discovered');
                showToast(`Sync failed: ${data.error || 'Unknown error'}`, 'error');
            }
        };
    }

    const pollFunction = async () => {
        if (socketConnected) return;
        try {
            const response = await fetch(`/api/spotify-public/sync/status/${playlistId}`);
            const status = await response.json();

            if (status.error) {
                console.error('Error polling Spotify public sync status:', status.error);
                clearInterval(pollInterval);
                delete activeYouTubePollers[urlHash];
                return;
            }

            updateSpotifyPublicCardSyncProgress(playlistId, status.progress);
            updateSpotifyPublicModalSyncProgress(urlHash, status.progress);

            if (status.complete) {
                clearInterval(pollInterval);
                delete activeYouTubePollers[urlHash];
                if (spotifyPublicPlaylistStates[playlistId]) spotifyPublicPlaylistStates[playlistId].phase = 'sync_complete';
                if (youtubePlaylistStates[urlHash]) youtubePlaylistStates[urlHash].phase = 'sync_complete';
                updateSpotifyPublicCardPhase(playlistId, 'sync_complete');
                updateSpotifyPublicModalButtons(urlHash, 'sync_complete');
                showToast('Spotify public playlist sync complete!', 'success');
            } else if (status.sync_status === 'error') {
                clearInterval(pollInterval);
                delete activeYouTubePollers[urlHash];
                if (spotifyPublicPlaylistStates[playlistId]) spotifyPublicPlaylistStates[playlistId].phase = 'discovered';
                if (youtubePlaylistStates[urlHash]) youtubePlaylistStates[urlHash].phase = 'discovered';
                updateSpotifyPublicCardPhase(playlistId, 'discovered');
                updateSpotifyPublicModalButtons(urlHash, 'discovered');
                showToast(`Sync failed: ${status.error || 'Unknown error'}`, 'error');
            }
        } catch (error) {
            console.error('Error polling Spotify public sync:', error);
            if (activeYouTubePollers[urlHash]) {
                clearInterval(activeYouTubePollers[urlHash]);
                delete activeYouTubePollers[urlHash];
            }
        }
    };

    if (!socketConnected) pollFunction();

    const pollInterval = setInterval(pollFunction, 1000);
    activeYouTubePollers[urlHash] = pollInterval;
}

async function cancelSpotifyPublicSync(urlHash) {
    try {
        console.log('Cancelling Spotify public sync:', urlHash);

        const state = youtubePlaylistStates[urlHash];
        if (!state || !state.is_spotify_public_playlist) {
            console.error('Invalid Spotify public playlist state');
            return;
        }

        const playlistId = state.spotify_public_playlist_id;
        const response = await fetch(`/api/spotify-public/sync/cancel/${playlistId}`, {
            method: 'POST'
        });

        const result = await response.json();

        if (result.error) {
            showToast(`Error cancelling sync: ${result.error}`, 'error');
            return;
        }

        if (activeYouTubePollers[urlHash]) {
            clearInterval(activeYouTubePollers[urlHash]);
            delete activeYouTubePollers[urlHash];
        }

        const syncId = state && state.syncPlaylistId;
        if (syncId && _syncProgressCallbacks[syncId]) {
            if (socketConnected) socket.emit('sync:unsubscribe', { playlist_ids: [syncId] });
            delete _syncProgressCallbacks[syncId];
        }

        updateSpotifyPublicCardPhase(playlistId, 'discovered');
        updateSpotifyPublicModalButtons(urlHash, 'discovered');

        showToast('Spotify public sync cancelled', 'info');

    } catch (error) {
        console.error('Error cancelling Spotify public sync:', error);
        showToast(`Error cancelling sync: ${error.message}`, 'error');
    }
}

function updateSpotifyPublicCardSyncProgress(urlHash, progress) {
    const state = spotifyPublicPlaylistStates[urlHash];
    if (!state || !state.playlist || !progress) return;

    state.lastSyncProgress = progress;

    const card = document.getElementById(`spotify-public-card-${urlHash}`);
    if (!card) return;

    const progressElement = card.querySelector('.playlist-card-progress');

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

    if (statusCounterHTML) {
        progressElement.innerHTML = statusCounterHTML;
    }
}

function updateSpotifyPublicModalSyncProgress(urlHash, progress) {
    const statusDisplay = document.getElementById(`spotify-public-sync-status-${urlHash}`);
    if (!statusDisplay || !progress) return;

    const totalEl = document.getElementById(`spotify-public-total-${urlHash}`);
    const matchedEl = document.getElementById(`spotify-public-matched-${urlHash}`);
    const failedEl = document.getElementById(`spotify-public-failed-${urlHash}`);
    const percentageEl = document.getElementById(`spotify-public-percentage-${urlHash}`);

    const total = progress.total_tracks || 0;
    const matched = progress.matched_tracks || 0;
    const failed = progress.failed_tracks || 0;

    if (totalEl) totalEl.textContent = total;
    if (matchedEl) matchedEl.textContent = matched;
    if (failedEl) failedEl.textContent = failed;

    if (total > 0) {
        const processed = matched + failed;
        const percentage = Math.round((processed / total) * 100);
        if (percentageEl) percentageEl.textContent = percentage;
    }
}

function updateSpotifyPublicModalButtons(urlHash, phase) {
    const modal = document.getElementById(`youtube-discovery-modal-${urlHash}`);
    if (!modal) return;

    const footerLeft = modal.querySelector('.modal-footer-left');
    if (footerLeft) {
        footerLeft.innerHTML = getModalActionButtons(urlHash, phase);
    }
}

async function startSpotifyPublicDownloadMissing(urlHash) {
    try {
        console.log('🔍 Starting download missing tracks for Spotify public playlist:', urlHash);

        const state = youtubePlaylistStates[urlHash];
        if (!state || !state.is_spotify_public_playlist) {
            console.error('Invalid Spotify public playlist state for download');
            return;
        }

        const discoveryResults = state.discoveryResults || state.discovery_results;

        if (!discoveryResults) {
            showToast('No discovery results available for download', 'error');
            return;
        }

        const spotifyTracks = [];
        for (const result of discoveryResults) {
            if (result.spotify_data) {
                spotifyTracks.push(result.spotify_data);
            } else if (result.spotify_track && result.status_class === 'found') {
                const albumData = result.spotify_album || 'Unknown Album';
                const albumObject = typeof albumData === 'object' && albumData !== null
                    ? albumData
                    : {
                        name: typeof albumData === 'string' ? albumData : 'Unknown Album',
                        album_type: 'album',
                        images: []
                    };

                spotifyTracks.push({
                    id: result.spotify_id || 'unknown',
                    name: result.spotify_track || 'Unknown Track',
                    artists: result.spotify_artist ? [result.spotify_artist] : ['Unknown Artist'],
                    album: albumObject,
                    duration_ms: 0
                });
            }
        }

        if (spotifyTracks.length === 0) {
            showToast('No Spotify matches found for download', 'error');
            return;
        }

        const realUrlHash = state.spotify_public_playlist_id;
        const virtualPlaylistId = `spotify_public_${realUrlHash}`;
        const playlistName = state.playlist.name;

        state.convertedSpotifyPlaylistId = virtualPlaylistId;

        // Sync convertedSpotifyPlaylistId to spotifyPublicPlaylistStates for card click routing
        if (realUrlHash && spotifyPublicPlaylistStates[realUrlHash]) {
            spotifyPublicPlaylistStates[realUrlHash].convertedSpotifyPlaylistId = virtualPlaylistId;
        }

        const discoveryModal = document.getElementById(`youtube-discovery-modal-${urlHash}`);
        if (discoveryModal) {
            discoveryModal.classList.add('hidden');
        }

        await openDownloadMissingModalForTidal(virtualPlaylistId, playlistName, spotifyTracks);

    } catch (error) {
        console.error('Error starting Spotify public download missing:', error);
        showToast(`Error: ${error.message}`, 'error');
    }
}

// ===============================
// URL HISTORY (Saved playlist URLs)
// ===============================

const URL_HISTORY_MAX = 10;
const URL_HISTORY_SOURCES = {
    youtube: { key: 'soulsync-url-history-youtube', icon: '▶', inputId: 'youtube-url-input', containerId: 'youtube-url-history', loadFn: () => parseYouTubePlaylist() },
    deezer: { key: 'soulsync-url-history-deezer', icon: '🎵', inputId: 'deezer-url-input', containerId: 'deezer-url-history', loadFn: () => loadDeezerPlaylist() },
    'spotify-public': { key: 'soulsync-url-history-spotify-public', icon: '🎧', inputId: 'spotify-public-url-input', containerId: 'spotify-public-url-history', loadFn: () => parseSpotifyPublicUrl() }
};

function getUrlHistory(source) {
    try {
        const cfg = URL_HISTORY_SOURCES[source];
        if (!cfg) return [];
        const raw = localStorage.getItem(cfg.key);
        return raw ? JSON.parse(raw) : [];
    } catch { return []; }
}

function saveUrlHistory(source, url, name) {
    const cfg = URL_HISTORY_SOURCES[source];
    if (!cfg || !url) return;
    let history = getUrlHistory(source);
    // Remove duplicate (same URL)
    history = history.filter(h => h.url !== url);
    // Add to front
    history.unshift({ url, name: name || url, ts: Date.now() });
    // Cap
    if (history.length > URL_HISTORY_MAX) history = history.slice(0, URL_HISTORY_MAX);
    localStorage.setItem(cfg.key, JSON.stringify(history));
    renderUrlHistory(source);
}

function removeUrlHistoryEntry(source, url) {
    const cfg = URL_HISTORY_SOURCES[source];
    if (!cfg) return;
    let history = getUrlHistory(source);
    history = history.filter(h => h.url !== url);
    localStorage.setItem(cfg.key, JSON.stringify(history));
    renderUrlHistory(source);
}

function renderUrlHistory(source) {
    const cfg = URL_HISTORY_SOURCES[source];
    if (!cfg) return;
    const container = document.getElementById(cfg.containerId);
    if (!container) return;
    const history = getUrlHistory(source);
    if (history.length === 0) {
        container.style.display = 'none';
        container.innerHTML = '';
        return;
    }
    container.style.display = 'flex';
    container.innerHTML = `<span class="url-history-bar-label">Recent</span>` +
        history.map(h => {
            const rawName = h.name.length > 30 ? h.name.substring(0, 28) + '...' : h.name;
            const safeName = escapeHtml(rawName);
            const safeTitle = escapeHtml(h.name);
            const safeUrl = h.url.replace(/"/g, '&quot;');
            return `<div class="url-history-pill" data-url="${safeUrl}" title="${safeTitle}">
                <span class="url-history-pill-icon">${cfg.icon}</span>
                <span class="url-history-pill-name">${safeName}</span>
                <button class="url-history-pill-remove" data-source="${source}" data-url="${safeUrl}">&times;</button>
            </div>`;
        }).join('');

    // Pill click → fill input and load (skip if already loaded)
    container.querySelectorAll('.url-history-pill').forEach(pill => {
        pill.addEventListener('click', (e) => {
            // Don't trigger if clicking the X button
            if (e.target.classList.contains('url-history-pill-remove')) return;
            const pillUrl = pill.dataset.url;
            if (_isUrlAlreadyLoaded(source, pillUrl)) {
                showToast('This playlist is already loaded', 'info');
                return;
            }
            const input = document.getElementById(cfg.inputId);
            if (input) input.value = pillUrl;
            cfg.loadFn();
        });
    });

    // X button click → remove entry
    container.querySelectorAll('.url-history-pill-remove').forEach(btn => {
        btn.addEventListener('click', (e) => {
            e.stopPropagation();
            removeUrlHistoryEntry(btn.dataset.source, btn.dataset.url);
        });
    });
}

function _isUrlAlreadyLoaded(source, url) {
    if (source === 'youtube') {
        // Check for existing YouTube card with this URL
        const container = document.getElementById('youtube-playlist-container');
        if (container) {
            const cards = container.querySelectorAll('.youtube-playlist-card[data-url]');
            for (const card of cards) {
                if (card.dataset.url === url) return true;
            }
        }
        return false;
    } else if (source === 'deezer') {
        // Extract playlist ID from URL and check deezerPlaylists array
        const match = url.match(/deezer\.com\/(?:[a-z]{2}\/)?playlist\/(\d+)/i);
        const id = match ? match[1] : (/^\d+$/.test(url) ? url : null);
        if (id && deezerPlaylists.find(p => String(p.id) === String(id))) return true;
        return false;
    } else if (source === 'spotify-public') {
        // Extract Spotify ID from URL and compare against loaded playlists
        const spMatch = url.match(/open\.spotify\.com\/(playlist|album)\/([a-zA-Z0-9]+)/);
        const spId = spMatch ? spMatch[2] : null;
        if (spId && spotifyPublicPlaylists.some(p => p.id === spId)) return true;
        // Fallback: direct URL comparison
        return spotifyPublicPlaylists.some(p => p.url === url);
    }
    return false;
}

function initUrlHistories() {
    for (const source of Object.keys(URL_HISTORY_SOURCES)) {
        renderUrlHistory(source);
    }
}

// ===============================
// YOUTUBE PLAYLIST FUNCTIONALITY
// ===============================

async function parseYouTubePlaylist() {
    const urlInput = document.getElementById('youtube-url-input');
    const url = urlInput.value.trim();

    if (!url) {
        showToast('Please enter a YouTube playlist URL', 'error');
        return;
    }

    // Validate URL format
    if (!url.includes('youtube.com/playlist') && !url.includes('music.youtube.com/playlist')) {
        showToast('Please enter a valid YouTube playlist URL', 'error');
        return;
    }

    // Check if already loaded
    if (_isUrlAlreadyLoaded('youtube', url)) {
        showToast('This playlist is already loaded', 'info');
        urlInput.value = '';
        return;
    }

    try {
        console.log('🎬 Parsing YouTube playlist:', url);

        // Create card immediately in 'fresh' phase
        createYouTubeCard(url, 'fresh');

        // Parse playlist via API
        const response = await fetch('/api/youtube/parse', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            },
            body: JSON.stringify({ url: url })
        });

        const result = await response.json();

        if (result.error) {
            showToast(`Error parsing YouTube playlist: ${result.error}`, 'error');
            removeYouTubeCard(url);
            return;
        }

        console.log('✅ YouTube playlist parsed:', result.name, `(${result.tracks.length} tracks)`);

        // Save to URL history
        saveUrlHistory('youtube', url, result.name);

        // Update card with parsed data and stay in 'fresh' phase
        updateYouTubeCardData(result.url_hash, result);
        updateYouTubeCardPhase(result.url_hash, 'fresh');

        // Auto-mirror this YouTube playlist
        mirrorPlaylist('youtube', result.url_hash, result.name, result.tracks.map(t => ({
            track_name: t.name || t.title || '', artist_name: Array.isArray(t.artists) ? t.artists[0] : (t.artist || ''),
            album_name: '', duration_ms: t.duration_ms || 0, source_track_id: t.id || ''
        })), { description: url });

        // Clear input
        urlInput.value = '';

        // Show success message
        showToast(`YouTube playlist parsed: ${result.name} (${result.tracks.length} tracks)`, 'success');

    } catch (error) {
        console.error('❌ Error parsing YouTube playlist:', error);
        showToast(`Error parsing YouTube playlist: ${error.message}`, 'error');
        removeYouTubeCard(url);
    }
}

function createYouTubeCard(url, phase = 'fresh') {
    const container = document.getElementById('youtube-playlist-container');
    const placeholder = container.querySelector('.playlist-placeholder');

    // Remove placeholder if it exists
    if (placeholder) {
        placeholder.style.display = 'none';
    }

    // Create temporary URL hash for initial card
    const tempHash = btoa(url).substring(0, 8);

    const cardHtml = `
        <div class="youtube-playlist-card" id="youtube-card-${tempHash}" data-url="${url}">
            <div class="playlist-card-icon youtube-icon">▶</div>
            <div class="playlist-card-content">
                <div class="playlist-card-name">Parsing YouTube playlist...</div>
                <div class="playlist-card-info">
                    <span class="playlist-card-track-count">-- tracks</span>
                    <span class="playlist-card-phase-text" style="color: #999;">Loading...</span>
                </div>
            </div>
            <div class="playlist-card-progress hidden">
                ♪ 0 / ✓ 0 / ✗ 0 / 0%
            </div>
            <button class="playlist-card-action-btn" disabled>Parsing...</button>
        </div>
    `;

    container.insertAdjacentHTML('beforeend', cardHtml);

    // Store temporary state
    youtubePlaylistStates[tempHash] = {
        phase: phase,
        url: url,
        cardElement: document.getElementById(`youtube-card-${tempHash}`),
        tempHash: tempHash
    };

    console.log('🃏 Created YouTube card for URL:', url);
}

function updateYouTubeCardData(urlHash, playlistData) {
    // Find the card by URL or temp hash
    let state = youtubePlaylistStates[urlHash];
    if (!state) {
        // Look for temporary card by URL
        const tempState = Object.values(youtubePlaylistStates).find(s => s.url === playlistData.url);
        if (tempState) {
            // Update the state with real hash
            delete youtubePlaylistStates[tempState.tempHash];
            youtubePlaylistStates[urlHash] = tempState;
            state = tempState;

            // Update card ID
            if (state.cardElement) {
                state.cardElement.id = `youtube-card-${urlHash}`;
            }
        }
    }

    if (!state || !state.cardElement) {
        console.error('❌ Could not find YouTube card for hash:', urlHash);
        return;
    }

    const card = state.cardElement;

    // Update card content
    const nameElement = card.querySelector('.playlist-card-name');
    const trackCountElement = card.querySelector('.playlist-card-track-count');

    nameElement.textContent = playlistData.name;
    trackCountElement.textContent = `${playlistData.tracks.length} tracks`;

    // Store playlist data
    state.playlist = playlistData;
    state.urlHash = urlHash;

    // Add click handler for card and action button
    const handleCardClick = () => handleYouTubeCardClick(urlHash);
    const actionBtn = card.querySelector('.playlist-card-action-btn');

    card.addEventListener('click', handleCardClick);
    actionBtn.addEventListener('click', (e) => {
        e.stopPropagation(); // Prevent card click
        handleCardClick();
    });

    console.log('🃏 Updated YouTube card data:', playlistData.name);
}

function updateYouTubeCardPhase(urlHash, phase) {
    const state = youtubePlaylistStates[urlHash];
    if (!state || !state.cardElement) return;

    const card = state.cardElement;
    const phaseTextElement = card.querySelector('.playlist-card-phase-text');
    const actionBtn = card.querySelector('.playlist-card-action-btn');
    const progressElement = card.querySelector('.playlist-card-progress');

    state.phase = phase;

    switch (phase) {
        case 'fresh':
            phaseTextElement.textContent = 'Ready to discover';
            phaseTextElement.style.color = '#999';
            actionBtn.textContent = 'Start Discovery';
            actionBtn.disabled = false;
            progressElement.classList.add('hidden');
            break;

        case 'discovering':
            phaseTextElement.textContent = 'Discovering...';
            phaseTextElement.style.color = '#ffa500'; // Orange
            actionBtn.textContent = 'View Progress';
            actionBtn.disabled = false;
            progressElement.classList.remove('hidden');
            break;

        case 'discovered':
            phaseTextElement.textContent = 'Discovery Complete';
            phaseTextElement.style.color = 'rgb(var(--accent-rgb))'; // Green
            actionBtn.textContent = 'View Details';
            actionBtn.disabled = false;
            progressElement.classList.add('hidden');
            break;

        case 'syncing':
            phaseTextElement.textContent = 'Syncing...';
            phaseTextElement.style.color = '#ffa500'; // Orange
            actionBtn.textContent = 'View Progress';
            actionBtn.disabled = false;
            progressElement.classList.remove('hidden');
            break;

        case 'sync_complete':
            phaseTextElement.textContent = 'Sync Complete';
            phaseTextElement.style.color = 'rgb(var(--accent-rgb))'; // Green
            actionBtn.textContent = 'View Details';
            actionBtn.disabled = false;
            progressElement.classList.add('hidden');
            break;

        case 'downloading':
            phaseTextElement.textContent = 'Downloading...';
            phaseTextElement.style.color = '#ffa500'; // Orange
            actionBtn.textContent = 'View Downloads';
            actionBtn.disabled = false;
            progressElement.classList.remove('hidden');
            break;

        case 'download_complete':
            phaseTextElement.textContent = 'Download Complete';
            phaseTextElement.style.color = 'rgb(var(--accent-rgb))'; // Green
            actionBtn.textContent = 'View Results';
            actionBtn.disabled = false;
            progressElement.classList.add('hidden');
            break;
    }

    console.log('🃏 Updated YouTube card phase:', urlHash, phase);
}

function handleYouTubeCardClick(urlHash) {
    const state = youtubePlaylistStates[urlHash];
    if (!state) return;

    switch (state.phase) {
        case 'fresh':
            // First click: Start discovery and open modal
            console.log('🎬 Starting YouTube discovery for first time:', urlHash);
            updateYouTubeCardPhase(urlHash, 'discovering');
            startYouTubeDiscovery(urlHash);
            openYouTubeDiscoveryModal(urlHash);
            break;

        case 'discovering':
        case 'discovered':
        case 'syncing':
        case 'sync_complete':
            // Open discovery modal with current state
            console.log('🎬 Opening YouTube discovery modal:', urlHash);
            openYouTubeDiscoveryModal(urlHash);
            break;

        case 'downloading':
        case 'download_complete':
            // Open download missing tracks modal
            console.log('🎬 Opening download modal for YouTube playlist:', urlHash);
            // Need to get playlist ID from converted Spotify data
            const spotifyPlaylistId = state.convertedSpotifyPlaylistId;
            if (spotifyPlaylistId) {
                // Check if we have discovery results, if not load them first
                if (!state.discoveryResults || state.discoveryResults.length === 0) {
                    console.log('🔍 Loading discovery results for download modal...');
                    fetch(`/api/youtube/state/${urlHash}`)
                        .then(response => response.json())
                        .then(fullState => {
                            if (fullState.discovery_results) {
                                state.discoveryResults = fullState.discovery_results;
                                console.log(`✅ Loaded ${state.discoveryResults.length} discovery results`);

                                // Now open the modal with the loaded data
                                const playlistName = state.playlist.name;
                                const spotifyTracks = state.discoveryResults
                                    .filter(result => result.spotify_data)
                                    .map(result => result.spotify_data);
                                openDownloadMissingModalForYouTube(spotifyPlaylistId, playlistName, spotifyTracks);
                            } else {
                                console.error('❌ No discovery results found for downloads');
                                showToast('Unable to open download modal - no discovery data', 'error');
                            }
                        })
                        .catch(error => {
                            console.error('❌ Error loading discovery results:', error);
                            showToast('Error loading playlist data', 'error');
                        });
                } else {
                    // Use the YouTube-specific function to maintain proper state linking
                    const playlistName = state.playlist.name;
                    const spotifyTracks = state.discoveryResults
                        .filter(result => result.spotify_data)
                        .map(result => result.spotify_data);
                    openDownloadMissingModalForYouTube(spotifyPlaylistId, playlistName, spotifyTracks);
                }
            } else {
                console.error('❌ No converted Spotify playlist ID found for downloads');
                showToast('Unable to open download modal - missing playlist data', 'error');
            }
            break;
    }
}

function updateYouTubeCardProgress(urlHash, progress) {
    const state = youtubePlaylistStates[urlHash];
    if (!state || !state.cardElement) return;

    const card = state.cardElement;
    const progressElement = card.querySelector('.playlist-card-progress');

    const total = progress.spotify_total || 0;
    const matches = progress.spotify_matches || 0;
    const failed = total - matches;
    const percentage = total > 0 ? Math.round((matches / total) * 100) : 0;

    progressElement.textContent = `♪ ${total} / ✓ ${matches} / ✗ ${failed} / ${percentage}%`;

    console.log('🃏 Updated YouTube card progress:', urlHash, `${matches}/${total} (${percentage}%)`);
}

function removeYouTubeCard(url) {
    const state = Object.values(youtubePlaylistStates).find(s => s.url === url);
    if (state && state.cardElement) {
        state.cardElement.remove();

        // Remove from state
        if (state.urlHash) {
            delete youtubePlaylistStates[state.urlHash];
        } else if (state.tempHash) {
            delete youtubePlaylistStates[state.tempHash];
        }
    }

    // Show placeholder if no cards left
    const container = document.getElementById('youtube-playlist-container');
    const cards = container.querySelectorAll('.youtube-playlist-card');
    const placeholder = container.querySelector('.playlist-placeholder');

    if (cards.length === 0 && placeholder) {
        placeholder.style.display = 'block';
    }
}

async function startYouTubeDiscovery(urlHash) {
    try {
        console.log('🔍 Starting YouTube Spotify discovery for:', urlHash);

        const response = await fetch(`/api/youtube/discovery/start/${urlHash}`, {
            method: 'POST'
        });

        const result = await response.json();

        if (result.error) {
            showToast(`Error starting discovery: ${result.error}`, 'error');
            return;
        }

        // Update frontend phase to match backend
        const state = listenbrainzPlaylistStates[urlHash] || youtubePlaylistStates[urlHash];
        if (state) {
            state.phase = 'discovering';
        }

        // Update modal buttons to show "Discovering..." instead of "Start Discovery"
        updateYouTubeModalButtons(urlHash, 'discovering');

        // Start polling for progress
        startYouTubeDiscoveryPolling(urlHash);

        // Open discovery modal
        openYouTubeDiscoveryModal(urlHash);

    } catch (error) {
        console.error('❌ Error starting YouTube discovery:', error);
        showToast(`Error starting discovery: ${error.message}`, 'error');
    }
}

function startYouTubeDiscoveryPolling(urlHash) {
    // Stop any existing polling
    if (activeYouTubePollers[urlHash]) {
        clearInterval(activeYouTubePollers[urlHash]);
    }

    // Phase 5: Subscribe via WebSocket
    if (socketConnected) {
        socket.emit('discovery:subscribe', { ids: [urlHash] });
        _discoveryProgressCallbacks[urlHash] = (data) => {
            if (data.error) {
                if (activeYouTubePollers[urlHash]) { clearInterval(activeYouTubePollers[urlHash]); delete activeYouTubePollers[urlHash]; }
                socket.emit('discovery:unsubscribe', { ids: [urlHash] }); delete _discoveryProgressCallbacks[urlHash];
                return;
            }
            updateYouTubeCardProgress(urlHash, data);
            const st = youtubePlaylistStates[urlHash];
            if (st) { st.discoveryResults = data.results || []; st.discovery_results = data.results || []; st.discoveryProgress = data.progress || 0; st.spotifyMatches = data.spotify_matches || 0; st.spotify_matches = data.spotify_matches || 0; }
            updateYouTubeDiscoveryModal(urlHash, data);
            if (data.complete) {
                if (activeYouTubePollers[urlHash]) { clearInterval(activeYouTubePollers[urlHash]); delete activeYouTubePollers[urlHash]; }
                socket.emit('discovery:unsubscribe', { ids: [urlHash] }); delete _discoveryProgressCallbacks[urlHash];
                // Update phase in state directly (updateYouTubeCardPhase may skip if no cardElement)
                if (st) st.phase = 'discovered';
                updateYouTubeCardPhase(urlHash, 'discovered');
                updateYouTubeModalButtons(urlHash, 'discovered');
                showToast('Discovery complete!', 'success');
            }
        };
    }

    const pollInterval = setInterval(async () => {
        // Always poll — no dedicated WebSocket events for discovery progress
        try {
            const response = await fetch(`/api/youtube/discovery/status/${urlHash}`);
            const status = await response.json();

            if (status.error) {
                console.error('❌ Error polling YouTube discovery status:', status.error);
                clearInterval(pollInterval);
                delete activeYouTubePollers[urlHash];
                return;
            }

            // Update card progress
            updateYouTubeCardProgress(urlHash, status);

            // Store discovery results and progress in state
            const state = youtubePlaylistStates[urlHash];
            if (state) {
                state.discoveryResults = status.results || [];
                state.discovery_results = status.results || [];
                state.discoveryProgress = status.progress || 0;
                state.spotifyMatches = status.spotify_matches || 0;
                state.spotify_matches = status.spotify_matches || 0;
            }

            // Update modal if open
            updateYouTubeDiscoveryModal(urlHash, status);

            // Check if complete
            if (status.complete) {
                clearInterval(pollInterval);
                delete activeYouTubePollers[urlHash];

                // Update phase in state directly (updateYouTubeCardPhase may skip if no cardElement)
                if (state) state.phase = 'discovered';
                // Update card phase to discovered
                updateYouTubeCardPhase(urlHash, 'discovered');

                // Update modal buttons to show sync and download buttons
                updateYouTubeModalButtons(urlHash, 'discovered');

                console.log('✅ Discovery complete:', urlHash);
                showToast('Discovery complete!', 'success');
            }

        } catch (error) {
            console.error('❌ Error polling YouTube discovery:', error);
            clearInterval(pollInterval);
            delete activeYouTubePollers[urlHash];
        }
    }, 1000);

    activeYouTubePollers[urlHash] = pollInterval;
}

function stopYouTubeDiscoveryPolling(urlHash) {
    if (activeYouTubePollers[urlHash]) {
        clearInterval(activeYouTubePollers[urlHash]);
        delete activeYouTubePollers[urlHash];
        console.log('⏹ Stopped YouTube discovery polling for:', urlHash);
    }
}

function openYouTubeDiscoveryModal(urlHash) {
    // Check ListenBrainz state first, then fallback to YouTube state
    const state = listenbrainzPlaylistStates[urlHash] || youtubePlaylistStates[urlHash];
    if (!state || !state.playlist) {
        console.error('❌ No playlist data found for identifier:', urlHash);
        return;
    }

    console.log('🎵 Opening discovery modal for:', state.playlist.name);

    // Check if modal already exists
    let modal = document.getElementById(`youtube-discovery-modal-${urlHash}`);

    if (modal) {
        // Modal exists, just show it
        modal.classList.remove('hidden');
        console.log('🔄 Showing existing modal with preserved state');
        console.log('🔄 Current discovery results count:', state.discoveryResults?.length || state.discovery_results?.length || 0);

        // Resume polling if discovery or sync is in progress
        if (state.phase === 'discovering' && !activeYouTubePollers[urlHash]) {
            console.log('🔄 Resuming discovery polling...');
            startYouTubeDiscoveryPolling(urlHash);
        } else if (state.phase === 'syncing' && !activeYouTubePollers[urlHash]) {
            console.log('🔄 Resuming sync polling...');
            if (state.is_tidal_playlist) {
                startTidalSyncPolling(urlHash);
            } else if (state.is_deezer_playlist) {
                startDeezerSyncPolling(urlHash);
            } else if (state.is_spotify_public_playlist) {
                startSpotifyPublicSyncPolling(urlHash);
            } else if (state.is_beatport_playlist) {
                startBeatportSyncPolling(urlHash);
            } else if (state.is_listenbrainz_playlist) {
                startListenBrainzSyncPolling(urlHash);
            } else {
                startYouTubeSyncPolling(urlHash);
            }
        }
    } else {
        // Create new modal (support YouTube, Tidal, Deezer, Beatport, ListenBrainz, Spotify Public, and Mirrored)
        const isTidal = state.is_tidal_playlist;
        const isDeezer = state.is_deezer_playlist;
        const isSpotifyPublic = state.is_spotify_public_playlist;
        const isBeatport = state.is_beatport_playlist;
        const isListenBrainz = state.is_listenbrainz_playlist;
        const isMirrored = state.is_mirrored_playlist;
        const isLastfmRadio = typeof urlHash === 'string' && urlHash.startsWith('lastfm_radio_');
        const modalTitle = isMirrored ? '🎵 Mirrored Playlist Discovery' :
            isSpotifyPublic ? '🎵 Spotify Playlist Discovery' :
                isDeezer ? '🎵 Deezer Playlist Discovery' :
                    isTidal ? '🎵 Tidal Playlist Discovery' :
                        isBeatport ? '🎵 Beatport Chart Discovery' :
                            isLastfmRadio ? '📻 Last.fm Radio Discovery' :
                                isListenBrainz ? '🎵 ListenBrainz Playlist Discovery' :
                                    '🎵 YouTube Playlist Discovery';
        const sourceLabel = isMirrored ? (state.mirrored_source ? state.mirrored_source.charAt(0).toUpperCase() + state.mirrored_source.slice(1) : 'Source') :
            isSpotifyPublic ? 'Spotify' :
                isDeezer ? 'Deezer' :
                    isTidal ? 'Tidal' :
                        isBeatport ? 'Beatport' :
                            isLastfmRadio ? 'Last.fm' :
                                isListenBrainz ? 'LB' :
                                    'YT';

        const modalHtml = `
            <div class="modal-overlay" id="youtube-discovery-modal-${urlHash}">
                <div class="youtube-discovery-modal">
                    <div class="modal-header">
                        <h2>${modalTitle}</h2>
                        <div class="modal-subtitle">${state.playlist.name} (${state.playlist.tracks.length} tracks)</div>
                        <div class="modal-description">${getModalDescription(state.phase, isTidal, isBeatport, isListenBrainz, isMirrored, isDeezer, isSpotifyPublic, isLastfmRadio)}</div>
                        <button class="modal-close-btn" onclick="closeYouTubeDiscoveryModal('${urlHash}')">✕</button>
                    </div>

                    <div class="modal-body">
                        <div class="progress-section">
                            <div class="progress-label">🔍 ${currentMusicSourceName} Discovery Progress</div>
                            <div class="progress-bar-container">
                                <div class="progress-bar-fill" id="youtube-discovery-progress-${urlHash}" style="width: 0%;"></div>
                            </div>
                            <div class="progress-text" id="youtube-discovery-progress-text-${urlHash}">${getInitialProgressText(state.phase, isTidal, isBeatport, isListenBrainz)}</div>
                        </div>
                        
                        <div class="discovery-table-container">
                            <table class="discovery-table">
                                <thead>
                                    <tr>
                                        <th>${sourceLabel} Track</th>
                                        <th>${sourceLabel} Artist</th>
                                        <th>Status</th>
                                        <th>${currentMusicSourceName} Track</th>
                                        <th>${currentMusicSourceName} Artist</th>
                                        <th>Album</th>
                                        <th>Actions</th>
                                    </tr>
                                </thead>
                                <tbody id="youtube-discovery-table-${urlHash}">
                                    ${generateTableRowsFromState(state, urlHash)}
                                </tbody>
                            </table>
                        </div>
                    </div>
                    
                    <div class="modal-footer">
                        <div class="modal-footer-left">
                            ${getModalActionButtons(urlHash, state.phase, state)}
                        </div>
                        <div class="modal-footer-right">
                            <button class="modal-btn modal-btn-secondary" onclick="closeYouTubeDiscoveryModal('${urlHash}')">🏠 Close</button>
                        </div>
                    </div>

                    <!-- Discovery Fix Modal (nested inside) -->
                    <div class="discovery-fix-modal-overlay hidden" id="discovery-fix-modal-overlay">
                        <div class="discovery-fix-modal">
                            <div class="discovery-fix-modal-header">
                                <h2>Fix Track Match</h2>
                                <button class="modal-close-btn" onclick="closeDiscoveryFixModal()">✕</button>
                            </div>

                            <div class="discovery-fix-modal-content">
                                <!-- Source track info (read-only) -->
                                <div class="source-track-info">
                                    <h3>Source Track</h3>
                                    <div class="source-track-display">
                                        <div class="source-field">
                                            <label>Track:</label>
                                            <span id="fix-modal-source-track">-</span>
                                        </div>
                                        <div class="source-field">
                                            <label>Artist:</label>
                                            <span id="fix-modal-source-artist">-</span>
                                        </div>
                                    </div>
                                </div>

                                <!-- Search inputs (editable) -->
                                <div class="search-inputs-section">
                                    <h3>Search for Match</h3>
                                    <div class="search-input-group">
                                        <input type="text"
                                               id="fix-modal-track-input"
                                               placeholder="Track name"
                                               class="fix-modal-input">
                                        <input type="text"
                                               id="fix-modal-artist-input"
                                               placeholder="Artist name"
                                               class="fix-modal-input">
                                        <button class="search-btn" onclick="searchDiscoveryFix()">
                                            🔍 Search
                                        </button>
                                    </div>
                                </div>

                                <!-- Search results -->
                                <div class="search-results-section">
                                    <h3>Results</h3>
                                    <div id="fix-modal-results" class="fix-modal-results">
                                        <!-- Auto-populated on modal open, updated on search -->
                                    </div>
                                </div>
                            </div>

                            <div class="discovery-fix-modal-footer">
                                <button class="modal-btn secondary" onclick="closeDiscoveryFixModal()">
                                    Cancel
                                </button>
                            </div>
                        </div>
                    </div>
                </div>
            </div>
        `;

        // Add modal to DOM
        document.body.insertAdjacentHTML('beforeend', modalHtml);
        modal = document.getElementById(`youtube-discovery-modal-${urlHash}`);

        // Store modal reference
        state.modalElement = modal;

        // Set initial progress if we have discovery results
        if (state.discoveryResults && state.discoveryResults.length > 0) {
            // Compute progress from results if discoveryProgress is missing/zero
            let progress = state.discoveryProgress || 0;
            const matches = state.spotifyMatches || 0;
            if (progress === 0 && state.discoveryResults.length > 0 && state.playlist.tracks.length > 0) {
                progress = Math.min(100, Math.round((state.discoveryResults.length / state.playlist.tracks.length) * 100));
            }
            const progressData = {
                progress: progress,
                spotify_matches: matches || state.discoveryResults.filter(r => r.status_class === 'found').length,
                spotify_total: state.playlist.tracks.length,
                results: state.discoveryResults
            };
            updateYouTubeDiscoveryModal(urlHash, progressData);
        }

        // Start polling immediately if modal is opened in syncing phase
        if (state.phase === 'syncing') {
            console.log('🔄 Modal opened in syncing phase - starting immediate polling...');
            if (state.is_tidal_playlist) {
                startTidalSyncPolling(urlHash);
            } else if (state.is_deezer_playlist) {
                startDeezerSyncPolling(urlHash);
            } else if (state.is_spotify_public_playlist) {
                startSpotifyPublicSyncPolling(urlHash);
            } else if (state.is_beatport_playlist) {
                startBeatportSyncPolling(urlHash);
            } else {
                startYouTubeSyncPolling(urlHash);
            }
        }

        console.log('✨ Created new modal with current state');
    }
}

function getModalActionButtons(urlHash, phase, state = null) {
    // Get state if not provided
    if (!state) {
        state = listenbrainzPlaylistStates[urlHash] || youtubePlaylistStates[urlHash];
    }

    const isTidal = state && state.is_tidal_playlist;
    const isDeezer = state && state.is_deezer_playlist;
    const isSpotifyPublic = state && state.is_spotify_public_playlist;
    const isBeatport = state && state.is_beatport_playlist;
    const isListenBrainz = state && state.is_listenbrainz_playlist;

    // Validate data availability for buttons (support both naming conventions)
    const hasDiscoveryResults = state && ((state.discoveryResults && state.discoveryResults.length > 0) || (state.discovery_results && state.discovery_results.length > 0));
    const hasSpotifyMatches = state && ((state.spotifyMatches > 0) || (state.spotify_matches > 0));
    const hasConvertedPlaylistId = state && state.convertedSpotifyPlaylistId;

    switch (phase) {
        case 'fresh':
        case 'discovering':
            // Show start discovery button for fresh playlists
            if (phase === 'fresh') {
                const wingItBtn = ` <span class="wing-it-wrap"><button class="modal-btn wing-it-btn" onclick="_toggleWingItDropdown(this, '${urlHash}')">⚡ Wing It</button></span>`;

                if (isListenBrainz) {
                    return `<button class="modal-btn modal-btn-primary" onclick="startListenBrainzDiscovery('${urlHash}')">🔍 Start Discovery</button>${wingItBtn}`;
                } else {
                    return `<button class="modal-btn modal-btn-primary" onclick="startYouTubeDiscovery('${urlHash}')">🔍 Start Discovery</button>${wingItBtn}`;
                }
            } else {
                // Discovering phase - show progress
                return `<div class="modal-info">🔍 Discovering ${currentMusicSourceName} matches...</div>`;
            }

        case 'discovered':
        case 'downloading':
        case 'download_complete':
            // Only show buttons if we actually have discovery data
            if (!hasDiscoveryResults) {
                return `<div class="modal-info">⚠️ No discovery results available. Try starting discovery again.</div>`;
            }

            let buttons = '';

            // Only show sync button if there are Spotify matches (and not standalone mode)
            if (hasSpotifyMatches && !_isSoulsyncStandalone) {
                if (isListenBrainz) {
                    buttons += `<button class="modal-btn modal-btn-primary" onclick="startListenBrainzPlaylistSync('${urlHash}')">🔄 Sync This Playlist</button>`;
                } else if (isTidal) {
                    buttons += `<button class="modal-btn modal-btn-primary" onclick="startTidalPlaylistSync('${urlHash}')">🔄 Sync This Playlist</button>`;
                } else if (isDeezer) {
                    buttons += `<button class="modal-btn modal-btn-primary" onclick="startDeezerPlaylistSync('${urlHash}')">🔄 Sync This Playlist</button>`;
                } else if (isSpotifyPublic) {
                    buttons += `<button class="modal-btn modal-btn-primary" onclick="startSpotifyPublicPlaylistSync('${urlHash}')">🔄 Sync This Playlist</button>`;
                } else if (isBeatport) {
                    buttons += `<button class="modal-btn modal-btn-primary" onclick="startBeatportPlaylistSync('${urlHash}')">🔄 Sync This Playlist</button>`;
                } else {
                    buttons += `<button class="modal-btn modal-btn-primary" onclick="startYouTubePlaylistSync('${urlHash}')">🔄 Sync This Playlist</button>`;
                }
            }

            // Only show download button if we have matches or a converted playlist ID
            if (hasSpotifyMatches || hasConvertedPlaylistId) {
                if (isListenBrainz) {
                    buttons += `<button class="modal-btn modal-btn-primary" onclick="startYouTubeDownloadMissing('${urlHash}')">🔍 Download Missing Tracks</button>`;
                } else if (isTidal) {
                    buttons += `<button class="modal-btn modal-btn-primary" onclick="startTidalDownloadMissing('${urlHash}')">🔍 Download Missing Tracks</button>`;
                } else if (isDeezer) {
                    buttons += `<button class="modal-btn modal-btn-primary" onclick="startDeezerDownloadMissing('${urlHash}')">🔍 Download Missing Tracks</button>`;
                } else if (isSpotifyPublic) {
                    buttons += `<button class="modal-btn modal-btn-primary" onclick="startSpotifyPublicDownloadMissing('${urlHash}')">🔍 Download Missing Tracks</button>`;
                } else if (isBeatport) {
                    buttons += `<button class="modal-btn modal-btn-primary" onclick="startBeatportDownloadMissing('${urlHash}')">🔍 Download Missing Tracks</button>`;
                } else {
                    buttons += `<button class="modal-btn modal-btn-primary" onclick="startYouTubeDownloadMissing('${urlHash}')">🔍 Download Missing Tracks</button>`;
                }
            }

            // Retry Failed button for mirrored playlists
            if (state && state.is_mirrored_playlist) {
                const results = state.discovery_results || state.discoveryResults || [];
                const failedCount = results.filter(r => r.status_class !== 'found').length;
                if (failedCount > 0) {
                    buttons += `<button class="modal-btn modal-btn-secondary" onclick="retryFailedMirroredDiscovery('${urlHash}')">🔄 Retry Failed (${failedCount})</button>`;
                }
            }

            // Rediscover button — reset and re-run discovery (only for sources with reset endpoints)
            if (isBeatport) {
                buttons += `<button class="modal-btn modal-btn-secondary" onclick="resetBeatportChart('${urlHash}')">🔄 Rediscover</button>`;
            } else if (!isListenBrainz && !isTidal && !isDeezer && !isSpotifyPublic) {
                buttons += `<button class="modal-btn modal-btn-secondary" onclick="resetYouTubePlaylist('${urlHash}')">🔄 Rediscover</button>`;
            }

            // Wing It button — available in discovered phase
            buttons += ` <span class="wing-it-wrap"><button class="modal-btn wing-it-btn" onclick="_toggleWingItDropdown(this, '${urlHash}')">⚡ Wing It</button></span>`;

            if (!buttons || buttons.trim().startsWith('<button class="modal-btn wing-it-btn"')) {
                buttons = `<div class="modal-info">ℹ️ No Spotify matches found.</div>` + buttons;
            }

            return buttons;

        case 'syncing':
            if (isListenBrainz) {
                return `
                    <button class="modal-btn modal-btn-danger" onclick="cancelYouTubeSync('${urlHash}')">❌ Cancel Sync</button>
                    <div class="playlist-modal-sync-status" id="listenbrainz-sync-status-${urlHash}" style="display: flex;">
                        <span class="sync-stat total-tracks">♪ <span id="listenbrainz-total-${urlHash}">0</span></span>
                        <span class="sync-separator">/</span>
                        <span class="sync-stat matched-tracks">✓ <span id="listenbrainz-matched-${urlHash}">0</span></span>
                        <span class="sync-separator">/</span>
                        <span class="sync-stat failed-tracks">✗ <span id="listenbrainz-failed-${urlHash}">0</span></span>
                        <span class="sync-stat percentage">(<span id="listenbrainz-percentage-${urlHash}">0</span>%)</span>
                    </div>
                `;
            } else if (isTidal) {
                return `
                    <button class="modal-btn modal-btn-danger" onclick="cancelTidalSync('${urlHash}')">❌ Cancel Sync</button>
                    <div class="playlist-modal-sync-status" id="tidal-sync-status-${urlHash}" style="display: flex;">
                        <span class="sync-stat total-tracks">♪ <span id="tidal-total-${urlHash}">0</span></span>
                        <span class="sync-separator">/</span>
                        <span class="sync-stat matched-tracks">✓ <span id="tidal-matched-${urlHash}">0</span></span>
                        <span class="sync-separator">/</span>
                        <span class="sync-stat failed-tracks">✗ <span id="tidal-failed-${urlHash}">0</span></span>
                        <span class="sync-stat percentage">(<span id="tidal-percentage-${urlHash}">0</span>%)</span>
                    </div>
                `;
            } else if (isDeezer) {
                return `
                    <button class="modal-btn modal-btn-danger" onclick="cancelDeezerSync('${urlHash}')">❌ Cancel Sync</button>
                    <div class="playlist-modal-sync-status" id="deezer-sync-status-${urlHash}" style="display: flex;">
                        <span class="sync-stat total-tracks">♪ <span id="deezer-total-${urlHash}">0</span></span>
                        <span class="sync-separator">/</span>
                        <span class="sync-stat matched-tracks">✓ <span id="deezer-matched-${urlHash}">0</span></span>
                        <span class="sync-separator">/</span>
                        <span class="sync-stat failed-tracks">✗ <span id="deezer-failed-${urlHash}">0</span></span>
                        <span class="sync-stat percentage">(<span id="deezer-percentage-${urlHash}">0</span>%)</span>
                    </div>
                `;
            } else if (isSpotifyPublic) {
                return `
                    <button class="modal-btn modal-btn-danger" onclick="cancelSpotifyPublicSync('${urlHash}')">❌ Cancel Sync</button>
                    <div class="playlist-modal-sync-status" id="spotify-public-sync-status-${urlHash}" style="display: flex;">
                        <span class="sync-stat total-tracks">♪ <span id="spotify-public-total-${urlHash}">0</span></span>
                        <span class="sync-separator">/</span>
                        <span class="sync-stat matched-tracks">✓ <span id="spotify-public-matched-${urlHash}">0</span></span>
                        <span class="sync-separator">/</span>
                        <span class="sync-stat failed-tracks">✗ <span id="spotify-public-failed-${urlHash}">0</span></span>
                        <span class="sync-stat percentage">(<span id="spotify-public-percentage-${urlHash}">0</span>%)</span>
                    </div>
                `;
            } else if (isBeatport) {
                return `
                    <button class="modal-btn modal-btn-danger" onclick="cancelBeatportSync('${urlHash}')">❌ Cancel Sync</button>
                    <div class="playlist-modal-sync-status" id="beatport-sync-status-${urlHash}" style="display: flex;">
                        <span class="sync-stat total-tracks">♪ <span id="beatport-total-${urlHash}">0</span></span>
                        <span class="sync-separator">/</span>
                        <span class="sync-stat matched-tracks">✓ <span id="beatport-matched-${urlHash}">0</span></span>
                        <span class="sync-separator">/</span>
                        <span class="sync-stat failed-tracks">✗ <span id="beatport-failed-${urlHash}">0</span></span>
                        <span class="sync-stat percentage">(<span id="beatport-percentage-${urlHash}">0</span>%)</span>
                    </div>
                `;
            } else {
                return `
                    <button class="modal-btn modal-btn-danger" onclick="cancelYouTubeSync('${urlHash}')">❌ Cancel Sync</button>
                    <div class="playlist-modal-sync-status" id="youtube-sync-status-${urlHash}" style="display: flex;">
                        <span class="sync-stat total-tracks">♪ <span id="youtube-total-${urlHash}">0</span></span>
                        <span class="sync-separator">/</span>
                        <span class="sync-stat matched-tracks">✓ <span id="youtube-matched-${urlHash}">0</span></span>
                        <span class="sync-separator">/</span>
                        <span class="sync-stat failed-tracks">✗ <span id="youtube-failed-${urlHash}">0</span></span>
                        <span class="sync-stat percentage">(<span id="youtube-percentage-${urlHash}">0</span>%)</span>
                    </div>
                `;
            }

        case 'sync_complete':
            let syncCompleteButtons = '';

            // Only show sync button if there are Spotify matches (and not standalone mode)
            if (hasSpotifyMatches && !_isSoulsyncStandalone) {
                if (isListenBrainz) {
                    syncCompleteButtons += `<button class="modal-btn modal-btn-primary" onclick="startListenBrainzPlaylistSync('${urlHash}')">🔄 Sync This Playlist</button>`;
                } else if (isTidal) {
                    syncCompleteButtons += `<button class="modal-btn modal-btn-primary" onclick="startTidalPlaylistSync('${urlHash}')">🔄 Sync This Playlist</button>`;
                } else if (isSpotifyPublic) {
                    syncCompleteButtons += `<button class="modal-btn modal-btn-primary" onclick="startSpotifyPublicPlaylistSync('${urlHash}')">🔄 Sync This Playlist</button>`;
                } else if (isBeatport) {
                    syncCompleteButtons += `<button class="modal-btn modal-btn-primary" onclick="startBeatportPlaylistSync('${urlHash}')">🔄 Sync This Playlist</button>`;
                } else {
                    syncCompleteButtons += `<button class="modal-btn modal-btn-primary" onclick="startYouTubePlaylistSync('${urlHash}')">🔄 Sync This Playlist</button>`;
                }
            }

            // Only show download button if we have matches or a converted playlist ID
            if (hasSpotifyMatches || hasConvertedPlaylistId) {
                if (isListenBrainz) {
                    syncCompleteButtons += `<button class="modal-btn modal-btn-primary" onclick="startYouTubeDownloadMissing('${urlHash}')">🔍 Download Missing Tracks</button>`;
                } else if (isTidal) {
                    syncCompleteButtons += `<button class="modal-btn modal-btn-primary" onclick="startTidalDownloadMissing('${urlHash}')">🔍 Download Missing Tracks</button>`;
                } else if (isSpotifyPublic) {
                    syncCompleteButtons += `<button class="modal-btn modal-btn-primary" onclick="startSpotifyPublicDownloadMissing('${urlHash}')">🔍 Download Missing Tracks</button>`;
                } else if (isBeatport) {
                    syncCompleteButtons += `<button class="modal-btn modal-btn-primary" onclick="startBeatportDownloadMissing('${urlHash}')">🔍 Download Missing Tracks</button>`;
                } else {
                    syncCompleteButtons += `<button class="modal-btn modal-btn-primary" onclick="startYouTubeDownloadMissing('${urlHash}')">🔍 Download Missing Tracks</button>`;
                }
            }

            // Rediscover button (only for sources with reset endpoints)
            if (isBeatport) {
                syncCompleteButtons += `<button class="modal-btn modal-btn-secondary" onclick="resetBeatportChart('${urlHash}')">🔄 Rediscover</button>`;
            } else if (!isListenBrainz && !isTidal && !isDeezer && !isSpotifyPublic) {
                syncCompleteButtons += `<button class="modal-btn modal-btn-secondary" onclick="resetYouTubePlaylist('${urlHash}')">🔄 Rediscover</button>`;
            }

            // Wing It button
            syncCompleteButtons += ` <span class="wing-it-wrap"><button class="modal-btn wing-it-btn" onclick="_toggleWingItDropdown(this, '${urlHash}')">⚡ Wing It</button></span>`;

            return syncCompleteButtons;

        case 'download_complete':
            // Same options as sync_complete — allow re-sync, download missing, and reset
            let dlCompleteButtons = '';

            if (hasSpotifyMatches) {
                if (isListenBrainz) {
                    dlCompleteButtons += `<button class="modal-btn modal-btn-primary" onclick="startListenBrainzPlaylistSync('${urlHash}')">🔄 Sync This Playlist</button>`;
                } else if (isTidal) {
                    dlCompleteButtons += `<button class="modal-btn modal-btn-primary" onclick="startTidalPlaylistSync('${urlHash}')">🔄 Sync This Playlist</button>`;
                } else if (isDeezer) {
                    dlCompleteButtons += `<button class="modal-btn modal-btn-primary" onclick="startDeezerPlaylistSync('${urlHash}')">🔄 Sync This Playlist</button>`;
                } else if (isSpotifyPublic) {
                    dlCompleteButtons += `<button class="modal-btn modal-btn-primary" onclick="startSpotifyPublicPlaylistSync('${urlHash}')">🔄 Sync This Playlist</button>`;
                } else if (isBeatport) {
                    dlCompleteButtons += `<button class="modal-btn modal-btn-primary" onclick="startBeatportPlaylistSync('${urlHash}')">🔄 Sync This Playlist</button>`;
                } else {
                    dlCompleteButtons += `<button class="modal-btn modal-btn-primary" onclick="startYouTubePlaylistSync('${urlHash}')">🔄 Sync This Playlist</button>`;
                }
            }

            if (hasSpotifyMatches || hasConvertedPlaylistId) {
                if (isListenBrainz) {
                    dlCompleteButtons += `<button class="modal-btn modal-btn-primary" onclick="startYouTubeDownloadMissing('${urlHash}')">🔍 Download Missing Tracks</button>`;
                } else if (isTidal) {
                    dlCompleteButtons += `<button class="modal-btn modal-btn-primary" onclick="startTidalDownloadMissing('${urlHash}')">🔍 Download Missing Tracks</button>`;
                } else if (isDeezer) {
                    dlCompleteButtons += `<button class="modal-btn modal-btn-primary" onclick="startDeezerDownloadMissing('${urlHash}')">🔍 Download Missing Tracks</button>`;
                } else if (isSpotifyPublic) {
                    dlCompleteButtons += `<button class="modal-btn modal-btn-primary" onclick="startSpotifyPublicDownloadMissing('${urlHash}')">🔍 Download Missing Tracks</button>`;
                } else if (isBeatport) {
                    dlCompleteButtons += `<button class="modal-btn modal-btn-primary" onclick="startBeatportDownloadMissing('${urlHash}')">🔍 Download Missing Tracks</button>`;
                } else {
                    dlCompleteButtons += `<button class="modal-btn modal-btn-primary" onclick="startYouTubeDownloadMissing('${urlHash}')">🔍 Download Missing Tracks</button>`;
                }
            }

            // Rediscover button (only for sources with reset endpoints)
            if (isBeatport) {
                dlCompleteButtons += `<button class="modal-btn modal-btn-secondary" onclick="resetBeatportChart('${urlHash}')">🔄 Rediscover</button>`;
            } else if (!isListenBrainz && !isTidal && !isDeezer && !isSpotifyPublic) {
                dlCompleteButtons += `<button class="modal-btn modal-btn-secondary" onclick="resetYouTubePlaylist('${urlHash}')">🔄 Rediscover</button>`;
            }

            return dlCompleteButtons;

        default:
            return '';
    }
}

function getModalDescription(phase, isTidal = false, isBeatport = false, isListenBrainz = false, isMirrored = false, isDeezer = false, isSpotifyPublic = false, isLastfmRadio = false) {
    const source = isMirrored ? 'mirrored' : (isSpotifyPublic ? 'Spotify' : (isDeezer ? 'Deezer' : (isLastfmRadio ? 'Last.fm Radio' : (isListenBrainz ? 'ListenBrainz' : (isBeatport ? 'Beatport' : (isTidal ? 'Tidal' : 'YouTube'))))));
    switch (phase) {
        case 'fresh':
            return `Ready to discover clean ${currentMusicSourceName} metadata for ${source} tracks...`;
        case 'discovering':
            return `Discovering clean ${currentMusicSourceName} metadata for ${source} tracks...`;
        case 'discovered':
        case 'downloading':
        case 'download_complete':
            return 'Discovery complete! View the results below.';
        default:
            return `Discovering clean ${currentMusicSourceName} metadata for ${source} tracks...`;
    }
}

function getInitialProgressText(phase, isTidal = false, isBeatport = false, isListenBrainz = false) {
    switch (phase) {
        case 'fresh':
            return 'Click Start Discovery to begin...';
        case 'discovering':
            return 'Starting discovery...';
        case 'discovered':
        case 'downloading':
        case 'download_complete':
            return 'Discovery completed!';
        default:
            return 'Starting discovery...';
    }
}

function generateTableRowsFromState(state, urlHash) {
    const isTidal = state.is_tidal_playlist;
    const isDeezer = state.is_deezer_playlist;
    const isSpotifyPublic = state.is_spotify_public_playlist;
    const isBeatport = state.is_beatport_playlist;
    const isListenBrainz = state.is_listenbrainz_playlist;
    const isMirrored = state.is_mirrored_playlist;
    const platform = isMirrored ? 'mirrored' : (isSpotifyPublic ? 'spotify_public' : (isDeezer ? 'deezer' : (isListenBrainz ? 'listenbrainz' : (isTidal ? 'tidal' : (isBeatport ? 'beatport' : 'youtube')))));

    // Support both camelCase and snake_case
    const discoveryResults = state.discoveryResults || state.discovery_results;

    if (discoveryResults && discoveryResults.length > 0) {
        // Generate rows from existing discovery results
        return discoveryResults.map((result, index) => {
            // Handle different field names based on platform
            const trackName = result.lb_track || result.yt_track || result.track_name || '-';
            const artistName = result.lb_artist || result.yt_artist || result.artist_name || '-';

            return `
            <tr id="discovery-row-${urlHash}-${result.index}">
                <td class="yt-track">${trackName}</td>
                <td class="yt-artist">${artistName}</td>
                <td class="discovery-status ${result.status_class}">${result.status}</td>
                <td class="spotify-track">${result.spotify_track || '-'}</td>
                <td class="spotify-artist">${result.spotify_artist || '-'}</td>
                <td class="spotify-album">${result.spotify_album || '-'}</td>
                <td class="discovery-actions">${generateDiscoveryActionButton(result, urlHash, platform)}</td>
            </tr>
        `;
        }).join('');
    } else {
        // Generate initial rows from playlist tracks
        return generateInitialTableRows(state.playlist.tracks, isTidal, urlHash, isBeatport, isListenBrainz);
    }
}

function generateInitialTableRows(tracks, isTidal = false, urlHash = '', isBeatport = false, isListenBrainz = false) {
    return tracks.map((track, index) => {
        // Handle different track formats based on platform
        let trackName, artistName;

        if (isListenBrainz) {
            // ListenBrainz tracks have track_name and artist_name
            trackName = track.track_name || 'Unknown Track';
            artistName = track.artist_name || 'Unknown Artist';
        } else {
            // YouTube/Tidal/Beatport tracks have name and artists
            trackName = track.name || 'Unknown Track';
            artistName = track.artists ? (Array.isArray(track.artists) ? track.artists.join(', ') : track.artists) : 'Unknown Artist';
        }

        return `
        <tr id="discovery-row-${urlHash}-${index}">
            <td class="yt-track">${trackName}</td>
            <td class="yt-artist">${artistName}</td>
            <td class="discovery-status">🔍 Pending...</td>
            <td class="spotify-track">-</td>
            <td class="spotify-artist">-</td>
            <td class="spotify-album">-</td>
            <td class="discovery-actions">-</td>
        </tr>
    `;
    }).join('');
}

function formatDuration(durationMs) {
    if (!durationMs) return '0:00';
    const minutes = Math.floor(durationMs / 60000);
    const seconds = Math.floor((durationMs % 60000) / 1000);
    return `${minutes}:${seconds.toString().padStart(2, '0')}`;
}

/**
 * Generate action button for discovery table row
 */
function generateDiscoveryActionButton(result, identifier, platform) {
    // Show fix button for not_found, error, or any non-found status
    const isNotFound = result.status === 'not_found' ||
        result.status_class === 'not-found' ||
        result.status === '❌ Not Found' ||
        result.status === 'Not Found';

    const isError = result.status === 'error' ||
        result.status_class === 'error' ||
        result.status === '❌ Error';

    const isWingIt = result.wing_it_fallback ||
        result.status_class === 'wing-it';

    const isFound = result.status === 'found' ||
        result.status_class === 'found' ||
        result.status === '✅ Found';

    if (isNotFound || isError) {
        return `<button class="fix-match-btn"
                        onclick="openDiscoveryFixModal('${platform}', '${identifier}', ${result.index})"
                        title="Manually search for this track">
                    🔧 Fix
                </button>`;
    }

    // For wing-it fallbacks, show fix button so user can find a real match
    if (isWingIt) {
        return `<button class="fix-match-btn"
                        onclick="openDiscoveryFixModal('${platform}', '${identifier}', ${result.index})"
                        title="Search for a proper metadata match">
                    🔧 Fix
                </button>`;
    }

    // For found matches, show re-match and unmatch buttons
    if (isFound) {
        return `<button class="rematch-btn"
                        onclick="openDiscoveryFixModal('${platform}', '${identifier}', ${result.index})"
                        title="Change this match">
                    ↻
                </button><button class="rematch-btn" style="margin-left:4px;color:#ff6b6b"
                        onclick="unmatchDiscoveryTrack('${platform}', '${identifier}', ${result.index})"
                        title="Remove this match">
                    ✕
                </button>`;
    }

    return '-';
}

function updateYouTubeDiscoveryModal(urlHash, status) {
    const progressBar = document.getElementById(`youtube-discovery-progress-${urlHash}`);
    const progressText = document.getElementById(`youtube-discovery-progress-text-${urlHash}`);
    const tableBody = document.getElementById(`youtube-discovery-table-${urlHash}`);

    if (!progressBar || !progressText || !tableBody) {
        console.warn(`⚠️ Missing modal elements for ${urlHash}:`, {
            progressBar: !!progressBar,
            progressText: !!progressText,
            tableBody: !!tableBody
        });
        return;
    }

    // Update progress bar
    progressBar.style.width = `${status.progress}%`;
    progressText.textContent = `${status.spotify_matches} / ${status.spotify_total} tracks matched (${status.progress}%)`;


    // Update table rows
    status.results.forEach(result => {
        const row = document.getElementById(`discovery-row-${urlHash}-${result.index}`);
        if (!row) return;

        const statusCell = row.querySelector('.discovery-status');
        const spotifyTrackCell = row.querySelector('.spotify-track');
        const spotifyArtistCell = row.querySelector('.spotify-artist');
        const spotifyAlbumCell = row.querySelector('.spotify-album');
        const actionsCell = row.querySelector('.discovery-actions');

        statusCell.textContent = result.status;
        statusCell.className = `discovery-status ${result.status_class}`;

        spotifyTrackCell.textContent = result.spotify_track || '-';
        spotifyArtistCell.textContent = result.spotify_artist || '-';
        spotifyAlbumCell.textContent = result.spotify_album || '-';

        // Update actions cell with appropriate button
        if (actionsCell) {
            const state = listenbrainzPlaylistStates[urlHash] || youtubePlaylistStates[urlHash];
            const platform = state?.is_mirrored_playlist ? 'mirrored' :
                (state?.is_spotify_public_playlist ? 'spotify_public' :
                    (state?.is_deezer_playlist ? 'deezer' :
                        (state?.is_listenbrainz_playlist ? 'listenbrainz' :
                            (state?.is_tidal_playlist ? 'tidal' :
                                (state?.is_beatport_playlist ? 'beatport' : 'youtube')))));
            actionsCell.innerHTML = generateDiscoveryActionButton(result, urlHash, platform);
        }
    });

    // Update action buttons and description when discovery is complete.
    // status.complete is explicitly set by LB/WS polling callers; only act when transitioning
    // from 'discovering' to avoid interfering with download/sync phases of other playlist types.
    if (status.complete) {
        const state = listenbrainzPlaylistStates[urlHash] || youtubePlaylistStates[urlHash];
        if (state && state.phase === 'discovering') {
            state.phase = 'discovered';
            const actionButtonsContainer = document.querySelector(`#youtube-discovery-modal-${urlHash} .modal-footer-left`);
            if (actionButtonsContainer) {
                actionButtonsContainer.innerHTML = getModalActionButtons(urlHash, 'discovered', state);
                console.log(`✨ Updated action buttons for completed discovery: ${urlHash}`);
            }
            const descEl = document.querySelector(`#youtube-discovery-modal-${urlHash} .modal-description`);
            if (descEl) descEl.textContent = 'Discovery complete! View the results below.';
        } else if (state && state.phase === 'discovered') {
            // Already discovered — ensure buttons are correct (e.g. after rehydration)
            const actionButtonsContainer = document.querySelector(`#youtube-discovery-modal-${urlHash} .modal-footer-left`);
            if (actionButtonsContainer && actionButtonsContainer.querySelector('.modal-info')) {
                actionButtonsContainer.innerHTML = getModalActionButtons(urlHash, 'discovered', state);
            }
        }
    }
}

function refreshYouTubeDiscoveryModalTable(urlHash) {
    const state = youtubePlaylistStates[urlHash];
    if (!state || !state.modalElement) {
        console.warn(`⚠️ Cannot refresh modal table: no state or modal for ${urlHash}`);
        return;
    }

    console.log(`🔄 Refreshing modal table with ${state.discoveryResults?.length || 0} discovery results`);

    // Update the table body with new discovery results
    const tableBody = state.modalElement.querySelector(`#youtube-discovery-table-${urlHash}`);
    if (tableBody) {
        tableBody.innerHTML = generateTableRowsFromState(state, urlHash);
        console.log(`✅ Modal table refreshed with discovery data`);
    } else {
        console.warn(`⚠️ Could not find table body for modal ${urlHash}`);
    }

    // Update the progress bar and footer buttons too
    if (state.discoveryResults && state.discoveryResults.length > 0) {
        const progressData = {
            progress: state.discoveryProgress || 100,
            spotify_matches: state.spotifyMatches || 0,
            spotify_total: state.playlist.tracks.length,
            results: state.discoveryResults
        };
        updateYouTubeDiscoveryModal(urlHash, progressData);
    }
}

function closeYouTubeDiscoveryModal(urlHash) {
    const modal = document.getElementById(`youtube-discovery-modal-${urlHash}`);
    if (modal) {
        // Hide modal instead of removing it to preserve state
        modal.classList.add('hidden');
        console.log('🚪 Hidden YouTube discovery modal (preserving state):', urlHash);
    }

    // Handle phase reset for completed discovery (Tidal/Beatport pattern)
    const state = youtubePlaylistStates[urlHash];
    if (state) {
        const isTidal = state.is_tidal_playlist;
        const isDeezer = state.is_deezer_playlist;
        const isSpotifyPublic = state.is_spotify_public_playlist;
        const isBeatport = state.is_beatport_playlist;

        // Reset to 'discovered' phase if modal is closed after completion (like Tidal does)
        if (state.phase === 'sync_complete' || state.phase === 'download_complete') {
            console.log(`🧹 [Modal Close] Resetting ${isSpotifyPublic ? 'Spotify Public' : (isDeezer ? 'Deezer' : (isBeatport ? 'Beatport' : (isTidal ? 'Tidal' : 'YouTube')))} state after completion`);

            if (isSpotifyPublic) {
                // Spotify Public: Extract url_hash and reset state
                const spUrlHash = state.spotify_public_playlist_id || null;
                if (spUrlHash && spotifyPublicPlaylistStates[spUrlHash]) {
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

                    updateSpotifyPublicCardPhase(spUrlHash, 'discovered');

                    try {
                        fetch(`/api/spotify-public/update_phase/${spUrlHash}`, {
                            method: 'POST',
                            headers: { 'Content-Type': 'application/json' },
                            body: JSON.stringify({ phase: 'discovered' })
                        });
                    } catch (error) {
                        console.warn('Error updating backend Spotify Public phase:', error);
                    }
                }
            } else if (isDeezer) {
                // Deezer: Extract playlist ID and reset Deezer state
                const deezerPlaylistId = state.deezer_playlist_id || null;
                if (deezerPlaylistId && deezerPlaylistStates[deezerPlaylistId]) {
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

                    updateDeezerCardPhase(deezerPlaylistId, 'discovered');

                    try {
                        fetch(`/api/deezer/update_phase/${deezerPlaylistId}`, {
                            method: 'POST',
                            headers: { 'Content-Type': 'application/json' },
                            body: JSON.stringify({ phase: 'discovered' })
                        });
                    } catch (error) {
                        console.warn('Error updating backend Deezer phase:', error);
                    }
                }
            } else if (isTidal) {
                // Tidal: Extract playlist ID and reset Tidal state
                const tidalPlaylistId = state.tidal_playlist_id || null;
                if (tidalPlaylistId && tidalPlaylistStates[tidalPlaylistId]) {
                    // Preserve discovery data but reset phase
                    const preservedData = {
                        playlist: tidalPlaylistStates[tidalPlaylistId].playlist,
                        discovery_results: tidalPlaylistStates[tidalPlaylistId].discovery_results,
                        spotify_matches: tidalPlaylistStates[tidalPlaylistId].spotify_matches,
                        discovery_progress: tidalPlaylistStates[tidalPlaylistId].discovery_progress,
                        convertedSpotifyPlaylistId: tidalPlaylistStates[tidalPlaylistId].convertedSpotifyPlaylistId
                    };

                    // Clear download state
                    delete tidalPlaylistStates[tidalPlaylistId].download_process_id;
                    delete tidalPlaylistStates[tidalPlaylistId].phase;

                    // Restore preserved data and set to discovered phase
                    Object.assign(tidalPlaylistStates[tidalPlaylistId], preservedData);
                    tidalPlaylistStates[tidalPlaylistId].phase = 'discovered';

                    updateTidalCardPhase(tidalPlaylistId, 'discovered');

                    // Update backend state
                    try {
                        fetch(`/api/tidal/update_phase/${tidalPlaylistId}`, {
                            method: 'POST',
                            headers: { 'Content-Type': 'application/json' },
                            body: JSON.stringify({ phase: 'discovered' })
                        });
                    } catch (error) {
                        console.warn('⚠️ Error updating backend Tidal phase:', error);
                    }
                }
            } else if (isBeatport) {
                // Beatport: Reset chart state
                const chartHash = state.beatport_chart_hash || urlHash;
                if (beatportChartStates[chartHash]) {
                    beatportChartStates[chartHash].phase = 'discovered';
                    updateBeatportCardPhase(chartHash, 'discovered');

                    // Update backend state
                    try {
                        fetch(`/api/beatport/charts/update-phase/${chartHash}`, {
                            method: 'POST',
                            headers: { 'Content-Type': 'application/json' },
                            body: JSON.stringify({ phase: 'discovered' })
                        });
                    } catch (error) {
                        console.warn('⚠️ Error updating backend Beatport phase:', error);
                    }
                }
            } else {
                // YouTube: Reset to discovered phase
                updateYouTubeCardPhase(urlHash, 'discovered');

                // Update backend state
                try {
                    fetch(`/api/youtube/update_phase/${urlHash}`, {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ phase: 'discovered' })
                    });
                } catch (error) {
                    console.warn('⚠️ Error updating backend YouTube phase:', error);
                }
            }

            // Reset frontend state to discovered
            state.phase = 'discovered';
            console.log(`✅ [Modal Close] Reset to discovered phase: ${urlHash}`);
        }
    }

    // Keep modal reference and all state intact
    // Discovery polling continues in background if active
}

// ===============================
// YOUTUBE SYNC FUNCTIONALITY
// ===============================

async function startYouTubePlaylistSync(urlHash) {
    try {
        console.log('🔄 Starting YouTube playlist sync:', urlHash);

        const response = await fetch(`/api/youtube/sync/start/${urlHash}`, {
            method: 'POST'
        });

        const result = await response.json();

        if (result.error) {
            showToast(`Error starting sync: ${result.error}`, 'error');
            return;
        }

        // Capture sync_playlist_id for WebSocket subscription
        const syncPlaylistId = result.sync_playlist_id;
        const ytState = youtubePlaylistStates[urlHash];
        if (ytState) ytState.syncPlaylistId = syncPlaylistId;

        // Update card and modal to syncing phase
        updateYouTubeCardPhase(urlHash, 'syncing');

        // Update modal buttons if modal is open
        updateYouTubeModalButtons(urlHash, 'syncing');

        // Start sync polling
        startYouTubeSyncPolling(urlHash, syncPlaylistId);

        showToast('YouTube playlist sync started!', 'success');

    } catch (error) {
        console.error('❌ Error starting YouTube sync:', error);
        showToast(`Error starting sync: ${error.message}`, 'error');
    }
}

function startYouTubeSyncPolling(urlHash, syncPlaylistId) {
    // Stop any existing polling
    if (activeYouTubePollers[urlHash]) {
        clearInterval(activeYouTubePollers[urlHash]);
    }

    // Resolve syncPlaylistId from argument or stored state
    const ytState = youtubePlaylistStates[urlHash];
    syncPlaylistId = syncPlaylistId || (ytState && ytState.syncPlaylistId);

    // Phase 6: Subscribe via WebSocket
    if (socketConnected && syncPlaylistId) {
        socket.emit('sync:subscribe', { playlist_ids: [syncPlaylistId] });
        _syncProgressCallbacks[syncPlaylistId] = (data) => {
            const progress = data.progress || {};
            updateYouTubeCardSyncProgress(urlHash, progress);
            updateYouTubeModalSyncProgress(urlHash, progress);

            if (data.status === 'finished') {
                if (activeYouTubePollers[urlHash]) { clearInterval(activeYouTubePollers[urlHash]); delete activeYouTubePollers[urlHash]; }
                socket.emit('sync:unsubscribe', { playlist_ids: [syncPlaylistId] });
                delete _syncProgressCallbacks[syncPlaylistId];
                updateYouTubeCardPhase(urlHash, 'sync_complete');
                updateYouTubeModalButtons(urlHash, 'sync_complete');
                showToast('YouTube playlist sync complete!', 'success');
            } else if (data.status === 'error' || data.status === 'cancelled') {
                if (activeYouTubePollers[urlHash]) { clearInterval(activeYouTubePollers[urlHash]); delete activeYouTubePollers[urlHash]; }
                socket.emit('sync:unsubscribe', { playlist_ids: [syncPlaylistId] });
                delete _syncProgressCallbacks[syncPlaylistId];
                updateYouTubeCardPhase(urlHash, 'discovered');
                updateYouTubeModalButtons(urlHash, 'discovered');
                showToast(`Sync failed: ${data.error || 'Unknown error'}`, 'error');
            }
        };
    }

    // Define the polling function (HTTP fallback)
    const pollFunction = async () => {
        if (socketConnected) return; // Phase 6: WS handles updates
        try {
            const response = await fetch(`/api/youtube/sync/status/${urlHash}`);
            const status = await response.json();

            if (status.error) {
                console.error('❌ Error polling YouTube sync status:', status.error);
                clearInterval(pollInterval);
                delete activeYouTubePollers[urlHash];
                return;
            }

            updateYouTubeCardSyncProgress(urlHash, status.progress);
            updateYouTubeModalSyncProgress(urlHash, status.progress);

            if (status.complete) {
                clearInterval(pollInterval);
                delete activeYouTubePollers[urlHash];
                updateYouTubeCardPhase(urlHash, 'sync_complete');
                updateYouTubeModalButtons(urlHash, 'sync_complete');
                showToast('YouTube playlist sync complete!', 'success');
            } else if (status.sync_status === 'error') {
                clearInterval(pollInterval);
                delete activeYouTubePollers[urlHash];
                updateYouTubeCardPhase(urlHash, 'discovered');
                updateYouTubeModalButtons(urlHash, 'discovered');
                showToast(`Sync failed: ${status.error || 'Unknown error'}`, 'error');
            }
        } catch (error) {
            console.error('❌ Error polling YouTube sync:', error);
            if (activeYouTubePollers[urlHash]) {
                clearInterval(activeYouTubePollers[urlHash]);
                delete activeYouTubePollers[urlHash];
            }
        }
    };

    // Run immediately to get current status (skip if WS active)
    if (!socketConnected) pollFunction();

    // Then continue polling at regular intervals
    const pollInterval = setInterval(pollFunction, 1000);
    activeYouTubePollers[urlHash] = pollInterval;
}

async function cancelYouTubeSync(urlHash) {
    try {
        console.log('❌ Cancelling YouTube sync:', urlHash);

        const response = await fetch(`/api/youtube/sync/cancel/${urlHash}`, {
            method: 'POST'
        });

        const result = await response.json();

        if (result.error) {
            showToast(`Error cancelling sync: ${result.error}`, 'error');
            return;
        }

        // Stop polling
        if (activeYouTubePollers[urlHash]) {
            clearInterval(activeYouTubePollers[urlHash]);
            delete activeYouTubePollers[urlHash];
        }

        // Phase 6: Clean up WS subscription
        const ytCancelState = youtubePlaylistStates[urlHash];
        const ytSyncId = ytCancelState && ytCancelState.syncPlaylistId;
        if (ytSyncId && _syncProgressCallbacks[ytSyncId]) {
            if (socketConnected) socket.emit('sync:unsubscribe', { playlist_ids: [ytSyncId] });
            delete _syncProgressCallbacks[ytSyncId];
        }

        // Revert to discovered phase
        updateYouTubeCardPhase(urlHash, 'discovered');
        updateYouTubeModalButtons(urlHash, 'discovered');

        showToast('YouTube sync cancelled', 'info');

    } catch (error) {
        console.error('❌ Error cancelling YouTube sync:', error);
        showToast(`Error cancelling sync: ${error.message}`, 'error');
    }
}

function updateYouTubeCardSyncProgress(urlHash, progress) {
    const state = youtubePlaylistStates[urlHash];
    if (!state || !state.cardElement || !progress) return;

    const card = state.cardElement;
    const progressElement = card.querySelector('.playlist-card-progress');

    // Build clean status counter HTML exactly like Spotify cards
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

    // Only update if we have valid sync progress, otherwise preserve existing discovery results
    if (statusCounterHTML) {
        progressElement.innerHTML = statusCounterHTML;
    }

    console.log(`🔄 Updated YouTube sync progress: ♪ ${progress?.total_tracks || 0} / ✓ ${progress?.matched_tracks || 0} / ✗ ${progress?.failed_tracks || 0}`);
}

function updateYouTubeModalSyncProgress(urlHash, progress) {
    // Try all source-specific element ID prefixes
    const prefixes = ['youtube', 'listenbrainz', 'tidal', 'deezer', 'spotify-public', 'beatport'];
    let statusDisplay = null;
    let prefix = 'youtube';
    for (const p of prefixes) {
        statusDisplay = document.getElementById(`${p}-sync-status-${urlHash}`);
        if (statusDisplay) { prefix = p; break; }
    }
    if (!statusDisplay || !progress) return;

    const totalEl = document.getElementById(`${prefix}-total-${urlHash}`);
    const matchedEl = document.getElementById(`${prefix}-matched-${urlHash}`);
    const failedEl = document.getElementById(`${prefix}-failed-${urlHash}`);
    const percentageEl = document.getElementById(`${prefix}-percentage-${urlHash}`);

    const total = progress.total_tracks || 0;
    const matched = progress.matched_tracks || 0;
    const failed = progress.failed_tracks || 0;

    if (totalEl) totalEl.textContent = total;
    if (matchedEl) matchedEl.textContent = matched;
    if (failedEl) failedEl.textContent = failed;

    // Calculate percentage like Spotify sync
    if (total > 0) {
        const processed = matched + failed;
        const percentage = Math.round((processed / total) * 100);
        if (percentageEl) percentageEl.textContent = percentage;
    }

    console.log(`📊 YouTube modal updated: ♪ ${total} / ✓ ${matched} / ✗ ${failed} (${Math.round((matched + failed) / total * 100)}%)`);
}

function updateYouTubeModalButtons(urlHash, phase) {
    const modal = document.getElementById(`youtube-discovery-modal-${urlHash}`);
    if (!modal) return;

    const footerLeft = modal.querySelector('.modal-footer-left');
    if (footerLeft) {
        footerLeft.innerHTML = getModalActionButtons(urlHash, phase);
    }
}

// ===============================
// YOUTUBE DOWNLOAD MISSING TRACKS
// ===============================

async function startYouTubeDownloadMissing(urlHash) {
    try {
        console.log('🔍 Starting download missing tracks:', urlHash);

        // Check both YouTube and ListenBrainz states (like Beatport does)
        const state = youtubePlaylistStates[urlHash] || listenbrainzPlaylistStates[urlHash];
        // Support both camelCase and snake_case
        const discoveryResults = state?.discoveryResults || state?.discovery_results;

        if (!state || !discoveryResults) {
            showToast('No discovery results available for download', 'error');
            return;
        }

        // Determine source type (prefix removed - no longer needed)
        const isListenBrainz = state.is_listenbrainz_playlist;
        const isBeatport = state.is_beatport_playlist;
        const isTidal = state.is_tidal_playlist;
        const isDeezer = state.is_deezer_playlist;

        // Convert discovery results to a format compatible with the download modal
        const spotifyTracks = discoveryResults
            .filter(result => result.spotify_data || (result.spotify_track && result.status_class === 'found'))
            .map(result => {
                if (result.spotify_data) {
                    return result.spotify_data;
                } else {
                    // Build from individual fields (automatic discovery format)
                    // Convert album to proper object format for wishlist compatibility
                    const albumData = result.spotify_album || 'Unknown Album';
                    const albumObject = typeof albumData === 'object' && albumData !== null
                        ? albumData
                        : {
                            name: typeof albumData === 'string' ? albumData : 'Unknown Album',
                            album_type: 'album',
                            images: []
                        };

                    return {
                        id: result.spotify_id || 'unknown',
                        name: result.spotify_track || 'Unknown Track',
                        artists: result.spotify_artist ? [result.spotify_artist] : ['Unknown Artist'],
                        album: albumObject,
                        duration_ms: 0
                    };
                }
            });

        if (spotifyTracks.length === 0) {
            showToast('No Spotify matches found for download', 'error');
            return;
        }

        // Create a virtual playlist for the download system
        const virtualPlaylistId = isListenBrainz ? `listenbrainz_${urlHash}` : (isDeezer ? `deezer_${urlHash}` : (isBeatport ? `beatport_${urlHash}` : (isTidal ? `tidal_${urlHash}` : `youtube_${urlHash}`)));
        const playlistName = state.playlist.name;

        // Store reference for card navigation
        state.convertedSpotifyPlaylistId = virtualPlaylistId;

        // Close the discovery modal if it's open
        const discoveryModal = document.getElementById(`youtube-discovery-modal-${urlHash}`);
        if (discoveryModal) {
            discoveryModal.classList.add('hidden');
            console.log('🔄 Closed YouTube discovery modal to show download modal');
        }

        // Open download missing tracks modal for YouTube playlist
        await openDownloadMissingModalForYouTube(virtualPlaylistId, playlistName, spotifyTracks);

        // Phase will change to 'downloading' when user clicks "Begin Analysis" button

    } catch (error) {
        console.error('❌ Error starting download missing tracks:', error);
        showToast(`Error starting downloads: ${error.message}`, 'error');
    }
}

async function resetYouTubePlaylist(urlHash) {
    const state = youtubePlaylistStates[urlHash];
    if (!state) return;

    try {
        console.log(`🔄 Resetting YouTube playlist to fresh state: ${state.playlist.name}`);

        // Call backend reset endpoint
        const response = await fetch(`/api/youtube/reset/${urlHash}`, {
            method: 'POST'
        });

        if (!response.ok) {
            const error = await response.json();
            throw new Error(error.error || 'Failed to reset playlist');
        }

        // Stop any active polling
        if (activeYouTubePollers[urlHash]) {
            clearInterval(activeYouTubePollers[urlHash]);
            delete activeYouTubePollers[urlHash];
        }

        // Update client state to match backend reset
        state.phase = 'fresh';
        state.discoveryResults = [];
        state.discoveryProgress = 0;
        state.spotifyMatches = 0;
        state.syncPlaylistId = null;
        state.syncProgress = {};
        state.convertedSpotifyPlaylistId = null;

        // Update card to reflect fresh state
        updateYouTubeCardPhase(urlHash, 'fresh');
        updateYouTubeCardProgress(urlHash, {
            discovery_progress: 0,
            spotify_matches: 0,
            spotify_total: state.playlist.tracks.length
        });

        // Close modal
        closeYouTubeDiscoveryModal(urlHash);

        showToast(`Reset "${state.playlist.name}" to fresh state`, 'success');
        console.log(`✅ Successfully reset YouTube playlist: ${state.playlist.name}`);

    } catch (error) {
        console.error(`❌ Error resetting YouTube playlist:`, error);
        showToast(`Error resetting playlist: ${error.message}`, 'error');
    }
}

async function resetBeatportChart(urlHash) {
    const state = youtubePlaylistStates[urlHash];
    const chartState = beatportChartStates[urlHash];

    if (!state || !state.is_beatport_playlist || !chartState) {
        console.error('❌ Invalid Beatport chart state for reset');
        return;
    }

    try {
        console.log(`🔄 Resetting Beatport chart to fresh state: ${state.playlist.name}`);

        // Call backend reset endpoint for Beatport
        const chartHash = state.beatport_chart_hash || urlHash;
        const response = await fetch(`/api/beatport/charts/update-phase/${chartHash}`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                phase: 'fresh',
                reset: true
            })
        });

        if (!response.ok) {
            const error = await response.json();
            throw new Error(error.error || 'Failed to reset Beatport chart');
        }

        // Stop any active polling
        if (activeYouTubePollers[urlHash]) {
            clearInterval(activeYouTubePollers[urlHash]);
            delete activeYouTubePollers[urlHash];
        }

        // Update client state to match backend reset
        state.phase = 'fresh';
        state.discoveryResults = [];
        state.discoveryProgress = 0;
        state.spotifyMatches = 0;
        state.discovery_results = [];
        state.discovery_progress = 0;
        state.spotify_matches = 0;
        state.syncPlaylistId = null;
        state.syncProgress = {};
        state.convertedSpotifyPlaylistId = null;

        // Update Beatport chart state
        chartState.phase = 'fresh';

        // Update card to reflect fresh state
        updateBeatportCardPhase(chartHash, 'fresh');
        updateBeatportCardProgress(chartHash, {
            spotify_total: state.playlist.tracks.length,
            spotify_matches: 0,
            failed: 0
        });

        // Close modal
        closeYouTubeDiscoveryModal(urlHash);

        showToast(`Reset "${state.playlist.name}" to fresh state`, 'success');
        console.log(`✅ Successfully reset Beatport chart: ${state.playlist.name}`);

    } catch (error) {
        console.error(`❌ Error resetting Beatport chart:`, error);
        showToast(`Error resetting chart: ${error.message}`, 'error');
    }
}

// ============================================================================
// LISTENBRAINZ PLAYLIST DISCOVERY & SYNC
// ============================================================================

function startListenBrainzDiscoveryPolling(playlistMbid) {
    console.log(`🔄 Starting ListenBrainz discovery polling for: ${playlistMbid}`);

    // Stop any existing polling (reuse YouTube polling infrastructure)
    if (activeYouTubePollers[playlistMbid]) {
        clearInterval(activeYouTubePollers[playlistMbid]);
    }

    // Phase 5: Subscribe via WebSocket
    if (socketConnected) {
        socket.emit('discovery:subscribe', { ids: [playlistMbid] });
        _discoveryProgressCallbacks[playlistMbid] = (data) => {
            if (data.error) {
                if (activeYouTubePollers[playlistMbid]) { clearInterval(activeYouTubePollers[playlistMbid]); delete activeYouTubePollers[playlistMbid]; }
                socket.emit('discovery:unsubscribe', { ids: [playlistMbid] }); delete _discoveryProgressCallbacks[playlistMbid];
                return;
            }
            if (listenbrainzPlaylistStates[playlistMbid]) {
                const transformed = {
                    progress: data.progress || 0, spotify_matches: data.spotify_matches || 0, spotify_total: data.spotify_total || 0,
                    results: (data.results || []).map((r, i) => ({
                        index: r.index !== undefined ? r.index : i,
                        yt_track: r.lb_track || r.track_name || 'Unknown',
                        yt_artist: r.lb_artist || r.artist_name || 'Unknown',
                        status: (r.status === 'found' || r.status === '✅ Found' || r.status_class === 'found') ? '✅ Found' : (r.status === 'error' ? '❌ Error' : '❌ Not Found'),
                        status_class: r.status_class || ((r.status === 'found' || r.status === '✅ Found') ? 'found' : (r.status === 'error' ? 'error' : 'not-found')),
                        spotify_track: r.spotify_data ? r.spotify_data.name : (r.spotify_track || '-'),
                        spotify_artist: r.spotify_data ? (r.spotify_data.artists && r.spotify_data.artists[0] ? (typeof r.spotify_data.artists[0] === 'object' ? r.spotify_data.artists[0].name : r.spotify_data.artists[0]) : '-') : (r.spotify_artist || '-'),
                        spotify_album: r.spotify_data ? (typeof r.spotify_data.album === 'object' ? r.spotify_data.album.name : r.spotify_data.album) || '-' : (r.spotify_album || '-'),
                        spotify_data: r.spotify_data, duration: r.duration || '0:00'
                    })),
                    complete: data.complete || data.phase === 'discovered'
                };
                const st = listenbrainzPlaylistStates[playlistMbid];
                st.discovery_results = data.results || []; st.discoveryResults = transformed.results;
                st.discovery_progress = data.progress || 0; st.discoveryProgress = data.progress || 0;
                st.spotify_matches = data.spotify_matches || 0; st.spotifyMatches = data.spotify_matches || 0;
                st.spotify_total = data.spotify_total || 0; st.spotifyTotal = data.spotify_total || 0;
                updateYouTubeDiscoveryModal(playlistMbid, transformed);
            }
            if (data.complete || data.phase === 'discovered') {
                if (activeYouTubePollers[playlistMbid]) { clearInterval(activeYouTubePollers[playlistMbid]); delete activeYouTubePollers[playlistMbid]; }
                socket.emit('discovery:unsubscribe', { ids: [playlistMbid] }); delete _discoveryProgressCallbacks[playlistMbid];
                if (listenbrainzPlaylistStates[playlistMbid]) listenbrainzPlaylistStates[playlistMbid].phase = 'discovered';
                updateYouTubeModalButtons(playlistMbid, 'discovered');
                const _descElWs = document.querySelector(`#youtube-discovery-modal-${playlistMbid} .modal-description`);
                if (_descElWs) _descElWs.textContent = 'Discovery complete! View the results below.';
                const playlistIdEl = `discover-lb-playlist-${playlistMbid}`;
                const syncBtn = document.getElementById(`${playlistIdEl}-sync-btn`);
                if (syncBtn) syncBtn.style.display = 'inline-block';
                showToast('ListenBrainz discovery complete!', 'success');
            }
        };
    }

    const pollInterval = setInterval(async () => {
        // Always poll — no dedicated WebSocket events for discovery progress
        try {
            const response = await fetch(`/api/listenbrainz/discovery/status/${playlistMbid}`);
            const status = await response.json();

            if (status.error) {
                console.error('❌ Error polling ListenBrainz discovery status:', status.error);
                clearInterval(pollInterval);
                delete activeYouTubePollers[playlistMbid];
                return;
            }

            // Update state and modal (reuse YouTube infrastructure like Beatport/Tidal)
            if (listenbrainzPlaylistStates[playlistMbid]) {
                // Transform ListenBrainz results to YouTube modal format (like Beatport does)
                const transformedStatus = {
                    progress: status.progress || 0,
                    spotify_matches: status.spotify_matches || 0,
                    spotify_total: status.spotify_total || 0,
                    results: (status.results || []).map((result, index) => ({
                        index: result.index !== undefined ? result.index : index,
                        yt_track: result.lb_track || result.track_name || 'Unknown',
                        yt_artist: result.lb_artist || result.artist_name || 'Unknown',
                        status: result.status === 'found' || result.status === '✅ Found' || result.status_class === 'found' ? '✅ Found' : (result.status === 'error' ? '❌ Error' : '❌ Not Found'),
                        status_class: result.status_class || (result.status === 'found' || result.status === '✅ Found' ? 'found' : (result.status === 'error' ? 'error' : 'not-found')),
                        spotify_track: result.spotify_data ? result.spotify_data.name : (result.spotify_track || '-'),
                        spotify_artist: result.spotify_data ? (result.spotify_data.artists && result.spotify_data.artists[0] ? (typeof result.spotify_data.artists[0] === 'object' ? result.spotify_data.artists[0].name : result.spotify_data.artists[0]) : '-') : (result.spotify_artist || '-'),
                        spotify_album: result.spotify_data ? (typeof result.spotify_data.album === 'object' ? result.spotify_data.album.name : result.spotify_data.album) || '-' : (result.spotify_album || '-'),
                        spotify_data: result.spotify_data,
                        duration: result.duration || '0:00'
                    })),
                    complete: status.complete || status.phase === 'discovered'
                };

                // Store both raw and transformed results (support both naming conventions)
                listenbrainzPlaylistStates[playlistMbid].discovery_results = status.results || [];
                listenbrainzPlaylistStates[playlistMbid].discoveryResults = transformedStatus.results;
                listenbrainzPlaylistStates[playlistMbid].discovery_progress = status.progress || 0;
                listenbrainzPlaylistStates[playlistMbid].discoveryProgress = status.progress || 0;
                listenbrainzPlaylistStates[playlistMbid].spotify_matches = status.spotify_matches || 0;
                listenbrainzPlaylistStates[playlistMbid].spotifyMatches = status.spotify_matches || 0;  // camelCase for modal
                listenbrainzPlaylistStates[playlistMbid].spotify_total = status.spotify_total || 0;
                listenbrainzPlaylistStates[playlistMbid].spotifyTotal = status.spotify_total || 0;  // camelCase for modal

                // Update modal if open
                updateYouTubeDiscoveryModal(playlistMbid, transformedStatus);
            }

            // Check if complete
            if (status.complete || status.phase === 'discovered') {
                clearInterval(pollInterval);
                delete activeYouTubePollers[playlistMbid];

                // Update phase in backend for persistence (like Beatport does)
                try {
                    await fetch(`/api/listenbrainz/update-phase/${playlistMbid}`, {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ phase: 'discovered' })
                    });
                    console.log('✅ Updated ListenBrainz backend phase to discovered');
                } catch (error) {
                    console.warn('⚠️ Failed to update backend phase:', error);
                }

                // Update phase in frontend state
                if (listenbrainzPlaylistStates[playlistMbid]) {
                    listenbrainzPlaylistStates[playlistMbid].phase = 'discovered';
                }

                // Update modal buttons to show sync and download buttons
                updateYouTubeModalButtons(playlistMbid, 'discovered');

                // Update modal description to "Discovery complete!"
                const descEl = document.querySelector(`#youtube-discovery-modal-${playlistMbid} .modal-description`);
                if (descEl) descEl.textContent = 'Discovery complete! View the results below.';

                // Show sync button in playlist listing (hidden by default until discovered)
                const playlistId = `discover-lb-playlist-${playlistMbid}`;
                const syncBtn = document.getElementById(`${playlistId}-sync-btn`);
                if (syncBtn) {
                    syncBtn.style.display = 'inline-block';
                    console.log('✅ Showing sync button after discovery completion');
                }

                console.log('✅ ListenBrainz discovery complete:', playlistMbid);
                showToast('ListenBrainz discovery complete!', 'success');
            }

        } catch (error) {
            console.error('❌ Error polling ListenBrainz discovery:', error);
            clearInterval(pollInterval);
            delete activeYouTubePollers[playlistMbid];
        }
    }, 1000);

    activeYouTubePollers[playlistMbid] = pollInterval;
}

function startListenBrainzSyncPolling(playlistMbid, syncPlaylistId) {
    // Stop any existing polling
    if (activeYouTubePollers[playlistMbid]) {
        clearInterval(activeYouTubePollers[playlistMbid]);
    }

    // Resolve syncPlaylistId from argument or stored state
    const lbState = listenbrainzPlaylistStates[playlistMbid];
    syncPlaylistId = syncPlaylistId || (lbState && lbState.syncPlaylistId);

    // Phase 6: Subscribe via WebSocket
    if (socketConnected && syncPlaylistId) {
        socket.emit('sync:subscribe', { playlist_ids: [syncPlaylistId] });
        _syncProgressCallbacks[syncPlaylistId] = (data) => {
            const progress = data.progress || {};
            updateYouTubeModalSyncProgress(playlistMbid, progress);

            if (data.status === 'finished') {
                if (activeYouTubePollers[playlistMbid]) { clearInterval(activeYouTubePollers[playlistMbid]); delete activeYouTubePollers[playlistMbid]; }
                socket.emit('sync:unsubscribe', { playlist_ids: [syncPlaylistId] });
                delete _syncProgressCallbacks[syncPlaylistId];
                updateYouTubeModalButtons(playlistMbid, 'sync_complete');
                showToast('ListenBrainz playlist sync complete!', 'success');
            } else if (data.status === 'error' || data.status === 'cancelled') {
                if (activeYouTubePollers[playlistMbid]) { clearInterval(activeYouTubePollers[playlistMbid]); delete activeYouTubePollers[playlistMbid]; }
                socket.emit('sync:unsubscribe', { playlist_ids: [syncPlaylistId] });
                delete _syncProgressCallbacks[syncPlaylistId];
                updateYouTubeModalButtons(playlistMbid, 'discovered');
                showToast(`Sync failed: ${data.error || 'Unknown error'}`, 'error');
            }
        };
    }

    // Define the polling function (HTTP fallback)
    const pollFunction = async () => {
        if (socketConnected) return; // Phase 6: WS handles updates
        try {
            const response = await fetch(`/api/listenbrainz/sync/status/${playlistMbid}`);
            const status = await response.json();

            if (status.error) {
                console.error('❌ Error polling ListenBrainz sync status:', status.error);
                clearInterval(pollInterval);
                delete activeYouTubePollers[playlistMbid];
                return;
            }

            updateYouTubeModalSyncProgress(playlistMbid, status.progress);

            if (status.complete) {
                clearInterval(pollInterval);
                delete activeYouTubePollers[playlistMbid];
                updateYouTubeModalButtons(playlistMbid, 'sync_complete');
                showToast('ListenBrainz playlist sync complete!', 'success');
            } else if (status.sync_status === 'error') {
                clearInterval(pollInterval);
                delete activeYouTubePollers[playlistMbid];
                updateYouTubeModalButtons(playlistMbid, 'discovered');
                showToast(`Sync failed: ${status.error || 'Unknown error'}`, 'error');
            }
        } catch (error) {
            console.error('❌ Error polling ListenBrainz sync:', error);
            if (activeYouTubePollers[playlistMbid]) {
                clearInterval(activeYouTubePollers[playlistMbid]);
                delete activeYouTubePollers[playlistMbid];
            }
        }
    };

    // Run immediately to get current status (skip if WS active)
    if (!socketConnected) pollFunction();

    // Then continue polling at regular intervals
    const pollInterval = setInterval(pollFunction, 1000);
    activeYouTubePollers[playlistMbid] = pollInterval;
}

async function startListenBrainzDiscovery(playlistMbid) {
    const state = listenbrainzPlaylistStates[playlistMbid];
    if (!state) {
        console.error('❌ No ListenBrainz playlist state found');
        return;
    }

    try {
        console.log('🔍 Starting ListenBrainz discovery for:', state.playlist.name);

        // Update local phase to discovering
        state.phase = 'discovering';
        state.status = 'discovering';

        // Call backend to start discovery worker
        const response = await fetch(`/api/listenbrainz/discovery/start/${playlistMbid}`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                playlist: state.playlist
            })
        });

        if (!response.ok) {
            const error = await response.json();
            throw new Error(error.error || 'Failed to start discovery');
        }

        console.log('✅ ListenBrainz discovery started on backend');

        // Start polling for progress
        startListenBrainzDiscoveryPolling(playlistMbid);

        // Update modal to show discovering state
        updateYouTubeDiscoveryModal(playlistMbid, {
            phase: 'discovering',
            progress: 0,
            results: []
        });

        showToast('Starting ListenBrainz discovery...', 'info');

    } catch (error) {
        console.error('❌ Error starting ListenBrainz discovery:', error);
        showToast(`Error: ${error.message}`, 'error');

        // Revert phase on error
        state.phase = 'fresh';
        state.status = 'pending';
    }
}

async function startListenBrainzPlaylistSync(playlistMbid) {
    const state = listenbrainzPlaylistStates[playlistMbid];
    if (!state) {
        console.error('❌ No ListenBrainz playlist state found');
        return;
    }

    try {
        console.log('🔄 Starting ListenBrainz sync for:', state.playlist.name);

        // Check if being called from playlist listing (has UI elements) or modal
        const listingPlaylistId = `discover-lb-playlist-${playlistMbid}`;
        const statusDisplay = document.getElementById(`${listingPlaylistId}-sync-status`);
        const isFromListing = statusDisplay !== null;

        if (isFromListing) {
            console.log('🔄 Sync initiated from playlist listing');
            // Show status display in listing
            statusDisplay.style.display = 'block';
            const syncButton = document.getElementById(`${listingPlaylistId}-sync-btn`);
            if (syncButton) {
                syncButton.disabled = true;
                syncButton.style.opacity = '0.5';
            }
        }

        // Call backend to start sync
        const response = await fetch(`/api/listenbrainz/sync/start/${playlistMbid}`, {
            method: 'POST'
        });

        if (!response.ok) {
            const error = await response.json();
            throw new Error(error.error || 'Failed to start sync');
        }

        // Capture sync_playlist_id for WebSocket subscription
        const result = await response.json();
        const syncPlaylistId = result.sync_playlist_id;
        if (state) state.syncPlaylistId = syncPlaylistId;

        // Update phase to syncing
        state.phase = 'syncing';

        // Start polling for sync progress
        if (isFromListing) {
            startListenBrainzListingSyncPolling(playlistMbid, listingPlaylistId, syncPlaylistId);
        } else {
            startListenBrainzSyncPolling(playlistMbid, syncPlaylistId);
            updateYouTubeModalButtons(playlistMbid, 'syncing');
        }

        showToast('Starting ListenBrainz sync...', 'info');

    } catch (error) {
        console.error('❌ Error starting ListenBrainz sync:', error);
        showToast(`Error: ${error.message}`, 'error');
    }
}

function startListenBrainzListingSyncPolling(playlistMbid, listingPlaylistId, syncPlaylistId) {
    console.log(`🔄 Starting listing sync polling for: ${playlistMbid} (UI: ${listingPlaylistId})`);

    // Stop any existing polling
    if (activeYouTubePollers[playlistMbid]) {
        clearInterval(activeYouTubePollers[playlistMbid]);
    }

    // Resolve syncPlaylistId from argument or stored state
    const lbState = listenbrainzPlaylistStates[playlistMbid];
    syncPlaylistId = syncPlaylistId || (lbState && lbState.syncPlaylistId);

    // Phase 6: Subscribe via WebSocket
    if (socketConnected && syncPlaylistId) {
        socket.emit('sync:subscribe', { playlist_ids: [syncPlaylistId] });
        _syncProgressCallbacks[syncPlaylistId] = (data) => {
            const progress = data.progress || {};
            const total = progress.total_tracks || 0;
            const matched = progress.matched_tracks || 0;
            const failed = progress.failed_tracks || 0;
            const percentage = total > 0 ? Math.round((matched / total) * 100) : 0;

            const totalEl = document.getElementById(`${listingPlaylistId}-sync-total`);
            const matchedEl = document.getElementById(`${listingPlaylistId}-sync-matched`);
            const failedEl = document.getElementById(`${listingPlaylistId}-sync-failed`);
            const percentageEl = document.getElementById(`${listingPlaylistId}-sync-percentage`);

            if (totalEl) totalEl.textContent = total;
            if (matchedEl) matchedEl.textContent = matched;
            if (failedEl) failedEl.textContent = failed;
            if (percentageEl) percentageEl.textContent = percentage;

            if (data.status === 'finished') {
                if (activeYouTubePollers[playlistMbid]) { clearInterval(activeYouTubePollers[playlistMbid]); delete activeYouTubePollers[playlistMbid]; }
                socket.emit('sync:unsubscribe', { playlist_ids: [syncPlaylistId] });
                delete _syncProgressCallbacks[syncPlaylistId];

                const statusDisplay = document.getElementById(`${listingPlaylistId}-sync-status`);
                const syncButton = document.getElementById(`${listingPlaylistId}-sync-btn`);
                if (statusDisplay) setTimeout(() => { statusDisplay.style.display = 'none'; }, 3000);
                if (syncButton) { syncButton.disabled = false; syncButton.style.opacity = '1'; }

                if (listenbrainzPlaylistStates[playlistMbid]) {
                    listenbrainzPlaylistStates[playlistMbid].phase = 'sync_complete';
                }

                showToast(`Sync complete: ${matched}/${total} tracks matched`, 'success');
            } else if (data.status === 'error' || data.status === 'cancelled') {
                if (activeYouTubePollers[playlistMbid]) { clearInterval(activeYouTubePollers[playlistMbid]); delete activeYouTubePollers[playlistMbid]; }
                socket.emit('sync:unsubscribe', { playlist_ids: [syncPlaylistId] });
                delete _syncProgressCallbacks[syncPlaylistId];
                showToast(`Sync failed: ${data.error || 'Unknown error'}`, 'error');
            }
        };
    }

    const pollInterval = setInterval(async () => {
        if (socketConnected) return; // Phase 6: WS handles updates
        try {
            const response = await fetch(`/api/listenbrainz/sync/status/${playlistMbid}`);
            const status = await response.json();

            if (status.error) {
                console.error('❌ Error polling ListenBrainz sync status:', status.error);
                clearInterval(pollInterval);
                delete activeYouTubePollers[playlistMbid];
                return;
            }

            const totalEl = document.getElementById(`${listingPlaylistId}-sync-total`);
            const matchedEl = document.getElementById(`${listingPlaylistId}-sync-matched`);
            const failedEl = document.getElementById(`${listingPlaylistId}-sync-failed`);
            const percentageEl = document.getElementById(`${listingPlaylistId}-sync-percentage`);

            if (totalEl) totalEl.textContent = status.progress?.total_tracks || 0;
            if (matchedEl) matchedEl.textContent = status.progress?.matched_tracks || 0;
            if (failedEl) failedEl.textContent = status.progress?.failed_tracks || 0;

            const percentage = status.progress?.total_tracks > 0
                ? Math.round(((status.progress?.matched_tracks || 0) / status.progress.total_tracks) * 100)
                : 0;
            if (percentageEl) percentageEl.textContent = percentage;

            if (status.complete) {
                clearInterval(pollInterval);
                delete activeYouTubePollers[playlistMbid];

                const statusDisplay = document.getElementById(`${listingPlaylistId}-sync-status`);
                const syncButton = document.getElementById(`${listingPlaylistId}-sync-btn`);
                if (statusDisplay) setTimeout(() => { statusDisplay.style.display = 'none'; }, 3000);
                if (syncButton) { syncButton.disabled = false; syncButton.style.opacity = '1'; }

                if (listenbrainzPlaylistStates[playlistMbid]) {
                    listenbrainzPlaylistStates[playlistMbid].phase = 'sync_complete';
                }

                showToast(`Sync complete: ${status.progress?.matched_tracks || 0}/${status.progress?.total_tracks || 0} tracks matched`, 'success');
            }
        } catch (error) {
            console.error('❌ Error polling ListenBrainz listing sync:', error);
            clearInterval(pollInterval);
            delete activeYouTubePollers[playlistMbid];
        }
    }, 1000);

    activeYouTubePollers[playlistMbid] = pollInterval;
}

// ============================================================================

