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
    initializeSettings();
    
    // Only initialize search if on downloads page
    const currentPage = getCurrentPage();
    if (currentPage === 'downloads') {
        initializeSearch();
    }
    
    // Start periodic updates
    updateServiceStatus();
    setInterval(updateServiceStatus, 5000); // Every 5 seconds
    
    // Load initial data
    loadInitialData();
    
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

async function loadPageData(pageId) {
    try {
        switch (pageId) {
            case 'dashboard':
                await loadDashboardData();
                break;
            case 'sync':
                await loadSyncData();
                break;
            case 'downloads':
                await loadDownloadsData();
                break;
            case 'artists':
                await loadArtistsData();
                break;
            case 'settings':
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
    
    // Track title click - toggle expansion
    trackTitle.addEventListener('click', toggleMediaPlayerExpansion);
    
    // Media controls
    playButton.addEventListener('click', handlePlayPause);
    stopButton.addEventListener('click', handleStop);
    volumeSlider.addEventListener('input', handleVolumeChange);
    
    // Update volume slider styling
    volumeSlider.addEventListener('input', updateVolumeSliderAppearance);
    
    // Start stream status polling if needed
    setInterval(updateStreamStatus, 1000);
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

function setTrackInfo(track) {
    currentTrack = track;
    
    document.getElementById('track-title').textContent = track.title || 'Unknown Track';
    document.getElementById('artist-name').textContent = track.artist || 'Unknown Artist';
    document.getElementById('album-name').textContent = track.album || 'Unknown Album';
    
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

function clearTrack() {
    currentTrack = null;
    isPlaying = false;
    
    document.getElementById('track-title').textContent = 'No track';
    document.getElementById('artist-name').textContent = 'Unknown Artist';
    document.getElementById('album-name').textContent = 'Unknown Album';
    document.getElementById('play-button').textContent = '▷';
    document.getElementById('play-button').disabled = true;
    document.getElementById('stop-button').disabled = true;
    
    // Hide loading animation
    hideLoadingAnimation();
    
    // Show no track message and collapse if expanded
    document.getElementById('no-track-message').classList.remove('hidden');
    if (mediaPlayerExpanded) {
        toggleMediaPlayerExpansion();
    }
}

function setPlayingState(playing) {
    isPlaying = playing;
    const playButton = document.getElementById('play-button');
    playButton.textContent = playing ? '⏸︎' : '▷';
}

async function handlePlayPause() {
    if (!currentTrack) return;
    
    try {
        const response = await fetch(API.stream.toggle, { method: 'POST' });
        const data = await response.json();
        
        if (data.error) {
            showToast(`Playback error: ${data.error}`, 'error');
        } else {
            setPlayingState(data.playing);
        }
    } catch (error) {
        console.error('Error toggling playback:', error);
        showToast('Failed to toggle playback', 'error');
    }
}

async function handleStop() {
    try {
        const response = await fetch(API.stream.stop, { method: 'POST' });
        const data = await response.json();
        
        if (data.error) {
            showToast(`Stop error: ${data.error}`, 'error');
        } else {
            clearTrack();
            currentStream.status = 'stopped';
        }
    } catch (error) {
        console.error('Error stopping playback:', error);
        showToast('Failed to stop playback', 'error');
    }
}

function handleVolumeChange(event) {
    const volume = event.target.value;
    updateVolumeSliderAppearance();
    
    // Send volume change to backend
    fetch('/api/media/volume', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ volume: volume / 100 })
    }).catch(error => console.error('Error setting volume:', error));
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

async function updateStreamStatus() {
    try {
        const response = await fetch(API.stream.status);
        const data = await response.json();
        
        if (data.track && currentStream.status !== data.status) {
            currentStream = data;
            
            switch (data.status) {
                case 'loading':
                    setLoadingProgress(data.progress);
                    break;
                case 'playing':
                    hideLoadingAnimation();
                    setPlayingState(true);
                    break;
                case 'paused':
                    hideLoadingAnimation();
                    setPlayingState(false);
                    break;
                case 'stopped':
                    clearTrack();
                    break;
            }
        }
    } catch (error) {
        // Don't log errors for stream status - it's expected when no stream is active
    }
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
    const saveButton = document.getElementById('save-settings');
    const mediaServerType = document.getElementById('media-server-type');
    
    if (saveButton) {
        saveButton.addEventListener('click', saveSettings);
    }
    
    if (mediaServerType) {
        mediaServerType.addEventListener('change', updateMediaServerFields);
    }
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
    console.log('Initializing search functionality...');
    
    const searchInput = document.getElementById('search-input');
    const searchButton = document.getElementById('search-btn');
    const cancelButton = document.getElementById('search-cancel-btn');
    const filterToggle = document.getElementById('filter-toggle-btn');
    
    console.log('Search elements found:', {
        searchInput: !!searchInput,
        searchButton: !!searchButton,
        cancelButton: !!cancelButton,
        filterToggle: !!filterToggle
    });
    
    // Search event handlers
    if (searchButton) {
        searchButton.addEventListener('click', performSearch);
        console.log('Search button click handler added');
    }
    
    if (cancelButton) {
        cancelButton.addEventListener('click', cancelSearch);
        console.log('Cancel button click handler added');
    }
    
    if (searchInput) {
        searchInput.addEventListener('keypress', (e) => {
            if (e.key === 'Enter') {
                console.log('Enter key pressed in search input');
                performSearch();
            }
        });
        console.log('Search input keypress handler added');
    }
    
    // Filter toggle handler
    if (filterToggle) {
        filterToggle.addEventListener('click', toggleFilters);
        console.log('Filter toggle handler added');
    }
    
    // Initialize download queue updates only when on downloads page
    const downloadsPage = document.getElementById('downloads-page');
    if (downloadsPage && !downloadsPage.classList.contains('hidden')) {
        startDownloadQueueUpdates();
        initializeDownloadTabs();
        console.log('Download queue updates started');
    }
}

// Global search state
let currentSearchQuery = '';
let currentSearchResults = { tracks: [], albums: [] };
let isSearching = false;
let searchAbortController = null;

async function performSearch() {
    const searchInput = document.getElementById('search-input');
    const query = searchInput ? searchInput.value.trim() : '';
    
    if (!query) {
        updateSearchStatus('⚠️ Please enter a search term', '#ffa500');
        return;
    }
    
    if (isSearching) {
        console.log('Search already in progress, ignoring new search request');
        return;
    }
    
    console.log(`🔍 Starting search for: "${query}"`);
    
    // Cancel any existing search
    if (searchAbortController) {
        searchAbortController.abort();
    }
    
    // Create new abort controller for this search
    searchAbortController = new AbortController();
    
    try {
        // Update UI for searching state
        isSearching = true;
        currentSearchQuery = query;
        startSearchAnimations();
        clearSearchResults();
        
        const searchBtn = document.getElementById('search-btn');
        const cancelBtn = document.getElementById('search-cancel-btn');
        
        if (searchBtn) {
            searchBtn.style.display = 'none';
        }
        if (cancelBtn) {
            cancelBtn.classList.remove('hidden');
        }
        
        updateSearchStatus(`🔍 Searching for "${query}"... Results will appear as they are found`, '#1db954');
        
        // Perform the actual search
        const response = await fetch(API.search, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ query }),
            signal: searchAbortController.signal
        });
        
        if (!response.ok) {
            throw new Error(`Search failed: ${response.status} ${response.statusText}`);
        }
        
        const data = await response.json();
        
        if (data.error) {
            throw new Error(data.error);
        }
        
        // Process search results
        if (data.success && data.results) {
            currentSearchResults = {
                tracks: data.results.tracks || [],
                albums: data.results.albums || []
            };
            
            const totalResults = currentSearchResults.tracks.length + currentSearchResults.albums.length;
            
            console.log(`✅ Search completed: ${currentSearchResults.tracks.length} tracks, ${currentSearchResults.albums.length} albums (${totalResults} total)`);
            
            displaySearchResults(currentSearchResults);
            
            if (totalResults === 0) {
                updateSearchStatus('😞 No results found. Try different search terms.', '#ffa500');
            } else {
                updateSearchStatus(`✅ Found ${totalResults} results (${currentSearchResults.tracks.length} tracks, ${currentSearchResults.albums.length} albums)`, '#1db954');
                
                // Show filter controls if we have results
                const filterContainer = document.getElementById('filter-container');
                if (filterContainer && totalResults > 5) {
                    filterContainer.classList.remove('hidden');
                    initializeFilters();
                }
            }
        } else {
            throw new Error('Invalid response format');
        }
        
    } catch (error) {
        if (error.name === 'AbortError') {
            console.log('🛑 Search was cancelled');
            updateSearchStatus('Search cancelled', '#ffa500');
        } else {
            console.error('❌ Search failed:', error);
            updateSearchStatus(`❌ Search failed: ${error.message}`, '#e22134');
            showToast(`Search failed: ${error.message}`, 'error');
        }
    } finally {
        // Reset search state
        isSearching = false;
        searchAbortController = null;
        stopSearchAnimations();
        
        const searchBtn = document.getElementById('search-btn');
        const cancelBtn = document.getElementById('search-cancel-btn');
        
        if (searchBtn) {
            searchBtn.style.display = 'inline-block';
        }
        if (cancelBtn) {
            cancelBtn.classList.add('hidden');
        }
    }
}

