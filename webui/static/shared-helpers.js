// SHARED HELPERS
// ============================================================================
// General-purpose helpers extracted from artists.js. These functions are used
// across discover.js, api-monitor.js, library.js, enrichment.js, wishlist-
// tools.js and others — they have no conceptual home in the old Artists page
// file. Moved here so artists.js can be deleted once the inline Artists page
// is fully retired.
//
// Load order: this file must load AFTER core.js (uses artistsPageState,
// artistDownloadBubbles, searchDownloadBubbles, beatportDownloadBubbles
// globals declared there) and BEFORE any file that calls these functions.
// ============================================================================


// ----------------------------------------------------------------------------
// Enhanced search shared utilities (used by Search page + global widget)
// ----------------------------------------------------------------------------

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

// Per-source labels + tab/badge CSS classes + icon glyph for the source
// picker row. The `logo` URL (when present) renders as an <img> in the
// source-picker chip; `icon` stays as the emoji fallback for sources
// without a canonical logo. Logo URLs mirror the constants in core.js so
// both places stay in sync.
const SOURCE_LABELS = {
    spotify: {
        text: 'Spotify', icon: '🎵',
        logo: 'https://storage.googleapis.com/pr-newsroom-wp/1/2023/05/Spotify_Primary_Logo_RGB_Green.png',
        tabClass: 'enh-tab-spotify', badgeClass: 'enh-badge-spotify',
    },
    itunes: {
        text: 'Apple Music', icon: '🍎',
        logo: 'https://upload.wikimedia.org/wikipedia/commons/thumb/d/df/ITunes_logo.svg/960px-ITunes_logo.svg.png',
        tabClass: 'enh-tab-itunes', badgeClass: 'enh-badge-itunes',
    },
    deezer: {
        text: 'Deezer', icon: '🎶',
        logo: 'https://cdn.brandfetch.io/idEUKgCNtu/theme/dark/symbol.svg?c=1bxid64Mup7aczewSAYMX&t=1758260798610',
        tabClass: 'enh-tab-deezer', badgeClass: 'enh-badge-deezer',
    },
    discogs: {
        text: 'Discogs', icon: '📀',
        logo: 'https://upload.wikimedia.org/wikipedia/commons/thumb/6/6b/Discogs_icon.svg/960px-Discogs_icon.svg.png',
        tabClass: 'enh-tab-discogs', badgeClass: 'enh-badge-discogs',
    },
    hydrabase: {
        text: 'Hydrabase', icon: '💎',
        logo: '/static/hydrabase.png',
        tabClass: 'enh-tab-hydrabase', badgeClass: 'enh-badge-hydrabase',
    },
    musicbrainz: {
        text: 'MusicBrainz', icon: '🧠',
        logo: 'https://upload.wikimedia.org/wikipedia/commons/thumb/9/9e/MusicBrainz_Logo_%282016%29.svg/500px-MusicBrainz_Logo_%282016%29.svg.png',
        tabClass: 'enh-tab-musicbrainz', badgeClass: 'enh-badge-musicbrainz',
    },
    youtube_videos: {
        text: 'Music Videos', icon: '🎬',
        tabClass: 'enh-tab-youtube', badgeClass: 'enh-badge-youtube',
    },
    soulseek: {
        // No canonical brand logo available — stick with a basic music glyph.
        text: 'Soulseek', icon: '🎼',
        tabClass: 'enh-tab-soulseek', badgeClass: 'enh-badge-soulseek',
    },
};

// Canonical display order for the source picker. Standard metadata sources
// first, then YouTube Music Videos, then Soulseek (basic-file source).
const SOURCE_ORDER = [
    'spotify', 'itunes', 'deezer', 'discogs', 'hydrabase', 'musicbrainz',
    'youtube_videos', 'soulseek',
];

// Sources the config-status endpoint doesn't cover because they don't need
// user-supplied credentials — they always render as "configured" in the picker.
// Soulseek IS configurable (needs slskd URL), so it's intentionally not here:
// /api/settings/config-status reports its real state and the picker dims it
// when no slskd is set up, redirecting clicks to Settings → Downloads.
const _ALWAYS_CONFIGURED_SOURCES = new Set(['musicbrainz', 'youtube_videos']);

