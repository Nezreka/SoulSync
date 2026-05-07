// DISCOVERY FIX MODAL - Manual Track Matching
// ============================================================================

// Global state for discovery fix
let currentDiscoveryFix = {
    platform: null,    // 'youtube', 'tidal', 'beatport'
    identifier: null,  // url_hash or playlist_id
    trackIndex: null,
    sourceTrack: null,
    sourceArtist: null
};

// Store event handler reference to allow proper removal
let discoveryFixEnterHandler = null;

/**
 * Open discovery fix modal for a specific track
 */
function openDiscoveryFixModal(platform, identifier, trackIndex) {
    console.log(`🔧 Opening fix modal: ${platform} - ${identifier} - track ${trackIndex}`);

    // Get the discovery state
    // Note: Beatport, Tidal, and ListenBrainz have their own states, but reuse YouTube modal infrastructure
    let state, result;
    if (platform === 'youtube') {
        // Check both states - ListenBrainz also uses YouTube modal infrastructure
        state = listenbrainzPlaylistStates[identifier] || youtubePlaylistStates[identifier];
    } else if (platform === 'tidal') {
        state = youtubePlaylistStates[identifier]; // Tidal uses YouTube state infrastructure
    } else if (platform === 'beatport') {
        state = youtubePlaylistStates[identifier]; // Beatport uses YouTube state infrastructure
    } else if (platform === 'listenbrainz') {
        state = listenbrainzPlaylistStates[identifier]; // ListenBrainz has its own state
    } else if (platform === 'deezer') {
        state = youtubePlaylistStates[identifier]; // Deezer uses YouTube state infrastructure
    } else if (platform === 'mirrored') {
        state = youtubePlaylistStates[identifier]; // Mirrored playlists use YouTube state infrastructure
    } else if (platform === 'spotify_public') {
        state = youtubePlaylistStates[identifier]; // Spotify public playlists use YouTube state infrastructure
    }

    // Support both camelCase and snake_case for discovery results
    const results = state?.discoveryResults || state?.discovery_results;
    result = results?.[trackIndex];

    if (!result) {
        console.error('❌ Track data not found');
        console.error('  Platform:', platform);
        console.error('  Identifier:', identifier);
        console.error('  State:', state);
        console.error('  Discovery results (camelCase):', state?.discoveryResults?.length);
        console.error('  Discovery results (snake_case):', state?.discovery_results?.length);
        showToast('Track data not found', 'error');
        return;
    }

    console.log('✅ Found result:', result);

    // Store context
    currentDiscoveryFix = {
        platform,
        identifier,
        trackIndex,
        sourceTrack: result.lb_track || result.yt_track || result.tidal_track?.name || result.beatport_track?.title || result.track_name || 'Unknown Track',
        sourceArtist: result.lb_artist || result.yt_artist || result.tidal_track?.artist || result.beatport_track?.artist || result.artist_name || 'Unknown Artist'
    };

    // Find the fix modal within the active discovery modal
    const discoveryModal = document.getElementById(`youtube-discovery-modal-${identifier}`);
    if (!discoveryModal) {
        console.error('❌ Discovery modal not found:', identifier);
        showToast('Discovery modal not found', 'error');
        return;
    }

    const fixModalOverlay = discoveryModal.querySelector('.discovery-fix-modal-overlay');
    if (!fixModalOverlay) {
        console.error('❌ Fix modal not found within discovery modal');
        showToast('Fix modal not found', 'error');
        return;
    }

    console.log('🔍 Source track:', currentDiscoveryFix.sourceTrack);
    console.log('🔍 Source artist:', currentDiscoveryFix.sourceArtist);
    console.log('🔍 Fix modal overlay found:', fixModalOverlay);

    // Populate modal - scope within the specific fix modal overlay to handle duplicate IDs
    const sourceTrackEl = fixModalOverlay.querySelector('#fix-modal-source-track');
    const sourceArtistEl = fixModalOverlay.querySelector('#fix-modal-source-artist');
    const trackInput = fixModalOverlay.querySelector('#fix-modal-track-input');
    const artistInput = fixModalOverlay.querySelector('#fix-modal-artist-input');

    console.log('🔍 Elements found:', {
        sourceTrackEl,
        sourceArtistEl,
        trackInput,
        artistInput
    });

    if (!sourceTrackEl || !sourceArtistEl || !trackInput || !artistInput) {
        console.error('❌ Fix modal elements not found in DOM');
        showToast('Fix modal not properly initialized', 'error');
        return;
    }

    sourceTrackEl.textContent = currentDiscoveryFix.sourceTrack;
    sourceArtistEl.textContent = currentDiscoveryFix.sourceArtist;
    trackInput.value = currentDiscoveryFix.sourceTrack;
    artistInput.value = currentDiscoveryFix.sourceArtist;

    console.log('✅ Populated modal with:', {
        track: trackInput.value,
        artist: artistInput.value
    });

    // Remove old enter key handler if exists
    if (discoveryFixEnterHandler) {
        trackInput.removeEventListener('keypress', discoveryFixEnterHandler);
        artistInput.removeEventListener('keypress', discoveryFixEnterHandler);
    }

    // Add new enter key handler
    discoveryFixEnterHandler = function (e) {
        if (e.key === 'Enter') searchDiscoveryFix();
    };
    trackInput.addEventListener('keypress', discoveryFixEnterHandler);
    artistInput.addEventListener('keypress', discoveryFixEnterHandler);

    // Show modal BEFORE auto-search so elements are visible
    fixModalOverlay.classList.remove('hidden');
    console.log('✅ Fix modal opened, starting auto-search...');

    // Auto-search with initial values (delay allows modal layout to settle and prevents accidental clicks)
    setTimeout(() => searchDiscoveryFix(), 500);
}

/**
 * Close discovery fix modal
 */
function closeDiscoveryFixModal() {
    if (!currentDiscoveryFix.identifier) {
        console.warn('No active fix modal to close');
        return;
    }

    const discoveryModal = document.getElementById(`youtube-discovery-modal-${currentDiscoveryFix.identifier}`);
    if (discoveryModal) {
        const fixModalOverlay = discoveryModal.querySelector('.discovery-fix-modal-overlay');
        if (fixModalOverlay) {
            fixModalOverlay.classList.add('hidden');
        }
    }

    currentDiscoveryFix = { platform: null, identifier: null, trackIndex: null, sourceTrack: null, sourceArtist: null };
}

/**
 * Search for tracks in the configured metadata source
 */
async function searchDiscoveryFix() {
    if (!currentDiscoveryFix.identifier) {
        console.error('No active fix modal context');
        return;
    }

    const discoveryModal = document.getElementById(`youtube-discovery-modal-${currentDiscoveryFix.identifier}`);
    if (!discoveryModal) {
        console.error('Discovery modal not found');
        return;
    }

    const fixModalOverlay = discoveryModal.querySelector('.discovery-fix-modal-overlay');
    if (!fixModalOverlay) {
        console.error('Fix modal not found');
        return;
    }

    const trackInput = fixModalOverlay.querySelector('#fix-modal-track-input').value.trim();
    const artistInput = fixModalOverlay.querySelector('#fix-modal-artist-input').value.trim();

    if (!trackInput && !artistInput) {
        showToast('Enter track name or artist', 'error');
        return;
    }

    const resultsContainer = fixModalOverlay.querySelector('#fix-modal-results');

    // Build search params
    const params = new URLSearchParams();
    if (trackInput) params.set('track', trackInput);
    if (artistInput) params.set('artist', artistInput);
    if (!trackInput && !artistInput) {
        resultsContainer.innerHTML = '<div class="no-results">Enter a track name or artist.</div>';
        return;
    }
    params.set('limit', '50');

    // Use the user's active metadata source first, then fall back to others
    const activeSource = (currentMusicSourceName || 'Spotify').toLowerCase();
    const allSources = [
        { key: 'spotify', endpoint: '/api/spotify/search_tracks', label: 'Spotify' },
        { key: 'deezer', endpoint: '/api/deezer/search_tracks', label: 'Deezer' },
        { key: 'itunes', endpoint: '/api/itunes/search_tracks', label: 'iTunes' },
    ];
    // Put the active source first, keep others as fallbacks
    const activeIdx = allSources.findIndex(s => activeSource.includes(s.key));
    const searchSources = activeIdx > 0
        ? [allSources[activeIdx], ...allSources.filter((_, i) => i !== activeIdx)]
        : allSources;

    resultsContainer.innerHTML = `<div class="loading">🔍 Searching ${searchSources[0].label}...</div>`;

    try {
        for (let i = 0; i < searchSources.length; i++) {
            const source = searchSources[i];
            try {
                const response = await fetch(`${source.endpoint}?${params.toString()}`);
                const data = await response.json();

                if (data.tracks && data.tracks.length > 0) {
                    renderDiscoveryFixResults(data.tracks, fixModalOverlay);
                    return;
                }
                // No results from this source — show next source status if there is one
                if (i < searchSources.length - 1) {
                    resultsContainer.innerHTML = `<div class="loading">🔍 Trying ${searchSources[i + 1].label}...</div>`;
                }
            } catch (e) {
                console.warn(`Discovery fix search failed on ${source.label}: ${e.message}`);
            }
        }
        // All sources exhausted
        resultsContainer.innerHTML = '<div class="no-results">No matches found on any source. Try different search terms.</div>';

    } catch (error) {
        console.error('Search error:', error);
        resultsContainer.innerHTML = '<div class="error-message">❌ Search failed. Try again.</div>';
    }
}

/**
 * Render search results as clickable cards
 */
function renderDiscoveryFixResults(tracks, fixModalOverlay) {
    const resultsContainer = fixModalOverlay.querySelector('#fix-modal-results');
    resultsContainer.innerHTML = '';

    // Sort: standard album versions first, live/remix/cover/soundtrack last
    const _variantPattern = /\b(live|remix|remaster|refix|cover|acoustic|demo|instrumental|radio edit|single version|deluxe|edition|soundtrack|from .* film|from .* movie|bonus track)\b|\b\w+ mix\b/i;
    const _albumVariantPattern = /\b(live|greatest hits|best of|collection|compilation|soundtrack|from .* film|from .* movie|remaster|deluxe|redux|expanded|anniversary)\b/i;
    tracks.sort((a, b) => {
        const aVariant = _variantPattern.test(a.name || '') || _albumVariantPattern.test(a.album || '');
        const bVariant = _variantPattern.test(b.name || '') || _albumVariantPattern.test(b.album || '');
        if (aVariant !== bVariant) return aVariant ? 1 : -1;
        return 0; // preserve original order within same category
    });

    tracks.forEach(track => {
        const card = document.createElement('div');
        card.className = 'fix-result-card';
        card.onclick = () => selectDiscoveryFixTrack(track);

        card.innerHTML = `
            <div class="fix-result-card-content">
                <div class="fix-result-title">${escapeHtml(track.name || 'Unknown Track')}</div>
                <div class="fix-result-artist">${escapeHtml((track.artists || ['Unknown Artist']).join(', '))}</div>
                <div class="fix-result-album">${escapeHtml(track.album || 'Unknown Album')}</div>
                <div class="fix-result-duration">${formatDuration(track.duration_ms || 0)}</div>
            </div>
        `;

        resultsContainer.appendChild(card);
    });
}

/**
 * User selected a track - update discovery state
 */
async function selectDiscoveryFixTrack(track) {
    console.log('✅ User selected track:', track);

    // Confirm selection to prevent accidental clicks from layout shift
    const artists = (track.artists || ['Unknown Artist']).join(', ');
    if (!await showConfirmDialog({ title: 'Confirm Match', message: `Match to "${track.name}" by ${artists}?`, confirmText: 'Confirm' })) return;

    const { platform, identifier, trackIndex } = currentDiscoveryFix;

    console.log('📡 Updating backend match:', { platform, identifier, trackIndex, track });

    // Update backend
    try {
        // Get the correct backend identifier based on platform
        let backendIdentifier = identifier;

        if (platform === 'tidal') {
            // For Tidal, backend expects the actual playlist_id, not url_hash
            const state = youtubePlaylistStates[identifier];
            backendIdentifier = state?.tidal_playlist_id || identifier;
        } else if (platform === 'deezer') {
            // For Deezer, backend expects the actual playlist_id, not url_hash
            const state = youtubePlaylistStates[identifier];
            backendIdentifier = state?.deezer_playlist_id || identifier;
        } else if (platform === 'spotify_public') {
            // For Spotify Public, backend expects the url_hash
            const state = youtubePlaylistStates[identifier];
            backendIdentifier = state?.spotify_public_playlist_id || identifier;
        } else if (platform === 'beatport') {
            // For Beatport, backend expects url_hash (same as identifier)
            backendIdentifier = identifier;
        }

        // Mirrored playlists route through the YouTube endpoint (which already handles mirrored_ prefixes)
        const apiPlatform = platform === 'mirrored' ? 'youtube' : (platform === 'spotify_public' ? 'spotify-public' : platform);

        const requestBody = {
            identifier: backendIdentifier,
            track_index: trackIndex,
            spotify_track: {
                id: track.id,
                name: track.name,
                artists: track.artists,
                album: track.album,
                duration_ms: track.duration_ms,
                image_url: track.image_url || null
            }
        };

        console.log('📡 Request body:', requestBody);
        console.log('📡 Backend identifier:', backendIdentifier);

        const response = await fetch(`/api/${apiPlatform}/discovery/update_match`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(requestBody)
        });

        console.log('📡 Response status:', response.status);

        const data = await response.json();

        console.log('📡 Response data:', data);

        if (data.error) {
            showToast(`Failed to update: ${data.error}`, 'error');
            console.error('❌ Backend update failed:', data.error);
            return;
        }

        showToast('Match updated successfully!', 'success');
        console.log('✅ Backend update successful');

        // Update frontend state
        // Note: Beatport and Tidal reuse youtubePlaylistStates for discovery results
        // ListenBrainz uses its own state but may also be accessed via YouTube
        let state;
        if (platform === 'youtube') {
            state = listenbrainzPlaylistStates[identifier] || youtubePlaylistStates[identifier];
        } else if (platform === 'tidal') {
            state = youtubePlaylistStates[identifier];
        } else if (platform === 'deezer') {
            state = youtubePlaylistStates[identifier];
        } else if (platform === 'beatport') {
            state = youtubePlaylistStates[identifier];
        } else if (platform === 'listenbrainz') {
            state = listenbrainzPlaylistStates[identifier];
        } else if (platform === 'mirrored') {
            state = youtubePlaylistStates[identifier];
        } else if (platform === 'spotify_public') {
            state = youtubePlaylistStates[identifier];
        }

        // Support both camelCase and snake_case
        const results = state?.discoveryResults || state?.discovery_results;
        if (state && results && results[trackIndex]) {
            const result = results[trackIndex];
            const wasNotFound = result.status !== 'found' && result.status_class !== 'found';

            // Update result
            result.status = '✅ Found';
            result.status_class = 'found';
            result.spotify_track = track.name;
            result.spotify_artist = Array.isArray(track.artists)
                ? track.artists
                    .map(a => (typeof a === 'object' && a !== null) ? (a.name || '') : a)
                    .filter(Boolean)
                    .join(', ') || '-'
                : (track.artists || '-');
            result.spotify_album = track.album;
            result.spotify_id = track.id;
            result.duration = formatDuration(track.duration_ms);
            result.manual_match = true;
            // User picked a real metadata match — no longer a wing-it track
            result.wing_it_fallback = false;

            // IMPORTANT: Also set spotify_data for download/sync compatibility.
            // Build album as a dict (not a bare string) so the download
            // pipeline can find cover art via album.image_url / album.images.
            // This matches the shape that normal discovery produces.
            const _fixImageUrl = track.image_url || '';
            let _fixAlbumObj;
            if (track.album && typeof track.album === 'object') {
                _fixAlbumObj = { ...track.album };
                if (_fixImageUrl && !_fixAlbumObj.image_url) _fixAlbumObj.image_url = _fixImageUrl;
                if (_fixImageUrl && !_fixAlbumObj.images) _fixAlbumObj.images = [{ url: _fixImageUrl }];
            } else {
                _fixAlbumObj = { name: track.album || '' };
                if (_fixImageUrl) {
                    _fixAlbumObj.image_url = _fixImageUrl;
                    _fixAlbumObj.images = [{ url: _fixImageUrl }];
                }
            }
            result.spotify_data = {
                id: track.id,
                name: track.name,
                artists: track.artists,
                album: _fixAlbumObj,
                duration_ms: track.duration_ms,
                image_url: _fixImageUrl
            };

            // Increment match count if this was previously not_found or error
            if (wasNotFound) {
                state.spotifyMatches = (state.spotifyMatches || 0) + 1;

                // Update progress bar and text
                const spotify_total = state.spotify_total || state.playlist?.tracks?.length || 0;
                const progress = spotify_total > 0 ? Math.round((state.spotifyMatches / spotify_total) * 100) : 0;

                const progressBar = document.getElementById(`youtube-discovery-progress-${identifier}`);
                const progressText = document.getElementById(`youtube-discovery-progress-text-${identifier}`);

                if (progressBar) {
                    progressBar.style.width = `${progress}%`;
                }
                if (progressText) {
                    progressText.textContent = `${state.spotifyMatches} / ${spotify_total} tracks matched (${progress}%)`;
                }

                console.log(`✅ Updated progress: ${state.spotifyMatches}/${spotify_total} (${progress}%)`);

                // Also update the Deezer playlist card if this is a Deezer fix
                if (platform === 'deezer' && state.deezer_playlist_id) {
                    const deezerState = deezerPlaylistStates[state.deezer_playlist_id];
                    if (deezerState) {
                        deezerState.spotifyMatches = state.spotifyMatches;
                        updateDeezerCardProgress(state.deezer_playlist_id, {
                            spotify_matches: state.spotifyMatches,
                            spotify_total: spotify_total
                        });
                    }
                }

                // Also update the Tidal playlist card if this is a Tidal fix
                if (platform === 'tidal' && state.tidal_playlist_id) {
                    const tidalState = tidalPlaylistStates?.[state.tidal_playlist_id];
                    if (tidalState) {
                        tidalState.spotifyMatches = state.spotifyMatches;
                    }
                }

                // Also update the Spotify Public playlist card if this is a Spotify Public fix
                if (platform === 'spotify_public' && state.spotify_public_playlist_id) {
                    const spState = spotifyPublicPlaylistStates?.[state.spotify_public_playlist_id];
                    if (spState) {
                        spState.spotifyMatches = state.spotifyMatches;
                        updateSpotifyPublicCardProgress(state.spotify_public_playlist_id, {
                            spotify_matches: state.spotifyMatches,
                            spotify_total: spotify_total
                        });
                    }
                }
            }

            // Update UI - refresh the table row
            updateDiscoveryModalSingleRow(platform, identifier, trackIndex);
        }

        // Close modal
        closeDiscoveryFixModal();

    } catch (error) {
        console.error('Error updating match:', error);
        showToast('Failed to update match', 'error');
    }
}

/**
 * Update a single row in the discovery modal table
 */
function updateDiscoveryModalSingleRow(platform, identifier, trackIndex) {
    // Check both state maps - ListenBrainz uses its own, others reuse youtubePlaylistStates
    const state = listenbrainzPlaylistStates[identifier] || youtubePlaylistStates[identifier];

    // Support both camelCase and snake_case
    const results = state?.discoveryResults || state?.discovery_results;
    if (!state || !results || !results[trackIndex]) {
        console.warn(`Cannot update row: state or result not found`);
        return;
    }

    const result = results[trackIndex];
    const row = document.getElementById(`discovery-row-${identifier}-${trackIndex}`);

    if (!row) {
        console.warn(`Cannot update row: row element not found for ${identifier}-${trackIndex}`);
        return;
    }

    // Update cells
    const statusCell = row.querySelector('.discovery-status');
    const spotifyTrackCell = row.querySelector('.spotify-track');
    const spotifyArtistCell = row.querySelector('.spotify-artist');
    const spotifyAlbumCell = row.querySelector('.spotify-album');
    const actionsCell = row.querySelector('.discovery-actions');

    if (statusCell) {
        statusCell.textContent = result.status;
        statusCell.className = `discovery-status ${result.status_class}`;
    }

    if (spotifyTrackCell) spotifyTrackCell.textContent = result.spotify_track || '-';
    if (spotifyArtistCell) spotifyArtistCell.textContent = result.spotify_artist || '-';
    if (spotifyAlbumCell) spotifyAlbumCell.textContent = result.spotify_album || '-';

    // Update action button
    if (actionsCell) {
        actionsCell.innerHTML = generateDiscoveryActionButton(result, identifier, platform);
    }

    console.log(`✅ Updated row ${trackIndex} in discovery modal`);
}

async function unmatchDiscoveryTrack(platform, identifier, trackIndex) {
    // Determine the correct API base for this platform
    const apiBase = platform === 'tidal' ? '/api/tidal'
        : platform === 'deezer' ? '/api/deezer'
        : platform === 'spotify-public' ? '/api/spotify-public'
        : platform === 'beatport' ? '/api/beatport'
        : platform === 'listenbrainz' ? '/api/listenbrainz'
        : '/api/youtube';

    try {
        const response = await fetch(`${apiBase}/discovery/unmatch`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ identifier, track_index: trackIndex })
        });
        const data = await response.json();
        if (data.success) {
            // Update the row in the discovery modal table
            const state = youtubePlaylistStates[identifier]
                || (window.tidalDiscoveryStates && window.tidalDiscoveryStates[identifier])
                || {};
            if (state.discovery_results && state.discovery_results[trackIndex]) {
                const r = state.discovery_results[trackIndex];
                r.status = '❌ Not Found';
                r.status_class = 'not-found';
                r.spotify_track = '-';
                r.spotify_artist = '-';
                r.spotify_album = '-';
                r.spotify_data = null;
                r.matched_data = null;
                r.confidence = 0;
                r.wing_it_fallback = false;
                r.manual_match = false;
            }
            // Re-render the row — discovery rows use id="discovery-row-{urlHash}-{index}"
            const row = document.getElementById(`discovery-row-${identifier}-${trackIndex}`);
            if (row) {
                const statusCell = row.querySelector('.discovery-status');
                if (statusCell) { statusCell.textContent = '❌ Not Found'; statusCell.className = 'discovery-status not-found'; }
                const matchedCells = row.querySelectorAll('.spotify-track, .spotify-artist, .spotify-album');
                matchedCells.forEach(c => c.textContent = '-');
                const actionsCell = row.querySelector('.discovery-actions');
                if (actionsCell) {
                    actionsCell.innerHTML = `<button class="fix-match-btn" onclick="openDiscoveryFixModal('${platform}', '${identifier}', ${trackIndex})" title="Manually search for this track">🔧 Fix</button>`;
                }
            }
            showToast('Match removed', 'success');
        } else {
            showToast(data.error || 'Failed to remove match', 'error');
        }
    } catch (e) {
        console.error('Unmatch error:', e);
        showToast('Failed to remove match', 'error');
    }
}

// Make discovery-fix functions available globally for onclick handlers
window.openDiscoveryFixModal = openDiscoveryFixModal;
window.closeDiscoveryFixModal = closeDiscoveryFixModal;
window.searchDiscoveryFix = searchDiscoveryFix;
window.unmatchDiscoveryTrack = unmatchDiscoveryTrack;
window.openMatchingModal = openMatchingModal;
window.closeMatchingModal = closeMatchingModal;
window.selectArtist = selectArtist;
window.selectAlbum = selectAlbum;

/**
 * Handle post-download cleanup: clear finished downloads from slskd.
 * Scan and database update are now handled by system automations
 * (batch_complete → scan_library → library_scan_completed → start_database_update).
 */
async function handlePostDownloadAutomation(playlistId, process) {
    try {
        const successfulDownloads = getSuccessfulDownloadCount(process);
        if (successfulDownloads === 0) {
            console.log(`🔄 [AUTO] No successful downloads for ${playlistId} - skipping cleanup`);
            return;
        }
        console.log(`🔄 [AUTO] Post-download cleanup for ${playlistId} (${successfulDownloads} successful downloads)`);

        // Clear completed downloads from slskd
        try {
            const clearResponse = await fetch('/api/downloads/clear-finished', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' }
            });
            if (clearResponse.ok) {
                console.log(`✅ [AUTO] Completed downloads cleared`);
            } else {
                console.warn(`⚠️ [AUTO] Clear downloads failed, continuing anyway`);
            }
        } catch (error) {
            console.warn(`⚠️ [AUTO] Clear error: ${error.message}`);
        }
    } catch (error) {
        console.error(`❌ [AUTO] Error in post-download cleanup: ${error.message}`);
    }
}

/**
 * Extract successful download count from a download process
 */
function getSuccessfulDownloadCount(process) {
    try {
        // For processes that have completed, check the modal for completed count
        if (process && process.modalElement) {
            const statElement = process.modalElement.querySelector('[id*="stat-downloaded-"]');
            if (statElement && statElement.textContent) {
                const count = parseInt(statElement.textContent, 10);
                return isNaN(count) ? 0 : count;
            }
        }

        // Fallback: assume successful if process completed without obvious failure
        if (process && process.status === 'complete') {
            return 1; // Conservative assumption for single download
        }

        return 0;
    } catch (error) {
        console.warn(`⚠️ [AUTO] Error getting successful download count: ${error.message}`);
        return 0;
    }
}

// ===============================
// ADD TO WISHLIST MODAL FUNCTIONS
// ===============================

let currentWishlistModalData = null;
let wishlistModalVersion = 0;

/**
 * Open the Add to Wishlist modal for an album/EP/single
 * @param {Object} album - Album object with id, name, image_url, etc.
 * @param {Object} artist - Artist object with id, name, image_url
 * @param {Array} tracks - Array of track objects
 * @param {string} albumType - Type of release (album, EP, single)
 */
async function openAddToWishlistModal(album, artist, tracks, albumType, trackOwnership) {
    wishlistModalVersion++;
    showLoadingOverlay('Preparing wishlist...');
    console.log(`🎵 Opening Add to Wishlist modal for: ${artist.name} - ${album.name}`);

    try {
        // Store current modal data for use by other functions
        currentWishlistModalData = {
            album,
            artist,
            tracks,
            albumType
        };

        const modal = document.getElementById('add-to-wishlist-modal');
        const overlay = document.getElementById('add-to-wishlist-modal-overlay');

        if (!modal || !overlay) {
            console.error('Add to wishlist modal elements not found');
            return;
        }

        // Generate and populate hero section
        const heroContent = generateWishlistModalHeroSection(album, artist, tracks, albumType, trackOwnership);
        const heroContainer = document.getElementById('add-to-wishlist-modal-hero');
        if (heroContainer) {
            heroContainer.innerHTML = heroContent;
        }

        // Generate and populate track list
        const trackListHTML = generateWishlistTrackList(tracks, trackOwnership);
        const trackListContainer = document.getElementById('wishlist-track-list');
        if (trackListContainer) {
            trackListContainer.innerHTML = trackListHTML;
        }

        // Set up the "Add to Wishlist" button click handler
        const addToWishlistBtn = document.getElementById('confirm-add-to-wishlist-btn');
        if (addToWishlistBtn) {
            addToWishlistBtn.onclick = () => handleAddToWishlist();
        }

        // Show the modal
        overlay.classList.remove('hidden');
        hideLoadingOverlay();

        console.log(`✅ Successfully opened Add to Wishlist modal for: ${album.name}`);

    } catch (error) {
        console.error('❌ Error opening Add to Wishlist modal:', error);
        hideLoadingOverlay();
        showToast(`Error opening wishlist modal: ${error.message}`, 'error');
    }
}

/**
 * Generate the hero section HTML for the wishlist modal
 */
function generateWishlistModalHeroSection(album, artist, tracks, albumType, trackOwnership) {
    const artistImage = artist.image_url || '';
    const albumImage = album.image_url || '';
    const trackCount = tracks.length;

    // Calculate missing tracks if ownership info is available
    let trackDetailText = `${trackCount} track${trackCount !== 1 ? 's' : ''}`;
    if (trackOwnership) {
        const ownedCount = Object.values(trackOwnership).filter(v => v === true).length;
        const missingCount = trackCount - ownedCount;
        if (missingCount > 0 && ownedCount > 0) {
            trackDetailText = `${missingCount} of ${trackCount} tracks missing`;
        }
    }

    let heroBackgroundImage = '';
    if (albumImage) {
        heroBackgroundImage = `<div class="add-to-wishlist-modal-hero-bg" style="background-image: url('${albumImage}');"></div>`;
    }

    const heroContent = `
        <div class="add-to-wishlist-modal-hero-content">
            <div class="add-to-wishlist-modal-hero-images">
                ${artistImage ? `<img class="add-to-wishlist-modal-hero-image artist" src="${artistImage}" alt="${escapeHtml(artist.name)}">` : ''}
                ${albumImage ? `<img class="add-to-wishlist-modal-hero-image album" src="${albumImage}" alt="${escapeHtml(album.name)}">` : ''}
            </div>
            <div class="add-to-wishlist-modal-hero-metadata">
                <h1 class="add-to-wishlist-modal-hero-title">${escapeHtml(album.name || 'Unknown Album')}</h1>
                <div class="add-to-wishlist-modal-hero-subtitle">by ${escapeHtml(artist.name || 'Unknown Artist')}</div>
                <div class="add-to-wishlist-modal-hero-details">
                    <span class="add-to-wishlist-modal-hero-detail">${albumType || 'Album'}</span>
                    <span class="add-to-wishlist-modal-hero-detail">${trackDetailText}</span>
                </div>
            </div>
        </div>
    `;

    return `
        ${heroBackgroundImage}
        ${heroContent}
    `;
}

/**
 * Generate the track list HTML for the wishlist modal
 */
function generateWishlistTrackList(tracks, trackOwnership) {
    if (!tracks || tracks.length === 0) {
        return '<div style="text-align: center; padding: 40px; color: rgba(255, 255, 255, 0.6);">No tracks found</div>';
    }

    return tracks.map((track, index) => {
        const trackNumber = track.track_number || (index + 1);
        const trackName = escapeHtml(track.name || 'Unknown Track');
        const artistsString = formatArtists(track.artists) || 'Unknown Artist';
        const duration = formatDuration(track.duration_ms);

        const trackData = trackOwnership ? trackOwnership[track.name] : null;
        const isOwned = trackData && (trackData.owned === true || trackData === true);
        const isKnown = trackData !== null && trackData !== undefined;
        const ownershipClass = isOwned ? 'owned' : (isKnown && !isOwned ? 'missing' : '');
        const badge = isOwned
            ? '<div class="wishlist-track-badge owned"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"><polyline points="20 6 9 17 4 12"></polyline></svg></div>'
            : '';

        return `
            <div class="wishlist-track-item ${ownershipClass}">
                <div class="wishlist-track-number">${trackNumber}</div>
                <div class="wishlist-track-info">
                    <div class="wishlist-track-name">${trackName}</div>
                    <div class="wishlist-track-artists">${artistsString}</div>
                </div>
                <div class="wishlist-track-duration">${duration}</div>
                ${badge}
            </div>
        `;
    }).join('');
}

/**
 * Handle the "Add to Wishlist" button click
 */
async function handleAddToWishlist() {
    if (!currentWishlistModalData) {
        console.error('❌ No wishlist modal data available');
        return;
    }

    const { album, artist, tracks, albumType } = currentWishlistModalData;
    const addToWishlistBtn = document.getElementById('confirm-add-to-wishlist-btn');

    try {
        // Show loading state
        if (addToWishlistBtn) {
            addToWishlistBtn.classList.add('loading');
            addToWishlistBtn.textContent = 'Adding...';
            addToWishlistBtn.disabled = true;
        }

        console.log(`🔄 Adding ${tracks.length} tracks to wishlist for: ${artist.name} - ${album.name}`);

        let successCount = 0;
        let errorCount = 0;

        // Add each track to wishlist individually
        for (const track of tracks) {
            try {
                // Ensure artists field is in the correct format (array of objects)
                let formattedArtists = track.artists;
                if (typeof track.artists === 'string') {
                    // If artists is a string, convert to array of objects
                    formattedArtists = [{ name: track.artists }];
                } else if (Array.isArray(track.artists)) {
                    // If artists is already an array, ensure each item is an object
                    formattedArtists = track.artists.map(artistItem => {
                        if (typeof artistItem === 'string') {
                            return { name: artistItem };
                        } else if (typeof artistItem === 'object' && artistItem !== null) {
                            return artistItem;
                        } else {
                            return { name: 'Unknown Artist' };
                        }
                    });
                } else {
                    // Fallback to array with single artist object
                    formattedArtists = [{ name: artist.name }];
                }

                const formattedTrack = {
                    ...track,
                    artists: formattedArtists
                };

                // Use track's album data if available (from API), falling back to modal's album data
                // This ensures consistency with how the Artists page handles wishlisting
                let trackAlbum = track.album;
                let trackAlbumType = albumType || 'album';

                if (trackAlbum && typeof trackAlbum === 'object') {
                    // Track has album data from API - use its album_type
                    trackAlbumType = trackAlbum.album_type || albumType || 'album';
                    // Ensure album has required fields
                    if (!trackAlbum.name) {
                        trackAlbum.name = album.name;
                    }
                    if (!trackAlbum.id) {
                        trackAlbum.id = album.id;
                    }
                } else {
                    // Fall back to the album passed to the modal
                    trackAlbum = album;
                }

                console.log(`🔄 Adding track with formatted artists:`, formattedTrack.name, formattedTrack.artists);
                console.log(`🔄 Using album_type: ${trackAlbumType} (from ${track.album ? 'track.album' : 'modal album'})`);

                const response = await fetch('/api/add-album-to-wishlist', {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json'
                    },
                    body: JSON.stringify({
                        track: formattedTrack,
                        artist: artist,
                        album: trackAlbum,
                        source_type: 'album',
                        source_context: {
                            album_name: trackAlbum.name,
                            artist_name: artist.name,
                            album_type: trackAlbumType
                        }
                    })
                });

                const result = await response.json();

                if (result.success) {
                    successCount++;
                    console.log(`✅ Added "${track.name}" to wishlist`);
                } else {
                    errorCount++;
                    console.error(`❌ Failed to add "${track.name}" to wishlist: ${result.error}`);
                }

            } catch (error) {
                errorCount++;
                console.error(`❌ Error adding "${track.name}" to wishlist:`, error);
            }
        }

        // Show completion message
        if (successCount > 0) {
            const message = errorCount > 0
                ? `Added ${successCount}/${tracks.length} tracks to wishlist (${errorCount} failed)`
                : `Added ${successCount} tracks to wishlist`;
            showToast(message, successCount === tracks.length ? 'success' : 'warning');
        } else {
            showToast('Failed to add any tracks to wishlist', 'error');
        }

        // Close the modal
        closeAddToWishlistModal();

        console.log(`✅ Wishlist addition complete: ${successCount} successful, ${errorCount} failed`);

    } catch (error) {
        console.error('❌ Error in handleAddToWishlist:', error);
        showToast(`Error adding to wishlist: ${error.message}`, 'error');
    } finally {
        // Reset button state
        if (addToWishlistBtn) {
            addToWishlistBtn.classList.remove('loading');
            addToWishlistBtn.textContent = 'Add to Wishlist';
            addToWishlistBtn.disabled = false;
        }
    }
}