async function cancelSearch() {
    console.log('🛑 Cancelling search...');
    
    if (searchAbortController) {
        searchAbortController.abort();
    }
    
    // Also try to cancel on the backend
    try {
        await fetch('/api/search/cancel', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' }
        });
    } catch (error) {
        console.error('Error cancelling search on backend:', error);
    }
    
    updateSearchStatus('Search cancelled', '#ffa500');
}

function updateSearchStatus(message, color = '#ffffff') {
    const statusElement = document.getElementById('search-status-text');
    if (statusElement) {
        statusElement.textContent = message;
        statusElement.style.color = color;
    }
}

function startSearchAnimations() {
    const spinner = document.getElementById('search-spinner');
    if (spinner) {
        spinner.classList.remove('hidden');
    }
}

function stopSearchAnimations() {
    const spinner = document.getElementById('search-spinner');
    if (spinner) {
        spinner.classList.add('hidden');
    }
}

function clearSearchResults() {
    const resultsContainer = document.getElementById('search-results-container');
    if (resultsContainer) {
        resultsContainer.innerHTML = '<div class="no-results-placeholder">Your search results will appear here.</div>';
    }
}

function displaySearchResults(results) {
    const resultsContainer = document.getElementById('search-results-container');
    if (!resultsContainer) {
        console.error('Search results container not found');
        return;
    }
    
    // Clear existing results
    resultsContainer.innerHTML = '';
    
    if (!results || (!results.tracks && !results.albums)) {
        resultsContainer.innerHTML = '<div class="no-results-placeholder">No search results to display.</div>';
        return;
    }
    
    const tracks = results.tracks || [];
    const albums = results.albums || [];
    
    // Display tracks first
    tracks.forEach((track, index) => {
        const trackElement = createSearchResultElement(track, index, 'track');
        resultsContainer.appendChild(trackElement);
    });
    
    // Display albums
    albums.forEach((album, index) => {
        const albumElement = createSearchResultElement(album, tracks.length + index, 'album');
        resultsContainer.appendChild(albumElement);
    });
    
    if (tracks.length === 0 && albums.length === 0) {
        resultsContainer.innerHTML = '<div class="no-results-placeholder">No results found for your search.</div>';
    }
}

