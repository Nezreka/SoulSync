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
let personalizedPopularPicks = [];
let personalizedHiddenGems = [];
let personalizedDailyMixes = [];
let personalizedDiscoveryShuffle = [];
let buildPlaylistSelectedArtists = [];

async function loadDiscoverPage() {
    console.log('Loading discover page...');

    // Load all sections
    await Promise.all([
        loadDiscoverHero(),
        loadYourArtists(),
        loadYourAlbums(),
        loadDiscoverRecentReleases(),
        loadSeasonalContent(),  // Seasonal discovery
        // loadPersonalizedDailyMixes(),  // NEW: Daily Mix playlists (HIDDEN)
        loadDiscoverReleaseRadar(),
        loadDiscoverWeekly(),
        loadPersonalizedPopularPicks(),  // NEW: Popular picks from discovery pool
        loadPersonalizedHiddenGems(),  // NEW: Hidden gems from discovery pool
        loadDiscoveryShuffle(),  // NEW: Discovery Shuffle
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
        discographyBtn.href = buildArtistDetailPath(artistId, artist.source || null);
        // Keep the source on the link so source-only hero artists resolve to
        // the correct artist-detail URL instead of being treated as library IDs.
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
let _recommendedArtistsSource = null;

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
        renderRecommendedArtistsModal(modal, _recommendedArtistsCache, _recommendedArtistsSource);
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

        // Phase 2: Enrich with images/genres progressively in batches of 50
        // Skip artists that already have cached metadata from the initial response
        const source = data.source || 'spotify';
        // Render cards immediately with fallback images
        _recommendedArtistsCache = data.artists;
        _recommendedArtistsSource = source;
        renderRecommendedArtistsModal(modal, data.artists, source);

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

function renderRecommendedArtistsModal(modal, artists, source = null) {
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
                const artistSource = artist.source || source || _recommendedArtistsSource || '';
        return `
                            <div class="recommended-artist-card"
                                 data-artist-name="${escapeHtml(artist.artist_name).toLowerCase()}"
                                 data-artist-id="${artist.artist_id}"
                                 data-artist-source="${escapeHtml(artistSource)}">
                                <button class="recommended-card-watchlist-btn"
                                        data-artist-id="${artist.artist_id}"
                                        data-artist-name="${escapeHtml(artist.artist_name)}">
                                    Add to Watchlist
                                </button>
                                <a class="recommended-card-link" href="${buildArtistDetailPath(artist.artist_id, artistSource || null)}"
                                   onclick="closeRecommendedArtistsModal()"
                                   style="display:block;text-decoration:none;color:inherit;">
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
                                </a>
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

function showDiscoverHeroEmpty() {
    const titleEl = document.getElementById('discover-hero-title');
    const subtitleEl = document.getElementById('discover-hero-subtitle');

    if (titleEl) titleEl.textContent = 'No Recommendations Yet';
    if (subtitleEl) subtitleEl.textContent = 'Run a watchlist scan to generate personalized recommendations';
}

// Recent Releases — first section migrated to the shared
// `createDiscoverSectionController`. The controller owns the
// loading / empty / error / refresh lifecycle that every other
// discover section currently re-implements by hand. This function
// stays as the public entry-point so existing callers don't change;
// internally it builds (or reuses) the controller and triggers a
// load. See `discover-section-controller.js` for the contract.
let _recentReleasesCtrl = null;

function _renderRecentReleaseCard(album, index) {
    const coverUrl = album.album_cover_url || '/static/placeholder-album.png';
    return `
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
}

async function loadDiscoverRecentReleases() {
    if (!_recentReleasesCtrl) {
        _recentReleasesCtrl = createDiscoverSectionController({
            id: 'recent-releases',
            contentEl: '#recent-releases-carousel',
            fetchUrl: '/api/discover/recent-releases',
            extractItems: (data) => data.albums || [],
            renderItems: (items) => {
                // Module-level `discoverRecentAlbums` is what the click
                // handler reads to look up the album by index. Keep it
                // in sync so `openDownloadModalForRecentAlbum(index)`
                // still resolves correctly after re-renders.
                discoverRecentAlbums = items;
                return items.map((album, i) => _renderRecentReleaseCard(album, i)).join('');
            },
            loadingMessage: 'Loading recent releases...',
            emptyMessage: 'No recent releases found',
            errorMessage: 'Failed to load recent releases',
            showErrorToast: true,
        });
    }
    return _recentReleasesCtrl.load();
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

let _yourAlbumsCtrl = null;

async function loadYourAlbums() {
    if (!_yourAlbumsCtrl) {
        _yourAlbumsCtrl = createDiscoverSectionController({
            id: 'your-albums',
            sectionEl: '#your-albums-section',
            contentEl: '#your-albums-grid',
            fetchUrl: '/api/discover/your-albums?page=1&per_page=48&status=all',
            extractItems: (data) => data.albums || [],
            // Truly empty (no data + not stale) \u2192 hide the whole section
            // (matches the legacy "Nothing to show yet" early-return). The
            // outer hideWhenEmpty + sectionEl handle the visibility flip.
            isEmpty: (items, data) => {
                const total = (data && data.stats && data.stats.total) || 0;
                return total === 0 && !data.stale;
            },
            hideWhenEmpty: true,
            // Stale + no albums yet \u2192 show the "fetching from connected
            // services" UI and start the poller. Fires before isEmpty.
            isStale: (items, data) => {
                const total = (data && data.stats && data.stats.total) || 0;
                return Boolean(data && data.stale) && total === 0;
            },
            renderStale: () =>
                '<div class="discover-loading"><div class="loading-spinner"></div><p>Fetching your albums from connected services...</p></div>',
            onStale: () => _pollYourAlbums(),
            // Side-effects against sibling DOM (subtitle / filters /
            // download button) belong here, not in renderItems.
            onSuccess: (data) => {
                const subtitle = document.getElementById('your-albums-subtitle');
                if (subtitle && data.stats) {
                    const s = data.stats;
                    subtitle.textContent = `${s.total} albums \u00B7 ${s.owned} owned \u00B7 ${s.missing} missing`;
                }
                const totalCount = (data.stats && data.stats.total) || 0;
                const filters = document.getElementById('your-albums-filters');
                if (filters && totalCount > 0) filters.style.display = '';
                const downloadBtn = document.getElementById('your-albums-download-btn');
                if (downloadBtn && data.stats && data.stats.missing > 0) downloadBtn.style.display = '';
            },
            // Renderer delegates to the existing grid renderer, which
            // writes its own DOM into `#your-albums-grid`. `manualDom`
            // tells the controller not to clobber it.
            manualDom: true,
            renderItems: (items, data) => {
                yourAlbums = items;
                yourAlbumsTotal = data.total || 0;
                yourAlbumsPage = 1;
                _renderYourAlbumsGrid(yourAlbums);
                _renderYourAlbumsPagination(yourAlbumsTotal, yourAlbumsPage);
            },
            errorMessage: 'Failed to load your albums',
            verboseErrors: true,
            showErrorToast: true,
        });
    }
    return _yourAlbumsCtrl.load();
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
        // Per-source dispatch: open with whichever source has an ID for
        // this album. For pure-Discogs collection items (no Spotify/
        // Deezer match), dispatch goes straight to Discogs so the
        // modal opens with Discogs context (vinyl/CD release detail,
        // tracklist from Discogs). For Spotify saved albums (no
        // discogs id), goes to Spotify. For multi-source albums
        // (album exists in BOTH Spotify saved and Discogs collection,
        // rare), tries streaming sources first since they have
        // tracklists with proper IDs ready for download.
        let albumData = null;
        const nameParams = new URLSearchParams({ name: album.album_name || '', artist: album.artist_name || '' });
        const discogsId = album.discogs_release_id || album.discogs_id;

        const trySources = [];
        if (album.spotify_album_id) trySources.push(['spotify', album.spotify_album_id]);
        if (album.deezer_album_id) trySources.push(['deezer', album.deezer_album_id]);
        if (album.tidal_album_id) trySources.push(['tidal', album.tidal_album_id]);
        if (discogsId) trySources.push(['discogs', discogsId]);

        for (const [src, id] of trySources) {
            const r = await fetch(`/api/discover/album/${src}/${id}?${nameParams}`);
            if (r.ok) {
                albumData = await r.json();
                if (albumData && albumData.tracks && albumData.tracks.length > 0) break;
                albumData = null;  // empty payload — try next
            }
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
        { id: 'discogs', label: 'Discogs', icon: '\uD83D\uDCBF' },
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

// Source-id → human label + setup hint shown when user tries to enable
// a disconnected source. Without this, the toggle silently bailed and
// users saw no feedback — just a non-responsive switch.
const _YAA_DISCONNECTED_HINTS = {
    spotify: 'Spotify not connected — log in at Settings → Connections first',
    tidal: 'Tidal not connected — set up Tidal in Settings → Connections first',
    deezer: 'Deezer not connected — log in or set ARL token at Settings → Connections first',
    discogs: 'Discogs not connected — paste your personal access token at Settings → Connections first',
};

function _yaaShowDisconnectedHint(id) {
    const msg = _YAA_DISCONNECTED_HINTS[id]
        || `${id} not connected — set it up in Settings → Connections first`;
    if (typeof showToast === 'function') showToast(msg, 'warning');
}

function _yaaSourceRowClick(id) {
    const row = document.querySelector(`.ya-source-row[data-yaa-source="${id}"]`);
    if (row && row.classList.contains('disconnected')) {
        _yaaShowDisconnectedHint(id);
        return;
    }
    _yaaSourceToggle(id);
}
function _yaaSourceToggle(id) {
    const row = document.querySelector(`.ya-source-row[data-yaa-source="${id}"]`);
    if (row && row.classList.contains('disconnected')) {
        _yaaShowDisconnectedHint(id);
        return;
    }
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
            const sourceNames = { spotify: 'Spotify', tidal: 'Tidal', deezer: 'Deezer', discogs: 'Discogs' };
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
    // Opens the same selectable-grid modal pattern used by Download
    // Discography on the library page. User picks which missing albums
    // they want, clicks Add to Wishlist, each album's tracks get
    // resolved + added to the wishlist for the existing auto-download
    // processor to pick up. Replaces the prior per-album direct-download
    // loop which was silently failing — actual downloads should go
    // through the wishlist queue, not bypass it.
    try {
        const resp = await fetch('/api/discover/your-albums?page=1&per_page=1000&status=missing');
        const data = await resp.json();
        if (!data.success || !data.albums || data.albums.length === 0) {
            showToast('No missing albums to download', 'info');
            return;
        }
        const missing = data.albums.filter(a => !a.in_library);
        if (missing.length === 0) {
            showToast('All albums are already in your library!', 'success');
            return;
        }
        _openYourAlbumsBatchModal(missing);
    } catch (e) {
        console.error('Error loading missing your albums:', e);
        showToast(`Error: ${e.message}`, 'error');
    }
}


// Map a Your Albums row to the single best source-id the
// /api/artist/<id>/download-discography endpoint can resolve. Each row
// in the missing list typically only has one populated source-id (the
// service it was saved on), so this is just a priority pick.
function _yourAlbumsPickSource(album) {
    if (album.spotify_album_id) return { id: String(album.spotify_album_id), source: 'spotify' };
    if (album.deezer_album_id) return { id: String(album.deezer_album_id), source: 'deezer' };
    if (album.tidal_album_id) return { id: String(album.tidal_album_id), source: 'tidal' };
    const discogsId = album.discogs_release_id || album.discogs_id;
    if (discogsId) return { id: String(discogsId), source: 'discogs' };
    return null;
}


function _openYourAlbumsBatchModal(missingAlbums) {
    // Reuses the .discog-modal styling from the library Download
    // Discography flow — same checkboxes, same Select All / Deselect
    // All semantics, same footer. Single difference: each card carries
    // its own artist+source (multi-artist) instead of all being one
    // artist's discography.
    const existing = document.getElementById('your-albums-batch-modal-overlay');
    if (existing) existing.remove();

    // Stash the source-id picks on the cards so the submit handler
    // can build the per-album payload without re-mapping the array.
    const rows = missingAlbums
        .map((a, i) => ({ ...a, _src: _yourAlbumsPickSource(a), _index: i }))
        .filter(a => a._src);  // Skip albums with no usable source-id

    if (rows.length === 0) {
        showToast('No missing albums have a usable source ID to resolve', 'warning');
        return;
    }

    const overlay = document.createElement('div');
    overlay.className = 'discog-modal-overlay';
    overlay.id = 'your-albums-batch-modal-overlay';
    overlay.innerHTML = `
        <div class="discog-modal">
            <div class="discog-modal-hero">
                <div class="discog-modal-hero-overlay"></div>
                <div class="discog-modal-hero-content">
                    <h2 class="discog-modal-title">Add Missing Albums to Wishlist</h2>
                    <p class="discog-modal-artist">${rows.length} albums missing from your library</p>
                </div>
                <button class="discog-modal-close" onclick="_closeYourAlbumsBatchModal()">&times;</button>
            </div>
            <div class="discog-filter-bar">
                <div class="discog-filters"></div>
                <div class="discog-select-actions">
                    <button class="discog-select-btn" onclick="_yourAlbumsBatchSelectAll(true)">Select All</button>
                    <button class="discog-select-btn" onclick="_yourAlbumsBatchSelectAll(false)">Deselect All</button>
                </div>
            </div>
            <div class="discog-grid" id="your-albums-batch-grid">
                ${rows.map((r, i) => _renderYourAlbumsBatchCard(r, i)).join('')}
            </div>
            <div class="discog-progress" id="your-albums-batch-progress" style="display:none;"></div>
            <div class="discog-footer" id="your-albums-batch-footer">
                <div class="discog-footer-info" id="your-albums-batch-footer-info"></div>
                <div class="discog-footer-actions">
                    <button class="discog-cancel-btn" onclick="_closeYourAlbumsBatchModal()">Cancel</button>
                    <button class="discog-submit-btn" id="your-albums-batch-submit-btn">
                        <span class="discog-submit-icon">⬇</span>
                        <span id="your-albums-batch-submit-text">Add to Wishlist</span>
                    </button>
                </div>
            </div>
        </div>
    `;

    document.body.appendChild(overlay);
    // Stash row data on the overlay for the submit handler — keeps the
    // multi-artist source info available without re-fetching.
    overlay._yourAlbumsRows = rows;

    requestAnimationFrame(() => overlay.classList.add('visible'));
    _updateYourAlbumsBatchFooterCount();

    document.getElementById('your-albums-batch-submit-btn')?.addEventListener('click', (e) => {
        e.stopPropagation();
        _startYourAlbumsBatchAddToWishlist();
    });
}


function _renderYourAlbumsBatchCard(row, index) {
    const albumName = row.album_name || '';
    const artistName = row.artist_name || '';
    const year = row.release_date ? row.release_date.substring(0, 4) : '';
    const tracks = row.total_tracks || 0;
    const img = row.image_url || '';
    const src = row._src?.source || '';
    return `
        <label class="discog-card" data-type="album" style="animation-delay:${index * 0.03}s">
            <input type="checkbox" class="your-albums-batch-cb"
                   data-row-index="${row._index}" data-tracks="${tracks}" checked
                   onchange="_updateYourAlbumsBatchFooterCount()">
            <div class="discog-card-art">
                ${img ? `<img src="${escapeHtml(img)}" alt="" loading="lazy">` : '<div class="discog-card-art-placeholder">🎵</div>'}
            </div>
            <div class="discog-card-info">
                <div class="discog-card-title">${escapeHtml(albumName)}</div>
                <div class="discog-card-meta">${escapeHtml(artistName)}${year ? ' · ' + year : ''}${tracks ? ' · ' + tracks + ' tracks' : ''}${src ? ' · ' + src : ''}</div>
            </div>
            <div class="discog-card-check"></div>
        </label>
    `;
}


function _yourAlbumsBatchSelectAll(select) {
    document.querySelectorAll('.your-albums-batch-cb').forEach(cb => {
        if (cb.closest('.discog-card').style.display !== 'none') cb.checked = select;
    });
    _updateYourAlbumsBatchFooterCount();
}


function _updateYourAlbumsBatchFooterCount() {
    const checked = document.querySelectorAll('.your-albums-batch-cb:checked');
    let releases = 0, tracks = 0;
    checked.forEach(cb => {
        if (cb.closest('.discog-card').style.display !== 'none') {
            releases++;
            tracks += parseInt(cb.dataset.tracks) || 0;
        }
    });
    const info = document.getElementById('your-albums-batch-footer-info');
    const btn = document.getElementById('your-albums-batch-submit-text');
    if (info) info.textContent = `${releases} album${releases !== 1 ? 's' : ''}${tracks ? ' · ' + tracks + ' tracks' : ''}`;
    if (btn) btn.textContent = releases > 0 ? `Add ${releases} to Wishlist` : 'Select albums';
    const submitBtn = document.getElementById('your-albums-batch-submit-btn');
    if (submitBtn) submitBtn.disabled = releases === 0;
}


function _closeYourAlbumsBatchModal() {
    const overlay = document.getElementById('your-albums-batch-modal-overlay');
    if (overlay) {
        overlay.classList.remove('visible');
        setTimeout(() => overlay.remove(), 200);
    }
}


async function _startYourAlbumsBatchAddToWishlist() {
    const overlay = document.getElementById('your-albums-batch-modal-overlay');
    if (!overlay) return;
    const rows = overlay._yourAlbumsRows || [];

    // Collect selected row indices from the checked checkboxes.
    const selectedRowIndices = [];
    document.querySelectorAll('.your-albums-batch-cb:checked').forEach(cb => {
        if (cb.closest('.discog-card').style.display !== 'none') {
            selectedRowIndices.push(parseInt(cb.dataset.rowIndex));
        }
    });
    const selected = rows.filter(r => selectedRowIndices.includes(r._index));
    if (selected.length === 0) return;

    // Switch to progress view.
    const grid = document.getElementById('your-albums-batch-grid');
    const progress = document.getElementById('your-albums-batch-progress');
    const footer = document.getElementById('your-albums-batch-footer');
    const filterBar = overlay.querySelector('.discog-filter-bar');

    if (grid) grid.style.display = 'none';
    if (filterBar) filterBar.style.display = 'none';
    if (progress) {
        progress.style.display = '';
        progress.innerHTML = '';
    }

    selected.forEach(row => {
        const item = document.createElement('div');
        item.className = 'discog-progress-item active';
        item.id = `your-albums-batch-prog-${row._src.source}-${row._src.id}`;
        item.innerHTML = `
            <div class="discog-prog-art">${row.image_url ? `<img src="${escapeHtml(row.image_url)}">` : '🎵'}</div>
            <div class="discog-prog-info">
                <div class="discog-prog-title">${escapeHtml(row.album_name || '')}</div>
                <div class="discog-prog-status">Waiting...</div>
            </div>
            <div class="discog-prog-icon"><div class="discog-spinner"></div></div>
        `;
        progress.appendChild(item);
    });

    const submitBtn = document.getElementById('your-albums-batch-submit-btn');
    if (submitBtn) submitBtn.style.display = 'none';
    if (footer) {
        const info = document.getElementById('your-albums-batch-footer-info');
        if (info) info.textContent = 'Processing... this may take a moment';
    }

    // Build per-album payload matching the discography endpoint contract.
    // URL artist_id is functionally unused by the endpoint when per-album
    // metadata is supplied — backend resolves each album through its own
    // `source` + `artist_name`. Placeholder 'your-albums' makes the route
    // match without picking an arbitrary library artist.
    const albumsPayload = selected.map(r => ({
        id: r._src.id,
        name: r.album_name || '',
        artist_name: r.artist_name || '',
        source: r._src.source,
    }));

    try {
        const response = await fetch(`/api/artist/your-albums/download-discography`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                albums: albumsPayload,
                artist_name: 'Your Albums',
                source: null,
            }),
        });

        const reader = response.body.getReader();
        const decoder = new TextDecoder();
        let buffer = '';
        let totalAdded = 0, totalSkipped = 0;

        while (true) {
            const { done, value } = await reader.read();
            if (done) break;
            buffer += decoder.decode(value, { stream: true });
            const lines = buffer.split('\n');
            buffer = lines.pop();
            for (const line of lines) {
                if (!line.trim()) continue;
                try {
                    const data = JSON.parse(line);
                    if (data.status === 'complete') {
                        totalAdded = data.total_added || 0;
                        totalSkipped = data.total_skipped || 0;
                    } else if (data.album_id) {
                        // Find the matching progress card — match by composite source-id
                        // pair since the same album_id could appear across sources.
                        const matching = selected.find(s => s._src.id === String(data.album_id));
                        if (matching) {
                            const item = document.getElementById(`your-albums-batch-prog-${matching._src.source}-${matching._src.id}`);
                            if (item) {
                                const status = item.querySelector('.discog-prog-status');
                                const icon = item.querySelector('.discog-prog-icon');
                                if (data.status === 'done') {
                                    if (status) status.textContent = `${data.tracks_added || 0} added · ${data.tracks_skipped || 0} skipped`;
                                    if (icon) icon.innerHTML = '✓';
                                    item.classList.add('done');
                                    item.classList.remove('active');
                                } else if (data.status === 'error') {
                                    if (status) status.textContent = `Error: ${data.message || 'unknown'}`;
                                    if (icon) icon.innerHTML = '✗';
                                    item.classList.add('error');
                                    item.classList.remove('active');
                                }
                            }
                        }
                    }
                } catch (parseErr) {
                    console.debug('your-albums batch ndjson parse:', parseErr);
                }
            }
        }

        if (footer) {
            const info = document.getElementById('your-albums-batch-footer-info');
            if (info) info.textContent = `${totalAdded} tracks added to wishlist · ${totalSkipped} skipped`;
        }
        if (submitBtn) {
            submitBtn.style.display = '';
            submitBtn.disabled = true;
            const txt = document.getElementById('your-albums-batch-submit-text');
            if (txt) txt.textContent = 'Done';
        }
        showToast(`${totalAdded} tracks added to wishlist`, totalAdded > 0 ? 'success' : 'info');
    } catch (e) {
        console.error('Error adding your albums to wishlist:', e);
        showToast(`Error: ${e.message}`, 'error');
    }
}


function _renderCompactTrackRow(track, index) {
    const coverUrl = track.album_cover_url || '/static/placeholder-album.png';
    const durationMin = Math.floor(track.duration_ms / 60000);
    const durationSec = Math.floor((track.duration_ms % 60000) / 1000);
    const duration = `${durationMin}:${durationSec.toString().padStart(2, '0')}`;
    return `
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
}

let _releaseRadarCtrl = null;

async function loadDiscoverReleaseRadar() {
    if (!_releaseRadarCtrl) {
        _releaseRadarCtrl = createDiscoverSectionController({
            id: 'release-radar',
            contentEl: '#release-radar-playlist',
            fetchUrl: '/api/discover/release-radar',
            extractItems: (data) => data.tracks || [],
            renderItems: (items) => {
                discoverReleaseRadarTracks = items;
                const rows = items.map((t, i) => _renderCompactTrackRow(t, i)).join('');
                return `<div class="discover-playlist-tracks-compact">${rows}</div>`;
            },
            loadingMessage: 'Loading release radar...',
            emptyMessage: 'No new releases available',
            errorMessage: 'Failed to load release radar',
            verboseErrors: true,
            showErrorToast: true,
        });
    }
    return _releaseRadarCtrl.load();
}

let _weeklyCtrl = null;

async function loadDiscoverWeekly() {
    if (!_weeklyCtrl) {
        _weeklyCtrl = createDiscoverSectionController({
            id: 'discovery-weekly',
            contentEl: '#discovery-weekly-playlist',
            fetchUrl: '/api/discover/weekly',
            extractItems: (data) => data.tracks || [],
            renderItems: (items) => {
                discoverWeeklyTracks = items;
                const rows = items.map((t, i) => _renderCompactTrackRow(t, i)).join('');
                return `<div class="discover-playlist-tracks-compact">${rows}</div>`;
            },
            loadingMessage: 'Curating your discovery playlist...',
            emptyMessage: 'No tracks available yet',
            errorMessage: 'Failed to load discovery weekly',
            verboseErrors: true,
            showErrorToast: true,
        });
    }
    return _weeklyCtrl.load();
}

// ===============================
// DECADE BROWSER
// ===============================

let selectedDecade = null;
let decadeTracks = [];

function _renderDecadeCard(decade) {
    const icon = getDecadeIcon(decade.year);
    const label = `${decade.year}s`;
    return `
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
}

let _decadeBrowserCtrl = null;

async function loadDecadeBrowser() {
    if (!_decadeBrowserCtrl) {
        _decadeBrowserCtrl = createDiscoverSectionController({
            id: 'decade-browser',
            contentEl: '#decade-browser-carousel',
            fetchUrl: '/api/discover/decades/available',
            extractItems: (data) => data.decades || [],
            renderItems: (items) => items.map(d => _renderDecadeCard(d)).join(''),
            loadingMessage: 'Loading decades...',
            emptyMessage: 'No decade content available yet. Run a watchlist scan to populate your discovery pool!',
            errorMessage: 'Failed to load decades',
            verboseErrors: true,
            showErrorToast: true,
        });
    }
    return _decadeBrowserCtrl.load();
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

function _renderGenreCard(genre) {
    const icon = getGenreIcon(genre.name);
    const displayName = capitalizeGenre(genre.name);
    return `
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
}

let _genreBrowserCtrl = null;

async function loadGenreBrowser() {
    if (!_genreBrowserCtrl) {
        _genreBrowserCtrl = createDiscoverSectionController({
            id: 'genre-browser',
            contentEl: '#genre-browser-carousel',
            fetchUrl: '/api/discover/genres/available',
            extractItems: (data) => data.genres || [],
            renderItems: (items) => items.map(g => _renderGenreCard(g)).join(''),
            loadingMessage: 'Loading genres...',
            emptyMessage: 'No genre content available yet. Run a watchlist scan to populate your discovery pool!',
            errorMessage: 'Failed to load genres',
            verboseErrors: true,
            showErrorToast: true,
        });
    }
    return _genreBrowserCtrl.load();
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

// Shared sync-status display block. Used by per-tab playlists
// (decade browser, genre browser) where we show download progress
// in the standard "✓ completed | ⏳ pending | ✗ failed (N%)" format.
// ListenBrainz playlists use a different shape (total/matched/failed)
// because they show MATCHING progress against the library, not
// download progress, so they intentionally don't use this helper.
function _renderSyncStatusBlock(idPrefix) {
    return `
        <div class="discover-sync-status" id="${idPrefix}-sync-status" style="display: none;">
            <div class="sync-status-content">
                <div class="sync-status-label">
                    <span class="sync-icon">⟳</span>
                    <span>Syncing to media server...</span>
                </div>
                <div class="sync-status-stats">
                    <span class="sync-stat">✓ <span id="${idPrefix}-sync-completed">0</span></span>
                    <span class="sync-stat">⏳ <span id="${idPrefix}-sync-pending">0</span></span>
                    <span class="sync-stat">✗ <span id="${idPrefix}-sync-failed">0</span></span>
                    <span class="sync-stat">(<span id="${idPrefix}-sync-percentage">0</span>%)</span>
                </div>
            </div>
        </div>
    `;
}

// ===============================
// TABBED BROWSER HELPER
// ===============================
//
// Drives the lifecycle the decade browser ("Time Machine") and
// genre browser ("Browse by Genre") share: fetch tab list → paint
// tab strip + per-tab content shells → fetch + render content for
// the active tab → handle empty / error states.
//
// The two browsers paint slightly different markup (different CSS
// prefixes, different action buttons, different sync handlers) but
// the lifecycle is identical. Each browser registers a config; the
// helper handles the rest.
//
// Two-phase render:
//   Phase 1 (loadTabs)        — paint tab strip + N content shells,
//                                each shell containing a loading
//                                spinner in its playlist container,
//                                then trigger Phase 2 for first tab.
//   Phase 2 (loadTabContent)  — fetch tracks for one tab, swap the
//                                spinner in its playlist container
//                                for the rendered track list.
//
// Renderers stay per-browser because action buttons + classes
// legitimately differ. The helper owns the lifecycle, not the look.
function createTabbedBrowserSection(config) {
    const cfg = Object.assign({
        // Diagnostic id used in console errors.
        id: 'tabbed-browser',
        // DOM IDs of the tab-strip + per-tab-contents containers.
        tabsContainerId: null,
        contentsContainerId: null,
        // Async fn returning array of tab descriptors (e.g. decades).
        fetchTabs: null,
        // (tab) => string  unique id for one tab (e.g. 'decade-1980').
        // Used as the prefix for that tab's content + playlist + sync IDs.
        tabId: null,
        // (tab) => string  HTML for one tab button. Receives `(tab, isActive)`.
        renderTabButton: null,
        // (tab) => string  HTML for one tab's content shell (action
        // buttons + sync-status block + empty playlist container).
        // The playlist container inside MUST have id `${tabId}-playlist`
        // so the helper can fill it during Phase 2.
        renderTabShell: null,
        // Async fn (tab) => array of tracks for that tab.
        fetchTabContent: null,
        // (tracks, tab) => string  HTML for the playlist container.
        renderTabTracks: null,
        // Copy / messages.
        emptyTabsMessage: 'No content available',
        emptyContentMessage: (tab) => 'No tracks found',
        errorTabsMessage: 'Failed to load',
        errorContentMessage: 'Failed to load tracks',
        // Fired after Phase 1 paints the tab strip + shells, before
        // Phase 2 is triggered for the first tab. Useful for caching
        // the tab list (e.g. `availableGenres = ...`).
        onTabsRendered: null,
    }, config || {});

    async function loadTabs() {
        try {
            const tabsContainer = document.getElementById(cfg.tabsContainerId);
            const contentsContainer = document.getElementById(cfg.contentsContainerId);
            if (!tabsContainer || !contentsContainer) return;

            const tabs = await cfg.fetchTabs();
            if (!Array.isArray(tabs) || tabs.length === 0) {
                tabsContainer.innerHTML = `<div class="discover-empty"><p>${cfg.emptyTabsMessage}</p></div>`;
                return;
            }

            let tabsHTML = '';
            let contentsHTML = '';
            tabs.forEach((tab, index) => {
                const isActive = index === 0;
                tabsHTML += cfg.renderTabButton(tab, isActive);
                contentsHTML += cfg.renderTabShell(tab, isActive);
            });

            tabsContainer.innerHTML = tabsHTML;
            contentsContainer.innerHTML = contentsHTML;

            if (typeof cfg.onTabsRendered === 'function') {
                try { cfg.onTabsRendered(tabs); }
                catch (err) { console.debug(`[${cfg.id}] onTabsRendered threw:`, err); }
            }

            // Phase 2: kick off content load for the first tab.
            await loadTabContent(tabs[0]);
        } catch (error) {
            console.error(`Error loading ${cfg.id} tabs:`, error);
            const tabsContainer = document.getElementById(cfg.tabsContainerId);
            if (tabsContainer) {
                tabsContainer.innerHTML = `<div class="discover-empty"><p>${cfg.errorTabsMessage}</p></div>`;
            }
        }
    }

    async function loadTabContent(tab) {
        const tabId = cfg.tabId(tab);
        const playlistContainer = document.getElementById(`${tabId}-playlist`);
        if (!playlistContainer) return;

        try {
            const tracks = await cfg.fetchTabContent(tab);
            if (!Array.isArray(tracks) || tracks.length === 0) {
                const msg = (typeof cfg.emptyContentMessage === 'function')
                    ? cfg.emptyContentMessage(tab)
                    : cfg.emptyContentMessage;
                playlistContainer.innerHTML = `<div class="discover-empty"><p>${msg}</p></div>`;
                return;
            }
            playlistContainer.innerHTML = cfg.renderTabTracks(tracks, tab);
        } catch (error) {
            console.error(`Error loading ${cfg.id} tab content:`, error);
            const stillThere = document.getElementById(`${tabId}-playlist`);
            if (stillThere) {
                stillThere.innerHTML = `<div class="discover-empty"><p>${cfg.errorContentMessage}</p></div>`;
            }
        }
    }

    return { loadTabs, loadTabContent };
}

// ----- Decade browser config + thin wrappers -----

let _decadeBrowserTabsCtrl = null;

function _getDecadeBrowserTabsCtrl() {
    if (_decadeBrowserTabsCtrl) return _decadeBrowserTabsCtrl;
    _decadeBrowserTabsCtrl = createTabbedBrowserSection({
        id: 'decade-browser-tabs',
        tabsContainerId: 'decade-tabs',
        contentsContainerId: 'decade-tab-contents',
        fetchTabs: async () => {
            const response = await fetch('/api/discover/decades/available');
            if (!response.ok) throw new Error('Failed to fetch available decades');
            const data = await response.json();
            if (!data.success) return [];
            return data.decades || [];
        },
        tabId: (decade) => `decade-${decade.year}`,
        renderTabButton: (decade, isActive) => {
            const icon = getDecadeIcon(decade.year);
            return `
                <button class="decade-tab ${isActive ? 'active' : ''}"
                        data-decade="${decade.year}"
                        onclick="switchDecadeTab(${decade.year})">
                    ${icon} ${decade.year}s
                </button>
            `;
        },
        renderTabShell: (decade, isActive) => {
            const tabId = `decade-${decade.year}`;
            return `
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

                        ${_renderSyncStatusBlock(tabId)}
                    </div>

                    <!-- Track List -->
                    <div class="discover-playlist-container compact" id="${tabId}-playlist">
                        <div class="discover-loading"><div class="loading-spinner"></div><p>Loading ${decade.year}s tracks...</p></div>
                    </div>
                </div>
            `;
        },
        fetchTabContent: async (decade) => {
            const response = await fetch(`/api/discover/decade/${decade.year}`);
            if (!response.ok) throw new Error('Failed to fetch decade playlist');
            const data = await response.json();
            if (!data.success) return [];
            // Side-effect: cache + active marker, exactly as old code did.
            decadeTracksCache[decade.year] = data.tracks || [];
            activeDecade = decade.year;
            return data.tracks || [];
        },
        renderTabTracks: (tracks) => _renderTabbedTrackList(tracks),
        emptyContentMessage: (decade) => `No tracks found for the ${decade.year}s`,
        errorTabsMessage: 'Failed to load decades',
        errorContentMessage: 'Failed to load decade tracks',
        emptyTabsMessage: 'No decade content available yet. Run a watchlist scan to populate your discovery pool!',
    });
    return _decadeBrowserTabsCtrl;
}

// Shared track-row markup for tabbed browsers. Decade + genre rows
// have the same shape — both pull from `track_data_json` first then
// fall back to top-level fields. Lifted so the helper-driven
// renderers don't each carry a copy.
function _renderTabbedTrackList(tracks) {
    let html = '<div class="discover-playlist-tracks-compact">';
    tracks.forEach((track, index) => {
        let trackData = track;
        if (track.track_data_json) {
            trackData = track.track_data_json;
        }

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
    return html;
}

async function loadDecadeBrowserTabs() {
    return _getDecadeBrowserTabsCtrl().loadTabs();
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
    return _getDecadeBrowserTabsCtrl().loadTabContent({ year: decade });
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
                showToast(`${decade}s Classics sync complete!`, 'success');
                setTimeout(() => { const sd = el(`decade-${decade}-sync-status`); if (sd) sd.style.display = 'none'; }, 3000);
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

                showToast(`${decade}s Classics sync complete!`, 'success');

                setTimeout(() => {
                    const statusDisplay = document.getElementById(`decade-${decade}-sync-status`);
                    if (statusDisplay) statusDisplay.style.display = 'none';
                }, 3000);
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

// Helper: derive the URL/DOM-safe id used for a genre tab. Both
// the tab shell and the playlist-container element are keyed on this.
function _genreTabId(genreName) {
    const safe = genreName.replace(/\s+/g, '-').replace(/[^a-zA-Z0-9-]/g, '');
    return `genre-${safe}`;
}

let _genreBrowserTabsCtrl = null;

function _getGenreBrowserTabsCtrl() {
    if (_genreBrowserTabsCtrl) return _genreBrowserTabsCtrl;
    _genreBrowserTabsCtrl = createTabbedBrowserSection({
        id: 'genre-browser-tabs',
        tabsContainerId: 'genre-tabs',
        contentsContainerId: 'genre-tab-contents',
        fetchTabs: async () => {
            const response = await fetch('/api/discover/genres/available');
            if (!response.ok) throw new Error('Failed to fetch available genres');
            const data = await response.json();
            if (!data.success) return [];
            // Cap at 10 to avoid overcrowding; old behavior preserved.
            return (data.genres || []).slice(0, 10);
        },
        onTabsRendered: (genres) => { availableGenres = genres; },
        tabId: (genre) => _genreTabId(genre.name),
        renderTabButton: (genre, isActive) => {
            const icon = getGenreIcon(genre.name);
            return `
                <button class="genre-tab ${isActive ? 'active' : ''}"
                        data-genre="${escapeHtml(genre.name)}"
                        onclick="switchGenreTab('${escapeForInlineJs(genre.name)}')">
                    ${icon} ${capitalizeGenre(genre.name)}
                </button>
            `;
        },
        renderTabShell: (genre, isActive) => {
            const tabId = _genreTabId(genre.name);
            return `
                <div class="genre-tab-content ${isActive ? 'active' : ''}" id="${tabId}-content" data-genre="${escapeHtml(genre.name)}">
                    <!-- Action Buttons -->
                    <div class="genre-actions">
                        <div class="discover-section-header">
                            <div>
                                <h3 style="margin: 0; color: #fff; font-size: 18px;">${capitalizeGenre(genre.name)} Mix</h3>
                                <p id="${tabId}-subtitle" style="margin: 4px 0 0 0; color: #999; font-size: 13px;">${genre.track_count} tracks</p>
                            </div>
                            <div class="discover-section-actions">
                                <button class="action-button secondary" onclick="openDownloadModalForGenre('${escapeForInlineJs(genre.name)}')" title="Download missing tracks">
                                    <span class="button-icon">↓</span>
                                    <span class="button-text">Download</span>
                                </button>
                                <button class="action-button primary" id="${tabId}-sync-btn" onclick="startGenreSync('${escapeForInlineJs(genre.name)}')" title="Sync to media server">
                                    <span class="button-icon">⟳</span>
                                    <span class="button-text">Sync</span>
                                </button>
                            </div>
                        </div>

                        ${_renderSyncStatusBlock(tabId)}
                    </div>

                    <!-- Track List -->
                    <div class="discover-playlist-container compact" id="${tabId}-playlist">
                        <div class="discover-loading"><div class="loading-spinner"></div><p>Loading ${capitalizeGenre(genre.name)} tracks...</p></div>
                    </div>
                </div>
            `;
        },
        fetchTabContent: async (genre) => {
            const response = await fetch(`/api/discover/genre/${encodeURIComponent(genre.name)}`);
            if (!response.ok) throw new Error('Failed to fetch genre playlist');
            const data = await response.json();
            if (!data.success) return [];
            // Side-effect: cache + active marker, exactly as old code did.
            genreTracksCache[genre.name] = data.tracks || [];
            activeGenre = genre.name;
            return data.tracks || [];
        },
        renderTabTracks: (tracks) => _renderTabbedTrackList(tracks),
        emptyContentMessage: (genre) => `No tracks found for ${capitalizeGenre(genre.name)}`,
        errorTabsMessage: 'Failed to load genres',
        errorContentMessage: 'Failed to load genre tracks',
        emptyTabsMessage: 'No genre content available yet. Run a watchlist scan to populate your discovery pool!',
    });
    return _genreBrowserTabsCtrl;
}

async function loadGenreBrowserTabs() {
    return _getGenreBrowserTabsCtrl().loadTabs();
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
    return _getGenreBrowserTabsCtrl().loadTabContent({ name: genreName });
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
                showToast(`${capitalizeGenre(genreName)} Mix sync complete!`, 'success');
                setTimeout(() => { const sd = el(`genre-${genreId}-sync-status`); if (sd) sd.style.display = 'none'; }, 3000);
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

                showToast(`${capitalizeGenre(genreName)} Mix sync complete!`, 'success');

                setTimeout(() => {
                    const statusDisplay = document.getElementById(`genre-${genreId}-sync-status`);
                    if (statusDisplay) statusDisplay.style.display = 'none';
                }, 3000);
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

function _renderSeasonalAlbumCard(album, index) {
    const coverUrl = album.album_cover_url || '/static/placeholder-album.png';
    return `
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
}

// Seasonal Albums uses no-fetch `data:` mode — the parent
// `loadSeasonalContent` already fetched the season payload and passes
// the album list directly. Controller is recreated per call so the
// per-season `data` snapshot is current.
async function loadSeasonalAlbums(seasonData) {
    const albums = (seasonData && seasonData.albums) || [];
    const ctrl = createDiscoverSectionController({
        id: 'seasonal-albums',
        sectionEl: '#seasonal-albums-section',
        contentEl: '#seasonal-albums-carousel',
        data: { success: true, albums },
        extractItems: (data) => data.albums,
        beforeLoad: () => {
            const section = document.getElementById('seasonal-albums-section');
            if (section) section.style.display = 'block';
            const titleEl = document.getElementById('seasonal-albums-title');
            const subtitleEl = document.getElementById('seasonal-albums-subtitle');
            if (titleEl && seasonData) titleEl.textContent = `${seasonData.icon} ${seasonData.name}`;
            if (subtitleEl && seasonData) subtitleEl.textContent = seasonData.description;
        },
        renderItems: (items) => {
            discoverSeasonalAlbums = items;
            return items.map((album, i) => _renderSeasonalAlbumCard(album, i)).join('');
        },
        emptyMessage: 'No seasonal albums found',
        errorMessage: 'Failed to load seasonal albums',
        verboseErrors: true,
            showErrorToast: true,
    });
    return ctrl.load();
}

let _seasonalPlaylistCtrl = null;
let _seasonalPlaylistCtrlKey = null;

async function loadSeasonalPlaylist(seasonData) {
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

    // Re-create the controller when the season key changes so the
    // fetchUrl always points at the active season's endpoint.
    if (!_seasonalPlaylistCtrl || _seasonalPlaylistCtrlKey !== currentSeasonKey) {
        _seasonalPlaylistCtrl = createDiscoverSectionController({
            id: 'seasonal-playlist',
            contentEl: '#seasonal-playlist',
            fetchUrl: `/api/discover/seasonal/${currentSeasonKey}/playlist`,
            extractItems: (data) => data.tracks || [],
            renderItems: (items) => {
                discoverSeasonalTracks = items;
                const rows = items.map((t, i) => _renderCompactTrackRow(t, i)).join('');
                return `<div class="discover-playlist-tracks-compact">${rows}</div>`;
            },
            loadingMessage: 'Loading playlist...',
            emptyMessage: 'No tracks available yet',
            errorMessage: 'Failed to load playlist',
            verboseErrors: true,
            showErrorToast: true,
        });
        _seasonalPlaylistCtrlKey = currentSeasonKey;
    }
    return _seasonalPlaylistCtrl.load();
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

function _buildDiscoverArtistContext(source, artistName, sourceData = {}, albumData = {}) {
    const normalizedSource = (source || '').toString().toLowerCase();
    const albumArtist = Array.isArray(albumData.artists) ? albumData.artists[0] : null;
    const activeSource = (source || sourceData.active_source || sourceData.source || '').toString().toLowerCase();
    const context = {
        ...sourceData,
        id: sourceData.active_source_id || sourceData.artist_id || albumArtist?.id || '',
        name: artistName || sourceData.artist_name || sourceData.name || albumArtist?.name || '',
        source: normalizedSource || activeSource || '',
        spotify_artist_id: sourceData.spotify_artist_id || sourceData.artist_spotify_id || ((normalizedSource || activeSource) === 'spotify' ? albumArtist?.id : '') || '',
        itunes_artist_id: sourceData.itunes_artist_id || sourceData.artist_itunes_id || ((normalizedSource || activeSource) === 'itunes' ? albumArtist?.id : '') || '',
        deezer_artist_id: sourceData.deezer_artist_id || sourceData.artist_deezer_id || sourceData.deezer_id || ((normalizedSource || activeSource) === 'deezer' ? albumArtist?.id : '') || '',
        discogs_artist_id: sourceData.discogs_artist_id || sourceData.artist_discogs_id || sourceData.discogs_id || '',
        amazon_artist_id: sourceData.amazon_artist_id || sourceData.artist_amazon_id || sourceData.amazon_id || '',
        soul_id: sourceData.soul_id || sourceData.hydrabase_artist_id || '',
    };

    const sourceIdBySource = {
        spotify: context.spotify_artist_id,
        itunes: context.itunes_artist_id,
        deezer: context.deezer_artist_id,
        discogs: context.discogs_artist_id,
        amazon: context.amazon_artist_id,
        hydrabase: context.soul_id,
    };
    context.id = sourceIdBySource[normalizedSource || activeSource] || context.id;
    return context;
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
        const artistContext = _buildDiscoverArtistContext(source, album.artist_name, album, albumData);

        const albumContext = {
            id: albumData.id,
            name: albumData.name,
            source: source,
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
            // Refresh discovery sections to remove the artist. Daily Mixes
            // is intentionally paused (see loadDiscoverPage), so don't
            // refresh it — the section isn't on the page anyway.
            loadPersonalizedHiddenGems();
            loadDiscoveryShuffle();
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

let _yourArtistsCtrl = null;

function _pickArtistDetailSource(artist) {
    if (!artist) return { id: '', source: '' };
    const sourceFields = {
        spotify: 'spotify_artist_id',
        deezer: 'deezer_artist_id',
        itunes: 'itunes_artist_id',
        discogs: 'discogs_artist_id',
        amazon: 'amazon_artist_id',
        musicbrainz: 'musicbrainz_artist_id',
        hydrabase: 'soul_id',
    };
    const active = String(artist.active_source || artist.source || '').toLowerCase();
    const activeField = sourceFields[active];
    if (activeField && artist[activeField]) {
        return { id: String(artist[activeField]), source: active };
    }
    for (const [source, field] of Object.entries(sourceFields)) {
        if (artist[field]) return { id: String(artist[field]), source };
    }
    if (artist.active_source_id && activeField) {
        return { id: String(artist.active_source_id), source: active };
    }
    return { id: '', source: '' };
}

async function loadYourArtists() {
    if (!_yourArtistsCtrl) {
        _yourArtistsCtrl = createDiscoverSectionController({
            id: 'your-artists',
            sectionEl: '#your-artists-section',
            contentEl: '#your-artists-carousel',
            fetchUrl: '/api/discover/your-artists',
            extractItems: (data) => data.artists || [],
            // Only treat as "truly empty" when there's no data AND the
            // upstream isn't still discovering. When stale + empty, the
            // renderer shows a custom in-progress message and a poller
            // is started in onRendered.
            isEmpty: (items, data) => items.length === 0 && !data.stale,
            hideWhenEmpty: true,
            renderItems: (items, data) => {
                const subtitle = document.getElementById('your-artists-subtitle');

                // Stale + empty — show custom "still fetching" message
                if (items.length === 0 && data.stale) {
                    if (subtitle) subtitle.textContent = 'Discovering your artists across connected services...';
                    return `
                        <div class="ya-loading">
                            <div class="watch-all-loading-spinner"></div>
                            <span>Fetching and matching artists from your services...</span>
                        </div>
                    `;
                }

                // Update subtitle with source info
                const sources = new Set();
                items.forEach(a => (a.source_services || []).forEach(s => sources.add(s)));
                const sourceNames = { spotify: 'Spotify', lastfm: 'Last.fm', tidal: 'Tidal', deezer: 'Deezer' };
                const sourceList = [...sources].map(s => sourceNames[s] || s).join(' and ');
                if (subtitle) {
                    subtitle.textContent = `Artists you follow on ${sourceList || 'your music services'}`;
                    if (data.stale) subtitle.textContent += ' (updating...)';
                }

                // Store for modal access and render carousel cards
                window._yaArtists = {};
                window._yaActiveSource = data.active_source || 'spotify';
                items.forEach(a => { window._yaArtists[a.id] = a; });
                return items.map(a => _renderYourArtistCard(a)).join('');
            },
            onRendered: ({ data }) => {
                // Continue polling while upstream is still discovering.
                if (data.stale) _pollYourArtists();
            },
            loadingMessage: 'Loading your artists...',
            emptyMessage: 'No followed artists found',
            errorMessage: 'Failed to load your artists',
            verboseErrors: true,
            showErrorToast: true,
        });
    }
    return _yourArtistsCtrl.load();
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
    const detailSource = _pickArtistDetailSource(artist);
    const hasId = detailSource.id && detailSource.id !== '';

    const detailHref = hasId ? buildArtistDetailPath(detailSource.id, detailSource.source) : '';

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
                ${hasId
            ? `<a class="ya-card-name" href="${detailHref}" onclick="event.stopPropagation(); document.getElementById('ya-info-modal-overlay')?.remove(); document.getElementById('your-artists-modal-overlay')?.remove();" style="display:block;text-decoration:none;color:inherit;">${_esc(artist.artist_name)}</a>`
            : `<div class="ya-card-name">${_esc(artist.artist_name)}</div>`}
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
                ? `<button class="btn btn--sm btn--secondary ya-header-btn" onclick="toggleYourArtistWatchlist(${pool.id}, '${escapeForInlineJs(artistName)}', '${escapeForInlineJs(artistId)}', '${escapeForInlineJs(pool.active_source || '')}', this); this.textContent='Done'; this.disabled=true">Remove from Watchlist</button>`
                : `<button class="btn btn--sm btn--secondary ya-header-btn" onclick="toggleYourArtistWatchlist(${pool.id}, '${escapeForInlineJs(artistName)}', '${escapeForInlineJs(artistId)}', '${escapeForInlineJs(pool.active_source || '')}', this); this.textContent='Added!'; this.disabled=true">Add to Watchlist</button>`;
            footerEl.innerHTML = `
                ${watchBtn}
                <button class="btn btn--sm btn--secondary ya-header-btn" onclick="document.getElementById('ya-info-modal-overlay')?.remove(); document.getElementById('your-artists-modal-overlay')?.remove(); openArtistMapExplorerDirect('${escapeForInlineJs(artistName)}')">
                    <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/></svg>
                    <span>Explore</span>
                </button>
                <a class="btn btn--sm btn--secondary ya-header-btn ya-viewall-btn" href="${buildArtistDetailPath(artistId, pool.active_source || null)}" onclick="document.getElementById('ya-info-modal-overlay')?.remove(); document.getElementById('your-artists-modal-overlay')?.remove();" style="text-decoration:none;color:inherit;">
                    <span>View Discography</span>
                    <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><path d="M5 12h14"/><path d="M12 5l7 7-7 7"/></svg>
                </a>
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
    // Max offscreen-buffer dimension (px). The buffer renders the whole world,
    // so on big/dense maps (e.g. 2000-node genre map) this was 10240 → a 76 MP
    // canvas that took ~1s to rebuild and ~150ms to blit per frame (3 fps).
    // Capping it far lower makes rebuild + blit cheap (and pushes more nodes
    // under the LOD dot threshold). Only binds on large worlds — small maps
    // stay crisp via the z*2 / 1.0 caps. Tunable.
    MAX_BUFFER_PX: 4096,

    // ── v2 live-animation engine ──────────────────────────────────────────
    // Two-layer render: the offscreen buffer holds the STATIC far field (small
    // bubbles, drawn once); a LIVE overlay redraws only the bubbles big enough
    // to read on screen, every frame, so they can scale/bob/ripple. A node is
    // "live" when its on-screen radius (radius*zoom) clears LIVE_PX; everything
    // smaller stays baked in the buffer. This bounds per-frame work to what you
    // can actually see, not the full 2000-node world.
    LIVE_PX: 12,
    // The rAF loop runs ONLY while something is animating (reveal/ripple) and
    // idles otherwise, falling back to on-demand blits. _anim tracks it.
    _anim: { running: false, raf: null, last: 0 },
    _fieldAlpha: 1,   // global fade for the static far-field buffer (reveal)
    _revealT0: 0,     // performance.now() when the current reveal began
    _panelW: 320,     // right-side info panel width (reserved when framing islands)
};

// ── Genre-island layout (shared by watchlist / genre / explore) ───────────
// Every map groups its artists into genre "islands" floating on the water:
// each island is a FILLED disc of album covers (packed centre-out, no empty
// donut hole), with a floating genre title above it and a per-genre accent hue.
// Islands are spread by a golden spiral + push-apart with generous spacing.

// A cached circular "gloss" sprite — a soft top-left specular highlight that,
// drawn over each bubble, makes it read as a glassy orb. One radial gradient,
// rendered once; per-bubble it's just a cheap drawImage (no per-frame gradient).
function _artMapGlossSprite() {
    if (_artMap._gloss) return _artMap._gloss;
    const S = 128;
    const c = document.createElement('canvas');
    c.width = S; c.height = S;
    const cx = c.getContext('2d');
    cx.beginPath(); cx.arc(S / 2, S / 2, S / 2, 0, Math.PI * 2); cx.clip();
    const g = cx.createRadialGradient(S * 0.34, S * 0.28, S * 0.02, S * 0.5, S * 0.5, S * 0.62);
    g.addColorStop(0, 'rgba(255,255,255,0.40)');
    g.addColorStop(0.32, 'rgba(255,255,255,0.10)');
    g.addColorStop(0.6, 'rgba(255,255,255,0.0)');
    cx.fillStyle = g;
    cx.fillRect(0, 0, S, S);
    // A faint inner-bottom shade for roundness
    const g2 = cx.createRadialGradient(S * 0.5, S * 0.78, S * 0.05, S * 0.5, S * 0.5, S * 0.7);
    g2.addColorStop(0, 'rgba(0,0,0,0.18)');
    g2.addColorStop(0.5, 'rgba(0,0,0,0.0)');
    cx.fillStyle = g2;
    cx.fillRect(0, 0, S, S);
    _artMap._gloss = c;
    return c;
}

// A cached soft radial "halo" sprite per genre hue — drawn behind the focused
// island so it reads as a glowing place on the water. Cached per hue (≤ a few),
// so it's just a drawImage per frame, never a per-frame gradient.
function _artMapHaloSprite(hue) {
    _artMap._halos = _artMap._halos || {};
    if (_artMap._halos[hue]) return _artMap._halos[hue];
    const S = 256;
    const c = document.createElement('canvas');
    c.width = S; c.height = S;
    const cx = c.getContext('2d');
    const g = cx.createRadialGradient(S / 2, S / 2, 0, S / 2, S / 2, S / 2);
    g.addColorStop(0, `hsla(${hue},75%,55%,0.22)`);
    g.addColorStop(0.45, `hsla(${hue},75%,50%,0.08)`);
    g.addColorStop(1, `hsla(${hue},75%,50%,0)`);
    cx.fillStyle = g;
    cx.fillRect(0, 0, S, S);
    _artMap._halos[hue] = c;
    return c;
}

// Deterministic hue (0–360) from a genre name, so each island has a stable tint.
function _artMapGenreHue(name) {
    let h = 0;
    const s = (name || '').toLowerCase();
    for (let i = 0; i < s.length; i++) h = (h * 31 + s.charCodeAt(i)) % 360;
    return h;
}

// Pack members into a filled disc, centre outward. Returns relative offsets and
// the disc radius. Most-popular members land nearest the centre.
function _artMapPackDisc(members, nodeR, gap) {
    const placements = [];
    if (!members.length) return { placements, islandR: nodeR };
    placements.push({ node: members[0], dx: 0, dy: 0 });
    let idx = 1, ring = 1, ringDist = nodeR * 2 + gap;
    const step = nodeR * 2 + gap;
    while (idx < members.length) {
        const circ = 2 * Math.PI * ringDist;
        const cap = Math.max(1, Math.floor(circ / step));
        const cnt = Math.min(cap, members.length - idx);
        const aStep = (2 * Math.PI) / cnt;
        const off = ring * 2.399963; // golden offset per ring → no spokes
        for (let i = 0; i < cnt; i++) {
            const a = off + i * aStep;
            placements.push({ node: members[idx + i], dx: Math.cos(a) * ringDist, dy: Math.sin(a) * ringDist });
        }
        idx += cnt;
        ringDist += step;
        ring++;
    }
    return { placements, islandR: ringDist };
}

// Group flat nodes by primary genre into {name, count, nodes[]}, largest first.
// Nodes with no genre fall into "Other". Focal nodes (watchlist/center) keep
// their flag so the layout can size them up.
function _artMapGroupByGenre(nodes, maxIslands = 14) {
    const byGenre = {};
    for (const n of nodes) {
        const g = (n.genres && n.genres.length) ? String(n.genres[0]) : 'Other';
        const key = g.replace(/\b\w/g, c => c.toUpperCase());
        (byGenre[key] = byGenre[key] || []).push(n);
    }
    let groups = Object.keys(byGenre).map(name => ({ name, nodes: byGenre[name], count: byGenre[name].length }));
    groups.sort((a, b) => b.count - a.count);
    if (groups.length > maxIslands) {
        // Fold the long tail of tiny genres into one "Other" island.
        const head = groups.slice(0, maxIslands - 1);
        const tail = groups.slice(maxIslands - 1);
        const tailNodes = tail.flatMap(g => g.nodes);
        head.push({ name: 'Other', nodes: tailNodes, count: tailNodes.length });
        groups = head;
    }
    return groups;
}

// Lay out islands → fills _artMap.placed and _artMap._islands. groups is an
// array of {name, count, nodes[]} where each node has name/image_url/genres/ids.
function _artMapLayoutIslands(groups, opts = {}) {
    _artMap.placed = [];
    _artMap._islands = [];
    const nodeR = opts.nodeR || (_artMap.WATCHLIST_R * 0.22);
    const gap = opts.gap || (_artMap.BUFFER * 2.2);
    const cap = opts.maxPerIsland || 300;
    let pid = 0;

    const islands = groups.map(g => {
        const members = g.nodes.slice()
            .sort((a, b) => (b._focal ? 1 : 0) - (a._focal ? 1 : 0) || (b.popularity || 0) - (a.popularity || 0))
            .slice(0, cap);
        const { placements, islandR } = _artMapPackDisc(members, nodeR, gap);
        return { name: g.name, count: g.count != null ? g.count : members.length, placements, islandR, hue: _artMapGenreHue(g.name) };
    });

    // Golden-spiral seed placement
    islands.forEach((isl, i) => {
        if (i === 0) { isl.cx = 0; isl.cy = 0; }
        else { const a = i * 2.399963; const r = isl.islandR * Math.sqrt(i) * 1.05; isl.cx = Math.cos(a) * r; isl.cy = Math.sin(a) * r; }
    });
    // Push apart — generous water between islands
    for (let pass = 0; pass < 160; pass++) {
        let moved = false;
        for (let i = 0; i < islands.length; i++) {
            for (let j = i + 1; j < islands.length; j++) {
                const dx = islands[j].cx - islands[i].cx, dy = islands[j].cy - islands[i].cy;
                const dist = Math.hypot(dx, dy) || 1;
                const minD = islands[i].islandR + islands[j].islandR + nodeR * 3.5;
                if (dist < minD) {
                    const push = (minD - dist) / 2 + 1;
                    islands[i].cx -= dx / dist * push; islands[i].cy -= dy / dist * push;
                    islands[j].cx += dx / dist * push; islands[j].cy += dy / dist * push;
                    moved = true;
                }
            }
        }
        if (!moved) break;
    }

    for (const isl of islands) {
        // Floating genre title above the island
        _artMap.placed.push({
            id: `label_${isl.name}`, name: isl.name, x: isl.cx, y: isl.cy - isl.islandR - nodeR * 1.4,
            radius: Math.max(nodeR * 1.3, isl.islandR * 0.16), opacity: 1,
            type: 'genre_label', _isLabel: true, _count: isl.count, _hue: isl.hue, image_url: '',
        });
        for (const p of isl.placements) {
            const n = p.node;
            _artMap.placed.push({
                id: pid++, _origId: n.id, name: n.name,
                x: isl.cx + p.dx, y: isl.cy + p.dy,
                radius: nodeR * (n._focal ? 1.45 : 1), opacity: 1,
                type: n.type || 'similar',
                image_url: n.image_url || '', genres: n.genres || [],
                spotify_id: n.spotify_id || '', itunes_id: n.itunes_id || '',
                deezer_id: n.deezer_id || '', discogs_id: n.discogs_id || '',
                musicbrainz_id: n.musicbrainz_id || '',
                popularity: n.popularity || 0, _hue: isl.hue, _island: isl.name,
                // Ambient buoyancy — phase varies by position so bubbles bob in a
                // gentle wave (not in unison); amplitude in world units.
                _bobPhase: (isl.cx + p.dx + isl.cy + p.dy) * 0.0022,
                _bobAmp: nodeR * 0.12,
            });
        }
        _artMap._islands.push({ name: isl.name, cx: isl.cx, cy: isl.cy, r: isl.islandR, hue: isl.hue, count: isl.count });
    }

    _artMap._nodeById = {};
    _artMap.placed.forEach(n => { _artMap._nodeById[n.id] = n; });
}

// Remap edges that used original node ids to the new placed-node ids.
function _artMapRemapEdges(edges) {
    const map = {};
    for (const n of _artMap.placed) if (n._origId != null && map[n._origId] == null) map[n._origId] = n.id;
    const out = [];
    for (const e of (edges || [])) {
        const s = map[e.source], t = map[e.target];
        if (s != null && t != null && s !== t) out.push({ source: s, target: t, weight: e.weight || 1 });
    }
    return out;
}

// Auto-zoom/pan so all placed nodes fit the viewport with a margin.
function _artMapFitToContent(marginPx = 120) {
    let minX = Infinity, maxX = -Infinity, minY = Infinity, maxY = -Infinity;
    for (const n of _artMap.placed) {
        minX = Math.min(minX, n.x - n.radius); maxX = Math.max(maxX, n.x + n.radius);
        minY = Math.min(minY, n.y - n.radius); maxY = Math.max(maxY, n.y + n.radius);
    }
    if (!isFinite(minX)) return;
    const usableW = Math.max(200, _artMap.width - _artMap._panelW);
    const mapW = maxX - minX + marginPx * 2, mapH = maxY - minY + marginPx * 2;
    _artMap.zoom = Math.min(usableW / mapW, _artMap.height / mapH, 1);
    _artMap.offsetX = usableW / 2 - ((minX + maxX) / 2) * _artMap.zoom;
    _artMap.offsetY = _artMap.height / 2 - ((minY + maxY) / 2) * _artMap.zoom;
}

// ── One-island-at-a-time view (genre + watchlist) ─────────────────────────
// Big maps spread thousands of artists thin; fitting them all makes every cover
// tiny. Instead we frame ONE island filling the view (big crisp covers, live
// layer + bob viable on ~hundreds of bubbles) and navigate between them. Only
// the focused island is visible, so the buffer covers a small region at high
// res and the whole-world-buffer "small version" problem disappears.

function _artMapFocusIsland(idx, opts = {}) {
    const islands = _artMap._islands || [];
    if (!islands.length) return;
    idx = Math.max(0, Math.min(islands.length - 1, idx));
    _artMap._focusIdx = idx;
    const isl = islands[idx];

    // Show only this island's bubbles; hide everything else (and the in-world
    // titles — the nav bar already names the genre) so the frame is just this
    // island's covers.
    for (const n of _artMap.placed) {
        n.opacity = (!n._isLabel && n._island === isl.name) ? 1 : 0;
    }

    // Frame the island in the space LEFT of the info panel (~80% of it).
    const usableW = Math.max(200, _artMap.width - _artMap._panelW);
    const span = (isl.r * 2.3) + 120;
    const z = Math.min(usableW / span, _artMap.height / span, 1.2);
    _artMap.zoom = z;
    _artMap.offsetX = usableW / 2 - isl.cx * z;
    _artMap.offsetY = _artMap.height / 2 - isl.cy * z;

    _artMapUpdateIslandNav();
    _artMapRefreshPanel();

    if (opts.bloom !== false) {
        _artMapBloomIsland(isl);
    } else {
        _artMap.dirty = true;
        _artMapRender();
    }
}

// Bloom one island's bubbles (drop-in-water) + a ripple. Reuses the reveal loop.
function _artMapBloomIsland(isl) {
    const t0 = performance.now();
    _artMap._revealing = true;
    _artMap._ambient = true;
    for (const n of _artMap.placed) {
        if ((n.opacity || 0) < 0.01) continue;
        n.aScale = 0; n.aAlpha = 0;
        let radial = 0;
        if (isl.r > 0) radial = Math.min(1, Math.hypot(n.x - isl.cx, n.y - isl.cy) / isl.r);
        // Continuous radial stagger + deterministic per-bubble jitter so they
        // surface organically rather than in visible rings/segments.
        const jitter = ((Math.abs((n.id | 0) * 1103515245 + 12345) % 1000) / 1000) * 200;
        n._revealAt = t0 + radial * 300 + jitter;
        n._revealDur = 560;
        n._riseAmp = (n.radius || 20) * 1.15; // bubbles rise up into place (surfacing)
        n._revealRise = n._riseAmp;
    }
    _artMap._ripples = [{ cx: isl.cx, cy: isl.cy, hue: isl.hue, maxR: isl.r * 1.45, t0, dur: 1100 }];
    _artMapStartLoop();
}

// Step to the prev/next island (wraps).
function _artMapIslandNav(dir) {
    const islands = _artMap._islands || [];
    if (islands.length < 2) return;
    let idx = (_artMap._focusIdx || 0) + dir;
    if (idx < 0) idx = islands.length - 1;
    if (idx >= islands.length) idx = 0;
    _artMapFocusIsland(idx, { bloom: true });
}

// Build/update the bottom nav bar (prev / genre name + i/N / next). Inline-styled
// so it doesn't depend on CSS that might not exist.
function _artMapUpdateIslandNav() {
    const islands = _artMap._islands || [];
    let nav = document.getElementById('artmap-island-nav');
    if (!_artMap._oneIsland || islands.length < 1) { if (nav) nav.remove(); return; }
    const container = document.getElementById('artist-map-container');
    if (!container) return;
    if (!nav) {
        nav = document.createElement('div');
        nav.id = 'artmap-island-nav';
        container.appendChild(nav);
    }
    // Position top-left, clearing the genre sidebar (when shown) and the toolbar.
    const sb = document.getElementById('artmap-genre-sidebar');
    const sbW = (sb && sb.style.display !== 'none') ? (sb.offsetWidth || 0) : 0;
    nav.style.cssText = `position:absolute;top:64px;left:${sbW + 16}px;display:flex;align-items:center;gap:12px;padding:7px 12px;background:rgba(16,12,28,0.82);backdrop-filter:blur(10px);border:1px solid rgba(168,85,247,0.25);border-radius:14px;z-index:30;box-shadow:0 6px 24px rgba(0,0,0,0.45);user-select:none;`;
    const idx = _artMap._focusIdx || 0;
    const isl = islands[idx];
    const btn = 'width:30px;height:30px;border-radius:50%;border:1px solid rgba(255,255,255,0.18);background:rgba(255,255,255,0.06);color:#fff;font-size:13px;cursor:pointer;display:flex;align-items:center;justify-content:center;flex:none;';
    nav.innerHTML = `
        <button style="${btn}" onclick="_artMapIslandNav(-1)" title="Previous genre (←)">&#9664;</button>
        <div style="text-align:center;min-width:120px;cursor:pointer;" onclick="_artMapToggleIslandMenu(event)" title="Jump to a genre">
            <div style="font-weight:700;font-size:13px;letter-spacing:0.04em;color:hsl(${isl.hue},80%,80%);">${escapeHtml((isl.name || '').toUpperCase())} &#9662;</div>
            <div style="font-size:11px;color:rgba(255,255,255,0.55);margin-top:1px;">${isl.count} artists &middot; ${idx + 1} / ${islands.length}</div>
        </div>
        <button style="${btn}" onclick="_artMapIslandNav(1)" title="Next genre (→)">&#9654;</button>
    `;
    // Re-anchor an open menu (or leave it closed).
    const menu = document.getElementById('artmap-island-menu');
    if (menu) menu.remove();
}

// Quick-jump dropdown: list every genre island; click to jump straight to it.
function _artMapToggleIslandMenu(ev) {
    if (ev) ev.stopPropagation();
    const existing = document.getElementById('artmap-island-menu');
    if (existing) { existing.remove(); return; }
    const islands = _artMap._islands || [];
    const nav = document.getElementById('artmap-island-nav');
    if (!nav || !islands.length) return;
    const menu = document.createElement('div');
    menu.id = 'artmap-island-menu';
    menu.style.cssText = `position:absolute;top:${nav.offsetTop + nav.offsetHeight + 6}px;left:${nav.offsetLeft}px;min-width:${Math.max(180, nav.offsetWidth)}px;max-height:50vh;overflow-y:auto;background:rgba(16,12,28,0.96);backdrop-filter:blur(10px);border:1px solid rgba(168,85,247,0.25);border-radius:12px;z-index:31;box-shadow:0 8px 28px rgba(0,0,0,0.5);padding:6px;`;
    const cur = _artMap._focusIdx || 0;
    menu.innerHTML = islands.map((isl, i) => `
        <div onclick="_artMapJumpIsland(${i})" style="display:flex;align-items:center;justify-content:space-between;gap:10px;padding:7px 10px;border-radius:8px;cursor:pointer;${i === cur ? 'background:rgba(168,85,247,0.18);' : ''}"
             onmouseover="this.style.background='rgba(255,255,255,0.08)'" onmouseout="this.style.background='${i === cur ? 'rgba(168,85,247,0.18)' : 'transparent'}'">
            <span style="display:flex;align-items:center;gap:8px;font-size:12.5px;font-weight:600;color:#fff;">
                <span style="width:9px;height:9px;border-radius:50%;background:hsl(${isl.hue},75%,62%);flex:none;"></span>
                ${escapeHtml(isl.name)}
            </span>
            <span style="font-size:11px;color:rgba(255,255,255,0.45);">${isl.count}</span>
        </div>`).join('');
    nav.parentElement.appendChild(menu);
    // Close on next outside click.
    setTimeout(() => {
        const close = (e) => { if (!menu.contains(e.target)) { menu.remove(); document.removeEventListener('mousedown', close); } };
        document.addEventListener('mousedown', close);
    }, 0);
}

function _artMapJumpIsland(i) {
    const menu = document.getElementById('artmap-island-menu');
    if (menu) menu.remove();
    _artMapFocusIsland(i, { bloom: true });
}

// ── Right-side info panel ──────────────────────────────────────────────────
// A polished detail panel: a discovery dashboard + current-view coverage at the
// top, a clickable top-artists list, and a rich artist card when you hover/click
// a bubble. Lives on the right so it never collides with the genre sidebar.

function _artMapNodeBest(n) {
    const map = [['spotify_id', 'spotify'], ['itunes_id', 'itunes'], ['deezer_id', 'deezer'], ['discogs_id', 'discogs'], ['musicbrainz_id', 'musicbrainz']];
    for (const [k, s] of map) if (n && n[k]) return { id: n[k], source: s };
    return { id: '', source: '' };
}

function _artMapConnCount(n) {
    let c = 0;
    for (const e of (_artMap.edges || [])) if (e.source === n.id || e.target === n.id) c++;
    return c;
}

// ── Watchlist state for the panel card ──
function _artMapIsWatched(n) {
    if (!n) return false;
    if (n.type === 'watchlist') return true;
    const best = _artMapNodeBest(n);
    return !!(best.id && _artMap._watchSet && _artMap._watchSet.has(best.id));
}

// The watchlist button's markup, reflecting current state (filled star + "On
// watchlist" when watched, outline + "Watchlist" when not).
function _artMapWatchBtnHtml(n) {
    const watched = _artMapIsWatched(n);
    const idArg = typeof n.id === 'number' ? n.id : `'${n.id}'`;
    return `<button id="artmap-card-watch" onclick="_artMapToggleWatch(${idArg})"
        style="flex:1;background:${watched ? 'rgba(192,132,252,0.3)' : 'rgba(192,132,252,0.12)'};border:1px solid rgba(192,132,252,${watched ? '0.55' : '0.35'});color:#e9d5ff;border-radius:10px;padding:9px;font-size:12px;font-weight:600;cursor:pointer;">${watched ? '&#9733; On watchlist' : '&#9734; Watchlist'}</button>`;
}

function _artMapRenderWatchBtn(n) {
    const b = document.getElementById('artmap-card-watch');
    if (b) b.outerHTML = _artMapWatchBtnHtml(n); // inline onclick survives outerHTML swap
}

// Toggle watchlist membership for a node, updating the cache + button in place.
async function _artMapToggleWatch(id) {
    const n = (_artMap._nodeById || {})[id];
    if (!n) return;
    const best = _artMapNodeBest(n);
    if (!best.id) { showToast('No source id for this artist', 'error'); return; }
    _artMap._watchSet = _artMap._watchSet || new Set();
    const watched = _artMapIsWatched(n);
    try {
        const resp = await fetch(watched ? '/api/watchlist/remove' : '/api/watchlist/add', {
            method: 'POST', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(watched ? { artist_id: best.id } : { artist_id: best.id, artist_name: n.name, source: best.source }),
        });
        if (!resp.ok) { showToast('Failed to update watchlist', 'error'); return; }
        if (watched) {
            _artMap._watchSet.delete(best.id);
            if (n.type === 'watchlist') n.type = 'similar';
        } else {
            _artMap._watchSet.add(best.id);
        }
        showToast(watched ? `Removed ${n.name} from watchlist` : `Added ${n.name} to watchlist`, watched ? 'info' : 'success');
        _artMapRenderWatchBtn(n);
    } catch (e) {
        showToast('Failed to update watchlist', 'error');
    }
}

// Lazily confirm watchlist membership from the server, then refresh the button.
function _artMapCheckWatched(n) {
    const best = _artMapNodeBest(n);
    _artMap._watchSet = _artMap._watchSet || new Set();
    _artMap._watchChecked = _artMap._watchChecked || new Set();
    if (!best.id || _artMap._watchChecked.has(best.id)) return;
    fetch('/api/watchlist/check', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ artist_id: best.id }),
    }).then(r => r.json()).then(d => {
        _artMap._watchChecked.add(best.id);
        if (d && d.success) {
            if (d.is_watching) _artMap._watchSet.add(best.id); else _artMap._watchSet.delete(best.id);
            if (_artMap._panelArtistId === n.id) _artMapRenderWatchBtn(n);
        }
    }).catch(() => { });
}

function _artMapEnsurePanel() {
    const container = document.getElementById('artist-map-container');
    if (!container) return null;
    let p = document.getElementById('artmap-info-panel');
    if (!p) {
        p = document.createElement('div');
        p.id = 'artmap-info-panel';
        p.innerHTML = `<div id="artmap-panel-head" style="padding:16px 16px 12px;border-bottom:1px solid rgba(255,255,255,0.06);"></div>`
            + `<div id="artmap-panel-body" style="flex:1;overflow-y:auto;padding:12px 14px;"></div>`;
        container.appendChild(p);
    }
    // Start below the toolbar so it never covers the navbar (measured each call
    // in case the toolbar height changes).
    const tb = container.querySelector('.artist-map-toolbar');
    const top = tb ? tb.offsetHeight : 56;
    p.style.cssText = `position:absolute;top:${top}px;right:0;width:${_artMap._panelW}px;height:calc(100% - ${top}px);`
        + `background:linear-gradient(180deg,rgba(20,15,34,0.92),rgba(11,8,20,0.96));backdrop-filter:blur(16px);`
        + `border-left:1px solid rgba(168,85,247,0.18);z-index:20;display:flex;flex-direction:column;`
        + `color:#fff;overflow:hidden;box-shadow:-10px 0 36px rgba(0,0,0,0.45);font-size:13px;`;
    return p;
}

function _artMapClosePanel() {
    const p = document.getElementById('artmap-info-panel');
    if (p) p.remove();
    _artMap._panelArtistId = null;
}

// Stat tile helper
function _miniStat(label, value, hue) {
    return `<div style="flex:1;background:rgba(255,255,255,0.04);border:1px solid rgba(255,255,255,0.06);border-radius:10px;padding:8px 6px;text-align:center;">
        <div style="font-size:18px;font-weight:800;color:${hue != null ? `hsl(${hue},80%,80%)` : '#fff'};line-height:1;">${value}</div>
        <div style="font-size:9.5px;letter-spacing:0.06em;text-transform:uppercase;color:rgba(255,255,255,0.45);margin-top:4px;">${label}</div>
    </div>`;
}

// Build the panel header (dashboard) + body (top-artists list) for the view.
function _artMapRefreshPanel() {
    const p = _artMapEnsurePanel();
    if (!p) return;
    const head = document.getElementById('artmap-panel-head');
    const body = document.getElementById('artmap-panel-body');
    if (!head || !body) return;

    const nodes = (_artMap.placed || []).filter(n => !n._isLabel);
    const total = nodes.length;
    const watch = nodes.filter(n => n.type === 'watchlist' || n.type === 'center').length;
    const islands = _artMap._islands || [];
    const oneIsland = _artMap._oneIsland;
    const isl = oneIsland && islands.length ? islands[_artMap._focusIdx || 0] : null;

    // Scope (current island vs whole map)
    const scope = isl ? nodes.filter(n => n._island === isl.name) : nodes;
    const scopeTotal = isl ? isl.count : total;
    const scopeWatch = scope.filter(n => n.type === 'watchlist' || n.type === 'center').length;
    const cov = scopeTotal ? Math.round((scopeWatch / scopeTotal) * 100) : 0;
    const hue = isl ? isl.hue : 270;

    const title = _artMap._mapTitle || 'Artist Map';
    head.innerHTML = `
        <div style="font-size:10px;letter-spacing:0.12em;text-transform:uppercase;color:rgba(255,255,255,0.4);">${title}</div>
        ${isl ? `<div style="font-size:19px;font-weight:800;color:hsl(${hue},82%,80%);margin-top:2px;">${escapeHtml(isl.name)}</div>` : `<div style="font-size:19px;font-weight:800;margin-top:2px;">Overview</div>`}
        <div style="display:flex;gap:8px;margin-top:12px;">
            ${_miniStat('Artists', scopeTotal, hue)}
            ${_miniStat('Watchlist', scopeWatch)}
            ${_miniStat(isl ? 'Genre' : 'Genres', isl ? '1' : (islands.length || 1))}
        </div>
        <div style="margin-top:12px;">
            <div style="display:flex;justify-content:space-between;font-size:10.5px;color:rgba(255,255,255,0.5);margin-bottom:4px;">
                <span>On your watchlist</span><span>${scopeWatch}/${scopeTotal}</span>
            </div>
            <div style="height:6px;background:rgba(255,255,255,0.08);border-radius:3px;overflow:hidden;">
                <div style="height:100%;width:${cov}%;background:linear-gradient(90deg,hsl(${hue},80%,60%),hsl(${(hue + 40) % 360},80%,62%));border-radius:3px;"></div>
            </div>
        </div>`;

    // Body: top artists (by popularity) in the current scope.
    _artMap._panelArtistId = null;
    const top = scope.slice().sort((a, b) => (b.popularity || 0) - (a.popularity || 0)).slice(0, 14);
    body.innerHTML = `
        <div style="font-size:10px;letter-spacing:0.1em;text-transform:uppercase;color:rgba(255,255,255,0.4);margin:2px 2px 8px;">Top artists</div>
        ${top.map((n, i) => `
            <div onclick="_artMapPanelArtistById(${typeof n.id === 'number' ? n.id : `'${n.id}'`})"
                 style="display:flex;align-items:center;gap:10px;padding:6px 8px;border-radius:9px;cursor:pointer;"
                 onmouseover="this.style.background='rgba(255,255,255,0.06)'" onmouseout="this.style.background='transparent'">
                <span style="width:18px;text-align:center;font-size:11px;font-weight:700;color:rgba(255,255,255,0.35);">${i + 1}</span>
                <span style="width:30px;height:30px;border-radius:50%;flex:none;overflow:hidden;background:rgba(255,255,255,0.06);display:flex;align-items:center;justify-content:center;">
                    ${n.image_url ? `<img src="${escapeHtml(n.image_url)}" style="width:100%;height:100%;object-fit:cover;" loading="lazy" onerror="this.style.display='none'">` : '&#9835;'}
                </span>
                <span style="flex:1;min-width:0;font-size:12.5px;font-weight:600;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;">${escapeHtml(n.name)}</span>
                ${n.type === 'watchlist' ? '<span style="color:#c084fc;font-size:11px;">&#9733;</span>' : ''}
            </div>`).join('') || '<div style="color:rgba(255,255,255,0.4);padding:8px;">No artists</div>'}`;
}

// Show a rich artist card in the body (hover/click); pass null to restore the list.
function _artMapPanelArtist(node) {
    const body = document.getElementById('artmap-panel-body');
    if (!body) return;
    if (!node) { if (_artMap._panelArtistId != null) _artMapRefreshPanel(); return; }
    if (_artMap._panelArtistId === node.id) return; // already showing
    _artMap._panelArtistId = node.id;

    const hue = node._hue == null ? 270 : node._hue;
    const conn = _artMapConnCount(node);
    const pop = Math.max(0, Math.min(100, Math.round(node.popularity || 0)));
    const genres = (node.genres || []).slice(0, 5);
    const best = _artMapNodeBest(node);
    const typeLabel = (node.type === 'watchlist' || node.type === 'center') ? 'On watchlist' : 'Discovered';
    const bmp = _artMap.images[node.id];

    body.innerHTML = `
        <button onclick="_artMapPanelArtist(null)" style="background:rgba(255,255,255,0.06);border:1px solid rgba(255,255,255,0.1);color:rgba(255,255,255,0.7);border-radius:8px;padding:4px 10px;font-size:11px;cursor:pointer;margin-bottom:12px;">&#8592; Top artists</button>
        <div style="text-align:center;">
            <div style="width:120px;height:120px;margin:0 auto;border-radius:50%;overflow:hidden;border:2px solid hsl(${hue},80%,65%);box-shadow:0 8px 28px hsla(${hue},80%,40%,0.4);background:rgba(255,255,255,0.05);display:flex;align-items:center;justify-content:center;">
                ${bmp ? `<canvas id="artmap-card-canvas" width="120" height="120" style="width:120px;height:120px;"></canvas>`
                    : (node.image_url ? `<img src="${escapeHtml(node.image_url)}" style="width:100%;height:100%;object-fit:cover;" onerror="this.style.display='none'">` : '<span style="font-size:36px;opacity:0.5;">&#9835;</span>')}
            </div>
            <div style="font-size:18px;font-weight:800;margin-top:12px;">${escapeHtml(node.name)}</div>
            <div style="font-size:11px;color:hsl(${hue},70%,72%);margin-top:3px;">${typeLabel}</div>
        </div>
        ${genres.length ? `<div style="display:flex;flex-wrap:wrap;gap:6px;justify-content:center;margin-top:14px;">
            ${genres.map(g => `<span style="font-size:10.5px;padding:3px 9px;border-radius:999px;background:hsla(${hue},60%,55%,0.16);border:1px solid hsla(${hue},60%,60%,0.3);color:hsl(${hue},70%,82%);">${escapeHtml(g)}</span>`).join('')}
        </div>` : ''}
        <div style="display:flex;gap:8px;margin-top:16px;">
            ${_miniStat('Popularity', pop, hue)}
            ${_miniStat('Connections', conn)}
        </div>
        <div style="margin-top:8px;height:6px;background:rgba(255,255,255,0.08);border-radius:3px;overflow:hidden;">
            <div style="height:100%;width:${pop}%;background:linear-gradient(90deg,hsl(${hue},80%,60%),hsl(${(hue + 40) % 360},80%,62%));"></div>
        </div>
        <div style="display:flex;flex-direction:column;gap:8px;margin-top:18px;">
            <button onclick="artMapExploreArtist('${escapeForInlineJs(node.name)}')" style="background:linear-gradient(90deg,hsl(${hue},70%,52%),hsl(${(hue + 30) % 360},70%,54%));border:none;color:#fff;border-radius:10px;padding:10px;font-size:13px;font-weight:700;cursor:pointer;">Explore from here &rarr;</button>
            <div style="display:flex;gap:8px;">
                <button onclick="openYourArtistInfoModal_direct(_artMap._nodeById[${typeof node.id === 'number' ? node.id : `'${node.id}'`}])" style="flex:1;background:rgba(255,255,255,0.06);border:1px solid rgba(255,255,255,0.12);color:#fff;border-radius:10px;padding:9px;font-size:12px;cursor:pointer;">Details</button>
                ${_artMapWatchBtnHtml(node)}
            </div>
            ${best.id ? `<a href="${buildArtistDetailPath(best.id, best.source)}" style="text-align:center;color:rgba(255,255,255,0.55);font-size:11.5px;text-decoration:none;padding:4px;">Open artist page</a>` : ''}
        </div>`;

    if (bmp) {
        const c = document.getElementById('artmap-card-canvas');
        if (c) { try { c.getContext('2d').drawImage(bmp, 0, 0, 120, 120); } catch (e) { /* ignore */ } }
    }

    // Confirm watchlist membership from the server (refreshes the button if it
    // differs from the optimistic guess).
    _artMapCheckWatched(node);
}

// Top-list / external entry: show a node's card by id (also ripples it on the map).
function _artMapPanelArtistById(id) {
    const n = (_artMap._nodeById || {})[id];
    if (!n) return;
    _artMapPanelArtist(n);
    _artMapEmitRipple(n.x, n.y, n._hue);
}

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

        // Group every watchlist + similar artist into genre islands. Watchlist
        // artists are focal (sit centre-most + sized up). The discovery edges
        // (watchlist → similar) are remapped to the new node ids so the hover
        // constellation still shows who's related across islands.
        const rawNodes = data.nodes.map(n => ({ ...n, _focal: n.type === 'watchlist' }));
        const groups = _artMapGroupByGenre(rawNodes);
        _artMapLayoutIslands(groups);
        _artMap.edges = _artMapRemapEdges(data.edges);
        _artMap._oneIsland = true; // focus one genre island at a time
        _artMap._mapTitle = 'Watchlist Map';

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

            // Paint NOW — the map is fully interactive (pan/zoom/hover/click)
            // before a single image is fetched. The reveal animation blooms the
            // bubbles in (far field fades, near bubbles pop outward); images then
            // stream in behind it and sharpen in place. No blocking on N fetches.
            _artMap.dirty = true;

            const le = document.getElementById('artist-map-loading');
            if (le) le.remove();

            _artMapFocusIsland(0, { bloom: true }); // frame + bloom the first genre island
            _artMapStreamImages(_artMap.placed);
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
    // Stop the ambient buoyancy loop so it doesn't run with the map hidden.
    _artMap._ambient = false;
    _artMap._anim.running = false;
    if (_artMap._anim.raf) { cancelAnimationFrame(_artMap._anim.raf); _artMap._anim.raf = null; }
    _artMap._oneIsland = false;
    const navEl = document.getElementById('artmap-island-nav');
    if (navEl) navEl.remove();
    _artMapClosePanel();
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
    const scale = Math.min(z * 2, 1.0, _artMap.MAX_BUFFER_PX / Math.max(bw, bh));

    if (!_artMap.offscreen) _artMap.offscreen = document.createElement('canvas');
    const oc = _artMap.offscreen;
    oc.width = Math.ceil(bw * scale);
    oc.height = Math.ceil(bh * scale);
    const octx = oc.getContext('2d');
    _artMap._bufferScale = scale;
    _artMap._bufferMinX = minX;
    _artMap._bufferMinY = minY;
    // Freeze the live/buffer partition to this build's zoom (see _artMapIsLiveSize).
    _artMap._liveBuildZoom = _artMap.zoom;
    _artMap._drawAlphaMul = 1; // buffer bakes at full alpha; the blit applies the reveal fade

    // If more bubbles would be "live" than the live layer can draw, bake them ALL
    // into the buffer (set the overflow flag BEFORE the draw loop so the live-size
    // check below returns false and nothing is skipped). This is what prevents the
    // genre-overview "small/sparse" render: the live layer caps out, so let the
    // buffer own the whole crowd; live + bob only kick in once zoomed in.
    const bz = _artMap.zoom;
    let liveN = 0;
    for (const n of visible) { if (!n._isLabel && (n.radius || 0) * bz >= _artMap.LIVE_PX) liveN++; }
    // In one-island mode the buffer already covers just the focused island at
    // high resolution, so let the BUFFER own any non-trivial crowd (one cheap
    // crisp blit, no per-frame redraw → no lag). The live layer + bob/shove only
    // take over for small views (zoomed-in subsets, explore) where redrawing a
    // handful of bubbles each frame is cheap.
    _artMap._liveOverflow = liveN > 140;

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
            if (_artMapIsLiveSize(n)) continue; // big enough to read → drawn live on the overlay
            _artMapDrawNodeToBuffer(octx, n, scale);
        }
    }

    octx.globalAlpha = 1;

    _artMap.dirty = false;
}

// Draw a SINGLE node into the offscreen buffer (in world coords; caller has
// already applied the buffer's scale+translate). Shared by the full rebuild and
// the incremental image compositor so the two can never drift visually.
function _artMapDrawNodeToBuffer(octx, n, scale) {
    const op = n.opacity || 0;
    if (op < 0.01) return;
    const r = n.radius;
    const isW = n.type === 'watchlist' || n.type === 'center';
    // Global fade multiplier (reveal). Lets the whole map fade in cleanly while
    // each painter keeps its own per-element alpha.
    const mul = _artMap._drawAlphaMul == null ? 1 : _artMap._drawAlphaMul;
    octx.globalAlpha = op * mul;

    // Genre title — a clean floating label above its island (no big bubble).
    if (n._isLabel) {
        const hue = n._hue == null ? 270 : n._hue;
        const titleSize = Math.max(13, n.radius * 0.42);
        const name = (n.name || '').toUpperCase();
        octx.textAlign = 'center';
        octx.textBaseline = 'middle';
        // Soft glow behind the title for legibility over the water.
        octx.globalAlpha = mul;
        octx.font = `800 ${titleSize}px system-ui, sans-serif`;
        octx.shadowColor = `hsla(${hue},70%,12%,0.9)`;
        octx.shadowBlur = titleSize * 0.6;
        octx.fillStyle = `hsla(${hue},85%,82%,0.96)`;
        octx.fillText(name, n.x, n.y);
        octx.shadowBlur = 0;
        // Count subtitle
        octx.globalAlpha = 0.55 * mul;
        octx.font = `600 ${titleSize * 0.42}px system-ui, sans-serif`;
        octx.fillStyle = 'rgba(255,255,255,0.7)';
        octx.fillText(`${n._count || 0} artists`, n.x, n.y + titleSize * 0.85);
        octx.globalAlpha = 1;
        return;
    }

    // On-screen size drives detail. Album art shows at nearly every size (the
    // images are pre-masked to circles, so this is just a cheap drawImage — no
    // per-frame clip) for a consistent "sea of covers" look. Only the very
    // smallest fall back to a coloured dot.
    const rScaled = r * scale;
    const img = _artMap.images[n.id];

    if (rScaled < 2.2) {
        octx.beginPath();
        octx.arc(n.x, n.y, r, 0, Math.PI * 2);
        octx.fillStyle = isW ? '#6b21a8' : '#2a2a40';
        octx.fill();
        return;
    }

    // Focal glow ring for watchlist/center bubbles
    if (isW && rScaled >= 7) {
        octx.beginPath();
        octx.arc(n.x, n.y, r + 4, 0, Math.PI * 2);
        octx.strokeStyle = 'rgba(138,43,226,0.25)';
        octx.lineWidth = 5;
        octx.stroke();
    }

    // Body — pre-masked circular image (no clip) or a placeholder disc.
    if (img) {
        octx.drawImage(img, n.x - r, n.y - r, r * 2, r * 2);
    } else {
        octx.beginPath();
        octx.arc(n.x, n.y, r, 0, Math.PI * 2);
        octx.fillStyle = isW ? '#1a0a30' : '#141420';
        octx.fill();
    }

    // Glassy specular highlight (orb look) — only on bubbles big enough to read
    // it; skipping the dense swarm halves per-frame drawImage cost when zoomed in.
    if (rScaled >= 12) {
        octx.drawImage(_artMapGlossSprite(), n.x - r, n.y - r, r * 2, r * 2);
    }

    const showLabel = rScaled >= 13;

    // Darken art behind the label so the name stays legible.
    if (showLabel && img) {
        octx.beginPath();
        octx.arc(n.x, n.y, r, 0, Math.PI * 2);
        octx.fillStyle = 'rgba(0,0,0,0.42)';
        octx.fill();
    }

    // Border — tinted with the island's genre hue so clusters read as a family.
    octx.beginPath();
    octx.arc(n.x, n.y, r, 0, Math.PI * 2);
    if (isW) octx.strokeStyle = 'rgba(138,43,226,0.5)';
    else if (n._hue != null) octx.strokeStyle = `hsla(${n._hue},70%,70%,0.22)`;
    else octx.strokeStyle = 'rgba(255,255,255,0.10)';
    octx.lineWidth = isW ? 2 : (rScaled >= 7 ? 1 : 0.5);
    octx.stroke();

    if (showLabel) {
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

// Composite ONE node into the EXISTING buffer without a full rebuild. This is
// what makes image streaming cheap: when a bitmap arrives we redraw only that
// node (in place, over its placeholder) instead of redrawing all ~1500 nodes.
// Returns false if there's no buffer yet (or the node is hidden) so the caller
// can fall back to a full rebuild. Zoom changes rebuild the buffer at a new
// scale; this always reads the CURRENT buffer scale/origin, so it stays correct.
function _artMapCompositeNode(n) {
    const oc = _artMap.offscreen;
    const scale = _artMap._bufferScale;
    if (!oc || scale == null) return false;
    if (_artMap._hideSimilar && n.type !== 'watchlist' && n.type !== 'center' && !n._isLabel) return false;
    if ((n.opacity || 0) < 0.01) return false;
    // Live-layer bubbles aren't in the buffer — they read their image fresh each
    // frame, so just signal "blit" and let the overlay pick it up. Compositing
    // them here would double-draw (buffer copy + live copy).
    if (_artMapIsLiveSize(n)) return true;
    const octx = oc.getContext('2d');
    octx.save();
    octx.scale(scale, scale);
    octx.translate(-_artMap._bufferMinX, -_artMap._bufferMinY);
    _artMapDrawNodeToBuffer(octx, n, scale);
    octx.restore();
    octx.globalAlpha = 1;
    return true;
}

// A node renders on the live overlay when it's big enough on screen to read.
// Labels stay baked in the static buffer (no per-frame motion needed in v2).
// IMPORTANT: this uses the zoom the BUFFER WAS BUILT AT (_liveBuildZoom), not
// the live zoom. The buffer only rebuilds ~300ms after zooming stops, so the
// live/buffer split must stay frozen to whatever the (possibly stale) buffer
// excluded — otherwise a bubble could fall out of both during an active zoom
// and flicker. Both the buffer-exclude and the live-draw read this same value,
// so the two sets are always exact complements.
function _artMapIsLiveSize(n) {
    if (n._isLabel) return false;
    // When too many bubbles would be "live" at once (e.g. the genre overview),
    // the live layer's cap can't draw them all and the buffer would exclude them
    // → a sparse/half-rendered map. In that case treat NOTHING as live so the
    // buffer bakes everything (full, correct render); the live layer + bob only
    // take over once you've zoomed in to where few bubbles are big.
    if (_artMap._liveOverflow) return false;
    const z = _artMap._liveBuildZoom || _artMap.zoom;
    return (n.radius || 0) * z >= _artMap.LIVE_PX;
}

// Draw the live overlay: every big/near bubble, in world space, honouring its
// per-node animation transform (aScale for reveal/pop, opacity for fade). Kept
// cheap by viewport culling + a hard cap; the static far field is already on
// screen via the buffer blit.
function _artMapDrawLiveLayer(ctx) {
    const placed = _artMap.placed;
    if (!placed || !placed.length) return;
    const z = _artMap.zoom;
    const ox = _artMap.offsetX, oy = _artMap.offsetY;
    const w = _artMap.width, h = _artMap.height;
    const margin = 80;

    ctx.save();
    ctx.translate(ox, oy);
    ctx.scale(z, z);
    _artMap._drawAlphaMul = 1;

    // During the reveal the buffer is bypassed, so the live layer draws EVERY
    // bubble (and the genre titles) so they can all bloom. Otherwise it draws
    // only the big/near ones; the rest live in the static buffer.
    const revealing = _artMap._revealing;
    let drawn = 0;
    const CAP = revealing ? 2200 : 600;
    for (const n of placed) {
        if (!revealing && !_artMapIsLiveSize(n)) continue;
        if (_artMap._hideSimilar && n.type !== 'watchlist' && n.type !== 'center' && !n._isLabel) continue;
        // Viewport cull (screen space)
        const sx = ox + n.x * z, sy = oy + n.y * z;
        const rPx = (n.radius || 0) * z;
        if (sx + rPx < -margin || sx - rPx > w + margin || sy + rPx < -margin || sy - rPx > h + margin) continue;
        _artMapDrawLiveNode(ctx, n);
        if (++drawn >= CAP) break;
    }
    _artMap._drawAlphaMul = 1;
    ctx.restore();
    ctx.globalAlpha = 1;
    // Count of non-label bubbles drawn live — drives whether the ambient loop
    // keeps running (zoomed out = 0 = loop parks).
    _artMap._liveCount = revealing ? 0 : drawn;
}

// Tactile hover-pop: redraw the hovered bubble slightly larger with its cover
// + a bright hue ring, on top of everything. Works even when the bubble lives
// in the static buffer (genre islands), so hover always feels responsive.
// ctx is already in world space (translate(offset) + scale(zoom)).
function _artMapDrawHoverPop(ctx, n) {
    const r = n.radius;
    const hue = n._hue == null ? 270 : n._hue;
    const s = 1.16;
    const img = _artMap.images[n.id];
    ctx.save();
    ctx.translate(n.x, n.y); ctx.scale(s, s); ctx.translate(-n.x, -n.y);
    if (img) {
        ctx.drawImage(img, n.x - r, n.y - r, r * 2, r * 2);
    } else {
        ctx.beginPath(); ctx.arc(n.x, n.y, r, 0, Math.PI * 2);
        ctx.fillStyle = '#1a0a30'; ctx.fill();
    }
    ctx.beginPath(); ctx.arc(n.x, n.y, r, 0, Math.PI * 2);
    ctx.strokeStyle = `hsla(${hue},90%,78%,0.95)`;
    ctx.lineWidth = 2.5 / s; ctx.stroke();
    ctx.restore();
    ctx.beginPath(); ctx.arc(n.x, n.y, r * s + 5, 0, Math.PI * 2);
    ctx.strokeStyle = `hsla(${hue},85%,66%,0.45)`;
    ctx.lineWidth = 3; ctx.stroke();
}

// Draw one live bubble with its animation transform. aScale scales about the
// node centre; aAlpha fades it (folded into the global draw-alpha multiplier).
// Reuses the shared node painter so the bubble is identical to its baked form
// once settled.
function _artMapDrawLiveNode(ctx, n) {
    const sc = n.aScale == null ? 1 : n.aScale;
    if (sc <= 0.001) return;
    const baseMul = _artMap._drawAlphaMul == null ? 1 : _artMap._drawAlphaMul;
    _artMap._drawAlphaMul = baseMul * (n.aAlpha == null ? 1 : n.aAlpha);
    // Ambient buoyancy + ripple shove (steady state only — the reveal has its
    // own motion). Both are world-space offsets applied about the node centre.
    let ox = 0, oy = 0;
    if (_artMap._revealing) {
        if (n._revealRise) oy += n._revealRise; // surfacing rise during the bloom
    } else {
        if (n._bobAmp) oy += Math.sin((_artMap._now || 0) * 0.0016 + (n._bobPhase || 0)) * n._bobAmp;
        const disp = _artMapNodeDisplacement(n);
        if (disp) { ox += disp.dx; oy += disp.dy; }
    }
    if (sc !== 1 || ox || oy) {
        ctx.save();
        ctx.translate(n.x + ox, n.y + oy);
        ctx.scale(sc, sc);
        ctx.translate(-n.x, -n.y);
        _artMapDrawNodeToBuffer(ctx, n, _artMap.zoom);
        ctx.restore();
    } else {
        _artMapDrawNodeToBuffer(ctx, n, _artMap.zoom);
    }
    _artMap._drawAlphaMul = baseMul;
    ctx.globalAlpha = 1;
}

// ── Animation loop ────────────────────────────────────────────────────────
// Runs only while something is animating; idles otherwise. Each tick advances
// the active animations (reveal field-fade + per-node pop), draws one frame,
// and re-arms itself only if work remains — so a still map costs nothing.
function _artMapStartLoop() {
    const a = _artMap._anim;
    if (a.running) return;
    a.running = true;
    a.last = performance.now();
    const tick = (t) => {
        if (!a.running) return;
        _artMap._now = t;
        const more = _artMapStepAnimations(t);
        // Cap the whole animation loop at ~30fps. The reveal bloom, ripples and
        // ambient bob all read fine at 30, and halving the redraws keeps the
        // 1800-bubble genre map smooth instead of churning every frame. Always
        // honour a pending buffer rebuild (dirty) so the throttle can't skip the
        // frame that bakes the map after the reveal ends.
        if (_artMap.dirty || (t - (a._lastDraw || 0)) >= 31) {
            _artMapDraw(); // sets _artMap._liveCount
            a._lastDraw = t;
        }
        const keep = more || (_artMap._ambient && _artMap._liveCount > 0 && !document.hidden);
        if (keep) {
            a.raf = requestAnimationFrame(tick);
        } else {
            a.running = false;
            a.raf = null;
        }
    };
    a.raf = requestAnimationFrame(tick);
}

// (Re)start the ambient loop if buoyancy is on and it isn't already running —
// called after the reveal and on zoom/pan so bob resumes when bubbles appear.
function _artMapEnsureAmbient() {
    if (_artMap._ambient && !_artMap._anim.running && !document.hidden) _artMapStartLoop();
}

// Advance every active animation by absolute time t (ms). Returns true while
// anything is still moving. Phase C: each bubble scales+fades in (ease-out)
// once past its staggered start, and a water ripple expands from each island.
function _artMapStepAnimations(t) {
    let active = false;

    const placed = _artMap.placed;
    if (placed) {
        for (const n of placed) {
            if (n.aScale == null || n.aScale >= 1) continue;
            if (t < n._revealAt) { active = true; continue; }
            const p = Math.min(1, (t - n._revealAt) / (n._revealDur || 480));
            if (p >= 1) { n.aScale = 1; n.aAlpha = 1; n._revealRise = 0; }
            else {
                // Scale eases in with a gentle overshoot (ease-out-back, subtle),
                // alpha fades a touch faster, and the bubble rises up into place
                // like it's surfacing through water — the remaining rise decays
                // as (1-p)^3.
                const c1 = 1.18, c3 = c1 + 1;
                const back = 1 + c3 * Math.pow(p - 1, 3) + c1 * Math.pow(p - 1, 2);
                n.aScale = back;
                n.aAlpha = Math.min(1, p * 1.6);
                n._revealRise = Math.pow(1 - p, 3) * (n._riseAmp || 0);
                active = true;
            }
        }
    }

    const rip = _artMap._ripples;
    if (rip && rip.length) {
        let anyAlive = false;
        for (const r of rip) if (t < r.t0 + r.dur) anyAlive = true;
        if (anyAlive) active = true; else _artMap._ripples = [];
    }

    // When the bloom finishes, leave reveal mode and bake everything into the
    // static buffer (one rebuild) so steady-state goes back to the cheap path.
    if (!active && _artMap._revealing) {
        _artMap._revealing = false;
        _artMap.dirty = true;
        return true; // one more frame to do the rebuild + final blit
    }
    return active;
}

// Kick off the ripple-bloom reveal. Each island blooms in turn (staggered by
// island order); within an island, bubbles fade+scale outward from the centre
// like a drop hitting water, and a ripple ring expands from each island centre.
// During the reveal the whole map renders on the live layer (the static buffer
// is bypassed) so every bubble can animate; it bakes into the buffer at the end.
function _artMapBeginReveal() {
    const t0 = performance.now();
    _artMap._revealT0 = t0;
    _artMap._revealing = true;
    _artMap._ambient = true; // keep the loop alive afterwards for buoyancy
    _artMap._fieldAlpha = 1; // buffer is bypassed while revealing; live layer draws all

    const islands = _artMap._islands || [];
    const islByName = {};
    islands.forEach((isl, i) => { isl._order = i; islByName[isl.name] = isl; });

    const ISL_STAGGER = 145, RADIAL_MS = 430, NODE_DUR = 470;
    for (const n of _artMap.placed) {
        n.aScale = 0; n.aAlpha = 0;
        const isl = islByName[n._island] || (n._isLabel ? islByName[n.name] : null);
        const order = isl ? isl._order : 0;
        let radial = 0;
        if (isl && isl.r > 0) radial = Math.min(1, Math.hypot(n.x - isl.cx, n.y - isl.cy) / isl.r);
        n._revealAt = t0 + order * ISL_STAGGER + radial * RADIAL_MS + (n._isLabel ? 90 : 0);
        n._revealDur = NODE_DUR;
    }

    _artMap._ripples = islands.map(isl => ({
        cx: isl.cx, cy: isl.cy, hue: isl.hue,
        maxR: isl.r * 1.45, t0: t0 + isl._order * ISL_STAGGER, dur: 1150,
    }));

    _artMapStartLoop();
}

// Draw the expanding water-ripple rings (reveal + click). Cheap stroked arcs,
// hue-tinted, fading as they grow. Drawn in world space with a screen-constant
// line width.
function _artMapDrawRipples(ctx) {
    const rip = _artMap._ripples;
    if (!rip || !rip.length) return;
    const t = performance.now();
    const z = _artMap.zoom;
    ctx.save();
    ctx.translate(_artMap.offsetX, _artMap.offsetY);
    ctx.scale(z, z);
    for (const r of rip) {
        const p = (t - r.t0) / r.dur;
        if (p < 0 || p > 1) continue;
        const radius = r.maxR * (0.08 + 0.92 * (1 - Math.pow(1 - p, 2)));
        const alpha = (1 - p) * 0.55;
        ctx.beginPath();
        ctx.arc(r.cx, r.cy, radius, 0, Math.PI * 2);
        ctx.strokeStyle = `hsla(${r.hue},85%,72%,${alpha})`;
        ctx.lineWidth = (1.5 + 6 * (1 - p)) / z; // ~constant on screen
        ctx.stroke();
    }
    ctx.restore();
}

// Total world-space displacement on a node from all active "push" ripples — the
// expanding wavefront shoves nearby bubbles radially outward, then they settle
// back as the wave passes and decays. Returns null when nothing is pushing.
function _artMapNodeDisplacement(n) {
    const rip = _artMap._ripples;
    if (!rip || !rip.length) return null;
    const t = _artMap._now || performance.now();
    let dx = 0, dy = 0;
    for (const r of rip) {
        if (!r.push) continue;
        const p = (t - r.t0) / r.dur;
        if (p < 0 || p > 1) continue;
        const front = r.maxR * (0.08 + 0.92 * (1 - Math.pow(1 - p, 2)));
        const ddx = n.x - r.cx, ddy = n.y - r.cy;
        const d = Math.hypot(ddx, ddy) || 1;
        const delta = d - front;
        const width = r.width || (r.maxR * 0.2);
        const env = Math.exp(-(delta * delta) / (2 * width * width)); // bump at the wavefront
        const push = r.push * env * (1 - p);                          // decays over the ripple's life
        if (push > 0.05) { dx += (ddx / d) * push; dy += (ddy / d) * push; }
    }
    return (dx || dy) ? { dx, dy } : null;
}

// Emit a water ripple at a world point — a fading ring plus a radial shove of
// nearby bubbles. Used for click/tap feedback.
function _artMapEmitRipple(wx, wy, hue) {
    if (!_artMap._ripples) _artMap._ripples = [];
    const WR = _artMap.WATCHLIST_R;
    _artMap._ripples.push({
        cx: wx, cy: wy, hue: hue == null ? 270 : hue,
        maxR: WR * 2.6, t0: performance.now(), dur: 900,
        push: WR * 0.22, width: WR * 0.6,
    });
    _artMapStartLoop(); // animate the ripple (guards against double-start)
}

function _artMapRender() {
    // v2 perf: coalesce every render request into a single rAF, so a burst of
    // mousemove/pan/animation calls never draws more than once per frame.
    if (_artMap._rafPending) return;
    _artMap._rafPending = requestAnimationFrame(() => {
        _artMap._rafPending = null;
        _artMapDraw();
    });
}

function _artMapDraw() {
    /**Blit offscreen buffer to screen canvas with pan/zoom. Near-zero cost.**/
    const _t0 = _artMap._perf ? performance.now() : 0;
    if (!_artMap._anim.running) _artMap._now = performance.now(); // keep bob current on on-demand draws
    const ctx = _artMap.ctx;
    const w = _artMap.width;
    const h = _artMap.height;

    ctx.fillStyle = '#0a0a14';
    ctx.fillRect(0, 0, w, h);

    // Premium backdrop: a soft central glow fading to a dark vignette. The
    // gradient is cached and only rebuilt on resize, so it's one cheap fillRect.
    if (!_artMap._bgGrad || _artMap._bgW !== w || _artMap._bgH !== h) {
        const g = ctx.createRadialGradient(w / 2, h * 0.42, Math.min(w, h) * 0.12,
            w / 2, h / 2, Math.max(w, h) * 0.78);
        g.addColorStop(0, 'rgba(46,34,78,0.40)');
        g.addColorStop(0.5, 'rgba(16,12,28,0.0)');
        g.addColorStop(1, 'rgba(0,0,0,0.55)');
        _artMap._bgGrad = g; _artMap._bgW = w; _artMap._bgH = h;
    }
    ctx.fillStyle = _artMap._bgGrad;
    ctx.fillRect(0, 0, w, h);

    const z = _artMap.zoom;

    // Soft genre-hued halo behind the focused island (one-island mode) — gives
    // the island a sense of place on the water. Cached sprite → one drawImage.
    if (_artMap._oneIsland && _artMap._islands && _artMap._islands.length) {
        const isl = _artMap._islands[_artMap._focusIdx || 0];
        if (isl) {
            const hr = (isl.r * 2.5) * z;
            const hsx = _artMap.offsetX + isl.cx * z;
            const hsy = _artMap.offsetY + isl.cy * z;
            ctx.drawImage(_artMapHaloSprite(isl.hue), hsx - hr, hsy - hr, hr * 2, hr * 2);
        }
    }

    // While the ripple-bloom reveal is running, bypass the static buffer
    // entirely and let the live layer draw every bubble (so each can animate).
    // The buffer is (re)built once when the reveal ends.
    if (!_artMap._revealing) {
        if (_artMap.dirty || !_artMap.offscreen) {
            const _rt = _artMap._perf ? performance.now() : 0;
            _artMapRebuildBuffer();
            if (_artMap._perf) _artMap._rebuildMs = performance.now() - _rt;
        }
        if (_artMap.offscreen) {
            const oc = _artMap.offscreen;
            const s = _artMap._bufferScale;
            const mx = _artMap._bufferMinX;
            const my = _artMap._bufferMinY;
            // Blit offscreen buffer (built with scale(s) + translate(-minX,-minY)).
            const fieldAlpha = _artMap._fieldAlpha == null ? 1 : _artMap._fieldAlpha;
            if (fieldAlpha < 0.999) ctx.globalAlpha = fieldAlpha;
            ctx.drawImage(oc,
                _artMap.offsetX + mx * z,
                _artMap.offsetY + my * z,
                oc.width * z / s,
                oc.height * z / s
            );
            ctx.globalAlpha = 1;
        }
    }

    // ── Live overlay layer: big/near bubbles every frame (during the reveal,
    // ALL bubbles) so they can scale/bob/ripple. Viewport-culled + capped. ──
    _artMapDrawLiveLayer(ctx);
    _artMapDrawRipples(ctx);

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

                // Connection lines — build the path ONCE, then two cheap strokes
                // (wide faint halo + crisp core) for a glow look without per-frame
                // gradients or shadowBlur (those were the hover-lag culprits).
                ctx.lineCap = 'round';
                ctx.beginPath();
                for (const cn of highlightNodes) {
                    if (cn === n) continue;
                    ctx.moveTo(n.x, n.y);
                    ctx.lineTo(cn.x, cn.y);
                }
                ctx.strokeStyle = `rgba(168,85,247,${0.18 * cFade})`;
                ctx.lineWidth = 6;
                ctx.stroke();
                ctx.strokeStyle = `rgba(201,150,255,${0.6 * cFade})`;
                ctx.lineWidth = 1.5;
                ctx.stroke();

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

                    // Circle + image — pre-masked, so no per-frame clip (that was
                    // the hover-lag culprit once the ambient loop forced redraws).
                    const img = _artMap.images[hn.id];
                    if (img) {
                        ctx.drawImage(img, hn.x - r, hn.y - r, r * 2, r * 2);
                        ctx.beginPath();
                        ctx.arc(hn.x, hn.y, r, 0, Math.PI * 2);
                        ctx.fillStyle = 'rgba(0,0,0,0.35)'; // keep the name legible over art
                        ctx.fill();
                    } else {
                        ctx.beginPath();
                        ctx.arc(hn.x, hn.y, r, 0, Math.PI * 2);
                        ctx.fillStyle = isW ? '#1a0a30' : '#141420';
                        ctx.fill();
                    }

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
                // Single node, no connections — pop the hovered bubble.
                _artMapDrawHoverPop(ctx, n);
            }

            ctx.restore();
        } // end if(n)
    } else if (_artMap.hoveredNode && !_artMap._constellationActive) {
        // Pre-constellation: instant tactile pop on the hovered bubble.
        ctx.save();
        ctx.translate(_artMap.offsetX, _artMap.offsetY);
        ctx.scale(z, z);
        _artMapDrawHoverPop(ctx, _artMap.hoveredNode);
        ctx.restore();
    }

    if (_artMap._perf) _artMapDrawPerf(ctx, _t0);
}

// Toggle with 'd' on the map. Shows where frame time goes so we optimise the
// real bottleneck (buffer rebuild on zoom vs. blit on pan) instead of guessing.
function _artMapDrawPerf(ctx, t0) {
    const drawMs = performance.now() - t0;
    const now = performance.now();
    const dt = _artMap._lastPerfTs ? now - _artMap._lastPerfTs : 0;
    _artMap._lastPerfTs = now;
    const fps = dt > 0 ? Math.round(1000 / dt) : 0;
    const oc = _artMap.offscreen;

    // Ship the numbers to app.log (~1.5/s) so they can be read server-side —
    // the on-canvas text below can't be copied, especially mid-lag.
    if (!_artMap._perfPostTs || now - _artMap._perfPostTs > 700) {
        _artMap._perfPostTs = now;
        try {
            fetch('/api/discover/artist-map/perf', {
                method: 'POST', headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    nodes: _artMap.placed.length, edges: (_artMap.edges || []).length,
                    buffer: oc ? oc.width + 'x' + oc.height : '-',
                    scale: +(_artMap._bufferScale || 0).toFixed(3),
                    zoom: +_artMap.zoom.toFixed(3),
                    rebuildMs: +(_artMap._rebuildMs || 0).toFixed(1),
                    drawMs: +drawMs.toFixed(1), fps,
                }),
            }).catch(() => { });
        } catch (e) { /* ignore */ }
    }

    const lines = [
        `nodes ${_artMap.placed.length}   edges ${(_artMap.edges || []).length}`,
        `buffer ${oc ? oc.width + '×' + oc.height : '—'}   scale ${(_artMap._bufferScale || 0).toFixed(3)}`,
        `zoom ${_artMap.zoom.toFixed(3)}`,
        `rebuild ${(_artMap._rebuildMs || 0).toFixed(1)}ms   draw ${drawMs.toFixed(1)}ms`,
        `~${fps} fps (while interacting)`,
    ];
    ctx.save();
    ctx.setTransform(1, 0, 0, 1, 0, 0); // device pixels, ignore dpr scale
    ctx.font = '12px monospace';
    ctx.textAlign = 'left';
    ctx.textBaseline = 'top';
    const pad = 8, lh = 16;
    ctx.fillStyle = 'rgba(0,0,0,0.72)';
    ctx.fillRect(10, 10, 270, lines.length * lh + pad * 2);
    ctx.fillStyle = '#7CFC00';
    lines.forEach((l, i) => ctx.fillText(l, 10 + pad, 10 + pad + i * lh));
    ctx.restore();
}

// Toolbar search: query the metadata source for ANY artist (like the discover
// page) and launch an exploration on click — not just filter the current map.
function artMapSearch(query) {
    const results = document.getElementById('artist-map-search-results');
    if (!results) return;
    const q = (query || '').trim();
    if (q.length < 2) { results.style.display = 'none'; results.innerHTML = ''; return; }

    clearTimeout(_artMap._searchTimer);
    _artMap._searchTimer = setTimeout(async () => {
        const myToken = (_artMap._searchToken = (_artMap._searchToken || 0) + 1);
        results.style.display = 'block';
        results.innerHTML = '<div class="artist-map-search-item artist-map-search-empty">Searching…</div>';
        try {
            const resp = await fetch(`/api/discover/build-playlist/search-artists?query=${encodeURIComponent(q)}`);
            const data = await resp.json();
            if (myToken !== _artMap._searchToken) return; // superseded by a newer keystroke
            const artists = (data && data.success && Array.isArray(data.artists)) ? data.artists : [];
            if (!artists.length) {
                results.innerHTML = '<div class="artist-map-search-item artist-map-search-empty">No artists found</div>';
                return;
            }
            results.innerHTML = artists.slice(0, 8).map(a =>
                `<div class="artist-map-search-item" onclick="artMapExploreArtist('${escapeForInlineJs(a.name)}')">
                    <span class="artist-map-search-type similar">○</span>
                    ${escapeHtml(a.name)}
                    <span class="artist-map-search-go">Explore &rarr;</span>
                </div>`
            ).join('');
        } catch (e) {
            if (myToken === _artMap._searchToken) {
                results.innerHTML = '<div class="artist-map-search-item artist-map-search-empty">Search failed — try again</div>';
            }
        }
    }, 300);
}

// Launch the explorer for a searched artist (closes the dropdown first).
function artMapExploreArtist(name) {
    const results = document.getElementById('artist-map-search-results');
    if (results) { results.style.display = 'none'; results.innerHTML = ''; }
    const input = document.getElementById('artist-map-search');
    if (input) input.value = '';
    _artMap._skipSectionToggle = true;
    _openArtistMapExplorerWithName(name);
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
    if (!node) { tip.style.display = 'none'; _artMap._tipNodeId = null; return; }

    // v2 perf: only rebuild the tooltip's innerHTML (and reload its image) when
    // the hovered artist actually changes — a plain mousemove just repositions.
    if (_artMap._tipNodeId !== node.id) {
        _artMap._tipNodeId = node.id;
        // Prefer the already-decoded (pre-masked) bitmap — instant, and it can't
        // churn-blank while sweeping across dense zoomed-in bubbles the way a
        // fresh <img src> reload does. Fall back to the URL, then a glyph.
        const bmp = _artMap.images[node.id];
        const img = bmp
            ? '<canvas class="artmap-tip-img" width="88" height="88"></canvas>'
            : (node.image_url ? `<img class="artmap-tip-img" src="${escapeHtml(node.image_url)}" alt="">` : '<div class="artmap-tip-img artmap-tip-img-fallback">&#9835;</div>');
        const genres = (node.genres || []).slice(0, 3);
        const genreHTML = genres.length ? `<div class="artmap-tip-genres">${genres.map(g => `<span>${escapeHtml(g)}</span>`).join('')}</div>` : '';
        const typeLabel = node.type === 'watchlist' ? '<span class="artmap-tip-badge">★ Watchlist</span>' : '';
        // Real connection count from the map's edges (cheap; only on hover change).
        let conn = 0;
        const edges = _artMap.edges || [];
        for (const ed of edges) { if (ed.source === node.id || ed.target === node.id) conn++; }
        const connHTML = conn ? `<div class="artmap-tip-conn">${conn} connection${conn === 1 ? '' : 's'}</div>` : '';
        tip.innerHTML = `
            <div class="artmap-tip-row">
                ${img}
                <div class="artmap-tip-info">
                    <div class="artmap-tip-name">${escapeHtml(node.name)}</div>
                    ${typeLabel}
                    ${connHTML}
                    ${genreHTML}
                </div>
            </div>
        `;
        tip.style.display = 'block';
        // Paint the cached bitmap into the tooltip canvas (instant, no reload).
        if (bmp) {
            const c = tip.querySelector('canvas.artmap-tip-img');
            if (c) { try { c.getContext('2d').drawImage(bmp, 0, 0, 88, 88); } catch (e) { /* ignore */ } }
        }
    }

    // Position — keep on screen (cheap; runs every move)
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

        // Build genre-island groups from the selected + related genres and lay
        // them out as filled-disc islands on the water (shared engine).
        const groups = genres.map(g => ({
            name: g.name,
            count: g.count,
            nodes: (g.artist_ids || []).map(nid => data.nodes[nid]).filter(Boolean),
        }));
        _artMapLayoutIslands(groups);
        _artMap.edges = [];
        _artMap._oneIsland = true; // focus one genre island at a time
        _artMap._mapTitle = 'Genre Map';
        const placedCount = _artMap.placed.filter(n => !n._isLabel).length;

        _artMapSetupInteraction(canvas);

        // Load images + render
        if (loadingText) loadingText.textContent = `Rendering ${placedCount} artists...`;

        const le = document.getElementById('artist-map-loading');
        if (le) le.remove();

        _artMapFocusIsland(0, { bloom: true }); // frame + bloom the selected genre island

        // Stream images in throttled waves — interactive immediately, sharpens in place.
        _artMapStreamImages(_artMap.placed.filter(n => !n._isLabel));

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

        // Group the center + all discovered artists into genre islands. The
        // center artist is focal. Discovery edges (center → similar → extended)
        // are remapped so the hover constellation still traces how you got from
        // one artist to another across the islands.
        const rawNodes = data.nodes.map(n => ({ ...n, _focal: n.ring === 0 || n.type === 'center' }));
        const groups = _artMapGroupByGenre(rawNodes);
        _artMapLayoutIslands(groups);
        _artMap.edges = _artMapRemapEdges(data.edges);
        _artMap._oneIsland = false; // explore stays multi-island (it's small)
        _artMap._mapTitle = 'Explore: ' + (data.center || name);
        _artMapUpdateIslandNav(); // tear down any leftover nav from a prior map
        _artMapFitToContent();
        _artMapRefreshPanel();

        _artMapSetupInteraction(canvas);

        // Load images
        const loadingText = container.querySelector('.artist-map-loading-text');
        if (loadingText) loadingText.textContent = `Loading ${_artMap.placed.length} artists...`;

        const le = document.getElementById('artist-map-loading');
        if (le) le.remove();

        _artMap.dirty = true;
        _artMapBeginReveal();

        // Stream images in throttled waves — interactive immediately, sharpens in place.
        _artMapStreamImages(_artMap.placed);

    } catch (err) {
        console.error('Artist explorer error:', err);
        const lt = container.querySelector('.artist-map-loading-text');
        if (lt) lt.textContent = 'Error loading explorer';
    }
}

