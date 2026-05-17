// SoulSync WebUI JavaScript - Replicating PyQt6 GUI Functionality

const PAGE_WILL_CHANGE_EVENT = 'ss:webui-page-will-change';

// Global state management
let currentPage = 'dashboard';
let currentTrack = null;
let isPlaying = false;
let mediaPlayerExpanded = false;
let searchResults = [];
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
let allSearchResults = [];
let currentFilterType = 'all';
let currentFilterFormat = 'all';
let currentSortBy = 'quality_score';
let isSortReversed = false;
let searchAbortController = null;
let dbStatsInterval = null;
let dbUpdateStatusInterval = null;
let qualityScannerStatusInterval = null;
let duplicateCleanerStatusInterval = null;
let wishlistCountInterval = null;
let wishlistCountdownInterval = null;  // Countdown timer for wishlist overview modal
let watchlistCountdownInterval = null;  // Countdown timer for watchlist overview modal

// Page state for Watchlist & Wishlist sidebar pages
let watchlistPageState = { isInitialized: false, artists: [] };
let wishlistPageState = { isInitialized: false };

// --- Add these globals for the Sync Page ---
let spotifyPlaylists = [];
let selectedPlaylists = new Set();
let activeSyncPollers = {}; // Key: playlist_id, Value: intervalId
// Phase 5: WebSocket sync/discovery/scan state
let _syncProgressCallbacks = {};
let _discoveryProgressCallbacks = {};
let _lastWatchlistScanStatus = null;
let _lastMediaScanStatus = null;
let _lastWishlistStats = null;
let playlistTrackCache = {}; // Key: playlist_id, Value: tracks array
let spotifyPlaylistsLoaded = false;
let activeDownloadProcesses = {};
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
    const rebuildSlider = document.getElementById('beatport-rebuild-slider');
    if (rebuildSlider) rebuildSlider.dataset.initialized = 'false';

    const releasesSlider = document.getElementById('beatport-releases-slider');
    if (releasesSlider) releasesSlider.dataset.initialized = 'false';
    beatportReleasesSliderState.isInitialized = false;

    beatportHypePicksSliderState.isInitialized = false;

    const chartsSlider = document.getElementById('beatport-charts-slider');
    if (chartsSlider) chartsSlider.dataset.initialized = 'false';
    beatportChartsSliderState.isInitialized = false;

    const djSlider = document.getElementById('beatport-dj-slider');
    if (djSlider) djSlider.dataset.initialized = 'false';
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

function initializeWebSocket() {
    if (typeof io === 'undefined') {
        console.warn('Socket.IO client not loaded — falling back to HTTP polling');
        return;
    }

    socket = io({
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
    });

    socket.on('disconnect', (reason) => {
        console.warn('WebSocket disconnected:', reason);
        socketConnected = false;
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
        // Phase 2: Refresh dashboard data if on dashboard page
        if (currentPage === 'dashboard') {
            fetchAndUpdateSystemStats();
            fetchAndUpdateActivityFeed();
            fetchAndUpdateDbStats();
            updateWishlistCount();
        }
    });

    // Phase 1 event listeners
    socket.on('status:update', handleServiceStatusUpdate);
    socket.on('watchlist:count', handleWatchlistCountUpdate);
    socket.on('downloads:batch_update', handleDownloadBatchUpdate);

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
    socket.on('enrichment:spotify-enrichment', (data) => updateSpotifyEnrichmentStatusFromData(data));
    socket.on('enrichment:itunes-enrichment', (data) => updateiTunesEnrichmentStatusFromData(data));
    socket.on('enrichment:lastfm-enrichment', (data) => updateLastFMEnrichmentStatusFromData(data));
    socket.on('enrichment:genius-enrichment', (data) => updateGeniusEnrichmentStatusFromData(data));
    socket.on('enrichment:tidal-enrichment', (data) => updateTidalEnrichmentStatusFromData(data));
    socket.on('enrichment:qobuz-enrichment', (data) => updateQobuzEnrichmentStatusFromData(data));
    socket.on('enrichment:amazon-enrichment', (data) => updateAmazonEnrichmentStatusFromData(data));
    socket.on('enrichment:hydrabase', (data) => updateHydrabaseStatusFromData(data));
    socket.on('enrichment:repair', (data) => updateRepairStatusFromData(data));
    socket.on('enrichment:soulid', (data) => updateSoulIDStatusFromData(data));
    socket.on('enrichment:listening-stats', () => { }); // Status only, no UI update needed
    socket.on('repair:progress', (data) => updateRepairJobProgressFromData(data));

    // Phase 4 event listeners (tool progress)
    socket.on('tool:stream', (data) => updateStreamStatusFromData(data));
    socket.on('tool:quality-scanner', (data) => updateQualityScanProgressFromData(data));
    socket.on('tool:duplicate-cleaner', (data) => updateDuplicateCleanProgressFromData(data));
    socket.on('tool:retag', (data) => updateRetagStatusFromData(data));
    socket.on('tool:db-update', (data) => updateDbProgressFromData(data));
    socket.on('tool:metadata', (data) => updateMetadataStatusFromData(data));
    socket.on('tool:logs', (data) => updateLogsFromData(data));

    // Phase 5 event listeners (sync/discovery progress + scans)
    socket.on('sync:progress', (data) => updateSyncProgressFromData(data));
    socket.on('discovery:progress', (data) => updateDiscoveryProgressFromData(data));
    socket.on('scan:watchlist', (data) => updateWatchlistScanFromData(data));
    socket.on('scan:media', (data) => updateMediaScanFromData(data));
    socket.on('wishlist:stats', (data) => updateWishlistStatsFromData(data));
    // Phase 6: Automation progress
    socket.on('automation:progress', (data) => updateAutomationProgressFromData(data));
}

