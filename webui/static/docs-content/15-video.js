// ═══════════════════════════════════════════════════════════════════
// SoulSync Docs — Video side, part 2: Downloads, Requests, Calendar,
// Automations, YouTube, Tools, Import, Settings, API reference.
// ═══════════════════════════════════════════════════════════════════

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

Quality isn't one-size-fits-all: individual movies and shows can carry their own quality profile and series-type setting from their detail page, overriding the global default.

> [!WARNING]
> Quality profiles, custom formats, and download-client credentials are **admin-only** for both reads and writes — the reads can expose tokens. The download modal's read-only metadata lookups stay open so any allowed profile can queue a grab.

YouTube grabs use their own separate quality selector rather than the movie/TV ladder.
`
        },
        {
            id: 'vdl-lists',
            title: 'Import Lists',
            lede: 'Turn lists you curate elsewhere — Trakt, IMDb, Plex Watchlist — into titles SoulSync goes and gets.',
            body: `
**Import lists** sync an external list into your video wishlist on a schedule. Maintain a list on Trakt, IMDb, or your Plex Watchlist, and SoulSync periodically pulls it in — every title becomes something the pipeline will search for and download.

## Setting one up

::: steps
1. Open **Video → Downloads → Import Lists** (admin).
2. Add a list: choose the provider and paste the list URL or identifier.
3. Pick a sync schedule and the quality profile new titles should use.
4. Save — the next sync imports any titles you don't already have as wishlist items.
:::

> [!TIP]
> Import lists are a great way to share curation duties: let a partner maintain a Trakt list and your library fills itself in.

Import-list configuration is admin-only.
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
The **Requests** system gives every member a voice in what gets added. A member searches for a movie or show and submits a request; an admin reviews the queue and either **approves** it — which drops the title straight onto the wishlist for the pipeline to fulfill — or **denies** it.

## How it works

::: steps
1. **Member requests** — any video-enabled profile can submit a request for a title.
2. **Admin reviews** — the queue shows pending requests with a counts badge so admins never miss one.
3. **Approve or deny** — approving adds the title to the wishlist immediately; denying closes the request.
4. **Clean up** — resolved requests can be cleared from the queue.
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

You can scope the calendar to your **watchlist**, your **library**, or everything, and switch between compact and full views. Everything is scoped per media server, so you only see what's relevant to your setup.

> [!TIP]
> The calendar is driven by your monitored shows — follow a show on the [video watchlist](#vwatch-follow) and its airings appear automatically.
`
        },
        {
            id: 'vcal-movie',
            title: 'Movie Lane',
            lede: 'Upcoming film releases — theatrical, digital, and physical windows — in one lane.',
            body: `
Alongside the TV grid, a dedicated **movie lane** tracks upcoming film releases across their windows: theatrical premieres, digital releases, and physical media dates. Movies and shows live in one view, so you can see the whole week of what's coming at a glance.
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

The feed respects the same scope parameter as the page (\`watchlist\`, \`library\`, or \`all\`), so your phone calendar only shows what you care about.

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
The video side surfaces the same **system automation engine** the music side uses: scheduled tasks and event-driven workflows, minus the music-only kinds (Beatport, user, playlist triggers).

Use it to schedule the recurring work a video library needs:

- **Library refreshes** on a schedule
- **Wishlist scans** for wanted titles
- **Watchlist checks** for new releases
- **Enrichment passes** to backfill metadata

If you already know the [music automations](#auto-overview), the video side works identically — same builder, same signals, same history view.
`
        },
        {
            id: 'vauto-events',
            title: 'Event Triggers',
            lede: 'Chain workflows off video events: a grab completes, a scan finishes, a title is added.',
            body: `
A generic **event bus** exposes video events as automation triggers, so you can chain workflows together. Video events include things like:

- A grab completes
- A library scan finishes
- A title is added to the library
- A wishlist item is fulfilled

Example chain: *"after a scan finishes → run enrichment → refresh artwork."* Build it once in the automation builder and it runs itself forever.

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

Each followed channel has its own settings:

- **How many recent videos** to pull when you first follow
- **How far back** to reach into the archive
- **Quality** selection for channel downloads

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
2. Provide your subscription list — SoulSync shows a **preview** of every channel it found.
3. Tick the channels you want to follow and hit **Import**.
4. Watch live progress as each channel is added with your default per-channel settings.
:::

You can adjust per-channel pull settings after import from each channel's page.
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
**Library Maintenance** runs repair jobs that scan the video library for problems:

- Missing artwork
- Ghost / orphan database rows
- Un-monitored gaps
- YouTube ghosts (entries with no backing channel)
- Stale watched state
- And more

Each job produces rich, lazy-loaded **findings** you can fix individually, fix in bulk, resolve, or dismiss — with a full run history and live progress while jobs execute.

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
> A restore **replaces the entire database**. Backup endpoints are admin-only, and you should verify a backup's integrity before restoring over a live system.
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

This is the video equivalent of the music side's manual import step.

> [!NOTE]
> Manual import placement mutates the library on disk, so it's **admin-only**.
`
        },
    ]
});

