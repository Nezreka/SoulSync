// SEARCH FUNCTIONALITY
// ===============================

// Shared enhanced-search fetch used by the Search page and the global widget.
// Pass source to restrict results to a single metadata provider; omit or pass
// null/'auto' to let the backend fan out across all configured sources.
async function enhancedSearchFetch(query, { source = null, signal = null } = {}) {
    const body = { query };
    if (source && source !== 'auto') body.source = source;
    const res = await fetch('/api/enhanced-search', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
        signal: signal || undefined,
    });
    if (!res.ok) throw new Error(`Enhanced search failed: ${res.status}`);
    return res.json();
}

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

function initializeSearchModeToggle() {
    // Only initialize once to prevent duplicate event listeners
    if (searchModeToggleInitialized) {
        console.log('Search mode toggle already initialized, skipping...');
        return;
    }

    const sourceSelect = document.getElementById('search-source-select');
    const basicSection = document.getElementById('basic-search-section');
    const enhancedSection = document.getElementById('enhanced-search-section');

    if (!sourceSelect || !basicSection || !enhancedSection) {
        console.warn('Search source picker elements not found');
        return;
    }

    searchModeToggleInitialized = true;
    console.log('✅ Initializing search source picker (first time only)');

    // Current source selection — 'auto' (fan-out) by default. Soulseek routes
    // to the raw-file basic search; everything else routes to enhanced.
    let currentSearchSource = sourceSelect.value || 'auto';

    const applySourceSelection = (value) => {
        currentSearchSource = value;
        if (value === 'soulseek') {
            basicSection.classList.add('active');
            enhancedSection.classList.remove('active');
        } else {
            basicSection.classList.remove('active');
            enhancedSection.classList.add('active');
        }
    };

    applySourceSelection(currentSearchSource);

    sourceSelect.addEventListener('change', (e) => {
        applySourceSelection(e.target.value);
        console.log('Search source →', currentSearchSource);
    });

    // Initialize enhanced search
    const enhancedInput = document.getElementById('enhanced-search-input');
    const enhancedSearchBtn = document.getElementById('enhanced-search-btn');
    const enhancedCancelBtn = document.getElementById('enhanced-cancel-btn');
    const enhancedDropdown = document.getElementById('enhanced-dropdown');
    const loadingState = document.getElementById('enhanced-loading');
    const emptyState = document.getElementById('enhanced-empty');
    const resultsContainer = document.getElementById('enhanced-results-container');

    let debounceTimer = null;
    let abortController = null;

    // Multi-source search state
    let _enhancedSearchData = null;   // Full response with all sources
    let _activeSearchSource = null;   // Currently displayed source tab
    let _altSourceController = null;  // AbortController for alternate source fetches

    const SOURCE_LABELS = {
        spotify: { text: 'Spotify', tabClass: 'enh-tab-spotify', badgeClass: 'enh-badge-spotify' },
        itunes: { text: 'Apple Music', tabClass: 'enh-tab-itunes', badgeClass: 'enh-badge-itunes' },
        deezer: { text: 'Deezer', tabClass: 'enh-tab-deezer', badgeClass: 'enh-badge-deezer' },
        discogs: { text: 'Discogs', tabClass: 'enh-tab-discogs', badgeClass: 'enh-badge-discogs' },
        hydrabase: { text: 'Hydrabase', tabClass: 'enh-tab-hydrabase', badgeClass: 'enh-badge-hydrabase' },
        youtube_videos: { text: 'Music Videos', tabClass: 'enh-tab-youtube', badgeClass: 'enh-badge-youtube' },
        musicbrainz: { text: 'MusicBrainz', tabClass: 'enh-tab-musicbrainz', badgeClass: 'enh-badge-musicbrainz' },
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
                performEnhancedSearch(query);
            }, 300);
        });

        enhancedInput.addEventListener('keypress', (e) => {
            if (e.key === 'Enter') {
                const query = e.target.value.trim();
                if (query.length >= 2) {
                    clearTimeout(debounceTimer);
                    performEnhancedSearch(query);
                }
            }
        });
    }

    if (enhancedSearchBtn) {
        enhancedSearchBtn.addEventListener('click', (e) => {
            // Prevent click from bubbling to document (which would close the dropdown)
            e.stopPropagation();

            // Get fresh references (in case we navigated away and back)
            const dropdown = document.getElementById('enhanced-dropdown');
            const results = document.getElementById('enhanced-results-container');

            if (!dropdown) return;

            // Toggle the dropdown visibility to show/hide previous search results
            if (dropdown.classList.contains('hidden')) {
                // Check if there are results to show by looking for actual content
                const hasResults = results &&
                    !results.classList.contains('hidden') &&
                    results.children.length > 0;

                if (hasResults) {
                    showDropdown();
                } else {
                    showToast('No previous results to show. Type to search!', 'info');
                }
            } else {
                hideDropdown();
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
            if (!isClickInside) {
                hideDropdown();
            }
        }
    });

    async function performEnhancedSearch(query) {
        console.log('Enhanced search:', query);
        const searchId = Date.now() + Math.random();

        // Show loading state with correct source name
        showDropdown();
        const loadingText = document.getElementById('enhanced-loading-text');
        if (loadingText) {
            const _sourceLabelMap = {
                spotify: 'Spotify', itunes: 'Apple Music', deezer: 'Deezer',
                discogs: 'Discogs', hydrabase: 'Hydrabase', musicbrainz: 'MusicBrainz',
            };
            const _sourceName = currentSearchSource && currentSearchSource !== 'auto'
                ? (_sourceLabelMap[currentSearchSource] || currentSearchSource)
                : currentMusicSourceName;
            loadingText.textContent = `Searching across ${_sourceName} and your library...`;
        }
        loadingState.classList.remove('hidden');
        emptyState.classList.add('hidden');
        resultsContainer.classList.add('hidden');

        // Abort previous requests (primary + alternates)
        if (abortController) {
            abortController.abort();
        }
        if (_altSourceController) {
            _altSourceController.abort();
        }
        abortController = new AbortController();
        _altSourceController = new AbortController();

        // Initialize multi-source state early so alternate fetches can write to it
        _enhancedSearchData = { db_artists: [], primary_source: null, sources: {}, searchId, query };

        try {
            const data = await enhancedSearchFetch(query, {
                source: currentSearchSource,
                signal: abortController.signal,
            });
            console.log('Enhanced results:', data);

            // Store multi-source state
            const primarySource = data.primary_source || data.metadata_source || 'deezer';
            _activeSearchSource = primarySource;
            _enhancedSearchData = _enhancedSearchData || {};
            _enhancedSearchData.db_artists = data.db_artists;
            _enhancedSearchData.primary_source = primarySource;
            if (!_enhancedSearchData.sources) _enhancedSearchData.sources = {};
            _enhancedSearchData.sources[primarySource] = {
                artists: data.spotify_artists || [],
                albums: data.spotify_albums || [],
                tracks: data.spotify_tracks || [],
                available: true,
            };

            // Calculate total from primary source
            const total = (data.db_artists?.length || 0) +
                (data.spotify_artists?.length || 0) +
                (data.spotify_albums?.length || 0) +
                (data.spotify_tracks?.length || 0);

            // Hide loading
            loadingState.classList.add('hidden');

            if (total === 0) {
                emptyState.classList.remove('hidden');
            } else {
                renderSourceTabs(_enhancedSearchData);
                renderDropdownResults(data);
                resultsContainer.classList.remove('hidden');
            }

            // Alternate sources now start after the primary response has landed.
            // This avoids speculative fan-out for short or aborted searches.
            _queueAlternateSourceFetches(data.alternate_sources || [], query, searchId);

        } catch (error) {
            if (error.name !== 'AbortError') {
                console.error('Enhanced search error:', error);
                loadingState.classList.add('hidden');
                emptyState.classList.remove('hidden');
            }
        }
    }

    function renderDropdownResults(data) {
        // Music Videos tab — don't render regular sections
        if (_activeSearchSource === 'youtube_videos') return;

        // Determine source badge from active tab (not just primary)
        const displaySource = _activeSearchSource || data.metadata_source || 'spotify';
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
                onClick: async () => {
                    const sourceOverride = _activeSearchSource;
                    console.log(`🎵 Opening artist detail: ${artist.name} (ID: ${artist.id}, source: ${sourceOverride})`);
                    hideDropdown();

                    // Source artists are NOT library entries — their id is a Deezer/
                    // Spotify/iTunes id, not a library PK. Route to the Artists page's
                    // inline selectArtistForDetail which fetches discography from the
                    // source directly, not the library's /api/artist-detail endpoint.
                    navigateToPage('artists');
                    await new Promise(resolve => setTimeout(resolve, 100));
                    await selectArtistForDetail(artist, {
                        source: sourceOverride,
                        plugin: artist.external_urls?.hydrabase_plugin,
                    });
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

    function _queueAlternateSourceFetches(alternateSources, query, searchId) {
        if (!Array.isArray(alternateSources) || alternateSources.length === 0) return;

        // Fetch metadata sources first, then YouTube last so it does not compete
        // with the primary artist/album/track results for early attention.
        const orderedSources = ['spotify', 'itunes', 'deezer', 'discogs', 'musicbrainz', 'hydrabase', 'youtube_videos']
            .filter(src => alternateSources.includes(src) && src !== _activeSearchSource);

        orderedSources.forEach((src, index) => {
            setTimeout(() => {
                if (!_enhancedSearchData || _enhancedSearchData.searchId !== searchId) return;
                _fetchAlternateSource(src, query, searchId);
            }, index * 150);
        });
    }

    async function _fetchAlternateSource(sourceName, query, searchId) {
        try {
            if (!_enhancedSearchData || _enhancedSearchData.searchId !== searchId) return;

            const response = await fetch(`/api/enhanced-search/source/${sourceName}`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ query }),
                signal: _altSourceController?.signal,
            });
            if (!response.ok) return;
            if (!_enhancedSearchData || _enhancedSearchData.searchId !== searchId) return;

            // Stream NDJSON — render each search type (artists, albums, tracks) as it arrives
            if (!_enhancedSearchData.sources[sourceName]) {
                const loadingSet = sourceName === 'youtube_videos' ? new Set(['videos']) : new Set(['artists', 'albums', 'tracks']);
                _enhancedSearchData.sources[sourceName] = { artists: [], albums: [], tracks: [], videos: [], available: true, _loading: loadingSet };
            }
            const sourceData = _enhancedSearchData.sources[sourceName];

            const reader = response.body.getReader();
            const decoder = new TextDecoder();
            let buffer = '';

            while (true) {
                const { done, value } = await reader.read();
                if (done) break;
                buffer += decoder.decode(value, { stream: true });

                let newlineIdx;
                while ((newlineIdx = buffer.indexOf('\n')) !== -1) {
                    const line = buffer.slice(0, newlineIdx).trim();
                    buffer = buffer.slice(newlineIdx + 1);
                    if (!line) continue;
                    if (!_enhancedSearchData || _enhancedSearchData.searchId !== searchId) return;

                    try {
                        const chunk = JSON.parse(line);
                        if (chunk.type === 'artists') { sourceData.artists = chunk.data; if (sourceData._loading) sourceData._loading.delete('artists'); }
                        else if (chunk.type === 'albums') { sourceData.albums = chunk.data; if (sourceData._loading) sourceData._loading.delete('albums'); }
                        else if (chunk.type === 'tracks') { sourceData.tracks = chunk.data; if (sourceData._loading) sourceData._loading.delete('tracks'); }
                        else if (chunk.type === 'videos') { sourceData.videos = chunk.data; if (sourceData._loading) sourceData._loading.delete('videos'); }
                        else if (chunk.type === 'done') { delete sourceData._loading; break; }

                        // Re-render tabs + content if this is the active source
                        if (_enhancedSearchData.primary_source) {
                            renderSourceTabs(_enhancedSearchData);
                            if (_activeSearchSource === sourceName) {
                                window._switchEnhSourceTab(sourceName);
                            }
                        }
                    } catch (parseErr) {
                        console.debug(`NDJSON parse error for ${sourceName}:`, parseErr);
                    }
                }
            }

            // Final render
            if (_enhancedSearchData && _enhancedSearchData.searchId === searchId && _enhancedSearchData.primary_source) {
                renderSourceTabs(_enhancedSearchData);
            }
        } catch (e) {
            if (e.name !== 'AbortError') {
                console.debug(`Alternate source ${sourceName} failed:`, e);
            }
        }
    }

    function renderSourceTabs(data) {
        const tabBar = document.getElementById('enh-source-tabs');
        if (!tabBar) return;

        const sources = data.sources || {};
        const primary = data.primary_source || 'spotify';

        // Build tab list: primary first, then alternates sorted alphabetically.
        // Hide completed zero-result sources so the bar stays focused.
        const sourceNames = Object.keys(sources).filter(s => sources[s].available);
        const visibleSources = sourceNames.filter(name => {
            const src = sources[name] || {};
            const count = name === 'youtube_videos'
                ? (src.videos?.length || 0)
                : (src.artists?.length || 0) + (src.albums?.length || 0) + (src.tracks?.length || 0);
            const isLoading = !!(src._loading && src._loading.size > 0);
            return isLoading || count > 0 || name === _activeSearchSource;
        });
        if (visibleSources.length <= 1) {
            tabBar.classList.add('hidden');
            tabBar.innerHTML = '';
            return;
        }

        // Primary tab first, then others
        const ordered = [primary, ...visibleSources.filter(s => s !== primary).sort()];

        tabBar.innerHTML = ordered.map(name => {
            const info = SOURCE_LABELS[name] || { text: name, tabClass: '' };
            const src = sources[name] || {};
            const count = name === 'youtube_videos'
                ? (src.videos?.length || 0)
                : (src.artists?.length || 0) + (src.albums?.length || 0) + (src.tracks?.length || 0);
            const isActive = name === _activeSearchSource;
            return `<button class="enh-source-tab ${info.tabClass} ${isActive ? 'active' : ''}"
                            onclick="window._switchEnhSourceTab('${name}')"
                            data-source="${name}">
                        ${info.text}<span class="enh-tab-count">(${count})</span>
                    </button>`;
        }).join('');

        tabBar.classList.remove('hidden');
    }

    // Expose tab switch globally (onclick from HTML)
    window._switchEnhSourceTab = function (sourceName) {
        if (!_enhancedSearchData || !_enhancedSearchData.sources) return;
        const src = _enhancedSearchData.sources[sourceName];
        if (!src) return;

        _activeSearchSource = sourceName;

        // Update tab active states
        document.querySelectorAll('.enh-source-tab').forEach(tab => {
            tab.classList.toggle('active', tab.dataset.source === sourceName);
        });

        // Music Videos tab — render video cards instead of regular sections
        if (sourceName === 'youtube_videos') {
            // Hide ALL regular sections including wrappers
            ['enh-db-artists-section', 'enh-spotify-artists-section', 'enh-albums-section', 'enh-singles-section', 'enh-tracks-section'].forEach(id => {
                const el = document.getElementById(id);
                if (el) el.classList.add('hidden');
            });
            // Hide the artists wrapper div too
            const artistsWrapper = document.querySelector('.enh-artists-wrapper');
            if (artistsWrapper) artistsWrapper.style.display = 'none';
            _renderVideoResults(src.videos || []);
            resultsContainer.classList.remove('hidden');
            return;
        }

        // Hide videos section and restore regular layout when switching to a metadata tab
        const videosSec = document.getElementById('enh-videos-section');
        if (videosSec) videosSec.classList.add('hidden');
        const artistsWrapper = document.querySelector('.enh-artists-wrapper');
        if (artistsWrapper) artistsWrapper.style.display = '';

        // Build data in the shape renderDropdownResults expects
        const viewData = {
            db_artists: _enhancedSearchData.db_artists,
            spotify_artists: src.artists || [],
            spotify_albums: src.albums || [],
            spotify_tracks: src.tracks || [],
            metadata_source: sourceName,
        };

        renderDropdownResults(viewData);
        resultsContainer.classList.remove('hidden');

        // Show loading spinners for categories still streaming
        if (src._loading && src._loading.size > 0) {
            const loadingHtml = '<div class="enh-section-loading"><div class="server-search-spinner" style="width:16px;height:16px"></div><span>Loading...</span></div>';
            if (src._loading.has('artists')) {
                const sec = document.getElementById('enh-spotify-artists-section');
                if (sec) { sec.classList.remove('hidden'); document.getElementById('enh-spotify-artists-list').innerHTML = loadingHtml; }
            }
            if (src._loading.has('albums')) {
                const sec = document.getElementById('enh-albums-section');
                if (sec) { sec.classList.remove('hidden'); document.getElementById('enh-albums-list').innerHTML = loadingHtml; }
                const sec2 = document.getElementById('enh-singles-section');
                if (sec2) { sec2.classList.remove('hidden'); document.getElementById('enh-singles-list').innerHTML = loadingHtml; }
            }
            if (src._loading.has('tracks')) {
                const sec = document.getElementById('enh-tracks-section');
                if (sec) { sec.classList.remove('hidden'); document.getElementById('enh-tracks-list').innerHTML = loadingHtml; }
            }
        }
    };

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
                    const imgUrl = _activeSearchSource && _activeSearchSource !== 'spotify'
                        ? `/api/artist/${artistId}/image?source=${_activeSearchSource}`
                        : `/api/artist/${artistId}/image`;
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

    function renderCompactSection(sectionId, listId, countId, items, mapItem) {
        const section = document.getElementById(sectionId);
        const list = document.getElementById(listId);
        const count = document.getElementById(countId);

        if (!list) return;

        list.innerHTML = '';

        if (!items || items.length === 0) {
            section.classList.add('hidden');
            return;
        }

        section.classList.remove('hidden');
        count.textContent = items.length;

        // Determine type based on section ID
        const isArtist = sectionId.includes('artists');
        const isAlbum = sectionId.includes('albums') || sectionId.includes('singles');
        const isTrack = sectionId.includes('tracks');

        // Add appropriate grid class to list
        if (isArtist) {
            list.classList.add('enh-artists-grid');
        } else if (isAlbum) {
            list.classList.add('enh-albums-grid');
        } else if (isTrack) {
            list.classList.add('enh-tracks-list');
        }

        items.forEach(item => {
            const config = mapItem(item);
            const elem = document.createElement('div');

            // Add appropriate card class
            if (isArtist) {
                elem.className = 'enh-compact-item artist-card';
                // Add data attributes for lazy loading
                if (item.id) {
                    elem.dataset.artistId = item.id;
                    elem.dataset.needsImage = config.image ? 'false' : 'true';
                }
            } else if (isAlbum) {
                elem.className = 'enh-compact-item album-card';
            } else if (isTrack) {
                elem.className = 'enh-compact-item track-item';
            }

            // Build image HTML with type-specific classes
            let imageClass = 'enh-item-image';
            let placeholderClass = 'enh-item-image-placeholder';

            if (isArtist) {
                imageClass += ' artist-image';
                placeholderClass += ' artist-placeholder';
            } else if (isAlbum) {
                imageClass += ' album-cover';
                placeholderClass += ' album-placeholder';
            } else if (isTrack) {
                imageClass += ' track-cover';
                placeholderClass += ' track-placeholder';
            }

            const imageHtml = config.image
                ? `<img src="${escapeHtml(config.image)}" class="${imageClass}" alt="${escapeHtml(config.name)}">`
                : `<div class="${placeholderClass}" data-lazy-image="true">${config.placeholder}</div>`;

            const badgeHtml = config.badge
                ? `<div class="enh-item-badge ${config.badge.class}">${config.badge.text}</div>`
                : '';

            const durationHtml = config.duration && isTrack
                ? `<div class="enh-item-duration">
                     ${escapeHtml(config.duration)}
                     <button class="enh-item-play-btn" title="Stream this track">▶</button>
                   </div>`
                : '';

            elem.innerHTML = `
                ${imageHtml}
                <div class="enh-item-info">
                    <div class="enh-item-name">${escapeHtml(config.name)}</div>
                    <div class="enh-item-meta">${escapeHtml(config.meta)}</div>
                </div>
                ${durationHtml}
                ${badgeHtml}
            `;

            elem.addEventListener('click', config.onClick);

            // Add play button handler for tracks
            if (isTrack && config.onPlay) {
                const playBtn = elem.querySelector('.enh-item-play-btn');
                if (playBtn) {
                    playBtn.addEventListener('click', (e) => {
                        e.stopPropagation(); // Don't trigger main onClick
                        config.onPlay();
                    });
                }
            }

            list.appendChild(elem);

            // Extract colors from image for dynamic glow effect
            if (config.image) {
                extractImageColors(config.image, (colors) => {
                    applyDynamicGlow(elem, colors);
                });
            }
        });
    }

    async function handleEnhancedSearchAlbumClick(album) {
        console.log(`💿 Enhanced search album clicked: ${album.name} by ${album.artist}`);

        hideDropdown();
        showLoadingOverlay('Loading album...');

        try {
            // Fetch full album data with tracks — pass source for correct routing
            const albumParams = new URLSearchParams({ name: album.name || '', artist: album.artist || '' });
            if (_activeSearchSource && _activeSearchSource !== 'spotify') {
                albumParams.set('source', _activeSearchSource);
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
                source: _activeSearchSource || '',
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

        hideDropdown();
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

        hideDropdown();
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
        if (dropdown) {
            dropdown.classList.remove('hidden');
            updateToggleButtonState();
        }
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
        if (dropdown) {
            dropdown.classList.add('hidden');
            updateToggleButtonState();
        }
        // Restore hidden elements
        const header = document.querySelector('#search-page .downloads-header');
        const modeToggle = document.querySelector('.search-source-picker-container');
        const slskdPlaceholder = document.querySelector('#enhanced-search-section .search-results-container');
        if (header) header.classList.remove('enh-results-active-hide');
        if (modeToggle) modeToggle.classList.remove('enh-results-active-hide');
        if (slskdPlaceholder) slskdPlaceholder.classList.remove('enh-results-active-hide');
    }

    function updateToggleButtonState() {
        // Get fresh references
        const btn = document.getElementById('enhanced-search-btn');
        const dropdown = document.getElementById('enhanced-dropdown');

        if (!btn || !dropdown) return;

        const btnIcon = btn.querySelector('.btn-icon');
        const btnText = btn.querySelector('.btn-text');

        if (dropdown.classList.contains('hidden')) {
            // Dropdown is hidden - button should say "Show Results"
            if (btnIcon) btnIcon.textContent = '👁️';
            if (btnText) btnText.textContent = 'Show Results';
        } else {
            // Dropdown is visible - button should say "Hide Results"
            if (btnIcon) btnIcon.textContent = '🙈';
            if (btnText) btnText.textContent = 'Hide Results';
        }
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

        history.replaceState({ page: targetPage }, '', (targetPage === 'dashboard' ? '/' : '/' + targetPage) + window.location.search + window.location.hash);

        if (targetPage !== 'dashboard') {
            navigateToPage(targetPage, { skipPushState: true });
        } else {
            await loadDashboardData();
            loadDashboardSyncHistory();
        }
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