/**
 * Lazy-load per-track ownership indicators into an already-open wishlist modal.
 * Fetches ownership from the backend, then updates the modal DOM in-place.
 * If all tracks are owned (release-source discrepancy), also fixes the source card.
 */
async function lazyLoadTrackOwnership(artistName, tracks, sourceCard, albumName = null) {
    const myVersion = wishlistModalVersion;
    try {
        const checkBody = {
            artist_name: artistName,
            tracks: tracks.map(t => ({ name: t.name, track_number: t.track_number }))
        };
        if (albumName) checkBody.album_name = albumName;
        const resp = await fetch('/api/library/check-tracks', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(checkBody)
        });
        const data = await resp.json();
        if (!data.success) return;

        // Guard against stale updates if user reopened modal for a different album
        if (myVersion !== wishlistModalVersion) return;

        const ownership = data.owned_tracks;
        const trackItems = document.querySelectorAll('#wishlist-track-list .wishlist-track-item');

        let ownedCount = 0;
        trackItems.forEach((item, index) => {
            const track = tracks[index];
            if (!track) return;
            const trackData = ownership[track.name];
            const isOwned = trackData && trackData.owned === true;
            if (isOwned) {
                ownedCount++;
                item.classList.add('owned');
                // Add metadata line below track name
                const trackInfo = item.querySelector('.wishlist-track-info');
                if (trackInfo && (trackData.format || trackData.bitrate)) {
                    const metaDiv = document.createElement('div');
                    metaDiv.className = 'wishlist-track-meta';
                    let metaHtml = '';
                    if (trackData.format === 'MP3' && trackData.bitrate) {
                        metaHtml += `<span class="wishlist-track-format">MP3 ${trackData.bitrate}</span>`;
                    } else {
                        if (trackData.format) {
                            metaHtml += `<span class="wishlist-track-format">${trackData.format}</span>`;
                        }
                        if (trackData.bitrate) {
                            metaHtml += `<span class="wishlist-track-bitrate">${trackData.bitrate} kbps</span>`;
                        }
                    }
                    metaDiv.innerHTML = metaHtml;
                    trackInfo.appendChild(metaDiv);
                }
                const badge = document.createElement('div');
                badge.className = 'wishlist-track-badge owned';
                badge.innerHTML = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"><polyline points="20 6 9 17 4 12"></polyline></svg>';
                item.appendChild(badge);
            } else {
                item.classList.add('missing');
            }
        });

        // Aggregate format summary from owned tracks
        const formatSet = new Set();
        for (const trackName of Object.keys(ownership)) {
            const td = ownership[trackName];
            if (td && td.owned && td.format) {
                if (td.format === 'MP3' && td.bitrate) {
                    formatSet.add(`MP3-${td.bitrate}`);
                } else {
                    formatSet.add(td.format);
                }
            }
        }
        if (formatSet.size > 0) {
            const heroDetailsContainer = document.querySelector('.add-to-wishlist-modal-hero-details');
            if (heroDetailsContainer) {
                // Remove any existing format tag
                const existing = heroDetailsContainer.querySelector('.modal-format-tag');
                if (existing) existing.remove();
                const formatTag = document.createElement('span');
                formatTag.className = 'modal-format-tag';
                formatTag.textContent = [...formatSet].sort().join(' / ');
                heroDetailsContainer.appendChild(formatTag);
            }
        }

        // Update hero subtitle with missing count
        const missingCount = tracks.length - ownedCount;
        const heroDetails = document.querySelectorAll('.add-to-wishlist-modal-hero-detail');
        const trackDetailEl = heroDetails.length > 1 ? heroDetails[heroDetails.length - 1] : null;
        if (trackDetailEl && missingCount > 0 && ownedCount > 0) {
            trackDetailEl.textContent = `${missingCount} of ${tracks.length} tracks missing`;
        }

        // If ALL returned tracks are owned, this is a release-source discrepancy
        // (e.g. total_tracks says 15 but API only returns 14, and all 14 are owned)
        // Fix the source card to show complete
        if (missingCount === 0 && sourceCard && sourceCard._releaseData) {
            sourceCard._releaseData.track_completion = {
                owned_tracks: ownedCount,
                total_tracks: tracks.length,
                percentage: 100,
                missing_tracks: 0
            };
            const completionText = sourceCard.querySelector('.completion-text');
            if (completionText) {
                completionText.textContent = `Complete (${ownedCount})`;
                completionText.className = 'completion-text complete';
                completionText.title = '';
            }
            const completionFill = sourceCard.querySelector('.completion-fill');
            if (completionFill) {
                completionFill.style.width = '100%';
                completionFill.classList.remove('partial');
                completionFill.classList.add('complete');
            }
        }
    } catch (e) {
        console.warn('Could not load track ownership:', e);
    }
}

/**
 * Close the Add to Wishlist modal
 */
function closeAddToWishlistModal() {
    console.log('🔄 Closing Add to Wishlist modal');

    try {
        const overlay = document.getElementById('add-to-wishlist-modal-overlay');
        if (overlay) {
            overlay.classList.add('hidden');
        }

        // Clear current modal data
        currentWishlistModalData = null;

        // Clear hero content
        const heroContainer = document.getElementById('add-to-wishlist-modal-hero');
        if (heroContainer) {
            heroContainer.innerHTML = '';
        }

        // Clear track list
        const trackListContainer = document.getElementById('wishlist-track-list');
        if (trackListContainer) {
            trackListContainer.innerHTML = '';
        }

        console.log('✅ Add to Wishlist modal closed successfully');

    } catch (error) {
        console.error('❌ Error closing Add to Wishlist modal:', error);
    }
}

/**
 * Handle "Download Now" button click from the Add to Wishlist modal.
 * Captures modal data, closes the wishlist modal, then opens the download missing tracks modal.
 */
async function handleWishlistDownloadNow() {
    if (!currentWishlistModalData) {
        showToast('No album data available', 'error');
        return;
    }

    // Capture data before closeAddToWishlistModal clears it
    const { album, artist, tracks, albumType } = currentWishlistModalData;

    // Close the wishlist modal
    closeAddToWishlistModal();

    // Build virtual playlist ID and name (same pattern as createArtistAlbumVirtualPlaylist)
    const virtualPlaylistId = `artist_album_${artist.id}_${album.id}`;
    const playlistName = `[${artist.name}] ${album.name}`;

    // If a download process already exists for this album, just show the existing modal
    if (activeDownloadProcesses[virtualPlaylistId]) {
        const process = activeDownloadProcesses[virtualPlaylistId];
        if (process.modalElement) {
            process.modalElement.style.display = 'flex';
        }
        return;
    }

    // Open download missing modal (reuses existing function)
    showLoadingOverlay('Loading album...');
    await openDownloadMissingModalForArtistAlbum(
        virtualPlaylistId, playlistName, tracks, album, artist, false
    );
    hideLoadingOverlay();

    // Register download bubble (reuses existing artist bubble system)
    registerArtistDownload(artist, album, virtualPlaylistId, albumType);
}

/**
 * Add all tracks from any download modal to the wishlist
 * Universal handler for all modal types (artist albums, playlists, YouTube, Tidal, etc.)
 */
async function addModalTracksToWishlist(playlistId) {
    const process = activeDownloadProcesses[playlistId];
    if (!process) {
        console.error('❌ No active process found for:', playlistId);
        showToast('Error: Could not find playlist data', 'error');
        return;
    }

    // Verify we have tracks
    if (!process.tracks || process.tracks.length === 0) {
        console.error('❌ No tracks found in process:', process);
        showToast('Error: No tracks to add', 'error');
        return;
    }

    // Filter tracks based on checkbox selection (if checkboxes exist in this modal)
    const wishlistTbody = document.getElementById(`download-tracks-tbody-${playlistId}`);
    let tracks = process.tracks;
    if (wishlistTbody) {
        const allCbs = wishlistTbody.querySelectorAll('.track-select-cb');
        if (allCbs.length > 0) {
            const checkedCbs = wishlistTbody.querySelectorAll('.track-select-cb:checked');
            const selectedIndices = new Set([...checkedCbs].map(cb => parseInt(cb.dataset.trackIndex)));
            tracks = process.tracks.filter((_, i) => selectedIndices.has(i));
        }
    }

    // Get album context if available (for artist album downloads)
    // Artist is resolved per-track below — process.artist is only set for album downloads,
    // not for playlists, so we must NOT use it as a blanket default.
    const processArtist = process.artist || null;
    const album = process.album || process.playlist || { name: 'Playlist', id: playlistId };

    console.log(`🔄 Adding ${tracks.length} tracks from "${album.name}" to wishlist (process artist: ${processArtist?.name || 'per-track'})`);

    // Disable the button to prevent double-clicks
    const wishlistBtn = document.getElementById(`add-to-wishlist-btn-${playlistId}`);
    if (wishlistBtn) {
        wishlistBtn.disabled = true;
        wishlistBtn.classList.add('loading');
        wishlistBtn.textContent = 'Adding...';
    }

    try {
        let successCount = 0;
        let errorCount = 0;

        // Add each track to wishlist individually
        let wingItSkipped = 0;
        for (const track of tracks) {
            try {
                // Skip wing-it fallback tracks — they have no real metadata,
                // adding them to wishlist would just retry with raw data
                const trackId = track.id || '';
                if (String(trackId).startsWith('wing_it_')) {
                    wingItSkipped++;
                    console.log(`⏭️ Skipping wing-it track from wishlist: ${track.name}`);
                    continue;
                }

                // Format artists field to match backend expectations
                let formattedArtists = track.artists;
                if (typeof track.artists === 'string') {
                    formattedArtists = [{ name: track.artists }];
                } else if (Array.isArray(track.artists)) {
                    formattedArtists = track.artists.map(artistItem => {
                        if (typeof artistItem === 'string') {
                            return { name: artistItem };
                        } else if (typeof artistItem === 'object' && artistItem !== null) {
                            return artistItem;
                        } else {
                            return { name: 'Unknown Artist' };
                        }
                    });
                } else {
                    formattedArtists = [{ name: artist.name }];
                }

                const formattedTrack = {
                    ...track,
                    artists: formattedArtists
                };

                // Use track's own album data if available
                // Convert string album names to objects if needed (no Spotify fetch!)
                let trackAlbum = track.album;
                let trackAlbumType = 'album';

                // Handle both object and string album formats
                if (typeof trackAlbum === 'string') {
                    // Album is just a string - convert to minimal object
                    trackAlbum = {
                        name: trackAlbum,
                        album_type: 'album',
                        images: []
                    };
                    trackAlbumType = 'album';
                } else if (trackAlbum && typeof trackAlbum === 'object') {
                    // Album is already an object - extract album_type
                    trackAlbumType = trackAlbum.album_type || 'album';
                    // Ensure it has a name
                    if (!trackAlbum.name) {
                        trackAlbum.name = 'Unknown Album';
                    }
                } else {
                    // No album data at all - create minimal object
                    trackAlbum = {
                        name: 'Unknown Album',
                        album_type: 'album',
                        images: []
                    };
                    trackAlbumType = 'album';
                }

                // Resolve artist: for album downloads, use the album-level artist to keep
                // all tracks grouped under one artist in the wishlist. Per-track artists
                // (like individual vocalists on a soundtrack) should NOT split the album.
                let trackArtist;
                if (processArtist && processArtist.name) {
                    // Album context exists — use album artist to keep tracks grouped
                    trackArtist = processArtist;
                } else if (formattedArtists.length > 0 && formattedArtists[0].name && formattedArtists[0].name !== 'Unknown Artist') {
                    // No album context (playlist/single) — use track's own artist
                    trackArtist = formattedArtists[0];
                } else {
                    trackArtist = { name: 'Unknown Artist', id: null };
                }

                const response = await fetch('/api/add-album-to-wishlist', {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json'
                    },
                    body: JSON.stringify({
                        track: formattedTrack,
                        artist: trackArtist,
                        album: trackAlbum,
                        source_type: 'album',
                        source_context: {
                            album_name: trackAlbum.name,
                            artist_name: trackArtist.name,
                            album_type: trackAlbumType
                        }
                    })
                });

                const result = await response.json();

                if (result.success) {
                    successCount++;
                } else {
                    errorCount++;
                    console.error(`❌ Failed to add "${track.name}" to wishlist: ${result.error}`);
                }
            } catch (error) {
                errorCount++;
                console.error(`❌ Error adding "${track.name}" to wishlist:`, error);
            }
        }

        // Show result toast
        if (successCount > 0) {
            let message = errorCount > 0
                ? `Added ${successCount}/${tracks.length} tracks to wishlist (${errorCount} failed)`
                : `Added ${successCount} tracks to wishlist`;
            if (wingItSkipped > 0) message += ` (${wingItSkipped} wing-it skipped)`;
            showToast(message, 'success');

            // Close the modal on success
            await closeDownloadMissingModal(playlistId);
        } else {
            showToast('Failed to add any tracks to wishlist', 'error');
        }

    } catch (error) {
        console.error('❌ Error in addModalTracksToWishlist:', error);
        showToast(`Error adding to wishlist: ${error.message}`, 'error');
    } finally {
        // Re-enable button if still on screen (in case of error)
        if (wishlistBtn) {
            wishlistBtn.disabled = false;
            wishlistBtn.classList.remove('loading');
            wishlistBtn.textContent = 'Add to Wishlist';
        }
    }
}

/**
 * Format duration from milliseconds to MM:SS format
 */
function formatDuration(durationMs) {
    if (!durationMs || durationMs <= 0) {
        return '--:--';
    }

    const totalSeconds = Math.floor(durationMs / 1000);
    const minutes = Math.floor(totalSeconds / 60);
    const seconds = totalSeconds % 60;

    return `${minutes}:${seconds.toString().padStart(2, '0')}`;
}

// Note: Functions from other modules (downloads.js, sync-spotify.js, sync-services.js, artists.js)
// are already global via their function declarations and do not need window.X = X assignments.

// Add to Wishlist Modal functions (new)
window.openAddToWishlistModal = openAddToWishlistModal;
window.closeAddToWishlistModal = closeAddToWishlistModal;
window.handleAddToWishlist = handleAddToWishlist;
window.handleWishlistDownloadNow = handleWishlistDownloadNow;
window.addModalTracksToWishlist = addModalTracksToWishlist;


// APPEND THIS JAVASCRIPT SNIPPET (B)

function initializeFilters() {
    const toggleBtn = document.getElementById('filter-toggle-btn');
    const container = document.getElementById('filters-container');
    const content = document.getElementById('filter-content');

    if (toggleBtn && container && content) {
        // Using .onclick ensures we only ever have one click handler
        toggleBtn.onclick = () => {
            const isExpanded = container.classList.contains('expanded');

            if (isExpanded) {
                // Collapse the container
                container.classList.remove('expanded');
                toggleBtn.textContent = '⏷ Filters';
            } else {
                // Expand the container
                content.classList.remove('hidden'); // Make sure content is visible for animation
                container.classList.add('expanded');
                toggleBtn.textContent = '⏶ Filters';
            }
        };
    }

    // This part is correct and doesn't need to change
    document.querySelectorAll('.filter-btn').forEach(button => {
        button.addEventListener('click', handleFilterClick);
    });
}

function handleFilterClick(event) {
    const button = event.target;
    const filterType = button.dataset.filterType;
    const value = button.dataset.value;

    if (filterType === 'type') currentFilterType = value;
    if (filterType === 'format') currentFilterFormat = value;
    if (filterType === 'sort') currentSortBy = value;

    if (button.id === 'sort-order-btn') {
        isSortReversed = !isSortReversed;
        button.textContent = isSortReversed ? '↑' : '↓';
    }

    document.querySelectorAll(`.filter-btn[data-filter-type="${filterType}"]`).forEach(btn => {
        btn.classList.remove('active');
    });
    if (filterType) { // Don't try to activate the sort order button
        button.classList.add('active');
    }

    applyFiltersAndSort();
}

function resetFilters() {
    currentFilterType = 'all';
    currentFilterFormat = 'all';
    currentSortBy = 'quality_score';
    isSortReversed = false;

    document.querySelectorAll('.filter-btn').forEach(btn => btn.classList.remove('active'));
    document.querySelector('.filter-btn[data-filter-type="type"][data-value="all"]').classList.add('active');
    document.querySelector('.filter-btn[data-filter-type="format"][data-value="all"]').classList.add('active');
    document.querySelector('.filter-btn[data-filter-type="sort"][data-value="quality_score"]').classList.add('active');
    document.getElementById('sort-order-btn').textContent = '↓';
}

function applyFiltersAndSort() {
    let processedResults = [...allSearchResults];
    const query = document.getElementById('downloads-search-input').value.trim().toLowerCase();

    // 1. Filter by Type
    if (currentFilterType !== 'all') {
        processedResults = processedResults.filter(r => r.result_type === currentFilterType);
    }

    // 2. Filter by Format
    if (currentFilterFormat !== 'all') {
        processedResults = processedResults.filter(r => {
            const quality = (r.dominant_quality || r.quality || '').toLowerCase();
            return quality === currentFilterFormat;
        });
    }

    // 3. Sort Results
    processedResults.sort((a, b) => {
        let valA, valB;

        // Special handling for relevance sort
        if (currentSortBy === 'relevance') {
            valA = calculateRelevanceScore(a, query);
            valB = calculateRelevanceScore(b, query);
            return valB - valA; // Higher score is better
        }

        // Special handling for availability
        if (currentSortBy === 'availability') {
            valA = (a.free_upload_slots || 0) - (a.queue_length || 0) * 0.1;
            valB = (b.free_upload_slots || 0) - (b.queue_length || 0) * 0.1;
            return valB - valA;
        }

        valA = a[currentSortBy] || 0;
        valB = b[currentSortBy] || 0;

        if (typeof valA === 'string') {
            // For name/title sort, use the correct property
            const titleA = (a.album_title || a.title || '').toLowerCase();
            const titleB = (b.album_title || b.title || '').toLowerCase();
            return titleA.localeCompare(titleB);
        }

        // Default numeric sort (descending)
        return valB - valA;
    });

    // Handle sort direction toggle
    const sortDefaults = {
        relevance: 'desc', quality_score: 'desc', size: 'desc', bitrate: 'desc',
        upload_speed: 'desc', duration: 'desc', availability: 'desc',
        title: 'asc', username: 'asc'
    };

    const defaultOrder = sortDefaults[currentSortBy] || 'desc';
    if ((defaultOrder === 'asc' && isSortReversed) || (defaultOrder === 'desc' && !isSortReversed)) {
        processedResults.reverse();
    }

    displayDownloadsResults(processedResults);
}

function calculateRelevanceScore(result, query) {
    let score = 0.0;
    const queryTerms = query.split(' ').filter(t => t.length > 1);

    // 1. Search Term Matching (40%)
    let searchableText = `${result.title || ''} ${result.artist || ''} ${result.album || ''} ${result.album_title || ''}`.toLowerCase();
    let termMatches = 0;
    for (const term of queryTerms) {
        if (searchableText.includes(term)) {
            termMatches++;
        }
    }
    score += (termMatches / queryTerms.length) * 0.40;

    // 2. Quality Score (25%)
    score += (result.quality_score || 0) * 0.25;

    // 3. User Reliability (Availability & Speed) (20%)
    const reliability = ((result.free_upload_slots || 0) > 0 ? 0.5 : 0) + Math.min(1, (result.upload_speed || 0) / 500) * 0.5;
    score += reliability * 0.20;

    // 4. File Completeness (Bitrate & Duration) (15%)
    const completeness = (Math.min(1, (result.bitrate || 0) / 320) * 0.5) + (result.duration > 0 ? 0.5 : 0);
    score += completeness * 0.15;

    return score;
}
// APPEND THIS JAVASCRIPT SNIPPET (B)

function initializeFilters() {
    const toggleBtn = document.getElementById('filter-toggle-btn');
    const container = document.getElementById('filters-container');
    const content = document.getElementById('filter-content');

    if (toggleBtn && container && content) {
        // Using .onclick ensures we only ever have one click handler
        toggleBtn.onclick = () => {
            const isExpanded = container.classList.contains('expanded');

            if (isExpanded) {
                // Collapse the container
                container.classList.remove('expanded');
                toggleBtn.textContent = '⏷ Filters';
            } else {
                // Expand the container
                content.classList.remove('hidden'); // Make sure content is visible for animation
                container.classList.add('expanded');
                toggleBtn.textContent = '⏶ Filters';
            }
        };
    }

    // This part is correct and doesn't need to change
    document.querySelectorAll('.filter-btn').forEach(button => {
        button.addEventListener('click', handleFilterClick);
    });
}

function handleFilterClick(event) {
    const button = event.target;
    const filterType = button.dataset.filterType;
    const value = button.dataset.value;

    if (filterType === 'type') currentFilterType = value;
    if (filterType === 'format') currentFilterFormat = value;
    if (filterType === 'sort') currentSortBy = value;

    if (button.id === 'sort-order-btn') {
        isSortReversed = !isSortReversed;
        button.textContent = isSortReversed ? '↑' : '↓';
    }

    document.querySelectorAll(`.filter-btn[data-filter-type="${filterType}"]`).forEach(btn => {
        btn.classList.remove('active');
    });
    if (filterType) { // Don't try to activate the sort order button
        button.classList.add('active');
    }

    applyFiltersAndSort();
}

function resetFilters() {
    currentFilterType = 'all';
    currentFilterFormat = 'all';
    currentSortBy = 'quality_score';
    isSortReversed = false;

    document.querySelectorAll('.filter-btn').forEach(btn => btn.classList.remove('active'));
    document.querySelector('.filter-btn[data-filter-type="type"][data-value="all"]').classList.add('active');
    document.querySelector('.filter-btn[data-filter-type="format"][data-value="all"]').classList.add('active');
    document.querySelector('.filter-btn[data-filter-type="sort"][data-value="quality_score"]').classList.add('active');
    document.getElementById('sort-order-btn').textContent = '↓';
}

function applyFiltersAndSort() {
    let processedResults = [...allSearchResults];
    const query = document.getElementById('downloads-search-input').value.trim().toLowerCase();

    // 1. Filter by Type
    if (currentFilterType !== 'all') {
        processedResults = processedResults.filter(r => r.result_type === currentFilterType);
    }

    // 2. Filter by Format
    if (currentFilterFormat !== 'all') {
        processedResults = processedResults.filter(r => {
            const quality = (r.dominant_quality || r.quality || '').toLowerCase();
            return quality === currentFilterFormat;
        });
    }

    // 3. Sort Results
    processedResults.sort((a, b) => {
        let valA, valB;

        // Special handling for relevance sort
        if (currentSortBy === 'relevance') {
            valA = calculateRelevanceScore(a, query);
            valB = calculateRelevanceScore(b, query);
            return valB - valA; // Higher score is better
        }

        // Special handling for availability
        if (currentSortBy === 'availability') {
            valA = (a.free_upload_slots || 0) - (a.queue_length || 0) * 0.1;
            valB = (b.free_upload_slots || 0) - (b.queue_length || 0) * 0.1;
            return valB - valA;
        }

        valA = a[currentSortBy] || 0;
        valB = b[currentSortBy] || 0;

        if (typeof valA === 'string') {
            // For name/title sort, use the correct property
            const titleA = (a.album_title || a.title || '').toLowerCase();
            const titleB = (b.album_title || b.title || '').toLowerCase();
            return titleA.localeCompare(titleB);
        }

        // Default numeric sort (descending)
        return valB - valA;
    });

    // Handle sort direction toggle
    const sortDefaults = {
        relevance: 'desc', quality_score: 'desc', size: 'desc', bitrate: 'desc',
        upload_speed: 'desc', duration: 'desc', availability: 'desc',
        title: 'asc', username: 'asc'
    };

    const defaultOrder = sortDefaults[currentSortBy] || 'desc';
    if ((defaultOrder === 'asc' && isSortReversed) || (defaultOrder === 'desc' && !isSortReversed)) {
        processedResults.reverse();
    }

    displayDownloadsResults(processedResults);
}

function calculateRelevanceScore(result, query) {
    let score = 0.0;
    const queryTerms = query.split(' ').filter(t => t.length > 1);

    // 1. Search Term Matching (40%)
    let searchableText = `${result.title || ''} ${result.artist || ''} ${result.album || ''} ${result.album_title || ''}`.toLowerCase();
    let termMatches = 0;
    for (const term of queryTerms) {
        if (searchableText.includes(term)) {
            termMatches++;
        }
    }
    score += (termMatches / queryTerms.length) * 0.40;

    // 2. Quality Score (25%)
    score += (result.quality_score || 0) * 0.25;

    // 3. User Reliability (Availability & Speed) (20%)
    const reliability = ((result.free_upload_slots || 0) > 0 ? 0.5 : 0) + Math.min(1, (result.upload_speed || 0) / 500) * 0.5;
    score += reliability * 0.20;

    // 4. File Completeness (Bitrate & Duration) (15%)
    const completeness = (Math.min(1, (result.bitrate || 0) / 320) * 0.5) + (result.duration > 0 ? 0.5 : 0);
    score += completeness * 0.15;

    return score;
}

// Add to global scope for onclick
window.handleFilterClick = handleFilterClick;

// ===============================
// MATCHED DOWNLOADS MODAL
// ===============================

// Global state for matching modal
let currentMatchingData = {
    searchResult: null,
    isAlbumDownload: false,
    albumResult: null,
    selectedArtist: null,
    selectedAlbum: null,
    currentStage: 'artist' // 'artist' or 'album'
};

let searchTimers = {
    artist: null,
    album: null
};

function openMatchingModal(searchResult, isAlbumDownload = false, albumResult = null) {
    console.log('🎯 Opening matching modal for:', searchResult);

    // Store the current matching data
    currentMatchingData = {
        searchResult: searchResult,
        isAlbumDownload: isAlbumDownload,
        albumResult: albumResult,
        selectedArtist: null,
        selectedAlbum: null,
        currentStage: 'artist'
    };

    // Show modal
    const overlay = document.getElementById('matching-modal-overlay');
    overlay.classList.remove('hidden');

    // Reset modal state
    resetModalState();

    // Set appropriate title and stage
    const modalTitle = document.getElementById('matching-modal-title');
    const artistStageTitle = document.getElementById('artist-stage-title');

    if (isAlbumDownload) {
        modalTitle.textContent = 'Match album download to release';
        artistStageTitle.textContent = 'Step 1: Select the correct Artist';
        document.getElementById('album-selection-stage').style.display = 'block';
    } else {
        modalTitle.textContent = 'Match track download to release';
        artistStageTitle.textContent = 'Select the correct Artist for this Single';
        document.getElementById('album-selection-stage').style.display = 'none';
    }

    // Generate initial artist suggestions
    fetchArtistSuggestions();

    // Setup event listeners
    setupModalEventListeners();
}

function closeMatchingModal() {
    const overlay = document.getElementById('matching-modal-overlay');
    overlay.classList.add('hidden');

    // Clear timers
    Object.values(searchTimers).forEach(timer => {
        if (timer) clearTimeout(timer);
    });

    // Reset state
    currentMatchingData = {
        searchResult: null,
        isAlbumDownload: false,
        albumResult: null,
        selectedArtist: null,
        selectedAlbum: null,
        currentStage: 'artist'
    };
}

function resetModalState() {
    // Show artist stage, hide album stage
    document.getElementById('artist-selection-stage').classList.remove('hidden');
    document.getElementById('album-selection-stage').classList.add('hidden');

    // Clear all suggestion containers
    document.getElementById('artist-suggestions').innerHTML = '';
    document.getElementById('artist-manual-results').innerHTML = '';
    document.getElementById('album-suggestions').innerHTML = '';
    document.getElementById('album-manual-results').innerHTML = '';

    // Clear search inputs
    document.getElementById('artist-search-input').value = '';
    document.getElementById('album-search-input').value = '';

    // Reset button states
    document.getElementById('confirm-match-btn').disabled = true;

    // Reset selections
    currentMatchingData.selectedArtist = null;
    currentMatchingData.selectedAlbum = null;
    currentMatchingData.currentStage = 'artist';
}

function setupModalEventListeners() {
    // Search input listeners
    const artistInput = document.getElementById('artist-search-input');
    const albumInput = document.getElementById('album-search-input');

    artistInput.removeEventListener('input', handleArtistSearch);
    artistInput.addEventListener('input', handleArtistSearch);

    albumInput.removeEventListener('input', handleAlbumSearch);
    albumInput.addEventListener('input', handleAlbumSearch);

    // Button listeners
    const skipBtn = document.getElementById('skip-matching-btn');
    const cancelBtn = document.getElementById('cancel-match-btn');
    const confirmBtn = document.getElementById('confirm-match-btn');

    skipBtn.onclick = skipMatching;
    cancelBtn.onclick = closeMatchingModal;
    confirmBtn.onclick = confirmMatch;
}

async function fetchArtistSuggestions() {
    try {
        showLoadingCards('artist-suggestions', 'Finding artist...');

        const response = await fetch('/api/match/suggestions', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                search_result: currentMatchingData.searchResult,
                context: 'artist',
                is_album: currentMatchingData.isAlbumDownload,
                album_result: currentMatchingData.albumResult
            })
        });

        const data = await response.json();
        if (data.suggestions) {
            renderArtistSuggestions(data.suggestions);
        } else {
            showNoResultsMessage('artist-suggestions', 'No artist suggestions found');
        }
    } catch (error) {
        console.error('Error fetching artist suggestions:', error);
        showNoResultsMessage('artist-suggestions', 'Error loading suggestions');
    }
}

async function fetchAlbumSuggestions() {
    if (!currentMatchingData.selectedArtist) return;

    try {
        showLoadingCards('album-suggestions', 'Finding album...');

        const response = await fetch('/api/match/suggestions', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                search_result: currentMatchingData.searchResult,
                context: 'album',
                selected_artist: currentMatchingData.selectedArtist
            })
        });

        const data = await response.json();
        if (data.suggestions) {
            renderAlbumSuggestions(data.suggestions);
        } else {
            showNoResultsMessage('album-suggestions', 'No album suggestions found');
        }
    } catch (error) {
        console.error('Error fetching album suggestions:', error);
        showNoResultsMessage('album-suggestions', 'Error loading suggestions');
    }
}

function renderArtistSuggestions(suggestions) {
    const container = document.getElementById('artist-suggestions');
    container.innerHTML = '';

    if (!suggestions.length) {
        showNoResultsMessage('artist-suggestions', 'No artist matches found');
        return;
    }

    suggestions.forEach(suggestion => {
        const card = createArtistCard(suggestion.artist, suggestion.confidence);
        container.appendChild(card);
    });
}

function renderAlbumSuggestions(suggestions) {
    const container = document.getElementById('album-suggestions');
    container.innerHTML = '';

    if (!suggestions.length) {
        showNoResultsMessage('album-suggestions', 'No album matches found');
        return;
    }

    suggestions.forEach(suggestion => {
        const card = createAlbumCard(suggestion.album, suggestion.confidence);
        container.appendChild(card);
    });
}

function createArtistCard(artist, confidence) {
    const card = document.createElement('div');
    card.className = 'suggestion-card';
    card.onclick = () => selectArtist(artist);

    const imageUrl = artist.image_url || '';
    const confidencePercent = Math.round(confidence * 100);

    // Add data attribute for lazy loading
    card.dataset.artistId = artist.id;
    card.dataset.needsImage = imageUrl ? 'false' : 'true';

    card.innerHTML = `
        <div class="suggestion-card-overlay"></div>
        <div class="suggestion-card-content">
            <div class="suggestion-card-name" title="${escapeHtml(artist.name)}">${escapeHtml(artist.name)}</div>
            <div class="suggestion-card-details">
                ${artist.genres && artist.genres.length ? escapeHtml(artist.genres.slice(0, 2).join(', ')) : 'Artist'}
            </div>
            <div class="suggestion-card-confidence">${confidencePercent}% match</div>
        </div>
    `;

    // Set background image if available
    if (imageUrl) {
        card.style.backgroundImage = `url(${imageUrl})`;
        card.style.backgroundSize = 'cover';
        card.style.backgroundPosition = 'center';
    }

    return card;
}

function createAlbumCard(album, confidence) {
    const card = document.createElement('div');
    card.className = 'suggestion-card';
    card.onclick = () => selectAlbum(album);

    const imageUrl = album.image_url || '';
    const confidencePercent = Math.round(confidence * 100);
    const year = album.release_date ? album.release_date.split('-')[0] : '';

    card.innerHTML = `
        <div class="suggestion-card-overlay"></div>
        <div class="suggestion-card-content">
            <div class="suggestion-card-name" title="${escapeHtml(album.name)}">${escapeHtml(album.name)}</div>
            <div class="suggestion-card-details">
                ${album.album_type ? escapeHtml(album.album_type.charAt(0).toUpperCase() + album.album_type.slice(1)) : 'Album'}${year ? ` • ${year}` : ''}
            </div>
            <div class="suggestion-card-confidence">${confidencePercent}% match</div>
        </div>
    `;

    // Set background image if available
    if (imageUrl) {
        card.style.backgroundImage = `url(${imageUrl})`;
        card.style.backgroundSize = 'cover';
        card.style.backgroundPosition = 'center';
    }

    return card;
}

