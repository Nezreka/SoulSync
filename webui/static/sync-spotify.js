// == SYNC PAGE SPOTIFY FUNCTIONALITY       ==
// ===========================================

async function checkForActiveProcesses() {
    try {
        const response = await fetch('/api/active-processes');
        if (!response.ok) return;

        const data = await response.json();
        const processes = data.active_processes || [];

        if (processes.length > 0) {
            console.log(`🔄 Found ${processes.length} active process(es) from backend. Rehydrating UI...`);

            // Separate download batch processes from YouTube playlist processes
            const downloadProcesses = processes.filter(p => p.type === 'batch');
            const youtubeProcesses = processes.filter(p => p.type === 'youtube_playlist');

            console.log(`📊 Process breakdown: ${downloadProcesses.length} download batches, ${youtubeProcesses.length} YouTube playlists`);

            // Rehydrate download modal processes (existing Spotify system)
            for (const processInfo of downloadProcesses) {
                if (!activeDownloadProcesses[processInfo.playlist_id]) {
                    rehydrateModal(processInfo);
                }
            }

            // Note: YouTube playlists are handled by loadYouTubePlaylistsFromBackend() and rehydrateYouTubePlaylist()
            // in loadSyncData(), which provides more complete data than active processes and handles download modal rehydration.
            console.log(`ℹ️ Skipping ${youtubeProcesses.length} YouTube playlists - handled by full backend loading`);
        }
    } catch (error) {
        console.error('Failed to check for active processes:', error);
    }
}

async function rehydrateArtistAlbumModal(virtualPlaylistId, playlistName, batchId) {
    /**
     * Rehydrates an artist album download modal from backend process data.
     * Extracts artist/album info from virtual playlist ID and recreates the modal.
     */
    try {
        console.log(`💧 Rehydrating artist album modal: ${virtualPlaylistId} (${playlistName})`);

        // Extract artist_id and album_id from virtualPlaylistId format: artist_album_[artist_id]_[album_id]
        const parts = virtualPlaylistId.split('_');
        if (parts.length < 4 || parts[0] !== 'artist' || parts[1] !== 'album') {
            console.error(`❌ Invalid virtual playlist ID format: ${virtualPlaylistId}`);
            return;
        }

        const artistId = parts[2];
        const albumId = parts.slice(3).join('_'); // Handle album IDs that might contain underscores

        console.log(`🔍 Extracted from virtual playlist: artistId=${artistId}, albumId=${albumId}`);

        // Fetch the album tracks to get proper artist and album data
        try {
            const rehydrateProcess = activeDownloadProcesses[virtualPlaylistId];
            const albumSource = rehydrateProcess?.source || rehydrateProcess?.artist?.source || rehydrateProcess?.album?.source || null;
            const artistName = rehydrateProcess?.artist?.name || '';
            const params = new URLSearchParams({
                name: playlistName || '',
                artist: artistName
            });
            if (albumSource) {
                params.set('source', albumSource);
            }

            const response = await fetch(`/api/album/${albumId}/tracks?${params}`);
            const data = await response.json();

            if (!data.success || !data.album || !data.tracks) {
                console.error('❌ Failed to fetch album data for rehydration:', data.error);
                return;
            }

            const album = data.album;
            const tracks = data.tracks;
            const source = data.source || tracks[0]?._source || albumSource || null;

            // Extract artist info from the first track (all tracks should have same artist)
            const artist = {
                id: artistId,
                name: tracks[0].artists[0], // Use first artist name from first track
                source
            };
            if (source) {
                album.source = source;
            }

            console.log(`✅ Retrieved album data: "${album.name}" by ${artist.name} (${tracks.length} tracks)`);

            // Create the modal using the same function as normal artist album downloads
            await openDownloadMissingModalForArtistAlbum(virtualPlaylistId, playlistName, tracks, album, artist);

            // Update the rehydrated process with batch info and hide modal for background rehydration
            const process = activeDownloadProcesses[virtualPlaylistId];
            if (process) {
                process.status = 'running';
                process.batchId = batchId;
                subscribeToDownloadBatch(batchId);

                // Update button states to reflect running status
                const beginBtn = document.getElementById(`begin-analysis-btn-${virtualPlaylistId}`);
                const cancelBtn = document.getElementById(`cancel-all-btn-${virtualPlaylistId}`);
                const wishlistBtn = document.getElementById(`add-to-wishlist-btn-${virtualPlaylistId}`);
                if (beginBtn) beginBtn.style.display = 'none';
                if (cancelBtn) cancelBtn.style.display = 'inline-block';
                if (wishlistBtn) wishlistBtn.style.display = 'none';

                // Hide the modal - this is background rehydration, not user-requested
                if (process.modalElement) {
                    process.modalElement.style.display = 'none';
                    console.log(`🔍 Hiding rehydrated modal for background processing: ${album.name}`);
                }

                console.log(`✅ Rehydrated artist album modal: ${artist.name} - ${album.name}`);
            } else {
                console.error(`❌ Failed to find rehydrated process for ${virtualPlaylistId}`);
            }

        } catch (error) {
            console.error(`❌ Error fetching album data for rehydration:`, error);
        }

    } catch (error) {
        console.error(`❌ Error rehydrating artist album modal:`, error);
    }
}

