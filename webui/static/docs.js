// ===============================
// HELP & DOCS PAGE
// ===============================

function docsImg(src, alt) {
    return `<div class="docs-screenshot-wrapper" onclick="openDocsLightbox(this)">
        <img class="docs-screenshot" src="/static/docs/${src}" alt="${alt}" loading="lazy" onerror="this.parentElement.style.display='none'">
        <span class="docs-screenshot-label">${alt}</span>
    </div>`;
}

function openDocsLightbox(wrapper) {
    const img = wrapper.querySelector('.docs-screenshot');
    if (!img) return;
    const existing = document.querySelector('.docs-lightbox');
    if (existing) existing.remove();
    const overlay = document.createElement('div');
    overlay.className = 'docs-lightbox';
    overlay.innerHTML = `<button class="docs-lightbox-close">&times;</button><img src="${img.src}" alt="${img.alt}">`;
    document.body.appendChild(overlay);
    requestAnimationFrame(() => overlay.classList.add('active'));
    const close = () => {
        overlay.classList.remove('active');
        setTimeout(() => overlay.remove(), 250);
    };
    overlay.addEventListener('click', close);
    document.addEventListener('keydown', function handler(e) {
        if (e.key === 'Escape') { close(); document.removeEventListener('keydown', handler); }
    });
}

