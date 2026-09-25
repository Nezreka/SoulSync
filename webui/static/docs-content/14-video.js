// Video: Overview — what the video side is, switching sides, servers, sources, access.
registerDocsSection({
    id: 'video-overview',
    title: 'Video: Overview',
    icon: '🎬',
    pages: [
        {
            id: 'vid-what',
            title: 'What the Video Side Is',
            lede: 'A self-hosted movies & TV manager built into SoulSync — Sonarr/Radarr-class downloading plus overlay and collection studios.',
            body: `
SoulSync's **Video** side is a self-hosted movies & TV manager that lives inside the same app as the music side but runs as an **isolated application** — its own database, its own pages, its own API. Think of it as a Sonarr + Radarr + overlay/collection manager built into SoulSync.

It connects to **Plex** or **Jellyfin/Emby** to read your movie and TV libraries, enriches every title from **TMDB**, **TVDB**, **OMDb**, **fanart.tv**, **Trakt**, **TVMaze** and **OpenSubtitles**, and can search, grab, organize, and upgrade downloads to fill the gaps in your collection.

::: cards
### 🎬 Movies & TV
Browse your Plex/Jellyfin library, enriched with posters, backdrops, cast, ratings, awards, and format badges (HDR, Dolby Vision, Atmos, channel layout).
### ⬇️ Arr-Class Downloading
Quality profiles, custom formats, import lists, blocklist, upgrade-until-cutoff, and a live download queue with monitored grabs.
### 👀 Watchlist & Wishlist
Follow people and studios, auto-add their upcoming titles, and track everything wanted or below cutoff in one place.
### 🖼️ Overlay & Collection Studios
A Kometa-style overlay template editor and a collection builder that write badges and collections straight back to Plex/Jellyfin.
### 🔧 Library Maintenance
Repair jobs that scan for problems — missing art, ghosts, orphans, un-monitored gaps — and fix them, with rich findings.
### 📺 YouTube Channels
Follow YouTube channels and playlists like shows and pull new uploads into the video download pipeline.
:::

> [!NOTE]
> The video side keeps its own database (\`video_library.db\`) completely separate from the music library. Nothing you do on the video side touches your music, and vice versa.
`
        },
        {
            id: 'vid-switch',
            title: 'Switching Sides',
            lede: 'Flip between the Music and Video apps with the side switcher — each side is its own world.',
            body: `
Use the **side switcher** in the sidebar (the Audio ↔ Video toggle) to flip between the Music and Video apps. Each side has its own navigation, its own pages, and its own settings.

A handful of pages are **shared** across both sides — **Chat**, **Issues**, and this **Help & Docs** page — so you land on the same page no matter which side you were on. Everything else is side-specific: the video sidebar shows Dashboard, Search, Discover, Library, Watchlist, Wishlist, Downloads, Requests, Calendar, Automations, Tools, Import, and Settings for the video side.

> [!TIP]
> Your last-used side is remembered per profile, so you'll land right back where you left off next time you open SoulSync.
`
        },
        {
            id: 'vid-server',
            title: 'Connecting a Media Server',
            lede: 'Point the video side at Plex or Jellyfin/Emby, pick your libraries, and scan them in.',
            body: `
The video side reads your libraries from **Plex** or **Jellyfin/Emby**. Configure the connection under **Video → Settings**, pick which libraries to include, then run a scan to import them.

SoulSync stores a lightweight copy of every movie/show/episode row and enriches it in the background — it never modifies your server's files during a scan.

| Server | What SoulSync reads | Auth |
|--------|--------------------|------|
| **Plex** | Movie & TV libraries, watch state, collections, incremental delta via \`updatedAt\` | URL + Token |
| **Jellyfin / Emby** | Movie & TV libraries, watch state, BoxSets, incremental delta via \`MinDateLastSaved\` | URL + API Key (+ user for watch state) |

## Scan types

- **Incremental** — only re-reads titles modified since the last full scan, so routine refreshes are fast.
- **Full / Deep** — re-reads everything. Use it after a big library change or if something looks out of sync.

> [!TIP]
> Run an incremental scan on a schedule (see [Video: Automations](#video-automations)) and save full scans for when you've restructured libraries or moved files around.
`
        },
        {
            id: 'vid-sources',
            title: 'Download Sources',
            lede: 'Indexers, download clients, and library paths — what the video side shares with music and what it keeps to itself.',
            body: `
Video downloads flow through your configured indexers and clients. Grabs are ranked by your **quality profile** and **custom formats**, then handed to the client, monitored to completion, organized into your library folder, and (optionally) your server is told to scan.

## What's shared with the music side

Some connection settings are genuinely **shared** because they're one physical resource:

- **Prowlarr indexers** — configured once, surfaced on the shared Indexers tab
- **Torrent / usenet download clients** — the same client instances serve both sides
- **slskd** — the shared slskd block covers both music and video

These live in the music-side config and are deliberately surfaced to the video side rather than duplicated.

## What's video-only

The video side keeps its own library paths, migrated from the legacy single video transfer path on first read:

- \`movies_path\` — where finished movie downloads are organized
- \`tv_path\` — where finished TV downloads are organized
- \`youtube_path\` — where YouTube channel downloads land

The engine routes each finished download to the library path matching its type. See [Video: Downloads](#video-downloads) for the full pipeline.
`
        },
        {
            id: 'vid-access',
            title: 'Per-Profile Side Access',
            lede: 'Control which profiles can see the video side at all — enforced server-side, not just hidden in the UI.',
            body: `
With multi-profile enabled, each profile can be granted access to **Music**, **Video**, or **both**. A music-only profile can't see or reach the video side at all — the whole Video navigation is hidden and the video API rejects its requests server-side. Admins always have both sides.

> [!DANGER]
> Side access is enforced on the server, not just hidden in the UI — a music-only profile that tries to hit a \`/api/video/*\` URL directly gets a \`403\`.

See [Video: Settings](#video-settings) and [Multi-Profile](#profiles) for how to configure side access per profile.
`
        },
    ]
});

