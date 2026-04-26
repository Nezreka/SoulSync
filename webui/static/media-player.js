// MEDIA PLAYER FUNCTIONALITY
// ===============================

function initializeMediaPlayer() {
    const trackTitle = document.getElementById('track-title');
    const playButton = document.getElementById('play-button');
    const stopButton = document.getElementById('stop-button');
    const volumeSlider = document.getElementById('volume-slider');

    // Start in idle state (no track playing)
    const player = document.getElementById('media-player');
    if (player && !currentTrack) player.classList.add('idle');

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
        if (volumeSlider) volumeSlider.value = 70;
    }

    // Track title click handled by initExpandedPlayer's media-player click handler

    // Media controls
    playButton.addEventListener('click', handlePlayPause);
    stopButton.addEventListener('click', handleStop);
    if (volumeSlider) volumeSlider.addEventListener('input', handleVolumeChange);

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
    if (volumeSlider) volumeSlider.addEventListener('input', updateVolumeSliderAppearance);

    // Mini player prev / next buttons
    const miniPrevBtn = document.getElementById('mini-prev-btn');
    const miniNextBtn = document.getElementById('mini-next-btn');
    if (miniPrevBtn) miniPrevBtn.addEventListener('click', (e) => { e.stopPropagation(); playPreviousInQueue(); });
    if (miniNextBtn) miniNextBtn.addEventListener('click', (e) => { e.stopPropagation(); playNextInQueue(); });
}

function toggleMediaPlayerExpansion() {
    // No-op: controls are always visible in the new layout.
    // Kept for backward compatibility with any callers.
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

    // Hide no track message and expand player
    document.getElementById('no-track-message').classList.add('hidden');
    document.getElementById('media-player').classList.remove('idle');

    // Sync expanded player and media session
    updateNpTrackInfo();
    updateMediaSessionMetadata();
    updateMediaSessionPlaybackState();
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
    // Clear track state
    currentTrack = null;
    isPlaying = false;

    const trackTitleElement = document.getElementById('track-title');
    trackTitleElement.innerHTML = '<span class="title-text">No track</span>';
    trackTitleElement.classList.remove('scrolling'); // Remove scrolling animation
    trackTitleElement.style.removeProperty('--scroll-distance'); // Clear CSS variable

    document.getElementById('artist-name').textContent = 'Unknown Artist';
    document.getElementById('album-name').textContent = 'Unknown Album';
    // Reset play button SVGs (don't use textContent — it destroys SVG children)
    const clearPlayBtn = document.getElementById('play-button');
    const clearPlayIcon = clearPlayBtn.querySelector('.play-icon');
    const clearPauseIcon = clearPlayBtn.querySelector('.pause-icon');
    if (clearPlayIcon) clearPlayIcon.style.display = '';
    if (clearPauseIcon) clearPauseIcon.style.display = 'none';
    clearPlayBtn.disabled = true;
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

    // Show no track message and collapse player
    document.getElementById('no-track-message').classList.remove('hidden');
    document.getElementById('media-player').classList.add('idle');

    // Reset queue state
    npQueue = [];
    npQueueIndex = -1;

    // Sync expanded player and media session
    updateNpTrackInfo();
    updateNpPlayButton();
    updateNpProgress();
    renderNpQueue();
    updateNpPrevNextButtons();
    updateMediaSessionPlaybackState();
    stopSidebarVisualizer();
    if (npModalOpen) closeNowPlayingModal();

    console.log('🧹 Track cleared and media player reset');
}