function selectArtist(artist) {
    // Clear previous selections
    document.querySelectorAll('#artist-suggestions .suggestion-card').forEach(card => {
        card.classList.remove('selected');
    });
    document.querySelectorAll('#artist-manual-results .suggestion-card').forEach(card => {
        card.classList.remove('selected');
    });

    // Mark new selection
    event.currentTarget.classList.add('selected');

    // Store selection
    currentMatchingData.selectedArtist = artist;

    console.log('🎯 Selected artist:', artist.name);

    if (currentMatchingData.isAlbumDownload) {
        // Transition to album selection stage
        transitionToAlbumStage();
    } else {
        // Enable confirm button for single downloads
        document.getElementById('confirm-match-btn').disabled = false;
    }
}

function selectAlbum(album) {
    // Clear previous selections
    document.querySelectorAll('#album-suggestions .suggestion-card').forEach(card => {
        card.classList.remove('selected');
    });
    document.querySelectorAll('#album-manual-results .suggestion-card').forEach(card => {
        card.classList.remove('selected');
    });

    // Mark new selection
    event.currentTarget.classList.add('selected');

    // Store selection
    currentMatchingData.selectedAlbum = album;

    console.log('🎯 Selected album:', album.name);

    // Enable confirm button
    document.getElementById('confirm-match-btn').disabled = false;
}

function transitionToAlbumStage() {
    // Hide artist stage
    document.getElementById('artist-selection-stage').classList.add('hidden');

    // Show album stage
    const albumStage = document.getElementById('album-selection-stage');
    albumStage.classList.remove('hidden');

    // Update selected artist name
    document.getElementById('selected-artist-name').textContent = currentMatchingData.selectedArtist.name;

    // Update current stage
    currentMatchingData.currentStage = 'album';

    // Fetch album suggestions
    fetchAlbumSuggestions();
}

function handleArtistSearch(event) {
    const query = event.target.value.trim();

    // Clear previous timer
    if (searchTimers.artist) {
        clearTimeout(searchTimers.artist);
    }

    if (query.length < 2) {
        document.getElementById('artist-manual-results').innerHTML = '';
        return;
    }

    // Debounce search
    searchTimers.artist = setTimeout(() => {
        performArtistSearch(query);
    }, 400);
}

function handleAlbumSearch(event) {
    const query = event.target.value.trim();

    // Clear previous timer
    if (searchTimers.album) {
        clearTimeout(searchTimers.album);
    }

    if (query.length < 2) {
        document.getElementById('album-manual-results').innerHTML = '';
        return;
    }

    // Debounce search
    searchTimers.album = setTimeout(() => {
        performAlbumSearch(query);
    }, 400);
}

async function performArtistSearch(query) {
    try {
        showLoadingCards('artist-manual-results', 'Searching artists...');

        const requestBody = {
            query: query,
            context: 'artist'
        };
        console.log('Manual search request:', requestBody);

        const response = await fetch('/api/match/search', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(requestBody)
        });

        const data = await response.json();
        console.log('Manual search response:', data);
        if (data.provider) currentMatchingData.provider = data.provider;
        if (data.results) {
            console.log('Results array:', data.results);
            renderArtistSearchResults(data.results);
        } else {
            showNoResultsMessage('artist-manual-results', 'No artists found');
        }
    } catch (error) {
        console.error('Error searching artists:', error);
        showNoResultsMessage('artist-manual-results', 'Error searching artists');
    }
}

async function performAlbumSearch(query) {
    if (!currentMatchingData.selectedArtist) return;

    try {
        showLoadingCards('album-manual-results', 'Searching albums...');

        const response = await fetch('/api/match/search', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                query: query,
                context: 'album',
                artist_id: currentMatchingData.selectedArtist.id
            })
        });

        const data = await response.json();
        if (data.results) {
            renderAlbumSearchResults(data.results);
        } else {
            showNoResultsMessage('album-manual-results', 'No albums found');
        }
    } catch (error) {
        console.error('Error searching albums:', error);
        showNoResultsMessage('album-manual-results', 'Error searching albums');
    }
}

function renderArtistSearchResults(results) {
    const container = document.getElementById('artist-manual-results');
    container.innerHTML = '';

    results.forEach((result, index) => {
        console.log(`Manual search result ${index}:`, result);
        console.log(`  result.artist:`, result.artist);
        console.log(`  result.confidence:`, result.confidence);
        try {
            const card = createArtistCard(result.artist, result.confidence);
            console.log(`createArtistCard returned:`, card, typeof card, card instanceof Element);
            if (card && card instanceof Element) {
                container.appendChild(card);
            } else {
                console.error(`Invalid card returned for result ${index}:`, card);
            }
        } catch (error) {
            console.error(`Error calling createArtistCard for result ${index}:`, error);
        }
    });

    // Lazy load missing artist images
    console.log('🖼️ Starting lazy load for artist images in matching modal...');
    if (typeof lazyLoadArtistImages === 'function') {
        lazyLoadArtistImages(container);
    } else if (typeof window.lazyLoadArtistImages === 'function') {
        window.lazyLoadArtistImages(container);
    } else {
        console.error('❌ lazyLoadArtistImages function not found!');
    }
}

function renderAlbumSearchResults(results) {
    const container = document.getElementById('album-manual-results');
    container.innerHTML = '';

    results.forEach(result => {
        const card = createAlbumCard(result.album, result.confidence);
        container.appendChild(card);
    });
}

function showLoadingCards(containerId, message) {
    const container = document.getElementById(containerId);
    container.innerHTML = `<div class="loading-card">${message}</div>`;
}

function showNoResultsMessage(containerId, message) {
    const container = document.getElementById(containerId);
    container.innerHTML = `<div class="loading-card" style="color: rgba(255,255,255,0.5)">${message}</div>`;
}

function skipMatching() {
    console.log('🎯 Skipping matching, proceeding with normal download');

    // Close modal
    closeMatchingModal();

    // Start normal download
    if (currentMatchingData.isAlbumDownload) {
        // For albums, we need to download each track
        showToast('⬇️ Starting album download (unmatched)', 'info');
        // This would need to be implemented to download all album tracks
    } else {
        // Single track download
        startDownload(window.currentSearchResults.indexOf(currentMatchingData.searchResult));
    }
}

function matchSlskdTracksToSpotify(slskdTracks, spotifyTracks) {
    /**
     * Matches Soulseek tracks to Spotify tracks based on filename analysis.
     * Returns enhanced tracks with full Spotify metadata.
     */
    console.log(`🎯 Starting track matching: ${slskdTracks.length} Soulseek tracks vs ${spotifyTracks.length} Spotify tracks`);

    const matched = [];
    const unmatched = [];

    for (const slskdTrack of slskdTracks) {
        const filename = slskdTrack.filename || slskdTrack.title || '';
        const parsedMeta = parseTrackFilename(filename);

        console.log(`🔍 Matching: "${filename}" -> parsed as: "${parsedMeta.title}" (track #${parsedMeta.trackNumber})`);

        // Find best matching Spotify track
        let bestMatch = null;
        let bestScore = 0;

        for (const spotifyTrack of spotifyTracks) {
            let score = 0;

            // Match by track number (highest priority if available)
            if (parsedMeta.trackNumber && spotifyTrack.track_number === parsedMeta.trackNumber) {
                score += 50;
                console.log(`   ✓ Track number match: ${parsedMeta.trackNumber} == ${spotifyTrack.track_number} (+50)`);
            }

            // Match by title similarity
            const titleScore = calculateStringSimilarity(
                parsedMeta.title.toLowerCase(),
                spotifyTrack.name.toLowerCase()
            );
            score += titleScore * 50; // Max 50 points for perfect title match

            console.log(`   Spotify track "${spotifyTrack.name}" (${spotifyTrack.track_number}): score ${score.toFixed(2)}`);

            if (score > bestScore) {
                bestScore = score;
                bestMatch = spotifyTrack;
            }
        }

        // Accept match if score is above threshold (70/100)
        if (bestMatch && bestScore >= 70) {
            console.log(`✅ MATCHED: "${filename}" -> "${bestMatch.name}" (score: ${bestScore.toFixed(2)})`);
            matched.push({
                slskd_track: slskdTrack,
                spotify_track: bestMatch,
                confidence: bestScore / 100
            });
        } else {
            console.log(`❌ NO MATCH: "${filename}" (best score: ${bestScore.toFixed(2)})`);
            unmatched.push(slskdTrack);
        }
    }

    console.log(`🎯 Matching complete: ${matched.length} matched, ${unmatched.length} unmatched`);

    return {
        matched: matched,
        unmatched: unmatched,
        total: slskdTracks.length
    };
}

function parseTrackFilename(filename) {
    /**
     * Parse track metadata from filename.
     * Handles common patterns like:
     * - "01 - Title.flac"
     * - "01. Title.flac"
     * - "Artist - Title.flac"
     * - "Title.flac"
     * - YouTube: "video_id||title" (extract title part)
     */
    // YouTube special handling: Extract title from encoded format
    if (filename && filename.includes('||')) {
        const parts = filename.split('||');
        const youtubeTitle = parts[1] || parts[0];  // Use title part, fallback to video_id
        // Remove common YouTube suffixes
        const cleanTitle = youtubeTitle
            .replace(/\s*\[.*?\]\s*/g, '')  // Remove [Official Video], [Lyrics], etc.
            .replace(/\s*\(.*?\)\s*/g, '')  // Remove (Official), (Audio), etc.
            .trim();
        return { title: cleanTitle, trackNumber: null };
    }

    // Remove file extension and path
    let basename = filename.split('/').pop().split('\\').pop();
    basename = basename.replace(/\.(flac|mp3|m4a|ogg|wav)$/i, '');

    let trackNumber = null;
    let title = basename;

    // Pattern 1: "01 - Title" or "01. Title"
    const pattern1 = /^(\d{1,2})\s*[-\.]\s*(.+)$/;
    const match1 = basename.match(pattern1);
    if (match1) {
        trackNumber = parseInt(match1[1]);
        title = match1[2].trim();
        return { title, trackNumber };
    }

    // Pattern 2: "Artist - Title" (extract title only)
    const pattern2 = /^.+?\s*[-–]\s*(.+)$/;
    const match2 = basename.match(pattern2);
    if (match2) {
        title = match2[1].trim();
        return { title, trackNumber };
    }

    // Fallback: use whole basename as title
    return { title: basename.trim(), trackNumber };
}

function calculateStringSimilarity(str1, str2) {
    /**
     * Calculate similarity between two strings (0-1 range).
     * Uses Levenshtein distance for fuzzy matching.
     */
    // Normalize strings
    str1 = str1.trim().toLowerCase();
    str2 = str2.trim().toLowerCase();

    if (str1 === str2) return 1.0;

    // Simple contains check
    if (str1.includes(str2) || str2.includes(str1)) {
        return 0.9;
    }

    // Levenshtein distance calculation
    const matrix = [];
    const len1 = str1.length;
    const len2 = str2.length;

    for (let i = 0; i <= len1; i++) {
        matrix[i] = [i];
    }

    for (let j = 0; j <= len2; j++) {
        matrix[0][j] = j;
    }

    for (let i = 1; i <= len1; i++) {
        for (let j = 1; j <= len2; j++) {
            const cost = str1[i - 1] === str2[j - 1] ? 0 : 1;
            matrix[i][j] = Math.min(
                matrix[i - 1][j] + 1,      // deletion
                matrix[i][j - 1] + 1,      // insertion
                matrix[i - 1][j - 1] + cost // substitution
            );
        }
    }

    const maxLen = Math.max(len1, len2);
    const distance = matrix[len1][len2];
    const similarity = 1 - (distance / maxLen);

    return Math.max(0, similarity);
}

async function confirmMatch() {
    if (!currentMatchingData.selectedArtist) {
        showToast('⚠️ Please select an artist first', 'error');
        return;
    }

    if (currentMatchingData.isAlbumDownload && !currentMatchingData.selectedAlbum) {
        showToast('⚠️ Please select an album first', 'error');
        return;
    }

    const confirmBtn = document.getElementById('confirm-match-btn');
    const originalText = confirmBtn.textContent;

    try {
        console.log('🎯 Confirming match with:', {
            artist: currentMatchingData.selectedArtist.name,
            album: currentMatchingData.selectedAlbum?.name
        });

        confirmBtn.disabled = true;
        confirmBtn.textContent = 'Starting...';

        // Determine the correct data to send
        const downloadPayload = currentMatchingData.isAlbumDownload
            ? currentMatchingData.albumResult
            : currentMatchingData.searchResult;

        // --- NEW: For album downloads, fetch Spotify tracklist and match tracks ---
        if (currentMatchingData.isAlbumDownload && currentMatchingData.selectedAlbum) {
            confirmBtn.textContent = 'Matching tracks...';
            console.log('🎵 Fetching Spotify tracklist for album:', currentMatchingData.selectedAlbum.name);

            try {
                // Fetch album tracks (pass name/artist for Hydrabase support)
                const artistId = currentMatchingData.selectedArtist.id;
                const albumId = currentMatchingData.selectedAlbum.id;
                const _aat3 = new URLSearchParams({ name: currentMatchingData.selectedAlbum.name || '', artist: currentMatchingData.selectedArtist.name || '' });
                const albumSource = currentMatchingData.selectedAlbum?.source || currentMatchingData.selectedArtist?.source || null;
                if (albumSource) {
                    _aat3.set('source', albumSource);
                }
                const tracksResponse = await fetch(`/api/album/${albumId}/tracks?${_aat3}`);

                if (!tracksResponse.ok) {
                    throw new Error(`Failed to fetch Spotify tracks: ${tracksResponse.status}`);
                }

                const tracksData = await tracksResponse.json();
                const spotifyTracks = tracksData.tracks || [];

                console.log(`✅ Fetched ${spotifyTracks.length} Spotify tracks for matching`);

                // Match each Soulseek track to a Spotify track
                const enhancedTracks = matchSlskdTracksToSpotify(
                    downloadPayload.tracks || [],
                    spotifyTracks
                );

                console.log(`🎯 Matched ${enhancedTracks.matched.length}/${enhancedTracks.total} tracks to Spotify`);

                // Send enhanced data with full Spotify track objects
                confirmBtn.textContent = 'Downloading...';
                const response = await fetch('/api/download/matched', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        search_result: downloadPayload,
                        spotify_artist: currentMatchingData.selectedArtist,
                        spotify_album: currentMatchingData.selectedAlbum,
                        enhanced_tracks: enhancedTracks.matched, // Send matched tracks with full Spotify data
                        unmatched_tracks: enhancedTracks.unmatched // Send unmatched tracks for basic processing
                    })
                });

                const data = await response.json();

                if (data.success) {
                    showToast(`🎯 Matched ${enhancedTracks.matched.length} tracks to Spotify`, 'success');
                    closeMatchingModal();
                } else {
                    throw new Error(data.error || 'Failed to start matched download');
                }

            } catch (trackMatchError) {
                console.error('❌ Track matching failed, falling back to simple matching:', trackMatchError);
                showToast('⚠️ Track matching failed, using basic matching', 'warning');

                // Fallback to simple matching (current behavior)
                const response = await fetch('/api/download/matched', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        search_result: downloadPayload,
                        spotify_artist: currentMatchingData.selectedArtist,
                        spotify_album: currentMatchingData.selectedAlbum || null
                    })
                });

                const data = await response.json();

                if (data.success) {
                    showToast(`🎯 Matched download started for "${currentMatchingData.selectedArtist.name}"`, 'success');
                    closeMatchingModal();
                } else {
                    throw new Error(data.error || 'Failed to start matched download');
                }
            }
        } else {
            // Single track download - fetch release data for full details
            confirmBtn.textContent = 'Searching release data...';

            try {
                // Parse track name from Soulseek filename
                const filename = downloadPayload.filename || downloadPayload.title || '';
                const parsedMeta = parseTrackFilename(filename);

                console.log(`🔍 Searching release data for: "${parsedMeta.title}" by ${currentMatchingData.selectedArtist.name}`);

                // Search the configured provider for this track
                const searchQuery = `track:${parsedMeta.title} artist:${currentMatchingData.selectedArtist.name}`;
                const searchResponse = await fetch(`/api/spotify/search?q=${encodeURIComponent(searchQuery)}&type=track&limit=5`);

                if (!searchResponse.ok) {
                    throw new Error('Failed to search Spotify for track');
                }

                const searchData = await searchResponse.json();
                const spotifyTracks = searchData.tracks?.items || [];

                if (spotifyTracks.length === 0) {
                    throw new Error('No Spotify tracks found for this search');
                }

                // Find best match (prefer exact artist match)
                let bestMatch = spotifyTracks.find(track =>
                    track.artists.some(artist => artist.id === currentMatchingData.selectedArtist.id)
                ) || spotifyTracks[0];

                console.log(`✅ Found Spotify track: "${bestMatch.name}" (${bestMatch.id})`);

                // Get full track details with album info
                const trackResponse = await fetch(`/api/spotify/track/${bestMatch.id}`);
                if (!trackResponse.ok) {
                    throw new Error('Failed to fetch Spotify track details');
                }

                const fullTrack = await trackResponse.json();

                // Send with full Spotify metadata (single track enhanced)
                confirmBtn.textContent = 'Downloading...';
                const response = await fetch('/api/download/matched', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        search_result: downloadPayload,
                        spotify_artist: currentMatchingData.selectedArtist,
                        spotify_album: null,  // Singles don't have album context
                        spotify_track: fullTrack,  // Full Spotify track object
                        is_single_track: true  // Flag for single track processing
                    })
                });

                const data = await response.json();

                if (data.success) {
                    showToast(`🎯 Matched single: "${fullTrack.name}"`, 'success');
                    closeMatchingModal();
                } else {
                    throw new Error(data.error || 'Failed to start matched download');
                }

            } catch (singleMatchError) {
                console.error('❌ Release matching failed, falling back to basic:', singleMatchError);
                showToast('⚠️ Release matching failed, using basic track data', 'warning');

                // Fallback to basic matching (current behavior)
                const response = await fetch('/api/download/matched', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        search_result: downloadPayload,
                        spotify_artist: currentMatchingData.selectedArtist,
                        spotify_album: currentMatchingData.selectedAlbum || null
                    })
                });

                const data = await response.json();

                if (data.success) {
                    showToast(`🎯 Matched download started for "${currentMatchingData.selectedArtist.name}"`, 'success');
                    closeMatchingModal();
                } else {
                    throw new Error(data.error || 'Failed to start matched download');
                }
            }
        }

    } catch (error) {
        console.error('Error starting matched download:', error);
        showToast(`❌ Error starting matched download: ${error.message}`, 'error');

        // Re-enable confirm button on failure
        confirmBtn.disabled = false;
        confirmBtn.textContent = originalText;
    }
}




function matchedDownloadTrack(trackIndex) {
    const results = window.currentSearchResults;
    if (!results || !results[trackIndex]) {
        console.error('Could not find track for matched download:', trackIndex);
        showToast('Error preparing matched download.', 'error');
        return;
    }
    const trackData = results[trackIndex];
    // It's a single track, so isAlbumDownload is false and there's no album context.
    openMatchingModal(trackData, false, null);
}

function matchedDownloadAlbum(albumIndex) {
    const results = window.currentSearchResults;
    if (!results || !results[albumIndex]) {
        console.error('Could not find album for matched download:', albumIndex);
        showToast('Error preparing matched download.', 'error');
        return;
    }
    const albumData = results[albumIndex];
    // The first track is used as a reference for the initial artist search.
    const firstTrack = albumData.tracks ? albumData.tracks[0] : albumData;
    openMatchingModal(firstTrack, true, albumData);
}

function matchedDownloadAlbumTrack(albumIndex, trackIndex) {
    const results = window.currentSearchResults;
    if (!results || !results[albumIndex] || !results[albumIndex].tracks || !results[albumIndex].tracks[trackIndex]) {
        console.error('Could not find album track for matched download:', albumIndex, trackIndex);
        showToast('Error preparing matched download.', 'error');
        return;
    }
    const albumData = results[albumIndex];
    const trackData = albumData.tracks[trackIndex];

    // This is the definitive fix.
    // The second argument MUST be 'false' to treat this as a single track download,
    // which prevents the modal from asking for an album selection.
    openMatchingModal(trackData, false, albumData);
}

// ===========================================
// == DASHBOARD DATABASE UPDATER FUNCTIONALITY ==
// ===========================================

// --- State and Polling Management ---

function stopDbStatsPolling() {
    if (dbStatsInterval) {
        clearInterval(dbStatsInterval);
        dbStatsInterval = null;
    }
}

function stopDbUpdatePolling() {
    if (dbUpdateStatusInterval) {
        console.log('⏹️ Stopping database update polling');
        clearInterval(dbUpdateStatusInterval);
        dbUpdateStatusInterval = null;
    }
}

// ===================================================================
// QUALITY SCANNER TOOL
// ===================================================================

