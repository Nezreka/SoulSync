// Video — the full video side: overview, dashboard, search, discover, library,
// detail pages, watchlist, wishlist, downloads, requests, calendar, automations,
// YouTube, tools, import, settings, API reference. (14 + 15 merged.)

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

It connects to **Plex** or **Jellyfin** to read your movie and TV libraries, enriches every title from **TMDB**, **TVDB**, **OMDb**, **fanart.tv**, **Trakt**, **TVMaze** and **OpenSubtitles**, and can search, grab, organize, and upgrade downloads to fill the gaps in your collection.

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
Ten repair jobs that scan for problems — broken files, duplicates, metadata gaps, missing episodes, YouTube ghost files, and more — and fix them, with rich findings.
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
Use the **side switcher** in the app header (the Audio ↔ Video toggle) to flip between the Music and Video apps. Each side has its own navigation, its own pages, and its own settings.

A handful of pages are **shared** across both sides — **Chat**, **Issues**, and this **Help & Docs** page — so you land on the same page no matter which side you were on. Everything else is side-specific: the video sidebar shows Dashboard, Search, Discover, Library, Watchlist, Wishlist, Downloads, Requests, Calendar, Automations, Tools, and Settings for the video side.

> [!TIP]
> Your last-used side is remembered in this browser (localStorage), so you'll land right back where you left off next time you open SoulSync on this device — it's not tied to your profile.
`
        },
        {
            id: 'vid-server',
            title: 'Connecting a Media Server',
            lede: 'Point the video side at Plex or Jellyfin, pick your libraries, and scan them in.',
            body: `
The video side reads your libraries from **Plex** or **Jellyfin**. Configure the connection under **Video → Settings**, pick which libraries to include, then run a scan to import them.

SoulSync stores a lightweight copy of every movie/show/episode row and enriches it in the background — it never modifies your server's files during a scan.

