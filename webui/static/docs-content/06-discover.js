registerDocsSection({
    id: 'discover',
    title: 'Discover Artists',
    icon: '✨',
    pages: [
        {
            id: 'disc-hero',
            title: 'Featured Artists',
            lede: 'A rotating showcase of recommended artists based on your watchlist — jump into any of them with one click.',
            body: `
The hero slider showcases **recommended artists** based on your watchlist. Each slide shows the artist's image, name, popularity score, genres, and similarity context. Use the arrows or dots to navigate, or click:

- **View Discography** — browse the artist's albums and download
- **Add to Watchlist** — follow this artist for new release scanning
- **Watch All** — add all featured artists to your watchlist at once
- **View Recommended** — see 50+ similar artists with enrichment data

![Featured artist hero slider](disc-hero.jpg)
`
        },
        {
            id: 'disc-playlists',
            title: 'Discovery Playlists',
            lede: 'Auto-generated playlists from your discovery pool and your own listening data.',
            body: `
SoulSync auto-generates playlists from your **discovery pool** (similar artists found during watchlist scans) and your **listening data**:

| Playlist | Description |
|----------|-------------|
| **The Archives** | Your Discover Weekly — curated discovery picks |
| **Daily Mix** | One personalized mix per top library genre |
| **Discovery Shuffle** | Pure random shuffle from the discovery pool — different every refresh |
| **Fresh Tape** | Release Radar — new releases from artists you follow |
| **Genre — X** | Discovery picks within one genre |
| **Hidden Gems** | Low-popularity underground / indie discovery picks |
| **Your Listening Mix** | Tracks from artists matched to what you actually listen to |
| **Popular Picks** | High-popularity tracks from the discovery pool |
| **Seasonal — X** | Holiday / season-themed picks |
| **Time Machine — X** | Tracks from a specific decade |

![Discovery playlist cards](disc-playlists.jpg)

Each playlist can be played in the media player, downloaded, or synced to your media server.

## Genre Explorer

A row of genre pills in the **Explore & Build** zone near the bottom of the Discover page. Each pill shows an artist count and whether the genre has been explored — click one to open a **Genre Deep Dive** of that genre's artists.

![Genre browser](disc-genre-browser.jpg)

## Because You Listen To

One shelf per seed artist — "Because You Listen To X" shows tracks related to artists you actually play, each shelf carrying its seed identity, a truthful reason, and resolved/unavailable counts so you know exactly what's playable.

## Recommended Stations

Stations turn an artist into endless radio with two distinct actions:

- **Play radio** — starts non-stop playback that keeps refilling itself
- **View station** — takes a finite snapshot (up to 40 library tracks) that stays fixed, so you can Download or Sync it without the track list shifting under you

## ListenBrainz Playlists

If ListenBrainz is configured, the Discover page also shows personalized playlists generated from your listening history: Created For You, Your Playlists, and Collaborative playlists.
`
        },
        {
            id: 'disc-build',
            title: 'Build Custom Playlist',
            lede: 'Pick 1–5 seed artists and generate a 50-track playlist from albums by similar artists.',
            body: `
Search for 1–5 seed artists, select them, and click **Generate**. SoulSync finds similar artists, pulls their albums, and assembles a 50-track playlist mixing your picks with new discoveries. You can then download the generated playlist or sync it to your media server.

![Build custom playlist](disc-build-playlist.jpg)

> [!TIP]
> Mixing artists from adjacent genres is a great way to build party or workout playlists that stay coherent but varied.
`
        },
        {
            id: 'disc-seasonal',
            title: 'Seasonal & Curated',
            lede: 'Time-of-year content plus two curated sections that refresh on their own.',
            body: `
The Discover page includes auto-generated seasonal content based on the current time of year, plus two curated sections:

- **Fresh Tape** (Release Radar) — latest drops from recent releases
- **The Archives** (Spotify Discover Weekly) — discovery picks sourced from your Spotify Discover Weekly

Both can be synced to your media server with live progress tracking.
`
        },
        {
            id: 'disc-timemachine',
            title: 'Time Machine',
            lede: 'Browse your discovery pool by decade, from the 1950s to today.',
            body: `
Browse discovery pool content by **decade** — a **Decades** shelf of mix cards from the 1950s through the 2020s. Each decade pulls top tracks from pool artists active in that era.

![Time Machine decade browser](disc-time-machine.jpg)

> [!TIP]
> Time Machine is perfect for themed listening — throw on the 70s mix for a dinner party or the 90s mix for a nostalgia trip.
`
        },
        {
            id: 'disc-artist-map',
            title: 'Artist Map',
            lede: 'Three interactive visualizations of how artists connect to each other.',
            body: `
Three interactive canvas-based visualization modes for exploring artist relationships, accessed from the Discover page:

- **Watchlist Map** — your watched artists as large nodes with similar artists orbiting around them. Reveals connections you might not have noticed
- **Genre Map** — browse all artists by genre with a sidebar picker. Ring-packed clusters, no artist cap. Great for exploring genres you don't normally listen to
- **Artist Explorer** — deep-dive any artist. Ring 1 shows direct similar artists, Ring 2 shows the extended network. Exploring an unknown artist fetches similar artists in real-time and caches them

**Controls:** mouse wheel to zoom, click to explore, hover for tooltips with genre tags. Keyboard shortcuts: **F** / **0** to fit the view, **S** to focus search, **D** to toggle the perf overlay, **H** to toggle similar artists, **+** / **-** to zoom, **Esc** to close.
`
        },
        {
            id: 'disc-stats',
            title: 'Listening Stats',
            lede: 'Analytics about your library and listening activity.',
            body: `
The Stats page shows analytics about your music library and listening activity, split into two tabs: **Listening** and **Library**.

Listening data requires **Listening Stats** to be enabled in Settings → Library ("Enable listening stats collection from media server") — it polls play history from your active media server (Plex, Jellyfin, or Navidrome).

- **Overview** — total plays, listening time, and unique artists, albums, and tracks
- **Top artists, albums, and tracks** — ranked by play count
- **Genre breakdown** — visual distribution of genres across your listening
- **Recently played** — your latest plays
- **Listening timeline** — activity over time

The **Library** tab shows operational facts instead: Library Health, Library Disk Usage, and Database Storage.
`
        },
    ]
});