function createSearchResultElement(result, index, type) {
    const resultDiv = document.createElement('div');
    resultDiv.className = 'search-result-item';
    resultDiv.setAttribute('data-index', index);
    resultDiv.setAttribute('data-type', type);
    
    // Format file size
    const fileSizeFormatted = result.file_size ? formatFileSize(result.file_size) : 'Unknown size';
    const bitrate = result.bitrate ? `${result.bitrate} kbps` : '';
    const duration = result.duration ? formatDuration(result.duration) : '';
    
    resultDiv.innerHTML = `
        <div class="result-header">
            <h4 class="result-title">${escapeHtml(result.title || 'Unknown Title')}</h4>
            <span class="result-type-badge ${type}">${type.toUpperCase()}</span>
        </div>
        <div class="result-details">
            <div class="result-detail-item">
                <span>🎤 ${escapeHtml(result.artist || 'Unknown Artist')}</span>
            </div>
            ${result.album ? `<div class="result-detail-item"><span>💿 ${escapeHtml(result.album)}</span></div>` : ''}
            ${result.quality ? `<div class="result-detail-item"><span>🎵 ${result.quality}</span></div>` : ''}
            ${bitrate ? `<div class="result-detail-item"><span>⚡ ${bitrate}</span></div>` : ''}
            ${duration ? `<div class="result-detail-item"><span>⏱️ ${duration}</span></div>` : ''}
            <div class="result-detail-item">
                <span>📂 ${fileSizeFormatted}</span>
            </div>
            <div class="result-detail-item">
                <span>👤 ${escapeHtml(result.username || 'Unknown User')}</span>
            </div>
            ${type === 'album' && result.track_count ? `<div class="result-detail-item"><span>🎼 ${result.track_count} tracks</span></div>` : ''}
        </div>
        <div class="result-actions">
            <button class="result-btn download" onclick="startDownloadWithModal(${index}, '${type}')">
                ⬇️ Download
            </button>
            ${type === 'track' ? `<button class="result-btn stream" onclick="startStream(${index})">▶️ Stream</button>` : ''}
        </div>
    `;
    
    return resultDiv;
}

// Utility functions for search results
function formatFileSize(bytes) {
    if (!bytes || bytes === 0) return 'Unknown size';
    const sizes = ['B', 'KB', 'MB', 'GB'];
    const i = Math.floor(Math.log(bytes) / Math.log(1024));
    return Math.round(bytes / Math.pow(1024, i) * 100) / 100 + ' ' + sizes[i];
}

function formatDuration(seconds) {
    if (!seconds || seconds === 0) return '';
    const minutes = Math.floor(seconds / 60);
    const remainingSeconds = seconds % 60;
    return `${minutes}:${remainingSeconds.toString().padStart(2, '0')}`;
}

function toggleFilters() {
    const filterContent = document.getElementById('filter-content');
    const filterToggle = document.getElementById('filter-toggle-btn');
    
    if (filterContent && filterToggle) {
        if (filterContent.classList.contains('hidden')) {
            filterContent.classList.remove('hidden');
            filterToggle.textContent = '⏶ Filters';
        } else {
            filterContent.classList.add('hidden');
            filterToggle.textContent = '⏷ Filters';
        }
    }
}