// Video: Dashboard — overview & health, continue watching, recent & activity.
registerDocsSection({
    id: 'video-dashboard',
    title: 'Video: Dashboard',
    icon: '🎬',
    pages: [
        {
            id: 'vdash-overview',
            title: 'Overview & Health',
            lede: 'Your at-a-glance home for the video side: library counts, download activity, enrichment coverage, and service health.',
            body: `
The Video **Dashboard** is your at-a-glance home for the video side:

- **Library counts** — movies, shows, and episodes tracked in \`video_library.db\`
- **Download activity** — what's grabbing, importing, or upgrading right now
- **Enrichment coverage** — how much of your library has full metadata and art
- **Health strip** — whether your media server, indexers, and download clients are reachable

If something in your stack goes down — Prowlarr stops responding, a download client drops off, the media server is unreachable — the health strip flags it here first, before you go hunting through settings.
`
        },
        {
            id: 'vdash-continue',
            title: 'Continue Watching',
            lede: 'Pick up right where you left off — partially-watched movies and next-up episodes in one rail.',
            body: `
A **Continue Watching** rail surfaces partially-watched movies and the next unwatched episode of shows in progress, drawn from the watch state SoulSync ingests from your server.

Each card jumps straight to the title's detail page with a **Next Up** call-to-action, so resuming a series is one click. Watch state syncs from Plex or Jellyfin/Emby during library scans — mark something watched in your server app and SoulSync picks it up on the next pass.

See [Watch State & History](#vdet-watch) for how watch state works per title.
`
        },
        {
            id: 'vdash-activity',
            title: 'Recent & Activity',
            lede: 'Recently added titles and a running feed of what the video side has been doing.',
            body: `
**Recently added** titles show what's landed in your library, and a running **activity feed** (scans, grabs, imports, upgrades) keeps you current on what the video side has been doing — without opening every page.

Use it as a quick sanity check: if an automation grabbed something overnight, you'll see it here first.
`
        },
    ]
});

