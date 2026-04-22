// MUSICBRAINZ ENRICHMENT UI - PHASE 5 WEB UI
// ============================================================================

/**
 * Poll MusicBrainz status every 2 seconds and update UI
 */
async function updateMusicBrainzStatus() {
    if (socketConnected) return; // WebSocket handles this
    if (document.hidden) return; // Skip polling when tab is not visible
    try {
        const response = await fetch('/api/musicbrainz/status');
        if (!response.ok) { console.warn('MusicBrainz status endpoint unavailable'); return; }
        const data = await response.json();
        updateMusicBrainzStatusFromData(data);
    } catch (error) {
        console.error('Error updating MusicBrainz status:', error);
    }
}

function updateMusicBrainzStatusFromData(data) {
    const button = document.getElementById('musicbrainz-button');
    if (!button) return;

    // Update button state classes
    button.classList.remove('active', 'paused', 'complete');
    if (data.idle) {
        button.classList.add('complete');
    } else if (data.running && !data.paused) {
        button.classList.add('active');
    } else if (data.paused) {
        button.classList.add('paused');
    }

    // Update tooltip content
    const tooltipStatus = document.getElementById('mb-tooltip-status');
    const tooltipCurrent = document.getElementById('mb-tooltip-current');
    const tooltipProgress = document.getElementById('mb-tooltip-progress');

    if (tooltipStatus) {
        if (data.idle) {
            tooltipStatus.textContent = 'Complete';
        } else if (data.running && !data.paused) {
            tooltipStatus.textContent = 'Running';
        } else if (data.paused) {
            tooltipStatus.textContent = data.yield_reason === 'downloads' ? 'Yielding for downloads' : 'Paused';
        } else {
            tooltipStatus.textContent = 'Idle';
        }
    }

    if (tooltipCurrent) {
        if (data.idle) {
            tooltipCurrent.textContent = 'All items processed';
        } else if (data.current_item && data.current_item.name) {
            const type = data.current_item.type || 'item';
            const name = data.current_item.name;
            tooltipCurrent.textContent = `${type.charAt(0).toUpperCase() + type.slice(1)}: "${name}"`;
        } else {
            tooltipCurrent.textContent = 'No active matches';
        }
    }

    if (tooltipProgress && data.progress) {
        const artists = data.progress.artists || {};
        const albums = data.progress.albums || {};
        const tracks = data.progress.tracks || {};

        const currentType = data.current_item?.type;
        let progressText = '';

        const artistsComplete = artists.matched >= artists.total;
        const albumsComplete = albums.matched >= albums.total;

        if (currentType === 'artist' || (!artistsComplete && !currentType)) {
            progressText = `Artists: ${artists.matched || 0} / ${artists.total} (${artists.percent || 0}%)`;
        } else if (currentType === 'album' || (artistsComplete && !albumsComplete)) {
            progressText = `Albums: ${albums.matched || 0} / ${albums.total} (${albums.percent || 0}%)`;
        } else if (currentType === 'track' || (artistsComplete && albumsComplete)) {
            progressText = `Tracks: ${tracks.matched || 0} / ${tracks.total} (${tracks.percent || 0}%)`;
        } else {
            progressText = `Artists: ${artists.matched || 0} / ${artists.total} (${artists.percent || 0}%)`;
        }

        tooltipProgress.textContent = progressText;
    }
}

/**
 * Toggle MusicBrainz enrichment pause/resume
 */
async function toggleMusicBrainzEnrichment() {
    try {
        const button = document.getElementById('musicbrainz-button');
        if (!button) return;

        const isRunning = button.classList.contains('active');
        const endpoint = isRunning ? '/api/musicbrainz/pause' : '/api/musicbrainz/resume';

        const response = await fetch(endpoint, { method: 'POST' });
        if (!response.ok) {
            throw new Error(`Failed to ${isRunning ? 'pause' : 'resume'} MusicBrainz enrichment`);
        }

        // Immediately update UI
        await updateMusicBrainzStatus();

        console.log(`✅ MusicBrainz enrichment ${isRunning ? 'paused' : 'resumed'}`);

    } catch (error) {
        console.error('Error toggling MusicBrainz enrichment:', error);
        showToast(`Error: ${error.message}`, 'error');
    }
}

// Initialize MusicBrainz UI on page load
if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', () => {
        const button = document.getElementById('musicbrainz-button');
        if (button) {
            button.addEventListener('click', toggleMusicBrainzEnrichment);
            // Start polling
            updateMusicBrainzStatus();
            setInterval(updateMusicBrainzStatus, 2000); // Poll every 2 seconds
            console.log('✅ MusicBrainz UI initialized');
        }
    });
} else {
    const button = document.getElementById('musicbrainz-button');
    if (button) {
        button.addEventListener('click', toggleMusicBrainzEnrichment);
        // Start polling
        updateMusicBrainzStatus();
        setInterval(updateMusicBrainzStatus, 2000); // Poll every 2 seconds
        console.log('✅ MusicBrainz UI initialized');
    }
}

// ============================================================================
// AUDIODB ENRICHMENT UI
// ============================================================================

/**
 * Poll AudioDB status every 2 seconds and update UI
 */
async function updateAudioDBStatus() {
    if (socketConnected) return; // WebSocket handles this
    if (document.hidden) return; // Skip polling when tab is not visible
    try {
        const response = await fetch('/api/audiodb/status');
        if (!response.ok) { console.warn('AudioDB status endpoint unavailable'); return; }
        const data = await response.json();
        updateAudioDBStatusFromData(data);
    } catch (error) {
        console.error('Error updating AudioDB status:', error);
    }
}

function updateAudioDBStatusFromData(data) {
    const button = document.getElementById('audiodb-button');
    if (!button) return;

    button.classList.remove('active', 'paused', 'complete');
    if (data.idle) {
        button.classList.add('complete');
    } else if (data.running && !data.paused) {
        button.classList.add('active');
    } else if (data.paused) {
        button.classList.add('paused');
    }

    const tooltipStatus = document.getElementById('audiodb-tooltip-status');
    const tooltipCurrent = document.getElementById('audiodb-tooltip-current');
    const tooltipProgress = document.getElementById('audiodb-tooltip-progress');

    if (tooltipStatus) {
        if (data.idle) { tooltipStatus.textContent = 'Complete'; }
        else if (data.running && !data.paused) { tooltipStatus.textContent = 'Running'; }
        else if (data.paused) { tooltipStatus.textContent = data.yield_reason === 'downloads' ? 'Yielding for downloads' : 'Paused'; }
        else { tooltipStatus.textContent = 'Idle'; }
    }

    if (tooltipCurrent) {
        if (data.idle) {
            tooltipCurrent.textContent = 'All items processed';
        } else if (data.current_item && data.current_item.name) {
            const type = data.current_item.type || 'item';
            const name = data.current_item.name;
            tooltipCurrent.textContent = `${type.charAt(0).toUpperCase() + type.slice(1)}: "${name}"`;
        } else {
            tooltipCurrent.textContent = 'No active matches';
        }
    }

    if (tooltipProgress && data.progress) {
        const artists = data.progress.artists || {};
        const albums = data.progress.albums || {};
        const tracks = data.progress.tracks || {};

        const currentType = data.current_item?.type;
        let progressText = '';

        const artistsComplete = artists.matched >= artists.total;
        const albumsComplete = albums.matched >= albums.total;

        if (currentType === 'artist' || (!artistsComplete && !currentType)) {
            progressText = `Artists: ${artists.matched || 0} / ${artists.total || 0} (${artists.percent || 0}%)`;
        } else if (currentType === 'album' || (artistsComplete && !albumsComplete)) {
            progressText = `Albums: ${albums.matched || 0} / ${albums.total || 0} (${albums.percent || 0}%)`;
        } else if (currentType === 'track' || (artistsComplete && albumsComplete)) {
            progressText = `Tracks: ${tracks.matched || 0} / ${tracks.total || 0} (${tracks.percent || 0}%)`;
        } else {
            progressText = `Artists: ${artists.matched || 0} / ${artists.total || 0} (${artists.percent || 0}%)`;
        }

        tooltipProgress.textContent = progressText;
    }
}

function updateDiscogsStatusFromData(data) {
    const button = document.getElementById('discogs-button');
    if (!button) return;

    button.classList.remove('active', 'paused', 'complete');
    if (data.idle) {
        button.classList.add('complete');
    } else if (data.running && !data.paused) {
        button.classList.add('active');
    } else if (data.paused) {
        button.classList.add('paused');
    }

    const tooltipStatus = document.getElementById('discogs-tooltip-status');
    const tooltipCurrent = document.getElementById('discogs-tooltip-current');
    const tooltipProgress = document.getElementById('discogs-tooltip-progress');

    if (tooltipStatus) {
        if (data.idle) tooltipStatus.textContent = 'Complete';
        else if (data.running && !data.paused) tooltipStatus.textContent = 'Running';
        else if (data.paused) tooltipStatus.textContent = data.yield_reason === 'downloads' ? 'Yielding for downloads' : 'Paused';
        else tooltipStatus.textContent = 'Idle';
    }

    if (tooltipCurrent) {
        if (data.idle) tooltipCurrent.textContent = 'All items processed';
        else if (data.current_item) tooltipCurrent.textContent = `Processing: "${data.current_item}"`;
        else tooltipCurrent.textContent = 'No active matches';
    }

    if (tooltipProgress && data.stats) {
        const s = data.stats;
        tooltipProgress.textContent = `Matched: ${s.matched || 0} | Not found: ${s.not_found || 0} | Pending: ${s.pending || 0}`;
    }
}

async function toggleDiscogsEnrichment() {
    try {
        const button = document.getElementById('discogs-button');
        if (!button) return;
        const isPaused = button.classList.contains('paused') || button.classList.contains('complete');
        const endpoint = isPaused ? '/api/discogs/resume' : '/api/discogs/pause';
        const response = await fetch(endpoint, { method: 'POST' });
        if (response.ok) {
            showToast(isPaused ? 'Discogs enrichment resumed' : 'Discogs enrichment paused', 'info');
        }
    } catch (e) {
        showToast('Failed to toggle Discogs enrichment', 'error');
    }
}

/**
 * Toggle AudioDB enrichment pause/resume
 */
async function toggleAudioDBEnrichment() {
    try {
        const button = document.getElementById('audiodb-button');
        if (!button) return;

        const isRunning = button.classList.contains('active');
        const endpoint = isRunning ? '/api/audiodb/pause' : '/api/audiodb/resume';

        const response = await fetch(endpoint, { method: 'POST' });
        if (!response.ok) {
            throw new Error(`Failed to ${isRunning ? 'pause' : 'resume'} AudioDB enrichment`);
        }

        // Immediately update UI
        await updateAudioDBStatus();

        console.log(`✅ AudioDB enrichment ${isRunning ? 'paused' : 'resumed'}`);

    } catch (error) {
        console.error('Error toggling AudioDB enrichment:', error);
        showToast(`Error: ${error.message}`, 'error');
    }
}

// Initialize AudioDB UI on page load
if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', () => {
        const button = document.getElementById('audiodb-button');
        if (button) {
            button.addEventListener('click', toggleAudioDBEnrichment);
            updateAudioDBStatus();
            setInterval(updateAudioDBStatus, 2000);
            console.log('✅ AudioDB UI initialized');
        }
    });
} else {
    const button = document.getElementById('audiodb-button');
    if (button) {
        button.addEventListener('click', toggleAudioDBEnrichment);
        updateAudioDBStatus();
        setInterval(updateAudioDBStatus, 2000);
        console.log('✅ AudioDB UI initialized');
    }
}

// ===================================================================
// DEEZER ENRICHMENT STATUS
// ===================================================================

async function updateDeezerStatus() {
    if (socketConnected) return; // WebSocket handles this
    if (document.hidden) return; // Skip polling when tab is not visible
    try {
        const response = await fetch('/api/deezer/status');
        if (!response.ok) { console.warn('Deezer status endpoint unavailable'); return; }
        const data = await response.json();
        updateDeezerStatusFromData(data);
    } catch (error) {
        console.error('Error updating Deezer status:', error);
    }
}

function updateDeezerStatusFromData(data) {
    const button = document.getElementById('deezer-button');
    if (!button) return;

    button.classList.remove('active', 'paused', 'complete');
    if (data.idle) {
        button.classList.add('complete');
    } else if (data.running && !data.paused) {
        button.classList.add('active');
    } else if (data.paused) {
        button.classList.add('paused');
    }

    const tooltipStatus = document.getElementById('deezer-tooltip-status');
    const tooltipCurrent = document.getElementById('deezer-tooltip-current');
    const tooltipProgress = document.getElementById('deezer-tooltip-progress');

    if (tooltipStatus) {
        if (data.idle) { tooltipStatus.textContent = 'Complete'; }
        else if (data.running && !data.paused) { tooltipStatus.textContent = 'Running'; }
        else if (data.paused) { tooltipStatus.textContent = data.yield_reason === 'downloads' ? 'Yielding for downloads' : 'Paused'; }
        else { tooltipStatus.textContent = 'Idle'; }
    }

    if (tooltipCurrent) {
        if (data.idle) {
            tooltipCurrent.textContent = 'All items processed';
        } else if (data.current_item && data.current_item.name) {
            tooltipCurrent.textContent = `Now: ${data.current_item.name}`;
        }
    }

    if (data.progress && tooltipProgress) {
        const artists = data.progress.artists || {};
        const albums = data.progress.albums || {};
        const tracks = data.progress.tracks || {};

        const currentType = data.current_item?.type;
        let progressText = '';

        const artistsComplete = artists.matched >= artists.total;
        const albumsComplete = albums.matched >= albums.total;

        if (currentType === 'artist' || (!artistsComplete && !currentType)) {
            progressText = `Artists: ${artists.matched || 0} / ${artists.total || 0} (${artists.percent || 0}%)`;
        } else if (currentType === 'album' || (artistsComplete && !albumsComplete)) {
            progressText = `Albums: ${albums.matched || 0} / ${albums.total || 0} (${albums.percent || 0}%)`;
        } else if (currentType === 'track' || (artistsComplete && albumsComplete)) {
            progressText = `Tracks: ${tracks.matched || 0} / ${tracks.total || 0} (${tracks.percent || 0}%)`;
        } else {
            progressText = `Artists: ${artists.matched || 0} / ${artists.total || 0} (${artists.percent || 0}%)`;
        }

        tooltipProgress.textContent = progressText;
    }
}

async function toggleDeezerEnrichment() {
    try {
        const button = document.getElementById('deezer-button');
        if (!button) return;

        const isRunning = button.classList.contains('active');
        const endpoint = isRunning ? '/api/deezer/pause' : '/api/deezer/resume';

        const response = await fetch(endpoint, { method: 'POST' });
        if (!response.ok) {
            throw new Error(`Failed to ${isRunning ? 'pause' : 'resume'} Deezer enrichment`);
        }

        // Immediately update UI
        await updateDeezerStatus();

        console.log(`✅ Deezer enrichment ${isRunning ? 'paused' : 'resumed'}`);

    } catch (error) {
        console.error('Error toggling Deezer enrichment:', error);
        showToast(`Error: ${error.message}`, 'error');
    }
}

// Initialize Deezer UI on page load
if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', () => {
        const button = document.getElementById('deezer-button');
        if (button) {
            button.addEventListener('click', toggleDeezerEnrichment);
            updateDeezerStatus();
            setInterval(updateDeezerStatus, 2000);
            console.log('✅ Deezer UI initialized');
        }
    });
} else {
    const button = document.getElementById('deezer-button');
    if (button) {
        button.addEventListener('click', toggleDeezerEnrichment);
        updateDeezerStatus();
        setInterval(updateDeezerStatus, 2000);
        console.log('✅ Deezer UI initialized');
    }
}

// ===================================================================
// SPOTIFY ENRICHMENT STATUS
// ===================================================================

async function updateSpotifyEnrichmentStatus() {
    if (socketConnected) return; // WebSocket handles this
    if (document.hidden) return; // Skip polling when tab is not visible
    try {
        const response = await fetch('/api/spotify-enrichment/status');
        if (!response.ok) { console.warn('Spotify enrichment status endpoint unavailable'); return; }
        const data = await response.json();
        updateSpotifyEnrichmentStatusFromData(data);
    } catch (error) {
        console.error('Error updating Spotify enrichment status:', error);
    }
}

function updateSpotifyEnrichmentStatusFromData(data) {
    const button = document.getElementById('spotify-enrich-button');
    if (!button) return;

    const notAuthenticated = data.authenticated === false;
    const isRateLimited = data.rate_limited === true;
    const budgetExhausted = data.daily_budget && data.daily_budget.exhausted;

    button.classList.remove('active', 'paused', 'complete', 'no-auth');
    if (data.paused) {
        button.classList.add('paused');
    } else if (notAuthenticated) {
        button.classList.add('no-auth');
    } else if (isRateLimited || budgetExhausted) {
        button.classList.add('paused');
    } else if (data.idle) {
        button.classList.add('complete');
    } else if (data.running && !data.paused) {
        button.classList.add('active');
    }

    const tooltipStatus = document.getElementById('spotify-enrich-tooltip-status');
    const tooltipCurrent = document.getElementById('spotify-enrich-tooltip-current');
    const tooltipProgress = document.getElementById('spotify-enrich-tooltip-progress');

    if (tooltipStatus) {
        if (data.paused) { tooltipStatus.textContent = 'Paused'; }
        else if (notAuthenticated) { tooltipStatus.textContent = 'Not Authenticated'; }
        else if (isRateLimited) { tooltipStatus.textContent = 'Rate Limited'; }
        else if (budgetExhausted) { tooltipStatus.textContent = 'Daily Limit Reached'; }
        else if (data.idle) { tooltipStatus.textContent = 'Complete'; }
        else if (data.running) { tooltipStatus.textContent = 'Running'; }
        else { tooltipStatus.textContent = 'Idle'; }
    }

    if (tooltipCurrent) {
        if (data.paused) {
            tooltipCurrent.textContent = notAuthenticated ? 'Connect Spotify in Settings to enrich' : 'Click to resume';
        } else if (notAuthenticated) {
            tooltipCurrent.textContent = 'Connect Spotify in Settings to enrich';
        } else if (isRateLimited) {
            const info = data.rate_limit || {};
            const remaining = info.remaining_seconds || 0;
            tooltipCurrent.textContent = remaining > 0 ? `Waiting ${Math.ceil(remaining / 60)}m for rate limit to clear` : 'Waiting for rate limit to clear';
        } else if (budgetExhausted) {
            const resets = data.daily_budget.resets_in_seconds || 0;
            const hours = Math.floor(resets / 3600);
            const mins = Math.floor((resets % 3600) / 60);
            tooltipCurrent.textContent = `Resets in ${hours}h ${mins}m`;
        } else if (data.idle) {
            tooltipCurrent.textContent = 'All items processed';
        } else if (data.current_item && data.current_item.name) {
            tooltipCurrent.textContent = `Now: ${data.current_item.name}`;
        } else {
            tooltipCurrent.textContent = 'Waiting for next item...';
        }
    }

    if (data.progress && tooltipProgress) {
        if (notAuthenticated) {
            tooltipProgress.textContent = `Pending: ${data.stats?.pending || 0} items`;
        } else {
            const artists = data.progress.artists || {};
            const albums = data.progress.albums || {};
            const tracks = data.progress.tracks || {};

            const currentType = data.current_item?.type || '';
            let progressText = '';

            const artistsComplete = artists.matched >= artists.total;
            const albumsComplete = albums.matched >= albums.total;

            if (currentType === 'artist') {
                progressText = `Artists: ${artists.matched || 0} / ${artists.total || 0} (${artists.percent || 0}%)`;
            } else if (currentType.includes('album')) {
                progressText = `Albums: ${albums.matched || 0} / ${albums.total || 0} (${albums.percent || 0}%)`;
            } else if (currentType.includes('track')) {
                progressText = `Tracks: ${tracks.matched || 0} / ${tracks.total || 0} (${tracks.percent || 0}%)`;
            } else if (!artistsComplete) {
                progressText = `Artists: ${artists.matched || 0} / ${artists.total || 0} (${artists.percent || 0}%)`;
            } else if (!albumsComplete) {
                progressText = `Albums: ${albums.matched || 0} / ${albums.total || 0} (${albums.percent || 0}%)`;
            } else {
                progressText = `Tracks: ${tracks.matched || 0} / ${tracks.total || 0} (${tracks.percent || 0}%)`;
            }

            tooltipProgress.textContent = progressText;
        }
    }
}