async function rehydrateDiscoverPlaylistModal(virtualPlaylistId, playlistName, batchId) {
    /**
     * Rehydrates a discover playlist download modal from backend process data.
     * Fetches tracks from the appropriate discover API endpoint and recreates the modal.
     */
    try {
        console.log(`💧 Rehydrating discover playlist modal: ${virtualPlaylistId} (${playlistName})`);

        // Handle album downloads from Recent Releases
        if (virtualPlaylistId.startsWith('discover_album_')) {
            const albumId = virtualPlaylistId.replace('discover_album_', '');
            console.log(`💧 Album download - fetching album ${albumId}...`);

            try {
                const albumResponse = await fetch(`/api/spotify/album/${albumId}`);
                if (!albumResponse.ok) {
                    console.error(`❌ Failed to fetch album: ${albumResponse.status}`);
                    return;
                }

                const albumData = await albumResponse.json();
                if (!albumData.tracks || albumData.tracks.length === 0) {
                    console.error(`❌ No tracks in album`);
                    return;
                }

                // Convert tracks to expected format
                const spotifyTracks = albumData.tracks.map(track => {
                    let artists = track.artists || [];
                    if (Array.isArray(artists)) {
                        artists = artists.map(a => a.name || a);
                    }

                    return {
                        id: track.id,
                        name: track.name,
                        artists: artists,
                        album: {
                            name: albumData.name || playlistName.split(' - ')[0],
                            images: albumData.images || []
                        },
                        duration_ms: track.duration_ms || 0
                    };
                });

                console.log(`✅ Retrieved ${spotifyTracks.length} tracks for album`);

                // Create modal
                await openDownloadMissingModalForYouTube(virtualPlaylistId, playlistName, spotifyTracks);

                // Update process
                const process = activeDownloadProcesses[virtualPlaylistId];
                if (process) {
                    process.status = 'running';
                    process.batchId = batchId;
                    subscribeToDownloadBatch(batchId);
                    const beginBtn = document.getElementById(`begin-analysis-btn-${virtualPlaylistId}`);
                    const cancelBtn = document.getElementById(`cancel-all-btn-${virtualPlaylistId}`);
                    if (beginBtn) beginBtn.style.display = 'none';
                    if (cancelBtn) cancelBtn.style.display = 'inline-block';

                    // Hide modal for background rehydration
                    if (process.modalElement) {
                        process.modalElement.style.display = 'none';
                        console.log(`🔍 Hiding rehydrated modal for background processing: ${playlistName}`);
                    }

                    console.log(`✅ Rehydrated album modal: ${playlistName}`);
                }
                return;

            } catch (error) {
                console.error(`❌ Error fetching album:`, error);
                return;
            }
        }

        // Determine API endpoint based on playlist ID
        let apiEndpoint;
        if (virtualPlaylistId === 'discover_release_radar') {
            apiEndpoint = '/api/discover/release-radar';
        } else if (virtualPlaylistId === 'discover_discovery_weekly') {
            apiEndpoint = '/api/discover/weekly';
        } else if (virtualPlaylistId === 'discover_seasonal_playlist') {
            apiEndpoint = '/api/discover/seasonal/current/playlist';
        } else if (virtualPlaylistId === 'discover_popular_picks') {
            apiEndpoint = '/api/discover/personalized/popular-picks';
        } else if (virtualPlaylistId === 'discover_hidden_gems') {
            apiEndpoint = '/api/discover/personalized/hidden-gems';
        } else if (virtualPlaylistId === 'discover_discovery_shuffle') {
            apiEndpoint = '/api/discover/personalized/discovery-shuffle';
        } else if (virtualPlaylistId === 'build_playlist_custom') {
            apiEndpoint = '/api/discover/build-playlist';
        } else if (virtualPlaylistId.startsWith('discover_lb_')) {
            console.log(`💧 ListenBrainz playlist - skipping (no automatic rehydration for ListenBrainz)`);
            return;
        } else {
            console.error(`❌ Unknown discover playlist type: ${virtualPlaylistId}`);
            return;
        }

        // Fetch tracks from API
        console.log(`📡 Fetching tracks from ${apiEndpoint}...`);
        const response = await fetch(apiEndpoint);
        if (!response.ok) {
            console.error(`❌ Failed to fetch discover playlist data: ${response.status}`);
            return;
        }

        const data = await response.json();
        if (!data.success || !data.tracks) {
            console.error(`❌ Invalid discover playlist data:`, data);
            return;
        }

        const tracks = data.tracks;
        console.log(`✅ Retrieved ${tracks.length} tracks for ${playlistName}`);

        // Transform tracks to format expected by download modal (same as openDownloadModalForDiscoverPlaylist)
        const spotifyTracks = tracks.map(track => {
            let spotifyTrack;

            // Use track_data_json if available, otherwise construct from track data
            if (track.track_data_json) {
                spotifyTrack = track.track_data_json;
            } else {
                // Fallback: construct track object from available data
                spotifyTrack = {
                    id: track.spotify_track_id,
                    name: track.track_name,
                    artists: [{ name: track.artist_name }],
                    album: {
                        name: track.album_name,
                        images: track.album_cover_url ? [{ url: track.album_cover_url }] : []
                    },
                    duration_ms: track.duration_ms || 0
                };
            }

            // Normalize artists to array of strings for modal compatibility
            if (spotifyTrack.artists && Array.isArray(spotifyTrack.artists)) {
                spotifyTrack.artists = spotifyTrack.artists.map(a => a.name || a);
            }

            return spotifyTrack;
        });

        // Create the modal using the same function as normal discover downloads
        await openDownloadMissingModalForYouTube(virtualPlaylistId, playlistName, spotifyTracks);

        // Update the rehydrated process with batch info and hide modal for background rehydration
        const process = activeDownloadProcesses[virtualPlaylistId];
        if (process) {
            process.status = 'running';
            process.batchId = batchId;
            subscribeToDownloadBatch(batchId);

            // Update button states to reflect running status
            const beginBtn = document.getElementById(`begin-analysis-btn-${virtualPlaylistId}`);
            const cancelBtn = document.getElementById(`cancel-all-btn-${virtualPlaylistId}`);
            if (beginBtn) beginBtn.style.display = 'none';
            if (cancelBtn) cancelBtn.style.display = 'inline-block';

            // Hide the modal - this is background rehydration, not user-requested
            if (process.modalElement) {
                process.modalElement.style.display = 'none';
                console.log(`🔍 Hiding rehydrated modal for background processing: ${playlistName}`);
            }

            console.log(`✅ Rehydrated discover playlist modal: ${playlistName}`);
        } else {
            console.error(`❌ Failed to find rehydrated process for ${virtualPlaylistId}`);
        }

    } catch (error) {
        console.error(`❌ Error rehydrating discover playlist modal:`, error);
    }
}