async function handleQualityScanButtonClick() {
    const button = document.getElementById('quality-scan-button');
    const currentAction = button.textContent;

    if (currentAction === 'Scan Library') {
        const scopeSelect = document.getElementById('quality-scan-scope');
        const scope = scopeSelect.value;

        try {
            button.disabled = true;
            button.textContent = 'Starting...';
            const response = await fetch('/api/quality-scanner/start', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ scope: scope })
            });

            if (response.ok) {
                showToast('Quality scan started!', 'success');
                // Start polling immediately to get live status
                checkAndUpdateQualityScanProgress();
            } else {
                const errorData = await response.json();
                showToast(`Error: ${errorData.error}`, 'error');
                button.disabled = false;
                button.textContent = 'Scan Library';
            }
        } catch (error) {
            showToast('Failed to start quality scan.', 'error');
            button.disabled = false;
            button.textContent = 'Scan Library';
        }

    } else { // "Stop Scan"
        try {
            const response = await fetch('/api/quality-scanner/stop', { method: 'POST' });
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

async function checkAndUpdateQualityScanProgress() {
    if (socketConnected) return; // WebSocket handles this
    try {
        const response = await fetch('/api/quality-scanner/status', {
            signal: AbortSignal.timeout(10000) // 10 second timeout
        });
        if (!response.ok) return;

        const state = await response.json();
        console.debug('🔍 Quality Scanner Status:', state.status, `${state.processed}/${state.total}`, `${state.progress.toFixed(1)}%`);
        updateQualityScanProgressUI(state);

        // Start polling only if not already polling and status is running
        if (state.status === 'running' && !qualityScannerStatusInterval) {
            console.log('🔄 Starting quality scanner polling (1 second interval)');
            qualityScannerStatusInterval = setInterval(checkAndUpdateQualityScanProgress, 1000);
        }

    } catch (error) {
        console.warn('Could not fetch quality scanner status:', error);
        // Don't stop polling on network errors - keep trying
    }
}

function updateQualityScanProgressFromData(data) {
    const prev = _lastToolStatus['quality-scanner'];
    _lastToolStatus['quality-scanner'] = data.status;
    if (prev !== undefined && data.status === prev && data.status !== 'running') return;
    updateQualityScanProgressUI(data);
}

function updateQualityScanProgressUI(state) {
    const button = document.getElementById('quality-scan-button');
    const phaseLabel = document.getElementById('quality-phase-label');
    const progressLabel = document.getElementById('quality-progress-label');
    const progressBar = document.getElementById('quality-progress-bar');
    const scopeSelect = document.getElementById('quality-scan-scope');

    // Stats
    const processedStat = document.getElementById('quality-stat-processed');
    const metStat = document.getElementById('quality-stat-met');
    const lowStat = document.getElementById('quality-stat-low');
    const matchedStat = document.getElementById('quality-stat-matched');

    if (!button || !phaseLabel || !progressLabel || !progressBar || !scopeSelect) return;

    // Update stats
    if (processedStat) processedStat.textContent = state.processed || 0;
    if (metStat) metStat.textContent = state.quality_met || 0;
    if (lowStat) lowStat.textContent = state.low_quality || 0;
    if (matchedStat) matchedStat.textContent = state.matched || 0;

    if (state.status === 'running') {
        button.textContent = 'Stop Scan';
        button.disabled = false;
        scopeSelect.disabled = true;

        phaseLabel.textContent = state.phase || 'Scanning...';
        progressLabel.textContent = `${state.processed} / ${state.total} tracks scanned (${state.progress.toFixed(1)}%)`;
        progressBar.style.width = `${state.progress}%`;
    } else { // idle, finished, or error
        stopQualityScannerPolling();
        button.textContent = 'Scan Library';
        button.disabled = false;
        scopeSelect.disabled = false;

        if (state.status === 'error') {
            phaseLabel.textContent = `Error: ${state.error_message}`;
            progressBar.style.backgroundColor = '#ff4444'; // Red for error
        } else {
            phaseLabel.textContent = state.phase || 'Ready to scan';
            progressBar.style.backgroundColor = 'rgb(var(--accent-rgb))'; // Green for normal
        }

        if (state.status === 'finished') {
            // Show completion toast with results
            showToast(`Scan complete! ${state.matched} tracks added to wishlist`, 'success');
        }
    }
}

function stopQualityScannerPolling() {
    if (qualityScannerStatusInterval) {
        console.log('⏹️ Stopping quality scanner polling');
        clearInterval(qualityScannerStatusInterval);
        qualityScannerStatusInterval = null;
    }
}

// ============================================
// == DUPLICATE CLEANER FUNCTIONS            ==
// ============================================

async function handleDuplicateCleanButtonClick() {
    const button = document.getElementById('duplicate-clean-button');
    const currentAction = button.textContent;

    if (currentAction === 'Clean Duplicates') {
        try {
            button.disabled = true;
            button.textContent = 'Starting...';
            const response = await fetch('/api/duplicate-cleaner/start', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' }
            });

            if (response.ok) {
                showToast('Duplicate cleaner started!', 'success');
                // Start polling immediately to get live status
                checkAndUpdateDuplicateCleanProgress();
            } else {
                const errorData = await response.json();
                showToast(`Error: ${errorData.error}`, 'error');
                button.disabled = false;
                button.textContent = 'Clean Duplicates';
            }
        } catch (error) {
            showToast('Failed to start duplicate cleaner.', 'error');
            button.disabled = false;
            button.textContent = 'Clean Duplicates';
        }

    } else { // "Stop Cleaning"
        try {
            const response = await fetch('/api/duplicate-cleaner/stop', { method: 'POST' });
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

async function checkAndUpdateDuplicateCleanProgress() {
    if (socketConnected) return; // WebSocket handles this
    try {
        const response = await fetch('/api/duplicate-cleaner/status', {
            signal: AbortSignal.timeout(10000) // 10 second timeout
        });
        if (!response.ok) return;

        const state = await response.json();
        console.debug('🧹 Duplicate Cleaner Status:', state.status, `${state.files_scanned}/${state.total_files}`, `${state.progress.toFixed(1)}%`);
        updateDuplicateCleanProgressUI(state);

        // Start polling only if not already polling and status is running
        if (state.status === 'running' && !duplicateCleanerStatusInterval) {
            console.log('🔄 Starting duplicate cleaner polling (1 second interval)');
            duplicateCleanerStatusInterval = setInterval(checkAndUpdateDuplicateCleanProgress, 1000);
        }

    } catch (error) {
        console.warn('Could not fetch duplicate cleaner status:', error);
        // Don't stop polling on network errors - keep trying
    }
}

function updateDuplicateCleanProgressFromData(data) {
    const prev = _lastToolStatus['duplicate-cleaner'];
    _lastToolStatus['duplicate-cleaner'] = data.status;
    if (prev !== undefined && data.status === prev && data.status !== 'running') return;
    updateDuplicateCleanProgressUI(data);
}

function updateDuplicateCleanProgressUI(state) {
    const button = document.getElementById('duplicate-clean-button');
    const phaseLabel = document.getElementById('duplicate-phase-label');
    const progressLabel = document.getElementById('duplicate-progress-label');
    const progressBar = document.getElementById('duplicate-progress-bar');

    // Stats
    const scannedStat = document.getElementById('duplicate-stat-scanned');
    const foundStat = document.getElementById('duplicate-stat-found');
    const deletedStat = document.getElementById('duplicate-stat-deleted');
    const spaceStat = document.getElementById('duplicate-stat-space');

    if (!button || !phaseLabel || !progressLabel || !progressBar) return;

    // Update stats
    if (scannedStat) scannedStat.textContent = state.files_scanned || 0;
    if (foundStat) foundStat.textContent = state.duplicates_found || 0;
    if (deletedStat) deletedStat.textContent = state.deleted || 0;
    if (spaceStat) {
        const spaceMB = state.space_freed_mb || 0;
        if (spaceMB >= 1024) {
            spaceStat.textContent = `${(spaceMB / 1024).toFixed(2)} GB`;
        } else {
            spaceStat.textContent = `${spaceMB.toFixed(2)} MB`;
        }
    }

    if (state.status === 'running') {
        button.textContent = 'Stop Cleaning';
        button.disabled = false;

        phaseLabel.textContent = state.phase || 'Scanning...';
        progressLabel.textContent = `${state.files_scanned} / ${state.total_files} files scanned (${state.progress.toFixed(1)}%)`;
        progressBar.style.width = `${state.progress}%`;
    } else { // idle, finished, or error
        stopDuplicateCleanerPolling();
        button.textContent = 'Clean Duplicates';
        button.disabled = false;

        if (state.status === 'error') {
            phaseLabel.textContent = `Error: ${state.error_message}`;
            progressBar.style.backgroundColor = '#ff4444'; // Red for error
        } else {
            phaseLabel.textContent = state.phase || 'Ready to scan';
            progressBar.style.backgroundColor = 'rgb(var(--accent-rgb))'; // Green for normal
        }

        if (state.status === 'finished') {
            // Show completion toast with results
            const spaceMB = state.space_freed_mb || 0;
            const spaceDisplay = spaceMB >= 1024 ? `${(spaceMB / 1024).toFixed(2)} GB` : `${spaceMB.toFixed(1)} MB`;
            showToast(`Cleaning complete! ${state.deleted} files removed, ${spaceDisplay} freed`, 'success');
        }
    }
}

function stopDuplicateCleanerPolling() {
    if (duplicateCleanerStatusInterval) {
        console.log('⏹️ Stopping duplicate cleaner polling');
        clearInterval(duplicateCleanerStatusInterval);
        duplicateCleanerStatusInterval = null;
    }
}

// ============================================
// == BACKUP MANAGER                         ==
// ============================================

async function loadBackupList() {
    try {
        const res = await fetch('/api/database/backups');
        const data = await res.json();
        if (data.success) {
            updateBackupManagerUI(data);
            renderBackupList(data.backups);
        }
    } catch (e) {
        console.error('Failed to load backup list:', e);
    }
}

function updateBackupManagerUI(data) {
    const lastEl = document.getElementById('backup-stat-last');
    const countEl = document.getElementById('backup-stat-count');
    const latestSizeEl = document.getElementById('backup-stat-latest-size');
    const dbSizeEl = document.getElementById('backup-stat-db-size');

    if (countEl) countEl.textContent = data.count;
    if (dbSizeEl) dbSizeEl.textContent = data.db_size_mb + ' MB';

    if (data.backups && data.backups.length > 0) {
        const newest = data.backups[0];
        if (lastEl) lastEl.textContent = timeAgo(newest.created);
        if (latestSizeEl) latestSizeEl.textContent = newest.size_mb + ' MB';
    } else {
        if (lastEl) lastEl.textContent = 'Never';
        if (latestSizeEl) latestSizeEl.textContent = '—';
    }
}

function renderBackupList(backups) {
    const container = document.getElementById('backup-list-container');
    if (!container) return;
    if (!backups || backups.length === 0) {
        container.innerHTML = '';
        return;
    }

    container.innerHTML = backups.map(b => {
        const date = new Date(b.created + (b.created.includes('Z') ? '' : 'Z'));
        const dateStr = date.toLocaleDateString(undefined, { month: 'short', day: 'numeric', year: 'numeric' })
            + ' ' + date.toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit' });
        const safeName = escapeForInlineJs(b.filename);
        const versionBadge = b.version ? `<span class="backup-list-version">v${escapeHtml(b.version)}</span>` : '';
        return `<div class="backup-list-item">
            <div class="backup-list-info">
                <span class="backup-list-date">${escapeHtml(dateStr)}</span>
                <span class="backup-list-size">${b.size_mb} MB</span>
                ${versionBadge}
            </div>
            <div class="backup-list-actions">
                <button class="backup-dl-btn" onclick="downloadBackup('${safeName}')" title="Download">DL</button>
                <button class="backup-restore-btn" onclick="restoreBackup('${safeName}')" title="Restore">Restore</button>
                <button class="backup-delete-btn" onclick="deleteBackup('${safeName}')" title="Delete">Del</button>
            </div>
        </div>`;
    }).join('');
}

async function handleBackupNowClick() {
    const button = document.getElementById('backup-now-button');
    if (!button) return;
    const origText = button.textContent;
    button.disabled = true;
    button.textContent = 'Backing up...';
    try {
        const res = await fetch('/api/database/backup', { method: 'POST' });
        const data = await res.json();
        if (data.success) {
            showToast(`Database backed up (${data.size_mb} MB)`, 'success');
            await loadBackupList();
        } else {
            showToast(`Backup failed: ${data.error}`, 'error');
        }
    } catch (e) {
        showToast('Backup request failed', 'error');
    }
    button.disabled = false;
    button.textContent = origText;
}

function downloadBackup(filename) {
    const a = document.createElement('a');
    a.href = `/api/database/backups/${encodeURIComponent(filename)}/download`;
    a.download = filename;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
}

async function restoreBackup(filename, force = false) {
    if (!force) {
        if (!await showConfirmDialog({ title: 'Restore Backup', message: `Restore database from "${filename}"?\n\nA safety backup of the current database will be created first.`, confirmText: 'Restore' })) return;
    }
    try {
        const fetchOpts = { method: 'POST' };
        if (force) {
            fetchOpts.headers = { 'Content-Type': 'application/json' };
            fetchOpts.body = JSON.stringify({ force: true });
        }
        const res = await fetch(`/api/database/backups/${encodeURIComponent(filename)}/restore`, fetchOpts);
        const data = await res.json();
        if (data.success) {
            let msg = `Database restored from ${data.restored_from} (${data.artist_count} artists). Safety backup: ${data.safety_backup}`;
            if (data.version_warning) msg += `\n⚠️ ${data.version_warning}`;
            showToast(msg, 'success');
            await loadBackupList();
        } else if (data.version_mismatch) {
            // Version mismatch — ask user to confirm
            const confirmed = await showConfirmDialog({
                title: 'Version Mismatch',
                message: `This backup was created on SoulSync v${data.backup_version}, but you're running v${data.current_version}.\n\nRestoring an older backup may cause issues if the database schema has changed. A safety backup will be created first.\n\nProceed anyway?`,
                confirmText: 'Restore Anyway',
                destructive: true
            });
            if (confirmed) {
                await restoreBackup(filename, true);
            }
        } else {
            showToast(`Restore failed: ${data.error}`, 'error');
        }
    } catch (e) {
        showToast('Restore request failed', 'error');
    }
}

async function deleteBackup(filename) {
    if (!await showConfirmDialog({ title: 'Delete Backup', message: `Delete backup "${filename}"? This cannot be undone.`, confirmText: 'Delete', destructive: true })) return;
    try {
        const res = await fetch(`/api/database/backups/${encodeURIComponent(filename)}`, { method: 'DELETE' });
        const data = await res.json();
        if (data.success) {
            showToast(`Backup deleted: ${data.deleted}`, 'success');
            await loadBackupList();
        } else {
            showToast(`Delete failed: ${data.error}`, 'error');
        }
    } catch (e) {
        showToast('Delete request failed', 'error');
    }
}

// ============================================
// == METADATA CACHE                         ==
// ============================================

async function loadMetadataCacheStats() {
    try {
        const response = await fetch('/api/metadata-cache/stats');
        if (!response.ok) return;
        const stats = await response.json();

        const artistsEl = document.getElementById('mcache-stat-artists');
        const albumsEl = document.getElementById('mcache-stat-albums');
        const tracksEl = document.getElementById('mcache-stat-tracks');
        const hitsEl = document.getElementById('mcache-stat-hits');

        if (artistsEl) artistsEl.textContent = (stats.artists?.spotify || 0) + (stats.artists?.itunes || 0) + (stats.artists?.deezer || 0) + (stats.artists?.beatport || 0);
        if (albumsEl) albumsEl.textContent = (stats.albums?.spotify || 0) + (stats.albums?.itunes || 0) + (stats.albums?.deezer || 0) + (stats.albums?.beatport || 0);
        if (tracksEl) tracksEl.textContent = (stats.tracks?.spotify || 0) + (stats.tracks?.itunes || 0) + (stats.tracks?.deezer || 0) + (stats.tracks?.beatport || 0);
        if (hitsEl) hitsEl.textContent = stats.total_hits || 0;
    } catch (e) {
        // Silently fail — cache may not be initialized yet
    }
}

// ── Library History Modal ────────────────────────────────────────────
let _libraryHistoryState = { tab: 'download', page: 1, limit: 50 };

function openLibraryHistoryModal() {
    const overlay = document.getElementById('library-history-overlay');
    if (overlay) {
        overlay.classList.remove('hidden');
        _libraryHistoryState.page = 1;
        loadLibraryHistory();
    }
}

function closeLibraryHistoryModal() {
    const overlay = document.getElementById('library-history-overlay');
    if (overlay) overlay.classList.add('hidden');
}

function switchHistoryTab(tab) {
    _libraryHistoryState.tab = tab;
    _libraryHistoryState.page = 1;
    document.querySelectorAll('.library-history-tab').forEach(t => {
        t.classList.toggle('active', t.dataset.tab === tab);
    });
    loadLibraryHistory();
}

async function loadLibraryHistory() {
    const { tab, page, limit } = _libraryHistoryState;
    const list = document.getElementById('library-history-list');
    const pagination = document.getElementById('library-history-pagination');
    if (!list) return;
    list.innerHTML = '<div class="library-history-loading">Loading...</div>';
    if (pagination) pagination.innerHTML = '';

    try {
        const resp = await fetch(`/api/library/history?type=${tab}&page=${page}&limit=${limit}`);
        const data = await resp.json();

        // Update tab counts
        const dlCount = document.getElementById('history-download-count');
        const imCount = document.getElementById('history-import-count');
        if (dlCount) dlCount.textContent = data.stats?.downloads || 0;
        if (imCount) imCount.textContent = data.stats?.imports || 0;

        // Source breakdown bar (downloads tab only)
        const sourceBar = document.getElementById('history-source-bar');
        if (sourceBar) {
            const sc = data.stats?.source_counts || {};
            const srcEntries = Object.entries(sc).sort((a, b) => b[1] - a[1]);
            if (srcEntries.length > 0 && tab === 'download') {
                const _srcColors = { Soulseek: '#4caf50', Tidal: '#000', YouTube: '#ff0000', Qobuz: '#4285f4', HiFi: '#00bcd4', Deezer: '#a238ff' };
                sourceBar.innerHTML = srcEntries.map(([src, cnt]) =>
                    `<span class="history-source-chip" style="border-color:${_srcColors[src] || '#888'};color:${_srcColors[src] || '#888'}">${src}: ${cnt}</span>`
                ).join('');
                sourceBar.style.display = '';
            } else {
                sourceBar.style.display = 'none';
            }
        }

        if (!data.entries || data.entries.length === 0) {
            const emptyIcon = tab === 'download' ? '📥' : '📚';
            const emptyText = tab === 'download'
                ? 'No downloads recorded yet. Completed downloads will appear here.'
                : 'No server imports recorded yet. New tracks from library scans will appear here.';
            list.innerHTML = `<div class="library-history-empty">${emptyIcon}<br><br>${emptyText}</div>`;
            return;
        }

        list.innerHTML = data.entries.map(renderHistoryEntry).join('');
        renderHistoryPagination(data.total, page, limit);
    } catch (err) {
        console.error('Error loading library history:', err);
        list.innerHTML = '<div class="library-history-empty">Error loading history</div>';
    }
}

function renderHistoryEntry(entry) {
    // Server import thumb_urls are relative paths (e.g. /library/metadata/...) — use placeholder
    const hasValidThumb = entry.thumb_url && (entry.thumb_url.startsWith('http://') || entry.thumb_url.startsWith('https://'));
    const thumb = hasValidThumb
        ? `<img src="${escapeHtml(entry.thumb_url)}" class="library-history-thumb" loading="lazy" onerror="this.outerHTML='<div class=\\'library-history-thumb-placeholder\\'>${entry.event_type === 'download' ? '📥' : '📚'}</div>'">`
        : `<div class="library-history-thumb-placeholder">${entry.event_type === 'download' ? '📥' : '📚'}</div>`;

    let badge = '';
    if (entry.event_type === 'download') {
        const parts = [];
        if (entry.download_source) parts.push(entry.download_source);
        if (entry.quality) parts.push(entry.quality);
        badge = parts.map(p => `<span class="library-history-badge download">${escapeHtml(p)}</span>`).join('');
    } else if (entry.event_type === 'import' && entry.server_source) {
        const sourceName = { plex: 'Plex', jellyfin: 'Jellyfin', navidrome: 'Navidrome' }[entry.server_source] || entry.server_source;
        badge = `<span class="library-history-badge import">${escapeHtml(sourceName)}</span>`;
    }

    // AcoustID badge
    let acoustidBadge = '';
    if (entry.acoustid_result) {
        const _aidColors = { pass: '#4caf50', fail: '#ef5350', skip: '#ff9800', disabled: '#666', error: '#ef5350' };
        const _aidLabels = { pass: 'Verified', fail: 'Failed', skip: 'Skipped', disabled: 'Off', error: 'Error' };
        const color = _aidColors[entry.acoustid_result] || '#666';
        const label = _aidLabels[entry.acoustid_result] || entry.acoustid_result;
        acoustidBadge = `<span class="library-history-badge" style="border-color:${color};color:${color}">AcoustID: ${label}</span>`;
    }

    const meta = [entry.artist_name, entry.album_name].filter(Boolean).join(' — ');

    // Source provenance — expected vs downloaded
    let sourceDetail = '';
    if (entry.event_type === 'download') {
        const lines = [];
        // Expected line (what we asked for)
        if (entry.title || entry.artist_name) {
            lines.push(`<span class="lh-prov-label">Expected:</span> ${escapeHtml(entry.title || '?')} <span class="lh-prov-dim">by</span> ${escapeHtml(entry.artist_name || '?')}`);
        }
        // Downloaded line (what the source provided)
        const srcTitle = entry.source_track_title || '';
        const srcArtist = entry.source_artist || '';
        if (srcTitle || srcArtist) {
            const isMismatch = (srcTitle && entry.title && srcTitle.toLowerCase() !== entry.title.toLowerCase())
                || (srcArtist && entry.artist_name && srcArtist.toLowerCase() !== entry.artist_name.toLowerCase());
            const mismatchClass = isMismatch ? ' lh-prov-mismatch' : '';
            lines.push(`<span class="lh-prov-label">Downloaded:</span> <span class="${mismatchClass}">${escapeHtml(srcTitle || '?')} <span class="lh-prov-dim">by</span> ${escapeHtml(srcArtist || '?')}</span>`);
        }
        // Source file + ID line
        if (entry.source_filename || entry.source_track_id) {
            const fileParts = [];
            if (entry.source_filename) fileParts.push(`<span class="lh-prov-label">File:</span> ${escapeHtml(entry.source_filename)}`);
            if (entry.source_track_id) fileParts.push(`<span class="lh-prov-label">${entry.source_filename ? '' : 'Source '}ID:</span> ${escapeHtml(entry.source_track_id)}`);
            lines.push(fileParts.join(` <span class="lh-prov-dim">·</span> `));
        }
        if (lines.length > 0) {
            sourceDetail = `<div class="library-history-entry-source">${lines.join('<br>')}</div>`;
        }
    }

    const hasDetails = sourceDetail || acoustidBadge;
    const expandIndicator = hasDetails ? `<span class="lh-expand-btn">&#x25BE;</span>` : '';

    return `<div class="library-history-entry${hasDetails ? ' lh-expandable' : ''}" ${hasDetails ? 'onclick="this.classList.toggle(\'lh-expanded\')"' : ''}>
        ${thumb}
        <div class="library-history-entry-content">
            <div class="library-history-entry-row1">
                <div class="library-history-entry-text">
                    <div class="library-history-entry-title">${escapeHtml(entry.title || 'Unknown')}</div>
                    <div class="library-history-entry-meta">${escapeHtml(meta)}</div>
                </div>
                <div class="library-history-entry-badges">${badge}</div>
                <div class="library-history-entry-time">${formatHistoryTime(entry.created_at)}</div>
                ${expandIndicator}
            </div>
            ${hasDetails ? `<div class="library-history-entry-details">
                ${sourceDetail}
                ${acoustidBadge ? `<div class="library-history-entry-badges" style="margin-top:4px">${acoustidBadge}</div>` : ''}
            </div>` : ''}
        </div>
    </div>`;
}

function formatHistoryTime(isoStr) {
    if (!isoStr) return '';
    try {
        // SQLite CURRENT_TIMESTAMP is UTC but lacks timezone marker — append Z
        let normalized = isoStr;
        if (/^\d{4}-\d{2}-\d{2} \d{2}:\d{2}/.test(normalized) && !normalized.includes('Z') && !normalized.includes('+')) {
            normalized = normalized.replace(' ', 'T') + 'Z';
        }
        const date = new Date(normalized);
        const now = new Date();
        const diffMs = now - date;
        const diffMins = Math.floor(diffMs / 60000);
        if (diffMins < 1) return 'Just now';
        if (diffMins < 60) return `${diffMins}m ago`;
        const diffHours = Math.floor(diffMins / 60);
        if (diffHours < 24) return `${diffHours}h ago`;
        const diffDays = Math.floor(diffHours / 24);
        if (diffDays < 7) return `${diffDays}d ago`;
        return date.toLocaleDateString(undefined, { month: 'short', day: 'numeric' });
    } catch { return ''; }
}

function renderHistoryPagination(total, page, limit) {
    const pagination = document.getElementById('library-history-pagination');
    if (!pagination) return;

    const totalPages = Math.ceil(total / limit);
    if (totalPages <= 1) { pagination.innerHTML = ''; return; }

    pagination.innerHTML = `
        <button class="library-history-page-btn" onclick="changeHistoryPage(${page - 1})" ${page <= 1 ? 'disabled' : ''}>Prev</button>
        <span class="library-history-page-info">Page ${page} of ${totalPages}</span>
        <button class="library-history-page-btn" onclick="changeHistoryPage(${page + 1})" ${page >= totalPages ? 'disabled' : ''}>Next</button>
    `;
}

function changeHistoryPage(newPage) {
    if (newPage < 1) return;
    _libraryHistoryState.page = newPage;
    loadLibraryHistory();
}

// ── Sync History Modal ──────────────────────────────────────────────
const _syncHistoryState = { source: null, page: 1, limit: 20 };

function openSyncHistoryModal() {
    const overlay = document.getElementById('sync-history-overlay');
    if (overlay) {
        overlay.classList.remove('hidden');
        _syncHistoryState.page = 1;
        _syncHistoryState.source = null;
        loadSyncHistory();
    }
}

function closeSyncHistoryModal() {
    const overlay = document.getElementById('sync-history-overlay');
    if (overlay) overlay.classList.add('hidden');
}

function switchSyncHistoryTab(source) {
    _syncHistoryState.source = source;
    _syncHistoryState.page = 1;
    document.querySelectorAll('.sync-history-tab').forEach(t => {
        t.classList.toggle('active', t.dataset.source === (source || 'all'));
    });
    loadSyncHistory();
}

async function loadSyncHistory() {
    const { source, page, limit } = _syncHistoryState;
    const list = document.getElementById('sync-history-list');
    const tabsContainer = document.getElementById('sync-history-tabs');
    if (!list) return;
    list.innerHTML = '<div class="sync-history-loading">Loading...</div>';

    try {
        const params = new URLSearchParams({ page, limit });
        if (source) params.set('source', source);
        const resp = await fetch(`/api/sync/history?${params}`);
        const data = await resp.json();

        // Build tabs from stats
        if (tabsContainer && data.stats) {
            const totalCount = Object.values(data.stats).reduce((a, b) => a + b, 0);
            const sourceLabels = {
                spotify: 'Spotify', beatport: 'Beatport', youtube: 'YouTube',
                tidal: 'Tidal', deezer: 'Deezer', wishlist: 'Wishlist',
                library: 'Library', discover: 'Discover', listenbrainz: 'ListenBrainz',
                spotify_public: 'Spotify Public', mirrored: 'Mirrored'
            };
            let tabsHtml = `<button class="sync-history-tab ${!source ? 'active' : ''}" data-source="all" onclick="switchSyncHistoryTab(null)">All <span class="sync-history-tab-count">${totalCount}</span></button>`;
            for (const [src, count] of Object.entries(data.stats).sort((a, b) => b[1] - a[1])) {
                const label = sourceLabels[src] || src;
                const isActive = source === src ? ' active' : '';
                tabsHtml += `<button class="sync-history-tab${isActive}" data-source="${src}" onclick="switchSyncHistoryTab('${src}')">${label} <span class="sync-history-tab-count">${count}</span></button>`;
            }
            tabsContainer.innerHTML = tabsHtml;
        }

        // Filter to only show playlist syncs — not album downloads, wishlist, or redownloads
        const syncEntries = (data.entries || []).filter(e => e.sync_type === 'playlist' || !e.sync_type);

        if (syncEntries.length === 0) {
            list.innerHTML = '<div class="sync-history-empty">No sync history yet. Completed syncs will appear here.</div>';
            return;
        }

        list.innerHTML = syncEntries.map(renderSyncHistoryEntry).join('');
        renderSyncHistoryPagination(data.total, page, limit);
    } catch (err) {
        console.error('Error loading sync history:', err);
        list.innerHTML = '<div class="sync-history-empty">Error loading sync history</div>';
    }
}

function renderSyncHistoryEntry(entry) {
    const thumb = entry.thumb_url
        ? `<img src="${escapeHtml(entry.thumb_url)}" class="sync-history-thumb" loading="lazy" onerror="this.outerHTML='<div class=\\'sync-history-thumb-placeholder\\'>&#x1F4E5;</div>'">`
        : `<div class="sync-history-thumb-placeholder">${_syncSourceIcon(entry.source)}</div>`;

    const sourceBadge = `<span class="sync-history-source-badge ${entry.source}">${escapeHtml(entry.source)}</span>`;

    const title = entry.playlist_name || 'Unknown';
    const meta = [entry.artist_name, entry.album_name].filter(Boolean).join(' — ') || entry.sync_type;

    // Stats
    let statsHtml = '';
    if (entry.completed_at) {
        const parts = [];
        if (entry.tracks_found > 0) parts.push(`<span class="sync-history-stat found">${entry.tracks_found} found</span>`);
        if (entry.tracks_downloaded > 0) parts.push(`<span class="sync-history-stat downloaded">${entry.tracks_downloaded} downloaded</span>`);
        if (entry.tracks_failed > 0) parts.push(`<span class="sync-history-stat failed">${entry.tracks_failed} failed</span>`);
        if (parts.length === 0) parts.push(`<span class="sync-history-stat found">${entry.total_tracks} in library</span>`);
        statsHtml = `<div class="sync-history-stats">${parts.join('')}</div>`;
    } else {
        statsHtml = `<div class="sync-history-stats"><span class="sync-history-stat pending">In progress</span></div>`;
    }

    const timeStr = formatHistoryTime(entry.started_at);

    return `<div class="sync-history-entry-wrapper" id="sync-history-wrapper-${entry.id}">
        <div class="sync-history-entry">
            ${thumb}
            <div class="sync-history-entry-text">
                <div class="sync-history-entry-title">${escapeHtml(title)}</div>
                <div class="sync-history-entry-meta">${escapeHtml(meta)}</div>
            </div>
            ${sourceBadge}
            ${statsHtml}
            <div class="sync-history-entry-time">${timeStr}</div>
            <button class="sync-history-delete-btn" onclick="deleteSyncHistoryEntry(${entry.id})" title="Delete this entry">&times;</button>
            <button class="sync-history-resync-btn" id="resync-btn-${entry.id}" onclick="retriggerSync(${entry.id})" title="Re-sync this playlist">Re-sync</button>
        </div>
        <div class="sync-history-live-progress" id="sync-history-progress-${entry.id}" style="display:none;">
            <div class="sync-history-progress-bar-container">
                <div class="sync-history-progress-bar-fill" id="sync-history-bar-${entry.id}"></div>
            </div>
            <div class="sync-history-progress-text">
                <span class="sync-history-progress-step" id="sync-history-step-${entry.id}">Starting sync...</span>
                <div class="sync-history-progress-stats">
                    <span class="matched" id="sync-history-matched-${entry.id}">0 matched</span>
                    <span class="failed" id="sync-history-failed-${entry.id}">0 failed</span>
                </div>
                <button class="sync-history-cancel-btn" id="sync-history-cancel-${entry.id}" onclick="cancelSyncHistoryResync(${entry.id})">Cancel</button>
            </div>
        </div>
    </div>`;
}

function _syncSourceIcon(source) {
    const icons = {
        spotify: '&#x1F3B5;', beatport: '&#x1F3B6;', youtube: '&#x25B6;',
        tidal: '&#x1F30A;', deezer: '&#x1F3A7;', wishlist: '&#x2B50;',
        library: '&#x1F4DA;', discover: '&#x1F50D;', mirrored: '&#x1F517;',
        listenbrainz: '&#x1F3A7;', spotify_public: '&#x1F3B5;'
    };
    return icons[source] || '&#x1F4E5;';
}

function renderSyncHistoryPagination(total, page, limit) {
    const pagination = document.getElementById('sync-history-pagination');
    if (!pagination) return;
    const totalPages = Math.ceil(total / limit);
    if (totalPages <= 1) { pagination.innerHTML = ''; return; }
    pagination.innerHTML = `
        <button class="sync-history-page-btn" onclick="changeSyncHistoryPage(${page - 1})" ${page <= 1 ? 'disabled' : ''}>Prev</button>
        <span class="sync-history-page-info">Page ${page} of ${totalPages}</span>
        <button class="sync-history-page-btn" onclick="changeSyncHistoryPage(${page + 1})" ${page >= totalPages ? 'disabled' : ''}>Next</button>
    `;
}

function changeSyncHistoryPage(newPage) {
    if (newPage < 1) return;
    _syncHistoryState.page = newPage;
    loadSyncHistory();
}

// Track active re-syncs from history
let _activeSyncHistoryResyncs = {};

// Sources that do server playlist sync (match to media server) vs download (Soulseek download)
const _serverSyncSources = new Set(['spotify', 'tidal', 'deezer', 'youtube', 'mirrored', 'listenbrainz', 'spotify_public', 'beatport']);
const _downloadSyncSources = new Set(['discover', 'library', 'wishlist']);

async function retriggerSync(entryId) {
    try {
        const resp = await fetch(`/api/sync/history/${entryId}`);
        const data = await resp.json();

        if (!data.success || !data.entry) {
            showToast('Failed to load sync data', 'error');
            return;
        }

        const entry = data.entry;

        // Determine if this is a download-type sync or a server-sync-type
        const isDownloadSync = entry.is_album_download || _downloadSyncSources.has(entry.source);
        const isServerSync = _serverSyncSources.has(entry.source) && !entry.is_album_download;

        if (isDownloadSync) {
            // Download syncs open the download modal (existing behavior)
            closeSyncHistoryModal();

            const virtualPlaylistId = entry.playlist_id || `resync_${Date.now()}_${Math.random().toString(36).substr(2, 9)}`;
            const albumObj = entry.album_context || {
                id: `resync_album_${entryId}`,
                name: entry.playlist_name,
                album_type: entry.sync_type === 'album' ? 'album' : 'compilation',
                images: entry.thumb_url ? [{ url: entry.thumb_url }] : [],
                total_tracks: entry.total_tracks
            };
            const artistObj = entry.artist_context || { id: 'resync_artist', name: 'Various Artists' };
            const contextType = entry.sync_type === 'album' ? 'artist_album' : 'playlist';

            await openDownloadMissingModalForArtistAlbum(
                virtualPlaylistId, entry.playlist_name, entry.tracks,
                albumObj, artistObj, false, contextType
            );
        } else {
            // Server sync — start sync and show live progress in the card
            await _startSyncHistoryResync(entryId, entry);
        }
    } catch (err) {
        console.error('Error re-triggering sync:', err);
        showToast('Error loading sync data', 'error');
    }
}

async function _startSyncHistoryResync(entryId, entry) {
    // Disable the re-sync button
    const btn = document.getElementById(`resync-btn-${entryId}`);
    if (btn) { btn.disabled = true; btn.textContent = 'Syncing...'; }

    // Show the progress area
    const wrapper = document.getElementById(`sync-history-wrapper-${entryId}`);
    const progressArea = document.getElementById(`sync-history-progress-${entryId}`);
    if (wrapper) wrapper.classList.add('syncing');
    if (progressArea) progressArea.style.display = '';

    // Build a unique sync playlist ID for this re-sync
    const syncPlaylistId = `resync_${entryId}_${Date.now()}`;

    // Prepare tracks for the sync API
    const tracks = (entry.tracks || []).map(t => {
        const artists = Array.isArray(t.artists)
            ? (typeof t.artists[0] === 'object' ? t.artists.map(a => a.name || a) : t.artists)
            : [t.artists || 'Unknown Artist'];
        const albumName = typeof t.album === 'object' ? (t.album?.name || '') : (t.album || '');
        return {
            id: t.id || '',
            name: t.name || '',
            artists: artists,
            album: albumName,
            duration_ms: t.duration_ms || 0,
            popularity: t.popularity || 0
        };
    });

    try {
        const response = await fetch('/api/sync/start', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                playlist_id: syncPlaylistId,
                playlist_name: entry.playlist_name,
                tracks: tracks
            })
        });

        const result = await response.json();
        if (!result.success) {
            showToast(`Sync failed: ${result.error || 'Unknown error'}`, 'error');
            _cleanupSyncHistoryResync(entryId);
            return;
        }

        // Store active re-sync state
        _activeSyncHistoryResyncs[entryId] = { syncPlaylistId, entryId };

        // Start polling for progress
        _pollSyncHistoryProgress(entryId, syncPlaylistId);

    } catch (err) {
        console.error('Error starting re-sync:', err);
        showToast('Failed to start sync', 'error');
        _cleanupSyncHistoryResync(entryId);
    }
}

function _pollSyncHistoryProgress(entryId, syncPlaylistId) {
    const pollInterval = setInterval(async () => {
        try {
            const resp = await fetch(`/api/sync/status/${syncPlaylistId}`);
            if (!resp.ok) {
                clearInterval(pollInterval);
                _cleanupSyncHistoryResync(entryId, 'error');
                return;
            }
            const state = await resp.json();

            if (state.status === 'syncing' || state.status === 'starting') {
                const progress = state.progress || {};
                const matched = progress.matched_tracks || 0;
                const failed = progress.failed_tracks || 0;
                const total = progress.total_tracks || 0;
                const step = progress.current_step || 'Processing';
                const currentTrack = progress.current_track || '';
                const processed = matched + failed;
                const percent = total > 0 ? Math.round((processed / total) * 100) : 0;

                const bar = document.getElementById(`sync-history-bar-${entryId}`);
                const stepEl = document.getElementById(`sync-history-step-${entryId}`);
                const matchedEl = document.getElementById(`sync-history-matched-${entryId}`);
                const failedEl = document.getElementById(`sync-history-failed-${entryId}`);

                if (bar) bar.style.width = `${percent}%`;
                if (stepEl) stepEl.textContent = currentTrack ? `${step} — ${currentTrack}` : step;
                if (matchedEl) matchedEl.textContent = `${matched} matched`;
                if (failedEl) failedEl.textContent = `${failed} failed`;

            } else if (state.status === 'finished') {
                clearInterval(pollInterval);
                const progress = state.progress || state.result || {};
                const matched = progress.matched_tracks || 0;
                const failed = progress.failed_tracks || 0;
                const total = progress.total_tracks || 0;
                const synced = progress.synced_tracks || 0;

                const bar = document.getElementById(`sync-history-bar-${entryId}`);
                const stepEl = document.getElementById(`sync-history-step-${entryId}`);
                const matchedEl = document.getElementById(`sync-history-matched-${entryId}`);
                const failedEl = document.getElementById(`sync-history-failed-${entryId}`);

                if (bar) bar.style.width = '100%';
                if (stepEl) stepEl.textContent = `Sync complete — ${matched}/${total} matched, ${synced} synced`;
                if (matchedEl) matchedEl.textContent = `${matched} matched`;
                if (failedEl) failedEl.textContent = `${failed} failed`;

                // Hide cancel button
                const cancelBtn = document.getElementById(`sync-history-cancel-${entryId}`);
                if (cancelBtn) cancelBtn.style.display = 'none';

                showToast(`Re-sync complete: ${matched}/${total} matched`, 'success');

                // Auto-collapse after 5 seconds
                setTimeout(() => _cleanupSyncHistoryResync(entryId, 'finished'), 5000);

            } else if (state.status === 'cancelled' || state.status === 'error') {
                clearInterval(pollInterval);
                const stepEl = document.getElementById(`sync-history-step-${entryId}`);
                if (stepEl) stepEl.textContent = state.status === 'cancelled' ? 'Sync cancelled' : `Sync error: ${state.error || 'Unknown'}`;

                const cancelBtn = document.getElementById(`sync-history-cancel-${entryId}`);
                if (cancelBtn) cancelBtn.style.display = 'none';

                setTimeout(() => _cleanupSyncHistoryResync(entryId, state.status), 3000);
            }
        } catch (err) {
            console.error('Error polling sync status:', err);
            clearInterval(pollInterval);
            _cleanupSyncHistoryResync(entryId, 'error');
        }
    }, 2000);

    // Store interval so cancel can clear it
    if (_activeSyncHistoryResyncs[entryId]) {
        _activeSyncHistoryResyncs[entryId].pollInterval = pollInterval;
    }
}

async function cancelSyncHistoryResync(entryId) {
    const active = _activeSyncHistoryResyncs[entryId];
    if (!active) return;

    try {
        await fetch('/api/sync/cancel', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ playlist_id: active.syncPlaylistId })
        });

        const stepEl = document.getElementById(`sync-history-step-${entryId}`);
        if (stepEl) stepEl.textContent = 'Cancelling...';

    } catch (err) {
        console.error('Error cancelling sync:', err);
        showToast('Failed to cancel sync', 'error');
    }
}

function _cleanupSyncHistoryResync(entryId, finalStatus) {
    const active = _activeSyncHistoryResyncs[entryId];
    if (active && active.pollInterval) {
        clearInterval(active.pollInterval);
    }
    delete _activeSyncHistoryResyncs[entryId];

    const wrapper = document.getElementById(`sync-history-wrapper-${entryId}`);
    const progressArea = document.getElementById(`sync-history-progress-${entryId}`);
    const btn = document.getElementById(`resync-btn-${entryId}`);

    if (wrapper) wrapper.classList.remove('syncing');
    if (progressArea) progressArea.style.display = 'none';
    if (btn) { btn.disabled = false; btn.textContent = 'Re-sync'; }
}

async function deleteSyncHistoryEntry(entryId) {
    try {
        const resp = await fetch(`/api/sync/history/${entryId}`, { method: 'DELETE' });
        const data = await resp.json();
        if (data.success) {
            const wrapper = document.getElementById(`sync-history-wrapper-${entryId}`);
            if (wrapper) {
                wrapper.style.transition = 'opacity 0.2s ease, max-height 0.3s ease';
                wrapper.style.opacity = '0';
                wrapper.style.maxHeight = wrapper.offsetHeight + 'px';
                requestAnimationFrame(() => { wrapper.style.maxHeight = '0'; wrapper.style.overflow = 'hidden'; });
                setTimeout(() => wrapper.remove(), 300);
            }
        } else {
            showToast('Failed to delete entry', 'error');
        }
    } catch (err) {
        console.error('Error deleting sync history entry:', err);
        showToast('Failed to delete entry', 'error');
    }
}

// ── Sync Playlist to Server (from Download Modal) ──────────────────

// Track active modal syncs
let _activeModalSyncs = {};

function _isBeatportPlaylistId(id) {
    return id.startsWith('beatport_chart_') || id.startsWith('beatport_top100_') || id.startsWith('beatport_hype100_');
}

async function syncPlaylistToServer(playlistId) {
    const process = activeDownloadProcesses[playlistId];
    if (!process) { showToast('No playlist data found', 'error'); return; }

    // Disable the sync button
    const btn = document.getElementById(`sync-server-btn-${playlistId}`);
    if (btn) { btn.disabled = true; btn.textContent = 'Syncing...'; }

    // Show progress area
    const progressArea = document.getElementById(`modal-sync-progress-${playlistId}`);
    if (progressArea) progressArea.style.display = '';

    const syncPlaylistId = `beatport_sync_${Date.now()}_${Math.random().toString(36).substr(2, 9)}`;
    const playlistName = process.playlist?.name || 'Beatport Playlist';

    // Format tracks for the sync API
    const tracks = (process.tracks || []).map(t => {
        const artists = Array.isArray(t.artists)
            ? (typeof t.artists[0] === 'object' ? t.artists.map(a => a.name || a) : t.artists)
            : [t.artists || 'Unknown Artist'];
        const albumName = typeof t.album === 'object' ? (t.album?.name || '') : (t.album || '');
        return {
            id: t.id || '',
            name: t.name || '',
            artists: artists,
            album: albumName,
            duration_ms: t.duration_ms || 0,
            popularity: t.popularity || 0
        };
    });

    try {
        const response = await fetch('/api/sync/start', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                playlist_id: syncPlaylistId,
                playlist_name: playlistName,
                tracks: tracks
            })
        });

        const result = await response.json();
        if (!result.success) {
            showToast(`Sync failed: ${result.error || 'Unknown error'}`, 'error');
            _cleanupModalSync(playlistId);
            return;
        }

        _activeModalSyncs[playlistId] = { syncPlaylistId };
        _pollModalSyncProgress(playlistId, syncPlaylistId);

    } catch (err) {
        console.error('Error starting playlist sync:', err);
        showToast('Failed to start sync', 'error');
        _cleanupModalSync(playlistId);
    }
}

