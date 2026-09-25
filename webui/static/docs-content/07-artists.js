registerDocsSection({
    id: 'artists',
    title: 'Artists & Watchlist',
    icon: '🎤',
    pages: [
        {
            id: 'art-search',
            title: 'Artist Search',
            lede: 'Find any artist and jump straight into their discography.',
            body: `
Search for any artist by name. Results show artist cards with images and genres, sourced from Spotify (or iTunes as fallback). Click any card to open the artist detail view.

![Artist search results](art-search.jpg)
`
        },
        {
            id: 'art-detail',
            title: 'Artist Detail & Discography',
            lede: 'The full discography with completion percentages, filters, and per-release downloads.',
            body: `
The artist detail page shows a full discography organized by category:

- **Albums**, **Singles & EPs**, **Compilations**, and **Appearances**
- Each release card shows cover art, title, year, track count, and a **completion percentage** — how many tracks you already own
- Filter by category, content type (live / compilations / featured), or status (owned / missing)
- Click any release to open the download modal with track selection

At the top, **View on** buttons link to the artist on each matched external service (Spotify, Apple Music, MusicBrainz, Deezer, AudioDB, Last.fm, Genius, Tidal, Qobuz). **Service badges** on artist cards also indicate which services have matched this artist.

**Similar Artists** appear as clickable bubbles below the discography for further exploration and discovery.

![Artist detail page](art-detail.jpg)
`
        },
        {
            id: 'art-watchlist',
            title: 'Watchlist',
            lede: 'Follow artists and let SoulSync catch every new release for you.',
            body: `
The watchlist tracks artists you want to follow for new releases. When SoulSync scans your watchlist, it checks each artist's discography and adds any new tracks to your **wishlist** for downloading.

- Add artists from search results, the Discover page hero, or library artist cards
- Remove artists individually or in bulk
- Filter your library by Watched / Unwatched status
- Use **Watch All** to add all recommended artists at once
- **Watch All Unwatched** — bulk-add every library artist that isn't already on your watchlist

![Watchlist page](art-watchlist.jpg)
`
        },
        {
            id: 'art-scanning',
            title: 'New Release Scanning',
            lede: 'Automatic daily scans keep your watchlist fresh — or trigger one manually.',
            body: `
Click **Scan for New Releases** or let the system automation handle it (runs every 24 hours). The scan shows a live activity panel with:

- Current artist being scanned (with image)
- Current album being processed
- Recent wishlist additions feed
- Stats: artists scanned, new tracks found, tracks added to wishlist

![New release scan panel](art-scan.jpg)

> [!TIP]
> Scanning respects your per-artist release-type settings — if you've excluded live albums for an artist, they won't land in your wishlist.
`
        },
        {
            id: 'art-wishlist',
            title: 'Wishlist',
            lede: 'The queue of tracks waiting to be downloaded — fed by scans, syncs, and your own picks.',
            body: `
The **wishlist** is the queue of tracks waiting to be downloaded. Tracks are added from multiple sources:

- **Watchlist scans** — new releases from watched artists are automatically added
- **Playlist sync** — tracks from mirrored playlists that aren't in your library
- **Manual** — individual track or album downloads go through the wishlist

## Auto-processing

The system automation runs every 30 minutes, picking up wishlist items and attempting to download them from your configured source. Processing alternates between **album** and **singles** cycles — one run processes albums, the next processes singles. If one category is empty, it automatically switches to the other. Failed items are retried with increasing backoff.

## Manual processing

Use the **Process Wishlist** automation action to trigger processing on demand, with options for all items, albums only, or singles only.

## Cleanup

The **Cleanup Wishlist** action removes duplicates (the same track added multiple times) and items you already own in your library.

> [!NOTE]
> Each wishlist item tracks its source (watchlist scan, playlist sync, manual), retry attempts, last error message, and status (pending, downloading, failed, complete).

![Wishlist queue](art-wishlist.jpg)
`
        },
        {
            id: 'art-settings',
            title: 'Watchlist Settings',
            lede: 'Control exactly which release types SoulSync grabs for each artist — or set one global rule.',
            body: `
## Per-artist settings

Click the config icon on any watched artist to customize which release types to include:

- Albums
- EPs
- Singles
- Live versions
- Remixes
- Acoustic versions
- Compilations

## Global settings

Override all per-artist settings at once. Enable **Global Override**, select which types to include, and every watchlist scan will follow the global config instead of individual artist preferences.

> [!TIP]
> Global override is handy if you generally only want studio albums — turn it on, select Albums only, and your wishlist stays focused.
`
        },
    ]
});
