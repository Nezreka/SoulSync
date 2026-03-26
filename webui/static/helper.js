// ===============================
// INTERACTIVE CONTEXTUAL HELP SYSTEM
// ===============================

let helperModeActive = false;
let _helperPopover = null;
let _helperHighlighted = null;

// ── Content Database ─────────────────────────────────────────────────────
// Keys: CSS selectors matched via element.matches()
// Values: { title, description, tips[], docsId (optional — links to help page section) }

const HELPER_CONTENT = {

    // ─── SIDEBAR NAVIGATION ─────────────────────────────────────────

    '.nav-button[data-page="dashboard"]': {
        title: 'System Dashboard',
        description: 'Your central command center for monitoring system health, managing background operations, and running maintenance tools. Service connections, download stats, and system resources are all visible at a glance.',
        tips: [
            'Service cards show real-time connection status with response times',
            'Tools run database updates, quality scans, backups, and more',
            'Activity feed tracks every operation in real-time via WebSocket'
        ],
        docsId: 'dashboard'
    },
    '.nav-button[data-page="sync"]': {
        title: 'Playlist Sync',
        description: 'Mirror playlists from Spotify, YouTube, Tidal, Deezer, ListenBrainz, and Beatport. SoulSync matches each track to your download sources and downloads what\'s missing from your library.',
        tips: [
            'Select playlists from the left panel to begin syncing',
            'Real-time progress shows matched, pending, and failed tracks',
            'Synced playlists are monitored for changes on future syncs'
        ],
        docsId: 'sync'
    },
    '.nav-button[data-page="downloads"]': {
        title: 'Music Search & Downloads',
        description: 'Search for music across all your configured metadata sources and download from Soulseek, YouTube, Tidal, Qobuz, HiFi, or Deezer. Enhanced Search shows categorized results; Basic Search gives raw Soulseek results with filters.',
        tips: [
            'Enhanced Search: click an album to download, click a track to search sources',
            'Multi-source tabs let you compare results across Spotify, iTunes, and Deezer',
            'Play button previews tracks from your download source before committing'
        ],
        docsId: 'search'
    },
    '.nav-button[data-page="discover"]': {
        title: 'Discover New Music',
        description: 'Personalized music discovery through genre exploration, similar artists, seasonal picks, curated playlists, and recommendations based on your library and listening habits.',
        tips: [
            'Genre Explorer combines data from all your metadata sources',
            'Similar artists are generated from your watchlist artists',
            'Time Machine lets you browse music by decade'
        ],
        docsId: 'discover'
    },
    '.nav-button[data-page="artists"]': {
        title: 'Artist Browser',
        description: 'Search for any artist and explore their full discography — albums, singles, and EPs with one-click download. View rich artist profiles with bio, stats, genres, and service links.',
        tips: [
            'Click any album card to open the download modal with track selection',
            'Similar artists appear below the discography for discovery',
            'Add artists to your Watchlist for automatic new release monitoring'
        ],
        docsId: 'artists'
    },
    '.nav-button[data-page="automations"]': {
        title: 'Automation Hub',
        description: 'Build automated workflows with a visual builder: WHEN something happens → DO an action → THEN notify. Schedule tasks, chain operations with signals, and get alerts via Discord, Pushbullet, Telegram, or Gotify.',
        tips: [
            'Signals let you chain multiple automations together',
            'Schedule automations daily, weekly, or triggered by events',
            'Built-in actions include library scans, watchlist checks, and quality scans'
        ],
        docsId: 'automations'
    },
    '.nav-button[data-page="library"]': {
        title: 'Music Library',
        description: 'Browse your complete collection organized by artists. Click any artist to see their albums with ownership stats. Enhanced view enables inline metadata editing, tag writing, and bulk operations.',
        tips: [
            'Enhanced view toggle on artist detail pages enables advanced management',
            'Write tags directly to audio files (MP3, FLAC, OGG, M4A)',
            'Bulk select tracks across albums for batch operations'
        ],
        docsId: 'library'
    },
    '.nav-button[data-page="stats"]': {
        title: 'Library Statistics',
        description: 'Detailed analytics — genre breakdowns, format distribution, quality analysis, collection growth, and enrichment coverage across all metadata services.',
        docsId: 'dashboard'
    },
    '.nav-button[data-page="import"]': {
        title: 'Music Import',
        description: 'Import music files from your staging folder. SoulSync identifies tracks using AcoustID fingerprinting, matches them to metadata, and organizes them into your library with proper tagging.',
        docsId: 'import'
    },
    '.nav-button[data-page="settings"]': {
        title: 'Settings',
        description: 'Configure everything — service credentials, download sources, quality profiles, file organization templates, processing options, and media server connections.',
        tips: [
            'Connect your metadata source (Spotify, iTunes, or Deezer) first',
            'Set up your media server (Plex, Jellyfin, or Navidrome)',
            'Quality Profile controls which audio formats and bitrates are preferred'
        ],
        docsId: 'settings'
    },
    '.nav-button[data-page="issues"]': {
        title: 'Issues & Repair',
        description: 'Automated library health scanner that finds and fixes problems — dead files, missing covers, duplicates, incomplete albums, metadata gaps, and more. Each finding can be auto-fixed or dismissed.',
        tips: [
            'The nav badge shows pending issue count',
            'Run individual repair jobs or scan everything at once',
            'Auto-fix handles most issues; manual review for edge cases'
        ]
    },
    '.nav-button[data-page="help"]': {
        title: 'Help & Documentation',
        description: 'Comprehensive documentation covering every feature, complete API reference, workflow guides, and troubleshooting. Fully searchable.',
        docsId: 'getting-started'
    },

    // ─── SIDEBAR: PLAYER & STATUS ───────────────────────────────────

    '#media-player': {
        title: 'Media Player',
        description: 'Stream music directly from your media server. Play tracks from search results, library, or discovery playlists. Supports play/pause, seek, volume, and queue management.',
        tips: [
            'Click any track\'s play button anywhere in the app to start streaming',
            'Queue tracks from the Enhanced Library view or search results',
            'Integrates with your OS media controls (lock screen, system tray)'
        ],
        docsId: 'player'
    },
    '.version-button': {
        title: 'Version & Changelog',
        description: 'Shows the current SoulSync version. Click to see the full release notes, changelog, and what\'s new.',
    },
    '.support-button': {
        title: 'Support & Community',
        description: 'Links to the SoulSync community Discord, GitHub issues for bug reports, and documentation resources.',
    },
    '#spotify-indicator': {
        title: 'Metadata Source',
        description: 'Connection status of your primary metadata source. This service provides artist, album, and track information for searches, enrichment, and discovery.',
        tips: [
            'Green dot = connected and responding',
            'Red dot = disconnected or erroring',
            'iTunes and Deezer work without authentication; Spotify requires OAuth'
        ],
        docsId: 'gs-connecting'
    },
    '#media-server-indicator': {
        title: 'Media Server',
        description: 'Connection to your music server where your library lives. SoulSync reads your collection from here and triggers scans after new downloads.',
        tips: [
            'Supports Plex, Jellyfin, and Navidrome',
            'Configure in Settings → Media Server Setup',
            'Auto-scans your library after every successful download'
        ],
        docsId: 'set-media'
    },
    '#soulseek-indicator': {
        title: 'Download Source',
        description: 'Status of your active download source. Shows the primary source in your configuration — Soulseek, YouTube, Tidal, Qobuz, HiFi, or Deezer.',
        tips: [
            'Hybrid mode tries multiple sources in priority order',
            'Each streaming source has independent quality settings',
            'Configure source priority via drag-and-drop in Settings'
        ],
        docsId: 'search-sources'
    },

    // ─── DASHBOARD: HEADER BUTTONS ──────────────────────────────────

    '#watchlist-button': {
        title: 'Watchlist',
        description: 'Artists you\'re following for new releases. SoulSync periodically scans for new albums and singles from these artists and adds them to your Wishlist for download.',
        tips: [
            'Add artists from the Artists page or Library page',
            'Badge shows total watched artist count',
            'New releases trigger the "New Watchlist Release" automation event',
            'Watchlist scans also build the Discovery Pool for recommendations'
        ],
        docsId: 'art-watchlist'
    },
    '#wishlist-button': {
        title: 'Wishlist',
        description: 'Tracks queued for download. Failed downloads, watchlist new releases, and manually added tracks all land here. Process the wishlist to retry downloads.',
        tips: [
            'Badge shows total wishlist track count',
            'Click to open the wishlist modal with all pending tracks',
            'Process All starts downloading every wishlist item',
            'Tracks can be added manually or arrive from failed batch downloads'
        ],
        docsId: 'art-wishlist'
    },
    '#import-button': {
        title: 'Quick Import',
        description: 'Shortcut to the Import page. Drop music files in your staging folder and import them into your library with metadata matching and tagging.',
        docsId: 'import'
    },

    // ─── DASHBOARD: SERVICE CARDS ───────────────────────────────────

    '#spotify-service-card': {
        title: 'Metadata Source Status',
        description: 'Detailed connection info for your active metadata source. Shows connection state, response latency, and allows manual connection testing.',
        tips: [
            '"Test Connection" verifies the API is responding',
            'Response time indicates network latency to the service',
            'If stuck on "Checking...", the service may be rate-limited'
        ],
        docsId: 'gs-connecting'
    },
    '#media-server-service-card': {
        title: 'Media Server Status',
        description: 'Detailed connection info for your media server. Verifies SoulSync can communicate with Plex, Jellyfin, or Navidrome for library scanning and audio streaming.',
        tips: [
            '"Test Connection" verifies the server URL and credentials',
            'Select your Music Library in Settings after first connecting',
            'Navidrome auto-detects new files — no scan trigger needed'
        ],
        docsId: 'set-media'
    },
    '#soulseek-service-card': {
        title: 'Download Source Status',
        description: 'Connection status of your primary download source. For Soulseek, this checks the slskd API; for streaming sources, it verifies authentication.',
        tips: [
            '"Test Connection" confirms the source is ready for downloads',
            'Soulseek requires a running slskd instance with API key',
            'Streaming sources (Tidal, Qobuz) need active subscriptions'
        ],
        docsId: 'search-sources'
    },

    // ─── DASHBOARD: SYSTEM STATS ────────────────────────────────────

    '#active-downloads-card': {
        title: 'Active Downloads',
        description: 'Tracks currently being downloaded across all configured sources — Soulseek P2P transfers, YouTube audio extraction, and streaming source downloads.',
    },
    '#finished-downloads-card': {
        title: 'Finished Downloads',
        description: 'Completed downloads this session. These tracks have been processed through the full pipeline — verification, tagging, cover art, file organization, and media server scan.',
    },
    '#download-speed-card': {
        title: 'Download Speed',
        description: 'Aggregate download throughput across all active transfers. Speed depends on your sources — Soulseek varies by peer; streaming sources are typically consistent.',
    },
    '#active-syncs-card': {
        title: 'Active Syncs',
        description: 'Playlist sync operations currently in progress. Each sync matches tracks against your library, searches download sources for missing ones, and downloads them.',
    },
    '#uptime-card': {
        title: 'System Uptime',
        description: 'Time since last SoulSync restart. Background workers (metadata enrichment, watchlist scanner, repair jobs) run continuously during uptime.',
    },
    '#memory-card': {
        title: 'Memory Usage',
        description: 'RAM consumed by the SoulSync process. Includes web server, all background workers, metadata caches, and WebSocket connections.',
    },

    // ─── DASHBOARD: TOOL CARDS ──────────────────────────────────────

    '#db-updater-card': {
        title: 'Database Updater',
        description: 'Syncs your media server\'s library into SoulSync\'s database. Three modes: Incremental (fast, new content only), Full Refresh (rebuilds everything), and Deep Scan (finds stale entries).',
        tips: [
            'Run after adding music outside of SoulSync',
            'Incremental runs in seconds; Full Refresh takes longer',
            'Deep Scan removes tracks deleted from your media server'
        ],
        docsId: 'dashboard'
    },
    '#metadata-updater-card': {
        title: 'Metadata Enrichment',
        description: 'Background workers that enrich your library with data from 9 services — Spotify, MusicBrainz, Deezer, Last.fm, iTunes, AudioDB, Genius, Tidal, and Qobuz. Adds genres, bios, cover art, IDs, and more.',
        tips: [
            'Runs automatically at the configured interval',
            'Each service enriches different metadata fields',
            'Check coverage per-artist in the Library\'s Enhanced view'
        ],
        docsId: 'dashboard'
    },
    '#quality-scanner-card': {
        title: 'Quality Scanner',
        description: 'Analyzes audio files for quality integrity. Calculates bitrate density to detect transcodes (e.g., an MP3 re-encoded as FLAC). Scope options: Full Library, New Only, or Single Artist.',
        tips: [
            '"Quality Met" = file quality matches its format claims',
            '"Low Quality" = suspicious file flagged for review',
            'Matched count shows tracks with verified metadata'
        ],
        docsId: 'dashboard'
    },
    '#duplicate-cleaner-card': {
        title: 'Duplicate Cleaner',
        description: 'Scans your library for duplicate tracks by comparing title, artist, album, and file characteristics. Reviews duplicates before taking any action.',
        tips: [
            'Shows total space savings from cleanup',
            'Nothing is deleted without your review',
            'Safe to run regularly'
        ],
        docsId: 'dashboard'
    },
    '#discovery-pool-card': {
        title: 'Discovery Pool',
        description: 'Collection of tracks from similar artists discovered during watchlist scans. Matched tracks feed the Discover page\'s personalized playlists and genre browser. Failed matches can be fixed manually.',
        tips: [
            'Click "Open Discovery Pool" to review matched and failed tracks',
            '"Rematch" button on matched tracks lets you pick a different match',
            'Search filter helps find specific tracks in large pools'
        ],
        docsId: 'discover'
    },
    '#retag-tool-card': {
        title: 'Retag Tool',
        description: 'Queue of tracks needing metadata corrections. When enrichment detects better metadata than what\'s in your files, corrections appear here for batch review.',
        tips: [
            'Groups corrections by artist for efficient processing',
            'Preview all changes before applying',
            'Writes corrected tags directly to audio files'
        ]
    },
    '#media-scan-card': {
        title: 'Media Server Scan',
        description: 'Manually trigger a library scan on your media server. SoulSync auto-scans after downloads, but this is useful after bulk imports or external changes.',
        tips: [
            'Plex: triggers partial scan of music library section',
            'Jellyfin: triggers full library refresh task',
            'Navidrome: auto-detects changes, manual scan rarely needed'
        ]
    },
    '#backup-manager-card': {
        title: 'Backup Manager',
        description: 'Create and manage database backups. The backup includes all library metadata, settings, enrichment data, automation configs, and profiles — everything except audio files.',
        tips: [
            'Backup before major updates or settings changes',
            'Download backups for off-site copies',
            'Backups are stored in the database folder'
        ]
    },
    '#metadata-cache-card': {
        title: 'Metadata Cache Browser',
        description: 'Browse all cached API responses from metadata searches. Every artist, album, and track looked up across all services is stored here, speeding up future lookups and reducing API calls.',
        tips: [
            'Filter by source (Spotify, iTunes, Deezer) and entity type',
            'Cache grows automatically as you search and enrichment runs',
            'Feeds the Genre Explorer and other Discover page features'
        ]
    },

    // ─── WATCHLIST MODAL ──────────────────────────────────────────────

    '#watchlist-modal .playlist-modal-header': {
        title: 'Watchlist Header',
        description: 'Shows total watched artists and countdown to the next automatic scan. Auto-scans run on the interval configured in Automations.',
        tips: [
            'Artist count updates when you add/remove artists',
            'Auto timer resets after each completed scan'
        ],
        docsId: 'art-watchlist'
    },
    '#scan-watchlist-btn': {
        title: 'Scan for New Releases',
        description: 'Starts scanning all watchlisted artists for new albums, EPs, and singles. New releases are added to your Wishlist for download. Also updates the Discovery Pool with similar artist data.',
        tips: [
            'Scan checks each artist against your metadata source',
            'Live activity shows current artist and recently found tracks',
            'New releases trigger the "New Watchlist Release" automation event'
        ],
        docsId: 'art-watchlist'
    },
    '#cancel-watchlist-scan-btn': {
        title: 'Cancel Scan',
        description: 'Stops the current watchlist scan. Any releases found so far are kept — only remaining artists are skipped.',
    },
    '#update-similar-artists-btn': {
        title: 'Update Similar Artists',
        description: 'Refreshes the similar artist database for all watched artists. This data powers the Discovery Pool, genre explorer, and personalized playlists on the Discover page.',
        tips: [
            'Queries metadata sources for artists related to your watchlist',
            'Results appear in the Discovery Pool and feed Discover page features',
            'Runs automatically during watchlist scans, but this forces a refresh'
        ],
        docsId: 'discover'
    },
    '#watchlist-global-settings-btn': {
        title: 'Global Watchlist Settings',
        description: 'Override download preferences for ALL watchlisted artists at once. When enabled, these settings replace individual artist configurations. Useful for applying the same release type and content filters across your entire watchlist.',
        tips: [
            'Button shows "Global Override ON" when active',
            'Overrides individual artist settings while enabled',
            'Disable to return to per-artist configurations'
        ]
    },
    '.watchlist-artist-card': {
        title: 'Watched Artist',
        description: 'An artist on your watchlist. SoulSync monitors this artist for new releases and adds them to your Wishlist. Click the gear icon to configure which release types to monitor.',
        tips: [
            'Gear icon opens per-artist download preferences',
            'Configure which release types (Albums, EPs, Singles) to monitor',
            'Content filters control whether live, remix, acoustic versions are included'
        ]
    },

    // ─── WATCHLIST ARTIST CONFIG MODAL ──────────────────────────────

    '#watchlist-artist-config-modal .config-section:first-child': {
        title: 'Download Preferences',
        description: 'Choose which types of releases to watch for this artist. Checked types will be monitored during scans and added to your Wishlist when found.',
        tips: [
            'Albums: Full-length studio albums',
            'EPs: Extended plays (4-6 tracks)',
            'Singles: Individual tracks and 2-3 track releases'
        ]
    },
    '#watchlist-artist-config-modal .config-section:nth-child(2)': {
        title: 'Content Filters',
        description: 'Control which types of content to include or exclude when scanning for new releases. By default, live, remix, acoustic, compilation, and instrumental versions are all excluded — check the ones you want.',
        tips: [
            'Unchecked = excluded from scans (won\'t be added to wishlist)',
            'These filters apply during watchlist scans only',
            'Global Settings can override these per-artist filters'
        ]
    },
    '#config-include-live': {
        title: 'Include Live Versions',
        description: 'When checked, live performances, concert recordings, and live album versions will be included in watchlist scans. Default: excluded.',
    },
    '#config-include-remixes': {
        title: 'Include Remixes',
        description: 'When checked, remix versions, edits, and reworked tracks will be included. Default: excluded.',
    },
    '#config-include-compilations': {
        title: 'Include Compilations',
        description: 'When checked, greatest hits, best-of collections, and compilation albums will be included. Default: excluded.',
    },
    '#config-include-acoustic': {
        title: 'Include Acoustic Versions',
        description: 'When checked, acoustic, stripped-back, and unplugged versions will be included in watchlist scans. Default: excluded.',
    },
    '#config-include-instrumentals': {
        title: 'Include Instrumentals',
        description: 'When checked, instrumental, karaoke, and backing track versions will be included. Default: excluded.',
    },
    '#watchlist-linked-provider-section': {
        title: 'Linked Artist',
        description: 'Shows which metadata provider artist is linked to this watchlist entry. SoulSync uses this link to look up releases. If the wrong artist is linked, the scan will find incorrect releases.',
        tips: [
            'The linked artist is matched automatically when you add to watchlist',
            'If releases look wrong, the link may point to the wrong artist',
            'Remove and re-add the artist to force a fresh match'
        ]
    },
    '#save-artist-config-btn': {
        title: 'Save Preferences',
        description: 'Saves this artist\'s download preferences. Changes take effect on the next watchlist scan.',
    },

    // ─── WATCHLIST GLOBAL CONFIG MODAL ──────────────────────────────

    '#watchlist-global-config-modal': {
        title: 'Global Watchlist Settings',
        description: 'When global override is enabled, these settings apply to ALL watched artists, replacing their individual configurations. Useful for uniform preferences across your entire watchlist.',
        tips: [
            'Toggle "Enable Global Override" at the top to activate',
            'Same options as per-artist: release types + content filters',
            'Disable override to return to individual artist settings'
        ]
    },

    // ─── DASHBOARD: ACTIVITY FEED ───────────────────────────────────

    '#dashboard-activity-feed': {
        title: 'Activity Feed',
        description: 'Live stream of system events — downloads started/completed, sync progress, enrichment updates, automation triggers, errors, and more. Updates in real-time via WebSocket.',
        tips: [
            'Newest events appear at the top',
            'Events are timestamped and categorized by type',
            'The feed persists across page navigation within the session'
        ]
    },
};

