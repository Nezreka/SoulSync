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
        detailBackButton.addEventListener('click', () => {
            // If the user searched within the Artists page, back returns to the
            // results list so they can pick a different artist.
            if (artistsPageState.searchResults && artistsPageState.searchResults.length > 0) {
                showArtistsResultsState();
                return;
            }
            // Otherwise the user reached this detail view from elsewhere (Search,
            // Discover, watchlist, etc.). The Artists page is no longer a sidebar
            // entry, so there's nothing useful to fall back to here — let the
            // browser take them back to wherever they came from, or drop them on
            // Search (the go-forward way to find another artist).
            if (window.history.length > 1) {
                window.history.back();
            } else {
                navigateToPage('search');
            }
        });
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
    observeLazyBackgrounds(container);

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

    // Lazy load missing artist images
    console.log('🖼️ Starting lazy load for artist images on Artists page...');
    if (typeof lazyLoadArtistImages === 'function') {
        lazyLoadArtistImages(container);
    } else if (typeof window.lazyLoadArtistImages === 'function') {
        window.lazyLoadArtistImages(container);
    } else {
        console.error('❌ lazyLoadArtistImages function not found!');
    }

    // Add mouse wheel horizontal scrolling
    container.addEventListener('wheel', (event) => {
        if (event.deltaY !== 0) {
            event.preventDefault();
            container.scrollLeft += event.deltaY;
        }
    });
}

/**
 * Lazy load artist images for cards that don't have images yet.
 * Fetches images asynchronously so search results appear immediately.
 */
async function lazyLoadArtistImages(container) {
    if (!container) {
        console.error('❌ lazyLoadArtistImages: container is null');
        return;
    }

    // Find all cards that need images
    const cardsNeedingImages = container.querySelectorAll('[data-needs-image="true"]');

    if (cardsNeedingImages.length === 0) {
        console.log('✅ All artist cards have images');
        return;
    }

    console.log(`🖼️ Lazy loading images for ${cardsNeedingImages.length} artist cards`);

    // Load images in parallel (but with a small batch to avoid overwhelming the server)
    const batchSize = 5;
    const cards = Array.from(cardsNeedingImages);

    for (let i = 0; i < cards.length; i += batchSize) {
        const batch = cards.slice(i, i + batchSize);

        await Promise.all(batch.map(async (card) => {
            const artistId = card.dataset.artistId;
            if (!artistId) {
                console.warn('⚠️ Card missing artistId:', card);
                return;
            }

            try {
                console.log(`🔄 Fetching image for artist ${artistId}...`);
                const response = await fetch(`/api/artist/${artistId}/image`);
                const data = await response.json();

                console.log(`📥 Got response for ${artistId}:`, data);

                if (data.success && data.image_url) {
                    // Update the card's background image
                    // Handle both card types (suggestion-card and artist-card)
                    if (card.classList.contains('suggestion-card')) {
                        card.style.backgroundImage = `url(${data.image_url})`;
                        card.style.backgroundSize = 'cover';
                        card.style.backgroundPosition = 'center';
                    } else if (card.classList.contains('artist-card')) {
                        const bgElement = card.querySelector('.artist-card-background');
                        if (bgElement) {
                            // Clear the gradient first, then set the image
                            bgElement.style.cssText = `background-image: url('${data.image_url}'); background-size: cover; background-position: center;`;
                        }
                    }

                    card.dataset.needsImage = 'false';
                    console.log(`✅ Loaded image for artist ${artistId}`);
                }
            } catch (error) {
                console.error(`❌ Failed to load image for artist ${artistId}:`, error);
            }
        }));
    }

    console.log('✅ Finished lazy loading artist images');
}

// Make function globally accessible
window.lazyLoadArtistImages = lazyLoadArtistImages;

/**
 * Create HTML for an artist card
 */
function createArtistCardHTML(artist) {
    const imageUrl = artist.image_url || '';
    const genres = artist.genres && artist.genres.length > 0 ?
        artist.genres.slice(0, 3).join(', ') : 'Various genres';
    const popularity = artist.popularity || 0;

    // Use data-bg-src for lazy background loading via IntersectionObserver
    const backgroundAttr = imageUrl ?
        `data-bg-src="${imageUrl}"` :
        `style="background: linear-gradient(135deg, rgba(29, 185, 84, 0.3) 0%, rgba(24, 156, 71, 0.2) 100%);"`;

    // Format popularity as a percentage for better UX
    const popularityText = popularity > 0 ? `${popularity}% Popular` : 'Popularity Unknown';

    // Track if image needs to be lazy loaded
    const needsImage = imageUrl ? 'false' : 'true';

    // Check for MusicBrainz ID
    let mbIconHTML = '';
    if (artist.musicbrainz_id) {
        mbIconHTML = `
            <div class="mb-card-icon" title="View on MusicBrainz" onclick="event.stopPropagation(); window.open('https://musicbrainz.org/artist/${artist.musicbrainz_id}', '_blank')">
                <img src="${MUSICBRAINZ_LOGO_URL}" style="width: 20px; height: auto; display: block;">
            </div>
        `;
    }

    return `
        <div class="artist-card" data-artist-id="${artist.id}" data-needs-image="${needsImage}">
            ${mbIconHTML}
            <div class="artist-card-background" ${backgroundAttr}></div>
            <div class="artist-card-overlay"></div>
            <div class="artist-card-content">
                <div class="artist-card-name">${escapeHtml(artist.name)}</div>
                <div class="artist-card-genres">${escapeHtml(genres)}</div>
                <div class="artist-card-popularity">
                    <span class="popularity-icon">🔥</span>
                    <span>${popularityText}</span>
                </div>
                <div class="artist-card-actions">
                    <div class="watchlist-btn-group">
                        <button class="watchlist-toggle-btn" data-artist-id="${artist.id}" data-artist-name="${escapeHtml(artist.name)}" onclick="toggleWatchlist(event, '${artist.id}', '${escapeForInlineJs(artist.name)}')">
                            <span class="watchlist-icon">👁️</span>
                            <span class="watchlist-text">Add to Watchlist</span>
                        </button>
                        <button class="watchlist-settings-btn hidden" data-artist-id="${artist.id}" data-artist-name="${escapeHtml(artist.name)}" onclick="event.stopPropagation(); openWatchlistArtistConfigModal('${artist.id}', '${escapeForInlineJs(artist.name)}')" title="Watchlist Settings">&#9881;</button>
                    </div>
                </div>
            </div>
        </div>
    `;
}

/**
 * Select an artist and show their discography
 */
async function selectArtistForDetail(artist, options = {}) {
    console.log(`🎤 Selected artist: ${artist.name}`);

    // Cancel any ongoing completion check from previous artist
    if (artistCompletionController) {
        console.log('⏹️ Canceling previous artist completion check');
        artistCompletionController.abort();
        artistCompletionController = null;
    }

    // Cancel any ongoing similar artists stream from previous artist
    if (similarArtistsController) {
        console.log('⏹️ Canceling previous similar artists stream');
        similarArtistsController.abort();
        similarArtistsController = null;
    }

    // Update state
    artistsPageState.selectedArtist = artist;
    artistsPageState.currentView = 'detail';
    artistsPageState.sourceOverride = options.source || artist.source || null;
    artistsPageState.pluginOverride = options.plugin || null;

    // Show detail state
    showArtistDetailState();

    // Update artist info in header
    updateArtistDetailHeader(artist);

    // Load discography (pass artist name for cross-source fallback)
    await loadArtistDiscography(artist.id, artist.name, artistsPageState.sourceOverride, options.plugin);
}

/**
 * Load artist's discography from Spotify or iTunes
 * @param {string} artistId - Artist ID (Spotify or iTunes format)
 * @param {string} [artistName] - Optional artist name for fallback searches
 */
