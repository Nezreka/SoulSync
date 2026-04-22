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
