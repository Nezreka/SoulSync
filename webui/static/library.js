// LIBRARY PAGE FUNCTIONALITY
// ===============================

// Library page state
const libraryPageState = {
    isInitialized: false,
    currentSearch: "",
    currentLetter: "all",
    currentPage: 1,
    limit: 75,
    debounceTimer: null,
    watchlistFilter: "all",
    sourceFilter: ""
};

function initializeLibraryPage() {
    console.log("🔧 Initializing Library page...");

    try {
        // Initialize search functionality
        initializeLibrarySearch();

        // Initialize watchlist filter
        initializeWatchlistFilter();

        // Initialize metadata source filter
        initializeSourceFilter();

        // Initialize alphabet selector
        initializeAlphabetSelector();

        // Initialize pagination
        initializeLibraryPagination();

        // Load initial data
        loadLibraryArtists();

        // Show download bubbles if any exist
        showLibraryDownloadsSection();

        libraryPageState.isInitialized = true;
        console.log("✅ Library page initialized successfully");

    } catch (error) {
        console.error("❌ Error initializing Library page:", error);
        showToast("Failed to initialize Library page", "error");
    }
}

function initializeLibrarySearch() {
    const searchInput = document.getElementById("library-search-input");
    if (!searchInput) return;

    searchInput.addEventListener("input", (e) => {
        const query = e.target.value.trim();

        // Clear existing debounce timer
        if (libraryPageState.debounceTimer) {
            clearTimeout(libraryPageState.debounceTimer);
        }

        // Debounce search requests
        libraryPageState.debounceTimer = setTimeout(() => {
            libraryPageState.currentSearch = query;
            libraryPageState.currentPage = 1; // Reset to first page
            loadLibraryArtists();
        }, 300);
    });

    // Clear search on Escape key
    searchInput.addEventListener("keydown", (e) => {
        if (e.key === "Escape") {
            searchInput.value = "";
            libraryPageState.currentSearch = "";
            libraryPageState.currentPage = 1;
            loadLibraryArtists();
        }
    });
}

function initializeWatchlistFilter() {
    const filterButtons = document.querySelectorAll(".watchlist-filter-btn");
    const watchAllBtn = document.getElementById("library-watchlist-all-btn");

    filterButtons.forEach(button => {
        button.addEventListener("click", () => {
            const filter = button.getAttribute("data-filter");

            // Update active state
            filterButtons.forEach(btn => btn.classList.remove("active"));
            button.classList.add("active");

            // Show/hide "Watch All Unwatched" button
            if (watchAllBtn) {
                if (filter === "unwatched") {
                    watchAllBtn.classList.remove("hidden");
                } else {
                    watchAllBtn.classList.add("hidden");
                }
            }

            // Update state and reload
            libraryPageState.watchlistFilter = filter;
            libraryPageState.currentPage = 1;
            loadLibraryArtists();
        });
    });
}

function initializeSourceFilter() {
    const select = document.getElementById('library-source-filter');
    if (!select) return;
    select.addEventListener('change', () => {
        libraryPageState.sourceFilter = select.value;
        libraryPageState.currentPage = 1;
        loadLibraryArtists();
    });
}

function initializeAlphabetSelector() {
    const alphabetButtons = document.querySelectorAll(".alphabet-btn");

    alphabetButtons.forEach(button => {
        button.addEventListener("click", () => {
            const letter = button.getAttribute("data-letter");

            // Update active state
            alphabetButtons.forEach(btn => btn.classList.remove("active"));
            button.classList.add("active");

            // Update state and load data
            libraryPageState.currentLetter = letter;
            libraryPageState.currentPage = 1; // Reset to first page
            loadLibraryArtists();
        });
    });
}

function initializeLibraryPagination() {
    const prevBtn = document.getElementById("prev-page-btn");
    const nextBtn = document.getElementById("next-page-btn");

    if (prevBtn) {
        prevBtn.addEventListener("click", () => {
            if (libraryPageState.currentPage > 1) {
                libraryPageState.currentPage--;
                loadLibraryArtists();
            }
        });
    }

    if (nextBtn) {
        nextBtn.addEventListener("click", () => {
            libraryPageState.currentPage++;
            loadLibraryArtists();
        });
    }
}

async function loadLibraryArtists() {
    try {
        // Show loading state
        showLibraryLoading(true);

        // Build query parameters
        const params = new URLSearchParams({
            search: libraryPageState.currentSearch,
            letter: libraryPageState.currentLetter,
            page: libraryPageState.currentPage,
            limit: libraryPageState.limit,
            watchlist: libraryPageState.watchlistFilter
        });
        if (libraryPageState.sourceFilter) params.set('source_filter', libraryPageState.sourceFilter);

        // Fetch artists from API
        const response = await fetch(`/api/library/artists?${params}`);
        const data = await response.json();

        if (!data.success) {
            throw new Error(data.error || "Failed to load artists");
        }

        // Update UI with artists
        displayLibraryArtists(data.artists);
        updateLibraryPagination(data.pagination);
        updateLibraryStats(data.pagination.total_count);

        // Hide loading state
        showLibraryLoading(false);

        // Show empty state if no artists
        if (data.artists.length === 0) {
            showLibraryEmpty(true);
        } else {
            showLibraryEmpty(false);
        }

    } catch (error) {
        console.error("❌ Error loading library artists:", error);
        showToast("Failed to load artists", "error");
        showLibraryLoading(false);
        showLibraryEmpty(true);
    }
}

function displayLibraryArtists(artists) {
    const grid = document.getElementById("library-artists-grid");
    if (!grid) return;

    // Build all cards as HTML string for single DOM write (much faster than createElement loop)
    grid.innerHTML = artists.map((artist, i) => {
        try { return buildLibraryArtistCardHTML(artist, i); }
        catch (e) { console.error('Failed to render artist card:', artist.name, e); return ''; }
    }).join('');

    // Attach click handlers via event delegation (single listener vs 75+ individual)
    grid.onclick = (e) => {
        // Ignore clicks on badge icons (they open external links / toggle watchlist)
        const badge = e.target.closest('.source-card-icon');
        if (badge) {
            e.stopPropagation();
            const url = badge.dataset.url;
            if (url) { window.open(url, '_blank'); return; }
            // Watchlist toggle
            if (badge.classList.contains('watch-card-icon') && badge.dataset.unwatched) {
                const card = badge.closest('.library-artist-card');
                if (card) {
                    const artistId = card.dataset.artistId;
                    const artistName = card.dataset.artistName;
                    const artist = artists.find(a => String(a.id) === artistId);
                    if (artist) toggleLibraryCardWatchlist(badge, artist);
                }
            }
            return;
        }
        const card = e.target.closest('.library-artist-card');
        if (card) {
            navigateToArtistDetail(card.dataset.artistId, card.dataset.artistName);
        }
    };
}

function buildLibraryArtistCardHTML(artist, index) {
    const _esc = (s) => (s || '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;').replace(/'/g, '&#39;');
    const delay = Math.min(index * 20, 600); // Cap at 600ms so last cards don't wait too long

    // Build badge icons
    const badges = [];
    if (artist.spotify_artist_id) badges.push({ logo: SPOTIFY_LOGO_URL, fb: 'SP', title: 'Spotify', url: `https://open.spotify.com/artist/${artist.spotify_artist_id}` });
    if (artist.musicbrainz_id) badges.push({ logo: MUSICBRAINZ_LOGO_URL, fb: 'MB', title: 'MusicBrainz', url: `https://musicbrainz.org/artist/${artist.musicbrainz_id}` });
    if (artist.deezer_id) badges.push({ logo: DEEZER_LOGO_URL, fb: 'Dz', title: 'Deezer', url: `https://www.deezer.com/artist/${artist.deezer_id}` });
    if (artist.audiodb_id) {
        const slug = artist.name ? artist.name.replace(/\s+/g, '-').replace(/[^a-zA-Z0-9-]/g, '') : '';
        badges.push({ logo: typeof getAudioDBLogoURL === 'function' ? getAudioDBLogoURL() : '', fb: 'ADB', title: 'AudioDB', url: `https://www.theaudiodb.com/artist/${artist.audiodb_id}-${slug}` });
    }
    if (artist.itunes_artist_id) badges.push({ logo: ITUNES_LOGO_URL, fb: 'IT', title: 'Apple Music', url: `https://music.apple.com/artist/${artist.itunes_artist_id}` });
    if (artist.lastfm_url) badges.push({ logo: LASTFM_LOGO_URL, fb: 'LFM', title: 'Last.fm', url: artist.lastfm_url });
    if (artist.genius_url) badges.push({ logo: GENIUS_LOGO_URL, fb: 'GEN', title: 'Genius', url: artist.genius_url });
    if (artist.tidal_id) badges.push({ logo: TIDAL_LOGO_URL, fb: 'TD', title: 'Tidal', url: `https://tidal.com/browse/artist/${artist.tidal_id}` });
    if (artist.qobuz_id) badges.push({ logo: QOBUZ_LOGO_URL, fb: 'Qz', title: 'Qobuz', url: `https://www.qobuz.com/artist/${artist.qobuz_id}` });
    if (artist.discogs_id) badges.push({ logo: DISCOGS_LOGO_URL, fb: 'DC', title: 'Discogs', url: `https://www.discogs.com/artist/${artist.discogs_id}` });
    if (artist.soul_id && !String(artist.soul_id).startsWith('soul_unnamed_')) badges.push({ logo: '/static/trans2.png', fb: 'SS', title: `SoulID: ${artist.soul_id}`, url: null });

    // Watchlist badge
    const hasActiveSourceId = currentMusicSourceName === 'iTunes'
        ? (artist.itunes_artist_id || artist.spotify_artist_id)
        : (artist.spotify_artist_id || artist.itunes_artist_id);
    let watchBadgeHTML = '';
    if (artist.is_watched) {
        watchBadgeHTML = `<div class="watch-card-icon watched source-card-icon" title="On your watchlist"><span class="watch-icon-emoji">👁️</span><span class="watch-icon-label">Watching</span></div>`;
    } else if (hasActiveSourceId) {
        watchBadgeHTML = `<div class="watch-card-icon source-card-icon" data-unwatched="1" title="Add to Watchlist" style="opacity:0.4"><span class="watch-icon-emoji">👁️</span><span class="watch-icon-label">Watch</span></div>`;
    }

    const maxPerColumn = 6;
    const needsOverflow = badges.length > maxPerColumn;
    const badgeIcon = (b) => `<div class="source-card-icon" title="${_esc(b.title)}" ${b.url ? `data-url="${_esc(b.url)}"` : ''}>${b.logo ? `<img src="${_esc(b.logo)}" style="width:16px;height:auto;display:block" onerror="this.parentNode.textContent='${b.fb}'">` : `<span style="font-size:9px;font-weight:700">${b.fb}</span>`}</div>`;

    let badgeContainerHTML = '';
    if (badges.length > 0 || watchBadgeHTML) {
        if (needsOverflow) {
            badgeContainerHTML = `<div class="card-badge-container">
                <div class="badge-overflow-column">${watchBadgeHTML}${badges.slice(maxPerColumn).map(badgeIcon).join('')}</div>
                <div class="badge-primary-column">${badges.slice(0, maxPerColumn).map(badgeIcon).join('')}</div>
            </div>`;
        } else {
            badgeContainerHTML = `<div class="card-badge-container">${badges.map(badgeIcon).join('')}${watchBadgeHTML}</div>`;
        }
    }

    // Image
    const hasImage = artist.image_url && artist.image_url.trim() !== '';
    const deezerFallback = artist.deezer_id ? `if(!this.dataset.triedDeezer){this.dataset.triedDeezer='true';this.src='https://api.deezer.com/artist/${artist.deezer_id}/image?size=big'}else{this.parentNode.innerHTML='<div class=\\'library-artist-image-fallback\\'>🎵</div>'}` : `this.parentNode.innerHTML='<div class=\\'library-artist-image-fallback\\'>🎵</div>'`;
    const imageHTML = hasImage
        ? `<div class="library-artist-image"><img src="${_esc(artist.image_url)}" alt="${_esc(artist.name)}" loading="lazy" onerror="${deezerFallback}"></div>`
        : `<div class="library-artist-image"><div class="library-artist-image-fallback">🎵</div></div>`;

    // Track stats
    const trackStat = artist.track_count > 0 ? `<span class="library-artist-stat">${artist.track_count} track${artist.track_count !== 1 ? 's' : ''}</span>` : '';

    return `<div class="library-artist-card" data-artist-id="${_esc(String(artist.id))}" data-artist-name="${_esc(artist.name)}" style="position:relative;animation:cardFadeIn 0.35s cubic-bezier(0.4,0,0.2,1) ${delay}ms both">
        ${badgeContainerHTML}
        ${imageHTML}
        <div class="library-artist-info">
            <h3 class="library-artist-name" title="${_esc(artist.name)}">${_esc(artist.name)}</h3>
            <div class="library-artist-stats">${trackStat}</div>
        </div>
    </div>`;
}

function updateLibraryPagination(pagination) {
    const prevBtn = document.getElementById("prev-page-btn");
    const nextBtn = document.getElementById("next-page-btn");
    const pageInfo = document.getElementById("page-info");
    const paginationContainer = document.getElementById("library-pagination");

    if (!paginationContainer) return;

    // Update button states
    if (prevBtn) {
        prevBtn.disabled = !pagination.has_prev;
    }

    if (nextBtn) {
        nextBtn.disabled = !pagination.has_next;
    }

    // Update page info
    if (pageInfo) {
        pageInfo.textContent = `Page ${pagination.page} of ${pagination.total_pages}`;
    }

    // Show/hide pagination based on total pages
    if (pagination.total_pages > 1) {
        paginationContainer.classList.remove("hidden");
    } else {
        paginationContainer.classList.add("hidden");
    }
}

function updateLibraryStats(totalCount) {
    const countElement = document.getElementById("library-artist-count");
    if (countElement) {
        countElement.textContent = totalCount;
    }
}

function showLibraryLoading(show) {
    const loadingElement = document.getElementById("library-loading");
    if (loadingElement) {
        if (show) {
            loadingElement.classList.remove("hidden");
        } else {
            loadingElement.classList.add("hidden");
        }
    }
}

function showLibraryEmpty(show) {
    const emptyElement = document.getElementById("library-empty");
    if (emptyElement) {
        if (show) {
            emptyElement.classList.remove("hidden");
        } else {
            emptyElement.classList.add("hidden");
        }
    }
}

async function openWatchAllUnwatchedModal() {
    if (document.getElementById('watch-all-modal-overlay')) return;

    const sourceIdField = currentMusicSourceName === 'iTunes' ? 'itunes_artist_id'
        : currentMusicSourceName === 'Deezer' ? 'deezer_id' : 'spotify_artist_id';
    const sourceName = currentMusicSourceName || 'Spotify';

    const overlay = document.createElement('div');
    overlay.id = 'watch-all-modal-overlay';
    overlay.className = 'modal-overlay';
    overlay.onclick = (e) => { if (e.target === overlay) closeWatchAllUnwatchedModal(); };

    overlay.innerHTML = `
        <div class="watch-all-modal">
            <div class="watch-all-header">
                <div class="watch-all-header-content">
                    <div class="watch-all-header-icon">&#128065;</div>
                    <div>
                        <h2 class="watch-all-title">Watch All Unwatched</h2>
                        <p class="watch-all-subtitle">Add unwatched artists with ${_esc(sourceName)} IDs to your watchlist</p>
                    </div>
                </div>
                <button class="watch-all-close" onclick="closeWatchAllUnwatchedModal()">&times;</button>
            </div>
            <div class="watch-all-body">
                <div class="watch-all-loading-state">
                    <div class="watch-all-loading-spinner"></div>
                    <div class="watch-all-loading-text">Loading unwatched artists...</div>
                    <div class="watch-all-loading-count" id="watch-all-load-count"></div>
                </div>
            </div>
            <div class="watch-all-footer">
                <button class="watch-all-btn watch-all-btn-cancel" onclick="closeWatchAllUnwatchedModal()">Cancel</button>
                <button class="watch-all-btn watch-all-btn-primary" id="watch-all-confirm-btn" disabled>Watch All</button>
            </div>
        </div>
    `;
    document.body.appendChild(overlay);

    // Fetch all unwatched artists paginated (SQLite variable limit safe)
    try {
        const eligible = [];
        const ineligible = [];
        let page = 1;
        const pageSize = 400;
        const countEl = document.getElementById('watch-all-load-count');

        while (true) {
            if (!document.getElementById('watch-all-modal-overlay')) return;
            if (countEl) countEl.textContent = `${eligible.length + ineligible.length} artists loaded...`;

            const params = new URLSearchParams({ search: '', letter: 'all', page, limit: pageSize, watchlist: 'unwatched' });
            const response = await fetch(`/api/library/artists?${params}`);
            const data = await response.json();
            if (!data.success) throw new Error(data.error || 'Failed to load artists');

            for (const a of (data.artists || [])) {
                if (a[sourceIdField]) eligible.push(a);
                else ineligible.push(a);
            }

            if (!data.pagination.has_next) break;
            page++;
        }

        _renderWatchAllModalContent(overlay, eligible, ineligible, sourceName);
    } catch (error) {
        console.error('Error loading unwatched artists:', error);
        const body = overlay.querySelector('.watch-all-body');
        if (body) body.innerHTML = `<div class="watch-all-empty-state"><div class="watch-all-empty-icon">&#9888;</div><div>Failed to load artists</div><a href="#" onclick="closeWatchAllUnwatchedModal(); openWatchAllUnwatchedModal(); return false;" class="watch-all-retry-link">Retry</a></div>`;
    }
}

function _renderWatchAllModalContent(overlay, eligible, ineligible, sourceName) {
    const body = overlay.querySelector('.watch-all-body');
    const confirmBtn = overlay.querySelector('#watch-all-confirm-btn');

    if (eligible.length === 0 && ineligible.length === 0) {
        body.innerHTML = '<div class="watch-all-empty-state"><div class="watch-all-empty-icon">&#127925;</div><div>No unwatched artists found</div></div>';
        return;
    }

    // Store data for search filtering
    overlay._watchAllEligible = eligible;
    overlay._watchAllIneligible = ineligible;

    let html = '';

    // Summary bar (sticky)
    html += '<div class="watch-all-stats">';
    html += `<div class="watch-all-stat-card eligible"><div class="watch-all-stat-value">${eligible.length}</div><div class="watch-all-stat-label">Ready to watch</div></div>`;
    html += `<div class="watch-all-stat-card ineligible"><div class="watch-all-stat-value">${ineligible.length}</div><div class="watch-all-stat-label">No ${_esc(sourceName)} ID</div></div>`;
    html += `<div class="watch-all-stat-card total"><div class="watch-all-stat-value">${eligible.length + ineligible.length}</div><div class="watch-all-stat-label">Total unwatched</div></div>`;
    html += '</div>';

    // Search filter
    if (eligible.length > 10) {
        html += '<div class="watch-all-search-wrap"><input type="text" class="watch-all-search" id="watch-all-search" placeholder="Search artists..." oninput="_filterWatchAllList(this.value)"></div>';
    }

    // Eligible grid
    if (eligible.length > 0) {
        html += '<div class="watch-all-section-label">Artists to be watched</div>';
        html += '<div class="watch-all-grid" id="watch-all-eligible-grid">';
        html += _buildWatchAllRows(eligible, false);
        html += '</div>';
    }

    // Ineligible section
    if (ineligible.length > 0) {
        html += `<div class="watch-all-ineligible">
            <div class="watch-all-ineligible-header" onclick="this.parentElement.classList.toggle('expanded')">
                <div class="watch-all-ineligible-label">
                    <span class="watch-all-ineligible-icon">&#9888;</span>
                    <span>${ineligible.length} artist${ineligible.length !== 1 ? 's' : ''} without ${_esc(sourceName)} ID</span>
                </div>
                <span class="watch-all-chevron">&#9660;</span>
            </div>
            <div class="watch-all-ineligible-body">
                <div class="watch-all-ineligible-hint">These artists haven't been matched to ${_esc(sourceName)} yet. The background enrichment worker will match them over time.</div>
                <div class="watch-all-grid" id="watch-all-ineligible-grid">${_buildWatchAllRows(ineligible, true)}</div>
            </div>
        </div>`;
    }

    if (eligible.length === 0) {
        html += `<div class="watch-all-empty-state"><div class="watch-all-empty-icon">&#128268;</div><div>None of your unwatched artists have a ${_esc(sourceName)} ID yet</div><div class="watch-all-empty-hint">The background enrichment worker will match them over time.</div></div>`;
    }

    body.innerHTML = html;

    if (eligible.length > 0 && confirmBtn) {
        confirmBtn.textContent = `Watch All (${eligible.length})`;
        confirmBtn.disabled = false;
        confirmBtn.onclick = () => _confirmWatchAllUnwatched(overlay, eligible.length);
    }
}

function _buildWatchAllRows(artists, dimmed) {
    let html = '';
    for (const a of artists) {
        const img = a.image_url
            ? `<img src="${_esc(a.image_url)}" alt="" onerror="this.style.display='none'; this.nextElementSibling.style.display='flex'" loading="lazy"><div class="watch-all-cell-placeholder" style="display:none">&#127925;</div>`
            : `<div class="watch-all-cell-placeholder">&#127925;</div>`;
        html += `<div class="watch-all-cell${dimmed ? ' dimmed' : ''}" data-name="${_esc(a.name.toLowerCase())}">
            <div class="watch-all-cell-img">${img}</div>
            <div class="watch-all-cell-name" title="${_esc(a.name)}">${_esc(a.name)}</div>
            <div class="watch-all-cell-meta">${a.track_count || 0} tracks</div>
        </div>`;
    }
    return html;
}

function _filterWatchAllList(query) {
    const q = query.toLowerCase().trim();
    document.querySelectorAll('#watch-all-eligible-grid .watch-all-cell').forEach(cell => {
        cell.style.display = !q || cell.dataset.name.includes(q) ? '' : 'none';
    });
}

async function _confirmWatchAllUnwatched(overlay, expectedCount) {
    const confirmBtn = overlay.querySelector('#watch-all-confirm-btn');
    const cancelBtn = overlay.querySelector('.watch-all-btn-cancel');
    if (confirmBtn) { confirmBtn.disabled = true; confirmBtn.textContent = 'Adding...'; }
    if (cancelBtn) cancelBtn.disabled = true;

    try {
        const response = await fetch('/api/library/watchlist-all-unwatched', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' }
        });
        const data = await response.json();

        if (data.success) {
            const body = overlay.querySelector('.watch-all-body');
            body.innerHTML = `<div class="watch-all-results">
                <div class="watch-all-results-icon">&#10003;</div>
                <div class="watch-all-results-title">Added ${data.added} artist${data.added !== 1 ? 's' : ''} to watchlist</div>
                ${data.skipped_already > 0 ? `<div class="watch-all-results-detail">${data.skipped_already} already watched</div>` : ''}
                ${data.skipped_no_id > 0 ? `<div class="watch-all-results-detail">${data.skipped_no_id} skipped (no external ID)</div>` : ''}
            </div>`;

            if (confirmBtn) confirmBtn.style.display = 'none';
            if (cancelBtn) { cancelBtn.disabled = false; cancelBtn.textContent = 'Close'; }
            overlay.dataset.needsRefresh = 'true';
        } else {
            throw new Error(data.error || 'Failed to add artists');
        }
    } catch (error) {
        console.error('Error in watch all:', error);
        if (confirmBtn) { confirmBtn.disabled = false; confirmBtn.textContent = `Watch All (${expectedCount})`; }
        if (cancelBtn) cancelBtn.disabled = false;
        showToast('Failed to add artists to watchlist', 'error');
    }
}

function closeWatchAllUnwatchedModal() {
    const overlay = document.getElementById('watch-all-modal-overlay');
    if (!overlay) return;
    const needsRefresh = overlay.dataset.needsRefresh === 'true';
    overlay.remove();
    if (needsRefresh) loadLibraryArtists();
}

async function toggleLibraryCardWatchlist(btn, artist) {
    if (btn.disabled) return;
    btn.disabled = true;

    // Support both badge-style (.watch-icon-label) and button-style (.watchlist-text)
    const label = btn.querySelector('.watch-icon-label') || btn.querySelector('.watchlist-text');
    const isWatching = btn.classList.contains('watched') || btn.classList.contains('watching');

    if (label) label.textContent = '...';

    try {
        // Use the ID matching the active metadata source
        const artistId = currentMusicSourceName === 'iTunes'
            ? (artist.itunes_artist_id || artist.spotify_artist_id)
            : (artist.spotify_artist_id || artist.itunes_artist_id);
        if (!artistId) throw new Error('No iTunes or Spotify ID available for this artist');

        if (isWatching) {
            const response = await fetch('/api/watchlist/remove', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ artist_id: artistId })
            });
            const data = await response.json();
            if (!data.success) throw new Error(data.error);

            btn.classList.remove('watched', 'watching');
            btn.style.opacity = '0.4';
            btn.title = 'Add to Watchlist';
            if (label) label.textContent = 'Watch';
            showToast(`Removed ${artist.name} from watchlist`, 'success');
        } else {
            const response = await fetch('/api/watchlist/add', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ artist_id: artistId, artist_name: artist.name })
            });
            const data = await response.json();
            if (!data.success) throw new Error(data.error);

            btn.classList.add('watched');
            btn.style.opacity = '';
            btn.title = 'Remove from Watchlist';
            if (label) label.textContent = 'Watching';
            showToast(`Added ${artist.name} to watchlist`, 'success');
        }

        if (typeof updateWatchlistCount === 'function') {
            updateWatchlistCount();
        }
    } catch (error) {
        console.error('Error toggling library card watchlist:', error);
        if (label) label.textContent = isWatching ? 'Watching' : 'Watch';
        showToast(`Error: ${error.message}`, 'error');
    } finally {
        btn.disabled = false;
    }
}

// ===============================================
// Artist Detail Page Functions
// ===============================================

// Artist detail page state
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

// Discography filter state
let discographyFilterState = {
    categories: { albums: true, eps: true, singles: true },
    content: { live: true, compilations: true, featured: true },
    ownership: 'all'  // 'all', 'owned', 'missing'
};

function navigateToArtistDetail(artistId, artistName, sourceOverride = null) {
    console.log(`🎵 Navigating to artist detail: ${artistName} (ID: ${artistId}${sourceOverride ? `, source: ${sourceOverride}` : ''})`);

    // Abort any in-progress completion stream
    if (artistDetailPageState.completionController) {
        artistDetailPageState.completionController.abort();
        artistDetailPageState.completionController = null;
    }

    // Cancel any active inline edit and close manual match modal before resetting state
    cancelInlineEdit();
    const existingMatchOverlay = document.getElementById('enhanced-manual-match-overlay');
    if (existingMatchOverlay) existingMatchOverlay.remove();

    // Store current artist info and reset enhanced view state
    artistDetailPageState.currentArtistId = artistId;
    artistDetailPageState.currentArtistName = artistName;
    artistDetailPageState.currentArtistSource = sourceOverride || null;
    artistDetailPageState.enhancedData = null;
    artistDetailPageState.expandedAlbums = new Set();
    artistDetailPageState.selectedTracks = new Set();
    artistDetailPageState.enhancedTrackSort = {};
    artistDetailPageState.enhancedView = false;

    // Reset enhanced view toggle to standard
    const toggleBtns = document.querySelectorAll('.enhanced-view-toggle-btn');
    toggleBtns.forEach(btn => {
        btn.classList.toggle('active', btn.getAttribute('data-view') === 'standard');
    });
    const enhancedContainer = document.getElementById('enhanced-view-container');
    if (enhancedContainer) enhancedContainer.classList.add('hidden');
    const standardSections = document.querySelector('.discography-sections');
    if (standardSections) standardSections.classList.remove('hidden');
    // Restore standard view filter groups
    const filterGroups = document.querySelectorAll('#discography-filters .filter-group');
    filterGroups.forEach(group => {
        const label = group.querySelector('.filter-label');
        if (label && label.textContent !== 'View') group.style.display = '';
    });
    const dividers = document.querySelectorAll('#discography-filters .filter-divider');
    dividers.forEach(d => d.style.display = '');
    // Hide bulk bar
    const bulkBar = document.getElementById('enhanced-bulk-bar');
    if (bulkBar) bulkBar.classList.remove('visible');

    // Navigate to artist detail page
    navigateToPage('artist-detail');

    // Initialize if needed and load data
    if (!artistDetailPageState.isInitialized) {
        initializeArtistDetailPage();
    }

    // Load artist data
    loadArtistDetailData(artistId, artistName);
}

function initializeArtistDetailPage() {
    console.log("🔧 Initializing Artist Detail page...");

    // Initialize back button
    const backBtn = document.getElementById("artist-detail-back-btn");
    if (backBtn) {
        backBtn.addEventListener("click", () => {
            console.log("🔙 Returning to Library page");
            // Abort any in-progress completion stream
            if (artistDetailPageState.completionController) {
                artistDetailPageState.completionController.abort();
                artistDetailPageState.completionController = null;
            }
            // Clear artist detail state so we go back to the list view
            artistDetailPageState.currentArtistId = null;
            artistDetailPageState.currentArtistName = null;
            navigateToPage('library');
        });
    }

    // Initialize retry button
    const retryBtn = document.getElementById("artist-detail-retry-btn");
    if (retryBtn) {
        retryBtn.addEventListener("click", () => {
            if (artistDetailPageState.currentArtistId && artistDetailPageState.currentArtistName) {
                loadArtistDetailData(artistDetailPageState.currentArtistId, artistDetailPageState.currentArtistName);
            }
        });
    }

    // Initialize discography filter buttons
    initializeDiscographyFilters();

    artistDetailPageState.isInitialized = true;
    console.log("✅ Artist Detail page initialized successfully");
}

async function loadArtistDetailData(artistId, artistName) {
    console.log(`🔄 Loading artist detail data for: ${artistName} (ID: ${artistId})`);

    // Reset discography filters to defaults
    resetDiscographyFilters();

    // Show loading state and hide all content
    showArtistDetailLoading(true);
    showArtistDetailError(false);
    showArtistDetailMain(false);
    showArtistDetailHero(false);

    // Don't update header until data loads to avoid showing stale data

    try {
        // Call API to get artist discography data. If this artist came from a
        // metadata source (not the library), pass source + name so the backend
        // can synthesize a response from that source instead of 404ing on the
        // local DB lookup.
        const params = new URLSearchParams();
        if (artistDetailPageState.currentArtistSource) {
            params.set('source', artistDetailPageState.currentArtistSource);
        }
        if (artistName) {
            params.set('name', artistName);
        }
        const qs = params.toString();
        const response = await fetch(
            `/api/artist-detail/${encodeURIComponent(artistId)}${qs ? '?' + qs : ''}`
        );

        if (!response.ok) {
            throw new Error(`Failed to load artist data: ${response.statusText}`);
        }

        const data = await response.json();

        if (!data.success) {
            throw new Error(data.error || 'Failed to load artist data');
        }

        console.log(`✅ Loaded artist detail data:`, data);

        // Hide loading and show all content
        showArtistDetailLoading(false);
        showArtistDetailMain(true);
        showArtistDetailHero(true);

        console.log(`🎨 Main content visibility:`, document.getElementById('artist-detail-main'));
        console.log(`🎨 Albums section:`, document.getElementById('albums-section'));

        // Populate the page with data (which updates the hero section and sets textContent)
        populateArtistDetailPage(data);

        // Keep the resolved metadata source for album-track lookups.
        artistDetailPageState.currentArtistSource = data.discography?.source || data.artist?.source || null;

        // Update header with artist name and MusicBrainz link LAST to avoid overwrite
        updateArtistDetailPageHeaderWithData(data.artist);

        // Render per-artist enrichment coverage
        renderArtistEnrichmentCoverage(data.enrichment_coverage);

        // Start streaming ownership checks if we have Spotify discography with checking state
        if (data.discography && data.discography.albums) {
            const hasChecking = [...(data.discography.albums || []), ...(data.discography.eps || []), ...(data.discography.singles || [])]
                .some(r => r.owned === null);
            if (hasChecking) {
                // Store discography for stream updates
                artistDetailPageState.currentDiscography = data.discography;
                checkLibraryCompletion(data.artist.name, data.discography);
            }
        }

        // Check if artist has tracks eligible for quality enhancement
        checkArtistEnhanceEligibility(artistId);

    } catch (error) {
        console.error(`❌ Error loading artist detail data:`, error);

        // Show error state (keep hero section hidden)
        showArtistDetailLoading(false);
        showArtistDetailError(true, error.message);
        showArtistDetailHero(false);

        showToast(`Failed to load artist details: ${error.message}`, "error");
    }
}