async function toggleSpotifyEnrichment() {
    try {
        const button = document.getElementById('spotify-enrich-button');
        if (!button) return;

        const isRunning = button.classList.contains('active');
        const endpoint = isRunning ? '/api/spotify-enrichment/pause' : '/api/spotify-enrichment/resume';

        const response = await fetch(endpoint, { method: 'POST' });
        if (!response.ok) {
            const data = await response.json().catch(() => ({}));
            if (data.rate_limited) {
                showToast('Cannot resume — Spotify is rate limited', 'warning');
                return;
            }
            throw new Error(`Failed to ${isRunning ? 'pause' : 'resume'} Spotify enrichment`);
        }

        await updateSpotifyEnrichmentStatus();
        console.log(`Spotify enrichment ${isRunning ? 'paused' : 'resumed'}`);

    } catch (error) {
        console.error('Error toggling Spotify enrichment:', error);
        showToast(`Error: ${error.message}`, 'error');
    }
}

// Initialize Spotify Enrichment UI on page load
if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', () => {
        const button = document.getElementById('spotify-enrich-button');
        if (button) {
            button.addEventListener('click', toggleSpotifyEnrichment);
            updateSpotifyEnrichmentStatus();
            setInterval(updateSpotifyEnrichmentStatus, 2000);
        }
    });
} else {
    const button = document.getElementById('spotify-enrich-button');
    if (button) {
        button.addEventListener('click', toggleSpotifyEnrichment);
        updateSpotifyEnrichmentStatus();
        setInterval(updateSpotifyEnrichmentStatus, 2000);
    }
}

// ===================================================================
// ITUNES ENRICHMENT STATUS
// ===================================================================

async function updateiTunesEnrichmentStatus() {
    if (socketConnected) return; // WebSocket handles this
    if (document.hidden) return; // Skip polling when tab is not visible
    try {
        const response = await fetch('/api/itunes-enrichment/status');
        if (!response.ok) { console.warn('iTunes enrichment status endpoint unavailable'); return; }
        const data = await response.json();
        updateiTunesEnrichmentStatusFromData(data);
    } catch (error) {
        console.error('Error updating iTunes enrichment status:', error);
    }
}

function updateiTunesEnrichmentStatusFromData(data) {
    const button = document.getElementById('itunes-enrich-button');
    if (!button) return;

    button.classList.remove('active', 'paused', 'complete');
    if (data.idle) {
        button.classList.add('complete');
    } else if (data.running && !data.paused) {
        button.classList.add('active');
    } else if (data.paused) {
        button.classList.add('paused');
    }

    const tooltipStatus = document.getElementById('itunes-enrich-tooltip-status');
    const tooltipCurrent = document.getElementById('itunes-enrich-tooltip-current');
    const tooltipProgress = document.getElementById('itunes-enrich-tooltip-progress');

    if (tooltipStatus) {
        if (data.idle) { tooltipStatus.textContent = 'Complete'; }
        else if (data.running && !data.paused) { tooltipStatus.textContent = 'Running'; }
        else if (data.paused) { tooltipStatus.textContent = data.yield_reason === 'downloads' ? 'Yielding for downloads' : 'Paused'; }
        else { tooltipStatus.textContent = 'Idle'; }
    }

    if (tooltipCurrent) {
        if (data.idle) {
            tooltipCurrent.textContent = 'All items processed';
        } else if (data.current_item && data.current_item.name) {
            tooltipCurrent.textContent = `Now: ${data.current_item.name}`;
        }
    }

    if (data.progress && tooltipProgress) {
        const artists = data.progress.artists || {};
        const albums = data.progress.albums || {};
        const tracks = data.progress.tracks || {};

        const currentType = data.current_item?.type || '';
        let progressText = '';

        const artistsComplete = artists.matched >= artists.total;
        const albumsComplete = albums.matched >= albums.total;

        if (currentType === 'artist') {
            progressText = `Artists: ${artists.matched || 0} / ${artists.total || 0} (${artists.percent || 0}%)`;
        } else if (currentType.includes('album')) {
            progressText = `Albums: ${albums.matched || 0} / ${albums.total || 0} (${albums.percent || 0}%)`;
        } else if (currentType.includes('track')) {
            progressText = `Tracks: ${tracks.matched || 0} / ${tracks.total || 0} (${tracks.percent || 0}%)`;
        } else if (!artistsComplete) {
            progressText = `Artists: ${artists.matched || 0} / ${artists.total || 0} (${artists.percent || 0}%)`;
        } else if (!albumsComplete) {
            progressText = `Albums: ${albums.matched || 0} / ${albums.total || 0} (${albums.percent || 0}%)`;
        } else {
            progressText = `Tracks: ${tracks.matched || 0} / ${tracks.total || 0} (${tracks.percent || 0}%)`;
        }

        tooltipProgress.textContent = progressText;
    }
}

async function toggleiTunesEnrichment() {
    try {
        const button = document.getElementById('itunes-enrich-button');
        if (!button) return;

        const isRunning = button.classList.contains('active');
        const endpoint = isRunning ? '/api/itunes-enrichment/pause' : '/api/itunes-enrichment/resume';

        const response = await fetch(endpoint, { method: 'POST' });
        if (!response.ok) {
            throw new Error(`Failed to ${isRunning ? 'pause' : 'resume'} iTunes enrichment`);
        }

        await updateiTunesEnrichmentStatus();
        console.log(`iTunes enrichment ${isRunning ? 'paused' : 'resumed'}`);

    } catch (error) {
        console.error('Error toggling iTunes enrichment:', error);
        showToast(`Error: ${error.message}`, 'error');
    }
}

// Initialize iTunes Enrichment UI on page load
if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', () => {
        const button = document.getElementById('itunes-enrich-button');
        if (button) {
            button.addEventListener('click', toggleiTunesEnrichment);
            updateiTunesEnrichmentStatus();
            setInterval(updateiTunesEnrichmentStatus, 2000);
        }
    });
} else {
    const button = document.getElementById('itunes-enrich-button');
    if (button) {
        button.addEventListener('click', toggleiTunesEnrichment);
        updateiTunesEnrichmentStatus();
        setInterval(updateiTunesEnrichmentStatus, 2000);
    }
}

// ===================================================================
// LAST.FM ENRICHMENT STATUS
// ===================================================================

async function updateLastFMEnrichmentStatus() {
    if (socketConnected) return;
    if (document.hidden) return;
    try {
        const response = await fetch('/api/lastfm-enrichment/status');
        if (!response.ok) { console.warn('Last.fm status endpoint unavailable'); return; }
        const data = await response.json();
        updateLastFMEnrichmentStatusFromData(data);
    } catch (error) {
        console.error('Error updating Last.fm status:', error);
    }
}

function updateLastFMEnrichmentStatusFromData(data) {
    const button = document.getElementById('lastfm-enrich-button');
    if (!button) return;

    const notAuthenticated = data.authenticated === false;

    button.classList.remove('active', 'paused', 'complete', 'no-auth');
    if (data.paused) {
        button.classList.add('paused');
    } else if (notAuthenticated) {
        button.classList.add('no-auth');
    } else if (data.idle) {
        button.classList.add('complete');
    } else if (data.running && !data.paused) {
        button.classList.add('active');
    }

    const tooltipStatus = document.getElementById('lastfm-enrich-tooltip-status');
    const tooltipCurrent = document.getElementById('lastfm-enrich-tooltip-current');
    const tooltipProgress = document.getElementById('lastfm-enrich-tooltip-progress');

    if (tooltipStatus) {
        if (data.paused) { tooltipStatus.textContent = 'Paused'; }
        else if (notAuthenticated) { tooltipStatus.textContent = 'Not Authenticated'; }
        else if (data.idle) { tooltipStatus.textContent = 'Complete'; }
        else if (data.running) { tooltipStatus.textContent = 'Running'; }
        else { tooltipStatus.textContent = 'Idle'; }
    }

    if (tooltipCurrent) {
        if (data.paused) {
            tooltipCurrent.textContent = notAuthenticated ? 'Add Last.fm API key in Settings to enrich' : 'Click to resume';
        } else if (notAuthenticated) {
            tooltipCurrent.textContent = 'Add Last.fm API key in Settings to enrich';
        } else if (data.idle) {
            tooltipCurrent.textContent = 'All items processed';
        } else if (data.current_item && data.current_item.name) {
            tooltipCurrent.textContent = `Now: ${data.current_item.name}`;
        }
    }

    if (data.progress && tooltipProgress) {
        if (notAuthenticated) {
            tooltipProgress.textContent = `Pending: ${data.stats?.pending || 0} items`;
        } else {
            const artists = data.progress.artists || {};
            const albums = data.progress.albums || {};
            const tracks = data.progress.tracks || {};

            const currentType = data.current_item?.type;
            let progressText = '';

            const artistsComplete = artists.matched >= artists.total;
            const albumsComplete = albums.matched >= albums.total;

            if (currentType === 'artist' || (!artistsComplete && !currentType)) {
                progressText = `Artists: ${artists.matched || 0} / ${artists.total || 0} (${artists.percent || 0}%)`;
            } else if (currentType === 'album' || (artistsComplete && !albumsComplete)) {
                progressText = `Albums: ${albums.matched || 0} / ${albums.total || 0} (${albums.percent || 0}%)`;
            } else if (currentType === 'track' || (artistsComplete && albumsComplete)) {
                progressText = `Tracks: ${tracks.matched || 0} / ${tracks.total || 0} (${tracks.percent || 0}%)`;
            } else {
                progressText = `Artists: ${artists.matched || 0} / ${artists.total || 0} (${artists.percent || 0}%)`;
            }

            tooltipProgress.textContent = progressText;
        }
    }
}

async function toggleLastFMEnrichment() {
    try {
        const button = document.getElementById('lastfm-enrich-button');
        if (!button) return;

        const isRunning = button.classList.contains('active');
        const endpoint = isRunning ? '/api/lastfm-enrichment/pause' : '/api/lastfm-enrichment/resume';

        const response = await fetch(endpoint, { method: 'POST' });
        if (!response.ok) {
            throw new Error(`Failed to ${isRunning ? 'pause' : 'resume'} Last.fm enrichment`);
        }

        await updateLastFMEnrichmentStatus();
        console.log(`Last.fm enrichment ${isRunning ? 'paused' : 'resumed'}`);

    } catch (error) {
        console.error('Error toggling Last.fm enrichment:', error);
        showToast(`Error: ${error.message}`, 'error');
    }
}

if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', () => {
        const button = document.getElementById('lastfm-enrich-button');
        if (button) {
            button.addEventListener('click', toggleLastFMEnrichment);
            updateLastFMEnrichmentStatus();
            setInterval(updateLastFMEnrichmentStatus, 2000);
        }
    });
} else {
    const button = document.getElementById('lastfm-enrich-button');
    if (button) {
        button.addEventListener('click', toggleLastFMEnrichment);
        updateLastFMEnrichmentStatus();
        setInterval(updateLastFMEnrichmentStatus, 2000);
    }
}

// ===================================================================
// GENIUS ENRICHMENT STATUS
// ===================================================================

async function updateGeniusEnrichmentStatus() {
    if (socketConnected) return;
    if (document.hidden) return;
    try {
        const response = await fetch('/api/genius-enrichment/status');
        if (!response.ok) { console.warn('Genius status endpoint unavailable'); return; }
        const data = await response.json();
        updateGeniusEnrichmentStatusFromData(data);
    } catch (error) {
        console.error('Error updating Genius status:', error);
    }
}

function updateGeniusEnrichmentStatusFromData(data) {
    const button = document.getElementById('genius-enrich-button');
    if (!button) return;

    const notAuthenticated = data.authenticated === false;

    button.classList.remove('active', 'paused', 'complete', 'no-auth');
    if (data.paused) {
        button.classList.add('paused');
    } else if (notAuthenticated) {
        button.classList.add('no-auth');
    } else if (data.idle) {
        button.classList.add('complete');
    } else if (data.running && !data.paused) {
        button.classList.add('active');
    }

    const tooltipStatus = document.getElementById('genius-enrich-tooltip-status');
    const tooltipCurrent = document.getElementById('genius-enrich-tooltip-current');
    const tooltipProgress = document.getElementById('genius-enrich-tooltip-progress');

    if (tooltipStatus) {
        if (data.paused) { tooltipStatus.textContent = 'Paused'; }
        else if (notAuthenticated) { tooltipStatus.textContent = 'Not Authenticated'; }
        else if (data.idle) { tooltipStatus.textContent = 'Complete'; }
        else if (data.running) { tooltipStatus.textContent = 'Running'; }
        else { tooltipStatus.textContent = 'Idle'; }
    }

    if (tooltipCurrent) {
        if (data.paused) {
            tooltipCurrent.textContent = notAuthenticated ? 'Add Genius access token in Settings to enrich' : 'Click to resume';
        } else if (notAuthenticated) {
            tooltipCurrent.textContent = 'Add Genius access token in Settings to enrich';
        } else if (data.idle) {
            tooltipCurrent.textContent = 'All items processed';
        } else if (data.current_item && data.current_item.name) {
            tooltipCurrent.textContent = `Now: ${data.current_item.name}`;
        }
    }

    if (data.progress && tooltipProgress) {
        if (notAuthenticated) {
            tooltipProgress.textContent = `Pending: ${data.stats?.pending || 0} items`;
        } else {
            const artists = data.progress.artists || {};
            const tracks = data.progress.tracks || {};

            const currentType = data.current_item?.type;
            let progressText = '';

            const artistsComplete = artists.matched >= artists.total;

            if (currentType === 'artist' || (!artistsComplete && !currentType)) {
                progressText = `Artists: ${artists.matched || 0} / ${artists.total || 0} (${artists.percent || 0}%)`;
            } else {
                progressText = `Tracks: ${tracks.matched || 0} / ${tracks.total || 0} (${tracks.percent || 0}%)`;
            }

            tooltipProgress.textContent = progressText;
        }
    }
}

async function toggleGeniusEnrichment() {
    try {
        const button = document.getElementById('genius-enrich-button');
        if (!button) return;

        const isRunning = button.classList.contains('active');
        const endpoint = isRunning ? '/api/genius-enrichment/pause' : '/api/genius-enrichment/resume';

        const response = await fetch(endpoint, { method: 'POST' });
        if (!response.ok) {
            throw new Error(`Failed to ${isRunning ? 'pause' : 'resume'} Genius enrichment`);
        }

        await updateGeniusEnrichmentStatus();
        console.log(`Genius enrichment ${isRunning ? 'paused' : 'resumed'}`);

    } catch (error) {
        console.error('Error toggling Genius enrichment:', error);
        showToast(`Error: ${error.message}`, 'error');
    }
}

if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', () => {
        const button = document.getElementById('genius-enrich-button');
        if (button) {
            button.addEventListener('click', toggleGeniusEnrichment);
            updateGeniusEnrichmentStatus();
            setInterval(updateGeniusEnrichmentStatus, 2000);
        }
    });
} else {
    const button = document.getElementById('genius-enrich-button');
    if (button) {
        button.addEventListener('click', toggleGeniusEnrichment);
        updateGeniusEnrichmentStatus();
        setInterval(updateGeniusEnrichmentStatus, 2000);
    }
}

// ===================================================================
// TIDAL ENRICHMENT WORKER
// ===================================================================

async function updateTidalEnrichmentStatus() {
    if (socketConnected) return;
    if (document.hidden) return;
    try {
        const response = await fetch('/api/tidal-enrichment/status');
        if (!response.ok) { console.warn('Tidal status endpoint unavailable'); return; }
        const data = await response.json();
        updateTidalEnrichmentStatusFromData(data);
    } catch (error) {
        console.error('Error updating Tidal status:', error);
    }
}

