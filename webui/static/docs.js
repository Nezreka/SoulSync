// ===============================
// HELP & DOCS PAGE
// ===============================

const DOCS_SECTIONS = [
    {
        id: 'getting-started',
        title: 'Getting Started',
        icon: '/static/dashboard.png',
        children: [
            { id: 'gs-overview', title: 'Overview' },
            { id: 'gs-first-setup', title: 'First-Time Setup' },
            { id: 'gs-connecting', title: 'Connecting Services' },
            { id: 'gs-interface', title: 'Understanding the Interface' }
        ],
        content: () => `
            <div class="docs-subsection" id="gs-overview">
                <h3 class="docs-subsection-title">Overview</h3>
                <p class="docs-text">SoulSync is a self-hosted music download, sync, and library management platform. It connects to <strong>Spotify</strong>, <strong>Apple Music/iTunes</strong>, <strong>Tidal</strong>, <strong>YouTube</strong>, and <strong>Beatport</strong> for metadata, and uses <strong>Soulseek</strong> (via slskd) as the primary download source. Your library is served through <strong>Plex</strong>, <strong>Jellyfin</strong>, or <strong>Navidrome</strong>.</p>
                <div class="docs-features">
                    <div class="docs-feature-card"><h4>&#x1F3B5; Download Music</h4><p>Search and download tracks in FLAC, MP3, and more from Soulseek, YouTube, or Tidal, with automatic metadata tagging and file organization.</p></div>
                    <div class="docs-feature-card"><h4>&#x1F504; Playlist Sync</h4><p>Mirror playlists from Spotify, YouTube, Tidal, and Beatport. Discover official metadata and sync to your media server.</p></div>
                    <div class="docs-feature-card"><h4>&#x1F4DA; Library Management</h4><p>Browse, edit, and enrich your music library with metadata from 7 services. Write tags directly to audio files.</p></div>
                    <div class="docs-feature-card"><h4>&#x1F916; Automations</h4><p>Schedule tasks, chain workflows with signals, and get notified via Discord, Pushbullet, or Telegram.</p></div>
                    <div class="docs-feature-card"><h4>&#x1F50D; Artist Discovery</h4><p>Discover new artists via similar-artist recommendations, seasonal playlists, genre exploration, and time-machine browsing.</p></div>
                    <div class="docs-feature-card"><h4>&#x1F440; Watchlist</h4><p>Follow artists and automatically scan for new releases. New tracks are added to your wishlist for download.</p></div>
                </div>
            </div>
            <div class="docs-subsection" id="gs-first-setup">
                <h3 class="docs-subsection-title">First-Time Setup</h3>
                <p class="docs-text">After launching SoulSync, head to the <strong>Settings</strong> page to configure your services. At minimum you need:</p>
                <ol class="docs-steps">
                    <li><strong>Download Source</strong> &mdash; Connect at least one download source: Soulseek (slskd), YouTube, or Tidal. Soulseek offers the best quality selection; YouTube and Tidal work as alternatives or fallbacks in Hybrid mode.</li>
                    <li><strong>Media Server</strong> &mdash; Connect Plex, Jellyfin, or Navidrome so SoulSync knows where your library lives and can trigger scans.</li>
                    <li><strong>Spotify (Recommended)</strong> &mdash; Connect Spotify for the richest metadata. Create an app at <strong>developer.spotify.com</strong>, enter your Client ID and Secret, then click Authenticate.</li>
                    <li><strong>Download Path</strong> &mdash; Set your download and transfer paths in the Download Settings section. The transfer path should point to your media server's monitored folder.</li>
                </ol>
                <div class="docs-callout tip"><span class="docs-callout-icon">&#x1F4A1;</span><div>You can start using SoulSync with just one download source (Soulseek, YouTube, or Tidal). Spotify and other services add metadata enrichment but aren't strictly required &mdash; iTunes/Apple Music is always available as a free fallback.</div></div>
            </div>
            <div class="docs-subsection" id="gs-connecting">
                <h3 class="docs-subsection-title">Connecting Services</h3>
                <p class="docs-text">SoulSync integrates with many external services. Here's a quick reference for each:</p>
                <table class="docs-table">
                    <thead><tr><th>Service</th><th>Purpose</th><th>Auth Required</th></tr></thead>
                    <tbody>
                        <tr><td><strong>Spotify</strong></td><td>Primary metadata source (artists, albums, tracks, cover art, genres)</td><td>OAuth &mdash; Client ID + Secret</td></tr>
                        <tr><td><strong>iTunes / Apple Music</strong></td><td>Fallback metadata source, always free, no auth needed</td><td>None</td></tr>
                        <tr><td><strong>Soulseek (slskd)</strong></td><td>Download source &mdash; P2P network, best for lossless and rare music</td><td>URL + API key</td></tr>
                        <tr><td><strong>YouTube</strong></td><td>Download source &mdash; audio extraction via yt-dlp</td><td>None (optional cookies browser)</td></tr>
                        <tr><td><strong>Tidal</strong></td><td>Download source + playlist import</td><td>OAuth &mdash; Client ID + Secret</td></tr>
                        <tr><td><strong>Plex</strong></td><td>Media server &mdash; library scanning, metadata sync, audio streaming</td><td>URL + Token</td></tr>
                        <tr><td><strong>Jellyfin</strong></td><td>Media server &mdash; library scanning, audio streaming</td><td>URL + API Key</td></tr>
                        <tr><td><strong>Navidrome</strong></td><td>Media server &mdash; auto-detects changes, audio streaming</td><td>URL + Username + Password</td></tr>
                        <tr><td><strong>Last.fm</strong></td><td>Enrichment &mdash; listener stats, tags, bios, similar artists</td><td>API Key</td></tr>
                        <tr><td><strong>Genius</strong></td><td>Enrichment &mdash; lyrics, descriptions, alternate names</td><td>Access Token</td></tr>
                        <tr><td><strong>AcoustID</strong></td><td>Audio fingerprint verification of downloads</td><td>API Key</td></tr>
                        <tr><td><strong>ListenBrainz</strong></td><td>Listening history and recommendations</td><td>URL + Token</td></tr>
                    </tbody>
                </table>
            </div>
            <div class="docs-subsection" id="gs-interface">
                <h3 class="docs-subsection-title">Understanding the Interface</h3>
                <p class="docs-text">SoulSync uses a <strong>sidebar navigation</strong> layout. The left sidebar contains links to every page, a media player at the bottom, and service status indicators. The main content area changes based on the selected page.</p>
                <ul class="docs-list">
                    <li><strong>Dashboard</strong> &mdash; System overview, tool cards, enrichment worker status, activity feed</li>
                    <li><strong>Sync</strong> &mdash; Import and manage playlists from Spotify, YouTube, Tidal, Beatport, ListenBrainz</li>
                    <li><strong>Search</strong> &mdash; Find and download music via enhanced or basic search</li>
                    <li><strong>Discover</strong> &mdash; Explore new artists, curated playlists, genre browsers, time machine</li>
                    <li><strong>Artists</strong> &mdash; Search artists, manage your watchlist, scan for new releases</li>
                    <li><strong>Automations</strong> &mdash; Create scheduled tasks and event-driven workflows</li>
                    <li><strong>Library</strong> &mdash; Browse and manage your music collection with standard or enhanced views</li>
                    <li><strong>Import</strong> &mdash; Import music files from a staging folder with album/track matching</li>
                    <li><strong>Settings</strong> &mdash; Configure services, download preferences, quality profiles, and more</li>
                </ul>
            </div>
        `
    },
    {
        id: 'dashboard',
        title: 'Dashboard',
        icon: '/static/dashboard.png',
        children: [
            { id: 'dash-overview', title: 'Overview & Stats' },
            { id: 'dash-workers', title: 'Enrichment Workers' },
            { id: 'dash-tools', title: 'Tool Cards' },
            { id: 'dash-retag', title: 'Retag Tool' },
            { id: 'dash-backup', title: 'Backup Manager' },
            { id: 'dash-repair', title: 'Repair & Maintenance' },
            { id: 'dash-activity', title: 'Activity Feed' }
        ],
        content: () => `
            <div class="docs-subsection" id="dash-overview">
                <h3 class="docs-subsection-title">Overview & Stats</h3>
                <p class="docs-text">The dashboard is your command center. At the top you'll see <strong>service status indicators</strong> for Spotify, your media server, and Soulseek &mdash; showing connected/disconnected state at a glance. Below that, stat cards display your library totals: artists, albums, tracks, and total library size.</p>
                <p class="docs-text">Stats update in real-time via WebSocket &mdash; no page refresh needed.</p>
            </div>
            <div class="docs-subsection" id="dash-workers">
                <h3 class="docs-subsection-title">Enrichment Workers</h3>
                <p class="docs-text">The header bar contains <strong>enrichment worker icons</strong> for each metadata service. Hover over any icon to see its current status, what item it's processing, and progress counts (e.g., "142/500 matched").</p>
                <p class="docs-text">Workers run automatically in the background, enriching your library with metadata from:</p>
                <div class="docs-features">
                    <div class="docs-feature-card"><h4>Spotify</h4><p>Artist genres, follower counts, images, album release dates, track preview URLs</p></div>
                    <div class="docs-feature-card"><h4>MusicBrainz</h4><p>MBIDs for artists, albums, and tracks &mdash; enables accurate cross-referencing</p></div>
                    <div class="docs-feature-card"><h4>Deezer</h4><p>Deezer IDs, genres, album metadata</p></div>
                    <div class="docs-feature-card"><h4>AudioDB</h4><p>Artist descriptions, artist art, album info</p></div>
                    <div class="docs-feature-card"><h4>iTunes</h4><p>iTunes/Apple Music IDs, preview links</p></div>
                    <div class="docs-feature-card"><h4>Last.fm</h4><p>Listener/play counts, bios, tags, similar artists for every artist/album/track</p></div>
                    <div class="docs-feature-card"><h4>Genius</h4><p>Lyrics, descriptions, alternate names, song artwork</p></div>
                </div>
                <div class="docs-callout info"><span class="docs-callout-icon">&#x2139;&#xFE0F;</span><div>Workers retry "not found" items every 30 days and errored items every 7 days. You can pause/resume any worker from the dashboard.</div></div>
                <p class="docs-text"><strong>Rate Limit Protection</strong>: Workers include smart rate limiting for all APIs. If Spotify returns a rate limit (429), a global ban activates &mdash; all Spotify calls are suppressed and searches automatically fall back to iTunes. A countdown modal appears showing ban duration, and the worker auto-resumes when the ban expires. You can manually disconnect Spotify from the modal to clear the ban immediately.</p>
            </div>
            <div class="docs-subsection" id="dash-tools">
                <h3 class="docs-subsection-title">Tool Cards</h3>
                <p class="docs-text">The dashboard features several tool cards for library maintenance:</p>
                <table class="docs-table">
                    <thead><tr><th>Tool</th><th>What It Does</th></tr></thead>
                    <tbody>
                        <tr><td><strong>Database Updater</strong></td><td>Refreshes your library by scanning your media server. Choose incremental (new only) or full refresh.</td></tr>
                        <tr><td><strong>Metadata Updater</strong></td><td>Updates artist photos, genres, styles, and biographies from MusicBrainz, Spotify, iTunes, and Last.fm.</td></tr>
                        <tr><td><strong>Quality Scanner</strong></td><td>Scans library for tracks below your quality preferences. Shows how many meet standards and finds replacements.</td></tr>
                        <tr><td><strong>Duplicate Cleaner</strong></td><td>Identifies and removes duplicate tracks from your library, freeing up disk space.</td></tr>
                        <tr><td><strong>Discovery Pool</strong></td><td>View and fix matched/failed discovery results across all mirrored playlists.</td></tr>
                        <tr><td><strong>Retag Tool</strong></td><td>Batch retag downloaded files with correct album metadata from Spotify/iTunes.</td></tr>
                        <tr><td><strong>Backup Manager</strong></td><td>Create, download, restore, and delete database backups. Rolling cleanup keeps the 5 most recent.</td></tr>
                    </tbody>
                </table>
                <div class="docs-callout tip"><span class="docs-callout-icon">&#x1F4A1;</span><div>Each tool card has a help button (?) that opens detailed instructions for that specific tool.</div></div>
            </div>
            <div class="docs-subsection" id="dash-retag">
                <h3 class="docs-subsection-title">Retag Tool</h3>
                <p class="docs-text">The Retag Tool lets you fix incorrect metadata tags on files already in your library. This is useful when files were downloaded with wrong or incomplete tags.</p>
                <ol class="docs-steps">
                    <li>Open the <strong>Retag Tool</strong> card on the Dashboard</li>
                    <li>Select an artist and album from the dropdown filters</li>
                    <li>The tool displays all tracks in the album with their <strong>current file tags</strong> alongside the <strong>correct metadata</strong> from Spotify or iTunes</li>
                    <li>Review the tag differences &mdash; mismatches are highlighted</li>
                    <li>Click <strong>Retag</strong> to write the corrected metadata to the audio files</li>
                </ol>
                <p class="docs-text">The retag operation writes title, artist, album artist, album, track number, disc number, year, and genre. Cover art can optionally be re-embedded.</p>
            </div>
            <div class="docs-subsection" id="dash-backup">
                <h3 class="docs-subsection-title">Backup Manager</h3>
                <p class="docs-text">The Backup Manager protects your SoulSync database (all library data, watchlists, playlists, automations, and settings).</p>
                <ul class="docs-list">
                    <li><strong>Create Backup</strong> &mdash; Creates a timestamped copy of the database file</li>
                    <li><strong>Download</strong> &mdash; Download any backup to your local machine</li>
                    <li><strong>Restore</strong> &mdash; Restore the database from a selected backup (current state is backed up first)</li>
                    <li><strong>Delete</strong> &mdash; Remove individual backups</li>
                    <li><strong>Rolling Cleanup</strong> &mdash; Automatically keeps only the 5 most recent backups to save disk space</li>
                </ul>
                <p class="docs-text">The system automation <strong>Auto-Backup Database</strong> creates a backup every 3 days automatically. You can adjust the interval in Automations.</p>
            </div>
            <div class="docs-subsection" id="dash-repair">
                <h3 class="docs-subsection-title">Repair & Maintenance</h3>
                <p class="docs-text">Additional maintenance tools accessible from the dashboard:</p>
                <ul class="docs-list">
                    <li><strong>Quality Scanner</strong> &mdash; Scans your entire library and flags tracks below your quality preferences. Shows a breakdown of formats and bitrates, and identifies tracks where higher-quality versions may be available on Soulseek.</li>
                    <li><strong>Duplicate Cleaner</strong> &mdash; Identifies duplicate tracks by comparing title, artist, album, and duration. Lets you review duplicates and choose which version to keep (typically the higher-quality one). Frees disk space by removing redundant files.</li>
                    <li><strong>Database Updater</strong> &mdash; Refreshes your library database by scanning your media server. <strong>Incremental</strong> mode only adds new content; <strong>Full Refresh</strong> rebuilds the entire database. <strong>Deep Scan</strong> performs a full comparison without losing any enrichment data from services.</li>
                    <li><strong>Metadata Updater</strong> &mdash; Triggers enrichment workers to update artist photos, genres, styles, biographies, and related metadata from all connected services (MusicBrainz, Spotify, iTunes, Last.fm, Deezer, AudioDB, Genius).</li>
                </ul>
            </div>
            <div class="docs-subsection" id="dash-activity">
                <h3 class="docs-subsection-title">Activity Feed</h3>
                <p class="docs-text">The activity feed at the bottom of the dashboard shows recent system events: downloads completed, syncs started, settings changed, automation runs, and errors. Events appear in real-time via WebSocket.</p>
            </div>
        `
    },
    {
        id: 'sync',
        title: 'Playlist Sync',
        icon: '/static/sync.png',
        children: [
            { id: 'sync-overview', title: 'Overview' },
            { id: 'sync-spotify', title: 'Spotify Playlists' },
            { id: 'sync-youtube', title: 'YouTube Playlists' },
            { id: 'sync-tidal', title: 'Tidal Playlists' },
            { id: 'sync-listenbrainz', title: 'ListenBrainz' },
            { id: 'sync-beatport', title: 'Beatport' },
            { id: 'sync-mirrored', title: 'Mirrored Playlists' },
            { id: 'sync-m3u', title: 'M3U Export' },
            { id: 'sync-discovery', title: 'Discovery Pipeline' }
        ],
        content: () => `
            <div class="docs-subsection" id="sync-overview">
                <h3 class="docs-subsection-title">Overview</h3>
                <p class="docs-text">The Sync page lets you import playlists from <strong>Spotify</strong>, <strong>YouTube</strong>, <strong>Tidal</strong>, and <strong>Beatport</strong>. Once imported, playlists are <strong>mirrored</strong> &mdash; they persist in your SoulSync instance and can be refreshed, discovered, and synced to your wishlist for downloading.</p>
            </div>
            <div class="docs-subsection" id="sync-spotify">
                <h3 class="docs-subsection-title">Spotify Playlists</h3>
                <p class="docs-text">If Spotify is connected, click <strong>Refresh</strong> to load all your Spotify playlists. Each playlist shows its cover art, track count, and sync status.</p>
                <p class="docs-text">For each playlist you can:</p>
                <ul class="docs-list">
                    <li><strong>View Details</strong> &mdash; See full track list and sync status</li>
                    <li><strong>Download Missing</strong> &mdash; Opens a modal showing tracks not in your library, with download controls</li>
                    <li><strong>Sync Playlist</strong> &mdash; Adds tracks to your wishlist for automated downloading</li>
                </ul>
                <div class="docs-callout tip"><span class="docs-callout-icon">&#x1F4A1;</span><div>Spotify-sourced playlists are auto-discovered at confidence 1.0 during refresh &mdash; no separate discovery step needed.</div></div>
            </div>
            <div class="docs-subsection" id="sync-youtube">
                <h3 class="docs-subsection-title">YouTube Playlists</h3>
                <p class="docs-text">Paste a YouTube playlist URL into the input field and click <strong>Parse Playlist</strong>. SoulSync extracts the track list and attempts to match each track to official Spotify/iTunes metadata.</p>
                <div class="docs-callout warning"><span class="docs-callout-icon">&#x26A0;&#xFE0F;</span><div>YouTube tracks often have non-standard titles (e.g., "Artist - Song (Official Video)"). The discovery pipeline handles this, but some manual fixes may be needed for edge cases.</div></div>
            </div>
            <div class="docs-subsection" id="sync-tidal">
                <h3 class="docs-subsection-title">Tidal Playlists</h3>
                <p class="docs-text">Requires Tidal authentication in Settings. Once connected, refresh to load your Tidal playlists. You can also select Tidal download quality: HQ (320kbps), HiFi (FLAC 16-bit), or HiFi Plus (up to 24-bit).</p>
            </div>
            <div class="docs-subsection" id="sync-listenbrainz">
                <h3 class="docs-subsection-title">ListenBrainz</h3>
                <p class="docs-text">If ListenBrainz is configured in Settings, the Sync page includes a ListenBrainz tab for browsing and importing playlists from your ListenBrainz account:</p>
                <ul class="docs-list">
                    <li><strong>Your Playlists</strong> &mdash; Playlists you've created on ListenBrainz</li>
                    <li><strong>Collaborative</strong> &mdash; Playlists shared with you by other users</li>
                    <li><strong>Created For You</strong> &mdash; Auto-generated playlists based on your listening history</li>
                </ul>
                <p class="docs-text">ListenBrainz tracks are matched against Spotify/iTunes using a <strong>4-strategy search</strong>: direct match, swapped artist/title, album-based lookup, and extended fuzzy search. Discovered tracks can be synced to your library like any other playlist.</p>
            </div>
            <div class="docs-subsection" id="sync-beatport">
                <h3 class="docs-subsection-title">Beatport</h3>
                <p class="docs-text">The Beatport tab provides deep integration with electronic music content across three views:</p>
                <p class="docs-text"><strong>Browse</strong> &mdash; Featured content organized into sections:</p>
                <ul class="docs-list">
                    <li>Hero Tracks &mdash; Featured highlight tracks</li>
                    <li>New Releases &mdash; Latest additions to the catalog</li>
                    <li>Featured Charts &mdash; Curated editorial charts</li>
                    <li>DJ Charts &mdash; Charts created by DJs and producers</li>
                    <li>Top 10 Lists &mdash; Quick top picks across genres</li>
                    <li>Hype Picks &mdash; Trending underground tracks</li>
                </ul>
                <p class="docs-text"><strong>Genre Browser</strong> &mdash; Browse 12+ electronic music genres (House, Techno, Drum & Bass, Trance, etc.) with per-genre views: Top 10 tracks, staff picks, hype rankings, latest releases, and new charts.</p>
                <p class="docs-text"><strong>Charts</strong> &mdash; Top 100 and Hype charts with full track listings. Each track can be manually matched against Spotify for metadata, then synced and downloaded.</p>
                <div class="docs-callout info"><span class="docs-callout-icon">&#x2139;&#xFE0F;</span><div>Beatport data is cached with a configurable TTL. The system automation <strong>Refresh Beatport Cache</strong> runs every 24 hours to keep content fresh.</div></div>
            </div>
            <div class="docs-subsection" id="sync-mirrored">
                <h3 class="docs-subsection-title">Mirrored Playlists</h3>
                <p class="docs-text">Every parsed playlist from any source is automatically <strong>mirrored</strong>. The Mirrored tab shows all saved playlists with source-branded cards, live discovery status, and download progress.</p>
                <ul class="docs-list">
                    <li>Re-parsing the same playlist URL updates the existing mirror &mdash; no duplicates</li>
                    <li>Cards show live state: Discovering, Discovered, Downloading, Downloaded</li>
                    <li>Download progress survives page refresh</li>
                    <li>Each profile has its own mirrored playlists</li>
                </ul>
            </div>
            <div class="docs-subsection" id="sync-m3u">
                <h3 class="docs-subsection-title">M3U Export</h3>
                <p class="docs-text">Export any mirrored playlist as an <strong>M3U file</strong> for use in external media players or media servers. Enable M3U export in <strong>Settings</strong> and use the export button on any playlist card.</p>
                <p class="docs-text">M3U files reference the actual file paths in your library, so they work with any M3U-compatible player. Auto-save can be enabled to regenerate M3U files automatically when playlists are updated.</p>
            </div>
            <div class="docs-subsection" id="sync-discovery">
                <h3 class="docs-subsection-title">Discovery Pipeline</h3>
                <p class="docs-text">For non-Spotify playlists (YouTube, Tidal), tracks need to be <strong>discovered</strong> before syncing. Discovery matches raw titles to official Spotify/iTunes metadata using fuzzy matching with a 0.7 confidence threshold.</p>
                <ol class="docs-steps">
                    <li>Import a playlist (YouTube or Tidal)</li>
                    <li>Click <strong>Discover</strong> on the playlist card (or automate with the "Discover Playlist" action)</li>
                    <li>SoulSync matches each track to official metadata &mdash; results are cached globally</li>
                    <li><strong>Sync</strong> the playlist &mdash; only discovered tracks are included; unmatched tracks are skipped</li>
                </ol>
                <div class="docs-callout tip"><span class="docs-callout-icon">&#x1F4A1;</span><div>Chain automations for hands-free operation: Refresh Playlist &rarr; Playlist Changed &rarr; Discover &rarr; Discovery Complete &rarr; Sync</div></div>
            </div>
        `
    },
    {
        id: 'search',
        title: 'Music Downloads',
        icon: '/static/search.png',
        children: [
            { id: 'search-enhanced', title: 'Enhanced Search' },
            { id: 'search-basic', title: 'Basic Search' },
            { id: 'search-sources', title: 'Download Sources' },
            { id: 'search-downloading', title: 'Downloading Music' },
            { id: 'search-postprocess', title: 'Post-Processing Pipeline' },
            { id: 'search-quality', title: 'Quality Profiles' },
            { id: 'search-manager', title: 'Download Manager' }
        ],
        content: () => `
            <div class="docs-subsection" id="search-enhanced">
                <h3 class="docs-subsection-title">Enhanced Search</h3>
                <p class="docs-text">The default search mode. Type an artist, album, or track name and results appear in a categorized dropdown: <strong>In Your Library</strong>, <strong>Artists</strong>, <strong>Albums</strong>, <strong>Singles & EPs</strong>, and <strong>Tracks</strong>. Results come from Spotify (or iTunes if Spotify is unavailable).</p>
                <ul class="docs-list">
                    <li>Click an <strong>artist</strong> to view their full discography with download buttons on each release</li>
                    <li>Click an <strong>album</strong> to open the download modal with track selection</li>
                    <li>Click a <strong>track</strong> to search Soulseek for that specific song</li>
                </ul>
            </div>
            <div class="docs-subsection" id="search-basic">
                <h3 class="docs-subsection-title">Basic Search</h3>
                <p class="docs-text">Toggle to Basic Search mode for direct Soulseek queries. This shows raw search results with detailed info: format, bitrate, quality score, file size, uploader name, upload speed, and availability.</p>
                <p class="docs-text"><strong>Filters</strong> let you narrow results by type (Albums/Singles), format (FLAC/MP3/OGG/AAC/WMA), and sort by relevance, quality, size, bitrate, duration, or uploader speed.</p>
            </div>
            <div class="docs-subsection" id="search-sources">
                <h3 class="docs-subsection-title">Download Sources</h3>
                <p class="docs-text">SoulSync supports multiple download sources, configurable in <strong>Settings &rarr; Download Settings</strong>:</p>
                <table class="docs-table">
                    <thead><tr><th>Source</th><th>Description</th><th>Best For</th></tr></thead>
                    <tbody>
                        <tr><td><strong>Soulseek</strong></td><td>P2P network via slskd &mdash; largest selection of lossless and rare music</td><td>FLAC, rare tracks, DJ sets</td></tr>
                        <tr><td><strong>YouTube</strong></td><td>YouTube audio extraction via yt-dlp</td><td>Live performances, remixes, tracks not on Soulseek</td></tr>
                        <tr><td><strong>Tidal</strong></td><td>Tidal HiFi streaming rip (requires auth)</td><td>Guaranteed quality, official releases</td></tr>
                        <tr><td><strong>Hybrid</strong></td><td>Tries your primary source first, then automatically falls back to alternates</td><td>Best overall success rate</td></tr>
                    </tbody>
                </table>
                <div class="docs-callout tip"><span class="docs-callout-icon">&#x1F4A1;</span><div><strong>Hybrid mode</strong> is recommended for most users. It tries Soulseek first (best quality), then falls back to YouTube or Tidal if no suitable results are found. The fallback order respects your quality profile settings.</div></div>
                <p class="docs-text"><strong>YouTube settings</strong> include cookies browser selection (for bot detection bypass), download delay (seconds between requests), and minimum confidence threshold for title matching.</p>
            </div>
            <div class="docs-subsection" id="search-downloading">
                <h3 class="docs-subsection-title">Downloading Music</h3>
                <p class="docs-text">When you select an album or track to download, a modal appears with:</p>
                <ul class="docs-list">
                    <li><strong>Album hero</strong> &mdash; cover art, title, artist, year, track count</li>
                    <li><strong>Track list</strong> with checkboxes to select/deselect individual tracks</li>
                    <li><strong>Download progress</strong> with per-track status indicators (searching, downloading, processing, complete, failed)</li>
                </ul>
                <p class="docs-text">Downloads can be started from multiple places: Enhanced Search results, artist discography, Download Missing modal, wishlist auto-processing, and playlist sync.</p>
            </div>
            <div class="docs-subsection" id="search-postprocess">
                <h3 class="docs-subsection-title">Post-Processing Pipeline</h3>
                <p class="docs-text">After a file is downloaded, it goes through an automatic pipeline before appearing in your library:</p>
                <ol class="docs-steps">
                    <li><strong>AcoustID Fingerprint Verification</strong> &mdash; If AcoustID is configured, the downloaded file is fingerprinted and compared against the expected track. Title and artist are fuzzy-matched (title &ge; 70% similarity, artist &ge; 60%). Files that fail verification are <strong>quarantined</strong> instead of added to your library.</li>
                    <li><strong>Metadata Tagging</strong> &mdash; The file is tagged with official metadata: title, artist, album artist, album, track number, disc number, year, genre, and composer. Tags are written using Mutagen (supports MP3, FLAC, OGG, M4A).</li>
                    <li><strong>Cover Art Embedding</strong> &mdash; Album artwork is downloaded from the metadata source and embedded directly into the audio file.</li>
                    <li><strong>File Organization</strong> &mdash; The file is renamed and moved to your transfer path following the template: <code>Artist/Album/TrackNum - Title.ext</code>. For <strong>multi-disc albums</strong>, a <code>Disc N/</code> subfolder is automatically created when the album has more than one disc.</li>
                    <li><strong>Lyrics (LRC)</strong> &mdash; Synced lyrics are fetched from the LRClib API and saved as <code>.lrc</code> sidecar files alongside the audio file. Compatible media players (foobar2000, MusicBee, Plex, etc.) will display time-synced lyrics automatically. Falls back to plain-text lyrics if synced versions aren't available.</li>
                    <li><strong>Lossy Copy</strong> &mdash; If enabled in settings, a lower-bitrate copy is created alongside the original (useful for mobile device syncing).</li>
                    <li><strong>Media Server Scan</strong> &mdash; Your media server (Plex/Jellyfin) is notified to scan for the new file. Navidrome auto-detects changes.</li>
                </ol>
                <div class="docs-callout info"><span class="docs-callout-icon">&#x2139;&#xFE0F;</span><div><strong>Quarantine</strong>: Files that fail AcoustID verification are moved to a quarantine folder instead of your library. You can review quarantined files and manually approve or delete them. The automation engine can trigger notifications when files are quarantined.</div></div>
            </div>
            <div class="docs-subsection" id="search-quality">
                <h3 class="docs-subsection-title">Quality Profiles</h3>
                <p class="docs-text">Configure your quality preferences in <strong>Settings &rarr; Quality Profile</strong>. Quick presets:</p>
                <table class="docs-table">
                    <thead><tr><th>Preset</th><th>Priority</th></tr></thead>
                    <tbody>
                        <tr><td><strong>Audiophile</strong></td><td>FLAC first, then MP3 320</td></tr>
                        <tr><td><strong>Balanced</strong></td><td>MP3 320 first, then FLAC, then MP3 256</td></tr>
                        <tr><td><strong>Space Saver</strong></td><td>MP3 256 first, then MP3 192</td></tr>
                    </tbody>
                </table>
                <p class="docs-text">Each format has configurable bitrate ranges and a priority order. Enable <strong>Fallback</strong> to accept any quality when preferred formats aren't available.</p>
            </div>
            <div class="docs-subsection" id="search-manager">
                <h3 class="docs-subsection-title">Download Manager</h3>
                <p class="docs-text">Toggle the download manager panel (right sidebar) to see all active and completed downloads. Each download shows real-time progress: track name, format, speed, ETA, and a cancel button. Use <strong>Clear Completed</strong> to clean up finished items.</p>
            </div>
        `
    },
    {
        id: 'discover',
        title: 'Discover Artists',
        icon: '/static/discover.png',
        children: [
            { id: 'disc-hero', title: 'Featured Artists' },
            { id: 'disc-playlists', title: 'Discovery Playlists' },
            { id: 'disc-build', title: 'Build Custom Playlist' },
            { id: 'disc-seasonal', title: 'Seasonal & Curated' },
            { id: 'disc-timemachine', title: 'Time Machine' }
        ],
        content: () => `
            <div class="docs-subsection" id="disc-hero">
                <h3 class="docs-subsection-title">Featured Artists</h3>
                <p class="docs-text">The hero slider showcases <strong>recommended artists</strong> based on your watchlist. Each slide shows the artist's image, name, popularity score, genres, and similarity context. Use the arrows or dots to navigate, or click:</p>
                <ul class="docs-list">
                    <li><strong>View Discography</strong> &mdash; Browse the artist's albums and download</li>
                    <li><strong>Add to Watchlist</strong> &mdash; Follow this artist for new release scanning</li>
                    <li><strong>Watch All</strong> &mdash; Add all featured artists to your watchlist at once</li>
                    <li><strong>View Recommended</strong> &mdash; See 50+ similar artists with enrichment data</li>
                </ul>
            </div>
            <div class="docs-subsection" id="disc-playlists">
                <h3 class="docs-subsection-title">Discovery & Personalized Playlists</h3>
                <p class="docs-text">SoulSync generates playlists from two sources: your <strong>discovery pool</strong> (50 similar artists refreshed during watchlist scans) and your <strong>library listening data</strong>:</p>
                <table class="docs-table">
                    <thead><tr><th>Playlist</th><th>Source</th><th>Description</th></tr></thead>
                    <tbody>
                        <tr><td><strong>Popular Picks</strong></td><td>Discovery Pool</td><td>Top tracks from discovery pool artists</td></tr>
                        <tr><td><strong>Hidden Gems</strong></td><td>Discovery Pool</td><td>Rare and deeper cuts from pool artists</td></tr>
                        <tr><td><strong>Discovery Shuffle</strong></td><td>Discovery Pool</td><td>Randomized mix across all pool artists</td></tr>
                        <tr><td><strong>Recently Added</strong></td><td>Library</td><td>Tracks most recently added to your collection</td></tr>
                        <tr><td><strong>Top Tracks</strong></td><td>Library</td><td>Your most-played or highest-rated tracks</td></tr>
                        <tr><td><strong>Forgotten Favorites</strong></td><td>Library</td><td>Tracks you haven't listened to in a while</td></tr>
                        <tr><td><strong>Decade Mixes</strong></td><td>Library</td><td>Tracks grouped by release decade (70s, 80s, 90s, etc.)</td></tr>
                        <tr><td><strong>Daily Mixes</strong></td><td>Library</td><td>Auto-generated daily playlists based on your taste profile</td></tr>
                        <tr><td><strong>Familiar Favorites</strong></td><td>Library</td><td>Well-known tracks from artists you follow</td></tr>
                    </tbody>
                </table>
                <p class="docs-text">Each playlist can be played in the media player, downloaded, or synced to your media server. Genre browsers let you filter discovery pool content by specific genres.</p>
            </div>
            <div class="docs-subsection" id="disc-build">
                <h3 class="docs-subsection-title">Build Custom Playlist</h3>
                <p class="docs-text">Search for 1&ndash;5 artists, select them, and click <strong>Generate</strong> to create a custom playlist from their catalogs. You can then download or sync the generated playlist.</p>
            </div>
            <div class="docs-subsection" id="disc-seasonal">
                <h3 class="docs-subsection-title">Seasonal & Curated Content</h3>
                <p class="docs-text">The Discover page includes auto-generated seasonal content based on the current time of year, plus two curated sections:</p>
                <ul class="docs-list">
                    <li><strong>Fresh Tape</strong> (Release Radar) &mdash; Latest drops from recent releases</li>
                    <li><strong>The Archives</strong> (Discovery Weekly) &mdash; Curated content from your collection</li>
                </ul>
                <p class="docs-text">Both can be synced to your media server with live progress tracking.</p>
            </div>
            <div class="docs-subsection" id="disc-timemachine">
                <h3 class="docs-subsection-title">Time Machine</h3>
                <p class="docs-text">Browse discovery pool content by <strong>decade</strong> &mdash; tabs from the 1950s through the 2020s. Each decade pulls top tracks from pool artists active in that era.</p>
            </div>
        `
    },
    {
        id: 'artists',
        title: 'Artists & Watchlist',
        icon: '/static/artists.png',
        children: [
            { id: 'art-search', title: 'Artist Search' },
            { id: 'art-detail', title: 'Artist Detail & Discography' },
            { id: 'art-watchlist', title: 'Watchlist' },
            { id: 'art-scanning', title: 'New Release Scanning' },
            { id: 'art-wishlist', title: 'Wishlist' },
            { id: 'art-settings', title: 'Watchlist Settings' }
        ],
        content: () => `
            <div class="docs-subsection" id="art-search">
                <h3 class="docs-subsection-title">Artist Search</h3>
                <p class="docs-text">Search for any artist by name. Results show artist cards with images and genres. Results come from Spotify (or iTunes as fallback). Click any card to open the artist detail view.</p>
            </div>
            <div class="docs-subsection" id="art-detail">
                <h3 class="docs-subsection-title">Artist Detail & Discography</h3>
                <p class="docs-text">The artist detail page shows a full discography organized by category:</p>
                <ul class="docs-list">
                    <li><strong>Albums</strong>, <strong>Singles & EPs</strong>, <strong>Compilations</strong>, and <strong>Appearances</strong></li>
                    <li>Each release card shows cover art, title, year, track count, and a <strong>completion percentage</strong> (how many tracks you own)</li>
                    <li>Filter by category, content type (live/compilations/featured), or status (owned/missing)</li>
                    <li>Click any release to open the download modal with track selection</li>
                </ul>
                <p class="docs-text">At the top, <strong>View on</strong> buttons link to the artist on each matched external service (Spotify, Apple Music, MusicBrainz, Deezer, AudioDB, Last.fm, Genius). <strong>Service badges</strong> on artist cards also indicate which services have matched this artist.</p>
                <p class="docs-text"><strong>Similar Artists</strong> appear as clickable bubbles below the discography for further exploration and discovery.</p>
            </div>
            <div class="docs-subsection" id="art-watchlist">
                <h3 class="docs-subsection-title">Watchlist</h3>
                <p class="docs-text">The watchlist tracks artists you want to follow for new releases. When SoulSync scans your watchlist, it checks each artist's discography and adds any new tracks to your <strong>wishlist</strong> for downloading.</p>
                <ul class="docs-list">
                    <li>Add artists from search results, the Discover page hero, or library artist cards</li>
                    <li>Remove artists individually or in bulk</li>
                    <li>Filter your library by Watched / Unwatched status</li>
                    <li>Use <strong>Watch All</strong> to add all recommended artists at once</li>
                </ul>
            </div>
            <div class="docs-subsection" id="art-scanning">
                <h3 class="docs-subsection-title">New Release Scanning</h3>
                <p class="docs-text">Click <strong>Scan for New Releases</strong> or let the system automation handle it (runs every 24 hours). The scan shows a live activity panel with:</p>
                <ul class="docs-list">
                    <li>Current artist being scanned (with image)</li>
                    <li>Current album being processed</li>
                    <li>Recent wishlist additions feed</li>
                    <li>Stats: artists scanned, new tracks found, tracks added to wishlist</li>
                </ul>
            </div>
            <div class="docs-subsection" id="art-wishlist">
                <h3 class="docs-subsection-title">Wishlist</h3>
                <p class="docs-text">The <strong>wishlist</strong> is the queue of tracks waiting to be downloaded. Tracks are added to the wishlist from multiple sources:</p>
                <ul class="docs-list">
                    <li><strong>Watchlist scans</strong> &mdash; New releases from watched artists are automatically added</li>
                    <li><strong>Playlist sync</strong> &mdash; Tracks from mirrored playlists that aren't in your library</li>
                    <li><strong>Manual</strong> &mdash; Individual track or album downloads go through the wishlist</li>
                </ul>
                <p class="docs-text"><strong>Auto-Processing</strong>: The system automation runs every 30 minutes, picking up wishlist items and attempting to download them from your configured source. Failed items are retried with increasing backoff.</p>
                <p class="docs-text"><strong>Manual Processing</strong>: Use the <strong>Process Wishlist</strong> automation action to trigger processing on demand. Options include processing all items, albums only, or singles only.</p>
                <p class="docs-text"><strong>Cleanup</strong>: The <strong>Cleanup Wishlist</strong> action removes duplicates (same track added multiple times) and items you already own in your library.</p>
                <div class="docs-callout info"><span class="docs-callout-icon">&#x2139;&#xFE0F;</span><div>Each wishlist item tracks its source (watchlist scan, playlist sync, manual), number of retry attempts, last error message, and status (pending, downloading, failed, complete).</div></div>
            </div>
            <div class="docs-subsection" id="art-settings">
                <h3 class="docs-subsection-title">Watchlist Settings</h3>
                <p class="docs-text"><strong>Per-Artist Settings</strong> &mdash; Click the config icon on any watched artist to customize what release types to include: Albums, EPs, Singles, Live versions, Remixes, Acoustic versions, Compilations.</p>
                <p class="docs-text"><strong>Global Settings</strong> &mdash; Override all per-artist settings at once. Enable Global Override, select which types to include, and all watchlist scans will follow the global config.</p>
            </div>
        `
    },
    {
        id: 'automations',
        title: 'Automations',
        icon: '/static/automation.png',
        children: [
            { id: 'auto-overview', title: 'Overview' },
            { id: 'auto-builder', title: 'Builder' },
            { id: 'auto-triggers', title: 'All Triggers' },
            { id: 'auto-actions', title: 'All Actions' },
            { id: 'auto-then', title: 'Then-Actions & Signals' },
            { id: 'auto-history', title: 'Execution History' },
            { id: 'auto-system', title: 'System Automations' }
        ],
        content: () => `
            <div class="docs-subsection" id="auto-overview">
                <h3 class="docs-subsection-title">Overview</h3>
                <p class="docs-text">Automations let you schedule tasks and react to events with a visual <strong>WHEN &rarr; DO &rarr; THEN</strong> builder. Create custom workflows like "When a download completes, update the database, then notify me on Discord."</p>
                <p class="docs-text">Each automation card shows its trigger/action flow, last run time, next scheduled run (with countdown), and a <strong>Run Now</strong> button for instant execution.</p>
            </div>
            <div class="docs-subsection" id="auto-builder">
                <h3 class="docs-subsection-title">Builder</h3>
                <p class="docs-text">Click <strong>+ New Automation</strong> to open the builder. Drag or click blocks from the sidebar into the three slots:</p>
                <ol class="docs-steps">
                    <li><strong>WHEN</strong> (Trigger) &mdash; What event starts this automation</li>
                    <li><strong>DO</strong> (Action) &mdash; What task to perform. Optionally add a delay (minutes) before executing.</li>
                    <li><strong>THEN</strong> (Post-Action) &mdash; Up to 3 notification or signal actions after the DO completes</li>
                </ol>
                <p class="docs-text">Add <strong>Conditions</strong> to filter when the automation runs. Match modes: All (AND) or Any (OR). Operators: contains, equals, starts_with, not_contains.</p>
            </div>
            <div class="docs-subsection" id="auto-triggers">
                <h3 class="docs-subsection-title">All Triggers</h3>
                <table class="docs-table">
                    <thead><tr><th>Trigger</th><th>Description</th></tr></thead>
                    <tbody>
                        <tr><td><strong>Schedule</strong></td><td>Run on a timer interval (minutes/hours/days)</td></tr>
                        <tr><td><strong>Daily Time</strong></td><td>Run every day at a specific time</td></tr>
                        <tr><td><strong>Weekly Time</strong></td><td>Run on specific weekdays at a set time</td></tr>
                        <tr><td><strong>App Started</strong></td><td>Fires when SoulSync starts up</td></tr>
                        <tr><td><strong>Track Downloaded</strong></td><td>When a track finishes downloading</td></tr>
                        <tr><td><strong>Download Failed</strong></td><td>When a track permanently fails to download</td></tr>
                        <tr><td><strong>Download Quarantined</strong></td><td>When AcoustID verification rejects a download</td></tr>
                        <tr><td><strong>Batch Complete</strong></td><td>When an album/playlist batch download finishes</td></tr>
                        <tr><td><strong>Wishlist Item Added</strong></td><td>When a track is added to the wishlist</td></tr>
                        <tr><td><strong>Wishlist Processing Done</strong></td><td>When auto-wishlist processing finishes</td></tr>
                        <tr><td><strong>New Release Found</strong></td><td>When a watchlist scan finds new music</td></tr>
                        <tr><td><strong>Watchlist Scan Done</strong></td><td>When the full watchlist scan completes</td></tr>
                        <tr><td><strong>Artist Watched/Unwatched</strong></td><td>When an artist is added to or removed from the watchlist</td></tr>
                        <tr><td><strong>Playlist Synced</strong></td><td>When a playlist sync completes</td></tr>
                        <tr><td><strong>Playlist Changed</strong></td><td>When a mirrored playlist detects changes from the source</td></tr>
                        <tr><td><strong>Discovery Complete</strong></td><td>When playlist track discovery finishes</td></tr>
                        <tr><td><strong>Library Scan Done</strong></td><td>When a media library scan finishes</td></tr>
                        <tr><td><strong>Database Updated</strong></td><td>When a library database refresh finishes</td></tr>
                        <tr><td><strong>Quality/Duplicate Scan Done</strong></td><td>When quality or duplicate scanning finishes</td></tr>
                        <tr><td><strong>Import Complete</strong></td><td>When an album/track import finishes</td></tr>
                        <tr><td><strong>Signal Received</strong></td><td>Custom signal fired by another automation</td></tr>
                    </tbody>
                </table>
            </div>
            <div class="docs-subsection" id="auto-actions">
                <h3 class="docs-subsection-title">All Actions</h3>
                <table class="docs-table">
                    <thead><tr><th>Action</th><th>Description</th></tr></thead>
                    <tbody>
                        <tr><td><strong>Process Wishlist</strong></td><td>Retry failed downloads (all, albums only, or singles only)</td></tr>
                        <tr><td><strong>Scan Watchlist</strong></td><td>Check watched artists for new releases</td></tr>
                        <tr><td><strong>Cleanup Wishlist</strong></td><td>Remove duplicate/owned tracks from wishlist</td></tr>
                        <tr><td><strong>Scan Library</strong></td><td>Trigger a media server library scan</td></tr>
                        <tr><td><strong>Update Database</strong></td><td>Refresh library database (incremental or full)</td></tr>
                        <tr><td><strong>Deep Scan Library</strong></td><td>Full library comparison without losing enrichment data</td></tr>
                        <tr><td><strong>Refresh Mirrored Playlist</strong></td><td>Re-fetch playlist tracks from the source</td></tr>
                        <tr><td><strong>Sync Playlist</strong></td><td>Sync a specific playlist to your media server</td></tr>
                        <tr><td><strong>Discover Playlist</strong></td><td>Find official metadata for playlist tracks</td></tr>
                        <tr><td><strong>Run Duplicate Cleaner</strong></td><td>Scan for and remove duplicate files</td></tr>
                        <tr><td><strong>Run Quality Scan</strong></td><td>Scan for low-quality audio files</td></tr>
                        <tr><td><strong>Clear Quarantine</strong></td><td>Delete all quarantined files</td></tr>
                        <tr><td><strong>Update Discovery</strong></td><td>Refresh the discovery artist pool</td></tr>
                        <tr><td><strong>Backup Database</strong></td><td>Create a timestamped database backup</td></tr>
                        <tr><td><strong>Full Cleanup</strong></td><td>Clear quarantine, queue, staging, and search history</td></tr>
                        <tr><td><strong>Notify Only</strong></td><td>No action &mdash; just trigger notifications</td></tr>
                    </tbody>
                </table>
            </div>
            <div class="docs-subsection" id="auto-then">
                <h3 class="docs-subsection-title">Then-Actions & Signals</h3>
                <p class="docs-text">After the DO action completes, up to <strong>3 THEN actions</strong> run:</p>
                <ul class="docs-list">
                    <li><strong>Discord Webhook</strong> &mdash; Post a message to a Discord channel</li>
                    <li><strong>Pushbullet</strong> &mdash; Push notification to phone/desktop</li>
                    <li><strong>Telegram</strong> &mdash; Send a message via Telegram bot</li>
                    <li><strong>Fire Signal</strong> &mdash; Emit a custom signal that other automations can listen for</li>
                </ul>
                <p class="docs-text">All notification messages support <strong>variable substitution</strong>: <code>{name}</code>, <code>{status}</code>, <code>{time}</code>, <code>{run_count}</code>, and context-specific variables from the action result.</p>
                <p class="docs-text"><strong>Test Notifications</strong>: Use the test button next to any notification then-action to send a test message before saving. This verifies your webhook URL, API key, or bot token is working correctly.</p>
                <div class="docs-callout info"><span class="docs-callout-icon">&#x2139;&#xFE0F;</span><div><strong>Signal chaining</strong> lets you build multi-step workflows. Safety features include cycle detection (DFS), a 5-level chain depth limit, and a 10-second cooldown between signal fires.</div></div>
            </div>
            <div class="docs-subsection" id="auto-history">
                <h3 class="docs-subsection-title">Execution History</h3>
                <p class="docs-text">Each automation card shows its <strong>last run time</strong> and <strong>run count</strong>. For scheduled automations, a countdown timer shows when the next run will occur.</p>
                <p class="docs-text">Use the <strong>Run Now</strong> button on any automation card to execute it immediately, regardless of its schedule. The result (success/failure) updates in real-time on the card.</p>
                <p class="docs-text">The Dashboard activity feed also logs every automation execution with timestamps, so you can review the full history of what ran and when.</p>
            </div>
            <div class="docs-subsection" id="auto-system">
                <h3 class="docs-subsection-title">System Automations</h3>
                <p class="docs-text">SoulSync ships with 10 built-in automations that handle routine maintenance. You can enable/disable them and modify their configs, but you can't delete them or rename them.</p>
                <table class="docs-table">
                    <thead><tr><th>Automation</th><th>Schedule</th></tr></thead>
                    <tbody>
                        <tr><td>Auto-Process Wishlist</td><td>Every 30 minutes</td></tr>
                        <tr><td>Auto-Scan Watchlist</td><td>Every 24 hours</td></tr>
                        <tr><td>Auto-Scan After Downloads</td><td>On batch_complete event</td></tr>
                        <tr><td>Auto-Update Database</td><td>On library_scan_completed event</td></tr>
                        <tr><td>Refresh Beatport Cache</td><td>Every 24 hours</td></tr>
                        <tr><td>Clean Search History</td><td>Every 1 hour</td></tr>
                        <tr><td>Clean Completed Downloads</td><td>Every 5 minutes</td></tr>
                        <tr><td>Auto-Deep Scan Library</td><td>Every 7 days</td></tr>
                        <tr><td>Auto-Backup Database</td><td>Every 3 days</td></tr>
                        <tr><td>Full Cleanup</td><td>Every 12 hours</td></tr>
                    </tbody>
                </table>
            </div>
        `
    },
    {
        id: 'library',
        title: 'Music Library',
        icon: '/static/library.png',
        children: [
            { id: 'lib-standard', title: 'Standard View' },
            { id: 'lib-enhanced', title: 'Enhanced Library Manager' },
            { id: 'lib-matching', title: 'Service Matching' },
            { id: 'lib-tags', title: 'Write Tags to File' },
            { id: 'lib-bulk', title: 'Bulk Operations' },
            { id: 'lib-missing', title: 'Download Missing Tracks' }
        ],
        content: () => `
            <div class="docs-subsection" id="lib-standard">
                <h3 class="docs-subsection-title">Standard View</h3>
                <p class="docs-text">The Library page shows all artists in your collection as cards with images, album/track counts, and <strong>service badges</strong> (Spotify, MusicBrainz, Deezer, AudioDB, iTunes, Last.fm, Genius) indicating which services have matched this artist.</p>
                <p class="docs-text">Use the <strong>search bar</strong>, <strong>alphabet navigation</strong> (A&ndash;Z, #), and <strong>watchlist filter</strong> (All/Watched/Unwatched) to browse. Click any artist card to view their discography.</p>
                <p class="docs-text">The artist detail page shows albums, EPs, and singles as cards with completion percentages. Filter by category, content type (live/compilations/featured), or status (owned/missing). At the top, <strong>View on</strong> buttons link to the artist on each matched external service.</p>
            </div>
            <div class="docs-subsection" id="lib-enhanced">
                <h3 class="docs-subsection-title">Enhanced Library Manager</h3>
                <p class="docs-text">Toggle <strong>Enhanced</strong> on any artist's detail page to access the professional library management tool:</p>
                <ul class="docs-list">
                    <li><strong>Accordion layout</strong> &mdash; Albums as expandable rows showing full track tables</li>
                    <li><strong>Inline editing</strong> &mdash; Click any track title, track number, or BPM to edit in place (Enter saves, Escape cancels)</li>
                    <li><strong>Artist meta panel</strong> &mdash; Editable name, genres, label, style, mood, and summary</li>
                    <li><strong>Sortable columns</strong> &mdash; Click headers to sort by title, duration, format, bitrate, BPM, disc, or track number</li>
                    <li><strong>Play tracks</strong> &mdash; Queue button adds tracks to the media player</li>
                    <li><strong>Delete</strong> &mdash; Remove tracks or albums from the database (files on disk are never touched)</li>
                </ul>
            </div>
            <div class="docs-subsection" id="lib-matching">
                <h3 class="docs-subsection-title">Service Matching</h3>
                <p class="docs-text">In the Enhanced view, each artist, album, and track shows <strong>match status chips</strong> for all 7 services. Click any chip to manually search and link the correct external ID. Run per-service enrichment from the dropdown to pull in metadata from a specific source.</p>
                <p class="docs-text">Matched services show as clickable badges linking to the entity on that service's website.</p>
            </div>
            <div class="docs-subsection" id="lib-tags">
                <h3 class="docs-subsection-title">Write Tags to File</h3>
                <p class="docs-text">Sync your database metadata to actual audio file tags:</p>
                <ol class="docs-steps">
                    <li>Click the <strong>pencil icon</strong> on any track, or use <strong>Write All Tags</strong> for an entire album, or select tracks and use the bulk bar's <strong>Write Tags</strong></li>
                    <li>A <strong>tag preview modal</strong> shows a diff table: current file tags vs. database values</li>
                    <li>Optionally enable <strong>Embed cover art</strong> and <strong>Sync to server</strong></li>
                    <li>Click <strong>Write Tags</strong> to apply changes to the file</li>
                </ol>
                <p class="docs-text">Supports MP3, FLAC, OGG, and M4A via Mutagen. After writing, optional server sync pushes metadata to Plex (per-track update), Jellyfin (library scan), or Navidrome (auto-detects).</p>
            </div>
            <div class="docs-subsection" id="lib-bulk">
                <h3 class="docs-subsection-title">Bulk Operations</h3>
                <p class="docs-text">Select tracks across multiple albums using the checkboxes. The bulk bar appears showing the selection count with actions:</p>
                <ul class="docs-list">
                    <li><strong>Edit Selected</strong> &mdash; Open a modal to apply the same field changes to all selected tracks</li>
                    <li><strong>Write Tags</strong> &mdash; Batch write tags to all selected tracks with live progress</li>
                    <li><strong>Clear Selection</strong> &mdash; Deselect all</li>
                </ul>
            </div>
            <div class="docs-subsection" id="lib-missing">
                <h3 class="docs-subsection-title">Download Missing Tracks</h3>
                <p class="docs-text">From any album card showing missing tracks, click <strong>Download Missing</strong> to open a modal listing all tracks not in your library. Select tracks, choose a download source, and start the download. Progress is tracked per-track with status indicators.</p>
                <p class="docs-text"><strong>Multi-Disc Albums</strong>: Albums with multiple discs are handled automatically. Tracks are organized into <code>Disc N/</code> subfolders within the album directory, preventing track number collisions (e.g., Disc 1 Track 1 vs Disc 2 Track 1). The disc structure is detected from Spotify or iTunes metadata.</p>
            </div>
        `
    },
    {
        id: 'import',
        title: 'Import Music',
        icon: '/static/import.png',
        children: [
            { id: 'imp-setup', title: 'Staging Setup' },
            { id: 'imp-workflow', title: 'Import Workflow' },
            { id: 'imp-singles', title: 'Singles Import' },
            { id: 'imp-matching', title: 'Track Matching' },
            { id: 'imp-textfile', title: 'Import from Text File' }
        ],
        content: () => `
            <div class="docs-subsection" id="imp-setup">
                <h3 class="docs-subsection-title">Staging Setup</h3>
                <p class="docs-text">Set your <strong>staging folder path</strong> in Settings &rarr; Download Settings. Place audio files you want to import into this folder. SoulSync scans the folder and detects albums from the file structure.</p>
                <p class="docs-text">The import page header shows the total files in staging and their combined size.</p>
            </div>
            <div class="docs-subsection" id="imp-workflow">
                <h3 class="docs-subsection-title">Import Workflow</h3>
                <ol class="docs-steps">
                    <li>Place audio files in your staging folder</li>
                    <li>Navigate to the <strong>Import</strong> page &mdash; SoulSync detects and suggests album matches</li>
                    <li>Search for the correct album on Spotify/iTunes if the suggestion is wrong</li>
                    <li><strong>Match tracks</strong> &mdash; Drag-and-drop staged files onto album track slots, or let auto-match attempt it</li>
                    <li>Review the match and click <strong>Confirm</strong> to import &mdash; files are tagged, organized, and added to your library</li>
                </ol>
            </div>
            <div class="docs-subsection" id="imp-singles">
                <h3 class="docs-subsection-title">Singles Import</h3>
                <p class="docs-text">The <strong>Singles</strong> tab handles individual tracks that aren't part of an album structure. Files in the staging root (not in subfolders) appear here. Search for the correct track on Spotify/iTunes, confirm the match, and import. The file is tagged, renamed, and placed in your library.</p>
            </div>
            <div class="docs-subsection" id="imp-matching">
                <h3 class="docs-subsection-title">Track Matching</h3>
                <p class="docs-text">The import matching system compares staged files against official album track lists:</p>
                <ul class="docs-list">
                    <li><strong>Auto-Match</strong> &mdash; Attempts to match files to tracks automatically based on filename, duration, and track order</li>
                    <li><strong>Drag & Drop</strong> &mdash; Manually drag staged files onto the correct album track slots</li>
                    <li><strong>Conflict Detection</strong> &mdash; Highlights when a file matches multiple tracks or when tracks are unmatched</li>
                </ul>
                <p class="docs-text">After matching, the import process tags files with the official metadata (title, artist, album, track number, cover art) and moves them to your transfer path following the standard file organization template.</p>
            </div>
            <div class="docs-subsection" id="imp-textfile">
                <h3 class="docs-subsection-title">Import from Text File</h3>
                <p class="docs-text">Import track lists from <strong>CSV</strong>, <strong>TSV</strong>, or <strong>TXT</strong> files. Upload a file with columns for artist, album, and track title:</p>
                <ol class="docs-steps">
                    <li>Click <strong>Import from File</strong> and select your text file</li>
                    <li>Choose the <strong>separator</strong> (comma, tab, or pipe)</li>
                    <li>Map columns to the correct fields (Artist, Album, Track)</li>
                    <li>SoulSync searches for each track on Spotify/iTunes and adds matches to your wishlist for downloading</li>
                </ol>
            </div>
        `
    },
    {
        id: 'player',
        title: 'Media Player',
        icon: '/static/library.png',
        children: [
            { id: 'player-controls', title: 'Playback Controls' },
            { id: 'player-streaming', title: 'Streaming & Sources' },
            { id: 'player-queue', title: 'Queue & Smart Radio' },
            { id: 'player-shortcuts', title: 'Keyboard Shortcuts' }
        ],
        content: () => `
            <div class="docs-subsection" id="player-controls">
                <h3 class="docs-subsection-title">Playback Controls</h3>
                <p class="docs-text">The sidebar media player is always visible when a track is loaded. It shows album art, track info, a seekable progress bar, and playback controls (play/pause, previous, next, volume, repeat, shuffle).</p>
                <p class="docs-text">Click the sidebar player to open the <strong>Now Playing modal</strong> &mdash; a full-screen experience with large album art, ambient glow (dominant color from cover art), a frequency-driven audio visualizer, and expanded controls.</p>
            </div>
            <div class="docs-subsection" id="player-streaming">
                <h3 class="docs-subsection-title">Streaming & Sources</h3>
                <p class="docs-text">The media player streams audio directly from your connected media server &mdash; no local file access needed:</p>
                <ul class="docs-list">
                    <li><strong>Plex</strong> &mdash; Streams via Plex transcoding API with your Plex token</li>
                    <li><strong>Jellyfin</strong> &mdash; Streams via Jellyfin audio API</li>
                    <li><strong>Navidrome</strong> &mdash; Streams via the Subsonic-compatible API</li>
                </ul>
                <p class="docs-text">The browser auto-detects which audio formats it can play. Album art, track metadata, and ambient colors are all pulled from your server in real-time.</p>
            </div>
            <div class="docs-subsection" id="player-queue">
                <h3 class="docs-subsection-title">Queue & Smart Radio</h3>
                <p class="docs-text">Add tracks to the queue from the Enhanced Library Manager or download results. Manage the queue in the Now Playing modal: reorder, remove individual tracks, or clear all.</p>
                <p class="docs-text"><strong>Smart Radio</strong> mode (toggle in queue header) automatically adds similar tracks when the queue runs out, based on genre, mood, style, and artist similarity. Playback continues seamlessly.</p>
                <p class="docs-text"><strong>Repeat modes</strong>: Off &rarr; Repeat All (loop queue) &rarr; Repeat One. <strong>Shuffle</strong> randomizes the next track from the remaining queue.</p>
            </div>
            <div class="docs-subsection" id="player-shortcuts">
                <h3 class="docs-subsection-title">Keyboard Shortcuts</h3>
                <table class="docs-table">
                    <thead><tr><th>Key</th><th>Action</th></tr></thead>
                    <tbody>
                        <tr><td><span class="docs-kbd">Space</span></td><td>Play / Pause</td></tr>
                        <tr><td><span class="docs-kbd">&#x2192;</span></td><td>Next track</td></tr>
                        <tr><td><span class="docs-kbd">&#x2190;</span></td><td>Previous track</td></tr>
                        <tr><td><span class="docs-kbd">&#x2191;</span></td><td>Volume up</td></tr>
                        <tr><td><span class="docs-kbd">&#x2193;</span></td><td>Volume down</td></tr>
                        <tr><td><span class="docs-kbd">M</span></td><td>Mute / Unmute</td></tr>
                    </tbody>
                </table>
                <p class="docs-text"><strong>Media Session API</strong> &mdash; SoulSync integrates with your OS media controls (lock screen, system tray) for play/pause, next/previous, and seek.</p>
            </div>
        `
    },
    {
        id: 'settings',
        title: 'Settings',
        icon: '/static/settings.png',
        children: [
            { id: 'set-services', title: 'Service Credentials' },
            { id: 'set-media', title: 'Media Server Setup' },
            { id: 'set-download', title: 'Download Settings' },
            { id: 'set-processing', title: 'Processing & Organization' },
            { id: 'set-quality', title: 'Quality Profiles' },
            { id: 'set-other', title: 'Other Settings' }
        ],
        content: () => `
            <div class="docs-subsection" id="set-services">
                <h3 class="docs-subsection-title">Service Credentials</h3>
                <p class="docs-text">Configure credentials for each external service. All fields are saved to your local config &mdash; nothing is sent to external servers except during actual API calls. Each service has a <strong>Test Connection</strong> button to verify your credentials are working.</p>
                <ul class="docs-list">
                    <li><strong>Spotify</strong> &mdash; Client ID + Secret from developer.spotify.com, then click Authenticate to complete OAuth flow</li>
                    <li><strong>Soulseek (slskd)</strong> &mdash; Your slskd instance URL + API key</li>
                    <li><strong>Tidal</strong> &mdash; Client ID + Secret, then Authenticate via OAuth</li>
                    <li><strong>Last.fm</strong> &mdash; API key from last.fm/api</li>
                    <li><strong>Genius</strong> &mdash; Access token from genius.com/api-clients</li>
                    <li><strong>AcoustID</strong> &mdash; API key from acoustid.org (enables fingerprint verification)</li>
                    <li><strong>ListenBrainz</strong> &mdash; Base URL + token for listening history and playlist import</li>
                </ul>
            </div>
            <div class="docs-subsection" id="set-media">
                <h3 class="docs-subsection-title">Media Server Setup</h3>
                <p class="docs-text">Connect your media server so SoulSync can scan your library, trigger updates, stream audio, and sync metadata:</p>
                <table class="docs-table">
                    <thead><tr><th>Server</th><th>Credentials</th><th>Setup Details</th></tr></thead>
                    <tbody>
                        <tr><td><strong>Plex</strong></td><td>URL + Token</td><td>After connecting, select which <strong>Music Library</strong> to use from the dropdown. SoulSync scans this library for your collection and triggers scans after downloads.</td></tr>
                        <tr><td><strong>Jellyfin</strong></td><td>URL + API Key</td><td>Select the <strong>User</strong> and <strong>Music Library</strong> to target. SoulSync uses the Jellyfin API for library scans and can stream audio directly.</td></tr>
                        <tr><td><strong>Navidrome</strong></td><td>URL + Username + Password</td><td>Select the <strong>Music Folder</strong> to monitor. Navidrome auto-detects new files, so SoulSync doesn't need to trigger scans &mdash; just place files in the right folder.</td></tr>
                    </tbody>
                </table>
                <p class="docs-text">The media player streams audio directly from your connected server &mdash; tracks play through your Plex, Jellyfin, or Navidrome instance without needing local file access.</p>
            </div>
            <div class="docs-subsection" id="set-download">
                <h3 class="docs-subsection-title">Download Settings</h3>
                <ul class="docs-list">
                    <li><strong>Download Source Mode</strong> &mdash; Soulseek, YouTube, Tidal, or Hybrid. Hybrid tries your primary source first, then falls back to alternates. See <em>Download Sources</em> in the Music Downloads section for details.</li>
                    <li><strong>Download Path</strong> &mdash; Where files are initially downloaded and processed</li>
                    <li><strong>Transfer Path</strong> &mdash; Where processed files are moved after tagging and organization. Should point to your media server's monitored folder.</li>
                    <li><strong>Staging Path</strong> &mdash; Folder for the Import feature (files placed here appear on the Import page)</li>
                    <li><strong>iTunes Country</strong> &mdash; Storefront region for iTunes/Apple Music lookups (US, GB, FR, JP, etc.). Changes apply immediately to all searches without restarting.</li>
                    <li><strong>Lossy Copy</strong> &mdash; When enabled, creates a lower-bitrate copy (MP3) of every downloaded file alongside the original. Useful for syncing to mobile devices or streaming servers with bandwidth constraints. The copy is placed in a configurable output folder.</li>
                    <li><strong>Content Filtering</strong> &mdash; Toggle explicit content filtering to control whether explicit tracks appear in search results and downloads.</li>
                </ul>
            </div>
            <div class="docs-subsection" id="set-processing">
                <h3 class="docs-subsection-title">Processing & Organization</h3>
                <p class="docs-text">Control how downloaded files are processed and organized:</p>
                <ul class="docs-list">
                    <li><strong>AcoustID Verification</strong> &mdash; Toggle on/off. When enabled, every download is fingerprinted and compared against the expected track. Failed matches are quarantined.</li>
                    <li><strong>Metadata Enhancement</strong> &mdash; Master toggle for all enrichment workers. When disabled, no background metadata fetching occurs.</li>
                    <li><strong>Embed Album Art</strong> &mdash; Automatically embed cover art into audio file tags during post-processing.</li>
                    <li><strong>File Organization</strong> &mdash; Toggle automatic file renaming and folder placement. When disabled, files stay in the download folder as-is.</li>
                    <li><strong>Path Template</strong> &mdash; Customize the folder structure using variables: <code>{artist}</code>, <code>{album}</code>, <code>{title}</code>, <code>{track_number}</code>, <code>{year}</code>, <code>{genre}</code>. Default: <code>{artist}/{album}/{track_number} - {title}</code></li>
                    <li><strong>Disc Label</strong> &mdash; Customize the multi-disc subfolder prefix (default: "Disc"). Multi-disc albums create <code>Disc 1/</code>, <code>Disc 2/</code>, etc.</li>
                    <li><strong>Soulseek Search Timeout</strong> &mdash; How long to wait for Soulseek search results before giving up (seconds).</li>
                    <li><strong>Discovery Lookback Period</strong> &mdash; How many weeks back to check for new releases during watchlist scans.</li>
                </ul>
            </div>
            <div class="docs-subsection" id="set-quality">
                <h3 class="docs-subsection-title">Quality Profiles</h3>
                <p class="docs-text">Set your preferred audio quality with presets (Audiophile/Balanced/Space Saver) or custom configuration per format. Each format has a configurable bitrate range and priority order. Enable Fallback to accept any quality when nothing matches.</p>
            </div>
            <div class="docs-subsection" id="set-other">
                <h3 class="docs-subsection-title">Other Settings</h3>
                <ul class="docs-list">
                    <li><strong>YouTube Configuration</strong> &mdash; Select cookies browser (Chrome, Firefox, Edge) for bot detection bypass, set download delay (seconds between requests), and minimum confidence threshold for title matching</li>
                    <li><strong>UI Appearance</strong> &mdash; Custom accent colors with persistent preference. Changes apply immediately across the entire interface.</li>
                    <li><strong>API Keys</strong> &mdash; Generate and manage API keys for the REST API. Keys use a <code>sk_</code> prefix and are shown once at creation &mdash; only a SHA-256 hash is stored for security.</li>
                    <li><strong>Path Templates</strong> &mdash; Configure how files are organized in your library. The default template is <code>Artist/Album/TrackNum - Title.ext</code></li>
                    <li><strong>WebSocket</strong> &mdash; Real-time status updates are delivered via WebSocket. All downloads, enrichment progress, scan status, and system events push to the UI without polling.</li>
                </ul>
            </div>
        `
    },
    {
        id: 'profiles',
        title: 'Multi-Profile',
        icon: '/static/settings.png',
        children: [
            { id: 'prof-overview', title: 'How Profiles Work' },
            { id: 'prof-manage', title: 'Managing Profiles' }
        ],
        content: () => `
            <div class="docs-subsection" id="prof-overview">
                <h3 class="docs-subsection-title">How Profiles Work</h3>
                <p class="docs-text">SoulSync supports <strong>Netflix-style multiple profiles</strong> for shared households. Each profile gets its own:</p>
                <ul class="docs-list">
                    <li>Watchlist (followed artists)</li>
                    <li>Wishlist (tracks to download)</li>
                    <li>Discovery pool and similar artists</li>
                    <li>Mirrored playlists</li>
                    <li>Queue and listening state</li>
                </ul>
                <p class="docs-text"><strong>Shared across all profiles:</strong> Music library (files and metadata), service credentials, settings, and automations.</p>
                <div class="docs-callout tip"><span class="docs-callout-icon">&#x1F4A1;</span><div>Single-user installs see no changes until a second profile is created. The first profile is automatically the admin.</div></div>
            </div>
            <div class="docs-subsection" id="prof-manage">
                <h3 class="docs-subsection-title">Managing Profiles</h3>
                <ul class="docs-list">
                    <li>Open the profile picker from the sidebar indicator</li>
                    <li>Click <strong>Manage Profiles</strong> to create, edit, or delete profiles</li>
                    <li>Each profile can have a custom name, avatar (image URL or color), and optional 6-digit PIN</li>
                    <li>Set an <strong>Admin PIN</strong> when multiple profiles exist to protect management</li>
                    <li>Profile 1 (admin) cannot be deleted</li>
                </ul>
            </div>
        `
    },
    {
        id: 'api',
        title: 'REST API',
        icon: '/static/settings.png',
        children: [
            { id: 'api-auth', title: 'Authentication' },
            { id: 'api-endpoints', title: 'Key Endpoints' },
            { id: 'api-websocket', title: 'WebSocket Events' }
        ],
        content: () => `
            <div class="docs-subsection" id="api-auth">
                <h3 class="docs-subsection-title">Authentication</h3>
                <p class="docs-text">Generate API keys in <strong>Settings &rarr; API Keys</strong>. Use them via header or query parameter:</p>
                <ul class="docs-list">
                    <li>Header: <code>Authorization: Bearer sk_xxxxx</code></li>
                    <li>Query: <code>?api_key=sk_xxxxx</code></li>
                </ul>
                <p class="docs-text">Keys use a <code>sk_</code> prefix. The raw key is shown once at creation; only a SHA-256 hash is stored.</p>
            </div>
            <div class="docs-subsection" id="api-endpoints">
                <h3 class="docs-subsection-title">Key Endpoints</h3>
                <table class="docs-table">
                    <thead><tr><th>Endpoint</th><th>Description</th></tr></thead>
                    <tbody>
                        <tr><td><code>GET /api/system/status</code></td><td>Uptime and service connectivity</td></tr>
                        <tr><td><code>GET /api/system/stats</code></td><td>Library counts and sizes</td></tr>
                        <tr><td><code>GET /api/library/artists</code></td><td>Paginated artist list with filters</td></tr>
                        <tr><td><code>GET /api/artist-detail/{id}</code></td><td>Full artist info and discography</td></tr>
                        <tr><td><code>POST /api/download</code></td><td>Start a download</td></tr>
                        <tr><td><code>GET /api/downloads/status</code></td><td>Active download status</td></tr>
                        <tr><td><code>POST /api/search</code></td><td>Search Soulseek</td></tr>
                        <tr><td><code>POST /api/enhanced-search</code></td><td>Enhanced metadata search</td></tr>
                        <tr><td><code>GET /api/automations</code></td><td>List all automations</td></tr>
                        <tr><td><code>POST /api/database/backup</code></td><td>Create a backup</td></tr>
                    </tbody>
                </table>
                <p class="docs-text">The full API has 90+ endpoints covering library, downloads, playlists, automations, settings, and more. Use a reverse proxy (Nginx, Caddy, Traefik) for external access with HTTPS.</p>
            </div>
            <div class="docs-subsection" id="api-websocket">
                <h3 class="docs-subsection-title">WebSocket Events</h3>
                <p class="docs-text">SoulSync uses <strong>Socket.IO</strong> for real-time communication. The frontend connects automatically and receives live updates without polling:</p>
                <table class="docs-table">
                    <thead><tr><th>Event</th><th>Description</th></tr></thead>
                    <tbody>
                        <tr><td><code>download_progress</code></td><td>Per-track download progress (speed, ETA, percentage)</td></tr>
                        <tr><td><code>download_complete</code></td><td>Track finished downloading and post-processing</td></tr>
                        <tr><td><code>batch_progress</code></td><td>Album/playlist batch download status</td></tr>
                        <tr><td><code>worker_status</code></td><td>Enrichment worker status (Spotify, MusicBrainz, Deezer, etc.)</td></tr>
                        <tr><td><code>scan_progress</code></td><td>Library scan, quality scan, or duplicate scan progress</td></tr>
                        <tr><td><code>system_status</code></td><td>Service connectivity changes (Spotify rate limit, slskd disconnect)</td></tr>
                        <tr><td><code>activity</code></td><td>System activity feed entries</td></tr>
                    </tbody>
                </table>
                <p class="docs-text">All UI elements that show live progress (download bars, worker icons, scan counters) are driven by these WebSocket events.</p>
            </div>
        `
    }
];