registerDocsSection({
    id: 'video-settings',
    title: 'Video: Settings & Side Access',
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
        {
            id: 'vset-access',
            title: 'Side Access',
            lede: 'Grant each profile Music, Video, or both — enforced server-side, not just hidden.',
            body: `
Under multi-profile, an admin grants each profile access to **Music**, **Video**, or **both** when creating or editing the profile. A music-only profile has the entire video side hidden *and* blocked server-side — every \`/api/video/*\` route returns 403.

See [Multi-Profile](#prof-overview) for the full permission model.
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
The video API lives under \`/api/video\` and is **session-authenticated** — it uses your logged-in browser session and active profile, *not* the \`sk_\` API keys the public [REST API](#api-auth) uses. It's the app's own internal API; there is no key-authenticated public surface for the video side.

A single blueprint-level gate enforces these rules on every request:

| Rule | Effect |
|------|--------|
| **Side access** | A non-admin profile without video access gets \`403\` on *every* \`/api/video/*\` route. |
| **Admin-only surfaces** | Management & credential endpoints (overlays, collections, repair, import, server/library config, Soulseek, enrichment config, notifications, backups) require an admin profile for *both* reads and writes — their GETs can leak tokens or expose server config. |
| **Admin-only writes** | Config the Settings page writes but content views legitimately read (server presence, quality tiers, library metadata edits, monitor, blocklist, poster set, per-title quality/series-type, per-show sync) is gated on writes only. |
| **Download permission** | Actions that trigger a download (grab, retry, YouTube download, wishlist/watchlist add) require the profile's \`can_download\` flag. |

> [!NOTE]
> The tables below are a route reference. Because these run against your live library and some are destructive, there's no in-page "try it" runner for the video API (unlike the public REST API).
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
| GET | \`/dashboard\` | Dashboard tiles: counts, activity, continue-watching, health |
| GET | \`/library\` | Owned movies & shows with filters, sort, pagination |
| GET | \`/library/resolutions\`, \`/library/genres\` | Facet values for the library filters |
| GET | \`/detail/{kind}/{id}\` | Full detail record (seasons/episodes for shows) |
| GET | \`/detail/{kind}/{id}/history\`, \`/extras\` | Per-title grab/import history and extras |
| GET | \`/tmdb/{kind}/{tmdb_id}\`, \`/episode/...\`, \`/person/{id}\` | Live TMDB lookups for detail, seasons, episodes, people |
| GET | \`/search\`, \`/search/studios\`, \`/trending\` | Search movies/shows/people/studios; trending feed |
| GET | \`/studio/{id}\`, \`/studio/{id}/movies\`, \`/studio/presets\` | Studio detail, paged filmography, preset studios |
| GET | \`/discover/hero\`, \`/discover/foryou\`, \`/discover/taste\`, \`/discover/morelike\`, \`/discover/gaps\`, \`/discover/genres\`, \`/discover/list\`, \`/discover/trailer\` | Discover feed surfaces |
| GET/POST | \`/discover/ignore\`, \`/discover/languages\`, \`/discover/providers-pref\` | Per-profile discover preferences |
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
| GET/POST/DELETE | \`/downloads/blocklist\` | Release blocklist | admin |
| GET/POST | \`/downloads/slskd\` | Soulseek credentials/config | admin |
| GET | \`/wishlist\`, \`/wishlist/counts\` | Wanted & cutoff-unmet | video |
| POST | \`/wishlist/search\`, \`/wishlist/search-all\` | Search Now for wishlist items | video |
| POST | \`/wishlist/add\` | Add to wishlist | can_download |
| POST | \`/wishlist/remove\`, \`/wishlist/clear\`, \`/wishlist/backfill-art\` | Manage wishlist | video |
| GET | \`/watchlist\`, \`/watchlist/counts\` | Followed people/studios | video |
| POST | \`/watchlist/add\` | Follow a person/studio | can_download |
| POST | \`/watchlist/remove\`, \`/watchlist/person/{id}/settings\`, \`/watchlist/studio/{id}/settings\` | Manage follows | video |
| POST | \`/requests\` | Submit a member request | video |
| POST | \`/requests/{id}/approve\`, \`/requests/{id}/deny\`, DELETE \`/requests/{id}\` | Resolve requests | admin |
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
| GET/POST/DELETE | \`/backups...\` | Create, restore, download, delete backups |
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
| GET/POST | \`/server\`, \`/server-config\`, \`/server-config/test\` | Media-server connection | admin |
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
    ]
});
