// SEARCH FUNCTIONALITY
// ===============================
// `enhancedSearchFetch`, `SOURCE_LABELS`, and `renderCompactSection` live in
// shared-helpers.js so the Search page and the global widget share the same
// implementations.

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

// ===============================
// SEARCH MODE TOGGLE
// ===============================

let searchModeToggleInitialized = false;
// Set by the closure on first init; called by subsequent invocations to
// re-display the search dropdown from the controller's cached state.
// Solves the "results vanish on navigate-back" UX issue — a sidebar nav
// click is treated as outside-click and dismisses the dropdown, so when
// the user returns to /search we need to re-render whatever was cached.
let _searchPageRestoreOnEnter = null;
// Exposed so the global-search widget's Soulseek handoff can sync the
// controller's state.query to the widget's query before clicking the
// Soulseek icon — otherwise onSoulseekSelected fires with whatever the
// user last typed on /search and overwrites the basic input.
let _searchPageController = null;

function initializeSearchModeToggle() {
    // Subsequent invocations: just re-display cached results so they don't
    // vanish on navigate-back. Skip the duplicate-listener setup.
    if (searchModeToggleInitialized) {
        if (_searchPageRestoreOnEnter) _searchPageRestoreOnEnter();
        return;
    }

    const sourceRow = document.getElementById('enh-source-row');
    const basicSection = document.getElementById('basic-search-section');
    const enhancedSection = document.getElementById('enhanced-search-section');

    if (!sourceRow || !basicSection || !enhancedSection) {
        console.warn('Search source picker elements not found');
        return;
    }

    searchModeToggleInitialized = true;
    console.log('✅ Initializing search source picker (first time only)');

    // State + fetch dispatch + icon-row rendering live in the shared
    // `createSearchController` factory (shared-helpers.js) so this page and
    // the global search widget share one implementation. This closure wires
    // the controller up with Search-page-specific DOM + callbacks.

    const enhancedInput = document.getElementById('enhanced-search-input');
    const enhancedCancelBtn = document.getElementById('enhanced-cancel-btn');
    const enhancedDropdown = document.getElementById('enhanced-dropdown');
    const loadingState = document.getElementById('enhanced-loading');
    const emptyState = document.getElementById('enhanced-empty');
    const resultsContainer = document.getElementById('enhanced-results-container');

    let debounceTimer = null;

    // ── Fallback banner ("Spotify unavailable — showing Deezer") ───────
    function _renderFallbackBanner(state) {
        const banner = document.getElementById('enh-fallback-banner');
        if (!banner) return;
        const src = state.activeSource;
        const actual = state.fallbacks[src];
        if (actual && actual !== src) {
            const clicked = (SOURCE_LABELS[src] || {}).text || src;
            const served = (SOURCE_LABELS[actual] || {}).text || actual;
            banner.textContent = `${clicked} unavailable — showing ${served}.`;
            banner.classList.remove('hidden');
        } else {
            banner.classList.add('hidden');
        }
    }

    // Central re-render callback — called by the controller whenever state
    // changes (cache hit, fetch settle, query reset). Drives the enhanced
    // dropdown UI: loading state, empty state, results render, fallback
    // banner.
    function _renderFromState(state) {
        const src = state.activeSource;

        // Soulseek has its own surface (basic-section) — the controller fires
        // onSoulseekSelected for that, so there's nothing to render here.
        if (src === 'soulseek') return;

        // Ensure the enhanced section is visible (may have been hidden if the
        // user was previously on Soulseek).
        basicSection.classList.remove('active');
        enhancedSection.classList.add('active');

        _renderFallbackBanner(state);

        const cached = state.sources[src];
        const loading = state.loadingSources.has(src);

        // Mid-fetch with no cache yet → loading state.
        if (loading && !cached) {
            emptyState.classList.add('hidden');
            resultsContainer.classList.add('hidden');
            loadingState.classList.remove('hidden');
            const loadingText = document.getElementById('enhanced-loading-text');
            if (loadingText) {
                const info = SOURCE_LABELS[src];
                loadingText.textContent = `Searching ${(info && info.text) || src} and your library...`;
            }
            showDropdown();
            return;
        }

        // No cache + no query → nothing to show; hide the dropdown.
        if (!cached) {
            if (!state.query) {
                hideDropdown();
                return;
            }
            // Fetch settled with no data — empty state.
            loadingState.classList.add('hidden');
            resultsContainer.classList.add('hidden');
            emptyState.classList.remove('hidden');
            showDropdown();
            return;
        }

        const total = src === 'youtube_videos'
            ? ((cached.videos && cached.videos.length) || 0)
            : ((cached.db_artists && cached.db_artists.length) || 0)
              + ((cached.artists && cached.artists.length) || 0)
              + ((cached.albums && cached.albums.length) || 0)
              + ((cached.tracks && cached.tracks.length) || 0);

        loadingState.classList.add('hidden');

        if (total === 0) {
            resultsContainer.classList.add('hidden');
            emptyState.classList.remove('hidden');
            showDropdown();
            return;
        }

        emptyState.classList.add('hidden');
        resultsContainer.classList.remove('hidden');
        showDropdown();

        if (src === 'youtube_videos') {
            ['enh-db-artists-section', 'enh-spotify-artists-section', 'enh-albums-section', 'enh-singles-section', 'enh-tracks-section'].forEach(id => {
                const el = document.getElementById(id);
                if (el) el.classList.add('hidden');
            });
            const artistsWrapper = document.querySelector('.enh-artists-wrapper');
            if (artistsWrapper) artistsWrapper.style.display = 'none';
            _renderVideoResults(cached.videos || []);
            return;
        }

        const videosSec = document.getElementById('enh-videos-section');
        if (videosSec) videosSec.classList.add('hidden');
        const artistsWrapper = document.querySelector('.enh-artists-wrapper');
        if (artistsWrapper) artistsWrapper.style.display = '';

        renderDropdownResults({
            db_artists: cached.db_artists || [],
            spotify_artists: cached.artists || [],
            spotify_albums: cached.albums || [],
            spotify_tracks: cached.tracks || [],
            metadata_source: src,
        });
    }

    const searchController = createSearchController({
        sourceRowElement: sourceRow,
        iconClassPrefix: 'enh',
        onStateChange: _renderFromState,
        onSoulseekSelected: (query) => {
            // Soulseek returns raw file results, rendered by the basic-search
            // UI — swap sections and re-fire the basic search with the
            // current query.
            basicSection.classList.add('active');
            enhancedSection.classList.remove('active');
            hideDropdown();
            const basicInput = document.getElementById('downloads-search-input');
            if (basicInput) {
                if (query) basicInput.value = query;
                if (basicInput.value && typeof performDownloadsSearch === 'function') {
                    performDownloadsSearch();
                }
            }
        },
    });
    searchController.init();
    _searchPageController = searchController;

    // Expose a re-render hook so navigate-back to /search restores cached
    // results instead of leaving the dropdown hidden. Deferred to the next
    // tick so the render happens AFTER the nav-button click finishes
    // bubbling to the document outside-click handler — otherwise that
    // handler sees the just-shown dropdown and immediately dismisses it.
    _searchPageRestoreOnEnter = () => {
        if (!searchController.state.query) return;
        setTimeout(() => _renderFromState(searchController.state), 0);
    };

    // Live search with debouncing
    if (enhancedInput) {
        enhancedInput.addEventListener('input', (e) => {
            const query = e.target.value.trim();

            // Show/hide cancel button
            if (enhancedCancelBtn) {
                enhancedCancelBtn.classList.toggle('hidden', query.length === 0);
            }

            // Clear debounce timer
            clearTimeout(debounceTimer);

            // Hide dropdown if query too short
            if (query.length < 2) {
                hideDropdown();
                return;
            }

            // Debounce search
            debounceTimer = setTimeout(() => {
                searchController.submitQuery(query);
            }, 300);
        });

        enhancedInput.addEventListener('keypress', (e) => {
            if (e.key === 'Enter') {
                const query = e.target.value.trim();
                if (query.length >= 2) {
                    clearTimeout(debounceTimer);
                    searchController.submitQuery(query);
                }
            }
        });
    }

    if (enhancedCancelBtn) {
        enhancedCancelBtn.addEventListener('click', () => {
            enhancedInput.value = '';
            enhancedCancelBtn.classList.add('hidden');
            hideDropdown();
        });
    }

    // Close button inside dropdown (mobile)
    const dropdownCloseBtn = document.getElementById('enhanced-dropdown-close');
    if (dropdownCloseBtn) {
        dropdownCloseBtn.addEventListener('click', (e) => {
            e.stopPropagation();
            hideDropdown();
        });
    }

    // Close dropdown when clicking outside
    document.addEventListener('click', (e) => {
        const dropdown = document.getElementById('enhanced-dropdown');
        if (dropdown && !dropdown.classList.contains('hidden')) {
            const isClickInside = e.target.closest('.enhanced-search-input-wrapper');
            // Source icons live above the input, outside the dropdown — they
            // control which cached source is shown, so don't dismiss when the
            // user clicks them.
            const isClickOnSourceRow = e.target.closest('#enh-source-row');
            // Modal sits above the dropdown; closing it shouldn't dismiss results.
            const isClickInModal = e.target.closest('.download-missing-modal');
            if (!isClickInside && !isClickOnSourceRow && !isClickInModal) {
                hideDropdown();
            }
        }
    });

    function renderDropdownResults(data) {
        const activeSource = searchController.state.activeSource;

        // Music Videos tab — don't render regular sections
        if (activeSource === 'youtube_videos') return;

        // Determine source badge from active tab (not just primary)
        const displaySource = activeSource || data.metadata_source || 'spotify';
        const sourceInfo = SOURCE_LABELS[displaySource] || SOURCE_LABELS.spotify;
        const sourceBadge = { text: sourceInfo.text, class: sourceInfo.badgeClass };

        // Render DB Artists
        renderCompactSection(
            'enh-db-artists-section',
            'enh-db-artists-list',
            'enh-db-artists-count',
            data.db_artists || [],
            (artist) => ({
                image: artist.image_url,
                placeholder: '📚',
                name: artist.name,
                meta: 'In Your Library',
                badge: { text: 'Library', class: 'enh-badge-library' },
                onClick: () => {
                    console.log(`🎵 Opening library artist detail: ${artist.name} (ID: ${artist.id})`);
                    hideDropdown();
                    navigateToArtistDetail(artist.id, artist.name);
                }
            })
        );

        // Render Artists (source-aware badge)
        renderCompactSection(
            'enh-spotify-artists-section',
            'enh-spotify-artists-list',
            'enh-spotify-artists-count',
            data.spotify_artists || [],
            (artist) => ({
                image: artist.image_url,
                placeholder: '🎤',
                name: artist.name,
                meta: 'Artist',
                badge: sourceBadge,
                onClick: () => {
                    const sourceOverride = searchController.state.activeSource;
                    console.log(`🎵 Opening artist detail: ${artist.name} (ID: ${artist.id}, source: ${sourceOverride})`);
                    hideDropdown();
                    navigateToArtistDetail(artist.id, artist.name, sourceOverride || null);
                }
            })
        );

        // Split albums from singles/EPs (albums is the catch-all for unknown types)
        const allAlbums = data.spotify_albums || [];
        const singlesAndEPs = allAlbums.filter(a => a.album_type === 'single' || a.album_type === 'ep');
        const albums = allAlbums.filter(a => a.album_type !== 'single' && a.album_type !== 'ep');

        // Render Albums
        renderCompactSection(
            'enh-albums-section',
            'enh-albums-list',
            'enh-albums-count',
            albums,
            (album) => ({
                image: album.image_url,
                placeholder: '💿',
                name: album.name,
                meta: `${album.artist} • ${album.release_date ? album.release_date.substring(0, 4) : 'N/A'}`,
                onClick: () => handleEnhancedSearchAlbumClick(album)
            })
        );

        // Render Singles & EPs
        renderCompactSection(
            'enh-singles-section',
            'enh-singles-list',
            'enh-singles-count',
            singlesAndEPs,
            (album) => ({
                image: album.image_url,
                placeholder: '🎶',
                name: album.name,
                meta: `${album.artist} • ${album.release_date ? album.release_date.substring(0, 4) : 'N/A'}`,
                onClick: () => handleEnhancedSearchAlbumClick(album)
            })
        );

        // Render Tracks
        renderCompactSection(
            'enh-tracks-section',
            'enh-tracks-list',
            'enh-tracks-count',
            data.spotify_tracks || [],
            (track) => {
                const duration = formatDuration(track.duration_ms);
                return {
                    image: track.image_url,
                    placeholder: '🎵',
                    name: track.name,
                    meta: `${track.artist} • ${track.album}`,
                    duration: duration,
                    onClick: () => handleEnhancedSearchTrackClick(track),
                    onPlay: () => streamEnhancedSearchTrack(track)
                };
            }
        );

        // Lazy load artist images that are missing
        lazyLoadEnhancedSearchArtistImages();

        // Async library ownership check — doesn't block rendering
        _checkSearchResultsLibraryOwnership(data);
    }

    async function _checkSearchResultsLibraryOwnership(data) {
        try {
            const allAlbums = data.spotify_albums || [];
            const allTracks = data.spotify_tracks || [];
            if (!allAlbums.length && !allTracks.length) return;

            const resp = await fetch('/api/enhanced-search/library-check', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    albums: allAlbums.map(a => ({ name: a.name, artist: a.artist })),
                    tracks: allTracks.map(t => ({ name: t.name, artist: t.artist })),
                }),
            });
            const result = await resp.json();

            // Tag album cards with staggered animation
            const albumCards = document.querySelectorAll('#enh-albums-list .enh-compact-item, #enh-singles-list .enh-compact-item');
            const albumResults = result.albums || [];
            let delay = 0;
            albumCards.forEach((card, i) => {
                if (albumResults[i]) {
                    setTimeout(() => {
                        const badge = document.createElement('div');
                        badge.className = 'enh-item-lib-badge';
                        badge.textContent = 'In Library';
                        card.appendChild(badge);
                    }, delay);
                    delay += 30;
                }
            });

            // Tag track rows + wire up library playback
            const trackCards = document.querySelectorAll('#enh-tracks-list .enh-compact-item');
            const trackResults = result.tracks || [];
            trackCards.forEach((card, i) => {
                const tr = trackResults[i];
                if (tr && tr.in_library) {
                    setTimeout(() => {
                        const badge = document.createElement('div');
                        badge.className = 'enh-item-lib-badge';
                        badge.textContent = 'In Library';
                        card.appendChild(badge);

                        // Replace stream button to play from library instead of searching
                        if (tr.file_path) {
                            const playBtn = card.querySelector('.enh-item-play-btn');
                            if (playBtn) {
                                const newBtn = playBtn.cloneNode(true);
                                newBtn.title = 'Play from library';
                                newBtn.textContent = '▶';
                                const trackInfo = tr;
                                newBtn.addEventListener('click', (e) => {
                                    e.stopPropagation();
                                    playLibraryTrack(
                                        { id: trackInfo.track_id, title: trackInfo.title, file_path: trackInfo.file_path, _stats_image: trackInfo.album_thumb_url || null },
                                        trackInfo.album_title || '',
                                        trackInfo.artist_name || ''
                                    );
                                });
                                playBtn.replaceWith(newBtn);
                            }
                        }
                    }, delay);
                    delay += 30;
                } else if (tr && tr.in_wishlist) {
                    setTimeout(() => {
                        if (!card.querySelector('.enh-item-wishlist-badge')) {
                            const badge = document.createElement('div');
                            badge.className = 'enh-item-wishlist-badge';
                            badge.textContent = 'In Wishlist';
                            card.appendChild(badge);
                        }
                    }, delay);
                    delay += 30;
                }
            });
        } catch (e) {
            console.debug('Library check failed:', e);
        }
    }

    function _renderVideoResults(videos) {
        let section = document.getElementById('enh-videos-section');
        if (!section) {
            // Create the section dynamically if it doesn't exist
            const container = document.getElementById('enhanced-results-container');
            if (!container) return;
            section = document.createElement('div');
            section.id = 'enh-videos-section';
            section.className = 'enh-dropdown-section';
            section.innerHTML = `
                <div class="enh-section-header">
                    <span class="enh-section-icon">🎬</span>
                    <h4 class="enh-section-title">Music Videos</h4>
                    <span class="enh-section-count" id="enh-videos-count">0</span>
                </div>
                <div class="enh-video-grid" id="enh-videos-list"></div>
            `;
            container.appendChild(section);
        }

        section.classList.remove('hidden');
        const countEl = document.getElementById('enh-videos-count');
        const listEl = document.getElementById('enh-videos-list');
        if (countEl) countEl.textContent = videos.length;

        if (!videos.length) {
            listEl.innerHTML = '<div class="enh-empty-state">No music videos found</div>';
            return;
        }

        listEl.innerHTML = videos.map(v => {
            const duration = v.duration ? `${Math.floor(v.duration / 60)}:${String(v.duration % 60).padStart(2, '0')}` : '';
            const views = v.view_count ? _formatViewCount(v.view_count) : '';
            return `
                <div class="enh-video-card" data-video-id="${v.video_id}" onclick="_downloadMusicVideo(this, ${JSON.stringify(v).replace(/"/g, '&quot;')})">
                    <div class="enh-video-thumb">
                        <img src="${v.thumbnail}" alt="" loading="lazy" onerror="this.style.display='none'">
                        <div class="enh-video-play">▶</div>
                        <div class="enh-video-progress-ring hidden">
                            <svg viewBox="0 0 36 36">
                                <circle class="enh-video-progress-bg" cx="18" cy="18" r="15.5" fill="none" stroke="rgba(255,255,255,0.15)" stroke-width="3"/>
                                <circle class="enh-video-progress-bar" cx="18" cy="18" r="15.5" fill="none" stroke="rgb(var(--accent-rgb))" stroke-width="3" stroke-dasharray="97.4" stroke-dashoffset="97.4" stroke-linecap="round" transform="rotate(-90 18 18)"/>
                            </svg>
                        </div>
                        <div class="enh-video-done hidden">✓</div>
                        <div class="enh-video-error hidden">✗</div>
                        ${duration ? `<span class="enh-video-duration">${duration}</span>` : ''}
                    </div>
                    <div class="enh-video-info">
                        <div class="enh-video-title" title="${v.title.replace(/"/g, '&quot;')}">${v.title}</div>
                        <div class="enh-video-channel">${v.channel}${views ? ` · ${views} views` : ''}</div>
                    </div>
                </div>
            `;
        }).join('');
    }

    function _formatViewCount(count) {
        if (count >= 1000000000) return `${(count / 1000000000).toFixed(1)}B`;
        if (count >= 1000000) return `${(count / 1000000).toFixed(1)}M`;
        if (count >= 1000) return `${(count / 1000).toFixed(1)}K`;
        return String(count);
    }

    // Lazy load artist images for enhanced search results
    async function lazyLoadEnhancedSearchArtistImages() {
        const artistLists = [
            document.getElementById('enh-db-artists-list'),
            document.getElementById('enh-spotify-artists-list')
        ];

        for (const list of artistLists) {
            if (!list) continue;

            const cardsNeedingImages = list.querySelectorAll('[data-needs-image="true"]');
            if (cardsNeedingImages.length === 0) continue;

            console.log(`🖼️ Lazy loading ${cardsNeedingImages.length} artist images in enhanced search`);

            for (const card of cardsNeedingImages) {
                const artistId = card.dataset.artistId;
                if (!artistId) continue;

                try {
                    const activeSource = searchController.state.activeSource;
                    // Pass the artist name so the backend can look up images
                    // for sources that don't store them (e.g. MusicBrainz —
                    // it only has MBIDs, not artist art, so the resolver
                    // falls back to iTunes/Deezer keyed by name).
                    const artistName = card.dataset.artistName || '';
                    const params = new URLSearchParams();
                    if (activeSource && activeSource !== 'spotify') params.set('source', activeSource);
                    if (artistName) params.set('name', artistName);
                    const qs = params.toString();
                    const imgUrl = `/api/artist/${artistId}/image${qs ? '?' + qs : ''}`;
                    const response = await fetch(imgUrl);
                    const data = await response.json();

                    if (data.success && data.image_url) {
                        // Find the placeholder and replace with image
                        const placeholder = card.querySelector('.enh-item-image-placeholder');
                        if (placeholder) {
                            const img = document.createElement('img');
                            img.src = data.image_url;
                            img.className = 'enh-item-image artist-image';
                            img.alt = card.querySelector('.enh-item-name')?.textContent || 'Artist';
                            placeholder.replaceWith(img);

                            // Apply dynamic glow
                            extractImageColors(data.image_url, (colors) => {
                                applyDynamicGlow(card, colors);
                            });
                        }
                        card.dataset.needsImage = 'false';
                        console.log(`✅ Loaded image for artist ${artistId}`);
                    }
                } catch (error) {
                    console.warn(`⚠️ Failed to load image for artist ${artistId}:`, error);
                }
            }
        }
    }

    function formatDuration(durationMs) {
        if (!durationMs) return '';
        const totalSeconds = Math.floor(durationMs / 1000);
        const minutes = Math.floor(totalSeconds / 60);
        const seconds = totalSeconds % 60;
        return `${minutes}:${seconds.toString().padStart(2, '0')}`;
    }

    // renderCompactSection now lives in shared-helpers.js.

    async function handleEnhancedSearchAlbumClick(album) {
        console.log(`💿 Enhanced search album clicked: ${album.name} by ${album.artist}`);

        showLoadingOverlay('Loading album...');

        try {
            // Fetch full album data with tracks — pass source for correct routing
            const albumParams = new URLSearchParams({ name: album.name || '', artist: album.artist || '' });
            const activeSource = searchController.state.activeSource;
            if (activeSource && activeSource !== 'spotify') {
                albumParams.set('source', activeSource);
            }
            // Pass Hydrabase plugin origin so server routes to correct client
            if (album.external_urls?.hydrabase_plugin) {
                albumParams.set('plugin', album.external_urls.hydrabase_plugin);
            }
            const response = await fetch(`/api/spotify/album/${album.id}?${albumParams}`);

            if (!response.ok) {
                if (response.status === 401) {
                    throw new Error('Spotify not authenticated. Please check your API settings.');
                }
                throw new Error(`Failed to load album: ${response.status}`);
            }

            const albumData = await response.json();

            if (!albumData || !albumData.tracks || albumData.tracks.length === 0) {
                hideLoadingOverlay();
                showToast(`No tracks available for "${album.name}". This release may have been delisted or is not available in your region.`, 'warning');
                return;
            }

            console.log(`✅ Loaded ${albumData.tracks.length} tracks for ${albumData.name}`);

            // Create virtual playlist ID for enhanced search albums
            const virtualPlaylistId = `enhanced_search_album_${album.id}`;

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

            // Enrich each track with full album object (needed for wishlist functionality)
            const enrichedTracks = albumData.tracks.map(track => ({
                ...track,
                album: {
                    name: albumData.name,
                    id: albumData.id,
                    album_type: albumData.album_type || 'album',
                    images: albumData.images || [],
                    release_date: albumData.release_date,
                    total_tracks: albumData.total_tracks
                }
            }));

            console.log(`📦 Enriched ${enrichedTracks.length} tracks with album metadata`);

            // Format playlist name
            const playlistName = `[${album.artist}] ${albumData.name}`;

            // Create artist object for the modal — extract ID from album data
            const firstArtist = (albumData.artists || [])[0] || {};
            const artistObject = {
                id: firstArtist.id || album.id?.split?.('_')?.[0] || '',
                name: firstArtist.name || album.artist,
                image_url: firstArtist.image_url || firstArtist.images?.[0]?.url || '',
                source: activeSource || '',
            };

            // Prepare full album object for modal
            const fullAlbumObject = {
                name: albumData.name,
                id: albumData.id,
                album_type: albumData.album_type || 'album',
                images: albumData.images || [],
                release_date: albumData.release_date,
                total_tracks: albumData.total_tracks,
                artists: albumData.artists || [{ name: album.artist }]
            };

            // Open download missing tracks modal
            await openDownloadMissingModalForArtistAlbum(
                virtualPlaylistId,
                playlistName,
                enrichedTracks,
                fullAlbumObject,
                artistObject,
                false // Don't show loading overlay, we already have one
            );

            // Register this download in search bubbles
            registerSearchDownload(
                {
                    id: album.id,
                    name: albumData.name,
                    artist: album.artist,
                    image_url: albumData.images?.[0]?.url || null,
                    images: albumData.images || []
                },
                'album',
                virtualPlaylistId,
                album.artist // artistName for grouping
            );

            hideLoadingOverlay();

        } catch (error) {
            hideLoadingOverlay();
            console.error('❌ Error handling enhanced search album click:', error);
            showToast(`Error opening album: ${error.message}`, 'error');
        }
    }

    async function streamEnhancedSearchTrack(track) {
        console.log(`▶️ Stream enhanced search track: ${track.name} by ${track.artist}`);

        showLoadingOverlay(`Searching for ${track.name}...`);

        try {
            // Send track metadata to backend for quick slskd search
            const response = await fetch('/api/enhanced-search/stream-track', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    track_name: track.name,
                    artist_name: track.artist,
                    album_name: track.album,
                    duration_ms: track.duration_ms
                })
            });

            if (!response.ok) {
                const errorData = await response.json();
                throw new Error(errorData.error || 'Failed to search for track');
            }

            const data = await response.json();

            if (!data.success || !data.result) {
                throw new Error('No suitable track found');
            }

            const slskdResult = data.result;

            // Check if audio format is supported (YouTube/Tidal use encoded filenames, skip check)
            const isStreamingSource = slskdResult.username === 'youtube' || slskdResult.username === 'tidal' || slskdResult.username === 'qobuz' || slskdResult.username === 'hifi';
            if (!isStreamingSource && slskdResult.filename && !isAudioFormatSupported(slskdResult.filename)) {
                const format = getFileExtension(slskdResult.filename);
                hideLoadingOverlay();
                showToast(`Sorry, ${format.toUpperCase()} format is not supported in your browser. Try downloading instead.`, 'error');
                return;
            }

            console.log(`✅ Found track to stream:`, slskdResult);
            console.log(`🎵 Track details - Username: ${slskdResult.username}, Filename: ${slskdResult.filename}`);

            hideLoadingOverlay();

            // Use existing startStream function to play the track
            console.log(`📡 Calling startStream() with result...`);
            await startStream(slskdResult);
            console.log(`✅ startStream() completed`);

        } catch (error) {
            hideLoadingOverlay();
            console.error('❌ Error streaming enhanced search track:', error);
            showToast(`Failed to stream track: ${error.message}`, 'error');
        }
    }

    async function handleEnhancedSearchTrackClick(track) {
        console.log(`🎵 Enhanced search track clicked: ${track.name} by ${track.artist}`);

        showLoadingOverlay('Loading track...');

        try {
            // Create virtual playlist ID for enhanced search tracks
            const virtualPlaylistId = `enhanced_search_track_${track.id}`;

            // Check if modal already exists and show it
            if (activeDownloadProcesses[virtualPlaylistId]) {
                console.log(`📱 Reopening existing modal for ${track.name}`);
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

            // Enrich track with album object (needed for wishlist functionality)
            const enrichedTrack = {
                id: track.id,
                name: track.name,
                artists: [track.artist], // Convert string to array for modal compatibility
                album: {
                    name: track.album,
                    id: null,
                    album_type: 'single',
                    images: track.image_url ? [{ url: track.image_url }] : [],
                    release_date: track.release_date || null,
                    total_tracks: 1
                },
                duration_ms: track.duration_ms,
                popularity: track.popularity || 0,
                preview_url: track.preview_url || null,
                external_urls: track.external_urls || null,
                image_url: track.image_url
            };

            console.log(`📦 Enriched track with album metadata`);

            // Format playlist name
            const playlistName = `${track.artist} - ${track.name}`;

            // Create minimal artist object for the modal
            const artistObject = {
                id: null,
                name: track.artist
            };

            // Prepare album object for modal (single track)
            const albumObject = {
                name: track.album,
                id: null,
                album_type: 'single',
                images: track.image_url ? [{ url: track.image_url }] : [],
                release_date: track.release_date || null,
                total_tracks: 1,
                artists: [{ name: track.artist }]
            };

            // Open download missing tracks modal with single track
            await openDownloadMissingModalForArtistAlbum(
                virtualPlaylistId,
                playlistName,
                [enrichedTrack], // Array with single track
                albumObject,
                artistObject,
                false
            );

            // Register this download in search bubbles
            registerSearchDownload(
                {
                    id: track.id,
                    name: track.name,
                    artist: track.artist,
                    image_url: track.image_url,
                    images: track.image_url ? [{ url: track.image_url }] : []
                },
                'track',
                virtualPlaylistId,
                track.artist // artistName for grouping
            );

            hideLoadingOverlay();

        } catch (error) {
            hideLoadingOverlay();
            console.error('❌ Error handling enhanced search track click:', error);
            showToast(`Error opening track: ${error.message}`, 'error');
        }
    }

    async function searchSlskdFor(type, item) {
        const mainResultsArea = document.getElementById('enhanced-main-results-area');
        if (!mainResultsArea) return;

        // Show loading in main results area
        mainResultsArea.innerHTML = `
            <div style="text-align: center; padding: 60px 20px; color: rgba(255,255,255,0.7);">
                <div style="width: 40px; height: 40px; margin: 0 auto 16px; border: 3px solid rgba(138,43,226,0.2); border-top-color: rgba(138,43,226,0.8); border-radius: 50%; animation: spin 1s linear infinite;"></div>
                <p>Searching for ${type === 'album' ? 'album' : 'track'}...</p>
            </div>
        `;

        const query = `${item.artist} ${item.name}`;

        try {
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

            // Filter results
            const filtered = data.results.filter(r => r.result_type === type);

            // Render slskd results in main area
            renderSlskdInMainArea(filtered, type, item);

        } catch (error) {
            console.error('Slskd search error:', error);
            showToast('Search failed', 'error');
            mainResultsArea.innerHTML = '<div class="search-results-placeholder"><p>Search failed. Please try again.</p></div>';
        }
    }

    function renderSlskdInMainArea(results, type, originalItem) {
        const mainResultsArea = document.getElementById('enhanced-main-results-area');
        if (!mainResultsArea) return;

        if (!results || results.length === 0) {
            mainResultsArea.innerHTML = '<div class="search-results-placeholder"><p>No matches found for this ' + type + '.</p></div>';
            return;
        }

        // Render results using same style as basic search
        mainResultsArea.innerHTML = results.map(result => {
            const title = type === 'album'
                ? `${result.album_title} (${result.tracks ? result.tracks.length : 0} tracks)`
                : result.title;

            return `
                <div class="result-card">
                    <div class="result-card-header">
                        <h4 class="result-title">${escapeHtml(title)}</h4>
                        <button class="download-result-btn" data-result='${JSON.stringify(result).replace(/'/g, "&#39;")}' data-type="${type}">
                            💾 Download
                        </button>
                    </div>
                    <div class="result-meta">
                        ${result.bitrate ? `<span class="meta-badge">${result.bitrate} kbps</span>` : ''}
                        ${result.format ? `<span class="meta-badge">${result.format.toUpperCase()}</span>` : ''}
                        ${result.size ? `<span class="meta-badge">${(result.size / 1024 / 1024).toFixed(1)} MB</span>` : ''}
                        ${result.username ? `<span class="meta-badge">👤 ${escapeHtml(result.username)}</span>` : ''}
                    </div>
                </div>
            `;
        }).join('');

        // Attach download handlers
        mainResultsArea.querySelectorAll('.download-result-btn').forEach(btn => {
            btn.addEventListener('click', async function () {
                const result = JSON.parse(this.dataset.result);
                const type = this.dataset.type;

                this.disabled = true;
                this.textContent = 'Downloading...';

                try {
                    const downloadData = type === 'album'
                        ? { result_type: 'album', tracks: result.tracks || [] }
                        : { result_type: 'track', username: result.username, filename: result.filename, size: result.size };

                    const response = await fetch('/api/download', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify(downloadData)
                    });

                    const data = await response.json();

                    if (data.error) {
                        showToast(`Download error: ${data.error}`, 'error');
                        this.disabled = false;
                        this.innerHTML = '💾 Download';
                    } else {
                        showToast('Download started!', 'success');
                        this.innerHTML = '✅ Added';
                    }
                } catch (error) {
                    console.error('Download error:', error);
                    showToast('Download failed', 'error');
                    this.disabled = false;
                    this.innerHTML = '💾 Download';
                }
            });
        });
    }

    function showDropdown() {
        const dropdown = document.getElementById('enhanced-dropdown');
        if (dropdown) dropdown.classList.remove('hidden');
        // Hide the page header + source picker to reclaim space
        const header = document.querySelector('#search-page .downloads-header');
        const modeToggle = document.querySelector('.search-source-picker-container');
        const slskdPlaceholder = document.querySelector('#enhanced-search-section .search-results-container');
        if (header) header.classList.add('enh-results-active-hide');
        if (modeToggle) modeToggle.classList.add('enh-results-active-hide');
        if (slskdPlaceholder) slskdPlaceholder.classList.add('enh-results-active-hide');
    }

    function hideDropdown() {
        const dropdown = document.getElementById('enhanced-dropdown');
        if (dropdown) dropdown.classList.add('hidden');
        // Restore hidden elements
        const header = document.querySelector('#search-page .downloads-header');
        const modeToggle = document.querySelector('.search-source-picker-container');
        const slskdPlaceholder = document.querySelector('#enhanced-search-section .search-results-container');
        if (header) header.classList.remove('enh-results-active-hide');
        if (modeToggle) modeToggle.classList.remove('enh-results-active-hide');
        if (slskdPlaceholder) slskdPlaceholder.classList.remove('enh-results-active-hide');
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

        // Load search bubble state
        await hydrateSearchBubblesFromSnapshot();

        // Load discover download state
        await hydrateDiscoverDownloadsFromSnapshot();

        // Navigate to user's home page (or dashboard for admin)
        const homePage = getProfileHomePage();
        const urlPage = _getPageFromPath();
        const targetPage = (urlPage && urlPage !== 'dashboard' && isPageAllowed(urlPage))
            ? urlPage
            : homePage;

        // Always apply the target page to the legacy shell chrome.
        const router = getWebRouter();
        const route = router?.routeManifest?.find((entry) => entry.pageId === targetPage);

        if (route?.kind === 'react') {
            showReactHost(targetPage);
            setActivePageChrome(targetPage);
            if (window.location.pathname !== route.path) {
                history.replaceState({ page: targetPage }, '', route.path);
            }
            return;
        }

        navigateToPage(targetPage, { skipRouteChange: true, forceReload: true });
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