function updateArtistDetailPageHeader(artistName) {
    // Update header title
    const headerTitle = document.getElementById("artist-detail-name");
    if (headerTitle) {
        headerTitle.textContent = artistName;
    }

    // Update main artist name
    const mainTitle = document.getElementById("artist-info-name");
    if (mainTitle) {
        mainTitle.textContent = artistName;
    }
}

function updateArtistDetailPageHeaderWithData(artist) {
    // Update name
    const mainTitle = document.getElementById("artist-detail-name");
    if (mainTitle) {
        mainTitle.textContent = artist.name;
        // Remove any old source links that were appended to the h1
        mainTitle.querySelectorAll('.source-link-btn').forEach(el => el.remove());
    }

    // Render badges in dedicated container
    const badgesContainer = document.getElementById("artist-hero-badges");
    if (badgesContainer) {
        const _hb = (logo, fallback, title, url) => {
            const inner = logo
                ? `<img src="${logo}" alt="${fallback}" onerror="this.parentNode.textContent='${fallback}'">`
                : `<span style="font-size:9px;font-weight:700;">${fallback}</span>`;
            if (url) return `<a class="artist-hero-badge" title="${title}" href="${url}" target="_blank" rel="noopener noreferrer">${inner}</a>`;
            return `<div class="artist-hero-badge" title="${title}">${inner}</div>`;
        };

        const adbSlug = artist.name ? artist.name.replace(/\s+/g, '-').replace(/[^a-zA-Z0-9-]/g, '') : '';
        const badges = [];
        if (artist.spotify_artist_id) badges.push(_hb(SPOTIFY_LOGO_URL, 'SP', 'Spotify', `https://open.spotify.com/artist/${artist.spotify_artist_id}`));
        if (artist.musicbrainz_id) badges.push(_hb(MUSICBRAINZ_LOGO_URL, 'MB', 'MusicBrainz', `https://musicbrainz.org/artist/${artist.musicbrainz_id}`));
        if (artist.deezer_id) badges.push(_hb(DEEZER_LOGO_URL, 'Dz', 'Deezer', `https://www.deezer.com/artist/${artist.deezer_id}`));
        if (artist.audiodb_id) badges.push(_hb(typeof getAudioDBLogoURL === 'function' ? getAudioDBLogoURL() : '', 'ADB', 'AudioDB', `https://www.theaudiodb.com/artist/${artist.audiodb_id}-${adbSlug}`));
        if (artist.itunes_artist_id) badges.push(_hb(ITUNES_LOGO_URL, 'IT', 'Apple Music', `https://music.apple.com/artist/${artist.itunes_artist_id}`));
        if (artist.lastfm_url) badges.push(_hb(LASTFM_LOGO_URL, 'LFM', 'Last.fm', artist.lastfm_url));
        if (artist.genius_url) badges.push(_hb(GENIUS_LOGO_URL, 'GEN', 'Genius', artist.genius_url));
        if (artist.tidal_id) badges.push(_hb(TIDAL_LOGO_URL, 'TD', 'Tidal', `https://tidal.com/browse/artist/${artist.tidal_id}`));
        if (artist.qobuz_id) badges.push(_hb(QOBUZ_LOGO_URL, 'Qz', 'Qobuz', `https://www.qobuz.com/artist/${artist.qobuz_id}`));
        if (artist.discogs_id) badges.push(_hb(DISCOGS_LOGO_URL, 'DC', 'Discogs', `https://www.discogs.com/artist/${artist.discogs_id}`));
        if (artist.soul_id && !String(artist.soul_id).startsWith('soul_unnamed_')) badges.push(_hb('/static/trans2.png', 'SS', `SoulID: ${artist.soul_id}`, null));

        badgesContainer.innerHTML = badges.join('');
    }
}

function renderArtistEnrichmentCoverage(enrichment) {
    const el = document.getElementById('artist-enrichment-coverage');
    if (!el) return;

    if (!enrichment || !enrichment.total_tracks) {
        el.style.display = 'none';
        return;
    }

    const services = [
        { name: 'Spotify', key: 'spotify', color: '#1db954' },
        { name: 'MusicBrainz', key: 'musicbrainz', color: '#ba55d3' },
        { name: 'Deezer', key: 'deezer', color: '#a238ff' },
        { name: 'Last.fm', key: 'lastfm', color: '#d51007' },
        { name: 'iTunes', key: 'itunes', color: '#fc3c44' },
        { name: 'AudioDB', key: 'audiodb', color: '#1a9fff' },
        { name: 'Discogs', key: 'discogs', color: '#D4A574' },
        { name: 'Genius', key: 'genius', color: '#ffff64' },
        { name: 'Tidal', key: 'tidal', color: '#00ffff' },
        { name: 'Qobuz', key: 'qobuz', color: '#4285f4' },
    ];

    const r = 20, circ = 2 * Math.PI * r;

    el.style.display = '';
    el.innerHTML = `
        <div class="artist-enrich-title">Enrichment Coverage</div>
        <div class="artist-enrich-grid">
            ${services.map((s, i) => {
        const pct = enrichment[s.key] || 0;
        const offset = circ - (circ * pct / 100);
        const delay = (i * 0.08).toFixed(2);
        return `<div class="artist-enrich-circle">
                    <div class="artist-enrich-ring" style="--ring-color:${s.color}">
                        <svg viewBox="0 0 48 48">
                            <circle class="ring-bg" cx="24" cy="24" r="${r}"/>
                            <circle class="ring-fill" cx="24" cy="24" r="${r}"
                                stroke="${s.color}" stroke-dasharray="${circ.toFixed(1)}"
                                style="--ring-circ:${circ.toFixed(1)};--ring-offset:${offset.toFixed(1)};stroke-dashoffset:${offset.toFixed(1)};animation:ringFillIn 1s cubic-bezier(0.4,0,0.2,1) ${delay}s both"/>
                        </svg>
                        <span class="ring-pct" style="animation:ringPctFade 0.8s ease ${(parseFloat(delay) + 0.3).toFixed(2)}s both">${Math.round(pct)}</span>
                    </div>
                    <span class="artist-enrich-label">${s.name}</span>
                </div>`;
    }).join('')}
        </div>
    `;
}

function populateArtistDetailPage(data) {
    const artist = data.artist;
    const discography = data.discography;

    console.log(`🎨 Populating artist detail page for: ${artist.name}`);
    console.log(`📀 Discography data:`, discography);
    console.log(`📀 Albums:`, discography.albums);
    console.log(`📀 EPs:`, discography.eps);
    console.log(`📀 Singles:`, discography.singles);

    // Update hero section with image, name, and stats
    updateArtistHeroSection(artist, discography);

    // Update genres (if element exists)
    updateArtistGenres(artist.genres);

    // Update summary stats (if element exists)
    updateArtistSummaryStats(discography);

    // Populate discography sections
    populateDiscographySections(discography);

    // Initialize library watchlist button if it exists (for library page)
    const libraryWatchlistBtn = document.getElementById('library-artist-watchlist-btn');
    if (libraryWatchlistBtn && data.spotify_artist && data.spotify_artist.spotify_artist_id) {
        initializeLibraryWatchlistButton(data.spotify_artist.spotify_artist_id, data.spotify_artist.spotify_artist_name);
    }
}

function updateArtistDetailImage(imageUrl, artistName) {
    const imageElement = document.getElementById("artist-detail-image");
    const fallbackElement = document.getElementById("artist-image-fallback");

    if (imageUrl && imageUrl.trim() !== "") {
        imageElement.src = imageUrl;
        imageElement.alt = artistName;
        imageElement.classList.remove("hidden");
        fallbackElement.classList.add("hidden");

        imageElement.onerror = () => {
            console.log(`Failed to load artist image for ${artistName}: ${imageUrl}`);
            // Replace with fallback on error
            imageElement.classList.add("hidden");
            fallbackElement.classList.remove("hidden");
        };

        imageElement.onload = () => {
            console.log(`Successfully loaded artist image for ${artistName}: ${imageUrl}`);
        };
    } else {
        console.log(`No image URL for ${artistName}: '${imageUrl}'`);
        imageElement.classList.add("hidden");
        fallbackElement.classList.remove("hidden");
    }
}

function updateArtistGenres(genres) {
    const genresContainer = document.getElementById("artist-genres");
    if (!genresContainer) return;

    genresContainer.innerHTML = "";

    // Clear any previous artist format tags (they arrive later via streaming)
    const oldFormats = genresContainer.parentElement?.querySelector('.artist-formats');
    if (oldFormats) oldFormats.remove();

    if (genres && genres.length > 0) {
        genres.forEach(genre => {
            const genreTag = document.createElement("span");
            genreTag.className = "genre-tag";
            genreTag.textContent = genre;
            genresContainer.appendChild(genreTag);
        });
    }
}

function updateArtistSummaryStats(discography) {
    const allReleases = [...discography.albums, ...discography.eps, ...discography.singles];
    const hasChecking = allReleases.some(r => r.owned === null);

    const ownedAlbums = discography.albums.filter(album => album.owned === true).length;
    const missingAlbums = discography.albums.filter(album => album.owned === false).length;
    const totalAlbums = discography.albums.length;
    const completionPercentage = totalAlbums > 0 ? Math.round((ownedAlbums / totalAlbums) * 100) : 0;

    // Update owned albums count
    const ownedElement = document.getElementById("owned-albums-count");
    if (ownedElement) {
        ownedElement.textContent = hasChecking ? '...' : ownedAlbums;
    }

    // Update missing albums count
    const missingElement = document.getElementById("missing-albums-count");
    if (missingElement) {
        missingElement.textContent = hasChecking ? '...' : missingAlbums;
    }

    // Update completion percentage
    const completionElement = document.getElementById("completion-percentage");
    if (completionElement) {
        completionElement.textContent = hasChecking ? 'Checking...' : `${completionPercentage}%`;
    }
}

function updateArtistHeaderStats(albumCount, trackCount) {
    // This function is deprecated - now using updateArtistHeroSection
    console.log("📊 Using new hero section instead of old header stats");
}

function updateArtistHeroSection(artist, discography) {
    console.log("🖼️ Updating artist hero section");

    // Update artist image with detailed debugging
    const imageElement = document.getElementById("artist-detail-image");
    const fallbackElement = document.getElementById("artist-detail-image-fallback");

    console.log(`🖼️ Debug Artist image info:`);
    console.log(`   - URL: '${artist.image_url}'`);
    console.log(`   - Type: ${typeof artist.image_url}`);
    console.log(`   - Full artist object:`, artist);
    console.log(`   - Image element:`, imageElement);
    console.log(`   - Fallback element:`, fallbackElement);

    if (artist.image_url && artist.image_url.trim() !== "" && artist.image_url !== "null") {
        console.log(`✅ Setting image src to: ${artist.image_url}`);
        imageElement.src = artist.image_url;
        imageElement.alt = artist.name;
        imageElement.style.display = "block";
        if (fallbackElement) {
            fallbackElement.style.display = "none";
        }

        imageElement.onload = () => {
            console.log(`✅ Successfully loaded artist image: ${artist.image_url}`);
        };

        imageElement.onerror = () => {
            console.error(`❌ Failed to load artist image: ${artist.image_url}`);
            // Try Deezer fallback before emoji
            if (artist.deezer_id && !imageElement.dataset.triedDeezer) {
                imageElement.dataset.triedDeezer = 'true';
                imageElement.src = `https://api.deezer.com/artist/${artist.deezer_id}/image?size=big`;
            } else {
                imageElement.style.display = "none";
                if (fallbackElement) {
                    fallbackElement.style.display = "flex";
                }
            }
        };
    } else {
        console.log(`🖼️ No valid image URL - showing fallback for ${artist.name}`);
        imageElement.style.display = "none";
        if (fallbackElement) {
            fallbackElement.style.display = "flex";
        }
    }

    // Update artist name
    const nameElement = document.getElementById("artist-detail-name");
    if (nameElement) {
        nameElement.textContent = artist.name;
    }

    // Calculate and update stats for each category
    updateCategoryStats('albums', discography.albums);
    updateCategoryStats('eps', discography.eps);
    updateCategoryStats('singles', discography.singles);

    // Show Download Discography button(s) if there are any releases
    const _totalReleases = (discography.albums?.length || 0) + (discography.eps?.length || 0) + (discography.singles?.length || 0);
    const _discogWrap = document.getElementById('discog-download-wrap');
    if (_discogWrap) _discogWrap.style.display = _totalReleases > 0 ? '' : 'none';
    const _discogBtnArtists = document.getElementById('discog-download-btn-artists');
    if (_discogBtnArtists) _discogBtnArtists.style.display = _totalReleases > 0 ? '' : 'none';

    // Last.fm stats (listeners / playcount)
    const _fmtNum = (n) => {
        if (!n || n <= 0) return '0';
        if (n >= 1000000) return (n / 1000000).toFixed(1).replace(/\.0$/, '') + 'M';
        if (n >= 1000) return (n / 1000).toFixed(1).replace(/\.0$/, '') + 'K';
        return n.toLocaleString();
    };

    const listenersEl = document.getElementById('artist-hero-listeners');
    if (listenersEl) {
        if (artist.lastfm_listeners) {
            listenersEl.querySelector('.hero-stat-value').textContent = _fmtNum(artist.lastfm_listeners);
            listenersEl.style.display = '';
        } else {
            listenersEl.style.display = 'none';
        }
    }

    const playcountEl = document.getElementById('artist-hero-playcount');
    if (playcountEl) {
        if (artist.lastfm_playcount) {
            playcountEl.querySelector('.hero-stat-value').textContent = _fmtNum(artist.lastfm_playcount);
            playcountEl.style.display = '';
        } else {
            playcountEl.style.display = 'none';
        }
    }

    // Last.fm bio
    const bioEl = document.getElementById('artist-hero-bio');
    if (bioEl) {
        const bio = artist.lastfm_bio;
        if (bio && bio.trim()) {
            // Strip HTML tags and "Read more on Last.fm" links
            let cleanBio = bio.replace(/<a\b[^>]*>.*?<\/a>/gi, '').replace(/<[^>]+>/g, '').trim();
            if (cleanBio) {
                bioEl.innerHTML = `<span class="bio-text">${cleanBio}</span>
                    <span class="artist-hero-bio-toggle" onclick="this.parentElement.classList.toggle('expanded');this.textContent=this.parentElement.classList.contains('expanded')?'Show less':'Read more'">Read more</span>`;
                bioEl.style.display = '';
            } else {
                bioEl.style.display = 'none';
            }
        } else {
            bioEl.style.display = 'none';
        }
    }

    // Last.fm tags — merge with existing genres (deduplicate)
    if (artist.lastfm_tags) {
        try {
            let lfmTags = typeof artist.lastfm_tags === 'string' ? JSON.parse(artist.lastfm_tags) : artist.lastfm_tags;
            if (Array.isArray(lfmTags) && lfmTags.length > 0) {
                const existingGenres = new Set((artist.genres || []).map(g => g.toLowerCase()));
                const newTags = lfmTags.filter(t => !existingGenres.has(t.toLowerCase())).slice(0, 5);
                if (newTags.length > 0) {
                    const genresContainer = document.getElementById('artist-genres');
                    if (genresContainer) {
                        newTags.forEach(tag => {
                            const el = document.createElement('span');
                            el.className = 'genre-tag';
                            el.textContent = tag;
                            el.style.opacity = '0.6';
                            genresContainer.appendChild(el);
                        });
                    }
                }
            }
        } catch (e) {
            console.debug('Failed to parse Last.fm tags:', e);
        }
    }

    // Lazy-load top tracks sidebar
    if (artist.lastfm_url || artist.lastfm_listeners) {
        _loadArtistTopTracks(artist.name);
    }
}

async function _loadArtistTopTracks(artistName) {
    const sidebar = document.getElementById('artist-hero-sidebar');
    const container = document.getElementById('hero-top-tracks');
    if (!sidebar || !container) return;

    try {
        const resp = await fetch(`/api/artist/0/lastfm-top-tracks?name=${encodeURIComponent(artistName)}`);
        const data = await resp.json();
        if (!data.success || !data.tracks || data.tracks.length === 0) {
            sidebar.style.display = 'none';
            return;
        }

        const _fmtNum = (n) => {
            if (!n || n <= 0) return '0';
            if (n >= 1000000) return (n / 1000000).toFixed(1).replace(/\.0$/, '') + 'M';
            if (n >= 1000) return (n / 1000).toFixed(1).replace(/\.0$/, '') + 'K';
            return n.toLocaleString();
        };

        const _escAttr = (s) => (s || '').replace(/&/g, '&amp;').replace(/"/g, '&quot;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
        container.innerHTML = data.tracks.map((t, i) => `
            <div class="hero-top-track">
                <span class="hero-top-track-num">${i + 1}</span>
                <button class="hero-top-track-play" data-track="${_escAttr(t.name)}" data-artist="${_escAttr(artistName)}" title="Play">▶</button>
                <span class="hero-top-track-name" title="${_escAttr(t.name)}">${_escAttr(t.name)}</span>
                <span class="hero-top-track-plays">${_fmtNum(t.playcount)}</span>
            </div>
        `).join('');

        // Attach play handlers via delegation (avoids inline JS escaping issues)
        container.onclick = (e) => {
            const btn = e.target.closest('.hero-top-track-play');
            if (btn) {
                e.stopPropagation();
                playStatsTrack(btn.dataset.track, btn.dataset.artist, '');
            }
        };
        sidebar.style.display = '';
    } catch (e) {
        console.debug('Failed to load top tracks:', e);
        sidebar.style.display = 'none';
    }
}

function updateCategoryStats(category, releases) {
    const hasChecking = releases.some(r => r.owned === null);
    const owned = releases.filter(r => r.owned === true).length;
    const total = releases.length;
    const completion = total > 0 ? Math.round((owned / total) * 100) : 100;

    // Update stats text (compact: "3/12")
    const statsElement = document.getElementById(`${category}-stats`);
    if (statsElement) {
        statsElement.textContent = hasChecking ? '...' : `${owned}/${total}`;
    }

    // Update completion bar
    const fillElement = document.getElementById(`${category}-completion-fill`);
    if (fillElement) {
        if (hasChecking) {
            fillElement.style.width = '100%';
            fillElement.classList.add('checking');
        } else {
            fillElement.style.width = `${completion}%`;
            fillElement.classList.remove('checking');
        }
    }
}

function populateDiscographySections(discography) {
    // Populate albums
    populateReleaseSection('albums', discography.albums);

    // Populate EPs
    populateReleaseSection('eps', discography.eps);

    // Populate singles
    populateReleaseSection('singles', discography.singles);

    // Apply any active filters after populating
    applyDiscographyFilters();
}

function populateReleaseSection(sectionType, releases) {
    const gridId = `${sectionType}-grid`;
    const ownedCountId = `${sectionType}-owned-count`;
    const missingCountId = `${sectionType}-missing-count`;

    const grid = document.getElementById(gridId);
    if (!grid) return;

    // Clear existing content
    grid.innerHTML = "";

    const hasChecking = releases.some(r => r.owned === null);
    const ownedCount = releases.filter(release => release.owned === true).length;
    const missingCount = releases.filter(release => release.owned === false).length;

    // Update section stats
    const ownedElement = document.getElementById(ownedCountId);
    const missingElement = document.getElementById(missingCountId);

    if (ownedElement) {
        ownedElement.textContent = hasChecking ? 'Checking...' : `${ownedCount} owned`;
    }

    if (missingElement) {
        missingElement.textContent = hasChecking ? '' : `${missingCount} missing`;
    }

    // Create release cards
    releases.forEach((release, index) => {
        const card = createReleaseCard(release);
        grid.appendChild(card);
    });

    console.log(`📀 Populated ${sectionType} section: ${ownedCount} owned, ${missingCount} missing`);
    console.log(`📀 Grid element:`, grid);
    console.log(`📀 Grid children count:`, grid.children.length);
}

function createReleaseCard(release) {
    const card = document.createElement("div");
    const isChecking = release.owned === null;
    card.className = `release-card${isChecking ? " checking" : (release.owned ? "" : " missing")}`;
    const releaseId = release.id || "";
    card.setAttribute("data-release-id", releaseId);
    // Store mutable reference so stream updates propagate to click handler
    card._releaseData = release;

    // Tag card for content-type filtering
    const titleLower = (release.title || '').toLowerCase();
    const livePattern = /\b(live)\b|\(live[^)]*\)|\[live[^]]*\]/i;
    const compilationPattern = /\b(greatest hits|best of|collection|anthology|essential)\b/i;
    const featuredPattern = /\(?\bfeat\.?\s|\bft\.?\s|\bfeaturing\b/i;
    const isLive = livePattern.test(release.title || '') || (release.album_type === 'compilation' && livePattern.test(release.title || ''));
    const isCompilation = (release.album_type === 'compilation') || compilationPattern.test(release.title || '');
    const isFeatured = featuredPattern.test(release.title || '');
    card.setAttribute("data-is-live", isLive ? "true" : "false");
    card.setAttribute("data-is-compilation", isCompilation ? "true" : "false");
    card.setAttribute("data-is-featured", isFeatured ? "true" : "false");

    // Add MusicBrainz icon if available
    let mbIcon = null;
    if (release.musicbrainz_release_id) {
        mbIcon = document.createElement("div");
        mbIcon.className = "mb-card-icon";
        mbIcon.title = "View on MusicBrainz";
        mbIcon.innerHTML = `<img src="${MUSICBRAINZ_LOGO_URL}" style="width: 20px; height: auto; display: block;">`;
        mbIcon.onclick = (e) => {
            e.stopPropagation();
            window.open(`https://musicbrainz.org/release/${release.musicbrainz_release_id}`, '_blank');
        };
    }

    // Create image
    const imageContainer = document.createElement("div");
    if (release.image_url && release.image_url.trim() !== "") {
        const img = document.createElement("img");
        img.src = release.image_url;
        img.alt = release.title;
        img.className = "release-image";
        img.loading = 'lazy';
        img.onerror = () => {
            imageContainer.innerHTML = `<div class="release-image-fallback">💿</div>`;
        };
        imageContainer.appendChild(img);
    } else {
        imageContainer.innerHTML = `<div class="release-image-fallback">💿</div>`;
    }

    // Create title
    const title = document.createElement("h4");
    title.className = "release-title";
    title.textContent = release.title;
    title.title = release.title;

    // Create year - extract from release_date (Spotify format) or fall back to year field
    const year = document.createElement("div");
    year.className = "release-year";

    let yearText = "Unknown Year";

    // DEBUG: Log the release data to see what we're working with (remove this after testing)
    // console.log(`🔍 DEBUG: Release "${release.title}" data:`, {
    //     title: release.title,
    //     owned: release.owned,
    //     year: release.year,
    //     release_date: release.release_date,
    //     track_completion: release.track_completion
    // });

    // First try to extract year from release_date (Spotify format: "YYYY-MM-DD")
    if (release.release_date) {
        try {
            // Extract year directly from string to avoid timezone issues
            const yearMatch = release.release_date.match(/^(\d{4})/);
            if (yearMatch) {
                const releaseYear = parseInt(yearMatch[1]);
                if (releaseYear && !isNaN(releaseYear) && releaseYear > 1900 && releaseYear <= new Date().getFullYear() + 1) {
                    yearText = releaseYear.toString();
                }
            } else {
                // Fallback to Date parsing if format is different
                const releaseYear = new Date(release.release_date).getFullYear();
                if (releaseYear && !isNaN(releaseYear) && releaseYear > 1900 && releaseYear <= new Date().getFullYear() + 1) {
                    yearText = releaseYear.toString();
                }
            }
        } catch (e) {
            console.warn('Error parsing release_date:', release.release_date, e);
        }
    }

    // Fallback to direct year field if release_date parsing failed
    if (yearText === "Unknown Year" && release.year) {
        yearText = release.year.toString();
    }

    year.textContent = yearText;

    // Create completion info
    const completion = document.createElement("div");
    completion.className = "release-completion";

    const completionText = document.createElement("span");
    const completionBar = document.createElement("div");
    completionBar.className = "completion-bar";

    const completionFill = document.createElement("div");
    completionFill.className = "completion-fill";

    if (release.owned === null || release.track_completion === 'checking') {
        // Checking state - ownership not yet resolved
        completionText.textContent = "Checking...";
        completionText.className = "completion-text checking";
        completionFill.className += " checking";
        completionFill.style.width = "100%";
    } else if (release.owned) {
        // Handle new detailed track completion object
        if (release.track_completion && typeof release.track_completion === 'object') {
            const completion = release.track_completion;
            const percentage = completion.percentage || 100;
            const ownedTracks = completion.owned_tracks || 0;
            const totalTracks = completion.total_tracks || 0;
            const missingTracks = completion.missing_tracks || 0;

            completionFill.style.width = `${percentage}%`;

            if (missingTracks === 0) {
                completionText.textContent = `Complete (${ownedTracks})`;
                completionText.className = "completion-text complete";
                completionFill.className += " complete";
            } else {
                completionText.textContent = `${ownedTracks}/${totalTracks} tracks`;
                completionText.className = "completion-text partial";
                completionFill.className += " partial";

                // Add missing tracks indicator
                completionText.title = `Missing ${missingTracks} track${missingTracks !== 1 ? 's' : ''}`;
            }
        } else {
            // Fallback for legacy simple percentage
            const percentage = release.track_completion || 100;
            completionFill.style.width = `${percentage}%`;

            if (percentage === 100) {
                completionText.textContent = "Complete";
                completionText.className = "completion-text complete";
                completionFill.className += " complete";
            } else {
                completionText.textContent = `${percentage}%`;
                completionText.className = "completion-text partial";
                completionFill.className += " partial";
            }
        }
    } else {
        const totalTr = release.total_tracks || release.track_completion?.total_tracks || 0;
        completionText.textContent = totalTr > 0 ? `Missing (${totalTr} tracks)` : "Not in library";
        completionText.className = "completion-text missing";
        completionFill.className += " missing";
        completionFill.style.width = "0%";
    }

    completionBar.appendChild(completionFill);
    completion.appendChild(completionText);
    completion.appendChild(completionBar);

    // Assemble card
    card.appendChild(imageContainer);
    card.appendChild(title);
    card.appendChild(year);
    card.appendChild(completion);

    // Add MusicBrainz icon LAST to ensure it's on top
    if (release.musicbrainz_release_id && mbIcon) { // Check if mbIcon was created
        card.appendChild(mbIcon);
    }

    // Add click handler for release card (uses card._releaseData for mutable reference)
    card.addEventListener("click", async () => {
        const rel = card._releaseData;
        console.log(`Clicked on release: ${rel.title} (Owned: ${rel.owned})`);

        // Still checking - ignore click
        if (rel.owned === null) {
            showToast(`Still checking ownership for ${rel.title}...`, "info");
            return;
        }

        showLoadingOverlay('Loading album...');

        // For missing or incomplete releases, open wishlist modal
        try {
            // Convert release object to album format expected by our function
            const albumData = {
                id: rel.id,
                name: rel.title,
                image_url: rel.image_url,
                release_date: rel.year ? `${rel.year}-01-01` : '',
                album_type: rel.album_type || rel.type || 'album',
                total_tracks: (rel.track_completion && typeof rel.track_completion === 'object')
                    ? rel.track_completion.total_tracks : (rel.track_count || 1)
            };

            // Get current artist from artist detail page state
            const currentArtist = artistDetailPageState.currentArtistName ? {
                id: artistDetailPageState.currentArtistId,
                name: artistDetailPageState.currentArtistName,
                image_url: getArtistImageFromPage() || '', // Get artist image from page
                source: artistDetailPageState.currentArtistSource || null
            } : null;

            if (!currentArtist) {
                console.error('❌ No current artist found for release click');
                showToast('Error: No artist information available', 'error');
                return;
            }

            // Load tracks for the album (pass name/artist for Hydrabase support)
            const _aat2 = new URLSearchParams({ name: albumData.name || '', artist: currentArtist.name || '' });
            if (currentArtist.source) {
                _aat2.set('source', currentArtist.source);
            }
            const response = await fetch(`/api/album/${albumData.id}/tracks?${_aat2}`);
            if (!response.ok) {
                throw new Error(`Failed to load album tracks: ${response.status}`);
            }

            const data = await response.json();
            if (!data.success || !data.tracks || data.tracks.length === 0) {
                throw new Error('No tracks found for this release');
            }

            // Use the actual album type from release data
            const albumType = rel.album_type || rel.type || 'album';

            // Open the Add to Wishlist modal immediately (no waiting for ownership check)
            hideLoadingOverlay();
            await openAddToWishlistModal(albumData, currentArtist, data.tracks, albumType);

            // Always lazy-load track ownership + metadata (non-blocking)
            lazyLoadTrackOwnership(currentArtist.name, data.tracks, card, albumData.name);

        } catch (error) {
            hideLoadingOverlay();
            console.error('❌ Error handling release click:', error);
            showToast(`Error opening wishlist modal: ${error.message}`, 'error');
        }
    });

    return card;
}

/**
 * Helper function to get artist image from the current artist detail page
 */