const DOCS_SECTIONS = [
    {
        id: 'getting-started',
        title: 'Getting Started',
        icon: '/static/dashboard.jpg',
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
                <p class="docs-text">SoulSync is a self-hosted music download, sync, and library management platform. It connects to <strong>Spotify</strong>, <strong>Apple Music/iTunes</strong>, <strong>Deezer</strong>, <strong>Discogs</strong>, <strong>Tidal</strong>, <strong>Qobuz</strong>, <strong>YouTube</strong>, and <strong>Beatport</strong> for metadata, and downloads from <strong>Soulseek</strong>, <strong>YouTube</strong>, <strong>Tidal</strong>, <strong>Qobuz</strong>, <strong>HiFi</strong>, and <strong>Deezer</strong>. Your library is served through <strong>Plex</strong>, <strong>Jellyfin/Emby</strong>, or <strong>Navidrome</strong>.</p>
                ${docsImg('gs-overview.jpg', 'SoulSync dashboard overview')}
                <div class="docs-features">
                    <div class="docs-feature-card"><h4>&#x1F3B5; Download Music</h4><p>Search and download tracks in FLAC, MP3, and more from 6 sources (Soulseek, YouTube, Tidal, Qobuz, HiFi, Deezer), with automatic metadata tagging and file organization.</p></div>
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
                    <li><strong>Download Source</strong> &mdash; Connect at least one download source: Soulseek (slskd), YouTube, Tidal, Qobuz, HiFi, or Deezer. Soulseek offers the best quality selection; the others work as alternatives or fallbacks in Hybrid mode.</li>
                    <li><strong>Media Server</strong> &mdash; Connect Plex, Jellyfin, or Navidrome so SoulSync knows where your library lives and can trigger scans.</li>
                    <li><strong>Spotify (Recommended)</strong> &mdash; Connect Spotify for the richest metadata. Create an app at <strong>developer.spotify.com</strong>, enter your Client ID and Secret, then click Authenticate.</li>
                    <li><strong>Download Path</strong> &mdash; Set your download and transfer paths in the Download Settings section. The transfer path should point to your media server's monitored folder.</li>
                </ol>
                ${docsImg('gs-first-setup.jpg', 'Settings page first-time setup')}
                <div class="docs-callout tip"><span class="docs-callout-icon">&#x1F4A1;</span><div>You can start using SoulSync with just one download source. Spotify and other services add metadata enrichment but aren't strictly required &mdash; iTunes/Apple Music and Deezer are always available as free fallbacks.</div></div>
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
                        <tr><td><strong>Qobuz</strong></td><td>Download source + enrichment</td><td>Username + Password, or Auth Token (from browser DevTools)</td></tr>
                        <tr><td><strong>HiFi</strong></td><td>Download source &mdash; free lossless via community API</td><td>None</td></tr>
                        <tr><td><strong>Deezer</strong></td><td>Download source + metadata fallback + user playlists</td><td>ARL cookie token</td></tr>
                        <tr><td><strong>Discogs</strong></td><td>Enrichment &mdash; genres, styles, labels, catalog numbers, community ratings</td><td>Personal Access Token (free)</td></tr>
                        <tr><td><strong>Plex</strong></td><td>Media server &mdash; library scanning, metadata sync, audio streaming</td><td>URL + Token</td></tr>
                        <tr><td><strong>Jellyfin / Emby</strong></td><td>Media server &mdash; library scanning, playlist sync, audio streaming</td><td>URL + API Key</td></tr>
                        <tr><td><strong>Navidrome</strong></td><td>Media server &mdash; auto-detects changes, audio streaming</td><td>URL + Username + Password</td></tr>
                        <tr><td><strong>Last.fm</strong></td><td>Enrichment &mdash; listener stats, tags, bios, similar artists</td><td>API Key</td></tr>
                        <tr><td><strong>Genius</strong></td><td>Enrichment &mdash; lyrics, descriptions, alternate names</td><td>Access Token</td></tr>
                        <tr><td><strong>AcoustID</strong></td><td>Audio fingerprint verification of downloads</td><td>API Key</td></tr>
                        <tr><td><strong>ListenBrainz</strong></td><td>Listening history and recommendations</td><td>URL + Token</td></tr>
                    </tbody>
                </table>
                ${docsImg('gs-connecting.jpg', 'Service credentials connected')}
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
                ${docsImg('gs-interface.jpg', 'SoulSync interface layout')}
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
                ${docsImg('gs-folders.jpg', 'Download settings folder configuration')}

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
                ${docsImg('gs-folder-docker.jpg', 'Docker folder mapping')}
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
                ${docsImg('gs-docker.jpg', 'Docker compose configuration')}

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
        icon: '/static/help.jpg',
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
        icon: '/static/dashboard.jpg',
        children: [
            { id: 'dash-overview', title: 'Overview & Stats' },
            { id: 'dash-history', title: 'Download History' },
            { id: 'dash-global-search', title: 'Global Search' },
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
                ${docsImg('dash-overview.jpg', 'Dashboard overview')}
            </div>
            <div class="docs-subsection" id="dash-history">
                <h3 class="docs-subsection-title">Download History</h3>
                <p class="docs-text">Click <strong>Download History</strong> in the Recent Activity section to view a persistent log of every downloaded and imported track. Each entry is a collapsible card &mdash; click to expand and reveal source provenance details.</p>
                <ul class="docs-list">
                    <li><strong>Expected vs Downloaded</strong> &mdash; Shows what you asked for and what the source actually provided. Mismatches are highlighted in red.</li>
                    <li><strong>Source file</strong> &mdash; The original filename from the peer (Soulseek) or internal ID (streaming sources)</li>
                    <li><strong>AcoustID badge</strong> &mdash; Color-coded verification result: Verified (green), Failed (red), Skipped (orange), Off (gray)</li>
                    <li><strong>Source badges</strong> &mdash; Download source (Soulseek/Tidal/Qobuz/YouTube/HiFi/Deezer) and quality (FLAC/MP3/etc.)</li>
                    <li><strong>Tabs</strong> &mdash; Switch between Downloads and Server Imports. Source breakdown bar shows counts per download source.</li>
                </ul>
            </div>
            <div class="docs-subsection" id="dash-global-search">
                <h3 class="docs-subsection-title">Global Search</h3>
                <p class="docs-text">The search bar at the top of every page is the <strong>Global Search</strong>. Type any artist, album, or track name to search across all configured metadata sources. Results appear in a dropdown organized by category.</p>
                <ul class="docs-list">
                    <li><strong>Library artists</strong> &mdash; Artists already in your library (shown first with a "Library" badge)</li>
                    <li><strong>Artists</strong> &mdash; External artist results from Spotify/iTunes/Deezer</li>
                    <li><strong>Albums &amp; Singles</strong> &mdash; Click to open the download modal directly</li>
                    <li><strong>Tracks</strong> &mdash; Click to open the download modal, or use the play button to stream</li>
                    <li>Downloads started from Global Search create <strong>download bubbles</strong> on the Dashboard and Search page, same as Enhanced Search</li>
                </ul>
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
                ${docsImg('dash-workers.jpg', 'Enrichment workers status')}
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
                ${docsImg('dash-tools.jpg', 'Dashboard tool cards')}
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
                ${docsImg('dash-retag.jpg', 'Retag tool interface')}
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
                ${docsImg('dash-backup.jpg', 'Backup manager')}
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
                    <li><strong>Repair Worker</strong> &mdash; Background service with 16 automated repair jobs. Open <strong>Library Maintenance</strong> from the dashboard to view all jobs, enable/disable them, and trigger manual runs. Each job runs on a configurable schedule and creates findings that can be reviewed and fixed individually or in bulk.</li>
                </ul>
                <p class="docs-text"><strong>Repair Jobs:</strong></p>
                <table class="docs-table">
                    <thead><tr><th>Job</th><th>What It Does</th></tr></thead>
                    <tbody>
                        <tr><td>Track Number Repair</td><td>Fixes missing or incorrect track numbers by comparing against official tracklists</td></tr>
                        <tr><td>Orphan File Detector</td><td>Finds audio files in your transfer folder not tracked in the database. Can move to staging or delete.</td></tr>
                        <tr><td>Dead File Cleaner</td><td>Removes database entries pointing to files that no longer exist on disk</td></tr>
                        <tr><td>Duplicate Detector</td><td>Identifies duplicate tracks by fingerprint or metadata match</td></tr>
                        <tr><td>AcoustID Scanner</td><td>Batch audio fingerprint verification across your library</td></tr>
                        <tr><td>Missing Cover Art</td><td>Detects albums and tracks without embedded artwork and fetches it</td></tr>
                        <tr><td>Metadata Gap Filler</td><td>Completes missing metadata fields (genre, year, etc.) from connected services</td></tr>
                        <tr><td>Album Completeness</td><td>Verifies you have all tracks for each album and flags incomplete ones</td></tr>
                        <tr><td>Fake Lossless Detector</td><td>Identifies FLAC files that don't actually contain high-frequency audio content</td></tr>
                        <tr><td>Library Reorganize</td><td>Restructures library folders to match your configured path templates</td></tr>
                        <tr><td>MBID Mismatch Detector</td><td>Verifies MusicBrainz IDs are still accurate and flags mismatches</td></tr>
                        <tr><td>Single Album Dedup</td><td>Removes redundant single-track albums when the track exists on a full album</td></tr>
                        <tr><td>Album Tag Consistency</td><td>Standardizes album tags across all tracks in the same album</td></tr>
                        <tr><td>Live Commentary Cleaner</td><td>Detects and flags non-music content (commentary, interviews) in your library</td></tr>
                        <tr><td>Cache Evictor</td><td>Cleans expired metadata cache entries to free database space</td></tr>
                        <tr><td>Lossy Converter</td><td>Converts lossy files to alternative formats based on your preferences</td></tr>
                    </tbody>
                </table>
                <div class="docs-callout warning"><span class="docs-callout-icon">&#x26A0;&#xFE0F;</span><div><strong>Mass orphan safety:</strong> If the orphan detector flags more than 50% of files as orphans, it triggers a <strong>"Witness Me"</strong> confirmation dialog requiring you to type the phrase before any deletions proceed. This prevents accidental mass deletion from path mismatches.</div></div>
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
        icon: '/static/sync.jpg',
        children: [
            { id: 'sync-overview', title: 'Overview' },
            { id: 'sync-spotify', title: 'Spotify Playlists' },
            { id: 'sync-spotify-public', title: 'Spotify Public Links' },
            { id: 'sync-youtube', title: 'YouTube Playlists' },
            { id: 'sync-tidal', title: 'Tidal Playlists' },
            { id: 'sync-deezer', title: 'Deezer Playlists' },
            { id: 'sync-deezer-link', title: 'Deezer Link' },
            { id: 'sync-listenbrainz', title: 'ListenBrainz' },
            { id: 'sync-beatport', title: 'Beatport' },
            { id: 'sync-import-file', title: 'Import from File' },
            { id: 'sync-mirrored', title: 'Mirrored Playlists' },
            { id: 'sync-history', title: 'Sync History' },
            { id: 'sync-m3u', title: 'M3U Export' },
            { id: 'sync-discovery', title: 'Discovery Pipeline' },
            { id: 'sync-explorer', title: 'Playlist Explorer' }
        ],
        content: () => `
            <div class="docs-subsection" id="sync-overview">
                <h3 class="docs-subsection-title">Overview</h3>
                <p class="docs-text">The Sync page lets you import playlists from <strong>Spotify</strong>, <strong>YouTube</strong>, <strong>Tidal</strong>, and <strong>Beatport</strong>. Once imported, playlists are <strong>mirrored</strong> &mdash; they persist in your SoulSync instance and can be refreshed, discovered, and synced to your wishlist for downloading.</p>
                ${docsImg('sync-overview.jpg', 'Playlist sync page')}
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
                ${docsImg('sync-spotify.jpg', 'Spotify playlists loaded')}
                <div class="docs-callout tip"><span class="docs-callout-icon">&#x1F4A1;</span><div>Spotify-sourced playlists are auto-discovered at confidence 1.0 during refresh &mdash; no separate discovery step needed.</div></div>
            </div>
            <div class="docs-subsection" id="sync-youtube">
                <h3 class="docs-subsection-title">YouTube Playlists</h3>
                <p class="docs-text">Paste a YouTube playlist URL into the input field and click <strong>Parse Playlist</strong>. SoulSync extracts the track list and attempts to match each track to official Spotify/iTunes metadata.</p>
                ${docsImg('sync-youtube.jpg', 'YouTube playlist import')}
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
                ${docsImg('sync-beatport.jpg', 'Beatport genre browser')}
                <div class="docs-callout info"><span class="docs-callout-icon">&#x2139;&#xFE0F;</span><div>Beatport data is cached with a configurable TTL. The system automation <strong>Refresh Beatport Cache</strong> runs every 24 hours to keep content fresh.</div></div>
            </div>
            <div class="docs-subsection" id="sync-spotify-public">
                <h3 class="docs-subsection-title">Spotify Public Links</h3>
                <p class="docs-text">Sync Spotify playlists and albums <strong>without OAuth credentials</strong>. Paste any public Spotify playlist or album URL and SoulSync will load the tracks for download. Useful when you don't want to connect a Spotify account or want to sync from someone else's public playlist.</p>
                <ul class="docs-list">
                    <li>Paste any <code>open.spotify.com/playlist/...</code> or <code>open.spotify.com/album/...</code> URL</li>
                    <li>Works without Spotify API credentials</li>
                    <li>Previously loaded URLs appear in the history bar for quick re-access</li>
                    <li>Loaded playlists become mirrored for persistent state</li>
                </ul>
            </div>
            <div class="docs-subsection" id="sync-deezer">
                <h3 class="docs-subsection-title">Deezer Playlists</h3>
                <p class="docs-text">If you have a <strong>Deezer ARL token</strong> configured (Settings &gt; Downloads), the Deezer tab shows all your personal playlists &mdash; identical to how Spotify playlists work. Click <strong>Refresh</strong> to load your playlists, then click any playlist to view tracks and download.</p>
                <ul class="docs-list">
                    <li>Requires ARL token (a browser cookie from deezer.com &mdash; configure in Settings &gt; Downloads)</li>
                    <li>Click <strong>Sync / Download</strong> to open the playlist details modal with full track listing</li>
                    <li>Click <strong>Download Missing Tracks</strong> to analyze your library and download what's missing</li>
                    <li>Click <strong>Sync Playlist</strong> to sync tracks to your media server</li>
                    <li>Tracks include full album metadata with release dates, cover art, and proper organization</li>
                    <li>No discovery step needed &mdash; tracks go directly to download (like Spotify)</li>
                    <li>Track data is cached after first load for instant subsequent access</li>
                </ul>
                <div class="docs-callout tip"><span class="docs-callout-icon">&#x1F4A1;</span><div>The ARL token is the same one used for Deezer downloads. If you already have Deezer configured as a download source, your playlists will appear automatically.</div></div>
            </div>
            <div class="docs-subsection" id="sync-deezer-link">
                <h3 class="docs-subsection-title">Deezer Link</h3>
                <p class="docs-text">Import any public Deezer playlist by URL without needing an ARL token. Paste a Deezer playlist URL, click <strong>Load Playlist</strong>, and SoulSync parses the tracks for discovery and download.</p>
                <ul class="docs-list">
                    <li>Paste any <code>deezer.com/playlist/...</code> URL or raw playlist ID</li>
                    <li>Track matching uses the same fuzzy discovery pipeline as YouTube and Tidal</li>
                    <li>Previously loaded URLs appear in the history bar for quick re-access</li>
                    <li>Loaded playlists are automatically mirrored for persistent state</li>
                </ul>
            </div>
            <div class="docs-subsection" id="sync-import-file">
                <h3 class="docs-subsection-title">Import from File</h3>
                <p class="docs-text">Import track lists from <strong>CSV, TSV, or plain text files</strong>. Drag and drop a file or click to browse. SoulSync parses the file, lets you preview and map columns, then creates a mirrored playlist for discovery and download.</p>
                <ul class="docs-list">
                    <li><strong>CSV/TSV</strong>: Auto-detects columns; map Artist, Title, and Album from dropdowns</li>
                    <li><strong>Text files</strong>: One track per line; choose Artist-Title or Title-Artist order and separator (dash, tab, pipe, etc.)</li>
                    <li>Preview parsed tracks before importing</li>
                    <li>Name your playlist and it becomes a mirrored playlist for sync</li>
                </ul>
            </div>
            <div class="docs-subsection" id="sync-history">
                <h3 class="docs-subsection-title">Sync History</h3>
                <p class="docs-text">View a log of all sync operations. The <strong>Sync History</strong> button in the page header opens a modal showing every playlist sync, album download, and wishlist processing operation with timestamps, track counts, and completion status.</p>
                <ul class="docs-list">
                    <li>Shows playlist name, source, track count, and completion stats</li>
                    <li>Filter by source (Spotify, YouTube, Tidal, etc.)</li>
                    <li>Entries update in-place when the same playlist is re-synced</li>
                </ul>
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
                ${docsImg('sync-mirror.jpg', 'Mirrored playlist cards')}
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
            <div class="docs-subsection" id="sync-explorer">
                <h3 class="docs-subsection-title">Playlist Explorer</h3>
                <p class="docs-text">A visual tree-based browser for exploring playlists across all sources. Navigate through your server playlists, Spotify playlists, and mirrored playlists in a unified interface. Click any playlist to expand and view its tracks, then download or sync directly.</p>
            </div>
        `
    },
    {
        id: 'search',
        title: 'Music Downloads',
        icon: '/static/search.jpg',
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
                <p class="docs-text">The default search mode. Type an artist, album, or track name and results appear in a categorized dropdown: <strong>In Your Library</strong>, <strong>Artists</strong>, <strong>Albums</strong>, <strong>Singles & EPs</strong>, and <strong>Tracks</strong>. Results come from your primary metadata source (Spotify by default).</p>
                <ul class="docs-list">
                    <li>Click an <strong>artist</strong> to view their full discography with download buttons on each release</li>
                    <li>Click an <strong>album</strong> to open the download modal with track selection</li>
                    <li>Click a <strong>track</strong> to search your download source for that specific song</li>
                    <li><strong>Preview tracks</strong> &mdash; Play button on search result tracks lets you stream a preview directly from your download source before committing to a download</li>
                    <li><strong>Multi-source tabs</strong> &mdash; Switch between metadata sources (Spotify, iTunes, Deezer) using tabs above the results. Each source returns its own catalog, so tracks missing on one may be found on another</li>
                </ul>
                ${docsImg('dl-enhanced-search.jpg', 'Enhanced search results')}
            </div>
            <div class="docs-subsection" id="search-basic">
                <h3 class="docs-subsection-title">Basic Search</h3>
                <p class="docs-text">Toggle to Basic Search mode for direct Soulseek queries. This shows raw search results with detailed info: format, bitrate, quality score, file size, uploader name, upload speed, and availability.</p>
                <p class="docs-text"><strong>Filters</strong> let you narrow results by type (Albums/Singles), format (FLAC/MP3/OGG/AAC/WMA), and sort by relevance, quality, size, bitrate, duration, or uploader speed.</p>
                ${docsImg('dl-basic-search.jpg', 'Basic Soulseek search')}
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
                        <tr><td><strong>HiFi</strong></td><td>Free lossless downloads via community-run API instances</td><td>No account needed, good FLAC availability</td></tr>
                        <tr><td><strong>Deezer</strong></td><td>Deezer streaming rip via ARL token (FLAC/MP3)</td><td>Large catalog, easy setup, FLAC with HiFi sub</td></tr>
                        <tr><td><strong>Hybrid</strong></td><td>Tries your primary source first, then automatically falls back to alternates</td><td>Best overall success rate</td></tr>
                    </tbody>
                </table>
                <div class="docs-callout tip"><span class="docs-callout-icon">&#x1F4A1;</span><div><strong>Hybrid mode</strong> is recommended for most users. It tries your primary source first, then falls back through your configured priority order. All six sources (Soulseek, YouTube, Tidal, Qobuz, HiFi, Deezer) can be ordered via drag-and-drop in Settings.</div></div>
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
                ${docsImg('dl-candidates.jpg', 'Download candidate selection')}
            </div>
            <div class="docs-subsection" id="search-postprocess">
                <h3 class="docs-subsection-title">Post-Processing Pipeline</h3>
                <p class="docs-text">After a file is downloaded, it goes through an automatic pipeline before appearing in your library:</p>
                <ol class="docs-steps">
                    <li><strong>AcoustID Fingerprint Verification</strong> &mdash; If AcoustID is configured, the downloaded file is fingerprinted and compared against the expected track. Title and artist are fuzzy-matched (title &ge; 70% similarity, artist &ge; 60%). Files that fail verification are <strong>quarantined</strong> instead of added to your library. <em>Note: AcoustID is skipped for streaming sources (Tidal, Qobuz, Deezer, HiFi) since files are downloaded by exact track ID. However, streaming search results are still verified by artist and title matching before download to prevent wrong-track matches (e.g. same title, different artist).</em></li>
                    <li><strong>Metadata Tagging</strong> &mdash; The file is tagged with official metadata: title, artist, album artist, album, track number, disc number, year, genre, and composer. Tags are written using Mutagen (supports MP3, FLAC, OGG, M4A).</li>
                    <li><strong>Cover Art Embedding</strong> &mdash; Album artwork is downloaded from the metadata source and embedded directly into the audio file.</li>
                    <li><strong>File Organization</strong> &mdash; The file is renamed and moved to your transfer path following customizable templates. Separate templates for albums, singles, and playlists are configured in Settings. Available variables include <code>$artist</code>, <code>$album</code>, <code>$title</code>, <code>$track</code>, <code>$year</code>, <code>$quality</code>, and <code>$albumtype</code> (resolves to Album, Single, EP, or Compilation). For <strong>multi-disc albums</strong>, a <code>Disc N/</code> subfolder is automatically created when the album has more than one disc (or use <code>$disc</code> for zero-padded "01" or <code>$discnum</code> for unpadded "1" in your template for manual control).</li>
                    <li><strong>Lyrics (LRC)</strong> &mdash; Synced lyrics are fetched from the LRClib API and saved as <code>.lrc</code> sidecar files alongside the audio file. Compatible media players (foobar2000, MusicBee, Plex, etc.) will display time-synced lyrics automatically. Falls back to plain-text lyrics if synced versions aren't available.</li>
                    <li><strong>Lossy Copy</strong> &mdash; If enabled in settings, a lower-bitrate copy is created alongside the original (useful for mobile device syncing).</li>
                    <li><strong>Media Server Scan</strong> &mdash; Your media server (Plex/Jellyfin) is notified to scan for the new file. Navidrome auto-detects changes.</li>
                </ol>
                ${docsImg('dl-post-processing.jpg', 'Post-processing pipeline complete')}
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
                <div class="docs-callout tip"><span class="docs-callout-icon">&#x1F4A1;</span><div><strong>Streaming source quality</strong>: Tidal, Qobuz, HiFi, and Deezer each have their own quality dropdown in Settings. By default, if your preferred quality isn't available for a track, the source falls back to the next lower tier (e.g. FLAC &rarr; AAC 320). Disable <strong>Allow quality fallback</strong> next to the quality dropdown to enforce strict quality &mdash; the source will skip tracks it can't deliver at your chosen quality, and the orchestrator will try the next source in your priority list.</div></div>
                ${docsImg('dl-quality-profiles.jpg', 'Quality profile settings')}
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
        icon: '/static/discover.jpg',
        children: [
            { id: 'disc-hero', title: 'Featured Artists' },
            { id: 'disc-playlists', title: 'Discovery Playlists' },
            { id: 'disc-build', title: 'Build Custom Playlist' },
            { id: 'disc-seasonal', title: 'Seasonal & Curated' },
            { id: 'disc-timemachine', title: 'Time Machine' },
            { id: 'disc-artist-map', title: 'Artist Map' },
            { id: 'disc-stats', title: 'Listening Stats' }
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
                ${docsImg('disc-hero.jpg', 'Featured artist hero slider')}
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
                ${docsImg('disc-playlists.jpg', 'Discovery playlist cards')}
                <p class="docs-text">Each playlist can be played in the media player, downloaded, or synced to your media server.</p>
                <p class="docs-text"><strong>Genre Browser</strong> &mdash; Filter discovery pool content by specific genres. Browse available genres and view top tracks within each genre category.</p>
                ${docsImg('disc-genre-browser.jpg', 'Genre browser')}
                <p class="docs-text"><strong>ListenBrainz Playlists</strong> &mdash; If ListenBrainz is configured, the Discover page also shows personalized playlists generated from your listening history: Created For You, Your Playlists, and Collaborative playlists.</p>
            </div>
            <div class="docs-subsection" id="disc-build">
                <h3 class="docs-subsection-title">Build Custom Playlist</h3>
                <p class="docs-text">Search for 1&ndash;5 artists, select them, and click <strong>Generate</strong> to create a custom playlist from their catalogs. You can then download or sync the generated playlist.</p>
                ${docsImg('disc-build-playlist.jpg', 'Build custom playlist')}
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
                ${docsImg('disc-time-machine.jpg', 'Time Machine decade browser')}
            </div>
            <div class="docs-subsection" id="disc-artist-map">
                <h3 class="docs-subsection-title">Artist Map</h3>
                <p class="docs-text">Three interactive canvas-based visualization modes for exploring artist relationships. Accessed from the Discover page.</p>
                <ul class="docs-list">
                    <li><strong>Watchlist Constellation</strong> &mdash; Your watched artists as large nodes with similar artists orbiting around them. Reveals connections you might not have noticed.</li>
                    <li><strong>Genre Map</strong> &mdash; Browse all artists by genre with a sidebar picker. Ring-packed clusters, no artist cap. Great for exploring genres you don't normally listen to.</li>
                    <li><strong>Artist Explorer</strong> &mdash; Deep-dive any artist. Ring 1 shows direct similar artists, Ring 2 shows the extended network. Exploring an unknown artist fetches similar artists in real-time and caches them.</li>
                </ul>
                <p class="docs-text"><strong>Controls:</strong> Mouse wheel to zoom, click to explore, hover for tooltips with genre tags. Keyboard shortcuts: <span class="docs-kbd">?</span> for help, <span class="docs-kbd">F</span> to fit view, <span class="docs-kbd">S</span> to search.</p>
            </div>
            <div class="docs-subsection" id="disc-stats">
                <h3 class="docs-subsection-title">Listening Stats</h3>
                <p class="docs-text">The Stats page shows analytics about your music library and listening activity. Requires ListenBrainz or Last.fm scrobbling to be enabled for listening data.</p>
                <ul class="docs-list">
                    <li><strong>Library overview</strong> &mdash; Total artists, albums, tracks, total file size, format distribution</li>
                    <li><strong>Top artists, albums, and tracks</strong> &mdash; Ranked by play count or library presence</li>
                    <li><strong>Genre distribution</strong> &mdash; Visual breakdown of genres across your library</li>
                    <li><strong>Recent additions</strong> &mdash; Latest tracks and albums added to your library</li>
                    <li><strong>Listening timeline</strong> &mdash; Activity over time when scrobbling is configured</li>
                </ul>
            </div>
        `
    },
    {
        id: 'artists',
        title: 'Artists & Watchlist',
        icon: '/static/artists.jpg',
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
                ${docsImg('art-search.jpg', 'Artist search results')}
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
                ${docsImg('art-detail.jpg', 'Artist detail page')}
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
                ${docsImg('art-watchlist.jpg', 'Watchlist page')}
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
                ${docsImg('art-scan.jpg', 'New release scan panel')}
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
                ${docsImg('art-wishlist.jpg', 'Wishlist queue')}
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
        icon: '/static/automation.jpg',
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
                ${docsImg('auto-overview.jpg', 'Automations page')}
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
                ${docsImg('auto-builder.jpg', 'Automation builder')}
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
                ${docsImg('auto-history.jpg', 'Automation execution history')}
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
                ${docsImg('auto-system.jpg', 'System automations')}
            </div>
        `
    },
    {
        id: 'library',
        title: 'Music Library',
        icon: '/static/library.jpg',
        children: [
            { id: 'lib-standard', title: 'Standard View' },
            { id: 'lib-enhanced', title: 'Enhanced Library Manager' },
            { id: 'lib-matching', title: 'Service Matching' },
            { id: 'lib-tags', title: 'Write Tags to File' },
            { id: 'lib-bulk', title: 'Bulk Operations' },
            { id: 'lib-missing', title: 'Download Missing Tracks' },
            { id: 'lib-smart-delete', title: 'Smart Delete' },
            { id: 'lib-redownload', title: 'Track Redownload' },
            { id: 'lib-issues', title: 'Library Issues' }
        ],
        content: () => `
            <div class="docs-subsection" id="lib-standard">
                <h3 class="docs-subsection-title">Standard View</h3>
                <p class="docs-text">The Library page shows all artists in your collection as cards with images, album/track counts, and <strong>service badges</strong> (Spotify, MusicBrainz, Deezer, AudioDB, iTunes, Last.fm, Genius, Tidal, Qobuz) indicating which services have matched this artist.</p>
                <p class="docs-text">Use the <strong>search bar</strong>, <strong>alphabet navigation</strong> (A&ndash;Z, #), <strong>watchlist filter</strong> (All/Watched/Unwatched), and <strong>metadata source filter</strong> to browse. The source filter lets you find artists unmatched to a specific service (e.g. "No Discogs" shows artists missing a Discogs match) or matched to one (e.g. "Has Spotify"). Click any artist card to view their discography.</p>
                <p class="docs-text">The artist detail page shows albums, EPs, and singles as cards with completion percentages. Filter by category, content type (live/compilations/featured), or status (owned/missing). At the top, <strong>View on</strong> buttons link to the artist on each matched external service.</p>
                ${docsImg('lib-standard.jpg', 'Library artist grid')}
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
                ${docsImg('lib-enhanced.jpg', 'Enhanced Library Manager')}
            </div>
            <div class="docs-subsection" id="lib-matching">
                <h3 class="docs-subsection-title">Service Matching</h3>
                <p class="docs-text">In the Enhanced view, each artist, album, and track shows <strong>match status chips</strong> for all 10 services (Spotify, MusicBrainz, Deezer, Discogs, AudioDB, iTunes, Last.fm, Genius, Tidal, Qobuz). Click any chip to manually search and link the correct external ID. Run per-service enrichment from the <strong>Enrich</strong> dropdown to pull in metadata from a specific source.</p>
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
                ${docsImg('lib-tags.jpg', 'Tag preview modal')}
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
                ${docsImg('lib-bulk.jpg', 'Bulk operations bar')}
            </div>
            <div class="docs-subsection" id="lib-missing">
                <h3 class="docs-subsection-title">Download Missing Tracks</h3>
                <p class="docs-text">From any album card showing missing tracks, click <strong>Download Missing</strong> to open a modal listing all tracks not in your library. Select tracks, choose a download source, and start the download. Progress is tracked per-track with status indicators.</p>
                <p class="docs-text"><strong>Multi-Disc Albums</strong>: Albums with multiple discs are handled automatically. Tracks are organized into <code>Disc N/</code> subfolders within the album directory, preventing track number collisions (e.g., Disc 1 Track 1 vs Disc 2 Track 1). The disc structure is detected from Spotify or iTunes metadata.</p>
            </div>
            <div class="docs-subsection" id="lib-smart-delete">
                <h3 class="docs-subsection-title">Smart Delete</h3>
                <p class="docs-text">Right-click or use the delete action on any track to open the Smart Delete dialog. Three options are available:</p>
                <ul class="docs-list">
                    <li><strong>Remove from Library</strong> &mdash; Removes the track from SoulSync's database only. The audio file on disk is not touched. Use this if you want to clean up the database without losing files.</li>
                    <li><strong>Delete File Too</strong> &mdash; Removes the database entry AND deletes the audio file from disk. Irreversible.</li>
                    <li><strong>Delete &amp; Blacklist</strong> &mdash; Removes and deletes the file, then adds it to the <strong>download blacklist</strong> so it won't be re-downloaded by the wishlist or automation system.</li>
                </ul>
            </div>
            <div class="docs-subsection" id="lib-redownload">
                <h3 class="docs-subsection-title">Track Redownload</h3>
                <p class="docs-text">Redownload a specific track from your library with a different source or quality. A 3-step wizard guides you through:</p>
                <ol class="docs-steps">
                    <li><strong>Choose metadata source</strong> &mdash; Confirm the correct track identity (Spotify, iTunes, or Deezer match)</li>
                    <li><strong>Choose download source</strong> &mdash; Search across all configured download sources (Soulseek, Tidal, Qobuz, YouTube, HiFi, Deezer) and pick a specific result</li>
                    <li><strong>Download &amp; replace</strong> &mdash; The new file replaces the existing one with updated metadata and tags</li>
                </ol>
            </div>
            <div class="docs-subsection" id="lib-issues">
                <h3 class="docs-subsection-title">Library Issues</h3>
                <p class="docs-text">The Issues page tracks problems detected in your library by the repair worker. Issues are categorized by type and severity:</p>
                <ul class="docs-list">
                    <li><strong>Orphan files</strong> &mdash; Audio files in your transfer folder not tracked in the database</li>
                    <li><strong>Dead references</strong> &mdash; Database entries pointing to files that no longer exist on disk</li>
                    <li><strong>Duplicate tracks</strong> &mdash; Multiple copies of the same track detected by fingerprint or metadata</li>
                    <li><strong>Missing cover art</strong> &mdash; Albums or tracks without embedded artwork</li>
                    <li><strong>Metadata gaps</strong> &mdash; Tracks with incomplete metadata (missing genre, year, etc.)</li>
                    <li><strong>Fake lossless</strong> &mdash; Files labeled as FLAC but with audio that doesn't actually contain high-frequency content</li>
                </ul>
                <p class="docs-text">Each issue can be fixed individually or in bulk. Orphan files can be moved to staging (safe, reversible) or deleted. Mass deletions (50+ files) require typing <strong>"witness me"</strong> to confirm.</p>
            </div>
        `
    },
    {
        id: 'import',
        title: 'Import Music',
        icon: '/static/import.jpg',
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
                ${docsImg('imp-staging.jpg', 'Import staging page')}
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
                ${docsImg('imp-matching.jpg', 'Track matching interface')}
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
                ${docsImg('imp-textfile.jpg', 'Text file import')}
            </div>
        `
    },
    {
        id: 'player',
        title: 'Media Player',
        icon: '/static/library.jpg',
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
                ${docsImg('player-sidebar.jpg', 'Sidebar media player')}
                <p class="docs-text">Click the sidebar player to open the <strong>Now Playing modal</strong> &mdash; a full-screen experience with large album art, ambient glow (dominant color from cover art), a frequency-driven audio visualizer, and expanded controls.</p>
                ${docsImg('player-nowplaying.jpg', 'Now Playing modal')}
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
                ${docsImg('player-queue.jpg', 'Queue panel')}
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
        icon: '/static/settings.jpg',
        children: [
            { id: 'set-services', title: 'Service Credentials' },
            { id: 'set-media', title: 'Media Server Setup' },
            { id: 'set-download', title: 'Download Settings' },
            { id: 'set-processing', title: 'Processing & Organization' },
            { id: 'set-quality', title: 'Quality Profiles' },
            { id: 'set-other', title: 'Other Settings' },
            { id: 'set-db-maintenance', title: 'Database Maintenance' }
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
                    <li><strong>Qobuz</strong> &mdash; Username + Password (app ID auto-fetched), or paste an Auth Token from browser DevTools if login fails due to CAPTCHA</li>
                    <li><strong>HiFi</strong> &mdash; No credentials needed, uses community-run API instances. Test Connection to verify.</li>
                    <li><strong>Deezer</strong> &mdash; ARL cookie token from your browser (log into deezer.com &rarr; DevTools &rarr; Cookies &rarr; copy <code>arl</code>). Used for downloads AND user playlist access.</li>
                    <li><strong>Discogs</strong> &mdash; Personal Access Token from discogs.com/settings/developers (free, no app registration needed). Provides genres, styles, labels, catalog numbers, and community ratings.</li>
                    <li><strong>AcoustID</strong> &mdash; API key from acoustid.org (enables fingerprint verification of downloaded files)</li>
                    <li><strong>ListenBrainz</strong> &mdash; Base URL + token for listening history, scrobbling, and playlist import</li>
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
                ${docsImg('settings-media-server.jpg', 'Media server setup')}
                <p class="docs-text">The media player streams audio directly from your connected server &mdash; tracks play through your Plex, Jellyfin, or Navidrome instance without needing local file access.</p>
                <div class="docs-callout tip"><span class="docs-callout-icon">&#x1F4A1;</span><div><strong>Navidrome users:</strong> If artist images are broken after upgrading, use the <strong>Fix Navidrome URLs</strong> tool in Settings to convert old image URL formats to the correct Subsonic API format.</div></div>
            </div>
            <div class="docs-subsection" id="set-download">
                <h3 class="docs-subsection-title">Download Settings</h3>
                <ul class="docs-list">
                    <li><strong>Download Source Mode</strong> &mdash; Soulseek, YouTube, Tidal, Qobuz, HiFi, Deezer, or Hybrid. Hybrid tries your primary source first, then falls back to alternates with configurable priority via drag-and-drop. Each streaming source has its own quality dropdown and an <strong>Allow quality fallback</strong> toggle. See <em>Download Sources</em> and <em>Quality Profiles</em> in the Music Downloads section for details.</li>
                    <li><strong>Download Path</strong> &mdash; The folder where files are initially downloaded. This <strong>must match</strong> the folder your download source (slskd) writes to. In Docker, this is the container-side mount point (e.g., <code>/app/downloads</code>), not the host path. SoulSync monitors this folder for completed downloads to begin post-processing.</li>
                    <li><strong>Transfer Path</strong> &mdash; The final destination for processed music files. After tagging, renaming, and organizing, files are moved here. This <strong>must</strong> point to your media server's monitored music folder (the folder Plex/Jellyfin/Navidrome watches for new content). In Docker, use the container-side path (e.g., <code>/app/Transfer</code>).</li>
                    <li><strong>Staging Path</strong> &mdash; Folder for the Import feature (files placed here appear on the Import page). Separate from the download/transfer pipeline.</li>
                    <li><strong>iTunes Country</strong> &mdash; Storefront region for iTunes/Apple Music lookups (US, GB, FR, JP, etc.). Changes apply immediately to all searches without restarting. ID-based lookups automatically try up to 10 regional storefronts as fallback when the primary country returns no results.</li>
                    <li><strong>Lossy Copy</strong> &mdash; When enabled, creates a lower-bitrate MP3 copy of every downloaded file. Configure the output bitrate (default 320kbps) and output folder. Optionally delete the original lossless file after creating the lossy copy. Useful for syncing to mobile devices or streaming servers with bandwidth constraints.</li>
                    <li><strong>Content Filtering</strong> &mdash; Toggle explicit content filtering to control whether explicit tracks appear in search results and downloads.</li>
                </ul>
                ${docsImg('settings-downloads.jpg', 'Download settings')}
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
                ${docsImg('settings-processing.jpg', 'Processing settings')}
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
                    <li><strong>Music Library Paths</strong> &mdash; In Settings &gt; Library, add folder paths where your music files live. Required for tag writing, streaming, and file detection when your media server stores files at a different path than SoulSync can see. Docker users: mount your music folder(s) with read-write access, then add the container-side path.</li>
                    <li><strong>Replace Lower Quality on Import</strong> &mdash; Opt-in toggle in Settings &gt; Library. When importing from Staging, if a track already exists at lower quality (e.g. MP3), it gets replaced with the higher quality version (e.g. FLAC). Disabled by default.</li>
                    <li><strong>HiFi Instance Health</strong> &mdash; In Settings &gt; Downloads &gt; HiFi, click "Check All Instances" to see which community API instances are online, searchable, or able to download.</li>
                    <li><strong>Dead File Fix Options</strong> &mdash; Dead file findings in Library Maintenance now prompt with two choices: "Re-download" (adds to wishlist) or "Remove from DB" (just deletes the stale record). Works for single and bulk fix.</li>
                </ul>
            </div>
            <div class="docs-subsection" id="set-db-maintenance">
                <h3 class="docs-subsection-title">Database Maintenance</h3>
                <p class="docs-text">In <strong>Settings &gt; Advanced</strong>, the Database Maintenance section shows your database size, free (reclaimable) pages, and auto-vacuum mode. Two operations are available:</p>
                <ul class="docs-list">
                    <li><strong>Compact Database (VACUUM)</strong> &mdash; Rewrites the entire database file to reclaim unused space from deleted records. Locks the database during operation and may take over a minute on large databases. Shows elapsed time and space saved when complete.</li>
                    <li><strong>Enable Incremental Vacuum</strong> &mdash; Switches SQLite to incremental auto-vacuum mode, which reclaims freed pages automatically in small batches. Requires a one-time full VACUUM to activate. After enabled, the button grays out. This is the recommended approach for large databases.</li>
                </ul>
                <div class="docs-callout warning"><span class="docs-callout-icon">&#x26A0;&#xFE0F;</span><div>VACUUM requires temporary disk space equal to the database size. For a 5 GB database, ensure at least 5 GB free space before running.</div></div>
            </div>
        `
    },
    {
        id: 'profiles',
        title: 'Multi-Profile',
        icon: '/static/settings.jpg',
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
                ${docsImg('profiles-picker.jpg', 'Profile picker')}
                ${docsImg('profiles-create.jpg', 'Profile creation')}
            </div>
            <div class="docs-subsection" id="prof-permissions">
                <h3 class="docs-subsection-title">Permissions & Page Access</h3>
                <p class="docs-text">Admins can control what each profile has access to. When creating or editing a non-admin profile:</p>
                <ul class="docs-list">
                    <li><strong>Page Access</strong> &mdash; Check or uncheck which sidebar pages the profile can see (Dashboard, Sync, Search, Discover, Artists, Automations, Library, Import). Help & Docs is always accessible. Settings is admin-only.</li>
                    <li><strong>Can Download Music</strong> &mdash; Toggle whether the profile can initiate downloads. When disabled, all download buttons are hidden and the backend blocks download API calls with a 403 error.</li>
                    <li><strong>Enhanced Library Manager</strong> &mdash; The Enhanced view toggle on artist detail pages is only available to admin profiles. Non-admin users see the Standard view only.</li>
                </ul>
                ${docsImg('profiles-permissions.jpg', 'Profile permissions')}
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
        icon: '/static/settings.jpg',
        children: [
            { id: 'api-auth', title: 'Authentication' },
            { id: 'api-system', title: 'System' },
            { id: 'api-library', title: 'Library' },
            { id: 'api-search', title: 'Search' },
            { id: 'api-downloads', title: 'Downloads' },
            { id: 'api-playlists', title: 'Playlists' },
            { id: 'api-watchlist', title: 'Watchlist' },
            { id: 'api-wishlist', title: 'Wishlist' },
            { id: 'api-discover', title: 'Discover' },
            { id: 'api-profiles', title: 'Profiles' },
            { id: 'api-settings', title: 'Settings & Keys' },
            { id: 'api-retag', title: 'Retag' },
            { id: 'api-cache', title: 'Cache' },
            { id: 'api-listenbrainz', title: 'ListenBrainz' },
            { id: 'api-websocket', title: 'WebSocket Events' }
        ],
        content: () => {
            // --- API Endpoint definitions ---
            const E = (method, path, desc, params, bodyFields, example) => ({ method, path, desc, params, bodyFields, example });
            const P = (name, type, req, desc, def) => ({ name, type, required: req, desc, default: def });

            const apiGroups = [
                {
                    id: 'api-system', title: 'System', desc: 'Server status, activity feed, and combined statistics.',
                    endpoints: [
                        E('GET', '/system/status', 'Server uptime and service connectivity', [], null, {
                            response: '{\n  "success": true,\n  "data": {\n    "uptime": "4h 32m 10s",\n    "uptime_seconds": 16330,\n    "services": {\n      "spotify": true,\n      "soulseek": true,\n      "hydrabase": false\n    }\n  }\n}'
                        }),
                        E('GET', '/system/stats', 'Combined library and download statistics', [], null, {
                            response: '{\n  "success": true,\n  "data": {\n    "library": { "artists": 342, "albums": 1205, "tracks": 14832 },\n    "database": { "size_mb": 45.2, "last_update": "2026-03-13T08:00:00Z" },\n    "downloads": { "active": 3 }\n  }\n}'
                        }),
                        E('GET', '/system/activity', 'Recent activity feed', [], null, {
                            response: '{\n  "success": true,\n  "data": {\n    "activities": [\n      { "timestamp": "2026-03-13T10:30:00Z", "type": "download", "message": "Downloaded: Radiohead - Karma Police" }\n    ]\n  }\n}'
                        })
                    ]
                },
                {
                    id: 'api-library', title: 'Library', desc: 'Browse artists, albums, tracks, genres, and library statistics. Most endpoints support <code>?fields=</code> for field selection and pagination via <code>?page=</code> and <code>?limit=</code>.',
                    endpoints: [
                        E('GET', '/library/artists', 'List library artists with search, letter filter, and pagination', [
                            P('search', 'string', false, 'Substring filter on artist name', '""'),
                            P('letter', 'string', false, 'Filter by first letter, or "all"', '"all"'),
                            P('watchlist', 'string', false, 'Filter by watchlist status', '"all"'),
                            P('page', 'int', false, 'Page number', '1'),
                            P('limit', 'int', false, 'Results per page (max 200)', '50'),
                            P('fields', 'string', false, 'Comma-separated field names to include', 'all')
                        ], null, {
                            response: '{\n  "success": true,\n  "data": {\n    "artists": [\n      {\n        "id": 1,\n        "name": "Radiohead",\n        "thumb_url": "https://...",\n        "banner_url": "https://...",\n        "genres": ["alternative rock", "art rock"],\n        "summary": "English rock band...",\n        "style": "Alternative/Indie", "mood": "Melancholy",\n        "label": "XL Recordings",\n        "musicbrainz_id": "a74b1b7f-...",\n        "spotify_artist_id": "4Z8W4fKeB5YxbusRsdQVPb",\n        "itunes_artist_id": "657515",\n        "deezer_id": "399", "tidal_id": "3746724",\n        "qobuz_id": "61592", "genius_id": "604",\n        "lastfm_listeners": 5832451,\n        "lastfm_playcount": 328456789,\n        "genius_url": "https://genius.com/artists/Radiohead",\n        "album_count": 9, "track_count": 101,\n        "...": "all 50+ fields included"\n      }\n    ]\n  },\n  "pagination": {\n    "page": 1, "limit": 50, "total": 342, "total_pages": 7,\n    "has_next": true, "has_prev": false\n  }\n}'
                        }),
                        E('GET', '/library/artists/{artist_id}', 'Get a single artist with all metadata and album list', [
                            P('fields', 'string', false, 'Comma-separated fields', 'all')
                        ], null, {
                            response: '{\n  "success": true,\n  "data": {\n    "artist": {\n      "id": 1, "name": "Radiohead",\n      "thumb_url": "https://...", "banner_url": "https://...",\n      "genres": ["alternative rock", "art rock"],\n      "summary": "English rock band formed in 1985...",\n      "style": "Alternative/Indie", "mood": "Melancholy",\n      "label": "XL Recordings",\n      "server_source": "plex",\n      "created_at": "2026-01-15T10:00:00Z",\n      "updated_at": "2026-03-13T08:00:00Z",\n      "musicbrainz_id": "a74b1b7f-71a3-4b73-8c51-5c1f3a71c9e8",\n      "spotify_artist_id": "4Z8W4fKeB5YxbusRsdQVPb",\n      "itunes_artist_id": "657515",\n      "audiodb_id": "111239",\n      "deezer_id": "399",\n      "tidal_id": "3746724",\n      "qobuz_id": "61592",\n      "genius_id": "604",\n      "musicbrainz_match_status": "matched",\n      "spotify_match_status": "matched",\n      "itunes_match_status": "matched",\n      "audiodb_match_status": "matched",\n      "deezer_match_status": "matched",\n      "lastfm_match_status": "matched",\n      "genius_match_status": "matched",\n      "tidal_match_status": "matched",\n      "qobuz_match_status": "matched",\n      "musicbrainz_last_attempted": "2026-03-10T08:00:00Z",\n      "spotify_last_attempted": "2026-03-10T08:00:00Z",\n      "itunes_last_attempted": "2026-03-10T08:00:00Z",\n      "audiodb_last_attempted": "2026-03-10T08:00:00Z",\n      "deezer_last_attempted": "2026-03-10T08:00:00Z",\n      "lastfm_last_attempted": "2026-03-10T08:00:00Z",\n      "genius_last_attempted": "2026-03-10T08:00:00Z",\n      "tidal_last_attempted": "2026-03-10T08:00:00Z",\n      "qobuz_last_attempted": "2026-03-10T08:00:00Z",\n      "lastfm_listeners": 5832451,\n      "lastfm_playcount": 328456789,\n      "lastfm_tags": "alternative, rock, experimental",\n      "lastfm_similar": "Thom Yorke, Atoms for Peace, Portishead",\n      "lastfm_bio": "Radiohead are an English rock band...",\n      "lastfm_url": "https://www.last.fm/music/Radiohead",\n      "genius_description": "Radiohead is an English rock band...",\n      "genius_alt_names": "On a Friday",\n      "genius_url": "https://genius.com/artists/Radiohead",\n      "album_count": 9, "track_count": 101\n    },\n    "albums": [\n      { "id": 10, "title": "OK Computer", "year": 1997, "track_count": 12, "record_type": "album" }\n    ]\n  }\n}'
                        }),
                        E('GET', '/library/artists/{artist_id}/albums', 'List albums for an artist', [
                            P('fields', 'string', false, 'Comma-separated fields', 'all')
                        ], null, {
                            response: '{\n  "success": true,\n  "data": {\n    "albums": [\n      {\n        "id": 10, "artist_id": 1, "title": "OK Computer", "year": 1997,\n        "thumb_url": "https://...", "track_count": 12, "duration": 3214000,\n        "genres": ["alternative rock"],\n        "style": "Art Rock", "mood": "Atmospheric",\n        "label": "Parlophone", "record_type": "album", "explicit": false,\n        "upc": "0724385522529", "copyright": "1997 Parlophone Records",\n        "spotify_album_id": "6dVIqQ8qmQ5GBnJ9shOYGE",\n        "tidal_id": "17914997", "qobuz_id": "0724385522529",\n        "lastfm_listeners": 1543000, "lastfm_playcount": 89234567,\n        "...": "all 45+ fields included"\n      }\n    ]\n  }\n}'
                        }),
                        E('GET', '/library/albums', 'List or search albums with pagination', [
                            P('search', 'string', false, 'Substring filter on album title', '""'),
                            P('artist_id', 'int', false, 'Filter by artist ID'),
                            P('year', 'int', false, 'Filter by release year'),
                            P('page', 'int', false, 'Page number', '1'),
                            P('limit', 'int', false, 'Results per page (max 200)', '50'),
                            P('fields', 'string', false, 'Comma-separated fields', 'all')
                        ], null, {
                            response: '{\n  "success": true,\n  "data": { "albums": [ { "id": 10, "title": "OK Computer", "year": 1997, "artist_id": 1 } ] },\n  "pagination": { "page": 1, "limit": 50, "total": 1205, "total_pages": 25, "has_next": true, "has_prev": false }\n}'
                        }),
                        E('GET', '/library/albums/{album_id}', 'Get a single album with metadata and embedded tracks', [
                            P('fields', 'string', false, 'Comma-separated fields', 'all')
                        ], null, {
                            response: '{\n  "success": true,\n  "data": {\n    "album": {\n      "id": 10, "artist_id": 1, "title": "OK Computer", "year": 1997,\n      "thumb_url": "https://...",\n      "genres": ["alternative rock"],\n      "track_count": 12, "duration": 3214000,\n      "style": "Art Rock", "mood": "Atmospheric",\n      "label": "Parlophone", "explicit": false, "record_type": "album",\n      "server_source": "plex",\n      "created_at": "2026-01-15T10:00:00Z",\n      "updated_at": "2026-03-13T08:00:00Z",\n      "upc": "0724385522529", "copyright": "1997 Parlophone Records",\n      "musicbrainz_release_id": "b1a9c0e7-...",\n      "spotify_album_id": "6dVIqQ8qmQ5GBnJ9shOYGE",\n      "itunes_album_id": "1097861387",\n      "audiodb_id": "2115888",\n      "deezer_id": "6575789",\n      "tidal_id": "17914997",\n      "qobuz_id": "0724385522529",\n      "musicbrainz_match_status": "matched",\n      "spotify_match_status": "matched",\n      "itunes_match_status": "matched",\n      "audiodb_match_status": "matched",\n      "deezer_match_status": "matched",\n      "lastfm_match_status": "matched",\n      "tidal_match_status": "matched",\n      "qobuz_match_status": "matched",\n      "musicbrainz_last_attempted": "2026-03-10T08:00:00Z",\n      "spotify_last_attempted": "2026-03-10T08:00:00Z",\n      "itunes_last_attempted": "2026-03-10T08:00:00Z",\n      "audiodb_last_attempted": "2026-03-10T08:00:00Z",\n      "deezer_last_attempted": "2026-03-10T08:00:00Z",\n      "lastfm_last_attempted": "2026-03-10T08:00:00Z",\n      "tidal_last_attempted": "2026-03-10T08:00:00Z",\n      "qobuz_last_attempted": "2026-03-10T08:00:00Z",\n      "lastfm_listeners": 1543000,\n      "lastfm_playcount": 89234567,\n      "lastfm_tags": "alternative, 90s, rock",\n      "lastfm_wiki": "OK Computer is the third studio album...",\n      "lastfm_url": "https://www.last.fm/music/Radiohead/OK+Computer"\n    },\n    "tracks": [\n      { "id": 100, "title": "Airbag", "track_number": 1, "duration": 284000, "bitrate": 1411 }\n    ]\n  }\n}'
                        }),
                        E('GET', '/library/albums/{album_id}/tracks', 'List tracks in an album', [
                            P('fields', 'string', false, 'Comma-separated fields', 'all')
                        ], null, {
                            response: '{\n  "success": true,\n  "data": {\n    "tracks": [\n      {\n        "id": 100, "album_id": 10, "artist_id": 1, "title": "Airbag",\n        "track_number": 1, "duration": 284000,\n        "file_path": "/music/Radiohead/OK Computer/01 Airbag.flac",\n        "bitrate": 1411, "bpm": 120.5, "explicit": false,\n        "isrc": "GBAYE9700106",\n        "spotify_track_id": "6anwyDGQmsg45JKiVKpKGA",\n        "tidal_id": "17914998", "genius_id": "1342",\n        "lastfm_listeners": 892000, "lastfm_playcount": 4567890,\n        "genius_url": "https://genius.com/Radiohead-airbag-lyrics",\n        "...": "all 55+ fields included"\n      }\n    ]\n  }\n}'
                        }),
                        E('GET', '/library/tracks/{track_id}', 'Get a single track with all metadata', [
                            P('fields', 'string', false, 'Comma-separated fields', 'all')
                        ], null, {
                            response: '{\n  "success": true,\n  "data": {\n    "track": {\n      "id": 100, "album_id": 10, "artist_id": 1, "title": "Airbag",\n      "track_number": 1, "duration": 284000,\n      "file_path": "/music/Radiohead/OK Computer/01 Airbag.flac",\n      "bitrate": 1411, "bpm": 120.5, "explicit": false,\n      "style": "Art Rock", "mood": "Atmospheric",\n      "repair_status": null, "repair_last_checked": null,\n      "server_source": "plex",\n      "created_at": "2026-01-15T10:00:00Z",\n      "updated_at": "2026-03-13T08:00:00Z",\n      "isrc": "GBAYE9700106", "copyright": "1997 Parlophone Records",\n      "musicbrainz_recording_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",\n      "spotify_track_id": "6anwyDGQmsg45JKiVKpKGA",\n      "itunes_track_id": "1097861700",\n      "audiodb_id": null,\n      "deezer_id": "72420132",\n      "tidal_id": "17914998",\n      "qobuz_id": "24517824",\n      "genius_id": "1342",\n      "musicbrainz_match_status": "matched",\n      "spotify_match_status": "matched",\n      "itunes_match_status": "matched",\n      "audiodb_match_status": "not_found",\n      "deezer_match_status": "matched",\n      "lastfm_match_status": "matched",\n      "genius_match_status": "matched",\n      "tidal_match_status": "matched",\n      "qobuz_match_status": "matched",\n      "musicbrainz_last_attempted": "2026-03-10T08:00:00Z",\n      "spotify_last_attempted": "2026-03-10T08:00:00Z",\n      "itunes_last_attempted": "2026-03-10T08:00:00Z",\n      "audiodb_last_attempted": "2026-03-10T08:00:00Z",\n      "deezer_last_attempted": "2026-03-10T08:00:00Z",\n      "lastfm_last_attempted": "2026-03-10T08:00:00Z",\n      "genius_last_attempted": "2026-03-10T08:00:00Z",\n      "tidal_last_attempted": "2026-03-10T08:00:00Z",\n      "qobuz_last_attempted": "2026-03-10T08:00:00Z",\n      "lastfm_listeners": 892000,\n      "lastfm_playcount": 4567890,\n      "lastfm_tags": "alternative rock, radiohead",\n      "lastfm_url": "https://www.last.fm/music/Radiohead/_/Airbag",\n      "genius_lyrics": "In the next world war, in a jackknifed juggernaut...",\n      "genius_description": "The opening track of OK Computer...",\n      "genius_url": "https://genius.com/Radiohead-airbag-lyrics"\n    }\n  }\n}'
                        }),
                        E('GET', '/library/tracks', 'Search tracks by title and/or artist', [
                            P('title', 'string', false, 'Track title to search (at least one of title/artist required)', '""'),
                            P('artist', 'string', false, 'Artist name to search', '""'),
                            P('limit', 'int', false, 'Max results (max 200)', '50'),
                            P('fields', 'string', false, 'Comma-separated fields', 'all')
                        ], null, {
                            response: '{\n  "success": true,\n  "data": {\n    "tracks": [\n      { "id": 100, "title": "Airbag", "artist_name": "Radiohead", "album_title": "OK Computer" }\n    ]\n  }\n}'
                        }),
                        E('GET', '/library/genres', 'List all genres with occurrence counts', [
                            P('source', 'string', false, '"artists" or "albums"', '"artists"')
                        ], null, {
                            response: '{\n  "success": true,\n  "data": {\n    "genres": [ { "name": "alternative rock", "count": 45 }, { "name": "electronic", "count": 38 } ],\n    "source": "artists"\n  }\n}'
                        }),
                        E('GET', '/library/recently-added', 'Get recently added content', [
                            P('type', 'string', false, '"albums", "artists", or "tracks"', '"albums"'),
                            P('limit', 'int', false, 'Max items (max 200)', '50'),
                            P('fields', 'string', false, 'Comma-separated fields', 'all')
                        ], null, {
                            response: '{\n  "success": true,\n  "data": {\n    "items": [ { "id": 10, "title": "OK Computer", "year": 1997, "created_at": "2026-03-12T10:00:00Z" } ],\n    "type": "albums"\n  }\n}'
                        }),
                        E('GET', '/library/lookup', 'Look up a library entity by external provider ID', [
                            P('type', 'string', true, '"artist", "album", or "track"'),
                            P('provider', 'string', true, '"spotify", "musicbrainz", "itunes", "deezer", "audiodb", "tidal", "qobuz", or "genius"'),
                            P('id', 'string', true, 'The external ID value'),
                            P('fields', 'string', false, 'Comma-separated fields', 'all')
                        ], null, {
                            response: '{\n  "success": true,\n  "data": {\n    "artist": { "id": 1, "name": "Radiohead", "spotify_artist_id": "4Z8W4fKeB5YxbusRsdQVPb" }\n  }\n}'
                        }),
                        E('GET', '/library/stats', 'Get library statistics', [], null, {
                            response: '{\n  "success": true,\n  "data": {\n    "artists": 342,\n    "albums": 1205,\n    "tracks": 14832,\n    "database_size_mb": 45.2,\n    "last_update": "2026-03-13T08:00:00Z"\n  }\n}'
                        })
                    ]
                },
                {
                    id: 'api-search', title: 'Search', desc: 'Search external music sources (Spotify, iTunes, Hydrabase). All search endpoints use POST with a JSON body.',
                    endpoints: [
                        E('POST', '/search/tracks', 'Search for tracks across music sources', [], [
                            P('query', 'string', true, 'Search query'),
                            P('source', 'string', false, '"spotify", "itunes", or "auto"', '"auto"'),
                            P('limit', 'int', false, 'Max results (1-50)', '20')
                        ], {
                            request: '{\n  "query": "Karma Police",\n  "source": "auto",\n  "limit": 10\n}',
                            response: '{\n  "success": true,\n  "data": {\n    "tracks": [\n      {\n        "id": "3SVAN3BRByDmHOhKyIDxfC",\n        "name": "Karma Police",\n        "artists": ["Radiohead"],\n        "album": "OK Computer",\n        "duration_ms": 264066,\n        "popularity": 78,\n        "image_url": "https://...",\n        "release_date": "1997-05-28"\n      }\n    ],\n    "source": "spotify"\n  }\n}'
                        }),
                        E('POST', '/search/albums', 'Search for albums', [], [
                            P('query', 'string', true, 'Search query'),
                            P('limit', 'int', false, 'Max results (1-50)', '20')
                        ], {
                            request: '{\n  "query": "OK Computer",\n  "limit": 5\n}',
                            response: '{\n  "success": true,\n  "data": {\n    "albums": [\n      {\n        "id": "6dVIqQ8qmQ5GBnJ9shOYGE",\n        "name": "OK Computer",\n        "artists": ["Radiohead"],\n        "release_date": "1997-05-28",\n        "total_tracks": 12,\n        "album_type": "album",\n        "image_url": "https://..."\n      }\n    ],\n    "source": "spotify"\n  }\n}'
                        }),
                        E('POST', '/search/artists', 'Search for artists', [], [
                            P('query', 'string', true, 'Search query'),
                            P('limit', 'int', false, 'Max results (1-50)', '20')
                        ], {
                            request: '{\n  "query": "Radiohead",\n  "limit": 5\n}',
                            response: '{\n  "success": true,\n  "data": {\n    "artists": [\n      {\n        "id": "4Z8W4fKeB5YxbusRsdQVPb",\n        "name": "Radiohead",\n        "popularity": 79,\n        "genres": ["alternative rock", "art rock"],\n        "followers": 8500000,\n        "image_url": "https://..."\n      }\n    ],\n    "source": "spotify"\n  }\n}'
                        })
                    ]
                },
                {
                    id: 'api-downloads', title: 'Downloads', desc: 'List active downloads, cancel individual or all downloads.',
                    endpoints: [
                        E('GET', '/downloads', 'List active and recent download tasks', [], null, {
                            response: '{\n  "success": true,\n  "data": {\n    "downloads": [\n      {\n        "id": "abc123",\n        "status": "downloading",\n        "track_name": "Karma Police",\n        "artist_name": "Radiohead",\n        "album_name": "OK Computer",\n        "username": "slsk_user42",\n        "progress": 67,\n        "size": 34500000,\n        "batch_id": null,\n        "error": null\n      }\n    ]\n  }\n}'
                        }),
                        E('POST', '/downloads/{download_id}/cancel', 'Cancel a specific download', [], [
                            P('username', 'string', true, 'Soulseek username for the transfer')
                        ], {
                            request: '{\n  "username": "slsk_user42"\n}',
                            response: '{\n  "success": true,\n  "data": { "message": "Download cancelled." }\n}'
                        }),
                        E('POST', '/downloads/cancel-all', 'Cancel all active downloads and clear completed', [], null, {
                            response: '{\n  "success": true,\n  "data": { "message": "All downloads cancelled and cleared." }\n}'
                        })
                    ]
                },
                {
                    id: 'api-playlists', title: 'Playlists', desc: 'List and inspect playlists from Spotify or Tidal, and trigger playlist sync.',
                    endpoints: [
                        E('GET', '/playlists', 'List user playlists from Spotify or Tidal', [
                            P('source', 'string', false, '"spotify" or "tidal"', '"spotify"')
                        ], null, {
                            response: '{\n  "success": true,\n  "data": {\n    "playlists": [\n      {\n        "id": "37i9dQZF1DXcBWIGoYBM5M",\n        "name": "Today\'s Top Hits",\n        "owner": "spotify",\n        "track_count": 50,\n        "image_url": "https://..."\n      }\n    ],\n    "source": "spotify"\n  }\n}'
                        }),
                        E('GET', '/playlists/{playlist_id}', 'Get playlist details with tracks', [
                            P('source', 'string', false, 'Only "spotify" is supported', '"spotify"')
                        ], null, {
                            response: '{\n  "success": true,\n  "data": {\n    "playlist": {\n      "id": "37i9dQZF1DXcBWIGoYBM5M",\n      "name": "Today\'s Top Hits",\n      "owner": "spotify",\n      "total_tracks": 50,\n      "tracks": [\n        {\n          "id": "3SVAN3BRByDmHOhKyIDxfC",\n          "name": "Karma Police",\n          "artists": ["Radiohead"],\n          "album": "OK Computer",\n          "duration_ms": 264066,\n          "image_url": "https://..."\n        }\n      ]\n    },\n    "source": "spotify"\n  }\n}'
                        }),
                        E('POST', '/playlists/{playlist_id}/sync', 'Trigger playlist sync and download', [], [
                            P('playlist_name', 'string', true, 'Name of the playlist'),
                            P('tracks', 'array', true, 'Array of track objects to sync')
                        ], {
                            request: '{\n  "playlist_name": "My Playlist",\n  "tracks": [\n    { "id": "3SVAN3...", "name": "Karma Police", "artists": [{ "name": "Radiohead" }] }\n  ]\n}',
                            response: '{\n  "success": true,\n  "data": { "message": "Playlist sync started.", "playlist_id": "37i9dQZF1DXcBWIGoYBM5M" }\n}'
                        })
                    ]
                },
                {
                    id: 'api-watchlist', title: 'Watchlist', desc: 'View, add, remove watched artists and trigger new release scans. Profile-scoped via <code>X-Profile-Id</code> header.',
                    endpoints: [
                        E('GET', '/watchlist', 'List all watchlist artists for the current profile', [
                            P('fields', 'string', false, 'Comma-separated fields', 'all')
                        ], null, {
                            response: '{\n  "success": true,\n  "data": {\n    "artists": [\n      {\n        "id": 1,\n        "artist_name": "Radiohead",\n        "spotify_artist_id": "4Z8W4fKeB5YxbusRsdQVPb",\n        "image_url": "https://...",\n        "date_added": "2026-01-15T10:00:00Z",\n        "include_albums": true,\n        "include_eps": true,\n        "include_singles": true,\n        "include_live": false,\n        "include_remixes": false,\n        "profile_id": 1\n      }\n    ]\n  }\n}'
                        }),
                        E('POST', '/watchlist', 'Add an artist to the watchlist', [], [
                            P('artist_id', 'string', true, 'Spotify or iTunes artist ID'),
                            P('artist_name', 'string', true, 'Artist display name')
                        ], {
                            request: '{\n  "artist_id": "4Z8W4fKeB5YxbusRsdQVPb",\n  "artist_name": "Radiohead"\n}',
                            response: '{\n  "success": true,\n  "data": { "message": "Added Radiohead to watchlist." }\n}'
                        }),
                        E('DELETE', '/watchlist/{artist_id}', 'Remove an artist from the watchlist', [], null, {
                            response: '{\n  "success": true,\n  "data": { "message": "Artist removed from watchlist." }\n}'
                        }),
                        E('PATCH', '/watchlist/{artist_id}', 'Update content type filters for a watchlist artist', [], [
                            P('include_albums', 'bool', false, 'Include albums'),
                            P('include_eps', 'bool', false, 'Include EPs'),
                            P('include_singles', 'bool', false, 'Include singles'),
                            P('include_live', 'bool', false, 'Include live recordings'),
                            P('include_remixes', 'bool', false, 'Include remixes'),
                            P('include_acoustic', 'bool', false, 'Include acoustic versions'),
                            P('include_compilations', 'bool', false, 'Include compilations')
                        ], {
                            request: '{\n  "include_live": true,\n  "include_remixes": false\n}',
                            response: '{\n  "success": true,\n  "data": {\n    "message": "Watchlist filters updated.",\n    "updated": { "include_live": true, "include_remixes": false }\n  }\n}'
                        }),
                        E('POST', '/watchlist/scan', 'Trigger a watchlist scan for new releases', [], null, {
                            response: '{\n  "success": true,\n  "data": { "message": "Watchlist scan started." }\n}'
                        })
                    ]
                },
                {
                    id: 'api-wishlist', title: 'Wishlist', desc: 'View, add, remove wishlist tracks and trigger download processing. Profile-scoped.',
                    endpoints: [
                        E('GET', '/wishlist', 'List wishlist tracks with optional category filter', [
                            P('category', 'string', false, '"singles" or "albums"', 'all'),
                            P('page', 'int', false, 'Page number', '1'),
                            P('limit', 'int', false, 'Results per page (max 200)', '50'),
                            P('fields', 'string', false, 'Comma-separated fields', 'all')
                        ], null, {
                            response: '{\n  "success": true,\n  "data": {\n    "tracks": [\n      {\n        "id": 1,\n        "spotify_track_id": "3SVAN3BRByDmHOhKyIDxfC",\n        "track_name": "Karma Police",\n        "artist_name": "Radiohead",\n        "album_name": "OK Computer",\n        "failure_reason": "No suitable source found",\n        "retry_count": 2,\n        "last_attempted": "2026-03-12T10:00:00Z",\n        "date_added": "2026-03-10T08:00:00Z",\n        "source_type": "playlist_sync"\n      }\n    ]\n  },\n  "pagination": { "page": 1, "limit": 50, "total": 12, "total_pages": 1, "has_next": false, "has_prev": false }\n}'
                        }),
                        E('POST', '/wishlist', 'Add a track to the wishlist', [], [
                            P('spotify_track_data', 'object', true, 'Full Spotify track data object'),
                            P('failure_reason', 'string', false, 'Reason for adding', '"Added via API"'),
                            P('source_type', 'string', false, 'Source identifier', '"api"')
                        ], {
                            request: '{\n  "spotify_track_data": {\n    "id": "3SVAN3BRByDmHOhKyIDxfC",\n    "name": "Karma Police",\n    "artists": [{ "name": "Radiohead" }],\n    "album": { "name": "OK Computer", "album_type": "album" }\n  },\n  "source_type": "api"\n}',
                            response: '{\n  "success": true,\n  "data": { "message": "Track added to wishlist." }\n}'
                        }),
                        E('DELETE', '/wishlist/{track_id}', 'Remove a track by Spotify track ID', [], null, {
                            response: '{\n  "success": true,\n  "data": { "message": "Track removed from wishlist." }\n}'
                        }),
                        E('POST', '/wishlist/process', 'Trigger wishlist download processing', [], null, {
                            response: '{\n  "success": true,\n  "data": { "message": "Wishlist processing started." }\n}'
                        })
                    ]
                },
                {
                    id: 'api-discover', title: 'Discover', desc: 'Browse the discovery pool, similar artists, recent releases, and bubble snapshots. Profile-scoped.',
                    endpoints: [
                        E('GET', '/discover/pool', 'List discovery pool tracks with optional filters', [
                            P('new_releases_only', 'string', false, '"true" to filter new releases only', 'false'),
                            P('source', 'string', false, '"spotify" or "itunes"', 'all'),
                            P('page', 'int', false, 'Page number', '1'),
                            P('limit', 'int', false, 'Max tracks (max 500)', '100'),
                            P('fields', 'string', false, 'Comma-separated fields', 'all')
                        ], null, {
                            response: '{\n  "success": true,\n  "data": {\n    "tracks": [\n      {\n        "id": 1,\n        "spotify_track_id": "3SVAN3...",\n        "track_name": "Karma Police",\n        "artist_name": "Radiohead",\n        "album_name": "OK Computer",\n        "album_cover_url": "https://...",\n        "duration_ms": 264066,\n        "popularity": 78,\n        "is_new_release": false,\n        "source": "spotify"\n      }\n    ]\n  },\n  "pagination": { "page": 1, "limit": 100, "total": 850, "total_pages": 9, "has_next": true, "has_prev": false }\n}'
                        }),
                        E('GET', '/discover/similar-artists', 'List top similar artists from the watchlist', [
                            P('limit', 'int', false, 'Max artists (max 200)', '50'),
                            P('fields', 'string', false, 'Comma-separated fields', 'all')
                        ], null, {
                            response: '{\n  "success": true,\n  "data": {\n    "artists": [\n      {\n        "id": 1,\n        "similar_artist_name": "Thom Yorke",\n        "similar_artist_spotify_id": "2x9SpqnPi8rlE9pjHBwN5z",\n        "similarity_rank": 1,\n        "occurrence_count": 5\n      }\n    ]\n  }\n}'
                        }),
                        E('GET', '/discover/recent-releases', 'List recent releases from watched artists', [
                            P('limit', 'int', false, 'Max releases (max 200)', '50'),
                            P('fields', 'string', false, 'Comma-separated fields', 'all')
                        ], null, {
                            response: '{\n  "success": true,\n  "data": {\n    "releases": [\n      {\n        "id": 1,\n        "album_name": "A Moon Shaped Pool",\n        "album_spotify_id": "2ix8vWvvSp2Yo7rKMiWpkg",\n        "release_date": "2016-05-08",\n        "album_cover_url": "https://...",\n        "track_count": 11,\n        "source": "spotify"\n      }\n    ]\n  }\n}'
                        }),
                        E('GET', '/discover/pool/metadata', 'Get discovery pool metadata', [], null, {
                            response: '{\n  "success": true,\n  "data": {\n    "last_populated": "2026-03-12T10:00:00Z",\n    "track_count": 850,\n    "updated_at": "2026-03-12T10:00:00Z"\n  }\n}'
                        }),
                        E('GET', '/discover/bubbles', 'List all bubble snapshots for the current profile', [], null, {
                            response: '{\n  "success": true,\n  "data": {\n    "snapshots": {\n      "artist_bubbles": { "snapshot_data": [...], "updated_at": "..." },\n      "search_bubbles": null,\n      "discover_downloads": null\n    }\n  }\n}'
                        }),
                        E('GET', '/discover/bubbles/{snapshot_type}', 'Get a specific bubble snapshot (artist_bubbles, search_bubbles, discover_downloads)', [], null, {
                            response: '{\n  "success": true,\n  "data": {\n    "snapshot": { "snapshot_data": [...], "updated_at": "2026-03-12T10:00:00Z" }\n  }\n}'
                        })
                    ]
                },
                {
                    id: 'api-profiles', title: 'Profiles', desc: 'Manage multi-profile support. Create, update, delete profiles with PIN protection and page access control.',
                    endpoints: [
                        E('GET', '/profiles', 'List all profiles', [], null, {
                            response: '{\n  "success": true,\n  "data": {\n    "profiles": [\n      {\n        "id": 1,\n        "name": "Admin",\n        "is_admin": 1,\n        "avatar_color": "#6366f1",\n        "avatar_url": null,\n        "created_at": "2026-01-01T00:00:00Z"\n      }\n    ]\n  }\n}'
                        }),
                        E('GET', '/profiles/{profile_id}', 'Get a single profile by ID', [], null, {
                            response: '{\n  "success": true,\n  "data": {\n    "profile": {\n      "id": 1, "name": "Admin", "is_admin": 1,\n      "avatar_color": "#6366f1", "avatar_url": null\n    }\n  }\n}'
                        }),
                        E('POST', '/profiles', 'Create a new profile', [], [
                            P('name', 'string', true, 'Profile display name'),
                            P('avatar_color', 'string', false, 'Hex color for avatar', '"#6366f1"'),
                            P('avatar_url', 'string', false, 'Custom avatar image URL'),
                            P('is_admin', 'bool', false, 'Admin privileges', 'false'),
                            P('pin', 'string', false, 'PIN for profile protection')
                        ], {
                            request: '{\n  "name": "Family Room",\n  "is_admin": false,\n  "avatar_color": "#22c55e",\n  "pin": "1234"\n}',
                            response: '{\n  "success": true,\n  "data": {\n    "profile": {\n      "id": 3, "name": "Family Room", "is_admin": 0,\n      "avatar_color": "#22c55e"\n    }\n  }\n}'
                        }),
                        E('PUT', '/profiles/{profile_id}', 'Update a profile', [], [
                            P('name', 'string', false, 'New display name'),
                            P('avatar_color', 'string', false, 'Hex color'),
                            P('avatar_url', 'string', false, 'Avatar image URL'),
                            P('is_admin', 'bool', false, 'Admin privileges'),
                            P('pin', 'string', false, 'New PIN (empty string clears PIN)')
                        ], {
                            request: '{\n  "name": "Kids Room",\n  "avatar_color": "#f59e0b"\n}',
                            response: '{\n  "success": true,\n  "data": {\n    "profile": { "id": 3, "name": "Kids Room", "avatar_color": "#f59e0b" }\n  }\n}'
                        }),
                        E('DELETE', '/profiles/{profile_id}', 'Delete a profile (cannot delete profile 1)', [], null, {
                            response: '{\n  "success": true,\n  "data": { "message": "Profile 3 deleted." }\n}'
                        })
                    ]
                },
                {
                    id: 'api-settings', title: 'Settings & API Keys', desc: 'Read and update application settings. Manage API keys. Sensitive values are always redacted in GET responses.',
                    endpoints: [
                        E('GET', '/settings', 'Get current settings (sensitive values redacted)', [], null, {
                            response: '{\n  "success": true,\n  "data": {\n    "settings": {\n      "spotify": {\n        "client_id": "***REDACTED***",\n        "country": "US"\n      },\n      "download_path": "/music",\n      "download_source": "hybrid",\n      "ui_appearance": {\n        "accent_preset": "green",\n        "particles_enabled": true\n      }\n    }\n  }\n}'
                        }),
                        E('PATCH', '/settings', 'Update settings (partial, dot-notation keys accepted)', [], [
                            P('{key}', 'any', true, 'One or more key-value pairs. The "api_keys" key is blocked.')
                        ], {
                            request: '{\n  "spotify.country": "GB",\n  "download_path": "/new/music/path"\n}',
                            response: '{\n  "success": true,\n  "data": {\n    "message": "Settings updated.",\n    "updated_keys": ["spotify.country", "download_path"]\n  }\n}'
                        }),
                        E('GET', '/api-keys', 'List all API keys (prefix and label only, never the full key)', [], null, {
                            response: '{\n  "success": true,\n  "data": {\n    "keys": [\n      {\n        "id": "a1b2c3d4-...",\n        "label": "My Bot",\n        "key_prefix": "sk_AbCdEfGh",\n        "created_at": "2026-03-01T10:00:00Z",\n        "last_used_at": "2026-03-13T09:15:00Z"\n      }\n    ]\n  }\n}'
                        }),
                        E('POST', '/api-keys', 'Generate a new API key (raw key returned once)', [], [
                            P('label', 'string', false, 'Descriptive label for the key', '""')
                        ], {
                            request: '{\n  "label": "Home Assistant"\n}',
                            response: '{\n  "success": true,\n  "data": {\n    "key": "sk_AbCdEfGhIjKlMnOpQrStUvWxYz123456789...",\n    "id": "a1b2c3d4-...",\n    "label": "Home Assistant",\n    "key_prefix": "sk_AbCdEfGh",\n    "created_at": "2026-03-13T10:00:00Z"\n  }\n}'
                        }),
                        E('DELETE', '/api-keys/{key_id}', 'Revoke an API key by its UUID', [], null, {
                            response: '{\n  "success": true,\n  "data": { "message": "API key revoked." }\n}'
                        }),
                        E('POST', '/api-keys/bootstrap', 'Generate the first API key when none exist (NO AUTH REQUIRED)', [], [
                            P('label', 'string', false, 'Label for the key', '"Default"')
                        ], {
                            request: '{\n  "label": "My First Key"\n}',
                            response: '{\n  "success": true,\n  "data": {\n    "key": "sk_...",\n    "id": "...",\n    "label": "My First Key",\n    "key_prefix": "sk_...",\n    "created_at": "2026-03-13T10:00:00Z"\n  }\n}'
                        })
                    ]
                },
                {
                    id: 'api-retag', title: 'Retag', desc: 'View and manage the pending metadata correction queue.',
                    endpoints: [
                        E('GET', '/retag/groups', 'List all retag groups with track counts', [], null, {
                            response: '{\n  "success": true,\n  "data": {\n    "groups": [\n      {\n        "id": 1,\n        "original_artist": "Radiohed",\n        "corrected_artist": "Radiohead",\n        "track_count": 5,\n        "created_at": "2026-03-12T10:00:00Z"\n      }\n    ]\n  }\n}'
                        }),
                        E('GET', '/retag/groups/{group_id}', 'Get a retag group with its tracks', [], null, {
                            response: '{\n  "success": true,\n  "data": {\n    "group": { "id": 1, "original_artist": "Radiohed", "corrected_artist": "Radiohead" },\n    "tracks": [\n      { "id": 100, "title": "Airbag", "file_path": "/music/..." }\n    ]\n  }\n}'
                        }),
                        E('DELETE', '/retag/groups/{group_id}', 'Delete a retag group and its tracks', [], null, {
                            response: '{\n  "success": true,\n  "data": { "message": "Retag group 1 deleted." }\n}'
                        }),
                        E('DELETE', '/retag/groups', 'Delete all retag groups and tracks', [], null, {
                            response: '{\n  "success": true,\n  "data": { "message": "Cleared 5 retag groups." }\n}'
                        }),
                        E('GET', '/retag/stats', 'Get retag queue statistics', [], null, {
                            response: '{\n  "success": true,\n  "data": {\n    "total_groups": 5,\n    "total_tracks": 23,\n    "pending": 18,\n    "completed": 5\n  }\n}'
                        })
                    ]
                },
                {
                    id: 'api-cache', title: 'Cache', desc: 'Browse MusicBrainz and discovery match caches for debugging and inspection.',
                    endpoints: [
                        E('GET', '/cache/musicbrainz', 'List cached MusicBrainz lookups', [
                            P('entity_type', 'string', false, '"artist", "album", or "track"'),
                            P('search', 'string', false, 'Filter by entity name'),
                            P('page', 'int', false, 'Page number', '1'),
                            P('limit', 'int', false, 'Results per page (max 200)', '50')
                        ], null, {
                            response: '{\n  "success": true,\n  "data": {\n    "entries": [\n      {\n        "entity_type": "artist",\n        "entity_name": "Radiohead",\n        "musicbrainz_id": "a74b1b7f-...",\n        "last_updated": "2026-03-12T10:00:00Z",\n        "metadata_json": { "type": "Group", "country": "GB" }\n      }\n    ]\n  },\n  "pagination": { "page": 1, "limit": 50, "total": 342, "total_pages": 7, "has_next": true, "has_prev": false }\n}'
                        }),
                        E('GET', '/cache/musicbrainz/stats', 'Get MusicBrainz cache statistics', [], null, {
                            response: '{\n  "success": true,\n  "data": {\n    "total": 1024,\n    "matched": 890,\n    "unmatched": 134,\n    "by_type": { "artist": 342, "album": 450, "track": 232 }\n  }\n}'
                        }),
                        E('GET', '/cache/discovery-matches', 'List cached discovery provider matches', [
                            P('provider', 'string', false, '"spotify", "itunes", etc.'),
                            P('search', 'string', false, 'Filter by title or artist'),
                            P('page', 'int', false, 'Page number', '1'),
                            P('limit', 'int', false, 'Results per page (max 200)', '50')
                        ], null, {
                            response: '{\n  "success": true,\n  "data": {\n    "entries": [\n      {\n        "provider": "spotify",\n        "original_title": "Karma Police",\n        "original_artist": "Radiohead",\n        "matched_data_json": { "id": "3SVAN3...", "confidence": 0.95 },\n        "use_count": 3,\n        "last_used_at": "2026-03-12T10:00:00Z"\n      }\n    ]\n  },\n  "pagination": { "page": 1, "limit": 50, "total": 5000, "total_pages": 100, "has_next": true, "has_prev": false }\n}'
                        }),
                        E('GET', '/cache/discovery-matches/stats', 'Get discovery match cache statistics', [], null, {
                            response: '{\n  "success": true,\n  "data": {\n    "total": 5000,\n    "total_uses": 18500,\n    "avg_confidence": 0.872,\n    "by_provider": { "spotify": 3200, "itunes": 1800 }\n  }\n}'
                        })
                    ]
                },
                {
                    id: 'api-listenbrainz', title: 'ListenBrainz', desc: 'Browse cached ListenBrainz playlists and their tracks.',
                    endpoints: [
                        E('GET', '/listenbrainz/playlists', 'List cached ListenBrainz playlists', [
                            P('type', 'string', false, 'Filter by playlist_type (e.g. "weekly-jams")'),
                            P('page', 'int', false, 'Page number', '1'),
                            P('limit', 'int', false, 'Results per page (max 200)', '50')
                        ], null, {
                            response: '{\n  "success": true,\n  "data": {\n    "playlists": [\n      {\n        "id": 1,\n        "playlist_mbid": "a1b2c3d4-...",\n        "title": "Weekly Jams for user",\n        "playlist_type": "weekly-jams",\n        "track_count": 50,\n        "created_at": "2026-03-10T00:00:00Z"\n      }\n    ]\n  },\n  "pagination": { "page": 1, "limit": 50, "total": 12, "total_pages": 1, "has_next": false, "has_prev": false }\n}'
                        }),
                        E('GET', '/listenbrainz/playlists/{playlist_id}', 'Get a ListenBrainz playlist with tracks (ID or MBID)', [], null, {
                            response: '{\n  "success": true,\n  "data": {\n    "playlist": {\n      "id": 1,\n      "playlist_mbid": "a1b2c3d4-...",\n      "title": "Weekly Jams for user",\n      "playlist_type": "weekly-jams"\n    },\n    "tracks": [\n      {\n        "id": 1,\n        "position": 0,\n        "recording_mbid": "e1f2g3h4-...",\n        "title": "Karma Police",\n        "artist": "Radiohead"\n      }\n    ]\n  }\n}'
                        })
                    ]
                }
            ];

            // --- Build endpoint HTML ---
            function methodClass(m) { return m.toLowerCase(); }

            function buildParamsTable(params) {
                if (!params || !params.length) return '';
                let html = '<div class="api-detail-label">Parameters</div>';
                html += '<table class="api-params-table"><thead><tr><th>Name</th><th>Type</th><th>Required</th><th>Description</th></tr></thead><tbody>';
                params.forEach(p => {
                    const req = p.required ? '<span class="api-param-required">required</span>' : '<span class="api-param-optional">optional</span>';
                    const def = p.default !== undefined ? ' <span style="color:rgba(255,255,255,0.25)">(default: ' + p.default + ')</span>' : '';
                    html += '<tr><td>' + p.name + '</td><td>' + p.type + '</td><td>' + req + '</td><td>' + p.desc + def + '</td></tr>';
                });
                html += '</tbody></table>';
                return html;
            }

            function buildBodyTable(fields) {
                if (!fields || !fields.length) return '';
                let html = '<div class="api-detail-label">Request Body (JSON)</div>';
                html += '<table class="api-params-table"><thead><tr><th>Field</th><th>Type</th><th>Required</th><th>Description</th></tr></thead><tbody>';
                fields.forEach(p => {
                    const req = p.required ? '<span class="api-param-required">required</span>' : '<span class="api-param-optional">optional</span>';
                    const def = p.default !== undefined ? ' <span style="color:rgba(255,255,255,0.25)">(default: ' + p.default + ')</span>' : '';
                    html += '<tr><td>' + p.name + '</td><td>' + p.type + '</td><td>' + req + '</td><td>' + p.desc + def + '</td></tr>';
                });
                html += '</tbody></table>';
                return html;
            }

            function buildExample(ex) {
                if (!ex) return '';
                let html = '';
                if (ex.request) {
                    html += '<div class="api-detail-label">Example Request Body</div>';
                    html += '<div class="api-example-json">' + escHtml(ex.request) + '</div>';
                }
                if (ex.response) {
                    html += '<div class="api-detail-label">Example Response</div>';
                    html += '<div class="api-example-json">' + escHtml(ex.response) + '</div>';
                }
                return html;
            }

            function escHtml(s) {
                return s.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
            }

            function buildTryIt(ep, idx) {
                const hasPathParam = ep.path.includes('{');
                const pathParams = [];
                const pathMatch = ep.path.match(/\{([^}]+)\}/g);
                if (pathMatch) pathMatch.forEach(m => pathParams.push(m.replace(/[{}]/g, '')));

                let html = '<div class="api-detail-label">Try It</div>';

                // Path param inputs
                if (pathParams.length) {
                    html += '<div class="api-try-params">';
                    pathParams.forEach(pp => {
                        html += '<div class="api-try-param"><label>' + pp + '</label><input type="text" id="api-try-path-' + idx + '-' + pp + '" placeholder="' + pp + '"></div>';
                    });
                    html += '</div>';
                }

                // Query param inputs for GET endpoints
                if (ep.params && ep.params.length && (ep.method === 'GET')) {
                    html += '<div class="api-try-params">';
                    ep.params.forEach(p => {
                        if (p.name === 'fields') return; // skip fields param in try-it
                        html += '<div class="api-try-param"><label>' + p.name + '</label><input type="text" id="api-try-q-' + idx + '-' + p.name + '" placeholder="' + (p.default || '') + '"></div>';
                    });
                    html += '</div>';
                }

                html += '<div class="api-try-bar">';
                // Body textarea for POST/PUT/PATCH/DELETE with body
                if (ep.bodyFields && ep.bodyFields.length) {
                    const defaultBody = ep.example && ep.example.request ? ep.example.request : '{}';
                    html += '<textarea class="api-try-body" id="api-try-body-' + idx + '">' + escHtml(defaultBody) + '</textarea>';
                }
                html += '<button class="api-try-btn" onclick="window._apiTryIt(' + idx + ')" id="api-try-btn-' + idx + '">&#9654; Send</button>';
                html += '</div>';

                html += '<div id="api-try-result-' + idx + '"></div>';
                return html;
            }

            // Build all sections
            let sectionsHTML = '';

            // Auth section (not a group)
            sectionsHTML += '<div class="docs-subsection" id="api-auth">';
            sectionsHTML += '<h3 class="docs-subsection-title">Authentication</h3>';
            sectionsHTML += '<p class="docs-text">All API v1 endpoints require an API key (except <code>POST /api-keys/bootstrap</code>). Generate keys in <strong>Settings &rarr; API Keys</strong> or via the bootstrap endpoint.</p>';
            sectionsHTML += '<div class="api-detail-label">Two authentication methods</div>';
            sectionsHTML += '<table class="api-params-table"><thead><tr><th>Method</th><th>Format</th><th>Example</th></tr></thead><tbody>';
            sectionsHTML += '<tr><td>Header</td><td>Authorization: Bearer {key}</td><td><code>Authorization: Bearer sk_AbCd...</code></td></tr>';
            sectionsHTML += '<tr><td>Query</td><td>?api_key={key}</td><td><code>/api/v1/system/status?api_key=sk_AbCd...</code></td></tr>';
            sectionsHTML += '</tbody></table>';
            sectionsHTML += '<div class="api-note">Keys use the <code>sk_</code> prefix. The raw key is shown exactly once at creation time. Only a SHA-256 hash is stored server-side. Rate limit: 60 requests per minute per IP.</div>';
            sectionsHTML += '<div class="api-detail-label">Base URL</div>';
            sectionsHTML += '<p class="docs-text">All endpoints are prefixed with <code class="api-base-url">/api/v1</code></p>';
            sectionsHTML += '<div class="api-detail-label">Response Envelope</div>';
            sectionsHTML += '<p class="docs-text">Every response follows this structure:</p>';
            sectionsHTML += '<div class="api-example-json">{\n  "success": true | false,\n  "data": { ... } | null,\n  "error": { "code": "ERROR_CODE", "message": "..." } | null,\n  "pagination": { "page": 1, "limit": 50, "total": 342, "total_pages": 7, "has_next": true, "has_prev": false } | null\n}</div>';
            sectionsHTML += '<div class="api-detail-label">Error Codes</div>';
            sectionsHTML += '<table class="api-params-table"><thead><tr><th>Status</th><th>Code</th><th>Meaning</th></tr></thead><tbody>';
            sectionsHTML += '<tr><td>400</td><td>BAD_REQUEST</td><td>Missing or invalid parameters</td></tr>';
            sectionsHTML += '<tr><td>401</td><td>AUTH_REQUIRED</td><td>No API key provided</td></tr>';
            sectionsHTML += '<tr><td>403</td><td>INVALID_KEY / FORBIDDEN</td><td>Invalid key or insufficient permissions</td></tr>';
            sectionsHTML += '<tr><td>404</td><td>NOT_FOUND</td><td>Resource not found</td></tr>';
            sectionsHTML += '<tr><td>409</td><td>CONFLICT</td><td>Resource already exists or action in progress</td></tr>';
            sectionsHTML += '<tr><td>429</td><td>RATE_LIMITED</td><td>Too many requests</td></tr>';
            sectionsHTML += '<tr><td>500</td><td>*_ERROR</td><td>Internal server error</td></tr>';
            sectionsHTML += '</tbody></table>';
            sectionsHTML += '<div class="api-detail-label">cURL Example</div>';
            sectionsHTML += '<div class="api-example-json">curl -H "Authorization: Bearer sk_abc123..." \\\n     http://localhost:5000/api/v1/system/status</div>';
            sectionsHTML += '</div>';

            // API key input bar
            sectionsHTML += '<div class="api-key-bar">';
            sectionsHTML += '<label>API Key</label>';
            sectionsHTML += '<input type="password" id="api-tester-key" placeholder="sk_..." autocomplete="off">';
            sectionsHTML += '<span class="api-key-status" id="api-key-status">Enter key to test endpoints</span>';
            sectionsHTML += '</div>';

            // Endpoint groups
            let globalIdx = 0;
            const endpointRegistry = [];

            apiGroups.forEach(group => {
                sectionsHTML += '<div class="docs-subsection" id="' + group.id + '">';
                sectionsHTML += '<h3 class="docs-subsection-title">' + group.title + '</h3>';
                sectionsHTML += '<p class="docs-text">' + group.desc + '</p>';

                group.endpoints.forEach(ep => {
                    const idx = globalIdx++;
                    endpointRegistry.push(ep);

                    sectionsHTML += '<div class="api-endpoint" id="api-ep-' + idx + '">';
                    sectionsHTML += '<div class="api-endpoint-header" onclick="this.parentElement.classList.toggle(\'expanded\')">';
                    sectionsHTML += '<span class="api-method ' + methodClass(ep.method) + '">' + ep.method + '</span>';
                    sectionsHTML += '<span class="api-endpoint-path">' + ep.path + '</span>';
                    sectionsHTML += '<span class="api-endpoint-desc">' + ep.desc + '</span>';
                    sectionsHTML += '<span class="api-endpoint-arrow">&#x25B6;</span>';
                    sectionsHTML += '</div>';
                    sectionsHTML += '<div class="api-endpoint-body">';
                    sectionsHTML += '<p class="docs-text" style="margin-top:10px">' + ep.desc + '</p>';
                    sectionsHTML += buildParamsTable(ep.params);
                    sectionsHTML += buildBodyTable(ep.bodyFields);
                    sectionsHTML += buildExample(ep.example);
                    sectionsHTML += buildTryIt(ep, idx);
                    sectionsHTML += '</div></div>';
                });

                sectionsHTML += '</div>';
            });

            // WebSocket section
            sectionsHTML += '<div class="docs-subsection" id="api-websocket">';
            sectionsHTML += '<h3 class="docs-subsection-title">WebSocket Events</h3>';
            sectionsHTML += '<p class="docs-text">SoulSync uses <strong>Socket.IO</strong> for real-time updates. Connect to the same host/port as the web UI. No API key required for WebSocket connections.</p>';
            sectionsHTML += '<table class="docs-table"><thead><tr><th>Event</th><th>Description</th><th>Key Fields</th></tr></thead><tbody>';
            sectionsHTML += '<tr><td><code>download_progress</code></td><td>Per-track download progress</td><td>title, percent, speed, eta</td></tr>';
            sectionsHTML += '<tr><td><code>download_complete</code></td><td>Track finished downloading</td><td>title, artist, album, file_path</td></tr>';
            sectionsHTML += '<tr><td><code>batch_progress</code></td><td>Album/playlist batch status</td><td>batch_id, completed, total, current_track</td></tr>';
            sectionsHTML += '<tr><td><code>worker_status</code></td><td>Enrichment worker updates</td><td>worker, status, matched, total, current</td></tr>';
            sectionsHTML += '<tr><td><code>scan_progress</code></td><td>Library/quality/duplicate scan</td><td>type, progress, total, current</td></tr>';
            sectionsHTML += '<tr><td><code>system_status</code></td><td>Service connectivity changes</td><td>service, connected, rate_limited</td></tr>';
            sectionsHTML += '<tr><td><code>activity</code></td><td>Activity feed entries</td><td>timestamp, type, message</td></tr>';
            sectionsHTML += '<tr><td><code>wishlist_update</code></td><td>Wishlist item changes</td><td>action, track_id, track_name</td></tr>';
            sectionsHTML += '<tr><td><code>automation_run</code></td><td>Automation execution events</td><td>automation_id, status, result</td></tr>';
            sectionsHTML += '</tbody></table>';
            sectionsHTML += '<div class="api-detail-label">JavaScript Example</div>';
            sectionsHTML += '<div class="api-example-json">import { io } from "socket.io-client";\n\nconst socket = io("http://localhost:5000");\n\nsocket.on("download_progress", (data) =&gt; {\n  console.log(`${data.title}: ${data.percent}%`);\n});\n\nsocket.on("worker_status", (data) =&gt; {\n  console.log(`${data.worker}: ${data.status} (${data.matched}/${data.total})`);\n});\n\nsocket.on("activity", (data) =&gt; {\n  console.log(`[${data.timestamp}] ${data.message}`);\n});</div>';
            sectionsHTML += '</div>';

            // Wire up API key status indicator
            setTimeout(() => {
                const keyInput = document.getElementById('api-tester-key');
                const keyStatus = document.getElementById('api-key-status');
                if (keyInput && keyStatus) {
                    keyInput.addEventListener('input', () => {
                        const val = keyInput.value.trim();
                        if (!val) {
                            keyStatus.textContent = 'Enter key to test endpoints';
                            keyStatus.classList.remove('connected');
                        } else if (val.startsWith('sk_')) {
                            keyStatus.textContent = 'Key set \u2713';
                            keyStatus.classList.add('connected');
                        } else {
                            keyStatus.textContent = 'Key should start with sk_';
                            keyStatus.classList.remove('connected');
                        }
                    });
                }
            }, 0);

            // Register the try-it handler on window
            window._apiEndpointRegistry = endpointRegistry;
            window._apiTryIt = async function(idx) {
                const ep = endpointRegistry[idx];
                const btn = document.getElementById('api-try-btn-' + idx);
                const resultDiv = document.getElementById('api-try-result-' + idx);
                const apiKey = document.getElementById('api-tester-key')?.value?.trim();

                if (!apiKey) {
                    resultDiv.innerHTML = '<div class="api-response-panel"><div class="api-response-header"><span style="color:#f14668">Enter your API key above first</span></div></div>';
                    return;
                }

                // Build path
                let path = ep.path;
                const pathMatch = path.match(/\{([^}]+)\}/g);
                if (pathMatch) {
                    for (const m of pathMatch) {
                        const paramName = m.replace(/[{}]/g, '');
                        const input = document.getElementById('api-try-path-' + idx + '-' + paramName);
                        const val = input?.value?.trim();
                        if (!val) {
                            resultDiv.innerHTML = '<div class="api-response-panel"><div class="api-response-header"><span style="color:#f14668">Fill in path parameter: ' + paramName + '</span></div></div>';
                            return;
                        }
                        path = path.replace(m, encodeURIComponent(val));
                    }
                }

                // Build query string for GET
                let qs = '';
                if (ep.method === 'GET' && ep.params) {
                    const parts = [];
                    ep.params.forEach(p => {
                        if (p.name === 'fields') return;
                        const input = document.getElementById('api-try-q-' + idx + '-' + p.name);
                        const val = input?.value?.trim();
                        if (val) parts.push(encodeURIComponent(p.name) + '=' + encodeURIComponent(val));
                    });
                    if (parts.length) qs = '?' + parts.join('&');
                }

                const url = '/api/v1' + path + qs;
                const fetchOpts = {
                    method: ep.method === 'PATCH' ? 'PATCH' : ep.method,
                    headers: { 'Authorization': 'Bearer ' + apiKey }
                };

                // Body
                if (ep.bodyFields && ep.bodyFields.length) {
                    const bodyEl = document.getElementById('api-try-body-' + idx);
                    if (bodyEl) {
                        fetchOpts.headers['Content-Type'] = 'application/json';
                        fetchOpts.body = bodyEl.value;
                    }
                }

                btn.classList.add('loading');
                btn.innerHTML = '&#9203; Sending...';
                resultDiv.innerHTML = '';

                const startTime = performance.now();
                try {
                    const resp = await fetch(url, fetchOpts);
                    const elapsed = Math.round(performance.now() - startTime);
                    let bodyText;
                    try { bodyText = await resp.text(); } catch(e) { bodyText = '(empty response)'; }

                    let formatted = bodyText;
                    try {
                        const parsed = JSON.parse(bodyText);
                        formatted = JSON.stringify(parsed, null, 2);
                    } catch(e) {}

                    const statusClass = resp.status < 300 ? 's2xx' : resp.status < 500 ? 's4xx' : 's5xx';
                    resultDiv.innerHTML = '<div class="api-response-panel">' +
                        '<div class="api-response-header">' +
                        '<span class="api-response-status ' + statusClass + '">' + resp.status + ' ' + resp.statusText + '</span>' +
                        '<span class="api-response-time">' + elapsed + 'ms</span>' +
                        '</div>' +
                        '<div class="api-response-body">' + syntaxHighlight(escHtml2(formatted)) + '</div>' +
                        '</div>';
                } catch(err) {
                    resultDiv.innerHTML = '<div class="api-response-panel"><div class="api-response-header"><span class="api-response-status s5xx">Network Error</span></div><div class="api-response-body">' + escHtml2(err.message) + '</div></div>';
                }
                btn.classList.remove('loading');
                btn.innerHTML = '&#9654; Send';
            };

            function escHtml2(s) {
                return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
            }

            function syntaxHighlight(json) {
                return json.replace(/"([^"]+)":/g, '<span class="json-key">"$1"</span>:')
                           .replace(/: "((?:[^"\\]|\\.)*)"/g, ': <span class="json-string">"$1"</span>')
                           .replace(/: (-?\d+\.?\d*)/g, ': <span class="json-number">$1</span>')
                           .replace(/: (true|false)/g, ': <span class="json-bool">$1</span>')
                           .replace(/: (null)/g, ': <span class="json-null">$1</span>');
            }

            return sectionsHTML;
        }
    }
];