async function rehydrateEnhancedSearchModal(virtualPlaylistId, playlistName, batchId) {
    /**
     * Rehydrates an enhanced search download modal from backend process data.
     * Fetches item data from searchDownloadBubbles and recreates the modal.
     */
    try {
        console.log(`💧 Rehydrating enhanced search modal: ${virtualPlaylistId} (${playlistName})`);

        // Find the download in searchDownloadBubbles
        let downloadData = null;
        for (const artistName in searchDownloadBubbles) {
            const bubble = searchDownloadBubbles[artistName];
            const download = bubble.downloads.find(d => d.virtualPlaylistId === virtualPlaylistId);
            if (download) {
                downloadData = download;
                break;
            }
        }

        if (!downloadData) {
            console.warn(`⚠️ No download data found in searchDownloadBubbles for ${virtualPlaylistId}`);
            return;
        }

        const { item, type } = downloadData;

        if (type === 'album') {
            // For albums, fetch tracks (pass name/artist for Hydrabase support)
            console.log(`💧 Album download - fetching album ${item.id}...`);

            try {
                const _sap1 = new URLSearchParams({ name: item.name || '', artist: item.artist || '' });
                const response = await fetch(`/api/spotify/album/${item.id}?${_sap1}`);
                if (!response.ok) {
                    console.error(`❌ Failed to fetch album: ${response.status}`);
                    return;
                }

                const albumData = await response.json();
                if (!albumData.tracks || albumData.tracks.length === 0) {
                    console.error(`❌ No tracks in album`);
                    return;
                }

                const spotifyTracks = albumData.tracks.map(track => ({
                    id: track.id,
                    name: track.name,
                    artists: track.artists || [{ name: item.artists?.[0]?.name || item.artist || 'Unknown Artist' }],
                    album: {
                        name: item.name,
                        images: item.image_url ? [{ url: item.image_url }] : []
                    },
                    duration_ms: track.duration_ms || 0
                }));

                console.log(`✅ Retrieved ${spotifyTracks.length} tracks for album`);

                // Create modal
                await openDownloadMissingModalForArtistAlbum(
                    virtualPlaylistId,
                    item.name,
                    spotifyTracks,
                    item,
                    { name: item.artists?.[0]?.name || item.artist || 'Unknown Artist' },
                    false // Don't show loading overlay
                );

                // Update process
                const process = activeDownloadProcesses[virtualPlaylistId];
                if (process) {
                    process.status = 'running';
                    process.batchId = batchId;
                    subscribeToDownloadBatch(batchId);

                    const beginBtn = document.getElementById(`begin-analysis-btn-${virtualPlaylistId}`);
                    const cancelBtn = document.getElementById(`cancel-all-btn-${virtualPlaylistId}`);
                    if (beginBtn) beginBtn.style.display = 'none';
                    if (cancelBtn) cancelBtn.style.display = 'inline-block';

                    // Hide modal for background rehydration
                    if (process.modalElement) {
                        process.modalElement.style.display = 'none';
                        console.log(`🔍 Hiding rehydrated modal for background processing: ${playlistName}`);
                    }

                    // Start polling for live updates
                    startModalDownloadPolling(virtualPlaylistId);

                    console.log(`✅ Rehydrated enhanced search album modal: ${playlistName}`);
                } else {
                    console.error(`❌ Failed to find rehydrated process for ${virtualPlaylistId}`);
                }

            } catch (error) {
                console.error(`❌ Error fetching album:`, error);
            }

        } else {
            // For tracks, create enriched track and open modal
            console.log(`💧 Track download - creating modal for ${item.name}...`);

            const enrichedTrack = {
                id: item.id,
                name: item.name,
                artists: item.artists || [{ name: item.artist || 'Unknown Artist' }],
                album: item.album || {
                    name: item.album?.name || 'Unknown Album',
                    images: item.image_url ? [{ url: item.image_url }] : []
                },
                duration_ms: item.duration_ms || 0
            };

            // Create modal
            await openDownloadMissingModalForYouTube(
                virtualPlaylistId,
                `${enrichedTrack.name} - ${enrichedTrack.artists[0].name || enrichedTrack.artists[0]}`,
                [enrichedTrack]
            );

            // Update process
            const process = activeDownloadProcesses[virtualPlaylistId];
            if (process) {
                process.status = 'running';
                process.batchId = batchId;
                subscribeToDownloadBatch(batchId);

                const beginBtn = document.getElementById(`begin-analysis-btn-${virtualPlaylistId}`);
                const cancelBtn = document.getElementById(`cancel-all-btn-${virtualPlaylistId}`);
                if (beginBtn) beginBtn.style.display = 'none';
                if (cancelBtn) cancelBtn.style.display = 'inline-block';

                // Hide modal for background rehydration
                if (process.modalElement) {
                    process.modalElement.style.display = 'none';
                    console.log(`🔍 Hiding rehydrated modal for background processing: ${playlistName}`);
                }

                // Start polling for live updates
                startModalDownloadPolling(virtualPlaylistId);

                console.log(`✅ Rehydrated enhanced search track modal: ${playlistName}`);
            } else {
                console.error(`❌ Failed to find rehydrated process for ${virtualPlaylistId}`);
            }
        }

    } catch (error) {
        console.error(`❌ Error rehydrating enhanced search modal:`, error);
    }
}

async function rehydrateModal(processInfo, userRequested = false) {
    const { playlist_id, playlist_name, batch_id, current_cycle } = processInfo;
    console.log(`💧 Rehydrating modal for "${playlist_name}" (batch: ${batch_id}) - User requested: ${userRequested}`);

    // Handle YouTube virtual playlists - skip rehydration here, handled by YouTube system
    if (playlist_id.startsWith('youtube_')) {
        console.log(`⏭️ Skipping YouTube virtual playlist rehydration - handled by YouTube system`);
        return;
    }

    // Handle Beatport virtual playlists - skip rehydration here, handled by Beatport system
    if (playlist_id.startsWith('beatport_')) {
        console.log(`⏭️ Skipping Beatport virtual playlist rehydration - handled by Beatport system`);
        return;
    }

    // Handle artist album virtual playlists
    if (playlist_id.startsWith('artist_album_')) {
        console.log(`💧 Rehydrating artist album virtual playlist: ${playlist_id}`);
        await rehydrateArtistAlbumModal(playlist_id, playlist_name, batch_id);
        return;
    }

    // Handle discover virtual playlists (Fresh Tape, The Archives)
    if (playlist_id.startsWith('discover_')) {
        console.log(`💧 Rehydrating discover playlist: ${playlist_id}`);
        await rehydrateDiscoverPlaylistModal(playlist_id, playlist_name, batch_id);
        return;
    }

    // Handle enhanced search virtual playlists (albums and tracks)
    if (playlist_id.startsWith('enhanced_search_album_') || playlist_id.startsWith('enhanced_search_track_')) {
        console.log(`💧 Rehydrating enhanced search virtual playlist: ${playlist_id}`);
        await rehydrateEnhancedSearchModal(playlist_id, playlist_name, batch_id);
        return;
    }

    // Handle wishlist processes specially
    if (playlist_id === "wishlist") {
        console.log(`💧 [Rehydrate] Handling wishlist modal for active process: ${batch_id}`);

        // Check if modal already exists and is visible
        const existingProcess = activeDownloadProcesses[playlist_id];
        const modalAlreadyOpen = existingProcess && existingProcess.modalElement &&
            existingProcess.modalElement.style.display === 'flex';

        if (modalAlreadyOpen) {
            console.log(`💧 [Rehydrate] Wishlist modal already open - updating existing modal with auto-process state`);

            // Update existing process with new batch info
            existingProcess.status = 'running';
            existingProcess.batchId = batch_id;

            // Update UI to reflect running state
            const beginBtn = document.getElementById(`begin-analysis-btn-${playlist_id}`);
            const cancelBtn = document.getElementById(`cancel-all-btn-${playlist_id}`);
            if (beginBtn) beginBtn.style.display = 'none';
            if (cancelBtn) cancelBtn.style.display = 'inline-block';

            // Ensure polling is active for live updates
            if (!existingProcess.intervalId) {
                console.log(`💧 [Rehydrate] Starting polling for existing modal`);
                startModalDownloadPolling(playlist_id);
            }

            console.log(`✅ [Rehydrate] Successfully updated existing wishlist modal for auto-process`);
        } else {
            // Only create modal if user requested it - don't create for background auto-processing
            if (userRequested) {
                console.log(`💧 [Rehydrate] User requested - creating wishlist modal for active process: ${batch_id}`);

                // Create the modal with current server state (pass category filter for auto-processing)
                await openDownloadMissingWishlistModal(current_cycle);
                const process = activeDownloadProcesses[playlist_id];
                if (!process) {
                    console.error('❌ [Rehydrate] Failed to create wishlist process in activeDownloadProcesses');
                    return;
                }

                // Sync process state with server
                console.log(`✅ [Rehydrate] Syncing wishlist process state - batchId: ${batch_id}, status: running`);
                process.status = 'running';
                process.batchId = batch_id;

                // Update UI to reflect running state
                const beginBtn = document.getElementById(`begin-analysis-btn-${playlist_id}`);
                const cancelBtn = document.getElementById(`cancel-all-btn-${playlist_id}`);
                if (beginBtn) beginBtn.style.display = 'none';
                if (cancelBtn) cancelBtn.style.display = 'inline-block';

                // Start polling for live updates
                startModalDownloadPolling(playlist_id);

                // Show modal
                console.log('👤 [Rehydrate] User requested - showing wishlist modal');
                process.modalElement.style.display = 'flex';
                WishlistModalState.setVisible();
                WishlistModalState.clearUserClosed();
            } else {
                console.log('🔄 [Rehydrate] Background auto-processing detected - NOT creating modal (user must click wishlist button to see progress)');
                // Don't create modal for background auto-processing
                // User must click the wishlist button to see the modal
            }
        }
        return;
    }

    // Handle Deezer ARL playlist processes — ensure playlist data is in spotifyPlaylists for modal reuse
    if (playlist_id.startsWith('deezer_arl_') && !spotifyPlaylists.find(p => p.id === playlist_id)) {
        const rawId = playlist_id.replace('deezer_arl_', '');
        const deezerPlaylist = deezerArlPlaylists.find(p => String(p.id) === rawId);
        if (deezerPlaylist) {
            spotifyPlaylists.push({
                id: playlist_id,
                name: deezerPlaylist.name,
                track_count: deezerPlaylist.track_count || 0,
                image_url: deezerPlaylist.image_url || '',
                owner: deezerPlaylist.owner || '',
            });
        } else {
            // Playlists not loaded yet — use process info as fallback
            spotifyPlaylists.push({
                id: playlist_id,
                name: playlist_name || 'Deezer Playlist',
                track_count: 0,
            });
        }
    }

    // Handle regular Spotify / Deezer ARL playlist processes
    let playlistData = spotifyPlaylists.find(p => p.id === playlist_id);
    if (!playlistData) {
        console.warn(`Cannot rehydrate modal: Playlist data for ${playlist_id} not loaded.`);
        return;
    }
    await openDownloadMissingModal(playlist_id);
    const process = activeDownloadProcesses[playlist_id];
    if (!process) return;

    process.status = 'running';
    process.batchId = batch_id;
    updatePlaylistCardUI(playlist_id);
    updateRefreshButtonState();

    document.getElementById(`begin-analysis-btn-${playlist_id}`).style.display = 'none';
    document.getElementById(`cancel-all-btn-${playlist_id}`).style.display = 'inline-block';

    // Hide wishlist button if it exists
    const wishlistBtn = document.getElementById(`add-to-wishlist-btn-${playlist_id}`);
    if (wishlistBtn) wishlistBtn.style.display = 'none';

    startModalDownloadPolling(playlist_id);

    process.modalElement.style.display = 'none';
}