function updateTidalEnrichmentStatusFromData(data) {
    const button = document.getElementById('tidal-enrich-button');
    if (!button) return;

    const notAuthenticated = data.authenticated === false;

    button.classList.remove('active', 'paused', 'complete', 'no-auth');
    if (data.paused) {
        button.classList.add('paused');
    } else if (notAuthenticated) {
        button.classList.add('no-auth');
    } else if (data.idle) {
        button.classList.add('complete');
    } else if (data.running && !data.paused) {
        button.classList.add('active');
    }

    const tooltipStatus = document.getElementById('tidal-enrich-tooltip-status');
    const tooltipCurrent = document.getElementById('tidal-enrich-tooltip-current');
    const tooltipProgress = document.getElementById('tidal-enrich-tooltip-progress');

    if (tooltipStatus) {
        if (data.paused) { tooltipStatus.textContent = 'Paused'; }
        else if (notAuthenticated) { tooltipStatus.textContent = 'Not Authenticated'; }
        else if (data.idle) { tooltipStatus.textContent = 'Complete'; }
        else if (data.running) { tooltipStatus.textContent = 'Running'; }
        else { tooltipStatus.textContent = 'Idle'; }
    }

    if (tooltipCurrent) {
        if (data.paused) {
            tooltipCurrent.textContent = notAuthenticated ? 'Connect Tidal in Settings to enrich' : 'Click to resume';
        } else if (notAuthenticated) {
            tooltipCurrent.textContent = 'Connect Tidal in Settings to enrich';
        } else if (data.idle) {
            tooltipCurrent.textContent = 'All items processed';
        } else if (data.current_item && data.current_item.name) {
            tooltipCurrent.textContent = `Now: ${data.current_item.name}`;
        }
    }

    if (data.progress && tooltipProgress) {
        if (notAuthenticated) {
            tooltipProgress.textContent = `Pending: ${data.stats?.pending || 0} items`;
        } else {
            const artists = data.progress.artists || {};
            const albums = data.progress.albums || {};
            const tracks = data.progress.tracks || {};

            const currentType = data.current_item?.type;
            let progressText = '';

            const artistsComplete = artists.matched >= artists.total;
            const albumsComplete = albums.matched >= albums.total;

            if (currentType === 'artist' || (!artistsComplete && !currentType)) {
                progressText = `Artists: ${artists.matched || 0} / ${artists.total || 0} (${artists.percent || 0}%)`;
            } else if (currentType === 'album' || (!albumsComplete && !currentType)) {
                progressText = `Albums: ${albums.matched || 0} / ${albums.total || 0} (${albums.percent || 0}%)`;
            } else {
                progressText = `Tracks: ${tracks.matched || 0} / ${tracks.total || 0} (${tracks.percent || 0}%)`;
            }

            tooltipProgress.textContent = progressText;
        }
    }
}

async function toggleTidalEnrichment() {
    try {
        const button = document.getElementById('tidal-enrich-button');
        if (!button) return;

        const isRunning = button.classList.contains('active');
        const endpoint = isRunning ? '/api/tidal-enrichment/pause' : '/api/tidal-enrichment/resume';

        const response = await fetch(endpoint, { method: 'POST' });
        if (!response.ok) {
            throw new Error(`Failed to ${isRunning ? 'pause' : 'resume'} Tidal enrichment`);
        }

        await updateTidalEnrichmentStatus();
        console.log(`Tidal enrichment ${isRunning ? 'paused' : 'resumed'}`);

    } catch (error) {
        console.error('Error toggling Tidal enrichment:', error);
        showToast(`Error: ${error.message}`, 'error');
    }
}

if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', () => {
        const button = document.getElementById('tidal-enrich-button');
        if (button) {
            button.addEventListener('click', toggleTidalEnrichment);
            updateTidalEnrichmentStatus();
            setInterval(updateTidalEnrichmentStatus, 2000);
        }
    });
} else {
    const button = document.getElementById('tidal-enrich-button');
    if (button) {
        button.addEventListener('click', toggleTidalEnrichment);
        updateTidalEnrichmentStatus();
        setInterval(updateTidalEnrichmentStatus, 2000);
    }
}

// ===================================================================
// QOBUZ ENRICHMENT WORKER
// ===================================================================

async function updateQobuzEnrichmentStatus() {
    if (socketConnected) return;
    if (document.hidden) return;
    try {
        const response = await fetch('/api/qobuz-enrichment/status');
        if (!response.ok) { console.warn('Qobuz status endpoint unavailable'); return; }
        const data = await response.json();
        updateQobuzEnrichmentStatusFromData(data);
    } catch (error) {
        console.error('Error updating Qobuz status:', error);
    }
}

function updateQobuzEnrichmentStatusFromData(data) {
    const button = document.getElementById('qobuz-enrich-button');
    if (!button) return;

    const notAuthenticated = data.authenticated === false;

    button.classList.remove('active', 'paused', 'complete', 'no-auth');
    if (data.paused) {
        button.classList.add('paused');
    } else if (notAuthenticated) {
        button.classList.add('no-auth');
    } else if (data.idle) {
        button.classList.add('complete');
    } else if (data.running && !data.paused) {
        button.classList.add('active');
    }

    const tooltipStatus = document.getElementById('qobuz-enrich-tooltip-status');
    const tooltipCurrent = document.getElementById('qobuz-enrich-tooltip-current');
    const tooltipProgress = document.getElementById('qobuz-enrich-tooltip-progress');

    if (tooltipStatus) {
        if (data.paused) { tooltipStatus.textContent = 'Paused'; }
        else if (notAuthenticated) { tooltipStatus.textContent = 'Not Authenticated'; }
        else if (data.idle) { tooltipStatus.textContent = 'Complete'; }
        else if (data.running) { tooltipStatus.textContent = 'Running'; }
        else { tooltipStatus.textContent = 'Idle'; }
    }

    if (tooltipCurrent) {
        if (data.paused) {
            tooltipCurrent.textContent = notAuthenticated ? 'Connect Qobuz in Settings to enrich' : 'Click to resume';
        } else if (notAuthenticated) {
            tooltipCurrent.textContent = 'Connect Qobuz in Settings to enrich';
        } else if (data.idle) {
            tooltipCurrent.textContent = 'All items processed';
        } else if (data.current_item && data.current_item.name) {
            tooltipCurrent.textContent = `Now: ${data.current_item.name}`;
        }
    }

    if (data.progress && tooltipProgress) {
        if (notAuthenticated) {
            tooltipProgress.textContent = `Pending: ${data.stats?.pending || 0} items`;
        } else {
            const artists = data.progress.artists || {};
            const albums = data.progress.albums || {};
            const tracks = data.progress.tracks || {};

            const currentType = data.current_item?.type;
            let progressText = '';

            const artistsComplete = artists.matched >= artists.total;
            const albumsComplete = albums.matched >= albums.total;

            if (currentType === 'artist' || (!artistsComplete && !currentType)) {
                progressText = `Artists: ${artists.matched || 0} / ${artists.total || 0} (${artists.percent || 0}%)`;
            } else if (currentType === 'album' || (!albumsComplete && !currentType)) {
                progressText = `Albums: ${albums.matched || 0} / ${albums.total || 0} (${albums.percent || 0}%)`;
            } else {
                progressText = `Tracks: ${tracks.matched || 0} / ${tracks.total || 0} (${tracks.percent || 0}%)`;
            }

            tooltipProgress.textContent = progressText;
        }
    }
}

async function toggleQobuzEnrichment() {
    try {
        const button = document.getElementById('qobuz-enrich-button');
        if (!button) return;

        const isRunning = button.classList.contains('active');
        const endpoint = isRunning ? '/api/qobuz-enrichment/pause' : '/api/qobuz-enrichment/resume';

        const response = await fetch(endpoint, { method: 'POST' });
        if (!response.ok) {
            throw new Error(`Failed to ${isRunning ? 'pause' : 'resume'} Qobuz enrichment`);
        }

        await updateQobuzEnrichmentStatus();
        console.log(`Qobuz enrichment ${isRunning ? 'paused' : 'resumed'}`);

    } catch (error) {
        console.error('Error toggling Qobuz enrichment:', error);
        showToast(`Error: ${error.message}`, 'error');
    }
}

if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', () => {
        const button = document.getElementById('qobuz-enrich-button');
        if (button) {
            button.addEventListener('click', toggleQobuzEnrichment);
            updateQobuzEnrichmentStatus();
            setInterval(updateQobuzEnrichmentStatus, 2000);
        }
    });
} else {
    const button = document.getElementById('qobuz-enrich-button');
    if (button) {
        button.addEventListener('click', toggleQobuzEnrichment);
        updateQobuzEnrichmentStatus();
        setInterval(updateQobuzEnrichmentStatus, 2000);
    }
}

// ===================================================================
// HYDRABASE P2P MIRROR WORKER
// ===================================================================

async function updateHydrabaseStatus() {
    if (socketConnected) return; // WebSocket handles this
    if (document.hidden) return; // Skip polling when tab is not visible
    try {
        const response = await fetch('/api/hydrabase-worker/status');
        if (!response.ok) return;
        const data = await response.json();
        updateHydrabaseStatusFromData(data);
    } catch (error) {
        // Silently ignore — worker may not be available
    }
}

function updateHydrabaseStatusFromData(data) {
    const button = document.getElementById('hydrabase-button');
    if (!button) return;

    button.classList.remove('active', 'paused');
    if (data.running && !data.paused) {
        button.classList.add('active');
    } else if (data.paused) {
        button.classList.add('paused');
    }

    const statusEl = document.getElementById('hydrabase-tooltip-status');
    if (statusEl) {
        if (data.paused) {
            statusEl.textContent = 'Paused';
            statusEl.style.color = '#ffc107';
        } else if (data.running) {
            statusEl.textContent = 'Active';
            statusEl.style.color = '#ffffff';
        } else {
            statusEl.textContent = 'Stopped';
            statusEl.style.color = '#ff5252';
        }
    }
}

async function toggleHydrabaseWorker() {
    const button = document.getElementById('hydrabase-button');
    if (!button) return;
    const isRunning = button.classList.contains('active');
    const endpoint = isRunning ? '/api/hydrabase-worker/pause' : '/api/hydrabase-worker/resume';
    try {
        await fetch(endpoint, { method: 'POST' });
        await updateHydrabaseStatus();
    } catch (error) {
        console.error('Error toggling Hydrabase worker:', error);
    }
}

// Initialize Hydrabase UI on page load
if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', () => {
        const button = document.getElementById('hydrabase-button');
        if (button) {
            button.addEventListener('click', toggleHydrabaseWorker);
            updateHydrabaseStatus();
            setInterval(updateHydrabaseStatus, 2000);
        }
    });
} else {
    const button = document.getElementById('hydrabase-button');
    if (button) {
        button.addEventListener('click', toggleHydrabaseWorker);
        updateHydrabaseStatus();
        setInterval(updateHydrabaseStatus, 2000);
    }
}

// ===================================================================
// LIBRARY REPAIR WORKER
// ===================================================================

async function updateRepairStatus() {
    if (socketConnected) return; // WebSocket handles this
    if (document.hidden) return; // Skip polling when tab is not visible
    try {
        const response = await fetch('/api/repair/status');
        if (!response.ok) { console.warn('Repair status endpoint unavailable'); return; }
        const data = await response.json();
        updateRepairStatusFromData(data);
    } catch (error) {
        console.error('Error updating repair status:', error);
    }
}

function updateRepairStatusFromData(data) {
    const button = document.getElementById('repair-button');
    if (!button) return;

    button.classList.remove('active', 'paused', 'complete');
    if (data.idle) {
        button.classList.add('complete');
    } else if (data.running && !data.paused) {
        button.classList.add('active');
    } else if (data.paused) {
        button.classList.add('paused');
    }

    const tooltipStatus = document.getElementById('repair-tooltip-status');
    const tooltipCurrent = document.getElementById('repair-tooltip-current');
    const tooltipProgress = document.getElementById('repair-tooltip-progress');

    if (tooltipStatus) {
        if (data.idle) { tooltipStatus.textContent = 'Complete'; }
        else if (data.running && !data.paused) { tooltipStatus.textContent = 'Running'; }
        else if (data.paused) { tooltipStatus.textContent = data.yield_reason === 'downloads' ? 'Yielding for downloads' : 'Paused'; }
        else { tooltipStatus.textContent = 'Idle'; }
    }

    if (tooltipCurrent) {
        if (data.idle) {
            tooltipCurrent.textContent = 'All jobs complete — waiting for next schedule';
        } else if (data.current_job && data.current_job.display_name) {
            const jobName = data.current_job.display_name;
            const jobProgress = data.progress && data.progress.current_job;
            if (jobProgress && jobProgress.total > 0) {
                tooltipCurrent.textContent = `${jobName}: ${jobProgress.scanned} / ${jobProgress.total} (${jobProgress.percent}%)`;
            } else {
                tooltipCurrent.textContent = `Running: ${jobName}`;
            }
        } else if (data.current_item && data.current_item.name) {
            tooltipCurrent.textContent = `Running: ${data.current_item.name}`;
        } else {
            tooltipCurrent.textContent = 'No active repairs';
        }
    }

    if (tooltipProgress && data.progress) {
        const tracks = data.progress.tracks || {};
        const parts = [];
        if (tracks.total > 0) parts.push(`Checked: ${tracks.checked || 0} / ${tracks.total || 0}`);
        if (tracks.repaired > 0) parts.push(`Repaired: ${tracks.repaired}`);
        const pending = data.findings_pending || 0;
        if (pending > 0) parts.push(`Findings: ${pending}`);
        tooltipProgress.textContent = parts.length ? parts.join(' · ') : 'No items processed yet';
    }

    // Update findings badge
    const badge = document.getElementById('repair-findings-badge');
    const findingsPending = data.findings_pending || 0;
    if (badge) {
        badge.textContent = findingsPending;
        badge.style.display = findingsPending > 0 ? '' : 'none';
    }
    const tabBadge = document.getElementById('repair-findings-tab-badge');
    if (tabBadge) {
        tabBadge.textContent = findingsPending;
        tabBadge.style.display = findingsPending > 0 ? '' : 'none';
    }

    // Update master toggle in modal if open
    const masterToggle = document.getElementById('repair-master-toggle');
    const masterLabel = document.getElementById('repair-master-label');
    if (masterToggle) masterToggle.checked = data.enabled || false;
    if (masterLabel) masterLabel.textContent = data.enabled ? 'Enabled' : 'Disabled';

    // Update button state
    if (!data.enabled) {
        button.classList.add('paused');
        button.classList.remove('active', 'complete');
    }
}

// ── SoulID Worker Status ──

function updateSoulIDStatusFromData(data) {
    const button = document.getElementById('soulid-button');
    if (!button) return;

    button.classList.remove('active', 'complete');
    if (data.idle) {
        button.classList.add('complete');
    } else if (data.running && !data.paused) {
        button.classList.add('active');
    }

    const tooltipStatus = document.getElementById('soulid-tooltip-status');
    const tooltipCurrent = document.getElementById('soulid-tooltip-current');
    const tooltipProgress = document.getElementById('soulid-tooltip-progress');

    if (tooltipStatus) {
        if (data.idle) tooltipStatus.textContent = 'Complete';
        else if (data.running && !data.paused) tooltipStatus.textContent = 'Running';
        else if (data.paused) tooltipStatus.textContent = 'Paused';
        else tooltipStatus.textContent = 'Idle';
    }

    if (tooltipCurrent) {
        if (data.current_item) {
            tooltipCurrent.textContent = data.current_item;
        } else if (data.idle) {
            tooltipCurrent.textContent = 'All entities have soul IDs';
        } else {
            tooltipCurrent.textContent = 'No items processing';
        }
    }

    if (tooltipProgress && data.stats) {
        const s = data.stats;
        const parts = [];
        if (s.artists_processed) parts.push(`Artists: ${s.artists_processed}`);
        if (s.albums_processed) parts.push(`Albums: ${s.albums_processed}`);
        if (s.tracks_processed) parts.push(`Tracks: ${s.tracks_processed}`);
        if (s.pending > 0) parts.push(`Pending: ${s.pending}`);
        tooltipProgress.textContent = parts.length ? parts.join(' · ') : 'No items processed yet';
    }
}

// ── Repair Modal State ──
let _repairCurrentTab = 'jobs';
let _repairFindingsPage = 0;
let _repairSelectedFindings = new Set();
let _repairFindingsTotal = 0;
const REPAIR_FINDINGS_PAGE_SIZE = 30;
let _repairJobsCache = {}; // Cache job data for help modal

/**
 * Open the Library Maintenance modal
 */
async function openRepairModal() {
    navigateToPage('tools');
    // Scroll to maintenance section
    setTimeout(() => {
        const section = document.querySelector('.tools-maintenance-section');
        if (section) section.scrollIntoView({ behavior: 'smooth' });
    }, 100);
    _repairCurrentTab = 'jobs';
    switchRepairTab('jobs');
    // Load master toggle state
    updateRepairStatus();
    // Load any active job progress
    try {
        const resp = await fetch('/api/repair/progress');
        if (resp.ok) {
            const data = await resp.json();
            if (Object.keys(data).length > 0) {
                // Brief delay so job cards are rendered first
                setTimeout(() => updateRepairJobProgressFromData(data), 300);
            }
        }
    } catch (e) { /* ignore */ }
}

function closeRepairModal() {
    // No-op — repair content now lives on the tools page, no modal to close
}

async function toggleRepairMaster() {
    try {
        const response = await fetch('/api/repair/toggle', { method: 'POST' });
        if (!response.ok) throw new Error('Failed to toggle');
        const data = await response.json();
        const label = document.getElementById('repair-master-label');
        const toggle = document.getElementById('repair-master-toggle');
        if (label) label.textContent = data.enabled ? 'Enabled' : 'Disabled';
        if (toggle) toggle.checked = data.enabled;
        await updateRepairStatus();
    } catch (error) {
        console.error('Error toggling repair master:', error);
        showToast('Error toggling maintenance worker', 'error');
    }
}

function switchRepairTab(tab) {
    _repairCurrentTab = tab;
    document.querySelectorAll('.repair-tab').forEach(t => {
        t.classList.toggle('active', t.dataset.tab === tab);
    });
    document.querySelectorAll('.repair-tab-content').forEach(c => {
        c.style.display = 'none';
    });
    const content = document.getElementById(`repair-tab-${tab}`);
    if (content) content.style.display = '';

    if (tab === 'jobs') loadRepairJobs();
    else if (tab === 'findings') { loadRepairFindingsDashboard(); loadRepairFindings(); }
    else if (tab === 'history') loadRepairHistory();
}

// Turn a snake_case setting key into a human label. Handles acronym fix-ups
// (EP, ID, URL, MB, AC, OS) that the naive Title-Case would otherwise botch.
function _prettifyRepairSettingKey(key) {
    const words = key.replace(/^_+/, '').split('_');
    const acronyms = { 'eps': 'EPs', 'id': 'ID', 'url': 'URL', 'mb': 'MB',
                       'ac': 'AC', 'os': 'OS', 'api': 'API', 'mp3': 'MP3',
                       'flac': 'FLAC', 'cd': 'CD' };
    return words.map(w => acronyms[w.toLowerCase()] || (w.charAt(0).toUpperCase() + w.slice(1))).join(' ');
}