function getArtistImageFromPage() {
    try {
        // Try to get from artist detail image element
        const artistDetailImage = document.getElementById('artist-detail-image');
        if (artistDetailImage && artistDetailImage.src && artistDetailImage.src !== window.location.href) {
            return artistDetailImage.src;
        }

        // Try to get from artist hero image
        const artistImage = document.getElementById('artist-image');
        if (artistImage) {
            const bgImage = window.getComputedStyle(artistImage).backgroundImage;
            if (bgImage && bgImage !== 'none') {
                // Extract URL from CSS background-image
                const urlMatch = bgImage.match(/url\(["']?(.*?)["']?\)/);
                if (urlMatch && urlMatch[1]) {
                    return urlMatch[1];
                }
            }
        }

        return null;
    } catch (error) {
        console.warn('Error getting artist image from page:', error);
        return null;
    }
}

// ================================================================================================
// LIBRARY COMPLETION STREAMING - Two-phase lazy-load pattern
// ================================================================================================

async function checkLibraryCompletion(artistName, discography) {
    // Abort any in-progress check
    if (artistDetailPageState.completionController) {
        artistDetailPageState.completionController.abort();
    }
    artistDetailPageState.completionController = new AbortController();

    const payload = {
        artist_name: artistName,
        albums: discography.albums || [],
        eps: discography.eps || [],
        singles: discography.singles || [],
        source: discography?.source || null
    };

    try {
        const response = await fetch('/api/library/completion-stream', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload),
            signal: artistDetailPageState.completionController.signal
        });

        if (!response.ok) {
            console.error(`❌ Completion stream failed: ${response.status}`);
            return;
        }

        const reader = response.body.getReader();
        const decoder = new TextDecoder();
        let buffer = '';
        let ownedCounts = { albums: 0, eps: 0, singles: 0 };
        let totalCounts = { albums: 0, eps: 0, singles: 0 };
        const artistFormatSet = new Set();

        while (true) {
            const { done, value } = await reader.read();
            if (done) break;

            buffer += decoder.decode(value, { stream: true });
            const lines = buffer.split('\n');
            buffer = lines.pop(); // Keep incomplete line in buffer

            for (const line of lines) {
                if (!line.startsWith('data: ')) continue;
                try {
                    const eventData = JSON.parse(line.slice(6));
                    if (eventData.type === 'completion') {
                        updateLibraryReleaseCard(eventData);
                        totalCounts[eventData.category]++;
                        if (eventData.status !== 'missing' && eventData.status !== 'error') {
                            ownedCounts[eventData.category]++;
                            // Accumulate formats for artist-level summary
                            if (eventData.formats) {
                                eventData.formats.forEach(f => artistFormatSet.add(f));
                            }
                        }
                        // Update stats incrementally
                        updateCategoryStatsFromStream(
                            eventData.category,
                            ownedCounts[eventData.category],
                            totalCounts[eventData.category] - ownedCounts[eventData.category]
                        );
                    } else if (eventData.type === 'complete') {
                        console.log(`✅ Library completion stream done: ${eventData.processed_count} items`);
                        // Final stats recalculation
                        recalculateSummaryStats(artistFormatSet);
                    }
                } catch (parseError) {
                    console.warn('Error parsing SSE event:', parseError, line);
                }
            }
        }
    } catch (error) {
        if (error.name === 'AbortError') {
            console.log('🛑 Library completion stream aborted (navigation)');
        } else {
            console.error('❌ Error in library completion stream:', error);
        }
    }
}

function updateLibraryReleaseCard(data) {
    const releaseId = data.id || "";
    const card = document.querySelector(`[data-release-id="${releaseId}"]`);
    if (!card) return;

    const isOwned = data.status !== 'missing' && data.status !== 'error';

    // Update card class
    card.classList.remove('checking', 'missing');
    if (!isOwned) {
        card.classList.add('missing');
    }

    // Use real numbers — no rounding or overrides
    const isComplete = data.owned_tracks >= data.expected_tracks && data.owned_tracks > 0;
    const effectiveMissing = data.expected_tracks - data.owned_tracks;

    // Update the mutable release data on the card
    if (card._releaseData) {
        card._releaseData.owned = isOwned;
        if (isOwned && data.expected_tracks > 0) {
            card._releaseData.track_completion = {
                owned_tracks: data.owned_tracks,
                total_tracks: isComplete ? data.owned_tracks : data.expected_tracks,
                percentage: isComplete ? 100 : data.completion_percentage,
                missing_tracks: effectiveMissing
            };
        } else if (isOwned) {
            card._releaseData.track_completion = {
                owned_tracks: data.owned_tracks,
                total_tracks: data.owned_tracks,
                percentage: 100,
                missing_tracks: 0
            };
        } else {
            card._releaseData.track_completion = 0;
        }
    }

    // Update completion text element in-place
    const completionText = card.querySelector('.completion-text');
    if (completionText) {
        completionText.classList.remove('checking', 'complete', 'partial', 'missing');
        if (isOwned) {
            if (effectiveMissing <= 0) {
                completionText.textContent = `Complete (${data.owned_tracks})`;
                completionText.className = 'completion-text complete';
            } else {
                completionText.textContent = `${data.owned_tracks}/${data.expected_tracks} tracks`;
                completionText.className = 'completion-text partial';
                completionText.title = `Missing ${effectiveMissing} track${effectiveMissing !== 1 ? 's' : ''}`;
            }
        } else {
            completionText.textContent = 'Missing';
            completionText.className = 'completion-text missing';
        }
    }

    // Update completion fill bar in-place
    const completionFill = card.querySelector('.completion-fill');
    if (completionFill) {
        completionFill.classList.remove('checking', 'complete', 'partial', 'missing');
        if (isOwned) {
            const pct = isComplete ? 100 : (data.completion_percentage || 100);
            completionFill.style.width = `${pct}%`;
            completionFill.classList.add(effectiveMissing <= 0 ? 'complete' : 'partial');
        } else {
            completionFill.style.width = '0%';
            completionFill.classList.add('missing');
        }
    }

    // Display format tags on owned releases
    if (isOwned && data.formats && data.formats.length > 0) {
        // Store formats on release data for modal use
        if (card._releaseData) {
            card._releaseData.formats = data.formats;
        }
        // Remove any existing format tags
        const existingFormats = card.querySelector('.release-formats');
        if (existingFormats) existingFormats.remove();

        const formatsDiv = document.createElement('div');
        formatsDiv.className = 'release-formats';
        formatsDiv.innerHTML = data.formats.map(f => `<span class="release-format-tag">${f}</span>`).join('');
        card.appendChild(formatsDiv);
    }

    // Re-apply filters so newly resolved cards respect active filters
    applyDiscographyFilters();
}

function updateCategoryStatsFromStream(category, ownedCount, missingCount) {
    const total = ownedCount + missingCount;
    const completion = total > 0 ? Math.round((ownedCount / total) * 100) : 100;

    const statsElement = document.getElementById(`${category}-stats`);
    if (statsElement) {
        statsElement.textContent = `${ownedCount}/${total}`;
    }

    const fillElement = document.getElementById(`${category}-completion-fill`);
    if (fillElement) {
        fillElement.classList.remove('checking');
        fillElement.style.width = `${completion}%`;
    }
}

function recalculateSummaryStats(artistFormatSet) {
    const disc = artistDetailPageState.currentDiscography;
    if (!disc) return;

    // Recalculate from the live card data
    const categories = ['albums', 'eps', 'singles'];
    for (const cat of categories) {
        const grid = document.getElementById(`${cat}-grid`);
        if (!grid) continue;
        let owned = 0, missing = 0;
        grid.querySelectorAll('.release-card').forEach(card => {
            if (card._releaseData) {
                if (card._releaseData.owned === true) owned++;
                else if (card._releaseData.owned === false) missing++;
            }
        });
        updateCategoryStatsFromStream(cat, owned, missing);
    }

    // Update summary stats (albums only, matches original behavior)
    const albumGrid = document.getElementById('albums-grid');
    if (albumGrid) {
        let ownedAlbums = 0, missingAlbums = 0;
        albumGrid.querySelectorAll('.release-card').forEach(card => {
            if (card._releaseData) {
                if (card._releaseData.owned === true) ownedAlbums++;
                else if (card._releaseData.owned === false) missingAlbums++;
            }
        });
        const total = ownedAlbums + missingAlbums;
        const pct = total > 0 ? Math.round((ownedAlbums / total) * 100) : 0;

        const ownedEl = document.getElementById("owned-albums-count");
        if (ownedEl) ownedEl.textContent = ownedAlbums;
        const missingEl = document.getElementById("missing-albums-count");
        if (missingEl) missingEl.textContent = missingAlbums;
        const completionEl = document.getElementById("completion-percentage");
        if (completionEl) completionEl.textContent = `${pct}%`;
    }

    // Display artist-level format summary
    if (artistFormatSet && artistFormatSet.size > 0) {
        const heroInfo = document.querySelector('.artist-hero-section .artist-info');
        if (heroInfo) {
            // Remove any existing artist format tag
            const existing = heroInfo.querySelector('.artist-formats');
            if (existing) existing.remove();

            const formatsDiv = document.createElement('div');
            formatsDiv.className = 'artist-formats';
            formatsDiv.innerHTML = [...artistFormatSet].sort()
                .map(f => `<span class="artist-format-tag">${f}</span>`)
                .join('');
            // Insert after genres container
            const genresContainer = heroInfo.querySelector('.artist-genres-container');
            if (genresContainer && genresContainer.nextSibling) {
                heroInfo.insertBefore(formatsDiv, genresContainer.nextSibling);
            } else {
                heroInfo.appendChild(formatsDiv);
            }
        }
    }
}

// ===============================================
// Discography Filter Functions
// ===============================================

function initializeDiscographyFilters() {
    const container = document.getElementById('discography-filters');
    if (!container) return;

    container.addEventListener('click', (e) => {
        const btn = e.target.closest('.discography-filter-btn');
        if (!btn) return;

        const filterType = btn.dataset.filter;
        const value = btn.dataset.value;

        if (filterType === 'category') {
            // Multi-toggle: toggle this category on/off
            btn.classList.toggle('active');
            discographyFilterState.categories[value] = btn.classList.contains('active');
        } else if (filterType === 'content') {
            // Multi-toggle: toggle this content type on/off
            btn.classList.toggle('active');
            discographyFilterState.content[value] = btn.classList.contains('active');
        } else if (filterType === 'ownership') {
            // Single-select: deactivate siblings, activate this one
            container.querySelectorAll('[data-filter="ownership"]').forEach(b => b.classList.remove('active'));
            btn.classList.add('active');
            discographyFilterState.ownership = value;
        }

        applyDiscographyFilters();
    });
}

function resetDiscographyFilters() {
    discographyFilterState.categories = { albums: true, eps: true, singles: true };
    discographyFilterState.content = { live: true, compilations: true, featured: true };
    discographyFilterState.ownership = 'all';

    // Reset button visual states
    const container = document.getElementById('discography-filters');
    if (!container) return;
    container.querySelectorAll('.discography-filter-btn').forEach(btn => {
        const filterType = btn.dataset.filter;
        const value = btn.dataset.value;
        if (filterType === 'ownership') {
            btn.classList.toggle('active', value === 'all');
        } else {
            btn.classList.add('active');
        }
    });
}

function applyDiscographyFilters() {
    const categories = ['albums', 'eps', 'singles'];

    for (const cat of categories) {
        const section = document.getElementById(`${cat}-section`);
        if (!section) continue;

        // Category toggle — hide entire section
        if (!discographyFilterState.categories[cat]) {
            section.style.display = 'none';
            continue;
        }
        section.style.display = '';

        // Filter individual cards within the section
        const grid = document.getElementById(`${cat}-grid`);
        if (!grid) continue;

        let visibleOwned = 0;
        let visibleMissing = 0;
        let visibleCount = 0;

        grid.querySelectorAll('.release-card').forEach(card => {
            let hidden = false;

            // Content filters
            if (!discographyFilterState.content.live && card.getAttribute('data-is-live') === 'true') {
                hidden = true;
            }
            if (!discographyFilterState.content.compilations && card.getAttribute('data-is-compilation') === 'true') {
                hidden = true;
            }
            if (!discographyFilterState.content.featured && card.getAttribute('data-is-featured') === 'true') {
                hidden = true;
            }

            // Ownership filter (only apply if card is not still checking)
            if (!hidden && discographyFilterState.ownership !== 'all' && card._releaseData) {
                const owned = card._releaseData.owned;
                if (owned !== null) {  // Don't hide cards still being checked
                    if (discographyFilterState.ownership === 'owned' && !owned) hidden = true;
                    if (discographyFilterState.ownership === 'missing' && owned) hidden = true;
                }
            }

            card.style.display = hidden ? 'none' : '';

            // Count visible cards for stats
            if (!hidden && card._releaseData) {
                visibleCount++;
                if (card._releaseData.owned === true) visibleOwned++;
                else if (card._releaseData.owned === false) visibleMissing++;
            }
        });

        // Update section stats to reflect filtered view
        const ownedEl = document.getElementById(`${cat}-owned-count`);
        const missingEl = document.getElementById(`${cat}-missing-count`);
        if (ownedEl) ownedEl.textContent = `${visibleOwned} owned`;
        if (missingEl) missingEl.textContent = `${visibleMissing} missing`;

        // Hide section entirely if all cards are hidden
        section.style.display = visibleCount === 0 ? 'none' : '';
    }
}

// ==================== Download Discography Modal ====================

async function openDiscographyModal() {
    // Support both Artists search page and Library artist detail page
    let artist = artistsPageState.selectedArtist;
    let discography = artistsPageState.artistDiscography;
    let completionCache = artistsPageState.cache.completionData;

    // Fallback to Library page state if Artists page has no data for THIS artist
    const libId = artistDetailPageState.currentArtistId;
    const libName = artistDetailPageState.currentArtistName;
    const isLibraryPage = libId && libName;
    const artistsPageMatchesLibrary = artist && isLibraryPage && artist.name?.toLowerCase() === libName?.toLowerCase();

    if (isLibraryPage && (!artist || !discography || !artistsPageMatchesLibrary)) {
        // On library page — don't trust stale artistsPageState from a previous Artists page search
        artist = { id: libId, name: libName, image_url: document.getElementById('artist-detail-image')?.src || '' };
        discography = null;

        let metadataArtistId = null;
        try {
            showToast('Loading discography...', 'info');

            // Fetch the artist's metadata IDs from the DB (enhanced view may not be loaded)
            let lookupId = libId;
            try {
                const idRes = await fetch(`/api/library/artist/${libId}/enhanced`);
                const idData = await idRes.json();
                if (idData.success && idData.artist) {
                    const a = idData.artist;
                    metadataArtistId = a.spotify_artist_id || a.itunes_artist_id || a.deezer_id || null;
                    lookupId = metadataArtistId || libId;
                }
            } catch (e) {
                console.debug('[Discography] Could not fetch artist IDs, using DB id');
            }

            const res = await fetch(`/api/artist/${encodeURIComponent(lookupId)}/discography?artist_name=${encodeURIComponent(libName)}`);
            const data = await res.json();

            if (!data.error) {
                discography = { albums: data.albums || [], singles: data.singles || [] };
                if (discography.albums.length > 0 || discography.singles.length > 0) {
                    artistsPageState.artistDiscography = discography;
                    artistsPageState.sourceOverride = data.source || artistsPageState.sourceOverride || null;
                    // Use metadata source ID for the modal (needed for download API calls)
                    if (metadataArtistId) artist.id = metadataArtistId;
                    artist.source = data.source || null;
                    artistsPageState.selectedArtist = artist;
                } else {
                    discography = null;
                }
            }
        } catch (e) {
            console.error('Failed to load discography:', e);
        }
    }

    if (!artist || !discography) {
        showToast('No discography found. Try searching this artist from the Search page instead.', 'error');
        return;
    }

    const completionData = (completionCache || {})[artist.id] || {};
    const allReleases = [
        ...(discography.albums || []).map(a => ({ ...a, _type: 'album' })),
        ...(discography.eps || []).map(a => ({ ...a, _type: 'ep' })),
        ...(discography.singles || []).map(a => ({ ...a, _type: 'single' })),
    ];

    // Build modal
    const overlay = document.createElement('div');
    overlay.className = 'discog-modal-overlay';
    overlay.id = 'discog-modal-overlay';

    const artistImg = artist.image_url || '';

    overlay.innerHTML = `
        <div class="discog-modal">
            <div class="discog-modal-hero" ${artistImg ? `style="background-image:url('${artistImg}')"` : ''}>
                <div class="discog-modal-hero-overlay"></div>
                <div class="discog-modal-hero-content">
                    <h2 class="discog-modal-title">Download Discography</h2>
                    <p class="discog-modal-artist">${_esc(artist.name)}</p>
                </div>
                <button class="discog-modal-close" onclick="closeDiscographyModal()">&times;</button>
            </div>
            <div class="discog-filter-bar">
                <div class="discog-filters">
                    <button class="discog-filter active" data-type="album" onclick="toggleDiscogFilter(this)">Albums</button>
                    <button class="discog-filter active" data-type="ep" onclick="toggleDiscogFilter(this)">EPs</button>
                    <button class="discog-filter active" data-type="single" onclick="toggleDiscogFilter(this)">Singles</button>
                </div>
                <div class="discog-select-actions">
                    <button class="discog-select-btn" onclick="discogSelectAll(true)">Select All</button>
                    <button class="discog-select-btn" onclick="discogSelectAll(false)">Deselect All</button>
                </div>
            </div>
            <div class="discog-grid" id="discog-grid">
                ${allReleases.map((r, i) => _renderDiscogCard(r, i, completionData)).join('')}
            </div>
            <div class="discog-progress" id="discog-progress" style="display:none;"></div>
            <div class="discog-footer" id="discog-footer">
                <div class="discog-footer-info" id="discog-footer-info"></div>
                <div class="discog-footer-actions">
                    <button class="discog-cancel-btn" onclick="closeDiscographyModal()">Cancel</button>
                    <button class="discog-submit-btn" id="discog-submit-btn">
                        <span class="discog-submit-icon">⬇</span>
                        <span id="discog-submit-text">Add to Wishlist</span>
                    </button>
                </div>
            </div>
        </div>
    `;

    document.body.appendChild(overlay);
    requestAnimationFrame(() => overlay.classList.add('visible'));
    _updateDiscogFooterCount();

    // Bind submit button (avoids onclick being intercepted by helper system)
    document.getElementById('discog-submit-btn')?.addEventListener('click', (e) => {
        e.stopPropagation();
        startDiscographyDownload();
    });
}

function _esc(s) { const d = document.createElement('div'); d.textContent = s; return d.innerHTML; }

function _renderDiscogCard(release, index, completionData) {
    const comp = completionData?.albums?.find(c => c.id === release.id) || completionData?.singles?.find(c => c.id === release.id);
    const status = comp?.status || 'unknown';
    const isOwned = status === 'completed';
    const isPartial = status === 'partial' || status === 'nearly_complete';
    const year = release.release_date ? release.release_date.substring(0, 4) : '';
    const tracks = release.total_tracks || 0;
    const img = release.image_url || '';
    const checked = !isOwned;
    const statusClass = isOwned ? 'owned' : isPartial ? 'partial' : '';
    const statusIcon = isOwned ? '✓' : isPartial ? '◐' : '';

    return `
        <label class="discog-card ${statusClass}" data-type="${release._type}" style="animation-delay:${index * 0.03}s">
            <input type="checkbox" class="discog-card-cb" data-album-id="${release.id}" data-tracks="${tracks}" ${checked ? 'checked' : ''} onchange="_updateDiscogFooterCount()">
            <div class="discog-card-art">
                ${img ? `<img src="${img}" alt="" loading="lazy">` : '<div class="discog-card-art-placeholder">🎵</div>'}
                ${statusIcon ? `<span class="discog-card-status">${statusIcon}</span>` : ''}
            </div>
            <div class="discog-card-info">
                <div class="discog-card-title">${_esc(release.name)}</div>
                <div class="discog-card-meta">${year}${year && tracks ? ' · ' : ''}${tracks ? tracks + ' tracks' : ''}</div>
            </div>
            <div class="discog-card-check"></div>
        </label>
    `;
}

function toggleDiscogFilter(btn) {
    btn.classList.toggle('active');
    const type = btn.dataset.type;
    document.querySelectorAll(`.discog-card[data-type="${type}"]`).forEach(card => {
        card.style.display = btn.classList.contains('active') ? '' : 'none';
    });
    _updateDiscogFooterCount();
}

function discogSelectAll(select) {
    document.querySelectorAll('.discog-card-cb').forEach(cb => {
        if (cb.closest('.discog-card').style.display !== 'none') {
            cb.checked = select;
        }
    });
    _updateDiscogFooterCount();
}

function _updateDiscogFooterCount() {
    const checked = document.querySelectorAll('.discog-card-cb:checked');
    let releases = 0, tracks = 0;
    checked.forEach(cb => {
        if (cb.closest('.discog-card').style.display !== 'none') {
            releases++;
            tracks += parseInt(cb.dataset.tracks) || 0;
        }
    });
    const info = document.getElementById('discog-footer-info');
    const btn = document.getElementById('discog-submit-text');
    if (info) info.textContent = `${releases} release${releases !== 1 ? 's' : ''} · ${tracks} tracks`;
    if (btn) btn.textContent = releases > 0 ? `Add ${releases} to Wishlist` : 'Select releases';
    const submitBtn = document.getElementById('discog-submit-btn');
    if (submitBtn) submitBtn.disabled = releases === 0;
}

async function startDiscographyDownload() {
    let artist = artistsPageState.selectedArtist;
    // Fallback to library page state
    if (!artist && artistDetailPageState.currentArtistId) {
        artist = { id: artistDetailPageState.currentArtistId, name: artistDetailPageState.currentArtistName || 'Unknown' };
    }
    if (!artist || !artist.id) {
        showToast('No artist data available', 'error');
        return;
    }

    const checked = document.querySelectorAll('.discog-card-cb:checked');
    const albumEntries = [];
    checked.forEach(cb => {
        if (cb.closest('.discog-card').style.display !== 'none') {
            albumEntries.push({
                id: cb.dataset.albumId,
                tracks: parseInt(cb.dataset.tracks) || 0
            });
        }
    });
    // Sort by track count descending — process Deluxe/expanded editions first
    // so their tracks get added before standard editions (which then get deduped)
    albumEntries.sort((a, b) => b.tracks - a.tracks);
    const albumIds = albumEntries.map(e => e.id);

    if (albumIds.length === 0) return;

    // Switch to progress view
    const grid = document.getElementById('discog-grid');
    const progress = document.getElementById('discog-progress');
    const footer = document.getElementById('discog-footer');
    const filterBar = document.querySelector('.discog-filter-bar');

    if (grid) grid.style.display = 'none';
    if (filterBar) filterBar.style.display = 'none';
    if (progress) {
        progress.style.display = '';
        progress.innerHTML = '';
    }

    // Build progress items
    const albumMap = {};
    checked.forEach(cb => {
        if (cb.closest('.discog-card').style.display !== 'none') {
            const card = cb.closest('.discog-card');
            const id = cb.dataset.albumId;
            const title = card.querySelector('.discog-card-title')?.textContent || '';
            const img = card.querySelector('.discog-card-art img')?.src || '';
            albumMap[id] = { title, img };

            const item = document.createElement('div');
            item.className = 'discog-progress-item';
            item.id = `discog-prog-${id}`;
            item.innerHTML = `
                <div class="discog-prog-art">${img ? `<img src="${img}">` : '🎵'}</div>
                <div class="discog-prog-info">
                    <div class="discog-prog-title">${_esc(title)}</div>
                    <div class="discog-prog-status">Waiting...</div>
                </div>
                <div class="discog-prog-icon"><div class="discog-spinner"></div></div>
            `;
            progress.appendChild(item);
        }
    });

    // Update footer
    const submitBtn = document.getElementById('discog-submit-btn');
    if (submitBtn) submitBtn.style.display = 'none';
    if (footer) {
        const info = document.getElementById('discog-footer-info');
        if (info) info.textContent = 'Processing... this may take a moment';
    }

    // Mark all items as active
    document.querySelectorAll('.discog-progress-item').forEach(item => item.classList.add('active'));

    try {
        const response = await fetch(`/api/artist/${artist.id}/download-discography`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ album_ids: albumIds, artist_name: artist.name })
        });

        const reader = response.body.getReader();
        const decoder = new TextDecoder();
        let buffer = '';

        while (true) {
            const { done, value } = await reader.read();
            if (done) break;

            buffer += decoder.decode(value, { stream: true });
            const lines = buffer.split('\n');
            buffer = lines.pop(); // Keep incomplete line in buffer

            for (const line of lines) {
                if (!line.trim()) continue;
                try {
                    const data = JSON.parse(line);

                    if (data.status === 'complete') {
                        _handleDiscogProgress({ type: 'complete', total_added: data.total_added, total_skipped: data.total_skipped });
                    } else {
                        // Per-album update
                        const item = document.getElementById(`discog-prog-${data.album_id}`);
                        if (!item) continue;

                        const statusEl = item.querySelector('.discog-prog-status');
                        const iconEl = item.querySelector('.discog-prog-icon');
                        item.classList.remove('active');

                        if (data.status === 'done') {
                            const parts = [];
                            if (data.tracks_added > 0) parts.push(`${data.tracks_added} added`);
                            if (data.tracks_skipped > 0) parts.push(`${data.tracks_skipped} skipped`);
                            statusEl.textContent = parts.join(', ') || 'No new tracks';
                            iconEl.innerHTML = data.tracks_added > 0 ? '<span class="discog-check">✓</span>' : '<span class="discog-skip">—</span>';
                            item.classList.add(data.tracks_added > 0 ? 'done' : 'skipped');
                        } else if (data.status === 'error') {
                            statusEl.textContent = data.message || 'Error';
                            iconEl.innerHTML = '<span class="discog-error">✗</span>';
                            item.classList.add('error');
                        }
                    }
                } catch (e) { /* skip malformed line */ }
            }
        }
    } catch (err) {
        showToast(`Discography download failed: ${err.message}`, 'error');
    }
}

function _handleDiscogProgress(data) {
    if (data.type === 'album') {
        const item = document.getElementById(`discog-prog-${data.album_id}`);
        if (!item) return;

        const statusEl = item.querySelector('.discog-prog-status');
        const iconEl = item.querySelector('.discog-prog-icon');

        if (data.status === 'processing') {
            statusEl.textContent = `Processing ${data.tracks_total} tracks...`;
            item.classList.add('active');
        } else if (data.status === 'done') {
            const parts = [];
            if (data.tracks_added > 0) parts.push(`${data.tracks_added} added`);
            if (data.tracks_skipped > 0) parts.push(`${data.tracks_skipped} skipped`);
            statusEl.textContent = parts.join(', ') || 'No new tracks';
            iconEl.innerHTML = data.tracks_added > 0 ? '<span class="discog-check">✓</span>' : '<span class="discog-skip">—</span>';
            item.classList.remove('active');
            item.classList.add(data.tracks_added > 0 ? 'done' : 'skipped');
        } else if (data.status === 'error') {
            statusEl.textContent = data.message || 'Error';
            iconEl.innerHTML = '<span class="discog-error">✗</span>';
            item.classList.add('error');
        }
    } else if (data.type === 'complete') {
        const info = document.getElementById('discog-footer-info');
        if (info) info.textContent = `Done — ${data.total_added} tracks added, ${data.total_skipped} skipped`;

        // Show "Process Wishlist" button
        const footer = document.querySelector('.discog-footer-actions');
        if (footer && data.total_added > 0) {
            footer.innerHTML = `
                <button class="discog-cancel-btn" onclick="closeDiscographyModal()">Close</button>
                <button class="discog-submit-btn" onclick="closeDiscographyModal();fetch('/api/wishlist/process',{method:'POST'});showToast('Wishlist processing started','success')">
                    <span class="discog-submit-icon">🚀</span>
                    <span>Process Wishlist Now</span>
                </button>
            `;
        } else if (footer) {
            footer.innerHTML = '<button class="discog-cancel-btn" onclick="closeDiscographyModal()">Close</button>';
        }
    }
}

function closeDiscographyModal() {
    const overlay = document.getElementById('discog-modal-overlay');
    if (overlay) {
        overlay.classList.remove('visible');
        setTimeout(() => overlay.remove(), 300);
    }
}

// ==================== Enhanced Library Management View ====================

function isEnhancedAdmin() {
    return currentProfile && currentProfile.is_admin;
}

function toggleEnhancedView(enabled) {

    const standardSections = document.querySelector('.discography-sections');
    const enhancedContainer = document.getElementById('enhanced-view-container');
    const toggleBtns = document.querySelectorAll('.enhanced-view-toggle-btn');

    if (!standardSections || !enhancedContainer) return;

    artistDetailPageState.enhancedView = enabled;

    // Update toggle button states
    toggleBtns.forEach(btn => {
        const view = btn.getAttribute('data-view');
        btn.classList.toggle('active', (view === 'enhanced') === enabled);
    });

    // Hide/show standard filter groups (not relevant in enhanced view)
    const filterGroups = document.querySelectorAll('#discography-filters .filter-group');
    filterGroups.forEach(group => {
        const label = group.querySelector('.filter-label');
        if (label && label.textContent !== 'View') {
            group.style.display = enabled ? 'none' : '';
        }
    });
    const dividers = document.querySelectorAll('#discography-filters .filter-divider');
    dividers.forEach((d, i) => {
        if (i < dividers.length - 1) d.style.display = enabled ? 'none' : '';
    });

    if (enabled) {
        standardSections.classList.add('hidden');
        enhancedContainer.classList.remove('hidden');

        if (!artistDetailPageState.enhancedData) {
            loadEnhancedViewData(artistDetailPageState.currentArtistId);
        } else {
            renderEnhancedView();
        }
    } else {
        standardSections.classList.remove('hidden');
        enhancedContainer.classList.add('hidden');
        const bulkBar = document.getElementById('enhanced-bulk-bar');
        if (bulkBar) bulkBar.classList.remove('visible');
    }
}

async function loadEnhancedViewData(artistId) {
    const container = document.getElementById('enhanced-view-container');
    if (!container) return;

    container.innerHTML = '<div class="enhanced-loading">Loading library data...</div>';

    try {
        const response = await fetch(`/api/library/artist/${artistId}/enhanced`);
        const data = await response.json();

        if (!data.success) throw new Error(data.error || 'Failed to load enhanced data');

        artistDetailPageState.enhancedData = data;
        artistDetailPageState.expandedAlbums = new Set();
        artistDetailPageState.selectedTracks = new Set();
        artistDetailPageState.enhancedTrackSort = {};
        artistDetailPageState.serverType = data.server_type || null;
        _tagPreviewServerType = data.server_type || null;
        _rebuildAlbumMap();
        renderEnhancedView();

    } catch (error) {
        console.error('Error loading enhanced view data:', error);
        container.innerHTML = `<div class="enhanced-loading" style="color: #ff6b6b;">Failed to load: ${escapeHtml(error.message)}</div>`;
    }
}

function renderEnhancedView() {
    const container = document.getElementById('enhanced-view-container');
    const data = artistDetailPageState.enhancedData;
    if (!container || !data) return;

    container.innerHTML = '';

    // Artist metadata card (visual + editable)
    container.appendChild(renderArtistMetaPanel(data.artist));

    // Library stats summary bar
    container.appendChild(renderEnhancedStatsBar(data));

    // Group albums by type
    const grouped = { album: [], ep: [], single: [] };
    (data.albums || []).forEach(album => {
        const type = (album.record_type || 'album').toLowerCase();
        if (grouped[type]) grouped[type].push(album);
        else grouped[type] = [album];
    });

    const sectionLabels = { album: 'Albums', ep: 'EPs', single: 'Singles' };
    for (const [type, label] of Object.entries(sectionLabels)) {
        const albums = grouped[type] || [];
        if (albums.length === 0) continue;
        container.appendChild(renderEnhancedSection(type, label, albums));
    }
}