// ===================================================================
// YOUTUBE PLAYLIST BACKEND HYDRATION FUNCTIONS
// ===================================================================

async function loadListenBrainzPlaylistsFromBackend() {
    // Load all stored ListenBrainz playlist states from backend for persistence (similar to Beatport hydration)
    try {
        console.log('📋 Loading ListenBrainz playlists from backend...');

        const response = await fetch('/api/listenbrainz/playlists');
        if (!response.ok) {
            const error = await response.json();
            throw new Error(error.error || 'Failed to fetch ListenBrainz playlists');
        }

        const data = await response.json();
        const playlists = data.playlists || [];

        console.log(`🎵 Found ${playlists.length} stored ListenBrainz playlists in backend`);

        if (playlists.length === 0) {
            console.log('📋 No ListenBrainz playlists to hydrate');
            listenbrainzPlaylistsLoaded = true;
            return;
        }

        // Restore state for each playlist
        for (const playlistInfo of playlists) {
            const playlistMbid = playlistInfo.playlist_mbid;

            console.log(`🎵 Hydrating ListenBrainz playlist: ${playlistInfo.playlist.name} (Phase: ${playlistInfo.phase}, MBID: ${playlistMbid})`);

            // Fetch full state for non-fresh playlists to restore discovery results
            if (playlistInfo.phase !== 'fresh') {
                try {
                    console.log(`🔍 Fetching full state for: ${playlistInfo.playlist.name}`);
                    const stateResponse = await fetch(`/api/listenbrainz/state/${playlistMbid}`);
                    if (stateResponse.ok) {
                        const fullState = await stateResponse.json();
                        console.log(`📋 Retrieved full state with ${fullState.discovery_results?.length || 0} discovery results`);

                        // Transform backend results to frontend format (like Beatport does)
                        const transformedResults = (fullState.discovery_results || []).map((result, index) => ({
                            index: result.index !== undefined ? result.index : index,
                            yt_track: result.lb_track || result.track_name || 'Unknown',
                            yt_artist: result.lb_artist || result.artist_name || 'Unknown',
                            status: result.status === 'found' || result.status === '✅ Found' || result.status_class === 'found' ? '✅ Found' : (result.status === 'error' ? '❌ Error' : '❌ Not Found'),
                            status_class: result.status_class || (result.status === 'found' || result.status === '✅ Found' ? 'found' : (result.status === 'error' ? 'error' : 'not-found')),
                            spotify_track: result.spotify_data ? result.spotify_data.name : (result.spotify_track || '-'),
                            spotify_artist: result.spotify_data && result.spotify_data.artists ?
                                (Array.isArray(result.spotify_data.artists) ? (typeof result.spotify_data.artists[0] === 'object' ? result.spotify_data.artists[0].name : result.spotify_data.artists[0]) : result.spotify_data.artists) : (result.spotify_artist || '-'),
                            spotify_album: result.spotify_data ? (typeof result.spotify_data.album === 'object' ? result.spotify_data.album.name : result.spotify_data.album) : (result.spotify_album || '-'),
                            spotify_data: result.spotify_data,
                            duration: result.duration || '0:00'
                        }));

                        // Create ListenBrainz state with both naming conventions
                        listenbrainzPlaylistStates[playlistMbid] = {
                            phase: fullState.phase,
                            playlist: fullState.playlist,
                            is_listenbrainz_playlist: true,
                            playlist_mbid: playlistMbid,
                            // Store with both naming conventions
                            discovery_results: fullState.discovery_results || [],
                            discoveryResults: transformedResults,
                            discovery_progress: fullState.discovery_progress || 0,
                            discoveryProgress: fullState.discovery_progress || 0,
                            spotify_matches: fullState.spotify_matches || 0,
                            spotifyMatches: fullState.spotify_matches || 0,
                            spotify_total: fullState.spotify_total || 0,
                            spotifyTotal: fullState.spotify_total || 0,
                            convertedSpotifyPlaylistId: fullState.converted_spotify_playlist_id,
                            download_process_id: fullState.download_process_id
                        };

                        console.log(`✅ Restored ${transformedResults.length} discovery results for: ${playlistInfo.playlist.name}`);
                    } else {
                        console.warn(`⚠️ Could not fetch full state for: ${playlistInfo.playlist.name}`);
                    }
                } catch (error) {
                    console.warn(`⚠️ Error fetching full state for ${playlistInfo.playlist.name}:`, error.message);
                }
            }
        }

        // Start polling for any playlists that are still in discovering phase
        for (const playlistInfo of playlists) {
            if (playlistInfo.phase === 'discovering') {
                console.log(`🔄 [Backend Loading] Auto-starting polling for discovering playlist: ${playlistInfo.playlist.name}`);
                startListenBrainzDiscoveryPolling(playlistInfo.playlist_mbid);
            }
            // Show sync button for discovered playlists (hidden by default)
            else if (playlistInfo.phase === 'discovered' || playlistInfo.phase === 'syncing' || playlistInfo.phase === 'sync_complete') {
                const playlistId = `discover-lb-playlist-${playlistInfo.playlist_mbid}`;
                const syncBtn = document.getElementById(`${playlistId}-sync-btn`);
                if (syncBtn) {
                    syncBtn.style.display = 'inline-block';
                    console.log(`✅ Showing sync button for discovered playlist: ${playlistInfo.playlist.name}`);
                }
            }
        }

        listenbrainzPlaylistsLoaded = true;
        console.log(`✅ Successfully loaded and rehydrated ${playlists.length} ListenBrainz playlists`);

    } catch (error) {
        console.error('❌ Error loading ListenBrainz playlists from backend:', error);
        listenbrainzPlaylistsLoaded = true;  // Mark as loaded even on error to prevent retries
    }
}

