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
SoulSync generates playlists from two sources: your **discovery pool** (50 similar artists refreshed during watchlist scans) and your **library listening data**:

| Playlist | Source | Description |
|----------|--------|-------------|
| **Popular Picks** | Discovery Pool | Top tracks from discovery pool artists |
| **Hidden Gems** | Discovery Pool | Rare and deeper cuts from pool artists |
| **Discovery Shuffle** | Discovery Pool | Randomized mix across all pool artists |
| **Recently Added** | Library | Tracks most recently added to your collection |
| **Top Tracks** | Library | Your most-played or highest-rated tracks |
| **Forgotten Favorites** | Library | Tracks you haven't listened to in a while |
| **Decade Mixes** | Library | Tracks grouped by release decade (70s, 80s, 90s, etc.) |
| **Daily Mixes** | Library | Auto-generated daily playlists based on your taste profile |
| **Familiar Favorites** | Library | Well-known tracks from artists you follow |

![Discovery playlist cards](disc-playlists.jpg)

Each playlist can be played in the media player, downloaded, or synced to your media server.

## Genre Browser

Filter discovery pool content by specific genres. Browse available genres and view top tracks within each genre category.

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
            lede: 'Pick 1–5 artists and generate a custom playlist from their catalogs.',
            body: `
Search for 1–5 artists, select them, and click **Generate** to create a custom playlist from their catalogs. You can then download the generated playlist or sync it to your media server.

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

- **Watchlist Constellation** — your watched artists as large nodes with similar artists orbiting around them. Reveals connections you might not have noticed
- **Genre Map** — browse all artists by genre with a sidebar picker. Ring-packed clusters, no artist cap. Great for exploring genres you don't normally listen to
- **Artist Explorer** — deep-dive any artist. Ring 1 shows direct similar artists, Ring 2 shows the extended network. Exploring an unknown artist fetches similar artists in real-time and caches them

**Controls:** mouse wheel to zoom, click to explore, hover for tooltips with genre tags. Keyboard shortcuts: \`?\` for help, \`F\` to fit view, \`S\` to search.
`
        },
        {
            id: 'disc-stats',
            title: 'Listening Stats',
            lede: 'Analytics about your library and listening activity.',
            body: `
The Stats page shows analytics about your music library and listening activity. Requires ListenBrainz or Last.fm scrobbling to be enabled for listening data.

- **Library overview** — total artists, albums, tracks, total file size, format distribution
- **Top artists, albums, and tracks** — ranked by play count or library presence
- **Genre distribution** — visual breakdown of genres across your library
- **Recent additions** — latest tracks and albums added to your library
- **Listening timeline** — activity over time when scrobbling is configured
`
        },
    ]
});