async function loadRepairJobs() {
    const container = document.getElementById('repair-jobs-list');
    if (!container) return;

    try {
        const response = await fetch('/api/repair/jobs');
        if (!response.ok) throw new Error('Failed to fetch jobs');
        const data = await response.json();
        const jobs = data.jobs || [];

        // Cache job data for help modal
        _repairJobsCache = {};
        jobs.forEach(j => { _repairJobsCache[j.job_id] = j; });

        if (jobs.length === 0) {
            container.innerHTML = `<div class="repair-empty-state">
                <div class="repair-empty-icon">🔧</div>
                <div class="repair-empty-title">No Maintenance Jobs</div>
                <div class="repair-empty-text">Library maintenance jobs will appear here once available.</div>
            </div>`;
            return;
        }

        // Populate findings job filter dropdown
        const jobFilter = document.getElementById('repair-findings-job-filter');
        if (jobFilter && jobFilter.options.length <= 1) {
            jobs.forEach(job => {
                const opt = document.createElement('option');
                opt.value = job.job_id;
                opt.textContent = job.display_name;
                jobFilter.appendChild(opt);
            });
        }

        container.innerHTML = jobs.map(job => {
            const lastRunText = job.last_run ? formatCacheAge(job.last_run.finished_at) : 'Never';
            const nextRunText = job.next_run ? formatCacheAge(job.next_run) : (job.enabled ? 'Pending' : '-');
            const statusClass = job.is_running ? 'running' : (job.enabled ? 'idle' : 'disabled');
            const dotClass = job.is_running ? 'running' : (job.enabled ? 'enabled' : 'disabled');
            const cardClass = job.is_running ? 'running' : (!job.enabled ? 'disabled' : '');

            // Build flow badges
            const flowParts = [];
            flowParts.push(`<span class="repair-flow-badge scan">${job.is_running ? '&#9654; Running' : 'Scan'}</span>`);
            if (job.auto_fix) {
                flowParts.push('<span class="repair-flow-arrow">&rarr;</span>');
                const isDryRun = job.settings && job.settings.dry_run === true;
                if (isDryRun) {
                    flowParts.push('<span class="repair-flow-badge dryrun">Dry Run</span>');
                } else {
                    flowParts.push('<span class="repair-flow-badge autofix">Auto-fix</span>');
                }
            }
            // Show pending findings count
            const findingsCount = job.last_run ? (job.last_run.findings_created || 0) : 0;
            if (findingsCount > 0) {
                flowParts.push('<span class="repair-flow-arrow">&rarr;</span>');
                flowParts.push(`<span class="repair-flow-badge findings">${findingsCount} finding${findingsCount !== 1 ? 's' : ''}</span>`);
            }

            // Build meta parts
            const metaParts = [];
            metaParts.push('Last: ' + lastRunText);
            metaParts.push('Next: ' + nextRunText);
            if (job.last_run) {
                metaParts.push(`Scanned: ${(job.last_run.items_scanned || 0).toLocaleString()}`);
                if (job.last_run.auto_fixed) metaParts.push(`Fixed: ${job.last_run.auto_fixed}`);
            }
            if (job.last_run && job.last_run.duration_seconds) {
                metaParts.push(`${job.last_run.duration_seconds.toFixed(1)}s`);
            }

            // Build settings HTML
            let settingsHtml = '';
            if (job.settings && Object.keys(job.settings).length > 0) {
                const settingsRows = Object.entries(job.settings).map(([key, val]) => {
                    // Section header: keys starting with `_section_` render as a
                    // group divider + title instead of a setting row. The value
                    // is the human-readable title.
                    if (key.startsWith('_section_')) {
                        return `<div class="repair-setting-section">${val}</div>`;
                    }
                    const label = _prettifyRepairSettingKey(key);
                    const inputType = typeof val === 'boolean' ? 'checkbox' :
                        typeof val === 'number' ? 'number' : 'text';
                    const inputVal = inputType === 'checkbox' ?
                        (val ? ' checked' : '') :
                        ` value="${val}"`;
                    return `<div class="repair-setting-row">
                        <label>${label}</label>
                        <input type="${inputType}" class="repair-setting-input"
                               data-job="${job.job_id}" data-key="${key}"${inputVal}
                               ${inputType === 'number' ? 'step="0.01" min="0"' : ''}>
                    </div>`;
                }).join('');

                settingsHtml = `
                    <div class="repair-job-settings" id="repair-settings-${job.job_id}" style="display:none;">
                        <div class="repair-setting-row">
                            <label>Interval (hours)</label>
                            <input type="number" class="repair-setting-input"
                                   data-job="${job.job_id}" data-key="_interval_hours"
                                   value="${job.interval_hours}" min="1" step="1">
                        </div>
                        ${settingsRows}
                        <button class="repair-save-settings-btn" onclick="saveRepairJobSettings('${job.job_id}')">Save Settings</button>
                    </div>`;
            }

            return `<div class="repair-job-card ${cardClass}" data-job-id="${job.job_id}">
                <div class="repair-job-main">
                    <div class="repair-job-status ${dotClass}"></div>
                    <div class="repair-job-info">
                        <div class="repair-job-name">${job.display_name}</div>
                        <div class="repair-job-desc">${job.description || ''}</div>
                        <div class="repair-job-flow">${flowParts.join('')}</div>
                        <div class="repair-job-meta">${metaParts.join(' &middot; ')}</div>
                    </div>
                    <div class="repair-job-actions">
                        <label class="repair-job-toggle">
                            <input type="checkbox" ${job.enabled ? 'checked' : ''}
                                   onchange="toggleRepairJob('${job.job_id}', this.checked)">
                            <span class="repair-toggle-slider small"></span>
                        </label>
                        <button class="repair-run-btn" onclick="runRepairJobNow('${job.job_id}')"
                                title="Run now">&#9654;</button>
                        ${Object.keys(job.settings || {}).length > 0 ?
                    `<button class="repair-settings-btn" onclick="expandRepairJobSettings('${job.job_id}')"
                                     title="Settings">&#9881;</button>` : ''}
                        <button class="repair-help-btn" onclick="event.stopPropagation(); showRepairJobHelp('${job.job_id}')"
                                title="About this job">?</button>
                    </div>
                </div>
                ${settingsHtml}
            </div>`;
        }).join('');

    } catch (error) {
        console.error('Error loading repair jobs:', error);
        container.innerHTML = '<div class="repair-empty">Error loading jobs</div>';
    }
}

async function toggleRepairJob(jobId, enabled) {
    try {
        await fetch(`/api/repair/jobs/${jobId}/toggle`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ enabled })
        });
        // Update card visuals immediately
        const card = document.querySelector(`.repair-job-card[data-job-id="${jobId}"]`);
        if (card) {
            card.classList.toggle('disabled', !enabled);
            const dot = card.querySelector('.repair-job-status');
            if (dot) dot.className = 'repair-job-status ' + (enabled ? 'enabled' : 'disabled');
        }
    } catch (error) {
        console.error('Error toggling job:', error);
        showToast('Error toggling job', 'error');
    }
}

function expandRepairJobSettings(jobId) {
    const el = document.getElementById(`repair-settings-${jobId}`);
    if (el) el.style.display = el.style.display === 'none' ? '' : 'none';
}

function showRepairJobHelp(jobId) {
    const job = _repairJobsCache[jobId];
    if (!job) return;

    // Remove existing overlay if present
    let overlay = document.getElementById('repair-help-overlay');
    if (overlay) overlay.remove();

    // Build settings summary (skip `_section_` group-header sentinels)
    let settingsHtml = '';
    if (job.settings && Object.keys(job.settings).length > 0) {
        const rows = Object.entries(job.settings)
            .filter(([key]) => !key.startsWith('_section_'))
            .map(([key, val]) => {
                const label = _prettifyRepairSettingKey(key);
                const display = typeof val === 'boolean' ? (val ? 'Yes' : 'No') : val;
                return `<div class="repair-help-setting"><span class="repair-help-setting-key">${label}</span><span class="repair-help-setting-val">${display}</span></div>`;
            }).join('');
        settingsHtml = `<div class="repair-help-settings-section">
            <div class="repair-help-section-title">Current Settings</div>
            ${rows}
        </div>`;
    }

    // Build info badges
    const badges = [];
    if (job.auto_fix) {
        const isDryRun = job.settings && job.settings.dry_run === true;
        badges.push(isDryRun
            ? '<span class="repair-flow-badge dryrun">Dry Run</span>'
            : '<span class="repair-flow-badge autofix">Auto-fix</span>');
    } else {
        badges.push('<span class="repair-flow-badge scan">Scan Only</span>');
    }
    badges.push(`<span class="repair-flow-badge scan">Every ${job.interval_hours}h</span>`);
    if (job.enabled) {
        badges.push('<span class="repair-flow-badge" style="background:rgba(74,222,128,0.12);color:#4ade80;">Enabled</span>');
    } else {
        badges.push('<span class="repair-flow-badge" style="background:rgba(255,255,255,0.06);color:rgba(255,255,255,0.4);">Disabled</span>');
    }

    // Format help text paragraphs
    const helpBody = (job.help_text || job.description || '').split('\n\n').map(p => {
        if (p.startsWith('Settings:\n')) {
            const lines = p.split('\n').slice(1);
            return '<div class="repair-help-setting-list">' +
                lines.map(l => `<div class="repair-help-setting-item">${l.replace(/^- /, '')}</div>`).join('') +
                '</div>';
        }
        return `<p>${p.replace(/\n/g, '<br>')}</p>`;
    }).join('');

    overlay = document.createElement('div');
    overlay.id = 'repair-help-overlay';
    overlay.className = 'repair-help-overlay';
    overlay.onclick = (e) => { if (e.target === overlay) overlay.remove(); };
    overlay.innerHTML = `
        <div class="repair-help-modal">
            <div class="repair-help-header">
                <h3>${job.display_name}</h3>
                <button class="repair-help-close" onclick="document.getElementById('repair-help-overlay').remove()">&times;</button>
            </div>
            <div class="repair-help-badges">${badges.join('')}</div>
            <div class="repair-help-body">${helpBody}</div>
            ${settingsHtml}
        </div>
    `;
    document.body.appendChild(overlay);
}

async function saveRepairJobSettings(jobId) {
    try {
        const inputs = document.querySelectorAll(`.repair-setting-input[data-job="${jobId}"]`);
        let intervalHours = null;
        const settings = {};

        inputs.forEach(input => {
            const key = input.dataset.key;
            if (key === '_interval_hours') {
                intervalHours = parseInt(input.value) || 24;
            } else {
                if (input.type === 'checkbox') settings[key] = input.checked;
                else if (input.type === 'number') settings[key] = parseFloat(input.value);
                else settings[key] = input.value;
            }
        });

        await fetch(`/api/repair/jobs/${jobId}/settings`, {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ interval_hours: intervalHours, settings })
        });

        showToast('Settings saved', 'success');
    } catch (error) {
        console.error('Error saving job settings:', error);
        showToast('Error saving settings', 'error');
    }
}

async function runRepairJobNow(jobId) {
    try {
        await fetch(`/api/repair/jobs/${jobId}/run`, { method: 'POST' });
        showToast('Job started', 'success');
        setTimeout(() => loadRepairJobs(), 1000);
    } catch (error) {
        console.error('Error running job:', error);
        showToast('Error starting job', 'error');
    }
}

// ── Repair Job Live Progress ──
const _repairProgressLogCounts = {};
const _repairProgressHideTimers = {};

function updateRepairJobProgressFromData(data) {
    for (const [jobId, state] of Object.entries(data)) {
        const card = document.querySelector(`.repair-job-card[data-job-id="${jobId}"]`);
        if (!card) continue;

        // Update status dot
        const statusDot = card.querySelector('.repair-job-status');
        if (statusDot) {
            if (state.status === 'running') statusDot.className = 'repair-job-status running';
            else if (state.status === 'finished') statusDot.className = 'repair-job-status enabled';
            else if (state.status === 'error') statusDot.className = 'repair-job-status enabled';
        }

        // Update flow badge to show running state
        const firstBadge = card.querySelector('.repair-flow-badge.scan');
        if (firstBadge) {
            if (state.status === 'running') firstBadge.innerHTML = '&#9654; Running';
            else if (state.status === 'finished') firstBadge.innerHTML = '&#10003; Complete';
            else if (state.status === 'error') firstBadge.innerHTML = '&#10007; Error';
        }

        // Add/update card running class
        card.classList.toggle('running', state.status === 'running');
        card.classList.remove('disabled');

        // Create or find progress panel (bar-first layout like automation)
        let panel = card.querySelector('.repair-job-progress');
        if (!panel) {
            panel = document.createElement('div');
            panel.className = 'repair-job-progress';
            panel.innerHTML = `
                <div class="repair-progress-bar-wrap">
                    <div class="repair-progress-bar" style="width:0%"></div>
                </div>
                <div class="repair-progress-phase"></div>
                <div class="repair-progress-log"></div>
            `;
            card.appendChild(panel);
        }

        // Show panel
        panel.classList.add('visible');
        panel.classList.toggle('finished', state.status === 'finished');
        panel.classList.toggle('error', state.status === 'error');

        if (state.status === 'running') {
            panel.classList.remove('finished', 'error');
            if (_repairProgressHideTimers[jobId]) {
                clearTimeout(_repairProgressHideTimers[jobId]);
                delete _repairProgressHideTimers[jobId];
            }
            // Reset log for re-run
            if (_repairProgressLogCounts[jobId] > 0 && state.log && state.log.length < _repairProgressLogCounts[jobId]) {
                const existingLog = panel.querySelector('.repair-progress-log');
                if (existingLog) existingLog.innerHTML = '';
                _repairProgressLogCounts[jobId] = 0;
            }
        }

        // Update progress bar
        const bar = panel.querySelector('.repair-progress-bar');
        if (bar) bar.style.width = (state.progress || 0) + '%';

        // Update phase
        const phaseEl = panel.querySelector('.repair-progress-phase');
        if (phaseEl && state.phase) phaseEl.textContent = state.phase;

        // Update log
        const logEl = panel.querySelector('.repair-progress-log');
        if (logEl && state.log) {
            const prevCount = _repairProgressLogCounts[jobId] || 0;
            if (state.log.length > prevCount) {
                const newLines = state.log.slice(prevCount);
                for (const line of newLines) {
                    const div = document.createElement('div');
                    div.className = 'repair-log-line ' + (line.type || 'info');
                    div.textContent = line.text;
                    logEl.appendChild(div);
                }
                logEl.scrollTop = logEl.scrollHeight;
            }
            _repairProgressLogCounts[jobId] = state.log.length;
        }

        // Auto-hide panel after completion
        if (state.status === 'finished' || state.status === 'error') {
            if (!_repairProgressHideTimers[jobId]) {
                _repairProgressHideTimers[jobId] = setTimeout(() => {
                    panel.classList.remove('visible');
                    card.classList.remove('running');
                    delete _repairProgressHideTimers[jobId];
                    delete _repairProgressLogCounts[jobId];
                    // Reload to get updated stats
                    loadRepairJobs();
                }, 30000);
            }
        } else {
            // Clear any existing hide timer if job restarts
            if (_repairProgressHideTimers[jobId]) {
                clearTimeout(_repairProgressHideTimers[jobId]);
                delete _repairProgressHideTimers[jobId];
            }
        }
    }
}

async function loadRepairFindingsDashboard() {
    const dashboard = document.getElementById('repair-findings-dashboard');
    if (!dashboard) return;

    try {
        const response = await fetch('/api/repair/findings/counts');
        if (!response.ok) throw new Error('Failed to fetch counts');
        const data = await response.json();

        const pending = data.pending || 0;
        const resolved = data.resolved || 0;
        const dismissed = data.dismissed || 0;
        const autoFixed = data.auto_fixed || 0;
        const byJob = data.by_job || {};

        // Summary stats row
        let html = '<div class="repair-dashboard-summary">';
        html += `<div class="repair-dashboard-stat pending">
            <span class="stat-count">${pending.toLocaleString()}</span> pending
        </div>`;
        html += `<div class="repair-dashboard-stat resolved">
            <span class="stat-count">${resolved.toLocaleString()}</span> resolved
        </div>`;
        html += `<div class="repair-dashboard-stat dismissed">
            <span class="stat-count">${dismissed.toLocaleString()}</span> dismissed
        </div>`;
        if (autoFixed > 0) {
            html += `<div class="repair-dashboard-stat auto-fixed">
                <span class="stat-count">${autoFixed.toLocaleString()}</span> auto-fixed
            </div>`;
        }
        html += '</div>';

        // Per-job chips (only if there are pending findings)
        const jobIds = Object.keys(byJob).sort((a, b) => byJob[b].total - byJob[a].total);
        if (jobIds.length > 0) {
            html += '<div class="repair-dashboard-jobs">';
            const jobFilter = document.getElementById('repair-findings-job-filter');
            const activeJob = jobFilter ? jobFilter.value : '';

            for (const jid of jobIds) {
                const job = byJob[jid];
                const isActive = activeJob === jid;
                const severityDots = [];
                if (job.warning > 0) severityDots.push(`<span class="repair-dashboard-chip-severity warning" title="${job.warning} warnings"></span>`);
                if (job.info > 0) severityDots.push(`<span class="repair-dashboard-chip-severity info" title="${job.info} info"></span>`);

                html += `<div class="repair-dashboard-chip ${isActive ? 'active' : ''}" onclick="filterFindingsByJob('${jid}')">
                    <span class="repair-dashboard-chip-count">${job.total.toLocaleString()}</span>
                    <span class="repair-dashboard-chip-name">${_escFinding(job.display_name || jid.replace(/_/g, ' '))}</span>
                    ${severityDots.length ? `<span class="repair-dashboard-chip-bar">${severityDots.join('')}</span>` : ''}
                </div>`;
            }
            html += '</div>';
        }

        dashboard.innerHTML = html;

        // Load cache health stats
        _loadCacheHealthStats(dashboard);
    } catch (error) {
        console.error('Error loading findings dashboard:', error);
        dashboard.innerHTML = '';
    }
}

async function _loadCacheHealthStats(dashboard) {
    try {
        const response = await fetch('/api/repair/cache-health');
        if (!response.ok) return;
        const stats = await response.json();
        if (!stats.total_entities && !stats.total_searches) return;

        const healthScore = stats.junk_entities === 0 && stats.stale_mb_nulls === 0 ? 'healthy' : stats.junk_entities > 50 ? 'poor' : 'fair';
        const healthLabel = healthScore === 'healthy' ? 'Healthy' : healthScore === 'fair' ? 'Needs Cleanup' : 'Needs Attention';

        // Remove any existing cache-health bar before appending — prevents
        // stacking when multiple dashboard refreshes race and each resolved
        // fetch appends its own section.
        dashboard.querySelectorAll('.repair-cache-health').forEach(el => el.remove());

        const section = document.createElement('div');
        section.className = 'repair-cache-health';
        section.innerHTML = `
            <div class="repair-cache-health-bar" onclick="openCacheHealthModal()">
                <span class="repair-cache-health-dot ${healthScore}"></span>
                <span class="repair-cache-health-title">Metadata Cache</span>
                <span class="repair-cache-health-summary">${stats.total_entities.toLocaleString()} entities · ${healthLabel}</span>
                <span class="repair-cache-health-action">View Details ›</span>
            </div>
        `;
        dashboard.appendChild(section);
    } catch (error) {
        console.error('Error loading cache health:', error);
    }
}

async function openCacheHealthModal() {
    if (document.getElementById('cache-health-modal-overlay')) return;

    const overlay = document.createElement('div');
    overlay.id = 'cache-health-modal-overlay';
    overlay.className = 'modal-overlay';
    overlay.onclick = (e) => { if (e.target === overlay) overlay.remove(); };

    overlay.innerHTML = `
        <div class="cache-health-modal">
            <div class="cache-health-header">
                <div class="cache-health-header-content">
                    <div class="cache-health-header-icon">&#128202;</div>
                    <div>
                        <h2 class="cache-health-title">Cache Health</h2>
                        <p class="cache-health-subtitle">Metadata cache status across all sources</p>
                    </div>
                </div>
                <button class="watch-all-close" onclick="document.getElementById('cache-health-modal-overlay').remove()">&times;</button>
            </div>
            <div class="cache-health-body">
                <div class="cache-health-loading">
                    <div class="watch-all-loading-spinner"></div>
                    <div>Loading cache stats...</div>
                </div>
            </div>
            <div class="cache-health-footer">
                <button class="watch-all-btn watch-all-btn-cancel" onclick="document.getElementById('cache-health-modal-overlay').remove()">Close</button>
            </div>
        </div>
    `;
    document.body.appendChild(overlay);

    try {
        const response = await fetch('/api/repair/cache-health');
        if (!response.ok) throw new Error('Failed to load');
        const s = await response.json();

        const body = overlay.querySelector('.cache-health-body');
        const healthScore = s.junk_entities === 0 && s.stale_mb_nulls === 0 ? 'healthy' : s.junk_entities > 50 ? 'poor' : 'fair';
        const healthEmoji = healthScore === 'healthy' ? '&#10003;' : healthScore === 'fair' ? '&#9888;' : '&#10060;';
        const healthLabel = healthScore === 'healthy' ? 'Cache is healthy' : healthScore === 'fair' ? 'Minor issues detected' : 'Cleanup recommended';

        body.innerHTML = `
            <div class="cache-health-status ${healthScore}">
                <div class="cache-health-status-icon">${healthEmoji}</div>
                <div class="cache-health-status-text">${healthLabel}</div>
            </div>

            <div class="cache-health-cards">
                <div class="cache-health-card">
                    <div class="cache-health-card-value">${s.total_entities.toLocaleString()}</div>
                    <div class="cache-health-card-label">Total Entities</div>
                </div>
                <div class="cache-health-card">
                    <div class="cache-health-card-value">${s.total_searches.toLocaleString()}</div>
                    <div class="cache-health-card-label">Search Results</div>
                </div>
                <div class="cache-health-card">
                    <div class="cache-health-card-value ${s.junk_entities > 0 ? 'warn' : ''}">${s.junk_entities}</div>
                    <div class="cache-health-card-label">Junk Entries</div>
                </div>
                <div class="cache-health-card ${s.stale_mb_nulls > 0 ? 'clickable' : ''}" ${s.stale_mb_nulls > 0 ? 'onclick="openFailedMBLookupsModal()"' : ''}>
                    <div class="cache-health-card-value ${s.stale_mb_nulls > 10 ? 'warn' : ''}">${s.stale_mb_nulls}</div>
                    <div class="cache-health-card-label">Failed MB Lookups</div>
                    ${s.stale_mb_nulls > 0 ? '<div class="cache-health-card-action">Manage ›</div>' : ''}
                </div>
            </div>

            <div class="cache-health-section">
                <div class="cache-health-section-title">By Source</div>
                <div class="cache-health-source-bars">
                    ${(() => {
                const allSources = { ...(s.by_source || {}) };
                if (s.total_musicbrainz) allSources['musicbrainz'] = s.total_musicbrainz;
                const maxCount = Math.max(...Object.values(allSources), 1);
                return Object.entries(allSources).map(([src, count]) => {
                    const pct = Math.round(count / maxCount * 100);
                    const color = src === 'spotify' ? '#1DB954' : src === 'itunes' ? '#FC3C44' : src === 'deezer' ? '#A238FF' : src === 'musicbrainz' ? '#BA478F' : '#666';
                    return `<div class="cache-health-source-row">
                                <span class="cache-health-source-name">${src === 'musicbrainz' ? 'MusicBrainz' : src}</span>
                                <div class="cache-health-source-track"><div class="cache-health-source-fill" style="width:${pct}%;background:${color}"></div></div>
                                <span class="cache-health-source-count">${count.toLocaleString()}</span>
                            </div>`;
                }).join('');
            })()}
                </div>
            </div>

            <div class="cache-health-section">
                <div class="cache-health-section-title">By Type</div>
                <div class="cache-health-type-pills">
                    ${Object.entries(s.by_type || {}).map(([type, count]) => `<span class="cache-health-pill">${type}s <strong>${count.toLocaleString()}</strong></span>`).join('')}
                </div>
            </div>

            <div class="cache-health-section">
                <div class="cache-health-section-title">Metrics</div>
                <div class="cache-health-metrics">
                    <div class="cache-health-metric"><span class="cache-health-metric-label">Average Age</span><span class="cache-health-metric-value">${s.avg_age_days} days</span></div>
                    <div class="cache-health-metric"><span class="cache-health-metric-label">Total Cache Hits</span><span class="cache-health-metric-value">${s.total_access_hits.toLocaleString()}</span></div>
                    <div class="cache-health-metric"><span class="cache-health-metric-label">Expiring in 24h</span><span class="cache-health-metric-value">${s.expiring_24h}</span></div>
                    <div class="cache-health-metric"><span class="cache-health-metric-label">Expiring in 7 days</span><span class="cache-health-metric-value">${s.expiring_7d}</span></div>
                </div>
            </div>
        `;
    } catch (error) {
        const body = overlay.querySelector('.cache-health-body');
        body.innerHTML = '<div class="cache-health-loading">Failed to load cache stats</div>';
    }
}

// ── Failed MB Lookups Management Modal ──
let _failedMBState = { items: [], total: 0, page: 1, filter: '', typeFilter: '', typeCounts: {} };

async function openFailedMBLookupsModal() {
    if (document.getElementById('failed-mb-modal-overlay')) return;

    const overlay = document.createElement('div');
    overlay.id = 'failed-mb-modal-overlay';
    overlay.className = 'modal-overlay';
    overlay.onclick = (e) => { if (e.target === overlay) overlay.remove(); };

    overlay.innerHTML = `
        <div class="failed-mb-modal">
            <div class="failed-mb-header">
                <div>
                    <h2 class="failed-mb-title">Failed MusicBrainz Lookups</h2>
                    <p class="failed-mb-subtitle">Tracks, albums, and artists that couldn't be matched automatically</p>
                </div>
                <button class="watch-all-close" onclick="document.getElementById('failed-mb-modal-overlay').remove()">&times;</button>
            </div>
            <div class="failed-mb-toolbar">
                <div class="failed-mb-tabs" id="failed-mb-tabs"></div>
                <div class="failed-mb-search-row">
                    <input type="text" id="failed-mb-search" class="failed-mb-search-input" placeholder="Filter by name...">
                    <button class="failed-mb-btn failed-mb-btn-danger" onclick="_failedMBClearAll()">Clear All Failed</button>
                </div>
            </div>
            <div class="failed-mb-body" id="failed-mb-body">
                <div class="cache-health-loading"><div class="watch-all-loading-spinner"></div><div>Loading...</div></div>
            </div>
            <div class="failed-mb-footer" id="failed-mb-footer"></div>
        </div>
    `;
    document.body.appendChild(overlay);

    // Search debounce
    const searchInput = overlay.querySelector('#failed-mb-search');
    let searchTimer = null;
    searchInput.addEventListener('input', () => {
        clearTimeout(searchTimer);
        searchTimer = setTimeout(() => {
            _failedMBState.filter = searchInput.value;
            _failedMBState.page = 1;
            _loadFailedMBLookups();
        }, 300);
    });

    _failedMBState = { items: [], total: 0, page: 1, filter: '', typeFilter: '', typeCounts: {} };
    await _loadFailedMBLookups();
}

async function _loadFailedMBLookups() {
    const body = document.getElementById('failed-mb-body');
    if (!body) return;

    // Only fetch type_counts on first load — cache them for tab switches
    const needCounts = Object.keys(_failedMBState.typeCounts).length === 0;
    const params = new URLSearchParams({
        page: _failedMBState.page,
        limit: 50,
    });
    if (needCounts) params.set('counts', 'true');
    if (_failedMBState.typeFilter) params.set('entity_type', _failedMBState.typeFilter);
    if (_failedMBState.filter) params.set('search', _failedMBState.filter);

    try {
        const resp = await fetch(`/api/metadata-cache/failed-mb-lookups?${params}`);
        if (!resp.ok) throw new Error('Failed to load');
        const data = await resp.json();
        _failedMBState.items = data.items;
        _failedMBState.total = data.total;
        if (data.type_counts) _failedMBState.typeCounts = data.type_counts;

        // Render type filter tabs
        const tabsEl = document.getElementById('failed-mb-tabs');
        if (tabsEl) {
            const allCount = Object.values(_failedMBState.typeCounts).reduce((a, b) => a + b, 0);
            let tabsHTML = `<button class="failed-mb-tab ${!_failedMBState.typeFilter ? 'active' : ''}" onclick="_failedMBSetType('')">All (${allCount})</button>`;
            const typeLabels = { artist: 'Artists', release: 'Albums', recording: 'Tracks' };
            for (const [type, count] of Object.entries(_failedMBState.typeCounts)) {
                tabsHTML += `<button class="failed-mb-tab ${_failedMBState.typeFilter === type ? 'active' : ''}" onclick="_failedMBSetType('${type}')">${typeLabels[type] || type} (${count})</button>`;
            }
            tabsEl.innerHTML = tabsHTML;
        }

        // Render items
        if (data.items.length === 0) {
            body.innerHTML = `<div class="failed-mb-empty">${_failedMBState.filter ? 'No matches for your search' : 'No failed lookups — cache is clean!'}</div>`;
        } else {
            const typeIcons = { artist: '🎤', release: '💿', recording: '🎵' };
            body.innerHTML = data.items.map(item => `
                <div class="failed-mb-item" data-id="${item.id}">
                    <div class="failed-mb-item-icon">${typeIcons[item.entity_type] || '?'}</div>
                    <div class="failed-mb-item-info">
                        <div class="failed-mb-item-name">${escapeHtml(item.entity_name)}</div>
                        ${item.artist_name ? `<div class="failed-mb-item-artist">${escapeHtml(item.artist_name)}</div>` : ''}
                    </div>
                    <div class="failed-mb-item-meta">
                        <span class="failed-mb-item-type">${item.entity_type}</span>
                        <span class="failed-mb-item-date">${item.last_updated ? new Date(item.last_updated).toLocaleDateString() : ''}</span>
                    </div>
                    <div class="failed-mb-item-actions">
                        <button class="failed-mb-btn-sm failed-mb-btn-primary" onclick="_failedMBSearch(${item.id}, '${item.entity_type}', '${escapeForInlineJs(item.entity_name)}', '${escapeForInlineJs(item.artist_name || '')}')">Search MB</button>
                        <button class="failed-mb-btn-sm failed-mb-btn-ghost" onclick="_failedMBDelete(${item.id})">Remove</button>
                    </div>
                </div>
            `).join('');
        }

        // Pagination footer
        const footer = document.getElementById('failed-mb-footer');
        if (footer) {
            const totalPages = Math.ceil(data.total / 50);
            footer.innerHTML = totalPages > 1 ? `
                <div class="failed-mb-pagination">
                    <button class="failed-mb-btn-sm" ${_failedMBState.page <= 1 ? 'disabled' : ''} onclick="_failedMBPage(${_failedMBState.page - 1})">Prev</button>
                    <span>Page ${_failedMBState.page} of ${totalPages} (${data.total} total)</span>
                    <button class="failed-mb-btn-sm" ${_failedMBState.page >= totalPages ? 'disabled' : ''} onclick="_failedMBPage(${_failedMBState.page + 1})">Next</button>
                </div>
            ` : `<div class="failed-mb-pagination"><span>${data.total} entries</span></div>`;
        }
    } catch (err) {
        body.innerHTML = '<div class="failed-mb-empty">Failed to load data</div>';
    }
}

function _failedMBSetType(type) {
    _failedMBState.typeFilter = type;
    _failedMBState.page = 1;
    _loadFailedMBLookups();
}

function _failedMBPage(page) {
    _failedMBState.page = page;
    _loadFailedMBLookups();
}

async function _failedMBDelete(entryId) {
    try {
        const resp = await fetch(`/api/metadata-cache/mb-entry/${entryId}`, { method: 'DELETE' });
        if (resp.ok) {
            const row = document.querySelector(`.failed-mb-item[data-id="${entryId}"]`);
            if (row) {
                row.style.opacity = '0';
                setTimeout(() => {
                    row.remove();
                    _failedMBState.typeCounts = {};  // Force refresh counts
                    _loadFailedMBLookups();
                }, 200);
            }
        }
    } catch (err) {
        showToast('Failed to delete entry', 'error');
    }
}

async function _failedMBClearAll() {
    if (!confirm(`Clear all ${_failedMBState.total} failed lookups? They will be retried on next enrichment run.`)) return;
    try {
        const resp = await fetch('/api/metadata-cache/clear-musicbrainz?failed_only=true', { method: 'DELETE' });
        const data = await resp.json();
        if (data.success) {
            showToast(`Cleared ${data.cleared} failed lookups`, 'success');
            _failedMBState.page = 1;
            _failedMBState.typeCounts = {};  // Force refresh counts
            _loadFailedMBLookups();
        }
    } catch (err) {
        showToast('Failed to clear lookups', 'error');
    }
}

// ── MusicBrainz Search Sub-Modal ──
async function _failedMBSearch(entryId, entityType, entityName, artistName) {
    // Remove existing search modal if any
    const existing = document.getElementById('mb-search-modal-overlay');
    if (existing) existing.remove();

    const overlay = document.createElement('div');
    overlay.id = 'mb-search-modal-overlay';
    overlay.className = 'modal-overlay';
    overlay.style.zIndex = '10001';
    overlay.onclick = (e) => { if (e.target === overlay) overlay.remove(); };

    const typeLabels = { artist: 'Artist', release: 'Album', recording: 'Track' };
    overlay.innerHTML = `
        <div class="mb-search-modal">
            <div class="mb-search-header">
                <div>
                    <h2 class="mb-search-title">Search MusicBrainz</h2>
                    <p class="mb-search-subtitle">Find a match for: <strong>${escapeHtml(entityName)}</strong>${artistName ? ` by ${escapeHtml(artistName)}` : ''}</p>
                </div>
                <button class="watch-all-close" onclick="document.getElementById('mb-search-modal-overlay').remove()">&times;</button>
            </div>
            <div class="mb-search-inputs">
                <div class="mb-search-input-row">
                    <label>Type</label>
                    <select id="mb-search-type" class="mb-search-select">
                        <option value="artist" ${entityType === 'artist' ? 'selected' : ''}>Artist</option>
                        <option value="release" ${entityType === 'release' ? 'selected' : ''}>Album / Release</option>
                        <option value="recording" ${entityType === 'recording' ? 'selected' : ''}>Track / Recording</option>
                    </select>
                </div>
                <div class="mb-search-input-row">
                    <label>Name</label>
                    <input type="text" id="mb-search-query" class="mb-search-input" value="${escapeHtml(entityName)}">
                </div>
                <div class="mb-search-input-row" id="mb-search-artist-row" ${entityType === 'artist' ? 'style="display:none"' : ''}>
                    <label>Artist</label>
                    <input type="text" id="mb-search-artist" class="mb-search-input" value="${escapeHtml(artistName)}">
                </div>
                <button class="failed-mb-btn failed-mb-btn-primary" id="mb-search-go-btn" onclick="_runMBSearch(${entryId})">Search</button>
            </div>
            <div class="mb-search-results" id="mb-search-results">
                <div class="failed-mb-empty">Enter a search query and click Search</div>
            </div>
        </div>
    `;
    document.body.appendChild(overlay);

    // Toggle artist row visibility based on type
    const typeSelect = overlay.querySelector('#mb-search-type');
    typeSelect.addEventListener('change', () => {
        const artistRow = overlay.querySelector('#mb-search-artist-row');
        artistRow.style.display = typeSelect.value === 'artist' ? 'none' : '';
    });

    // Enter to search
    overlay.querySelectorAll('.mb-search-input').forEach(input => {
        input.addEventListener('keydown', (e) => { if (e.key === 'Enter') _runMBSearch(entryId); });
    });

    // Auto-search on open
    _runMBSearch(entryId);
}

async function _runMBSearch(entryId) {
    const resultsEl = document.getElementById('mb-search-results');
    const typeEl = document.getElementById('mb-search-type');
    const queryEl = document.getElementById('mb-search-query');
    const artistEl = document.getElementById('mb-search-artist');
    const goBtn = document.getElementById('mb-search-go-btn');
    if (!resultsEl || !queryEl) return;

    const type = typeEl.value;
    const query = queryEl.value.trim();
    const artist = artistEl ? artistEl.value.trim() : '';
    if (!query) return;

    goBtn.disabled = true;
    goBtn.textContent = 'Searching...';
    resultsEl.innerHTML = '<div class="cache-health-loading"><div class="watch-all-loading-spinner"></div><div>Searching MusicBrainz...</div></div>';

    try {
        const params = new URLSearchParams({ type, q: query, limit: 10 });
        if (artist && type !== 'artist') params.set('artist', artist);

        const resp = await fetch(`/api/musicbrainz/search?${params}`);
        if (!resp.ok) throw new Error('Search failed');
        const data = await resp.json();

        if (!data.results || data.results.length === 0) {
            resultsEl.innerHTML = '<div class="failed-mb-empty">No results found. Try adjusting your search.</div>';
            return;
        }

        resultsEl.innerHTML = data.results.map((r, i) => {
            const scoreColor = r.score >= 90 ? '#4ade80' : r.score >= 70 ? '#fbbf24' : '#f87171';
            let detail = '';
            if (type === 'release') detail = [r.artist, r.date, r.track_count ? `${r.track_count} tracks` : ''].filter(Boolean).join(' · ');
            else if (type === 'recording') detail = [r.artist, r.album].filter(Boolean).join(' · ');
            else detail = [r.type, r.country].filter(Boolean).join(' · ');

            return `
                <div class="mb-search-result" onclick="_selectMBMatch(${entryId}, '${r.mbid}', '${escapeForInlineJs(r.name)}')">
                    <div class="mb-search-result-score" style="color:${scoreColor}">${r.score}%</div>
                    <div class="mb-search-result-info">
                        <div class="mb-search-result-name">${escapeHtml(r.name)}</div>
                        ${r.disambiguation ? `<div class="mb-search-result-disambig">${escapeHtml(r.disambiguation)}</div>` : ''}
                        ${detail ? `<div class="mb-search-result-detail">${escapeHtml(detail)}</div>` : ''}
                    </div>
                    <div class="mb-search-result-mbid" title="${r.mbid}">${r.mbid.substring(0, 8)}...</div>
                </div>
            `;
        }).join('');
    } catch (err) {
        resultsEl.innerHTML = `<div class="failed-mb-empty">Search error: ${err.message}</div>`;
    } finally {
        goBtn.disabled = false;
        goBtn.textContent = 'Search';
    }
}

async function _selectMBMatch(entryId, mbid, mbName) {
    try {
        const resp = await fetch('/api/metadata-cache/mb-match', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ entry_id: entryId, mbid, mb_name: mbName })
        });
        const data = await resp.json();
        if (data.success) {
            showToast(`Matched to: ${mbName}`, 'success');
            // Close search modal, refresh list with fresh counts
            const searchOverlay = document.getElementById('mb-search-modal-overlay');
            if (searchOverlay) searchOverlay.remove();
            _failedMBState.typeCounts = {};
            _loadFailedMBLookups();
        } else {
            showToast(data.error || 'Failed to save match', 'error');
        }
    } catch (err) {
        showToast('Failed to save match', 'error');
    }
}

function filterFindingsByJob(jobId) {
    const jobFilter = document.getElementById('repair-findings-job-filter');
    if (!jobFilter) return;

    // Toggle: click same chip again to clear filter
    if (jobFilter.value === jobId) {
        jobFilter.value = '';
    } else {
        jobFilter.value = jobId;
    }
    _repairFindingsPage = 0;
    loadRepairFindingsDashboard();
    loadRepairFindings();
}

async function loadRepairFindings() {
    const container = document.getElementById('repair-findings-list');
    if (!container) return;

    const jobFilter = document.getElementById('repair-findings-job-filter');
    const severityFilter = document.getElementById('repair-findings-severity-filter');
    const statusFilter = document.getElementById('repair-findings-status-filter');

    const params = new URLSearchParams();
    if (jobFilter && jobFilter.value) params.set('job_id', jobFilter.value);
    if (severityFilter && severityFilter.value) params.set('severity', severityFilter.value);
    if (statusFilter && statusFilter.value) params.set('status', statusFilter.value);
    params.set('page', _repairFindingsPage);
    params.set('limit', REPAIR_FINDINGS_PAGE_SIZE);

    try {
        const response = await fetch(`/api/repair/findings?${params}`);
        if (!response.ok) throw new Error('Failed to fetch findings');
        const data = await response.json();
        const items = data.items || [];

        _repairSelectedFindings.clear();
        _repairFindingsTotal = data.total || 0;
        const bulkBar = document.getElementById('repair-findings-bulk');
        if (bulkBar) bulkBar.style.display = 'none';
        const selectAllCb = document.getElementById('repair-select-all-cb');
        if (selectAllCb) { selectAllCb.checked = false; selectAllCb.indeterminate = false; }

        if (items.length === 0) {
            container.innerHTML = `<div class="repair-empty-state">
                <div class="repair-empty-icon">&#10003;</div>
                <div class="repair-empty-title">All Clear</div>
                <div class="repair-empty-text">No findings match your filters. Your library is looking good!</div>
            </div>`;
            document.getElementById('repair-findings-pagination').innerHTML = '';
            return;
        }

        const severityIcons = { info: 'ℹ️', warning: '⚠️', critical: '🔴' };
        const typeLabels = {
            dead_file: 'Dead File', orphan_file: 'Orphan', acoustid_mismatch: 'Wrong Song',
            acoustid_no_match: 'No Match', fake_lossless: 'Fake Lossless',
            duplicate_tracks: 'Duplicate', incomplete_album: 'Incomplete',
            path_mismatch: 'Path Mismatch', metadata_gap: 'Missing Metadata',
            missing_cover_art: 'Missing Art', track_number_mismatch: 'Track Number',
            missing_lossy_copy: 'No Lossy Copy'
        };

        // Finding types that have an automated fix action
        const fixableTypes = {
            dead_file: 'Re-download',
            orphan_file: 'Resolve',
            track_number_mismatch: 'Fix',
            missing_cover_art: 'Apply Art',
            metadata_gap: 'Apply',
            duplicate_tracks: 'Keep Best',
            incomplete_album: 'Auto-Fill',
            missing_lossy_copy: 'Convert',
            acoustid_mismatch: 'Fix',
            missing_discography_track: 'Add to Wishlist',
        };

        container.innerHTML = items.map(f => {
            const icon = severityIcons[f.severity] || 'ℹ️';
            const age = formatCacheAge(f.created_at);
            const actionLabels = {
                removed_db_entry: 'Entry Removed', added_to_wishlist: 'Wishlisted', deleted_file: 'File Deleted',
                already_gone: 'Already Gone', fixed_track_number: 'Track # Fixed',
                applied_cover_art: 'Art Applied', applied_metadata: 'Metadata Applied',
                removed_duplicates: 'Duplicates Removed',
            };
            let statusBadge = '';
            if (f.status !== 'pending') {
                const actionText = actionLabels[f.user_action] || f.status;
                statusBadge = `<span class="repair-finding-status-badge ${f.status}">${actionText}</span>`;
            }
            const typeLabel = typeLabels[f.finding_type] || f.finding_type.replace(/_/g, ' ');
            const d = f.details || {};
            const filePath = f.file_path || d.original_path || d.file_path || '';
            const fixLabel = fixableTypes[f.finding_type];

            return `<div class="repair-finding-card ${f.severity}" data-id="${f.id}" data-job-id="${f.job_id}" data-mass-orphan="${!!(d.mass_orphan)}">
                <div class="repair-finding-main" onclick="toggleFindingDetail(${f.id})">
                    <div class="repair-finding-select" onclick="event.stopPropagation()">
                        <input type="checkbox" onchange="toggleFindingSelect(${f.id}, this.checked)">
                    </div>
                    <div class="repair-finding-content">
                        <div class="repair-finding-title">
                            <span class="repair-finding-icon">${icon}</span>
                            ${_escFinding(f.title)}
                            <span class="repair-finding-type-badge">${typeLabel}</span>
                            ${statusBadge}
                        </div>
                        <div class="repair-finding-desc">${_escFinding(f.description || '')}</div>
                        ${filePath ? `<div class="repair-finding-path">${_escFinding(filePath)}</div>` : ''}
                        <div class="repair-finding-meta">
                            <span>${f.job_id.replace(/_/g, ' ')}</span>
                            <span>&middot;</span>
                            <span>${f.entity_type || 'file'}</span>
                            ${f.entity_id ? `<span>&middot;</span><span>ID: ${f.entity_id}</span>` : ''}
                            <span>&middot;</span>
                            <span>${age}</span>
                        </div>
                    </div>
                    <div class="repair-finding-actions" onclick="event.stopPropagation()">
                        ${f.status === 'pending' ? `
                            ${fixLabel ? `<button class="repair-finding-btn fix" onclick="fixRepairFinding(${f.id}, '${f.finding_type}')" title="${fixLabel}">${_escFinding(fixLabel)}</button>` : ''}
                            <button class="repair-finding-btn dismiss" onclick="dismissRepairFinding(${f.id})" title="Dismiss">&times;</button>
                        ` : ''}
                        <button class="repair-finding-expand-btn" data-finding="${f.id}" title="Details">&#9660;</button>
                    </div>
                </div>
                <div class="repair-finding-detail" id="repair-detail-${f.id}">
                    <div class="repair-finding-detail-inner">
                        ${_renderFindingDetail(f)}
                    </div>
                </div>
            </div>`;
        }).join('');

        // Pagination
        renderRepairFindingsPagination(data.total, data.page);

    } catch (error) {
        console.error('Error loading findings:', error);
        container.innerHTML = '<div class="repair-empty">Error loading findings</div>';
    }
}

function _escFinding(s) {
    if (!s) return '';
    return s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}

function _renderScoreBar(value, label) {
    const pct = Math.round((value || 0) * 100);
    const cls = pct >= 80 ? 'good' : pct >= 50 ? 'warn' : 'bad';
    return `<div class="repair-score-bar">
        <span class="repair-detail-key">${label}</span>
        <div class="repair-score-bar-track"><div class="repair-score-bar-fill ${cls}" style="width:${pct}%"></div></div>
        <span class="repair-detail-val">${pct}%</span>
    </div>`;
}

function _formatFileSize(bytes) {
    if (!bytes) return '-';
    if (bytes < 1024) return bytes + ' B';
    if (bytes < 1048576) return (bytes / 1024).toFixed(1) + ' KB';
    return (bytes / 1048576).toFixed(1) + ' MB';
}

function _renderPlayButton(f) {
    const d = f.details || {};
    const filePath = f.file_path || d.file_path || d.original_path;
    if (!filePath) return '';
    const title = d.expected_title || d.title || d.file_title || d.matched_title || '';
    const artist = d.expected_artist || d.artist || d.artist_name || '';
    const album = d.album || d.album_title || '';
    const albumArt = d.album_thumb_url || '';
    return `<button class="repair-finding-play-btn" onclick="event.stopPropagation(); playFindingTrack(this)"
        data-path="${_escFinding(filePath)}" data-title="${_escFinding(title)}"
        data-artist="${_escFinding(artist)}" data-album="${_escFinding(album)}"
        data-art="${_escFinding(albumArt)}" data-entity-id="${_escFinding(f.entity_id || '')}" title="Play this track">
        <svg viewBox="0 0 24 24"><polygon points="5,3 19,12 5,21"/></svg> Play
    </button>`;
}

function playFindingTrack(btn) {
    const track = {
        file_path: btn.dataset.path,
        title: btn.dataset.title || 'Unknown Track',
        id: btn.dataset.entityId || null
    };
    const albumTitle = btn.dataset.album || '';
    const artistName = btn.dataset.artist || '';
    playLibraryTrack(track, albumTitle, artistName);
}

function _renderFindingMedia(d) {
    const albumUrl = d.album_thumb_url;
    const artistUrl = d.artist_thumb_url;
    if (!albumUrl && !artistUrl) return '';
    let html = '<div class="repair-finding-media">';
    if (albumUrl) {
        const albumLabel = d.album_title || 'Album';
        html += `<div class="repair-finding-media-card">
            <img class="repair-finding-media-img" src="${_escFinding(albumUrl)}" alt="Album art"
                 onerror="this.parentElement.style.display='none'" />
            <span class="repair-finding-media-label">${_escFinding(albumLabel)}</span>
        </div>`;
    }
    if (artistUrl) {
        const artistLabel = d.artist_name || d.artist || 'Artist';
        html += `<div class="repair-finding-media-card">
            <img class="repair-finding-media-img artist" src="${_escFinding(artistUrl)}" alt="Artist"
                 onerror="this.parentElement.style.display='none'" />
            <span class="repair-finding-media-label">${_escFinding(artistLabel)}</span>
        </div>`;
    }
    html += '</div>';
    return html;
}

function _renderFindingDetail(f) {
    const d = f.details || {};
    const rows = [];
    const media = _renderFindingMedia(d);

    switch (f.finding_type) {
        case 'dead_file':
            if (d.artist) rows.push(['Artist', d.artist]);
            if (d.album) rows.push(['Album', d.album]);
            if (d.title) rows.push(['Title', d.title]);
            if (d.track_id) rows.push(['Track ID', d.track_id]);
            if (d.original_path) rows.push(['Original Path', d.original_path, 'path']);
            return media + _gridRows(rows) + _renderPlayButton(f);

        case 'orphan_file':
            if (d.folder) rows.push(['Folder', d.folder, 'path']);
            if (d.format) rows.push(['Format', d.format.toUpperCase()]);
            if (d.file_size) rows.push(['File Size', _formatFileSize(d.file_size)]);
            if (d.modified) rows.push(['Last Modified', d.modified]);
            if (f.file_path) rows.push(['Full Path', f.file_path, 'path']);
            return _gridRows(rows) + _renderPlayButton(f);

        case 'acoustid_mismatch': {
            let html = media + '<div style="margin-bottom:8px">';
            html += _renderScoreBar(d.fingerprint_score, 'Fingerprint');
            html += _renderScoreBar(d.title_similarity, 'Title Match');
            html += _renderScoreBar(d.artist_similarity, 'Artist Match');
            html += '</div>';
            rows.push(['Expected Title', d.expected_title || '-']);
            rows.push(['Expected Artist', d.expected_artist || '-']);
            rows.push(['AcoustID Title', d.acoustid_title || '-', 'highlight']);
            rows.push(['AcoustID Artist', d.acoustid_artist || '-', 'highlight']);
            if (f.file_path) rows.push(['File', f.file_path, 'path']);
            return html + _gridRows(rows) + _renderPlayButton(f);
        }

        case 'acoustid_no_match':
            if (d.expected_title) rows.push(['Expected Title', d.expected_title]);
            if (d.expected_artist) rows.push(['Expected Artist', d.expected_artist]);
            if (f.file_path) rows.push(['File', f.file_path, 'path']);
            return media + _gridRows(rows) + _renderPlayButton(f);

        case 'fake_lossless': {
            const cutoff = d.detected_cutoff_khz || 0;
            const expectedMin = d.expected_min_khz || 0;
            const nyquist = d.nyquist_khz || (d.sample_rate ? d.sample_rate / 2000 : 22.05);
            let flHtml = '';
            if (cutoff && expectedMin) {
                const cutoffPct = Math.min(100, Math.round((cutoff / nyquist) * 100));
                const expectedPct = Math.min(100, Math.round((expectedMin / nyquist) * 100));
                flHtml += `<div class="repair-spectrum-bar">
                    <div class="repair-spectrum-label">Spectral Analysis</div>
                    <div class="repair-spectrum-track">
                        <div class="repair-spectrum-detected" style="width:${cutoffPct}%"></div>
                        <div class="repair-spectrum-expected" style="left:${expectedPct}%"></div>
                    </div>
                    <div class="repair-spectrum-legend">
                        <span class="repair-spectrum-legend-detected">${cutoff} kHz detected</span>
                        <span class="repair-spectrum-legend-expected">${expectedMin} kHz expected min</span>
                    </div>
                </div>`;
            }
            if (d.format) rows.push(['Format', d.format.toUpperCase()]);
            if (d.sample_rate) rows.push(['Sample Rate', `${d.sample_rate} Hz`]);
            if (d.bit_depth) rows.push(['Bit Depth', `${d.bit_depth}-bit`]);
            if (d.bitrate) rows.push(['Bitrate', `${d.bitrate} kbps`]);
            if (d.file_size) rows.push(['File Size', _formatFileSize(d.file_size)]);
            if (f.file_path) rows.push(['File', f.file_path, 'path']);
            return flHtml + _gridRows(rows) + _renderPlayButton(f);
        }

        case 'duplicate_tracks':
            if (!d.tracks || !d.tracks.length) return _gridRows([['Count', d.count || '?']]);
            // Determine best copy (same logic as backend: highest bitrate, then duration, then track number)
            const bestDup = d.tracks.reduce((best, t) => {
                const bBr = best.bitrate || 0, tBr = t.bitrate || 0;
                const bDur = best.duration || 0, tDur = t.duration || 0;
                const bTn = best.track_number || 0, tTn = t.track_number || 0;
                return (tBr > bBr || (tBr === bBr && tDur > bDur) || (tBr === bBr && tDur === bDur && tTn > bTn)) ? t : best;
            }, d.tracks[0]);
            const findingId = f.id;
            return media + `<div class="repair-detail-sublist">${d.tracks.map((t, i) => {
                const tid = t.track_id || t.id;
                const isBest = (t.id === bestDup.id);
                return `<div class="repair-detail-subitem ${isBest ? 'best' : 'removable'}" style="cursor:pointer;" onclick="selectDuplicateToKeep(${findingId}, '${tid}')" title="Click to keep this version">
                    <strong>
                        ${isBest ? '<span class="repair-keep-badge">KEEP</span>' : '<span class="repair-remove-badge">REMOVE</span>'}
                        ${_escFinding(t.title)} by ${_escFinding(t.artist)}
                    </strong>
                    <span>Album: ${_escFinding(t.album || 'Unknown')}${t.bitrate ? ` &middot; ${t.bitrate} kbps` : ''}${t.duration ? ` &middot; ${Math.round(t.duration)}s` : ''}${t.track_number ? ` &middot; Track #${t.track_number}` : ''}</span>
                    ${t.file_path ? `<span class="mono">${_escFinding(t.file_path)}</span>` : ''}
                </div>`;
            }).join('')}</div>
            <div style="color:rgba(255,255,255,0.3);font-size:11px;padding:4px 0;">Click on a version to keep it, or use "Keep Best" for auto-selection</div>`;

        case 'incomplete_album':
            if (d.artist) rows.push(['Artist', d.artist]);
            if (d.album_title) rows.push(['Album', d.album_title]);
            if (d.primary_source && d.primary_album_id) {
                const primaryLabel = d.primary_source.charAt(0).toUpperCase() + d.primary_source.slice(1);
                rows.push([`${primaryLabel} ID`, d.primary_album_id]);
                if (d.spotify_album_id && d.primary_source !== 'spotify') {
                    rows.push(['Spotify ID', d.spotify_album_id]);
                }
            } else if (d.spotify_album_id) {
                rows.push(['Spotify ID', d.spotify_album_id]);
            }
            let incHtml = media + _gridRows(rows);
            const actual = d.actual_tracks || 0, expected = d.expected_tracks || 0;
            if (expected > 0) {
                const pct = Math.round((actual / expected) * 100);
                incHtml += `<div class="repair-completion-bar">
                    <div class="repair-completion-label">${actual} of ${expected} tracks (${pct}%)</div>
                    <div class="repair-completion-track"><div class="repair-completion-fill" style="width:${pct}%"></div></div>
                </div>`;
            }
            if (d.missing_tracks && d.missing_tracks.length) {
                incHtml += `<div class="repair-detail-sublist">${d.missing_tracks.map(t => `
                    <div class="repair-detail-subitem">
                        <strong>#${t.track_number || '?'} ${_escFinding(t.name || t.title || 'Unknown')}</strong>
                        ${t.source && t.source !== 'spotify' ? `<span>Source: ${_escFinding(t.source)}${t.source_track_id ? ` · ID: ${_escFinding(t.source_track_id)}` : ''}</span>` : ''}
                        ${t.duration_ms ? `<span>Duration: ${Math.round(t.duration_ms / 1000)}s</span>` : ''}
                    </div>`).join('')}</div>`;
            }
            return incHtml;

        case 'path_mismatch':
            if (d.from) rows.push(['Current Path', d.from, 'path']);
            if (d.to) rows.push(['Expected Path', d.to, 'success']);
            return _gridRows(rows);

        case 'metadata_gap':
            if (d.artist) rows.push(['Artist', d.artist]);
            if (d.album) rows.push(['Album', d.album]);
            if (d.title) rows.push(['Title', d.title]);
            if (d.spotify_track_id) rows.push(['Spotify ID', d.spotify_track_id]);
            if (d.resolved_source) rows.push(['Resolved Source', d.resolved_source]);
            if (d.resolved_track_id) rows.push(['Resolved Track ID', d.resolved_track_id]);
            if (d.found_fields && typeof d.found_fields === 'object') {
                Object.entries(d.found_fields).forEach(([k, v]) => {
                    rows.push([`Found: ${k}`, String(v), 'success']);
                });
            }
            return media + _gridRows(rows);

        case 'missing_cover_art':
            if (d.artist) rows.push(['Artist', d.artist]);
            if (d.album_title) rows.push(['Album', d.album_title]);
            if (d.spotify_album_id) rows.push(['Spotify ID', d.spotify_album_id]);
            let artHtml = '';
            // Show artist image + found artwork side by side
            if (d.artist_thumb_url || d.found_artwork_url) {
                artHtml += '<div class="repair-finding-media">';
                if (d.artist_thumb_url) {
                    artHtml += `<div class="repair-finding-media-card">
                        <img class="repair-finding-media-img artist" src="${_escFinding(d.artist_thumb_url)}" alt="Artist"
                             onerror="this.parentElement.style.display='none'" />
                        <span class="repair-finding-media-label">${_escFinding(d.artist || 'Artist')}</span>
                    </div>`;
                }
                if (d.found_artwork_url) {
                    artHtml += `<div class="repair-finding-media-card">
                        <img class="repair-finding-media-img" src="${_escFinding(d.found_artwork_url)}" alt="Found artwork"
                             onerror="this.parentElement.style.display='none'" />
                        <span class="repair-finding-media-label">Found Artwork</span>
                    </div>`;
                }
                artHtml += '</div>';
            }
            artHtml += _gridRows(rows);
            return artHtml;

        case 'track_number_mismatch':
            if (d.album_title) rows.push(['Album', d.album_title]);
            if (d.artist_name) rows.push(['Artist', d.artist_name]);
            if (d.matched_title) rows.push(['Matched To', d.matched_title]);
            if (d.file_title) rows.push(['File Title', d.file_title]);
            if (d.current_track_num !== undefined) rows.push(['Current Track #', String(d.current_track_num)]);
            if (d.correct_track_num !== undefined) rows.push(['Correct Track #', String(d.correct_track_num), 'success']);
            if (f.file_path) rows.push(['File', f.file_path, 'path']);
            let tnHtml = media;
            if (d.match_score) {
                tnHtml += '<div style="margin-bottom:8px">';
                tnHtml += _renderScoreBar(d.match_score, 'Title Match');
                tnHtml += '</div>';
            }
            tnHtml += _gridRows(rows);
            if (d.changes && d.changes.length) {
                tnHtml += `<div class="repair-detail-sublist">${d.changes.map(c => `
                    <div class="repair-detail-subitem"><strong>${_escFinding(c)}</strong></div>`).join('')}</div>`;
            }
            tnHtml += _renderPlayButton(f);
            return tnHtml;

        default:
            // Generic: render all detail keys
            Object.entries(d).forEach(([k, v]) => {
                if (typeof v !== 'object' && !k.endsWith('_thumb_url')) {
                    rows.push([k.replace(/_/g, ' ').replace(/\b\w/g, l => l.toUpperCase()), String(v)]);
                }
            });
            if (f.file_path) rows.push(['File', f.file_path, 'path']);
            return (media || '') + (rows.length ? _gridRows(rows) : '<span style="color:rgba(255,255,255,0.3);font-size:12px;">No additional details available</span>');
    }
}

function _gridRows(rows) {
    if (!rows.length) return '';
    return `<div class="repair-detail-grid">${rows.map(([k, v, cls]) =>
        `<span class="repair-detail-key">${_escFinding(k)}</span><span class="repair-detail-val ${cls || ''}">${_escFinding(v)}</span>`
    ).join('')}</div>`;
}

function toggleFindingDetail(id) {
    const panel = document.getElementById(`repair-detail-${id}`);
    const btn = document.querySelector(`.repair-finding-expand-btn[data-finding="${id}"]`);
    if (!panel) return;
    const isOpen = panel.classList.toggle('open');
    if (btn) btn.classList.toggle('open', isOpen);
}

function toggleFindingSelect(id, checked) {
    if (checked) _repairSelectedFindings.add(id);
    else _repairSelectedFindings.delete(id);

    _updateFindingsBulkBar();
}

function _updateFindingsBulkBar() {
    const bulkBar = document.getElementById('repair-findings-bulk');
    const count = _repairSelectedFindings.size;
    if (bulkBar) bulkBar.style.display = count > 0 ? '' : 'none';
    const countEl = document.getElementById('repair-bulk-count');
    if (countEl) countEl.textContent = count > 0 ? `${count} selected` : '';

    // Show "Fix All (N)" when all on page are selected and there are more pages
    const fixAllBtn = document.getElementById('repair-fix-all-btn');
    if (fixAllBtn && _repairFindingsTotal > 0) {
        const allPageSelected = count > 0 && count >= document.querySelectorAll('.repair-finding-card').length;
        fixAllBtn.style.display = (allPageSelected && _repairFindingsTotal > count) ? '' : 'none';
        fixAllBtn.textContent = `Fix All ${_repairFindingsTotal}`;
    }

    // Sync "Select All" checkbox
    const selectAllCb = document.getElementById('repair-select-all-cb');
    if (selectAllCb) {
        const totalOnPage = document.querySelectorAll('.repair-finding-card').length;
        selectAllCb.checked = totalOnPage > 0 && count >= totalOnPage;
        selectAllCb.indeterminate = count > 0 && count < totalOnPage;
    }
}

function toggleSelectAllFindings(checked) {
    const checkboxes = document.querySelectorAll('.repair-finding-select input[type="checkbox"]');
    checkboxes.forEach(cb => {
        cb.checked = checked;
        const card = cb.closest('.repair-finding-card');
        if (card) {
            const id = parseInt(card.dataset.id);
            if (checked) _repairSelectedFindings.add(id);
            else _repairSelectedFindings.delete(id);
        }
    });
    _updateFindingsBulkBar();
}

async function fixAllMatchingFindings() {
    const jobFilter = document.getElementById('repair-findings-job-filter');
    const severityFilter = document.getElementById('repair-findings-severity-filter');
    const jobId = jobFilter ? jobFilter.value : '';
    const severity = severityFilter ? severityFilter.value : '';

    // If fixing orphan files or dead files, prompt for action FIRST
    let fixAction = null;
    // Discography backfill: 3-option prompt (Add to Wishlist / Just Clear / Cancel).
    // "Just Clear" bypasses bulk-fix entirely and goes through the clear endpoint,
    // which is why it's handled inline and returns early.
    if (jobId === 'discography_backfill') {
        const choice = await _promptDiscographyBackfillAction(_repairFindingsTotal);
        if (!choice) return;
        if (choice === 'dismiss') {
            if (!await showConfirmDialog({
                title: 'Clear All Discography Findings',
                message: `Clear all ${_repairFindingsTotal} discography backfill findings without adding any to the wishlist? Tracks can be re-detected next scan.`,
                confirmText: 'Clear All',
                destructive: false
            })) return;
            showToast(`Clearing ${_repairFindingsTotal} findings...`, 'info');
            try {
                const resp = await fetch('/api/repair/findings/clear', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ job_id: 'discography_backfill', status: 'pending' })
                });
                const result = await resp.json();
                if (result.success) {
                    showToast(`Cleared ${result.deleted} findings`, 'success');
                } else {
                    showToast(result.error || 'Clear failed', 'error');
                }
            } catch (err) {
                console.error('Error clearing findings:', err);
                showToast('Error clearing findings', 'error');
            }
            _repairSelectedFindings.clear();
            loadRepairFindingsDashboard();
            loadRepairFindings();
            updateRepairStatus();
            return;
        }
        // 'add_to_wishlist' falls through to bulk-fix. No destructive warning —
        // the backend handler only adds tracks to the wishlist.
    } else if (jobId === 'dead_file_cleaner') {
        fixAction = await _promptDeadFileAction();
        if (!fixAction) return;
    } else if (jobId === 'orphan_file_detector' || _isMassOrphanFix(jobId, _repairFindingsTotal)) {
        fixAction = await _promptOrphanAction();
        if (!fixAction) return;
        // Confirm before proceeding
        if (fixAction === 'delete' && _repairFindingsTotal > 50) {
            if (!await showWitnessMeDialog(_repairFindingsTotal)) return;
        } else if (fixAction === 'delete') {
            if (!await showConfirmDialog({
                title: 'Delete Orphan Files',
                message: `Permanently delete ${_repairFindingsTotal} orphan files from disk? This cannot be undone.`,
                confirmText: 'Delete',
                destructive: true
            })) return;
        } else if (fixAction === 'staging') {
            if (!await showConfirmDialog({
                title: 'Move to Staging',
                message: `Move ${_repairFindingsTotal} orphan files to the import folder? Files are NOT deleted — you can review and import them.`,
                confirmText: 'Move All to Staging',
                destructive: false
            })) return;
        }
    } else {
        const scopeLabel = jobId ? jobId.replace(/_/g, ' ') : 'all jobs';
        if (!await showConfirmDialog({
            title: 'Fix All Findings',
            message: `Apply fixes to all ${_repairFindingsTotal} pending fixable findings for ${scopeLabel}? This may delete files or remove database entries depending on finding type.`,
            confirmText: 'Fix All',
            destructive: true
        })) return;
    }

    showToast(`Fixing ${_repairFindingsTotal} findings...`, 'info');

    try {
        const body = {};
        if (jobId) body.job_id = jobId;
        if (severity) body.severity = severity;
        if (fixAction) body.fix_action = fixAction;

        const response = await fetch('/api/repair/findings/bulk-fix', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(body)
        });
        const result = await response.json();
        if (result.success) {
            let msg = `Fixed ${result.fixed}${result.failed ? `, ${result.failed} failed` : ''} of ${result.total}`;
            if (result.errors && result.errors.length > 0) {
                msg += `: ${result.errors[0].error}`;
            }
            showToast(msg, result.fixed > 0 ? 'success' : 'error');
        } else {
            showToast(result.error || 'Bulk fix failed', 'error');
        }
    } catch (error) {
        console.error('Error in bulk fix:', error);
        showToast('Error applying bulk fix', 'error');
    }

    _repairSelectedFindings.clear();
    loadRepairFindingsDashboard();
    loadRepairFindings();
    updateRepairStatus();
}

function renderRepairFindingsPagination(total, currentPage) {
    const container = document.getElementById('repair-findings-pagination');
    if (!container) return;

    const totalPages = Math.ceil(total / REPAIR_FINDINGS_PAGE_SIZE);
    if (totalPages <= 1) { container.innerHTML = ''; return; }

    let html = '';
    if (currentPage > 0) {
        html += `<button class="repair-page-btn" onclick="_repairFindingsPage=${currentPage - 1};loadRepairFindings()">&larr;</button>`;
    }

    // Smart page range
    let startPage = Math.max(0, currentPage - 3);
    let endPage = Math.min(totalPages, startPage + 7);
    if (endPage - startPage < 7) startPage = Math.max(0, endPage - 7);

    if (startPage > 0) {
        html += `<button class="repair-page-btn" onclick="_repairFindingsPage=0;loadRepairFindings()">1</button>`;
        if (startPage > 1) html += '<span class="repair-page-info">...</span>';
    }
    for (let i = startPage; i < endPage; i++) {
        html += `<button class="repair-page-btn ${i === currentPage ? 'active' : ''}"
                         onclick="_repairFindingsPage=${i};loadRepairFindings()">${i + 1}</button>`;
    }
    if (endPage < totalPages) {
        if (endPage < totalPages - 1) html += '<span class="repair-page-info">...</span>';
        html += `<button class="repair-page-btn" onclick="_repairFindingsPage=${totalPages - 1};loadRepairFindings()">${totalPages}</button>`;
    }

    if (currentPage < totalPages - 1) {
        html += `<button class="repair-page-btn" onclick="_repairFindingsPage=${currentPage + 1};loadRepairFindings()">&rarr;</button>`;
    }
    html += `<span class="repair-page-info">${total.toLocaleString()} total</span>`;
    container.innerHTML = html;
}

async function selectDuplicateToKeep(findingId, keepTrackId) {
    if (!await showConfirmDialog({ title: 'Keep This Version', message: 'Keep this version and remove the other duplicate(s)?', confirmText: 'Keep', destructive: true })) return;
    try {
        const response = await fetch(`/api/repair/findings/${findingId}/fix`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ fix_action: keepTrackId }),
        });
        const result = await response.json();
        if (result.success) {
            showToast(result.message || 'Duplicate resolved', 'success');
        } else {
            showToast(result.error || 'Failed to resolve duplicate', 'error');
        }
        loadRepairFindingsDashboard();
        loadRepairFindings();
        updateRepairStatus();
    } catch (error) {
        console.error('Error fixing duplicate:', error);
        showToast('Error resolving duplicate', 'error');
    }
}

