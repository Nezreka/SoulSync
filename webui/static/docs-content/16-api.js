/* SoulSync docs content: Public REST API (api) */
(function () {
    'use strict';

    function register(section) {
        if (typeof registerDocsSection === 'function') {
            registerDocsSection(section);
        }
    }

    register({
        id: 'api',
        title: 'API Reference',
        icon: '🔌',
        pages: [
        {
            id: 'api-auth',
            title: 'Authentication',
            lede: 'One key unlocks the whole public API. Here is how to mint it, send it, and keep it safe.',
            body: `
Every public endpoint lives under \`/api/v1\` and expects an API key. Send it as a bearer token (preferred) or as a query parameter:

\`\`\`bash
curl -H "Authorization: Bearer sk_..." http://localhost:8008/api/v1/system/status
curl "http://localhost:8008/api/v1/system/status?api_key=sk_..."
\`\`\`

## Keys

Keys start with \`sk_\` and are minted by an admin. The raw key is shown **exactly once** — only a SHA-256 hash is stored, so a leaked database cannot be turned back into working keys. Each key records a label, a visible prefix (\`sk_\` plus the first 8 characters), creation time, and last-used time.

> [!IMPORTANT]
> An API key acts with **admin rights**. Treat it like a password: store it in a secrets manager, never commit it to git, and rotate it if it ever leaks.

## Bootstrap the first key

A fresh install has no keys yet, so one endpoint works without auth — but only while zero keys exist:

| Method | Endpoint | Purpose |
|--------|----------|---------|
| POST | \`/api-keys/bootstrap\` | Mint the first key (no auth, only when no keys exist) |

\`\`\`bash
curl -X POST http://localhost:8008/api/v1/api-keys/bootstrap \\
  -H "Content-Type: application/json" \\
  -d '{"label": "My First Key"}'
\`\`\`

Once a key exists, \`bootstrap\` answers \`403\` — create further keys with an authenticated \`POST /api-keys\`.

## Response envelope

Every response uses the same shape, success or failure:

\`\`\`json
{
  "success": true,
  "data": { "...": "..." },
  "error": null,
  "pagination": { "page": 1, "limit": 50, "total": 128, "total_pages": 3, "has_next": true, "has_prev": false }
}
\`\`\`

Errors look like \`{"success": false, "data": null, "error": {"code": "INVALID_KEY", "message": "..."}, "pagination": null}\`. \`pagination\` is only populated on endpoints that are paginated (each section below says which ones are) — everywhere else it is \`null\`. Paginated endpoints accept \`page\` and \`limit\` query parameters.

## Field selection & profile scoping

Two cross-cutting parameters work on many endpoints:

- \`?fields=id,name,thumb_url\` trims returned objects to just the comma-separated fields you name.
- **Profile scoping:** send an \`X-Profile-Id\` header (or \`?profile_id=\` query parameter) to scope per-profile endpoints (watchlist, wishlist, …) to that profile. It defaults to profile 1 when omitted.

## Limits & errors

| Behavior | Detail |
|----------|--------|
| Rate limit | 60 requests/minute per IP across the API |
| Rate limited | \`429\` with code \`RATE_LIMITED\` |
| Missing key | \`401\` with code \`AUTH_REQUIRED\` |
| Wrong key | \`403\` with code \`INVALID_KEY\` |
`
        },
        {
            id: 'api-system',
            title: 'System',
            lede: 'Health, statistics, and the live activity feed — the fastest way to check the server is alive.',
            body: `
| Method | Endpoint | Purpose |
|--------|----------|---------|
| GET | \`/system/status\` | Health check: server uptime and service connectivity |
| GET | \`/system/stats\` | Library and worker statistics |
| GET | \`/system/activity\` | Recent activity feed entries |

\`/system/status\` is the one to point your uptime monitor at — it is cheap and answers whether the web server is healthy, with uptime and service connectivity flags. \`/system/stats\` backs the dashboard numbers, and \`/system/activity\` returns the same feed you see in the UI, newest first.
`
        },
        {
            id: 'api-library',
            title: 'Library',
            lede: 'Read-only access to everything SoulSync knows about your music: artists, albums, tracks, and genres.',
            body: `
| Method | Endpoint | Purpose |
|--------|----------|---------|
| GET | \`/library/artists\` | List artists (paginated) |
| GET | \`/library/artists/{artist_id}\` | One artist |
| GET | \`/library/artists/{artist_id}/albums\` | Albums by an artist |
| GET | \`/library/albums\` | List albums (paginated) |
| GET | \`/library/albums/{album_id}\` | One album |
| GET | \`/library/albums/{album_id}/tracks\` | Tracks on an album |
| GET | \`/library/tracks\` | Search tracks — needs \`title\` or \`artist\` (not paginated) |
| GET | \`/library/tracks/{track_id}\` | One track |
| GET | \`/library/genres\` | Genre list with counts |
| GET | \`/library/recently-added\` | Newest additions first |
| GET | \`/library/lookup\` | Resolve an artist/album/track by external ID |
| GET | \`/library/stats\` | Library totals |

Only some list endpoints are paginated: \`/library/artists\`, \`/library/artists/{artist_id}/albums\`, and \`/library/albums\` accept \`page\` and \`limit\` and return the standard \`pagination\` object. \`/library/tracks\` is a **search**, not a list — it requires a \`title\` or \`artist\` query parameter (both together narrow the match) and answers \`400\` without one; it takes \`limit\` (default 50, max 200) but is not paginated. \`/library/recently-added\` and \`/library/genres\` take an optional \`limit\` and are not paginated either. Use \`/library/lookup\` when you have a MusicBrainz or provider ID from somewhere else and need the matching SoulSync record.
`
        },
        {
            id: 'api-search',
            title: 'Search',
            lede: 'Provider search for tracks, albums, and artists — the same results the Add pages show.',
            body: `
| Method | Endpoint | Purpose |
|--------|----------|---------|
| POST | \`/search/tracks\` | Search providers for tracks |
| POST | \`/search/albums\` | Search providers for albums |
| POST | \`/search/artists\` | Search providers for artists |

Send a JSON body with your query, for example \`{"query": "kind of blue", "limit": 10}\`. Results mirror what the UI search pages return, including provider metadata and match quality — handy for building your own request or curation tooling on top of SoulSync.
`
        },
        {
            id: 'api-downloads',
            title: 'Downloads',
            lede: 'See what is downloading right now, and stop anything that should not be.',
            body: `
| Method | Endpoint | Purpose |
|--------|----------|---------|
| GET | \`/downloads\` | Active and queued downloads with progress |
| POST | \`/downloads/{download_id}/cancel\` | Cancel one download |
| POST | \`/downloads/cancel-all\` | Cancel everything in the queue |

\`GET /downloads\` returns each download with its state and progress percentage — poll it if you are building a status display. For push updates instead of polling, subscribe to the \`downloads:batch_update\` real-time event (see [Real-time Events](#api-websocket)).
`
        },
        {
            id: 'api-playlists',
            title: 'Playlists',
            lede: 'List your playlists, inspect one, or trigger a sync on demand.',
            body: `
| Method | Endpoint | Purpose |
|--------|----------|---------|
| GET | \`/playlists\` | List playlists |
| GET | \`/playlists/{playlist_id}\` | One playlist with its tracks |
| POST | \`/playlists/{playlist_id}/sync\` | Trigger a sync now |

Syncing a playlist re-resolves its tracks against the current library and provider state — the same operation as the Sync button in the UI. The list endpoint reads your playlists live from the connected provider (\`?source=spotify|tidal\`, default \`spotify\`) and is not paginated.
`
        },
        {
            id: 'api-watchlist',
            title: 'Watchlist',
            lede: 'Manage the artists SoulSync is watching, and kick off a scan whenever you like.',
            body: `
| Method | Endpoint | Purpose |
|--------|----------|---------|
| GET | \`/watchlist\` | Watched artists for the current profile |
| POST | \`/watchlist\` | Add an artist to the watchlist |
| PATCH | \`/watchlist/{artist_id}\` | Update watch settings for an artist |
| DELETE | \`/watchlist/{artist_id}\` | Remove an artist from the watchlist |
| POST | \`/watchlist/scan\` | Trigger a watchlist scan now |

Adding to the watchlist takes \`artist_id\` **and** \`artist_name\` in the JSON body — both are required (missing either is a \`400\`). Optional: \`source\` to name the provider explicitly, and \`quality_profile_id\` to set the acquisition quality intent. The scan endpoint starts the same background scan the scheduler runs — useful after bulk changes or from an external trigger.
`
        },
        {
            id: 'api-wishlist',
            title: 'Wishlist',
            lede: 'The tracks you want but do not have yet — add, remove, and process them via the API.',
            body: `
| Method | Endpoint | Purpose |
|--------|----------|---------|
| GET | \`/wishlist\` | Wishlist entries (paginated) |
| POST | \`/wishlist\` | Add a track to the wishlist |
| DELETE | \`/wishlist/{track_id}\` | Remove a track from the wishlist |
| POST | \`/wishlist/process\` | Process the wishlist now |

\`POST /wishlist\` takes the track wrapped in a \`track_data\` object: \`{"track_data": {...}}\`. \`track_data\` is required (missing it is a \`400\`); \`quality_profile_id\` is optional and sets the acquisition quality intent for the item. Processing walks the wishlist and attempts to acquire entries that have become available — the same job the automation scheduler runs. Pair it with the \`wishlist:stats\` real-time event to watch counts change live.
`
        },
        {
            id: 'api-request',
            title: 'Requests',
            lede: 'Submit a download request and check on it later — the API behind the request flow.',
            body: `
| Method | Endpoint | Purpose |
|--------|----------|---------|
| POST | \`/request\` | Submit a new request |
| GET | \`/request/{request_id}\` | Check a request's status |

\`POST /request\` takes a free-text \`query\` in the JSON body — for example \`{"query": "Kind of Blue"}\` — and immediately returns \`202\` with a \`request_id\`. Request state is kept **in memory**: it tracks \`queued → searching → downloading → completed / not_found / failed\`, and entries expire over time, so don't treat \`request_id\` as durable. Poll \`GET /request/{request_id}\` (or watch download events) to follow it from submitted to fulfilled.
`
        },
        {
            id: 'api-discover',
            title: 'Discover',
            lede: 'Programmatic access to the discovery pool, similar artists, and new releases.',
            body: `
| Method | Endpoint | Purpose |
|--------|----------|---------|
| GET | \`/discover/pool\` | The current discovery pool |
| GET | \`/discover/pool/metadata\` | Metadata about pool entries |
| GET | \`/discover/similar-artists\` | Similar-artist recommendations |
| GET | \`/discover/recent-releases\` | Recently released albums |
| GET | \`/discover/bubbles\` | Discovery bubble snapshots |
| GET | \`/discover/bubbles/{snapshot_type}\` | One bubble snapshot |

These power the Discover pages in the UI. The pool endpoints accept filtering parameters — start a session in the UI to see which filters your setup supports, then replay them here.
`
        },
        {
            id: 'api-profiles',
            title: 'Profiles',
            lede: 'Create, read, update, and delete user profiles — full CRUD under /profiles.',
            body: `
| Method | Endpoint | Purpose |
|--------|----------|---------|
| GET | \`/profiles\` | List profiles |
| POST | \`/profiles\` | Create a profile |
| GET | \`/profiles/{profile_id}\` | One profile |
| PUT | \`/profiles/{profile_id}\` | Update a profile |
| DELETE | \`/profiles/{profile_id}\` | Delete a profile |

> [!WARNING]
> Profile management is destructive surface area. Deleting a profile removes its settings, watchlist scope, and history — there is no undo. Double-check \`profile_id\` before sending \`DELETE\`. Profile 1 (the default admin profile) cannot be deleted: \`DELETE\` answers \`403\`.

> [!NOTE]
> \`/profiles\` is not paginated — it returns the full list.
`
        },
        {
            id: 'api-settings',
            title: 'Settings & API Keys',
            lede: 'Read and update server settings, and manage API keys themselves.',
            body: `
| Method | Endpoint | Purpose |
|--------|----------|---------|
| GET | \`/settings\` | Current settings (sensitive values redacted) |
| PATCH | \`/settings\` | Update settings |
| GET | \`/api-keys\` | List API keys (prefixes and labels — never raw keys or hashes) |
| POST | \`/api-keys\` | Mint a new key (raw key returned once) |
| DELETE | \`/api-keys/{key_id}\` | Revoke a key |
| POST | \`/api-keys/bootstrap\` | First-key bootstrap (no auth, only when no keys exist) |

\`PATCH /settings\` accepts a partial settings object — only the keys you send are changed. Secrets in \`GET /settings\` come back redacted, so it is safe to log or display the response.

> [!WARNING]
> Revoking a key with \`DELETE /api-keys/{key_id}\` takes effect immediately. If it is the key your automation uses, that automation breaks on its next call — rotate first, then revoke.
`
        },
        {
            id: 'api-retag',
            title: 'Retag',
            lede: 'Inspect and manage retag groups — the batches behind library-wide tag fixes.',
            body: `
| Method | Endpoint | Purpose |
|--------|----------|---------|
| GET | \`/retag/groups\` | List retag groups |
| GET | \`/retag/groups/{group_id}\` | One retag group, with its tracks |
| DELETE | \`/retag/groups/{group_id}\` | Delete one retag group |
| DELETE | \`/retag/groups\` | Delete all retag groups |
| GET | \`/retag/stats\` | Retag statistics |

Retag groups collect files that need their metadata rewritten. List the groups, review what one caught, then delete it when its fixes are done — or clear everything at once. The API does not create or edit groups; they are produced by the retag worker in the app itself.
`
        },
        {
            id: 'api-cache',
            title: 'Cache',
            lede: 'Peek at (and clear) the metadata caches SoulSync keeps warm.',
            body: `
| Method | Endpoint | Purpose |
|--------|----------|---------|
| GET | \`/cache/musicbrainz\` | MusicBrainz cache entries |
| GET | \`/cache/musicbrainz/stats\` | MusicBrainz cache statistics |
| GET | \`/cache/discovery-matches\` | Discovery match cache entries |
| GET | \`/cache/discovery-matches/stats\` | Discovery match cache statistics |

These are primarily diagnostic: check hit rates when metadata feels slow, or confirm a stale match is actually cached before you go hunting elsewhere.
`
        },
        {
            id: 'api-listenbrainz',
            title: 'ListenBrainz',
            lede: 'Read the ListenBrainz playlists SoulSync has imported or generated.',
            body: `
| Method | Endpoint | Purpose |
|--------|----------|---------|
| GET | \`/listenbrainz/playlists\` | List ListenBrainz playlists |
| GET | \`/listenbrainz/playlists/{playlist_id}\` | One playlist with tracks |

Use these to feed ListenBrainz-derived playlists into external players or dashboards without going through the UI.
`
        },
        {
            id: 'api-metasync',
            title: 'MetaSync Export',
            lede: 'A read-only, cursor-paged walk of the library\u2019s resolved metadata, built for the MetaSync sidecar.',
            body: `
| Method | Endpoint | Purpose |
|--------|----------|---------|
| GET | \`/metasync/export\` | Export resolved metadata for artists, albums, or tracks |

\`entity\` is required and must be one of \`artist\`, \`album\`, or \`track\`. Page through with \`cursor\` (an opaque base64 token), limit with \`limit\`, and pass \`since\` (an ISO-8601 timestamp) to export only what changed after a point in time:

\`\`\`bash
curl -H "Authorization: Bearer sk_..." \\
  "http://localhost:8008/api/v1/metasync/export?entity=track&limit=500"
\`\`\`
`
        },
        {
            id: 'api-video',
            title: 'Video',
            lede: 'The full video v1 surface: library, search, wishlist, watchlist, scans, downloads, calendar, and requests.',
            body: `
| Method | Endpoint | Purpose |
|--------|----------|---------|
| GET | \`/video/library\` | What's in the video library (\`?kind=\` — \`movies\` or \`shows\` — plus \`search\`, \`letter\`, \`sort\`, \`status\`, \`genre\`, \`page\`, \`limit\`) |
| GET | \`/video/library/genres\` | Video library genres |
| GET | \`/video/search\` | TMDB multi-search — \`?q=\` is required |
| GET | \`/video/trending\` | Trending titles |
| GET | \`/video/wishlist\` | Wishlist items — \`?kind=\` (\`movie\` or \`show\`) plus \`search\`, \`sort\`, \`page\`, \`limit\` for a page; no \`kind\` for counts only |
| GET | \`/video/wishlist/counts\` | Wishlist counts |
| POST | \`/video/wishlist\` | Add — \`{"movie": {tmdb_id, title, year?, poster_url?}}\` or \`{"show": {…}, "episodes": [{season_number, episode_number, …}]}\` |
| DELETE | \`/video/wishlist\` | Remove — \`{scope, tmdb_id, season_number?, episode_number?}\` (\`scope\`: \`movie\`, \`show\`, \`season\`, or \`episode\`) |
| GET | \`/video/watchlist\` | Watched shows, people, and studios |
| POST | \`/video/watchlist\` | Follow — \`{kind, tmdb_id, title, poster_url?}\` (\`kind\`: \`show\`, \`person\`, or \`studio\`) |
| DELETE | \`/video/watchlist\` | Unfollow — \`{kind, tmdb_id}\` |
| POST | \`/video/scan\` | Request a library scan — \`{mode?}\` (\`incremental\`, \`deep\`, or \`full\`; default \`full\`); returns 200 \`{"status":"in_progress"}\` if a scan is already running |
| GET | \`/video/scan/status\` | Scan status |
| GET | \`/video/downloads\` | Active video downloads |
| GET | \`/video/downloads/status\` | Video download status |
| GET | \`/video/downloads/history\` | Video download history |
| GET | \`/video/calendar\` | Upcoming and recent episodes/releases — \`?start=\` (ISO date, default today), \`?days=\` (1–31, default 7), \`?scope=\` (\`watchlist\` or \`all\`) |
| GET | \`/video/requests\` | List video requests |
| POST | \`/video/requests\` | Create — \`{kind, tmdb_id, title, year?, poster_url?, note?, monitor?}\` (\`kind\`: \`movie\` or \`show\`) |
| POST | \`/video/requests/{request_id}/approve\` | Approve a video request |
| POST | \`/video/requests/{request_id}/deny\` | Deny a video request |

These all use the same API-key authentication as the music endpoints and relay to the video backend.
`
        },
        {
            id: 'api-automations',
            title: 'Automations',
            lede: 'Drive the automation engine over HTTP: CRUD, run-now, progress, history, and the block catalog.',
            body: `
> [!NOTE]
> These endpoints live **outside** \`/api/v1\` — they are the web UI's own session-authenticated surface and do **not** accept API keys. Call them from an authenticated browser session (export your login cookies and pass \`curl -b cookies.txt\`).

| Method | Path | Purpose | Auth |
|--------|------|---------|------|
| GET | \`/api/automations\` | List automations for the current profile | session (profile-scoped) |
| POST | \`/api/automations\` | Create an automation | session (profile-scoped) |
| GET | \`/api/automations/master\` | Master pause state — \`{"music": bool, "video": bool}\` | session |
| POST | \`/api/automations/master\` | Flip the master pause switch | admin |
| GET | \`/api/automations/{automation_id}\` | One automation | session |
| PUT | \`/api/automations/{automation_id}\` | Update an automation | session |
| PUT | \`/api/automations/group\` | Move automations into a group | session |
| POST | \`/api/automations/bulk-toggle\` | Enable/disable many automations at once | session |
| DELETE | \`/api/automations/{automation_id}\` | Delete an automation | session |
| POST | \`/api/automations/{automation_id}/duplicate\` | Duplicate an automation into your profile | session (profile-scoped) |
| POST | \`/api/automations/{automation_id}/toggle\` | Enable/disable one automation | session |
| POST | \`/api/automations/{automation_id}/run\` | Run an automation right now | session (profile-scoped) |
| GET | \`/api/automations/progress\` | Live progress of running automations | session |
| GET | \`/api/automations/{automation_id}/history\` | Run history — \`?limit=50&offset=0\` | session |
| GET | \`/api/scripts\` | Scripts available to the run-script action | session |
| GET | \`/api/automations/blocks\` | Trigger/action/notification block catalog | session |
| POST | \`/api/automations/test-notify\` | Fire a test notification | session |

## Creating and updating

\`POST /api/automations\` takes the full automation definition as JSON — \`name\` is required:

\`\`\`bash
curl -b cookies.txt -X POST http://localhost:8008/api/automations \\
  -H "Content-Type: application/json" \\
  -d '{"name": "Nightly wishlist", "trigger_type": "schedule", "trigger_config": {"interval": 6, "unit": "hours"}, "action_type": "process_wishlist", "action_config": {}}'
\`\`\`

Accepted fields: \`name\`, \`trigger_type\`, \`trigger_config\`, \`action_type\`, \`action_config\`, \`then_actions\`, \`notify_type\`, \`notify_config\`, \`group_name\`, \`owned_by\`. Success answers \`{"success": true, "id": <new_id>}\`; a trigger loop is rejected with \`400\` ("Signal cycle detected"). \`PUT /api/automations/{automation_id}\` accepts the same fields (anything outside the whitelist is ignored) and answers \`{"success": true}\`.

## Grouping and bulk toggles

\`\`\`bash
curl -b cookies.txt -X PUT http://localhost:8008/api/automations/group \\
  -H "Content-Type: application/json" \\
  -d '{"automation_ids": [3, 7], "group_name": "Nightly"}'

curl -b cookies.txt -X POST http://localhost:8008/api/automations/bulk-toggle \\
  -H "Content-Type: application/json" \\
  -d '{"automation_ids": [3, 7], "enabled": false}'
\`\`\`

Both answer \`{"success": true, "updated": <n>}\`.

## Master pause

\`POST /api/automations/master\` takes \`{"side": "music", "enabled": false}\` and pauses **every** automation on that side at once (admin only). \`GET\` returns the current state, e.g. \`{"music": true, "video": true}\`.

## Notifications, scripts, blocks

- \`POST /api/automations/test-notify\` sends a real test message through a notification channel: \`{"type": "discord_webhook", "config": {...}}\`. \`type\` must be one of \`discord_webhook\`, \`pushbullet\`, \`telegram\`, \`webhook\`; \`config\` carries that channel's settings and the test goes out with sample run variables filled in.
- \`GET /api/scripts\` lists the configured scripts directory as \`{name, extension, size}\` entries — the options the run-script action block offers. Recognized: \`.sh\` \`.py\` \`.bat\` \`.ps1\` \`.rb\` \`.pl\` \`.js\`, plus any executable file.
- \`GET /api/automations/blocks\` returns the music-scope builder catalog — \`{triggers, actions, notifications, category_order}\` plus \`known_signals\`, every signal name the engine can trigger on. The automation builder renders its palette from this endpoint.

## Notes

- System automations cannot be deleted or duplicated — both answer \`403\`.
- Live run progress also streams over the \`automation:progress\` real-time event (see [Real-time Events](#api-websocket)); \`GET /api/automations/progress\` is the pollable equivalent, keyed by automation id.
`
        },
        {
            id: 'api-legacy',
            title: 'Requests & Profile Admin',
            lede: 'Member download requests, invites, devices, and avatars — the session-authenticated endpoints behind household management.',
            body: `
> [!NOTE]
> Like the automation endpoints, these live **outside** \`/api/v1\` and use your logged-in browser session, not API keys. The basic profile CRUD is documented under [Profiles](#api-profiles); this page covers everything around it.

## Music requests

Members submit download requests; admins approve or decline them.

| Method | Path | Purpose | Auth |
|--------|------|---------|------|
| GET | \`/api/requests/music\` | List requests — \`?status=\` is one of \`pending\`, \`approved\`, \`available\`, \`declined\`, \`all\` | session |
| GET | \`/api/requests/music/quota\` | Your request quota (\`quota: null\` = unlimited) | session |
| GET | \`/api/requests/music/counts\` | Pending count plus unseen updates | session |
| POST | \`/api/requests/music/seen\` | Mark request updates as seen | session |
| POST | \`/api/requests/music/approve\` | Approve one request | admin |
| POST | \`/api/requests/music/approve-all\` | Approve all pending (optionally one profile's) | admin |
| POST | \`/api/requests/music/decline\` | Decline one request | admin |
| POST | \`/api/requests/music/withdraw\` | Withdraw your own request | session |
| DELETE | \`/api/requests/music/{request_id}\` | Delete a request from history | session (own) / admin |

\`POST /api/requests/music/approve\` takes \`{"profile_id": 2, "key": "<request-key>"}\` with an optional \`response\` note (truncated to 500 characters) and answers \`{"success": true, "approved": <n>}\`. \`approve-all\` takes an optional \`profile_id\` — omit it to approve everything pending. \`decline\` takes the same body shape as \`approve\`. \`withdraw\` takes \`{"key": "<request-key>"}\`.

## Profile admin

| Method | Path | Purpose | Auth |
|--------|------|---------|------|
| GET | \`/api/profiles/audit\` | Audit log — \`?limit=100&offset=0\` | admin |
| POST | \`/api/profiles/{profile_id}/sign-out-everywhere\` | Revoke every session for a profile | session (owner or admin) |
| GET | \`/api/profiles/{profile_id}/devices\` | Logged-in devices, each with a \`current\` flag | session (owner or admin) |
| DELETE | \`/api/profiles/{profile_id}/devices/{device_id}\` | Sign out one device | session (owner or admin) |
| GET | \`/api/profiles/invites\` | Invites with \`state\` (\`open\`/\`used\`/\`revoked\`/\`expired\`) | admin |
| POST | \`/api/profiles/invites\` | Create an invite — answers \`201\`, token shown **once** | admin |
| DELETE | \`/api/profiles/invites/{invite_id}\` | Revoke an invite | admin |
| GET | \`/api/invite/{token}\` | Inspect an invite (rate-limited) | open |
| POST | \`/api/invite/{token}/accept\` | Claim an invite, creating a profile | open |
| POST | \`/api/profiles/{profile_id}/avatar\` | Upload an avatar (multipart \`file\`, ≤3 MB, stored as 512px webp) | session (owner or admin) |
| GET | \`/api/profiles/{profile_id}/avatar\` | The avatar image itself (\`image/webp\`, not JSON) | open |
| DELETE | \`/api/profiles/{profile_id}/avatar\` | Remove the avatar | session (owner or admin) |

Creating an invite takes an optional \`preset\` — the new profile's starting permissions (\`allowed_sides\`, \`can_download\`, \`allowed_pages\`, \`hide_explicit\`, \`max_rating\`, \`request_limit\`, \`request_limit_days\`) — plus an optional \`note\` (≤200 chars) and \`expires_hours\` (default 72, clamped 1–720). The response includes the invite \`token\` **exactly once** (only a hash is stored), alongside the shareable \`path\` and \`expires_hours\`.

Accepting an invite takes \`{"name": "..."}\` (required, ≤40 chars) with optional \`avatar_color\`, \`pin\` (4–20 digits), and \`password\` (required when login is enabled, ≥6 chars), and answers \`201\` with \`{"success": true, "profile_id": <id>}\`.

> [!WARNING]
> Treat invite tokens like passwords: anyone with the link can create a profile on your server until it expires or is revoked. Keep \`expires_hours\` short for links you share anywhere public.
`
        },
        {
            id: 'api-websocket',
            title: 'Real-time Events',
            lede: 'Skip polling: open a Socket.IO connection and let SoulSync push updates to you.',
            body: `
SoulSync runs **Socket.IO** on the same host and port as the web UI. Connect with any Socket.IO client — the connection uses your logged-in session, not an API key, so connect from a context where you are authenticated.

\`\`\`javascript
import { io } from "socket.io-client";
const socket = io("http://localhost:8008");
socket.on("downloads:batch_update", (payload) => console.log(payload));
\`\`\`

## Events

Every emit below is server → client, pushed from a background thread — none fire from HTTP request handlers. Some are broadcast; some go to rooms you must join first (see "Client → server" below).

| Event | Direction | Payload | When it fires |
|-------|-----------|---------|---------------|
| \`downloads:batch_update\` | S→C | \`{batch_id, data}\` | Download queue progress — every 2s per batch. Join \`batch:{id}\` rooms with \`downloads:subscribe\`. |
| \`scan:media\` | S→C | \`{success, status}\` | Library scan progress. |
| \`scan:watchlist\` | S→C | \`{success, …state}\` | Watchlist scan progress. |
| \`discovery:progress\` | S→C | \`{platform, id, phase, status, progress, …}\` | Discovery run progress, one emit per active platform scan. Join \`discovery:{id}\` rooms with \`discovery:subscribe\`. |
| \`sync:progress\` | S→C | \`{playlist_id, …state}\` | Playlist sync progress. Join \`sync:{id}\` rooms with \`sync:subscribe\`. |
| \`sync:active\` | S→C | \`{active, syncs}\` | The currently active syncs — broadcast while any sync runs (deliberately unscoped). |
| \`repair:progress\` | S→C | \`{job_id: state}\` | Library Maintenance job progress; state carries \`status\`, \`progress\`, \`processed\`, \`total\`, \`log\`, \`finished_at\`. |
| \`automation:progress\` | S→C | \`{automation_id: state}\` | Automation run progress (also pollable via \`GET /api/automations/progress\`). |
| \`dashboard:stats\` | S→C | system stats | Dashboard numbers refreshed (every 10s): active/finished downloads, download speed, active syncs, uptime, memory. |
| \`dashboard:db_stats\` | S→C | database info | Database statistics refreshed (every 10s). |
| \`dashboard:activity\` | S→C | \`{activities}\` | New activity feed entry (last 10, every 2s). |
| \`dashboard:toast\` | S→C | \`{icon, title, subtitle}\` | Toast notification shown in the UI. |
| \`dashboard:wishlist_count\` | S→C | \`{count}\` | Wishlist count changed (per profile). |
| \`wishlist:stats\` | S→C | \`{is_auto_processing, active_batches, next_run_in_seconds}\` | Wishlist statistics changed. |
| \`watchlist:count\` | S→C | \`{success, count, next_run_in_seconds}\` | Watchlist count changed (per profile). |
| \`status:update\` | S→C | service connectivity flags | System status changed (every 5s): metadata source, Spotify, media server, Soulseek, enrichment, active downloads. |
| \`activity:update\` | S→C | activity snapshot | Server activity updated — join the \`activity:live\` room with \`activity:subscribe\`. |
| \`enrichment:{worker}\` | S→C | worker stats | Enrichment worker progress — one event per worker (e.g. \`enrichment:youtube\`). |
| \`tool:logs\` | S→C | \`{logs}\` | Tool output streamed (last 50 activity entries, formatted). |
| \`tool:metadata\` | S→C | \`{success, status}\` | Metadata tool progress. |
| \`tool:db-update\` | S→C | db-update state | Database update tool progress. |
| \`tool:duplicate-cleaner\` | S→C | cleaner state + \`space_freed_mb\` | Duplicate cleaner progress. |
| \`logs:live\` | S→C | \`{lines, source}\` | Live log lines — admin only, subscribe with \`logs:subscribe\` first. |
| \`lastfm:import-progress\` | S→C | import state | Last.fm history import progress (per profile). |
| \`listenbrainz:import-progress\` | S→C | import state | ListenBrainz import progress (per profile). |
| \`rate-monitor:update\` | S→C | per-service rate limits | API rate-monitor tick. |
| \`chat:room_message\` | S→C | \`{room, messages}\` | New Soulseek chat messages (last 20 decoded). |
| \`chat:room_protocol\` | S→C | \`{room, events}\` | Soulseek chat protocol events (last 40). |
| \`chat:unread\` | S→C | \`{pms, users, grew}\` | PM unread count changed. |
| \`overlay:progress\` | S→C | overlay job state | Video overlay render progress. |
| \`video:bulk\` | S→C | bulk-op state | Video bulk operation progress. |
| \`video:repair:progress\` | S→C | repair snapshot | Video repair worker progress. |
| \`collections:sync\` | S→C | sync job state | Video collection sync progress. |
| \`collections:cleanup\` | S→C | cleanup state | Video collection server-cleanup progress. |
| \`collections:artwork\` | S→C | poster-gen state | Collection artwork generation progress. |

## Client → server

| Event | Direction | Payload | What it does |
|-------|-----------|---------|--------------|
| \`connect\` / \`disconnect\` | C→S | — | Connection lifecycle. \`connect\` runs the launch-PIN/login gate — unverified handshakes are rejected. |
| \`activity:subscribe\` / \`activity:unsubscribe\` | C→S | — | Join/leave the \`activity:live\` room to receive \`activity:update\`. |
| \`downloads:subscribe\` / \`downloads:unsubscribe\` | C→S | \`{batch_ids}\` | Join/leave \`batch:{id}\` rooms to receive \`downloads:batch_update\`. |
| \`profile:join\` | C→S | \`{profile_id?, old_profile_id?}\` | Join your profile room for per-profile pushes (\`watchlist:count\`, \`dashboard:wishlist_count\`, import progress). The room is derived from your session, not trusted from the payload. |
| \`logs:subscribe\` / \`logs:unsubscribe\` | C→S | \`{source?}\` (default \`app\`) | Live log tail for \`logs:live\` — admin only. |
| \`sync:subscribe\` / \`sync:unsubscribe\` | C→S | \`{playlist_ids}\` | Join/leave \`sync:{id}\` rooms for \`sync:progress\`. |
| \`discovery:subscribe\` / \`discovery:unsubscribe\` | C→S | \`{ids}\` | Join/leave \`discovery:{id}\` rooms for \`discovery:progress\`. |

> [!NOTE]
> Event names are namespaced (\`downloads:batch_update\`, not \`download_progress\`). If you are migrating from an older integration, update your listeners — the flat names from earlier versions no longer fire.
>
> \`app_started\` and \`mirrored_playlist_created\` are automation-engine trigger events, not Socket.IO events — react to them with an automation, not a socket listener.
`
        },
    ]
    });

})();
