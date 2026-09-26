// Podcasts & Audiobooks — the spoken-word sides: podcast discovery, RSS/OPML,
// watchlist auto-download, episode retention, and the full audiobook pipeline
// (browse, library, wishlist, releases, quality, per-author settings, API).

// Podcasts: Overview — RSS-based podcasts, the watchlist model, what lives where.
registerDocsSection({
    id: 'podcasts',
    title: 'Podcasts',
    icon: '🎙️',
    pages: [
        {
            id: 'pod-overview',
            title: 'Podcasts: Overview',
            lede: 'RSS-powered podcasts inside SoulSync — browse a directory, follow shows, auto-download new episodes.',
            body: `
SoulSync's **Podcasts** side is an RSS-powered podcast manager. Browse a global podcast directory, open any show, and **follow** it by adding it to your podcast **Watchlist** — SoulSync then checks the show's RSS feed on a schedule, auto-downloads new episodes, and prunes old ones per your retention settings.

There is no separate "subscribe" concept: **following = watchlisting**. Everything downstream — new-episode checks, auto-download, retention cleanup — keys off the Watchlist.

::: cards
### 🎙️ Directory & Search
Search the iTunes podcast directory, browse 20 categories, or paste any RSS feed URL directly — including private Patreon/Substack member feeds.
### 👁️ Watchlist
Following a show adds it to the Watchlist with auto-download on and 14-day retention by default. Per-show settings tune both.
### ⬇️ Auto-Download
The **Auto-Scan Podcasts** system automation checks every watchlisted feed every 6 hours and queues episodes published since you followed the show (up to 5 per show per pass).
### 🧹 Retention
Downloaded episodes older than your retention window are deleted from disk automatically. Set per show: 7/14/30/60 days, a custom window, or keep forever.
:::

> [!NOTE]
> Podcast routes are guarded by page access the same way other sections are. The podcast API has no per-route auth decorators — access follows the app-global login gate (\`security.require_login\`, off by default), and the download endpoint additionally honours your profile's download permission.
`
        },
        {
            id: 'pod-browse',
            title: 'Browsing & Search',
            lede: 'The podcast hub: search, 20 categories, a featured spotlight, and the show grid.',
            body: `
The **Podcasts** hub page (\`/podcasts\`) is laid out top to bottom:

## Header & search

The hero header carries the page title **Podcasts** ("Discover, search, and download podcast episodes") and three right-side controls:

- **Add RSS** — opens the **Add Podcast by RSS Feed** modal (see [Adding Feeds](#pod-rss-opml)).
- **OPML** — opens the **Podcast Subscriptions (OPML)** modal for import/export.
- **{N} Downloads** — a badge that appears only when you have completed downloads.

The search bar placeholder reads **"Search podcasts or paste RSS URL (e.g. Huberman, NPR, https://…)"**. Typing searches the directory (debounced); pressing **Enter** with an \`http(s)://\` value jumps straight to that feed's show page. A **✕** clear button appears while text is present.

## Categories

Ten popular category pills sit in a tablist — **Trending** (the default), **Technology**, **News & Politics**, **True Crime**, **Comedy**, **Science**, **Business**, and three more — plus a **🗂️ All Categories (20)** pill that opens the **Explore All Categories** modal: a searchable grid of 20 genre cards ("Discover curated podcasts, top charts, and featured shows by genre").

## Spotlight & grid

- **★ FEATURED PODCAST** — a spotlight carousel of the first 5 featured shows. It auto-advances every 5.5 seconds (pausing on hover), with arrows and dots. Each card has **Explore Episodes** and **Add to Watchlist** / **Watching ✓** buttons, plus an "Available Content" meta panel.
- **Show grid** — titled **{Category} Podcasts**, or **Search Results for "{query}"** ("Showing top matches from the global podcast directory"). When more than 5 shows are listed, sort tabs appear: **Featured**, **A–Z**, **Most Episodes**.

Show cards display artwork, title, an explicit **E** badge where applicable, author, **N eps**, and the first category. Hovering reveals an eye-icon **watchlist quick-toggle**; clicking a card opens the show page (addressed by iTunes ID when the show has one, otherwise the encoded feed URL).

> [!NOTE]
> The **{N} Downloads** badge in the header currently has no click action wired up — it's a display-only count of completed downloads.
`
        },
        {
            id: 'pod-detail',
            title: 'Show Page & Episodes',
            lede: 'The podcast detail page: billboard, season tabs, episode rows, notes, and downloads.',
            body: `
Opening a show (\`/podcasts/$podcastId\`) loads its live RSS feed — full metadata plus the episode list — with on-disk **downloaded** flags merged in.

## Billboard

The top billboard shows:

- **Back to Podcasts** button, category tags, explicit **E** badge, language chip.
- Title, **By {author}**, and chips: **Episodes: N**, **Latest: {date}**, **Website: {hostname}**.
- Action buttons: **Play Latest Episode**, **Add to Watchlist** / **Watching** / **Updating…** (eye icon), **RSS Feed** (copies the feed URL to your clipboard, then reads **Feed URL Copied**), and **Visit Site ↗**.
- The description, truncated at 280 characters with **Read more** / **Show less**.
- A right-hand panel with a **LATEST RELEASE** card (own **Play Episode** button), **Total Archive … eps**, and **Language …** stats.

## Episodes

Below the billboard, the episode browser offers:

- Tabs: **All Episodes ({N})** plus one tab per season (**Season {N}**; year-like numbers ≥ 1900 render as-is).
- A **Filter episodes…** input (shows **(N matched)**) and a sort toggle: **↓ Newest** / **↑ Oldest**.

Each episode row shows artwork with a floating play/pause button, pub-date kicker, duration, an episode-type pill (non-full types in uppercase), **S{season} · E{number}** / **EP {n}** pills, a clickable title, a description snippet, and a file-size badge.

The per-episode action bar has:

- A play pill (**{duration}** or **Play**, toggling to **Pause**).
- **Notes** — opens the **Episode Notes & Transcript** modal: full HTML show notes, date/duration/filesize stats, and its own **Play Episode** plus **Download** / **Downloading…** / **✓ Downloaded** buttons.
- **Download** — queues the episode (see below).

Results paginate at 40 episodes per chunk: **Showing {n} of {m} episodes**, with **Load Next {n} Episodes ↓** and **Show All ({m})** buttons.

## Downloading a single episode

The **Download** button calls the download endpoint for that episode's enclosure URL. Its states run **Download** → spinner **Saving…** / **{n}%** → **✓ Saved**. It is disabled when the episode has no direct audio URL, is already downloaded, or is currently downloading. Episode download state comes in two flavours: the server-supplied **downloaded** flag (on disk, survives restarts) and the live status polled every few seconds — **queued**, **downloading**, **completed**, **error**. Hovering a row explains the state: "Already downloaded", "Downloading ({n}%)", or "No direct audio stream available to download".

> [!NOTE]
> There is no "download all episodes" button anywhere in the podcast UI. Bulk acquisition happens through the Watchlist's auto-download (new episodes only), not the show page.
`
        },
        {
            id: 'pod-rss-opml',
            title: 'Adding Feeds: RSS & OPML',
            lede: 'Add any RSS feed by URL — including private member feeds — or migrate subscriptions with OPML.',
            body: `
## Add Podcast by RSS Feed

The **Add RSS** button opens the **Add Podcast by RSS Feed** modal ("Add custom, Patreon, Substack, or private member podcast feeds."):

::: steps
1. Paste the feed into **RSS Feed URL** (placeholder: \`https://feeds.patreon.com/private/12345\` or \`https://example.com/rss\`).
2. The URL must begin with \`http://\` or \`https://\` — anything else is rejected with a validation error.
3. Click **Load Podcast** (shows **Loading Feed…** while fetching) to open the show's detail page.
:::

The modal accepts standard RSS 2.0 and iTunes-compliant podcast feeds, which is what makes private/paid member feeds work. Shortcut: pasting an \`http(s)://\` URL into the hub search box and pressing **Enter** goes straight to the feed's show page without the modal.

## OPML import & export

The **OPML** button opens the **Podcast Subscriptions (OPML)** modal ("Migrate subscriptions between SoulSync, Apple Podcasts, Pocket Casts, and Overcast."), with two tabs:

**Import OPML** — a drag-and-drop zone (**"Drop an .opml or .xml file here"**, accepts \`.opml\`/\`.xml\`, 5 MB max). Dropping a file previews it: **Found {n} podcasts in file ({m} selected)** with **Select All** / **Deselect All** / **Choose Another File**, then **Import {m} to Watchlist**. Imported shows land on the Watchlist with auto-download on and 14-day retention.

**Export OPML** — **Export Subscribed Podcasts** ("Download a standard OPML 2.0 XML file containing all your SoulSync podcast subscriptions…") downloads \`soulsync-podcasts.opml\`.

## Ingest security

Every external URL passes through the ingest guard:

- Only \`http://\` and \`https://\` are allowed, and only **public internet addresses** — loopback, private, link-local, reserved, and multicast ranges are blocked. The audio proxy re-checks every redirect hop (max 5).
- Feeds are capped at **10 MB**, OPML files at **5 MB**.
- Feed XML is parsed with \`<!DOCTYPE\` refused outright (billion-laughs protection).

> [!TIP]
> Self-hosting a feed on your LAN? The \`podcasts.allow_private_feed_hosts\` config flag opts in to private feed hosts. It has no UI — it's config-file only and defaults to off.
`
        },
        {
            id: 'pod-watchlist',
            title: 'Watchlist, Auto-Download & Retention',
            lede: 'Following a show: the Watchlist model, per-show settings, and the 6-hour scan automation.',
            body: `
## Following = watchlisting

Podcasts have no separate subscribe button. Every **Add to Watchlist** / eye-icon toggle calls the same flow, defaulting to **auto-download: on** and **retention: 14 days**. Toasts confirm: **Added "{title}" to Watchlist** / **Removed "{title}" from Watchlist**.

## Per-show settings

Each watchlisted show has its own settings (opened from the Watchlist page — **Podcast Settings**, "Download & Retention Preferences"):

**Auto-Download** — "Configure automatic downloading for newly published episodes."
- **Auto-download new episodes** — "Automatically grab fresh episodes when new feed releases are detected."

**Episode Retention Period** — "Podcasts are episodic and can accumulate quickly. How long should downloaded episodes be kept before automated cleanup?"
- Presets: **7 days**, **14 days (Default)**, **30 days**, **60 days**, **Keep forever (0)**.
- **Custom retention (days):** — a 0–3650 number input. At 0 the hint reads "Episodes will be kept indefinitely (never cleaned up)."; otherwise "Episodes older than {n} days will be eligible for cleanup."

**Danger Zone** — **Remove from Watchlist** ("Stop monitoring this show for new releases. Existing downloads are untouched."), with a confirm dialog (**Remove Podcast from Watchlist** — "Stop monitoring \"{title}\" for new episodes?", confirm **Remove**).

Footer: **Cancel** / **Save Changes** (toast: "Podcast settings updated").

## The scan automation

The **Auto-Scan Podcasts** system automation runs the scan:

- **Schedule:** every **6 hours**, first run 3 minutes after startup. It's a schedule-triggered system automation (action \`scan_watchlist_podcasts\`), so it can be paused, rescheduled, or toggled with the automation master switch like any other. It also appears in the automation builder as the **Scan Watchlist Podcasts** block ("Check watchlisted podcasts for new episodes and prune expired").
- **Scope:** with no profile given it fans out to every profile that has podcast watchlist rows (falling back to profile 1). A thread lock skips overlapping runs.
- **New episodes:** each watchlisted feed is fetched live; the scan timestamp and episode count are updated. With auto-download on, episodes **newer than the date you followed the show** are claimed newest-first, capped at **5 per show per pass** ("published since you followed the show"). Episodes with no usable follow date fall back to legacy behaviour (newest episode only); episodes with no pub date are treated as new. Already-downloaded episodes (matched by enclosure URL, guid, title, or show) are skipped.
- **Retention:** when \`retention_days > 0\`, downloaded records older than the window are pruned — the file is deleted from disk (empty parent directories removed too) and the record marked pruned. Rows whose file never landed are skipped so they can be retried.
- **Status:** the last scan's stats (time, podcasts checked, episodes queued/pruned, errors, whether a scan is in progress) are exposed for the UI.

> [!TIP]
> The Watchlist page can also trigger a scan immediately for your profile (the scan-now endpoint) — you don't have to wait for the 6-hour tick after following a show.
`
        },
        {
            id: 'pod-downloads',
            title: 'Downloads & Playback',
            lede: 'Episode download states, the streaming proxy, and the floating audio player.',
            body: `
## Download states

Episode downloads are tracked server-side with live status polled every few seconds:

| Status | Meaning |
|--------|---------|
| \`queued\` | Waiting for a download slot |
| \`downloading\` | In flight, with a percentage |
| \`completed\` | On disk |
| \`error\` | Failed — the row keeps the error |
| \`cancelled\` | Cancelled before completion |

Downloads dedupe by id — queueing an episode that's already queued or downloading returns the existing record instead of starting a second copy. Parallelism is bounded by a semaphore (1–10 concurrent). A **cancel-queued** action clears everything still waiting.

Files land in your **Podcasts Folder (output)** (Settings → Library → Folders), organized by the **Podcast Path Template** (default \`$show/Season $season/$title\`).

## Playback

While an episode plays, a floating **Podcast Audio Player** bar (\`MediaPlayerBar\`) appears at the top of the podcast layout and stays visible as you navigate between podcast pages. Streams go through the **audio proxy** (\`GET /api/podcasts/audio-proxy?url=\`), which forwards range requests to the enclosure host and SSRF-guards every URL and redirect hop.

## Podcast Path Template variables

| Variable | Expands to |
|----------|-----------|
| \`$show\` | Show title |
| \`$author\` | Show author |
| \`$title\` | Episode title |
| \`$season\` | Season, zero-padded (01) |
| \`$seasonnum\` | Season number (1) |
| \`$episode\` | Episode, zero-padded (01) |
| \`$episodenum\` | Episode number (1) |
| \`$year\` | Release year |
| \`$date\` | Release date (YYYY-MM-DD) |
| \`$type\` | Episode type |

## Podcast Media Format

The **Podcast Media Format** setting (Settings → Library → Organization) chooses how episodes are stored:

- **Audio Only (MP3 / M4A) — Default**
- **Video Only (MP4 with static artwork for TV media servers)**
- **Audio & Video (Both formats side-by-side)**
`
        },
        {
            id: 'pod-settings',
            title: 'Podcast Settings',
            lede: 'Every podcast setting, where it lives, and the config-only flags.',
            body: `
All podcast settings live under **Settings → Library** (inside the Music media sub-tab — there is no Podcasts sub-tab):

| Setting (exact label) | Location | Default |
|----------------------|----------|---------|
| **Podcasts Folder (output):** | Library → Folders → Music | \`./podcasts\` (\`/app/podcasts\` in Docker; locked, **Unlock** to edit) |
| **Podcast Path Template:** | Library → Organization → File Organization | \`$show/Season $season/$title\` |
| **Podcast Media Format:** | Library → Organization → File Organization | Audio Only (MP3 / M4A) |

The folder hint reads: "Where downloaded podcast episodes are saved and organized."

## Config-only flags (no UI)

These live in \`core/settings.py\` defaults under \`podcasts.*\`:

| Key | Default | Meaning |
|-----|---------|---------|
| \`podcasts.embed_metadata\` | True | Write tags into downloaded episodes |
| \`podcasts.embed_artwork\` | True | Embed cover art |
| \`podcasts.save_artwork\` | True | Save artwork alongside |
| \`podcasts.write_nfo\` | True | Write NFO sidecar |
| \`podcasts.write_json\` | True | Write JSON sidecar |
| \`podcasts.max_concurrent_downloads\` | (bounded 1–10) | Parallel download cap |
| \`podcasts.allow_private_feed_hosts\` | False | Allow LAN/private feed URLs |

> [!NOTE]
> Only the download path and media format are exposed in the Settings UI. The embed/sidecar flags, concurrency cap, and private-host opt-in are config-file only.
`
        },
        {
            id: 'pod-api',
            title: 'Podcast API Reference',
            lede: 'Every podcast endpoint: search, shows, downloads, watchlist, and OPML.',
            body: `
Base path: \`/api/podcasts\`. No per-route auth decorators — access follows the app-global login gate (\`security.require_login\`, off by default); \`POST /download\` additionally honours your profile's download permission.

| Method | Path | Purpose |
|--------|------|---------|
| GET | \`/search?q=&limit=\` | Search the iTunes Search API (limit 1–50, default 20); empty query returns empty results |
| GET | \`/featured?category=&limit=\` | Curated featured shows per category (mapped search terms; 1-hour in-memory cache) |
| GET | \`/show?url=&itunes_id=\` | Full show metadata + episode list from the live RSS feed (15-min feed cache; episodes carry on-disk \`downloaded\` flags) |
| POST | \`/download\` | Queue a background episode download (requires \`enclosure_url\`; refused when the profile download gate denies) |
| GET | \`/downloads\` | All tracked podcast downloads, newest-started first (queued/downloading/completed/error/cancelled) |
| POST | \`/downloads/cancel-queued\` | Cancel every still-queued podcast download |
| GET | \`/audio-proxy?url=\` | Streaming proxy for enclosure URLs with range-request forwarding; SSRF-guarded, max 5 redirect hops |
| GET | \`/watchlist?profile_id=\` | Watchlisted podcasts for the acting profile |
| POST | \`/watchlist/check\` | Is a show (feed_url/itunes_id) watchlisted? |
| POST | \`/watchlist/add\` | Add/update a show (feed_url + title required; defaults auto_download=True, retention_days=14) |
| POST | \`/watchlist/remove\` | Remove a show from the watchlist |
| POST | \`/watchlist/settings\` | Update \`auto_download\` / \`retention_days\` for a show (feed_url required) |
| POST | \`/watchlist/scan-now\` | Immediately run the podcast scan + auto-download for the acting profile |
| GET | \`/watchlist/scan-status\` | Last scan's stats (time, checked, queued, pruned, errors, in-progress) |
| POST | \`/opml/import\` | OPML import: \`?action=preview\` parses the uploaded \`.opml\`/\`.xml\` (5 MB max, 413 above); \`?action=subscribe\` adds the given shows to the watchlist |
| GET | \`/opml/export?profile_id=\` | Download watchlisted podcasts as OPML 2.0 (\`soulsync-podcasts.opml\`) |
`
        },
    ]
});