async function loadArtistDiscography(artistId, artistName = null, sourceOverride = null, pluginOverride = null) {
    console.log(`💿 Loading discography for artist: ${artistId} (name: ${artistName}, source: ${sourceOverride || 'auto'})`);

    // Use source-prefixed cache key to avoid ID collisions between sources
    const cacheKey = sourceOverride ? `${sourceOverride}:${artistId}` : artistId;

    // Check cache first
    if (artistsPageState.cache.discography[cacheKey]) {
        console.log('📦 Using cached discography');
        const cachedDiscography = artistsPageState.cache.discography[cacheKey];
        if (artistsPageState.selectedArtist) {
            artistsPageState.selectedArtist = {
                ...artistsPageState.selectedArtist,
                source: cachedDiscography.source || sourceOverride || artistsPageState.selectedArtist.source || null,
            };
        }
        artistsPageState.sourceOverride = cachedDiscography.source || sourceOverride || artistsPageState.sourceOverride || null;
        displayArtistDiscography(cachedDiscography);

        // Load similar artists in parallel (don't wait) — always uses primary source
        loadSimilarArtists(artistsPageState.selectedArtist?.name).catch(err => {
            console.error('❌ Error loading similar artists:', err);
        });

        // Still check completion status for cached data
        await checkDiscographyCompletion(artistId, cachedDiscography);
        return;
    }

    try {
        // Show loading states
        showDiscographyLoading();

        // Build URL with optional artist name and source override for fallback
        let url = `/api/artist/${artistId}/discography`;
        const params = new URLSearchParams();
        if (artistName) params.set('artist_name', artistName);
        if (sourceOverride) params.set('source', sourceOverride);
        if (pluginOverride) params.set('plugin', pluginOverride);
        if (params.toString()) url += `?${params.toString()}`;

        // Call the real API endpoint
        const response = await fetch(url);

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
            singles: data.singles || [],
            source: data.source || sourceOverride || null,
        };

        // Keep the resolved metadata source on the selected artist so album clicks
        // can pass it through to /api/album/<id>/tracks.
        if (artistsPageState.selectedArtist) {
            artistsPageState.selectedArtist = {
                ...artistsPageState.selectedArtist,
                source: discography.source,
            };
        }
        artistsPageState.sourceOverride = discography.source || artistsPageState.sourceOverride || null;

        // Update selected artist with full details from backend (includes MusicBrainz ID)
        if (data.artist) {
            console.log('✨ Updating artist details with fresh data from backend');
            artistsPageState.selectedArtist = {
                ...artistsPageState.selectedArtist,
                ...data.artist
            };
        }

        // Merge artist_info enrichment from discography response
        if (data.artist_info) {
            artistsPageState.selectedArtist = {
                ...artistsPageState.selectedArtist,
                artist_info: data.artist_info,
            };
        }

        // Refresh header with all available data
        updateArtistDetailHeader(artistsPageState.selectedArtist);

        console.log(`✅ Loaded ${discography.albums.length} albums and ${discography.singles.length} singles`);

        // Cache the results (use source-prefixed key if source override active)
        artistsPageState.cache.discography[cacheKey] = discography;
        artistsPageState.artistDiscography = discography;

        // Display results
        displayArtistDiscography(discography);

        // Load similar artists and check completion in parallel (don't wait)
        loadSimilarArtists(artistsPageState.selectedArtist?.name).catch(err => {
            console.error('❌ Error loading similar artists:', err);
        });

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

    // Show Download Discography button(s) if there are any releases
    const _totalReleases = (discography.albums?.length || 0) + (discography.eps?.length || 0) + (discography.singles?.length || 0);
    const _discogWrap = document.getElementById('discog-download-wrap');
    if (_discogWrap) _discogWrap.style.display = _totalReleases > 0 ? '' : 'none';
    const _discogBtnArtists = document.getElementById('discog-download-btn-artists');
    if (_discogBtnArtists) _discogBtnArtists.style.display = _totalReleases > 0 ? '' : 'none';

    // Populate albums
    const albumsContainer = document.getElementById('album-cards-container');
    if (albumsContainer) {
        if (discography.albums?.length > 0) {
            albumsContainer.innerHTML = discography.albums.map(album => createAlbumCardHTML(album)).join('');
            observeLazyBackgrounds(albumsContainer);

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
            observeLazyBackgrounds(singlesContainer);

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

    // Auto-switch to Singles tab if no albums but has singles
    if ((!discography.albums || discography.albums.length === 0) &&
        discography.singles && discography.singles.length > 0) {
        console.log('📀 No albums found, auto-switching to Singles & EPs tab');

        // Switch to singles tab
        const albumsTab = document.getElementById('albums-tab');
        const singlesTab = document.getElementById('singles-tab');
        const albumsContent = document.getElementById('albums-content');
        const singlesContent = document.getElementById('singles-content');

        if (albumsTab && singlesTab && albumsContent && singlesContent) {
            // Remove active from albums
            albumsTab.classList.remove('active');
            albumsContent.classList.remove('active');

            // Add active to singles
            singlesTab.classList.add('active');
            singlesContent.classList.add('active');
        }
    }
}

/**
 * Load similar artists from MusicMap
 */
async function loadSimilarArtists(artistName) {
    if (!artistName) {
        console.warn('⚠️ No artist name provided for similar artists');
        return;
    }

    console.log(`🔍 Loading similar artists for: ${artistName}`);

    // Get DOM elements
    const section = document.getElementById('similar-artists-section');
    const loadingEl = document.getElementById('similar-artists-loading');
    const errorEl = document.getElementById('similar-artists-error');
    const container = document.getElementById('similar-artists-bubbles-container');

    if (!section || !loadingEl || !errorEl || !container) {
        console.warn('⚠️ Similar artists section elements not found');
        return;
    }

    // Show loading state
    loadingEl.classList.remove('hidden');
    errorEl.classList.add('hidden');
    container.innerHTML = '';
    section.style.display = 'block';

    try {
        // Create new abort controller for this similar artists stream
        similarArtistsController = new AbortController();

        // Use streaming endpoint for real-time bubble creation
        const url = `/api/artist/similar/${encodeURIComponent(artistName)}/stream`;
        console.log(`📡 Streaming from: ${url}`);

        const response = await fetch(url, {
            signal: similarArtistsController.signal
        });

        if (!response.ok) {
            throw new Error(`Failed to fetch similar artists: ${response.status}`);
        }

        const reader = response.body.getReader();
        const decoder = new TextDecoder();
        let buffer = '';
        let artistCount = 0;

        // Read the stream
        while (true) {
            const { done, value } = await reader.read();

            if (done) {
                console.log('✅ Stream complete');
                break;
            }

            // Decode the chunk and add to buffer
            buffer += decoder.decode(value, { stream: true });

            // Process complete messages (separated by \n\n)
            const messages = buffer.split('\n\n');
            buffer = messages.pop() || ''; // Keep incomplete message in buffer

            for (const message of messages) {
                if (!message.trim() || !message.startsWith('data: ')) continue;

                try {
                    const jsonData = JSON.parse(message.substring(6)); // Remove 'data: ' prefix

                    if (jsonData.error) {
                        throw new Error(jsonData.error);
                    }

                    if (jsonData.artist) {
                        // Hide loading on first artist
                        if (artistCount === 0) {
                            loadingEl.classList.add('hidden');
                        }

                        // Create and append bubble immediately
                        const bubble = createSimilarArtistBubble(jsonData.artist);
                        container.appendChild(bubble);
                        artistCount++;

                        console.log(`✅ Added bubble for: ${jsonData.artist.name} (${artistCount})`);
                    }

                    if (jsonData.complete) {
                        console.log(`🎉 Streaming complete: ${jsonData.total} artists`);

                        if (artistCount === 0) {
                            loadingEl.classList.add('hidden');
                            container.innerHTML = `
                                <div style="width: 100%; text-align: center; padding: 40px 20px; color: rgba(255, 255, 255, 0.5);">
                                    <div style="font-size: 18px; margin-bottom: 8px;">🎵</div>
                                    <div style="font-size: 14px;">No similar artists found</div>
                                </div>
                            `;
                        } else {
                            // Lazy load images for similar artists that don't have them
                            lazyLoadSimilarArtistImages(container);
                        }
                    }
                } catch (parseError) {
                    console.error('❌ Error parsing stream message:', parseError);
                }
            }
        }

        // Clear the controller when done
        similarArtistsController = null;

    } catch (error) {
        // Don't show error if it was aborted (user navigated away)
        if (error.name === 'AbortError') {
            console.log('⏹️ Similar artists stream aborted (user navigated to new artist)');
            loadingEl.classList.add('hidden');
            return;
        }

        console.error('❌ Error loading similar artists:', error);

        // Hide loading, show error
        loadingEl.classList.add('hidden');
        errorEl.classList.remove('hidden');

        // Also show error message in container
        container.innerHTML = `
            <div style="width: 100%; text-align: center; padding: 40px 20px; color: rgba(239, 68, 68, 0.7);">
                <div style="font-size: 18px; margin-bottom: 8px;">⚠️</div>
                <div style="font-size: 14px;">${error.message}</div>
            </div>
        `;
    } finally {
        // Always clear the controller
        similarArtistsController = null;
    }
}

/**
 * Lazy load images for similar artist bubbles that don't have images
 */
async function lazyLoadSimilarArtistImages(container) {
    if (!container) return;

    const bubblesNeedingImages = container.querySelectorAll('.similar-artist-bubble[data-needs-image="true"]');

    if (bubblesNeedingImages.length === 0) {
        console.log('✅ All similar artist bubbles have images');
        return;
    }

    console.log(`🖼️ Lazy loading images for ${bubblesNeedingImages.length} similar artists`);

    // Load images in parallel batches
    const batchSize = 5;
    const bubbles = Array.from(bubblesNeedingImages);

    for (let i = 0; i < bubbles.length; i += batchSize) {
        const batch = bubbles.slice(i, i + batchSize);

        await Promise.all(batch.map(async (bubble) => {
            const artistId = bubble.getAttribute('data-artist-id');
            const artistSource = bubble.getAttribute('data-artist-source') || '';
            const artistPlugin = bubble.getAttribute('data-artist-plugin') || '';
            if (!artistId) return;

            try {
                const params = new URLSearchParams();
                if (artistSource) params.set('source', artistSource);
                if (artistPlugin) params.set('plugin', artistPlugin);

                const imageUrl = params.toString()
                    ? `/api/artist/${encodeURIComponent(artistId)}/image?${params.toString()}`
                    : `/api/artist/${encodeURIComponent(artistId)}/image`;

                const response = await fetch(imageUrl);
                const data = await response.json();

                if (data.success && data.image_url) {
                    const imageContainer = bubble.querySelector('.similar-artist-bubble-image');
                    if (imageContainer) {
                        const artistName = bubble.querySelector('.similar-artist-bubble-name')?.textContent || 'Artist';
                        imageContainer.innerHTML = `<img src="${data.image_url}" alt="${artistName}">`;
                        bubble.setAttribute('data-needs-image', 'false');
                        console.log(`✅ Loaded image for similar artist ${artistId}`);
                    }
                }
            } catch (error) {
                console.warn(`⚠️ Failed to load image for similar artist ${artistId}:`, error);
            }
        }));
    }

    console.log('✅ Finished lazy loading similar artist images');
}

/**
 * Display similar artist bubble cards progressively (one at a time with delay)
 */
function displaySimilarArtistsProgressively(artists) {
    const container = document.getElementById('similar-artists-bubbles-container');

    if (!container) {
        console.warn('⚠️ Similar artists container not found');
        return;
    }

    // Clear container
    container.innerHTML = '';

    // Add each bubble with a delay to simulate progressive loading
    artists.forEach((artist, index) => {
        setTimeout(() => {
            const bubble = createSimilarArtistBubble(artist);
            container.appendChild(bubble);
        }, index * 100); // 100ms delay between each bubble
    });

    console.log(`✅ Displaying ${artists.length} similar artist bubbles progressively`);
}

/**
 * Display similar artist bubble cards (all at once - legacy)
 */
function displaySimilarArtists(artists) {
    const container = document.getElementById('similar-artists-bubbles-container');

    if (!container) {
        console.warn('⚠️ Similar artists container not found');
        return;
    }

    // Clear container
    container.innerHTML = '';

    // Create bubble cards with staggered animation
    artists.forEach((artist, index) => {
        const bubble = createSimilarArtistBubble(artist);

        // Add staggered animation delay (50ms per bubble)
        bubble.style.animationDelay = `${index * 0.05}s`;

        container.appendChild(bubble);
    });

    console.log(`✅ Displayed ${artists.length} similar artist bubbles`);
}

/**
 * Create a similar artist bubble card element
 */
function createSimilarArtistBubble(artist) {
    // Create bubble container
    const bubble = document.createElement('div');
    bubble.className = 'similar-artist-bubble';
    bubble.setAttribute('data-artist-id', artist.id);
    bubble.setAttribute('data-artist-source', artist.source || '');
    if (artist.plugin) {
        bubble.setAttribute('data-artist-plugin', artist.plugin);
    }

    // Track if image needs lazy loading
    const hasImage = artist.image_url && artist.image_url.trim() !== '';
    bubble.setAttribute('data-needs-image', hasImage ? 'false' : 'true');

    // Create image container
    const imageContainer = document.createElement('div');
    imageContainer.className = 'similar-artist-bubble-image';

    if (hasImage) {
        const img = document.createElement('img');
        img.src = artist.image_url;
        img.alt = artist.name;

        // Handle image load error
        img.onerror = () => {
            console.log(`Failed to load image for ${artist.name}`);
            imageContainer.innerHTML = `<div class="similar-artist-bubble-image-fallback">🎵</div>`;
            bubble.setAttribute('data-needs-image', 'true');
        };

        imageContainer.appendChild(img);
    } else {
        // No image - show fallback (will be lazy loaded)
        imageContainer.innerHTML = `<div class="similar-artist-bubble-image-fallback">🎵</div>`;
    }

    // Create name element
    const name = document.createElement('div');
    name.className = 'similar-artist-bubble-name';
    name.textContent = artist.name;
    name.title = artist.name; // Tooltip for full name

    // Optional: Create genres element (hidden by default in CSS)
    const genres = document.createElement('div');
    genres.className = 'similar-artist-bubble-genres';
    if (artist.genres && artist.genres.length > 0) {
        genres.textContent = artist.genres.slice(0, 2).join(', ');
    }

    // Assemble bubble
    bubble.appendChild(imageContainer);
    bubble.appendChild(name);
    if (artist.genres && artist.genres.length > 0) {
        bubble.appendChild(genres);
    }

    // Add click handler to navigate to artist detail page
    bubble.addEventListener('click', () => {
        console.log(`🎵 Clicked similar artist: ${artist.name} (ID: ${artist.id})`);
        // Navigate to this artist's detail page (same as clicking from search results)
        selectArtistForDetail(
            artist,
            artist.source ? { source: artist.source, plugin: artist.plugin } : {}
        );
    });

    return bubble;
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
        // Create new abort controller for this completion check
        artistCompletionController = new AbortController();

        // Use fetch with streaming response
        const response = await fetch(`/api/artist/${artistId}/completion-stream`, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            },
            body: JSON.stringify({
                discography: discography,
                artist_name: artistsPageState.selectedArtist?.name || 'Unknown Artist',
                source: discography?.source || artistsPageState.sourceOverride || null,
            }),
            signal: artistCompletionController.signal
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

        // Clear the controller when done
        artistCompletionController = null;

    } catch (error) {
        // Don't show error if it was aborted (user navigated away)
        if (error.name === 'AbortError') {
            console.log('⏹️ Completion check aborted (user navigated to new artist)');
            return;
        }

        console.error('❌ Failed to check completion status:', error);
        showCompletionError();
    } finally {
        // Always clear the controller
        artistCompletionController = null;
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

    // Reclassify and move cards when track count reveals single/EP (Discogs lazy fetch)
    const currentType = albumCard.dataset.albumType;
    const expectedTracks = completionData.expected_tracks || 0;
    if (expectedTracks > 0) {
        albumCard.dataset.totalTracks = expectedTracks;
        let newType = currentType;
        if (currentType === 'album' && expectedTracks <= 3) newType = 'single';
        else if (currentType === 'album' && expectedTracks <= 6) newType = 'ep';

        if (newType !== currentType) {
            albumCard.dataset.albumType = newType;
            const typeEl = albumCard.querySelector('.album-card-type');
            if (typeEl) typeEl.textContent = newType === 'single' ? 'Single' : 'EP';

            // Move card from albums grid to singles grid
            const singlesGrid = document.getElementById('singles-grid');
            const singlesSection = singlesGrid?.closest('.discography-section');
            if (singlesGrid) {
                albumCard.remove();
                singlesGrid.appendChild(albumCard);
                if (singlesSection) singlesSection.style.display = '';
            }
        }
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
    const progressText = completionData.expected_tracks > 0
        ? `${completionData.owned_tracks}/${completionData.expected_tracks}`
        : '';

    overlay.innerHTML = progressText
        ? `<span class="completion-status">${statusText}</span><span class="completion-progress">${progressText}</span>`
        : `<span class="completion-status">${statusText}</span>`;

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

    // Use data-bg-src for lazy background loading via IntersectionObserver
    const backgroundAttr = imageUrl ?
        `data-bg-src="${imageUrl}"` :
        `style="background: linear-gradient(135deg, rgba(29, 185, 84, 0.2) 0%, rgba(24, 156, 71, 0.1) 100%);"`;

    return `
        <div class="album-card" data-album-id="${album.id}" data-album-name="${escapeHtml(album.name)}" data-album-type="${album.album_type}" data-total-tracks="${album.total_tracks || 0}">
            <div class="album-card-image" ${backgroundAttr}></div>
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

    // Cancel any ongoing completion check when navigating back to search
    if (artistCompletionController) {
        console.log('⏹️ Canceling completion check (navigating back to search)');
        artistCompletionController.abort();
        artistCompletionController = null;
    }

    // Cancel any ongoing similar artists stream when navigating back to search
    if (similarArtistsController) {
        console.log('⏹️ Canceling similar artists stream (navigating back to search)');
        similarArtistsController.abort();
        similarArtistsController = null;
    }

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

    // Cancel any ongoing completion check when navigating back
    if (artistCompletionController) {
        console.log('⏹️ Canceling completion check (navigating back to results)');
        artistCompletionController.abort();
        artistCompletionController = null;
    }

    // Cancel any ongoing similar artists stream when navigating back
    if (similarArtistsController) {
        console.log('⏹️ Canceling similar artists stream (navigating back to results)');
        similarArtistsController.abort();
        similarArtistsController = null;
    }

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
    const _esc = (s) => (s || '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
    const info = artist.artist_info || {};
    const imageUrl = artist.image_url || info.image_url || '';

    // Background blur
    const heroBg = document.getElementById('artists-hero-bg');
    if (heroBg) {
        heroBg.style.backgroundImage = imageUrl ? `url('${imageUrl}')` : 'none';
    }

    // Artist image
    const heroImage = document.getElementById('artists-hero-image');
    if (heroImage) {
        if (imageUrl) {
            heroImage.style.backgroundImage = `url('${imageUrl}')`;
            heroImage.innerHTML = '';
        } else {
            heroImage.style.backgroundImage = 'none';
            heroImage.innerHTML = '<span class="artists-hero-image-fallback">🎤</span>';
            // Lazy load
            fetch(`/api/artist/${artist.id}/image`)
                .then(r => r.json())
                .then(d => {
                    if (d.success && d.image_url) {
                        heroImage.style.backgroundImage = `url('${d.image_url}')`;
                        heroImage.innerHTML = '';
                        if (heroBg) heroBg.style.backgroundImage = `url('${d.image_url}')`;
                        artist.image_url = d.image_url;
                    }
                }).catch(() => { });
        }
    }

    // Name
    const heroName = document.getElementById('artists-hero-name');
    if (heroName) heroName.textContent = artist.name || 'Unknown Artist';

    // Badges (service links — real logos matching library page)
    const badgesEl = document.getElementById('artists-hero-badges');
    if (badgesEl) {
        const _hb = (logo, fallback, title, url) => {
            const inner = logo
                ? `<img src="${logo}" alt="${fallback}" onerror="this.parentNode.textContent='${fallback}'">`
                : `<span style="font-size:9px;font-weight:700;">${fallback}</span>`;
            if (url) return `<a class="artists-hero-badge" title="${title}" href="${_esc(url)}" target="_blank" rel="noopener noreferrer">${inner}</a>`;
            return `<div class="artists-hero-badge" title="${title}">${inner}</div>`;
        };
        const badges = [];
        if (info.spotify_artist_id) badges.push(_hb(SPOTIFY_LOGO_URL, 'SP', 'Spotify', `https://open.spotify.com/artist/${info.spotify_artist_id}`));
        if (info.musicbrainz_id || artist.musicbrainz_id) badges.push(_hb(MUSICBRAINZ_LOGO_URL, 'MB', 'MusicBrainz', `https://musicbrainz.org/artist/${info.musicbrainz_id || artist.musicbrainz_id}`));
        if (info.deezer_id) badges.push(_hb(DEEZER_LOGO_URL, 'Dz', 'Deezer', `https://www.deezer.com/artist/${info.deezer_id}`));
        if (info.itunes_artist_id) badges.push(_hb(ITUNES_LOGO_URL, 'IT', 'Apple Music', `https://music.apple.com/artist/${info.itunes_artist_id}`));
        if (info.lastfm_url) badges.push(_hb(LASTFM_LOGO_URL, 'LFM', 'Last.fm', info.lastfm_url));
        if (info.genius_url) badges.push(_hb(GENIUS_LOGO_URL, 'GEN', 'Genius', info.genius_url));
        if (info.tidal_id) badges.push(_hb(TIDAL_LOGO_URL, 'TD', 'Tidal', `https://tidal.com/browse/artist/${info.tidal_id}`));
        if (info.qobuz_id) badges.push(_hb(QOBUZ_LOGO_URL, 'Qz', 'Qobuz', `https://www.qobuz.com/artist/${info.qobuz_id}`));
        if (info.discogs_id) badges.push(_hb(DISCOGS_LOGO_URL, 'DC', 'Discogs', `https://www.discogs.com/artist/${info.discogs_id}`));
        badgesEl.innerHTML = badges.join('');
    }

    // Genres (pill tags — merge with Last.fm tags, deduplicated)
    const genresEl = document.getElementById('artists-hero-genres');
    if (genresEl) {
        let genres = info.genres || artist.genres || [];
        // Merge Last.fm tags
        const lfmTags = info.lastfm_tags || [];
        if (Array.isArray(lfmTags) && lfmTags.length > 0) {
            const existing = new Set(genres.map(g => g.toLowerCase()));
            const newTags = lfmTags.filter(t => !existing.has(t.toLowerCase()));
            genres = [...genres, ...newTags];
        }
        if (genres.length > 0) {
            genresEl.innerHTML = genres.slice(0, 8).map(g =>
                `<span class="artists-hero-genre-pill">${_esc(g)}</span>`
            ).join('');
        } else {
            genresEl.innerHTML = '';
        }
    }

    // Bio (Last.fm bio or summary fallback — matching library page pattern)
    const bioEl = document.getElementById('artists-hero-bio');
    if (bioEl) {
        const bio = info.lastfm_bio || info.bio || '';
        if (bio) {
            // Strip HTML tags and "Read more on Last.fm" links
            let cleanBio = bio.replace(/<a\b[^>]*>.*?<\/a>/gi, '').replace(/<[^>]+>/g, '').trim();
            if (cleanBio) {
                bioEl.innerHTML = `<span class="artists-hero-bio-text">${_esc(cleanBio)}</span>
                    <span class="artists-hero-bio-toggle" onclick="this.parentElement.classList.toggle('expanded');this.textContent=this.parentElement.classList.contains('expanded')?'Show less':'Read more'">Read more</span>`;
                bioEl.style.display = '';
            } else {
                bioEl.style.display = 'none';
            }
        } else {
            bioEl.style.display = 'none';
        }
    }

    // Stats (Last.fm listeners + playcount, with followers fallback)
    const statsEl = document.getElementById('artists-hero-stats');
    if (statsEl) {
        const _fmtNum = (n) => {
            if (!n || n <= 0) return '0';
            if (n >= 1000000) return (n / 1000000).toFixed(1).replace(/\.0$/, '') + 'M';
            if (n >= 1000) return (n / 1000).toFixed(1).replace(/\.0$/, '') + 'K';
            return n.toLocaleString();
        };
        let stats = '';
        if (info.lastfm_listeners) {
            stats += `<span class="artists-hero-stat"><strong>${_fmtNum(info.lastfm_listeners)}</strong> listeners</span>`;
        }
        if (info.lastfm_playcount) {
            stats += `<span class="artists-hero-stat"><strong>${_fmtNum(info.lastfm_playcount)}</strong> plays</span>`;
        }
        if (!stats && info.followers) {
            stats += `<span class="artists-hero-stat"><strong>${_fmtNum(info.followers)}</strong> followers</span>`;
        }
        statsEl.innerHTML = stats;
    }

    // Also update old hidden elements for any JS that references them
    const oldImage = document.getElementById('search-artist-detail-image');
    if (oldImage && imageUrl) oldImage.style.backgroundImage = `url('${imageUrl}')`;
    const oldName = document.getElementById('search-artist-detail-name');
    if (oldName) oldName.textContent = artist.name;

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

    // Store artist info on the button for settings gear access
    button.dataset.artistId = artist.id;
    button.dataset.artistName = artist.name;

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
    await updateArtistDetailWatchlistButton(artist.id, artist.name);
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

        // Show/hide watchlist settings gear
        const settingsBtn = document.getElementById('artist-detail-watchlist-settings-btn');
        if (settingsBtn) {
            if (!isWatching) {
                // Just added to watchlist — show gear
                settingsBtn.classList.remove('hidden');
                settingsBtn.onclick = () => openWatchlistArtistConfigModal(artistId, artistName);
            } else {
                // Just removed from watchlist — hide gear
                settingsBtn.classList.add('hidden');
                settingsBtn.onclick = null;
            }
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
async function updateArtistDetailWatchlistButton(artistId, artistName) {
    const button = document.getElementById('artist-detail-watchlist-btn');
    if (!button) {
        console.warn('⚠️ Artist detail watchlist button not found');
        return;
    }

    // Use passed name or fall back to stored data attribute
    const name = artistName || button.dataset.artistName || '';

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

            // Show/hide watchlist settings gear
            const settingsBtn = document.getElementById('artist-detail-watchlist-settings-btn');
            if (settingsBtn) {
                if (data.is_watching) {
                    settingsBtn.classList.remove('hidden');
                    settingsBtn.onclick = () => openWatchlistArtistConfigModal(artistId, name);
                } else {
                    settingsBtn.classList.add('hidden');
                    settingsBtn.onclick = null;
                }
            }

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

        // Fetch album tracks from backend (pass name/artist for Hydrabase support)
        const _aat1 = new URLSearchParams({ name: album.name || '', artist: artist.name || '' });
        const albumSource = artistsPageState.sourceOverride || album.source || artist.source || artistsPageState.artistDiscography?.source || null;
        if (albumSource) {
            _aat1.set('source', albumSource);
        }
        if (artistsPageState.pluginOverride) {
            _aat1.set('plugin', artistsPageState.pluginOverride);
        }
        const response = await fetch(`/api/album/${album.id}/tracks?${_aat1}`);

        if (!response.ok) {
            if (response.status === 401) {
                throw new Error('Spotify not authenticated. Please check your API settings.');
            }
            const errData = await response.json().catch(() => ({}));
            throw new Error(errData.error || `Failed to load album tracks: ${response.status}`);
        }

        const data = await response.json();

        if (!data.success || !data.tracks || data.tracks.length === 0) {
            throw new Error('No tracks found for this album');
        }

        console.log(`✅ Loaded ${data.tracks.length} tracks for ${data.album.name}`);

        // Use album data from API response (has complete data including images array)
        const fullAlbumData = data.album;

        // Format playlist name with artist and album info
        const playlistName = `[${artist.name}] ${fullAlbumData.name}`;

        // Open download missing tracks modal with formatted tracks
        // Pass false for showLoadingOverlay since we already have one from handleArtistAlbumClick
        // Use fullAlbumData from API response instead of album parameter
        await openDownloadMissingModalForArtistAlbum(virtualPlaylistId, playlistName, data.tracks, fullAlbumData, artist, false);

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
async function openDownloadMissingModalForArtistAlbum(virtualPlaylistId, playlistName, spotifyTracks, album, artist, showLoadingOverlayParam = true, contextType = 'artist_album') {
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
        albumType: album.album_type,
        source: artist?.source || album?.source || artistsPageState.artistDiscography?.source || null
    };

    // Generate hero section — 'artist_album' for releases, 'playlist' for charts/compilations
    const heroContext = contextType === 'playlist' ? {
        type: 'playlist',
        playlist: { name: playlistName, owner: 'Beatport' },
        trackCount: spotifyTracks.length,
        playlistId: virtualPlaylistId
    } : {
        type: 'artist_album',
        artist: artist,
        album: album,
        trackCount: spotifyTracks.length,
        playlistId: virtualPlaylistId
    };

    // Use the exact same modal HTML structure as the existing modals
    modal.innerHTML = `
        <div class="download-missing-modal-content" data-context="${contextType}">
            <div class="download-missing-modal-header">
                ${generateDownloadModalHeroSection(heroContext)}
            </div>

            <div class="download-missing-modal-body">
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
                        <span class="track-selection-count" id="track-selection-count-${virtualPlaylistId}">${spotifyTracks.length} / ${spotifyTracks.length} tracks selected</span>
                    </div>
                    <div class="download-tracks-table-container">
                        <table class="download-tracks-table">
                            <thead>
                                <tr>
                                    <th class="track-select-header">
                                        <input type="checkbox" class="track-select-all"
                                               id="select-all-${virtualPlaylistId}" checked
                                               onchange="toggleAllTrackSelections('${virtualPlaylistId}', this.checked)">
                                    </th>
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
                                        <td class="track-select-cell">
                                            <input type="checkbox" class="track-select-cb"
                                                   data-track-index="${index}" checked
                                                   onchange="updateTrackSelectionCount('${virtualPlaylistId}')">
                                        </td>
                                        <td class="track-number">${index + 1}</td>
                                        <td class="track-name" title="${escapeHtml(track.name)}">${escapeHtml(track.name)}</td>
                                        <td class="track-artist" title="${escapeHtml(formatArtists(track.artists))}">${escapeHtml(formatArtists(track.artists))}</td>
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
                    <div class="force-download-toggle-container" style="margin-bottom: 0px; display: flex; flex-direction: column; gap: 8px; align-items: flex-start;">
                        <label class="force-download-toggle">
                            <input type="checkbox" id="force-download-all-${virtualPlaylistId}">
                            <span>Force Download All</span>
                        </label>
                        ${contextType === 'playlist' ? `
                        <label class="force-download-toggle">
                            <input type="checkbox" id="playlist-folder-mode-${virtualPlaylistId}">
                            <span>Organize by Playlist (Downloads/Playlist/Artist - Track.ext)</span>
                        </label>
                        ` : ''}
                    </div>
                    <button class="download-control-btn primary" id="begin-analysis-btn-${virtualPlaylistId}" onclick="startMissingTracksProcess('${virtualPlaylistId}')">
                        Begin Analysis
                    </button>
                    ${_isBeatportPlaylistId(virtualPlaylistId) ? `
                    <button class="download-control-btn sync-to-server-btn" id="sync-server-btn-${virtualPlaylistId}" onclick="syncPlaylistToServer('${virtualPlaylistId}')" ${_isSoulsyncStandalone ? 'style="display:none"' : ''}>
                        Sync to Server
                    </button>` : ''}
                    <button class="download-control-btn" id="add-to-wishlist-btn-${virtualPlaylistId}" onclick="addModalTracksToWishlist('${virtualPlaylistId}')" style="background-color: #9333ea; color: white;">
                        Add to Wishlist
                    </button>
                    <button class="download-control-btn danger" id="cancel-all-btn-${virtualPlaylistId}" onclick="cancelAllOperations('${virtualPlaylistId}')" style="display: none;">
                        Cancel All
                    </button>
                </div>
                <div class="modal-close-section">
                    <button class="download-control-btn export" onclick="exportPlaylistAsM3U('${virtualPlaylistId}')">
                        📋 Export as M3U
                    </button>
                    <button class="download-control-btn secondary" onclick="closeDownloadMissingModal('${virtualPlaylistId}')">Close</button>
                </div>
            </div>
            <!-- Sync to server live progress (below footer, hidden by default) -->
            <div class="modal-sync-progress-area" id="modal-sync-progress-${virtualPlaylistId}" style="display:none;">
                <div class="modal-sync-progress-bar-bg">
                    <div class="modal-sync-progress-bar-fill" id="modal-sync-bar-${virtualPlaylistId}"></div>
                </div>
                <div class="modal-sync-progress-info">
                    <span class="modal-sync-step" id="modal-sync-step-${virtualPlaylistId}">Starting sync...</span>
                    <div class="modal-sync-stats">
                        <span class="matched" id="modal-sync-matched-${virtualPlaylistId}">0 matched</span>
                        <span class="failed" id="modal-sync-failed-${virtualPlaylistId}">0 failed</span>
                    </div>
                    <button class="modal-sync-cancel-btn" id="modal-sync-cancel-${virtualPlaylistId}" onclick="cancelModalSync('${virtualPlaylistId}')">Cancel</button>
                </div>
            </div>
        </div>
    `;

    applyProgressiveTrackRendering(virtualPlaylistId, spotifyTracks.length);
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
        showLibraryDownloadsSection();
        showBeatportDownloadsSection();
        updateDashboardDownloads();
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

// --- Search Bubble Snapshot System ---

async function saveSearchBubbleSnapshot() {
    /**
     * Saves current searchDownloadBubbles state to backend for persistence.
     */
    try {
        // Rate limit saves to avoid spamming backend
        if (saveSearchBubbleSnapshot.lastSaveTime) {
            const timeSinceLastSave = Date.now() - saveSearchBubbleSnapshot.lastSaveTime;
            if (timeSinceLastSave < 2000) {
                console.log('⏱️ Skipping search bubble snapshot save (rate limited)');
                return;
            }
        }

        const bubbleCount = Object.keys(searchDownloadBubbles).length;

        if (bubbleCount === 0) {
            console.log('📸 Skipping snapshot save - no search bubbles to save');
            return;
        }

        console.log(`📸 Saving search bubble snapshot: ${bubbleCount} artists`);

        // Convert search bubbles to plain objects for serialization
        const bubblesToSave = {};
        for (const [artistName, bubbleData] of Object.entries(searchDownloadBubbles)) {
            bubblesToSave[artistName] = {
                artist: bubbleData.artist,
                downloads: bubbleData.downloads.map(d => ({
                    virtualPlaylistId: d.virtualPlaylistId,
                    item: d.item,
                    type: d.type,
                    status: d.status,
                    startTime: d.startTime
                }))
            };
        }

        const response = await fetch('/api/search_bubbles/snapshot', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ bubbles: bubblesToSave })
        });

        const data = await response.json();

        if (data.success) {
            console.log(`✅ Search bubble snapshot saved: ${bubbleCount} artists`);
            saveSearchBubbleSnapshot.lastSaveTime = Date.now();
        } else {
            console.error('❌ Failed to save search bubble snapshot:', data.error);
        }

    } catch (error) {
        console.error('❌ Error saving search bubble snapshot:', error);
    }
}

async function hydrateSearchBubblesFromSnapshot() {
    /**
     * Hydrates search download bubbles from backend snapshot with live status.
     */
    try {
        console.log('🔄 Loading search bubble snapshot from backend...');

        const response = await fetch('/api/search_bubbles/hydrate');
        const data = await response.json();

        if (!data.success) {
            console.error('❌ Failed to load search bubble snapshot:', data.error);
            return;
        }

        const bubbles = data.bubbles || {};
        const stats = data.stats || {};

        if (Object.keys(bubbles).length === 0) {
            console.log('ℹ️ No search bubbles to hydrate');
            return;
        }

        // Clear and restore search bubbles
        searchDownloadBubbles = {};

        for (const [artistName, bubbleData] of Object.entries(bubbles)) {
            searchDownloadBubbles[artistName] = {
                artist: bubbleData.artist,
                downloads: bubbleData.downloads || []
            };

            console.log(`🔄 Hydrated artist: ${artistName} (${bubbleData.downloads.length} downloads)`);

            // Setup monitoring for each download
            for (const download of bubbleData.downloads) {
                if (download.status === 'in_progress') {
                    monitorSearchDownload(artistName, download.virtualPlaylistId);
                }
            }
        }

        const totalArtists = Object.keys(searchDownloadBubbles).length;
        console.log(`✅ Successfully hydrated ${totalArtists} search download bubbles`);

        // Refresh display
        showSearchDownloadBubbles();

    } catch (error) {
        console.error('❌ Error hydrating search bubbles from snapshot:', error);
    }
}

/**
 * Register a new search download for bubble management (grouped by artist)
 */
function registerSearchDownload(item, type, virtualPlaylistId, artistName) {
    console.log(`📝 [REGISTER] Registering search download: ${item.name} (${type}) by ${artistName}`);

    // Initialize artist bubble if it doesn't exist
    if (!searchDownloadBubbles[artistName]) {
        searchDownloadBubbles[artistName] = {
            artist: {
                name: artistName,
                image_url: item.image_url || (item.images && item.images[0]?.url) || null
            },
            downloads: []
        };
    }

    // Add this download to the artist's downloads
    const downloadInfo = {
        virtualPlaylistId: virtualPlaylistId,
        item: item,
        type: type, // 'album' or 'track'
        status: 'in_progress',
        startTime: new Date().toISOString()
    };

    searchDownloadBubbles[artistName].downloads.push(downloadInfo);

    console.log(`✅ [REGISTER] Registered search download for ${artistName} - ${item.name}`);

    // Save snapshot
    saveSearchBubbleSnapshot();

    // Setup monitoring
    monitorSearchDownload(artistName, virtualPlaylistId);

    // Refresh display
    updateSearchDownloadsSection();
}

/**
 * Debounced update for search downloads section
 */
function updateSearchDownloadsSection() {
    if (window.searchUpdateTimeout) {
        clearTimeout(window.searchUpdateTimeout);
    }
    window.searchUpdateTimeout = setTimeout(() => {
        showSearchDownloadBubbles();
        updateDashboardDownloads();
    }, 300);
}

/**
 * Monitor a search download for completion status changes
 */
function monitorSearchDownload(artistName, virtualPlaylistId) {
    const checkCompletion = setInterval(() => {
        const process = activeDownloadProcesses[virtualPlaylistId];

        if (!process || !searchDownloadBubbles[artistName]) {
            clearInterval(checkCompletion);
            return;
        }

        // Find the download in the artist's downloads
        const download = searchDownloadBubbles[artistName].downloads.find(
            d => d.virtualPlaylistId === virtualPlaylistId
        );

        if (!download) {
            clearInterval(checkCompletion);
            return;
        }

        // Update status
        const newStatus = process.status === 'complete' || process.status === 'view_results'
            ? 'view_results'
            : 'in_progress';

        if (download.status !== newStatus) {
            console.log(`🔄 [MONITOR] Status changed for ${download.item.name}: ${download.status} -> ${newStatus}`);
            download.status = newStatus;

            // Save snapshot and refresh
            saveSearchBubbleSnapshot();
            updateSearchDownloadsSection();
        }
    }, 2000);
}

/**
 * Show or update the search downloads bubble section
 */
function showSearchDownloadBubbles() {
    console.log(`🔄 [SHOW] showSearchDownloadBubbles() called`);

    const resultsArea = document.getElementById('enhanced-main-results-area');
    if (!resultsArea) {
        console.log(`⏭️ [SHOW] Skipping - no enhanced-main-results-area found`);
        return;
    }

    // Count active artists (those with downloads)
    const activeArtists = Object.keys(searchDownloadBubbles).filter(artistName =>
        searchDownloadBubbles[artistName].downloads.length > 0
    );

    if (activeArtists.length === 0) {
        // Show placeholder
        resultsArea.innerHTML = `
            <div class="search-results-placeholder">
                <p>Search results will appear here when you select an album or track.</p>
            </div>
        `;
        return;
    }

    // Create bubbles display
    const bubblesHTML = activeArtists.map(artistName =>
        createSearchBubbleCard(searchDownloadBubbles[artistName])
    ).join('');

    resultsArea.innerHTML = `
        <div class="search-bubble-section">
            <div class="search-bubble-header">
                <h3 class="search-bubble-title">Active Downloads</h3>
                <span class="search-bubble-count">${activeArtists.length}</span>
            </div>
            <div class="search-bubble-container" id="search-bubble-container">
                ${bubblesHTML}
            </div>
        </div>
    `;

    console.log(`✅ [SHOW] Displayed ${activeArtists.length} search bubbles`);
}

/**
 * Create HTML for a search bubble card (grouped by artist)
 */
function createSearchBubbleCard(artistBubbleData) {
    const { artist, downloads } = artistBubbleData;
    const activeCount = downloads.filter(d => d.status === 'in_progress').length;
    const completedCount = downloads.filter(d => d.status === 'view_results').length;
    const allCompleted = activeCount === 0 && completedCount > 0;

    console.log(`🔵 [BUBBLE] Creating bubble for ${artist.name}:`, {
        totalDownloads: downloads.length,
        activeCount,
        completedCount,
        allCompleted
    });

    const imageUrl = artist.image_url || '';
    const backgroundStyle = imageUrl ?
        `background-image: url('${escapeHtml(imageUrl)}');` :
        `background: linear-gradient(135deg, rgba(29, 185, 84, 0.3) 0%, rgba(24, 156, 71, 0.2) 100%);`;

    return `
        <div class="search-bubble-card ${allCompleted ? 'all-completed' : ''}"
             data-artist-name="${escapeHtml(artist.name)}"
             onclick="openSearchDownloadModal('${escapeForInlineJs(artist.name)}')"
             title="Click to manage downloads for ${escapeHtml(artist.name)}">
            <div class="search-bubble-image" style="${backgroundStyle}"></div>
            <div class="search-bubble-overlay"></div>
            <div class="search-bubble-content">
                <div class="search-bubble-name">${escapeHtml(artist.name)}</div>
                <div class="search-bubble-status">
                    ${activeCount > 0 ? `${activeCount} active` : ''}
                    ${completedCount > 0 ? `${completedCount} completed` : ''}
                </div>
            </div>
            ${allCompleted ? `
                <div class="bulk-complete-indicator"
                     onclick="event.stopPropagation(); bulkCompleteSearchDownloads('${escapeForInlineJs(artist.name)}')"
                     title="Complete all downloads">
                    <span class="bulk-complete-icon">✅</span>
                </div>
            ` : ''}
        </div>
    `;
}

/**
 * Open modal showing all downloads for an artist
 */
async function openSearchDownloadModal(artistName) {
    const artistBubbleData = searchDownloadBubbles[artistName];
    if (!artistBubbleData || searchDownloadModalOpen) return;

    console.log(`🎵 [MODAL OPEN] Opening search download modal for: ${artistBubbleData.artist.name}`);

    searchDownloadModalOpen = true;

    const modal = document.createElement('div');
    modal.id = 'search-download-management-modal';
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
            : '<div class="artist-download-modal-hero-fallback">🎵</div>'
        }
                        </div>
                        <div class="artist-download-modal-hero-info">
                            <h2 class="artist-download-modal-hero-title">${escapeHtml(artistBubbleData.artist.name)}</h2>
                            <p class="artist-download-modal-hero-subtitle">${artistBubbleData.downloads.length} active download${artistBubbleData.downloads.length !== 1 ? 's' : ''}</p>
                        </div>
                    </div>
                    <span class="artist-download-modal-close" onclick="closeSearchDownloadModal()">&times;</span>
                </div>
            </div>

            <div class="artist-download-modal-body">
                <div class="artist-download-items" id="search-download-items">
                    ${artistBubbleData.downloads.map((download, index) => createSearchDownloadItem(download, index)).join('')}
                </div>
            </div>
        </div>
        <div class="artist-download-modal-overlay" onclick="closeSearchDownloadModal()"></div>
    `;

    document.body.appendChild(modal);
    modal.style.display = 'flex';

    // Start monitoring for status changes
    // Start monitoring for status changes
    monitorSearchDownloadModal(artistName);

    // Lazy load artist image if missing (common for iTunes)
    if (!artistBubbleData.artist.image_url) {
        console.log(`🖼️ Lazy loading modal image for ${artistBubbleData.artist.name} (${artistBubbleData.artist.id})`);
        fetch(`/api/artist/${artistBubbleData.artist.id}/image`)
            .then(response => response.json())
            .then(data => {
                if (data.success && data.image_url) {
                    // Update header background
                    const headerBg = modal.querySelector('.artist-download-modal-hero-bg');
                    if (headerBg) {
                        headerBg.style.backgroundImage = `url('${data.image_url}')`;
                    }

                    // Update avatar
                    const avatarContainer = modal.querySelector('.artist-download-modal-hero-avatar');
                    if (avatarContainer) {
                        avatarContainer.innerHTML = `<img src="${data.image_url}" alt="${artistBubbleData.artist.name}" class="artist-download-modal-hero-image" loading="lazy">`;
                    }

                    // Update artist object in memory
                    artistBubbleData.artist.image_url = data.image_url;
                }
            })
            .catch(err => console.error('❌ Failed to load modal image:', err));
    }
}

/**
 * Create HTML for a download item in the search modal
 */
function createSearchDownloadItem(download, index) {
    const { item, type, status, virtualPlaylistId } = download;
    const buttonText = status === 'view_results' ? 'View Results' : 'View Progress';
    const buttonClass = status === 'view_results' ? 'completed' : 'active';
    const typeLabel = type === 'album' ? 'Album' : type === 'single' ? 'Single' : 'Track';

    return `
        <div class="artist-download-item" data-playlist-id="${virtualPlaylistId}">
            <div class="download-item-artwork">
                ${item.image_url
            ? `<img src="${escapeHtml(item.image_url)}" alt="${escapeHtml(item.name)}" class="download-item-image" loading="lazy">`
            : `<div class="download-item-fallback">
                         ${type === 'album' ? '💿' : '🎵'}
                       </div>`
        }
            </div>
            <div class="download-item-info">
                <div class="download-item-name">${escapeHtml(item.name)}</div>
                <div class="download-item-type">${typeLabel}</div>
            </div>
            <div class="download-item-actions">
                <button class="download-item-btn ${buttonClass}"
                        onclick="reopenDownloadModal('${virtualPlaylistId}')">
                    ${buttonText}
                </button>
            </div>
        </div>
    `;
}

/**
 * Reopen an individual download modal from the artist modal
 */
async function reopenDownloadModal(virtualPlaylistId) {
    const process = activeDownloadProcesses[virtualPlaylistId];

    // If process exists, show the existing modal
    if (process && process.modalElement) {
        console.log(`✅ [REOPEN] Showing existing modal for ${virtualPlaylistId}`);
        closeSearchDownloadModal();
        setTimeout(() => {
            process.modalElement.style.display = 'flex';
        }, 100);
        return;
    }

    // Process doesn't exist (after page refresh) - recreate it
    console.log(`🔄 [REOPEN] Modal not found, recreating for ${virtualPlaylistId}`);

    // Find the download in searchDownloadBubbles
    let downloadData = null;
    for (const artistName in searchDownloadBubbles) {
        const bubble = searchDownloadBubbles[artistName];
        const download = bubble.downloads.find(d => d.virtualPlaylistId === virtualPlaylistId);
        if (download) {
            downloadData = download;
            break;
        }
    }

    if (!downloadData) {
        console.warn(`⚠️ No download data found for ${virtualPlaylistId}`);
        return;
    }

    // Close search modal first
    closeSearchDownloadModal();

    // Recreate the modal based on type
    const { item, type } = downloadData;

    if (type === 'album') {
        // For albums, we need to fetch the tracks
        console.log(`📥 [REOPEN] Recreating album modal for: ${item.name}`);

        // Fetch album tracks (pass name/artist for Hydrabase support)
        showLoadingOverlay(`Loading ${item.name}...`);

        try {
            const _sap2 = new URLSearchParams({ name: item.name || '', artist: item.artist || '' });
            const response = await fetch(`/api/spotify/album/${item.id}?${_sap2}`);
            if (!response.ok) {
                throw new Error('Failed to fetch album tracks');
            }

            const albumData = await response.json();
            if (!albumData.tracks || albumData.tracks.length === 0) {
                throw new Error('No tracks found in album');
            }

            const spotifyTracks = albumData.tracks.map(track => ({
                id: track.id,
                name: track.name,
                artists: track.artists || [{ name: item.artists?.[0]?.name || item.artist || 'Unknown Artist' }],
                album: {
                    name: item.name,
                    images: item.image_url ? [{ url: item.image_url }] : []
                },
                duration_ms: track.duration_ms || 0
            }));

            hideLoadingOverlay();

            // Open the modal
            await openDownloadMissingModalForArtistAlbum(
                virtualPlaylistId,
                item.name,
                spotifyTracks,
                item,
                { name: item.artists?.[0]?.name || item.artist || 'Unknown Artist' },
                false // Don't show loading overlay again
            );

            // Sync with backend to check for active batch process
            const process = activeDownloadProcesses[virtualPlaylistId];
            if (process) {
                try {
                    const processResponse = await fetch('/api/active-processes');
                    if (processResponse.ok) {
                        const processData = await processResponse.json();
                        const activeProcess = processData.active_processes?.find(p => p.playlist_id === virtualPlaylistId);

                        if (activeProcess) {
                            console.log(`📡 [REOPEN] Found active batch for album: ${activeProcess.batch_id}`);
                            process.status = 'running';
                            process.batchId = activeProcess.batch_id;

                            // Update UI to show running state
                            const beginBtn = document.getElementById(`begin-analysis-btn-${virtualPlaylistId}`);
                            const cancelBtn = document.getElementById(`cancel-all-btn-${virtualPlaylistId}`);
                            if (beginBtn) beginBtn.style.display = 'none';
                            if (cancelBtn) cancelBtn.style.display = 'inline-block';

                            // Start polling for live updates
                            startModalDownloadPolling(virtualPlaylistId);
                        }
                    }
                } catch (err) {
                    console.warn('Could not check for active processes:', err);
                }
            }

        } catch (error) {
            hideLoadingOverlay();
            showToast(`Failed to load album: ${error.message}`, 'error');
            console.error('Error loading album:', error);
        }

    } else {
        // For tracks, create enriched track and open modal
        console.log(`🎵 [REOPEN] Recreating track modal for: ${item.name}`);

        const enrichedTrack = {
            id: item.id,
            name: item.name,
            artists: item.artists || [{ name: item.artist || 'Unknown Artist' }],
            album: item.album || {
                name: item.album?.name || 'Unknown Album',
                images: item.image_url ? [{ url: item.image_url }] : []
            },
            duration_ms: item.duration_ms || 0
        };

        await openDownloadMissingModalForYouTube(
            virtualPlaylistId,
            `${enrichedTrack.name} - ${enrichedTrack.artists[0].name || enrichedTrack.artists[0]}`,
            [enrichedTrack]
        );

        // Sync with backend to check for active batch process
        const process = activeDownloadProcesses[virtualPlaylistId];
        if (process) {
            try {
                const processResponse = await fetch('/api/active-processes');
                if (processResponse.ok) {
                    const processData = await processResponse.json();
                    const activeProcess = processData.active_processes?.find(p => p.playlist_id === virtualPlaylistId);

                    if (activeProcess) {
                        console.log(`📡 [REOPEN] Found active batch for track: ${activeProcess.batch_id}`);
                        process.status = 'running';
                        process.batchId = activeProcess.batch_id;

                        // Update UI to show running state
                        const beginBtn = document.getElementById(`begin-analysis-btn-${virtualPlaylistId}`);
                        const cancelBtn = document.getElementById(`cancel-all-btn-${virtualPlaylistId}`);
                        if (beginBtn) beginBtn.style.display = 'none';
                        if (cancelBtn) cancelBtn.style.display = 'inline-block';

                        // Start polling for live updates
                        startModalDownloadPolling(virtualPlaylistId);
                    }
                }
            } catch (err) {
                console.warn('Could not check for active processes:', err);
            }
        }
    }
}

/**
 * Monitor search download modal for status changes
 */
function monitorSearchDownloadModal(artistName) {
    const updateModal = () => {
        if (!searchDownloadModalOpen) return;

        const modal = document.getElementById('search-download-management-modal');
        const itemsContainer = document.getElementById('search-download-items');

        if (!modal || !itemsContainer || !searchDownloadBubbles[artistName]) return;

        const downloads = searchDownloadBubbles[artistName].downloads;

        // If no downloads at all, close modal
        if (downloads.length === 0) {
            closeSearchDownloadModal();
            return;
        }

        // Update modal content and sync status with active processes
        let statusChanged = false;
        itemsContainer.innerHTML = downloads.map((download, index) => {
            const process = activeDownloadProcesses[download.virtualPlaylistId];

            // Only update status if process exists (otherwise keep current status)
            if (process) {
                const newStatus = process.status === 'complete' || process.status === 'view_results'
                    ? 'view_results'
                    : 'in_progress';

                if (download.status !== newStatus) {
                    console.log(`🔄 [MODAL MONITOR] Status changed: ${download.item.name} ${download.status} -> ${newStatus}`);
                    download.status = newStatus;
                    statusChanged = true;
                }
            }

            return createSearchDownloadItem(download, index);
        }).join('');

        // If status changed, refresh bubble display and save
        if (statusChanged) {
            updateSearchDownloadsSection();
            saveSearchBubbleSnapshot();
        }

        // Continue monitoring
        setTimeout(updateModal, 2000);
    };

    setTimeout(updateModal, 1000);
}

/**
 * Close the search download modal
 */
function closeSearchDownloadModal() {
    const modal = document.getElementById('search-download-management-modal');
    if (modal) {
        modal.style.display = 'none';
        if (modal.parentElement) {
            modal.parentElement.removeChild(modal);
        }
    }
    searchDownloadModalOpen = false;
}

/**
 * Bulk complete all downloads for an artist (called when user clicks green checkmark)
 */
function bulkCompleteSearchDownloads(artistName) {
    console.log(`🎯 Bulk completing downloads for artist: ${artistName}`);

    const artistBubbleData = searchDownloadBubbles[artistName];
    if (!artistBubbleData) {
        console.warn(`❌ No artist bubble data found for ${artistName}`);
        return;
    }

    // Find all completed downloads
    const completedDownloads = artistBubbleData.downloads.filter(d => d.status === 'view_results');
    console.log(`📋 Found ${completedDownloads.length} completed downloads to close:`,
        completedDownloads.map(d => d.item.name));

    if (completedDownloads.length === 0) {
        console.warn(`⚠️ No completed downloads found for bulk close`);
        showToast('No completed downloads to close', 'info');
        return;
    }

    // Close all completed modals
    completedDownloads.forEach(download => {
        const process = activeDownloadProcesses[download.virtualPlaylistId];
        if (process && process.modalElement) {
            console.log(`🗑️ Closing modal for: ${download.item.name}`);
            closeDownloadMissingModal(download.virtualPlaylistId);
        } else {
            // No modal open — clean up the bubble entry directly
            console.log(`🧹 Direct cleanup (no modal) for: ${download.item.name}`);
            cleanupSearchDownload(download.virtualPlaylistId);
        }
    });

    showToast(`Completed ${completedDownloads.length} downloads for ${artistBubbleData.artist.name}`, 'success');
}

/**
 * Cleanup search download when modal is closed
 */
function cleanupSearchDownload(virtualPlaylistId) {
    console.log(`🔍 [CLEANUP] Looking for search download to cleanup: ${virtualPlaylistId}`);

    // Find which artist this download belongs to
    for (const artistName in searchDownloadBubbles) {
        const downloads = searchDownloadBubbles[artistName].downloads;
        const downloadIndex = downloads.findIndex(d => d.virtualPlaylistId === virtualPlaylistId);

        if (downloadIndex !== -1) {
            console.log(`🧹 [CLEANUP] Found download in artist ${artistName}: ${downloads[downloadIndex].item.name}`);

            // Remove this download
            downloads.splice(downloadIndex, 1);
            console.log(`🗑️ [CLEANUP] Removed download from ${artistName}'s bubble`);

            // If no more downloads for this artist, remove the bubble
            if (downloads.length === 0) {
                delete searchDownloadBubbles[artistName];
                console.log(`🧹 [CLEANUP] No more downloads - removed artist bubble: ${artistName}`);
            }

            // Save snapshot and refresh
            saveSearchBubbleSnapshot();
            updateSearchDownloadsSection();

            return;
        }
    }

    console.log(`⚠️ [CLEANUP] No matching search download found for: ${virtualPlaylistId}`);
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
 * Show download bubbles on the Library page (mirrors showArtistDownloadsSection)
 */