async function fixRepairFinding(id, findingType) {
    // Orphan files require user to choose an action
    let fixAction = null;
    if (findingType === 'orphan_file') {
        fixAction = await _promptOrphanAction();
        if (!fixAction) return; // User cancelled
    }
    // Dead files: re-download or just remove from DB
    if (findingType === 'dead_file') {
        fixAction = await _promptDeadFileAction();
        if (!fixAction) return;
    }
    // AcoustID mismatch: retag, redownload, or delete
    if (findingType === 'acoustid_mismatch') {
        fixAction = await _promptAcoustidAction();
        if (!fixAction) return;
    }
    // Discography backfill: add to wishlist or just clear the finding
    if (findingType === 'missing_discography_track') {
        const choice = await _promptDiscographyBackfillAction(1);
        if (!choice) return;  // cancel
        if (choice === 'dismiss') {
            // User just wants to remove the finding without adding to wishlist
            await dismissRepairFinding(id);
            return;
        }
        // 'add_to_wishlist' — fall through to the fix endpoint. The handler
        // already defaults to adding to wishlist, so no fix_action is needed.
    }

    const card = document.querySelector(`.repair-finding-card[data-id="${id}"]`);
    const fixBtn = card ? card.querySelector('.repair-finding-btn.fix') : null;
    let originalText = '';
    if (fixBtn) {
        originalText = fixBtn.textContent;
        fixBtn.disabled = true;
        fixBtn.textContent = '...';
    }
    try {
        const body = fixAction ? { fix_action: fixAction } : {};
        const response = await fetch(`/api/repair/findings/${id}/fix`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(body),
        });
        const result = await response.json();
        if (result.success) {
            showToast(result.message || 'Fixed successfully', 'success');
        } else {
            showToast(result.error || 'Fix failed', 'error');
        }
        loadRepairFindingsDashboard();
        loadRepairFindings();
        updateRepairStatus();
    } catch (error) {
        console.error('Error fixing finding:', error);
        showToast('Error applying fix', 'error');
        if (fixBtn) {
            fixBtn.disabled = false;
            fixBtn.textContent = originalText;
        }
    }
}