function renderEnhancedStatsBar(data) {
    const bar = document.createElement('div');
    bar.className = 'enhanced-stats-bar';

    const albums = data.albums || [];
    const totalAlbums = albums.filter(a => (a.record_type || 'album') === 'album').length;
    const totalEps = albums.filter(a => a.record_type === 'ep').length;
    const totalSingles = albums.filter(a => a.record_type === 'single').length;
    const totalTracks = albums.reduce((s, a) => s + (a.tracks ? a.tracks.length : 0), 0);

    // Calculate total duration
    let totalDurationMs = 0;
    albums.forEach(a => (a.tracks || []).forEach(t => { totalDurationMs += (t.duration || 0); }));
    const totalHours = Math.floor(totalDurationMs / 3600000);
    const totalMins = Math.floor((totalDurationMs % 3600000) / 60000);

    // Calculate format breakdown
    const formatCounts = {};
    albums.forEach(a => (a.tracks || []).forEach(t => {
        const fmt = extractFormat(t.file_path);
        if (fmt !== '-') formatCounts[fmt] = (formatCounts[fmt] || 0) + 1;
    }));

    const statsItems = [
        { value: totalAlbums, label: 'Albums', icon: '&#128191;' },
        { value: totalEps, label: 'EPs', icon: '&#128192;' },
        { value: totalSingles, label: 'Singles', icon: '&#9834;' },
        { value: totalTracks, label: 'Tracks', icon: '&#127925;' },
        { value: totalHours > 0 ? `${totalHours}h ${totalMins}m` : `${totalMins}m`, label: 'Duration', icon: '&#9202;' },
    ];

    let statsHtml = statsItems.map(s =>
        `<div class="enhanced-stat-item">
            <span class="enhanced-stat-value">${s.value}</span>
            <span class="enhanced-stat-label">${s.label}</span>
        </div>`
    ).join('');

    // Format badges
    const formatBadges = Object.entries(formatCounts)
        .sort((a, b) => b[1] - a[1])
        .map(([fmt, count]) => {
            const cls = fmt === 'FLAC' ? 'flac' : (fmt === 'MP3' ? 'mp3' : 'other');
            return `<span class="enhanced-format-badge ${cls}">${fmt} (${count})</span>`;
        }).join('');

    bar.innerHTML = `
        <div class="enhanced-stats-items">${statsHtml}</div>
        <div class="enhanced-stats-formats">${formatBadges}</div>
    `;

    return bar;
}

function renderArtistMetaPanel(artist) {
    const panel = document.createElement('div');
    panel.className = 'enhanced-artist-meta';
    panel.id = 'enhanced-artist-meta';

    // Build using DOM to avoid innerHTML escaping issues
    const header = document.createElement('div');
    header.className = 'enhanced-artist-meta-header';

    // Left side: artist image + name display
    const headerLeft = document.createElement('div');
    headerLeft.className = 'enhanced-artist-meta-header-left';

    if (artist.thumb_url) {
        const img = document.createElement('img');
        img.className = 'enhanced-artist-meta-image';
        img.src = artist.thumb_url;
        img.alt = artist.name || '';
        img.onerror = function () { this.style.display = 'none'; };
        headerLeft.appendChild(img);
    }

    const headerInfo = document.createElement('div');
    headerInfo.className = 'enhanced-artist-meta-info';
    const artistTitle = document.createElement('div');
    artistTitle.className = 'enhanced-artist-meta-name';
    artistTitle.textContent = artist.name || 'Unknown Artist';
    headerInfo.appendChild(artistTitle);

    // ID badges row (clickable links)
    const idBadges = document.createElement('div');
    idBadges.className = 'enhanced-artist-id-badges';
    const idSources = [
        { key: 'spotify_artist_id', label: 'Spotify', svc: 'spotify' },
        { key: 'musicbrainz_id', label: 'MusicBrainz', svc: 'musicbrainz' },
        { key: 'deezer_id', label: 'Deezer', svc: 'deezer' },
        { key: 'audiodb_id', label: 'AudioDB', svc: 'audiodb' },
        { key: 'discogs_id', label: 'Discogs', svc: 'discogs' },
        { key: 'itunes_artist_id', label: 'iTunes', svc: 'itunes' },
        { key: 'lastfm_url', label: 'Last.fm', svc: 'lastfm' },
        { key: 'genius_url', label: 'Genius', svc: 'genius' },
        { key: 'tidal_id', label: 'Tidal', svc: 'tidal' },
        { key: 'qobuz_id', label: 'Qobuz', svc: 'qobuz' },
    ];
    idSources.forEach(src => {
        if (artist[src.key]) {
            idBadges.appendChild(makeClickableBadge(src.svc, 'artist', artist[src.key], src.label));
        }
    });
    headerInfo.appendChild(idBadges);
    headerLeft.appendChild(headerInfo);
    header.appendChild(headerLeft);

    // Right side: admin actions
    const headerRight = document.createElement('div');
    headerRight.className = 'enhanced-artist-meta-actions';

    if (isEnhancedAdmin()) {
        const editToggle = document.createElement('button');
        editToggle.className = 'enhanced-meta-edit-toggle';
        editToggle.textContent = 'Edit Metadata';
        editToggle.onclick = () => {
            const form = document.getElementById('enhanced-artist-meta-form');
            if (form) {
                const isVisible = !form.classList.contains('hidden');
                form.classList.toggle('hidden');
                editToggle.textContent = isVisible ? 'Edit Metadata' : 'Hide Editor';
                editToggle.classList.toggle('active', !isVisible);
            }
        };
        headerRight.appendChild(editToggle);

        // Enrich dropdown button
        const enrichWrap = document.createElement('div');
        enrichWrap.className = 'enhanced-enrich-wrap';
        const enrichBtn = document.createElement('button');
        enrichBtn.className = 'enhanced-enrich-btn';
        enrichBtn.textContent = 'Enrich ▾';
        enrichBtn.onclick = (e) => {
            e.stopPropagation();
            enrichMenu.classList.toggle('visible');
        };
        enrichWrap.appendChild(enrichBtn);

        const enrichMenu = document.createElement('div');
        enrichMenu.className = 'enhanced-enrich-menu';
        const services = [
            { id: 'spotify', label: 'Spotify', icon: '🟢' },
            { id: 'musicbrainz', label: 'MusicBrainz', icon: '🟠' },
            { id: 'deezer', label: 'Deezer', icon: '🟣' },
            { id: 'discogs', label: 'Discogs', icon: '🟤' },
            { id: 'audiodb', label: 'AudioDB', icon: '🔵' },
            { id: 'itunes', label: 'iTunes', icon: '🔴' },
            { id: 'lastfm', label: 'Last.fm', icon: '⚪' },
            { id: 'genius', label: 'Genius', icon: '🟡' },
            { id: 'tidal', label: 'Tidal', icon: '⬛' },
            { id: 'qobuz', label: 'Qobuz', icon: '🔷' },
        ];
        services.forEach(svc => {
            const item = document.createElement('div');
            item.className = 'enhanced-enrich-menu-item';
            item.textContent = `${svc.icon} ${svc.label}`;
            item.onclick = (e) => {
                e.stopPropagation();
                enrichMenu.classList.remove('visible');
                runEnrichment('artist', artist.id, svc.id, artist.name, '', artist.id);
            };
            enrichMenu.appendChild(item);
        });
        enrichWrap.appendChild(enrichMenu);
        headerRight.appendChild(enrichWrap);
    }

    // Sync / Validate button
    const syncBtn = document.createElement('button');
    syncBtn.className = 'enhanced-sync-btn';
    syncBtn.innerHTML = '&#x1f504; Sync';
    syncBtn.title = 'Validate files — removes stale entries for tracks no longer on disk';
    syncBtn.onclick = async (e) => {
        e.stopPropagation();
        syncBtn.disabled = true;
        syncBtn.textContent = 'Syncing...';
        try {
            const res = await fetch(`/api/library/artist/${artist.id}/sync`, { method: 'POST' });
            const data = await res.json();
            if (data.success) {
                const parts = [];
                if (data.new_albums > 0) parts.push(`+${data.new_albums} albums`);
                if (data.new_tracks > 0) parts.push(`+${data.new_tracks} tracks`);
                if (data.stale_removed > 0) parts.push(`${data.stale_removed} stale removed`);
                if (data.empty_albums_removed > 0) parts.push(`${data.empty_albums_removed} empty albums cleaned`);
                if (data.name_updated) parts.push('name updated');
                if (parts.length === 0) parts.push('Already in sync');
                showToast(`${data.artist_name}: ${parts.join(', ')}`, 'success');
                // Refresh enhanced view if anything changed
                if (data.stale_removed > 0 || data.empty_albums_removed > 0) {
                    loadEnhancedViewData(artist.id);
                }
            } else {
                showToast(`Sync failed: ${data.error}`, 'error');
            }
        } catch (err) {
            showToast(`Sync failed: ${err.message}`, 'error');
        }
        syncBtn.disabled = false;
        syncBtn.innerHTML = '&#x1f504; Sync';
    };
    headerRight.appendChild(syncBtn);

    const reorgAllBtn = document.createElement('button');
    reorgAllBtn.className = 'enhanced-sync-btn';
    reorgAllBtn.innerHTML = '&#128193; Reorganize All';
    reorgAllBtn.title = 'Reorganize all albums for this artist using path template';
    reorgAllBtn.onclick = () => _showReorganizeAllModal();
    headerRight.appendChild(reorgAllBtn);

    header.appendChild(headerRight);

    panel.appendChild(header);

    // Match status row (clickable to rematch)
    const statusRow = document.createElement('div');
    statusRow.className = 'enhanced-match-status-row';
    const statusServices = [
        { key: 'spotify_match_status', label: 'Spotify', attempted: 'spotify_last_attempted', svc: 'spotify' },
        { key: 'musicbrainz_match_status', label: 'MusicBrainz', attempted: 'musicbrainz_last_attempted', svc: 'musicbrainz' },
        { key: 'deezer_match_status', label: 'Deezer', attempted: 'deezer_last_attempted', svc: 'deezer' },
        { key: 'audiodb_match_status', label: 'AudioDB', attempted: 'audiodb_last_attempted', svc: 'audiodb' },
        { key: 'discogs_match_status', label: 'Discogs', attempted: 'discogs_last_attempted', svc: 'discogs' },
        { key: 'itunes_match_status', label: 'iTunes', attempted: 'itunes_last_attempted', svc: 'itunes' },
        { key: 'lastfm_match_status', label: 'Last.fm', attempted: 'lastfm_last_attempted', svc: 'lastfm' },
        { key: 'genius_match_status', label: 'Genius', attempted: 'genius_last_attempted', svc: 'genius' },
        { key: 'tidal_match_status', label: 'Tidal', attempted: 'tidal_last_attempted', svc: 'tidal' },
        { key: 'qobuz_match_status', label: 'Qobuz', attempted: 'qobuz_last_attempted', svc: 'qobuz' },
    ];
    statusServices.forEach(s => {
        const status = artist[s.key];
        const attempted = artist[s.attempted];
        const chip = document.createElement('span');
        chip.className = `enhanced-match-chip clickable ${status === 'matched' ? 'matched' : (status === 'not_found' ? 'not-found' : 'pending')}`;
        chip.textContent = `${s.label}: ${status || 'pending'}`;
        const tipParts = [];
        if (attempted) tipParts.push(`Last: ${new Date(attempted).toLocaleString()}`);
        tipParts.push('Click to rematch');
        chip.title = tipParts.join(' · ');
        chip.onclick = () => openManualMatchModal('artist', artist.id, s.svc, artist.name, artist.id);
        statusRow.appendChild(chip);
    });
    panel.appendChild(statusRow);

    // Collapsible edit form (hidden by default)
    const form = document.createElement('div');
    form.className = 'enhanced-artist-meta-form hidden';
    form.id = 'enhanced-artist-meta-form';

    const editableFields = [
        { key: 'name', label: 'Artist Name', type: 'text' },
        { key: 'genres', label: 'Genres (comma separated)', type: 'text', isArray: true },
        { key: 'label', label: 'Label', type: 'text' },
        { key: 'style', label: 'Style', type: 'text' },
        { key: 'mood', label: 'Mood', type: 'text' },
        { key: 'summary', label: 'Summary / Bio', type: 'textarea', wide: true },
    ];

    const grid = document.createElement('div');
    grid.className = 'enhanced-artist-meta-grid';

    editableFields.forEach(f => {
        const fieldDiv = document.createElement('div');
        fieldDiv.className = 'enhanced-meta-field' + (f.wide ? ' wide' : '');

        const label = document.createElement('label');
        label.className = 'enhanced-meta-field-label';
        label.textContent = f.label;
        fieldDiv.appendChild(label);

        const val = f.isArray
            ? (Array.isArray(artist[f.key]) ? artist[f.key].join(', ') : (artist[f.key] || ''))
            : (artist[f.key] || '');

        if (f.type === 'textarea') {
            const ta = document.createElement('textarea');
            ta.className = 'enhanced-meta-field-input';
            ta.dataset.field = f.key;
            ta.placeholder = f.label + '...';
            ta.textContent = val;
            fieldDiv.appendChild(ta);
        } else {
            const inp = document.createElement('input');
            inp.type = 'text';
            inp.className = 'enhanced-meta-field-input';
            inp.dataset.field = f.key;
            inp.value = val;
            inp.placeholder = f.label + '...';
            fieldDiv.appendChild(inp);
        }

        grid.appendChild(fieldDiv);
    });

    form.appendChild(grid);

    // Save/revert buttons
    const formActions = document.createElement('div');
    formActions.className = 'enhanced-artist-form-actions';
    const revertBtn = document.createElement('button');
    revertBtn.className = 'enhanced-meta-cancel-btn';
    revertBtn.textContent = 'Revert';
    revertBtn.onclick = () => revertArtistMetadata();
    const saveBtn = document.createElement('button');
    saveBtn.className = 'enhanced-meta-save-btn';
    saveBtn.textContent = 'Save Changes';
    saveBtn.onclick = () => saveArtistMetadata();
    formActions.appendChild(revertBtn);
    formActions.appendChild(saveBtn);
    form.appendChild(formActions);

    panel.appendChild(form);

    return panel;
}

function renderEnhancedSection(type, label, albums) {
    const section = document.createElement('div');
    section.className = 'enhanced-section';

    const totalTracks = albums.reduce((sum, a) => sum + (a.tracks ? a.tracks.length : 0), 0);

    const sectionHeader = document.createElement('div');
    sectionHeader.className = 'enhanced-section-header';
    sectionHeader.innerHTML = `
        <span class="enhanced-section-title">${label}</span>
        <span class="enhanced-section-count">${albums.length} release${albums.length !== 1 ? 's' : ''} &middot; ${totalTracks} tracks</span>
    `;
    section.appendChild(sectionHeader);

    const grid = document.createElement('div');
    grid.className = 'enhanced-album-grid';

    albums.forEach(album => {
        const wrapper = document.createElement('div');
        wrapper.className = 'enhanced-album-wrapper';
        wrapper.id = `enhanced-album-wrapper-${album.id}`;
        const isExpanded = artistDetailPageState.expandedAlbums.has(album.id);
        if (isExpanded) wrapper.classList.add('expanded');

        wrapper.appendChild(renderAlbumRow(album, type));

        const tracksPanel = document.createElement('div');
        tracksPanel.className = 'enhanced-tracks-panel';
        tracksPanel.id = `enhanced-tracks-panel-${album.id}`;
        if (isExpanded) tracksPanel.classList.add('visible');
        const inner = document.createElement('div');
        inner.className = 'enhanced-tracks-panel-inner';
        if (isExpanded) {
            inner.dataset.rendered = 'true';
            inner.appendChild(renderExpandedAlbumHeader(album));
            inner.appendChild(renderAlbumMetaRow(album));
            inner.appendChild(renderTrackTable(album));
        }
        tracksPanel.appendChild(inner);
        wrapper.appendChild(tracksPanel);

        grid.appendChild(wrapper);
    });
    section.appendChild(grid);

    return section;
}

function renderAlbumRow(album, type) {
    const row = document.createElement('div');
    row.className = 'enhanced-album-row';
    row.id = `enhanced-album-row-${album.id}`;

    if (artistDetailPageState.expandedAlbums.has(album.id)) row.classList.add('expanded');

    const trackCount = album.tracks ? album.tracks.length : 0;
    const typeClass = (type || 'album').toLowerCase();

    // Total duration for this album
    let albumDurMs = 0;
    (album.tracks || []).forEach(t => { albumDurMs += (t.duration || 0); });
    const albumDur = formatDurationMs(albumDurMs);

    // Format breakdown for this album
    const fmts = {};
    (album.tracks || []).forEach(t => {
        const f = extractFormat(t.file_path);
        if (f !== '-') fmts[f] = (fmts[f] || 0) + 1;
    });
    const primaryFormat = Object.keys(fmts).sort((a, b) => fmts[b] - fmts[a])[0] || '';

    // Build with DOM for safety
    const expandIcon = document.createElement('span');
    expandIcon.className = 'enhanced-album-expand-icon';
    expandIcon.innerHTML = '&#9654;';
    row.appendChild(expandIcon);

    // Album art - larger, prominent
    const artWrap = document.createElement('div');
    artWrap.className = 'enhanced-album-art-wrap';
    if (album.thumb_url) {
        const img = document.createElement('img');
        img.className = 'enhanced-album-thumb';
        img.src = album.thumb_url;
        img.alt = '';
        img.loading = 'lazy';
        img.onerror = function () {
            const fallback = document.createElement('div');
            fallback.className = 'enhanced-album-thumb-fallback';
            fallback.innerHTML = '&#127925;';
            this.replaceWith(fallback);
        };
        artWrap.appendChild(img);
    } else {
        const fallback = document.createElement('div');
        fallback.className = 'enhanced-album-thumb-fallback';
        fallback.innerHTML = '&#127925;';
        artWrap.appendChild(fallback);
    }
    row.appendChild(artWrap);

    // Info block (title + meta line)
    const infoBlock = document.createElement('div');
    infoBlock.className = 'enhanced-album-info-block';

    const titleEl = document.createElement('span');
    titleEl.className = 'enhanced-album-title';
    titleEl.textContent = album.title || 'Unknown';
    titleEl.title = album.title || '';
    infoBlock.appendChild(titleEl);

    const metaLine = document.createElement('span');
    metaLine.className = 'enhanced-album-meta-line';
    const metaParts = [];
    if (album.year) metaParts.push(String(album.year));
    metaParts.push(`${trackCount} track${trackCount !== 1 ? 's' : ''}`);
    if (albumDur !== '-') metaParts.push(albumDur);
    if (album.label) metaParts.push(album.label);
    metaLine.textContent = metaParts.join(' \u00B7 ');
    infoBlock.appendChild(metaLine);

    row.appendChild(infoBlock);

    // Type badge
    const badge = document.createElement('span');
    badge.className = `enhanced-album-type-badge ${typeClass}`;
    badge.textContent = type;
    row.appendChild(badge);

    // Format badge inline
    if (primaryFormat) {
        const fmtBadge = document.createElement('span');
        const fmtClass = primaryFormat === 'FLAC' ? 'flac' : (primaryFormat === 'MP3' ? 'mp3' : 'other');
        fmtBadge.className = `enhanced-format-badge ${fmtClass}`;
        fmtBadge.textContent = primaryFormat;
        row.appendChild(fmtBadge);
    }

    row.addEventListener('click', () => toggleAlbumExpand(album.id));

    return row;
}

function toggleAlbumExpand(albumId) {
    const row = document.getElementById(`enhanced-album-row-${albumId}`);
    const panel = document.getElementById(`enhanced-tracks-panel-${albumId}`);
    const wrapper = document.getElementById(`enhanced-album-wrapper-${albumId}`);
    if (!row || !panel) return;

    const isExpanded = artistDetailPageState.expandedAlbums.has(albumId);

    if (isExpanded) {
        artistDetailPageState.expandedAlbums.delete(albumId);
        row.classList.remove('expanded');
        panel.classList.remove('visible');
        if (wrapper) wrapper.classList.remove('expanded');
    } else {
        artistDetailPageState.expandedAlbums.add(albumId);
        row.classList.add('expanded');
        panel.classList.add('visible');
        if (wrapper) wrapper.classList.add('expanded');

        // Lazy render
        const inner = panel.querySelector('.enhanced-tracks-panel-inner');
        if (inner && !inner.dataset.rendered) {
            const album = findEnhancedAlbum(albumId);
            if (album) {
                inner.innerHTML = '';
                inner.appendChild(renderExpandedAlbumHeader(album));
                inner.appendChild(renderAlbumMetaRow(album));
                inner.appendChild(renderTrackTable(album));
                inner.dataset.rendered = 'true';
            }
        }
    }
}

function findEnhancedAlbum(albumId) {
    // Use cached map for O(1) lookups instead of O(n) array scan
    if (artistDetailPageState._albumMap) {
        return artistDetailPageState._albumMap.get(String(albumId)) || null;
    }
    const data = artistDetailPageState.enhancedData;
    if (!data || !data.albums) return null;
    return data.albums.find(a => String(a.id) === String(albumId));
}

function _rebuildAlbumMap() {
    const data = artistDetailPageState.enhancedData;
    if (!data || !data.albums) { artistDetailPageState._albumMap = null; return; }
    const map = new Map();
    data.albums.forEach(a => map.set(String(a.id), a));
    artistDetailPageState._albumMap = map;
}

function renderExpandedAlbumHeader(album) {
    const header = document.createElement('div');
    header.className = 'enhanced-expanded-header';

    // Large album art
    if (album.thumb_url) {
        const img = document.createElement('img');
        img.className = 'enhanced-expanded-art';
        img.src = album.thumb_url;
        img.alt = album.title || '';
        img.onerror = function () { this.style.display = 'none'; };
        header.appendChild(img);
    }

    const info = document.createElement('div');
    info.className = 'enhanced-expanded-info';

    const title = document.createElement('div');
    title.className = 'enhanced-expanded-title';
    title.textContent = album.title || 'Unknown';
    info.appendChild(title);

    const meta = document.createElement('div');
    meta.className = 'enhanced-expanded-meta';

    const details = [];
    if (album.year) details.push(String(album.year));
    const trackCount = album.tracks ? album.tracks.length : 0;
    details.push(`${trackCount} track${trackCount !== 1 ? 's' : ''}`);
    let durMs = 0;
    (album.tracks || []).forEach(t => { durMs += (t.duration || 0); });
    if (durMs > 0) details.push(formatDurationMs(durMs));
    if (album.label) details.push(album.label);
    if (album.record_type) details.push(album.record_type.toUpperCase());

    meta.textContent = details.join(' \u00B7 ');
    info.appendChild(meta);

    // Genre tags
    const genres = Array.isArray(album.genres) ? album.genres : [];
    if (genres.length > 0) {
        const genreRow = document.createElement('div');
        genreRow.className = 'enhanced-expanded-genres';
        genres.forEach(g => {
            const tag = document.createElement('span');
            tag.className = 'enhanced-genre-tag';
            tag.textContent = g;
            genreRow.appendChild(tag);
        });
        info.appendChild(genreRow);
    }

    // External ID badges (clickable links)
    const ids = document.createElement('div');
    ids.className = 'enhanced-expanded-ids';
    const idFields = [
        { key: 'spotify_album_id', label: 'Spotify', svc: 'spotify' },
        { key: 'musicbrainz_release_id', label: 'MusicBrainz', svc: 'musicbrainz' },
        { key: 'deezer_id', label: 'Deezer', svc: 'deezer' },
        { key: 'audiodb_id', label: 'AudioDB', svc: 'audiodb' },
        { key: 'discogs_id', label: 'Discogs', svc: 'discogs' },
        { key: 'itunes_album_id', label: 'iTunes', svc: 'itunes' },
        { key: 'lastfm_url', label: 'Last.fm', svc: 'lastfm' },
    ];
    idFields.forEach(f => {
        if (album[f.key]) {
            ids.appendChild(makeClickableBadge(f.svc, 'album', album[f.key], f.label));
        }
    });
    if (ids.children.length > 0) info.appendChild(ids);

    // Resolve artist name for enrichment calls
    const artistName = artistDetailPageState.enhancedData ? artistDetailPageState.enhancedData.artist.name : '';

    // Match status chips (clickable to rematch)
    const statusRow = document.createElement('div');
    statusRow.className = 'enhanced-match-status-row compact';
    const statusSvcs = [
        { key: 'spotify_match_status', label: 'Spotify', attempted: 'spotify_last_attempted', svc: 'spotify' },
        { key: 'musicbrainz_match_status', label: 'MB', attempted: 'musicbrainz_last_attempted', svc: 'musicbrainz' },
        { key: 'deezer_match_status', label: 'Deezer', attempted: 'deezer_last_attempted', svc: 'deezer' },
        { key: 'audiodb_match_status', label: 'AudioDB', attempted: 'audiodb_last_attempted', svc: 'audiodb' },
        { key: 'discogs_match_status', label: 'Discogs', attempted: 'discogs_last_attempted', svc: 'discogs' },
        { key: 'itunes_match_status', label: 'iTunes', attempted: 'itunes_last_attempted', svc: 'itunes' },
        { key: 'lastfm_match_status', label: 'Last.fm', attempted: 'lastfm_last_attempted', svc: 'lastfm' },
    ];
    statusSvcs.forEach(s => {
        const status = album[s.key];
        const attempted = album[s.attempted];
        const chip = document.createElement('span');
        chip.className = `enhanced-match-chip clickable ${status === 'matched' ? 'matched' : (status === 'not_found' ? 'not-found' : 'pending')}`;
        chip.textContent = `${s.label}: ${status || '—'}`;
        const tipParts = [];
        if (attempted) tipParts.push(`Last: ${new Date(attempted).toLocaleString()}`);
        tipParts.push('Click to rematch');
        chip.title = tipParts.join(' · ');
        chip.onclick = (e) => {
            e.stopPropagation();
            const aId = artistDetailPageState.enhancedData ? artistDetailPageState.enhancedData.artist.id : '';
            openManualMatchModal('album', album.id, s.svc, album.title || '', aId);
        };
        statusRow.appendChild(chip);
    });
    info.appendChild(statusRow);

    // Action buttons row
    const enrichRow = document.createElement('div');
    enrichRow.className = 'enhanced-expanded-actions';

    if (isEnhancedAdmin()) {
        const albumEnrichWrap = document.createElement('div');
        albumEnrichWrap.className = 'enhanced-enrich-wrap';
        const albumEnrichBtn = document.createElement('button');
        albumEnrichBtn.className = 'enhanced-enrich-btn small';
        albumEnrichBtn.textContent = 'Enrich Album ▾';
        albumEnrichBtn.onclick = (e) => { e.stopPropagation(); albumEnrichMenu.classList.toggle('visible'); };
        albumEnrichWrap.appendChild(albumEnrichBtn);
        const albumEnrichMenu = document.createElement('div');
        albumEnrichMenu.className = 'enhanced-enrich-menu';
        [
            { id: 'spotify', label: 'Spotify', icon: '🟢' },
            { id: 'musicbrainz', label: 'MusicBrainz', icon: '🟠' },
            { id: 'deezer', label: 'Deezer', icon: '🟣' },
            { id: 'discogs', label: 'Discogs', icon: '🟤' },
            { id: 'audiodb', label: 'AudioDB', icon: '🔵' },
            { id: 'itunes', label: 'iTunes', icon: '🔴' },
            { id: 'lastfm', label: 'Last.fm', icon: '⚪' },
            { id: 'genius', label: 'Genius', icon: '🟡' },
        ].forEach(svc => {
            const item = document.createElement('div');
            item.className = 'enhanced-enrich-menu-item';
            item.textContent = `${svc.icon} ${svc.label}`;
            item.onclick = (e) => {
                e.stopPropagation();
                albumEnrichMenu.classList.remove('visible');
                const aId = artistDetailPageState.enhancedData ? artistDetailPageState.enhancedData.artist.id : '';
                runEnrichment('album', album.id, svc.id, album.title || '', artistName, aId);
            };
            albumEnrichMenu.appendChild(item);
        });
        albumEnrichWrap.appendChild(albumEnrichMenu);
        enrichRow.appendChild(albumEnrichWrap);

        const writeTagsBtn = document.createElement('button');
        writeTagsBtn.className = 'enhanced-write-tags-album-btn';
        writeTagsBtn.innerHTML = '&#9998; Write All Tags';
        writeTagsBtn.title = 'Write DB metadata to file tags for all tracks in this album';
        writeTagsBtn.onclick = (e) => { e.stopPropagation(); writeAlbumTags(album.id); };
        enrichRow.appendChild(writeTagsBtn);

        const rgAlbumBtn = document.createElement('button');
        rgAlbumBtn.className = 'enhanced-rg-album-btn';
        rgAlbumBtn.innerHTML = '&#9835; ReplayGain';
        rgAlbumBtn.title = 'Analyze ReplayGain for all tracks in this album (writes track + album gain)';
        rgAlbumBtn.dataset.albumId = album.id;
        rgAlbumBtn.onclick = (e) => { e.stopPropagation(); analyzeAlbumReplayGain(album.id, rgAlbumBtn); };
        enrichRow.appendChild(rgAlbumBtn);

        const reorganizeBtn = document.createElement('button');
        reorganizeBtn.className = 'enhanced-reorganize-album-btn';
        reorganizeBtn.innerHTML = '&#128193; Reorganize';
        reorganizeBtn.title = 'Reorganize album files using a custom path template';
        reorganizeBtn.onclick = (e) => { e.stopPropagation(); showReorganizeModal(album.id); };
        enrichRow.appendChild(reorganizeBtn);

        const redownloadBtn = document.createElement('button');
        redownloadBtn.className = 'enhanced-redownload-album-btn';
        redownloadBtn.innerHTML = '&#8635; Redownload';
        redownloadBtn.title = 'Redownload this album (opens Download Missing modal with force-download)';
        redownloadBtn.onclick = (e) => {
            e.stopPropagation();
            const aName = artistDetailPageState.enhancedData ? artistDetailPageState.enhancedData.artist.name : '';
            redownloadLibraryAlbum(album, aName, redownloadBtn);
        };
        enrichRow.appendChild(redownloadBtn);

        const deleteAlbumBtn = document.createElement('button');
        deleteAlbumBtn.className = 'enhanced-delete-album-btn';
        deleteAlbumBtn.textContent = 'Delete Album';
        deleteAlbumBtn.onclick = (e) => { e.stopPropagation(); deleteLibraryAlbum(album.id); };
        enrichRow.appendChild(deleteAlbumBtn);
    }

    // Report Issue button (available to all users)
    const reportBtn = document.createElement('button');
    reportBtn.className = 'enhanced-report-issue-btn';
    reportBtn.innerHTML = '&#9873; Report Issue';
    reportBtn.title = 'Report a problem with this album';
    reportBtn.onclick = (e) => {
        e.stopPropagation();
        const aName = artistDetailPageState.enhancedData ? artistDetailPageState.enhancedData.artist.name : '';
        showReportIssueModal('album', album.id, album.title || '', aName);
    };
    enrichRow.appendChild(reportBtn);

    info.appendChild(enrichRow);

    header.appendChild(info);
    return header;
}

function renderAlbumMetaRow(album) {
    const row = document.createElement('div');
    row.className = 'enhanced-album-meta-row';
    row.id = `enhanced-album-meta-${album.id}`;

    const fields = [
        { key: 'title', label: 'Title', value: album.title || '' },
        { key: 'year', label: 'Year', value: album.year || '', type: 'number' },
        { key: 'genres', label: 'Genres', value: Array.isArray(album.genres) ? album.genres.join(', ') : (album.genres || '') },
        { key: 'label', label: 'Label', value: album.label || '' },
        { key: 'style', label: 'Style', value: album.style || '' },
        { key: 'mood', label: 'Mood', value: album.mood || '' },
        { key: 'record_type', label: 'Type', value: album.record_type || 'album' },
        { key: 'explicit', label: 'Explicit', value: album.explicit ? '1' : '0' },
    ];

    const admin = isEnhancedAdmin();
    fields.forEach(f => {
        const fieldDiv = document.createElement('div');
        fieldDiv.className = 'enhanced-album-meta-field';
        const label = document.createElement('label');
        label.className = 'enhanced-album-meta-label';
        label.textContent = f.label;
        fieldDiv.appendChild(label);
        if (admin) {
            const input = document.createElement('input');
            input.className = 'enhanced-album-meta-input';
            input.type = f.type || 'text';
            input.dataset.albumId = album.id;
            input.dataset.field = f.key;
            input.value = String(f.value);
            input.addEventListener('click', e => e.stopPropagation());
            fieldDiv.appendChild(input);
        } else {
            const span = document.createElement('span');
            span.className = 'enhanced-album-meta-value';
            span.textContent = String(f.value) || '—';
            fieldDiv.appendChild(span);
        }
        row.appendChild(fieldDiv);
    });

    if (admin) {
        const saveDiv = document.createElement('div');
        saveDiv.className = 'enhanced-album-meta-field';
        const spacer = document.createElement('label');
        spacer.className = 'enhanced-album-meta-label';
        spacer.innerHTML = '&nbsp;';
        saveDiv.appendChild(spacer);
        const saveBtn = document.createElement('button');
        saveBtn.className = 'enhanced-album-save-btn';
        saveBtn.textContent = 'Save Album';
        saveBtn.onclick = (e) => { e.stopPropagation(); saveAlbumMetadata(album.id); };
        saveDiv.appendChild(saveBtn);
        row.appendChild(saveDiv);
    }

    return row;
}