// Fetch /api/settings/config-status and return a map { src -> bool }
// covering every source in SOURCE_ORDER. Sources not present in the backend
// registry (musicbrainz / youtube_videos / soulseek) are reported as
// configured so the picker doesn't dim always-available sources.
async function fetchSourceConfiguredMap() {
    const map = {};
    try {
        const resp = await fetch('/api/settings/config-status');
        if (resp.ok) {
            const data = await resp.json();
            for (const src of SOURCE_ORDER) {
                if (_ALWAYS_CONFIGURED_SOURCES.has(src)) {
                    map[src] = true;
                } else {
                    map[src] = !!(data[src] && data[src].configured);
                }
            }
            return map;
        }
    } catch (_) { /* fall through to conservative default */ }
    // Network / endpoint failure — be permissive rather than dim everything.
    for (const src of SOURCE_ORDER) map[src] = true;
    return map;
}

// Shared source-picker controller used by both the unified Search page
// and the global search widget. Owns all the query/active-source/per-query
// cache state, fetch dispatch (enhanced-search for standard sources, NDJSON
// for YouTube Music Videos), configured-source discovery, fallback tracking,
// and icon-row rendering. Each surface passes per-surface wiring — DOM
// elements, a CSS class prefix, and callbacks — and the controller takes
// care of the rest.
//
// Config:
//   sourceRowElement        — HTMLElement where the icon row is rendered
//   iconClassPrefix         — 'enh' or 'gsearch' (drives CSS class names)
//   onStateChange(state)    — called whenever the surface should re-render
//                             results (cache hit, fetch settle, query reset)
//   onSoulseekSelected(q)   — surface decides what happens when the user
//                             clicks the Soulseek icon (basic-section swap
//                             on the Search page, /search handoff on the
//                             global widget)
//   onUnconfiguredClick(src)— override the default "open Settings" behaviour
//
// Returned methods:
//   init()                  — async; reads /api/settings + /api/settings/
//                             config-status, seeds default source, falls
//                             forward if primary is unconfigured, draws row
//   submitQuery(query)      — user typed a new query (clears cache on change)
//   setActiveSource(src)    — user clicked a different source icon
//   renderSourceRow()       — re-draws the icon row (call after state edits)
function createSearchController({
    sourceRowElement,
    iconClassPrefix = 'enh',
    onStateChange,
    onSoulseekSelected,
    onUnconfiguredClick,
} = {}) {
    const iconClass = `${iconClassPrefix}-source-icon`;
    const glyphClass = `${iconClassPrefix}-source-icon-glyph`;
    const labelClass = `${iconClassPrefix}-source-icon-label`;

    // Per-query cache. `sources[src]` holds the result payload the last
    // time `src` was fetched for the current query. `fallbacks[src]`
    // records the source the backend actually served when it auto-fell-
    // back (e.g. user clicked Spotify but got Deezer because Spotify is
    // rate-limited). `loadingSources` drives per-icon spinners. The whole
    // cache is cleared whenever the query string changes — we never
    // serve stale results across queries.
    const state = {
        query: '',
        activeSource: 'spotify',
        sources: {},
        fallbacks: {},
        loadingSources: new Set(),
        configuredSources: {},
        _initialized: false,
    };
    // Optimistic default — replaced by the real config-status lookup on
    // init. Prevents a flash of "all unconfigured" icons.
    for (const src of SOURCE_ORDER) state.configuredSources[src] = true;

    let abortCtrl = null;
    // Monotonic request token. Each _fetchSource call captures the next
    // value; settle/error blocks bail before mutating shared state if a
    // newer request has superseded them. Without this, a fast retype lets
    // the in-flight fetch's catch (or settle) clear loadingSources / write
    // stale data into state.sources, causing a flash of empty/error UI
    // while the new query's fetch is still running.
    let _requestSeq = 0;

    function _notify() { if (onStateChange) onStateChange(state); }

    function renderSourceRow() {
        if (!sourceRowElement) return;
        sourceRowElement.innerHTML = SOURCE_ORDER.map(src => {
            const info = SOURCE_LABELS[src];
            if (!info) return '';
            const active = src === state.activeSource;
            const cached = !!state.sources[src];
            const loading = state.loadingSources.has(src);
            const fallback = state.fallbacks[src];
            const configured = state.configuredSources[src] !== false;

            const classes = [
                iconClass,
                active ? 'active' : '',
                cached ? 'cached' : '',
                loading ? 'loading' : '',
                fallback ? 'fallback-warning' : '',
                configured ? '' : 'unconfigured',
            ].filter(Boolean).join(' ');

            let title;
            if (!configured) {
                title = `${info.text} — set up in Settings`;
            } else if (fallback) {
                title = `${info.text} unavailable — served from ${(SOURCE_LABELS[fallback] || {}).text || fallback}`;
            } else {
                title = info.text;
            }

            const glyph = loading
                ? '⏳'
                : (info.logo
                    ? `<img src="${escapeHtml(info.logo)}" alt="" loading="lazy">`
                    : info.icon);

            return `
                <button class="${classes}" data-source="${src}" role="tab"
                        aria-selected="${active}" title="${escapeHtml(title)}">
                    <span class="${glyphClass}">${glyph}</span>
                    <span class="${labelClass}">${escapeHtml(info.text)}</span>
                </button>`;
        }).join('');

        sourceRowElement.querySelectorAll(`.${iconClass}`).forEach(btn => {
            btn.addEventListener('click', (e) => {
                // stopPropagation prevents surface-level outside-click handlers
                // from dismissing the results while we re-render the icon row
                // (which detaches the clicked button from the DOM).
                e.stopPropagation();
                setActiveSource(btn.dataset.source);
            });
        });
    }

    async function init() {
        if (state._initialized) return;
        state._initialized = true;

        // Resolve the user's configured primary source.
        try {
            const resp = await fetch('/api/settings');
            if (resp.ok) {
                const settings = await resp.json();
                const cfg = settings.metadata && settings.metadata.fallback_source;
                if (cfg && SOURCE_LABELS[cfg]) state.activeSource = cfg;
            }
        } catch (_) { /* best-effort */ }
        if (!SOURCE_LABELS[state.activeSource]) state.activeSource = 'spotify';

        // Figure out which sources actually have credentials saved.
        try {
            state.configuredSources = await fetchSourceConfiguredMap();
        } catch (_) { /* keep optimistic default */ }

        // If the configured primary is itself unconfigured (Spotify saved
        // as primary but no client_id yet), fall forward to the first
        // configured source so the default active icon is usable.
        if (state.configuredSources[state.activeSource] === false) {
            const firstConfigured = SOURCE_ORDER.find(s => state.configuredSources[s] !== false);
            if (firstConfigured) state.activeSource = firstConfigured;
        }

        renderSourceRow();
        _notify();
    }

    function setActiveSource(src) {
        if (!SOURCE_LABELS[src]) return;

        // Unconfigured — jump to the relevant card in Settings rather than
        // firing a search that can't succeed. Don't swap activeSource so the
        // user's previous pick stays current when they come back.
        if (state.configuredSources[src] === false) {
            if (onUnconfiguredClick) onUnconfiguredClick(src);
            else openSettingsForSource(src);
            return;
        }

        // Clicking the already-active source is a no-op for normal sources,
        // but for Soulseek we still re-fire the callback so the surface can
        // re-issue the handoff (e.g. user typed and wants a fresh search).
        if (src === state.activeSource) {
            if (src === 'soulseek' && onSoulseekSelected) onSoulseekSelected(state.query);
            return;
        }

        state.activeSource = src;
        renderSourceRow();

        // Soulseek — let the surface decide what to do (basic-section swap
        // on Search page, /search handoff on global widget). We don't cache
        // or auto-fetch soulseek results in the controller.
        if (src === 'soulseek') {
            if (onSoulseekSelected) onSoulseekSelected(state.query);
            return;
        }

        if (state.sources[src]) {
            _notify();
        } else if (state.query) {
            _fetchSource(src);
        } else {
            _notify();
        }
    }

    async function _fetchSource(src) {
        const query = state.query;
        if (!query) return;

        const requestId = ++_requestSeq;

        state.loadingSources.add(src);
        renderSourceRow();
        _notify();

        if (abortCtrl) abortCtrl.abort();
        abortCtrl = new AbortController();

        try {
            if (src === 'youtube_videos') {
                await _fetchYouTubeVideos(query, abortCtrl.signal, requestId);
            } else {
                const data = await enhancedSearchFetch(query, {
                    source: src,
                    signal: abortCtrl.signal,
                });
                // Bail without writing if a newer query has superseded us.
                if (requestId !== _requestSeq) return;
                state.sources[src] = {
                    artists: data.spotify_artists || [],
                    albums: data.spotify_albums || [],
                    tracks: data.spotify_tracks || [],
                    videos: [],
                    db_artists: data.db_artists || [],
                };
                const served = data.primary_source || data.metadata_source;
                if (served && served !== src) state.fallbacks[src] = served;
            }

            // Only the latest request gets to clear loadingSources + notify.
            // A stale completion would otherwise wipe the spinner the new
            // request just set.
            if (requestId !== _requestSeq) return;
            state.loadingSources.delete(src);
            renderSourceRow();
            _notify();
        } catch (err) {
            if (requestId !== _requestSeq) return;
            state.loadingSources.delete(src);
            renderSourceRow();
            _notify();
            if (err.name !== 'AbortError') {
                console.debug(`Source fetch failed for ${src}:`, err);
            }
        }
    }

    async function _fetchYouTubeVideos(query, signal, requestId) {
        const res = await fetch('/api/enhanced-search/source/youtube_videos', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ query }),
            signal,
        });
        if (!res.ok) throw new Error(`YouTube search failed: ${res.status}`);

        // Bail before allocating cache entry if superseded by a newer request.
        if (requestId !== _requestSeq) return;

        state.sources['youtube_videos'] = {
            artists: [], albums: [], tracks: [], videos: [], db_artists: [],
        };
        const cache = state.sources['youtube_videos'];

        const reader = res.body.getReader();
        const decoder = new TextDecoder();
        let buffer = '';
        while (true) {
            const { done, value } = await reader.read();
            if (done) break;
            // Mid-stream supersession check — abort cleanly without writing
            // additional chunks into stale cache.
            if (requestId !== _requestSeq) return;
            buffer += decoder.decode(value, { stream: true });
            let idx;
            while ((idx = buffer.indexOf('\n')) !== -1) {
                const line = buffer.slice(0, idx).trim();
                buffer = buffer.slice(idx + 1);
                if (!line) continue;
                try {
                    const chunk = JSON.parse(line);
                    if (chunk.type === 'videos') {
                        cache.videos = chunk.data;
                        // Live-render if still the active source.
                        if (state.activeSource === 'youtube_videos') _notify();
                    }
                } catch (_) { /* best-effort NDJSON parse */ }
            }
        }
    }

    function submitQuery(query) {
        if (query !== state.query) {
            state.query = query;
            state.sources = {};
            state.fallbacks = {};
            state.loadingSources = new Set();
            renderSourceRow();
        }

        // Soulseek — surface handles the full query handoff.
        if (state.activeSource === 'soulseek') {
            if (onSoulseekSelected) onSoulseekSelected(query);
            return;
        }

        // Cache hit — instant re-render, no fetch.
        if (state.sources[state.activeSource]) {
            _notify();
            return;
        }

        _fetchSource(state.activeSource);
    }

    return {
        state,
        init,
        submitQuery,
        setActiveSource,
        renderSourceRow,
    };
}


