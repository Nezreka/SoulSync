registerDocsSection({
    id: 'import',
    title: 'Import Music',
    icon: '📥',
    pages: [
        {
            id: 'imp-setup',
            title: 'Import Folder',
            lede: 'Drop audio files in a folder — or upload from your browser — and SoulSync identifies and files them.',
            body: `
Set your **import folder path** in Settings → Library → Folders. Drop audio files you want to import into it. Album folders (e.g. \`Artist - Album/\`), loose files that share an album tag, and single files each become one item in the inbox.

You can also **upload from the browser**: drop files or a folder anywhere on the list, or use **Add files** / **Add a folder**. A dropped folder keeps its name, so an album lands as one item.

The strip under the page header shows the folder, how many items and files are in it, and the auto-import switch.

![Import page](imp-staging.jpg)

> [!TIP]
> **Files not showing up?** The page says so when it cannot read a folder. On a bind mount that is nearly always ownership: the folder's owner has to match the container's PUID/PGID (TrueNAS datasets are usually owned by the \`apps\` user, uid 568, while the container defaults to 1000).
`
        },
        {
            id: 'imp-workflow',
            title: 'The Inbox',
            lede: 'One row per item, with its state and the actions it earns.',
            body: `
| State | Meaning |
|-------|---------|
| **Waiting** | Nobody has looked at it yet. Auto-import will, or you can identify it yourself. |
| **Needs review** | A probable match (70–90%). Approve it, fix it in the matcher, or dismiss it. |
| **Needs identifying** | Tags, folder name, and fingerprint all came up empty. Identify it in the matcher. |
| **Importing** | Tagging and moving files, with the live track list. |
| **Failed** | The import did not finish. Retry it, or fix the match. |
| **Imported** / **Dismissed** | History. |

**Needs attention** is the default filter and shows only what needs a person. Tick rows to approve or dismiss several at once, or to import waiting singles straight from their own tags. **Show files** opens the per-file list with length, bitrate, and size.

Keyboard: \`j\`/\`k\` move, \`x\` tick, \`a\` approve, \`d\` dismiss, \`Enter\` opens the matcher.

The **Import Needs Attention** automation trigger fires whenever the watcher leaves something for you, so a notification can reach you without opening the page.
`
        },
        {
            id: 'imp-auto',
            title: 'Auto-import',
            lede: 'Let the watcher identify and import high-confidence matches on its own.',
            body: `
With the switch on, a watcher checks the folder on a timer, identifies each item from its tags, folder name, or AcoustID fingerprint, matches the files to the release's tracklist, and imports anything above the **confidence line** on its own.

- Between 70% and the confidence line, it **waits for review**
- Below 70%, it **needs identifying**

The gear opens the settings: the confidence line, how often the folder is checked, whether matches import without asking, and which quality profile they are checked against.
`
        },
        {
            id: 'imp-matching',
            title: 'The Matcher',
            lede: 'Match your files to the right release, track by track.',
            body: `
**Identify**, **Fix match**, and the per-file "Open in the matcher" link all open the same page: the release on the left, its tracklist on the right.

- **Release** — the pick, with the other candidates under it. Click one to swap. Search when none fit; pick a specific source to bypass the primary one.
- **Tracks** — each release track beside the file matched to it, with the file's own length and bitrate. A length that differs from the release is flagged.
- **Files without a track** — drag one onto a track, or tap it and then the track. The × takes a file off a track.

Under the table, **before it imports**: where each file will land on your naming template, which tags the release will change, and which tracks your library already has (kept or replaced by quality). **Show details** lists it per track.

**Import** tags the matched files with the release's metadata (title, artist, album, track number, cover art) and moves them into your library on the standard file template. Files without a track stay in the import folder.

![The matcher](imp-matching.jpg)
`
        },
        {
            id: 'imp-singles',
            title: 'Singles',
            lede: 'Single files get the same matcher with track candidates instead of releases.',
            body: `
A single file gets the same matcher with track candidates instead of releases. Pick the track it is and import — or pick nothing and it imports from its own tags.
`
        },
        {
            id: 'imp-textfile',
            title: 'Import from Text File',
            lede: 'Turn a CSV, TSV, TXT, or M3U playlist into a mirrored playlist on the sync page.',
            body: `
The sync page's **Import Playlist from File** tab takes a **CSV**, **TSV**, **TXT**, or **M3U/M3U8** file and mirrors it as a playlist (source \`file\`):

::: steps
1. Drop the file on the tab or browse to select it. It's parsed entirely in the browser — nothing is uploaded.
2. Tune the reading: for **TXT** files pick the line format (**Artist - Title** or **Title - Artist**) and the separator — \` - \` , \` — \`, \`|\`, or \`/\`. For **CSV/TSV**, the column delimiter (tab, semicolon, or comma) is sniffed automatically, then map columns to **Track**, **Artist**, **Album**, **Duration**, or **Skip**.
3. Preview every parsed row, give the playlist a name (M3U files pre-fill it), and import — the playlist lands in your **mirrored playlists** on the sync page, ready to match and download.
:::

![Text file import](imp-textfile.jpg)
`
        },
    ]
});