function getActionButtonText(phase) {
    switch (phase) {
        case 'fresh': return 'Identify';
        case 'discovering': return 'View Progress';
        case 'discovered': return 'View Results';
        case 'syncing': return 'View Sync';
        case 'sync_complete': return 'Download';
        case 'downloading': return 'View Downloads';
        case 'download_complete': return 'Complete';
        default: return 'Open';
    }
}

function getPhaseText(phase) {
    switch (phase) {
        case 'fresh': return 'Ready to identify';
        case 'discovering': return 'Identifying...';
        case 'discovered': return 'Identification Complete';
        case 'syncing': return 'Syncing...';
        case 'sync_complete': return 'Sync Complete';
        case 'downloading': return 'Downloading...';
        case 'download_complete': return 'Download Complete';
        default: return phase;
    }
}

function getPhaseColor(phase) {
    switch (phase) {
        case 'fresh': return '#999';
        case 'discovering': case 'syncing': case 'downloading': return '#ffa500';
        case 'discovered': case 'sync_complete': case 'download_complete': return 'rgb(var(--accent-rgb))';
        default: return '#999';
    }
}

function updatePlaylistCardUI(playlistId) {
    const process = activeDownloadProcesses[playlistId];
    const progressBtn = document.getElementById(`progress-btn-${playlistId}`);
    const actionBtn = document.getElementById(`action-btn-${playlistId}`);
    const card = document.querySelector(`.playlist-card[data-playlist-id="${playlistId}"]`);

    if (!progressBtn || !actionBtn) return;

    if (process && process.status === 'running') {
        // A process is running: show the progress button
        progressBtn.classList.remove('hidden');
        progressBtn.textContent = 'View Progress';
        progressBtn.style.backgroundColor = '';  // Reset any custom styling
        actionBtn.textContent = '📥 Downloading...';
        actionBtn.disabled = true;

        // Remove completion styling from card
        if (card) card.classList.remove('download-complete');

    } else if (process && process.status === 'complete') {
        // Process completed: show "ready for review" indicator
        progressBtn.classList.remove('hidden');
        progressBtn.textContent = '📋 View Results';
        progressBtn.style.backgroundColor = '#28a745'; // Green success color
        progressBtn.style.color = 'white';
        actionBtn.textContent = '✅ Ready for Review';
        actionBtn.disabled = false; // Allow clicking to see results

        // Add completion styling to card
        if (card) card.classList.add('download-complete');

    } else {
        // No process or it's been cleaned up: normal state
        progressBtn.classList.add('hidden');
        progressBtn.style.backgroundColor = '';  // Reset styling
        progressBtn.style.color = '';  // Reset styling
        actionBtn.textContent = 'Sync / Download';
        actionBtn.disabled = false;

        // Remove completion styling from card
        if (card) card.classList.remove('download-complete');
    }
}

async function cleanupDownloadProcess(playlistId) {
    const process = activeDownloadProcesses[playlistId];
    if (!process) return;

    console.log(`🧹 Cleaning up download process for playlist ${playlistId}`);

    // Stop any active polling first
    if (process.poller) {
        console.log(`🛑 Stopping individual polling for ${playlistId}`);
        clearInterval(process.poller);
        process.poller = null;
    }

    // Mark process as no longer running
    if (process.status === 'running') {
        process.status = 'complete';
    }

    // If the process has a batchId, tell the server to clean it up.
    if (process.batchId) {
        try {
            console.log(`🚀 Sending cleanup request to server for batch: ${process.batchId}`);
            const response = await fetch('/api/playlists/cleanup_batch', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ batch_id: process.batchId })
            });

            // Handle deferred cleanup (202 = wishlist processing in progress)
            if (response.status === 202) {
                console.log(`⏳ Wishlist processing in progress for batch ${process.batchId}, will retry cleanup in 2s...`);
                // Retry cleanup after delay to allow wishlist processing to complete
                setTimeout(async () => {
                    try {
                        await fetch('/api/playlists/cleanup_batch', {
                            method: 'POST',
                            headers: { 'Content-Type': 'application/json' },
                            body: JSON.stringify({ batch_id: process.batchId })
                        });
                        console.log(`✅ Delayed cleanup completed for batch: ${process.batchId}`);
                    } catch (error) {
                        console.warn(`⚠️ Delayed cleanup failed:`, error);
                    }
                }, 2000); // 2 second delay
            } else {
                console.log(`✅ Server cleanup completed for batch: ${process.batchId}`);
            }
        } catch (error) {
            console.warn(`⚠️ Failed to send cleanup request to server:`, error);
            // Don't show toast for cleanup failures - they're not user-facing
        }
    }

    // Remove modal from DOM
    if (process.modalElement && process.modalElement.parentElement) {
        process.modalElement.parentElement.removeChild(process.modalElement);
    }

    // Remove from client-side global state
    delete activeDownloadProcesses[playlistId];

    // Check if global polling should be stopped
    checkAndCleanupGlobalPolling();

    // Restore card UI (only for non-wishlist playlists)
    if (playlistId !== 'wishlist') {
        updatePlaylistCardUI(playlistId);
    }
    updateRefreshButtonState(); // Now safe since hasActiveOperations() excludes wishlist
}