function _pollModalSyncProgress(playlistId, syncPlaylistId) {
    const pollInterval = setInterval(async () => {
        try {
            const resp = await fetch(`/api/sync/status/${syncPlaylistId}`);
            if (!resp.ok) { clearInterval(pollInterval); _cleanupModalSync(playlistId, 'error'); return; }
            const state = await resp.json();

            const bar = document.getElementById(`modal-sync-bar-${playlistId}`);
            const stepEl = document.getElementById(`modal-sync-step-${playlistId}`);
            const matchedEl = document.getElementById(`modal-sync-matched-${playlistId}`);
            const failedEl = document.getElementById(`modal-sync-failed-${playlistId}`);

            if (state.status === 'syncing' || state.status === 'starting') {
                const p = state.progress || {};
                const matched = p.matched_tracks || 0;
                const failed = p.failed_tracks || 0;
                const total = p.total_tracks || 0;
                const step = p.current_step || 'Processing';
                const currentTrack = p.current_track || '';
                const processed = matched + failed;
                const percent = total > 0 ? Math.round((processed / total) * 100) : 0;

                if (bar) bar.style.width = `${percent}%`;
                if (stepEl) stepEl.textContent = currentTrack ? `${step} — ${currentTrack}` : step;
                if (matchedEl) matchedEl.textContent = `${matched} matched`;
                if (failedEl) failedEl.textContent = `${failed} failed`;

            } else if (state.status === 'finished') {
                clearInterval(pollInterval);
                const p = state.progress || state.result || {};
                const matched = p.matched_tracks || 0;
                const failed = p.failed_tracks || 0;
                const total = p.total_tracks || 0;
                const synced = p.synced_tracks || 0;

                if (bar) bar.style.width = '100%';
                if (stepEl) stepEl.textContent = `Sync complete — ${matched}/${total} matched, ${synced} synced`;
                if (matchedEl) matchedEl.textContent = `${matched} matched`;
                if (failedEl) failedEl.textContent = `${failed} failed`;

                const cancelBtn = document.getElementById(`modal-sync-cancel-${playlistId}`);
                if (cancelBtn) cancelBtn.style.display = 'none';

                showToast(`Server sync complete: ${matched}/${total} matched`, 'success');

                // Re-enable sync button after a delay
                setTimeout(() => _cleanupModalSync(playlistId, 'finished'), 5000);

            } else if (state.status === 'cancelled' || state.status === 'error') {
                clearInterval(pollInterval);
                if (stepEl) stepEl.textContent = state.status === 'cancelled' ? 'Sync cancelled' : `Sync error`;
                const cancelBtn = document.getElementById(`modal-sync-cancel-${playlistId}`);
                if (cancelBtn) cancelBtn.style.display = 'none';
                setTimeout(() => _cleanupModalSync(playlistId, state.status), 3000);
            }
        } catch (err) {
            console.error('Error polling modal sync status:', err);
            clearInterval(pollInterval);
            _cleanupModalSync(playlistId, 'error');
        }
    }, 2000);

    if (_activeModalSyncs[playlistId]) {
        _activeModalSyncs[playlistId].pollInterval = pollInterval;
    }
}

async function cancelModalSync(playlistId) {
    const active = _activeModalSyncs[playlistId];
    if (!active) return;

    try {
        await fetch('/api/sync/cancel', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ playlist_id: active.syncPlaylistId })
        });
        const stepEl = document.getElementById(`modal-sync-step-${playlistId}`);
        if (stepEl) stepEl.textContent = 'Cancelling...';
    } catch (err) {
        console.error('Error cancelling modal sync:', err);
    }
}

function _cleanupModalSync(playlistId, finalStatus) {
    const active = _activeModalSyncs[playlistId];
    if (active && active.pollInterval) clearInterval(active.pollInterval);
    delete _activeModalSyncs[playlistId];

    const progressArea = document.getElementById(`modal-sync-progress-${playlistId}`);
    const btn = document.getElementById(`sync-server-btn-${playlistId}`);

    if (finalStatus === 'finished') {
        // Keep progress visible but hide after fade
        if (progressArea) setTimeout(() => { progressArea.style.display = 'none'; }, 300);
    } else {
        if (progressArea) progressArea.style.display = 'none';
    }
    if (btn) { btn.disabled = false; btn.textContent = 'Sync to Server'; }
}

// ── Metadata Cache Modal ────────────────────────────────────────────
let _mcacheCurrentTab = 'artist';
let _mcachePage = 0;
let _mcacheSearchTimeout = null;
// ==================================================================================
// DOWNLOAD BLACKLIST VIEWER
// ==================================================================================

async function loadBlacklistCount() {
    try {
        const res = await fetch('/api/library/blacklist');
        const data = await res.json();
        const el = document.getElementById('blacklist-count');
        if (el) el.textContent = data.entries?.length || 0;
    } catch (e) { /* ignore */ }
}

async function openBlacklistModal() {
    const existing = document.getElementById('blacklist-modal-overlay');
    if (existing) existing.remove();

    const overlay = document.createElement('div');
    overlay.id = 'blacklist-modal-overlay';
    overlay.className = 'redownload-overlay';
    overlay.onclick = e => { if (e.target === overlay) overlay.remove(); };

    overlay.innerHTML = `
        <div class="blacklist-modal">
            <div class="blacklist-modal-header">
                <h3>Download Blacklist</h3>
                <button class="redownload-close" onclick="document.getElementById('blacklist-modal-overlay')?.remove()">&times;</button>
            </div>
            <div class="blacklist-modal-body" id="blacklist-modal-body">
                <div class="redownload-loading"><div class="server-search-spinner"></div>Loading...</div>
            </div>
        </div>
    `;

    document.body.appendChild(overlay);

    const escH = e => { if (e.key === 'Escape') { overlay.remove(); document.removeEventListener('keydown', escH); } };
    document.addEventListener('keydown', escH);

    try {
        const res = await fetch('/api/library/blacklist');
        const data = await res.json();
        const body = document.getElementById('blacklist-modal-body');

        if (!data.success || !data.entries || data.entries.length === 0) {
            body.innerHTML = '<div class="blacklist-empty">No blocked sources. Sources can be blacklisted from the Source Info (ℹ) button on tracks in the enhanced library view.</div>';
            return;
        }

        const serviceIcons = { soulseek: '🔍', youtube: '▶️', tidal: '🌊', qobuz: '🎵', hifi: '🎧', deezer_dl: '💜' };

        body.innerHTML = data.entries.map(e => {
            const displayFile = (e.blocked_filename || '').replace(/\\/g, '/').split('/').pop() || 'Unknown';
            const svc = e.blocked_username && ['youtube', 'tidal', 'qobuz', 'hifi', 'deezer_dl'].includes(e.blocked_username) ? e.blocked_username : 'soulseek';
            const icon = serviceIcons[svc] || '🔍';
            const ago = e.created_at ? timeAgo(e.created_at) : '';
            return `
                <div class="blacklist-entry">
                    <div class="blacklist-entry-icon">${icon}</div>
                    <div class="blacklist-entry-info">
                        <div class="blacklist-entry-track">${_esc(e.track_artist || '')}${e.track_artist && e.track_title ? ' — ' : ''}${_esc(e.track_title || '')}</div>
                        <div class="blacklist-entry-file" title="${_esc(e.blocked_filename || '')}">${_esc(displayFile)}</div>
                        ${e.blocked_username && svc === 'soulseek' ? `<div class="blacklist-entry-user">from ${_esc(e.blocked_username)}</div>` : ''}
                    </div>
                    <div class="blacklist-entry-meta">${ago}</div>
                    <button class="blacklist-entry-remove" onclick="_removeBlacklistEntry(${e.id}, this)" title="Remove from blacklist">✕</button>
                </div>`;
        }).join('');

    } catch (e) {
        document.getElementById('blacklist-modal-body').innerHTML = `<div class="blacklist-empty">Error: ${e.message}</div>`;
    }
}

async function _removeBlacklistEntry(id, btn) {
    if (!await showConfirmDialog({ title: 'Remove from Blacklist', message: 'Allow this source to be used for downloads again?', confirmText: 'Remove' })) return;
    try {
        const res = await fetch(`/api/library/blacklist/${id}`, { method: 'DELETE' });
        const data = await res.json();
        if (data.success) {
            btn.closest('.blacklist-entry').remove();
            showToast('Removed from blacklist', 'success');
            loadBlacklistCount();
        }
    } catch (e) {
        showToast('Error: ' + e.message, 'error');
    }
}

const MCACHE_PAGE_SIZE = 48;

function openMetadataCacheModal() {
    const modal = document.getElementById('mcache-browse-modal');
    if (modal) {
        modal.style.display = 'flex';
        _mcacheCurrentTab = 'artist';
        _mcachePage = 0;
        // Reset UI
        document.querySelectorAll('.mcache-tab').forEach(t => t.classList.remove('active'));
        document.querySelector('.mcache-tab[data-tab="artist"]')?.classList.add('active');
        const searchInput = document.getElementById('mcache-search');
        if (searchInput) searchInput.value = '';
        const sourceFilter = document.getElementById('mcache-source-filter');
        if (sourceFilter) sourceFilter.value = '';
        const sortFilter = document.getElementById('mcache-sort-filter');
        if (sortFilter) sortFilter.value = 'last_accessed_at';
        loadMetadataCacheBrowseStats();
        loadMetadataCacheBrowse();
    }
}

function closeMetadataCacheModal() {
    const modal = document.getElementById('mcache-browse-modal');
    if (modal) modal.style.display = 'none';
}

async function loadMetadataCacheBrowseStats() {
    try {
        const response = await fetch('/api/metadata-cache/stats');
        if (!response.ok) return;
        const stats = await response.json();

        const el = (id, val) => {
            const e = document.getElementById(id);
            if (e) e.textContent = val;
        };

        const spotifyTotal = (stats.artists?.spotify || 0) + (stats.albums?.spotify || 0) + (stats.tracks?.spotify || 0);
        const itunesTotal = (stats.artists?.itunes || 0) + (stats.albums?.itunes || 0) + (stats.tracks?.itunes || 0);
        const deezerTotal = (stats.artists?.deezer || 0) + (stats.albums?.deezer || 0) + (stats.tracks?.deezer || 0);
        const beatportTotal = (stats.artists?.beatport || 0) + (stats.albums?.beatport || 0) + (stats.tracks?.beatport || 0);
        el('mcache-browse-spotify-count', spotifyTotal);
        el('mcache-browse-itunes-count', itunesTotal);
        el('mcache-browse-deezer-count', deezerTotal);
        el('mcache-browse-beatport-count', beatportTotal);
        const discogsTotal = (stats.artists?.discogs || 0) + (stats.albums?.discogs || 0) + (stats.tracks?.discogs || 0);
        el('mcache-browse-discogs-count', discogsTotal);
        el('mcache-browse-musicbrainz-count', stats.musicbrainz_total || 0);
        el('mcache-browse-hits', stats.total_hits || 0);
        el('mcache-browse-searches', stats.searches || 0);
    } catch (e) { /* ignore */ }
}

function switchMetadataCacheTab(tab) {
    _mcacheCurrentTab = tab;
    _mcachePage = 0;
    document.querySelectorAll('.mcache-tab').forEach(t => {
        t.classList.toggle('active', t.dataset.tab === tab);
    });
    loadMetadataCacheBrowse();
}

async function loadMetadataCacheBrowse() {
    const grid = document.getElementById('mcache-grid');
    if (!grid) return;

    const source = document.getElementById('mcache-source-filter')?.value || '';
    const search = document.getElementById('mcache-search')?.value || '';
    const sort = document.getElementById('mcache-sort-filter')?.value || 'last_accessed_at';

    grid.innerHTML = '<div class="mcache-empty"><div class="mcache-empty-icon">...</div><div class="mcache-empty-sub">Loading...</div></div>';

    try {
        let data;
        if (source === 'musicbrainz') {
            // MusicBrainz is a separate cache table — use dedicated endpoint
            const params = new URLSearchParams({
                entity_type: _mcacheCurrentTab,
                page: _mcachePage + 1,
                limit: MCACHE_PAGE_SIZE
            });
            if (search) params.set('search', search);
            const response = await fetch(`/api/metadata-cache/browse-musicbrainz?${params}`);
            if (!response.ok) throw new Error('Failed to load');
            data = await response.json();
        } else {
            const params = new URLSearchParams({
                type: _mcacheCurrentTab,
                sort: sort,
                sort_dir: sort === 'name' ? 'asc' : 'desc',
                offset: _mcachePage * MCACHE_PAGE_SIZE,
                limit: MCACHE_PAGE_SIZE
            });
            if (source) params.set('source', source);
            if (search) params.set('search', search);
            const response = await fetch(`/api/metadata-cache/browse?${params}`);
            if (!response.ok) throw new Error('Failed to load');
            data = await response.json();
        }

        if (!data.items || data.items.length === 0) {
            grid.innerHTML = `
                <div class="mcache-empty">
                    <div class="mcache-empty-icon">📦</div>
                    <div class="mcache-empty-title">No cached ${_mcacheCurrentTab}s yet</div>
                    <div class="mcache-empty-sub">As you search and browse music in SoulSync, API responses will be cached here automatically.</div>
                </div>`;
            renderMetadataCachePagination(0, 0);
            return;
        }

        renderMetadataCacheGrid(data.items, _mcacheCurrentTab);
        renderMetadataCachePagination(data.total, data.offset);
    } catch (e) {
        grid.innerHTML = '<div class="mcache-empty"><div class="mcache-empty-sub">Failed to load cache data.</div></div>';
    }
}

function renderMetadataCacheGrid(items, entityType) {
    const grid = document.getElementById('mcache-grid');
    if (!grid) return;

    grid.innerHTML = items.map(item => {
        const source = item.source || 'spotify';
        const sourceBadge = `<span class="mcache-source-badge ${source}">${source}</span>`;
        const cacheAge = formatCacheAge(item.last_accessed_at);
        const hits = item.access_count || 1;

        let imageHtml = '';
        const isArtist = entityType === 'artist';
        const shapeClass = isArtist ? ' artist' : '';

        if (item.image_url) {
            imageHtml = `<img class="mcache-card-image${shapeClass}" src="${item.image_url}" alt="" loading="lazy" onerror="this.style.display='none';this.nextElementSibling.style.display='flex'"><div class="mcache-card-image-placeholder${shapeClass}" style="display:none">${(item.name || '?')[0].toUpperCase()}</div>`;
        } else {
            imageHtml = `<div class="mcache-card-image-placeholder${shapeClass}">${(item.name || '?')[0].toUpperCase()}</div>`;
        }

        let subText = '';
        let metaText = '';

        if (source === 'musicbrainz') {
            subText = item.artist_name || '';
            metaText = item._mb_matched ? `MBID: ${(item._mb_id || '').substring(0, 8)}…` : 'No match found';
        } else if (entityType === 'artist') {
            const genres = item.genres ? (typeof item.genres === 'string' ? JSON.parse(item.genres || '[]') : item.genres) : [];
            subText = genres.length > 0 ? genres.slice(0, 2).join(', ') : '';
            if (item.popularity) metaText = `Pop: ${item.popularity}`;
        } else if (entityType === 'album') {
            subText = item.artist_name || '';
            const parts = [];
            if (item.release_date) parts.push(item.release_date.substring(0, 4));
            if (item.total_tracks) parts.push(`${item.total_tracks} tracks`);
            if (item.album_type) parts.push(item.album_type);
            metaText = parts.join(' · ');
        } else if (entityType === 'track') {
            subText = item.artist_name || '';
            const parts = [];
            if (item.album_name) parts.push(item.album_name);
            if (item.duration_ms) parts.push(formatDuration(item.duration_ms));
            metaText = parts.join(' · ');
        }

        const clickAttr = source === 'musicbrainz' ? '' : `onclick="openMetadataCacheDetail('${source}', '${entityType}', '${encodeURIComponent(item.entity_id)}')"`;
        const mbStatusClass = source === 'musicbrainz' ? (item._mb_matched ? ' mb-matched' : ' mb-failed') : '';

        return `
            <div class="mcache-card${mbStatusClass}" ${clickAttr}>
                <div class="mcache-card-top">
                    ${imageHtml}
                    <div class="mcache-card-info">
                        <div class="mcache-card-name" title="${(item.name || '').replace(/"/g, '&quot;')}">${item.name || 'Unknown'}</div>
                        ${subText ? `<div class="mcache-card-sub">${subText}</div>` : ''}
                        ${metaText ? `<div class="mcache-card-meta">${metaText}</div>` : ''}
                    </div>
                </div>
                <div class="mcache-card-bottom">
                    ${sourceBadge}
                    <span class="mcache-card-cache-info">${cacheAge} · ${hits}x</span>
                </div>
            </div>`;
    }).join('');
}

function renderMetadataCachePagination(total, offset) {
    const container = document.getElementById('mcache-pagination');
    if (!container) return;

    const totalPages = Math.ceil(total / MCACHE_PAGE_SIZE);
    const currentPage = Math.floor(offset / MCACHE_PAGE_SIZE);

    if (totalPages <= 1) {
        container.innerHTML = total > 0 ? `<span style="font-size:11px;color:rgba(255,255,255,0.3)">${total} result${total !== 1 ? 's' : ''}</span>` : '';
        return;
    }

    let html = '';
    html += `<button class="mcache-page-btn" ${currentPage === 0 ? 'disabled' : ''} onclick="_mcachePage=${currentPage - 1};loadMetadataCacheBrowse()">‹</button>`;

    const maxVisible = 7;
    let start = Math.max(0, currentPage - Math.floor(maxVisible / 2));
    let end = Math.min(totalPages, start + maxVisible);
    if (end - start < maxVisible) start = Math.max(0, end - maxVisible);

    if (start > 0) {
        html += `<button class="mcache-page-btn" onclick="_mcachePage=0;loadMetadataCacheBrowse()">1</button>`;
        if (start > 1) html += `<span style="color:rgba(255,255,255,0.2);padding:0 4px">...</span>`;
    }

    for (let i = start; i < end; i++) {
        html += `<button class="mcache-page-btn${i === currentPage ? ' active' : ''}" onclick="_mcachePage=${i};loadMetadataCacheBrowse()">${i + 1}</button>`;
    }

    if (end < totalPages) {
        if (end < totalPages - 1) html += `<span style="color:rgba(255,255,255,0.2);padding:0 4px">...</span>`;
        html += `<button class="mcache-page-btn" onclick="_mcachePage=${totalPages - 1};loadMetadataCacheBrowse()">${totalPages}</button>`;
    }

    html += `<button class="mcache-page-btn" ${currentPage >= totalPages - 1 ? 'disabled' : ''} onclick="_mcachePage=${currentPage + 1};loadMetadataCacheBrowse()">›</button>`;
    html += `<span style="font-size:11px;color:rgba(255,255,255,0.25);margin-left:8px">${total} total</span>`;

    container.innerHTML = html;
}

async function openMetadataCacheDetail(source, entityType, entityId) {
    const modal = document.getElementById('mcache-detail-modal');
    const body = document.getElementById('mcache-detail-body');
    const title = document.getElementById('mcache-detail-title');
    if (!modal || !body) return;

    modal.style.display = 'flex';
    body.innerHTML = '<div style="text-align:center;padding:40px;color:rgba(255,255,255,0.4)">Loading...</div>';
    if (title) title.textContent = 'Loading...';

    try {
        const response = await fetch(`/api/metadata-cache/entity/${source}/${entityType}/${entityId}`);
        if (!response.ok) throw new Error('Not found');
        const data = await response.json();

        if (title) title.textContent = data.name || 'Unknown';

        const isArtist = entityType === 'artist';
        const shapeClass = isArtist ? ' artist' : '';
        let imageHtml = '';
        if (data.image_url) {
            imageHtml = `<img class="mcache-detail-image${shapeClass}" src="${data.image_url}" alt="" onerror="this.style.display='none';this.nextElementSibling.style.display='flex'"><div class="mcache-detail-image-placeholder${shapeClass}" style="display:none">${(data.name || '?')[0].toUpperCase()}</div>`;
        } else {
            imageHtml = `<div class="mcache-detail-image-placeholder${shapeClass}">${(data.name || '?')[0].toUpperCase()}</div>`;
        }

        const sourceBadge = `<span class="mcache-source-badge ${source}">${source}</span>`;
        const typeBadge = `<span class="mcache-type-badge">${entityType}</span>`;

        // Build structured fields table
        let fieldsHtml = '<table class="mcache-detail-table">';
        const addRow = (label, value) => {
            if (value !== null && value !== undefined && value !== '') {
                fieldsHtml += `<tr><td>${label}</td><td>${value}</td></tr>`;
            }
        };

        addRow('Entity ID', data.entity_id);
        addRow('Name', data.name);

        if (entityType === 'artist') {
            const genres = data.genres ? (typeof data.genres === 'string' ? JSON.parse(data.genres || '[]') : data.genres) : [];
            if (genres.length) addRow('Genres', genres.join(', '));
            if (data.popularity) addRow('Popularity', data.popularity);
            if (data.followers) addRow('Followers', data.followers.toLocaleString());
        } else if (entityType === 'album') {
            addRow('Artist', data.artist_name);
            addRow('Release Date', data.release_date);
            addRow('Total Tracks', data.total_tracks);
            addRow('Album Type', data.album_type);
            addRow('Label', data.label);
        } else if (entityType === 'track') {
            addRow('Artist', data.artist_name);
            addRow('Album', data.album_name);
            if (data.duration_ms) addRow('Duration', formatDuration(data.duration_ms));
            addRow('Track Number', data.track_number);
            addRow('Disc Number', data.disc_number);
            addRow('Explicit', data.explicit ? 'Yes' : 'No');
            addRow('ISRC', data.isrc);
            if (data.preview_url) addRow('Preview', `<a href="${data.preview_url}" target="_blank" style="color:var(--accent,#6d5dfc)">Listen</a>`);
        }

        fieldsHtml += '</table>';

        // Cache metadata section
        let cacheHtml = '<div class="mcache-detail-section-title">Cache Metadata</div>';
        cacheHtml += '<table class="mcache-detail-table">';
        if (data.created_at) cacheHtml += `<tr><td>Cached At</td><td>${new Date(data.created_at).toLocaleString()}</td></tr>`;
        if (data.last_accessed_at) cacheHtml += `<tr><td>Last Accessed</td><td>${new Date(data.last_accessed_at).toLocaleString()}</td></tr>`;
        if (data.access_count) cacheHtml += `<tr><td>Access Count</td><td>${data.access_count}</td></tr>`;
        if (data.ttl_days) cacheHtml += `<tr><td>TTL</td><td>${data.ttl_days} days</td></tr>`;
        cacheHtml += '</table>';

        // Raw JSON section
        let rawJsonHtml = '';
        if (data.raw_json) {
            const rawStr = typeof data.raw_json === 'string' ? data.raw_json : JSON.stringify(data.raw_json, null, 2);
            const escapedJson = rawStr.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
            rawJsonHtml = `
                <div class="mcache-detail-section-title">Raw API Response</div>
                <button class="mcache-raw-json-toggle" onclick="this.nextElementSibling.style.display=this.nextElementSibling.style.display==='none'?'block':'none';this.textContent=this.nextElementSibling.style.display==='none'?'Show Raw JSON':'Hide Raw JSON'">Show Raw JSON</button>
                <pre class="mcache-raw-json" style="display:none">${escapedJson}</pre>`;
        }

        body.innerHTML = `
            <div class="mcache-detail-hero">
                ${imageHtml}
                <div class="mcache-detail-hero-info">
                    <div class="mcache-detail-hero-name">${data.name || 'Unknown'}</div>
                    ${entityType !== 'artist' && data.artist_name ? `<div class="mcache-detail-hero-sub">${data.artist_name}</div>` : ''}
                    <div class="mcache-detail-badges">
                        ${sourceBadge}
                        ${typeBadge}
                    </div>
                </div>
            </div>
            <div class="mcache-detail-section-title">Details</div>
            ${fieldsHtml}
            ${cacheHtml}
            ${rawJsonHtml}`;
    } catch (e) {
        body.innerHTML = '<div style="text-align:center;padding:40px;color:rgba(255,255,255,0.4)">Failed to load entity details.</div>';
    }
}

function closeMetadataCacheDetail() {
    const modal = document.getElementById('mcache-detail-modal');
    if (modal) modal.style.display = 'none';
}

function toggleMcacheClearDropdown(event) {
    event.stopPropagation();
    const menu = document.getElementById('mcache-clear-dropdown-menu');
    if (!menu) return;
    const isOpen = menu.style.display === 'block';
    menu.style.display = isOpen ? 'none' : 'block';
    if (!isOpen) {
        const closeHandler = (e) => {
            if (!e.target.closest('#mcache-clear-dropdown')) {
                menu.style.display = 'none';
                document.removeEventListener('click', closeHandler);
            }
        };
        setTimeout(() => document.addEventListener('click', closeHandler), 0);
    }
}

async function clearMetadataCache() {
    if (!confirm('Clear ALL cached metadata? This removes all cached API responses.')) return;
    document.getElementById('mcache-clear-dropdown-menu').style.display = 'none';

    try {
        const response = await fetch('/api/metadata-cache/clear', { method: 'DELETE' });
        const data = await response.json();
        if (data.success) {
            showToast(`Cleared ${data.cleared} cached entries`, 'success');
            loadMetadataCacheBrowseStats();
            loadMetadataCacheBrowse();
            loadMetadataCacheStats();
        } else {
            showToast('Failed to clear cache', 'error');
        }
    } catch (e) {
        showToast('Error clearing cache', 'error');
    }
}

async function clearMetadataCacheBySource(source) {
    if (!confirm(`Clear all ${source} cached metadata?`)) return;
    document.getElementById('mcache-clear-dropdown-menu').style.display = 'none';

    try {
        const response = await fetch(`/api/metadata-cache/clear?source=${source}`, { method: 'DELETE' });
        const data = await response.json();
        if (data.success) {
            showToast(`Cleared ${data.cleared} ${source} cache entries`, 'success');
            loadMetadataCacheBrowseStats();
            loadMetadataCacheBrowse();
            loadMetadataCacheStats();
        } else {
            showToast(`Failed to clear ${source} cache`, 'error');
        }
    } catch (e) {
        showToast(`Error clearing ${source} cache`, 'error');
    }
}

async function clearMusicBrainzCache(failedOnly = false) {
    const label = failedOnly ? 'failed MusicBrainz lookups' : 'ALL MusicBrainz cache entries';
    if (!confirm(`Clear ${label}?`)) return;
    document.getElementById('mcache-clear-dropdown-menu').style.display = 'none';

    try {
        const url = failedOnly ? '/api/metadata-cache/clear-musicbrainz?failed_only=true' : '/api/metadata-cache/clear-musicbrainz';
        const response = await fetch(url, { method: 'DELETE' });
        const data = await response.json();
        if (data.success) {
            showToast(`Cleared ${data.cleared} MusicBrainz cache entries`, 'success');
            loadMetadataCacheBrowseStats();
            loadMetadataCacheBrowse();
            loadMetadataCacheStats();
        } else {
            showToast('Failed to clear MusicBrainz cache', 'error');
        }
    } catch (e) {
        showToast('Error clearing MusicBrainz cache', 'error');
    }
}

function debouncedMetadataCacheSearch() {
    if (_mcacheSearchTimeout) clearTimeout(_mcacheSearchTimeout);
    _mcacheSearchTimeout = setTimeout(() => {
        _mcachePage = 0;
        loadMetadataCacheBrowse();
    }, 400);
}

function formatCacheAge(timestamp) {
    if (!timestamp) return '—';
    const now = new Date();
    const then = new Date(timestamp);
    const diffMs = now - then;
    const diffMin = Math.floor(diffMs / 60000);
    if (diffMin < 1) return 'now';
    if (diffMin < 60) return `${diffMin}m`;
    const diffHr = Math.floor(diffMin / 60);
    if (diffHr < 24) return `${diffHr}h`;
    const diffDays = Math.floor(diffHr / 24);
    if (diffDays < 30) return `${diffDays}d`;
    return `${Math.floor(diffDays / 30)}mo`;
}

// ============================================
// == TOOL HELP MODAL                        ==
// ============================================

