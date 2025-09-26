// SoulSync WebUI JavaScript - Replicating PyQt6 GUI Functionality

// Global state management
let currentPage = 'dashboard';
let currentTrack = null;
let isPlaying = false;
let mediaPlayerExpanded = false;
let donationAddressesVisible = false;
let searchResults = [];
let currentStream = {
    status: 'stopped',
    progress: 0,
    track: null
};

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
let wishlistCountInterval = null;

// --- Add these globals for the Sync Page ---
let spotifyPlaylists = [];
let selectedPlaylists = new Set();
let activeSyncPollers = {}; // Key: playlist_id, Value: intervalId
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

// --- Artists Page State Management ---
let artistsPageState = {
    currentView: 'search', // 'search', 'results', 'detail'
    searchQuery: '',
    searchResults: [],
    selectedArtist: null,
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
let artistsSearchTimeout = null;
let artistsSearchController = null;

// --- Wishlist Modal Persistence State Management ---
const WishlistModalState = {
    // Track if wishlist modal was visible before page refresh
    setVisible: function() {
        localStorage.setItem('wishlist_modal_visible', 'true');
        console.log('📱 [Modal State] Wishlist modal marked as visible in localStorage');
    },
    
    setHidden: function() {
        localStorage.setItem('wishlist_modal_visible', 'false');
        console.log('📱 [Modal State] Wishlist modal marked as hidden in localStorage');
    },
    
    wasVisible: function() {
        const visible = localStorage.getItem('wishlist_modal_visible') === 'true';
        console.log(`📱 [Modal State] Checking if wishlist modal was visible: ${visible}`);
        return visible;
    },
    
    clear: function() {
        localStorage.removeItem('wishlist_modal_visible');
        console.log('📱 [Modal State] Cleared wishlist modal visibility state');
    },
    
    // Track if user manually closed the modal during auto-processing
    setUserClosed: function() {
        localStorage.setItem('wishlist_modal_user_closed', 'true');
        console.log('📱 [Modal State] User manually closed wishlist modal during auto-processing');
    },
    
    clearUserClosed: function() {
        localStorage.removeItem('wishlist_modal_user_closed');
        console.log('📱 [Modal State] Cleared user closed state');
    },
    
    wasUserClosed: function() {
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

// ===============================
// INITIALIZATION
// ===============================

document.addEventListener('DOMContentLoaded', function() {
    console.log('SoulSync WebUI initializing...');
    
    // Initialize components
    initializeNavigation();
    initializeMediaPlayer();
    initializeDonationWidget();
    initializeSyncPage();
    initializeWatchlist();

    
    // Start global service status polling for sidebar (works on all pages)
    fetchAndUpdateServiceStatus();
    setInterval(fetchAndUpdateServiceStatus, 10000); // Every 10 seconds
    
    // Start always-on download polling (batched, minimal overhead)
    startGlobalDownloadPolling();
    
    // Load initial data
    loadInitialData();
    
    // Handle window resize to re-check track title scrolling
    window.addEventListener('resize', function() {
        if (currentTrack) {
            const trackTitleElement = document.getElementById('track-title');
            const trackTitle = currentTrack.title || 'Unknown Track';
            setTimeout(() => {
                checkAndEnableScrolling(trackTitleElement, trackTitle);
            }, 100); // Small delay to allow layout to settle
        }
    });
    
    console.log('SoulSync WebUI initialized successfully!');
});

// ===============================
// NAVIGATION SYSTEM
// ===============================

function initializeNavigation() {
    const navButtons = document.querySelectorAll('.nav-button');
    
    navButtons.forEach(button => {
        button.addEventListener('click', () => {
            const page = button.getAttribute('data-page');
            navigateToPage(page);
        });
    });
}

function initializeWatchlist() {
    // Add watchlist button click handler
    const watchlistButton = document.getElementById('watchlist-button');
    if (watchlistButton) {
        watchlistButton.addEventListener('click', showWatchlistModal);
    }
    
    // Update watchlist count initially
    updateWatchlistButtonCount();
    
    // Update count every 30 seconds
    setInterval(updateWatchlistButtonCount, 30000);
    
    console.log('Watchlist system initialized');
}

function navigateToPage(pageId) {
    if (pageId === currentPage) return;
    
    // Update navigation buttons (only if there's a nav button for this page)
    document.querySelectorAll('.nav-button').forEach(btn => {
        btn.classList.remove('active');
    });
    const navButton = document.querySelector(`[data-page="${pageId}"]`);
    if (navButton) {
        navButton.classList.add('active');
    }
    
    // Update pages
    document.querySelectorAll('.page').forEach(page => {
        page.classList.remove('active');
    });
    document.getElementById(`${pageId}-page`).classList.add('active');
    
    currentPage = pageId;
    
    // Load page-specific data
    loadPageData(pageId);
}

// REPLACE your old loadPageData function with this one:
// REPLACE your old loadPageData function with this corrected one

async function loadPageData(pageId) {
    try {
        // Stop any active polling when navigating away
        stopDbStatsPolling();
        stopDbUpdatePolling();
        stopWishlistCountPolling();
        stopLogPolling();
        switch (pageId) {
            case 'dashboard':
                await loadDashboardData();
                break;
            case 'sync':
                initializeSyncPage();
                await loadSyncData();
                break;
            case 'downloads':
                initializeSearch();
                initializeFilters();
                await loadDownloadsData();
                break;
            case 'artists':
                // Only fully initialize if not already initialized
                if (!artistsPageState.isInitialized) {
                    initializeArtistsPage();
                } else {
                    // Just restore state if already initialized
                    restoreArtistsPageState();
                }
                break;
            case 'library':
                // Check if we should return to artist detail view instead of list
                if (artistDetailPageState.currentArtistId && artistDetailPageState.currentArtistName) {
                    console.log(`🔄 Returning to artist detail: ${artistDetailPageState.currentArtistName}`);
                    navigateToPage('artist-detail');
                    if (!artistDetailPageState.isInitialized) {
                        initializeArtistDetailPage();
                    }
                    loadArtistDetailData(artistDetailPageState.currentArtistId, artistDetailPageState.currentArtistName);
                } else {
                    // Initialize and load library data
                    if (!libraryPageState.isInitialized) {
                        initializeLibraryPage();
                    } else {
                        // Refresh data when returning to page
                        await loadLibraryArtists();
                    }
                }
                break;
            case 'artist-detail':
                // Artist detail page is handled separately by navigateToArtistDetail()
                break;
            case 'settings':
                initializeSettings();
                await loadSettingsData();
                break;
        }
    } catch (error) {
        console.error(`Error loading ${pageId} data:`, error);
        showToast(`Failed to load ${pageId} data`, 'error');
    }
}

// ===============================
// SERVICE STATUS MONITORING
// ===============================

// Legacy function - now handled by fetchAndUpdateServiceStatus
// Keeping this for compatibility but it's no longer actively used

// Old updateStatusIndicator function removed - replaced by updateSidebarServiceStatus

// ===============================
// MEDIA PLAYER FUNCTIONALITY
// ===============================

function initializeMediaPlayer() {
    const trackTitle = document.getElementById('track-title');
    const playButton = document.getElementById('play-button');
    const stopButton = document.getElementById('stop-button');
    const volumeSlider = document.getElementById('volume-slider');
    
    // Initialize HTML5 audio player
    audioPlayer = document.getElementById('audio-player');
    if (audioPlayer) {
        // Set up audio event listeners
        audioPlayer.addEventListener('timeupdate', updateAudioProgress);
        audioPlayer.addEventListener('ended', onAudioEnded);
        audioPlayer.addEventListener('error', onAudioError);
        audioPlayer.addEventListener('loadstart', onAudioLoadStart);
        audioPlayer.addEventListener('canplay', onAudioCanPlay);
        
        // Set initial volume
        audioPlayer.volume = 0.7; // 70%
        volumeSlider.value = 70;
    }
    
    // Track title click - toggle expansion
    trackTitle.addEventListener('click', toggleMediaPlayerExpansion);
    
    // Media controls
    playButton.addEventListener('click', handlePlayPause);
    stopButton.addEventListener('click', handleStop);
    volumeSlider.addEventListener('input', handleVolumeChange);
    
    // Progress bar controls
    const progressBar = document.getElementById('progress-bar');
    if (progressBar) {
        // Handle seeking
        progressBar.addEventListener('input', handleProgressBarChange);
        progressBar.addEventListener('mousedown', () => {
            progressBar.dataset.seeking = 'true';
        });
        progressBar.addEventListener('mouseup', () => {
            delete progressBar.dataset.seeking;
        });
    }
    
    // Update volume slider styling
    volumeSlider.addEventListener('input', updateVolumeSliderAppearance);
}

function toggleMediaPlayerExpansion() {
    if (!currentTrack) return;
    
    const mediaPlayer = document.getElementById('media-player');
    const expandedContent = document.getElementById('media-expanded');
    const noTrackMessage = document.getElementById('no-track-message');
    
    mediaPlayerExpanded = !mediaPlayerExpanded;
    
    if (mediaPlayerExpanded) {
        mediaPlayer.style.minHeight = '145px';
        expandedContent.classList.remove('hidden');
        noTrackMessage.classList.add('hidden');
    } else {
        mediaPlayer.style.minHeight = '85px';
        expandedContent.classList.add('hidden');
    }
}

function extractTrackTitle(filename) {
    if (!filename) return null;
    
    // Remove file extension
    let title = filename.replace(/\.[^/.]+$/, '');
    
    // Remove path components, keep only the filename
    title = title.split('/').pop().split('\\').pop();
    
    // Clean up common filename patterns
    title = title
        .replace(/^\d+\.?\s*/, '') // Remove track numbers at start
        .replace(/^\d+\s*-\s*/, '') // Remove "01 - " patterns
        .replace(/\s*-\s*\d{4}\s*$/, '') // Remove years at end
        .replace(/\s*\[\d+kbps\].*$/, '') // Remove bitrate info
        .replace(/\s*\(.*?\)\s*$/, '') // Remove parenthetical info at end
        .trim();
    
    return title || null;
}

function setTrackInfo(track) {
    currentTrack = track;
    
    const trackTitleElement = document.getElementById('track-title');
    const trackTitle = track.title || 'Unknown Track';
    
    // Set up the HTML structure for scrolling
    trackTitleElement.innerHTML = `<span class="title-text">${escapeHtml(trackTitle)}</span>`;
    
    document.getElementById('artist-name').textContent = track.artist || 'Unknown Artist';
    document.getElementById('album-name').textContent = track.album || 'Unknown Album';
    
    // Check if title needs scrolling (similar to GUI app)
    setTimeout(() => {
        checkAndEnableScrolling(trackTitleElement, trackTitle);
    }, 100); // Allow DOM to settle
    
    // Enable controls
    document.getElementById('play-button').disabled = false;
    document.getElementById('stop-button').disabled = false;
    
    // Hide no track message
    document.getElementById('no-track-message').classList.add('hidden');
    
    // Auto-expand if collapsed
    if (!mediaPlayerExpanded) {
        toggleMediaPlayerExpansion();
    }
}

function checkAndEnableScrolling(element, text) {
    // Remove any existing scrolling class and reset styles
    element.classList.remove('scrolling');
    element.style.removeProperty('--scroll-distance');
    
    // Force a layout to get accurate measurements
    element.offsetWidth;
    
    // Get the inner text element
    const titleTextElement = element.querySelector('.title-text');
    if (!titleTextElement) return;
    
    // Check if text is wider than container
    const containerWidth = element.offsetWidth;
    const textWidth = titleTextElement.scrollWidth;
    
    // Enable scrolling if text is significantly wider than container
    if (textWidth > containerWidth + 15) {
        const scrollDistance = containerWidth - textWidth;
        element.style.setProperty('--scroll-distance', `${scrollDistance}px`);
        element.classList.add('scrolling');
        console.log(`📜 Enabled scrolling for title: "${text}"`);
        console.log(`📜 Container: ${containerWidth}px, Text: ${textWidth}px, Scroll: ${scrollDistance}px`);
    }
}


function clearTrack() {
    // Force collapse the media player BEFORE clearing currentTrack
    if (mediaPlayerExpanded) {
        // Manually collapse since toggleMediaPlayerExpansion() needs currentTrack
        mediaPlayerExpanded = false;
        const mediaPlayer = document.getElementById('media-player');
        const expandedContent = document.getElementById('media-expanded');
        
        if (mediaPlayer) mediaPlayer.style.minHeight = '85px';
        if (expandedContent) expandedContent.classList.add('hidden');
    }
    
    // Now clear track state
    currentTrack = null;
    isPlaying = false;
    
    const trackTitleElement = document.getElementById('track-title');
    trackTitleElement.innerHTML = '<span class="title-text">No track</span>';
    trackTitleElement.classList.remove('scrolling'); // Remove scrolling animation
    trackTitleElement.style.removeProperty('--scroll-distance'); // Clear CSS variable
    
    document.getElementById('artist-name').textContent = 'Unknown Artist';
    document.getElementById('album-name').textContent = 'Unknown Album';
    document.getElementById('play-button').textContent = '▷';
    document.getElementById('play-button').disabled = true;
    document.getElementById('stop-button').disabled = true;
    
    // Reset progress bar and time displays
    const progressBar = document.getElementById('progress-bar');
    const progressFill = document.getElementById('progress-fill');
    if (progressBar) {
        progressBar.value = 0;
        delete progressBar.dataset.seeking;
    }
    if (progressFill) {
        progressFill.style.width = '0%';
    }
    
    const currentTimeElement = document.getElementById('current-time');
    const totalTimeElement = document.getElementById('total-time');
    if (currentTimeElement) currentTimeElement.textContent = '0:00';
    if (totalTimeElement) totalTimeElement.textContent = '0:00';
    
    // Hide loading animation
    hideLoadingAnimation();
    
    // Show no track message
    document.getElementById('no-track-message').classList.remove('hidden');
    
    console.log('🧹 Track cleared and media player reset');
}

function setPlayingState(playing) {
    isPlaying = playing;
    const playButton = document.getElementById('play-button');
    playButton.textContent = playing ? '⏸︎' : '▷';
}

async function handlePlayPause() {
    // Use new streaming system toggle function
    togglePlayback();
}

async function handleStop() {
    // Use new streaming system stop function
    await stopStream();
    clearTrack();
}

function handleVolumeChange(event) {
    const volume = event.target.value;
    updateVolumeSliderAppearance();
    
    // Update HTML5 audio player volume
    if (audioPlayer) {
        audioPlayer.volume = volume / 100;
    }
}

function handleProgressBarChange(event) {
    // Handle seeking in the audio track
    if (!audioPlayer || !audioPlayer.duration) return;
    
    const progress = parseFloat(event.target.value);
    const newTime = (progress / 100) * audioPlayer.duration;
    
    console.log(`🎯 Seeking to ${formatTime(newTime)} (${progress.toFixed(1)}%)`);
    
    try {
        audioPlayer.currentTime = newTime;
        
        // Update visual progress immediately
        const progressFill = document.getElementById('progress-fill');
        if (progressFill) {
            progressFill.style.width = `${progress}%`;
        }
        
        // Update time displays immediately
        const currentTimeElement = document.getElementById('current-time');
        if (currentTimeElement) {
            currentTimeElement.textContent = formatTime(newTime);
        }
    } catch (error) {
        console.warn('⚠️ Seek failed:', error.message);
        // Reset progress bar to current position
        const actualProgress = (audioPlayer.currentTime / audioPlayer.duration) * 100;
        event.target.value = actualProgress;
        const progressFill = document.getElementById('progress-fill');
        if (progressFill) {
            progressFill.style.width = `${actualProgress}%`;
        }
    }
}

function updateVolumeSliderAppearance() {
    const slider = document.getElementById('volume-slider');
    const value = slider.value;
    slider.style.setProperty('--volume-percent', `${value}%`);
}

function showLoadingAnimation() {
    document.getElementById('loading-animation').classList.remove('hidden');
}

function hideLoadingAnimation() {
    document.getElementById('loading-animation').classList.add('hidden');
}

function setLoadingProgress(percentage) {
    const loadingAnimation = document.getElementById('loading-animation');
    const progressBar = loadingAnimation.querySelector('.loading-progress');
    const loadingText = loadingAnimation.querySelector('.loading-text');
    
    loadingAnimation.classList.remove('hidden');
    progressBar.style.width = `${percentage}%`;
    loadingText.textContent = `${Math.round(percentage)}%`;
}

// ===============================
// STREAMING FUNCTIONALITY
// ===============================

async function startStream(searchResult) {
    // Start streaming a track - handles same track toggle and new track streaming
    try {
        console.log(`🎮 startStream() called with data:`, searchResult);
        
        // Check if this is the same track that's currently playing/loading
        const currentTrackId = currentTrack ? `${currentTrack.username}:${currentTrack.filename}` : null;
        const newTrackId = `${searchResult.username}:${searchResult.filename}`;
        
        console.log(`🎮 startStream() called for: ${searchResult.filename}`);
        console.log(`🎮 Current track ID: ${currentTrackId}`);
        console.log(`🎮 New track ID: ${newTrackId}`);
        
        if (currentTrackId === newTrackId && audioPlayer && !audioPlayer.paused) {
            // Same track clicked while playing - toggle pause
            console.log("🔄 Toggling playback for same track");
            togglePlayback();
            return;
        }
        
        // Different track or no current track - start new stream
        console.log("🎵 Starting new stream");
        
        // Stop current streaming/playback if any
        await stopStream();
        
        // Set track info and show loading state
        setTrackInfo({
            title: extractTrackTitle(searchResult.filename) || searchResult.title || 'Unknown Track',
            artist: searchResult.artist || searchResult.username || 'Unknown Artist', 
            album: searchResult.album || 'Unknown Album',
            username: searchResult.username,
            filename: searchResult.filename
        });
        
        showLoadingAnimation();
        setLoadingProgress(0);
        
        // Start streaming request
        const response = await fetch(API.stream.start, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(searchResult)
        });
        
        if (!response.ok) {
            throw new Error(`HTTP ${response.status}: ${response.statusText}`);
        }
        
        const data = await response.json();
        
        if (!data.success) {
            throw new Error(data.error || 'Failed to start streaming');
        }
        
        console.log("✅ Stream started successfully");
        
        // Start status polling
        startStreamStatusPolling();
        
    } catch (error) {
        console.error('Error starting stream:', error);
        showToast(`Failed to start stream: ${error.message}`, 'error');
        hideLoadingAnimation();
        clearTrack();
    }
}

function startStreamStatusPolling() {
    // Start polling for stream status updates with retry logic
    if (streamStatusPoller) {
        clearInterval(streamStatusPoller);
    }
    
    // Reset polling state
    streamPollingRetries = 0;
    streamPollingInterval = 1000; // Reset to 1-second interval
    
    console.log('🔄 Starting enhanced stream status polling');
    updateStreamStatus(); // Initial check
    streamStatusPoller = setInterval(updateStreamStatus, streamPollingInterval);
}

function stopStreamStatusPolling() {
    // Stop polling for stream status updates
    if (streamStatusPoller) {
        clearInterval(streamStatusPoller);
        streamStatusPoller = null;
        streamPollingRetries = 0;
        streamPollingInterval = 1000; // Reset interval
        console.log('⏹️ Stopped stream status polling');
    }
}

async function updateStreamStatus() {
    // Poll server for streaming progress and handle state changes with enhanced error recovery
    try {
        const controller = new AbortController();
        const timeoutId = setTimeout(() => controller.abort(), 10000); // 10-second timeout
        
        const response = await fetch(API.stream.status, {
            signal: controller.signal
        });
        
        clearTimeout(timeoutId);
        
        if (!response.ok) {
            throw new Error(`HTTP ${response.status}: ${response.statusText}`);
        }
        
        const data = await response.json();
        
        // Reset retry count on successful response
        streamPollingRetries = 0;
        streamPollingInterval = 1000; // Reset to normal interval
        
        // Update current stream state
        currentStream.status = data.status;
        currentStream.progress = data.progress;
        
        switch (data.status) {
            case 'loading':
                setLoadingProgress(data.progress);
                // Update loading text with progress
                const loadingText = document.querySelector('.loading-text');
                if (loadingText && data.progress > 0) {
                    loadingText.textContent = `Downloading... ${Math.round(data.progress)}%`;
                }
                break;
                
            case 'queued':
                // Show queue status with better messaging
                const queueText = document.querySelector('.loading-text');
                if (queueText) {
                    queueText.textContent = 'Queuing with uploader...';
                }
                setLoadingProgress(0); // Reset progress for queue state
                break;
                
            case 'ready':
                // Stream is ready - start audio playback
                console.log('🎵 Stream ready, starting audio playback');
                stopStreamStatusPolling();
                await startAudioPlayback();
                break;
                
            case 'error':
                console.error('❌ Streaming error:', data.error_message);
                stopStreamStatusPolling();
                hideLoadingAnimation();
                showToast(`Streaming error: ${data.error_message || 'Unknown error'}`, 'error');
                clearTrack();
                break;
                
            case 'stopped':
                // Handle stopped state
                console.log('🛑 Stream stopped');
                stopStreamStatusPolling();
                hideLoadingAnimation();
                clearTrack();
                break;
        }
        
    } catch (error) {
        streamPollingRetries++;
        console.warn(`Stream status polling error (attempt ${streamPollingRetries}):`, error.message);
        
        if (streamPollingRetries >= maxStreamPollingRetries) {
            // Too many consecutive failures - give up
            console.error('❌ Stream status polling failed after maximum retries');
            stopStreamStatusPolling();
            hideLoadingAnimation();
            showToast('Lost connection to streaming server', 'error');
            clearTrack();
        } else {
            // Implement exponential backoff for retries
            const backoffMultiplier = Math.min(streamPollingRetries, 5); // Max 5x backoff
            streamPollingInterval = 1000 * backoffMultiplier;
            
            // Restart polling with new interval
            if (streamStatusPoller) {
                clearInterval(streamStatusPoller);
                streamStatusPoller = setInterval(updateStreamStatus, streamPollingInterval);
                console.log(`🔄 Retrying stream status polling with ${streamPollingInterval}ms interval`);
            }
        }
    }
}

async function startAudioPlayback() {
    // Start HTML5 audio playback of the streamed file with enhanced state management
    try {
        if (!audioPlayer) {
            throw new Error('Audio player not initialized');
        }
        
        // Show loading state while preparing audio
        const loadingText = document.querySelector('.loading-text');
        if (loadingText) {
            loadingText.textContent = 'Preparing playback...';
        }
        
        // Set audio source with cache-busting timestamp
        const audioUrl = `/stream/audio?t=${new Date().getTime()}`;
        console.log(`🎵 Loading audio from: ${audioUrl}`);
        
        // Clear any existing source first
        audioPlayer.pause();
        audioPlayer.currentTime = 0;
        audioPlayer.src = '';
        
        // Set new source
        audioPlayer.src = audioUrl;
        audioPlayer.load(); // Force reload
        
        // Wait for audio to be ready with promise-based approach
        await new Promise((resolve, reject) => {
            const timeout = setTimeout(() => {
                reject(new Error('Audio loading timeout'));
            }, 15000); // 15-second timeout
            
            const onCanPlay = () => {
                clearTimeout(timeout);
                audioPlayer.removeEventListener('canplay', onCanPlay);
                audioPlayer.removeEventListener('error', onError);
                resolve();
            };
            
            const onError = (event) => {
                clearTimeout(timeout);
                audioPlayer.removeEventListener('canplay', onCanPlay);
                audioPlayer.removeEventListener('error', onError);
                const error = event.target.error || new Error('Audio loading failed');
                reject(error);
            };
            
            audioPlayer.addEventListener('canplay', onCanPlay);
            audioPlayer.addEventListener('error', onError);
            
            // If already ready, resolve immediately
            if (audioPlayer.readyState >= 3) { // HAVE_FUTURE_DATA
                onCanPlay();
            }
        });
        
        console.log('✅ Audio loaded and ready for playback');
        
        // Try to start playback with retry logic
        let retryCount = 0;
        const maxRetries = 3;
        
        while (retryCount < maxRetries) {
            try {
                await audioPlayer.play();
                console.log('✅ Audio playback started successfully');
                
                // Update UI to playing state
                hideLoadingAnimation();
                setPlayingState(true);
                
                // Show media player if hidden
                const noTrackMessage = document.getElementById('no-track-message');
                if (noTrackMessage) {
                    noTrackMessage.classList.add('hidden');
                }
                
                // Ensure media player is expanded when playback starts
                if (!mediaPlayerExpanded) {
                    toggleMediaPlayerExpansion();
                }
                
                // Update volume to current slider value
                const volumeSlider = document.getElementById('volume-slider');
                if (volumeSlider) {
                    audioPlayer.volume = volumeSlider.value / 100;
                }
                
                // Enable play/stop buttons
                const playButton = document.getElementById('play-button');
                const stopButton = document.getElementById('stop-button');
                if (playButton) playButton.disabled = false;
                if (stopButton) stopButton.disabled = false;
                
                return; // Success!
                
            } catch (playError) {
                retryCount++;
                console.warn(`⚠️ Audio play attempt ${retryCount} failed:`, playError.message);
                
                if (retryCount >= maxRetries) {
                    throw playError; // Re-throw after max retries
                }
                
                // Wait before retry with exponential backoff
                await new Promise(resolve => setTimeout(resolve, 1000 * retryCount));
            }
        }
        
    } catch (error) {
        console.error('❌ Error starting audio playback:', error);
        hideLoadingAnimation();
        
        // Provide user-friendly error messages
        let userMessage = 'Playback failed';
        
        if (error.message.includes('no supported source') || 
            error.message.includes('Not supported') ||
            error.message.includes('MEDIA_ELEMENT_ERROR')) {
            userMessage = 'Audio format not supported by your browser. Try downloading instead.';
        } else if (error.message.includes('network') || error.message.includes('fetch')) {
            userMessage = 'Network error - please check your connection';
        } else if (error.message.includes('decode')) {
            userMessage = 'Audio file is corrupted or incompatible';
        } else if (error.message.includes('timeout')) {
            userMessage = 'Audio loading timeout - file may be too large';
        } else if (error.message.includes('AbortError')) {
            userMessage = 'Playback was interrupted';
        }
        
        showToast(userMessage, 'error');
        clearTrack();
    }
}

async function stopStream() {
    // Stop streaming and clean up all state
    try {
        // Stop status polling
        stopStreamStatusPolling();
        
        // Stop audio playback
        if (audioPlayer) {
            audioPlayer.pause();
            audioPlayer.src = '';
        }
        
        // Call backend stop endpoint
        const response = await fetch(API.stream.stop, { method: 'POST' });
        if (response.ok) {
            const data = await response.json();
            console.log('🛑 Stream stopped:', data.message);
        }
        
        // Reset UI state
        hideLoadingAnimation();
        setPlayingState(false);
        
        // Reset stream state
        currentStream = {
            status: 'stopped',
            progress: 0,
            track: null
        };
        
    } catch (error) {
        console.error('Error stopping stream:', error);
    }
}

function togglePlayback() {
    // Toggle play/pause for currently loaded audio
    if (!audioPlayer || !currentTrack) {
        console.log('⚠️ No audio player or track to toggle');
        return;
    }
    
    if (audioPlayer.paused) {
        audioPlayer.play()
            .then(() => {
                setPlayingState(true);
                console.log('▶️ Resumed playback');
            })
            .catch(error => {
                console.error('Error resuming playback:', error);
                showToast('Failed to resume playback', 'error');
            });
    } else {
        audioPlayer.pause();
        setPlayingState(false);
        console.log('⏸️ Paused playback');
    }
}

// ===============================
// AUDIO EVENT HANDLERS
// ===============================

function updateAudioProgress() {
    // Update progress bar based on audio playback time
    if (!audioPlayer || !audioPlayer.duration) return;
    
    const progress = (audioPlayer.currentTime / audioPlayer.duration) * 100;
    
    // Update progress bar
    const progressBar = document.getElementById('progress-bar');
    const progressFill = document.getElementById('progress-fill');
    if (progressBar && !progressBar.dataset.seeking) {
        progressBar.value = progress;
        // Update visual progress fill
        if (progressFill) {
            progressFill.style.width = `${progress}%`;
        }
    }
    
    // Update time display
    const currentTimeElement = document.getElementById('current-time');
    const totalTimeElement = document.getElementById('total-time');
    
    if (currentTimeElement) {
        currentTimeElement.textContent = formatTime(audioPlayer.currentTime);
    }
    if (totalTimeElement) {
        totalTimeElement.textContent = formatTime(audioPlayer.duration);
    }
}

function onAudioEnded() {
    // Handle audio playback completion
    console.log('🏁 Audio playback ended');
    setPlayingState(false);
    
    // Reset progress to beginning
    const progressBar = document.getElementById('progress-bar');
    const progressFill = document.getElementById('progress-fill');
    if (progressBar) {
        progressBar.value = 0;
    }
    if (progressFill) {
        progressFill.style.width = '0%';
    }
    
    const currentTimeElement = document.getElementById('current-time');
    if (currentTimeElement) {
        currentTimeElement.textContent = '0:00';
    }
    
    // TODO: Auto-advance to next track if queue exists
}

function onAudioError(event) {
    // Handle audio playback errors
    const error = event.target.error;
    console.error('❌ Audio error:', error);
    
    // Don't show error toast if it's just a format/codec issue and retrying
    if (error && error.code) {
        console.error(`Audio error code: ${error.code}, message: ${error.message || 'Unknown error'}`);
        
        // Only show user-facing errors for serious issues
        if (error.code === 4) { // MEDIA_ELEMENT_ERROR: Media not supported
            console.warn('⚠️ Media format not supported by browser, but streaming may still work');
            // Don't clear track or show error - let retry logic handle it
            return;
        }
    }
    
    hideLoadingAnimation();
    
    // Only clear track after a short delay to allow for recovery
    setTimeout(() => {
        if (audioPlayer && audioPlayer.error) {
            let userMessage = 'Audio format not supported by your browser. Try downloading instead.';
            
            if (error && error.code) {
                switch (error.code) {
                    case 1: // MEDIA_ERR_ABORTED
                        userMessage = 'Playback was stopped';
                        break;
                    case 2: // MEDIA_ERR_NETWORK
                        userMessage = 'Network error - please try again';
                        break;
                    case 3: // MEDIA_ERR_DECODE
                        userMessage = 'Audio file is corrupted or incompatible';
                        break;
                    case 4: // MEDIA_ERR_SRC_NOT_SUPPORTED
                        userMessage = 'Audio format not supported by your browser. Try downloading instead.';
                        break;
                }
            }
            
            showToast(userMessage, 'error');
            clearTrack();
        }
    }, 2000);
}

function onAudioLoadStart() {
    // Handle audio load start
    console.log('🔄 Audio loading started');
}

function onAudioCanPlay() {
    // Handle when audio can start playing
    console.log('✅ Audio ready to play');
}

function formatTime(seconds) {
    // Format seconds as MM:SS
    if (!seconds || !isFinite(seconds)) return '0:00';
    
    const minutes = Math.floor(seconds / 60);
    const secs = Math.floor(seconds % 60);
    return `${minutes}:${secs.toString().padStart(2, '0')}`;
}

// ===============================
// AUDIO FORMAT SUPPORT DETECTION
// ===============================

function getFileExtension(filename) {
    if (!filename) return '';
    const ext = filename.toLowerCase().match(/\.([^.]+)$/);
    return ext ? ext[1] : '';
}

function isAudioFormatSupported(filename) {
    const ext = getFileExtension(filename);
    const supportedFormats = ['mp3', 'ogg', 'wav'];  // Most reliable formats
    const partialSupport = ['flac', 'aac', 'm4a', 'opus', 'webm'];  // Test browser support
    const unsupported = ['wma', 'ape', 'aiff'];  // Generally problematic

    if (supportedFormats.includes(ext)) {
        return true;
    }

    if (partialSupport.includes(ext)) {
        // Test if browser can actually play this format
        return canPlayAudioFormat(ext);
    }

    return false;  // Unsupported formats
}

function canPlayAudioFormat(extension) {
    const audio = document.createElement('audio');

    const mimeTypes = {
        'mp3': 'audio/mpeg',
        'ogg': 'audio/ogg; codecs="vorbis"',
        'wav': 'audio/wav',
        'flac': 'audio/flac',
        'aac': 'audio/aac',
        'm4a': 'audio/mp4; codecs="mp4a.40.2"',  // More specific M4A MIME type
        'opus': 'audio/ogg; codecs="opus"',
        'webm': 'audio/webm; codecs="opus"',
        'wma': 'audio/x-ms-wma'
    };

    const mimeType = mimeTypes[extension];
    if (!mimeType) {
        console.warn(`🎵 [FORMAT CHECK] No MIME type found for extension: ${extension}`);
        return false;
    }

    const canPlay = audio.canPlayType(mimeType);
    console.log(`🎵 [FORMAT CHECK] ${extension} (${mimeType}): ${canPlay}`);

    let isSupported = canPlay === 'probably' || canPlay === 'maybe';

    // Special handling for M4A - try fallback MIME types if first one fails
    if (!isSupported && extension === 'm4a') {
        const fallbackMimeTypes = ['audio/mp4', 'audio/x-m4a', 'audio/aac'];
        console.log(`🎵 [FORMAT CHECK] M4A failed with primary MIME type, trying fallbacks...`);

        for (const fallbackMime of fallbackMimeTypes) {
            const fallbackResult = audio.canPlayType(fallbackMime);
            console.log(`🎵 [FORMAT CHECK] M4A fallback (${fallbackMime}): ${fallbackResult}`);
            if (fallbackResult === 'probably' || fallbackResult === 'maybe') {
                isSupported = true;
                console.log(`🎵 [FORMAT CHECK] M4A supported with fallback MIME type: ${fallbackMime}`);
                break;
            }
        }
    }

    console.log(`🎵 [FORMAT CHECK] ${extension} final support result: ${isSupported}`);
    return isSupported;
}

// ===============================
// DONATION WIDGET
// ===============================

function initializeDonationWidget() {
    const toggleButton = document.getElementById('donation-toggle');
    toggleButton.addEventListener('click', toggleDonationAddresses);
}

function toggleDonationAddresses() {
    const addresses = document.getElementById('donation-addresses');
    const toggleButton = document.getElementById('donation-toggle');
    
    donationAddressesVisible = !donationAddressesVisible;
    
    if (donationAddressesVisible) {
        addresses.classList.remove('hidden');
        toggleButton.textContent = 'Hide';
    } else {
        addresses.classList.add('hidden');
        toggleButton.textContent = 'Show';
    }
}

function openKofi() {
    window.open('https://ko-fi.com/boulderbadgedad', '_blank');
    console.log('Opening Ko-fi link');
}

async function copyAddress(address, cryptoName) {
    try {
        await navigator.clipboard.writeText(address);
        showToast(`${cryptoName} address copied to clipboard`, 'success');
        console.log(`Copied ${cryptoName} address: ${address}`);
    } catch (error) {
        console.error('Failed to copy address:', error);
        showToast(`Failed to copy ${cryptoName} address`, 'error');
    }
}

// ===============================
// SETTINGS FUNCTIONALITY
// ===============================

function initializeSettings() {
    // This function is called when the settings page is loaded.
    // It attaches event listeners to all interactive elements on the page.

    // Main save button
    const saveButton = document.getElementById('save-settings');
    if (saveButton) {
        saveButton.addEventListener('click', saveSettings);
    }

    // Server toggle buttons
    const plexToggle = document.getElementById('plex-toggle');
    if (plexToggle) {
        plexToggle.addEventListener('click', () => toggleServer('plex'));
    }
    const jellyfinToggle = document.getElementById('jellyfin-toggle');
    if (jellyfinToggle) {
        jellyfinToggle.addEventListener('click', () => toggleServer('jellyfin'));
    }

    // Auto-detect buttons
    const detectSlskdBtn = document.querySelector('#soulseek-url + .detect-button');
    if (detectSlskdBtn) {
        detectSlskdBtn.addEventListener('click', autoDetectSlskd);
    }
    const detectPlexBtn = document.querySelector('#plex-container .detect-button');
    if (detectPlexBtn) {
        detectPlexBtn.addEventListener('click', autoDetectPlex);
    }
    const detectJellyfinBtn = document.querySelector('#jellyfin-container .detect-button');
    if (detectJellyfinBtn) {
        detectJellyfinBtn.addEventListener('click', autoDetectJellyfin);
    }

    // Test connection buttons
    // Test button event listeners removed - they use onclick attributes in HTML to avoid double firing
}

async function loadSettingsData() {
    try {
        const response = await fetch(API.settings);
        const settings = await response.json();
        
        // Populate Spotify settings
        document.getElementById('spotify-client-id').value = settings.spotify?.client_id || '';
        document.getElementById('spotify-client-secret').value = settings.spotify?.client_secret || '';
        document.getElementById('spotify-redirect-uri').value = settings.spotify?.redirect_uri || 'http://127.0.0.1:8888/callback';
        document.getElementById('spotify-callback-display').textContent = settings.spotify?.redirect_uri || 'http://127.0.0.1:8888/callback';
        
        // Populate Tidal settings  
        document.getElementById('tidal-client-id').value = settings.tidal?.client_id || '';
        document.getElementById('tidal-client-secret').value = settings.tidal?.client_secret || '';
        document.getElementById('tidal-redirect-uri').value = settings.tidal?.redirect_uri || 'http://127.0.0.1:8889/tidal/callback';
        document.getElementById('tidal-callback-display').textContent = settings.tidal?.redirect_uri || 'http://127.0.0.1:8889/tidal/callback';
        
        // Add event listeners to update display URLs when input changes
        document.getElementById('spotify-redirect-uri').addEventListener('input', function() {
            document.getElementById('spotify-callback-display').textContent = this.value || 'http://127.0.0.1:8888/callback';
        });
        
        document.getElementById('tidal-redirect-uri').addEventListener('input', function() {
            document.getElementById('tidal-callback-display').textContent = this.value || 'http://127.0.0.1:8889/tidal/callback';
        });
        
        // Populate Plex settings
        document.getElementById('plex-url').value = settings.plex?.base_url || '';
        document.getElementById('plex-token').value = settings.plex?.token || '';
        
        // Populate Jellyfin settings
        document.getElementById('jellyfin-url').value = settings.jellyfin?.base_url || '';
        document.getElementById('jellyfin-api-key').value = settings.jellyfin?.api_key || '';

        // Populate Navidrome settings
        document.getElementById('navidrome-url').value = settings.navidrome?.base_url || '';
        document.getElementById('navidrome-username').value = settings.navidrome?.username || '';
        document.getElementById('navidrome-password').value = settings.navidrome?.password || '';

        // Set active server and toggle visibility
        const activeServer = settings.active_media_server || 'plex';
        toggleServer(activeServer);
        
        // Populate Soulseek settings
        document.getElementById('soulseek-url').value = settings.soulseek?.slskd_url || '';
        document.getElementById('soulseek-api-key').value = settings.soulseek?.api_key || '';
        
        // Populate Download settings (right column)
        document.getElementById('preferred-quality').value = settings.settings?.audio_quality || 'flac';
        document.getElementById('download-path').value = settings.soulseek?.download_path || './downloads';
        document.getElementById('transfer-path').value = settings.soulseek?.transfer_path || './Transfer';
        
        // Populate Database settings
        document.getElementById('max-workers').value = settings.database?.max_workers || '5';
        
        // Populate Metadata Enhancement settings
        document.getElementById('metadata-enabled').checked = settings.metadata_enhancement?.enabled !== false;
        document.getElementById('embed-album-art').checked = settings.metadata_enhancement?.embed_album_art !== false;
        
        // Populate Playlist Sync settings
        document.getElementById('create-backup').checked = settings.playlist_sync?.create_backup !== false;
        
        // Populate Logging information (read-only)
        document.getElementById('log-level-display').textContent = settings.logging?.level || 'INFO';
        document.getElementById('log-path-display').textContent = settings.logging?.path || 'logs/app.log';
        
    } catch (error) {
        console.error('Error loading settings:', error);
        showToast('Failed to load settings', 'error');
    }
}

function updateMediaServerFields() {
    const serverType = document.getElementById('media-server-type').value;
    const urlInput = document.getElementById('media-server-url');
    const tokenInput = document.getElementById('media-server-token');
    
    if (serverType === 'plex') {
        urlInput.placeholder = 'http://localhost:32400';
        tokenInput.placeholder = 'Plex Token';
    } else {
        urlInput.placeholder = 'http://localhost:8096';
        tokenInput.placeholder = 'Jellyfin API Key';
    }
}

function toggleServer(serverType) {
    // Update toggle buttons
    document.getElementById('plex-toggle').classList.remove('active');
    document.getElementById('jellyfin-toggle').classList.remove('active');
    document.getElementById('navidrome-toggle').classList.remove('active');
    document.getElementById(`${serverType}-toggle`).classList.add('active');

    // Show/hide server containers
    document.getElementById('plex-container').classList.toggle('hidden', serverType !== 'plex');
    document.getElementById('jellyfin-container').classList.toggle('hidden', serverType !== 'jellyfin');
    document.getElementById('navidrome-container').classList.toggle('hidden', serverType !== 'navidrome');
}

async function saveSettings() {
    // Determine active server from toggle buttons
    let activeServer = 'plex';
    if (document.getElementById('jellyfin-toggle').classList.contains('active')) {
        activeServer = 'jellyfin';
    } else if (document.getElementById('navidrome-toggle').classList.contains('active')) {
        activeServer = 'navidrome';
    }
    
    const settings = {
        active_media_server: activeServer,
        spotify: {
            client_id: document.getElementById('spotify-client-id').value,
            client_secret: document.getElementById('spotify-client-secret').value,
            redirect_uri: document.getElementById('spotify-redirect-uri').value
        },
        tidal: {
            client_id: document.getElementById('tidal-client-id').value,
            client_secret: document.getElementById('tidal-client-secret').value,
            redirect_uri: document.getElementById('tidal-redirect-uri').value
        },
        plex: {
            base_url: document.getElementById('plex-url').value,
            token: document.getElementById('plex-token').value
        },
        jellyfin: {
            base_url: document.getElementById('jellyfin-url').value,
            api_key: document.getElementById('jellyfin-api-key').value
        },
        navidrome: {
            base_url: document.getElementById('navidrome-url').value,
            username: document.getElementById('navidrome-username').value,
            password: document.getElementById('navidrome-password').value
        },
        soulseek: {
            slskd_url: document.getElementById('soulseek-url').value,
            api_key: document.getElementById('soulseek-api-key').value,
            download_path: document.getElementById('download-path').value,
            transfer_path: document.getElementById('transfer-path').value
        },
        settings: {
            audio_quality: document.getElementById('preferred-quality').value
        },
        database: {
            max_workers: parseInt(document.getElementById('max-workers').value)
        },
        metadata_enhancement: {
            enabled: document.getElementById('metadata-enabled').checked,
            embed_album_art: document.getElementById('embed-album-art').checked
        },
        playlist_sync: {
            create_backup: document.getElementById('create-backup').checked
        }
    };
    
    try {
        showLoadingOverlay('Saving settings...');
        
        const response = await fetch(API.settings, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(settings)
        });
        
        const result = await response.json();
        
        if (result.success) {
            showToast('Settings saved successfully', 'success');
            // Trigger immediate status update
            setTimeout(updateServiceStatus, 1000);
        } else {
            showToast(`Failed to save settings: ${result.error}`, 'error');
        }
    } catch (error) {
        console.error('Error saving settings:', error);
        showToast('Failed to save settings', 'error');
    } finally {
        hideLoadingOverlay();
    }
}

async function testConnection(service) {
    try {
        showLoadingOverlay(`Testing ${service} connection...`);
        
        const response = await fetch(API.testConnection, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ service })
        });
        
        const result = await response.json();
        
        if (result.success) {
            showToast(`${service} connection successful`, 'success');
        } else {
            showToast(`${service} connection failed: ${result.error}`, 'error');
        }
    } catch (error) {
        console.error(`Error testing ${service} connection:`, error);
        showToast(`Failed to test ${service} connection`, 'error');
    } finally {
        hideLoadingOverlay();
    }
}

