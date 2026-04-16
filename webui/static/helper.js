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
    '#toggle-download-manager-btn': {
        title: 'Toggle Download Manager',
        description: 'Show or hide the download manager panel on the right side. The panel shows active downloads, finished downloads, and queue management.',
        docsId: 'search-manager'
    },
    '.search-mode-toggle': {
        title: 'Search Mode',
        description: 'Switch between Enhanced Search (categorized metadata results with album art) and Basic Search (raw Soulseek results with detailed file info and filters).',
        tips: [
            'Enhanced: shows Artists, Albums, Singles, Tracks from your metadata source',
            'Basic: shows raw Soulseek P2P results with format, bitrate, size, uploader info'
        ],
        docsId: 'search-enhanced'
    },

    // Enhanced Search
    '.enhanced-search-input-wrapper': {
        title: 'Enhanced Search',
        description: 'Type an artist, album, or track name. Results appear in categorized sections: Library Artists, Artists, Albums, Singles & EPs, and Tracks. Results come from your active metadata source.',
        tips: [
            'Click an album to open the download modal',
            'Click a track to search your download source',
            'Play button previews tracks from your download source',
            'Multi-source tabs compare results across Spotify, iTunes, and Deezer'
        ],
        docsId: 'search-enhanced'
    },
    '.enh-source-tabs': {
        title: 'Source Tabs',
        description: 'Switch between metadata sources to see results from Spotify, iTunes, or Deezer. Each source has its own catalog — tracks missing on one may be found on another.',
        docsId: 'search-enhanced'
    },
    '#enh-db-artists-section': {
        title: 'Library Artists',
        description: 'Artists from your local music library that match the search. Click to view their collection on the Library page.',
    },
    '#enh-spotify-artists-section': {
        title: 'Artists',
        description: 'Artists from your metadata source matching the search. Click to view their full discography on the Artists page.',
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

    // Download Manager Side Panel
    '.downloads-side-panel': {
        title: 'Download Manager',
        description: 'Shows all active and completed downloads. Manage downloads, clear completed items, or cancel active transfers.',
        docsId: 'search-manager'
    },
    '.controls-panel': {
        title: 'Download Controls',
        description: 'Overview of active and finished download counts. Clear Completed removes finished items from the list. Clear Current cancels all active downloads.',
        docsId: 'search-manager'
    },
    '.controls-panel__clear-btn': {
        title: 'Clear Completed',
        description: 'Remove all finished downloads from the download manager list. Doesn\'t affect the downloaded files — they\'re already in your library.',
    },
    '.controls-panel__cancel-all-btn': {
        title: 'Clear Current',
        description: 'Cancel all active downloads in progress. Files that were partially downloaded will be cleaned up.',
    },
    '#active-queue': {
        title: 'Download Queue',
        description: 'Active downloads in progress. Each item shows track name, format, download speed, progress, and a cancel button.',
    },
    '#finished-queue': {
        title: 'Finished Downloads',
        description: 'Completed downloads. Shows track name, format, file size, and final status (success or error).',
    },

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
    '#personalized-recently-added': {
        title: 'Recently Added',
        description: 'The latest tracks added to your library. A quick way to see what\'s new in your collection.',
    },
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
    '#personalized-top-tracks': {
        title: 'Your Top 50',
        description: 'Your all-time most played tracks from listening history. A snapshot of your personal favorites.',
    },
    '#personalized-forgotten-favorites': {
        title: 'Forgotten Favorites',
        description: 'Tracks you used to play frequently but haven\'t listened to in a while. Rediscover music you loved.',
    },
    '#personalized-discovery-shuffle': {
        title: 'Discovery Shuffle',
        description: 'Random tracks from your entire discovery pool — different every time you load. A surprise mix for when you want something new.',
        docsId: 'disc-playlists'
    },
    '#personalized-familiar-favorites': {
        title: 'Familiar Favorites',
        description: 'Your reliable go-to tracks. Consistently played songs that define your taste.',
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

    // Spotify Library
    '#spotify-library-section': {
        title: 'Your Spotify Library',
        description: 'Albums saved in your Spotify account. Browse, search, and download albums you\'ve saved on Spotify but don\'t have locally.',
        tips: [
            'Search and filter by status (All/Missing/Owned)',
            'Sort by date saved, artist, album name, or release date',
            '"Download Missing" downloads all albums not in your library'
        ],
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

    // ─── ARTISTS PAGE ─────────────────────────────────────────────────

    // Search State
    '.artists-search-state': {
        title: 'Artist Search',
        description: 'Search for any artist by name. Results show artist cards with images — click one to view their full discography.',
        docsId: 'art-search'
    },
    '#artists-search-input': {
        title: 'Search Input',
        description: 'Type an artist name to search across your active metadata source. Results appear as you type with a short debounce delay.',
        docsId: 'art-search'
    },
    '#artists-search-status': {
        title: 'Search Status',
        description: 'Shows the current search state — ready, searching, or result count.',
    },

    // Results State
    '#artists-results-state': {
        title: 'Search Results',
        description: 'Artist cards matching your search. Click any artist to view their full discography with albums, singles, and EPs.',
        docsId: 'art-search'
    },
    '#artists-back-button': {
        title: 'Back to Search',
        description: 'Return to the initial search view to start a new artist search.',
    },
    '#artists-cards-container': {
        title: 'Artist Cards',
        description: 'Each card shows an artist from your metadata source. Click to load their full discography.',
    },

    // Artist Detail — Hero
    '#artists-hero-section': {
        title: 'Artist Profile',
        description: 'Rich artist profile with photo, name, genres, bio, and service links. Data comes from your metadata cache and library enrichment (Last.fm, Spotify, MusicBrainz, etc.).',
        tips: [
            'Service badges link to the artist on each platform',
            'Bio comes from Last.fm enrichment if the artist is in your library',
            'Listener and play count stats from Last.fm',
            'Genre pills combine metadata source genres with Last.fm tags'
        ],
        docsId: 'art-detail'
    },
    '.artists-hero-name': {
        title: 'Artist Name',
        description: 'The artist\'s official name from your metadata source.',
    },
    '.artists-hero-badges': {
        title: 'Service Badges',
        description: 'Links to this artist on external services. Click any badge to open the artist\'s page on that platform. Badges appear for services where this artist has been enriched.',
        tips: [
            'Spotify, MusicBrainz, Deezer, iTunes, Last.fm, Genius, Tidal, Qobuz',
            'Badges only appear when the artist has an ID for that service',
            'Data comes from library enrichment workers'
        ]
    },
    '.artists-hero-genres': {
        title: 'Genres',
        description: 'Genre tags for this artist. Combines genres from your metadata source with Last.fm tags for comprehensive coverage.',
    },
    '.artists-hero-bio': {
        title: 'Artist Bio',
        description: 'Biography from Last.fm. Click "Read more" to expand. Only available for artists in your library that have been enriched by the Last.fm worker.',
    },
    '.artists-hero-stats': {
        title: 'Listener Stats',
        description: 'Last.fm listener count and total play count. Shows how popular this artist is globally on Last.fm.',
    },
    '#artist-detail-watchlist-btn': {
        title: 'Watchlist',
        description: 'Add or remove this artist from your Watchlist. Watched artists are scanned for new releases which get added to your Wishlist for download.',
        docsId: 'art-watchlist'
    },
    '#artist-detail-watchlist-settings-btn': {
        title: 'Watchlist Settings',
        description: 'Configure which release types to monitor for this artist — Albums, EPs, Singles, and content filters (live, remixes, acoustic, compilations).',
        docsId: 'art-settings'
    },

    // Discography Tabs
    '.artist-detail-tabs': {
        title: 'Discography Tabs',
        description: 'Switch between Albums and Singles & EPs. Each tab shows the artist\'s releases in that category.',
        docsId: 'art-detail'
    },
    '#albums-tab': {
        title: 'Albums',
        description: 'Full-length studio albums by this artist. Click any album card to open the download modal.',
    },
    '#singles-tab': {
        title: 'Singles & EPs',
        description: 'Singles and extended plays by this artist. Includes single tracks, 2-3 track releases, and EPs.',
    },

    // Album Cards
    '#album-cards-container': {
        title: 'Album Grid',
        description: 'Album cards with cover art. Click any album to open the download modal where you can select tracks, check library matches, and start downloading.',
        tips: [
            'Cover art fills the card with album name overlaid at the bottom',
            'Completion badges show ownership status (Complete, Partial, Missing)',
            'Cards check your library automatically after loading'
        ],
        docsId: 'art-detail'
    },
    '#singles-cards-container': {
        title: 'Singles Grid',
        description: 'Single and EP cards. Same behavior as albums — click to open the download modal.',
    },
    '.album-card': {
        title: 'Release Card',
        description: 'An album, single, or EP from this artist. Click to open the download modal with track selection, library matching, and download controls.',
        tips: [
            'Completion overlay shows how many tracks you own',
            'Green = complete, Yellow = partial, Red = missing',
            '"Checking..." means library match is in progress'
        ]
    },
    '.completion-overlay': {
        title: 'Completion Status',
        description: 'Shows how many tracks from this release are in your library. "Complete" means you have all tracks, "Partial" shows owned/total, "Missing" means none found.',
    },

    // Similar Artists
    '#similar-artists-section': {
        title: 'Similar Artists',
        description: 'Artists with a similar sound, streamed in real-time from your metadata source. Click any card to view that artist\'s discography.',
        tips: [
            'Cards load progressively as the server finds matches',
            'Click a card to navigate to that artist\'s discography',
            'Images are lazy-loaded for performance'
        ],
        docsId: 'art-detail'
    },
    '.similar-artist-bubble': {
        title: 'Similar Artist',
        description: 'An artist similar to the one you\'re viewing. Click to load their discography and browse their releases.',
    },

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
        description: 'Import audio files from your staging folder into your library. Match files to album metadata, tag them, and organize into your collection.',
        docsId: 'import'
    },
    '.import-page-refresh-btn': {
        title: 'Refresh',
        description: 'Re-scan your staging folder for new audio files. Use after dropping new files into the staging path.',
    },
    '#import-staging-bar': {
        title: 'Staging Folder',
        description: 'Shows your configured staging folder path and the number of audio files found. Set the staging path in Settings → Download Settings.',
        docsId: 'imp-setup'
    },
    '#import-page-queue': {
        title: 'Processing Queue',
        description: 'Shows albums and singles currently being processed. Each job goes through matching, tagging, cover art embedding, and file organization.',
    },
    '#import-page-tab-album': {
        title: 'Albums Tab',
        description: 'Import complete albums. Search for an album, match staging files to tracks, then process. Suggestions appear automatically from your staging folder.',
        docsId: 'imp-workflow'
    },
    '#import-page-tab-singles': {
        title: 'Singles Tab',
        description: 'Import individual audio files as single tracks. Select files, and SoulSync identifies them using AcoustID fingerprinting or filename matching.',
        docsId: 'imp-singles'
    },
    '#import-page-suggestions-grid': {
        title: 'Suggestions',
        description: 'Albums automatically detected from your staging folder based on folder names and file metadata. Click a suggestion to start the matching process.',
    },
    '#import-page-album-search-input': {
        title: 'Album Search',
        description: 'Search your metadata source for an album to match against staging files. Enter the album name or artist + album.',
    },
    '#import-page-album-match-section': {
        title: 'Track Matching',
        description: 'Match your staging files to album tracks. Drag files from the unmatched pool onto tracks, or let auto-matching do it. Green = matched, red = unmatched.',
        tips: [
            'Drag and drop files from the unmatched pool to track slots',
            '"Re-match Automatically" re-runs the matching algorithm',
            '"Back to Search" returns to the album search view'
        ],
        docsId: 'imp-matching'
    },
    '#import-page-unmatched-pool': {
        title: 'Unmatched Files',
        description: 'Audio files in your staging folder that haven\'t been matched to an album track yet. Drag them onto the correct track slot above.',
        docsId: 'imp-matching'
    },
    '#import-page-album-process-btn': {
        title: 'Process Album',
        description: 'Start processing the matched album. Tags files with metadata, embeds cover art, renames and organizes files into your library, then triggers a media server scan.',
    },
    '#import-page-singles-list': {
        title: 'Singles List',
        description: 'Individual audio files in your staging folder. Select files and click "Process Selected" to identify and import them as single tracks.',
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
    'downloads':   'first-download',
    'discover':    'discover',
    'artists':     'artists-browse',
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
            { page: 'dashboard', selector: '#spotify-service-card', title: 'Metadata Source', description: 'Shows your metadata source connection (Spotify, iTunes, or Deezer). This determines where album, artist, and track info comes from. Click "Test Connection" to verify.' },
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
            // Search page layout (top-to-bottom, elements visible on load)
            { page: 'downloads', selector: '.search-mode-toggle', title: 'Search Modes', description: 'Two search modes: Enhanced Search (default) shows categorized results from your metadata source. Classic Search queries your download source directly for raw file results.' },
            { page: 'downloads', selector: '.enhanced-search-input-wrapper', title: 'Search for Music', description: 'Type an artist or album name here. Results appear in categorized sections — Artists, Albums, Singles/EPs, and Tracks. Try searching for your favorite artist now!' },
            { page: 'downloads', selector: '#toggle-download-manager-btn', title: 'Download Manager', description: 'This button toggles the download manager panel on the right side. It shows active downloads with progress bars, queued items, and completed downloads with their file paths.' },

            // What results look like (describe since they appear after searching)
            { page: 'downloads', selector: '#enh-results-container', title: 'Search Results', description: 'After searching, results appear here organized by type: Artists at the top as cards, then Albums, Singles/EPs, and individual Tracks. "In Library" badges mark items you already own.' },
            { page: 'downloads', selector: '.search-mode-toggle', title: 'Downloading an Album', description: 'Click any album card to open the download modal. You\'ll see the tracklist, quality options, and a big "Download Album" button. Individual tracks have a play button to preview before downloading.' },
            { page: 'downloads', selector: '.enhanced-search-input-wrapper', title: 'That\'s It!', description: 'Search, click, download — it\'s that simple. Albums go to your configured download path, get tagged with metadata, and sync to your media server automatically. 🎉' },
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
    'artists-browse': {
        title: 'Browse Artists',
        description: 'Search for artists and explore their discography.',
        icon: '🎤',
        steps: [
            // Artists list page (visible on load)
            { page: 'artists', selector: '#artists-search-input', title: 'Search for an Artist', description: 'Type any artist name to search your metadata source. Results appear instantly as cards below. Click one to open their full profile and discography.' },

            // Artist detail page (describe what they'll see after clicking)
            { page: 'artists', selector: '#artists-search-input', title: 'Artist Profile', description: 'After clicking an artist, you\'ll see a rich hero section with their photo, bio, genres, listening stats from Last.fm, and links to external services like Spotify and MusicBrainz.' },
            { page: 'artists', selector: '#artists-search-input', title: 'Discography & Downloads', description: 'Below the hero, tabs show Albums and Singles/EPs. Click any release to open the download modal. The "Similar Artists" section at the bottom shows recommendations — click any to keep exploring.' },
            { page: 'artists', selector: '#artists-search-input', title: 'Try It Now!', description: 'Search for your favorite artist above to see their full profile. From there you can download albums, explore similar artists, and add them to your watchlist. 🎉' },
        ]
    },
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
            { page: 'discover', selector: '#spotify-library-section', title: 'Your Spotify Library', description: 'If Spotify is connected, this shows all your saved albums. Filter by Missing/Owned, sort by date, and click "Download Missing" to grab everything you don\'t have yet. Only visible with Spotify connected.' },
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
            { page: 'import', selector: '.import-page-header', title: 'Import Music', description: 'Import audio files from your staging folder into your organized library. Files are matched to album metadata, tagged, and moved to the correct location.' },
            { page: 'import', selector: '.import-page-staging-bar', title: 'Staging Folder', description: 'Shows your configured staging folder path and stats (file count, total size). This is where you drop audio files before importing. Configure the path in Settings → Downloads.' },
            { page: 'import', selector: '.import-page-refresh-btn', title: 'Refresh', description: 'Re-scans your staging folder for new audio files. Hit this after dropping new files in.' },

            // Queue
            { page: 'import', selector: '#import-page-queue', title: 'Processing Queue', description: 'When you process albums or singles, jobs appear here with progress indicators. "Clear finished" removes completed jobs from the list.' },

            // Tabs
            { page: 'import', selector: '.import-page-tab-bar', title: 'Albums vs Singles', description: 'Two modes: Albums tab matches full albums to metadata (cover art, track numbers, disc info). Singles tab processes individual files one at a time.' },

            // Album workflow
            { page: 'import', selector: '#import-page-suggestions', title: 'Album Suggestions', description: 'The importer analyzes your staging files and suggests album matches based on embedded tags. Click a suggestion to start the matching process.' },
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
    { id: 'first-download',  label: 'Download Your First Track',    desc: 'Search for and download something',                  icon: '🎶', page: 'downloads' },
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

    // ── /status — checks services (spotify, media_server, soulseek) ─────
    try {
        const resp = await fetch('/status');
        if (resp.ok) {
            const data = await resp.json();
            // Metadata source: spotify.connected is always true (iTunes fallback), check .source
            if (data.spotify?.connected && data.spotify?.source) {
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
        // Also check the finished queue (if on downloads page)
        if (!results['first-download']) {
            const fq = document.querySelector('#finished-queue');
            if (fq && fq.querySelector('.download-item')) {
                results['first-download'] = Date.now();
                _markSetupComplete('first-download');
            }
        }
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

const WHATS_NEW = {
    '2.2': [
        // --- April 15, 2026 ---
        { date: 'April 15, 2026' },
        { title: 'Dashboard Library Status Card', desc: 'Smart card on the Dashboard showing your library state — server connection, track counts, last refresh time. Guides new users through setup, shows empty-library prompts, and lets you trigger a scan directly from the dashboard', page: 'dashboard' },
        { title: 'AcoustID Scanner Upgrade', desc: 'Now scans your full library (not just Transfer) to detect wrong downloads. Actionable fixes: retag with correct metadata, re-download the right track, or delete the wrong file. Enabled by default, runs daily' },
        { title: 'Tools Page', desc: 'All tool cards (Database Updater, Quality Scanner, Duplicate Cleaner, Retag, Backups, Cache, etc.) and Library Maintenance moved from the Dashboard to a dedicated Tools page in the sidebar. Dashboard shows a quick-link card', page: 'tools' },
        { title: 'Watchlist & Wishlist Sidebar Pages', desc: 'Watchlist and Wishlist promoted from modals to full sidebar pages. All features preserved — artist grid, scan controls, batch operations, live activity, countdown timers, category cards with mosaic backgrounds. Header buttons now navigate to the pages', page: 'watchlist' },
        { title: 'Picard-Style MusicBrainz Album Consistency', desc: 'Recording MBIDs now pulled from the matched release tracklist instead of independent searches. Batch-level artist name used for stable cache keys. Post-batch consistency pass rewrites album-level tags on all files to guarantee identical MusicBrainz IDs — prevents Navidrome album splits' },
        { title: 'Fix Spotify API Leaking When Deezer/iTunes is Primary', desc: 'Spotify was being called for watchlist album scanning, similar artist discovery, repair jobs, and the Artists page search even when another source was set as primary. All data-fetching now respects the configured primary source. Spotify playlist sync is unaffected' },
        { title: 'Fix OAuth Callback Port Hardcoding', desc: 'Custom callback ports (SOULSYNC_SPOTIFY_CALLBACK_PORT / SOULSYNC_TIDAL_CALLBACK_PORT) are now respected in auth instruction pages and log messages instead of always showing 8888. Added startup diagnostics logging for callback port binding' },
        { title: 'Fix Wishlist Button on Non-Dashboard Pages', desc: 'Wishlist button click handler moved to global init so it works from any page, not just the dashboard' },
        { title: 'Fix Allow Duplicates Setting Not Saving', desc: 'The "Allow duplicate tracks across albums" toggle was never persisted — it silently reset to ON on every page reload. Now saves correctly' },
        { title: 'Fix Wishlist Dropping Cross-Album Tracks', desc: 'Wishlist cleanup was removing same-titled tracks from different albums even when Allow Duplicates was enabled. Cleanup now respects the setting — same song from different albums can coexist in the wishlist' },
        { title: 'Fix "Replace Lower Quality" Setting Not Persisting', desc: 'The import section appeared twice in the settings save payload — the second instance (with only staging_path) overwrote the first (with replace_lower_quality). Merged into a single block' },
        { title: 'Inbound Music Request API', desc: 'New POST /api/v1/request endpoint — trigger downloads from Discord bots, Home Assistant, curl, or any external tool. Async with status polling and optional notify_url callback. New "Webhook Received" automation trigger and "Search & Download" action in the Automation Hub' },
        { title: 'Fix Spotify Enrichment Worker Infinite Loop', desc: 'Artists with an existing Spotify ID but no match status got stuck in the enrichment queue — the worker processed them every 3 seconds forever without marking them as done. Now correctly marks them as matched' },
        { title: 'Reject Qobuz 30-Second Samples', desc: 'Qobuz previews (30s samples for tracks requiring a subscription or region-restricted) are now detected and rejected. Checks the API sample flag before downloading, and validates file duration after download as a safety net' },

        // --- April 14, 2026 ---
        { date: 'April 14, 2026' },
        { title: 'Fix Staging Files Ignoring Path Template',        desc: 'Files matched from the Staging folder were copied to the transfer root with their original filename instead of applying the configured path template. Post-processing now receives full artist/album context for staging matches' },

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

function _getCurrentVersion() {
    const btn = document.querySelector('.version-button');
    return btn ? btn.textContent.trim().replace('v', '') : '2.2';
}

function _getLatestWhatsNewVersion() {
    const versions = Object.keys(WHATS_NEW).sort((a, b) => parseFloat(b) - parseFloat(a));
    return versions[0] || '2.2';
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
    // Cycle to next older version in the what's new panel
    const versions = Object.keys(WHATS_NEW).sort((a, b) => parseFloat(b) - parseFloat(a));
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
        selector: '#spotify-service-card .status-dot.disconnected, #spotify-service-card .status-dot.error',
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
        selector: '#media-server-service-card .status-dot.disconnected, #media-server-service-card .status-dot.error',
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
        selector: '#soulseek-service-card .status-dot.disconnected, #soulseek-service-card .status-dot.error',
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
    {
        selector: '.issue-card.status-open, .issues-stat-open',
        title: 'Open Issues in Library',
        steps: [
            'Open issues have been reported for tracks in your library',
            'Go to the Issues page to review and resolve them',
            'Common issues: wrong track downloaded, bad metadata, low audio quality',
            'Each issue has fix suggestions and action buttons'
        ],
        action: { label: 'View Issues', fn: () => navigateToPage('issues') }
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