function showLibraryDownloadsSection() {
    const libraryContent = document.querySelector('.library-content');
    if (!libraryContent) return;

    let downloadsSection = document.getElementById('library-downloads-section');

    // Create section if it doesn't exist
    if (!downloadsSection) {
        downloadsSection = document.createElement('div');
        downloadsSection.id = 'library-downloads-section';
        downloadsSection.className = 'artist-downloads-section';

        // Insert before the artist grid
        const artistGrid = document.getElementById('library-artists-grid');
        if (artistGrid) {
            libraryContent.insertBefore(downloadsSection, artistGrid);
        }
    }

    // Count active artists (reuses artistDownloadBubbles state)
    const activeArtists = Object.keys(artistDownloadBubbles).filter(artistId =>
        artistDownloadBubbles[artistId].downloads.length > 0
    );

    if (activeArtists.length === 0) {
        downloadsSection.style.display = 'none';
        return;
    }

    downloadsSection.style.display = 'block';
    downloadsSection.innerHTML = `
        <div class="artist-downloads-header">
            <h3 class="artist-downloads-title">Current Downloads</h3>
            <p class="artist-downloads-subtitle">Active download processes</p>
        </div>
        <div class="artist-bubble-container">
            ${activeArtists.map(artistId => createArtistBubbleCard(artistDownloadBubbles[artistId])).join('')}
        </div>
    `;

    // Add click handlers + glow effects
    activeArtists.forEach(artistId => {
        const bubbleCard = downloadsSection.querySelector(`[data-artist-id="${artistId}"]`);
        if (bubbleCard) {
            bubbleCard.addEventListener('click', () => openArtistDownloadModal(artistId));
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
            // No modal open — clean up the bubble entry directly
            console.log(`🧹 Direct cleanup (no modal) for: ${download.album.name}`);
            cleanupArtistDownload(download.virtualPlaylistId);
        }
    });

    showToast(`Completed ${completedDownloads.length} downloads for ${artistBubbleData.artist.name}`, 'success');
}

