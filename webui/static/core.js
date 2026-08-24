// SoulSync WebUI JavaScript - Replicating PyQt6 GUI Functionality

const PAGE_WILL_CHANGE_EVENT = 'ss:webui-page-will-change';

// Global state management
let currentPage = 'dashboard';
let currentTrack = null;
let isPlaying = false;
let mediaPlayerExpanded = false;
let currentStream = {
    status: 'stopped',
    progress: 0,
    track: null
};
let currentMusicSourceName = 'Spotify'; // 'Spotify', 'iTunes', or 'Deezer' - updated from status endpoint

// Streaming state management (enhanced functionality)
let streamStatusPoller = null;
let audioPlayer = null;
let streamPollingRetries = 0;
let streamPollingInterval = 1000; // Start with 1-second polling
const maxStreamPollingRetries = 10;
let dbStatsInterval = null;
let dbUpdateStatusInterval = null;
let duplicateCleanerStatusInterval = null;
let wishlistCountInterval = null;
let wishlistCountdownInterval = null;  // Countdown timer for wishlist overview modal

// Page state for Watchlist & Wishlist sidebar pages
let wishlistPageState = { isInitialized: false };

// --- Add these globals for the Sync Page ---
let spotifyPlaylists = [];
let selectedPlaylists = new Set();
let activeSyncPollers = {}; // Key: playlist_id, Value: intervalId

/**
 * Whether the download engine still holds a sync poller for this playlist.
 *
 * A SEAM for the React sync page. `activeSyncPollers` is a top-level `let`,
 * which — unlike a `function` declaration — creates NO property on `window`,
 * so React cannot read it however it is spelled. The sequential-sync runner
 * needs exactly one bit from it: is this playlist still syncing. It watches
 * the poller's disappearance rather than any completion event, because
 * `stopSyncPolling` is the single place every terminal path funnels through.
 *
 * A function rather than an alias of the object: the binding stays private,
 * and the closure reads it live even if it were ever reassigned.
 */
window.isPlaylistSyncing = function isPlaylistSyncing(playlistId) {
    return Boolean(activeSyncPollers[playlistId]);
};
// Phase 5: WebSocket sync/discovery/scan state
let _syncProgressCallbacks = {};
let _discoveryProgressCallbacks = {};
let _lastMediaScanStatus = null;
let _lastWishlistStats = null;
let playlistTrackCache = {}; // Key: playlist_id, Value: tracks array
let playlistTrackSnapshotCache = {}; // Key: playlist_id, Value: upstream snapshot_id at cache time
let spotifyPlaylistsLoaded = false;
let activeDownloadProcesses = {};
// Relocated discover.js state the surviving flows still share: the LB caches
// (sync-listenbrainz reads/fills them; the relocated discovery modal reads
// them) and the init.js one-shot flag. Declared with `let` exactly as the
// vanilla page declared them, so sync-listenbrainz's window-fallback branch
// stays dormant and every bare read resolves to the same binding.
let listenbrainzPlaylistsCache = {};
let listenbrainzTracksCache = {};
let discoverPageInitialized = false;
/**
 * Show the modal of an already-active download process, for the React pages.
 *
 * `activeDownloadProcesses` is a top-level `let`, so it lives in the script's
 * lexical scope and is NOT a window property — a module cannot reach it. The
 * download modal reopens an existing process by itself
 * (openDownloadMissingModalForArtistAlbum, shared-helpers.js:1767), but the
 * search page checks FIRST so it can skip fetching album detail it does not
 * need. That is not just an optimisation: when the re-click happens while the
 * metadata source is down, the fetch fails and the user gets an error toast
 * instead of the modal they already had open.
 *
 * Returns whether a modal was shown, so the caller knows to stop.
 */
/**
 * Bridge for the React discover page: seed a VIRTUAL playlist and its tracks,
 * then hand off to the shared sync engine. `playlistTrackCache` and
 * `spotifyPlaylists` are top-level `let`s in this script's lexical scope, so a
 * module cannot seed them itself — the same reason the function below exists.
 * Mirrors what startDecadeSync/startDiscoverPlaylistSync did inline (discover.js
 * 2753-2764) before the page moved to React.
 */
window.startDiscoverVirtualSync = function (virtualPlaylistId, name, spotifyTracks) {
    playlistTrackCache[virtualPlaylistId] = spotifyTracks;
    if (!spotifyPlaylists.find(p => p.id === virtualPlaylistId)) {
        spotifyPlaylists.push({ id: virtualPlaylistId, name, track_count: spotifyTracks.length });
    }
    return startPlaylistSync(virtualPlaylistId);
};

/**
 * Bridge for the React discover download bar: expose one download's process
 * record (status + modal handles). `activeDownloadProcesses` is a top-level
 * `let` in this script's lexical scope — same story as everything else here.
 */
window.discoverDownloadProcess = function (virtualPlaylistId) {
    return activeDownloadProcesses[virtualPlaylistId] || null;
};

/**
 * Bridge for the React SYNC page's account tabs (Spotify + Deezer ARL): put a
 * playlist row into `spotifyPlaylists` so `openDownloadMissingModal` can find
 * it. Same lexical-scope story as startDiscoverVirtualSync above — and the same
 * shape, minus the sync kickoff, because this one only seeds.
 *
 * Without it openDownloadMissingModal bails at its `if (!playlist)` guard with
 * 'Could not find playlist data.' (sync-spotify.js 2235-2240). The vanilla page
 * never needed a bridge: loadSpotifyPlaylists assigns the whole array (1612)
 * and the ARL flow pushes its own shim rows (sync-services.js 2471, 2646-2654).
 * Once React owns those tabs, neither of those runs.
 *
 * Idempotent by id, exactly like the push it replaces.
 */
window.registerSyncAccountPlaylist = function (row) {
    if (!row || !row.id) return;
    if (!spotifyPlaylists.find(p => p.id === row.id)) {
        spotifyPlaylists.push(row);
    }
};

/**
 * The registered account playlists, in registration order.
 *
 * The SECOND half of the seam above, and the React sync page needs both:
 * `startSequentialSync` has to know what order to queue the selection in, and
 * the sidebar has to resolve a playlist id to a name for "Syncing 2/5: Beta".
 * `spotifyPlaylists` is a top-level `let`, so — like `activeSyncPollers` — it
 * is NOT a window property and React cannot read it directly.
 *
 * Reading the ENGINE's array rather than the tab's React state is deliberate.
 * `startPlaylistSync` resolves every id against this same array and bails with
 * 'Could not find playlist data.' for anything missing, so a queue built from
 * it can only contain ids the engine can actually run. The tab renders its
 * rows in the order it registers them, so this order is also the display
 * order the vanilla read off the DOM.
 *
 * A COPY, so a caller cannot reorder or splice the engine's own array.
 */
window.getSyncAccountPlaylists = function () {
    return spotifyPlaylists.slice();
};


/**
 * Bridge for the React discover page: the ListenBrainz/Last.fm playlist
 * DISCOVERY download flow, moved VERBATIM from discover.js's
 * openDownloadModalForListenBrainzPlaylist (3934-4137) with one change — the
 * tracks arrive as a parameter instead of discover.js's script-scoped
 * listenbrainzTracksCache. Everything it touches lives in classic-script
 * scope this file shares: listenbrainzPlaylistStates (this file),
 * activeDownloadProcesses (this file), openDownloadMissingModalForYouTube
 * (downloads.js), openYouTubeDiscoveryModal + startListenBrainzDiscoveryPolling
 * + startModalDownloadPolling (sync-services.js) — all of which survive
 * discover.js's deletion, which is why the flow lives HERE and not in a module.
 */