// Dashboard-specific test functions that create activity items
async function testDashboardConnection(service) {
    try {
        showLoadingOverlay(`Testing ${service} service...`);
        
        const response = await fetch(API.testDashboardConnection, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ service })
        });
        
        const result = await response.json();
        
        if (result.success) {
            showToast(`${service} service verified`, 'success');
        } else {
            showToast(`${service} service check failed: ${result.error}`, 'error');
        }
    } catch (error) {
        console.error(`Error testing ${service} service:`, error);
        showToast(`Failed to test ${service} service`, 'error');
    } finally {
        hideLoadingOverlay();
    }
}

// Individual Auto-detect functions - same as GUI
async function autoDetectPlex() {
    try {
        showLoadingOverlay('Auto-detecting Plex server...');
        
        const response = await fetch('/api/detect-media-server', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ server_type: 'plex' })
        });
        
        const result = await response.json();
        
        if (result.success) {
            document.getElementById('plex-url').value = result.found_url;
            showToast(`Plex server detected: ${result.found_url}`, 'success');
        } else {
            showToast(result.error, 'error');
        }
        
    } catch (error) {
        console.error('Error auto-detecting Plex:', error);
        showToast('Failed to auto-detect Plex server', 'error');
    } finally {
        hideLoadingOverlay();
    }
}

async function autoDetectJellyfin() {
    try {
        showLoadingOverlay('Auto-detecting Jellyfin server...');
        
        const response = await fetch('/api/detect-media-server', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ server_type: 'jellyfin' })
        });
        
        const result = await response.json();
        
        if (result.success) {
            document.getElementById('jellyfin-url').value = result.found_url;
            showToast(`Jellyfin server detected: ${result.found_url}`, 'success');
        } else {
            showToast(result.error, 'error');
        }
        
    } catch (error) {
        console.error('Error auto-detecting Jellyfin:', error);
        showToast('Failed to auto-detect Jellyfin server', 'error');
    } finally {
        hideLoadingOverlay();
    }
}

async function autoDetectNavidrome() {
    try {
        showLoadingOverlay('Auto-detecting Navidrome server...');

        const response = await fetch('/api/detect-media-server', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ server_type: 'navidrome' })
        });

        const result = await response.json();

        if (result.success) {
            document.getElementById('navidrome-url').value = result.found_url;
            showToast(`Navidrome server detected: ${result.found_url}`, 'success');
        } else {
            showToast(result.error, 'error');
        }

    } catch (error) {
        console.error('Error auto-detecting Navidrome:', error);
        showToast('Failed to auto-detect Navidrome server', 'error');
    } finally {
        hideLoadingOverlay();
    }
}

async function autoDetectSlskd() {
    try {
        showLoadingOverlay('Auto-detecting Soulseek (slskd) server...');
        
        const response = await fetch('/api/detect-soulseek', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' }
        });
        
        const result = await response.json();
        
        if (result.success) {
            document.getElementById('soulseek-url').value = result.found_url;
            showToast(`Soulseek server detected: ${result.found_url}`, 'success');
        } else {
            showToast(result.error, 'error');
        }
        
    } catch (error) {
        console.error('Error auto-detecting Soulseek:', error);
        showToast('Failed to auto-detect Soulseek server', 'error');
    } finally {
        hideLoadingOverlay();
    }
}


function cancelDetection(service) {
    const progressDiv = document.getElementById(`${service}-detection-progress`);
    progressDiv.classList.add('hidden');
    showToast(`${service} detection cancelled`, 'error');
}

function updateStatusDisplays() {
    // Update status displays based on current service status
    // This would be called after status updates
    const services = ['spotify', 'media-server', 'soulseek'];
    services.forEach(service => {
        const display = document.getElementById(`${service}-status-display`);
        if (display) {
            // Status will be updated by the regular status monitoring
        }
    });
}

async function authenticateSpotify() {
    try {
        showLoadingOverlay('Starting Spotify authentication...');
        showToast('Spotify authentication started', 'success');
        window.open('/auth/spotify', '_blank');
    } catch (error) {
        console.error('Error authenticating Spotify:', error);
        showToast('Failed to start Spotify authentication', 'error');
    } finally {
        hideLoadingOverlay();
    }
}

async function authenticateTidal() {
    try {
        showLoadingOverlay('Starting Tidal authentication...');
        // This would trigger the OAuth flow
        showToast('Tidal authentication started', 'success');
        // In a real implementation, this would open the OAuth URL
        window.open('/auth/tidal', '_blank');
    } catch (error) {
        console.error('Error authenticating Tidal:', error);
        showToast('Failed to start Tidal authentication', 'error');
    } finally {
        hideLoadingOverlay();
    }
}

function browsePath(pathType) {
    showToast(`Path browser not available in web interface. Please enter path manually.`, 'error');
}


// ===============================
// SEARCH FUNCTIONALITY
// ===============================

function initializeSearch() {
    // --- FIX: Corrected the element IDs to match the HTML ---
    const searchInput = document.getElementById('downloads-search-input');
    const searchButton = document.getElementById('downloads-search-btn');
    
    // Add this line to get the cancel button
    const cancelButton = document.getElementById('downloads-cancel-btn');

    if (searchButton && searchInput) {
        searchButton.addEventListener('click', performDownloadsSearch);
        searchInput.addEventListener('keypress', (e) => {
            if (e.key === 'Enter') performDownloadsSearch();
        });
    }

    // Add this event listener for the cancel button
    if (cancelButton) {
        cancelButton.addEventListener('click', () => {
            if (searchAbortController) {
                searchAbortController.abort(); // This cancels the fetch request
                console.log("Search cancelled by user.");
            }
        });
    }
}

async function performSearch() {
    const query = document.getElementById('search-input').value.trim();
    if (!query) {
        showToast('Please enter a search term', 'error');
        return;
    }
    
    try {
        showLoadingOverlay('Searching...');
        displaySearchResults([]);  // Clear previous results
        
        const response = await fetch(API.search, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ query })
        });
        
        const data = await response.json();
        
        if (data.error) {
            showToast(`Search error: ${data.error}`, 'error');
            return;
        }
        
        searchResults = data.results || [];
        displaySearchResults(searchResults);
        
        if (searchResults.length === 0) {
            showToast('No results found', 'error');
        } else {
            showToast(`Found ${searchResults.length} results`, 'success');
        }
        
    } catch (error) {
        console.error('Error performing search:', error);
        showToast('Search failed', 'error');
    } finally {
        hideLoadingOverlay();
    }
}

function displaySearchResults(results) {
    const resultsContainer = document.getElementById('search-results');
    
    if (!results.length) {
        resultsContainer.innerHTML = '<div class="no-results">No search results</div>';
        return;
    }
    
    resultsContainer.innerHTML = results.map((result, index) => {
        const isAlbum = result.type === 'album';
        const sizeText = isAlbum ? 
            `${result.track_count || 0} tracks, ${(result.size_mb || 0).toFixed(1)} MB` :
            `${(result.file_size / 1024 / 1024).toFixed(1)} MB, ${result.bitrate || 0}kbps`;
        
        return `
            <div class="search-result-item" onclick="selectResult(${index})">
                <div class="result-header">
                    <div class="result-info">
                        <div class="result-title">${escapeHtml(result.title)}</div>
                        <div class="result-artist">${escapeHtml(result.artist)}</div>
                        ${result.album ? `<div class="result-album">${escapeHtml(result.album)}</div>` : ''}
                    </div>
                    <div class="result-actions">
                        <button class="stream-button" onclick="event.stopPropagation(); streamTrack(${index})">
                            ▷ Stream
                        </button>
                        <button class="download-button" onclick="event.stopPropagation(); startDownload(${index})">
                            ⬇ Download
                        </button>
                    </div>
                </div>
                <div class="result-details">
                    <span class="result-size">${sizeText}</span>
                    <span class="result-user">by ${escapeHtml(result.username)}</span>
                    ${result.quality ? `<span class="result-quality">${escapeHtml(result.quality)}</span>` : ''}
                </div>
            </div>
        `;
    }).join('');
}

function selectResult(index) {
    const result = searchResults[index];
    if (!result) return;
    
    console.log('Selected result:', result);
    // Could show detailed view or additional actions here
}


async function startDownload(index) {
    const result = searchResults[index];
    if (!result) return;
    
    try {
        const response = await fetch('/api/downloads/start', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(result)
        });
        
        const data = await response.json();
        
        if (data.success) {
            showToast('Download started', 'success');
        } else {
            showToast(`Download failed: ${data.error}`, 'error');
        }
    } catch (error) {
        console.error('Error starting download:', error);
        showToast('Failed to start download', 'error');
    }
}

// ===============================
// PAGE DATA LOADING
// ===============================

async function loadInitialData() {
    try {
        // Load artist bubble state first
        await hydrateArtistBubblesFromSnapshot();
        
        // Load dashboard data by default
        await loadDashboardData();
    } catch (error) {
        console.error('Error loading initial data:', error);
    }
}

async function loadDashboardData() {
    try {
        const response = await fetch(API.activity);
        const data = await response.json();
        
        const activityFeed = document.getElementById('activity-feed');
        if (data.activities && data.activities.length) {
            activityFeed.innerHTML = data.activities.map(activity => `
                <div class="activity-item">
                    <span class="activity-time">${activity.time}</span>
                    <span class="activity-text">${escapeHtml(activity.text)}</span>
                </div>
            `).join('');
        }
        
        // Initialize wishlist count when dashboard loads
        await updateWishlistCount();
        
        // Start periodic refresh of wishlist count (every 30 seconds, matching GUI behavior)
        stopWishlistCountPolling(); // Ensure no duplicates
        wishlistCountInterval = setInterval(updateWishlistCount, 30000);
        
    } catch (error) {
        console.error('Error loading dashboard data:', error);
    }
}

// ===========================================
// == SYNC PAGE SPOTIFY FUNCTIONALITY       ==
// ===========================================

async function loadSyncData() {
    // This is called when the sync page is navigated to.
    if (!spotifyPlaylistsLoaded) {
        await loadSpotifyPlaylists();
    }
    
    // Load YouTube playlists from backend (always refresh to get latest state)
    await loadYouTubePlaylistsFromBackend();
}

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
            const response = await fetch(`/api/artist/${artistId}/album/${albumId}/tracks`);
            const data = await response.json();
            
            if (!data.success || !data.album || !data.tracks) {
                console.error('❌ Failed to fetch album data for rehydration:', data.error);
                return;
            }
            
            const album = data.album;
            const tracks = data.tracks;
            
            // Extract artist info from the first track (all tracks should have same artist)
            const artist = {
                id: artistId,
                name: tracks[0].artists[0] // Use first artist name from first track
            };
            
            console.log(`✅ Retrieved album data: "${album.name}" by ${artist.name} (${tracks.length} tracks)`);
            
            // Create the modal using the same function as normal artist album downloads
            await openDownloadMissingModalForArtistAlbum(virtualPlaylistId, playlistName, tracks, album, artist);
            
            // Update the rehydrated process with batch info and hide modal for background rehydration
            const process = activeDownloadProcesses[virtualPlaylistId];
            if (process) {
                process.status = 'running';
                process.batchId = batchId;
                
                // Update button states to reflect running status
                const beginBtn = document.getElementById(`begin-analysis-btn-${virtualPlaylistId}`);
                const cancelBtn = document.getElementById(`cancel-all-btn-${virtualPlaylistId}`);
                if (beginBtn) beginBtn.style.display = 'none';
                if (cancelBtn) cancelBtn.style.display = 'inline-block';
                
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

async function rehydrateModal(processInfo, userRequested = false) {
    const { playlist_id, playlist_name, batch_id } = processInfo;
    console.log(`💧 Rehydrating modal for "${playlist_name}" (batch: ${batch_id}) - User requested: ${userRequested}`);

    // Handle YouTube virtual playlists - skip rehydration here, handled by YouTube system
    if (playlist_id.startsWith('youtube_')) {
        console.log(`⏭️ Skipping YouTube virtual playlist rehydration - handled by YouTube system`);
        return;
    }

    // Handle artist album virtual playlists
    if (playlist_id.startsWith('artist_album_')) {
        console.log(`💧 Rehydrating artist album virtual playlist: ${playlist_id}`);
        await rehydrateArtistAlbumModal(playlist_id, playlist_name, batch_id);
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
            console.log(`💧 [Rehydrate] Creating new wishlist modal for active process: ${batch_id}`);
            
            // Create the modal with current server state
            await openDownloadMissingWishlistModal();
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

            // SIMPLIFIED VISIBILITY LOGIC: Show modal if user requested it, otherwise keep hidden for background sync
            if (userRequested) {
                console.log('👤 [Rehydrate] User requested - showing wishlist modal');
                process.modalElement.style.display = 'flex';
                WishlistModalState.setVisible();
                WishlistModalState.clearUserClosed();
            } else {
                console.log('🔄 [Rehydrate] Background sync - keeping modal hidden until user interaction');
                process.modalElement.style.display = 'none';
                WishlistModalState.setHidden();
            }
        }
        return;
    }

    // Handle regular Spotify playlist processes
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

    startModalDownloadPolling(playlist_id);

    process.modalElement.style.display = 'none';
}

// ===================================================================
// YOUTUBE PLAYLIST BACKEND HYDRATION FUNCTIONS
// ===================================================================

async function loadYouTubePlaylistsFromBackend() {
    // Load all stored YouTube playlists from backend and recreate cards (similar to Spotify hydration)
    try {
        console.log('📋 Loading YouTube playlists from backend...');
        
        const response = await fetch('/api/youtube/playlists');
        if (!response.ok) {
            const error = await response.json();
            throw new Error(error.error || 'Failed to fetch YouTube playlists');
        }
        
        const data = await response.json();
        const playlists = data.playlists || [];
        
        console.log(`🎬 Found ${playlists.length} stored YouTube playlists in backend`);
        
        if (playlists.length === 0) {
            console.log('📋 No YouTube playlists to hydrate');
            return;
        }
        
        const container = document.getElementById('youtube-playlist-container');
        
        // Create cards for playlists that don't already exist (avoid duplicates)
        for (const playlistInfo of playlists) {
            const urlHash = playlistInfo.url_hash;
            
            // Check if card already exists (from rehydration or previous loading)
            if (youtubePlaylistStates[urlHash] && youtubePlaylistStates[urlHash].cardElement && 
                document.body.contains(youtubePlaylistStates[urlHash].cardElement)) {
                console.log(`⏭️ Skipping existing YouTube playlist card: ${playlistInfo.playlist.name}`);
                
                // Update existing state with backend data
                const state = youtubePlaylistStates[urlHash];
                state.phase = playlistInfo.phase;
                state.discoveryProgress = playlistInfo.discovery_progress;
                state.spotifyMatches = playlistInfo.spotify_matches;
                state.convertedSpotifyPlaylistId = playlistInfo.converted_spotify_playlist_id;
                
                // Fetch discovery results for existing cards too if they don't have them
                if (playlistInfo.phase !== 'fresh' && playlistInfo.phase !== 'discovering' && 
                    (!state.discoveryResults || state.discoveryResults.length === 0)) {
                    try {
                        console.log(`🔍 Fetching missing discovery results for existing card: ${playlistInfo.playlist.name}`);
                        const stateResponse = await fetch(`/api/youtube/state/${urlHash}`);
                        if (stateResponse.ok) {
                            const fullState = await stateResponse.json();
                            if (fullState.discovery_results) {
                                state.discoveryResults = fullState.discovery_results;
                                state.syncPlaylistId = fullState.sync_playlist_id;
                                state.syncProgress = fullState.sync_progress || {};
                                console.log(`✅ Restored ${state.discoveryResults.length} discovery results for existing card`);
                            }
                        }
                    } catch (error) {
                        console.warn(`⚠️ Error fetching discovery results for existing card:`, error.message);
                    }
                }
                
                continue;
            }
            
            console.log(`🎬 Creating YouTube playlist card: ${playlistInfo.playlist.name} (Phase: ${playlistInfo.phase})`);
            createYouTubeCardFromBackendState(playlistInfo);
            
            // Fetch discovery results for non-fresh playlists (same logic as rehydrateYouTubePlaylist)
            if (playlistInfo.phase !== 'fresh' && playlistInfo.phase !== 'discovering') {
                try {
                    console.log(`🔍 Fetching discovery results for: ${playlistInfo.playlist.name}`);
                    const stateResponse = await fetch(`/api/youtube/state/${urlHash}`);
                    if (stateResponse.ok) {
                        const fullState = await stateResponse.json();
                        console.log(`📋 Retrieved full state with ${fullState.discovery_results?.length || 0} discovery results`);
                        
                        // Store discovery results in local state
                        const state = youtubePlaylistStates[urlHash];
                        if (fullState.discovery_results && state) {
                            state.discoveryResults = fullState.discovery_results;
                            state.syncPlaylistId = fullState.sync_playlist_id;
                            state.syncProgress = fullState.sync_progress || {};
                            console.log(`✅ Restored ${state.discoveryResults.length} discovery results for: ${playlistInfo.playlist.name}`);
                        }
                    } else {
                        console.warn(`⚠️ Could not fetch discovery results for: ${playlistInfo.playlist.name}`);
                    }
                } catch (error) {
                    console.warn(`⚠️ Error fetching discovery results for ${playlistInfo.playlist.name}:`, error.message);
                }
            }
        }
        
        // Rehydrate download modals for YouTube playlists in downloading/download_complete phases
        for (const playlistInfo of playlists) {
            if ((playlistInfo.phase === 'downloading' || playlistInfo.phase === 'download_complete') && 
                playlistInfo.converted_spotify_playlist_id && playlistInfo.download_process_id) {
                
                const convertedPlaylistId = playlistInfo.converted_spotify_playlist_id;
                
                if (!activeDownloadProcesses[convertedPlaylistId]) {
                    console.log(`💧 Rehydrating download modal for YouTube playlist: ${playlistInfo.playlist.name}`);
                    try {
                        // Create the download modal using the YouTube-specific function
                        const spotifyTracks = youtubePlaylistStates[playlistInfo.url_hash]?.discoveryResults
                            ?.filter(result => result.spotify_data)
                            ?.map(result => result.spotify_data) || [];
                        
                        if (spotifyTracks.length > 0) {
                            await openDownloadMissingModalForYouTube(
                                convertedPlaylistId, 
                                playlistInfo.playlist.name, 
                                spotifyTracks
                            );
                            
                            // Set the modal to running state with the correct batch ID
                            const process = activeDownloadProcesses[convertedPlaylistId];
                            if (process) {
                                process.status = 'running';
                                process.batchId = playlistInfo.download_process_id;
                                
                                // Update UI to running state
                                const beginBtn = document.getElementById(`begin-analysis-btn-${convertedPlaylistId}`);
                                const cancelBtn = document.getElementById(`cancel-all-btn-${convertedPlaylistId}`);
                                if (beginBtn) beginBtn.style.display = 'none';
                                if (cancelBtn) cancelBtn.style.display = 'inline-block';
                                
                                // Start polling for this process
                                startModalDownloadPolling(convertedPlaylistId);
                                
                                // Hide modal since this is background rehydration
                                process.modalElement.style.display = 'none';
                                console.log(`✅ Rehydrated download modal for YouTube playlist: ${playlistInfo.playlist.name}`);
                            }
                        } else {
                            console.warn(`⚠️ No Spotify tracks found for YouTube download modal: ${playlistInfo.playlist.name}`);
                        }
                    } catch (error) {
                        console.error(`❌ Error rehydrating download modal for ${playlistInfo.playlist.name}:`, error);
                    }
                }
            }
        }
        
        console.log(`✅ Successfully hydrated ${playlists.length} YouTube playlists from backend`);
        
    } catch (error) {
        console.error('❌ Error loading YouTube playlists from backend:', error);
        showToast(`Error loading YouTube playlists: ${error.message}`, 'error');
    }
}

function createYouTubeCardFromBackendState(playlistInfo) {
    // Create YouTube playlist card from backend state data
    const urlHash = playlistInfo.url_hash;
    const playlist = playlistInfo.playlist;
    const phase = playlistInfo.phase;
    
    const container = document.getElementById('youtube-playlist-container');
    
    // Remove placeholder if it exists
    const placeholder = container.querySelector('.youtube-playlist-placeholder');
    if (placeholder) {
        placeholder.remove();
    }
    
    // Create card HTML (using EXACT same structure as createYouTubeCard)
    const cardHtml = `
        <div class="youtube-playlist-card" id="youtube-card-${urlHash}" data-url="${playlistInfo.url}" onclick="handleYouTubeCardClick('${urlHash}')">
            <div class="playlist-card-icon youtube-icon">▶</div>
            <div class="playlist-card-content">
                <div class="playlist-card-name">${escapeHtml(playlist.name)}</div>
                <div class="playlist-card-info">
                    <span class="playlist-card-track-count">${playlist.tracks.length} tracks</span>
                    <span class="playlist-card-phase-text" style="color: ${getPhaseColor(phase)};">${getPhaseText(phase)}</span>
                </div>
            </div>
            <div class="playlist-card-progress ${phase === 'fresh' ? 'hidden' : ''}">
                ♪ ${playlistInfo.spotify_total} / ✓ ${playlistInfo.spotify_matches} / ✗ ${playlistInfo.spotify_total - playlistInfo.spotify_matches} / ${Math.round(getProgressWidth(playlistInfo))}%
            </div>
            <button class="playlist-card-action-btn">${getActionButtonText(phase)}</button>
        </div>
    `;
    
    container.insertAdjacentHTML('beforeend', cardHtml);
    
    // Store state for UI management (but backend remains source of truth)
    youtubePlaylistStates[urlHash] = {
        phase: phase,
        url: playlistInfo.url,
        playlist: playlist,
        cardElement: document.getElementById(`youtube-card-${urlHash}`),
        discoveryResults: [],
        discoveryProgress: playlistInfo.discovery_progress,
        spotifyMatches: playlistInfo.spotify_matches,
        convertedSpotifyPlaylistId: playlistInfo.converted_spotify_playlist_id,
        backendSynced: true  // Flag to indicate this came from backend
    };
    
    console.log(`🃏 Created YouTube card from backend state: ${playlist.name} (${phase})`);
}

function getActionButtonText(phase) {
    switch (phase) {
        case 'fresh': return 'Discover';
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
        case 'fresh': return 'Ready to discover';
        case 'discovering': return 'Discovering...';
        case 'discovered': return 'Discovery Complete';
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
        case 'discovered': case 'sync_complete': case 'download_complete': return '#1db954';
        default: return '#999';
    }
}

function getProgressWidth(playlistInfo) {
    if (playlistInfo.phase === 'fresh') return 0;
    if (playlistInfo.spotify_total === 0) return 0;
    return Math.round((playlistInfo.spotify_matches / playlistInfo.spotify_total) * 100);
}

async function rehydrateYouTubePlaylist(playlistInfo, userRequested = false) {
    // Rehydrate a YouTube playlist's discovery modal state (similar to rehydrateModal)
    const urlHash = playlistInfo.url_hash;
    const playlistName = playlistInfo.playlist_name;
    const phase = playlistInfo.phase;
    
    console.log(`💧 Rehydrating YouTube playlist "${playlistName}" (Phase: ${phase}) - User requested: ${userRequested}`);
    
    try {
        // First, ensure the card exists (create from backend if needed)
        if (!youtubePlaylistStates[urlHash] || !youtubePlaylistStates[urlHash].cardElement) {
            console.log(`🃏 Creating missing YouTube card for rehydration: ${playlistName}`);
            
            // Since playlistInfo from active processes doesn't have full playlist data,
            // we need to fetch it from the backend first
            try {
                const stateResponse = await fetch(`/api/youtube/state/${urlHash}`);
                if (stateResponse.ok) {
                    const fullPlaylistState = await stateResponse.json();
                    createYouTubeCardFromBackendState(fullPlaylistState);
                } else {
                    console.error(`❌ Could not fetch full playlist state for card creation: ${playlistName}`);
                    return; // Can't create card without playlist data
                }
            } catch (error) {
                console.error(`❌ Error fetching playlist state for card creation: ${error.message}`);
                return;
            }
        }
        
        // Fetch full state from backend to get discovery results
        let fullState = null;
        if (phase !== 'fresh' && phase !== 'discovering') {
            try {
                console.log(`🔍 Fetching full backend state for: ${playlistName}`);
                const stateResponse = await fetch(`/api/youtube/state/${urlHash}`);
                if (stateResponse.ok) {
                    fullState = await stateResponse.json();
                    console.log(`📋 Retrieved full state with ${fullState.discovery_results?.length || 0} discovery results`);
                }
            } catch (error) {
                console.warn(`⚠️ Could not fetch full state for ${playlistName}:`, error.message);
            }
        }

        // Update local state to match backend
        const state = youtubePlaylistStates[urlHash];
        state.phase = phase;
        state.discoveryProgress = playlistInfo.discovery_progress;
        state.spotifyMatches = playlistInfo.spotify_matches;
        state.convertedSpotifyPlaylistId = playlistInfo.converted_spotify_playlist_id;
        
        // Restore discovery results if we have them
        if (fullState && fullState.discovery_results) {
            state.discoveryResults = fullState.discovery_results;
            state.syncPlaylistId = fullState.sync_playlist_id;
            state.syncProgress = fullState.sync_progress || {};
            console.log(`✅ Restored ${state.discoveryResults.length} discovery results from backend`);
            
            // Update modal if it already exists
            const existingModal = document.getElementById(`youtube-discovery-modal-${urlHash}`);
            if (existingModal && !existingModal.classList.contains('hidden')) {
                console.log(`🔄 Refreshing existing modal with restored discovery results`);
                refreshYouTubeDiscoveryModalTable(urlHash);
            }
        }
        
        // Update card display
        updateYouTubeCardPhase(urlHash, phase);
        updateYouTubeCardProgress(urlHash, playlistInfo);
        
        // Handle active discovery polling
        if (phase === 'discovering') {
            console.log(`🔍 Resuming discovery polling for: ${playlistName}`);
            startYouTubeDiscoveryPolling(urlHash);
        }
        
        // Open modal if user requested
        if (userRequested) {
            switch (phase) {
                case 'discovering':
                case 'discovered':
                case 'syncing':
                case 'sync_complete':
                    openYouTubeDiscoveryModal(urlHash);
                    break;
                case 'downloading':
                case 'download_complete':
                    // Open download modal if we have the converted playlist ID
                    if (playlistInfo.converted_spotify_playlist_id) {
                        await openDownloadMissingModal(playlistInfo.converted_spotify_playlist_id);
                    }
                    break;
            }
        }
        
        console.log(`✅ Successfully rehydrated YouTube playlist: ${playlistName}`);
        
    } catch (error) {
        console.error(`❌ Error rehydrating YouTube playlist "${playlistName}":`, error);
    }
}

async function removeYouTubePlaylistFromBackend(event, urlHash) {
    // Remove YouTube playlist from backend storage and update UI
    event.stopPropagation(); // Prevent card click
    
    const state = youtubePlaylistStates[urlHash];
    if (!state) return;
    
    const playlistName = state.playlist.name;
    
    try {
        console.log(`🗑️ Removing YouTube playlist from backend: ${playlistName}`);
        
        const response = await fetch(`/api/youtube/delete/${urlHash}`, {
            method: 'DELETE'
        });
        
        if (!response.ok) {
            const error = await response.json();
            throw new Error(error.error || 'Failed to delete playlist');
        }
        
        // Remove card from UI
        if (state.cardElement) {
            state.cardElement.remove();
        }
        
        // Remove from client state
        delete youtubePlaylistStates[urlHash];
        
        // Stop any active polling
        if (activeYouTubePollers[urlHash]) {
            clearInterval(activeYouTubePollers[urlHash]);
            delete activeYouTubePollers[urlHash];
        }
        
        // Close discovery modal if open
        const modal = document.getElementById(`youtube-discovery-modal-${urlHash}`);
        if (modal) {
            modal.remove();
        }
        
        // Show placeholder if no cards left
        const container = document.getElementById('youtube-playlist-container');
        const cards = container.querySelectorAll('.youtube-playlist-card');
        if (cards.length === 0) {
            container.innerHTML = '<div class="youtube-playlist-placeholder">No YouTube playlists added yet. Parse a YouTube playlist URL above to get started!</div>';
        }
        
        showToast(`Removed "${playlistName}" from backend storage`, 'success');
        console.log(`✅ Successfully removed YouTube playlist: ${playlistName}`);
        
    } catch (error) {
        console.error(`❌ Error removing YouTube playlist "${playlistName}":`, error);
        showToast(`Error removing playlist: ${error.message}`, 'error');
    }
}