// Audiobooks: Overview — the Audible/Apple catalogue side, what "owned" means.
registerDocsSection({
    id: 'audiobooks',
    title: 'Audiobooks',
    icon: '📚',
    pages: [
        {
            id: 'abk-overview',
            title: 'Audiobooks: Overview',
            lede: 'An Audible-class audiobook manager: browse, wishlist, grab releases, and keep a real library.',
            body: `
SoulSync's **Audiobooks** side is a full audiobook manager built around the Audible catalogue (with Apple as a fallback source). Browse bestsellers and genres, wishlist titles, and let the pipeline search your indexers — torrents and Usenet via Prowlarr plus Soulseek — rank every release, download the best one, and file it into your library.

The model mirrors the music side but adapted to books:

::: cards
### 📚 Catalogue Browsing
Audible search, genres, series in reading order, author and narrator pages, ratings, and per-title audio samples.
### 💛 Wishlist
Heart any title; the **Auto-Process Audiobook Wishlist** automation searches indexers hourly (each title backs off 6 hours between attempts).
### 👀 Author Watchlist
Follow authors — new releases are auto-wishlisted (or just counted), with per-author narrator and date controls.
### 📥 Releases & Ranking
The releases modal streams live indexer results, ranks them (right book, right narrator, format, completeness), and grabs your pick.
### 🗄️ Real Library
Scan your audiobook folder, auto-match files to catalogue editions, review ambiguous matches, recycle or restore deletes.
:::

> [!NOTE]
> Audiobooks keep their own settings (folder, path template, quality, download sources) separate from music on purpose — including a separate download-source chain so audiobook searches never pollute music searches.
`
        },
        {
            id: 'abk-browse',
            title: 'Browsing, Search & Genres',
            lede: 'The audiobook hub: home shelves, genre views, search modes, and the sample player.',
            body: `
The **Audiobooks** hub (\`/audiobooks\`) has three views driven by the URL, so the Back button from a detail page preserves exactly where you were:

- **Home** (no params): hero billboard + shelves.
- **Genre** (\`genre=<name>\` — the genre *name*, not id, because Audible's ids differ per storefront): heading plus **Top sellers** and **New releases** rails.
- **Search** (\`q=<query>&type=<keywords|title|author|narrator>\`): result header and grid.

## Home view

The home page loads in **one round trip** (shelves fail soft individually):

- **Hero billboard** — "Top seller" eyebrow, cover-as-backdrop, title/subtitle, **By** / **Narrated by** credits, star rating with average and count, facts (runtime, **Unabridged**, year), short summary, and **Play sample** / **View details** buttons.
- **Rails** — horizontal shelves with arrows; each has a **See all** link jumping to the genre view.

## Search

The search bar placeholder reads **"Search audiobooks, authors, narrators…"** with a **Search** / **Searching…** submit button and segmented modes: **All**, **Title**, **Author**, **Narrator**. Pressing **/** anywhere focuses it.

Results show **N results for 'query'**. A **People** row above the grid lifts authors/narrators out of the results (only people with more than one title, max 6), each with its own watch badge. If Apple answered instead of Audible, a notice reads: **"Audible had no match — showing Apple results, which carry less detail."** Empty results: **"Nothing matched"**.

Apple-sourced results carry an Apple collection id rather than an ASIN, so they **can't be wishlisted**.

## Navigation & cards

The browse header has three links:

- **Wishlist** (heart, count badge) → the shared wishlist's audiobooks half.
- **Library** → \`/audiobooks/library\`.
- **Bin** (title: "Deleted books you can still restore, and releases that will never be grabbed") → the **Removed & refused** review modal with **🗑 Recycle bin (N)** and **⛔ Blocklist (N)** panes.

A scrollable **genre pills** nav (with edge arrows) starts with **For you** then the Audible category tree.

Cards show the cover (click → detail page), an **Owned** badge when the title is in your library, a runtime badge, a heart wishlist icon, a hover **play-sample** button, title/author/**Narrated by** narrator, an inline star rating, and a series line (**Book 2.5 · Series**).

## Sample player

Every Audible title carries a preview. A persistent player bar in the layout streams it via \`/api/audiobooks/sample-proxy?url=\` (a server-side CORS/Range proxy) and **survives navigation** between browse and detail pages.

> [!TIP]
> **Owned** is computed server-side on every catalogue payload and refreshed by the daily library scan — a title you already own is badged **Owned** on cards and **In your library** on its detail page, so you never grab it twice.
`
        },
        {
            id: 'abk-detail',
            title: 'Book Detail Page',
            lede: 'Everything about one audiobook: metadata, series order, ratings, releases, and samples.',
            body: `
The detail page (\`/audiobooks/$asin\`) opens on a blurred-cover backdrop with a back button.

## Header

- Large cover with a full-width **Add to wishlist** / **Wishlisted** button, a **Find releases** button (Audible titles), and **Play sample** / **Pause sample** when a sample exists.
- Breadcrumb of up to 3 genres; an **In your library** marker when owned.
- Title + subtitle; series line (**Book 2.5 of <Series>**); **Written by** / **Narrated by** link rows leading to the author/narrator pages.
- Facts: **Runtime**, **Format**, **Released**, **Publisher**, **Language**.
- Expandable summary (**Show more** / **Show less** past 420 characters) and a rating breakdown (stars, count, distribution histogram).

## Series strip

**<Series>** — **N books in reading order**, ordered by printed sequence (2.5 sorts between 2 and 3); the current book is tagged **You're here**.

## Discovery rails

**More by {author}**, **More narrated by {narrator}**, and **Listeners also enjoyed** (Audible's own similar-titles).

## Find releases

**Find releases** opens the releases modal — the heart of audiobook acquisition:

::: steps
1. The modal starts a streaming search (\`POST releases/<asin>/start\`) and polls for results (\`GET releases/poll\`, ~1.2 s cadence), re-ranking the whole pool as results arrive. Stage text reads **Searching your indexers…**.
2. Each row shows a format tag, an **Abridged** tag where applicable, the protocol (**Soulseek** / **torrent** / **usenet**), the indexer or peer username, file count (Soulseek), size, **~N kbps** with a quality band, **N slots free** / **N seeders**, a **★ Best from {source}** marker on each source's winner, and **⚠ {short_warning}** when a release's named bitrate can't hold the whole book ("The reasons below show the arithmetic").
3. **▸ What's inside** decodes the \`.torrent\`/NZB in memory (**N audio files · FORMATS · N extras** plus a file list) so you can inspect before grabbing. Soulseek releases need no fetch.
4. **Block** refuses a single release (never the book). Per-row **Download** grabs it.
:::

After grabbing, the row becomes live status — **Queued**, **Downloading**, **Held back**, **In your library**, **Failed**, **Cancelled** (with the completeness reason when staged, or the error) — plus a **View downloads** link. When nothing is found: **"Nothing found … Add it to your wishlist and it will keep looking on its own."** with an **Add to wishlist** button.
`
        },
        {
            id: 'abk-library',
            title: 'Your Library',
            lede: 'Scan your audiobook folder, match files to catalogue editions, and manage what you own.',
            body: `
**Library** (\`/audiobooks/library\`) is a full page — "a library is a place, not a dialog." Its header reads **Audiobooks / On your shelves** and **Your library**, with **N books · X GB on disk**.

## Scan panel

The scan panel reports the folder state honestly:

- Status line: **Starting scan…**, **Scanning folder · N books checked**, **Matching catalogue · N checked**, **Last scanned …**, **This folder has not been scanned yet**, or **Scan needs attention**.
- The library root path — or **"Set your audiobook folder in Settings → Library"** when unset.
- Last-scan counts (**N added · N refreshed · N missing**).
- **Scan folder** / **Scanning…** button, which runs the **Auto-Scan Audiobook Library** automation's Run Now. If the automation is missing, errors tell you to **"Add Scan Audiobook Library on the Automations page, then try again."**
- A **Schedule & history ↗** link into the Automations page.
- Notices for match-pending and match-error counts.

Scanning **indexes without moving or touching your files**. It finds books ≥ 1 MB with audio files — including books you added outside SoulSync — matches them to catalogue editions (ASIN from sidecars when present, otherwise conservative title/author/narrator/runtime scoring), and flags changed files for review. Empty state: **"Your books belong here … Scan your audiobook folder to find the books you already own, including those added outside SoulSync. Your files stay where they are."** with a **Scan my folder** button.

## Browsing the shelves

- Search: **"Search title, author, narrator or series…"**
- Sort: **Title A–Z**, **Author A–Z**, **Recently added**, **Largest first**.
- Filters: **All books**, **Needs review**, **Matched** (count buttons) plus origin select: **All origins**, **SoulSync downloads**, **Found on disk**, **Origin unknown**.

Book cards show the cover (clickable through to the detail page only once catalogue-matched), a format badge, title/author/series facts (runtime · size), an origin badge (**SoulSync download** / **Found on disk** / **Origin unknown**), and a match-status badge: **Identified by ASIN**, **Auto-matched**, **Confirmed by you**, **Unmatched**, **Review matches**, **Kept unmatched**, **Files changed · review**, or **Match lookup failed**.

A **File details** expander reveals file count, narrator, disk path, grouping, an expandable file list, and — for SoulSync downloads — a **Downloaded by SoulSync** block (release title, indexer, date). From here you can **Change catalogue match** or **Delete from disk** (two-step: **"Delete this book from disk? Your recycle bin settings apply."** → **Confirm delete** / **Keep book**; success reads **"{Title}" moved to the recycle bin.** or **"{Title}" was deleted.**).

## Matching a book to the catalogue

**Match this recording** shows your local evidence (author, narrator, runtime, file count, grouping/path, saved catalogue edition, match evidence lines) and warns: **"Compare the narrator, runtime and edition before confirming."**

Type a title — or paste an Audible ASIN — and hit **Find editions**. Candidates show **Match score N/100 · ASIN** with evidence lines and conflicts; each has **Use this edition**. Bottom actions: **Keep unmatched** and **Queue automatic matching** ("Queued books are checked on the next scan. Keeping a book unmatched prevents automatic matching until you change it.").

Server-side, automatic matching requires score **≥ 96 with an 8-point margin**; anything ambiguous becomes a suggestion (**Review matches**) instead of auto-matching.
`
        },
        {
            id: 'abk-wishlist',
            title: 'Wishlist',
            lede: 'One shared wishlist page — the audiobooks half, the worker, and per-title controls.',
            body: `
There is no separate audiobooks wishlist tab: \`/audiobooks/wishlist\` redirects to \`/wishlist?media=audiobooks\` — one shared wishlist page for all media, with an audiobooks half.

## Adding titles

- The **heart** on any Audible catalogue card (one click, optimistic UI).
- The full **Add to wishlist** button on the detail page; the empty releases-modal state has one too.
- Adding defaults the narrator mode to **exact**; re-adding is idempotent and never rewrites your choice.
- The server resolves metadata by ASIN itself — a row can't be stored from a browser-assembled payload.

## The wishlist page

The hero shows an **Automation Active** pill with the automation name (**Auto-Process Audiobook Wishlist**) and the description: **"Searches indexers on a schedule — each title waits N hours between attempts to prevent rate-limiting. Customise frequency anytime on the Automations page."** Stats: **Total / Looking / In Flight / In Library**, plus a **⚡ Search now** button.

Filters: **All**, **Looking**, **In progress**, **In library**, **Not found yet**, **Cancelled** (with counts); a text filter (**"Filter by title, author, narrator, or series…"**); and a view switch (**⊞ Grid** / **☰ Shelf**, persisted in localStorage).

Per-row states: **Looking**, **Searching now**, **Queued**/download sub-status (**Downloading**, **Queued**, **Paused**, **Waiting for client**, **Checking files**, **Importing**, **Cancelled**), **In your library**, **Not found yet**, **Cancelled**. Each row shows its trail — **"Not looked for yet"** or **"Looked N× · last … ago"** plus the last error — and a series badge (**Book N of {series}**).

Per-row actions: **Search** (targeted search-and-grab now), the **Narrator locked** / **Any narrator** toggle with a 🎙️ narrator pill (exact shows 🔒), **Look again** (failed/cancelled rows — resets to wanted without losing your narrator choice), **Find releases** (the releases modal), **Remove**.

## The worker

The **Auto-Process Audiobook Wishlist** automation runs the worker **hourly** (the schedule belongs to the automation engine, not the worker module):

- **5 books per profile per pass**; each title waits **6 hours between attempts** (\`audiobooks.wishlist_retry_after_seconds\`); all searches share the Prowlarr throttle.
- Each pass **self-heals**: stale "searching" and stale "grabbed" rows are reset; rows already owned in the library flip to done.
- Per book: claim **searching**, search the top 10 honouring the row's narrator mode, grab the best release, mark **grabbed**. Failures ("No releases found", grab errors) count an attempt. Already-owned books flip to done without searching.
- **⚡ Search now** (\`POST audiobooks/wishlist/search\`, force on by default) runs the same pass as the timer. Per-book **Search** is a single-book search-and-grab.
- Retrying: setting a row back to **wanted** is the way back from cancelled (cancelled rows are never retried on their own). Changing **narrator_mode** (exact ↔ any) adjusts strictness **without** resetting the backoff.
`
        },
        {
            id: 'abk-people',
            title: 'Authors, Narrators & Watchlist',
            lede: 'Follow authors, tune per-author settings, and let the daily scan wishlist new releases.',
            body: `
## Author & narrator pages

Author (\`/audiobooks/author/$name\`) and narrator (\`/audiobooks/narrator/$name\`) pages are keyed on **name**, not ASIN — Audible's author ASIN is accepted as a filter then ignored (it returns the whole storefront anyway), and narrators have no ASIN at all.

- Header: blurred covers as backdrop, a fan of up to 3 best-rated covers (the catalogue has no author photos), an **Author** / **Narrator** role label, name, and facts: **N titles**, **N series**, **N hours**, plus genres.
- **Collaborators:** author pages show the top **Narrated by** narrators; narrator pages show the top **Writes with** authors — with counts, linking to each other's pages.
- **Works:** jump-to-series pills (when there's more than one series), one rail per series in reading order, then a **Standalone** / **All titles** grid, plus a **Highest rated** rail.
- Not found: **"The catalogue has no audiobooks credited {name} as {role}."**

Only **author** pages carry a watchlist button — 👁️ **Add to Watchlist** / **Watching** ("Add to Watchlist — new releases are wishlisted automatically"). Narrator pages have no follow button: a narrator has no release of their own.

## Following an author

You can follow from the author page, or from the **Watch** / **Watching** badge on person tiles in search results (top-right corner). Following asks the narrator question up front (defaults **exact**) and sets the cutoff to **the day you followed** — "following an author means 'tell me about the next one', not 'queue the eighty-eight they already wrote'." A \`since\` date can be passed to backfill older titles.

## The watchlist tab

On the shared watchlist page, the audiobooks tab shows one card per followed author (click → author page):

- **Watching since {date}**; badges **⚡ Auto-wishlist** (or **👁️ Monitored** when auto-wishlist is off), **🎙️ Any narrator** / **🎙️ Exact narrator**, **N found**; footer **Checked X ago** or **Last check failed: {error}**.
- Header note: **"Checked once a day. Only books published after you followed an author are picked up."** with a **Check now** button (**Checking…** → **"Wishlisted N new releases."** / **"No new releases since the last look."**).
- Card menu (•••): **⚙️ Watchlist Settings**, **🗑️ Remove from Watchlist**.

## Per-author settings

**Author Watchlist Settings** (the "per-author card" the Quality settings hint refers to):

- **New Release Preferences** ("New Releases") — "What to do when this author publishes something you do not already have." The **Add new releases to the wishlist** checkbox; when off, "their new books are still found and counted, just not queued for download."
- **Narrator** — "Answered here because nobody sees an auto-wishlisted book before it is queued. A book is always one narrator, never a mixture." Two buttons: **Only the credited narrator** ("A release naming a different narrator is rejected. Releases that name nobody are always allowed, because most do not say.") vs **Any narrator** (still accepted, just ranked lower).
- **Watching Since** — **Published after:** date input. "Only books published after this date are picked up. Move it back to pull in titles you missed…"
- **Danger Zone** — **Remove from Watchlist** (unfollows; downloads and wishlist untouched).
- Footer: **Cancel** / **Save Changes**.

## The author scan

The **Auto-Scan Audiobook Authors** system automation runs **daily** (audiobooks are announced weeks ahead and published on a date — checking more often "spends effort to learn nothing"). It checks each due author's newest 20 titles, newest-first, and wishlists only books newer than the row's \`since_date\` that aren't already wanted or owned. With **auto_wishlist off** it's "tell me, do not fetch": new books are counted and reported, not queued. Auto-wishlisted books inherit the row's narrator mode. Books with no release date are never treated as new.
`
        },
        {
            id: 'abk-downloads',
            title: 'Releases, Ranking & the Download Pipeline',
            lede: 'How releases are found, ranked, grabbed, verified complete, and filed into your library.',
            body: `
## Sources

Audiobooks search **three sources**, deliberately separate from the music chain so audiobook queries never pollute music searches:

| Source | Via | Notes |
|--------|-----|-------|
| Torrent | Prowlarr | Newznab category **3030**, same client/adapters/config/throttle as music |
| Usenet | Prowlarr | Same category **3030** |
| Soulseek | slskd | Every file in the peer's folder starts as one book |

The search fans out to whichever sources your chain names (up to three query variants in series, then Soulseek), then **ranks everything on one scale**.

## Ranking

Releases are judged mostly on being the *right book*:

- Title tokens, series + sequence match.
- **Narrator verdict** — exact vs any, per the wishlist row or follow setting.
- **Abridgement verdict** — abridged vs unabridged (Audible sells them as separate ASINs).
- Language, format preference, dramatised detection (demoted, never silently preferred).
- **Completeness plausibility:** implied kbps = size ÷ catalogue runtime, in quality bands (thin / standard / good / generous / oversized). A **short_warning** fires when a release's named bitrate can't hold the whole book. Rejected releases always show the reason.

## Grab

Grabbing sends the chosen release to the download client (torrent/Usenet share the music adapters, tagged with the audiobook category; the same **Minimum free disk space (GB)** floor as music applies). Soulseek starts every file in the peer's folder as one book. Grabbing is **refused when the book is already owned** unless forced, and marks the wishlist row **grabbed** only for existing rows. Manual grabs honour your profile's download switch. Each grab writes two records: the audiobook download history and the shared runtime state, so it appears on the Downloads page.

## Monitor

A monitor polls roughly every **20 seconds** and watches grabbed downloads to completion — **seeding counts as complete**. A download unknown to the client for 8 polls running is failed and handed back to the wishlist.

## Completeness: staged, not failed

Audible's published runtime is compared against the measured audio duration (ffprobe):

- **Import When Runtime Reaches:** (default **92**) — the tolerance. Books coming up short are **staged, not failed**: **Held back** with the reason shown (e.g. part 1 of 5) until the staging deadline.
- **Give Up On A Short Book After:** (default **7** days) — the staging deadline, after which the book fails back to the wishlist.
- Overshoot beyond **1.6×** is rejected as the wrong folder.

## Organization & post-processing

Finished downloads are **copied** (never moved — source files are never deleted) into the library under your path template, one release per folder (enforced by a hidden \`.soulsync-release\` marker, so a retry can't mix a second narrator's chapters in). Even a single M4B gets its own folder. Chapter files are naturally sorted and optionally renumbered.

Post-processing is best-effort (mutagen): narrator → COMPOSER tag, embedded cover, \`cover.jpg\` beside the files, and \`metadata.opf\` + \`book.nfo\` sidecars carrying the ASIN so the library scan re-recognises imported books. Each of these is individually toggleable (see [Audiobook Settings](#abk-settings)).
`
        },
        {
            id: 'abk-bin',
            title: 'Recycle Bin & Blocklist',
            lede: 'Soft deletes with restore, expiring bins, and per-release blocks.',
            body: `
## Recycle bin

Deleting a book from the library doesn't nuke it — with **Recycle deleted audiobooks** on, the folder moves to a hidden \`.deleted\` folder inside the audiobook library, timestamped and manifest-backed. **Restore** puts the book back exactly where it was.

- **Empty The Recycle Bin After:** (default **7** days) — bin entries expire automatically; **0 turns the bin off** (deletes are permanent).
- The **Empty Audiobook Recycle Bin** system automation runs **daily** — the schedule is what matters, because the opportunistic pass only fires when something else is deleted.

The **Bin** button in the browse header opens the **Removed & refused** review modal with two panes:

- **🗑 Recycle bin (N)** — restore or purge individual books, or empty the whole bin (purge/empty erase immediately).
- **⛔ Blocklist (N)** — releases you've refused.

## Blocklist

**Block** on a release row refuses that *release*, never the book — the title stays wanted and other releases can still match. Unblock individual releases or clear the whole list from the review modal.
`
        },
        {
            id: 'abk-settings',
            title: 'Audiobook Settings',
            lede: 'Quality, folders, path templates, download sources, and import behaviour — every control.',
            body: `
## Audiobook Quality

"What counts as a good file. One profile for every audiobook — the per-author card carries the settings that differ by author." (The per-author card = the Author Watchlist Settings modal: auto-wishlist on/off, narrator mode, since date.)

| Control (exact label) | Options / behaviour |
|----------------------|---------------------|
| **Preferred Format:** | **M4B (recommended)**, M4A, MP3, Opus, OGG, FLAC. "Ranked first when several releases are otherwise equal… Format never outweighs having the right book and the right narrator." |
| **Reject Below:** | kbps number input. "Hides releases below this bitrate. 0 means no opinion… Audible's own files are 32 kbps mono and 64 kbps stereo, so 32 is a reasonable floor and anything above 128 is more than speech needs." |
| **Reject Above:** | kbps number input. "Hides releases above this bitrate… 0 means no opinion." |
| **Show dramatised adaptations** | Checkbox. "GraphicAudio and similar full-cast productions… Left on they are shown but ranked below real readings and clearly labelled; turned off they are hidden entirely." |

Backend defaults: format order m4b → m4a → mp3 → opus → ogg → flac; min/max 0 = no opinion; dramatised allowed. Rejections are always explained in plain terms on the release row.

## Folders & naming

| Control (exact label) | Notes |
|----------------------|-------|
| **Audiobooks Folder (output):** | "Where downloaded audiobooks are saved and organized. Keep this separate from your music library so a media server does not index chapter files as albums." |
| **Audiobook Path Template:** | Variables \`$author\`, \`$authorletter\`, \`$narrator\`, \`$title\`, \`$series\`, \`$seriespos\` (01), \`$year\`, \`$asin\`. Default \`$author/$series/$seriespos - $title\`; series-less books collapse the folder cleanly. |

## Download sources

The **Audiobooks** subsection carries its own chain, separate from music on purpose:

- **Audiobook Download Sources:** — **Soulseek Only**, **Torrent Only (via Prowlarr)**, **Usenet Only (via Prowlarr)**, **Hybrid (Primary + Fallback)**.
- **Audiobook Downloader Category:** (default \`audiobooks\`).
- **Audiobook Prowlarr Categories:** (default \`3030\`, deliberately excluded from music searches).

## Import & file handling

| Control (exact label) | Default |
|----------------------|---------|
| **Import When Runtime Reaches:** (%) | 92 — completeness tolerance before a book is staged |
| **Give Up On A Short Book After:** (days) | 7 — staging deadline before failing back to the wishlist |
| **Renumber chapter files on import** | On |
| **Write tags into audiobook files** | On (narrator → COMPOSER field) |
| **Embed cover art in audiobook files** | On |
| **Save cover.jpg beside audiobooks** | On |
| **Write audiobook metadata sidecars** | On (\`metadata.opf\` + \`book.nfo\`, carrying the ASIN) |
| **Recycle deleted audiobooks** | On |
| **Empty The Recycle Bin After:** (days) | 7 (0 = bin off) |
`
        },
        {
            id: 'abk-automations',
            title: 'Audiobook Automations',
            lede: 'The four system automations that run the audiobook side: wishlist, authors, bin, library.',
            body: `
Audiobooks drain through the automation engine like every other side. Four system automations (no owned_by — audio-side automations sit on the same page as music):

| Automation | Schedule | What it does |
|-----------|----------|--------------|
| **Auto-Process Audiobook Wishlist** | Every **1 hour** (first run 4 min after startup) | Runs the wishlist worker: 5 books per profile per pass, 6-hour per-title backoff, self-healing stale rows. Hourly rather than music's 30 minutes — each book's own multi-hour backoff means a shorter interval would only re-walk rows not due yet. |
| **Auto-Scan Audiobook Authors** | Every **24 hours** (first run 7 min after startup) | Checks each followed author's newest 20 titles, newest-first; wishlists books newer than the follow date. |
| **Empty Audiobook Recycle Bin** | Every **24 hours** (first run 13 min after startup) | Expires bin entries past **Empty The Recycle Bin After**. |
| **Auto-Scan Audiobook Library** | Every **24 hours** (first run 10 min after startup, last of the audio jobs) | Indexes the audiobook folder: new books, refreshed metadata, missing files, catalogue matching. |

All four are schedule-triggered system automations: pausable, reschedulable, and obedient to the automation master switch. Tune them on the **Automations** page like any other — the wishlist hero's "Customise frequency anytime on the Automations page" points here.
`
        },
        {
            id: 'abk-api',
            title: 'Audiobook API Reference',
            lede: 'Every audiobook endpoint: catalogue, wishlist, watchlist, releases, library, and downloads.',
            body: `
Base path: \`/api/audiobooks\`.

## Catalogue

| Method | Path | Purpose |
|--------|------|---------|
| GET | \`/search\` | Audible search with Apple fallback (\`q\`, \`type\` = keywords/title/author/narrator, limit, marketplace, page, sort; reports which source answered) |
| GET | \`/book/<asin>\` | Full metadata for one ASIN (owned-flagged) |
| GET | \`/similar/<asin>\` | Audible's own recommendations |
| GET | \`/series\` | Series in reading order (\`name\`, optional \`asin\` for exact match) |
| GET | \`/author\` | Author bibliography |
| GET | \`/narrator\` | Narrator's performances |
| GET | \`/person\` | Grouped author/narrator profile — series, standalone, collaborators, highlights; \`watching\` only for authors; owned-flagged |
| GET | \`/browse\` | One shelf (\`category\` by name — \`category_id\` legacy accepted, \`sort\`, limit) |
| GET | \`/bestsellers\` | Top sellers, overall or in a genre |
| GET | \`/new-releases\` | Newest first, overall or in a genre |
| GET | \`/categories\` | Audible genre tree |
| GET | \`/home\` | Whole browse page in one round trip (concurrent shelves, deduped, hero = first bestseller; failing shelves come back empty, not 500) |
| GET | \`/sample-proxy\` | Proxied audio sample stream with Range support (host allowlist enforced) |

## Wishlist

| Method | Path | Purpose |
|--------|------|---------|
| GET | \`/wishlist\` | Items + counts + worker status for the profile |
| POST | \`/wishlist\` | Add by ASIN (metadata resolved server-side; \`narrator_mode\` exact/any; idempotent) |
| PATCH | \`/wishlist/<asin>\` | \`{"status":"wanted"}\` retries a cancelled/failed row; or change \`narrator_mode\` (no backoff reset) |
| DELETE | \`/wishlist\` | Clear the profile's wishlist |
| DELETE | \`/wishlist/<asin>\` | Remove one entry |
| POST | \`/wishlist/search\` | Run a wishlist pass now (force=true bypasses backoff) |
| POST | \`/wishlist/<asin>/search\` | Search + grab one book now |

## Watchlist (authors)

| Method | Path | Purpose |
|--------|------|---------|
| GET | \`/watchlist\` | Followed authors |
| POST | \`/watchlist\` | Follow an author (\`name\`, \`cover_url\`, \`since\` cutoff defaults to today) |
| PATCH | \`/watchlist/<name>\` | Per-author settings (\`auto_wishlist\`, \`narrator_mode\`, \`since_date\`) |
| DELETE | \`/watchlist/<name>\` | Unfollow |
| POST | \`/watchlist/scan\` | Check followed authors now (same path as the daily automation) |

## Releases & grabbing

| Method | Path | Purpose |
|--------|------|---------|
| GET | \`/releases/<asin>\` | Synchronous indexed release search (honours the book's narrator mode) |
| POST | \`/releases/<asin>/start\` | Start a streaming search job (returns id + \`poll_ms: 1200\`) |
| GET | \`/releases/poll\` | Whole ranked pool so far (\`id\` param); 404 \`{expired: true}\` when gone |
| DELETE | \`/releases/poll\` | Drop a search job |
| POST | \`/releases/contents\` | What's inside one release (decodes .torrent/NZB in memory; Soulseek needs no fetch) |
| POST | \`/grab\` | Send a chosen release to the download client (refuses already-owned unless forced; wakes the monitor) |
| GET | \`/downloads\` | Audiobook downloads, in flight or finished (+ monitor status); \`?active=1\` filter |

## Library, recycle & blocklist

| Method | Path | Purpose |
|--------|------|---------|
| GET | \`/library\` | Indexed books + total bytes + root + scan status |
| GET | \`/library/<asin>/matches\` | Match candidates for a book (\`q\` search or exact ASIN) |
| PATCH | \`/library/<asin>/match\` | Confirm / ignore / retry a match (signature + revision guarded) |
| GET | \`/library/<asin>/cover\` | Local cover (file, sidecar, or embedded) |
| DELETE | \`/library/<asin>\` | Delete book → recycle bin; removes the library record |
| GET | \`/library/recycle\` | Bin entries + keep_days |
| POST | \`/library/recycle/<name>\` | Restore one book |
| DELETE | \`/library/recycle/<name>\` | Purge one book |
| DELETE | \`/library/recycle\` | Empty the bin |
| GET | \`/blocklist\` | Blocked releases |
| POST | \`/blocklist\` | Block one release (never the book) |
| DELETE | \`/blocklist/<key>\` | Unblock |
| DELETE | \`/blocklist\` | Clear the blocklist |

> [!NOTE]
> \`GET /bestsellers\` and \`GET /new-releases\` exist in the API but no current UI calls them (the UI uses \`/browse\` and \`/home\`); the synchronous \`GET /releases/<asin>\` exists but the releases modal uses the streaming start/poll path. Narrators cannot be followed — both UI and API restrict follows to authors.
`
        },
    ]
});