// ========================================
// Beatport Download Bubbles
// ========================================

/**
 * Register a new Beatport chart download for bubble management
 */
function registerBeatportDownload(chartName, chartImage, virtualPlaylistId) {
    console.log(`📝 Registering Beatport download: ${chartName}`);

    // Use chart name as key (sanitised)
    const chartKey = chartName.replace(/[^a-zA-Z0-9]/g, '_').toLowerCase();

    if (!beatportDownloadBubbles[chartKey]) {
        beatportDownloadBubbles[chartKey] = {
            chart: { name: chartName, image: chartImage || '' },
            downloads: []
        };
    }

    beatportDownloadBubbles[chartKey].downloads.push({
        virtualPlaylistId: virtualPlaylistId,
        status: 'in_progress',
        startTime: new Date()
    });

    updateBeatportDownloadsSection();
    saveBeatportBubbleSnapshot();
    monitorBeatportDownload(chartKey, virtualPlaylistId);
}

/**
 * Debounced update for Beatport downloads section
 */
function updateBeatportDownloadsSection() {
    if (beatportDownloadsUpdateTimeout) {
        clearTimeout(beatportDownloadsUpdateTimeout);
    }
    beatportDownloadsUpdateTimeout = setTimeout(() => {
        showBeatportDownloadsSection();
        updateDashboardDownloads();
    }, 300);
}