function handleServiceStatusUpdate(data) {
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

    // Same logic as fetchAndUpdateServiceStatus response handler
    updateServiceStatus('metadata-source', data.metadata_source, data.spotify);
    updateServiceStatus('media-server', data.media_server);
    updateServiceStatus('soulseek', data.soulseek);

    updateSidebarServiceStatus('metadata-source', data.metadata_source, data.spotify);
    updateSidebarServiceStatus('media-server', data.media_server);
    updateSidebarServiceStatus('soulseek', data.soulseek);

    // Update downloads nav badge from status push
    if (data.active_downloads !== undefined) _updateDlNavBadge(data.active_downloads);

    // Hide sync buttons (not the page) for standalone mode — playlists still browsable/downloadable
    const isSoulsyncStandalone = data.media_server?.type === 'soulsync';
    _isSoulsyncStandalone = isSoulsyncStandalone;
    document.querySelectorAll('.sync-to-server-btn, [id$="-sync-btn"], [onclick*="startPlaylistSync"], [onclick*="syncPlaylistToServer"], [onclick*="startDecadeSync"]').forEach(btn => {
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

    // Update enrichment service cards
    if (data.enrichment) renderEnrichmentCards(data.enrichment);

    // Spotify rate limit / cooldown / recovery
    if (data.spotify?.rate_limited && data.spotify.rate_limit) {
        handleSpotifyRateLimit(data.spotify.rate_limit);
        _spotifyInCooldown = false;
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
                loadDiscoverPage();
            }
        } else if (_spotifyRateLimitShown) {
            handleSpotifyRateLimit(null);
        }
    }
}

function _updateHeroBtnCount(buttonId, badgeId, count) {
    const badge = document.getElementById(badgeId);
    if (badge) {
        badge.textContent = count;
        badge.classList.toggle('has-items', count > 0);
    }
}

function handleWatchlistCountUpdate(data) {
    if (data.success) {
        _updateHeroBtnCount('watchlist-button', 'watchlist-badge', data.count);
        // Update sidebar nav badge
        const wlNavBadge = document.getElementById('watchlist-nav-badge');
        if (wlNavBadge) {
            wlNavBadge.textContent = data.count;
            wlNavBadge.classList.toggle('hidden', data.count === 0);
        }
        const watchlistButton = document.getElementById('watchlist-button');
        if (watchlistButton) {
            const countdownText = data.next_run_in_seconds ? formatCountdownTime(data.next_run_in_seconds) : '';
            if (countdownText) {
                watchlistButton.title = `Next auto-scan in ${countdownText}`;
            }
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
    // Same logic as fetchAndUpdateSystemStats response handler
    updateStatCard('active-downloads-card', data.active_downloads, 'Currently downloading');
    updateStatCard('finished-downloads-card', data.finished_downloads, 'Completed this session');
    updateStatCard('download-speed-card', data.download_speed, 'Combined speed');
    updateStatCard('active-syncs-card', data.active_syncs, 'Playlists syncing');
    updateStatCard('uptime-card', data.uptime, 'Application runtime');
    updateStatCard('memory-card', data.memory_usage, 'Current usage');
}

function handleDashboardActivity(data) {
    // Same logic as fetchAndUpdateActivityFeed response handler
    updateActivityFeed(data.activities || []);
}

function handleDashboardToast(activity) {
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
    // Same logic as fetchAndUpdateDbStats response handler
    updateDashboardStatCards(stats);
    updateDbUpdaterCardInfo(stats);
}

function handleDashboardWishlistCount(data) {
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
    checkForAutoInitiatedWishlistProcess();
}

// ===============================
// END WEBSOCKET CONNECTION MANAGER
// ===============================

// --- Service Integration Logo Constants ---
const MUSICBRAINZ_LOGO_URL = 'https://upload.wikimedia.org/wikipedia/commons/thumb/9/9e/MusicBrainz_Logo_%282016%29.svg/500px-MusicBrainz_Logo_%282016%29.svg.png';
const DEEZER_LOGO_URL = 'https://cdn.brandfetch.io/idEUKgCNtu/theme/dark/symbol.svg?c=1bxid64Mup7aczewSAYMX&t=1758260798610';
const SPOTIFY_LOGO_URL = 'https://storage.googleapis.com/pr-newsroom-wp/1/2023/05/Spotify_Primary_Logo_RGB_Green.png';
const ITUNES_LOGO_URL = 'https://upload.wikimedia.org/wikipedia/commons/thumb/d/df/ITunes_logo.svg/960px-ITunes_logo.svg.png';
const LASTFM_LOGO_URL = 'https://www.last.fm/static/images/lastfm_avatar_twitter.52a5d69a85ac.png';
const GENIUS_LOGO_URL = 'https://images.genius.com/8ed669cadd956443e29c70361ec4f372.1000x1000x1.png';
const TIDAL_LOGO_URL = 'https://www.svgrepo.com/show/519734/tidal.svg';
const QOBUZ_LOGO_URL = 'https://www.svgrepo.com/show/504778/qobuz.svg';
const DISCOGS_LOGO_URL = 'https://www.svgrepo.com/show/305957/discogs.svg';
const AMAZON_LOGO_URL = '/static/amazon.svg';
function getAudioDBLogoURL() { const el = document.querySelector('img.audiodb-logo'); return el ? el.src : null; }

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
