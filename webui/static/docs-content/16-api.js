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
| GET | \`/video/library\` | What's in the video library (\`?kind=movies|shows&search=&letter=&sort=&status=&genre=&page=&limit=\`) |
| GET | \`/video/library/genres\` | Video library genres |
| GET | \`/video/search\` | TMDB multi-search — \`?q=\` is required |
| GET | \`/video/trending\` | Trending titles |
| GET | \`/video/wishlist\` | Wishlist items — \`?kind=movie|show&search=&sort=&page=&limit=\` for a page; no \`kind\` for counts only |
| GET | \`/video/wishlist/counts\` | Wishlist counts |
| POST | \`/video/wishlist\` | Add — \`{"movie": {tmdb_id, title, year?, poster_url?}}\` or \`{"show": {…}, "episodes": [{season_number, episode_number, …}]}\` |
| DELETE | \`/video/wishlist\` | Remove — \`{scope: movie|show|season|episode, tmdb_id, season_number?, episode_number?}\` |
| GET | \`/video/watchlist\` | Watched shows, people, and studios |
| POST | \`/video/watchlist\` | Follow — \`{kind: show|person|studio, tmdb_id, title, poster_url?}\` |
| DELETE | \`/video/watchlist\` | Unfollow — \`{kind, tmdb_id}\` |
| POST | \`/video/scan\` | Request a library scan — \`{mode?: incremental|deep|full}\`; \`409\` if a scan is already running |
| GET | \`/video/scan/status\` | Scan status |
| GET | \`/video/downloads\` | Active video downloads |
| GET | \`/video/downloads/status\` | Video download status |
| GET | \`/video/downloads/history\` | Video download history |
| GET | \`/video/calendar\` | Upcoming and recent episodes/releases — \`?start=&end=\` as ISO dates |
| GET | \`/video/requests\` | List video requests |
| POST | \`/video/requests\` | Create — \`{kind: movie|show, tmdb_id, title, year?, poster_url?, note?, monitor?}\` |
| POST | \`/video/requests/{request_id}/approve\` | Approve a video request |
| POST | \`/video/requests/{request_id}/deny\` | Deny a video request |

These all use the same API-key authentication as the music endpoints and relay to the video backend.
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

| Event | When it fires |
|-------|---------------|
| \`downloads:batch_update\` | Download queue progress changed |
| \`scan:media\` | Library scan progress |
| \`scan:watchlist\` | Watchlist scan progress |
| \`discovery:progress\` | Discovery run progress |
| \`sync:progress\` / \`sync:active\` | Sync job progress / active syncs |
| \`repair:progress\` | Library Maintenance job progress |
| \`dashboard:stats\` / \`dashboard:db_stats\` | Dashboard numbers refreshed |
| \`dashboard:activity\` | New activity feed entry |
| \`dashboard:toast\` | Toast notification shown in UI |
| \`dashboard:wishlist_count\` | Wishlist count changed |
| \`wishlist:stats\` | Wishlist statistics changed |
| \`watchlist:count\` | Watchlist count changed |
| \`status:update\` | System status changed |
| \`activity:update\` | Activity feed updated |
| \`enrichment:youtube\` | YouTube enrichment finished |
| \`tool:logs\` / \`tool:metadata\` | Tool output streamed |
| \`logs:live\` | Live log lines |
| \`chat:room_message\` / \`chat:room_protocol\` / \`chat:unread\` | Chat updates |
| \`mirrored_playlist_created\` | A mirrored playlist was created |
| \`app_started\` | Server finished starting |

> [!NOTE]
> Event names are namespaced (\`downloads:batch_update\`, not \`download_progress\`). If you are migrating from an older integration, update your listeners — the flat names from earlier versions no longer fire.
`
        },
    ]
    });

})();
