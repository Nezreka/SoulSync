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

A row of genre pills at the top of the Discover page. Each pill shows an artist count and whether the genre has been explored — click one to open a **Genre Deep Dive** of that genre's artists.

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
- **The Archives** (Discovery Weekly) — curated content from your collection

Both can be synced to your media server with live progress tracking.
`
        },
        {
            id: 'disc-timemachine',
            title: 'Time Machine',
            lede: 'Browse your discovery pool by decade, from the 1950s to today.',
            body: `
Browse discovery pool content by **decade** — tabs from the 1950s through the 2020s. Each decade pulls top tracks from pool artists active in that era.

![Time Machine decade browser](disc-time-machine.jpg)

> [!TIP]
> Time Machine is perfect for themed listening — throw on the 70s tab for a dinner party or the 90s tab for a nostalgia trip.
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