/**
 * Render Beatport download bubbles on the Beatport page
 */
function showBeatportDownloadsSection() {
    const downloadsSection = document.getElementById('beatport-downloads-section');
    if (!downloadsSection) return;

    const activeCharts = Object.keys(beatportDownloadBubbles).filter(key =>
        beatportDownloadBubbles[key].downloads.length > 0
    );

    if (activeCharts.length === 0) {
        downloadsSection.style.display = 'none';
        return;
    }

    downloadsSection.style.display = 'block';
    downloadsSection.innerHTML = `
        <div class="artist-downloads-header">
            <h3 class="artist-downloads-title">Beatport Downloads</h3>
            <p class="artist-downloads-subtitle">Active chart download processes</p>
        </div>
        <div class="artist-bubble-container" id="beatport-bubble-container">
            ${activeCharts.map(key => createBeatportBubbleCard(beatportDownloadBubbles[key])).join('')}
        </div>
    `;

    // Attach click handlers + glow
    activeCharts.forEach(chartKey => {
        const card = downloadsSection.querySelector(`[data-chart-key="${chartKey}"]`);
        if (card) {
            card.addEventListener('click', () => openBeatportBubbleModal(chartKey));
            const chartImage = beatportDownloadBubbles[chartKey].chart.image;
            if (chartImage) {
                extractImageColors(chartImage, (colors) => {
                    applyDynamicGlow(card, colors);
                });
            }
        }
    });
}