function _promptOrphanAction() {
    return new Promise(resolve => {
        const overlay = document.createElement('div');
        overlay.className = 'modal-overlay';
        overlay.style.cssText = 'display:flex;align-items:center;justify-content:center;z-index:10000;';
        overlay.innerHTML = `
            <div style="background:#1e1e2e;border:1px solid rgba(255,255,255,0.1);border-radius:16px;padding:28px;max-width:380px;width:90%;text-align:center;">
                <div style="font-size:1.1em;font-weight:600;color:#fff;margin-bottom:8px;">Orphan File Action</div>
                <div style="font-size:0.88em;color:rgba(255,255,255,0.6);margin-bottom:20px;">
                    Choose how to handle orphan files. Staging is safe and reversible.
                </div>
                <div style="display:flex;gap:10px;justify-content:center;">
                    <button id="_orphan-staging" style="padding:10px 20px;border-radius:10px;border:1px solid rgba(29,185,84,0.4);background:rgba(29,185,84,0.15);color:#1db954;font-weight:600;cursor:pointer;font-family:inherit;">
                        Move to Staging
                    </button>
                    <button id="_orphan-delete" style="padding:10px 20px;border-radius:10px;border:1px solid rgba(239,68,68,0.4);background:rgba(239,68,68,0.1);color:#ef4444;font-weight:500;cursor:pointer;font-family:inherit;">
                        Delete
                    </button>
                </div>
                <button id="_orphan-cancel" style="margin-top:12px;padding:6px 16px;border:none;background:none;color:rgba(255,255,255,0.4);cursor:pointer;font-size:0.82em;font-family:inherit;">
                    Cancel
                </button>
            </div>
        `;
        document.body.appendChild(overlay);

        overlay.querySelector('#_orphan-staging').onclick = () => { overlay.remove(); resolve('staging'); };
        overlay.querySelector('#_orphan-delete').onclick = () => { overlay.remove(); resolve('delete'); };
        overlay.querySelector('#_orphan-cancel').onclick = () => { overlay.remove(); resolve(null); };
        overlay.onclick = (e) => { if (e.target === overlay) { overlay.remove(); resolve(null); } };
    });
}