// Video: Search & Studios — searching, trending, studios.
registerDocsSection({
    id: 'video-search',
    title: 'Video: Search & Studios',
    icon: '🎬',
    pages: [
        {
            id: 'vsearch-search',
            title: 'Searching',
            lede: 'One search bar across movies, TV shows, people, and studios — with ownership status on every result.',
            body: `
Search across **movies**, **TV shows**, **people**, and **studios** from one bar. Results are source-agnostic and show whether you already own a title, so you can:

- Jump to a title's [detail page](#vdet-layout)
- Add something to your [wishlist](#vwish-wanted)
- Open a person or studio to browse their catalog

Search draws on TMDB and friends for metadata, matched against your local library so owned titles are flagged inline — no more adding something you already have.
`
        },
        {
            id: 'vsearch-trending',
            title: 'Trending',
            lede: "What's popular right now — a fast way to spot new releases worth adding.",
            body: `
A **Trending** feed highlights what's popular right now across movies and TV, giving you a fast way to spot new releases worth adding to your library or wishlist.

Trending is metadata-driven (what the world is watching), not library-driven — it's for discovery, not for auditing what you own. Pair it with [Discover](#video-discover) for personalized recommendations.
`
        },
        {
            id: 'vsearch-studios',
            title: 'Studios',
            lede: 'Browse production company catalogs, follow studios, and auto-catch their new releases.',
            body: `
Search for a production company (studio) and open its **Studio detail** page to browse its full filmography, paged from TMDB.

**Studio presets** give quick access to well-known studios and studio families, and you can **follow** a studio to have its new releases auto-added via the [Watchlist](#vwatch-follow).

Studios are also a discovery vector: if you love everything A24 puts out, following the studio means you never miss their next release.
`
        },
    ]
});

// Video: Discover — cinematic hero, For You & taste, More Like This & gaps, genres, preferences.
registerDocsSection({
    id: 'video-discover',
    title: 'Video: Discover',
    icon: '🎬',
    pages: [
        {
            id: 'vdisc-hero',
            title: 'Cinematic Hero',
            lede: 'A streaming-style home screen: a rotating billboard of standout titles with trailers.',
            body: `
Discover opens on a full-bleed **billboard** that rotates through standout titles with logos, backdrops, and a one-click **trailer**. It's built to feel like a streaming home screen rather than a database.

Below the hero, content is organized into rails — personalized picks, recommendations, and browsable grids — so you can lean back and browse or drill straight into something specific.
`
        },
        {
            id: 'vdisc-foryou',
            title: 'For You & Taste',
            lede: 'Personalized rows built from your library\'s taste profile — the sharper your library, the sharper the picks.',
            body: `
**For You** rows are personalized from your library's **taste** profile — the genres, people, and studios you already own most. The more SoulSync knows about your library, the sharper these get.

Taste is derived from what you actually own and watch, not from a questionnaire. A library heavy on sci-fi with a Denis Villeneuve streak will surface very different rows than a library full of 90s sitcoms.
`
        },
        {
            id: 'vdisc-more',
            title: 'More Like This & Gaps',
            lede: 'Recommendations from titles you own, plus missing entries in the franchises you\'ve started.',
            body: `
**More Like This** pulls recommendations from titles you own — similar tone, cast, crew, and genre — while **Gaps** highlights missing entries in collections and franchises you've partially collected.

Gaps are the completionist's shortcut: own 5 of 6 Mission: Impossible films and the missing one shows up here, ready to add to your [wishlist](#vwish-wanted) in one click.
`
        },
        {
            id: 'vdisc-browse',
            title: 'Genres & Browsing',
            lede: 'Genre tiles and an endless, honestly-paged grid for lean-back browsing.',
            body: `
Browse by **genre tiles** and live-filter an endless grid. The feed pages honestly (no duplicate padding) and keeps scrolling as far as you want to go.

Genre browsing respects your [preferences](#vdisc-prefs) — ignored titles stay out of the grid, and provider restrictions apply.
`
        },
        {
            id: 'vdisc-prefs',
            title: 'Preferences',
            lede: 'Tune Discover to your region, services, and taste — these persist per profile.',
            body: `
Tune Discover to your region and services:

- **Languages** — preferred languages for recommendations and metadata
- **Providers** — restrict recommendations to the streaming services you actually use
- **Ignored titles** — titles you never want to see again, anywhere in Discover

These preferences persist **per profile**, so everyone in the household gets their own tuned experience.
`
        },
    ]
});

