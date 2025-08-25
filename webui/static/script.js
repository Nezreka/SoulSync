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

// Streaming state management (new functionality)
let streamStatusPoller = null;
let audioPlayer = null;
let allSearchResults = [];
let currentFilterType = 'all';
let currentFilterFormat = 'all';
let currentSortBy = 'quality_score';
let isSortReversed = false;
let searchAbortController = null;
let dbStatsInterval = null;
let dbUpdateStatusInterval = null;

// --- Add these globals for the Sync Page ---
let spotifyPlaylists = [];
let selectedPlaylists = new Set();
let activeSyncPollers = {}; // Key: playlist_id, Value: intervalId
let playlistTrackCache = {}; // Key: playlist_id, Value: tracks array
let spotifyPlaylistsLoaded = false; 

// API endpoints
const API = {
    status: '/status',
    config: '/config',
    settings: '/api/settings',
    testConnection: '/api/test-connection',
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

    
    // Start periodic updates
    updateServiceStatus();
    setInterval(updateServiceStatus, 5000); // Every 5 seconds
    
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

function navigateToPage(pageId) {
    if (pageId === currentPage) return;
    
    // Update navigation buttons
    document.querySelectorAll('.nav-button').forEach(btn => {
        btn.classList.remove('active');
    });
    document.querySelector(`[data-page="${pageId}"]`).classList.add('active');
    
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
        switch (pageId) {
            case 'dashboard':
                stopDownloadPolling();
                await loadDashboardData();
                break;
            case 'sync':
                stopDownloadPolling();
                initializeSyncPage();
                await loadSyncData();
                break;
            case 'downloads':
                initializeSearch();
                initializeFilters();
                await loadDownloadsData();
                break;
            case 'artists':
                stopDownloadPolling();
                await loadArtistsData();
                break;
            case 'settings':
                initializeSettings();
                stopDownloadPolling();
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

async function updateServiceStatus() {
    try {
        const response = await fetch(API.status);
        const data = await response.json();
        
        // Update sidebar status indicators
        updateStatusIndicator('spotify', data.spotify);
        updateStatusIndicator('media-server', data.media_server);
        updateStatusIndicator('soulseek', data.soulseek);
        
        // Update media server name
        const serverName = data.active_media_server === 'plex' ? 'Plex' : 'Jellyfin';
        document.getElementById('media-server-name').textContent = serverName;
        
    } catch (error) {
        console.error('Error fetching status:', error);
        // Set all to disconnected on error
        updateStatusIndicator('spotify', false);
        updateStatusIndicator('media-server', false);
        updateStatusIndicator('soulseek', false);
    }
}

function updateStatusIndicator(service, connected) {
    const indicator = document.getElementById(`${service}-indicator`);
    const dot = indicator.querySelector('.status-dot');
    
    if (connected) {
        dot.classList.remove('disconnected');
        dot.classList.add('connected');
    } else {
        dot.classList.remove('connected');
        dot.classList.add('disconnected');
    }
}

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
    
    // Hide loading animation
    hideLoadingAnimation();
    
    // Show no track message and collapse media player
    document.getElementById('no-track-message').classList.remove('hidden');
    
    // Force collapse the media player when track is cleared
    if (mediaPlayerExpanded) {
        toggleMediaPlayerExpansion();
    }
    
    // Ensure media player returns to compact state
    const mediaPlayer = document.getElementById('media-player');
    if (mediaPlayer) {
        mediaPlayer.style.minHeight = '85px';
        const expandedContent = document.getElementById('media-expanded');
        if (expandedContent) {
            expandedContent.classList.add('hidden');
        }
    }
    
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
    // Start polling for stream status updates
    if (streamStatusPoller) {
        clearInterval(streamStatusPoller);
    }
    
    console.log('🔄 Starting stream status polling (1-second interval)');
    updateStreamStatus(); // Initial check
    streamStatusPoller = setInterval(updateStreamStatus, 1000);
}

function stopStreamStatusPolling() {
    // Stop polling for stream status updates
    if (streamStatusPoller) {
        clearInterval(streamStatusPoller);
        streamStatusPoller = null;
        console.log('⏹️ Stopped stream status polling');
    }
}

async function updateStreamStatus() {
    // Poll server for streaming progress and handle state changes
    try {
        const response = await fetch(API.stream.status);
        if (!response.ok) {
            throw new Error(`HTTP ${response.status}`);
        }
        
        const data = await response.json();
        
        // Update current stream state
        currentStream.status = data.status;
        currentStream.progress = data.progress;
        
        switch (data.status) {
            case 'loading':
                setLoadingProgress(data.progress);
                break;
                
            case 'queued':
                // Show queue status
                const loadingText = document.querySelector('.loading-text');
                if (loadingText) {
                    loadingText.textContent = 'Queued...';
                }
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
                showToast(`Streaming error: ${data.error_message}`, 'error');
                clearTrack();
                break;
        }
        
    } catch (error) {
        console.error('Error updating stream status:', error);
        // Don't clear everything on network errors - might be temporary
    }
}

async function startAudioPlayback() {
    // Start HTML5 audio playback of the streamed file
    try {
        if (!audioPlayer) {
            throw new Error('Audio player not initialized');
        }
        
        // Set audio source with cache-busting timestamp
        const audioUrl = `/stream/audio?t=${new Date().getTime()}`;
        audioPlayer.src = audioUrl;
        
        console.log(`🎵 Loading audio from: ${audioUrl}`);
        
        // Wait a moment for the file to be fully ready
        await new Promise(resolve => setTimeout(resolve, 500));
        
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
                return; // Success!
                
            } catch (playError) {
                retryCount++;
                console.warn(`⚠️ Audio play attempt ${retryCount} failed:`, playError.message);
                
                if (retryCount >= maxRetries) {
                    throw playError; // Re-throw after max retries
                }
                
                // Wait before retry
                await new Promise(resolve => setTimeout(resolve, 1000));
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
            userMessage = 'Network error - please try again';
        } else if (error.message.includes('decode')) {
            userMessage = 'Audio file is corrupted or incompatible';
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
    // TODO: Update progress bar in sidebar when implemented
    
    // Update time display if elements exist
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
    const partialSupport = ['flac', 'aac'];  // Depends on browser
    const unsupported = ['m4a', 'wma', 'ape', 'aiff'];  // Generally problematic
    
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
        'm4a': 'audio/mp4',
        'wma': 'audio/x-ms-wma'
    };
    
    const mimeType = mimeTypes[extension];
    if (!mimeType) return false;
    
    const canPlay = audio.canPlayType(mimeType);
    return canPlay === 'probably' || canPlay === 'maybe';
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
        
        // Populate Tidal settings  
        document.getElementById('tidal-client-id').value = settings.tidal?.client_id || '';
        document.getElementById('tidal-client-secret').value = settings.tidal?.client_secret || '';
        
        // Populate Plex settings
        document.getElementById('plex-url').value = settings.plex?.base_url || '';
        document.getElementById('plex-token').value = settings.plex?.token || '';
        
        // Populate Jellyfin settings
        document.getElementById('jellyfin-url').value = settings.jellyfin?.base_url || '';
        document.getElementById('jellyfin-api-key').value = settings.jellyfin?.api_key || '';
        
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
    document.getElementById(`${serverType}-toggle`).classList.add('active');
    
    // Show/hide server containers
    document.getElementById('plex-container').classList.toggle('hidden', serverType !== 'plex');
    document.getElementById('jellyfin-container').classList.toggle('hidden', serverType !== 'jellyfin');
}

async function saveSettings() {
    // Determine active server from toggle buttons
    const activeServer = document.getElementById('plex-toggle').classList.contains('active') ? 'plex' : 'jellyfin';
    
    const settings = {
        active_media_server: activeServer,
        spotify: {
            client_id: document.getElementById('spotify-client-id').value,
            client_secret: document.getElementById('spotify-client-secret').value
        },
        tidal: {
            client_id: document.getElementById('tidal-client-id').value,
            client_secret: document.getElementById('tidal-client-secret').value
        },
        plex: {
            base_url: document.getElementById('plex-url').value,
            token: document.getElementById('plex-token').value
        },
        jellyfin: {
            base_url: document.getElementById('jellyfin-url').value,
            api_key: document.getElementById('jellyfin-api-key').value
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
                    <button onclick="openPlaylistDetailsModal(event, '${p.id}')">Sync / Download</button>
                </div>
            </div>
        </div>
        `;
    }).join('');
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
}

function stopSyncPolling(playlistId) {
    if (activeSyncPollers[playlistId]) {
        clearInterval(activeSyncPollers[playlistId]);
        delete activeSyncPollers[playlistId];
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
    
    // Connect downloads search button
    const searchButton = document.getElementById('downloads-search-btn');
    const searchInput = document.getElementById('downloads-search-input');
    const clearButton = document.querySelector('.controls-panel__clear-btn');
    
    if (searchButton && searchInput) {
        searchButton.addEventListener('click', performDownloadsSearch);
        searchInput.addEventListener('keypress', (e) => {
            if (e.key === 'Enter') performDownloadsSearch();
        });
    }
    
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
        if (result.filename && !isAudioFormatSupported(result.filename)) {
            const format = getFileExtension(result.filename);
            showToast(`Sorry, ${format.toUpperCase()} format is not supported in web browsers. Try downloading instead.`, 'error');
            return;
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

function showToast(message, type = 'success') {
    const container = document.getElementById('toast-container');
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
window.showVersionInfo = showVersionInfo;
window.closeVersionModal = closeVersionModal;
window.testConnection = testConnection;
window.autoDetectPlex = autoDetectPlex;
window.autoDetectJellyfin = autoDetectJellyfin;
window.autoDetectSlskd = autoDetectSlskd;
window.toggleServer = toggleServer;
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
        
        const response = await fetch('/api/match/search', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                query: query,
                context: 'artist'
            })
        });
        
        const data = await response.json();
        if (data.results) {
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
    
    results.forEach(result => {
        const card = createArtistCard(result.artist, result.confidence);
        container.appendChild(card);
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
        clearInterval(dbUpdateStatusInterval);
        dbUpdateStatusInterval = null;
    }
}

async function loadDashboardData() {
    // Attach event listeners for the DB updater tool
    const updateButton = document.getElementById('db-update-button');
    if (updateButton) {
        updateButton.addEventListener('click', handleDbUpdateButtonClick);
    }

    // Initial load of stats
    await fetchAndUpdateDbStats();
    
    // Start periodic refresh of stats (every 30 seconds)
    stopDbStatsPolling(); // Ensure no duplicates
    dbStatsInterval = setInterval(fetchAndUpdateDbStats, 30000);

    // Also check the status of any ongoing update when the page loads
    await checkAndUpdateDbProgress();
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

async function checkAndUpdateDbProgress() {
    try {
        const response = await fetch('/api/database/update/status');
        if (!response.ok) return;

        const state = await response.json();
        updateDbProgressUI(state);

        if (state.status === 'running') {
            // If an update is running, start polling for progress
            stopDbUpdatePolling();
            dbUpdateStatusInterval = setInterval(checkAndUpdateDbProgress, 1000);
        }

    } catch (error) {
        console.warn('Could not fetch DB update status:', error);
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