// ── Docs Navigation Helper ───────────────────────────────────────────────

function _navigateToDocsSection(docsId) {
    dismissHelperPopover();
    toggleHelperMode();
    navigateToPage('help');

    // Wait for docs page to initialize, then simulate a nav click
    setTimeout(() => {
        // Try clicking the nav section title first (top-level like 'dashboard', 'sync')
        const navTitle = document.querySelector(`.docs-nav-section-title[data-target="${docsId}"]`);
        if (navTitle) {
            navTitle.click();
            return;
        }

        // Try clicking a child nav item (subsections like 'gs-connecting', 'set-media')
        const navChild = document.querySelector(`.docs-nav-child[data-target="${docsId}"]`);
        if (navChild) {
            // Expand parent section first
            const parentSection = navChild.closest('.docs-nav-section');
            if (parentSection) {
                const parentTitle = parentSection.querySelector('.docs-nav-section-title');
                if (parentTitle && !parentTitle.classList.contains('expanded')) {
                    parentTitle.click();
                }
            }
            setTimeout(() => navChild.click(), 200);
            return;
        }

        // Fallback: scroll to element by ID
        const el = document.getElementById(docsId) || document.getElementById('docs-' + docsId);
        if (el) {
            const docsContent = document.getElementById('docs-content');
            if (docsContent) {
                el.scrollIntoView({ behavior: 'smooth', block: 'start' });
            }
        }
    }, 600);
}

