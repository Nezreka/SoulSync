// == DISCOVER PAGE                          ==
// ============================================

let discoverHeroIndex = 0;
let discoverHeroArtists = [];
let discoverHeroInterval = null;
let discoverPageInitialized = false;

// Store discover playlist tracks for download/sync functionality
let discoverReleaseRadarTracks = [];
let discoverWeeklyTracks = [];
let discoverRecentAlbums = [];
let discoverSeasonalAlbums = [];
let discoverSeasonalTracks = [];
let currentSeasonKey = null;

// Personalized playlists storage
let personalizedRecentlyAdded = [];
let personalizedTopTracks = [];
let personalizedForgottenFavorites = [];
let personalizedPopularPicks = [];
let personalizedHiddenGems = [];
let personalizedDailyMixes = [];
let personalizedDiscoveryShuffle = [];
let personalizedFamiliarFavorites = [];
let buildPlaylistSelectedArtists = [];

async function loadDiscoverPage() {
    console.log('Loading discover page...');

    // Load all sections
    await Promise.all([
        loadDiscoverHero(),
        loadYourArtists(),
        loadYourAlbums(),
        loadSpotifyLibrarySection(),
        loadDiscoverRecentReleases(),
        loadSeasonalContent(),  // Seasonal discovery
        loadPersonalizedRecentlyAdded(),  // NEW: Recently added from library
        // loadPersonalizedDailyMixes(),  // NEW: Daily Mix playlists (HIDDEN)
        loadDiscoverReleaseRadar(),
        loadDiscoverWeekly(),
        loadPersonalizedPopularPicks(),  // NEW: Popular picks from discovery pool
        loadPersonalizedHiddenGems(),  // NEW: Hidden gems from discovery pool
        loadPersonalizedTopTracks(),  // NEW: Your top tracks
        loadPersonalizedForgottenFavorites(),  // NEW: Forgotten favorites
        loadDiscoveryShuffle(),  // NEW: Discovery Shuffle
        loadFamiliarFavorites(),  // NEW: Familiar Favorites
        loadBecauseYouListenTo(),  // Personalized by listening stats
        loadCacheUndiscoveredAlbums(),  // From metadata cache
        loadCacheGenreNewReleases(),    // From metadata cache
        loadCacheLabelExplorer(),       // From metadata cache
        loadCacheDeepCuts(),            // From metadata cache
        loadCacheGenreExplorer(),       // From metadata cache
        initializeLastfmRadioSection(),  // Last.fm Radio section (gated on API key)
        initializeListenBrainzTabs(),  // ListenBrainz playlists (tabbed)
        loadDecadeBrowserTabs(),  // Time Machine (tabbed by decade)
        loadGenreBrowserTabs(),  // Browse by Genre (tabbed by genre)
        loadListenBrainzPlaylistsFromBackend(),  // Load ListenBrainz playlist states for persistence
        loadDiscoveryBlacklist()  // Blocked artists list
    ]);

    // Check for active syncs after page load
    checkForActiveDiscoverSyncs();
}

async function checkForActiveDiscoverSyncs() {
    // Check if Fresh Tape sync is active
    try {
        const releaseRadarResponse = await fetch('/api/sync/status/discover_release_radar');
        if (releaseRadarResponse.ok) {
            const data = await releaseRadarResponse.json();
            if (data.status === 'syncing' || data.status === 'starting') {
                console.log('🔄 Resuming Fresh Tape sync polling after page refresh');

                // Show status display
                const statusDisplay = document.getElementById('release-radar-sync-status');
                if (statusDisplay) {
                    statusDisplay.style.display = 'block';
                }

                // Disable button
                const syncButton = document.getElementById('release-radar-sync-btn');
                if (syncButton) {
                    syncButton.disabled = true;
                    syncButton.style.opacity = '0.5';
                    syncButton.style.cursor = 'not-allowed';
                }

                // Resume polling
                startDiscoverSyncPolling('release_radar', 'discover_release_radar');
            }
        }
    } catch (error) {
        // Sync not active, ignore
    }

    // Check if The Archives sync is active
    try {
        const discoveryWeeklyResponse = await fetch('/api/sync/status/discover_discovery_weekly');
        if (discoveryWeeklyResponse.ok) {
            const data = await discoveryWeeklyResponse.json();
            if (data.status === 'syncing' || data.status === 'starting') {
                console.log('🔄 Resuming The Archives sync polling after page refresh');

                // Show status display
                const statusDisplay = document.getElementById('discovery-weekly-sync-status');
                if (statusDisplay) {
                    statusDisplay.style.display = 'block';
                }

                // Disable button
                const syncButton = document.getElementById('discovery-weekly-sync-btn');
                if (syncButton) {
                    syncButton.disabled = true;
                    syncButton.style.opacity = '0.5';
                    syncButton.style.cursor = 'not-allowed';
                }

                // Resume polling
                startDiscoverSyncPolling('discovery_weekly', 'discover_discovery_weekly');
            }
        }
    } catch (error) {
        // Sync not active, ignore
    }

    // Check if Seasonal Playlist sync is active
    try {
        const seasonalResponse = await fetch('/api/sync/status/discover_seasonal_playlist');
        if (seasonalResponse.ok) {
            const data = await seasonalResponse.json();
            if (data.status === 'syncing' || data.status === 'starting') {
                console.log('🔄 Resuming Seasonal Playlist sync polling after page refresh');

                const statusDisplay = document.getElementById('seasonal-playlist-sync-status');
                if (statusDisplay) {
                    statusDisplay.style.display = 'block';
                }

                const syncButton = document.getElementById('seasonal-playlist-sync-btn');
                if (syncButton) {
                    syncButton.disabled = true;
                    syncButton.style.opacity = '0.5';
                    syncButton.style.cursor = 'not-allowed';
                }

                startDiscoverSyncPolling('seasonal_playlist', 'discover_seasonal_playlist');
            }
        }
    } catch (error) {
        // Sync not active, ignore
    }
}

async function loadDiscoverHero() {
    try {
        const response = await fetch('/api/discover/hero');
        if (!response.ok) {
            console.error('Failed to fetch discover hero');
            return;
        }

        const data = await response.json();
        if (!data.success || !data.artists || data.artists.length === 0) {
            console.log('No hero artists available');
            showDiscoverHeroEmpty();
            return;
        }

        discoverHeroArtists = data.artists;
        discoverHeroIndex = 0;

        // Display first artist
        displayDiscoverHeroArtist(discoverHeroArtists[0]);

        // Start slideshow (change every 8 seconds)
        if (discoverHeroInterval) {
            clearInterval(discoverHeroInterval);
        }
        if (discoverHeroArtists.length > 1) {
            discoverHeroInterval = setInterval(() => {
                discoverHeroIndex = (discoverHeroIndex + 1) % discoverHeroArtists.length;
                displayDiscoverHeroArtist(discoverHeroArtists[discoverHeroIndex]);
            }, 8000);
        }

        // Check if all hero artists are already watched
        checkAllHeroWatchlistStatus();

    } catch (error) {
        console.error('Error loading discover hero:', error);
        showDiscoverHeroEmpty();
    }
}

function displayDiscoverHeroArtist(artist) {
    const titleEl = document.getElementById('discover-hero-title');
    const subtitleEl = document.getElementById('discover-hero-subtitle');
    const metaEl = document.getElementById('discover-hero-meta');
    const imageEl = document.getElementById('discover-hero-image');
    const bgEl = document.getElementById('discover-hero-bg');

    if (titleEl) {
        titleEl.textContent = artist.artist_name;
    }

    if (subtitleEl) {
        // Show recommendation context based on occurrence count
        let subtitle = '';
        if (artist.occurrence_count > 1) {
            subtitle = `Similar to ${artist.occurrence_count} artists in your watchlist`;
        } else {
            subtitle = 'Similar to an artist in your watchlist';
        }
        subtitleEl.textContent = subtitle;
    }

    // Build metadata section with popularity and genres
    if (metaEl) {
        let metaHTML = '<div class="discover-hero-meta-content">';

        // Add popularity indicator
        if (artist.popularity !== undefined && artist.popularity > 0) {
            const popularityClass = artist.popularity >= 80 ? 'high' :
                artist.popularity >= 50 ? 'medium' : 'low';
            metaHTML += `
                <div class="hero-meta-item hero-popularity ${popularityClass}">
                    <span class="meta-icon">⭐</span>
                    <span class="meta-value">${artist.popularity}/100</span>
                    <span class="meta-label">Popularity</span>
                </div>
            `;
        }

        // Add genre tags
        if (artist.genres && artist.genres.length > 0) {
            metaHTML += '<div class="hero-meta-item hero-genres">';
            artist.genres.slice(0, 3).forEach(genre => {
                metaHTML += `<span class="genre-tag">${genre}</span>`;
            });
            metaHTML += '</div>';
        }

        metaHTML += '</div>';
        metaEl.innerHTML = metaHTML;
    }

    if (imageEl && artist.image_url) {
        imageEl.innerHTML = `<img src="${artist.image_url}" alt="${artist.artist_name}">`;
    } else if (imageEl) {
        imageEl.innerHTML = '<div class="hero-image-placeholder">🎧</div>';
    }

    if (bgEl && artist.image_url) {
        bgEl.style.backgroundImage = `url('${artist.image_url}')`;
        bgEl.style.backgroundSize = 'cover';
        bgEl.style.backgroundPosition = 'center';
    }

    // Store artist ID for both buttons and update watchlist state
    // Use artist_id which is set by the backend to the appropriate ID for the active source
    const addBtn = document.getElementById('discover-hero-add');
    const discographyBtn = document.getElementById('discover-hero-discography');
    const artistId = artist.artist_id || artist.spotify_artist_id || artist.itunes_artist_id;

    if (addBtn && artistId) {
        addBtn.setAttribute('data-artist-id', artistId);
        addBtn.setAttribute('data-artist-name', artist.artist_name);
        // Also store both IDs for cross-source operations
        if (artist.spotify_artist_id) addBtn.setAttribute('data-spotify-id', artist.spotify_artist_id);
        if (artist.itunes_artist_id) addBtn.setAttribute('data-itunes-id', artist.itunes_artist_id);

        // Check if this artist is already in watchlist and update button appearance
        checkAndUpdateDiscoverHeroWatchlistButton(artistId);
    }

    if (discographyBtn && artistId) {
        discographyBtn.setAttribute('data-artist-id', artistId);
        discographyBtn.setAttribute('data-artist-name', artist.artist_name);
        // Source the click handler will pass to navigateToArtistDetail. Without
        // this, source-only hero artists (which is the typical case — they
        // come from discover similar-artists, not the library) get looked up
        // as library IDs and 404. Backend always includes artist.source.
        if (artist.source) discographyBtn.setAttribute('data-source', artist.source);
        else discographyBtn.removeAttribute('data-source');
        // Also store both IDs for cross-source operations
        if (artist.spotify_artist_id) discographyBtn.setAttribute('data-spotify-id', artist.spotify_artist_id);
        if (artist.itunes_artist_id) discographyBtn.setAttribute('data-itunes-id', artist.itunes_artist_id);
    }

    // Update slideshow indicators
    updateDiscoverHeroIndicators();
}

async function checkAndUpdateDiscoverHeroWatchlistButton(artistId) {
    try {
        const response = await fetch('/api/watchlist/check', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ artist_id: artistId })
        });

        const data = await response.json();
        if (!data.success) return;

        const addBtn = document.getElementById('discover-hero-add');
        if (!addBtn) return;

        const icon = addBtn.querySelector('.watchlist-icon');
        const text = addBtn.querySelector('.watchlist-text');

        if (data.is_watching) {
            // Artist is in watchlist
            if (icon) icon.textContent = '👁️';
            if (text) text.textContent = 'Watching...';
            addBtn.classList.add('watching');
        } else {
            // Artist not in watchlist
            if (icon) icon.textContent = '👁️';
            if (text) text.textContent = 'Add to Watchlist';
            addBtn.classList.remove('watching');
        }
    } catch (error) {
        console.error('Error checking watchlist status for hero:', error);
    }
}

function toggleDiscoverHeroWatchlist(event) {
    event.stopPropagation();

    const button = document.getElementById('discover-hero-add');
    if (!button) return;

    const artistId = button.getAttribute('data-artist-id');
    const artistName = button.getAttribute('data-artist-name');

    if (!artistId || !artistName) {
        console.error('No artist data found on discover hero button');
        return;
    }

    // Call the existing toggleWatchlist function
    toggleWatchlist(event, artistId, artistName);
}

async function watchAllHeroArtists(btn) {
    if (!discoverHeroArtists || discoverHeroArtists.length === 0) return;
    if (btn.classList.contains('all-watched')) return;

    const textEl = btn.querySelector('.watch-all-text');
    const originalText = textEl ? textEl.textContent : '';

    // Loading state
    btn.disabled = true;
    if (textEl) textEl.textContent = 'Adding...';

    try {
        const artists = discoverHeroArtists.map(a => ({
            artist_id: a.artist_id,
            artist_name: a.artist_name
        }));

        const response = await fetch('/api/watchlist/add-batch', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ artists })
        });

        const data = await response.json();
        if (data.success) {
            if (textEl) textEl.textContent = 'All Watched';
            btn.classList.add('all-watched');
            btn.disabled = true;

            // Sync the per-slide watchlist button for current artist
            const currentArtist = discoverHeroArtists[discoverHeroIndex];
            if (currentArtist) {
                checkAndUpdateDiscoverHeroWatchlistButton(currentArtist.artist_id);
            }

            // Update watchlist count badge
            if (typeof updateWatchlistButtonCount === 'function') {
                updateWatchlistButtonCount();
            }
        } else {
            if (textEl) textEl.textContent = originalText;
            btn.disabled = false;
        }
    } catch (error) {
        console.error('Error watching all hero artists:', error);
        if (textEl) textEl.textContent = originalText;
        btn.disabled = false;
    }
}

// Cache for recommended artists data so reopening is instant
let _recommendedArtistsCache = null;

async function openRecommendedArtistsModal() {
    let modal = document.getElementById('recommended-artists-modal');
    if (!modal) {
        modal = document.createElement('div');
        modal.id = 'recommended-artists-modal';
        modal.className = 'modal-overlay';
        document.body.appendChild(modal);

        modal.addEventListener('click', function (e) {
            if (e.target === modal) closeRecommendedArtistsModal();
        });
    }

    // If cached, render instantly and refresh watchlist statuses
    if (_recommendedArtistsCache) {
        modal.style.display = 'flex';
        renderRecommendedArtistsModal(modal, _recommendedArtistsCache);
        checkRecommendedWatchlistStatuses(_recommendedArtistsCache);
        return;
    }

    // Show loading
    modal.innerHTML = `
        <div class="modal-container playlist-modal recommended-modal">
            <div class="playlist-modal-header">
                <div class="playlist-header-content" style="width: 100%;">
                    <h2>Recommended Artists</h2>
                    <div class="playlist-quick-info">
                        <span class="playlist-track-count">Loading...</span>
                    </div>
                </div>
                <span class="playlist-modal-close" onclick="closeRecommendedArtistsModal()">&times;</span>
            </div>
            <div class="playlist-modal-body">
                <div class="recommended-loading">Loading recommended artists...</div>
            </div>
        </div>
    `;
    modal.style.display = 'flex';

    try {
        // Phase 1: Fetch basic data (instant — no API enrichment)
        const response = await fetch('/api/discover/similar-artists');
        const data = await response.json();

        if (!data.success || !data.artists || data.artists.length === 0) {
            modal.querySelector('.playlist-modal-body').innerHTML = `
                <div class="recommended-empty-state">
                    No recommended artists yet.<br>
                    Run a watchlist scan to generate recommendations.
                </div>
            `;
            modal.querySelector('.playlist-track-count').textContent = '0 artists';
            return;
        }

        // Render cards immediately with fallback images
        _recommendedArtistsCache = data.artists;
        renderRecommendedArtistsModal(modal, data.artists);

        // Phase 2: Enrich with images/genres progressively in batches of 50
        // Skip artists that already have cached metadata from the initial response
        const source = data.source || 'spotify';
        const idKey = source === 'spotify' ? 'spotify_artist_id' : source === 'deezer' ? 'deezer_artist_id' : 'itunes_artist_id';
        const allIds = data.artists
            .filter(a => !a.image_url)  // Only enrich artists without cached images
            .map(a => a[idKey]).filter(Boolean);

        for (let i = 0; i < allIds.length; i += 50) {
            const batchIds = allIds.slice(i, i + 50);
            try {
                const enrichResp = await fetch('/api/discover/similar-artists/enrich', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ artist_ids: batchIds, source })
                });
                const enrichData = await enrichResp.json();
                if (enrichData.success && enrichData.artists) {
                    // Update cards and cache as each batch arrives
                    for (const [aid, info] of Object.entries(enrichData.artists)) {
                        // Update the card in DOM
                        const card = modal.querySelector(`.recommended-artist-card[data-artist-id="${aid}"]`);
                        if (card && info.image_url) {
                            const imgContainer = card.querySelector('.recommended-card-image');
                            if (imgContainer) {
                                imgContainer.innerHTML = `<img src="${info.image_url}" alt="" loading="lazy"
                                    onerror="this.parentElement.innerHTML='<div class=\\'recommended-card-image-fallback\\'>🎤</div>';">`;
                            }
                        }
                        if (card && info.genres && info.genres.length > 0) {
                            const genresContainer = card.querySelector('.recommended-card-genres');
                            if (genresContainer) {
                                genresContainer.innerHTML = info.genres.map(g =>
                                    `<span class="recommended-card-genre">${escapeHtml(g)}</span>`
                                ).join('');
                            } else {
                                const infoDiv = card.querySelector('.recommended-card-info');
                                if (infoDiv) {
                                    const genreDiv = document.createElement('div');
                                    genreDiv.className = 'recommended-card-genres';
                                    genreDiv.innerHTML = info.genres.map(g =>
                                        `<span class="recommended-card-genre">${escapeHtml(g)}</span>`
                                    ).join('');
                                    infoDiv.appendChild(genreDiv);
                                }
                            }
                        }

                        // Update cache
                        const cached = _recommendedArtistsCache.find(a => a.artist_id === aid || a.spotify_artist_id === aid || a.itunes_artist_id === aid);
                        if (cached) {
                            if (info.image_url) cached.image_url = info.image_url;
                            if (info.genres) cached.genres = info.genres;
                            if (info.artist_name) cached.artist_name = info.artist_name;
                        }
                    }
                }
            } catch (enrichErr) {
                console.error('Error enriching batch:', enrichErr);
            }
        }

        // Phase 3: Check watchlist statuses
        checkRecommendedWatchlistStatuses(data.artists);

    } catch (error) {
        console.error('Error loading recommended artists:', error);
        modal.querySelector('.playlist-modal-body').innerHTML = `
            <div class="recommended-empty-state">Error loading recommended artists.</div>
        `;
    }
}

function renderRecommendedArtistsModal(modal, artists) {
    modal.innerHTML = `
        <div class="modal-container playlist-modal recommended-modal">
            <div class="playlist-modal-header">
                <div class="playlist-header-content" style="width: 100%;">
                    <h2>Recommended Artists</h2>
                    <div class="playlist-quick-info">
                        <span class="playlist-track-count">${artists.length} artist${artists.length !== 1 ? 's' : ''}</span>
                    </div>
                </div>
                <span class="playlist-modal-close" onclick="closeRecommendedArtistsModal()">&times;</span>
            </div>
            <div class="playlist-modal-body">
                <div class="recommended-actions-bar">
                    <div class="recommended-search-container">
                        <input type="text"
                               class="recommended-search-input"
                               id="recommended-search-input"
                               placeholder="Search recommended artists..."
                               oninput="filterRecommendedArtists()">
                    </div>
                    <button class="recommended-add-all-btn" id="recommended-add-all-btn"
                            onclick="addAllRecommendedToWatchlist(this)">
                        Add All to Watchlist
                    </button>
                </div>
                <div class="recommended-artists-grid" id="recommended-artists-grid">
                    ${artists.map(artist => {
        const genreTags = (artist.genres || []).slice(0, 3).map(g =>
            `<span class="recommended-card-genre">${escapeHtml(g)}</span>`
        ).join('');
        const similarText = artist.occurrence_count > 1
            ? `Similar to ${artist.occurrence_count} in your watchlist`
            : 'Similar to an artist in your watchlist';
        return `
                            <div class="recommended-artist-card"
                                 data-artist-name="${escapeHtml(artist.artist_name).toLowerCase()}"
                                 data-artist-id="${artist.artist_id}">
                                <button class="recommended-card-watchlist-btn"
                                        data-artist-id="${artist.artist_id}"
                                        data-artist-name="${escapeHtml(artist.artist_name)}">
                                    Add to Watchlist
                                </button>
                                <div class="recommended-card-image">
                                    ${artist.image_url ? `
                                        <img src="${artist.image_url}"
                                             alt="${escapeHtml(artist.artist_name)}"
                                             loading="lazy"
                                             onerror="this.parentElement.innerHTML='<div class=\\'recommended-card-image-fallback\\'>🎤</div>';">
                                    ` : `
                                        <div class="recommended-card-image-fallback">🎤</div>
                                    `}
                                </div>
                                <div class="recommended-card-info">
                                    <span class="recommended-card-name">${escapeHtml(artist.artist_name)}</span>
                                    <span class="recommended-card-similarity">${similarText}</span>
                                    <div class="recommended-card-genres">${genreTags}</div>
                                </div>
                            </div>
                        `;
    }).join('')}
                </div>
            </div>
        </div>
    `;

    // Event delegation for card clicks and watchlist buttons
    const grid = modal.querySelector('#recommended-artists-grid');
    if (grid) {
        grid.addEventListener('click', function (e) {
            const watchlistBtn = e.target.closest('.recommended-card-watchlist-btn');
            if (watchlistBtn) {
                e.stopPropagation();
                toggleRecommendedWatchlist(watchlistBtn);
                return;
            }

            const card = e.target.closest('.recommended-artist-card');
            if (card) {
                const artistId = card.getAttribute('data-artist-id');
                const nameEl = card.querySelector('.recommended-card-name');
                const artistName = nameEl ? nameEl.textContent : '';
                viewRecommendedArtistDiscography(artistId, artistName);
            }
        });
    }
}

async function addAllRecommendedToWatchlist(btn) {
    if (!_recommendedArtistsCache || _recommendedArtistsCache.length === 0) return;
    if (btn.classList.contains('all-added')) return;

    const originalText = btn.textContent;
    btn.disabled = true;
    btn.textContent = 'Adding...';

    try {
        const artists = _recommendedArtistsCache.map(a => ({
            artist_id: a.artist_id,
            artist_name: a.artist_name
        }));

        const resp = await fetch('/api/watchlist/add-batch', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ artists })
        });
        const data = await resp.json();

        if (data.success) {
            btn.textContent = `All Added (${data.added} new)`;
            btn.classList.add('all-added');
            btn.disabled = true;

            // Update all watchlist buttons in the modal to "Watching"
            document.querySelectorAll('.recommended-card-watchlist-btn').forEach(wBtn => {
                wBtn.classList.add('watching');
                wBtn.textContent = 'Watching';
            });

            if (typeof updateWatchlistButtonCount === 'function') updateWatchlistButtonCount();
        } else {
            btn.textContent = originalText;
            btn.disabled = false;
        }
    } catch (error) {
        console.error('Error adding all recommended to watchlist:', error);
        btn.textContent = originalText;
        btn.disabled = false;
    }
}

function closeRecommendedArtistsModal() {
    const modal = document.getElementById('recommended-artists-modal');
    if (modal) modal.style.display = 'none';
}

function filterRecommendedArtists() {
    const query = (document.getElementById('recommended-search-input')?.value || '').toLowerCase();
    const cards = document.querySelectorAll('.recommended-artist-card');
    cards.forEach(card => {
        const name = card.getAttribute('data-artist-name') || '';
        card.style.display = name.includes(query) ? '' : 'none';
    });
}

async function toggleRecommendedWatchlist(btn) {
    const artistId = btn.getAttribute('data-artist-id');
    const artistName = btn.getAttribute('data-artist-name');
    if (!artistId || !artistName) return;

    btn.disabled = true;
    const wasWatching = btn.classList.contains('watching');

    try {
        if (wasWatching) {
            const resp = await fetch('/api/watchlist/remove', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ artist_id: artistId })
            });
            const data = await resp.json();
            if (data.success) {
                btn.classList.remove('watching');
                btn.textContent = 'Add to Watchlist';
            }
        } else {
            const resp = await fetch('/api/watchlist/add', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ artist_id: artistId, artist_name: artistName })
            });
            const data = await resp.json();
            if (data.success) {
                btn.classList.add('watching');
                btn.textContent = 'Watching';
            }
        }
        if (typeof updateWatchlistButtonCount === 'function') updateWatchlistButtonCount();
    } catch (error) {
        console.error('Error toggling recommended watchlist:', error);
    } finally {
        btn.disabled = false;
    }
}

async function checkRecommendedWatchlistStatuses(artists) {
    try {
        const artistIds = artists.map(a => a.artist_id).filter(Boolean);
        if (!artistIds.length) return;

        const resp = await fetch('/api/watchlist/check-batch', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ artist_ids: artistIds })
        });
        const data = await resp.json();
        if (data.success && data.results) {
            for (const [aid, isWatching] of Object.entries(data.results)) {
                if (isWatching) {
                    const btn = document.querySelector(`.recommended-card-watchlist-btn[data-artist-id="${aid}"]`);
                    if (btn) {
                        btn.classList.add('watching');
                        btn.textContent = 'Watching';
                    }
                }
            }
        }
    } catch (e) {
        // Non-critical
    }
}

async function viewRecommendedArtistDiscography(artistId, artistName) {
    closeRecommendedArtistsModal();
    navigateToArtistDetail(artistId, artistName);
}

async function checkAllHeroWatchlistStatus() {
    const btn = document.getElementById('discover-hero-watch-all');
    if (!btn || !discoverHeroArtists || discoverHeroArtists.length === 0) return;

    try {
        let allWatched = true;
        for (const artist of discoverHeroArtists) {
            const response = await fetch('/api/watchlist/check', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ artist_id: artist.artist_id })
            });
            const data = await response.json();
            if (!data.success || !data.is_watching) {
                allWatched = false;
                break;
            }
        }

        const textEl = btn.querySelector('.watch-all-text');
        if (allWatched) {
            if (textEl) textEl.textContent = 'All Watched';
            btn.classList.add('all-watched');
            btn.disabled = true;
        } else {
            if (textEl) textEl.textContent = 'Watch All';
            btn.classList.remove('all-watched');
            btn.disabled = false;
        }
    } catch (error) {
        console.error('Error checking hero watchlist status:', error);
    }
}

function navigateDiscoverHero(direction) {
    if (!discoverHeroArtists || discoverHeroArtists.length === 0) return;

    // Update index with wrapping
    discoverHeroIndex = (discoverHeroIndex + direction + discoverHeroArtists.length) % discoverHeroArtists.length;

    // Display the artist
    displayDiscoverHeroArtist(discoverHeroArtists[discoverHeroIndex]);

    // Update indicators
    updateDiscoverHeroIndicators();
}

function updateDiscoverHeroIndicators() {
    const indicatorsContainer = document.getElementById('discover-hero-indicators');
    if (!indicatorsContainer || !discoverHeroArtists || discoverHeroArtists.length === 0) return;

    // Create indicator dots
    indicatorsContainer.innerHTML = discoverHeroArtists.map((_, index) => `
        <button class="hero-indicator ${index === discoverHeroIndex ? 'active' : ''}"
                onclick="jumpToDiscoverHeroSlide(${index})"
                aria-label="Go to slide ${index + 1}"></button>
    `).join('');
}

function jumpToDiscoverHeroSlide(index) {
    if (!discoverHeroArtists || index < 0 || index >= discoverHeroArtists.length) return;

    discoverHeroIndex = index;
    displayDiscoverHeroArtist(discoverHeroArtists[discoverHeroIndex]);
    updateDiscoverHeroIndicators();
}

async function viewDiscoverHeroDiscography() {
    const button = document.getElementById('discover-hero-discography');
    if (!button) return;

    const artistId = button.getAttribute('data-artist-id');
    const artistName = button.getAttribute('data-artist-name');
    // Pass the source so /api/artist-detail knows to synthesize from that
    // metadata provider instead of doing a local DB lookup. Hero similar
    // artists are almost always source-only (not in the library).
    const source = button.getAttribute('data-source') || null;

    if (!artistId || !artistName) {
        console.error('No artist data found for discography view');
        return;
    }

    console.log(`🎵 Navigating to artist detail for: ${artistName} (source: ${source || 'library'})`);
    navigateToArtistDetail(artistId, artistName, source);
}

function showDiscoverHeroEmpty() {
    const titleEl = document.getElementById('discover-hero-title');
    const subtitleEl = document.getElementById('discover-hero-subtitle');

    if (titleEl) titleEl.textContent = 'No Recommendations Yet';
    if (subtitleEl) subtitleEl.textContent = 'Run a watchlist scan to generate personalized recommendations';
}

async function loadDiscoverRecentReleases() {
    try {
        const carousel = document.getElementById('recent-releases-carousel');
        if (!carousel) return;

        carousel.innerHTML = '<div class="discover-loading"><div class="loading-spinner"></div><p>Loading recent releases...</p></div>';

        const response = await fetch('/api/discover/recent-releases');
        if (!response.ok) {
            throw new Error('Failed to fetch recent releases');
        }

        const data = await response.json();
        if (!data.success || !data.albums || data.albums.length === 0) {
            carousel.innerHTML = '<div class="discover-empty"><p>No recent releases found</p></div>';
            return;
        }

        // Store albums for download functionality
        discoverRecentAlbums = data.albums;

        // Build carousel HTML
        let html = '';
        data.albums.forEach((album, index) => {
            const coverUrl = album.album_cover_url || '/static/placeholder-album.png';
            html += `
                <div class="discover-card" onclick="openDownloadModalForRecentAlbum(${index})" style="cursor: pointer;">
                    <div class="discover-card-image">
                        <img src="${coverUrl}" alt="${album.album_name}" loading="lazy">
                    </div>
                    <div class="discover-card-info">
                        <h4 class="discover-card-title">${album.album_name}</h4>
                        <p class="discover-card-subtitle">${album.artist_name}</p>
                        <p class="discover-card-meta">${album.release_date}</p>
                    </div>
                </div>
            `;
        });

        carousel.innerHTML = html;

    } catch (error) {
        console.error('Error loading recent releases:', error);
        const carousel = document.getElementById('recent-releases-carousel');
        if (carousel) {
            carousel.innerHTML = '<div class="discover-empty"><p>Failed to load recent releases</p></div>';
        }
    }
}

// ===============================
// ===============================
// YOUR ALBUMS SECTION
// ===============================

let yourAlbums = [];
let yourAlbumsPage = 1;
let yourAlbumsTotal = 0;
const YOUR_ALBUMS_PAGE_SIZE = 48;
let _yourAlbumsSearchTimeout = null;

function debouncedYourAlbumsSearch() {
    clearTimeout(_yourAlbumsSearchTimeout);
    _yourAlbumsSearchTimeout = setTimeout(() => {
        yourAlbumsPage = 1;
        loadYourAlbumsGrid();
    }, 400);
}

async function loadYourAlbums() {
    const section = document.getElementById('your-albums-section');
    if (!section) return;
    try {
        const resp = await fetch('/api/discover/your-albums?page=1&per_page=48&status=all');
        if (!resp.ok) return;
        const data = await resp.json();
        if (!data.success) return;

        const totalCount = (data.stats && data.stats.total) || 0;
        if (totalCount === 0 && !data.stale) return; // Nothing to show yet

        section.style.display = '';
        yourAlbums = data.albums || [];
        yourAlbumsTotal = data.total || 0;
        yourAlbumsPage = 1;

        const subtitle = document.getElementById('your-albums-subtitle');
        if (subtitle && data.stats) {
            const s = data.stats;
            subtitle.textContent = `${s.total} albums \u00B7 ${s.owned} owned \u00B7 ${s.missing} missing`;
        }

        const filters = document.getElementById('your-albums-filters');
        if (filters && totalCount > 0) filters.style.display = '';

        const downloadBtn = document.getElementById('your-albums-download-btn');
        if (downloadBtn && data.stats && data.stats.missing > 0) downloadBtn.style.display = '';

        _renderYourAlbumsGrid(yourAlbums);
        _renderYourAlbumsPagination(yourAlbumsTotal, yourAlbumsPage);

        if (data.stale && totalCount === 0) {
            const grid = document.getElementById('your-albums-grid');
            if (grid) grid.innerHTML = '<div class="discover-loading"><div class="loading-spinner"></div><p>Fetching your albums from connected services...</p></div>';
            _pollYourAlbums();
        }
    } catch (e) {
        console.error('Error loading your albums:', e);
    }
}

function _pollYourAlbums() {
    let attempts = 0;
    const poll = setInterval(async () => {
        attempts++;
        if (attempts > 12) { clearInterval(poll); return; }
        try {
            const resp = await fetch('/api/discover/your-albums?page=1&per_page=48&status=all');
            if (!resp.ok) return;
            const data = await resp.json();
            if (!data.success) return;
            const total = (data.stats && data.stats.total) || 0;
            if (total > 0) {
                clearInterval(poll);
                loadYourAlbums();
            }
        } catch (e) { }
    }, 5000);
}

async function loadYourAlbumsGrid() {
    const grid = document.getElementById('your-albums-grid');
    if (!grid) return;
    grid.innerHTML = '<div class="discover-loading"><div class="loading-spinner"></div><p>Loading...</p></div>';
    try {
        const search = (document.getElementById('your-albums-search')?.value || '').trim();
        const status = document.getElementById('your-albums-status-filter')?.value || 'all';
        const sort = document.getElementById('your-albums-sort')?.value || 'artist_name';
        const params = new URLSearchParams({ page: yourAlbumsPage, per_page: YOUR_ALBUMS_PAGE_SIZE, sort, status });
        if (search) params.set('search', search);
        const resp = await fetch(`/api/discover/your-albums?${params}`);
        const data = await resp.json();
        if (!data.success) throw new Error(data.error);
        yourAlbums = data.albums || [];
        yourAlbumsTotal = data.total || 0;
        const subtitle = document.getElementById('your-albums-subtitle');
        if (subtitle && data.stats) {
            const s = data.stats;
            subtitle.textContent = `${s.total} albums \u00B7 ${s.owned} owned \u00B7 ${s.missing} missing`;
        }
        _renderYourAlbumsGrid(yourAlbums);
        _renderYourAlbumsPagination(yourAlbumsTotal, yourAlbumsPage);
    } catch (e) {
        console.error('Error loading your albums grid:', e);
        grid.innerHTML = '<div class="spotify-library-empty"><p>Failed to load albums</p></div>';
    }
}

function _renderYourAlbumsGrid(albums) {
    const grid = document.getElementById('your-albums-grid');
    if (!grid) return;
    if (!albums || albums.length === 0) {
        grid.innerHTML = '<div class="spotify-library-empty"><p>No albums found</p></div>';
        return;
    }
    let html = '';
    albums.forEach((album, index) => {
        const coverUrl = album.image_url || '/static/placeholder-album.png';
        const year = album.release_date ? album.release_date.substring(0, 4) : '';
        const badgeClass = album.in_library ? 'owned' : 'missing';
        const badgeIcon = album.in_library ? '\u2713' : '\u2193';
        const trackInfo = album.total_tracks ? `${album.total_tracks} tracks` : '';
        const meta = [year, trackInfo].filter(Boolean).join(' \u00B7 ');
        const sources = (album.source_services || []).join(', ');
        html += `
            <div class="spotify-library-card" onclick="openYourAlbumDownload(${index})" title="${escapeHtml(album.album_name)} \u2014 ${escapeHtml(album.artist_name)}">
                <div class="spotify-library-card-img">
                    <img src="${coverUrl}" alt="${escapeHtml(album.album_name)}" loading="lazy">
                    <div class="spotify-library-card-badge ${badgeClass}">${badgeIcon}</div>
                </div>
                <div class="spotify-library-card-info">
                    <p class="spotify-library-card-title">${escapeHtml(album.album_name)}</p>
                    <p class="spotify-library-card-artist">${escapeHtml(album.artist_name)}</p>
                    <p class="spotify-library-card-meta">${escapeHtml(meta)}</p>
                </div>
            </div>`;
    });
    grid.innerHTML = html;
}

function _renderYourAlbumsPagination(total, page) {
    const container = document.getElementById('your-albums-pagination');
    if (!container) return;
    if (total <= YOUR_ALBUMS_PAGE_SIZE) { container.style.display = 'none'; return; }
    container.style.display = '';
    const totalPages = Math.ceil(total / YOUR_ALBUMS_PAGE_SIZE);
    const start = (page - 1) * YOUR_ALBUMS_PAGE_SIZE + 1;
    const end = Math.min(page * YOUR_ALBUMS_PAGE_SIZE, total);
    container.innerHTML = `
        <button class="spotify-library-page-btn" onclick="_yourAlbumsPrevPage()" ${page <= 1 ? 'disabled' : ''}>&larr; Previous</button>
        <span class="spotify-library-page-info">${start}\u2013${end} of ${total}</span>
        <button class="spotify-library-page-btn" onclick="_yourAlbumsNextPage()" ${page >= totalPages ? 'disabled' : ''}>Next &rarr;</button>
    `;
}

function _yourAlbumsPrevPage() {
    if (yourAlbumsPage > 1) { yourAlbumsPage--; loadYourAlbumsGrid(); }
}
function _yourAlbumsNextPage() {
    const totalPages = Math.ceil(yourAlbumsTotal / YOUR_ALBUMS_PAGE_SIZE);
    if (yourAlbumsPage < totalPages) { yourAlbumsPage++; loadYourAlbumsGrid(); }
}

async function openYourAlbumDownload(index) {
    const album = yourAlbums[index];
    if (!album) { showToast('Album data not found', 'error'); return; }
    showLoadingOverlay(`Loading tracks for ${album.album_name}...`);
    try {
        // Prefer Spotify ID, fall back to Deezer, then search by name
        let albumData = null;
        const nameParams = new URLSearchParams({ name: album.album_name || '', artist: album.artist_name || '' });
        if (album.spotify_album_id) {
            const r = await fetch(`/api/discover/album/spotify/${album.spotify_album_id}?${nameParams}`);
            if (r.ok) albumData = await r.json();
        }
        if (!albumData && album.deezer_album_id) {
            const r = await fetch(`/api/discover/album/deezer/${album.deezer_album_id}?${nameParams}`);
            if (r.ok) albumData = await r.json();
        }
        if (!albumData) {
            // Last resort — search by name
            const r = await fetch(`/api/discover/album/spotify/search?${nameParams}`);
            if (r.ok) albumData = await r.json();
        }
        if (!albumData || !albumData.tracks || albumData.tracks.length === 0) {
            throw new Error('No tracks found for this album');
        }
        const tracks = albumData.tracks.map(track => {
            let artists = track.artists || albumData.artists || [{ name: album.artist_name }];
            if (Array.isArray(artists)) artists = artists.map(a => a.name || a);
            return {
                id: track.id, name: track.name, artists,
                album: {
                    id: albumData.id, name: albumData.name,
                    album_type: albumData.album_type || 'album',
                    total_tracks: albumData.total_tracks || 0,
                    release_date: albumData.release_date || '',
                    images: albumData.images || []
                },
                duration_ms: track.duration_ms || 0,
                track_number: track.track_number || 0
            };
        });
        const virtualId = `discover_album_${album.spotify_album_id || album.deezer_album_id || album.tidal_album_id || index}`;
        const albumObj = {
            id: albumData.id, name: albumData.name, album_type: albumData.album_type || 'album',
            total_tracks: albumData.total_tracks || 0, release_date: albumData.release_date || '',
            images: albumData.images || [], artists: [{ name: album.artist_name }]
        };
        const artistObj = { id: null, name: album.artist_name };
        await openDownloadMissingModalForArtistAlbum(virtualId, albumData.name, tracks, albumObj, artistObj, false);
        hideLoadingOverlay();
    } catch (e) {
        console.error('Error opening your album download:', e);
        showToast(`Failed to load album: ${e.message}`, 'error');
        hideLoadingOverlay();
    }
}

async function refreshYourAlbums() {
    const btn = document.getElementById('your-albums-refresh-btn');
    if (btn) btn.disabled = true;
    const subtitle = document.getElementById('your-albums-subtitle');
    if (subtitle) subtitle.textContent = 'Refreshing from connected services...';
    try {
        await fetch('/api/discover/your-albums/refresh?clear=true', { method: 'POST' });
        showToast('Refresh started — checking for new albums...', 'info');
        const poll = setInterval(async () => {
            try {
                const resp = await fetch('/api/discover/your-albums?page=1&per_page=48');
                const data = await resp.json();
                if (data.success && data.stats && data.stats.total > 0) {
                    clearInterval(poll);
                    loadYourAlbums();
                    if (btn) btn.disabled = false;
                }
            } catch (e) { }
        }, 4000);
        setTimeout(() => { clearInterval(poll); if (btn) btn.disabled = false; }, 60000);
    } catch (e) {
        showToast('Failed to start refresh', 'error');
        if (btn) btn.disabled = false;
    }
}

async function openYourAlbumsSourcesModal() {
    const existing = document.getElementById('ya-albums-sources-modal-overlay');
    if (existing) existing.remove();

    let enabled = ['spotify', 'tidal', 'deezer'];
    let connected = [];
    try {
        const resp = await fetch('/api/discover/your-albums/sources');
        if (resp.ok) {
            const data = await resp.json();
            if (data.enabled) enabled = data.enabled;
            if (data.connected) connected = data.connected;
        }
    } catch (e) { }

    const sourceInfo = [
        { id: 'spotify', label: 'Spotify', icon: '\uD83C\uDFB5' },
        { id: 'tidal', label: 'Tidal', icon: '\uD83C\uDF0A' },
        { id: 'deezer', label: 'Deezer', icon: '\uD83C\uDFB6' },
    ];
    const state = {};
    sourceInfo.forEach(s => { state[s.id] = enabled.includes(s.id); });

    const overlay = document.createElement('div');
    overlay.id = 'ya-albums-sources-modal-overlay';
    overlay.className = 'modal-overlay';
    overlay.onclick = (e) => { if (e.target === overlay) overlay.remove(); };

    const rows = sourceInfo.map(s => {
        const isConnected = connected.includes(s.id);
        const isOn = state[s.id];
        return `
            <div class="ya-source-row${isConnected ? '' : ' disconnected'}" data-yaa-source="${s.id}" onclick="_yaaSourceRowClick('${s.id}')">
                <div class="ya-source-row-left">
                    <span style="font-size:18px">${s.icon}</span>
                    <div>
                        <div class="ya-source-name">${s.label}</div>
                        <div class="ya-source-status">${isConnected ? 'Connected' : 'Not connected'}</div>
                    </div>
                </div>
                <button class="ya-source-toggle${isOn ? ' on' : ''}" id="yaa-toggle-${s.id}" onclick="event.stopPropagation();_yaaSourceToggle('${s.id}')"></button>
            </div>`;
    }).join('');

    overlay.innerHTML = `
        <div class="ya-sources-modal">
            <h2>Your Albums Sources</h2>
            <p class="ya-sources-desc">Choose which connected services contribute albums to this section.</p>
            <div class="ya-sources-list">${rows}</div>
            <div class="ya-sources-footer">
                <button class="ya-sources-cancel-btn" onclick="document.getElementById('ya-albums-sources-modal-overlay').remove()">Cancel</button>
                <button class="ya-sources-save-btn" onclick="_yaaSourcesSave()">Save</button>
            </div>
        </div>
    `;
    document.body.appendChild(overlay);
    window._yaaSourcesState = state;
}

function _yaaSourceRowClick(id) {
    const row = document.querySelector(`.ya-source-row[data-yaa-source="${id}"]`);
    if (row && row.classList.contains('disconnected')) return;
    _yaaSourceToggle(id);
}
function _yaaSourceToggle(id) {
    const row = document.querySelector(`.ya-source-row[data-yaa-source="${id}"]`);
    if (row && row.classList.contains('disconnected')) return;
    window._yaaSourcesState[id] = !window._yaaSourcesState[id];
    const btn = document.getElementById(`yaa-toggle-${id}`);
    if (btn) btn.classList.toggle('on', window._yaaSourcesState[id]);
}
async function _yaaSourcesSave() {
    const enabledArr = Object.entries(window._yaaSourcesState).filter(([, v]) => v).map(([k]) => k);
    if (enabledArr.length === 0) { showToast('Select at least one source', 'error'); return; }
    try {
        const resp = await fetch('/api/settings', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ discover: { your_albums_sources: enabledArr.join(',') } })
        });
        if (resp.ok) {
            document.getElementById('ya-albums-sources-modal-overlay')?.remove();
            showToast('Sources saved — refresh to apply', 'success');
            const sourceNames = { spotify: 'Spotify', tidal: 'Tidal', deezer: 'Deezer' };
            const subtitle = document.getElementById('your-albums-subtitle');
            if (subtitle) {
                const names = enabledArr.map(s => sourceNames[s] || s).join(' and ');
                subtitle.textContent = `Albums you\u2019ve saved on ${names}`;
            }
        } else {
            showToast('Failed to save sources', 'error');
        }
    } catch (e) {
        showToast('Failed to save sources', 'error');
    }
}

async function downloadMissingYourAlbums() {
    try {
        const resp = await fetch('/api/discover/your-albums?page=1&per_page=1000&status=missing');
        const data = await resp.json();
        if (!data.success || !data.albums || data.albums.length === 0) {
            showToast('No missing albums to download', 'info');
            return;
        }
        const missing = data.albums.filter(a => !a.in_library);
        if (missing.length === 0) { showToast('All albums are already in your library!', 'success'); return; }
        if (!confirm(`Download ${missing.length} missing album${missing.length > 1 ? 's' : ''} from your saved albums?`)) return;
        showToast(`Starting download for ${missing.length} albums...`, 'info');
        for (let i = 0; i < missing.length; i++) {
            const album = missing[i];
            try {
                showToast(`Queuing ${i + 1}/${missing.length}: ${album.album_name}`, 'info');
                const nameParams = new URLSearchParams({ name: album.album_name || '', artist: album.artist_name || '' });
                let albumData = null;
                if (album.spotify_album_id) {
                    const r = await fetch(`/api/discover/album/spotify/${album.spotify_album_id}?${nameParams}`);
                    if (r.ok) albumData = await r.json();
                }
                if (!albumData && album.deezer_album_id) {
                    const r = await fetch(`/api/discover/album/deezer/${album.deezer_album_id}?${nameParams}`);
                    if (r.ok) albumData = await r.json();
                }
                if (!albumData || !albumData.tracks || albumData.tracks.length === 0) continue;
                const tracks = albumData.tracks.map(track => {
                    let artists = track.artists || albumData.artists || [{ name: album.artist_name }];
                    if (Array.isArray(artists)) artists = artists.map(a => a.name || a);
                    return {
                        id: track.id, name: track.name, artists,
                        album: {
                            id: albumData.id, name: albumData.name, album_type: albumData.album_type || 'album',
                            total_tracks: albumData.total_tracks || 0, release_date: albumData.release_date || '',
                            images: albumData.images || []
                        },
                        duration_ms: track.duration_ms || 0, track_number: track.track_number || 0
                    };
                });
                const virtualId = `your_albums_${album.spotify_album_id || album.deezer_album_id || i}`;
                await openDownloadMissingModalForYouTube(virtualId, albumData.name, tracks,
                    { name: album.artist_name, source: albumData.source || 'spotify' },
                    {
                        id: albumData.id, name: albumData.name, album_type: albumData.album_type || 'album',
                        total_tracks: albumData.total_tracks || 0, release_date: albumData.release_date || '',
                        images: albumData.images || []
                    }
                );
            } catch (err) { console.error(`Error queuing ${album.album_name}:`, err); }
        }
    } catch (e) {
        console.error('Error downloading missing your albums:', e);
        showToast(`Error: ${e.message}`, 'error');
    }
}

// ===============================
// SPOTIFY LIBRARY SECTION
// ===============================

let spotifyLibraryAlbums = [];
let spotifyLibraryPage = 0;
let spotifyLibraryTotal = 0;
const SPOTIFY_LIBRARY_PAGE_SIZE = 48;
let _spotifyLibrarySearchTimeout = null;

function debouncedSpotifyLibrarySearch() {
    clearTimeout(_spotifyLibrarySearchTimeout);
    _spotifyLibrarySearchTimeout = setTimeout(() => {
        spotifyLibraryPage = 0;
        loadSpotifyLibraryAlbums();
    }, 400);
}

async function loadSpotifyLibrarySection() {
    try {
        const section = document.getElementById('spotify-library-section');
        if (!section) return;

        const response = await fetch(`/api/discover/spotify-library?offset=0&limit=${SPOTIFY_LIBRARY_PAGE_SIZE}`);
        if (!response.ok) throw new Error('Failed to fetch');

        const data = await response.json();
        if (!data.success || !data.albums || data.albums.length === 0) {
            section.style.display = 'none';
            return;
        }

        section.style.display = '';
        spotifyLibraryAlbums = data.albums;
        spotifyLibraryTotal = data.total;
        spotifyLibraryPage = 0;

        // Update subtitle with stats
        const subtitle = document.getElementById('spotify-library-subtitle');
        if (subtitle && data.stats) {
            const s = data.stats;
            subtitle.textContent = `${s.total} albums \u00B7 ${s.owned} owned \u00B7 ${s.missing} missing`;
        }

        // Show download missing button if there are missing albums
        const dlBtn = document.getElementById('spotify-library-download-missing-btn');
        if (dlBtn && data.stats && data.stats.missing > 0) {
            dlBtn.style.display = '';
        }

        // Show filters
        const filters = document.getElementById('spotify-library-filters');
        if (filters) filters.style.display = '';

        renderSpotifyLibraryGrid(data.albums);
        renderSpotifyLibraryPagination(data.total, 0);

    } catch (error) {
        console.error('Error loading Spotify library section:', error);
        const section = document.getElementById('spotify-library-section');
        if (section) section.style.display = 'none';
    }
}

async function loadSpotifyLibraryAlbums() {
    const grid = document.getElementById('spotify-library-grid');
    if (!grid) return;

    grid.innerHTML = '<div class="discover-loading"><div class="loading-spinner"></div><p>Loading...</p></div>';

    try {
        const search = (document.getElementById('spotify-library-search')?.value || '').trim();
        const status = document.getElementById('spotify-library-status-filter')?.value || 'all';
        const sort = document.getElementById('spotify-library-sort')?.value || 'date_saved';
        const offset = spotifyLibraryPage * SPOTIFY_LIBRARY_PAGE_SIZE;

        const params = new URLSearchParams({
            offset, limit: SPOTIFY_LIBRARY_PAGE_SIZE, sort, sort_dir: 'desc', status
        });
        if (search) params.set('search', search);

        const response = await fetch(`/api/discover/spotify-library?${params}`);
        const data = await response.json();

        if (!data.success) throw new Error(data.error);

        spotifyLibraryAlbums = data.albums;
        spotifyLibraryTotal = data.total;

        // Update subtitle
        const subtitle = document.getElementById('spotify-library-subtitle');
        if (subtitle && data.stats) {
            const s = data.stats;
            subtitle.textContent = `${s.total} albums \u00B7 ${s.owned} owned \u00B7 ${s.missing} missing`;
        }

        renderSpotifyLibraryGrid(data.albums);
        renderSpotifyLibraryPagination(data.total, offset);

    } catch (error) {
        console.error('Error loading Spotify library albums:', error);
        grid.innerHTML = '<div class="spotify-library-empty"><p>Failed to load albums</p></div>';
    }
}

function renderSpotifyLibraryGrid(albums) {
    const grid = document.getElementById('spotify-library-grid');
    if (!grid) return;

    if (!albums || albums.length === 0) {
        grid.innerHTML = '<div class="spotify-library-empty"><p>No albums found</p></div>';
        return;
    }

    let html = '';
    albums.forEach((album, index) => {
        const coverUrl = album.image_url || '/static/placeholder-album.png';
        const year = album.release_date ? album.release_date.substring(0, 4) : '';
        const badgeClass = album.in_library ? 'owned' : 'missing';
        const badgeIcon = album.in_library ? '\u2713' : '\u2193';
        const trackInfo = album.total_tracks ? `${album.total_tracks} tracks` : '';
        const meta = [year, trackInfo].filter(Boolean).join(' \u00B7 ');

        html += `
            <div class="spotify-library-card" onclick="openSpotifyLibraryAlbumDownload(${index})" title="${album.album_name} — ${album.artist_name}">
                <div class="spotify-library-card-img">
                    <img src="${coverUrl}" alt="${album.album_name}" loading="lazy">
                    <div class="spotify-library-card-badge ${badgeClass}">${badgeIcon}</div>
                </div>
                <div class="spotify-library-card-info">
                    <p class="spotify-library-card-title">${album.album_name}</p>
                    <p class="spotify-library-card-artist">${album.artist_name}</p>
                    <p class="spotify-library-card-meta">${meta}</p>
                </div>
            </div>
        `;
    });

    grid.innerHTML = html;
}

function renderSpotifyLibraryPagination(total, offset) {
    const container = document.getElementById('spotify-library-pagination');
    if (!container) return;

    if (total <= SPOTIFY_LIBRARY_PAGE_SIZE) {
        container.style.display = 'none';
        return;
    }

    container.style.display = '';
    const totalPages = Math.ceil(total / SPOTIFY_LIBRARY_PAGE_SIZE);
    const currentPage = Math.floor(offset / SPOTIFY_LIBRARY_PAGE_SIZE) + 1;
    const showEnd = Math.min(offset + SPOTIFY_LIBRARY_PAGE_SIZE, total);

    container.innerHTML = `
        <button class="spotify-library-page-btn" onclick="spotifyLibraryPrevPage()" ${currentPage <= 1 ? 'disabled' : ''}>&larr; Previous</button>
        <span class="spotify-library-page-info">${offset + 1}\u2013${showEnd} of ${total}</span>
        <button class="spotify-library-page-btn" onclick="spotifyLibraryNextPage()" ${currentPage >= totalPages ? 'disabled' : ''}>Next &rarr;</button>
    `;
}

function spotifyLibraryPrevPage() {
    if (spotifyLibraryPage > 0) {
        spotifyLibraryPage--;
        loadSpotifyLibraryAlbums();
    }
}

function spotifyLibraryNextPage() {
    const totalPages = Math.ceil(spotifyLibraryTotal / SPOTIFY_LIBRARY_PAGE_SIZE);
    if (spotifyLibraryPage < totalPages - 1) {
        spotifyLibraryPage++;
        loadSpotifyLibraryAlbums();
    }
}

async function openSpotifyLibraryAlbumDownload(index) {
    const album = spotifyLibraryAlbums[index];
    if (!album) {
        showToast('Album data not found', 'error');
        return;
    }

    console.log(`\u{1F4E5} Opening download modal for Spotify library album: ${album.album_name}`);
    showLoadingOverlay(`Loading tracks for ${album.album_name}...`);

    try {
        const _params = new URLSearchParams({ name: album.album_name || '', artist: album.artist_name || '' });
        const response = await fetch(`/api/discover/album/spotify/${album.spotify_album_id}?${_params}`);
        if (!response.ok) throw new Error('Failed to fetch album tracks');

        const albumData = await response.json();
        if (!albumData.tracks || albumData.tracks.length === 0) {
            throw new Error('No tracks found in album');
        }

        const spotifyTracks = albumData.tracks.map(track => {
            let artists = track.artists || albumData.artists || [{ name: album.artist_name }];
            if (Array.isArray(artists)) {
                artists = artists.map(a => a.name || a);
            }
            return {
                id: track.id,
                name: track.name,
                artists: artists,
                album: {
                    id: albumData.id,
                    name: albumData.name,
                    album_type: albumData.album_type || 'album',
                    total_tracks: albumData.total_tracks || 0,
                    release_date: albumData.release_date || '',
                    images: albumData.images || []
                },
                duration_ms: track.duration_ms || 0,
                track_number: track.track_number || 0
            };
        });

        const virtualPlaylistId = `spotify_library_${album.spotify_album_id}`;
        const artistContext = {
            id: album.artist_id,
            name: album.artist_name,
            source: 'spotify'
        };
        const albumContext = {
            id: albumData.id,
            name: albumData.name,
            album_type: albumData.album_type || 'album',
            total_tracks: albumData.total_tracks || 0,
            release_date: albumData.release_date || '',
            images: albumData.images || []
        };

        await openDownloadMissingModalForYouTube(virtualPlaylistId, albumData.name, spotifyTracks, artistContext, albumContext);
        hideLoadingOverlay();

    } catch (error) {
        console.error('Error opening Spotify library album download:', error);
        showToast(`Failed to load album: ${error.message}`, 'error');
        hideLoadingOverlay();
    }
}

async function refreshSpotifyLibraryCache() {
    try {
        showToast('Refreshing Spotify library...', 'info');
        const response = await fetch('/api/discover/spotify-library/refresh', { method: 'POST' });
        const data = await response.json();
        if (data.success) {
            showToast('Spotify library refresh started — will update shortly', 'success');
            // Reload after a delay to let the sync run
            setTimeout(() => loadSpotifyLibrarySection(), 10000);
        } else {
            showToast(`Error: ${data.error}`, 'error');
        }
    } catch (error) {
        showToast(`Error: ${error.message}`, 'error');
    }
}

async function downloadMissingSpotifyLibraryAlbums() {
    // Fetch all missing albums (no pagination limit)
    try {
        const response = await fetch('/api/discover/spotify-library?status=missing&limit=500&offset=0');
        const data = await response.json();
        if (!data.success || !data.albums || data.albums.length === 0) {
            showToast('No missing albums to download', 'info');
            return;
        }

        const missing = data.albums.filter(a => !a.in_library);
        if (missing.length === 0) {
            showToast('All albums are already in your library!', 'success');
            return;
        }

        if (!confirm(`Download ${missing.length} missing album${missing.length > 1 ? 's' : ''} from your Spotify library?`)) {
            return;
        }

        showToast(`Starting download for ${missing.length} albums...`, 'info');

        // Download one at a time to avoid overwhelming the system
        for (let i = 0; i < missing.length; i++) {
            const album = missing[i];
            try {
                showToast(`Queuing ${i + 1}/${missing.length}: ${album.album_name}`, 'info');

                const _params = new URLSearchParams({ name: album.album_name || '', artist: album.artist_name || '' });
                const response = await fetch(`/api/discover/album/spotify/${album.spotify_album_id}?${_params}`);
                if (!response.ok) continue;

                const albumData = await response.json();
                if (!albumData.tracks || albumData.tracks.length === 0) continue;

                const spotifyTracks = albumData.tracks.map(track => {
                    let artists = track.artists || albumData.artists || [{ name: album.artist_name }];
                    if (Array.isArray(artists)) artists = artists.map(a => a.name || a);
                    return {
                        id: track.id,
                        name: track.name,
                        artists: artists,
                        album: {
                            id: albumData.id,
                            name: albumData.name,
                            album_type: albumData.album_type || 'album',
                            total_tracks: albumData.total_tracks || 0,
                            release_date: albumData.release_date || '',
                            images: albumData.images || []
                        },
                        duration_ms: track.duration_ms || 0,
                        track_number: track.track_number || 0
                    };
                });

                const virtualPlaylistId = `spotify_library_${album.spotify_album_id}`;
                await openDownloadMissingModalForYouTube(virtualPlaylistId, albumData.name, spotifyTracks, {
                    id: album.artist_id, name: album.artist_name, source: 'spotify'
                }, {
                    id: albumData.id, name: albumData.name, album_type: albumData.album_type || 'album',
                    total_tracks: albumData.total_tracks || 0, release_date: albumData.release_date || '',
                    images: albumData.images || []
                });

            } catch (err) {
                console.error(`Error downloading album ${album.album_name}:`, err);
            }
        }

    } catch (error) {
        console.error('Error downloading missing Spotify library albums:', error);
        showToast(`Error: ${error.message}`, 'error');
    }
}

async function loadDiscoverReleaseRadar() {
    try {
        const playlistContainer = document.getElementById('release-radar-playlist');
        if (!playlistContainer) return;

        playlistContainer.innerHTML = '<div class="discover-loading"><div class="loading-spinner"></div><p>Loading release radar...</p></div>';

        const response = await fetch('/api/discover/release-radar');
        if (!response.ok) {
            throw new Error('Failed to fetch release radar');
        }

        const data = await response.json();
        if (!data.success || !data.tracks || data.tracks.length === 0) {
            playlistContainer.innerHTML = '<div class="discover-empty"><p>No new releases available</p></div>';
            return;
        }

        // Store tracks for download/sync functionality
        discoverReleaseRadarTracks = data.tracks;

        // Build compact playlist HTML
        let html = '<div class="discover-playlist-tracks-compact">';
        data.tracks.forEach((track, index) => {
            const coverUrl = track.album_cover_url || '/static/placeholder-album.png';
            const durationMin = Math.floor(track.duration_ms / 60000);
            const durationSec = Math.floor((track.duration_ms % 60000) / 1000);
            const duration = `${durationMin}:${durationSec.toString().padStart(2, '0')}`;

            html += `
                <div class="discover-playlist-track-compact" data-track-index="${index}">
                    <div class="track-compact-number">${index + 1}</div>
                    <div class="track-compact-image">
                        <img src="${coverUrl}" alt="${track.album_name}" loading="lazy">
                    </div>
                    <div class="track-compact-info">
                        <div class="track-compact-name">${track.track_name}</div>
                        <div class="track-compact-artist">${track.artist_name}</div>
                    </div>
                    <div class="track-compact-album">${track.album_name}</div>
                    <div class="track-compact-duration">${duration}</div>
                </div>
            `;
        });
        html += '</div>';

        playlistContainer.innerHTML = html;

    } catch (error) {
        console.error('Error loading release radar:', error);
        const playlistContainer = document.getElementById('release-radar-playlist');
        if (playlistContainer) {
            playlistContainer.innerHTML = '<div class="discover-empty"><p>Failed to load release radar</p></div>';
        }
    }
}

async function loadDiscoverWeekly() {
    try {
        const playlistContainer = document.getElementById('discovery-weekly-playlist');
        if (!playlistContainer) return;

        playlistContainer.innerHTML = '<div class="discover-loading"><div class="loading-spinner"></div><p>Curating your discovery playlist...</p></div>';

        const response = await fetch('/api/discover/weekly');
        if (!response.ok) {
            throw new Error('Failed to fetch discovery weekly');
        }

        const data = await response.json();
        if (!data.success || !data.tracks || data.tracks.length === 0) {
            playlistContainer.innerHTML = '<div class="discover-empty"><p>No tracks available yet</p></div>';
            return;
        }

        // Store tracks for download/sync functionality
        discoverWeeklyTracks = data.tracks;

        // Build compact playlist HTML
        let html = '<div class="discover-playlist-tracks-compact">';
        data.tracks.forEach((track, index) => {
            const coverUrl = track.album_cover_url || '/static/placeholder-album.png';
            const durationMin = Math.floor(track.duration_ms / 60000);
            const durationSec = Math.floor((track.duration_ms % 60000) / 1000);
            const duration = `${durationMin}:${durationSec.toString().padStart(2, '0')}`;

            html += `
                <div class="discover-playlist-track-compact" data-track-index="${index}">
                    <div class="track-compact-number">${index + 1}</div>
                    <div class="track-compact-image">
                        <img src="${coverUrl}" alt="${track.album_name}" loading="lazy">
                    </div>
                    <div class="track-compact-info">
                        <div class="track-compact-name">${track.track_name}</div>
                        <div class="track-compact-artist">${track.artist_name}</div>
                    </div>
                    <div class="track-compact-album">${track.album_name}</div>
                    <div class="track-compact-duration">${duration}</div>
                </div>
            `;
        });
        html += '</div>';

        playlistContainer.innerHTML = html;

    } catch (error) {
        console.error('Error loading discovery weekly:', error);
        const playlistContainer = document.getElementById('discovery-weekly-playlist');
        if (playlistContainer) {
            playlistContainer.innerHTML = '<div class="discover-empty"><p>Failed to load discovery weekly</p></div>';
        }
    }
}

// ===============================
// DECADE BROWSER
// ===============================

let selectedDecade = null;
let decadeTracks = [];

async function loadDecadeBrowser() {
    try {
        const carousel = document.getElementById('decade-browser-carousel');
        if (!carousel) return;

        // Fetch available decades from backend
        const response = await fetch('/api/discover/decades/available');
        if (!response.ok) {
            throw new Error('Failed to fetch available decades');
        }

        const data = await response.json();
        if (!data.success || !data.decades || data.decades.length === 0) {
            carousel.innerHTML = '<div class="discover-empty"><p>No decade content available yet. Run a watchlist scan to populate your discovery pool!</p></div>';
            return;
        }

        // Build decade cards matching Recent Releases style
        let html = '';
        data.decades.forEach(decade => {
            const icon = getDecadeIcon(decade.year);
            const label = `${decade.year}s`;
            html += `
                <div class="discover-card decade-card-modern" onclick="openDecadePlaylist(${decade.year})">
                    <div class="discover-card-image decade-card-image">
                        <div class="decade-icon-large">${icon}</div>
                    </div>
                    <div class="discover-card-info">
                        <h4 class="discover-card-title">${label}</h4>
                        <p class="discover-card-subtitle">${decade.track_count} tracks</p>
                        <p class="discover-card-meta">Classics</p>
                    </div>
                </div>
            `;
        });

        carousel.innerHTML = html;

    } catch (error) {
        console.error('Error loading decade browser:', error);
        const carousel = document.getElementById('decade-browser-carousel');
        if (carousel) {
            carousel.innerHTML = '<div class="discover-empty"><p>Failed to load decades</p></div>';
        }
    }
}

function getDecadeIcon(year) {
    const icons = {
        1950: '🎺',
        1960: '🎸',
        1970: '🕺',
        1980: '📻',
        1990: '💿',
        2000: '📱',
        2010: '🎧',
        2020: '🌐'
    };
    return icons[year] || '🎵';
}

async function openDecadePlaylist(decade) {
    try {
        showLoadingOverlay(`Loading ${decade}s playlist...`);

        const response = await fetch(`/api/discover/decade/${decade}`);
        if (!response.ok) {
            throw new Error('Failed to fetch decade playlist');
        }

        const data = await response.json();
        if (!data.success || !data.tracks || data.tracks.length === 0) {
            const message = data.message || `No tracks found for the ${decade}s`;
            showToast(message, 'info');
            hideLoadingOverlay();
            return;
        }

        selectedDecade = decade;
        decadeTracks = data.tracks;

        // Open download modal
        const playlistName = `${decade}s Classics`;
        const virtualPlaylistId = `decade_${decade}`;

        await openDownloadMissingModalForYouTube(virtualPlaylistId, playlistName, data.tracks);
        hideLoadingOverlay();

    } catch (error) {
        console.error(`Error opening ${decade}s playlist:`, error);
        showToast(`Failed to load ${decade}s playlist`, 'error');
        hideLoadingOverlay();
    }
}

// ===============================
// GENRE BROWSER
// ===============================

let selectedGenre = null;
let genreTracks = [];

async function loadGenreBrowser() {
    try {
        const carousel = document.getElementById('genre-browser-carousel');
        if (!carousel) return;

        // Fetch available genres from backend
        const response = await fetch('/api/discover/genres/available');
        if (!response.ok) {
            throw new Error('Failed to fetch available genres');
        }

        const data = await response.json();
        if (!data.success || !data.genres || data.genres.length === 0) {
            carousel.innerHTML = '<div class="discover-empty"><p>No genre content available yet. Run a watchlist scan to populate your discovery pool!</p></div>';
            return;
        }

        // Build genre cards matching Recent Releases style
        let html = '';
        data.genres.forEach(genre => {
            const icon = getGenreIcon(genre.name);
            const displayName = capitalizeGenre(genre.name);
            html += `
                <div class="discover-card genre-card-modern" onclick="openGenrePlaylist('${escapeForInlineJs(genre.name)}')">
                    <div class="discover-card-image genre-card-image">
                        <div class="genre-icon-large">${icon}</div>
                    </div>
                    <div class="discover-card-info">
                        <h4 class="discover-card-title">${displayName}</h4>
                        <p class="discover-card-subtitle">${genre.track_count} tracks</p>
                        <p class="discover-card-meta">Curated</p>
                    </div>
                </div>
            `;
        });

        carousel.innerHTML = html;

    } catch (error) {
        console.error('Error loading genre browser:', error);
        const carousel = document.getElementById('genre-browser-carousel');
        if (carousel) {
            carousel.innerHTML = '<div class="discover-empty"><p>Failed to load genres</p></div>';
        }
    }
}

function getGenreIcon(genreName) {
    const genre = genreName.toLowerCase();

    // Parent genre exact matches (consolidated categories)
    if (genre === 'electronic/dance') return '🎹';
    if (genre === 'hip hop/rap') return '🎤';
    if (genre === 'rock') return '🎸';
    if (genre === 'pop') return '🎵';
    if (genre === 'r&b/soul') return '🎙️';
    if (genre === 'jazz') return '🎺';
    if (genre === 'classical') return '🎻';
    if (genre === 'metal') return '🤘';
    if (genre === 'country') return '🪕';
    if (genre === 'folk/indie') return '🎧';
    if (genre === 'latin') return '💃';
    if (genre === 'reggae/dancehall') return '🌴';
    if (genre === 'world') return '🌍';
    if (genre === 'alternative') return '🎭';
    if (genre === 'blues') return '🎸';
    if (genre === 'funk/disco') return '🕺';

    // Fallback: partial matching for specific genres
    if (genre.includes('house') || genre.includes('techno') || genre.includes('edm') ||
        genre.includes('electro') || genre.includes('trance') || genre.includes('electronic')) {
        return '🎹';
    }
    if (genre.includes('hip hop') || genre.includes('rap') || genre.includes('trap')) {
        return '🎤';
    }
    if (genre.includes('rock') || genre.includes('punk')) {
        return '🎸';
    }
    if (genre.includes('metal')) {
        return '🤘';
    }
    if (genre.includes('jazz') || genre.includes('blues')) {
        return '🎺';
    }
    if (genre.includes('pop')) {
        return '🎵';
    }
    if (genre.includes('r&b') || genre.includes('soul')) {
        return '🎙️';
    }
    if (genre.includes('country') || genre.includes('folk')) {
        return '🪕';
    }
    if (genre.includes('classical') || genre.includes('orchestra')) {
        return '🎻';
    }
    if (genre.includes('indie') || genre.includes('alternative')) {
        return '🎧';
    }
    if (genre.includes('latin') || genre.includes('reggaeton') || genre.includes('salsa')) {
        return '💃';
    }
    if (genre.includes('reggae') || genre.includes('dancehall')) {
        return '🌴';
    }
    if (genre.includes('funk') || genre.includes('disco')) {
        return '🕺';
    }

    // Default
    return '🎶';
}

function capitalizeGenre(genre) {
    // Capitalize each word in genre, handling both spaces and slashes
    return genre.split(/(\s|\/)/g)
        .map(part => {
            if (part === ' ' || part === '/') return part;
            return part.charAt(0).toUpperCase() + part.slice(1);
        })
        .join('');
}

function escapeHtml(text) {
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}

async function openGenrePlaylist(genre) {
    try {
        showLoadingOverlay(`Loading ${capitalizeGenre(genre)} playlist...`);

        const response = await fetch(`/api/discover/genre/${encodeURIComponent(genre)}`);
        if (!response.ok) {
            throw new Error('Failed to fetch genre playlist');
        }

        const data = await response.json();
        if (!data.success || !data.tracks || data.tracks.length === 0) {
            const message = data.message || `No tracks found for ${genre}`;
            showToast(message, 'info');
            hideLoadingOverlay();
            return;
        }

        selectedGenre = genre;
        genreTracks = data.tracks;

        // Open download modal
        const playlistName = `${capitalizeGenre(genre)} Mix`;
        const virtualPlaylistId = `genre_${genre.replace(/\s+/g, '_')}`;

        await openDownloadMissingModalForYouTube(virtualPlaylistId, playlistName, data.tracks);
        hideLoadingOverlay();

    } catch (error) {
        console.error(`Error opening ${genre} playlist:`, error);
        showToast(`Failed to load ${genre} playlist`, 'error');
        hideLoadingOverlay();
    }
}

// ===============================
// TIME MACHINE (TABBED BY DECADE)
// ===============================

let decadeTracksCache = {}; // Store tracks for each decade
let activeDecade = null;

async function loadDecadeBrowserTabs() {
    try {
        const tabsContainer = document.getElementById('decade-tabs');
        const contentsContainer = document.getElementById('decade-tab-contents');

        if (!tabsContainer || !contentsContainer) return;

        // Fetch available decades from backend
        const response = await fetch('/api/discover/decades/available');
        if (!response.ok) {
            throw new Error('Failed to fetch available decades');
        }

        const data = await response.json();
        if (!data.success || !data.decades || data.decades.length === 0) {
            tabsContainer.innerHTML = '<div class="discover-empty"><p>No decade content available yet. Run a watchlist scan to populate your discovery pool!</p></div>';
            return;
        }

        // Build decade tabs
        let tabsHTML = '';
        let contentsHTML = '';

        data.decades.forEach((decade, index) => {
            const isActive = index === 0;
            const icon = getDecadeIcon(decade.year);
            const tabId = `decade-${decade.year}`;

            // Tab button
            tabsHTML += `
                <button class="decade-tab ${isActive ? 'active' : ''}"
                        data-decade="${decade.year}"
                        onclick="switchDecadeTab(${decade.year})">
                    ${icon} ${decade.year}s
                </button>
            `;

            // Tab content
            contentsHTML += `
                <div class="decade-tab-content ${isActive ? 'active' : ''}" id="${tabId}-content">
                    <!-- Action Buttons -->
                    <div class="decade-actions">
                        <div class="discover-section-header">
                            <div>
                                <h3 style="margin: 0; color: #fff; font-size: 18px;">${decade.year}s Classics</h3>
                                <p id="${tabId}-subtitle" style="margin: 4px 0 0 0; color: #999; font-size: 13px;">${decade.track_count} tracks</p>
                            </div>
                            <div class="discover-section-actions">
                                <button class="action-button secondary" onclick="openDownloadModalForDecade(${decade.year})" title="Download missing tracks">
                                    <span class="button-icon">↓</span>
                                    <span class="button-text">Download</span>
                                </button>
                                <button class="action-button primary" id="${tabId}-sync-btn" onclick="startDecadeSync(${decade.year})" title="Sync to media server">
                                    <span class="button-icon">⟳</span>
                                    <span class="button-text">Sync</span>
                                </button>
                            </div>
                        </div>

                        <!-- Sync Status Display -->
                        <div class="discover-sync-status" id="${tabId}-sync-status" style="display: none;">
                            <div class="sync-status-content">
                                <div class="sync-status-label">
                                    <span class="sync-icon">⟳</span>
                                    <span>Syncing to media server...</span>
                                </div>
                                <div class="sync-status-stats">
                                    <span class="sync-stat">✓ <span id="${tabId}-sync-completed">0</span></span>
                                    <span class="sync-stat">⏳ <span id="${tabId}-sync-pending">0</span></span>
                                    <span class="sync-stat">✗ <span id="${tabId}-sync-failed">0</span></span>
                                    <span class="sync-stat">(<span id="${tabId}-sync-percentage">0</span>%)</span>
                                </div>
                            </div>
                        </div>
                    </div>

                    <!-- Track List -->
                    <div class="discover-playlist-container compact" id="${tabId}-playlist">
                        <div class="discover-loading"><div class="loading-spinner"></div><p>Loading ${decade.year}s tracks...</p></div>
                    </div>
                </div>
            `;
        });

        tabsContainer.innerHTML = tabsHTML;
        contentsContainer.innerHTML = contentsHTML;

        // Load first decade's tracks
        if (data.decades.length > 0) {
            await loadDecadeTracks(data.decades[0].year);
        }

    } catch (error) {
        console.error('Error loading decade browser tabs:', error);
        const tabsContainer = document.getElementById('decade-tabs');
        if (tabsContainer) {
            tabsContainer.innerHTML = '<div class="discover-empty"><p>Failed to load decades</p></div>';
        }
    }
}

function switchDecadeTab(decade) {
    // Update tab buttons
    const tabs = document.querySelectorAll('.decade-tab');
    tabs.forEach(tab => {
        if (parseInt(tab.getAttribute('data-decade')) === decade) {
            tab.classList.add('active');
        } else {
            tab.classList.remove('active');
        }
    });

    // Update tab content
    const tabContents = document.querySelectorAll('.decade-tab-content');
    tabContents.forEach(content => {
        if (content.id === `decade-${decade}-content`) {
            content.classList.add('active');
        } else {
            content.classList.remove('active');
        }
    });

    // Load tracks if not already loaded
    if (!decadeTracksCache[decade]) {
        loadDecadeTracks(decade);
    }
}

async function loadDecadeTracks(decade) {
    try {
        const playlistContainer = document.getElementById(`decade-${decade}-playlist`);
        if (!playlistContainer) return;

        const response = await fetch(`/api/discover/decade/${decade}`);
        if (!response.ok) {
            throw new Error('Failed to fetch decade playlist');
        }

        const data = await response.json();
        if (!data.success || !data.tracks || data.tracks.length === 0) {
            playlistContainer.innerHTML = '<div class="discover-empty"><p>No tracks found for the ' + decade + 's</p></div>';
            return;
        }

        // Store tracks in cache
        decadeTracksCache[decade] = data.tracks;
        activeDecade = decade;

        // Build compact playlist HTML
        let html = '<div class="discover-playlist-tracks-compact">';
        data.tracks.forEach((track, index) => {
            // Extract track data from track_data_json if available
            let trackData = track;
            if (track.track_data_json) {
                trackData = track.track_data_json;
            }

            // Get track properties with fallbacks
            const trackName = trackData.name || trackData.track_name || track.track_name || 'Unknown Track';
            const artistName = trackData.artists?.[0]?.name || trackData.artists?.[0] || trackData.artist_name || track.artist_name || 'Unknown Artist';
            const albumName = trackData.album?.name || trackData.album_name || track.album_name || 'Unknown Album';
            const coverUrl = trackData.album?.images?.[0]?.url || track.album_cover_url || '/static/placeholder-album.png';
            const durationMs = trackData.duration_ms || track.duration_ms || 0;

            const durationMin = Math.floor(durationMs / 60000);
            const durationSec = Math.floor((durationMs % 60000) / 1000);
            const duration = `${durationMin}:${durationSec.toString().padStart(2, '0')}`;

            html += `
                <div class="discover-playlist-track-compact" data-track-index="${index}">
                    <div class="track-compact-number">${index + 1}</div>
                    <div class="track-compact-image">
                        <img src="${coverUrl}" alt="${albumName}" loading="lazy">
                    </div>
                    <div class="track-compact-info">
                        <div class="track-compact-name">${trackName}</div>
                        <div class="track-compact-artist">${artistName}</div>
                    </div>
                    <div class="track-compact-album">${albumName}</div>
                    <div class="track-compact-duration">${duration}</div>
                </div>
            `;
        });
        html += '</div>';

        playlistContainer.innerHTML = html;

    } catch (error) {
        console.error('Error loading decade tracks:', error);
        const playlistContainer = document.getElementById(`decade-${decade}-playlist`);
        if (playlistContainer) {
            playlistContainer.innerHTML = '<div class="discover-empty"><p>Failed to load decade tracks</p></div>';
        }
    }
}

async function startDecadeSync(decade) {
    const tracks = decadeTracksCache[decade];
    if (!tracks || tracks.length === 0) {
        showToast('No tracks available for this decade', 'warning');
        return;
    }

    // Convert to format expected by sync API
    const spotifyTracks = tracks.map(track => {
        // Extract track data from track_data_json if available
        let trackData = track;
        if (track.track_data_json) {
            trackData = track.track_data_json;
        }

        // Build properly formatted Spotify track object
        let spotifyTrack = {
            id: trackData.id || track.spotify_track_id,
            name: trackData.name || trackData.track_name || track.track_name,
            artists: trackData.artists || [{ name: trackData.artist_name || track.artist_name }],
            album: trackData.album || {
                name: trackData.album_name || track.album_name,
                images: trackData.album?.images || (track.album_cover_url ? [{ url: track.album_cover_url }] : [])
            },
            duration_ms: trackData.duration_ms || track.duration_ms || 0
        };

        // Normalize artists to array of strings for sync compatibility
        if (spotifyTrack.artists && Array.isArray(spotifyTrack.artists)) {
            spotifyTrack.artists = spotifyTrack.artists.map(a => a.name || a);
        }

        return spotifyTrack;
    });

    const virtualPlaylistId = `discover_decade_${decade}`;
    playlistTrackCache[virtualPlaylistId] = spotifyTracks;

    const virtualPlaylist = {
        id: virtualPlaylistId,
        name: `${decade}s Classics`,
        track_count: spotifyTracks.length
    };

    if (!spotifyPlaylists.find(p => p.id === virtualPlaylistId)) {
        spotifyPlaylists.push(virtualPlaylist);
    }

    // Show sync status display
    const statusDisplay = document.getElementById(`decade-${decade}-sync-status`);
    if (statusDisplay) statusDisplay.style.display = 'block';

    // Disable sync button
    const syncButton = document.getElementById(`decade-${decade}-sync-btn`);
    if (syncButton) {
        syncButton.disabled = true;
        syncButton.style.opacity = '0.5';
        syncButton.style.cursor = 'not-allowed';
    }

    // Start sync
    await startPlaylistSync(virtualPlaylistId);

    // Start polling
    startDecadeSyncPolling(decade, virtualPlaylistId);
}

function startDecadeSyncPolling(decade, virtualPlaylistId) {
    const pollerId = `decade_${decade}`;

    if (discoverSyncPollers[pollerId]) {
        clearInterval(discoverSyncPollers[pollerId]);
    }

    // Phase 5: Subscribe via WebSocket
    if (socketConnected) {
        socket.emit('sync:subscribe', { playlist_ids: [virtualPlaylistId] });
        _syncProgressCallbacks[virtualPlaylistId] = (data) => {
            const progress = data.progress || {};
            const total = progress.total_tracks || 0;
            const matched = progress.matched_tracks || 0;
            const failed = progress.failed_tracks || 0;
            const processed = matched + failed;
            const pending = total - processed;
            const pct = total > 0 ? Math.round((processed / total) * 100) : 0;
            const el = (id) => document.getElementById(id);
            if (el(`decade-${decade}-sync-completed`)) el(`decade-${decade}-sync-completed`).textContent = matched;
            if (el(`decade-${decade}-sync-pending`)) el(`decade-${decade}-sync-pending`).textContent = pending;
            if (el(`decade-${decade}-sync-failed`)) el(`decade-${decade}-sync-failed`).textContent = failed;
            if (el(`decade-${decade}-sync-percentage`)) el(`decade-${decade}-sync-percentage`).textContent = pct;
            if (data.status === 'finished') {
                if (discoverSyncPollers[pollerId]) { clearInterval(discoverSyncPollers[pollerId]); delete discoverSyncPollers[pollerId]; }
                socket.emit('sync:unsubscribe', { playlist_ids: [virtualPlaylistId] });
                delete _syncProgressCallbacks[virtualPlaylistId];
                const syncButton = el(`decade-${decade}-sync-btn`);
                if (syncButton) { syncButton.disabled = false; syncButton.style.opacity = '1'; syncButton.style.cursor = 'pointer'; }
                const _m2 = progress.matched_tracks || matched || 0;
                const _t2 = progress.total_tracks || total || 0;
                const _miss2 = _t2 - _m2;
                if (_miss2 > 0) {
                    showToast(`${decade}s Classics: ${_m2}/${_t2} matched, ${_miss2} missing`, 'warning');
                } else {
                    showToast(`${decade}s Classics: all ${_t2} tracks matched!`, 'success');
                }
                if (el(`decade-${decade}-sync-percentage`)) el(`decade-${decade}-sync-percentage`).textContent = '100';
                if (el(`decade-${decade}-sync-pending`)) el(`decade-${decade}-sync-pending`).textContent = '0';
                setTimeout(() => { const sd = el(`decade-${decade}-sync-status`); if (sd) sd.style.display = 'none'; }, 5000);
            }
        };
    }

    discoverSyncPollers[pollerId] = setInterval(async () => {
        // Always poll — no dedicated WebSocket events for discovery progress
        try {
            const response = await fetch(`/api/sync/status/${virtualPlaylistId}`);
            if (!response.ok) return;

            const data = await response.json();
            const progress = data.progress || {};

            const completedEl = document.getElementById(`decade-${decade}-sync-completed`);
            const pendingEl = document.getElementById(`decade-${decade}-sync-pending`);
            const failedEl = document.getElementById(`decade-${decade}-sync-failed`);
            const percentageEl = document.getElementById(`decade-${decade}-sync-percentage`);

            const total = progress.total_tracks || 0;
            const matched = progress.matched_tracks || 0;
            const failed = progress.failed_tracks || 0;
            const processed = matched + failed;
            const pending = total - processed;
            const completionPercentage = total > 0 ? Math.round((processed / total) * 100) : 0;

            if (completedEl) completedEl.textContent = matched;
            if (pendingEl) pendingEl.textContent = pending;
            if (failedEl) failedEl.textContent = failed;
            if (percentageEl) percentageEl.textContent = completionPercentage;

            if (data.status === 'finished') {
                clearInterval(discoverSyncPollers[pollerId]);
                delete discoverSyncPollers[pollerId];

                const syncButton = document.getElementById(`decade-${decade}-sync-btn`);
                if (syncButton) {
                    syncButton.disabled = false;
                    syncButton.style.opacity = '1';
                    syncButton.style.cursor = 'pointer';
                }

                const missing = total - matched;
                if (missing > 0) {
                    showToast(`${decade}s Classics: ${matched}/${total} matched, ${missing} missing`, 'warning');
                } else {
                    showToast(`${decade}s Classics: all ${total} tracks matched!`, 'success');
                }

                if (percentageEl) percentageEl.textContent = '100';
                if (pendingEl) pendingEl.textContent = '0';
                setTimeout(() => {
                    const statusDisplay = document.getElementById(`decade-${decade}-sync-status`);
                    if (statusDisplay) statusDisplay.style.display = 'none';
                }, 5000);
            }
        } catch (error) {
            console.error(`Error polling sync status for decade ${decade}:`, error);
        }
    }, 500);
}

async function openDownloadModalForDecade(decade) {
    const tracks = decadeTracksCache[decade];
    if (!tracks || tracks.length === 0) {
        showToast('No tracks available for this decade', 'warning');
        return;
    }

    // Convert to format expected by download modal
    const spotifyTracks = tracks.map(track => {
        // Extract track data from track_data_json if available
        let trackData = track;
        if (track.track_data_json) {
            trackData = track.track_data_json;
        }

        // Build properly formatted Spotify track object
        let spotifyTrack = {
            id: trackData.id || track.spotify_track_id,
            name: trackData.name || trackData.track_name || track.track_name,
            artists: trackData.artists || [{ name: trackData.artist_name || track.artist_name }],
            album: trackData.album || {
                name: trackData.album_name || track.album_name,
                images: trackData.album?.images || (track.album_cover_url ? [{ url: track.album_cover_url }] : [])
            },
            duration_ms: trackData.duration_ms || track.duration_ms || 0
        };

        return spotifyTrack;
    });

    const playlistName = `${decade}s Classics`;
    const virtualPlaylistId = `decade_${decade}`;

    await openDownloadMissingModalForYouTube(virtualPlaylistId, playlistName, spotifyTracks);
}

// ===============================
// BROWSE BY GENRE (TABBED BY GENRE)
// ===============================

let genreTracksCache = {}; // Store tracks for each genre
let activeGenre = null;
let availableGenres = [];

async function loadGenreBrowserTabs() {
    try {
        const tabsContainer = document.getElementById('genre-tabs');
        const contentsContainer = document.getElementById('genre-tab-contents');

        if (!tabsContainer || !contentsContainer) return;

        // Fetch available genres from backend
        const response = await fetch('/api/discover/genres/available');
        if (!response.ok) {
            throw new Error('Failed to fetch available genres');
        }

        const data = await response.json();
        if (!data.success || !data.genres || data.genres.length === 0) {
            tabsContainer.innerHTML = '<div class="discover-empty"><p>No genre content available yet. Run a watchlist scan to populate your discovery pool!</p></div>';
            return;
        }

        availableGenres = data.genres;

        // Build genre tabs (limit to first 8-10 to avoid overcrowding)
        const displayGenres = data.genres.slice(0, 10);
        let tabsHTML = '';
        let contentsHTML = '';

        displayGenres.forEach((genre, index) => {
            const isActive = index === 0;
            const icon = getGenreIcon(genre.name);
            const genreName = genre.name;
            const genreId = genreName.replace(/\s+/g, '-').replace(/[^a-zA-Z0-9-]/g, '');
            const tabId = `genre-${genreId}`;

            // Tab button
            tabsHTML += `
                <button class="genre-tab ${isActive ? 'active' : ''}"
                        data-genre="${escapeHtml(genreName)}"
                        onclick="switchGenreTab('${escapeForInlineJs(genreName)}')">
                    ${icon} ${capitalizeGenre(genreName)}
                </button>
            `;

            // Tab content
            contentsHTML += `
                <div class="genre-tab-content ${isActive ? 'active' : ''}" id="${tabId}-content" data-genre="${escapeHtml(genreName)}">
                    <!-- Action Buttons -->
                    <div class="genre-actions">
                        <div class="discover-section-header">
                            <div>
                                <h3 style="margin: 0; color: #fff; font-size: 18px;">${capitalizeGenre(genreName)} Mix</h3>
                                <p id="${tabId}-subtitle" style="margin: 4px 0 0 0; color: #999; font-size: 13px;">${genre.track_count} tracks</p>
                            </div>
                            <div class="discover-section-actions">
                                <button class="action-button secondary" onclick="openDownloadModalForGenre('${escapeForInlineJs(genreName)}')" title="Download missing tracks">
                                    <span class="button-icon">↓</span>
                                    <span class="button-text">Download</span>
                                </button>
                                <button class="action-button primary" id="${tabId}-sync-btn" onclick="startGenreSync('${escapeForInlineJs(genreName)}')" title="Sync to media server">
                                    <span class="button-icon">⟳</span>
                                    <span class="button-text">Sync</span>
                                </button>
                            </div>
                        </div>

                        <!-- Sync Status Display -->
                        <div class="discover-sync-status" id="${tabId}-sync-status" style="display: none;">
                            <div class="sync-status-content">
                                <div class="sync-status-label">
                                    <span class="sync-icon">⟳</span>
                                    <span>Syncing to media server...</span>
                                </div>
                                <div class="sync-status-stats">
                                    <span class="sync-stat">✓ <span id="${tabId}-sync-completed">0</span></span>
                                    <span class="sync-stat">⏳ <span id="${tabId}-sync-pending">0</span></span>
                                    <span class="sync-stat">✗ <span id="${tabId}-sync-failed">0</span></span>
                                    <span class="sync-stat">(<span id="${tabId}-sync-percentage">0</span>%)</span>
                                </div>
                            </div>
                        </div>
                    </div>

                    <!-- Track List -->
                    <div class="discover-playlist-container compact" id="${tabId}-playlist">
                        <div class="discover-loading"><div class="loading-spinner"></div><p>Loading ${capitalizeGenre(genreName)} tracks...</p></div>
                    </div>
                </div>
            `;
        });

        tabsContainer.innerHTML = tabsHTML;
        contentsContainer.innerHTML = contentsHTML;

        // Load first genre's tracks
        if (displayGenres.length > 0) {
            await loadGenreTracks(displayGenres[0].name);
        }

    } catch (error) {
        console.error('Error loading genre browser tabs:', error);
        const tabsContainer = document.getElementById('genre-tabs');
        if (tabsContainer) {
            tabsContainer.innerHTML = '<div class="discover-empty"><p>Failed to load genres</p></div>';
        }
    }
}

function switchGenreTab(genreName) {
    // Update tab buttons
    const tabs = document.querySelectorAll('.genre-tab');
    tabs.forEach(tab => {
        if (tab.getAttribute('data-genre') === genreName) {
            tab.classList.add('active');
        } else {
            tab.classList.remove('active');
        }
    });

    // Update tab content
    const tabContents = document.querySelectorAll('.genre-tab-content');
    tabContents.forEach(content => {
        if (content.getAttribute('data-genre') === genreName) {
            content.classList.add('active');
        } else {
            content.classList.remove('active');
        }
    });

    // Load tracks if not already loaded
    if (!genreTracksCache[genreName]) {
        loadGenreTracks(genreName);
    }
}

async function loadGenreTracks(genreName) {
    try {
        const genreId = genreName.replace(/\s+/g, '-').replace(/[^a-zA-Z0-9-]/g, '');
        const playlistContainer = document.getElementById(`genre-${genreId}-playlist`);
        if (!playlistContainer) return;

        const response = await fetch(`/api/discover/genre/${encodeURIComponent(genreName)}`);
        if (!response.ok) {
            throw new Error('Failed to fetch genre playlist');
        }

        const data = await response.json();
        if (!data.success || !data.tracks || data.tracks.length === 0) {
            playlistContainer.innerHTML = `<div class="discover-empty"><p>No tracks found for ${capitalizeGenre(genreName)}</p></div>`;
            return;
        }

        // Store tracks in cache
        genreTracksCache[genreName] = data.tracks;
        activeGenre = genreName;

        // Build compact playlist HTML
        let html = '<div class="discover-playlist-tracks-compact">';
        data.tracks.forEach((track, index) => {
            // Extract track data from track_data_json if available
            let trackData = track;
            if (track.track_data_json) {
                trackData = track.track_data_json;
            }

            // Get track properties with fallbacks
            const trackName = trackData.name || trackData.track_name || track.track_name || 'Unknown Track';
            const artistName = trackData.artists?.[0]?.name || trackData.artists?.[0] || trackData.artist_name || track.artist_name || 'Unknown Artist';
            const albumName = trackData.album?.name || trackData.album_name || track.album_name || 'Unknown Album';
            const coverUrl = trackData.album?.images?.[0]?.url || track.album_cover_url || '/static/placeholder-album.png';
            const durationMs = trackData.duration_ms || track.duration_ms || 0;

            const durationMin = Math.floor(durationMs / 60000);
            const durationSec = Math.floor((durationMs % 60000) / 1000);
            const duration = `${durationMin}:${durationSec.toString().padStart(2, '0')}`;

            html += `
                <div class="discover-playlist-track-compact" data-track-index="${index}">
                    <div class="track-compact-number">${index + 1}</div>
                    <div class="track-compact-image">
                        <img src="${coverUrl}" alt="${albumName}" loading="lazy">
                    </div>
                    <div class="track-compact-info">
                        <div class="track-compact-name">${trackName}</div>
                        <div class="track-compact-artist">${artistName}</div>
                    </div>
                    <div class="track-compact-album">${albumName}</div>
                    <div class="track-compact-duration">${duration}</div>
                </div>
            `;
        });
        html += '</div>';

        playlistContainer.innerHTML = html;

    } catch (error) {
        console.error('Error loading genre tracks:', error);
        const genreId = genreName.replace(/\s+/g, '-').replace(/[^a-zA-Z0-9-]/g, '');
        const playlistContainer = document.getElementById(`genre-${genreId}-playlist`);
        if (playlistContainer) {
            playlistContainer.innerHTML = '<div class="discover-empty"><p>Failed to load genre tracks</p></div>';
        }
    }
}

async function startGenreSync(genreName) {
    const tracks = genreTracksCache[genreName];
    if (!tracks || tracks.length === 0) {
        showToast('No tracks available for this genre', 'warning');
        return;
    }

    const genreId = genreName.replace(/\s+/g, '-').replace(/[^a-zA-Z0-9-]/g, '');

    // Convert to format expected by sync API
    const spotifyTracks = tracks.map(track => {
        // Extract track data from track_data_json if available
        let trackData = track;
        if (track.track_data_json) {
            trackData = track.track_data_json;
        }

        // Build properly formatted Spotify track object
        let spotifyTrack = {
            id: trackData.id || track.spotify_track_id,
            name: trackData.name || trackData.track_name || track.track_name,
            artists: trackData.artists || [{ name: trackData.artist_name || track.artist_name }],
            album: trackData.album || {
                name: trackData.album_name || track.album_name,
                images: trackData.album?.images || (track.album_cover_url ? [{ url: track.album_cover_url }] : [])
            },
            duration_ms: trackData.duration_ms || track.duration_ms || 0
        };

        // Normalize artists to array of strings for sync compatibility
        if (spotifyTrack.artists && Array.isArray(spotifyTrack.artists)) {
            spotifyTrack.artists = spotifyTrack.artists.map(a => a.name || a);
        }

        return spotifyTrack;
    });

    const virtualPlaylistId = `discover_genre_${genreName.replace(/\s+/g, '_')}`;
    playlistTrackCache[virtualPlaylistId] = spotifyTracks;

    const virtualPlaylist = {
        id: virtualPlaylistId,
        name: `${capitalizeGenre(genreName)} Mix`,
        track_count: spotifyTracks.length
    };

    if (!spotifyPlaylists.find(p => p.id === virtualPlaylistId)) {
        spotifyPlaylists.push(virtualPlaylist);
    }

    // Show sync status display
    const statusDisplay = document.getElementById(`genre-${genreId}-sync-status`);
    if (statusDisplay) statusDisplay.style.display = 'block';

    // Disable sync button
    const syncButton = document.getElementById(`genre-${genreId}-sync-btn`);
    if (syncButton) {
        syncButton.disabled = true;
        syncButton.style.opacity = '0.5';
        syncButton.style.cursor = 'not-allowed';
    }

    // Start sync
    await startPlaylistSync(virtualPlaylistId);

    // Start polling
    startGenreSyncPolling(genreName, genreId, virtualPlaylistId);
}

function startGenreSyncPolling(genreName, genreId, virtualPlaylistId) {
    const pollerId = `genre_${genreId}`;

    if (discoverSyncPollers[pollerId]) {
        clearInterval(discoverSyncPollers[pollerId]);
    }

    // Phase 5: Subscribe via WebSocket
    if (socketConnected) {
        socket.emit('sync:subscribe', { playlist_ids: [virtualPlaylistId] });
        _syncProgressCallbacks[virtualPlaylistId] = (data) => {
            const progress = data.progress || {};
            const total = progress.total_tracks || 0;
            const matched = progress.matched_tracks || 0;
            const failed = progress.failed_tracks || 0;
            const processed = matched + failed;
            const pending = total - processed;
            const pct = total > 0 ? Math.round((processed / total) * 100) : 0;
            const el = (id) => document.getElementById(id);
            if (el(`genre-${genreId}-sync-completed`)) el(`genre-${genreId}-sync-completed`).textContent = matched;
            if (el(`genre-${genreId}-sync-pending`)) el(`genre-${genreId}-sync-pending`).textContent = pending;
            if (el(`genre-${genreId}-sync-failed`)) el(`genre-${genreId}-sync-failed`).textContent = failed;
            if (el(`genre-${genreId}-sync-percentage`)) el(`genre-${genreId}-sync-percentage`).textContent = pct;
            if (data.status === 'finished') {
                if (discoverSyncPollers[pollerId]) { clearInterval(discoverSyncPollers[pollerId]); delete discoverSyncPollers[pollerId]; }
                socket.emit('sync:unsubscribe', { playlist_ids: [virtualPlaylistId] });
                delete _syncProgressCallbacks[virtualPlaylistId];
                const syncButton = el(`genre-${genreId}-sync-btn`);
                if (syncButton) { syncButton.disabled = false; syncButton.style.opacity = '1'; syncButton.style.cursor = 'pointer'; }
                const _m3 = progress.matched_tracks || matched || 0;
                const _t3 = progress.total_tracks || total || 0;
                const _miss3 = _t3 - _m3;
                if (_miss3 > 0) {
                    showToast(`${capitalizeGenre(genreName)} Mix: ${_m3}/${_t3} matched, ${_miss3} missing`, 'warning');
                } else {
                    showToast(`${capitalizeGenre(genreName)} Mix: all ${_t3} tracks matched!`, 'success');
                }
                if (el(`genre-${genreId}-sync-percentage`)) el(`genre-${genreId}-sync-percentage`).textContent = '100';
                if (el(`genre-${genreId}-sync-pending`)) el(`genre-${genreId}-sync-pending`).textContent = '0';
                setTimeout(() => { const sd = el(`genre-${genreId}-sync-status`); if (sd) sd.style.display = 'none'; }, 5000);
            }
        };
    }

    discoverSyncPollers[pollerId] = setInterval(async () => {
        // Always poll — no dedicated WebSocket events for discovery progress
        try {
            const response = await fetch(`/api/sync/status/${virtualPlaylistId}`);
            if (!response.ok) return;

            const data = await response.json();
            const progress = data.progress || {};

            const completedEl = document.getElementById(`genre-${genreId}-sync-completed`);
            const pendingEl = document.getElementById(`genre-${genreId}-sync-pending`);
            const failedEl = document.getElementById(`genre-${genreId}-sync-failed`);
            const percentageEl = document.getElementById(`genre-${genreId}-sync-percentage`);

            const total = progress.total_tracks || 0;
            const matched = progress.matched_tracks || 0;
            const failed = progress.failed_tracks || 0;
            const processed = matched + failed;
            const pending = total - processed;
            const completionPercentage = total > 0 ? Math.round((processed / total) * 100) : 0;

            if (completedEl) completedEl.textContent = matched;
            if (pendingEl) pendingEl.textContent = pending;
            if (failedEl) failedEl.textContent = failed;
            if (percentageEl) percentageEl.textContent = completionPercentage;

            if (data.status === 'finished') {
                clearInterval(discoverSyncPollers[pollerId]);
                delete discoverSyncPollers[pollerId];

                const syncButton = document.getElementById(`genre-${genreId}-sync-btn`);
                if (syncButton) {
                    syncButton.disabled = false;
                    syncButton.style.opacity = '1';
                    syncButton.style.cursor = 'pointer';
                }

                const missing = total - matched;
                if (missing > 0) {
                    showToast(`${capitalizeGenre(genreName)} Mix: ${matched}/${total} matched, ${missing} missing`, 'warning');
                } else {
                    showToast(`${capitalizeGenre(genreName)} Mix: all ${total} tracks matched!`, 'success');
                }

                if (percentageEl) percentageEl.textContent = '100';
                if (pendingEl) pendingEl.textContent = '0';
                setTimeout(() => {
                    const statusDisplay = document.getElementById(`genre-${genreId}-sync-status`);
                    if (statusDisplay) statusDisplay.style.display = 'none';
                }, 5000);
            }
        } catch (error) {
            console.error(`Error polling sync status for genre ${genreName}:`, error);
        }
    }, 500);
}

async function openDownloadModalForGenre(genreName) {
    const tracks = genreTracksCache[genreName];
    if (!tracks || tracks.length === 0) {
        showToast('No tracks available for this genre', 'warning');
        return;
    }

    // Convert to format expected by download modal
    const spotifyTracks = tracks.map(track => {
        // Extract track data from track_data_json if available
        let trackData = track;
        if (track.track_data_json) {
            trackData = track.track_data_json;
        }

        // Build properly formatted Spotify track object
        let spotifyTrack = {
            id: trackData.id || track.spotify_track_id,
            name: trackData.name || trackData.track_name || track.track_name,
            artists: trackData.artists || [{ name: trackData.artist_name || track.artist_name }],
            album: trackData.album || {
                name: trackData.album_name || track.album_name,
                images: trackData.album?.images || (track.album_cover_url ? [{ url: track.album_cover_url }] : [])
            },
            duration_ms: trackData.duration_ms || track.duration_ms || 0
        };

        return spotifyTrack;
    });

    const playlistName = `${capitalizeGenre(genreName)} Mix`;
    const virtualPlaylistId = `genre_${genreName.replace(/\s+/g, '_')}`;

    await openDownloadMissingModalForYouTube(virtualPlaylistId, playlistName, spotifyTracks);
}

// ===============================
// LISTENBRAINZ PLAYLISTS
// ===============================

let listenbrainzPlaylistsCache = {}; // Store playlists by type
let listenbrainzTracksCache = {}; // Store tracks for each playlist
let activeListenBrainzTab = 'recommendations'; // Track active tab
let activeListenBrainzSubTab = null; // Track active sub-tab within recommendations

// ── Last.fm Track Radio ──────────────────────────────────────────────────────

let _lastfmRadioDebounceTimer = null;
let _lastfmRadioSelected = null; // {name, artist}

function debouncedLastfmTrackSearch(query) {
    clearTimeout(_lastfmRadioDebounceTimer);
    const q = (query || '').trim();
    if (!q) {
        document.getElementById('lastfm-radio-dropdown').style.display = 'none';
        return;
    }
    _lastfmRadioDebounceTimer = setTimeout(() => _runLastfmTrackSearch(q), 400);
}

async function _runLastfmTrackSearch(q) {
    if (q.length < 2) return;
    const dropdown = document.getElementById('lastfm-radio-dropdown');
    // Show a mini spinner while fetching
    dropdown.innerHTML = '<div class="lastfm-radio-searching"><div class="server-search-spinner" style="width:14px;height:14px;margin:0 auto;"></div></div>';
    dropdown.style.display = 'block';
    try {
        const res = await fetch(`/api/lastfm/search/tracks?q=${encodeURIComponent(q)}`);
        if (!res.ok) { dropdown.style.display = 'none'; return; }
        const data = await res.json();
        if (!data.results || data.results.length === 0) {
            dropdown.style.display = 'none';
            return;
        }
        dropdown.innerHTML = data.results.map(t => {
            const imgHtml = t.image_url
                ? `<img src="${t.image_url}" alt="" loading="lazy" onerror="this.parentElement.innerHTML='<div class=\\'lastfm-radio-art-empty\\'></div>'">`
                : '<div class="lastfm-radio-art-empty"></div>';
            const listeners = t.listeners > 0
                ? `<span class="lastfm-radio-result-listeners">${(t.listeners / 1000).toFixed(0)}k listeners</span>`
                : '';
            return `
                <div class="lastfm-radio-result" onclick="selectLastfmRadioTrack(decodeURIComponent('${encodeURIComponent(t.name)}'), decodeURIComponent('${encodeURIComponent(t.artist)}'))">
                    <div class="lastfm-radio-result-art">${imgHtml}</div>
                    <div class="lastfm-radio-result-meta">
                        <span class="lastfm-radio-result-track">${t.name}</span>
                        <span class="lastfm-radio-result-artist">${t.artist}${listeners ? ' · ' + t.listeners.toLocaleString() + ' listeners' : ''}</span>
                    </div>
                </div>`;
        }).join('');
        dropdown.style.display = 'block';
    } catch (e) {
        console.error('Last.fm search error:', e);
        dropdown.style.display = 'none';
    }
}

function selectLastfmRadioTrack(name, artist) {
    // Close dropdown and update input to show selection
    document.getElementById('lastfm-radio-dropdown').style.display = 'none';
    document.getElementById('lastfm-radio-input').value = `${name} — ${artist}`;
    document.getElementById('lastfm-radio-input').blur();
    // Immediately kick off generation
    _generateLastfmRadioFor(name, artist);
}

function clearLastfmRadioSelection() {
    document.getElementById('lastfm-radio-input').value = '';
    document.getElementById('lastfm-radio-dropdown').style.display = 'none';
}

// Keep generateLastfmRadio as public alias (called by nothing now but harmless)
async function generateLastfmRadio() {
    const input = (document.getElementById('lastfm-radio-input').value || '').trim();
    if (!input) return;
    // Parse "Track — Artist" format if present
    const parts = input.split(' — ');
    if (parts.length >= 2) {
        await _generateLastfmRadioFor(parts[0].trim(), parts[1].trim());
    }
}

async function _generateLastfmRadioFor(name, artist) {
    const container = document.getElementById('lastfm-radio-playlists');
    const input = document.getElementById('lastfm-radio-input');

    // Show loading state in the playlists area
    if (container) {
        container.innerHTML = `
            <div class="lastfm-radio-generating">
                <div class="server-search-spinner"></div>
                <p>Building radio for <strong>${name}</strong> by <strong>${artist}</strong>…</p>
            </div>`;
    }
    if (input) input.disabled = true;

    try {
        const res = await fetch('/api/lastfm/radio/generate', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ track_name: name, artist_name: artist }),
        });
        const data = await res.json();
        if (!data.success) {
            if (container) container.innerHTML = '';
            showToast(data.error || 'Failed to generate radio', 'error');
            return;
        }
        // Reload all radio playlist cards
        await _loadLastfmRadioPlaylists();
    } catch (e) {
        if (container) container.innerHTML = '';
        showToast('Error generating Last.fm radio', 'error');
        console.error(e);
    } finally {
        if (input) input.disabled = false;
    }
}

async function initializeLastfmRadioSection() {
    try {
        const cfgRes = await fetch('/api/lastfm/configured');
        if (!cfgRes.ok) return;
        const { configured } = await cfgRes.json();
        const section = document.getElementById('lastfm-radio-section');
        if (!section) return;
        if (!configured) {
            section.style.display = 'none';
            return;
        }
        section.style.display = '';
        await _loadLastfmRadioPlaylists();
    } catch (e) {
        console.error('Error initializing Last.fm Radio section:', e);
    }
}

async function _loadLastfmRadioPlaylists() {
    const container = document.getElementById('lastfm-radio-playlists');
    if (!container) return;
    try {
        const res = await fetch('/api/discover/listenbrainz/lastfm-radio');
        if (!res.ok) return;
        const data = await res.json();
        if (!data.success || !data.playlists || data.playlists.length === 0) {
            container.innerHTML = '';
            return;
        }
        // Reuse the same LB playlist card builder — cards are identical
        container.innerHTML = buildListenBrainzPlaylistsHtml(data.playlists, 'lastfm_radio');
        loadTracksForPlaylists(data.playlists);
    } catch (e) {
        console.error('Error loading Last.fm radio playlists:', e);
    }
}

// Close dropdown when clicking outside
document.addEventListener('click', (e) => {
    const section = document.getElementById('lastfm-radio-search-section');
    if (section && !section.contains(e.target)) {
        const dd = document.getElementById('lastfm-radio-dropdown');
        if (dd) dd.style.display = 'none';
    }
});

// ────────────────────────────────────────────────────────────────────────────

async function initializeListenBrainzTabs() {
    try {
        console.log('🧠 Initializing ListenBrainz tabs...');

        // Fetch all playlist types
        const [createdForRes, userPlaylistsRes, collaborativeRes] = await Promise.all([
            fetch('/api/discover/listenbrainz/created-for'),
            fetch('/api/discover/listenbrainz/user-playlists'),
            fetch('/api/discover/listenbrainz/collaborative'),
        ]);

        console.log('📡 API Responses:', {
            createdFor: createdForRes.status,
            userPlaylists: userPlaylistsRes.status,
            collaborative: collaborativeRes.status,
        });

        const tabs = [
            { id: 'recommendations', label: '🎁 Recommendations', hasData: false },
            { id: 'user', label: '📚 Your Playlists', hasData: false },
            { id: 'collaborative', label: '🤝 Collaborative', hasData: false },
        ];

        // Track LB username for header display
        let lbUsername = null;

        // Check which tabs have data
        if (createdForRes.ok) {
            const data = await createdForRes.json();
            console.log('📋 Created For data:', data);
            if (data.username) lbUsername = data.username;
            if (data.success && data.playlists && data.playlists.length > 0) {
                listenbrainzPlaylistsCache['recommendations'] = data.playlists;
                tabs[0].hasData = true;
                console.log(`✅ Found ${data.playlists.length} recommendation playlists`);
            }
        }

        if (userPlaylistsRes.ok) {
            const data = await userPlaylistsRes.json();
            console.log('📚 User Playlists data:', data);
            if (data.username && !lbUsername) lbUsername = data.username;
            if (data.success && data.playlists && data.playlists.length > 0) {
                listenbrainzPlaylistsCache['user'] = data.playlists;
                tabs[1].hasData = true;
                console.log(`✅ Found ${data.playlists.length} user playlists`);
            }
        }

        if (collaborativeRes.ok) {
            const data = await collaborativeRes.json();
            console.log('🤝 Collaborative data:', data);
            if (data.username && !lbUsername) lbUsername = data.username;
            if (data.success && data.playlists && data.playlists.length > 0) {
                listenbrainzPlaylistsCache['collaborative'] = data.playlists;
                tabs[2].hasData = true;
                console.log(`✅ Found ${data.playlists.length} collaborative playlists`);
            }
        }

        // Build tabs HTML
        const tabsContainer = document.getElementById('listenbrainz-tabs');
        console.log('🔧 Building tabs. Available tabs:', tabs.filter(t => t.hasData).map(t => t.label));

        let tabsHtml = '<div class="decade-tabs-inner">'; // Reuse decade tabs styling

        tabs.forEach(tab => {
            if (tab.hasData) {
                const isActive = tab.id === activeListenBrainzTab;
                tabsHtml += `
                    <button class="decade-tab${isActive ? ' active' : ''}"
                            onclick="switchListenBrainzTab('${tab.id}')"
                            data-tab="${tab.id}">
                        ${tab.label}
                    </button>
                `;
            }
        });
        tabsHtml += '</div>';

        if (tabs.every(t => !t.hasData)) {
            console.log('⚠️ No tabs have data');
            tabsContainer.innerHTML = `
                <div class="lb-empty-state">
                    <div class="lb-empty-icon">&#129504;</div>
                    <h3>Connect ListenBrainz</h3>
                    <p>Link your ListenBrainz account to see personalized playlists, recommendations, and collaborative playlists.</p>
                    <button class="action-button primary lb-connect-btn" onclick="openPersonalSettings()">
                        Connect ListenBrainz
                    </button>
                    <p class="lb-empty-help">Get your token from <a href="https://listenbrainz.org/profile/" target="_blank">listenbrainz.org/profile</a></p>
                </div>`;
            return;
        }

        tabsContainer.innerHTML = tabsHtml;

        // Update section subtitle with username
        const lbSubtitle = document.getElementById('listenbrainz-section-subtitle');
        if (lbSubtitle) {
            lbSubtitle.textContent = lbUsername ? `Playlists for ${lbUsername}` : 'Playlists from ListenBrainz';
        }

        // Load first available tab
        const firstTab = tabs.find(t => t.hasData);
        if (firstTab) {
            console.log(`🎯 Loading first tab: ${firstTab.label} (${firstTab.id})`);
            activeListenBrainzTab = firstTab.id;
            loadListenBrainzTabContent(firstTab.id);
        } else {
            console.log('❌ No first tab found');
        }

    } catch (error) {
        console.error('Error initializing ListenBrainz tabs:', error);
        const tabsContainer = document.getElementById('listenbrainz-tabs');
        if (tabsContainer) {
            tabsContainer.innerHTML = '<div class="discover-empty"><p>Failed to load playlists</p></div>';
        }
    }
}

function switchListenBrainzTab(tabId) {
    // Update active tab
    activeListenBrainzTab = tabId;

    // Update tab buttons
    const tabs = document.querySelectorAll('#listenbrainz-tabs .decade-tab');
    tabs.forEach(tab => {
        if (tab.dataset.tab === tabId) {
            tab.classList.add('active');
        } else {
            tab.classList.remove('active');
        }
    });

    // Load content
    loadListenBrainzTabContent(tabId);
}

function groupListenBrainzPlaylists(playlists) {
    const groups = {};
    const groupOrder = [];

    playlists.forEach(playlist => {
        const playlistData = playlist.playlist || playlist;
        const title = (playlistData.title || '').toLowerCase();

        let groupName;
        if (title.includes('weekly jams')) {
            groupName = 'Weekly Jams';
        } else if (title.includes('weekly exploration')) {
            groupName = 'Weekly Exploration';
        } else if (title.includes('top discoveries')) {
            groupName = 'Top Discoveries';
        } else if (title.includes('top missed recordings')) {
            groupName = 'Top Missed Recordings';
        } else if (title.includes('daily jams')) {
            groupName = 'Daily Jams';
        } else {
            groupName = 'Other';
        }

        if (!groups[groupName]) {
            groups[groupName] = [];
            groupOrder.push(groupName);
        }
        groups[groupName].push(playlist);
    });

    // Move "Other" to the end if it exists
    const otherIdx = groupOrder.indexOf('Other');
    if (otherIdx !== -1 && otherIdx !== groupOrder.length - 1) {
        groupOrder.splice(otherIdx, 1);
        groupOrder.push('Other');
    }

    return { groups, groupOrder };
}

function buildListenBrainzPlaylistsHtml(playlists, tabId) {
    let html = '';
    playlists.forEach((playlist, index) => {
        const playlistData = playlist.playlist || playlist;
        const identifier = playlistData.identifier?.split('/').pop() || '';
        console.log(`📋 Playlist ${index}:`, {
            title: playlistData.title,
            fullIdentifier: playlistData.identifier,
            extractedIdentifier: identifier
        });
        const title = playlistData.title || 'Untitled Playlist';
        const creator = playlistData.creator || 'ListenBrainz';

        let trackCount = 50;
        if (playlistData.annotation?.track_count && playlistData.annotation.track_count > 0) {
            trackCount = playlistData.annotation.track_count;
        } else if (playlistData.track && Array.isArray(playlistData.track) && playlistData.track.length > 0) {
            trackCount = playlistData.track.length;
        }

        const playlistId = `discover-lb-playlist-${identifier}`;  // Use consistent MBID-based ID
        const virtualPlaylistId = `discover_lb_${tabId}_${identifier}`;

        html += `
            <div class="discover-section-subsection">
                <div class="discover-section-header">
                    <div>
                        <h3 class="discover-section-subtitle-large">${title}</h3>
                        <p class="discover-section-meta" id="${playlistId}-meta">by ${creator} • Loading tracks...</p>
                    </div>
                    <div class="discover-section-actions">
                        <button class="action-button secondary"
                                onclick="openDownloadModalForListenBrainzPlaylist('${identifier}', '${escapeForInlineJs(title)}')"
                                title="Download missing tracks">
                            <span class="button-icon">↓</span>
                            <span class="button-text">Download</span>
                        </button>
                        <span class="wing-it-wrap">
                        <button class="action-button wing-it-btn-sm"
                                onclick="_toggleWingItDropdownLB(this, '${identifier}', '${escapeForInlineJs(title)}')"
                                title="Download or sync using raw track names — no metadata discovery">
                            <span class="button-icon">⚡</span>
                            <span class="button-text">Wing It</span>
                        </button>
                        </span>
                        <button class="action-button primary"
                                id="${playlistId}-sync-btn"
                                onclick="startListenBrainzPlaylistSync('${identifier}')"
                                title="Sync to media server"
                                style="display: none;">
                            <span class="button-icon">⟳</span>
                            <span class="button-text">Sync</span>
                        </button>
                    </div>
                </div>
                <!-- Sync Status Display -->
                <div class="discover-sync-status" id="${playlistId}-sync-status" style="display: none;">
                    <div class="sync-status-content">
                        <div class="sync-status-label">
                            <span class="sync-icon">⟳</span>
                            <span>Syncing to media server...</span>
                        </div>
                        <div class="sync-status-stats">
                            <span class="sync-stat">♪ <span id="${playlistId}-sync-total">0</span></span>
                            <span class="sync-separator">/</span>
                            <span class="sync-stat">✓ <span id="${playlistId}-sync-matched">0</span></span>
                            <span class="sync-separator">/</span>
                            <span class="sync-stat">✗ <span id="${playlistId}-sync-failed">0</span></span>
                            <span class="sync-stat">(<span id="${playlistId}-sync-percentage">0</span>%)</span>
                        </div>
                    </div>
                </div>
                <div class="discover-playlist-container compact" id="${playlistId}-playlist">
                    <div class="discover-loading"><div class="loading-spinner"></div><p>Loading tracks...</p></div>
                </div>
            </div>
        `;
    });
    return html;
}

function loadTracksForPlaylists(playlists) {
    playlists.forEach((playlist) => {
        const playlistData = playlist.playlist || playlist;
        const identifier = playlistData.identifier?.split('/').pop() || '';
        const playlistId = `discover-lb-playlist-${identifier}`;
        loadListenBrainzPlaylistTracks(identifier, playlistId);
    });
}

function switchListenBrainzSubTab(groupId) {
    activeListenBrainzSubTab = groupId;

    // Update sub-tab buttons
    const subTabs = document.querySelectorAll('#lb-subtabs-bar .lb-subtab');
    subTabs.forEach(tab => {
        if (tab.dataset.group === groupId) {
            tab.classList.add('active');
        } else {
            tab.classList.remove('active');
        }
    });

    // Show/hide sub-tab content panels
    const panels = document.querySelectorAll('.lb-subtab-panel');
    panels.forEach(panel => {
        if (panel.dataset.group === groupId) {
            panel.style.display = 'block';
            // Load tracks for playlists in this panel if not already loaded
            const unloaded = panel.querySelectorAll('.discover-loading');
            if (unloaded.length > 0) {
                const groupPlaylists = panel._playlists;
                if (groupPlaylists) {
                    loadTracksForPlaylists(groupPlaylists);
                }
            }
        } else {
            panel.style.display = 'none';
        }
    });
}

async function loadListenBrainzTabContent(tabId) {
    const container = document.getElementById('listenbrainz-tab-content');
    if (!container) return;

    const playlists = listenbrainzPlaylistsCache[tabId] || [];
    if (playlists.length === 0) {
        container.innerHTML = '<div class="discover-empty"><p>No playlists in this category</p></div>';
        return;
    }

    // For recommendations tab with multiple playlists, group into sub-tabs
    if (tabId === 'recommendations' && playlists.length > 1) {
        const { groups, groupOrder } = groupListenBrainzPlaylists(playlists);

        // If only one group, no need for sub-tabs
        if (groupOrder.length <= 1) {
            const html = buildListenBrainzPlaylistsHtml(playlists, tabId);
            container.innerHTML = html;
            loadTracksForPlaylists(playlists);
            return;
        }

        // Build sub-tabs bar
        const firstGroup = activeListenBrainzSubTab && groupOrder.includes(activeListenBrainzSubTab)
            ? activeListenBrainzSubTab
            : groupOrder[0];
        activeListenBrainzSubTab = firstGroup;

        let subTabsHtml = '<div class="decade-tabs-inner" id="lb-subtabs-bar" style="margin-bottom: 16px;">';
        groupOrder.forEach(groupName => {
            const isActive = groupName === firstGroup;
            const count = groups[groupName].length;
            subTabsHtml += `
                <button class="decade-tab lb-subtab${isActive ? ' active' : ''}"
                        onclick="switchListenBrainzSubTab('${groupName}')"
                        data-group="${groupName}"
                        style="font-size: 13px; padding: 8px 16px;">
                    ${groupName} (${count})
                </button>
            `;
        });
        subTabsHtml += '</div>';

        // Build content panels for each group
        let panelsHtml = '';
        groupOrder.forEach(groupName => {
            const isActive = groupName === firstGroup;
            panelsHtml += `<div class="lb-subtab-panel" data-group="${groupName}" style="display: ${isActive ? 'block' : 'none'};">`;
            panelsHtml += buildListenBrainzPlaylistsHtml(groups[groupName], tabId);
            panelsHtml += '</div>';
        });

        container.innerHTML = subTabsHtml + panelsHtml;

        // Store playlist references on panels for lazy loading
        groupOrder.forEach(groupName => {
            const panel = container.querySelector(`.lb-subtab-panel[data-group="${groupName}"]`);
            if (panel) {
                panel._playlists = groups[groupName];
            }
        });

        // Load tracks only for the active sub-tab
        loadTracksForPlaylists(groups[firstGroup]);
        return;
    }

    // Default: flat list for user/collaborative tabs (or single-group recommendations)
    const html = buildListenBrainzPlaylistsHtml(playlists, tabId);
    container.innerHTML = html;
    loadTracksForPlaylists(playlists);
}

async function loadListenBrainzPlaylistTracks(identifier, playlistId) {
    try {
        const playlistContainer = document.getElementById(`${playlistId}-playlist`);
        if (!playlistContainer) return;

        // Check cache first
        if (listenbrainzTracksCache[identifier]) {
            displayListenBrainzTracks(listenbrainzTracksCache[identifier], playlistId);
            return;
        }

        console.log(`🔄 Fetching tracks for playlist: ${identifier}`);
        const response = await fetch(`/api/discover/listenbrainz/playlist/${identifier}`);
        console.log(`📡 Response status: ${response.status}`);

        if (!response.ok) {
            const errorText = await response.text();
            console.error(`❌ Failed to fetch playlist: ${response.status} - ${errorText}`);
            throw new Error('Failed to fetch playlist tracks');
        }

        const data = await response.json();
        console.log(`📋 Received data:`, data);
        console.log(`📊 Tracks count: ${data.tracks?.length || 0}`);

        if (!data.success || !data.tracks || data.tracks.length === 0) {
            playlistContainer.innerHTML = '<div class="discover-empty"><p>No tracks available</p></div>';
            return;
        }

        // Cache the tracks
        listenbrainzTracksCache[identifier] = data.tracks;

        // Display tracks
        displayListenBrainzTracks(data.tracks, playlistId);

    } catch (error) {
        console.error('Error loading ListenBrainz playlist tracks:', error);
        const playlistContainer = document.getElementById(`${playlistId}-playlist`);
        if (playlistContainer) {
            playlistContainer.innerHTML = '<div class="discover-empty"><p>Failed to load tracks</p></div>';
        }
    }
}

/**
 * Clean artist name by removing featured artists
 * e.g., "Blackstreet feat. Dr. Dre & Queen Pen" -> "Blackstreet"
 */
function cleanArtistName(artistName) {
    if (!artistName) return artistName;

    // Remove everything after common featuring patterns (case insensitive)
    const patterns = [
        /\s+feat\.?\s+.*/i,      // "feat." or "feat"
        /\s+featuring\s+.*/i,    // "featuring"
        /\s+ft\.?\s+.*/i,        // "ft." or "ft"
        /\s+with\s+.*/i,         // "with"
        /\s+x\s+.*/i             // " x " (common in collaborations)
    ];

    let cleaned = artistName;
    for (const pattern of patterns) {
        cleaned = cleaned.replace(pattern, '');
    }

    return cleaned.trim();
}

function displayListenBrainzTracks(tracks, playlistId) {
    const playlistContainer = document.getElementById(`${playlistId}-playlist`);
    if (!playlistContainer) return;

    console.log(`🎨 Displaying ${tracks.length} tracks for ${playlistId}`);
    if (tracks.length > 0) {
        console.log('Sample track data:', tracks[0]);
    }

    // Update track count in the metadata section
    const metaElement = document.getElementById(`${playlistId}-meta`);
    if (metaElement) {
        // Extract creator from existing text (before the bullet)
        const currentText = metaElement.textContent;
        const creatorMatch = currentText.match(/by (.+?) •/);
        const creator = creatorMatch ? creatorMatch[1] : 'ListenBrainz';
        metaElement.textContent = `by ${creator} • ${tracks.length} track${tracks.length !== 1 ? 's' : ''}`;
    }

    // Simple SVG placeholder for missing album art (music note icon)
    const placeholderImage = 'data:image/svg+xml;base64,PHN2ZyB3aWR0aD0iNDAiIGhlaWdodD0iNDAiIHZpZXdCb3g9IjAgMCA0MCA0MCIgZmlsbD0ibm9uZSIgeG1sbnM9Imh0dHA6Ly93d3cudzMub3JnLzIwMDAvc3ZnIj48cmVjdCB3aWR0aD0iNDAiIGhlaWdodD0iNDAiIGZpbGw9IiMyYTJhMmEiLz48cGF0aCBkPSJNMjQgMTJ2MTIuNUEzLjUgMy41IDAgMSAxIDIwLjUgMjFWMTZsLTUgMXY5YTMuNSAzLjUgMCAxIDEtMy41LTMuNVYxM2wxMi0zeiIgZmlsbD0iIzU1NSIvPjwvc3ZnPg==';

    let html = '<div class="discover-playlist-tracks-compact">';
    tracks.forEach((track, index) => {
        const coverUrl = track.album_cover_url || placeholderImage;
        const durationMin = Math.floor(track.duration_ms / 60000);
        const durationSec = Math.floor((track.duration_ms % 60000) / 1000);
        const duration = track.duration_ms > 0 ? `${durationMin}:${durationSec.toString().padStart(2, '0')}` : '';

        const albumName = track.album_name ? escapeHtml(track.album_name) : '';

        html += `
            <div class="discover-playlist-track-compact" data-track-index="${index}">
                <div class="track-compact-number">${index + 1}</div>
                <div class="track-compact-image">
                    <img src="${coverUrl}" alt="${albumName}" loading="lazy" onerror="this.src='${placeholderImage}'">
                </div>
                <div class="track-compact-info">
                    <div class="track-compact-name">${escapeHtml(track.track_name || 'Unknown Track')}</div>
                    <div class="track-compact-artist">${escapeHtml(cleanArtistName(track.artist_name) || 'Unknown Artist')}</div>
                </div>
                <div class="track-compact-album">${albumName}</div>
                <div class="track-compact-duration">${duration}</div>
            </div>
        `;
    });
    html += '</div>';

    playlistContainer.innerHTML = html;
}

function _toggleWingItDropdownLB(btn, identifier, title) {
    const existing = document.querySelector('.wing-it-dropdown.visible');
    if (existing) { existing.classList.remove('visible'); setTimeout(() => existing.remove(), 150); return; }

    const wrap = btn.closest('.wing-it-wrap');
    if (!wrap) return;

    const dropdown = document.createElement('div');
    dropdown.className = 'wing-it-dropdown';
    dropdown.innerHTML = `
        <button class="wing-it-dropdown-item" data-action="download">
            <span class="wing-it-dropdown-icon">⬇️</span>
            <span class="wing-it-dropdown-label">Download</span>
            <span class="wing-it-dropdown-hint">Raw names</span>
        </button>
        <button class="wing-it-dropdown-item" data-action="sync">
            <span class="wing-it-dropdown-icon">🔄</span>
            <span class="wing-it-dropdown-label">Sync to Server</span>
            <span class="wing-it-dropdown-hint">Best-effort</span>
        </button>
    `;

    dropdown.querySelectorAll('.wing-it-dropdown-item').forEach(item => {
        item.addEventListener('click', () => {
            dropdown.classList.remove('visible');
            setTimeout(() => dropdown.remove(), 150);
            const tracks = listenbrainzTracksCache[identifier];
            if (!tracks || tracks.length === 0) {
                showToast('No tracks cached. Try opening the playlist first.', 'error');
                return;
            }
            if (item.dataset.action === 'download') {
                wingItDownload(tracks, title, 'ListenBrainz', identifier, true);
            } else {
                _wingItSync(tracks, title, 'ListenBrainz', identifier);
            }
        });
    });

    const btnRect2 = btn.getBoundingClientRect();
    if (btnRect2.top < 200) dropdown.classList.add('flip-down');

    wrap.appendChild(dropdown);
    requestAnimationFrame(() => dropdown.classList.add('visible'));

    setTimeout(() => {
        const closeHandler = e => {
            if (!dropdown.contains(e.target) && e.target !== btn) {
                dropdown.classList.remove('visible');
                setTimeout(() => dropdown.remove(), 150);
                document.removeEventListener('click', closeHandler);
            }
        };
        document.addEventListener('click', closeHandler);
    }, 50);
}

async function _wingItFromLBCard(identifier, title) {
    // Legacy — kept for backward compat
    const tracks = listenbrainzTracksCache[identifier];
    if (!tracks || tracks.length === 0) {
        showToast('No tracks cached for this playlist. Try opening the discovery modal first.', 'error');
        return;
    }
    wingItDownload(tracks, title, 'ListenBrainz', identifier);
}

async function openDownloadModalForListenBrainzPlaylist(identifier, title) {
    try {
        const tracks = listenbrainzTracksCache[identifier];
        if (!tracks || tracks.length === 0) {
            showToast('No tracks to download', 'error');
            return;
        }

        console.log(`🎵 Opening ListenBrainz discovery modal: ${title}`);
        console.log(`🔍 Looking for existing state with identifier: ${identifier}`);
        console.log(`📋 All ListenBrainz states:`, Object.keys(listenbrainzPlaylistStates));

        // Check if state already exists from backend hydration (like Beatport does)
        const existingState = listenbrainzPlaylistStates[identifier];
        console.log(`🔍 Existing state found:`, existingState ? `Phase: ${existingState.phase}` : 'None');

        if (existingState && existingState.phase !== 'fresh') {
            // State exists - rehydrate the modal with existing data
            console.log(`🔄 Rehydrating existing ListenBrainz state (Phase: ${existingState.phase})`);

            // If downloading/download_complete, rehydrate download modal instead
            if ((existingState.phase === 'downloading' || existingState.phase === 'download_complete') &&
                existingState.convertedSpotifyPlaylistId && existingState.download_process_id) {

                console.log(`📥 Rehydrating download modal for ListenBrainz playlist: ${title}`);

                // Implement download modal rehydration (like Beatport does)
                const convertedPlaylistId = existingState.convertedSpotifyPlaylistId;

                try {
                    // Check if modal already exists (user just closed it)
                    if (activeDownloadProcesses[convertedPlaylistId]) {
                        console.log(`✅ Download modal already exists, just showing it`);
                        const process = activeDownloadProcesses[convertedPlaylistId];
                        if (process.modalElement) {
                            process.modalElement.style.display = 'flex';
                        }
                        return;
                    }

                    // Create the download modal using the ListenBrainz state
                    console.log(`🆕 Creating new download modal for rehydration`);
                    // Get tracks from the existing state
                    let spotifyTracks = [];

                    if (existingState && existingState.discovery_results) {
                        spotifyTracks = existingState.discovery_results
                            .filter(result => result.spotify_data)
                            .map(result => {
                                const track = result.spotify_data;
                                // Ensure artists is an array of strings
                                if (track.artists && Array.isArray(track.artists)) {
                                    track.artists = track.artists.map(artist =>
                                        typeof artist === 'string' ? artist : (artist.name || artist)
                                    );
                                } else if (track.artists && typeof track.artists === 'string') {
                                    track.artists = [track.artists];
                                } else {
                                    track.artists = ['Unknown Artist'];
                                }
                                return {
                                    id: track.id,
                                    name: track.name,
                                    artists: track.artists,
                                    album: track.album || 'Unknown Album',
                                    duration_ms: track.duration_ms || 0,
                                    external_urls: track.external_urls || {}
                                };
                            });
                    }

                    if (spotifyTracks.length > 0) {
                        await openDownloadMissingModalForYouTube(
                            convertedPlaylistId,
                            title,
                            spotifyTracks
                        );

                        // Set the modal to running state with the correct batch ID
                        const process = activeDownloadProcesses[convertedPlaylistId];
                        if (process) {
                            process.status = existingState.phase === 'download_complete' ? 'complete' : 'running';
                            process.batchId = existingState.download_process_id;

                            // Update UI to running state
                            const beginBtn = document.getElementById(`begin-analysis-btn-${convertedPlaylistId}`);
                            const cancelBtn = document.getElementById(`cancel-all-btn-${convertedPlaylistId}`);
                            if (beginBtn) beginBtn.style.display = 'none';
                            if (cancelBtn) cancelBtn.style.display = 'inline-block';

                            // Start polling for this process
                            startModalDownloadPolling(convertedPlaylistId);

                            // Add to discover download sidebar if this has discoverMetadata
                            if (process.discoverMetadata) {
                                const playlistName = title;
                                const imageUrl = process.discoverMetadata.imageUrl;
                                const type = process.discoverMetadata.type || 'album';
                                addDiscoverDownload(convertedPlaylistId, playlistName, type, imageUrl);
                                console.log(`📥 [REHYDRATION] Added ListenBrainz download to sidebar: ${playlistName}`);
                            }

                            // Show modal since user clicked the download button (different from background rehydration)
                            if (process.modalElement) {
                                process.modalElement.style.display = 'flex';
                            }
                            console.log(`✅ Rehydrated download modal for ListenBrainz playlist: ${title}`);
                        }
                    } else {
                        console.warn(`⚠️ No Spotify tracks found for ListenBrainz download modal: ${title}`);
                    }
                } catch (error) {
                    console.warn(`⚠️ Error setting up download process for ListenBrainz playlist "${title}":`, error.message);
                }

                return;
            }

            // Open discovery modal with existing state
            openYouTubeDiscoveryModal(identifier);

            // If still discovering, resume polling
            if (existingState.phase === 'discovering') {
                console.log(`🔄 Resuming discovery polling for: ${title}`);
                startListenBrainzDiscoveryPolling(identifier);
            }

            return;
        }

        // No existing state - create fresh state and start discovery
        console.log(`🆕 Creating fresh ListenBrainz state for: ${title}`);

        // Create YouTube-style state entry for this ListenBrainz playlist (like Beatport does)
        const listenbrainzState = {
            phase: 'fresh',
            playlist: {
                name: title,
                tracks: tracks.map(track => ({
                    track_name: track.track_name,
                    artist_name: track.artist_name,
                    album_name: track.album_name,
                    duration_ms: track.duration_ms || 0,
                    mbid: track.mbid,
                    release_mbid: track.release_mbid,
                    album_cover_url: track.album_cover_url
                })),
                description: `${tracks.length} tracks from ${title}`,
                source: 'listenbrainz'
            },
            is_listenbrainz_playlist: true,
            playlist_mbid: identifier,  // Link to ListenBrainz playlist
            // Initialize discovery state properties (both naming conventions for modal compatibility)
            discovery_results: [],
            discoveryResults: [],
            discovery_progress: 0,
            discoveryProgress: 0,
            spotify_matches: 0,
            spotifyMatches: 0,
            spotify_total: tracks.length,
            spotifyTotal: tracks.length
        };

        // Store in ListenBrainz playlist states
        listenbrainzPlaylistStates[identifier] = listenbrainzState;

        // Start discovery automatically (like Beatport and Tidal do)
        try {
            console.log(`🔍 Starting ListenBrainz discovery for: ${title}`);

            // Call the discovery start endpoint with playlist data
            const response = await fetch(`/api/listenbrainz/discovery/start/${identifier}`, {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json'
                },
                body: JSON.stringify({
                    playlist: listenbrainzState.playlist
                })
            });

            const result = await response.json();
            if (result.success) {
                // Update state to discovering
                listenbrainzPlaylistStates[identifier].phase = 'discovering';

                // Start polling for progress
                startListenBrainzDiscoveryPolling(identifier);

                console.log(`✅ Started ListenBrainz discovery for: ${title}`);
            } else {
                console.error('❌ Error starting ListenBrainz discovery:', result.error);
                showToast(`Error starting discovery: ${result.error}`, 'error');
            }
        } catch (error) {
            console.error('❌ Error starting ListenBrainz discovery:', error);
            showToast(`Error starting discovery: ${error.message}`, 'error');
        }

        // Open the existing YouTube discovery modal infrastructure
        openYouTubeDiscoveryModal(identifier);

        console.log(`✅ ListenBrainz discovery modal opened for ${title} with ${tracks.length} tracks`);

    } catch (error) {
        console.error('Error opening discovery modal for ListenBrainz playlist:', error);
        showToast('Failed to open discovery modal', 'error');
    }
}

async function openListenBrainzPlaylist(playlistMbid, playlistName) {
    try {
        showLoadingOverlay(`Loading ${playlistName}...`);

        const response = await fetch(`/api/discover/listenbrainz/playlist/${playlistMbid}`);
        if (!response.ok) {
            throw new Error('Failed to fetch playlist');
        }

        const data = await response.json();
        if (!data.success || !data.playlist) {
            showToast('Failed to load playlist', 'error');
            hideLoadingOverlay();
            return;
        }

        const playlist = data.playlist;
        const tracks = playlist.tracks || [];

        if (tracks.length === 0) {
            showToast('This playlist is empty', 'info');
            hideLoadingOverlay();
            return;
        }

        // Convert to Spotify-like format for compatibility with download modal
        const spotifyTracks = tracks.map(track => ({
            id: track.recording_mbid || `listenbrainz_${track.title}_${track.creator}`.replace(/[^a-z0-9]/gi, '_'),  // Generate ID if missing
            name: track.title || 'Unknown',
            artists: [{ name: cleanArtistName(track.creator || 'Unknown') }], // Proper Spotify format
            album: {
                name: track.album || 'Unknown Album',
                images: track.album_cover_url ? [{ url: track.album_cover_url }] : []
            },
            duration_ms: track.duration_ms || 0,
            listenbrainz_metadata: track.additional_metadata
        }));

        const virtualPlaylistId = `listenbrainz_${playlistMbid}`;
        await openDownloadMissingModalForYouTube(virtualPlaylistId, playlistName, spotifyTracks);
        hideLoadingOverlay();

    } catch (error) {
        console.error(`Error opening ListenBrainz playlist:`, error);
        showToast(`Failed to load playlist`, 'error');
        hideLoadingOverlay();
    }
}

async function refreshListenBrainzPlaylists() {
    const button = document.getElementById('listenbrainz-refresh-btn');
    if (!button) return;

    try {
        // Show loading state on button
        const originalContent = button.innerHTML;
        button.disabled = true;
        button.innerHTML = '<span class="button-icon">⏳</span><span class="button-text">Refreshing...</span>';

        console.log('🔄 Refreshing ListenBrainz playlists...');
        showToast('Refreshing ListenBrainz playlists...', 'info');

        const response = await fetch('/api/discover/listenbrainz/refresh', {
            method: 'POST'
        });

        if (!response.ok) {
            throw new Error(`Failed to refresh: ${response.statusText}`);
        }

        const data = await response.json();

        if (data.success) {
            const summary = data.summary || {};
            let message = 'ListenBrainz playlists refreshed!';

            // Build summary message
            const updates = [];
            for (const [type, stats] of Object.entries(summary)) {
                const total = (stats.new || 0) + (stats.updated || 0);
                if (total > 0) {
                    updates.push(`${total} ${type}`);
                }
            }

            if (updates.length > 0) {
                message += ` Updated: ${updates.join(', ')}`;
            } else {
                message = 'All playlists are up to date';
            }

            console.log('✅ Refresh complete:', data.summary);
            showToast(message, 'success');

            // Reload the tabs to show updated data
            await initializeListenBrainzTabs();

        } else {
            throw new Error(data.error || 'Unknown error');
        }

        // Restore button
        button.disabled = false;
        button.innerHTML = originalContent;

    } catch (error) {
        console.error('Error refreshing ListenBrainz playlists:', error);
        showToast(`Failed to refresh: ${error.message}`, 'error');

        // Restore button
        button.disabled = false;
        button.innerHTML = '<span class="button-icon">🔄</span><span class="button-text">Refresh</span>';
    }
}

// ===============================
// SEASONAL DISCOVERY
// ===============================

async function loadSeasonalContent() {
    try {
        const response = await fetch('/api/discover/seasonal/current');
        if (!response.ok) {
            console.error('Failed to fetch seasonal content');
            return;
        }

        const data = await response.json();

        // If no active season, hide seasonal sections
        if (!data.success || !data.season) {
            hideSeasonalSections();
            return;
        }

        currentSeasonKey = data.season;

        // Load seasonal albums
        await loadSeasonalAlbums(data);

        // Load seasonal playlist if available
        if (data.playlist_available) {
            await loadSeasonalPlaylist(data);
        }

    } catch (error) {
        console.error('Error loading seasonal content:', error);
        hideSeasonalSections();
    }
}

async function loadSeasonalAlbums(seasonData) {
    try {
        const carousel = document.getElementById('seasonal-albums-carousel');
        if (!carousel) return;

        // Show seasonal section
        const seasonalSection = document.getElementById('seasonal-albums-section');
        if (seasonalSection) {
            seasonalSection.style.display = 'block';
        }

        // Update header
        const seasonalTitle = document.getElementById('seasonal-albums-title');
        const seasonalSubtitle = document.getElementById('seasonal-albums-subtitle');

        if (seasonalTitle) {
            seasonalTitle.textContent = `${seasonData.icon} ${seasonData.name}`;
        }
        if (seasonalSubtitle) {
            seasonalSubtitle.textContent = seasonData.description;
        }

        // Store albums for download functionality
        discoverSeasonalAlbums = seasonData.albums || [];

        if (discoverSeasonalAlbums.length === 0) {
            carousel.innerHTML = '<div class="discover-empty"><p>No seasonal albums found</p></div>';
            return;
        }

        // Build carousel HTML
        let html = '';
        discoverSeasonalAlbums.forEach((album, index) => {
            const coverUrl = album.album_cover_url || '/static/placeholder-album.png';
            html += `
                <div class="discover-card" onclick="openDownloadModalForSeasonalAlbum(${index})" style="cursor: pointer;">
                    <div class="discover-card-image">
                        <img src="${coverUrl}" alt="${album.album_name}" loading="lazy">
                    </div>
                    <div class="discover-card-info">
                        <h4 class="discover-card-title">${album.album_name}</h4>
                        <p class="discover-card-subtitle">${album.artist_name}</p>
                        ${album.release_date ? `<p class="discover-card-meta">${album.release_date}</p>` : ''}
                    </div>
                </div>
            `;
        });

        carousel.innerHTML = html;

    } catch (error) {
        console.error('Error loading seasonal albums:', error);
    }
}

async function loadSeasonalPlaylist(seasonData) {
    try {
        const playlistContainer = document.getElementById('seasonal-playlist');
        if (!playlistContainer) return;

        // Show seasonal playlist section
        const seasonalPlaylistSection = document.getElementById('seasonal-playlist-section');
        if (seasonalPlaylistSection) {
            seasonalPlaylistSection.style.display = 'block';
        }

        // Update header
        const playlistTitle = document.getElementById('seasonal-playlist-title');
        const playlistSubtitle = document.getElementById('seasonal-playlist-subtitle');

        if (playlistTitle) {
            playlistTitle.textContent = `${seasonData.icon} ${seasonData.name} Mix`;
        }
        if (playlistSubtitle) {
            playlistSubtitle.textContent = `Curated playlist for ${seasonData.name.toLowerCase()}`;
        }

        playlistContainer.innerHTML = '<div class="discover-loading"><div class="loading-spinner"></div><p>Loading playlist...</p></div>';

        // Fetch playlist tracks
        const response = await fetch(`/api/discover/seasonal/${currentSeasonKey}/playlist`);
        if (!response.ok) {
            throw new Error('Failed to fetch seasonal playlist');
        }

        const data = await response.json();

        if (!data.success || !data.tracks || data.tracks.length === 0) {
            playlistContainer.innerHTML = '<div class="discover-empty"><p>No tracks available yet</p></div>';
            return;
        }

        // Store tracks for download/sync functionality
        discoverSeasonalTracks = data.tracks;

        // Build compact playlist HTML
        let html = '<div class="discover-playlist-tracks-compact">';
        data.tracks.forEach((track, index) => {
            const coverUrl = track.album_cover_url || '/static/placeholder-album.png';
            const durationMin = Math.floor(track.duration_ms / 60000);
            const durationSec = Math.floor((track.duration_ms % 60000) / 1000);
            const duration = `${durationMin}:${durationSec.toString().padStart(2, '0')}`;

            html += `
                <div class="discover-playlist-track-compact" data-track-index="${index}">
                    <div class="track-compact-number">${index + 1}</div>
                    <div class="track-compact-image">
                        <img src="${coverUrl}" alt="${track.album_name}" loading="lazy">
                    </div>
                    <div class="track-compact-info">
                        <div class="track-compact-name">${track.track_name}</div>
                        <div class="track-compact-artist">${track.artist_name}</div>
                    </div>
                    <div class="track-compact-album">${track.album_name}</div>
                    <div class="track-compact-duration">${duration}</div>
                </div>
            `;
        });
        html += '</div>';

        playlistContainer.innerHTML = html;

    } catch (error) {
        console.error('Error loading seasonal playlist:', error);
        const playlistContainer = document.getElementById('seasonal-playlist');
        if (playlistContainer) {
            playlistContainer.innerHTML = '<div class="discover-empty"><p>Failed to load playlist</p></div>';
        }
    }
}

function hideSeasonalSections() {
    const seasonalAlbumsSection = document.getElementById('seasonal-albums-section');
    const seasonalPlaylistSection = document.getElementById('seasonal-playlist-section');

    if (seasonalAlbumsSection) {
        seasonalAlbumsSection.style.display = 'none';
    }
    if (seasonalPlaylistSection) {
        seasonalPlaylistSection.style.display = 'none';
    }
}

async function openDownloadModalForSeasonalAlbum(albumIndex) {
    const album = discoverSeasonalAlbums[albumIndex];
    if (!album) {
        showToast('Album data not found', 'error');
        return;
    }

    console.log(`📥 Opening Download Missing Tracks modal for seasonal album: ${album.album_name}`);
    showLoadingOverlay(`Loading tracks for ${album.album_name}...`);

    try {
        // Determine source and album ID - use source-agnostic endpoint (matches Recent Releases)
        const source = album.source || (album.spotify_album_id && !album.spotify_album_id.match(/^\d+$/) ? 'spotify' : 'itunes');
        const albumId = album.spotify_album_id;

        if (!albumId) {
            throw new Error('No album ID available');
        }

        // Fetch album tracks from appropriate source (pass name/artist for Hydrabase support)
        const _dap1 = new URLSearchParams({ name: album.album_name || '', artist: album.artist_name || '' });
        const response = await fetch(`/api/discover/album/${source}/${albumId}?${_dap1}`);
        if (!response.ok) {
            throw new Error('Failed to fetch album tracks');
        }

        const albumData = await response.json();
        if (!albumData.tracks || albumData.tracks.length === 0) {
            throw new Error('No tracks found in album');
        }

        // Convert to expected format with full album context (matches Recent Releases)
        const spotifyTracks = albumData.tracks.map(track => {
            let artists = track.artists || albumData.artists || [{ name: album.artist_name }];
            if (Array.isArray(artists)) {
                artists = artists.map(a => a.name || a);
            }

            return {
                id: track.id,
                name: track.name,
                artists: artists,
                album: {
                    id: albumData.id,
                    name: albumData.name,
                    album_type: albumData.album_type || 'album',
                    total_tracks: albumData.total_tracks || 0,
                    release_date: albumData.release_date || '',
                    images: albumData.images || []
                },
                duration_ms: track.duration_ms || 0,
                track_number: track.track_number || 0
            };
        });

        // Create virtual playlist ID
        const virtualPlaylistId = `seasonal_album_${albumId}`;

        // Pass proper artist/album context for album download (1 worker + source reuse)
        const artistContext = {
            name: album.artist_name,
            source: source
        };

        const albumContext = {
            id: albumData.id,
            name: albumData.name,
            album_type: albumData.album_type || 'album',
            total_tracks: albumData.total_tracks || 0,
            release_date: albumData.release_date || '',
            images: albumData.images || []
        };

        // Open download modal with album context (same as Recent Releases)
        await openDownloadMissingModalForYouTube(virtualPlaylistId, albumData.name, spotifyTracks, artistContext, albumContext);

        hideLoadingOverlay();

    } catch (error) {
        console.error(`Error loading seasonal album: ${error.message}`);
        hideLoadingOverlay();
        showToast(`Failed to load album tracks: ${error.message}`, 'error');
    }
}

async function openDownloadModalForSeasonalPlaylist() {
    if (!discoverSeasonalTracks || discoverSeasonalTracks.length === 0) {
        alert('No seasonal tracks available');
        return;
    }

    // Convert to track format expected by modal
    const tracks = discoverSeasonalTracks.map(track => ({
        id: track.spotify_track_id,
        name: track.track_name,
        artists: [{ name: track.artist_name }],
        album: { name: track.album_name }
    }));

    openDownloadMissingModal(tracks, `${currentSeasonKey} Seasonal Mix`);
}

async function syncSeasonalPlaylist() {
    if (!currentSeasonKey) {
        alert('No active season');
        return;
    }

    // Use the same sync logic as other discover playlists
    // Create a virtual playlist ID for tracking
    const virtualPlaylistId = `discover_seasonal_${currentSeasonKey}`;

    // Build playlist data from seasonal tracks
    const playlistData = {
        id: virtualPlaylistId,
        name: `${currentSeasonKey.charAt(0).toUpperCase() + currentSeasonKey.slice(1)} Mix`,
        tracks: discoverSeasonalTracks.map(track => ({
            id: track.spotify_track_id,
            name: track.track_name,
            artists: [{ name: track.artist_name }],
            album: { name: track.album_name },
            duration_ms: track.duration_ms
        }))
    };

    // Trigger sync (reuse existing sync infrastructure)
    await syncPlaylistToLibrary(playlistData);
}

// ===============================
// PERSONALIZED PLAYLISTS
// ===============================

async function loadPersonalizedRecentlyAdded() {
    try {
        const container = document.getElementById('personalized-recently-added');
        if (!container) return;

        const response = await fetch('/api/discover/personalized/recently-added');
        if (!response.ok) return;

        const data = await response.json();
        if (!data.success || !data.tracks || data.tracks.length === 0) {
            container.closest('.discover-section').style.display = 'none';
            return;
        }

        personalizedRecentlyAdded = data.tracks;
        renderCompactPlaylist(container, data.tracks);
        container.closest('.discover-section').style.display = 'block';

    } catch (error) {
        console.error('Error loading recently added:', error);
    }
}

async function loadPersonalizedTopTracks() {
    try {
        const container = document.getElementById('personalized-top-tracks');
        if (!container) return;

        const response = await fetch('/api/discover/personalized/top-tracks');
        if (!response.ok) return;

        const data = await response.json();
        if (!data.success || !data.tracks || data.tracks.length === 0) {
            container.closest('.discover-section').style.display = 'none';
            return;
        }

        personalizedTopTracks = data.tracks;
        renderCompactPlaylist(container, data.tracks);
        container.closest('.discover-section').style.display = 'block';

    } catch (error) {
        console.error('Error loading top tracks:', error);
    }
}

async function loadPersonalizedForgottenFavorites() {
    try {
        const container = document.getElementById('personalized-forgotten-favorites');
        if (!container) return;

        const response = await fetch('/api/discover/personalized/forgotten-favorites');
        if (!response.ok) return;

        const data = await response.json();
        if (!data.success || !data.tracks || data.tracks.length === 0) {
            container.closest('.discover-section').style.display = 'none';
            return;
        }

        personalizedForgottenFavorites = data.tracks;
        renderCompactPlaylist(container, data.tracks);
        container.closest('.discover-section').style.display = 'block';

    } catch (error) {
        console.error('Error loading forgotten favorites:', error);
    }
}

async function loadPersonalizedPopularPicks() {
    try {
        const container = document.getElementById('personalized-popular-picks');
        if (!container) return;

        const response = await fetch('/api/discover/personalized/popular-picks');
        if (!response.ok) return;

        const data = await response.json();
        if (!data.success || !data.tracks || data.tracks.length === 0) {
            container.closest('.discover-section').style.display = 'none';
            return;
        }

        personalizedPopularPicks = data.tracks;
        renderCompactPlaylist(container, data.tracks);
        container.closest('.discover-section').style.display = 'block';

    } catch (error) {
        console.error('Error loading popular picks:', error);
    }
}

async function loadPersonalizedHiddenGems() {
    try {
        const container = document.getElementById('personalized-hidden-gems');
        if (!container) return;

        const response = await fetch('/api/discover/personalized/hidden-gems');
        if (!response.ok) return;

        const data = await response.json();
        if (!data.success || !data.tracks || data.tracks.length === 0) {
            container.closest('.discover-section').style.display = 'none';
            return;
        }

        personalizedHiddenGems = data.tracks;
        renderCompactPlaylist(container, data.tracks);
        container.closest('.discover-section').style.display = 'block';

    } catch (error) {
        console.error('Error loading hidden gems:', error);
    }
}

async function loadPersonalizedDailyMixes() {
    try {
        const container = document.getElementById('daily-mixes-grid');
        if (!container) return;

        const response = await fetch('/api/discover/personalized/daily-mixes');
        if (!response.ok) return;

        const data = await response.json();
        if (!data.success || !data.mixes || data.mixes.length === 0) {
            container.closest('.discover-section').style.display = 'none';
            return;
        }

        personalizedDailyMixes = data.mixes;

        // Render Daily Mix cards
        let html = '';
        data.mixes.forEach((mix, index) => {
            const coverUrl = mix.tracks && mix.tracks.length > 0 ?
                (mix.tracks[0].album_cover_url || '/static/placeholder-album.png') :
                '/static/placeholder-album.png';

            html += `
                <div class="discover-playlist-card" onclick="openDailyMix(${index})">
                    <div class="discover-playlist-cover">
                        <img src="${coverUrl}" alt="${mix.name}" loading="lazy">
                        <div class="playlist-play-overlay">▶</div>
                    </div>
                    <div class="discover-playlist-info">
                        <h4 class="discover-playlist-name">${mix.name}</h4>
                        <p class="discover-playlist-description">${mix.description}</p>
                        <p class="discover-playlist-count">${mix.track_count} tracks</p>
                    </div>
                </div>
            `;
        });

        container.innerHTML = html;
        container.closest('.discover-section').style.display = 'block';

    } catch (error) {
        console.error('Error loading daily mixes:', error);
    }
}

function renderCompactPlaylist(container, tracks) {
    let html = '<div class="discover-playlist-tracks-compact">';

    tracks.forEach((track, index) => {
        const coverUrl = track.album_cover_url || '/static/placeholder-album.png';
        const durationMin = Math.floor(track.duration_ms / 60000);
        const durationSec = Math.floor((track.duration_ms % 60000) / 1000);
        const duration = `${durationMin}:${durationSec.toString().padStart(2, '0')}`;
        const artistEsc = (track.artist_name || '').replace(/'/g, "\\'").replace(/"/g, '&quot;');

        html += `
            <div class="discover-playlist-track-compact" data-track-index="${index}">
                <div class="track-compact-number">${index + 1}</div>
                <div class="track-compact-image">
                    <img src="${coverUrl}" alt="${track.album_name}" loading="lazy">
                </div>
                <div class="track-compact-info">
                    <div class="track-compact-name">${track.track_name}</div>
                    <div class="track-compact-artist">${track.artist_name}</div>
                </div>
                <div class="track-compact-album">${track.album_name}</div>
                <div class="track-compact-duration">${duration}</div>
                <button class="track-compact-block" onclick="event.stopPropagation(); blockDiscoveryArtist('${artistEsc}')" title="Block ${artistEsc} from discovery">✕</button>
            </div>
        `;
    });

    html += '</div>';
    container.innerHTML = html;
}

async function blockDiscoveryArtist(artistName) {
    if (!confirm(`Block "${artistName}" from all discovery playlists?`)) return;
    try {
        const res = await fetch('/api/discover/artist-blacklist', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ artist_name: artistName })
        });
        const data = await res.json();
        if (data.success) {
            showToast(`Blocked ${artistName} from discovery`, 'success');
            // Refresh all discovery sections to remove the artist
            loadPersonalizedHiddenGems();
            loadDiscoveryShuffle();
            loadPersonalizedDailyMixes();
        } else {
            showToast(data.error || 'Failed to block artist', 'error');
        }
    } catch (e) {
        showToast('Error blocking artist', 'error');
    }
}

async function openDiscoveryBlacklistModal() {
    if (document.getElementById('discovery-blacklist-modal-overlay')) return;

    const overlay = document.createElement('div');
    overlay.id = 'discovery-blacklist-modal-overlay';
    overlay.className = 'modal-overlay';
    overlay.onclick = (e) => { if (e.target === overlay) overlay.remove(); };

    overlay.innerHTML = `
        <div class="discover-blacklist-modal">
            <div class="discover-blacklist-modal-header">
                <h2>Blocked Artists</h2>
                <p>These artists won't appear in any discovery playlist across all sources</p>
                <button class="watch-all-close" onclick="document.getElementById('discovery-blacklist-modal-overlay').remove()">&times;</button>
            </div>
            <div class="discover-blacklist-modal-search">
                <input type="text" id="dbl-search-input" placeholder="Search for an artist to block..." autocomplete="off">
                <div id="dbl-search-results" class="dbl-search-results" style="display:none"></div>
            </div>
            <div class="discover-blacklist-modal-list" id="dbl-list">
                <div class="discover-blacklist-empty">Loading...</div>
            </div>
            <div class="discover-blacklist-modal-footer">
                <button class="watch-all-btn watch-all-btn-cancel" onclick="document.getElementById('discovery-blacklist-modal-overlay').remove()">Close</button>
            </div>
        </div>
    `;
    document.body.appendChild(overlay);

    // Wire up search
    let searchTimer = null;
    const input = document.getElementById('dbl-search-input');
    input.addEventListener('input', () => {
        clearTimeout(searchTimer);
        const q = input.value.trim();
        if (q.length < 2) { document.getElementById('dbl-search-results').style.display = 'none'; return; }
        searchTimer = setTimeout(() => _dblSearch(q), 300);
    });

    _dblLoadList();
}

async function _dblSearch(query) {
    const resultsEl = document.getElementById('dbl-search-results');
    if (!resultsEl) return;
    try {
        // Use existing enhanced search to find artists
        const res = await fetch('/api/enhanced-search', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ query, limit: 8 })
        });
        const data = await res.json();
        const artists = data.spotify_artists || data.artists || [];
        if (artists.length === 0) {
            resultsEl.innerHTML = '<div class="dbl-search-empty">No artists found</div>';
            resultsEl.style.display = 'block';
            return;
        }
        resultsEl.innerHTML = artists.map(a => {
            const name = _escToast(a.name || '');
            const img = a.image_url ? `<img src="${a.image_url}" class="dbl-search-img">` : '<div class="dbl-search-img-placeholder">🎤</div>';
            return `<div class="dbl-search-item" onclick="_dblBlockFromSearch('${name.replace(/'/g, "\\'")}')">
                ${img}
                <span class="dbl-search-name">${name}</span>
                <span class="dbl-search-action">Block</span>
            </div>`;
        }).join('');
        resultsEl.style.display = 'block';
    } catch (e) {
        resultsEl.style.display = 'none';
    }
}

async function _dblBlockFromSearch(artistName) {
    try {
        const res = await fetch('/api/discover/artist-blacklist', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ artist_name: artistName })
        });
        const data = await res.json();
        if (data.success) {
            showToast(`Blocked ${artistName} from discovery`, 'success');
            document.getElementById('dbl-search-results').style.display = 'none';
            const input = document.getElementById('dbl-search-input');
            if (input) input.value = '';
            _dblLoadList();
        }
    } catch (e) {
        showToast('Error blocking artist', 'error');
    }
}

async function _dblLoadList() {
    const container = document.getElementById('dbl-list');
    if (!container) return;
    try {
        const res = await fetch('/api/discover/artist-blacklist');
        const data = await res.json();
        if (!data.success || !data.entries || data.entries.length === 0) {
            container.innerHTML = '<div class="discover-blacklist-empty">No blocked artists yet — search above to block one</div>';
            return;
        }
        container.innerHTML = data.entries.map(e => `
            <div class="discover-blacklist-item">
                <span class="discover-blacklist-name">${_escToast(e.artist_name)}</span>
                <span class="discover-blacklist-date">${e.created_at ? new Date(e.created_at).toLocaleDateString() : ''}</span>
                <button class="discover-blacklist-remove" onclick="unblockDiscoveryArtist(${e.id}, '${_escAttr(e.artist_name)}')" title="Unblock">✕</button>
            </div>
        `).join('');
    } catch (e) {
        container.innerHTML = '<div class="discover-blacklist-empty">Failed to load</div>';
    }
}

async function unblockDiscoveryArtist(id, name) {
    try {
        const res = await fetch(`/api/discover/artist-blacklist/${id}`, { method: 'DELETE' });
        const data = await res.json();
        if (data.success) {
            showToast(`Unblocked ${name}`, 'success');
            _dblLoadList();
        }
    } catch (e) {
        showToast('Error unblocking artist', 'error');
    }
}

// Backwards compat — called during page init but now a no-op (modal handles it)
// ── Your Artists (Liked Artists Pool) ──

async function loadYourArtists() {
    const section = document.getElementById('your-artists-section');
    const carousel = document.getElementById('your-artists-carousel');
    const subtitle = document.getElementById('your-artists-subtitle');
    if (!section || !carousel) return;

    try {
        const resp = await fetch('/api/discover/your-artists');
        if (!resp.ok) return;
        const data = await resp.json();

        if (!data.artists || data.artists.length === 0) {
            if (data.stale) {
                // First load — show section with loading state, poll until ready
                section.style.display = '';
                if (subtitle) subtitle.textContent = 'Discovering your artists across connected services...';
                carousel.innerHTML = `
                    <div class="ya-loading">
                        <div class="watch-all-loading-spinner"></div>
                        <span>Fetching and matching artists from your services...</span>
                    </div>
                `;
                _pollYourArtists();
            } else {
                section.style.display = 'none';
            }
            return;
        }

        // Show section
        section.style.display = '';

        // Update subtitle with source info
        const sources = new Set();
        data.artists.forEach(a => (a.source_services || []).forEach(s => sources.add(s)));
        const sourceNames = { spotify: 'Spotify', lastfm: 'Last.fm', tidal: 'Tidal', deezer: 'Deezer' };
        const sourceList = [...sources].map(s => sourceNames[s] || s).join(' and ');
        if (subtitle) subtitle.textContent = `Artists you follow on ${sourceList || 'your music services'}`;

        if (data.stale) {
            if (subtitle) subtitle.textContent += ' (updating...)';
            _pollYourArtists();
        }

        // Store for modal access and render carousel cards
        window._yaArtists = {};
        window._yaActiveSource = data.active_source || 'spotify';
        data.artists.forEach(a => { window._yaArtists[a.id] = a; });
        carousel.innerHTML = data.artists.map(a => _renderYourArtistCard(a)).join('');

    } catch (err) {
        console.error('Error loading Your Artists:', err);
    }
}

function _pollYourArtists() {
    // Poll every 5s until artists appear, then stop
    if (window._yaPoller) clearInterval(window._yaPoller);
    let attempts = 0;
    window._yaPoller = setInterval(async () => {
        attempts++;
        if (attempts > 60) { clearInterval(window._yaPoller); window._yaPoller = null; return; }
        try {
            const resp = await fetch('/api/discover/your-artists');
            if (!resp.ok) return;
            const data = await resp.json();
            if (data.artists && data.artists.length > 0) {
                clearInterval(window._yaPoller);
                window._yaPoller = null;
                loadYourArtists(); // Re-render with real data
            }
        } catch (e) { }
    }, 5000);
}

function _renderYourArtistCard(artist) {
    const _esc = (s) => escapeHtml(s || '');
    const img = artist.image_url || '';

    // Build metadata source badges (same pattern as library page)
    const badges = [];
    if (artist.spotify_artist_id) badges.push({ logo: SPOTIFY_LOGO_URL, fb: 'SP', title: 'Spotify' });
    if (artist.itunes_artist_id) badges.push({ logo: ITUNES_LOGO_URL, fb: 'IT', title: 'Apple Music' });
    if (artist.deezer_artist_id) badges.push({ logo: DEEZER_LOGO_URL, fb: 'Dz', title: 'Deezer' });
    if (artist.discogs_artist_id) badges.push({ logo: DISCOGS_LOGO_URL, fb: 'DC', title: 'Discogs' });
    const badgeHTML = badges.map(b =>
        `<div class="ya-badge" title="${b.title}">${b.logo ? `<img src="${b.logo}" onerror="this.parentNode.textContent='${b.fb}'">` : `<span>${b.fb}</span>`}</div>`
    ).join('');

    // Origin dots (which services the artist came from)
    const sources = artist.source_services || [];
    const sourceColors = { spotify: '#1DB954', lastfm: '#D51007', tidal: '#00FFFF', deezer: '#A238FF' };
    const originDots = sources.map(s =>
        `<span class="ya-origin-dot" style="background:${sourceColors[s] || '#666'}" title="From ${s}"></span>`
    ).join('');

    const watchlistClass = artist.on_watchlist ? 'active' : '';
    const hasId = artist.active_source_id && artist.active_source_id !== '';

    // Navigate to Artists page (name click) — source artist id, needs inline view
    const navAction = hasId
        ? `event.stopPropagation(); navigateToArtistDetail('${escapeForInlineJs(artist.active_source_id)}', '${escapeForInlineJs(artist.artist_name)}')`
        : '';

    // Open info modal (card body click) — pass pool ID so we can look up all data
    const infoAction = hasId
        ? `openYourArtistInfoModal(${artist.id})`
        : '';

    // Deezer fallback for images
    const deezerFb = artist.deezer_artist_id ? `onerror="if(!this.dataset.tried){this.dataset.tried='1';this.src='https://api.deezer.com/artist/${artist.deezer_artist_id}/image?size=big'}else{this.style.display='none';this.nextElementSibling.style.display='flex'}"` : `onerror="this.style.display='none';this.nextElementSibling.style.display='flex'"`;

    return `
        <div class="ya-card" ${infoAction ? `onclick="${infoAction}"` : ''}>
            <div class="ya-card-img">
                ${img ? `<img src="${img}" alt="" loading="lazy" ${deezerFb}>` : ''}
                <div class="ya-card-placeholder" ${img ? 'style="display:none"' : ''}>&#9835;</div>
            </div>
            <div class="ya-card-gradient"></div>
            <div class="ya-card-badges">${badgeHTML}</div>
            <button class="ya-watchlist-btn ${watchlistClass}" title="${artist.on_watchlist ? 'On watchlist' : 'Add to watchlist'}"
                    onclick="event.stopPropagation(); toggleYourArtistWatchlist(${artist.id}, '${escapeForInlineJs(artist.artist_name)}', '${escapeForInlineJs(artist.active_source_id || '')}', '${escapeForInlineJs(artist.active_source || '')}', this)">
                <svg width="14" height="14" viewBox="0 0 24 24" fill="${artist.on_watchlist ? 'currentColor' : 'none'}" stroke="currentColor" stroke-width="2">
                    <path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"/><circle cx="12" cy="12" r="3"/>
                </svg>
            </button>
            <div class="ya-card-info">
                <div class="ya-card-info-row">
                    <div class="ya-origin-dots">${originDots}</div>
                </div>
                <div class="ya-card-name" ${navAction ? `onclick="${navAction}"` : ''}>${_esc(artist.artist_name)}</div>
            </div>
        </div>
    `;
}

async function openYourArtistInfoModal(poolId) {
    const pool = (window._yaArtists || {})[poolId];
    if (!pool) return;

    const artistId = pool.active_source_id;
    const artistName = pool.artist_name;
    const imageUrl = pool.image_url || '';

    const existing = document.getElementById('ya-info-modal-overlay');
    if (existing) existing.remove();

    const overlay = document.createElement('div');
    overlay.id = 'ya-info-modal-overlay';
    overlay.className = 'modal-overlay';
    overlay.style.zIndex = '10001';
    overlay.onclick = (e) => { if (e.target === overlay) overlay.remove(); };

    // Build matched source badges from pool data
    const _mb = (logo, fb, title) => `<div class="ya-info-badge" title="${title}">${logo ? `<img src="${logo}" onerror="this.parentNode.textContent='${fb}'">` : `<span>${fb}</span>`}</div>`;
    const matchBadges = [];
    if (pool.spotify_artist_id) matchBadges.push(_mb(SPOTIFY_LOGO_URL, 'SP', 'Matched on Spotify'));
    if (pool.itunes_artist_id) matchBadges.push(_mb(ITUNES_LOGO_URL, 'IT', 'Matched on Apple Music'));
    if (pool.deezer_artist_id) matchBadges.push(_mb(DEEZER_LOGO_URL, 'Dz', 'Matched on Deezer'));
    if (pool.discogs_artist_id) matchBadges.push(_mb(DISCOGS_LOGO_URL, 'DC', 'Matched on Discogs'));

    // Origin info
    const sources = pool.source_services || [];
    const sourceNames = { spotify: 'Spotify', lastfm: 'Last.fm', tidal: 'Tidal', deezer: 'Deezer' };
    const originText = sources.map(s => sourceNames[s] || s).join(', ');

    overlay.innerHTML = `
        <div class="ya-info-modal">
            <button class="watch-all-close" onclick="document.getElementById('ya-info-modal-overlay').remove()">&times;</button>
            <div class="ya-info-hero">
                <div class="ya-info-hero-bg" ${imageUrl ? `style="background-image:url('${escapeHtml(imageUrl)}')"` : ''}></div>
                <div class="ya-info-hero-content">
                    <div class="ya-info-hero-img">
                        ${imageUrl ? `<img src="${escapeHtml(imageUrl)}" alt="">` : '<div class="ya-info-img-fallback">&#9835;</div>'}
                    </div>
                    <div class="ya-info-hero-text">
                        <h2 class="ya-info-name">${escapeHtml(artistName)}</h2>
                        <div class="ya-info-badges">${matchBadges.join('')}</div>
                        ${originText ? `<div class="ya-info-origin">Followed on ${escapeHtml(originText)}</div>` : ''}
                    </div>
                </div>
            </div>
            <div class="ya-info-body" id="ya-info-body">
                <div class="cache-health-loading"><div class="watch-all-loading-spinner"></div><div>Loading artist info...</div></div>
            </div>
            <div class="ya-info-footer" id="ya-info-footer"></div>
        </div>
    `;
    document.body.appendChild(overlay);

    // Fetch enrichment data (with timeout)
    try {
        const controller = new AbortController();
        const timeout = setTimeout(() => controller.abort(), 8000);
        const lookupId = artistId || encodeURIComponent(artistName);
        const resp = await fetch(`/api/discover/your-artists/info/${lookupId}?name=${encodeURIComponent(artistName)}`, { signal: controller.signal });
        clearTimeout(timeout);
        const artist = resp.ok ? await resp.json() : {};
        const bodyEl = document.getElementById('ya-info-body');
        const footerEl = document.getElementById('ya-info-footer');

        const genres = artist.genres || [];
        const bio = artist.summary || '';
        const listeners = artist.lastfm_listeners || artist.followers || 0;
        const playcount = artist.lastfm_playcount || 0;
        const popularity = artist.popularity || 0;

        let bodyHTML = '';

        // Stats
        if (listeners || playcount || popularity) {
            bodyHTML += `<div class="ya-info-stats">
                ${listeners ? `<div class="ya-info-stat"><span class="ya-info-stat-value">${Number(listeners).toLocaleString()}</span><span class="ya-info-stat-label">listeners</span></div>` : ''}
                ${playcount ? `<div class="ya-info-stat"><span class="ya-info-stat-value">${Number(playcount).toLocaleString()}</span><span class="ya-info-stat-label">plays</span></div>` : ''}
                ${popularity ? `<div class="ya-info-stat"><span class="ya-info-stat-value">${popularity}</span><span class="ya-info-stat-label">popularity</span></div>` : ''}
            </div>`;
        }

        // Genres
        if (genres.length > 0) {
            bodyHTML += `<div class="ya-info-section">
                <div class="ya-info-genres">${genres.map(g => `<span class="ya-info-genre">${escapeHtml(g)}</span>`).join('')}</div>
            </div>`;
        }

        // Bio
        if (bio) {
            const cleanBio = bio.replace(/<a[^>]*>.*?<\/a>/gi, '').replace(/<[^>]+>/g, '').trim();
            if (cleanBio) {
                bodyHTML += `<div class="ya-info-section">
                    <div class="ya-info-section-title">About</div>
                    <div class="ya-info-bio">${escapeHtml(cleanBio.length > 600 ? cleanBio.substring(0, 600) + '...' : cleanBio)}</div>
                </div>`;
            }
        }

        // Related artists from map connections
        const related = pool._related || [];
        if (related.length > 0) {
            const relLabel = pool.on_watchlist ? 'Similar Artists' : 'Connected To';
            bodyHTML += `<div class="ya-info-section">
                <div class="ya-info-section-title">${relLabel}</div>
                <div class="ya-info-related">
                    ${related.slice(0, 12).map(r => {
                const rImg = r.image_url || '';
                const rType = r.type === 'watchlist';
                return `<div class="ya-info-related-item" onclick="document.getElementById('ya-info-modal-overlay')?.remove(); setTimeout(() => openYourArtistInfoModal_direct(${JSON.stringify({
                    id: r.id, name: r.name, image_url: rImg,
                    spotify_id: r.spotify_id || '', itunes_id: r.itunes_id || '',
                    deezer_id: r.deezer_id || '', discogs_id: r.discogs_id || '',
                    type: r.type
                }).replace(/"/g, '&quot;')}), 100)">
                            <div class="ya-info-related-img">
                                ${rImg ? `<img src="${escapeHtml(rImg)}" alt="">` : '<span>&#9835;</span>'}
                            </div>
                            <div class="ya-info-related-text">
                                <div class="ya-info-related-name">${escapeHtml(r.name)}</div>
                                ${rType ? '<div class="ya-info-related-badge">★ Watchlist</div>' : ''}
                            </div>
                        </div>`;
            }).join('')}
                    ${related.length > 12 ? `<div class="ya-info-related-more">+${related.length - 12} more</div>` : ''}
                </div>
            </div>`;
        }

        if (!bodyHTML) bodyHTML = '<div class="ya-info-empty">No additional info available</div>';
        if (bodyEl) bodyEl.innerHTML = bodyHTML;

        // Footer
        if (footerEl) {
            const watchBtn = pool.on_watchlist
                ? `<button class="ya-header-btn" onclick="toggleYourArtistWatchlist(${pool.id}, '${escapeForInlineJs(artistName)}', '${escapeForInlineJs(artistId)}', '${escapeForInlineJs(pool.active_source || '')}', this); this.textContent='Done'; this.disabled=true">Remove from Watchlist</button>`
                : `<button class="ya-header-btn" onclick="toggleYourArtistWatchlist(${pool.id}, '${escapeForInlineJs(artistName)}', '${escapeForInlineJs(artistId)}', '${escapeForInlineJs(pool.active_source || '')}', this); this.textContent='Added!'; this.disabled=true">Add to Watchlist</button>`;
            footerEl.innerHTML = `
                ${watchBtn}
                <button class="ya-header-btn" onclick="document.getElementById('ya-info-modal-overlay')?.remove(); document.getElementById('your-artists-modal-overlay')?.remove(); openArtistMapExplorerDirect('${escapeForInlineJs(artistName)}')">
                    <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/></svg>
                    <span>Explore</span>
                </button>
                <button class="ya-header-btn ya-viewall-btn" onclick="document.getElementById('ya-info-modal-overlay')?.remove(); document.getElementById('your-artists-modal-overlay')?.remove(); navigateToArtistDetail('${escapeForInlineJs(artistId)}', '${escapeForInlineJs(artistName)}', '${escapeForInlineJs(pool.active_source || '')}' || null)">
                    <span>View Discography</span>
                    <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><path d="M5 12h14"/><path d="M12 5l7 7-7 7"/></svg>
                </button>
            `;
        }
    } catch (err) {
        console.error('[Artist Info] Error loading artist info:', err);
        const bodyEl = document.getElementById('ya-info-body');
        if (bodyEl) bodyEl.innerHTML = `<div class="ya-info-empty">Could not load artist info</div>`;
    }
}

async function toggleYourArtistWatchlist(poolId, artistName, sourceId, source, btnEl) {
    const isWatched = btnEl && btnEl.classList.contains('active');
    try {
        if (isWatched) {
            const resp = await fetch('/api/watchlist/remove', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ artist_id: sourceId })
            });
            if (resp.ok) {
                if (btnEl) {
                    btnEl.classList.remove('active');
                    const svg = btnEl.querySelector('svg');
                    if (svg) svg.setAttribute('fill', 'none');
                }
                showToast(`Removed ${artistName} from watchlist`, 'info');
                // Sync card eye icon
                _syncYaCardWatchlist(poolId, false);
            }
        } else {
            const resp = await fetch('/api/watchlist/add', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ artist_id: sourceId, artist_name: artistName, source: source })
            });
            if (resp.ok) {
                if (btnEl) {
                    btnEl.classList.add('active');
                    const svg = btnEl.querySelector('svg');
                    if (svg) svg.setAttribute('fill', 'currentColor');
                }
                showToast(`Added ${artistName} to watchlist`, 'success');
                _syncYaCardWatchlist(poolId, true);
            }
        }
    } catch (err) {
        showToast('Failed to update watchlist', 'error');
    }
}

function _syncYaCardWatchlist(poolId, watched) {
    // Sync the card's eye icon with watchlist state (covers modal → card sync)
    document.querySelectorAll('.ya-card .ya-watchlist-btn').forEach(btn => {
        const card = btn.closest('.ya-card');
        if (!card) return;
        // Match by onclick containing the poolId
        const onclick = btn.getAttribute('onclick') || '';
        if (onclick.includes(`(${poolId},`)) {
            if (watched) {
                btn.classList.add('active');
                const svg = btn.querySelector('svg');
                if (svg) svg.setAttribute('fill', 'currentColor');
            } else {
                btn.classList.remove('active');
                const svg = btn.querySelector('svg');
                if (svg) svg.setAttribute('fill', 'none');
            }
        }
    });
    // Update pool data
    if (window._yaArtists && window._yaArtists[poolId]) {
        window._yaArtists[poolId].on_watchlist = watched ? 1 : 0;
    }
}

async function refreshYourArtists() {
    const btn = document.getElementById('your-artists-refresh-btn');
    if (btn) { btn.disabled = true; btn.style.opacity = '0.5'; }
    const subtitle = document.getElementById('your-artists-subtitle');
    if (subtitle) subtitle.textContent = 'Refreshing from your services...';

    try {
        await fetch('/api/discover/your-artists/refresh?clear=true', { method: 'POST' });
        // Poll until done
        let attempts = 0;
        const poll = setInterval(async () => {
            attempts++;
            if (attempts > 60) { clearInterval(poll); return; } // 5 min max
            try {
                const resp = await fetch('/api/discover/your-artists');
                const data = await resp.json();
                if (!data.stale && data.artists && data.artists.length > 0) {
                    clearInterval(poll);
                    loadYourArtists();
                    if (btn) { btn.disabled = false; btn.style.opacity = ''; }
                    showToast(`Found ${data.total} artists from your services`, 'success');
                }
            } catch (e) { }
        }, 5000);
    } catch (err) {
        showToast('Failed to start refresh', 'error');
        if (btn) { btn.disabled = false; btn.style.opacity = ''; }
    }
}

async function openYourArtistsSourcesModal() {
    const existing = document.getElementById('ya-sources-modal-overlay');
    if (existing) existing.remove();

    // Fetch current config + connected services
    let enabled = ['spotify', 'tidal', 'lastfm', 'deezer'];
    let connected = [];
    try {
        const resp = await fetch('/api/discover/your-artists/sources');
        if (resp.ok) {
            const data = await resp.json();
            if (data.enabled) enabled = data.enabled;
            if (data.connected) connected = data.connected;
        }
    } catch (e) { }

    const sourceInfo = [
        { id: 'spotify', label: 'Spotify', icon: '🎵' },
        { id: 'tidal', label: 'Tidal', icon: '🌊' },
        { id: 'lastfm', label: 'Last.fm', icon: '📻' },
        { id: 'deezer', label: 'Deezer', icon: '🎶' },
    ];

    const state = {};
    sourceInfo.forEach(s => { state[s.id] = enabled.includes(s.id); });

    const overlay = document.createElement('div');
    overlay.id = 'ya-sources-modal-overlay';
    overlay.className = 'modal-overlay';
    overlay.onclick = (e) => { if (e.target === overlay) overlay.remove(); };

    const rows = sourceInfo.map(s => {
        const isConnected = connected.includes(s.id);
        const isOn = state[s.id];
        return `
            <div class="ya-source-row${isConnected ? '' : ' disconnected'}" data-source="${s.id}" onclick="_yaSourceRowClick('${s.id}')">
                <div class="ya-source-row-left">
                    <span style="font-size:18px">${s.icon}</span>
                    <div>
                        <div class="ya-source-name">${s.label}</div>
                        <div class="ya-source-status">${isConnected ? 'Connected' : 'Not connected'}</div>
                    </div>
                </div>
                <button class="ya-source-toggle${isOn ? ' on' : ''}" id="ya-toggle-${s.id}" onclick="event.stopPropagation();_yaSourceToggle('${s.id}')"></button>
            </div>`;
    }).join('');

    overlay.innerHTML = `
        <div class="ya-sources-modal">
            <h2>Your Artists Sources</h2>
            <p class="ya-sources-desc">Choose which connected services contribute artists to this section.</p>
            <div class="ya-sources-list">${rows}</div>
            <div class="ya-sources-footer">
                <button class="ya-sources-cancel-btn" onclick="document.getElementById('ya-sources-modal-overlay').remove()">Cancel</button>
                <button class="ya-sources-save-btn" onclick="_yaSourcesSave()">Save</button>
            </div>
        </div>
    `;
    document.body.appendChild(overlay);
    window._yaSourcesState = state;
}

function _yaSourceRowClick(id) {
    // Don't allow toggling disconnected services
    const row = document.querySelector(`.ya-source-row[data-source="${id}"]`);
    if (row && row.classList.contains('disconnected')) return;
    _yaSourceToggle(id);
}

function _yaSourceToggle(id) {
    // Don't allow toggling disconnected services
    const row = document.querySelector(`.ya-source-row[data-source="${id}"]`);
    if (row && row.classList.contains('disconnected')) return;
    window._yaSourcesState[id] = !window._yaSourcesState[id];
    const btn = document.getElementById(`ya-toggle-${id}`);
    if (btn) btn.classList.toggle('on', window._yaSourcesState[id]);
}

async function _yaSourcesSave() {
    const enabledArr = Object.entries(window._yaSourcesState)
        .filter(([, v]) => v).map(([k]) => k);
    if (enabledArr.length === 0) {
        showToast('Select at least one source', 'error');
        return;
    }
    const enabled = enabledArr.join(',');
    try {
        const resp = await fetch('/api/settings', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ discover: { your_artists_sources: enabled } })
        });
        if (resp.ok) {
            document.getElementById('ya-sources-modal-overlay')?.remove();
            showToast('Sources saved — refresh to apply', 'success');
            // Update subtitle immediately
            const sourceNames = { spotify: 'Spotify', tidal: 'Tidal', lastfm: 'Last.fm', deezer: 'Deezer' };
            const subtitle = document.getElementById('your-artists-subtitle');
            if (subtitle) {
                const names = enabledArr.map(s => sourceNames[s] || s).join(' and ');
                subtitle.textContent = `Artists you follow on ${names}`;
            }
        } else {
            showToast('Failed to save sources', 'error');
        }
    } catch (e) {
        showToast('Failed to save sources', 'error');
    }
}

async function openYourArtistsModal() {
    const existing = document.getElementById('your-artists-modal-overlay');
    if (existing) existing.remove();

    const overlay = document.createElement('div');
    overlay.id = 'your-artists-modal-overlay';
    overlay.className = 'modal-overlay';
    overlay.onclick = (e) => { if (e.target === overlay) overlay.remove(); };

    overlay.innerHTML = `
        <div class="ya-modal">
            <div class="ya-modal-header">
                <div>
                    <h2 class="ya-modal-title">Your Artists</h2>
                    <p class="ya-modal-subtitle" id="ya-modal-subtitle">Loading...</p>
                </div>
                <button class="watch-all-close" onclick="document.getElementById('your-artists-modal-overlay').remove()">&times;</button>
            </div>
            <div class="ya-modal-toolbar">
                <input type="text" id="ya-modal-search" class="ya-modal-search" placeholder="Search artists...">
                <div class="ya-modal-filters">
                    <button class="ya-filter-btn active" data-source="" onclick="_yaFilterSource('')">All</button>
                    <button class="ya-filter-btn" data-source="spotify" onclick="_yaFilterSource('spotify')">Spotify</button>
                    <button class="ya-filter-btn" data-source="tidal" onclick="_yaFilterSource('tidal')">Tidal</button>
                    <button class="ya-filter-btn" data-source="lastfm" onclick="_yaFilterSource('lastfm')">Last.fm</button>
                    <button class="ya-filter-btn" data-source="deezer" onclick="_yaFilterSource('deezer')">Deezer</button>
                </div>
                <select class="ya-modal-sort" id="ya-modal-sort" onchange="_yaLoadModal()">
                    <option value="name">A-Z</option>
                    <option value="recent">Recently Added</option>
                    <option value="source">By Source</option>
                </select>
            </div>
            <div class="ya-modal-body" id="ya-modal-body">
                <div class="cache-health-loading"><div class="watch-all-loading-spinner"></div><div>Loading...</div></div>
            </div>
            <div class="ya-modal-footer" id="ya-modal-footer"></div>
        </div>
    `;
    document.body.appendChild(overlay);

    // Search debounce
    let searchTimer = null;
    overlay.querySelector('#ya-modal-search').addEventListener('input', () => {
        clearTimeout(searchTimer);
        searchTimer = setTimeout(() => _yaLoadModal(), 300);
    });

    window._yaModalState = { page: 1, source: '', sort: 'name' };
    _yaLoadModal();
}

function _yaFilterSource(source) {
    window._yaModalState.source = source;
    window._yaModalState.page = 1;
    document.querySelectorAll('.ya-filter-btn').forEach(b => b.classList.toggle('active', b.dataset.source === source));
    _yaLoadModal();
}

async function _yaLoadModal() {
    const body = document.getElementById('ya-modal-body');
    const footer = document.getElementById('ya-modal-footer');
    const subtitle = document.getElementById('ya-modal-subtitle');
    if (!body) return;

    const state = window._yaModalState || { page: 1, source: '', sort: 'name' };
    const search = document.getElementById('ya-modal-search')?.value || '';
    const sort = document.getElementById('ya-modal-sort')?.value || 'name';
    state.sort = sort;

    const params = new URLSearchParams({ page: state.page, per_page: 60, sort: state.sort });
    if (state.source) params.set('source', state.source);
    if (search) params.set('search', search);

    try {
        const resp = await fetch(`/api/discover/your-artists/all?${params}`);
        const data = await resp.json();

        if (subtitle) subtitle.textContent = `${data.total} artists matched`;

        if (!data.artists || data.artists.length === 0) {
            body.innerHTML = '<div class="failed-mb-empty">No artists found</div>';
            if (footer) footer.innerHTML = '';
            return;
        }

        // Store for info modal access
        if (!window._yaArtists) window._yaArtists = {};
        data.artists.forEach(a => { window._yaArtists[a.id] = a; });
        body.innerHTML = `<div class="ya-modal-grid">${data.artists.map(a => _renderYourArtistCard(a)).join('')}</div>`;

        // Pagination
        const totalPages = Math.ceil(data.total / 60);
        if (footer && totalPages > 1) {
            footer.innerHTML = `
                <div class="failed-mb-pagination">
                    <button class="failed-mb-btn-sm" ${state.page <= 1 ? 'disabled' : ''} onclick="window._yaModalState.page--; _yaLoadModal()">Prev</button>
                    <span>Page ${state.page} of ${totalPages}</span>
                    <button class="failed-mb-btn-sm" ${state.page >= totalPages ? 'disabled' : ''} onclick="window._yaModalState.page++; _yaLoadModal()">Next</button>
                </div>
            `;
        } else if (footer) {
            footer.innerHTML = '';
        }
    } catch (err) {
        body.innerHTML = '<div class="failed-mb-empty">Failed to load</div>';
    }
}

function loadDiscoveryBlacklist() { }

// ── Artist Map — Circle-packed staged canvas visualization ──
const _artMap = {
    placed: [],
    edges: [],
    images: {},
    canvas: null, ctx: null,
    offscreen: null, offCtx: null, // offscreen buffer for fast pan/zoom
    width: 0, height: 0,
    offsetX: 0, offsetY: 0, zoom: 0.15,
    hoveredNode: null, animFrame: null,
    dirty: true, // true = need to rebuild offscreen buffer
    WATCHLIST_R: 320,
    BUFFER: 8,
};

async function openArtistMap() {
    const container = document.getElementById('artist-map-container');
    if (!container) return;

    // Hide discover sections, show map
    document.querySelectorAll('#discover-page > .discover-container > *:not(#artist-map-container)').forEach(el => {
        el._prevDisplay = el.style.display;
        el.style.display = 'none';
    });
    container.style.display = 'flex';

    const canvas = document.getElementById('artist-map-canvas');
    _artMap.canvas = canvas;
    _artMap.ctx = canvas.getContext('2d');
    _artMap.width = container.clientWidth;
    _artMap.height = container.clientHeight - 50;
    canvas.width = _artMap.width * window.devicePixelRatio;
    canvas.height = _artMap.height * window.devicePixelRatio;
    canvas.style.width = _artMap.width + 'px';
    canvas.style.height = _artMap.height + 'px';
    _artMap.ctx.scale(window.devicePixelRatio, window.devicePixelRatio);
    _artMap.offsetX = _artMap.width / 2;
    _artMap.offsetY = _artMap.height / 2;
    _artMap.placed = [];
    _artMap.images = {};
    _artMap._nodeById = null;

    // Loading screen
    _artMap.ctx.fillStyle = '#0a0a14';
    _artMap.ctx.fillRect(0, 0, _artMap.width, _artMap.height);
    _artMap.ctx.fillStyle = 'rgba(255,255,255,0.3)';
    _artMap.ctx.font = '14px system-ui';
    _artMap.ctx.textAlign = 'center';
    _artMap.ctx.fillText('Building artist map...', _artMap.width / 2, _artMap.height / 2);

    try {
        const resp = await fetch('/api/discover/artist-map');
        const data = await resp.json();
        if (!data.success || !data.nodes.length) {
            _artMap.ctx.fillText('No watchlist artists. Add artists to your watchlist first.', _artMap.width / 2, _artMap.height / 2 + 30);
            return;
        }

        document.getElementById('artist-map-stats').textContent =
            `${data.watchlist_count} watchlist · ${data.similar_count} similar`;

        _artMap.edges = data.edges;
        const WR = _artMap.WATCHLIST_R;
        const BUF = _artMap.BUFFER;

        // ── PHASE 1: Place watchlist artists with guaranteed no-overlap ──
        const wNodes = data.nodes.filter(n => n.type === 'watchlist');
        // Minimum center-to-center distance between watchlist nodes
        const minCenterDist = WR * 3.5; // WR*2 for radii + WR*1.5 gap — similar artists fill the gaps via spiral packing

        // Place watchlist nodes in a spiral — deterministic, guaranteed spacing
        wNodes.forEach((n, i) => {
            n.radius = WR;
            n.opacity = 0;
            if (i === 0) {
                n.x = 0; n.y = 0;
            } else {
                // Golden angle spiral for even distribution
                const angle = i * 2.399963; // golden angle in radians
                const r = minCenterDist * Math.sqrt(i) * 0.7;
                n.x = Math.cos(angle) * r;
                n.y = Math.sin(angle) * r;
            }
        });

        // Post-process: push apart any watchlist nodes that ended up too close
        for (let pass = 0; pass < 50; pass++) {
            let moved = false;
            for (let i = 0; i < wNodes.length; i++) {
                for (let j = i + 1; j < wNodes.length; j++) {
                    const dx = wNodes[j].x - wNodes[i].x;
                    const dy = wNodes[j].y - wNodes[i].y;
                    const dist = Math.sqrt(dx * dx + dy * dy) || 1;
                    if (dist < minCenterDist) {
                        const push = (minCenterDist - dist) / 2 + 1;
                        const nx = (dx / dist) * push;
                        const ny = (dy / dist) * push;
                        wNodes[i].x -= nx; wNodes[i].y -= ny;
                        wNodes[j].x += nx; wNodes[j].y += ny;
                        moved = true;
                    }
                }
            }
            if (!moved) break;
        }

        wNodes.forEach(n => { _artMap.placed.push(n); });

        // ── PHASE 2: Place similar artists around their source watchlist nodes ──
        const sNodes = data.nodes.filter(n => n.type === 'similar');
        sNodes.forEach(n => {
            const occ = n.occurrence || 1;
            const rank = n.rank || 5;
            // Bigger overall: min 25% of WR, max 55%, scaled by relevance
            n.radius = Math.min(WR * 0.55, Math.max(WR * 0.25, WR * 0.2 + occ * WR * 0.06 + (10 - rank) * WR * 0.025));
        });
        sNodes.sort((a, b) => b.radius - a.radius);

        // Build edge lookup: target_id → source node (O(1) instead of .find())
        const edgeMap = {};
        _artMap.edges.forEach(e => { edgeMap[e.target] = e.source; });
        const nodeById = {};
        _artMap.placed.forEach(n => { nodeById[n.id] = n; });

        // Spatial grid for fast collision detection
        // Cell size must cover the largest possible bubble diameter + buffer
        const CELL = WR * 2 + BUF * 2;
        const grid = {};
        function _gridKey(x, y) { return `${Math.floor(x / CELL)},${Math.floor(y / CELL)}`; }
        function _gridAdd(n) {
            const k = _gridKey(n.x, n.y);
            if (!grid[k]) grid[k] = [];
            grid[k].push(n);
        }
        function _gridCheck(x, y, r) {
            const cx = Math.floor(x / CELL);
            const cy = Math.floor(y / CELL);
            // Search wider radius to catch large watchlist bubbles
            for (let dx = -3; dx <= 3; dx++) {
                for (let dy = -3; dy <= 3; dy++) {
                    const cell = grid[`${cx + dx},${cy + dy}`];
                    if (!cell) continue;
                    for (const p of cell) {
                        const ddx = x - p.x, ddy = y - p.y;
                        const minD = r + p.radius + BUF;
                        if (ddx * ddx + ddy * ddy < minD * minD) return true;
                    }
                }
            }
            return false;
        }
        // Add watchlist nodes to grid
        _artMap.placed.forEach(n => _gridAdd(n));

        // Place similar nodes with spatial grid collision
        for (const sn of sNodes) {
            const srcId = edgeMap[sn.id];
            const src = srcId != null ? nodeById[srcId] : null;
            const cx = src ? src.x : 0;
            const cy = src ? src.y : 0;
            const startDist = (src ? src.radius : WR) + sn.radius + BUF;

            let placed = false;
            for (let dist = startDist; dist < startDist + WR * 3; dist += sn.radius * 0.5) {
                const steps = Math.max(8, Math.floor(dist * 0.1));
                const off = Math.random() * Math.PI * 2;
                for (let a = 0; a < steps; a++) {
                    const angle = off + (a / steps) * Math.PI * 2;
                    const tx = cx + Math.cos(angle) * dist;
                    const ty = cy + Math.sin(angle) * dist;
                    if (!_gridCheck(tx, ty, sn.radius)) {
                        sn.x = tx; sn.y = ty; sn.opacity = 0;
                        _artMap.placed.push(sn);
                        nodeById[sn.id] = sn;
                        _gridAdd(sn);
                        placed = true;
                        break;
                    }
                }
                if (placed) break;
            }
        }

        // Auto-zoom to fit all nodes
        let minX = Infinity, maxX = -Infinity, minY = Infinity, maxY = -Infinity;
        _artMap.placed.forEach(n => {
            minX = Math.min(minX, n.x - n.radius);
            maxX = Math.max(maxX, n.x + n.radius);
            minY = Math.min(minY, n.y - n.radius);
            maxY = Math.max(maxY, n.y + n.radius);
        });
        const mapW = maxX - minX + 200;
        const mapH = maxY - minY + 200;
        _artMap.zoom = Math.min(_artMap.width / mapW, _artMap.height / mapH, 1);
        _artMap.offsetX = _artMap.width / 2 - ((minX + maxX) / 2) * _artMap.zoom;
        _artMap.offsetY = _artMap.height / 2 - ((minY + maxY) / 2) * _artMap.zoom;

        // Setup interaction
        _artMapSetupInteraction(canvas);

        // ── PHASE 3: Set all visible, build buffer, render ──
        // Show loading overlay while buffer builds
        const loadingEl = document.createElement('div');
        loadingEl.id = 'artist-map-loading';
        loadingEl.innerHTML = `
            <div class="artist-map-loading-content">
                <div class="watch-all-loading-spinner"></div>
                <div class="artist-map-loading-text" id="artmap-loading-text">Placing ${_artMap.placed.length} artists on the map...</div>
            </div>
        `;
        container.appendChild(loadingEl);

        // Defer heavy work so loading overlay renders first
        setTimeout(async () => {
            _artMap.placed.forEach(n => { n.opacity = 1; });

            // Load images in parallel using createImageBitmap (non-blocking)
            const loadingText = container.querySelector('.artist-map-loading-text');
            const imgNodes = _artMap.placed.filter(n => n.image_url);
            let loaded = 0;

            if (loadingText) loadingText.textContent = `Loading ${imgNodes.length} artist images...`;

            // Batch image loading — 20 concurrent fetches
            const CONCURRENT = 20;
            let idx = 0;

            async function loadNextBatch() {
                const batch = [];
                while (idx < imgNodes.length && batch.length < CONCURRENT) {
                    const n = imgNodes[idx++];
                    if (_artMap.images[n.id]) { loaded++; continue; }
                    batch.push(
                        _artMapLoadImage(n.image_url)
                            .then(bmp => { if (bmp) _artMap.images[n.id] = bmp; })
                            .finally(() => {
                                loaded++;
                                if (loadingText && loaded % 50 === 0) {
                                    loadingText.textContent = `Loading images... ${loaded}/${imgNodes.length}`;
                                }
                            })
                    );
                }
                if (batch.length) await Promise.all(batch);
                if (idx < imgNodes.length) return loadNextBatch();
            }

            await loadNextBatch();

            // Build buffer and render
            if (loadingText) loadingText.textContent = 'Rendering map...';
            await new Promise(r => setTimeout(r, 20)); // let text update render

            _artMap.dirty = true;
            _artMapRender();

            const le = document.getElementById('artist-map-loading');
            if (le) le.remove();
        }, 50);

    } catch (err) {
        console.error('Artist map error:', err);
    }
}

function artMapZoom(factor) {
    const cx = _artMap.width / 2;
    const cy = _artMap.height / 2;
    const targetZoom = Math.max(0.02, Math.min(3, _artMap.zoom * factor));
    const targetOX = cx - (cx - _artMap.offsetX) * (targetZoom / _artMap.zoom);
    const targetOY = cy - (cy - _artMap.offsetY) * (targetZoom / _artMap.zoom);
    _artMapAnimateTo(targetZoom, targetOX, targetOY);
}

function artMapFitToView() {
    if (!_artMap.placed.length) return;
    let minX = Infinity, maxX = -Infinity, minY = Infinity, maxY = -Infinity;
    _artMap.placed.forEach(n => {
        if ((n.opacity || 0) < 0.01) return;
        minX = Math.min(minX, n.x - n.radius);
        maxX = Math.max(maxX, n.x + n.radius);
        minY = Math.min(minY, n.y - n.radius);
        maxY = Math.max(maxY, n.y + n.radius);
    });
    const mapW = maxX - minX + 100;
    const mapH = maxY - minY + 100;
    const targetZoom = Math.min(_artMap.width / mapW, _artMap.height / mapH, 1);
    const targetOX = _artMap.width / 2 - ((minX + maxX) / 2) * targetZoom;
    const targetOY = _artMap.height / 2 - ((minY + maxY) / 2) * targetZoom;
    _artMapAnimateTo(targetZoom, targetOX, targetOY);
}

function _artMapAnimateTo(targetZoom, targetOX, targetOY) {
    if (_artMap._animating) cancelAnimationFrame(_artMap._animating);
    const startZoom = _artMap.zoom;
    const startOX = _artMap.offsetX;
    const startOY = _artMap.offsetY;
    const duration = 250;
    const start = performance.now();

    function step(now) {
        const t = Math.min(1, (now - start) / duration);
        // Ease out cubic
        const e = 1 - Math.pow(1 - t, 3);
        _artMap.zoom = startZoom + (targetZoom - startZoom) * e;
        _artMap.offsetX = startOX + (targetOX - startOX) * e;
        _artMap.offsetY = startOY + (targetOY - startOY) * e;
        _artMapRender(); // blit only, no rebuild
        if (t < 1) {
            _artMap._animating = requestAnimationFrame(step);
        } else {
            _artMap._animating = null;
            _artMap.dirty = true;
            _artMapRender(); // rebuild at final zoom level
        }
    }
    _artMap._animating = requestAnimationFrame(step);
}

function closeArtistMap() {
    const container = document.getElementById('artist-map-container');
    if (container) container.style.display = 'none';
    const sidebar = document.getElementById('artmap-genre-sidebar');
    if (sidebar) sidebar.style.display = 'none';
    if (_artMap.animFrame) cancelAnimationFrame(_artMap.animFrame);
    if (_artMap._keyHandler) window.removeEventListener('keydown', _artMap._keyHandler);
    _artMapHideContextMenu();

    // Restore discover sections
    document.querySelectorAll('#discover-page > .discover-container > *:not(#artist-map-container)').forEach(el => {
        el.style.display = el._prevDisplay !== undefined ? el._prevDisplay : '';
    });
}

// No force simulation — layout is pre-computed via circle packing

function _artMapRebuildBuffer() {
    /**Render ALL nodes once to offscreen canvas. Only called on data changes, not pan/zoom.**/
    const placed = _artMap.placed;
    if (!placed.length) return;

    const visible = placed.filter(n => (n.opacity || 0) > 0.01);
    if (!visible.length) return;

    // World bounds
    let minX = Infinity, maxX = -Infinity, minY = Infinity, maxY = -Infinity;
    visible.forEach(n => {
        minX = Math.min(minX, n.x - n.radius - 10);
        maxX = Math.max(maxX, n.x + n.radius + 10);
        minY = Math.min(minY, n.y - n.radius - 10);
        maxY = Math.max(maxY, n.y + n.radius + 10);
    });

    const bw = maxX - minX;
    const bh = maxY - minY;
    // Scale based on zoom — higher zoom = higher res buffer, capped for memory
    const z = _artMap.zoom || 0.1;
    const scale = Math.min(z * 2, 1.0, 10240 / Math.max(bw, bh));

    if (!_artMap.offscreen) _artMap.offscreen = document.createElement('canvas');
    const oc = _artMap.offscreen;
    oc.width = Math.ceil(bw * scale);
    oc.height = Math.ceil(bh * scale);
    const octx = oc.getContext('2d');
    _artMap._bufferScale = scale;
    _artMap._bufferMinX = minX;
    _artMap._bufferMinY = minY;

    octx.scale(scale, scale);
    octx.translate(-minX, -minY);

    // Build node lookup
    if (!_artMap._nodeById) {
        _artMap._nodeById = {};
        placed.forEach(n => { _artMap._nodeById[n.id] = n; });
    }

    // Draw edges (connection lines between related nodes)
    if (_artMap.edges && _artMap.edges.length > 0) {
        octx.lineWidth = 1;
        octx.strokeStyle = 'rgba(138,43,226,0.08)';
        octx.beginPath();
        for (const edge of _artMap.edges) {
            const s = _artMap._nodeById[edge.source];
            const t = _artMap._nodeById[edge.target];
            if (!s || !t || (s.opacity || 0) < 0.05 || (t.opacity || 0) < 0.05) continue;
            octx.moveTo(s.x, s.y);
            octx.lineTo(t.x, t.y);
        }
        octx.stroke();
    }

    // Draw ALL nodes — genre labels first, similar next, watchlist on top
    const hideSimilar = _artMap._hideSimilar || false;
    // Pass 0: genre labels, Pass 1: similar/ring2, Pass 2: watchlist/center/ring1
    for (let pass = 0; pass < 3; pass++) {
        for (const n of visible) {
            if (pass === 0 && n._isLabel) { /* draw */ }
            else if (pass === 1 && !n._isLabel && n.type !== 'watchlist' && n.type !== 'center' && n.ring !== 1) { /* draw */ }
            else if (pass === 2 && !n._isLabel && (n.type === 'watchlist' || n.type === 'center' || n.ring === 1)) { /* draw */ }
            else continue;
            if (hideSimilar && n.type !== 'watchlist' && n.type !== 'center' && !n._isLabel) continue;
            const op = n.opacity || 0;
            if (op < 0.01) continue;
            const r = n.radius;
            const isW = n.type === 'watchlist' || n.type === 'center';
            octx.globalAlpha = op;

            // Genre label node — transparent circle with large text
            if (n._isLabel) {
                octx.globalAlpha = 0.6;
                octx.beginPath();
                octx.arc(n.x, n.y, n.radius, 0, Math.PI * 2);
                octx.fillStyle = 'rgba(138,43,226,0.04)';
                octx.fill();
                octx.strokeStyle = 'rgba(138,43,226,0.08)';
                octx.lineWidth = 1;
                octx.stroke();
                const labelSize = Math.max(12, n.radius * 0.25);
                octx.font = `800 ${labelSize}px system-ui`;
                octx.textAlign = 'center';
                octx.textBaseline = 'middle';
                octx.fillStyle = 'rgba(138,43,226,0.35)';
                octx.fillText(n.name, n.x, n.y - labelSize * 0.3);
                octx.font = `500 ${labelSize * 0.5}px system-ui`;
                octx.fillStyle = 'rgba(255,255,255,0.15)';
                octx.fillText(`${n._count || 0} artists`, n.x, n.y + labelSize * 0.5);
                octx.globalAlpha = 1;
                continue;
            }

            // Render quality based on node size in buffer pixels
            const rScaled = r * scale;
            const isSmall = rScaled < 8;
            const isTiny = rScaled < 3;

            // Tiny nodes: just a colored dot (no clip, no image, no text)
            if (isTiny) {
                octx.beginPath();
                octx.arc(n.x, n.y, r, 0, Math.PI * 2);
                octx.fillStyle = isW ? '#6b21a8' : '#2a2a40';
                octx.fill();
                continue;
            }

            // Small nodes: filled circle + border, no image clip
            if (isSmall) {
                octx.beginPath();
                octx.arc(n.x, n.y, r, 0, Math.PI * 2);
                const img = _artMap.images[n.id];
                if (img) {
                    octx.save(); octx.clip();
                    octx.drawImage(img, n.x - r, n.y - r, r * 2, r * 2);
                    octx.restore();
                } else {
                    octx.fillStyle = isW ? '#1a0a30' : '#141420';
                    octx.fill();
                }
                octx.strokeStyle = isW ? 'rgba(138,43,226,0.3)' : 'rgba(255,255,255,0.06)';
                octx.lineWidth = isW ? 1.5 : 0.5;
                octx.stroke();
                continue;
            }

            // Full quality: glow + clip + image + text
            if (isW) {
                octx.beginPath();
                octx.arc(n.x, n.y, r + 4, 0, Math.PI * 2);
                octx.strokeStyle = 'rgba(138,43,226,0.25)';
                octx.lineWidth = 5;
                octx.stroke();
            }

            octx.save();
            octx.beginPath();
            octx.arc(n.x, n.y, r, 0, Math.PI * 2);
            octx.closePath();
            octx.clip();

            const img = _artMap.images[n.id];
            if (img) {
                octx.drawImage(img, n.x - r, n.y - r, r * 2, r * 2);
                octx.fillStyle = 'rgba(0,0,0,0.45)';
                octx.fillRect(n.x - r, n.y - r, r * 2, r * 2);
            } else {
                octx.fillStyle = isW ? '#1a0a30' : '#141420';
                octx.fillRect(n.x - r, n.y - r, r * 2, r * 2);
            }
            octx.restore();

            octx.beginPath();
            octx.arc(n.x, n.y, r, 0, Math.PI * 2);
            octx.strokeStyle = isW ? 'rgba(138,43,226,0.4)' : 'rgba(255,255,255,0.08)';
            octx.lineWidth = isW ? 2 : 0.5;
            octx.stroke();

            const fontSize = isW ? Math.max(16, r * 0.14) : Math.max(8, r * 0.3);
            octx.font = `${isW ? '700' : '600'} ${fontSize}px system-ui`;
            octx.textAlign = 'center';
            octx.textBaseline = 'middle';
            octx.fillStyle = '#fff';
            const maxC = isW ? 20 : 12;
            const label = n.name.length > maxC ? n.name.substring(0, maxC - 1) + '…' : n.name;
            octx.fillText(label, n.x, n.y);
        }
    }

    octx.globalAlpha = 1;

    _artMap.dirty = false;
}

function _artMapRender() {
    /**Blit offscreen buffer to screen canvas with pan/zoom. Near-zero cost.**/
    const ctx = _artMap.ctx;
    const w = _artMap.width;
    const h = _artMap.height;

    ctx.fillStyle = '#0a0a14';
    ctx.fillRect(0, 0, w, h);

    if (_artMap.dirty || !_artMap.offscreen) _artMapRebuildBuffer();
    if (!_artMap.offscreen) return;

    const oc = _artMap.offscreen;
    const s = _artMap._bufferScale;
    const mx = _artMap._bufferMinX;
    const my = _artMap._bufferMinY;
    const z = _artMap.zoom;

    // Blit offscreen buffer: the buffer was drawn with scale(s) + translate(-minX,-minY)
    // So buffer pixel (bx,by) corresponds to world (bx/s + minX, by/s + minY)
    // Screen position of world (wx,wy) = offsetX + wx*zoom, offsetY + wy*zoom
    // Therefore buffer origin on screen = offsetX + minX*zoom, offsetY + minY*zoom
    // And buffer is drawn at size (bufferWidth * zoom/s, bufferHeight * zoom/s)
    ctx.drawImage(oc,
        _artMap.offsetX + mx * z,
        _artMap.offsetY + my * z,
        oc.width * z / s,
        oc.height * z / s
    );

    // ── Interactive overlay (drawn on main canvas, not buffer) ──
    const cFade = _artMap._constellationFade || 0;
    if (cFade > 0 && (_artMap.hoveredNode || _artMap._constellationCache)) {
        const n = _artMap.hoveredNode || (_artMap._constellationCache ? (_artMap._nodeById || {})[_artMap._constellationCache.nodeId] : null);
        if (!n) { _artMap._constellationFade = 0; _artMap._constellationCache = null; }
        if (n) {
            ctx.save();
            ctx.translate(_artMap.offsetX, _artMap.offsetY);
            ctx.scale(z, z);

            // Cache connected node lookup (don't recompute every frame)
            if (!_artMap._constellationCache || _artMap._constellationCache.nodeId !== n.id) {
                const connectedIds = new Set();
                if (n.type === 'watchlist') {
                    for (const e of _artMap.edges) {
                        if (e.source === n.id) connectedIds.add(e.target);
                    }
                } else {
                    const sourceIds = new Set();
                    for (const e of _artMap.edges) {
                        if (e.target === n.id) sourceIds.add(e.source);
                    }
                    for (const sid of sourceIds) {
                        connectedIds.add(sid);
                        for (const e of _artMap.edges) {
                            if (e.source === sid) connectedIds.add(e.target);
                        }
                    }
                }
                const nById = _artMap._nodeById || {};
                _artMap._constellationCache = {
                    nodeId: n.id,
                    nodes: [n, ...[...connectedIds].map(id => nById[id]).filter(Boolean)],
                };
            }

            const highlightNodes = _artMap._constellationCache.nodes;

            if (highlightNodes.length > 1) {
                // Semi-transparent dark overlay on entire visible area
                ctx.save();
                ctx.resetTransform();
                ctx.globalAlpha = 0.6 * cFade;
                ctx.fillStyle = '#0a0a14';
                ctx.fillRect(0, 0, _artMap.canvas.width, _artMap.canvas.height);
                ctx.globalAlpha = 1;
                ctx.restore();

                // Draw glowing connection lines
                for (const cn of highlightNodes) {
                    if (cn === n) continue;
                    ctx.beginPath();
                    ctx.moveTo(n.x, n.y);
                    ctx.lineTo(cn.x, cn.y);
                    // Gradient line
                    const lineGrad = ctx.createLinearGradient(n.x, n.y, cn.x, cn.y);
                    lineGrad.addColorStop(0, `rgba(138,43,226,${0.5 * cFade})`);
                    lineGrad.addColorStop(1, `rgba(138,43,226,${0.15 * cFade})`);
                    ctx.strokeStyle = lineGrad;
                    ctx.lineWidth = 2;
                    ctx.stroke();
                }

                // Redraw highlighted nodes on top
                ctx.globalAlpha = cFade;
                for (const hn of highlightNodes) {
                    const r = hn.radius;
                    const isW = hn.type === 'watchlist';
                    const isHov = hn === n;

                    // Glow
                    if (isHov) {
                        ctx.beginPath();
                        ctx.arc(hn.x, hn.y, r + 8, 0, Math.PI * 2);
                        ctx.strokeStyle = 'rgba(138,43,226,0.4)';
                        ctx.lineWidth = 6;
                        ctx.stroke();
                    }

                    // Circle + image
                    ctx.save();
                    ctx.beginPath();
                    ctx.arc(hn.x, hn.y, r, 0, Math.PI * 2);
                    ctx.closePath();
                    ctx.clip();

                    const img = _artMap.images[hn.id];
                    if (img) {
                        ctx.drawImage(img, hn.x - r, hn.y - r, r * 2, r * 2);
                        ctx.fillStyle = 'rgba(0,0,0,0.35)';
                        ctx.fillRect(hn.x - r, hn.y - r, r * 2, r * 2);
                    } else {
                        ctx.fillStyle = isW ? '#1a0a30' : '#141420';
                        ctx.fillRect(hn.x - r, hn.y - r, r * 2, r * 2);
                    }
                    ctx.restore();

                    // Border
                    ctx.beginPath();
                    ctx.arc(hn.x, hn.y, r, 0, Math.PI * 2);
                    ctx.strokeStyle = isHov ? 'rgba(255,255,255,0.7)' : isW ? 'rgba(138,43,226,0.5)' : 'rgba(255,255,255,0.3)';
                    ctx.lineWidth = isHov ? 3 : 1.5;
                    ctx.stroke();

                    // Name
                    const fontSize = isW ? Math.max(14, r * 0.14) : Math.max(8, r * 0.3);
                    ctx.font = `${isW ? '700' : '600'} ${fontSize}px system-ui`;
                    ctx.textAlign = 'center';
                    ctx.textBaseline = 'middle';
                    ctx.fillStyle = '#fff';
                    const maxC = isW ? 20 : 12;
                    const label = hn.name.length > maxC ? hn.name.substring(0, maxC - 1) + '…' : hn.name;
                    ctx.fillText(label, hn.x, hn.y);
                }
                ctx.globalAlpha = 1;
            } else {
                // Single node, no connections
                ctx.beginPath();
                ctx.arc(n.x, n.y, n.radius + 4, 0, Math.PI * 2);
                ctx.strokeStyle = 'rgba(255,255,255,0.5)';
                ctx.lineWidth = 3;
                ctx.stroke();
            }

            ctx.restore();
        } // end if(n)
    } else if (_artMap.hoveredNode && !_artMap._constellationActive) {
        // Pre-constellation: just show a simple highlight ring (instant, no delay)
        const n = _artMap.hoveredNode;
        ctx.save();
        ctx.translate(_artMap.offsetX, _artMap.offsetY);
        ctx.scale(z, z);
        ctx.beginPath();
        ctx.arc(n.x, n.y, n.radius + 3, 0, Math.PI * 2);
        ctx.strokeStyle = 'rgba(255,255,255,0.35)';
        ctx.lineWidth = 2;
        ctx.stroke();
        ctx.restore();
    }

    // Click ripple animation
    if (_artMap._ripple) {
        const rip = _artMap._ripple;
        const elapsed = performance.now() - rip.start;
        const progress = elapsed / 400; // 400ms duration
        if (progress < 1) {
            ctx.save();
            ctx.translate(_artMap.offsetX, _artMap.offsetY);
            ctx.scale(z, z);
            const ripR = rip.radius + rip.radius * progress * 0.5;
            ctx.beginPath();
            ctx.arc(rip.x, rip.y, ripR, 0, Math.PI * 2);
            ctx.strokeStyle = `rgba(138,43,226,${0.5 * (1 - progress)})`;
            ctx.lineWidth = 3 * (1 - progress);
            ctx.stroke();
            ctx.restore();
            requestAnimationFrame(() => _artMapRender());
        } else {
            _artMap._ripple = null;
        }
    }
}

function artMapSearch(query) {
    const results = document.getElementById('artist-map-search-results');
    if (!results) return;
    if (!query || query.length < 2) { results.style.display = 'none'; return; }

    const q = query.toLowerCase();
    const matches = _artMap.placed.filter(n => (n.opacity || 0) > 0.5 && n.name.toLowerCase().includes(q)).slice(0, 8);

    if (!matches.length) { results.style.display = 'none'; return; }

    results.style.display = 'block';
    results.innerHTML = matches.map(n =>
        `<div class="artist-map-search-item" onclick="artMapZoomToNode(${n.id})">
            <span class="artist-map-search-type ${n.type}">${n.type === 'watchlist' ? '★' : '○'}</span>
            ${escapeHtml(n.name)}
        </div>`
    ).join('');
}

function artMapZoomToNode(nodeId) {
    const n = _artMap.placed.find(p => p.id === nodeId);
    if (!n) return;
    // Zoom to show this node centered, at a comfortable zoom level
    const targetZoom = Math.max(0.3, Math.min(1, 200 / n.radius));
    const targetOX = _artMap.width / 2 - n.x * targetZoom;
    const targetOY = _artMap.height / 2 - n.y * targetZoom;
    _artMapAnimateTo(targetZoom, targetOX, targetOY);
    // Highlight briefly after animation
    setTimeout(() => { _artMap.hoveredNode = n; _artMapRender(); }, 300);
    setTimeout(() => { _artMap.hoveredNode = null; _artMapRender(); }, 2500);
    // Close search
    const results = document.getElementById('artist-map-search-results');
    if (results) results.style.display = 'none';
    const input = document.getElementById('artist-map-search');
    if (input) input.value = '';
}

function _artMapShowTooltip(e, node) {
    const tip = document.getElementById('artist-map-tooltip');
    if (!tip) return;
    if (!node) { tip.style.display = 'none'; return; }

    const img = node.image_url ? `<img class="artmap-tip-img" src="${escapeHtml(node.image_url)}" alt="">` : '<div class="artmap-tip-img artmap-tip-img-fallback">&#9835;</div>';
    const genres = (node.genres || []).slice(0, 3);
    const genreHTML = genres.length ? `<div class="artmap-tip-genres">${genres.map(g => `<span>${escapeHtml(g)}</span>`).join('')}</div>` : '';
    const typeLabel = node.type === 'watchlist' ? '<span class="artmap-tip-badge">★ Watchlist</span>' : '';

    tip.innerHTML = `
        <div class="artmap-tip-row">
            ${img}
            <div class="artmap-tip-info">
                <div class="artmap-tip-name">${escapeHtml(node.name)}</div>
                ${typeLabel}
                ${genreHTML}
            </div>
        </div>
    `;
    tip.style.display = 'block';

    // Position — keep on screen
    const x = Math.min(e.clientX + 16, window.innerWidth - tip.offsetWidth - 10);
    const y = Math.min(e.clientY - 10, window.innerHeight - tip.offsetHeight - 10);
    tip.style.left = x + 'px';
    tip.style.top = y + 'px';
}

function _artMapAnimateConstellation() {
    if (_artMap._constellationActive && _artMap._constellationFade < 1) {
        _artMap._constellationFade = Math.min(1, (_artMap._constellationFade || 0) + 0.08);
        _artMapRender();
        requestAnimationFrame(_artMapAnimateConstellation);
    } else if (!_artMap._constellationActive && _artMap._constellationFade > 0) {
        _artMap._constellationFade = Math.max(0, _artMap._constellationFade - 0.1);
        _artMapRender();
        if (_artMap._constellationFade > 0) {
            requestAnimationFrame(_artMapAnimateConstellation);
        } else {
            _artMap._constellationCache = null;
        }
    }
}

function artMapShowShortcuts() {
    const existing = document.getElementById('artmap-shortcuts-overlay');
    if (existing) { existing.remove(); return; }

    const overlay = document.createElement('div');
    overlay.id = 'artmap-shortcuts-overlay';
    overlay.className = 'modal-overlay';
    overlay.style.zIndex = '10002';
    overlay.onclick = (e) => { if (e.target === overlay) overlay.remove(); };

    overlay.innerHTML = `
        <div class="artmap-shortcuts-modal">
            <div class="artmap-shortcuts-header">
                <h3>Keyboard Shortcuts</h3>
                <button class="watch-all-close" onclick="document.getElementById('artmap-shortcuts-overlay').remove()">&times;</button>
            </div>
            <div class="artmap-shortcuts-grid">
                <div class="artmap-shortcut"><kbd>Esc</kbd><span>Close map</span></div>
                <div class="artmap-shortcut"><kbd>+</kbd> / <kbd>-</kbd><span>Zoom in / out</span></div>
                <div class="artmap-shortcut"><kbd>F</kbd><span>Fit to view</span></div>
                <div class="artmap-shortcut"><kbd>S</kbd><span>Focus search</span></div>
                <div class="artmap-shortcut"><kbd>H</kbd><span>Toggle similar artists</span></div>
                <div class="artmap-shortcut"><kbd>Scroll</kbd><span>Zoom at cursor</span></div>
                <div class="artmap-shortcut"><kbd>Click</kbd><span>Artist info</span></div>
                <div class="artmap-shortcut"><kbd>Right-click</kbd><span>Context menu</span></div>
                <div class="artmap-shortcut"><kbd>Drag</kbd><span>Pan around</span></div>
                <div class="artmap-shortcut"><kbd>Hover 1s</kbd><span>Show connections</span></div>
            </div>
        </div>
    `;
    document.body.appendChild(overlay);
}

async function openArtistMapGenre() {
    // Show picker immediately — uses lightweight genre list endpoint
    const genre = await _showGenrePickerModal();
    if (!genre) return;
    _openGenreMapWithSelection(genre);
}

async function _showGenrePickerModal() {
    return new Promise(resolve => {
        const existing = document.getElementById('artmap-genre-picker');
        if (existing) existing.remove();

        const overlay = document.createElement('div');
        overlay.id = 'artmap-genre-picker';
        overlay.className = 'modal-overlay';
        overlay.onclick = (e) => { if (e.target === overlay) { overlay.remove(); resolve(null); } };

        overlay.innerHTML = `
            <div class="artmap-genre-picker-modal">
                <div class="artmap-genre-picker-header">
                    <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5">
                        <circle cx="12" cy="12" r="10"/><path d="M12 2a15.3 15.3 0 0 1 4 10 15.3 15.3 0 0 1-4 10 15.3 15.3 0 0 1-4-10 15.3 15.3 0 0 1 4-10z"/><line x1="2" y1="12" x2="22" y2="12"/>
                    </svg>
                    <div>
                        <h3>Select a Genre</h3>
                        <p>Choose a genre to explore its artists</p>
                    </div>
                </div>
                <input type="text" class="artmap-genre-picker-search" placeholder="Filter genres..." oninput="_filterGenrePicker(this.value)">
                <div class="artmap-genre-picker-list" id="artmap-genre-picker-list">
                    <div class="cache-health-loading"><div class="watch-all-loading-spinner"></div><div>Loading genres...</div></div>
                </div>
            </div>
        `;
        document.body.appendChild(overlay);

        // Use cached data or fetch
        const renderGenreList = (data) => {
            if (!data?.success || !data?.genres?.length) {
                document.getElementById('artmap-genre-picker-list').innerHTML = '<div class="ya-info-empty">No genres found</div>';
                return;
            }
            const list = document.getElementById('artmap-genre-picker-list');
            list.innerHTML = data.genres.map(g => `
                <div class="artmap-genre-picker-item" data-genre="${escapeHtml(g.name)}" onclick="document.getElementById('artmap-genre-picker')._resolve('${escapeForInlineJs(g.name)}')">
                    <div class="artmap-genre-picker-name">${escapeHtml(g.name)}</div>
                    <div class="artmap-genre-picker-count">${g.count} artists</div>
                </div>
            `).join('');
        };

        if (window._artMapGenreList) {
            renderGenreList(window._artMapGenreList);
        } else {
            fetch('/api/discover/artist-map/genre-list')
                .then(r => r.json())
                .then(data => { window._artMapGenreList = data; renderGenreList(data); })
                .catch(() => { document.getElementById('artmap-genre-picker-list').innerHTML = '<div class="ya-info-empty">Error loading genres</div>'; });
        }

        overlay._resolve = (genre) => { overlay.remove(); resolve(genre); };
    });
}

function _switchGenre(genre) {
    _artMap._skipSectionToggle = true;
    _openGenreMapWithSelection(genre);
}

function _filterGenreSidebar(query) {
    const q = query.toLowerCase();
    document.querySelectorAll('.artmap-genre-sidebar-item').forEach(el => {
        el.style.display = el.dataset.genre.toLowerCase().includes(q) ? '' : 'none';
    });
}

async function _changeGenre() {
    const genre = await _showGenrePickerModal();
    if (!genre) return;
    _artMap._skipSectionToggle = true;
    _openGenreMapWithSelection(genre);
}

function _filterGenrePicker(query) {
    const q = query.toLowerCase();
    document.querySelectorAll('.artmap-genre-picker-item').forEach(el => {
        el.style.display = el.dataset.genre.toLowerCase().includes(q) ? '' : 'none';
    });
}

async function _openGenreMapWithSelection(selectedGenre) {
    const container = document.getElementById('artist-map-container');
    if (!container) return;

    const skipToggle = _artMap._skipSectionToggle;
    _artMap._skipSectionToggle = false;

    if (!skipToggle) {
        document.querySelectorAll('#discover-page > .discover-container > *:not(#artist-map-container)').forEach(el => {
            el._prevDisplay = el.style.display;
            el.style.display = 'none';
        });
    }
    container.style.display = 'flex';

    // Show + populate genre sidebar
    const sidebar = document.getElementById('artmap-genre-sidebar');
    const genreListData = window._artMapGenreList || window._artMapGenreData;
    if (sidebar && genreListData?.genres) {
        sidebar.style.display = 'flex';
        const list = document.getElementById('artmap-genre-sidebar-list');
        if (list) {
            list.innerHTML = genreListData.genres.map(g => `
                <div class="artmap-genre-sidebar-item ${g.name === selectedGenre ? 'active' : ''}"
                     data-genre="${escapeHtml(g.name)}"
                     onclick="_switchGenre('${escapeForInlineJs(g.name)}')">
                    <span class="artmap-genre-sidebar-name">${escapeHtml(g.name)}</span>
                    <span class="artmap-genre-sidebar-count">${g.count}</span>
                </div>
            `).join('');
        }
    }

    const canvas = document.getElementById('artist-map-canvas');
    const contentRow = canvas.parentElement;
    _artMap.canvas = canvas;
    _artMap.ctx = canvas.getContext('2d');
    _artMap.width = canvas.clientWidth || (container.clientWidth - (sidebar?.offsetWidth || 0));
    _artMap.height = contentRow?.clientHeight || (container.clientHeight - 50);
    canvas.width = _artMap.width * window.devicePixelRatio;
    canvas.height = _artMap.height * window.devicePixelRatio;
    canvas.style.width = _artMap.width + 'px';
    canvas.style.height = _artMap.height + 'px';
    _artMap.ctx.scale(window.devicePixelRatio, window.devicePixelRatio);
    _artMap.offsetX = _artMap.width / 2;
    _artMap.offsetY = _artMap.height / 2;
    _artMap.placed = [];
    _artMap.edges = [];
    _artMap.images = {};
    _artMap._nodeById = null;
    _artMap.dirty = true;

    // Show loading
    const loadingEl = document.createElement('div');
    loadingEl.id = 'artist-map-loading';
    loadingEl.innerHTML = `<div class="artist-map-loading-content"><div class="watch-all-loading-spinner"></div><div class="artist-map-loading-text" id="artmap-genre-loading-text">Loading genre map...</div></div>`;
    container.appendChild(loadingEl);

    // Update toolbar
    document.querySelector('.artmap-brand-text').textContent = 'Genre Map';
    document.getElementById('artist-map-stats').textContent = 'Loading...';

    try {
        // Use cached data from picker or fetch fresh
        const data = window._artMapGenreData || await fetch('/api/discover/artist-map/genres').then(r => r.json());
        const loadingText = document.getElementById('artmap-genre-loading-text');
        if (!data.success || !data.nodes.length) {
            if (loadingText) loadingText.textContent = 'No artists with genre data found.';
            return;
        }

        // Find the selected genre + closely related genres (high artist overlap)
        const allGenres = data.genres;
        const primary = allGenres.find(g => g.name === selectedGenre);
        if (!primary) {
            if (loadingText) loadingText.textContent = `Genre "${selectedGenre}" not found.`;
            return;
        }
        const primarySet = new Set(primary.artist_ids);

        // Find up to 4 related genres by artist overlap
        const related = allGenres
            .filter(g => g.name !== selectedGenre)
            .map(g => {
                const overlap = g.artist_ids.filter(id => primarySet.has(id)).length;
                return { ...g, overlap };
            })
            .filter(g => g.overlap > primarySet.size * 0.1) // At least 10% overlap
            .sort((a, b) => b.overlap - a.overlap)
            .slice(0, 4);

        const genres = [primary, ...related];
        const totalArtists = genres.reduce((sum, g) => sum + g.artist_ids.length, 0);

        document.getElementById('artist-map-stats').innerHTML =
            `<span class="artmap-genre-change" onclick="event.stopPropagation(); _changeGenre()" title="Change genre">${escapeHtml(selectedGenre)} ▾</span> · ${genres.length} genre${genres.length > 1 ? 's' : ''} · ${totalArtists} artists`;

        const WR = _artMap.WATCHLIST_R;
        const BUF = _artMap.BUFFER;

        const maxPerGenre = 500;
        const nodeR = WR * 0.2;

        // Calculate actual cluster radius for each genre based on ring count
        function getClusterRadius(artistCount) {
            const count = Math.min(artistCount, maxPerGenre);
            let ringDist = WR + nodeR * 2 + BUF;
            let placed = 0;
            while (placed < count) {
                const circ = 2 * Math.PI * ringDist;
                const inRing = Math.max(1, Math.floor(circ / (nodeR * 2 + BUF)));
                placed += Math.min(inRing, count - placed);
                ringDist += nodeR * 2 + BUF;
            }
            return ringDist;
        }

        // Pre-compute cluster radii
        genres.forEach(g => { g._clusterR = getClusterRadius(g.artist_ids.length); });

        // Golden spiral placement
        genres.forEach((g, i) => {
            if (i === 0) { g._cx = 0; g._cy = 0; }
            else {
                const angle = i * 2.399963;
                const r = g._clusterR * Math.sqrt(i) * 0.9;
                g._cx = Math.cos(angle) * r;
                g._cy = Math.sin(angle) * r;
            }
        });

        // Push apart using actual cluster radii — no overlap possible
        for (let pass = 0; pass < 80; pass++) {
            let moved = false;
            for (let i = 0; i < genres.length; i++) {
                for (let j = i + 1; j < genres.length; j++) {
                    const dx = genres[j]._cx - genres[i]._cx;
                    const dy = genres[j]._cy - genres[i]._cy;
                    const dist = Math.sqrt(dx * dx + dy * dy) || 1;
                    const minDist = genres[i]._clusterR + genres[j]._clusterR + BUF * 4;
                    if (dist < minDist) {
                        const push = (minDist - dist) / 2 + 1;
                        genres[i]._cx -= (dx / dist) * push; genres[i]._cy -= (dy / dist) * push;
                        genres[j]._cx += (dx / dist) * push; genres[j]._cy += (dy / dist) * push;
                        moved = true;
                    }
                }
            }
            if (!moved) break;
        }

        let placedCount = 0;

        // Place genre labels as big watchlist-style bubbles
        for (const g of genres) {
            _artMap.placed.push({
                id: `genre_${g.name}`, name: g.name.toUpperCase(),
                x: g._cx, y: g._cy, radius: WR, opacity: 1,
                type: 'genre_label', image_url: '', genres: [g.name],
                _isLabel: true, _count: g.count
            });
        }

        // Place artists in concentric rings — deterministic O(1) per node, handles 10K+ instantly
        let genreIdx = 0;

        async function placeGenreArtists() {
            for (; genreIdx < genres.length; genreIdx++) {
                const genre = genres[genreIdx];
                const artists = genre.artist_ids.slice(0, maxPerGenre);
                const sorted = artists.map(nid => data.nodes[nid]).filter(Boolean).sort((a, b) => (b.popularity || 0) - (a.popularity || 0));

                let ringDist = WR + nodeR * 2 + BUF;
                let ringNum = 0;
                let placed = 0;

                while (placed < sorted.length) {
                    const circumference = 2 * Math.PI * ringDist;
                    const nodesInRing = Math.max(1, Math.floor(circumference / (nodeR * 2 + BUF)));
                    const count = Math.min(nodesInRing, sorted.length - placed);
                    const angleStep = (2 * Math.PI) / nodesInRing;
                    const angleOffset = ringNum * 0.618;

                    for (let i = 0; i < count; i++) {
                        const n = sorted[placed + i];
                        if (!n) continue;
                        const isW = n.type === 'watchlist' || n.type === 'center';
                        const r = isW ? nodeR * 1.5 : nodeR;
                        const angle = angleOffset + i * angleStep;

                        _artMap.placed.push({
                            id: placedCount + 1000, _origId: n.id, name: n.name,
                            x: genre._cx + Math.cos(angle) * ringDist,
                            y: genre._cy + Math.sin(angle) * ringDist,
                            radius: r, opacity: 1,
                            type: isW ? 'watchlist' : 'similar',
                            image_url: n.image_url || '', genres: n.genres || [],
                            spotify_id: n.spotify_id || '', itunes_id: n.itunes_id || '',
                            deezer_id: n.deezer_id || '', discogs_id: n.discogs_id || '',
                        });
                        placedCount++;
                    }
                    placed += count;
                    ringDist += nodeR * 2 + BUF;
                    ringNum++;
                }

                if (loadingText) loadingText.textContent = `Placing artists... ${genreIdx + 1}/${genres.length} genres (${placedCount} placed)`;
                if (genreIdx % 5 === 0) await new Promise(r => setTimeout(r, 0));
            }
        }
        await placeGenreArtists();

        // Build edges: connect artists that appear in multiple genre clusters
        _artMap.edges = [];
        const artistNodes = {};
        _artMap.placed.forEach(n => {
            if (n._origId != null) {
                if (!artistNodes[n._origId]) artistNodes[n._origId] = [];
                artistNodes[n._origId].push(n.id);
            }
        });
        Object.values(artistNodes).forEach(ids => {
            if (ids.length > 1) {
                for (let i = 0; i < ids.length - 1; i++) {
                    _artMap.edges.push({ source: ids[i], target: ids[i + 1], weight: 5 });
                }
            }
        });

        _artMap._nodeById = {};
        _artMap.placed.forEach(n => { _artMap._nodeById[n.id] = n; });

        // Auto-zoom
        let minX = Infinity, maxX = -Infinity, minY = Infinity, maxY = -Infinity;
        _artMap.placed.forEach(n => {
            minX = Math.min(minX, n.x - n.radius);
            maxX = Math.max(maxX, n.x + n.radius);
            minY = Math.min(minY, n.y - n.radius);
            maxY = Math.max(maxY, n.y + n.radius);
        });
        const mapW = maxX - minX + 200, mapH = maxY - minY + 200;
        _artMap.zoom = Math.min(_artMap.width / mapW, _artMap.height / mapH, 1);
        _artMap.offsetX = _artMap.width / 2 - ((minX + maxX) / 2) * _artMap.zoom;
        _artMap.offsetY = _artMap.height / 2 - ((minY + maxY) / 2) * _artMap.zoom;

        _artMapSetupInteraction(canvas);

        // Load images + render
        if (loadingText) loadingText.textContent = `Rendering ${placedCount} artists...`;

        setTimeout(async () => {
            const imgNodes = _artMap.placed.filter(n => n.image_url && !n._isLabel);
            let loaded = 0;
            const CONCURRENT = 20;
            let idx = 0;
            async function loadBatch() {
                const batch = [];
                while (idx < imgNodes.length && batch.length < CONCURRENT) {
                    const n = imgNodes[idx++];
                    batch.push(_artMapLoadImage(n.image_url)
                        .then(bmp => { if (bmp) _artMap.images[n.id] = bmp; })
                        .finally(() => { loaded++; }));
                }
                if (batch.length) await Promise.all(batch);
                if (idx < imgNodes.length) return loadBatch();
            }
            await loadBatch();
            _artMap.dirty = true;
            _artMapRender();
            const le = document.getElementById('artist-map-loading');
            if (le) le.remove();
        }, 50);

        _artMap.dirty = true;
        _artMapRender();

    } catch (err) {
        console.error('Genre map error:', err);
        const lt = container.querySelector('.artist-map-loading-text');
        if (lt) lt.textContent = 'Error loading genre map';
    }
}

function openArtistMapExplorerDirect(name) {
    if (!name) return;
    // Already in map — just reload with new data, don't re-hide sections
    _artMap._skipSectionToggle = true;
    _openArtistMapExplorerWithName(name);
}

async function openArtistMapExplorer() {
    const name = await _showArtistMapSearchPrompt();
    if (!name) return;
    _openArtistMapExplorerWithName(name);
}

async function _openArtistMapExplorerWithName(name) {

    const container = document.getElementById('artist-map-container');
    if (!container) return;

    const skipToggle = _artMap._skipSectionToggle;
    _artMap._skipSectionToggle = false;

    if (!skipToggle) {
        document.querySelectorAll('#discover-page > .discover-container > *:not(#artist-map-container)').forEach(el => {
            el._prevDisplay = el.style.display;
            el.style.display = 'none';
        });
    }
    container.style.display = 'flex';

    const canvas = document.getElementById('artist-map-canvas');
    _artMap.canvas = canvas;
    _artMap.ctx = canvas.getContext('2d');
    _artMap.width = container.clientWidth;
    _artMap.height = container.clientHeight - 50;
    canvas.width = _artMap.width * window.devicePixelRatio;
    canvas.height = _artMap.height * window.devicePixelRatio;
    canvas.style.width = _artMap.width + 'px';
    canvas.style.height = _artMap.height + 'px';
    _artMap.ctx.scale(window.devicePixelRatio, window.devicePixelRatio);
    _artMap.offsetX = _artMap.width / 2;
    _artMap.offsetY = _artMap.height / 2;
    _artMap.placed = [];
    _artMap.edges = [];
    _artMap.images = {};
    _artMap._nodeById = null;
    _artMap.dirty = true;

    const loadingEl = document.createElement('div');
    loadingEl.id = 'artist-map-loading';
    loadingEl.innerHTML = `<div class="artist-map-loading-content"><div class="watch-all-loading-spinner"></div><div class="artist-map-loading-text">Exploring ${escapeHtml(name)}...</div></div>`;
    container.appendChild(loadingEl);

    document.querySelector('.artmap-brand-text').textContent = 'Artist Explorer';

    try {
        const resp = await fetch(`/api/discover/artist-map/explore?name=${encodeURIComponent(name.trim())}`);
        const data = await resp.json();
        if (!data.success || !data.nodes.length) {
            const lt = document.querySelector('.artist-map-loading-text');
            if (lt) {
                lt.textContent = resp.status === 404
                    ? `"${name}" doesn't appear to be a real artist. Try a different name.`
                    : `No data found for "${name}". Try a different artist.`;
            }
            setTimeout(() => {
                const le = document.getElementById('artist-map-loading');
                if (le) le.remove();
                closeArtistMap();
            }, 2500);
            return;
        }

        const ring1Count = data.nodes.filter(n => n.ring === 1).length;
        const ring2Count = data.nodes.filter(n => n.ring === 2).length;
        document.getElementById('artist-map-stats').textContent =
            `${data.center} · ${ring1Count} similar · ${ring2Count} extended`;

        _artMap.edges = data.edges;
        const WR = _artMap.WATCHLIST_R;
        const BUF = _artMap.BUFFER;

        // Layout: center node at origin, ring 1 in circle around it, ring 2 around ring 1
        const centerNode = data.nodes[0];
        centerNode.x = 0; centerNode.y = 0;
        centerNode.radius = WR * 1.2; // Extra large center
        centerNode.opacity = 1;
        centerNode.type = 'center';
        _artMap.placed.push(centerNode);

        const CELL = WR * 2 + BUF * 2;
        const grid = {};
        function _gk(x, y) { return `${Math.floor(x / CELL)},${Math.floor(y / CELL)}`; }
        function _ga(n) { const k = _gk(n.x, n.y); if (!grid[k]) grid[k] = []; grid[k].push(n); }
        function _gc(x, y, r) {
            const cx = Math.floor(x / CELL), cy = Math.floor(y / CELL);
            for (let dx = -3; dx <= 3; dx++) for (let dy = -3; dy <= 3; dy++) {
                const cell = grid[`${cx + dx},${cy + dy}`];
                if (!cell) continue;
                for (const p of cell) {
                    const ddx = x - p.x, ddy = y - p.y;
                    if (ddx * ddx + ddy * ddy < (r + p.radius + BUF) * (r + p.radius + BUF)) return true;
                }
            }
            return false;
        }
        _ga(centerNode);

        // Place ring 1 in a circle
        const ring1 = data.nodes.filter(n => n.ring === 1);
        const ring1Dist = WR * 2.5;
        ring1.forEach((n, i) => {
            const angle = (i / ring1.length) * Math.PI * 2;
            const rank = n.rank || 5;
            n.radius = WR * 0.4 + (10 - rank) * WR * 0.03;
            n.opacity = 1;

            // Find non-colliding position near ideal
            let placed = false;
            for (let dist = ring1Dist; dist < ring1Dist + WR * 3; dist += n.radius * 0.5) {
                for (let ao = 0; ao < 6; ao++) {
                    const a = angle + (ao * 0.1 * (ao % 2 ? 1 : -1));
                    const tx = Math.cos(a) * dist;
                    const ty = Math.sin(a) * dist;
                    if (!_gc(tx, ty, n.radius)) {
                        n.x = tx; n.y = ty;
                        _artMap.placed.push(n);
                        _ga(n);
                        placed = true;
                        break;
                    }
                }
                if (placed) break;
            }
        });

        // Place ring 2 around their ring 1 sources
        const ring2 = data.nodes.filter(n => n.ring === 2);
        const nodeById = {};
        _artMap.placed.forEach(n => { nodeById[n.id] = n; });

        ring2.forEach(n => {
            // Find the ring 1 node that connects to this
            const edge = data.edges.find(e => e.target === n.id);
            const src = edge ? nodeById[edge.source] : null;
            const cx = src ? src.x : 0;
            const cy = src ? src.y : 0;

            n.radius = WR * 0.2 + (n.popularity || 0) / 100 * WR * 0.1;
            n.opacity = 1;

            const startDist = (src ? src.radius : WR) + n.radius + BUF;
            let placed = false;
            for (let dist = startDist; dist < startDist + WR * 2; dist += n.radius * 0.5) {
                const steps = Math.max(8, Math.floor(dist * 0.08));
                const off = Math.random() * Math.PI * 2;
                for (let a = 0; a < steps; a++) {
                    const angle = off + (a / steps) * Math.PI * 2;
                    const tx = cx + Math.cos(angle) * dist;
                    const ty = cy + Math.sin(angle) * dist;
                    if (!_gc(tx, ty, n.radius)) {
                        n.x = tx; n.y = ty;
                        _artMap.placed.push(n);
                        _ga(n);
                        placed = true;
                        break;
                    }
                }
                if (placed) break;
            }
        });

        // Build node lookup for edges
        _artMap._nodeById = {};
        _artMap.placed.forEach(n => { _artMap._nodeById[n.id] = n; });

        // Auto-zoom
        let minX = Infinity, maxX = -Infinity, minY = Infinity, maxY = -Infinity;
        _artMap.placed.forEach(n => {
            minX = Math.min(minX, n.x - n.radius);
            maxX = Math.max(maxX, n.x + n.radius);
            minY = Math.min(minY, n.y - n.radius);
            maxY = Math.max(maxY, n.y + n.radius);
        });
        const mapW = maxX - minX + 200, mapH = maxY - minY + 200;
        _artMap.zoom = Math.min(_artMap.width / mapW, _artMap.height / mapH, 1);
        _artMap.offsetX = _artMap.width / 2 - ((minX + maxX) / 2) * _artMap.zoom;
        _artMap.offsetY = _artMap.height / 2 - ((minY + maxY) / 2) * _artMap.zoom;

        _artMapSetupInteraction(canvas);

        // Load images
        const loadingText = container.querySelector('.artist-map-loading-text');
        if (loadingText) loadingText.textContent = `Loading ${_artMap.placed.length} artists...`;

        setTimeout(async () => {
            const imgNodes = _artMap.placed.filter(n => n.image_url);
            let loaded = 0;
            const CONCURRENT = 20;
            let idx = 0;
            async function loadBatch() {
                const batch = [];
                while (idx < imgNodes.length && batch.length < CONCURRENT) {
                    const n = imgNodes[idx++];
                    batch.push(_artMapLoadImage(n.image_url)
                        .then(bmp => { if (bmp) _artMap.images[n.id] = bmp; })
                        .finally(() => { loaded++; }));
                }
                if (batch.length) await Promise.all(batch);
                if (idx < imgNodes.length) return loadBatch();
            }
            await loadBatch();
            _artMap.dirty = true;
            _artMapRender();
            const le = document.getElementById('artist-map-loading');
            if (le) le.remove();
        }, 50);

        _artMap.dirty = true;
        _artMapRender();

    } catch (err) {
        console.error('Artist explorer error:', err);
        const lt = container.querySelector('.artist-map-loading-text');
        if (lt) lt.textContent = 'Error loading explorer';
    }
}

function _showArtistMapSearchPrompt() {
    return new Promise(resolve => {
        const existing = document.getElementById('artmap-search-prompt');
        if (existing) existing.remove();

        const overlay = document.createElement('div');
        overlay.id = 'artmap-search-prompt';
        overlay.className = 'modal-overlay';
        overlay.onclick = (e) => { if (e.target === overlay) { overlay.remove(); resolve(null); } };

        overlay.innerHTML = `
            <div class="artmap-search-prompt-modal">
                <div class="artmap-search-prompt-header">
                    <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5">
                        <circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/>
                        <line x1="11" y1="8" x2="11" y2="14"/><line x1="8" y1="11" x2="14" y2="11"/>
                    </svg>
                    <div>
                        <h3>Artist Explorer</h3>
                        <p>Enter an artist to explore their connections</p>
                    </div>
                </div>
                <input type="text" id="artmap-explore-input" class="artmap-explore-input" placeholder="Artist name..." autofocus>
                <div class="artmap-search-prompt-actions">
                    <button class="ya-header-btn" onclick="document.getElementById('artmap-search-prompt').remove()">Cancel</button>
                    <button class="ya-header-btn ya-viewall-btn" id="artmap-explore-go">
                        <span>Explore</span>
                        <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><path d="M5 12h14"/><path d="M12 5l7 7-7 7"/></svg>
                    </button>
                </div>
            </div>
        `;
        document.body.appendChild(overlay);

        const input = overlay.querySelector('#artmap-explore-input');
        const goBtn = overlay.querySelector('#artmap-explore-go');

        const submit = () => {
            const val = input.value.trim();
            overlay.remove();
            resolve(val || null);
        };

        goBtn.onclick = submit;
        input.addEventListener('keydown', (e) => { if (e.key === 'Enter') submit(); });
        setTimeout(() => input.focus(), 50);
    });
}

function artMapToggleSimilar() {
    _artMap._hideSimilar = !_artMap._hideSimilar;
    _artMap.dirty = true;
    _artMapRender();
    const btn = document.getElementById('artmap-toggle-similar');
    if (btn) btn.style.opacity = _artMap._hideSimilar ? '0.4' : '1';
    showToast(_artMap._hideSimilar ? 'Showing watchlist only' : 'Showing all artists', 'info', 1500);
}

function _artMapLoadImage(url) {
    // Try direct CORS fetch first (zero server load, works for Spotify/iTunes/Discogs)
    return fetch(url, { mode: 'cors' })
        .then(r => r.ok ? r.blob() : Promise.reject('not ok'))
        .then(b => createImageBitmap(b))
        .catch(() => {
            // Fallback: server proxy for CDNs without CORS headers
            return fetch('/api/image-proxy?url=' + encodeURIComponent(url))
                .then(r => r.ok ? r.blob() : null)
                .then(b => b ? createImageBitmap(b) : null)
                .catch(() => null);
        });
}

function _artMapHideContextMenu() {
    const m = document.getElementById('artist-map-context');
    if (m) m.style.display = 'none';
}

function _artMapSetupInteraction(canvas) {
    // Prevent stacking listeners on repeated opens
    if (canvas._artMapListenersAttached) return;
    canvas._artMapListenersAttached = true;

    let isPanning = false, panStartX = 0, panStartY = 0;

    canvas.addEventListener('wheel', (e) => {
        e.preventDefault();
        const delta = e.deltaY > 0 ? 0.9 : 1.1;
        const newZoom = Math.max(0.02, Math.min(5, _artMap.zoom * delta));
        // Zoom toward mouse
        const rect = canvas.getBoundingClientRect();
        const mx = e.clientX - rect.left;
        const my = e.clientY - rect.top;
        _artMap.offsetX = mx - (mx - _artMap.offsetX) * (newZoom / _artMap.zoom);
        _artMap.offsetY = my - (my - _artMap.offsetY) * (newZoom / _artMap.zoom);
        _artMap.zoom = newZoom;
        _artMapRender(); // fast blit
        // Debounce hi-res rebuild after zoom settles
        clearTimeout(_artMap._zoomRebuild);
        _artMap._zoomRebuild = setTimeout(() => { _artMap.dirty = true; _artMapRender(); }, 300);
    }, { passive: false });

    let clickStart = null;

    // Keyboard shortcuts
    function _artMapKeyHandler(e) {
        if (!document.getElementById('artist-map-container') || document.getElementById('artist-map-container').style.display === 'none') return;
        if (e.target.tagName === 'INPUT') return; // don't intercept search typing
        if (e.key === 'Escape') { closeArtistMap(); e.preventDefault(); }
        else if (e.key === '=' || e.key === '+') { artMapZoom(1.3); e.preventDefault(); }
        else if (e.key === '-') { artMapZoom(0.7); e.preventDefault(); }
        else if (e.key === '0') { artMapFitToView(); e.preventDefault(); }
        else if (e.key === 'f' || e.key === 'F') { artMapFitToView(); e.preventDefault(); }
        else if (e.key === 's' || e.key === 'S') {
            const input = document.getElementById('artist-map-search');
            if (input) { input.focus(); e.preventDefault(); }
        }
        else if (e.key === 'h' || e.key === 'H') {
            // Toggle similar artists visibility
            _artMap._hideSimilar = !_artMap._hideSimilar;
            _artMap.dirty = true;
            _artMapRender();
        }
    }
    window.addEventListener('keydown', _artMapKeyHandler);
    _artMap._keyHandler = _artMapKeyHandler;

    // Right-click context menu
    canvas.addEventListener('contextmenu', (e) => {
        e.preventDefault();
        const { nx, ny } = _artMapScreenToWorld(e, canvas);
        const node = _artMapHitTest(nx, ny);
        if (!node || node._isLabel) { _artMapHideContextMenu(); return; }

        const menu = document.getElementById('artist-map-context') || (() => {
            const m = document.createElement('div');
            m.id = 'artist-map-context';
            m.className = 'artmap-context-menu';
            document.getElementById('artist-map-container').appendChild(m);
            return m;
        })();

        const hasId = node.spotify_id || node.itunes_id || node.deezer_id;
        const activeSource = window._yaActiveSource || 'spotify';
        const bestId = node[activeSource + '_id'] || node.spotify_id || node.itunes_id || node.deezer_id || '';
        const bestSource = node[activeSource + '_id'] ? activeSource : node.spotify_id ? 'spotify' : node.itunes_id ? 'itunes' : 'deezer';

        menu.innerHTML = `
            <div class="artmap-ctx-item" onclick="_artMapHideContextMenu(); ${hasId ? `openYourArtistInfoModal_direct(${JSON.stringify(node).replace(/"/g, '&quot;')})` : ''}">
                <span>&#9432;</span> Artist Info
            </div>
            <div class="artmap-ctx-item" onclick="_artMapHideContextMenu(); navigateToArtistDetail('${escapeForInlineJs(bestId)}', '${escapeForInlineJs(node.name)}', '${bestSource}' || null)">
                <span>&#128191;</span> View Discography
            </div>
            <div class="artmap-ctx-item" onclick="_artMapHideContextMenu(); toggleYourArtistWatchlist(0,'${escapeForInlineJs(node.name)}','${escapeForInlineJs(bestId)}','${bestSource}',null)">
                <span>&#128065;</span> ${node.type === 'watchlist' ? 'On Watchlist' : 'Add to Watchlist'}
            </div>
        `;
        menu.style.display = 'block';
        menu.style.left = Math.min(e.clientX, window.innerWidth - 200) + 'px';
        menu.style.top = Math.min(e.clientY, window.innerHeight - 200) + 'px';

        // Close on next click anywhere
        setTimeout(() => {
            window.addEventListener('click', _artMapHideContextMenu, { once: true });
        }, 10);
    });

    canvas.addEventListener('mousedown', (e) => {
        if (e.button !== 0) return; // left button only
        clickStart = { x: e.clientX, y: e.clientY, time: Date.now() };
        isPanning = true;
        panStartX = e.clientX;
        panStartY = e.clientY;
    });

    canvas.addEventListener('mousemove', (e) => {
        if (isPanning) {
            _artMap.offsetX += e.clientX - panStartX;
            _artMap.offsetY += e.clientY - panStartY;
            panStartX = e.clientX;
            panStartY = e.clientY;
            _artMapRender();
        } else {
            const { nx, ny } = _artMapScreenToWorld(e, canvas);
            const prev = _artMap.hoveredNode;
            _artMap.hoveredNode = _artMapHitTest(nx, ny);
            canvas.style.cursor = _artMap.hoveredNode ? 'pointer' : 'grab';
            _artMapShowTooltip(e, _artMap.hoveredNode);
            if (prev !== _artMap.hoveredNode) {
                // Reset constellation highlight timer
                clearTimeout(_artMap._constellationTimer);
                if (_artMap._constellationActive) {
                    _artMap._constellationActive = false;
                    _artMapAnimateConstellation(); // fade out
                }
                if (_artMap.hoveredNode) {
                    // Delay constellation effect by 800ms of sustained hover
                    _artMap._constellationTimer = setTimeout(() => {
                        if (_artMap.hoveredNode) {
                            _artMap._constellationActive = true;
                            _artMap._constellationFade = 0;
                            _artMap._constellationCache = null;
                            _artMapAnimateConstellation();
                        }
                    }, 800);
                }
                _artMapRender();
            }
        }
    });

    canvas.addEventListener('mouseup', (e) => {
        if (e.button !== 0) return; // left button only
        const wasDrag = clickStart && (Math.abs(e.clientX - clickStart.x) > 5 || Math.abs(e.clientY - clickStart.y) > 5);
        isPanning = false;

        if (!wasDrag && clickStart) {
            // It was a click — find the node under cursor
            const { nx, ny } = _artMapScreenToWorld(e, canvas);
            const node = _artMapHitTest(nx, ny);
            if (node) {
                _artMap._ripple = { x: node.x, y: node.y, radius: node.radius, start: performance.now() };
                _artMapRender();
                if (node.spotify_id || node.itunes_id || node.deezer_id) {
                    setTimeout(() => openYourArtistInfoModal_direct(node), 200);
                }
            }
        }

        clickStart = null;
        _artMapShowTooltip(e, null);
    });

    canvas.addEventListener('mouseleave', () => {
        _artMapShowTooltip(null, null);
        clearTimeout(_artMap._constellationTimer);
        if (_artMap._constellationActive) {
            _artMap._constellationActive = false;
            _artMapAnimateConstellation();
        }
        _artMap.hoveredNode = null;
        _artMapRender();
    });

    // Touch support — single finger pan, pinch to zoom
    let lastTouches = null;
    canvas.addEventListener('touchstart', (e) => {
        e.preventDefault();
        lastTouches = [...e.touches];
    }, { passive: false });

    canvas.addEventListener('touchmove', (e) => {
        e.preventDefault();
        if (!lastTouches) return;
        const touches = [...e.touches];

        if (touches.length === 1 && lastTouches.length === 1) {
            // Pan
            _artMap.offsetX += touches[0].clientX - lastTouches[0].clientX;
            _artMap.offsetY += touches[0].clientY - lastTouches[0].clientY;
            _artMapRender();
        } else if (touches.length === 2 && lastTouches.length === 2) {
            // Pinch zoom
            const prevDist = Math.hypot(lastTouches[1].clientX - lastTouches[0].clientX, lastTouches[1].clientY - lastTouches[0].clientY);
            const curDist = Math.hypot(touches[1].clientX - touches[0].clientX, touches[1].clientY - touches[0].clientY);
            const factor = curDist / prevDist;
            const cx = (touches[0].clientX + touches[1].clientX) / 2;
            const cy = (touches[0].clientY + touches[1].clientY) / 2;
            const newZoom = Math.max(0.02, Math.min(3, _artMap.zoom * factor));
            _artMap.offsetX = cx - (cx - _artMap.offsetX) * (newZoom / _artMap.zoom);
            _artMap.offsetY = cy - (cy - _artMap.offsetY) * (newZoom / _artMap.zoom);
            _artMap.zoom = newZoom;
            _artMap.dirty = true;
            _artMapRender();
        }
        lastTouches = touches;
    }, { passive: false });

    canvas.addEventListener('touchend', (e) => {
        e.preventDefault();
        // Tap to click
        if (lastTouches && lastTouches.length === 1 && e.changedTouches.length === 1) {
            const t = e.changedTouches[0];
            const rect = canvas.getBoundingClientRect();
            const wx = (t.clientX - rect.left - _artMap.offsetX) / _artMap.zoom;
            const wy = (t.clientY - rect.top - _artMap.offsetY) / _artMap.zoom;
            const node = _artMapHitTest(wx, wy);
            if (node && (node.spotify_id || node.itunes_id || node.deezer_id)) {
                openYourArtistInfoModal_direct(node);
            }
        }
        lastTouches = null;
    }, { passive: false });

    // Handle resize
    window.addEventListener('resize', () => {
        const container = document.getElementById('artist-map-container');
        if (!container || container.style.display === 'none') return;
        _artMap.width = container.clientWidth;
        _artMap.height = container.clientHeight - 50;
        canvas.width = _artMap.width * window.devicePixelRatio;
        canvas.height = _artMap.height * window.devicePixelRatio;
        canvas.style.width = _artMap.width + 'px';
        canvas.style.height = _artMap.height + 'px';
        _artMap.ctx.scale(window.devicePixelRatio, window.devicePixelRatio);
    });
}

function _artMapScreenToWorld(e, canvas) {
    const rect = canvas.getBoundingClientRect();
    const sx = e.clientX - rect.left;
    const sy = e.clientY - rect.top;
    // Inverse of: translate(offsetX, offsetY) → scale(zoom)
    return {
        nx: (sx - _artMap.offsetX) / _artMap.zoom,
        ny: (sy - _artMap.offsetY) / _artMap.zoom,
    };
}

function _artMapHitTest(wx, wy) {
    // Check watchlist first (drawn on top), then similar
    const sorted = [..._artMap.placed].sort((a, b) =>
        (b.type === 'watchlist' ? 1 : 0) - (a.type === 'watchlist' ? 1 : 0));
    for (const n of sorted) {
        if ((n.opacity || 0) < 0.3) continue;
        const dx = wx - n.x;
        const dy = wy - n.y;
        if (dx * dx + dy * dy <= n.radius * n.radius) return n;
    }
    return null;
}

async function openYourArtistInfoModal_direct(node) {
    // Determine best source ID — prefer active metadata source
    let bestId = '', bestSource = '';
    // Check what the active source is
    const activeSource = window._yaActiveSource || 'spotify';
    const sourceOrder = activeSource === 'spotify' ? ['spotify_id', 'itunes_id', 'deezer_id', 'discogs_id']
        : activeSource === 'itunes' ? ['itunes_id', 'spotify_id', 'deezer_id', 'discogs_id']
            : activeSource === 'deezer' ? ['deezer_id', 'spotify_id', 'itunes_id', 'discogs_id']
                : ['spotify_id', 'itunes_id', 'deezer_id', 'discogs_id'];
    const sourceMap = { spotify_id: 'spotify', itunes_id: 'itunes', deezer_id: 'deezer', discogs_id: 'discogs' };
    for (const key of sourceOrder) {
        if (node[key]) { bestId = node[key]; bestSource = sourceMap[key]; break; }
    }

    // Gather ALL connected artists from map edges (both directions)
    const related = [];
    const relatedIds = new Set();
    const nById = _artMap._nodeById || {};
    _artMap.edges.forEach(e => {
        if (e.source === node.id && nById[e.target] && !relatedIds.has(e.target)) {
            related.push(nById[e.target]);
            relatedIds.add(e.target);
        }
        if (e.target === node.id && nById[e.source] && !relatedIds.has(e.source)) {
            related.push(nById[e.source]);
            relatedIds.add(e.source);
        }
    });

    const poolEntry = {
        id: node.id,
        artist_name: node.name,
        active_source_id: bestId,
        active_source: bestSource,
        image_url: node.image_url || '',
        spotify_artist_id: node.spotify_id || '',
        itunes_artist_id: node.itunes_id || '',
        deezer_artist_id: node.deezer_id || '',
        discogs_artist_id: node.discogs_id || '',
        source_services: [],
        on_watchlist: node.type === 'watchlist' ? 1 : 0,
        _related: related,
    };
    if (!window._yaArtists) window._yaArtists = {};
    window._yaArtists[node.id] = poolEntry;
    openYourArtistInfoModal(node.id);
}

async function loadDiscoveryShuffle() {
    try {
        const container = document.getElementById('personalized-discovery-shuffle');
        if (!container) return;

        const response = await fetch('/api/discover/personalized/discovery-shuffle?limit=50');
        if (!response.ok) return;

        const data = await response.json();
        if (!data.success || !data.tracks || data.tracks.length === 0) {
            container.closest('.discover-section').style.display = 'none';
            return;
        }

        personalizedDiscoveryShuffle = data.tracks;
        renderCompactPlaylist(container, data.tracks);
        container.closest('.discover-section').style.display = 'block';

    } catch (error) {
        console.error('Error loading discovery shuffle:', error);
    }
}

async function loadFamiliarFavorites() {
    try {
        const container = document.getElementById('personalized-familiar-favorites');
        if (!container) return;

        const response = await fetch('/api/discover/personalized/familiar-favorites?limit=50');
        if (!response.ok) return;

        const data = await response.json();
        if (!data.success || !data.tracks || data.tracks.length === 0) {
            container.closest('.discover-section').style.display = 'none';
            return;
        }

        personalizedFamiliarFavorites = data.tracks;
        renderCompactPlaylist(container, data.tracks);
        container.closest('.discover-section').style.display = 'block';

    } catch (error) {
        console.error('Error loading familiar favorites:', error);
    }
}

// ===============================
// BECAUSE YOU LISTEN TO
// ===============================

async function loadBecauseYouListenTo() {
    try {
        const resp = await fetch('/api/discover/because-you-listen-to');
        if (!resp.ok) return;
        const data = await resp.json();
        if (!data.success || !data.sections || data.sections.length === 0) return;

        // Find or create the BYLT container
        let byltContainer = document.getElementById('discover-bylt-sections');
        if (!byltContainer) {
            // Insert after the release radar section
            const releaseRadar = document.getElementById('discover-release-radar');
            if (!releaseRadar) return;
            const parent = releaseRadar.closest('.discover-section');
            if (!parent) return;

            byltContainer = document.createElement('div');
            byltContainer.id = 'discover-bylt-sections';
            parent.parentNode.insertBefore(byltContainer, parent.nextSibling);
        }

        byltContainer.innerHTML = data.sections.map((section, idx) => `
            <div class="discover-section bylt-section">
                <div class="discover-section-header">
                    <div class="bylt-header">
                        ${section.artist_image ? `<img class="bylt-artist-img" src="${section.artist_image}" alt="" onerror="this.style.display='none'">` : ''}
                        <div>
                            <div class="discover-section-subtitle">Because you listen to</div>
                            <h3 class="discover-section-title">${_esc(section.artist_name)}</h3>
                        </div>
                    </div>
                </div>
                <div class="discover-carousel" id="bylt-carousel-${idx}"></div>
            </div>
        `).join('');

        // Render track cards in each carousel
        data.sections.forEach((section, idx) => {
            const carousel = document.getElementById(`bylt-carousel-${idx}`);
            if (!carousel) return;
            carousel.innerHTML = section.tracks.map(t => `
                <div class="discover-card">
                    <div class="discover-card-image">
                        ${t.image_url ? `<img src="${t.image_url}" alt="" loading="lazy" onerror="this.src='/static/placeholder.png'">` : '<div class="discover-card-placeholder">🎵</div>'}
                    </div>
                    <div class="discover-card-title">${_esc(t.name)}</div>
                    <div class="discover-card-artist">${_esc(t.artist)}</div>
                </div>
            `).join('');
        });

    } catch (error) {
        console.debug('Error loading Because You Listen To:', error);
    }
}

// ===============================
// CACHE DISCOVERY SECTIONS
// ===============================

// Global arrays for cache discovery click handlers
let _cacheDiscoverData = {};

function _cacheDiscoverCard(item, type, sectionKey, index) {
    const _esc = (s) => (s || '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
    const coverUrl = item.image_url || '/static/placeholder-album.png';
    const title = item.name || '';
    const subtitle = item.artist_name || '';
    const meta = item.release_date ? item.release_date.substring(0, 10) : (item.label || '');
    const onclick = `openCacheDiscoverAlbum('${sectionKey}',${index})`;
    const libBadge = item.in_library ? '<div class="discover-card-lib-badge">In Library</div>' : '';
    return `<div class="discover-card" onclick="${onclick}" style="cursor:pointer">
        <div class="discover-card-image">
            <img src="${_esc(coverUrl)}" alt="${_esc(title)}" loading="lazy" onerror="this.src='/static/placeholder-album.png'">
            ${libBadge}
        </div>
        <div class="discover-card-info">
            <h4 class="discover-card-title">${_esc(title)}</h4>
            <p class="discover-card-subtitle">${_esc(subtitle)}</p>
            ${meta ? `<p class="discover-card-meta">${_esc(meta)}</p>` : ''}
        </div>
    </div>`;
}

async function openCacheDiscoverAlbum(sectionKey, index) {
    const items = _cacheDiscoverData[sectionKey];
    if (!items || !items[index]) return;
    const item = items[index];
    const source = item.source || 'spotify';
    const albumId = item.entity_id;

    // Deep cuts / genre dive tracks — find the real album by searching the cache
    if (sectionKey === 'deep_cuts' || sectionKey === 'genre_dive_tracks') {
        document.getElementById('genre-deep-dive-modal')?.remove();
        const albumName = item.album_name || item.name || '';
        const artistName = item.artist_name || '';
        const trackAlbumId = item.album_id || '';
        const trackSource = item.source || source;

        if (!artistName) {
            showToast('No artist data available for this track', 'error');
            return;
        }

        showLoadingOverlay(`Loading ${albumName}...`);
        try {
            let resolvedSource = trackSource;
            let resolvedId = trackAlbumId;
            let response;

            // If we have an album_id, use it directly
            if (trackAlbumId) {
                const _params = new URLSearchParams({ name: albumName, artist: artistName });
                response = await fetch(`/api/discover/album/${trackSource}/${trackAlbumId}?${_params}`);
            }

            // Fallback: resolve by name+artist if no album_id or direct fetch failed
            if (!trackAlbumId || (response && !response.ok)) {
                const searchResp = await fetch(`/api/discover/resolve-cache-album?name=${encodeURIComponent(albumName)}&artist=${encodeURIComponent(artistName)}`);
                if (searchResp.ok) {
                    const searchData = await searchResp.json();
                    if (searchData.success && searchData.entity_id) {
                        resolvedSource = searchData.source || trackSource;
                        resolvedId = searchData.entity_id;
                        const _params = new URLSearchParams({ name: albumName, artist: artistName });
                        response = await fetch(`/api/discover/album/${resolvedSource}/${resolvedId}?${_params}`);
                    }
                }
            }

            if (!response || !response.ok) throw new Error('Failed to fetch album tracks');
            const albumData = await response.json();
            if (!albumData.tracks || albumData.tracks.length === 0) throw new Error('No tracks found');

            const spotifyTracks = albumData.tracks.map(track => {
                let artists = track.artists || albumData.artists || [{ name: artistName }];
                if (Array.isArray(artists)) artists = artists.map(a => a.name || a);
                return {
                    id: track.id, name: track.name, artists,
                    album: { id: albumData.id, name: albumData.name, album_type: albumData.album_type || 'album', total_tracks: albumData.total_tracks || 0, release_date: albumData.release_date || '', images: albumData.images || [] },
                    duration_ms: track.duration_ms || 0, track_number: track.track_number || 0,
                };
            });
            const artistContext = { id: albumData.artists?.[0]?.id || '', name: artistName, source: resolvedSource };
            const albumContext = { id: albumData.id, name: albumData.name, album_type: albumData.album_type || 'album', total_tracks: albumData.total_tracks || 0, release_date: albumData.release_date || '', images: albumData.images || [] };
            await openDownloadMissingModalForYouTube(`discover_cache_${resolvedId}`, albumData.name, spotifyTracks, artistContext, albumContext);
            hideLoadingOverlay();
        } catch (error) {
            console.error('Error opening deep cut album:', error);
            showToast(`Failed to load album: ${error.message}`, 'error');
            hideLoadingOverlay();
        }
        return;
    }

    if (!albumId) {
        showToast('No album ID available', 'error');
        return;
    }

    // Close genre deep dive modal if open
    document.getElementById('genre-deep-dive-modal')?.remove();

    showLoadingOverlay(`Loading ${item.name || 'album'}...`);
    try {
        const _params = new URLSearchParams({ name: item.name || '', artist: item.artist_name || '' });
        let response = await fetch(`/api/discover/album/${source}/${albumId}?${_params}`);

        // If 404 (stale cache entry), try resolving via name+artist
        if (response.status === 404) {
            const resolveResp = await fetch(`/api/discover/resolve-cache-album?name=${encodeURIComponent(item.name || '')}&artist=${encodeURIComponent(item.artist_name || '')}`);
            if (resolveResp.ok) {
                const resolved = await resolveResp.json();
                if (resolved.success && resolved.entity_id && resolved.entity_id !== albumId) {
                    response = await fetch(`/api/discover/album/${resolved.source || source}/${resolved.entity_id}?${_params}`);
                }
            }
        }

        if (!response.ok) throw new Error('Album not available — it may have been removed from the source');
        const albumData = await response.json();
        if (!albumData.tracks || albumData.tracks.length === 0) throw new Error('No tracks found');

        const spotifyTracks = albumData.tracks.map(track => {
            let artists = track.artists || albumData.artists || [{ name: item.artist_name }];
            if (Array.isArray(artists)) artists = artists.map(a => a.name || a);
            return {
                id: track.id,
                name: track.name,
                artists: artists,
                album: {
                    id: albumData.id,
                    name: albumData.name,
                    album_type: albumData.album_type || 'album',
                    total_tracks: albumData.total_tracks || 0,
                    release_date: albumData.release_date || '',
                    images: albumData.images || [],
                },
                duration_ms: track.duration_ms || 0,
                track_number: track.track_number || 0,
            };
        });

        const artistContext = {
            id: albumData.artists?.[0]?.id || '',
            name: item.artist_name || albumData.artists?.[0]?.name || '',
            source: source,
        };
        const albumContext = {
            id: albumData.id,
            name: albumData.name,
            album_type: albumData.album_type || 'album',
            total_tracks: albumData.total_tracks || 0,
            release_date: albumData.release_date || '',
            images: albumData.images || [],
        };

        await openDownloadMissingModalForYouTube(
            `discover_cache_${albumId}`, albumData.name, spotifyTracks, artistContext, albumContext
        );
        hideLoadingOverlay();
    } catch (error) {
        console.error('Error opening cache discover album:', error);
        showToast(`Failed to load album: ${error.message}`, 'error');
        hideLoadingOverlay();
    }
}

function _insertCacheSection(id, title, subtitle, html, position) {
    const container = document.getElementById('discover-bylt-sections') || document.querySelector('.discover-container');
    if (!container) return;
    let section = document.getElementById(id);
    if (!section) {
        section = document.createElement('div');
        section.id = id;
        section.className = 'discover-section';
        if (position === 'top') {
            // Insert after the hero section (first child), not before it
            const hero = container.querySelector('.discover-hero');
            if (hero && hero.nextSibling) {
                container.insertBefore(section, hero.nextSibling);
            } else {
                container.prepend(section);
            }
        } else {
            container.appendChild(section);
        }
    }
    section.innerHTML = `
        <div class="discover-section-header">
            <div>
                <div class="discover-section-subtitle">${subtitle}</div>
                <h3 class="discover-section-title">${title}</h3>
            </div>
        </div>
        <div class="discover-carousel">${html}</div>
    `;
}

async function loadCacheUndiscoveredAlbums() {
    try {
        const resp = await fetch('/api/discover/undiscovered-albums');
        if (!resp.ok) return;
        const data = await resp.json();
        if (!data.success || !data.albums || !data.albums.length) return;
        _cacheDiscoverData['undiscovered'] = data.albums;
        _insertCacheSection('cache-undiscovered',
            'Undiscovered Albums', 'From artists you love',
            data.albums.map((a, i) => _cacheDiscoverCard(a, 'album', 'undiscovered', i)).join(''));
    } catch (e) { console.debug('Cache undiscovered albums:', e); }
}

async function loadCacheGenreNewReleases() {
    try {
        const resp = await fetch('/api/discover/genre-new-releases');
        if (!resp.ok) return;
        const data = await resp.json();
        if (!data.success || !data.albums || !data.albums.length) return;
        _cacheDiscoverData['genre_releases'] = data.albums;
        _insertCacheSection('cache-genre-releases',
            'New In Your Genres', 'Released in the last 90 days',
            data.albums.map((a, i) => _cacheDiscoverCard(a, 'album', 'genre_releases', i)).join(''));
    } catch (e) { console.debug('Cache genre new releases:', e); }
}

async function loadCacheLabelExplorer() {
    try {
        const resp = await fetch('/api/discover/label-explorer');
        if (!resp.ok) return;
        const data = await resp.json();
        if (!data.success || !data.albums || !data.albums.length) return;
        _cacheDiscoverData['label_explorer'] = data.albums;
        _insertCacheSection('cache-label-explorer',
            'From Your Labels', 'Popular on labels in your library',
            data.albums.map((a, i) => _cacheDiscoverCard(a, 'album', 'label_explorer', i)).join(''));
    } catch (e) { console.debug('Cache label explorer:', e); }
}

async function loadCacheDeepCuts() {
    try {
        const resp = await fetch('/api/discover/deep-cuts');
        if (!resp.ok) return;
        const data = await resp.json();
        if (!data.success || !data.tracks || !data.tracks.length) return;
        _cacheDiscoverData['deep_cuts'] = data.tracks;
        _insertCacheSection('cache-deep-cuts',
            'Deep Cuts', 'Hidden tracks from artists you know',
            data.tracks.map((t, i) => _cacheDiscoverCard(t, 'track', 'deep_cuts', i)).join(''));
    } catch (e) { console.debug('Cache deep cuts:', e); }
}

async function loadCacheGenreExplorer() {
    try {
        const resp = await fetch('/api/discover/genre-explorer');
        if (!resp.ok) return;
        const data = await resp.json();
        if (!data.success || !data.genres || !data.genres.length) return;
        const _esc = (s) => (s || '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/'/g, '&#39;');
        const html = `<div class="genre-explorer-grid">${data.genres.map(g => `
            <div class="genre-explorer-pill ${g.explored ? 'explored' : 'unexplored'}" onclick="openGenreDeepDive('${_esc(g.genre)}')" style="cursor:pointer">
                <span class="genre-pill-name">${_esc(g.genre)}</span>
                <span class="genre-pill-count">${g.artist_count} artist${g.artist_count !== 1 ? 's' : ''}</span>
                ${!g.explored ? '<span class="genre-pill-badge">New</span>' : ''}
            </div>
        `).join('')}</div>`;
        _insertCacheSection('cache-genre-explorer',
            'Genre Explorer', 'Tap a genre to explore', html, 'top');
    } catch (e) { console.debug('Cache genre explorer:', e); }
}

async function openGenreDeepDive(genre) {
    document.getElementById('genre-deep-dive-modal')?.remove();

    const _esc = (s) => (s || '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;').replace(/'/g, '&#39;');
    const _fmtNum = (n) => {
        if (!n) return '';
        if (n >= 1000000) return (n / 1000000).toFixed(1) + 'M';
        if (n >= 1000) return (n / 1000).toFixed(0) + 'K';
        return n.toString();
    };
    const _fmtDur = (ms) => {
        if (!ms) return '';
        const m = Math.floor(ms / 60000);
        const s = Math.floor((ms % 60000) / 1000);
        return `${m}:${s.toString().padStart(2, '0')}`;
    };

    const overlay = document.createElement('div');
    overlay.id = 'genre-deep-dive-modal';
    overlay.className = 'genre-dive-overlay';
    overlay.innerHTML = `
        <div class="genre-dive-modal">
            <div class="genre-dive-header">
                <div>
                    <div class="genre-dive-subtitle">Genre Deep Dive</div>
                    <h2 class="genre-dive-title">${_esc(genre)}</h2>
                </div>
                <button class="genre-dive-close" onclick="document.getElementById('genre-deep-dive-modal').remove()">&times;</button>
            </div>
            <div class="genre-dive-body" id="genre-dive-body">
                <div class="genre-dive-loading"><div class="genre-dive-spinner"></div>Exploring ${_esc(genre)}...</div>
            </div>
        </div>
    `;
    overlay.addEventListener('click', (e) => { if (e.target === overlay) overlay.remove(); });
    document.body.appendChild(overlay);

    try {
        const resp = await fetch(`/api/discover/genre-deep-dive?genre=${encodeURIComponent(genre)}`);
        if (!resp.ok) throw new Error('Failed to load');
        const data = await resp.json();
        if (!data.success) throw new Error('Failed');

        const body = document.getElementById('genre-dive-body');
        if (!body) return;

        // Update header with counts
        const subtitle = document.querySelector('.genre-dive-subtitle');
        if (subtitle) {
            const parts = [];
            if (data.artists?.length) parts.push(`${data.artists.length} artist${data.artists.length !== 1 ? 's' : ''}`);
            if (data.tracks?.length) parts.push(`${data.tracks.length} track${data.tracks.length !== 1 ? 's' : ''}`);
            if (data.albums?.length) parts.push(`${data.albums.length} album${data.albums.length !== 1 ? 's' : ''}`);
            subtitle.textContent = parts.length ? parts.join(' · ') : 'Genre Deep Dive';
        }

        let html = '';

        // Related genres — clickable pills that reload the modal
        if (data.related_genres && data.related_genres.length) {
            html += `<div class="genre-dive-related">
                <div class="genre-dive-related-label">Related Genres</div>
                ${data.related_genres.map(rg => `
                    <button class="genre-dive-related-pill" onclick="document.getElementById('genre-deep-dive-modal').remove();openGenreDeepDive('${_esc(rg.genre)}')">${_esc(rg.genre)}</button>
                `).join('')}
            </div>`;
        }

        // Artists section — clickable, navigates to artist page
        // Uses library_id for in-library artists (source-agnostic), falls back to search by name
        if (data.artists && data.artists.length) {
            html += `<div class="genre-dive-section">
                <h3 class="genre-dive-section-title"><span class="genre-dive-icon">🎤</span> Artists in ${_esc(genre)}</h3>
                <div class="genre-dive-artists">
                    ${data.artists.map(a => {
                // Always open on Artists page with discography — pass source for correct routing
                const imgUrl = _esc(a.image_url || '');
                const artSource = _esc(a.source || '');
                const clickAction = `onclick="document.getElementById('genre-deep-dive-modal').remove();navigateToArtistDetail('${_esc(a.entity_id)}','${_esc(a.name)}','${artSource}' || null)"`;
                const srcClass = (a.source || '').toLowerCase();
                return `<div class="genre-dive-artist" ${clickAction}>
                            <div class="genre-dive-artist-img" style="${a.image_url ? `background-image:url('${_esc(a.image_url)}')` : ''}">
                                ${!a.image_url ? '<span>🎤</span>' : ''}
                            </div>
                            <span class="genre-dive-src-dot genre-dive-src-${srcClass}"></span>
                            <div class="genre-dive-artist-name">${_esc(a.name)}</div>
                            ${a.followers ? `<div class="genre-dive-artist-meta">${_fmtNum(a.followers)} followers</div>` : ''}
                            ${a.library_id ? '<div class="genre-dive-artist-badge">In Library</div>' : ''}
                        </div>`;
            }).join('')}
                </div>
            </div>`;
        }

        // Tracks section — clickable, opens album download
        if (data.tracks && data.tracks.length) {
            _cacheDiscoverData['genre_dive_tracks'] = data.tracks;
            html += `<div class="genre-dive-section">
                <h3 class="genre-dive-section-title"><span class="genre-dive-icon">🎵</span> Popular Tracks</h3>
                <div class="genre-dive-tracks">
                    ${data.tracks.map((t, i) => {
                const tSrcClass = (t.source || '').toLowerCase();
                return `
                        <div class="genre-dive-track" onclick="document.getElementById('genre-deep-dive-modal').remove();openCacheDiscoverAlbum('genre_dive_tracks',${i})">
                            <div class="genre-dive-track-num">${i + 1}</div>
                            <div class="genre-dive-track-img" style="${t.image_url ? `background-image:url('${_esc(t.image_url)}')` : ''}">
                                ${!t.image_url ? '🎵' : ''}
                            </div>
                            <div class="genre-dive-track-info">
                                <div class="genre-dive-track-name">${_esc(t.name)}</div>
                                <div class="genre-dive-track-artist">${_esc(t.artist_name)}${t.album_name ? ' · ' + _esc(t.album_name) : ''}</div>
                            </div>
                            <span class="genre-dive-src-dot genre-dive-src-${tSrcClass}" style="flex-shrink:0"></span>
                            <div class="genre-dive-track-duration">${_fmtDur(t.duration_ms)}</div>
                        </div>
                    `}).join('')}
                </div>
            </div>`;
        }

        // Albums section
        if (data.albums && data.albums.length) {
            _cacheDiscoverData['genre_dive_albums'] = data.albums;
            html += `<div class="genre-dive-section">
                <h3 class="genre-dive-section-title"><span class="genre-dive-icon">💿</span> Albums</h3>
                <div class="discover-carousel">${data.albums.map((a, i) => _cacheDiscoverCard(a, 'album', 'genre_dive_albums', i)).join('')}</div>
            </div>`;
        }

        if (!html) {
            html = '<div class="genre-dive-empty"><div class="genre-dive-empty-icon">🔍</div><p>No cached data found for this genre yet</p><p class="genre-dive-empty-hint">Search for artists in this genre to build up the cache</p></div>';
        }

        body.innerHTML = html;
    } catch (e) {
        const body = document.getElementById('genre-dive-body');
        if (body) body.innerHTML = '<div style="color:rgba(255,100,100,0.6);text-align:center;padding:40px;">Failed to load genre data</div>';
    }
}

// ===============================
// BUILD A PLAYLIST FEATURE
// ===============================

let buildPlaylistSearchTimeout = null;

async function searchBuildPlaylistArtists() {
    const searchInput = document.getElementById('build-playlist-search');
    const resultsContainer = document.getElementById('build-playlist-search-results');
    const spinner = document.getElementById('bp-search-spinner');
    const query = searchInput.value.trim();

    if (!query) {
        resultsContainer.innerHTML = '';
        resultsContainer.style.display = 'none';
        if (spinner) spinner.style.display = 'none';
        return;
    }

    // Debounce search
    clearTimeout(buildPlaylistSearchTimeout);
    buildPlaylistSearchTimeout = setTimeout(async () => {
        if (spinner) spinner.style.display = 'flex';
        try {
            const response = await fetch(`/api/discover/build-playlist/search-artists?query=${encodeURIComponent(query)}`);
            const data = await response.json();
            if (!response.ok) {
                showToast(data.error || 'Search failed', 'error');
                return;
            }
            if (!data.success || !data.artists || data.artists.length === 0) {
                resultsContainer.innerHTML = '<div class="build-playlist-no-results">No artists found for "' + query.replace(/</g, '&lt;') + '"</div>';
                resultsContainer.style.display = 'block';
                return;
            }

            // Filter out already-selected artists
            const selectedIds = new Set(buildPlaylistSelectedArtists.map(a => a.id));
            const filtered = data.artists.filter(a => !selectedIds.has(a.id));

            if (filtered.length === 0) {
                resultsContainer.innerHTML = '<div class="build-playlist-no-results">All results already selected</div>';
                resultsContainer.style.display = 'block';
                return;
            }

            // Render search results
            let html = '';
            filtered.forEach(artist => {
                const imageUrl = artist.image_url || '/static/placeholder-album.png';
                const escapedName = artist.name.replace(/'/g, "\\'").replace(/"/g, '&quot;');
                html += `
                    <div class="build-playlist-search-result" onclick="addBuildPlaylistArtist('${artist.id}', '${escapedName}', '${imageUrl}')">
                        <img src="${imageUrl}" alt="${artist.name}" loading="lazy" onerror="this.src='/static/placeholder-album.png'">
                        <span class="bp-result-name">${artist.name}</span>
                        <span class="bp-result-add">+ Add</span>
                    </div>
                `;
            });

            resultsContainer.innerHTML = html;
            resultsContainer.style.display = 'block';

        } catch (error) {
            console.error('Error searching artists:', error);
        } finally {
            if (spinner) spinner.style.display = 'none';
        }
    }, 400);
}

function addBuildPlaylistArtist(artistId, artistName, imageUrl) {
    if (buildPlaylistSelectedArtists.some(a => a.id === artistId)) {
        showToast('Artist already selected', 'warning');
        return;
    }
    if (buildPlaylistSelectedArtists.length >= 5) {
        showToast('Maximum 5 seed artists', 'warning');
        return;
    }

    buildPlaylistSelectedArtists.push({
        id: artistId,
        name: artistName,
        image_url: imageUrl
    });

    renderBuildPlaylistSelectedArtists();

    // Clear search
    document.getElementById('build-playlist-search').value = '';
    document.getElementById('build-playlist-search-results').innerHTML = '';
    document.getElementById('build-playlist-search-results').style.display = 'none';
}

function removeBuildPlaylistArtist(artistId) {
    buildPlaylistSelectedArtists = buildPlaylistSelectedArtists.filter(a => a.id !== artistId);
    renderBuildPlaylistSelectedArtists();
}

function renderBuildPlaylistSelectedArtists() {
    const container = document.getElementById('build-playlist-selected-artists');
    const generateBtn = document.getElementById('build-playlist-generate-btn');
    const counter = document.getElementById('bp-selected-counter');
    const count = buildPlaylistSelectedArtists.length;

    if (counter) counter.textContent = `${count} / 5`;

    if (count === 0) {
        container.innerHTML = `
            <div class="build-playlist-no-selection">
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" style="width: 32px; height: 32px; opacity: 0.4; margin-bottom: 8px;"><path d="M12 4.5v15m7.5-7.5h-15"/></svg>
                <span>Search above to add seed artists</span>
            </div>`;
        generateBtn.disabled = true;
        return;
    }

    let html = '';
    buildPlaylistSelectedArtists.forEach(artist => {
        const escapedId = artist.id.replace(/'/g, "\\'");
        html += `
            <div class="build-playlist-selected-artist">
                <img src="${artist.image_url || '/static/placeholder-album.png'}" alt="${artist.name}" loading="lazy" onerror="this.src='/static/placeholder-album.png'">
                <span>${artist.name}</span>
                <button onclick="removeBuildPlaylistArtist('${escapedId}')" class="build-playlist-remove-artist" title="Remove">×</button>
            </div>
        `;
    });

    container.innerHTML = html;
    generateBtn.disabled = false;
}

let buildPlaylistTracks = [];

async function generateBuildPlaylist() {
    if (buildPlaylistSelectedArtists.length === 0) {
        showToast('Please select at least 1 artist', 'warning');
        return;
    }

    const generateBtn = document.getElementById('build-playlist-generate-btn');
    const resultsContainer = document.getElementById('build-playlist-results');
    const resultsWrapper = document.getElementById('build-playlist-results-wrapper');
    const loadingIndicator = document.getElementById('build-playlist-loading');
    const metadataDisplay = document.getElementById('build-playlist-metadata-display');
    const titleEl = document.getElementById('build-playlist-results-title');
    const subtitleEl = document.getElementById('build-playlist-results-subtitle');

    // Show loading, hide search area
    generateBtn.disabled = true;
    loadingIndicator.style.display = 'flex';
    resultsWrapper.style.display = 'none';
    resultsContainer.innerHTML = '';

    try {
        const seedIds = buildPlaylistSelectedArtists.map(a => a.id);
        const response = await fetch('/api/discover/build-playlist/generate', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                seed_artist_ids: seedIds,
                playlist_size: 50
            })
        });

        const data = await response.json();
        if (!response.ok || !data.success) {
            throw new Error(data.error || 'Failed to generate playlist');
        }
        if (!data.playlist || !data.playlist.tracks || data.playlist.tracks.length === 0) {
            throw new Error(data.playlist?.error || 'No tracks found. Try different seed artists.');
        }

        // Store tracks globally
        buildPlaylistTracks = data.playlist.tracks;

        // Update title and subtitle
        const artistNames = buildPlaylistSelectedArtists.map(a => a.name).join(', ');
        titleEl.textContent = 'Custom Playlist';
        subtitleEl.textContent = `Based on: ${artistNames}`;

        // Render metadata
        const metadata = data.playlist.metadata;
        metadataDisplay.innerHTML = `
            <div class="build-playlist-metadata">
                <div class="bp-meta-stat">
                    <span class="bp-meta-value">${metadata.total_tracks}</span>
                    <span class="bp-meta-label">Tracks</span>
                </div>
                <div class="bp-meta-stat">
                    <span class="bp-meta-value">${metadata.similar_artists_count}</span>
                    <span class="bp-meta-label">Similar Artists</span>
                </div>
                <div class="bp-meta-stat">
                    <span class="bp-meta-value">${metadata.albums_count}</span>
                    <span class="bp-meta-label">Albums Sampled</span>
                </div>
            </div>
        `;

        // Render playlist
        renderCompactPlaylist(resultsContainer, data.playlist.tracks);

        // Show results wrapper
        resultsWrapper.style.display = 'block';

    } catch (error) {
        console.error('Error generating playlist:', error);
        resultsWrapper.style.display = 'none';
        showToast(error.message || 'Failed to generate playlist', 'error');
    } finally {
        loadingIndicator.style.display = 'none';
        generateBtn.disabled = false;
    }
}

async function openDownloadModalForBuildPlaylist() {
    if (!buildPlaylistTracks || buildPlaylistTracks.length === 0) {
        showToast('No playlist tracks available', 'warning');
        return;
    }

    const artistNames = buildPlaylistSelectedArtists.map(a => a.name).join(', ');
    const playlistName = `Custom Playlist - ${artistNames}`;
    const virtualPlaylistId = 'build_playlist_custom';

    // Open download modal
    await openDownloadMissingModalForYouTube(virtualPlaylistId, playlistName, buildPlaylistTracks);
}

function openDailyMix(mixIndex) {
    const mix = personalizedDailyMixes[mixIndex];
    if (!mix || !mix.tracks) return;

    // TODO: Open modal or dedicated view for Daily Mix
    console.log('Opening Daily Mix:', mix.name);
}

// ===============================
// DISCOVER PLAYLIST ACTIONS
// ===============================

async function openDownloadModalForDiscoverPlaylist(playlistType, playlistName) {
    console.log(`📥 Opening Download Missing Tracks modal for ${playlistName}`);

    try {
        // Get tracks based on playlist type
        let tracks = [];
        if (playlistType === 'release_radar') {
            tracks = discoverReleaseRadarTracks;
        } else if (playlistType === 'discovery_weekly') {
            tracks = discoverWeeklyTracks;
        } else if (playlistType === 'seasonal_playlist') {
            tracks = discoverSeasonalTracks;
        } else if (playlistType === 'popular_picks') {
            tracks = personalizedPopularPicks;
        } else if (playlistType === 'hidden_gems') {
            tracks = personalizedHiddenGems;
        } else if (playlistType === 'discovery_shuffle') {
            tracks = personalizedDiscoveryShuffle;
        } else if (playlistType === 'familiar_favorites') {
            tracks = personalizedFamiliarFavorites;
        } else if (playlistType === 'recently_added') {
            tracks = personalizedRecentlyAdded;
        } else if (playlistType === 'top_tracks') {
            tracks = personalizedTopTracks;
        } else if (playlistType === 'forgotten_favorites') {
            tracks = personalizedForgottenFavorites;
        } else if (playlistType === 'build_playlist') {
            tracks = buildPlaylistTracks;
        }

        if (!tracks || tracks.length === 0) {
            showToast(`No tracks available for ${playlistName}`, 'warning');
            return;
        }

        // Convert discover tracks to format expected by download modal
        const spotifyTracks = tracks.map(track => {
            let spotifyTrack;

            // Use track_data_json if available, otherwise construct from track data
            if (track.track_data_json) {
                spotifyTrack = track.track_data_json;
            } else {
                // Fallback: construct track object from available data
                spotifyTrack = {
                    id: track.spotify_track_id,
                    name: track.track_name,
                    artists: [{ name: track.artist_name }],
                    album: {
                        name: track.album_name,
                        images: track.album_cover_url ? [{ url: track.album_cover_url }] : []
                    },
                    duration_ms: track.duration_ms || 0
                };
            }

            // Normalize artists to array of strings for modal compatibility
            if (spotifyTrack.artists && Array.isArray(spotifyTrack.artists)) {
                spotifyTrack.artists = spotifyTrack.artists.map(a => a.name || a);
            }

            return spotifyTrack;
        });

        // Create virtual playlist ID
        const virtualPlaylistId = `discover_${playlistType}`;

        // Use existing modal system (same as YouTube/Tidal playlists)
        await openDownloadMissingModalForYouTube(virtualPlaylistId, playlistName, spotifyTracks);

    } catch (error) {
        console.error('Error opening download modal for discover playlist:', error);
        showToast(`Failed to open download modal: ${error.message}`, 'error');
        hideLoadingOverlay();  // Ensure overlay is hidden on error
    }
}

function updateDiscoverDownloadButton(playlistType, state) {
    /**
     * Update the download button appearance based on download state
     * @param {string} playlistType - 'release_radar' or 'discovery_weekly'
     * @param {string} state - 'idle', 'downloading', or 'complete'
     */
    const buttonId = `${playlistType}-download-btn`;
    const button = document.getElementById(buttonId);

    if (!button) return;

    const icon = button.querySelector('.button-icon');
    const text = button.querySelector('.button-text');

    if (state === 'downloading') {
        if (icon) icon.textContent = '⏳';
        if (text) text.textContent = 'View Progress';
        button.title = 'View download progress';
    } else {
        if (icon) icon.textContent = '↓';
        if (text) text.textContent = 'Download';
        button.title = 'Download missing tracks';
    }
}

function checkForActiveDiscoverDownloads() {
    /**
     * Check for active download processes and update button states
     * Only runs if discover page is actually loaded in the DOM
     */
    // Check if discover page is loaded by looking for a discover-specific element
    const discoverPage = document.getElementById('release-radar-download-btn') ||
        document.getElementById('discovery-weekly-download-btn');

    if (!discoverPage) return;

    const discoverPlaylists = [
        { id: 'discover_release_radar', type: 'release_radar' },
        { id: 'discover_discovery_weekly', type: 'discovery_weekly' }
    ];

    discoverPlaylists.forEach(({ id, type }) => {
        if (activeDownloadProcesses[id]) {
            const process = activeDownloadProcesses[id];
            if (process.status === 'running' || process.status === 'idle') {
                updateDiscoverDownloadButton(type, 'downloading');
            }
        }
    });
}

async function startDiscoverPlaylistSync(playlistType, playlistName) {
    console.log(`🔄 Starting sync for ${playlistName} (fire-and-forget from Discover page)`);

    // Disable the sync button on the Discover page
    const buttonId = playlistType.replace(/_/g, '-') + '-sync-btn';
    const syncButton = document.getElementById(buttonId);
    if (syncButton) {
        syncButton.disabled = true;
        syncButton.style.opacity = '0.5';
        syncButton.style.cursor = 'not-allowed';
    }

    try {
        // Fetch tracks from API
        const apiUrl = _discoverPlaylistApiUrl(playlistType);
        if (!apiUrl) {
            showToast(`Unknown playlist type: ${playlistType}`, 'error');
            if (syncButton) { syncButton.disabled = false; syncButton.style.opacity = '1'; syncButton.style.cursor = 'pointer'; }
            return;
        }

        const tracksResponse = await fetch(apiUrl);
        let tracks = [];
        if (tracksResponse.ok) {
            const data = await tracksResponse.json();
            tracks = data.tracks || [];
        }

        if (!tracks.length) {
            showToast(`No tracks available for ${playlistName}`, 'warning');
            if (syncButton) { syncButton.disabled = false; syncButton.style.opacity = '1'; syncButton.style.cursor = 'pointer'; }
            return;
        }

        // Convert to sync format
        const syncTracks = tracks.map(track => {
            if (track.track_data_json) {
                const t = track.track_data_json;
                if (t.artists && Array.isArray(t.artists)) {
                    t.artists = t.artists.map(a => a.name || a);
                }
                return t;
            }
            return {
                id: track.spotify_track_id || track.track_id || '',
                name: track.track_name || track.name || '',
                artists: [track.artist_name || 'Unknown Artist'],
                album: track.album_name || '',
                duration_ms: track.duration_ms || 0,
                image_url: track.album_cover_url || track.image_url || ''
            };
        });

        const virtualPlaylistId = `discover_${playlistType}`;

        // Fire the batch download
        const batchResponse = await fetch(`/api/playlists/${virtualPlaylistId}/start-missing-process`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ tracks: syncTracks, playlist_name: playlistName })
        });

        const result = await batchResponse.json();
        if (result.success) {
            // Show toast with clickable link to Sync → Discover tab
            _showSyncToastWithLink(
                `${playlistName} (${syncTracks.length} tracks) syncing...`,
                'info',
                'View in Sync \u2192',
                () => navigateToSyncTab('discover', { highlight: `discover-sync-card-${playlistType}` })
            );
        } else {
            showToast(`Failed to start sync: ${result.error || 'Unknown error'}`, 'error');
            if (syncButton) { syncButton.disabled = false; syncButton.style.opacity = '1'; syncButton.style.cursor = 'pointer'; }
        }
    } catch (error) {
        console.error(`Error syncing ${playlistName}:`, error);
        showToast(`Failed to sync ${playlistName}`, 'error');
        if (syncButton) { syncButton.disabled = false; syncButton.style.opacity = '1'; syncButton.style.cursor = 'pointer'; }
    }
}

/**
 * Show a toast with a clickable action link (like showToast but with a custom link).
 */
function _showSyncToastWithLink(message, type, linkText, onClick) {
    const container = document.getElementById('toast-container');
    if (!container) { showToast(message, type); return; }

    const icon = { success: '\u2705', error: '\u274c', warning: '\u26a0\ufe0f', info: '\u2139\ufe0f' }[type] || '\u2139\ufe0f';
    const toast = document.createElement('div');
    toast.className = `toast-compact toast-${type}`;
    toast.innerHTML = `<span class="toast-compact-icon">${icon}</span><span class="toast-compact-msg">${_escToast(message)}</span>`;

    const link = document.createElement('span');
    link.className = 'toast-compact-link';
    link.textContent = linkText;
    link.onclick = e => { e.stopPropagation(); onClick(); };
    toast.appendChild(link);

    toast.onclick = () => { toast.classList.add('toast-exit'); setTimeout(() => { if (container.contains(toast)) container.removeChild(toast); }, 200); };
    container.appendChild(toast);
    requestAnimationFrame(() => toast.classList.add('toast-enter'));

    setTimeout(() => {
        if (container.contains(toast)) {
            toast.classList.add('toast-exit');
            setTimeout(() => { if (container.contains(toast)) container.removeChild(toast); }, 300);
        }
    }, 6000);
}

// Track active discover sync pollers
const discoverSyncPollers = {};

function startDiscoverSyncPolling(playlistType, virtualPlaylistId) {
    // Stop any existing poller for this playlist type
    if (discoverSyncPollers[playlistType]) {
        clearInterval(discoverSyncPollers[playlistType]);
    }

    console.log(`🔄 Starting sync polling for ${playlistType} (${virtualPlaylistId})`);

    // Phase 5: Subscribe via WebSocket
    if (socketConnected) {
        socket.emit('sync:subscribe', { playlist_ids: [virtualPlaylistId] });
        _syncProgressCallbacks[virtualPlaylistId] = (data) => {
            const prefix = playlistType.replace(/_/g, '-');
            const progress = data.progress || {};
            const total = progress.total_tracks || 0;
            const matched = progress.matched_tracks || 0;
            const failed = progress.failed_tracks || 0;
            const processed = matched + failed;
            const pending = total - processed;
            const pct = total > 0 ? Math.round((processed / total) * 100) : 0;
            const el = (id) => document.getElementById(id);
            if (el(`${prefix}-sync-completed`)) el(`${prefix}-sync-completed`).textContent = matched;
            if (el(`${prefix}-sync-pending`)) el(`${prefix}-sync-pending`).textContent = pending;
            if (el(`${prefix}-sync-failed`)) el(`${prefix}-sync-failed`).textContent = failed;
            if (el(`${prefix}-sync-percentage`)) el(`${prefix}-sync-percentage`).textContent = pct;
            if (data.status === 'finished') {
                if (discoverSyncPollers[playlistType]) { clearInterval(discoverSyncPollers[playlistType]); delete discoverSyncPollers[playlistType]; }
                socket.emit('sync:unsubscribe', { playlist_ids: [virtualPlaylistId] });
                delete _syncProgressCallbacks[virtualPlaylistId];
                const buttonId = playlistType.replace(/_/g, '-') + '-sync-btn';
                const syncButton = el(buttonId);
                if (syncButton) { syncButton.disabled = false; syncButton.style.opacity = '1'; syncButton.style.cursor = 'pointer'; }
                const playlistNames = {
                    'release_radar': 'Fresh Tape', 'discovery_weekly': 'The Archives',
                    'seasonal_playlist': 'Seasonal Mix', 'popular_picks': 'Popular Picks',
                    'hidden_gems': 'Hidden Gems', 'discovery_shuffle': 'Discovery Shuffle',
                    'familiar_favorites': 'Familiar Favorites', 'build_playlist': 'Custom Playlist'
                };
                const dn = playlistNames[playlistType] || playlistType;
                const _m = progress.matched_tracks || matched || 0;
                const _t = progress.total_tracks || total || 0;
                const _miss = _t - _m;
                if (_miss > 0) {
                    showToast(`${dn}: ${_m}/${_t} matched, ${_miss} missing`, 'warning');
                } else {
                    showToast(`${dn}: all ${_t} tracks matched!`, 'success');
                }
                if (el(`${prefix}-sync-percentage`)) el(`${prefix}-sync-percentage`).textContent = '100';
                if (el(`${prefix}-sync-pending`)) el(`${prefix}-sync-pending`).textContent = '0';
                setTimeout(() => { const sd = el(`${prefix}-sync-status`); if (sd) sd.style.display = 'none'; }, 5000);
            }
        };
    }

    // Poll every 500ms for progress updates
    discoverSyncPollers[playlistType] = setInterval(async () => {
        // Always poll — no dedicated WebSocket events for discovery progress
        try {
            const response = await fetch(`/api/sync/status/${virtualPlaylistId}`);
            if (!response.ok) {
                console.log(`⚠️ Sync status response not OK: ${response.status}`);
                return;
            }

            const data = await response.json();
            console.log(`📊 Sync status for ${playlistType}:`, data);

            // Update UI with progress (data structure: {status: ..., progress: {...}})
            // Convert underscores to hyphens for HTML IDs
            const prefix = playlistType.replace(/_/g, '-');
            const progress = data.progress || {};

            const completedEl = document.getElementById(`${prefix}-sync-completed`);
            const pendingEl = document.getElementById(`${prefix}-sync-pending`);
            const failedEl = document.getElementById(`${prefix}-sync-failed`);
            const percentageEl = document.getElementById(`${prefix}-sync-percentage`);

            const total = progress.total_tracks || 0;
            const matched = progress.matched_tracks || 0;
            const failed = progress.failed_tracks || 0;
            const processed = matched + failed;
            const pending = total - processed;
            const completionPercentage = total > 0 ? Math.round((processed / total) * 100) : 0;

            if (completedEl) completedEl.textContent = matched;
            if (pendingEl) pendingEl.textContent = pending;
            if (failedEl) failedEl.textContent = failed;
            if (percentageEl) percentageEl.textContent = completionPercentage;

            // If complete, stop polling and hide status after delay
            if (data.status === 'finished') {
                console.log(`✅ Sync complete for ${playlistType}`);
                clearInterval(discoverSyncPollers[playlistType]);
                delete discoverSyncPollers[playlistType];

                // Re-enable sync button
                const buttonId = playlistType.replace(/_/g, '-') + '-sync-btn';
                const syncButton = document.getElementById(buttonId);
                if (syncButton) {
                    syncButton.disabled = false;
                    syncButton.style.opacity = '1';
                    syncButton.style.cursor = 'pointer';
                }

                // Show completion toast with playlist name
                const playlistNames = {
                    'release_radar': 'Fresh Tape',
                    'discovery_weekly': 'The Archives',
                    'seasonal_playlist': 'Seasonal Mix',
                    'popular_picks': 'Popular Picks',
                    'hidden_gems': 'Hidden Gems',
                    'discovery_shuffle': 'Discovery Shuffle',
                    'familiar_favorites': 'Familiar Favorites',
                    'build_playlist': 'Custom Playlist'
                };
                const displayName = playlistNames[playlistType] || playlistType;
                const missing = total - matched;
                if (missing > 0) {
                    showToast(`${displayName}: ${matched}/${total} matched, ${missing} missing`, 'warning');
                } else {
                    showToast(`${displayName}: all ${total} tracks matched!`, 'success');
                }

                // Update status display to show final result, then hide after 5s
                if (percentageEl) percentageEl.textContent = '100';
                if (pendingEl) pendingEl.textContent = '0';
                setTimeout(() => {
                    const statusDisplay = document.getElementById(`${prefix}-sync-status`);
                    if (statusDisplay) {
                        statusDisplay.style.display = 'none';
                    }
                }, 5000);
            }

        } catch (error) {
            console.error(`❌ Error polling sync status for ${playlistType}:`, error);
        }
    }, 500);
}

async function openDownloadModalForRecentAlbum(albumIndex) {
    const album = discoverRecentAlbums[albumIndex];
    if (!album) {
        showToast('Album data not found', 'error');
        return;
    }

    console.log(`📥 Opening Download Missing Tracks modal for album: ${album.album_name}`);
    showLoadingOverlay(`Loading tracks for ${album.album_name}...`);

    try {
        // Determine source and album ID - use source-agnostic endpoint
        const source = album.source || (album.album_spotify_id ? 'spotify' : album.album_deezer_id ? 'deezer' : 'itunes');
        const albumId = source === 'spotify' ? album.album_spotify_id : source === 'deezer' ? album.album_deezer_id : album.album_itunes_id;

        if (!albumId) {
            throw new Error(`No ${source} album ID available`);
        }

        // Fetch album tracks from appropriate source (pass name/artist for Hydrabase support)
        const _dap2 = new URLSearchParams({ name: album.album_name || '', artist: album.artist_name || '' });
        const response = await fetch(`/api/discover/album/${source}/${albumId}?${_dap2}`);
        if (!response.ok) {
            throw new Error('Failed to fetch album tracks');
        }

        const albumData = await response.json();
        if (!albumData.tracks || albumData.tracks.length === 0) {
            throw new Error('No tracks found in album');
        }

        // Convert to expected format - CRITICAL FIX: Use fresh albumData from Spotify, not cached album
        const spotifyTracks = albumData.tracks.map(track => {
            // Normalize artists to array of strings
            let artists = track.artists || albumData.artists || [{ name: album.artist_name }];
            if (Array.isArray(artists)) {
                artists = artists.map(a => a.name || a);
            }

            return {
                id: track.id,
                name: track.name,
                artists: artists,
                album: {
                    id: albumData.id,                              // ✅ Album ID for proper tracking
                    name: albumData.name,                          // ✅ Use fresh data, not cached
                    album_type: albumData.album_type || 'album',   // ✅ Critical: Album type for classification
                    total_tracks: albumData.total_tracks || 0,     // ✅ Total tracks for context
                    release_date: albumData.release_date || '',    // ✅ Release date
                    images: albumData.images || []                 // ✅ Use Spotify images
                },
                duration_ms: track.duration_ms || 0,
                track_number: track.track_number || 0
            };
        });

        // Create virtual playlist ID using the appropriate album ID
        const virtualPlaylistId = `discover_album_${albumId}`;

        // CRITICAL FIX: Pass proper artist/album context for modal display
        const artistContext = {
            id: source === 'spotify' ? album.artist_spotify_id : source === 'deezer' ? album.artist_deezer_id : album.artist_itunes_id,
            name: album.artist_name,
            source: source
        };

        const albumContext = {
            id: albumData.id,
            name: albumData.name,
            album_type: albumData.album_type || 'album',
            total_tracks: albumData.total_tracks || 0,
            release_date: albumData.release_date || '',
            images: albumData.images || []
        };

        // Open download modal with artist/album context
        await openDownloadMissingModalForYouTube(virtualPlaylistId, albumData.name, spotifyTracks, artistContext, albumContext);

        hideLoadingOverlay();

    } catch (error) {
        console.error('Error opening album download modal:', error);
        showToast(`Failed to load album: ${error.message}`, 'error');
        hideLoadingOverlay();
    }
}

// ===============================
// DISCOVER DOWNLOAD BAR
// ===============================

// Track discover page downloads
let discoverDownloads = {}; // playlistId -> { name, type, status, virtualPlaylistId, startTime }

/**
 * Add a download to the discover download bar
 */
function addDiscoverDownload(playlistId, playlistName, playlistType, imageUrl = null) {
    console.log(`📥 [DOWNLOAD SIDEBAR] Adding discover download: ${playlistName} (${playlistId}) type: ${playlistType}, image: ${imageUrl}`);

    // Always register the download in state (needed for dashboard even when not on discover page)
    discoverDownloads[playlistId] = {
        name: playlistName,
        type: playlistType,
        status: 'in_progress',
        virtualPlaylistId: playlistId,
        imageUrl: imageUrl,
        startTime: new Date()
    };

    console.log(`📊 [DOWNLOAD SIDEBAR] Active downloads:`, Object.keys(discoverDownloads));

    // Update discover page sidebar if it exists (user is on discover page)
    const downloadSidebar = document.getElementById('discover-download-sidebar');
    if (downloadSidebar) {
        updateDiscoverDownloadBar(); // Also saves snapshot internally
    } else {
        console.log('ℹ️ [DOWNLOAD SIDEBAR] Sidebar not present - skipping sidebar UI update');
        saveDiscoverDownloadSnapshot(); // Persist state even when sidebar is absent
    }

    updateDashboardDownloads();
    monitorDiscoverDownload(playlistId);
}

/**
 * Monitor a discover download for completion
 */
function monitorDiscoverDownload(playlistId) {
    let notFoundCount = 0;
    const maxNotFoundAttempts = 5; // Give sync 10 seconds to start (5 checks * 2 seconds)

    // Phase 5: Subscribe via WebSocket for sync status updates
    if (socketConnected) {
        socket.emit('sync:subscribe', { playlist_ids: [playlistId] });
        _syncProgressCallbacks[playlistId] = (data) => {
            if (!discoverDownloads[playlistId]) return;
            if (data.status === 'complete' || data.status === 'finished') {
                discoverDownloads[playlistId].status = 'completed';
                updateDiscoverDownloadBar();
                updateDashboardDownloads();
                socket.emit('sync:unsubscribe', { playlist_ids: [playlistId] });
                delete _syncProgressCallbacks[playlistId];
                setTimeout(() => {
                    if (discoverDownloads[playlistId] && discoverDownloads[playlistId].status === 'completed') {
                        removeDiscoverDownload(playlistId);
                    }
                }, 30000);
            }
        };
    }

    const checkInterval = setInterval(async () => {
        try {
            // Check if download still exists
            if (!discoverDownloads[playlistId]) {
                clearInterval(checkInterval);
                if (_syncProgressCallbacks[playlistId]) {
                    if (socketConnected) socket.emit('sync:unsubscribe', { playlist_ids: [playlistId] });
                    delete _syncProgressCallbacks[playlistId];
                }
                return;
            }

            // First check if there's an active download process (modal-based downloads)
            const activeProcess = activeDownloadProcesses[playlistId];
            if (activeProcess) {
                console.log(`📂 [DOWNLOAD BAR] Found active process for ${playlistId}, status: ${activeProcess.status}`);

                if (activeProcess.status === 'complete') {
                    console.log(`✅ [DOWNLOAD BAR] Process completed: ${discoverDownloads[playlistId].name}`);
                    discoverDownloads[playlistId].status = 'completed';
                    updateDiscoverDownloadBar();
                    updateDashboardDownloads();
                    clearInterval(checkInterval);

                    // Auto-remove completed downloads after 30 seconds
                    setTimeout(() => {
                        if (discoverDownloads[playlistId] && discoverDownloads[playlistId].status === 'completed') {
                            removeDiscoverDownload(playlistId);
                        }
                    }, 30000);
                }
                return; // Continue monitoring
            }

            // Check sync status API (for sync-based downloads)
            if (socketConnected) return; // Phase 5: WS handles sync status
            const response = await fetch(`/api/sync/status/${playlistId}`);
            if (response.ok) {
                const data = await response.json();
                notFoundCount = 0; // Reset counter if found

                console.log(`🔄 [DOWNLOAD BAR] Sync status for ${playlistId}: ${data.status}`);

                if (data.status === 'complete') {
                    console.log(`✅ [DOWNLOAD BAR] Sync completed: ${discoverDownloads[playlistId].name}`);
                    discoverDownloads[playlistId].status = 'completed';
                    updateDiscoverDownloadBar();
                    updateDashboardDownloads();
                    clearInterval(checkInterval);

                    // Auto-remove completed downloads after 30 seconds
                    setTimeout(() => {
                        if (discoverDownloads[playlistId] && discoverDownloads[playlistId].status === 'completed') {
                            removeDiscoverDownload(playlistId);
                        }
                    }, 30000);
                }
            } else if (response.status === 404) {
                notFoundCount++;
                console.log(`🔍 [DOWNLOAD BAR] Sync not found for ${playlistId} (attempt ${notFoundCount}/${maxNotFoundAttempts})`);

                // Only remove after multiple attempts (give it time to start)
                if (notFoundCount >= maxNotFoundAttempts) {
                    console.log(`⏹️ [DOWNLOAD BAR] Sync not found after ${maxNotFoundAttempts} attempts, removing`);
                    clearInterval(checkInterval);
                    removeDiscoverDownload(playlistId);
                }
            }
        } catch (error) {
            console.error(`❌ [DOWNLOAD BAR] Error monitoring ${playlistId}:`, error);
        }
    }, 2000); // Check every 2 seconds
}

/**
 * Remove a download from the bar
 */
function removeDiscoverDownload(playlistId) {
    console.log(`🗑️ Removing discover download: ${playlistId}`);
    delete discoverDownloads[playlistId];
    updateDiscoverDownloadBar();
    updateDashboardDownloads();
    saveDiscoverDownloadSnapshot(); // Save state after removal
}

/**
 * Update the discover download sidebar UI
 */
function updateDiscoverDownloadBar() {
    const downloadSidebar = document.getElementById('discover-download-sidebar');
    const bubblesContainer = document.getElementById('discover-download-bubbles');
    const countElement = document.getElementById('discover-download-count');

    console.log(`🔄 [DOWNLOAD SIDEBAR] Updating sidebar - found elements:`, {
        downloadSidebar: !!downloadSidebar,
        bubblesContainer: !!bubblesContainer,
        countElement: !!countElement
    });

    if (!downloadSidebar || !bubblesContainer || !countElement) {
        console.warn('⚠️ [DOWNLOAD SIDEBAR] Missing elements, cannot update');
        return;
    }

    const activeDownloads = Object.keys(discoverDownloads);
    const count = activeDownloads.length;

    console.log(`📊 [DOWNLOAD SIDEBAR] Updating with ${count} active downloads`);

    // Update count
    countElement.textContent = count;

    // Show/hide sidebar
    if (count === 0) {
        console.log(`👁️ [DOWNLOAD SIDEBAR] No downloads, hiding sidebar`);
        downloadSidebar.classList.add('hidden');
        return;
    } else {
        console.log(`👁️ [DOWNLOAD SIDEBAR] ${count} downloads, showing sidebar`);
        downloadSidebar.classList.remove('hidden');
    }

    // Update bubbles
    bubblesContainer.innerHTML = activeDownloads.map(playlistId => {
        const download = discoverDownloads[playlistId];
        const isCompleted = download.status === 'completed';
        const icon = isCompleted ? '✅' : '⏳';

        // Use image if available, otherwise gradient background
        const imageUrl = download.imageUrl || '';
        const backgroundStyle = imageUrl ?
            `background-image: url('${imageUrl}');` :
            `background: linear-gradient(135deg, rgba(29, 185, 84, 0.3) 0%, rgba(24, 156, 71, 0.2) 100%);`;

        return `
            <div class="discover-download-bubble">
                <div class="discover-download-bubble-card ${isCompleted ? 'completed' : ''}"
                     onclick="openDiscoverDownloadModal('${playlistId}')"
                     title="${escapeHtml(download.name)} - Click to view">
                    <div class="discover-download-bubble-image" style="${backgroundStyle}"></div>
                    <div class="discover-download-bubble-overlay"></div>
                    <div class="discover-download-bubble-content">
                        <span class="discover-download-bubble-icon">${icon}</span>
                    </div>
                </div>
                <div class="discover-download-bubble-name">${escapeHtml(download.name)}</div>
            </div>
        `;
    }).join('');

    console.log(`📊 Updated discover download sidebar: ${count} active downloads`);

    // Save snapshot after UI update
    saveDiscoverDownloadSnapshot();
}

/**
 * Open download modal for a discover playlist
 */
async function openDiscoverDownloadModal(playlistId) {
    console.log(`📂 [DOWNLOAD BAR] Opening download modal for: ${playlistId}`);

    // Check if there's an active download process with modal
    let process = activeDownloadProcesses[playlistId];

    console.log(`📋 [DOWNLOAD BAR] Process found:`, {
        exists: !!process,
        hasModalElement: !!(process && process.modalElement),
        hasModalId: !!(process && process.modalId)
    });

    if (process) {
        // Try modalElement first (album downloads)
        if (process.modalElement) {
            console.log(`✅ [DOWNLOAD BAR] Opening modal via modalElement`);
            process.modalElement.style.display = 'flex';
            return;
        }

        // Try modalId (sync downloads)
        if (process.modalId) {
            const modal = document.getElementById(process.modalId);
            if (modal) {
                console.log(`✅ [DOWNLOAD BAR] Opening modal via modalId: ${process.modalId}`);
                modal.style.display = 'flex';
                return;
            }
        }
    }

    // If no process found, try to rehydrate from backend
    console.log(`💧 [DOWNLOAD BAR] No modal found, attempting to rehydrate from backend...`);
    const rehydrated = await rehydrateDiscoverDownloadModal(playlistId);

    if (rehydrated) {
        console.log(`✅ [DOWNLOAD BAR] Successfully rehydrated modal, opening it...`);
        // Try again after rehydration
        process = activeDownloadProcesses[playlistId];
        if (process && process.modalElement) {
            process.modalElement.style.display = 'flex';
            return;
        }
    }

    // Fallback: show toast
    const download = discoverDownloads[playlistId];
    if (download) {
        console.log(`ℹ️ [DOWNLOAD BAR] No modal found after rehydration attempt, showing toast`);
        showToast(`Download: ${download.name} - ${download.status}`, 'info');
    } else {
        console.warn(`⚠️ [DOWNLOAD BAR] No download or process found for: ${playlistId}`);
    }
}

/**
 * Initialize discover download sidebar on page load
 */
function initializeDiscoverDownloadBar() {
    console.log('🎵 Initializing discover download sidebar...');

    // Start with sidebar hidden (will be shown if downloads exist after hydration)
    const downloadSidebar = document.getElementById('discover-download-sidebar');
    if (downloadSidebar) {
        downloadSidebar.classList.add('hidden');
    }
}

// --- Discover Download Modal Rehydration ---

async function rehydrateDiscoverDownloadModal(playlistId) {
    /**
     * Rehydrates a discover download modal from backend process data.
     * Fetches tracks from backend API and recreates the modal (user-requested).
     */
    try {
        console.log(`💧 [REHYDRATE] Attempting to rehydrate modal for: ${playlistId}`);

        // Check if there's an active backend process for this playlist
        const batchResponse = await fetch(`/api/download_status/batch`);
        if (!batchResponse.ok) {
            console.log(`⚠️ [REHYDRATE] Failed to fetch batch info`);
            return false;
        }

        const batchData = await batchResponse.json();
        const batches = batchData.batches || {};

        // Find the batch for this playlist (batches is an object with batch_id keys)
        let batchId = null;
        let batch = null;
        for (const [id, batchStatus] of Object.entries(batches)) {
            if (batchStatus.playlist_id === playlistId) {
                batchId = id;
                batch = batchStatus;
                break;
            }
        }

        if (!batch || !batchId) {
            console.log(`⚠️ [REHYDRATE] No active batch found for ${playlistId}`);
            return false;
        }

        console.log(`✅ [REHYDRATE] Found active batch for ${playlistId}: ${batchId}`, batch);

        // Get the download metadata from discoverDownloads
        const downloadData = discoverDownloads[playlistId];
        if (!downloadData) {
            console.log(`⚠️ [REHYDRATE] No download metadata found for ${playlistId}`);
            return false;
        }

        // Handle album downloads from Recent Releases
        if (playlistId.startsWith('discover_album_')) {
            const albumId = playlistId.replace('discover_album_', '');
            console.log(`💧 [REHYDRATE] Album download - fetching album ${albumId}...`);

            try {
                const albumResponse = await fetch(`/api/spotify/album/${albumId}`);
                if (!albumResponse.ok) {
                    console.error(`❌ [REHYDRATE] Failed to fetch album: ${albumResponse.status}`);
                    return false;
                }

                const albumData = await albumResponse.json();
                if (!albumData.tracks || albumData.tracks.length === 0) {
                    console.error(`❌ [REHYDRATE] No tracks in album`);
                    return false;
                }

                // Convert tracks to expected format
                const spotifyTracks = albumData.tracks.map(track => {
                    let artists = track.artists || [];
                    if (Array.isArray(artists)) {
                        artists = artists.map(a => a.name || a);
                    }

                    return {
                        id: track.id,
                        name: track.name,
                        artists: artists,
                        album: {
                            name: albumData.name || downloadData.name.split(' - ')[0],
                            images: downloadData.imageUrl ? [{ url: downloadData.imageUrl }] : []
                        },
                        duration_ms: track.duration_ms || 0
                    };
                });

                console.log(`✅ [REHYDRATE] Retrieved ${spotifyTracks.length} tracks for album`);

                // Create modal
                await openDownloadMissingModalForYouTube(playlistId, downloadData.name, spotifyTracks);

                // Update process
                const process = activeDownloadProcesses[playlistId];
                if (process) {
                    process.status = 'running';
                    process.batchId = batchId;
                    subscribeToDownloadBatch(batchId);
                    const beginBtn = document.getElementById(`begin-analysis-btn-${playlistId}`);
                    const cancelBtn = document.getElementById(`cancel-all-btn-${playlistId}`);
                    if (beginBtn) beginBtn.style.display = 'none';
                    if (cancelBtn) cancelBtn.style.display = 'inline-block';

                    // Start polling for status updates
                    startModalDownloadPolling(playlistId);
                    console.log(`✅ [REHYDRATE] Successfully rehydrated album modal with polling`);
                    return true;
                }
                return false;

            } catch (error) {
                console.error(`❌ [REHYDRATE] Error fetching album:`, error);
                return false;
            }
        }

        // Determine API endpoint based on playlist ID
        let apiEndpoint;
        if (playlistId === 'discover_release_radar') {
            apiEndpoint = '/api/discover/release-radar';
        } else if (playlistId === 'discover_discovery_weekly') {
            apiEndpoint = '/api/discover/discovery-weekly';
        } else if (playlistId === 'discover_seasonal_playlist') {
            apiEndpoint = '/api/discover/seasonal-playlist';
        } else if (playlistId === 'discover_popular_picks') {
            apiEndpoint = '/api/discover/popular-picks';
        } else if (playlistId === 'discover_hidden_gems') {
            apiEndpoint = '/api/discover/hidden-gems';
        } else if (playlistId === 'discover_discovery_shuffle') {
            apiEndpoint = '/api/discover/discovery-shuffle';
        } else if (playlistId === 'discover_familiar_favorites') {
            apiEndpoint = '/api/discover/familiar-favorites';
        } else if (playlistId === 'build_playlist_custom') {
            apiEndpoint = '/api/discover/build-playlist';
        } else if (playlistId.startsWith('discover_lb_')) {
            // ListenBrainz playlist - fetch from cache
            const identifier = playlistId.replace('discover_lb_', '');
            const tracks = listenbrainzTracksCache[identifier];
            if (!tracks || tracks.length === 0) {
                console.log(`⚠️ [REHYDRATE] No ListenBrainz tracks in cache for ${identifier}`);
                return false;
            }

            // Convert to Spotify format
            const spotifyTracks = tracks.map(track => ({
                id: track.mbid || `listenbrainz_${track.track_name}_${track.artist_name}`.replace(/[^a-z0-9]/gi, '_'),  // Generate ID if missing
                name: track.track_name,
                artists: [{ name: cleanArtistName(track.artist_name) }], // Proper Spotify format
                album: {
                    name: track.album_name,
                    images: track.album_cover_url ? [{ url: track.album_cover_url }] : []
                },
                duration_ms: track.duration_ms || 0,
                mbid: track.mbid
            }));

            // Create modal and update process
            await openDownloadMissingModalForYouTube(playlistId, downloadData.name, spotifyTracks);
            const process = activeDownloadProcesses[playlistId];
            if (process) {
                process.status = 'running';
                process.batchId = batchId;
                subscribeToDownloadBatch(batchId);
                const beginBtn = document.getElementById(`begin-analysis-btn-${playlistId}`);
                const cancelBtn = document.getElementById(`cancel-all-btn-${playlistId}`);
                if (beginBtn) beginBtn.style.display = 'none';
                if (cancelBtn) cancelBtn.style.display = 'inline-block';

                // Start polling for status updates
                startModalDownloadPolling(playlistId);
                console.log(`✅ [REHYDRATE] Successfully rehydrated ListenBrainz modal with polling`);
                return true;
            }
            return false;
        } else if (playlistId.startsWith('listenbrainz_')) {
            // ListenBrainz download from discovery modal - get from backend state
            const mbid = playlistId.replace('listenbrainz_', '');
            console.log(`💧 [REHYDRATE] ListenBrainz download - fetching state for MBID: ${mbid}`);

            try {
                // Fetch ListenBrainz state from backend
                const stateResponse = await fetch(`/api/listenbrainz/state/${mbid}`);
                if (!stateResponse.ok) {
                    console.log(`⚠️ [REHYDRATE] Failed to fetch ListenBrainz state`);
                    return false;
                }

                const stateData = await stateResponse.json();
                if (!stateData || !stateData.discovery_results) {
                    console.log(`⚠️ [REHYDRATE] No discovery results in ListenBrainz state`);
                    return false;
                }

                // Convert discovery results to Spotify tracks
                const spotifyTracks = stateData.discovery_results
                    .filter(result => result.spotify_data)
                    .map(result => {
                        const track = result.spotify_data;
                        // Ensure artists is in proper Spotify format: [{name: ...}]
                        let artistsArray = [];
                        if (track.artists && Array.isArray(track.artists)) {
                            artistsArray = track.artists.map(artist => {
                                if (typeof artist === 'string') {
                                    return { name: artist };
                                } else if (artist && artist.name) {
                                    return { name: artist.name };
                                } else {
                                    return { name: String(artist || 'Unknown Artist') };
                                }
                            });
                        } else if (track.artists && typeof track.artists === 'string') {
                            artistsArray = [{ name: track.artists }];
                        } else {
                            artistsArray = [{ name: 'Unknown Artist' }];
                        }
                        return {
                            id: track.id,
                            name: track.name,
                            artists: artistsArray,
                            album: track.album || { name: 'Unknown Album', images: [] },
                            duration_ms: track.duration_ms || 0,
                            external_urls: track.external_urls || {}
                        };
                    });

                if (spotifyTracks.length === 0) {
                    console.log(`⚠️ [REHYDRATE] No Spotify tracks in ListenBrainz discovery results`);
                    return false;
                }

                console.log(`✅ [REHYDRATE] Retrieved ${spotifyTracks.length} tracks from ListenBrainz state`);

                // Create modal and update process
                await openDownloadMissingModalForYouTube(playlistId, downloadData.name, spotifyTracks);
                const process = activeDownloadProcesses[playlistId];
                if (process) {
                    process.status = 'running';
                    process.batchId = batchId;
                    subscribeToDownloadBatch(batchId);
                    const beginBtn = document.getElementById(`begin-analysis-btn-${playlistId}`);
                    const cancelBtn = document.getElementById(`cancel-all-btn-${playlistId}`);
                    if (beginBtn) beginBtn.style.display = 'none';
                    if (cancelBtn) cancelBtn.style.display = 'inline-block';

                    // Start polling for status updates
                    startModalDownloadPolling(playlistId);
                    console.log(`✅ [REHYDRATE] Successfully rehydrated ListenBrainz download modal with polling`);
                    return true;
                }
                return false;

            } catch (error) {
                console.error(`❌ [REHYDRATE] Error fetching ListenBrainz state:`, error);
                return false;
            }
        } else {
            console.error(`❌ [REHYDRATE] Unknown discover playlist type: ${playlistId}`);
            return false;
        }

        // Fetch tracks from API
        console.log(`📡 [REHYDRATE] Fetching tracks from ${apiEndpoint}...`);
        const response = await fetch(apiEndpoint);
        if (!response.ok) {
            console.error(`❌ [REHYDRATE] Failed to fetch tracks: ${response.status}`);
            return false;
        }

        const data = await response.json();
        if (!data.success || !data.tracks) {
            console.error(`❌ [REHYDRATE] Invalid track data:`, data);
            return false;
        }

        const tracks = data.tracks;
        console.log(`✅ [REHYDRATE] Retrieved ${tracks.length} tracks`);

        // Transform tracks to Spotify format
        const spotifyTracks = tracks.map(track => {
            let spotifyTrack;
            if (track.track_data_json) {
                spotifyTrack = track.track_data_json;
            } else {
                spotifyTrack = {
                    id: track.spotify_track_id,
                    name: track.track_name,
                    artists: [{ name: track.artist_name }],
                    album: {
                        name: track.album_name,
                        images: track.album_cover_url ? [{ url: track.album_cover_url }] : []
                    },
                    duration_ms: track.duration_ms || 0
                };
            }
            if (spotifyTrack.artists && Array.isArray(spotifyTrack.artists)) {
                spotifyTrack.artists = spotifyTrack.artists.map(a => a.name || a);
            }
            return spotifyTrack;
        });

        // Create the modal
        await openDownloadMissingModalForYouTube(playlistId, downloadData.name, spotifyTracks);

        // Update process with batch info
        const process = activeDownloadProcesses[playlistId];
        if (process) {
            process.status = 'running';
            process.batchId = batchId;
            subscribeToDownloadBatch(batchId);

            // Update button states
            const beginBtn = document.getElementById(`begin-analysis-btn-${playlistId}`);
            const cancelBtn = document.getElementById(`cancel-all-btn-${playlistId}`);
            if (beginBtn) beginBtn.style.display = 'none';
            if (cancelBtn) cancelBtn.style.display = 'inline-block';

            // Start polling for status updates
            startModalDownloadPolling(playlistId);

            // Don't hide the modal - user clicked to open it
            console.log(`✅ [REHYDRATE] Successfully rehydrated modal for ${downloadData.name} with polling`);
            return true;
        } else {
            console.error(`❌ [REHYDRATE] Failed to find rehydrated process for ${playlistId}`);
            return false;
        }

    } catch (error) {
        console.error(`❌ [REHYDRATE] Error rehydrating discover download modal:`, error);
        return false;
    }
}

// --- Discover Download Snapshot System ---

let discoverSnapshotSaveTimeout = null; // Debounce snapshot saves

async function saveDiscoverDownloadSnapshot() {
    /**
     * Saves current discoverDownloads state to backend for persistence.
     * Debounced to prevent excessive backend calls.
     */

    // Clear any existing timeout
    if (discoverSnapshotSaveTimeout) {
        clearTimeout(discoverSnapshotSaveTimeout);
    }

    // Debounce the actual save
    discoverSnapshotSaveTimeout = setTimeout(async () => {
        try {
            const downloadCount = Object.keys(discoverDownloads).length;

            // Don't save empty state
            if (downloadCount === 0) {
                console.log('📸 Skipping discover snapshot save - no downloads to save');
                return;
            }

            console.log(`📸 Saving discover download snapshot: ${downloadCount} downloads`);

            // Prepare snapshot data (clean format)
            const cleanDownloads = {};
            for (const [playlistId, downloadData] of Object.entries(discoverDownloads)) {
                cleanDownloads[playlistId] = {
                    name: downloadData.name,
                    type: downloadData.type,
                    status: downloadData.status,
                    virtualPlaylistId: downloadData.virtualPlaylistId,
                    imageUrl: downloadData.imageUrl,
                    startTime: downloadData.startTime instanceof Date ? downloadData.startTime.toISOString() : downloadData.startTime
                };
            }

            const response = await fetch('/api/discover_downloads/snapshot', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json'
                },
                body: JSON.stringify({
                    downloads: cleanDownloads
                })
            });

            const data = await response.json();

            if (data.success) {
                console.log(`✅ Discover download snapshot saved: ${downloadCount} downloads`);
            } else {
                console.error('❌ Failed to save discover download snapshot:', data.error);
            }

        } catch (error) {
            console.error('❌ Error saving discover download snapshot:', error);
        }
    }, 1000); // 1 second debounce
}

async function hydrateDiscoverDownloadsFromSnapshot() {
    /**
     * Hydrates discover downloads from backend snapshot with live status.
     * Called on page load to restore download state.
     */
    try {
        console.log('🔄 Loading discover download snapshot from backend...');

        const response = await fetch('/api/discover_downloads/hydrate');
        const data = await response.json();

        if (!data.success) {
            console.error('❌ Failed to load discover download snapshot:', data.error);
            return;
        }

        const downloads = data.downloads || {};
        const stats = data.stats || {};

        console.log(`🔄 Loaded discover snapshot: ${stats.total_downloads || 0} downloads, ${stats.active_downloads || 0} active, ${stats.completed_downloads || 0} completed`);

        if (Object.keys(downloads).length === 0) {
            console.log('ℹ️ No discover downloads to hydrate');
            return;
        }

        // Clear existing state
        discoverDownloads = {};

        // Restore discoverDownloads with hydrated data
        for (const [playlistId, downloadData] of Object.entries(downloads)) {
            discoverDownloads[playlistId] = {
                name: downloadData.name,
                type: downloadData.type,
                status: downloadData.status, // Live status from backend
                virtualPlaylistId: downloadData.virtualPlaylistId,
                imageUrl: downloadData.imageUrl,
                startTime: new Date(downloadData.startTime)
            };

            console.log(`🔄 Hydrated download: ${downloadData.name} (${downloadData.status})`);

            // Start monitoring for any in-progress downloads
            if (downloadData.status === 'in_progress') {
                console.log(`📡 Starting monitoring for: ${downloadData.name}`);
                monitorDiscoverDownload(playlistId);
            }
        }

        // Don't update UI here - it will be updated when user navigates to discover page
        // This allows hydration to work even if page loads on a different tab

        const totalDownloads = Object.keys(discoverDownloads).length;
        console.log(`✅ Successfully hydrated ${totalDownloads} discover downloads (UI will update on discover page navigation)`);

    } catch (error) {
        console.error('❌ Error hydrating discover downloads from snapshot:', error);
    }
}

// Initialize on page load
if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', initializeDiscoverDownloadBar);
} else {
    initializeDiscoverDownloadBar();
}

// ============================================================================


// ── SoulSync Discover Sync Tab ─────────────────────────────────────────

async function loadDiscoverSyncPlaylists() {
    if (discoverSyncPlaylistsLoaded) return;
    discoverSyncPlaylistsLoaded = true;
    const container = document.getElementById('discover-sync-playlist-container');
    if (!container) return;
    container.innerHTML = '<div class="playlist-placeholder">Loading Discover playlists...</div>';

    try {
        const response = await fetch('/api/discover/synced-playlists');
        const data = await response.json();

        if (!data.success || !data.playlists || data.playlists.length === 0) {
            container.innerHTML = '<div class="playlist-placeholder">No Discover playlists available. Visit the Discover page to generate playlists first.</div>';
            return;
        }

        container.innerHTML = '';

        // Show source info and empty-state hint if no playlists have data
        if (!data.has_data) {
            const hint = document.createElement('div');
            hint.className = 'discover-sync-empty-hint';
            hint.innerHTML = `
                <p>Your Discover playlists don't have any tracks yet.</p>
                <p>Go to the <strong>Discover</strong> page and let it build your playlist pool — it uses your <strong>${data.source_label || 'configured source'}</strong> data and watchlist to generate personalized playlists.</p>
            `;
            container.appendChild(hint);
        }

        data.playlists.forEach(playlist => {
            renderDiscoverSyncCard(playlist, container, data.source_label || data.source);
            // Resume polling if there's an active batch for this playlist
            if (playlist.active_batch_id && playlist.sync_status === 'syncing') {
                const btn = document.getElementById(`discover-sync-btn-${playlist.type}`);
                if (btn) { btn.disabled = true; btn.textContent = 'Syncing...'; }
                pollDiscoverBatchFromTab(playlist.type, playlist.active_batch_id, playlist.name);
            }
        });

        // Also fetch ListenBrainz playlists and add them
        try {
            // Fetch saved auto-update settings so LB toggles persist across restarts
            let lbAutoSettings = {};
            try {
                const settingsRes = await fetch('/api/discover/auto-update');
                if (settingsRes.ok) {
                    const settingsData = await settingsRes.json();
                    if (settingsData.success) lbAutoSettings = settingsData.settings || {};
                }
            } catch (_) {}

            const lbRes = await fetch('/api/discover/listenbrainz/created-for');
            if (lbRes.ok) {
                const lbData = await lbRes.json();
                if (lbData.success && lbData.playlists && lbData.playlists.length > 0) {
                    // Fetch sync history once for all LB playlists
                    let historyEntries = [];
                    try {
                        const histRes = await fetch('/api/sync/history?source=discover&limit=50');
                        if (histRes.ok) {
                            const histData = await histRes.json();
                            historyEntries = histData.entries || [];
                        }
                    } catch (_) {}

                    // Deduplicate by base name — only show the latest of each type
                    const seen = new Map();
                    for (const p of lbData.playlists) {
                        const pl = p.playlist || p;
                        const rawTitle = pl.title || 'ListenBrainz Playlist';
                        // Strip ", week of YYYY-MM-DD ..." suffix for a stable display name
                        const baseName = rawTitle.replace(/,\s*week of .+$/i, '').trim();
                        // Keep only the first (latest) for each base name
                        if (!seen.has(baseName)) seen.set(baseName, { pl, rawTitle, baseName });
                    }

                    for (const { pl, rawTitle, baseName } of seen.values()) {
                        const identifier = pl.identifier || '';
                        const mbid = identifier.split('/').pop();
                        const trackCount = pl.track_count || (pl.track || []).length;
                        // Determine icon from title
                        let icon = '🧠';
                        if (rawTitle.toLowerCase().includes('jam')) icon = '🎸';
                        else if (rawTitle.toLowerCase().includes('explor')) icon = '🔭';

                        const lbType = `listenbrainz_${mbid}`;

                        // Check sync history for this playlist by matching the base name
                        let syncStatus = 'never';
                        let lastSynced = null;
                        let matchedTracks = 0;
                        let totalSyncTracks = 0;
                        for (const entry of historyEntries) {
                            const eName = entry.playlist_name || '';
                            if (eName === baseName || eName.startsWith(baseName)) {
                                syncStatus = 'synced';
                                lastSynced = entry.completed_at || entry.started_at || entry.created_at;
                                matchedTracks = entry.tracks_found || 0;
                                totalSyncTracks = entry.total_tracks || 0;
                                break;
                            }
                        }

                        renderDiscoverSyncCard({
                            type: lbType,
                            name: baseName,
                            description: '',
                            icon: icon,
                            track_count: trackCount,
                            sync_status: syncStatus,
                            last_synced: lastSynced,
                            matched_tracks: matchedTracks,
                            total_sync_tracks: totalSyncTracks,
                            auto_update: !!lbAutoSettings[lbType],
                            virtual_id: `discover_listenbrainz_${mbid}`,
                            _lb_mbid: mbid,
                        }, container, 'ListenBrainz');
                    }
                }
            }
        } catch (lbErr) {
            console.warn('Could not load ListenBrainz playlists for discover sync tab:', lbErr);
        }

    } catch (error) {
        console.error('Error loading discover sync playlists:', error);
        container.innerHTML = '<div class="playlist-placeholder">Error loading Discover playlists.</div>';
    }
}

function renderDiscoverSyncCard(playlist, container, sourceLabel) {
    const card = document.createElement('div');
    const isEmpty = playlist.track_count === 0;
    card.className = `discover-sync-card${isEmpty ? ' discover-sync-card-empty' : ''}`;
    card.id = `discover-sync-card-${playlist.type}`;

    const lastSyncedText = playlist.last_synced
        ? `Last synced ${timeAgo(playlist.last_synced)}`
        : 'Never synced';

    const statusClass = playlist.sync_status === 'syncing' ? 'syncing' :
                         playlist.sync_status === 'synced' ? 'synced' : 'not-synced';
    let statusText = playlist.sync_status === 'syncing' ? 'Syncing...' :
                     playlist.sync_status === 'synced' ? 'Synced' : 'Not synced';

    // Show matched/total counts if available (only when matched > 0, meaning completion was recorded)
    if (playlist.sync_status === 'synced' && playlist.matched_tracks > 0 && playlist.total_sync_tracks > 0) {
        statusText = `Synced ${playlist.matched_tracks}/${playlist.total_sync_tracks}`;
    }

    const trackLabel = isEmpty ? 'No tracks yet' : `${playlist.track_count} tracks`;

    card.innerHTML = `
        <div class="discover-sync-card-icon">${_esc(playlist.icon)}</div>
        <div class="discover-sync-card-info">
            <div class="discover-sync-card-name">${_esc(playlist.name)}
                <span class="discover-sync-card-meta-inline">
                    <span class="discover-sync-source-badge">${_esc(sourceLabel || 'unknown')}</span>
                    <span class="discover-sync-separator">\u00b7</span>
                    <span class="discover-sync-track-count">${_esc(trackLabel)}</span>
                    <span class="discover-sync-separator">\u00b7</span>
                    <span class="discover-sync-status ${statusClass}">${_esc(statusText)}</span>
                    <span class="discover-sync-separator">\u00b7</span>
                    <span class="discover-sync-last-synced">${_esc(lastSyncedText)}</span>
                </span>
            </div>
        </div>
        <div class="discover-sync-card-actions">
            <div class="discover-sync-toggle-wrapper" title="${isEmpty ? 'No tracks available — visit Discover first' : 'Keep this playlist updated automatically'}">
                <label class="discover-sync-toggle-label">Keep updated</label>
                <label class="discover-sync-toggle">
                    <input type="checkbox" class="discover-auto-update-toggle" ${playlist.auto_update ? 'checked' : ''} ${isEmpty ? 'disabled' : ''}>
                    <span class="discover-sync-toggle-slider"></span>
                </label>
            </div>
            <div class="discover-sync-toggle-wrapper" title="Coming soon: download any available quality for this batch, even if it's below your global quality profile. Useful for rotating discover playlists where quantity matters more than quality.">
                <label class="discover-sync-toggle-label" style="opacity:0.5">Any Quality</label>
                <label class="discover-sync-toggle" style="opacity:0.5;cursor:not-allowed">
                    <input type="checkbox" class="discover-any-quality-toggle" disabled>
                    <span class="discover-sync-toggle-slider"></span>
                </label>
            </div>
            <button class="discover-sync-btn"
                    ${playlist.sync_status === 'syncing' || isEmpty ? 'disabled' : ''}>
                \u27f3 Sync Now
            </button>
        </div>
    `;

    // Bind event listeners instead of inline handlers (avoids XSS from playlist names)
    const autoUpdateToggle = card.querySelector('.discover-auto-update-toggle');
    if (autoUpdateToggle) {
        autoUpdateToggle.addEventListener('change', function() {
            toggleDiscoverAutoUpdate(playlist.type, this.checked);
        });
    }

    const anyQualityToggle = card.querySelector('.discover-any-quality-toggle');
    if (anyQualityToggle) {
        anyQualityToggle.id = `discover-any-quality-${playlist.type}`;
    }

    const syncButton = card.querySelector('.discover-sync-btn');
    if (syncButton) {
        syncButton.id = `discover-sync-btn-${playlist.type}`;
        syncButton.addEventListener('click', () => syncDiscoverPlaylistFromTab(playlist.type, playlist.name));
    }

    // Make the icon + info area clickable to view tracks
    if (!isEmpty) {
        const clickArea = card.querySelector('.discover-sync-card-info');
        const iconArea = card.querySelector('.discover-sync-card-icon');
        [clickArea, iconArea].forEach(el => {
            el.style.cursor = 'pointer';
            el.addEventListener('click', () => openDiscoverPlaylistModal(playlist.type, playlist.name, playlist.icon));
        });
    }

    container.appendChild(card);
}

async function toggleDiscoverAutoUpdate(playlistType, enabled) {
    try {
        const response = await fetch('/api/discover/auto-update', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ playlist_type: playlistType, enabled: enabled })
        });
        const data = await response.json();
        if (data.success) {
            showToast(`Auto-update ${enabled ? 'enabled' : 'disabled'} for ${playlistType.replace(/_/g, ' ')}`, 'success');
        } else {
            showToast(`Failed to update setting: ${data.error}`, 'error');
        }
    } catch (error) {
        console.error('Error toggling auto-update:', error);
        showToast('Failed to update setting', 'error');
    }
}

const _discoverSyncQueue = [];
let _discoverSyncRunning = false;

async function syncDiscoverPlaylistFromTab(playlistType, playlistName) {
    // Serialize sync operations to avoid concurrent backend contention
    return new Promise((resolve) => {
        _discoverSyncQueue.push({ playlistType, playlistName, resolve });
        _processDiscoverSyncQueue();
    });
}

async function _processDiscoverSyncQueue() {
    if (_discoverSyncRunning || _discoverSyncQueue.length === 0) return;
    _discoverSyncRunning = true;
    const { playlistType, playlistName, resolve } = _discoverSyncQueue.shift();
    try {
        await _doSyncDiscoverPlaylist(playlistType, playlistName);
    } finally {
        _discoverSyncRunning = false;
        resolve();
        _processDiscoverSyncQueue();
    }
}

async function _doSyncDiscoverPlaylist(playlistType, playlistName) {
    const btn = document.getElementById(`discover-sync-btn-${playlistType}`);
    if (btn) {
        btn.disabled = true;
        btn.textContent = 'Syncing...';
    }

    try {
        let tracks = [];

        if (playlistType === 'build_playlist') {
            // Build Playlist tracks are assembled client-side; no API endpoint.
            tracks = (typeof buildPlaylistTracks !== 'undefined' && buildPlaylistTracks) || [];
        } else {
            // Use unified URL helper (handles ListenBrainz + standard discover types)
            const apiUrl = _discoverPlaylistApiUrl(playlistType);
            if (apiUrl) {
                const tracksResponse = await fetch(apiUrl);
                if (tracksResponse.ok) {
                    const data = await tracksResponse.json();
                    tracks = data.tracks || [];
                }
            }
        }

        if (!tracks.length) {
            showToast(`No tracks available for ${playlistName}. Visit the Discover page first.`, 'warning');
            if (btn) { btn.disabled = false; btn.textContent = '\u27f3 Sync Now'; }
            return;
        }

        const syncTracks = tracks.map(track => {
            if (track.track_data_json) {
                const t = track.track_data_json;
                if (t.artists && Array.isArray(t.artists)) {
                    t.artists = t.artists.map(a => a.name || a);
                }
                return t;
            }
            return {
                id: track.spotify_track_id || track.track_id || '',
                name: track.track_name || track.name || '',
                artists: [track.artist_name || 'Unknown Artist'],
                album: track.album_name || '',
                duration_ms: track.duration_ms || 0,
                image_url: track.album_cover_url || track.image_url || ''
            };
        });

        const virtualPlaylistId = `discover_${playlistType}`;

        // Use the download batch endpoint directly so the batch is labeled
        // as "Discover" instead of going through sync → wishlist → "Wishlist" batch.
        const bodyPayload = {
            tracks: syncTracks,
            playlist_name: playlistName
        };

        const batchResponse = await fetch(`/api/playlists/${virtualPlaylistId}/start-missing-process`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(bodyPayload)
        });

        const result = await batchResponse.json();
        if (result.success) {
            showToast(`Downloading ${playlistName} (${syncTracks.length} tracks)...`, 'info');
            const card = document.getElementById(`discover-sync-card-${playlistType}`);
            if (card) {
                const statusEl = card.querySelector('.discover-sync-status');
                if (statusEl) {
                    statusEl.className = 'discover-sync-status syncing';
                    statusEl.textContent = 'Downloading...';
                }
            }
            // Poll the download batch status
            pollDiscoverBatchFromTab(playlistType, result.batch_id, playlistName);
        } else {
            showToast(`Download failed: ${result.error || 'Unknown error'}`, 'error');
            if (btn) { btn.disabled = false; btn.textContent = '\u27f3 Sync Now'; }
        }
    } catch (error) {
        console.error(`Error syncing ${playlistName}:`, error);
        showToast(`Failed to sync ${playlistName}`, 'error');
        if (btn) { btn.disabled = false; btn.textContent = '\u27f3 Sync Now'; }
    }
}

function pollDiscoverSyncFromTab(playlistType, virtualPlaylistId, playlistName) {
    const pollInterval = setInterval(async () => {
        try {
            const resp = await fetch(`/api/sync/status/${virtualPlaylistId}`);
            if (!resp.ok) { clearInterval(pollInterval); return; }
            const data = await resp.json();

            if (data.status === 'finished' || data.status === 'error') {
                clearInterval(pollInterval);
                const btn = document.getElementById(`discover-sync-btn-${playlistType}`);
                if (btn) { btn.disabled = false; btn.textContent = '\u27f3 Sync Now'; }

                const card = document.getElementById(`discover-sync-card-${playlistType}`);
                if (card) {
                    const statusEl = card.querySelector('.discover-sync-status');
                    if (statusEl) {
                        if (data.status === 'finished') {
                            const progress = data.progress || data.result || {};
                            const matched = progress.matched_tracks || 0;
                            const total = progress.total_tracks || 0;
                            statusEl.className = 'discover-sync-status synced';
                            statusEl.textContent = matched > 0 && total > 0 ? `Synced ${matched}/${total}` : 'Synced';
                        } else {
                            statusEl.className = 'discover-sync-status not-synced';
                            statusEl.textContent = 'Failed';
                        }
                    }
                    const lastSyncedEl = card.querySelector('.discover-sync-last-synced');
                    if (lastSyncedEl && data.status === 'finished') {
                        lastSyncedEl.textContent = 'Last synced just now';
                    }
                }

                if (data.status === 'finished') {
                    showToast(`${playlistName} synced successfully!`, 'success');
                } else {
                    showToast(`${playlistName} sync failed`, 'error');
                }
            }
        } catch (error) {
            clearInterval(pollInterval);
        }
    }, 2000);
}

function pollDiscoverBatchFromTab(playlistType, batchId, playlistName) {
    // Clear any existing poller for this playlist type
    if (discoverSyncPollers[playlistType]) {
        clearInterval(discoverSyncPollers[playlistType]);
        delete discoverSyncPollers[playlistType];
    }

    let ticks = 0;
    const maxTicks = 600; // 30 min at 3s intervals

    const pollInterval = setInterval(async () => {
        ticks++;
        // Stall guard — stop polling after maxTicks or if the card is no longer in DOM
        if (ticks > maxTicks || !document.getElementById(`discover-sync-card-${playlistType}`)) {
            clearInterval(pollInterval);
            delete discoverSyncPollers[playlistType];
            return;
        }
        try {
            const resp = await fetch(`/api/playlists/${batchId}/download_status`);
            if (!resp.ok) { clearInterval(pollInterval); return; }
            const data = await resp.json();
            const phase = data.phase || data.status;

            if (phase === 'complete' || phase === 'error' || phase === 'cancelled') {
                clearInterval(pollInterval);
                delete discoverSyncPollers[playlistType];
                const btn = document.getElementById(`discover-sync-btn-${playlistType}`);
                if (btn) { btn.disabled = false; btn.textContent = '\u27f3 Sync Now'; }

                // Extract matched/total from analysis_results
                const analysisResults = data.analysis_results || [];
                const totalTracks = analysisResults.length;
                const matchedTracks = analysisResults.filter(r => r.found).length;
                const tasks = data.tasks || [];
                const downloaded = tasks.filter(t => t.status === 'completed').length;
                const failed = tasks.filter(t => t.status === 'failed' || t.status === 'not_found').length;

                const card = document.getElementById(`discover-sync-card-${playlistType}`);
                const syncedCount = matchedTracks + downloaded;
                if (card) {
                    const statusEl = card.querySelector('.discover-sync-status');
                    if (statusEl) {
                        statusEl.className = `discover-sync-status ${phase === 'complete' ? 'synced' : 'not-synced'}`;
                        if (phase === 'complete' && totalTracks > 0) {
                            statusEl.textContent = `Synced ${syncedCount}/${totalTracks}`;
                        } else {
                            statusEl.textContent = phase === 'complete' ? 'Synced' : (phase === 'cancelled' ? 'Cancelled' : 'Failed');
                        }
                    }
                    const lastSyncedEl = card.querySelector('.discover-sync-last-synced');
                    if (lastSyncedEl && phase === 'complete') {
                        lastSyncedEl.textContent = 'Last synced just now';
                    }
                }

                if (phase === 'complete') {
                    if (totalTracks > 0) {
                        const missing = totalTracks - syncedCount;
                        let msg = `${playlistName}: ${syncedCount}/${totalTracks} in library`;
                        if (downloaded > 0) msg += `, ${downloaded} downloaded`;
                        if (failed > 0) msg += `, ${failed} failed`;
                        if (missing === 0) msg += ' - all owned!';
                        showToast(msg, 'success');
                    } else {
                        showToast(`${playlistName} download complete!`, 'success');
                    }
                } else if (phase !== 'cancelled') {
                    showToast(`${playlistName} download failed`, 'error');
                }
            }
        } catch (error) {
            clearInterval(pollInterval);
            delete discoverSyncPollers[playlistType];
        }
    }, 3000);

    // Register so page-leave cleanup can clear it
    discoverSyncPollers[playlistType] = pollInterval;
}

/**
 * Map a discover playlist type to its API endpoint for fetching tracks.
 */
function _discoverPlaylistApiUrl(playlistType) {
    // ListenBrainz playlists
    if (playlistType.startsWith('listenbrainz_')) {
        const mbid = playlistType.replace('listenbrainz_', '');
        return `/api/discover/listenbrainz/playlist/${mbid}`;
    }
    const map = {
        release_radar: '/api/discover/release-radar',
        discovery_weekly: '/api/discover/weekly',
        seasonal_playlist: '/api/discover/seasonal/current-playlist',
        popular_picks: '/api/discover/personalized/popular-picks',
        hidden_gems: '/api/discover/personalized/hidden-gems',
        discovery_shuffle: '/api/discover/personalized/discovery-shuffle',
        familiar_favorites: '/api/discover/personalized/familiar-favorites',
    };
    return map[playlistType] || null;
}

/**
 * Open a modal showing all tracks in a Discover playlist (mirrored-modal style).
 */
async function openDiscoverPlaylistModal(playlistType, playlistName, icon) {
    const apiUrl = _discoverPlaylistApiUrl(playlistType);
    if (!apiUrl) { showToast('Unknown playlist type', 'error'); return; }

    showLoadingOverlay(`Loading ${playlistName}...`);
    try {
        const res = await fetch(apiUrl);
        const data = await res.json();
        const tracks = data.tracks || [];

        hideLoadingOverlay();

        if (!tracks.length) {
            showToast(`No tracks in ${playlistName}. Visit the Discover page first.`, 'warning');
            return;
        }

        // Remove any existing modal
        const old = document.getElementById('discover-playlist-modal');
        if (old) old.remove();

        const overlay = document.createElement('div');
        overlay.id = 'discover-playlist-modal';
        overlay.className = 'mirrored-modal-overlay';

        const trackRows = tracks.map((t, idx) => {
            const name = t.track_name || t.name || '';
            const artist = t.artist_name || (t.artists ? (Array.isArray(t.artists) ? t.artists.map(a => a.name || a).join(', ') : t.artists) : '');
            const album = t.album_name || t.album || '';
            const dur = t.duration_ms ? `${Math.floor(t.duration_ms / 60000)}:${String(Math.floor((t.duration_ms % 60000) / 1000)).padStart(2, '0')}` : '';
            const coverUrl = t.album_cover_url || '';
            const coverHtml = coverUrl
                ? `<img src="${_escAttr(coverUrl)}" alt="" class="discover-modal-track-img" loading="lazy">`
                : `<div class="discover-modal-track-img-placeholder"></div>`;
            return `<div class="mirrored-track-row">
                <span class="track-pos">${idx + 1}</span>
                <span class="track-cover">${coverHtml}</span>
                <span class="track-title">${_esc(name)}</span>
                <span class="track-artist">${_esc(artist)}</span>
                <span class="track-album">${_esc(album)}</span>
                <span class="track-duration">${dur}</span>
            </div>`;
        }).join('');

        overlay.innerHTML = `
            <div class="mirrored-modal">
                <div class="mirrored-modal-header">
                    <div class="mirrored-modal-hero">
                        <div class="mirrored-modal-hero-icon discover">${icon || '🎵'}</div>
                        <div class="mirrored-modal-hero-info">
                            <h2 class="mirrored-modal-hero-title">${_esc(playlistName)}</h2>
                            <div class="mirrored-modal-hero-subtitle">
                                <span class="mirrored-modal-hero-badge">discover</span>
                                <span>${tracks.length} tracks</span>
                            </div>
                        </div>
                    </div>
                    <span class="mirrored-modal-close" onclick="closeDiscoverPlaylistModal()">&times;</span>
                </div>
                <div class="mirrored-modal-tracks">
                    <div class="mirrored-track-header">
                        <span>#</span><span></span><span>Track</span><span>Artist</span><span>Album</span><span style="text-align:right">Time</span>
                    </div>
                    ${trackRows}
                </div>
                <div class="mirrored-modal-footer">
                    <div class="mirrored-modal-footer-left"></div>
                    <div class="mirrored-modal-footer-right" style="display:flex;gap:10px;">
                        <button class="mirrored-btn-close" onclick="closeDiscoverPlaylistModal()">Close</button>
                        <button class="mirrored-btn-discover" onclick="closeDiscoverPlaylistModal(); syncDiscoverPlaylistFromTab('${_escAttr(playlistType)}', '${_escAttr(playlistName)}')">Sync Now</button>
                    </div>
                </div>
            </div>
        `;

        overlay.addEventListener('click', e => { if (e.target === overlay) closeDiscoverPlaylistModal(); });
        document.body.appendChild(overlay);
    } catch (err) {
        hideLoadingOverlay();
        showToast(`Error loading ${playlistName}: ${err.message}`, 'error');
    }
}

function closeDiscoverPlaylistModal() {
    const m = document.getElementById('discover-playlist-modal');
    if (m) m.remove();
}