function _promptDeadFileAction() {
    return new Promise(resolve => {
        const overlay = document.createElement('div');
        overlay.className = 'modal-overlay';
        overlay.style.cssText = 'display:flex;align-items:center;justify-content:center;z-index:10000;';
        overlay.innerHTML = `
            <div style="background:#1e1e2e;border:1px solid rgba(255,255,255,0.1);border-radius:16px;padding:28px;max-width:420px;width:90%;text-align:center;">
                <div style="font-size:1.1em;font-weight:600;color:#fff;margin-bottom:8px;">Dead File Action</div>
                <div style="font-size:0.88em;color:rgba(255,255,255,0.6);margin-bottom:20px;">
                    This track's file no longer exists on disk. Choose how to handle it.
                </div>
                <div style="display:flex;gap:10px;justify-content:center;">
                    <button id="_dead-redownload" style="padding:10px 20px;border-radius:10px;border:1px solid rgba(29,185,84,0.4);background:rgba(29,185,84,0.15);color:#1db954;font-weight:600;cursor:pointer;font-family:inherit;">
                        Re-download
                    </button>
                    <button id="_dead-remove" style="padding:10px 20px;border-radius:10px;border:1px solid rgba(239,68,68,0.4);background:rgba(239,68,68,0.1);color:#ef4444;font-weight:500;cursor:pointer;font-family:inherit;">
                        Remove from DB
                    </button>
                </div>
                <button id="_dead-cancel" style="margin-top:12px;padding:6px 16px;border:none;background:none;color:rgba(255,255,255,0.4);cursor:pointer;font-size:0.82em;font-family:inherit;">
                    Cancel
                </button>
            </div>
        `;
        document.body.appendChild(overlay);

        overlay.querySelector('#_dead-redownload').onclick = () => { overlay.remove(); resolve('redownload'); };
        overlay.querySelector('#_dead-remove').onclick = () => { overlay.remove(); resolve('remove'); };
        overlay.querySelector('#_dead-cancel').onclick = () => { overlay.remove(); resolve(null); };
        overlay.onclick = (e) => { if (e.target === overlay) { overlay.remove(); resolve(null); } };
    });
}