/**
 * Create HTML for a Beatport bubble card (reuses artist bubble CSS)
 */
function createBeatportBubbleCard(bubbleData) {
    const { chart, downloads } = bubbleData;
    const chartKey = chart.name.replace(/[^a-zA-Z0-9]/g, '_').toLowerCase();
    const activeCount = downloads.filter(d => d.status === 'in_progress').length;
    const completedCount = downloads.filter(d => d.status === 'view_results').length;
    const allCompleted = activeCount === 0 && completedCount > 0;

    const backgroundStyle = chart.image
        ? `background-image: url('${chart.image}');`
        : `background: linear-gradient(135deg, rgba(0, 210, 120, 0.3) 0%, rgba(0, 170, 100, 0.2) 100%);`;

    return `
        <div class="artist-bubble-card ${allCompleted ? 'all-completed' : ''}"
             data-chart-key="${chartKey}"
             title="Click to manage downloads for ${escapeHtml(chart.name)}">
            <div class="artist-bubble-image" style="${backgroundStyle}"></div>
            <div class="artist-bubble-overlay"></div>
            <div class="artist-bubble-content">
                <div class="artist-bubble-name">${escapeHtml(chart.name)}</div>
                <div class="artist-bubble-status">
                    ${activeCount > 0 ? `${activeCount} active` : ''}
                    ${completedCount > 0 ? `${completedCount} completed` : ''}
                </div>
            </div>
            ${allCompleted ? `
                <div class="bulk-complete-indicator"
                     onclick="event.stopPropagation(); bulkCompleteBeatportDownloads('${chartKey}')"
                     title="Complete all downloads">
                    <span class="bulk-complete-icon">✅</span>
                </div>
            ` : ''}
        </div>
    `;
}