function closePlaylistDetailsModal() {
    const modal = document.getElementById('playlist-details-modal');
    if (modal) {
        modal.style.display = 'none';
    }
}

// ===============================
// DOWNLOAD MISSING TRACKS MODAL
// ===============================

let activeAnalysisTaskId = null;
let currentPlaylistTracks = [];
let analysisResults = [];
let missingTracks = [];

// New variables for enhanced modal functionality
let currentDownloadBatchId = null;

// ===============================
// HERO SECTION HELPER FUNCTIONS
// ===============================

/**
 * Generate hero section HTML for download missing tracks modal
 * Context-aware display based on available data
 */
function generateDownloadModalHeroSection(context) {
    const { type, playlist, artist, album, trackCount } = context;

    let heroContent = '';
    let heroBackgroundImage = '';

    switch (type) {
        case 'album':
        case 'artist_album':
            // Artist/album context - show artist + album images
            const artistImage = artist?.image_url || artist?.images?.[0]?.url;
            const albumImage = album?.image_url || album?.images?.[0]?.url;
            const artistSource = artist?.source || album?.source || context.source || '';
            const sourceKey = (artistSource || '').toString().toLowerCase();
            const sourceIdFields = {
                spotify: ['spotify_artist_id', 'id', 'artist_id'],
                itunes: ['itunes_artist_id', 'artist_id', 'id'],
                deezer: ['deezer_artist_id', 'deezer_id', 'artist_id', 'id'],
                discogs: ['discogs_artist_id', 'discogs_id', 'artist_id', 'id'],
                amazon: ['amazon_artist_id', 'amazon_id', 'artist_id', 'id'],
                hydrabase: ['soul_id', 'hydrabase_artist_id', 'artist_id', 'id'],
                musicbrainz: ['musicbrainz_id', 'artist_id', 'id'],
            };
            let detailArtistId = artist?.id || artist?.artist_id || '';
            for (const field of (sourceIdFields[sourceKey] || ['artist_id', 'id'])) {
                const candidate = artist?.[field];
                if (candidate) {
                    detailArtistId = candidate;
                    break;
                }
            }
            if (detailArtistId && String(detailArtistId).toLowerCase() === String(artist?.name || '').toLowerCase()) {
                detailArtistId = '';
            }
            const artistHref = detailArtistId ? buildArtistDetailPath(detailArtistId, artistSource || null) : '#';

            // Use album image as background if available
            if (albumImage) {
                heroBackgroundImage = `<div class="download-missing-modal-hero-bg" style="background-image: url('${albumImage}');"></div>`;
            }

            heroContent = `
                <div class="download-missing-modal-hero-content">
                    <div class="download-missing-modal-hero-images">
                        ${artistImage ? `<img class="download-missing-modal-hero-image artist" src="${artistImage}" alt="${escapeHtml(artist.name)}">` : ''}
                        ${albumImage ? `<img class="download-missing-modal-hero-image album" src="${albumImage}" alt="${escapeHtml(album.name)}">` : ''}
                    </div>
                    <div class="download-missing-modal-hero-metadata">
                        <h1 class="download-missing-modal-hero-title">${escapeHtml(album.name || 'Unknown Album')}</h1>
                        <div class="download-missing-modal-hero-subtitle">by <a href="${artistHref}" class="hero-artist-link" onclick="closeDownloadMissingModal('${escapeForInlineJs(context.playlistId || '')}')" style="text-decoration:none;color:inherit;">${escapeHtml(artist.name || 'Unknown Artist')}</a></div>
                        <div class="download-missing-modal-hero-details">
                            <span class="download-missing-modal-hero-detail">${album.album_type || 'Album'}</span>
                            <span class="download-missing-modal-hero-detail">${trackCount} tracks</span>
                        </div>
                    </div>
                </div>
            `;
            break;

        case 'playlist':
            // Playlist context - show playlist info
            heroContent = `
                <div class="download-missing-modal-hero-content">
                    <div class="download-missing-modal-hero-icon">🎵</div>
                    <div class="download-missing-modal-hero-metadata">
                        <h1 class="download-missing-modal-hero-title">${escapeHtml(playlist.name)}</h1>
                        <div class="download-missing-modal-hero-subtitle">by ${escapeHtml(playlist.owner || 'Spotify')}</div>
                        <div class="download-missing-modal-hero-details">
                            <span class="download-missing-modal-hero-detail">Playlist</span>
                            <span class="download-missing-modal-hero-detail">${trackCount} tracks</span>
                        </div>
                    </div>
                </div>
            `;
            break;

        case 'wishlist':
            // Wishlist context - show wishlist icon
            heroContent = `
                <div class="download-missing-modal-hero-content">
                    <div class="download-missing-modal-hero-icon">👁️</div>
                    <div class="download-missing-modal-hero-metadata">
                        <h1 class="download-missing-modal-hero-title">Wishlist</h1>
                        <div class="download-missing-modal-hero-subtitle">From watched artists</div>
                        <div class="download-missing-modal-hero-details">
                            <span class="download-missing-modal-hero-detail">Wishlist</span>
                            <span class="download-missing-modal-hero-detail">${trackCount} tracks</span>
                        </div>
                    </div>
                </div>
            `;
            break;

        default:
            // Fallback - basic display
            heroContent = `
                <div class="download-missing-modal-hero-content">
                    <div class="download-missing-modal-hero-icon">📥</div>
                    <div class="download-missing-modal-hero-metadata">
                        <h1 class="download-missing-modal-hero-title">Download Missing Tracks</h1>
                        <div class="download-missing-modal-hero-subtitle">${trackCount} tracks</div>
                    </div>
                </div>
            `;
            break;
    }

    return `
        <div class="download-missing-modal-hero">
            ${heroBackgroundImage}
            ${heroContent}
            <div class="download-dashboard-stats">
                <div class="dashboard-stat stat-total">
                    <div class="dashboard-stat-number" id="stat-total-${context.playlistId}">${context.trackCount}</div>
                    <div class="dashboard-stat-label">Total</div>
                </div>
                <div class="dashboard-stat stat-found">
                    <div class="dashboard-stat-number" id="stat-found-${context.playlistId}">-</div>
                    <div class="dashboard-stat-label">Found</div>
                </div>
                <div class="dashboard-stat stat-missing">
                    <div class="dashboard-stat-number" id="stat-missing-${context.playlistId}">-</div>
                    <div class="dashboard-stat-label">Missing</div>
                </div>
                <div class="dashboard-stat stat-downloaded">
                    <div class="dashboard-stat-number" id="stat-downloaded-${context.playlistId}">0</div>
                    <div class="dashboard-stat-label">Downloaded</div>
                </div>
            </div>
        </div>
        <div class="download-missing-modal-header-actions">
            <span class="download-missing-modal-close" onclick="closeDownloadMissingModal('${context.playlistId || 'unknown'}')">&times;</span>
        </div>
    `;
}
let modalDownloadPoller = null;
let currentModalPlaylistId = null;

