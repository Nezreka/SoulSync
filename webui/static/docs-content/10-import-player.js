registerDocsSection({
    id: 'import',
    needs: 'music.import',
    title: 'Import Music',
    icon: '📥',
    pages: [
        {
            id: 'imp-setup',
            needs: 'admin',
            title: 'Import Folder',
            lede: 'Drop audio files in a folder — or upload from your browser — and SoulSync identifies and files them.',
            body: `
Set your **import folder path** in Settings → Library → Folders. Drop audio files you want to import into it. Album folders (e.g. \`Artist - Album/\`), loose files that share an album tag, and single files each become one item in the inbox.

You can also **upload from the browser**: drop files or a folder anywhere on the list, or use **Add files** / **Add a folder**. A dropped folder keeps its name, so an album lands as one item.

![Import page header with the import folder path and Add files drop strip](imp-setup.jpg)

The strip under the page header shows the folder, how many items and files are in it, and the auto-import switch.

![Import inbox showing the items waiting in the import folder](imp-staging.jpg)

> [!TIP]
> **Files not showing up?** The page says so when it cannot read a folder. On a bind mount that is nearly always ownership: the folder's owner has to match the container's PUID/PGID (TrueNAS datasets are usually owned by the \`apps\` user, uid 568, while the container defaults to 1000).
`
        },
        {
            id: 'imp-workflow',
            title: 'The Inbox',
            lede: 'One row per item, with its state and the actions it earns.',
            body: `
![Import inbox rows with their states and per-item actions](imp-workflow.jpg)

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
            needs: 'admin',
            title: 'Auto-import',
            lede: 'Let the watcher identify and import high-confidence matches on its own.',
            body: `
With the switch on, a watcher checks the folder on a timer, identifies each item from its tags, folder name, or AcoustID fingerprint, matches the files to the release's tracklist, and imports anything above the **confidence line** on its own.

![Auto-import switch in the Import page header strip](imp-auto.jpg)

- Between 70% and the confidence line, it **waits for review**
- Below 70%, it **needs identifying**

A folder imports as one album. When the release it picked is missing some of the folder's songs, the same album's other editions are tried first, and the one that holds the most of the folder wins. Files no edition holds stay in the import folder, and **What happened** on the row lists each one with the reason. **Retry** and **Fix match** keep what's left on the release the rest of the folder went to, so the album never ends up split over two releases in your library.

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

![The matcher pairing release tracks with your files](imp-matching.jpg)
`
        },
        {
            id: 'imp-singles',
            title: 'Singles',
            lede: 'Single files get the same matcher with track candidates instead of releases.',
            body: `
A single file gets the same matcher with track candidates instead of releases. Pick the track it is and import — or pick nothing and it imports from its own tags.

![The matcher in singles mode with track candidates](imp-singles.jpg)
`
        },
        {
            id: 'imp-textfile',
            needs: 'music.playlist_sync',
            title: 'Import from Text File',
            lede: 'Turn a CSV, TSV, TXT, or M3U playlist into a mirrored playlist on the sync page.',
            body: `
The sync page's **Import Playlist from File** tab takes a **CSV**, **TSV**, **TXT**, or **M3U/M3U8** file and mirrors it as a playlist (source \`file\`):

::: steps
1. Drop the file on the tab or browse to select it. It's parsed entirely in the browser — nothing is uploaded.
2. Tune the reading: for **TXT** files pick the line format (**Artist - Title** or **Title - Artist**) and the separator — \` - \` , \` — \`, \`|\`, or \`/\`. For **CSV/TSV**, the column delimiter (tab, semicolon, or comma) is sniffed automatically, then map columns to **Track**, **Artist**, **Album**, **Duration**, or **Skip**.
3. Preview every parsed row, give the playlist a name (M3U files pre-fill it), and import — the playlist lands in your **mirrored playlists** on the sync page, ready to match and download.
:::

![Add a playlist from a CSV, TSV, TXT or M3U file](imp-textfile.jpg)
`
        },
    ]
});