const TOOL_HELP_CONTENT = {
    'db-updater': {
        title: 'Database Updater',
        content: `
            <h4>What does this tool do?</h4>
            <p>The Database Updater syncs your media server library (Plex, Jellyfin, or Navidrome) with SoulSync's internal database.</p>

            <h4>Update Modes</h4>
            <ul>
                <li><strong>Incremental Update:</strong> Only scans for new artists, albums, and tracks that have been added since the last update. Fast and efficient for regular updates.</li>
                <li><strong>Full Refresh:</strong> Completely rebuilds the database from scratch. Use this if you've made significant changes to your library or if data seems out of sync.</li>
            </ul>

            <h4>When to use it?</h4>
            <ul>
                <li>After adding new music to your media server</li>
                <li>When library statistics seem incorrect</li>
                <li>After changing media server settings</li>
            </ul>

            <h4>Progress Persistence</h4>
            <p>The update runs in the background. You can close this page and return later - progress will be preserved and continue where it left off.</p>
        `
    },
    'metadata-updater': {
        title: 'Metadata Updater',
        content: `
            <h4>What does this tool do?</h4>
            <p>The Metadata Updater triggers all enrichment workers simultaneously, re-checking every item in your library against all connected services (Spotify, MusicBrainz, iTunes, Deezer, AudioDB, Last.fm, Genius, Tidal, Qobuz).</p>

            <h4>Refresh Interval Options</h4>
            <ul>
                <li><strong>6 months:</strong> Only updates metadata for artists not updated in the last 180 days</li>
                <li><strong>3 months:</strong> Updates metadata for artists not updated in the last 90 days</li>
                <li><strong>1 month:</strong> Updates metadata for artists not updated in the last 30 days</li>
                <li><strong>Force All:</strong> Updates all artists regardless of when they were last updated</li>
            </ul>

            <h4>What gets updated?</h4>
            <ul>
                <li>Artist profile photos, genres, and descriptions</li>
                <li>Album cover artwork, labels, and release info</li>
                <li>Track ISRCs, explicit flags, and external IDs</li>
                <li>Service match status for all 9 enrichment workers</li>
            </ul>

            <h4>Note</h4>
            <p>Available for <strong>Plex</strong> and <strong>Jellyfin</strong> media servers. Each enrichment worker only runs if its service is authenticated.</p>
        `
    },
    'quality-scanner': {
        title: 'Quality Scanner',
        content: `
            <h4>What does this tool do?</h4>
            <p>The Quality Scanner identifies tracks in your library that don't meet your preferred quality settings and automatically matches them to Spotify to add to your wishlist for re-downloading.</p>

            <h4>Scan Scope</h4>
            <ul>
                <li><strong>Watchlist Artists Only:</strong> Only scans tracks from artists you're watching. Faster and more focused.</li>
                <li><strong>All Library Tracks:</strong> Scans your entire music library. Comprehensive but takes longer.</li>
            </ul>

            <h4>How it works</h4>
            <ol>
                <li>Scans tracks and checks file format against your quality preferences</li>
                <li>Identifies tracks below your quality threshold (e.g., MP3 when you prefer FLAC)</li>
                <li>Uses fuzzy matching to find the track on Spotify (70% confidence minimum)</li>
                <li>Automatically adds matched tracks to your wishlist for re-download</li>
            </ol>

            <h4>Quality Tiers</h4>
            <ul>
                <li><strong>Tier 1 (Best):</strong> FLAC, WAV, ALAC, AIFF - Lossless formats</li>
                <li><strong>Tier 2:</strong> OPUS, OGG - High quality lossy</li>
                <li><strong>Tier 3:</strong> M4A, AAC - Standard lossy</li>
                <li><strong>Tier 4:</strong> MP3, WMA - Lower quality lossy</li>
            </ul>

            <h4>Stats Explained</h4>
            <ul>
                <li><strong>Processed:</strong> Total tracks scanned so far</li>
                <li><strong>Quality Met:</strong> Tracks that meet your quality standards</li>
                <li><strong>Low Quality:</strong> Tracks below your quality threshold</li>
                <li><strong>Matched:</strong> Low quality tracks successfully matched to Spotify and added to wishlist</li>
            </ul>
        `
    },
    'duplicate-cleaner': {
        title: 'Duplicate Cleaner',
        content: `
            <h4>What does this tool do?</h4>
            <p>The Duplicate Cleaner scans your output folder for duplicate audio files and automatically removes lower-quality versions, keeping only the best copy.</p>

            <h4>How it detects duplicates</h4>
            <p>Files are considered duplicates when:</p>
            <ul>
                <li>They are in the <strong>same folder</strong></li>
                <li>They have the <strong>exact same filename</strong> (ignoring file extension)</li>
            </ul>
            <p>Example: <code>Song.flac</code> and <code>Song.mp3</code> in the same folder = duplicates ✓</p>
            <p>Example: <code>Song.flac</code> and <code>Song (Remaster).flac</code> = NOT duplicates ✗</p>

            <h4>Which file is kept?</h4>
            <p>Priority order (best to worst):</p>
            <ol>
                <li><strong>Format priority:</strong> FLAC/Lossless > OPUS/OGG > M4A/AAC > MP3/WMA</li>
                <li><strong>If same format:</strong> Larger file size is kept (usually indicates better bitrate)</li>
            </ol>

            <h4>Where do deleted files go?</h4>
            <p>Removed files are moved to <code>Transfer/deleted/</code> folder (not permanently deleted). You can review and recover them if needed.</p>

            <h4>Safety Features</h4>
            <ul>
                <li>Only processes audio files (FLAC, MP3, M4A, etc.)</li>
                <li>Only removes files with identical names in the same folder</li>
                <li>Files are moved, not deleted - fully recoverable</li>
                <li>Preserves original folder structure in the deleted folder</li>
            </ul>

            <h4>Stats Explained</h4>
            <ul>
                <li><strong>Files Scanned:</strong> Total audio files checked</li>
                <li><strong>Duplicates Found:</strong> Number of duplicate files detected</li>
                <li><strong>Deleted:</strong> Files moved to deleted folder</li>
                <li><strong>Space Freed:</strong> Total disk space reclaimed</li>
            </ul>
        `
    },
    'media-scan': {
        title: 'Media Server Scan',
        content: `
            <h4>What does this tool do?</h4>
            <p>The Media Server Scan tool manually triggers a Plex media library scan to detect newly downloaded music files.</p>

            <h4>When to use it?</h4>
            <ul>
                <li>After downloading new tracks to refresh your Plex library</li>
                <li>When new music isn't showing up in Plex</li>
                <li>To force an immediate library update instead of waiting for auto-scan</li>
            </ul>

            <h4>What happens when you scan?</h4>
            <ol>
                <li><strong>Plex library scan:</strong> Plex scans your music folder for new/changed files</li>
                <li><strong>Automatic database update:</strong> After the scan completes, SoulSync automatically updates its internal database with new tracks</li>
                <li><strong>Library refreshed:</strong> New music appears in Plex and SoulSync within moments</li>
            </ol>

            <h4>Plex only?</h4>
            <p>Yes! This tool only appears when Plex is your active media server because:</p>
            <ul>
                <li><strong>Jellyfin</strong> automatically detects new files instantly (real-time monitoring)</li>
                <li><strong>Navidrome</strong> automatically detects new files instantly (real-time monitoring)</li>
                <li><strong>Plex</strong> requires manual scans or has delayed auto-scanning</li>
            </ul>

            <h4>Stats Explained</h4>
            <ul>
                <li><strong>Last Scan:</strong> Time of the most recent scan request</li>
                <li><strong>Status:</strong> Current scan state (Idle, Scanning, Error)</li>
            </ul>

            <h4>Scan workflow</h4>
            <p>This tool replicates the same scan process that runs automatically after completing a download modal - ensuring your new tracks are immediately available in your library!</p>
        `
    },
    'retag-tool': {
        title: 'Retag Tool',
        content: `
            <h4>What does this tool do?</h4>
            <p>The Retag Tool lets you fix metadata on files that have already been downloaded and processed. If an album was tagged with wrong metadata, you can search for the correct match and re-apply tags.</p>

            <h4>How it works</h4>
            <ul>
                <li>Browse your past downloads organized by artist</li>
                <li>Expand an album or single to see individual tracks</li>
                <li>Click <strong>Retag</strong> to search for the correct album match</li>
                <li>Select the right album and confirm &mdash; metadata and file paths are updated automatically</li>
            </ul>

            <h4>What gets updated?</h4>
            <ul>
                <li><strong>File tags:</strong> Title, artist, album, track number, genre, cover art</li>
                <li><strong>File paths:</strong> Files are moved/renamed to match new metadata (based on your path template)</li>
                <li><strong>Cover art:</strong> cover.jpg is updated in the album folder</li>
            </ul>

            <h4>Stats Explained</h4>
            <ul>
                <li><strong>Groups:</strong> Number of album/single download groups tracked</li>
                <li><strong>Tracks:</strong> Total individual track files tracked</li>
                <li><strong>Artists:</strong> Number of unique artists across all groups</li>
            </ul>

            <h4>Notes</h4>
            <ul>
                <li>Only album and single downloads are tracked (not playlists)</li>
                <li>Deleting a group from the list does <strong>not</strong> delete the files</li>
                <li>Only one retag operation can run at a time</li>
            </ul>
        `
    },
    'discover-page': {
        title: 'Discover Page Guide',
        content: `
            <h4>What is the Discover page?</h4>
            <p>The Discover page is your personalized music discovery hub. It uses your watchlist, library listening history, and <strong>MusicMap</strong> to surface new music, create curated playlists, and organize your collection in dynamic ways.</p>

            <h4>🎯 Hero Section (Featured Artists)</h4>
            <p>The rotating hero showcases <strong>similar artists</strong> discovered via MusicMap. These are artists you don't already have in your library but might enjoy based on your watchlist.</p>
            <ul>
                <li>Auto-rotates every 8 seconds through 10 featured artists</li>
                <li>Similar artists sourced from MusicMap and matched to Spotify</li>
                <li>Click arrows to navigate manually</li>
                <li>Add artists to watchlist or view their full discography</li>
                <li>Data refreshed when watchlist scanner runs</li>
            </ul>

            <h4>📀 Recent Releases</h4>
            <p>New albums from artists you're watching <strong>and their MusicMap similar artists</strong>. Cached from Spotify and updated during watchlist scans.</p>
            <ul>
                <li>Shows up to 20 recent albums</li>
                <li>Click any album to view tracks and add to wishlist</li>
                <li>Automatically filtered to show albums released in the last 90 days</li>
                <li>Includes both watchlist artists and similar artists from MusicMap</li>
            </ul>

            <h4>🍂 Seasonal Content (Auto-detected)</h4>
            <p>Seasonal albums and playlists that appear automatically based on the current season (Winter, Spring, Summer, Fall).</p>
            <ul>
                <li><strong>Seasonal Albums:</strong> Albums matching the current season's vibe</li>
                <li><strong>Seasonal Playlist:</strong> Curated playlist that refreshes with each season</li>
                <li>Only visible during the matching season</li>
                <li>Can download and sync to your media server</li>
            </ul>

            <h4>🎵 Fresh Tape (Release Radar)</h4>
            <p>Curated playlist of <strong>brand new releases</strong> from your discovery pool. Focuses on tracks released in the past 30 days.</p>
            <ul>
                <li>50 tracks, refreshed weekly by watchlist scanner</li>
                <li>Stays consistent until next update (not random)</li>
                <li>Download missing tracks or sync to media server</li>
                <li>Tracks from watchlist artists and MusicMap similar artists</li>
            </ul>

            <h4>📚 The Archives (Discovery Weekly)</h4>
            <p>Curated playlist from your <strong>entire discovery pool</strong> - a mix of new and catalog tracks from MusicMap discoveries.</p>
            <ul>
                <li>50 tracks, refreshed weekly by watchlist scanner</li>
                <li>Stays consistent until next update (not random)</li>
                <li>Broader selection than Fresh Tape (includes older releases)</li>
                <li>Download missing tracks or sync to media server</li>
            </ul>

            <h4>📊 Personalized Library Playlists</h4>
            <p>Playlists generated from <strong>your existing library</strong> using listening statistics:</p>
            <ul>
                <li><strong>Recently Added:</strong> Latest 50 tracks added to your library</li>
                <li><strong>Your Top 50:</strong> All-time most played tracks (requires play count data)</li>
                <li><strong>Forgotten Favorites:</strong> Tracks you loved but haven't played recently</li>
            </ul>

            <h4>🎲 Discovery Pool Playlists</h4>
            <p>Playlists generated from your <strong>discovery pool</strong> (tracks from watchlist/similar artists you don't own yet):</p>
            <ul>
                <li><strong>Popular Picks:</strong> High-popularity tracks (Spotify popularity 70+)</li>
                <li><strong>Hidden Gems:</strong> Underground discoveries (Spotify popularity &lt;40)</li>
                <li><strong>Discovery Shuffle:</strong> 50 random tracks - different every time you load</li>
                <li><strong>Familiar Favorites:</strong> Reliable, mid-popularity tracks (40-70)</li>
            </ul>

            <h4>🎨 Build a Playlist</h4>
            <p>Create custom playlists using <strong>MusicMap similar artists</strong> from seed artists you select.</p>
            <ul>
                <li>Search for any artist on Spotify (even if not in your library)</li>
                <li>Select 1-5 seed artists</li>
                <li>Choose playlist size: 25, 50, 75, or 100 tracks</li>
                <li>Uses cached MusicMap similar artists from your database</li>
                <li>Pulls albums from those similar artists to build the playlist</li>
                <li>Download and sync like any other discover playlist</li>
            </ul>

            <h4>🧠 ListenBrainz Playlists</h4>
            <p>Access playlists from your ListenBrainz account (requires ListenBrainz authentication).</p>
            <ul>
                <li><strong>Created For You:</strong> Playlists generated by ListenBrainz for you</li>
                <li><strong>Your Playlists:</strong> Playlists you've created on ListenBrainz</li>
                <li><strong>Collaborative:</strong> Collaborative playlists you're part of</li>
                <li>Cached locally for performance - click Refresh to update from ListenBrainz</li>
                <li>Click any playlist to view tracks and download/sync</li>
            </ul>

            <h4>⏰ Time Machine (Browse by Decade)</h4>
            <p>Explore your discovery pool organized by release decade.</p>
            <ul>
                <li>Dynamically generated tabs for decades with available content (1950s-2020s)</li>
                <li>Each decade shows up to 100 tracks from that era</li>
                <li>Great for discovering older catalog releases from your favorite artists</li>
            </ul>

            <h4>🎵 Browse by Genre</h4>
            <p>Explore your discovery pool filtered by music genre.</p>
            <ul>
                <li>Shows top genres from your discovery pool</li>
                <li>Click any genre tab to see up to 100 tracks in that genre</li>
                <li>Genres sourced from Spotify metadata</li>
            </ul>

            <h4>💾 What is the Discovery Pool?</h4>
            <p>The <strong>discovery pool</strong> is a database of tracks from:</p>
            <ul>
                <li>Artists in your watchlist</li>
                <li>Similar artists found via <strong>MusicMap</strong></li>
                <li>Populated during watchlist scanner runs (scrapes music-map.com, matches to Spotify)</li>
                <li>Filtered to exclude tracks already in your library</li>
                <li>Used to generate Fresh Tape, The Archives, and discovery pool playlists</li>
                <li>Caches up to 50 top similar artists across your watchlist</li>
            </ul>

            <h4>🗺️ How MusicMap Integration Works</h4>
            <p>SoulSync uses <strong>MusicMap</strong> (music-map.com) instead of Spotify's recommendation API to find similar artists:</p>
            <ul>
                <li>During watchlist scans, each watchlist artist is looked up on MusicMap</li>
                <li>MusicMap's artist similarity graph is scraped to find related artists</li>
                <li>Similar artist names are matched to Spotify IDs</li>
                <li>Up to 10 similar artists per watchlist artist are cached (refreshed every 30 days)</li>
                <li>These cached similar artists power all discovery features</li>
                <li>This approach gives you more diverse, community-driven recommendations</li>
            </ul>

            <h4>⬇️ Download & Sync Features</h4>
            <p>Most discover playlists support two actions:</p>
            <ul>
                <li><strong>Download:</strong> Opens modal to match tracks to Soulseek and add to download queue</li>
                <li><strong>Sync:</strong> Downloads tracks and automatically transfers them to your media server</li>
                <li>Sync progress persists - you can close the page and it continues in the background</li>
                <li>Sync status shows: ✓ completed, ⏳ pending, ✗ failed</li>
            </ul>

            <h4>🔄 When is data refreshed?</h4>
            <ul>
                <li><strong>MusicMap Similar Artists:</strong> Fetched during watchlist scans, cached for 30 days</li>
                <li><strong>Hero, Recent Releases, Fresh Tape, The Archives:</strong> Updated during watchlist scanner runs (Dashboard page)</li>
                <li><strong>Discovery Pool:</strong> Fully refreshed every 24 hours during watchlist scans (50 top similar artists, 10 albums each)</li>
                <li><strong>Seasonal Content:</strong> Auto-detected based on current date</li>
                <li><strong>Personalized Library Playlists:</strong> Generated on-demand from current library data</li>
                <li><strong>Discovery Pool Playlists:</strong> Generated on-demand from current discovery pool</li>
                <li><strong>Build a Playlist:</strong> Generated on-demand from cached MusicMap similar artists</li>
                <li><strong>ListenBrainz:</strong> Cached locally, manually refreshed via Refresh button</li>
                <li><strong>Time Machine & Genre:</strong> Generated on-demand from current discovery pool</li>
            </ul>

            <h4>💡 Pro Tips</h4>
            <ul>
                <li>Curated playlists (Fresh Tape, The Archives) stay consistent until next watchlist scan - great for weekly listening routines</li>
                <li>Discovery Shuffle changes every page load - perfect when you want spontaneous recommendations</li>
                <li>Use Build a Playlist to explore artists not in your watchlist (if seed artist isn't in watchlist, MusicMap data must be cached first)</li>
                <li>The discovery pool only includes tracks you <strong>don't own yet</strong> - download them to build your collection!</li>
                <li>Sync feature is ideal for batch downloading entire playlists to your media server</li>
                <li>MusicMap provides more diverse recommendations than Spotify's algorithm - expect deeper cuts and underground artists!</li>
                <li>Add more artists to your watchlist to expand your discovery pool with their MusicMap similar artists</li>
            </ul>
        `
    },

    // ==================== Automation Trigger Help ====================

    'auto-schedule': {
        title: 'Schedule Timer',
        content: `
            <h4>What is this trigger?</h4>
            <p>Runs your automation on a repeating interval — every X minutes, hours, or days.</p>

            <h4>Configuration</h4>
            <ul>
                <li><strong>Interval:</strong> How often to repeat (e.g. every 6 hours)</li>
                <li><strong>Unit:</strong> Minutes, Hours, or Days</li>
            </ul>

            <h4>When does it first run?</h4>
            <p>The timer starts when SoulSync boots. If the automation was previously scheduled, it resumes from where it left off.</p>

            <h4>Good for</h4>
            <ul>
                <li>Regular wishlist processing (every 30 minutes)</li>
                <li>Periodic database backups (every 12 hours)</li>
                <li>Any recurring maintenance task</li>
            </ul>
        `
    },
    'auto-daily_time': {
        title: 'Daily Time',
        content: `
            <h4>What is this trigger?</h4>
            <p>Runs your automation once per day at a specific time.</p>

            <h4>Configuration</h4>
            <ul>
                <li><strong>Time:</strong> The wall-clock time to run (e.g. 03:00 for 3 AM)</li>
            </ul>

            <h4>Good for</h4>
            <ul>
                <li>Nightly watchlist scans</li>
                <li>Off-peak database updates</li>
                <li>Daily backups at a consistent time</li>
            </ul>
        `
    },
    'auto-weekly_time': {
        title: 'Weekly Schedule',
        content: `
            <h4>What is this trigger?</h4>
            <p>Runs your automation on specific days of the week at a set time.</p>

            <h4>Configuration</h4>
            <ul>
                <li><strong>Days:</strong> Select one or more days (Mon–Sun)</li>
                <li><strong>Time:</strong> The time to run on those days</li>
            </ul>

            <h4>Good for</h4>
            <ul>
                <li>Weekend-only quality scans</li>
                <li>Weekly playlist refreshes</li>
                <li>Scheduled maintenance on quiet days</li>
            </ul>
        `
    },
    'auto-app_started': {
        title: 'App Started',
        content: `
            <h4>What is this trigger?</h4>
            <p>Fires once when SoulSync starts up. Useful for tasks you want to run on every boot.</p>

            <h4>Good for</h4>
            <ul>
                <li>Refreshing mirrored playlists on startup</li>
                <li>Running a quick database sync</li>
                <li>Sending a "SoulSync is online" notification</li>
            </ul>

            <h4>Note</h4>
            <p>This trigger fires only once per startup — it will not fire again until SoulSync is restarted.</p>
        `
    },
    'auto-track_downloaded': {
        title: 'Track Downloaded',
        content: `
            <h4>What is this trigger?</h4>
            <p>Fires every time a single track finishes downloading and post-processing (tagging, moving to library).</p>

            <h4>Conditions</h4>
            <p>You can filter which downloads trigger this automation:</p>
            <ul>
                <li><strong>Artist:</strong> Only fire for specific artists</li>
                <li><strong>Title:</strong> Match on track title</li>
                <li><strong>Album:</strong> Match on album name</li>
                <li><strong>Quality:</strong> Match on file format (FLAC, MP3, etc.)</li>
            </ul>

            <h4>Available variables for notifications</h4>
            <p><code>{artist}</code>, <code>{title}</code>, <code>{album}</code>, <code>{quality}</code></p>

            <h4>Note</h4>
            <p>This fires per-track, not per-album. For an album with 12 tracks, it fires 12 times. Use <strong>Batch Complete</strong> if you want one event per album.</p>
        `
    },
    'auto-batch_complete': {
        title: 'Batch Complete',
        content: `
            <h4>What is this trigger?</h4>
            <p>Fires when an entire album or playlist download batch finishes — all tracks in the batch are done (whether successful or failed).</p>

            <h4>Conditions</h4>
            <ul>
                <li><strong>Playlist name:</strong> Filter by the name of the album or playlist</li>
            </ul>

            <h4>Available variables for notifications</h4>
            <p><code>{playlist_name}</code>, <code>{total_tracks}</code>, <code>{completed_tracks}</code>, <code>{failed_tracks}</code></p>

            <h4>Good for</h4>
            <ul>
                <li>Triggering a media server scan after downloads finish</li>
                <li>Sending a notification when an album is fully downloaded</li>
                <li>Running a database update after new content arrives</li>
            </ul>
        `
    },
    'auto-watchlist_new_release': {
        title: 'New Release Found',
        content: `
            <h4>What is this trigger?</h4>
            <p>Fires when the watchlist scanner detects new music from an artist you're watching. This means a new album, EP, or single has been released that you don't already have.</p>

            <h4>Conditions</h4>
            <ul>
                <li><strong>Artist:</strong> Only fire for specific watched artists</li>
            </ul>

            <h4>Available variables for notifications</h4>
            <p><code>{artist}</code>, <code>{new_tracks}</code>, <code>{added_to_wishlist}</code></p>

            <h4>Good for</h4>
            <ul>
                <li>Getting notified when your favorite artists drop new music</li>
                <li>Auto-processing the wishlist immediately after new releases are found</li>
            </ul>
        `
    },
    'auto-playlist_synced': {
        title: 'Playlist Synced',
        content: `
            <h4>What is this trigger?</h4>
            <p>Fires after a mirrored playlist is synced to your media server (Plex, Jellyfin, or Navidrome). This means the playlist has been matched and created/updated on your server.</p>

            <h4>Conditions</h4>
            <ul>
                <li><strong>Playlist name:</strong> Only fire for specific playlists</li>
            </ul>

            <h4>Available variables for notifications</h4>
            <p><code>{playlist_name}</code>, <code>{total_tracks}</code>, <code>{matched_tracks}</code>, <code>{synced_tracks}</code>, <code>{failed_tracks}</code></p>
        `
    },
    'auto-playlist_changed': {
        title: 'Playlist Changed',
        content: `
            <h4>What is this trigger?</h4>
            <p>Fires when a mirrored playlist detects that the source playlist (on Spotify, Tidal, YouTube, etc.) has changed — tracks were added or removed.</p>

            <h4>Conditions</h4>
            <ul>
                <li><strong>Playlist name:</strong> Only fire for specific playlists</li>
            </ul>

            <h4>Available variables for notifications</h4>
            <p><code>{playlist_name}</code>, <code>{old_count}</code>, <code>{new_count}</code>, <code>{added}</code>, <code>{removed}</code></p>

            <h4>Good for</h4>
            <ul>
                <li>Auto-discovering new tracks after a playlist updates</li>
                <li>Auto-syncing the playlist to your media server</li>
                <li>Getting notified when your followed playlists change</li>
            </ul>
        `
    },
    'auto-discovery_completed': {
        title: 'Discovery Complete',
        content: `
            <h4>What is this trigger?</h4>
            <p>Fires when Spotify/iTunes metadata discovery finishes for a mirrored playlist. Discovery is the process of matching playlist tracks to official Spotify or iTunes metadata.</p>

            <h4>Conditions</h4>
            <ul>
                <li><strong>Playlist name:</strong> Only fire for specific playlists</li>
            </ul>

            <h4>Available variables for notifications</h4>
            <p><code>{playlist_name}</code>, <code>{total_tracks}</code>, <code>{discovered_count}</code>, <code>{failed_count}</code>, <code>{skipped_count}</code></p>

            <h4>Good for</h4>
            <ul>
                <li>Auto-syncing a playlist after discovery completes</li>
                <li>Getting notified about discovery results (how many matched vs failed)</li>
            </ul>
        `
    },
    'auto-wishlist_processing_completed': {
        title: 'Wishlist Processed',
        content: `
            <h4>What is this trigger?</h4>
            <p>Fires when the auto-wishlist processing batch finishes. This is the automated download cycle that searches Soulseek for wishlist tracks.</p>

            <h4>Available variables for notifications</h4>
            <p><code>{tracks_processed}</code>, <code>{tracks_found}</code>, <code>{tracks_failed}</code></p>
        `
    },
    'auto-watchlist_scan_completed': {
        title: 'Watchlist Scan Done',
        content: `
            <h4>What is this trigger?</h4>
            <p>Fires when the watchlist artist scan completes. The scan checks all watched artists for new releases and adds new tracks to your wishlist.</p>

            <h4>Available variables for notifications</h4>
            <p><code>{artists_scanned}</code>, <code>{new_tracks_found}</code>, <code>{tracks_added}</code></p>
        `
    },
    'auto-database_update_completed': {
        title: 'Database Updated',
        content: `
            <h4>What is this trigger?</h4>
            <p>Fires when the library database refresh finishes — either incremental or full. This means SoulSync's internal database has been synced with your media server.</p>

            <h4>Available variables for notifications</h4>
            <p><code>{total_artists}</code>, <code>{total_albums}</code>, <code>{total_tracks}</code></p>

            <h4>Good for</h4>
            <ul>
                <li>Running a quality scan after the database is refreshed</li>
                <li>Sending a summary notification with library stats</li>
            </ul>
        `
    },
    'auto-download_failed': {
        title: 'Download Failed',
        content: `
            <h4>What is this trigger?</h4>
            <p>Fires when a track permanently fails to download. This means all retry attempts and sources have been exhausted.</p>

            <h4>Conditions</h4>
            <ul>
                <li><strong>Artist:</strong> Only fire for specific artists</li>
                <li><strong>Title:</strong> Match on track title</li>
                <li><strong>Reason:</strong> Match on failure reason</li>
            </ul>

            <h4>Available variables for notifications</h4>
            <p><code>{artist}</code>, <code>{title}</code>, <code>{reason}</code></p>
        `
    },
    'auto-download_quarantined': {
        title: 'File Quarantined',
        content: `
            <h4>What is this trigger?</h4>
            <p>Fires when a downloaded file fails AcoustID verification and is moved to the quarantine folder. This means the audio fingerprint didn't match what was expected — the file might be the wrong song.</p>

            <h4>Conditions</h4>
            <ul>
                <li><strong>Artist:</strong> Only fire for specific artists</li>
                <li><strong>Title:</strong> Match on track title</li>
            </ul>

            <h4>Available variables for notifications</h4>
            <p><code>{artist}</code>, <code>{title}</code>, <code>{reason}</code></p>

            <h4>What is quarantine?</h4>
            <p>Files that fail audio fingerprint verification are moved to a quarantine folder instead of your library. This prevents wrong songs from polluting your collection. You can review quarantined files manually.</p>
        `
    },
    'auto-wishlist_item_added': {
        title: 'Wishlist Item Added',
        content: `
            <h4>What is this trigger?</h4>
            <p>Fires when a track is added to your wishlist — whether manually, by the quality scanner, or by the watchlist scan.</p>

            <h4>Conditions</h4>
            <ul>
                <li><strong>Artist:</strong> Only fire for specific artists</li>
                <li><strong>Title:</strong> Match on track title</li>
            </ul>

            <h4>Available variables for notifications</h4>
            <p><code>{artist}</code>, <code>{title}</code>, <code>{reason}</code></p>
        `
    },
    'auto-watchlist_artist_added': {
        title: 'Artist Watched',
        content: `
            <h4>What is this trigger?</h4>
            <p>Fires when an artist is added to your watchlist. Watched artists are periodically scanned for new releases.</p>

            <h4>Conditions</h4>
            <ul>
                <li><strong>Artist:</strong> Only fire for specific artists</li>
            </ul>

            <h4>Available variables for notifications</h4>
            <p><code>{artist}</code>, <code>{artist_id}</code></p>
        `
    },
    'auto-watchlist_artist_removed': {
        title: 'Artist Unwatched',
        content: `
            <h4>What is this trigger?</h4>
            <p>Fires when an artist is removed from your watchlist.</p>

            <h4>Conditions</h4>
            <ul>
                <li><strong>Artist:</strong> Only fire for specific artists</li>
            </ul>

            <h4>Available variables for notifications</h4>
            <p><code>{artist}</code>, <code>{artist_id}</code></p>
        `
    },
    'auto-import_completed': {
        title: 'Import Complete',
        content: `
            <h4>What is this trigger?</h4>
            <p>Fires when an album or track import operation finishes. Imports bring music from external sources into your library.</p>

            <h4>Conditions</h4>
            <ul>
                <li><strong>Artist:</strong> Only fire for specific artists</li>
                <li><strong>Album name:</strong> Match on album name</li>
            </ul>

            <h4>Available variables for notifications</h4>
            <p><code>{track_count}</code>, <code>{album_name}</code>, <code>{artist}</code></p>
        `
    },
    'auto-mirrored_playlist_created': {
        title: 'Playlist Mirrored',
        content: `
            <h4>What is this trigger?</h4>
            <p>Fires when a new playlist mirror is created — a playlist from Spotify, Tidal, YouTube, ListenBrainz, or Beatport is set up for mirroring.</p>

            <h4>Conditions</h4>
            <ul>
                <li><strong>Playlist name:</strong> Match on playlist name</li>
                <li><strong>Source:</strong> Match on platform (spotify, tidal, youtube, etc.)</li>
            </ul>

            <h4>Available variables for notifications</h4>
            <p><code>{playlist_name}</code>, <code>{source}</code>, <code>{track_count}</code></p>

            <h4>Good for</h4>
            <ul>
                <li>Auto-discovering tracks immediately after a new mirror is created</li>
                <li>Getting notified when new playlists are mirrored</li>
            </ul>
        `
    },
    'auto-quality_scan_completed': {
        title: 'Quality Scan Done',
        content: `
            <h4>What is this trigger?</h4>
            <p>Fires when the quality scanner finishes. The scanner identifies tracks below your quality preferences and adds them to your wishlist for re-downloading.</p>

            <h4>Available variables for notifications</h4>
            <p><code>{quality_met}</code>, <code>{low_quality}</code>, <code>{total_scanned}</code></p>
        `
    },
    'auto-duplicate_scan_completed': {
        title: 'Duplicate Scan Done',
        content: `
            <h4>What is this trigger?</h4>
            <p>Fires when the duplicate cleaner finishes scanning your output folder for duplicate audio files.</p>

            <h4>Available variables for notifications</h4>
            <p><code>{files_scanned}</code>, <code>{duplicates_found}</code>, <code>{space_freed}</code></p>
        `
    },
    'auto-library_scan_completed': {
        title: 'Library Scan Done',
        content: `
            <h4>What is this trigger?</h4>
            <p>Fires when a media server library scan is considered complete. This only happens after a <strong>Scan Library</strong> action was triggered — it cannot fire on its own.</p>

            <h4>How does it know the scan is done?</h4>
            <p>Your media server (Plex, Jellyfin, Navidrome) doesn't send a "scan finished" signal back to SoulSync. So after telling the server to scan, SoulSync waits <strong>approximately 5 minutes</strong> and then assumes the scan has finished. This is a generous estimate that works for most libraries.</p>

            <h4>Timing</h4>
            <p>From the moment a download finishes to when this trigger fires, expect roughly <strong>6-7 minutes</strong>:</p>
            <ol>
                <li>60 second debounce wait (groups multiple downloads together)</li>
                <li>Media server scan triggered</li>
                <li>~5 minute wait (assumed scan completion)</li>
                <li>This event fires</li>
            </ol>

            <h4>Default use</h4>
            <p>The system automation <strong>Auto-Update Database After Scan</strong> listens for this trigger to start an incremental database update, keeping your SoulSync library in sync with your media server.</p>

            <h4>Available variables</h4>
            <p><code>{server_type}</code> — which media server was scanned (plex, jellyfin, navidrome)</p>
        `
    },

    // ==================== Automation Action Help ====================

    'auto-process_wishlist': {
        title: 'Process Wishlist',
        content: `
            <h4>What does this action do?</h4>
            <p>Searches Soulseek for tracks in your wishlist and downloads them. This is the same process that runs automatically on a timer — this action lets you trigger it manually or chain it to events.</p>

            <h4>Configuration</h4>
            <ul>
                <li><strong>Category:</strong> Process all wishlist tracks, or only Albums/EPs, or only Singles</li>
            </ul>

            <h4>How it works</h4>
            <ol>
                <li>Picks tracks from the wishlist (alternating Albums and Singles cycles)</li>
                <li>Searches Soulseek for each track</li>
                <li>Downloads the best quality match found</li>
                <li>Tags and moves files to your library</li>
            </ol>
        `
    },
    'auto-scan_watchlist': {
        title: 'Scan Watchlist',
        content: `
            <h4>What does this action do?</h4>
            <p>Checks all watched artists for new releases you don't already have. New tracks are automatically added to your wishlist for downloading.</p>

            <h4>How it works</h4>
            <ol>
                <li>Goes through each artist in your watchlist</li>
                <li>Fetches their discography from Spotify</li>
                <li>Compares against your library to find missing releases</li>
                <li>Adds new tracks to your wishlist</li>
            </ol>
        `
    },
    'auto-scan_library': {
        title: 'Scan Library',
        content: `
            <h4>What does this action do?</h4>
            <p>Tells your media server (Plex, Jellyfin, or Navidrome) to scan its music library folder for new or changed files. This makes newly downloaded music appear in your media server.</p>

            <h4>How it works</h4>
            <ol>
                <li>A <strong>60 second debounce</strong> groups rapid requests — if multiple downloads finish close together, only one scan is triggered</li>
                <li>After the debounce, your media server is told to scan</li>
                <li>SoulSync waits <strong>~5 minutes</strong> (your media server doesn't report when it's finished, so this is an assumed completion time)</li>
                <li>The <strong>Library Scan Done</strong> event fires, which can trigger follow-up actions like a database update</li>
            </ol>

            <h4>Default use</h4>
            <p>The system automation <strong>Auto-Scan After Downloads</strong> uses this action to automatically scan your library when a batch download completes. You can disable that automation if you prefer to scan manually.</p>

            <h4>Note</h4>
            <p>Jellyfin and Navidrome often detect new files automatically, but the scan ensures nothing is missed.</p>
        `
    },
    'auto-refresh_mirrored': {
        title: 'Refresh Mirrored Playlist',
        content: `
            <h4>What does this action do?</h4>
            <p>Re-fetches a mirrored playlist from its source platform (Spotify, Tidal, YouTube, etc.) and updates the local mirror with any track changes.</p>

            <h4>Configuration</h4>
            <ul>
                <li><strong>Playlist:</strong> Select a specific mirrored playlist, or check "Refresh all" to update all mirrors</li>
            </ul>

            <h4>Good for</h4>
            <ul>
                <li>Keeping mirrors in sync with playlists that change frequently</li>
                <li>Detecting added/removed tracks on the source platform</li>
            </ul>
        `
    },
    'auto-sync_playlist': {
        title: 'Sync Playlist',
        content: `
            <h4>What does this action do?</h4>
            <p>Syncs a mirrored playlist to your media server. It matches discovered tracks against your library and creates or updates the playlist on Plex, Jellyfin, or Navidrome.</p>

            <h4>Configuration</h4>
            <ul>
                <li><strong>Playlist:</strong> Select which mirrored playlist to sync</li>
            </ul>

            <h4>Prerequisites</h4>
            <p>Tracks should be discovered first (matched to Spotify/iTunes metadata) before syncing. Undiscovered tracks will be skipped.</p>
        `
    },
    'auto-discover_playlist': {
        title: 'Discover Playlist',
        content: `
            <h4>What does this action do?</h4>
            <p>Finds official Spotify or iTunes metadata for tracks in a mirrored playlist. This is required before syncing — it matches each track to a known release so it can be found in your library.</p>

            <h4>Configuration</h4>
            <ul>
                <li><strong>Playlist:</strong> Select a specific playlist, or check "Discover all" to process all mirrored playlists</li>
            </ul>

            <h4>How it works</h4>
            <ol>
                <li>Takes each track name and artist from the mirror</li>
                <li>Searches Spotify (or iTunes as fallback) for a match</li>
                <li>Stores the best match with confidence score in the discovery cache</li>
                <li>Already-discovered tracks are skipped for efficiency</li>
            </ol>
        `
    },
    'auto-playlist_pipeline': {
        title: 'Playlist Pipeline',
        content: `
            <h4>What does this action do?</h4>
            <p>Runs the full playlist lifecycle in one automation — no signal wiring needed. Executes four phases sequentially:</p>
            <ol>
                <li><strong>Refresh</strong> — Re-fetches playlist tracks from the source platform (Spotify, Tidal, YouTube, Deezer)</li>
                <li><strong>Discover</strong> — Matches each track to official metadata (Spotify/iTunes/Deezer IDs)</li>
                <li><strong>Sync</strong> — Pushes the playlist to your media server (Plex, Jellyfin, Navidrome)</li>
                <li><strong>Download Missing</strong> — Queues unmatched tracks to the wishlist for automatic download</li>
            </ol>

            <h4>Configuration</h4>
            <ul>
                <li><strong>Playlist:</strong> Select a specific mirrored playlist, or check "Process all" to run the pipeline for every mirrored playlist</li>
                <li><strong>Skip wishlist:</strong> Check this to skip the download phase (useful if you only want to sync, not download)</li>
            </ul>

            <h4>How the re-sync loop works</h4>
            <p>Set this on a schedule (e.g., every 6 hours). Between runs, the wishlist processor downloads missing tracks in the background. On the next pipeline run, those newly downloaded tracks will match during the sync phase — so your server playlist gets more complete with each cycle until fully synced.</p>

            <h4>Replaces</h4>
            <p>This single automation replaces the 4-automation signal chain pattern (Refresh → signal → Discover → signal → Sync → signal → Download). No signals, no chaining, no room for misconfiguration.</p>
        `
    },
    'auto-notify_only': {
        title: 'Notify Only',
        content: `
            <h4>What does this action do?</h4>
            <p>Nothing — it performs no action. It just passes the event data through to the notification step.</p>

            <h4>Good for</h4>
            <ul>
                <li>Getting notified about events without taking any automated action</li>
                <li>Monitoring what's happening in SoulSync (downloads, failures, changes)</li>
                <li>Pair with any event trigger + Discord/Telegram/Pushbullet notification</li>
            </ul>
        `
    },
    'auto-start_database_update': {
        title: 'Update Database',
        content: `
            <h4>What does this action do?</h4>
            <p>Refreshes SoulSync's internal library database by syncing with your media server (Plex, Jellyfin, or Navidrome).</p>

            <h4>Configuration</h4>
            <ul>
                <li><strong>Full refresh:</strong> When checked, completely rebuilds the database from scratch. When unchecked, only scans for new content (faster).</li>
            </ul>
        `
    },
    'auto-run_duplicate_cleaner': {
        title: 'Run Duplicate Cleaner',
        content: `
            <h4>What does this action do?</h4>
            <p>Scans your output folder for duplicate audio files (same filename, different format) and removes the lower-quality version. For example, if you have both <code>Song.flac</code> and <code>Song.mp3</code>, the MP3 is removed.</p>

            <h4>Safety</h4>
            <p>Removed files are moved to a <code>deleted/</code> subfolder, not permanently deleted. You can recover them if needed.</p>
        `
    },
    'auto-clear_quarantine': {
        title: 'Clear Quarantine',
        content: `
            <h4>What does this action do?</h4>
            <p>Permanently deletes all files in the quarantine folder. Quarantined files are downloads that failed AcoustID audio fingerprint verification — they might be the wrong song.</p>

            <h4>Warning</h4>
            <p>This permanently deletes files. Make sure you've reviewed quarantined files before setting up an automation for this.</p>
        `
    },
    'auto-cleanup_wishlist': {
        title: 'Clean Up Wishlist',
        content: `
            <h4>What does this action do?</h4>
            <p>Removes duplicate entries and tracks you already own from your wishlist. Keeps the wishlist lean by removing items that no longer need downloading.</p>
        `
    },
    'auto-update_discovery_pool': {
        title: 'Update Discovery Pool',
        content: `
            <h4>What does this action do?</h4>
            <p>Refreshes the discovery pool with new tracks from your mirrored playlists. The discovery pool tracks which playlist tracks have been successfully matched and which ones failed.</p>
        `
    },
    'auto-start_quality_scan': {
        title: 'Run Quality Scan',
        content: `
            <h4>What does this action do?</h4>
            <p>Scans your library for tracks that don't meet your quality preferences (e.g., MP3 when you prefer FLAC). Low-quality tracks are matched to Spotify and added to your wishlist for re-downloading in better quality.</p>

            <h4>Configuration</h4>
            <ul>
                <li><strong>Scope:</strong> Scan only watchlist artists (faster) or your entire library (thorough)</li>
            </ul>
        `
    },
    'auto-backup_database': {
        title: 'Backup Database',
        content: `
            <h4>What does this action do?</h4>
            <p>Creates a timestamped backup of SoulSync's SQLite database. Uses the SQLite backup API for a safe hot-copy while the app is running.</p>

            <h4>Retention</h4>
            <p>Keeps the last 5 backups automatically. Older backups are cleaned up to save disk space.</p>

            <h4>Good for</h4>
            <ul>
                <li>Nightly automated backups</li>
                <li>Pre-update safety backups</li>
                <li>Peace of mind for your library data</li>
            </ul>
        `
    },
    'auto-refresh_beatport_cache': {
        title: 'Refresh Beatport Cache',
        content: `
            <h4>What does this action do?</h4>
            <p>Scrapes the Beatport homepage for top charts and caches the results locally. Keeps the Beatport charts page loading instantly without needing to scrape on every visit.</p>

            <h4>Cache duration</h4>
            <p>Cache lasts 24 hours. This action refreshes it early so it's always warm when you visit the charts page.</p>

            <h4>Good for</h4>
            <ul>
                <li>Keeping Beatport charts available instantly</li>
                <li>Scheduling daily cache refreshes (e.g. every morning)</li>
            </ul>
        `
    },
    'auto-clean_search_history': {
        title: 'Clean Search History',
        content: `
            <h4>What does this action do?</h4>
            <p>Removes old search queries from Soulseek. This keeps your search history clean and prevents buildup over time.</p>

            <h4>Good for</h4>
            <ul>
                <li>Periodic housekeeping</li>
                <li>Keeping Soulseek search history tidy</li>
            </ul>
        `
    },
    'auto-clean_completed_downloads': {
        title: 'Clean Completed Downloads',
        content: `
            <h4>What does this action do?</h4>
            <p>Clears completed downloads from the transfer list and removes any empty directories left behind in the import folder.</p>

            <h4>Good for</h4>
            <ul>
                <li>Automatic cleanup after batch downloads</li>
                <li>Preventing import folder clutter</li>
                <li>Chaining after a batch complete trigger</li>
            </ul>
        `
    },
    'auto-full_cleanup': {
        title: 'Full Cleanup',
        content: `
            <h4>What does this action do?</h4>
            <p>Runs all housekeeping tasks in a single sweep:</p>
            <ol>
                <li><strong>Clear Quarantine</strong> — permanently deletes all quarantined files</li>
                <li><strong>Clear Download Queue</strong> — removes completed, errored, and cancelled downloads from Soulseek</li>
                <li><strong>Sweep Empty Directories</strong> — removes empty folders left behind in the input directory</li>
                <li><strong>Sweep Import Folder</strong> — removes empty directories from the import folder</li>
                <li><strong>Clean Search History</strong> — trims old Soulseek search queries</li>
            </ol>

            <h4>Safety</h4>
            <p>Skips download queue cleanup if batches are actively downloading or post-processing. Each step runs independently — a failure in one step won't stop the others.</p>

            <h4>Good for</h4>
            <ul>
                <li>Scheduled housekeeping every 12 hours</li>
                <li>Keeping disk usage and queue clutter under control</li>
                <li>Running after large batch downloads complete</li>
            </ul>
        `
    },
    'auto-deep_scan_library': {
        title: 'Deep Scan Library',
        content: `
            <h4>What does this action do?</h4>
            <p>Walks your entire media server library and compares it against SoulSync's database. Adds any new tracks found and removes stale entries that no longer exist on the server.</p>

            <h4>How is this different from Database Update?</h4>
            <ul>
                <li><strong>Database Update:</strong> Incremental — only looks for new artists/albums added since last update</li>
                <li><strong>Deep Scan:</strong> Full comparison — checks every track on the server against the database, catches anything missed</li>
            </ul>

            <h4>Safety</h4>
            <ul>
                <li>Never overwrites existing enrichment data (genres, Spotify IDs, artwork)</li>
                <li>Only inserts tracks that don't already exist in the database</li>
                <li>Stale track removal has a 50% safety threshold — if more than half the library appears missing, removal is skipped</li>
            </ul>
        `
    },

    // ==================== Notification/Then-Action Help ====================

    'auto-discord_webhook': {
        title: 'Discord Webhook',
        content: `
            <h4>What does this then-action do?</h4>
            <p>Sends a notification to a Discord channel via webhook when the automation's action completes.</p>

            <h4>Configuration</h4>
            <ul>
                <li><strong>Webhook URL:</strong> The Discord webhook URL for your channel (found in Channel Settings → Integrations → Webhooks)</li>
                <li><strong>Message Template:</strong> Custom message with variable placeholders</li>
            </ul>

            <h4>Available variables</h4>
            <p>Use these in your message template:</p>
            <ul>
                <li><code>{time}</code> — When the automation ran</li>
                <li><code>{name}</code> — Automation name</li>
                <li><code>{run_count}</code> — How many times this automation has run</li>
                <li><code>{status}</code> — Result status of the action</li>
            </ul>
        `
    },
    'auto-pushbullet': {
        title: 'Pushbullet',
        content: `
            <h4>What does this then-action do?</h4>
            <p>Sends a push notification to your phone or desktop via Pushbullet when the automation's action completes.</p>

            <h4>Configuration</h4>
            <ul>
                <li><strong>API Key:</strong> Your Pushbullet access token (found in Pushbullet Settings → Access Tokens)</li>
                <li><strong>Message Template:</strong> Custom message with variable placeholders</li>
            </ul>

            <h4>Available variables</h4>
            <p>Use these in your message template:</p>
            <ul>
                <li><code>{time}</code> — When the automation ran</li>
                <li><code>{name}</code> — Automation name</li>
                <li><code>{run_count}</code> — How many times this automation has run</li>
                <li><code>{status}</code> — Result status of the action</li>
            </ul>
        `
    },
    'auto-telegram': {
        title: 'Telegram',
        content: `
            <h4>What does this then-action do?</h4>
            <p>Sends a message to a Telegram chat via bot when the automation's action completes.</p>

            <h4>Configuration</h4>
            <ul>
                <li><strong>Bot Token:</strong> Your Telegram bot token (from @BotFather)</li>
                <li><strong>Chat ID:</strong> The chat/group ID to send messages to</li>
                <li><strong>Message Template:</strong> Custom message with variable placeholders</li>
            </ul>

            <h4>Available variables</h4>
            <p>Use these in your message template:</p>
            <ul>
                <li><code>{time}</code> — When the automation ran</li>
                <li><code>{name}</code> — Automation name</li>
                <li><code>{run_count}</code> — How many times this automation has run</li>
                <li><code>{status}</code> — Result status of the action</li>
            </ul>
        `
    },

    'auto-webhook': {
        title: 'Webhook (POST)',
        content: `
            <h4>What does this then-action do?</h4>
            <p>Sends an HTTP POST request with a JSON payload to any URL when the automation's action completes. Use it to integrate with Gotify, Home Assistant, Slack, n8n, or any service that accepts webhooks.</p>

            <h4>Configuration</h4>
            <ul>
                <li><strong>URL:</strong> The endpoint to POST to (e.g. <code>https://gotify.example.com/message?token=xxx</code>)</li>
                <li><strong>Headers:</strong> Optional custom headers, one per line in <code>Key: Value</code> format. Useful for auth tokens.</li>
                <li><strong>Custom Message:</strong> Optional message with variable placeholders. Added as a "message" field in the JSON payload.</li>
            </ul>

            <h4>JSON payload</h4>
            <p>The POST body always includes all event variables as JSON fields:</p>
            <pre style="background:rgba(255,255,255,0.05);padding:8px;border-radius:6px;font-size:11px;">{"time": "2026-04-02 ...", "name": "My Automation", "status": "success", ...}</pre>

            <h4>Available variables</h4>
            <p>Use these in your message or header values:</p>
            <ul>
                <li><code>{time}</code> — When the automation ran</li>
                <li><code>{name}</code> — Automation name</li>
                <li><code>{run_count}</code> — How many times this automation has run</li>
                <li><code>{status}</code> — Result status of the action</li>
            </ul>
        `
    },

    // ==================== Signal System Help ====================

    'auto-signal_received': {
        title: 'Signal Received',
        content: `
            <h4>What is this trigger?</h4>
            <p>Fires when another automation sends a named signal using the <strong>Fire Signal</strong> then-action. This lets you chain automations together — one automation finishes and wakes up another.</p>

            <h4>Configuration</h4>
            <ul>
                <li><strong>Signal Name:</strong> The name to listen for (e.g. <code>library_ready</code>, <code>scan_done</code>). Must match the name used in the Fire Signal action.</li>
            </ul>

            <h4>How chaining works</h4>
            <ol>
                <li><strong>Automation A:</strong> Trigger = Batch Complete, Action = Scan Library, Then = Fire Signal "scan_done"</li>
                <li><strong>Automation B:</strong> Trigger = Signal Received "scan_done", Action = Update Database</li>
                <li>When a download finishes → A scans library → fires signal → B wakes up → updates database</li>
            </ol>

            <h4>Safety</h4>
            <ul>
                <li>Circular signal chains are detected and blocked when you save</li>
                <li>Maximum chain depth of 5 levels to prevent runaway cascades</li>
                <li>Same signal can only fire once every 10 seconds (cooldown)</li>
            </ul>

            <h4>Signal names</h4>
            <p>Use descriptive lowercase names with underscores: <code>library_ready</code>, <code>scan_complete</code>, <code>downloads_done</code>. Existing signal names from other automations appear as suggestions.</p>
        `
    },
    'auto-fire_signal': {
        title: 'Fire Signal',
        content: `
            <h4>What does this then-action do?</h4>
            <p>Fires a named signal after the automation's action completes. Any other automation with a <strong>Signal Received</strong> trigger listening for this signal name will wake up and run.</p>

            <h4>Configuration</h4>
            <ul>
                <li><strong>Signal Name:</strong> The signal to fire (e.g. <code>library_ready</code>). Use the same name in a Signal Received trigger on another automation to connect them.</li>
            </ul>

            <h4>Use cases</h4>
            <ul>
                <li><strong>Multi-step workflows:</strong> Scan library → fire signal → update database → fire signal → send notification</li>
                <li><strong>Fan-out:</strong> One signal can trigger multiple automations simultaneously</li>
                <li><strong>Decoupled logic:</strong> Keep each automation simple with one job, chain them via signals</li>
            </ul>

            <h4>Combining with notifications</h4>
            <p>You can add up to 3 then-actions per automation. For example: Fire Signal + Discord notification + Telegram notification — all run after the action completes.</p>
        `
    },
    'backup-manager': {
        title: 'Backup Manager',
        content: `
            <h4>What does this tool do?</h4>
            <p>The Backup Manager lets you create, view, download, restore, and delete database backups directly from the dashboard.</p>

            <h4>Features</h4>
            <ul>
                <li><strong>Backup Now:</strong> Create an instant backup of the current database using SQLite's hot-copy API</li>
                <li><strong>Download:</strong> Download any backup file to your local machine</li>
                <li><strong>Restore:</strong> Roll back the database to a previous backup state</li>
                <li><strong>Delete:</strong> Remove old backups you no longer need</li>
            </ul>

            <h4>Auto-Backups</h4>
            <p>SoulSync automatically creates a backup every 3 days via the automation engine. Up to 5 rolling backups are kept (oldest are removed when the limit is exceeded).</p>

            <h4>Restore Safety</h4>
            <p>When you restore from a backup, a <strong>safety backup</strong> of your current database is created first. This means you can always undo a restore if something goes wrong.</p>

            <h4>Stats Explained</h4>
            <ul>
                <li><strong>Last Backup:</strong> When the most recent backup was created</li>
                <li><strong>Backups:</strong> Total number of backup files available</li>
                <li><strong>Latest Size:</strong> Size of the most recent backup</li>
                <li><strong>DB Size:</strong> Current size of the live database</li>
            </ul>
        `
    },
    'metadata-cache': {
        title: 'Metadata Cache',
        content: `
            <h4>What is this?</h4>
            <p>The Metadata Cache stores every API response from Spotify and iTunes so SoulSync can reuse them instead of making duplicate API calls. This reduces rate limit pressure and speeds up lookups.</p>

            <h4>How it works</h4>
            <p>When SoulSync fetches artist, album, or track data from Spotify or iTunes, the response is cached locally. The next time the same data is needed, it's served from cache instantly — no API call required. Cached data is even served during Spotify rate limit bans.</p>

            <h4>Browsing the Cache</h4>
            <p>Click <strong>Browse Cache</strong> to explore all cached metadata. You can filter by entity type (artists, albums, tracks), search by name, filter by source (Spotify/iTunes), and sort by different fields. Click any card to see full details including the raw API response.</p>

            <h4>Cache Management</h4>
            <ul>
                <li><strong>TTL:</strong> Entities expire after 30 days, search mappings after 7 days</li>
                <li><strong>Eviction:</strong> Expired entries are automatically cleaned up</li>
                <li><strong>Clear:</strong> You can clear the entire cache or filter by source/type</li>
            </ul>

            <h4>Stats Explained</h4>
            <ul>
                <li><strong>Artists:</strong> Total cached artist profiles</li>
                <li><strong>Albums:</strong> Total cached album records</li>
                <li><strong>Tracks:</strong> Total cached track records</li>
                <li><strong>Hits:</strong> Total number of times cached data was served instead of making an API call</li>
            </ul>
        `
    }
};