async function loadSpotifyPlaylists() {
    const container = document.getElementById('spotify-playlist-container');
    const refreshBtn = document.getElementById('spotify-refresh-btn');
    
    container.innerHTML = `<div class="playlist-placeholder">🔄 Loading playlists...</div>`;
    refreshBtn.disabled = true;
    refreshBtn.textContent = '🔄 Loading...';

    try {
        const response = await fetch('/api/spotify/playlists');
        if (!response.ok) {
            const error = await response.json();
            throw new Error(error.error || 'Failed to fetch playlists');
        }
        spotifyPlaylists = await response.json();
        renderSpotifyPlaylists();
        spotifyPlaylistsLoaded = true;

        await checkForActiveProcesses();

    } catch (error) {
        container.innerHTML = `<div class="playlist-placeholder">❌ Error: ${error.message}</div>`;
        showToast(`Error loading playlists: ${error.message}`, 'error');
    } finally {
        refreshBtn.disabled = false;
        refreshBtn.textContent = '🔄 Refresh';
    }
}

function renderSpotifyPlaylists() {
    const container = document.getElementById('spotify-playlist-container');
    if (spotifyPlaylists.length === 0) {
        container.innerHTML = `<div class="playlist-placeholder">No Spotify playlists found.</div>`;
        return;
    }

    container.innerHTML = spotifyPlaylists.map(p => {
        let statusClass = 'status-never-synced';
        if (p.sync_status.startsWith('Synced')) statusClass = 'status-synced';
        if (p.sync_status === 'Needs Sync') statusClass = 'status-needs-sync';

        // This HTML structure creates the interactive playlist cards
        return `
        <div class="playlist-card" data-playlist-id="${p.id}" onclick="togglePlaylistSelection(event)">
            <div class="playlist-card-main">
                <div class="playlist-card-content">
                    <div class="playlist-card-name">${escapeHtml(p.name)}</div>
                    <div class="playlist-card-info">
                        <span>${p.track_count} tracks</span> • 
                        <span class="playlist-card-status ${statusClass}">${p.sync_status}</span>
                    </div>
                    <div class="sync-progress-indicator" id="progress-${p.id}"></div>
                </div>
                <div class="playlist-card-actions">
                    <button id="action-btn-${p.id}" onclick="openPlaylistDetailsModal(event, '${p.id}')">Sync / Download</button>
                    <button id="progress-btn-${p.id}" class="view-progress-btn hidden" onclick="handleViewProgressClick(event, '${p.id}')">
                        View Progress
                    </button>
                </div>
            </div>
        </div>
        `;
    }).join('');
}

function handleViewProgressClick(event, playlistId) {
    event.stopPropagation(); // Prevent the card selection from toggling
    const process = activeDownloadProcesses[playlistId];

    if (process && process.modalElement) {
        // If a process is active, just show its modal
        console.log(`Re-opening active download modal for playlist ${playlistId}`);
        process.modalElement.style.display = 'flex';
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
            await fetch('/api/playlists/cleanup_batch', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ batch_id: process.batchId })
            });
            console.log(`✅ Server cleanup completed for batch: ${process.batchId}`);
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

function togglePlaylistSelection(event) {
    const card = event.currentTarget;
    const playlistId = card.dataset.playlistId;

    // Don't toggle if clicking the button
    if (event.target.tagName === 'BUTTON') return;
    
    const isSelected = !card.classList.contains('selected');
    card.classList.toggle('selected', isSelected);

    if (isSelected) {
        selectedPlaylists.add(playlistId);
    } else {
        selectedPlaylists.delete(playlistId);
    }
    updateSyncActionsUI();
}

function updateSyncActionsUI() {
    // If sequential sync is running, let the manager handle UI updates
    if (sequentialSyncManager && sequentialSyncManager.isRunning) {
        sequentialSyncManager.updateUI();
        return;
    }

    const selectionInfo = document.getElementById('selection-info');
    const startSyncBtn = document.getElementById('start-sync-btn');
    const count = selectedPlaylists.size;

    if (count === 0) {
        if (selectionInfo) selectionInfo.textContent = 'Select playlists to sync';
        if (startSyncBtn) startSyncBtn.disabled = true;
    } else {
        if (selectionInfo) selectionInfo.textContent = `${count} playlist${count > 1 ? 's' : ''} selected`;
        if (startSyncBtn) startSyncBtn.disabled = false;
    }
}

async function openPlaylistDetailsModal(event, playlistId) {
    event.stopPropagation();

    const playlist = spotifyPlaylists.find(p => p.id === playlistId);
    if (!playlist) return;

    showLoadingOverlay(`Loading playlist: ${playlist.name}...`);

    try {
        // --- CACHING LOGIC START ---
        if (playlistTrackCache[playlistId]) {
            console.log(`Cache HIT for playlist ${playlistId}. Using cached tracks.`);
            // Use the cached tracks instead of fetching
            const fullPlaylist = { ...playlist, tracks: playlistTrackCache[playlistId] };
            showPlaylistDetailsModal(fullPlaylist);
        } else {
            console.log(`Cache MISS for playlist ${playlistId}. Fetching from server...`);
            // Fetch from the server if not in cache
            const response = await fetch(`/api/spotify/playlist/${playlistId}`);
            const fullPlaylist = await response.json();
            if (fullPlaylist.error) throw new Error(fullPlaylist.error);

            // Store the fetched tracks in the cache
            playlistTrackCache[playlistId] = fullPlaylist.tracks;
            console.log(`Cached ${fullPlaylist.tracks.length} tracks for playlist ${playlistId}.`);

            showPlaylistDetailsModal(fullPlaylist);
        }
        // --- CACHING LOGIC END ---

    } catch (error) {
        showToast(`Error: ${error.message}`, 'error');
    } finally {
        hideLoadingOverlay();
    }
}

function showPlaylistDetailsModal(playlist) {
    // Create modal if it doesn't exist
    let modal = document.getElementById('playlist-details-modal');
    if (!modal) {
        modal = document.createElement('div');
        modal.id = 'playlist-details-modal';
        modal.className = 'modal-overlay';
        document.body.appendChild(modal);
    }
    
    // Check if there's a completed download missing tracks process for this playlist
    const activeProcess = activeDownloadProcesses[playlist.id];
    const hasCompletedProcess = activeProcess && activeProcess.status === 'complete';
    
    modal.innerHTML = `
        <div class="modal-container playlist-modal">
            <div class="playlist-modal-header">
                <div class="playlist-header-content">
                    <h2>${escapeHtml(playlist.name)}</h2>
                    <div class="playlist-quick-info">
                        <span class="playlist-track-count">${playlist.track_count} tracks</span>
                        <span class="playlist-owner">by ${escapeHtml(playlist.owner)}</span>
                    </div>
                    <!-- Sync status display (hidden by default, matches GUI) -->
                    <div class="playlist-modal-sync-status" id="modal-sync-status-${playlist.id}" style="display: none;">
                        <span class="sync-stat total-tracks">♪ <span id="modal-total-${playlist.id}">0</span></span>
                        <span class="sync-separator">/</span>
                        <span class="sync-stat matched-tracks">✓ <span id="modal-matched-${playlist.id}">0</span></span>
                        <span class="sync-separator">/</span>
                        <span class="sync-stat failed-tracks">✗ <span id="modal-failed-${playlist.id}">0</span></span>
                        <span class="sync-stat percentage">(<span id="modal-percentage-${playlist.id}">0</span>%)</span>
                    </div>
                </div>
                <span class="playlist-modal-close" onclick="closePlaylistDetailsModal()">&times;</span>
            </div>
            
            <div class="playlist-modal-body">
                ${playlist.description ? `<div class="playlist-description">${escapeHtml(playlist.description)}</div>` : ''}
                
                <div class="playlist-tracks-container">
                    <div class="playlist-tracks-list">
                        ${playlist.tracks.map((track, index) => `
                            <div class="playlist-track-item">
                                <span class="playlist-track-number">${index + 1}</span>
                                <div class="playlist-track-info">
                                    <div class="playlist-track-name">${escapeHtml(track.name)}</div>
                                    <div class="playlist-track-artists">${track.artists.join(', ')}</div>
                                </div>
                                <div class="playlist-track-duration">${formatDuration(track.duration_ms)}</div>
                            </div>
                        `).join('')}
                    </div>
                </div>
            </div>
            
            <div class="playlist-modal-footer">
                <button class="playlist-modal-btn playlist-modal-btn-secondary" onclick="closePlaylistDetailsModal()">Close</button>
                <button class="playlist-modal-btn playlist-modal-btn-tertiary" onclick="openDownloadMissingModal('${playlist.id}')">
                    ${hasCompletedProcess 
                        ? '📊 View Download Results' 
                        : '📥 Download Missing Tracks'}
                </button>
                <button class="playlist-modal-btn playlist-modal-btn-primary" onclick="startPlaylistSync('${playlist.id}')">Sync Playlist</button>
            </div>
        </div>
    `;

    modal.style.display = 'flex';
}

function closePlaylistDetailsModal() {
    const modal = document.getElementById('playlist-details-modal');
    if (modal) {
        modal.style.display = 'none';
    }
}

