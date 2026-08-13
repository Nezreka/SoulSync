// Library globals — the cross-file contract that outlived library.js.
//
// When the library + artist-detail pages went React, everything page-shaped
// moved to webui/src and library.js was deleted. These few pieces stayed
// vanilla because OTHER classic scripts and the React shell call them by
// global name:
//
//   - artistDetailPageState / the back-label stack — the shared state spine.
//     React syncs into the SAME objects (vanilla-state.ts); stats-automations
//     reads them for Artist Radio and the report-issue modal.
//   - navigateToArtistDetail — init.js, enrichment.js and the shell bridge
//     route through it; it owns the label-stack push/pop semantics.
//   - playLibraryTrack — downloads.js, enrichment.js, stats-automations.js and
//     the shell bridge all start library playback through it.
//   - _updateSidebarLibraryBreadcrumb — init.js repaints the nav breadcrumb.
//   - _handoffLibrarySearchToEnhancedSearch — the React library and label
//     pages hand a query off to the search page with it.
//
// Everything here is moved VERBATIM from library.js (one documented exception
// inside navigateToArtistDetail). Depends on core.js (PAGE_WILL_CHANGE_EVENT,
// currentPage, navigateToPage) and media-player.js globals, both loaded first.

function _handoffLibrarySearchToEnhancedSearch(query) {
    if (typeof navigateToPage !== 'function') return;
    navigateToPage('search');
    setTimeout(() => {
        const input = document.getElementById('enhanced-search-input');
        if (input && query) {
            input.value = query;
            input.dispatchEvent(new Event('input', { bubbles: true }));
        }
    }, 300);
}

// ===============================================
// Artist Detail Page Functions
// ===============================================

// Artist detail page state
const _ARTIST_DETAIL_BACK_LABELS = {
    library: 'Back to Library',
    search: 'Back to Search',
    discover: 'Back to Discover',
    watchlist: 'Back to Watchlist',
    wishlist: 'Back to Wishlist',
    stats: 'Back to Stats',
    'playlist-explorer': 'Back to Explorer',
    automations: 'Back to Automations',
    dashboard: 'Back to Dashboard',
    sync: 'Back to Sync',
    'active-downloads': 'Back to Downloads',
};

// Stack of origins for the back-button label. Each entry: {type:'page', pageId}
// or {type:'artist', name}. Pushed on forward navigation, popped on back.
// Separate from browser history — only used for the label display.
let _artistDetailLabelStack = [];
let _artistDetailGoingBack = false;

// Exported for the React artist-detail page, which renders the back button now.
// Arrivals from a still-vanilla page (search, label detail, enrichment) push
// onto this stack here, so React has to read the SAME array to label the button
// "Back to Search" rather than a bare "Back".
if (typeof window !== 'undefined') {
    window.artistDetailBackLabels = _ARTIST_DETAIL_BACK_LABELS;
    window.artistDetailLabelStack = _artistDetailLabelStack;
}

let artistDetailPageState = {
    isInitialized: false,
    currentArtistId: null,
    currentArtistName: null,
    currentArtistSource: null,
    enhancedView: false,
    enhancedData: null,
    expandedAlbums: new Set(),
    selectedTracks: new Set(),
    editingCell: null,
    enhancedTrackSort: {}
};

// Exported for the React artist-detail page. `let` at the top level of a
// classic script is a global LEXICAL binding and never lands on window, so a
// module cannot see it — yet a dozen functions here (playArtistRadio,
// openArtistArtPicker, openDiscographyModal, deleteLibraryAlbum, runEnrichment,
// …) read this object when the React page invokes them. Same object, not a
// copy, so both sides stay in step.
if (typeof window !== 'undefined') window.artistDetailPageState = artistDetailPageState;

function clearArtistDetailPageState() {
    if (artistDetailPageState.completionController) {
        artistDetailPageState.completionController.abort();
        artistDetailPageState.completionController = null;
    }

    artistDetailPageState.currentArtistId = null;
    artistDetailPageState.currentArtistName = null;
    artistDetailPageState.currentArtistSource = null;
}

if (typeof window !== 'undefined') {
    window.addEventListener(PAGE_WILL_CHANGE_EVENT, (event) => {
        const detail = event.detail || {};
        if (detail.fromPageId === 'artist-detail' && detail.toPageId !== 'artist-detail') {
            clearArtistDetailPageState();
        }
    });
}


// Maximum visible characters of an artist name in the sidebar Library
// breadcrumb. Names longer than this get truncated with an ellipsis so the
// nav button width stays consistent across the rest of the sidebar.
const _SIDEBAR_BREADCRUMB_ARTIST_MAXLEN = 14;