/**
 * Monitor a Beatport download for completion
 */
function monitorBeatportDownload(chartKey, virtualPlaylistId) {
    const checkStatus = () => {
        const process = activeDownloadProcesses[virtualPlaylistId];
        if (!process || !beatportDownloadBubbles[chartKey]) return;

        const download = beatportDownloadBubbles[chartKey].downloads.find(d => d.virtualPlaylistId === virtualPlaylistId);
        if (!download) return;

        if (process.status === 'complete' && download.status === 'in_progress') {
            download.status = 'view_results';
            console.log(`✅ Beatport download completed for ${beatportDownloadBubbles[chartKey].chart.name}`);

            updateBeatportDownloadsSection();
            saveBeatportBubbleSnapshot();

            const allCompleted = beatportDownloadBubbles[chartKey].downloads.every(d => d.status === 'view_results');
            if (allCompleted) {
                console.log(`🟢 All Beatport downloads completed for ${beatportDownloadBubbles[chartKey].chart.name}`);
                setTimeout(updateBeatportDownloadsSection, 100);
            }
        }

        if (process.status !== 'complete') {
            setTimeout(checkStatus, 2000);
        }
    };

    setTimeout(checkStatus, 1000);
}

/**
 * Open the download modal for a Beatport chart bubble
 */
function openBeatportBubbleModal(chartKey) {
    const bubbleData = beatportDownloadBubbles[chartKey];
    if (!bubbleData) return;

    // Find the first download with an active modal
    for (const download of bubbleData.downloads) {
        const process = activeDownloadProcesses[download.virtualPlaylistId];
        if (process && process.modalElement) {
            process.modalElement.style.display = 'flex';
            if (process.status === 'complete') {
                showToast('Review download results and click "Close" to finish.', 'info');
            }
            return;
        }
    }

    showToast('No active download modal found for this chart', 'info');
}

/**
 * Bulk complete all downloads for a Beatport chart
 */
function bulkCompleteBeatportDownloads(chartKey) {
    console.log(`🎯 Bulk completing Beatport downloads for chart: ${chartKey}`);

    const bubbleData = beatportDownloadBubbles[chartKey];
    if (!bubbleData) return;

    const completedDownloads = bubbleData.downloads.filter(d => d.status === 'view_results');
    if (completedDownloads.length === 0) {
        showToast('No completed downloads to close', 'info');
        return;
    }

    completedDownloads.forEach(download => {
        const process = activeDownloadProcesses[download.virtualPlaylistId];
        if (process && process.modalElement) {
            closeDownloadMissingModal(download.virtualPlaylistId);
        } else {
            cleanupBeatportDownload(download.virtualPlaylistId);
        }
    });

    showToast(`Completed ${completedDownloads.length} downloads for ${bubbleData.chart.name}`, 'success');
}

/**
 * Clean up a Beatport download when its modal is closed
 */
function cleanupBeatportDownload(virtualPlaylistId) {
    console.log(`🔍 [CLEANUP] Looking for Beatport download to cleanup: ${virtualPlaylistId}`);

    for (const chartKey in beatportDownloadBubbles) {
        const downloads = beatportDownloadBubbles[chartKey].downloads;
        const downloadIndex = downloads.findIndex(d => d.virtualPlaylistId === virtualPlaylistId);

        if (downloadIndex !== -1) {
            downloads.splice(downloadIndex, 1);
            console.log(`🧹 [CLEANUP] Removed Beatport download from ${chartKey}. Remaining: ${downloads.length}`);

            if (downloads.length === 0) {
                delete beatportDownloadBubbles[chartKey];
                console.log(`🧹 [CLEANUP] No more downloads - removed Beatport bubble: ${chartKey}`);
            }

            updateBeatportDownloadsSection();
            saveBeatportBubbleSnapshot();
            return;
        }
    }
}

// --- Beatport Bubble Snapshot System ---

let beatportSnapshotSaveTimeout = null;

async function saveBeatportBubbleSnapshot() {
    if (beatportSnapshotSaveTimeout) {
        clearTimeout(beatportSnapshotSaveTimeout);
    }

    beatportSnapshotSaveTimeout = setTimeout(async () => {
        try {
            const bubbleCount = Object.keys(beatportDownloadBubbles).length;
            if (bubbleCount === 0) return;

            const cleanBubbles = {};
            for (const [chartKey, bubbleData] of Object.entries(beatportDownloadBubbles)) {
                cleanBubbles[chartKey] = {
                    chart: bubbleData.chart,
                    downloads: bubbleData.downloads.map(d => ({
                        virtualPlaylistId: d.virtualPlaylistId,
                        status: d.status,
                        startTime: d.startTime instanceof Date ? d.startTime.toISOString() : d.startTime
                    }))
                };
            }

            const response = await fetch('/api/beatport_bubbles/snapshot', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ bubbles: cleanBubbles })
            });

            const data = await response.json();
            if (data.success) {
                console.log(`✅ Beatport bubble snapshot saved: ${bubbleCount} charts`);
            }
        } catch (error) {
            console.error('❌ Error saving Beatport bubble snapshot:', error);
        }
    }, 1000);
}

