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
    initializeSearch();
    
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
                // Stop download polling when leaving downloads page
                stopDownloadPolling();
                await loadDashboardData();
                break;
            case 'sync':
                // Stop download polling when leaving downloads page
                stopDownloadPolling();
                await loadSyncData();
                break;
            case 'downloads':
                await loadDownloadsData();
                break;
            case 'artists':
                // Stop download polling when leaving downloads page
                stopDownloadPolling();
                await loadArtistsData();
                break;
            case 'settings':
                // Stop download polling when leaving downloads page
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
    
    saveButton.addEventListener('click', saveSettings);
    mediaServerType.addEventListener('change', updateMediaServerFields);
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
    const searchInput = document.getElementById('search-input');
    const searchButton = document.getElementById('search-button');
    
    searchButton.addEventListener('click', performSearch);
    searchInput.addEventListener('keypress', (e) => {
        if (e.key === 'Enter') performSearch();
    });
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
                        <button class="stream-button" onclick="event.stopPropagation(); startStream(${index})">
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

async function startStream(index) {
    const result = searchResults[index];
    if (!result || result.type === 'album') {
        showToast('Cannot stream albums (yet)', 'error');
        return;
    }
    
    try {
        showLoadingAnimation();
        
        const streamData = {
            username: result.username,
            filename: result.filename,
            title: result.title,
            artist: result.artist,
            album: result.album,
            quality: result.quality,
            bitrate: result.bitrate,
            duration: result.duration,
            size_mb: result.file_size / 1024 / 1024
        };
        
        const response = await fetch(API.stream.start, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(streamData)
        });
        
        const data = await response.json();
        
        if (data.error) {
            hideLoadingAnimation();
            showToast(`Stream error: ${data.error}`, 'error');
        } else {
            setTrackInfo(data.track);
            currentStream.status = 'loading';
            showToast('Starting stream...', 'success');
        }
    } catch (error) {
        hideLoadingAnimation();
        console.error('Error starting stream:', error);
        showToast('Failed to start stream', 'error');
    }
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

async function loadSyncData() {
    try {
        const response = await fetch(API.playlists);
        const data = await response.json();
        
        const playlistSelector = document.getElementById('playlist-selector');
        if (data.playlists && data.playlists.length) {
            playlistSelector.innerHTML = [
                '<option value="">Select a playlist...</option>',
                ...data.playlists.map(playlist => 
                    `<option value="${playlist.id}">${escapeHtml(playlist.name)}</option>`
                )
            ].join('');
        } else {
            playlistSelector.innerHTML = '<option value="">No playlists available</option>';
        }
    } catch (error) {
        console.error('Error loading sync data:', error);
        document.getElementById('playlist-selector').innerHTML = '<option value="">Error loading playlists</option>';
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

async function performDownloadsSearch() {
    const query = document.getElementById('downloads-search-input').value.trim();
    if (!query) {
        showToast('Please enter a search term', 'error');
        return;
    }
    
    try {
        showLoadingOverlay('Searching...');
        
        const response = await fetch('/api/search', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ query })
        });
        
        const data = await response.json();
        
        if (data.error) {
            showToast(`Search error: ${data.error}`, 'error');
            return;
        }
        
        const results = data.results || [];
        displayDownloadsResults(results);
        
        if (results.length === 0) {
            showToast('No results found', 'error');
        } else {
            showToast(`Found ${results.length} results`, 'success');
        }
        
    } catch (error) {
        console.error('Search failed:', error);
        showToast('Search failed', 'error');
    } finally {
        hideLoadingOverlay();
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
                                <button onclick="downloadAlbumTrack(${index}, ${trackIndex})" class="track-download-btn">⬇</button>
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
                        <button onclick="downloadTrack(${index})" class="track-download-btn">⬇ Download</button>
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

// Global functions (for onclick handlers)
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
window.startDownload = startDownload;
window.downloadTrack = downloadTrack;
window.downloadAlbum = downloadAlbum;
window.toggleAlbumExpansion = toggleAlbumExpansion;
window.downloadAlbumTrack = downloadAlbumTrack;
window.switchDownloadTab = switchDownloadTab;
window.cancelDownloadItem = cancelDownloadItem;
window.clearFinishedDownloads = clearFinishedDownloads;