function _buildTrackRow(track, album, admin) {
    const tr = document.createElement('tr');
    tr.dataset.trackId = track.id;
    tr.dataset.albumId = album.id;
    if (artistDetailPageState.selectedTracks.has(String(track.id))) tr.classList.add('selected');

    // Checkbox (admin only)
    if (admin) {
        const cbTd = document.createElement('td');
        const cb = document.createElement('input');
        cb.type = 'checkbox';
        cb.className = 'enhanced-track-checkbox';
        cb.checked = artistDetailPageState.selectedTracks.has(String(track.id));
        cbTd.appendChild(cb);
        tr.appendChild(cbTd);
    }

    // Play button
    const playTd = document.createElement('td');
    playTd.className = 'col-play';
    const playBtn = document.createElement('button');
    playBtn.className = 'enhanced-play-btn';
    playBtn.innerHTML = '&#9654;';
    playBtn.title = track.file_path ? 'Play track' : 'No file available';
    if (!track.file_path) playBtn.disabled = true;
    playTd.appendChild(playBtn);
    tr.appendChild(playTd);

    // Track number
    const numTd = document.createElement('td');
    numTd.className = 'col-num' + (admin ? ' editable' : '');
    numTd.textContent = track.track_number || '-';
    tr.appendChild(numTd);

    // Disc number
    const discTd = document.createElement('td');
    discTd.className = 'col-disc';
    discTd.textContent = track.disc_number || '-';
    tr.appendChild(discTd);

    // Title
    const titleTd = document.createElement('td');
    titleTd.className = 'col-title' + (admin ? ' editable' : '');
    titleTd.textContent = track.title || 'Unknown';
    tr.appendChild(titleTd);

    // Duration
    const durTd = document.createElement('td');
    durTd.className = 'col-duration';
    durTd.textContent = formatDurationMs(track.duration);
    tr.appendChild(durTd);

    // Format
    const fmtTd = document.createElement('td');
    fmtTd.className = 'col-format';
    const format = extractFormat(track.file_path);
    const fmtSpan = document.createElement('span');
    const fmtClass = format === 'FLAC' ? 'flac' : (format === 'MP3' ? 'mp3' : 'other');
    fmtSpan.className = `enhanced-format-badge ${fmtClass}`;
    fmtSpan.textContent = format;
    fmtTd.appendChild(fmtSpan);
    tr.appendChild(fmtTd);

    // Bitrate
    const brTd = document.createElement('td');
    brTd.className = 'col-bitrate';
    const brSpan = document.createElement('span');
    const brClass = (track.bitrate || 0) >= 320 ? 'high' : ((track.bitrate || 0) >= 192 ? 'medium' : 'low');
    brSpan.className = `enhanced-bitrate ${brClass}`;
    brSpan.textContent = track.bitrate ? track.bitrate + ' kbps' : '-';
    brTd.appendChild(brSpan);
    tr.appendChild(brTd);

    // BPM
    const bpmTd = document.createElement('td');
    bpmTd.className = 'col-bpm' + (admin ? ' editable' : '');
    bpmTd.textContent = track.bpm || '-';
    tr.appendChild(bpmTd);

    // File path
    const pathTd = document.createElement('td');
    pathTd.className = 'col-path';
    const filePath = track.file_path || '-';
    const fileName = filePath !== '-' ? filePath.split(/[\\/]/).pop() : '-';
    pathTd.textContent = fileName;
    pathTd.title = filePath;
    tr.appendChild(pathTd);

    // Match status chips
    const matchTd = document.createElement('td');
    matchTd.className = 'col-match';
    const matchCell = document.createElement('div');
    matchCell.className = 'enhanced-track-match-cell';
    const trackServices = [
        { svc: 'spotify', col: 'spotify_track_id', label: 'SP' },
        { svc: 'musicbrainz', col: 'musicbrainz_recording_id', label: 'MB' },
        { svc: 'deezer', col: 'deezer_id', label: 'Dz' },
        { svc: 'audiodb', col: 'audiodb_id', label: 'ADB' },
        { svc: 'itunes', col: 'itunes_track_id', label: 'iT' },
        { svc: 'lastfm', col: 'lastfm_url', label: 'LFM' },
        { svc: 'genius', col: 'genius_id', label: 'Gen' },
    ];
    trackServices.forEach(s => {
        const hasId = !!track[s.col];
        const chip = document.createElement('span');
        chip.className = 'enhanced-track-match-chip' + (hasId ? ' matched' : ' not-found');
        chip.textContent = s.label;
        chip.title = hasId ? `${s.svc}: ${track[s.col]}` : `${s.svc}: no match`;
        chip.dataset.service = s.svc;
        matchCell.appendChild(chip);
    });
    matchTd.appendChild(matchCell);
    tr.appendChild(matchTd);

    // Add to Queue button
    const queueTd = document.createElement('td');
    queueTd.className = 'col-queue';
    if (track.file_path) {
        const queueBtn = document.createElement('button');
        queueBtn.className = 'enhanced-queue-btn';
        queueBtn.innerHTML = '&#43;';
        queueBtn.title = 'Add to queue';
        queueTd.appendChild(queueBtn);
    }
    tr.appendChild(queueTd);

    if (admin) {
        // Write Tags button (admin only)
        const tagTd = document.createElement('td');
        tagTd.className = 'col-writetag';
        if (track.file_path) {
            const tagBtn = document.createElement('button');
            tagBtn.className = 'enhanced-write-tag-btn';
            tagBtn.innerHTML = '&#9998;';
            tagBtn.title = 'Write tags to file';
            tagTd.appendChild(tagBtn);

            const rgBtn = document.createElement('button');
            rgBtn.className = 'enhanced-rg-btn';
            rgBtn.textContent = 'RG';
            rgBtn.title = 'Analyze & write ReplayGain (track gain)';
            tagTd.appendChild(rgBtn);
        }
        tr.appendChild(tagTd);

        // Track actions cell — source info, redownload, delete (admin only)
        const actionsTd = document.createElement('td');
        actionsTd.className = 'col-track-actions';
        actionsTd.innerHTML = `
            <div class="enhanced-track-actions-group">
                <button class="enhanced-source-info-btn" title="View download source info">ℹ</button>
                <button class="enhanced-redownload-btn" title="Redownload this track">&#8635;</button>
                <button class="enhanced-delete-btn" title="Delete track from library">&#10005;</button>
            </div>
        `;
        tr.appendChild(actionsTd);
    } else {
        // Report Issue button per track (non-admin)
        const reportTd = document.createElement('td');
        reportTd.className = 'col-report';
        const reportBtn = document.createElement('button');
        reportBtn.className = 'enhanced-track-report-btn';
        reportBtn.innerHTML = '&#9873;';
        reportBtn.title = 'Report issue with this track';
        reportTd.appendChild(reportBtn);
        tr.appendChild(reportTd);
    }

    // Mobile actions column (visible only on mobile via CSS)
    const mobileTd = document.createElement('td');
    mobileTd.className = 'col-mobile-actions';
    const mobileBtn = document.createElement('button');
    mobileBtn.className = 'enhanced-mobile-actions-btn';
    mobileBtn.innerHTML = '⋯';
    mobileBtn.title = 'Actions';
    mobileTd.appendChild(mobileBtn);
    tr.appendChild(mobileTd);

    return tr;
}

function _getTrackDataFromRow(tr) {
    const trackId = tr.dataset.trackId;
    const albumId = tr.dataset.albumId;
    const album = findEnhancedAlbum(albumId);
    if (!album) return null;
    const track = (album.tracks || []).find(t => String(t.id) === String(trackId));
    return track ? { track, album, trackId, albumId } : null;
}

function _attachTableDelegation(table, album) {
    // Single click handler for the entire table — replaces 12-16 per-row handlers
    const admin = isEnhancedAdmin();
    table.addEventListener('click', (e) => {
        const target = e.target;
        const tr = target.closest('tr[data-track-id]');

        // Header checkbox (select all)
        if (target.closest('thead') && target.classList.contains('enhanced-track-checkbox')) {
            toggleSelectAllTracks(album.id, target.checked);
            return;
        }

        // Sort header click
        const th = target.closest('th[data-sort-field]');
        if (th) {
            cancelInlineEdit();
            const sortField = th.dataset.sortField;
            const current = artistDetailPageState.enhancedTrackSort[album.id];
            const ascending = current && current.field === sortField ? !current.ascending : true;
            artistDetailPageState.enhancedTrackSort[album.id] = { field: sortField, ascending };
            sortEnhancedTracks(album, sortField, ascending);
            _rebuildTbody(table, album);
            // Update header sort indicators
            table.querySelectorAll('th[data-sort-field]').forEach(h => {
                const sf = h.dataset.sortField;
                const baseLabel = h.dataset.label || '';
                const sort = artistDetailPageState.enhancedTrackSort[album.id];
                h.textContent = sort && sort.field === sf ? baseLabel + (sort.ascending ? ' \u25B2' : ' \u25BC') : baseLabel;
            });
            return;
        }

        if (!tr) return;
        const info = _getTrackDataFromRow(tr);
        if (!info) return;
        const { track, trackId } = info;

        // Checkbox
        if (target.classList.contains('enhanced-track-checkbox')) {
            toggleTrackSelection(String(trackId));
            return;
        }

        // Play button
        if (target.closest('.enhanced-play-btn')) {
            e.stopPropagation();
            if (track.file_path) {
                const artistName = artistDetailPageState.enhancedData ? artistDetailPageState.enhancedData.artist.name : '';
                playLibraryTrack(track, album.title || '', artistName);
            }
            return;
        }

        // Inline editable cells (admin)
        if (admin) {
            const cell = target.closest('td.editable');
            if (cell) {
                e.stopPropagation();
                if (cell.classList.contains('col-num')) {
                    startInlineEdit(cell, 'track', track.id, 'track_number', track.track_number || '');
                } else if (cell.classList.contains('col-title')) {
                    startInlineEdit(cell, 'track', track.id, 'title', track.title || '');
                } else if (cell.classList.contains('col-bpm')) {
                    startInlineEdit(cell, 'track', track.id, 'bpm', track.bpm || '');
                }
                return;
            }
        }

        // Match chip click (admin — open manual match modal)
        if (admin) {
            const chip = target.closest('.enhanced-track-match-chip');
            if (chip) {
                e.stopPropagation();
                const svc = chip.dataset.service;
                const aId = artistDetailPageState.enhancedData ? artistDetailPageState.enhancedData.artist.id : null;
                openManualMatchModal('track', track.id, svc, track.title || '', aId);
                return;
            }
        }

        // Queue button
        if (target.closest('.enhanced-queue-btn')) {
            e.stopPropagation();
            if (track.file_path) {
                const artistName = artistDetailPageState.enhancedData ? artistDetailPageState.enhancedData.artist.name : '';
                let albumArt = album.thumb_url || null;
                if (!albumArt && artistDetailPageState.enhancedData) {
                    albumArt = artistDetailPageState.enhancedData.artist?.thumb_url;
                }
                addToQueue({
                    title: track.title || 'Unknown Track',
                    artist: artistName || 'Unknown Artist',
                    album: album.title || 'Unknown Album',
                    file_path: track.file_path,
                    filename: track.file_path,
                    is_library: true,
                    image_url: albumArt,
                    id: track.id,
                    artist_id: artistDetailPageState.enhancedData ? artistDetailPageState.enhancedData.artist.id : null,
                    album_id: album.id,
                    bitrate: track.bitrate,
                    sample_rate: track.sample_rate
                });
            }
            return;
        }

        // Write tags button (admin)
        if (target.closest('.enhanced-write-tag-btn')) {
            e.stopPropagation();
            showTagPreview(track.id);
            return;
        }

        // ReplayGain analyze button (admin)
        if (target.closest('.enhanced-rg-btn')) {
            e.stopPropagation();
            analyzeTrackReplayGain(track.id, target.closest('.enhanced-rg-btn'));
            return;
        }

        // Source info button (admin)
        if (target.closest('.enhanced-source-info-btn')) {
            e.stopPropagation();
            showTrackSourceInfo(track, target.closest('.enhanced-source-info-btn'));
            return;
        }

        // Redownload button (admin)
        if (target.closest('.enhanced-redownload-btn')) {
            e.stopPropagation();
            showTrackRedownloadModal(track, album);
            return;
        }

        // Delete button (admin)
        if (target.closest('.enhanced-delete-btn')) {
            e.stopPropagation();
            deleteLibraryTrack(track.id, album.id);
            return;
        }

        // Report button (non-admin)
        if (target.closest('.enhanced-track-report-btn')) {
            e.stopPropagation();
            const artistName = artistDetailPageState.enhancedData ? artistDetailPageState.enhancedData.artist.name : '';
            showReportIssueModal('track', track.id, track.title || 'Unknown', artistName, album.title || '');
            return;
        }

        // Mobile actions button (⋯)
        if (target.closest('.enhanced-mobile-actions-btn')) {
            e.stopPropagation();
            _showMobileTrackActions(track, album);
            return;
        }
    });
}

function _showMobileTrackActions(track, album) {
    // Remove any existing popover
    document.querySelectorAll('.mobile-popover-overlay, .enhanced-mobile-actions-popover').forEach(el => el.remove());

    const overlay = document.createElement('div');
    overlay.className = 'mobile-popover-overlay';

    const popover = document.createElement('div');
    popover.className = 'enhanced-mobile-actions-popover';

    const title = document.createElement('div');
    title.className = 'popover-title';
    title.textContent = track.title || 'Track';
    popover.appendChild(title);

    const admin = isEnhancedAdmin();
    const artistName = artistDetailPageState.enhancedData ? artistDetailPageState.enhancedData.artist.name : '';
    const albumArt = album.thumb_url || (artistDetailPageState.enhancedData ? artistDetailPageState.enhancedData.artist?.thumb_url : null);

    const actions = [];
    if (track.file_path) {
        actions.push({
            icon: '▶', label: 'Play', action: () => {
                playLibraryTrack({ id: track.id, title: track.title, file_path: track.file_path, bitrate: track.bitrate, artist_id: artistDetailPageState.enhancedData?.artist?.id, album_id: album.id }, album.title || '', artistName);
            }
        });
        actions.push({
            icon: '+', label: 'Add to Queue', action: () => {
                addToQueue({ title: track.title || 'Unknown', artist: artistName, album: album.title || '', file_path: track.file_path, filename: track.file_path, is_library: true, image_url: albumArt, id: track.id, artist_id: artistDetailPageState.enhancedData?.artist?.id, album_id: album.id, bitrate: track.bitrate });
            }
        });
    }
    if (admin && track.file_path) {
        actions.push({ icon: '✎', label: 'Write Tags', action: () => showTagPreview(track.id) });
    }
    if (admin) {
        actions.push({ icon: 'ℹ', label: 'Source Info', action: () => showTrackSourceInfo(track, null) });
        actions.push({ icon: '↻', label: 'Redownload Track', action: () => showTrackRedownloadModal(track, album) });
        actions.push({ icon: '✕', label: 'Delete Track', cls: 'popover-delete', action: () => deleteLibraryTrack(track.id, album.id) });
    }

    actions.forEach(a => {
        const btn = document.createElement('button');
        if (a.cls) btn.className = a.cls;
        btn.innerHTML = `<span class="popover-icon">${a.icon}</span>${a.label}`;
        btn.addEventListener('click', () => { close(); a.action(); });
        popover.appendChild(btn);
    });

    const cancelBtn = document.createElement('button');
    cancelBtn.className = 'popover-cancel';
    cancelBtn.textContent = 'Cancel';
    cancelBtn.addEventListener('click', close);
    popover.appendChild(cancelBtn);

    function close() {
        overlay.remove();
        popover.remove();
    }
    overlay.addEventListener('click', close);

    document.body.appendChild(overlay);
    document.body.appendChild(popover);
}

function _rebuildTbody(table, album) {
    // Replace only the tbody — keeps thead and event delegation intact
    const admin = isEnhancedAdmin();
    const oldTbody = table.querySelector('tbody');
    const newTbody = document.createElement('tbody');
    (album.tracks || []).forEach(track => {
        newTbody.appendChild(_buildTrackRow(track, album, admin));
    });
    if (oldTbody) table.replaceChild(newTbody, oldTbody);
    else table.appendChild(newTbody);
}

function renderTrackTable(album) {
    const wrapper = document.createElement('div');
    const tracks = album.tracks || [];

    // Re-apply stored sort order if any
    const activeSort = artistDetailPageState.enhancedTrackSort[album.id];
    if (activeSort) {
        sortEnhancedTracks(album, activeSort.field, activeSort.ascending);
    }

    if (tracks.length === 0) {
        wrapper.innerHTML = '<div class="enhanced-no-tracks">No tracks in database</div>';
        return wrapper;
    }

    const table = document.createElement('table');
    table.className = 'enhanced-track-table';
    table.dataset.albumId = album.id;

    const admin = isEnhancedAdmin();
    // Clear stale selections for non-admin to prevent ghost state
    if (!admin) {
        artistDetailPageState.selectedTracks.clear();
    }

    // Header
    const thead = document.createElement('thead');
    const headRow = document.createElement('tr');
    if (admin) {
        const selectAllTh = document.createElement('th');
        const selectAllCb = document.createElement('input');
        selectAllCb.type = 'checkbox';
        selectAllCb.className = 'enhanced-track-checkbox';
        selectAllTh.appendChild(selectAllCb);
        headRow.appendChild(selectAllTh);
    }

    const columns = [
        { label: '', cls: 'col-play' },
        { label: '#', cls: 'col-num', sortField: 'track_number' },
        { label: 'Disc', cls: 'col-disc', sortField: 'disc_number' },
        { label: 'Title', cls: 'col-title', sortField: 'title' },
        { label: 'Duration', cls: 'col-duration', sortField: 'duration' },
        { label: 'Format', cls: 'col-format', sortField: 'format' },
        { label: 'Bitrate', cls: 'col-bitrate', sortField: 'bitrate' },
        { label: 'BPM', cls: 'col-bpm', sortField: 'bpm' },
        { label: 'File', cls: 'col-path' },
        { label: 'Match', cls: 'col-match' },
        { label: '', cls: 'col-queue' },
        ...(admin ? [
            { label: '', cls: 'col-writetag' },
            { label: '', cls: 'col-delete' },
        ] : [
            { label: '', cls: 'col-report' },
        ]),
        { label: '', cls: 'col-mobile-actions' },
    ];
    const currentSort = artistDetailPageState.enhancedTrackSort[album.id];
    columns.forEach(col => {
        const th = document.createElement('th');
        th.className = col.cls;
        if (col.sortField) {
            let headerText = col.label;
            if (currentSort && currentSort.field === col.sortField) {
                headerText += currentSort.ascending ? ' \u25B2' : ' \u25BC';
            }
            th.textContent = headerText;
            th.style.cursor = 'pointer';
            th.dataset.sortField = col.sortField;
            th.dataset.label = col.label;
        } else {
            th.textContent = col.label;
        }
        headRow.appendChild(th);
    });
    thead.appendChild(headRow);
    table.appendChild(thead);

    // Body
    const tbody = document.createElement('tbody');
    tracks.forEach(track => {
        tbody.appendChild(_buildTrackRow(track, album, admin));
    });
    table.appendChild(tbody);

    // Single delegated event listener for the whole table
    _attachTableDelegation(table, album);

    wrapper.appendChild(table);
    return wrapper;
}

function sortEnhancedTracks(album, field, ascending) {
    const tracks = album.tracks || [];
    tracks.sort((a, b) => {
        let valA, valB;
        if (field === 'format') {
            valA = extractFormat(a.file_path);
            valB = extractFormat(b.file_path);
        } else {
            valA = a[field];
            valB = b[field];
        }
        if (valA == null) return 1;
        if (valB == null) return -1;
        if (['track_number', 'disc_number', 'bpm', 'bitrate', 'duration'].includes(field)) {
            return ascending ? (Number(valA) - Number(valB)) : (Number(valB) - Number(valA));
        }
        valA = String(valA).toLowerCase();
        valB = String(valB).toLowerCase();
        return ascending ? valA.localeCompare(valB) : valB.localeCompare(valA);
    });
}

async function deleteLibraryTrack(trackId, albumId) {
    cancelInlineEdit();

    // Smart delete dialog — three options
    const choice = await _showSmartDeleteDialog();
    if (!choice) return;

    const params = new URLSearchParams();
    if (choice === 'delete_file') params.set('delete_file', 'true');

    try {
        const response = await fetch(`/api/library/track/${trackId}?${params}`, { method: 'DELETE' });
        const result = await response.json();
        if (!result.success) throw new Error(result.error);

        let msg = 'Track removed from library';
        let toastType = 'success';
        if (result.file_deleted) {
            msg = 'Track deleted from library and disk';
        } else if (result.file_error) {
            msg = 'Track removed from library but file could not be deleted';
            toastType = 'warning';
        }
        if (result.blacklisted) msg += ' (source blacklisted)';
        showToast(msg, toastType);
        if (result.file_error) {
            showToast(result.file_error, 'error', 8000);
        }

        if (artistDetailPageState.enhancedData) {
            const albums = artistDetailPageState.enhancedData.albums || [];
            const album = albums.find(a => a.id === albumId);
            if (album) {
                album.tracks = (album.tracks || []).filter(t => t.id !== trackId);
            }
        }
        artistDetailPageState.selectedTracks.delete(String(trackId));
        renderEnhancedView();
    } catch (error) {
        showToast(`Delete failed: ${error.message}`, 'error');
    }
}

function _showSmartDeleteDialog() {
    return new Promise(resolve => {
        const overlay = document.createElement('div');
        overlay.className = 'modal-overlay';
        overlay.style.cssText = 'position:fixed;inset:0;background:rgba(0,0,0,0.7);z-index:10000;display:flex;align-items:center;justify-content:center;';

        const close = (val) => { overlay.remove(); resolve(val); };
        overlay.onclick = e => { if (e.target === overlay) close(null); };

        overlay.innerHTML = `
            <div class="smart-delete-modal">
                <div class="smart-delete-header">
                    <h3>Delete Track</h3>
                    <button class="smart-delete-close">&times;</button>
                </div>
                <p class="smart-delete-desc">How should this track be deleted?</p>
                <div class="smart-delete-options">
                    <button class="smart-delete-option" data-choice="db_only">
                        <div class="smart-delete-option-icon">📋</div>
                        <div class="smart-delete-option-info">
                            <div class="smart-delete-option-title">Remove from Library</div>
                            <div class="smart-delete-option-desc">Remove the database entry only. File stays on disk.</div>
                        </div>
                    </button>
                    <button class="smart-delete-option destructive" data-choice="delete_file">
                        <div class="smart-delete-option-icon">🗑️</div>
                        <div class="smart-delete-option-info">
                            <div class="smart-delete-option-title">Delete File Too</div>
                            <div class="smart-delete-option-desc">Remove from library and delete the audio file from disk.</div>
                        </div>
                    </button>
                    <!-- Blacklisting is done from Source Info (ℹ) where real download source data is available -->
                </div>
            </div>
        `;

        overlay.querySelectorAll('.smart-delete-option').forEach(btn => {
            btn.addEventListener('click', () => close(btn.dataset.choice));
        });
        overlay.querySelector('.smart-delete-close').addEventListener('click', () => close(null));

        // Escape to close
        const escHandler = e => { if (e.key === 'Escape') { document.removeEventListener('keydown', escHandler); close(null); } };
        document.addEventListener('keydown', escHandler);

        document.body.appendChild(overlay);
    });
}

// ==================================================================================
// TRACK SOURCE INFO — View download provenance and blacklist sources
// ==================================================================================

async function showTrackSourceInfo(track, anchorEl) {
    // Remove existing popover
    const existing = document.getElementById('source-info-popover');
    if (existing) existing.remove();

    const popover = document.createElement('div');
    popover.id = 'source-info-popover';
    popover.className = 'source-info-popover';
    popover.innerHTML = '<div class="source-info-loading"><div class="server-search-spinner"></div>Loading source info...</div>';

    document.body.appendChild(popover);

    // Position near the button or center on mobile
    if (anchorEl) {
        const rect = anchorEl.getBoundingClientRect();
        const popW = 360;
        let left = rect.left - popW - 8;
        if (left < 10) left = rect.right + 8;
        let top = rect.top - 20;
        if (top + 300 > window.innerHeight) top = window.innerHeight - 310;
        popover.style.left = `${left}px`;
        popover.style.top = `${Math.max(10, top)}px`;
    } else {
        popover.style.left = '50%';
        popover.style.top = '50%';
        popover.style.transform = 'translate(-50%, -50%)';
    }

    requestAnimationFrame(() => popover.classList.add('visible'));

    // Close on outside click
    const closeHandler = e => {
        if (!popover.contains(e.target) && e.target !== anchorEl) {
            popover.remove();
            document.removeEventListener('click', closeHandler);
        }
    };
    setTimeout(() => document.addEventListener('click', closeHandler), 100);

    // Escape to close
    const escH = e => { if (e.key === 'Escape') { popover.remove(); document.removeEventListener('keydown', escH); document.removeEventListener('click', closeHandler); } };
    document.addEventListener('keydown', escH);

    try {
        const res = await fetch(`/api/library/track/${track.id}/source-info`);
        const data = await res.json();

        if (!data.success || !data.downloads || data.downloads.length === 0) {
            popover.innerHTML = `
                <div class="source-info-header">
                    <span class="source-info-title">Source Info</span>
                    <button class="source-info-close" onclick="document.getElementById('source-info-popover')?.remove()">&times;</button>
                </div>
                <div class="source-info-empty">No download source data available for this track. Source tracking starts with new downloads.</div>
            `;
            return;
        }

        const serviceIcons = { soulseek: '🔍', youtube: '▶️', tidal: '🌊', qobuz: '🎵', hifi: '🎧', deezer: '💜' };
        const serviceLabels = { soulseek: 'Soulseek', youtube: 'YouTube', tidal: 'Tidal', qobuz: 'Qobuz', hifi: 'HiFi', deezer: 'Deezer' };

        const dl = data.downloads[0]; // Most recent download
        const icon = serviceIcons[dl.source_service] || '📦';
        const label = serviceLabels[dl.source_service] || dl.source_service;
        const displayFile = dl.source_filename ? dl.source_filename.replace(/\\/g, '/').split('/').pop() : 'Unknown';
        const sizeStr = dl.source_size ? `${(dl.source_size / 1048576).toFixed(1)} MB` : '';
        const dateStr = dl.created_at ? timeAgo(dl.created_at) : '';

        popover.innerHTML = `
            <div class="source-info-header">
                <span class="source-info-title">Source Info</span>
                <button class="source-info-close" onclick="document.getElementById('source-info-popover')?.remove()">&times;</button>
            </div>
            <div class="source-info-body">
                <div class="source-info-row">
                    <span class="source-info-label">Service</span>
                    <span class="source-info-value">${icon} ${label}</span>
                </div>
                ${dl.source_service === 'soulseek' && dl.source_username ? `<div class="source-info-row">
                    <span class="source-info-label">User</span>
                    <span class="source-info-value source-info-mono">${_esc(dl.source_username)}</span>
                </div>` : ''}
                <div class="source-info-row">
                    <span class="source-info-label">Original File</span>
                    <span class="source-info-value source-info-mono source-info-ellipsis" title="${_esc(dl.source_filename || '')}">${_esc(displayFile)}</span>
                </div>
                ${sizeStr ? `<div class="source-info-row">
                    <span class="source-info-label">Size</span>
                    <span class="source-info-value">${sizeStr}</span>
                </div>` : ''}
                ${dl.audio_quality ? `<div class="source-info-row">
                    <span class="source-info-label">Quality</span>
                    <span class="source-info-value">${_esc(dl.audio_quality)}</span>
                </div>` : ''}
                ${dl.bit_depth || dl.sample_rate || dl.bitrate ? `<div class="source-info-row">
                    <span class="source-info-label">Audio</span>
                    <span class="source-info-value">${[dl.bit_depth ? `${dl.bit_depth}-bit` : '', dl.sample_rate ? `${(dl.sample_rate / 1000).toFixed(1)}kHz` : '', dl.bitrate ? `${Math.round(dl.bitrate / 1000)}kbps` : ''].filter(Boolean).join(' · ')}</span>
                </div>` : ''}
                ${dateStr ? `<div class="source-info-row">
                    <span class="source-info-label">Downloaded</span>
                    <span class="source-info-value">${dateStr}</span>
                </div>` : ''}
                ${dl.status !== 'completed' ? `<div class="source-info-row">
                    <span class="source-info-label">Status</span>
                    <span class="source-info-value" style="color:#ef5350">${dl.status}</span>
                </div>` : ''}
            </div>
            ${dl.source_username && dl.source_filename ? `
            <div class="source-info-actions">
                <button class="source-info-blacklist-btn" id="source-info-blacklist-btn">⛔ Blacklist This Source</button>
            </div>` : ''}
            ${data.downloads.length > 1 ? `<div class="source-info-history">${data.downloads.length} download records for this track</div>` : ''}
        `;

        // Blacklist button handler
        const blBtn = document.getElementById('source-info-blacklist-btn');
        if (blBtn) {
            blBtn.addEventListener('click', async () => {
                if (!await showConfirmDialog({ title: 'Blacklist Source', message: `Blacklist "${displayFile}" from ${dl.source_service === 'soulseek' ? dl.source_username : label}? This source will be skipped in future downloads.`, confirmText: 'Blacklist', destructive: true })) return;

                try {
                    const db_res = await fetch('/api/library/blacklist', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({
                            track_title: dl.track_title || track.title,
                            track_artist: dl.track_artist || '',
                            blocked_filename: dl.source_filename,
                            blocked_username: dl.source_username,
                            reason: 'user_rejected'
                        })
                    });
                    const result = await db_res.json();
                    if (result.success) {
                        showToast('Source blacklisted', 'success');
                        blBtn.disabled = true;
                        blBtn.textContent = '⛔ Blacklisted';
                    } else {
                        showToast(result.error || 'Failed to blacklist', 'error');
                    }
                } catch (e) {
                    showToast('Error: ' + e.message, 'error');
                }
            });
        }

    } catch (e) {
        popover.innerHTML = `<div class="source-info-empty">Error loading source info: ${_esc(e.message)}</div>`;
    }
}


// ==================================================================================
// TRACK REDOWNLOAD MODAL — Multi-step: metadata selection → source selection → download
// ==================================================================================