function initializeToolHelpButtons() {
    const helpButtons = document.querySelectorAll('.tool-help-button');
    const modal = document.getElementById('tool-help-modal');
    const closeButton = modal.querySelector('.tool-help-modal-close');

    // Attach click handlers to all help buttons
    helpButtons.forEach(button => {
        button.addEventListener('click', (e) => {
            e.stopPropagation();
            const toolId = button.getAttribute('data-tool');
            openToolHelpModal(toolId);
        });
    });

    // Close modal when clicking close button
    closeButton.addEventListener('click', closeToolHelpModal);

    // Close modal when clicking outside content
    modal.addEventListener('click', (e) => {
        if (e.target === modal) {
            closeToolHelpModal();
        }
    });

    // Close modal on Escape key
    document.addEventListener('keydown', (e) => {
        if (e.key === 'Escape' && modal.classList.contains('active')) {
            closeToolHelpModal();
        }
    });
}

function openToolHelpModal(toolId) {
    const modal = document.getElementById('tool-help-modal');
    const titleElement = document.getElementById('tool-help-modal-title');
    const bodyElement = document.getElementById('tool-help-modal-body');

    const helpData = TOOL_HELP_CONTENT[toolId];
    if (!helpData) {
        console.warn(`No help content found for tool: ${toolId}`);
        return;
    }

    titleElement.textContent = helpData.title;
    bodyElement.innerHTML = helpData.content;

    modal.classList.add('active');
    document.body.style.overflow = 'hidden'; // Prevent background scrolling
}

function closeToolHelpModal() {
    const modal = document.getElementById('tool-help-modal');
    if (modal) modal.classList.remove('active');
    document.body.style.overflow = ''; // Restore scrolling
}
// Global Escape key handler for tool help modal (works even if Tools page wasn't visited)
document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') {
        const modal = document.getElementById('tool-help-modal');
        if (modal && modal.classList.contains('active')) closeToolHelpModal();
    }
});

// ===============================
// == RETAG TOOL FUNCTIONS      ==
// ===============================

let retagStatusInterval = null;
let retagCurrentGroupId = null;

async function loadRetagStats() {
    try {
        const response = await fetch('/api/retag/stats');
        const data = await response.json();
        if (data.success !== false) {
            const groupsEl = document.getElementById('retag-stat-groups');
            const tracksEl = document.getElementById('retag-stat-tracks');
            const artistsEl = document.getElementById('retag-stat-artists');
            if (groupsEl) groupsEl.textContent = data.groups || 0;
            if (tracksEl) tracksEl.textContent = data.tracks || 0;
            if (artistsEl) artistsEl.textContent = data.artists || 0;
        }
    } catch (e) {
        console.warn('Failed to load retag stats:', e);
    }
}

async function openRetagModal() {
    const modal = document.getElementById('retag-modal');
    if (!modal) return;
    modal.style.display = 'flex';
    document.body.style.overflow = 'hidden';

    // Reset batch bar and clear-all button
    const batchBar = document.getElementById('retag-batch-bar');
    if (batchBar) batchBar.style.display = 'none';
    const clearBtn = document.getElementById('retag-clear-all-btn');
    if (clearBtn) { clearBtn.textContent = 'Clear All'; clearBtn.dataset.confirming = ''; clearBtn.style.background = ''; }

    const body = document.getElementById('retag-modal-body');
    body.innerHTML = '<div class="retag-loading">Loading downloads...</div>';

    try {
        const response = await fetch('/api/retag/groups');
        const data = await response.json();
        if (!data.success || !data.groups || data.groups.length === 0) {
            body.innerHTML = '<p class="retag-empty">No downloads recorded yet. Downloads will appear here after completing album or single downloads.</p>';
            if (clearBtn) clearBtn.style.display = 'none';
            return;
        }
        if (clearBtn) clearBtn.style.display = '';
        renderRetagGroups(data.groups, body);
    } catch (e) {
        body.innerHTML = '<p class="retag-error">Failed to load downloads.</p>';
    }
}

function closeRetagModal() {
    const modal = document.getElementById('retag-modal');
    if (modal) modal.style.display = 'none';
    document.body.style.overflow = '';
}

function renderRetagGroups(groups, container) {
    // Group by artist_name
    const byArtist = {};
    groups.forEach(g => {
        const artist = g.artist_name || 'Unknown Artist';
        if (!byArtist[artist]) byArtist[artist] = [];
        byArtist[artist].push(g);
    });

    let html = '';
    Object.keys(byArtist).sort((a, b) => a.localeCompare(b)).forEach(artist => {
        html += `<div class="retag-artist-section">
            <h3 class="retag-artist-name">${escapeHtml(artist)}</h3>
            <div class="retag-artist-groups">`;

        byArtist[artist].forEach(group => {
            const imgHtml = group.image_url
                ? `<img class="retag-group-image" src="${group.image_url}" alt="" loading="lazy">`
                : '<div class="retag-group-image-placeholder"></div>';
            const trackCount = group.track_count || group.total_tracks || 0;
            const typeLabel = (group.group_type || 'album').charAt(0).toUpperCase() + (group.group_type || 'album').slice(1);
            const releaseDate = group.release_date ? group.release_date.substring(0, 4) : '';
            const defaultQuery = (artist + ' ' + (group.album_name || '')).trim();

            html += `<div class="retag-group-card" data-group-id="${group.id}">
                <div class="retag-group-header" data-group-id="${group.id}" data-default-query="${escapeHtml(defaultQuery).replace(/"/g, '&quot;')}">
                    <label class="retag-group-checkbox">
                        <input type="checkbox" class="retag-select-cb" data-group-id="${group.id}">
                        <span class="retag-checkbox-custom"></span>
                    </label>
                    ${imgHtml}
                    <div class="retag-group-info">
                        <span class="retag-group-album">${escapeHtml(group.album_name || 'Unknown')}</span>
                        <span class="retag-group-meta">${typeLabel}${releaseDate ? ' \u00b7 ' + releaseDate : ''} \u00b7 ${trackCount} track${trackCount !== 1 ? 's' : ''}</span>
                    </div>
                    <button class="retag-group-btn" data-group-id="${group.id}" title="Re-tag with different album">Retag</button>
                    <div class="retag-group-delete-area" id="retag-delete-area-${group.id}">
                        <button class="retag-group-delete-btn" data-group-id="${group.id}" title="Remove from list">&times;</button>
                    </div>
                </div>
                <div class="retag-group-tracks" id="retag-tracks-${group.id}" style="display:none;">
                    <div class="retag-tracks-loading">Loading tracks...</div>
                </div>
            </div>`;
        });

        html += `</div></div>`;
    });

    container.innerHTML = html;
    _attachRetagDelegation(container);
}

function _attachRetagDelegation(container) {
    // Single click handler for all retag group interactions
    container.addEventListener('click', (e) => {
        const target = e.target;

        // Skip checkbox wrapper clicks — handled by change listener
        if (target.closest('.retag-group-checkbox')) return;

        // Retag button
        const retagBtn = target.closest('.retag-group-btn');
        if (retagBtn) {
            e.stopPropagation();
            const groupId = parseInt(retagBtn.dataset.groupId);
            const header = retagBtn.closest('.retag-group-header');
            const defaultQuery = header ? header.dataset.defaultQuery || '' : '';
            openRetagSearch(groupId, defaultQuery);
            return;
        }

        // Delete confirm buttons (dynamically injected)
        const confirmYes = target.closest('.retag-confirm-yes');
        if (confirmYes) {
            e.stopPropagation();
            const card = confirmYes.closest('.retag-group-card');
            if (card) executeRetagGroupDelete(parseInt(card.dataset.groupId));
            return;
        }
        const confirmNo = target.closest('.retag-confirm-no');
        if (confirmNo) {
            e.stopPropagation();
            const card = confirmNo.closest('.retag-group-card');
            if (card) cancelRetagDeleteConfirm(parseInt(card.dataset.groupId));
            return;
        }

        // Delete button
        const delBtn = target.closest('.retag-group-delete-btn');
        if (delBtn) {
            e.stopPropagation();
            showRetagDeleteConfirm(parseInt(delBtn.dataset.groupId));
            return;
        }

        // Group header click (expand/collapse)
        const header = target.closest('.retag-group-header');
        if (header) {
            toggleRetagGroup(parseInt(header.dataset.groupId));
            return;
        }
    });

    // Separate change handler for checkboxes
    container.addEventListener('change', (e) => {
        if (e.target.classList.contains('retag-select-cb')) {
            updateRetagBatchBar();
        }
    });
}

async function toggleRetagGroup(groupId) {
    const tracksDiv = document.getElementById(`retag-tracks-${groupId}`);
    if (!tracksDiv) return;

    if (tracksDiv.style.display === 'none') {
        tracksDiv.style.display = 'block';
        if (tracksDiv.querySelector('.retag-tracks-loading')) {
            try {
                const response = await fetch(`/api/retag/groups/${groupId}/tracks`);
                const data = await response.json();
                if (data.success && data.tracks && data.tracks.length > 0) {
                    tracksDiv.innerHTML = data.tracks.map(t => {
                        const discPrefix = t.disc_number > 1 ? `${t.disc_number}-` : '';
                        const trackNum = t.track_number != null ? `${discPrefix}${String(t.track_number).padStart(2, '0')}` : '--';
                        return `<div class="retag-track-item">
                            <span class="retag-track-number">${trackNum}</span>
                            <span class="retag-track-title">${escapeHtml(t.title || 'Unknown')}</span>
                            <span class="retag-track-format">${(t.file_format || '').toUpperCase()}</span>
                        </div>`;
                    }).join('');
                } else {
                    tracksDiv.innerHTML = '<p class="retag-tracks-empty">No tracks found</p>';
                }
            } catch (e) {
                tracksDiv.innerHTML = '<p class="retag-tracks-empty">Failed to load tracks</p>';
            }
        }
    } else {
        tracksDiv.style.display = 'none';
    }
}

function openRetagSearch(groupId, defaultQuery) {
    retagCurrentGroupId = groupId;
    const modal = document.getElementById('retag-search-modal');
    if (!modal) return;
    modal.style.display = 'flex';

    const input = document.getElementById('retag-search-input');
    if (input) {
        input.value = defaultQuery || '';
        input.focus();
        if (defaultQuery) {
            searchRetagAlbums(defaultQuery);
        }
    }
}

function closeRetagSearch() {
    const modal = document.getElementById('retag-search-modal');
    if (modal) modal.style.display = 'none';
    retagCurrentGroupId = null;
}

let retagSearchTimeout = null;
document.addEventListener('DOMContentLoaded', () => {
    const retagSearchInput = document.getElementById('retag-search-input');
    if (retagSearchInput) {
        retagSearchInput.addEventListener('input', (e) => {
            clearTimeout(retagSearchTimeout);
            retagSearchTimeout = setTimeout(() => searchRetagAlbums(e.target.value), 400);
        });
        retagSearchInput.addEventListener('keydown', (e) => {
            if (e.key === 'Enter') {
                clearTimeout(retagSearchTimeout);
                searchRetagAlbums(e.target.value);
            }
        });
    }

    // Close retag modals on escape
    document.addEventListener('keydown', (e) => {
        if (e.key === 'Escape') {
            const searchModal = document.getElementById('retag-search-modal');
            if (searchModal && searchModal.style.display === 'flex') {
                closeRetagSearch();
                return;
            }
            const mainModal = document.getElementById('retag-modal');
            if (mainModal && mainModal.style.display === 'flex') {
                closeRetagModal();
            }
        }
    });

    // Close retag modal on overlay click
    const retagModal = document.getElementById('retag-modal');
    if (retagModal) {
        retagModal.addEventListener('click', (e) => {
            if (e.target === retagModal) closeRetagModal();
        });
    }
    const retagSearchModal = document.getElementById('retag-search-modal');
    if (retagSearchModal) {
        retagSearchModal.addEventListener('click', (e) => {
            if (e.target === retagSearchModal) closeRetagSearch();
        });
    }
});

async function searchRetagAlbums(query) {
    if (!query || !query.trim()) return;
    const resultsDiv = document.getElementById('retag-search-results');
    if (!resultsDiv) return;
    resultsDiv.innerHTML = '<div class="retag-search-loading">Searching...</div>';

    try {
        const response = await fetch(`/api/retag/search?q=${encodeURIComponent(query.trim())}`);
        const data = await response.json();
        if (data.success && data.albums && data.albums.length > 0) {
            resultsDiv.innerHTML = data.albums.map(a => {
                const imgHtml = a.image_url
                    ? `<img class="retag-result-image" src="${a.image_url}" alt="" loading="lazy">`
                    : '<div class="retag-result-image-placeholder"></div>';
                const typeLabel = (a.album_type || 'album').charAt(0).toUpperCase() + (a.album_type || 'album').slice(1);
                const releaseYear = a.release_date ? a.release_date.substring(0, 4) : '';
                return `<div class="retag-search-result" id="retag-result-${a.id}" onclick="showRetagConfirm(this, ${retagCurrentGroupId}, '${a.id}', '${escapeHtml(a.name).replace(/'/g, "\\'")}')">
                    ${imgHtml}
                    <div class="retag-result-info">
                        <span class="retag-result-name">${escapeHtml(a.name || 'Unknown')}</span>
                        <span class="retag-result-artist">${escapeHtml(a.artist || 'Unknown')}</span>
                        <span class="retag-result-meta">${typeLabel}${releaseYear ? ' \u00b7 ' + releaseYear : ''} \u00b7 ${a.total_tracks || 0} tracks</span>
                    </div>
                </div>`;
            }).join('');
        } else {
            resultsDiv.innerHTML = '<p class="retag-no-results">No albums found.</p>';
        }
    } catch (e) {
        resultsDiv.innerHTML = '<p class="retag-search-error">Search failed.</p>';
    }
}

/**
 * Show inline confirmation on a search result before retagging
 */
function showRetagConfirm(el, groupId, albumId, albumName) {
    // Clear any other confirming states
    document.querySelectorAll('.retag-search-result.retag-confirming').forEach(r => {
        r.classList.remove('retag-confirming');
        const bar = r.querySelector('.retag-result-confirm-bar');
        if (bar) bar.remove();
        r.onclick = r._originalOnclick || null;
    });

    el.classList.add('retag-confirming');
    el._originalOnclick = el.onclick;
    el.onclick = null; // Disable clicking the row again

    const confirmBar = document.createElement('div');
    confirmBar.className = 'retag-result-confirm-bar';
    confirmBar.innerHTML = `
        <span>Re-tag with "${escapeHtml(albumName)}"?</span>
        <div class="retag-result-confirm-actions">
            <button class="retag-result-confirm-yes" onclick="event.stopPropagation(); executeRetag(${groupId}, '${albumId}', '${albumName.replace(/'/g, "\\'")}')">Confirm</button>
            <button class="retag-result-confirm-cancel" onclick="event.stopPropagation(); cancelRetagConfirm(this)">Cancel</button>
        </div>
    `;
    el.appendChild(confirmBar);
}

function cancelRetagConfirm(cancelBtn) {
    const result = cancelBtn.closest('.retag-search-result');
    if (!result) return;
    result.classList.remove('retag-confirming');
    const bar = result.querySelector('.retag-result-confirm-bar');
    if (bar) bar.remove();
    if (result._originalOnclick) {
        result.onclick = result._originalOnclick;
    }
}

async function executeRetag(groupId, albumId, albumName) {

    closeRetagSearch();
    closeRetagModal();

    try {
        const response = await fetch('/api/retag/execute', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ group_id: groupId, album_id: albumId })
        });
        const data = await response.json();
        if (data.success) {
            showToast('Retag operation started', 'success');
            startRetagPolling();
        } else {
            showToast(`Error: ${data.error || 'Unknown error'}`, 'error');
        }
    } catch (e) {
        showToast('Failed to start retag operation', 'error');
    }
}

function startRetagPolling() {
    if (retagStatusInterval) return;
    retagStatusInterval = setInterval(checkRetagStatus, 1000);
    checkRetagStatus();
}

async function checkRetagStatus() {
    if (socketConnected) return; // WebSocket handles this
    try {
        const response = await fetch('/api/retag/status');
        const state = await response.json();
        updateRetagProgressUI(state);

        if (state.status === 'running' && !retagStatusInterval) {
            startRetagPolling();
        }

        if (state.status !== 'running' && retagStatusInterval) {
            clearInterval(retagStatusInterval);
            retagStatusInterval = null;
            if (state.status === 'finished') {
                showToast('Retag completed successfully', 'success');
                loadRetagStats();
            } else if (state.status === 'error') {
                showToast(`Retag error: ${state.error_message || 'Unknown error'}`, 'error');
            }
        }
    } catch (e) {
        // Ignore fetch errors during polling
    }
}

function updateRetagStatusFromData(data) {
    const prev = _lastToolStatus['retag'];
    _lastToolStatus['retag'] = data.status;
    if (prev !== undefined && data.status === prev && data.status !== 'running') return;
    updateRetagProgressUI(data);
    // Handle terminal state toasts (only on transition)
    if (prev === 'running' || prev === undefined) {
        if (data.status === 'finished') {
            showToast('Retag completed successfully', 'success');
            loadRetagStats();
        } else if (data.status === 'error') {
            showToast(`Retag error: ${data.error_message || 'Unknown error'}`, 'error');
        }
    }
}

function updateRetagProgressUI(state) {
    const phaseLabel = document.getElementById('retag-phase-label');
    const progressBar = document.getElementById('retag-progress-bar');
    const progressLabel = document.getElementById('retag-progress-label');
    const statusEl = document.getElementById('retag-stat-status');

    if (phaseLabel) phaseLabel.textContent = state.phase || 'Ready';
    if (progressBar) progressBar.style.width = `${state.progress || 0}%`;
    if (progressLabel) {
        progressLabel.textContent = `${state.processed || 0} / ${state.total_tracks || 0} tracks (${(state.progress || 0).toFixed(1)}%)`;
    }
    if (statusEl) {
        statusEl.textContent = state.status === 'running' ? 'Running' : 'Idle';
    }

    // Color the progress bar red on error
    if (progressBar) {
        progressBar.style.backgroundColor = state.status === 'error' ? '#ff4444' : '';
    }
}

/**
 * Show inline delete confirmation for a retag group
 */
function showRetagDeleteConfirm(groupId) {
    const area = document.getElementById(`retag-delete-area-${groupId}`);
    if (!area) return;
    area.innerHTML = `<div class="retag-confirm-inline">
        <span>Remove?</span>
        <button class="retag-confirm-yes">Yes</button>
        <button class="retag-confirm-no">No</button>
    </div>`;
}

function cancelRetagDeleteConfirm(groupId) {
    const area = document.getElementById(`retag-delete-area-${groupId}`);
    if (!area) return;
    area.innerHTML = `<button class="retag-group-delete-btn" data-group-id="${groupId}" title="Remove from list">&times;</button>`;
}

async function executeRetagGroupDelete(groupId) {
    try {
        const response = await fetch(`/api/retag/groups/${groupId}`, { method: 'DELETE' });
        const data = await response.json();
        if (data.success) {
            const card = document.querySelector(`.retag-group-card[data-group-id="${groupId}"]`);
            if (card) {
                const section = card.closest('.retag-artist-section');
                card.remove();
                if (section && section.querySelectorAll('.retag-group-card').length === 0) {
                    section.remove();
                }
            }
            loadRetagStats();
            updateRetagBatchBar();
            showToast('Group removed', 'success');
        } else {
            showToast('Failed to remove group', 'error');
        }
    } catch (e) {
        showToast('Failed to remove group', 'error');
    }
}

