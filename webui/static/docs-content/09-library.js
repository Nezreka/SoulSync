registerDocsSection({
    id: 'library',
    needs: 'music.library',
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

- **Search bar** — find artists by name. When it matches songs, the best few show above the results, with a link into all of them
- **Artists / Albums / Songs** — three views of the same library. **Songs** lists every song you can see, searchable by title or artist (the match is highlighted), sorted A–Z, by artist, by album, newest first or recently added
  - **Click a song** to play it and the songs after it; the playing one shows moving bars. **Play all** / **Shuffle** queue up to 500 of what you're looking at
  - **♥** saves a song to Liked Songs; the playlist button files it; **⋯** plays it next, adds it to the queue, opens its album or artist
  - **Filters**: **Duplicates** (songs you have more than one copy of), **Lossless**, and **Could be better** (below their quality profile, from Library Maintenance)
  - **N copies** opens the copies side by side, best file first (lossless, then bitrate), each playable; admins can **Keep this one** and remove the rest, from the library or from disk
  - **Tick songs** (or shift-click a range) to play, queue, add to a playlist, like or (admins) delete them together
  - On a big library a broad search or filter counts up to 1,000 songs and says "1,000+"
- **Alphabet navigation** (A–Z, #) — jump through large collections
- **Watchlist filter** — All, Watched, or Unwatched artists
- **Metadata source filter** — find artists unmatched to a specific service (e.g. "No Discogs") or matched to one (e.g. "Has Spotify")
- **Reviewed filter** — All, Not reviewed, or Reviewed. An artist is reviewed when every album of theirs with files is locked
- **Collection**: each artist card and table row shows owned / expected tracks with a bar, green when complete, amber when tracks are missing. For a Watchlist artist, expected follows their collection settings, so a release they leave out never counts as missing. For anyone else it's the albums you have: every track of each release you hold something of, plus anything you asked for, so one track of a 14 track album reads 1 / 14. An artist whose release sizes were never looked up says **Not checked** instead of complete. Click **N missing** to see those tracks: in Wanted for a Watchlist artist, on the artist's page for anyone else
- **Leftover rows**: when a retag or re-identify moves a file onto another row of its album, the old row is left with no file at the same position. A library scan and an artist's **Refresh & Scan** fold it into the row that has the file (its plays come along), so the album counts right again. A locked album keeps them until you act, and you can remove one yourself from its **⋯** menu with **Remove This Row**. A row alone at its position is a missing track and stays
- **Move a track here**: a missing track's **⋯** menu (admins) finds a track you already have somewhere else, a stray single say, and moves it into the slot. Search your library (it starts with the missing title), pick the track by its album, path and quality, and the preview shows where the file goes and which tags change. **Move here** moves the file, it never copies it: the old row goes (its plays, history and playlist spots come along), its album leaves the library when nothing else is on it, the file is retagged for this album and filed into its folder by your naming template (left where it is when file organization is off). A locked album, on either side, has to be unlocked first. **Reassign to another artist** on an album also moves now: the originals go once the re-import lands, with their old row and an album left empty
- **Collection filters** live in the Filters menu: complete or incomplete, missing count and completion ranges, and artists only credited on other people's tracks. Sort by completion, missing tracks, could be better or incomplete albums, and turn any sort around with the arrow
- **Overview** opens a summary above the list: artists, tracks owned, complete and incomplete collections, tracks missing, could be better, and Watchlist artists
- **Card size** slider in the toolbar, separate for artist and album cards (and posters on the video library), with a reset. The list also remembers its view, sort, filters and scroll when you come back from an artist
- **Select artists** with the checkbox on a card or row (shift-click for a range, or select the page or everything that matches). The bar can add or remove them from the Watchlist, run Automatic Search, refresh discographies, lock or unlock, and change the quality profile. It runs in the background, shows done, skipped and failed, and asks first before undoing anything

#### Reviewed and locked

The **padlock** on an album or an artist is for when you have gone through it and it is right. Locking it does two things: it counts as reviewed, and nothing automated changes it after that.

- **What is left alone** is a set of switches, all on by default: tags in the files, artwork, library details, file names and folders, the release match, and quality upgrades. Switch one off to keep that part handled for you
- **What a lock does not stop**: missing tracks still download, and a missing or damaged file is still reported on the Tools page (marked **Locked**). **Fix all** passes a locked finding over; the button on the finding itself still works
- **Your own hands are never blocked** — editing, retagging or reorganizing a locked album from its page works as before
- **Kept away** lists the jobs the lock turned back, so you can see it working
- **Changed since your review** shows when a file of a locked album was replaced, renamed, added or removed outside SoulSync. **Mark reviewed again** clears it
- **Lock and next** (on the album page) locks the album and opens the artist's next album that is not reviewed. On the artist page the padlock on an album row locks it and you stay on the page, so you can go down the list. The artist's toolbar shows the count, e.g. "14 of 20 reviewed"
- **An artist's lock** covers the artist (photo, details, folder, match). Albums are locked one by one, so a new release still gets tagged; locking an artist offers **And all current albums**
- Locking needs the admin profile. Everyone sees the padlock on what is locked

Click any artist card to open their detail page: albums, EPs, and singles as cards with completion percentages. Live records, compilations, soundtracks, remixes, demos and spoken word each get a section of their own under Albums, EPs and Singles, so the studio albums are not buried: the **Show** pills are those sections, each with its count, and a kind the artist has nothing of is not listed. A release is placed by its MusicBrainz release type when that is known (SoulSync looks it up in the background for every album it has matched), and by its title until then. Filter by section, featured releases, or status (owned, missing). **View on** buttons at the top link the artist on each matched external service.
`
        },
        {
            id: 'lib-enhanced',
            needs: 'admin',
            title: 'Library Management',
            lede: 'Manage catalogue metadata and physical file versions in the Library.',
            body: `
Management actions are part of the Library page; there is no separate Enhanced toggle.

![Library v2 management view with the track table](lib-enhanced.jpg)

Catalogue tracks and their physical files are managed separately. **File versions** shows the copies held for a track, and **Recording duplicates** reviews linked single/album recordings. Removal actions preview the selected catalogue entries and file paths before applying changes. Available actions depend on your profile permissions and selected library.

**Preview Rename / Organize** shows where each file would go under your naming template before anything moves. You don't have to open it to find out: on the artist page an album row shows **N to rename** when some of its files are not where the template puts them, the artist's toolbar shows the total, and the album's track table marks those files in the **File path** column. Click the count to open the preview. The check runs after the page loads and only while file organization is on and the template is valid.

**Folder and tag name** (Artist Settings) shows what new downloads of an artist are filed and tagged as. A download goes under the name your library uses for that artist, found by its Deezer, Spotify or MusicBrainz id, even when the download source spells it another way: if your library calls Machine Gun Kelly **mgk** and Deezer still says Machine Gun Kelly, new releases land in \`mgk/\` and are tagged mgk. A name in another script counts too: when iTunes says **Δέσποινα Βανδή** and your library has **Despina Vandi** (her usual Latin spelling, or one of the MusicBrainz aliases SoulSync stores), the download joins Despina Vandi. Playlist sync and the "already owned" check look an artist up under those names as well. A featured artist keeps its own name. Type a name there to set it yourself (it is also the name SoulSync shows, the same one Edit Metadata sets). Files you already have stay where they are: **Preview rename** and **Preview retag** bring them in line. A name a different artist in your library already uses is refused, so two different artists never share a folder.

On an album's track list, a small **flame** next to a title marks the album's most played tracks on Last.fm: up to three that clearly stand out from the rest. It needs the Last.fm worker to have enriched the album's tracks, and an album where the plays are close together, or where Last.fm knows too few of the tracks, shows none.
`
        },
        {
            id: 'lib-matching',
            needs: 'admin',
            title: 'Service Matching',
            lede: 'Link your artists, albums, and tracks to external services for richer metadata.',
            body: `
The Library exposes provider links and matching actions on catalogue entities. The available services depend on the entity and its stored provider IDs.

![Provider badges on a library artist: MusicBrainz is linked, while Deezer, iTunes, AudioDB and Discogs have no stored ID yet](lib-matching.jpg)

- Click any chip to **manually search and link** the correct external ID when automatic matching gets it wrong
- **Paste a link or an ID** instead of a name when several artists share one (eleven called "1905"): open the right one on that service, copy its address, and paste it. Works for Spotify, MusicBrainz, Deezer, iTunes/Apple Music, Discogs, Tidal, Qobuz, Amazon, AudioDB, JioSaavn, Last.fm and Bandcamp links, and Genius IDs. A paste that doesn't resolve falls back to a normal search
- Fixing an artist's match also fixes it on **your watchlist**, so new releases are scanned for the right artist. Only your own watchlist changes, and never onto an ID another watched artist already uses
- **Other versions…** in an album's **⋯** menu is the edition picker (like Picard's): it lists the album's releases from its MusicBrainz release group and the same-titled releases on your primary source, each with its track count, date, country, format and label, the current one marked. A search box finds one that is not listed, kept to that artist. Pick one to see what changes (track count, how your files line up) and confirm: the album then follows that release, the same as a manual match on its chip. Your files stay as they are, so run **Preview Retag** afterwards to write it into their tags
- Run per-service **enrichment** from the Enrich dropdown to pull in metadata from a specific source
- Matched services show as clickable badges linking to the entity on that service's website

Good matches are the foundation of everything downstream — discovery, similar artists, and metadata quality all depend on correct service links.
`
        },
        {
            id: 'lib-tags',
            needs: 'admin',
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
            needs: 'admin',
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
            needs: 'music.get=download',
            title: 'Download Missing Tracks',
            lede: 'Fill gaps in albums without leaving the library.',
            body: `
From any album card showing missing tracks, click **Download Missing** to open a modal listing every track not in your library. Select tracks, choose a download source, and start the download — progress is tracked per track with status indicators.

![Album with missing tracks and the Download Missing action](lib-missing.jpg)

**Multi-disc albums** are handled automatically: tracks organize into \`Disc N/\` subfolders within the album directory, preventing track number collisions (Disc 1 Track 1 vs Disc 2 Track 1). Disc structure is detected from Spotify or iTunes metadata.
`
        },
        {
            id: 'lib-smart-delete',
            needs: 'admin',
            title: 'Remove Entries and Files',
            lede: 'Remove tracks from the database only, or from disk as well — with a deliberate choice each time.',
            body: `
The Library previews the affected entries and file paths before removal:

![Remove dialog offering database-only removal or deleting the files too](lib-delete.jpg)

- **Remove from library database only** — removes the selected library records and keeps the physical files on disk.
- **Permanently delete files** — removes the corresponding records and disk files after explicit confirmation. Paths outside configured Music Library Paths, or on unavailable storage, are blocked.

File-removal operations are recorded in the deletion journal. Permanent deletion cannot be undone by reopening a finding. Maintenance fixes that move files into the deleted-files quarantine are recorded as quarantine operations instead; the confirmation for that action describes the move.
`
        },
        {
            id: 'lib-redownload',
            needs: 'admin',
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
            needs: 'everyone',
            title: 'Library Issues',
            lede: 'Problems people reported by hand — the manual issue tracker.',
            body: `
The Issues page is the **manual issue tracker**: nothing here is ever written by a scan — every row is a problem reported by a person (profiles can file them from library pages and the player). Automated scan findings live in **Library Maintenance** on the Tools page, a deliberately separate surface.

![Library Issues page with reported problems and status counters](lib-issues.jpg)

Issues are grouped by category — wrong track, wrong metadata, wrong cover, wrong artist, wrong album, duplicate tracks, missing tracks, audio quality, incomplete album, other — each with a status (**open**, **in progress**, **resolved**, **dismissed**) and a priority (**low**, **normal**, **high**). Filter by status and category to work through them, and update issues individually or in bulk.

> [!NOTE]
> The **"witness me"** type-the-phrase confirmation doesn't live here — it gates bulk orphan deletes in **Library Maintenance**: a delete targeting more than 20 orphan findings where at least one finding carries the backend's \`mass_orphan\` flag. But the current backend never sets that flag — when the orphan-file scan trips its mass-orphan guard (over half the scanned files look like orphans, which usually means a DB↔filesystem path mismatch rather than real orphans), it refuses to create any findings at all, so the confirmation can't trigger today.
`
        },
    ]
});