function _updateSidebarLibraryBreadcrumb() {
    // Rewrite the Library nav button label between plain "Library" and a
    // "Library / <Artist>" breadcrumb depending on whether the user is on
    // the artist-detail pseudo-page. Pure visual — touches no app state.
    const btn = document.querySelector('[data-page="library"]');
    if (!btn) return;
    const textEl = btn.querySelector('.nav-text');
    if (!textEl) return;

    const onArtistDetail = (typeof currentPage === 'string' && currentPage === 'artist-detail');
    const artistName = onArtistDetail ? (artistDetailPageState.currentArtistName || '') : '';

    if (!onArtistDetail || !artistName) {
        // Default state: plain "Library" label. Use textContent so we wipe
        // any previously-injected breadcrumb spans cleanly.
        textEl.textContent = 'Library';
        textEl.removeAttribute('data-breadcrumb');
        return;
    }

    // Truncate long names so the button width stays consistent.
    let display = artistName;
    if (display.length > _SIDEBAR_BREADCRUMB_ARTIST_MAXLEN) {
        display = display.slice(0, _SIDEBAR_BREADCRUMB_ARTIST_MAXLEN - 1).trimEnd() + '…';
    }

    // Render via inline spans so CSS can style the root / separator / context
    // independently. Escape via textContent on individual spans.
    textEl.setAttribute('data-breadcrumb', '1');
    textEl.textContent = '';
    const root = document.createElement('span');
    root.className = 'nav-text-root';
    root.textContent = 'Library';
    const sep = document.createElement('span');
    sep.className = 'nav-text-sep';
    sep.textContent = ' / ';
    const ctx = document.createElement('span');
    ctx.className = 'nav-text-context';
    ctx.textContent = display;
    ctx.title = artistName;  // full name on hover
    textEl.appendChild(root);
    textEl.appendChild(sep);
    textEl.appendChild(ctx);
}

// Expose so init.js navigateToPage can call it without a circular import.
if (typeof window !== 'undefined') {
    window._updateSidebarLibraryBreadcrumb = _updateSidebarLibraryBreadcrumb;
}


function navigateToArtistDetail(artistId, artistName, sourceOverride = null, options = {}) {
    const normalizedSource = sourceOverride || null;

    // Skip reload if already on this exact artist/source (prevents double-fetch
    // when the router fires activateLegacyPath after navigating to an
    // /artist-detail/:source/:id URL).
    if (artistId &&
            String(artistId) === String(artistDetailPageState.currentArtistId) &&
            String(normalizedSource || '') === String(artistDetailPageState.currentArtistSource || '')) {
        if (currentPage !== 'artist-detail') {
            navigateToPage('artist-detail', {
                artistId,
                artistSource: normalizedSource,
                skipRouteChange: options.skipRouteChange === true
            });
        }
        return;
    }
    console.log(`🎵 Navigating to artist detail: ${artistName} (ID: ${artistId}${sourceOverride ? `, source: ${sourceOverride}` : ''})`);

    // Maintain the label stack. Back navigations pop; forward navigations push.
    // Only treat the flag as a back-nav signal when we're still on artist-detail —
    // if history.back() landed on a non-artist page first, the flag is stale.
    if (_artistDetailGoingBack && currentPage === 'artist-detail') {
        _artistDetailLabelStack.pop();
        _artistDetailGoingBack = false;
    } else {
        _artistDetailGoingBack = false; // clear any stale flag
        if (currentPage !== 'artist-detail') {
            // Cleared IN PLACE, not reassigned: window.artistDetailLabelStack
            // holds this same array for the React page, and swapping the
            // binding would leave React reading a detached copy.
            _artistDetailLabelStack.length = 0; // fresh chain from a non-artist page
        }
        if (currentPage === 'artist-detail' && artistDetailPageState.currentArtistName) {
            _artistDetailLabelStack.push({ type: 'artist', name: artistDetailPageState.currentArtistName });
        } else {
            const pageId = (typeof currentPage === 'string' && currentPage && currentPage !== 'artist-detail')
                ? currentPage : 'library';
            _artistDetailLabelStack.push({ type: 'page', pageId });
        }
    }

    // Abort any in-progress completion stream
    if (artistDetailPageState.completionController) {
        artistDetailPageState.completionController.abort();
        artistDetailPageState.completionController = null;
    }

    // The vanilla cancelled its inline edit and removed the manual-match
    // overlay here. Both are React-owned now: editingCell is never set, and
    // #enhanced-manual-match-overlay is a React-rendered node that must not be
    // removed out from under its owner — the route change unmounts it.

    // Store current artist info and reset enhanced view state
    artistDetailPageState.currentArtistId = artistId;
    artistDetailPageState.currentArtistName = artistName;
    artistDetailPageState.currentArtistSource = normalizedSource;
    artistDetailPageState.enhancedData = null;
    artistDetailPageState.expandedAlbums = new Set();
    // Cleared IN PLACE: React mirrors its selection into this same Set, and the
    // vanilla track-delete path deletes from it. Swapping the object out would
    // leave both writing somewhere nobody reads.
    artistDetailPageState.selectedTracks.clear();
    artistDetailPageState.enhancedTrackSort = {};
    artistDetailPageState.enhancedView = false;

    // Hand off. React owns this route outright now — this function's remaining
    // job is the state written above, which a dozen globals over in
    // stats-automations.js and the Enhanced modals read back out.
    //
    // What used to follow here (resetting the legacy view chrome, then
    // initializeArtistDetailPage + loadArtistDetailData) rendered the vanilla
    // page on top of the React one: both target the same ids, so
    // applyDiscographyFilters hid React's .release-card elements using legacy
    // filter state and the discography vanished. It ran behind a manifest
    // check; with the vanilla page deleted there is nothing left to run.
    navigateToPage('artist-detail', {
        artistId,
        artistSource: normalizedSource,
        skipRouteChange: options.skipRouteChange === true
    });
}