async function showTrackRedownloadModal(track, album) {
    const overlay = document.createElement('div');
    overlay.id = 'redownload-overlay';
    overlay.className = 'redownload-overlay';
    overlay.onclick = e => { if (e.target === overlay) overlay.remove(); };

    const artistName = artistDetailPageState.enhancedData?.artist?.name || '';
    const ext = (track.file_path || '').split('.').pop().toUpperCase();
    const fmt = ['FLAC', 'MP3', 'OPUS', 'OGG', 'M4A', 'WAV'].includes(ext) ? ext : '';

    overlay.innerHTML = `
        <div class="redownload-modal">
            <div class="redownload-header">
                <div>
                    <h3>Redownload Track</h3>
                    <p class="redownload-header-sub">Find the correct version and download from your preferred source</p>
                </div>
                <button class="redownload-close" onclick="document.getElementById('redownload-overlay')?.remove()">&times;</button>
            </div>
            <div class="redownload-current" id="redownload-current">
                <div class="redownload-current-art" id="redownload-current-art">
                    <div class="redownload-art-empty">🎵</div>
                </div>
                <div class="redownload-current-info">
                    <div class="redownload-current-title">${_esc(track.title)}</div>
                    <div class="redownload-current-meta">${_esc(artistName)} · ${_esc(album?.title || '')}</div>
                </div>
                <div class="redownload-current-badges">
                    ${fmt ? `<span class="redownload-badge fmt">${fmt}</span>` : ''}
                    ${track.bitrate ? `<span class="redownload-badge bitrate">${track.bitrate}k</span>` : ''}
                </div>
            </div>
            <div class="redownload-steps">
                <div class="redownload-step active" data-step="1"><span class="redownload-step-num">1</span> Choose Metadata</div>
                <div class="redownload-step-line"></div>
                <div class="redownload-step" data-step="2"><span class="redownload-step-num">2</span> Choose Source</div>
                <div class="redownload-step-line"></div>
                <div class="redownload-step" data-step="3"><span class="redownload-step-num">3</span> Downloading</div>
            </div>
            <div class="redownload-body" id="redownload-body">
                <div class="redownload-loading">
                    <div class="server-search-spinner"></div>
                    Searching metadata sources...
                </div>
            </div>
        </div>
    `;

    // Escape to close
    const escH = e => { if (e.key === 'Escape') { document.removeEventListener('keydown', escH); overlay.remove(); } };
    document.addEventListener('keydown', escH);

    document.body.appendChild(overlay);

    // Auto-search metadata
    try {
        const res = await fetch(`/api/library/track/${track.id}/redownload/search-metadata`, { method: 'POST' });
        const data = await res.json();
        if (!data.success) throw new Error(data.error);

        // Set album art in header if available
        const artEl = document.getElementById('redownload-current-art');
        if (artEl && data.current_track?.thumb_url) {
            artEl.innerHTML = `<img src="${data.current_track.thumb_url}" alt="">`;
        }

        _renderRedownloadStep1(overlay, track, data);
    } catch (e) {
        document.getElementById('redownload-body').innerHTML = `<div class="redownload-error">Error: ${_esc(e.message)}</div>`;
    }
}

function _renderRedownloadStep1(overlay, track, data) {
    const body = document.getElementById('redownload-body');
    if (!body) return;

    const sources = Object.keys(data.metadata_results);
    if (sources.length === 0) {
        body.innerHTML = '<div class="redownload-error">No metadata sources available. Check your Spotify/iTunes/Deezer connections.</div>';
        return;
    }

    const bestSource = data.best_match?.source || sources[0];
    const sourceIcons = { spotify: '🟢', itunes: '🍎', deezer: '🟣', hydrabase: '🔷' };
    const sourceLabels = { spotify: 'Spotify', itunes: 'Apple Music', deezer: 'Deezer', discogs: 'Discogs', hydrabase: 'Hydrabase' };

    // Build columns — one per source, side by side
    const columnsHtml = sources.map(source => {
        const results = data.metadata_results[source] || [];
        const icon = sourceIcons[source] || '📋';
        const label = sourceLabels[source] || source;

        let itemsHtml;
        if (results.length === 0) {
            itemsHtml = `<div class="redownload-col-empty">No results</div>`;
        } else {
            itemsHtml = results.slice(0, 8).map((r, i) => {
                const pct = Math.round((r.match_score || 0) * 100);
                const cls = pct >= 90 ? 'high' : pct >= 70 ? 'medium' : 'low';
                const dur = r.duration_ms ? `${Math.floor(r.duration_ms / 60000)}:${String(Math.floor((r.duration_ms % 60000) / 1000)).padStart(2, '0')}` : '';
                const checked = (source === bestSource && i === 0) ? 'checked' : '';
                return `
                    <label class="redownload-result" data-source="${source}" data-index="${i}">
                        <input type="radio" name="metadata-choice" value="${source}|${i}" ${checked}>
                        <div class="redownload-result-art">${r.image_url ? `<img src="${r.image_url}" loading="lazy">` : '<div class="redownload-art-empty"></div>'}</div>
                        <div class="redownload-result-info">
                            <div class="redownload-result-title">${_esc(r.name)}${r.is_current_match ? ' <span class="redownload-current-badge">current</span>' : ''}</div>
                            <div class="redownload-result-meta">${_esc(r.artist)}${r.album ? ` · ${_esc(r.album)}` : ''}</div>
                        </div>
                        <div class="redownload-result-right">
                            <div class="redownload-result-score ${cls}">${pct}%</div>
                            ${dur ? `<div class="redownload-result-dur">${dur}</div>` : ''}
                        </div>
                    </label>`;
            }).join('');
        }

        return `
            <div class="redownload-source-col">
                <div class="redownload-col-header">
                    <span class="redownload-col-icon">${icon}</span>
                    <span class="redownload-col-label">${label}</span>
                    <span class="redownload-col-count">${results.length}</span>
                </div>
                <div class="redownload-col-results">${itemsHtml}</div>
            </div>`;
    }).join('');

    body.innerHTML = `<div class="redownload-columns">${columnsHtml}</div>`;

    // Add sticky footer for Step 1
    const modal = overlay.querySelector('.redownload-modal');
    const oldFooter = modal.querySelector('.redownload-sticky-footer');
    if (oldFooter) oldFooter.remove();
    const footer = document.createElement('div');
    footer.className = 'redownload-sticky-footer';
    footer.innerHTML = `
        <div class="redownload-actions">
            <button class="redownload-btn secondary" onclick="document.getElementById('redownload-overlay')?.remove()">Cancel</button>
            <button class="redownload-btn primary" id="redownload-next-btn">Search Download Sources →</button>
        </div>
    `;
    modal.appendChild(footer);

    // Next button
    document.getElementById('redownload-next-btn').addEventListener('click', async () => {
        const checked = body.querySelector('input[name="metadata-choice"]:checked');
        if (!checked) { showToast('Select a metadata source first', 'error'); return; }
        const [source, idx] = checked.value.split('|');
        selectedMeta = data.metadata_results[source][parseInt(idx)];
        selectedMeta._source = source;

        // Update step indicator
        overlay.querySelectorAll('.redownload-step').forEach(s => s.classList.remove('active'));
        overlay.querySelector('.redownload-step[data-step="2"]').classList.add('active');

        // Stream results from all download sources — columns appear as each source responds
        // Body gets the scrollable content, footer is sticky outside the scroll
        body.innerHTML = `
            <div class="rdl-src-columns" id="rdl-src-columns">
                <div class="redownload-loading" id="rdl-src-loading"><div class="server-search-spinner"></div>Searching download sources...</div>
            </div>
        `;
        // Add sticky footer outside the scrollable body
        const existingFooter = overlay.querySelector('.redownload-sticky-footer');
        if (existingFooter) existingFooter.remove();
        const modal = overlay.querySelector('.redownload-modal');
        const footer = document.createElement('div');
        footer.className = 'redownload-sticky-footer';
        footer.innerHTML = `
            <label class="redownload-delete-old">
                <input type="checkbox" id="redownload-delete-old-check" checked>
                Delete old file after successful download
            </label>
            <div class="redownload-actions">
                <button class="redownload-btn secondary" onclick="document.getElementById('redownload-overlay')?.remove()">Cancel</button>
                <button class="redownload-btn primary" id="redownload-start-btn" disabled>Waiting for results...</button>
            </div>
        `;
        modal.appendChild(footer);

        // Wire up download button IMMEDIATELY (before streaming starts)
        // so it works as soon as results appear
        window._redownloadCandidates = [];
        window._redownloadMetadata = selectedMeta;
        document.getElementById('redownload-start-btn').addEventListener('click', async () => {
            const checked = document.querySelector('input[name="source-choice"]:checked');
            if (!checked) { showToast('Select a download source', 'error'); return; }
            const cand = window._redownloadCandidates[parseInt(checked.value)];
            if (!cand) { showToast('Invalid selection', 'error'); return; }
            const deleteOld = document.getElementById('redownload-delete-old-check')?.checked ?? true;

            overlay.querySelectorAll('.redownload-step').forEach(s => s.classList.remove('active'));
            overlay.querySelector('.redownload-step[data-step="3"]').classList.add('active');

            // Remove sticky footer for step 3
            const ft = overlay.querySelector('.redownload-sticky-footer');
            if (ft) ft.remove();

            const body = document.getElementById('redownload-body');
            body.innerHTML = `
                <div class="redownload-progress">
                    <div class="redownload-progress-title">Downloading: ${_esc(cand.display_name)}</div>
                    <div class="redownload-progress-from">from ${_esc(cand.source_service === 'soulseek' ? cand.username : (cand.source_service || 'unknown'))}</div>
                    <div class="redownload-progress-bar-wrap"><div class="redownload-progress-bar" id="redownload-progress-bar"></div></div>
                    <div class="redownload-progress-status" id="redownload-progress-status">Starting download...</div>
                </div>
            `;

            try {
                const res = await fetch(`/api/library/track/${track.id}/redownload/start`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ metadata: window._redownloadMetadata, candidate: cand, delete_old_file: deleteOld })
                });
                const startData = await res.json();
                if (!startData.success) throw new Error(startData.error);
                _pollRedownloadProgress(startData.task_id, overlay);
            } catch (e) {
                body.innerHTML = `<div class="redownload-error">Download failed: ${_esc(e.message)}</div>`;
            }
        });

        _streamRedownloadSources(overlay, track, selectedMeta);
    });
}

async function _streamRedownloadSources(overlay, track, metadata) {
    const columnsEl = document.getElementById('rdl-src-columns');
    const loadingEl = document.getElementById('rdl-src-loading');
    const startBtn = document.getElementById('redownload-start-btn');
    if (!columnsEl) return;

    const serviceIcons = { soulseek: '🔍', youtube: '▶️', tidal: '🌊', qobuz: '🎵', hifi: '🎧', deezer_dl: '💜', hybrid: '⚡' };
    const serviceLabels = { soulseek: 'Soulseek', youtube: 'YouTube', tidal: 'Tidal', qobuz: 'Qobuz', hifi: 'HiFi', deezer_dl: 'Deezer', hybrid: 'Auto' };

    let allCandidates = [];
    let firstResult = true;
    let bestGlobalIdx = -1;

    try {
        const res = await fetch(`/api/library/track/${track.id}/redownload/search-sources`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ metadata })
        });

        const reader = res.body.getReader();
        const decoder = new TextDecoder();
        let buffer = '';

        while (true) {
            const { done, value } = await reader.read();
            if (done) break;
            buffer += decoder.decode(value, { stream: true });

            const lines = buffer.split('\n');
            buffer = lines.pop(); // keep incomplete line

            for (const line of lines) {
                if (!line.trim()) continue;
                try {
                    const data = JSON.parse(line);
                    if (data.done) continue;

                    const svc = data.source;
                    const candidates = data.candidates || [];

                    // Remove loading spinner on first result
                    if (firstResult && loadingEl) { loadingEl.remove(); firstResult = false; }

                    // Assign global indices
                    const startIdx = allCandidates.length;
                    candidates.forEach((c, i) => { c._globalIdx = startIdx + i; });
                    allCandidates.push(...candidates);
                    window._redownloadCandidates = allCandidates; // Keep global ref updated for button handler

                    // Find best overall candidate
                    bestGlobalIdx = -1;
                    let bestConf = 0;
                    allCandidates.forEach((c, i) => {
                        if (!c.blacklisted && c.confidence > bestConf) { bestConf = c.confidence; bestGlobalIdx = i; }
                    });

                    // Render column for this source
                    const icon = serviceIcons[svc] || '📦';
                    const label = serviceLabels[svc] || svc;

                    const itemsHtml = candidates.length === 0
                        ? '<div class="rdl-src-col-empty">No results</div>'
                        : candidates.slice(0, 10).map(c => {
                            const confPct = Math.round((c.confidence || 0) * 100);
                            const confCls = confPct >= 90 ? 'high' : confPct >= 70 ? 'medium' : 'low';
                            const isRec = c._globalIdx === bestGlobalIdx;
                            const blClass = c.blacklisted ? ' blacklisted' : '';
                            const dur = c.duration ? `${Math.floor(c.duration / 60000)}:${String(Math.floor((c.duration % 60000) / 1000)).padStart(2, '0')}` : '';
                            return `
                                <label class="rdl-src-item${blClass}${isRec ? ' recommended' : ''}">
                                    ${c.blacklisted ? '<div class="rdl-src-radio-placeholder"></div>' : `<input type="radio" name="source-choice" value="${c._globalIdx}" ${isRec ? 'checked' : ''}>`}
                                    <div class="rdl-src-item-body">
                                        <div class="rdl-src-item-top">
                                            <div class="rdl-src-item-name" title="${_esc(c.filename)}">${_esc(c.display_name)}</div>
                                            ${isRec ? '<span class="rdl-src-recommended">Best</span>' : ''}
                                        </div>
                                        <div class="rdl-src-item-details">
                                            ${c.quality ? `<span class="rdl-src-fmt">${c.quality}</span>` : ''}
                                            ${c.bitrate ? `<span class="rdl-src-detail">${c.bitrate}k</span>` : ''}
                                            <span class="rdl-src-detail">${c.size_display}</span>
                                            ${dur ? `<span class="rdl-src-detail">${dur}</span>` : ''}
                                            ${svc === 'soulseek' ? `<span class="rdl-src-detail rdl-src-user">${_esc(c.username)}</span>` : ''}
                                            ${svc === 'soulseek' && c.free_upload_slots != null ? `<span class="rdl-src-detail">${c.free_upload_slots} slots</span>` : ''}
                                        </div>
                                        <div class="rdl-src-conf-bar"><div class="rdl-src-conf-fill ${confCls}" style="width:${confPct}%"></div></div>
                                    </div>
                                    <div class="rdl-src-conf-pct ${confCls}">${confPct}%</div>
                                    ${c.blacklisted ? '<span class="rdl-src-bl">Blacklisted</span>' : ''}
                                </label>`;
                        }).join('');

                    const colEl = document.createElement('div');
                    colEl.className = 'rdl-src-col';
                    colEl.style.animation = 'fadeSlideUp 0.3s ease both';
                    colEl.innerHTML = `
                        <div class="rdl-src-col-header">
                            <span class="rdl-src-col-icon">${icon}</span>
                            <span class="rdl-src-col-label">${label}</span>
                            <span class="rdl-src-col-count">${candidates.length}</span>
                        </div>
                        <div class="rdl-src-col-body">${itemsHtml}</div>
                    `;
                    columnsEl.appendChild(colEl);

                    // Enable the download button
                    if (startBtn && allCandidates.some(c => !c.blacklisted)) {
                        startBtn.disabled = false;
                        startBtn.textContent = 'Download Selected';
                    }

                } catch (e) { /* skip malformed lines */ }
            }
        }
    } catch (e) {
        if (loadingEl) loadingEl.innerHTML = `<div class="redownload-error">Error: ${_esc(e.message)}</div>`;
    }

    // If no results at all
    if (allCandidates.length === 0 && loadingEl) {
        loadingEl.innerHTML = '<div class="rdl-src-col-empty">No download sources found for this track.</div>';
    }

    // Update the shared candidates array (button handler reads from window._redownloadCandidates)
    window._redownloadCandidates = allCandidates;
}

/* _renderRedownloadStep2 removed — replaced by _streamRedownloadSources above */
if (false) {
    const serviceIcons = { soulseek: '🔍', youtube: '▶️', tidal: '🌊', qobuz: '🎵', hifi: '🎧', deezer_dl: '💜', hybrid: '⚡' };
    const serviceLabels = { soulseek: 'Soulseek', youtube: 'YouTube', tidal: 'Tidal', qobuz: 'Qobuz', hifi: 'HiFi', deezer_dl: 'Deezer', hybrid: 'Auto' };

    // Group candidates by source service
    const grouped = {};
    candidates.forEach((c, i) => {
        c._origIdx = i; // preserve original index for radio value
        const svc = c.source_service || 'unknown';
        if (!grouped[svc]) grouped[svc] = [];
        grouped[svc].push(c);
    });

    // Build columns — one per source
    const sourceColumnsHtml = Object.entries(grouped).map(([svc, items]) => {
        const icon = serviceIcons[svc] || '📦';
        const label = serviceLabels[svc] || svc;

        const itemsHtml = items.slice(0, 10).map(c => {
            const confPct = Math.round((c.confidence || 0) * 100);
            const confCls = confPct >= 90 ? 'high' : confPct >= 70 ? 'medium' : 'low';
            const isRecommended = c._origIdx === bestIdx && !c.blacklisted;
            const checked = isRecommended ? 'checked' : '';
            const blClass = c.blacklisted ? ' blacklisted' : '';
            const dur = c.duration ? `${Math.floor(c.duration / 60000)}:${String(Math.floor((c.duration % 60000) / 1000)).padStart(2, '0')}` : '';

            return `
                <label class="rdl-src-item${blClass}${isRecommended ? ' recommended' : ''}" data-index="${c._origIdx}">
                    ${c.blacklisted ? '<div class="rdl-src-radio-placeholder"></div>' : `<input type="radio" name="source-choice" value="${c._origIdx}" ${checked}>`}
                    <div class="rdl-src-item-body">
                        <div class="rdl-src-item-top">
                            <div class="rdl-src-item-name" title="${_esc(c.filename)}">${_esc(c.display_name)}</div>
                            ${isRecommended ? '<span class="rdl-src-recommended">Best Match</span>' : ''}
                        </div>
                        <div class="rdl-src-item-details">
                            ${c.quality ? `<span class="rdl-src-fmt">${c.quality}</span>` : ''}
                            ${c.bitrate ? `<span class="rdl-src-detail">${c.bitrate}k</span>` : ''}
                            <span class="rdl-src-detail">${c.size_display}</span>
                            ${dur ? `<span class="rdl-src-detail">${dur}</span>` : ''}
                            ${svc === 'soulseek' ? `<span class="rdl-src-detail rdl-src-user">${_esc(c.username)}</span>` : ''}
                            ${svc === 'soulseek' ? `<span class="rdl-src-detail">${c.free_upload_slots || 0} slots</span>` : ''}
                        </div>
                        <div class="rdl-src-conf-bar">
                            <div class="rdl-src-conf-fill ${confCls}" style="width:${confPct}%"></div>
                        </div>
                    </div>
                    <div class="rdl-src-conf-pct ${confCls}">${confPct}%</div>
                    ${c.blacklisted ? '<span class="rdl-src-bl">Blacklisted</span>' : ''}
                </label>`;
        }).join('');

        return `
            <div class="rdl-src-col">
                <div class="rdl-src-col-header">
                    <span class="rdl-src-col-icon">${icon}</span>
                    <span class="rdl-src-col-label">${label}</span>
                    <span class="rdl-src-col-count">${items.length}</span>
                </div>
                <div class="rdl-src-col-body">${itemsHtml}</div>
            </div>`;
    }).join('');

    body.innerHTML = `
        <div class="rdl-src-columns">${sourceColumnsHtml}</div>
        <label class="redownload-delete-old">
            <input type="checkbox" id="redownload-delete-old-check" checked>
            Delete old file after successful download
        </label>
        <div class="redownload-actions">
            <button class="redownload-btn secondary" onclick="document.getElementById('redownload-overlay')?.remove()">Cancel</button>
            <button class="redownload-btn primary" id="redownload-start-btn">Download Selected</button>
        </div>
    `;

    document.getElementById('redownload-start-btn').addEventListener('click', async () => {
        const checked = body.querySelector('input[name="source-choice"]:checked');
        if (!checked) { showToast('Select a download source', 'error'); return; }
        const candidate = candidates[parseInt(checked.value)];
        const deleteOld = document.getElementById('redownload-delete-old-check')?.checked ?? true;

        // Update step indicator
        overlay.querySelectorAll('.redownload-step').forEach(s => s.classList.remove('active'));
        overlay.querySelector('.redownload-step[data-step="3"]').classList.add('active');

        body.innerHTML = `
            <div class="redownload-progress">
                <div class="redownload-progress-title">Downloading: ${_esc(candidate.display_name)}</div>
                <div class="redownload-progress-from">from ${_esc(candidate.username)}</div>
                <div class="redownload-progress-bar-wrap"><div class="redownload-progress-bar" id="redownload-progress-bar"></div></div>
                <div class="redownload-progress-status" id="redownload-progress-status">Starting download...</div>
            </div>
        `;

        try {
            const res = await fetch(`/api/library/track/${track.id}/redownload/start`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ metadata, candidate, delete_old_file: deleteOld })
            });
            const startData = await res.json();
            if (!startData.success) throw new Error(startData.error);

            // Poll for progress
            _pollRedownloadProgress(startData.task_id, overlay);
        } catch (e) {
            body.innerHTML = `<div class="redownload-error">Download failed: ${_esc(e.message)}</div>`;
        }
    });
}

function _pollRedownloadProgress(taskId, overlay) {
    let completed = false;

    const poll = setInterval(async () => {
        if (completed) return;

        // Get fresh DOM references every tick (in case DOM was rebuilt)
        const bar = document.getElementById('redownload-progress-bar');
        const status = document.getElementById('redownload-progress-status');

        try {
            // Poll real download progress from /api/downloads/status
            const dlRes = await fetch('/api/downloads/status');
            const dlData = await dlRes.json();
            const transfers = dlData.transfers || [];

            // Find any active transfer
            let bestTransfer = null;
            for (const t of transfers) {
                const st = (t.state || '').toLowerCase();
                if (st.includes('inprogress') || st.includes('queued') || st.includes('initializing')) {
                    bestTransfer = t;
                    break;
                }
            }

            if (bestTransfer) {
                const pct = bestTransfer.percentComplete || 0;
                const transferred = bestTransfer.bytesTransferred || 0;
                const total = bestTransfer.size || 0;
                const transferredMB = (transferred / 1048576).toFixed(1);
                const totalMB = (total / 1048576).toFixed(1);

                if (bar) bar.style.width = `${Math.min(95, pct)}%`;
                if (status) {
                    status.textContent = total > 0
                        ? `Downloading... ${Math.round(pct)}% (${transferredMB} / ${totalMB} MB)`
                        : `Downloading... ${Math.round(pct)}%`;
                }
            } else {
                // No active slskd transfer — streaming source or post-processing
                if (bar) bar.style.width = '80%';
                if (status) status.textContent = 'Processing...';
            }

            // Check for batch completion
            const procRes = await fetch('/api/active-processes');
            const procData = await procRes.json();
            const procs = procData.active_processes || [];
            const ourBatch = procs.find(p => p.batch_id && p.batch_id.includes('redownload_batch_'));

            if (!ourBatch) {
                completed = true;
                clearInterval(poll);
                if (bar) bar.style.width = '100%';
                if (status) status.textContent = 'Complete! File replaced successfully.';
                showToast('Track redownloaded successfully', 'success');
                setTimeout(() => {
                    overlay.remove();
                    if (artistDetailPageState.enhancedData?.artist?.id) {
                        loadEnhancedViewData(artistDetailPageState.enhancedData.artist.id);
                    }
                }, 2000);
            }
        } catch (e) { /* ignore poll errors */ }
    }, 1500);

    // Safety timeout — 5 minutes
    setTimeout(() => {
        if (!completed) {
            clearInterval(poll);
            const status = document.getElementById('redownload-progress-status');
            if (status) status.textContent = 'Download may still be in progress. Check the dashboard.';
        }
    }, 300000);
}

async function deleteLibraryAlbum(albumId) {
    const choice = await _showAlbumDeleteDialog();
    if (!choice) return;

    const deleteFiles = choice === 'delete_files';
    const params = deleteFiles ? '?delete_files=true' : '';

    try {
        const response = await fetch(`/api/library/album/${albumId}${params}`, { method: 'DELETE' });
        const result = await response.json();
        if (!result.success) throw new Error(result.error);

        let msg = `Album removed from library (${result.tracks_deleted || 0} tracks)`;
        let toastType = 'success';
        if (deleteFiles) {
            if (result.files_deleted > 0) {
                msg = `Album deleted — ${result.files_deleted} files removed from disk`;
            }
            if (result.files_failed > 0) {
                msg += ` (${result.files_failed} files could not be deleted)`;
                toastType = 'warning';
            }
        }
        showToast(msg, toastType);

        if (artistDetailPageState.enhancedData) {
            const album = (artistDetailPageState.enhancedData.albums || []).find(a => a.id === albumId);
            if (album && album.tracks) {
                album.tracks.forEach(t => artistDetailPageState.selectedTracks.delete(String(t.id)));
            }
            artistDetailPageState.enhancedData.albums = (artistDetailPageState.enhancedData.albums || []).filter(a => a.id !== albumId);
            _rebuildAlbumMap();
        }
        artistDetailPageState.expandedAlbums.delete(albumId);
        delete artistDetailPageState.enhancedTrackSort[albumId];
        renderEnhancedView();
    } catch (error) {
        showToast(`Delete failed: ${error.message}`, 'error');
    }
}

function _showAlbumDeleteDialog() {
    return new Promise(resolve => {
        const overlay = document.createElement('div');
        overlay.className = 'modal-overlay';
        overlay.style.cssText = 'position:fixed;inset:0;background:rgba(0,0,0,0.7);z-index:10000;display:flex;align-items:center;justify-content:center;';

        const close = (val) => { overlay.remove(); resolve(val); };
        overlay.onclick = e => { if (e.target === overlay) close(null); };

        overlay.innerHTML = `
            <div class="smart-delete-modal">
                <div class="smart-delete-header">
                    <h3>Delete Album</h3>
                    <button class="smart-delete-close">&times;</button>
                </div>
                <p class="smart-delete-desc">How should this album be deleted?</p>
                <div class="smart-delete-options">
                    <button class="smart-delete-option" data-choice="db_only">
                        <div class="smart-delete-option-icon">📋</div>
                        <div class="smart-delete-option-info">
                            <div class="smart-delete-option-title">Remove from Library</div>
                            <div class="smart-delete-option-desc">Remove the album and all tracks from the database. Files on disk are not affected.</div>
                        </div>
                    </button>
                    <button class="smart-delete-option destructive" data-choice="delete_files">
                        <div class="smart-delete-option-icon">🗑️</div>
                        <div class="smart-delete-option-info">
                            <div class="smart-delete-option-title">Delete Files Too</div>
                            <div class="smart-delete-option-desc">Remove from library and delete all audio files from disk. Empty album folder will be cleaned up.</div>
                        </div>
                    </button>
                </div>
            </div>
        `;

        overlay.querySelectorAll('.smart-delete-option').forEach(btn => {
            btn.addEventListener('click', () => close(btn.dataset.choice));
        });
        overlay.querySelector('.smart-delete-close').addEventListener('click', () => close(null));

        const escHandler = e => { if (e.key === 'Escape') { document.removeEventListener('keydown', escHandler); close(null); } };
        document.addEventListener('keydown', escHandler);

        document.body.appendChild(overlay);
    });
}

function extractFormat(filePath) {
    if (!filePath) return '-';
    const ext = filePath.split('.').pop().toLowerCase();
    const formatMap = { mp3: 'MP3', flac: 'FLAC', m4a: 'AAC', ogg: 'OGG', opus: 'OPUS', wav: 'WAV', wma: 'WMA', aac: 'AAC' };
    return formatMap[ext] || ext.toUpperCase();
}

function formatDurationMs(ms) {
    if (!ms) return '-';
    const totalSeconds = Math.floor(ms / 1000);
    const minutes = Math.floor(totalSeconds / 60);
    const seconds = totalSeconds % 60;
    return `${minutes}:${seconds.toString().padStart(2, '0')}`;
}

function getServiceUrl(service, entityType, id) {
    if (!id) return null;
    const urls = {
        spotify: {
            artist: `https://open.spotify.com/artist/${id}`,
            album: `https://open.spotify.com/album/${id}`,
            track: `https://open.spotify.com/track/${id}`,
        },
        musicbrainz: {
            artist: `https://musicbrainz.org/artist/${id}`,
            album: `https://musicbrainz.org/release/${id}`,
            track: `https://musicbrainz.org/recording/${id}`,
        },
        deezer: {
            artist: `https://www.deezer.com/artist/${id}`,
            album: `https://www.deezer.com/album/${id}`,
            track: `https://www.deezer.com/track/${id}`,
        },
        audiodb: {
            artist: `https://www.theaudiodb.com/artist/${id}`,
            album: `https://www.theaudiodb.com/album/${id}`,
            track: `https://www.theaudiodb.com/track/${id}`,
        },
        itunes: {
            artist: `https://music.apple.com/artist/${id}`,
            album: `https://music.apple.com/album/${id}`,
            track: `https://music.apple.com/song/${id}`,
        },
        lastfm: {
            artist: id,  // lastfm_url is already a full URL
            album: id,
            track: id,
        },
        genius: {
            artist: id,  // genius_url is already a full URL
            track: id,   // genius_url on tracks is already a full URL
        },
        tidal: {
            artist: `https://tidal.com/browse/artist/${id}`,
            album: `https://tidal.com/browse/album/${id}`,
            track: `https://tidal.com/browse/track/${id}`,
        },
        qobuz: {
            artist: `https://www.qobuz.com/artist/${id}`,
            album: `https://www.qobuz.com/album/${id}`,
            track: `https://www.qobuz.com/track/${id}`,
        },
    };
    return urls[service] && urls[service][entityType] || null;
}

function makeClickableBadge(service, entityType, id, label) {
    const url = getServiceUrl(service, entityType, id);
    if (url) {
        const a = document.createElement('a');
        a.className = `enhanced-id-badge ${service === 'musicbrainz' ? 'mb' : service}`;
        a.href = url;
        a.target = '_blank';
        a.rel = 'noopener noreferrer';
        a.textContent = label;
        a.title = `${label}: ${id} (click to open)`;
        a.onclick = (e) => e.stopPropagation();
        return a;
    }
    const span = document.createElement('span');
    span.className = `enhanced-id-badge ${service === 'musicbrainz' ? 'mb' : service}`;
    span.textContent = label;
    span.title = `${label}: ${id}`;
    return span;
}

// ---- Inline Editing ----

function startInlineEdit(cell, type, id, field, currentValue) {
    if (cell.querySelector('.enhanced-inline-input')) return;
    cancelInlineEdit();

    const isNumeric = ['track_number', 'bpm'].includes(field);
    const originalContent = cell.innerHTML;
    cell.dataset.originalContent = originalContent;

    const input = document.createElement('input');
    input.type = isNumeric ? 'number' : 'text';
    input.className = 'enhanced-inline-input' + (isNumeric ? ' num' : '');
    input.value = currentValue || '';
    if (field === 'bpm') input.step = '0.1';
    if (field === 'track_number') { input.min = '1'; input.step = '1'; }

    cell.innerHTML = '';
    cell.appendChild(input);
    input.focus();
    input.select();

    artistDetailPageState.editingCell = { cell, type, id, field, originalContent };

    input.addEventListener('click', e => e.stopPropagation());
    input.addEventListener('keydown', (e) => {
        if (e.key === 'Enter') {
            e.preventDefault();
            saveInlineEdit(type, id, field, input.value);
        } else if (e.key === 'Escape') {
            cancelInlineEdit();
        }
        e.stopPropagation();
    });
    input.addEventListener('blur', () => {
        setTimeout(() => {
            if (artistDetailPageState.editingCell && artistDetailPageState.editingCell.cell === cell) {
                saveInlineEdit(type, id, field, input.value);
            }
        }, 150);
    });
}