// Navigate to Settings → relevant tab and scroll to the service card that
// matches the picker's source id. Called when a user clicks an unconfigured
// source icon. Soulseek is special-cased to land on the Downloads tab where
// its slskd URL field lives (gated behind the download-source-mode select);
// every other source has a card on Connections.
function openSettingsForSource(src) {
    if (typeof navigateToPage !== 'function') return;
    navigateToPage('settings');
    const targetTab = src === 'soulseek' ? 'downloads' : 'connections';
    setTimeout(() => {
        try {
            if (typeof switchSettingsTab === 'function') switchSettingsTab(targetTab);
        } catch (_) { /* best-effort */ }
        setTimeout(() => {
            // Soulseek doesn't have a .stg-service card — scroll to the
            // slskd URL input instead so the user lands on the right field.
            const target = src === 'soulseek'
                ? document.querySelector('#settings-page #soulseek-url')
                : document.querySelector(`#settings-page .stg-service[data-service="${src}"]`);
            if (!target) return;
            target.scrollIntoView({ behavior: 'smooth', block: 'center' });
            if (src === 'soulseek') {
                try { target.focus(); } catch (_) { /* best-effort */ }
            } else {
                target.classList.add('stg-service-flash');
                setTimeout(() => target.classList.remove('stg-service-flash'), 2200);
            }
        }, 120);
    }, 60);
}