window.openLbPlaylistDiscovery = async function (identifier, title, tracks) {
    try {
        if (!tracks || tracks.length === 0) {
            showToast('No tracks to download', 'error');
            return;
        }

        console.log(`🎵 Opening ListenBrainz discovery modal: ${title}`);
        console.log(`🔍 Looking for existing state with identifier: ${identifier}`);
        console.log(`📋 All ListenBrainz states:`, Object.keys(listenbrainzPlaylistStates));

        // Check if state already exists from backend hydration (like Beatport does)
        const existingState = listenbrainzPlaylistStates[identifier];
        console.log(`🔍 Existing state found:`, existingState ? `Phase: ${existingState.phase}` : 'None');

        if (existingState && existingState.phase !== 'fresh') {
            // State exists - rehydrate the modal with existing data
            console.log(`🔄 Rehydrating existing ListenBrainz state (Phase: ${existingState.phase})`);

            // If downloading/download_complete, rehydrate download modal instead
            if ((existingState.phase === 'downloading' || existingState.phase === 'download_complete') &&
                existingState.convertedSpotifyPlaylistId && existingState.download_process_id) {

                console.log(`📥 Rehydrating download modal for ListenBrainz playlist: ${title}`);

                // Implement download modal rehydration (like Beatport does)
                const convertedPlaylistId = existingState.convertedSpotifyPlaylistId;

                try {
                    // Check if modal already exists (user just closed it)
                    if (activeDownloadProcesses[convertedPlaylistId]) {
                        console.log(`✅ Download modal already exists, just showing it`);
                        const process = activeDownloadProcesses[convertedPlaylistId];
                        if (process.modalElement) {
                            process.modalElement.style.display = 'flex';
                        }
                        return;
                    }

                    // Create the download modal using the ListenBrainz state
                    console.log(`🆕 Creating new download modal for rehydration`);
                    // Get tracks from the existing state
                    let spotifyTracks = [];

                    if (existingState && existingState.discovery_results) {
                        spotifyTracks = existingState.discovery_results
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
                    }

                    if (spotifyTracks.length > 0) {
                        await openDownloadMissingModalForYouTube(
                            convertedPlaylistId,
                            title,
                            spotifyTracks
                        );

                        // Set the modal to running state with the correct batch ID
                        const process = activeDownloadProcesses[convertedPlaylistId];
                        if (process) {
                            process.status = existingState.phase === 'download_complete' ? 'complete' : 'running';
                            process.batchId = existingState.download_process_id;

                            // Update UI to running state
                            const beginBtn = document.getElementById(`begin-analysis-btn-${convertedPlaylistId}`);
                            const cancelBtn = document.getElementById(`cancel-all-btn-${convertedPlaylistId}`);
                            if (beginBtn) beginBtn.style.display = 'none';
                            if (cancelBtn) cancelBtn.style.display = 'inline-block';

                            // Start polling for this process
                            startModalDownloadPolling(convertedPlaylistId);

                            // Add to discover download sidebar if this has discoverMetadata
                            if (process.discoverMetadata) {
                                const playlistName = title;
                                const imageUrl = process.discoverMetadata.imageUrl;
                                const type = process.discoverMetadata.type || 'album';
                                addDiscoverDownload(convertedPlaylistId, playlistName, type, imageUrl);
                                console.log(`📥 [REHYDRATION] Added ListenBrainz download to sidebar: ${playlistName}`);
                            }

                            // Show modal since user clicked the download button (different from background rehydration)
                            if (process.modalElement) {
                                process.modalElement.style.display = 'flex';
                            }
                            console.log(`✅ Rehydrated download modal for ListenBrainz playlist: ${title}`);
                        }
                    } else {
                        console.warn(`⚠️ No Spotify tracks found for ListenBrainz download modal: ${title}`);
                    }
                } catch (error) {
                    console.warn(`⚠️ Error setting up download process for ListenBrainz playlist "${title}":`, error.message);
                }

                return;
            }

            // Open discovery modal with existing state
            openYouTubeDiscoveryModal(identifier);

            // If still discovering, resume polling
            if (existingState.phase === 'discovering') {
                console.log(`🔄 Resuming discovery polling for: ${title}`);
                startListenBrainzDiscoveryPolling(identifier);
            }

            return;
        }

        // No existing state - create fresh state and start discovery
        console.log(`🆕 Creating fresh ListenBrainz state for: ${title}`);

        // Create YouTube-style state entry for this ListenBrainz playlist (like Beatport does)
        const listenbrainzState = {
            phase: 'fresh',
            playlist: {
                name: title,
                tracks: tracks.map(track => ({
                    track_name: track.track_name,
                    artist_name: track.artist_name,
                    album_name: track.album_name,
                    duration_ms: track.duration_ms || 0,
                    mbid: track.mbid,
                    release_mbid: track.release_mbid,
                    album_cover_url: track.album_cover_url
                })),
                description: `${tracks.length} tracks from ${title}`,
                source: 'listenbrainz'
            },
            is_listenbrainz_playlist: true,
            playlist_mbid: identifier,  // Link to ListenBrainz playlist
            // Initialize discovery state properties (both naming conventions for modal compatibility)
            discovery_results: [],
            discoveryResults: [],
            discovery_progress: 0,
            discoveryProgress: 0,
            spotify_matches: 0,
            spotifyMatches: 0,
            spotify_total: tracks.length,
            spotifyTotal: tracks.length
        };

        // Store in ListenBrainz playlist states
        listenbrainzPlaylistStates[identifier] = listenbrainzState;

        // Start discovery automatically (like Beatport and Tidal do)
        try {
            console.log(`🔍 Starting ListenBrainz discovery for: ${title}`);

            // Call the discovery start endpoint with playlist data
            const response = await fetch(`/api/listenbrainz/discovery/start/${identifier}`, {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json'
                },
                body: JSON.stringify({
                    playlist: listenbrainzState.playlist
                })
            });

            const result = await response.json();
            if (result.success) {
                // Update state to discovering
                listenbrainzPlaylistStates[identifier].phase = 'discovering';

                // Start polling for progress
                startListenBrainzDiscoveryPolling(identifier);

                console.log(`✅ Started ListenBrainz discovery for: ${title}`);
            } else {
                console.error('❌ Error starting ListenBrainz discovery:', result.error);
                showToast(`Error starting discovery: ${result.error}`, 'error');
            }
        } catch (error) {
            console.error('❌ Error starting ListenBrainz discovery:', error);
            showToast(`Error starting discovery: ${error.message}`, 'error');
        }

        // Open the existing YouTube discovery modal infrastructure
        openYouTubeDiscoveryModal(identifier);

        console.log(`✅ ListenBrainz discovery modal opened for ${title} with ${tracks.length} tracks`);

    } catch (error) {
        console.error('Error opening discovery modal for ListenBrainz playlist:', error);
        showToast('Failed to open discovery modal', 'error');
    }
};

window.reopenActiveDownloadModal = function (virtualPlaylistId) {
    const process = activeDownloadProcesses[virtualPlaylistId];
    if (!process || !process.modalElement) return false;
    if (process.status === 'complete') {
        showToast('Showing previous results. Close this modal to start a new analysis.', 'info');
    }
    process.modalElement.style.display = 'flex';
    return true;
};

/**
 * Open the download modal for a batch shown on the Downloads page.
 *
 * Lives here for the same reason as the function above: `activeDownloadProcesses`
 * is a top-level `let` in this script's lexical scope, so a module cannot read
 * it. `rehydrateModal` and `WishlistModalState` are likewise script-scoped.
 * Moved verbatim from _adlOpenBatchModal (pages-extra.js) when the Downloads
 * page became React.
 *
 * Wishlist gets its own branch because its modal is a singleton keyed by the
 * literal 'wishlist' rather than by batch id, and it has visibility state of
 * its own that has to be told the modal is showing again.
 */
window.openDownloadBatchModal = function (batchId, playlistId, batchName) {
    if (playlistId === 'wishlist') {
        const clientProcess = activeDownloadProcesses['wishlist'];
        if (clientProcess && clientProcess.modalElement && document.body.contains(clientProcess.modalElement)) {
            clientProcess.modalElement.style.display = 'flex';
            if (typeof WishlistModalState !== 'undefined') WishlistModalState.setVisible();
        } else {
            rehydrateModal({ playlist_id: playlistId, playlist_name: batchName, batch_id: batchId }, true);
        }
        return;
    }

    // Any other batch: show the modal it already has, or rebuild from the server.
    for (const [, process] of Object.entries(activeDownloadProcesses)) {
        if (process.batchId === batchId && process.modalElement && document.body.contains(process.modalElement)) {
            process.modalElement.style.display = 'flex';
            return;
        }
    }
    rehydrateModal({ playlist_id: playlistId, playlist_name: batchName, batch_id: batchId }, true);
};

/**
 * Paint the downloads count on the nav button.
 *
 * Moved verbatim from pages-extra.js when the Downloads page became React. It
 * has to live in a classic script rather than in the React page, because the
 * websocket status handler below (and its twin in shared-helpers.js) calls it
 * on every push — including on pages where the Downloads route is not mounted.
 *
 * It reads NOTHING from the downloads page: the count is the real server-side
 * active total from the status push, not the page's list. That distinction is
 * why the vanilla deliberately never called this from its own poll — the poll
 * caps at 300 rows and would under-report a bigger queue.
 */
function _updateDlNavBadge(count) {
    const badge = document.getElementById('dl-nav-badge');
    if (badge) {
        if (count > 0) {
            badge.textContent = count;
            badge.classList.remove('hidden');
        } else {
            badge.classList.add('hidden');
        }
    }
    const dlBtn = document.querySelector('.nav-button[data-page="active-downloads"]');
    if (dlBtn) {
        dlBtn.classList.toggle('nav-downloads-active', count > 0);
    }
}

let sequentialSyncManager = null;

// --- YouTube Playlist State Management ---
let youtubePlaylistStates = {}; // Key: url_hash, Value: playlist state
let activeYouTubePollers = {}; // Key: url_hash, Value: intervalId

// --- Tidal Playlist State Management (Similar to YouTube but loads from API like Spotify) ---
let tidalPlaylists = [];
let tidalPlaylistStates = {}; // Key: playlist_id, Value: playlist state with phases
let tidalPlaylistsLoaded = false;
let deezerPlaylists = [];
let deezerPlaylistStates = {};
let deezerArlPlaylists = [];
let deezerArlPlaylistsLoaded = false;

// --- Qobuz Playlist State Management (mirrors Tidal — github issue #677) ---
let qobuzPlaylists = [];
let qobuzPlaylistStates = {}; // Key: playlist_id, Value: playlist state with phases
let qobuzPlaylistsLoaded = false;

// --- Beatport Chart State Management (Similar to YouTube/Tidal) ---
let beatportChartStates = {}; // Key: chart_hash, Value: chart state with phases
let beatportContentState = {
    loaded: false,
    loadingPromise: null,
    abortController: null
};

function getBeatportContentSignal() {
    return beatportContentState.abortController ? beatportContentState.abortController.signal : null;
}

function throwIfBeatportLoadAborted() {
    if (beatportContentState.abortController && beatportContentState.abortController.signal.aborted) {
        throw new DOMException('Beatport load aborted', 'AbortError');
    }
}