function formatDuration(ms) {
    const minutes = Math.floor(ms / 60000);
    const seconds = Math.floor((ms % 60000) / 1000);
    return `${minutes}:${seconds.toString().padStart(2, '0')}`;
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
        case 'artist_album':
            // Artist album context - show artist + album images
            const artistImage = artist?.image_url || artist?.images?.[0]?.url;
            const albumImage = album?.image_url || album?.images?.[0]?.url;

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
                        <div class="download-missing-modal-hero-subtitle">by ${escapeHtml(artist.name || 'Unknown Artist')}</div>
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

async function openDownloadMissingModal(playlistId) {
    showLoadingOverlay('Loading playlist...');

    // **NEW**: Check if a process is already active for this playlist
    if (activeDownloadProcesses[playlistId]) {
        console.log(`Modal for ${playlistId} already exists. Showing it.`);
        closePlaylistDetailsModal(); // Close playlist details modal even when reusing existing modal
        const process = activeDownloadProcesses[playlistId];
        if (process.modalElement) {
            // Show helpful message if it's a completed process
            if (process.status === 'complete') {
                showToast('Showing previous results. Close this modal to start a new analysis.', 'info');
            }
            process.modalElement.style.display = 'flex';
        }
        hideLoadingOverlay();
        return; // Don't create a new one
    }

    console.log(`📥 Opening Download Missing Tracks modal for playlist: ${playlistId}`);
    
    closePlaylistDetailsModal();
    const playlist = spotifyPlaylists.find(p => p.id === playlistId);
    if (!playlist) {
        showToast('Could not find playlist data.', 'error');
        return;
    }
    
    let tracks = playlistTrackCache[playlistId];
    if (!tracks) {
        try {
            const response = await fetch(`/api/spotify/playlist/${playlistId}`);
            const fullPlaylist = await response.json();
            if (fullPlaylist.error) throw new Error(fullPlaylist.error);
            tracks = fullPlaylist.tracks;
            playlistTrackCache[playlistId] = tracks;
        } catch (error) {
            showToast(`Failed to fetch tracks: ${error.message}`, 'error');
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
            
            <div class="download-missing-modal-body">
                <div class="download-dashboard-stats">
                    <div class="dashboard-stat stat-total">
                        <div class="dashboard-stat-number" id="stat-total-${playlistId}">${tracks.length}</div>
                        <div class="dashboard-stat-label">Total Tracks</div>
                    </div>
                    <div class="dashboard-stat stat-found">
                        <div class="dashboard-stat-number" id="stat-found-${playlistId}">-</div>
                        <div class="dashboard-stat-label">Found in Library</div>
                    </div>
                    <div class="dashboard-stat stat-missing">
                        <div class="dashboard-stat-number" id="stat-missing-${playlistId}">-</div>
                        <div class="dashboard-stat-label">Missing Tracks</div>
                    </div>
                    <div class="dashboard-stat stat-downloaded">
                        <div class="dashboard-stat-number" id="stat-downloaded-${playlistId}">0</div>
                        <div class="dashboard-stat-label">Downloaded</div>
                    </div>
                </div>
                
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
                                    <th>Duration</th>
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
                                        <td class="track-artist" title="${escapeHtml(track.artists.join(', '))}">${track.artists.join(', ')}</td>
                                        <td class="track-duration">${formatDuration(track.duration_ms)}</td>
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
                    <button class="download-control-btn primary" id="begin-analysis-btn-${playlistId}" onclick="startMissingTracksProcess('${playlistId}')">
                        Begin Analysis
                    </button>
                    <button class="download-control-btn danger" id="cancel-all-btn-${playlistId}" onclick="cancelAllOperations('${playlistId}')" style="display: none;">
                        Cancel All
                    </button>
                </div>
                <div class="modal-close-section">
                    <button class="download-control-btn secondary" onclick="closeDownloadMissingModal('${playlistId}')">Close</button>
                </div>
            </div>
        </div>
    `;

    modal.style.display = 'flex';
    hideLoadingOverlay();
}

async function openDownloadMissingModalForYouTube(virtualPlaylistId, playlistName, spotifyTracks) {
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
        tracks: spotifyTracks
    };

    // Generate hero section for YouTube playlist context
    const heroContext = {
        type: 'playlist',
        playlist: { name: playlistName, owner: 'YouTube' },
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
                <div class="download-dashboard-stats">
                    <div class="dashboard-stat stat-total">
                        <div class="dashboard-stat-number" id="stat-total-${virtualPlaylistId}">${spotifyTracks.length}</div>
                        <div class="dashboard-stat-label">Total Tracks</div>
                    </div>
                    <div class="dashboard-stat stat-found">
                        <div class="dashboard-stat-number" id="stat-found-${virtualPlaylistId}">-</div>
                        <div class="dashboard-stat-label">Found in Library</div>
                    </div>
                    <div class="dashboard-stat stat-missing">
                        <div class="dashboard-stat-number" id="stat-missing-${virtualPlaylistId}">-</div>
                        <div class="dashboard-stat-label">Missing Tracks</div>
                    </div>
                    <div class="dashboard-stat stat-downloaded">
                        <div class="dashboard-stat-number" id="stat-downloaded-${virtualPlaylistId}">0</div>
                        <div class="dashboard-stat-label">Downloaded</div>
                    </div>
                </div>
                
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
                    </div>
                    <div class="download-tracks-table-container">
                        <table class="download-tracks-table">
                            <thead>
                                <tr>
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
                                        <td class="track-number">${index + 1}</td>
                                        <td class="track-name" title="${escapeHtml(track.name)}">${escapeHtml(track.name)}</td>
                                        <td class="track-artist" title="${escapeHtml(track.artists.join(', '))}">${track.artists.join(', ')}</td>
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
                    <div class="force-download-toggle-container" style="margin-bottom: 0px;">
                        <label class="force-download-toggle">
                            <input type="checkbox" id="force-download-all-${virtualPlaylistId}">
                            <span>Force Download All</span>
                        </label>
                    </div>
                    <button class="download-control-btn primary" id="begin-analysis-btn-${virtualPlaylistId}" onclick="startMissingTracksProcess('${virtualPlaylistId}')">
                        Begin Analysis
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

    modal.style.display = 'flex';
    hideLoadingOverlay();
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

        // Automatic cleanup and server operations after successful downloads
        await handlePostDownloadAutomation(playlistId, process);

        cleanupDownloadProcess(playlistId);
    }
}

async function openDownloadMissingWishlistModal() {
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
        return; // Don't create a new one
    }

    console.log(`📥 Opening Download Missing Tracks modal for wishlist`);
    
    // Fetch actual wishlist tracks from the server
    let tracks;
    try {
        const response = await fetch('/api/wishlist/count');
        const countData = await response.json();
        if (countData.count === 0) {
            showToast('Wishlist is empty. No tracks to download.', 'info');
            return;
        }
        
        // Fetch the actual wishlist tracks for display
        const tracksResponse = await fetch('/api/wishlist/tracks');
        if (!tracksResponse.ok) {
            throw new Error('Failed to fetch wishlist tracks');
        }
        const tracksData = await tracksResponse.json();
        tracks = tracksData.tracks || [];
        
    } catch (error) {
        showToast(`Failed to fetch wishlist data: ${error.message}`, 'error');
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
                <div class="download-dashboard-stats">
                    <div class="dashboard-stat stat-total">
                        <div class="dashboard-stat-number" id="stat-total-${playlistId}">${tracks.length}</div>
                        <div class="dashboard-stat-label">Total Tracks</div>
                    </div>
                    <div class="dashboard-stat stat-found">
                        <div class="dashboard-stat-number" id="stat-found-${playlistId}">-</div>
                        <div class="dashboard-stat-label">Found in Library</div>
                    </div>
                    <div class="dashboard-stat stat-missing">
                        <div class="dashboard-stat-number" id="stat-missing-${playlistId}">-</div>
                        <div class="dashboard-stat-label">Missing Tracks</div>
                    </div>
                    <div class="dashboard-stat stat-downloaded">
                        <div class="dashboard-stat-number" id="stat-downloaded-${playlistId}">0</div>
                        <div class="dashboard-stat-label">Downloaded</div>
                    </div>
                </div>
                
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
                                        <td class="track-artist" title="${escapeHtml(formatArtists(track.artists))}">${formatArtists(track.artists)}</td>
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

        const response = await fetch('/api/wishlist/download_missing', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                force_download_all: forceDownloadAll
            })
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

        const response = await fetch(`/api/playlists/${playlistId}/start-missing-process`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                tracks: process.tracks,
                playlist_name: process.playlist.name,
                force_download_all: forceDownloadAll
            })
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
        startModalDownloadPolling(playlistId);
    } catch (error) {
        showToast(`Failed to start process: ${error.message}`, 'error');
        process.status = 'cancelled';
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
    if (globalDownloadStatusPoller) {
        console.debug('🔄 [Global Polling] Already running, skipping start');
        return; // Prevent duplicate pollers
    }
    
    console.log('🔄 [Global Polling] Starting batched download status polling');
    
    globalDownloadStatusPoller = setInterval(async () => {
        // Get all active processes that need polling
        const activeBatchIds = [];
        const batchToPlaylistMap = {};
        let hasOpenWishlistModal = false;
        
        Object.entries(activeDownloadProcesses).forEach(([playlistId, process]) => {
            if (process.batchId && process.status === 'running') {
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
            if (process.batchId && process.status === 'running') {
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
        }
    } else if (data.phase === 'downloading' || data.phase === 'complete' || data.phase === 'error') {
        console.debug(`📊 [Status Update] Processing ${data.phase} phase for playlistId: ${playlistId}, tasks: ${(data.tasks || []).length}`);
        
        if (document.getElementById(`analysis-progress-fill-${playlistId}`).style.width !== '100%') {
             document.getElementById(`analysis-progress-fill-${playlistId}`).style.width = '100%';
             document.getElementById(`analysis-progress-text-${playlistId}`).textContent = 'Analysis complete!';
             if(data.analysis_results) {
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
        
        // Verify modal exists before processing tasks
        const modal = document.getElementById(`download-missing-modal-${playlistId}`);
        if (!modal) {
            console.error(`❌ [Status Update] Modal not found: download-missing-modal-${playlistId}`);
            return;
        }

        (data.tasks || []).forEach(task => {
            const row = document.querySelector(`#download-missing-modal-${playlistId} tr[data-track-index="${task.track_index}"]`);
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
                    case 'failed': statusText = '❌ Failed'; failedOrCancelledCount++; break;
                    case 'cancelled': statusText = '🚫 Cancelled'; failedOrCancelledCount++; break;
                    default: statusText = `⚪ ${task.status}`; break;
                }
            }
            
            if(statusEl) {
                statusEl.textContent = statusText;
                console.debug(`✅ [Status Update] Updated track ${task.track_index} to: ${statusText}${isV2Task ? ' (V2)' : ''}`);
            } else {
                console.warn(`❌ [Status Update] Status element not found: download-${playlistId}-${task.track_index}`);
            }
            
            // V2 SYSTEM: Smart button management with persistent state awareness
            if (actionsEl && !['completed', 'failed', 'cancelled', 'post_processing'].includes(task.status)) {
                // Check if we're in a cancelling state
                if (isV2Task && uiState === 'cancelling') {
                    actionsEl.innerHTML = '<span style="color: #666;">Cancelling...</span>';
                } else {
                    // Create V2 cancel button for all active tasks
                    const onclickHandler = isV2Task ? 'cancelTrackDownloadV2' : 'cancelTrackDownload';
                    actionsEl.innerHTML = `<button class="cancel-track-btn" title="Cancel this download" onclick="${onclickHandler}('${playlistId}', ${task.track_index})">×</button>`;
                }
            } else if (actionsEl && ['completed', 'failed', 'cancelled', 'post_processing'].includes(task.status)) {
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
        
        const totalFinished = completedCount + failedOrCancelledCount;
        const progressPercent = missingCount > 0 ? (totalFinished / missingCount) * 100 : 0;
        document.getElementById(`download-progress-fill-${playlistId}`).style.width = `${progressPercent}%`;
        document.getElementById(`download-progress-text-${playlistId}`).textContent = `${completedCount}/${missingCount} completed (${progressPercent.toFixed(0)}%)`;
        document.getElementById(`stat-downloaded-${playlistId}`).textContent = completedCount;

        // CLIENT-SIDE COMPLETION: If all tracks are finished (completed or failed), complete the modal
        const allTracksFinished = totalFinished >= missingCount && missingCount > 0;
        if (allTracksFinished && process.status !== 'complete') {
            console.log(`🎯 [Client Completion] All ${totalFinished}/${missingCount} tracks finished - completing modal locally`);

            // Hide cancel button and mark as complete
            document.getElementById(`cancel-all-btn-${playlistId}`).style.display = 'none';
            process.status = 'complete';
            updatePlaylistCardUI(playlistId);

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

            // Show completion message
            const completionMessage = `Download complete! ${completedCount} downloaded, ${failedOrCancelledCount} failed.`;
            showToast(completionMessage, 'success');

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
                process.status = 'cancelled';
                
                // Reset YouTube playlist phase to 'discovered' if this is a YouTube playlist on cancel
                if (playlistId.startsWith('youtube_')) {
                    const urlHash = playlistId.replace('youtube_', '');
                    updateYouTubeCardPhase(urlHash, 'discovered');
                }
                
                showToast(`Process cancelled for ${process.playlist.name}.`, 'info');
            } else if (data.phase === 'error') {
                process.status = 'complete'; // Treat as complete to allow cleanup
                updatePlaylistCardUI(playlistId); // Update card to show ready for review
                
                // Reset YouTube playlist phase to 'discovered' if this is a YouTube playlist on error
                if (playlistId.startsWith('youtube_')) {
                    const urlHash = playlistId.replace('youtube_', '');
                    updateYouTubeCardPhase(urlHash, 'discovered');
                }
                
                showToast(`Process for ${process.playlist.name} failed!`, 'error');
            } else {
                process.status = 'complete';
                updatePlaylistCardUI(playlistId); // Update card to show ready for review
                
                // Update YouTube playlist phase to 'download_complete' if this is a YouTube playlist
                if (playlistId.startsWith('youtube_')) {
                    const urlHash = playlistId.replace('youtube_', '');
                    updateYouTubeCardPhase(urlHash, 'download_complete');
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
                
                // Handle background wishlist processing completion specially
                if (isBackgroundWishlist) {
                    console.log(`🎉 Background wishlist processing complete: ${completedCount} downloaded, ${failedOrCancelledCount} failed`);
                    
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
                    completionMessage = `Download process complete! Downloaded: ${completedCount}, Failed/Cancelled: ${failedOrCancelledCount}.`;
                    
                    if (summary.tracks_added > 0) {
                        completionMessage += ` Added ${summary.tracks_added} failed track${summary.tracks_added !== 1 ? 's' : ''} to wishlist for automatic retry.`;
                    } else if (summary.total_failed > 0) {
                        completionMessage += ` ${summary.total_failed} track${summary.total_failed !== 1 ? 's' : ''} could not be added to wishlist.`;
                        messageType = 'warning';
                    }
                }
                
                showToast(completionMessage, messageType);
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
        const allDownloads = {...(downloadData.active || {}), ...(downloadData.finished || {})};
        
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
                const downloadTitle = downloadInfo.filename ? downloadInfo.filename.split(/[\\/]/).pop() : '';
                
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

    const row = document.querySelector(`#download-missing-modal-${playlistId} tr[data-track-index="${trackIndex}"]`);
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

    const row = document.querySelector(`#download-missing-modal-${playlistId} tr[data-track-index="${trackIndex}"]`);
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

    // Ensure we have the full track list before starting
    let tracks = playlistTrackCache[playlistId];
    if (!tracks) {
        const trackFetchStart = Date.now();
        console.log(`🔄 [${new Date().toTimeString().split(' ')[0]}] Cache miss - fetching tracks for playlist ${playlistId}`);
        try {
            const response = await fetch(`/api/spotify/playlist/${playlistId}`);
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
                tracks: tracks // Send the full track list
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
        const modal = document.getElementById('playlist-details-modal');
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

    // Start a new poller that checks every 2 seconds
    console.log(`🔄 Starting sync polling for playlist: ${playlistId}`);
    activeSyncPollers[playlistId] = setInterval(async () => {
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
                closePlaylistDetailsModal(); // Close modal on completion/error
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
    updateRefreshButtonState();
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
            showToast(`Sync complete for "${card.querySelector('.playlist-card-name').textContent}"`, 'success');
        } else {
            statusEl.textContent = `Sync Failed`;
            statusEl.className = 'playlist-card-status status-needs-sync'; // Or a new error class
            showToast(`Sync failed: ${finalState.error || 'Unknown error'}`, 'error');
        }
    }
}

// Update the modal's sync progress display (matches GUI functionality)
function updateModalSyncProgress(playlistId, progress) {
    const modal = document.getElementById('playlist-details-modal');
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


// Download tracking state management - matching GUI functionality
let activeDownloads = {};
let finishedDownloads = {};
let downloadStatusInterval = null;
let isDownloadPollingActive = false;

async function loadDownloadsData() {
    // Downloads page loads search results dynamically
    console.log('Downloads page loaded');

    // Event listeners are already set up in initializeSearch() - don't duplicate them
    const clearButton = document.querySelector('.controls-panel__clear-btn');
    
    if (clearButton) {
        clearButton.addEventListener('click', clearFinishedDownloads);
    }
    
    // Start sophisticated polling system (1-second interval like GUI)
    startDownloadPolling();
    
    // Initialize tab management
    initializeDownloadTabs();
}

function startDownloadPolling() {
    if (isDownloadPollingActive) return;
    
    console.log('Starting download status polling (1-second interval)');
    isDownloadPollingActive = true;
    
    // Initial call
    updateDownloadQueues();
    
    // Start 1-second polling (matching GUI's 1000ms timer)
    downloadStatusInterval = setInterval(updateDownloadQueues, 1000);
}

function stopDownloadPolling() {
    if (downloadStatusInterval) {
        clearInterval(downloadStatusInterval);
        downloadStatusInterval = null;
    }
    isDownloadPollingActive = false;
    console.log('Stopped download status polling');
}

async function updateDownloadQueues() {
    try {
        const response = await fetch('/api/downloads/status');
        const data = await response.json();

        if (data.error) {
            console.error("Error fetching download status:", data.error);
            return;
        }

        const newActive = {};
        const newFinished = {};
        
        // Terminal states matching GUI logic
        const terminalStates = ['Completed', 'Succeeded', 'Cancelled', 'Canceled', 'Failed', 'Errored'];

        // Process transfers exactly like GUI
        data.transfers.forEach(item => {
            const isTerminal = terminalStates.some(state => 
                item.state && item.state.includes(state)
            );
            
            if (isTerminal) {
                newFinished[item.id] = item;
            } else {
                newActive[item.id] = item;
            }
        });

        // Update global state
        activeDownloads = newActive;
        finishedDownloads = newFinished;
        
        // Render both queues
        renderQueue('active-queue', activeDownloads, true);
        renderQueue('finished-queue', finishedDownloads, false);
        
        // Update tab counts
        updateTabCounts();
        
        // Update stats in the side panel
        updateDownloadStats();

    } catch (error) {
        // Only log errors occasionally to avoid console spam
        if (Math.random() < 0.1) {
            console.error("Failed to update download queues:", error);
        }
    }
}

function renderQueue(containerId, downloads, isActiveQueue) {
    const container = document.getElementById(containerId);
    if (!container) return;

    const downloadIds = Object.keys(downloads);

    if (downloadIds.length === 0) {
        container.innerHTML = `<div class="download-queue__empty-message">${isActiveQueue ? 'No active downloads.' : 'No finished downloads.'}</div>`;
        return;
    }

    let html = '';
    for (const id of downloadIds) {
        const item = downloads[id];
        const title = item.filename ? item.filename.split(/[\\/]/).pop() : 'Unknown File';
        const progress = item.percentComplete || 0;
        const bytesTransferred = item.bytesTransferred || 0;
        const totalBytes = item.size || 0;
        const speed = item.averageSpeed || 0;
        
        // Format file size
        const formatSize = (bytes) => {
            if (!bytes) return 'Unknown size';
            const units = ['B', 'KB', 'MB', 'GB'];
            let size = bytes;
            let unitIndex = 0;
            while (size >= 1024 && unitIndex < units.length - 1) {
                size /= 1024;
                unitIndex++;
            }
            return `${size.toFixed(1)} ${units[unitIndex]}`;
        };
        
        // Format speed
        const formatSpeed = (bytesPerSecond) => {
            if (!bytesPerSecond || bytesPerSecond <= 0) return '';
            return `${formatSize(bytesPerSecond)}/s`;
        };
        
        let actionButtonHTML = '';
        if (isActiveQueue) {
            // Active items get progress bar and cancel button
            actionButtonHTML = `
                <div class="download-item__progress-container">
                    <div class="progress-bar">
                        <div class="progress-fill" style="width: ${progress}%;"></div>
                    </div>
                    <div class="progress-text">
                        ${item.state} - ${progress.toFixed(1)}%
                        ${speed > 0 ? `• ${formatSpeed(speed)}` : ''}
                        ${totalBytes > 0 ? `• ${formatSize(bytesTransferred)} / ${formatSize(totalBytes)}` : ''}
                    </div>
                </div>
                <button class="download-item__cancel-btn" onclick="cancelDownloadItem('${item.id}', '${item.username}')">✕ Cancel</button>
            `;
        } else {
            // Finished items get status and open button
            let statusClass = '';
            if (item.state.includes('Cancelled')) statusClass = 'status--cancelled';
            else if (item.state.includes('Failed') || item.state.includes('Errored')) statusClass = 'status--failed';
            else if (item.state.includes('Completed') || item.state.includes('Succeeded')) statusClass = 'status--completed';
            
            actionButtonHTML = `
                <div class="download-item__status-container">
                    <span class="download-item__status-text ${statusClass}">${item.state}</span>
                </div>
                <button class="download-item__open-btn" title="Cannot open folder from web browser" disabled>📁 Open</button>
            `;
        }
        
        html += `
            <div class="download-item" data-id="${item.id}">
                <div class="download-item__header">
                    <div class="download-item__title" title="${title}">${title}</div>
                    <div class="download-item__uploader" title="from ${item.username}">from ${item.username}</div>
                </div>
                <div class="download-item__content">
                    ${actionButtonHTML}
                </div>
            </div>
        `;
    }
    container.innerHTML = html;
}

function updateTabCounts() {
    const activeCount = Object.keys(activeDownloads).length;
    const finishedCount = Object.keys(finishedDownloads).length;
    
    const activeTabBtn = document.querySelector('.tab-btn[data-tab="active-queue"]');
    const finishedTabBtn = document.querySelector('.tab-btn[data-tab="finished-queue"]');
    
    if (activeTabBtn) activeTabBtn.textContent = `Download Queue (${activeCount})`;
    if (finishedTabBtn) finishedTabBtn.textContent = `Finished (${finishedCount})`;
}

function updateDownloadStats() {
    const activeCount = Object.keys(activeDownloads).length;
    const finishedCount = Object.keys(finishedDownloads).length;
    
    const activeLabel = document.getElementById('active-downloads-label');
    const finishedLabel = document.getElementById('finished-downloads-label');
    
    if (activeLabel) activeLabel.textContent = `• Active Downloads: ${activeCount}`;
    if (finishedLabel) finishedLabel.textContent = `• Finished Downloads: ${finishedCount}`;
}

function initializeDownloadTabs() {
    const tabButtons = document.querySelectorAll('.tab-btn');
    tabButtons.forEach(btn => {
        btn.addEventListener('click', () => switchDownloadTab(btn));
    });
}

function switchDownloadTab(button) {
    const targetTabId = button.getAttribute('data-tab');
    
    // Update buttons
    document.querySelectorAll('.tab-btn').forEach(btn => btn.classList.remove('active'));
    button.classList.add('active');

    // Update content panes
    document.querySelectorAll('.download-queue').forEach(queue => queue.classList.remove('active'));
    const targetQueue = document.getElementById(targetTabId);
    if (targetQueue) targetQueue.classList.add('active');
}

async function cancelDownloadItem(downloadId, username) {
    try {
        const response = await fetch('/api/downloads/cancel', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ download_id: downloadId, username: username })
        });
        const result = await response.json();
        
        if (result.success) {
            showToast('Download cancelled', 'success');
        } else {
            showToast(`Failed to cancel: ${result.error}`, 'error');
        }
    } catch (error) {
        console.error('Error cancelling download:', error);
        showToast('Error sending cancel request', 'error');
    }
}

async function clearFinishedDownloads() {
    const finishedCount = Object.keys(finishedDownloads).length;
    if (finishedCount === 0) {
        showToast('No finished downloads to clear', 'error');
        return;
    }
    
    try {
        const response = await fetch('/api/downloads/clear-finished', {
            method: 'POST'
        });
        const result = await response.json();
        
        if (result.success) {
            showToast('Finished downloads cleared', 'success');
        } else {
            showToast(`Failed to clear: ${result.error}`, 'error');
        }
    } catch (error) {
        console.error('Error clearing finished downloads:', error);
        showToast('Error sending clear request', 'error');
    }
}

// REPLACE the old performDownloadsSearch function with this new one.
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
                result.tracks.forEach((track, trackIndex) => {
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
        
        // Check for unsupported formats before streaming
        if (result.filename) {
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
        
        // Check for unsupported formats before streaming
        if (trackData.filename && !isAudioFormatSupported(trackData.filename)) {
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

// Toast deduplication cache
let recentToasts = new Map();

function showToast(message, type = 'success') {
    const container = document.getElementById('toast-container');
    
    // Create a unique key for this toast
    const toastKey = `${type}:${message}`;
    const now = Date.now();
    
    // Check if we've shown this exact toast recently (within 5 seconds)
    if (recentToasts.has(toastKey)) {
        const lastShown = recentToasts.get(toastKey);
        if (now - lastShown < 5000) {
            console.log(`🚫 Suppressing duplicate toast: "${message}"`);
            return; // Don't show duplicate
        }
    }
    
    // Record this toast
    recentToasts.set(toastKey, now);
    
    // Clean up old entries (older than 10 seconds)
    for (const [key, timestamp] of recentToasts.entries()) {
        if (now - timestamp > 10000) {
            recentToasts.delete(key);
        }
    }
    
    const toast = document.createElement('div');
    toast.className = `toast ${type}`;
    toast.textContent = message;
    
    container.appendChild(toast);
    
    // Auto-remove after 3 seconds
    setTimeout(() => {
        if (container.contains(toast)) {
            container.removeChild(toast);
        }
    }, 3000);
}

function escapeHtml(text) {
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}

function formatArtists(artists) {
    if (!artists || !Array.isArray(artists)) {
        return 'Unknown Artist';
    }
    
    // Handle both string arrays and object arrays with 'name' property
    const artistNames = artists.map(artist => {
        if (typeof artist === 'string') {
            return artist;
        } else if (artist && typeof artist === 'object' && artist.name) {
            return artist.name;
        } else {
            return 'Unknown Artist';
        }
    });
    
    return artistNames.join(', ') || 'Unknown Artist';
}

async function showVersionInfo() {
    try {
        console.log('Fetching version info...');
        
        // Fetch version data from API
        const response = await fetch('/api/version-info');
        if (!response.ok) {
            throw new Error('Failed to fetch version info');
        }
        
        const versionData = await response.json();
        console.log('Version data received:', versionData);
        
        // Populate modal content
        populateVersionModal(versionData);
        
        // Show modal
        const modalOverlay = document.getElementById('version-modal-overlay');
        modalOverlay.classList.remove('hidden');
        
        console.log('Version modal opened');
        
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

function populateVersionModal(versionData) {
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
    background: rgba(29, 185, 84, 0.1);
    color: #1ed760;
    border: 1px solid rgba(29, 185, 84, 0.3);
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
    color: #1ed760;
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

// Make functions available globally for onclick handlers
window.openMatchingModal = openMatchingModal;
window.closeMatchingModal = closeMatchingModal;
window.selectArtist = selectArtist;
window.selectAlbum = selectAlbum;
window.navigateToPage = navigateToPage;
window.openKofi = openKofi;
window.copyAddress = copyAddress;
window.retryLastSearch = retryLastSearch;
window.showVersionInfo = showVersionInfo;
window.closeVersionModal = closeVersionModal;
window.testConnection = testConnection;
window.autoDetectPlex = autoDetectPlex;
window.autoDetectJellyfin = autoDetectJellyfin;
window.autoDetectSlskd = autoDetectSlskd;
window.toggleServer = toggleServer;
window.authenticateSpotify = authenticateSpotify;
window.authenticateTidal = authenticateTidal;
window.browsePath = browsePath;
window.selectResult = selectResult;
window.startStream = startStream;
window.streamTrack = streamTrack;
window.streamAlbumTrack = streamAlbumTrack;
window.startDownload = startDownload;
window.downloadTrack = downloadTrack;
window.downloadAlbum = downloadAlbum;
window.toggleAlbumExpansion = toggleAlbumExpansion;
window.downloadAlbumTrack = downloadAlbumTrack;
window.switchDownloadTab = switchDownloadTab;
window.cancelDownloadItem = cancelDownloadItem;
window.clearFinishedDownloads = clearFinishedDownloads;

window.matchedDownloadTrack = matchedDownloadTrack;
window.matchedDownloadAlbum = matchedDownloadAlbum;
window.matchedDownloadAlbumTrack = matchedDownloadAlbumTrack;

/**
 * Handle automatic post-download operations: cleanup → scan → database update
 * This replicates the GUI's automatic functionality after download modal completion
 */
async function handlePostDownloadAutomation(playlistId, process) {
    try {
        // Check if we have successful downloads that warrant automation
        const successfulDownloads = getSuccessfulDownloadCount(process);

        if (successfulDownloads === 0) {
            console.log(`🔄 [AUTO] No successful downloads for ${playlistId} - skipping automation`);
            return;
        }

        console.log(`🔄 [AUTO] Starting automatic post-download operations for ${playlistId} (${successfulDownloads} successful downloads)`);

        // Step 1: Clear completed downloads from slskd
        console.log(`🗑️ [AUTO] Step 1: Clearing completed downloads...`);
        showToast('🗑️ Clearing completed downloads...', 'info', 3000);

        try {
            const clearResponse = await fetch('/api/downloads/clear-finished', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' }
            });

            if (clearResponse.ok) {
                console.log(`✅ [AUTO] Step 1 complete: Downloads cleared`);
            } else {
                console.warn(`⚠️ [AUTO] Step 1 warning: Clear downloads failed, continuing anyway`);
            }
        } catch (error) {
            console.warn(`⚠️ [AUTO] Step 1 error: ${error.message}, continuing anyway`);
        }

        // Step 2: Request media server scan
        console.log(`📡 [AUTO] Step 2: Requesting media server scan...`);
        showToast('📡 Scanning media server library...', 'info', 5000);

        try {
            const scanResponse = await fetch('/api/scan/request', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    reason: `Download modal completed for ${playlistId} (${successfulDownloads} tracks)`,
                    auto_database_update: true  // This will trigger step 3 automatically after scan completes
                })
            });

            const scanResult = await scanResponse.json();

            if (scanResponse.ok && scanResult.success) {
                console.log(`✅ [AUTO] Step 2 complete: Media scan requested`);
                console.log(`🔄 [AUTO] Scan info:`, scanResult.scan_info);

                // Show success toast with scan details
                if (scanResult.scan_info.status === 'scheduled') {
                    showToast(`📡 Media scan scheduled (${scanResult.scan_info.delay_seconds}s delay)`, 'success', 5000);
                } else {
                    showToast('📡 Media scan requested successfully', 'success', 3000);
                }

                // Database update will be triggered automatically by the scan completion callback
                if (scanResult.auto_database_update) {
                    console.log(`🔄 [AUTO] Step 3 will run automatically after scan completes`);
                    showToast('🔄 Database update will follow automatically', 'info', 3000);
                }
            } else {
                console.error(`❌ [AUTO] Step 2 failed: ${scanResult.error || 'Unknown scan error'}`);
                showToast('❌ Media scan failed', 'error', 5000);
            }
        } catch (error) {
            console.error(`❌ [AUTO] Step 2 error: ${error.message}`);
            showToast('❌ Media scan request failed', 'error', 5000);
        }

        console.log(`🏁 [AUTO] Automatic post-download operations initiated for ${playlistId}`);

    } catch (error) {
        console.error(`❌ [AUTO] Error in post-download automation: ${error.message}`);
        showToast('❌ Automatic operations failed', 'error', 5000);
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

/**
 * Open the Add to Wishlist modal for an album/EP/single
 * @param {Object} album - Album object with id, name, image_url, etc.
 * @param {Object} artist - Artist object with id, name, image_url
 * @param {Array} tracks - Array of track objects
 * @param {string} albumType - Type of release (album, EP, single)
 */
async function openAddToWishlistModal(album, artist, tracks, albumType) {
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
        const heroContent = generateWishlistModalHeroSection(album, artist, tracks, albumType);
        const heroContainer = document.getElementById('add-to-wishlist-modal-hero');
        if (heroContainer) {
            heroContainer.innerHTML = heroContent;
        }

        // Generate and populate track list
        const trackListHTML = generateWishlistTrackList(tracks);
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
function generateWishlistModalHeroSection(album, artist, tracks, albumType) {
    const artistImage = artist.image_url || '';
    const albumImage = album.image_url || '';
    const trackCount = tracks.length;

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
                    <span class="add-to-wishlist-modal-hero-detail">${trackCount} track${trackCount !== 1 ? 's' : ''}</span>
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
function generateWishlistTrackList(tracks) {
    if (!tracks || tracks.length === 0) {
        return '<div style="text-align: center; padding: 40px; color: rgba(255, 255, 255, 0.6);">No tracks found</div>';
    }

    return tracks.map((track, index) => {
        const trackNumber = track.track_number || (index + 1);
        const trackName = escapeHtml(track.name || 'Unknown Track');
        const artistsString = formatArtists(track.artists) || 'Unknown Artist';
        const duration = formatDuration(track.duration_ms);

        return `
            <div class="wishlist-track-item">
                <div class="wishlist-track-number">${trackNumber}</div>
                <div class="wishlist-track-info">
                    <div class="wishlist-track-name">${trackName}</div>
                    <div class="wishlist-track-artists">${artistsString}</div>
                </div>
                <div class="wishlist-track-duration">${duration}</div>
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

                console.log(`🔄 Adding track with formatted artists:`, formattedTrack.name, formattedTrack.artists);

                const response = await fetch('/api/add-album-to-wishlist', {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json'
                    },
                    body: JSON.stringify({
                        track: formattedTrack,
                        artist: artist,
                        album: album,
                        source_type: 'album',
                        source_context: {
                            album_name: album.name,
                            artist_name: artist.name,
                            album_type: albumType
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

// Download Missing Tracks Modal functions
window.openDownloadMissingModal = openDownloadMissingModal;
window.closeDownloadMissingModal = closeDownloadMissingModal;
window.startMissingTracksProcess = startMissingTracksProcess;
window.cancelAllOperations = cancelAllOperations;
window.cancelTrackDownload = cancelTrackDownload; // Legacy system
window.cancelTrackDownloadV2 = cancelTrackDownloadV2; // NEW V2 system
window.handleViewProgressClick = handleViewProgressClick;

// Wishlist Modal functions (existing)
window.openDownloadMissingWishlistModal = openDownloadMissingWishlistModal;
window.startWishlistMissingTracksProcess = startWishlistMissingTracksProcess;
window.handleWishlistButtonClick = handleWishlistButtonClick;

// Add to Wishlist Modal functions (new)
window.openAddToWishlistModal = openAddToWishlistModal;
window.closeAddToWishlistModal = closeAddToWishlistModal;
window.handleAddToWishlist = handleAddToWishlist;

// Helper functions
window.escapeHtml = escapeHtml;
window.formatArtists = formatArtists;

// Artist Download Management functions
window.closeArtistDownloadModal = closeArtistDownloadModal;
window.openArtistDownloadProcess = openArtistDownloadProcess;
window.bulkCompleteArtistDownloads = bulkCompleteArtistDownloads;
window.refreshAllArtistDownloadStatuses = refreshAllArtistDownloadStatuses;


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
        modalTitle.textContent = 'Match Album Download to Spotify';
        artistStageTitle.textContent = 'Step 1: Select the correct Artist';
        document.getElementById('album-selection-stage').style.display = 'block';
    } else {
        modalTitle.textContent = 'Match Download to Spotify';
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
    const originalText = confirmBtn.textContent; // FIX: Declare outside try block

    try {
        console.log('🎯 Confirming match with:', {
            artist: currentMatchingData.selectedArtist.name,
            album: currentMatchingData.selectedAlbum?.name
        });

        confirmBtn.disabled = true;
        confirmBtn.textContent = 'Starting...';

        // --- THIS IS THE CRITICAL FIX ---
        // Determine the correct data to send. For albums, we send the full albumResult
        // which contains the complete list of tracks.
        const downloadPayload = currentMatchingData.isAlbumDownload
            ? currentMatchingData.albumResult
            : currentMatchingData.searchResult;
        // --- END OF FIX ---

        const response = await fetch('/api/download/matched', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                search_result: downloadPayload, // Send the correct payload
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
        const trackRows = document.querySelectorAll(`#download-missing-modal-${playlistId} tr[data-track-index]`);
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

async function loadDashboardData() {
    // Attach event listeners for the DB updater tool
    const updateButton = document.getElementById('db-update-button');
    if (updateButton) {
        updateButton.addEventListener('click', handleDbUpdateButtonClick);
    }

    // Attach event listeners for the metadata updater tool
    const metadataButton = document.getElementById('metadata-update-button');
    if (metadataButton) {
        metadataButton.addEventListener('click', handleMetadataUpdateButtonClick);
    }
    
    // Check active media server and hide metadata updater if not Plex
    await checkAndHideMetadataUpdaterForNonPlex();
    
    // Check for ongoing metadata update and restore state
    await checkAndRestoreMetadataUpdateState();

    // Attach event listener for the wishlist button
    const wishlistButton = document.getElementById('wishlist-button');
    if (wishlistButton) {
        wishlistButton.addEventListener('click', handleWishlistButtonClick);
    }

    // Initial load of stats
    await fetchAndUpdateDbStats();
    
    // Start periodic refresh of stats (every 30 seconds)
    stopDbStatsPolling(); // Ensure no duplicates
    dbStatsInterval = setInterval(fetchAndUpdateDbStats, 30000);

    // Initial load of wishlist count
    await updateWishlistCount();
    
    // Start periodic refresh of wishlist count (every 30 seconds, matching GUI behavior)
    stopWishlistCountPolling(); // Ensure no duplicates
    wishlistCountInterval = setInterval(updateWishlistCount, 30000);
    
    // Initial load of service status and system statistics
    await fetchAndUpdateServiceStatus();
    await fetchAndUpdateSystemStats();
    
    // Start periodic refresh of service status and system stats (every 10 seconds)
    setInterval(fetchAndUpdateServiceStatus, 10000);
    setInterval(fetchAndUpdateSystemStats, 10000);
    
    // Initial load of activity feed
    await fetchAndUpdateActivityFeed();
    
    // Start periodic refresh of activity feed (every 5 seconds for responsiveness)
    setInterval(fetchAndUpdateActivityFeed, 5000);
    
    // Start periodic toast checking (every 3 seconds)
    setInterval(checkForActivityToasts, 3000);

    // Also check the status of any ongoing update when the page loads
    await checkAndUpdateDbProgress();
    
    // Check for any active download processes that need rehydration
    await checkForActiveProcesses();
    
    // Automatic wishlist processing now runs server-side
}

// --- Data Fetching and UI Updates ---

async function fetchAndUpdateDbStats() {
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
    // You can expand this later to update the main stat cards
    // For now, we focus on the updater tool itself.
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
    try {
        const response = await fetch('/api/wishlist/count');
        if (!response.ok) return;
        
        const data = await response.json();
        const count = data.count || 0;
        
        const wishlistButton = document.getElementById('wishlist-button');
        if (wishlistButton) {
            wishlistButton.textContent = `🎵 Wishlist (${count})`;
            
            // Update button styling based on count (matching GUI behavior)
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
            progressBar.style.backgroundColor = '#1db954'; // Green for normal
        }
        
        if (state.status === 'finished' || state.status === 'error') {
             // Final stats refresh after completion/error
            setTimeout(fetchAndUpdateDbStats, 500);
        }
    }
}

// ===================================================================
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
        // No need to fetch data - we already have all tracks from initial load (like sync.py)
        console.log(`🎵 Using pre-loaded Tidal playlist data for: ${state.playlist.name}`);
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
                        tidalPlaylistStates[playlistId] = {...tidalPlaylistStates[playlistId], ...state};
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
        const playlistName = `[Tidal] ${state.playlist.name}`;
        
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

        (downloadData.tasks || []).forEach(task => {
            const row = document.querySelector(`#download-missing-modal-${playlistId} tr[data-track-index="${task.track_index}"]`);
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
                case 'failed': statusText = '❌ Failed'; failedOrCancelledCount++; break;
                case 'cancelled': statusText = '🚫 Cancelled'; failedOrCancelledCount++; break;
                default: statusText = `⚪ ${task.status}`; break;
            }
            
            if (statusEl) statusEl.textContent = statusText;
            if (actionsEl) actionsEl.innerHTML = '-'; // Remove action buttons for completed tasks
        });

        // Update download progress to final state
        const totalFinished = completedCount + failedOrCancelledCount;
        const missingCount = missingTracks.length;
        const progressPercent = missingCount > 0 ? (totalFinished / missingCount) * 100 : 100;
        
        const downloadProgressFill = document.getElementById(`download-progress-fill-${playlistId}`);
        const downloadProgressText = document.getElementById(`download-progress-text-${playlistId}`);
        const statDownloaded = document.getElementById(`stat-downloaded-${playlistId}`);
        
        if (downloadProgressFill) downloadProgressFill.style.width = `${progressPercent}%`;
        if (downloadProgressText) downloadProgressText.textContent = `${completedCount}/${missingCount} completed (${progressPercent.toFixed(0)}%)`;
        if (statDownloaded) statDownloaded.textContent = completedCount;
        
        console.log(`✅ [Completed Results] Updated modal with ${completedCount} completed, ${failedOrCancelledCount} failed tasks`);
        
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
            const isFound = result.status === 'found';
            if (isFound) actualMatches++;
            
            return {
                index: index,
                yt_track: result.tidal_track ? result.tidal_track.name : 'Unknown',
                yt_artist: result.tidal_track ? (result.tidal_track.artists ? result.tidal_track.artists.join(', ') : 'Unknown') : 'Unknown',
                status: isFound ? '✅ Found' : '❌ Not Found',
                status_class: isFound ? 'found' : 'not-found',
                spotify_track: result.spotify_data ? result.spotify_data.name : '-',
                spotify_artist: result.spotify_data ? result.spotify_data.artists.join(', ') : '-',
                spotify_album: result.spotify_data ? result.spotify_data.album : '-'
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
    
    const pollInterval = setInterval(async () => {
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
                results: status.results.map((result, index) => ({
                    index: index,
                    yt_track: result.tidal_track ? result.tidal_track.name : 'Unknown',
                    yt_artist: result.tidal_track ? (result.tidal_track.artists ? result.tidal_track.artists.join(', ') : 'Unknown') : 'Unknown',
                    status: result.status === 'found' ? '✅ Found' : '❌ Not Found',
                    status_class: result.status === 'found' ? 'found' : 'not-found',
                    spotify_track: result.spotify_data ? result.spotify_data.name : '-',
                    spotify_artist: result.spotify_data ? result.spotify_data.artists.join(', ') : '-',
                    spotify_album: result.spotify_data ? result.spotify_data.album : '-'
                }))
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
                                `[Tidal] ${playlistData.name}`, 
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
        
        // Update card and modal to syncing phase
        updateTidalCardPhase(playlistId, 'syncing');
        
        // Update modal buttons if modal is open
        updateTidalModalButtons(urlHash, 'syncing');
        
        // Start sync polling
        startTidalSyncPolling(urlHash);
        
        showToast('Tidal playlist sync started!', 'success');
        
    } catch (error) {
        console.error('❌ Error starting Tidal sync:', error);
        showToast(`Error starting sync: ${error.message}`, 'error');
    }
}

function startTidalSyncPolling(urlHash) {
    // Stop any existing polling
    if (activeYouTubePollers[urlHash]) {
        clearInterval(activeYouTubePollers[urlHash]);
    }
    
    const state = youtubePlaylistStates[urlHash];
    const playlistId = state.tidal_playlist_id;
    
    const pollInterval = setInterval(async () => {
        try {
            const response = await fetch(`/api/tidal/sync/status/${playlistId}`);
            const status = await response.json();
            
            if (status.error) {
                console.error('❌ Error polling Tidal sync status:', status.error);
                clearInterval(pollInterval);
                delete activeYouTubePollers[urlHash];
                return;
            }
            
            // Update card progress with sync stats
            updateTidalCardSyncProgress(playlistId, status.progress);
            
            // Update modal sync display if open
            updateTidalModalSyncProgress(urlHash, status.progress);
            
            // Check if complete
            if (status.complete) {
                clearInterval(pollInterval);
                delete activeYouTubePollers[urlHash];
                
                // Update both states to sync_complete
                if (tidalPlaylistStates[playlistId]) {
                    tidalPlaylistStates[playlistId].phase = 'sync_complete';
                }
                if (youtubePlaylistStates[urlHash]) {
                    youtubePlaylistStates[urlHash].phase = 'sync_complete';
                }
                
                // Update card phase to sync complete
                updateTidalCardPhase(playlistId, 'sync_complete');
                
                // Update modal buttons
                updateTidalModalButtons(urlHash, 'sync_complete');
                
                console.log('✅ Tidal sync complete:', urlHash);
                showToast('Tidal playlist sync complete!', 'success');
            } else if (status.sync_status === 'error') {
                clearInterval(pollInterval);
                delete activeYouTubePollers[urlHash];
                
                // Update both states to discovered (revert on error)
                if (tidalPlaylistStates[playlistId]) {
                    tidalPlaylistStates[playlistId].phase = 'discovered';
                }
                if (youtubePlaylistStates[urlHash]) {
                    youtubePlaylistStates[urlHash].phase = 'discovered';
                }
                
                // Revert to discovered phase on error
                updateTidalCardPhase(playlistId, 'discovered');
                updateTidalModalButtons(urlHash, 'discovered');
                
                showToast(`Sync failed: ${status.error || 'Unknown error'}`, 'error');
            }
            
        } catch (error) {
            console.error('❌ Error polling Tidal sync:', error);
            clearInterval(pollInterval);
            delete activeYouTubePollers[urlHash];
        }
    }, 1000);
    
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
        
        // Get the actual Tidal playlist ID
        const tidalPlaylistId = state.tidal_playlist_id;
        const tidalState = tidalPlaylistStates[tidalPlaylistId];
        
        if (!tidalState || !tidalState.discovery_results) {
            showToast('No discovery results available for download', 'error');
            return;
        }
        
        // Convert Tidal discovery results to Spotify tracks format (same as YouTube)
        const spotifyTracks = [];
        for (const result of tidalState.discovery_results) {
            if (result.spotify_data) {
                spotifyTracks.push(result.spotify_data);
            }
        }
        
        if (spotifyTracks.length === 0) {
            showToast('No Spotify matches found for download', 'error');
            return;
        }
        
        // Create a virtual playlist for the download system
        const virtualPlaylistId = `tidal_${tidalPlaylistId}`;
        const playlistName = `[Tidal] ${tidalState.playlist.name}`;
        
        // Store reference for card navigation (same as YouTube)
        tidalState.convertedSpotifyPlaylistId = virtualPlaylistId;
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
    
    // Use the exact same modal HTML structure as the existing Spotify modal
    modal.innerHTML = `
        <div class="download-missing-modal-content">
            <div class="download-missing-modal-header">
                <h2 class="download-missing-modal-title">Download Missing Tracks - ${escapeHtml(playlistName)}</h2>
                <span class="download-missing-modal-close" onclick="closeDownloadMissingModal('${virtualPlaylistId}')">&times;</span>
            </div>
            
            <div class="download-missing-modal-body">
                <div class="download-dashboard-stats">
                    <div class="dashboard-stat stat-total">
                        <div class="dashboard-stat-number" id="stat-total-${virtualPlaylistId}">${spotifyTracks.length}</div>
                        <div class="dashboard-stat-label">Total Tracks</div>
                    </div>
                    <div class="dashboard-stat stat-found">
                        <div class="dashboard-stat-number" id="stat-found-${virtualPlaylistId}">-</div>
                        <div class="dashboard-stat-label">Found in Library</div>
                    </div>
                    <div class="dashboard-stat stat-missing">
                        <div class="dashboard-stat-number" id="stat-missing-${virtualPlaylistId}">-</div>
                        <div class="dashboard-stat-label">Missing Tracks</div>
                    </div>
                    <div class="dashboard-stat stat-downloaded">
                        <div class="dashboard-stat-number" id="stat-downloaded-${virtualPlaylistId}">0</div>
                        <div class="dashboard-stat-label">Downloaded</div>
                    </div>
                </div>
                
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
                    </div>
                    <div class="download-tracks-table-container">
                        <table class="download-tracks-table">
                            <thead>
                                <tr>
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
                                        <td class="track-number">${index + 1}</td>
                                        <td class="track-name" title="${escapeHtml(track.name)}">${escapeHtml(track.name)}</td>
                                        <td class="track-artist" title="${escapeHtml(track.artists.join(', '))}">${track.artists.join(', ')}</td>
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
                    <div class="force-download-toggle-container" style="margin-bottom: 0px;">
                        <label class="force-download-toggle">
                            <input type="checkbox" id="force-download-all-${virtualPlaylistId}">
                            <span>Force Download All</span>
                        </label>
                    </div>
                    <button class="download-control-btn primary" id="begin-analysis-btn-${virtualPlaylistId}" onclick="startMissingTracksProcess('${virtualPlaylistId}')">
                        Begin Analysis
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

    modal.style.display = 'flex';
    hideLoadingOverlay();
}


// ===============================
// SYNC PAGE FUNCTIONALITY (REDESIGNED)
// ===============================

function initializeSyncPage() {
    // Logic for tab switching
    const tabButtons = document.querySelectorAll('.sync-tab-button');
    tabButtons.forEach(button => {
        button.addEventListener('click', () => {
            const tabId = button.dataset.tab;

            // Update button active state
            tabButtons.forEach(btn => btn.classList.remove('active'));
            button.classList.add('active');

            // Update content active state
            document.querySelectorAll('.sync-tab-content').forEach(content => {
                content.classList.remove('active');
            });
            document.getElementById(`${tabId}-tab-content`).classList.add('active');
        });
    });

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

    // Logic for the Beatport refresh button
    const beatportRefreshBtn = document.getElementById('beatport-refresh-btn');
    if (beatportRefreshBtn) {
        beatportRefreshBtn.addEventListener('click', loadBeatportCharts);
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
        });
    });

    // Logic for Beatport category cards
    const beatportCategoryCards = document.querySelectorAll('.beatport-category-card');
    beatportCategoryCards.forEach(card => {
        card.addEventListener('click', () => {
            const category = card.dataset.category;
            handleBeatportCategoryClick(category);
        });
    });

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
            const confirmed = confirm("⚠️ Full Refresh Warning!\n\nThis will clear and rebuild the database for the active server. It can take a long time. Are you sure you want to proceed?");
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
        
        // STEP 4: Create fresh modal for new wishlist process
        console.log(`🆕 [Wishlist Button] Creating fresh modal for ${countData.count} wishlist tracks`);
        await openDownloadMissingWishlistModal();
        
    } catch (error) {
        console.error('❌ [Wishlist Button] Error handling wishlist button click:', error);
        showToast(`Error opening wishlist: ${error.message}`, 'error');
    }
}

async function cleanupWishlist(playlistId) {
    try {
        // Show information dialog
        const confirmed = confirm(
            "Cleanup Wishlist\n\n" +
            "This will check all wishlist tracks against your music library and automatically remove " +
            "any tracks that already exist in your database.\n\n" +
            "This is a safe operation that only removes tracks you already have. " +
            "Continue with cleanup?"
        );
        
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
        const confirmed = confirm(
            "Clear Wishlist\n\n" +
            "Are you sure you want to clear the entire wishlist?\n\n" +
            "This will permanently remove all failed tracks from the wishlist. " +
            "This action cannot be undone."
        );
        
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

async function loadBeatportCharts() {
    const container = document.getElementById('beatport-playlist-container');
    const refreshBtn = document.getElementById('beatport-refresh-btn');

    container.innerHTML = `<div class="playlist-placeholder">🔄 Loading Beatport playlists...</div>`;
    refreshBtn.disabled = true;
    refreshBtn.textContent = '🔄 Loading...';

    try {
        // Placeholder functionality - will be implemented later with actual Beatport playlist management
        await new Promise(resolve => setTimeout(resolve, 1000)); // Simulate loading

        container.innerHTML = `
            <div class="playlist-placeholder">
                <h4 style="color: #01FF95; margin-bottom: 10px;">🎵 My Beatport Playlists</h4>
                <p>Your created Beatport playlists will appear here.</p>
                <p style="font-size: 12px; color: #888;">
                    Create playlists from Beatport charts using the Browse Charts tab,<br>
                    then sync them directly to your media server.
                </p>
            </div>
        `;

        showToast('Beatport playlists loaded successfully!', 'success');

    } catch (error) {
        container.innerHTML = `<div class="playlist-placeholder">❌ Error: ${error.message}</div>`;
        showToast(`Error loading Beatport playlists: ${error.message}`, 'error');
    } finally {
        refreshBtn.disabled = false;
        refreshBtn.textContent = '🔄 Refresh';
    }
}

function handleBeatportCategoryClick(category) {
    console.log(`🎵 Beatport category clicked: ${category}`);

    // Placeholder functionality for category navigation
    switch(category) {
        case 'top-charts':
            showToast('🔥 Top Charts navigation coming soon!', 'info');
            break;
        case 'genres':
            showToast('🎵 Genre Explorer navigation coming soon!', 'info');
            break;
        case 'staff-picks':
            showToast('📊 Staff Picks navigation coming soon!', 'info');
            break;
        default:
            showToast(`Category "${category}" clicked`, 'info');
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
        
        // Update card with parsed data and stay in 'fresh' phase
        updateYouTubeCardData(result.url_hash, result);
        updateYouTubeCardPhase(result.url_hash, 'fresh');
        
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
            phaseTextElement.style.color = '#1db954'; // Green
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
            phaseTextElement.style.color = '#1db954'; // Green
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
            phaseTextElement.style.color = '#1db954'; // Green
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
                                const playlistName = `[YouTube] ${state.playlist.name}`;
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
                    const playlistName = `[YouTube] ${state.playlist.name}`;
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
    
    const pollInterval = setInterval(async () => {
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
                state.discoveryProgress = status.progress || 0;
                state.spotifyMatches = status.spotify_matches || 0;
            }
            
            // Update modal if open
            updateYouTubeDiscoveryModal(urlHash, status);
            
            // Check if complete
            if (status.complete) {
                clearInterval(pollInterval);
                delete activeYouTubePollers[urlHash];
                
                // Update card phase to discovered
                updateYouTubeCardPhase(urlHash, 'discovered');
                
                // Update modal buttons to show sync and download buttons
                updateYouTubeModalButtons(urlHash, 'discovered');
                
                console.log('✅ YouTube discovery complete:', urlHash);
                showToast('YouTube discovery complete!', 'success');
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
    const state = youtubePlaylistStates[urlHash];
    if (!state || !state.playlist) {
        console.error('❌ No YouTube playlist data found for hash:', urlHash);
        return;
    }
    
    console.log('🎵 Opening YouTube discovery modal for:', state.playlist.name);
    
    // Check if modal already exists
    let modal = document.getElementById(`youtube-discovery-modal-${urlHash}`);
    
    if (modal) {
        // Modal exists, just show it
        modal.classList.remove('hidden');
        console.log('🔄 Showing existing modal with preserved state');
        
        // Resume polling if discovery is in progress
        if (state.phase === 'discovering' && !activeYouTubePollers[urlHash]) {
            console.log('🔄 Resuming discovery polling...');
            startYouTubeDiscoveryPolling(urlHash);
        }
    } else {
        // Create new modal (support both YouTube and Tidal like sync.py)
        const isTidal = state.is_tidal_playlist;
        const modalTitle = isTidal ? '🎵 Tidal Playlist Discovery' : '🎵 YouTube Playlist Discovery';
        const sourceLabel = isTidal ? 'Tidal' : 'YT';
        
        const modalHtml = `
            <div class="modal-overlay" id="youtube-discovery-modal-${urlHash}">
                <div class="youtube-discovery-modal">
                    <div class="modal-header">
                        <h2>${modalTitle}</h2>
                        <div class="modal-subtitle">${state.playlist.name} (${state.playlist.tracks.length} tracks)</div>
                        <div class="modal-description">${getModalDescription(state.phase, isTidal)}</div>
                        <button class="modal-close-btn" onclick="closeYouTubeDiscoveryModal('${urlHash}')">✕</button>
                    </div>
                    
                    <div class="modal-body">
                        <div class="progress-section">
                            <div class="progress-label">🔍 Spotify Discovery Progress</div>
                            <div class="progress-bar-container">
                                <div class="progress-bar-fill" id="youtube-discovery-progress-${urlHash}" style="width: 0%;"></div>
                            </div>
                            <div class="progress-text" id="youtube-discovery-progress-text-${urlHash}">${getInitialProgressText(state.phase, isTidal)}</div>
                        </div>
                        
                        <div class="discovery-table-container">
                            <table class="discovery-table">
                                <thead>
                                    <tr>
                                        <th>${sourceLabel} Track</th>
                                        <th>${sourceLabel} Artist</th>
                                        <th>Status</th>
                                        <th>Spotify Track</th>
                                        <th>Spotify Artist</th>
                                        <th>Album</th>
                                        ${isTidal ? '' : '<th>Duration</th>'}
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
            const progressData = {
                progress: state.discoveryProgress || 0,
                spotify_matches: state.spotifyMatches || 0,
                spotify_total: state.playlist.tracks.length,
                results: state.discoveryResults
            };
            updateYouTubeDiscoveryModal(urlHash, progressData);
        }
        
        console.log('✨ Created new modal with current state');
    }
}

function getModalActionButtons(urlHash, phase, state = null) {
    // Get state if not provided
    if (!state) {
        state = youtubePlaylistStates[urlHash];
    }
    
    const isTidal = state && state.is_tidal_playlist;
    
    // Validate data availability for buttons
    const hasDiscoveryResults = state && state.discoveryResults && state.discoveryResults.length > 0;
    const hasSpotifyMatches = state && state.spotifyMatches > 0;
    const hasConvertedPlaylistId = state && state.convertedSpotifyPlaylistId;
    
    switch (phase) {
        case 'discovered':
            // Only show buttons if we actually have discovery data
            if (!hasDiscoveryResults) {
                return `<div class="modal-info">⚠️ No discovery results available. Try starting discovery again.</div>`;
            }
            
            let buttons = '';
            
            // Only show sync button if there are Spotify matches
            if (hasSpotifyMatches) {
                if (isTidal) {
                    buttons += `<button class="modal-btn modal-btn-primary" onclick="startTidalPlaylistSync('${urlHash}')">🔄 Sync This Playlist</button>`;
                } else {
                    buttons += `<button class="modal-btn modal-btn-primary" onclick="startYouTubePlaylistSync('${urlHash}')">🔄 Sync This Playlist</button>`;
                }
            }
            
            // Only show download button if we have matches or a converted playlist ID
            if (hasSpotifyMatches || hasConvertedPlaylistId) {
                if (isTidal) {
                    buttons += `<button class="modal-btn modal-btn-primary" onclick="startTidalDownloadMissing('${urlHash}')">🔍 Download Missing Tracks</button>`;
                } else {
                    buttons += `<button class="modal-btn modal-btn-primary" onclick="startYouTubeDownloadMissing('${urlHash}')">🔍 Download Missing Tracks</button>`;
                }
            }
            
            if (!buttons) {
                buttons = `<div class="modal-info">ℹ️ No Spotify matches found. Discovery complete but no tracks could be matched.</div>`;
            }
            
            return buttons;
            
        case 'syncing':
            if (isTidal) {
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
            
            // Only show sync button if there are Spotify matches
            if (hasSpotifyMatches) {
                if (isTidal) {
                    syncCompleteButtons += `<button class="modal-btn modal-btn-primary" onclick="startTidalPlaylistSync('${urlHash}')">🔄 Sync This Playlist</button>`;
                } else {
                    syncCompleteButtons += `<button class="modal-btn modal-btn-primary" onclick="startYouTubePlaylistSync('${urlHash}')">🔄 Sync This Playlist</button>`;
                }
            }
            
            // Only show download button if we have matches or a converted playlist ID
            if (hasSpotifyMatches || hasConvertedPlaylistId) {
                if (isTidal) {
                    syncCompleteButtons += `<button class="modal-btn modal-btn-primary" onclick="startTidalDownloadMissing('${urlHash}')">🔍 Download Missing Tracks</button>`;
                } else {
                    syncCompleteButtons += `<button class="modal-btn modal-btn-primary" onclick="startYouTubeDownloadMissing('${urlHash}')">🔍 Download Missing Tracks</button>`;
                }
            }
            
            if (isTidal) {
                // Tidal doesn't have a reset function yet, but could be added
                // syncCompleteButtons += `<button class="modal-btn modal-btn-secondary" onclick="resetTidalPlaylist('${urlHash}')">🔄 Reset</button>`;
            } else {
                syncCompleteButtons += `<button class="modal-btn modal-btn-secondary" onclick="resetYouTubePlaylist('${urlHash}')">🔄 Reset</button>`;
            }
            
            return syncCompleteButtons;
            
        default:
            return '';
    }
}

function getModalDescription(phase, isTidal = false) {
    const source = isTidal ? 'Tidal' : 'YouTube';
    switch (phase) {
        case 'fresh':
            return `Ready to discover clean Spotify metadata for ${source} tracks...`;
        case 'discovering':
            return `Discovering clean Spotify metadata for ${source} tracks...`;
        case 'discovered':
            return 'Discovery complete! View the results below.';
        default:
            return `Discovering clean Spotify metadata for ${source} tracks...`;
    }
}

function getInitialProgressText(phase, isTidal = false) {
    switch (phase) {
        case 'fresh':
            return 'Click Start Discovery to begin...';
        case 'discovering':
            return 'Starting discovery...';
        case 'discovered':
            return 'Discovery completed!';
        default:
            return 'Starting discovery...';
    }
}

function generateTableRowsFromState(state, urlHash) {
    const isTidal = state.is_tidal_playlist;
    
    if (state.discoveryResults && state.discoveryResults.length > 0) {
        // Generate rows from existing discovery results
        return state.discoveryResults.map((result, index) => `
            <tr id="discovery-row-${urlHash}-${result.index}">
                <td class="yt-track">${result.yt_track}</td>
                <td class="yt-artist">${result.yt_artist}</td>
                <td class="discovery-status ${result.status_class}">${result.status}</td>
                <td class="spotify-track">${result.spotify_track || '-'}</td>
                <td class="spotify-artist">${result.spotify_artist || '-'}</td>
                <td class="spotify-album">${result.spotify_album || '-'}</td>
                ${isTidal ? '' : `<td class="duration">${result.duration}</td>`}
            </tr>
        `).join('');
    } else {
        // Generate initial rows from playlist tracks
        return generateInitialTableRows(state.playlist.tracks, isTidal, urlHash);
    }
}

function generateInitialTableRows(tracks, isTidal = false, urlHash = '') {
    return tracks.map((track, index) => `
        <tr id="discovery-row-${urlHash}-${index}">
            <td class="yt-track">${track.name}</td>
            <td class="yt-artist">${track.artists ? (Array.isArray(track.artists) ? track.artists.join(', ') : track.artists) : 'Unknown Artist'}</td>
            <td class="discovery-status">🔍 Pending...</td>
            <td class="spotify-track">-</td>
            <td class="spotify-artist">-</td>
            <td class="spotify-album">-</td>
            ${isTidal ? '' : `<td class="duration">${formatDuration(track.duration_ms)}</td>`}
        </tr>
    `).join('');
}

function formatDuration(durationMs) {
    if (!durationMs) return '0:00';
    const minutes = Math.floor(durationMs / 60000);
    const seconds = Math.floor((durationMs % 60000) / 1000);
    return `${minutes}:${seconds.toString().padStart(2, '0')}`;
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
        
        statusCell.textContent = result.status;
        statusCell.className = `discovery-status ${result.status_class}`;
        
        spotifyTrackCell.textContent = result.spotify_track || '-';
        spotifyArtistCell.textContent = result.spotify_artist || '-';
        spotifyAlbumCell.textContent = result.spotify_album || '-';
    });
    
    // Update action buttons if discovery is complete (progress = 100%)
    if (status.progress >= 100) {
        const state = youtubePlaylistStates[urlHash];
        if (state && state.phase === 'discovered') {
            const actionButtonsContainer = document.querySelector(`#youtube-discovery-modal-${urlHash} .modal-footer-left`);
            if (actionButtonsContainer) {
                actionButtonsContainer.innerHTML = getModalActionButtons(urlHash, 'discovered', state);
                console.log(`✨ Updated action buttons for completed discovery: ${urlHash}`);
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
        
        // Update card and modal to syncing phase
        updateYouTubeCardPhase(urlHash, 'syncing');
        
        // Update modal buttons if modal is open
        updateYouTubeModalButtons(urlHash, 'syncing');
        
        // Start sync polling
        startYouTubeSyncPolling(urlHash);
        
        showToast('YouTube playlist sync started!', 'success');
        
    } catch (error) {
        console.error('❌ Error starting YouTube sync:', error);
        showToast(`Error starting sync: ${error.message}`, 'error');
    }
}

function startYouTubeSyncPolling(urlHash) {
    // Stop any existing polling
    if (activeYouTubePollers[urlHash]) {
        clearInterval(activeYouTubePollers[urlHash]);
    }
    
    const pollInterval = setInterval(async () => {
        try {
            const response = await fetch(`/api/youtube/sync/status/${urlHash}`);
            const status = await response.json();
            
            if (status.error) {
                console.error('❌ Error polling YouTube sync status:', status.error);
                clearInterval(pollInterval);
                delete activeYouTubePollers[urlHash];
                return;
            }
            
            // Update card progress with sync stats
            updateYouTubeCardSyncProgress(urlHash, status.progress);
            
            // Update modal sync display if open
            updateYouTubeModalSyncProgress(urlHash, status.progress);
            
            // Check if complete
            if (status.complete) {
                clearInterval(pollInterval);
                delete activeYouTubePollers[urlHash];
                
                // Update card phase to sync complete
                updateYouTubeCardPhase(urlHash, 'sync_complete');
                
                // Update modal buttons
                updateYouTubeModalButtons(urlHash, 'sync_complete');
                
                console.log('✅ YouTube sync complete:', urlHash);
                showToast('YouTube playlist sync complete!', 'success');
            } else if (status.sync_status === 'error') {
                clearInterval(pollInterval);
                delete activeYouTubePollers[urlHash];
                
                // Revert to discovered phase on error
                updateYouTubeCardPhase(urlHash, 'discovered');
                updateYouTubeModalButtons(urlHash, 'discovered');
                
                showToast(`Sync failed: ${status.error || 'Unknown error'}`, 'error');
            }
            
        } catch (error) {
            console.error('❌ Error polling YouTube sync:', error);
            clearInterval(pollInterval);
            delete activeYouTubePollers[urlHash];
        }
    }, 1000);
    
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
    const statusDisplay = document.getElementById(`youtube-sync-status-${urlHash}`);
    if (!statusDisplay || !progress) return;
    
    console.log(`📊 Updating YouTube modal sync progress for ${urlHash}:`, progress);
    
    // Update individual counters exactly like Spotify sync
    const totalEl = document.getElementById(`youtube-total-${urlHash}`);
    const matchedEl = document.getElementById(`youtube-matched-${urlHash}`);
    const failedEl = document.getElementById(`youtube-failed-${urlHash}`);
    const percentageEl = document.getElementById(`youtube-percentage-${urlHash}`);
    
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
        console.log('🔍 Starting download missing tracks for YouTube playlist:', urlHash);
        
        const state = youtubePlaylistStates[urlHash];
        if (!state || !state.discoveryResults) {
            showToast('No discovery results available for download', 'error');
            return;
        }
        
        // Convert YouTube results to a format compatible with the download modal
        const spotifyTracks = state.discoveryResults
            .filter(result => result.spotify_data)
            .map(result => result.spotify_data);
        
        if (spotifyTracks.length === 0) {
            showToast('No Spotify matches found for download', 'error');
            return;
        }
        
        // Create a virtual playlist for the download system
        const virtualPlaylistId = `youtube_${urlHash}`;
        const playlistName = `[YouTube] ${state.playlist.name}`;
        
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

// ============================================================================
// ARTISTS PAGE FUNCTIONALITY - ELEGANT SEARCH & DISCOVERY
// ============================================================================

/**
 * Initialize the artists page when navigated to (only runs once)
 */
function initializeArtistsPage() {
    console.log('🎵 Initializing Artists Page (first time)');
    
    // Get DOM elements
    const searchInput = document.getElementById('artists-search-input');
    const headerSearchInput = document.getElementById('artists-header-search-input');
    const searchStatus = document.getElementById('artists-search-status');
    const backButton = document.getElementById('artists-back-button');
    const detailBackButton = document.getElementById('artist-detail-back-button');
    
    // Set up event listeners (only need to do this once)
    if (searchInput) {
        searchInput.addEventListener('input', handleArtistsSearchInput);
        searchInput.addEventListener('keypress', handleArtistsSearchKeypress);
    }
    
    if (headerSearchInput) {
        headerSearchInput.addEventListener('input', handleArtistsHeaderSearchInput);
        headerSearchInput.addEventListener('keypress', handleArtistsSearchKeypress);
    }
    
    if (backButton) {
        backButton.addEventListener('click', () => showArtistsSearchState());
    }
    
    if (detailBackButton) {
        detailBackButton.addEventListener('click', () => showArtistsResultsState());
    }
    
    // Initialize tabs (only need to do this once)
    initializeArtistTabs();
    
    // Mark as initialized
    artistsPageState.isInitialized = true;
    
    // Restore previous state instead of always resetting to search
    restoreArtistsPageState();
    console.log('✅ Artists Page initialized successfully (ready for navigation)');
}

/**
 * Restore the artists page to its previous state
 */
function restoreArtistsPageState() {
    console.log(`🔄 Restoring artists page state: ${artistsPageState.currentView}`);
    
    switch (artistsPageState.currentView) {
        case 'results':
            // Restore search results state
            if (artistsPageState.searchQuery && artistsPageState.searchResults.length > 0) {
                console.log(`📦 Restoring search results for: "${artistsPageState.searchQuery}"`);
                
                // Restore search input values
                const searchInput = document.getElementById('artists-search-input');
                const headerSearchInput = document.getElementById('artists-header-search-input');
                
                if (searchInput) searchInput.value = artistsPageState.searchQuery;
                if (headerSearchInput) headerSearchInput.value = artistsPageState.searchQuery;
                
                // Display the cached results
                displayArtistsResults(artistsPageState.searchQuery, artistsPageState.searchResults);
            } else {
                // No valid results state, fall back to search
                showArtistsSearchState();
            }
            break;
            
        case 'detail':
            // Restore artist detail state
            if (artistsPageState.selectedArtist && artistsPageState.artistDiscography) {
                console.log(`🎤 Restoring artist detail for: ${artistsPageState.selectedArtist.name}`);
                
                // First restore search results if they exist
                if (artistsPageState.searchQuery && artistsPageState.searchResults.length > 0) {
                    const searchInput = document.getElementById('artists-search-input');
                    const headerSearchInput = document.getElementById('artists-header-search-input');
                    
                    if (searchInput) searchInput.value = artistsPageState.searchQuery;
                    if (headerSearchInput) headerSearchInput.value = artistsPageState.searchQuery;
                }
                
                // Show artist detail state
                showArtistDetailState();
                
                // Update artist info in header
                updateArtistDetailHeader(artistsPageState.selectedArtist);
                
                // Display cached discography
                if (artistsPageState.artistDiscography.albums || artistsPageState.artistDiscography.singles) {
                    displayArtistDiscography(artistsPageState.artistDiscography);
                    // Restore cached completion data instead of re-scanning
                    restoreCachedCompletionData(artistsPageState.selectedArtist.id);
                }
            } else {
                // No valid detail state, fall back to search or results
                if (artistsPageState.searchQuery && artistsPageState.searchResults.length > 0) {
                    displayArtistsResults(artistsPageState.searchQuery, artistsPageState.searchResults);
                } else {
                    showArtistsSearchState();
                }
            }
            break;
            
        default:
        case 'search':
            // Show search state (but preserve any existing search query)
            if (artistsPageState.searchQuery) {
                const searchInput = document.getElementById('artists-search-input');
                if (searchInput) searchInput.value = artistsPageState.searchQuery;
            }
            showArtistsSearchState();
            break;
    }
}

/**
 * Handle search input with debouncing
 */
function handleArtistsSearchInput(event) {
    const query = event.target.value.trim();
    updateArtistsSearchStatus('searching');
    
    // Clear existing timeout
    if (artistsSearchTimeout) {
        clearTimeout(artistsSearchTimeout);
    }
    
    // Cancel any active search
    if (artistsSearchController) {
        artistsSearchController.abort();
    }
    
    if (query === '') {
        updateArtistsSearchStatus('default');
        return;
    }
    
    // Set up new debounced search
    artistsSearchTimeout = setTimeout(() => {
        performArtistsSearch(query);
    }, 1000); // 1 second debounce
}

/**
 * Handle header search input (already in results state)
 */
function handleArtistsHeaderSearchInput(event) {
    const query = event.target.value.trim();
    
    // Update main search input to match
    const mainInput = document.getElementById('artists-search-input');
    if (mainInput) {
        mainInput.value = query;
    }
    
    // Trigger search with same debouncing logic
    handleArtistsSearchInput(event);
}

/**
 * Handle Enter key press in search inputs
 */
function handleArtistsSearchKeypress(event) {
    if (event.key === 'Enter') {
        event.preventDefault();
        const query = event.target.value.trim();
        
        if (query && query !== artistsPageState.searchQuery) {
            // Clear timeout and search immediately
            if (artistsSearchTimeout) {
                clearTimeout(artistsSearchTimeout);
            }
            performArtistsSearch(query);
        }
    }
}

/**
 * Perform artist search with API call
 */
async function performArtistsSearch(query) {
    console.log(`🔍 Searching for artists: "${query}"`);
    
    // Check cache first
    if (artistsPageState.cache.searches[query]) {
        console.log('📦 Using cached search results');
        displayArtistsResults(query, artistsPageState.cache.searches[query]);
        return;
    }
    
    // Update status
    updateArtistsSearchStatus('searching');
    
    // Show loading cards immediately if we're in results view
    if (artistsPageState.currentView === 'results') {
        showSearchLoadingCards();
    }
    
    try {
        // Set up abort controller
        artistsSearchController = new AbortController();
        
        const response = await fetch('/api/match/search', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            },
            body: JSON.stringify({
                query: query,
                context: 'artist'
            }),
            signal: artistsSearchController.signal
        });
        
        if (!response.ok) {
            throw new Error(`Search failed: ${response.status}`);
        }
        
        const data = await response.json();
        console.log(`✅ Found ${data.results?.length || 0} artists`);
        
        // Transform the results to flatten the nested artist data
        const transformedResults = (data.results || []).map(result => {
            // Extract artist data from the nested structure
            const artist = result.artist || result;
            return {
                id: artist.id,
                name: artist.name,
                image_url: artist.image_url,
                genres: artist.genres,
                popularity: artist.popularity,
                confidence: result.confidence || 0
            };
        });
        
        console.log('🔧 Transformed results:', transformedResults);
        
        // Cache the transformed results
        artistsPageState.cache.searches[query] = transformedResults;
        
        // Display results
        displayArtistsResults(query, transformedResults);
        
    } catch (error) {
        if (error.name !== 'AbortError') {
            console.error('❌ Artist search failed:', error);
            
            // Provide specific error messages based on the error type
            let errorMessage = 'Search failed. Please try again.';
            if (error.message.includes('401') || error.message.includes('authentication')) {
                errorMessage = 'Spotify not authenticated. Please check your API settings.';
            } else if (error.message.includes('network') || error.message.includes('fetch')) {
                errorMessage = 'Network error. Please check your connection.';
            } else if (error.message.includes('timeout')) {
                errorMessage = 'Search timed out. Please try again.';
            }
            
            updateArtistsSearchStatus('error', errorMessage);
        }
    } finally {
        artistsSearchController = null;
    }
}

/**
 * Display artist search results
 */
function displayArtistsResults(query, results) {
    console.log(`📊 Displaying ${results.length} artist results`);
    
    // Update state
    artistsPageState.searchQuery = query;
    artistsPageState.searchResults = results;
    artistsPageState.currentView = 'results';
    
    // Update header search input if different
    const headerInput = document.getElementById('artists-header-search-input');
    if (headerInput && headerInput.value !== query) {
        headerInput.value = query;
    }
    
    // Show results state
    showArtistsResultsState();
    
    // Populate results
    const container = document.getElementById('artists-cards-container');
    if (!container) return;
    
    if (results.length === 0) {
        container.innerHTML = `
            <div style="grid-column: 1 / -1; text-align: center; padding: 60px 20px; color: rgba(255, 255, 255, 0.6);">
                <div style="font-size: 24px; margin-bottom: 12px;">🔍</div>
                <div style="font-size: 16px; font-weight: 600; margin-bottom: 8px;">No artists found</div>
                <div style="font-size: 14px;">Try a different search term</div>
            </div>
        `;
        return;
    }
    
    // Create artist cards
    container.innerHTML = results.map(result => createArtistCardHTML(result)).join('');
    
    // Add event listeners to cards
    container.querySelectorAll('.artist-card').forEach((card, index) => {
        card.addEventListener('click', () => selectArtistForDetail(results[index]));
        
        // Extract colors from artist image for dynamic glow
        const artist = results[index];
        if (artist.image_url) {
            extractImageColors(artist.image_url, (colors) => {
                applyDynamicGlow(card, colors);
            });
        }
    });
    
    // Update watchlist status for all cards
    updateArtistCardWatchlistStatus();
    
    // Add mouse wheel horizontal scrolling
    container.addEventListener('wheel', (event) => {
        if (event.deltaY !== 0) {
            event.preventDefault();
            container.scrollLeft += event.deltaY;
        }
    });
}

/**
 * Create HTML for an artist card
 */
function createArtistCardHTML(artist) {
    const imageUrl = artist.image_url || '';
    const genres = artist.genres && artist.genres.length > 0 ? 
        artist.genres.slice(0, 3).join(', ') : 'Various genres';
    const popularity = artist.popularity || 0;
    
    // Create a fallback gradient if no image is available
    const backgroundStyle = imageUrl ? 
        `background-image: url('${imageUrl}');` :
        `background: linear-gradient(135deg, rgba(29, 185, 84, 0.3) 0%, rgba(24, 156, 71, 0.2) 100%);`;
    
    // Format popularity as a percentage for better UX
    const popularityText = popularity > 0 ? `${popularity}% Popular` : 'Popularity Unknown';
    
    return `
        <div class="artist-card" data-artist-id="${artist.id}">
            <div class="artist-card-background" style="${backgroundStyle}"></div>
            <div class="artist-card-overlay"></div>
            <div class="artist-card-content">
                <div class="artist-card-name">${escapeHtml(artist.name)}</div>
                <div class="artist-card-genres">${escapeHtml(genres)}</div>
                <div class="artist-card-popularity">
                    <span class="popularity-icon">🔥</span>
                    <span>${popularityText}</span>
                </div>
                <div class="artist-card-actions">
                    <button class="watchlist-toggle-btn" data-artist-id="${artist.id}" data-artist-name="${escapeHtml(artist.name)}" onclick="toggleWatchlist(event, '${artist.id}', '${escapeHtml(artist.name)}')">
                        <span class="watchlist-icon">👁️</span>
                        <span class="watchlist-text">Add to Watchlist</span>
                    </button>
                </div>
            </div>
        </div>
    `;
}

/**
 * Select an artist and show their discography
 */
async function selectArtistForDetail(artist) {
    console.log(`🎤 Selected artist: ${artist.name}`);

    // Update state
    artistsPageState.selectedArtist = artist;
    artistsPageState.currentView = 'detail';

    // Show detail state
    showArtistDetailState();

    // Update artist info in header
    updateArtistDetailHeader(artist);

    // Load discography
    await loadArtistDiscography(artist.id);
}

/**
 * Load artist's discography from Spotify
 */
async function loadArtistDiscography(artistId) {
    console.log(`💿 Loading discography for artist: ${artistId}`);
    
    // Check cache first
    if (artistsPageState.cache.discography[artistId]) {
        console.log('📦 Using cached discography');
        const cachedDiscography = artistsPageState.cache.discography[artistId];
        displayArtistDiscography(cachedDiscography);
        // Still check completion status for cached data
        await checkDiscographyCompletion(artistId, cachedDiscography);
        return;
    }
    
    try {
        // Show loading states
        showDiscographyLoading();
        
        // Call the real API endpoint
        const response = await fetch(`/api/artist/${artistId}/discography`);
        
        if (!response.ok) {
            if (response.status === 401) {
                throw new Error('Spotify not authenticated. Please check your API settings.');
            }
            throw new Error(`Failed to load discography: ${response.status}`);
        }
        
        const data = await response.json();
        
        if (data.error) {
            throw new Error(data.error);
        }
        
        const discography = {
            albums: data.albums || [],
            singles: data.singles || []
        };
        
        console.log(`✅ Loaded ${discography.albums.length} albums and ${discography.singles.length} singles`);
        
        // Cache the results
        artistsPageState.cache.discography[artistId] = discography;
        artistsPageState.artistDiscography = discography;
        
        // Display results
        displayArtistDiscography(discography);
        
        // Check completion status for all albums and singles
        await checkDiscographyCompletion(artistId, discography);
        
    } catch (error) {
        console.error('❌ Failed to load discography:', error);
        showDiscographyError(error.message);
    }
}

/**
 * Display artist's discography in tabs
 */
function displayArtistDiscography(discography) {
    console.log(`📀 Displaying discography: ${discography.albums?.length || 0} albums, ${discography.singles?.length || 0} singles`);
    
    // Populate albums
    const albumsContainer = document.getElementById('album-cards-container');
    if (albumsContainer) {
        if (discography.albums?.length > 0) {
            albumsContainer.innerHTML = discography.albums.map(album => createAlbumCardHTML(album)).join('');
            
            // Add dynamic glow effects and click handlers to album cards
            albumsContainer.querySelectorAll('.album-card').forEach((card, index) => {
                const album = discography.albums[index];
                if (album.image_url) {
                    extractImageColors(album.image_url, (colors) => {
                        applyDynamicGlow(card, colors);
                    });
                }
                
                // Add click handler for download missing tracks modal
                card.addEventListener('click', () => handleArtistAlbumClick(album, 'albums'));
                card.style.cursor = 'pointer';
            });
        } else {
            albumsContainer.innerHTML = `
                <div style="grid-column: 1 / -1; text-align: center; padding: 40px 20px; color: rgba(255, 255, 255, 0.6);">
                    <div style="font-size: 18px; margin-bottom: 8px;">💿</div>
                    <div style="font-size: 14px;">No albums found</div>
                </div>
            `;
        }
    }
    
    // Populate singles
    const singlesContainer = document.getElementById('singles-cards-container');
    if (singlesContainer) {
        if (discography.singles?.length > 0) {
            singlesContainer.innerHTML = discography.singles.map(single => createAlbumCardHTML(single)).join('');
            
            // Add dynamic glow effects and click handlers to singles cards
            singlesContainer.querySelectorAll('.album-card').forEach((card, index) => {
                const single = discography.singles[index];
                if (single.image_url) {
                    extractImageColors(single.image_url, (colors) => {
                        applyDynamicGlow(card, colors);
                    });
                }
                
                // Add click handler for download missing tracks modal
                card.addEventListener('click', () => handleArtistAlbumClick(single, 'singles'));
                card.style.cursor = 'pointer';
            });
        } else {
            singlesContainer.innerHTML = `
                <div style="grid-column: 1 / -1; text-align: center; padding: 40px 20px; color: rgba(255, 255, 255, 0.6);">
                    <div style="font-size: 18px; margin-bottom: 8px;">🎵</div>
                    <div style="font-size: 14px;">No singles or EPs found</div>
                </div>
            `;
        }
    }
}

/**
 * Restore cached completion data without re-scanning the database
 */
function restoreCachedCompletionData(artistId) {
    console.log(`📦 Restoring cached completion data for artist: ${artistId}`);
    
    const cachedData = artistsPageState.cache.completionData[artistId];
    if (!cachedData) {
        console.log('⚠️ No cached completion data found, skipping restoration');
        return;
    }
    
    // Restore album completion overlays
    if (cachedData.albums) {
        cachedData.albums.forEach(albumCompletion => {
            updateAlbumCompletionOverlay(albumCompletion, 'albums');
        });
        console.log(`✅ Restored ${cachedData.albums.length} album completion overlays`);
    }
    
    // Restore singles completion overlays  
    if (cachedData.singles) {
        cachedData.singles.forEach(singleCompletion => {
            updateAlbumCompletionOverlay(singleCompletion, 'singles');
        });
        console.log(`✅ Restored ${cachedData.singles.length} single completion overlays`);
    }
}

/**
 * Check completion status for entire discography with streaming updates
 */
async function checkDiscographyCompletion(artistId, discography) {
    console.log(`🔍 Starting streaming completion check for artist: ${artistId}`);
    
    try {
        // Use fetch with streaming response (Server-Sent Events)
        
        // Use fetch with streaming response
        const response = await fetch(`/api/artist/${artistId}/completion-stream`, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            },
            body: JSON.stringify({
                discography: discography,
                artist_name: artistsPageState.selectedArtist?.name || 'Unknown Artist',
                test_mode: window.location.search.includes('test=true')
            })
        });
        
        if (!response.ok) {
            throw new Error(`Failed to start completion check: ${response.status}`);
        }
        
        // Handle streaming response
        const reader = response.body.getReader();
        const decoder = new TextDecoder();
        
        while (true) {
            const { done, value } = await reader.read();
            if (done) break;
            
            const chunk = decoder.decode(value);
            const lines = chunk.split('\n');
            
            for (const line of lines) {
                if (line.startsWith('data: ')) {
                    try {
                        const data = JSON.parse(line.slice(6));
                        handleStreamingCompletionUpdate(data);
                    } catch (e) {
                        console.warn('Failed to parse streaming data:', line);
                    }
                }
            }
        }
        
    } catch (error) {
        console.error('❌ Failed to check completion status:', error);
        showCompletionError();
    }
}

/**
 * Handle individual streaming completion updates
 */
function handleStreamingCompletionUpdate(data) {
    console.log('🔄 Streaming update received:', data.type, data.name || data.artist_name);
    
    switch (data.type) {
        case 'start':
            console.log(`🎤 Starting completion check for ${data.artist_name} (${data.total_items} items)`);
            // Initialize cache for this artist if not exists
            const artistId = artistsPageState.selectedArtist?.id;
            if (artistId && !artistsPageState.cache.completionData[artistId]) {
                artistsPageState.cache.completionData[artistId] = {
                    albums: [],
                    singles: []
                };
            }
            break;
            
        case 'album_completion':
            updateAlbumCompletionOverlay(data, 'albums');
            // Cache the completion data
            cacheCompletionData(data, 'albums');
            console.log(`📀 Updated album: ${data.name} (${data.status})`);
            break;
            
        case 'single_completion':
            updateAlbumCompletionOverlay(data, 'singles');
            // Cache the completion data
            cacheCompletionData(data, 'singles');
            console.log(`🎵 Updated single: ${data.name} (${data.status})`);
            break;
            
        case 'error':
            console.error('❌ Error processing item:', data.name, data.error);
            // Could show error for specific item
            break;
            
        case 'complete':
            console.log(`✅ Completion check finished (${data.processed_count} items processed)`);
            break;
            
        default:
            console.log('Unknown streaming update type:', data.type);
    }
}

/**
 * Cache completion data for future restoration
 */
function cacheCompletionData(completionData, type) {
    const artistId = artistsPageState.selectedArtist?.id;
    if (!artistId) return;
    
    // Ensure cache structure exists
    if (!artistsPageState.cache.completionData[artistId]) {
        artistsPageState.cache.completionData[artistId] = {
            albums: [],
            singles: []
        };
    }
    
    // Add to appropriate cache array
    if (type === 'albums') {
        artistsPageState.cache.completionData[artistId].albums.push(completionData);
    } else if (type === 'singles') {
        artistsPageState.cache.completionData[artistId].singles.push(completionData);
    }
}

/**
 * Update completion overlay for a specific album/single
 */
function updateAlbumCompletionOverlay(completionData, containerType) {
    const containerId = containerType === 'albums' ? 'album-cards-container' : 'singles-cards-container';
    const container = document.getElementById(containerId);
    
    if (!container) {
        console.warn(`Container ${containerId} not found`);
        return;
    }
    
    // Find the album card by data-album-id
    const albumCard = container.querySelector(`[data-album-id="${completionData.id}"]`);
    
    if (!albumCard) {
        console.warn(`Album card not found for ID: ${completionData.id}`);
        return;
    }
    
    const overlay = albumCard.querySelector('.completion-overlay');
    if (!overlay) {
        console.warn(`Completion overlay not found for album: ${completionData.name}`);
        return;
    }
    
    // Remove existing status classes
    overlay.classList.remove('checking', 'completed', 'nearly_complete', 'partial', 'missing', 'downloading', 'downloaded', 'error');

    // Add new status class
    overlay.classList.add(completionData.status);
    
    // Update overlay text and content
    const statusText = getCompletionStatusText(completionData);
    const progressText = `${completionData.owned_tracks}/${completionData.expected_tracks}`;
    
    overlay.innerHTML = `
        <span class="completion-status">${statusText}</span>
        <span class="completion-progress">${progressText}</span>
    `;
    
    // Add tooltip with more details
    overlay.title = `${completionData.name}\n${statusText} (${completionData.completion_percentage}%)\nTracks: ${completionData.owned_tracks}/${completionData.expected_tracks}\nConfidence: ${completionData.confidence}`;
    
    // Add brief flash animation to indicate update
    overlay.style.animation = 'none';
    overlay.offsetHeight; // Trigger reflow
    overlay.style.animation = 'completionOverlayFadeIn 0.6s cubic-bezier(0.4, 0, 0.2, 1)';
    
    console.log(`📊 Updated overlay for "${completionData.name}": ${statusText} (${completionData.completion_percentage}%)`);
}

/**
 * Get human-readable status text for completion overlay
 */
function getCompletionStatusText(completionData) {
    switch (completionData.status) {
        case 'completed':
            return 'Complete';
        case 'nearly_complete':
            return 'Nearly Complete';
        case 'partial':
            return 'Partial';
        case 'missing':
            return 'Missing';
        case 'downloading':
            return 'Downloading...';
        case 'downloaded':
            return 'Downloaded';
        case 'error':
            return 'Error';
        default:
            return 'Unknown';
    }
}

/**
 * Set album to downloaded status after download finishes
 */
function setAlbumDownloadedStatus(albumId) {
    console.log(`✅ [DOWNLOAD COMPLETE] Setting album ${albumId} to downloaded status`);

    const completionData = {
        id: albumId,
        status: 'downloaded',
        owned_tracks: 0,
        expected_tracks: 0,
        name: 'Downloaded',
        completion_percentage: 100
    };

    // Find if it's in albums or singles container
    let containerType = 'albums';
    let albumCard = document.querySelector(`#album-cards-container [data-album-id="${albumId}"]`);
    if (!albumCard) {
        containerType = 'singles';
        albumCard = document.querySelector(`#singles-cards-container [data-album-id="${albumId}"]`);
    }

    if (albumCard) {
        updateAlbumCompletionOverlay(completionData, containerType);
        console.log(`✅ [DOWNLOAD COMPLETE] Album ${albumId} set to Downloaded status`);
    } else {
        console.warn(`❌ [DOWNLOAD COMPLETE] Album card not found for ID: "${albumId}"`);
    }
}

/**
 * Set album to downloading status
 */
function setAlbumDownloadingStatus(albumId, downloaded = 0, total = 0) {
    console.log(`🔍 [DOWNLOAD STATUS] Searching for album card with ID: "${albumId}"`);

    const completionData = {
        id: albumId,
        status: 'downloading',
        owned_tracks: downloaded,
        expected_tracks: total,
        name: 'Downloading',
        completion_percentage: Math.round((downloaded / total) * 100) || 0
    };

    // Find if it's in albums or singles container
    let containerType = 'albums';
    let albumCard = document.querySelector(`#album-cards-container [data-album-id="${albumId}"]`);
    if (!albumCard) {
        containerType = 'singles';
        albumCard = document.querySelector(`#singles-cards-container [data-album-id="${albumId}"]`);
    }

    if (albumCard) {
        console.log(`✅ [DOWNLOAD STATUS] Found album card in ${containerType} container, updating overlay`);
        updateAlbumCompletionOverlay(completionData, containerType);
    } else {
        console.warn(`❌ [DOWNLOAD STATUS] Album card not found for ID: "${albumId}"`);
        // Debug: List all available album cards
        const allAlbums = document.querySelectorAll('#album-cards-container [data-album-id], #singles-cards-container [data-album-id]');
        console.log(`🔍 [DEBUG] Available album IDs:`, Array.from(allAlbums).map(card => card.dataset.albumId));
    }
}

/**
 * Show error state on all completion overlays
 */
function showCompletionError() {
    const allOverlays = document.querySelectorAll('.completion-overlay.checking');
    allOverlays.forEach(overlay => {
        overlay.classList.remove('checking');
        overlay.classList.add('error');
        overlay.innerHTML = '<span class="completion-status">Error</span>';
        overlay.title = 'Failed to check completion status';
    });
}

/**
 * Create HTML for an album/single card
 */
function createAlbumCardHTML(album) {
    const imageUrl = album.image_url || '';
    const year = album.release_date ? new Date(album.release_date).getFullYear() : '';
    const type = album.album_type === 'album' ? 'Album' : 
                 album.album_type === 'single' ? 'Single' : 'EP';
    
    // Create a fallback gradient if no image is available
    const backgroundStyle = imageUrl ? 
        `background-image: url('${imageUrl}');` :
        `background: linear-gradient(135deg, rgba(29, 185, 84, 0.2) 0%, rgba(24, 156, 71, 0.1) 100%);`;
    
    return `
        <div class="album-card" data-album-id="${album.id}" data-album-name="${escapeHtml(album.name)}" data-album-type="${album.album_type}" data-total-tracks="${album.total_tracks || 0}">
            <div class="album-card-image" style="${backgroundStyle}"></div>
            <div class="completion-overlay checking">
                <span class="completion-status">Checking...</span>
            </div>
            <div class="album-card-content">
                <div class="album-card-name" title="${escapeHtml(album.name)}">${escapeHtml(album.name)}</div>
                <div class="album-card-year">${year || 'Unknown'}</div>
                <div class="album-card-type">${type}</div>
            </div>
        </div>
    `;
}

/**
 * Initialize artist detail tabs
 */
function initializeArtistTabs() {
    const tabButtons = document.querySelectorAll('.artist-tab');
    const tabContents = document.querySelectorAll('.tab-content');
    
    tabButtons.forEach(button => {
        button.addEventListener('click', () => {
            const tabName = button.getAttribute('data-tab');
            
            // Update button states
            tabButtons.forEach(btn => btn.classList.remove('active'));
            button.classList.add('active');
            
            // Update content states
            tabContents.forEach(content => {
                content.classList.remove('active');
                if (content.id === `${tabName}-content`) {
                    content.classList.add('active');
                }
            });
            
            console.log(`🔄 Switched to ${tabName} tab`);
        });
    });
}

/**
 * State management functions
 */
function showArtistsSearchState() {
    console.log('🔄 Showing search state');
    
    const searchState = document.getElementById('artists-search-state');
    const resultsState = document.getElementById('artists-results-state');
    const detailState = document.getElementById('artist-detail-state');
    
    if (searchState) {
        searchState.classList.remove('hidden', 'fade-out');
    }
    if (resultsState) {
        resultsState.classList.add('hidden');
        resultsState.classList.remove('show');
    }
    if (detailState) {
        detailState.classList.add('hidden');
        detailState.classList.remove('show');
    }
    
    artistsPageState.currentView = 'search';
    updateArtistsSearchStatus('default');
    
    // Show artist downloads section if there are active downloads
    showArtistDownloadsSection();
}

function showArtistsResultsState() {
    console.log('🔄 Showing results state');
    
    // Clear artist-specific data when navigating back to results
    // This ensures that selecting the same artist again will trigger a fresh scan
    if (artistsPageState.selectedArtist) {
        const artistId = artistsPageState.selectedArtist.id;
        console.log(`🗑️ Clearing cached data for artist: ${artistsPageState.selectedArtist.name}`);
        
        // Clear artist-specific cache data
        delete artistsPageState.cache.completionData[artistId];
        delete artistsPageState.cache.discography[artistId];
        
        // Clear artist state
        artistsPageState.selectedArtist = null;
        artistsPageState.artistDiscography = { albums: [], singles: [] };
    }
    
    const searchState = document.getElementById('artists-search-state');
    const resultsState = document.getElementById('artists-results-state');
    const detailState = document.getElementById('artist-detail-state');
    
    if (searchState) {
        searchState.classList.add('fade-out');
        setTimeout(() => searchState.classList.add('hidden'), 200);
    }
    if (resultsState) {
        resultsState.classList.remove('hidden');
        setTimeout(() => resultsState.classList.add('show'), 50);
    }
    if (detailState) {
        detailState.classList.add('hidden');
        detailState.classList.remove('show');
    }
    
    artistsPageState.currentView = 'results';
}

function showArtistDetailState() {
    console.log('🔄 Showing detail state');
    
    const searchState = document.getElementById('artists-search-state');
    const resultsState = document.getElementById('artists-results-state');
    const detailState = document.getElementById('artist-detail-state');
    
    if (searchState) {
        searchState.classList.add('hidden', 'fade-out');
    }
    if (resultsState) {
        resultsState.classList.add('hidden');
        resultsState.classList.remove('show');
    }
    if (detailState) {
        detailState.classList.remove('hidden');
        setTimeout(() => detailState.classList.add('show'), 50);
    }
    
    artistsPageState.currentView = 'detail';
}

/**
 * Update search status text and styling
 */
function updateArtistsSearchStatus(status, message = null) {
    const statusElement = document.getElementById('artists-search-status');
    if (!statusElement) return;
    
    // Clear all status classes
    statusElement.classList.remove('searching', 'error');
    
    switch (status) {
        case 'default':
            statusElement.textContent = 'Start typing to search for artists';
            break;
        case 'searching':
            statusElement.classList.add('searching');
            statusElement.textContent = 'Searching for artists...';
            break;
        case 'error':
            statusElement.classList.add('error');
            statusElement.innerHTML = `
                <div style="margin-bottom: 8px;">${message || 'Search failed. Please try again.'}</div>
                <button onclick="retryLastSearch()" style="
                    background: rgba(29, 185, 84, 0.15);
                    color: rgba(29, 185, 84, 0.9);
                    border: 1px solid rgba(29, 185, 84, 0.3);
                    border-radius: 8px;
                    padding: 4px 12px;
                    font-size: 12px;
                    cursor: pointer;
                    font-family: inherit;
                " onmouseover="this.style.background='rgba(29, 185, 84, 0.25)'" 
                onmouseout="this.style.background='rgba(29, 185, 84, 0.15)'">
                    🔄 Retry Search
                </button>
            `;
            break;
    }
}

/**
 * Retry the last search query
 */
function retryLastSearch() {
    const searchInput = document.getElementById('artists-search-input');
    const headerSearchInput = document.getElementById('artists-header-search-input');
    
    // Get the last search query from either input
    const query = searchInput?.value?.trim() || headerSearchInput?.value?.trim() || artistsPageState.searchQuery;
    
    if (query) {
        console.log(`🔄 Retrying search for: "${query}"`);
        performArtistsSearch(query);
    }
}

/**
 * Update artist detail header with artist info
 */
function updateArtistDetailHeader(artist) {
    const imageElement = document.getElementById('search-artist-detail-image');
    const nameElement = document.getElementById('search-artist-detail-name');
    const genresElement = document.getElementById('search-artist-detail-genres');
    
    if (imageElement && artist.image_url) {
        imageElement.style.backgroundImage = `url('${artist.image_url}')`;
    }
    
    if (nameElement) {
        nameElement.textContent = artist.name;
    }
    
    if (genresElement) {
        const genres = artist.genres?.slice(0, 4).join(' • ') || 'Various genres';
        genresElement.textContent = genres;
    }
    
    // Initialize watchlist button
    initializeArtistDetailWatchlistButton(artist);
}

/**
 * Initialize watchlist button for artist detail page
 */
async function initializeArtistDetailWatchlistButton(artist) {
    const button = document.getElementById('artist-detail-watchlist-btn');
    if (!button) return;
    
    console.log(`🔧 Initializing watchlist button for artist: ${artist.name} (${artist.id})`);
    
    // Reset button state completely
    button.disabled = false;
    button.classList.remove('watching');
    button.style.background = '';
    button.style.cursor = '';
    
    // Remove any existing click handlers to prevent duplicates
    button.onclick = null;
    
    // Set up new click handler
    button.onclick = (event) => toggleArtistDetailWatchlist(event, artist.id, artist.name);
    
    // Check and update current status
    await updateArtistDetailWatchlistButton(artist.id);
}

/**
 * Toggle watchlist status for artist detail page
 */
async function toggleArtistDetailWatchlist(event, artistId, artistName) {
    event.preventDefault();
    
    const button = document.getElementById('artist-detail-watchlist-btn');
    const icon = button.querySelector('.watchlist-icon');
    const text = button.querySelector('.watchlist-text');
    
    // Show loading state
    const originalText = text.textContent;
    text.textContent = 'Loading...';
    button.disabled = true;
    
    try {
        // Check current status
        const checkResponse = await fetch('/api/watchlist/check', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ artist_id: artistId })
        });
        
        const checkData = await checkResponse.json();
        if (!checkData.success) {
            throw new Error(checkData.error || 'Failed to check watchlist status');
        }
        
        const isWatching = checkData.is_watching;
        
        // Toggle watchlist status
        const endpoint = isWatching ? '/api/watchlist/remove' : '/api/watchlist/add';
        const payload = isWatching ? 
            { artist_id: artistId } : 
            { artist_id: artistId, artist_name: artistName };
        
        const response = await fetch(endpoint, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload)
        });
        
        const data = await response.json();
        if (!data.success) {
            throw new Error(data.error || 'Failed to update watchlist');
        }
        
        // Update button appearance
        if (isWatching) {
            // Was watching, now removed
            icon.textContent = '👁️';
            text.textContent = 'Add to Watchlist';
            button.classList.remove('watching');
            console.log(`❌ Removed ${artistName} from watchlist`);
        } else {
            // Was not watching, now added
            icon.textContent = '👁️';
            text.textContent = 'Remove from Watchlist';
            button.classList.add('watching');
            console.log(`✅ Added ${artistName} to watchlist`);
        }
        
        // Update dashboard watchlist count
        updateWatchlistButtonCount();
        
        // Update any visible artist cards
        updateArtistCardWatchlistStatus();
        
    } catch (error) {
        console.error('Error toggling watchlist:', error);
        text.textContent = originalText;
        
        // Show error feedback
        const originalBackground = button.style.background;
        button.style.background = 'rgba(255, 59, 48, 0.3)';
        setTimeout(() => {
            button.style.background = originalBackground;
        }, 2000);
    } finally {
        button.disabled = false;
    }
}

/**
 * Update artist detail watchlist button status
 */
async function updateArtistDetailWatchlistButton(artistId) {
    const button = document.getElementById('artist-detail-watchlist-btn');
    if (!button) {
        console.warn('⚠️ Artist detail watchlist button not found');
        return;
    }
    
    try {
        console.log(`🔍 Checking watchlist status for artist: ${artistId}`);
        
        const response = await fetch('/api/watchlist/check', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ artist_id: artistId })
        });
        
        const data = await response.json();
        if (data.success) {
            const icon = button.querySelector('.watchlist-icon');
            const text = button.querySelector('.watchlist-text');
            
            console.log(`📊 Watchlist status for ${artistId}: ${data.is_watching ? 'WATCHING' : 'NOT WATCHING'}`);
            
            // Ensure button is enabled
            button.disabled = false;
            
            if (data.is_watching) {
                icon.textContent = '👁️';
                text.textContent = 'Remove from Watchlist';
                button.classList.add('watching');
            } else {
                icon.textContent = '👁️';
                text.textContent = 'Add to Watchlist';
                button.classList.remove('watching');
            }
        } else {
            console.error('❌ Failed to check watchlist status:', data.error);
        }
    } catch (error) {
        console.error('❌ Error checking watchlist status:', error);
        // Ensure button doesn't get stuck in bad state
        button.disabled = false;
    }
}

/**
 * Show loading state for discography
 */
function showDiscographyLoading() {
    const albumsContainer = document.getElementById('album-cards-container');
    const singlesContainer = document.getElementById('singles-cards-container');
    
    const loadingHtml = `
        <div class="album-card loading">
            <div class="album-card-image"></div>
            <div class="album-card-content">
                <div class="album-card-name">Loading...</div>
                <div class="album-card-year">-</div>
                <div class="album-card-type">-</div>
            </div>
        </div>
    `.repeat(4);
    
    if (albumsContainer) albumsContainer.innerHTML = loadingHtml;
    if (singlesContainer) singlesContainer.innerHTML = loadingHtml;
}

/**
 * Show error state for discography
 */
function showDiscographyError(message = 'Failed to load discography') {
    const albumsContainer = document.getElementById('album-cards-container');
    const singlesContainer = document.getElementById('singles-cards-container');
    
    const errorHtml = `
        <div style="grid-column: 1 / -1; text-align: center; padding: 40px 20px; color: rgba(255, 65, 54, 0.8);">
            <div style="font-size: 18px; margin-bottom: 8px;">⚠️</div>
            <div style="font-size: 14px; font-weight: 600; margin-bottom: 8px;">Failed to load discography</div>
            <div style="font-size: 12px; color: rgba(255, 65, 54, 0.6); max-width: 300px; margin: 0 auto;">${escapeHtml(message)}</div>
        </div>
    `;
    
    if (albumsContainer) albumsContainer.innerHTML = errorHtml;
    if (singlesContainer) singlesContainer.innerHTML = errorHtml;
}

/**
 * Show loading cards while searching
 */
function showSearchLoadingCards() {
    const container = document.getElementById('artists-cards-container');
    if (!container) return;
    
    const loadingCardHtml = `
        <div class="artist-card loading">
            <div class="artist-card-background"></div>
            <div class="artist-card-overlay"></div>
            <div class="artist-card-content">
                <div class="artist-card-name">Loading...</div>
                <div class="artist-card-genres">Fetching data...</div>
                <div class="artist-card-popularity">
                    <span class="popularity-icon">⏳</span>
                    <span>Loading...</span>
                </div>
            </div>
        </div>
    `;
    
    // Show 6 loading cards
    container.innerHTML = loadingCardHtml.repeat(6);
}

// ===============================
// ARTIST ALBUM DOWNLOAD MISSING TRACKS INTEGRATION
// ===============================

/**
 * Get the completion status of an album from cached data or DOM
 * @param {string} albumId - The album ID
 * @param {string} albumType - The album type ('albums' or 'singles')
 * @returns {Object|null} - Completion status object or null
 */
function getAlbumCompletionStatus(albumId, albumType) {
    try {
        // First, check cached completion data
        const artistId = artistsPageState.selectedArtist?.id;
        if (artistId && artistsPageState.cache.completionData[artistId]) {
            const cachedData = artistsPageState.cache.completionData[artistId];
            const dataArray = albumType === 'albums' ? cachedData.albums : cachedData.singles;

            if (dataArray) {
                const completionData = dataArray.find(item => item.album_id === albumId || item.id === albumId);
                if (completionData) {
                    console.log(`📊 Found cached completion data for album ${albumId}:`, completionData);
                    return completionData;
                }
            }
        }

        // Fallback: Check DOM completion overlay
        const containerId = albumType === 'albums' ? 'album-cards-container' : 'singles-cards-container';
        const container = document.getElementById(containerId);

        if (container) {
            const albumCard = container.querySelector(`[data-album-id="${albumId}"]`);
            if (albumCard) {
                const overlay = albumCard.querySelector('.completion-overlay');
                if (overlay) {
                    // Extract status from overlay classes
                    const classList = Array.from(overlay.classList);
                    const statusClasses = ['completed', 'nearly_complete', 'partial', 'missing', 'downloading', 'downloaded', 'error'];
                    const status = statusClasses.find(cls => classList.includes(cls));

                    if (status) {
                        console.log(`📊 Found DOM completion status for album ${albumId}: ${status}`);
                        return { status, completion_percentage: status === 'completed' ? 100 : 0 };
                    }
                }
            }
        }

        console.warn(`⚠️ No completion status found for album ${albumId}`);
        return null;

    } catch (error) {
        console.error(`❌ Error getting album completion status for ${albumId}:`, error);
        return null;
    }
}

/**
 * Handle album/single/EP click to open download missing tracks modal
 */
async function handleArtistAlbumClick(album, albumType) {
    console.log(`🎵 Album clicked: ${album.name} (${album.album_type}) from artist: ${artistsPageState.selectedArtist?.name}`);

    if (!artistsPageState.selectedArtist) {
        console.error('❌ No selected artist found');
        showToast('Error: No artist selected', 'error');
        return;
    }

    showLoadingOverlay('Loading album...');

    try {
        // Check completion status of the album
        const completionStatus = getAlbumCompletionStatus(album.id, albumType);
        console.log(`📊 Album completion status: ${completionStatus?.status || 'unknown'} (${completionStatus?.completion_percentage || 0}%)`);

        // If album is complete, show informational message and exit
        if (completionStatus?.status === 'completed') {
            hideLoadingOverlay();
            showToast(`${album.name} is already complete in your library`, 'info');
            return;
        }

        // For Artists page, always use Download Missing Tracks modal to analyze and download
        console.log(`🔄 Opening download missing tracks modal for album analysis`);

        // Create virtual playlist ID
        const virtualPlaylistId = `artist_album_${artistsPageState.selectedArtist.id}_${album.id}`;

        // Check if modal already exists and show it
        if (activeDownloadProcesses[virtualPlaylistId]) {
            console.log(`📱 Reopening existing modal for ${album.name}`);
            const process = activeDownloadProcesses[virtualPlaylistId];
            if (process.modalElement) {
                if (process.status === 'complete') {
                    showToast('Showing previous results. Close this modal to start a new analysis.', 'info');
                }
                process.modalElement.style.display = 'flex';
                hideLoadingOverlay();
                return;
            }
        }

        // Create virtual playlist and open modal
        // Note: Don't hide loading overlay here - let the flow continue through to the modal
        await createArtistAlbumVirtualPlaylist(album, albumType);

    } catch (error) {
        hideLoadingOverlay();
        console.error('❌ Error handling album click:', error);
        showToast(`Error opening download modal: ${error.message}`, 'error');
    }
}

/**
 * Create virtual playlist for artist album and open download missing tracks modal
 */
async function createArtistAlbumVirtualPlaylist(album, albumType) {
    const artist = artistsPageState.selectedArtist;
    const virtualPlaylistId = `artist_album_${artist.id}_${album.id}`;
    
    console.log(`🎵 Creating virtual playlist for: ${artist.name} - ${album.name}`);
    
    try {
        // Loading overlay already shown by handleArtistAlbumClick
        
        // Fetch album tracks from backend
        const response = await fetch(`/api/artist/${artist.id}/album/${album.id}/tracks`);
        
        if (!response.ok) {
            if (response.status === 401) {
                throw new Error('Spotify not authenticated. Please check your API settings.');
            }
            throw new Error(`Failed to load album tracks: ${response.status}`);
        }
        
        const data = await response.json();
        
        if (!data.success || !data.tracks || data.tracks.length === 0) {
            throw new Error('No tracks found for this album');
        }
        
        console.log(`✅ Loaded ${data.tracks.length} tracks for ${album.name}`);
        
        // Format playlist name with artist and album info
        const playlistName = `[${artist.name}] ${album.name}`;
        
        // Open download missing tracks modal with formatted tracks
        // Pass false for showLoadingOverlay since we already have one from handleArtistAlbumClick
        await openDownloadMissingModalForArtistAlbum(virtualPlaylistId, playlistName, data.tracks, album, artist, false);
        
        // Track this download for artist bubble management
        registerArtistDownload(artist, album, virtualPlaylistId, albumType);
        
    } catch (error) {
        console.error('❌ Error creating virtual playlist:', error);
        showToast(`Failed to load album: ${error.message}`, 'error');
        throw error;
    }
}

/**
 * Open download missing tracks modal specifically for artist albums
 * Similar to openDownloadMissingModalForYouTube but for artist albums
 */
async function openDownloadMissingModalForArtistAlbum(virtualPlaylistId, playlistName, spotifyTracks, album, artist, showLoadingOverlayParam = true) {
    if (showLoadingOverlayParam) {
        showLoadingOverlay('Loading album...');
    }
    // Check if a process is already active for this virtual playlist
    if (activeDownloadProcesses[virtualPlaylistId]) {
        console.log(`Modal for ${virtualPlaylistId} already exists. Showing it.`);
        const process = activeDownloadProcesses[virtualPlaylistId];
        if (process.modalElement) {
            if (process.status === 'complete') {
                showToast('Showing previous results. Close this modal to start a new analysis.', 'info');
            }
            process.modalElement.style.display = 'flex';
            if (showLoadingOverlayParam) {
                hideLoadingOverlay();
            }
        }
        return;
    }

    console.log(`📥 Opening Download Missing Tracks modal for artist album: ${virtualPlaylistId}`);
    
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

    // Register the new process in our global state tracker using the same structure as other modals
    activeDownloadProcesses[virtualPlaylistId] = {
        status: 'idle',
        modalElement: modal,
        poller: null,
        batchId: null,
        playlist: virtualPlaylist,
        tracks: spotifyTracks,
        // Additional metadata for artist albums
        artist: artist,
        album: album,
        albumType: album.album_type
    };
    
    // Generate hero section for artist album context
    const heroContext = {
        type: 'artist_album',
        artist: artist,
        album: album,
        trackCount: spotifyTracks.length,
        playlistId: virtualPlaylistId
    };

    // Use the exact same modal HTML structure as the existing modals
    modal.innerHTML = `
        <div class="download-missing-modal-content" data-context="artist_album">
            <div class="download-missing-modal-header">
                ${generateDownloadModalHeroSection(heroContext)}
            </div>
            
            <div class="download-missing-modal-body">
                <div class="download-dashboard-stats">
                    <div class="dashboard-stat stat-total">
                        <div class="dashboard-stat-number" id="stat-total-${virtualPlaylistId}">${spotifyTracks.length}</div>
                        <div class="dashboard-stat-label">Total Tracks</div>
                    </div>
                    <div class="dashboard-stat stat-found">
                        <div class="dashboard-stat-number" id="stat-found-${virtualPlaylistId}">-</div>
                        <div class="dashboard-stat-label">Found in Library</div>
                    </div>
                    <div class="dashboard-stat stat-missing">
                        <div class="dashboard-stat-number" id="stat-missing-${virtualPlaylistId}">-</div>
                        <div class="dashboard-stat-label">Missing Tracks</div>
                    </div>
                    <div class="dashboard-stat stat-downloaded">
                        <div class="dashboard-stat-number" id="stat-downloaded-${virtualPlaylistId}">0</div>
                        <div class="dashboard-stat-label">Downloaded</div>
                    </div>
                </div>
                
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
                    </div>
                    <div class="download-tracks-table-container">
                        <table class="download-tracks-table">
                            <thead>
                                <tr>
                                    <th>#</th>
                                    <th>Track Name</th>
                                    <th>Artist(s)</th>
                                    <th>Duration</th>
                                    <th>Library Status</th>
                                    <th>Download Status</th>
                                    <th>Actions</th>
                                </tr>
                            </thead>
                            <tbody id="download-tracks-tbody-${virtualPlaylistId}">
                                ${spotifyTracks.map((track, index) => `
                                    <tr data-track-index="${index}">
                                        <td class="track-number">${index + 1}</td>
                                        <td class="track-name" title="${escapeHtml(track.name)}">${escapeHtml(track.name)}</td>
                                        <td class="track-artist" title="${escapeHtml(track.artists.join(', '))}">${track.artists.join(', ')}</td>
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
                    <div class="force-download-toggle-container" style="margin-bottom: 0px;">
                        <label class="force-download-toggle">
                            <input type="checkbox" id="force-download-all-${virtualPlaylistId}">
                            <span>Force Download All</span>
                        </label>
                    </div>
                    <button class="download-control-btn primary" id="begin-analysis-btn-${virtualPlaylistId}" onclick="startMissingTracksProcess('${virtualPlaylistId}')">
                        Begin Analysis
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

    modal.style.display = 'flex';
    hideLoadingOverlay();

    console.log(`✅ Successfully opened download missing tracks modal for: ${playlistName}`);
}

// ===============================
// ARTIST DOWNLOADS MANAGEMENT SYSTEM
// ===============================

/**
 * Register a new artist download for bubble management
 */
function registerArtistDownload(artist, album, virtualPlaylistId, albumType) {
    console.log(`📝 Registering artist download: ${artist.name} - ${album.name}`);
    
    const artistId = artist.id;
    
    // Initialize artist bubble if it doesn't exist
    if (!artistDownloadBubbles[artistId]) {
        artistDownloadBubbles[artistId] = {
            artist: artist,
            downloads: [],
            element: null,
            hasCompletedDownloads: false
        };
    }
    
    // Add this download to the artist's downloads
    const downloadInfo = {
        virtualPlaylistId: virtualPlaylistId,
        album: album,
        albumType: albumType,
        status: 'in_progress', // 'in_progress', 'completed', 'view_results'
        startTime: new Date()
    };
    
    artistDownloadBubbles[artistId].downloads.push(downloadInfo);
    
    // Show/update the artist downloads section
    updateArtistDownloadsSection();
    
    // Save snapshot of current state
    saveArtistBubbleSnapshot();
    
    // Monitor this download for completion
    monitorArtistDownload(artistId, virtualPlaylistId);
}

/**
 * Debounced update for artist downloads section to prevent rapid updates
 */
function updateArtistDownloadsSection() {
    if (downloadsUpdateTimeout) {
        clearTimeout(downloadsUpdateTimeout);
    }
    downloadsUpdateTimeout = setTimeout(() => {
        showArtistDownloadsSection();
    }, 300); // 300ms debounce
}

// --- Artist Bubble Snapshot System ---

let snapshotSaveTimeout = null; // Debounce snapshot saves

async function saveArtistBubbleSnapshot() {
    /**
     * Saves current artistDownloadBubbles state to backend for persistence.
     * Debounced to prevent excessive backend calls.
     */
    
    // Clear any existing timeout
    if (snapshotSaveTimeout) {
        clearTimeout(snapshotSaveTimeout);
    }
    
    // Debounce the actual save
    snapshotSaveTimeout = setTimeout(async () => {
        try {
            const bubbleCount = Object.keys(artistDownloadBubbles).length;
            
            // Don't save empty state
            if (bubbleCount === 0) {
                console.log('📸 Skipping snapshot save - no artist bubbles to save');
                return;
            }
            
            console.log(`📸 Saving artist bubble snapshot: ${bubbleCount} artists`);
            
            // Prepare snapshot data (clean up DOM references)
            const cleanBubbles = {};
            for (const [artistId, bubbleData] of Object.entries(artistDownloadBubbles)) {
                cleanBubbles[artistId] = {
                    artist: bubbleData.artist,
                    downloads: bubbleData.downloads.map(download => ({
                        virtualPlaylistId: download.virtualPlaylistId,
                        album: download.album,
                        albumType: download.albumType,
                        status: download.status,
                        startTime: download.startTime instanceof Date ? download.startTime.toISOString() : download.startTime
                    })),
                    hasCompletedDownloads: bubbleData.hasCompletedDownloads
                };
            }
            
            const response = await fetch('/api/artist_bubbles/snapshot', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json'
                },
                body: JSON.stringify({
                    bubbles: cleanBubbles
                })
            });
            
            const data = await response.json();
            
            if (data.success) {
                console.log(`✅ Artist bubble snapshot saved: ${bubbleCount} artists`);
            } else {
                console.error('❌ Failed to save artist bubble snapshot:', data.error);
            }
            
        } catch (error) {
            console.error('❌ Error saving artist bubble snapshot:', error);
        }
    }, 1000); // 1 second debounce
}