async function saveInlineEdit(type, id, field, newValue) {
    const editInfo = artistDetailPageState.editingCell;
    if (!editInfo) return;
    artistDetailPageState.editingCell = null;

    let parsedValue = newValue;
    if (field === 'track_number') parsedValue = parseInt(newValue) || null;
    else if (field === 'bpm') parsedValue = parseFloat(newValue) || null;
    else if (field === 'explicit') parsedValue = parseInt(newValue) || 0;

    const url = type === 'track' ? `/api/library/track/${id}` : `/api/library/album/${id}`;

    try {
        const response = await fetch(url, {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ [field]: parsedValue })
        });
        const result = await response.json();
        if (!result.success) throw new Error(result.error);

        const displayValue = parsedValue !== null && parsedValue !== '' ? String(parsedValue) : '-';
        editInfo.cell.textContent = displayValue;
        updateLocalEnhancedData(type, id, field, parsedValue);
        showToast(`Updated ${field}`, 'success');
    } catch (error) {
        console.error('Failed to save inline edit:', error);
        editInfo.cell.innerHTML = editInfo.originalContent;
        showToast(`Failed to update: ${error.message}`, 'error');
    }
}

function cancelInlineEdit() {
    const editInfo = artistDetailPageState.editingCell;
    if (!editInfo) return;
    editInfo.cell.innerHTML = editInfo.originalContent;
    artistDetailPageState.editingCell = null;
}

function updateLocalEnhancedData(type, id, field, value) {
    const data = artistDetailPageState.enhancedData;
    if (!data) return;

    if (type === 'track') {
        for (const album of data.albums) {
            const track = (album.tracks || []).find(t => String(t.id) === String(id));
            if (track) { track[field] = value; break; }
        }
    } else if (type === 'album') {
        const album = data.albums.find(a => String(a.id) === String(id));
        if (album) album[field] = value;
    } else if (type === 'artist') {
        data.artist[field] = value;
    }
}

// ---- Track Selection & Bulk Operations ----

function toggleTrackSelection(trackId) {
    trackId = String(trackId);
    if (artistDetailPageState.selectedTracks.has(trackId)) {
        artistDetailPageState.selectedTracks.delete(trackId);
    } else {
        artistDetailPageState.selectedTracks.add(trackId);
    }
    const row = document.querySelector(`tr[data-track-id="${trackId}"]`);
    if (row) row.classList.toggle('selected', artistDetailPageState.selectedTracks.has(trackId));
    updateBulkBar();
}

function toggleSelectAllTracks(albumId, checked) {
    const album = findEnhancedAlbum(albumId);
    if (!album || !album.tracks) return;

    // Batch update state
    album.tracks.forEach(track => {
        const tid = String(track.id);
        if (checked) artistDetailPageState.selectedTracks.add(tid);
        else artistDetailPageState.selectedTracks.delete(tid);
    });

    // Scoped DOM query — only search within this album's panel, not entire document
    const panel = document.getElementById(`enhanced-tracks-panel-${albumId}`);
    if (panel) {
        panel.querySelectorAll('tr[data-track-id]').forEach(row => {
            row.classList.toggle('selected', checked);
            const cb = row.querySelector('.enhanced-track-checkbox');
            if (cb) cb.checked = checked;
        });
    }
    updateBulkBar();
}

function clearTrackSelection() {
    // Scoped batch clear — query the container once instead of per-track
    const container = document.getElementById('enhanced-view-container');
    if (container) {
        container.querySelectorAll('tr[data-track-id].selected').forEach(row => {
            row.classList.remove('selected');
            const cb = row.querySelector('.enhanced-track-checkbox');
            if (cb) cb.checked = false;
        });
        container.querySelectorAll('.enhanced-track-table thead .enhanced-track-checkbox').forEach(cb => cb.checked = false);
    }
    artistDetailPageState.selectedTracks.clear();
    updateBulkBar();
}

function updateBulkBar() {
    const bar = document.getElementById('enhanced-bulk-bar');
    const count = document.getElementById('enhanced-bulk-count');
    if (!bar || !count) return;
    if (!isEnhancedAdmin()) {
        bar.classList.remove('visible');
        return;
    }
    const n = artistDetailPageState.selectedTracks.size;
    count.textContent = n;
    bar.classList.toggle('visible', n > 0);
}

function showBulkEditModal() {
    const overlay = document.getElementById('enhanced-bulk-edit-overlay');
    const body = document.getElementById('enhanced-bulk-modal-body');
    const title = document.getElementById('enhanced-bulk-modal-title');
    if (!overlay || !body) return;

    const count = artistDetailPageState.selectedTracks.size;
    title.textContent = `Batch Edit ${count} Track${count !== 1 ? 's' : ''}`;

    body.innerHTML = `
        <div class="enhanced-bulk-modal-field">
            <label>Track Number (leave blank to skip)</label>
            <input type="number" id="bulk-edit-track-number" placeholder="Track number..." min="1">
        </div>
        <div class="enhanced-bulk-modal-field">
            <label>BPM (leave blank to skip)</label>
            <input type="number" id="bulk-edit-bpm" placeholder="BPM..." step="0.1">
        </div>
        <div class="enhanced-bulk-modal-field">
            <label>Style (leave blank to skip)</label>
            <input type="text" id="bulk-edit-style" placeholder="Style...">
        </div>
        <div class="enhanced-bulk-modal-field">
            <label>Mood (leave blank to skip)</label>
            <input type="text" id="bulk-edit-mood" placeholder="Mood...">
        </div>
        <div class="enhanced-bulk-modal-field">
            <label>Explicit</label>
            <select id="bulk-edit-explicit">
                <option value="">-- No change --</option>
                <option value="0">No</option>
                <option value="1">Yes</option>
            </select>
        </div>
    `;

    overlay.classList.remove('hidden');
}

function closeBulkEditModal() {
    const overlay = document.getElementById('enhanced-bulk-edit-overlay');
    if (overlay) overlay.classList.add('hidden');
}

async function executeBulkEdit() {
    const trackIds = Array.from(artistDetailPageState.selectedTracks);
    if (trackIds.length === 0) return;

    const updates = {};
    const trackNum = document.getElementById('bulk-edit-track-number');
    const bpm = document.getElementById('bulk-edit-bpm');
    const style = document.getElementById('bulk-edit-style');
    const mood = document.getElementById('bulk-edit-mood');
    const explicit = document.getElementById('bulk-edit-explicit');

    if (trackNum && trackNum.value !== '') updates.track_number = parseInt(trackNum.value);
    if (bpm && bpm.value !== '') updates.bpm = parseFloat(bpm.value);
    if (style && style.value !== '') updates.style = style.value;
    if (mood && mood.value !== '') updates.mood = mood.value;
    if (explicit && explicit.value !== '') updates.explicit = parseInt(explicit.value);

    if (Object.keys(updates).length === 0) {
        showToast('No changes to apply', 'error');
        return;
    }

    try {
        const response = await fetch('/api/library/tracks/batch', {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ track_ids: trackIds, updates })
        });
        const result = await response.json();
        if (!result.success) throw new Error(result.error);

        showToast(`Updated ${result.updated_count} tracks`, 'success');
        closeBulkEditModal();

        for (const [field, val] of Object.entries(updates)) {
            trackIds.forEach(tid => updateLocalEnhancedData('track', tid, field, val));
        }

        reRenderExpandedPanels();
        clearTrackSelection();

    } catch (error) {
        console.error('Bulk edit failed:', error);
        showToast(`Bulk edit failed: ${error.message}`, 'error');
    }
}

// ---- Save Artist / Album Metadata ----

async function saveArtistMetadata() {
    const form = document.getElementById('enhanced-artist-meta-form');
    if (!form) return;

    const inputs = form.querySelectorAll('.enhanced-meta-field-input');
    const updates = {};
    const original = artistDetailPageState.enhancedData.artist;

    inputs.forEach(input => {
        const field = input.dataset.field;
        if (!field) return;
        let value = (input.tagName === 'TEXTAREA' ? input.value : input.value).trim();

        let origVal = original[field];
        if (field === 'genres') {
            const newGenres = value ? value.split(',').map(g => g.trim()).filter(Boolean) : [];
            const origGenres = Array.isArray(origVal) ? origVal : [];
            if (JSON.stringify(newGenres) !== JSON.stringify(origGenres)) updates[field] = newGenres;
        } else {
            if ((value || '') !== (origVal || '')) updates[field] = value || null;
        }
    });

    if (Object.keys(updates).length === 0) {
        showToast('No changes to save', 'error');
        return;
    }

    try {
        const response = await fetch(`/api/library/artist/${original.id}`, {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(updates)
        });
        const result = await response.json();
        if (!result.success) throw new Error(result.error);

        for (const [field, value] of Object.entries(updates)) {
            artistDetailPageState.enhancedData.artist[field] = value;
        }

        // Update the display name in the header
        if (updates.name) {
            const nameEl = document.querySelector('.enhanced-artist-meta-name');
            if (nameEl) nameEl.textContent = updates.name;
        }

        showToast(`Artist metadata saved (${(result.updated_fields || []).join(', ')})`, 'success');
    } catch (error) {
        console.error('Failed to save artist metadata:', error);
        showToast(`Failed to save: ${error.message}`, 'error');
    }
}

function revertArtistMetadata() {
    const data = artistDetailPageState.enhancedData;
    if (!data) return;

    const panel = document.getElementById('enhanced-artist-meta');
    if (!panel) return;

    const parent = panel.parentNode;
    const newPanel = renderArtistMetaPanel(data.artist);
    parent.replaceChild(newPanel, panel);
    showToast('Reverted to saved values', 'success');
}

async function saveAlbumMetadata(albumId) {
    const metaRow = document.getElementById(`enhanced-album-meta-${albumId}`);
    if (!metaRow) return;

    const album = findEnhancedAlbum(albumId);
    if (!album) return;

    const inputs = metaRow.querySelectorAll('.enhanced-album-meta-input');
    const updates = {};

    inputs.forEach(input => {
        const field = input.dataset.field;
        if (!field) return;
        let value = input.value.trim();

        if (field === 'genres') {
            const newGenres = value ? value.split(',').map(g => g.trim()).filter(Boolean) : [];
            const origGenres = Array.isArray(album.genres) ? album.genres : [];
            if (JSON.stringify(newGenres) !== JSON.stringify(origGenres)) updates[field] = newGenres;
        } else if (field === 'year' || field === 'explicit' || field === 'track_count') {
            const numVal = value !== '' ? parseInt(value) : null;
            if (numVal !== (album[field] || null)) updates[field] = numVal;
        } else {
            if ((value || '') !== (album[field] || '')) updates[field] = value || null;
        }
    });

    if (Object.keys(updates).length === 0) {
        showToast('No album changes to save', 'error');
        return;
    }

    try {
        const response = await fetch(`/api/library/album/${albumId}`, {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(updates)
        });
        const result = await response.json();
        if (!result.success) throw new Error(result.error);

        for (const [field, value] of Object.entries(updates)) {
            album[field] = value;
        }

        // Update album row display
        const albumRow = document.getElementById(`enhanced-album-row-${albumId}`);
        if (albumRow) {
            if (updates.title) {
                const titleEl = albumRow.querySelector('.enhanced-album-title');
                if (titleEl) { titleEl.textContent = updates.title; titleEl.title = updates.title; }
            }
            if (updates.year !== undefined) {
                const yearEl = albumRow.querySelector('.enhanced-album-year');
                if (yearEl) yearEl.textContent = updates.year || '-';
            }
        }

        showToast(`Album metadata saved (${(result.updated_fields || []).join(', ')})`, 'success');
    } catch (error) {
        console.error('Failed to save album metadata:', error);
        showToast(`Failed to save: ${error.message}`, 'error');
    }
}

function reRenderExpandedPanels() {
    artistDetailPageState.expandedAlbums.forEach(albumId => {
        const panel = document.getElementById(`enhanced-tracks-panel-${albumId}`);
        if (!panel) return;
        const inner = panel.querySelector('.enhanced-tracks-panel-inner');
        if (!inner) return;

        const album = findEnhancedAlbum(albumId);
        if (album) {
            inner.innerHTML = '';
            inner.appendChild(renderExpandedAlbumHeader(album));
            inner.appendChild(renderAlbumMetaRow(album));
            inner.appendChild(renderTrackTable(album));
        }
    });
}

// ---- Manual Match Modal ----

function openManualMatchModal(entityType, entityId, service, defaultQuery, artistId) {
    // Remove existing modal if any
    const existing = document.getElementById('enhanced-manual-match-overlay');
    if (existing) existing.remove();

    const serviceLabels = {
        spotify: 'Spotify', musicbrainz: 'MusicBrainz', deezer: 'Deezer',
        audiodb: 'AudioDB', itunes: 'iTunes', lastfm: 'Last.fm', genius: 'Genius'
    };

    const overlay = document.createElement('div');
    overlay.id = 'enhanced-manual-match-overlay';
    overlay.className = 'modal-overlay';
    overlay.onclick = (e) => { if (e.target === overlay) overlay.remove(); };

    const modal = document.createElement('div');
    modal.className = 'enhanced-manual-match-modal';

    // Header
    const header = document.createElement('div');
    header.className = 'enhanced-bulk-modal-header';
    const title = document.createElement('h3');
    title.textContent = `Match ${entityType} on ${serviceLabels[service] || service}`;
    header.appendChild(title);
    const closeBtn = document.createElement('button');
    closeBtn.className = 'enhanced-bulk-modal-close';
    closeBtn.innerHTML = '&times;';
    closeBtn.onclick = () => overlay.remove();
    header.appendChild(closeBtn);
    modal.appendChild(header);

    // Search bar
    const searchRow = document.createElement('div');
    searchRow.className = 'enhanced-match-search-row';
    const searchInput = document.createElement('input');
    searchInput.type = 'text';
    searchInput.className = 'enhanced-match-search-input';
    searchInput.placeholder = `Search ${serviceLabels[service] || service}...`;
    searchInput.value = defaultQuery;
    searchRow.appendChild(searchInput);
    const searchBtn = document.createElement('button');
    searchBtn.className = 'enhanced-enrich-btn';
    searchBtn.textContent = 'Search';
    searchBtn.onclick = () => doManualMatchSearch(service, entityType, searchInput.value, resultsContainer, entityId, artistId);
    searchRow.appendChild(searchBtn);

    // Clear Match button — lets user revert a wrong match to not_found
    const clearBtn = document.createElement('button');
    clearBtn.className = 'enhanced-enrich-btn';
    clearBtn.style.cssText = 'background:rgba(255,80,80,0.12);color:#ff6b6b;margin-left:6px';
    clearBtn.textContent = 'Clear Match';
    clearBtn.title = 'Remove the current match — reverts to Not Found';
    clearBtn.onclick = async () => {
        if (!confirm(`Clear ${serviceLabels[service] || service} match for this ${entityType}? It will revert to "Not Found".`)) return;
        try {
            const res = await fetch('/api/library/clear-match', {
                method: 'PUT',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ entity_type: entityType, entity_id: entityId, service, artist_id: artistId })
            });
            const data = await res.json();
            if (data.success) {
                showToast(`Cleared ${serviceLabels[service] || service} match`, 'success');
                overlay.remove();
                if (data.updated_data) {
                    artistDetailPageState.enhancedData = data.updated_data;
                    renderEnhancedArtistView(data.updated_data, true);
                }
            } else {
                showToast(data.error || 'Failed to clear match', 'error');
            }
        } catch (e) {
            showToast('Error clearing match', 'error');
        }
    };
    searchRow.appendChild(clearBtn);

    modal.appendChild(searchRow);

    // Handle Enter key
    searchInput.onkeydown = (e) => {
        if (e.key === 'Enter') searchBtn.click();
    };

    // Results container
    const resultsContainer = document.createElement('div');
    resultsContainer.className = 'enhanced-match-results';
    resultsContainer.innerHTML = '<div class="enhanced-match-results-hint">Press Search or Enter to find matches</div>';
    modal.appendChild(resultsContainer);

    overlay.appendChild(modal);
    document.body.appendChild(overlay);

    // Auto-search on open
    searchInput.focus();
    searchBtn.click();
}

async function doManualMatchSearch(service, entityType, query, container, entityId, artistId) {
    if (!query.trim()) {
        container.innerHTML = '<div class="enhanced-match-results-hint">Enter a search term</div>';
        return;
    }

    container.innerHTML = '<div class="enhanced-loading">Searching...</div>';

    try {
        const response = await fetch('/api/library/search-service', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ service, entity_type: entityType, query: query.trim() })
        });

        const data = await response.json();
        if (!data.success) throw new Error(data.error);

        const results = data.results || [];
        container.innerHTML = '';

        if (results.length === 0) {
            container.innerHTML = '<div class="enhanced-match-results-hint">No results found. Try a different search.</div>';
            return;
        }

        results.forEach(result => {
            const row = document.createElement('div');
            row.className = 'enhanced-match-result-row';

            if (result.image) {
                const img = document.createElement('img');
                img.className = 'enhanced-match-result-img';
                img.src = result.image;
                img.alt = '';
                img.onerror = function () { this.style.display = 'none'; };
                row.appendChild(img);
            } else {
                const placeholder = document.createElement('div');
                placeholder.className = 'enhanced-match-result-img-placeholder';
                placeholder.innerHTML = '&#127925;';
                row.appendChild(placeholder);
            }

            const info = document.createElement('div');
            info.className = 'enhanced-match-result-info';
            const name = document.createElement('div');
            name.className = 'enhanced-match-result-name';
            name.textContent = result.name || 'Unknown';
            info.appendChild(name);
            if (result.extra) {
                const extra = document.createElement('div');
                extra.className = 'enhanced-match-result-extra';
                extra.textContent = result.extra;
                info.appendChild(extra);
            }
            const idLine = document.createElement('div');
            idLine.className = 'enhanced-match-result-id';
            const providerLabel = result.provider && result.provider !== service ? ` (${result.provider})` : '';
            idLine.textContent = `ID: ${result.id}${providerLabel}`;
            info.appendChild(idLine);
            row.appendChild(info);

            const matchBtn = document.createElement('button');
            matchBtn.className = 'enhanced-meta-save-btn';
            matchBtn.textContent = 'Match';
            matchBtn.onclick = () => applyManualMatch(entityType, entityId, result.provider || service, result.id, artistId);
            row.appendChild(matchBtn);

            container.appendChild(row);
        });

    } catch (error) {
        container.innerHTML = `<div class="enhanced-match-results-hint" style="color:#ff6b6b;">Error: ${escapeHtml(error.message)}</div>`;
    }
}

async function applyManualMatch(entityType, entityId, service, serviceId, artistId) {
    try {
        showToast(`Matching ${entityType} to ${service}...`, 'info');

        const response = await fetch('/api/library/manual-match', {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                entity_type: entityType,
                entity_id: entityId,
                service: service,
                service_id: serviceId,
                artist_id: artistId
            })
        });

        const result = await response.json();
        if (!result.success) throw new Error(result.error);

        showToast(`Manually matched to ${service} ID: ${serviceId}`, 'success');

        // Close modal
        const overlay = document.getElementById('enhanced-manual-match-overlay');
        if (overlay) overlay.remove();

        // Update view with fresh data
        if (result.updated_data && result.updated_data.success) {
            artistDetailPageState.enhancedData = result.updated_data;
            _rebuildAlbumMap();
            renderEnhancedView();
        } else if (artistDetailPageState.currentArtistId) {
            await loadEnhancedViewData(artistDetailPageState.currentArtistId);
        }

    } catch (error) {
        showToast(`Match failed: ${error.message}`, 'error');
    }
}

// ---- Enrichment ----

let _enrichmentInFlight = false;

async function runEnrichment(entityType, entityId, service, name, artistName, artistId) {
    if (_enrichmentInFlight) {
        showToast('An enrichment is already in progress', 'error');
        return;
    }

    _enrichmentInFlight = true;

    // Add loading class to all match chips for this service
    const chipPrefixes = {
        'spotify': ['spotify', 'sp'],
        'musicbrainz': ['musicbrainz', 'mb'],
        'deezer': ['deezer', 'dz'],
        'audiodb': ['audiodb', 'adb'],
        'itunes': ['itunes', 'it'],
        'lastfm': ['last.fm', 'lfm'],
        'genius': ['genius', 'gen'],
    };
    const prefixes = chipPrefixes[service] || [service];
    document.querySelectorAll('.enhanced-match-chip').forEach(chip => {
        const chipText = chip.textContent.toLowerCase();
        if (prefixes.some(p => chipText.startsWith(p))) {
            chip.classList.add('loading');
        }
    });

    showToast(`Enriching ${entityType} from ${service}...`, 'info');

    try {
        const response = await fetch('/api/library/enrich', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                entity_type: entityType,
                entity_id: entityId,
                service: service,
                name: name,
                artist_name: artistName,
                artist_id: artistId
            })
        });

        const result = await response.json();

        if (response.status === 429) {
            showToast(result.error || 'Another enrichment is in progress', 'error');
            return;
        }

        if (!result.success) {
            throw new Error(result.error || 'Enrichment failed');
        }

        // Show per-service results
        const results = result.results || {};
        const successes = Object.entries(results).filter(([, r]) => r.success).map(([s]) => s);
        const failures = Object.entries(results).filter(([, r]) => !r.success).map(([s, r]) => `${s}: ${r.error}`);

        if (successes.length > 0) {
            showToast(`Enriched from: ${successes.join(', ')}`, 'success');
        }
        if (failures.length > 0) {
            showToast(`Failed: ${failures.join('; ')}`, 'error');
        }

        // Update local data with fresh response and re-render (preserves expanded state)
        if (result.updated_data && result.updated_data.success) {
            artistDetailPageState.enhancedData = result.updated_data;
            _rebuildAlbumMap();
            renderEnhancedView();
        } else if (artistDetailPageState.currentArtistId) {
            await loadEnhancedViewData(artistDetailPageState.currentArtistId);
        }

    } catch (error) {
        console.error('Enrichment error:', error);
        showToast(`Enrichment error: ${error.message}`, 'error');
    } finally {
        _enrichmentInFlight = false;
        document.querySelectorAll('.enhanced-match-chip.loading').forEach(c => c.classList.remove('loading'));
    }
}

// Close enrich dropdowns when clicking outside (early bail when enhanced view isn't active)
document.addEventListener('click', (e) => {
    if (!artistDetailPageState.enhancedView) return;
    if (!e.target.closest('.enhanced-enrich-wrap')) {
        document.querySelectorAll('.enhanced-enrich-menu.visible').forEach(m => m.classList.remove('visible'));
    }
});

// ---- Write Tags to File ----

let _tagPreviewTrackId = null;
let _tagPreviewServerType = null;

async function showTagPreview(trackId) {
    _tagPreviewTrackId = trackId;
    _tagPreviewServerType = null;
    const overlay = document.getElementById('tag-preview-overlay');
    const body = document.getElementById('tag-preview-body');
    const title = document.getElementById('tag-preview-title');
    if (!overlay || !body) return;

    title.textContent = 'Write Tags to File';
    body.innerHTML = '<div class="tag-preview-loading">Loading tag comparison...</div>';
    overlay.classList.remove('hidden');

    // Hide sync checkbox until we know server type
    const syncLabel = document.getElementById('tag-preview-sync-label');
    if (syncLabel) syncLabel.classList.add('hidden');

    try {
        const response = await fetch(`/api/library/track/${trackId}/tag-preview`);
        const result = await response.json();
        if (!result.success) {
            body.innerHTML = `<div class="tag-preview-error">${escapeHtml(result.error)}</div>`;
            return;
        }

        const diff = result.diff || [];
        const hasChanges = result.has_changes;

        // Show server sync checkbox if a server is connected (not navidrome — it auto-detects)
        _tagPreviewServerType = result.server_type || null;
        if (syncLabel && _tagPreviewServerType && _tagPreviewServerType !== 'navidrome') {
            const syncText = document.getElementById('tag-preview-sync-text');
            if (syncText) syncText.textContent = `Sync to ${_tagPreviewServerType === 'plex' ? 'Plex' : 'Jellyfin'}`;
            syncLabel.classList.remove('hidden');
        }

        let html = '<table class="tag-preview-table"><thead><tr>';
        html += '<th>Field</th><th>Current File Tag</th><th></th><th>DB Value</th>';
        html += '</tr></thead><tbody>';

        diff.forEach(d => {
            const rowClass = d.changed ? 'tag-diff-changed' : 'tag-diff-same';
            const arrow = d.changed ? '<span class="tag-diff-arrow">&rarr;</span>' : '<span class="tag-diff-check">&#10003;</span>';
            html += `<tr class="${rowClass}">`;
            html += `<td class="tag-field-name">${d.field}</td>`;
            html += `<td class="tag-file-value">${escapeHtml(d.file_value) || '<span class="tag-empty">empty</span>'}</td>`;
            html += `<td class="tag-diff-indicator">${arrow}</td>`;
            html += `<td class="tag-db-value">${escapeHtml(d.db_value) || '<span class="tag-empty">empty</span>'}</td>`;
            html += '</tr>';
        });

        html += '</tbody></table>';

        if (!hasChanges) {
            html += '<div class="tag-preview-no-changes">File tags already match DB metadata</div>';
        }

        body.innerHTML = html;

        const writeBtn = document.getElementById('tag-preview-write-btn');
        if (writeBtn) {
            writeBtn.disabled = !hasChanges && !document.getElementById('tag-preview-embed-cover')?.checked;
        }

    } catch (error) {
        body.innerHTML = `<div class="tag-preview-error">Failed to load preview: ${escapeHtml(error.message)}</div>`;
    }
}

function closeTagPreviewModal() {
    const overlay = document.getElementById('tag-preview-overlay');
    if (overlay) overlay.classList.add('hidden');
    _tagPreviewTrackId = null;
}

async function executeWriteTags() {
    if (!_tagPreviewTrackId) return;

    const writeBtn = document.getElementById('tag-preview-write-btn');
    if (writeBtn) {
        writeBtn.disabled = true;
        writeBtn.textContent = 'Writing...';
    }

    const embedCover = document.getElementById('tag-preview-embed-cover')?.checked ?? true;
    const syncToServer = document.getElementById('tag-preview-sync-server')?.checked && _tagPreviewServerType && _tagPreviewServerType !== 'navidrome';

    try {
        const response = await fetch(`/api/library/track/${_tagPreviewTrackId}/write-tags`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ embed_cover: embedCover, sync_to_server: syncToServer })
        });
        const result = await response.json();
        if (!result.success) throw new Error(result.error);

        const fieldCount = (result.written_fields || []).length;
        let msg = `Tags written successfully (${fieldCount} fields)`;
        if (result.server_sync) {
            const ss = result.server_sync;
            if (ss.synced > 0) msg += ` — synced to ${_tagPreviewServerType === 'plex' ? 'Plex' : 'Jellyfin'}`;
            else if (ss.failed > 0) msg += ` — server sync failed`;
        }
        showToast(msg, 'success');
        closeTagPreviewModal();

    } catch (error) {
        showToast(`Failed to write tags: ${error.message}`, 'error');
    } finally {
        if (writeBtn) {
            writeBtn.disabled = false;
            writeBtn.textContent = 'Write Tags';
        }
    }
}

async function writeAlbumTags(albumId) {
    const album = findEnhancedAlbum(albumId);
    if (!album) return;

    const tracks = (album.tracks || []).filter(t => t.file_path);
    if (tracks.length === 0) {
        showToast('No tracks with files in this album', 'error');
        return;
    }

    await showBatchTagPreview(tracks.map(t => t.id), album.title);
}

async function batchWriteTagsSelected() {
    const trackIds = Array.from(artistDetailPageState.selectedTracks);
    if (trackIds.length === 0) return;

    await showBatchTagPreview(trackIds, null);
}

async function showBatchTagPreview(trackIds, albumTitle) {
    const overlay = document.getElementById('batch-tag-preview-overlay');
    const body = document.getElementById('batch-tag-preview-body');
    const titleEl = document.getElementById('batch-tag-preview-title');
    const summary = document.getElementById('batch-tag-preview-summary');
    const writeBtn = document.getElementById('batch-tag-preview-write-btn');
    if (!overlay || !body) return;

    titleEl.textContent = albumTitle ? `Write Tags — ${albumTitle}` : `Write Tags — ${trackIds.length} Tracks`;
    body.innerHTML = '<div class="tag-preview-loading">Loading tag previews...</div>';
    summary.innerHTML = '';
    writeBtn.disabled = true;
    overlay.classList.remove('hidden');

    // Hide sync checkbox until we know server type
    const syncLabel = document.getElementById('batch-tag-preview-sync-label');
    if (syncLabel) syncLabel.classList.add('hidden');

    try {
        const response = await fetch('/api/library/tracks/tag-preview-batch', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ track_ids: trackIds })
        });
        const result = await response.json();
        if (!result.success) {
            body.innerHTML = `<div class="tag-preview-error">${escapeHtml(result.error)}</div>`;
            return;
        }

        const tracks = result.tracks || [];
        const serverType = result.server_type || null;

        // Show sync checkbox if server connected
        if (syncLabel && serverType && serverType !== 'navidrome') {
            const syncText = document.getElementById('batch-tag-preview-sync-text');
            if (syncText) syncText.textContent = `Sync to ${serverType === 'plex' ? 'Plex' : 'Jellyfin'}`;
            syncLabel.classList.remove('hidden');
        }

        // Categorize tracks
        const withChanges = tracks.filter(t => t.has_changes);
        const noChanges = tracks.filter(t => !t.error && !t.has_changes);
        const errors = tracks.filter(t => t.error);

        // Summary bar
        let summaryHtml = '<div class="batch-tag-summary">';
        if (withChanges.length > 0) summaryHtml += `<span class="batch-tag-stat changed">${withChanges.length} with changes</span>`;
        if (noChanges.length > 0) summaryHtml += `<span class="batch-tag-stat unchanged">${noChanges.length} unchanged</span>`;
        if (errors.length > 0) summaryHtml += `<span class="batch-tag-stat errored">${errors.length} unavailable</span>`;
        summaryHtml += '</div>';
        summary.innerHTML = summaryHtml;

        // Build track accordion
        let html = '';

        // Tracks with changes (expanded by default)
        withChanges.forEach(track => {
            html += _renderBatchTrackDiff(track, true);
        });

        // Errors
        errors.forEach(track => {
            html += `<div class="batch-tag-track error">`;
            html += `<div class="batch-tag-track-header">`;
            html += `<span class="batch-tag-track-number">${track.track_number || '—'}</span>`;
            html += `<span class="batch-tag-track-title">${escapeHtml(track.title)}</span>`;
            html += `<span class="batch-tag-track-status error">${escapeHtml(track.error)}</span>`;
            html += `</div></div>`;
        });

        // Unchanged tracks (collapsed)
        if (noChanges.length > 0) {
            html += `<div class="batch-tag-unchanged-group">`;
            html += `<div class="batch-tag-unchanged-header" onclick="this.parentElement.classList.toggle('expanded')">`;
            html += `<span>${noChanges.length} track${noChanges.length !== 1 ? 's' : ''} already up to date</span>`;
            html += `<span class="batch-tag-chevron">&#9662;</span>`;
            html += `</div>`;
            html += `<div class="batch-tag-unchanged-list">`;
            noChanges.forEach(track => {
                html += `<div class="batch-tag-track-row unchanged">`;
                html += `<span class="batch-tag-track-number">${track.track_number || '—'}</span>`;
                html += `<span class="batch-tag-track-title">${escapeHtml(track.title)}</span>`;
                html += `<span class="batch-tag-track-status ok">✓ Tags match</span>`;
                html += `</div>`;
            });
            html += `</div></div>`;
        }

        if (withChanges.length === 0 && errors.length === 0) {
            html += '<div class="tag-preview-no-changes">All file tags already match DB metadata</div>';
        }

        body.innerHTML = html;

        // Store state for write action
        overlay._batchTrackIds = trackIds;
        overlay._batchServerType = serverType;
        writeBtn.disabled = withChanges.length === 0;

    } catch (error) {
        body.innerHTML = `<div class="tag-preview-error">Failed to load previews: ${escapeHtml(error.message)}</div>`;
    }
}