// Video: Library — browsing & filters, manage panel, bulk operations.
registerDocsSection({
    id: 'video-library',
    title: 'Video: Library',
    icon: '🎬',
    pages: [
        {
            id: 'vlib-browse',
            title: 'Browsing & Filters',
            lede: 'Your owned movies and shows as a poster wall — filterable, sortable, and scroll-position aware.',
            body: `
The **Library** page is your owned movies and shows as a poster wall.

- **Filter** by resolution, genre, monitored state, and more
- **Sort** by title, added date, release year, or rating
- The page **remembers your scroll position** when you navigate away and come back

Filters compose — combine "4K + unwatched + sci-fi" to find exactly what you're in the mood for.
`
        },
        {
            id: 'vlib-manage',
            title: 'Manage Panel',
            lede: 'Per-title control: metadata, locks, quality profiles, monitoring, issues, and re-scans.',
            body: `
Open any title's **Manage** panel to:

- Edit metadata and **lock** fields against enrichment overwrites
- Change its **quality profile**
- Mark it **monitored / unmonitored**
- Report an **issue**
- Trigger a targeted **re-scan**

Locked fields are respected by every future enrichment pass — your corrections stick. See [Metadata Edit & Lock](#vdet-meta) for details.
`
        },
        {
            id: 'vlib-bulk',
            title: 'Bulk Operations',
            lede: 'Act on many titles at once — monitor, assign profiles, edit metadata, mark watched.',
            body: `
Select multiple titles to act on them at once:

- Bulk **monitor / unmonitor**
- Bulk **quality-profile** assignment
- Bulk **metadata** edits
- Mass **mark-watched**

Large bulk jobs run in the background so the UI stays responsive — kick off a thousand-title re-profile and keep browsing.

> [!WARNING]
> Library edits, deletes, re-matches, and bulk jobs are **admin-only**. Non-admin profiles can browse everything, but mutating the library requires an admin profile.
`
        },
    ]
});

// Video: Detail Pages — layout, watch state, metadata edit & lock, quality & series type, people & studios.
registerDocsSection({
    id: 'video-detail',
    title: 'Video: Detail Pages',
    icon: '🎬',
    pages: [
        {
            id: 'vdet-layout',
            title: 'Movie & Show Pages',
            lede: 'Every title gets a rich detail page: art, cast, ratings, format badges, and full season breakdowns.',
            body: `
Every title has a rich **detail page**:

- Hero art, overview, cast & crew
- Ratings, awards, and a post-credits / stinger flag
- Format badges (HDR, Dolby Vision, Atmos, channel layout)
- For shows: a full **season / episode** breakdown

Movies and shows can come from either Plex or Jellyfin/Emby and render the same way. From a detail page you can manage metadata, change quality profiles, toggle watch state, and kick off downloads.
`
        },
        {
            id: 'vdet-watch',
            title: 'Watch State & History',
            lede: 'Per-episode watch state from your server, a Next Up CTA, and a full per-title history.',
            body: `
SoulSync ingests per-episode and per-movie **watch state** from your server:

- Drives the **Continue Watching** rail on the [dashboard](#vdash-continue)
- Powers a **Next Up** call-to-action on show pages
- Lets you toggle **watched / unwatched** right on the page

A per-title **History** tab shows grabs, imports, and upgrades over time — useful for answering "when did this land, and from where?"
`
        },
        {
            id: 'vdet-meta',
            title: 'Metadata Edit & Lock',
            lede: 'Fix any field and lock it — enrichment will never overwrite your corrections.',
            body: `
Edit any metadata field and **lock** it so enrichment never overwrites your change. You can also **refresh art** to pull fresh posters/backdrops, or pick a specific poster from the available options.

> [!WARNING]
> A re-match changes the title's identity and can update its name and art — but it does **not** silently wipe your locked fields. Locked means locked.

Episodes are treated as facts: a scan that no longer sees an episode **demotes** it to missing rather than deleting the row, so your history and watch state survive server hiccups.
`
        },
        {
            id: 'vdet-quality',
            title: 'Per-Title Quality & Series Type',
            lede: 'Give individual titles their own quality profile and tell shows how their numbering works.',
            body: `
Assign a title its own **quality profile** — "this show is always 1080p, that movie is 4K" — overriding the global default. Set a show's **series type** so numbering and matching behave correctly:

| Series type | For |
|-------------|-----|
| **Standard** | Regular season/episode shows |
| **Daily** | Talk shows, soaps — date-based episodes |
| **Anime** | Absolute numbering and anime-specific matching |

Both the quality profile and series type ride the download pipeline for every future grab and upgrade of that title. See [Quality Profiles & Formats](#vdl-quality).
`
        },
        {
            id: 'vdet-people',
            title: 'People & Studios',
            lede: 'Actor and director filmographies, studio catalogs — and follow buttons that feed your watchlist.',
            body: `
**Person** pages show an actor or director's filmography with what you own highlighted. **Studio** pages show a production company's catalog.

From either, you can **follow** them straight into your [watchlist](#vwatch-follow) — so the next project from a director you love, or the next release from a studio you track, gets picked up automatically by release scanning.
`
        },
    ]
});