function stopBeatportDiscoveryAndSyncPolling() {
    Object.entries(activeYouTubePollers).forEach(([identifier, poller]) => {
        const isBeatportChart = !!youtubePlaylistStates[identifier]?.is_beatport_playlist ||
            !!beatportChartStates[identifier];
        if (isBeatportChart) {
            clearInterval(poller);
            delete activeYouTubePollers[identifier];
        }
    });

    Object.entries(_discoveryProgressCallbacks).forEach(([identifier]) => {
        const isBeatportChart = !!youtubePlaylistStates[identifier]?.is_beatport_playlist ||
            !!beatportChartStates[identifier];
        if (isBeatportChart) {
            if (socketConnected) socket.emit('discovery:unsubscribe', { ids: [identifier] });
            delete _discoveryProgressCallbacks[identifier];
        }
    });

    Object.entries(_syncProgressCallbacks).forEach(([syncPlaylistId]) => {
        const beatportState = Object.values(youtubePlaylistStates).find(state =>
            state && state.is_beatport_playlist && state.syncPlaylistId === syncPlaylistId
        );
        if (beatportState) {
            if (socketConnected) socket.emit('sync:unsubscribe', { playlist_ids: [syncPlaylistId] });
            delete _syncProgressCallbacks[syncPlaylistId];
        }
    });
}

function resetBeatportSliderInitFlags() {
    // The four `dataset.initialized = 'false'` writes that stood here are gone.
    // They targeted #beatport-{rebuild,releases,charts,dj}-slider, ids the sync
    // flip deleted along with the page — every lookup returned null, so every
    // write was already a no-op. Removing them is behaviour-neutral and stops
    // four dead `getElementById` calls tripping the class-as-id guard.
    //
    // The state resets below are KEPT: they are plain object writes, not DOM,
    // and the slider state objects still exist in this file.
    beatportReleasesSliderState.isInitialized = false;
    beatportHypePicksSliderState.isInitialized = false;
    beatportChartsSliderState.isInitialized = false;
    beatportDJSliderState.isInitialized = false;
}

function cleanupBeatportContent() {
    const wasLoaded = beatportContentState.loaded || !!beatportContentState.loadingPromise;
    if (!wasLoaded) return;

    console.log('🧹 Cleaning up Beatport content...');

    if (beatportContentState.abortController) {
        beatportContentState.abortController.abort();
        beatportContentState.abortController = null;
    }

    stopBeatportDiscoveryAndSyncPolling();
    cleanupBeatportRebuildSlider();
    cleanupBeatportReleasesSlider();
    cleanupBeatportHypePicksSlider();
    cleanupBeatportChartsSlider();
    cleanupBeatportDJSlider();
    resetBeatportSliderInitFlags();

    beatportContentState.loadingPromise = null;
    beatportContentState.loaded = false;

    console.log('✅ Beatport content cleaned up');
}

// --- ListenBrainz Playlist State Management (Similar to YouTube/Tidal/Beatport) ---
let listenbrainzPlaylistStates = {}; // Key: playlist_mbid, Value: playlist state with phases
let listenbrainzPlaylistsLoaded = false;  // Track if playlists have been loaded from backend

// --- Artists Page State Management ---
let artistsPageState = {
    currentView: 'search', // 'search', 'results', 'detail'
    searchQuery: '',
    searchResults: [],
    selectedArtist: null,
    sourceOverride: null, // Set when navigating from an alternate search tab
    artistDiscography: {
        albums: [],
        singles: []
    },
    cache: {
        searches: {}, // Cache search results by query
        discography: {}, // Cache discography by artist ID
        colors: {}, // Cache extracted colors by image URL
        completionData: {} // Cache completion data by artist ID
    },
    isInitialized: false // Track if the page has been initialized
};

// --- Artist Downloads Management State ---
let artistDownloadBubbles = {}; // Track artist download bubbles: artistId -> { artist, downloads: [], element }
let artistDownloadModalOpen = false; // Track if artist download modal is open
let downloadsUpdateTimeout = null; // Debounce downloads section updates

// --- Search Downloads Management State ---
let searchDownloadBubbles = {}; // Track search download bubbles: artistName -> { artist, downloads: [] }
let searchDownloadModalOpen = false; // Track if search download modal is open

// --- Beatport Downloads Management State ---
let beatportDownloadBubbles = {}; // Track Beatport download bubbles: chartKey -> { chart: { name, image }, downloads: [] }
let beatportDownloadsUpdateTimeout = null; // Debounce Beatport downloads section updates

let artistsSearchTimeout = null;
let artistsSearchController = null;
let artistCompletionController = null; // Track ongoing completion check to cancel when navigating away
let similarArtistsController = null; // Track ongoing similar artists stream to cancel when navigating away

function cancelSimilarArtistsLoad() {
    if (similarArtistsController) {
        similarArtistsController.abort();
        similarArtistsController = null;
    }
}

// --- Lazy Background Image Observer ---
// Watches elements with data-bg-src, applies background-image when visible, unobserves after.
const lazyBgObserver = new IntersectionObserver((entries) => {
    entries.forEach(entry => {
        if (entry.isIntersecting) {
            const el = entry.target;
            const src = el.dataset.bgSrc;
            if (src) {
                el.style.backgroundImage = `url('${src}')`;
                delete el.dataset.bgSrc;
            }
            lazyBgObserver.unobserve(el);
        }
    });
}, { rootMargin: '200px' });

/**
 * Observe all elements with data-bg-src within a container for lazy background loading.
 */
function observeLazyBackgrounds(container) {
    if (!container) return;
    const elements = container.querySelectorAll('[data-bg-src]');
    elements.forEach(el => lazyBgObserver.observe(el));
}

// ===============================
// CONFIRM DIALOG (themed replacement for native confirm())
// ===============================
let _confirmResolver = null;

function showConfirmDialog({ title = 'Confirm', message = '', confirmText = 'Confirm', cancelText = 'Cancel', destructive = false } = {}) {
    // Resolve any pending dialog as cancelled before opening a new one
    if (_confirmResolver) {
        _confirmResolver(false);
        _confirmResolver = null;
    }

    const overlay = document.getElementById('confirm-modal-overlay');
    const titleEl = document.getElementById('confirm-modal-title');
    const messageEl = document.getElementById('confirm-modal-message');
    const confirmBtn = document.getElementById('confirm-modal-confirm');
    const cancelBtn = document.getElementById('confirm-modal-cancel');

    titleEl.textContent = title;
    messageEl.textContent = message;
    confirmBtn.textContent = confirmText;
    cancelBtn.textContent = cancelText;

    // Toggle destructive (red) vs primary (accent) confirm button
    confirmBtn.className = destructive
        ? 'modal-button modal-button--cancel'
        : 'modal-button modal-button--primary';

    overlay.classList.remove('hidden');

    return new Promise(resolve => {
        _confirmResolver = resolve;
    });
}

function resolveConfirmDialog(result) {
    const overlay = document.getElementById('confirm-modal-overlay');
    overlay.classList.add('hidden');
    if (_confirmResolver) {
        _confirmResolver(result);
        _confirmResolver = null;
    }
}

/**
 * Nuclear confirmation dialog for mass-destructive operations.
 * User must type an exact phrase to proceed.
 */
function showWitnessMeDialog(orphanCount) {
    return new Promise(resolve => {
        const overlay = document.createElement('div');
        overlay.className = 'confirm-modal-overlay';
        overlay.style.cssText = 'position:fixed;inset:0;background:rgba(0,0,0,0.7);z-index:10000;display:flex;align-items:center;justify-content:center;';

        overlay.innerHTML = `
            <div style="background:var(--bg-secondary, #1e1e2e);border:2px solid #e74c3c;border-radius:12px;padding:28px;max-width:480px;width:90%;color:var(--text-primary, #fff);font-family:inherit;">
                <h3 style="margin:0 0 8px;color:#e74c3c;font-size:1.2em;">Mass Deletion Warning</h3>
                <p style="margin:0 0 12px;font-size:0.95em;opacity:0.9;">
                    You are about to <strong>permanently delete ${orphanCount.toLocaleString()} files</strong> from your disk.
                </p>
                <p style="margin:0 0 12px;font-size:0.9em;opacity:0.75;">
                    This many orphans usually means a path mismatch between your database and filesystem
                    — not actual orphan files. A previous user lost their entire library this way.
                </p>
                <p style="margin:0 0 6px;font-size:0.9em;opacity:0.9;">
                    To confirm you understand the risk, type <strong style="color:#e74c3c;">witness me</strong> below:
                </p>
                <input type="text" id="witness-me-input" autocomplete="off" spellcheck="false"
                       placeholder="Type the phrase here..."
                       style="width:100%;padding:10px;border:1px solid #555;border-radius:6px;background:var(--bg-primary, #111);color:var(--text-primary, #fff);font-size:1em;margin:8px 0 16px;box-sizing:border-box;">
                <div style="display:flex;gap:10px;justify-content:flex-end;">
                    <button id="witness-cancel" style="padding:8px 20px;border:1px solid #555;border-radius:6px;background:transparent;color:var(--text-primary, #fff);cursor:pointer;font-size:0.9em;">
                        Cancel
                    </button>
                    <button id="witness-confirm" disabled
                            style="padding:8px 20px;border:none;border-radius:6px;background:#555;color:#888;cursor:not-allowed;font-size:0.9em;font-weight:600;transition:all 0.2s;">
                        Delete Files
                    </button>
                </div>
            </div>
        `;

        document.body.appendChild(overlay);

        const input = overlay.querySelector('#witness-me-input');
        const confirmBtn = overlay.querySelector('#witness-confirm');
        const cancelBtn = overlay.querySelector('#witness-cancel');

        input.addEventListener('input', () => {
            const match = input.value.trim().toLowerCase() === 'witness me';
            confirmBtn.disabled = !match;
            confirmBtn.style.background = match ? '#e74c3c' : '#555';
            confirmBtn.style.color = match ? '#fff' : '#888';
            confirmBtn.style.cursor = match ? 'pointer' : 'not-allowed';
        });

        confirmBtn.addEventListener('click', () => {
            document.body.removeChild(overlay);
            resolve(true);
        });

        cancelBtn.addEventListener('click', () => {
            document.body.removeChild(overlay);
            resolve(false);
        });

        overlay.addEventListener('click', (e) => {
            if (e.target === overlay) {
                document.body.removeChild(overlay);
                resolve(false);
            }
        });

        setTimeout(() => input.focus(), 100);
    });
}