function _renderBatchTrackDiff(track, expanded) {
    let html = `<div class="batch-tag-track${expanded ? ' expanded' : ''}">`;
    html += `<div class="batch-tag-track-header" onclick="this.parentElement.classList.toggle('expanded')">`;
    html += `<span class="batch-tag-track-number">${track.track_number || '—'}</span>`;
    html += `<span class="batch-tag-track-title">${escapeHtml(track.title)}</span>`;
    html += `<span class="batch-tag-track-status changed">${track.changed_count} field${track.changed_count !== 1 ? 's' : ''} changed</span>`;
    html += `<span class="batch-tag-chevron">&#9662;</span>`;
    html += `</div>`;
    html += `<div class="batch-tag-track-diff">`;
    html += '<table class="tag-preview-table"><thead><tr>';
    html += '<th>Field</th><th>Current File</th><th></th><th>New Value</th>';
    html += '</tr></thead><tbody>';

    (track.diff || []).forEach(d => {
        if (!d.changed) return; // Only show changed fields in batch view
        html += `<tr class="tag-diff-changed">`;
        html += `<td class="tag-field-name">${d.field}</td>`;
        html += `<td class="tag-file-value">${escapeHtml(d.file_value) || '<span class="tag-empty">empty</span>'}</td>`;
        html += `<td class="tag-diff-indicator"><span class="tag-diff-arrow">&rarr;</span></td>`;
        html += `<td class="tag-db-value">${escapeHtml(d.db_value) || '<span class="tag-empty">empty</span>'}</td>`;
        html += '</tr>';
    });

    html += '</tbody></table></div></div>';
    return html;
}

function closeBatchTagPreviewModal() {
    const overlay = document.getElementById('batch-tag-preview-overlay');
    if (overlay) {
        overlay.classList.add('hidden');
        overlay._batchTrackIds = null;
        overlay._batchServerType = null;
    }
}

async function executeBatchWriteTags() {
    const overlay = document.getElementById('batch-tag-preview-overlay');
    const trackIds = overlay?._batchTrackIds;
    if (!trackIds || trackIds.length === 0) return;

    const writeBtn = document.getElementById('batch-tag-preview-write-btn');
    if (writeBtn) {
        writeBtn.disabled = true;
        writeBtn.textContent = 'Writing...';
    }

    const embedCover = document.getElementById('batch-tag-preview-embed-cover')?.checked ?? true;
    const serverType = overlay._batchServerType;
    const syncToServer = document.getElementById('batch-tag-preview-sync-server')?.checked && serverType && serverType !== 'navidrome';

    closeBatchTagPreviewModal();
    await _startBatchWriteTags(trackIds, embedCover, syncToServer);

    if (writeBtn) {
        writeBtn.disabled = false;
        writeBtn.textContent = 'Write Tags';
    }
}

async function _startBatchWriteTags(trackIds, embedCover, syncToServer = false) {
    try {
        const response = await fetch('/api/library/tracks/write-tags-batch', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ track_ids: trackIds, embed_cover: embedCover, sync_to_server: syncToServer })
        });
        const result = await response.json();
        if (!result.success) throw new Error(result.error);

        showToast(`Writing tags for ${trackIds.length} tracks...`, 'info');
        _pollBatchWriteTagsStatus();

    } catch (error) {
        showToast(`Failed to start tag write: ${error.message}`, 'error');
    }
}

let _batchWriteTagsPollTimer = null;

function _pollBatchWriteTagsStatus() {
    if (_batchWriteTagsPollTimer) clearTimeout(_batchWriteTagsPollTimer);

    async function poll() {
        try {
            const response = await fetch('/api/library/tracks/write-tags-batch/status');
            const state = await response.json();

            if (state.status === 'running') {
                if (state.sync_phase === 'syncing') {
                    const serverName = state.sync_server === 'plex' ? 'Plex' : state.sync_server === 'jellyfin' ? 'Jellyfin' : state.sync_server;
                    showToast(`Syncing to ${serverName}...`, 'info');
                } else {
                    const pct = state.total > 0 ? Math.round(state.processed / state.total * 100) : 0;
                    showToast(`Writing tags: ${state.processed}/${state.total} (${pct}%) — ${state.current_track}`, 'info');
                }
                _batchWriteTagsPollTimer = setTimeout(poll, 1000);
            } else if (state.status === 'done') {
                let msg = `Tags written: ${state.written} succeeded, ${state.failed} failed`;
                if (state.sync_phase === 'done') {
                    const serverName = state.sync_server === 'plex' ? 'Plex' : state.sync_server === 'jellyfin' ? 'Jellyfin' : state.sync_server;
                    if (state.sync_synced > 0 && state.sync_failed === 0) {
                        msg += ` — synced to ${serverName}`;
                    } else if (state.sync_failed > 0) {
                        msg += ` — ${serverName} sync: ${state.sync_synced} synced, ${state.sync_failed} failed`;
                    }
                }
                // Surface the first error reason so users can diagnose (e.g. "File not found")
                if (state.failed > 0 && state.errors && state.errors.length > 0) {
                    const firstErr = state.errors[0].error || 'Unknown error';
                    msg += ` (${firstErr})`;
                }
                showToast(msg, state.failed > 0 || state.sync_failed > 0 ? 'warning' : 'success');
                _batchWriteTagsPollTimer = null;
            }
        } catch (error) {
            console.error('Poll write-tags status failed:', error);
            _batchWriteTagsPollTimer = null;
        }
    }

    _batchWriteTagsPollTimer = setTimeout(poll, 800);
}

// ── ReplayGain Analysis ──

let _rgBatchPollTimer = null;
let _rgAlbumPollTimer = null;

/**
 * Analyze a single track and write track-level ReplayGain tags.
 * Synchronous on the server side (~1–3 s). Shows spinner on the button.
 */
async function analyzeTrackReplayGain(trackId, btn) {
    if (btn) {
        btn.disabled = true;
        btn.textContent = '…';
    }
    try {
        const res = await fetch(`/api/library/track/${trackId}/analyze-replaygain`, { method: 'POST' });
        const data = await res.json();
        if (data.success) {
            showToast(`ReplayGain written: ${data.track_gain} (${data.lufs} LUFS)`, 'success');
        } else {
            showToast(`ReplayGain failed: ${data.error}`, 'error');
        }
    } catch (err) {
        showToast('ReplayGain analysis failed', 'error');
    } finally {
        if (btn) {
            btn.disabled = false;
            btn.textContent = 'RG';
        }
    }
}

/**
 * Analyze all tracks in an album and write track + album ReplayGain tags.
 * Kicks off a background job; polls for progress.
 */
async function analyzeAlbumReplayGain(albumId, btn) {
    if (btn) {
        btn.disabled = true;
        btn.innerHTML = '&#9835; Analyzing…';
    }
    try {
        const res = await fetch(`/api/library/album/${albumId}/analyze-replaygain`, { method: 'POST' });
        const data = await res.json();
        if (!data.success) {
            showToast(`ReplayGain: ${data.error}`, 'error');
            if (btn) { btn.disabled = false; btn.innerHTML = '&#9835; ReplayGain'; }
            return;
        }
        showToast('Album ReplayGain analysis started…', 'info');
        _pollAlbumRgStatus(albumId, btn);
    } catch (err) {
        showToast('Failed to start album ReplayGain analysis', 'error');
        if (btn) { btn.disabled = false; btn.innerHTML = '&#9835; ReplayGain'; }
    }
}

function _pollAlbumRgStatus(albumId, btn) {
    if (_rgAlbumPollTimer) clearTimeout(_rgAlbumPollTimer);

    async function poll() {
        try {
            const res = await fetch(`/api/library/album/${albumId}/analyze-replaygain/status`);
            const state = await res.json();

            if (state.status === 'running') {
                const pct = state.total > 0 ? Math.round(state.processed / state.total * 100) : 0;
                showToast(`ReplayGain: ${state.processed}/${state.total} tracks (${pct}%)`, 'info');
                _rgAlbumPollTimer = setTimeout(poll, 1200);
            } else if (state.status === 'done') {
                const msg = `ReplayGain done: ${state.analyzed} analyzed, ${state.failed} failed`;
                showToast(msg, state.failed > 0 ? 'warning' : 'success');
                if (btn) { btn.disabled = false; btn.innerHTML = '&#9835; ReplayGain'; }
                _rgAlbumPollTimer = null;
            }
        } catch (err) {
            console.error('ReplayGain album poll failed:', err);
            if (btn) { btn.disabled = false; btn.innerHTML = '&#9835; ReplayGain'; }
            _rgAlbumPollTimer = null;
        }
    }

    _rgAlbumPollTimer = setTimeout(poll, 1000);
}

/**
 * Analyze selected tracks (track gain only — they may span albums).
 */
async function batchAnalyzeReplayGainSelected() {
    const trackIds = Array.from(artistDetailPageState.selectedTracks);
    if (trackIds.length === 0) return;

    try {
        const res = await fetch('/api/library/tracks/analyze-replaygain-batch', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ track_ids: trackIds }),
        });
        const data = await res.json();
        if (!data.success) {
            showToast(`ReplayGain: ${data.error}`, 'error');
            return;
        }
        showToast(`ReplayGain analysis started for ${trackIds.length} tracks…`, 'info');
        _pollBatchRgStatus();
    } catch (err) {
        showToast('Failed to start batch ReplayGain analysis', 'error');
    }
}

function _pollBatchRgStatus() {
    if (_rgBatchPollTimer) clearTimeout(_rgBatchPollTimer);

    async function poll() {
        try {
            const res = await fetch('/api/library/tracks/analyze-replaygain-batch/status');
            const state = await res.json();

            if (state.status === 'running') {
                const pct = state.total > 0 ? Math.round(state.processed / state.total * 100) : 0;
                showToast(`ReplayGain: ${state.processed}/${state.total} (${pct}%) — ${state.current_track}`, 'info');
                _rgBatchPollTimer = setTimeout(poll, 1000);
            } else if (state.status === 'done') {
                const msg = `ReplayGain done: ${state.analyzed} written, ${state.failed} failed`;
                showToast(msg, state.failed > 0 ? 'warning' : 'success');
                _rgBatchPollTimer = null;
            }
        } catch (err) {
            console.error('ReplayGain batch poll failed:', err);
            _rgBatchPollTimer = null;
        }
    }

    _rgBatchPollTimer = setTimeout(poll, 800);
}

// ── Reorganize Album Files ──

let _reorganizeAlbumId = null;
let _reorganizePollTimer = null;

async function showReorganizeModal(albumId) {
    _reorganizeAlbumId = albumId;
    const overlay = document.getElementById('reorganize-overlay');
    const body = document.getElementById('reorganize-modal-body');
    const title = document.getElementById('reorganize-modal-title');
    const applyBtn = document.getElementById('reorganize-apply-btn');
    if (!overlay || !body) return;

    // Find album data from enhanced view state
    let albumData = null;
    let artistName = '';
    if (artistDetailPageState.enhancedData) {
        artistName = artistDetailPageState.enhancedData.artist.name || '';
        const allAlbums = artistDetailPageState.enhancedData.albums || [];
        albumData = allAlbums.find(a => String(a.id) === String(albumId));
    }

    title.textContent = `Reorganize: ${albumData ? albumData.title : 'Album'}`;
    if (applyBtn) {
        applyBtn.disabled = true;
        applyBtn.textContent = 'Apply';
        applyBtn.onclick = () => executeReorganize();
    }

    // Build modal content
    const variables = [
        { var: '$artist', desc: 'Track artist', example: artistName || 'Artist' },
        { var: '$albumartist', desc: 'Album artist', example: artistName || 'Album Artist' },
        { var: '$artistletter', desc: 'First letter of artist', example: (artistName || 'A')[0].toUpperCase() },
        { var: '$album', desc: 'Album title', example: albumData ? albumData.title : 'Album' },
        { var: '$albumtype', desc: 'Album/EP/Single', example: 'Album' },
        { var: '$title', desc: 'Track title', example: 'Track Name' },
        { var: '$track', desc: 'Track number (zero-padded)', example: '01' },
        { var: '$disc', desc: 'Disc number (filename only)', example: '01' },
        { var: '$cdnum', desc: 'CD label — "CD01" on multi-disc, empty otherwise', example: 'CD01' },
        { var: '$year', desc: 'Release year', example: albumData && albumData.year ? String(albumData.year) : '2024' },
        { var: '$quality', desc: 'Audio quality (filename only)', example: 'FLAC 16bit/44kHz' },
    ];

    let html = '<div class="reorganize-content">';

    // Template input
    html += '<div class="reorganize-template-section">';
    html += '<label class="reorganize-label">Path Template</label>';
    html += '<div class="reorganize-template-hint">Use <code>/</code> to separate folders. The last segment becomes the filename.</div>';
    // Load saved template from settings, fall back to default
    let savedTemplate = '$albumartist/$albumartist - $album/$track - $title';
    try {
        const settingsResp = await fetch('/api/settings');
        if (settingsResp.ok) {
            const settings = await settingsResp.json();
            savedTemplate = settings.file_organization?.templates?.album_path || savedTemplate;
        }
    } catch (_) { }
    html += '<input type="text" id="reorganize-template-input" class="reorganize-template-input" ';
    html += `value="${savedTemplate.replace(/"/g, '&quot;')}" `;
    html += 'placeholder="$albumartist/$album/$track - $title" spellcheck="false">';
    html += '</div>';

    // Variables reference
    html += '<div class="reorganize-variables">';
    html += '<label class="reorganize-label">Available Variables</label>';
    html += '<div class="reorganize-var-grid">';
    variables.forEach(v => {
        html += `<div class="reorganize-var-chip" onclick="insertReorganizeVar('${v.var}')" title="${escapeHtml(v.desc)} — e.g. ${escapeHtml(v.example)}">`;
        html += `<code>${v.var}</code><span class="reorganize-var-desc">${v.desc}</span>`;
        html += '</div>';
    });
    html += '</div></div>';

    // Preview area
    html += '<div class="reorganize-preview-section">';
    html += '<div class="reorganize-preview-header">';
    html += '<label class="reorganize-label">Preview</label>';
    html += '<button class="reorganize-preview-btn" onclick="loadReorganizePreview()">Generate Preview</button>';
    html += '</div>';
    html += '<div id="reorganize-preview-body" class="reorganize-preview-body">';
    html += '<div class="reorganize-preview-hint">Click "Generate Preview" to see how files will be reorganized.</div>';
    html += '</div></div>';

    html += '</div>';
    body.innerHTML = html;
    overlay.classList.remove('hidden');

    // Wire up live preview on enter key
    setTimeout(() => {
        const input = document.getElementById('reorganize-template-input');
        if (input) {
            input.addEventListener('keydown', (e) => {
                if (e.key === 'Enter') {
                    e.preventDefault();
                    loadReorganizePreview();
                }
            });
            input.focus();
        }
    }, 50);
}

function insertReorganizeVar(varName) {
    const input = document.getElementById('reorganize-template-input');
    if (!input) return;
    const start = input.selectionStart;
    const end = input.selectionEnd;
    const val = input.value;
    input.value = val.substring(0, start) + varName + val.substring(end);
    input.focus();
    const newPos = start + varName.length;
    input.setSelectionRange(newPos, newPos);
}

function closeReorganizeModal() {
    const overlay = document.getElementById('reorganize-overlay');
    if (overlay) overlay.classList.add('hidden');
    _reorganizeAlbumId = null;
}

async function loadReorganizePreview() {
    const template = document.getElementById('reorganize-template-input')?.value?.trim();
    const previewBody = document.getElementById('reorganize-preview-body');
    const applyBtn = document.getElementById('reorganize-apply-btn');
    if (!template || !previewBody || !_reorganizeAlbumId) return;

    if (applyBtn) applyBtn.disabled = true;
    previewBody.innerHTML = '<div class="reorganize-preview-loading">Loading preview...</div>';

    try {
        const response = await fetch(`/api/library/album/${_reorganizeAlbumId}/reorganize/preview`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ template })
        });
        const result = await response.json();
        if (!result.success) {
            previewBody.innerHTML = `<div class="reorganize-preview-error">${escapeHtml(result.error || 'Preview failed')}</div>`;
            return;
        }

        const tracks = result.tracks || [];
        if (tracks.length === 0) {
            previewBody.innerHTML = '<div class="reorganize-preview-hint">No tracks found.</div>';
            return;
        }

        let hasChanges = false;
        let hasCollisions = false;
        let html = '<table class="reorganize-preview-table"><thead><tr>';
        html += '<th>#</th><th>Title</th><th>Current Path</th><th></th><th>New Path</th>';
        html += '</tr></thead><tbody>';

        tracks.forEach(t => {
            const unchanged = t.unchanged;
            const noFile = !t.file_exists;
            const collision = t.collision;
            if (!unchanged && t.file_exists) hasChanges = true;
            if (collision) hasCollisions = true;

            const rowClass = collision ? 'reorganize-row-collision' : noFile ? 'reorganize-row-missing' : unchanged ? 'reorganize-row-unchanged' : 'reorganize-row-changed';
            html += `<tr class="${rowClass}">`;
            html += `<td>${t.track_number || ''}</td>`;
            html += `<td>${escapeHtml(t.title)}</td>`;
            html += `<td class="reorganize-path">${noFile ? '<em>File not found</em>' : escapeHtml(t.current_path)}</td>`;
            html += `<td class="reorganize-arrow">${collision ? '!!' : unchanged ? '=' : noFile ? '' : '→'}</td>`;
            html += `<td class="reorganize-path">${noFile ? '' : escapeHtml(t.new_path)}${collision ? ' <em>(collision)</em>' : ''}</td>`;
            html += '</tr>';
        });

        html += '</tbody></table>';

        const changedCount = tracks.filter(t => !t.unchanged && t.file_exists && !t.collision).length;
        const skippedCount = tracks.filter(t => t.unchanged).length;
        const missingCount = tracks.filter(t => !t.file_exists).length;
        const collisionCount = tracks.filter(t => t.collision).length;

        let summary = `<div class="reorganize-preview-summary">`;
        if (changedCount > 0) summary += `<span class="reorganize-stat changed">${changedCount} will move</span>`;
        if (skippedCount > 0) summary += `<span class="reorganize-stat unchanged">${skippedCount} unchanged</span>`;
        if (missingCount > 0) summary += `<span class="reorganize-stat missing">${missingCount} missing</span>`;
        if (collisionCount > 0) summary += `<span class="reorganize-stat collision">${collisionCount} collision${collisionCount !== 1 ? 's' : ''} — add $track or $disc to fix</span>`;
        summary += '</div>';

        previewBody.innerHTML = summary + html;

        // Block apply if collisions exist
        if (applyBtn) applyBtn.disabled = !hasChanges || hasCollisions;

    } catch (error) {
        previewBody.innerHTML = `<div class="reorganize-preview-error">Error: ${escapeHtml(error.message)}</div>`;
    }
}

async function executeReorganize() {
    const template = document.getElementById('reorganize-template-input')?.value?.trim();
    if (!template || !_reorganizeAlbumId) return;

    const applyBtn = document.getElementById('reorganize-apply-btn');
    if (applyBtn) {
        applyBtn.disabled = true;
        applyBtn.textContent = 'Reorganizing...';
    }

    try {
        const response = await fetch(`/api/library/album/${_reorganizeAlbumId}/reorganize`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ template })
        });
        const result = await response.json();
        if (!result.success) throw new Error(result.error);

        closeReorganizeModal();
        showToast(`Reorganizing ${result.total} tracks...`, 'info');
        _pollReorganizeStatus();

    } catch (error) {
        showToast(`Reorganize failed: ${error.message}`, 'error');
        if (applyBtn) {
            applyBtn.disabled = false;
            applyBtn.textContent = 'Apply';
        }
    }
}

function _pollReorganizeStatus() {
    if (_reorganizePollTimer) clearTimeout(_reorganizePollTimer);

    async function poll() {
        try {
            const response = await fetch('/api/library/album/reorganize/status');
            const state = await response.json();

            if (state.status === 'running') {
                const pct = state.total > 0 ? Math.round(state.processed / state.total * 100) : 0;
                showToast(`Reorganizing: ${state.processed}/${state.total} (${pct}%) — ${state.current_track}`, 'info');
                _reorganizePollTimer = setTimeout(poll, 800);
            } else if (state.status === 'done') {
                let msg = `Reorganized: ${state.moved} moved`;
                if (state.skipped > 0) msg += `, ${state.skipped} skipped`;
                if (state.failed > 0) msg += `, ${state.failed} failed`;
                if (state.failed > 0 && state.errors && state.errors.length > 0) {
                    msg += ` (${state.errors[0].error})`;
                }
                showToast(msg, state.failed > 0 ? 'warning' : 'success');
                _reorganizePollTimer = null;

                // Refresh the enhanced view to show updated paths
                if (artistDetailPageState.currentArtistId && artistDetailPageState.enhancedView) {
                    loadEnhancedViewData(artistDetailPageState.currentArtistId);
                }
            }
        } catch (error) {
            console.error('Poll reorganize status failed:', error);
            _reorganizePollTimer = null;
        }
    }

    _reorganizePollTimer = setTimeout(poll, 600);
}

// ── Reorganize All Albums for Artist ──

let _reorganizeAllRunning = false;

async function _showReorganizeAllModal() {
    if (!artistDetailPageState.enhancedData) {
        showToast('No album data loaded', 'error');
        return;
    }
    const albums = artistDetailPageState.enhancedData.albums || [];
    const artistName = artistDetailPageState.enhancedData.artist.name || 'Artist';

    if (albums.length === 0) {
        showToast('No albums to reorganize', 'error');
        return;
    }

    const overlay = document.getElementById('reorganize-overlay');
    const body = document.getElementById('reorganize-modal-body');
    const title = document.getElementById('reorganize-modal-title');
    const applyBtn = document.getElementById('reorganize-apply-btn');
    if (!overlay || !body) return;

    title.textContent = `Reorganize All Albums — ${artistName}`;

    // Load saved template
    let savedTemplate = '$albumartist/$albumartist - $album/$track - $title';
    try {
        const settingsResp = await fetch('/api/settings');
        if (settingsResp.ok) {
            const settings = await settingsResp.json();
            savedTemplate = settings.file_organization?.templates?.album_path || savedTemplate;
        }
    } catch (_) { }

    let html = '<div class="reorganize-content">';

    // Template input
    html += '<div class="reorganize-template-section">';
    html += '<label class="reorganize-label">Path Template</label>';
    html += '<div class="reorganize-template-hint">This template will be applied to all albums below. Use <code>/</code> to separate folders.</div>';
    html += `<input type="text" id="reorganize-template-input" class="reorganize-template-input" value="${savedTemplate.replace(/"/g, '&quot;')}" placeholder="$albumartist/$album/$track - $title" spellcheck="false">`;
    html += '</div>';

    // Album list
    html += '<div style="margin-top:14px;">';
    html += `<label class="reorganize-label">${albums.length} album${albums.length !== 1 ? 's' : ''} will be reorganized:</label>`;
    html += '<div style="max-height:200px;overflow-y:auto;margin-top:6px;border:1px solid rgba(255,255,255,0.08);border-radius:8px;padding:6px 10px;">';
    albums.forEach((a, i) => {
        const trackCount = a.tracks ? a.tracks.length : '?';
        html += `<div style="padding:4px 0;font-size:0.88em;color:rgba(255,255,255,0.7);border-bottom:${i < albums.length - 1 ? '1px solid rgba(255,255,255,0.04)' : 'none'};">`;
        html += `${escapeHtml(a.title)} <span style="color:rgba(255,255,255,0.3);">(${trackCount} tracks)</span>`;
        html += '</div>';
    });
    html += '</div></div>';

    html += '</div>';
    body.innerHTML = html;

    // Wire apply button for bulk mode
    if (applyBtn) {
        applyBtn.disabled = false;
        applyBtn.textContent = 'Reorganize All';
        applyBtn.onclick = () => _executeReorganizeAll();
    }

    overlay.classList.remove('hidden');
}

async function _executeReorganizeAll() {
    if (_reorganizeAllRunning) return;

    const templateInput = document.getElementById('reorganize-template-input');
    const template = templateInput ? templateInput.value.trim() : '';
    if (!template) {
        showToast('Template cannot be empty', 'error');
        return;
    }

    const albums = artistDetailPageState.enhancedData.albums || [];
    const total = albums.length;
    const artistName = artistDetailPageState.enhancedData.artist?.name || 'this artist';

    const confirmed = await showConfirmDialog({
        title: 'Reorganize All Albums',
        message: `This will reorganize ${total} album${total !== 1 ? 's' : ''} for ${artistName} using the template:\n\n${template}\n\nFiles will be moved and renamed. This cannot be undone.`,
        confirmText: 'Reorganize All',
        destructive: false,
    });
    if (!confirmed) return;

    _reorganizeAllRunning = true;
    const applyBtn = document.getElementById('reorganize-apply-btn');
    if (applyBtn) { applyBtn.disabled = true; applyBtn.textContent = 'Working...'; }

    // Close modal
    const overlay = document.getElementById('reorganize-overlay');
    if (overlay) overlay.classList.add('hidden');

    let succeeded = 0, failed = 0;

    for (let i = 0; i < total; i++) {
        const album = albums[i];
        showToast(`Reorganizing album ${i + 1}/${total}: ${album.title}`, 'info');

        try {
            const resp = await fetch(`/api/library/album/${album.id}/reorganize`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ template }),
            });
            const result = await resp.json();
            if (!result.success) {
                showToast(`Failed: ${album.title} — ${result.error || 'unknown error'}`, 'error');
                failed++;
                continue;
            }

            // Wait for this album to finish
            await _waitForReorganizeComplete();
            succeeded++;
        } catch (err) {
            showToast(`Error: ${album.title} — ${err.message}`, 'error');
            failed++;
        }
    }

    let msg = `Reorganized ${succeeded} of ${total} album${total !== 1 ? 's' : ''}`;
    if (failed > 0) msg += ` (${failed} failed)`;
    showToast(msg, failed > 0 ? 'warning' : 'success');

    _reorganizeAllRunning = false;
    if (applyBtn) { applyBtn.disabled = false; applyBtn.textContent = 'Reorganize All'; }

    // Refresh enhanced view
    if (artistDetailPageState.currentArtistId && artistDetailPageState.enhancedView) {
        loadEnhancedViewData(artistDetailPageState.currentArtistId);
    }
}

function _waitForReorganizeComplete() {
    return new Promise(resolve => {
        const poll = setInterval(async () => {
            try {
                const resp = await fetch('/api/library/album/reorganize/status');
                const state = await resp.json();
                if (state.status === 'done' || state.status === 'idle') {
                    clearInterval(poll);
                    resolve();
                }
            } catch {
                clearInterval(poll);
                resolve();
            }
        }, 800);
    });
}

async function playLibraryTrack(track, albumTitle, artistName) {
    if (!track.file_path) {
        showToast('No file available for this track', 'error');
        return;
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
                album: albumTitle || ''
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

// ==================== End Enhanced Library Management View ====================

// UI state management functions
function showArtistDetailLoading(show) {
    const loadingElement = document.getElementById("artist-detail-loading");
    if (loadingElement) {
        if (show) {
            loadingElement.classList.remove("hidden");
        } else {
            loadingElement.classList.add("hidden");
        }
    }
}

function showArtistDetailError(show, message = "") {
    const errorElement = document.getElementById("artist-detail-error");
    const errorMessageElement = document.getElementById("artist-detail-error-message");

    if (errorElement) {
        if (show) {
            errorElement.classList.remove("hidden");
            if (errorMessageElement && message) {
                errorMessageElement.textContent = message;
            }
        } else {
            errorElement.classList.add("hidden");
        }
    }
}

function showArtistDetailMain(show) {
    const mainElement = document.getElementById("artist-detail-main");
    if (mainElement) {
        if (show) {
            mainElement.classList.remove("hidden");
        } else {
            mainElement.classList.add("hidden");
        }
    }
}

function showArtistDetailHero(show) {
    const heroElement = document.getElementById("artist-hero-section");
    if (heroElement) {
        if (show) {
            heroElement.classList.remove("hidden");
        } else {
            heroElement.classList.add("hidden");
        }
    }
}

/**
 * Initialize the library page watchlist button
 */
async function initializeLibraryWatchlistButton(artistId, artistName) {
    const button = document.getElementById('library-artist-watchlist-btn');
    if (!button) return;

    console.log(`🔧 Initializing library watchlist button for: ${artistName} (${artistId})`);

    // Reset button state
    button.disabled = false;
    button.classList.remove('watching');

    // Set up click handler
    button.onclick = (e) => toggleLibraryWatchlist(e, artistId, artistName);

    // Check and update current status
    await updateLibraryWatchlistButtonStatus(artistId);
}

/**
 * Toggle watchlist status for library page
 */
async function toggleLibraryWatchlist(event, artistId, artistName) {
    event.preventDefault();

    const button = document.getElementById('library-artist-watchlist-btn');
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

        // Update button state based on new status
        if (isWatching) {
            // Was watching, now removed
            icon.textContent = '👁️';
            text.textContent = 'Add to Watchlist';
            button.classList.remove('watching');
            console.log(`❌ Removed ${artistName} from watchlist`);
        } else {
            // Was not watching, now added
            icon.textContent = '👁️';
            text.textContent = 'Watching...';
            button.classList.add('watching');
            console.log(`✅ Added ${artistName} to watchlist`);
        }

        // Update dashboard watchlist count if function exists
        if (typeof updateWatchlistCount === 'function') {
            updateWatchlistCount();
        }

        showToast(data.message, 'success');

    } catch (error) {
        console.error('Error toggling library watchlist:', error);

        // Restore button state
        text.textContent = originalText;
        showToast(`Error: ${error.message}`, 'error');

    } finally {
        button.disabled = false;
    }
}

/**
 * Update library watchlist button status based on current state
 */
async function updateLibraryWatchlistButtonStatus(artistId) {
    const button = document.getElementById('library-artist-watchlist-btn');
    if (!button) return;

    try {
        const response = await fetch('/api/watchlist/check', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ artist_id: artistId })
        });

        const data = await response.json();

        if (data.success) {
            const icon = button.querySelector('.watchlist-icon');
            const text = button.querySelector('.watchlist-text');

            if (data.is_watching) {
                icon.textContent = '👁️';
                text.textContent = 'Watching...';
                button.classList.add('watching');
            } else {
                icon.textContent = '👁️';
                text.textContent = 'Add to Watchlist';
                button.classList.remove('watching');
            }
        }
    } catch (error) {
        console.warn('Failed to check library watchlist status:', error);
    }
}

// =================================