// PHASE 2: Local cancelled track management (GUI PARITY)
let cancelledTracks = new Set(); // Track cancelled track indices like GUI's cancelled_tracks

const TRACK_RENDER_BATCH_SIZE = 100;

function applyProgressiveTrackRendering(playlistId, totalTrackCount) {
    if (totalTrackCount <= TRACK_RENDER_BATCH_SIZE) return;

    const modal = document.getElementById(`download-missing-modal-${playlistId}`);
    if (!modal) return;

    const tbody = document.getElementById(`download-tracks-tbody-${playlistId}`);
    if (!tbody) return;

    const rows = tbody.querySelectorAll('tr[data-track-index]');
    if (rows.length <= TRACK_RENDER_BATCH_SIZE) return;

    // Hide rows beyond first batch
    for (let i = TRACK_RENDER_BATCH_SIZE; i < rows.length; i++) {
        rows[i].classList.add('hidden');
    }

    let revealedCount = TRACK_RENDER_BATCH_SIZE;

    // Append indicator into .download-tracks-title
    const titleEl = modal.querySelector('.download-tracks-title');
    if (titleEl) {
        const indicator = document.createElement('span');
        indicator.className = 'track-render-indicator';
        indicator.id = `track-render-indicator-${playlistId}`;
        indicator.textContent = `Showing ${revealedCount} of ${totalTrackCount} tracks`;
        titleEl.appendChild(indicator);
    }

    // Scroll listener on table container
    const container = modal.querySelector('.download-tracks-table-container');
    if (!container) return;

    container.addEventListener('scroll', function onScroll() {
        const scrollBottom = container.scrollHeight - container.scrollTop - container.clientHeight;
        if (scrollBottom > 200) return;
        if (revealedCount >= rows.length) return;

        const nextEnd = Math.min(revealedCount + TRACK_RENDER_BATCH_SIZE, rows.length);
        for (let i = revealedCount; i < nextEnd; i++) {
            rows[i].classList.remove('hidden');
        }
        revealedCount = nextEnd;

        const indicator = document.getElementById(`track-render-indicator-${playlistId}`);
        if (indicator) {
            indicator.textContent = revealedCount >= rows.length
                ? `Showing all ${totalTrackCount} tracks`
                : `Showing ${revealedCount} of ${totalTrackCount} tracks`;
        }

        if (revealedCount >= rows.length) {
            container.removeEventListener('scroll', onScroll);
        }
    });
}

async function openDownloadMissingModal(playlistId) {
    showLoadingOverlay('Loading playlist...');

    // **NEW**: Check if a process is already active for this playlist
    const playlistMeta = spotifyPlaylists.find(p => p.id === playlistId);
    const processStale = typeof isPlaylistDownloadProcessStale === 'function'
        ? isPlaylistDownloadProcessStale(playlistId, playlistMeta)
        : (typeof playlistTrackCacheIsStale === 'function'
            && playlistTrackCacheIsStale(playlistId, playlistMeta));

    if (activeDownloadProcesses[playlistId] && !processStale) {
        console.log(`Modal for ${playlistId} already exists. Showing it.`);
        closePlaylistDetailsModal();
        const process = activeDownloadProcesses[playlistId];
        if (process.modalElement) {
            if (process.status === 'complete') {
                showToast('Showing previous results. Use "Download Missing (New)" for a fresh run.', 'info');
            }
            process.modalElement.style.display = 'flex';
        }
        if (typeof refreshOrganizePreferenceForDownloadModal === 'function') {
            await refreshOrganizePreferenceForDownloadModal(playlistId);
        }
        hideLoadingOverlay();
        return;
    }
    if (processStale && activeDownloadProcesses[playlistId]) {
        if (typeof restartPlaylistDownloadMissing === 'function') {
            await restartPlaylistDownloadMissing(playlistId);
            hideLoadingOverlay();
            return;
        }
        if (typeof clearPlaylistDownloadProcess === 'function') {
            clearPlaylistDownloadProcess(playlistId);
        } else if (typeof invalidatePlaylistTrackCache === 'function') {
            invalidatePlaylistTrackCache(playlistId);
        }
    }

    console.log(`📥 Opening Download Missing Tracks modal for playlist: ${playlistId}`);

    closePlaylistDetailsModal();
    const playlist = spotifyPlaylists.find(p => p.id === playlistId);
    if (!playlist) {
        showToast('Could not find playlist data.', 'error');
        hideLoadingOverlay();
        return;
    }

    let tracks = playlistTrackCache[playlistId];
    const needFreshTracks = !tracks || (
        typeof playlistTrackCacheIsStale === 'function'
        && playlistTrackCacheIsStale(playlistId, playlist)
    );
    if (needFreshTracks) {
        try {
            if (playlistId.startsWith('deezer_arl_')) {
                const fetchUrl = `/api/deezer/arl-playlist/${playlistId.replace('deezer_arl_', '')}`;
                const response = await fetch(fetchUrl);
                const fullPlaylist = await response.json();
                if (fullPlaylist.error) throw new Error(fullPlaylist.error);
                tracks = fullPlaylist.tracks;
                playlistTrackCache[playlistId] = tracks;
            } else if (typeof fetchAndCacheSpotifyPlaylistTracks === 'function') {
                const fullPlaylist = await fetchAndCacheSpotifyPlaylistTracks(playlistId);
                tracks = fullPlaylist.tracks;
            } else {
                const response = await fetch(`/api/spotify/playlist/${playlistId}`);
                const fullPlaylist = await response.json();
                if (fullPlaylist.error) throw new Error(fullPlaylist.error);
                tracks = fullPlaylist.tracks;
                playlistTrackCache[playlistId] = tracks;
            }
        } catch (error) {
            showToast(`Failed to fetch tracks: ${error.message}`, 'error');
            hideLoadingOverlay();
            return;
        }
    }

    currentPlaylistTracks = tracks;
    currentModalPlaylistId = playlistId;

    let modal = document.createElement('div');
    modal.id = `download-missing-modal-${playlistId}`; // **NEW**: Unique ID
    modal.className = 'download-missing-modal'; // **NEW**: Use class for styling
    modal.style.display = 'none'; // Start hidden
    document.body.appendChild(modal);

    // **NEW**: Register the new process in our global state tracker
    activeDownloadProcesses[playlistId] = {
        status: 'idle', // idle, running, complete, cancelled
        modalElement: modal,
        poller: null,
        batchId: null,
        playlist: playlist,
        tracks: tracks
    };

    // Generate hero section for playlist context
    const heroContext = {
        type: 'playlist',
        playlist: playlist,
        trackCount: tracks.length,
        playlistId: playlistId
    };

    modal.innerHTML = `
        <div class="download-missing-modal-content" data-context="playlist">
            <div class="download-missing-modal-header">
                ${generateDownloadModalHeroSection(heroContext)}
            </div>

            ${renderDownloadModalBody(playlistId, tracks)}
            
            <div class="download-missing-modal-footer">
                <div class="download-phase-controls">
                    <div class="force-download-toggle-container" style="margin-bottom: 0px; display: flex; flex-direction: column; gap: 8px; align-items: flex-start;">
                        <label class="force-download-toggle">
                            <input type="checkbox" id="force-download-all-${playlistId}">
                            <span>Force Download All</span>
                        </label>
                        ${typeof downloadMissingModalOrganizeCheckboxHtml === 'function'
                            ? downloadMissingModalOrganizeCheckboxHtml(playlistId)
                            : `<input type="checkbox" id="playlist-folder-mode-${playlistId}" class="playlist-folder-mode-sync">`}
                    </div>
                    <button class="download-control-btn primary" id="begin-analysis-btn-${playlistId}" onclick="startMissingTracksProcess('${playlistId}')">
                        Begin Analysis
                    </button>
                    <button class="download-control-btn" id="add-to-wishlist-btn-${playlistId}" onclick="addModalTracksToWishlist('${playlistId}')" style="background-color: #9333ea; color: white;">
                        ${typeof wishlistAddLabel === 'function' ? wishlistAddLabel() : 'Add to Wishlist'}
                    </button>
                    <button class="download-control-btn danger" id="cancel-all-btn-${playlistId}" onclick="cancelAllOperations('${playlistId}')" style="display: none;">
                        Cancel All
                    </button>
                </div>
                <div class="modal-close-section">
                    <button class="download-control-btn export" onclick="exportPlaylistAsM3U('${playlistId}')">
                        📋 Export as M3U
                    </button>
                    <button class="download-control-btn secondary" onclick="closeDownloadMissingModal('${playlistId}')">Close</button>
                </div>
            </div>
        </div>
    `;

    applyProgressiveTrackRendering(playlistId, tracks.length);
    await applyMirroredOrganizePreference(playlistId);
    modal.style.display = 'flex';
    hideLoadingOverlay();
}