// Render a single enhanced-search result section (artists / albums / tracks).
// Shared between the Search page and the global widget. The mapItem callback
// projects each backend item to the card config consumed here.
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


// ----------------------------------------------------------------------------
// Discography completion checking (for artist-detail pages, library page)
// ----------------------------------------------------------------------------

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


// ----------------------------------------------------------------------------
// Download bubble infrastructure, image colour/glow helpers, HTML escape,
// service-status polling, enrichment-card rendering. All originally defined
// in artists.js but used broadly across the app.
// ----------------------------------------------------------------------------

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


// ----------------------------------------------------------------------------
// Similar Artists — fetch + render via MusicMap. Source-agnostic (artist name
// based), works for both library and metadata-source artists. Targets DOM IDs
// #similar-artists-loading, #similar-artists-error, #similar-artists-bubbles-
// container that live on the artist-detail page.
// ----------------------------------------------------------------------------

// Similar artists section lives on the standalone artist-detail page with the
// 'ad-' prefixed ids. The resolver shape was originally designed for both the
// inline Artists page and the standalone page; the inline page has since been
// retired, so only the standalone candidate remains.
function _resolveSimilarArtistsTargets() {
    const sectionEl = document.getElementById('ad-similar-artists-section');
    if (!sectionEl) return null;
    return {
        section: sectionEl,
        loadingEl: document.getElementById('ad-similar-artists-loading'),
        errorEl: document.getElementById('ad-similar-artists-error'),
        container: document.getElementById('ad-similar-artists-bubbles-container'),
    };
}

