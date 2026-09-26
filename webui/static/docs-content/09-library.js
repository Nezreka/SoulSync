registerDocsSection({
    id: 'library',
    title: 'Music Library',
    icon: '📚',
    pages: [
        {
            id: 'lib-standard',
            title: 'Standard View',
            lede: 'Browse your collection by artist, with rich filtering and service match badges.',
            body: `
The Library page shows every artist in your collection as cards with images, album and track counts, and **service badges** — Spotify, MusicBrainz, Deezer, Discogs, AudioDB, iTunes, Last.fm, Genius, Tidal, Qobuz — indicating which services have matched each artist.

![Library artist grid](lib-standard.jpg)

## Browsing

- **Search bar** — find artists by name
- **Alphabet navigation** (A–Z, #) — jump through large collections
- **Watchlist filter** — All, Watched, or Unwatched artists
- **Metadata source filter** — find artists unmatched to a specific service (e.g. "No Discogs") or matched to one (e.g. "Has Spotify")

Click any artist card to open their detail page: albums, EPs, and singles as cards with completion percentages. Filter by category, content type (live, compilations, featured), or status (owned, missing). **View on** buttons at the top link the artist on each matched external service.
`
        },
        {
            id: 'lib-enhanced',
            title: 'Enhanced Library Manager',
            lede: 'A pro-grade management view with inline editing, sortable tracks, and service matching.',
            body: `
Toggle **Enhanced** on any library artist's detail page to access the professional library management tool.

> [!NOTE]
> The Enhanced view is **admin-only** — non-admin profiles see the Standard view. It's also only offered for artists already in your library.

![Enhanced Library Manager](lib-enhanced.jpg)

- **Accordion layout** — albums as expandable rows showing full track tables
- **Inline editing** — click any track title, track number, or BPM to edit in place (Enter saves, Escape cancels)
- **Artist meta panel** — editable name, genres, label, style, mood, and summary
- **Sortable columns** — sort by title, duration, format, bitrate, BPM, disc, or track number
- **Play tracks** — queue button adds tracks to the media player
- **Delete** — opens the Smart Delete dialog with two choices: **Remove from Library** (database entry only, files on disk untouched) or **Delete File Too** (also deletes the audio file from disk — irreversible)
`
        },
        {
            id: 'lib-matching',
            title: 'Service Matching',
            lede: 'Link your artists, albums, and tracks to external services for richer metadata.',
            body: `
In the Enhanced view, each artist, album, and track shows **match status chips** — the service set differs by level: 12 on artists (adding Tidal, Qobuz and Amazon), 10 on albums, and 9 on tracks (Spotify, MusicBrainz, Deezer, JioSaavn, AudioDB, iTunes, Last.fm, Genius, Bandcamp — no Discogs, Tidal or Qobuz chip at track level).

- Click any chip to **manually search and link** the correct external ID when automatic matching gets it wrong
- Run per-service **enrichment** from the Enrich dropdown to pull in metadata from a specific source
- Matched services show as clickable badges linking to the entity on that service's website

Good matches are the foundation of everything downstream — discovery, similar artists, and metadata quality all depend on correct service links.
`
        },
        {
            id: 'lib-tags',
            title: 'Write Tags to File',
            lede: 'Sync your database metadata into the actual audio file tags.',
            body: `
::: steps
1. Open a track's **⋯** menu and choose **Write tags to file** — or an album's **⋯** menu and choose **Write all tags to files** — or select tracks and use the bulk bar's **Write tags**.
2. A **tag preview modal** shows a diff table: current file tags vs. database values.
3. Optionally enable **Embed cover art** and **Sync to server**.
4. Click **Write Tags** to apply the changes to the file.
:::

![Tag preview modal](lib-tags.jpg)

Supports MP3, FLAC, OGG, and M4A via Mutagen. After writing, optional server sync pushes metadata to Plex (per-track update), Jellyfin (library scan), or Navidrome (auto-detects changes).
`
        },
        {
            id: 'lib-bulk',
            title: 'Bulk Operations',
            lede: 'Select tracks across albums and act on all of them at once.',
            body: `
Select tracks across multiple albums using the checkboxes. The bulk bar appears showing the selection count with actions:

- **Edit Selected** — apply the same field changes to all selected tracks
- **Write Tags** — batch write tags to all selected tracks with live progress
- **Clear Selection** — deselect all

![Bulk operations bar](lib-bulk.jpg)
`
        },
        {
            id: 'lib-missing',
            title: 'Download Missing Tracks',
            lede: 'Fill gaps in albums without leaving the library.',
            body: `
From any album card showing missing tracks, click **Download Missing** to open a modal listing every track not in your library. Select tracks, choose a download source, and start the download — progress is tracked per track with status indicators.

**Multi-disc albums** are handled automatically: tracks organize into \`Disc N/\` subfolders within the album directory, preventing track number collisions (Disc 1 Track 1 vs Disc 2 Track 1). Disc structure is detected from Spotify or iTunes metadata.
`
        },
        {
            id: 'lib-smart-delete',
            title: 'Smart Delete',
            lede: 'Remove tracks from the database only, or from disk as well — with a deliberate choice each time.',
            body: `
Right-click or use the delete action on any track to open the Smart Delete dialog — it offers exactly two options:

- **Remove from Library** — removes the track from SoulSync's database only. The audio file on disk is untouched. Use this to clean up the database without losing files.
- **Delete File Too** — removes the database entry AND deletes the audio file from disk. Irreversible.

There is no blacklist option here — blacklisting lives in **Source Info**, where the real download-source data is.
`
        },
        {
            id: 'lib-redownload',
            title: 'Track Redownload',
            lede: 'Replace a track with a better copy from a different source or quality.',
            body: `
A 3-step wizard guides you through redownloading a specific track:

::: steps
1. **Choose metadata source** — confirm the correct track identity (Spotify, iTunes, or Deezer match).
2. **Choose download source** — search across all configured download sources (Soulseek, Tidal, Qobuz, YouTube, HiFi, Deezer) and pick a specific result.
3. **Download & replace** — the new file replaces the existing one with updated metadata and tags.
:::
`
        },
        {
            id: 'lib-issues',
            title: 'Library Issues',
            lede: 'Problems people reported by hand — the manual issue tracker.',
            body: `
The Issues page is the **manual issue tracker**: nothing here is ever written by a scan — every row is a problem reported by a person (profiles can file them from library pages and the player). Automated scan findings live in **Library Maintenance** on the Tools page, a deliberately separate surface.

Issues are grouped by category — wrong track, wrong metadata, wrong cover, wrong artist, wrong album, duplicate tracks, missing tracks, audio quality, incomplete album, other — each with a status (**open**, **in progress**, **resolved**, **dismissed**) and a priority (**low**, **normal**, **high**). Filter by status and category to work through them, and update issues individually or in bulk.

> [!NOTE]
> The **"witness me"** type-the-phrase confirmation doesn't live here — it gates bulk orphan deletes in **Library Maintenance**: a delete targeting more than 20 orphan findings where at least one finding carries the backend's \`mass_orphan\` flag. But the current backend never sets that flag — when the orphan-file scan trips its mass-orphan guard (over half the scanned files look like orphans, which usually means a DB↔filesystem path mismatch rather than real orphans), it refuses to create any findings at all, so the confirmation can't trigger today.
`
        },
    ]
});