function setPlayingState(playing) {
    isPlaying = playing;
    const playButton = document.getElementById('play-button');
    // Toggle SVG icons (don't use textContent — it destroys SVG children)
    const playIcon = playButton.querySelector('.play-icon');
    const pauseIcon = playButton.querySelector('.pause-icon');
    if (playIcon) playIcon.style.display = playing ? 'none' : '';
    if (pauseIcon) pauseIcon.style.display = playing ? '' : 'none';
    updateNpPlayButton();
    updateMediaSessionPlaybackState();

    // Sidebar audio visualizer
    if (playing) {
        npInitVisualizer();
        startSidebarVisualizer();
    } else {
        stopSidebarVisualizer();
    }
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

    // Sync modal volume and clear mute state
    npMuted = false;
    const npVol = document.getElementById('np-volume-slider');
    const npFill = document.getElementById('np-volume-fill');
    if (npVol) npVol.value = volume;
    if (npFill) npFill.style.width = volume + '%';
    updateNpMuteIcon();
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

        // Sync modal progress
        const npBar = document.getElementById('np-progress-bar');
        const npFill = document.getElementById('np-progress-fill');
        const npTime = document.getElementById('np-current-time');
        if (npBar) npBar.value = progress;
        if (npFill) npFill.style.width = progress + '%';
        if (npTime) npTime.textContent = formatTime(newTime);
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
    if (!slider) return;
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

let _streamLock = false;

async function startStream(searchResult) {
    // Start streaming a track - handles same track toggle and new track streaming
    try {
        // Prevent multiple concurrent stream starts (rapid clicking)
        if (_streamLock) {
            console.log('⏳ Stream already starting, ignoring duplicate click');
            return;
        }

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

        // Lock to prevent duplicate stream starts
        _streamLock = true;

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
            filename: searchResult.filename,
            image_url: searchResult.image_url || searchResult.album_cover_url || null
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
    } finally {
        _streamLock = false;
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

// Phase 4: Track last known tool statuses to prevent repeated toasts on terminal states
let _lastToolStatus = {};

// Phase 5: Sync/Discovery/Scan WebSocket router functions
function updateSyncProgressFromData(data) {
    const pid = data.playlist_id;
    const callback = _syncProgressCallbacks[pid];
    if (callback) callback(data);
}

function updateDiscoveryProgressFromData(data) {
    const id = data.id;
    const callback = _discoveryProgressCallbacks[id];
    if (callback) callback(data);
}

function updateWatchlistScanFromData(data) {
    if (!data.success) return;
    if (_lastWatchlistScanStatus === data.status && data.status !== 'scanning') return;
    _lastWatchlistScanStatus = data.status;
    handleWatchlistScanData(data);
}

function updateMediaScanFromData(data) {
    if (!data.success || !data.status) return;
    const status = data.status;
    const statusKey = status.is_scanning ? 'scanning' : (status.status || 'unknown');
    if (_lastMediaScanStatus === statusKey && statusKey !== 'scanning') return;
    _lastMediaScanStatus = statusKey;

    const phaseLabel = document.getElementById('media-scan-phase-label');
    const progressLabel = document.getElementById('media-scan-progress-label');
    const button = document.getElementById('media-scan-btn');
    const progressBar = document.getElementById('media-scan-progress-bar');
    const statusValue = document.getElementById('media-scan-status');

    if (status.is_scanning) {
        if (phaseLabel) phaseLabel.textContent = 'Media server scanning...';
        if (progressLabel) progressLabel.textContent = status.progress_message || 'Scan in progress';
    } else if (status.status === 'idle') {
        if (button) button.disabled = false;
        if (phaseLabel) phaseLabel.textContent = 'Scan completed successfully';
        if (progressBar) progressBar.style.width = '0%';
        if (progressLabel) progressLabel.textContent = 'Ready for next scan';
        if (statusValue) {
            statusValue.textContent = 'Idle';
            statusValue.style.color = '#b3b3b3';
        }
        showToast('✅ Media scan completed', 'success', 3000);
    }
}

let _wishlistAutoProcessingNotified = false;
function updateWishlistStatsFromData(data) {
    // Auto-processing detection: close modal and notify (once only)
    if (data.is_auto_processing) {
        if (!_wishlistAutoProcessingNotified) {
            if (currentPage === 'wishlist') navigateToPage('active-downloads');
            showToast('Wishlist auto-processing started. View progress in Download Manager.', 'info');
            _wishlistAutoProcessingNotified = true;
        }
        return;
    }
    // Reset flag when auto-processing ends
    _wishlistAutoProcessingNotified = false;
    // Store latest stats for countdown timer refresh
    _lastWishlistStats = data;
}

async function updateStreamStatus() {
    if (socketConnected) return; // WebSocket handles this
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
                // Restore player UI if JS state was wiped (e.g. page refresh)
                if (!currentTrack && data.track_info) {
                    const ti = data.track_info;
                    setTrackInfo({
                        title: ti.name || ti.title || 'Unknown Track',
                        artist: ti.artist || 'Unknown Artist',
                        album: ti.album || 'Unknown Album',
                        filename: ti.filename || '',
                        is_library: !!ti.is_library,
                        image_url: ti.image_url || null,
                        id: ti.id || null,
                        artist_id: ti.artist_id || null,
                        album_id: ti.album_id || null,
                    });
                }
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
                // Handle stopped state — do NOT clear track here; explicit stop (handleStop)
                // calls clearTrack() directly. Clearing here collapses the player mid-playback
                // when the backend transitions to 'stopped' after audio naturally ends or during
                // queue track transitions.
                console.log('🛑 Stream stopped');
                stopStreamStatusPolling();
                hideLoadingAnimation();
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

function updateStreamStatusFromData(data) {
    const prev = _lastToolStatus['stream'];
    _lastToolStatus['stream'] = data.status;
    // Skip repeated terminal states to avoid duplicate toasts/actions
    if (prev !== undefined && data.status === prev && data.status !== 'loading' && data.status !== 'queued') return;

    currentStream.status = data.status;
    currentStream.progress = data.progress;

    switch (data.status) {
        case 'loading':
            setLoadingProgress(data.progress);
            const loadingText = document.querySelector('.loading-text');
            if (loadingText && data.progress > 0) {
                loadingText.textContent = `Downloading... ${Math.round(data.progress)}%`;
            }
            break;
        case 'queued':
            const queueText = document.querySelector('.loading-text');
            if (queueText) {
                queueText.textContent = 'Queuing with uploader...';
            }
            setLoadingProgress(0);
            break;
        case 'ready':
            console.log('🎵 Stream ready, starting audio playback');
            stopStreamStatusPolling();
            // Restore player UI if JS state was wiped (e.g. page refresh)
            if (!currentTrack && data.track_info) {
                const ti = data.track_info;
                setTrackInfo({
                    title: ti.name || ti.title || 'Unknown Track',
                    artist: ti.artist || 'Unknown Artist',
                    album: ti.album || 'Unknown Album',
                    filename: ti.filename || '',
                    is_library: !!ti.is_library,
                    image_url: ti.image_url || null,
                    id: ti.id || null,
                    artist_id: ti.artist_id || null,
                    album_id: ti.album_id || null,
                });
            }
            startAudioPlayback();
            break;
        case 'error':
            console.error('❌ Streaming error:', data.error_message);
            stopStreamStatusPolling();
            hideLoadingAnimation();
            showToast(`Streaming error: ${data.error_message || 'Unknown error'}`, 'error');
            clearTrack();
            break;
        case 'stopped':
            // Do NOT clear track here — explicit stop (handleStop) calls clearTrack() directly.
            // Clearing here collapses the player after audio naturally ends or during queue transitions.
            console.log('🛑 Stream stopped');
            stopStreamStatusPolling();
            hideLoadingAnimation();
            break;
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
        // Only clear track if not in queue playback mode — queue handles its own error recovery
        if (npQueue.length === 0) {
            clearTrack();
        }
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

    // Sync expanded player modal
    if (npModalOpen) updateNpProgress();
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

    // Repeat-one is handled by audioPlayer.loop (set in handleNpRepeat)
    // Auto-advance to next track if queue has a next item (guard against race conditions)
    if (npQueue.length > 0 && !npLoadingQueueItem) {
        const hasNext = npShuffleOn
            ? npQueue.length > 1
            : (npQueueIndex < npQueue.length - 1 || npRepeatMode === 'all');
        if (hasNext) { playNextInQueue(); return; }
    }

    // Radio mode: auto-fetch similar tracks when queue is exhausted
    if (npRadioMode && currentTrack && currentTrack.id && !npLoadingQueueItem) {
        npFetchRadioTracks();
    }
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
            // Only clear track if not in queue playback — queue handles its own recovery
            if (npQueue.length === 0) {
                clearTrack();
            }
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

function formatCountdownTime(seconds) {
    // Format seconds as countdown timer (e.g., "24m 13s", "2h 15m", "23h 59m")
    if (seconds === null || seconds === undefined || seconds < 0) return '';
    if (seconds === 0) return '0s';  // Show "0s" instead of hiding timer

    const hours = Math.floor(seconds / 3600);
    const minutes = Math.floor((seconds % 3600) / 60);
    const secs = Math.floor(seconds % 60);

    if (hours > 0) {
        return `${hours}h ${minutes}m`;
    } else if (minutes > 0) {
        return `${minutes}m ${secs}s`;
    } else {
        return `${secs}s`;
    }
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
// EXPANDED NOW PLAYING MODAL
// ===============================

let npModalOpen = false;
let npRepeatMode = 'off';     // 'off' | 'all' | 'one'
let npShuffleOn = false;
let npQueue = [];
let npQueueIndex = -1;
let npMuted = false;
let npPreMuteVolume = 70;
let npMediaSessionThrottle = 0;
let npLoadingQueueItem = false;
let npRadioMode = false;
let npRecentlyPlayedIds = [];
let npAudioContext = null;
let npAnalyser = null;
let npMediaSource = null;
let npVizAnimFrame = null;
let npVizInitialized = false;

function initExpandedPlayer() {
    const closeBtn = document.getElementById('np-close-btn');
    const overlay = document.getElementById('np-modal-overlay');
    const playBtn = document.getElementById('np-play-btn');
    const stopBtn = document.getElementById('np-stop-btn');
    const shuffleBtn = document.getElementById('np-shuffle-btn');
    const repeatBtn = document.getElementById('np-repeat-btn');
    const muteBtn = document.getElementById('np-mute-btn');
    const npProgressBar = document.getElementById('np-progress-bar');
    const npVolumeSlider = document.getElementById('np-volume-slider');

    if (!overlay) return;

    // Close handlers
    closeBtn.addEventListener('click', closeNowPlayingModal);
    overlay.addEventListener('click', (e) => { if (e.target === overlay) closeNowPlayingModal(); });

    // Control handlers
    playBtn.addEventListener('click', () => { togglePlayback(); });
    stopBtn.addEventListener('click', async () => { await handleStop(); closeNowPlayingModal(); });
    shuffleBtn.addEventListener('click', handleNpShuffle);
    repeatBtn.addEventListener('click', handleNpRepeat);
    muteBtn.addEventListener('click', handleNpMuteToggle);

    // Progress bar (mouse)
    npProgressBar.addEventListener('input', handleNpProgressBarChange);
    npProgressBar.addEventListener('mousedown', () => { npProgressBar.dataset.seeking = 'true'; });
    npProgressBar.addEventListener('mouseup', () => { delete npProgressBar.dataset.seeking; });

    // Progress bar (touch)
    npProgressBar.addEventListener('touchstart', () => { npProgressBar.dataset.seeking = 'true'; }, { passive: true });
    npProgressBar.addEventListener('touchmove', (e) => {
        const touch = e.touches[0];
        const rect = npProgressBar.getBoundingClientRect();
        const pct = Math.max(0, Math.min(100, ((touch.clientX - rect.left) / rect.width) * 100));
        npProgressBar.value = pct;
        npProgressBar.dispatchEvent(new Event('input'));
    }, { passive: true });
    npProgressBar.addEventListener('touchend', () => { delete npProgressBar.dataset.seeking; }, { passive: true });

    // Volume slider
    npVolumeSlider.addEventListener('input', handleNpVolumeChange);

    // Keyboard shortcuts (global)
    document.addEventListener('keydown', handlePlayerKeyboardShortcuts);

    // Make sidebar media player clickable to open modal
    const mediaPlayer = document.getElementById('media-player');
    if (mediaPlayer) {
        mediaPlayer.style.cursor = 'pointer';
        mediaPlayer.addEventListener('click', (e) => {
            // Don't open modal when clicking controls (let expand-hint through)
            if (e.target.closest('.play-button, .stop-button, .volume-slider, .volume-control, .progress-bar, .volume-icon, .mini-nav-btn') && !e.target.closest('.expand-hint')) return;
            if (currentTrack) openNowPlayingModal();
        });
    }

    // Prev / Next buttons
    const prevBtn = document.getElementById('np-prev-btn');
    const nextBtn = document.getElementById('np-next-btn');
    if (prevBtn) prevBtn.addEventListener('click', () => { playPreviousInQueue(); });
    if (nextBtn) nextBtn.addEventListener('click', () => { playNextInQueue(); });

    // Queue panel toggle + clear
    const queueToggle = document.getElementById('np-queue-toggle');
    if (queueToggle) {
        queueToggle.addEventListener('click', () => {
            const body = document.getElementById('np-queue-body');
            if (body) body.classList.toggle('hidden');
            queueToggle.classList.toggle('active');
        });
    }
    const queueClearBtn = document.getElementById('np-queue-clear');
    if (queueClearBtn) queueClearBtn.addEventListener('click', () => { clearQueue(); });

    // Radio mode button
    const radioBtn = document.getElementById('np-radio-btn');
    if (radioBtn) {
        radioBtn.addEventListener('click', () => {
            npRadioMode = !npRadioMode;
            radioBtn.classList.toggle('active', npRadioMode);
            showToast(npRadioMode ? 'Radio mode on — similar tracks will auto-queue' : 'Radio mode off', 'success');
            // Immediately fetch radio tracks if turned on while playing with empty/exhausted queue
            if (npRadioMode && currentTrack && currentTrack.id && !npLoadingQueueItem) {
                const hasNext = npQueue.length > 0 && (npShuffleOn
                    ? npQueue.length > 1
                    : (npQueueIndex < npQueue.length - 1 || npRepeatMode === 'all'));
                if (!hasNext) {
                    // Add current track to queue first so it appears as "now playing" in context
                    if (npQueue.length === 0 && currentTrack.is_library) {
                        npQueue.push({
                            title: currentTrack.title,
                            artist: currentTrack.artist,
                            album: currentTrack.album,
                            file_path: currentTrack.filename || currentTrack.file_path,
                            filename: currentTrack.filename || currentTrack.file_path,
                            is_library: true,
                            image_url: currentTrack.image_url,
                            id: currentTrack.id,
                            artist_id: currentTrack.artist_id,
                            album_id: currentTrack.album_id,
                            bitrate: currentTrack.bitrate
                        });
                        npQueueIndex = 0;
                        renderNpQueue();
                        updateNpPrevNextButtons();
                    }
                    npFetchRadioTracks();
                }
            }
        });
    }

    // Action button (Go to Artist)
    const gotoArtistBtn = document.getElementById('np-goto-artist');
    if (gotoArtistBtn) {
        gotoArtistBtn.addEventListener('click', () => {
            if (currentTrack && currentTrack.artist_id) {
                closeNowPlayingModal();
                navigateToArtistDetail(currentTrack.artist_id, currentTrack.artist || '');
            }
        });
    }
    // Buffering state listeners on audioPlayer
    if (audioPlayer) {
        audioPlayer.addEventListener('waiting', () => {
            const ring = document.getElementById('np-buffering-ring');
            if (ring) ring.classList.remove('hidden');
        });
        audioPlayer.addEventListener('canplay', () => {
            const ring = document.getElementById('np-buffering-ring');
            if (ring) ring.classList.add('hidden');
        });
        audioPlayer.addEventListener('playing', () => {
            const ring = document.getElementById('np-buffering-ring');
            if (ring) ring.classList.add('hidden');
        });
    }

    // Init Media Session API
    initMediaSession();
}

function openNowPlayingModal() {
    const overlay = document.getElementById('np-modal-overlay');
    if (!overlay) return;
    npModalOpen = true;
    overlay.classList.remove('hidden');
    document.body.style.overflow = 'hidden';
    syncExpandedPlayerUI();
    // Start visualizer if already playing
    if (isPlaying) { npInitVisualizer(); npStartVisualizerLoop(); }
}

function closeNowPlayingModal() {
    const overlay = document.getElementById('np-modal-overlay');
    if (!overlay) return;
    npModalOpen = false;
    overlay.classList.add('hidden');
    document.body.style.overflow = '';
    npStopVisualizerLoop();
}

function syncExpandedPlayerUI() {
    if (!npModalOpen) return;

    // Track info
    updateNpTrackInfo();

    // Play state
    updateNpPlayButton();

    // Progress
    updateNpProgress();

    // Volume
    const sidebarVol = document.getElementById('volume-slider');
    const npVol = document.getElementById('np-volume-slider');
    const npVolFill = document.getElementById('np-volume-fill');
    if (sidebarVol && npVol) {
        npVol.value = sidebarVol.value;
        if (npVolFill) npVolFill.style.width = sidebarVol.value + '%';
    }

    // Visualizer
    const viz = document.getElementById('np-visualizer');
    if (viz) viz.classList.toggle('playing', isPlaying);

    // Queue
    renderNpQueue();
    updateNpPrevNextButtons();
}

function updateNpTrackInfo() {
    const titleEl = document.getElementById('np-track-title');
    const artistEl = document.getElementById('np-artist-name');
    const albumEl = document.getElementById('np-album-name');
    const artImg = document.getElementById('np-album-art');
    const artPlaceholder = document.getElementById('np-album-art-placeholder');
    const badgesEl = document.getElementById('np-format-badges');
    const actionBtns = document.getElementById('np-action-buttons');

    if (!titleEl) return;

    // Sidebar album art
    const sidebarArt = document.getElementById('sidebar-album-art');

    if (currentTrack) {
        // Track text transition animation
        const textEls = [titleEl, artistEl, albumEl];
        const oldTitle = titleEl.textContent;
        const newTitle = currentTrack.title || 'Unknown Track';
        const trackChanged = oldTitle !== newTitle && oldTitle !== 'No track';

        titleEl.textContent = newTitle;
        artistEl.textContent = currentTrack.artist || 'Unknown Artist';
        albumEl.textContent = currentTrack.album || 'Unknown Album';

        if (trackChanged) {
            textEls.forEach(el => {
                el.classList.remove('np-text-transition');
                void el.offsetWidth; // force reflow
                el.classList.add('np-text-transition');
            });
        }

        // Album art (modal + sidebar) + ambient glow extraction
        const artUrl = getNpAlbumArtUrl();
        if (artUrl && artImg) {
            // Only set crossOrigin for external URLs — local paths break with CORS headers
            if (artUrl.startsWith('http')) {
                artImg.crossOrigin = 'anonymous';
            } else {
                artImg.removeAttribute('crossOrigin');
            }
            artImg.src = artUrl;
            artImg.classList.remove('hidden');
            artImg.onerror = () => { artImg.classList.add('hidden'); npResetAmbientGlow(); };
            artImg.onload = () => { npExtractAmbientColor(artImg); };
        } else if (artImg) {
            artImg.classList.add('hidden');
            npResetAmbientGlow();
        }
        if (sidebarArt) {
            if (artUrl) {
                sidebarArt.src = artUrl;
                sidebarArt.style.display = '';
                sidebarArt.onerror = () => { sidebarArt.src = '/static/trans2.png'; };
            } else {
                sidebarArt.src = '/static/trans2.png';
            }
        }

        // Format badges (richer: include bitrate/sample_rate)
        if (badgesEl) {
            badgesEl.innerHTML = '';
            const filename = currentTrack.filename || '';
            if (filename) {
                const ext = getFileExtension(filename);
                if (ext) {
                    let label = ext.toUpperCase();
                    if (currentTrack.sample_rate) {
                        const khz = (currentTrack.sample_rate / 1000);
                        label += ' ' + (khz % 1 === 0 ? khz.toFixed(0) : khz.toFixed(1)) + 'kHz';
                    }
                    const badge = document.createElement('span');
                    badge.className = 'np-format-badge' + (ext === 'flac' ? ' flac' : '');
                    badge.textContent = label;
                    badgesEl.appendChild(badge);
                }
                if (currentTrack.bitrate) {
                    const brBadge = document.createElement('span');
                    brBadge.className = 'np-format-badge';
                    brBadge.textContent = currentTrack.bitrate + 'k';
                    badgesEl.appendChild(brBadge);
                }
            }
        }

        // Action buttons visibility
        if (actionBtns) {
            const hasArtist = currentTrack.artist_id;
            actionBtns.classList.toggle('hidden', !hasArtist);
        }

        // Track recently played for radio mode
        if (currentTrack.id && !npRecentlyPlayedIds.includes(currentTrack.id)) {
            npRecentlyPlayedIds.push(currentTrack.id);
            if (npRecentlyPlayedIds.length > 50) npRecentlyPlayedIds.shift();
        }
    } else {
        titleEl.textContent = 'No track';
        artistEl.textContent = 'Unknown Artist';
        albumEl.textContent = 'Unknown Album';
        if (artImg) artImg.classList.add('hidden');
        if (sidebarArt) sidebarArt.src = '/static/trans2.png';
        if (badgesEl) badgesEl.innerHTML = '';
        if (actionBtns) actionBtns.classList.add('hidden');
        npResetAmbientGlow();
    }
}

function npExtractAmbientColor(imgEl) {
    try {
        const canvas = document.createElement('canvas');
        const ctx = canvas.getContext('2d');
        canvas.width = 50;
        canvas.height = 50;
        ctx.drawImage(imgEl, 0, 0, 50, 50);
        const data = ctx.getImageData(0, 0, 50, 50).data;
        let rSum = 0, gSum = 0, bSum = 0, count = 0;
        for (let i = 0; i < data.length; i += 16) { // sample every 4th pixel
            const r = data[i], g = data[i + 1], b = data[i + 2];
            const brightness = (r + g + b) / 3;
            if (brightness > 20 && brightness < 230) {
                rSum += r; gSum += g; bSum += b; count++;
            }
        }
        if (count > 0) {
            const modal = document.querySelector('.np-modal');
            if (modal) {
                modal.style.setProperty('--np-ambient-r', Math.round(rSum / count));
                modal.style.setProperty('--np-ambient-g', Math.round(gSum / count));
                modal.style.setProperty('--np-ambient-b', Math.round(bSum / count));
            }
        }
    } catch (e) {
        // Cross-origin or canvas error — ignore silently
    }
}

function npResetAmbientGlow() {
    const modal = document.querySelector('.np-modal');
    if (modal) {
        modal.style.setProperty('--np-ambient-r', '29');
        modal.style.setProperty('--np-ambient-g', '185');
        modal.style.setProperty('--np-ambient-b', '84');
    }
}

function updateNpPlayButton() {
    const playIcon = document.querySelector('.np-icon-play');
    const pauseIcon = document.querySelector('.np-icon-pause');
    if (playIcon && pauseIcon) {
        playIcon.classList.toggle('hidden', isPlaying);
        pauseIcon.classList.toggle('hidden', !isPlaying);
    }

    const viz = document.getElementById('np-visualizer');
    if (viz) viz.classList.toggle('playing', isPlaying);

    // Drive Web Audio visualizer (only when modal is open to save CPU)
    if (isPlaying && npModalOpen) {
        npInitVisualizer();
        npStartVisualizerLoop();
    } else {
        npStopVisualizerLoop();
    }
}

function updateNpProgress() {
    if (!npModalOpen || !audioPlayer) return;

    const npProgressBar = document.getElementById('np-progress-bar');
    const npProgressFill = document.getElementById('np-progress-fill');
    const npCurrentTime = document.getElementById('np-current-time');
    const npTotalTime = document.getElementById('np-total-time');

    if (audioPlayer.duration) {
        const progress = (audioPlayer.currentTime / audioPlayer.duration) * 100;
        if (npProgressBar && !npProgressBar.dataset.seeking) {
            npProgressBar.value = progress;
        }
        if (npProgressFill) npProgressFill.style.width = progress + '%';
        if (npCurrentTime) npCurrentTime.textContent = formatTime(audioPlayer.currentTime);
        if (npTotalTime) npTotalTime.textContent = formatTime(audioPlayer.duration);
    } else {
        if (npProgressBar) npProgressBar.value = 0;
        if (npProgressFill) npProgressFill.style.width = '0%';
        if (npCurrentTime) npCurrentTime.textContent = '0:00';
        if (npTotalTime) npTotalTime.textContent = '0:00';
    }
}

function handleNpProgressBarChange(event) {
    if (!audioPlayer || !audioPlayer.duration) return;
    const progress = parseFloat(event.target.value);
    const newTime = (progress / 100) * audioPlayer.duration;

    try {
        audioPlayer.currentTime = newTime;

        // Sync sidebar progress
        const sidebarBar = document.getElementById('progress-bar');
        const sidebarFill = document.getElementById('progress-fill');
        if (sidebarBar) sidebarBar.value = progress;
        if (sidebarFill) sidebarFill.style.width = progress + '%';

        // Sync modal progress fill
        const npFill = document.getElementById('np-progress-fill');
        if (npFill) npFill.style.width = progress + '%';

        // Update time displays
        const sidebarTime = document.getElementById('current-time');
        const npTime = document.getElementById('np-current-time');
        if (sidebarTime) sidebarTime.textContent = formatTime(newTime);
        if (npTime) npTime.textContent = formatTime(newTime);
    } catch (error) {
        console.warn('Seek failed:', error.message);
    }
}

function handleNpVolumeChange(event) {
    const volume = parseInt(event.target.value);
    if (audioPlayer) audioPlayer.volume = volume / 100;

    // Sync sidebar volume slider
    const sidebarVol = document.getElementById('volume-slider');
    if (sidebarVol) {
        sidebarVol.value = volume;
        sidebarVol.style.setProperty('--volume-percent', volume + '%');
    }

    // Update modal volume fill
    const npFill = document.getElementById('np-volume-fill');
    if (npFill) npFill.style.width = volume + '%';

    // Update mute state
    npMuted = volume === 0;
    updateNpMuteIcon();
}

function handleNpMuteToggle() {
    const npVol = document.getElementById('np-volume-slider');
    if (!npVol) return;

    if (npMuted) {
        // Unmute — restore previous volume
        npVol.value = npPreMuteVolume;
        npVol.dispatchEvent(new Event('input'));
        npMuted = false;
    } else {
        // Mute — save current volume, set to 0
        npPreMuteVolume = parseInt(npVol.value) || 70;
        npVol.value = 0;
        npVol.dispatchEvent(new Event('input'));
        npMuted = true;
    }
    updateNpMuteIcon();
}

function updateNpMuteIcon() {
    const muteBtn = document.getElementById('np-mute-btn');
    const volIcon = muteBtn ? muteBtn.querySelector('.np-icon-vol') : null;
    const mutedIcon = muteBtn ? muteBtn.querySelector('.np-icon-muted') : null;
    if (volIcon && mutedIcon) {
        volIcon.classList.toggle('hidden', npMuted);
        mutedIcon.classList.toggle('hidden', !npMuted);
    }
    if (muteBtn) muteBtn.classList.toggle('muted', npMuted);
}

function handleNpShuffle() {
    npShuffleOn = !npShuffleOn;
    const btn = document.getElementById('np-shuffle-btn');
    if (btn) btn.classList.toggle('active', npShuffleOn);
    updateNpPrevNextButtons();
}

function handleNpRepeat() {
    const badge = document.getElementById('np-repeat-one-badge');
    if (npRepeatMode === 'off') {
        npRepeatMode = 'all';
        if (audioPlayer) audioPlayer.loop = false;
    } else if (npRepeatMode === 'all') {
        npRepeatMode = 'one';
        if (audioPlayer) audioPlayer.loop = true;
    } else {
        npRepeatMode = 'off';
        if (audioPlayer) audioPlayer.loop = false;
    }
    const btn = document.getElementById('np-repeat-btn');
    if (btn) btn.classList.toggle('active', npRepeatMode !== 'off');
    if (badge) badge.classList.toggle('hidden', npRepeatMode !== 'one');
    updateNpPrevNextButtons();
}

// ===============================
// QUEUE MANAGEMENT
// ===============================

function addToQueue(track) {
    npQueue.push(track);
    showToast('Added to queue', 'success');
    renderNpQueue();
    updateNpPrevNextButtons();
    // If nothing is currently playing, auto-play the first queued track
    if (!currentTrack) {
        playQueueItem(npQueue.length - 1);
    }
}

function removeFromQueue(index) {
    if (index < 0 || index >= npQueue.length) return;
    const wasCurrentTrack = (index === npQueueIndex);
    npQueue.splice(index, 1);
    // Adjust current index
    if (npQueue.length === 0) {
        npQueueIndex = -1;
        // Current track keeps playing but queue is now empty — that's OK
    } else if (index < npQueueIndex) {
        npQueueIndex--;
    } else if (wasCurrentTrack) {
        // Removed the currently playing item
        if (npQueueIndex >= npQueue.length) {
            npQueueIndex = npQueue.length - 1;
        }
        // Play the next track at the adjusted index
        playQueueItem(npQueueIndex);
    }
    renderNpQueue();
    updateNpPrevNextButtons();
}

function clearQueue() {
    npQueue = [];
    npQueueIndex = -1;
    renderNpQueue();
    updateNpPrevNextButtons();
}

function playNextInQueue() {
    if (npQueue.length === 0) return;
    if (npShuffleOn) {
        // Pick a random index that is not the current one
        const candidates = [];
        for (let i = 0; i < npQueue.length; i++) {
            if (i !== npQueueIndex) candidates.push(i);
        }
        if (candidates.length === 0) return;
        const next = candidates[Math.floor(Math.random() * candidates.length)];
        playQueueItem(next);
    } else {
        const next = npQueueIndex + 1;
        if (next >= npQueue.length) {
            // End of queue — repeat-all wraps to start
            if (npRepeatMode === 'all') {
                playQueueItem(0);
            }
            return;
        }
        playQueueItem(next);
    }
}

function playPreviousInQueue() {
    // If more than 3 seconds in, restart current track
    if (audioPlayer && audioPlayer.currentTime > 3) {
        audioPlayer.currentTime = 0;
        if (audioPlayer.paused) audioPlayer.play();
        return;
    }
    if (npQueue.length === 0) return;
    const prev = npQueueIndex - 1;
    if (prev < 0) {
        // At start — restart current track
        if (audioPlayer) {
            audioPlayer.currentTime = 0;
            if (audioPlayer.paused) audioPlayer.play();
        }
        return;
    }
    playQueueItem(prev);
}

async function playQueueItem(index) {
    if (index < 0 || index >= npQueue.length) return;
    if (npLoadingQueueItem) return; // Prevent race condition from double-advance
    npLoadingQueueItem = true;
    npQueueIndex = index;
    const track = npQueue[index];

    try {
        if (track.is_library) {
            // Library track playback flow
            await stopStream();
            setTrackInfo({
                title: track.title,
                artist: track.artist,
                album: track.album,
                filename: track.file_path,
                is_library: true,
                image_url: track.image_url,
                id: track.id,
                artist_id: track.artist_id,
                album_id: track.album_id,
                bitrate: track.bitrate,
                sample_rate: track.sample_rate
            });
            showLoadingAnimation();

            const response = await fetch('/api/library/play', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    file_path: track.file_path,
                    title: track.title || '',
                    artist: track.artist || '',
                    album: track.album || ''
                })
            });
            const result = await response.json();
            if (!result.success) throw new Error(result.error || 'Failed to start playback');
            // Re-apply repeat-one loop property
            if (audioPlayer) audioPlayer.loop = (npRepeatMode === 'one');
            await startAudioPlayback();
        } else {
            // Non-library (stream) tracks cannot be queued for auto-advance
            // Just show track info — the stream flow handles its own playback
            setTrackInfo({
                title: track.title,
                artist: track.artist,
                album: track.album,
                filename: track.filename || track.file_path,
                is_library: false,
                image_url: track.image_url,
                id: track.id,
                artist_id: track.artist_id,
                album_id: track.album_id,
                bitrate: track.bitrate,
                sample_rate: track.sample_rate
            });
        }
    } catch (error) {
        console.error('Queue playback error:', error);
        showToast(`Skipping track: ${error.message}`, 'error');
        hideLoadingAnimation();
        // Auto-skip to next track on failure instead of stopping the queue
        npLoadingQueueItem = false;
        const nextIdx = npQueueIndex + 1;
        if (nextIdx < npQueue.length) {
            setTimeout(() => playQueueItem(nextIdx), 500);
        }
        return;
    } finally {
        npLoadingQueueItem = false;
    }

    renderNpQueue();
    updateNpPrevNextButtons();
}

function renderNpQueue() {
    const listEl = document.getElementById('np-queue-list');
    const emptyEl = document.getElementById('np-queue-empty');
    const countEl = document.getElementById('np-queue-count');
    if (!listEl) return;

    if (countEl) countEl.textContent = npQueue.length > 0 ? `(${npQueue.length})` : '';

    if (npQueue.length === 0) {
        listEl.innerHTML = '';
        if (emptyEl) emptyEl.classList.remove('hidden');
        return;
    }

    if (emptyEl) emptyEl.classList.add('hidden');
    listEl.innerHTML = '';

    npQueue.forEach((track, i) => {
        const item = document.createElement('div');
        item.className = 'np-queue-item' + (i === npQueueIndex ? ' active' : '');
        item.onclick = () => playQueueItem(i);

        const info = document.createElement('div');
        info.className = 'np-queue-item-info';

        const title = document.createElement('div');
        title.className = 'np-queue-item-title';
        title.textContent = track.title || 'Unknown Track';

        const artist = document.createElement('div');
        artist.className = 'np-queue-item-artist';
        artist.textContent = track.artist || 'Unknown Artist';

        info.appendChild(title);
        info.appendChild(artist);
        item.appendChild(info);

        const removeBtn = document.createElement('button');
        removeBtn.className = 'np-queue-item-remove';
        removeBtn.innerHTML = '&#10005;';
        removeBtn.title = 'Remove from queue';
        removeBtn.onclick = (e) => {
            e.stopPropagation();
            removeFromQueue(i);
        };
        item.appendChild(removeBtn);

        listEl.appendChild(item);
    });
}

function updateNpPrevNextButtons() {
    const canPrev = npQueueIndex > 0 || (audioPlayer && audioPlayer.currentTime > 3);
    const canNext = npQueue.length > 0 && (npShuffleOn ? npQueue.length > 1 : (npQueueIndex < npQueue.length - 1 || npRepeatMode === 'all'));

    // Full Now Playing modal buttons
    const prevBtn = document.getElementById('np-prev-btn');
    const nextBtn = document.getElementById('np-next-btn');
    if (prevBtn) prevBtn.disabled = !canPrev;
    if (nextBtn) nextBtn.disabled = !canNext;

    // Mini player buttons
    const miniPrevBtn = document.getElementById('mini-prev-btn');
    const miniNextBtn = document.getElementById('mini-next-btn');
    if (miniPrevBtn) miniPrevBtn.disabled = !canPrev;
    if (miniNextBtn) miniNextBtn.disabled = !canNext;
}

function handlePlayerKeyboardShortcuts(event) {
    // Don't intercept when typing in inputs or when non-player modals are open
    const tag = document.activeElement.tagName.toLowerCase();
    if (tag === 'input' || tag === 'textarea' || tag === 'select' || document.activeElement.isContentEditable) return;

    // Only handle when player modal is open OR when no other modal is visible
    const otherModals = document.querySelectorAll('.modal-overlay:not(.hidden):not(#np-modal-overlay)');
    if (otherModals.length > 0 && !npModalOpen) return;

    switch (event.key) {
        case ' ':
            if (!currentTrack) return;
            event.preventDefault();
            togglePlayback();
            break;
        case 'ArrowLeft':
            if (!audioPlayer || !audioPlayer.duration) return;
            event.preventDefault();
            audioPlayer.currentTime = Math.max(0, audioPlayer.currentTime - 5);
            break;
        case 'ArrowRight':
            if (!audioPlayer || !audioPlayer.duration) return;
            event.preventDefault();
            audioPlayer.currentTime = Math.min(audioPlayer.duration, audioPlayer.currentTime + 5);
            break;
        case 'ArrowUp':
            event.preventDefault();
            if (audioPlayer) {
                const newVol = Math.min(1, audioPlayer.volume + 0.05);
                audioPlayer.volume = newVol;
                syncVolumeUI(Math.round(newVol * 100));
            }
            break;
        case 'ArrowDown':
            event.preventDefault();
            if (audioPlayer) {
                const newVol = Math.max(0, audioPlayer.volume - 0.05);
                audioPlayer.volume = newVol;
                syncVolumeUI(Math.round(newVol * 100));
            }
            break;
        case 'm':
        case 'M':
            if (npModalOpen) handleNpMuteToggle();
            break;
        case 'Escape':
            if (npModalOpen) closeNowPlayingModal();
            break;
        default:
            return; // Don't prevent default for unhandled keys
    }
}

function syncVolumeUI(volumePercent) {
    // Sync both sidebar and modal volume UIs
    const sidebarVol = document.getElementById('volume-slider');
    const npVol = document.getElementById('np-volume-slider');
    const npFill = document.getElementById('np-volume-fill');

    if (sidebarVol) {
        sidebarVol.value = volumePercent;
        sidebarVol.style.setProperty('--volume-percent', volumePercent + '%');
    }
    if (npVol) npVol.value = volumePercent;
    if (npFill) npFill.style.width = volumePercent + '%';
}

function getNpAlbumArtUrl() {
    if (!currentTrack) return null;
    return currentTrack.image_url || currentTrack.album_cover_url || currentTrack.thumb_url || null;
}

// ===============================
// WEB AUDIO VISUALIZER
// ===============================

function npInitVisualizer() {
    if (npVizInitialized || !audioPlayer) return;
    try {
        if (!npAudioContext) {
            npAudioContext = new (window.AudioContext || window.webkitAudioContext)();
        }
        if (!npMediaSource) {
            npMediaSource = npAudioContext.createMediaElementSource(audioPlayer);
            npAnalyser = npAudioContext.createAnalyser();
            npAnalyser.fftSize = 64;
            npAnalyser.smoothingTimeConstant = 0.8;
            npMediaSource.connect(npAnalyser);
            npAnalyser.connect(npAudioContext.destination);
        }
        npVizInitialized = true;
    } catch (e) {
        console.warn('Web Audio visualizer init failed, using CSS fallback:', e.message);
        // Mark as CSS fallback
        const viz = document.getElementById('np-visualizer');
        if (viz) viz.classList.add('np-viz-css-fallback');
        npVizInitialized = true; // don't retry
    }
}

function npStartVisualizerLoop() {
    if (npVizAnimFrame) return; // Already running
    if (!npAnalyser) return; // No analyser — CSS fallback handles it

    if (npAudioContext && npAudioContext.state === 'suspended') {
        npAudioContext.resume();
    }

    const bars = document.querySelectorAll('.np-viz-bar');
    if (bars.length === 0) return;
    const bufferLength = npAnalyser.frequencyBinCount;
    const dataArray = new Uint8Array(bufferLength);

    function draw() {
        npVizAnimFrame = requestAnimationFrame(draw);
        npAnalyser.getByteFrequencyData(dataArray);

        // Map 7 bars to frequency bins (skip bin 0 which is DC offset)
        const binCount = Math.min(bufferLength - 1, 7);
        for (let i = 0; i < bars.length; i++) {
            const binIndex = Math.min(i + 1, bufferLength - 1);
            const value = dataArray[binIndex] / 255; // 0..1
            const scale = Math.max(0.08, value); // minimum visible height
            bars[i].style.transform = `scaleY(${scale})`;
        }
    }
    draw();
}

function npStopVisualizerLoop() {
    if (npVizAnimFrame) {
        cancelAnimationFrame(npVizAnimFrame);
        npVizAnimFrame = null;
    }
    // Reset bars to min
    const bars = document.querySelectorAll('.np-viz-bar');
    bars.forEach(bar => { bar.style.transform = 'scaleY(0.125)'; });
}

// ===============================
// SIDEBAR AUDIO VISUALIZER
// ===============================

let sidebarVizAnimFrame = null;
let sidebarVisualizerType = 'bars'; // bars | wave | spectrum | mirror | equalizer | none
const SIDEBAR_VIZ_BAR_COUNT = 32;

let _sidebarVizBuiltType = null;

function buildSidebarVizElements(type) {
    const container = document.getElementById('sidebar-visualizer');
    if (!container) return;
    if (_sidebarVizBuiltType === type && container.children.length > 0) return;
    _sidebarVizBuiltType = type;
    container.innerHTML = '';
    container.className = 'sidebar-visualizer';

    if (type === 'bars') {
        container.classList.add('viz-bars');
        for (let i = 0; i < SIDEBAR_VIZ_BAR_COUNT; i++) {
            const bar = document.createElement('div');
            bar.className = 'sidebar-viz-bar';
            container.appendChild(bar);
        }
    } else if (type === 'wave' || type === 'spectrum') {
        container.classList.add('viz-canvas');
        const canvas = document.createElement('canvas');
        canvas.className = 'sidebar-viz-canvas';
        canvas.width = 10;
        canvas.height = 600;
        container.appendChild(canvas);
    } else if (type === 'mirror') {
        container.classList.add('viz-mirror');
        for (let i = 0; i < SIDEBAR_VIZ_BAR_COUNT; i++) {
            const bar = document.createElement('div');
            bar.className = 'sidebar-viz-mirror-bar';
            container.appendChild(bar);
        }
    } else if (type === 'equalizer') {
        container.classList.add('viz-equalizer');
        for (let i = 0; i < SIDEBAR_VIZ_BAR_COUNT; i++) {
            const wrap = document.createElement('div');
            wrap.className = 'sidebar-viz-eq-wrap';
            const bar = document.createElement('div');
            bar.className = 'sidebar-viz-eq-bar';
            const peak = document.createElement('div');
            peak.className = 'sidebar-viz-eq-peak';
            wrap.appendChild(bar);
            wrap.appendChild(peak);
            container.appendChild(wrap);
        }
    }
}

function startSidebarVisualizer() {
    const type = sidebarVisualizerType;
    if (type === 'none') return;

    const container = document.getElementById('sidebar-visualizer');
    if (!container) return;

    buildSidebarVizElements(type);
    container.classList.add('active');

    if (sidebarVizAnimFrame) return;
    if (!npAnalyser) return;

    const bufferLength = npAnalyser.frequencyBinCount;
    const dataArray = new Uint8Array(bufferLength);
    const hueStart = 200, hueRange = 160;

    // Helper: average frequency bins for a given segment index
    function getBinValue(i, count) {
        const binsPerSeg = Math.max(1, Math.floor((bufferLength - 1) / count));
        let sum = 0;
        const start = i * binsPerSeg + 1;
        for (let b = 0; b < binsPerSeg; b++) sum += dataArray[Math.min(start + b, bufferLength - 1)];
        return (sum / binsPerSeg) / 255;
    }

    // ── Bars ──
    if (type === 'bars') {
        const bars = container.querySelectorAll('.sidebar-viz-bar');
        if (bars.length === 0) return;
        function drawBars() {
            sidebarVizAnimFrame = requestAnimationFrame(drawBars);
            npAnalyser.getByteFrequencyData(dataArray);
            for (let i = 0; i < bars.length; i++) {
                const value = getBinValue(i, bars.length);
                const scale = Math.max(0.08, value);
                const hue = (hueStart + (i / bars.length) * hueRange + value * 30) % 360;
                bars[i].style.transform = `scaleX(${scale})`;
                bars[i].style.backgroundColor = `hsla(${hue}, 80%, ${50 + value * 15}%, ${0.5 + value * 0.5})`;
            }
        }
        drawBars();

        // ── Wave ──
    } else if (type === 'wave') {
        const canvas = container.querySelector('.sidebar-viz-canvas');
        if (!canvas) return;
        const ctx = canvas.getContext('2d');
        let hueOffset = 0;
        function drawWave() {
            sidebarVizAnimFrame = requestAnimationFrame(drawWave);
            const ch = container.clientHeight;
            if (ch > 0 && canvas.height !== ch) canvas.height = ch;
            npAnalyser.getByteFrequencyData(dataArray);
            const w = canvas.width, h = canvas.height;
            if (h === 0) return;
            ctx.clearRect(0, 0, w, h);

            let totalEnergy = 0;
            for (let i = 1; i < bufferLength; i++) totalEnergy += dataArray[i];
            const avgEnergy = totalEnergy / (bufferLength - 1) / 255;
            hueOffset = (hueOffset + 0.5) % 360;

            const segments = 64;
            ctx.lineWidth = 3;
            ctx.lineCap = 'round';
            ctx.beginPath();
            for (let i = 0; i <= segments; i++) {
                const y = (i / segments) * h;
                const binIdx = Math.min(Math.floor((i / segments) * (bufferLength - 1)) + 1, bufferLength - 1);
                const value = dataArray[binIdx] / 255;
                const x = (w / 2) + Math.sin((i / segments) * Math.PI * 4 + Date.now() * 0.003) * value * (w - 2) * 0.4;
                if (i === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y);
            }
            const grad = ctx.createLinearGradient(0, 0, 0, h);
            grad.addColorStop(0, `hsla(${hueOffset + 200}, 80%, 60%, ${0.3 + avgEnergy * 0.7})`);
            grad.addColorStop(0.5, `hsla(${hueOffset + 280}, 80%, 55%, ${0.3 + avgEnergy * 0.7})`);
            grad.addColorStop(1, `hsla(${hueOffset + 360}, 80%, 60%, ${0.3 + avgEnergy * 0.7})`);
            ctx.strokeStyle = grad;
            ctx.stroke();
            ctx.lineWidth = 6;
            ctx.globalAlpha = 0.15 + avgEnergy * 0.2;
            ctx.stroke();
            ctx.globalAlpha = 1;
        }
        drawWave();

        // ── Spectrum (mountain/terrain fill) ──
    } else if (type === 'spectrum') {
        const canvas = container.querySelector('.sidebar-viz-canvas');
        if (!canvas) return;
        const ctx = canvas.getContext('2d');
        let hueOffset = 0;
        // Smoothed values for fluid motion
        const smoothed = new Float32Array(64);

        function drawSpectrum() {
            sidebarVizAnimFrame = requestAnimationFrame(drawSpectrum);
            const ch = container.clientHeight;
            if (ch > 0 && canvas.height !== ch) canvas.height = ch;
            npAnalyser.getByteFrequencyData(dataArray);
            const w = canvas.width, h = canvas.height;
            if (h === 0) return;
            ctx.clearRect(0, 0, w, h);

            hueOffset = (hueOffset + 0.3) % 360;
            const segments = smoothed.length;

            // Smooth the frequency data
            for (let i = 0; i < segments; i++) {
                const binIdx = Math.min(Math.floor((i / segments) * (bufferLength - 1)) + 1, bufferLength - 1);
                const target = dataArray[binIdx] / 255;
                smoothed[i] += (target - smoothed[i]) * 0.25;
            }

            // Draw filled mountain shape from left edge
            ctx.beginPath();
            ctx.moveTo(0, 0);
            for (let i = 0; i <= segments; i++) {
                const y = (i / segments) * h;
                const value = i < segments ? smoothed[i] : smoothed[segments - 1];
                const x = value * w * 0.95;
                ctx.lineTo(x, y);
            }
            ctx.lineTo(0, h);
            ctx.closePath();

            // Gradient fill
            const fillGrad = ctx.createLinearGradient(0, 0, 0, h);
            fillGrad.addColorStop(0, `hsla(${hueOffset + 200}, 85%, 55%, 0.7)`);
            fillGrad.addColorStop(0.25, `hsla(${hueOffset + 240}, 80%, 50%, 0.6)`);
            fillGrad.addColorStop(0.5, `hsla(${hueOffset + 290}, 85%, 50%, 0.65)`);
            fillGrad.addColorStop(0.75, `hsla(${hueOffset + 330}, 80%, 50%, 0.6)`);
            fillGrad.addColorStop(1, `hsla(${hueOffset + 360}, 85%, 55%, 0.7)`);
            ctx.fillStyle = fillGrad;
            ctx.fill();

            // Bright edge line
            ctx.beginPath();
            ctx.moveTo(0, 0);
            for (let i = 0; i <= segments; i++) {
                const y = (i / segments) * h;
                const value = i < segments ? smoothed[i] : smoothed[segments - 1];
                ctx.lineTo(value * w * 0.95, y);
            }
            const lineGrad = ctx.createLinearGradient(0, 0, 0, h);
            lineGrad.addColorStop(0, `hsla(${hueOffset + 200}, 90%, 70%, 0.9)`);
            lineGrad.addColorStop(0.5, `hsla(${hueOffset + 290}, 90%, 65%, 0.9)`);
            lineGrad.addColorStop(1, `hsla(${hueOffset + 360}, 90%, 70%, 0.9)`);
            ctx.strokeStyle = lineGrad;
            ctx.lineWidth = 1.5;
            ctx.stroke();

            // Outer glow
            ctx.lineWidth = 4;
            ctx.globalAlpha = 0.2;
            ctx.stroke();
            ctx.globalAlpha = 1;
        }
        drawSpectrum();

        // ── Mirror (bars from center outward) ──
    } else if (type === 'mirror') {
        const bars = container.querySelectorAll('.sidebar-viz-mirror-bar');
        if (bars.length === 0) return;
        function drawMirror() {
            sidebarVizAnimFrame = requestAnimationFrame(drawMirror);
            npAnalyser.getByteFrequencyData(dataArray);
            const half = Math.floor(bars.length / 2);
            for (let i = 0; i < half; i++) {
                const value = getBinValue(i, half);
                const scale = Math.max(0.06, value);
                const hue = (hueStart + (i / half) * hueRange + value * 30) % 360;
                const color = `hsla(${hue}, 80%, ${50 + value * 15}%, ${0.5 + value * 0.5})`;
                // Top half — mirror index from center
                const topIdx = half - 1 - i;
                const bottomIdx = half + i;
                bars[topIdx].style.transform = `scaleX(${scale})`;
                bars[topIdx].style.backgroundColor = color;
                if (bottomIdx < bars.length) {
                    bars[bottomIdx].style.transform = `scaleX(${scale})`;
                    bars[bottomIdx].style.backgroundColor = color;
                }
            }
        }
        drawMirror();

        // ── Equalizer (bars with falling peak indicators) ──
    } else if (type === 'equalizer') {
        const wraps = container.querySelectorAll('.sidebar-viz-eq-wrap');
        if (wraps.length === 0) return;
        const peaks = new Float32Array(wraps.length);
        const peakVelocity = new Float32Array(wraps.length);

        function drawEqualizer() {
            sidebarVizAnimFrame = requestAnimationFrame(drawEqualizer);
            npAnalyser.getByteFrequencyData(dataArray);
            for (let i = 0; i < wraps.length; i++) {
                const value = getBinValue(i, wraps.length);
                const scale = Math.max(0.06, value);
                const hue = (hueStart + (i / wraps.length) * hueRange + value * 30) % 360;
                const barEl = wraps[i].querySelector('.sidebar-viz-eq-bar');
                const peakEl = wraps[i].querySelector('.sidebar-viz-eq-peak');

                barEl.style.transform = `scaleX(${scale})`;
                barEl.style.backgroundColor = `hsla(${hue}, 80%, ${50 + value * 15}%, ${0.5 + value * 0.5})`;

                // Peak hold with gravity
                if (value > peaks[i]) {
                    peaks[i] = value;
                    peakVelocity[i] = 0;
                } else {
                    peakVelocity[i] += 0.002; // gravity
                    peaks[i] = Math.max(0, peaks[i] - peakVelocity[i]);
                }
                const peakPos = Math.max(0.06, peaks[i]);
                peakEl.style.left = `${peakPos * 100}%`;
                peakEl.style.backgroundColor = `hsla(${hue}, 90%, 75%, ${0.6 + peaks[i] * 0.4})`;
                peakEl.style.boxShadow = `0 0 4px hsla(${hue}, 90%, 70%, ${peaks[i] * 0.5})`;
            }
        }
        drawEqualizer();
    }
}

function stopSidebarVisualizer() {
    if (sidebarVizAnimFrame) {
        cancelAnimationFrame(sidebarVizAnimFrame);
        sidebarVizAnimFrame = null;
    }
    const container = document.getElementById('sidebar-visualizer');
    if (container) {
        container.classList.remove('active');
    }
}

// Listen for visualizer type changes in settings — use isPlaying (not wasRunning)
// so switching from 'none' to a real type while music plays starts the visualizer
document.addEventListener('change', (e) => {
    if (e.target.id === 'sidebar-visualizer-type') {
        const newType = e.target.value;
        stopSidebarVisualizer();
        _sidebarVizBuiltType = null; // force rebuild for new type
        sidebarVisualizerType = newType;
        if (isPlaying && newType !== 'none') {
            npInitVisualizer();
            startSidebarVisualizer();
        }
    }
});

// ===============================
// RADIO MODE
// ===============================

async function npFetchRadioTracks() {
    if (!currentTrack || !currentTrack.id) return;
    try {
        npLoadingQueueItem = true;
        const excludeIds = npRecentlyPlayedIds.join(',');
        const resp = await fetch(`/api/library/radio?track_id=${currentTrack.id}&limit=50&exclude=${encodeURIComponent(excludeIds)}`);
        if (!resp.ok) {
            console.warn('Radio endpoint returned', resp.status);
            npLoadingQueueItem = false;
            return;
        }
        const data = await resp.json();
        // Bail if radio was toggled off during the fetch
        if (!npRadioMode) { npLoadingQueueItem = false; return; }
        if (data.tracks && data.tracks.length > 0) {
            data.tracks.forEach(t => {
                npQueue.push({
                    title: t.title || 'Unknown Track',
                    artist: t.artist || 'Unknown Artist',
                    album: t.album || 'Unknown Album',
                    file_path: t.file_path,
                    filename: t.file_path,
                    is_library: true,
                    image_url: t.image_url || null,
                    id: t.id,
                    artist_id: t.artist_id,
                    album_id: t.album_id,
                    bitrate: t.bitrate,
                    sample_rate: t.sample_rate
                });
            });
            showToast(`Radio: Added ${data.tracks.length} similar tracks`, 'success');
            renderNpQueue();
            updateNpPrevNextButtons();
            npLoadingQueueItem = false;
            // Only auto-advance if nothing is currently playing (triggered by onAudioEnded)
            if (!isPlaying) {
                playNextInQueue();
            }
        } else {
            showToast('Radio: No similar tracks found', 'info');
            npLoadingQueueItem = false;
        }
    } catch (e) {
        console.warn('Radio fetch error:', e);
        npLoadingQueueItem = false;
    }
}

// Media Session API
function initMediaSession() {
    if (!('mediaSession' in navigator)) return;

    navigator.mediaSession.setActionHandler('play', () => {
        if (audioPlayer && currentTrack) {
            audioPlayer.play().then(() => setPlayingState(true));
        }
    });
    navigator.mediaSession.setActionHandler('pause', () => {
        if (audioPlayer) {
            audioPlayer.pause();
            setPlayingState(false);
        }
    });
    navigator.mediaSession.setActionHandler('stop', () => {
        handleStop();
    });
    navigator.mediaSession.setActionHandler('seekbackward', () => {
        if (audioPlayer && audioPlayer.duration) {
            audioPlayer.currentTime = Math.max(0, audioPlayer.currentTime - 10);
        }
    });
    navigator.mediaSession.setActionHandler('seekforward', () => {
        if (audioPlayer && audioPlayer.duration) {
            audioPlayer.currentTime = Math.min(audioPlayer.duration, audioPlayer.currentTime + 10);
        }
    });
    navigator.mediaSession.setActionHandler('previoustrack', () => {
        if (npQueue.length > 0) playPreviousInQueue();
    });
    navigator.mediaSession.setActionHandler('nexttrack', () => {
        if (npQueue.length > 0) playNextInQueue();
    });
}

function updateMediaSessionMetadata() {
    if (!('mediaSession' in navigator) || !currentTrack) return;
    const artwork = [];
    const artUrl = getNpAlbumArtUrl();
    if (artUrl) artwork.push({ src: artUrl, sizes: '512x512', type: 'image/jpeg' });

    navigator.mediaSession.metadata = new MediaMetadata({
        title: currentTrack.title || 'Unknown Track',
        artist: currentTrack.artist || 'Unknown Artist',
        album: currentTrack.album || 'Unknown Album',
        artwork: artwork
    });
}

function updateMediaSessionPlaybackState() {
    if (!('mediaSession' in navigator)) return;
    if (!currentTrack) {
        navigator.mediaSession.playbackState = 'none';
    } else {
        navigator.mediaSession.playbackState = isPlaying ? 'playing' : 'paused';
    }
}

// ===============================