| Server | What SoulSync reads | Auth |
|--------|--------------------|------|
| **Plex** | Movie & TV libraries, watch state, collections, incremental delta via \`addedAt\` | URL + Token |
| **Jellyfin** | Movie & TV libraries, watch state, BoxSets, incremental delta via \`MinDateLastSaved\` | URL + API Key (+ user for watch state) |

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
With multi-profile enabled, each profile can be granted access to **Music**, **Video**, or **both** when an admin creates or edits the profile. A music-only profile can't see or reach the video side at all — the whole Video navigation is hidden and the video API rejects its requests server-side. Admins always have both sides.

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
- **Attention** — open issues and pending maintenance findings that need you
- **System stats** — live memory and uptime

> [!NOTE]
> There is no health strip here. Server, indexer, and download-client reachability surfaces through the scan flows that actually use them. \`/api/video/health\` checks local state only — library roots, disk space, the recycle folder, maintenance errors, monitor liveness — and the notification panel header surfaces it (fetched by the downloads page, which is the only page that queries it).
`
        },
        {
            id: 'vdash-continue',
            title: 'Continue Watching',
            lede: 'Pick up right where you left off — partially-watched movies and next-up episodes in one rail.',
            body: `
A **Continue Watching** rail surfaces partially-watched movies and the next unwatched episode of shows in progress, drawn from the watch state SoulSync ingests from your server.

Each card jumps straight to the title's detail page with a **Next Up** call-to-action, so resuming a series is one click. Watch state syncs from Plex or Jellyfin during library scans — mark something watched in your server app and SoulSync picks it up on the next pass.

See [Watch State & History](#vdet-watch) for how watch state works per title.
`
        },
        {
            id: 'vdash-activity',
            title: 'Recently Added & Attention',
            lede: 'What just landed in your library, plus open issues and maintenance findings that need you.',
            body: `
**Recently added** titles show what's landed in your library. The dashboard's **attention** area surfaces open issues and pending maintenance findings — the things that actually need your input.

Use it as a quick sanity check after an overnight run: if an automation grabbed something, you'll see it in recently-added; if it found a problem, it's in attention. There is no running activity feed.
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
        {
            id: 'vsearch-fresh',
            title: 'Fresh Releases Board',
            lede: 'A live torrent board from EXT.to — cached, matched, and instant to open.',
            body: `
The **Fresh Releases** tab on the Search page is a live torrent board sourced from **EXT.to** — the Movies and TV torrent sections for Day, Week, and Month, parsed from the EXT.to homepage into rows carrying size, file count, age, seeders, and leechers.

## How the board stays warm

Building the board on the render path used to mean sitting through a Cloudflare challenge every time you opened the tab — and matching each release against its own detail page (where the poster, IMDb id, rating, genres, and runtime live) would have made it far worse: every row is a separate challenge, and a bad minute on ext.to can cost 40 seconds a page.

So the work moved **off the render path entirely**:

::: steps
1. **Pull** — the board scraper fetches the EXT.to homepage through FlareSolverr and parses the Movies/TV tables for Day, Week, and Month.
2. **Match** — every release is matched against its own detail page (poster, IMDb id + rating, genres, runtime). Release titles are parsed for year, resolution, source, codec, audio, HDR, group, and season/episode info (packs detected).
3. **Cache** — matched details are keyed by detail URL and kept in the video database; the rendered snapshot is stored as a setting. A returning release costs nothing — each run only pays for what's genuinely new.
4. **Serve** — opening the tab reads the **last snapshot** via \`GET /api/video/downloads/fresh-releases\`, so it's instant.
:::

Two things run this same refresh (identical work, identical state):

- The **Refresh Fresh Releases** automation (hourly by default) — the \`Refresh Fresh Releases\` button on the [Automations](#vauto-shared) page
- The **Refresh** button on the tab itself — \`POST /api/video/downloads/fresh-releases/refresh\`

> [!NOTE]
> The board turns over slowly: most of one hour's pull was there the hour before, so steady-state runs are cheap. A first run on a cold cache is the expensive one. A per-run time budget (10 minutes) and a new-detail backstop keep a bad Cloudflare minute from overlapping the next tick — whatever a run defers is logged and picked up by the next one.

## Grabbing from the board

The board is **browse-only**. Grabbing goes through the **identify modal** — click a release, tell SoulSync what the title actually is (movie or episode, which one), and the exact release is handed to the normal download pipeline with the release-parser hints the row already carries (magnet URI, parsed quality, season/episode).

> [!IMPORTANT]
> The board requires **FlareSolverr** (\`flaresolverr.url\` on Settings). Without it the tab tells you so and shows nothing — the scraper can't get past EXT.to's Cloudflare challenge on its own.
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
            lede: 'Tune Discover to your region and services — these preferences are global.',
            body: `
Tune Discover to your region and services:

- **Languages** — preferred languages for recommendations and metadata
- **Providers** — restrict recommendations to the streaming services you actually use
- **Ignored titles** — titles you never want to see again, anywhere in Discover

These preferences are **global**, not per profile — one shared set for the whole household, stored in the video database.
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

- **Filter** by status (all / owned / wanted / watched / unwatched), resolution, genre, and more
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
            lede: 'Act on many titles at once — monitor, mark watched, edit ratings and genres, refresh art.',
            body: `
Select multiple titles to act on them at once:

- Bulk **monitor / unmonitor**
- Bulk **mark watched / unwatched**
- Bulk **content rating** changes
- Bulk **genre add / remove**
- Bulk **artwork refresh**

Large bulk jobs run in the background so the UI stays responsive — kick off a thousand-title job and keep browsing.

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

Movies and shows can come from either Plex or Jellyfin and render the same way. From a detail page you can manage metadata, change quality profiles, toggle watch state, and kick off downloads.
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
            lede: 'Fine-tune each follow: how far back to reach into their catalog.',
            body: `
Each follow has one setting: the **back-catalog window** (\`lookback_years\`) — how far back to reach into their catalog. \`0\` is forward-only (upcoming and new releases), \`N\` reaches N years back, and \`-1\` is everything.

This keeps a prolific studio from flooding your wishlist while still catching the titles you care about.

> [!NOTE]
> Person scans are **movies only** — shows are followed through the watchlist's own show automations, not through person follows. There is no movies/TV/both choice per follow.
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

registerDocsSection({
    id: 'video-downloads',
    title: 'Video: Downloads',
    icon: '🎬',
    pages: [
        {
            id: 'vdl-queue',
            title: 'The Download Queue',
            lede: 'A live view of everything in flight — per-item speed, ETA, progress, and state.',
            body: `
The **Downloads** page is the mission-control view for video acquisition. Every grab shows live speed, ETA, progress, and state. From here you can grab a release manually, retry a failed one, cancel, or clear completed items.

## How a grab flows

::: steps
1. **Search** — find releases for a movie, episode, season, or pack from the detail page or wishlist.
2. **Evaluate** — each candidate is ranked against your [quality profile](#vdl-quality) and custom formats before it's accepted.
3. **Download** — the winning release goes to your download client (torrent, Usenet, or Soulseek) and is monitored to completion.
4. **Organize** — finished files are renamed and moved into your library according to your [organization settings](#vdl-organize), then your media server is told to scan.
:::

> [!NOTE]
> Grabbing from the download modal's read-only lookups is open to any video-enabled profile. Actions that actually start a download require the profile's **download permission**; changing profiles, formats, and client config is admin-only.

## Queue actions

| Action | What it does |
|--------|--------------|
| **Grab** | Queue the selected release for download |
| **Retry** | Re-attempt a failed or stalled grab |
| **Cancel** | Stop an in-flight download and remove it from the queue |
| **Clear** | Remove completed items from the queue view |

The **Clients** view (Torrents / Usenet / Soulseek tabs) shows per-client health and throughput so you can see at a glance which download path is doing the work.
`
        },
        {
            id: 'vdl-quality',
            title: 'Quality Profiles & Formats',
            lede: 'Radarr-class quality ladders with cutoffs, plus scored custom formats that steer the ranker.',
            body: `
## Quality profiles

A **quality profile** is an ordered ladder of allowed qualities — for example, HDTV-720p → WEBDL-1080p → WEBDL-2160p → Bluray-2160p. Two settings control upgrades:

- **Allowed qualities** — which rungs of the ladder are acceptable at all.
- **Cutoff** — once a file at or above the cutoff is in your library, SoulSync stops looking for upgrades. Items below the cutoff stay on the wishlist with an "upgrade watch" flag until something better arrives.

## Custom formats

**Custom formats** are scored release matchers layered on top of the profile. They nudge the ranker toward releases you like (a preferred release group, a specific HDR flavor, TrueHD audio) and away from ones you don't (extras, mislabeled scene tags). Positive scores boost a release; negative scores penalize it.

## Per-title overrides

Quality isn't one-size-fits-all — see [Per-Title Quality & Series Type](#vdet-quality) for how individual movies and shows carry their own quality profile and series-type setting, overriding the global default.

> [!WARNING]
> Quality profile and custom format reads are open to any video-enabled profile (the download modal needs them to queue a grab); only **writes** are admin-only. Download-client credentials are admin-only for both reads and writes — those reads can expose tokens.

YouTube grabs use their own separate quality selector rather than the movie/TV ladder.
`
        },
        {
            id: 'vdl-lists',
            title: 'Import Lists',
            lede: 'Turn lists you curate elsewhere — TMDB, IMDb, Plex Watchlist — into titles SoulSync goes and gets.',
            body: `
**Import lists** sync an external list into your video acquisition lists. SoulSync periodically pulls each list in — movies go to the video wishlist, shows are followed on the watchlist with the list's monitor policy — and the pipeline then searches for and downloads them.

## Supported sources

- **tmdb_list** — a public TMDB list id
- **tmdb_chart** — a living chart (trending_movies, top_shows, …)
- **imdb_list** — an IMDb list id
- **plex_watchlist** — your Plex account's watchlist

## Setting one up

::: steps
1. Open **Video → Settings → Downloads** (admin) and add a list: choose the source and paste the list URL or identifier.
2. Pick the quality profile new titles should use.
3. Save — the next sync imports any titles you don't already have (movies as wishlist items, shows as watchlist follows).
:::

> [!TIP]
> Import lists are a great way to share curation duties: let a partner maintain an IMDb list and your library fills itself in.

There's no per-list schedule — syncing runs through the **Sync Import Lists** automation block, so you decide the cadence with an automation on a schedule. Import-list configuration is admin-only.
`
        },
        {
            id: 'vdl-blocklist',
            title: 'Blocklist & Recycle Bin',
            lede: 'Bad releases get quarantined automatically — and replaced files are kept, not destroyed.',
            body: `
## Blocklist

When a grab turns out to be a bad file — corrupt, mislabeled, wrong title — SoulSync **blocklists** that specific release (by source + filename). The blocklist is honored at every stage:

- The **ranker** filters blocked releases out of future searches.
- **Retry** and automatic re-queries skip them.
- A **manual grab** always overrides the blocklist, in case you know better.

## Recycle bin

When an upgrade replaces an existing file, the old file isn't deleted outright — it lands in a **recycle bin** first. If the "upgrade" turns out to be worse, you can recover the original.

> [!NOTE]
> Blocklist management is admin-gated, since it affects what the whole server will download.
`
        },
        {
            id: 'vdl-history',
            title: 'History',
            lede: 'A permanent archive of every grab and its outcome.',
            body: `
The **History** view archives every grab with its outcome — what was grabbed, when, from which source, and whether it succeeded. It's the paper trail for your library: useful for auditing, for figuring out why a title never arrived, and for spotting patterns in failing sources.

After a download completes, a lightweight post-download scan probes your media server first and skips a full library crawl when it can already see the newest grab — faster imports, less churn.
`
        },
        {
            id: 'vdl-organize',
            title: 'Organization & Rename',
            lede: 'Naming schemes, folder layout, and mass rename with a preview before you commit.',
            body: `
## Naming & folders

Set your **naming scheme** (how files and folders are named — title, year, edition tags, season/episode patterns) and your **folder layout** once, and every import follows it. Consistent naming keeps Plex/Jellyfin matching clean.

## Mass rename

Already have a library with messy names? **Mass rename** shows you a full preview of what every file *would* become under your current scheme, and only applies the changes when you confirm. Nothing is renamed blind.

## When imports fail

Sometimes a completed download can't be placed automatically — ambiguous match, unexpected folder layout. Those land in [Failed Imports](#vimp-failed), where an admin can manually place each file into the right title or dismiss it.
`
        },
    ]
});

registerDocsSection({
    id: 'video-requests',
    title: 'Video: Requests',
    icon: '🎬',
    pages: [
        {
            id: 'vreq-flow',
            title: 'Request → Approve → Wishlist',
            lede: 'Let non-admin members ask for movies and shows; admins approve with one click.',
            body: `
The **Requests** system gives every member a voice in what gets added. A member searches for a movie or show and submits a request; an admin reviews the queue and either **approves** it — movies drop straight onto the wishlist, shows go to the watchlist — or **denies** it.

## How it works

::: steps
1. **Member requests** — any video-enabled profile can submit a request for a title.
2. **Admin reviews** — the queue shows pending requests with a counts badge so admins never miss one.
3. **Approve or deny** — approving adds the title to acquisition immediately (movie → wishlist, show → watchlist with the monitor policy expanded); denying closes the request.
4. **Clean up** — resolved requests can be cleared from the queue; a member can withdraw their own request at any time.
:::

> [!NOTE]
> Members submit requests and see their own. Viewing everyone's requests and approving/denying are **admin** actions.

This is the video equivalent of the music side's request flow — one shared pattern for household curation.
`
        },
    ]
});

registerDocsSection({
    id: 'video-calendar',
    title: 'Video: Calendar',
    icon: '🎬',
    pages: [
        {
            id: 'vcal-grid',
            title: 'Week Grid & Air Times',
            lede: 'Upcoming episodes on a week grid with air times, agenda, and click-through details.',
            body: `
The **Calendar** lays out upcoming episodes on a week grid — the same shape as the Sonarr/Radarr calendars. Each day shows what's airing with air times; clicking a day opens an agenda, and clicking an episode opens its details.

You can scope the calendar to your **watchlist** or everything (\`watchlist\` / \`all\`), and switch between compact and agenda views. Everything is scoped per media server, so you only see what's relevant to your setup.

> [!TIP]
> The calendar is driven by your monitored shows — follow a show on the [video watchlist](#vwatch-follow) and its airings appear automatically.
`
        },
        {
            id: 'vcal-movie',
            title: 'Movie Lane',
            lede: 'Upcoming film releases — theatrical, digital, and physical windows — in one lane.',
            body: `
Alongside the TV grid, a dedicated **movie lane** tracks upcoming releases for your **wishlisted movies**: the **cinema** (theatrical premiere) date and the **available** (home-release) date, filterable by type. Movies and shows live in one view, so you can see the whole week of what's coming at a glance.
`
        },
        {
            id: 'vcal-ical',
            title: 'iCal Feed',
            lede: 'Subscribe to upcoming airings and releases from any calendar app.',
            body: `
Take the calendar with you: SoulSync serves an **iCal feed** you can subscribe to from Apple Calendar, Google Calendar, or anything else that speaks iCal.

\`\`\`text
/api/video/calendar.ics?scope=watchlist
\`\`\`

The feed respects the same scope parameter as the page (\`watchlist\` or \`all\`), so your phone calendar only shows what you care about.

> [!NOTE]
> The iCal feed uses your session context — subscribe from a browser where you're logged in, or check with your admin about feed authentication for your setup.
`
        },
    ]
});

registerDocsSection({
    id: 'video-automations',
    title: 'Video: Automations',
    icon: '🎬',
    pages: [
        {
            id: 'vauto-shared',
            title: 'Shared System Automations',
            lede: 'The same automation engine as the music side — scheduled tasks and event workflows for video.',
            body: `
The video side surfaces the same **system automation engine** the music side uses: scheduled tasks and event-driven workflows, minus the music-only kinds (Beatport, user, playlist triggers). SoulSync seeds **28 system automations** for video — all enabled, all editable or deletable.

## Seeded video system automations

| Name | Schedule | What it does |
|------|----------|--------------|
| **Auto-Scan Video After Downloads** | On *Video Download Batch Done* | Scans the server for new downloads, waits for it to finish, then fires *Video Library Scan Done* |
| **Auto-Update Video Database After Scan** | On *Video Library Scan Done* | Reads newly-indexed media from the server into SoulSync |
| **Auto-Update Video Database (Hourly)** | Every hour | Incremental server read so manual additions appear within the hour |
| **Refresh Airing TV Schedules** | Daily 23:00 | Re-pulls TMDB episode schedules (air dates, stills) for still-airing watchlisted shows |
| **Auto-Wishlist Episodes Airing Today** | Daily 01:00 | Wishes every episode airing today for followed shows; prunes ended/canceled shows |
| **Refresh Stale Metadata** | Every 6 hours | Re-pulls the stalest matched movies & shows (up to 500/run, skipping anything refreshed in the last 30 days) |
| **Auto-Update Overlays** | Daily 04:00 | Renders + pushes enabled overlay templates onto server artwork |
| **Sync Collections** | Daily 04:30 | Resolves + pushes every enabled collection to the server |
| **Clean Up Plex Images** | Every 7 days | Clears Plex's stale cached artwork so replaced posters/overlays actually show |
| **Clean Search History** | Every hour | Removes old Soulseek searches |
| **Clean Completed Downloads** | Every 5 minutes | Clears completed downloads and empties directories |
| **Full Cleanup** | Every 12 hours | Clears quarantine, download queue, import folder, and search history in one sweep |
| **Auto-Backup Database** | Every 3 days | Timestamped backup of the video library database |
| **Auto-Deep Scan TV Library** | Weekly, Mon 02:00 | Full reconcile of the TV library — re-reads every show, drops what the server no longer has |
| **Auto-Deep Scan Movie Library** | Weekly, Tue 02:00 | Full reconcile of the movie library |
| **Auto-Scan Watchlist People** | Daily 03:00 | Wishes every movie a followed person acted in or directed that you don't own |
| **Auto-Scan Watchlist Studios** | Daily 03:30 | Wishes every movie a followed studio produced that you don't own |
| **Auto-Scan Watchlist Channels** | Every 6 hours | Wishes new long-form uploads from followed YouTube channels (Shorts excluded) |
| **Auto-Scan Watchlist Playlists** | Every 6 hours | Wishes newly-added videos from followed YouTube playlists |
| **Auto-Process Movie Wishlist** | Every hour | Grabs wished, released movies from Soulseek per your quality profile |
| **Auto-Process Episode Wishlist** | Every hour | Grabs wished episodes from Soulseek per your quality profile |
| **Auto-Process YouTube Wishlist** | Every hour | Downloads wished YouTube videos via yt-dlp |
| **RSS Sync (Instant Grabs)** | Every 15 minutes | Pulls indexer RSS via Prowlarr and instantly grabs wishlist matches — no waiting for the hourly drain |
| **Seeding Sweep** | Every 30 minutes | Removes completed torrents that hit your seed ratio/time goal (library copy untouched) |
| **Refresh Fresh Releases** | Every hour | Warms the [Fresh Releases board](#vsearch-fresh) — pulls the EXT.to board and matches new releases to their detail pages |
| **Sync Import Lists** | Every 6 hours | Imports TMDB/IMDb charts and lists + your Plex watchlist into acquisition |
| **Auto-Clean Old YouTube Episodes** | Daily 04:00 | Deletes downloaded channel episodes outside each channel's keep window |
| **Empty Recycle Bin** | Daily 04:30 | Permanently deletes recycled files older than the keep window |

> [!TIP]
> Stagger-heavy schedules (watchlist scans, wishlist drains, seeding sweep) already have startup delays baked in so a restart doesn't fire everything at once.

## The 30 video action blocks

Every action below is a DO block you can drop into your own automations. Config shown as *field (default)*.

| Action | What it does | Key config |
|--------|--------------|------------|
| **Scan Video Library** | Tell the media server to rescan your movie/TV sections, then read what it found into SoulSync | Mode (Full), Library (Movies + TV) |
| **Scan Video Server** | Get the server to index new downloads (skips if it already has them), wait, then fire *Video Library Scan Done* | Library (all), Skip if present (on), Grace 2 min, Max wait 60 min, Fallback wait 120 sec |
| **Update Video Database** | Read newly-indexed media from the server into SoulSync | Mode (Incremental), Library (all) |
| **Update Video Database (Hourly)** | Same incremental read for an hourly schedule — pair with a 1-hour Schedule trigger | — |
| **Deep Scan TV Library** | Full reconcile of TV: re-read every show, drop what the server no longer has (never touches movies) | — |
| **Deep Scan Movie Library** | Full reconcile of the movie library (never touches TV) | — |
| **Refresh Airing TV Schedules** | Re-pull TMDB episode schedules for still-airing watchlisted shows — run a couple hours before the airing run | — |
| **Refresh Stale Metadata** | Rolling freshness pass over the stalest matched titles (ratings, overviews, art, air dates); never clobbers your locked data | Items per run (500), Skip movies/shows refreshed within 30 days |
| **Clean Old YouTube Episodes** | Delete downloaded channel episodes outside each channel's keep window (history kept so they're never re-downloaded) | — |
| **Empty Recycle Bin** | Permanently delete recycled files older than the keep window in Settings → Library Organization | — |
| **Wishlist Today's Airings** | Add every episode airing today (followed shows) to the wishlist; optionally prune ended/canceled shows | Prune ended shows (on) |
| **Scan Watchlist People** | Wishlist every movie a followed person acted in or directed that you don't own | — |
| **Scan Watchlist Studios** | Wishlist every movie a followed studio produced that you don't own | — |
| **Scan Watchlist Channels** | Wishlist new long-form uploads from followed YouTube channels | — |
| **Scan Watchlist Playlists** | Wishlist newly-added videos from followed YouTube playlists (each becomes its own show) | — |
| **Process Movie Wishlist** | Auto-grab wished, released movies from Soulseek per your quality profile | Max simultaneous searches (3) |
| **Process Episode Wishlist** | Auto-grab wished episodes from Soulseek per your quality profile | Max simultaneous searches (3) |
| **RSS Sync (Instant Grabs)** | Instantly grab wishlist matches from indexer RSS (needs torrent/Usenet + Prowlarr) | — |
| **Refresh Fresh Releases** | Keep Search → Fresh Releases warm: pull the EXT.to board, match releases to detail pages (poster, IMDb, rating) | Max new releases to match per run (40) |
| **Sync Import Lists** | TMDB lists/charts, IMDb lists, and your Plex watchlist enter acquisition automatically | — |
| **Seeding Sweep** | Remove completed torrents at your seed ratio/time goal — the client's copy only, library copy never touched | — |
| **Process YouTube Wishlist** | Download wished YouTube videos via yt-dlp into channel/year/date shows | Max simultaneous downloads (3) |
| **Clean Search History** | Remove old Soulseek searches | — |
| **Clean Completed Downloads** | Clear completed downloads and empty directories | — |
| **Full Cleanup** | Quarantine + download queue + import folder + search history in one sweep | — |
| **Backup Database** | Timestamped backup of the video library database | — |
| **Apply Overlays** | Render + push enabled overlay templates onto server artwork | — |
| **Sync Collections** | Resolve + push every enabled collection to the server | — |
| **Clean Up Plex Images** | Clear Plex's stale cached artwork so replaced posters/overlays actually show | — |
| **Run Maintenance Job** | Force-run one [Library Maintenance](#vtool-repair) job (or all enabled due jobs) | Job (All enabled jobs) |

If you already know the [music automations](#auto-overview), the video side works identically — same builder, same signals, same history view.
`
        },
        {
            id: 'vauto-events',
            title: 'Event Triggers',
            lede: 'Chain workflows off video events: a grab starts, a scan finishes, a download completes.',
            body: `
The video builder exposes **19 video-only event triggers**, plus the shared ones (Schedule, Daily Time, Weekly Schedule, Monthly Schedule, App Started, Signal Received, Issue Reported, Issue Status Changed, Issue Reply, Webhook Received — the same blocks the music side uses).

Triggers with conditions let you filter (e.g. only fire when \`kind\` is \`movie\`, or when a finding's severity is \`critical\`). Every trigger also exposes its variables — \`{title}\`, \`{quality}\`, \`{season}\`, … — for use in notification message templates.

| Trigger | Fires when | Key config (conditions) |
|---------|-----------|-------------------------|
| **Release Grabbed** | A release is handed to a download client (Soulseek / torrent / Usenet) — fires at grab time with the release name, long before anything lands in the library | title, kind (movie / show / youtube), quality, source |
| **Video Download Batch Done** | A batch of video downloads finishes | — |
| **Video Library Scan Done** | The media server finishes rescanning your video sections | — |
| **Request Filed** | A user files a request on the Requests page | title, kind, requester |
| **Request Approved** | An admin approves a request and the title enters acquisition | title, kind, requester |
| **Request Declined** | An admin declines a request | title, kind, requester |
| **Request Arrived** | A title somebody requested shows up in the library | title, kind, requester |
| **Video Downloaded** | One movie, episode, or YouTube video finishes downloading and lands in the library | title, kind, channel, quality |
| **Video Download Failed** | A download gives up for good (after retries) — the item goes back on the wishlist | title, kind, error |
| **Video Import Failed** | A file downloads fine but can't be placed (sample, wrong episode, not an upgrade) and needs manual import | title, kind, error |
| **Quality Upgrade Landed** | A download **replaced** an existing library copy with a better one | title, kind |
| **Maintenance Finding Raised** | A Library Maintenance job raises a **new** finding — condition on severity \`critical\` for outage alerts | job_id, finding_type, severity, title |
| **Maintenance Scan Done** | A Library Maintenance job finishes a scan | job_id, status |
| **Video Wishlist Item Added** | Something is added to the video wishlist (a movie, one or more episodes, or YouTube videos) | title, kind |
| **Video Watchlist Follow** | A show, person, channel, or playlist is followed on the watchlist | title, kind |
| **Video Watchlist Unfollow** | A show, person, channel, or playlist is unfollowed | title, kind |
| **Collections Synced** | A collections sync pass finishes (manual or the nightly automation) | — |
| **Overlays Applied** | An overlay apply pass finishes | — |
| **Video Database Updated** | SoulSync finishes reading the server's library into its database | — |

::: steps
1. Pick a trigger — e.g. **Video Downloaded** to react to every finished download.
2. Add conditions — e.g. only when \`kind\` is \`episode\` and \`quality\` is below your target.
3. Add a DO action — e.g. **Refresh Stale Metadata**, or **Fire Signal** to hand off to a second automation.
4. Add THEN steps — notify yourself on Discord, run a script, fire a signal.
:::

> [!TIP]
> The classic post-download chain is already seeded for you: **Auto-Scan Video After Downloads** (Video Download Batch Done → Scan Video Server) and **Auto-Update Video Database After Scan** (Video Library Scan Done → Update Video Database). Chain your own steps after them — e.g. *Video Database Updated → Apply Overlays → Discord*.

See [Automations](#auto-overview) for the full builder reference.
`
        },
    ]
});

registerDocsSection({
    id: 'video-youtube',
    title: 'Video: YouTube Channels',
    icon: '🎬',
    pages: [
        {
            id: 'vyt-follow',
            title: 'Following Channels',
            lede: 'Follow a YouTube channel like a show — new uploads flow through the normal download pipeline.',
            body: `
Follow a **YouTube channel** (or a specific **playlist**) the way you'd follow a TV show. SoulSync uses yt-dlp to track new uploads and bridges them into the video wishlist/watchlist, so channel videos flow through the same download pipeline as everything else.

It's visual-first: channels get artwork and a proper detail page showing their videos, what you own, and what's still available.

## Per-channel settings

Each followed channel (or playlist) has its own settings:

- **Custom name** — a display-name override
- **Quality** — a per-channel quality override (blank uses the global default)
- **Title include / exclude** filters and a **minimum minutes** length (channels only)
- **Retention** — blank or "all" means keep everything
- **Retry policy** and **archive recheck days** — how failed pulls are retried and how often archived videos are rechecked

> [!NOTE]
> Starting a download from a channel requires the profile's **download permission**; following and browsing are open to any video-enabled profile.
`
        },
        {
            id: 'vyt-import',
            title: 'Import Subscriptions',
            lede: 'Bulk-import your existing YouTube subscriptions with a preview first.',
            body: `
Already subscribed to dozens of channels on YouTube? Don't re-follow them one by one.

::: steps
1. Open the YouTube section and choose **Import Subscriptions**.
2. Paste (or upload) your **ytdl-sub / Kometa subscription file** — SoulSync shows a **preview** of every channel it found.
3. Hit **Import** — everything in the file is followed at once (there's nothing to pick and choose; already-followed channels are skipped untouched).
4. Watch live progress as the background job adds each channel and wishes its recent videos.
:::

You can adjust per-channel settings after import from each channel's page.
`
        },
        {
            id: 'vyt-downloaded',
            title: 'Downloaded State',
            lede: 'Honest ownership tracking: what you have versus what is still available.',
            body: `
Ownership is tracked by your **actual download history**, so a channel's video list honestly shows what you already have versus what's still available — no guessing, no phantom entries.

To avoid mismatches between YouTube's ever-shifting metadata and your files, the extractor's title at download time is treated as authoritative for matching.
`
        },
    ]
});

registerDocsSection({
    id: 'video-tools',
    title: 'Video: Tools',
    icon: '🎬',
    pages: [
        {
            id: 'vtool-overlays',
            title: 'Overlay Studio',
            lede: 'A visual, Kometa-style overlay template editor with live preview.',
            body: `
**Overlay Studio** is a visual editor for poster overlays, Kometa-style. Design **badge templates** — resolution, HDR flavor, ratings, audio codec, awards, custom text, logo packs — on a live preview canvas, assign templates to filtered sets of titles, and **apply** them. SoulSync renders the overlays with Pillow and writes them straight back to your Plex/Jellyfin posters.

When you want the originals back, the **cleanup** tool removes overlays again.

> [!WARNING]
> The Overlay Studio is a management surface — **admin-only** for both reads and writes.
`
        },
        {
            id: 'vtool-collections',
            title: 'Collection Manager',
            lede: 'Smart collections synced to Plex Collections or Jellyfin BoxSets, with generated posters.',
            body: `
Build **collections** two ways:

- **Smart filters** — "all 4K sci-fi from the 80s", "every Oscar Best Picture winner you own" — membership updates itself.
- **Hand-picked** — curate the exact titles yourself.

Then **sync** them to your server as Plex **Collections** or Jellyfin **BoxSets**, complete with auto-generated posters.

## Gap filling

The manager shows you what's *missing* from a collection — and lets you **wishlist the gaps in one click**. You can also import/export collection definitions and **adopt** collections that already exist on the server.

> [!WARNING]
> The Collection Manager is **admin-only** for both reads and writes.
`
        },
        {
            id: 'vtool-repair',
            title: 'Library Maintenance',
            lede: 'Repair jobs that find library problems and fix them — individually or in bulk.',
            body: `
**Library Maintenance** runs ten repair jobs that scan the video library for problems. Each job produces rich, lazy-loaded **findings** you can fix individually, fix in bulk, resolve, or dismiss — with a full run history and live progress while jobs execute. Run jobs on demand, on the Tools page's interval, or from an automation via the **Run Maintenance Job** action (chain it with the *Maintenance Finding Raised* trigger for alerts).

| Job | What it scans | What approving a finding does |
|-----|---------------|------------------------------|
| **Missing Episodes** | Every library show, one finding per show listing its aired, monitored, un-owned episodes (specials opt-in; ended shows included for back-catalog gaps). The finding is keyed to the missing set, so a changed set raises a fresh finding and retires the stale one. | Sends the episodes to the video wishlist (poster + per-episode stills/overviews from a cached TMDB fetch); the wishlist drain downloads them. |
| **Complete the Collection** | Owned movies grouped by TMDB collection; one finding per franchise with the gap ("The Matrix Collection — you have 2 of 4"). Unreleased members don't count as missing. | Missing films go to the movie wishlist (poster + year); the wishlist drain downloads them. |
| **Quality Upgrade** | Each owned movie/episode's **best** file judged against your quality profile with the same seam the Download modal uses. Stays quiet when you chase-the-best (no cutoff configured). | A real upgrade grab — the drain's own search/pick/enqueue seams run for that one title (the drain itself refuses owned titles, so upgrades can't ride it). |
| **Broken Files** | A cheap corruption heuristic, no ffmpeg: probed runtime vs the movie's known runtime (a 138-minute film whose file runs 61 minutes is truncated); anything under 5 MB is a stub. Warning severity — these *look* owned but won't play through. | A replacement grab through the same seams as Quality Upgrades; the import pipeline swaps the file in. |
| **Metadata Gaps** | Owned movies that are unmatched (TMDB never identified them) or missing overview / genres / poster / backdrop. Fields you deliberately blanked and **locked** are respected. One finding per movie listing exactly what's missing. | Re-queues the TMDB match when unmatched, then runs the detail page's art/credits refresh — gap-fill only, never clobbers. |
| **Duplicate Copies** | Three signals: the same \`tmdb_id\` owned as two library rows, one movie carrying 2+ version files, and one episode carrying 2+ files. Every finding names the **drive** each copy sits on. | Varies by finding — review the drives and keep the copy you want. |
| **Wishlist Audit** | Wishlist rows the pipeline will never act on: the target is already owned **and done** (owned copy meets the cutoff, or its quality is unreadable so the upgrader can't judge it). Owned-but-below-cutoff rows are deliberately left alone so upgrade-until can chase them. | Removes the row. The owned copy is untouched. |
| **YouTube Ghost Files** | The download-ledger rows whose file is gone from disk (deleted on the server side). Without this, badges lie and retention counts overcount. Two fix modes: **prune** (stamp \`pruned_at\` — remembered, never re-downloaded) or **forget** (delete the row — becomes eligible again). Files are never touched; DB-only. Safety: unreachable YouTube root (down SMB mount) hard-errors instead of flagging everything; mass-missing (over half of 5+ checked files gone at once) asks rather than assuming. | Prune or forget the ghost row. |
| **Watched Cleanup** | Movies watched at least once whose last watch is older than your \`watched_days\` (optionally also never-watched movies sitting \`unwatched_days\`, off by default). Watch-state changes ride the incremental scan, so candidates appear without a deep scan. Movies with no watch date are skipped, not guessed. | Moves the file to the **recycle bin** (never a hard delete) and marks the row file-less. |
| **Naming Conformance** | Renders the **current** organization template for every owned movie/episode file and flags paths that differ — the Sonarr/Radarr "preview rename" gap, since templates only ever applied to new imports. The findings list *is* the preview (current → new); bulk select = mass rename. Safety: unlocatable files are skipped, occupied destinations are per-finding errors (never overwritten), and DB paths aren't rewritten here — the next scan reconciles them after the server notices. | Renames the file (plus same-stem sidecars and subtitles) into place. |

> [!TIP]
> Run maintenance after big imports or server migrations. It's the fastest way to reconcile the database with what's actually on disk.
`
        },
        {
            id: 'vtool-enrichment',
            title: 'Enrichment',
            lede: 'Background workers that fill in metadata from TMDB, TVDB, OMDb, fanart.tv, and more.',
            body: `
Background **enrichment workers** fill in metadata from TMDB, TVDB, OMDb, fanart.tv, OpenSubtitles, Return-YouTube-Dislike, SponsorBlock, and more.

From the Enrichment view you can:

- Watch **per-service coverage** and status
- **Pause / resume** a service
- **Re-prioritize** which services run first
- **Test credentials** for a service
- Inspect **unmatched items** and **manually re-match** a stubborn title
`
        },
        {
            id: 'vtool-activity',
            title: 'Server Activity',
            lede: 'Tautulli-style live streams and watch history without leaving SoulSync.',
            body: `
A **Server Activity** drawer shows live Plex streams and recent watch history app-wide — who's watching what, on which device, with transcoding details. It's the Tautulli glanceable view, built in, so you can see what's playing without leaving SoulSync.
`
        },
        {
            id: 'vtool-backups',
            title: 'Backups',
            lede: 'On-demand or scheduled backups of the video database — restore or download when needed.',
            body: `
Take **on-demand or scheduled backups** of the video database, then restore or download them when needed.

> [!DANGER]
> A restore **replaces the entire database** — and it's **staged, not instant**: the swap happens on the next restart, with the current database set aside (kept, not deleted). Backup endpoints are admin-only, and you should verify a backup's integrity before restoring over a live system.
`
        },
    ]
});

registerDocsSection({
    id: 'video-import',
    title: 'Video: Import',
    icon: '🎬',
    pages: [
        {
            id: 'vimp-failed',
            title: 'Failed Imports',
            lede: 'When a download can\'t be placed automatically, put it where it belongs by hand.',
            body: `
When a completed download can't be automatically placed into the library — ambiguous match, unexpected folder layout — it lands in **Failed Imports**. From there you can:

- **Place** it manually into the correct title, or
- **Dismiss** it if it doesn't belong in your library.

> [!NOTE]
> The Import page is hidden from the sidebar nav — you reach it through the **Manual Import** button on failed items in [Downloads](#video-downloads). Manual import placement mutates the library on disk, so it's **admin-only**.

This is the video equivalent of the music side's manual import step.
`
        },
    ]
});

registerDocsSection({
    id: 'video-settings',
    title: 'Video: Settings',
    icon: '🎬',
    pages: [
        {
            id: 'vset-server',
            title: 'Server & Libraries',
            lede: 'Connect Plex/Jellyfin, test it, and choose which libraries the video side manages.',
            body: `
Configure your **Plex / Jellyfin connection**, test it, and pick which **libraries** the video side manages. For Jellyfin you can select which user's watch state to read.

Server and library configuration lives on the admin-only Settings page — the connection details are sensitive, so reads are gated too.
`
        },
        {
            id: 'vset-services',
            title: 'Enrichment Services & Keys',
            lede: 'API keys for TMDB, TVDB, OMDb, fanart.tv — plus indexer and download-client credentials.',
            body: `
Enter API keys for **TMDB, TVDB, OMDb, fanart.tv, OpenSubtitles** and the rest, set **service priority** (which source wins when several have the answer), and manage **indexer / download-client credentials** (Usenet, torrents, Soulseek).

> [!WARNING]
> These endpoints return raw tokens, so they're **admin-gated for both reads and writes**.
`
        },
        {
            id: 'vset-notify',
            title: 'Notifications',
            lede: 'Get pinged when grabs land, imports finish, and upgrades arrive.',
            body: `
Wire up event **notifications** — Discord, webhook, Telegram — for grabs, imports, and upgrades, and send a **test** to verify delivery before you rely on it.

> [!WARNING]
> Notification config exposes webhook URLs and bot tokens, so it's **admin-only**.
`
        },
    ]
});

registerDocsSection({
    id: 'video-api',
    title: 'Video: API Reference',
    icon: '🎬',
    pages: [
        {
            id: 'vapi-auth',
            title: 'Auth Model',
            lede: 'Session-authenticated internal API — your browser session, not API keys.',
            body: `
The video API lives under \`/api/video\` and is **session-authenticated** — it uses your logged-in browser session and active profile, *not* the \`sk_\` API keys the public [REST API](#api-auth) uses. It's the app's own internal API — separate from the key-authenticated public video v1 surface (\`/api/v1/video/*\`, documented under [Video](#api-video)).

A single blueprint-level gate enforces these rules on every request:

| Rule | Effect |
|------|--------|
| **Side access** | A non-admin profile without video access gets \`403\` on *every* \`/api/video/*\` route. |
| **Admin-only surfaces** | Management & credential endpoints (overlays, collections, repair, import, server/library config, Soulseek, enrichment config, notifications, backups) require an admin profile for *both* reads and writes — their GETs can leak tokens or expose server config. |
| **Admin-only writes** | Config the Settings page writes but content views legitimately read (server presence, quality tiers, library metadata edits, monitor, blocklist, poster set, per-title quality/series-type, per-show sync) is gated on writes only. |
| **Download permission** | Actions that trigger a download (grab, retry, YouTube download, wishlist/watchlist add) require the profile's \`can_download\` flag. |

> [!NOTE]
> The tables below are a route reference for the session-authenticated internal \`/api/video\` surface — these act on your logged-in browser session, not an API key, so there's no in-page "try it" runner for them. The key-authenticated video v1 surface (\`/api/v1/video/*\`, documented under [Video](#api-video)) **is** covered by the Try It runner in the API explorer at the bottom of the API Reference pages.
`
        },
        {
            id: 'vapi-content',
            title: 'Library, Detail & Discover',
            lede: 'Read-only content endpoints — open to any video-enabled profile.',
            body: `
Base path: \`/api/video\`. Read-only content endpoints, open to any video-enabled profile.

| Method | Path | Purpose |
|--------|------|---------|
| GET | \`/dashboard\` | Dashboard tiles: stats + recent (continue-watching and health are separate endpoints) |
| GET | \`/library\` | Owned movies & shows with filters, sort, pagination |
| GET | \`/library/resolutions\`, \`/library/genres\` | Facet values for the library filters |
| GET | \`/detail/{kind}/{id}\` | Full detail record (seasons/episodes for shows) |
| GET | \`/detail/{kind}/{id}/history\`, \`/extras\` | Per-title grab/import history and extras |
| GET | \`/tmdb/{kind}/{tmdb_id}\`, \`/episode/...\`, \`/person/{id}\` | Live TMDB lookups for detail, seasons, episodes, people |
| GET | \`/search\`, \`/search/studios\`, \`/trending\` | Search movies/shows/people/studios; trending feed |
| GET | \`/studio/{id}\`, \`/studio/{id}/movies\`, \`/studio/presets\` | Studio detail, paged filmography, preset studios |
| GET | \`/discover/hero\`, \`/discover/foryou\`, \`/discover/taste\`, \`/discover/morelike\`, \`/discover/gaps\`, \`/discover/genres\`, \`/discover/list\`, \`/discover/trailer\` | Discover feed surfaces |
| GET/POST | \`/discover/ignore\`, \`/discover/languages\`, \`/discover/providers-pref\` | Global discover preferences |
| GET | \`/calendar\`, \`/calendar.ics\` | Upcoming airings/releases (JSON + iCal feed) |
| GET | \`/poster/{kind}/{id}\`, \`/backdrop/...\`, \`/img\` | Poster/backdrop/art proxy |
`
        },
        {
            id: 'vapi-acquire',
            title: 'Downloads, Wishlist & Watchlist',
            lede: 'Acquisition endpoints. Download triggers need can_download; config writes need admin.',
            body: `
Base path: \`/api/video\`. Download *triggers* require \`can_download\`; config writes and library mutations require admin.

| Method | Path | Purpose | Auth |
|--------|------|---------|------|
| GET | \`/downloads/active\`, \`/downloads/status\`, \`/downloads/history\` | Live queue & history | video |
| POST | \`/downloads/search\`, \`/downloads/evaluate\` | Find & rank releases | video |
| POST | \`/downloads/grab\`, \`/downloads/grab-pack\`, \`/downloads/retry\` | Queue a grab / retry | can_download |
| POST | \`/downloads/cancel\`, \`/downloads/clear\` | Cancel / clear queue items | video |
| GET/POST | \`/downloads/quality\`, \`/downloads/quality/profiles\`, \`/downloads/quality/formats\` | Quality profiles & custom formats | admin (write) |
| GET/POST | \`/downloads/config\`, \`/downloads/config/import-lists\` | Download & import-list config | admin (write) |
| GET/POST/DELETE | \`/downloads/blocklist\` | Release blocklist | video (GET open); admin (writes) |
| GET/POST | \`/downloads/slskd\` | Soulseek credentials/config | admin |
| GET | \`/wishlist\`, \`/wishlist/counts\` | Wanted & cutoff-unmet | video |
| POST | \`/wishlist/search\`, \`/wishlist/search-all\` | Search Now for wishlist items | can_download |
| POST | \`/wishlist/add\` | Add to wishlist | can_download |
| POST | \`/wishlist/remove\`, \`/wishlist/clear\` | Remove / clear wishlist | can_download |
| POST | \`/wishlist/backfill-art\` | Backfill missing wishlist art | video |
| GET | \`/watchlist\`, \`/watchlist/counts\` | Followed people/studios | video |
| POST | \`/watchlist/add\` | Follow a person/studio | can_download |
| POST | \`/watchlist/remove\` | Unfollow a person/studio | can_download |
| POST | \`/watchlist/person/{id}/settings\`, \`/watchlist/studio/{id}/settings\` | Per-follow settings | can_download |
| POST | \`/requests\` | Submit a member request | video |
| POST | \`/requests/{id}/approve\`, \`/requests/{id}/deny\` | Approve / deny a request | admin |
| DELETE | \`/requests/{id}\` | Withdraw your own request (admins: any) | video |
| POST | \`/scan/request\`, \`/scan/server\`, \`/scan/stop\` | Trigger / stop a library scan | video |
| POST | \`/monitor\`, \`/bulk/start\` | Monitor toggle & bulk jobs | admin |
| PUT/POST | \`/detail/{kind}/{id}/metadata\`, \`/lock\`, \`/quality-profile\`, \`/series-type\`, \`/watched\` | Edit / lock / configure a title (watched is open) | admin (watched: video) |
`
        },
        {
            id: 'vapi-manage',
            title: 'Studios: Overlays, Collections & Repair',
            lede: 'Management surfaces — admin-only for every method.',
            body: `
Base path: \`/api/video\`. **Admin-only** for every method.

| Method | Path | Purpose |
|--------|------|---------|
| GET/POST/PUT/DELETE | \`/overlays/templates...\` | Overlay template CRUD, duplicate, thumbnails |
| GET/PUT | \`/overlays/assignments\` | Assign templates to filtered title sets |
| POST | \`/overlays/apply\`, \`/overlays/cleanup\`, \`/overlays/filter/preview\` | Render & write overlays to the server; remove them |
| GET/POST | \`/overlays/logopack\`, \`/overlays/upload\`, \`/overlays/preview\` | Logo packs, uploads, preview filmstrip |
| GET/POST/PUT/DELETE | \`/collections...\` | Collection CRUD, presets, preview, members, missing |
| POST | \`/collections/{id}/sync\`, \`/collections/sync\`, \`/collections/server/adopt\` | Sync collections to Plex/Jellyfin; adopt existing |
| POST | \`/collections/{id}/wishlist_missing\`, \`/collections/posters/regenerate\` | Wishlist gaps; regenerate posters |
| GET/POST/PUT | \`/repair/jobs...\`, \`/repair/status\`, \`/repair/toggle\`, \`/repair/pause\`, \`/repair/resume\` | Library Maintenance jobs & scheduler |
| GET/POST | \`/repair/findings...\` | Findings: fix, bulk-fix, resolve, dismiss, clear |
| GET/POST | \`/import/failed\`, \`/import/{id}/place\`, \`/import/{id}/dismiss\` | Manual import of failed downloads |
| GET | \`/backups\` | List backups |
| POST | \`/backups\` | Create a backup |
| POST | \`/backups/restore\` | Stage a restore (applies on next restart) |
| DELETE | \`/backups/restore\` | Cancel a staged restore |
| GET | \`/backups/<name>/download\` | Download a backup file |
`
        },
        {
            id: 'vapi-settings',
            title: 'Settings & Enrichment',
            lede: 'Server, library, enrichment, and notification config.',
            body: `
Base path: \`/api/video\`. Credential-exposing endpoints are **admin (any method)**; content-read config is admin-on-write.

| Method | Path | Purpose | Auth |
|--------|------|---------|------|
| GET | \`/server\` | Media-server connection | open (any video profile) |
| POST | \`/server\` | Media-server connection | admin |
| GET/POST | \`/server-config\`, \`/server-config/test\` | Media-server connection | admin |
| GET/POST | \`/libraries\`, \`/jellyfin/users\`, \`/jellyfin/user\` | Managed libraries & Jellyfin user | admin |
| GET/POST | \`/enrichment/config\`, \`/enrichment/priority\`, \`/enrichment/retry-all-failed\` | Enrichment services & priority | admin |
| GET/POST | \`/notifications\`, \`/notifications/test\` | Notification config & test | admin |
| GET/POST | \`/downloads/config\` | Download client & naming config | admin (write) |
`
        },
        {
            id: 'vapi-youtube',
            title: 'YouTube',
            lede: 'Channel/playlist following and downloads.',
            body: `
Channel/playlist following and downloads under \`/api/video/youtube\`.

| Method | Path | Purpose | Auth |
|--------|------|---------|------|
| GET | \`/youtube/channels\`, \`/youtube/channel/{id}\`, \`/youtube/wishlist\` | Followed channels & their videos | video |
| GET | \`/youtube/search\`, \`/youtube/resolve\`, \`/youtube/video/{id}\`, \`/youtube/playlist/{id}\` | Search & resolve YouTube content | video |
| POST | \`/youtube/follow\`, \`/youtube/unfollow\`, \`/youtube/playlist/follow\`, \`/youtube/playlist/unfollow\` | Follow/unfollow a channel or playlist | video |
| POST | \`/youtube/subscriptions/preview\`, \`/youtube/subscriptions/import\` | Bulk-import subscriptions | video |
| POST | \`/youtube/download\`, \`/youtube/wishlist/add\` | Download a video / add to wishlist | can_download |
| GET/POST | \`/youtube/channel/{id}/settings\` | Per-channel pull settings | video |
`
        },
        {
            id: 'vapi-bulk',
            title: 'Bulk Ops, Rename, Recycle, Watch & Issues',
            lede: 'The rest of the video API: bulk metadata, mass rename, recycle bin, Fresh Releases, watch party, overlay assets, and issues.',
            body: `
Base path: \`/api/video\`. Session-authenticated like the rest of this surface (see [Auth Model](#vapi-auth)).

## Bulk metadata operations

The library grid's multi-select action bar. Items go through the same edit-and-lock engine as the Manage sidebar, so bulk edits push to the server and survive scans.

| Method | Path | Purpose | Auth |
|--------|------|---------|------|
| POST | \`/bulk/start\` | Start a bulk job: \`{kind, ids[], action, params}\`. Only one bulk op runs at a time — \`409\` if busy | admin |
| GET | \`/bulk/status\` | Job state (polling fallback for the bell) | video |

Bulk actions: \`content_rating\`, \`genre_add\`, \`genre_remove\`, \`monitored\`, \`watched\`, \`refresh_art\`. The \`collection_add\` action runs **inline** (one write, no job) and needs \`kind\` \`movie\`/\`show\` plus \`params.collection_id\`.

## Mass rename

Preview and apply the current organization templates to existing files (the API behind [Naming Conformance](#vtool-repair) and the mass-rename UI). Scanning a big library resolves every stored path against the filesystem, so the preview runs on a background worker and the UI polls.

| Method | Path | Purpose |
|--------|------|---------|
| GET | \`/organization/rename/preview\` | Kick off (or report) the preview → \`{success, ready, done, total, entries?, unresolved?, error?}\` |
| GET | \`/organization/rename/preview/status\` | Poll the in-flight preview without starting a new one |
| POST | \`/organization/rename/apply\` | Apply renames from a fresh preview: \`{keys?: [...]}\` — omitted keys means everything the preview found. \`409\` if a run is already in progress |

**Admin-only, any method** — renaming the library is management.

## Recycle bin

Every video delete lands here first (upgrade replaces, YouTube retention, watched cleanup). Browse, restore, or purge.

| Method | Path | Purpose |
|--------|------|---------|
| GET | \`/downloads/recycle\` | List entries → \`{items, total_size, keep_days, recycle_enabled}\` (keep window defaults to 7 days) |
| POST | \`/downloads/recycle/restore\` | Restore one entry \`{trash_dir, name}\`, or everything with \`{all: true}\` |
| POST | \`/downloads/recycle/purge\` | Permanently delete one entry, or empty the bin with \`{all: true}\` |

## Fresh Releases, release detail, quality evaluate

| Method | Path | Purpose |
|--------|------|---------|
| GET | \`/downloads/fresh-releases\` | The stored [Fresh Releases board](#vsearch-fresh): reads the **last snapshot**, never the network, so the tab opens instantly → \`{source: "EXT.to", sections, total, fetched_at}\`. Errors when FlareSolverr isn't configured or no board exists yet |
| POST | \`/downloads/fresh-releases/refresh\` | Refresh the board **now** (the tab's Refresh button) — identical work to the hourly automation; reports \`{running: true}\` if one is already in flight |
| GET | \`/downloads/detail?url=\` | The facts behind one EXT.to release (poster, IMDb id, rating, genres, runtime). Cache-first; add \`fetch=1\` for a live lookup on a miss |
| POST | \`/downloads/evaluate\` | Judge an owned file against your quality profile: \`{file: {resolution, video_codec, …}}\` → \`{meets, resolution_label, reasons[]}\`. Powers the Download modal's *"In your library · … (below your target)"* line |

## SponsorBlock segments

| Method | Path | Purpose |
|--------|------|---------|
| GET | \`/youtube/video/{video_id}/segments\` | Crowd segments stored for a video (sponsor / intro / outro / …) so the player can offer skips. \`[]\` until the SponsorBlock enrichment worker processes it |

## Watch party (watch together)

Movie night runs on what someone can actually play. The chat page's ballot is a deterministic fold over the room's protocol bus — every client sees the same nominations, but **ownership is personal**: each SoulSync checks its own library and renders Play or Grab. "Owned" means a real playable file, not mere library presence.

| Method | Path | Purpose | Auth |
|--------|------|---------|------|
| GET | \`/watch/library?q=\` | Nominate picker: owned titles matching the query (2+ chars). Rows carry \`art\` (local poster proxy) and \`po\` (bus-safe TMDB CDN URL or null — a tokened server URL never rides the public bus) | video |
| POST | \`/watch/owned\` | \`{items: [{kd: "m"/"t", id, s?, e?}]}\` → \`{owned: {"m:603": true, "t:1399:1x1": false, …}}\` | video |
| GET | \`/watch/playable?kd=&id=&s=&e=\` | Whether **this box** can stream the party's pick to a browser (direct-play verdict) — asked before playback so the room gets an honest answer | video |
| GET | \`/watch/stream?kd=&id=&s=&e=\` | Byte-serves the party's file with Range/206 support (seeking is how a latecomer joins mid-showing). Local file, or proxied from the media server — the upstream URL carries the server token and is **never exposed to the browser** | video |
| POST | \`/watch/grab\` | \`{kd, id, s?, e?, ti?, y?, po?}\` → hydrated wishlist add + immediate manual search (envelopes stay lean; hydration happens here) | can_download |

> [!NOTE]
> The client names a **title** (kind + TMDB id + season/episode), never a path — the path comes from the library row and is re-rooted through the path resolver, so no request shape can point these endpoints at an arbitrary file. Kids profiles get \`403\` on over-cap titles.

## Overlay assets

| Method | Path | Purpose |
|--------|------|---------|
| GET | \`/overlays/logo/{field}/{value}\` | The drop-in-pack logo for a field value, so the editor previews the real mark. \`404\` when no pack/match — the editor falls back to text |
| GET | \`/overlays/logopack\` | What logo art is installed (powers the palette gate) + install job state |
| POST | \`/overlays/logopack/install\` | Copy Kometa's public logo set into the local drop-in folders (opt-in, user-initiated). Returns immediately — poll \`/overlays/logopack\` |
| GET | \`/overlays/preview/random?kind=\` | A random owned item for the editor's preview ("surprise me"); \`kind=poster/season/episode\` |
| GET | \`/overlays/preview/search?kind=&q=\` | Search seasons/episodes to preview a Season/Episode template on a real one |

All \`/overlays/*\` endpoints are **admin, any method**.

## Issues

Problem reports from the people using the library (filed against a movie, show, or episode). They feed the **Issue Reported / Issue Status Changed / Issue Reply** automation triggers.

| Method | Path | Purpose |
|--------|------|---------|
| GET | \`/issues?status=&category=&entity_type=&limit=&offset=\` | List issues (reporter identity is admin-only) |
| POST | \`/issues\` | File an issue: \`{entity_type: "movie"/"show"/"episode", entity_id, category, …}\` |
| GET | \`/issues/{id}\` | Issue detail |
| PUT | \`/issues/{id}\` | Update — owners may edit their own title/description; admins can move status |
| DELETE | \`/issues/{id}\` | Delete (admin) |
| POST | \`/issues/bulk\` | Admin bulk: \`{ids, status?/priority?}\` or \`{ids, delete: true}\` |
| POST | \`/issues/{id}/comments\` | Reply on an issue |
| GET | \`/issues/counts\`, \`/issues/categories\` | Counts and the category list |

Categories: \`wrong_match\`, \`wrong_metadata\`, \`wrong_poster\`, \`bad_quality\`, \`audio_issue\`, \`subtitle_issue\`, \`playback_issue\`, \`missing_content\`, \`duplicate\`, \`other\`.
`
        },
    ]
});