// Video: Watchlist — following people & studios, per-follow settings, release scanning.
registerDocsSection({
    id: 'video-watchlist',
    title: 'Video: Watchlist',
    icon: '🎬',
    pages: [
        {
            id: 'vwatch-follow',
            title: 'Following People & Studios',
            lede: 'Tell SoulSync who to keep an eye on — their new titles flow into your wishlist automatically.',
            body: `
The video **Watchlist** is how you tell SoulSync "keep an eye on this." Follow an **actor**, **director**, or **studio** and their upcoming and newly-released titles are automatically added to your [Wishlist](#vwish-wanted) so the download pipeline can go find them.

Follow from anywhere a follow button appears: [detail pages](#vdet-people), [studio pages](#vsearch-studios), or search results.
`
        },
        {
            id: 'vwatch-settings',
            title: 'Per-Follow Settings',
            lede: 'Fine-tune each follow: movies, TV, or both — and how far back to reach.',
            body: `
Each follow has its own settings — decide whether to auto-add **movies**, **TV**, or **both**, and how far back to reach into their catalog.

This keeps a prolific studio from flooding your wishlist while still catching the titles you care about. Following A24 for movies-only, for example, won't bury you in their television output.
`
        },
        {
            id: 'vwatch-scan',
            title: 'Release Scanning',
            lede: 'Scheduled scans walk your follows, find what\'s new, and write it into the wishlist.',
            body: `
A scan automation walks your follows on a schedule, finds anything new, and writes it into the wishlist with poster art and season metadata — so entries render correctly and are ready to grab.

You can also trigger a check on demand when you're impatient about a specific release. Scan frequency and behavior are configurable under [Video: Automations](#video-automations).
`
        },
    ]
});

// Video: Wishlist — wanted & cutoff-unmet, search now & status, upgrade until cutoff.
registerDocsSection({
    id: 'video-wishlist',
    title: 'Video: Wishlist',
    icon: '🎬',
    pages: [
        {
            id: 'vwish-wanted',
            title: 'Wanted & Cutoff-Unmet',
            lede: 'Everything the video side wants but doesn\'t have at the quality you asked for.',
            body: `
The **Wishlist** is everything the video side wants but doesn't have at the quality you asked for:

- **Wanted** — titles you don't own yet
- **Cutoff-unmet** — titles you own below their profile's cutoff quality

It's the video equivalent of a Sonarr/Radarr "Wanted" queue: the single list the download pipeline works through.
`
        },
        {
            id: 'vwish-search',
            title: 'Search Now & Status',
            lede: 'Kick off an immediate hunt for a title — and see honestly where every entry stands.',
            body: `
**Search Now** kicks off an immediate hunt for a wishlist entry instead of waiting for the next automation pass.

Status is shown honestly — searching, found, grabbed, failed with an attempt counter — so you always know where each title stands. Missing art can be backfilled for movies and shows in bulk, so the wishlist stays visually complete even for titles added from bare metadata.
`
        },
        {
            id: 'vwish-upgrade',
            title: 'Upgrade Until Cutoff',
            lede: 'Below-cutoff titles stay wanted until a strictly better release appears — then upgrade in place.',
            body: `
Titles below their profile's cutoff stay on the wishlist and are re-grabbed only when a **strictly better** release appears — no churn on sidegrades.

When an upgrade lands, it **replaces the file in place** in the real library folder using the video path resolver — so you upgrade quality without duplicating files or breaking your server's paths. Once a title meets its cutoff, it leaves the wishlist for good (until you raise the cutoff).
`
        },
    ]
});