const MASS_ORPHAN_THRESHOLD = 20;

function _isMassOrphanFix(jobId, count) {
    if (count <= MASS_ORPHAN_THRESHOLD) return false;
    // Only trigger if mass_orphan flag is actually set on visible findings
    // (flag is set by backend when >50% of files are orphans — likely path mismatch)
    if (jobId === 'orphan_file_detector' || !jobId) {
        const massCards = document.querySelectorAll('.repair-finding-card[data-mass-orphan="true"]');
        if (massCards.length > 0) return true;
    }
    return false;
}

// ===============================
// WEBSOCKET CONNECTION MANAGER
// ===============================
let socket = null;
let socketConnected = false;
// Mirrored onto window so the React dashboard's fallback pollers can apply the
// SAME socket gate the vanilla poller twins do (`socketConnected` is a
// script-scoped `let` no module can read). Kept in lockstep at every write.
window._socketConnected = false;

function initializeWebSocket() {
    if (typeof io === 'undefined') {
        console.warn('Socket.IO client not loaded — falling back to HTTP polling');
        return;
    }

    socket = io({
        // Polling-first (Socket.IO default) then upgrade — most compatible behind
        // reverse proxies that don't cleanly forward WebSocket upgrade headers
        // (common in self-hosted setups). websocket-first shaves connect time when
        // it works but silently breaks real-time updates where the proxy blocks WS.
        transports: ['polling', 'websocket'],
        reconnection: true,
        reconnectionAttempts: Infinity,
        reconnectionDelay: 1000,
        reconnectionDelayMax: 10000,
        timeout: 20000
    });

    socket.on('connect', () => {
        console.log('WebSocket connected');
        socketConnected = true;
        window._socketConnected = true;
        resubscribeDownloadBatches();
        // Re-subscribe to any active sync/discovery rooms after reconnect
        const activeSyncIds = Object.keys(_syncProgressCallbacks);
        if (activeSyncIds.length > 0) {
            socket.emit('sync:subscribe', { playlist_ids: activeSyncIds });
            console.log('🔄 Re-subscribed to sync rooms:', activeSyncIds);
        }
        const activeDiscoveryIds = Object.keys(_discoveryProgressCallbacks);
        if (activeDiscoveryIds.length > 0) {
            socket.emit('discovery:subscribe', { ids: activeDiscoveryIds });
            console.log('🔄 Re-subscribed to discovery rooms:', activeDiscoveryIds);
        }
        // Join profile room for scoped watchlist/wishlist count updates
        if (currentProfile) {
            socket.emit('profile:join', { profile_id: currentProfile.id });
        }
        // Re-subscribe the Server Activity drawer if it's live (survives reconnects)
        if (window.ServerActivity && window.ServerActivity._wantsLive && window.ServerActivity._wantsLive()) {
            socket.emit('activity:subscribe');
        }
    });

    socket.on('disconnect', (reason) => {
        console.warn('WebSocket disconnected:', reason);
        socketConnected = false;
        window._socketConnected = false;
    });

    socket.on('reconnect', (attemptNumber) => {
        console.log(`WebSocket reconnected after ${attemptNumber} attempts`);
        // Rejoin profile room for scoped WebSocket emits
        if (currentProfile) {
            socket.emit('profile:join', { profile_id: currentProfile.id });
        }
        // Phase 1: Full state refresh on reconnect
        fetchAndUpdateServiceStatus();
        updateWatchlistButtonCount();
        resubscribeDownloadBatches();
        // Phase 2: Refresh dashboard data if on dashboard page. The stats and
        // activity refreshers are gone with the flip (the React cards fall
        // back to their own polls when the socket drops, so a reconnect gap
        // self-heals); the db-stats push and wishlist count still flow through
        // their vanilla fetchers' dispatches.
        if (currentPage === 'dashboard') {
            fetchAndUpdateDbStats();
            updateWishlistCount();
        }
    });

    // Phase 1 event listeners
    socket.on('status:update', handleServiceStatusUpdate);
    socket.on('watchlist:count', handleWatchlistCountUpdate);
    socket.on('downloads:batch_update', handleDownloadBatchUpdate);

    // Soulseek chat push (badges + PM toasts live in chat.js; guard: the
    // module owns all chat state, core.js only routes the events)
    socket.on('chat:room_message', function (d) {
        if (window.ChatPage && ChatPage.onRoomMessages) ChatPage.onRoomMessages(d);
    });
    socket.on('chat:unread', function (d) {
        if (window.ChatPage && ChatPage.onUnread) ChatPage.onUnread(d);
    });
    socket.on('chat:room_protocol', function (d) {
        if (window.ChatPage && ChatPage.onRoomProtocol) ChatPage.onRoomProtocol(d);
    });

    // Phase 2 event listeners (dashboard pollers)
    socket.on('rate-monitor:update', _handleRateMonitorUpdate);
    socket.on('dashboard:stats', handleDashboardStats);
    socket.on('dashboard:activity', handleDashboardActivity);
    socket.on('dashboard:toast', handleDashboardToast);
    socket.on('dashboard:db_stats', handleDashboardDbStats);
    socket.on('dashboard:wishlist_count', handleDashboardWishlistCount);

    // Phase 3 event listeners (enrichment sidebar workers)
    socket.on('enrichment:musicbrainz', (data) => updateMusicBrainzStatusFromData(data));
    socket.on('enrichment:audiodb', (data) => updateAudioDBStatusFromData(data));
    socket.on('enrichment:discogs', (data) => updateDiscogsStatusFromData(data));
    socket.on('enrichment:deezer', (data) => updateDeezerStatusFromData(data));
    socket.on('enrichment:jiosaavn', (data) => updateJioSaavnStatusFromData(data));
    socket.on('enrichment:spotify-enrichment', (data) => updateSpotifyEnrichmentStatusFromData(data));
    socket.on('enrichment:itunes-enrichment', (data) => updateiTunesEnrichmentStatusFromData(data));
    socket.on('enrichment:lastfm-enrichment', (data) => updateLastFMEnrichmentStatusFromData(data));
    socket.on('enrichment:genius-enrichment', (data) => updateGeniusEnrichmentStatusFromData(data));
    socket.on('enrichment:tidal-enrichment', (data) => updateTidalEnrichmentStatusFromData(data));
    socket.on('enrichment:qobuz-enrichment', (data) => updateQobuzEnrichmentStatusFromData(data));
    socket.on('enrichment:amazon-enrichment', (data) => updateAmazonEnrichmentStatusFromData(data));
    socket.on('enrichment:bandcamp-enrichment', (data) => updateBandcampEnrichmentStatusFromData(data));
    socket.on('enrichment:similar_artists', (data) => updateSimilarArtistsEnrichmentStatusFromData(data));
    socket.on('enrichment:hydrabase', (data) => updateHydrabaseStatusFromData(data));
    // NB the ss:repair-status re-broadcast for the React tools page lives inside
    // updateRepairStatusFromData, not here — the HTTP poller calls it too.
    socket.on('enrichment:repair', (data) => updateRepairStatusFromData(data));
    socket.on('enrichment:soulid', (data) => updateSoulIDStatusFromData(data));
    socket.on('enrichment:listening-stats', () => { }); // Status only, no UI update needed
    socket.on('repair:progress', (data) => { qaSignal('tools'); updateRepairJobProgressFromData(data); if (typeof updateMusicRepairTask === 'function') updateMusicRepairTask(data); });
    // Server Activity live push — feed the open drawer (Tautulli replacement)
    socket.on('activity:update', (data) => {
        if (window.ServerActivity && window.ServerActivity._onSocket) window.ServerActivity._onSocket(data);
    });
    // Bridge so server-activity.js can join/leave the live room without owning the
    // socket. isConnected() lets it fall back to HTTP polling when there's no WS.
    window.SoulSyncActivitySocket = {
        subscribe: () => { if (socket && socketConnected) socket.emit('activity:subscribe'); },
        unsubscribe: () => { if (socket && socketConnected) socket.emit('activity:unsubscribe'); },
        isConnected: () => !!socketConnected
    };

    // Forward enrichment status to the dashboard worker-orbs so the hub fires
    // a pulse on each real item matched / error (additional listener — does not
    // disturb the UI handlers above).
    ['musicbrainz', 'audiodb', 'discogs', 'deezer', 'jiosaavn', 'spotify-enrichment',
     'itunes-enrichment', 'lastfm-enrichment', 'genius-enrichment', 'tidal-enrichment',
     'qobuz-enrichment', 'amazon-enrichment', 'bandcamp-enrichment', 'similar_artists', 'hydrabase',
     'soulid', 'repair'].forEach((ch) => {
        socket.on('enrichment:' + ch, (data) => {
            if (window.workerOrbs && window.workerOrbs.onStatus) window.workerOrbs.onStatus(ch, data);
        });
    });

    // Phase 4 event listeners (tool progress)
    // 'tool:stream' is intentionally NOT wired: stream state is per-listener
    // (session cookie), so the global broadcast could only carry the DEFAULT
    // session's eternal "stopped" — the player polls /api/stream/status instead.
    socket.on('tool:duplicate-cleaner', (data) => { if (_qaToolBusy(data)) qaSignal('tools'); updateDuplicateCleanProgressFromData(data); });
    socket.on('tool:db-update', (data) => { if (_qaToolBusy(data)) qaSignal('tools'); updateDbProgressFromData(data); });
    socket.on('tool:metadata', (data) => { if (_qaToolBusy(data)) qaSignal('tools'); updateMetadataStatusFromData(data); });
    socket.on('tool:logs', (data) => updateLogsFromData(data));

    // Phase 5 event listeners (sync/discovery progress + scans)
    socket.on('sync:progress', (data) => {
        qaSignal('sync');
        updateSyncProgressFromData(data);
        window.dispatchEvent(new CustomEvent('ss:sync-progress', { detail: data }));
        if (typeof updateMusicSyncTask === 'function') updateMusicSyncTask(data);
    });
    socket.on('discovery:progress', (data) => {
        qaSignal('sync');
        updateDiscoveryProgressFromData(data);
        // Mirror to the React playlist-explorer page. Same `ss:` seam as
        // ss:watchlist-scan and ss:automation-progress: `socket` is a
        // module-scoped `let` in this file, so no module can subscribe to it,
        // and `youtubePlaylistStates` — which the explorer's poller used to
        // read for the finished phase — is module-scoped here too. The event
        // carries the phase, so React needs no second bridge for it.
        // Purely additive; the vanilla handling above is untouched.
        window.dispatchEvent(new CustomEvent('ss:discovery-progress', { detail: data }));
    });
    // Loading a big Deezer playlist resolves ~900 albums over rate-limited
    // requests and legitimately takes minutes. Re-broadcast so the sync page's
    // React card can say WHICH album it is on instead of showing a bare
    // spinner — same ss: seam as the frames above, `socket` being module-scoped
    // here and unreachable from a route.
    socket.on('deezer:playlist_progress', (data) => {
        window.dispatchEvent(new CustomEvent('ss:deezer-playlist-progress', { detail: data }));
    });
    // Unscoped heartbeat for the Auto-Sync tile: sync:progress above is
    // room-scoped (only playlist watchers receive it), so the dashboard
    // relies on this 1s pulse that fires while ANY pipeline work runs —
    // manual syncs, UI pipelines, and the scheduled auto-sync automation.
    socket.on('sync:active', (data) => {
        qaSignal('sync');
        window.dispatchEvent(new CustomEvent('ss:sync-progress', { detail: data }));
        if (typeof updateMusicSyncTask === 'function') updateMusicSyncTask(data);
    });
    socket.on('scan:watchlist', (data) => {
        const watchlistBtn = document.querySelector('.nav-button[data-page="watchlist"]');
        if (watchlistBtn) {
            watchlistBtn.classList.toggle('nav-watchlist-scanning', data.status === 'scanning');
        }
        // Re-broadcast to the React side. `socket` is a module-scoped `let` in
        // this file, so a React route cannot subscribe to it directly; this is
        // the same `ss:` window-event seam the shell bridge already uses.
        // Purely additive — the vanilla handlers above are untouched.
        window.dispatchEvent(new CustomEvent('ss:watchlist-scan', { detail: data }));
        if (typeof updateMusicWatchlistScanTask === 'function') updateMusicWatchlistScanTask(data);
    });
    socket.on('scan:media', (data) => { if (_qaToolBusy(data)) qaSignal('tools'); updateMediaScanFromData(data); if (typeof updateMusicMediaScanTask === 'function') updateMusicMediaScanTask(data); });
    socket.on('wishlist:stats', (data) => { updateWishlistStatsFromData(data); if (typeof updateMusicWishlistTask === 'function') updateMusicWishlistTask(data); });
    socket.on('lastfm:import-progress', (data) => {
        window.dispatchEvent(new CustomEvent('ss:lastfm-import-progress', { detail: data }));
        if (typeof updateLastfmListeningImportTask === 'function') updateLastfmListeningImportTask(data);
    });
    // Phase 6: Automation progress
    socket.on('automation:progress', (data) => {
        qaSignal('auto');
        updateAutomationProgressFromData(data);
        // Mirror to the React automations page. Same seam as ss:watchlist-scan:
        // the progress state is module-scoped in stats-automations.js and cannot
        // be read from a module, so the vanilla side announces and React reacts.
        window.dispatchEvent(new CustomEvent('ss:automation-progress', { detail: data }));
        if (typeof updateMusicAutomationTask === 'function') updateMusicAutomationTask(data);
    });
    socket.on('overlay:progress', (data) => { if (typeof updateOverlayTask === 'function') updateOverlayTask(data); });
    socket.on('collections:sync', (data) => { if (typeof updateCollectionSyncTask === 'function') updateCollectionSyncTask(data); });
    socket.on('collections:artwork', (data) => { if (typeof updateCollectionArtTask === 'function') updateCollectionArtTask(data); });
    socket.on('video:bulk', (data) => { if (typeof updateVideoBulkTask === 'function') updateVideoBulkTask(data); });
    socket.on('video:repair:progress', (data) => { if (typeof updateVideoRepairProgressFromData === 'function') updateVideoRepairProgressFromData(data); });
}