function initializeFilters() {
    // This would implement filter functionality
    // For now, it's a placeholder
    console.log('Initializing filters...');
}

// ===============================
// DOWNLOAD FUNCTIONALITY
// ===============================

// Global download state
let selectedSearchResult = null;
let currentDownloadModal = null;

async function startDownloadWithModal(index, type) {
    console.log(`⬇️ Starting download with modal for index ${index}, type ${type}`);
    
    // Get the search result based on type and index
    let searchResult;
    if (type === 'track') {
        searchResult = currentSearchResults.tracks[index];
    } else if (type === 'album') {
        searchResult = currentSearchResults.albums[index];
    }
    
    if (!searchResult) {
        showToast('Search result not found', 'error');
        return;
    }
    
    selectedSearchResult = searchResult;
    
    // Show the Spotify matching modal
    openSpotifyMatchingModal(searchResult, type);
}

async function startDirectDownload(searchResult) {
    
    if (!searchResult) {
        showToast('No search result provided for download', 'error');
        return;
    }
    
    try {
        console.log(`⬇️ Starting direct download: ${searchResult.title}`);
        
        const response = await fetch('/api/downloads/start', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(searchResult)
        });
        
        const data = await response.json();
        
        if (data.success) {
            showToast(`Download started: ${searchResult.title}`, 'success');
            console.log(`✅ Download started: ${data.download_id}`);
        } else {
            throw new Error(data.error || 'Unknown error');
        }
        
    } catch (error) {
        console.error('❌ Download failed:', error);
        showToast(`Download failed: ${error.message}`, 'error');
    }
}

async function startMatchedDownload(searchResult, spotifyMatch) {
    
    if (!searchResult || !spotifyMatch) {
        showToast('Missing search result or Spotify match for download', 'error');
        return;
    }
    
    try {
        console.log(`⬇️🎵 Starting matched download: ${searchResult.title}`);
        console.log(`   🎤 Matched to: ${spotifyMatch.artist.name} - ${spotifyMatch.album ? spotifyMatch.album.name : 'Single'}`);
        
        const downloadData = {
            ...searchResult,
            spotify_match: spotifyMatch
        };
        
        const response = await fetch('/api/downloads/start-matched', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(downloadData)
        });
        
        const data = await response.json();
        
        if (data.success) {
            showToast(`Matched download started: ${searchResult.title}`, 'success');
            console.log(`✅ Matched download started: ${data.download_id}`);
        } else {
            throw new Error(data.error || 'Unknown error');
        }
        
    } catch (error) {
        console.error('❌ Matched download failed:', error);
        showToast(`Matched download failed: ${error.message}`, 'error');
    }
}

// ===============================
// DOWNLOAD QUEUE MANAGEMENT
// ===============================

let downloadQueueUpdateInterval = null;

function startDownloadQueueUpdates() {
    // Update immediately
    updateDownloadQueues();
    
    // Update every 2 seconds
    downloadQueueUpdateInterval = setInterval(updateDownloadQueues, 2000);
}

function stopDownloadQueueUpdates() {
    if (downloadQueueUpdateInterval) {
        clearInterval(downloadQueueUpdateInterval);
        downloadQueueUpdateInterval = null;
    }
}

async function updateDownloadQueues() {
    try {
        const response = await fetch('/api/downloads/status');
        const data = await response.json();
        
        if (data.success) {
            updateDownloadQueueDisplay(data.downloads);
            updateDownloadCounts(data.downloads);
        }
        
    } catch (error) {
        console.error('Error updating download queues:', error);
    }
}

function updateDownloadQueueDisplay(downloads) {
    const activeQueue = document.getElementById('active-queue');
    const finishedQueue = document.getElementById('finished-queue');
    
    if (activeQueue) {
        displayDownloadQueue(activeQueue, downloads.active, 'active');
    }
    
    if (finishedQueue) {
        displayDownloadQueue(finishedQueue, downloads.completed, 'completed');
    }
}

function displayDownloadQueue(container, downloads, queueType) {
    if (!container) return;
    
    // Clear existing content
    container.innerHTML = '';
    
    if (!downloads || downloads.length === 0) {
        const emptyMessage = document.createElement('div');
        emptyMessage.className = 'empty-queue-message';
        emptyMessage.textContent = queueType === 'active' ? 'No active downloads.' : 'No finished downloads.';
        container.appendChild(emptyMessage);
        return;
    }
    
    downloads.forEach(download => {
        const downloadElement = createDownloadQueueItem(download, queueType);
        container.appendChild(downloadElement);
    });
}