// ── Helper Mode Toggle ───────────────────────────────────────────────────

function toggleHelperMode() {
    helperModeActive = !helperModeActive;
    document.body.classList.toggle('helper-mode-active', helperModeActive);

    const floatBtn = document.getElementById('helper-float-btn');
    if (floatBtn) floatBtn.classList.toggle('active', helperModeActive);

    if (!helperModeActive) {
        dismissHelperPopover();
    }
}

// ── Click Interception ───────────────────────────────────────────────────

document.addEventListener('click', function(e) {
    if (!helperModeActive) return;

    // Allow clicking the helper button itself to toggle
    const floatBtn = document.getElementById('helper-float-btn');
    if (floatBtn && (e.target === floatBtn || floatBtn.contains(e.target))) {
        return;
    }

    // Allow clicking popover links/close
    if (_helperPopover && _helperPopover.contains(e.target)) {
        return;
    }

    e.preventDefault();
    e.stopPropagation();

    // Walk up the DOM tree to find a matching element
    let target = e.target;
    while (target && target !== document.body) {
        for (const selector of Object.keys(HELPER_CONTENT)) {
            try {
                if (target.matches(selector)) {
                    showHelperPopover(target, HELPER_CONTENT[selector]);
                    return;
                }
            } catch (err) { /* invalid selector */ }
        }
        target = target.parentElement;
    }

    // No match — dismiss
    dismissHelperPopover();
}, true);