// ── Quick Actions tiles: animation == gauge ──
// Each tile's signature background animation SURGES while its subsystem is
// actually working: the sync EQ dances while a playlist pipeline runs, the
// gear spins up while a tool/scan/repair job runs, the automation flow pulses
// while an automation fires. Socket handlers ping a channel; tiles carry
// .is-live while the last ping is fresh. Idle keeps the original calm look.
const _qaLastSignal = { sync: 0, tools: 0, auto: 0 };

function qaSignal(channel) {
    _qaLastSignal[channel] = Date.now();
}

// Recognise "actually running" across the tool payload shapes
// ({status:'running'}, {status:{is_scanning:true}}, {running:true}, ...).
function _qaToolBusy(d) {
    if (!d) return false;
    const s = d.status;
    if (s && typeof s === 'object') {
        return !!(s.is_scanning || s.status === 'running' || s.status === 'scanning');
    }
    return s === 'running' || s === 'scanning' || d.is_scanning === true || d.running === true;
}

setInterval(() => {
    const now = Date.now();
    const map = { sync: '.qa-tile--sync', tools: '.qa-tile--tools', auto: '.qa-tile--auto' };
    for (const ch in map) {
        const tile = document.querySelector(map[ch]);
        if (tile) tile.classList.toggle('is-live', now - _qaLastSignal[ch] < 6000);
    }
}, 2000);

function handleServiceStatusUpdate(data) {
    // Re-broadcast for the React dashboard's service cards + library card
    // (tools-seam rule: in the handler, so any future HTTP caller counts too).
    window.dispatchEvent(new CustomEvent('ss:service-status', { detail: data }));
    // Cache for library status card
    _lastStatusPayload = data;

    if (typeof syncSpotifySettingsAuthState === 'function') {
        syncSpotifySettingsAuthState(data?.spotify || null);
    }
    if (typeof syncPrimaryMetadataSourceAvailability === 'function') {
        syncPrimaryMetadataSourceAvailability(data?.spotify || null);
    }
    if (typeof sanitizeMetadataSourceSelection === 'function') {
        sanitizeMetadataSourceSelection({ quiet: true });
    }

    // The dashboard service cards are React-rendered from the dispatch above
    // since the flip (service-cards.tsx) — only the sidebar half survives here.
    updateSidebarServiceStatus('metadata-source', data.metadata_source, data.spotify);
    updateSidebarServiceStatus('media-server', data.media_server);
    updateSidebarServiceStatus('soulseek', data.soulseek);

    // Update downloads nav badge from status push
    if (data.active_downloads !== undefined) _updateDlNavBadge(data.active_downloads);

    // Hide sync buttons (not the page) for standalone mode — playlists still browsable/downloadable
    const isSoulsyncStandalone = data.media_server?.type === 'soulsync';
    _isSoulsyncStandalone = isSoulsyncStandalone;
    document.querySelectorAll('.sync-to-server-btn, [id$="-sync-btn"], [onclick*="startPlaylistSync"], [onclick*="syncPlaylistToServer"], [onclick*="startDecadeSync"]').forEach(btn => {
        if (btn.id === 'stats-sync-btn') return; // React stats page owns this control now.
        if (btn.classList.contains('soulsync-standalone-action')) return;
        if (isSoulsyncStandalone) {
            btn.dataset.hiddenByStandalone = '1';
            btn.style.display = 'none';
        } else if (btn.dataset.hiddenByStandalone) {
            delete btn.dataset.hiddenByStandalone;
            btn.style.display = '';
        }
        // If not standalone and not previously hidden by standalone, leave display untouched
        // (preserves display:none on undiscovered LB/Last.fm playlist sync buttons)
    });

    // The enrichment chips grid is React-rendered from the dispatch above
    // since the dashboard flip (service-cards.tsx) — no vanilla write here.

    // Spotify rate limit / cooldown / recovery
    //
    // Only worth interrupting someone when the official API is actually
    // serving their metadata. On Deezer (or any other source) a ban changes
    // nothing they can see, so the modal announced that search and enrichment
    // were paused when neither was — pure noise.
    //
    // Gate on the SOURCE, not on `authenticated`: during a ban the client
    // deliberately reports authenticated=true (it means "you are connected,
    // just throttled"), so an auth check would never suppress anything.
    //
    // The ban itself is untouched — it still suppresses calls and still
    // protects against hammering. This only stops it interrupting people it
    // does not apply to.
    const _spotifyMattersHere = (data.metadata_source?.source || 'spotify') === 'spotify';
    if (data.spotify?.rate_limited && data.spotify.rate_limit && _spotifyMattersHere) {
        handleSpotifyRateLimit(data.spotify.rate_limit);
        _spotifyInCooldown = false;
    } else if (data.spotify?.rate_limited && !_spotifyMattersHere) {
        // Banned but irrelevant to this install — make sure a modal raised
        // before the account went away does not linger.
        if (_spotifyRateLimitShown) { _spotifyRateLimitShown = false; closeRateLimitModal(); }
    } else if (data.spotify?.post_ban_cooldown > 0) {
        if (_spotifyRateLimitShown && !_spotifyInCooldown) {
            _spotifyRateLimitShown = false;
            _spotifyInCooldown = true;
            closeRateLimitModal();
            showToast('Spotify ban expired \u2014 recovering shortly', 'info');
        }
    } else {
        if (_spotifyInCooldown) {
            _spotifyInCooldown = false;
            showToast('Spotify access restored', 'success');
            if (currentPage === 'discover') {
                if (typeof loadDiscoverPage === 'function') loadDiscoverPage();
            }
        } else if (_spotifyRateLimitShown) {
            handleSpotifyRateLimit(null);
        }
    }
}