registerDocsSection({
    id: 'chat-jukebox',
    title: 'Chat & Jukebox',
    icon: '💬',
    pages: [
        {
            id: 'chat-rooms',
            title: 'Chat Rooms',
            lede: 'Soulseek chat rooms and the community room, proxied through your slskd connection.',
            body: `
Open **Chat** in the sidebar — "Soulseek rooms & private messages". Everything here rides on your Soulseek connection: if slskd isn't configured, the page says **"Soulseek (slskd) isn't configured — set it up in Settings to join the chat."** and there is nothing to join yet.

## Layout

- **Guild rail** (left edge) — every Soulseek room you've joined is an icon button. The community room uses the SoulSync logo; other rooms show two-letter initials with the room name as tooltip. Click to switch rooms. Below a divider: the **✉ Direct Messages** puck with an unread-count badge (caps at "99+"), and a **+** button ("Browse Soulseek rooms").
- **Sidebar** — the current room name with a ⌄ caret, the channel list, three social buttons (**⭐ Friends**, **🚫 Blocklist**, **📚 Bookmarks**, each with a count badge), the **Direct Messages** conversation list, and your user panel at the bottom (avatar, name, "Online" or "Read-only").
- **Main area** — the room head, message list, and composer.
- **Right rail** — the ♫ Jukebox panel card, the 🎬 Movie night card, and the user list with a "Find a user…" filter.

The room head shows, in order: **#channel-or-room** title, the room topic as subtitle, **✎** topic edit, **🔍** history search, **📌** pins (with pinned count), **♫ Jukebox**, **🎬 Movie night**, a **SoulSync only / All messages** filter toggle, and **⚙ Chat settings** (admin only).

## Browsing and joining rooms

::: steps
1. Click the **+** button in the guild rail to open **Browse Soulseek Rooms**.
2. Filter with **Search rooms…** (client-side, first 200 rooms shown).
3. Each room shows **# name** plus **N online** and either a **joined** badge or a **Join** button. Private rooms never appear in this list.
4. Click **Join** — you switch to the room immediately and get a "Joined # name" toast.
:::

Room data refreshes on a 4-second poll, but only while the Chat page is visible and the browser tab is foregrounded — the timer stops when you navigate away.

## Leaving rooms

Hover a non-home room icon in the rail to reveal its **×** ("Leave …"), then confirm in the dialog (**Leave Room** / "Leave # X? You can rejoin any time from Browse rooms." / **Leave**). Leaving the room you're viewing drops you back into the home room. The home room itself is left by turning off **Auto-join** in Chat settings — then the page shows a join gate ("You've left the X room." plus a **Join room** button that flips auto-join back on). If you're not joined, the composer is hidden.

> [!NOTE]
> Managing the room set (browse/join/leave) is admin-only. The server reports \`can_manage\`, and room membership is visible to the whole Soulseek network.

## Sending messages

Type in the composer (**Message…**, 1000 characters max) and hit Enter or **Send**. Messages send via \`POST /api/chat/room/message\` and render instantly as an optimistic echo. Multi-line text is flattened to a single line on the wire because slskd drops messages containing newlines.

Two message modes, toggled in the room head:

| Mode | What it means |
|------|---------------|
| **SoulSync only** | Rich envelope format: formatting, replies, reactions, embeds, polls. Only SoulSync clients render it richly. |
| **All messages** | Plain text every Soulseek client in the room can read — no formatting, replies, or attachments. |

The composer toolbar: **bold / italic / strike / code / code-block / quote / spoiler** (hints: \`**bold** · *italic* · \`code\` · ||spoiler||\`), now-playing ♪ (**Share what you're playing (/np)**), wanted 🔍+ (**In Search Of: Post a Wanted card (/want)**), emoji, attach (**Share a file (filepost.dev)**), poll (**Start a room poll**), **GIF**, and **Send**. A reply bar ("↩ Replying to …") appears when you reply; a **New messages ↓** pill appears when you're scrolled up; files can be drag-dropped onto the message list ("Drop file to share in chat").

**Slash commands:** \`/np\`, \`/want\`, \`/iso\`, \`/friends\`, \`/blocklist\`, \`/bookmarks\`, \`/browse <user>\`, \`/upload\`, \`/shrug\`, \`/skip\`, \`/tune\`, \`/topic <text>\`, \`/play <text>\`, \`/poll\`, \`/pin\`, \`/gif\`. An unknown \`/word\` just posts as a plain message.

## Who can send

Admins can always send. Non-admin profiles can only send when **Let other profiles send** (\`soulseek.chat_member_send\`) is enabled in Chat settings. Without sending rights the composer locks ("Read-only — chat sending is admin-only on this server"), your user panel reads "Read-only", and room-management buttons disappear.

## Moderation

A moderator hover action (**🚫 Hidden for the room**) collapses a message for every SoulSync client into a stub — "🚫 Message from **X** hidden by a moderator" — which anyone can click to reveal locally; moderators get an **unhide** button. Moderators can also end any poll or trivia game, close threads, and kill Arcade games.

## Chat settings (⚙, admin only)

| Tab | Controls |
|-----|----------|
| **Profile** | Avatar picker — saves as soon as you pick one. |
| **Notifications** | **Mention sound** — "Play a sound when someone mentions or replies to you. This browser only." |
| **Privacy** | **Share what I'm playing** — "Everyone in the Soulseek room can see it. This browser only." |
| **Room** | **Room name** ("Soulseek room names are case-sensitive."), **Auto-join** ("Join the community room when SoulSync connects."), **Let other profiles send** ("Non-admin profiles can post as this Soulseek account."), **Answer "prove you're human"** ("Auto-answer the challenges some users send before a download.") |
| **Integrations** | **GIPHY API key** ("Free at developers.giphy.com. Enables GIF search."), **filepost.dev API key** ("Free at filepost.dev. Enables file sharing in chat."), **Shared files expire after** (Never / 24 hours / 7 days / 30 days) |

Room and profile settings save to the server; mention sound and now-playing sharing are per-browser.

> [!TIP]
> Click any username to open their user card: **⭐ Add Friend**, **🚫 Block**, **⭐ Bookmark**, **Mute**, **⚔ Challenge**, **Browse files**, **Message**. The peer file browser shows upload speed, queue length, free slots, and shared folders, with format filter pills (All / FLAC / Lossless / MP3 320k / VBR / Other) and a download dock ("N files selected" / size / **Clear** / **Download Selected**).
`
        },
        {
            id: 'chat-pm',
            title: 'Private Messages',
            lede: 'One-to-one Soulseek conversations, with unread tracking that follows you around the app.',
            body: `
## Starting a conversation

- Click a username anywhere → user card → **Message**
- The Social drawer's **💬 Message** buttons, or the peer file browser's **💬 Message**
- Clicking the ✉ puck in the guild rail opens your most recent conversation

## The conversation list

The sidebar's **Direct Messages** section lists every conversation: username, an unread dot, and a **×** ("Close conversation"). Empty state: "No conversations". Closing a conversation hides it locally (remembered per browser); the next unread message from that user un-hides it automatically.

The open-PM head shows the username (plus a ⭐ Friend badge when applicable), the words "private message", **📁 Browse Files**, and **✕ Close Chat**. Closing a conversation you were viewing returns you to the room.

## Unread indicators

- Unread dot on the conversation row
- Count badge on the ✉ puck (caps at "99+")
- A nav badge total across the app
- A rising PM count fires a toast: "New Soulseek message from X — open Chat to reply"
- Room mentions ping you app-wide too: "💬 X mentioned you in # Y" fires even when you're off the Chat page

## PM composer limits

PMs are **plain-text only** — the formatting toolbar, GIF, poll, now-playing, and wanted buttons are room-only. Emoji and file attach still work in PMs.

> [!NOTE]
> PMs use the same Soulseek connection as rooms, so the slskd prerequisite and the admin / "Let other profiles send" rules apply identically.
`
        },
        {
            id: 'jukebox',
            title: 'Jukebox Auto-DJ',
            lede: 'A room-wide YouTube jukebox: queue tracks, vote on what plays next, and let Auto-DJ keep it fed.',
            body: `
The Jukebox lives in the Chat page header and the right-rail panel (toggled by the room-head **♫ Jukebox** button — "Room jukebox — listen together, vote on what plays next"). Everything is YouTube-based, and jukebox state is derived from the room's protocol events, so every SoulSync client in the room agrees on the queue.

The headbar shows the **♫ Jukebox** brand, **♪ N listening**, the **📻 Auto-DJ** toggle ("Auto-DJ: when the queue runs dry, keep the music going with related tracks"), an **Add a song or paste a YouTube link…** input (200 chars) with an **Add** button, and a results dropdown.

## Tuning in and playback controls

- **▶ Tune in** / **Tune out** — tuning in announces you as a listener; tuning out destroys the player. The player follows the **room, not the panel**: a tuned-in listener keeps hearing while reading PMs or with the panel closed.
- **⏭ Skip N/M** — vote to skip ("Vote to skip this track (majority of listeners)"). Skipping needs a majority of tuned-in listeners (minimum 1).
- **🎧 Audio only ⇄ 🎬 Video** — persisted per browser.
- **Volume slider** (0–100) — persisted per browser, applied live.

There is no pause button — skip is the control.

## The queue

Add via the headbar input or **\`/play <text>\`** in chat. A pasted YouTube link goes straight into the queue; text goes through search and opens the **♫ Add to the jukebox** modal with video cards (thumbnail, title, channel, views, duration) to pick from.

| Rule | Detail |
|------|--------|
| **Cap** | 25 tracks |
| **Dedupe** | By video id — first submitter keeps attribution |
| **Ordering** | Vote winner plays first, otherwise FIFO — there is no drag-to-reorder |
| **Voting** | **▲ N** per row ("Vote to play this next"); your latest vote counts; the leader is marked "up next" |
| **Removal** | Your own submissions get a **×** ("Remove your submission") — only the submitter can remove |
| **History** | **Recently played (N)** expands the last 10 tracks, each with a **↻** ("Queue it again") button |

## DJ election

The DJ is the lexicographically-smallest protocol-capable SoulSync username in the room — clients that only speak the envelope format can never be elected. The DJ advances the queue when a track ends (detected via the player's ended state, elapsed-vs-duration, or a 15-minute cap for unknown durations, with a 15-second double-fire guard). If the DJ vanishes, any capable client takes over after 45 seconds. The watchdog runs on the 4-second room refresh even with the panel closed.

## Auto-DJ

Flip the **📻 Auto-DJ** headbar toggle (a shared room toggle — latest sender wins; toast: "📻 Auto-DJ on — the queue keeps itself fed"). When the queue runs dry and at least one listener is tuned in, the DJ requests a pick with the seed title and an avoid-list of recently played tracks: Last.fm similar tracks first, then similar artists, then your local similar-artist graph, falling back to searching the seed title. Auto picks queue marked **📻 auto** with a "why" credit ("similar to X") and stay vote- and skip-able. Cooldown is 25 seconds, and it never runs in an empty, unwatched room — "Auto-DJ is on, but it needs a starting point — add one song and it takes over from there."

> [!TIP]
> Jukebox needs the same Soulseek connection as chat. If the page shows the "slskd isn't configured" notice, neither rooms nor the Jukebox will work until Settings → Sources is set up.
`
        },
    ]
});