function _showDebugTextModal(text) {
    // Remove existing modal if any
    const existing = document.getElementById('debug-text-modal');
    if (existing) existing.remove();

    const overlay = document.createElement('div');
    overlay.id = 'debug-text-modal';
    overlay.style.cssText = 'position:fixed;top:0;left:0;width:100%;height:100%;background:rgba(0,0,0,0.7);z-index:10000;display:flex;align-items:center;justify-content:center;';
    overlay.onclick = (e) => { if (e.target === overlay) overlay.remove(); };

    const modal = document.createElement('div');
    modal.style.cssText = 'background:#1a1a2e;border:1px solid rgba(255,255,255,0.1);border-radius:12px;padding:20px;width:90%;max-width:700px;max-height:80vh;display:flex;flex-direction:column;gap:12px;';

    const header = document.createElement('div');
    header.style.cssText = 'display:flex;justify-content:space-between;align-items:center;';
    header.innerHTML = '<span style="color:#fff;font-weight:600;">Debug Info — Select All &amp; Copy</span>';
    const closeBtn = document.createElement('button');
    closeBtn.textContent = '\u2715';
    closeBtn.style.cssText = 'background:none;border:none;color:#888;font-size:18px;cursor:pointer;';
    closeBtn.onclick = () => overlay.remove();
    header.appendChild(closeBtn);

    const ta = document.createElement('textarea');
    ta.value = text;
    ta.readOnly = true;
    ta.style.cssText = 'width:100%;height:50vh;background:#0d0d1a;color:#e0e0e0;border:1px solid rgba(255,255,255,0.1);border-radius:8px;padding:12px;font-family:monospace;font-size:12px;resize:none;outline:none;';

    modal.appendChild(header);
    modal.appendChild(ta);
    overlay.appendChild(modal);
    document.body.appendChild(overlay);

    // Auto-select all text for easy copying
    ta.focus();
    ta.select();
}

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

    // Add debug info panel to sidebar header
    const sidebarHeader = document.querySelector('.docs-sidebar-header');
    if (sidebarHeader) {
        const debugWrap = document.createElement('div');
        debugWrap.className = 'docs-debug-wrap';
        debugWrap.innerHTML = `
            <button class="docs-debug-button">&#x1F4CB; Copy Debug Info</button>
            <div class="docs-debug-options">
                <div class="docs-debug-row">
                    <label>Log lines</label>
                    <select class="docs-debug-select" id="debug-log-lines">
                        <option value="20">20</option>
                        <option value="50">50</option>
                        <option value="100" selected>100</option>
                        <option value="200">200</option>
                        <option value="500">500</option>
                    </select>
                </div>
                <div class="docs-debug-row">
                    <label>Log source</label>
                    <select class="docs-debug-select" id="debug-log-source">
                        <option value="app">app.log</option>
                        <option value="post_processing">post_processing.log</option>
                        <option value="acoustid">acoustid.log</option>
                        <option value="source_reuse">source_reuse.log</option>
                    </select>
                </div>
            </div>
        `;
        sidebarHeader.appendChild(debugWrap);

        const debugBtn = debugWrap.querySelector('.docs-debug-button');
        debugBtn.onclick = async () => {
            const logLines = document.getElementById('debug-log-lines').value;
            const logSource = document.getElementById('debug-log-source').value;
            try {
                debugBtn.textContent = 'Collecting...';
                const resp = await fetch(`/api/debug-info?lines=${logLines}&log=${logSource}`);
                const data = await resp.json();

                const ck = '\u2713';
                const ex = '\u2717';
                let text = 'SoulSync Debug Info\n';
                text += '═══════════════════════════════════\n\n';

                text += '── System ──\n';
                text += `Version:     ${data.version}\n`;
                text += `OS:          ${data.os}${data.docker ? ' (Docker)' : ''}\n`;
                text += `Python:      ${data.python}\n`;
                text += `Uptime:      ${data.uptime || 'unknown'}\n`;
                text += `Memory:      ${data.memory_usage || '?'} (system: ${data.system_memory || '?'})\n`;
                text += `CPU:         ${data.cpu_percent || '?'}\n`;
                text += `Threads:     ${data.thread_count || '?'}\n\n`;

                text += '── Services ──\n';
                text += `Music Source:  ${data.services?.music_source || 'unknown'}\n`;
                text += `Spotify:       ${data.services?.spotify_connected ? ck + ' Connected' : ex + ' Disconnected'}${data.services?.spotify_rate_limited ? ' (RATE LIMITED)' : ''}\n`;
                text += `Media Server:  ${data.services?.media_server_type || 'none'} ${data.services?.media_server_connected ? ck + ' Connected' : ex + ' Disconnected'}\n`;
                text += `Soulseek:      ${data.services?.soulseek_connected ? ck + ' Connected' : ex + ' Disconnected'}\n`;
                text += `Tidal:         ${data.services?.tidal_connected ? ck + ' Connected' : ex + ' Disconnected'}\n`;
                text += `Qobuz:         ${data.services?.qobuz_connected ? ck + ' Connected' : ex + ' Disconnected'}\n`;
                text += `Download Mode: ${data.services?.download_source || 'unknown'}\n\n`;

                text += '── Library ──\n';
                text += `Artists:  ${data.library?.artists?.toLocaleString() || '0'}\n`;
                text += `Albums:   ${data.library?.albums?.toLocaleString() || '0'}\n`;
                text += `Tracks:   ${data.library?.tracks?.toLocaleString() || '0'}\n`;
                text += `Database: ${data.database_size || 'unknown'}\n`;
                text += `Watchlist: ${data.watchlist_count || 0} artists\n`;
                text += `Automations: ${data.automations?.enabled || 0} enabled / ${data.automations?.total || 0} total\n\n`;

                text += '── Active ──\n';
                text += `Downloads: ${data.active_downloads || 0}\n`;
                text += `Syncs:     ${data.active_syncs || 0}\n\n`;

                text += '── Paths ──\n';
                const pathStatus = (exists, writable) => exists ? (writable ? ck + ' ok' : ck + ' exists ' + ex + ' not writable') : ex + ' missing';
                text += `Download: ${data.paths?.download_path || '(not set)'} [${pathStatus(data.paths?.download_path_exists, data.paths?.download_path_writable)}]\n`;
                text += `Transfer: ${data.paths?.transfer_folder || '(not set)'} [${pathStatus(data.paths?.transfer_folder_exists, data.paths?.transfer_folder_writable)}]\n`;
                text += `Staging:  ${data.paths?.staging_folder || '(not set)'} [${data.paths?.staging_folder_exists ? ck + ' ok' : ex + ' missing'}]\n\n`;

                text += '── Config ──\n';
                if (data.config) {
                    text += `Source Mode:      ${data.config.source_mode || 'unknown'}\n`;
                    text += `Quality Profile:  ${data.config.quality_profile || 'default'}\n`;
                    text += `Folder Template:  ${data.config.organization_template || '(default)'}\n`;
                    text += `Post-Processing:  ${data.config.post_processing_enabled ? 'enabled' : 'disabled'}\n`;
                    text += `AcoustID:         ${data.config.acoustid_enabled ? 'enabled' : 'disabled'}\n`;
                    text += `Auto Scan:        ${data.config.auto_scan_enabled ? 'enabled' : 'disabled'}\n`;
                    text += `M3U Export:       ${data.config.m3u_export_enabled ? 'enabled' : 'disabled'}\n`;
                }
                text += '\n';

                text += '── Enrichment Workers ──\n';
                if (data.enrichment_workers) {
                    const active = [], paused = [];
                    Object.entries(data.enrichment_workers).forEach(([name, status]) => {
                        (status === 'active' ? active : paused).push(name);
                    });
                    text += `Active:  ${active.length > 0 ? active.join(', ') : 'none'}\n`;
                    text += `Paused:  ${paused.length > 0 ? paused.join(', ') : 'none'}\n`;
                }
                text += '\n';

                if (data.download_client_failures?.length > 0) {
                    text += '── Download Client Failures ──\n';
                    data.download_client_failures.forEach(f => { text += `  ❌ ${f}\n`; });
                    text += '\n';
                }

                text += '── API Rates (calls/min) ──\n';
                if (data.api_rates) {
                    Object.entries(data.api_rates).forEach(([svc, info]) => {
                        const cpm = info.cpm || 0;
                        const limit = info.limit || '?';
                        const pct = limit ? Math.round(cpm / limit * 100) : 0;
                        text += `${svc.padEnd(14)} ${String(cpm).padStart(5)}/min  (limit: ${limit}, ${pct}%)`;
                        if (info.endpoints && Object.keys(info.endpoints).length > 0) {
                            text += `  endpoints: ${Object.entries(info.endpoints).map(([e, c]) => `${e}:${c}`).join(', ')}`;
                        }
                        text += '\n';
                    });
                }
                if (data.spotify_rate_limit?.active) {
                    const rl = data.spotify_rate_limit;
                    const mins = Math.ceil((rl.remaining_seconds || 0) / 60);
                    text += `\n*** SPOTIFY RATE LIMITED ***\n`;
                    text += `Triggered by: ${rl.endpoint || 'unknown'}\n`;
                    text += `Remaining:    ${mins} minutes\n`;
                    text += `Retry-After:  ${rl.retry_after || '?'}s\n`;
                }
                text += '\n';

                text += `── Logs: ${data.log_source || 'app'}.log (last ${data.recent_logs?.length || 0} lines) ──\n`;
                if (data.recent_logs?.length) {
                    data.recent_logs.forEach(line => { text += line + '\n'; });
                } else {
                    text += '(no log lines)\n';
                }

                // Copy to clipboard — navigator.clipboard requires HTTPS/localhost,
                // so fall back to execCommand for Docker/LAN HTTP access
                let copied = false;
                if (navigator.clipboard && window.isSecureContext) {
                    try {
                        await navigator.clipboard.writeText(text);
                        copied = true;
                    } catch (_) {}
                }
                if (!copied) {
                    const ta = document.createElement('textarea');
                    ta.value = text;
                    ta.style.cssText = 'position:fixed;left:-9999px;top:-9999px';
                    document.body.appendChild(ta);
                    ta.select();
                    try { copied = document.execCommand('copy'); } catch (_) {}
                    document.body.removeChild(ta);
                }
                if (copied) {
                    debugBtn.innerHTML = '&#x2705; Copied!';
                    debugBtn.classList.add('copied');
                    setTimeout(() => {
                        debugBtn.innerHTML = '&#x1F4CB; Copy Debug Info';
                        debugBtn.classList.remove('copied');
                    }, 2000);
                } else {
                    // Clipboard APIs blocked (HTTP over LAN) — show selectable text modal
                    _showDebugTextModal(text);
                    debugBtn.innerHTML = '&#x1F4CB; Copy Debug Info';
                }
            } catch (err) {
                debugBtn.innerHTML = '&#x274C; Failed';
                console.error('Debug info error:', err);
                setTimeout(() => { debugBtn.innerHTML = '&#x1F4CB; Copy Debug Info'; }, 2000);
            }
        };
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

    // Scroll a target element into view within the docs-content container.
    // Uses manual offsetTop calculation instead of scrollIntoView to avoid
    // misalignment caused by lazy-loaded images that haven't reserved height yet.
    // Does an initial scroll, then a correction after images near the target load.
    function scrollDocTarget(target) {
        if (!target || !docsContent) return;
        suppressScrollSpy();

        function calcOffset(el) {
            let offset = 0;
            let current = el;
            while (current && current !== docsContent) {
                offset += current.offsetTop;
                current = current.offsetParent;
            }
            return offset;
        }

        // Initial scroll
        docsContent.scrollTop = calcOffset(target);

        // Correction pass after lazy images near the target have had time to load
        // and shift layout. Two passes cover most reflow scenarios.
        setTimeout(() => { docsContent.scrollTop = calcOffset(target); }, 150);
        setTimeout(() => { docsContent.scrollTop = calcOffset(target); }, 500);
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

            // Scroll to section
            const target = document.getElementById('docs-' + sectionId);
            scrollDocTarget(target);
        });
    });

    // Child click → scroll to subsection
    nav.querySelectorAll('.docs-nav-child').forEach(child => {
        child.addEventListener('click', (e) => {
            e.stopPropagation();
            nav.querySelectorAll('.docs-nav-child').forEach(c => c.classList.remove('active'));
            child.classList.add('active');

            // Keep parent section expanded
            const target = document.getElementById(child.dataset.target);
            scrollDocTarget(target);
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
    if (typeof navigateToPage === 'function') navigateToPage('help');
    // Wait for docs to initialize, then use manual scroll with correction passes
    setTimeout(() => {
        const target = document.getElementById(sectionId);
        const docsContent = document.getElementById('docs-content');
        if (target && docsContent) {
            function calcOffset(el) {
                let offset = 0;
                let current = el;
                while (current && current !== docsContent) {
                    offset += current.offsetTop;
                    current = current.offsetParent;
                }
                return offset;
            }
            docsContent.scrollTop = calcOffset(target);
            setTimeout(() => { docsContent.scrollTop = calcOffset(target); }, 150);
            setTimeout(() => { docsContent.scrollTop = calcOffset(target); }, 500);
        }
    }, 300);
}