async function hydrateArtistBubblesFromSnapshot() {
    /**
     * Hydrates artist download bubbles from backend snapshot with live status.
     * Called on page load to restore bubble state.
     */
    try {
        console.log('🔄 Loading artist bubble snapshot from backend...');
        
        const response = await fetch('/api/artist_bubbles/hydrate');
        const data = await response.json();
        
        if (!data.success) {
            console.error('❌ Failed to load artist bubble snapshot:', data.error);
            return;
        }
        
        const bubbles = data.bubbles || {};
        const stats = data.stats || {};
        
        console.log(`🔄 Loaded bubble snapshot: ${stats.total_artists || 0} artists, ${stats.active_downloads || 0} active, ${stats.completed_downloads || 0} completed`);
        
        if (Object.keys(bubbles).length === 0) {
            console.log('ℹ️ No artist bubbles to hydrate');
            return;
        }
        
        // Clear existing state
        artistDownloadBubbles = {};
        
        // Restore artistDownloadBubbles with hydrated data
        for (const [artistId, bubbleData] of Object.entries(bubbles)) {
            artistDownloadBubbles[artistId] = {
                artist: bubbleData.artist,
                downloads: bubbleData.downloads.map(download => ({
                    virtualPlaylistId: download.virtualPlaylistId,
                    album: download.album,
                    albumType: download.albumType,
                    status: download.status, // Live status from backend
                    startTime: new Date(download.startTime)
                })),
                element: null, // Will be created when UI updates
                hasCompletedDownloads: bubbleData.hasCompletedDownloads
            };
            
            console.log(`🔄 Hydrated artist: ${bubbleData.artist.name} (${bubbleData.downloads.length} downloads)`);
            
            // Start monitoring for any in-progress downloads
            for (const download of bubbleData.downloads) {
                if (download.status === 'in_progress') {
                    console.log(`📡 Starting monitoring for: ${download.album.name}`);
                    monitorArtistDownload(artistId, download.virtualPlaylistId);
                }
            }
        }
        
        // Update UI to show hydrated bubbles
        updateArtistDownloadsSection();
        
        const totalArtists = Object.keys(artistDownloadBubbles).length;
        console.log(`✅ Successfully hydrated ${totalArtists} artist download bubbles`);
        
    } catch (error) {
        console.error('❌ Error hydrating artist bubbles from snapshot:', error);
    }
}