// ── Escape Key ───────────────────────────────────────────────────────────

document.addEventListener('keydown', function(e) {
    if (e.key === 'Escape' && helperModeActive) {
        if (_helperPopover) {
            dismissHelperPopover();
        } else {
            toggleHelperMode();
        }
    }
});

// ── Popover Display ──────────────────────────────────────────────────────

function showHelperPopover(targetEl, content) {
    dismissHelperPopover();

    // Highlight target
    targetEl.classList.add('helper-highlight');
    _helperHighlighted = targetEl;

    // Build popover
    const popover = document.createElement('div');
    popover.className = 'helper-popover';

    let tipsHtml = '';
    if (content.tips && content.tips.length > 0) {
        tipsHtml = `<div class="helper-popover-tips">
            ${content.tips.map(t => `<div class="helper-popover-tip">${t}</div>`).join('')}
        </div>`;
    }

    let docsLink = '';
    if (content.docsId) {
        docsLink = `<div class="helper-popover-docs">
            <a href="#" onclick="event.preventDefault();_navigateToDocsSection('${content.docsId}')">
                View full documentation &rarr;
            </a>
        </div>`;
    }

    popover.innerHTML = `
        <div class="helper-popover-arrow"></div>
        <div class="helper-popover-header">
            <div class="helper-popover-title">${content.title}</div>
            <button class="helper-popover-close" onclick="dismissHelperPopover()">&times;</button>
        </div>
        <div class="helper-popover-desc">${content.description}</div>
        ${tipsHtml}
        ${docsLink}
    `;

    document.body.appendChild(popover);
    _helperPopover = popover;

    // Position
    requestAnimationFrame(() => positionPopover(popover, targetEl));
}