function _promptAcoustidAction() {
    return new Promise(resolve => {
        const overlay = document.createElement('div');
        overlay.className = 'modal-overlay';
        overlay.style.cssText = 'display:flex;align-items:center;justify-content:center;z-index:10000;';
        overlay.innerHTML = `
            <div style="background:#1e1e2e;border:1px solid rgba(255,255,255,0.1);border-radius:16px;padding:28px;max-width:460px;width:90%;text-align:center;">
                <div style="font-size:1.1em;font-weight:600;color:#fff;margin-bottom:8px;">AcoustID Mismatch</div>
                <div style="font-size:0.88em;color:rgba(255,255,255,0.6);margin-bottom:20px;">
                    The audio fingerprint doesn't match the expected track. Choose how to fix it.
                </div>
                <div style="display:flex;gap:10px;justify-content:center;flex-wrap:wrap;">
                    <button id="_acid-retag" style="padding:10px 20px;border-radius:10px;border:1px solid rgba(102,126,234,0.4);background:rgba(102,126,234,0.15);color:#667eea;font-weight:600;cursor:pointer;font-family:inherit;">
                        Retag
                    </button>
                    <button id="_acid-redownload" style="padding:10px 20px;border-radius:10px;border:1px solid rgba(29,185,84,0.4);background:rgba(29,185,84,0.15);color:#1db954;font-weight:600;cursor:pointer;font-family:inherit;">
                        Re-download
                    </button>
                    <button id="_acid-delete" style="padding:10px 20px;border-radius:10px;border:1px solid rgba(239,68,68,0.4);background:rgba(239,68,68,0.1);color:#ef4444;font-weight:500;cursor:pointer;font-family:inherit;">
                        Delete
                    </button>
                </div>
                <div style="margin-top:12px;font-size:0.78em;color:rgba(255,255,255,0.35);line-height:1.4;">
                    Retag = update metadata to match actual audio &bull; Re-download = add correct track to wishlist &amp; delete wrong file &bull; Delete = remove file and DB entry
                </div>
                <button id="_acid-cancel" style="margin-top:12px;padding:6px 16px;border:none;background:none;color:rgba(255,255,255,0.4);cursor:pointer;font-size:0.82em;font-family:inherit;">
                    Cancel
                </button>
            </div>
        `;
        document.body.appendChild(overlay);

        overlay.querySelector('#_acid-retag').onclick = () => { overlay.remove(); resolve('retag'); };
        overlay.querySelector('#_acid-redownload').onclick = () => { overlay.remove(); resolve('redownload'); };
        overlay.querySelector('#_acid-delete').onclick = () => { overlay.remove(); resolve('delete'); };
        overlay.querySelector('#_acid-cancel').onclick = () => { overlay.remove(); resolve(null); };
        overlay.onclick = (e) => { if (e.target === overlay) { overlay.remove(); resolve(null); } };
    });
}

function _promptDiscographyBackfillAction(count = 1) {
    const isSingle = count <= 1;
    const headerText = isSingle ? 'Missing Discography Track' : `Missing Discography Tracks (${count})`;
    const bodyText = isSingle
        ? 'Add this track to the wishlist for automatic download, or just clear the finding?'
        : `Add all ${count} selected tracks to the wishlist for automatic download, or just clear the findings?`;
    const addLabel = isSingle ? 'Add to Wishlist' : `Add All ${count} to Wishlist`;
    const clearLabel = isSingle ? 'Just Clear Finding' : 'Just Clear Findings';

    return new Promise(resolve => {
        const overlay = document.createElement('div');
        overlay.className = 'modal-overlay';
        overlay.style.cssText = 'display:flex;align-items:center;justify-content:center;z-index:10000;';
        overlay.innerHTML = `
            <div style="background:#1e1e2e;border:1px solid rgba(255,255,255,0.1);border-radius:16px;padding:28px;max-width:460px;width:90%;text-align:center;">
                <div id="_dbf-header" style="font-size:1.1em;font-weight:600;color:#fff;margin-bottom:8px;"></div>
                <div id="_dbf-body" style="font-size:0.88em;color:rgba(255,255,255,0.6);margin-bottom:20px;"></div>
                <div style="display:flex;gap:10px;justify-content:center;flex-wrap:wrap;">
                    <button id="_dbf-add" style="padding:10px 20px;border-radius:10px;border:1px solid rgba(29,185,84,0.4);background:rgba(29,185,84,0.15);color:#1db954;font-weight:600;cursor:pointer;font-family:inherit;"></button>
                    <button id="_dbf-dismiss" style="padding:10px 20px;border-radius:10px;border:1px solid rgba(102,126,234,0.4);background:rgba(102,126,234,0.15);color:#667eea;font-weight:500;cursor:pointer;font-family:inherit;"></button>
                </div>
                <button id="_dbf-cancel" style="margin-top:12px;padding:6px 16px;border:none;background:none;color:rgba(255,255,255,0.4);cursor:pointer;font-size:0.82em;font-family:inherit;">
                    Cancel
                </button>
            </div>
        `;
        // Assign text content (avoids HTML-escaping gotchas with dynamic values)
        overlay.querySelector('#_dbf-header').textContent = headerText;
        overlay.querySelector('#_dbf-body').textContent = bodyText;
        overlay.querySelector('#_dbf-add').textContent = addLabel;
        overlay.querySelector('#_dbf-dismiss').textContent = clearLabel;
        document.body.appendChild(overlay);

        overlay.querySelector('#_dbf-add').onclick = () => { overlay.remove(); resolve('add_to_wishlist'); };
        overlay.querySelector('#_dbf-dismiss').onclick = () => { overlay.remove(); resolve('dismiss'); };
        overlay.querySelector('#_dbf-cancel').onclick = () => { overlay.remove(); resolve(null); };
        overlay.onclick = (e) => { if (e.target === overlay) { overlay.remove(); resolve(null); } };
    });
}

async function resolveRepairFinding(id) {
    try {
        await fetch(`/api/repair/findings/${id}/resolve`, { method: 'POST' });
        loadRepairFindingsDashboard();
        loadRepairFindings();
        updateRepairStatus();
    } catch (error) {
        console.error('Error resolving finding:', error);
    }
}

async function dismissRepairFinding(id) {
    try {
        await fetch(`/api/repair/findings/${id}/dismiss`, { method: 'POST' });
        loadRepairFindingsDashboard();
        loadRepairFindings();
        updateRepairStatus();
    } catch (error) {
        console.error('Error dismissing finding:', error);
    }
}

async function bulkRepairAction(action) {
    if (_repairSelectedFindings.size === 0) return;
    try {
        await fetch('/api/repair/findings/bulk', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ ids: Array.from(_repairSelectedFindings), action })
        });
        showToast(`${_repairSelectedFindings.size} findings ${action === 'dismiss' ? 'dismissed' : 'resolved'}`, 'success');
        _repairSelectedFindings.clear();
        loadRepairFindingsDashboard();
        loadRepairFindings();
        updateRepairStatus();
    } catch (error) {
        console.error('Error bulk updating findings:', error);
        showToast('Error updating findings', 'error');
    }
}

async function bulkFixFindings() {
    if (_repairSelectedFindings.size === 0) return;
    const ids = Array.from(_repairSelectedFindings);

    // If any selected findings are orphan files, prompt for action FIRST
    const selectedOrphanCards = ids.filter(id => {
        const card = document.querySelector(`.repair-finding-card[data-id="${id}"]`);
        return card && card.dataset.jobId === 'orphan_file_detector';
    });
    let orphanFixAction = null;
    if (selectedOrphanCards.length > 0) {
        orphanFixAction = await _promptOrphanAction();
        if (!orphanFixAction) return;
        // Only show scary dialog for mass deletion, not staging
        if (orphanFixAction === 'delete' && selectedOrphanCards.length > MASS_ORPHAN_THRESHOLD) {
            const hasMassFlag = ids.some(id => {
                const card = document.querySelector(`.repair-finding-card[data-id="${id}"]`);
                return card && card.dataset.massOrphan === 'true';
            });
            if (hasMassFlag && !await showWitnessMeDialog(selectedOrphanCards.length)) return;
        }
    }

    // If any selected findings are dead files, prompt for action
    const selectedDeadCards = ids.filter(id => {
        const card = document.querySelector(`.repair-finding-card[data-id="${id}"]`);
        return card && card.dataset.jobId === 'dead_file_cleaner';
    });
    let deadFixAction = null;
    if (selectedDeadCards.length > 0) {
        deadFixAction = await _promptDeadFileAction();
        if (!deadFixAction) return;
    }

    // If any selected findings are AcoustID mismatches, prompt for action
    const selectedAcoustidCards = ids.filter(id => {
        const card = document.querySelector(`.repair-finding-card[data-id="${id}"]`);
        return card && card.dataset.jobId === 'acoustid_scanner';
    });
    let acoustidFixAction = null;
    if (selectedAcoustidCards.length > 0) {
        acoustidFixAction = await _promptAcoustidAction();
        if (!acoustidFixAction) return;
    }

    // If any selected findings are discography backfill, prompt once (add-to-wishlist vs clear)
    const selectedBackfillCards = ids.filter(id => {
        const card = document.querySelector(`.repair-finding-card[data-id="${id}"]`);
        return card && card.dataset.jobId === 'discography_backfill';
    });
    let backfillAction = null;
    if (selectedBackfillCards.length > 0) {
        backfillAction = await _promptDiscographyBackfillAction(selectedBackfillCards.length);
        if (!backfillAction) return;
    }

    let fixed = 0, failed = 0, lastError = '';
    showToast(`Fixing ${ids.length} findings...`, 'info');

    for (const id of ids) {
        try {
            // Determine if this finding needs a specific action
            const card = document.querySelector(`.repair-finding-card[data-id="${id}"]`);
            const isOrphan = card && card.dataset.jobId === 'orphan_file_detector';
            const isDead = card && card.dataset.jobId === 'dead_file_cleaner';
            const isAcoustid = card && card.dataset.jobId === 'acoustid_scanner';
            const isBackfill = card && card.dataset.jobId === 'discography_backfill';

            // Discography backfill "Just Clear" path uses the dismiss endpoint,
            // not the fix endpoint — so handle it inline before the fix call.
            if (isBackfill && backfillAction === 'dismiss') {
                try {
                    const resp = await fetch(`/api/repair/findings/${id}/dismiss`, { method: 'POST' });
                    if (resp.ok) fixed++;
                    else { failed++; lastError = 'dismiss failed'; }
                } catch {
                    failed++;
                }
                continue;
            }

            let body = {};
            if (isOrphan && orphanFixAction) body = { fix_action: orphanFixAction };
            else if (isDead && deadFixAction) body = { fix_action: deadFixAction };
            else if (isAcoustid && acoustidFixAction) body = { fix_action: acoustidFixAction };
            // Discography backfill "Add to Wishlist" falls through with empty body
            // — the fix handler already adds to wishlist by default.

            const response = await fetch(`/api/repair/findings/${id}/fix`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(body),
            });
            const result = await response.json();
            if (result.success) fixed++;
            else { failed++; lastError = result.error || 'unknown error'; }
        } catch {
            failed++;
        }
    }

    _repairSelectedFindings.clear();
    let fixMsg = `Fixed ${fixed}${failed ? `, ${failed} failed` : ''}`;
    if (failed && lastError) fixMsg += `: ${lastError}`;
    showToast(fixMsg, fixed > 0 ? 'success' : 'error');
    loadRepairFindingsDashboard();
    loadRepairFindings();
    updateRepairStatus();
}

async function clearRepairFindings() {
    const jobFilter = document.getElementById('repair-findings-job-filter');
    const statusFilter = document.getElementById('repair-findings-status-filter');
    const jobId = jobFilter ? jobFilter.value : '';
    const status = statusFilter ? statusFilter.value : '';

    const scopeLabel = jobId ? jobId.replace(/_/g, ' ') : 'all jobs';
    const statusLabel = status ? ` (${status})` : '';
    if (!await showConfirmDialog({
        title: 'Clear Findings',
        message: `Delete all findings for ${scopeLabel}${statusLabel}? This cannot be undone.`,
        confirmText: 'Clear',
        destructive: true
    })) return;

    try {
        const body = {};
        if (jobId) body.job_id = jobId;
        if (status) body.status = status;

        const response = await fetch('/api/repair/findings/clear', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(body)
        });
        const result = await response.json();
        if (result.success) {
            showToast(`Cleared ${result.deleted} findings`, 'success');
        } else {
            showToast(result.error || 'Failed to clear findings', 'error');
        }
        _repairSelectedFindings.clear();
        loadRepairFindingsDashboard();
        loadRepairFindings();
        updateRepairStatus();
    } catch (error) {
        console.error('Error clearing findings:', error);
        showToast('Error clearing findings', 'error');
    }
}

async function loadRepairHistory() {
    const container = document.getElementById('repair-history-list');
    if (!container) return;

    try {
        const response = await fetch('/api/repair/history?limit=50');
        if (!response.ok) throw new Error('Failed to fetch history');
        const data = await response.json();
        const runs = data.runs || [];

        if (runs.length === 0) {
            container.innerHTML = `<div class="repair-empty-state">
                <div class="repair-empty-icon">&#128337;</div>
                <div class="repair-empty-title">No History Yet</div>
                <div class="repair-empty-text">Job run history will appear here after maintenance jobs complete their first scan.</div>
            </div>`;
            return;
        }

        container.innerHTML = runs.map(run => {
            const duration = run.duration_seconds ? `${run.duration_seconds.toFixed(1)}s` : '-';
            const age = formatCacheAge(run.started_at);
            const statusClass = run.status === 'completed' ? 'success' :
                run.status === 'failed' ? 'error' : 'running';

            // Build stat pills
            const stats = [];
            stats.push(`<span class="repair-history-stat"><strong>${(run.items_scanned || 0).toLocaleString()}</strong> scanned</span>`);
            if (run.findings_created) stats.push(`<span class="repair-history-stat findings"><strong>${run.findings_created}</strong> findings</span>`);
            if (run.auto_fixed) stats.push(`<span class="repair-history-stat fixed"><strong>${run.auto_fixed}</strong> fixed</span>`);
            if (run.errors) stats.push(`<span class="repair-history-stat errors"><strong>${run.errors}</strong> errors</span>`);

            // Format timestamps
            const startTime = run.started_at ? new Date(run.started_at).toLocaleString() : '-';
            const endTime = run.finished_at ? new Date(run.finished_at).toLocaleString() : 'In progress';

            return `<div class="repair-history-entry">
                <div class="repair-history-header">
                    <div class="repair-history-dot ${statusClass}"></div>
                    <span class="repair-history-name">${run.display_name || run.job_id}</span>
                    <span class="repair-history-status ${statusClass}">${run.status}</span>
                    <span class="repair-history-duration">${duration}</span>
                </div>
                <div class="repair-history-stats">${stats.join('')}</div>
                <div class="repair-history-meta">${age} &middot; ${startTime} &rarr; ${endTime}</div>
            </div>`;
        }).join('');

    } catch (error) {
        console.error('Error loading repair history:', error);
        container.innerHTML = '<div class="repair-empty">Error loading history</div>';
    }
}

// Initialize Repair Worker UI on page load
if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', () => {
        const button = document.getElementById('repair-button');
        if (button) {
            button.addEventListener('click', openRepairModal);
            updateRepairStatus();
            setInterval(updateRepairStatus, 5000);
        }
    });
} else {
    const button = document.getElementById('repair-button');
    if (button) {
        button.addEventListener('click', openRepairModal);
        updateRepairStatus();
        setInterval(updateRepairStatus, 5000);
    }
}

// ===================================================================