async function hydrateBeatportBubblesFromSnapshot() {
    try {
        console.log('🔄 Loading Beatport bubble snapshot from backend...');

        const signal = getBeatportContentSignal();
        const response = await fetch('/api/beatport_bubbles/hydrate', signal ? { signal } : undefined);
        const data = await response.json();

        if (!data.success) {
            console.error('❌ Failed to load Beatport bubble snapshot:', data.error);
            return;
        }

        const bubbles = data.bubbles || {};
        if (Object.keys(bubbles).length === 0) {
            console.log('ℹ️ No Beatport bubbles to hydrate');
            return;
        }

        beatportDownloadBubbles = {};

        for (const [chartKey, bubbleData] of Object.entries(bubbles)) {
            beatportDownloadBubbles[chartKey] = {
                chart: bubbleData.chart,
                downloads: bubbleData.downloads.map(d => ({
                    virtualPlaylistId: d.virtualPlaylistId,
                    status: d.status,
                    startTime: new Date(d.startTime)
                }))
            };

            for (const download of bubbleData.downloads) {
                if (download.status === 'in_progress') {
                    monitorBeatportDownload(chartKey, download.virtualPlaylistId);
                }
            }
        }

        updateBeatportDownloadsSection();
        console.log(`✅ Hydrated ${Object.keys(beatportDownloadBubbles).length} Beatport download bubbles`);
    } catch (error) {
        if (error && error.name === 'AbortError') {
            console.log('⏹ Beatport bubble hydration aborted');
            return;
        }
        console.error('❌ Error hydrating Beatport bubbles:', error);
    }
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
        callback(getAccentFallbackColors()); // Fallback to Spotify green
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

        img.onload = function () {
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
                    callback(getAccentFallbackColors()); // Fallback
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
                callback(getAccentFallbackColors());
            }
        };

        img.onerror = function () {
            callback(getAccentFallbackColors()); // Fallback on error
        };

        img.src = imageUrl;

    } catch (error) {
        console.warn('Image color extraction error:', error);
        callback(getAccentFallbackColors());
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

async function _forceServiceStatusRefresh() {
    // Force an immediate status refresh (bypasses WebSocket check) — used after settings save
    try {
        const response = await fetch('/status');
        if (!response.ok) return;
        const data = await response.json();
        handleServiceStatusUpdate(data);
    } catch (error) {
        console.warn('Could not force service status refresh:', error);
    }
}

async function fetchAndUpdateServiceStatus() {
    if (document.hidden) return; // Skip polling when tab is not visible
    if (socketConnected) return; // WebSocket is pushing updates — skip HTTP poll
    try {
        const response = await fetch('/status');
        if (!response.ok) return;

        const data = await response.json();

        // Cache for library status card
        _lastServiceStatus = data;

        // Update service status indicators and text (dashboard)
        updateServiceStatus('spotify', data.spotify);
        updateServiceStatus('media-server', data.media_server);
        updateServiceStatus('soulseek', data.soulseek);

        // Update sidebar service status indicators
        updateSidebarServiceStatus('spotify', data.spotify);
        updateSidebarServiceStatus('media-server', data.media_server);
        updateSidebarServiceStatus('soulseek', data.soulseek);

        // Update downloads nav badge
        if (data.active_downloads !== undefined) _updateDlNavBadge(data.active_downloads);

        // Hide sync buttons (not the page) for standalone mode
        const isSoulsyncStandalone2 = data.media_server?.type === 'soulsync';
        _isSoulsyncStandalone = isSoulsyncStandalone2;
        document.querySelectorAll('.sync-to-server-btn, [id$="-sync-btn"], [onclick*="startPlaylistSync"], [onclick*="syncPlaylistToServer"], [onclick*="startDecadeSync"]').forEach(btn => {
            if (isSoulsyncStandalone2) {
                btn.dataset.hiddenByStandalone = '1';
                btn.style.display = 'none';
            } else if (btn.dataset.hiddenByStandalone) {
                delete btn.dataset.hiddenByStandalone;
                btn.style.display = '';
            }
        });

        // Update enrichment service cards
        if (data.enrichment) renderEnrichmentCards(data.enrichment);

        // Check for Spotify rate limit
        if (data.spotify && data.spotify.rate_limited && data.spotify.rate_limit) {
            handleSpotifyRateLimit(data.spotify.rate_limit);
        } else if (_spotifyRateLimitShown) {
            handleSpotifyRateLimit(null);
        }

    } catch (error) {
        console.warn('Could not fetch service status:', error);
    }
}

function updateServiceStatus(service, statusData) {
    const indicator = document.getElementById(`${service}-status-indicator`);
    const statusText = document.getElementById(`${service}-status-text`);

    if (indicator && statusText) {
        if (service === 'spotify' && (statusData.rate_limited || statusData.post_ban_cooldown)) {
            indicator.className = 'service-card-indicator rate-limited';
            const remaining = statusData.rate_limited
                ? formatRateLimitDuration(statusData.rate_limit?.remaining_seconds || 0)
                : formatRateLimitDuration(statusData.post_ban_cooldown);
            const phase = statusData.rate_limited ? 'paused' : 'recovering';
            const fallbackLabel = statusData.source === 'deezer' ? 'Deezer' : 'iTunes';
            statusText.textContent = `${fallbackLabel} (Spotify ${phase} \u2014 ${remaining})`;
            statusText.className = 'service-card-status-text rate-limited';
        } else if (statusData.connected) {
            indicator.className = 'service-card-indicator connected';
            statusText.textContent = `Connected (${statusData.response_time}ms)`;
            statusText.className = 'service-card-status-text connected';
        } else {
            indicator.className = 'service-card-indicator disconnected';
            statusText.textContent = 'Disconnected';
            statusText.className = 'service-card-status-text disconnected';
        }
    }

    // Update music source title based on active source
    if (service === 'spotify' && statusData.source) {
        const musicSourceTitleElement = document.getElementById('music-source-title');
        if (musicSourceTitleElement) {
            const sourceName = statusData.source === 'spotify' ? 'Spotify' : statusData.source === 'deezer' ? 'Deezer' : statusData.source === 'discogs' ? 'Discogs' : 'iTunes';
            musicSourceTitleElement.textContent = sourceName;
            currentMusicSourceName = sourceName;
        }

        // Show/hide Spotify disconnect button based on connection state
        const disconnectBtn = document.getElementById('spotify-disconnect-btn');
        if (disconnectBtn) {
            disconnectBtn.style.display = statusData.source === 'spotify' ? '' : 'none';
        }
    }

    // Update download source title on dashboard card
    if (service === 'soulseek' && statusData.source) {
        const sourceNames = { soulseek: 'Soulseek', youtube: 'YouTube', tidal: 'Tidal', qobuz: 'Qobuz', hifi: 'HiFi', deezer_dl: 'Deezer', hybrid: 'Hybrid' };
        const displayName = sourceNames[statusData.source] || 'Soulseek';
        const titleEl = document.getElementById('download-source-title');
        if (titleEl) titleEl.textContent = displayName;
    }
}

function updateSidebarServiceStatus(service, statusData) {
    const indicator = document.getElementById(`${service}-indicator`);
    if (indicator) {
        const dot = indicator.querySelector('.status-dot');
        const nameElement = indicator.querySelector('.status-name');

        if (dot) {
            if (service === 'spotify' && (statusData.rate_limited || statusData.post_ban_cooldown)) {
                dot.className = 'status-dot rate-limited';
                dot.title = statusData.rate_limited
                    ? `Spotify paused \u2014 ${formatRateLimitDuration(statusData.rate_limit?.remaining_seconds || 0)} remaining`
                    : `Spotify recovering \u2014 ${formatRateLimitDuration(statusData.post_ban_cooldown)} cooldown`;
            } else if (statusData.connected) {
                dot.className = 'status-dot connected';
                dot.title = '';
            } else {
                dot.className = 'status-dot disconnected';
                dot.title = '';
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

        // Update music source name in sidebar based on active source
        if (service === 'spotify' && statusData.source) {
            const musicSourceNameElement = document.getElementById('music-source-name');
            if (musicSourceNameElement) {
                const sourceName = statusData.source === 'spotify' ? 'Spotify' : statusData.source === 'deezer' ? 'Deezer' : statusData.source === 'discogs' ? 'Discogs' : 'iTunes';
                musicSourceNameElement.textContent = sourceName;
            }
        }

        // Update download source name based on configured mode
        if (service === 'soulseek' && statusData.source) {
            const sourceNames = { soulseek: 'Soulseek', youtube: 'YouTube', tidal: 'Tidal', qobuz: 'Qobuz', hifi: 'HiFi', deezer_dl: 'Deezer', hybrid: 'Hybrid' };
            const displayName = sourceNames[statusData.source] || 'Soulseek';
            const sidebarName = document.getElementById('download-source-name');
            if (sidebarName) sidebarName.textContent = displayName;
        }
    }
}

function renderEnrichmentCards(enrichment) {
    const grid = document.getElementById('enrichment-status-grid');
    if (!grid || !enrichment) return;

    // Service display order
    const serviceOrder = [
        'musicbrainz', 'spotify_enrichment', 'itunes_enrichment', 'deezer_enrichment',
        'tidal_enrichment', 'qobuz_enrichment', 'lastfm', 'genius', 'audiodb',
        'acoustid', 'listenbrainz'
    ];

    // Map service keys to their settings page selector for click-to-configure
    const settingsSelectors = {
        'spotify_enrichment': '.spotify-title',
        'tidal_enrichment': '.tidal-title',
        'qobuz_enrichment': '.qobuz-title',
        'lastfm': '.lastfm-title',
        'genius': '.genius-title',
        'acoustid': '.acoustid-title',
        'listenbrainz': '.listenbrainz-title',
    };

    const chips = [];
    for (const key of serviceOrder) {
        const svc = enrichment[key];
        if (!svc) continue;

        // Determine status class and text
        let statusClass, statusLabel;
        if ('running' in svc) {
            if (!svc.configured) {
                statusClass = 'not-configured';
                statusLabel = 'Set up';
            } else if (svc.paused) {
                statusClass = 'paused';
                statusLabel = svc.yield_reason === 'downloads' ? 'Yielding' : 'Paused';
            } else if (svc.running) {
                statusClass = svc.idle ? 'idle' : 'running';
                statusLabel = svc.idle ? 'Idle' : 'Running';
            } else {
                statusClass = 'stopped';
                statusLabel = 'Stopped';
            }
        } else {
            statusClass = svc.configured ? 'running' : 'not-configured';
            statusLabel = svc.configured ? 'Ready' : 'Set up';
        }

        const selector = settingsSelectors[key];
        const clickAttr = selector
            ? `onclick="navigateToPage('settings'); setTimeout(() => { switchSettingsTab('connections'); setTimeout(() => { const el = document.querySelector('${selector}'); if (el) el.scrollIntoView({ behavior: 'smooth', block: 'center' }); }, 100); }, 50);"`
            : '';

        // Build activity display — human-readable, not cryptic numbers
        let activityHtml = '';
        let metaHtml = '';
        const isSpotify = key === 'spotify_enrichment';

        if ('running' in svc && svc.configured) {
            const c1h = svc.calls_1h || 0;
            const c24h = svc.calls_24h || 0;

            if (isSpotify && svc.daily_budget) {
                // Spotify: show budget usage prominently
                const b = svc.daily_budget;
                const pct = Math.min(100, Math.round((b.used / b.limit) * 100));
                const barClass = b.exhausted ? 'exhausted' : pct > 80 ? 'high' : '';
                activityHtml = `<span class="enrichment-chip-activity">${b.used.toLocaleString()} / ${b.limit.toLocaleString()}</span>`;
                metaHtml = `<div class="enrichment-chip-budget">
                    <div class="enrichment-chip-budget-bar ${barClass}" style="width: ${pct}%"></div>
                </div>`;
            } else if (c24h > 0) {
                // Other services: show 24h count
                activityHtml = `<span class="enrichment-chip-activity">${c24h.toLocaleString()} / 24h</span>`;
            }
        }

        // Tooltip: full details including 1h breakdown
        let tooltipLines = [svc.name + ' — ' + statusLabel];
        if ('running' in svc && svc.configured) {
            const c1h = svc.calls_1h || 0;
            const c24h = svc.calls_24h || 0;
            if (c24h > 0 || c1h > 0) tooltipLines.push('Last hour: ' + c1h + ' · Last 24h: ' + c24h);
        }
        if (isSpotify && svc.daily_budget) {
            const b = svc.daily_budget;
            tooltipLines.push('Daily budget: ' + b.used + ' / ' + b.limit + (b.exhausted ? ' (exhausted)' : ''));
        }
        if (selector && statusClass === 'not-configured') {
            tooltipLines = ['Click to configure in Settings'];
        }

        const statusDisplay = statusClass === 'not-configured' && selector ? 'Configure →' : statusLabel;

        chips.push(`
            <div class="enrichment-chip status-${statusClass}" ${clickAttr} title="${tooltipLines.join('\n')}">
                <span class="enrichment-chip-dot"></span>
                <span class="enrichment-chip-name">${svc.name}</span>
                ${activityHtml}
                <span class="enrichment-chip-status">${statusDisplay}</span>
                ${metaHtml}
            </div>
        `);
    }

    grid.innerHTML = chips.join('');
}

// ===============================