/**
 * Show or update the artist downloads section in search state
 */
function showArtistDownloadsSection() {
    console.log(`🔄 [SHOW] showArtistDownloadsSection() called - refreshing artist bubbles`);
    console.log(`🔄 [SHOW] Current view: ${artistsPageState.currentView}, artistDownloadBubbles count: ${Object.keys(artistDownloadBubbles).length}`);
    
    // Only show in search state
    if (artistsPageState.currentView !== 'search') {
        console.log(`⏭️ [SHOW] Skipping - not in search state (current: ${artistsPageState.currentView})`);
        return;
    }
    
    const artistsSearchState = document.getElementById('artists-search-state');
    if (!artistsSearchState) {
        console.log(`⏭️ [SHOW] Skipping - no artists-search-state element found`);
        return;
    }
    
    let downloadsSection = document.getElementById('artist-downloads-section');
    
    // Create section if it doesn't exist
    if (!downloadsSection) {
        downloadsSection = document.createElement('div');
        downloadsSection.id = 'artist-downloads-section';
        downloadsSection.className = 'artist-downloads-section';
        
        // Insert after the search container
        const searchContainer = artistsSearchState.querySelector('.artists-search-container');
        if (searchContainer) {
            searchContainer.insertAdjacentElement('afterend', downloadsSection);
        }
    }
    
    // Count active artists (those with downloads)
    const activeArtists = Object.keys(artistDownloadBubbles).filter(artistId => 
        artistDownloadBubbles[artistId].downloads.length > 0
    );
    
    if (activeArtists.length === 0) {
        downloadsSection.style.display = 'none';
        return;
    }
    
    // Show and populate the section
    downloadsSection.style.display = 'block';
    downloadsSection.innerHTML = `
        <div class="artist-downloads-header">
            <h3 class="artist-downloads-title">Current Downloads</h3>
            <p class="artist-downloads-subtitle">Active download processes</p>
        </div>
        <div class="artist-bubble-container" id="artist-bubble-container">
            ${activeArtists.map(artistId => createArtistBubbleCard(artistDownloadBubbles[artistId])).join('')}
        </div>
    `;
    
    // Add event listeners to bubble cards
    activeArtists.forEach(artistId => {
        const bubbleCard = downloadsSection.querySelector(`[data-artist-id="${artistId}"]`);
        if (bubbleCard) {
            bubbleCard.addEventListener('click', () => openArtistDownloadModal(artistId));
            
            // Add dynamic glow effect
            const artist = artistDownloadBubbles[artistId].artist;
            if (artist.image_url) {
                extractImageColors(artist.image_url, (colors) => {
                    applyDynamicGlow(bubbleCard, colors);
                });
            }
        }
    });
}

/**
 * Create HTML for an artist bubble card
 */
function createArtistBubbleCard(artistBubbleData) {
    const { artist, downloads } = artistBubbleData;
    const activeCount = downloads.filter(d => d.status === 'in_progress').length;
    const completedCount = downloads.filter(d => d.status === 'view_results').length;
    const allCompleted = activeCount === 0 && completedCount > 0;
    
    // Enhanced debug logging for bubble card creation and green checkmark detection
    console.log(`🔵 [BUBBLE] Creating bubble for ${artist.name}:`, {
        totalDownloads: downloads.length,
        activeCount,
        completedCount,
        allCompleted,
        downloadStatuses: downloads.map(d => `${d.album.name}: ${d.status}`)
    });
    
    // CRITICAL: Green checkmark detection logging
    if (allCompleted) {
        console.log(`🟢 [BUBBLE] GREEN CHECKMARK DETECTED for ${artist.name} - all ${downloads.length} downloads completed`);
        console.log(`✅ [BUBBLE] This bubble will have 'all-completed' class and green checkmark`);
    } else if (activeCount === 0 && completedCount === 0) {
        console.log(`⭕ [BUBBLE] No active or completed downloads for ${artist.name} - this shouldn't happen`);
    } else {
        console.log(`⏳ [BUBBLE] Still waiting for completion: ${activeCount} active, ${completedCount} completed`);
    }
    
    const imageUrl = artist.image_url || '';
    const backgroundStyle = imageUrl ? 
        `background-image: url('${imageUrl}');` :
        `background: linear-gradient(135deg, rgba(29, 185, 84, 0.3) 0%, rgba(24, 156, 71, 0.2) 100%);`;
    
    return `
        <div class="artist-bubble-card ${allCompleted ? 'all-completed' : ''}" 
             data-artist-id="${artist.id}"
             title="Click to manage downloads for ${escapeHtml(artist.name)}">
            <div class="artist-bubble-image" style="${backgroundStyle}"></div>
            <div class="artist-bubble-overlay"></div>
            <div class="artist-bubble-content">
                <div class="artist-bubble-name">${escapeHtml(artist.name)}</div>
                <div class="artist-bubble-status">
                    ${activeCount > 0 ? `${activeCount} active` : ''}
                    ${completedCount > 0 ? `${completedCount} completed` : ''}
                </div>
            </div>
            ${allCompleted ? `
                <div class="bulk-complete-indicator" 
                     onclick="event.stopPropagation(); bulkCompleteArtistDownloads('${artist.id}')"
                     title="Complete all downloads">
                    <span class="bulk-complete-icon">✅</span>
                </div>
            ` : ''}
        </div>
    `;
}

/**
 * Monitor an artist download for completion status changes
 */
function monitorArtistDownload(artistId, virtualPlaylistId) {
    // Check if the download process exists and monitor its status
    const checkStatus = () => {
        const process = activeDownloadProcesses[virtualPlaylistId];
        if (!process || !artistDownloadBubbles[artistId]) {
            return; // Process or artist bubble no longer exists
        }
        
        // Find this download in the artist's downloads
        const download = artistDownloadBubbles[artistId].downloads.find(d => d.virtualPlaylistId === virtualPlaylistId);
        if (!download) return;
        
        // Update download status based on process status
        if (process.status === 'complete' && download.status === 'in_progress') {
            download.status = 'view_results';
            console.log(`✅ Download completed for ${artistDownloadBubbles[artistId].artist.name} - ${download.album.name}`);
            console.log(`📊 Artist ${artistId} downloads status:`, artistDownloadBubbles[artistId].downloads.map(d => `${d.album.name}: ${d.status}`));
            
            // Update the downloads section
            updateArtistDownloadsSection();
            
            // Save snapshot of updated state
            saveArtistBubbleSnapshot();
            
            // Check if all downloads for this artist are now completed
            const artistDownloads = artistDownloadBubbles[artistId].downloads;
            const allCompleted = artistDownloads.every(d => d.status === 'view_results');
            if (allCompleted) {
                console.log(`🟢 All downloads completed for ${artistDownloadBubbles[artistId].artist.name} - green checkmark should appear`);
                console.log(`🎯 [STATUS DEBUG] Green checkmark trigger - forcing bubble refresh`);
                // Force immediate bubble refresh to show green checkmark
                setTimeout(updateArtistDownloadsSection, 100);
            }
        }
        
        // Continue monitoring if still active
        if (process.status !== 'complete') {
            setTimeout(checkStatus, 2000); // Check every 2 seconds
        }
    };
    
    // Start monitoring after a brief delay
    setTimeout(checkStatus, 1000);
}

/**
 * Open the artist download management modal
 */
function openArtistDownloadModal(artistId) {
    const artistBubbleData = artistDownloadBubbles[artistId];
    if (!artistBubbleData || artistDownloadModalOpen) return;

    console.log(`🎵 [MODAL OPEN] Opening artist download modal for: ${artistBubbleData.artist.name}`);
    console.log(`📊 [MODAL OPEN] Current download statuses:`, artistBubbleData.downloads.map(d => `${d.album.name}: ${d.status}`));
    artistDownloadModalOpen = true;

    const modal = document.createElement('div');
    modal.id = 'artist-download-management-modal';
    modal.className = 'artist-download-management-modal';
    modal.innerHTML = `
        <div class="artist-download-modal-content">
            <div class="artist-download-modal-hero">
                <div class="artist-download-modal-hero-bg" ${artistBubbleData.artist.image_url ? `style="background-image: url('${escapeHtml(artistBubbleData.artist.image_url)}')"` : ''}></div>
                <div class="artist-download-modal-hero-overlay">
                    <div class="artist-download-modal-hero-content">
                        <div class="artist-download-modal-hero-avatar">
                            ${artistBubbleData.artist.image_url
                                ? `<img src="${escapeHtml(artistBubbleData.artist.image_url)}" alt="${escapeHtml(artistBubbleData.artist.name)}" class="artist-download-modal-hero-image" loading="lazy">`
                                : '<div class="artist-download-modal-hero-fallback"><i class="fas fa-user-music"></i></div>'
                            }
                        </div>
                        <div class="artist-download-modal-hero-info">
                            <h2 class="artist-download-modal-hero-title">${escapeHtml(artistBubbleData.artist.name)}</h2>
                            <p class="artist-download-modal-hero-subtitle">${artistBubbleData.downloads.length} active download${artistBubbleData.downloads.length !== 1 ? 's' : ''}</p>
                        </div>
                    </div>
                    <span class="artist-download-modal-close" onclick="closeArtistDownloadModal()">&times;</span>
                </div>
            </div>

            <div class="artist-download-modal-body">
                <div class="artist-download-items" id="artist-download-items-${artistId}">
                    ${artistBubbleData.downloads.map((download, index) => createArtistDownloadItem(download, index)).join('')}
                </div>
            </div>
        </div>
        <div class="artist-download-modal-overlay" onclick="closeArtistDownloadModal()"></div>
    `;

    document.body.appendChild(modal);
    modal.style.display = 'flex';
    
    // Monitor for real-time updates
    startArtistDownloadModalMonitoring(artistId);
}

/**
 * Create HTML for an individual download item in the artist modal
 */
function createArtistDownloadItem(download, index) {
    const { album, albumType, status, virtualPlaylistId } = download;
    const buttonText = status === 'view_results' ? 'View Results' : 'View Progress';
    const buttonClass = status === 'view_results' ? 'completed' : 'active';

    // Enhanced debugging for button text generation
    console.log(`🎯 [BUTTON] Creating item for ${album.name}: status='${status}' → buttonText='${buttonText}'`);

    return `
        <div class="artist-download-item" data-playlist-id="${virtualPlaylistId}">
            <div class="download-item-artwork">
                ${album.image_url
                    ? `<img src="${escapeHtml(album.image_url)}" alt="${escapeHtml(album.name)}" class="download-item-image" loading="lazy">`
                    : `<div class="download-item-fallback">
                         <i class="fas fa-${albumType === 'album' ? 'compact-disc' : albumType === 'single' ? 'music' : 'record-vinyl'}"></i>
                       </div>`
                }
            </div>
            <div class="download-item-info">
                <div class="download-item-name">${escapeHtml(album.name)}</div>
                <div class="download-item-type">${albumType === 'album' ? 'Album' : albumType === 'single' ? 'Single' : 'EP'}</div>
            </div>
            <div class="download-item-actions">
                <button class="download-item-btn ${buttonClass}"
                        onclick="openArtistDownloadProcess('${virtualPlaylistId}')">
                    ${buttonText}
                </button>
            </div>
        </div>
    `;
}

/**
 * Monitor artist download modal for real-time updates
 */