async function playLibraryTrack(track, albumTitle, artistName) {
    if (!track.file_path) {
        showToast('No file available for this track', 'error');
        return;
    }

    // Library tracks have authoritative metadata in the SoulSync DB —
    // any title / artist / album the caller passes in is downstream of
    // whatever modal triggered playback and may carry noise like the
    // ``<source_id>||<display>`` filename prefix from a Prowlarr result.
    // When the caller has a track.id, fetch the canonical row from
    // resolve-track and overwrite the caller-supplied fields with the
    // DB values. Falls back silently to the caller-supplied values on
    // any error so we never lose the play action over a metadata fetch.
    if (track.id && (track.title || track.name) && (artistName || track.artist_name)) {
        try {
            const _dbResp = await fetch('/api/stats/resolve-track', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    title: track.title || track.name,
                    artist: artistName || track.artist_name || '',
                }),
            });
            const _dbData = await _dbResp.json();
            if (_dbData && _dbData.success && _dbData.track) {
                const _row = _dbData.track;
                track = {
                    ...track,
                    id: _row.id ?? track.id,
                    title: _row.title || track.title,
                    file_path: _row.file_path || track.file_path,
                    bitrate: _row.bitrate ?? track.bitrate,
                    artist_id: _row.artist_id ?? track.artist_id,
                    album_id: _row.album_id ?? track.album_id,
                    _stats_image: _row.image_url || _row.album_thumb_url || track._stats_image || null,
                };
                if (_row.album_title) albumTitle = _row.album_title;
                if (_row.artist_name) artistName = _row.artist_name;
            }
        } catch (_dbErr) {
            console.debug('library track DB refresh skipped:', _dbErr);
        }
    }

    try {
        // Stop any current playback first
        if (audioPlayer && !audioPlayer.paused) {
            audioPlayer.pause();
        }

        // Get album art from enhanced data if available
        let albumArt = null;
        if (artistDetailPageState.enhancedData) {
            const albums = artistDetailPageState.enhancedData.albums || [];
            for (const a of albums) {
                if ((a.tracks || []).some(t => t.id === track.id)) {
                    albumArt = a.thumb_url;
                    break;
                }
            }
            if (!albumArt) albumArt = artistDetailPageState.enhancedData.artist?.thumb_url;
        }
        if (!albumArt && track._stats_image) albumArt = track._stats_image;

        // Set track info in the media player UI
        setTrackInfo({
            title: track.title || 'Unknown Track',
            artist: artistName || 'Unknown Artist',
            album: albumTitle || 'Unknown Album',
            filename: track.file_path,
            is_library: true,
            image_url: albumArt,
            id: track.id,
            artist_id: track.artist_id,
            album_id: track.album_id,
            bitrate: track.bitrate,
            sample_rate: track.sample_rate
        });

        // Show loading state
        showLoadingAnimation();
        const loadingText = document.querySelector('.loading-text');
        if (loadingText) {
            loadingText.textContent = 'Loading library track...';
        }

        // POST to library play endpoint
        const response = await fetch('/api/library/play', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                file_path: track.file_path,
                title: track.title || '',
                artist: artistName || '',
                album: albumTitle || '',
                // Server song id so playback can stream via the media server
                // when the file isn't on SoulSync's disk (#809).
                track_id: track.id || null
            })
        });

        const result = await response.json();
        if (!result.success) {
            // File not on disk — fall back to streaming from configured source
            console.warn('Library file not found, falling back to stream source');
            hideLoadingAnimation();
            const streamRes = await fetch('/api/enhanced-search/stream-track', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    track_name: track.title || '',
                    artist_name: artistName || '',
                    album_name: albumTitle || '',
                })
            });
            const streamData = await streamRes.json();
            if (streamData.success && streamData.result) {
                streamData.result.artist = artistName;
                streamData.result.title = track.title;
                streamData.result.album = albumTitle;
                streamData.result.image_url = track._stats_image || null;
                startStream(streamData.result);
                return;
            }
            throw new Error(result.error || 'Failed to start library playback');
        }

        // Re-apply repeat-one loop property
        if (audioPlayer) audioPlayer.loop = (npRepeatMode === 'one');
        // Stream state is already "ready" — start audio playback directly
        await startAudioPlayback();

    } catch (error) {
        console.error('Library playback error:', error);
        showToast(`Playback error: ${error.message}`, 'error');
        hideLoadingAnimation();
        clearTrack();
    }
}