function _showArtistMapSearchPrompt() {
    // Search the metadata source and make the user PICK a real artist, rather
    // than exploring whatever loose text they typed. Resolves with the chosen
    // artist's resolved name (which the explorer hands to /artist-map/explore),
    // or null if cancelled.
    return new Promise(resolve => {
        const existing = document.getElementById('artmap-search-prompt');
        if (existing) existing.remove();

        let done = false;
        let overlay;
        const finish = (val) => { if (done) return; done = true; if (overlay) overlay.remove(); resolve(val); };

        overlay = document.createElement('div');
        overlay.id = 'artmap-search-prompt';
        overlay.className = 'modal-overlay';
        overlay.onclick = (e) => { if (e.target === overlay) finish(null); };

        overlay.innerHTML = `
            <div class="artmap-search-prompt-modal">
                <div class="artmap-search-prompt-header">
                    <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5">
                        <circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/>
                        <line x1="11" y1="8" x2="11" y2="14"/><line x1="8" y1="11" x2="14" y2="11"/>
                    </svg>
                    <div>
                        <h3>Artist Explorer</h3>
                        <p>Search and pick an artist to explore</p>
                    </div>
                </div>
                <div class="artmap-explore-search-wrap">
                    <input type="text" id="artmap-explore-input" class="artmap-explore-input"
                           placeholder="Search artists…" autocomplete="off" autofocus>
                    <div class="artmap-explore-spinner" id="artmap-explore-spinner" style="display:none">
                        <div class="watch-all-loading-spinner"></div>
                    </div>
                </div>
                <div class="artmap-explore-results" id="artmap-explore-results"></div>
                <div class="artmap-search-prompt-actions">
                    <button class="btn btn--sm btn--secondary ya-header-btn" id="artmap-explore-cancel">Cancel</button>
                </div>
            </div>
        `;
        document.body.appendChild(overlay);

        const input = overlay.querySelector('#artmap-explore-input');
        const results = overlay.querySelector('#artmap-explore-results');
        const spinner = overlay.querySelector('#artmap-explore-spinner');
        overlay.querySelector('#artmap-explore-cancel').onclick = () => finish(null);

        const renderResults = (artists) => {
            results.innerHTML = '';
            if (!artists.length) {
                results.innerHTML = '<div class="artmap-explore-empty">No artists found</div>';
                return;
            }
            artists.forEach(a => {
                const img = a.image_url || '/static/placeholder-album.png';
                const row = document.createElement('div');
                row.className = 'artmap-explore-result';
                row.innerHTML = `
                    <img src="${escapeHtml(img)}" alt="" loading="lazy" onerror="this.src='/static/placeholder-album.png'">
                    <span class="artmap-explore-result-name">${escapeHtml(a.name)}</span>
                    <span class="artmap-explore-result-go">Explore &rarr;</span>`;
                row.onclick = () => finish(a.name);   // pick the resolved artist, not raw text
                results.appendChild(row);
            });
        };

        let timer = null;
        let token = 0;
        const doSearch = () => {
            const q = input.value.trim();
            if (!q) { results.innerHTML = ''; spinner.style.display = 'none'; clearTimeout(timer); return; }
            clearTimeout(timer);
            timer = setTimeout(async () => {
                const myToken = ++token;
                spinner.style.display = 'flex';
                try {
                    const resp = await fetch(`/api/discover/build-playlist/search-artists?query=${encodeURIComponent(q)}`);
                    const data = await resp.json();
                    if (myToken !== token) return;  // a newer keystroke superseded this
                    renderResults((data && data.success && Array.isArray(data.artists)) ? data.artists : []);
                } catch (e) {
                    if (myToken === token) results.innerHTML = '<div class="artmap-explore-empty">Search failed — try again</div>';
                } finally {
                    if (myToken === token) spinner.style.display = 'none';
                }
            }, 350);
        };

        input.addEventListener('input', doSearch);
        input.addEventListener('keydown', (e) => {
            if (e.key === 'Enter') {
                const first = results.querySelector('.artmap-explore-result');
                if (first) first.click();        // Enter = pick top match, never raw text
            } else if (e.key === 'Escape') {
                finish(null);
            }
        });
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

// Artist images come in at up to 1000×1000. Nodes are drawn tiny, so holding
// full-res bitmaps is pointless and ruinous: ~1500 nodes × 1000² × 4 bytes ≈ 6 GB
// of decoded image memory → GC/GPU thrash that locks the browser even though the
// per-frame draw is cheap. Decode straight to a small avatar (~128px) so the
// whole map's images fit in ~100 MB instead of gigabytes.
// Decode to a sensible avatar size for how big the node actually draws — crisp
// where it matters (focal/watchlist nodes), light for the swarm of small ones —
// so total image memory stays ~150-250 MB instead of multiple GB.
function _artMapImgPx(px) {
    return Math.min(384, Math.max(112, Math.round(px || 144)));
}
function _artMapDecodeSmall(blob, px) {
    if (!blob) return Promise.resolve(null);
    const d = _artMapImgPx(px);
    try {
        return createImageBitmap(blob, {
            resizeWidth: d, resizeHeight: d, resizeQuality: 'high',
        }).then(_artMapCircleMask)
          .catch(() => createImageBitmap(blob).then(_artMapCircleMask).catch(() => null));
    } catch (e) {
        return createImageBitmap(blob).then(_artMapCircleMask).catch(() => null);
    }
}

// Pre-mask a decoded bitmap into a CIRCLE once, at load time, returning a
// canvas. The whole map then draws bubbles with a plain drawImage (the canvas
// is already round) instead of a per-frame ctx.clip() per bubble — clipping is
// one of the most expensive canvas ops and, at hundreds of visible bubbles per
// frame, was the live-layer stutter. Done once here, it's free forever after.
function _artMapCircleMask(src) {
    if (!src) return null;
    const w = src.width || 0;
    if (!w) return src;
    try {
        const c = document.createElement('canvas');
        c.width = w; c.height = w;
        const cx = c.getContext('2d');
        cx.beginPath();
        cx.arc(w / 2, w / 2, w / 2, 0, Math.PI * 2);
        cx.closePath();
        cx.clip();
        cx.drawImage(src, 0, 0, w, w);
        if (src.close) src.close(); // free the ImageBitmap; we keep the canvas
        return c;
    } catch (e) {
        return src; // fall back to the raw bitmap (draw path still clips defensively)
    }
}

function _artMapLoadImage(url, px) {
    // Try direct CORS fetch first (zero server load, works for Spotify/iTunes/Discogs)
    return fetch(url, { mode: 'cors' })
        .then(r => r.ok ? r.blob() : Promise.reject('not ok'))
        .then(b => _artMapDecodeSmall(b, px))
        .catch(() => {
            // Fallback: server proxy for CDNs without CORS headers
            return fetch('/api/image-proxy?url=' + encodeURIComponent(url))
                .then(r => r.ok ? r.blob() : null)
                .then(b => _artMapDecodeSmall(b, px))
                .catch(() => null);
        });
}

// Target avatar px for a node, based on its world radius (≈ its on-screen size
// at full zoom). Focal/watchlist nodes get a big crisp avatar; small ones stay
// light. Used by every map's image loader.
function _artMapNodeImgPx(n) {
    const isFocal = n.type === 'watchlist' || n.type === 'center' || n.ring === 1;
    return _artMapImgPx(isFocal ? Math.max(256, (n.radius || 0) * 1.4) : (n.radius || 0) * 1.6);
}

// Stream node images in the background WITHOUT blocking the first paint. The map
// is drawn immediately with placeholder circles and stays fully interactive
// (click/hover/pan) while images fill in. Redraws are throttled into ~waves so
// 1000s of arrivals don't trigger 1000s of buffer rebuilds. A load token makes
// opening another map cancel this stream (stale bitmaps are dropped). Focal
// nodes are fetched first so what you're looking at sharpens soonest.
function _artMapStreamImages(imgNodes, concurrent = 24) {
    const token = (_artMap._loadToken = (_artMap._loadToken || 0) + 1);
    // Focal/large nodes first — the user's eye lands there.
    const queue = imgNodes.filter(n => n.image_url).slice().sort((a, b) => (b.radius || 0) - (a.radius || 0));
    let idx = 0, inFlight = 0, redrawPending = false;

    // Throttled FULL rebuild as images arrive. The per-map buffer is now small
    // (one focused island / a small explore map), so a full rebuild is cheap and
    // — unlike the per-node composite — is guaranteed to pick up every cached
    // image. This is what makes streamed art appear on its own instead of only
    // after a manual zoom forced a rebuild.
    const scheduleRedraw = () => {
        if (redrawPending || token !== _artMap._loadToken) return;
        redrawPending = true;
        setTimeout(() => {
            redrawPending = false;
            if (token !== _artMap._loadToken) return;
            _artMap.dirty = true;
            _artMapRender();
            _artMapEnsureAmbient();
        }, 200);
    };

    function pump() {
        if (token !== _artMap._loadToken) return; // a newer map took over
        while (inFlight < concurrent && idx < queue.length) {
            const n = queue[idx++];
            if (_artMap.images[n.id]) continue;
            inFlight++;
            _artMapLoadImage(n.image_url, _artMapNodeImgPx(n))
                .then(bmp => {
                    if (bmp && token === _artMap._loadToken) {
                        _artMap.images[n.id] = bmp;
                        // Hidden bubbles (other islands in one-island mode): just
                        // cache the image for when you navigate there — don't
                        // redraw for something off-screen.
                        if ((n.opacity || 0) < 0.01) return;
                        // Throttled full rebuild — reliably bakes newly-arrived art
                        // into the (small) buffer. No manual zoom needed.
                        scheduleRedraw();
                    }
                })
                .finally(() => { inFlight--; pump(); });
        }
    }
    pump();
}

function _artMapHideContextMenu() {
    const m = document.getElementById('artist-map-context');
    if (m) m.style.display = 'none';
}

function _artMapSetupInteraction(canvas) {
    // Prevent stacking listeners on repeated opens
    if (canvas._artMapListenersAttached) return;
    canvas._artMapListenersAttached = true;

    // Pause ambient buoyancy when the tab is hidden; resume on return.
    document.addEventListener('visibilitychange', () => {
        if (!document.hidden) _artMapEnsureAmbient();
    });

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
        _artMapEnsureAmbient(); // resume buoyancy if we zoomed bubbles into view
        // Debounce hi-res rebuild after zoom settles; then resume buoyancy (the
        // rebuild may have flipped the live/overflow partition).
        clearTimeout(_artMap._zoomRebuild);
        _artMap._zoomRebuild = setTimeout(() => { _artMap.dirty = true; _artMapRender(); _artMapEnsureAmbient(); }, 300);
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
        else if (e.key === 'd' || e.key === 'D') { _artMap._perf = !_artMap._perf; _artMapRender(); e.preventDefault(); }
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
        else if (_artMap._oneIsland && e.key === 'ArrowLeft') { _artMapIslandNav(-1); e.preventDefault(); }
        else if (_artMap._oneIsland && e.key === 'ArrowRight') { _artMapIslandNav(1); e.preventDefault(); }
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
            <a class="artmap-ctx-item" href="${bestId ? buildArtistDetailPath(bestId, bestSource) : '#'}" onclick="_artMapHideContextMenu()" ${bestId ? '' : 'aria-disabled="true" style="pointer-events:none;opacity:0.5;text-decoration:none;color:inherit;"'}>
                <span>&#128191;</span> View Discography
            </a>
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
                // Debounce the side-panel card: only swap to a bubble you've
                // settled on for ~0.8s, so sweeping toward the panel doesn't keep
                // changing the card on bubbles you pass over en route.
                clearTimeout(_artMap._panelTimer);
                if (_artMap.hoveredNode) {
                    const target = _artMap.hoveredNode;
                    _artMap._panelTimer = setTimeout(() => {
                        if (_artMap.hoveredNode === target) _artMapPanelArtist(target);
                    }, 800);
                }
            }
            if (prev !== _artMap.hoveredNode) {
                // Reset constellation highlight timer
                clearTimeout(_artMap._constellationTimer);
                if (_artMap._constellationActive) {
                    _artMap._constellationActive = false;
                    _artMapAnimateConstellation(); // fade out
                }
                if (_artMap.hoveredNode) {
                    // Snappy sustained-hover delay before the constellation lights up
                    // (was 800ms, which felt like nothing happened).
                    _artMap._constellationTimer = setTimeout(() => {
                        if (_artMap.hoveredNode) {
                            _artMap._constellationActive = true;
                            _artMap._constellationFade = 0;
                            _artMap._constellationCache = null;
                            _artMapAnimateConstellation();
                        }
                    }, 220);
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
            // A click is a deliberate select — ripple it and pin its card in the
            // side panel immediately (bypassing the hover debounce). The card's
            // Details button opens the full modal; click no longer auto-opens it.
            const { nx, ny } = _artMapScreenToWorld(e, canvas);
            const node = _artMapHitTest(nx, ny);
            _artMapEmitRipple(node ? node.x : nx, node ? node.y : ny, node ? node._hue : null);
            if (node) {
                clearTimeout(_artMap._panelTimer);
                _artMapPanelArtist(node);
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
            _artMapEmitRipple(node ? node.x : wx, node ? node.y : wy, node ? node._hue : null);
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
    // Single O(N) pass, no per-move sort, no allocation. Watchlist nodes draw on
    // top so they win ties; otherwise the first node whose circle contains the
    // point wins. (A spatial grid was tried and reverted — it exploded building
    // cells for large-radius genre cluster nodes. A flat scan of even thousands
    // of nodes is sub-millisecond and can't lock up.)
    let similarHit = null;
    for (const n of _artMap.placed) {
        if ((n.opacity || 0) < 0.3) continue;
        const dx = wx - n.x, dy = wy - n.y;
        if (dx * dx + dy * dy <= n.radius * n.radius) {
            if (n.type === 'watchlist') return n;
            if (!similarHit) similarHit = n;
        }
    }
    return similarHit;
}

async function openYourArtistInfoModal_direct(node) {
    // Determine best source ID — prefer active metadata source
    let bestId = '', bestSource = '';
    // Check what the active source is
    const activeSource = window._yaActiveSource || 'spotify';
    const sourceOrder = activeSource === 'spotify' ? ['spotify_id', 'itunes_id', 'deezer_id', 'discogs_id', 'musicbrainz_id']
        : activeSource === 'itunes' ? ['itunes_id', 'spotify_id', 'deezer_id', 'discogs_id', 'musicbrainz_id']
            : activeSource === 'deezer' ? ['deezer_id', 'spotify_id', 'itunes_id', 'discogs_id', 'musicbrainz_id']
                : activeSource === 'musicbrainz' ? ['musicbrainz_id', 'spotify_id', 'itunes_id', 'deezer_id', 'discogs_id']
                    : ['spotify_id', 'itunes_id', 'deezer_id', 'discogs_id', 'musicbrainz_id'];
    const sourceMap = { spotify_id: 'spotify', itunes_id: 'itunes', deezer_id: 'deezer', discogs_id: 'discogs', musicbrainz_id: 'musicbrainz' };
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

// ===============================
// BECAUSE YOU LISTEN TO
// ===============================

function _renderByltSection(section, idx) {
    return `
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
    `;
}

function _renderByltTrackCard(t) {
    return `
        <div class="discover-card">
            <div class="discover-card-image">
                ${t.image_url ? `<img src="${t.image_url}" alt="" loading="lazy" onerror="this.src='/static/placeholder-album.png'">` : '<div class="discover-card-placeholder">🎵</div>'}
            </div>
            <div class="discover-card-title">${_esc(t.name)}</div>
            <div class="discover-card-artist">${_esc(t.artist)}</div>
        </div>
    `;
}

let _byltCtrl = null;

async function loadBecauseYouListenTo() {
    // Ensure the BYLT container exists in the DOM. It's dynamically
    // inserted after the release radar section because the markup
    // doesn't ship a placeholder for it. Bail if anchor section
    // isn't present.
    let byltContainer = document.getElementById('discover-bylt-sections');
    if (!byltContainer) {
        const releaseRadar = document.getElementById('discover-release-radar');
        if (!releaseRadar) return;
        const parent = releaseRadar.closest('.discover-section');
        if (!parent) return;

        byltContainer = document.createElement('div');
        byltContainer.id = 'discover-bylt-sections';
        parent.parentNode.insertBefore(byltContainer, parent.nextSibling);
    }

    if (!_byltCtrl) {
        _byltCtrl = createDiscoverSectionController({
            id: 'because-you-listen-to',
            contentEl: '#discover-bylt-sections',
            fetchUrl: '/api/discover/because-you-listen-to',
            extractItems: (data) => data.sections || [],
            // No per-section empty/loading copy — when there's nothing
            // to show we leave the container blank rather than render a
            // placeholder, matching the original no-op behavior.
            renderEmptyState: false,
            loadingMessage: '',
            renderItems: (items) => items.map((s, i) => _renderByltSection(s, i)).join(''),
            onRendered: ({ items }) => {
                // Inject track cards into each section's carousel after
                // the section wrappers exist in the DOM.
                items.forEach((section, idx) => {
                    const carousel = document.getElementById(`bylt-carousel-${idx}`);
                    if (!carousel) return;
                    carousel.innerHTML = section.tracks.map(t => _renderByltTrackCard(t)).join('');
                });
            },
        });
    }
    return _byltCtrl.load();
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
            const artistContext = _buildDiscoverArtistContext(resolvedSource, artistName, item, albumData);
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

        const artistContext = _buildDiscoverArtistContext(source, item.artist_name || albumData.artists?.[0]?.name || '', item, albumData);
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
                const detailHref = a.entity_id ? buildArtistDetailPath(a.entity_id, a.source || null) : '#';
                const srcClass = (a.source || '').toLowerCase();
                return `<a class="genre-dive-artist" href="${detailHref}" onclick="document.getElementById('genre-deep-dive-modal').remove()" style="text-decoration:none;color:inherit;">
                            <div class="genre-dive-artist-img" style="${a.image_url ? `background-image:url('${_esc(a.image_url)}')` : ''}">
                                ${!a.image_url ? '<span>🎤</span>' : ''}
                            </div>
                            <span class="genre-dive-src-dot genre-dive-src-${srcClass}"></span>
                            <div class="genre-dive-artist-name">${_esc(a.name)}</div>
                            ${a.followers ? `<div class="genre-dive-artist-meta">${_fmtNum(a.followers)} followers</div>` : ''}
                            ${a.library_id ? '<div class="genre-dive-artist-badge">In Library</div>' : ''}
                        </a>`;
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
    console.log(`🔄 Starting sync for ${playlistName}`);

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
    } else if (playlistType === 'build_playlist') {
        tracks = buildPlaylistTracks;
    }

    if (!tracks || tracks.length === 0) {
        showToast(`No tracks available for ${playlistName}`, 'warning');
        return;
    }

    // Convert to format expected by sync API
    const spotifyTracks = tracks.map(track => {
        let spotifyTrack;

        // Use track_data_json if available
        if (track.track_data_json) {
            spotifyTrack = track.track_data_json;
        } else {
            // Fallback: construct track object
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

        // Normalize artists to array of strings for sync compatibility
        if (spotifyTrack.artists && Array.isArray(spotifyTrack.artists)) {
            spotifyTrack.artists = spotifyTrack.artists.map(a => a.name || a);
        }

        return spotifyTrack;
    });

    // Create virtual playlist ID
    const virtualPlaylistId = `discover_${playlistType}`;

    // Store in cache for sync function
    playlistTrackCache[virtualPlaylistId] = spotifyTracks;

    // Create virtual playlist object
    const virtualPlaylist = {
        id: virtualPlaylistId,
        name: playlistName,
        track_count: spotifyTracks.length
    };

    // Add to spotify playlists array if not already there
    if (!spotifyPlaylists.find(p => p.id === virtualPlaylistId)) {
        spotifyPlaylists.push(virtualPlaylist);
    }

    // Show sync status display (convert underscores to hyphens for ID)
    const statusId = playlistType.replace(/_/g, '-') + '-sync-status';
    const statusDisplay = document.getElementById(statusId);
    if (statusDisplay) {
        statusDisplay.style.display = 'block';
    }

    // Disable sync button to prevent duplicate syncs (convert underscores to hyphens for ID)
    const buttonId = playlistType.replace(/_/g, '-') + '-sync-btn';
    const syncButton = document.getElementById(buttonId);
    if (syncButton) {
        syncButton.disabled = true;
        syncButton.style.opacity = '0.5';
        syncButton.style.cursor = 'not-allowed';
    }

    // Start sync using existing function
    await startPlaylistSync(virtualPlaylistId);

    // Extract image URL from first track for download bar bubble
    let imageUrl = null;
    if (spotifyTracks && spotifyTracks.length > 0) {
        const firstTrack = spotifyTracks[0];
        if (firstTrack.album && firstTrack.album.images && firstTrack.album.images.length > 0) {
            imageUrl = firstTrack.album.images[0].url;
        }
    }

    // Add to discover download bar
    addDiscoverDownload(virtualPlaylistId, playlistName, playlistType, imageUrl);

    // Start polling for progress updates
    startDiscoverSyncPolling(playlistType, virtualPlaylistId);
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
                    'build_playlist': 'Custom Playlist'
                };
                showToast(`${playlistNames[playlistType] || playlistType} sync complete!`, 'success');
                setTimeout(() => { const sd = el(`${prefix}-sync-status`); if (sd) sd.style.display = 'none'; }, 3000);
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
                    'build_playlist': 'Custom Playlist'
                };
                const displayName = playlistNames[playlistType] || playlistType;
                showToast(`${displayName} sync complete!`, 'success');

                // Hide status display after 3 seconds
                setTimeout(() => {
                    const statusDisplay = document.getElementById(`${prefix}-sync-status`);
                    if (statusDisplay) {
                        statusDisplay.style.display = 'none';
                    }
                }, 3000);
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
        const artistContext = _buildDiscoverArtistContext(source, album.artist_name, album, albumData);

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