function startArtistDownloadModalMonitoring(artistId) {
    if (!artistDownloadModalOpen) return;
    
    const updateModal = () => {
        const modal = document.getElementById('artist-download-management-modal');
        const itemsContainer = document.getElementById(`artist-download-items-${artistId}`);
        
        if (!modal || !itemsContainer || !artistDownloadBubbles[artistId]) return;
        
        // Check for completed downloads that need to be removed
        const activeDownloads = artistDownloadBubbles[artistId].downloads.filter(download => {
            const process = activeDownloadProcesses[download.virtualPlaylistId];
            // Keep if process exists or if it's completed but not yet cleaned up
            return process !== undefined;
        });
        
        // Update the downloads array
        artistDownloadBubbles[artistId].downloads = activeDownloads;
        
        // If no downloads left, close modal
        if (activeDownloads.length === 0) {
            closeArtistDownloadModal();
            return;
        }
        
        // Update modal content and synchronize with bubble state
        let statusChanged = false;
        itemsContainer.innerHTML = activeDownloads.map((download, index) => {
            const process = activeDownloadProcesses[download.virtualPlaylistId];
            if (process) {
                const newStatus = process.status === 'complete' ? 'view_results' : 'in_progress';
                if (download.status !== newStatus) {
                    console.log(`🔄 [ARTIST MODAL] Updating ${download.album.name} status from ${download.status} to ${newStatus}`);
                    download.status = newStatus;
                    statusChanged = true;
                }
            }
            return createArtistDownloadItem(download, index);
        }).join('');
        
        // CRITICAL: If any status changed, immediately refresh artist bubble to show green checkmarks
        if (statusChanged) {
            console.log(`🎯 [SYNC] Status change detected in artist modal - refreshing bubble display`);
            updateArtistDownloadsSection();
            
            // Check if all downloads for this artist are now completed
            const artistDownloads = artistDownloadBubbles[artistId].downloads;
            const allCompleted = artistDownloads.every(d => d.status === 'view_results');
            if (allCompleted) {
                console.log(`🟢 [ARTIST MODAL] All downloads completed for artist ${artistId} - triggering green checkmark`);
                // Force additional refresh after a brief delay to ensure UI updates
                setTimeout(() => {
                    console.log(`✨ [ARTIST MODAL] Forcing final refresh for green checkmark`);
                    updateArtistDownloadsSection();
                }, 200);
            }
        }
        
        // Continue monitoring
        setTimeout(updateModal, 2000);
    };
    
    setTimeout(updateModal, 1000);
}

/**
 * Open a specific artist download process modal
 */
function openArtistDownloadProcess(virtualPlaylistId) {
    const process = activeDownloadProcesses[virtualPlaylistId];
    if (process && process.modalElement) {
        // Close artist management modal first
        closeArtistDownloadModal();
        
        // Show the download process modal
        process.modalElement.style.display = 'flex';
        
        if (process.status === 'complete') {
            showToast('Review download results and click "Close" to finish.', 'info');
        }
    }
}

/**
 * Close the artist download management modal
 */
function closeArtistDownloadModal() {
    const modal = document.getElementById('artist-download-management-modal');
    if (modal) {
        modal.remove();
    }
    artistDownloadModalOpen = false;
}

/**
 * Bulk complete all downloads for an artist (when all are in 'view_results' state)
 */
function bulkCompleteArtistDownloads(artistId) {
    console.log(`🎯 Bulk completing downloads for artist: ${artistId}`);
    
    const artistBubbleData = artistDownloadBubbles[artistId];
    if (!artistBubbleData) {
        console.warn(`❌ No artist bubble data found for ${artistId}`);
        return;
    }
    
    // Find all downloads in 'view_results' state
    const completedDownloads = artistBubbleData.downloads.filter(d => d.status === 'view_results');
    console.log(`📋 Found ${completedDownloads.length} completed downloads to close:`, 
                completedDownloads.map(d => d.album.name));
    
    if (completedDownloads.length === 0) {
        console.warn(`⚠️ No completed downloads found for bulk close`);
        showToast('No completed downloads to close', 'info');
        return;
    }
    
    // Programmatically close all completed modals
    completedDownloads.forEach(download => {
        const process = activeDownloadProcesses[download.virtualPlaylistId];
        if (process && process.modalElement) {
            console.log(`🗑️ Closing modal for: ${download.album.name}`);
            // Trigger the close function which handles cleanup
            closeDownloadMissingModal(download.virtualPlaylistId);
        } else {
            console.warn(`⚠️ No active process or modal found for: ${download.album.name}`);
        }
    });
    
    showToast(`Completed ${completedDownloads.length} downloads for ${artistBubbleData.artist.name}`, 'success');
}

/**
 * Clean up artist download when a modal is closed
 */
function cleanupArtistDownload(virtualPlaylistId) {
    console.log(`🔍 [CLEANUP] Looking for download to cleanup: ${virtualPlaylistId}`);
    console.log(`🔍 [CLEANUP] Current artist bubbles:`, Object.keys(artistDownloadBubbles));
    
    // Find which artist this download belongs to
    for (const artistId in artistDownloadBubbles) {
        const downloads = artistDownloadBubbles[artistId].downloads;
        const downloadIndex = downloads.findIndex(d => d.virtualPlaylistId === virtualPlaylistId);
        
        console.log(`🔍 [CLEANUP] Checking artist ${artistId}: ${downloads.length} downloads`);
        downloads.forEach(d => console.log(`  - ${d.album.name} (${d.virtualPlaylistId}): ${d.status}`));
        
        if (downloadIndex !== -1) {
            const downloadToRemove = downloads[downloadIndex];
            console.log(`🧹 [CLEANUP] Found download to cleanup: ${downloadToRemove.album.name} (status: ${downloadToRemove.status})`);
            
            // Remove this download from the artist's downloads
            downloads.splice(downloadIndex, 1);
            console.log(`✅ [CLEANUP] Removed download from artist ${artistId}. Remaining: ${downloads.length}`);
            
            // If no more downloads for this artist, remove the bubble
            if (downloads.length === 0) {
                delete artistDownloadBubbles[artistId];
                console.log(`🧹 [CLEANUP] No more downloads - removed artist bubble: ${artistId}`);
            } else {
                console.log(`📊 [CLEANUP] Artist ${artistId} still has ${downloads.length} downloads remaining`);
            }
            
            // Update the downloads section
            console.log(`🔄 [CLEANUP] Updating artist downloads section...`);
            updateArtistDownloadsSection();
            
            // Save snapshot of updated state
            saveArtistBubbleSnapshot();
            break;
        }
    }
    console.log(`✅ [CLEANUP] Cleanup process completed for ${virtualPlaylistId}`);
}

/**
 * Force refresh all artist download statuses (useful for debugging)
 */
function refreshAllArtistDownloadStatuses() {
    console.log('🔄 Force refreshing all artist download statuses...');
    
    for (const artistId in artistDownloadBubbles) {
        const artistData = artistDownloadBubbles[artistId];
        let hasChanges = false;
        
        artistData.downloads.forEach(download => {
            const process = activeDownloadProcesses[download.virtualPlaylistId];
            if (process) {
                const expectedStatus = process.status === 'complete' ? 'view_results' : 'in_progress';
                if (download.status !== expectedStatus) {
                    console.log(`🔧 Fixing status for ${download.album.name}: ${download.status} → ${expectedStatus}`);
                    download.status = expectedStatus;
                    hasChanges = true;
                }
            }
        });
        
        if (hasChanges) {
            console.log(`✅ Updated statuses for ${artistData.artist.name}`);
        }
    }
    
    // Force update the downloads section
    showArtistDownloadsSection();
}

/**
 * Extract dominant colors from an image for dynamic glow effects
 */
async function extractImageColors(imageUrl, callback) {
    if (!imageUrl) {
        callback(['#1db954', '#1ed760']); // Fallback to Spotify green
        return;
    }
    
    // Check cache first for performance
    if (artistsPageState.cache.colors[imageUrl]) {
        callback(artistsPageState.cache.colors[imageUrl]);
        return;
    }
    
    try {
        // Create a canvas to analyze the image
        const canvas = document.createElement('canvas');
        const ctx = canvas.getContext('2d');
        const img = new Image();
        
        img.crossOrigin = 'anonymous';
        
        img.onload = function() {
            // Resize to small dimensions for faster processing
            const size = 50;
            canvas.width = size;
            canvas.height = size;
            
            // Draw image to canvas
            ctx.drawImage(img, 0, 0, size, size);
            
            try {
                // Get image data
                const imageData = ctx.getImageData(0, 0, size, size);
                const data = imageData.data;
                
                // Extract colors (sample every few pixels for performance)
                const colors = [];
                for (let i = 0; i < data.length; i += 16) { // Sample every 4th pixel
                    const r = data[i];
                    const g = data[i + 1];
                    const b = data[i + 2];
                    const alpha = data[i + 3];
                    
                    // Skip transparent or very dark pixels
                    if (alpha > 128 && (r + g + b) > 150) {
                        colors.push({ r, g, b });
                    }
                }
                
                if (colors.length === 0) {
                    callback(['#1db954', '#1ed760']); // Fallback
                    return;
                }
                
                // Find dominant colors using a simple clustering approach
                const dominantColors = findDominantColors(colors, 2);
                
                // Convert to CSS hex colors
                const hexColors = dominantColors.map(color => 
                    `#${((1 << 24) + (color.r << 16) + (color.g << 8) + color.b).toString(16).slice(1)}`
                );
                
                // Cache the colors for future use
                artistsPageState.cache.colors[imageUrl] = hexColors;
                
                callback(hexColors);
                
            } catch (e) {
                console.warn('Color extraction failed, using fallback colors:', e);
                callback(['#1db954', '#1ed760']);
            }
        };
        
        img.onerror = function() {
            callback(['#1db954', '#1ed760']); // Fallback on error
        };
        
        img.src = imageUrl;
        
    } catch (error) {
        console.warn('Image color extraction error:', error);
        callback(['#1db954', '#1ed760']);
    }
}

/**
 * Simple color clustering to find dominant colors
 */
function findDominantColors(colors, numColors = 2) {
    if (colors.length === 0) return [{ r: 29, g: 185, b: 84 }];
    
    // Simple k-means clustering
    let centroids = [];
    
    // Initialize centroids randomly
    for (let i = 0; i < numColors; i++) {
        centroids.push(colors[Math.floor(Math.random() * colors.length)]);
    }
    
    // Run a few iterations of k-means
    for (let iteration = 0; iteration < 5; iteration++) {
        const clusters = Array(numColors).fill().map(() => []);
        
        // Assign each color to nearest centroid
        colors.forEach(color => {
            let minDistance = Infinity;
            let nearestCluster = 0;
            
            centroids.forEach((centroid, i) => {
                const distance = Math.sqrt(
                    Math.pow(color.r - centroid.r, 2) +
                    Math.pow(color.g - centroid.g, 2) +
                    Math.pow(color.b - centroid.b, 2)
                );
                
                if (distance < minDistance) {
                    minDistance = distance;
                    nearestCluster = i;
                }
            });
            
            clusters[nearestCluster].push(color);
        });
        
        // Update centroids
        centroids = clusters.map(cluster => {
            if (cluster.length === 0) return centroids[0]; // Fallback
            
            const avgR = cluster.reduce((sum, c) => sum + c.r, 0) / cluster.length;
            const avgG = cluster.reduce((sum, c) => sum + c.g, 0) / cluster.length;
            const avgB = cluster.reduce((sum, c) => sum + c.b, 0) / cluster.length;
            
            return { r: Math.round(avgR), g: Math.round(avgG), b: Math.round(avgB) };
        });
    }
    
    // Ensure we have vibrant colors by boosting saturation
    return centroids.map(color => {
        const max = Math.max(color.r, color.g, color.b);
        const min = Math.min(color.r, color.g, color.b);
        const saturation = max === 0 ? 0 : (max - min) / max;
        
        // Boost low saturation colors
        if (saturation < 0.4) {
            const factor = 1.3;
            return {
                r: Math.min(255, Math.round(color.r * factor)),
                g: Math.min(255, Math.round(color.g * factor)),
                b: Math.min(255, Math.round(color.b * factor))
            };
        }
        
        return color;
    });
}

/**
 * Apply dynamic glow effect to a card element
 */
function applyDynamicGlow(cardElement, colors) {
    if (!cardElement || colors.length < 2) return;
    
    const color1 = colors[0];
    const color2 = colors[1];
    
    // Add a small delay to make the effect feel more natural
    setTimeout(() => {
        // Create CSS custom properties for the dynamic colors
        cardElement.style.setProperty('--glow-color-1', color1);
        cardElement.style.setProperty('--glow-color-2', color2);
        cardElement.classList.add('has-dynamic-glow');
        
        console.log(`🎨 Applied dynamic glow: ${color1}, ${color2}`);
    }, Math.random() * 200 + 100); // Random delay between 100-300ms
}

/**
 * Utility function to escape HTML
 */
function escapeHtml(text) {
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}

// --- Service Status and System Stats Functions ---

async function fetchAndUpdateServiceStatus() {
    try {
        const response = await fetch('/status');
        if (!response.ok) return;
        
        const data = await response.json();
        
        // Update service status indicators and text (dashboard)
        updateServiceStatus('spotify', data.spotify);
        updateServiceStatus('media-server', data.media_server);
        updateServiceStatus('soulseek', data.soulseek);
        
        // Update sidebar service status indicators
        updateSidebarServiceStatus('spotify', data.spotify);
        updateSidebarServiceStatus('media-server', data.media_server);
        updateSidebarServiceStatus('soulseek', data.soulseek);
        
    } catch (error) {
        console.warn('Could not fetch service status:', error);
    }
}

function updateServiceStatus(service, statusData) {
    const indicator = document.getElementById(`${service}-status-indicator`);
    const statusText = document.getElementById(`${service}-status-text`);
    
    if (indicator && statusText) {
        if (statusData.connected) {
            indicator.className = 'service-card-indicator connected';
            statusText.textContent = `Connected (${statusData.response_time}ms)`;
            statusText.className = 'service-card-status-text connected';
        } else {
            indicator.className = 'service-card-indicator disconnected';
            statusText.textContent = 'Disconnected';
            statusText.className = 'service-card-status-text disconnected';
        }
    }
}

function updateSidebarServiceStatus(service, statusData) {
    const indicator = document.getElementById(`${service}-indicator`);
    if (indicator) {
        const dot = indicator.querySelector('.status-dot');
        const nameElement = indicator.querySelector('.status-name');
        
        if (dot) {
            if (statusData.connected) {
                dot.className = 'status-dot connected';
            } else {
                dot.className = 'status-dot disconnected';
            }
        }
        
        // Update media server name if it's the media server indicator
        if (service === 'media-server' && statusData.type) {
            const mediaServerNameElement = document.getElementById('media-server-name');
            if (mediaServerNameElement) {
                const serverName = statusData.type.charAt(0).toUpperCase() + statusData.type.slice(1);
                mediaServerNameElement.textContent = serverName;
            }
        }
    }
}

async function fetchAndUpdateSystemStats() {
    try {
        const response = await fetch('/api/system/stats');
        if (!response.ok) return;
        
        const data = await response.json();
        
        // Update all stat cards
        updateStatCard('active-downloads-card', data.active_downloads, 'Currently downloading');
        updateStatCard('finished-downloads-card', data.finished_downloads, 'Completed this session');
        updateStatCard('download-speed-card', data.download_speed, 'Combined speed');
        updateStatCard('active-syncs-card', data.active_syncs, 'Playlists syncing');
        updateStatCard('uptime-card', data.uptime, 'Application runtime');
        updateStatCard('memory-card', data.memory_usage, 'Current usage');
        
    } catch (error) {
        console.warn('Could not fetch system stats:', error);
    }
}

function updateStatCard(cardId, value, subtitle) {
    const card = document.getElementById(cardId);
    if (card) {
        const valueElement = card.querySelector('.stat-card-value');
        const subtitleElement = card.querySelector('.stat-card-subtitle');
        
        if (valueElement) {
            valueElement.textContent = value;
        }
        if (subtitleElement) {
            subtitleElement.textContent = subtitle;
        }
    }
}

async function fetchAndUpdateActivityFeed() {
    try {
        const response = await fetch('/api/activity/feed');
        if (!response.ok) {
            console.warn('Activity feed response not ok:', response.status, response.statusText);
            return;
        }
        
        const data = await response.json();
        console.log('Activity feed data received:', data);
        updateActivityFeed(data.activities || []);
        
    } catch (error) {
        console.warn('Could not fetch activity feed:', error);
    }
}

function updateActivityFeed(activities) {
    const feedContainer = document.getElementById('dashboard-activity-feed');
    if (!feedContainer) {
        console.warn('Activity feed container not found!');
        return;
    }
    
    console.log('Updating activity feed with', activities.length, 'activities:', activities);
    
    // Clear existing content
    feedContainer.innerHTML = '';
    
    if (activities.length === 0) {
        console.log('No activities found, showing placeholder');
        // Show placeholder if no activities
        feedContainer.innerHTML = `
            <div class="activity-item">
                <span class="activity-icon">📊</span>
                <div class="activity-text-content">
                    <p class="activity-title">System Started</p>
                    <p class="activity-subtitle">Dashboard initialized successfully</p>
                </div>
                <p class="activity-time">Now</p>
            </div>
        `;
        return;
    }
    
    // Add activities (limit to 5 most recent)
    activities.slice(0, 5).forEach((activity, index) => {
        const activityElement = document.createElement('div');
        activityElement.className = 'activity-item';
        activityElement.innerHTML = `
            <span class="activity-icon">${escapeHtml(activity.icon)}</span>
            <div class="activity-text-content">
                <p class="activity-title">${escapeHtml(activity.title)}</p>
                <p class="activity-subtitle">${escapeHtml(activity.subtitle)}</p>
            </div>
            <p class="activity-time">${escapeHtml(activity.time)}</p>
        `;
        
        feedContainer.appendChild(activityElement);
        
        // Add separator between items (except after last item)
        if (index < activities.slice(0, 5).length - 1) {
            const separator = document.createElement('div');
            separator.className = 'activity-separator';
            feedContainer.appendChild(separator);
        }
    });
}

async function checkForActivityToasts() {
    try {
        const response = await fetch('/api/activity/toasts');
        if (!response.ok) return;
        
        const data = await response.json();
        const toasts = data.toasts || [];
        
        toasts.forEach(activity => {
            // Convert activity to toast type based on icon/title
            let toastType = 'info';
            if (activity.icon === '✅' || activity.title.includes('Complete')) {
                toastType = 'success';
            } else if (activity.icon === '❌' || activity.title.includes('Failed') || activity.title.includes('Error')) {
                toastType = 'error';
            } else if (activity.icon === '🚫' || activity.title.includes('Cancelled')) {
                toastType = 'warning';
            }
            
            // Show toast with activity info
            showToast(`${activity.title}: ${activity.subtitle}`, toastType);
        });
        
    } catch (error) {
        // Silently fail for toast checking to avoid spam
    }
}

// --- Watchlist Functions ---

/**
 * Toggle an artist's watchlist status
 */
async function toggleWatchlist(event, artistId, artistName) {
    // Prevent event bubbling to parent card
    event.stopPropagation();
    
    const button = event.currentTarget;
    const icon = button.querySelector('.watchlist-icon');
    const text = button.querySelector('.watchlist-text');
    
    // Show loading state
    const originalText = text.textContent;
    text.textContent = 'Loading...';
    button.disabled = true;
    
    try {
        // Check current status
        const checkResponse = await fetch('/api/watchlist/check', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ artist_id: artistId })
        });
        
        const checkData = await checkResponse.json();
        if (!checkData.success) {
            throw new Error(checkData.error || 'Failed to check watchlist status');
        }
        
        const isWatching = checkData.is_watching;
        
        // Toggle watchlist status
        const endpoint = isWatching ? '/api/watchlist/remove' : '/api/watchlist/add';
        const payload = isWatching ? 
            { artist_id: artistId } : 
            { artist_id: artistId, artist_name: artistName };
        
        const response = await fetch(endpoint, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload)
        });
        
        const data = await response.json();
        if (!data.success) {
            throw new Error(data.error || 'Failed to update watchlist');
        }
        
        // Update button appearance
        if (isWatching) {
            // Was watching, now removed
            icon.textContent = '👁️';
            text.textContent = 'Add to Watchlist';
            button.classList.remove('watching');
            console.log(`❌ Removed ${artistName} from watchlist`);
        } else {
            // Was not watching, now added
            icon.textContent = '👁️';
            text.textContent = 'Watching...';
            button.classList.add('watching');
            console.log(`✅ Added ${artistName} to watchlist`);
        }
        
        // Update dashboard watchlist count
        updateWatchlistButtonCount();
        
    } catch (error) {
        console.error('Error toggling watchlist:', error);
        text.textContent = originalText;
        
        // Show error feedback
        const originalBackground = button.style.background;
        button.style.background = 'rgba(255, 59, 48, 0.3)';
        setTimeout(() => {
            button.style.background = originalBackground;
        }, 2000);
    } finally {
        button.disabled = false;
    }
}

/**
 * Update the watchlist button count on dashboard
 */
async function updateWatchlistButtonCount() {
    try {
        const response = await fetch('/api/watchlist/count');
        const data = await response.json();
        
        if (data.success) {
            const watchlistButton = document.getElementById('watchlist-button');
            if (watchlistButton) {
                watchlistButton.textContent = `👁️ Watchlist (${data.count})`;
            }
        }
    } catch (error) {
        console.error('Error updating watchlist count:', error);
    }
}

/**
 * Check and update watchlist status for all visible artist cards
 */
async function updateArtistCardWatchlistStatus() {
    const artistCards = document.querySelectorAll('.artist-card');
    
    for (const card of artistCards) {
        const artistId = card.dataset.artistId;
        if (!artistId) continue;
        
        try {
            const response = await fetch('/api/watchlist/check', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ artist_id: artistId })
            });
            
            const data = await response.json();
            if (data.success) {
                const button = card.querySelector('.watchlist-toggle-btn');
                const icon = button.querySelector('.watchlist-icon');
                const text = button.querySelector('.watchlist-text');
                
                if (data.is_watching) {
                    icon.textContent = '👁️';
                    text.textContent = 'Watching...';
                    button.classList.add('watching');
                } else {
                    icon.textContent = '👁️';
                    text.textContent = 'Add to Watchlist';
                    button.classList.remove('watching');
                }
            }
        } catch (error) {
            console.error(`Error checking watchlist status for artist ${artistId}:`, error);
        }
    }
}

/**
 * Show watchlist modal
 */
async function showWatchlistModal() {
    try {
        // Check if watchlist has any artists
        const countResponse = await fetch('/api/watchlist/count');
        const countData = await countResponse.json();
        
        if (!countData.success) {
            console.error('Error getting watchlist count:', countData.error);
            return;
        }
        
        if (countData.count === 0) {
            // Show empty state message
            alert('Your watchlist is empty!\n\nAdd artists to your watchlist from the Artists page to monitor them for new releases.');
            return;
        }
        
        // Get watchlist artists
        const artistsResponse = await fetch('/api/watchlist/artists');
        const artistsData = await artistsResponse.json();
        
        if (!artistsData.success) {
            console.error('Error getting watchlist artists:', artistsData.error);
            return;
        }
        
        // Create modal if it doesn't exist
        let modal = document.getElementById('watchlist-modal');
        if (!modal) {
            modal = document.createElement('div');
            modal.id = 'watchlist-modal';
            modal.className = 'modal-overlay';
            document.body.appendChild(modal);
        }
        
        // Get scan status
        const statusResponse = await fetch('/api/watchlist/scan/status');
        const statusData = await statusResponse.json();
        const scanStatus = statusData.success ? statusData.status : 'idle';
        
        // Build modal content
        modal.innerHTML = `
            <div class="modal-container playlist-modal">
                <div class="playlist-modal-header">
                    <div class="playlist-header-content">
                        <h2>👁️ Watchlist</h2>
                        <div class="playlist-quick-info">
                            <span class="playlist-track-count">${countData.count} artist${countData.count !== 1 ? 's' : ''}</span>
                        </div>
                        <div class="playlist-modal-sync-status" id="watchlist-scan-status" style="display: ${scanStatus !== 'idle' ? 'block' : 'none'};">
                            <div class="scan-status-main">
                                <span class="sync-stat"><span id="scan-status-text">${scanStatus}</span></span>
                            </div>
                            ${statusData.summary ? `
                                <div class="scan-status-summary" style="margin-top: 8px; font-size: 13px; opacity: 0.8;">
                                    <span class="sync-stat">Artists: ${statusData.summary.total_artists || 0}</span>
                                    <span class="sync-separator"> • </span>
                                    <span class="sync-stat">New tracks: ${statusData.summary.new_tracks_found || 0}</span>
                                    <span class="sync-separator"> • </span>
                                    <span class="sync-stat">Added to wishlist: ${statusData.summary.tracks_added_to_wishlist || 0}</span>
                                </div>
                            ` : ''}
                        </div>
                    </div>
                    <span class="playlist-modal-close" onclick="closeWatchlistModal()">&times;</span>
                </div>
                
                <div class="playlist-modal-body">
                    <div class="watchlist-actions" style="margin-bottom: 20px;">
                        <button class="playlist-modal-btn playlist-modal-btn-primary" 
                                id="scan-watchlist-btn" 
                                onclick="startWatchlistScan()"
                                ${scanStatus === 'scanning' ? 'disabled' : ''}>
                            ${scanStatus === 'scanning' ? 'Scanning...' : 'Scan for New Releases'}
                        </button>
                    </div>
                    
                    <div class="watchlist-artists-list">
                        ${artistsData.artists.map(artist => `
                            <div class="watchlist-artist-item">
                                <div class="watchlist-artist-info">
                                    <span class="watchlist-artist-name">${escapeHtml(artist.artist_name)}</span>
                                    <span class="watchlist-artist-date">Added ${new Date(artist.date_added).toLocaleDateString()}</span>
                                    ${artist.last_scan_timestamp ? `
                                        <span class="watchlist-artist-scan">Last scanned ${new Date(artist.last_scan_timestamp).toLocaleDateString()}</span>
                                    ` : ''}
                                </div>
                                <button class="playlist-modal-btn playlist-modal-btn-secondary watchlist-remove-btn"
                                        data-artist-id="${artist.spotify_artist_id}"
                                        data-artist-name="${escapeHtml(artist.artist_name)}">
                                    Remove
                                </button>
                            </div>
                        `).join('')}
                    </div>
                </div>
                
                <div class="playlist-modal-footer">
                    <button class="playlist-modal-btn playlist-modal-btn-secondary" onclick="closeWatchlistModal()">
                        Close
                    </button>
                </div>
            </div>
        `;

        // Add event listeners for remove buttons
        modal.querySelectorAll('.watchlist-remove-btn').forEach(button => {
            button.addEventListener('click', () => {
                const artistId = button.getAttribute('data-artist-id');
                const artistName = button.getAttribute('data-artist-name');
                removeFromWatchlistModal(artistId, artistName);
            });
        });

        // Show modal
        modal.style.display = 'flex';
        
        // Start polling for scan status if scanning
        if (scanStatus === 'scanning') {
            pollWatchlistScanStatus();
        }
        
    } catch (error) {
        console.error('Error showing watchlist modal:', error);
    }
}

/**
 * Close watchlist modal
 */
function closeWatchlistModal() {
    const modal = document.getElementById('watchlist-modal');
    if (modal) {
        modal.style.display = 'none';
    }
}

/**
 * Start watchlist scan
 */
async function startWatchlistScan() {
    try {
        const button = document.getElementById('scan-watchlist-btn');
        button.disabled = true;
        button.textContent = 'Starting scan...';
        
        const response = await fetch('/api/watchlist/scan', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' }
        });
        
        const data = await response.json();
        if (!data.success) {
            throw new Error(data.error || 'Failed to start scan');
        }
        
        button.textContent = 'Scanning...';
        
        // Show scan status
        const statusDiv = document.getElementById('watchlist-scan-status');
        if (statusDiv) {
            statusDiv.style.display = 'flex';
            document.getElementById('scan-status-text').textContent = 'scanning';
        }
        
        // Start polling for updates
        pollWatchlistScanStatus();
        
    } catch (error) {
        console.error('Error starting watchlist scan:', error);
        const button = document.getElementById('scan-watchlist-btn');
        button.disabled = false;
        button.textContent = 'Scan for New Releases';
        alert(`Error starting scan: ${error.message}`);
    }
}

/**
 * Poll watchlist scan status
 */
async function pollWatchlistScanStatus() {
    try {
        const response = await fetch('/api/watchlist/scan/status');
        const data = await response.json();
        
        if (data.success) {
            const statusText = document.getElementById('scan-status-text');
            const button = document.getElementById('scan-watchlist-btn');
            
            if (statusText) {
                // Show detailed progress if scanning
                if (data.status === 'scanning' && data.current_artist_name) {
                    const artistProgress = `${data.current_artist_index || 0}/${data.total_artists || 0}`;
                    let detailText = `Scanning ${data.current_artist_name} (${artistProgress})`;
                    
                    if (data.current_phase === 'fetching_discography') {
                        detailText += ' - Fetching releases...';
                    } else if (data.current_phase === 'checking_albums' && data.albums_to_check > 0) {
                        const albumProgress = `${data.albums_checked || 0}/${data.albums_to_check}`;
                        detailText += ` - Checking albums (${albumProgress})`;
                    } else if (data.current_phase && data.current_phase.startsWith('checking_album_')) {
                        detailText += ` - "${data.current_album || 'Unknown Album'}"`;
                    } else if (data.current_phase === 'rate_limiting') {
                        detailText += ' - Rate limiting...';
                    }
                    
                    // Add running totals
                    if (data.tracks_found_this_scan > 0 || data.tracks_added_this_scan > 0) {
                        detailText += ` | Found: ${data.tracks_found_this_scan || 0}, Added: ${data.tracks_added_this_scan || 0}`;
                    }
                    
                    statusText.textContent = detailText;
                } else {
                    statusText.textContent = data.status;
                }
            }
            
            if (data.status === 'completed') {
                if (button) {
                    button.disabled = false;
                    button.textContent = 'Scan for New Releases';
                }
                
                // Update status display with results
                const statusDiv = document.getElementById('watchlist-scan-status');
                if (statusDiv && data.summary) {
                    const newTracks = data.summary.new_tracks_found || 0;
                    const addedTracks = data.summary.tracks_added_to_wishlist || 0;
                    const totalArtists = data.summary.total_artists || 0;
                    const successfulScans = data.summary.successful_scans || 0;
                    
                    let completionMessage = `Scan completed: ${successfulScans}/${totalArtists} artists scanned`;
                    if (newTracks > 0) {
                        completionMessage += `, found ${newTracks} new track${newTracks !== 1 ? 's' : ''}`;
                        if (addedTracks > 0) {
                            completionMessage += `, added ${addedTracks} to wishlist`;
                        }
                    } else {
                        completionMessage += ', no new tracks found';
                    }
                    
                    statusDiv.innerHTML = `
                        <div class="scan-status-main">
                            <span class="sync-stat">${completionMessage}</span>
                        </div>
                    `;
                }
                
                // Update watchlist count
                updateWatchlistButtonCount();
                
                console.log('Watchlist scan completed:', data.summary);
                return; // Stop polling
                
            } else if (data.status === 'error') {
                if (button) {
                    button.disabled = false;
                    button.textContent = 'Scan for New Releases';
                }
                console.error('Watchlist scan error:', data.error);
                return; // Stop polling
            }
        }
        
        // Continue polling if still scanning
        if (data.success && data.status === 'scanning') {
            setTimeout(pollWatchlistScanStatus, 2000); // Poll every 2 seconds
        }
        
    } catch (error) {
        console.error('Error polling watchlist scan status:', error);
    }
}

/**
 * Remove artist from watchlist via modal
 */
async function removeFromWatchlistModal(artistId, artistName) {
    try {
        const response = await fetch('/api/watchlist/remove', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ artist_id: artistId })
        });
        
        const data = await response.json();
        if (!data.success) {
            throw new Error(data.error || 'Failed to remove from watchlist');
        }
        
        console.log(`❌ Removed ${artistName} from watchlist`);
        
        // Refresh the modal
        showWatchlistModal();
        
        // Update button count
        updateWatchlistButtonCount();
        
        // Update any visible artist cards
        updateArtistCardWatchlistStatus();
        
    } catch (error) {
        console.error('Error removing from watchlist:', error);
        alert(`Error removing ${artistName} from watchlist: ${error.message}`);
    }
}


// --- Metadata Updater Functions ---

// Global state for metadata update polling
let metadataUpdatePolling = false;
let metadataUpdateInterval = null;

/**
 * Handle metadata update button click
 */
async function handleMetadataUpdateButtonClick() {
    const button = document.getElementById('metadata-update-button');
    const currentAction = button.textContent;

    if (currentAction === 'Begin Update') {
        // Get refresh interval from dropdown
        const refreshSelect = document.getElementById('metadata-refresh-interval');
        const refreshIntervalDays = refreshSelect.value !== undefined ? parseInt(refreshSelect.value) : 30;

        try {
            button.disabled = true;
            button.textContent = 'Starting...';
            
            const response = await fetch('/api/metadata/start', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ refresh_interval_days: refreshIntervalDays })
            });

            const data = await response.json();
            if (!data.success) {
                throw new Error(data.error || 'Failed to start metadata update');
            }

            showToast('Metadata update started!', 'success');
            
            // Start polling for status updates
            startMetadataUpdatePolling();

        } catch (error) {
            console.error('Error starting metadata update:', error);
            button.disabled = false;
            button.textContent = 'Begin Update';
            showToast(`Error: ${error.message}`, 'error');
        }
    } else {
        // Stop metadata update
        try {
            button.disabled = true;
            button.textContent = 'Stopping...';
            
            const response = await fetch('/api/metadata/stop', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' }
            });

            if (!response.ok) {
                throw new Error('Failed to stop metadata update');
            }

        } catch (error) {
            console.error('Error stopping metadata update:', error);
            button.disabled = false;
            button.textContent = 'Stop Update';
        }
    }
}

/**
 * Start polling for metadata update status
 */
function startMetadataUpdatePolling() {
    if (metadataUpdatePolling) return; // Already polling
    
    metadataUpdatePolling = true;
    metadataUpdateInterval = setInterval(checkMetadataUpdateStatus, 1000); // Poll every second
    
    // Also check immediately
    checkMetadataUpdateStatus();
}

/**
 * Stop polling for metadata update status
 */
function stopMetadataUpdatePolling() {
    metadataUpdatePolling = false;
    if (metadataUpdateInterval) {
        clearInterval(metadataUpdateInterval);
        metadataUpdateInterval = null;
    }
}

/**
 * Check current metadata update status and update UI
 */
async function checkMetadataUpdateStatus() {
    try {
        const response = await fetch('/api/metadata/status');
        const data = await response.json();
        
        if (data.success && data.status) {
            updateMetadataProgressUI(data.status);
            
            // Stop polling if completed or error
            if (data.status.status === 'completed' || data.status.status === 'error') {
                stopMetadataUpdatePolling();
            }
        }
        
    } catch (error) {
        console.warn('Could not fetch metadata update status:', error);
    }
}

/**
 * Update metadata progress UI elements
 */
function updateMetadataProgressUI(status) {
    const button = document.getElementById('metadata-update-button');
    const phaseLabel = document.getElementById('metadata-phase-label');
    const progressLabel = document.getElementById('metadata-progress-label');
    const progressBar = document.getElementById('metadata-progress-bar');
    const refreshSelect = document.getElementById('metadata-refresh-interval');

    if (!button || !phaseLabel || !progressLabel || !progressBar || !refreshSelect) return;

    if (status.status === 'running') {
        button.textContent = 'Stop Update';
        button.disabled = false;
        refreshSelect.disabled = true;
        
        // Update current artist display
        const currentArtist = status.current_artist || 'Processing...';
        phaseLabel.textContent = `Current Artist: ${currentArtist}`;
        
        // Update progress
        const processed = status.processed || 0;
        const total = status.total || 0;
        const percentage = status.percentage || 0;
        
        progressLabel.textContent = `${processed} / ${total} artists (${percentage.toFixed(1)}%)`;
        progressBar.style.width = `${percentage}%`;
        
    } else if (status.status === 'stopping') {
        button.textContent = 'Stopping...';
        button.disabled = true;
        phaseLabel.textContent = 'Current Artist: Stopping...';
        
    } else if (status.status === 'completed') {
        button.textContent = 'Begin Update';
        button.disabled = false;
        refreshSelect.disabled = false;
        
        phaseLabel.textContent = 'Current Artist: Completed';
        
        const processed = status.processed || 0;
        const successful = status.successful || 0;
        const failed = status.failed || 0;
        
        progressLabel.textContent = `Completed: ${processed} processed, ${successful} successful, ${failed} failed`;
        progressBar.style.width = '100%';
        
        showToast(`Metadata update completed: ${successful} artists updated, ${failed} failed`, 'success');
        
    } else if (status.status === 'error') {
        button.textContent = 'Begin Update';
        button.disabled = false;
        refreshSelect.disabled = false;
        
        phaseLabel.textContent = 'Current Artist: Error occurred';
        progressLabel.textContent = status.error || 'Unknown error';
        progressBar.style.width = '0%';
        
    } else {
        // Idle state
        button.textContent = 'Begin Update';
        button.disabled = false;
        refreshSelect.disabled = false;
        
        phaseLabel.textContent = 'Current Artist: Not running';
        progressLabel.textContent = '0 / 0 artists (0.0%)';
        progressBar.style.width = '0%';
    }
}