function createDownloadQueueItem(download, queueType) {
    const itemDiv = document.createElement('div');
    itemDiv.className = 'download-queue-item';
    itemDiv.setAttribute('data-download-id', download.id);
    
    const progress = download.progress || 0;
    const status = download.status || 'unknown';
    const speedFormatted = download.download_speed ? formatSpeed(download.download_speed) : '';
    
    itemDiv.innerHTML = `
        <div class="download-item-header">
            <div class="download-item-title">${escapeHtml(download.title || 'Unknown Title')}</div>
            <div class="download-item-status ${status}">${status.toUpperCase()}</div>
        </div>
        <div class="download-item-details">
            🎤 ${escapeHtml(download.artist || 'Unknown Artist')} • 👤 ${escapeHtml(download.username || 'Unknown User')}
            ${speedFormatted ? ` • ⚡ ${speedFormatted}` : ''}
            ${download.spotify_matched ? ' • 🎵 Spotify Matched' : ''}
        </div>
        ${queueType === 'active' && progress !== undefined ? `
            <div class="download-progress">
                <div class="download-progress-fill" style="width: ${progress}%"></div>
            </div>
            <div class="download-progress-text">${progress}% Complete</div>
        ` : ''}
        ${queueType === 'active' ? `
            <button class="result-btn secondary" onclick="cancelDownload('${download.id}')" style="margin-top: 8px; padding: 4px 8px; font-size: 10px;">
                🛑 Cancel
            </button>
        ` : ''}
    `;
    
    return itemDiv;
}

function updateDownloadCounts(downloads) {
    const activeCount = document.getElementById('active-downloads-count');
    const finishedCount = document.getElementById('finished-downloads-count');
    
    if (activeCount) {
        activeCount.textContent = downloads.active_count || 0;
    }
    
    if (finishedCount) {
        finishedCount.textContent = downloads.completed_count || 0;
    }
}

async function cancelDownload(downloadId) {
    try {
        const response = await fetch(`/api/downloads/cancel/${downloadId}`, {
            method: 'POST'
        });
        
        const data = await response.json();
        
        if (data.success) {
            showToast('Download cancelled', 'success');
        } else {
            throw new Error(data.error || 'Failed to cancel download');
        }
        
    } catch (error) {
        console.error('Error cancelling download:', error);
        showToast(`Failed to cancel download: ${error.message}`, 'error');
    }
}

async function clearCompletedDownloads() {
    try {
        const response = await fetch('/api/downloads/clear-completed', {
            method: 'POST'
        });
        
        const data = await response.json();
        
        if (data.success) {
            showToast('Completed downloads cleared', 'success');
            updateDownloadQueues(); // Refresh the display
        } else {
            throw new Error(data.error || 'Failed to clear completed downloads');
        }
        
    } catch (error) {
        console.error('Error clearing completed downloads:', error);
        showToast(`Failed to clear downloads: ${error.message}`, 'error');
    }
}

function initializeDownloadTabs() {
    console.log('Initializing download tabs...');
    const tabButtons = document.querySelectorAll('.tab-btn');
    const clearButton = document.getElementById('clear-completed-btn');
    
    console.log(`Found ${tabButtons.length} tab buttons`);
    
    tabButtons.forEach((button, index) => {
        const targetTab = button.getAttribute('data-tab');
        console.log(`Tab button ${index}: data-tab="${targetTab}"`);
        
        button.addEventListener('click', () => {
            console.log(`Tab clicked: ${targetTab}`);
            switchDownloadTab(targetTab);
            
            // Update tab button states
            tabButtons.forEach(btn => btn.classList.remove('active'));
            button.classList.add('active');
        });
    });
    
    if (clearButton) {
        clearButton.addEventListener('click', clearCompletedDownloads);
        console.log('Clear completed button handler added');
    }
}

function switchDownloadTab(tabName) {
    console.log(`Switching to tab: ${tabName}`);
    const allQueues = document.querySelectorAll('.download-queue');
    console.log(`Found ${allQueues.length} download queues`);
    
    allQueues.forEach(queue => {
        console.log(`Removing active from: ${queue.id}`);
        queue.classList.remove('active');
    });
    
    const targetQueue = document.getElementById(tabName);
    if (targetQueue) {
        console.log(`Adding active to: ${tabName}`);
        targetQueue.classList.add('active');
    } else {
        console.log(`Target queue not found: ${tabName}`);
    }
}

function formatSpeed(bytesPerSecond) {
    if (!bytesPerSecond || bytesPerSecond === 0) return '';
    const sizes = ['B/s', 'KB/s', 'MB/s', 'GB/s'];
    const i = Math.floor(Math.log(bytesPerSecond) / Math.log(1024));
    return Math.round(bytesPerSecond / Math.pow(1024, i) * 100) / 100 + ' ' + sizes[i];
}

// ===============================
// SPOTIFY MATCHING MODAL
// ===============================

// Global Spotify modal state
let selectedArtist = null;
let selectedAlbum = null;
let modalStage = 'artist'; // 'artist' or 'album'
let isForAlbumDownload = false;