function handleWatchlistCountUpdate(data) {
    // Re-broadcast for the React dashboard (tools-seam rule: in the handler).
    window.dispatchEvent(new CustomEvent('ss:watchlist-count', { detail: data }));
    if (data.success) {
        // Only the SIDEBAR half survives the dashboard flip — the hero button
        // and its badge/countdown-title are React-rendered from the dispatch
        // above (dashboard-header.tsx).
        const wlNavBadge = document.getElementById('watchlist-nav-badge');
        if (wlNavBadge) {
            wlNavBadge.textContent = data.count;
            wlNavBadge.classList.toggle('hidden', data.count === 0);
        }
    }
}

function handleDownloadBatchUpdate(payload) {
    const { batch_id, data } = payload;
    // Find which playlistId maps to this batch_id
    for (const [playlistId, process] of Object.entries(activeDownloadProcesses)) {
        if (process.batchId === batch_id) {
            processModalStatusUpdate(playlistId, data);
            break;
        }
    }
}

function resubscribeDownloadBatches() {
    if (!socket || !socketConnected) return;
    const activeBatchIds = [];
    Object.entries(activeDownloadProcesses).forEach(([playlistId, process]) => {
        if (process.batchId && (process.status === 'running' || process.status === 'complete')) {
            activeBatchIds.push(process.batchId);
        }
    });
    if (activeBatchIds.length > 0) {
        socket.emit('downloads:subscribe', { batch_ids: activeBatchIds });
        console.log(`WebSocket subscribed to ${activeBatchIds.length} download batches`);
    }
}

function subscribeToDownloadBatch(batchId) {
    if (socket && socketConnected && batchId) {
        socket.emit('downloads:subscribe', { batch_ids: [batchId] });
    }
}

function unsubscribeFromDownloadBatch(batchId) {
    if (socket && socketConnected && batchId) {
        socket.emit('downloads:unsubscribe', { batch_ids: [batchId] });
    }
}

// --- Phase 2: Dashboard event handlers ---

function handleDashboardStats(data) {
    // Dispatch-only since the dashboard flip — the stat cards are
    // React-rendered from this frame (system-stats.tsx).
    window.dispatchEvent(new CustomEvent('ss:dashboard-stats', { detail: data }));
}

function handleDashboardActivity(data) {
    // Dispatch-only since the dashboard flip — the feed is React-rendered
    // from this frame (activity-feed.tsx).
    window.dispatchEvent(new CustomEvent('ss:dashboard-activity', { detail: data }));
}

function handleDashboardToast(activity) {
    // Re-broadcast for the React dashboard (tools-seam rule: in the handler).
    window.dispatchEvent(new CustomEvent('ss:dashboard-toast', { detail: activity }));
    // Same logic as checkForActivityToasts response handler
    let toastType = 'info';
    if (activity.icon === '\u2705' || activity.title.includes('Complete')) {
        toastType = 'success';
    } else if (activity.icon === '\u274C' || activity.title.includes('Failed') || activity.title.includes('Error')) {
        toastType = 'error';
    } else if (activity.icon === '\uD83D\uDEAB' || activity.title.includes('Cancelled')) {
        toastType = 'warning';
    }
    showToast(`${activity.title}: ${activity.subtitle}`, toastType);
}

function handleDashboardDbStats(stats) {
    // Dispatch-only since the dashboard flip — the Library card is
    // React-rendered from this frame (library-card.tsx), and the tools page's
    // db-updater card (the old updateDbUpdaterCardInfo target) has been React
    // since the tools flip.
    window.dispatchEvent(new CustomEvent('ss:dashboard-db-stats', { detail: stats }));
}

function handleDashboardWishlistCount(data) {
    // Re-broadcast for the React dashboard (tools-seam rule: in the handler).
    window.dispatchEvent(new CustomEvent('ss:dashboard-wishlist-count', { detail: data }));
    const count = data.count || 0;
    // Only the SIDEBAR half survives the dashboard flip — the hero button,
    // its badge and the active/inactive classes are React-rendered from the
    // dispatch above (dashboard-header.tsx).
    const wlNavBadge = document.getElementById('wishlist-nav-badge');
    if (wlNavBadge) {
        wlNavBadge.textContent = count;
        wlNavBadge.classList.toggle('hidden', count === 0);
    }
    checkForAutoInitiatedWishlistProcess();
}

// ===============================
// END WEBSOCKET CONNECTION MANAGER
// ===============================

// --- Service Integration Logo Constants ---
const AUDIODB_LOGO_URL = '/static/img/brands/audiodb.png';
const MUSICBRAINZ_LOGO_URL = '/static/img/brands/musicbrainz.png';
const DEEZER_LOGO_URL = '/static/img/brands/deezer.png';
const SPOTIFY_LOGO_URL = '/static/img/brands/spotify.png';
const ITUNES_LOGO_URL = '/static/img/brands/itunes.png';
const LASTFM_LOGO_URL = '/static/img/brands/lastfm.png';
const GENIUS_LOGO_URL = '/static/img/brands/genius.png';
const TIDAL_LOGO_URL = '/static/img/brands/tidal.svg';
const QOBUZ_LOGO_URL = '/static/img/brands/qobuz.svg';
const DISCOGS_LOGO_URL = '/static/img/brands/discogs.svg';
const AMAZON_LOGO_URL = '/static/amazon.svg';
const BANDCAMP_LOGO_URL = '/static/img/brands/bandcamp.svg';
function getAudioDBLogoURL() {
    // The logo used to live ONLY as a 40KB base64 line inside the dashboard
    // markup, read off the DOM here. It is a real file now
    // (static/img/brands/audiodb.png, extracted from that exact line), so this
    // no longer depends on any page's markup being mounted — the DOM read is
    // kept first purely so an override of the img keeps winning.
    const el = document.querySelector('img.audiodb-logo');
    return el ? el.src : AUDIODB_LOGO_URL;
}

// --- Wishlist Modal Persistence State Management ---
const WishlistModalState = {
    // Track if wishlist modal was visible before page refresh
    setVisible: function () {
        localStorage.setItem('wishlist_modal_visible', 'true');
        console.log('📱 [Modal State] Wishlist modal marked as visible in localStorage');
    },

    setHidden: function () {
        localStorage.setItem('wishlist_modal_visible', 'false');
        console.log('📱 [Modal State] Wishlist modal marked as hidden in localStorage');
    },

    wasVisible: function () {
        const visible = localStorage.getItem('wishlist_modal_visible') === 'true';
        console.log(`📱 [Modal State] Checking if wishlist modal was visible: ${visible}`);
        return visible;
    },

    clear: function () {
        localStorage.removeItem('wishlist_modal_visible');
        console.log('📱 [Modal State] Cleared wishlist modal visibility state');
    },

    // Track if user manually closed the modal during auto-processing
    setUserClosed: function () {
        localStorage.setItem('wishlist_modal_user_closed', 'true');
        console.log('📱 [Modal State] User manually closed wishlist modal during auto-processing');
    },

    clearUserClosed: function () {
        localStorage.removeItem('wishlist_modal_user_closed');
        console.log('📱 [Modal State] Cleared user closed state');
    },

    wasUserClosed: function () {
        const closed = localStorage.getItem('wishlist_modal_user_closed') === 'true';
        console.log(`📱 [Modal State] Checking if user closed modal: ${closed}`);
        return closed;
    }
};

// Sequential Sync Manager Class
class SequentialSyncManager {
    constructor() {
        this.queue = [];
        this.currentIndex = 0;
        this.isRunning = false;
        this.startTime = null;
    }

    start(playlistIds) {
        if (this.isRunning) {
            console.warn('Sequential sync already running');
            return;
        }

        // Convert playlist IDs to ordered array (maintain display order)
        this.queue = Array.from(playlistIds);
        this.currentIndex = 0;
        this.isRunning = true;
        this.startTime = Date.now();

        console.log(`🚀 Starting sequential sync for ${this.queue.length} playlists:`, this.queue);
        this.updateUI();
        this.syncNext();
    }