async function autoSavePlaylistM3U(playlistId) {
    /**
     * Automatically save M3U file server-side for playlist modals only.
     * Albums are skipped — they're already grouped by media servers.
     * The server checks the m3u_export.enabled setting before writing.
     * Uses real DB file paths via /api/generate-playlist-m3u.
     */
    const process = activeDownloadProcesses[playlistId];
    if (!process || !process.tracks || process.tracks.length === 0) {
        return;
    }

    const modal = document.getElementById(`download-missing-modal-${playlistId}`);
    if (!modal) return;

    // Skip M3U for non-playlist downloads — albums, singles, redownloads, etc.
    const nonPlaylistPrefixes = [
        'artist_album_', 'discover_album_', 'enhanced_search_album_', 'enhanced_search_track_',
        'seasonal_album_', 'spotify_library_', 'beatport_release_', 'discover_cache_',
        'issue_download_', 'library_redownload_', 'redownload_',
    ];
    if (nonPlaylistPrefixes.some(p => playlistId.startsWith(p))) return;

    const playlistName = process.playlist?.name || process.playlistName || 'Playlist';
    const artistName = process.artist?.name || '';
    const albumName = process.album?.name || '';
    const releaseDate = process.album?.release_date || '';
    const year = releaseDate ? releaseDate.substring(0, 4) : '';

    try {
        const response = await fetch('/api/generate-playlist-m3u', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                playlist_name: playlistName,
                tracks: _extractM3UTracks(process.tracks),
                context_type: 'playlist',
                artist_name: artistName,
                album_name: albumName,
                year: year,
                save_to_disk: true
            })
        });

        if (response.ok) {
            console.log(`✅ Auto-saved M3U for playlist: ${playlistName}`);
        } else {
            console.warn(`⚠️ Failed to auto-save M3U for ${playlistName}`);
        }
    } catch (error) {
        console.debug('Auto-save M3U error (non-critical):', error);
    }
}

async function exportPlaylistAsM3U(playlistId) {
    /**
     * Export the tracks from the download missing tracks modal as an M3U playlist file.
     * Downloads via browser AND saves server-side to the relevant folder (force=true).
     * Uses real DB file paths via /api/generate-playlist-m3u.
     */
    console.log(`📋 Exporting playlist ${playlistId} as M3U`);

    const process = activeDownloadProcesses[playlistId];
    if (!process || !process.tracks || process.tracks.length === 0) {
        showToast('No tracks available to export', 'warning');
        return;
    }

    const playlistName = process.playlist?.name || process.playlistName || 'Playlist';
    const albumPrefixes = ['artist_album_', 'discover_album_', 'enhanced_search_album_', 'seasonal_album_', 'spotify_library_', 'beatport_release_', 'discover_cache_'];
    const isAlbumExport = albumPrefixes.some(p => playlistId.startsWith(p));
    const releaseDate = process.album?.release_date || '';
    const year = releaseDate ? releaseDate.substring(0, 4) : '';

    let m3uContent, foundCount, missingCount;
    try {
        const response = await fetch('/api/generate-playlist-m3u', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                playlist_name: playlistName,
                tracks: _extractM3UTracks(process.tracks),
                context_type: isAlbumExport ? 'album' : 'playlist',
                artist_name: process.artist?.name || '',
                album_name: process.album?.name || '',
                year: year,
                save_to_disk: true,
                force: true
            })
        });
        const data = await response.json();
        if (!data.success) throw new Error(data.error || 'Unknown error');
        m3uContent = data.m3u_content;
        foundCount = (data.stats?.found || 0) + (data.stats?.downloaded || 0);
        missingCount = data.stats?.missing || 0;
    } catch (error) {
        showToast('Failed to generate M3U content', 'error');
        console.error('M3U export error:', error);
        return;
    }

    // Browser download
    const blob = new Blob([m3uContent], { type: 'audio/x-mpegurl;charset=utf-8' });
    const url = URL.createObjectURL(blob);
    const link = document.createElement('a');
    link.href = url;
    link.download = `${playlistName.replace(/[/\\?%*:|"<>]/g, '-')}.m3u`;
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);
    URL.revokeObjectURL(url);

    showToast(`Exported M3U: ${foundCount} available, ${missingCount} missing`, 'success');
    console.log(`✅ Exported M3U - Total: ${process.tracks.length}, Available: ${foundCount}, Missing: ${missingCount}`);
}

function _extractM3UTracks(tracks) {
    /** Extract simplified track data for the /api/generate-playlist-m3u endpoint. */
    return tracks.map(t => {
        let artist = '';
        if (Array.isArray(t.artists)) {
            const first = t.artists[0];
            artist = typeof first === 'object' ? (first.name || '') : String(first || '');
        } else if (typeof t.artists === 'string') {
            artist = t.artists;
        } else if (t.artist) {
            artist = typeof t.artist === 'object' ? (t.artist.name || '') : String(t.artist);
        }
        return { name: t.name || '', artist, duration_ms: t.duration_ms || 0 };
    });
}

// ==================================================================================