async function openSpotifyMatchingModal(searchResult, type) {
    console.log(`🎵 Opening Spotify matching modal for: ${searchResult.title} by ${searchResult.artist}`);
    
    selectedSearchResult = searchResult;
    isForAlbumDownload = (type === 'album');
    selectedArtist = null;
    selectedAlbum = null;
    modalStage = 'artist';
    
    // Show the modal
    const modalOverlay = document.getElementById('spotify-matching-modal-overlay');
    if (modalOverlay) {
        modalOverlay.classList.remove('hidden');
    }
    
    // Update modal title and subtitle
    updateModalHeader();
    
    // Generate artist suggestions
    await generateSpotifyArtistSuggestions(searchResult);
    
    // Setup manual search
    setupManualSearch();
}

function closeSpotifyMatchingModal() {
    const modalOverlay = document.getElementById('spotify-matching-modal-overlay');
    if (modalOverlay) {
        modalOverlay.classList.add('hidden');
    }
    
    // Reset modal state
    selectedArtist = null;
    selectedAlbum = null;
    modalStage = 'artist';
    selectedSearchResult = null;
    isForAlbumDownload = false;
    
    // Clear suggestions
    clearModalSuggestions();
    clearManualSearchResults();
}

function skipSpotifyMatching() {
    console.log('⏭️ Skipping Spotify matching, starting direct download');
    
    if (selectedSearchResult) {
        startDirectDownload(selectedSearchResult);
    }
    
    closeSpotifyMatchingModal();
}

function updateModalHeader() {
    const titleElement = document.getElementById('spotify-modal-title');
    const subtitleElement = document.getElementById('spotify-modal-subtitle');
    
    if (isForAlbumDownload) {
        if (modalStage === 'artist') {
            if (titleElement) titleElement.textContent = 'Match Album to Spotify';
            if (subtitleElement) subtitleElement.textContent = 'Step 1: Select the correct Artist';
        } else {
            if (titleElement) titleElement.textContent = 'Match Album to Spotify';
            if (subtitleElement) subtitleElement.textContent = 'Step 2: Select the correct Album';
        }
    } else {
        if (titleElement) titleElement.textContent = 'Match Track to Spotify';
        if (subtitleElement) subtitleElement.textContent = 'Select the correct Artist for this Track';
    }
}

async function generateSpotifyArtistSuggestions(searchResult) {
    const suggestionsGrid = document.getElementById('auto-suggestions-grid');
    if (!suggestionsGrid) return;
    
    // Show loading state
    suggestionsGrid.innerHTML = `
        <div class="loading-suggestions">
            <div class="suggestion-loading-spinner"></div>
            <span>Generating suggestions...</span>
        </div>
    `;
    
    try {
        const response = await fetch('/api/spotify/suggestions', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                title: searchResult.title,
                artist: searchResult.artist
            })
        });
        
        const data = await response.json();
        
        if (data.success && data.suggestions) {
            displayArtistSuggestions(data.suggestions);
        } else {
            throw new Error(data.error || 'Failed to generate suggestions');
        }
        
    } catch (error) {
        console.error('Error generating artist suggestions:', error);
        suggestionsGrid.innerHTML = `
            <div class="loading-suggestions">
                <span>⚠️ Failed to load suggestions</span>
            </div>
        `;
    }
}

function displayArtistSuggestions(artists) {
    const suggestionsGrid = document.getElementById('auto-suggestions-grid');
    if (!suggestionsGrid) return;
    
    suggestionsGrid.innerHTML = '';
    
    artists.forEach(artist => {
        const artistCard = createSpotifyCard(artist, 'artist');
        suggestionsGrid.appendChild(artistCard);
    });
}

function displayAlbumSuggestions(albums) {
    const suggestionsGrid = document.getElementById('auto-suggestions-grid');
    if (!suggestionsGrid) return;
    
    suggestionsGrid.innerHTML = '';
    
    albums.forEach(album => {
        const albumCard = createSpotifyCard(album, 'album');
        suggestionsGrid.appendChild(albumCard);
    });
}

function createSpotifyCard(item, type) {
    const cardDiv = document.createElement('div');
    cardDiv.className = 'spotify-card';
    cardDiv.setAttribute('data-id', item.id);
    cardDiv.setAttribute('data-type', type);
    
    // Add click handler
    cardDiv.addEventListener('click', () => selectSpotifyItem(item, type, cardDiv));
    
    let details = '';
    if (type === 'artist') {
        const genres = item.genres && item.genres.length > 0 ? item.genres.slice(0, 2).join(', ') : 'No genres';
        const followers = item.follower_count ? formatNumber(item.follower_count) + ' followers' : '';
        details = `${genres}${followers ? ' • ' + followers : ''}`;
    } else if (type === 'album') {
        const releaseYear = item.release_date ? new Date(item.release_date).getFullYear() : '';
        const trackCount = item.total_tracks ? `${item.total_tracks} tracks` : '';
        details = `${releaseYear ? releaseYear + ' • ' : ''}${trackCount}`;
    }
    
    cardDiv.innerHTML = `
        <div class="spotify-card-image">
            ${item.image_url ? 
                `<img src="${item.image_url}" alt="${escapeHtml(item.name)}" onerror="this.style.display='none'; this.nextElementSibling.style.display='block';">
                 <div class="spotify-card-image-placeholder" style="display: none;">🎵</div>` :
                `<div class="spotify-card-image-placeholder">🎵</div>`
            }
        </div>
        <div class="spotify-card-name">${escapeHtml(item.name)}</div>
        <div class="spotify-card-details">${details}</div>
    `;
    
    return cardDiv;
}