function positionPopover(popover, targetEl) {
    const rect = targetEl.getBoundingClientRect();
    const popRect = popover.getBoundingClientRect();
    const margin = 14;
    const arrowEl = popover.querySelector('.helper-popover-arrow');

    // Try right side first
    let left = rect.right + margin;
    let top = rect.top + (rect.height / 2) - (popRect.height / 2);
    let arrowSide = 'left';

    // If overflows right, try left
    if (left + popRect.width > window.innerWidth - 20) {
        left = rect.left - popRect.width - margin;
        arrowSide = 'right';
    }

    // If overflows left, try below
    if (left < 20) {
        left = rect.left + (rect.width / 2) - (popRect.width / 2);
        top = rect.bottom + margin;
        arrowSide = 'top';
    }

    // Clamp to viewport
    left = Math.max(12, Math.min(left, window.innerWidth - popRect.width - 12));
    top = Math.max(12, Math.min(top, window.innerHeight - popRect.height - 12));

    popover.style.left = left + 'px';
    popover.style.top = top + 'px';

    // Position arrow
    if (arrowEl) {
        arrowEl.className = 'helper-popover-arrow arrow-' + arrowSide;
    }

    // Animate in
    popover.classList.add('visible');
}

function dismissHelperPopover() {
    if (_helperPopover) {
        _helperPopover.remove();
        _helperPopover = null;
    }
    if (_helperHighlighted) {
        _helperHighlighted.classList.remove('helper-highlight');
        _helperHighlighted = null;
    }
}