registerDocsSection({
    id: 'player',
    title: 'Media Player',
    icon: '▶️',
    pages: [
        {
            id: 'player-controls',
            title: 'Playback Controls',
            lede: 'An always-available sidebar player plus a full-screen Now Playing experience.',
            body: `
The sidebar media player is always visible when a track is loaded. It shows album art, track info, a seekable progress bar, and playback controls (play/pause, previous, next, volume, repeat, shuffle).

![Sidebar media player](player-sidebar.jpg)

Click the sidebar player to open the **Now Playing modal** — a full-screen experience with large album art, ambient glow (dominant color from cover art), a frequency-driven audio visualizer, and expanded controls.

![Now Playing modal](player-nowplaying.jpg)
`
        },
        {
            id: 'player-streaming',
            title: 'Streaming & Sources',
            lede: 'Audio streams the file on SoulSync disk — with a Navidrome fallback when it is not there.',
            body: `
Playback serves the **local file on SoulSync's disk**: the browser asks \`/api/library/play\` for a track and the audio streams from \`/stream/audio\`.

- **Navidrome fallback** — when the file isn't on SoulSync's disk (e.g. the library isn't mounted into the container) and Navidrome is the active media server, playback falls back to streaming via Navidrome's own Subsonic-compatible stream API — no disk mount needed. Plex and Jellyfin have no such fallback, so their files must be reachable on SoulSync's disk.

The browser auto-detects which audio formats it can play. Album art, track metadata, and ambient colors are all pulled from your server in real time.
`
        },
        {
            id: 'player-queue',
            title: 'Queue & Smart Radio',
            lede: 'Manage the queue, or let Smart Radio keep the music going.',
            body: `
Add tracks to the queue from the Enhanced Library Manager or download results. Manage the queue in the Now Playing modal: reorder, remove individual tracks, or clear all.

**Smart Radio** mode (toggle in the queue header) automatically adds similar tracks when the queue runs out, based on genre, mood, style, and artist similarity. Playback continues seamlessly.

**Repeat modes**: Off → Repeat All (loop queue) → Repeat One. **Shuffle** randomizes the next track from the remaining queue.

![Queue panel](player-queue.jpg)
`
        },
        {
            id: 'player-shortcuts',
            title: 'Keyboard Shortcuts',
            lede: 'Control playback without touching the mouse.',
            body: `
| Key | Action |
|-----|--------|
| \`Space\` | Play / Pause |
| \`→\` | Seek forward 5s |
| \`←\` | Seek backward 5s |
| \`N\` | Next track in queue |
| \`P\` | Previous track in queue |
| \`↑\` | Volume up |
| \`↓\` | Volume down |
| \`M\` | Mute / Unmute |
| \`Escape\` | Close Now Playing modal |

**Media Session API** — SoulSync integrates with your OS media controls (lock screen, system tray) for play/pause, next/previous, and seek.
`
        },
    ]
});