function selectSpotifyItem(item, type, cardElement) {
    console.log(`🎯 Selected ${type}: ${item.name}`);
    
    // Update selection state
    if (type === 'artist') {
        selectedArtist = item;
        
        // Update visual selection
        document.querySelectorAll('.spotify-card').forEach(card => card.classList.remove('selected'));
        cardElement.classList.add('selected');
        
        // Enable confirm button or move to album selection
        if (isForAlbumDownload) {
            // For albums, proceed to album selection
            proceedToAlbumSelection();
        } else {
            // For tracks, enable confirm button
            enableConfirmButton();
        }
    } else if (type === 'album') {
        selectedAlbum = item;
        
        // Update visual selection
        document.querySelectorAll('.spotify-card').forEach(card => card.classList.remove('selected'));
        cardElement.classList.add('selected');
        
        // Enable confirm button
        enableConfirmButton();
    }
}

async function proceedToAlbumSelection() {
    console.log(`🎵 Proceeding to album selection for artist: ${selectedArtist.name}`);
    
    modalStage = 'album';
    updateModalHeader();
    
    // Clear manual search
    clearManualSearchResults();
    const manualSearch = document.getElementById('spotify-manual-search');
    if (manualSearch) {
        manualSearch.value = '';
        manualSearch.placeholder = 'Manually search for an album...';
    }
    
    // Load albums for selected artist
    await loadArtistAlbums(selectedArtist.id);
}

async function loadArtistAlbums(artistId) {
    const suggestionsGrid = document.getElementById('auto-suggestions-grid');
    if (!suggestionsGrid) return;
    
    // Show loading state
    suggestionsGrid.innerHTML = `
        <div class="loading-suggestions">
            <div class="suggestion-loading-spinner"></div>
            <span>Loading albums...</span>
        </div>
    `;
    
    try {
        const response = await fetch('/api/spotify/search-album', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                artist_id: artistId
            })
        });
        
        const data = await response.json();
        
        if (data.success && data.albums) {
            displayAlbumSuggestions(data.albums);
        } else {
            throw new Error(data.error || 'Failed to load albums');
        }
        
    } catch (error) {
        console.error('Error loading artist albums:', error);
        suggestionsGrid.innerHTML = `
            <div class="loading-suggestions">
                <span>⚠️ Failed to load albums</span>
            </div>
        `;
    }
}

function setupManualSearch() {
    const manualSearch = document.getElementById('spotify-manual-search');
    if (!manualSearch) return;
    
    // Clear previous listeners
    manualSearch.replaceWith(manualSearch.cloneNode(true));
    const newManualSearch = document.getElementById('spotify-manual-search');
    
    let searchTimeout;
    newManualSearch.addEventListener('input', (e) => {
        clearTimeout(searchTimeout);
        searchTimeout = setTimeout(() => {
            performManualSpotifySearch(e.target.value.trim());
        }, 500); // Debounce search
    });
}

async function performManualSpotifySearch(query) {
    if (!query || query.length < 2) {
        clearManualSearchResults();
        return;
    }
    
    const resultsContainer = document.getElementById('manual-search-results');
    if (!resultsContainer) return;
    
    try {
        let endpoint, searchData;
        
        if (modalStage === 'artist') {
            endpoint = '/api/spotify/search-artist';
            searchData = { query };
        } else {
            endpoint = '/api/spotify/search-album';
            searchData = { artist_id: selectedArtist.id, query };
        }
        
        const response = await fetch(endpoint, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(searchData)
        });
        
        const data = await response.json();
        
        if (data.success) {
            const items = modalStage === 'artist' ? data.artists : data.albums;
            displayManualSearchResults(items, modalStage);
        } else {
            throw new Error(data.error || 'Search failed');
        }
        
    } catch (error) {
        console.error('Manual search error:', error);
        clearManualSearchResults();
    }
}

function displayManualSearchResults(items, type) {
    const resultsContainer = document.getElementById('manual-search-results');
    if (!resultsContainer) return;
    
    resultsContainer.innerHTML = '';
    
    items.slice(0, 4).forEach(item => { // Limit to 4 results
        const card = createSpotifyCard(item, type);
        resultsContainer.appendChild(card);
    });
}

function clearModalSuggestions() {
    const suggestionsGrid = document.getElementById('auto-suggestions-grid');
    if (suggestionsGrid) {
        suggestionsGrid.innerHTML = '';
    }
}

function clearManualSearchResults() {
    const resultsContainer = document.getElementById('manual-search-results');
    if (resultsContainer) {
        resultsContainer.innerHTML = '';
    }
}

