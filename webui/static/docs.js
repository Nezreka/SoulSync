// ===============================
// HELP & DOCS PAGE
// ===============================

function docsImg(src, alt) {
    return `<img class="docs-screenshot" src="/static/docs/${src}" alt="${alt}" loading="lazy" onerror="this.style.display='none'">`;
}

const DOCS_SECTIONS = [
    {
        id: 'getting-started',
        title: 'Getting Started',
        icon: '/static/dashboard.png',
        children: [
            { id: 'gs-overview', title: 'Overview' },
            { id: 'gs-first-setup', title: 'First-Time Setup' },
            { id: 'gs-connecting', title: 'Connecting Services' },
            { id: 'gs-interface', title: 'Understanding the Interface' },
            { id: 'gs-folders', title: 'Folder Setup (Downloads & Transfer)' },
            { id: 'gs-docker', title: 'Docker & Deployment' }
        ],
        content: () => `
            <div class="docs-subsection" id="gs-overview">
                <h3 class="docs-subsection-title">Overview</h3>
                <p class="docs-text">SoulSync is a self-hosted music download, sync, and library management platform. It connects to <strong>Spotify</strong>, <strong>Apple Music/iTunes</strong>, <strong>Tidal</strong>, <strong>Qobuz</strong>, <strong>YouTube</strong>, and <strong>Beatport</strong> for metadata, and uses <strong>Soulseek</strong> (via slskd) as the primary download source. Your library is served through <strong>Plex</strong>, <strong>Jellyfin</strong>, or <strong>Navidrome</strong>.</p>
                ${docsImg('gs-overview.png', 'SoulSync dashboard overview')}
                <div class="docs-features">
                    <div class="docs-feature-card"><h4>&#x1F3B5; Download Music</h4><p>Search and download tracks in FLAC, MP3, and more from Soulseek, YouTube, Tidal, or Qobuz, with automatic metadata tagging and file organization.</p></div>
                    <div class="docs-feature-card"><h4>&#x1F504; Playlist Sync</h4><p>Mirror playlists from Spotify, YouTube, Tidal, and Beatport. Discover official metadata and sync to your media server.</p></div>
                    <div class="docs-feature-card"><h4>&#x1F4DA; Library Management</h4><p>Browse, edit, and enrich your music library with metadata from 9 services. Write tags directly to audio files.</p></div>
                    <div class="docs-feature-card"><h4>&#x1F916; Automations</h4><p>Schedule tasks, chain workflows with signals, and get notified via Discord, Pushbullet, or Telegram.</p></div>
                    <div class="docs-feature-card"><h4>&#x1F50D; Artist Discovery</h4><p>Discover new artists via similar-artist recommendations, seasonal playlists, genre exploration, and time-machine browsing.</p></div>
                    <div class="docs-feature-card"><h4>&#x1F440; Watchlist</h4><p>Follow artists and automatically scan for new releases. New tracks are added to your wishlist for download.</p></div>
                </div>
            </div>
            <div class="docs-subsection" id="gs-first-setup">
                <h3 class="docs-subsection-title">First-Time Setup</h3>
                <p class="docs-text">After launching SoulSync, head to the <strong>Settings</strong> page to configure your services. At minimum you need:</p>
                <ol class="docs-steps">
                    <li><strong>Download Source</strong> &mdash; Connect at least one download source: Soulseek (slskd), YouTube, Tidal, or Qobuz. Soulseek offers the best quality selection; YouTube, Tidal, and Qobuz work as alternatives or fallbacks in Hybrid mode.</li>
                    <li><strong>Media Server</strong> &mdash; Connect Plex, Jellyfin, or Navidrome so SoulSync knows where your library lives and can trigger scans.</li>
                    <li><strong>Spotify (Recommended)</strong> &mdash; Connect Spotify for the richest metadata. Create an app at <strong>developer.spotify.com</strong>, enter your Client ID and Secret, then click Authenticate.</li>
                    <li><strong>Download Path</strong> &mdash; Set your download and transfer paths in the Download Settings section. The transfer path should point to your media server's monitored folder.</li>
                </ol>
                ${docsImg('gs-first-setup.png', 'Settings page first-time setup')}
                <div class="docs-callout tip"><span class="docs-callout-icon">&#x1F4A1;</span><div>You can start using SoulSync with just one download source (Soulseek, YouTube, Tidal, or Qobuz). Spotify and other services add metadata enrichment but aren't strictly required &mdash; iTunes/Apple Music is always available as a free fallback.</div></div>
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
                        <tr><td><strong>Tidal</strong></td><td>Download source + playlist import + enrichment</td><td>OAuth &mdash; Client ID + Secret</td></tr>
                        <tr><td><strong>Qobuz</strong></td><td>Download source + enrichment</td><td>Username + Password (app ID auto-fetched)</td></tr>
                        <tr><td><strong>Plex</strong></td><td>Media server &mdash; library scanning, metadata sync, audio streaming</td><td>URL + Token</td></tr>
                        <tr><td><strong>Jellyfin</strong></td><td>Media server &mdash; library scanning, audio streaming</td><td>URL + API Key</td></tr>
                        <tr><td><strong>Navidrome</strong></td><td>Media server &mdash; auto-detects changes, audio streaming</td><td>URL + Username + Password</td></tr>
                        <tr><td><strong>Last.fm</strong></td><td>Enrichment &mdash; listener stats, tags, bios, similar artists</td><td>API Key</td></tr>
                        <tr><td><strong>Genius</strong></td><td>Enrichment &mdash; lyrics, descriptions, alternate names</td><td>Access Token</td></tr>
                        <tr><td><strong>AcoustID</strong></td><td>Audio fingerprint verification of downloads</td><td>API Key</td></tr>
                        <tr><td><strong>ListenBrainz</strong></td><td>Listening history and recommendations</td><td>URL + Token</td></tr>
                    </tbody>
                </table>
                ${docsImg('gs-connecting.png', 'Service credentials connected')}
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
                ${docsImg('gs-interface.png', 'SoulSync interface layout')}
                <p class="docs-text"><strong>Version & Updates</strong>: Click the version number in the sidebar footer to open the <strong>What's New</strong> modal, which shows detailed release notes for every feature and fix. SoulSync automatically checks for updates by comparing your running version against the latest GitHub commit. If an update is available, a banner appears in the modal. Docker users are notified when a new image has been pushed to the repo.</p>
            </div>
            <div class="docs-subsection" id="gs-folders">
                <h3 class="docs-subsection-title">Folder Setup (Downloads & Transfer)</h3>
                <p class="docs-text">SoulSync uses <strong>three folders</strong> to manage your music files. <strong>Most setup issues come from incorrect folder configuration</strong> &mdash; especially in Docker. Read this section carefully.</p>

                <div class="docs-callout warning"><span class="docs-callout-icon">&#x26A0;&#xFE0F;</span><div>
                    <strong>Docker users &mdash; there are TWO steps, not one!</strong><br><br>
                    <strong>Step 1:</strong> Map your volumes in <code>docker-compose.yml</code> &mdash; this makes folders <em>accessible</em> to the container.<br>
                    <strong>Step 2:</strong> Configure the paths in <strong>SoulSync Settings &rarr; Download Settings</strong> &mdash; this tells the app <em>where to look</em>.<br><br>
                    Setting up docker-compose volumes alone is <strong>not enough</strong>. You must also configure the app settings. If you skip Step 2, downloads will complete but nothing will transfer, post-processing will fail silently, and tracks will re-download repeatedly.
                </div></div>

                <h4>The Three Folders</h4>
                <table class="docs-table">
                    <thead><tr><th>Folder</th><th>Default (Docker)</th><th>Purpose</th></tr></thead>
                    <tbody>
                        <tr><td><strong>Download Path</strong></td><td><code>/app/downloads</code></td><td>Where slskd/YouTube/Tidal/Qobuz initially saves downloaded files. This is a <strong>temporary staging area</strong> &mdash; files should not stay here permanently.</td></tr>
                        <tr><td><strong>Transfer Path</strong></td><td><code>/app/Transfer</code></td><td>Where post-processed files are moved after tagging and renaming. This <strong>must</strong> be the folder your media server (Plex/Jellyfin/Navidrome) monitors.</td></tr>
                        <tr><td><strong>Staging Path</strong></td><td><code>/app/Staging</code></td><td>For the Import feature only. Drop audio files here to import them into your library via the Import page.</td></tr>
                    </tbody>
                </table>
                ${docsImg('gs-folders.png', 'Download settings folder configuration')}

                <h4>How Files Flow</h4>
                <div class="docs-callout info"><span class="docs-callout-icon">&#x2139;&#xFE0F;</span><div>
                    <strong>The complete download-to-library pipeline:</strong><br><br>
                    <strong>1.</strong> You search for music in SoulSync and click download<br>
                    <strong>2.</strong> SoulSync tells slskd to download the file &rarr; slskd saves it to its download folder<br>
                    <strong>3.</strong> SoulSync detects the completed download in the <strong>Download Path</strong><br>
                    <strong>4.</strong> Post-processing runs: AcoustID verification &rarr; metadata tagging &rarr; cover art embedding &rarr; lyrics fetch<br>
                    <strong>5.</strong> File is renamed and organized (e.g., <code>Artist/Album/01 - Title.flac</code>)<br>
                    <strong>6.</strong> File is moved from Download Path &rarr; <strong>Transfer Path</strong><br>
                    <strong>7.</strong> Media server scan is triggered &rarr; file appears in your library<br><br>
                    <strong>If any step fails, the pipeline stops.</strong> The most common failure point is Step 3 &mdash; SoulSync can't find the file because the Download Path doesn't match where slskd actually saved it.
                </div></div>

                <h4>Docker Setup: The Full Picture</h4>
                <p class="docs-text">In Docker, every app runs in its own isolated container with its own filesystem. <strong>Volume mounts</strong> in docker-compose create "bridges" between your host folders and the container. But SoulSync doesn't automatically know where those bridges go &mdash; you have to tell it via the Settings page.</p>

                <p class="docs-text">Here's what happens with a properly configured setup:</p>

                <div class="docs-callout info"><span class="docs-callout-icon">&#x1F5C2;&#xFE0F;</span><div>
                    <strong>HOST (your server)</strong><br>
                    <code style="color: var(--accent-primary);">/mnt/data/slskd-downloads/</code> &larr; where slskd saves files on your server<br>
                    <code style="color: #50e050;">/mnt/media/music/</code> &larr; where Plex/Jellyfin/Navidrome watches<br><br>
                    <strong>docker-compose.yml (the bridges)</strong><br>
                    <code style="color: var(--accent-primary);">/mnt/data/slskd-downloads</code>:<code>/app/downloads</code><br>
                    <code style="color: #50e050;">/mnt/media/music</code>:<code>/app/Transfer</code><br><br>
                    <strong>CONTAINER (what SoulSync sees)</strong><br>
                    <code>/app/downloads/</code> &larr; same files as <code style="color: var(--accent-primary);">/mnt/data/slskd-downloads/</code><br>
                    <code>/app/Transfer/</code> &larr; same files as <code style="color: #50e050;">/mnt/media/music/</code><br><br>
                    <strong>SoulSync Settings (what you enter in the app)</strong><br>
                    Download Path: <code>/app/downloads</code><br>
                    Transfer Path: <code>/app/Transfer</code>
                </div></div>

                <h4>The #1 Mistake: Not Configuring App Settings</h4>
                <p class="docs-text">Many users set up their docker-compose volumes correctly but <strong>never open SoulSync Settings to configure the paths</strong>. The app defaults may not match your volume mounts. You must go to <strong>Settings &rarr; Download Settings</strong> and verify that:</p>
                <ul class="docs-list">
                    <li><strong>Download Path</strong> matches where slskd puts completed files <em>inside the container</em> (usually <code>/app/downloads</code>)</li>
                    <li><strong>Transfer Path</strong> matches where you mounted your media library <em>inside the container</em> (usually <code>/app/Transfer</code>)</li>
                </ul>
                <div class="docs-callout warning"><span class="docs-callout-icon">&#x26A0;&#xFE0F;</span><div>
                    <strong>"I set up my docker-compose but nothing transfers"</strong> &mdash; this almost always means the app settings weren't configured. Docker-compose makes the folders accessible. The app settings tell SoulSync where to look. <strong>Both are required.</strong>
                </div></div>

                <h4>The #2 Mistake: Download Path Doesn't Match slskd</h4>
                <p class="docs-text">The <strong>Download Path</strong> in SoulSync must point to the <strong>exact same physical folder</strong> where slskd saves its completed downloads. If they don't match, SoulSync can't find the files and post-processing fails silently.</p>

                <div class="docs-callout info"><span class="docs-callout-icon">&#x2139;&#xFE0F;</span><div>
                    <strong>Both SoulSync and slskd must see the same download folder.</strong><br><br>
                    <strong>slskd container:</strong><br>
                    &bull; slskd downloads to <code>/downloads/complete</code> inside its own container<br>
                    &bull; slskd docker-compose: <code>- /mnt/data/slskd-downloads:/downloads/complete</code><br><br>
                    <strong>SoulSync container:</strong><br>
                    &bull; SoulSync docker-compose: <code>- /mnt/data/slskd-downloads:/app/downloads</code> (same host folder!)<br>
                    &bull; SoulSync Setting: Download Path = <code>/app/downloads</code><br><br>
                    <strong>The key:</strong> both containers mount the <strong>same host folder</strong> (<code>/mnt/data/slskd-downloads</code>). The container-internal paths can be different &mdash; that's fine. What matters is they point to the same physical directory on your server.
                </div></div>

                <h4>The #3 Mistake: Using Host Paths in Settings</h4>
                <p class="docs-text">If you're running in Docker, the paths you enter in SoulSync's Settings page must be <strong>container-side paths</strong> (the right side of the <code>:</code> in your volume mount), <strong>not</strong> host paths (the left side). SoulSync runs inside the container and can only see its own filesystem.</p>

                <table class="docs-table">
                    <thead><tr><th></th><th>Setting Value</th><th>Result</th></tr></thead>
                    <tbody>
                        <tr><td>&#x2705;</td><td><code>/app/downloads</code></td><td>Correct &mdash; this is the container-side path (right side of <code>:</code>)</td></tr>
                        <tr><td>&#x2705;</td><td><code>/app/Transfer</code></td><td>Correct &mdash; this is the container-side path (right side of <code>:</code>)</td></tr>
                        <tr><td>&#x274C;</td><td><code>/mnt/data/slskd-downloads</code></td><td>Wrong &mdash; this is the host path (left side of <code>:</code>), doesn't exist inside the container</td></tr>
                        <tr><td>&#x274C;</td><td><code>/mnt/music</code></td><td>Wrong &mdash; host path, the container can't see this</td></tr>
                        <tr><td>&#x274C;</td><td><code>./downloads</code></td><td>Wrong &mdash; relative path, use the full container path <code>/app/downloads</code></td></tr>
                    </tbody>
                </table>

                <h4>Transfer Path = Media Server's Music Folder</h4>
                <p class="docs-text">Your Transfer Path must ultimately point to the same physical directory your media server monitors. This is how new music appears in Plex/Jellyfin/Navidrome.</p>
                <div class="docs-callout tip"><span class="docs-callout-icon">&#x1F4A1;</span><div>
                    <strong>Example with Plex:</strong><br><br>
                    &bull; Plex monitors <code>/mnt/media/music</code> on the host<br>
                    &bull; SoulSync docker-compose: <code>- /mnt/media/music:/app/Transfer:rw</code><br>
                    &bull; SoulSync Settings: Transfer Path = <code>/app/Transfer</code><br><br>
                    <strong>Result:</strong> SoulSync writes to <code>/app/Transfer</code> inside the container &rarr; appears at <code>/mnt/media/music</code> on the host &rarr; Plex sees it and adds it to your library.
                </div></div>

                <h4>Complete Docker Compose Example (slskd + SoulSync)</h4>
                <p class="docs-text">Here's a working example showing both slskd and SoulSync configured to share the same download folder:</p>
                <div class="docs-callout info"><span class="docs-callout-icon">&#x1F4CB;</span><div>
                    <code><strong># docker-compose.yml</strong></code><br>
                    <code>services:</code><br>
                    <code>&nbsp;&nbsp;slskd:</code><br>
                    <code>&nbsp;&nbsp;&nbsp;&nbsp;image: slskd/slskd:latest</code><br>
                    <code>&nbsp;&nbsp;&nbsp;&nbsp;volumes:</code><br>
                    <code>&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;<span style="color: var(--accent-primary);"># slskd saves completed downloads here</span></code><br>
                    <code>&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;- /mnt/data/slskd-downloads:/downloads</code><br>
                    <code>&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;- /docker/slskd/config:/app</code><br><br>
                    <code>&nbsp;&nbsp;soulsync:</code><br>
                    <code>&nbsp;&nbsp;&nbsp;&nbsp;image: boulderbadgedad/soulsync:latest</code><br>
                    <code>&nbsp;&nbsp;&nbsp;&nbsp;volumes:</code><br>
                    <code>&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;<span style="color: var(--accent-primary);"># SAME host folder as slskd &mdash; this is the key!</span></code><br>
                    <code>&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;- /mnt/data/slskd-downloads:/app/downloads</code><br><br>
                    <code>&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;<span style="color: #50e050;"># Your media server's music folder</span></code><br>
                    <code>&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;- /mnt/media/music:/app/Transfer:rw</code><br><br>
                    <code>&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;<span style="color: #888;"># Config, logs, staging, database</span></code><br>
                    <code>&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;- /docker/soulsync/config:/app/config</code><br>
                    <code>&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;- /docker/soulsync/logs:/app/logs</code><br>
                    <code>&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;- /docker/soulsync/staging:/app/Staging</code><br>
                    <code>&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;- soulsync_database:/app/data</code><br><br>
                    <code><strong># Then in SoulSync Settings:</strong></code><br>
                    <code># Download Path: /app/downloads</code><br>
                    <code># Transfer Path: /app/Transfer</code>
                </div></div>
                ${docsImg('gs-docker.png', 'Docker compose configuration')}

                <h4>Setup Checklist</h4>
                <p class="docs-text">Go through every item. If you miss any single one, the pipeline will break:</p>
                <ol class="docs-steps">
                    <li><strong>slskd download folder is mounted in SoulSync's container</strong> &mdash; Both containers must mount the <strong>same host directory</strong>. The host paths (left side of <code>:</code>) must be identical.</li>
                    <li><strong>Media server's music folder is mounted as Transfer</strong> &mdash; Mount the folder your Plex/Jellyfin/Navidrome monitors as <code>/app/Transfer</code> with <code>:rw</code> permissions.</li>
                    <li><strong>SoulSync Settings are configured</strong> &mdash; Open <strong>Settings &rarr; Download Settings</strong>. Set Download Path to <code>/app/downloads</code> and Transfer Path to <code>/app/Transfer</code> (or whatever container paths you used on the right side of <code>:</code>).</li>
                    <li><strong>slskd URL and API key are set</strong> &mdash; In <strong>Settings &rarr; Soulseek</strong>, enter your slskd URL (e.g., <code>http://slskd:5030</code> or <code>http://host.docker.internal:5030</code>) and API key.</li>
                    <li><strong>PUID/PGID match your host user</strong> &mdash; Run <code>id</code> on your host. Set those values in docker-compose environment variables. Both slskd and SoulSync should use the same PUID/PGID.</li>
                    <li><strong>Test with one track</strong> &mdash; Download a single track. Watch the logs. If it downloads but doesn't transfer, the paths are wrong.</li>
                </ol>

                <h4>Permissions</h4>
                <p class="docs-text">If paths are correct but files still won't transfer, it's usually a permissions issue. SoulSync needs <strong>read + write</strong> access to all three folders.</p>
                <ul class="docs-list">
                    <li>Set <code>PUID</code> and <code>PGID</code> in your docker-compose to match the user that owns your music folders (run <code>id</code> on your host to find your UID/GID &mdash; usually 1000/1000)</li>
                    <li>Ensure the Transfer folder is writable: <code>chmod -R 755 /mnt/media/music</code> (use your actual host path)</li>
                    <li>If using multiple containers (slskd + SoulSync), both must use the <strong>same PUID/PGID</strong> so file permissions are compatible</li>
                    <li>NFS/CIFS/network mounts may need additional permissions &mdash; test with a local folder first to isolate the issue</li>
                </ul>

                <h4>Verifying Your Setup</h4>
                <p class="docs-text">Run these commands to confirm everything is wired up correctly:</p>
                <ol class="docs-steps">
                    <li><strong>Verify downloads are visible:</strong> <code>docker exec soulsync-webui ls -la /app/downloads</code> &mdash; you should see slskd's downloaded files here. If empty or "No such file or directory", your volume mount is wrong.</li>
                    <li><strong>Verify Transfer is writable:</strong> <code>docker exec soulsync-webui touch /app/Transfer/test.txt && echo "OK"</code> &mdash; then check that <code>test.txt</code> appears in your media server's music folder on the host. Clean up after: <code>rm /mnt/media/music/test.txt</code></li>
                    <li><strong>Verify permissions:</strong> <code>docker exec soulsync-webui id</code> &mdash; the uid and gid should match your PUID/PGID values.</li>
                    <li><strong>Verify app settings:</strong> Open SoulSync Settings &rarr; Download Settings. Confirm the Download Path and Transfer Path show container paths (like <code>/app/downloads</code>), not host paths.</li>
                    <li><strong>Test a single download:</strong> Search for a track, download it, and watch the logs. Enable DEBUG logging in Settings for full detail. Check <code>logs/app.log</code> for any path errors.</li>
                </ol>

                <h4>Troubleshooting</h4>
                <table class="docs-table">
                    <thead><tr><th>Symptom</th><th>Likely Cause</th><th>Fix</th></tr></thead>
                    <tbody>
                        <tr><td>Files download but never transfer</td><td>App settings not configured &mdash; docker-compose volumes are set but SoulSync Settings still have defaults or wrong paths</td><td>Open <strong>Settings &rarr; Download Settings</strong> and set Download Path + Transfer Path to your <strong>container-side</strong> mount paths.</td></tr>
                        <tr><td>Post-processing log is empty</td><td>SoulSync can't find the downloaded file at the expected path &mdash; the Download Path in Settings doesn't match where slskd actually saves files inside the container</td><td>Run <code>docker exec soulsync-webui ls /app/downloads</code> to see what's actually there. The Download Path in Settings must match this path exactly.</td></tr>
                        <tr><td>Same tracks downloading multiple times</td><td>Post-processing fails so SoulSync thinks the track was never downloaded successfully. On resume, it tries again.</td><td>Fix the folder paths first. Once post-processing works, files move to Transfer and SoulSync knows they exist.</td></tr>
                        <tr><td>Files not renamed properly</td><td>Post-processing isn't running (path mismatch) or file organization is disabled in Settings</td><td>Verify File Organization is enabled in <strong>Settings &rarr; Processing & Organization</strong>. Fix Download Path first.</td></tr>
                        <tr><td>Permission denied in logs</td><td>Container user can't write to the Transfer folder on the host</td><td>Set PUID/PGID to match the host user that owns the music folder. Run <code>chmod -R 755</code> on the Transfer host folder.</td></tr>
                        <tr><td>Media server doesn't see new files</td><td>Transfer Path doesn't map to the folder your media server monitors</td><td>Ensure the <strong>host path</strong> in your SoulSync volume mount (<code>/mnt/media/music:/app/Transfer</code>) is the same folder Plex/Jellyfin/Navidrome watches.</td></tr>
                        <tr><td>slskd downloads work fine on their own but not through SoulSync</td><td>slskd's download folder and SoulSync's Download Path point to different physical locations</td><td>Both containers must mount the <strong>same host directory</strong>. Check the left side of <code>:</code> in both docker-compose volume entries &mdash; they must match.</td></tr>
                    </tbody>
                </table>
                <div class="docs-callout tip"><span class="docs-callout-icon">&#x1F4A1;</span><div><strong>Still stuck?</strong> Enable DEBUG logging in Settings, download a single track, and check <code>logs/app.log</code>. The post-processing log will show exactly where the file pipeline breaks &mdash; whether it's a path not found, permission denied, or verification failure. If the post-processing log is empty, the issue is almost certainly a path mismatch (SoulSync never found the file to process).</div></div>
            </div>
            <div class="docs-subsection" id="gs-docker">
                <h3 class="docs-subsection-title">Docker & Deployment</h3>
                <p class="docs-text">SoulSync runs in Docker with the following environment variables:</p>
                <table class="docs-table">
                    <thead><tr><th>Variable</th><th>Default</th><th>Description</th></tr></thead>
                    <tbody>
                        <tr><td><code>DATABASE_PATH</code></td><td><code>./database</code></td><td>Directory where the SQLite database is stored. Mount a volume here to persist data across container restarts.</td></tr>
                        <tr><td><code>SOULSYNC_CONFIG_PATH</code></td><td><code>./config</code></td><td>Directory where <code>config.json</code> and the encryption key are stored. Mount a volume here to persist settings.</td></tr>
                        <tr><td><code>SOULSYNC_COMMIT_SHA</code></td><td>(auto)</td><td>Baked in at Docker build time. Used for update detection &mdash; compares against GitHub's latest commit.</td></tr>
                    </tbody>
                </table>
                <h4>Key Volume Mounts</h4>
                <p class="docs-text">Your docker-compose <code>volumes</code> section must include these mappings. The left side is your host path, the right side is where SoulSync sees it inside the container:</p>
                <table class="docs-table">
                    <thead><tr><th>Mount</th><th>Container Path</th><th>What Goes Here</th></tr></thead>
                    <tbody>
                        <tr><td>slskd downloads</td><td><code>/app/downloads</code></td><td>Must be the same physical folder slskd writes completed downloads to. Both containers mount the same host directory.</td></tr>
                        <tr><td>Music library</td><td><code>/app/Transfer</code></td><td>Your media server's monitored music folder. Add <code>:rw</code> to ensure write access.</td></tr>
                        <tr><td>Staging</td><td><code>/app/Staging</code></td><td>(Optional) For the Import feature &mdash; drop files here to import them.</td></tr>
                        <tr><td>Config</td><td><code>/app/config</code></td><td>Stores <code>config.json</code> and encryption key. Persists settings across restarts.</td></tr>
                        <tr><td>Logs</td><td><code>/app/logs</code></td><td>Application logs including <code>app.log</code> and <code>post-processing.log</code>.</td></tr>
                        <tr><td>Database</td><td><code>/app/data</code></td><td><strong>Must use a named volume</strong> (not a host path). Host path mounts can cause database corruption.</td></tr>
                    </tbody>
                </table>
                <div class="docs-callout warning"><span class="docs-callout-icon">&#x26A0;&#xFE0F;</span><div><strong>slskd + SoulSync shared downloads:</strong> If slskd runs in a separate container, both containers must mount the <strong>same host directory</strong> for downloads. A common issue is slskd writing to a path that SoulSync can't read because the volume mounts don't align. Both containers must see the same files. See the <strong>Folder Setup</strong> section above for detailed examples.</div></div>
                <div class="docs-callout warning"><span class="docs-callout-icon">&#x26A0;&#xFE0F;</span><div><strong>Database volume:</strong> Always use a named volume for the database (<code>soulsync_database:/app/data</code>), never a host path mount. Host path mounts can cause SQLite corruption, especially on networked file systems or when permissions don't align.</div></div>
                <p class="docs-text"><strong>Podman / Rootless Docker</strong>: SoulSync supports Podman rootless (keep-id) and rootless Docker setups. The entrypoint handles permission alignment automatically.</p>
                <p class="docs-text"><strong>Config migration</strong>: When upgrading from older versions, SoulSync automatically migrates settings from <code>config.json</code> to the database on first startup. No manual migration is needed.</p>
            </div>
        `
    },
    {
        id: 'workflows',
        title: 'Quick Start Workflows',
        icon: '/static/help.png',
        children: [
            { id: 'wf-first', title: 'What Should I Do First?' },
            { id: 'wf-download', title: 'How to: Download an Album' },
            { id: 'wf-sync', title: 'How to: Sync a Spotify Playlist' },
            { id: 'wf-auto', title: 'How to: Set Up Auto-Downloads' },
            { id: 'wf-import', title: 'How to: Import Existing Music' },
            { id: 'wf-media', title: 'How to: Connect Your Media Server' }
        ],
        content: () => `
            <div class="docs-subsection" id="wf-first">
                <h3 class="docs-subsection-title">What Should I Do First?</h3>
                <p class="docs-text">SoulSync can do a lot, but you don't need to learn everything at once. Here are the <strong>6 essential workflows</strong> that cover 90% of what most users need. Start with whichever one matches your goal, and explore the rest later.</p>
                <div class="docs-workflow-cards">
                    <div class="docs-workflow-card" onclick="document.getElementById('wf-download').scrollIntoView({behavior:'smooth'})">
                        <div class="docs-workflow-card-icon">&#x1F3B5;</div>
                        <div class="docs-workflow-card-title">Download an Album</div>
                        <span class="docs-workflow-card-badge">5 steps</span>
                        <p>Search for any album, pick your tracks, and download in FLAC or MP3 with full metadata.</p>
                        <a class="docs-workflow-card-link" onclick="event.stopPropagation(); document.getElementById('wf-download').scrollIntoView({behavior:'smooth'})">View Guide &rarr;</a>
                    </div>
                    <div class="docs-workflow-card" onclick="document.getElementById('wf-sync').scrollIntoView({behavior:'smooth'})">
                        <div class="docs-workflow-card-icon">&#x1F504;</div>
                        <div class="docs-workflow-card-title">Sync a Spotify Playlist</div>
                        <span class="docs-workflow-card-badge">4 steps</span>
                        <p>Import your Spotify playlists and download every track to your local library.</p>
                        <a class="docs-workflow-card-link" onclick="event.stopPropagation(); document.getElementById('wf-sync').scrollIntoView({behavior:'smooth'})">View Guide &rarr;</a>
                    </div>
                    <div class="docs-workflow-card" onclick="document.getElementById('wf-auto').scrollIntoView({behavior:'smooth'})">
                        <div class="docs-workflow-card-icon">&#x1F916;</div>
                        <div class="docs-workflow-card-title">Set Up Auto-Downloads</div>
                        <span class="docs-workflow-card-badge">4 steps</span>
                        <p>Follow your favorite artists and automatically download their new releases.</p>
                        <a class="docs-workflow-card-link" onclick="event.stopPropagation(); document.getElementById('wf-auto').scrollIntoView({behavior:'smooth'})">View Guide &rarr;</a>
                    </div>
                    <div class="docs-workflow-card" onclick="document.getElementById('wf-import').scrollIntoView({behavior:'smooth'})">
                        <div class="docs-workflow-card-icon">&#x1F4E5;</div>
                        <div class="docs-workflow-card-title">Import Existing Music</div>
                        <span class="docs-workflow-card-badge">5 steps</span>
                        <p>Bring your existing music files into SoulSync with proper tags and organization.</p>
                        <a class="docs-workflow-card-link" onclick="event.stopPropagation(); document.getElementById('wf-import').scrollIntoView({behavior:'smooth'})">View Guide &rarr;</a>
                    </div>
                    <div class="docs-workflow-card" onclick="document.getElementById('wf-media').scrollIntoView({behavior:'smooth'})">
                        <div class="docs-workflow-card-icon">&#x1F4FA;</div>
                        <div class="docs-workflow-card-title">Connect Your Media Server</div>
                        <span class="docs-workflow-card-badge">3 steps</span>
                        <p>Link Plex, Jellyfin, or Navidrome so downloads appear in your library automatically.</p>
                        <a class="docs-workflow-card-link" onclick="event.stopPropagation(); document.getElementById('wf-media').scrollIntoView({behavior:'smooth'})">View Guide &rarr;</a>
                    </div>
                    <div class="docs-workflow-card">
                        <div class="docs-workflow-card-icon">&#x1F3C1;</div>
                        <div class="docs-workflow-card-title">First Things After Setup</div>
                        <span class="docs-workflow-card-badge">5 steps</span>
                        <p>Once connected, do these 5 things to get the most out of SoulSync right away.</p>
                        <a class="docs-workflow-card-link">See below &darr;</a>
                    </div>
                </div>
                <h4>First Things After Setup</h4>
                <ol class="docs-steps">
                    <li><strong>Download one album</strong> &mdash; Verify your folder paths and post-processing work end-to-end</li>
                    <li><strong>Run a Database Update</strong> &mdash; Dashboard &rarr; Database Updater &rarr; Full Refresh to import your existing media server library</li>
                    <li><strong>Add 5&ndash;10 artists to your Watchlist</strong> &mdash; This seeds the discovery pool for recommendations</li>
                    <li><strong>Check the Automations page</strong> &mdash; Enable the system automations you want (auto-process wishlist, auto-scan watchlist, auto-backup)</li>
                    <li><strong>Explore the Discover page</strong> &mdash; Once your watchlist has artists, recommendations and playlists appear here</li>
                </ol>
            </div>
            <div class="docs-subsection" id="wf-download">
                <h3 class="docs-subsection-title">How to: Download an Album</h3>
                <p class="docs-text"><strong>Goal:</strong> Find an album and download it to your library with full metadata, cover art, and proper file organization.</p>
                <p class="docs-text"><strong>Prerequisites:</strong> At least one download source connected (Soulseek, YouTube, Tidal, or Qobuz). Download and Transfer paths configured.</p>
                <ol class="docs-steps">
                    <li><strong>Open Search</strong> &mdash; Click the Search page in the sidebar (make sure Enhanced Search is active)</li>
                    <li><strong>Type the album name</strong> &mdash; Results appear in a categorized dropdown: Artists, Albums, Singles & EPs, Tracks</li>
                    <li><strong>Click the album result</strong> &mdash; The download modal opens showing cover art, tracklist, and album details</li>
                    <li><strong>Select tracks</strong> &mdash; All tracks are selected by default. Uncheck any you don't want</li>
                    <li><strong>Click Download</strong> &mdash; SoulSync searches for each track, downloads, tags, and organizes the files automatically</li>
                </ol>
                ${docsImg('wf-download-album.gif', 'Downloading an album')}
                <p class="docs-text"><strong>Result:</strong> Tracks appear in your Transfer folder as <code>Artist/Album/01 - Title.flac</code> and your media server is notified to scan.</p>
                <div class="docs-callout tip"><span class="docs-callout-icon">&#x1F4A1;</span><div>If a track fails to download, click the retry icon or use the candidate selector to pick an alternative source file from a different user.</div></div>
            </div>
            <div class="docs-subsection" id="wf-sync">
                <h3 class="docs-subsection-title">How to: Sync a Spotify Playlist</h3>
                <p class="docs-text"><strong>Goal:</strong> Import a Spotify playlist and download all its tracks to your local library.</p>
                <ol class="docs-steps">
                    <li><strong>Go to the Sync page</strong> &mdash; Click Sync in the sidebar</li>
                    <li><strong>Click Refresh</strong> &mdash; Your Spotify playlists load automatically (or paste a playlist URL directly)</li>
                    <li><strong>Click Sync on a playlist</strong> &mdash; This adds all missing tracks to your wishlist</li>
                    <li><strong>Wait for auto-processing</strong> &mdash; The wishlist processor runs every 30 minutes and downloads queued tracks. Or click "Process Wishlist" in Automations to start immediately</li>
                </ol>
                ${docsImg('wf-sync-playlist.gif', 'Syncing a Spotify playlist')}
                <div class="docs-callout tip"><span class="docs-callout-icon">&#x1F4A1;</span><div>Use the "Download Missing" button on any playlist to see exactly which tracks are missing and download them all at once.</div></div>
            </div>
            <div class="docs-subsection" id="wf-auto">
                <h3 class="docs-subsection-title">How to: Set Up Auto-Downloads</h3>
                <p class="docs-text"><strong>Goal:</strong> Automatically download new releases from your favorite artists without manual intervention.</p>
                <ol class="docs-steps">
                    <li><strong>Add artists to your Watchlist</strong> &mdash; Search for artists on the Artists page and click the Watch button on each one</li>
                    <li><strong>Go to Automations</strong> &mdash; The built-in "Auto-Scan Watchlist" automation checks for new releases every 24 hours</li>
                    <li><strong>Enable "Auto-Process Wishlist"</strong> &mdash; This automation picks up new releases found by the scan and downloads them every 30 minutes</li>
                    <li><strong>Done!</strong> &mdash; New releases from watched artists are automatically found, queued, downloaded, tagged, and added to your library</li>
                </ol>
                ${docsImg('wf-auto-downloads.gif', 'Setting up auto-downloads')}
                <div class="docs-callout tip"><span class="docs-callout-icon">&#x1F4A1;</span><div>Customize per-artist settings (click the gear icon on a watched artist) to control which release types are included: Albums, EPs, Singles, Live, Remixes, etc.</div></div>
            </div>
            <div class="docs-subsection" id="wf-import">
                <h3 class="docs-subsection-title">How to: Import Existing Music</h3>
                <p class="docs-text"><strong>Goal:</strong> Bring music files you already have into SoulSync with proper metadata and organization.</p>
                <ol class="docs-steps">
                    <li><strong>Place files in your staging folder</strong> &mdash; Put album folders (e.g., <code>Artist - Album/</code>) in the Staging path configured in Settings</li>
                    <li><strong>Go to the Import page</strong> &mdash; SoulSync detects the files and suggests album matches</li>
                    <li><strong>Search for the correct album</strong> &mdash; If the auto-suggestion is wrong, search Spotify/iTunes for the right album</li>
                    <li><strong>Match tracks</strong> &mdash; Drag-and-drop files onto the correct track slots, or click Auto-Match</li>
                    <li><strong>Click Confirm</strong> &mdash; Files are tagged with official metadata, organized, and moved to your library</li>
                </ol>
                ${docsImg('wf-import-music.gif', 'Importing music')}
                <div class="docs-callout tip"><span class="docs-callout-icon">&#x1F4A1;</span><div>For loose singles (not in album folders), use the Singles tab on the Import page.</div></div>
            </div>
            <div class="docs-subsection" id="wf-media">
                <h3 class="docs-subsection-title">How to: Connect Your Media Server</h3>
                <p class="docs-text"><strong>Goal:</strong> Link your media server so downloaded music automatically appears in your library and can be streamed via the built-in player.</p>
                <ol class="docs-steps">
                    <li><strong>Go to Settings</strong> &mdash; Scroll to the Media Server section</li>
                    <li><strong>Enter your server details</strong> &mdash; URL and credentials for Plex (URL + Token), Jellyfin (URL + API Key), or Navidrome (URL + Username + Password). Select your music library from the dropdown</li>
                    <li><strong>Click Test Connection</strong> &mdash; Verify the connection is working. A green checkmark confirms success</li>
                </ol>
                ${docsImg('wf-media-server.gif', 'Connecting media server')}
                <div class="docs-callout tip"><span class="docs-callout-icon">&#x1F4A1;</span><div>Make sure your Transfer Path points to the same folder your media server monitors. This is how new downloads automatically appear in your library.</div></div>
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
                ${docsImg('dash-overview.png', 'Dashboard overview')}
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
                    <div class="docs-feature-card"><h4>Tidal</h4><p>Tidal IDs, artist images, album labels, explicit flags, ISRCs</p></div>
                    <div class="docs-feature-card"><h4>Qobuz</h4><p>Qobuz IDs, artist images, album labels, genres, explicit flags</p></div>
                </div>
                ${docsImg('dash-workers.png', 'Enrichment workers status')}
                <div class="docs-callout info"><span class="docs-callout-icon">&#x2139;&#xFE0F;</span><div>Workers retry "not found" items every 30 days and errored items every 7 days. You can pause/resume any worker from the dashboard.</div></div>
                <p class="docs-text"><strong>Rate Limit Protection</strong>: Workers include smart rate limiting for all APIs. If Spotify returns a rate limit with a Retry-After greater than 60 seconds, the app seamlessly switches to iTunes/Apple Music &mdash; an amber indicator appears in the sidebar, searches automatically use Apple Music, and the enrichment worker pauses. When the ban expires, everything recovers automatically. No action needed from the user.</p>
            </div>
            <div class="docs-subsection" id="dash-tools">
                <h3 class="docs-subsection-title">Tool Cards</h3>
                <p class="docs-text">The dashboard features several tool cards for library maintenance:</p>
                <table class="docs-table">
                    <thead><tr><th>Tool</th><th>What It Does</th></tr></thead>
                    <tbody>
                        <tr><td><strong>Database Updater</strong></td><td>Refreshes your library by scanning your media server. Choose incremental (new only) or full refresh.</td></tr>
                        <tr><td><strong>Metadata Updater</strong></td><td>Triggers all 9 enrichment workers to re-check your library against all connected services.</td></tr>
                        <tr><td><strong>Quality Scanner</strong></td><td>Scans library for tracks below your quality preferences. Shows how many meet standards and finds replacements.</td></tr>
                        <tr><td><strong>Duplicate Cleaner</strong></td><td>Identifies and removes duplicate tracks from your library, freeing up disk space.</td></tr>
                        <tr><td><strong>Discovery Pool</strong></td><td>View and fix matched/failed discovery results across all mirrored playlists.</td></tr>
                        <tr><td><strong>Retag Tool</strong></td><td>Batch retag downloaded files with correct album metadata from Spotify/iTunes.</td></tr>
                        <tr><td><strong>Backup Manager</strong></td><td>Create, download, restore, and delete database backups. Rolling cleanup keeps the 5 most recent.</td></tr>
                    </tbody>
                </table>
                ${docsImg('dash-tools.png', 'Dashboard tool cards')}
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
                ${docsImg('dash-retag.png', 'Retag tool interface')}
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
                ${docsImg('dash-backup.png', 'Backup manager')}
                <p class="docs-text">The system automation <strong>Auto-Backup Database</strong> creates a backup every 3 days automatically. You can adjust the interval in Automations.</p>
            </div>
            <div class="docs-subsection" id="dash-repair">
                <h3 class="docs-subsection-title">Repair & Maintenance</h3>
                <p class="docs-text">Additional maintenance tools accessible from the dashboard:</p>
                <ul class="docs-list">
                    <li><strong>Quality Scanner</strong> &mdash; Scans your entire library and flags tracks below your quality preferences. Shows a breakdown of formats and bitrates, identifies tracks where higher-quality versions may be available, and automatically adds low-quality tracks to your wishlist for re-downloading at better quality.</li>
                    <li><strong>Duplicate Cleaner</strong> &mdash; Identifies duplicate tracks by comparing title, artist, album, and duration. Lets you review duplicates and choose which version to keep (typically the higher-quality one). Frees disk space by removing redundant files.</li>
                    <li><strong>Database Updater</strong> &mdash; Refreshes your library database by scanning your media server. <strong>Incremental</strong> mode only adds new content; <strong>Full Refresh</strong> rebuilds the entire database. <strong>Deep Scan</strong> performs a full comparison without losing any enrichment data from services.</li>
                    <li><strong>Metadata Updater</strong> &mdash; Triggers all enrichment workers simultaneously with reset flags, forcing them to re-check every item in your library against all connected services (MusicBrainz, Spotify, iTunes, Last.fm, Deezer, AudioDB, Genius, Tidal, Qobuz). Useful after connecting a new service or when metadata seems incomplete.</li>
                    <li><strong>Repair Worker</strong> &mdash; Background service that scans recently downloaded folders and repairs track metadata. It reads album IDs from file tags, fetches official tracklists from Spotify or MusicBrainz, and fixes incorrect or missing track numbers. Runs automatically after batch downloads complete and can be paused/resumed from the dashboard.</li>
                </ul>
            </div>
            <div class="docs-subsection" id="dash-activity">
                <h3 class="docs-subsection-title">Activity Feed</h3>
                <p class="docs-text">The activity feed at the bottom of the dashboard shows recent system events: downloads completed, syncs started, settings changed, automation runs, and errors. Events appear in real-time via WebSocket.</p>
                <p class="docs-text">Events include: downloads started/completed/failed, playlist syncs, watchlist scans, automation runs, enrichment worker progress, settings changes, and system errors. The feed shows the 10 most recent events and updates in real-time via WebSocket. Older events are available in the application logs.</p>
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
                ${docsImg('sync-overview.png', 'Playlist sync page')}
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
                ${docsImg('sync-spotify.png', 'Spotify playlists loaded')}
                <div class="docs-callout tip"><span class="docs-callout-icon">&#x1F4A1;</span><div>Spotify-sourced playlists are auto-discovered at confidence 1.0 during refresh &mdash; no separate discovery step needed.</div></div>
            </div>
            <div class="docs-subsection" id="sync-youtube">
                <h3 class="docs-subsection-title">YouTube Playlists</h3>
                <p class="docs-text">Paste a YouTube playlist URL into the input field and click <strong>Parse Playlist</strong>. SoulSync extracts the track list and attempts to match each track to official Spotify/iTunes metadata.</p>
                ${docsImg('sync-youtube.png', 'YouTube playlist import')}
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
                ${docsImg('sync-beatport.png', 'Beatport genre browser')}
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
                ${docsImg('sync-mirror.png', 'Mirrored playlist cards')}
            </div>
            <div class="docs-subsection" id="sync-m3u">
                <h3 class="docs-subsection-title">M3U Export</h3>
                <p class="docs-text">Export any mirrored playlist as an <strong>M3U file</strong> for use in external media players or media servers. Enable M3U export in <strong>Settings</strong> and use the export button on any playlist card.</p>
                <p class="docs-text">M3U files reference the actual file paths in your library, so they work with any M3U-compatible player.</p>
                <p class="docs-text"><strong>Auto-Save</strong> &mdash; When enabled in Settings, M3U files are automatically regenerated every time a playlist is synced or updated. <strong>Manual Export</strong> &mdash; The export button on any playlist modal creates an M3U file on demand, even when auto-save is disabled.</p>
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
                    <li><strong>Preview tracks</strong> &mdash; Play button on search result tracks lets you stream a preview directly from your download source before committing to a download</li>
                </ul>
                ${docsImg('dl-enhanced-search.png', 'Enhanced search results')}
            </div>
            <div class="docs-subsection" id="search-basic">
                <h3 class="docs-subsection-title">Basic Search</h3>
                <p class="docs-text">Toggle to Basic Search mode for direct Soulseek queries. This shows raw search results with detailed info: format, bitrate, quality score, file size, uploader name, upload speed, and availability.</p>
                <p class="docs-text"><strong>Filters</strong> let you narrow results by type (Albums/Singles), format (FLAC/MP3/OGG/AAC/WMA), and sort by relevance, quality, size, bitrate, duration, or uploader speed.</p>
                ${docsImg('dl-basic-search.png', 'Basic Soulseek search')}
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
                        <tr><td><strong>Qobuz</strong></td><td>Qobuz Hi-Res streaming rip (requires auth)</td><td>Audiophile quality, up to 24-bit/192kHz</td></tr>
                        <tr><td><strong>Hybrid</strong></td><td>Tries your primary source first, then automatically falls back to alternates</td><td>Best overall success rate</td></tr>
                    </tbody>
                </table>
                <div class="docs-callout tip"><span class="docs-callout-icon">&#x1F4A1;</span><div><strong>Hybrid mode</strong> is recommended for most users. It tries Soulseek first (best quality), then falls back to YouTube, Tidal, or Qobuz if no suitable results are found. The fallback order and priority are configurable via drag-and-drop in Settings.</div></div>
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
                <p class="docs-text"><strong>Download Candidate Selection</strong>: If a download fails or no suitable source is found, you can view the cached search candidates and manually pick an alternative file from a different user. This lets you recover failed downloads without restarting the entire search.</p>
                ${docsImg('dl-candidates.png', 'Download candidate selection')}
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
                ${docsImg('dl-post-processing.png', 'Post-processing pipeline complete')}
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
                ${docsImg('dl-quality-profiles.png', 'Quality profile settings')}
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
                ${docsImg('disc-hero.png', 'Featured artist hero slider')}
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
                ${docsImg('disc-playlists.png', 'Discovery playlist cards')}
                <p class="docs-text">Each playlist can be played in the media player, downloaded, or synced to your media server.</p>
                <p class="docs-text"><strong>Genre Browser</strong> &mdash; Filter discovery pool content by specific genres. Browse available genres and view top tracks within each genre category.</p>
                <p class="docs-text"><strong>ListenBrainz Playlists</strong> &mdash; If ListenBrainz is configured, the Discover page also shows personalized playlists generated from your listening history: Created For You, Your Playlists, and Collaborative playlists.</p>
            </div>
            <div class="docs-subsection" id="disc-build">
                <h3 class="docs-subsection-title">Build Custom Playlist</h3>
                <p class="docs-text">Search for 1&ndash;5 artists, select them, and click <strong>Generate</strong> to create a custom playlist from their catalogs. You can then download or sync the generated playlist.</p>
                ${docsImg('disc-build-playlist.png', 'Build custom playlist')}
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
                ${docsImg('disc-time-machine.png', 'Time Machine decade browser')}
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
                ${docsImg('art-search.png', 'Artist search results')}
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
                <p class="docs-text">At the top, <strong>View on</strong> buttons link to the artist on each matched external service (Spotify, Apple Music, MusicBrainz, Deezer, AudioDB, Last.fm, Genius, Tidal, Qobuz). <strong>Service badges</strong> on artist cards also indicate which services have matched this artist.</p>
                <p class="docs-text"><strong>Similar Artists</strong> appear as clickable bubbles below the discography for further exploration and discovery.</p>
                ${docsImg('art-detail.png', 'Artist detail page')}
            </div>
            <div class="docs-subsection" id="art-watchlist">
                <h3 class="docs-subsection-title">Watchlist</h3>
                <p class="docs-text">The watchlist tracks artists you want to follow for new releases. When SoulSync scans your watchlist, it checks each artist's discography and adds any new tracks to your <strong>wishlist</strong> for downloading.</p>
                <ul class="docs-list">
                    <li>Add artists from search results, the Discover page hero, or library artist cards</li>
                    <li>Remove artists individually or in bulk</li>
                    <li>Filter your library by Watched / Unwatched status</li>
                    <li>Use <strong>Watch All</strong> to add all recommended artists at once</li>
                    <li><strong>Watch All Unwatched</strong> &mdash; Bulk-add every library artist that isn't already on your watchlist</li>
                </ul>
                ${docsImg('art-watchlist.png', 'Watchlist page')}
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
                ${docsImg('art-scan.png', 'New release scan panel')}
            </div>
            <div class="docs-subsection" id="art-wishlist">
                <h3 class="docs-subsection-title">Wishlist</h3>
                <p class="docs-text">The <strong>wishlist</strong> is the queue of tracks waiting to be downloaded. Tracks are added to the wishlist from multiple sources:</p>
                <ul class="docs-list">
                    <li><strong>Watchlist scans</strong> &mdash; New releases from watched artists are automatically added</li>
                    <li><strong>Playlist sync</strong> &mdash; Tracks from mirrored playlists that aren't in your library</li>
                    <li><strong>Manual</strong> &mdash; Individual track or album downloads go through the wishlist</li>
                </ul>
                <p class="docs-text"><strong>Auto-Processing</strong>: The system automation runs every 30 minutes, picking up wishlist items and attempting to download them from your configured source. Processing alternates between <strong>album</strong> and <strong>singles</strong> cycles &mdash; one run processes albums, the next run processes singles. If one category is empty, it automatically switches to the other. Failed items are retried with increasing backoff.</p>
                <p class="docs-text"><strong>Manual Processing</strong>: Use the <strong>Process Wishlist</strong> automation action to trigger processing on demand. Options include processing all items, albums only, or singles only.</p>
                <p class="docs-text"><strong>Cleanup</strong>: The <strong>Cleanup Wishlist</strong> action removes duplicates (same track added multiple times) and items you already own in your library.</p>
                <div class="docs-callout info"><span class="docs-callout-icon">&#x2139;&#xFE0F;</span><div>Each wishlist item tracks its source (watchlist scan, playlist sync, manual), number of retry attempts, last error message, and status (pending, downloading, failed, complete).</div></div>
                ${docsImg('art-wishlist.png', 'Wishlist queue')}
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
                ${docsImg('auto-overview.png', 'Automations page')}
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
                ${docsImg('auto-builder.png', 'Automation builder')}
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
                        <tr><td><strong>Quality Scan Done</strong></td><td>When quality scan finishes (with counts of quality met vs low quality)</td></tr>
                        <tr><td><strong>Duplicate Scan Done</strong></td><td>When duplicate cleaner finishes (with files scanned, duplicates found, space freed)</td></tr>
                        <tr><td><strong>Import Complete</strong></td><td>When an album/track import finishes</td></tr>
                        <tr><td><strong>Playlist Mirrored</strong></td><td>When a new playlist is mirrored for the first time</td></tr>
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
                        <tr><td><strong>Refresh Beatport Cache</strong></td><td>Scrape Beatport homepage and warm the data cache</td></tr>
                        <tr><td><strong>Clean Search History</strong></td><td>Remove old searches from Soulseek (keeps 50 most recent)</td></tr>
                        <tr><td><strong>Clean Completed Downloads</strong></td><td>Clear completed downloads and empty directories from the download folder</td></tr>
                        <tr><td><strong>Full Cleanup</strong></td><td>Clear quarantine, download queue, staging folder, and search history in one sweep</td></tr>
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
                <p class="docs-text">Use the <strong>Run Now</strong> button on any automation card to execute it immediately, regardless of its schedule. The result (success/failure) updates in real-time on the card. Running automations display a glow effect on their card.</p>
                <p class="docs-text"><strong>Stall detection</strong>: If an automation action runs for more than 2 hours without completing, it is automatically flagged as stalled and terminated to prevent resource leaks.</p>
                <p class="docs-text">The Dashboard activity feed also logs every automation execution with timestamps, so you can review the full history of what ran and when.</p>
                ${docsImg('auto-history.png', 'Automation execution history')}
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
                ${docsImg('auto-system.png', 'System automations')}
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
                <p class="docs-text">The Library page shows all artists in your collection as cards with images, album/track counts, and <strong>service badges</strong> (Spotify, MusicBrainz, Deezer, AudioDB, iTunes, Last.fm, Genius, Tidal, Qobuz) indicating which services have matched this artist.</p>
                <p class="docs-text">Use the <strong>search bar</strong>, <strong>alphabet navigation</strong> (A&ndash;Z, #), and <strong>watchlist filter</strong> (All/Watched/Unwatched) to browse. Click any artist card to view their discography.</p>
                <p class="docs-text">The artist detail page shows albums, EPs, and singles as cards with completion percentages. Filter by category, content type (live/compilations/featured), or status (owned/missing). At the top, <strong>View on</strong> buttons link to the artist on each matched external service.</p>
                ${docsImg('lib-standard.png', 'Library artist grid')}
            </div>
            <div class="docs-subsection" id="lib-enhanced">
                <h3 class="docs-subsection-title">Enhanced Library Manager</h3>
                <p class="docs-text">Toggle <strong>Enhanced</strong> on any artist's detail page to access the professional library management tool. This view is <strong>admin-only</strong> &mdash; non-admin profiles see the Standard view only.</p>
                <ul class="docs-list">
                    <li><strong>Accordion layout</strong> &mdash; Albums as expandable rows showing full track tables</li>
                    <li><strong>Inline editing</strong> &mdash; Click any track title, track number, or BPM to edit in place (Enter saves, Escape cancels)</li>
                    <li><strong>Artist meta panel</strong> &mdash; Editable name, genres, label, style, mood, and summary</li>
                    <li><strong>Sortable columns</strong> &mdash; Click headers to sort by title, duration, format, bitrate, BPM, disc, or track number</li>
                    <li><strong>Play tracks</strong> &mdash; Queue button adds tracks to the media player</li>
                    <li><strong>Delete</strong> &mdash; Remove tracks or albums from the database (files on disk are never touched)</li>
                </ul>
                ${docsImg('lib-enhanced.png', 'Enhanced Library Manager')}
            </div>
            <div class="docs-subsection" id="lib-matching">
                <h3 class="docs-subsection-title">Service Matching</h3>
                <p class="docs-text">In the Enhanced view, each artist, album, and track shows <strong>match status chips</strong> for all 9 services. Click any chip to manually search and link the correct external ID. Run per-service enrichment from the dropdown to pull in metadata from a specific source.</p>
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
                ${docsImg('lib-tags.png', 'Tag preview modal')}
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
                ${docsImg('lib-bulk.png', 'Bulk operations bar')}
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
                <p class="docs-text">Place albums in subfolders (e.g., <code>Artist - Album/</code>) and loose singles at the root level.</p>
                <p class="docs-text">The import page header shows the total files in staging and their combined size.</p>
                ${docsImg('imp-staging.png', 'Import staging page')}
                <div class="docs-callout tip"><span class="docs-callout-icon">&#x1F4A1;</span><div><strong>Files not showing up?</strong> Check that your staging folder path is correct in Settings and that the folder has read permissions. Docker users: make sure the staging volume mount is configured in your docker-compose.yml.</div></div>
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
                ${docsImg('imp-matching.png', 'Track matching interface')}
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
                ${docsImg('imp-textfile.png', 'Text file import')}
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
                ${docsImg('player-nowplaying.png', 'Now Playing modal')}
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
                ${docsImg('player-queue.png', 'Queue panel')}
            </div>
            <div class="docs-subsection" id="player-shortcuts">
                <h3 class="docs-subsection-title">Keyboard Shortcuts</h3>
                <table class="docs-table">
                    <thead><tr><th>Key</th><th>Action</th></tr></thead>
                    <tbody>
                        <tr><td><span class="docs-kbd">Space</span></td><td>Play / Pause</td></tr>
                        <tr><td><span class="docs-kbd">&#x2192;</span></td><td>Seek forward / Next track</td></tr>
                        <tr><td><span class="docs-kbd">&#x2190;</span></td><td>Seek backward / Previous track</td></tr>
                        <tr><td><span class="docs-kbd">&#x2191;</span></td><td>Volume up</td></tr>
                        <tr><td><span class="docs-kbd">&#x2193;</span></td><td>Volume down</td></tr>
                        <tr><td><span class="docs-kbd">M</span></td><td>Mute / Unmute</td></tr>
                        <tr><td><span class="docs-kbd">Escape</span></td><td>Close Now Playing modal</td></tr>
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
                <p class="docs-text">Configure credentials for each external service. All fields are saved to your local database and <strong>encrypted at rest</strong> using a Fernet key generated on first launch. Nothing is sent to external servers except during actual API calls. Each service has a <strong>Test Connection</strong> button to verify your credentials are working.</p>
                <ul class="docs-list">
                    <li><strong>Spotify</strong> &mdash; Client ID + Secret from developer.spotify.com, then click Authenticate to complete OAuth flow</li>
                    <li><strong>Soulseek (slskd)</strong> &mdash; Your slskd instance URL + API key</li>
                    <li><strong>Tidal</strong> &mdash; Client ID + Secret, then Authenticate via OAuth</li>
                    <li><strong>Last.fm</strong> &mdash; API key from last.fm/api</li>
                    <li><strong>Genius</strong> &mdash; Access token from genius.com/api-clients</li>
                    <li><strong>Qobuz</strong> &mdash; Username + Password (app ID is auto-fetched)</li>
                    <li><strong>AcoustID</strong> &mdash; API key from acoustid.org (enables fingerprint verification)</li>
                    <li><strong>ListenBrainz</strong> &mdash; Base URL + token for listening history and playlist import</li>
                </ul>
                ${docsImg('settings-credentials.png', 'Service credentials')}
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
                ${docsImg('settings-media-server.png', 'Media server setup')}
                <p class="docs-text">The media player streams audio directly from your connected server &mdash; tracks play through your Plex, Jellyfin, or Navidrome instance without needing local file access.</p>
                <div class="docs-callout tip"><span class="docs-callout-icon">&#x1F4A1;</span><div><strong>Navidrome users:</strong> If artist images are broken after upgrading, use the <strong>Fix Navidrome URLs</strong> tool in Settings to convert old image URL formats to the correct Subsonic API format.</div></div>
            </div>
            <div class="docs-subsection" id="set-download">
                <h3 class="docs-subsection-title">Download Settings</h3>
                <ul class="docs-list">
                    <li><strong>Download Source Mode</strong> &mdash; Soulseek, YouTube, Tidal, Qobuz, or Hybrid. Hybrid tries your primary source first, then falls back to alternates with configurable priority via drag-and-drop. See <em>Download Sources</em> in the Music Downloads section for details.</li>
                    <li><strong>Download Path</strong> &mdash; The folder where files are initially downloaded. This <strong>must match</strong> the folder your download source (slskd) writes to. In Docker, this is the container-side mount point (e.g., <code>/app/downloads</code>), not the host path. SoulSync monitors this folder for completed downloads to begin post-processing.</li>
                    <li><strong>Transfer Path</strong> &mdash; The final destination for processed music files. After tagging, renaming, and organizing, files are moved here. This <strong>must</strong> point to your media server's monitored music folder (the folder Plex/Jellyfin/Navidrome watches for new content). In Docker, use the container-side path (e.g., <code>/app/Transfer</code>).</li>
                    <li><strong>Staging Path</strong> &mdash; Folder for the Import feature (files placed here appear on the Import page). Separate from the download/transfer pipeline.</li>
                    <li><strong>iTunes Country</strong> &mdash; Storefront region for iTunes/Apple Music lookups (US, GB, FR, JP, etc.). Changes apply immediately to all searches without restarting. ID-based lookups automatically try up to 10 regional storefronts as fallback when the primary country returns no results.</li>
                    <li><strong>Lossy Copy</strong> &mdash; When enabled, creates a lower-bitrate MP3 copy of every downloaded file. Configure the output bitrate (default 320kbps) and output folder. Optionally delete the original lossless file after creating the lossy copy. Useful for syncing to mobile devices or streaming servers with bandwidth constraints.</li>
                    <li><strong>Content Filtering</strong> &mdash; Toggle explicit content filtering to control whether explicit tracks appear in search results and downloads.</li>
                </ul>
                ${docsImg('settings-downloads.png', 'Download settings')}
                <div class="docs-callout warning"><span class="docs-callout-icon">&#x26A0;&#xFE0F;</span><div><strong>Docker users:</strong> Always use container-side paths in these settings (e.g., <code>/app/downloads</code>, <code>/app/Transfer</code>). Never use host paths like <code>/mnt/music</code> &mdash; the container can't access those. Your docker-compose <code>volumes</code> section is where host paths are mapped to container paths. See <strong>Getting Started &rarr; Folder Setup</strong> for a complete walkthrough.</div></div>
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
                ${docsImg('settings-processing.png', 'Processing settings')}
            </div>
            <div class="docs-subsection" id="set-quality">
                <h3 class="docs-subsection-title">Quality Profiles</h3>
                <p class="docs-text">Set your preferred audio quality with presets (Audiophile/Balanced/Space Saver) or custom configuration per format. Each format has a configurable bitrate range and priority order. Enable Fallback to accept any quality when nothing matches.</p>
            </div>
            <div class="docs-subsection" id="set-other">
                <h3 class="docs-subsection-title">Other Settings</h3>
                <ul class="docs-list">
                    <li><strong>YouTube Configuration</strong> &mdash; Select cookies browser (Chrome, Firefox, Edge) for bot detection bypass, set download delay (seconds between requests), and minimum confidence threshold for title matching</li>
                    <li><strong>UI Appearance</strong> &mdash; Custom accent colors with persistent preference. Changes apply immediately across the entire interface. Choose from different <strong>sidebar visualizer types</strong> for the media player audio visualization.</li>
                    <li><strong>API Keys</strong> &mdash; Generate and manage API keys for the REST API. Keys use a <code>sk_</code> prefix and are shown once at creation &mdash; only a SHA-256 hash is stored for security.</li>
                    <li><strong>Path Templates</strong> &mdash; Configure how files are organized in your library. The default template is <code>Artist/Album/TrackNum - Title.ext</code></li>
                    <li><strong>Log Level</strong> &mdash; Set the application log verbosity (DEBUG, INFO, WARNING, ERROR) from the Settings page. Changes take effect immediately without restart. Useful for troubleshooting issues.</li>
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
            { id: 'prof-manage', title: 'Managing Profiles' },
            { id: 'prof-permissions', title: 'Permissions & Page Access' },
            { id: 'prof-home', title: 'Home Page & Preferences' }
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
                    <li>Home page preference</li>
                    <li>Page access permissions (admin-controlled)</li>
                </ul>
                <p class="docs-text"><strong>Shared across all profiles:</strong> Music library (files and metadata), service credentials, settings, and automations.</p>
                <div class="docs-callout tip"><span class="docs-callout-icon">&#x1F4A1;</span><div>Single-user installs see no changes until a second profile is created. The first profile is automatically the admin.</div></div>
            </div>
            <div class="docs-subsection" id="prof-manage">
                <h3 class="docs-subsection-title">Managing Profiles</h3>
                <ul class="docs-list">
                    <li>Open the profile picker by clicking the <strong>profile avatar</strong> in the sidebar header</li>
                    <li>Admin users see <strong>Manage Profiles</strong> to create, edit, or delete profiles</li>
                    <li>Non-admin users see <strong>My Profile</strong> to edit their own name and home page</li>
                    <li>Each profile can have a custom name, avatar (image URL or color), and optional 6-digit PIN</li>
                    <li>Set an <strong>Admin PIN</strong> when multiple profiles exist to protect the admin account</li>
                    <li>Profile 1 (admin) cannot be deleted</li>
                </ul>
                <p class="docs-text">PINs are 4-6 digits. If you forget your PIN, the admin can reset it from Manage Profiles. The admin PIN protects settings and destructive operations when multiple profiles exist.</p>
                ${docsImg('profiles-picker.png', 'Profile picker')}
            </div>
            <div class="docs-subsection" id="prof-permissions">
                <h3 class="docs-subsection-title">Permissions & Page Access</h3>
                <p class="docs-text">Admins can control what each profile has access to. When creating or editing a non-admin profile:</p>
                <ul class="docs-list">
                    <li><strong>Page Access</strong> &mdash; Check or uncheck which sidebar pages the profile can see (Dashboard, Sync, Search, Discover, Artists, Automations, Library, Import). Help & Docs is always accessible. Settings is admin-only.</li>
                    <li><strong>Can Download Music</strong> &mdash; Toggle whether the profile can initiate downloads. When disabled, all download buttons are hidden and the backend blocks download API calls with a 403 error.</li>
                    <li><strong>Enhanced Library Manager</strong> &mdash; The Enhanced view toggle on artist detail pages is only available to admin profiles. Non-admin users see the Standard view only.</li>
                </ul>
                ${docsImg('profiles-permissions.png', 'Profile permissions')}
                <p class="docs-text">If the admin removes a page that was set as a user's home page, the home page automatically resets. Navigation guards prevent users from accessing restricted pages even via direct URL or browser history.</p>
                <div class="docs-callout info"><span class="docs-callout-icon">&#x2139;&#xFE0F;</span><div>Existing profiles created before permissions were added have full access to all pages by default. The admin must explicitly restrict access per profile.</div></div>
            </div>
            <div class="docs-subsection" id="prof-home">
                <h3 class="docs-subsection-title">Home Page & Preferences</h3>
                <p class="docs-text">Each user can choose which page they land on when they log in:</p>
                <ul class="docs-list">
                    <li><strong>Admin profiles</strong> default to the <strong>Dashboard</strong></li>
                    <li><strong>Non-admin profiles</strong> default to the <strong>Discover</strong> page &mdash; a friendlier landing page for non-technical users</li>
                    <li>Any user can change their home page from their profile settings (click profile avatar &rarr; My Profile)</li>
                    <li>The home page selector only shows pages the user has access to</li>
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
            { id: 'api-system', title: 'System & Status' },
            { id: 'api-search', title: 'Search' },
            { id: 'api-downloads', title: 'Downloads' },
            { id: 'api-library', title: 'Library' },
            { id: 'api-library-edit', title: 'Library Editing' },
            { id: 'api-playlists', title: 'Playlists' },
            { id: 'api-watchlist', title: 'Watchlist & Wishlist' },
            { id: 'api-automations', title: 'Automations' },
            { id: 'api-import', title: 'Import' },
            { id: 'api-settings', title: 'Settings' },
            { id: 'api-enrichment', title: 'Enrichment Workers' },
            { id: 'api-profiles', title: 'Profiles' },
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
                <div class="docs-code-label">Example: cURL with API key</div>
                <div class="docs-code-block">curl -H "Authorization: Bearer sk_abc123..." http://localhost:5000/api/system/status</div>
            </div>
            <div class="docs-subsection" id="api-system">
                <h3 class="docs-subsection-title">System & Status</h3>
                <p class="docs-text">Endpoints for checking system health, service connectivity, and library statistics.</p>
                <table class="docs-table">
                    <thead><tr><th>Method</th><th>Endpoint</th><th>Description</th></tr></thead>
                    <tbody>
                        <tr><td>GET</td><td><code>/api/system/status</code></td><td>Uptime, version, and service connectivity</td></tr>
                        <tr><td>GET</td><td><code>/api/system/stats</code></td><td>Library counts (artists, albums, tracks) and total size</td></tr>
                        <tr><td>GET</td><td><code>/api/system/activity</code></td><td>Recent activity feed entries</td></tr>
                        <tr><td>GET</td><td><code>/api/debug-info</code></td><td>Full debug snapshot: services, paths, workers, recent logs</td></tr>
                        <tr><td>GET</td><td><code>/api/version</code></td><td>Current version and update availability</td></tr>
                    </tbody>
                </table>
                <div class="docs-code-label">Response: GET /api/system/stats</div>
                <div class="docs-code-block">{
  "artists": 342,
  "albums": 1205,
  "tracks": 14832,
  "total_size": "128.4 GB",
  "database_size": "45.2 MB"
}</div>
            </div>
            <div class="docs-subsection" id="api-search">
                <h3 class="docs-subsection-title">Search</h3>
                <p class="docs-text">Search for music across metadata sources and Soulseek.</p>
                <table class="docs-table">
                    <thead><tr><th>Method</th><th>Endpoint</th><th>Description</th></tr></thead>
                    <tbody>
                        <tr><td>POST</td><td><code>/api/enhanced-search</code></td><td>Search Spotify/iTunes for artists, albums, and tracks</td></tr>
                        <tr><td>POST</td><td><code>/api/search</code></td><td>Search Soulseek directly for files</td></tr>
                        <tr><td>POST</td><td><code>/api/spotify/search_tracks</code></td><td>Search Spotify for tracks by query</td></tr>
                        <tr><td>POST</td><td><code>/api/itunes/search_tracks</code></td><td>Search iTunes/Apple Music for tracks</td></tr>
                    </tbody>
                </table>
                <div class="docs-code-label">Request: POST /api/enhanced-search</div>
                <div class="docs-code-block">Content-Type: application/json

{
  "query": "Radiohead OK Computer",
  "type": "album"
}</div>
            </div>
            <div class="docs-subsection" id="api-downloads">
                <h3 class="docs-subsection-title">Downloads</h3>
                <p class="docs-text">Start, monitor, and manage music downloads.</p>
                <table class="docs-table">
                    <thead><tr><th>Method</th><th>Endpoint</th><th>Description</th></tr></thead>
                    <tbody>
                        <tr><td>POST</td><td><code>/api/download</code></td><td>Start downloading a track</td></tr>
                        <tr><td>GET</td><td><code>/api/downloads/status</code></td><td>Current download queue and progress</td></tr>
                        <tr><td>POST</td><td><code>/api/download/cancel</code></td><td>Cancel an active download</td></tr>
                        <tr><td>POST</td><td><code>/api/download/album</code></td><td>Start downloading an entire album</td></tr>
                        <tr><td>GET</td><td><code>/api/downloads/history</code></td><td>Completed and failed download history</td></tr>
                    </tbody>
                </table>
                <div class="docs-code-label">Request: POST /api/download</div>
                <div class="docs-code-block">Content-Type: application/json

{
  "artist": "Radiohead",
  "album": "OK Computer",
  "title": "Karma Police",
  "spotify_id": "3SVAN3BRByDmHOhKyIDxfC"
}</div>
                <div class="docs-code-label">Response</div>
                <div class="docs-code-block">{
  "success": true,
  "task_id": "abc123",
  "message": "Download started"
}</div>
            </div>
            <div class="docs-subsection" id="api-library">
                <h3 class="docs-subsection-title">Library</h3>
                <p class="docs-text">Browse and query your music library.</p>
                <table class="docs-table">
                    <thead><tr><th>Method</th><th>Endpoint</th><th>Description</th></tr></thead>
                    <tbody>
                        <tr><td>GET</td><td><code>/api/library/artists</code></td><td>Paginated artist list with search and filters</td></tr>
                        <tr><td>GET</td><td><code>/api/artist-detail/{id}</code></td><td>Full artist info, discography, and match status</td></tr>
                        <tr><td>GET</td><td><code>/api/library/artist/{id}/enhanced</code></td><td>Enhanced view: full tracks, tags, file paths</td></tr>
                        <tr><td>GET</td><td><code>/api/library/album/{id}/tracks</code></td><td>Track list for an album</td></tr>
                        <tr><td>POST</td><td><code>/api/database/update</code></td><td>Trigger library database refresh</td></tr>
                    </tbody>
                </table>
                <div class="docs-code-label">Response: GET /api/library/artists?page=1&per_page=20</div>
                <div class="docs-code-block">{
  "artists": [
    {
      "id": 1,
      "name": "Radiohead",
      "album_count": 9,
      "track_count": 101,
      "image_url": "https://..."
    }
  ],
  "total": 342,
  "page": 1,
  "per_page": 20
}</div>
            </div>
            <div class="docs-subsection" id="api-library-edit">
                <h3 class="docs-subsection-title">Library Editing</h3>
                <p class="docs-text">Edit metadata and write tags to files via the Enhanced Library Manager.</p>
                <table class="docs-table">
                    <thead><tr><th>Method</th><th>Endpoint</th><th>Description</th></tr></thead>
                    <tbody>
                        <tr><td>PUT</td><td><code>/api/library/artist/{id}</code></td><td>Update artist fields (name, genres, label, etc.)</td></tr>
                        <tr><td>PUT</td><td><code>/api/library/album/{id}</code></td><td>Update album fields</td></tr>
                        <tr><td>PUT</td><td><code>/api/library/track/{id}</code></td><td>Update track fields (title, track_number, bpm)</td></tr>
                        <tr><td>POST</td><td><code>/api/library/tracks/batch</code></td><td>Batch update multiple tracks at once</td></tr>
                        <tr><td>GET</td><td><code>/api/library/track/{id}/tag-preview</code></td><td>Preview tag diff (current file tags vs database)</td></tr>
                        <tr><td>POST</td><td><code>/api/library/track/{id}/write-tags</code></td><td>Write database metadata to audio file tags</td></tr>
                        <tr><td>POST</td><td><code>/api/library/tracks/write-tags-batch</code></td><td>Batch write tags to multiple files</td></tr>
                    </tbody>
                </table>
                <div class="docs-code-label">Request: PUT /api/library/track/42</div>
                <div class="docs-code-block">Content-Type: application/json

{
  "title": "Karma Police",
  "track_number": 6,
  "bpm": 75
}</div>
            </div>
            <div class="docs-subsection" id="api-playlists">
                <h3 class="docs-subsection-title">Playlists</h3>
                <p class="docs-text">Import, sync, and manage mirrored playlists from external sources.</p>
                <table class="docs-table">
                    <thead><tr><th>Method</th><th>Endpoint</th><th>Description</th></tr></thead>
                    <tbody>
                        <tr><td>GET</td><td><code>/api/playlists/spotify</code></td><td>List Spotify playlists</td></tr>
                        <tr><td>POST</td><td><code>/api/playlists/parse</code></td><td>Parse a YouTube/Tidal playlist URL</td></tr>
                        <tr><td>GET</td><td><code>/api/playlists/mirrored</code></td><td>List all mirrored playlists</td></tr>
                        <tr><td>POST</td><td><code>/api/playlists/{id}/sync</code></td><td>Sync playlist tracks to wishlist</td></tr>
                        <tr><td>POST</td><td><code>/api/playlists/{id}/discover</code></td><td>Discover official metadata for playlist tracks</td></tr>
                    </tbody>
                </table>
                <div class="docs-code-label">Request: POST /api/playlists/parse</div>
                <div class="docs-code-block">Content-Type: application/json

{
  "url": "https://www.youtube.com/playlist?list=PLrAXtmErZgOeiKm4sgNOknGvNjby9efdf"
}</div>
            </div>
            <div class="docs-subsection" id="api-watchlist">
                <h3 class="docs-subsection-title">Watchlist & Wishlist</h3>
                <p class="docs-text">Manage watched artists and the download wishlist queue.</p>
                <table class="docs-table">
                    <thead><tr><th>Method</th><th>Endpoint</th><th>Description</th></tr></thead>
                    <tbody>
                        <tr><td>GET</td><td><code>/api/watchlist</code></td><td>List all watched artists</td></tr>
                        <tr><td>POST</td><td><code>/api/watchlist/add</code></td><td>Add an artist to the watchlist</td></tr>
                        <tr><td>DELETE</td><td><code>/api/watchlist/{id}</code></td><td>Remove artist from watchlist</td></tr>
                        <tr><td>GET</td><td><code>/api/wishlist</code></td><td>List all wishlist items with status</td></tr>
                        <tr><td>POST</td><td><code>/api/wishlist/process</code></td><td>Trigger wishlist processing</td></tr>
                    </tbody>
                </table>
                <div class="docs-code-label">Request: POST /api/watchlist/add</div>
                <div class="docs-code-block">Content-Type: application/json

{
  "artist_name": "Radiohead",
  "spotify_id": "4Z8W4fKeB5YxbusRsdQVPb"
}</div>
            </div>
            <div class="docs-subsection" id="api-automations">
                <h3 class="docs-subsection-title">Automations</h3>
                <p class="docs-text">Create, manage, and trigger automations.</p>
                <table class="docs-table">
                    <thead><tr><th>Method</th><th>Endpoint</th><th>Description</th></tr></thead>
                    <tbody>
                        <tr><td>GET</td><td><code>/api/automations</code></td><td>List all automations with status</td></tr>
                        <tr><td>POST</td><td><code>/api/automations</code></td><td>Create a new automation</td></tr>
                        <tr><td>PUT</td><td><code>/api/automations/{id}</code></td><td>Update an automation</td></tr>
                        <tr><td>POST</td><td><code>/api/automations/{id}/run</code></td><td>Run an automation immediately</td></tr>
                        <tr><td>DELETE</td><td><code>/api/automations/{id}</code></td><td>Delete a custom automation</td></tr>
                    </tbody>
                </table>
                <div class="docs-code-label">Response: GET /api/automations</div>
                <div class="docs-code-block">[
  {
    "id": 1,
    "name": "Auto-Process Wishlist",
    "trigger_type": "schedule",
    "action_type": "process_wishlist",
    "enabled": true,
    "last_run": "2026-03-12T10:30:00Z",
    "run_count": 142,
    "is_system": true
  }
]</div>
            </div>
            <div class="docs-subsection" id="api-import">
                <h3 class="docs-subsection-title">Import</h3>
                <p class="docs-text">Manage the staging folder and import music files.</p>
                <table class="docs-table">
                    <thead><tr><th>Method</th><th>Endpoint</th><th>Description</th></tr></thead>
                    <tbody>
                        <tr><td>GET</td><td><code>/api/import/staging</code></td><td>List files and folders in the staging directory</td></tr>
                        <tr><td>POST</td><td><code>/api/import/match</code></td><td>Auto-match staged files to album tracks</td></tr>
                        <tr><td>POST</td><td><code>/api/import/confirm</code></td><td>Confirm and execute an import</td></tr>
                        <tr><td>POST</td><td><code>/api/import/search/albums</code></td><td>Search for album matches for staged files</td></tr>
                    </tbody>
                </table>
                <div class="docs-code-label">Response: GET /api/import/staging</div>
                <div class="docs-code-block">{
  "folders": [
    {
      "name": "Radiohead - OK Computer",
      "files": 12,
      "size": "485 MB"
    }
  ],
  "singles": 3,
  "total_size": "512 MB"
}</div>
            </div>
            <div class="docs-subsection" id="api-settings">
                <h3 class="docs-subsection-title">Settings</h3>
                <p class="docs-text">Read and update application settings. Admin-only endpoints.</p>
                <table class="docs-table">
                    <thead><tr><th>Method</th><th>Endpoint</th><th>Description</th></tr></thead>
                    <tbody>
                        <tr><td>GET</td><td><code>/api/settings</code></td><td>Get all current settings</td></tr>
                        <tr><td>POST</td><td><code>/api/settings</code></td><td>Update settings (partial update supported)</td></tr>
                        <tr><td>POST</td><td><code>/api/database/backup</code></td><td>Create a database backup</td></tr>
                        <tr><td>GET</td><td><code>/api/database/backups</code></td><td>List available backups</td></tr>
                        <tr><td>POST</td><td><code>/api/database/restore</code></td><td>Restore from a backup</td></tr>
                    </tbody>
                </table>
                <div class="docs-code-label">Response: POST /api/database/backup</div>
                <div class="docs-code-block">{
  "success": true,
  "filename": "backup_2026-03-12_103000.db",
  "size": "45.2 MB"
}</div>
            </div>
            <div class="docs-subsection" id="api-enrichment">
                <h3 class="docs-subsection-title">Enrichment Workers</h3>
                <p class="docs-text">Monitor and control the background metadata enrichment workers.</p>
                <table class="docs-table">
                    <thead><tr><th>Method</th><th>Endpoint</th><th>Description</th></tr></thead>
                    <tbody>
                        <tr><td>GET</td><td><code>/api/enrichment/status</code></td><td>Status of all enrichment workers</td></tr>
                        <tr><td>POST</td><td><code>/api/enrichment/pause</code></td><td>Pause a specific worker</td></tr>
                        <tr><td>POST</td><td><code>/api/enrichment/resume</code></td><td>Resume a paused worker</td></tr>
                        <tr><td>POST</td><td><code>/api/enrichment/reset</code></td><td>Reset worker progress and re-scan all items</td></tr>
                    </tbody>
                </table>
                <div class="docs-code-label">Response: GET /api/enrichment/status</div>
                <div class="docs-code-block">{
  "workers": {
    "spotify": { "status": "running", "matched": 142, "total": 342, "current": "Radiohead" },
    "musicbrainz": { "status": "paused", "matched": 98, "total": 342 },
    "lastfm": { "status": "running", "matched": 320, "total": 342 }
  }
}</div>
            </div>
            <div class="docs-subsection" id="api-profiles">
                <h3 class="docs-subsection-title">Profiles</h3>
                <p class="docs-text">Manage multi-profile support. Admin-only for create/edit/delete.</p>
                <table class="docs-table">
                    <thead><tr><th>Method</th><th>Endpoint</th><th>Description</th></tr></thead>
                    <tbody>
                        <tr><td>GET</td><td><code>/api/profiles</code></td><td>List all profiles</td></tr>
                        <tr><td>POST</td><td><code>/api/profiles</code></td><td>Create a new profile</td></tr>
                        <tr><td>PUT</td><td><code>/api/profiles/{id}</code></td><td>Update a profile (name, avatar, permissions)</td></tr>
                        <tr><td>DELETE</td><td><code>/api/profiles/{id}</code></td><td>Delete a profile (admin only, cannot delete profile 1)</td></tr>
                        <tr><td>POST</td><td><code>/api/profiles/switch</code></td><td>Switch active profile (PIN required if set)</td></tr>
                    </tbody>
                </table>
                <div class="docs-code-label">Request: POST /api/profiles</div>
                <div class="docs-code-block">Content-Type: application/json

{
  "name": "Family Room",
  "is_admin": false,
  "pin": "1234",
  "allowed_pages": ["search", "discover", "library", "sync"],
  "can_download": true
}</div>
            </div>
            <div class="docs-subsection" id="api-websocket">
                <h3 class="docs-subsection-title">WebSocket Events</h3>
                <p class="docs-text">SoulSync uses <strong>Socket.IO</strong> for real-time communication. The frontend connects automatically and receives live updates without polling. Connect to the same host/port as the web UI.</p>
                <table class="docs-table">
                    <thead><tr><th>Event</th><th>Description</th></tr></thead>
                    <tbody>
                        <tr><td><code>download_progress</code></td><td>Per-track download progress (speed, ETA, percentage)</td></tr>
                        <tr><td><code>download_complete</code></td><td>Track finished downloading and post-processing</td></tr>
                        <tr><td><code>batch_progress</code></td><td>Album/playlist batch download status</td></tr>
                        <tr><td><code>worker_status</code></td><td>Enrichment worker status (Spotify, MusicBrainz, Deezer, Tidal, Qobuz, etc.)</td></tr>
                        <tr><td><code>scan_progress</code></td><td>Library scan, quality scan, or duplicate scan progress</td></tr>
                        <tr><td><code>system_status</code></td><td>Service connectivity changes (Spotify rate limit, slskd disconnect)</td></tr>
                        <tr><td><code>activity</code></td><td>System activity feed entries</td></tr>
                        <tr><td><code>wishlist_update</code></td><td>Wishlist item added, status changed, or removed</td></tr>
                        <tr><td><code>automation_run</code></td><td>Automation started, completed, or failed</td></tr>
                    </tbody>
                </table>
                <p class="docs-text">All UI elements that show live progress (download bars, worker icons, scan counters) are driven by these WebSocket events. External clients can connect using any Socket.IO-compatible library and listen for these same events.</p>
                <div class="docs-code-label">Example: Socket.IO client (JavaScript)</div>
                <div class="docs-code-block">const socket = io('http://localhost:5000');
socket.on('download_progress', (data) => {
  console.log(data.title, data.percent + '%');
});
socket.on('activity', (data) => {
  console.log(data.timestamp, data.message);
});</div>
                <p class="docs-text">The full API has 200+ endpoints covering library, downloads, playlists, automations, profiles, settings, and more. Use a reverse proxy (Nginx, Caddy, Traefik) for external access with HTTPS.</p>
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

    // Add debug info button to sidebar header
    const sidebarHeader = document.querySelector('.docs-sidebar-header');
    if (sidebarHeader) {
        const debugBtn = document.createElement('button');
        debugBtn.className = 'docs-debug-button';
        debugBtn.innerHTML = '&#x1F4CB; Copy Debug Info';
        debugBtn.onclick = async () => {
            try {
                debugBtn.textContent = 'Collecting...';
                const resp = await fetch('/api/debug-info');
                const data = await resp.json();

                let text = 'SoulSync Debug Info\n';
                text += '===================================\n\n';
                text += `Version: ${data.version}\n`;
                text += `OS: ${data.os}${data.docker ? ' (Docker)' : ''}\n`;
                text += `Python: ${data.python}\n\n`;

                text += '-- Services --\n';
                text += `Music Source: ${data.services?.music_source || 'unknown'}\n`;
                text += `Spotify: ${data.services?.spotify_connected ? 'Connected' : 'Disconnected'}${data.services?.spotify_rate_limited ? ' (Rate Limited)' : ''}\n`;
                text += `Media Server: ${data.services?.media_server_type || 'none'} (${data.services?.media_server_connected ? 'Connected' : 'Disconnected'})\n`;
                text += `Soulseek: ${data.services?.soulseek_connected ? 'Connected' : 'Disconnected'}\n`;
                text += `Download Source: ${data.services?.download_source || 'unknown'}\n\n`;

                text += '-- Paths --\n';
                text += `Download: ${data.paths?.download_path || '(not set)'} ${data.paths?.download_path_exists ? '\u2713 exists' : '\u2717 missing'}${data.paths?.download_path_writable ? ' \u2713 writable' : ' \u2717 not writable'}\n`;
                text += `Transfer: ${data.paths?.transfer_folder || '(not set)'} ${data.paths?.transfer_folder_exists ? '\u2713 exists' : '\u2717 missing'}${data.paths?.transfer_folder_writable ? ' \u2713 writable' : ' \u2717 not writable'}\n`;
                text += `Staging: ${data.paths?.staging_folder || '(not set)'} ${data.paths?.staging_folder_exists ? '\u2713 exists' : '\u2717 missing'}\n\n`;

                text += '-- Enrichment Workers --\n';
                if (data.enrichment_workers) {
                    Object.entries(data.enrichment_workers).forEach(([name, status]) => {
                        text += `${name}: ${status}\n`;
                    });
                }
                text += '\n';

                text += `Database: ${data.database_size || 'unknown'}\n`;
                text += `Memory: ${data.memory_usage || 'unknown'}\n\n`;

                text += '-- Recent Logs (last 20 lines) --\n';
                if (data.recent_logs?.length) {
                    data.recent_logs.forEach(line => { text += line + '\n'; });
                }

                await navigator.clipboard.writeText(text);
                debugBtn.innerHTML = '&#x2705; Copied!';
                debugBtn.classList.add('copied');
                setTimeout(() => {
                    debugBtn.innerHTML = '&#x1F4CB; Copy Debug Info';
                    debugBtn.classList.remove('copied');
                }, 2000);
            } catch (err) {
                debugBtn.innerHTML = '&#x274C; Failed';
                console.error('Debug info error:', err);
                setTimeout(() => { debugBtn.innerHTML = '&#x1F4CB; Copy Debug Info'; }, 2000);
            }
        };
        sidebarHeader.appendChild(debugBtn);
    }

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
    const searchInput = document.getElementById('docs-search-input');
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

            // Default to first section if nothing scrolled past threshold yet
            if (!activeSection && DOCS_SECTIONS.length) {
                activeSection = DOCS_SECTIONS[0].id;
                if (DOCS_SECTIONS[0].children && DOCS_SECTIONS[0].children.length) {
                    activeChild = DOCS_SECTIONS[0].children[0].id;
                }
            }

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

    // Reset scroll position and auto-expand first section
    if (docsContent) docsContent.scrollTop = 0;
    const firstTitle = nav.querySelector('.docs-nav-section-title');
    if (firstTitle) {
        firstTitle.classList.add('expanded', 'active');
        const firstChildren = nav.querySelector('.docs-nav-children');
        if (firstChildren) firstChildren.classList.add('expanded');
    }
}

function navigateToDocsSection(sectionId) {
    // Switch to help page
    if (typeof showPage === 'function') showPage('help');
    // Wait for docs to initialize
    setTimeout(() => {
        const target = document.getElementById(sectionId);
        if (target) {
            target.scrollIntoView({ behavior: 'smooth', block: 'start' });
        }
    }, 300);
}