let _docsInitialized = false;

function initializeDocsPage() {
    if (_docsInitialized) return;
    _docsInitialized = true;

    const nav = document.getElementById('docs-nav');
    const content = document.getElementById('docs-content');
    if (!nav || !content) return;

    // Build sidebar nav
    let navHTML = '';
    DOCS_SECTIONS.forEach(section => {
        navHTML += `<div class="docs-nav-section" data-section="${section.id}">`;
        navHTML += `<div class="docs-nav-section-title" data-target="${section.id}">`;
        navHTML += `<img class="docs-nav-icon" src="${section.icon}" onerror="this.style.display='none'">`;
        navHTML += `<span>${section.title}</span>`;
        navHTML += `<span class="docs-nav-arrow">&#x25B6;</span>`;
        navHTML += `</div>`;
        if (section.children && section.children.length) {
            navHTML += `<div class="docs-nav-children" data-parent="${section.id}">`;
            section.children.forEach(child => {
                navHTML += `<div class="docs-nav-child" data-target="${child.id}">${child.title}</div>`;
            });
            navHTML += `</div>`;
        }
        navHTML += `</div>`;
    });
    nav.innerHTML = navHTML;

    // Build content
    let contentHTML = '';
    DOCS_SECTIONS.forEach(section => {
        contentHTML += `<div class="docs-section" id="docs-${section.id}">`;
        contentHTML += `<h2 class="docs-section-title">`;
        contentHTML += `<img class="docs-section-icon" src="${section.icon}" onerror="this.style.display='none'">`;
        contentHTML += `<span>${section.title}</span>`;
        contentHTML += `</h2>`;
        contentHTML += section.content();
        contentHTML += `</div>`;
    });
    content.innerHTML = contentHTML;

    // Suppress scroll spy during click-initiated scrolls
    let _scrollSpySuppressed = false;

    function suppressScrollSpy() {
        _scrollSpySuppressed = true;
        clearTimeout(suppressScrollSpy._timer);
        suppressScrollSpy._timer = setTimeout(() => { _scrollSpySuppressed = false; }, 800);
    }

    // Helper: get element offset relative to a scrollable ancestor
    function getOffsetRelativeTo(el, ancestor) {
        let offset = 0;
        let current = el;
        while (current && current !== ancestor) {
            offset += current.offsetTop;
            current = current.offsetParent;
        }
        return offset;
    }

    // Section title click → expand/collapse children + scroll
    nav.querySelectorAll('.docs-nav-section-title').forEach(title => {
        title.addEventListener('click', () => {
            const sectionId = title.dataset.target;
            const children = nav.querySelector(`.docs-nav-children[data-parent="${sectionId}"]`);

            // Toggle expanded
            const isExpanded = title.classList.contains('expanded');
            // Collapse all
            nav.querySelectorAll('.docs-nav-section-title').forEach(t => t.classList.remove('expanded', 'active'));
            nav.querySelectorAll('.docs-nav-children').forEach(c => c.classList.remove('expanded'));

            if (!isExpanded) {
                title.classList.add('expanded', 'active');
                if (children) children.classList.add('expanded');
            }

            // Scroll to section (suppress scroll spy so it doesn't fight)
            suppressScrollSpy();
            const target = document.getElementById('docs-' + sectionId);
            if (target) target.scrollIntoView({ behavior: 'smooth', block: 'start' });
        });
    });

    // Child click → scroll to subsection
    nav.querySelectorAll('.docs-nav-child').forEach(child => {
        child.addEventListener('click', (e) => {
            e.stopPropagation();
            nav.querySelectorAll('.docs-nav-child').forEach(c => c.classList.remove('active'));
            child.classList.add('active');

            // Keep parent section expanded
            suppressScrollSpy();
            const target = document.getElementById(child.dataset.target);
            if (target) target.scrollIntoView({ behavior: 'smooth', block: 'start' });
        });
    });

    // Search filter
    const searchInput = document.getElementById('docs-search');
    if (searchInput) {
        searchInput.addEventListener('input', () => {
            const q = searchInput.value.toLowerCase().trim();
            document.querySelectorAll('.docs-section').forEach(sec => {
                if (!q) {
                    sec.style.display = '';
                    return;
                }
                sec.style.display = sec.textContent.toLowerCase().includes(q) ? '' : 'none';
            });
            // Also filter nav
            nav.querySelectorAll('.docs-nav-section').forEach(navSec => {
                const sectionId = navSec.dataset.section;
                const docSection = document.getElementById('docs-' + sectionId);
                navSec.style.display = (!q || (docSection && docSection.style.display !== 'none')) ? '' : 'none';
            });
        });
    }

    // Scroll spy — highlight active section in nav
    const docsContent = document.getElementById('docs-content');
    if (docsContent) {
        docsContent.addEventListener('scroll', () => {
            if (_scrollSpySuppressed) return;

            const containerRect = docsContent.getBoundingClientRect();
            const threshold = containerRect.top + 120;
            let activeSection = null;
            let activeChild = null;

            // Find which section is currently in view using getBoundingClientRect
            DOCS_SECTIONS.forEach(section => {
                const el = document.getElementById('docs-' + section.id);
                if (el) {
                    const rect = el.getBoundingClientRect();
                    if (rect.top <= threshold) {
                        activeSection = section.id;
                    }
                }
                if (section.children) {
                    section.children.forEach(child => {
                        const childEl = document.getElementById(child.id);
                        if (childEl) {
                            const childRect = childEl.getBoundingClientRect();
                            if (childRect.top <= threshold) {
                                activeChild = child.id;
                            }
                        }
                    });
                }
            });

            // Update nav highlighting
            nav.querySelectorAll('.docs-nav-section-title').forEach(t => {
                const isActive = t.dataset.target === activeSection;
                t.classList.toggle('active', isActive);
                t.classList.toggle('expanded', isActive);
            });
            nav.querySelectorAll('.docs-nav-children').forEach(c => {
                c.classList.toggle('expanded', c.dataset.parent === activeSection);
            });
            nav.querySelectorAll('.docs-nav-child').forEach(c => {
                c.classList.toggle('active', c.dataset.target === activeChild);
            });
        });
    }

    // Auto-expand first section
    const firstTitle = nav.querySelector('.docs-nav-section-title');
    if (firstTitle) {
        firstTitle.classList.add('expanded', 'active');
        const firstChildren = nav.querySelector('.docs-nav-children');
        if (firstChildren) firstChildren.classList.add('expanded');
    }
}