function enableConfirmButton() {
    const confirmButton = document.getElementById('confirm-spotify-match');
    if (confirmButton) {
        confirmButton.disabled = false;
    }
}

function disableConfirmButton() {
    const confirmButton = document.getElementById('confirm-spotify-match');
    if (confirmButton) {
        confirmButton.disabled = true;
    }
}

async function confirmSpotifyMatch() {
    if (!selectedArtist) {
        showToast('Please select an artist first', 'error');
        return;
    }
    
    if (isForAlbumDownload && !selectedAlbum) {
        showToast('Please select an album first', 'error');
        return;
    }
    
    console.log(`✅ Confirming Spotify match:`);
    console.log(`   🎤 Artist: ${selectedArtist.name}`);
    if (selectedAlbum) {
        console.log(`   💿 Album: ${selectedAlbum.name}`);
    }
    
    const spotifyMatch = {
        artist: selectedArtist,
        album: selectedAlbum
    };
    
    // Start the matched download
    await startMatchedDownload(selectedSearchResult, spotifyMatch);
    
    // Close the modal
    closeSpotifyMatchingModal();
}

function formatNumber(num) {
    if (num >= 1000000) {
        return Math.round(num / 100000) / 10 + 'M';
    } else if (num >= 1000) {
        return Math.round(num / 100) / 10 + 'K';
    }
    return num.toString();
}

// ===============================
// GLOBAL FUNCTION DECLARATIONS
// ===============================

// Make all functions globally available for onclick handlers
window.startDownloadWithModal = startDownloadWithModal;
window.cancelDownload = cancelDownload;
window.clearCompletedDownloads = clearCompletedDownloads;
window.openSpotifyMatchingModal = openSpotifyMatchingModal;
window.closeSpotifyMatchingModal = closeSpotifyMatchingModal;
window.skipSpotifyMatching = skipSpotifyMatching;
window.confirmSpotifyMatch = confirmSpotifyMatch;
window.showToast = showToast;

// ===============================
// MISSING UTILITY FUNCTIONS
// ===============================

function escapeHtml(text) {
    if (!text) return '';
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}

function showToast(message, type = 'info') {
    console.log(`Toast [${type}]: ${message}`);
    
    // Create toast container if it doesn't exist
    let toastContainer = document.getElementById('toast-container');
    if (!toastContainer) {
        toastContainer = document.createElement('div');
        toastContainer.id = 'toast-container';
        toastContainer.style.cssText = `
            position: fixed;
            top: 20px;
            right: 20px;
            z-index: 10001;
            display: flex;
            flex-direction: column;
            gap: 10px;
        `;
        document.body.appendChild(toastContainer);
    }
    
    // Create toast element
    const toast = document.createElement('div');
    toast.className = `toast toast-${type}`;
    toast.textContent = message;
    toast.style.cssText = `
        padding: 12px 16px;
        border-radius: 6px;
        color: white;
        font-size: 14px;
        max-width: 300px;
        opacity: 0;
        transform: translateX(100%);
        transition: all 0.3s ease;
        cursor: pointer;
        ${type === 'success' ? 'background: #1db954;' : ''}
        ${type === 'error' ? 'background: #e22134;' : ''}
        ${type === 'info' ? 'background: #1ed760;' : ''}
    `;
    
    // Add click to dismiss
    toast.addEventListener('click', () => {
        removeToast(toast);
    });
    
    // Add to container
    toastContainer.appendChild(toast);
    
    // Animate in
    setTimeout(() => {
        toast.style.opacity = '1';
        toast.style.transform = 'translateX(0)';
    }, 10);
    
    // Auto remove after 4 seconds
    setTimeout(() => {
        removeToast(toast);
    }, 4000);
}

function removeToast(toast) {
    if (toast && toast.parentNode) {
        toast.style.opacity = '0';
        toast.style.transform = 'translateX(100%)';
        setTimeout(() => {
            if (toast.parentNode) {
                toast.parentNode.removeChild(toast);
            }
        }, 300);
    }
}

// ===============================
// MISSING PAGE LOAD FUNCTIONS
// ===============================

async function loadDownloadsData() {
    console.log('Loading downloads page...');
    // Initialize search functionality when loading downloads page
    initializeSearch();
}

function getCurrentPage() {
    // Find which page is currently visible (not hidden)
    const pages = ['dashboard', 'sync', 'downloads', 'artists', 'settings'];
    for (const page of pages) {
        const pageElement = document.getElementById(`${page}-page`);
        if (pageElement && !pageElement.classList.contains('hidden')) {
            return page;
        }
    }
    return 'dashboard'; // Default fallback
}

async function loadSyncData() {
    console.log('Loading sync page...');
    // Placeholder for sync page data loading
}

async function loadArtistsData() {
    console.log('Loading artists page...');
    // Placeholder for artists page data loading
}

async function loadSettingsData() {
    console.log('Loading settings page...');
    // Settings page data is loaded by initializeSettings()
}