async function loadSimilarArtists(artistName) {
    if (!artistName) {
        console.warn('⚠️ No artist name provided for similar artists');
        return;
    }

    console.log(`🔍 Loading similar artists for: ${artistName}`);

    const targets = _resolveSimilarArtistsTargets();
    if (!targets) {
        console.warn('⚠️ Similar artists section elements not found on any active page');
        return;
    }
    const { section, loadingEl, errorEl, container } = targets;

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
    const targets = _resolveSimilarArtistsTargets();
    const container = targets && targets.container;

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
    const targets = _resolveSimilarArtistsTargets();
    const container = targets && targets.container;

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

    // Click → navigate to the standalone artist-detail page. Works for both
    // library and source artists thanks to the source-aware backend endpoint.
    bubble.addEventListener('click', () => {
        console.log(`🎵 Clicked similar artist: ${artist.name} (ID: ${artist.id})`);
        navigateToArtistDetail(artist.id, artist.name, artist.source || null);
    });

    return bubble;
}


// ----------------------------------------------------------------------------
// Lazy artist-card image loader (used by wishlist-tools.js + the legacy inline
// Artists page search results). Fetches /api/artist/<id>/image for each card
// flagged data-needs-image="true" in batches of 5.
// ----------------------------------------------------------------------------

async function lazyLoadArtistImages(container) {
    if (!container) {
        console.error('❌ lazyLoadArtistImages: container is null');
        return;
    }

    const cardsNeedingImages = container.querySelectorAll('[data-needs-image="true"]');
    if (cardsNeedingImages.length === 0) return;

    const batchSize = 5;
    const cards = Array.from(cardsNeedingImages);

    for (let i = 0; i < cards.length; i += batchSize) {
        const batch = cards.slice(i, i + batchSize);
        await Promise.all(batch.map(async (card) => {
            const artistId = card.dataset.artistId;
            if (!artistId) return;
            try {
                const response = await fetch(`/api/artist/${artistId}/image`);
                const data = await response.json();
                if (data.success && data.image_url) {
                    if (card.classList.contains('suggestion-card')) {
                        card.style.backgroundImage = `url(${data.image_url})`;
                        card.style.backgroundSize = 'cover';
                        card.style.backgroundPosition = 'center';
                    } else if (card.classList.contains('artist-card')) {
                        const bgElement = card.querySelector('.artist-card-background');
                        if (bgElement) {
                            bgElement.style.cssText = `background-image: url('${data.image_url}'); background-size: cover; background-position: center;`;
                        }
                    }
                    card.dataset.needsImage = 'false';
                }
            } catch (error) {
                console.error(`❌ Failed to load image for artist ${artistId}:`, error);
            }
        }));
    }
}

// Legacy global alias — wishlist-tools.js falls back to window.lazyLoadArtistImages
window.lazyLoadArtistImages = lazyLoadArtistImages;


// ----------------------------------------------------------------------------
// Album-card completion overlay error state (called from checkDiscographyCompletion
// when the API request fails)
// ----------------------------------------------------------------------------

function showCompletionError() {
    const allOverlays = document.querySelectorAll('.completion-overlay.checking');
    allOverlays.forEach(overlay => {
        overlay.classList.remove('checking');
        overlay.classList.add('error');
        overlay.innerHTML = '<span class="completion-status">Error</span>';
        overlay.title = 'Failed to check completion status';
    });
}