    async syncNext() {
        // A cancel between iterations zeroes the queue AND clears isRunning,
        // but the `setTimeout(() => this.syncNext(), 1000)` queued by the
        // previous iteration still fires. Without this guard it then reads
        // `0 >= 0`, calls complete(), and announces a SUCCESS toast for zero
        // playlists — with a duration measured against a startTime cancel has
        // already set to null, so `Date.now() - null` renders the epoch in
        // seconds ("completed for 0 playlists in 1754584800.0s").
        if (!this.isRunning) return;

        if (this.currentIndex >= this.queue.length) {
            this.complete();
            return;
        }

        const playlistId = this.queue[this.currentIndex];
        const playlist = spotifyPlaylists.find(p => p.id === playlistId);
        console.log(`🔄 Sequential sync: Processing playlist ${this.currentIndex + 1}/${this.queue.length}: ${playlist?.name || playlistId}`);

        this.updateUI();

        try {
            // Use existing single sync function
            await startPlaylistSync(playlistId);

            // Wait for sync to complete by monitoring the poller
            await this.waitForSyncCompletion(playlistId);

        } catch (error) {
            console.error(`❌ Sequential sync: Failed to sync playlist ${playlistId}:`, error);
            showToast(`Failed to sync "${playlist?.name || playlistId}": ${error.message}`, 'error');
        }

        // Move to next playlist
        this.currentIndex++;
        setTimeout(() => this.syncNext(), 1000); // Small delay between syncs
    }

    async waitForSyncCompletion(playlistId) {
        return new Promise((resolve) => {
            // Monitor the existing sync poller for completion
            const checkCompletion = () => {
                if (!activeSyncPollers[playlistId]) {
                    // Poller stopped = sync completed
                    resolve();
                    return;
                }
                // Check again in 1 second
                setTimeout(checkCompletion, 1000);
            };
            checkCompletion();
        });
    }

    complete() {
        const duration = ((Date.now() - this.startTime) / 1000).toFixed(1);
        const completedCount = this.queue.length;
        console.log(`🏁 Sequential sync completed in ${duration}s`);

        this.isRunning = false;
        this.queue = [];
        this.currentIndex = 0;
        this.startTime = null;

        // Re-enable playlist selection
        disablePlaylistSelection(false);

        this.updateUI();
        updateRefreshButtonState(); // Refresh button state after completion
        showToast(`Sequential sync completed for ${completedCount} playlists in ${duration}s`, 'success');

        // Hide sidebar after completion
        hideSyncSidebar();
    }

    cancel() {
        if (!this.isRunning) return;

        console.log('🛑 Cancelling sequential sync');
        this.isRunning = false;
        this.queue = [];
        this.currentIndex = 0;
        this.startTime = null;

        // Re-enable playlist selection
        disablePlaylistSelection(false);

        this.updateUI();
        updateRefreshButtonState(); // Refresh button state after cancellation
        showToast('Sequential sync cancelled', 'info');

        // Hide sidebar after cancellation
        hideSyncSidebar();
    }

    updateUI() {
        const startSyncBtn = document.getElementById('start-sync-btn');
        const selectionInfo = document.getElementById('selection-info');

        if (!this.isRunning) {
            // Reset to normal state
            if (startSyncBtn) {
                startSyncBtn.textContent = 'Start Sync';
                startSyncBtn.disabled = selectedPlaylists.size === 0;
            }
            if (selectionInfo) {
                const count = selectedPlaylists.size;
                selectionInfo.textContent = count === 0
                    ? 'Select playlists to sync'
                    : `${count} playlist${count > 1 ? 's' : ''} selected`;
            }
        } else {
            // Show sequential sync status
            if (startSyncBtn) {
                startSyncBtn.textContent = 'Cancel Sequential Sync';
                startSyncBtn.disabled = false;
            }
            if (selectionInfo) {
                const current = this.currentIndex + 1;
                const total = this.queue.length;
                const currentPlaylist = spotifyPlaylists.find(p => p.id === this.queue[this.currentIndex]);
                selectionInfo.textContent = `Syncing ${current}/${total}: ${currentPlaylist?.name || 'Unknown'}`;
            }
        }
    }
}

// API endpoints
const API = {
    status: '/status',
    config: '/config',
    settings: '/api/settings',
    testConnection: '/api/test-connection',
    testDashboardConnection: '/api/test-dashboard-connection',
    playlists: '/api/playlists',
    sync: '/api/sync',
    search: '/api/search',
    artists: '/api/artists',
    activity: '/api/activity',
    stream: {
        start: '/api/stream/start',
        status: '/api/stream/status',
        toggle: '/api/stream/toggle',
        stop: '/api/stream/stop'
    }
};

// Track the last `/status` payload (shared service snapshot used across the UI)
let _lastStatusPayload = null;
let _isSoulsyncStandalone = false;  // Global flag: true when no media server (sync buttons hidden)

function getActiveMetadataSource() {
    return _lastStatusPayload?.metadata_source?.source || 'spotify';
}

// ===============================

// Relocated verbatim from discover.js: the dashboard discover bubble
// (wishlist-tools.js onclick) reopens a live download modal via these.
async function openDiscoverDownloadModal(playlistId) {
    console.log(`📂 [DOWNLOAD BAR] Opening download modal for: ${playlistId}`);

    // Check if there's an active download process with modal
    let process = activeDownloadProcesses[playlistId];

    console.log(`📋 [DOWNLOAD BAR] Process found:`, {
        exists: !!process,
        hasModalElement: !!(process && process.modalElement),
        hasModalId: !!(process && process.modalId)
    });

    if (process) {
        // Try modalElement first (album downloads)
        if (process.modalElement) {
            console.log(`✅ [DOWNLOAD BAR] Opening modal via modalElement`);
            process.modalElement.style.display = 'flex';
            return;
        }

        // Try modalId (sync downloads)
        if (process.modalId) {
            const modal = document.getElementById(process.modalId);
            if (modal) {
                console.log(`✅ [DOWNLOAD BAR] Opening modal via modalId: ${process.modalId}`);
                modal.style.display = 'flex';
                return;
            }
        }
    }

    // If no process found, try to rehydrate from backend
    console.log(`💧 [DOWNLOAD BAR] No modal found, attempting to rehydrate from backend...`);
    const rehydrated = await rehydrateDiscoverDownloadModal(playlistId);

    if (rehydrated) {
        console.log(`✅ [DOWNLOAD BAR] Successfully rehydrated modal, opening it...`);
        // Try again after rehydration
        process = activeDownloadProcesses[playlistId];
        if (process && process.modalElement) {
            process.modalElement.style.display = 'flex';
            return;
        }
    }

    // Fallback: show toast
    const download = discoverDownloads[playlistId];
    if (download) {
        console.log(`ℹ️ [DOWNLOAD BAR] No modal found after rehydration attempt, showing toast`);
        showToast(`Download: ${download.name} - ${download.status}`, 'info');
    } else {
        console.warn(`⚠️ [DOWNLOAD BAR] No download or process found for: ${playlistId}`);
    }
}