registerDocsSection({
    id: 'player',
    needs: 'music.access',
    title: 'Media Player',
    icon: '▶️',
    pages: [
        {
            id: 'player-controls',
            title: 'Playback Controls',
            lede: 'An always-available sidebar player plus a full-screen Now Playing experience.',
            body: `
The sidebar media player is always visible when a track is loaded. It shows album art, track info, a seekable progress bar, and playback controls (play/pause, previous, next, volume, repeat, shuffle).

![Mini-player pill with album art, progress bar and playback controls](player-sidebar.jpg)

Click the sidebar player to open the **Now Playing modal** — a full-screen experience with large album art, ambient glow (dominant color from cover art), a frequency-driven audio visualizer, and expanded controls.

![Now Playing view with large album art and ambient glow](player-nowplaying.jpg)
`
        },
        {
            id: 'player-streaming',
            title: 'Streaming & Sources',
            lede: 'Audio streams the file on SoulSync disk — with a Navidrome fallback when it is not there.',
            body: `
Playback serves the **local file on SoulSync's disk**: the browser asks \`/api/library/play\` for a track and the audio streams from \`/stream/audio\`.

![Now Playing details pane showing the track's source and format](player-streaming.jpg)

- **Navidrome fallback** — when the file isn't on SoulSync's disk (e.g. the library isn't mounted into the container) and Navidrome is the active media server, playback falls back to streaming via Navidrome's own Subsonic-compatible stream API — no disk mount needed. Plex and Jellyfin have no such fallback, so their files must be reachable on SoulSync's disk.

The browser auto-detects which audio formats it can play. Album art, track metadata, and ambient colors are all pulled from your server in real time.

**Lyrics** in Now Playing come from the first place that has them:

1. the \`.lrc\` (synced) or \`.txt\` (plain) file beside the song
2. lyrics embedded in the file's own tags: ID3 USLT and synced SYLT (MP3), LYRICS or UNSYNCEDLYRICS (FLAC, Ogg, Opus), and the lyrics atom (M4A). Files tagged by beets or Picard often carry these only
3. LRClib

An \`[offset:]\` line in LRC lyrics is honoured, so lyrics that run early or late line up. When LRClib finds lyrics for a song in your library, SoulSync saves them as a \`.lrc\` (or \`.txt\`) beside the file, so the next play needs no lookup. It never overwrites a sidecar you already have, skips locked albums and folders it can't write to, and never changes the audio file. Turn it off in Settings, Library, Post-Processing: **Save lyrics the player finds as .lrc files beside library songs** (it's also off whenever .lrc files are turned off for downloads).
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

![The queue list inside Now Playing](player-queue.jpg)
`
        },
        {
            id: 'player-shortcuts',
            title: 'Keyboard Shortcuts',
            lede: 'Control playback without touching the mouse.',
            body: `
![The always-available mini-player controlled by these shortcuts](player-sidebar.jpg)

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

registerDocsSection({
    id: 'player-home-videos',
    needs: 'everyone',
    title: 'Home Videos',
    icon: '🎥',
    pages: [
        {
            id: 'home-what',
            title: 'Your Home Videos',
            lede: 'Clips from your phone or camera, by the day they were filmed.',
            body: `
**Home videos** is a page of its own in the player's sidebar (on a phone, under **More**). It shows:

- **On this day**: clips filmed on today's date in earlier years, with how long ago.
- **Folders**: each folder your clips sit in is an album, so the way you sorted them on disk carries over. Open one to see it in order, play it all, or download the whole album.
- **The timeline**: every clip by month, newest first, with the day it was filmed, its length and how far you got.

Switch between **Timeline** and **Folders** at the top, or type in **Find a video** to search names, notes and folders.

Home videos are never looked up on TMDB or anywhere else, never matched, renamed or moved. A clip is named from its file: a camera's name like \`VID_20261031_183012\` becomes "Oct 31, 2026, 6:30 PM" until someone names it. The date comes from the clip itself (its own creation time), else a date in the name, else the file's date. The picture is a frame a few seconds in, made in the background.

They play, resume, cast and download like everything else in the player. A clip whose file is gone shows greyed in its album until it comes back.
`
        },
        {
            id: 'home-edit',
            title: 'Naming and Dating a Clip',
            lede: 'Give a clip a name, fix its date, pick a better picture, add notes.',
            body: `
Hover a clip and press the pencil (on a phone, press and hold; with a mouse, right-click). The sheet has:

- **Name**: shows what the file was called. Your name is kept, even after a rescan. **Use the automatic name** goes back.
- **Date filmed**: says where the date came from. Change it if the camera's clock was wrong; the clip moves to the right month and On this day follows.
- **Picture**: four frames from the clip. Pick one and it's the picture at once.
- **Notes**: who's in it, where it was. Notes are searched too.

Admins can edit by default. In Player setup, **Home videos**, an admin can let everyone who can see the clips edit them.
`
        },
        {
            id: 'home-setup',
            title: 'Setting Up Home Videos (Admin)',
            needs: 'admin',
            lede: 'Add your server\'s home video library or a folder, right from the player.',
            body: `
Open **Player setup** (the player's Settings, Libraries) and go to **Home videos**:

- **On Plex or Jellyfin**: a Plex library of the "Other videos" type, or a Jellyfin "Home videos and photos" library, is listed with how many clips it has. **Add it** and it's read at once, with progress on the card.
- **Folders on disk**: type a folder and SoulSync checks it can read it and how many clips are in it before you add it. Works with or without a media server.

The same lists are in Settings: **Connections** (Home videos libraries, a Scan switch each) and **Library, Folders** (Home videos folders). New clips are picked up after every library scan.

Once there are clips, the **Home videos** library appears. Rename it, split folders off into more home libraries (Kids, Grandparents), and choose who sees each one under **Who sees what**: you may want to switch them off for guests. Home libraries start with **Show in the guide** off; switch it on and the guide gets a Home videos channel.
`
        },
    ]
});

registerDocsSection({
    id: 'player-downloads',
    needs: 'everyone',
    title: 'Player Downloads',
    icon: '⬇️',
    pages: [
        {
            id: 'pdl-download',
            title: 'Downloading Something',
            lede: 'Keep a movie, a season, an album or a playlist on your phone, or save it to this computer.',
            body: `
Every movie, episode, season, show, album, playlist, audiobook and podcast in the player has a **Download** button. It also says where the thing already is: **Waiting**, **60%**, **On Pixel 8** or **Downloaded**.

The button opens the download sheet:

- **Download to**: *This computer* (your browser saves the file) or one of your phones and tablets that has the SoulSync app signed in to your profile.
- **Quality**: *Original* is the file as it is on the server. Smaller copies (**1080p**, **720p**, **480p** for video, **320**, **256** or **128 kbps** for music) are made by the server first. Each one shows its real size before you commit, and the quality you pick is remembered for next time. A copy never goes above the source: a 720p file only offers 720p and 480p.
- **Whole things in one go**: a season, a show, an album, a playlist, a book, or a podcast's newest episodes.
- **Fits**: for a phone, the sheet says how much room is left under its storage limit.

When you pick **This computer**, the server gets the file ready (a smaller copy shows its progress while it's made) and then your browser downloads it. When you pick a phone, the download waits for it: the phone picks it up the next time the app is open and online.

Subtitles and chapter marks come along in a smaller copy, so an offline episode plays like a streamed one.
`
        },
        {
            id: 'pdl-smart',
            title: 'Smart Downloads',
            lede: 'Let the phone keep itself topped up.',
            body: `
When you send a show, a playlist or a podcast to a phone, the sheet offers a **Smart download**:

- **Shows**: keep the next 1, 3 or 5 unwatched episodes ready. As you watch, the next ones download on their own. **Delete an episode once it's watched** clears the ones you've seen.
- **Playlists**: **Keep in step with the playlist**. Songs added later download, songs taken out are removed. Works for your own playlists, mirrored ones and your Discover mixes.
- **Podcasts**: keep the newest few episodes ready, and delete the ones you've played.

Change or turn off a smart download from the sheet, or from the Downloads page. Turning it off keeps what it already downloaded.
`
        },
        {
            id: 'pdl-page',
            title: 'The Downloads Page',
            lede: 'See and manage what is on each of your devices, from any screen.',
            body: `
**Downloads** in the player's sidebar lists your devices at the top, each with how much room it uses and when it was last seen. Pick one to see what's on it, grouped by show, movie, album, playlist, book and podcast, with each item's state: on the phone, downloading, waiting, watched.

From there you can **add the rest** of a season, change a group's **smart settings**, **remove** an item or **remove all** of a group, and cancel something still waiting. Changes reach the device the next time it's online.

**This computer** lists the files you saved from this browser. **Download again** gets one in another quality; **Forget** only takes it off the list (the file stays where your browser saved it).

Plays, positions and watched marks made offline on a phone are sent back when it reconnects, so Continue Watching and your listening history stay right.
`
        },
        {
            id: 'pdl-admin',
            title: 'Download Settings (Admin)',
            needs: 'admin',
            lede: 'Who may download, and how much work the server does for smaller copies.',
            body: `
In the player's **Settings**, under **Downloads**:

- **Members may ask for smaller copies**: off means members download originals only. Admins always can.
- **Copies made at once**: how many smaller copies the server makes at the same time. Each one keeps a CPU core or the GPU busy (the same encoder the player uses for streaming, so a GPU is used when it works).
- **Room for smaller copies**: how much disk the copies may use. When it's full, the copies used longest ago go first. A copy is made once per file and quality and shared by everyone who asks for it.

Whether a profile may download at all is set per person on the player's **Users & Downloads** page.
`
        },
    ]
});

registerDocsSection({
    id: 'player-notifications',
    needs: 'everyone',
    title: 'Player Notifications',
    icon: '🔔',
    pages: [
        {
            id: 'pnotify-what',
            title: 'What You Hear About',
            lede: 'The bell at the top of the player, and your phone, tell you what is new for you. Only what is new for you.',
            body: `
The **bell** in the player's header shows how many notifications you haven't read. Click it for the list: each one opens what it's about and counts as read. **Mark all read** clears the count, and the **x** on a row dismisses it.

Each kind is a switch in **Settings → Notifications**, and your phone app reads the same switches:

| Kind | When | On to start |
|---|---|---|
| **New episodes** | A new episode of a show you're watching (you played an episode in the last 120 days, or it's on your watchlist). Never more than one a day per show: a second episode the same day turns it into "3 new episodes of …". | Yes |
| **Featured films** | A well-reviewed film arrives in the library. TMDB has to rate it 7.0 or better and the other scores SoulSync has (IMDb, Rotten Tomatoes, Metacritic) have to agree. Never more than 3 a week. Turn on **Also films not in your library** to hear about the best of what's just out in cinemas too. | Yes |
| **From your watchlist** | Something on your watchlist is ready to play. | Yes |
| **Your requests** | Something you asked for is ready, or it was declined. | Yes |
| **Season premieres** | A new season of a show you've watched starts in the next few days, and again when episode 1 lands. | Yes |
| **Leaving soon** | Something you started is on the server's Shelf Life list to make room. | No |
| **Downloads ready** | A smart download finished on your phone. Each phone can turn this off for itself. | Yes |
| **Watch party invites** | Someone scheduled a watch party, or asked you to one. | Yes |
| **Messages from the admin** | A message sent from Server Activity. These always come through. | Always |
| **New music** | A new album from an artist you've played a lot lately. | No |
| **Memories** | Home videos filmed on this day in past years, once a day at the time you pick. | No |

Nothing is ever about a title your profile can't see: hidden libraries and rating limits apply to notifications too.

> [!TIP]
> **Send test** in Settings → Notifications shows you what one looks like, in the bell and on your phone.
`
        },
        {
            id: 'pnotify-when',
            title: 'Quiet Hours and the Daily Digest',
            lede: 'Hear about things when it suits you.',
            body: `
- **Quiet hours**: nothing arrives between the two times you set, on your own clock. What came in overnight arrives when quiet hours end. Messages from the admin still come through.
- **Daily digest**: one notification a day, at the time you pick, with everything new instead of one each. Watch party invites and messages from the admin still come straight away.

**Browser notifications**: the web player can also pop notifications up while it's in a background tab. Turn them on from **Settings → Notifications → Browser notifications**; your browser asks once. If you said no before, allow notifications for this site in your browser's site settings.

**On your phone**: the SoulSync app checks for new ones every 15 minutes or so, and whenever you open it. Android asks once for permission, from the app's notification settings. Each kind has its own channel, so Android's own notification settings can tune them too.
`
        },
        {
            id: 'pnotify-admin',
            title: 'For Admins',
            lede: 'One automation keeps everyone up to date.',
            body: `
The **Player Notifications** automation runs every 5 minutes. Each run reads only what changed since the last one (new files, new albums, airing dates), so it costs next to nothing. Its first run only marks where it starts: turning it on never sends anyone the whole library.

A message you send to someone's screen from **Server Activity** is kept in their notifications too, so a phone in a pocket sees it later.
`
        },
    ]
});