/**
 * Update the retag batch action bar based on checkbox selection
 */
function updateRetagBatchBar() {
    const checked = document.querySelectorAll('.retag-select-cb:checked');
    const bar = document.getElementById('retag-batch-bar');
    const countEl = document.getElementById('retag-batch-count');
    if (!bar) return;

    if (checked.length > 0) {
        bar.style.display = 'flex';
        countEl.textContent = `${checked.length} selected`;
    } else {
        bar.style.display = 'none';
    }
}

/**
 * Batch remove selected retag groups
 */
async function batchRemoveRetagGroups() {
    const checked = document.querySelectorAll('.retag-select-cb:checked');
    if (checked.length === 0) return;

    const groupIds = Array.from(checked).map(cb => parseInt(cb.getAttribute('data-group-id')));

    try {
        const response = await fetch('/api/retag/groups/delete-batch', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ group_ids: groupIds })
        });
        const data = await response.json();
        if (data.success) {
            showToast(`Removed ${data.removed} group${data.removed !== 1 ? 's' : ''}`, 'success');
            openRetagModal(); // Refresh
        } else {
            showToast('Failed to remove groups', 'error');
        }
    } catch (e) {
        showToast('Failed to remove groups', 'error');
    }
}

/**
 * Clear all retag groups — inline confirm on the button itself
 */
function clearAllRetagGroups(btn) {
    if (!btn) return;
    if (btn.dataset.confirming === 'true') {
        // Already confirming — execute
        btn.dataset.confirming = '';
        btn.textContent = 'Clear All';
        executeClearAllRetag();
        return;
    }
    // First click — show confirm state
    btn.dataset.confirming = 'true';
    btn.textContent = 'Confirm Clear?';
    btn.style.background = 'rgba(255, 59, 48, 0.15)';
    // Auto-reset after 3 seconds if not clicked again
    setTimeout(() => {
        if (btn.dataset.confirming === 'true') {
            btn.dataset.confirming = '';
            btn.textContent = 'Clear All';
            btn.style.background = '';
        }
    }, 3000);
}

async function executeClearAllRetag() {
    try {
        const response = await fetch('/api/retag/groups/clear-all', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' }
        });
        const data = await response.json();
        if (data.success) {
            showToast(`Cleared ${data.removed} group${data.removed !== 1 ? 's' : ''}`, 'success');
            openRetagModal(); // Refresh
        } else {
            showToast('Failed to clear groups', 'error');
        }
    } catch (e) {
        showToast('Failed to clear groups', 'error');
    }
}

function stopWishlistCountPolling() {
    if (wishlistCountInterval) {
        clearInterval(wishlistCountInterval);
        wishlistCountInterval = null;
    }
}



function resetWishlistModalToIdleState() {
    // Reset wishlist modal to idle state after background processing completes
    const playlistId = 'wishlist';
    const process = activeDownloadProcesses[playlistId];

    if (process) {
        console.log('🔄 Resetting wishlist modal to idle state...');

        // Reset button states
        const beginBtn = document.getElementById(`begin-analysis-btn-${playlistId}`);
        const cancelBtn = document.getElementById(`cancel-all-btn-${playlistId}`);
        if (beginBtn) {
            beginBtn.style.display = 'inline-block';
            beginBtn.disabled = false;
            beginBtn.textContent = 'Begin Analysis';
        }
        if (cancelBtn) {
            cancelBtn.style.display = 'none';
        }

        // Show the force download toggle again
        const forceToggleContainer = document.querySelector(`#force-download-all-${playlistId}`)?.closest('.force-download-toggle-container');
        if (forceToggleContainer) {
            forceToggleContainer.style.display = 'flex';
        }

        // Reset progress displays
        const analysisText = document.getElementById(`analysis-progress-text-${playlistId}`);
        const analysisBar = document.getElementById(`analysis-progress-fill-${playlistId}`);
        const downloadText = document.getElementById(`download-progress-text-${playlistId}`);
        const downloadBar = document.getElementById(`download-progress-fill-${playlistId}`);

        if (analysisText) analysisText.textContent = 'Ready to start';
        if (analysisBar) analysisBar.style.width = '0%';
        if (downloadText) downloadText.textContent = 'Waiting for analysis';
        if (downloadBar) downloadBar.style.width = '0%';

        // Reset all track rows to pending state
        const trackRows = document.querySelectorAll(`#download-missing-modal-${CSS.escape(playlistId)} tr[data-track-index]`);
        trackRows.forEach((row, index) => {
            const matchCell = row.querySelector(`#match-${playlistId}-${index}`);
            const downloadCell = row.querySelector(`#download-${playlistId}-${index}`);
            const actionsCell = row.querySelector(`#actions-${playlistId}-${index}`);

            if (matchCell) matchCell.textContent = '🔍 Pending';
            if (downloadCell) downloadCell.textContent = '-';
            if (actionsCell) actionsCell.innerHTML = '-';
        });

        // Reset stats
        const foundElement = document.getElementById(`stat-found-${playlistId}`);
        const missingElement = document.getElementById(`stat-missing-${playlistId}`);
        const downloadedElement = document.getElementById(`stat-downloaded-${playlistId}`);
        if (foundElement) foundElement.textContent = '-';
        if (missingElement) missingElement.textContent = '-';
        if (downloadedElement) downloadedElement.textContent = '0';

        // Reset process status
        process.status = 'idle';
        process.batchId = null;
        if (process.poller) {
            clearInterval(process.poller);
            process.poller = null;
        }

        console.log('✅ Wishlist modal fully reset to idle state');
    } else {
        console.log('⚠️ No wishlist process found to reset');
    }
}

let toolsPageState = { isInitialized: false };

async function initializeToolsPage() {
    // Attach event listeners for tool buttons (idempotent — getElementById returns null if already wired)
    const updateButton = document.getElementById('db-update-button');
    if (updateButton && !updateButton._toolsWired) {
        updateButton.addEventListener('click', handleDbUpdateButtonClick);
        updateButton._toolsWired = true;
    }

    const metadataButton = document.getElementById('metadata-update-button');
    if (metadataButton && !metadataButton._toolsWired) {
        metadataButton.addEventListener('click', handleMetadataUpdateButtonClick);
        metadataButton._toolsWired = true;
    }

    const qualityScanButton = document.getElementById('quality-scan-button');
    if (qualityScanButton && !qualityScanButton._toolsWired) {
        qualityScanButton.addEventListener('click', handleQualityScanButtonClick);
        qualityScanButton._toolsWired = true;
    }

    const duplicateCleanButton = document.getElementById('duplicate-clean-button');
    if (duplicateCleanButton && !duplicateCleanButton._toolsWired) {
        duplicateCleanButton.addEventListener('click', handleDuplicateCleanButtonClick);
        duplicateCleanButton._toolsWired = true;
    }

    const retagOpenButton = document.getElementById('retag-open-button');
    if (retagOpenButton && !retagOpenButton._toolsWired) {
        retagOpenButton.addEventListener('click', openRetagModal);
        retagOpenButton._toolsWired = true;
    }

    const mediaScanButton = document.getElementById('media-scan-button');
    if (mediaScanButton && !mediaScanButton._toolsWired) {
        mediaScanButton.addEventListener('click', handleMediaScanButtonClick);
        mediaScanButton._toolsWired = true;
    }

    const backupNowButton = document.getElementById('backup-now-button');
    if (backupNowButton && !backupNowButton._toolsWired) {
        backupNowButton.addEventListener('click', handleBackupNowClick);
        backupNowButton._toolsWired = true;
    }

    // Tool-specific init
    await checkAndHideMetadataUpdaterForNonPlex();
    await checkAndRestoreMetadataUpdateState();
    await checkAndShowMediaScanForPlex();
    loadBackupList();
    initializeToolHelpButtons();
    loadRetagStats();
    checkRetagStatus();
    await fetchAndUpdateDbStats();
    loadDiscoveryPoolStats();
    loadMetadataCacheStats();

    // Start polling (cleared when navigating away via loadPageData preamble)
    stopDbStatsPolling();
    dbStatsInterval = setInterval(fetchAndUpdateDbStats, 10000);

    // Check for ongoing operations
    await checkAndUpdateDbProgress();
    await checkAndUpdateQualityScanProgress();
    await checkAndUpdateDuplicateCleanProgress();

    // Initialize library maintenance section
    updateRepairStatus();
    switchRepairTab('jobs');

    toolsPageState.isInitialized = true;
}

async function loadDashboardData() {
    // Initial load of wishlist count
    await updateWishlistCount();

    // Start periodic refresh of wishlist count (every 10 seconds)
    stopWishlistCountPolling(); // Ensure no duplicates
    wishlistCountInterval = setInterval(updateWishlistCount, 10000);

    // Initial load of service status, system statistics, and library status
    await fetchAndUpdateServiceStatus();
    await fetchAndUpdateSystemStats();
    await fetchAndUpdateDbStats();

    // Service status is already polled globally (line 311)
    // System stats polling kept here (dashboard-specific)
    setInterval(fetchAndUpdateSystemStats, 10000);

    // Initial load of activity feed
    await fetchAndUpdateActivityFeed();

    // Start periodic refresh of activity feed (every 2 seconds for responsiveness)
    setInterval(fetchAndUpdateActivityFeed, 2000);

    // Start periodic toast checking (every 3 seconds)
    setInterval(checkForActivityToasts, 3000);

    // Check for any active download processes that need rehydration
    await checkForActiveProcesses();

    // Populate the Active Downloads dashboard section with any existing downloads
    updateDashboardDownloads();

    // Automatic wishlist processing now runs server-side
}

// --- Data Fetching and UI Updates ---

async function fetchAndUpdateDbStats() {
    if (socketConnected) return; // WebSocket handles this
    try {
        const response = await fetch('/api/database/stats');
        if (!response.ok) return;

        const stats = await response.json();

        // This function updates the stat cards in the top grid
        updateDashboardStatCards(stats);

        // This function updates the info within the DB Updater tool card
        updateDbUpdaterCardInfo(stats);

    } catch (error) {
        console.warn('Could not fetch DB stats:', error);
    }
}

function updateDashboardStatCards(stats) {
    // Update the Library Status card on the dashboard
    updateLibraryStatusCard(stats);
}

/**
 * Smart Library Status card on the Dashboard.
 * Shows different states: no server, empty library, healthy library, scanning.
 */
function updateLibraryStatusCard(dbStats) {
    const card = document.getElementById('library-status-card');
    if (!card) return;

    const title = document.getElementById('library-status-title');
    const subtitle = document.getElementById('library-status-subtitle');
    const statsRow = document.getElementById('library-status-stats');
    const scanBtn = document.getElementById('library-status-scan-btn');
    const scanLabel = document.getElementById('library-status-scan-label');
    const deepBtn = document.getElementById('library-status-deep-btn');
    const progressDiv = document.getElementById('library-status-progress');
    const messageDiv = document.getElementById('library-status-message');

    const artists = dbStats ? (dbStats.artists || 0) : 0;
    const albums = dbStats ? (dbStats.albums || 0) : 0;
    const tracks = dbStats ? (dbStats.tracks || 0) : 0;
    const sizeMb = dbStats ? (dbStats.database_size_mb || 0) : 0;
    const lastUpdate = dbStats ? dbStats.last_update : null;
    const serverSource = dbStats ? dbStats.server_source : null;

    // Check if a scan is in progress
    const isScanning = window._libraryStatusScanning || false;

    // Determine state
    const serverConnected = _lastStatusPayload && _lastStatusPayload.media_server && _lastStatusPayload.media_server.connected;
    const serverType = _lastStatusPayload && _lastStatusPayload.active_media_server;
    const hasData = tracks > 0;
    const hasServer = !!serverType && serverType !== 'none';

    // Reset classes
    card.className = 'library-status-card';

    if (isScanning) {
        // State: Scanning
        card.classList.add('scanning');
        if (title) title.textContent = 'Library Scan';
        if (subtitle) subtitle.textContent = 'Updating library database...';
        if (scanBtn) {
            scanBtn.style.display = '';
            scanBtn.classList.add('scanning');
            scanLabel.textContent = 'Stop';
            scanBtn.disabled = false;
        }
        if (deepBtn) deepBtn.style.display = 'none';
        if (statsRow) statsRow.style.display = hasData ? '' : 'none';
        if (progressDiv) progressDiv.style.display = '';
        if (messageDiv) messageDiv.style.display = 'none';

    } else if (!hasServer) {
        // State: No server configured
        card.classList.add('needs-setup');
        if (title) title.textContent = 'No Media Server';
        if (subtitle) subtitle.textContent = 'Connect a server to get started';
        if (scanBtn) scanBtn.style.display = 'none';
        if (deepBtn) deepBtn.style.display = 'none';
        if (statsRow) statsRow.style.display = 'none';
        if (progressDiv) progressDiv.style.display = 'none';
        if (messageDiv) {
            messageDiv.style.display = '';
            messageDiv.innerHTML = 'SoulSync needs a media server to manage your library. '
                + 'Go to <span class="link" onclick="navigateToPage(\'settings\')">Settings</span> '
                + 'to connect Plex, Jellyfin, or Navidrome.';
        }

    } else if (!serverConnected) {
        // State: Server configured but not connected
        card.classList.add('needs-setup');
        const serverName = _capitalize(serverType);
        if (title) title.textContent = `${serverName} — Disconnected`;
        if (subtitle) subtitle.textContent = 'Cannot reach your media server';
        if (scanBtn) scanBtn.style.display = 'none';
        if (deepBtn) deepBtn.style.display = 'none';
        if (statsRow) statsRow.style.display = 'none';
        if (progressDiv) progressDiv.style.display = 'none';
        if (messageDiv) {
            messageDiv.style.display = '';
            messageDiv.innerHTML = `Your ${serverName} server is configured but not responding. `
                + 'Check that it\'s running and the connection details are correct in '
                + '<span class="link" onclick="navigateToPage(\'settings\')">Settings</span>.';
        }

    } else if (!hasData) {
        // State: Server connected but library is empty
        card.classList.add('empty-library');
        const serverName = _capitalize(serverType);
        if (title) title.textContent = `${serverName} Connected`;
        if (subtitle) subtitle.textContent = 'Library database is empty';
        if (scanBtn) {
            scanBtn.style.display = '';
            scanBtn.classList.remove('scanning');
            scanLabel.textContent = 'Scan Now';
            scanBtn.disabled = false;
        }
        if (deepBtn) deepBtn.style.display = 'none';
        if (statsRow) statsRow.style.display = 'none';
        if (progressDiv) progressDiv.style.display = 'none';
        if (messageDiv) {
            messageDiv.style.display = '';
            messageDiv.innerHTML = 'Your server is connected but SoulSync hasn\'t imported your library yet. '
                + 'Click <strong>Scan Now</strong> to pull your artists, albums, and tracks into SoulSync.';
        }

    } else {
        // State: Healthy library with data
        card.classList.add('has-data');
        const serverName = _capitalize(serverType);
        let lastRefreshText = 'Never';
        if (lastUpdate) {
            const d = new Date(lastUpdate);
            if (!isNaN(d.getTime())) {
                lastRefreshText = typeof _formatTimeAgo === 'function' ? _formatTimeAgo(d) : d.toLocaleDateString();
            }
        }
        if (title) title.textContent = `${serverName} Library`;
        if (subtitle) subtitle.textContent = `Last refreshed ${lastRefreshText}`;
        if (scanBtn) {
            scanBtn.style.display = '';
            scanBtn.classList.remove('scanning');
            scanLabel.textContent = 'Refresh';
            scanBtn.disabled = false;
        }
        if (deepBtn) deepBtn.style.display = '';
        if (statsRow) {
            statsRow.style.display = '';
            document.getElementById('library-status-artists').textContent = artists.toLocaleString();
            document.getElementById('library-status-albums').textContent = albums.toLocaleString();
            document.getElementById('library-status-tracks').textContent = tracks.toLocaleString();
            document.getElementById('library-status-size').textContent = sizeMb < 1 ? `${Math.round(sizeMb * 1024)} KB` : `${sizeMb.toFixed(1)} MB`;
        }
        if (progressDiv) progressDiv.style.display = 'none';
        if (messageDiv) messageDiv.style.display = 'none';
    }
}

// _lastStatusPayload and _isSoulsyncStandalone are declared in core.js
const _origFetchServiceStatus = typeof fetchAndUpdateServiceStatus === 'function' ? fetchAndUpdateServiceStatus : null;

function _capitalize(s) { return s ? s.charAt(0).toUpperCase() + s.slice(1) : ''; }

/**
 * Dashboard library scan button handler — triggers incremental DB update.
 */
async function dashboardLibraryScan(fullRefresh = false) {
    const scanBtn = document.getElementById('library-status-scan-btn');
    const scanLabel = document.getElementById('library-status-scan-label');

    // If already scanning, stop it
    if (window._libraryStatusScanning) {
        try {
            await fetch('/api/database/update/stop', { method: 'POST' });
            window._libraryStatusScanning = false;
            showToast('Library scan stopped', 'info');
            // Refresh the card
            try {
                const r = await fetch('/api/database/stats');
                if (r.ok) updateLibraryStatusCard(await r.json());
            } catch (e) {}
        } catch (e) {
            showToast('Failed to stop scan', 'error');
        }
        return;
    }

    // Start scan
    try {
        window._libraryStatusScanning = true;
        updateLibraryStatusCard(null); // Update to scanning state

        const response = await fetch('/api/database/update', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ full_refresh: fullRefresh })
        });
        const data = await response.json();
        if (!data.success) {
            window._libraryStatusScanning = false;
            showToast(data.error || 'Failed to start scan', 'error');
            return;
        }

        showToast('Library scan started', 'success');

        // Poll for progress
        const pollInterval = setInterval(async () => {
            try {
                const statusResp = await fetch('/api/database/update/status');
                if (!statusResp.ok) return;
                const status = await statusResp.json();

                const phase = document.getElementById('library-status-phase');
                const barFill = document.getElementById('library-status-bar-fill');
                const detail = document.getElementById('library-status-progress-detail');

                if (phase) phase.textContent = status.phase || 'Scanning...';
                if (barFill) barFill.style.width = `${status.progress || 0}%`;
                if (detail && status.processed !== undefined) {
                    detail.textContent = `${status.processed} / ${status.total || '?'}`;
                }

                if (status.status === 'completed' || status.status === 'finished' || status.status === 'error' || status.status === 'idle') {
                    clearInterval(pollInterval);
                    window._libraryStatusScanning = false;

                    if (status.status === 'completed' || status.status === 'finished') {
                        showToast('Library scan complete', 'success');
                    } else if (status.status === 'error') {
                        showToast(`Scan error: ${status.error_message || 'Unknown'}`, 'error');
                    }

                    // Refresh stats
                    try {
                        const r = await fetch('/api/database/stats');
                        if (r.ok) updateLibraryStatusCard(await r.json());
                    } catch (e) {}
                }
            } catch (e) {
                clearInterval(pollInterval);
                window._libraryStatusScanning = false;
            }
        }, 2000);

    } catch (e) {
        window._libraryStatusScanning = false;
        showToast(`Scan failed: ${e.message}`, 'error');
    }
}

/**
 * Dashboard deep scan — finds new tracks, removes stale ones, preserves enrichment data.
 */
async function dashboardLibraryDeepScan() {
    if (window._libraryStatusScanning) {
        showToast('A scan is already running', 'warning');
        return;
    }

    if (!await showConfirmDialog({
        title: 'Deep Scan Library',
        message: 'A deep scan re-checks every track in your media server library.\n\n' +
                 '• Adds any new tracks that were missed\n' +
                 '• Removes tracks no longer on your server\n' +
                 '• Preserves all existing metadata and enrichment data\n\n' +
                 'This may take a while for large libraries. Continue?',
    })) return;

    // Use the same scan flow as dashboardLibraryScan but with deep_scan flag
    try {
        window._libraryStatusScanning = true;
        updateLibraryStatusCard(null);

        const response = await fetch('/api/database/update', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ deep_scan: true })
        });
        const data = await response.json();
        if (!data.success) {
            window._libraryStatusScanning = false;
            showToast(data.error || 'Failed to start deep scan', 'error');
            try { const r = await fetch('/api/database/stats'); if (r.ok) updateLibraryStatusCard(await r.json()); } catch (e) {}
            return;
        }

        showToast('Deep scan started — this may take a while', 'success');

        const pollInterval = setInterval(async () => {
            try {
                const statusResp = await fetch('/api/database/update/status');
                if (!statusResp.ok) return;
                const status = await statusResp.json();

                const phase = document.getElementById('library-status-phase');
                const barFill = document.getElementById('library-status-bar-fill');
                const detail = document.getElementById('library-status-progress-detail');

                if (phase) phase.textContent = status.phase || 'Deep scanning...';
                if (barFill) barFill.style.width = `${status.progress || 0}%`;
                if (detail && status.processed !== undefined) {
                    detail.textContent = `${status.processed} / ${status.total || '?'}`;
                }

                if (status.status === 'completed' || status.status === 'finished' || status.status === 'error' || status.status === 'idle') {
                    clearInterval(pollInterval);
                    window._libraryStatusScanning = false;

                    if (status.status === 'completed' || status.status === 'finished') {
                        showToast('Deep scan complete', 'success');
                    } else if (status.status === 'error') {
                        showToast(`Deep scan error: ${status.error_message || 'Unknown'}`, 'error');
                    }

                    try { const r = await fetch('/api/database/stats'); if (r.ok) updateLibraryStatusCard(await r.json()); } catch (e) {}
                }
            } catch (e) {
                clearInterval(pollInterval);
                window._libraryStatusScanning = false;
            }
        }, 2000);

    } catch (e) {
        window._libraryStatusScanning = false;
        showToast(`Deep scan failed: ${e.message}`, 'error');
    }
}

/**
 * Update the Active Downloads section on the dashboard.
 * Called from artist, search, and discover update points (event-driven, no polling).
 */
function updateDashboardDownloads() {
    const section = document.getElementById('dashboard-active-downloads-section');
    const container = document.getElementById('dashboard-downloads-container');
    if (!section || !container) return;

    // Collect active entries from each source
    const activeArtists = Object.keys(artistDownloadBubbles).filter(id =>
        artistDownloadBubbles[id].downloads.length > 0
    );
    const activeSearch = Object.keys(searchDownloadBubbles).filter(name =>
        searchDownloadBubbles[name].downloads.length > 0
    );
    const activeDiscover = Object.keys(discoverDownloads);
    const activeBeatport = Object.keys(beatportDownloadBubbles).filter(key =>
        beatportDownloadBubbles[key].downloads.length > 0
    );

    const totalCount = activeArtists.length + activeSearch.length + activeDiscover.length + activeBeatport.length;

    if (totalCount === 0) {
        section.style.display = 'none';
        container.innerHTML = '';
        return;
    }

    section.style.display = '';
    let html = '';

    // --- Artists group ---
    if (activeArtists.length > 0) {
        html += `
            <div class="dashboard-downloads-group">
                <div class="dashboard-downloads-group-header">
                    <span class="dashboard-downloads-group-label">Artists</span>
                    <span class="dashboard-downloads-group-count">${activeArtists.length}</span>
                </div>
                <div class="dashboard-bubble-container">
                    ${activeArtists.map(id => createArtistBubbleCard(artistDownloadBubbles[id])).join('')}
                </div>
            </div>`;
    }

    // --- Search group ---
    if (activeSearch.length > 0) {
        html += `
            <div class="dashboard-downloads-group">
                <div class="dashboard-downloads-group-header">
                    <span class="dashboard-downloads-group-label">Search</span>
                    <span class="dashboard-downloads-group-count">${activeSearch.length}</span>
                </div>
                <div class="dashboard-bubble-container">
                    ${activeSearch.map(name => createSearchBubbleCard(searchDownloadBubbles[name])).join('')}
                </div>
            </div>`;
    }

    // --- Discover group ---
    if (activeDiscover.length > 0) {
        html += `
            <div class="dashboard-downloads-group">
                <div class="dashboard-downloads-group-header">
                    <span class="dashboard-downloads-group-label">Discover</span>
                    <span class="dashboard-downloads-group-count">${activeDiscover.length}</span>
                </div>
                <div class="dashboard-bubble-container">
                    ${activeDiscover.map(pid => createDashboardDiscoverBubble(pid)).join('')}
                </div>
            </div>`;
    }

    // --- Beatport group ---
    if (activeBeatport.length > 0) {
        html += `
            <div class="dashboard-downloads-group">
                <div class="dashboard-downloads-group-header">
                    <span class="dashboard-downloads-group-label">Beatport</span>
                    <span class="dashboard-downloads-group-count">${activeBeatport.length}</span>
                </div>
                <div class="dashboard-bubble-container">
                    ${activeBeatport.map(key => createBeatportBubbleCard(beatportDownloadBubbles[key])).join('')}
                </div>
            </div>`;
    }

    container.innerHTML = html;

    // Post-render: attach artist bubble click handlers + dynamic glow
    activeArtists.forEach(artistId => {
        const card = container.querySelector(`.artist-bubble-card[data-artist-id="${artistId}"]`);
        if (card) {
            card.addEventListener('click', () => openArtistDownloadModal(artistId));
            const artist = artistDownloadBubbles[artistId].artist;
            if (artist.image_url) {
                extractImageColors(artist.image_url, (colors) => {
                    applyDynamicGlow(card, colors);
                });
            }
        }
    });
    // Beatport bubble click handlers + glow
    activeBeatport.forEach(chartKey => {
        const card = container.querySelector(`.artist-bubble-card[data-chart-key="${chartKey}"]`);
        if (card) {
            card.addEventListener('click', () => openBeatportBubbleModal(chartKey));
            const chartImage = beatportDownloadBubbles[chartKey].chart.image;
            if (chartImage) {
                extractImageColors(chartImage, (colors) => {
                    applyDynamicGlow(card, colors);
                });
            }
        }
    });
    // Search and discover cards use inline onclick — no post-render needed
}

/**
 * Create a 150px circle card for a discover download (dashboard variant).
 * Matches artist/search bubble sizing.
 */
function createDashboardDiscoverBubble(playlistId) {
    const download = discoverDownloads[playlistId];
    if (!download) return '';

    const isCompleted = download.status === 'completed';
    const imageUrl = download.imageUrl || '';
    const backgroundStyle = imageUrl
        ? `background-image: url('${imageUrl}');`
        : `background: linear-gradient(135deg, rgba(29, 185, 84, 0.3) 0%, rgba(24, 156, 71, 0.2) 100%);`;

    return `
        <div class="dashboard-discover-bubble ${isCompleted ? 'completed' : ''}"
             onclick="openDiscoverDownloadModal('${playlistId}')"
             title="${escapeHtml(download.name)} - Click to view">
            <div class="dashboard-discover-bubble-image" style="${backgroundStyle}"></div>
            <div class="dashboard-discover-bubble-overlay"></div>
            <div class="dashboard-discover-bubble-content">
                <div class="dashboard-discover-bubble-name">${escapeHtml(download.name)}</div>
                <div class="dashboard-discover-bubble-status">${isCompleted ? 'Completed' : 'In Progress'}</div>
            </div>
        </div>
    `;
}



function updateDbUpdaterCardInfo(stats) {
    // Update the detailed stats within the DB Updater tool card
    const lastRefreshEl = document.getElementById('db-last-refresh');
    const artistsStatEl = document.getElementById('db-stat-artists');
    const albumsStatEl = document.getElementById('db-stat-albums');
    const tracksStatEl = document.getElementById('db-stat-tracks');
    const sizeStatEl = document.getElementById('db-stat-size');

    if (lastRefreshEl) {
        if (stats.last_full_refresh) {
            const date = new Date(stats.last_full_refresh);
            lastRefreshEl.textContent = date.toLocaleString();
        } else {
            lastRefreshEl.textContent = 'Never';
        }
    }

    if (artistsStatEl) artistsStatEl.textContent = stats.artists.toLocaleString() || '0';
    if (albumsStatEl) albumsStatEl.textContent = stats.albums.toLocaleString() || '0';
    if (tracksStatEl) tracksStatEl.textContent = stats.tracks.toLocaleString() || '0';
    if (sizeStatEl) sizeStatEl.textContent = `${stats.database_size_mb.toFixed(2)} MB`;

    // Update the title of the tool card to show which server is active
    const toolCardTitle = document.querySelector('#db-updater-card .tool-card-title');
    if (toolCardTitle && stats.server_source) {
        const serverName = stats.server_source.charAt(0).toUpperCase() + stats.server_source.slice(1);
        toolCardTitle.textContent = `${serverName} Database Updater`;
    }
}

// --- Wishlist Count Functions ---

async function updateWishlistCount() {
    if (socketConnected) return; // WebSocket handles this
    try {
        const response = await fetch('/api/wishlist/count');
        if (!response.ok) return;

        const data = await response.json();
        const count = data.count || 0;

        _updateHeroBtnCount('wishlist-button', 'wishlist-badge', count);
        // Update sidebar nav badge
        const wlNavBadge = document.getElementById('wishlist-nav-badge');
        if (wlNavBadge) {
            wlNavBadge.textContent = count;
            wlNavBadge.classList.toggle('hidden', count === 0);
        }
        const wishlistButton = document.getElementById('wishlist-button');
        if (wishlistButton) {
            if (count === 0) {
                wishlistButton.classList.remove('wishlist-active');
                wishlistButton.classList.add('wishlist-inactive');
            } else {
                wishlistButton.classList.remove('wishlist-inactive');
                wishlistButton.classList.add('wishlist-active');
            }
        }

        // Check for auto-initiated wishlist processes that user should see immediately
        await checkForAutoInitiatedWishlistProcess();

    } catch (error) {
        console.warn('Could not fetch wishlist count:', error);
    }
}

async function checkForAutoInitiatedWishlistProcess() {
    try {
        const playlistId = 'wishlist';

        // Only check if we're on the dashboard and no modal is currently visible
        if (currentPage !== 'dashboard') {
            return;
        }

        // Don't override if user has manually closed the modal during auto-processing
        if (WishlistModalState.wasUserClosed()) {
            return;
        }

        // Check for active wishlist processes
        const response = await fetch('/api/active-processes');
        if (!response.ok) return;

        const data = await response.json();
        const processes = data.active_processes || [];
        const serverWishlistProcess = processes.find(p => p.playlist_id === playlistId);
        const clientWishlistProcess = activeDownloadProcesses[playlistId];

        if (serverWishlistProcess && serverWishlistProcess.auto_initiated) {
            console.log('🤖 [Auto-Processing] Detected auto-initiated wishlist process during polling');

            // Only sync frontend state if needed, but don't auto-show modal
            const needsSync = !clientWishlistProcess ||
                clientWishlistProcess.batchId !== serverWishlistProcess.batch_id ||
                !clientWishlistProcess.modalElement ||
                !document.body.contains(clientWishlistProcess.modalElement);

            if (needsSync) {
                console.log('🔄 [Auto-Processing] Syncing frontend state for auto-processing (background mode)');
                await rehydrateModal(serverWishlistProcess, false); // Background sync only
            }

            // Note: Modal visibility is controlled by user interaction only
            // User must click wishlist button to see auto-processing progress
        }

    } catch (error) {
        console.warn('Error checking for auto-initiated wishlist process:', error);
    }
}

async function checkAndUpdateDbProgress() {
    if (socketConnected) return; // WebSocket handles this
    try {
        const response = await fetch('/api/database/update/status', {
            signal: AbortSignal.timeout(10000) // 10 second timeout
        });
        if (!response.ok) return;

        const state = await response.json();
        console.debug('📊 DB Status:', state.status, `${state.processed}/${state.total}`, `${state.progress.toFixed(1)}%`);
        updateDbProgressUI(state);

        // Start polling only if not already polling and status is running
        if (state.status === 'running' && !dbUpdateStatusInterval) {
            console.log('🔄 Starting database update polling (1 second interval)');
            dbUpdateStatusInterval = setInterval(checkAndUpdateDbProgress, 1000);
        }

    } catch (error) {
        console.warn('Could not fetch DB update status:', error);
        // Don't stop polling on network errors - keep trying
    }
}

function updateDbProgressFromData(data) {
    const prev = _lastToolStatus['db-update'];
    _lastToolStatus['db-update'] = data.status;
    if (prev !== undefined && data.status === prev && data.status !== 'running') return;
    updateDbProgressUI(data);
}

function updateDbProgressUI(state) {
    const button = document.getElementById('db-update-button');
    const phaseLabel = document.getElementById('db-phase-label');
    const progressLabel = document.getElementById('db-progress-label');
    const progressBar = document.getElementById('db-progress-bar');
    const refreshSelect = document.getElementById('db-refresh-type');

    if (!button || !phaseLabel || !progressLabel || !progressBar || !refreshSelect) return;

    if (state.status === 'running') {
        button.textContent = 'Stop Update';
        button.disabled = false;
        refreshSelect.disabled = true;

        phaseLabel.textContent = state.phase || 'Processing...';
        progressLabel.textContent = `${state.processed} / ${state.total} artists (${state.progress.toFixed(1)}%)`;
        progressBar.style.width = `${state.progress}%`;
    } else { // idle, finished, or error
        stopDbUpdatePolling();
        button.textContent = 'Update Database';
        button.disabled = false;
        refreshSelect.disabled = false;

        if (state.status === 'error') {
            phaseLabel.textContent = `Error: ${state.error_message}`;
            progressBar.style.backgroundColor = '#ff4444'; // Red for error
        } else {
            phaseLabel.textContent = state.phase || 'Idle';
            progressBar.style.backgroundColor = 'rgb(var(--accent-rgb))'; // Green for normal
        }

        if (state.status === 'finished' || state.status === 'error') {
            // Final stats refresh after completion/error
            setTimeout(fetchAndUpdateDbStats, 500);
        }
    }
}

// ===================================================================