async function rehydrateDiscoverDownloadModal(playlistId) {
    /**
     * Rehydrates a discover download modal from backend process data.
     * Fetches tracks from backend API and recreates the modal (user-requested).
     */
    try {
        console.log(`💧 [REHYDRATE] Attempting to rehydrate modal for: ${playlistId}`);

        // Check if there's an active backend process for this playlist
        const batchResponse = await fetch(`/api/download_status/batch`);
        if (!batchResponse.ok) {
            console.log(`⚠️ [REHYDRATE] Failed to fetch batch info`);
            return false;
        }

        const batchData = await batchResponse.json();
        const batches = batchData.batches || {};

        // Find the batch for this playlist (batches is an object with batch_id keys)
        let batchId = null;
        let batch = null;
        for (const [id, batchStatus] of Object.entries(batches)) {
            if (batchStatus.playlist_id === playlistId) {
                batchId = id;
                batch = batchStatus;
                break;
            }
        }

        if (!batch || !batchId) {
            console.log(`⚠️ [REHYDRATE] No active batch found for ${playlistId}`);
            return false;
        }

        console.log(`✅ [REHYDRATE] Found active batch for ${playlistId}: ${batchId}`, batch);

        // Get the download metadata from discoverDownloads
        const downloadData = discoverDownloads[playlistId];
        if (!downloadData) {
            console.log(`⚠️ [REHYDRATE] No download metadata found for ${playlistId}`);
            return false;
        }

        // Handle album downloads from Recent Releases
        if (playlistId.startsWith('discover_album_')) {
            const albumId = playlistId.replace('discover_album_', '');
            console.log(`💧 [REHYDRATE] Album download - fetching album ${albumId}...`);

            try {
                const albumResponse = await fetch(`/api/spotify/album/${albumId}`);
                if (!albumResponse.ok) {
                    console.error(`❌ [REHYDRATE] Failed to fetch album: ${albumResponse.status}`);
                    return false;
                }

                const albumData = await albumResponse.json();
                if (!albumData.tracks || albumData.tracks.length === 0) {
                    console.error(`❌ [REHYDRATE] No tracks in album`);
                    return false;
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
                            name: albumData.name || downloadData.name.split(' - ')[0],
                            images: downloadData.imageUrl ? [{ url: downloadData.imageUrl }] : []
                        },
                        duration_ms: track.duration_ms || 0
                    };
                });

                console.log(`✅ [REHYDRATE] Retrieved ${spotifyTracks.length} tracks for album`);

                // Create modal
                await openDownloadMissingModalForYouTube(playlistId, downloadData.name, spotifyTracks);

                // Update process
                const process = activeDownloadProcesses[playlistId];
                if (process) {
                    process.status = 'running';
                    process.batchId = batchId;
                    subscribeToDownloadBatch(batchId);
                    const beginBtn = document.getElementById(`begin-analysis-btn-${playlistId}`);
                    const cancelBtn = document.getElementById(`cancel-all-btn-${playlistId}`);
                    if (beginBtn) beginBtn.style.display = 'none';
                    if (cancelBtn) cancelBtn.style.display = 'inline-block';

                    // Start polling for status updates
                    startModalDownloadPolling(playlistId);
                    console.log(`✅ [REHYDRATE] Successfully rehydrated album modal with polling`);
                    return true;
                }
                return false;

            } catch (error) {
                console.error(`❌ [REHYDRATE] Error fetching album:`, error);
                return false;
            }
        }

        // Determine API endpoint based on playlist ID
        let apiEndpoint;
        if (playlistId === 'discover_release_radar') {
            apiEndpoint = '/api/discover/release-radar';
        } else if (playlistId === 'discover_discovery_weekly') {
            apiEndpoint = '/api/discover/discovery-weekly';
        } else if (playlistId === 'discover_seasonal_playlist') {
            apiEndpoint = '/api/discover/seasonal-playlist';
        } else if (playlistId === 'discover_popular_picks') {
            apiEndpoint = '/api/discover/popular-picks';
        } else if (playlistId === 'discover_hidden_gems') {
            apiEndpoint = '/api/discover/hidden-gems';
        } else if (playlistId === 'discover_discovery_shuffle') {
            apiEndpoint = '/api/discover/discovery-shuffle';
        } else if (playlistId === 'build_playlist_custom') {
            apiEndpoint = '/api/discover/build-playlist';
        } else if (playlistId.startsWith('discover_lb_')) {
            // ListenBrainz playlist - fetch from cache
            const identifier = playlistId.replace('discover_lb_', '');
            const tracks = listenbrainzTracksCache[identifier];
            if (!tracks || tracks.length === 0) {
                console.log(`⚠️ [REHYDRATE] No ListenBrainz tracks in cache for ${identifier}`);
                return false;
            }

            // Convert to Spotify format
            const spotifyTracks = tracks.map(track => ({
                id: track.mbid || `listenbrainz_${track.track_name}_${track.artist_name}`.replace(/[^a-z0-9]/gi, '_'),  // Generate ID if missing
                name: track.track_name,
                artists: [{ name: cleanArtistName(track.artist_name) }], // Proper Spotify format
                album: {
                    name: track.album_name,
                    images: track.album_cover_url ? [{ url: track.album_cover_url }] : []
                },
                duration_ms: track.duration_ms || 0,
                mbid: track.mbid
            }));

            // Create modal and update process
            await openDownloadMissingModalForYouTube(playlistId, downloadData.name, spotifyTracks);
            const process = activeDownloadProcesses[playlistId];
            if (process) {
                process.status = 'running';
                process.batchId = batchId;
                subscribeToDownloadBatch(batchId);
                const beginBtn = document.getElementById(`begin-analysis-btn-${playlistId}`);
                const cancelBtn = document.getElementById(`cancel-all-btn-${playlistId}`);
                if (beginBtn) beginBtn.style.display = 'none';
                if (cancelBtn) cancelBtn.style.display = 'inline-block';

                // Start polling for status updates
                startModalDownloadPolling(playlistId);
                console.log(`✅ [REHYDRATE] Successfully rehydrated ListenBrainz modal with polling`);
                return true;
            }
            return false;
        } else if (playlistId.startsWith('listenbrainz_')) {
            // ListenBrainz download from discovery modal - get from backend state
            const mbid = playlistId.replace('listenbrainz_', '');
            console.log(`💧 [REHYDRATE] ListenBrainz download - fetching state for MBID: ${mbid}`);

            try {
                // Fetch ListenBrainz state from backend
                const stateResponse = await fetch(`/api/listenbrainz/state/${mbid}`);
                if (!stateResponse.ok) {
                    console.log(`⚠️ [REHYDRATE] Failed to fetch ListenBrainz state`);
                    return false;
                }

                const stateData = await stateResponse.json();
                if (!stateData || !stateData.discovery_results) {
                    console.log(`⚠️ [REHYDRATE] No discovery results in ListenBrainz state`);
                    return false;
                }

                // Convert discovery results to Spotify tracks
                const spotifyTracks = stateData.discovery_results
                    .filter(result => result.spotify_data)
                    .map(result => {
                        const track = result.spotify_data;
                        // Ensure artists is in proper Spotify format: [{name: ...}]
                        let artistsArray = [];
                        if (track.artists && Array.isArray(track.artists)) {
                            artistsArray = track.artists.map(artist => {
                                if (typeof artist === 'string') {
                                    return { name: artist };
                                } else if (artist && artist.name) {
                                    return { name: artist.name };
                                } else {
                                    return { name: String(artist || 'Unknown Artist') };
                                }
                            });
                        } else if (track.artists && typeof track.artists === 'string') {
                            artistsArray = [{ name: track.artists }];
                        } else {
                            artistsArray = [{ name: 'Unknown Artist' }];
                        }
                        return {
                            id: track.id,
                            name: track.name,
                            artists: artistsArray,
                            album: track.album || { name: 'Unknown Album', images: [] },
                            duration_ms: track.duration_ms || 0,
                            external_urls: track.external_urls || {}
                        };
                    });

                if (spotifyTracks.length === 0) {
                    console.log(`⚠️ [REHYDRATE] No Spotify tracks in ListenBrainz discovery results`);
                    return false;
                }

                console.log(`✅ [REHYDRATE] Retrieved ${spotifyTracks.length} tracks from ListenBrainz state`);

                // Create modal and update process
                await openDownloadMissingModalForYouTube(playlistId, downloadData.name, spotifyTracks);
                const process = activeDownloadProcesses[playlistId];
                if (process) {
                    process.status = 'running';
                    process.batchId = batchId;
                    subscribeToDownloadBatch(batchId);
                    const beginBtn = document.getElementById(`begin-analysis-btn-${playlistId}`);
                    const cancelBtn = document.getElementById(`cancel-all-btn-${playlistId}`);
                    if (beginBtn) beginBtn.style.display = 'none';
                    if (cancelBtn) cancelBtn.style.display = 'inline-block';

                    // Start polling for status updates
                    startModalDownloadPolling(playlistId);
                    console.log(`✅ [REHYDRATE] Successfully rehydrated ListenBrainz download modal with polling`);
                    return true;
                }
                return false;

            } catch (error) {
                console.error(`❌ [REHYDRATE] Error fetching ListenBrainz state:`, error);
                return false;
            }
        } else {
            console.error(`❌ [REHYDRATE] Unknown discover playlist type: ${playlistId}`);
            return false;
        }

        // Fetch tracks from API
        console.log(`📡 [REHYDRATE] Fetching tracks from ${apiEndpoint}...`);
        const response = await fetch(apiEndpoint);
        if (!response.ok) {
            console.error(`❌ [REHYDRATE] Failed to fetch tracks: ${response.status}`);
            return false;
        }

        const data = await response.json();
        if (!data.success || !data.tracks) {
            console.error(`❌ [REHYDRATE] Invalid track data:`, data);
            return false;
        }

        const tracks = data.tracks;
        console.log(`✅ [REHYDRATE] Retrieved ${tracks.length} tracks`);

        // Transform tracks to Spotify format
        const spotifyTracks = tracks.map(track => {
            let spotifyTrack;
            if (track.track_data_json) {
                spotifyTrack = track.track_data_json;
            } else {
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
            if (spotifyTrack.artists && Array.isArray(spotifyTrack.artists)) {
                spotifyTrack.artists = spotifyTrack.artists.map(a => a.name || a);
            }
            return spotifyTrack;
        });

        // Create the modal
        await openDownloadMissingModalForYouTube(playlistId, downloadData.name, spotifyTracks);

        // Update process with batch info
        const process = activeDownloadProcesses[playlistId];
        if (process) {
            process.status = 'running';
            process.batchId = batchId;
            subscribeToDownloadBatch(batchId);

            // Update button states
            const beginBtn = document.getElementById(`begin-analysis-btn-${playlistId}`);
            const cancelBtn = document.getElementById(`cancel-all-btn-${playlistId}`);
            if (beginBtn) beginBtn.style.display = 'none';
            if (cancelBtn) cancelBtn.style.display = 'inline-block';

            // Start polling for status updates
            startModalDownloadPolling(playlistId);

            // Don't hide the modal - user clicked to open it
            console.log(`✅ [REHYDRATE] Successfully rehydrated modal for ${downloadData.name} with polling`);
            return true;
        } else {
            console.error(`❌ [REHYDRATE] Failed to find rehydrated process for ${playlistId}`);
            return false;
        }

    } catch (error) {
        console.error(`❌ [REHYDRATE] Error rehydrating discover download modal:`, error);
        return false;
    }
}

// Relocated from discover.js: the LB sync modal's entry point. The body moved
// to window.openLbPlaylistDiscovery (parameterized by tracks) during the React
// port; this keeps the original name + cache sourcing for sync-listenbrainz.
async function openDownloadModalForListenBrainzPlaylist(identifier, title) {
    const tracks = listenbrainzTracksCache[identifier];
    if (!tracks || tracks.length === 0) {
        showToast('No tracks to download', 'error');
        return;
    }
    await window.openLbPlaylistDiscovery(identifier, title, tracks);
}
