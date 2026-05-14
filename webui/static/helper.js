// ===============================
// INTERACTIVE CONTEXTUAL HELP SYSTEM V2
// ===============================

// ── State ────────────────────────────────────────────────────────────────

const HelperState = {
    mode: null,           // null | 'info' | 'tour' | 'search' | 'shortcuts' | 'setup' | 'whats-new' | 'troubleshoot'
    menuOpen: false,
    tourStep: 0,
    tourId: null,
    setupData: null,
};

let helperModeActive = false;
let _helperPopover = null;
let _helperHighlighted = null;
let _helperMenu = null;
let _tourOverlay = null;
let _setupPanel = null;
let _shortcutsOverlay = null;
let _helperSearchPanel = null;
let _troubleshootActive = false;

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
    '.nav-button[data-page="active-downloads"]': {
        title: 'Downloads',
        description: 'Centralized view of every download across the entire app. Shows live status for all tracks from Sync, Discover, Artists, Search, and Wishlist in one place.',
        tips: [
            'Filter by status: Active, Queued, Completed, Failed',
            'Badge on the nav button shows active download count from any page',
            'Clear Completed button removes finished items from the list'
        ]
    },
    '.nav-button[data-page="playlist-explorer"]': {
        title: 'Playlist Explorer',
        description: 'Visual exploration tool for playlists. Browse album art grids or full discographies from any playlist source. Select tracks to add to wishlist or download directly.',
        tips: [
            'Toggle between Albums view and Full Discog view',
            'Select multiple tracks across albums for batch operations',
            'Works with Spotify, Tidal, Deezer, and ListenBrainz playlists'
        ]
    },
    '.nav-button[data-page="stats"]': {
        title: 'Library Statistics',
        description: 'Detailed analytics — genre breakdowns, format distribution, quality analysis, collection growth, and enrichment coverage across all metadata services.',
        docsId: 'dashboard'
    },
    '.nav-button[data-page="import"]': {
        title: 'Music Import',
        description: 'Import music files from your import folder. SoulSync identifies tracks using AcoustID fingerprinting, matches them to metadata, and organizes them into your library with proper tagging.',
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
    '#metadata-source-indicator': {
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
        description: 'Shortcut to the Import page. Drop music files in your import folder and import them into your library with metadata matching and tagging.',
        docsId: 'import'
    },

    // ─── DASHBOARD: SERVICE CARDS ───────────────────────────────────

    '#metadata-source-service-card': {
        title: 'Metadata Source Status',
        description: 'Detailed connection info for your active metadata source. Shows connection state, response latency, and allows manual connection testing.',
        tips: [
            '"Test Connection" verifies the API is responding',
            'Response time indicates network latency to the service',
            'If stuck on "Checking...", the service may be rate-limited'
        ],
        docsId: 'gs-connecting',
        actions: [
            { label: 'Open Settings', onClick: () => navigateToPage('settings') },
            { label: 'View Docs', onClick: () => _navigateToDocsSection('gs-connecting') }
        ]
    },
    '#media-server-service-card': {
        title: 'Media Server Status',
        description: 'Detailed connection info for your media server. Verifies SoulSync can communicate with Plex, Jellyfin, or Navidrome for library scanning and audio streaming.',
        tips: [
            '"Test Connection" verifies the server URL and credentials',
            'Select your Music Library in Settings after first connecting',
            'Navidrome auto-detects new files — no scan trigger needed'
        ],
        docsId: 'set-media',
        actions: [
            { label: 'Open Settings', onClick: () => navigateToPage('settings') },
            { label: 'View Docs', onClick: () => _navigateToDocsSection('set-media') }
        ]
    },
    '#soulseek-service-card': {
        title: 'Download Source Status',
        description: 'Connection status of your primary download source. For Soulseek, this checks the slskd API; for streaming sources, it verifies authentication.',
        tips: [
            '"Test Connection" confirms the source is ready for downloads',
            'Soulseek requires a running slskd instance with API key',
            'Streaming sources (Tidal, Qobuz) need active subscriptions'
        ],
        docsId: 'search-sources',
        actions: [
            { label: 'Open Settings', onClick: () => { navigateToPage('settings'); setTimeout(() => typeof switchSettingsTab === 'function' && switchSettingsTab('downloads'), 400); } },
            { label: 'View Docs', onClick: () => _navigateToDocsSection('search-sources') }
        ]
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

    // ─── WISHLIST MODAL ───────────────────────────────────────────────

    '#wishlist-overview-modal .playlist-modal-header': {
        title: 'Wishlist Header',
        description: 'Shows total track count across all categories and countdown to the next automatic processing cycle. The wishlist alternates between Albums/EPs and Singles each cycle.',
        tips: [
            '"Next Auto" shows which category processes next and when',
            'Cycles alternate: Albums/EPs → Singles → Albums/EPs → ...',
            'Auto-processing is triggered by the Watchlist automation'
        ],
        docsId: 'art-wishlist'
    },
    '.wishlist-category-card[data-category="albums"]': {
        title: 'Albums & EPs',
        description: 'Tracks from full albums and EPs waiting to be downloaded. Click to view and manage individual tracks. "Next in Queue" means this category will be processed in the next automatic cycle.',
        tips: [
            'Click to see all album/EP tracks in the wishlist',
            'Mosaic background shows cover art from queued items',
            'Select individual tracks or use "Select All" for batch operations'
        ],
        docsId: 'art-wishlist'
    },
    '.wishlist-category-card[data-category="singles"]': {
        title: 'Singles',
        description: 'Individual tracks and single releases waiting to be downloaded. These come from failed single-track downloads, manual additions, or watchlist new release scans.',
        tips: [
            'Click to see all single tracks in the wishlist',
            'Singles are processed in alternating cycles with Albums/EPs',
            'Failed downloads from search automatically land here'
        ],
        docsId: 'art-wishlist'
    },
    '.wishlist-back-btn': {
        title: 'Back to Categories',
        description: 'Return to the category selection view showing Albums/EPs and Singles cards.',
    },
    '#wishlist-select-all-btn': {
        title: 'Select All',
        description: 'Toggle selection on all tracks in the current category. Selected tracks can be batch-removed or batch-downloaded.',
    },
    '#wishlist-batch-bar': {
        title: 'Batch Actions',
        description: 'Appears when tracks are selected. Shows selection count and provides batch operations like removing selected tracks from the wishlist.',
    },
    '.wishlist-batch-remove-btn': {
        title: 'Remove Selected',
        description: 'Removes all selected tracks from the wishlist. They will no longer be queued for download unless re-added.',
    },
    '#wishlist-download-btn': {
        title: 'Download Selection',
        description: 'Start downloading all tracks in the currently visible category. Uses your configured download sources with quality profile and fallback settings.',
        tips: [
            'Downloads use the same pipeline as manual searches',
            'Each track goes through post-processing (tagging, cover art, organization)',
            'Failed downloads return to the wishlist for retry'
        ]
    },
    '.playlist-modal-btn-danger': {
        title: 'Clear Wishlist',
        description: 'Removes ALL tracks from the wishlist across all categories. This action requires confirmation and cannot be undone.',
    },
    '.playlist-modal-btn-warning': {
        title: 'Cleanup Wishlist',
        description: 'Removes tracks that already exist in your library. Useful after manual imports or when tracks were downloaded outside of SoulSync.',
    },

    // ─── WISHLIST: TRACK LIST VIEW ─────────────────────────────────

    '.wishlist-category-header': {
        title: 'Category Header',
        description: 'Navigation and selection controls for the current wishlist category. Use the back button to return to the overview, or Select All to batch-manage tracks.',
    },
    '.wishlist-album-card': {
        title: 'Wishlist Album',
        description: 'An album with tracks waiting to be downloaded. Click the header to expand/collapse the track list. Use the checkbox to select all tracks in this album, or the trash icon to remove the entire album from the wishlist.',
        tips: [
            'Expand to see individual tracks and their status',
            'Checkbox selects all tracks in this album for batch operations',
            'Trash icon removes all of this album\'s tracks from the wishlist'
        ]
    },
    '.wishlist-track-item': {
        title: 'Wishlist Track',
        description: 'An individual track queued for download. Select with the checkbox for batch operations, or remove individually with the trash icon.',
    },

    // ─── DOWNLOAD MODAL (used across the entire app) ────────────────

    '.download-missing-modal-hero': {
        title: 'Download Modal',
        description: 'Shows album/playlist info and real-time download statistics. The stats update live as tracks are analyzed and downloaded.',
        tips: [
            'Total: number of tracks in this batch',
            'Found: tracks already in your library (skipped)',
            'Missing: tracks that need to be downloaded',
            'Downloaded: successfully completed downloads'
        ]
    },
    '.stat-total': {
        title: 'Total Tracks',
        description: 'Total number of tracks in this download batch. Includes both tracks already in your library and ones that need downloading.',
    },
    '.stat-found': {
        title: 'Found in Library',
        description: 'Tracks that already exist in your media server library. These are skipped — no need to download them again.',
    },
    '.stat-missing': {
        title: 'Missing Tracks',
        description: 'Tracks not found in your library that will be searched and downloaded from your configured sources.',
    },
    '.stat-downloaded': {
        title: 'Downloaded',
        description: 'Tracks successfully downloaded, processed, and added to your library in this session.',
    },
    '.download-tracks-title': {
        title: 'Track Analysis & Status',
        description: 'Detailed per-track breakdown showing library match status, download progress, and available actions for each track.',
        tips: [
            'Library Match: shows if the track already exists in your library',
            'Download Status: real-time progress for each track',
            'Actions: cancel individual downloads or view download candidates'
        ]
    },
    '.track-select-all': {
        title: 'Select/Deselect All',
        description: 'Toggle selection for all tracks. Deselected tracks will be skipped during download. Useful for downloading only specific tracks from an album.',
    },
    'tr[data-track-index]': {
        title: 'Track Row',
        description: 'A single track in the download batch. Shows track number, name, artist, duration, library match status, download progress, and available actions.',
        tips: [
            'Checkbox on the left: deselect to skip this track during download',
            'Library Match: green "Found" means it\'s already in your library, red "Missing" means it needs downloading',
            'Download Status updates in real-time: Searching → Downloading → Processing → Complete',
            'Actions column: cancel an active download or view alternative download candidates if the first choice fails'
        ]
    },
    '.track-match-status': {
        title: 'Library Match',
        description: 'Shows whether this track was found in your media server library. "Found" means it\'s already there; "Missing" means it needs to be downloaded.',
    },
    '.track-download-status': {
        title: 'Download Status',
        description: 'Real-time status for this track: Pending → Searching → Downloading → Processing → Complete or Failed.',
    },
    '.force-download-toggle': {
        title: 'Download Options',
        description: '"Force Download All" skips the library check and downloads every track regardless of whether it already exists. "Organize by Playlist" puts files in a playlist-named folder instead of the normal artist/album structure.',
        tips: [
            'Force Download: useful for re-downloading with different quality settings',
            'Playlist folder: creates Downloads/PlaylistName/Artist - Track.ext structure'
        ]
    },
    '[id^="begin-analysis-btn"]': {
        title: 'Begin Analysis',
        description: 'Starts the download process: first checks your library for existing tracks, then searches your download sources for missing ones, and downloads them with full post-processing.',
        tips: [
            'Analysis runs through every track in order',
            'Found tracks are marked green and skipped',
            'Missing tracks are searched and queued for download',
            'Post-processing includes tagging, cover art, and file organization'
        ]
    },

    '[id^="add-to-wishlist-btn"]': {
        title: 'Add to Wishlist',
        description: 'Adds all missing tracks from this batch to your Wishlist for later download. Useful when you want to queue tracks but not download them right now.',
        tips: [
            'Only missing tracks are added (already-owned tracks are skipped)',
            'Tracks appear in the Wishlist modal under the appropriate category',
            'The Wishlist auto-processes on a schedule via the Automations system'
        ]
    },
    '.download-control-btn.primary': {
        title: 'Download / Analyze',
        description: 'The main action button — starts library analysis and downloads missing tracks. Changes label based on current state (Begin Analysis → Download Missing → Complete).',
    },

    // ─── SYNC PAGE ───────────────────────────────────────────────────

    // Tabs
    '.sync-tab-button[data-tab="spotify"]': {
        title: 'Spotify Playlists',
        description: 'Your Spotify playlists. Select one or more and click "Start Sync" to download missing tracks. Requires Spotify OAuth connection in Settings.',
        tips: ['Click a playlist card to open the detail/download modal', 'Checkbox selects playlists for batch sync', 'Green badge = fully synced, blue = in progress'],
        docsId: 'sync-spotify'
    },
    '.sync-tab-button[data-tab="spotify-public"]': {
        title: 'Spotify Public Links',
        description: 'Load any public Spotify playlist or album by URL — no Spotify account needed. Paste the URL and click Load.',
        tips: ['Works with playlist and album URLs', 'No OAuth credentials required', 'Previously loaded URLs appear in the history bar'],
        docsId: 'sync-spotify-public'
    },
    '.sync-tab-button[data-tab="tidal"]': {
        title: 'Tidal Playlists',
        description: 'Your Tidal playlists. Import and sync playlists from your Tidal account. Requires Tidal authentication in Settings.',
        docsId: 'sync-tidal'
    },
    '.sync-tab-button[data-tab="deezer"]': {
        title: 'Deezer Playlists',
        description: 'Import Deezer playlists by URL. Paste a playlist URL, load it, then discover and sync tracks.',
        docsId: 'sync-deezer'
    },
    '.sync-tab-button[data-tab="youtube"]': {
        title: 'YouTube Playlists',
        description: 'Import YouTube Music playlists by URL. Tracks go through the discovery pipeline to match official metadata before downloading.',
        tips: ['Paste any YouTube Music playlist URL', 'Discovery matches video titles to official tracks', 'Unmatched tracks can be fixed manually'],
        docsId: 'sync-youtube'
    },
    '.sync-tab-button[data-tab="beatport"]': {
        title: 'Beatport Charts',
        description: 'Browse Beatport charts, genres, and curated playlists. Find electronic music by genre, chart type, or editorial picks.',
        tips: ['Browse 12+ electronic genres', 'Top 100 and Hype charts with full track listings', 'Tracks can be matched to Spotify for metadata'],
        docsId: 'sync-beatport'
    },
    '.sync-tab-button[data-tab="import-file"]': {
        title: 'Import from File',
        description: 'Import track lists from CSV, TSV, or plain text files. Drag and drop or browse for a file, map columns, then create a playlist for sync.',
        tips: ['Supports CSV, TSV, and plain text (one track per line)', 'Column mapping for CSV/TSV files', 'Creates a mirrored playlist for persistent state'],
        docsId: 'sync-import-file'
    },
    '.sync-tab-button[data-tab="mirrored"]': {
        title: 'Mirrored Playlists',
        description: 'All imported playlists from every source, saved persistently. Shows discovery status, download progress, and allows re-syncing.',
        tips: ['Every parsed playlist is automatically mirrored here', 'Cards show live state: Discovering, Discovered, Syncing, Complete', 'Re-parsing the same URL updates the existing mirror'],
        docsId: 'sync-mirrored'
    },
    '.sync-tab-button[data-tab="server"]': {
        title: 'Server Playlists',
        description: 'View and manage playlists from your connected media server (Plex, Jellyfin, or Navidrome). Compare server-side playlists with source playlists to find differences.',
        tips: [
            'Two-column layout: source playlist vs server playlist',
            'Disambiguation overlay helps match tracks when names differ',
            'Useful for verifying sync completeness against your media server'
        ]
    },
    '.sync-tab-button[data-tab="listenbrainz"]': {
        title: 'ListenBrainz Playlists',
        description: 'Import playlists from ListenBrainz — community-generated playlists, weekly discoveries, and your own ListenBrainz playlists.',
        tips: ['Paste any ListenBrainz playlist URL', 'Supports weekly exploration and community playlists', 'Tracks are resolved via MusicBrainz recording IDs'],
    },

    // Sync page header & history
    '.sync-history-btn': {
        title: 'Sync History',
        description: 'View a log of all sync operations — playlist syncs, album downloads, and wishlist processing. Shows timestamps, track counts, and completion status.',
        docsId: 'sync-history'
    },
    '.sync-header': {
        title: 'Playlist Sync',
        description: 'Import and sync playlists from multiple sources. Select playlists, match tracks to your library, and download what\'s missing.',
        docsId: 'sync-overview'
    },

    // Spotify tab elements
    '#spotify-refresh-btn': {
        title: 'Refresh Playlists',
        description: 'Reload your Spotify playlists from the API. Use when you\'ve created or modified playlists in Spotify and they\'re not showing here.',
    },
    '.playlist-card': {
        title: 'Playlist Card',
        description: 'A playlist from your connected account. Click to open the detail view with track listing and download options. Use the checkbox to select for batch sync.',
        tips: ['Status badge shows sync state (synced, in progress, new)', 'Click the card to open the download modal', 'Select multiple with checkboxes, then click Start Sync'],
    },

    // URL input sections
    '#youtube-url-input': {
        title: 'YouTube URL Input',
        description: 'Paste a YouTube Music playlist URL here. Click "Parse Playlist" or press Enter to import the tracks.',
        docsId: 'sync-youtube'
    },
    '#deezer-url-input': {
        title: 'Deezer URL Input',
        description: 'Paste a Deezer playlist URL here. Click "Load Playlist" or press Enter to import the tracks.',
        docsId: 'sync-deezer'
    },
    '#spotify-public-url-input': {
        title: 'Spotify Public URL',
        description: 'Paste any public Spotify playlist or album URL. No Spotify account needed — works with share links.',
        docsId: 'sync-spotify-public'
    },

    // Playlist card action buttons
    '.playlist-card-action-btn': {
        title: 'Playlist Action',
        description: 'The action depends on the playlist state: "Discover" matches tracks to metadata, "Sync" downloads missing tracks, "Download" processes the playlist.',
    },
    '.youtube-playlist-card': {
        title: 'Imported Playlist',
        description: 'An imported playlist card. Shows track count, discovery status, and sync progress. Click the action button to advance to the next step.',
        tips: ['Progress shows: total tracks / matched / failed / percentage', 'Phase colors: gray=fresh, blue=discovering, green=discovered, orange=syncing'],
    },

    // Sidebar
    '.sync-sidebar': {
        title: 'Sync Actions',
        description: 'Select playlists from the left panel, then use these controls to start syncing. Progress and logs appear below.',
        docsId: 'sync-overview'
    },
    '#start-sync-btn': {
        title: 'Start Sync',
        description: 'Begin downloading missing tracks from all selected playlists. Playlists are processed sequentially — each one completes before the next starts.',
        tips: ['Select playlists first using checkboxes on the cards', 'Progress bar and log update in real-time', 'Button is disabled until at least one playlist is selected'],
    },
    '#sync-log-area': {
        title: 'Sync Log',
        description: 'Live log of sync operations. Shows each track as it\'s matched, downloaded, or skipped. Auto-scrolls to show the latest activity.',
    },

    // Import file elements
    '#import-file-dropzone': {
        title: 'File Drop Zone',
        description: 'Drag and drop a CSV, TSV, or text file here, or click to browse. The file will be parsed and previewed before importing.',
        docsId: 'sync-import-file'
    },
    '#import-file-import-btn': {
        title: 'Import as Playlist',
        description: 'Creates a mirrored playlist from the parsed file. Give it a name and click Import — the playlist will appear in the Mirrored tab for discovery and sync.',
    },

    // Beatport elements
    '.beatport-chart-item': {
        title: 'Beatport Chart',
        description: 'A Beatport chart or playlist. Click to view tracks and download. Charts are cached and refreshed daily.',
        docsId: 'sync-beatport'
    },
    '.beatport-genre-item': {
        title: 'Beatport Genre',
        description: 'Click to explore this genre\'s charts, top tracks, staff picks, and new releases.',
        docsId: 'sync-beatport'
    },
    '#beatport-top100-btn': {
        title: 'Beatport Top 100',
        description: 'Load the Beatport Top 100 overall chart — the most popular tracks across all genres.',
    },

    // Mirrored tab
    '.pool-trigger-btn': {
        title: 'Discovery Pool',
        description: 'Open the Discovery Pool to view matched and failed track discoveries across all mirrored playlists. Fix failed matches manually.',
        docsId: 'sync-discovery'
    },
    '#mirrored-refresh-btn': {
        title: 'Refresh Mirrored',
        description: 'Reload all mirrored playlists from the database.',
    },

    // ─── DISCOVERY MODAL (used by YouTube, Tidal, Deezer, Beatport, ListenBrainz, Mirrored) ───

    '.youtube-discovery-modal .modal-header': {
        title: 'Discovery Modal Header',
        description: 'Shows the playlist name, track count, and current phase description. The discovery pipeline matches raw track titles from the source to official metadata on your configured metadata service.',
        docsId: 'sync-discovery'
    },
    '.progress-section': {
        title: 'Discovery Progress',
        description: 'Real-time progress of the track matching process. Each track from the source playlist is compared against your metadata service (Spotify, iTunes, or Deezer) using fuzzy matching with a 0.7 confidence threshold.',
        tips: [
            'Green progress = tracks successfully matched',
            'Progress text shows matched/total count',
            'Matching runs server-side — you can close the modal and it continues'
        ],
        docsId: 'sync-discovery'
    },
    '.discovery-table-container': {
        title: 'Discovery Results Table',
        description: 'Shows each source track alongside its matched metadata result. Green rows = matched, red = failed, gray = pending. Failed matches can be fixed manually.',
        tips: [
            'Source columns show the original track/artist from the playlist',
            'Matched columns show the official metadata found',
            'Status shows confidence score for each match',
            'Actions column: "Fix Match" lets you manually search for the correct track'
        ]
    },
    '.discovery-fix-modal-overlay': {
        title: 'Fix Track Match',
        description: 'Manually search for the correct track when automatic matching fails. Edit the track name and artist, search, then select the right result.',
        tips: [
            'Edit the search terms to improve results',
            'Results come from your active metadata source',
            'Selecting a match updates the discovery cache for future use'
        ]
    },
    '[id^="youtube-discovery-modal"] .modal-footer': {
        title: 'Discovery Actions',
        description: 'Action buttons change based on the current phase. "Start Discovery" begins matching, "Sync to Wishlist" queues matched tracks for download, "Download Missing" starts downloading immediately.',
        tips: [
            'Discovery: matches source tracks to official metadata',
            'Sync: adds matched tracks to your wishlist',
            'Download: searches your download sources and downloads missing tracks',
            'You can close the modal — operations continue in the background'
        ]
    },

    // ─── SEARCH / DOWNLOADS PAGE ────────────────────────────────────

    // Header & Mode Toggle
    '.downloads-header': {
        title: 'Music Downloads',
        description: 'Search for music across your configured metadata sources and download from Soulseek, YouTube, Tidal, Qobuz, HiFi, or Deezer.',
        docsId: 'search'
    },
    '#enh-source-row': {
        title: 'Search Source Icons',
        description: 'Each icon is a metadata source. The highlighted one is what your next search will target — defaults to your configured primary source on page load. Click a different icon to search or switch to that source; a small dot on the icon marks sources that already have cached results for the current query.',
        tips: [
            'Typing searches only the highlighted source — no more silent fan-out across every provider',
            'Switching to an already-cached source is instant, no re-fetch',
            'The Soulseek icon routes to the raw-file search (same as the old Basic Search)',
            'Music Videos queries YouTube for downloadable music video files',
            'An amber border on a source means the backend fell back to a different provider for you (usually because Spotify is rate-limited)'
        ],
        docsId: 'search-enhanced'
    },

    // Enhanced Search
    '.enhanced-search-input-wrapper': {
        title: 'Search Bar',
        description: 'Type an artist, album, or track name. Results appear in categorized sections: Library Artists, Artists, Albums, Singles & EPs, and Tracks. Only the source highlighted in the icon row above is queried — click another icon to switch.',
        tips: [
            'Click an album to open the download modal',
            'Click a track to search your download source',
            'Play button previews tracks from your download source',
            'Switch sources via the icon row above — results are cached per query'
        ],
        docsId: 'search-enhanced'
    },
    '#enh-db-artists-section': {
        title: 'Library Artists',
        description: 'Artists from your local music library that match the search. Click to view their collection on the Library page.',
    },
    '#enh-spotify-artists-section': {
        title: 'Artists',
        description: 'Artists from your metadata source matching the search. Click one to open their discography.',
    },
    '#enh-albums-section': {
        title: 'Albums',
        description: 'Full-length albums matching the search. Click to open the download modal where you can select tracks and start downloading. "In Library" badge means you already own it.',
        docsId: 'search-downloading'
    },
    '#enh-singles-section': {
        title: 'Singles & EPs',
        description: 'Singles and EPs matching the search. Same as albums — click to open the download modal.',
        docsId: 'search-downloading'
    },
    '#enh-tracks-section': {
        title: 'Tracks',
        description: 'Individual tracks matching the search. Click to search your download source for that specific track. Play button streams a preview. "In Library" badge means it\'s already in your collection.',
        docsId: 'search-downloading'
    },

    // Basic Search
    '#basic-search-section .search-bar-container': {
        title: 'Basic Search',
        description: 'Direct search query sent to Soulseek. Enter artist name, song title, or any keywords. Results show raw P2P file listings.',
        docsId: 'search-basic'
    },
    '#filter-toggle-btn': {
        title: 'Filters',
        description: 'Toggle the filter panel to narrow results by type (Albums/Singles), format (FLAC/MP3/OGG/AAC/WMA), and sort order.',
        docsId: 'search-basic'
    },
    '#filter-content': {
        title: 'Search Filters',
        description: 'Filter and sort Soulseek results. Type filters hide non-matching results. Format filters show only specific audio formats. Sort reorders by relevance, quality, bitrate, size, speed, or name.',
        tips: [
            'Type: All, Albums (grouped results), or Singles (individual files)',
            'Format: FLAC for lossless, MP3 for compressed, or specific formats',
            'Sort: Relevance uses the matching engine score; Quality uses bitrate density'
        ],
        docsId: 'search-basic'
    },
    '.search-status-container': {
        title: 'Search Status',
        description: 'Shows the current search state — ready, searching, or results count. The spinner animates while Soulseek is being queried.',
    },
    '#search-results-area': {
        title: 'Search Results',
        description: 'Raw Soulseek results grouped by album or listed individually. Each result shows filename, format, bitrate, quality score, file size, uploader name, upload speed, and availability.',
        tips: [
            'Click a result to start downloading',
            'Album results group files from the same folder',
            'Quality score combines format, bitrate, peer speed, and availability',
            'Green = high quality, Yellow = medium, Red = low'
        ],
        docsId: 'search-basic'
    },

    // (Download Manager side-panel was retired — see the dedicated Downloads page)

    // ─── DISCOVER PAGE ────────────────────────────────────────────────

    // Hero
    '.discover-hero': {
        title: 'Featured Artists',
        description: 'Rotating showcase of recommended artists from your watchlist and discovery pool. Navigate with arrows or dot indicators.',
        tips: [
            '"View Discography" opens the artist on the Artists page',
            '"Add to Watchlist" monitors them for new releases',
            '"Watch All" adds all featured artists to your watchlist at once',
            '"View Recommended" opens a full list of recommended artists'
        ],
        docsId: 'disc-hero'
    },
    '#discover-hero-discography': {
        title: 'View Discography',
        description: 'Navigate to the Artists page and load this artist\'s full album, single, and EP discography for browsing and downloading.',
    },
    '#discover-hero-add': {
        title: 'Add to Watchlist',
        description: 'Add this artist to your Watchlist. SoulSync will scan for their new releases and add them to your Wishlist for download.',
    },
    '#discover-hero-watch-all': {
        title: 'Watch All',
        description: 'Add ALL featured artists from the hero slider to your Watchlist in one click.',
    },
    '#discover-hero-view-all': {
        title: 'View Recommended',
        description: 'Open a modal showing all recommended artists — not just the ones in the hero slider. Browse, add to watchlist, or view discographies.',
    },

    // Recent Releases
    '#recent-releases-carousel': {
        title: 'Recent Releases',
        description: 'New albums and singles from artists you follow. These are found during watchlist scans. Click any release to open the download modal.',
        docsId: 'disc-hero'
    },

    // Seasonal
    '#seasonal-albums-section': {
        title: 'Seasonal Albums',
        description: 'Albums curated for the current season based on mood, genre, and release timing. Refreshes with each season change.',
        docsId: 'disc-seasonal'
    },
    '#seasonal-playlist-section': {
        title: 'Seasonal Mix',
        description: 'A curated playlist of tracks matching the current season\'s vibe. Download missing tracks or sync to your media server.',
        docsId: 'disc-seasonal'
    },

    // Personalized Playlists
    '#personalized-popular-picks': {
        title: 'Popular Picks',
        description: 'Trending tracks from your discovery pool artists. These are the most popular songs from artists similar to the ones you follow.',
        tips: ['Download or Sync buttons queue tracks for your library', 'Tracks come from the discovery pool (built during watchlist scans)'],
        docsId: 'disc-playlists'
    },
    '#personalized-hidden-gems': {
        title: 'Hidden Gems',
        description: 'Rare and deeper cuts from your discovery pool artists. Lower popularity tracks that you might not find on mainstream playlists.',
        docsId: 'disc-playlists'
    },
    '#personalized-discovery-shuffle': {
        title: 'Discovery Shuffle',
        description: 'Random tracks from your entire discovery pool — different every time you load. A surprise mix for when you want something new.',
        docsId: 'disc-playlists'
    },

    // Curated Playlists
    '#release-radar-playlist': {
        title: 'Fresh Tape',
        description: 'New releases from recent additions to your library and discovery pool. Refreshes regularly with the latest drops.',
        docsId: 'disc-playlists'
    },
    '#discovery-weekly-playlist': {
        title: 'The Archives',
        description: 'Curated selection from your full collection — a weekly-style playlist that highlights tracks across your library.',
        docsId: 'disc-playlists'
    },

    // Build a Playlist — section container and all inner elements
    '.build-playlist-container': {
        title: 'Build a Playlist',
        description: 'Create a custom playlist by selecting seed artists. SoulSync finds similar artists, pulls their albums, and assembles a 50-track playlist mixing your picks with new discoveries.',
        tips: [
            'Search and select 1-5 seed artists',
            'Hit Generate for a fresh playlist every time',
            'The more seed artists, the more variety in the playlist'
        ],
        docsId: 'disc-build'
    },
    '#bp-info-panel': {
        title: 'How Build a Playlist Works',
        description: 'Search for seed artists → SoulSync finds similar artists → pulls their albums → picks random tracks → creates a 50-track playlist. More seed artists = more variety.',
        docsId: 'disc-build'
    },
    '#build-playlist-search': {
        title: 'Artist Search',
        description: 'Search for artists to include in your custom playlist. Select multiple artists and generate a playlist of their top tracks.',
        tips: [
            'Search and click artists to add them to your selection',
            'Selected artists appear below the search with remove buttons',
            'Click "Generate Playlist" when you\'ve chosen your artists'
        ],
        docsId: 'disc-build'
    },
    '#build-playlist-generate-btn': {
        title: 'Generate Playlist',
        description: 'Creates a playlist from top tracks of all your selected artists. The playlist can then be downloaded or synced to your media server.',
    },
    '#build-playlist-results-wrapper': {
        title: 'Generated Playlist',
        description: 'Your custom-built playlist. Download missing tracks or sync to your media server. Tracks are sorted by popularity across the selected artists.',
    },

    // Cache-based Discovery Sections
    '#cache-genre-explorer': {
        title: 'Genre Explorer',
        description: 'Browse music by genre across all your metadata sources. Click any genre pill to open a deep dive with artists, albums, tracks, and related genres.',
        tips: [
            'Genres are weighted: library and discovery pool count more than cache',
            '"New" badge means this genre isn\'t in your library yet',
            'Data comes from Spotify, iTunes, and Deezer caches combined'
        ],
        docsId: 'discover'
    },
    '#cache-undiscovered': {
        title: 'Undiscovered Albums',
        description: 'Albums from cached artists that you don\'t have in your library. A great way to find new music from artists you\'ve already searched for.',
    },
    '#cache-genre-releases': {
        title: 'Genre New Releases',
        description: 'Recently released albums matching your top library genres. Found in the metadata cache from recent searches.',
    },
    '#cache-label-explorer': {
        title: 'Label Explorer',
        description: 'Albums grouped by record label. Discover new music from labels whose artists you already enjoy.',
    },
    '#cache-deep-cuts': {
        title: 'Deep Cuts',
        description: 'Low-popularity tracks from artists in your metadata cache. These are the album tracks that never became singles — often the most interesting finds.',
    },

    // ListenBrainz — match both the tabs container and the parent section
    '#listenbrainz-tabs': {
        title: 'ListenBrainz Playlists',
        description: 'Playlists from your ListenBrainz account. Three categories: "Created For You" (algorithmic), "Your Playlists" (manually created), and "Collaborative" (shared).',
        tips: [
            'Requires ListenBrainz connection in Settings',
            'Click any playlist to view tracks and download',
            'Refresh button reloads from ListenBrainz API'
        ],
        docsId: 'sync-listenbrainz'
    },
    '#listenbrainz-tab-content': {
        title: 'ListenBrainz Playlist Content',
        description: 'Track listings for the selected ListenBrainz playlist. Click a track to download or stream it.',
        docsId: 'sync-listenbrainz'
    },
    '#listenbrainz-refresh-btn': {
        title: 'Refresh ListenBrainz',
        description: 'Reload playlists from your ListenBrainz account. Fetches the latest "Created For You", personal, and collaborative playlists.',
    },
    '.listenbrainz-tab': {
        title: 'ListenBrainz Tab',
        description: 'Switch between playlist categories: "Created For You" (algorithm-generated), "Your Playlists" (manually created), and "Collaborative" (shared with others).',
    },

    // Time Machine — match tabs, tab contents, and individual tabs
    '#decade-tabs': {
        title: 'Time Machine',
        description: 'Browse music by decade — from the 1950s to the 2020s. Each tab shows top tracks from your discovery pool artists active in that era.',
        tips: [
            'Download or Sync buttons queue decade tracks for your library',
            'Tracks come from discovery pool artists with releases in that decade'
        ],
        docsId: 'disc-timemachine'
    },
    '#decade-tab-contents': {
        title: 'Decade Tracks',
        description: 'Tracks from the selected decade. Download missing tracks or sync them to your media server.',
        docsId: 'disc-timemachine'
    },
    '.decade-tab': {
        title: 'Decade Tab',
        description: 'Click to browse music from this decade. Shows top tracks from your discovery pool artists who released music in this era.',
        docsId: 'disc-timemachine'
    },

    // Browse by Genre (discovery pool tabs)
    '#genre-tabs': {
        title: 'Browse by Genre',
        description: 'Genre-filtered playlists from your discovery pool. Each tab shows tracks matching that genre from artists in your discovery pool.',
        tips: [
            'Genres are consolidated from Spotify/iTunes categories',
            'Download or Sync buttons queue genre tracks for download',
            'Requires discovery pool data (run a watchlist scan first)'
        ],
        docsId: 'discover'
    },
    '#genre-tab-contents': {
        title: 'Genre Tracks',
        description: 'Tracks from the selected genre. Download or sync to add them to your library.',
    },
    '.genre-tab': {
        title: 'Genre Tab',
        description: 'Click to browse tracks in this genre from your discovery pool.',
    },

    // Playlist Sync/Download buttons (generic — matches all discover playlist sections)
    '.discover-section-actions .action-button.primary': {
        title: 'Sync to Media Server',
        description: 'Start syncing this playlist — matches tracks to your library, searches download sources for missing ones, and downloads them. Progress shows matched, pending, and failed counts.',
    },
    '.discover-section-actions .action-button.secondary': {
        title: 'Download Missing',
        description: 'Opens the download modal for this playlist. Review tracks, select which ones to download, and start the download process.',
    },

    // Daily Mixes
    '#daily-mixes-grid': {
        title: 'Daily Mixes',
        description: 'Personalized mixes generated from your listening patterns. Each mix focuses on a different aspect of your taste — genre clusters, mood, or artist groups.',
    },

    // ─── ARTIST DETAIL PAGE ───────────────────────────────────────────
    // (The standalone /artist-detail page is the unified destination for
    // both library and metadata-source artists. The inline /artists page
    // was retired in the unification project.)

    '.album-card': {
        title: 'Release Card',
        description: 'An album, single, or EP from this artist. Click to open the download modal with track selection, library matching, and download controls.',
        tips: [
            'Big-photo cover art fills the card with title and year overlaid at the bottom',
            'Completion badge (top-right) shows ownership status: ✓ Owned / N/M / Missing',
            'Library artists check ownership in the background — badge starts as "Checking…" then resolves'
        ]
    },
    '.completion-overlay': {
        title: 'Completion Badge',
        description: 'Top-right badge showing ownership state for library artists. ✓ Owned = full match, N/M = partial (owned/total tracks), Missing = no match. Source artists don\'t show this badge.',
    },
    '#ad-similar-artists-section': {
        title: 'Similar Artists',
        description: 'Artists with a similar sound, fetched from MusicMap by name. Works for both library and source artists. Click any bubble to navigate to that artist\'s detail page.',
        tips: [
            'Bubbles load progressively',
            'Click navigates to the standalone artist-detail page'
        ],
        docsId: 'art-detail'
    },
    '.similar-artist-bubble': {
        title: 'Similar Artist',
        description: 'An artist similar to the one you\'re viewing. Click to load their discography and browse their releases.',
    },
    // (Search source picker annotation lives under `#enh-source-row` above —
    //  the old `.search-source-picker-container` dropdown is gone.)

    // ─── AUTOMATIONS PAGE ─────────────────────────────────────────────

    // List View
    '#automations-list-view': {
        title: 'Automations List',
        description: 'All your automations — system and custom. Each card shows the trigger → action → then flow, run status, and controls.',
        docsId: 'auto-overview'
    },
    '.auto-new-btn': {
        title: 'New Automation',
        description: 'Open the visual builder to create a new automation. Choose a trigger (WHEN), an action (DO), and optional notifications (THEN).',
        docsId: 'auto-builder'
    },
    '#auto-filter-search': {
        title: 'Search Automations',
        description: 'Filter the list by name, trigger type, or action type. Matches are highlighted as you type.',
    },
    '#auto-filter-trigger': {
        title: 'Filter by Trigger',
        description: 'Show only automations with a specific trigger type (Schedule, Daily, Weekly, Event-based, Signal).',
    },
    '#auto-filter-action': {
        title: 'Filter by Action',
        description: 'Show only automations with a specific action type (Library Scan, Watchlist Scan, Process Wishlist, etc.).',
    },
    '#automations-stats': {
        title: 'Automation Stats',
        description: 'Quick overview: total active automations, system automations (built-in), and custom automations you\'ve created.',
    },

    // Automation Cards
    '.automation-card': {
        title: 'Automation',
        description: 'A single automation showing its trigger → action → notification flow. Use the controls on the right to run, edit, enable/disable, duplicate, or delete.',
        tips: [
            'Green dot = enabled and running on schedule',
            'Gray dot = disabled',
            'Blue dot = currently executing',
            'Click the run count to view execution history'
        ],
        docsId: 'auto-overview'
    },
    '.automation-flow': {
        title: 'Automation Flow',
        description: 'Visual representation of this automation: WHEN (trigger) → DO (action) → THEN (notification/signal). Each step shows its type and configuration.',
    },
    '.automation-run-btn': {
        title: 'Run Now',
        description: 'Execute this automation immediately, regardless of its schedule. The automation runs as if its trigger just fired.',
    },
    '.automation-toggle': {
        title: 'Enable/Disable',
        description: 'Toggle this automation on or off. Disabled automations keep their configuration but won\'t trigger.',
    },
    '.automation-edit-btn': {
        title: 'Edit',
        description: 'Open this automation in the visual builder to modify its trigger, action, or notification settings.',
    },
    '.automation-dupe-btn': {
        title: 'Duplicate',
        description: 'Create a copy of this automation with all the same settings. Useful for creating variations of existing workflows.',
    },
    '.automation-delete-btn': {
        title: 'Delete',
        description: 'Permanently delete this automation. Requires confirmation. Cannot be undone.',
    },
    '.auto-runs-link': {
        title: 'Run History',
        description: 'Click to view the execution history for this automation — timestamps, duration, status, and detailed logs for each run.',
        docsId: 'auto-history'
    },
    '.auto-group-btn': {
        title: 'Group',
        description: 'Assign this automation to a group for organization. Groups appear as collapsible sections in the list. Create new groups or assign to existing ones.',
    },

    // Automation Hub
    '#auto-section-hub': {
        title: 'Automation Hub',
        description: 'Guides, recipes, and reference material for building automations. Pipelines are pre-built workflow templates, recipes are common patterns, and guides explain concepts.',
        docsId: 'auto-overview'
    },
    '.auto-hub-tab[data-tab="pipelines"]': {
        title: 'Pipelines',
        description: 'Pre-built multi-step workflow templates. Each pipeline deploys several linked automations that work together — like a complete "new release → download → notify" chain.',
    },
    '.auto-hub-tab[data-tab="recipes"]': {
        title: 'Recipes',
        description: 'Single-automation patterns for common tasks. Quick one-click creation of popular automations.',
    },
    '.auto-hub-tab[data-tab="guides"]': {
        title: 'Guides',
        description: 'Step-by-step walkthroughs explaining how to build specific workflows and use advanced features like signals and conditions.',
    },
    '.auto-hub-tab[data-tab="tips"]': {
        title: 'Tips & Tricks',
        description: 'Best practices, performance tips, and common pitfalls when building automations.',
    },
    '.auto-hub-tab[data-tab="reference"]': {
        title: 'Reference',
        description: 'Complete list of all available triggers, actions, and then-actions with their configuration options.',
        docsId: 'auto-triggers'
    },

    // Builder View
    '#automations-builder-view': {
        title: 'Automation Builder',
        description: 'Visual editor for creating and editing automations. Drag blocks from the sidebar into the WHEN → DO → THEN flow slots.',
        docsId: 'auto-builder'
    },
    '#builder-name': {
        title: 'Automation Name',
        description: 'Give your automation a descriptive name. This appears in the list view and notifications.',
    },
    '#builder-group-name': {
        title: 'Group',
        description: 'Optionally assign this automation to a group. Groups organize automations into collapsible sections.',
    },
    '#builder-sidebar': {
        title: 'Block Library',
        description: 'Available triggers, actions, and then-actions. Drag a block to the canvas, or click to place it in the next empty slot.',
        tips: [
            'Triggers (WHEN): Schedule, Daily Time, Weekly Time, Events, Signals',
            'Actions (DO): Library Scan, Watchlist Scan, Process Wishlist, and more',
            'Then (THEN): Discord, Pushbullet, Telegram, Gotify, Fire Signal'
        ],
        docsId: 'auto-triggers'
    },
    '#slot-when': {
        title: 'WHEN — Trigger',
        description: 'Drop a trigger here to define WHEN this automation fires. Options: on a schedule, at a specific time, when an event occurs, or when a signal is received.',
        docsId: 'auto-triggers'
    },
    '#slot-do': {
        title: 'DO — Action',
        description: 'Drop an action here to define WHAT happens when the trigger fires. Options: scan library, check watchlist, process wishlist, refresh playlists, and more.',
        docsId: 'auto-actions'
    },
    '[id^="slot-then"]': {
        title: 'THEN — Notification/Signal',
        description: 'Drop a then-action here to define what happens AFTER the action completes. Send notifications via Discord, Pushbullet, Telegram, or fire a signal to chain automations.',
        tips: [
            'Up to 3 THEN actions per automation',
            'Signals let you chain automations together',
            'Message templates support variables: {time}, {name}, {status}'
        ],
        docsId: 'auto-then'
    },
    '.block-item': {
        title: 'Automation Block',
        description: 'A trigger, action, or notification type. Drag to a flow slot, or click to auto-place. The ? button shows detailed help for each block type.',
    },
    '.placed-block': {
        title: 'Placed Block',
        description: 'A configured block in the flow. Click the X to remove it. Configure options using the fields below the block.',
    },
    '.btn-save': {
        title: 'Save Automation',
        description: 'Save this automation. It will appear in the list view and start running according to its trigger configuration.',
    },

    // History Modal
    '.automation-history-modal': {
        title: 'Execution History',
        description: 'Detailed log of every time this automation ran. Shows timestamp, duration, status (success/error), and expandable logs with step-by-step details.',
        docsId: 'auto-history'
    },

    // ─── LIBRARY PAGE ─────────────────────────────────────────────────

    // Library Grid View
    '#library-page .library-controls': {
        title: 'Library Controls',
        description: 'Search, filter, and navigate your music library. Find artists by name, filter by watchlist status, or jump to a letter.',
        docsId: 'lib-standard'
    },
    '#library-search-input': {
        title: 'Search Library',
        description: 'Search your library by artist name. Results filter in real-time as you type.',
    },
    '#watchlist-filter': {
        title: 'Watchlist Filter',
        description: 'Filter artists by watchlist status: All shows everyone, Watched shows only artists you follow, Unwatched shows artists not on your watchlist.',
    },
    '#alphabet-selector': {
        title: 'Alphabet Jump',
        description: 'Jump to artists starting with a specific letter. Click "All" to reset. "#" shows artists starting with numbers.',
    },
    '#library-artists-grid': {
        title: 'Artist Grid',
        description: 'Your music library organized by artist. Each card shows the artist photo, name, track count, and service badges. Click any card to view their collection.',
        docsId: 'lib-standard'
    },
    '.library-artist-card': {
        title: 'Library Artist',
        description: 'An artist in your library. Click to view their full collection with albums, EPs, and singles. Service badges show which metadata sources have enriched this artist.',
        tips: [
            'Badge icons link to the artist on external services',
            'Eye icon toggles watchlist status',
            'Track count shows total tracks in your library for this artist'
        ]
    },
    '#library-pagination': {
        title: 'Pagination',
        description: 'Navigate through pages of artists. Your library shows 75 artists per page.',
    },

    // Artist Detail — Hero Section
    '#artist-hero-section': {
        title: 'Artist Profile',
        description: 'Full artist profile with image, name, service badges, genres, bio, listening stats, and collection overview. Data is enriched from up to 9 metadata services.',
        docsId: 'lib-standard'
    },
    '#artist-detail-name': {
        title: 'Artist Name',
        description: 'The artist\'s name as it appears in your library.',
    },
    '#artist-hero-badges': {
        title: 'Service Badges',
        description: 'Links to this artist on external platforms. Each badge indicates which services have matched and enriched this artist with metadata.',
        tips: [
            'Click any badge to open the artist on that platform',
            'More badges = more complete metadata enrichment',
            'Run the Metadata Updater on the dashboard to enrich more artists'
        ],
        docsId: 'lib-matching'
    },
    '#artist-genres': {
        title: 'Genres',
        description: 'Genre tags from Spotify, Last.fm, and other metadata sources. Merged and deduplicated across all enrichment sources.',
    },
    '#artist-hero-bio': {
        title: 'Artist Biography',
        description: 'Biography from Last.fm. Click "Read more" to expand. Populated by the Last.fm enrichment worker.',
    },
    '#artist-hero-listeners': {
        title: 'Listeners',
        description: 'Total unique listeners on Last.fm. Shows global popularity of this artist.',
    },
    '#artist-hero-playcount': {
        title: 'Play Count',
        description: 'Total plays on Last.fm across all listeners worldwide.',
    },
    '.collection-overview': {
        title: 'Collection Overview',
        description: 'Progress bars showing how complete your collection is for this artist — Albums, EPs, and Singles separately. Numbers show owned/total from the metadata source.',
    },
    '#artist-enrichment-coverage': {
        title: 'Enrichment Coverage',
        description: 'Animated rings showing metadata enrichment percentage per service. Each ring represents one metadata source — higher percentage means more tracks have been enriched by that service.',
        docsId: 'lib-matching'
    },

    // Artist Detail — Action Buttons
    '#library-artist-watchlist-btn': {
        title: 'Watchlist',
        description: 'Add or remove this artist from your Watchlist for new release monitoring.',
        docsId: 'art-watchlist'
    },
    '#library-artist-enhance-btn': {
        title: 'Enhance Quality',
        description: 'Scan your collection for this artist and find higher-quality versions of tracks you own. Compares bitrate and format against available sources.',
    },
    '#library-artist-radio-btn': {
        title: 'Artist Radio',
        description: 'Generate and play a radio mix of this artist\'s tracks from your library. Streams directly from your media server.',
    },

    // Discography Filters
    '#discography-filters': {
        title: 'Discography Filters',
        description: 'Filter the artist\'s releases by category, content type, and ownership status. Multiple filters can be combined.',
        tips: [
            'Category: toggle Albums, EPs, Singles on/off',
            'Content: show/hide Live, Compilations, Featured releases',
            'Ownership: All, Owned (in library), or Missing (not in library)'
        ],
        docsId: 'lib-standard'
    },
    '.discography-filter-btn[data-filter="ownership"][data-value="missing"]': {
        title: 'Missing Releases',
        description: 'Show only releases NOT in your library. Great for finding what to download next.',
    },
    '.discography-filter-btn[data-filter="ownership"][data-value="owned"]': {
        title: 'Owned Releases',
        description: 'Show only releases you already have in your library.',
    },

    // View Toggle
    '.enhanced-view-toggle-btn[data-view="standard"]': {
        title: 'Standard View',
        description: 'Card grid view of releases. Click any card to open the download modal.',
        docsId: 'lib-standard'
    },
    '.enhanced-view-toggle-btn[data-view="enhanced"]': {
        title: 'Enhanced View',
        description: 'Advanced management mode with accordion layout, inline editing, tag writing, and bulk operations. Admin-only feature.',
        tips: [
            'Expand albums to see track tables with editable fields',
            'Select tracks across albums for batch operations',
            'Write tags directly to audio files',
            'Reorganize files with the album reorganize tool'
        ],
        docsId: 'lib-enhanced'
    },

    // Discography Sections
    '#albums-section': {
        title: 'Albums',
        description: 'Full-length studio albums. Shows owned and missing counts in the header. Click any release card to download.',
    },
    '#eps-section': {
        title: 'EPs',
        description: 'Extended plays (4-6 tracks). Shows owned and missing counts.',
    },
    '#singles-section': {
        title: 'Singles',
        description: 'Single tracks and 2-3 track releases. Shows owned and missing counts.',
    },
    '.release-card': {
        title: 'Release Card',
        description: 'An album, EP, or single in the discography. Shows cover art, title, year, track count, and ownership status. Click to open the download modal.',
    },

    // Enhanced View
    '#enhanced-view-container': {
        title: 'Enhanced Library Manager',
        description: 'Accordion layout with expandable albums showing track tables. Edit metadata inline, write tags to files, and perform bulk operations across albums.',
        docsId: 'lib-enhanced'
    },
    '.enhanced-track-checkbox': {
        title: 'Track Selection',
        description: 'Select tracks for bulk operations. Hold Ctrl+Click for range selection. Selected tracks appear in the bulk actions bar at the bottom.',
        docsId: 'lib-bulk'
    },

    // Bulk Actions Bar
    '#enhanced-bulk-bar': {
        title: 'Bulk Actions',
        description: 'Appears when tracks are selected. Edit metadata for all selected tracks at once, write tags to files, or clear the selection.',
        tips: [
            'Edit Selected: opens a modal to change metadata fields for all selected tracks',
            'Write Tags: writes database metadata to the actual audio files',
            'Clear Selection: deselects all tracks'
        ],
        docsId: 'lib-bulk'
    },

    // Tag Preview Modal
    '#tag-preview-overlay': {
        title: 'Tag Preview',
        description: 'Compare current file tags against database metadata before writing. Shows a diff table highlighting what will change. Choose whether to embed cover art and sync to your media server.',
        docsId: 'lib-tags'
    },
    '#batch-tag-preview-overlay': {
        title: 'Batch Tag Preview',
        description: 'Preview tag changes for multiple tracks at once. Each track shows its own diff table. Write all tags in one batch operation.',
        docsId: 'lib-tags'
    },

    // Reorganize Modal
    '#reorganize-overlay': {
        title: 'Reorganize Album',
        description: 'Move and rename files in an album to match your file organization template. Preview the changes before applying.',
    },

    // ─── STATS PAGE ──────────────────────────────────────────────────

    '.stats-container': {
        title: 'Listening Stats',
        description: 'Analytics dashboard showing your listening activity, top artists/albums/tracks, genre breakdown, library health, and storage usage. Data syncs from your media server.',
    },
    '#stats-time-range': {
        title: 'Time Range',
        description: 'Filter all stats by time period: 7 Days, 30 Days, 12 Months, or All Time. Charts and rankings update instantly.',
    },
    '#stats-sync-btn': {
        title: 'Sync Now',
        description: 'Manually sync listening data from your media server. Pulls the latest play history, scrobbles, and library changes.',
    },
    '#stats-overview': {
        title: 'Overview Cards',
        description: 'Key metrics at a glance: Total Plays, Listening Time, unique Artists, Albums, and Tracks played in the selected time range.',
    },
    '#stats-timeline-chart': {
        title: 'Listening Activity',
        description: 'Chart showing your listening activity over time. Each bar represents plays in that time period. Helps visualize listening patterns and trends.',
    },
    '#stats-genre-chart': {
        title: 'Genre Breakdown',
        description: 'Pie/donut chart showing the genre distribution of your listening. Based on genre tags from your library\'s metadata enrichment.',
    },
    '#stats-recent-plays': {
        title: 'Recently Played',
        description: 'Your most recent listening history from the media server. Shows track, artist, album, and when it was played.',
    },
    '#stats-top-artists': {
        title: 'Top Artists',
        description: 'Your most-played artists in the selected time range, ranked by play count.',
    },
    '#stats-top-albums': {
        title: 'Top Albums',
        description: 'Your most-played albums in the selected time range, ranked by play count.',
    },
    '#stats-top-tracks': {
        title: 'Top Tracks',
        description: 'Your most-played individual tracks in the selected time range.',
    },
    '#stats-library-health': {
        title: 'Library Health',
        description: 'Overview of your library\'s format distribution, unplayed tracks, total duration, and track count. The format bar shows FLAC vs MP3 vs other formats.',
    },
    '#stats-enrichment-coverage': {
        title: 'Enrichment Coverage',
        description: 'How thoroughly your library has been enriched by each metadata service. Higher percentages mean more complete metadata.',
    },
    '#stats-db-storage-chart': {
        title: 'Database Storage',
        description: 'Breakdown of your SoulSync database size by category: library data, metadata cache, discovery pool, settings, and more.',
    },

    // ─── IMPORT PAGE ────────────────────────────────────────────────

    '.import-page-container': {
        title: 'Import Music',
        description: 'Import audio files from your import folder into your library. Match files to album metadata, tag them, and organize into your collection.',
        docsId: 'import'
    },
    '.import-page-refresh-btn': {
        title: 'Refresh',
        description: 'Re-scan your import folder for new audio files. Use after dropping new files in.',
    },
    '#import-staging-bar': {
        title: 'Import Folder',
        description: 'Shows your configured import folder path and the number of audio files found. Set the import path in Settings → Download Settings.',
        docsId: 'imp-setup'
    },
    '#import-page-queue': {
        title: 'Processing Queue',
        description: 'Shows albums and singles currently being processed. Each job goes through matching, tagging, cover art embedding, and file organization.',
    },
    '#import-page-tab-album': {
        title: 'Albums Tab',
        description: 'Import complete albums. Search for an album, match import files to tracks, then process. Suggestions appear automatically from your import folder.',
        docsId: 'imp-workflow'
    },
    '#import-page-tab-singles': {
        title: 'Singles Tab',
        description: 'Import individual audio files as single tracks. Select files, and SoulSync identifies them using AcoustID fingerprinting or filename matching.',
        docsId: 'imp-singles'
    },
    '#import-page-suggestions-grid': {
        title: 'Suggestions',
        description: 'Albums automatically detected from your import folder based on folder names and file metadata. Click a suggestion to start the matching process.',
    },
    '#import-page-album-search-input': {
        title: 'Album Search',
        description: 'Search your metadata source for an album to match against import files. Enter the album name or artist + album.',
    },
    '#import-page-album-match-section': {
        title: 'Track Matching',
        description: 'Match your import files to album tracks. Drag files from the unmatched pool onto tracks, or let auto-matching do it. Green = matched, red = unmatched.',
        tips: [
            'Drag and drop files from the unmatched pool to track slots',
            '"Re-match Automatically" re-runs the matching algorithm',
            '"Back to Search" returns to the album search view'
        ],
        docsId: 'imp-matching'
    },
    '#import-page-unmatched-pool': {
        title: 'Unmatched Files',
        description: 'Audio files in your import folder that haven\'t been matched to an album track yet. Drag them onto the correct track slot above.',
        docsId: 'imp-matching'
    },
    '#import-page-album-process-btn': {
        title: 'Process Album',
        description: 'Start processing the matched album. Tags files with metadata, embeds cover art, renames and organizes files into your library, then triggers a media server scan.',
    },
    '#import-page-singles-list': {
        title: 'Singles List',
        description: 'Individual audio files in your import folder. Select files and click "Process Selected" to identify and import them as single tracks.',
        docsId: 'imp-singles'
    },
    '#import-page-singles-process-btn': {
        title: 'Process Singles',
        description: 'Identify and import selected singles. Uses AcoustID fingerprinting to match files to tracks, then tags and organizes them.',
    },

    // ─── SETTINGS PAGE ────────────────────────────────────────────────

    // Tabs
    '.stg-tab[data-tab="connections"]': {
        title: 'Connections',
        description: 'Configure credentials for metadata sources (Spotify, Tidal, Last.fm, etc.) and media server connections (Plex, Jellyfin, Navidrome).',
        docsId: 'set-services'
    },
    '.stg-tab[data-tab="downloads"]': {
        title: 'Downloads',
        description: 'Configure download sources, paths, quality profiles, and hybrid mode priority order.',
        docsId: 'set-download'
    },
    '.stg-tab[data-tab="library"]': {
        title: 'Library',
        description: 'File organization templates, post-processing options, tag embedding, lossy copy, listening stats, and content filtering.',
        docsId: 'set-processing'
    },
    '.stg-tab[data-tab="appearance"]': {
        title: 'Appearance',
        description: 'Customize the accent color, sidebar visualizer style, and UI effects like particles and worker orbs.',
    },
    '.stg-tab[data-tab="advanced"]': {
        title: 'Advanced',
        description: 'Database workers, discovery pool settings, API key management, developer mode, and logging configuration.',
    },

    // Connections — API Services
    '.api-test-buttons': {
        title: 'Test Connections',
        description: 'Test each configured service to verify credentials are working. Green = connected, Red = failed.',
        docsId: 'set-services'
    },

    // Connections — Media Server
    '#plex-container': {
        title: 'Plex Configuration',
        description: 'Connect your Plex server. Enter the URL and token, then select your Music Library. SoulSync reads your library from Plex and triggers scans after downloads.',
        tips: [
            'URL format: http://IP:32400 (or your custom port)',
            'Token: find in Plex settings or browser URL bar while logged in',
            'Select the correct Music Library after connecting'
        ],
        docsId: 'set-media'
    },
    '#jellyfin-container': {
        title: 'Jellyfin Configuration',
        description: 'Connect your Jellyfin server. Enter URL, API key, then select a user and music library.',
        docsId: 'set-media'
    },
    '#navidrome-container': {
        title: 'Navidrome Configuration',
        description: 'Connect your Navidrome server. Enter URL, username, password, then select the music folder. Navidrome auto-detects new files.',
        docsId: 'set-media'
    },

    // Downloads — Source & Paths
    '#download-source-mode': {
        title: 'Download Source Mode',
        description: 'Choose your primary download source. Hybrid mode tries multiple sources in priority order with automatic fallback.',
        tips: [
            'Soulseek: P2P network via slskd — best for lossless and rare music',
            'YouTube: audio extraction via yt-dlp',
            'Tidal/Qobuz/HiFi/Deezer: streaming source downloads',
            'Hybrid: tries sources in your configured priority order'
        ],
        docsId: 'set-download'
    },
    '#hybrid-settings-container': {
        title: 'Hybrid Source Priority',
        description: 'Drag and drop to reorder your download source priority. The first source is tried first; if it fails or finds nothing, the next source is tried.',
        docsId: 'set-download'
    },
    '#soulseek-settings-container': {
        title: 'Soulseek Settings',
        description: 'Configure your slskd connection (URL + API key), search timeout, peer speed limits, queue limits, and download timeout.',
        docsId: 'set-download'
    },
    '#tidal-download-settings-container': {
        title: 'Tidal Download Settings',
        description: 'Quality selection for Tidal downloads. Authenticate with your Tidal account. "Allow quality fallback" controls whether lower quality is accepted when preferred isn\'t available.',
        docsId: 'set-download'
    },
    '#qobuz-settings-container': {
        title: 'Qobuz Settings',
        description: 'Quality selection and authentication for Qobuz downloads. Sign in with your Qobuz account credentials.',
        docsId: 'set-download'
    },
    '#hifi-download-settings-container': {
        title: 'HiFi Settings',
        description: 'Quality selection for HiFi downloads. No authentication needed — uses community API instances. Test connection to verify availability.',
        docsId: 'set-download'
    },
    '#deezer-download-settings-container': {
        title: 'Deezer Download Settings',
        description: 'Quality selection and ARL token for Deezer downloads. FLAC requires HiFi subscription. Paste your ARL cookie from the browser.',
        docsId: 'set-download'
    },
    '#youtube-settings-container': {
        title: 'YouTube Settings',
        description: 'Browser cookies selection for bot detection bypass and download delay between requests.',
    },

    // Quality Profile
    '#quality-profile-section': {
        title: 'Quality Profile',
        description: 'Configure which audio formats and bitrates are preferred for Soulseek downloads. Quick presets or custom per-format settings with bitrate ranges.',
        tips: [
            'Audiophile: FLAC only, strict — fails if no lossless found',
            'Balanced: FLAC preferred, MP3 320 fallback (default)',
            'Space Saver: MP3 preferred, smallest files',
            'FLAC bit depth: choose 16-bit, 24-bit, or any',
            'Fallback toggle: when off, only downloads at preferred quality'
        ],
        docsId: 'set-quality'
    },
    '.preset-button': {
        title: 'Quality Preset',
        description: 'One-click quality configuration. Presets set all format enables, priorities, and bitrate ranges at once.',
    },
    '.bit-depth-btn': {
        title: 'FLAC Bit Depth',
        description: 'Prefer 16-bit (CD quality, smaller), 24-bit (hi-res, larger), or Any. When a specific depth is chosen, the fallback toggle controls whether other depths are accepted.',
        docsId: 'set-quality'
    },
    '#quality-fallback-enabled': {
        title: 'Allow Lossy Fallback',
        description: 'When enabled, accepts any quality if no preferred formats are found. When disabled, downloads fail rather than grabbing lower quality — use for strict lossless libraries.',
        docsId: 'set-quality'
    },

    // Library — File Organization
    '#file-organization-enabled': {
        title: 'File Organization',
        description: 'When enabled, downloaded files are renamed and moved to your transfer path using customizable templates. Separate templates for albums, singles, and playlists.',
        tips: [
            'Variables: $artist, $album, $title, $track, $year, $quality, $albumtype, $disc',
            '$albumtype resolves to Album, Single, EP, or Compilation',
            'Multi-disc albums auto-create Disc N subfolders'
        ],
        docsId: 'set-processing'
    },

    // Library — Post-Processing
    '#metadata-enabled': {
        title: 'Post-Processing',
        description: 'Master toggle for all post-download processing: metadata tagging, cover art embedding, lyrics, and tag embedding from external services.',
        docsId: 'set-processing'
    },
    '#post-processing-options': {
        title: 'Post-Processing Options',
        description: 'Configure which metadata to embed in downloaded files. Per-service toggle controls whether that service\'s IDs and data are written to file tags.',
        tips: [
            'Album art: embeds cover art directly in the audio file',
            'LRC lyrics: fetches synced lyrics from LRClib',
            'Per-service tags: embed Spotify IDs, MusicBrainz IDs, etc.'
        ],
        docsId: 'set-processing'
    },

    // Library — Lossy Copy
    '#lossy-copy-enabled': {
        title: 'Lossy Copy',
        description: 'Create a lower-bitrate copy of every downloaded file alongside the original. Useful for syncing to mobile devices or bandwidth-limited streaming.',
        docsId: 'set-processing'
    },

    // Library — Listening Stats
    '#listening-stats-enabled': {
        title: 'Listening Stats',
        description: 'Track your listening activity from your media server. When enabled, SoulSync periodically syncs play history for the Stats page.',
    },

    // Advanced — API Keys
    '#api-keys-list': {
        title: 'API Keys',
        description: 'Manage API keys for external access to SoulSync\'s REST API. Generate keys with labels for different integrations.',
    },

    // Advanced — Discovery Pool
    '#discovery-lookback-period': {
        title: 'Discovery Lookback',
        description: 'How far back to look for new releases during watchlist scans. Shorter periods find only recent releases; longer periods catch older missed ones.',
    },
    '#discovery-hemisphere': {
        title: 'Hemisphere',
        description: 'Your geographic hemisphere for seasonal content. Affects which seasonal playlists and albums appear on the Discover page.',
    },

    // Appearance
    '#accent-preset': {
        title: 'Accent Color',
        description: 'Choose a color theme for the entire app. Affects buttons, badges, highlights, and interactive elements throughout SoulSync.',
    },
    '#sidebar-visualizer-type': {
        title: 'Sidebar Visualizer',
        description: 'Audio visualization style in the sidebar player. Choose from bars, wave, spectrum, mirror, equalizer, or none.',
    },

    // Save Button
    '.save-settings': {
        title: 'Save Settings',
        description: 'Save all settings changes. Some changes take effect immediately; others require a restart.',
    },

    // ─── DASHBOARD: ENRICHMENT SERVICES ────────────────────────────

    '#enrichment-pills-section': {
        title: 'Enrichment Service Workers',
        description: 'Per-service enrichment workers that run in the background to enrich your library metadata. Each button shows the worker status and lets you start/stop individual services.',
        tips: [
            'Green = running, grey = stopped, red = error',
            'Click a service pill to toggle its worker on/off',
            'Workers process tracks in batches — hover for detailed stats'
        ]
    },
    '#musicbrainz-button': {
        title: 'MusicBrainz Enrichment',
        description: 'Looks up recording IDs, release groups, and artist MBIDs from MusicBrainz. Provides canonical identifiers used by other services.',
    },
    '#audiodb-button': {
        title: 'AudioDB Enrichment',
        description: 'Adds artist bios, band member info, genre tags, and high-res artwork from TheAudioDB.',
    },
    '#deezer-button': {
        title: 'Deezer Enrichment',
        description: 'Enriches tracks with Deezer IDs, BPM data, and genre information from the Deezer catalog.',
    },
    '#spotify-enrich-button': {
        title: 'Spotify Enrichment',
        description: 'Links tracks to Spotify IDs for popularity scores, audio features, and cross-referencing. Requires Spotify OAuth connection.',
    },
    '#itunes-enrich-button': {
        title: 'iTunes Enrichment',
        description: 'Matches tracks to the Apple Music/iTunes catalog for genre tags and iTunes IDs.',
    },
    '#lastfm-enrich-button': {
        title: 'Last.fm Enrichment',
        description: 'Adds Last.fm listener/play counts and community genre tags to your library tracks.',
    },
    '#genius-enrich-button': {
        title: 'Genius Enrichment',
        description: 'Links tracks to Genius for lyrics availability and song descriptions.',
    },
    '#tidal-enrich-button': {
        title: 'Tidal Enrichment',
        description: 'Matches tracks to the Tidal catalog for Tidal IDs and lossless availability info.',
    },
    '#qobuz-enrich-button': {
        title: 'Qobuz Enrichment',
        description: 'Links tracks to Qobuz for Hi-Res availability data and Qobuz IDs.',
    },
    '#discogs-button': {
        title: 'Discogs Enrichment',
        description: 'Enriches with Discogs data — detailed genre/style taxonomy (400+ tags), label info, catalog numbers, and community ratings.',
    },

    // ─── DASHBOARD: RECENT SYNCS & RATE MONITOR ──────────────────────

    '#sync-history-cards': {
        title: 'Recent Syncs',
        description: 'Quick view of your most recent playlist sync operations. Shows playlist name, track counts, and completion status.',
    },
    '#rate-monitor-section': {
        title: 'API Rate Monitor',
        description: 'Live view of API rate limit usage across all metadata services. Shows remaining quota, cooldown timers, and ban status.',
    },
    '#repair-button': {
        title: 'Library Maintenance',
        description: 'Open the maintenance panel to run repair jobs — detect orphan files, fix missing covers, clean live recordings, reorganize files, and more.',
    },
    '#soulid-button': {
        title: 'SoulID Generator',
        description: 'Generate unique fingerprint IDs for your audio files using AcoustID. Useful for deduplication and cross-referencing.',
    },
    '#blacklist-card': {
        title: 'Download Blacklist',
        description: 'Sources that have been blocked from future downloads. Tracks from blacklisted sources will be skipped during search and matching.',
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

    // ─── ACTIVE DOWNLOADS PAGE ──────────────────────────────────────

    '.adl-container': {
        title: 'Downloads',
        description: 'Live view of every download happening across the app. Tracks from Search, Sync, Discover, Artists, and Wishlist all appear here in one unified list.',
    },
    '#adl-filter-pills': {
        title: 'Download Filters',
        description: 'Filter downloads by status. "All" shows everything, "Active" shows currently downloading/searching tracks, "Queued" shows waiting tracks, "Completed" and "Failed" show finished items.',
    },
    '#adl-list': {
        title: 'Download List',
        description: 'Each row shows track title, artist, album, which batch it belongs to (playlist name or album), and current status. Active downloads show a spinner, completed show green, failed show red with error details.',
        tips: [
            'Track position (e.g. "3 of 19") shows progress within album/playlist batches',
            'Section headers group downloads by status category',
            'List updates every 2 seconds while you\'re on this page'
        ]
    },
    '#adl-clear-btn': {
        title: 'Clear Completed',
        description: 'Remove all completed, failed, and cancelled downloads from the list. Only affects the tracker display — does not delete any downloaded files.',
    },

    // ─── PLAYLIST EXPLORER PAGE ──────────────────────────────────────

    '#playlist-explorer-page': {
        title: 'Playlist Explorer',
        description: 'Visual exploration tool for deep-diving into playlists. Browse album art grids, explore full artist discographies, and batch-select tracks for download or wishlist.',
        tips: [
            'Pick a playlist source (Spotify, Tidal, Deezer, ListenBrainz) and select a playlist',
            'Albums view shows album art cards; Full Discog view shows complete artist discographies',
            'Select tracks across multiple albums, then use the action bar to download or wishlist them all'
        ]
    },
    '#explorer-playlist-picker': {
        title: 'Playlist Picker',
        description: 'Choose which playlist to explore. Select a source tab, then pick a playlist from the dropdown.',
    },
    '.explorer-mode-btn': {
        title: 'View Mode Toggle',
        description: 'Switch between Albums view (grouped by album with artwork) and Full Discog view (complete discography for each artist in the playlist).',
    },
    '#explorer-build-btn': {
        title: 'Explore Playlist',
        description: 'Load the selected playlist and build the visual explorer view. Fetches album art and track listings from your metadata source.',
    },
    '#explorer-action-bar': {
        title: 'Selection Action Bar',
        description: 'Appears when tracks are selected. Shows selection count and provides batch actions — add to wishlist or download all selected tracks.',
    },

    // ─── ISSUES PAGE ────────────────────────────────────────────────

    '.issues-header': {
        title: 'Issues & Findings',
        description: 'Library health scanner results. Each finding is a detected problem — missing files, duplicate tracks, incomplete albums, bad metadata, and more.',
    },
    '#issues-filters': {
        title: 'Issue Filters',
        description: 'Filter findings by category (Missing Files, Duplicates, Metadata Gaps, etc.), severity, or job type. Helps focus on the most important issues first.',
    },
    '#issues-list': {
        title: 'Findings List',
        description: 'Each row is a detected issue with details, severity, and available actions. Click "Fix" to auto-repair, "Dismiss" to hide, or expand for more details.',
        tips: [
            'Green "Fix" button applies the suggested repair automatically',
            'Dismissed findings are hidden but can be restored from filters',
            'Run repair jobs from Settings > Maintenance to generate new findings'
        ]
    },

    // ─── DISCOVER PAGE: ADDITIONAL ─────────────────────────────────

    '#your-artists-section': {
        title: 'Your Artists',
        description: 'Carousel of artists from your watchlist. Quick access to view their latest releases, discography, or manage watchlist settings.',
    },

    '#your-albums-section': {
        title: 'Your Albums',
        description: 'Albums you\'ve saved or liked across connected services (Spotify, Tidal, Deezer). Shows which are already in your library and lets you download missing ones.',
    },

    // ─── PERSONAL SETTINGS ─────────────────────────────────────────

    '#personal-settings-btn': {
        title: 'My Settings',
        description: 'Personal settings for your profile — accent color, home page preference, notification preferences, and other per-user customizations.',
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

// ═══════════════════════════════════════════════════════════════════════════
// HELPER MENU & MODE SYSTEM
// ═══════════════════════════════════════════════════════════════════════════

const HELPER_MENU_ITEMS = [
    { id: 'info',         icon: '🎯', label: 'Element Info',    desc: 'Click any element to learn about it' },
    { id: 'tour',         icon: '🚶', label: 'Guided Tour',     desc: 'Step-by-step walkthrough' },
    { id: 'search',       icon: '🔍', label: 'Search Help',     desc: 'Find answers fast' },
    { id: 'shortcuts',    icon: '⌨️', label: 'Shortcuts',       desc: 'Keyboard reference' },
    { id: 'setup',        icon: '📋', label: 'Setup Progress',  desc: 'Onboarding checklist' },
    { id: 'whats-new',    icon: '✨', label: "What's New",      desc: 'Latest features' },
    { id: 'troubleshoot', icon: '🔧', label: 'Troubleshoot',    desc: 'Fix common issues' },
];

function toggleHelperMode() {
    // If a mode is active, deactivate everything
    if (HelperState.mode) {
        exitHelperMode();
        return;
    }
    // If menu is open, close it
    if (HelperState.menuOpen) {
        closeHelperMenu();
        return;
    }
    // Otherwise, open the menu
    openHelperMenu();
}

// Map page IDs → tour IDs (only where they differ)
const PAGE_TOUR_MAP = {
    'dashboard':   'dashboard',
    'sync':        'sync-playlist',
    'search':      'first-download',
    'downloads':   'first-download',  // legacy id — the Search page used to be called 'downloads'
    'discover':    'discover',
    'automations': 'automations',
    'library':     'library',
    'stats':       'stats',
    'import':      'import-music',
    'settings':    'settings-tour',
    'issues':      'issues-tour',
};

function openHelperMenu() {
    closeHelperMenu();
    HelperState.menuOpen = true;

    const floatBtn = document.getElementById('helper-float-btn');
    if (!floatBtn) return;

    // User has discovered the help system — stop the idle glow permanently
    floatBtn.classList.remove('undiscovered');
    localStorage.setItem('soulsync_helper_discovered', '1');
    floatBtn.classList.add('menu-open');

    // Detect current page for contextual tour suggestion
    const currentPage = document.querySelector('.page.active')?.id?.replace('-page', '') || '';
    const suggestedTourId = PAGE_TOUR_MAP[currentPage];
    const suggestedTour = suggestedTourId ? HELPER_TOURS[suggestedTourId] : null;

    const menu = document.createElement('div');
    menu.className = 'helper-menu';

    let contextualBtn = '';
    if (suggestedTour) {
        contextualBtn = `
            <button class="helper-menu-item helper-menu-contextual" onclick="closeHelperMenu();HelperState.mode='tour';document.getElementById('helper-float-btn')?.classList.add('active');startTour('${suggestedTourId}')" style="animation-delay:0s">
                <span class="helper-menu-icon">${suggestedTour.icon}</span>
                <span class="helper-menu-label">${suggestedTour.title}</span>
                <span class="helper-menu-badge">${suggestedTour.steps.length} steps</span>
            </button>
            <div class="helper-menu-divider"></div>
        `;
    }

    const offset = suggestedTour ? 1 : 0;
    menu.innerHTML = contextualBtn + HELPER_MENU_ITEMS.map((item, i) => `
        <button class="helper-menu-item" onclick="activateHelperMode('${item.id}')" style="animation-delay:${(i + offset) * 0.04}s">
            <span class="helper-menu-icon">${item.icon}</span>
            <span class="helper-menu-label">${item.label}</span>
        </button>
    `).join('');

    document.body.appendChild(menu);
    _helperMenu = menu;

    // Position above the float button
    const btnRect = floatBtn.getBoundingClientRect();
    menu.style.right = (window.innerWidth - btnRect.right) + 'px';
    menu.style.bottom = (window.innerHeight - btnRect.top + 8) + 'px';

    requestAnimationFrame(() => menu.classList.add('visible'));

    // Close on click outside
    setTimeout(() => {
        document.addEventListener('click', _helperMenuOutsideClick);
    }, 10);
}

function _helperMenuOutsideClick(e) {
    const floatBtn = document.getElementById('helper-float-btn');
    if (_helperMenu && !_helperMenu.contains(e.target) && !(floatBtn && floatBtn.contains(e.target))) {
        closeHelperMenu();
    }
}

function closeHelperMenu() {
    document.removeEventListener('click', _helperMenuOutsideClick);
    if (_helperMenu) {
        _helperMenu.remove();
        _helperMenu = null;
    }
    HelperState.menuOpen = false;
    const floatBtn = document.getElementById('helper-float-btn');
    if (floatBtn) floatBtn.classList.remove('menu-open');
}

function activateHelperMode(mode) {
    closeHelperMenu();
    HelperState.mode = mode;

    const floatBtn = document.getElementById('helper-float-btn');
    if (floatBtn) floatBtn.classList.add('active');

    switch (mode) {
        case 'info':
            helperModeActive = true;
            document.body.classList.add('helper-mode-active');
            break;
        case 'tour':        openTourSelector(); break;
        case 'search':      openHelperSearch(); break;
        case 'shortcuts':   openShortcutsOverlay(); break;
        case 'setup':       openSetupPanel(); break;
        case 'whats-new':   openWhatsNew(); break;
        case 'troubleshoot': activateTroubleshootMode(); break;
    }
}

function exitHelperMode() {
    helperModeActive = false;
    HelperState.mode = null;
    document.body.classList.remove('helper-mode-active');
    dismissHelperPopover();
    dismissTour();
    closeSetupPanel();
    closeShortcutsOverlay();
    closeHelperSearch();
    closeTroubleshootMode();

    const floatBtn = document.getElementById('helper-float-btn');
    if (floatBtn) floatBtn.classList.remove('active');
}

// ═══════════════════════════════════════════════════════════════════════════
// GUIDED TOUR ENGINE
// ═══════════════════════════════════════════════════════════════════════════

const HELPER_TOURS = {
    'dashboard': {
        title: 'Dashboard Tour',
        description: 'Learn what each section of the dashboard does.',
        icon: '📊',
        steps: [
            // Header area (top of page)
            { page: 'dashboard', selector: '.dashboard-header', title: 'Welcome to SoulSync', description: 'This is your System Dashboard — the central hub for monitoring your music system. Let\'s walk through everything from top to bottom.' },
            { page: 'dashboard', selector: '#watchlist-button', title: 'Watchlist', description: 'Artists you follow for new releases. Click to manage watched artists, run scans, and configure per-artist download preferences.' },
            { page: 'dashboard', selector: '#wishlist-button', title: 'Wishlist', description: 'Tracks queued for download. Failed downloads, watchlist discoveries, and manual additions all land here for retry.' },

            // Service cards
            { page: 'dashboard', selector: '#metadata-source-service-card', title: 'Metadata Source', description: 'Shows your metadata source connection (Spotify, iTunes, or Deezer). This determines where album, artist, and track info comes from. Click "Test Connection" to verify.' },
            { page: 'dashboard', selector: '#media-server-service-card', title: 'Media Server', description: 'Your media server (Plex, Jellyfin, or Navidrome). This is where your music library lives. SoulSync reads your collection and sends downloads here.' },
            { page: 'dashboard', selector: '#soulseek-service-card', title: 'Download Source', description: 'Your primary download source status. In hybrid mode, shows the first source in your priority chain.' },

            // System stats
            { page: 'dashboard', selector: '.stats-grid-dashboard', title: 'System Stats', description: 'Real-time metrics: active downloads, speed, sync operations, uptime, and memory usage. Updates live via WebSocket.' },

            // Tools — in page order
            { page: 'dashboard', selector: '#db-updater-card', title: 'Database Updater', description: 'Syncs your media server\'s library into SoulSync\'s database. Three modes: Incremental (fast, new content only), Full Refresh (rebuilds everything), Deep Scan (finds and removes stale entries).' },
            { page: 'dashboard', selector: '#metadata-updater-card', title: 'Metadata Enrichment', description: 'Background workers that enrich your library from 9 services — Spotify, MusicBrainz, Deezer, Last.fm, iTunes, AudioDB, Genius, Tidal, Qobuz. Runs automatically at the configured interval.' },
            { page: 'dashboard', selector: '#quality-scanner-card', title: 'Quality Scanner', description: 'Analyzes audio files for quality integrity. Calculates bitrate density to detect transcodes (e.g., an MP3 re-encoded as FLAC). Scan by Full Library, New Only, or Single Artist.' },
            { page: 'dashboard', selector: '#duplicate-cleaner-card', title: 'Duplicate Cleaner', description: 'Finds and removes duplicate tracks by comparing title, artist, album, and audio characteristics. Always reviews before deleting.' },
            { page: 'dashboard', selector: '#discovery-pool-card', title: 'Discovery Pool', description: 'Tracks from similar artists found during watchlist scans. Matched tracks feed the Discover page playlists and genre browser. Fix failed matches manually.' },
            { page: 'dashboard', selector: '#retag-tool-card', title: 'Retag Tool', description: 'Queue of tracks needing metadata corrections. When enrichment detects better tags than what\'s in your files, they appear here for batch review.' },
            { page: 'dashboard', selector: '#media-scan-card', title: 'Media Server Scan', description: 'Manually trigger a library scan on your media server. Usually automatic after downloads, but useful after bulk imports.' },
            { page: 'dashboard', selector: '#backup-manager-card', title: 'Backup Manager', description: 'Create and manage database backups. Includes all metadata, settings, enrichment data, and automation configs — everything except audio files.' },
            { page: 'dashboard', selector: '#metadata-cache-card', title: 'Metadata Cache', description: 'Browse cached API responses from all metadata searches. Every artist, album, and track looked up is stored here, speeding up future lookups and feeding the Genre Explorer.' },

            // Activity feed (bottom)
            { page: 'dashboard', selector: '#dashboard-activity-feed', title: 'Activity Feed', description: 'Live stream of system events — downloads, syncs, enrichment updates, errors. Newest at the top, updates in real-time via WebSocket. That\'s the dashboard! 🎉' },
        ]
    },
    'first-download': {
        title: 'Your First Download',
        description: 'Step-by-step guide to downloading your first album.',
        icon: '⬇️',
        steps: [
            { page: 'search', selector: '#enh-source-row', title: 'Pick a Search Source', description: 'Each icon is a metadata source. The highlighted one is where your next search goes — defaults to your configured primary source. Click a different icon to switch to Spotify, Apple Music, Deezer, Discogs, Hydrabase, MusicBrainz, Music Videos, or Soulseek (raw P2P files). A small dot marks sources you\'ve already searched for the current query.' },
            { page: 'search', selector: '.enhanced-search-input-wrapper', title: 'Search for Music', description: 'Type an artist or album name here. Results appear in categorized sections — Artists, Albums, Singles/EPs, and Tracks. Try searching for your favorite artist now!' },
            { page: 'search', selector: '#enh-results-container', title: 'Search Results', description: 'After searching, results appear organized by type: Artists at the top as cards, then Albums, Singles/EPs, and individual Tracks. "In Library" badges mark items you already own.' },
            { page: 'search', selector: '.enhanced-search-input-wrapper', title: 'Downloading an Album', description: 'Click any album card to open the download modal. You\'ll see the tracklist, quality options, and a big "Download Album" button. Individual tracks have a play button to preview before downloading.' },
            { page: 'search', selector: '.enhanced-search-input-wrapper', title: 'That\'s It!', description: 'Search, click, download. Albums go to your configured download path, get tagged with metadata, and sync to your media server automatically. Active downloads live on the dedicated Downloads page.' },
        ]
    },
    'sync-playlist': {
        title: 'Sync a Playlist',
        description: 'Import and download playlists from streaming services.',
        icon: '🔄',
        steps: [
            // Header
            { page: 'sync', selector: '.sync-header', title: 'Playlist Sync', description: 'Import playlists from any streaming service, match tracks to your download sources, and sync them to your media server. Everything happens from this page.' },
            { page: 'sync', selector: '.sync-history-btn', title: 'Sync History', description: 'View a log of all past sync operations — when they ran, how many tracks matched, and which ones failed. Useful for tracking down missing tracks.' },

            // Source tabs (left to right)
            { page: 'sync', selector: '.sync-tab-button[data-tab="spotify"]', title: 'Spotify Playlists', description: 'If Spotify is connected, click "Refresh" to load all your playlists. Select ones you want, then hit Start Sync in the sidebar.' },
            { page: 'sync', selector: '.sync-tab-button[data-tab="spotify-public"]', title: 'Spotify Link', description: 'Don\'t have a Spotify account? Paste any public Spotify playlist or album URL here to import it without authentication.' },
            { page: 'sync', selector: '.sync-tab-button[data-tab="tidal"]', title: 'Tidal Playlists', description: 'Same as Spotify — connect Tidal in Settings, refresh to load your playlists, then sync.' },
            { page: 'sync', selector: '.sync-tab-button[data-tab="deezer"]', title: 'Deezer', description: 'Paste a Deezer playlist URL to import. No account needed — just the public URL.' },
            { page: 'sync', selector: '.sync-tab-button[data-tab="youtube"]', title: 'YouTube Music', description: 'Paste a YouTube Music playlist URL. The parser extracts track titles and artists, then matches them against your metadata source.' },
            { page: 'sync', selector: '.sync-tab-button[data-tab="beatport"]', title: 'Beatport', description: 'For electronic music — paste a Beatport playlist URL to import DJ sets and charts.' },
            { page: 'sync', selector: '.sync-tab-button[data-tab="import-file"]', title: 'File Import', description: 'Import a playlist from a local file — M3U, CSV, or plain text. Map columns to track/artist/album fields.' },
            { page: 'sync', selector: '.sync-tab-button[data-tab="mirrored"]', title: 'Mirrored Playlists', description: 'Every imported playlist is saved here permanently. Re-sync anytime to catch new additions, check match status, or view the Discovery Pool for unmatched tracks.' },

            // Sidebar
            { page: 'sync', selector: '.sync-sidebar', title: 'Sync Controls', description: 'The command center. Select playlists with checkboxes on the left, then click "Start Sync" here. Progress bars, match counts, and logs update in real-time. That\'s the sync flow! 🎉' },
        ]
    },
    // 'artists-browse' tour retired — the Artists sidebar entry was replaced by the
    // unified Search page (see the first-download tour for the new flow).
    'automations': {
        title: 'Build an Automation',
        description: 'Create automated workflows with triggers and actions.',
        icon: '🤖',
        steps: [
            // List view (visible on load)
            { page: 'automations', selector: '#automations-list-view', title: 'Automations Overview', description: 'All your automations live here, organized into System (built-in), Custom groups, and My Automations. Each card shows its WHEN trigger, DO action, and THEN notifications.' },
            { page: 'automations', selector: '#automations-stats', title: 'Stats Bar', description: 'Quick counts of total automations, how many are active, paused, and custom. Also shows system automations running background tasks like enrichment and watchlist scanning.' },
            { page: 'automations', selector: '.auto-new-btn', title: 'Create New Automation', description: 'Opens the visual builder. Choose a trigger (WHEN), an action (DO), and optional notifications (THEN). Triggers include schedules, events (download complete, new release), and signals from other automations.' },

            // Builder (describe since it requires clicking)
            { page: 'automations', selector: '.auto-new-btn', title: 'The Builder', description: 'The builder has a sidebar with draggable blocks and a canvas. Drag a WHEN block (e.g., "Every 6 hours"), a DO block (e.g., "Run Watchlist Scan"), and optionally a THEN block (e.g., "Send Discord notification").' },
            { page: 'automations', selector: '.auto-new-btn', title: 'Signals & Chains', description: 'Advanced: automations can fire "signals" that trigger other automations, creating chains. Example: Watchlist scan → fires "new_release" signal → Download automation picks it up. Max chain depth is 5.' },

            // Hub section
            { page: 'automations', selector: '#auto-section-hub', title: 'Automation Hub', description: 'Pre-built templates, pipeline recipes, quick-start guides, and reference docs. Browse Pipelines for ready-made multi-step workflows, or check Recipes for common automation patterns. Great starting point! 🎉' },
        ]
    },
    'library': {
        title: 'Library Management',
        description: 'Browse and manage your music collection.',
        icon: '📚',
        steps: [
            // Header
            { page: 'library', selector: '.library-header', title: 'Music Library', description: 'Your complete music collection synced from your media server. The header shows your total artist count. Everything here comes from your last Database Updater run.' },

            // Controls
            { page: 'library', selector: '#library-search-input', title: 'Search Artists', description: 'Type to filter your library by artist name. Results update instantly as you type.' },
            { page: 'library', selector: '#watchlist-filter', title: 'Watchlist Filter', description: 'Filter by watchlist status: All, Watched (artists you follow for new releases), or Unwatched. The "Watch All Unwatched" button adds every remaining artist to your watchlist in one click.' },
            { page: 'library', selector: '#alphabet-selector', title: 'Alphabet Jump', description: 'Click any letter to jump directly to artists starting with that letter. Great for navigating large libraries.' },

            // Grid
            { page: 'library', selector: '#library-artists-grid', title: 'Artist Grid', description: 'Your artists as cards with photos, track counts, and service badges (Spotify, MusicBrainz, etc.). Click any card to open their artist detail page with full discography.' },

            // Pagination
            { page: 'library', selector: '#library-pagination', title: 'Pagination', description: 'Shows 75 artists per page. Use Previous/Next to browse, or combine with the alphabet selector and search to find artists faster.' },

            // Artist detail (describe what they'll see)
            { page: 'library', selector: '#library-artists-grid', title: 'Artist Detail View', description: 'Clicking an artist opens their detail page. From there you can view/download their discography, toggle "Enhanced Management" mode for inline tag editing, bulk operations, and writing tags to files. 🎉' },
        ]
    },
    'discover': {
        title: 'Discover Music',
        description: 'Explore personalized playlists, genre browsing, and new music.',
        icon: '🔮',
        steps: [
            // Hero section
            { page: 'discover', selector: '.discover-hero', title: 'Featured Artists', description: 'The hero slideshow showcases recommended artists based on your library. Use the arrows to browse, or click "View Discography" to explore their music. "Add to Watchlist" starts monitoring them for new releases.' },
            { page: 'discover', selector: '#discover-hero-view-all', title: 'View All Recommendations', description: 'Opens a modal with all recommended artists at once. "Watch All" adds every recommended artist to your watchlist in one click.' },

            // Content sections (top to bottom)
            { page: 'discover', selector: '#recent-releases-carousel', title: 'Recent Releases', description: 'New music from artists in your watchlist. Album cards show cover art — click any to open the download modal. Updates automatically when watchlist scans find new releases.' },
            { page: 'discover', selector: '#seasonal-albums-section', title: 'Seasonal Content', description: 'Season-aware sections that appear automatically — Christmas albums in December, summer vibes in July. Includes curated albums and a Seasonal Mix playlist you can sync to your server.' },

            // Playlists
            { page: 'discover', selector: '#release-radar-playlist', title: 'Fresh Tape', description: 'A playlist of brand-new tracks from recent releases. Each has Download and Sync buttons — sync sends the playlist directly to your media server as a new playlist.' },
            { page: 'discover', selector: '#discovery-weekly-playlist', title: 'The Archives', description: 'Curated tracks from your existing collection. Every playlist section has Download (grab missing tracks) and Sync (push to media server) buttons.' },

            // Build a playlist
            { page: 'discover', selector: '.build-playlist-container', title: 'Build a Playlist', description: 'Create custom playlists from seed artists. Search and select 1-5 artists, hit Generate, and get a 50-track playlist mixing your picks with similar artist discoveries. Download or sync the result.' },

            // ListenBrainz
            { page: 'discover', selector: '.listenbrainz-tabs', title: 'ListenBrainz Playlists', description: 'If ListenBrainz is connected, algorithmic playlists generated from your listening history appear here — weekly jams, exploration picks, and more.' },

            // Time Machine & Genre
            { page: 'discover', selector: '#decade-tabs', title: 'Time Machine', description: 'Browse music by decade — click a decade tab to see tracks from that era in your library. Great for rediscovering older music.' },
            { page: 'discover', selector: '#genre-tabs', title: 'Browse by Genre', description: 'Explore your library organized by genre. Click a genre pill to see artists and tracks in that category. Genres come from all your metadata sources. 🎉' },
        ]
    },
    'stats': {
        title: 'Listening Stats',
        description: 'Understand your listening habits and library health.',
        icon: '📊',
        steps: [
            // Header controls
            { page: 'stats', selector: '#stats-time-range', title: 'Time Range', description: 'Switch between 7 Days, 30 Days, 12 Months, and All Time. All charts and rankings below update to reflect the selected period.' },
            { page: 'stats', selector: '#stats-sync-btn', title: 'Sync Now', description: 'Pulls the latest listening data from your media server (Plex, Jellyfin, or Navidrome). Data syncs automatically, but you can force a refresh here.' },

            // Overview cards
            { page: 'stats', selector: '#stats-overview', title: 'Overview Cards', description: 'At-a-glance metrics: Total Plays, Listening Time, unique Artists, Albums, and Tracks you\'ve listened to in the selected time range.' },

            // Charts (left column)
            { page: 'stats', selector: '#stats-timeline-chart', title: 'Listening Activity', description: 'A timeline chart showing your listening pattern over time. Spot trends — are you listening more on weekends? Did you binge a new album last week?' },
            { page: 'stats', selector: '#stats-genre-chart', title: 'Genre Breakdown', description: 'Pie chart showing which genres you listen to most. The legend shows exact percentages. Useful for understanding your taste profile.' },
            { page: 'stats', selector: '#stats-recent-plays', title: 'Recently Played', description: 'A live feed of your most recent plays with timestamps, artist, and album info.' },

            // Rankings (right column)
            { page: 'stats', selector: '#stats-top-artists', title: 'Top Artists', description: 'Your most-played artists ranked by play count. The visual bar chart at the top shows relative listening time.' },
            { page: 'stats', selector: '#stats-top-albums', title: 'Top Albums', description: 'Most-played albums in the selected time range. Click any to navigate to the artist detail page.' },
            { page: 'stats', selector: '#stats-top-tracks', title: 'Top Tracks', description: 'Your most-played individual tracks. Great for building playlists from your actual favorites.' },

            // Library health
            { page: 'stats', selector: '#stats-library-health', title: 'Library Health', description: 'Technical metrics about your collection: audio format breakdown (FLAC vs MP3 vs others), unplayed tracks count, total duration, and total track count.' },
            { page: 'stats', selector: '#stats-enrichment-coverage', title: 'Enrichment Coverage', description: 'Shows how much of your library has been enriched with metadata from external services. Higher coverage means better search results and recommendations.' },

            // Storage
            { page: 'stats', selector: '#stats-db-storage-chart', title: 'Database Storage', description: 'A donut chart showing how your database space is used — metadata, cache, enrichment data, settings, etc. Helps you understand what\'s using disk space. 🎉' },
        ]
    },
    'import-music': {
        title: 'Import Music',
        description: 'Import existing audio files into your organized library.',
        icon: '📥',
        steps: [
            // Header
            { page: 'import', selector: '.import-page-header', title: 'Import Music', description: 'Import audio files from your import folder into your organized library. Files are matched to album metadata, tagged, and moved to the correct location.' },
            { page: 'import', selector: '.import-page-staging-bar', title: 'Import Folder', description: 'Shows your configured import folder path and stats (file count, total size). This is where you drop audio files before importing. Configure the path in Settings → Downloads.' },
            { page: 'import', selector: '.import-page-refresh-btn', title: 'Refresh', description: 'Re-scans your import folder for new audio files. Hit this after dropping new files in.' },

            // Queue
            { page: 'import', selector: '#import-page-queue', title: 'Processing Queue', description: 'When you process albums or singles, jobs appear here with progress indicators. "Clear finished" removes completed jobs from the list.' },

            // Tabs
            { page: 'import', selector: '.import-page-tab-bar', title: 'Albums vs Singles', description: 'Two modes: Albums tab matches full albums to metadata (cover art, track numbers, disc info). Singles tab processes individual files one at a time.' },

            // Album workflow
            { page: 'import', selector: '#import-page-suggestions', title: 'Album Suggestions', description: 'The importer analyzes your import files and suggests album matches based on embedded tags. Click a suggestion to start the matching process.' },
            { page: 'import', selector: '#import-page-album-search-input', title: 'Album Search', description: 'If suggestions don\'t match, search manually. Type an album name, click Search, and select the correct result.' },
            { page: 'import', selector: '#import-page-album-search-input', title: 'Track Matching', description: 'After selecting an album, you\'ll see a track matching table. Files are auto-matched to tracks by name/number. Drag unmatched files from the pool to the correct track slot, then click "Process Album".' },

            // Singles workflow
            { page: 'import', selector: '#import-page-tab-singles', title: 'Singles Import', description: 'The Singles tab lists all individual audio files. Select files with checkboxes (or "Select All"), then click "Process Selected" to tag and move them into your library. 🎉' },
        ]
    },
    'settings-tour': {
        title: 'Settings Walkthrough',
        description: 'Configure services, downloads, and preferences.',
        icon: '⚙️',
        steps: [
            // Tab bar
            { page: 'settings', selector: '.stg-tabbar', title: 'Settings Tabs', description: 'Settings are organized into 5 tabs: Connections (API keys, server setup), Downloads (sources, paths, quality), Library (file organization, post-processing), Appearance (theme, colors), and Advanced.' },

            // Connections
            { page: 'settings', selector: '.stg-tab[data-tab="connections"]', title: 'Connections Tab', description: 'This is where you connect all your services. API keys for Spotify, Tidal, Last.fm, Genius, AcoustID, and your metadata source preference. Plus your media server (Plex, Jellyfin, or Navidrome).' },
            { page: 'settings', selector: '.api-service-frame', title: 'API Configuration', description: 'Each service has its own frame with credential fields and an Authenticate/Test button. Spotify needs a Client ID + Secret from the Developer Dashboard. Last.fm needs an API key for scrobbling and stats.' },
            { page: 'settings', selector: '.server-toggle-container', title: 'Media Server', description: 'Toggle on your media server — Plex, Jellyfin, or Navidrome. Enter the server URL and token/API key. This is where your music library lives and where downloads get synced to.' },

            // Downloads
            { page: 'settings', selector: '.stg-tab[data-tab="downloads"]', title: 'Downloads Tab', description: 'Configure where music comes from and where it goes. Set your download source (Soulseek, YouTube, Tidal, Qobuz, HiFi, Deezer, or Hybrid mode), download paths, and quality preferences.' },
            { page: 'settings', selector: '.stg-tab[data-tab="downloads"]', title: 'Quality Profiles', description: 'Quality profiles control what files are acceptable — format (FLAC, MP3, etc.), minimum bitrate, bit depth preference, and peer speed requirements. The waterfall filter tries your preferred format first, then falls back.' },

            // Library
            { page: 'settings', selector: '.stg-tab[data-tab="library"]', title: 'Library Tab', description: 'File organization templates (folder structure, naming), post-processing rules (auto-tag, convert formats), M3U playlist export settings, and content filtering options.' },

            // Appearance
            { page: 'settings', selector: '.stg-tab[data-tab="appearance"]', title: 'Appearance Tab', description: 'Customize the UI — accent color picker to theme the entire interface to your taste.' },

            // Advanced
            { page: 'settings', selector: '.stg-tab[data-tab="advanced"]', title: 'Advanced Tab', description: 'Power-user settings, logging configuration, and system-level options. Most users won\'t need to touch this.' },

            // Save
            { page: 'settings', selector: '.save-button', title: 'Save Settings', description: 'Don\'t forget to save! Changes aren\'t applied until you click this button. Some settings (like download source changes) take effect immediately after saving. 🎉' },
        ]
    },
    'issues-tour': {
        title: 'Issues Tracker',
        description: 'Track and resolve problems in your library.',
        icon: '🐛',
        steps: [
            { page: 'issues', selector: '.issues-header', title: 'Issues Tracker', description: 'A built-in issue tracker for your music library. Report wrong tracks, bad metadata, missing albums, audio quality problems, and more. Issues are tracked through open → in progress → resolved.' },
            { page: 'issues', selector: '#issues-filters', title: 'Filters', description: 'Filter by status (Open, In Progress, Resolved, Dismissed) and category (Wrong Track, Wrong Artist, Audio Quality, Missing Tracks, Incomplete Album, etc.).' },
            { page: 'issues', selector: '#issues-stats', title: 'Stats Bar', description: 'Quick count of issues by status. Helps you see at a glance how many open issues need attention.' },
            { page: 'issues', selector: '#issues-list', title: 'Issues List', description: 'All issues matching your current filters. Click any issue to see details, add notes, change status, or take action (like re-downloading a track). 🎉' },
        ]
    },
};

function openTourSelector() {
    dismissHelperPopover();
    const popover = document.createElement('div');
    popover.className = 'helper-popover helper-tour-selector';
    popover.innerHTML = `
        <div class="helper-popover-header">
            <div class="helper-popover-title">Choose a Tour</div>
            <button class="helper-popover-close" onclick="exitHelperMode()">&times;</button>
        </div>
        <div class="helper-tour-list">
            ${Object.entries(HELPER_TOURS).map(([id, tour]) => `
                <button class="helper-tour-option" onclick="startTour('${id}')">
                    <span class="helper-tour-option-icon">${tour.icon || '🚶'}</span>
                    <div class="helper-tour-option-body">
                        <div class="helper-tour-option-title">${tour.title}</div>
                        <div class="helper-tour-option-desc">${tour.description}</div>
                    </div>
                    <div class="helper-tour-option-steps">${tour.steps.length} steps</div>
                </button>
            `).join('')}
        </div>
    `;
    document.body.appendChild(popover);
    _helperPopover = popover;

    // Position near the float button
    const floatBtn = document.getElementById('helper-float-btn');
    if (floatBtn) {
        const btnRect = floatBtn.getBoundingClientRect();
        popover.style.right = (window.innerWidth - btnRect.right) + 'px';
        popover.style.bottom = (window.innerHeight - btnRect.top + 8) + 'px';
        popover.style.left = 'auto';
        popover.style.top = 'auto';
    }
    requestAnimationFrame(() => popover.classList.add('visible'));
}

function startTour(tourId) {
    const tour = HELPER_TOURS[tourId];
    if (!tour) return;

    dismissHelperPopover();
    HelperState.tourId = tourId;
    HelperState.tourStep = 0;

    showTourStep();
}

function showTourStep() {
    const tour = HELPER_TOURS[HelperState.tourId];
    if (!tour) return;

    const step = tour.steps[HelperState.tourStep];
    if (!step) { dismissTour(); return; }

    dismissHelperPopover();
    removeTourOverlay();

    // Navigate to the correct page if needed
    if (step.page) {
        const currentPage = document.querySelector('.page.active')?.id?.replace('-page', '') || '';
        if (currentPage !== step.page) {
            navigateToPage(step.page);
            // Wait for page to render, then show the step
            setTimeout(() => _renderTourStep(tour, step), 350);
            return;
        }
    }

    _renderTourStep(tour, step);
}

function _renderTourStep(tour, step) {
    const target = document.querySelector(step.selector);

    // Create spotlight overlay
    _tourOverlay = document.createElement('div');
    _tourOverlay.className = 'helper-tour-overlay';
    _tourOverlay.addEventListener('click', (e) => {
        if (e.target === _tourOverlay) dismissTour();
    });
    document.body.appendChild(_tourOverlay);

    // Highlight target
    if (target) {
        target.classList.add('helper-tour-target');
        _helperHighlighted = target;
        setTimeout(() => target.scrollIntoView({ behavior: 'smooth', block: 'center' }), 50);
    }

    // Build tour popover
    const stepNum = HelperState.tourStep + 1;
    const totalSteps = tour.steps.length;
    const isFirst = stepNum === 1;
    const isLast = stepNum === totalSteps;
    const progressPct = (stepNum / totalSteps * 100).toFixed(0);

    const popover = document.createElement('div');
    popover.className = 'helper-popover helper-tour-popover';
    popover.innerHTML = `
        <div class="helper-popover-arrow"></div>
        <div class="helper-tour-progress-bar">
            <div class="helper-tour-progress-fill" style="width:${progressPct}%"></div>
        </div>
        <div class="helper-tour-step-counter">Step ${stepNum} of ${totalSteps}</div>
        <div class="helper-popover-header">
            <div class="helper-popover-title">${step.title}</div>
        </div>
        <div class="helper-popover-desc">${step.description}</div>
        <div class="helper-tour-nav">
            ${!isFirst ? '<button class="helper-tour-btn" onclick="prevTourStep()">← Back</button>' : '<div></div>'}
            <button class="helper-tour-btn helper-tour-btn-skip" onclick="dismissTour()">Exit Tour</button>
            ${!isLast ? '<button class="helper-tour-btn helper-tour-btn-next" onclick="nextTourStep()">Next →</button>'
                       : '<button class="helper-tour-btn helper-tour-btn-next" onclick="dismissTour()">Done ✓</button>'}
        </div>
    `;
    document.body.appendChild(popover);
    _helperPopover = popover;

    // Position near target with smooth animation
    if (target) {
        requestAnimationFrame(() => {
            setTimeout(() => positionPopover(popover, target), 100);
        });
    } else {
        // Target not found on this page — center the popover
        popover.style.left = '50%';
        popover.style.top = '40%';
        popover.style.transform = 'translate(-50%, -50%)';
        requestAnimationFrame(() => popover.classList.add('visible'));
    }
}

function nextTourStep() {
    const tour = HELPER_TOURS[HelperState.tourId];
    if (!tour) return;
    if (HelperState.tourStep < tour.steps.length - 1) {
        HelperState.tourStep++;
        showTourStep();
    } else {
        dismissTour();
    }
}

function prevTourStep() {
    if (HelperState.tourStep > 0) {
        HelperState.tourStep--;
        showTourStep();
    }
}

function dismissTour() {
    HelperState.tourId = null;
    HelperState.tourStep = 0;
    removeTourOverlay();
    dismissHelperPopover();
    if (HelperState.mode === 'tour') {
        HelperState.mode = null;
        const floatBtn = document.getElementById('helper-float-btn');
        if (floatBtn) floatBtn.classList.remove('active');
    }
}

function removeTourOverlay() {
    if (_tourOverlay) {
        _tourOverlay.remove();
        _tourOverlay = null;
    }
    // Clean up ALL tour targets (not just the tracked one — page nav can lose reference)
    document.querySelectorAll('.helper-tour-target').forEach(el => el.classList.remove('helper-tour-target'));
    document.querySelectorAll('.helper-highlight').forEach(el => el.classList.remove('helper-highlight'));
    _helperHighlighted = null;
}

// ═══════════════════════════════════════════════════════════════════════════
// CLICK INTERCEPTION (Element Info mode)
// ═══════════════════════════════════════════════════════════════════════════

document.addEventListener('click', function(e) {
    if (!helperModeActive) return;

    // Allow clicking helper UI elements
    const floatBtn = document.getElementById('helper-float-btn');
    if (floatBtn && (e.target === floatBtn || floatBtn.contains(e.target))) return;
    if (_helperPopover && _helperPopover.contains(e.target)) return;
    if (_helperMenu && _helperMenu.contains(e.target)) return;

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

    dismissHelperPopover();
}, true);

// ── Keyboard Navigation ──────────────────────────────────────────────────

document.addEventListener('keydown', function(e) {
    if (e.key === 'Escape') {
        if (_helperPopover) { dismissHelperPopover(); return; }
        if (HelperState.tourId) { dismissTour(); return; }
        if (HelperState.mode) { exitHelperMode(); return; }
        if (HelperState.menuOpen) { closeHelperMenu(); return; }
    }
    // Arrow keys for tour navigation
    if (HelperState.tourId) {
        if (e.key === 'ArrowRight' || e.key === 'ArrowDown') { e.preventDefault(); nextTourStep(); }
        if (e.key === 'ArrowLeft' || e.key === 'ArrowUp') { e.preventDefault(); prevTourStep(); }
    }
    // ? opens helper menu (when not typing in an input)
    if (e.key === '?' && !e.ctrlKey && !e.metaKey) {
        const tag = document.activeElement?.tagName;
        if (tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT') return;
        if (document.activeElement?.isContentEditable) return;
        e.preventDefault();
        toggleHelperMode();
    }
    // Ctrl+K / Cmd+K opens helper search
    if (e.key === 'k' && (e.ctrlKey || e.metaKey)) {
        e.preventDefault();
        if (HelperState.mode === 'search') { exitHelperMode(); return; }
        if (HelperState.mode) exitHelperMode();
        activateHelperMode('search');
    }
});

// ═══════════════════════════════════════════════════════════════════════════
// POPOVER DISPLAY
// ═══════════════════════════════════════════════════════════════════════════

function showHelperPopover(targetEl, content) {
    dismissHelperPopover();

    targetEl.classList.add('helper-highlight');
    _helperHighlighted = targetEl;

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

    let actionsHtml = '';
    if (content.actions && content.actions.length) {
        actionsHtml = `<div class="helper-popover-actions">
            ${content.actions.map(a => `<button class="helper-action-btn">${a.label}</button>`).join('')}
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
        ${actionsHtml}
        ${docsLink}
    `;

    // Bind action click handlers
    if (content.actions && content.actions.length) {
        popover.querySelectorAll('.helper-action-btn').forEach((btn, i) => {
            btn.addEventListener('click', () => {
                exitHelperMode();
                content.actions[i].onClick();
            });
        });
    }

    document.body.appendChild(popover);
    _helperPopover = popover;
    requestAnimationFrame(() => positionPopover(popover, targetEl));
}

function positionPopover(popover, targetEl) {
    const rect = targetEl.getBoundingClientRect();
    const popRect = popover.getBoundingClientRect();
    const margin = 14;
    const arrowEl = popover.querySelector('.helper-popover-arrow');

    let left = rect.right + margin;
    let top = rect.top + (rect.height / 2) - (popRect.height / 2);
    let arrowSide = 'left';

    if (left + popRect.width > window.innerWidth - 20) {
        left = rect.left - popRect.width - margin;
        arrowSide = 'right';
    }
    if (left < 20) {
        left = rect.left + (rect.width / 2) - (popRect.width / 2);
        top = rect.bottom + margin;
        arrowSide = 'top';
    }

    left = Math.max(12, Math.min(left, window.innerWidth - popRect.width - 12));
    top = Math.max(12, Math.min(top, window.innerHeight - popRect.height - 12));

    popover.style.left = left + 'px';
    popover.style.top = top + 'px';

    if (arrowEl) arrowEl.className = 'helper-popover-arrow arrow-' + arrowSide;

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

// ═══════════════════════════════════════════════════════════════════════════
// SETUP PROGRESS TRACKER (Phase 2)
// ═══════════════════════════════════════════════════════════════════════════

const SETUP_STEPS = [
    { id: 'metadata-source', label: 'Connect Metadata Source',      desc: 'Spotify, iTunes, or Deezer for album/artist info',   icon: '🎵', page: 'settings' },
    { id: 'media-server',    label: 'Connect Media Server',         desc: 'Plex, Jellyfin, or Navidrome',                       icon: '🖥️', page: 'settings' },
    { id: 'download-source', label: 'Set Up Download Source',       desc: 'Soulseek, YouTube, Tidal, Qobuz, HiFi, or Deezer',  icon: '⬇️', page: 'settings', settingsTab: 'downloads' },
    { id: 'download-paths',  label: 'Configure Download Paths',     desc: 'Where music is saved and organized',                 icon: '📁', page: 'settings', settingsTab: 'downloads' },
    { id: 'first-scan',      label: 'Run First Library Scan',       desc: 'Import your existing collection from media server',  icon: '🔍', page: 'dashboard', selector: '#db-updater-card' },
    { id: 'first-download',  label: 'Download Your First Track',    desc: 'Search for and download something',                  icon: '🎶', page: 'search' },
    { id: 'watchlist',       label: 'Add an Artist to Watchlist',   desc: 'Monitor for new releases automatically',             icon: '👁️', page: 'library' },
    { id: 'automation',      label: 'Create an Automation',         desc: 'Schedule tasks and build workflows',                 icon: '🤖', page: 'automations' },
];

function _getSetupCompletion() {
    return JSON.parse(localStorage.getItem('soulsync_setup') || '{}');
}

function _markSetupComplete(stepId) {
    const stored = _getSetupCompletion();
    stored[stepId] = Date.now();
    localStorage.setItem('soulsync_setup', JSON.stringify(stored));
}

async function _checkSetupStatus() {
    const completion = _getSetupCompletion();
    const results = { ...completion };

    // ── /status — checks metadata_source, media_server, soulseek ────────
    try {
        const resp = await fetch('/status');
        if (resp.ok) {
            const data = await resp.json();
            // Metadata source is available when status reports a source.
            if (data.metadata_source?.source) {
                results['metadata-source'] = results['metadata-source'] || Date.now();
                _markSetupComplete('metadata-source');
            }
            // Media server: single object, not per-server keys
            if (data.media_server?.connected) {
                results['media-server'] = results['media-server'] || Date.now();
                _markSetupComplete('media-server');
            }
            // Download source
            if (data.soulseek?.connected) {
                results['download-source'] = results['download-source'] || Date.now();
                _markSetupComplete('download-source');
            }
        }
    } catch (e) { /* API unavailable — use cached */ }

    // ── /api/settings — checks download paths (nested under soulseek.*) ─
    try {
        const resp = await fetch('/api/settings');
        if (resp.ok) {
            const cfg = await resp.json();
            if (cfg.soulseek?.download_path || cfg.soulseek?.transfer_path) {
                results['download-paths'] = results['download-paths'] || Date.now();
                _markSetupComplete('download-paths');
            }
        }
    } catch (e) { /* skip */ }

    // ── /api/library/artists — checks if library has been scanned ────────
    if (!results['first-scan']) {
        try {
            const resp = await fetch('/api/library/artists?page=1&limit=1');
            if (resp.ok) {
                const data = await resp.json();
                if (data.total_count > 0 || (data.artists && data.artists.length > 0)) {
                    results['first-scan'] = Date.now();
                    _markSetupComplete('first-scan');
                }
            }
        } catch (e) { /* skip */ }
    }

    // ── /api/watchlist/count — checks if any artist is watched ───────────
    if (!results['watchlist']) {
        try {
            const resp = await fetch('/api/watchlist/count');
            if (resp.ok) {
                const data = await resp.json();
                if (data.count > 0) {
                    results['watchlist'] = Date.now();
                    _markSetupComplete('watchlist');
                }
            }
        } catch (e) { /* skip */ }
    }

    // ── /api/automations — checks if any custom automations exist ────────
    if (!results['automation']) {
        try {
            const resp = await fetch('/api/automations');
            if (resp.ok) {
                const autos = await resp.json();
                // Filter to custom (non-system) automations
                const custom = Array.isArray(autos) ? autos.filter(a => !a.is_system) : [];
                if (custom.length > 0) {
                    results['automation'] = Date.now();
                    _markSetupComplete('automation');
                }
            }
        } catch (e) { /* skip */ }
    }

    // ── first-download: check dashboard stat card or finished queue ────────
    if (!results['first-download']) {
        // Dashboard stat card shows "X Completed this session"
        const finishedCard = document.querySelector('#finished-downloads-card .stat-card-value');
        const finishedVal = finishedCard ? parseInt(finishedCard.textContent) : 0;
        if (finishedVal > 0) {
            results['first-download'] = Date.now();
            _markSetupComplete('first-download');
        }
        // (The legacy #finished-queue side-panel was retired; the dashboard stat card
        // above is now the single source of truth for the first-download milestone.)
    }

    return results;
}

async function openSetupPanel() {
    closeSetupPanel();

    // Show loading state immediately
    const loader = document.createElement('div');
    loader.className = 'helper-setup-panel visible';
    loader.innerHTML = `
        <div class="helper-setup-header">
            <div class="helper-setup-title-row">
                <h3 class="helper-setup-title">Setup Progress</h3>
                <button class="helper-popover-close" onclick="exitHelperMode()">&times;</button>
            </div>
        </div>
        <div class="helper-setup-loading">
            <div class="loading-spinner"></div>
            <span>Checking your setup...</span>
        </div>
    `;
    document.body.appendChild(loader);
    _setupPanel = loader;

    const status = await _checkSetupStatus();

    // Replace loader with real panel
    if (_setupPanel) _setupPanel.remove();
    const completedCount = SETUP_STEPS.filter(s => status[s.id]).length;
    const totalCount = SETUP_STEPS.length;
    const pct = Math.round((completedCount / totalCount) * 100);

    const panel = document.createElement('div');
    panel.className = 'helper-setup-panel';
    panel.innerHTML = `
        <div class="helper-setup-header">
            <div class="helper-setup-title-row">
                <h3 class="helper-setup-title">Setup Progress</h3>
                <button class="helper-popover-close" onclick="exitHelperMode()">&times;</button>
            </div>
            <div class="helper-setup-ring-row">
                <div class="helper-setup-ring">
                    <svg viewBox="0 0 36 36" class="helper-setup-ring-svg">
                        <path d="M18 2.0845 a 15.9155 15.9155 0 0 1 0 31.831 a 15.9155 15.9155 0 0 1 0 -31.831"
                              fill="none" stroke="rgba(255,255,255,0.06)" stroke-width="3"/>
                        <path d="M18 2.0845 a 15.9155 15.9155 0 0 1 0 31.831 a 15.9155 15.9155 0 0 1 0 -31.831"
                              fill="none" stroke="rgb(var(--accent-rgb))" stroke-width="3"
                              stroke-dasharray="${pct}, 100" stroke-linecap="round"
                              class="helper-setup-ring-progress"/>
                    </svg>
                    <span class="helper-setup-ring-text">${pct}%</span>
                </div>
                <div class="helper-setup-summary">
                    <span class="helper-setup-count">${completedCount} of ${totalCount}</span>
                    <span class="helper-setup-label">steps complete</span>
                </div>
            </div>
        </div>
        <div class="helper-setup-list">
            ${SETUP_STEPS.map(step => {
                const done = !!status[step.id];
                return `
                    <div class="helper-setup-item ${done ? 'done' : ''}" data-step="${step.id}">
                        <div class="helper-setup-check">${done ? '✓' : step.icon}</div>
                        <div class="helper-setup-body">
                            <div class="helper-setup-item-label">${step.label}</div>
                            <div class="helper-setup-item-desc">${step.desc}</div>
                        </div>
                        ${!done ? `<button class="helper-setup-go" onclick="setupGoTo('${step.id}')">Start →</button>` : ''}
                    </div>`;
            }).join('')}
        </div>
        ${pct === 100 ? '<div class="helper-setup-done">All set! SoulSync is fully configured. 🎉</div>' : ''}
    `;

    document.body.appendChild(panel);
    _setupPanel = panel;
    requestAnimationFrame(() => panel.classList.add('visible'));
}

function setupGoTo(stepId) {
    const step = SETUP_STEPS.find(s => s.id === stepId);
    if (!step) return;
    exitHelperMode();
    navigateToPage(step.page);
    if (step.settingsTab) {
        setTimeout(() => typeof switchSettingsTab === 'function' && switchSettingsTab(step.settingsTab), 400);
    }
    if (step.selector) {
        setTimeout(() => {
            const el = document.querySelector(step.selector);
            if (el) el.scrollIntoView({ behavior: 'smooth', block: 'center' });
        }, 500);
    }
}

function closeSetupPanel() {
    if (_setupPanel) { _setupPanel.remove(); _setupPanel = null; }
}

// ═══════════════════════════════════════════════════════════════════════════
// KEYBOARD SHORTCUT OVERLAY (Phase 4)
// ═══════════════════════════════════════════════════════════════════════════

const KEYBOARD_SHORTCUTS = [
    // Global
    { key: '?',     desc: 'Open helper menu',             scope: 'Global' },
    { key: 'Ctrl+K', desc: 'Search help topics',          scope: 'Global' },
    { key: 'Esc',   desc: 'Close modal / Exit helper',    scope: 'Global' },

    // Player
    { key: 'Space', desc: 'Play / Pause',                 scope: 'Player' },
    { key: '←',     desc: 'Skip back 5 seconds',          scope: 'Player' },
    { key: '→',     desc: 'Skip forward 5 seconds',       scope: 'Player' },
    { key: '↑',     desc: 'Volume up 5%',                 scope: 'Player' },
    { key: '↓',     desc: 'Volume down 5%',               scope: 'Player' },
    { key: 'M',     desc: 'Mute / Unmute',                scope: 'Player' },

    // Helper
    { key: '←/→',   desc: 'Navigate tour steps',          scope: 'Helper Tours' },

    // Forms
    { key: 'Enter', desc: 'Submit / Confirm / Search',    scope: 'Forms & Search' },
    { key: 'Esc',   desc: 'Cancel edit / Close search',   scope: 'Forms & Search' },
];

let _shortcutsCloseHandler = null;

function openShortcutsOverlay() {
    closeShortcutsOverlay();

    // Group by scope
    const groups = {};
    KEYBOARD_SHORTCUTS.forEach(s => {
        if (!groups[s.scope]) groups[s.scope] = [];
        groups[s.scope].push(s);
    });

    const overlay = document.createElement('div');
    overlay.className = 'helper-shortcuts-overlay';
    overlay.innerHTML = `
        <div class="helper-shortcuts-panel">
            <div class="helper-shortcuts-header">
                <h3>Keyboard Shortcuts</h3>
                <span class="helper-shortcuts-hint">Press any key to dismiss</span>
            </div>
            <div class="helper-shortcuts-grid">
                ${Object.entries(groups).map(([scope, shortcuts]) => `
                    <div class="helper-shortcuts-group">
                        <div class="helper-shortcuts-scope">${scope}</div>
                        ${shortcuts.map(s => `
                            <div class="helper-shortcut-row">
                                <kbd class="helper-kbd">${s.key}</kbd>
                                <span class="helper-shortcut-desc">${s.desc}</span>
                            </div>
                        `).join('')}
                    </div>
                `).join('')}
            </div>
        </div>
    `;

    overlay.addEventListener('click', (e) => {
        if (e.target === overlay) exitHelperMode();
    });
    document.body.appendChild(overlay);
    _shortcutsOverlay = overlay;
    requestAnimationFrame(() => overlay.classList.add('visible'));

    // Dismiss on any keypress (except the initial ?)
    _shortcutsCloseHandler = (e) => {
        if (e.key === '?') return; // ignore the key that opened us
        exitHelperMode();
    };
    setTimeout(() => document.addEventListener('keydown', _shortcutsCloseHandler), 200);
}

function closeShortcutsOverlay() {
    if (_shortcutsCloseHandler) {
        document.removeEventListener('keydown', _shortcutsCloseHandler);
        _shortcutsCloseHandler = null;
    }
    if (_shortcutsOverlay) { _shortcutsOverlay.remove(); _shortcutsOverlay = null; }
}

// ═══════════════════════════════════════════════════════════════════════════
// SEARCH WITHIN HELPER (Phase 5)
// ═══════════════════════════════════════════════════════════════════════════

function openHelperSearch() {
    closeHelperSearch();

    const panel = document.createElement('div');
    panel.className = 'helper-search-panel';
    panel.innerHTML = `
        <div class="helper-search-header">
            <div class="helper-search-input-wrap">
                <span class="helper-search-icon">🔍</span>
                <input type="text" class="helper-search-input" placeholder="Search help topics..." autofocus>
            </div>
            <button class="helper-popover-close" onclick="exitHelperMode()">&times;</button>
        </div>
        <div class="helper-search-results">
            <div class="helper-search-hint">Type to search 200+ help topics, tours, and shortcuts...</div>
        </div>
    `;

    document.body.appendChild(panel);
    _helperSearchPanel = panel;

    const input = panel.querySelector('.helper-search-input');
    const resultsContainer = panel.querySelector('.helper-search-results');

    input.addEventListener('input', () => {
        const q = input.value.trim().toLowerCase();
        if (q.length < 2) {
            resultsContainer.innerHTML = '<div class="helper-search-hint">Type to search 200+ help topics, tours, and shortcuts...</div>';
            return;
        }

        const matches = [];

        // Search HELPER_CONTENT
        for (const [selector, content] of Object.entries(HELPER_CONTENT)) {
            const haystack = (content.title + ' ' + content.description + ' ' + (content.tips || []).join(' ')).toLowerCase();
            const idx = haystack.indexOf(q);
            if (idx !== -1) {
                matches.push({ type: 'content', selector, title: content.title, desc: content.description, score: idx });
            }
        }

        // Search HELPER_TOURS
        for (const [id, tour] of Object.entries(HELPER_TOURS)) {
            const haystack = (tour.title + ' ' + tour.description).toLowerCase();
            const idx = haystack.indexOf(q);
            if (idx !== -1) {
                matches.push({ type: 'tour', tourId: id, title: tour.icon + ' ' + tour.title, desc: tour.description + ` (${tour.steps.length} steps)`, score: idx });
            }
        }

        // Search KEYBOARD_SHORTCUTS
        for (const shortcut of KEYBOARD_SHORTCUTS) {
            const haystack = (shortcut.key + ' ' + shortcut.desc + ' ' + shortcut.scope).toLowerCase();
            const idx = haystack.indexOf(q);
            if (idx !== -1) {
                matches.push({ type: 'shortcut', title: shortcut.key + ' — ' + shortcut.desc, desc: 'Scope: ' + shortcut.scope, score: idx + 100 });
            }
        }

        // Sort: title matches first, then by position
        matches.sort((a, b) => a.score - b.score);

        if (matches.length === 0) {
            resultsContainer.innerHTML = '<div class="helper-search-hint">No results found for "' + q.replace(/</g, '&lt;') + '"</div>';
            return;
        }

        resultsContainer.innerHTML = matches.slice(0, 20).map((m, i) => {
            const typeIcon = m.type === 'tour' ? '🚶' : m.type === 'shortcut' ? '⌨️' : '🎯';
            const typeLabel = m.type === 'tour' ? 'Tour' : m.type === 'shortcut' ? 'Shortcut' : 'Help';
            return `
                <button class="helper-search-result" data-idx="${i}">
                    <span class="helper-search-result-type" title="${typeLabel}">${typeIcon}</span>
                    <div class="helper-search-result-body">
                        <div class="helper-search-result-title">${_highlightMatch(m.title, q)}</div>
                        <div class="helper-search-result-desc">${m.desc.slice(0, 120)}${m.desc.length > 120 ? '...' : ''}</div>
                    </div>
                </button>`;
        }).join('');

        // Bind click handlers
        const displayedMatches = matches.slice(0, 20);
        resultsContainer.querySelectorAll('.helper-search-result').forEach((btn, i) => {
            btn.addEventListener('click', () => _handleSearchResultClick(displayedMatches[i]));
        });
    });

    // Position near float button
    const floatBtn = document.getElementById('helper-float-btn');
    if (floatBtn) {
        const btnRect = floatBtn.getBoundingClientRect();
        panel.style.right = (window.innerWidth - btnRect.right) + 'px';
        panel.style.bottom = (window.innerHeight - btnRect.top + 8) + 'px';
    }

    requestAnimationFrame(() => {
        panel.classList.add('visible');
        input.focus();
    });
}

function _highlightMatch(text, query) {
    const idx = text.toLowerCase().indexOf(query.toLowerCase());
    if (idx === -1) return text;
    return text.slice(0, idx) + '<mark>' + text.slice(idx, idx + query.length) + '</mark>' + text.slice(idx + query.length);
}

function _handleSearchResultClick(match) {
    if (match.type === 'tour') {
        exitHelperMode();
        setTimeout(() => {
            HelperState.mode = 'tour';
            const floatBtn = document.getElementById('helper-float-btn');
            if (floatBtn) floatBtn.classList.add('active');
            startTour(match.tourId);
        }, 100);
    } else if (match.type === 'content') {
        exitHelperMode();

        // Try to find the element on the current page first
        let el = document.querySelector(match.selector);
        if (el && el.offsetParent !== null) {
            el.scrollIntoView({ behavior: 'smooth', block: 'center' });
            setTimeout(() => showHelperPopover(el, HELPER_CONTENT[match.selector]), 300);
            return;
        }

        // Element not visible — try to detect which page it's on from the selector
        const pageHint = _guessPageFromSelector(match.selector);
        if (pageHint) {
            navigateToPage(pageHint);
            setTimeout(() => {
                const el2 = document.querySelector(match.selector);
                if (el2) {
                    el2.scrollIntoView({ behavior: 'smooth', block: 'center' });
                    setTimeout(() => showHelperPopover(el2, HELPER_CONTENT[match.selector]), 300);
                }
            }, 400);
        }
    } else if (match.type === 'shortcut') {
        exitHelperMode();
        setTimeout(() => activateHelperMode('shortcuts'), 100);
    }
}

function _guessPageFromSelector(selector) {
    // Map well-known selector prefixes/patterns to pages
    const pageHints = {
        'sync':        ['sync-tab', 'sync-header', 'sync-sidebar', 'playlist-header', 'spotify-refresh', 'tidal-refresh', 'deezer-url', 'youtube-url', 'spotify-public', 'import-file-icon', 'mirrored'],
        'downloads':   ['enh-', 'enhanced-search', 'search-mode', 'download-manager', 'toggle-download-manager'],
        'discover':    ['discover-', 'spotify-library', 'recent-releases', 'seasonal', 'release-radar', 'discovery-weekly', 'build-playlist', 'listenbrainz', 'decade-tabs', 'genre-tabs', 'daily-mixes', 'personalized-'],
        'artists':     ['artists-search', 'artists-hero', 'artist-detail', 'similar-artists'],
        'automations': ['automations-', 'auto-', 'builder-'],
        'library':     ['library-', 'alphabet-selector', 'watchlist-filter'],
        'stats':       ['stats-'],
        'import':      ['import-page-'],
        'settings':    ['settings-', 'stg-tab', 'api-service', 'server-toggle', 'save-button', 'spotify-client', 'soulseek-url', 'quality-profile'],
        'issues':      ['issues-'],
        'dashboard':   ['dashboard-', 'service-card', 'watchlist-button', 'wishlist-button', 'db-updater', 'metadata-updater', 'quality-scanner', 'duplicate-cleaner', 'discovery-pool-card', 'retag-tool', 'media-scan', 'backup-manager', 'metadata-cache'],
    };

    const selectorLower = selector.toLowerCase();
    for (const [page, patterns] of Object.entries(pageHints)) {
        for (const pattern of patterns) {
            if (selectorLower.includes(pattern.toLowerCase())) {
                return page;
            }
        }
    }
    return null;
}

function closeHelperSearch() {
    if (_helperSearchPanel) { _helperSearchPanel.remove(); _helperSearchPanel = null; }
}

// ═══════════════════════════════════════════════════════════════════════════
// WHAT'S NEW (Phase 6)
// ═══════════════════════════════════════════════════════════════════════════

// Entries tagged with `unreleased: true` are accumulating under a version label
// but won't display until the build version catches up — used for in-progress
// projects that span multiple commits before shipping. Strip the flag at
// release time and add a real `date:` line at the top of the version block.
const WHATS_NEW = {
    '2.5.2': [
        // --- May 13, 2026 — 2.5.2 release ---
        { date: 'May 13, 2026 — 2.5.2 release' },
        { title: 'Configurable Duration Tolerance For Quarantined Tracks', desc: 'discord question: tracks were quarantining when their actual length drifted by a few seconds from what spotify/musicbrainz reported (3s tolerance hardcoded, 5s for tracks >10min). live recordings, alternate masterings, and some legitimate uploads routinely drift more than that. new setting on settings → metadata → post-processing: "duration tolerance (seconds)". `0 = auto` (preserves the existing 3s/5s defaults). raise it to 10 / 15 / 20 if your library has a lot of drift-prone material. capped at 60s — past that the check is effectively off. applies to ALL matched downloads (soulseek / tidal / qobuz / hifi / youtube / deezer-direct) since they all flow through the same post-process integrity check. logic lifted to a pure helper `core/imports/file_integrity.py:resolve_duration_tolerance` that coerces the config value (none / empty / 0 / negative / unparseable / above-cap) to either a float override or `None` for the auto-scaled default. 12 tests pin every input shape.', page: 'settings' },
        { title: 'Soulseek Downloads: Multi-Artist Tags Now Get Written Properly', desc: 'discord report: tracks downloaded via soulseek were getting tagged with primary artist only (no collab artists), while the same track downloaded via deezer tagged everyone correctly. trace: the soulseek matched-download context constructed `original_search_result` with `artist` (singular string) but no `artists` (list), even though the full multi-artist list lived on `track_info` (the matched spotify track object). `core/metadata/source.py:extract_source_metadata` only read `original_search.artists`, so soulseek path always fell through to the single-artist branch. fix: lifted artist resolution into a pure helper `core/metadata/artist_resolution.py:resolve_track_artists` that walks `original_search.artists` → `track_info.artists` → `artist_dict.name` fallback chain. handles all three list-item shapes (spotify-style dicts, bare strings, anything else stringified). 13 tests pin the resolution order, fallback chain, mixed-shape normalization, whitespace stripping, empty/none handling. composes with the existing deezer per-track upgrade (still fires when single-artist + track_id available) and feat_in_title / artist_separator settings (still drive the joined ARTIST string downstream).', page: 'downloads' },
        { title: 'Download Missing Modal: Tracklist Got A Polish Pass', desc: 'visual tune-up only — column layout untouched. hairline row dividers, accent gradient + edge bar on hover, monospace track numbers (glow accent on row hover), monospace tabular duration. status text in both library-match + download-status columns picks up a leading colored dot with a soft halo (green found / amber missing / blue checking / orange downloading / red failed) and pulses while in-flight. artist column centered. soft scrollbar.', page: 'downloads' },
        { title: 'Search Source Picker: Fix Default Always Sticking To Spotify', desc: 'enhanced search + global search source picker always defaulted to spotify even when the user\'s primary metadata source was deezer / itunes / discogs / etc. trace: `shared-helpers.js:createSearchController` reads `/status.metadata_source` to pick the initial active icon, then checks `SOURCE_LABELS[src]` to validate. backend was returning `metadata_source` as a dict (`{source, connected, response_time, ...}` — used elsewhere for connection-state display), so `SOURCE_LABELS[<dict>]` was always undefined, the `if` guard never fired, and `state.activeSource` silently stayed at the hardcoded `\'spotify\'` default. fix: read `.source` off the dict (with forward-compat fallback to plain-string in case any older /status response shape predates the dict change). other consumers (core.js sidebar tile, helper.js status checker, search.js display) already used `?.source` correctly — this was the only stale call site.', page: 'search' },
        { title: 'Download Discography: No Longer Caps Prolific Artists At 50 Releases', desc: 'discord report: clicking "download discography" on an artist with a deep catalogue (bach, beatles complete box, dance / electronic artists with hundreds of remixes) only showed ~50 albums in the modal. trace: `MetadataLookupOptions(limit=50, max_pages=0)` was hardcoded at the discography endpoint and the artist-detail discography view. spotify\'s `max_pages=0` already paginates through everything (per-page is clamped to 10 internally) so spotify-primary users were unaffected. but deezer / itunes / discogs / hydrabase all honor the outer `limit` as a hard cap. fix: bump `limit` from 50 to 200 at all three call sites (`web_server.py` discography endpoint + artist-detail view + `core/artist_source_detail.py`). 200 matches iTunes\'s and Discogs\'s own internal caps and covers near-everyone\'s full catalogue. spotify behavior unchanged.', page: 'library' },
        { title: 'Artist Page: "Write Artist Image" Button (Real Artist Photos For Navidrome)', desc: 'github issue #572 (rhwc): navidrome shows album-art-derived thumbnails as artist photos because navidrome has no api for setting an artist image — it only reads `artist.jpg` from the artist folder during library scans. soulsync\'s `update_artist_poster` for navidrome was a no-op. new button on the artist detail page header writes `artist.jpg` to the artist\'s folder on disk: looks up any album track, resolves it through the path resolver (handles docker mount translation like #558 settled on), goes up one level to the artist folder, fetches the artist photo from the configured metadata source priority chain (spotify primary, fallback to deezer / discogs / etc), downloads with content-type validation + atomic write via `<filename>.tmp + os.replace`. when active server is navidrome, triggers a library scan immediately so the new file gets indexed. respects existing `artist.jpg` files (asks before overwriting) so user-supplied photos aren\'t clobbered. works for plex / jellyfin too as a fallback layer — both servers also read `artist.jpg` from disk. 26 tests pin the pure helpers in `core/library/artist_image.py`: folder derivation (trailing slash / backslash / empty / non-string), image url picking (missing attr / whitespace strip / non-string), download (non-image content-type / 404 / timeout / empty body), and write (atomic replace / temp-cleanup-on-failure / overwrite guard / missing folder).', page: 'library' },
        { title: 'Library History: Per-Download Audit Trail Modal', desc: 'each download row in library history now has an "audit" button that opens a second modal visualizing the download lifecycle as a vertical chain of decision blocks: request → source selected → source match → verification → post processing → final placement. each step has a status (complete / partial / unknown / error) with a color-coded node, plus a card showing what was decided and the supporting metadata. post-processing step infers observable changes from source-vs-final state (format conversion, file rename via tag template, title/artist rewrite, folder template). new "embedded tags" section below the flow reads the audio file live via mutagen at audit-open time and surfaces every tag actually on the file — title / artist / album / album artist / date / genre / track # / disc # / bpm / mood / style / copyright / publisher / release type+status+country / barcode / catalog # / asin / isrc / replaygain values / cover-art status / lyrics / every source id (spotify, tidal, deezer, musicbrainz, audiodb, lastfm, genius, itunes, beatport ...). file is the single source of truth — a persisted snapshot would drift the moment a background enrichment worker writes more tags. clean fallback when file is missing or unreadable. 19 tests pin the pure mutagen reader: id3 path (TIT2/TPE1/TALB + TXXX user-defined frames + USLT + APIC cover-art), vorbis path (FLAC dict-style + pass-through for unknown _id / _url keys), mp4 stub, format+bitrate+duration metadata, defensive paths (empty path, missing file, mutagen returns None, mutagen raises), stringify edge cases (list / tuple / int / frame-with-text / whitespace). files: core/library/file_tags.py (new mutagen reader), web_server.py (new GET /api/library/history/<id>/file-tags endpoint), webui/index.html (audit-overlay modal), webui/static/wishlist-tools.js (renderer + async fetch + tag-grid render), webui/static/style.css (flow + tags section + lyrics block styles).', page: 'wishlist' },
        { title: '$albumtype Folder Template Now Splits EPs / Singles For Non-Spotify Sources', desc: 'discord report (cal): downloading an artist\'s discography with `$albumtype` in the path template put every release under `Album/` regardless of actual type — eps, singles, all dumped into the album folder. trace: the legacy duck-typed album-info builder at `core/metadata/album_tracks.py:_build_album_info_legacy` only checked the `album_type` key. spotify uses `album_type` (lowercase) so spotify discographies worked. but deezer\'s api uses `record_type`, tidal uses `type` (uppercase ALBUM/EP/SINGLE), and some flattened musicbrainz shapes use `primary-type` — none of those matched, all defaulted to `album`. fix: widen the legacy lookup to check `album_type` / `record_type` / `type` / `primary-type` and route the value through a new pure `_normalize_album_type` helper that lowercases + validates against the canonical token set (`album` / `single` / `ep` / `compilation`) and falls back to `album` for unknowns. typed-converter path for spotify / deezer / itunes / discogs / musicbrainz / hydrabase / qobuz unchanged — they were already correct. tidal users were the main offender (no typed converter for dict-shaped tidal data). 25 new tests pin: case-insensitive normalization for each canonical type, compilation preserved (spotify supports it), unknown values default to album, defensive against none / empty / non-string inputs, multi-key precedence (`album_type` wins over `record_type`), each known source shape produces correct token, generic `type=track` / `type=artist` collision case defaults to album rather than poisoning the path.', page: 'tools' },
    ],
    '2.5.1': [
        // --- May 12, 2026 — 2.5.1 release ---
        { date: 'May 12, 2026 — 2.5.1 release' },
        { title: 'Soulseek: Min Delay Between Searches (Fixes ISP Anti-Abuse Trips)', desc: 'reddit report (yelomelo95, bell canada): isp anti-abuse cuts the wan after a burst of slskd searches. soulsync\'s sliding-window cap (35 searches per 220s) prevented soulseek-side bans but allowed all 35 in rapid succession — which is exactly the connection-burst pattern that trips isp throttling. new knob on settings → connections → soulseek: minimum delay between searches (default 0 = disabled, preserves prior behavior). set it to 5-10 seconds if your isp throttles peer-connection spikes. throttle math lifted to a pure `compute_search_wait_seconds` helper so the gate logic is testable independent of asyncio.sleep + the singleton client. 15 new tests pin: defaults / no-throttle, sliding-window cap (legacy), min-delay (the new burst-smoother), max-of-both gates, defensive paths.', page: 'tools' },
        { title: 'Help & Docs: Copy Debug Info Now Reports The Right Music Source + Lists All Services', desc: 'the music_source field always rendered as "unknown" because the code read `_status_cache.get(\'spotify\', {})` — but the cache only has \'media_server\' and \'soulseek\' keys, so the lookup always fell through. same silent miss for spotify_connected and spotify_rate_limited. fix routes those reads through the canonical accessors: `get_primary_source()` for music source (which already accounts for the spotify→deezer auth fallback), `get_spotify_status()` for connection + rate-limit state. also added hydrabase_connected (was missing entirely), youtube_available (always true — yt-dlp + url-based, no auth), hifi_instance_count (separate from connection because each instance is its own endpoint with its own auth), and an always_available_metadata_sources list (deezer / itunes / musicbrainz — public apis, no auth) so the dump reflects the full metadata surface. while in there: removed a local `from core.metadata.status import get_spotify_status` re-import that was making python 3.12 treat the name as a function-scoped local, breaking the new lambda above it (NameError on free variable). 11 new tests at the endpoint boundary pin music_source, spotify_*, hydrabase_*, youtube_available, always_available_metadata_sources, hifi_instance_count, and the defensive paths when each lookup raises.', page: 'tools' },
        { title: 'Download Discography: Skips Tracks Already In Your Library', desc: 'discord report (skowl): clicking download discography on the same artist twice re-queued every track instead of skipping the half already on disk. trace: the endpoint added each track via `add_to_wishlist`, which dedups against the wishlist itself but never checks the library — once a downloaded track leaves the wishlist the next click re-inserts it. fix: same library-ownership check the discography backfill repair job already runs (`db.check_track_exists` at confidence ≥ 0.7). format-agnostic — name + artist + album, no extension comparison — so blasphemy mode (flac → mp3 with original deleted) doesn\'t false-miss. exception during the check returns "not owned" so a transient db hiccup doesn\'t silently nuke the discography fetch (a redundant wishlist add is cheap, a missed track isn\'t). per-album response carries a new `tracks_skipped_owned` counter alongside the artist / content / wishlist skips. 10 new tests at the helper boundary.', page: 'discover' },
        { title: 'Download Discography: No More Cross-Artist Tracks Or Unwanted Remixes', desc: 'issue #559: download discography pulled in tracks from compilations / appears-on albums where the artist was only featured on one or two tracks — every other track on those albums got added too. also ignored your watchlist "include remixes / live / acoustic / instrumental" settings, so one-off discography downloads kept stuffing your wishlist with remix ladders. fix: per-track filter at the endpoint. drops tracks where the requested artist isn\'t named in the track\'s artists list (keeps features, drops unrelated compilation entries). honors `watchlist.global_include_*` settings the same way the discography backfill repair job already does. per-album response carries new skip counts so the ui can show how much got filtered. 21 new tests at the helper boundary.', page: 'discover' },
        { title: 'Album Completeness: "Could Not Determine Album Folder" Error Now Tells You What To Fix', desc: 'github issue #558 (gabistek, navidrome on docker / arch host): clicking auto-fill or fix selected on the album completeness findings page returned a flat "could not determine album folder from existing tracks" error with no diagnostic. trace: the path resolver in `core/library/path_resolver.py` probes transfer + download + `library.music_paths` config + plex api library locations to map db-recorded paths to actual files on disk. for plex users the api auto-discovers the mount paths (per #476). navidrome\'s subsonic api doesn\'t expose filesystem paths at all (only folder names via `getMusicFolders`), and navidrome\'s native rest api on top of that doesn\'t expose them either — there is no api signal we can probe. so for navidrome users in docker, if the path navidrome reports (`/music/artist/album/track.flac`) doesn\'t exist as-is in the soulsync container view AND the user hasn\'t manually configured settings → library → music paths, the resolver returns none and the fix workflow bailed silently. fix: lifted the resolver into a diagnostic-aware variant (`resolve_library_file_path_with_diagnostic` returning a `(resolved, ResolveAttempt)` tuple) that records what was tried — raw-path-existed, base-dirs-probed, whether config_manager / plex_client were wired up. repair_worker uses the diagnostic to render a multi-part error: names the active media server, shows one sample db-recorded path the album\'s tracks have, lists every base directory the resolver actually probed, and points at settings → library → music paths as the actionable fix. user can now read the error and know exactly what to mount or configure. no auto-probing of common docker conventions — too speculative, could resolve to wrong dirs on the suffix-walk if conventional paths happen to contain a partial collision. backwards compatible: legacy `resolve_library_file_path` kept as a thin wrapper that drops the attempt, every existing call site unchanged. 12 new tests pin: tuple shape, raw-path short-circuit attempt fields, base-dirs listed even on walk failure, had-flags reflect caller inputs, error renders active server name + sample path + base dirs, distinguishes empty-base-dirs vs tried-and-failed cases, settings hint always present, defensive against none attempt + missing sample + missing config_manager.', page: 'tools' },
        { title: 'Import History: Clear History Button Now Clears Stuck "Processing" Rows', desc: 'noticed on the import page: clear history left zombie rows behind that all showed "⧗ processing" status from 2-9 days ago. trace: `_record_in_progress` inserts a `status=\'processing\'` row up-front so the ui can render the in-flight import while it runs, then `_finalize_result` updates it to `completed`/`failed` when the import finishes. when the server is restarted mid-import (or the worker crashes), the row never gets finalized — stays at `processing` forever. the clear-history endpoint\'s sql `DELETE ... WHERE status IN (\'completed\', \'approved\', \'failed\', \'needs_identification\', \'rejected\')` didn\'t include `processing`, so those zombies survived every click. fix: add `processing` to the delete list, but guard against nuking actually-live imports by intersecting against `_snapshot_active()` — any folder hash currently registered in the worker\'s in-memory `_active_imports` map is excluded from the delete. `pending_review` deliberately left out so user still has to approve/reject those explicitly. one endpoint touched (`/api/auto-import/clear-completed` in web_server.py). no worker changes. zombie-row pile gets swept on next click, new imports still record + update normally.', page: 'import' },
        { title: 'Auto-Import: Falls Through To Other Metadata Sources When Primary Has No Match', desc: 'discord report (mushy): 16 bandcamp indie albums sat in staging because auto-import couldn\'t identify them. manual search at the bottom of the import music tab found the same albums fine — they just weren\'t on the user\'s primary metadata source (spotify) but existed on tidal/deezer. trace: `_search_metadata_source` in `core/auto_import_worker.py` only queried `get_primary_source()` — single source, no fallback. meanwhile `search_import_albums` (the manual search bar at the bottom of the tab) already iterated the full `get_source_priority(get_primary_source())` chain and broke on first source with results. asymmetric behavior — manual search worked, auto-import didn\'t, same album. fix: lift auto-import to use the same source-chain pattern. try primary first; if it returns nothing OR scores below the 0.4 threshold, fall through to next source in priority order. first source that produces a strong-enough match wins. result dict carries the `source` that actually matched (not the primary name), so downstream `_match_tracks` calls the right client to fetch the album\'s tracklist. defensive per-source try/except so a rate-limited or auth-failed source doesn\'t abort the chain. unconfigured sources (client=None) silently skipped. scoring math lifted to pure helper `_score_album_search_result` so weight tweaks (album 50% / artist 20% / track-count 30%) are pinned at the function boundary independent of the orchestrator. weight constants exposed at module level (`_ALBUM_NAME_WEIGHT`, `_ARTIST_NAME_WEIGHT`, `_TRACK_COUNT_WEIGHT`) — greppable, bumpable in one place. 9 integration tests + 18 scoring-helper tests. integration tests pin: primary-success path unchanged (no fallback fires, only primary client called), primary-empty falls through to next source, primary-weak-score falls through, first fallback success stops the chain (no wasted api calls on remaining sources), all-sources-fail returns None, per-source exception contained, unconfigured-source skipped gracefully, result `source` field reflects winning source, `identification_confidence` from winning source. backwards compatible — single-source users see no change (chain just has one entry).', page: 'import' },
        { title: 'Multi-Artist Tag Settings Now Actually Work (artist_separator + feat_in_title + write_multi_artist)', desc: 'three settings on settings → metadata → tags were partially or completely unimplemented. (1) `write_multi_artist` only worked because of a never-populated `_artists_list` field — `core/metadata/source.py` built `metadata["artist"]` as a hardcoded ", "-joined string but never assigned `metadata["_artists_list"]`, so `core/metadata/enrichment.py:114` always saw an empty list and silently no-op\'d the multi-value tag write. (2) `artist_separator` (default ", ") was referenced in the UI + settings.js save path but ZERO python code read the value — every multi-artist track ended up with hardcoded ", " regardless of what the user picked. (3) `feat_in_title` (when true: pull featured artists into the title as " (feat. X, Y)" and leave only primary in the ARTIST tag — picard convention) had no implementation at all. fix in source.py: populate `_artists_list` from the search response\'s artists array, then build the ARTIST string per the user\'s settings — primary-only when feat_in_title is on (with featured names appended to title; double-append guarded for source titles that already include "feat."), else joined with the configured separator. fix in enrichment.py id3 path: writing TPE1 twice (single-string then list) was overwriting the configured separator. now keeps TPE1 as the display string and writes a separate `TXXX:Artists` frame for the multi-value list (picard convention). vorbis path was already correct (separate "artist" + "artists" keys). deezer-specific upgrade path: deezer\'s `/search` endpoint only returns the primary artist — full contributors live on `/track/<id>`. when source==deezer AND the search response had a single artist AND a track_id is available, enrichment now fetches the per-track endpoint and upgrades the artists list before tag-write. one extra API call per affected deezer track (skipped when search already returned multiple). spotify, tidal, itunes search responses already include all artists so they\'re unaffected. 29 new tests pin: `_artists_list` populated for multi/single/no-artist cases, separator drives ARTIST string (default + custom), single-artist case unaffected by either setting, feat_in_title pulls featured to title + leaves primary in ARTIST, feat_in_title no-op for single artist, double-append guard recognizes 9 source-title variants ("(feat. X)", "(Feat. X)", "(FEAT X)", "(feat X)", "(Featuring X)", "[feat. X]", "ft. X", "(ft X)", "FT. X"), word-boundary regex doesn\'t false-match substrings ("Aftermath" still gets the append), combined-settings precedence (feat_in_title wins over separator for ARTIST string but `_artists_list` carries everyone for the multi-value tag), deezer upgrade fires only when search returned single artist + track_id available, no upgrade for non-deezer sources, upgrade failure falls through to search-result list, no false-positive when /track/<id> confirms single artist.', page: 'settings' },
        { title: 'AudioDB Enrichment: Track Worker No Longer Stuck In Infinite Retry Loop', desc: 'github issue #553: audiodb track enrichment "stuck" — constant requests, no progress, only error log was a 10s read-timeout from `lookup_track_by_id` repeating against the same track. trace: when an entity already has `audiodb_id` populated (from manual match or earlier scan) but `audiodb_match_status` is NULL, the worker tries a direct ID lookup. if it fails (returns None on timeout — audiodb\'s `track.php` endpoint is slow, 10s timeouts common), the prior code logged "preserving manual match" and returned WITHOUT marking status. row stayed NULL → queue picked it up next tick → tried direct lookup → timed out → returned → infinite loop. fix: (1) when direct lookup fails (None or exception), mark `audiodb_match_status="error"` so the queue\'s NULL-status filter stops re-picking the row on every tick. preserves the existing `audiodb_id` (no fallback to name-search guess that would overwrite a manual match). (2) extended the retry-after-cutoff queue priorities (4/5/6) to include `\'error\'` rows alongside `\'not_found\'` — same `retry_days=30` window. transient audiodb outages still recover automatically; permanently-broken IDs eventually get re-attempted once a month. only triggered for entities in the inconsistent state of `audiodb_id` set + `match_status` NULL — happy path and already-matched/already-not-found rows unchanged. 5 new tests pin: lookup-returns-none marks error (no infinite loop), lookup-raises-exception marks error, lookup-success preserves happy path, error-row-past-cutoff gets re-picked, error-row-within-cutoff stays skipped.', page: 'tools' },
        { title: 'Docker: Container No Longer Restart-Loops On Bind-Mounted Staging Folder', desc: 'after pulling latest, the container refused to start. logs showed `mkdir: cannot create directory \'/app/Staging\': Permission denied`. cause traced back to the 2026-05-08 image-bloat fix (commit 70e1750) which changed the Dockerfile from `chown -R /app` to a scoped chown on specific subdirs (the recursive chown was duplicating the whole /app tree into a new layer and ballooning image size). side effect: `/app` itself went from soulsync:soulsync to root:root (Docker WORKDIR default), AND `/app/Staging` was left out of both the Dockerfile mkdir + chown list and only created at runtime by the entrypoint script. on rootless Docker / Podman where in-container "root" maps to a host UID, the entrypoint mkdir on `/app/Staging` could fail with EACCES depending on the bind-mount path\'s host ownership — `set -e` then aborted the script and the container restart-looped. fix: (1) Dockerfile now pre-bakes `/app/Staging` into the image alongside the other runtime mount points (mkdir + scoped chown) so the entrypoint mkdir is a guaranteed no-op even when bind-mount perms are weird. (2) entrypoint mkdir + chown both have `|| true` now so any future bind-mount permission quirk surfaces as a log line, not a restart loop. (3) new writability audit at the end of entrypoint setup — `gosu soulsync test -w` on every bind-mountable dir, logs a loud warning with the exact `chown` command to run on the host if perms mismatch the configured PUID/PGID. catches the underlying bind-mount perm issue that the restart-loop fix would otherwise mask (container starts, but auto-import / downloads write into unwritable dirs and fail silently). zero behavior change for users whose containers were already starting fine; defensive against the rootless/podman config that broke after the image-bloat refactor.', page: 'tools' },
        { title: 'Your Albums: Download Missing Now Opens Selectable Modal + Tidal Resolution', desc: 'two-part fix to the your albums "download missing" flow on discover. (1) replaced the broken per-album direct-download loop with a selectable-grid modal mirroring the library page\'s download discography flow. clicking the download button now opens a checkbox grid showing every missing album (cover, title, artist, year, track count, source) with select all / deselect all controls. user picks what they actually want, hits "add to wishlist", each album\'s tracks get resolved + queued through the existing wishlist auto-download processor. matches the discography flow\'s per-album ndjson progress stream so users see ✓/✗ per album as it processes. previous loop fired direct downloads via `openDownloadMissingModalForYouTube` which the user reported as silently failing — "queuing 2/2" toast with no actual transfer activity. wishlist is the right destination for batch missing-album adds since it already handles retry, source fallback, dedup, and rate limiting. (2) added tidal source resolution. backend `/api/discover/album/<source>/<album_id>` got a new `tidal` source branch that calls a NEW `tidal_client.get_album_tracks(album_id)` method — two-phase fetch (cursor-walk `/v2/albums/<id>/relationships/items?include=items` for track refs + position metadata, batch-hydrate via existing `_get_tracks_batch` for artist/album names). track refs carry `meta.trackNumber` + `meta.volumeNumber` so multi-disc compilations render in album order. inline `?include=coverArt` lookup pulls the album cover too. single-album click flow (`openYourAlbumDownload`) gets `tidal_album_id` added to `trySources`. virtual-id generation includes tidal_album_id for stable identifiers. backend reuses the existing `/api/artist/<id>/download-discography` endpoint — its url artist_id param is functionally unused (per-album payload carries everything), so the modal posts with placeholder `your-albums` and gets multi-artist resolution for free. 10 new tests pin the tidal album-tracks method: single-page walk + hydration, multi-page cursor chain, multi-disc sort order, limit short-circuit, no-token short-circuit, http error returns empty, 429 propagates to rate_limited decorator, forward-compat type filter, partial-batch failure containment, empty-album short-circuit.', page: 'discover' },
        { title: 'AcoustID Scanner: File-Tag Fallback For Legacy Compilation Tracks', desc: 'follow-up to the compilation-album scanner fix. previous patch made the scanner read `tracks.track_artist` (per-track artist column) via COALESCE so compilation tracks would compare against the right value. but tracks downloaded BEFORE that column existed have track_artist=NULL — COALESCE falls back to album artist (the curator) and we\'re back to the wrong-comparison case. fix: explicit 3-tier resolution in `_scan_file` — (1) `tracks.track_artist` from DB if populated → trust it (respects manual edits from the enhanced library view), (2) audio file\'s ARTIST tag via mutagen if present → use it (tidal/spotify/deezer all write the per-track artist into the file at download time, so it\'s ground truth even when DB is stale), (3) album artist → final fallback for files without proper ARTIST tags AND no DB track_artist. file open is essentially free since acoustid is opening it for fingerprinting anyway. critical guard: when DB track_artist is populated (curated value), it always wins over file tag — protects users who edited DB but didn\'t re-tag the file from getting false-positive flags. closes the legacy-data gap without requiring a one-time DB backfill or a re-download. 5 new tests pin: file-tag-resolves-skowl-case (legacy NULL track_artist → file tag wins → no flag), tag-missing-falls-back-to-album-artist (preserves existing genuine-mismatch contract), mutagen-exception-swallowed (debug log, fall-through), tag-matches-DB no behavioral change, and the false-positive guard (DB populated → trumps stale file tag).', page: 'tools' },
        { title: 'Tidal Favorite Albums + Artists Now Show Up On Discover', desc: 'discover → your albums (and your artists) was returning nothing for tidal users regardless of how many albums/artists they\'d favorited. cause: `get_favorite_albums` and `get_favorite_artists` were calling the deprecated `/v2/favorites?filter[type]=ALBUMS|ARTISTS` endpoint, which returns 404 for personal favorites — that endpoint is scoped to collections the third-party app created itself, not the user\'s app-level favorites. the V1 fallback was also dead because modern OAuth tokens carry `collection.read` instead of the legacy `r_usr` scope V1 requires (returns 403). same root cause as the favorited tracks fix from #502. fix: rewire to the working V2 user-collection endpoints — `/v2/userCollectionAlbums/me/relationships/items` and `/v2/userCollectionArtists/me/relationships/items` — using the same cursor-paginated pattern shipped for tracks. ID enumeration lifted into a generic `_iter_collection_resource_ids(path, expected_type, max_ids)` helper so tracks/albums/artists all share one walker (~80 lines deduped). batch hydration via `/v2/{albums|artists}?filter[id]=...&include=...` with extended JSON:API include semantics — single request returns 20 albums + their artists + cover artworks all in `included[]`, parsed via two static helpers (`_first_artist_name`, `_first_artwork_url`) that map relationship refs to the included map. cover/profile images pick `files[0]` (largest variant Tidal returns, typically 1280×1280). public methods preserve the prior return shape so the discover aggregator in web_server.py stays byte-identical. 24 new tests pin: cursor-walker dispatch (correct path + type), included-map building, artist + artwork relationship resolution (full + missing + unknown-id), batch hydration parse for albums + artists, empty-input + HTTP-error short-circuits, BATCH_SIZE chunking (41 IDs → 20/20/1), end-to-end orchestrator behavior.', page: 'discover' },
        { title: 'Server Playlist Sync: Append Mode (Stop Overwriting User-Added Tracks)', desc: 'discord report (cjfc, 2026-04-26): syncing a spotify playlist to your server overwrote anything you\'d manually added to the server-side playlist. now there\'s a per-sync mode picker next to the Sync button on the playlist details modal: "Replace" (default, current behavior — delete + recreate) or "Append only" (preserve existing, only add tracks not already there). useful when the source platform caps playlist size (spotify 100-track limit) and you\'re manually building beyond it on the server. each server client (plex / jellyfin / navidrome) gets a new `append_to_playlist(name, tracks)` method that uses the server\'s native append api — plex `addItems`, jellyfin `POST /Playlists/<id>/Items`, navidrome subsonic `updatePlaylist?songIdToAdd=...`. no delete-recreate, no backup playlist created in append mode (preserves playlist creation date + metadata + non-soulsync-managed tracks). dedup-by-id ensures we never add a track that\'s already on the playlist (matched by ratingKey for plex, jellyfin guid id for jellyfin, song id for navidrome — server-native identity, not fuzzy title+artist match). falls back to `create_playlist` when the playlist doesn\'t exist yet (first sync). sync_service dispatches via the new mode flag through /api/sync/start; soulsync standalone has no playlist methods at all so the dispatch falls back to update_playlist with a warning log when append is requested against it. 15 new tests pin: missing playlist → create delegation, dedup filtering (existing ids skipped), short-circuit on no-new-tracks (no api call), failure paths return False without raising, contract listing for each server client.', page: 'sync' },
    ],
    '2.5.0': [
        // --- May 10, 2026 — 2.5.0 release ---
        { date: 'May 10, 2026 — 2.5.0 release' },
        { title: 'Tidal: Favorite Tracks Now Show Up As A Playlist (Same As Spotify Liked Songs)', desc: 'github issue #502 (yug1900): tidal users wanted their favorited tracks ("my collection" in the tidal app) to appear alongside their normal playlists in the sync tab — same treatment spotify gets for "liked songs". prior attempt at this surfaced empty data because the wrong endpoint was being hit (`/v2/favorites?filter[type]=TRACKS` returns nothing for personal favorites — that endpoint is scoped to collections the third-party app created itself, not the user\'s app-level favorites). reporter located the working endpoint: `GET /v2/userCollectionTracks/me/relationships/items?countryCode=US&locale=en-US&include=items`. cursor-paginated (20 per page, follow `links.next` with `page[cursor]=...` until exhausted), responses only carry track-level attributes — artist + album NAMES come back as relationship-link stubs, not embedded data. fix: two-phase fetch. phase one walks the cursor chain to enumerate every track id (cheap, IDs only). phase two batch-hydrates 20 IDs at a time through the existing `_get_tracks_batch` helper which already knows how to `include=artists,albums` and produce fully-populated `Track` objects matching the rest of the codebase — no duplication of the JSON:API artist/album parse, no new dataclass shape. virtual playlist `tidal-favorites` appended to the end of `/api/tidal/playlists` (mirrors spotify\'s liked-songs placement). id intentionally has NO colon — sync-services.js renderer interpolates ids into css selectors via template literals (`#tidal-card-${p.id} .foo`) and a colon would parse as a css pseudo-class operator. `tidal_client.get_playlist("tidal-favorites")` recognizes the virtual id and dispatches to the collection path internally, so every existing per-id consumer gets it for free: per-playlist detail endpoint, mirror auto-refresh automation, "build spotify discovery from tidal playlist" flow. needs token reconnect to grant the new `collection.read` oauth scope (added to the auth flow). existing tokens hit a 401 — the client now sets a `_collection_needs_reconnect` flag and the listing endpoint surfaces a placeholder card titled "Favorite Tracks (reconnect Tidal to enable)" with a description pointing at settings, so the user has something visible to act on instead of a silently missing row. 22 new tests pin the cursor walk (full chain, max-ids cap mid-page + at page boundary), auth gates (no token / 401 / 403 all bail clean), reconnect-flag lifecycle (set on 401/403, cleared on next successful walk, NOT set on 5xx so transient server errors don\'t falsely tell the user to reconnect), forward-compat type filter (non-track entries skipped), count helper, batch hydration delegation + chunking at the 20-per-batch cap, partial-batch failure containment, and the virtual-id dispatch (real playlist ids still flow through the normal path).', page: 'sync' },
        { title: 'Library Reorganize: Stop Leaving Orphan Audio Files Behind + Hint For Unknown-Artist Rows', desc: 'discord report (foxxify): library reorganize wasn\'t organizing everything. two distinct gaps. (A) lossy-copy users have `track.flac` AND `track.opus` side-by-side at the source; the db only knows about ONE of those (whichever is the canonical library entry). reorganize moved the canonical, left the other format orphaned at the old location, and the empty-folder cleanup never fired because the source dir still had audio in it. fix: at the per-track finalize step the reorganize code now scans the source dir for sibling-stem audio files (same filename stem, audio extension, different format), moves them to the same destination dir as the canonical with the renamed stem + their original extension, then proceeds with the existing source removal + cleanup. preserves both formats post-move so users keep their flac archive AND their opus library copy. (B) old "Unknown Artist / album_id / 0 tracks" rows left over from the pre-#524 manual-import bug couldn\'t be relocated because the album row has no usable metadata source id — reorganize emitted a generic "run enrichment first" message that doesn\'t apply (enrichment can\'t fix these rows; they need their real metadata recovered from file tags). these are the existing `Fix Unknown Artists` repair job\'s domain — reads file tags, re-resolves artist/album/track via configured metadata source, re-tags + moves. reorganize now detects the bad-metadata shape (Unknown Artist OR album.title that\'s a 6+ digit numeric id) and emits a clear "run the Fix Unknown Artists repair job to recover real artist/album from file tags first" hint instead, pointing the user at the right tool. fixer was already implemented and handles the case end-to-end — discoverability gap, not a logic gap. 31 new tests pin: orphan-format detection (canonical-vs-sibling, multi-format, defensive on missing source dir, sidecar exclusion), sibling-move with renamed-stem propagation + dst-dir creation + idempotent re-runs + os-failure handling, and the unknown-artist-hint detection helpers (placeholder names, numeric-id title detection at 6+ digit cutoff, real-album-with-no-source-id keeps the generic enrichment hint, strict-source mode preserved when artist/title look fine).', page: 'library' },
        { title: 'AcoustID Scanner: Compilation Albums No Longer Flag Every Track', desc: 'discord report (skowl): downloaded a compilation album like "high tea music: vol 1" where every track has a different artist (eclypse, andromedik, t & sugah, gourski, himmes, sektor, lexurus, etc.) and the acoustid scanner flagged every single track as wrong song — the file tag had the correct per-track artist (e.g. "eclypse" for "city lights") but the scanner compared against the album-level artist ("andromedik", the curator). raw similarity 12% → wrong song flag. the multi-value-credit fix from the prior pr (foxxify) didn\'t help because both sides were single-value but DIFFERENT artists. cause: scanner sql joined `artists` table via `tracks.artist_id` which points at the ALBUM artist, not the per-track artist. but `tracks.track_artist` column was already populated with the correct per-track value by every server scan + auto-import path that handles compilations. scanner just wasn\'t reading it. fix: changed the scanner select to `COALESCE(NULLIF(t.track_artist, \'\'), ar.name)` — prefers per-track artist when populated, falls back to album artist for legacy rows / single-artist albums where track_artist is null. NULLIF handles the empty-string-instead-of-null case for legacy data. composes with foxxify\'s multi-value fix — for the rare compilation track where acoustid ALSO returns a multi-value credit, both paths work together. 2 new tests pin: compilation track uses per-track artist (reporter\'s exact case), null/empty track_artist falls back to album artist via coalesce.', page: 'library' },
        { title: 'AcoustID Scanner: Multi-Artist Songs No Longer Flagged As Wrong', desc: 'discord report (foxxify): the acoustid scanner repair job was flagging multi-artist tracks as "wrong song" because acoustid returns the full credit ("okayracer, aldrch & poptropicaslutz!") while the library db carries only the primary artist ("okayracer"). raw similarity scored ~43% — well below the 60% threshold — so the scanner created a wrong-song finding even though the audio was correct. user couldn\'t fix without lowering the global artist threshold to ~30% (which would let real mismatches through). cause: scanner used raw `SequenceMatcher` comparison that doesn\'t recognise the primary artist is just one of several contributors in the credit string. fix: extended the shared `core/matching/artist_aliases.py::artist_names_match` helper (lifted in #441) with credit-token splitting on common separators (comma, ampersand, semicolon, slash, plus, "feat.", "ft.", "featuring", "with", "vs.", "x"). when actual artist contains separators, helper splits into individual contributors and checks each against expected — primary-in-credit cases now resolve at 100% instead of 43%. composes with existing alias path so cross-script multi-artist credits ("hiroyuki sawano" expected, "澤野弘之, featured" actual) work too. wired into `core/repair_jobs/acoustid_scanner.py` — replaces the raw similarity call. acoustid post-download verifier already used the helper from #441 so it inherits the same fix automatically. 14 new tests pin: split-by-separator across 12 credit-string formats, primary at start/middle/end of credit, no-mask on genuine mismatches, single-token actual falls through to direct compare, multi-value composes with aliases, threshold still respected, end-to-end scanner integration with reporter\'s exact case (okayracer in okayracer-aldrch-poptropicaslutz credit → no finding), end-to-end scanner still flags genuine mismatches.', page: 'library' },
        { title: 'Deezer Cover Art: Embedded Covers No Longer Look Blurry', desc: 'discord report (tim): downloaded cover art via deezer metadata source came out visibly blurry in navidrome and on phones — particularly noticeable on large displays. cause: deezer\'s api returns `cover_xl` urls at 1000×1000 but the underlying cdn serves up to 1900×1900 by rewriting the size segment in the url path. soulsync wasn\'t doing the rewrite — same as iTunes mzstatic and spotify scdn already get upgraded. now `_upgrade_deezer_cover_url` (mirrors `_upgrade_spotify_image_url` pattern) rewrites the cdn url to request 1900×1900 before download. cdn serves source-native size when source < target so asking for 1900 on smaller-source albums returns the same bytes (no upscaling, no failure). applied at both download sites — auto post-process flow + the enhanced library view\'s "write tags to file" feature. existing `prefer_caa_art` toggle in settings → library → post-processing remains as the orthogonal workaround for users who want even higher quality (musicbrainz cover art archive, often 3000×3000+). 16 new tests pin: standard upgrade, alternate dzcdn host, artist picture urls, custom target sizes, idempotency on already-upgraded urls, defensive on non-deezer urls (spotify/itunes/caa/lastfm/random), empty/none handling.', page: 'settings' },
        { title: 'Cross-Script Artist Names No Longer Quarantine Files (Hiroyuki Sawano / 澤野弘之, Сергей Лазарев / Sergey Lazarev)', desc: 'github issue #442 (afonsog6): files where the artist tag was in one script and the expected metadata was in another — japanese kanji `澤野弘之` for `hiroyuki sawano`, cyrillic `сергей лазарев` for `sergey lazarev`, etc. — got quarantined post-download because acoustid verification scored the artist similarity at 0% (the two scripts share no characters). reporter could not even rescue the file via manual import — the import-modal goes through the same verifier and re-quarantined the same file. cause: verifier compared expected vs actual artist with raw `_similarity` and never consulted musicbrainz aliases, even though MB exposes them on every artist record. fix: new `core/matching/artist_aliases.py` pure helper with alias-aware comparison + new `artists.aliases` JSON column populated by the existing MB enrichment worker on every artist match (one extra `inc=aliases` request per artist) + new multi-tier resolver `MusicBrainzService.lookup_artist_aliases` (library DB → cache → live MB) so the verifier finds aliases even for un-enriched artists without thrashing the MB API. verifier resolves aliases ONCE per `verify_audio_file` call and feeds them through three artist comparison sites (best-match scoring, secondary scan when title matches but artist doesn\'t, final fallback scan). reporter\'s exact two cases reproduced as regression tests with stubbed MB service. backward compat: aliases unavailable / MB unreachable → verifier falls back to direct similarity (identical to pre-fix behaviour — never quarantines stricter than today). 70 new tests pin every layer: pure helper (28), service methods (31), verifier integration (11). audited adjacent artist-comparison sites (auto-import single-track id, discovery scoring, matching engine) — left untouched per scope discipline since they aren\'t the user-reported pain.', page: 'downloads' },
        { title: 'Plex: Library Scan Trigger No Longer Fails On Non-English Section Names', desc: 'github issue #535 (adrigzr): plex servers with the music library named anything other than "music" — Música, Musique, Musik, Musica, etc. — got a `Failed to trigger library scan for "Music": Invalid library section: Music` error after every import cycle, and `wishlist.processing` kept reporting "missing from media server after sync" for tracks that DID import correctly because the post-import scan never fired. cause: `trigger_library_scan` and `is_library_scanning` ignored the auto-detected `self.music_library` (correctly populated by `_find_music_library` filtering by `section.type == "artist"`) and called `self.server.library.section(library_name)` with a hardcoded "music" default — raised NotFound on any non-english server. read methods like `get_artists` already routed through `_get_music_sections` so they didn\'t have the bug; this aligns the scan-trigger path with the same resolution. fix: both single-library branches prefer `self.music_library` first, fall back to literal section lookup only when auto-detection hasn\'t run. activity-feed match in `is_library_scanning` also corrected to use the resolved section\'s actual title instead of the unused `library_name` arg — the prior log line read "triggered scan for music" even on Spanish servers. 13 new tests pin: trigger uses auto-detected section across 6 locale variants (Música / Musique / Musik / Musica / 音乐 / موسيقى), backward-compat fallback when music_library is None, explicit library_name kwarg ignored when auto-detected section exists, log line surfaces correct section title, scan-status check uses auto-detected section\'s `refreshing` attr, activity-feed match filters by resolved title (not library_name).', page: 'settings' },
        { title: 'Search For Match: No More Karaoke / Cover / "Originally Performed By" Junk At The Top', desc: 'github issue #534 (radoslav-orlov): typing "dirty white boy" + "foreigner" into the import-modal "search for match" dialog returned karaoke versions, "originally performed by" compilations, and tribute-band cuts ranked above the actual foreigner studio recording in some regions. user had to scroll past 5+ junk results before finding the canonical track. fix: new `core/metadata/relevance.py` helper reranks results locally with cover/karaoke/tribute/re-recorded penalties (multiplier 0.05× — effectively buries) + exact-artist-match boost (1.5×) + variant-tag (live/acoustic/remix/remaster) penalty (0.4×, skipped when user explicitly typed the variant — searching "track (live)" still ranks live versions correctly). applied at the deezer + itunes + spotify search-tracks endpoints so all three sources behave consistently. validated against live deezer api with the actual #534 query: real foreigner head games cut now lands at #1, live versions follow, karaoke / cover / tribute variants drop to positions 11-15. deezer client also gained optional field-scoped query kwargs (`track="X" artist="Y"`) that build deezer\'s advanced search syntax `track:"X" artist:"Y"` for future opt-in callers (e.g. exact-match flows where api-level filtering is more important than ranking) — kept in client but NOT used at the import-modal endpoint after live testing showed the advanced syntax has its own ranking bias (surfaced "(2008 remaster)" instead of the canonical recording). free-text + local rerank is the more reliable combination here. 75 new tests pin every scoring component, pattern detection (13 cover patterns, 11 variant patterns, 3 fields), score composition (real-cut > karaoke > remaster > re-recorded), the issue #534 screenshot reproduced as a regression test, deezer client query construction + free-text fallback safety net.', page: 'import' },
        { title: 'Auto-Import: Album Duration Is Album Total + Re-Imports Fill Metadata Gaps', desc: 'two more parity gaps closed in the soulsync standalone library write path. (1) album row\'s `duration` column was being written with the FIRST imported track\'s duration instead of the album total — pre-existing bug that survived the prior parity commit. soulsync_client deep scan computes `sum(t.duration for t in self._tracks)` for each album; auto-import now mirrors that by computing the sum across every matched track in the worker and threading it through context to the album INSERT. (2) `record_soulsync_library_entry` was insert-only on artists + albums — once a row existed (matched by id OR name fallback), subsequent imports of the same artist or album skipped completely. meant: artist genres / thumb / source-id reflected ONLY whatever the FIRST imported album supplied, never refreshing as more albums by that artist landed (ten more deezer/spotify imports later, artist row still had whatever the first random import wrote). new conservative UPDATE path: when an existing row matches, fill ONLY the columns whose current value is NULL or empty — never overwrites populated values. protects manual edits + enrichment-worker writes the same way scanner UPDATEs preserve enrichment columns. f-string column names are validated against an allowlist (`_SOULSYNC_FILLABLE_COLUMNS`) before interpolation — defensive against accidental misuse adding columns without an allowlist update. 4 new tests pin: album duration uses sum not single-track, re-import fills empty thumb + genres on existing artist row, re-import does NOT clobber populated values, re-import fills empty source-id columns when later import has them.', page: 'import' },
        { title: 'Auto-Import: Genre Tags Land On The Artists Row + ISRC/MBID Type Hardening', desc: 'small followup to the standalone-library parity commit. (1) auto-import now reads the GENRE tag from each matched audio file (mutagen easy mode, supports flac / mp3 / m4a) and aggregates the deduped set across the album onto the new artists row\'s genres column. matches what soulsync_client._scan_transfer would have written if you\'d done a fresh deep scan after the import — your imported artists no longer feel hollow compared to plex / jellyfin / navidrome scans. dedup is case-insensitive but preserves original casing + insertion order so the json column reads naturally ("Hip-Hop, Rap, Trap" not "hip-hop, rap, trap"). (2) defensive `str()` cast on the worker\'s isrc + mbid extraction. metadata source clients all coerce to string today via `_build_album_track_entry`, but if a future source ever returned int / None for either id the side-effects layer would crash on `.strip()`. cheap insurance. 3 new tests pin: genre aggregation produces deduped insertion-order list, empty when no GENRE tags, isrc/mbid hostile-type input (int, None) coerced to safe string before propagation.', page: 'import' },
        { title: 'Auto-Import: SoulSync Standalone Library Now Gets Full Server-Quality Rows', desc: 'soulsync standalone is meant to be a full replacement for plex / jellyfin / navidrome — the imported tracks should land in the db with the same field richness a media server scan would write. they weren\'t. the auto-import context dict (the payload it handed to the post-process pipeline) had no `source` field anywhere, so `record_soulsync_library_entry` couldn\'t pick the right source-id column on the new tracks/albums/artists rows. result: every auto-imported track landed with NULL on `spotify_track_id` / `deezer_id` / `itunes_track_id` / etc. — watchlist scans (which match by stable source IDs) couldn\'t recognise these tracks as already in library and would re-download them on the next pass. fixed by threading `identification[\'source\']` onto the top-level context, plus per-recording IDs (`isrc`, `musicbrainz_recording_id`) onto track_info so picard-tagged libraries land their per-recording metadata directly. also extracted the artist source ID from the metadata source\'s search response (`_search_metadata_source` and `_search_single_track` now pull `best_result.artists[0][\'id\']`) and threaded it through identification → context → standalone library write, so the artists row finally gets its source-ID column populated instead of staying NULL forever. also added `_download_username=\'auto_import\'` so library history shows "Auto-Import" instead of mislabeling every staging import as "Soulseek" (the fallback default), and an "auto_import" → "Auto-Import" mapping in the source-map dicts at side_effects.py to honour it. record_soulsync_library_entry tracks INSERT now also writes `musicbrainz_recording_id` + `isrc` columns directly (matches the navidrome scanner write path). 17 new tests pin: auto-import context carries source for every metadata source (spotify/deezer/itunes/discogs), `_download_username=auto_import`, isrc + mbid pass-through to track_info, album-id back-reference on track_info, artist source-id flows from identification → context (and not from album_id, the prior copy-paste bug), `_search_metadata_source` extracts artist_id from search response, soulsync library writes mbid + isrc to dedicated columns, deezer source maps to deezer_id column, library history + provenance use Auto-Import / auto_import labels.', page: 'import' },
        { title: 'Auto-Import: Process Multiple Albums At Once', desc: 'auto-import used to process one album at a time. drop 5 albums into staging → wait for the first to fully finish (identify + match + every track post-processed) before the second one even starts. on a slow network or with a big batch this means 30+ minutes of staring at "Processing AlbumOne" while the others sit untouched. now there\'s a small bounded thread pool (3 workers by default, configurable) — up to 3 albums process in parallel, the queue moves through the rest as workers free up. clicking "Scan Now" multiple times no longer spawns extra unbounded scan threads — every trigger (timer + manual button) routes through one shared scan lock so duplicate triggers no-op instead of stacking up. live progress widget on the auto-import card now lists EACH in-flight album with its own track index/total/name instead of one shared scalar that the parallel workers used to stomp on each other. graceful shutdown: stopping the worker waits for in-flight pool work to finish before reporting stopped — no half-moved files or partial DB writes mid-album. stats counters (`scanned` / `auto_processed` / `pending_review` / `failed`) now use a lock so parallel workers don\'t lose increments under load. 17 new tests pin: pool size config, scan lock dedup, executor dispatch + bounded parallelism, cross-trigger candidate dedup, graceful shutdown, per-candidate UI state isolation across parallel workers, stats counter thread-safety, and snapshot consistency.', page: 'import' },
        { title: 'Manual Search In The Failed-Track Candidates Modal', desc: 'when a download fails or returns "not found" the user can already click the status cell to open a modal showing whatever search candidates the auto-search left over and pick a different one. that modal now ALSO has a manual search bar. type any query, hit search, get a fresh round of results from the download sources without having to start the whole download flow over from the search page. solves the case where the auto-query was bad (featured artist not in title, parentheticals like "(remastered 2019)" tripping the matcher, slight artist-name variants) but the file genuinely exists on the source. source picker is smart per download mode: single-source mode (soulseek-only / youtube-only / etc) shows a "searching X" label, no dropdown; hybrid mode shows a dropdown with "all sources" default plus every configured source — picking "all" runs parallel searches across all of them and tags each result row with its source badge. only configured sources show up; unconfigured ones are hidden. results stream in as each source completes via NDJSON instead of blocking on the slowest source — the table starts populating the moment the first source returns. clicking a result reuses the existing retry-download flow → same path, same acoustid verification on the file when it lands, no shortcut around the safety net. additive in the truest sense: the existing modal layout / candidates table / download buttons are byte-identical when the user doesn\'t use manual search. backend extends the candidates endpoint with `download_mode` + `available_sources` + a `source` field per candidate (purely additive — old fields untouched), and adds a new `POST /api/downloads/task/<id>/manual-search` that streams NDJSON (one header line, one source_results line per source as completed, one done terminator) so the frontend renderer can append rows incrementally. 11 tests pin the streaming contract: query length / source whitelist / task 404 validation, single-source dispatch, parallel "all" dispatch, one-event-per-source streaming shape, unconfigured-source skip + reject, header metadata, and per-source exception isolation (one source raising emits a `source_error` event but doesn\'t fail the stream).', page: 'downloads' },
        { title: 'Manual Picks Don\'t Auto-Retry Anymore (And The Modal Always Opens)', desc: 'three follow-on fixes to the manual-search feature once people started actually using it. (1) when the user picked a candidate and that download failed (e.g. soundcloud 404 on a stale track url), the auto-retry monitor would treat it like any other failed auto-attempt — yank the task back to "searching" and pick a different candidate. felt completely wrong from the user\'s perspective: "i picked THIS one, why is it searching for something else?" now manual picks are tagged with a `_user_manual_pick` flag and the auto-retry path bails on it. failure surfaces to the user instead of getting silently fallen-back. (2) non-soulseek manual picks (youtube / tidal / qobuz / hifi / deezer / soundcloud / lidarr) were getting stuck at "downloading 0%" forever even after their engine reported terminal failure. cause: status polling went into a "let monitor handle retry" branch that never fired because manual picks bail on retry — task was orphaned in downloading state. fix: when the engine reports Errored on a manual pick, mark the task failed directly, don\'t defer to the monitor. plus an engine-state fallback path covers the rare race where the orchestrator\'s pre-populated transfer lookup is missing the entry. (3) failed / not_found rows were only clickable when the auto-search had cached candidates — but the whole point of opening the modal now is to RUN a manual search, which doesn\'t need pre-existing candidates. now every failed / not_found / cancelled row opens the modal regardless. (4) one nasty deadlock fix in the process: the new "mark failed" path was synchronously calling `on_download_completed` while holding `tasks_lock`, which itself re-acquires the same lock — `threading.Lock` is non-reentrant so the polling thread wedged forever. while wedged the lock was held → every other endpoint that needed it (including /candidates → can\'t open OTHER modals) hung waiting. moved completion callbacks onto a daemon thread so the lock releases first. (5) manual download worker now runs on its own dedicated thread instead of competing with the batch\'s 3-worker `missing_download_executor` pool — saturated batches no longer queue manual picks indefinitely. all changes are scoped to manual picks only via the `_user_manual_pick` flag — auto-attempt flow is byte-identical to before. 17 unit tests pin the gate behavior (status engine fallback / monitor retry skip / IF-branch failure transition / auto-attempt skip).', page: 'downloads' },
        { title: 'Manual Import: Stop Writing "Unknown Artist / album_id / 0 tracks" Garbage', desc: 'github issue #524 (radoslav-orlov): clicking an album in the import page → all imported albums landed in the library as "Unknown Artist" with the raw 10-digit album id as the title and 0 tracks. cause: the click handler `importPageSelectAlbum(albumId)` was passing only the id to the `/api/import/album/match` POST. the search response carried `source` (which metadata source the album_id came from) + `album_name` + `album_artist`, but the click discarded everything except the id. backend `get_artist_album_tracks` then guessed the source via the configured primary-source priority chain — for a non-deezer-primary user clicking a deezer search result, the chain tries spotify/itunes/discogs first against a deezer numeric id, all return None, and the lookup falls through to the failure-fallback dict (`name = album_id`, no artist field, `total_tracks = 0`). that broken metadata then flowed through the import pipeline → soulsync standalone library got the garbage rows. fix: cache album lookup by id when the suggestions / search renderers run, then have `importPageSelectAlbum` pull `source` + `name` + `artist` from the cache and include them in the match POST. backend now also logs a clear warning when source is missing from the match request, so any future caller dropping it shows up in app.log instead of silently corrupting library imports.', page: 'import' },
        { title: 'Auto-Import: Multi-Disc Albums No Longer Lose Half The Tracks', desc: 'caught while testing #524 with kendrick lamar mr morale & the big steppers (3 discs). dropped discs 1+2 loose in staging root + disc 3 in its own folder, all perfectly tagged → only 9 tracks ended up imported, the rest got integrity-rejected and quarantined. two related bugs in `auto_import_worker._match_tracks`: (1) the "quality dedup" loop kept `seen_track_nums[track_number] = file` and dropped any later file with the same number as a quality duplicate. on a multi-disc release where every disc has tracks 1..N, that collapses the album to one disc\'s worth of files BEFORE the matcher even runs. fix: dedup keys on `(disc_number, track_number)` tuples instead. (2) the 30% track-number bonus in the match scoring fired whenever `ft[track_number] == track_num` regardless of disc — file tagged (disc=2, track=6, "Auntie Diaries") got the full bonus matching API track (disc=1, track=6, "Rich Interlude"), wrong file → integrity check correctly rejected and quarantined. fix: 30% bonus only when BOTH disc and track numbers agree, with a small consolation bonus for cross-disc collisions so title similarity has to carry the match. 4 new tests pin: dedup preserves all files across discs (18-file regression case), match scoring pairs to correct disc, single-disc albums still match normally, and quality dedup within a single (disc, track) position still picks the higher quality file.', page: 'import' },
        { title: 'Auto-Import: Picard / Beets Tagged Libraries Now Get Perfect Matches', desc: 'follow-on to the multi-disc fix. brought the auto-import matcher up to picard / beets / roon parity — files with per-recording identifiers (musicbrainz id or isrc) now match via exact-id lookup before any fuzzy scoring runs. picard-tagged libraries land every track on the first pass with full confidence, no fuzzy guessing. three layered phases now: (1) MBID exact match — file has `musicbrainz_trackid` tag, source returns the same id → instant pair, full confidence. picard\'s primary identifier. (2) ISRC exact match — file has `isrc` tag, source returns the same id → same fast-path, slightly lower priority than mbid (isrc can be shared across remasters of the same recording). (3) duration sanity gate — files in the fuzzy phase whose audio length differs from the candidate track\'s duration by more than 3s are rejected before scoring runs, regardless of how good the title agreement looks. defends against cross-disc / cross-release / wrong-edit mismatches the post-download integrity check used to catch only AFTER the file had already been moved + tagged + db-inserted. metadata-source layer (`_build_album_track_entry`) also extended to propagate isrc + mbid from raw track responses (spotify uses `external_ids.isrc`, itunes uses top-level `isrc`) — without this, fast paths would never trigger in production even though unit tests pass. 18 new tests pin: mbid + isrc exact matches with normalization (dashes / spacing / case), mbid > isrc priority, fast-path bypassing fuzzy scoring entirely, duration gate rejecting wrong-disc collisions, deezer-seconds-vs-spotify-ms duration unit conversion, full picard-tagged 10-track album matching via mbid only.', page: 'import' },
        // --- May 8, 2026 — patch release ---
        { date: 'May 8, 2026 — 2.4.3 release' },
        { title: 'Discover: Sharper Track Selection (Diversity, Source-Aware Popularity, Library Dedup, SQL Genre Filter)', desc: 'four selection-quality fixes on the soulsync-made discover playlists. (1) hidden gems and discovery shuffle had no diversity caps — they could return 50 tracks from the same artist or 20 from one album. now both apply the existing `_apply_diversity_filter` (over-fetch 3x then enforce per-album/per-artist caps; shuffle uses tighter caps because it should feel maximally varied). (2) `popularity` thresholds were spotify-shaped (0-100 scale, popular >= 60 / hidden < 40), but deezer writes its rank value into that column (often six-digit integers) and itunes writes nothing meaningful. for deezer-primary users this meant popular picks pulled essentially everything and hidden gems pulled nothing. new `_get_popularity_thresholds(source)` returns per-source values: spotify (60, 40), deezer (500_000, 100_000) ballpark, itunes/other (None, None) which skips the popularity filter entirely and falls back to random + diversity. (3) `get_genre_playlist` used to load up to 1M discovery_pool rows into python and run a substring keyword filter on the json column. now the keyword OR chain pushes down into sql via `(artist_genres LIKE ? OR ...)` placeholders, fetch_limit drops to limit*10. parent-genre expansion via GENRE_MAPPING preserved. (4) discovery selectors now exclude tracks the user already owns — `_select_discovery_tracks` gained `exclude_owned: bool = True` (default on) which adds a `NOT EXISTS (SELECT FROM tracks WHERE source_id matches)` correlated subquery covering the spotify/itunes/deezer-id columns (with the deezer-column-name asymmetry handled inline: discovery_pool.deezer_track_id vs tracks.deezer_id). hidden gems / shuffle / popular picks / decade / genre browser all benefit automatically. 12 new tests (27 total in the file): diversity caps, source-aware threshold values, threshold-skip behavior, sql-pushed genre filter, parent-genre expansion, owned-track exclusion, opt-out flag, and the deezer-column-name asymmetry trap. 2232/2232 full suite green.', page: 'discover' },
        { title: 'Discover: Stop Showing Undownloadable Tracks (+Lift +Cleanup)', desc: 'audit found multiple discover-page sections (hidden gems / discovery shuffle / popular picks / decade browser / genre browser) had no `WHERE (spotify_track_id IS NOT NULL OR itunes_track_id IS NOT NULL OR ...)` gate on their selection sql. tracks with no source ids in the discovery pool were getting displayed, the user would click download, and the download would silently fail because there was nothing to look up. fix: lifted all five discovery_pool selection methods into shared private helpers (`_select_discovery_tracks`, `_apply_diversity_filter`, `_compute_adaptive_diversity_limits`) on `PersonalizedPlaylistsService`. mandatory id-validity gate is hard-coded into the selector — no opt-out flag, every public method inherits it for free. behavior preserved: same diversity tiers, same over-fetch multipliers, same popularity thresholds, same blacklist filter. ~314 lines of repeated select/diversity boilerplate collapsed across the 5 methods (-55% on those methods\' business logic). also deleted four sections that had been stubbed returning [] for ages (recently added / top tracks / forgotten favorites / familiar favorites) — frontend, backend endpoints, html blocks, helper docs, all gone. on the frontend, lifted the duplicated decade-browser + genre-browser tab management (~314 lines of identical fetch-tabs / render-tabstrip / fetch-content / render-tracklist / wire-sync-button code) into one shared `createTabbedBrowserSection(config)` helper. each browser is now a thin wrapper: ~3 lines per public function. 14 new tests pin the gate (every selector filters null-id rows), the diversity caps, the adaptive limit tiers, the source filter, and the blacklist filter.', page: 'discover' },
        { title: 'Internal: Discover Controller — Cin Pre-Review Polish', desc: 'tightened the controller before opening the PR. (1) dropped the magic `extractItems` defaults — controller used to auto-pull `data.items` / `data.albums` / `data.artists` / `data.tracks` / `data.results` if no extractor was provided. removed the fallback chain. each section now MUST supply its own `extractItems(data) => array` callback. cin standard: explicit > implicit; the auto-fallback could silently grab the wrong key on endpoints that return multiple arrays. validated at register-time so misuse fails immediately. all 10 existing call sites already had explicit extractors so no migration churn. (2) replaced the `renderItems` returning null convention (used by Your Albums + manualDom-style sections) with an explicit `manualDom: true` config flag. clearer intent at the call site, less likely to be confused with a renderer error. (3) added a minimal node `--test` JS test file at `tests/static/test_discover_section_controller.mjs` — 32 tests pin the lifecycle contract: config validation (every required field), happy-path fetch+render, empty/stale/error states, no-fetch `data:` mode, manualDom mode, callable `fetchUrl`, load coalescing, refresh bypass, hook error containment, error toasts. runs via `node --test tests/static/` directly, OR via the regular pytest sweep (`tests/test_discover_section_controller_js.py` shells out to node and asserts a clean exit). skipped gracefully when node isn\'t available or is &lt; 22. closes the "controller is a contract, pin it at the test boundary" gap that cin would have flagged. 2205/2205 full suite green (was 2204 + 1 new pytest wrapper); 32/32 node --test pass; ruff clean; js parses clean.', page: 'discover' },
        { title: 'Internal: Discover Cleanup Round — Toast Errors, Stale State, Skipped Sections', desc: 'follow-up to the controller migration. extended `createDiscoverSectionController` with the hooks the per-section migrations surfaced as needed: callable `fetchUrl` (resolves the seasonal-playlist recreate-on-key-change hack), no-fetch `data:` mode (lets render-only sections like seasonal albums use the controller without inventing a fake endpoint), `beforeLoad` hook (lets dynamically-inserted sections like because-you-listen-to ensure their container exists before the spinner shows), `onSuccess(data)` hook (cleaner home for sibling header / subtitle / button updates than folding them into renderItems), and an `isStale` / `onStale` / `renderStale` triple for the third render state (data is empty BUT upstream is still discovering — show updating UI + start a poller, instead of the bare empty-state copy). turned on `showErrorToast: true` for every migrated section — section load failures now surface a global toast instead of silently spinning forever or swallowing into console.debug. that\'s the JohnBaumb #369 pattern applied at the UI layer. migrated the two sections that didn\'t fit the original controller contract: `loadYourAlbums` (uses isStale/onStale for stale-fetch UI + onSuccess for subtitle/filters/download-button side-effects + renderItems returning null since it delegates to the existing grid renderer) and `loadSeasonalAlbums` (uses no-fetch data mode since the parent `loadSeasonalContent` already fetched the season payload). also lifted the duplicated decade-tab + genre-tab sync-status block (✓/⏳/✗/percentage) into a `_renderSyncStatusBlock(idPrefix)` helper — two call sites now share one implementation. listenbrainz playlists keep their own block because the semantics differ (matching progress vs download progress). audit found the 13 supposedly-dead hidden sections aren\'t dead at all — they\'re gated on user data (discovery pool, library content, metadata cache) and self-surface when their data exists. removed one orphaned `loadPersonalizedDailyMixes()` call from `blockDiscoveryArtist` — daily mixes is intentionally paused, refreshing it from there was a no-op.', page: 'discover' },
        { title: 'Internal: Migrate 7 More Discover Sections to the Controller', desc: 'follow-up to the foundation commit. migrated fresh tape, the archives, time machine intro carousel, browse by genre intro carousel, seasonal mix, your artists, and because-you-listen-to onto `createDiscoverSectionController`. each one drops its own hand-rolled try/catch + spinner injection + empty-state HTML + error swallow in favor of a config object — controller owns the lifecycle. net 76 lines smaller in discover.js even after adding the per-section render helpers. skipped two sections that don\'t fit the controller\'s single-fetch / single-render-target shape: `loadYourAlbums` (paginated grid + filters, four separate UI elements updated) and `loadSeasonalAlbums` (no fetch — receives pre-fetched data from parent). hidden / dead sections (~13 of them) untouched in this pass — separate audit commit will surface or kill them. controller extension candidates surfaced for follow-up: callable `fetchUrl` (so seasonal playlist doesn\'t need controller-recreate-on-key-change), explicit `isStale` / `onStale` hook (so your-artists doesn\'t fold stale handling into renderItems), `beforeLoad` hook (so because-you-listen-to can let the controller own the dynamic container creation), and a no-fetch `data:` mode (so render-only sections like seasonal albums can use the controller). zero behavior changes — every public load function keeps its name + signature so existing callers, refresh buttons, and dashboard wiring don\'t notice the swap.', page: 'discover' },
        { title: 'Internal: Discover Section Controller Foundation', desc: 'every section on the discover page (recent releases, your artists, your albums, seasonal, fresh tape, the archives, etc) re-implements the same lifecycle by hand: show spinner → fetch endpoint → parse → either render or show empty state or show error → maybe wire post-render handlers → maybe expose refresh. ~30 sections, all subtly drifting — different empty messages, different error handling (some console.debug, some silently swallowed, some leave the spinner spinning forever), different sync-status icons, no consistent error toast. lifted that lifecycle into a shared `createDiscoverSectionController` (renderers stay per-section because section data shapes legitimately differ — album cards vs artist circles vs playlist tiles vs track rows; the controller is the wrapper, not a forced visual abstraction). this commit is the foundation: built the controller + migrated `recent releases` as proof. each remaining section will migrate in its own follow-up commit (keeps reviews small + lets us sequence the work). once everything is on the controller, the discover-page cleanup work (kill 13 dead sections, standardize sync-status icons, add error toasts) becomes single-line registry edits instead of section-by-section rewrites.', page: 'discover' },
    ],
    '2.4.2': [
        // --- May 7, 2026 — patch release ---
        { date: 'May 7, 2026 — 2.4.2 release' },
        { title: 'Artist Top Tracks: Per-Row + Bulk Download', desc: 'github issue #513 (s66jones): wanted a way to grab an artist\'s top X popular songs without pulling the full discography (the zotify workflow). artist detail page already had a "popular on last.fm" sidebar, but it was display-only — play button per row, no download. now when your primary metadata source is spotify or deezer, that sidebar pulls top tracks via the source\'s native popularity endpoint (spotify `artist_top_tracks` returns 10 per market, deezer `/artist/{id}/top` supports up to 100), each row gets a wishlist-add button on hover, and a "download all" footer button opens the existing wishlist modal with all top tracks pre-loaded. files land in their REAL album folders on disk (not a fake "top tracks" folder) because each track carries its actual album metadata. itunes / discogs / musicbrainz primary still falls back to the existing last.fm playcount display (no popularity ranking on those sources). 10 new tests pin the spotify + deezer client method behavior (auth gate, limit clamping, malformed response handling, spotify-compatible shape conversion).', page: 'library' },
        { title: 'Fix: AcoustID Verification Let Instrumentals Pass As Vocal Tracks', desc: 'discord report (corruption [BWC]): downloads coming through as instrumental versions when the user expected the vocal cut. slipped past acoustid verification because the title-similarity normalizer strips parentheticals and version-suffix tags ("(Instrumental)", "- Live", etc) so legit name variations don\'t false-fail the comparison. side effect: "in my feelings" and "in my feelings (instrumental)" both normalize to "in my feelings", title sim is 1.0, file passes verification despite being the wrong cut. fix: detect the version label on each side BEFORE normalization runs — if expected and matched disagree (one is original, the other is instrumental / live / acoustic / remix / etc), reject as version mismatch. reuses `MusicMatchingEngine.detect_version_type` so post-download verification uses the same patterns the pre-download soulseek matcher already applies (no duplicated regex tables). also gates the secondary fallback scan, so a wrong-version variant in the same fingerprint cluster can\'t win the loop after the best match is rejected. 6 new tests pin the four direction cases (instrumental returned for vocal request → fail, vocal returned for instrumental request → fail, live vs acoustic → fail, matching versions on both sides → pass) plus the original-to-original happy path and the secondary-scan gate.', page: 'downloads' },
        { title: 'Fix: Search Picker Defaulted to Spotify on Non-Admin Profiles', desc: 'github issue #515 (jaruca): admin sets primary metadata source to deezer / itunes / discogs, but every non-admin profile saw spotify as the active source on the search page and global search popover, requiring manual click each time. cause: `shared-helpers.js` resolved the active source by fetching `/api/settings` — that endpoint is `@admin_only` because it returns full config including credentials, so non-admin profiles got 403 and silently fell back to the hardcoded `spotify` default. fix: read from `/status` instead, which is public and already returns `metadata_source` for the dashboard. one-line scope change, behavior preserved for admins (same value, different endpoint), non-admins now see the real configured source.', page: 'search' },
        { title: 'Internal: Stop Swallowing Exceptions Silently', desc: 'github issue #369 (johnbaumb): the codebase had ~300 `except Exception: pass` blocks — and another ~30 bare `except: pass` ones — across web_server.py, every metadata client, every download/import worker, the repair jobs, and most service modules. when one of those paths failed at runtime, the failure was completely invisible: no log line, no telemetry, nothing. you\'d see "downloads stopped working after a few hours" or "enrichment never finishes" and there was nothing to grep for in app.log because the exception had been thrown straight into the void. swept all of them. converted to `except Exception as e: logger.debug("<context>: %s", e)` so failures land in the log with enough context to grep. bare `except:` cases (which also swallow KeyboardInterrupt and SystemExit — actively wrong) got upgraded to `except Exception:` first so ctrl-c works correctly. ~14 cleanup-path sites (atexit handlers, finally-block conn.close calls) were intentionally left silent with explicit `# noqa: S110` comments — logging during shutdown can itself crash because file handles get torn down before the handler fires. and added ruff S110 to the lint config so this pattern fails CI going forward — drift fails at PR review instead of at runtime against a wedged worker thread. zero behavior change to any happy path; just made the failure paths inspectable. test suite (2188 tests) green throughout the sweep.' },
        { title: 'Fix: Repair Job Card "X Findings" Badge Was Misleading After Bulk-Fix', desc: 'discord report: duplicate detector card said "372 findings" and cover art filler said "60 findings", but clicking the findings tab pending filter showed 0 — read like a bug ("findings aren\'t being created"). actual cause: job-card badge displayed `last_run.findings_created` (historical "found in last scan") which doesn\'t reflect current state when those findings have since been bulk-fixed and moved to status="resolved". fix: api response now includes `pending_findings_count` per job (current pending count from a single sql aggregation). badge now shows "X pending" when pending count > 0 (urgent red color), or "X found in last scan" with a muted grey color when pending = 0 but the last scan did surface something. user can tell at a glance whether something needs review vs whether it\'s a historical reminder. 3 new tests pin the per-job pending count helper.', page: 'stats' },
        { title: 'Fix: Downloads Stop After 2-3 Hours (slskd HTTP Timeout)', desc: 'github issue #499 (bafoed): big initial sync of spotify playlists worked for 2-3 hours then downloads silently stopped. 3 active tasks stuck in "searching" state, replaced every ~10 min by different ones, but slskd ui showed no actual searches happening. only fix was restarting the soulsync container — which would buy another 2-3 hours. root cause: `core/soulseek_client.py` constructed `aiohttp.ClientSession()` with no timeout at four sites. when slskd hung on a request (overloaded, transient network blip, internal stall), the http call blocked indefinitely — and the worker thread blocked with it. download executor only has `max_workers=3` for download workers. once 3 worker threads were wedged on hung calls, no further downloads could start. batch-level "stuck detection" (10-min) was correctly marking tasks `not_found` and trying to start replacements, but the executor pool was exhausted — replacements queued forever. fix: bounded `aiohttp.ClientTimeout` (total 120s, connect 15s, sock_read 60s) on every slskd `ClientSession` construction. legitimate metadata calls (search submission, status polls, download enqueue) finish in seconds — slskd doesn\'t stream files through these requests, so the timeout can\'t kill a real operation. when timeout fires, the request raises `asyncio.TimeoutError` which is now explicitly caught + logged + returns None to the caller (treats as a normal failure, same code path as a 5xx response). worker thread unblocks → executor pool stays healthy → downloads keep flowing. 3 new tests pin the timeout config + the `asyncio.TimeoutError` handler so future drift fails at the test boundary instead of at runtime against a wedged executor.', page: 'downloads' },
        { title: 'Fix: Library Reorganize Job Misclassified Album Tracks As Singles', desc: 'github issue #500 (bafoed): library reorganize repair job moved tracks like `Surf Curse/Surf Curse - Nothing Yet (2017)/01 - Christine F.flac` to single-template paths like `Surf Curse/Surf Curse - Christine F/Surf Curse - Christine F.flac`. root cause: the job used `is_album = (group_size > 1)` where `group_size` was the count of tracks for the same album currently sitting in the transfer folder being scanned — when only one track of an album was in transfer (rest already moved to library, or album tags varied across tracks like "Buds" vs "Buds (Bonus)"), each track became a 1-element group → all routed through single template. fix: rewrote the job to delegate to the per-album planner (`core.library_reorganize.preview_album_reorganize` / `reorganize_queue`) — the same planner the artist-detail "reorganize" modal uses. db-driven: the planner knows the album has multiple tracks regardless of how many sit in the transfer folder, so the album-vs-single classification is structurally correct. apply mode delegates to the existing reorganize queue → file move + post-processing + db update + sidecar handling all flow through one code path. only iterates albums for the ACTIVE media server (matches the artist-detail modal\'s scope) — multi-server users (plex + jellyfin etc) won\'t accidentally have the job touch the inactive server\'s files. albums missing a metadata source id get a single "needs enrichment first" finding instead of n per-track "no source" findings. dropped ~500 loc of tag-reading + transfer-walk + template logic that was duplicated against the per-album path. files in transfer with no db entry are now exclusively the orphan_file_detector\'s domain (clean separation). 12 tests pin the delegation contract.', page: 'library' },
        { title: 'Fix: Enrich Honors Manual Album Matches', desc: 'github issue #501 (tacobell444): if you manually matched an album to a specific source ID via the match-chip UI, then clicked "enrich" on that album, the worker would search by name and overwrite your manual match with whatever the search returned (or revert status to "not_found" if it found nothing). reorganize then read the now-wrong id and moved files to the wrong destination. fix: extracted a shared `core/enrichment/manual_match_honoring.py` helper. every per-source enrichment worker (spotify / itunes / deezer / tidal / qobuz) now reads its stored id column at the top of `_process_*_individual` — if present, it fetches via `client.get_album(stored_id)` directly and refreshes metadata without touching the id. fuzzy name search only runs as fallback for never-matched entities. discogs / audiodb / musicbrainz already had inline stored-id fast paths and are left alone. lastfm / genius are name-based and don\'t store ids. cin-shape lift: same fix in 5 workers gets exactly one helper, per-worker variability (column name, client method, response shape) plugs in via callbacks. 11 new helper tests pin: stored-id fast-path, no-id fallthrough, fetch-failure fallthrough, table/column whitelist, callback contract.', page: 'library' },
        { title: 'Fix: "no such table: hifi_instances" When Adding HiFi Instance', desc: 'github issue #503 (hadshaw21): adding a hifi instance via downloader settings popped up `no such table: hifi_instances` even though the connection test and "check all instances" both worked. root cause: `_initialize_database` runs every CREATE TABLE + every migration step inside one sqlite transaction. python\'s sqlite3 module doesn\'t autocommit DDL by default, so if any later migration step throws on a user\'s specific DB shape (e.g. an old volume from a prior soulsync version with quirky schema state), the WHOLE batch rolls back — including the hifi_instances CREATE that ran successfully. user\'s next boot retries init, hits the same migration failure, rolls back again. table never lands. fix: defensive lazy-create. every hifi_instances CRUD method now runs `CREATE TABLE IF NOT EXISTS hifi_instances (...)` immediately before its operation. idempotent — costs one PRAGMA-level no-op when the table is already present, fully recovers from a broken init. read methods (`get_hifi_instances`, `get_all_hifi_instances`) now return empty instead of raising when init failed. write methods (`add`, `remove`, `toggle`, `reorder`, `seed`) work end-to-end. doesn\'t paper over the underlying init issue (still worth tracking down which migration breaks for which users) but makes hifi instance management self-healing. 7 new tests pin the lazy-create behavior — every method works against a DB that\'s missing the table.', page: 'settings' },
        { title: 'Plex: "All Libraries (Combined)" Mode', desc: 'github issue #505 (popebruhlxix): users with multiple plex music libraries (e.g. one per plex home user) only saw one library inside soulsync because the connection settings forced you to pick a single library section. now there\'s a new "all libraries (combined)" option in settings → connections → plex → music library dropdown. picking it flips the plex client into a server-wide read mode where every read method (`get_all_artists` / `get_all_album_ids` / `search_tracks` / `get_library_stats` / etc) dispatches through `server.library.search(libtype=...)` instead of querying a single section. one api call, plex handles the aggregation. cross-section dedup applied at the listing layer — same-name artists across sections collapse to a canonical entry (the one with more tracks), so plex home families with overlapping music tastes don\'t see "drake" twice. removal-detection id enumeration stays raw on purpose — deduping there would falsely prune tracks linked to non-canonical ratingKeys. write methods (genre / poster / metadata updates) are unaffected and operate on plex objects via ratingKey directly — write-back targets one section\'s copy of an artist if it exists in multiple, document and revisit if it matters. trigger_library_scan + is_library_scanning fan out across every music section in the new mode. backward compatible — existing users with a real library name saved see no behavior change. the "all libraries" option only appears in the dropdown when more than one music library exists on the server. 29 new tests pin both modes (single-section preserved, all-libraries dispatches through server-wide search, dedup keeps canonical, id enumeration stays raw).', page: 'settings' },
        { title: 'Fix: Download Discography Showed Wrong Artist\'s Albums', desc: 'clicked download discography on 50 cent → modal showed young hot rod\'s albums. clicked weird al → modal showed the beatles. cause: the endpoint received whichever single artist id the frontend happened to pick (spotify or itunes or deezer or library db id) and dispatched it as-is to whichever source it queried. when the picked id didn\'t match the queried source\'s id format, lookup either returned wrong-artist results (numeric collisions — db id 194687 was a real deezer artist for someone else) or fell back to a fuzzy name search that picked a wrong artist. the per-source id dispatch mechanism (`MetadataLookupOptions.artist_source_ids`) already existed and the watchlist scanner already used it; the on-demand discography endpoint just wasn\'t wired to it. fixed: when the url artist_id matches a library row by ANY stored id (db id, spotify_artist_id, itunes_artist_id, deezer_id, musicbrainz_id), backend pulls every stored provider id and dispatches the right id to each source. each source gets its OWN stored id regardless of what the url carries. when the url id is a non-library source-native id and the row lookup misses entirely, behavior is identical to before (single-id fallback). also fixed two log-namespace bugs: enhance quality and multi-source search were writing through `getLogger(__name__)` which resolves to `core.artists.quality` / `core.metadata.multi_source_search` — neither under the soulsync handler — so every diagnostic line was silently dropped. switched both to `get_logger()` from utils.logging_config so they actually land in app.log.', page: 'library' },
        { title: 'Enhance Quality: Direct ID Lookup Like Download Discography', desc: 'two related fixes. (1) discord report: enhance quality on an artist with neither spotify nor deezer connected added tracks as "unknown artist - unknown album - unknown track". enhance was running a single-source itunes fallback chain that returned junk matches with empty fields, while track redownload had been doing parallel multi-source search the whole time. extracted that search into `core/metadata/multi_source_search.py` and pointed both flows at it. (2) followup: enhance was still using fuzzy text search even when the library track had a stored source ID (spotify_track_id / deezer_id / itunes_track_id / soul_id) on the row, which meant tracks with messy tags ("Title (Live)", featured artists in the artist field, etc.) failed to match even though a perfect ID was sitting right there. download discography never had this problem because it resolves albums by stable ID, not by name. enhance now does the same: for every source you have configured, if the track has a stored ID for that source, it calls `get_track_details(id)` directly — no fuzzy matching. preferred source (your configured primary) is tried first so a deezer-primary user gets deezer payloads on the wishlist entry. text search is only the fallback now (kicks in for tracks with no stored IDs). also fixed the modal toast that lied "matching tracks to spotify..." regardless of which sources were actually being queried.', page: 'library' },
        { title: 'Internal: Media Server Engine Cin/JohnBaumb Pass', desc: 'internal — applied the same architectural cleanups the download engine PR went through to the media server engine PR before review. (1) every server client (Plex / Jellyfin / Navidrome / SoulSync) now explicitly inherits `MediaServerClient` instead of relying on structural typing — drift in any class fails at the conformance test boundary. (2) generic accessors on the engine: `configured_clients()` (replaces per-server `if X and X.is_connected(): clients[name] = X` chains in web_server.py) and `reload_config(name=None)` (generic dispatch instead of per-client reload calls). (3) singleton factory: `get_media_server_engine()` / `set_media_server_engine()` matching the metadata + download engine shape. web_server.py boots via `set_media_server_engine(...)` so factory + global handle share state. (4) ~70 direct `plex_client.X` / `jellyfin_client.X` / `navidrome_client.X` / `soulsync_library_client.X` attribute reaches in web_server.py migrated to `media_server_engine.client(\'<name>\').X`. ~60 standalone refs (truthy checks, media_client assignments, source-name tuples) also routed through the engine. (5) the per-server `plex_client` / `jellyfin_client` / `navidrome_client` / `soulsync_library_client` globals in web_server.py are gone entirely — engine owns the client instances now, every caller reaches via `media_server_engine.client(\'<name>\')`. four multi-client consumers (`PlaylistSyncService`, `ListeningStatsWorker`, `WebScanManager`, discovery `SyncDeps`) refactored to take the engine instead of separate per-server kwargs. (6) `TrackInfo` and `PlaylistInfo` lifted out of `core/plex_client.py` / `jellyfin_client.py` / `navidrome_client.py` (each was defining a near-identical copy) into the neutral `core/media_server/types.py` module — same lift Cin caught on the download `TrackResult`/`AlbumResult`/`DownloadStatus` situation. consumers (matching engine, sync service) get one import. zero behavior change.' },
        { title: 'Internal: Media Server Engine Foundation', desc: 'internal — companion to the download engine refactor. introduces a media-server engine + plugin contract on top of the four server clients (plex / jellyfin / navidrome / soulsync standalone). pre-refactor web_server.py held four separate per-server client globals that every dispatch site reached individually. new `core/media_server/` package provides `MediaServerEngine` that owns the per-server clients + a small set of generic accessors (`client(name)`, `active_client()`, `configured_clients()`, `reload_config(name)`) so call sites use one canonical lookup pattern. plugin contract requires the four methods every server actually implements (is_connected, ensure_connection, get_all_artists, get_all_album_ids) — methods that exist on most-but-not-all servers (search_tracks, trigger_library_scan, get_library_stats, etc.) are listed in `KNOWN_PER_SERVER_METHODS` for discoverability and reached directly via `engine.client(name).<method>` since there\'s no uniform safe-default that fits every method. honest scope: the four uniform-shape `is_connected` dispatches were lifted into `engine.is_connected()` (the one cross-server wrapper kept on the engine); the ~18 server-specific chains that do genuinely different per-server work (playlist track replace, metadata sync, scan strategies) stay explicit at the call site per the "lift what\'s truly shared" standard, but reach the per-server client through the engine. 42 tests pin: per-server observable behavior (4 server pinning files, 20 tests), engine surface + accessor contracts (15 tests), structural conformance + explicit-inheritance (9 tests). zero behavior change for users.' },
        { title: 'Drop SoundCloud Preview Snippets at Search Time', desc: 'soundcloud serves a ~30s preview clip for tracks that are gated behind go+ / login (very common for major-label uploads — official content basically doesn\'t exist on soundcloud, so what shows up is bootlegs, fan uploads, type beats, and these 30s previews). yt-dlp accepts the preview as the download payload, the post-download integrity check catches the duration mismatch and quarantines the file, but the user just sees "all candidates failed" with no explanation. previews also showed up in the candidate-review modal where clicking one bypassed validation and downloaded the same broken file. now `filter_soundcloud_previews` drops these candidates at every entry point — auto-search scoring, modal-cache fallback, AND the not-found raw-results path — so previews never reach the matcher OR the user. drops candidates < 35s or below half the expected duration, gated on expected being non-trivially long (>60s) so genuine short tracks still pass. also fixed a silent regression in the hybrid-fallback path where the per-source attribute removal left `getattr(orch, \'youtube\', None)` returning None for every source — fallback never fired. now resolves through the orchestrator\'s `client(name)` accessor.', page: 'downloads' },
        { title: 'Internal: Move Shared Download Dataclasses + Singleton Boot Path', desc: 'internal — two architectural cleanups on top of the download engine refactor. (1) `TrackResult`, `AlbumResult`, `DownloadStatus`, `SearchResult` lived in `core/soulseek_client.py` for historical reasons (they grew up there as the soulseek-only types and got exported when other download sources were added). every plugin imported these from the soulseek module just to satisfy the contract — coupling 8 clients to a sibling source for type imports only. moved them to `core/download_plugins/types.py` (the neutral plugin package) and updated all 14 import sites across deezer/hifi/lidarr/qobuz/soundcloud/tidal/youtube clients + the engine + matching engine + redownload + tests. clean break, no backward-compat re-export. (2) `web_server.py` now boots the orchestrator via `set_download_orchestrator(DownloadOrchestrator())` so the singleton factory + boot path share state — `get_download_orchestrator()` returns the same instance the global handle points at instead of lazily building a separate one. matches cin\'s `get_metadata_engine()` pattern.' },
        { title: 'Internal: Rename `soulseek_client` Global → `download_orchestrator`', desc: 'internal — followup cleanup. the global handle in web_server.py was named `soulseek_client` for historical reasons (the orchestrator was originally just the soulseek client and grew downstream sources around it), but the type has long been `DownloadOrchestrator` not `SoulseekClient`. renamed the global + every parameter/attribute that carried the legacy name across web_server.py, api/, core/downloads/*, core/search/*, core/streaming/*, services/sync_service.py, and the test fixtures (`MasterDeps.soulseek_client` → `download_orchestrator`, `init(soulseek_client_obj)` → `init(download_orchestrator_obj)`, etc). module path `core.soulseek_client` and class `SoulseekClient` (the actual soulseek-only client) are unchanged — only the orchestrator handle renamed. ~250 references touched, suite green.' },
        { title: 'Internal: Drop Backward-Compat Per-Source Attrs', desc: 'internal — followup to cin\'s download engine review. removed the `orchestrator.soulseek` / `.youtube` / `.tidal` / `.qobuz` / `.hifi` / `.deezer_dl` / `.lidarr` / `.soundcloud` attribute aliases that were preserved for backward compat. external callers (core/downloads/, core/search/, web_server.py) all migrated to the generic `orchestrator.client(\'<source>\')` accessor — alias-aware (legacy `deezer_dl` resolves to canonical `deezer`), single source of truth via the registry. the orchestrator\'s own internal `self.soulseek` / `self.deezer_dl` reaches also routed through `client()` so the only place that knows about per-source identity is the registry. test fakes updated to expose `client(name)` instead of stuffing attributes; conformance test pinned to the new accessor contract. zero behavior change — just cleaner shape.' },
        { title: 'Internal: Download Engine Review Followup', desc: 'internal — three correctness fixes on top of the download engine refactor, all flagged in cin\'s pr review. (1) `engine.cancel_download(source_hint=\'deezer_dl\')` was silently routing deezer cancels to soulseek because the legacy alias never made it to the engine\'s plugin map — only the registry knew about it. fix: aliases now flow through `register_plugin` and `get_plugin` / `cancel_download` resolve them to the canonical name. (2) `_resolve_source_chain` filtered hybrid_order against canonical registry names only, so any user with `deezer_dl` in their config quietly dropped deezer from hybrid mode. fix: orchestrator normalizes through `registry.get_spec()` first. (3) the worker\'s terminal write was a read-then-write split — a cancel landing between the snapshot and the update could be overwritten back to errored / completed. fix: new atomic `update_record_unless_state` on the engine holds `state_lock` across the check + write; both `_mark_terminal` AND the success path use it now. also added generic `client(name)` / `configured_clients()` / `reload_instances(name?)` accessors on the orchestrator + a `get/set_download_orchestrator()` singleton matching cin\'s `get_metadata_engine()` shape, and migrated 30 external `soulseek_client.<source>` reaches in web_server.py to `client("<source>")`. 18 new tests pin every fix.' },
        { title: 'Internal: Typed Metadata Foundation', desc: 'internal — first step of a multi-pr migration to give the metadata pipeline a real contract. the codebase historically grew duck-typed extractors (`_extract_lookup_value(album_data, "id", "album_id", "collectionId", "release_id", default=...)`) at every consumer site because each provider returns its own response shape. ~150 of those across the codebase. new `core/metadata/types.py` defines canonical typed `Album` / `Track` / `Artist` dataclasses with strict required fields. per-source classmethod converters (from_spotify_dict, from_itunes_dict, from_deezer_dict, from_discogs_dict, from_musicbrainz_dict, from_hydrabase_dict) are the SINGLE place that knows each provider\'s wire shape. zero behavior changes in this pr — pure additive foundation. follow-up prs migrate consumers one at a time. full migration plan documented at docs/metadata-types-migration.md.', page: 'library' },
        { title: 'Internal: Migrate Album-Info Builders to Typed Path', desc: 'internal — steps 2+3 of the typed metadata migration in one pr. two album-info builders now route through `Album.from_<source>_dict()` when the caller passes a known source: `_build_album_info` (used by every album-tracks lookup) and the embedded album section of `_build_single_import_context_payload` (used by single-track import context resolution). legacy duck-typed extraction stays as the fallback when source is empty/unknown, raw input isn\'t a dict, or the typed converter raises — so a converter bug can\'t break album resolution or import context. caller-provided album_id / album_name / artist_name fallbacks apply on the typed path the same way they did on legacy. zero behavior change for existing callers since they don\'t pass a source yet — opt-in only. 22 new tests pin the typed path, the legacy fallback, and parametrized coverage across registered providers.' },
        { title: 'Internal: Migrate Discography + Quality Scanner to Typed Path', desc: 'internal — next round of the typed metadata migration. three more album-shape consumers now route through `Album.from_<source>_dict()` when the caller passes a known source: `_build_discography_release_dict` (artist discography release cards), `_build_artist_detail_release_card` (artist detail page release cards), and `_normalize_track_album` (quality scanner result normalization). legacy duck-typed extraction stays as the fallback when source is empty/unknown, raw input isn\'t a dict, or the typed converter raises — same safety contract as the prior migration steps. 20 new tests pin the typed path + legacy fallback + parametrized coverage across registered providers.' },
        { title: 'Fix: Maintenance Findings Badge Showed Inflated Count With Empty Findings Tab', desc: 'discord report (husoyo): duplicate detector and cover art filler badges showed "364 findings" / "31 findings" after a scan, but clicking into the findings tab showed nothing. cause: `_create_finding` silently dedup-skipped re-discovered issues (when an equivalent row already existed with status pending/resolved/dismissed) but the caller incremented `result.findings_created` regardless of whether a row was actually inserted. so on a re-scan that found the same problems as a prior scan, the badge snapshot recorded 364 even though zero NEW pending rows hit the db. fix: `_create_finding` now returns a bool (True on insert, False on dedup-skip / db error). all 16 repair jobs updated to only increment `findings_created` on True. new `findings_skipped_dedup` counter added to job results and surfaced in the scan log: "Done: 2791 scanned, 0 fixed, 0 findings (363 already existed), 0 errors" — so re-scans show a real count, and you can see at a glance how many findings were carried over from prior scans. also fixed a missing `job_id` kwarg in the album tag consistency job that was silently breaking finding creation for that scan. companion ux improvement: findings tab now auto-switches its status filter from "pending" to "all status" when 0 pending rows exist but resolved/dismissed/auto-fixed rows do — with a small notice so you can see what carried over instead of staring at an "all clear" empty state.', page: 'library' },
        { title: 'Internal: Download Source Plugin Contract', desc: 'internal — first step of a multi-step refactor on the multi-source download dispatcher. the orchestrator historically had 8 download sources (soulseek/youtube/tidal/qobuz/hifi/deezer/lidarr/soundcloud) hardcoded into 6+ different dispatch sites — `if username == "youtube" elif username == "tidal" elif ...` chains in `__init__`, search, download, get_all_downloads, cancel_download, etc. adding usenet (planned) would have meant 700+ lines of copy-paste across the same files. new `core/download_plugins/` package defines `DownloadSourcePlugin` (Protocol) — the canonical contract every source must satisfy: `is_configured`, `check_connection`, `search`, `download`, `get_all_downloads`, `get_download_status`, `cancel_download`, `clear_all_completed_downloads`. plus `DownloadPluginRegistry` — single source of truth for which sources exist, with name/alias resolution (legacy `deezer_dl` alias preserved). orchestrator now dispatches through the registry instead of hardcoded `[self.soulseek, self.youtube, ...]` lists. (note: this PR initially preserved `self.<source>` attribute aliases for backward compat; followup commits in the same PR removed them — see the "Drop Backward-Compat Per-Source Attrs" entry above. external callers now reach individual clients via `orchestrator.client('<name>')`.) zero behavior change for end users — pure additive foundation that lets future PRs extract shared logic (background thread workers, search query normalization, post-processing context) into the contract instead of copy-pasted across all 8 sources. 19 new tests pin every plugin class\'s structural conformance to the contract — drift in any source will fail at the test boundary instead of at runtime against a live download.' },
        { title: 'Internal: Download Engine — Background Worker, State, Fallback', desc: 'internal — followup to the download source plugin contract. lifts the duplicated thread-spawn boilerplate, per-source active_downloads dicts, and hybrid-fallback dispatch into a central `core/download_engine/` package. each streaming source (youtube, tidal, qobuz, hifi, deezer, soundcloud) used to hand-roll the same ~70 LOC of background thread management — semaphore-gated serialization, rate-limit sleep between downloads, state-dict updates for InProgress/Completed/Errored transitions, exception capture. ~490 LOC of copy-paste across 7 files. all of it gone now — `engine.worker.dispatch(source, target_id, impl_callable, ...)` owns thread spawning + semaphore + delay + state lifecycle. plugins provide only `_download_sync(download_id, target_id, display_name) → file_path`, the source-specific atomic download. per-source rate-limit policy declared via `RateLimitPolicy` (concurrency, delay) — engine reads at register time. cross-source state queries (`get_all_downloads`, `get_download_status`, `cancel_download`, `clear_all_completed_downloads`) read engine state directly instead of iterating per-source dicts. hybrid-mode search now goes through `engine.search_with_fallback(chain)` — same ordering / skip-unconfigured / swallow-per-source-exceptions semantics as before. every per-source migration commit gated by phase A pinning tests (54 tests across all 8 sources) so contract drift fails fast at the test boundary instead of at runtime against a live download. net: ~700 LOC removed across 6 client files, ~85 new engine + worker + rate-limit tests, suite green at every commit. zero behavior change for end users — same downloads, same lifecycle states, same hybrid mode. (per-source attribute aliases like `orchestrator.soulseek` were initially preserved here for backward compat; followup commits in the same PR cycle removed them — see the "Drop Backward-Compat Per-Source Attrs" entry above. soulseek-specific internals are now reached via `orchestrator.client(\'soulseek\')._make_request(...)`.) adding usenet now = one new client class + one registry entry, no orchestrator changes. follow-up: cin\'s metadata engine work may shape further refactors (e.g. extracting search retry / quality filter — left per-source for now since search code is genuinely 90% source-specific).' },
        { title: 'Discogs Collection in "Your Albums"', desc: 'discord request: pull your discogs collection into the your albums section on discover, similar to spotify liked albums. set your discogs personal access token on settings → connections (already there from prior work) and add discogs as one of the configured sources via the gear button on your albums. background fetcher pulls your full collection (all folders, all pages — capped at 5000 releases), normalizes artist names (strips discogs `(N)` disambiguation suffix), dedupes against any spotify/tidal/deezer-saved versions of the same album. clicking a discogs-only album opens with discogs context — full release detail (year, format, label, country, tracklist) from the /releases endpoint. clicking an album that exists in both your spotify saved AND discogs collection prefers spotify (download flow is more direct). discogs is physical-media-first so many releases won\'t have streaming equivalents — those still show in the grid but the modal flow may need to fall back to a name search to find a downloadable digital version.', page: 'discover' },
        { title: 'Drop Redundant "Your Spotify Library" Section on Discover', desc: 'discover page used to show two near-identical sections: "Your Albums" (cross-source aggregator across spotify/deezer/etc) AND "Your Spotify Library" (spotify-only). same UI, same grid, same filter / sort / download-missing controls — the spotify-only one was a strict subset of what your albums already covers. removed it. spotify saved albums still surface via the your albums section with spotify as one of its configured sources (gear button → configure sources). backend collection / storage is unchanged — the watchlist scanner still populates the spotify_library_albums cache for your albums to read.', page: 'discover' },
        { title: 'Library Disk Usage on Stats Page', desc: 'discord request (samuel [KC]): show how much disk space the library takes. new card on stats → system statistics shows total bytes + per-format breakdown (FLAC vs MP3 vs M4A bars). data comes from `tracks.file_size` populated during deep scan from whatever the media server already returns (plex MediaPart.size, jellyfin MediaSources[].Size, navidrome song.size, soulsync standalone os.path.getsize) — zero filesystem walk overhead. existing libraries see "Run a Deep Scan to populate" until the next deep scan fills in sizes; partial coverage shown as "X tracks measured (+Y pending)". migration is additive (NULL on legacy rows) so upgrading users have nothing to do.', page: 'stats' },
        { title: 'Fix: ReplayGain Wrote Same +52 dB Gain to Every Track', desc: 'noticed every downloaded track came out with `replaygain_track_gain: +52.00 dB` regardless of actual loudness. cause: parser used `re.search` which returned the FIRST `I:` (integrated loudness) reading from ffmpeg\'s ebur128 output. that\'s the per-window measurement at t=0.5s — almost always ~-70 LUFS because tracks start with silence/encoder padding. -18 (RG2 reference) - (-70) = +52 dB on every track. fix: parser now anchors to the `Summary:` block at the end of ffmpeg\'s output and reads the actual integrated loudness from there, not the silent-intro partial. defensive fallback uses the LAST per-window reading if Summary is missing (still better than the first). gains now reflect real per-track loudness.', page: 'downloads' },
        { title: 'Fix: Tracks Showed Completed When File Was Quarantined', desc: 'caught downloading kendrick mr morale: three tracks (rich interlude, savior interlude, savior) showed ✅ completed in the modal but were missing on disk. two layered bugs. (1) the post-process verification wrapper had a fallback that assumed success when no `_final_processed_path` was in context — but integrity-rejected files (which get quarantined instead of moved) leave that path unset, so the wrapper marked them complete. now wrapper explicitly checks `_integrity_failure_msg` and `_race_guard_failed` markers before the assume-success fallback. failed integrity = task marked failed, batch tracker notified with success=false. (2) acoustid skip-logic was too lenient — when fingerprint confidence was very high and either title OR artist matched a bit, it skipped verification with reason "likely same song in different language/script." that fired for english-vs-english by the same artist with the word "interlude" in both — same artist + 0.55 title sim = skip = wrong file accepted. tightened: skip now requires non-ASCII chars present (real language/script case) AND artist match, OR very high title similarity (≥0.80) AND artist match. english-vs-english with very different titles by same artist no longer skipped — verification correctly returns FAIL and the wrong file gets quarantined.', page: 'downloads' },
        { title: 'Stop Navidrome From Splitting Albums Over Inconsistent MBIDs', desc: 'discord report (samuel [KC]): tracks of the same album sometimes carry different MUSICBRAINZ_ALBUMID tags, which causes navidrome to split the album into multiple entries. two-part fix: (1) the MBID Mismatch Detector now does a second scan that groups tracks by db album, finds the consensus (most-common) album mbid, and flags dissenters — fix action rewrites the dissenter\'s tag to match. catches existing inconsistencies in your library. (2) root cause: per-track musicbrainz release lookups went through an in-memory cache that\'s capped at 4096 entries and dies on server restart, so big libraries / restarts could resolve different release ids for tracks of the same album. added a persistent sqlite-backed cache so a release mbid resolved ONCE for an album applies to every future track of that album for the install\'s lifetime. strictly additive: any failure in the persistent layer falls through to the live musicbrainz lookup exactly as before.', page: 'library' },
        { title: 'Lidarr: Right Track Lands on Disk + Profile Lookup Stops Failing', desc: 'lidarr is an album-grabber — when you ask for one track it grabs the whole album, then we pick the wanted track out. old code blindly took the first imported file as the result, so any track you asked for got mistagged as track 1 of the album. now matches the wanted title against lidarr\'s track list (with punctuation-tolerant fuzzy compare) and copies only that file. also fixed a hardcoded `metadataProfileId=1` that broke artist-add on installs where someone had renamed/recreated profiles, and a polling-loop bug where the inner break never escaped the outer poll loop so completion detection was delayed. settings tooltip updated to be honest: lidarr is best for full-album grabs and effectively a no-op for playlist sync (track searches return nothing useful, hybrid mode falls through to your other sources).', page: 'settings' },
        { title: 'SoundCloud as a Download Source', desc: 'discord request (toasti): some tracks (DJ mixes, sets, removed-from-spotify exclusives) only live on soundcloud. soundcloud now plugs into the existing download-source picker on settings → downloads — pick "SoundCloud Only" or include it in the hybrid order alongside soulseek / youtube / tidal / qobuz / hifi / deezer / lidarr. anonymous-only (no account needed); quality is whatever soundcloud serves anonymously, typically 128 kbps mp3 or aac depending on the upload. soundcloud doesn\'t expose lossless to anyone, so don\'t expect flac. follows the exact same wiring contract as every other download source — search dispatch, hybrid fallback, queue / cancel / clear, sidebar source label, provenance + library history all work plug-and-play.', page: 'settings' },
        { title: 'Fix Qobuz Connection Not Sticking After Login', desc: 'logging in via the qobuz connect button on settings showed "connected: <username> (active)" but underneath an error said "qobuz not authenticated...", and the dashboard indicator stayed yellow. cause: two separate qobuz client instances run side by side (one for the auth flow, one for the enrichment worker) and login only updated the first one. now the worker\'s client gets synced from config the moment login / token / logout completes, so the dashboard indicator goes green and connection-test stops yelling.', page: 'settings' },
        { title: 'Fix Lossy Copy Not Deleting Original FLAC', desc: 'with lossy copy enabled and "delete original" turned on (you wanted an mp3-only library), every download still left both the flac and the converted mp3 sitting in the same folder. the setting was being read but never acted on during the conversion step. now the original gets removed right after a successful conversion, with a same-path safety check + graceful handling if the original is already gone or locked.', page: 'settings' },
        { title: 'Watchlist Stops Re-Downloading Tracks That Already Exist', desc: 'a track that was already on disk got re-downloaded by the watchlist on every scan because the library had stale album metadata for it (file tagged on the wrong album by an old import) and the album fuzzy comparison declared the track missing. now the watchlist also matches by stable external IDs (spotify / itunes / deezer / tidal / qobuz / musicbrainz / audiodb / hydrabase / isrc) before falling through to the fuzzy block — so any track whose tags or DB row carry a matching ID is recognized as already present regardless of album drift. provider-neutral, falls through to existing fuzzy logic for older imports without IDs.', page: 'watchlist' },
        { title: 'Persist Source IDs at Download Time + Backfill on Sync', desc: 'every download already collects spotify/itunes/deezer/tidal/qobuz/musicbrainz/audiodb/hydrabase/isrc IDs during post-processing, but for plex/jellyfin/navidrome users they got dropped on the floor — only enrichment workers eventually wrote them onto the tracks row, hours later. now those IDs persist to the track_downloads table immediately, the media-server sync code copies them onto the new tracks row the moment it gets created, and the watchlist scanner has a second-tier fallback to query provenance directly when the tracks row hasn\'t been synced yet. closes the enrichment-wait window — freshly downloaded files are recognizable on the very next watchlist scan instead of after enrichment catches up.', page: 'library' },
        { title: 'Fix Tidal Auth Error 1002 for Docker / Remote Access', desc: 'tidal returned error 1002 ("invalid redirect URI") on every authentication attempt for users accessing soulsync from a network IP. cause: when the redirect_uri config field was empty (which it usually was, because the UI just shows the default as a placeholder without saving it), the /auth/tidal route silently overrode the constructor default with a uri built from request.host — http://192.168.x.x:8889/tidal/callback. that didn\'t match what users had registered in their tidal developer portal (http://127.0.0.1:8889/tidal/callback per the docs and UI default), so tidal rejected the authorize request before users ever saw the consent screen. fix: drop the request-host fallback entirely. empty config now falls back to the constructor default that matches the documented portal registration. the existing post-auth swap-step instructions handle the docker/remote-access case as designed.', page: 'settings' },
        { title: 'Auto-Import: Live Per-Track Progress in History', desc: 'dropping an album into the staging folder used to leave the auto-import history blank for the entire processing window — sometimes 5+ minutes for a full album — because the database row only got written after every track was post-processed. now an in-progress row gets inserted up-front (status=processing) the moment processing starts, then updated to completed/failed when done. the status indicator + progress bar show "processing speak now — track 3/14: mine", and the history card itself gets a pulsing "Processing" badge, swaps its meta line to "track 3/14: mine", and highlights the currently-processing row in the expanded track list (with prior tracks dimmed as done). one row per album, not per track, so the history list stays clean.', page: 'import' },
        { title: 'Reject Broken Files from slskd Before Tagging', desc: 'slskd sometimes reports a download as complete when the file is actually broken — truncated transfer, corrupted FLAC frames, or the wrong file matched on a similar filename. those slipped through into the library and surfaced as "song plays for 5 seconds and stops" or "track shows the wrong duration in plex." now every download gets a fast integrity check after the file stabilizes but before tagging / library sync: file size sanity (catches 0-byte and stub transfers), mutagen parse (catches header damage and wrong-format-with-right-extension cases), and duration agreement against the metadata source\'s expected length within a 3-second tolerance (5s for tracks over 10 minutes). failed files get quarantined to `ss_quarantine/` with a JSON sidecar explaining the failure, and the download slot is freed so a retry from another candidate can run.', page: 'downloads' },
        { title: 'Auto-Import: Multi-Disc Albums + Featured-Artist Tag Handling', desc: 'two longstanding auto-import gaps that surfaced when a kendrick lamar deluxe rip got dropped into staging. (1) folders containing only `Disc 1/`, `Disc 2/` subfolders (no loose audio at the parent level) used to be invisible to the scanner — disc folders were only attached to a parent when the parent had its own loose tracks. now scanner treats a parent of disc-only subfolders as the album candidate. (2) tag identification grouped files by `(album, artist)` — but per-track artist often varies on albums with features ("kendrick lamar" vs "kendrick lamar, drake" vs "kendrick lamar, dr. dre"), which fragmented the consensus and rejected real albums. now groups by album first, picks the dominant artist within that album group; also prefers `albumartist` tag over per-track `artist` since the former is the album-level identity. as a defensive bonus, when the staging folder itself becomes the candidate (raw disc folders dropped at the root with no album wrapper), the folder-name fallback gets skipped — the name "Staging" was matching against random albums in the metadata source.', page: 'import' },
        { title: 'Album Completeness Auto-Fill Works on Docker / Shared Library Setups', desc: 'github issue #476 (gabistek): the "auto-fill" button on the album completeness findings page returned `Could not determine album folder from existing tracks` for every album on docker setups (and any setup where the media-server library lives somewhere other than the soulsync transfer/download folders). cause: the repair worker\'s path resolver only probed the transfer + download folders, ignoring the user-configured `library.music_paths` and the plex-reported library locations. that missing search space meant docker users — whose plex/jellyfin library is bind-mounted at `/music` while soulsync\'s transfer is at `/transfer` — got silent "file not found" results for every existing track. extracted the full resolver (with library + plex sources) into a shared `core/library/path_resolver.py` and wired it into all five repair-worker call paths plus the four jobs that had their own incomplete copy. side benefit: every other repair job (dead file cleaner, mbid mismatch detector, lossy converter, acoustid scanner, unknown artist fixer) also stops missing files in the media-server library mount.', page: 'library' },
        { title: 'Sidebar Library Button Shows Artist Breadcrumb', desc: 'when you open an artist detail page (from library, search, or the global search popover), the sidebar Library button now lights up and rewrites its label to "Library / Artist Name" — long names truncate with an ellipsis and the full name shows on hover. revertes to plain "Library" when you leave. purely visual, no functionality change.', page: 'library' },
        { title: 'Enrichment Bubble Routes Consolidated', desc: 'internal — every dashboard enrichment bubble (musicbrainz, spotify, itunes, deezer, discogs, audiodb, lastfm, genius, tidal, qobuz) used to hit its own per-service status / pause / resume route in web_server.py. unified them under a single registry-driven endpoint set: /api/enrichment/<service>/<action>. spotify\'s rate-limit guard, lastfm/genius yield-override behavior, and tidal/qobuz extra status fields are encoded as data on the registry. 27 new tests cover the registry behavior.' },
        { title: 'Drop Old Per-Service Enrichment Routes', desc: 'internal — followup to the registry consolidation. now that the dashboard has cut over to /api/enrichment/<service>/<action>, deleted the 30 hand-rolled per-service routes from web_server.py (musicbrainz/audiodb/discogs/deezer/spotify/itunes/lastfm/genius/tidal/qobuz status+pause+resume). ~510 lines gone from the monolith, no behavior change.' },
    ],
    '2.4.1': [
        // --- May 1, 2026 — patch release ---
        { date: 'May 1, 2026 — 2.4.1 release' },

        // --- Watchlist / wishlist correctness ---
        { title: 'Watchlist No Longer Re-Downloads Compilation Tracks', desc: 'spotify and your media server name compilation albums differently — "napoleon dynamite (music from the motion picture)" vs "napoleon dynamite ost". the watchlist scanner used a strict 0.85 fuzzy threshold against the raw names, which always failed for soundtracks / deluxe-editions, so it kept re-adding the same track to the wishlist on every scan. one user reported the same song downloaded 7 times. now strips qualifier parentheticals (music from..., ost, deluxe edition, remastered) before comparing, with a volume / disc / part guard so vol 1 vs vol 2 still count as different.', page: 'watchlist' },
        { title: 'Duplicate Detector Catches slskd Dedup Orphans', desc: 'when a track downloaded multiple times, slskd appended "_<timestamp>" to each copy and the media-server scan often parsed inconsistent titles for them — so the duplicate detector\'s title-bucket pass never compared them. added a second pass that re-buckets leftover tracks by canonical filename stem (slskd dedup tail stripped). seven copies of the same song in one folder now get caught as one duplicate group.', page: 'library' },
        { title: 'Clean Up slskd Dedup Orphans After Import', desc: 'slskd appends "_<timestamp>" to a download when the destination file already exists (retried partials, the same track in multiple playlists, etc.). the canonical file imported fine but the timestamp-suffixed siblings sat in the downloads folder forever. now they get pruned right after each successful import.', page: 'downloads' },
        { title: 'Bulk Watchlist Add: Try Every Source ID Before Failing', desc: 'bulk-add to watchlist used to give up if your active metadata source didn\'t resolve the artist. now falls back through every cached source id (spotify, deezer, itunes, discogs, hydrabase) before declaring failure. fixes adds going dead when one source rate-limited.', page: 'watchlist' },
        { title: 'Wishlist Respects Configured Providers', desc: 'wishlist UI was hardcoded to spotify in some places — labels, retry copy, source defaults. now mirrors your active primary metadata source so deezer / itunes / discogs / hydrabase users see consistent text everywhere.', page: 'sync' },
        { title: 'Quality Scanner Respects Primary Metadata Provider', desc: 'quality scanner queried spotify regardless of your configured primary source, leaking spotify api calls and ignoring discogs/hydrabase data. refactored to honor the primary provider for matching; artwork preserved on wishlist handoff.', page: 'library' },
        { title: 'Wishlist Track Counts Coerced Before Category Checks', desc: 'wishlist could crash on category gating when a track count came back as a string from one source vs an int from another. now coerces to int before checking single / EP / album thresholds.' },

        // --- Match engine correctness ---
        { title: 'Featured-Artist Tracks Match Across Discography Completion', desc: 'tracks where the watched artist is a feature (not the primary) used to be treated as missing during discography completion checks. now matches against the per-track artist list so guest spots count.', page: 'library' },
        { title: 'Soundtrack Tracks Match Against Per-Track Artist', desc: 'OST/compilation tracks were matched against the album\'s primary artist (often "Various Artists") instead of the actual track artist. fixed — soundtrack tracks now match against the track\'s real artist credit, and the dead fallback path that used to swallow the miss is gone.', page: 'library' },

        // --- Spotify auth flow rework (kettui PR) ---
        { title: 'Spotify Auth Flow: Clearer UI + Reliable Sync', desc: 'rewrote the spotify connection flow on settings → connections. separated "needs auth" / "connecting" / "connected" states with explicit labels, fixed completion-sync races where the page would say connected before the token finished saving, and surfaces auth-completion failures as toasts instead of silent fails. service status reads are simpler and more honest about state.', page: 'settings' },
        { title: 'Spotify Worker Pauses on Non-Spotify Primary', desc: 'spotify enrichment worker kept running and burning api budget even when spotify wasn\'t your primary source. now pauses unless spotify is selected. also cut the per-day budget cap from a higher value to 500 calls so accidental quota burns are bounded.', page: 'dashboard' },
        { title: 'Tidal Auth Instructions Show Tidal\'s Callback Port', desc: 'tidal connect screen displayed spotify\'s callback port number in its setup steps. fixed to show tidal\'s actual port so the redirect URI users set up actually works.', page: 'settings' },

        // --- Discogs ---
        { title: 'Discogs Primary Source Gated by Token', desc: 'selecting discogs as your primary metadata source without a token now reverts gracefully instead of erroring on every call. token presence is the gate — set it on settings → connections to enable discogs as primary.', page: 'settings' },

        // --- Imports ---
        { title: 'Parallel Singles Import (3 Workers)', desc: 'singles / EP imports used to process serially. now run through a 3-worker thread pool so a long backlog of liked-songs imports finishes ~3x faster. also routes singles + EPs through the album_path template so they file correctly.', page: 'sync' },

        // --- Duplicate detector ---
        { title: 'Same-Physical-File Duplicates No Longer Flagged', desc: 'if you bind-mount the same music folder into both soulsync (e.g. /app/Transfer) and plex (e.g. /media/Music), each row in the DB pointed at the same file via a different mount root and showed up as a "duplicate". detector now recognizes this — same trailing path segments + matching durations + different mount roots = filtered out.', page: 'library' },

        // --- Bug fixes ---
        { title: 'Fix Config DB Lock Spam on Slow Disks (#434)', desc: 'on slow / heavily-loaded disks, sqlite settings DB writes raced and spammed the log with "database is locked" errors every few seconds. added a retry loop with exponential backoff and bounded retry count. silent on healthy systems, recovers on slow ones.', page: 'settings' },
        { title: 'Fix Bulk Discography Losing Album Source Context (#399)', desc: 'bulk discography downloads weren\'t carrying the album\'s source provider through the pipeline, so downstream lookups defaulted to the wrong source. fixed by threading source context through every step.', page: 'sync' },
        { title: 'Beatport Tab Hidden Temporarily', desc: 'beatport rolled out cloudflare turnstile on every public page, so the scraper that powered the beatport tab now hits a bot challenge instead of html. their official oauth api is locked behind partner registration that isn\'t open to the public. hid the tab on sync until we find a workaround — backend endpoints are kept in code so revival is a one-line html change.', page: 'sync' },
        { title: 'Surface Handler-Returned Errors in Automation last_error', desc: 'automation actions could return an error string but the engine swallowed it — last_error stayed blank, debugging was painful. now propagates returned errors into last_error so you can see what failed and why.', page: 'stats' },
        { title: 'Silence Shutdown-Time Logger Noise in CI', desc: 'pytest closes log handles before atexit runs — produced "I/O operation on closed file" stack traces in CI stderr on every test run. registered a final atexit handler that toggles logging.raiseExceptions off so shutdown is silent.' },

        // --- Performance / infra ---
        { title: 'Service Worker for Cover Art + Installable PWA', desc: 'cover art used to re-fetch from the CDN on every library / discover page visit. now a service worker caches images locally — second visit serves art instantly from disk, no network hit. also added a PWA manifest so soulsync can be installed to home screen / desktop as a standalone app (chrome / edge / safari → install soulsync). cache versioned so future strategy changes invalidate cleanly.' },
        { title: 'Browser Caching for Static Assets + Discover Pages', desc: 'static assets (js / css / icons) now get a 1-year browser cache instead of revalidating on every page load. safe because the existing ?v=static_v cache-bust query changes every server restart, so deploys still ship live. discover pages (hero, similar artists, recent releases, deep cuts) now cache 5 minutes browser-side so toggling between sections doesn\'t re-fetch everything.', page: 'discover' },
        { title: 'Faster Docker Startup — yt-dlp Pinned', desc: 'docker startup used to run `pip install -U yt-dlp` on every container start. removed that — yt-dlp is now pinned in requirements.txt so startup is fast and reproducible. tradeoff: youtube fixes ship via soulsync releases now instead of next container restart.' },

        // --- Security ---
        { title: 'Lock Down Socket.IO CORS', desc: 'socket.io was accepting websocket connections from any origin (cors=*). now defaults to same-origin only. if your websocket fails after updating, the server logs a clear warning with the rejected origin — add it to settings → security → allowed websocket origins.', page: 'settings' },
        { title: 'Settings Endpoints: Admin-Only', desc: 'the /api/settings endpoints (read, write, log-level, config-status, verify) had no auth gate — any logged-in profile could read or change service tokens, oauth secrets, api keys. now admin-only. single-admin setups (no multi-profile config) work transparently as before.', page: 'settings' },

        // --- Internal / refactoring ---
        { title: 'Major web_server.py Decomposition', desc: 'internal — pulled ~30 routes / workers / helpers out of web_server.py into focused modules under core/ (search, automation, stats, discovery, library, downloads, workers, artists, connection, debug, watchlist auto-scan, retag, redownload, library service search, duplicate cleaner, monitor, validation, staging, etc.). meaningfully smaller monolith, better unit-testable seams, no behavior change.' },
        { title: 'Metadata Helpers Reorganized into Packages', desc: 'internal — metadata helpers and runtime client management moved into proper packages (core/metadata/, core/imports/), with profile spotify cache living in the registry. clearer ownership, fewer cross-module reach-ins.' },
        { title: 'Stats Endpoints Lifted to core/stats', desc: 'internal — moved /api/stats/* and /api/listening-stats/* logic out of web_server.py into core/stats/queries.py with full test coverage.' },
        { title: 'Search Endpoints Lifted to core/search', desc: 'internal — moved /api/search and /api/enhanced-search/* logic into core/search/ (cache, sources, library_check, stream, basic, orchestrator). 612 fewer lines in web_server.py, 94 new tests.' },
        { title: 'Automation Endpoints Lifted to core/automation', desc: 'internal — moved /api/automations/* CRUD + run + history routes, progress tracking helpers, and signal collection into core/automation/ (api, progress, signals). 383 fewer lines in web_server.py, 72 new tests.' },
    ],
    '2.4.0': [
        // --- April 26, 2026 — Search & Artists unification + reorganize queue ---
        { date: 'April 26, 2026 — 2.4.0 release' },
        { title: 'Reorganize Queue Polish', desc: 'cleaned up some race conditions in the reorganize queue. cancel + bulk dedupe behavior is solid now. preview button no longer gets stuck disabled on errors.', page: 'library' },
        { title: 'Reorganize Queue with Live Status Panel', desc: 'reorganize is now a queue with a live status panel. spam-click all you want — items run one at a time and you can keep browsing while they go. expand the panel to see queue + cancel buttons.', page: 'library' },
        { title: 'Album Completeness Job Actually Works', desc: 'completeness job was finding zero issues for everyone. now it works — uses real expected track counts from your metadata source instead of comparing your library to itself.', page: 'library' },
        { title: 'Reorganize Routes Through the Download Pipeline', desc: 'reorganize now uses the same pipeline downloads use. fixes 3-disc albums collapsing to single-disc and tracks silently disappearing on you. extracted to core/library_reorganize.py.', page: 'library' },
        { title: 'Spotify: Longer Post-Ban Cooldown', desc: 'bumped the post-ban cooldown from 5 to 30 minutes. first call after a ban was getting re-banned within seconds because spotify\'s memory outlasts the cooldown.', page: 'dashboard' },
        { title: 'Tidal: No More Silent Quality Downgrades', desc: 'tidal was silently serving 320kbps when you asked for hires. now it rejects the downgrade and the fallback chain advances properly — or fails honestly if you have "hires only, no fallback" set.', page: 'downloads' },
        { title: 'Search Source Picker Icon Row', desc: 'search page now has a row of source icons above the bar — one per source. typing only searches the active source instead of fanning out to all of them. click another icon to switch.', page: 'search' },
        { title: 'Per-Query Source Cache', desc: 'switching back to a source you already searched is instant — results are cached for the current query. cache resets when you type a new query. ~6-7x fewer api calls per search.', page: 'search' },
        { title: 'Global Search Widget Source Parity', desc: 'the sidebar global search popover got the same source icon row + cache dots + fallback banner as the full search page.', page: 'search' },
        { title: 'Rate-Limit Fallback Banner', desc: 'if the backend swaps your selected source for a working one (e.g. spotify rate-limited → deezer), you get a small amber banner explaining the swap. icon for the failed source gets an amber border.', page: 'search' },
        { title: 'Explicit Source Selection on /api/enhanced-search', desc: 'enhanced-search endpoint takes a source param now to skip the fan-out backend-side. cache keys isolate per-source so single and multi-source results don\'t collide.', page: 'search' },
        { title: 'Shared Enhanced-Search Fetch Helper', desc: 'internal — search dropdown and global widget share one fetch helper now instead of duplicating the post boilerplate.', page: 'search' },
        { title: 'Search Page Renamed to /search', desc: 'search page is now /search instead of the confusing /downloads (which clashed with the actual downloads page). old urls still work.', page: 'search' },
        { title: 'Embedded Download Manager Removed from Search Page', desc: 'killed the duplicate download manager on the search page (~330 lines of dead code). dedicated downloads page is the only one now.', page: 'search' },
        { title: 'Artists Sidebar Entry Retired', desc: 'removed the artists sidebar entry — unified search already does what it did. old /artists urls still resolve.', page: 'search' },
        { title: 'Artist Detail Back Button Fallback', desc: 'back button on inline artist detail uses browser history when you arrived from outside the artists page, instead of dumping you on an empty artists search.', page: 'search' },
        { title: 'Interactive Help Updated for Unified Search', desc: 'rewrote the click-for-help annotations and the first-download tour for the new search page. retired the standalone browse-artists tour.', page: 'help' },
        { title: 'Unified Source-Picker Controller', desc: 'internal — search page and global widget share one controller now (~380 lines of duplicate state/fetch/render code gone). bug fixes land everywhere at once.', page: 'search' },
        { title: 'Fix Clean Search History Automation Crashing', desc: 'hourly clean-search-history automation was crashing on a stale base_url path. fixed.', page: 'stats' },
        { title: 'Search Results Always Visible', desc: 'killed the show/hide results toggle. visibility is just based on whether you\'ve typed a query.', page: 'search' },
        { title: 'Cached Search Results Restore on Navigate-Back', desc: 'leaving and coming back to /search now re-renders your last query\'s results from cache instead of hiding them.', page: 'search' },
        { title: 'Fix Soulseek Handoff from Global Search', desc: 'clicking soulseek in the global search popover used to run metadata search against your default source instead of basic file search. fixed.', page: 'search' },
        { title: 'Stale Search Requests No Longer Flash Empty', desc: 'fast retypes used to flash an empty state for a moment while the new fetch was still mid-flight. added a request-sequence token so old responses don\'t clobber new ones.', page: 'search' },
        { title: 'Soulseek Icon Dims When slskd Isn\'t Configured', desc: 'soulseek icon dims if you don\'t have slskd set up. clicking it routes to settings → downloads instead of failing silently.', page: 'search' },
        { title: 'Fix Discover Hero View Discography 404', desc: 'view discography on the discover hero was 404ing for non-library artists. fixed by passing the source through to /api/artist-detail.', page: 'discover' },
        { title: 'MusicBrainz Search Actually Works', desc: 'musicbrainz search was returning empty/garbage results and taking 30+ seconds. rewrote it — artist, track, and album searches all work now and complete in ~3 seconds on cold cache.', page: 'search' },
        { title: 'MusicBrainz Search Follow-Ups', desc: 'three more musicbrainz fixes — artist images now resolve via itunes/deezer fallback, total_tracks off-by-one fixed, and "artist title" queries no longer browse the whole discography.', page: 'search' },
    ],
    '2.39': [
        // --- April 22, 2026 ---
        { date: 'April 22, 2026' },
        { title: 'Fix Wrong-Artist Tracks Silently Downloading from Tidal', desc: 'A user reported that searching for "Leave A Light On" by Maduk on Tidal silently downloaded Tom Walker\'s (completely different) song of the same name, embedding Maduk metadata into Tom Walker\'s audio. Two layers of defense were failing: (1) the candidate artist gate used `< 0.4` similarity and "maduk" vs "tom walker" scored exactly 0.400, slipping past the fencepost — raised to `< 0.5`. (2) AcoustID verification correctly identified the mismatch but returned SKIP (accept) instead of FAIL (quarantine) when title matched but artist was clearly different and the expected artist was absent from every recording. Now returns FAIL when artist similarity < 0.3 (clear mismatch); preserves SKIP for the ambiguous 0.3-0.6 range (covers/collabs/formatting differences)', page: 'sync' },
        { title: 'Tidal Search Falls Back to Shortened Queries on 0 Results', desc: 'Tidal\'s search chokes on long queries with multiple qualifier words (e.g., "maduk transformations remixed fire away fred v remix" returns nothing, but dropping "fred v remix" works). Search now retries with up to 4 progressively-shortened variants when the original returns 0 results. Qualifier-safe: if the original query mentions Live/Remix/Acoustic/etc., fallback results must still contain those keywords in their track names — otherwise a shortened query could silently downgrade "(Live)" to the studio version. Returns ([], []) if no variant preserves the qualifiers, same as before', page: 'sync' },
    ],
    '2.38': [
        // --- April 21, 2026 (late) ---
        { date: 'April 21, 2026 (late)' },
        { title: 'Fix Missing Cover Art on Manually Fixed Discovery Tracks', desc: 'The cache matched_data built by the fix modal dropped the image_url and album.images fields when album came back as a bare string (common for Deezer/iTunes search results). Result: re-discovery used the cached match but downloads showed no artwork. Cache writes now carry image_url through to album.images + top-level matched_data, matching what the in-memory state already did. Re-fix the track to refresh its cache entry (INSERT OR REPLACE)', page: 'sync' },
        { title: 'Fix Manual Discovery Fixes Lost After Restart (Non-Spotify Users)', desc: 'When you clicked Fix on a discovery track and picked a manual match, the cache save hardcoded the provider as "spotify" regardless of your configured primary metadata source. On re-scan, the worker queried the cache with your actual primary (Deezer, iTunes, Discogs, Hydrabase) and missed the fix entirely. All 5 save sites (Tidal / Deezer / Spotify Public / YouTube / Discovery Pool) now use the active primary source, matching what the automatic workers already do', page: 'sync' },
    ],
    '2.37': [
        // --- April 21, 2026 (evening) ---
        { date: 'April 21, 2026 (evening)' },
        { title: 'Fix Auto-Watchlist Ignoring Global Override Settings', desc: 'The scheduled auto-watchlist scan (not the manual one) called scan_watchlist_artists directly, which bypassed Global Override application. So if you disabled Albums or Live under Watchlist → Global Override, full albums and live tracks still got added to the wishlist during the nightly scan. Override logic now runs inside scan_watchlist_artists so every entry point respects it', page: 'watchlist' },
        { title: 'Fix Live Version Filter False Positives', desc: 'The \\blive\\b regex was too loose — it flagged any title with the word "live" regardless of context, so "What We Live For" by American Authors, "Live Forever" by Oasis, and similar verb uses got treated as live recordings. Tightened to require clear live-recording context: "(Live)", "- Live", "Live at/from/in/on/version/session/etc". Fixes both the watchlist/backfill track filter and the Library Maintenance Live/Commentary Cleaner', page: 'library' },
    ],
    '2.36': [
        // --- April 21, 2026 ---
        { date: 'April 21, 2026' },
        { title: 'Fix Metadata Cache Bar Duplicating on Findings Dashboard', desc: 'The "Metadata Cache · View Details" bar under the findings chips could stack into 2–6 copies if the dashboard refreshed while a cache-health fetch was still in flight. Each resolved fetch appended its own section. Now each fetch clears any existing bar before appending', page: 'library' },
        { title: 'Fix Discography Backfill Stalling When Repair Worker Paused', desc: 'Force-running a job via "Run Now" stalled forever when the master repair worker was paused. The job entered the scan function, logged its starting banner, then blocked on the first wait_if_paused check. Force-run now bypasses the master-pause — scheduled runs still respect it', page: 'library' },
        { title: 'Discography Backfill: 3-Option Fix Dialog', desc: 'Clicking Fix on a missing-track finding now prompts "Add to Wishlist", "Just Clear Finding", or "Cancel" instead of silently adding to wishlist. Bulk Fix shows the same prompt once for all selected backfill findings', page: 'library' },
        { title: 'Discography Backfill: Auto-Add to Wishlist Setting', desc: 'New opt-in setting in the Discography Backfill job config. When enabled, missing tracks are pushed straight to the wishlist during the scan AND a finding is created for the log. Default is off — you review and click Fix', page: 'library' },
        { title: 'Discography Backfill: Faster Batched Matching', desc: 'Each artist scan now pre-fetches the library albums + tracks once and matches in-memory — same fast path the Library and Artists pages use. Avoids thousands of per-track SQL queries on artists with big libraries', page: 'library' },
        { title: 'Discography Backfill: Rich Album Context per Finding', desc: 'Every finding now carries a full album dict (id, name, album_type, release_date, images, artists, total_tracks) matching the wishlist pipeline shape. No more generic "Add to Wishlist" loss of release metadata', page: 'library' },
        { title: 'Discography Backfill: Per-Artist Progress Logs', desc: 'Scan logs now show [N/50] Scanning ArtistName for each artist processed, with found-count or "no missing tracks" afterward. Makes it obvious whether the job is actually progressing' },

        // --- April 20, 2026 (part 2) ---
        { date: 'April 20, 2026 (evening)' },
        { title: 'Massively Faster Artist Detail Page Loads', desc: 'Artist discography completion checks used to fire hundreds of SQL queries per page load — 15+ fuzzy title/artist searches per album times 30 albums per artist. Now pre-fetches the artist\'s library albums and tracks ONCE upfront, then matches everything in-memory. Same matching logic and accuracy, roughly 100x fewer SQL round-trips. Applies to both the Library artist page and the Artists search page', page: 'library' },
        { title: 'Fix Reorganize All Ignoring Album Type', desc: 'Reorganize All was sending every album — EPs, singles, and compilations — into the "Albums" folder because the $albumtype template variable silently defaulted to "Album". The variable is now resolved from the album\'s record_type (with track-count fallback) so ${albumtype}s produces the expected Albums/Singles/EPs/Compilations split', page: 'library' },

        // --- April 20, 2026 ---
        { date: 'April 20, 2026' },
        { title: 'Discography Backfill Maintenance Job', desc: 'New library maintenance job that scans each artist in your library, fetches their full discography from metadata sources, and creates findings for any missing tracks. Review findings and click "Add to Wishlist" to queue them for download. Respects content filters (live/remix/acoustic/compilation) and release type filters. Opt-in, disabled by default', page: 'library' },
        { title: 'Multi-Artist Tagging Options', desc: 'Three new settings: configurable artist separator (comma/semicolon/slash), multi-value ARTISTS tag for Navidrome/Jellyfin multi-artist linking, and "Move featured artists to title" mode. All opt-in with defaults matching current behavior', page: 'settings' },
        { title: 'Reorganize All Albums for Artist', desc: 'New "Reorganize All" button in the enhanced library artist header. Processes all albums for an artist sequentially using the configured path template. Shows progress per album, continues on error', page: 'library' },
        { title: 'Enriched Downloads Page Cards', desc: 'Download cards now show album artwork thumbnail, artist name, album name, source badge, and quality badge — all pulled from existing metadata context. No extra API calls', page: 'downloads' },
        { title: 'Template Variable Delimiter Syntax', desc: 'Use ${var} syntax to append literal text to template variables: ${albumtype}s produces "Albums", "Singles", "EPs". Both $var and ${var} syntaxes work. Updated validation and hint text for all templates', page: 'settings' },
        { title: 'AcoustID Fix Action Prompt', desc: 'AcoustID mismatch findings now show a 3-option fix prompt (Retag/Re-download/Delete) instead of silently defaulting to retag. Works for both individual and bulk fix', page: 'library' },
        { title: 'Fix Sync Buttons on Undiscovered Playlists', desc: 'Sync buttons on ListenBrainz/Last.fm Radio playlists were visible before discovery due to the standalone mode handler resetting display:none on every WebSocket push. Now only restores buttons it specifically hid' },
        { title: 'Fix Wing It Tracks Added to Wishlist During Sync', desc: 'Wing It fallback tracks with no real metadata were being added to wishlist when they failed to match on the media server during playlist sync. Now skipped by checking the wing_it_ ID prefix' },
        { title: 'Fix iTunes Region-Restricted Albums', desc: 'iTunes API sometimes returns album metadata without song tracks for region-restricted releases. The empty result was cached permanently. Now tries fallback storefronts for actual songs, and skips caching empty results' },
        { title: 'Fix Disc Subfolder Missing on Single-Track Downloads', desc: 'Downloading a single track from search for a multi-disc album placed it without the Disc N/ subfolder. Now resolves total_discs from the album tracklist when not already known' },
        { title: 'Fix Allow Duplicate Tracks Setting Not Working', desc: 'The "Allow duplicate tracks across albums" setting was ignored during album download analysis. Tracks found in other albums were marked as owned and skipped. Now only checks ownership within the target album when duplicates are allowed' },
        { title: 'Stop slskd Log Spam When Not Active', desc: 'Download monitor and transfer cache were polling slskd every second during active downloads regardless of whether Soulseek was configured. Now skips slskd API calls entirely when Soulseek is not in the active download source' },
        { title: 'Fix AcoustID High-Confidence Skip', desc: 'AcoustID verification was letting wrong files through when the fingerprint score was high (0.95+) even with very low title/artist similarity. Now requires at least partial title or artist match before skipping verification' },
        { title: 'Fix Navidrome Multi-Library Import', desc: 'Full database refresh was importing albums from all Navidrome music folders even when only one was selected in settings. Now filters albums to the selected music folder using a cached album ID set' },
        { title: 'Fix Repair Worker Crash on Zero Interval', desc: 'Jobs with interval_hours set to 0 caused ZeroDivisionError in the repair worker staleness calculation. Now skips jobs with invalid intervals' },
        { title: 'Fix Playlist Mode Missing Metadata and Cover Art', desc: 'Playlist folder mode passed null album_info to metadata enhancement, causing the entire function to crash silently. All metadata was wiped from the file. Now normalizes null to empty dict and falls back to spotify_album context for cover art' },
        { title: 'Fix Unknown Artist Fixer Column Name', desc: 'The unknown_artist_fixer repair job crashed with "no such column: t.deezer_track_id". The tracks table uses deezer_id, not deezer_track_id' },
        { title: 'Fix Auto-Import Using Wrong Artist from Tags', desc: 'Auto-import trusted embedded file tags for artist names even when the parent folder clearly indicated the correct artist. Mixtapes tagged with DJ names (e.g. "Slim" instead of "2Pac") got organized under the wrong artist. Now uses parent folder structure as artist override when folder depth indicates an Artist/Album layout' },

        // --- April 19, 2026 ---
        { date: 'April 19, 2026' },
        { title: 'Fix Wishlist Albums Cycle Stuck at 1 Concurrent', desc: 'Auto-wishlist processing during the "albums" cycle was limited to 1 concurrent download even with higher configured settings. The max_concurrent=1 restriction is only needed for Soulseek folder-based album grabs, not individual wishlist track downloads. Albums cycle now uses the configured concurrency like singles' },
        { title: 'Fix Track Ownership False Positives Across Albums', desc: 'Track ownership check on the artist detail page now filters by album context. Previously "Thriller" from Thriller 25 would show as owned on every Michael Jackson album containing a track called Thriller. Now only matches within the specific album being checked' },
        { title: 'Fix Wing It Tracks Added to Wishlist via Button', desc: 'Wing It fallback tracks were skipped from wishlist on failed downloads but not when manually clicking "Add to Wishlist". Now consistently skipped across all paths' },
        { title: 'Fix Debug Info Showing Zero Counts', desc: 'Copy Debug Info button showed 0 for watchlist, wishlist, and automation counts due to calling get_db() instead of get_database(). Silent NameError was caught by try/except' },
        { title: 'Fix Album Track Lookup Hardcoded to Spotify', desc: 'Clicking an album on the Artists page to download tracks was hardcoded to use Spotify even when the user\'s primary metadata source was Deezer or iTunes. Now uses the configured primary source with Spotify as fallback' },
        { title: 'Fix Wishlist Splitting Albums by Track Artist', desc: 'Adding a multi-artist album (like a soundtrack) to wishlist was creating separate entries per track artist instead of keeping all tracks under the album artist. Now uses the album-level artist context when available to keep tracks grouped correctly' },
        { title: 'Fix Artist Search Case Sensitivity', desc: 'Artist search on the Artists page now normalizes all-lowercase queries to title case before hitting metadata APIs. Some APIs return fewer or no results for lowercase queries like "foreigner" vs "Foreigner"' },
        { title: 'Lidarr Download Source Now Production-Ready', desc: 'Lidarr is now a fully functional download source with complete orchestrator integration. Downloads appear in the UI, status polling works, cancellation works, and cleanup on shutdown works. Error messages are now visible in the download list. Removed "(Development)" label' },
        { title: 'Fix M3U Showing All Tracks as Missing', desc: 'M3U playlist files were generated before post-processing finished, so file paths pointed to download locations instead of final library paths. M3U is now regenerated from the backend after all post-processing completes, resolving real library paths from the DB' },
        { title: 'Fix AcoustID Retag Not Writing to File', desc: 'The AcoustID mismatch "Retag" fix action was only updating the database record without writing corrected tags to the actual audio file. Now writes title and artist tags to the file using Mutagen after updating the DB' },
        { title: 'Fix Downloads Badge Dropping to 300', desc: 'Downloads nav badge showed the correct count from WebSocket but dropped to max 300 after opening the Downloads page because it recounted from a truncated local array. Badge now stays accurate from the server-side count' },
        { title: 'Fix Server Playlist Find & Add Position', desc: 'When using "Find & add" on server playlists with Plex, the track was always appended to the end instead of inserted at the correct position. Now moves the track to the right slot after adding' },
        { title: 'Smarter Fix Modal Search Results', desc: 'The discovery Fix modal now sorts search results to prioritize standard album versions over live recordings, remixes, covers, soundtracks, remasters, and deluxe editions. Previously the first result was often a live or remix version instead of the original studio track' },
        { title: 'Unmatch Discovery Tracks', desc: 'Found tracks in playlist discovery now have a red ✕ button to remove the match. Sets the track back to Not Found so it won\'t be downloaded. For mirrored playlists, the unmatch persists in the DB and is respected on re-discovery runs' },
        { title: 'Customizable Music Video Naming', desc: 'Music video file naming is now configurable via a path template in Settings → Library → Paths & Organization. Default unchanged (Artist/Title-video.mp4). Remove "-video" from the template to get clean filenames. Available variables: $artist, $artistletter, $title, $year', page: 'settings' },
        { title: 'Fix Soulseek Log Spam', desc: 'The "Clean Search History" automation no longer tries to connect to slskd when Soulseek is not the active download source, eliminating noisy connection error logs for users who don\'t use Soulseek' },
        { title: 'Auto Wing It Discovery Fallback', desc: 'When playlist discovery fails to match a track on any metadata API (Spotify, Deezer, iTunes, etc.), the track now automatically falls back to Wing It mode instead of being marked "Not Found". Stub metadata is built from the raw source title and artist, and the track flows through the normal download pipeline via Soulseek. Amber "Wing It" badge distinguishes these from API-matched tracks. Works across all discovery sources: YouTube, Tidal, Deezer, Beatport, ListenBrainz, and mirrored playlists. Wing It stubs persist in the DB for mirrored playlists and are re-attempted on future discovery runs so real matches can replace them' },
        { title: 'Fix Library Page Crash on All Filter', desc: 'Library page could crash with "No artists found" when viewing all artists if any artist had a non-string soul_id. Individual letter filters worked because the problematic artist wasn\'t in those results. Card rendering is now fault-tolerant — one bad artist card can\'t take down the whole page', page: 'library' },
        { title: 'Fix CI Test Failures', desc: 'Fixed test suite failures caused by incomplete dummy config managers missing get_active_media_server() and script.js read encoding on non-UTF-8 locales' },

        // --- April 18, 2026 ---
        { date: 'April 18, 2026' },
        { title: 'Live Log Viewer', desc: 'New Logs tab on the Settings page — real-time terminal-style log viewer with color-coded log levels. Filter by DEBUG/INFO/WARNING/ERROR, search logs in real-time, switch between log files (app, post-processing, acoustid, source reuse). Auto-scroll, copy, clear. Live WebSocket updates every 0.5s. Smart level detection works on both logger output and print statements', page: 'settings' },
        { title: 'ReplayGain Post-Processing', desc: 'Optional ReplayGain tag analysis during post-processing. Enable in Settings → Library → Post-Processing. Analyzes loudness via ffmpeg and writes track-level gain/peak tags. Runs before lossy copy so both files get tagged. Off by default' },
        { title: 'Fix Your Albums Using Playlist Modal', desc: 'Albums in the Discover page "Your Albums" section now open with the proper album-style download modal instead of the playlist-style modal. Shows artist image, album art, and uses album download context for correct file organization', page: 'discover' },
        { title: 'Fix Tool Help Modal Not Closable', desc: 'The help "?" modal on automation triggers/actions could not be closed if the Tools page hadn\'t been visited first. Close button, backdrop click, and Escape key now work from any page' },
        { title: 'Fix Spotify OAuth Port Steal in Docker', desc: 'On fresh installs, Spotify auth probe silently started an HTTP server that stole port 8008 (crash loop) or bound loopback-only on 8888 (unreachable from host). Now skips the probe when no cached token exists' },
        { title: 'Genre Whitelist', desc: 'Filter junk genre tags (artist names, radio shows, playlist names) from enrichment. Enable strict mode in Settings → Library Preferences → Genre Whitelist. 272 curated default genres, fully customizable — add, remove, search, reset. Applied across all 10 enrichment sources. Off by default', page: 'settings' },
        { title: 'Per-Artist Watchlist Scan Source', desc: 'Override which metadata provider (Spotify, Deezer, Apple Music, Discogs) is used when scanning a specific watchlist artist for new releases. Source selector in the artist config modal only shows providers the artist has enrichment matches for. Global default unchanged unless explicitly overridden', page: 'watchlist' },
        { title: 'Standalone Full Refresh', desc: 'Full Refresh now works for SoulSync Standalone mode — clears all soulsync library records and rebuilds from audio file tags in the output folder. Previously did nothing for standalone users', page: 'tools' },
        { title: 'Folder Terminology Rebrand', desc: 'Download Path → Input Folder, Transfer Path → Output Folder, Staging Path → Import Folder. All UI labels, docs, help text, and error messages updated for clarity. No functional changes — actual paths and config keys unchanged' },
        { title: 'Enhanced Copy Debug Info', desc: 'Copy Debug Info button now includes ffmpeg version, runner type, Discogs status, wishlist count, music library paths, music videos dir, hybrid source priority, lossy copy config, auto import status, and a log file listing with sizes. Import path bug fixed. Library counts now match dashboard. Footer links to GitHub Issues', page: 'help' },
        { title: 'Troubleshooting Docs Section', desc: 'New Help page section with log file reference table, log level guide, Copy Debug Info walkthrough, common issues FAQ, and issue reporting checklist', page: 'help' },
        { title: 'Log Level Moved to Advanced Tab', desc: 'Log Level dropdown moved from Downloads tab to Settings → Advanced → Logging for better organization' },
        { title: 'Fix AcoustID Scanner Fix Action', desc: 'AcoustID mismatch "Fix" button was failing with a uuid error. Caused by a redundant local import shadowing the module-level import in Python\'s scoping rules' },
        { title: 'Fix Duplicate Detector Ignoring Allow Duplicates', desc: 'The Duplicate Detector repair job now respects the global "Allow duplicate tracks across albums" setting. Previously flagged cross-album duplicates regardless of the toggle' },
        { title: 'Fix Single Track Search Downloads Using Album Template', desc: 'Clicking a single track in search results and downloading it now uses the singles path template instead of the album template. The modal correctly showed SINGLE but the backend treated it as an album download' },
        { title: 'Fix Liked Songs Showing as YouTube', desc: 'Spotify Liked Songs playlist was misidentified as YouTube in the download modal hero section due to missing spotify: prefix detection' },
        { title: 'Fix Metadata Crash on Playlist Downloads', desc: 'Playlist and single track downloads could crash metadata enhancement with "NoneType has no attribute get" when album_info was None' },
        { title: 'Fix Library Scan Button Stuck on Stop', desc: 'Dashboard library scan polling checked for "completed" but backend sets "finished". Button now resets correctly and stats refresh on completion' },
        { title: 'Fix Deep Scan Reporting Stale Records as Failed', desc: 'Stale record removals during deep scan were counted as "failed" instead of "successful" in the completion message' },
        { title: 'Fix Settings Page Tab Flash', desc: 'Settings page no longer briefly shows all tabs on first load — tab filtering now runs before async data loading' },
        { title: 'Improved Deep Scan Logging', desc: 'Per-artist log lines now show "0 new tracks (150 existing updated)" instead of misleading "0 tracks". Completion message shows "library up to date" when nothing is new' },
        { title: 'Faster Standalone Verify', desc: 'Standalone verify button now stops counting after 10 audio files instead of 100, reducing verification time from 60+ seconds to near-instant on large libraries' },
        { title: 'MusicBrainz Search Tab', desc: 'New search tab in Enhanced and Global search — find tracks and albums on MusicBrainz\'s community database. Cover art from Cover Art Archive. Click results to open download modal with full tracklist. Finds obscure tracks that Spotify/Deezer/iTunes miss', page: 'downloads' },
        { title: 'Fix Library Page Crash on All Filter', desc: 'Library page could crash with "No artists found" when viewing all artists if any artist had a non-string soul_id. Individual letter filters worked because the problematic artist wasn\'t in those results. Card rendering is now fault-tolerant — one bad artist card can\'t take down the whole page', page: 'library' },

        // --- April 17, 2026 ---
        { date: 'April 17, 2026' },
        { title: 'SoulSync Standalone Library', desc: 'New "Standalone" server option — manage your library without Plex, Jellyfin, or Navidrome. Downloads and imports write directly to the library database with pre-populated enrichment IDs. Deep scan finds untracked files and cleans stale records. Select in Settings → Connections', page: 'settings' },
        { title: 'Auto-Import', desc: 'Background import folder watcher that automatically identifies and imports music. Three strategies: audio tags, folder name parsing, and AcoustID fingerprinting. Confidence-gated: 90%+ auto-imports, 70-90% queued for review, below 70% left for manual. Enable on the Import page Auto tab', page: 'import' },
        { title: 'Wishlist Nebula', desc: 'Wishlist redesigned as an interactive artist orb visualization. Each artist is a glowing orb with their photo — album fans and single moons orbit around them. Click orbs to expand, download albums/singles directly. Processing state shows live progress', page: 'wishlist' },
        { title: 'Automation Group Management', desc: 'Rename, delete, and bulk-toggle automation groups. Drag-and-drop automations between groups. Right-click group headers for context menu', page: 'automations' },
        { title: 'Bidirectional Artist Sync', desc: 'Artist Sync button now pulls new content from your media server AND removes stale library entries no longer on the server. Deep scan mode fetches full metadata for new tracks', page: 'library' },
        { title: 'Server Playlists — Synced vs Unsynced', desc: 'Server playlist view now shows all playlists from your media server with clear visual separation between synced and unsynced playlists', page: 'sync' },
        { title: 'Provider-Agnostic Discovery', desc: 'Similar artist matching, discovery pool, and incremental updates now work with any configured metadata source (Spotify, iTunes, Deezer) instead of requiring Spotify. Falls back through sources in priority order', page: 'watchlist' },
        { title: 'Live Sidebar Badges', desc: 'Watchlist and Wishlist sidebar nav items show live count badges that update from WebSocket pushes' },
        { title: 'Fix Source ID Embedding', desc: 'Critical fix — all source ID tags (Spotify, MusicBrainz, Deezer, AudioDB) were silently skipped on every download due to a missing function parameter. Tags now embed correctly again' },
        { title: 'Fix Watchlist Scan False Failures', desc: 'Artists with no new releases in the lookback window were incorrectly reported as scan failures. Empty discography now correctly treated as success' },
        { title: 'Fix Wishlist Album Remove', desc: 'Removing albums from the Wishlist Nebula now works — API accepts album_name as fallback when album_id is unavailable' },
        { title: 'Fix Soulseek Timeout Spam', desc: 'Dashboard stats and download status endpoints no longer poll slskd when Soulseek is not the active download source or is known to be disconnected. Eliminates connection timeout errors every 10 seconds for users who have a slskd URL configured but use YouTube/Tidal/etc.' },
        { title: 'Fix Soulseek Search Missing Album Name', desc: 'Soulseek search queries now include the album name (Artist + Album + Track) as the first search attempt for all download sources. Previously this was excluded for Soulseek-only mode, causing wrong-artist downloads when an artist name matched an album folder in another user\'s library' },
        { title: 'Reject Junk Artist Soulseek Results', desc: 'Soulseek search results from "Various Artists", "VA", "Unknown Artist", and "Unknown Album" folders are now automatically rejected. These compilation/junk folders almost never contain properly tagged files for the target artist' },
        { title: 'Clear Wishlist Cancels Downloads', desc: 'Clearing the wishlist now also cancels any active wishlist download batch. Previously the download queue would keep running after the wishlist was cleared' },
        { title: 'Downloads Batch Panel', desc: 'Downloads page now shows a batch context panel on the right side. Each active batch (wishlist, sync, album download) gets a color-coded card with progress, cancel button, and expandable track list. Color indicators on download rows link them to their batch. Completed batch history shows the last 7 days', page: 'active-downloads' },
        { title: 'Fix Unknown Artist on Wishlist Downloads', desc: 'Adding tracks to wishlist from a playlist download modal was storing "Unknown Artist" as the artist context. Now resolves the artist per-track from the track\'s own metadata instead of the playlist-level artist which is only set for album downloads' },
        { title: 'Fix Download Modal Freezing Mid-Download', desc: 'Download modals (wishlist, sync, album) would freeze and stop updating after the first track completed. Caused by M3U auto-save firing every 2 seconds during downloads, exhausting Flask server threads. Now saves M3U once on completion only' },
        { title: 'Auto-Import Improvements', desc: 'Recursive import folder scan (any folder depth), single file support, expandable track match details, stats bar with filters, Scan Now button, Approve All / Clear History batch actions. Tag-based identification preferred over weak metadata matches. AcoustID fallback for untagged files. Race condition fix prevents duplicate processing', page: 'import' },
        { title: 'Album Delete with File Removal', desc: 'Enhanced library album delete now offers "Delete Files Too" option alongside "Remove from Library" — deletes audio files from disk and cleans up empty album folders', page: 'library' },

        // --- April 15, 2026 ---
        { date: 'April 15, 2026' },
        { title: 'Dashboard Library Status Card', desc: 'Smart card on the Dashboard showing your library state — server connection, track counts, last refresh time. Guides new users through setup, shows empty-library prompts, and lets you trigger a scan directly from the dashboard', page: 'dashboard' },
        { title: 'AcoustID Scanner Upgrade', desc: 'Now scans your full library (not just Transfer) to detect wrong downloads. Actionable fixes: retag with correct metadata, re-download the right track, or delete the wrong file. Enabled by default, runs daily' },
        { title: 'Tools Page', desc: 'All tool cards (Database Updater, Quality Scanner, Duplicate Cleaner, Retag, Backups, Cache, etc.) and Library Maintenance moved from the Dashboard to a dedicated Tools page in the sidebar. Dashboard shows a quick-link card', page: 'tools' },
        { title: 'Watchlist & Wishlist Sidebar Pages', desc: 'Watchlist and Wishlist promoted from modals to full sidebar pages. All features preserved — artist grid, scan controls, batch operations, live activity, countdown timers. Header buttons now navigate to the pages', page: 'watchlist' },
        { title: 'Picard-Style MusicBrainz Album Consistency', desc: 'Recording MBIDs now pulled from the matched release tracklist instead of independent searches. Batch-level artist name used for stable cache keys. Post-batch consistency pass rewrites album-level tags on all files to guarantee identical MusicBrainz IDs — prevents Navidrome album splits' },
        { title: 'Fix Spotify API Leaking When Deezer/iTunes is Primary', desc: 'Spotify was being called for watchlist album scanning, similar artist discovery, repair jobs, and the Artists page search even when another source was set as primary. All data-fetching now respects the configured primary source. Spotify playlist sync is unaffected' },
        { title: 'Fix OAuth Callback Port Hardcoding', desc: 'Custom callback ports (SOULSYNC_SPOTIFY_CALLBACK_PORT / SOULSYNC_TIDAL_CALLBACK_PORT) are now respected in auth instruction pages and log messages instead of always showing 8888. Added startup diagnostics logging for callback port binding' },
        { title: 'Fix Allow Duplicates Setting Not Saving', desc: 'The "Allow duplicate tracks across albums" toggle was never persisted — it silently reset to ON on every page reload. Now saves correctly' },
        { title: 'Fix Wishlist Dropping Cross-Album Tracks', desc: 'Wishlist cleanup was removing same-titled tracks from different albums even when Allow Duplicates was enabled. Cleanup now respects the setting — same song from different albums can coexist in the wishlist' },
        { title: 'Fix "Replace Lower Quality" Setting Not Persisting', desc: 'The import section appeared twice in the settings save payload — the second instance (with only staging_path) overwrote the first (with replace_lower_quality). Merged into a single block' },
        { title: 'Inbound Music Request API', desc: 'New POST /api/v1/request endpoint — trigger downloads from Discord bots, Home Assistant, curl, or any external tool. Async with status polling and optional notify_url callback. New "Webhook Received" automation trigger and "Search & Download" action in the Automation Hub' },
        { title: 'Fix Spotify Enrichment Worker Infinite Loop', desc: 'Artists with an existing Spotify ID but no match status got stuck in the enrichment queue — the worker processed them every 3 seconds forever without marking them as done. Now correctly marks them as matched' },
        { title: 'Reject Qobuz 30-Second Samples', desc: 'Qobuz previews (30s samples for tracks requiring a subscription or region-restricted) are now detected and rejected. Checks the API sample flag before downloading, and validates file duration after download as a safety net' },

        // --- April 14, 2026 ---
        { date: 'April 14, 2026' },
        { title: 'Fix Import Files Ignoring Path Template',        desc: 'Files matched from the import folder were copied to the output root with their original filename instead of applying the configured path template. Post-processing now receives full artist/album context for import matches' },

        // --- April 4, 2026 ---
        { date: 'April 4, 2026' },
        { title: 'Artist Map — Visualize Your Music Universe',       desc: 'Three interactive canvas modes: Watchlist Constellation (your artists + similar), Genre Map (browse by genre with sidebar), and Artist Explorer (deep-dive any artist). Offscreen buffer rendering handles 1000+ nodes', page: 'discover' },
        { title: 'Artist Explorer — On-the-Fly Discovery',          desc: 'Explore any artist even if not in your library — fetches similar artists from MusicMap in real-time, stores results for instant future visits. Invalid names validated against Spotify/iTunes', page: 'discover' },
        { title: 'Genre Map — Full Artist Counts',                   desc: 'Genre map now shows all artists per genre (no caps). Ring packing layout handles large genres instantly. Genre sidebar for quick switching', page: 'discover' },
        { title: 'Artist Map Caching',                               desc: 'Server-side 5-minute cache on all artist map endpoints — switching genres and reopening maps is instant. Auto-invalidates on watchlist changes and scans' },
        { title: 'Image Proxy for Canvas Rendering',                 desc: 'Server-side image proxy solves CORS issues for canvas — Deezer, Last.fm, and Discogs images now render on Artist Map bubbles' },

        // --- April 3, 2026 ---
        { date: 'April 3, 2026' },
        { title: 'Your Artists on Discover',                         desc: 'Aggregates liked/followed artists from Spotify, Tidal, Last.fm, and Deezer. Auto-matched to all metadata sources. Click for artist info modal with bio, genres, stats, and watchlist toggle', page: 'discover' },
        { title: 'Deezer OAuth',                                     desc: 'Full Deezer OAuth integration for user favorites and playlists. Configure in Settings → Connections' },
        { title: 'Failed MB Lookups Manager',                        desc: 'Browse, search, and manually match failed MusicBrainz lookups from the Cache Health modal. Search MusicBrainz directly and save matches' },
        { title: 'Explorer Controls Redesign',                       desc: 'Playlist Explorer controls redesigned with prominent Explore button, icons, status badges, auto-refresh, and discover from Explorer', page: 'playlist-explorer' },
        { title: '$discnum Template Variable',                       desc: 'Unpadded disc number for multi-disc album path templates — e.g. Disc 1, Disc 2' },
        { title: 'Fix Album Folder Splitting',                       desc: 'Collab albums no longer scatter tracks across multiple folders — $albumartist uses album-level artist consistently' },
        { title: 'Fix Watchlist Rate Limiting',                      desc: 'Watchlist scans fetch only newest albums (~90% fewer API calls). Configurable API interval. Better Retry-After extraction' },
        { title: 'Fix Media Player Collapsing',                      desc: 'Media player no longer collapses in the sidebar on short viewports and mobile devices' },

        // --- April 2, 2026 ---
        { date: 'April 2, 2026' },
        { title: 'Discogs Integration',                              desc: 'New metadata source — enrichment worker, fallback source, enhanced search tab, watchlist support, cache browser. 400+ genre/style taxonomy', page: 'dashboard' },
        { title: 'Webhook THEN Action',                              desc: 'Send HTTP POST to any URL when automations complete — Gotify, Home Assistant, Slack, n8n', page: 'automations' },
        { title: 'API Rate Monitor',                               desc: 'Real-time speedometer gauges for all enrichment services on Dashboard. Click any gauge for 24h history', page: 'dashboard' },
        { title: 'Configurable Concurrent Downloads',             desc: 'Set max simultaneous downloads (1-10) in Settings. Soulseek albums stay at 1 for source reuse' },
        { title: 'Streaming Search Sources',                      desc: 'Apple Music results stream progressively instead of blocking for 9+ seconds' },
        { title: 'Track Provenance Through Transcoding',          desc: 'Download source info preserved when Blasphemy Mode converts FLAC to lossy (#245)' },

        // --- April 1, 2026 ---
        { date: 'April 1, 2026' },
        { title: 'Wing It Mode',                                desc: 'Download or sync playlists without metadata discovery — uses raw track names directly' },
        { title: 'Global Search Bar',                             desc: 'Spotlight-style search from any page — press / or Ctrl+K. Full enhanced search with source tabs', page: 'downloads' },
        { title: 'Redesigned Notifications',                    desc: 'Compact pill toasts, notification bell with unread badge, history panel with last 50 notifications' },
        { title: 'Track Redownload & Source Info',              desc: 'Fix mismatched downloads from the enhanced library view. Source Info shows download provenance with blacklist option', page: 'library' },
        { title: 'Block Artists from Discovery',                  desc: 'Permanently exclude artists from all discovery playlists — hover any track and click ✕', page: 'discover' },
        { title: 'MusicBrainz Cache in Browser',                 desc: 'MusicBrainz cache now visible in Cache Browser with clear and clear-failed-only options' },

        // --- Earlier in v2.2 ---
        { date: 'March 2026' },
        { title: 'Server Playlist Manager',                   desc: 'Compare source playlists against your media server — find missing tracks, swap wrong matches, remove extras', page: 'sync' },
        { title: 'Sync History Dashboard',                    desc: 'Recent syncs as cards on Dashboard — click for per-track match details with confidence scores' },
        { title: 'Playlist Explorer',                         desc: 'Expand playlists into visual discovery trees of albums and discographies', page: 'playlist-explorer' },
        { title: 'Enhanced Library Manager',                   desc: 'Inline tag editing, bulk operations, write-to-file, and per-artist library sync', page: 'library' },
        { title: 'Automation Signals',                         desc: 'Chain automations together using fire/receive signals with cycle detection', page: 'automations' },
        { title: 'Multi-Source Search Tabs',                   desc: 'Compare results from Spotify, iTunes, and Deezer side by side', page: 'downloads' },
        { title: 'Rich Artist Profiles',                      desc: 'Full-bleed hero section with bio, stats, genres, and service links', page: 'artists' },
        { title: 'Spotify API Rate Limit Improvements',       desc: 'Cached discography lookups, eliminated duplicate calls, enrichment workers auto-pause during downloads' },
    ],
};

// ═══════════════════════════════════════════════════════════════════════════
// VERSION MODAL — curated highlight reel
// ═══════════════════════════════════════════════════════════════════════════
//
// `WHATS_NEW` above is the per-version detailed log used by the "What's New"
// helper-popover panel — short one-liners, internal page links, every entry
// shown on every browse-back through versions.
//
// `VERSION_MODAL_SECTIONS` (this block) is the curated highlight reel shown
// when the user clicks the version button in the sidebar. It's NOT a
// mechanical view of WHATS_NEW — it's editorial curation: bigger-picture
// sections, bullet-list expansions, optional "usage" hints at the bottom.
// Some sections aggregate across multiple WHATS_NEW entries ("Recent Fixes",
// "Earlier in v2.3"); some don't have a 1:1 WHATS_NEW counterpart at all.
//
// Both consts live here so a release editor only opens one file. At release
// time:
//   1. Add the per-version block to `WHATS_NEW` (one entry per shipped item).
//   2. Promote any items worth a modal-section into `VERSION_MODAL_SECTIONS`
//      at the top of the array (latest highlights lead).
//   3. Roll older sections down or merge them into a "Recent Fixes" /
//      "Earlier in vX.Y" aggregator section as they age out of the spotlight.
//
// Section shape: { title, description, features: [bullet strings],
//                  usage_note?: 'optional hint shown at the bottom' }
const VERSION_MODAL_SECTIONS = [
    {
        title: "Big Sync Sessions No Longer Wedge After 2-3 Hours",
        description: "github issue #499 (bafoed): downloading a big initial sync from spotify playlists worked for 2-3 hours then silently stopped. 3 active tasks stuck in \"searching\" state, replaced every ~10 min, slskd ui showed no actual activity. only fix was restarting the container.",
        features: [
            "• root cause: `aiohttp.ClientSession()` was constructed with no timeout — when slskd hung (overloaded / network blip / internal stall), the http call blocked forever and the worker thread blocked with it",
            "• download executor only has 3 worker threads — once all 3 wedged on hung calls, no further downloads could start",
            "• fix: bounded `aiohttp.ClientTimeout` (total 120s, connect 15s, sock_read 60s) on every slskd session — slskd metadata calls finish in seconds, so the timeout can\'t kill a real operation",
            "• timeout fires → caught + logged + return None → caller treats as a normal failure (same code path as a 5xx response) → worker thread unblocks → executor stays healthy",
            "• 3 new pinning tests on the timeout config + handler so future drift fails at the test boundary, not against a wedged executor in production",
        ],
        usage_note: "no settings to change — applies on next container restart",
    },
    {
        title: "Library Reorganize No Longer Mistakes Album Tracks for Singles",
        description: "github issue #500 (bafoed): library reorganize repair job was moving album tracks like `01 - Christine F.flac` to single-template paths because of a fragile classification heuristic.",
        features: [
            "• pre-rewrite the job had its own tag-reading + transfer-folder walk + template logic — used `is_album = (group_size > 1)` where group_size was the count of same-album tracks in the transfer folder being scanned",
            "• when only one track of an album sat in transfer (rest already moved, or album tags varied slightly like \"Buds\" vs \"Buds (Bonus)\") → group size 1 → routed to single template → wrong destination",
            "• fix: delegate to the per-album planner the artist-detail \"reorganize\" modal already uses — db-driven, knows the album has n tracks regardless of how many currently sit in transfer",
            "• only iterates albums on the ACTIVE media server (matches what the artist-detail modal sees) — multi-server users (plex + jellyfin etc) won\'t accidentally have the job touch the inactive server\'s files",
            "• apply mode dispatches to the existing reorganize queue → one code path for file move + post-processing + db update + sidecar",
            "• albums missing a metadata source id get a single \"needs enrichment first\" finding instead of n per-track \"no source\" findings cluttering the ui",
            "• dropped ~500 loc that was duplicated against the per-album logic — files in transfer with no db entry are now exclusively the orphan file detector\'s domain",
        ],
        usage_note: "no settings to change — applies on next library reorganize repair job run",
    },
    {
        title: "Enrich Now Honors Manual Album Matches",
        description: "github issue #501 (tacobell444): manually matching an album then clicking enrich would overwrite your manual match with whatever the worker\'s name-search returned, or revert status to \"not found\". reorganize then read the wrong id and moved files to the wrong destination.",
        features: [
            "• every per-source enrichment worker (spotify / itunes / deezer / tidal / qobuz) now reads its stored id column at the top of `_process_*_individual` — if present, fetch directly via that id and refresh metadata without touching the id",
            "• fuzzy name search only runs as fallback for entities that have never been matched",
            "• discogs / audiodb / musicbrainz already had inline stored-id fast paths and are left alone — same correct behavior, just inline",
            "• lastfm / genius are name-based and don\'t store ids — no-op for those",
            "• cin-shape lift: same fix in 5 workers gets exactly one shared helper at `core/enrichment/manual_match_honoring.py`, per-worker variability (column name / client method / response shape) plugs in via callbacks",
            "• reorganize fixed indirectly — it always honored stored ids correctly, the bug was upstream in enrich corrupting the id",
        ],
        usage_note: "no settings to change — applies on next click of enrich on a manually-matched album or track",
    },
    {
        title: "HiFi Instance Add No Longer Errors With \"no such table\"",
        description: "github issue #503 (hadshaw21): adding a hifi instance from downloader settings popped up `no such table: hifi_instances` even when the connection test passed.",
        features: [
            "• root cause: the bulk db init runs every CREATE TABLE + every migration step inside one sqlite transaction — python\'s sqlite3 module doesn\'t autocommit DDL, so if any later migration throws on your DB shape, the WHOLE batch rolls back including the hifi_instances create that ran successfully",
            "• fix: defensive lazy-create — every hifi_instances CRUD method now runs `CREATE TABLE IF NOT EXISTS` right before its operation",
            "• idempotent — one no-op cost when the table is already there, fully self-heals when it isn\'t",
            "• read methods return empty instead of raising; write methods work end-to-end",
            "• doesn\'t paper over the underlying init issue (still worth tracking which migration breaks for which users) but makes hifi instance management work regardless",
        ],
        usage_note: "no settings to change — applies on next click of \"add\" in hifi instance settings",
    },
    {
        title: "Plex: Combine All Music Libraries Into One",
        description: "github issue #505 (popebruhlxix): users with multiple plex music libraries (e.g. one per plex home user) only saw one library inside soulsync because settings forced you to pick a single library section.",
        features: [
            "• new \"all libraries (combined)\" option in settings → connections → plex → music library dropdown — only shows up when your server has more than one music library",
            "• picking it flips the plex client into server-wide read mode — every scan / search / library-stat call dispatches through `server.library.search()` instead of querying a single section",
            "• one api call, plex handles the aggregation — no per-section iteration code on our side",
            "• cross-section dedup at the listing layer — same-name artists across sections (e.g. plex home families that both have drake) collapse to one canonical entry in your library list, so no more visual duplicates",
            "• removal detection stays on raw ratingKeys — deduping there would falsely prune tracks linked to non-canonical entries",
            "• write methods (genre / poster / metadata updates) and playlists are unaffected — section-agnostic, operate on plex objects via ratingKey",
            "• trigger_library_scan + is_library_scanning fan out across every music section in the new mode",
            "• backward compatible — existing users with a single library saved see no behavior change",
        ],
        usage_note: "settings → connections → plex → music library → pick \"all libraries (combined)\"",
    },
    {
        title: "Download Discography No Longer Shows Wrong Artist",
        description: "clicked download discog on 50 cent → modal showed young hot rod\'s albums. clicked weird al → modal showed beatles albums. real bug, not just data weirdness.",
        features: [
            "• endpoint received whichever single artist id the frontend happened to pick and dispatched it as-is to whichever source it queried — when the picked id didn\'t match the queried source\'s id format, lookup either returned wrong-artist results (numeric id collisions) or fell back to a fuzzy name search that picked a different artist",
            "• fixed: backend now looks up the library row by ANY stored id (db id, spotify, itunes, deezer, musicbrainz) and dispatches the correct stored id to each source — every source gets its OWN id regardless of what the frontend chose to send",
            "• mechanism already existed (`MetadataLookupOptions.artist_source_ids`) and the watchlist scanner already used it — discog endpoint just wasn\'t wired to it",
            "• also fixed two log-namespace bugs: enhance quality + multi-source search were writing to a logger with no handlers, so every diagnostic line was silently dropped — now lands in app.log where you can actually see them",
        ],
        usage_note: "no settings to change — applies on next click of download discography",
    },
    {
        title: "Enhance Quality Now Behaves Like Download Discography",
        description: "discord report — clicking enhance quality on an artist with no spotify/deezer was adding tracks as \"unknown artist - unknown album - unknown track\". root issue ran deeper than that single edge case.",
        features: [
            "• enhance used to fuzzy text search the configured primary source only — a single-source itunes fallback returning junk matches with empty fields, while track redownload had been doing parallel multi-source search the whole time",
            "• extracted that search into a shared module; both enhance and redownload now hit every configured source in parallel and pick the cross-source best match",
            "• bigger fix: enhance now uses stored source IDs (spotify_track_id / deezer_id / itunes_track_id / soul_id) the same way download discography uses album IDs — direct lookup against each source's API, no fuzzy text matching, no failures from messy tags like \"Title (Live)\" or featured artists in the artist field",
            "• preferred source (your configured primary) tried first so a deezer-primary user gets deezer payloads on the wishlist entry",
            "• text search is only the fallback now — kicks in only when no stored IDs exist for the track",
            "• modal toast no longer lies \"matching tracks to spotify\" regardless of which sources are actually configured",
        ],
        usage_note: "no settings to change — applies on next click of enhance quality",
    },
    {
        title: "Watchlist No Longer Re-Downloads Compilations",
        description: "compilation / soundtrack tracks were getting redownloaded on every watchlist scan because the album-name fuzzy check failed on naming drift between spotify and your media server.",
        features: [
            "• example: spotify says \"napoleon dynamite (music from the motion picture)\", navidrome says \"napoleon dynamite ost\" — old check scored 0.49, redownloaded daily",
            "• now strips qualifier parentheticals (music from..., ost, deluxe edition, remastered, anniversary, etc.) before comparing",
            "• volume / disc / part guard so vol 1 vs vol 2 still count as different",
            "• one user reported the same song downloaded 7 times — fix kills the loop",
        ],
        usage_note: "no settings to change — applies on next watchlist scan",
    },
    {
        title: "Duplicate Detector + Cleanup for slskd Dedup Orphans",
        description: "two-step fix for the dupe accumulation problem — stop new orphans from being created, and catch the existing ones.",
        features: [
            "• new cleanup pass after every successful import scans the source directory for slskd \"_<timestamp>\" siblings of the canonical file and deletes them",
            "• duplicate detector got a new second pass that re-buckets leftover tracks by canonical filename stem so dedup orphans get caught even when the media-server scan parsed inconsistent titles for them",
            "• safety net: if both rows have a duration must agree within 3s, otherwise relaxed artist check, otherwise skip",
            "• existing same-physical-file guard still runs so bind-mount setups (plex + soulsync sharing a folder) aren\'t flagged",
            "• also: same-physical-file dupe filter ships independently — bind-mounted setups stop seeing every file flagged twice",
        ],
    },
    {
        title: "Spotify Auth Flow Reworked",
        description: "rewrote the spotify connection flow on settings → connections so the state is honest about itself.",
        features: [
            "• explicit \"needs auth\" / \"connecting\" / \"connected\" states with consistent labels",
            "• fixed completion-sync race where the page said connected before the token finished saving",
            "• auth-completion failures surface as toasts instead of silent fails",
            "• service status reads simplified — fewer ways for the UI to drift from reality",
            "• spotify enrichment worker now pauses when spotify isn\'t your primary source (was burning api budget regardless)",
            "• per-day spotify call budget cut to 500 to bound accidental quota burns",
        ],
    },
    {
        title: "Match Engine: Featured Artists + Soundtracks",
        description: "two long-standing gaps in the matching logic that caused false \"missing\" verdicts.",
        features: [
            "• featured-artist tracks now match across discography completion checks — a guest spot on someone else\'s track no longer reports as missing for the watched artist",
            "• OST / compilation tracks now match against the per-track artist credit instead of the album\'s primary (which was usually \"Various Artists\")",
            "• fixed a dead fallback path that used to silently swallow these missed matches",
        ],
    },
    {
        title: "Beatport Tab Hidden Temporarily",
        description: "beatport rolled out cloudflare turnstile on every public page and locked their official api behind partner registration that isn\'t open to the public.",
        features: [
            "• every /api/beatport/* call was 500ing because the scraper got a bot challenge instead of html",
            "• tested both curl_cffi (chrome131 impersonate) and cloudscraper — both fail",
            "• tab hidden on sync, backend endpoints kept in code so revival is one html change",
            "• will revisit when beatport relaxes cf or a workaround surfaces",
        ],
    },
    {
        title: "Provider-Neutral Wishlist + Quality Scanner",
        description: "two more spots that hardcoded spotify even when you had a different primary source configured.",
        features: [
            "• wishlist UI labels, retry copy, and source defaults now mirror your active primary source",
            "• quality scanner refactored to query the configured primary instead of always spotify — no more leaked api calls and discogs / hydrabase data finally gets used",
            "• artwork preserved on quality-scanner → wishlist handoff",
            "• bulk watchlist add now falls back through every cached source ID before declaring failure (no more dead adds when one source rate-limits)",
        ],
    },
    {
        title: "Parallel Singles Import (3 Workers)",
        description: "long backlogs of liked-songs single imports finish ~3x faster.",
        features: [
            "• singles / EP imports run through a 3-worker thread pool instead of serial",
            "• singles + EPs now route through the album_path template so they file correctly (was using a different code path that drifted out of date)",
        ],
    },
    {
        title: "Service Worker for Cover Art + Installable PWA",
        description: "cover art now caches locally and soulsync installs as a standalone app.",
        features: [
            "• service worker caches cover art on disk — second visit to any page serves art instantly, no network round trip",
            "• PWA manifest added — chrome / edge / safari → install soulsync makes it a standalone app on your home screen / desktop",
            "• cache versioned so future strategy changes invalidate cleanly",
            "• also: static assets (js / css / icons) cache 1 year browser-side; discover pages cache 5 minutes — fewer round trips, faster repeat loads",
        ],
    },
    {
        title: "Security Tightenings",
        description: "two endpoint hardenings.",
        features: [
            "• socket.io now defaults to same-origin only (was cors=*) — if your websocket fails, server logs the rejected origin so you can add it to settings → security → allowed websocket origins",
            "• /api/settings endpoints (read, write, log-level, config-status, verify) are now admin-only — single-admin setups work transparently",
        ],
    },
    {
        title: "Bug Fix Round-Up",
        description: "smaller fixes that landed during the cycle.",
        features: [
            "• #434 — config DB lock spam on slow disks, fixed with bounded retry + exponential backoff",
            "• #399 — bulk discography losing album source context as it threaded through the pipeline",
            "• tidal auth instructions now show tidal\'s callback port (was showing spotify\'s)",
            "• discogs primary source gracefully reverts when no token is configured",
            "• automation handler-returned errors now surface in last_error instead of being swallowed",
            "• wishlist track counts coerced before category gating so mixed-type values don\'t crash",
            "• faster docker startup — yt-dlp pinned in requirements.txt instead of pip-installed on every container start",
            "• shutdown-time logger noise silenced so CI stderr stops carrying \"I/O on closed file\" tracebacks",
        ],
    },
    {
        title: "Major Internal: web_server.py Decomposition",
        description: "internal — large monolith broken up into focused modules under core/. behavior unchanged, but the codebase is meaningfully more testable and easier to navigate.",
        features: [
            "• ~30 routes / workers / helpers lifted out of web_server.py into core/search, core/automation, core/stats, core/discovery, core/library, core/downloads, core/workers, core/artists, core/imports, core/watchlist, core/connection, core/debug",
            "• metadata helpers reorganized into core/metadata/ package; profile spotify cache lives in registry now",
            "• search endpoints lift: 612 fewer lines in web_server.py, 94 new tests",
            "• automation endpoints lift: 383 fewer lines in web_server.py, 72 new tests",
            "• step-by-step toward retiring the monolith, no behavior change in any individual lift",
        ],
    },
    {
        title: "Earlier in v2.4 — Reorganize, Search, Sync polish",
        description: "highlights from the 2.4.0 cycle that landed before this patch.",
        features: [
            "• reorganize is now a queue with a live status panel — spam-click all you want, items run one at a time and you can keep browsing",
            "• search page got a row of source icons above the bar — typing only searches the active source instead of fanning out to all of them",
            "• per-query source cache + cache dots — switching back to a source you already searched is instant",
            "• fix: \"maduk — leave a light on\" on tidal was downloading tom walker\'s song of the same name with maduk\'s metadata embedded — tightened the candidate artist gate and acoustid verification",
            "• tidal: rejects silent quality downgrades (320kbps when you asked for hires)",
            "• spotify: bumped post-ban cooldown from 5 to 30 minutes — first call after a ban was getting re-banned within seconds",
        ],
    },
    {
        title: "Reorganize Queue Polish",
        description: "cleaned up some race conditions in the queue. behavior is solid now.",
        features: [
            "• worker pick + status flip is atomic now — cancel can\'t land between them and let a cancelled item still run",
            "• swapped lock + wakeup-event for a single threading.Condition — newly-queued items don\'t sleep up to 60s anymore",
            "• bulk enqueue dedupes within a single batch (was only deduping against pre-existing items)",
            "• reorganize-preview Apply button no longer gets stuck disabled on errors",
            "• db helpers let exceptions bubble instead of swallowing them as \"album not found\"",
        ],
    },
    {
        title: "Reorganize Queue with Live Status Panel",
        description: "reorganize is now a queue with a live status panel. spam-click all you want — items run one at a time and you can keep browsing.",
        features: [
            "• per-album reorganize and reorganize all both enqueue into a single backend queue",
            "• buttons stay clickable — clicking the same album twice silently dedupes",
            "• status panel shows active progress, queued count, and recent finishes",
            "• expand the panel for the full queue + per-item cancel buttons (running items can\'t be cancelled mid-flight)",
            "• cross-artist items get tagged so you know what\'s queued from where",
            "• continue-on-failure: one bad album never stalls the queue",
            "• reorganize all is now one backend call instead of N js-driven calls — way faster",
        ],
    },
    {
        title: "Fix Wrong-Artist Tracks Silently Downloading",
        description: "searching for a track could silently download a completely different artist\'s song with the same name. fixed at two layers.",
        features: [
            "• example: \"maduk — leave a light on\" on tidal was downloading tom walker\'s song of the same name with maduk\'s metadata embedded",
            "• tightened the candidate artist gate (was letting through 0.4 similarity, now blocks at 0.5)",
            "• acoustid verification now FAILs (quarantines) clear artist mismatches instead of accepting them",
            "• ambiguous matches (covers, collabs) still get the benefit of the doubt — only obvious mismatches get blocked",
        ],
    },
    {
        title: "Tidal Search Falls Back on Long Queries",
        description: "tidal\'s search chokes on long remix-credit queries. now retries with shorter variants when the original returns 0 results.",
        features: [
            "• example: \"maduk transformations remixed fire away fred v remix\" returned 0 — falls back to shorter queries until tidal finds the track",
            "• up to 4 shortened variants tried, capped at 5 total requests",
            "• qualifier-safe: live/remix/acoustic searches only accept fallback results that keep the qualifier",
            "• returns empty if no variant preserves the qualifiers — same as before",
        ],
    },
    {
        title: "Manual Discovery Fixes Persist Across Restart",
        description: "manual discovery fixes are now saved under your active metadata source instead of always \"spotify\" — so deezer / itunes / discogs / hydrabase users\' fixes survive restart.",
        features: [
            "• affects tidal, deezer, spotify public, youtube, and discovery pool manual fixes",
            "• matches how the auto-discovery worker already saved",
            "• spotify-primary users unaffected (hardcoded value matched their source)",
        ],
    },
    {
        title: "Watchlist Content Filters Fixed",
        description: "global override and live-version detection now behave the way the ui implies.",
        features: [
            "• scheduled auto-watchlist honors watchlist → global override (was bypassing it)",
            "• live detection tightened — no more false positives on titles like \"what we live for\"",
            "• same fix applies to the library maintenance live/commentary cleaner",
            "• still catches (live), - live, live at/from/in/on, unplugged, in concert",
        ],
    },
    {
        title: "Discography Backfill",
        description: "new maintenance job that scans each artist\'s full discography and finds what you\'re missing.",
        features: [
            "• scans each library artist against your metadata source",
            "• creates findings for missing tracks — review and add to wishlist",
            "• respects all content filters (live, remix, acoustic, etc.) and release type filters",
            "• optional auto-add-to-wishlist setting for hands-off operation",
            "• opt-in, runs weekly, processes up to 50 artists per run",
        ],
    },
    {
        title: "Repair 'Run Now' Honored While Paused",
        description: "force-running a repair job no longer stalls forever when the master worker is paused.",
        features: [
            "• jobs queued via run now complete even if the master worker is paused",
            "• fixes silent stalls where the job logged \"scanning 50 artists\" then did nothing",
            "• master-pause still blocks scheduled runs — only affects user-triggered runs",
        ],
    },
    {
        title: "Multi-Artist Tagging",
        description: "more control over how multiple artists are written to audio file tags.",
        features: [
            "• configurable separator: comma, semicolon, or slash",
            "• multi-value ARTISTS tag for navidrome / jellyfin multi-artist linking",
            "• \"move featured artists to title\" mode — primary in ARTIST tag, others as (feat. ...) in title",
            "• opt-in, defaults match current behavior",
        ],
    },
    {
        title: "Enriched Downloads Page",
        description: "download cards now show rich metadata instead of just filenames.",
        features: [
            "• album artwork thumbnail on each card",
            "• artist name, album name, source badge",
            "• quality badge appears after post-processing",
            "• falls back gracefully for transfers without metadata context",
        ],
    },
    {
        title: "Template Variable Delimiters",
        description: "use ${var} syntax to append literal text to template variables.",
        features: [
            "• ${albumtype}s produces \"Albums\", \"Singles\", \"EPs\"",
            "• both $var and ${var} syntaxes work everywhere",
            "• validation updated to accept delimited variables",
        ],
    },
    {
        title: "Reorganize All Albums",
        description: "bulk reorganize all albums for an artist from the enhanced library view.",
        features: [
            "• new reorganize all button in the artist header",
            "• processes sequentially with progress toasts",
            "• continues on error — one failed album doesn\'t block the rest",
            "• uses the same template + endpoint as per-album reorganize",
        ],
    },
    {
        title: "SoulSync Standalone Library",
        description: "use soulsync without plex, jellyfin, or navidrome — manage your library directly.",
        features: [
            "• new standalone server option in settings → connections",
            "• downloads and imports write to the library db immediately",
            "• pre-populated enrichment ids — workers skip re-discovery",
            "• deep scan finds untracked files and removes stale db records",
            "• sync page hidden automatically in standalone mode",
            "• full library / artist detail / discography all work standalone",
        ],
        usage_note: "settings → connections → standalone. no media server needed.",
    },
    {
        title: "Auto-Import",
        description: "background folder watcher that automatically identifies and imports music into your library.",
        features: [
            "• recursive scan — any folder depth (artist/album/tracks, loose files, whatever)",
            "• tag-based identification preferred, acoustid fingerprinting as fallback",
            "• stats bar, filter pills, scan now, approve all, clear history",
            "• expandable per-track match details with confidence scores",
            "• race condition fix prevents duplicate processing on multi-track albums",
        ],
        usage_note: "import page → auto tab. set your import folder in settings.",
    },
    {
        title: "Wishlist Nebula",
        description: "wishlist redesigned as an interactive artist orb visualization.",
        features: [
            "• each artist is a glowing orb — albums and singles orbit around it",
            "• click orbs to expand and download directly from the nebula",
            "• live progress with spinning ring animation while processing",
            "• stats strip up top: total artists, albums, singles, tracks",
        ],
        usage_note: "click wishlist in the sidebar.",
    },
    {
        title: "Automation Group Management",
        description: "organize and manage automation groups properly.",
        features: [
            "• rename, delete, and bulk-toggle groups from the group header",
            "• drag-and-drop automations between groups",
            "• delete confirmation shows group name and automation count",
        ],
        usage_note: "use the action buttons on group headers in the automations page.",
    },
    {
        title: "Bidirectional Artist Sync & Server Playlists",
        description: "artist sync now goes both ways, and server playlists show full coverage.",
        features: [
            "• artist sync pulls new content from your media server AND removes stale library entries",
            "• deep scan mode fetches full metadata for newly-discovered tracks",
            "• server playlist view shows all playlists with synced vs unsynced visual separation",
        ],
    },
    {
        title: "Provider-Agnostic Discovery",
        description: "discovery features work with any configured metadata source instead of requiring spotify.",
        features: [
            "• similar artist matching, discovery pool, and incremental updates use source priority",
            "• falls back through spotify, itunes, deezer in configured order",
            "• musicmap url encoding fixed for artists with special characters",
            "• freshness check simplified to age-based",
        ],
    },
    {
        title: "Dashboard & Navigation",
        description: "dashboard improvements and sidebar navigation enhancements.",
        features: [
            "• library status card on dashboard — server state, track counts, scan buttons",
            "• tools page in sidebar — maintenance tools moved out of the dashboard modal",
            "• watchlist and wishlist promoted to full sidebar pages with live count badges",
            "• acoustid scanner scans full library with retag / redownload / delete fix options",
        ],
    },
    {
        title: "MusicBrainz & Metadata Fixes",
        description: "critical tag embedding fix and picard-style album consistency.",
        features: [
            "• source id tags (spotify, musicbrainz, deezer, audiodb) were silently skipped on every download — now embed correctly",
            "• picard-style release preference scoring prevents navidrome album splits",
            "• source tags wiped when metadata enhancement is skipped or fails",
            "• spotify api no longer called when deezer/itunes is your primary source",
        ],
    },
    {
        title: "Downloads & Soulseek Improvements",
        description: "better download management, search accuracy, and queue control.",
        features: [
            "• downloads batch panel — color-coded cards with progress, cancel, expand, 7-day history",
            "• soulseek queries include album name now — fewer wrong-artist downloads",
            "• reject results from various artists / unknown artist folders",
            "• clearing wishlist cancels the active wishlist download batch",
            "• album delete with \"delete files too\" option on enhanced library",
            "• fix download modal freezing mid-download (m3u auto-save was exhausting server threads)",
            "• fix unknown artist when adding playlist tracks to wishlist",
        ],
    },
    {
        title: "Recent Fixes",
        description: "smaller bug fixes from recent releases and community reports.",
        features: [
            "• fix watchlist scan false failures — empty discography no longer reported as error",
            "• fix deezer_artist_id column error on enhanced library sync",
            "• fix wishlist button intermittently not navigating",
            "• fix worker orb tooltips rendering behind dashboard content",
            "• fix oauth callback port hardcoding — custom ports respected now",
            "• fix allow duplicates and replace-lower-quality settings not saving",
            "• fix wishlist dropping cross-album tracks when duplicates enabled",
            "• fix spotify enrichment worker infinite loop on pre-matched artists",
            "• reject qobuz 30-second sample/preview downloads",
            "• auto wing-it fallback for failed discovery",
            "• fix album track lookup hardcoded to spotify — uses configured primary now",
            "• fix m3u showing all tracks as missing after post-processing",
            "• fix acoustid retag not writing corrected tags to file",
            "• fix downloads badge dropping to 300 after opening downloads page",
            "• unmatch discovery tracks (red ✕ button)",
            "• customizable music video naming with $artist, $title, $year",
            "• fix soulseek log spam when not configured as download source",
        ],
    },
    {
        title: "Earlier in v2.3",
        description: "major features from earlier in this release cycle.",
        features: [
            "• centralized downloads page with live-updating list and filter pills",
            "• first-run setup wizard — 7-step guided configuration",
            "• music videos — search and download from youtube",
            "• inbound music request api for external tools (discord bots, home assistant)",
            "• lidarr download source (in development) for usenet / torrent",
            "• graceful shutdown — all workers respond to shutdown signals immediately",
            "• unknown artist prevention with 3-tier metadata fallback",
            "• deezer multi-artist tagging via contributors field",
            "• artist map — watchlist constellation, genre map, artist explorer",
            "• discogs integration — enrichment worker, fallback source, search tab",
            "• wing it mode, global search bar, redesigned notifications",
            "• server playlist manager, sync history dashboard, playlist explorer",
            "• enhanced library manager with inline tag editing and write-to-file",
            "• automation signals, multi-source search tabs, rich artist profiles",
        ],
    },
];

function _getCurrentVersion() {
    const btn = document.querySelector('.version-button');
    return btn ? btn.textContent.trim().replace('v', '') : '2.4.0';
}

// Compare two semver-ish strings ("2.4.0" vs "2.4.1" vs "2.39"). Returns
// negative if a < b, positive if a > b, 0 if equal. Strips any +sha suffix
// before parsing. Missing components are treated as 0 so "2.4" sorts as
// "2.4.0". Replaces the old parseFloat() approach which collapsed any
// 3-part version to its first two components — making 2.4.0 and 2.4.1
// indistinguishable.
function _compareVersions(a, b) {
    const parse = (s) => String(s || '0').split('+')[0].split('.').map(n => parseInt(n, 10) || 0);
    const pa = parse(a);
    const pb = parse(b);
    const len = Math.max(pa.length, pb.length);
    for (let i = 0; i < len; i++) {
        const diff = (pa[i] || 0) - (pb[i] || 0);
        if (diff !== 0) return diff;
    }
    return 0;
}

function _getLatestWhatsNewVersion() {
    // Only surface entries whose version number is <= the current build. Entries
    // sitting at higher versions are unreleased work-in-progress and shouldn't
    // flag as "new" in the helper badge until the build catches up.
    const buildVer = _getCurrentVersion();
    const versions = Object.keys(WHATS_NEW)
        .filter(v => _compareVersions(v, buildVer) <= 0)
        .sort((a, b) => _compareVersions(b, a));
    return versions[0] || '2.5.2';
}

function openWhatsNew() {
    dismissHelperPopover();
    const latestVersion = _getLatestWhatsNewVersion();
    const notes = WHATS_NEW[latestVersion];

    // Mark as seen
    localStorage.setItem('soulsync_helper_version_seen', latestVersion);
    _updateHelperBadge();

    if (!notes || !notes.length) {
        // Fall back to existing version modal
        exitHelperMode();
        const versionBtn = document.querySelector('.version-button');
        if (versionBtn) versionBtn.click();
        return;
    }

    const panel = document.createElement('div');
    panel.className = 'helper-popover helper-whats-new-panel';
    panel.innerHTML = `
        <div class="helper-popover-header">
            <div class="helper-popover-title">What's New in v${latestVersion}</div>
            <button class="helper-popover-close" onclick="exitHelperMode()">&times;</button>
        </div>
        <div class="helper-whats-new-list">
            ${notes.map(h => {
                if (h.date) return `<div class="helper-whats-new-date">${h.date}</div>`;
                const hasTarget = !!(h.selector || h.page);
                const linkText = h.selector ? 'Show me →' : h.page ? 'Go to page →' : '';
                return `
                <div class="helper-whats-new-item ${hasTarget ? 'clickable' : ''}"
                     ${h.selector ? `data-selector="${h.selector}"` : ''} ${h.page ? `data-page="${h.page}"` : ''}>
                    <div class="helper-whats-new-title">${h.title}</div>
                    <div class="helper-whats-new-desc">${h.desc}</div>
                    ${linkText ? `<span class="helper-whats-new-show">${linkText}</span>` : ''}
                </div>`;
            }).join('')}
        </div>
        <div class="helper-whats-new-footer">
            <button class="helper-tour-btn" onclick="_openFullChangelog()">Full Changelog</button>
            ${Object.keys(WHATS_NEW).length > 1 ? `<button class="helper-tour-btn" onclick="_showOlderNotes()">Older Versions</button>` : ''}
        </div>
    `;

    // "Show me" click handlers
    panel.querySelectorAll('.helper-whats-new-item.clickable').forEach(item => {
        item.addEventListener('click', () => {
            const page = item.getAttribute('data-page');
            const sel = item.getAttribute('data-selector');
            exitHelperMode();
            if (page) navigateToPage(page);
            if (sel) {
                setTimeout(() => {
                    const el = document.querySelector(sel);
                    if (el) {
                        el.scrollIntoView({ behavior: 'smooth', block: 'center' });
                        el.classList.add('helper-highlight');
                        setTimeout(() => el.classList.remove('helper-highlight'), 3000);
                    }
                }, page ? 400 : 50);
            }
        });
    });

    document.body.appendChild(panel);
    _helperPopover = panel;

    const floatBtn = document.getElementById('helper-float-btn');
    if (floatBtn) {
        const btnRect = floatBtn.getBoundingClientRect();
        panel.style.right = (window.innerWidth - btnRect.right) + 'px';
        panel.style.bottom = (window.innerHeight - btnRect.top + 8) + 'px';
        panel.style.left = 'auto';
        panel.style.top = 'auto';
    }
    requestAnimationFrame(() => panel.classList.add('visible'));
}

function _openFullChangelog() {
    exitHelperMode();
    const versionBtn = document.querySelector('.version-button');
    if (versionBtn) versionBtn.click();
}

function _showOlderNotes() {
    // Cycle to next older version in the what's new panel (skip unreleased entries)
    const buildVer = _getCurrentVersion();
    const versions = Object.keys(WHATS_NEW)
        .filter(v => _compareVersions(v, buildVer) <= 0)
        .sort((a, b) => _compareVersions(b, a));
    const panel = _helperPopover;
    if (!panel) return;
    const currentTitle = panel.querySelector('.helper-popover-title');
    const currentVer = currentTitle?.textContent.match(/v([\d.]+)/)?.[1] || versions[0];
    const currentIdx = versions.indexOf(currentVer);
    const nextIdx = (currentIdx + 1) % versions.length;
    const nextVer = versions[nextIdx];

    // Rebuild the list content
    const notes = WHATS_NEW[nextVer];
    if (currentTitle) currentTitle.textContent = `What's New in v${nextVer}`;
    const listEl = panel.querySelector('.helper-whats-new-list');
    if (listEl && notes) {
        listEl.innerHTML = notes.map(h => {
            const hasTarget = !!(h.selector || h.page);
            const linkText = h.selector ? 'Show me →' : h.page ? 'Go to page →' : '';
            return `
            <div class="helper-whats-new-item ${hasTarget ? 'clickable' : ''}"
                 ${h.selector ? `data-selector="${h.selector}"` : ''} ${h.page ? `data-page="${h.page}"` : ''}>
                <div class="helper-whats-new-title">${h.title}</div>
                <div class="helper-whats-new-desc">${h.desc}</div>
                ${linkText ? `<span class="helper-whats-new-show">${linkText}</span>` : ''}
            </div>`;
        }).join('');

        // Rebind click handlers
        listEl.querySelectorAll('.helper-whats-new-item.clickable').forEach(item => {
            item.addEventListener('click', () => {
                const page = item.getAttribute('data-page');
                const sel = item.getAttribute('data-selector');
                exitHelperMode();
                if (page) navigateToPage(page);
                if (sel) {
                    setTimeout(() => {
                        const el = document.querySelector(sel);
                        if (el) {
                            el.scrollIntoView({ behavior: 'smooth', block: 'center' });
                            el.classList.add('helper-highlight');
                            setTimeout(() => el.classList.remove('helper-highlight'), 3000);
                        }
                    }, page ? 400 : 50);
                }
            });
        });
    }
}

function _updateHelperBadge() {
    const floatBtn = document.getElementById('helper-float-btn');
    if (!floatBtn) return;
    const seen = localStorage.getItem('soulsync_helper_version_seen');
    const latest = _getLatestWhatsNewVersion();
    if (seen !== latest) {
        floatBtn.classList.add('has-badge');
    } else {
        floatBtn.classList.remove('has-badge');
    }
}

// ═══════════════════════════════════════════════════════════════════════════
// TROUBLESHOOT MODE (Phase 7)
// ═══════════════════════════════════════════════════════════════════════════

const TROUBLESHOOT_RULES = [
    {
        selector: '#metadata-source-service-card .service-card-indicator.disconnected, #metadata-source-service-card .service-card-indicator.error',
        title: 'Metadata Source Disconnected',
        steps: [
            'Go to Settings → Connections and verify your API credentials',
            'Click "Authenticate" to re-connect to Spotify',
            'If rate limited, wait for the countdown timer to expire',
            'Try switching to iTunes (no authentication required) as a fallback'
        ],
        action: { label: 'Open Settings', fn: () => navigateToPage('settings') }
    },
    {
        selector: '#media-server-service-card .service-card-indicator.disconnected, #media-server-service-card .service-card-indicator.error',
        title: 'Media Server Disconnected',
        steps: [
            'Check that your media server (Plex/Jellyfin/Navidrome) is running',
            'Verify the server URL and API token in Settings → Connections',
            'Ensure the server is accessible from the SoulSync host machine',
            'Try clicking "Test Connection" on the service card'
        ],
        action: { label: 'Open Settings', fn: () => navigateToPage('settings') }
    },
    {
        selector: '#soulseek-service-card .service-card-indicator.disconnected, #soulseek-service-card .service-card-indicator.error',
        title: 'Download Source Disconnected',
        steps: [
            'Verify your Soulseek/download client is running and reachable',
            'Check the API URL and credentials in Settings → Downloads',
            'For streaming sources (Tidal, Qobuz), verify your subscription is active',
            'Try restarting the download client application'
        ],
        action: { label: 'Configure Downloads', fn: () => { navigateToPage('settings'); setTimeout(() => typeof switchSettingsTab === 'function' && switchSettingsTab('downloads'), 400); } }
    },
    {
        selector: '.spotify-rate-limit-modal:not(.hidden), .rate-limit-banner',
        title: 'Spotify Rate Limited',
        steps: [
            'Spotify has temporarily blocked API requests due to too many calls',
            'Wait for the countdown timer to expire — requests auto-resume',
            'Avoid running multiple bulk operations (enrichment + search) simultaneously',
            'Consider switching to iTunes temporarily to continue working'
        ]
    },
];

function activateTroubleshootMode() {
    closeTroubleshootMode();
    _troubleshootActive = true;

    // We need to be on the dashboard to scan service cards
    const currentPage = document.querySelector('.page.active')?.id?.replace('-page', '') || '';
    if (currentPage !== 'dashboard') {
        navigateToPage('dashboard');
        setTimeout(() => _runTroubleshootScan(), 400);
    } else {
        _runTroubleshootScan();
    }
}

function _runTroubleshootScan() {
    const issues = [];

    TROUBLESHOOT_RULES.forEach(rule => {
        const selectors = rule.selector.split(',').map(s => s.trim());
        selectors.forEach(sel => {
            try {
                const els = document.querySelectorAll(sel);
                els.forEach(el => {
                    if (el.offsetParent !== null || el.offsetWidth > 0) {
                        issues.push({ el, rule });
                        el.classList.add('helper-troubleshoot-target');
                    }
                });
            } catch (e) { /* invalid selector */ }
        });
    });

    // Deduplicate by rule title
    const seen = new Set();
    const uniqueIssues = issues.filter(i => {
        if (seen.has(i.rule.title)) return false;
        seen.add(i.rule.title);
        return true;
    });

    if (uniqueIssues.length === 0) {
        // All clear!
        const panel = document.createElement('div');
        panel.className = 'helper-popover helper-troubleshoot-panel';
        panel.innerHTML = `
            <div class="helper-popover-header">
                <div class="helper-popover-title">System Health Check</div>
                <button class="helper-popover-close" onclick="exitHelperMode()">&times;</button>
            </div>
            <div class="helper-troubleshoot-clear">
                <div class="helper-troubleshoot-clear-icon">✅</div>
                <div class="helper-troubleshoot-clear-text">All Clear!</div>
                <div class="helper-troubleshoot-clear-desc">All services are connected and running normally. No issues detected.</div>
            </div>
        `;
        document.body.appendChild(panel);
        _helperPopover = panel;
        _positionPanelNearFloatBtn(panel);
        return;
    }

    // Show issues
    const panel = document.createElement('div');
    panel.className = 'helper-popover helper-troubleshoot-panel';
    panel.innerHTML = `
        <div class="helper-popover-header">
            <div class="helper-popover-title">⚠️ ${uniqueIssues.length} Issue${uniqueIssues.length > 1 ? 's' : ''} Found</div>
            <button class="helper-popover-close" onclick="exitHelperMode()">&times;</button>
        </div>
        <div class="helper-troubleshoot-list">
            ${uniqueIssues.map((issue, i) => `
                <div class="helper-troubleshoot-issue">
                    <div class="helper-troubleshoot-issue-title">${issue.rule.title}</div>
                    <div class="helper-troubleshoot-steps">
                        ${issue.rule.steps.map(s => `<div class="helper-troubleshoot-step">• ${s}</div>`).join('')}
                    </div>
                    ${issue.rule.action ? `<button class="helper-action-btn" data-tshoot-idx="${i}">${issue.rule.action.label}</button>` : ''}
                </div>
            `).join('')}
        </div>
    `;

    // Action click handlers
    panel.querySelectorAll('[data-tshoot-idx]').forEach(btn => {
        const idx = parseInt(btn.getAttribute('data-tshoot-idx'));
        btn.addEventListener('click', () => {
            exitHelperMode();
            if (uniqueIssues[idx]?.rule.action?.fn) uniqueIssues[idx].rule.action.fn();
        });
    });

    document.body.appendChild(panel);
    _helperPopover = panel;
    _positionPanelNearFloatBtn(panel);
}

function _positionPanelNearFloatBtn(panel) {
    const floatBtn = document.getElementById('helper-float-btn');
    if (floatBtn) {
        const btnRect = floatBtn.getBoundingClientRect();
        panel.style.right = (window.innerWidth - btnRect.right) + 'px';
        panel.style.bottom = (window.innerHeight - btnRect.top + 8) + 'px';
        panel.style.left = 'auto';
        panel.style.top = 'auto';
    }
    requestAnimationFrame(() => panel.classList.add('visible'));
}

function closeTroubleshootMode() {
    _troubleshootActive = false;
    document.querySelectorAll('.helper-troubleshoot-target').forEach(el => el.classList.remove('helper-troubleshoot-target'));
}

// ═══════════════════════════════════════════════════════════════════════════
// FIRST-LAUNCH & PAGE-LOAD HOOKS
// ═══════════════════════════════════════════════════════════════════════════

document.addEventListener('DOMContentLoaded', () => {
    setTimeout(() => {
        // First-launch welcome prompt
        const hasSetup = localStorage.getItem('soulsync_setup');
        const hasDismissed = localStorage.getItem('soulsync_setup_welcome_dismissed');
        if (!hasSetup && !hasDismissed) {
            const floatBtn = document.getElementById('helper-float-btn');
            if (floatBtn) {
                floatBtn.classList.add('first-launch-pulse');
                const tip = document.createElement('div');
                tip.className = 'helper-first-launch-tip';
                tip.textContent = 'New here? Click for setup help!';
                tip.addEventListener('click', () => {
                    tip.remove();
                    floatBtn.classList.remove('first-launch-pulse');
                    localStorage.setItem('soulsync_setup_welcome_dismissed', '1');
                    activateHelperMode('setup');
                });
                document.body.appendChild(tip);

                // Auto-dismiss after 12 seconds
                setTimeout(() => {
                    if (tip.parentElement) {
                        tip.classList.add('fading');
                        setTimeout(() => tip.remove(), 500);
                        floatBtn.classList.remove('first-launch-pulse');
                    }
                }, 12000);
            }
        }

        // What's New badge
        _updateHelperBadge();

        // Idle glow for undiscovered help button
        if (!localStorage.getItem('soulsync_helper_discovered')) {
            const btn = document.getElementById('helper-float-btn');
            if (btn) btn.classList.add('undiscovered');
        }
    }, 2500);
});
