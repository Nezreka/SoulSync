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
> Each wishlist item tracks its source (watchlist scan, playlist sync, manual), retry attempts, and the last error message. There is no status column — a wishlist row is deleted once its download completes.

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

registerDocsSection({
    id: 'label-detail',
    title: 'Record Labels',
    icon: '🏷️',
    pages: [
        {
            id: 'label-page',
            title: 'The Label Page',
            lede: 'Every record label gets a catalog page: releases, ownership status, and a follow button.',
            body: `
## Getting there

- **Search** — label results render as quiet tiles (name, "Record label", area) linking to \`/label-detail/<id>?name=<name>\`. Labels only appear when the query also has other results — a labels-only match never shows on its own.
- **Watchlist → Labels tab** — clicking a followed label's card opens its page (the Watchlist nav entry stays lit while you're viewing a label — it's the label's natural home).

The **← Back** button doesn't use browser history: it returns to wherever you came from (recorded when you navigated in) and falls back to Search.

## Layout (top to bottom)

1. **← Back** button.
2. **Label hero** — a 🏷️ glyph (labels have no artwork, so there's no cover), a "Record Label" eyebrow, the label name, and a meta line **"N releases · M artists"**. The meta line stays empty until the first catalog page lands, so you never see "0 releases · 0 artists" as an answer.
3. **Follow button** — **Add to Watchlist** → **Watching...** (styled exactly like an artist's watchlist button). Hidden until the first page of the catalog loads.
4. **Monitor segmented control** — only visible while you follow the label: **New releases** vs **Full backlog**.
5. **Toolbar** — filter pills **All, Missing, Owned** (in that deliberate order, with live counts), plus a sort dropdown: **Newest first** / **Oldest first** / **Artist A–Z**.
6. **Release grid** — the catalog, loading 60 releases per page with infinite scroll (the next page starts loading 400px before you reach the bottom; "Loading more…" shows while it fetches).
7. States: **"Loading label catalog…"** (first page), **"Could not load this label's catalog."** (first-page failure — later failures leave what loaded on screen), **"No releases to show."** / **"No owned releases in this label."** / **"No missing releases in this label."** (names the active filter so you know the catalog isn't empty).

> [!NOTE]
> The label id in the URL is a MusicBrainz label id; the \`?name=\` param carries the display name so refreshes and direct loads render a heading before the catalog resolves.
`
        },
        {
            id: 'label-catalog',
            title: 'Release Catalog',
            lede: 'The label\'s full release list with ownership badges, filters, and one-click downloads.',
            body: `
The catalog is a flat grid of release cards in server order (newest first), fetched 60 at a time from the label's MusicBrainz catalog. There is no grouping — filtering and sorting happen client-side, instantly, without refetching.

## Filters and sorting

| Control | Behavior |
|---------|----------|
| **All** | Every loaded release |
| **Missing** | Releases you don't own — this page is an acquisition surface, so Missing sits right next to All |
| **Owned** | Releases already in your library |
| **Newest first** | Server order |
| **Oldest first** | Reversed server order |
| **Artist A–Z** | By artist name, with year as a descending tiebreak so each artist's newest release still leads |

Pill counts are computed over **everything loaded so far**, not just the visible rows — switching filters never changes the counts, and counts shift as ownership checks resolve.

## Ownership badges

Each card carries **✓ Owned** (green) or **Missing**, resolved in batches against your library. There are three states, not two: an unchecked release shows **no badge at all**, so you never see a flash of wrong "Missing" answers while checks are in flight. Failed checks stay unchecked rather than mislabeling.

## Cover art

Covers resolve lazily through an iTunes lookup as you scroll (one shared observer for the whole grid, max 2 in flight, misses remembered so dead lookups aren't retried). Art only paints after a loader proves the URL actually loads — no broken-image glyphs.

## Card actions

- **Click the card** (or Enter/Space) — opens the release in the shared **download modal** with wishlist support. Because MusicBrainz has no usable images, the release is first re-resolved on a reliable source (exact artist + title, then title alone, then first result); if images are still unusable, an absolute CDN URL is fetched for the wishlist entry. If the modal isn't available it degrades to a search handoff. Hard failures toast "Could not open this release".
- **👤 button** — **Go to artist**: jumps to the artist's detail page (only rendered when the release has an artist id; doesn't trigger the download modal).

Cards show cover art, album title, and "artist · year" (year omitted when unknown). There are no play, preview, or download-now buttons on label cards.

> [!TIP]
> The toolbar only appears once the first catalog page lands with no error — if you see the hero but no pills, the catalog is still loading.
`
        },
        {
            id: 'label-follow',
            title: 'Following Labels',
            lede: 'Follow a label to monitor its new releases from the Watchlist.',
            body: `
## Following

Click **Add to Watchlist** in the label hero. The button flips to **Watching...** and the **Monitor** segmented control appears:

- **New releases** — only new drops get picked up
- **Full backlog** — the label's entire back catalog is in scope too

The backlog choice saves immediately (optimistic, reverted if the server refuses) — and it only means something for a label you follow, which is why the control hides when you unfollow. Unfollowing is instant; failures toast "Could not update watchlist" and leave the button alone.

## The Watchlist Labels tab

Followed labels live in the **Watchlist → Labels tab**. Each card shows the label name, a **"Scanned …"** line from its last scan timestamp, and a **Full backlog** badge when backlog monitoring is on. Per-card controls:

- **📚 / 🆕 toggle** — flip between "Monitoring full backlog — click for new-releases-only" and the reverse
- **Unfollow** — guarded by a confirm dialog ("Stop monitoring <name> for new releases?")

Empty state: "No labels followed yet — Search a record label, open it, and hit Follow to monitor its new releases here."

> [!NOTE]
> There is no feature flag or prerequisite for viewing or following a label — it works for every profile, scoped like the rest of the watchlist. Big labels can be slow: the catalog fetch has no timeout because a large label's MusicBrainz walk genuinely takes a while.
`
        },
    ]
});