/**
 * Check active media server and hide metadata updater if not Plex
 */
async function checkAndHideMetadataUpdaterForNonPlex() {
    try {
        const response = await fetch('/api/active-media-server');
        const data = await response.json();

        if (data.success) {
            const metadataCard = document.getElementById('metadata-updater-card');
            if (metadataCard) {
                // Show metadata updater only for Plex and Jellyfin
                if (data.active_server === 'plex' || data.active_server === 'jellyfin') {
                    metadataCard.style.display = 'flex';
                    console.log(`Metadata updater shown: ${data.active_server} is active server`);

                    // Update the header text to reflect the current server
                    const headerElement = metadataCard.querySelector('.card-header h3');
                    if (headerElement) {
                        const serverDisplayName = data.active_server.charAt(0).toUpperCase() + data.active_server.slice(1);
                        headerElement.textContent = `${serverDisplayName} Metadata Updater`;
                    }

                    // Update the description based on the server type
                    const descElement = metadataCard.querySelector('.metadata-updater-description');
                    if (descElement) {
                        if (data.active_server === 'jellyfin') {
                            descElement.textContent = 'Download and upload high-quality artist images from Spotify to your Jellyfin server for artists without photos.';
                        } else {
                            descElement.textContent = 'Download and upload high-quality artist images from Spotify to your Plex server for artists without photos.';
                        }
                    }
                } else {
                    // Hide metadata updater for Navidrome
                    metadataCard.style.display = 'none';
                    console.log(`Metadata updater hidden: ${data.active_server} does not support image uploads`);
                }
            }
        }
    } catch (error) {
        console.warn('Could not check active media server for metadata updater visibility:', error);
    }
}

/**
 * Check for ongoing metadata update and restore state on page load
 */
async function checkAndRestoreMetadataUpdateState() {
    try {
        const response = await fetch('/api/metadata/status');
        const data = await response.json();
        
        if (data.success && data.status) {
            const status = data.status;
            
            // If metadata update is running, restore the UI state and start polling
            if (status.status === 'running') {
                console.log('Found ongoing metadata update, restoring state...');
                updateMetadataProgressUI(status);
                startMetadataUpdatePolling();
            } else if (status.status === 'completed' || status.status === 'error') {
                // Show final state but don't start polling
                updateMetadataProgressUI(status);
            }
        }
    } catch (error) {
        console.warn('Could not check metadata update state on page load:', error);
    }
}

// --- Live Log Viewer Functions ---

// Global state for log polling
let logPolling = false;
let logInterval = null;
let lastLogCount = 0;

/**
 * Initialize the live log viewer for sync page
 */
function initializeLiveLogViewer() {
    const logArea = document.getElementById('sync-log-area');
    if (!logArea) return;

    // Set initial content
    logArea.value = 'Loading activity feed...';

    // Start log polling
    startLogPolling();

    // Initial load
    loadLogs();
}

/**
 * Start polling for logs
 */
function startLogPolling() {
    if (logPolling) return; // Already polling

    logPolling = true;
    logInterval = setInterval(loadLogs, 3000); // Poll every 3 seconds
    console.log('📝 Started activity feed polling for sync page');
}

/**
 * Stop polling for logs
 */
function stopLogPolling() {
    logPolling = false;
    if (logInterval) {
        clearInterval(logInterval);
        logInterval = null;
        console.log('📝 Stopped log polling');
    }
}

/**
 * Load and display activity feed as logs
 */
async function loadLogs() {
    try {
        const response = await fetch('/api/logs');
        const data = await response.json();

        if (data.logs && Array.isArray(data.logs)) {
            const logArea = document.getElementById('sync-log-area');
            if (!logArea) return;

            // Join logs with newlines and update textarea
            const logText = data.logs.join('\n');

            // Store current scroll state
            const wasAtTop = logArea.scrollTop <= 10;
            const wasUserScrolled = logArea.scrollTop < logArea.scrollHeight - logArea.clientHeight - 10;

            // Update content only if it has changed
            if (logArea.value !== logText) {
                logArea.value = logText;

                // Smart scrolling: stay at top for new entries, preserve user position if scrolled
                if (wasAtTop || !wasUserScrolled) {
                    logArea.scrollTop = 0; // Stay at top since newest entries are now at top
                }
                // If user had scrolled, keep their position (browser handles this automatically)
            }
        }
    } catch (error) {
        console.warn('Could not load activity logs for sync page:', error);
        const logArea = document.getElementById('sync-log-area');
        if (logArea && (logArea.value === 'Loading logs...' || logArea.value === '')) {
            logArea.value = 'Error loading activity feed. Check console for details.';
        }
    }
}

/**
 * Stop log polling when leaving sync page
 */
function cleanupSyncPageLogs() {
    stopLogPolling();
}

// --- Global Cleanup on Page Unload ---
// Note: Automatic wishlist processing now runs server-side and continues even when browser is closed
// ===============================
// LIBRARY PAGE FUNCTIONALITY
// ===============================

// Library page state
const libraryPageState = {
    isInitialized: false,
    currentSearch: "",
    currentLetter: "all",
    currentPage: 1,
    limit: 75,
    debounceTimer: null
};

function initializeLibraryPage() {
    console.log("🔧 Initializing Library page...");

    try {
        // Initialize search functionality
        initializeLibrarySearch();

        // Initialize alphabet selector
        initializeAlphabetSelector();

        // Initialize pagination
        initializeLibraryPagination();

        // Load initial data
        loadLibraryArtists();

        libraryPageState.isInitialized = true;
        console.log("✅ Library page initialized successfully");

    } catch (error) {
        console.error("❌ Error initializing Library page:", error);
        showToast("Failed to initialize Library page", "error");
    }
}

function initializeLibrarySearch() {
    const searchInput = document.getElementById("library-search-input");
    if (!searchInput) return;

    searchInput.addEventListener("input", (e) => {
        const query = e.target.value.trim();

        // Clear existing debounce timer
        if (libraryPageState.debounceTimer) {
            clearTimeout(libraryPageState.debounceTimer);
        }

        // Debounce search requests
        libraryPageState.debounceTimer = setTimeout(() => {
            libraryPageState.currentSearch = query;
            libraryPageState.currentPage = 1; // Reset to first page
            loadLibraryArtists();
        }, 300);
    });

    // Clear search on Escape key
    searchInput.addEventListener("keydown", (e) => {
        if (e.key === "Escape") {
            searchInput.value = "";
            libraryPageState.currentSearch = "";
            libraryPageState.currentPage = 1;
            loadLibraryArtists();
        }
    });
}

function initializeAlphabetSelector() {
    const alphabetButtons = document.querySelectorAll(".alphabet-btn");

    alphabetButtons.forEach(button => {
        button.addEventListener("click", () => {
            const letter = button.getAttribute("data-letter");

            // Update active state
            alphabetButtons.forEach(btn => btn.classList.remove("active"));
            button.classList.add("active");

            // Update state and load data
            libraryPageState.currentLetter = letter;
            libraryPageState.currentPage = 1; // Reset to first page
            loadLibraryArtists();
        });
    });
}

function initializeLibraryPagination() {
    const prevBtn = document.getElementById("prev-page-btn");
    const nextBtn = document.getElementById("next-page-btn");

    if (prevBtn) {
        prevBtn.addEventListener("click", () => {
            if (libraryPageState.currentPage > 1) {
                libraryPageState.currentPage--;
                loadLibraryArtists();
            }
        });
    }

    if (nextBtn) {
        nextBtn.addEventListener("click", () => {
            libraryPageState.currentPage++;
            loadLibraryArtists();
        });
    }
}

async function loadLibraryArtists() {
    try {
        // Show loading state
        showLibraryLoading(true);

        // Build query parameters
        const params = new URLSearchParams({
            search: libraryPageState.currentSearch,
            letter: libraryPageState.currentLetter,
            page: libraryPageState.currentPage,
            limit: libraryPageState.limit
        });

        // Fetch artists from API
        const response = await fetch(`/api/library/artists?${params}`);
        const data = await response.json();

        if (!data.success) {
            throw new Error(data.error || "Failed to load artists");
        }

        // Update UI with artists
        displayLibraryArtists(data.artists);
        updateLibraryPagination(data.pagination);
        updateLibraryStats(data.pagination.total_count);

        // Hide loading state
        showLibraryLoading(false);

        // Show empty state if no artists
        if (data.artists.length === 0) {
            showLibraryEmpty(true);
        } else {
            showLibraryEmpty(false);
        }

    } catch (error) {
        console.error("❌ Error loading library artists:", error);
        showToast("Failed to load artists", "error");
        showLibraryLoading(false);
        showLibraryEmpty(true);
    }
}

function displayLibraryArtists(artists) {
    const grid = document.getElementById("library-artists-grid");
    if (!grid) return;

    // Clear existing content
    grid.innerHTML = "";

    // Create artist cards
    artists.forEach(artist => {
        const card = createLibraryArtistCard(artist);
        grid.appendChild(card);
    });
}

function createLibraryArtistCard(artist) {
    const card = document.createElement("div");
    card.className = "library-artist-card";
    card.setAttribute("data-artist-id", artist.id);

    // Create image element
    const imageContainer = document.createElement("div");
    imageContainer.className = "library-artist-image";

    if (artist.image_url && artist.image_url.trim() !== "") {
        const img = document.createElement("img");
        img.src = artist.image_url;
        img.alt = artist.name;
        img.onerror = () => {
            console.log(`Failed to load image for ${artist.name}: ${artist.image_url}`);
            // Replace with fallback on error
            imageContainer.innerHTML = `<div class="library-artist-image-fallback">🎵</div>`;
        };
        img.onload = () => {
            console.log(`Successfully loaded image for ${artist.name}: ${artist.image_url}`);
        };
        imageContainer.appendChild(img);
    } else {
        console.log(`No image URL for ${artist.name}: '${artist.image_url}'`);
        imageContainer.innerHTML = `<div class="library-artist-image-fallback">🎵</div>`;
    }

    // Create info section
    const info = document.createElement("div");
    info.className = "library-artist-info";

    const name = document.createElement("h3");
    name.className = "library-artist-name";
    name.textContent = artist.name;
    name.title = artist.name; // For tooltip on long names

    const stats = document.createElement("div");
    stats.className = "library-artist-stats";

    if (artist.track_count > 0) {
        const trackStat = document.createElement("span");
        trackStat.className = "library-artist-stat";
        trackStat.textContent = `${artist.track_count} track${artist.track_count !== 1 ? "s" : ""}`;

        stats.appendChild(trackStat);
    }

    info.appendChild(name);
    info.appendChild(stats);

    // Assemble card
    card.appendChild(imageContainer);
    card.appendChild(info);

    // Add click handler to navigate to artist detail page
    card.addEventListener("click", () => {
        console.log(`🎵 Opening artist detail for: ${artist.name} (ID: ${artist.id})`);
        navigateToArtistDetail(artist.id, artist.name);
    });

    return card;
}

function updateLibraryPagination(pagination) {
    const prevBtn = document.getElementById("prev-page-btn");
    const nextBtn = document.getElementById("next-page-btn");
    const pageInfo = document.getElementById("page-info");
    const paginationContainer = document.getElementById("library-pagination");

    if (!paginationContainer) return;

    // Update button states
    if (prevBtn) {
        prevBtn.disabled = !pagination.has_prev;
    }

    if (nextBtn) {
        nextBtn.disabled = !pagination.has_next;
    }

    // Update page info
    if (pageInfo) {
        pageInfo.textContent = `Page ${pagination.page} of ${pagination.total_pages}`;
    }

    // Show/hide pagination based on total pages
    if (pagination.total_pages > 1) {
        paginationContainer.classList.remove("hidden");
    } else {
        paginationContainer.classList.add("hidden");
    }
}

function updateLibraryStats(totalCount) {
    const countElement = document.getElementById("library-artist-count");
    if (countElement) {
        countElement.textContent = totalCount;
    }
}

function showLibraryLoading(show) {
    const loadingElement = document.getElementById("library-loading");
    if (loadingElement) {
        if (show) {
            loadingElement.classList.remove("hidden");
        } else {
            loadingElement.classList.add("hidden");
        }
    }
}

function showLibraryEmpty(show) {
    const emptyElement = document.getElementById("library-empty");
    if (emptyElement) {
        if (show) {
            emptyElement.classList.remove("hidden");
        } else {
            emptyElement.classList.add("hidden");
        }
    }
}

// ===============================================
// Artist Detail Page Functions
// ===============================================

// Artist detail page state
let artistDetailPageState = {
    isInitialized: false,
    currentArtistId: null,
    currentArtistName: null
};

function navigateToArtistDetail(artistId, artistName) {
    console.log(`🎵 Navigating to artist detail: ${artistName} (ID: ${artistId})`);

    // Store current artist info
    artistDetailPageState.currentArtistId = artistId;
    artistDetailPageState.currentArtistName = artistName;

    // Navigate to artist detail page
    navigateToPage('artist-detail');

    // Initialize if needed and load data
    if (!artistDetailPageState.isInitialized) {
        initializeArtistDetailPage();
    }

    // Load artist data
    loadArtistDetailData(artistId, artistName);
}

function initializeArtistDetailPage() {
    console.log("🔧 Initializing Artist Detail page...");

    // Initialize back button
    const backBtn = document.getElementById("artist-detail-back-btn");
    if (backBtn) {
        backBtn.addEventListener("click", () => {
            console.log("🔙 Returning to Library page");
            // Clear artist detail state so we go back to the list view
            artistDetailPageState.currentArtistId = null;
            artistDetailPageState.currentArtistName = null;
            navigateToPage('library');
        });
    }

    // Initialize retry button
    const retryBtn = document.getElementById("artist-detail-retry-btn");
    if (retryBtn) {
        retryBtn.addEventListener("click", () => {
            if (artistDetailPageState.currentArtistId && artistDetailPageState.currentArtistName) {
                loadArtistDetailData(artistDetailPageState.currentArtistId, artistDetailPageState.currentArtistName);
            }
        });
    }

    artistDetailPageState.isInitialized = true;
    console.log("✅ Artist Detail page initialized successfully");
}

async function loadArtistDetailData(artistId, artistName) {
    console.log(`🔄 Loading artist detail data for: ${artistName} (ID: ${artistId})`);

    // Show loading state and hide all content
    showArtistDetailLoading(true);
    showArtistDetailError(false);
    showArtistDetailMain(false);
    showArtistDetailHero(false);

    // Don't update header until data loads to avoid showing stale data

    try {
        // Call API to get artist discography data
        const response = await fetch(`/api/artist-detail/${artistId}`);

        if (!response.ok) {
            throw new Error(`Failed to load artist data: ${response.statusText}`);
        }

        const data = await response.json();

        if (!data.success) {
            throw new Error(data.error || 'Failed to load artist data');
        }

        console.log(`✅ Loaded artist detail data:`, data);

        // Hide loading and show all content
        showArtistDetailLoading(false);
        showArtistDetailMain(true);
        showArtistDetailHero(true);

        console.log(`🎨 Main content visibility:`, document.getElementById('artist-detail-main'));
        console.log(`🎨 Albums section:`, document.getElementById('albums-section'));

        // Update header with artist name now that data is loaded
        updateArtistDetailPageHeader(data.artist.name);

        // Populate the page with data
        populateArtistDetailPage(data);

    } catch (error) {
        console.error(`❌ Error loading artist detail data:`, error);

        // Show error state (keep hero section hidden)
        showArtistDetailLoading(false);
        showArtistDetailError(true, error.message);
        showArtistDetailHero(false);

        showToast(`Failed to load artist details: ${error.message}`, "error");
    }
}

function updateArtistDetailPageHeader(artistName) {
    // Update header title
    const headerTitle = document.getElementById("artist-detail-name");
    if (headerTitle) {
        headerTitle.textContent = artistName;
    }

    // Update main artist name
    const mainTitle = document.getElementById("artist-info-name");
    if (mainTitle) {
        mainTitle.textContent = artistName;
    }
}

function populateArtistDetailPage(data) {
    const artist = data.artist;
    const discography = data.discography;

    console.log(`🎨 Populating artist detail page for: ${artist.name}`);
    console.log(`📀 Discography data:`, discography);
    console.log(`📀 Albums:`, discography.albums);
    console.log(`📀 EPs:`, discography.eps);
    console.log(`📀 Singles:`, discography.singles);

    // Update hero section with image, name, and stats
    updateArtistHeroSection(artist, discography);

    // Update genres (if element exists)
    updateArtistGenres(artist.genres);

    // Update summary stats (if element exists)
    updateArtistSummaryStats(discography);

    // Populate discography sections
    populateDiscographySections(discography);

    // Initialize library watchlist button if it exists (for library page)
    const libraryWatchlistBtn = document.getElementById('library-artist-watchlist-btn');
    if (libraryWatchlistBtn && data.spotify_artist && data.spotify_artist.spotify_artist_id) {
        initializeLibraryWatchlistButton(data.spotify_artist.spotify_artist_id, data.spotify_artist.spotify_artist_name);
    }
}

function updateArtistDetailImage(imageUrl, artistName) {
    const imageElement = document.getElementById("artist-detail-image");
    const fallbackElement = document.getElementById("artist-image-fallback");

    if (imageUrl && imageUrl.trim() !== "") {
        imageElement.src = imageUrl;
        imageElement.alt = artistName;
        imageElement.classList.remove("hidden");
        fallbackElement.classList.add("hidden");

        imageElement.onerror = () => {
            console.log(`Failed to load artist image for ${artistName}: ${imageUrl}`);
            // Replace with fallback on error
            imageElement.classList.add("hidden");
            fallbackElement.classList.remove("hidden");
        };

        imageElement.onload = () => {
            console.log(`Successfully loaded artist image for ${artistName}: ${imageUrl}`);
        };
    } else {
        console.log(`No image URL for ${artistName}: '${imageUrl}'`);
        imageElement.classList.add("hidden");
        fallbackElement.classList.remove("hidden");
    }
}

function updateArtistGenres(genres) {
    const genresContainer = document.getElementById("artist-genres");
    if (!genresContainer) return;

    genresContainer.innerHTML = "";

    if (genres && genres.length > 0) {
        genres.forEach(genre => {
            const genreTag = document.createElement("span");
            genreTag.className = "genre-tag";
            genreTag.textContent = genre;
            genresContainer.appendChild(genreTag);
        });
    }
}

function updateArtistSummaryStats(discography) {
    // Calculate stats
    const ownedAlbums = discography.albums.filter(album => album.owned).length;
    const missingAlbums = discography.albums.filter(album => !album.owned).length;
    const totalAlbums = discography.albums.length;
    const completionPercentage = totalAlbums > 0 ? Math.round((ownedAlbums / totalAlbums) * 100) : 0;

    // Update owned albums count
    const ownedElement = document.getElementById("owned-albums-count");
    if (ownedElement) {
        ownedElement.textContent = ownedAlbums;
    }

    // Update missing albums count
    const missingElement = document.getElementById("missing-albums-count");
    if (missingElement) {
        missingElement.textContent = missingAlbums;
    }

    // Update completion percentage
    const completionElement = document.getElementById("completion-percentage");
    if (completionElement) {
        completionElement.textContent = `${completionPercentage}%`;
    }
}

function updateArtistHeaderStats(albumCount, trackCount) {
    // This function is deprecated - now using updateArtistHeroSection
    console.log("📊 Using new hero section instead of old header stats");
}

function updateArtistHeroSection(artist, discography) {
    console.log("🖼️ Updating artist hero section");

    // Update artist image with detailed debugging
    const imageElement = document.getElementById("artist-detail-image");
    const fallbackElement = document.getElementById("artist-detail-image-fallback");

    console.log(`🖼️ Debug Artist image info:`);
    console.log(`   - URL: '${artist.image_url}'`);
    console.log(`   - Type: ${typeof artist.image_url}`);
    console.log(`   - Full artist object:`, artist);
    console.log(`   - Image element:`, imageElement);
    console.log(`   - Fallback element:`, fallbackElement);

    if (artist.image_url && artist.image_url.trim() !== "" && artist.image_url !== "null") {
        console.log(`✅ Setting image src to: ${artist.image_url}`);
        imageElement.src = artist.image_url;
        imageElement.alt = artist.name;
        imageElement.style.display = "block";
        if (fallbackElement) {
            fallbackElement.style.display = "none";
        }

        imageElement.onload = () => {
            console.log(`✅ Successfully loaded artist image: ${artist.image_url}`);
        };

        imageElement.onerror = () => {
            console.error(`❌ Failed to load artist image: ${artist.image_url}`);
            imageElement.style.display = "none";
            if (fallbackElement) {
                fallbackElement.style.display = "flex";
            }
        };
    } else {
        console.log(`🖼️ No valid image URL - showing fallback for ${artist.name}`);
        imageElement.style.display = "none";
        if (fallbackElement) {
            fallbackElement.style.display = "flex";
        }
    }

    // Update artist name
    const nameElement = document.getElementById("artist-detail-name");
    if (nameElement) {
        nameElement.textContent = artist.name;
    }

    // Calculate and update stats for each category
    updateCategoryStats('albums', discography.albums);
    updateCategoryStats('eps', discography.eps);
    updateCategoryStats('singles', discography.singles);
}

function updateCategoryStats(category, releases) {
    const owned = releases.filter(r => r.owned !== false).length;
    const missing = releases.filter(r => r.owned === false).length;
    const total = releases.length;
    const completion = total > 0 ? Math.round((owned / total) * 100) : 100;

    console.log(`📊 ${category}: ${owned} owned, ${missing} missing, ${completion}% complete`);

    // Update stats text
    const statsElement = document.getElementById(`${category}-stats`);
    if (statsElement) {
        statsElement.textContent = `${owned} owned, ${missing} missing`;
    }

    // Update completion bar
    const fillElement = document.getElementById(`${category}-completion-fill`);
    if (fillElement) {
        fillElement.style.width = `${completion}%`;
    }

    // Update completion text
    const textElement = document.getElementById(`${category}-completion-text`);
    if (textElement) {
        textElement.textContent = `${completion}%`;
    }
}

function populateDiscographySections(discography) {
    // Populate albums
    populateReleaseSection('albums', discography.albums);

    // Populate EPs
    populateReleaseSection('eps', discography.eps);

    // Populate singles
    populateReleaseSection('singles', discography.singles);
}

function populateReleaseSection(sectionType, releases) {
    const gridId = `${sectionType}-grid`;
    const ownedCountId = `${sectionType}-owned-count`;
    const missingCountId = `${sectionType}-missing-count`;

    const grid = document.getElementById(gridId);
    if (!grid) return;

    // Clear existing content
    grid.innerHTML = "";

    // Calculate stats
    const ownedCount = releases.filter(release => release.owned).length;
    const missingCount = releases.filter(release => !release.owned).length;

    // Update section stats
    const ownedElement = document.getElementById(ownedCountId);
    const missingElement = document.getElementById(missingCountId);

    if (ownedElement) {
        ownedElement.textContent = `${ownedCount} owned`;
    }

    if (missingElement) {
        missingElement.textContent = `${missingCount} missing`;
    }

    // Create release cards
    releases.forEach((release, index) => {
        console.log(`📀 Creating card ${index + 1} for: ${release.title}`);
        const card = createReleaseCard(release);
        grid.appendChild(card);
        console.log(`📀 Added card to grid:`, card);
    });

    console.log(`📀 Populated ${sectionType} section: ${ownedCount} owned, ${missingCount} missing`);
    console.log(`📀 Grid element:`, grid);
    console.log(`📀 Grid children count:`, grid.children.length);
}

function createReleaseCard(release) {
    const card = document.createElement("div");
    card.className = `release-card${release.owned ? "" : " missing"}`;
    card.setAttribute("data-release-id", release.id || "");
    card.setAttribute("data-spotify-id", release.spotify_id || "");

    // Create image
    const imageContainer = document.createElement("div");
    if (release.image_url && release.image_url.trim() !== "") {
        const img = document.createElement("img");
        img.src = release.image_url;
        img.alt = release.title;
        img.className = "release-image";
        img.onerror = () => {
            imageContainer.innerHTML = `<div class="release-image-fallback">💿</div>`;
        };
        imageContainer.appendChild(img);
    } else {
        imageContainer.innerHTML = `<div class="release-image-fallback">💿</div>`;
    }

    // Create title
    const title = document.createElement("h4");
    title.className = "release-title";
    title.textContent = release.title;
    title.title = release.title;

    // Create year - extract from release_date (Spotify format) or fall back to year field
    const year = document.createElement("div");
    year.className = "release-year";

    let yearText = "Unknown Year";

    // DEBUG: Log the release data to see what we're working with (remove this after testing)
    // console.log(`🔍 DEBUG: Release "${release.title}" data:`, {
    //     title: release.title,
    //     owned: release.owned,
    //     year: release.year,
    //     release_date: release.release_date,
    //     track_completion: release.track_completion
    // });

    // First try to extract year from release_date (Spotify format: "YYYY-MM-DD")
    if (release.release_date) {
        try {
            // Extract year directly from string to avoid timezone issues
            const yearMatch = release.release_date.match(/^(\d{4})/);
            if (yearMatch) {
                const releaseYear = parseInt(yearMatch[1]);
                if (releaseYear && !isNaN(releaseYear) && releaseYear > 1900 && releaseYear <= new Date().getFullYear() + 1) {
                    yearText = releaseYear.toString();
                }
            } else {
                // Fallback to Date parsing if format is different
                const releaseYear = new Date(release.release_date).getFullYear();
                if (releaseYear && !isNaN(releaseYear) && releaseYear > 1900 && releaseYear <= new Date().getFullYear() + 1) {
                    yearText = releaseYear.toString();
                }
            }
        } catch (e) {
            console.warn('Error parsing release_date:', release.release_date, e);
        }
    }

    // Fallback to direct year field if release_date parsing failed
    if (yearText === "Unknown Year" && release.year) {
        yearText = release.year.toString();
    }

    year.textContent = yearText;

    // Create completion info
    const completion = document.createElement("div");
    completion.className = "release-completion";

    const completionText = document.createElement("span");
    const completionBar = document.createElement("div");
    completionBar.className = "completion-bar";

    const completionFill = document.createElement("div");
    completionFill.className = "completion-fill";

    if (release.owned) {
        // Handle new detailed track completion object
        if (release.track_completion && typeof release.track_completion === 'object') {
            const completion = release.track_completion;
            const percentage = completion.percentage || 100;
            const ownedTracks = completion.owned_tracks || 0;
            const totalTracks = completion.total_tracks || 0;
            const missingTracks = completion.missing_tracks || 0;

            completionFill.style.width = `${percentage}%`;

            if (missingTracks === 0) {
                completionText.textContent = `Complete (${ownedTracks})`;
                completionText.className = "completion-text complete";
                completionFill.className += " complete";
            } else {
                completionText.textContent = `${ownedTracks}/${totalTracks} tracks`;
                completionText.className = "completion-text partial";
                completionFill.className += " partial";

                // Add missing tracks indicator
                completionText.title = `Missing ${missingTracks} track${missingTracks !== 1 ? 's' : ''}`;
            }
        } else {
            // Fallback for legacy simple percentage
            const percentage = release.track_completion || 100;
            completionFill.style.width = `${percentage}%`;

            if (percentage === 100) {
                completionText.textContent = "Complete";
                completionText.className = "completion-text complete";
                completionFill.className += " complete";
            } else {
                completionText.textContent = `${percentage}%`;
                completionText.className = "completion-text partial";
                completionFill.className += " partial";
            }
        }
    } else {
        completionText.textContent = "Missing";
        completionText.className = "completion-text missing";
        completionFill.className += " missing";
        completionFill.style.width = "0%";
    }

    completionBar.appendChild(completionFill);
    completion.appendChild(completionText);
    completion.appendChild(completionBar);

    // Assemble card
    card.appendChild(imageContainer);
    card.appendChild(title);
    card.appendChild(year);
    card.appendChild(completion);

    // Add click handler for release card
    card.addEventListener("click", async () => {
        console.log(`Clicked on release: ${release.title} (Owned: ${release.owned})`);

        // For owned/complete releases, show info message
        if (release.owned && (!release.track_completion ||
            (typeof release.track_completion === 'object' && release.track_completion.missing_tracks === 0) ||
            (typeof release.track_completion === 'number' && release.track_completion === 100))) {
            showToast(`${release.title} is already complete in your library`, "info");
            return;
        }

        showLoadingOverlay('Loading album...');

        // For missing or incomplete releases, open wishlist modal
        try {
            // Convert release object to album format expected by our function
            const albumData = {
                id: release.spotify_id || release.id,
                name: release.title,
                image_url: release.image_url,
                release_date: release.year ? `${release.year}-01-01` : '',
                album_type: release.type || 'album',
                total_tracks: (release.track_completion && typeof release.track_completion === 'object')
                    ? release.track_completion.total_tracks : 1
            };

            // Get current artist from artist detail page state
            const currentArtist = artistDetailPageState.currentArtistName ? {
                id: artistDetailPageState.currentArtistId,
                name: artistDetailPageState.currentArtistName,
                image_url: getArtistImageFromPage() || '' // Get artist image from page
            } : null;

            if (!currentArtist) {
                console.error('❌ No current artist found for release click');
                showToast('Error: No artist information available', 'error');
                return;
            }

            // Load tracks for the album
            const response = await fetch(`/api/artist/${currentArtist.id}/album/${albumData.id}/tracks`);
            if (!response.ok) {
                throw new Error(`Failed to load album tracks: ${response.status}`);
            }

            const data = await response.json();
            if (!data.success || !data.tracks || data.tracks.length === 0) {
                throw new Error('No tracks found for this release');
            }

            // Determine album type based on release data
            const albumType = release.type === 'single' ? 'singles' : 'albums';

            // Open the Add to Wishlist modal
            // Note: openAddToWishlistModal has its own loading overlay
            hideLoadingOverlay();
            await openAddToWishlistModal(albumData, currentArtist, data.tracks, albumType);

        } catch (error) {
            hideLoadingOverlay();
            console.error('❌ Error handling release click:', error);
            showToast(`Error opening wishlist modal: ${error.message}`, 'error');
        }
    });

    return card;
}

/**
 * Helper function to get artist image from the current artist detail page
 */
function getArtistImageFromPage() {
    try {
        // Try to get from artist detail image element
        const artistDetailImage = document.getElementById('artist-detail-image');
        if (artistDetailImage && artistDetailImage.src && artistDetailImage.src !== window.location.href) {
            return artistDetailImage.src;
        }

        // Try to get from artist hero image
        const artistImage = document.getElementById('artist-image');
        if (artistImage) {
            const bgImage = window.getComputedStyle(artistImage).backgroundImage;
            if (bgImage && bgImage !== 'none') {
                // Extract URL from CSS background-image
                const urlMatch = bgImage.match(/url\(["']?(.*?)["']?\)/);
                if (urlMatch && urlMatch[1]) {
                    return urlMatch[1];
                }
            }
        }

        return null;
    } catch (error) {
        console.warn('Error getting artist image from page:', error);
        return null;
    }
}

// UI state management functions
function showArtistDetailLoading(show) {
    const loadingElement = document.getElementById("artist-detail-loading");
    if (loadingElement) {
        if (show) {
            loadingElement.classList.remove("hidden");
        } else {
            loadingElement.classList.add("hidden");
        }
    }
}

function showArtistDetailError(show, message = "") {
    const errorElement = document.getElementById("artist-detail-error");
    const errorMessageElement = document.getElementById("artist-detail-error-message");

    if (errorElement) {
        if (show) {
            errorElement.classList.remove("hidden");
            if (errorMessageElement && message) {
                errorMessageElement.textContent = message;
            }
        } else {
            errorElement.classList.add("hidden");
        }
    }
}

function showArtistDetailMain(show) {
    const mainElement = document.getElementById("artist-detail-main");
    if (mainElement) {
        if (show) {
            mainElement.classList.remove("hidden");
        } else {
            mainElement.classList.add("hidden");
        }
    }
}

function showArtistDetailHero(show) {
    const heroElement = document.getElementById("artist-hero-section");
    if (heroElement) {
        if (show) {
            heroElement.classList.remove("hidden");
        } else {
            heroElement.classList.add("hidden");
        }
    }
}

/**
 * Initialize the library page watchlist button
 */
async function initializeLibraryWatchlistButton(artistId, artistName) {
    const button = document.getElementById('library-artist-watchlist-btn');
    if (!button) return;

    console.log(`🔧 Initializing library watchlist button for: ${artistName} (${artistId})`);

    // Reset button state
    button.disabled = false;
    button.classList.remove('watching');

    // Set up click handler
    button.onclick = (e) => toggleLibraryWatchlist(e, artistId, artistName);

    // Check and update current status
    await updateLibraryWatchlistButtonStatus(artistId);
}

/**
 * Toggle watchlist status for library page
 */
async function toggleLibraryWatchlist(event, artistId, artistName) {
    event.preventDefault();

    const button = document.getElementById('library-artist-watchlist-btn');
    const icon = button.querySelector('.watchlist-icon');
    const text = button.querySelector('.watchlist-text');

    // Show loading state
    const originalText = text.textContent;
    text.textContent = 'Loading...';
    button.disabled = true;

    try {
        // Check current status
        const checkResponse = await fetch('/api/watchlist/check', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ artist_id: artistId })
        });

        const checkData = await checkResponse.json();
        if (!checkData.success) {
            throw new Error(checkData.error || 'Failed to check watchlist status');
        }

        const isWatching = checkData.is_watching;

        // Toggle watchlist status
        const endpoint = isWatching ? '/api/watchlist/remove' : '/api/watchlist/add';
        const payload = isWatching ?
            { artist_id: artistId } :
            { artist_id: artistId, artist_name: artistName };

        const response = await fetch(endpoint, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload)
        });

        const data = await response.json();

        if (!data.success) {
            throw new Error(data.error || 'Failed to update watchlist');
        }

        // Update button state based on new status
        if (isWatching) {
            // Was watching, now removed
            icon.textContent = '👁️';
            text.textContent = 'Add to Watchlist';
            button.classList.remove('watching');
            console.log(`❌ Removed ${artistName} from watchlist`);
        } else {
            // Was not watching, now added
            icon.textContent = '👁️';
            text.textContent = 'Watching...';
            button.classList.add('watching');
            console.log(`✅ Added ${artistName} to watchlist`);
        }

        // Update dashboard watchlist count if function exists
        if (typeof updateWatchlistCount === 'function') {
            updateWatchlistCount();
        }

        showToast(data.message, 'success');

    } catch (error) {
        console.error('Error toggling library watchlist:', error);

        // Restore button state
        text.textContent = originalText;
        showToast(`Error: ${error.message}`, 'error');

    } finally {
        button.disabled = false;
    }
}

/**
 * Update library watchlist button status based on current state
 */
async function updateLibraryWatchlistButtonStatus(artistId) {
    const button = document.getElementById('library-artist-watchlist-btn');
    if (!button) return;

    try {
        const response = await fetch('/api/watchlist/check', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ artist_id: artistId })
        });

        const data = await response.json();

        if (data.success) {
            const icon = button.querySelector('.watchlist-icon');
            const text = button.querySelector('.watchlist-text');

            if (data.is_watching) {
                icon.textContent = '👁️';
                text.textContent = 'Watching...';
                button.classList.add('watching');
            } else {
                icon.textContent = '👁️';
                text.textContent = 'Add to Watchlist';
                button.classList.remove('watching');
            }
        }
    } catch (error) {
        console.warn('Failed to check library watchlist status:', error);
    }
}
