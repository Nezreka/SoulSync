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
# Authentication & API Keys

## How authentication works

Every public endpoint lives under \`/api/v1\` and requires an API key, except the first-key bootstrap endpoint. Send the key in either of two ways:

\`\`\`bash
curl -H "Authorization: Bearer sk_..." http://localhost:8008/api/v1/system/status
curl "http://localhost:8008/api/v1/system/status?api_key=sk_..."
\`\`\`

The \`Authorization: Bearer <key>\` header is preferred — a key in the query string can end up in server logs, proxy logs, and shell history. If the \`Authorization\` header is present but does not start with \`Bearer \`, it is ignored and the query parameter is tried; if neither is present the request is rejected.

**Key anatomy.** A raw key is \`sk_\` followed by 43 URL-safe random characters (generated with \`secrets.token_urlsafe(32)\`). The raw key is shown **exactly once**, in the response that creates it. Only the SHA-256 hash of the key is stored — a leaked database cannot be turned back into working keys.

**What a key can do.** An API key acts with **admin rights** (\`g.is_admin = True\` is stamped on every key-authenticated request). Treat keys like passwords: store them in a secrets manager, never commit them to git, and rotate immediately if one leaks. There is no read-only key tier.

**Key metadata.** Each stored key records a \`label\`, a visible \`key_prefix\` (\`sk_\` plus the first 8 characters of the raw key), \`created_at\`, and \`last_used_at\`. \`last_used_at\` is refreshed on every authenticated request (persisted to disk at most once every 15 minutes per key, so heavy polling does not rewrite the config file constantly).

**Rate limiting.** The whole \`/api/v1\` blueprint is limited to 60 requests per minute per client IP (Flask-Limiter, keyed on remote address). Exceeding it returns \`429\` with code \`RATE_LIMITED\`.

**Response envelope.** Every response — success or failure — uses the same shape:

\`\`\`json
{
  "success": true,
  "data": { "...": "..." },
  "error": null,
  "pagination": { "page": 1, "limit": 50, "total": 128, "total_pages": 3, "has_next": true, "has_prev": false }
}
\`\`\`

Errors look like \`{"success": false, "data": null, "error": {"code": "INVALID_KEY", "message": "..."}, "pagination": null}\`. \`pagination\` is only populated on paginated endpoints; everywhere else it is \`null\`.

## Key management endpoints

### \`GET /api/v1/api-keys\`

List all API keys. Only safe metadata is returned — never the raw key, never the hash.

**Response** — \`data.keys\` is an array of:

\`\`\`json
{
  "success": true,
  "data": {
    "keys": [
      {
        "id": "123e4567-e89b-12d3-a456-426614174000",
        "label": "Telegram bot",
        "key_prefix": "sk_aB3dE5fG",
        "created_at": "2026-09-20T14:02:11.123456+00:00",
        "last_used_at": "2026-09-30T07:58:02.445100+00:00"
      }
    ]
  },
  "error": null,
  "pagination": null
}
\`\`\`

| Field | Type | Description |
|-------|------|-------------|
| \`id\` | string (UUID) | The key's stable identifier. Use this (not the key itself) to revoke it. |
| \`label\` | string | Human label given at creation; empty string if none was given. |
| \`key_prefix\` | string | \`sk_\` plus the first 8 characters of the raw key — enough to recognize which key is which. |
| \`created_at\` | string (ISO 8601, UTC) | When the key was minted. |
| \`last_used_at\` | string/null | Last time the key authenticated a request; \`null\` if never used. |

**Errors:** \`401 AUTH_REQUIRED\`, \`403 INVALID_KEY\`, \`429 RATE_LIMITED\`, \`500 SETTINGS_ERROR\`.

### \`POST /api/v1/api-keys\`

Mint a new API key. The raw key is returned **once** in this response and can never be retrieved again — copy it immediately.

**Request body** (\`application/json\`):

| Name | Type | Required | Default | Description |
|------|------|----------|---------|-------------|
| \`label\` | string | No | \`""\` | Human-readable label, e.g. \`"My Bot"\`. Shown in the key list. |

**Response** — \`201 Created\`:

\`\`\`json
{
  "success": true,
  "data": {
    "key": "sk_...",
    "id": "123e4567-e89b-12d3-a456-426614174000",
    "label": "My Bot",
    "key_prefix": "sk_aB3dE5fG",
    "created_at": "2026-09-30T08:10:22.918273+00:00"
  },
  "error": null,
  "pagination": null
}
\`\`\`

Note the response does **not** include \`last_used_at\` (the key has never been used yet).

**Example:**

\`\`\`bash
curl -X POST http://localhost:8008/api/v1/api-keys \\
  -H "Authorization: Bearer sk_..." \\
  -H "Content-Type: application/json" \\
  -d '{"label": "My Bot"}'
\`\`\`

**Errors:** \`401 AUTH_REQUIRED\`, \`403 INVALID_KEY\`, \`429 RATE_LIMITED\`, \`500 SETTINGS_ERROR\`.

**Notes:**
- Save the returned \`key\` value immediately. If you lose it, revoke the key by its \`id\` and mint a new one.
- The new key is usable on its very next request.

### \`DELETE /api/v1/api-keys/{key_id}\`

Revoke (delete) an API key by its \`id\` — the UUID from the key list, not the raw key value.

**Path parameters:**

| Name | Type | Required | Description |
|------|------|----------|-------------|
| \`key_id\` | string (UUID) | Yes | The \`id\` field of the key to revoke, as returned by \`GET /api-keys\` or \`POST /api-keys\`. |

**Response:**

\`\`\`json
{
  "success": true,
  "data": { "message": "API key revoked." },
  "error": null,
  "pagination": null
}
\`\`\`

**Example:**

\`\`\`bash
curl -X DELETE http://localhost:8008/api/v1/api-keys/123e4567-e89b-12d3-a456-426614174000 \\
  -H "Authorization: Bearer sk_..."
\`\`\`

**Errors:** \`401 AUTH_REQUIRED\`, \`403 INVALID_KEY\`, \`404 NOT_FOUND\` (\`API key not found.\` — no key with that id), \`429 RATE_LIMITED\`, \`500 SETTINGS_ERROR\`.

**Notes:**
- Revocation takes effect immediately: the key fails authentication on its very next request.
- If the key you revoke is the one your automation uses, that automation breaks on its next call — mint a replacement first, switch the automation over, then revoke the old one.
- You can revoke the key you are currently authenticating with; the response to the DELETE itself still succeeds.

### \`POST /api/v1/api-keys/bootstrap\`

Mint the **first** API key on a fresh install. This is the only public endpoint that works without authentication — and it only works while **zero** keys exist.

**Request body** (\`application/json\`):

| Name | Type | Required | Default | Description |
|------|------|----------|---------|-------------|
| \`label\` | string | No | \`"Default"\` | Human-readable label for the first key. |

**Response** — \`201 Created\`, same shape as \`POST /api-keys\`:

\`\`\`json
{
  "success": true,
  "data": {
    "key": "sk_...",
    "id": "123e4567-e89b-12d3-a456-426614174000",
    "label": "My First Key",
    "key_prefix": "sk_aB3dE5fG",
    "created_at": "2026-09-30T08:10:22.918273+00:00"
  },
  "error": null,
  "pagination": null
}
\`\`\`

**Example:**

\`\`\`bash
curl -X POST http://localhost:8008/api/v1/api-keys/bootstrap \\
  -H "Content-Type: application/json" \\
  -d '{"label": "My First Key"}'
\`\`\`

**Errors:** \`403 FORBIDDEN\` (\`API keys already exist. Use an authenticated request to create more.\`), \`429 RATE_LIMITED\`, \`500 SETTINGS_ERROR\`.

**Notes:**
- As soon as one key exists, this endpoint permanently answers \`403\`. There is no way to re-enable it short of deleting all keys from the config.
- Create all further keys with an authenticated \`POST /api-keys\`.
`
        },
        {
            id: 'api-system',
            title: 'System',
            lede: 'Health, statistics, and the live activity feed — the fastest way to check the server is alive.',
            body: `
# System

### \`GET /api/v1/system/status\`

Server health check: uptime plus per-service connectivity flags. This is the endpoint to point an uptime monitor at — it does no database queries, only in-memory checks.

**Response** — \`data\`:

| Field | Type | Description |
|-------|------|-------------|
| \`uptime\` | string | Human-readable uptime, e.g. \`"2h 14m 9s"\`. Measured from the web server's start time. |
| \`uptime_seconds\` | integer | Same uptime as whole seconds. |
| \`services.spotify\` | boolean | \`true\` when a Spotify client is configured **and** authenticated. |
| \`services.soulseek\` | boolean | \`true\` when the download orchestrator is present. (Presence check, not a network probe.) |
| \`services.hydrabase\` | boolean | \`true\` when the Hydrabase websocket is connected; \`false\` when Hydrabase is not configured or the socket is down. A failed probe is logged at debug level and reported as \`false\`, never as an error. |

\`\`\`json
{
  "success": true,
  "data": {
    "uptime": "2h 14m 9s",
    "uptime_seconds": 8049,
    "services": {
      "spotify": true,
      "soulseek": true,
      "hydrabase": false
    }
  },
  "error": null,
  "pagination": null
}
\`\`\`

**Example:**

\`\`\`bash
curl -H "Authorization: Bearer sk_..." http://localhost:8008/api/v1/system/status
\`\`\`

**Errors:** \`401 AUTH_REQUIRED\`, \`403 INVALID_KEY\`, \`429 RATE_LIMITED\`, \`500 SYSTEM_ERROR\`.

### \`GET /api/v1/system/activity\`

Recent activity feed — the same events that surface as toasts/activity in the web UI.

**Response** — \`data.activities\` is an array (oldest first, see correction above) of:

| Field | Type | Description |
|-------|------|-------------|
| \`icon\` | string | Emoji/icon for the event, e.g. \`"⬇️"\`. |
| \`title\` | string | Short event title, e.g. \`"Download finished"\`. |
| \`subtitle\` | string | Longer detail line, e.g. \`"Artist — Album (FLAC)"\`. |
| \`time\` | string | Relative display time as recorded when the event fired, e.g. \`"2m ago"\`, or \`"Now"\`. |
| \`timestamp\` | number | Unix epoch seconds when the event fired. |
| \`show_toast\` | boolean | Whether this event also popped a UI toast. Informational for API consumers. |

\`\`\`json
{
  "success": true,
  "data": {
    "activities": [
      {
        "icon": "⬇️",
        "title": "Download finished",
        "subtitle": "Artist — Album (FLAC)",
        "time": "2m ago",
        "timestamp": 1759270092.5,
        "show_toast": true
      }
    ]
  },
  "error": null,
  "pagination": null
}
\`\`\`

**Example:**

\`\`\`bash
curl -H "Authorization: Bearer sk_..." http://localhost:8008/api/v1/system/activity
\`\`\`

**Errors:** \`401 AUTH_REQUIRED\`, \`403 INVALID_KEY\`, \`429 RATE_LIMITED\`, \`500 SYSTEM_ERROR\`.

**Notes:**
- The feed holds at most the **20 most recent** events; older ones are evicted. The response is **not paginated** — if you need history beyond that window, poll on an interval and append locally.
- Items arrive **oldest first**. Reverse the array client-side if you want newest-first display.
- An empty feed returns \`"activities": []\`, never an error.

### \`GET /api/v1/system/stats\`

Combined library and download statistics — the numbers behind the dashboard.

**Response** — \`data\`:

| Field | Type | Description |
|-------|------|-------------|
| \`library.artists\` | integer | Total artists in the library. |
| \`library.albums\` | integer | Total albums in the library. |
| \`library.tracks\` | integer | Total tracks in the library. |
| \`database.size_mb\` | number/null | Database file size in megabytes (\`null\` if unavailable). |
| \`database.last_update\` | string/null | Last database update timestamp (\`null\` if unavailable). |
| \`downloads.active\` | integer | Tasks currently in \`downloading\`, \`queued\`, or \`searching\` status. Finished/failed tasks are not counted. |

\`\`\`json
{
  "success": true,
  "data": {
    "library": {
      "artists": 1284,
      "albums": 3421,
      "tracks": 41877
    },
    "database": {
      "size_mb": 512.4,
      "last_update": "2026-09-30T07:00:00+00:00"
    },
    "downloads": {
      "active": 2
    }
  },
  "error": null,
  "pagination": null
}
\`\`\`

**Example:**

\`\`\`bash
curl -H "Authorization: Bearer sk_..." http://localhost:8008/api/v1/system/stats
\`\`\`

**Errors:** \`401 AUTH_REQUIRED\`, \`403 INVALID_KEY\`, \`429 RATE_LIMITED\`, \`500 SYSTEM_ERROR\`.

**Notes:**
- Library counts come from the same statistics query the server itself uses; missing values default to \`0\`, so the three library fields are always present.
- \`downloads.active\` is a live count from the in-memory task registry — cheap to call, safe to poll for progress displays.
`
        },
        {
            id: 'api-library',
            title: 'Library',
            lede: 'Read-only access to everything SoulSync knows about your music: artists, albums, tracks, and genres.',
            body: `
# Library API — draft documentation

> All endpoints live under \`/api/v1\` and require an API key: send it as an
> \`Authorization: Bearer <key>\` header (preferred) or as \`?api_key=<key>\`.
> Examples below assume \`API_KEY\` is set in the environment and the server is
> at \`http://localhost:8008\`. Every response uses the standard envelope
> \`{"success": true|false, "data": {...}|null, "error": {"code","message"}|null,
> "pagination": {...}|null}\`. \`pagination\` is populated only on paginated
> endpoints (noted per endpoint); elsewhere it is \`null\`. Rate limit: 60
> requests/minute per IP (\`429\`, code \`RATE_LIMITED\`).

## Endpoint reference

### \`GET /api/v1/library/artists\`

List artists in the library, with substring search, first-letter filter,
watchlist-status filter, and pagination. Results sort A–Z by name.

**Query parameters**

| Name | Type | Required | Default | Description |
|------|------|----------|---------|-------------|
| \`search\` | string | No | \`""\` | Case-insensitive substring filter on artist name |
| \`letter\` | string | No | \`"all"\` | First-letter filter: \`a\`–\`z\` (case-insensitive), \`#\` for names starting with a number or symbol, or \`"all"\` |
| \`watchlist\` | string | No | \`"all"\` | \`"all"\`, \`"watched"\`, or \`"unwatched"\` |
| \`page\` | int | No | \`1\` | Page number (minimum 1; invalid values fall back to 1) |
| \`limit\` | int | No | \`50\` | Results per page (1–200; values above 200 are clamped to 200; invalid values fall back to 50) |
| \`fields\` | string | No | — | Comma-separated field names to keep in each artist object, e.g. \`?fields=id,name,thumb_url\` |
| \`profile_id\` | int | No | \`1\` | Profile scope for the \`watchlist\` filter; also accepted as the \`X-Profile-Id\` header |

**Response**

\`data.artists\` is an array of serialized artist objects; \`pagination\` carries
the standard page object. Each artist includes full metadata: ids, artwork
URLs, genres (array of strings), summary/style/mood/label, \`server_source\`,
ISO-8601 \`created_at\`/\`updated_at\`, cross-install \`soul_id\`, external
provider IDs (\`musicbrainz_id\`, \`spotify_artist_id\`, \`itunes_artist_id\`,
\`audiodb_id\`, \`deezer_id\`, \`tidal_id\`, \`qobuz_id\`, \`genius_id\`), per-provider
match statuses and last-attempted timestamps, Last.fm stats, and Genius
metadata. Enriched queries may also add \`album_count\`, \`track_count\`,
\`is_watched\`, and \`image_url\`.

\`\`\`json
{
  "success": true,
  "data": {
    "artists": [
      {
        "id": 42,
        "name": "Miles Davis",
        "thumb_url": "https://image-cdn.example/abc123.jpg",
        "banner_url": null,
        "genres": ["Jazz", "Modal"],
        "summary": "...",
        "style": "Modal Jazz",
        "mood": null,
        "label": "Columbia",
        "server_source": "spotify",
        "created_at": "2026-09-12T18:04:22",
        "updated_at": "2026-09-20T09:11:03",
        "soul_id": "...",
        "musicbrainz_id": "...",
        "spotify_artist_id": "...",
        "itunes_artist_id": null,
        "audiodb_id": null,
        "deezer_id": "...",
        "tidal_id": null,
        "qobuz_id": null,
        "genius_id": null,
        "album_count": 18,
        "track_count": 214,
        "is_watched": true
      }
    ]
  },
  "error": null,
  "pagination": { "page": 1, "limit": 50, "total": 312, "total_pages": 7, "has_next": true, "has_prev": false }
}
\`\`\`

**Errors**

| Code | Status | When |
|------|--------|------|
| \`LIBRARY_ERROR\` | 500 | Database or unexpected failure |

**Example**

\`\`\`bash
curl -H "Authorization: Bearer $API_KEY" \\
  "http://localhost:8008/api/v1/library/artists?search=miles&limit=10&fields=id,name,thumb_url"
\`\`\`

---

### \`GET /api/v1/library/artists/{artist_id}\`

Get a single artist by its SoulSync ID, with full metadata and the artist's
album list embedded.

**Path parameters**

| Name | Type | Required | Description |
|------|------|----------|-------------|
| \`artist_id\` | int | Yes | SoulSync artist ID |

**Query parameters**

| Name | Type | Required | Default | Description |
|------|------|----------|---------|-------------|
| \`fields\` | string | No | — | Comma-separated field names applied to both the artist and album objects |

**Response**

\`data.artist\` is the serialized artist (same shape as in the list endpoint);
\`data.albums\` is an array of serialized albums (same shape as
\`GET /library/albums\`). Not paginated (\`pagination\` is \`null\`).

\`\`\`json
{
  "success": true,
  "data": {
    "artist": { "id": 42, "name": "Miles Davis", "...": "..." },
    "albums": [ { "id": 7, "title": "Kind of Blue", "year": 1959, "...": "..." } ]
  },
  "error": null,
  "pagination": null
}
\`\`\`

**Errors**

| Code | Status | When |
|------|--------|------|
| \`BAD_REQUEST\` | 400 | \`artist_id\` is not an integer |
| \`NOT_FOUND\` | 404 | No artist with that ID |
| \`LIBRARY_ERROR\` | 500 | Database or unexpected failure |

**Example**

\`\`\`bash
curl -H "Authorization: Bearer $API_KEY" \\
  "http://localhost:8008/api/v1/library/artists/42"
\`\`\`

---

### \`GET /api/v1/library/artists/{artist_id}/albums\`

List all albums for an artist, with full album metadata. **Not paginated** —
every album is returned in one response.

**Path parameters**

| Name | Type | Required | Description |
|------|------|----------|-------------|
| \`artist_id\` | int | Yes | SoulSync artist ID |

**Query parameters**

| Name | Type | Required | Default | Description |
|------|------|----------|---------|-------------|
| \`fields\` | string | No | — | Comma-separated field names to keep in each album object |

**Response**

\`data.albums\` is an array of serialized albums (same shape as
\`GET /library/albums\`). \`pagination\` is \`null\`.

**Errors**

| Code | Status | When |
|------|--------|------|
| \`BAD_REQUEST\` | 400 | \`artist_id\` is not an integer |
| \`LIBRARY_ERROR\` | 500 | Database or unexpected failure |

**Notes**

- Unlike \`GET /library/artists/<artist_id>\`, this endpoint returns no artist
  object — just the album array. A nonexistent artist yields an empty array,
  not a 404.

**Example**

\`\`\`bash
curl -H "Authorization: Bearer $API_KEY" \\
  "http://localhost:8008/api/v1/library/artists/42/albums?fields=id,title,year,thumb_url"
\`\`\`

---

### \`GET /api/v1/library/albums\`

List and search albums, with optional artist and year filters, and
pagination.

**Query parameters**

| Name | Type | Required | Default | Description |
|------|------|----------|---------|-------------|
| \`search\` | string | No | \`""\` | Substring filter on album title |
| \`artist_id\` | int | No | — | Filter to one artist's albums |
| \`year\` | int | No | — | Filter by release year |
| \`page\` | int | No | \`1\` | Page number (minimum 1; invalid values fall back to 1) |
| \`limit\` | int | No | \`50\` | Results per page (1–200; clamped to 200; invalid values fall back to 50) |
| \`fields\` | string | No | — | Comma-separated field names to keep in each album object |

**Response**

\`data.albums\` is an array of serialized album objects; \`pagination\` carries
the standard page object. Each album includes \`id\`, \`artist_id\`, \`title\`,
\`year\`, \`thumb_url\`, \`genres\` (array of strings), \`track_count\`, \`duration\`,
\`style\`, \`mood\`, \`label\`, \`explicit\` (bool or null), \`record_type\`,
\`server_source\`, ISO-8601 \`created_at\`/\`updated_at\`, \`upc\`, \`copyright\`,
cross-install \`soul_id\`, external provider IDs (\`musicbrainz_release_id\`,
\`spotify_album_id\`, \`itunes_album_id\`, \`audiodb_id\`, \`deezer_id\`, \`tidal_id\`,
\`qobuz_id\`), per-provider match statuses and last-attempted timestamps, and
Last.fm stats.

\`\`\`json
{
  "success": true,
  "data": {
    "albums": [
      {
        "id": 7,
        "artist_id": 42,
        "title": "Kind of Blue",
        "year": 1959,
        "thumb_url": "https://image-cdn.example/abc123.jpg",
        "genres": ["Jazz", "Modal"],
        "track_count": 5,
        "duration": 2734,
        "style": "Modal Jazz",
        "mood": null,
        "label": "Columbia",
        "explicit": false,
        "record_type": "album",
        "server_source": "spotify",
        "created_at": "2026-09-12T18:04:22",
        "updated_at": "2026-09-20T09:11:03"
      }
    ]
  },
  "error": null,
  "pagination": { "page": 1, "limit": 50, "total": 96, "total_pages": 2, "has_next": true, "has_prev": false }
}
\`\`\`

**Errors**

| Code | Status | When |
|------|--------|------|
| \`BAD_REQUEST\` | 400 | \`artist_id\` or \`year\` is not an integer |
| \`LIBRARY_ERROR\` | 500 | Database or unexpected failure |

**Example**

\`\`\`bash
curl -H "Authorization: Bearer $API_KEY" \\
  "http://localhost:8008/api/v1/library/albums?artist_id=42&year=1959"
\`\`\`

---

### \`GET /api/v1/library/albums/{album_id}\`

Get a single album by its SoulSync ID, with full metadata and its track
list embedded.

**Path parameters**

| Name | Type | Required | Description |
|------|------|----------|-------------|
| \`album_id\` | int | Yes | SoulSync album ID |

**Query parameters**

| Name | Type | Required | Default | Description |
|------|------|----------|---------|-------------|
| \`fields\` | string | No | — | Comma-separated field names applied to both the album and track objects |

**Response**

\`data.album\` is the serialized album (same shape as in the list endpoint);
\`data.tracks\` is an array of serialized tracks (same shape as
\`GET /library/albums/<album_id>/tracks\`). Not paginated (\`pagination\` is
\`null\`).

**Errors**

| Code | Status | When |
|------|--------|------|
| \`BAD_REQUEST\` | 400 | \`album_id\` is not an integer |
| \`NOT_FOUND\` | 404 | No album with that ID |
| \`LIBRARY_ERROR\` | 500 | Database or unexpected failure |

**Example**

\`\`\`bash
curl -H "Authorization: Bearer $API_KEY" \\
  "http://localhost:8008/api/v1/library/albums/7"
\`\`\`

---

### \`GET /api/v1/library/albums/{album_id}/tracks\`

List the tracks on an album, with full track metadata. **Not paginated** —
every track is returned in one response.

**Path parameters**

| Name | Type | Required | Description |
|------|------|----------|-------------|
| \`album_id\` | int | Yes | SoulSync album ID |

**Query parameters**

| Name | Type | Required | Default | Description |
|------|------|----------|---------|-------------|
| \`fields\` | string | No | — | Comma-separated field names to keep in each track object |

**Response**

\`data.tracks\` is an array of serialized track objects. Each track includes
\`id\`, \`album_id\`, \`artist_id\`, \`title\`, \`track_number\`, \`duration\`,
\`file_path\`, \`bitrate\`, \`bpm\`, \`explicit\` (bool or null), \`style\`, \`mood\`,
\`repair_status\`, ISO-8601 \`repair_last_checked\`, \`server_source\`,
ISO-8601 \`created_at\`/\`updated_at\`, \`isrc\`, \`copyright\`, cross-install
\`soul_id\` and \`album_soul_id\`, external provider IDs
(\`musicbrainz_recording_id\`, \`spotify_track_id\`, \`itunes_track_id\`,
\`audiodb_id\`, \`deezer_id\`, \`tidal_id\`, \`qobuz_id\`, \`genius_id\`),
per-provider match statuses and last-attempted timestamps, Last.fm stats,
and Genius metadata. Joined queries may also add \`artist_name\` and
\`album_title\`. \`pagination\` is \`null\`.

**Errors**

| Code | Status | When |
|------|--------|------|
| \`BAD_REQUEST\` | 400 | \`album_id\` is not an integer |
| \`LIBRARY_ERROR\` | 500 | Database or unexpected failure |

**Notes**

- A nonexistent album yields an empty \`tracks\` array, not a 404.

**Example**

\`\`\`bash
curl -H "Authorization: Bearer $API_KEY" \\
  "http://localhost:8008/api/v1/library/albums/7/tracks?fields=id,title,track_number,duration"
\`\`\`

---

### \`GET /api/v1/library/tracks/{track_id}\`

Get a single track by its SoulSync ID, with full metadata.

**Path parameters**

| Name | Type | Required | Description |
|------|------|----------|-------------|
| \`track_id\` | int | Yes | SoulSync track ID |

**Query parameters**

| Name | Type | Required | Default | Description |
|------|------|----------|---------|-------------|
| \`fields\` | string | No | — | Comma-separated field names to keep in the track object |

**Response**

\`data.track\` is the serialized track (same shape as in
\`GET /library/albums/<album_id>/tracks\`). Not paginated (\`pagination\` is
\`null\`).

**Errors**

| Code | Status | When |
|------|--------|------|
| \`BAD_REQUEST\` | 400 | \`track_id\` is not an integer |
| \`NOT_FOUND\` | 404 | No track with that ID |
| \`LIBRARY_ERROR\` | 500 | Database or unexpected failure |

**Example**

\`\`\`bash
curl -H "Authorization: Bearer $API_KEY" \\
  "http://localhost:8008/api/v1/library/tracks/1234"
\`\`\`

---

### \`GET /api/v1/library/tracks\`

Search tracks already in the library by title and/or artist. This is a
**search**, not a list — at least one of \`title\` or \`artist\` is required —
and it is **not paginated**; \`limit\` caps the result count instead.

**Query parameters**

| Name | Type | Required | Default | Description |
|------|------|----------|---------|-------------|
| \`title\` | string | At least one of \`title\`/\`artist\` | \`""\` | Substring match on track title |
| \`artist\` | string | At least one of \`title\`/\`artist\` | \`""\` | Substring match on artist name; combined with \`title\` it narrows the match |
| \`limit\` | int | No | \`50\` | Max results (1–200; values above 200 are clamped to 200; invalid values fall back to 50) |
| \`fields\` | string | No | — | Comma-separated field names to keep in each track object |

**Response**

\`data.tracks\` is an array of serialized tracks (same shape as
\`GET /library/albums/<album_id>/tracks\`). \`pagination\` is \`null\`.

**Errors**

| Code | Status | When |
|------|--------|------|
| \`BAD_REQUEST\` | 400 | Neither \`title\` nor \`artist\` was provided |
| \`LIBRARY_ERROR\` | 500 | Database or unexpected failure |

**Example**

\`\`\`bash
curl -H "Authorization: Bearer $API_KEY" \\
  "http://localhost:8008/api/v1/library/tracks?title=so+what&artist=miles+davis&limit=10"
\`\`\`

---

### \`GET /api/v1/library/genres\`

List every genre present in the library with occurrence counts, sorted by
count descending. **Not paginated** — the full list is returned in one
response.

**Query parameters**

| Name | Type | Required | Default | Description |
|------|------|----------|---------|-------------|
| \`source\` | string | No | \`"artists"\` | Which table to count from: \`"artists"\` or \`"albums"\` |

**Response**

\`data.genres\` is an array of \`{"name", "count"}\` objects; \`data.source\`
echoes the table that was counted. \`pagination\` is \`null\`.

\`\`\`json
{
  "success": true,
  "data": {
    "genres": [
      { "name": "Rock", "count": 412 },
      { "name": "Jazz", "count": 187 }
    ],
    "source": "artists"
  },
  "error": null,
  "pagination": null
}
\`\`\`

**Errors**

| Code | Status | When |
|------|--------|------|
| \`BAD_REQUEST\` | 400 | \`source\` is not \`"artists"\` or \`"albums"\` |
| \`LIBRARY_ERROR\` | 500 | Database or unexpected failure |

**Notes**

- Counts are per-row occurrences (an artist or album tagged with a genre
  counts once for that genre), not per-track.

**Example**

\`\`\`bash
curl -H "Authorization: Bearer $API_KEY" \\
  "http://localhost:8008/api/v1/library/genres?source=albums"
\`\`\`

---

### \`GET /api/v1/library/recently-added\`

Get recently added content ordered by \`created_at\`, newest first. **Not
paginated** — \`limit\` caps the result count.

**Query parameters**

| Name | Type | Required | Default | Description |
|------|------|----------|---------|-------------|
| \`type\` | string | No | \`"albums"\` | \`"albums"\`, \`"artists"\`, or \`"tracks"\` |
| \`limit\` | int | No | \`50\` | Max items (1–200; values above 200 are clamped to 200; invalid values fall back to 50) |
| \`fields\` | string | No | — | Comma-separated field names to keep in each item |

**Response**

\`data.items\` is an array of serialized artists, albums, or tracks depending
on \`type\` (same shapes as the corresponding list endpoints); \`data.type\`
echoes the requested type. \`pagination\` is \`null\`.

\`\`\`json
{
  "success": true,
  "data": {
    "items": [ { "id": 99, "title": "New Release", "year": 2026, "...": "..." } ],
    "type": "albums"
  },
  "error": null,
  "pagination": null
}
\`\`\`

**Errors**

| Code | Status | When |
|------|--------|------|
| \`BAD_REQUEST\` | 400 | \`type\` is not \`"albums"\`, \`"artists"\`, or \`"tracks"\` |
| \`LIBRARY_ERROR\` | 500 | Database or unexpected failure |

**Example**

\`\`\`bash
curl -H "Authorization: Bearer $API_KEY" \\
  "http://localhost:8008/api/v1/library/recently-added?type=tracks&limit=20"
\`\`\`

---

### \`GET /api/v1/library/lookup\`

Resolve a library entity from an external provider ID — e.g. you have a
Spotify or MusicBrainz ID from somewhere else and need the matching
SoulSync record. All three parameters are required.

**Query parameters**

| Name | Type | Required | Default | Description |
|------|------|----------|---------|-------------|
| \`type\` | string | Yes | — | \`"artist"\`, \`"album"\`, or \`"track"\` (singular) |
| \`provider\` | string | Yes | — | \`"spotify"\`, \`"musicbrainz"\`, \`"itunes"\`, \`"deezer"\`, \`"audiodb"\`, \`"tidal"\`, \`"qobuz"\`, or \`"genius"\` |
| \`id\` | string | Yes | — | The external ID value |
| \`fields\` | string | No | — | Comma-separated field names to keep in the returned object |

**Response**

The entity is returned under a key matching the requested \`type\`
(\`data.artist\`, \`data.album\`, or \`data.track\`), serialized in the same shape
as the corresponding list endpoints. \`pagination\` is \`null\`.

\`\`\`json
{
  "success": true,
  "data": {
    "album": { "id": 7, "title": "Kind of Blue", "year": 1959, "spotify_album_id": "...", "...": "..." }
  },
  "error": null,
  "pagination": null
}
\`\`\`

**Errors**

| Code | Status | When |
|------|--------|------|
| \`BAD_REQUEST\` | 400 | Missing \`type\`, \`provider\`, or \`id\`; unknown \`type\`; unknown \`provider\`; or \`provider=genius\` with \`type=album\` (Genius IDs are not stored for albums) |
| \`NOT_FOUND\` | 404 | No library entity matches that provider ID |
| \`LIBRARY_ERROR\` | 500 | Database or unexpected failure |

**Example**

\`\`\`bash
curl -H "Authorization: Bearer $API_KEY" \\
  "http://localhost:8008/api/v1/library/lookup?type=album&provider=spotify&id=<spotify-album-id>"
\`\`\`

---

### \`GET /api/v1/library/stats\`

Get library totals and database info — the numbers behind the dashboard.

**Query parameters**

None.

**Response**

\`data\` carries artist/album/track counts plus database size and last-update
info. \`database_size_mb\` and \`last_update\` are \`null\` when that info is
unavailable. \`pagination\` is \`null\`.

\`\`\`json
{
  "success": true,
  "data": {
    "artists": 312,
    "albums": 1204,
    "tracks": 14877,
    "database_size_mb": 84.6,
    "last_update": "2026-09-30T08:02:11"
  },
  "error": null,
  "pagination": null
}
\`\`\`

**Errors**

| Code | Status | When |
|------|--------|------|
| \`LIBRARY_ERROR\` | 500 | Database or unexpected failure |

**Example**

\`\`\`bash
curl -H "Authorization: Bearer $API_KEY" \\
  "http://localhost:8008/api/v1/library/stats"
\`\`\`
`
        },
        {
            id: 'api-search',
            title: 'Search',
            lede: 'Provider search for tracks, albums, and artists — the same results the Add pages show.',
            body: `
# Search API — draft

All endpoints live under \`/api/v1\` and require an API key: send it as an
\`Authorization: Bearer sk_...\` header (preferred) or as an \`?api_key=sk_...\`
query parameter. Every response uses the standard envelope
\`{"success": bool, "data": ..., "error": {"code","message"}|null, "pagination": ...|null}\`.
Rate limit: 60 requests/minute per IP (\`429\` / \`RATE_LIMITED\`).
These endpoints do not support \`?fields=\` or \`?page=\` — they take their
parameters in the JSON body as documented below.

### \`POST /api/v1/search/tracks\`

Search external metadata providers for tracks. This is the same provider
search the Add pages use. On \`source=auto\` (the default) SoulSync tries
Hydrabase first when it is active, then Spotify (only when Spotify is the
configured primary source **and** the client is authenticated), then falls
back to the configured primary metadata client. Passing an explicit \`source\`
skips the others: \`spotify\` queries only the Spotify branch, \`itunes\` or
\`deezer\` go straight to the primary-client fallback.

**Request body**

| Name | Type | Required | Default | Description |
|------|------|----------|---------|-------------|
| query | string | yes | — | Free-text query, e.g. \`"Miles Davis - So What"\`. Blank or missing is a \`400\`. |
| source | string | no | \`"auto"\` | \`"auto"\` \\| \`"spotify"\` \\| \`"itunes"\` \\| \`"deezer"\`. |
| limit | integer | no | \`20\` | Max results. Clamped to 1–50. Must be an integer (a non-numeric value fails the search with a \`500\`). |

**Response**

\`data\` is \`{"tracks": [...], "source": "<provider>"}\`. \`source\` names the
provider that actually answered (\`"hydrabase"\`, \`"spotify"\`, or the primary
source name) — or echoes your requested source when nothing was found. Each
track:

\`\`\`json
{
  "id": "4vLYewWIvqHfKtJDk8c8tq",
  "name": "So What",
  "artists": ["Miles Davis"],
  "album": "Kind of Blue",
  "duration_ms": 545280,
  "popularity": 72,
  "preview_url": "https://p.scdn.co/mp3-preview/...",
  "image_url": "https://i.scdn.co/image/...",
  "release_date": "1959-08-17"
}
\`\`\`

Fields may be \`null\` when the provider does not supply them. If no source
produces results, \`tracks\` is \`[]\`.

**Errors**

| Status | Code | Meaning |
|--------|------|---------|
| 400 | \`BAD_REQUEST\` | Missing or blank \`query\` (\`"Missing 'query' in request body."\`). |
| 500 | \`SEARCH_ERROR\` | Provider search raised (message carries the underlying error). |

**Example**

\`\`\`bash
curl -X POST http://localhost:8008/api/v1/search/tracks \\
  -H "Authorization: Bearer $SOULSYNC_API_KEY" \\
  -H "Content-Type: application/json" \\
  -d '{"query": "Miles Davis - So What", "limit": 5}'
\`\`\`

**Notes**

- If you pass \`source=spotify\` but Spotify is not the primary source or is
  not authenticated, you get \`{"tracks": [], "source": "spotify"}\` — the
  endpoint does not silently fall back when you name a source explicitly.
- Results carry no match score; if you need ranked picks, rank client-side on
  name/artist/duration.

---

### \`POST /api/v1/search/albums\`

Search external metadata providers for albums. Unlike track search there is
no \`source\` parameter and no Hydrabase branch: SoulSync queries Spotify first
(only when Spotify is the configured primary source **and** authenticated;
an empty Spotify result falls through) and then the configured primary
metadata client.

**Request body**

| Name | Type | Required | Default | Description |
|------|------|----------|---------|-------------|
| query | string | yes | — | Free-text query, e.g. \`"Kind of Blue"\`. Blank or missing is a \`400\`. |
| limit | integer | no | \`20\` | Max results. Clamped to 1–50. Must be an integer. |

**Response**

\`data\` is \`{"albums": [...], "source": "<provider>"}\`. Each album:

\`\`\`json
{
  "id": "1weenld61qoidwYuZ1GESA",
  "name": "Kind of Blue",
  "artists": ["Miles Davis"],
  "release_date": "1959-08-17",
  "total_tracks": 5,
  "album_type": "album",
  "image_url": "https://i.scdn.co/image/..."
}
\`\`\`

Empty result: \`{"albums": [], "source": "<primary source name>"}\`.

**Errors**

| Status | Code | Meaning |
|--------|------|---------|
| 400 | \`BAD_REQUEST\` | Missing or blank \`query\`. |
| 500 | \`SEARCH_ERROR\` | Provider search raised. |

**Example**

\`\`\`bash
curl -X POST http://localhost:8008/api/v1/search/albums \\
  -H "Authorization: Bearer $SOULSYNC_API_KEY" \\
  -H "Content-Type: application/json" \\
  -d '{"query": "Kind of Blue", "limit": 5}'
\`\`\`

---

### \`POST /api/v1/search/artists\`

Search external metadata providers for artists. Provider selection works
exactly like album search: Spotify first when it is the primary source and
authenticated, otherwise the configured primary metadata client. No \`source\`
parameter.

**Request body**

| Name | Type | Required | Default | Description |
|------|------|----------|---------|-------------|
| query | string | yes | — | Free-text query, e.g. \`"Miles Davis"\`. Blank or missing is a \`400\`. |
| limit | integer | no | \`20\` | Max results. Clamped to 1–50. Must be an integer. |

**Response**

\`data\` is \`{"artists": [...], "source": "<provider>"}\`. Each artist:

\`\`\`json
{
  "id": "0kbYTNQb4Pb1rPbbaF0pT4",
  "name": "Miles Davis",
  "popularity": 78,
  "genres": ["jazz", "cool jazz"],
  "followers": 2450311,
  "image_url": "https://i.scdn.co/image/..."
}
\`\`\`

Empty result: \`{"artists": [], "source": "<primary source name>"}\`.

**Errors**

| Status | Code | Meaning |
|--------|------|---------|
| 400 | \`BAD_REQUEST\` | Missing or blank \`query\`. |
| 500 | \`SEARCH_ERROR\` | Provider search raised. |

**Example**

\`\`\`bash
curl -X POST http://localhost:8008/api/v1/search/artists \\
  -H "Authorization: Bearer $SOULSYNC_API_KEY" \\
  -H "Content-Type: application/json" \\
  -d '{"query": "Miles Davis", "limit": 5}'
\`\`\`
`
        },
        {
            id: 'api-downloads',
            title: 'Downloads',
            lede: 'See what is downloading right now, and stop anything that should not be.',
            body: `
# Downloads API — draft

All endpoints live under \`/api/v1\` and require an API key: send it as an
\`Authorization: Bearer sk_...\` header (preferred) or as an \`?api_key=sk_...\`
query parameter. Every response uses the standard envelope
\`{"success": bool, "data": ..., "error": {"code","message"}|null, "pagination": ...|null}\`.
Rate limit: 60 requests/minute per IP (\`429\` / \`RATE_LIMITED\`).
These endpoints do not support \`?fields=\`. \`GET /downloads\` paginates with
\`limit\`/\`offset\`, not \`page\`.

### \`GET /api/v1/downloads\`

List tracked download tasks, newest first (sorted by \`status_change_time\`
descending). The response includes the post-filter \`total\` so clients can
paginate with \`limit\`/\`offset\` without fetching everything.

**Query parameters**

| Name | Type | Required | Default | Description |
|------|------|----------|---------|-------------|
| status | string | no | (all) | Comma-separated statuses to include, e.g. \`?status=downloading,queued\`. Matched exactly against each task's status. Omit for all statuses. |
| limit | integer | no | \`100\` | Max tasks returned. Clamped to 1–500. A non-numeric value falls back to \`100\`. |
| offset | integer | no | \`0\` | Skip the first N tasks. A non-numeric value falls back to \`0\`. |

**Response**

\`data\` is \`{"downloads": [...], "total": <int>, "limit": <int>, "offset": <int>}\`.
\`total\` is the count **after** status filtering. Each download task:

\`\`\`json
{
  "id": "d8f2a1c4-...",
  "status": "downloading",
  "track_name": "So What",
  "artist_name": "Miles Davis",
  "album_name": "Kind of Blue",
  "username": "somepeer",
  "filename": "Miles Davis - So What.flac",
  "progress": 62,
  "size": 28411520,
  "error": null,
  "batch_id": "batch-9f3a",
  "track_index": 0,
  "retry_count": 0,
  "metadata_enhanced": false,
  "status_change_time": "2026-09-30 08:02:11"
}
\`\`\`

\`progress\` defaults to \`0\`, \`retry_count\` to \`0\`, \`metadata_enhanced\` to
\`false\` when the task does not carry them. Track/artist/album names fall back
through several task fields and may be \`null\` for tasks that never resolved
metadata.

**Errors**

| Status | Code | Meaning |
|--------|------|---------|
| 501 | \`NOT_AVAILABLE\` | Download tracking not available (\`"Download tracking not available."\`). |
| 500 | \`DOWNLOAD_ERROR\` | Unexpected failure (message carries the detail). |

**Example**

\`\`\`bash
curl "http://localhost:8008/api/v1/downloads?status=downloading,queued&limit=20" \\
  -H "Authorization: Bearer $SOULSYNC_API_KEY"
\`\`\`

**Notes**

- For push updates instead of polling, subscribe to the \`downloads:batch_update\`
  real-time event (see Real-time Events in the docs) — verified to be emitted
  by the server.

---

### \`POST /api/v1/downloads/{download_id}/cancel\`

Cancel one specific download. The download is removed from the queue
(\`remove=True\` in the orchestrator call).

**Path parameters**

| Name | Type | Required | Default | Description |
|------|------|----------|---------|-------------|
| download_id | string | yes | — | The task id as returned by \`GET /downloads\` (\`id\` field). |

**Request body**

| Name | Type | Required | Default | Description |
|------|------|----------|---------|-------------|
| username | string | yes | — | The peer/service username that owns the download. Missing or empty is a \`400\`. |

**Response**

Success \`data\` is \`{"message": "Download cancelled."}\`.

**Errors**

| Status | Code | Meaning |
|--------|------|---------|
| 400 | \`BAD_REQUEST\` | Missing \`username\` (\`"Missing 'username' in body."\`). |
| 503 | \`NOT_AVAILABLE\` | No download orchestrator configured (\`"Soulseek client not configured."\`). |
| 500 | \`CANCEL_FAILED\` | The orchestrator reported the cancel did not take (\`"Failed to cancel download."\`). |
| 500 | \`DOWNLOAD_ERROR\` | Unexpected failure. |

**Example**

\`\`\`bash
curl -X POST http://localhost:8008/api/v1/downloads/d8f2a1c4-.../cancel \\
  -H "Authorization: Bearer $SOULSYNC_API_KEY" \\
  -H "Content-Type: application/json" \\
  -d '{"username": "somepeer"}'
\`\`\`

---

### \`POST /api/v1/downloads/cancel-all\`

Cancel all active downloads and clear completed ones. Takes no parameters and
no body.

**Response**

Success \`data\` is \`{"message": "All downloads cancelled and cleared."}\`.

**Errors**

| Status | Code | Meaning |
|--------|------|---------|
| 503 | \`NOT_AVAILABLE\` | No download orchestrator configured (\`"Soulseek client not configured."\`). |
| 500 | \`DOWNLOAD_ERROR\` | Unexpected failure. |

**Example**

\`\`\`bash
curl -X POST http://localhost:8008/api/v1/downloads/cancel-all \\
  -H "Authorization: Bearer $SOULSYNC_API_KEY"
\`\`\`

**Notes**

- This is destructive and immediate: in-flight transfers are cancelled and
  finished tasks are cleared from tracking. There is no undo.

---

### \`GET /api/v1/downloads/failed-blocklist\`

List the persistent failed-download blocklist, newest first. These are files
that **terminally failed import after every quarantine retry** — the import
gave up for good and the file's fingerprint is blocked for 90 days so the
next search does not pick the same poisoned file again. This is separate from
the user's own download blocklist (files they flagged as bad matches) and
separate from the quarantine (files parked for review).

**Query parameters**

| Name | Type | Required | Default | Description |
|------|------|----------|---------|-------------|
| limit | integer | no | \`200\` | Max entries. Clamped to 1–1000. A non-numeric value falls back to \`200\`. |

**Response**

\`data\` is \`{"entries": [...]}\`. Each entry:

\`\`\`json
{
  "fingerprint": "9f3ac21d...",
  "service": "soulseek",
  "artist": "Miles Davis",
  "title": "So What",
  "size_bytes": 28411520,
  "reason": "import failed: ...",
  "created_at": "2026-09-28 14:22:03",
  "expires_at": "2026-12-27 14:22:03"
}
\`\`\`

The fingerprint is the SHA1 of \`service | normalized artist | normalized
title | size\`. Soulseek peers collapse to the service \`soulseek\` — a username
is a peer, not a source.

**Errors**

| Status | Code | Meaning |
|--------|------|---------|
| 500 | \`BLOCKLIST_ERROR\` | Blocklist read failed (message carries the detail). Reads fail open elsewhere in the app, but the endpoint still reports the error. |

**Example**

\`\`\`bash
curl "http://localhost:8008/api/v1/downloads/failed-blocklist?limit=50" \\
  -H "Authorization: Bearer $SOULSYNC_API_KEY"
\`\`\`

---

### \`DELETE /api/v1/downloads/failed-blocklist\`

Remove one fingerprint from the failed-download blocklist, allowing that file
to be picked again by future searches.

**Request body**

| Name | Type | Required | Default | Description |
|------|------|----------|---------|-------------|
| fingerprint | string | yes | — | The entry's \`fingerprint\` as returned by \`GET /downloads/failed-blocklist\`. Missing or empty is a \`400\`. |

**Response**

Success \`data\` is \`{"message": "Entry removed."}\`.

**Errors**

| Status | Code | Meaning |
|--------|------|---------|
| 400 | \`BAD_REQUEST\` | Missing \`fingerprint\` (\`"Missing 'fingerprint' in body."\`). |
| 404 | \`BLOCKLIST_ERROR\` | \`"No blocklist entry with that fingerprint."\` — nothing was deleted. |
| 500 | \`BLOCKLIST_ERROR\` | Unexpected failure. |

**Example**

\`\`\`bash
# copy the fingerprint from GET /downloads/failed-blocklist first
curl -X DELETE http://localhost:8008/api/v1/downloads/failed-blocklist \\
  -H "Authorization: Bearer $SOULSYNC_API_KEY" \\
  -H "Content-Type: application/json" \\
  -d '{"fingerprint": "<fingerprint-from-the-listing>"}'
\`\`\`
`
        },
        {
            id: 'api-playlists',
            title: 'Playlists',
            lede: 'List your playlists, inspect one, or trigger a sync on demand.',
            body: `
# Playlists API — draft

Base path: \`/api/v1\`. Every endpoint requires an API key, sent as an
\`Authorization: Bearer sk_...\` header (preferred) or as a \`?api_key=\` query
parameter. An API key acts with admin rights.

All responses use the standard envelope:

\`\`\`json
{
  "success": true,
  "data": { "...": "..." },
  "error": null,
  "pagination": null
}
\`\`\`

On errors: \`{"success": false, "data": null, "error": {"code": "PLAYLIST_ERROR", "message": "..."}, "pagination": null}\`.
None of the playlist endpoints are paginated; \`pagination\` is always \`null\`.

These endpoints proxy the connected music providers (Spotify / Tidal) — they
read the provider's data live, not the SoulSync library. Profile scoping does
not apply.

Cross-cutting facts:

- **Rate limit:** 60 requests/minute per IP; exceeding it returns \`429\` with
  code \`RATE_LIMITED\`.

### \`GET /api/v1/playlists\`

List the authenticated user's playlists from the connected provider, read live
(not from the SoulSync library). Not paginated.

**Query parameters**

| Name | Type | Required | Default | Description |
|------|------|----------|---------|-------------|
| source | string | no | \`spotify\` | \`spotify\` or \`tidal\`. Anything else is a \`400\`. |

**Response**

\`data.playlists\` is an array; \`data.source\` echoes the provider used.

Spotify items:

\`\`\`json
{
  "success": true,
  "data": {
    "playlists": [
      {
        "id": "37i9dQZF1DXcBWIGoYBM5M",
        "name": "My Playlist",
        "owner": "someuser",
        "track_count": 42,
        "image_url": "https://..."
      }
    ],
    "source": "spotify"
  },
  "error": null,
  "pagination": null
}
\`\`\`

Tidal items have the same shape minus \`owner\` (\`id\` falls back to \`uuid\`,
\`name\` falls back to \`title\`, \`track_count\` defaults to \`0\`, \`image_url\` may
be \`null\`).

**Errors**

| Code | HTTP | Meaning |
|------|------|---------|
| NOT_AUTHENTICATED | 401 | Spotify is not authenticated (\`source=spotify\`). |
| NOT_AVAILABLE | 503 | Tidal client is not configured (\`source=tidal\`). |
| BAD_REQUEST | 400 | \`source\` is not \`spotify\` or \`tidal\`. |
| PLAYLIST_ERROR | 500 | Unexpected failure. |

**Example**

\`\`\`bash
curl -H "Authorization: Bearer sk_..." \\
  "http://localhost:8008/api/v1/playlists?source=spotify"
\`\`\`

### \`GET /api/v1/playlists/{playlist_id}\`

Get one playlist with its tracks, read live from the provider. Only
\`source=spotify\` is supported.

**Path parameters**

| Name | Type | Required | Description |
|------|------|----------|-------------|
| playlist_id | string | yes | The provider's playlist id. |

**Query parameters**

| Name | Type | Required | Default | Description |
|------|------|----------|---------|-------------|
| source | string | no | \`spotify\` | Must be \`spotify\`; any other value (including \`tidal\`) is a \`400\`. |

**Response**

\`\`\`json
{
  "success": true,
  "data": {
    "playlist": {
      "id": "37i9dQZF1DXcBWIGoYBM5M",
      "name": "My Playlist",
      "owner": "someuser",
      "total_tracks": 42,
      "tracks": [
        {
          "id": "6rqhFgbbKwnb9MLmUQDw0G",
          "name": "Midnight City",
          "artists": ["M83"],
          "album": "Hurry Up, We're Dreaming",
          "duration_ms": 243000,
          "image_url": "https://..."
        }
      ]
    },
    "source": "spotify"
  },
  "error": null,
  "pagination": null
}
\`\`\`

\`image_url\` is the album's first image or \`null\` when the album has no images.
Track entries whose payload is missing are skipped.

**Errors**

| Code | HTTP | Meaning |
|------|------|---------|
| NOT_AUTHENTICATED | 401 | Spotify is not authenticated. |
| NOT_FOUND | 404 | No playlist with that id. |
| BAD_REQUEST | 400 | \`source\` is not \`spotify\`. |
| PLAYLIST_ERROR | 500 | Unexpected failure. |

**Example**

\`\`\`bash
curl -H "Authorization: Bearer sk_..." \\
  http://localhost:8008/api/v1/playlists/37i9dQZF1DXcBWIGoYBM5M
\`\`\`

### \`POST /api/v1/playlists/{playlist_id}/sync\`

Start a background sync job for a playlist. The client supplies the playlist
name and the full track list; the server acquires the tracks and pushes the
playlist to the configured media servers (Plex / Jellyfin / Navidrome via
their native add APIs). Returns immediately — the sync runs on a worker thread
(\`api/source_playlists.py:start_playlist_sync_from_payload\`).

**Path parameters**

| Name | Type | Required | Description |
|------|------|----------|-------------|
| playlist_id | string | yes | Key the sync job is tracked under. A second call with the same id while its job is active answers \`409\`. |

**Request body** (JSON)

| Name | Type | Required | Default | Description |
|------|------|----------|---------|-------------|
| playlist_name | string | yes | — | Display name for the synced playlist. |
| tracks | array | yes | — | The full track list to sync (same track-payload shape as \`GET /playlists/<playlist_id>\` returns). Must be non-empty. |
| image_url | string | no | \`""\` | Cover art URL stored with the sync job. |
| sync_mode | string | no | Settings › Playlist sync mode (\`replace\` if unset) | \`replace\` — delete the server playlist and recreate it from the source track list; \`append\` — keep user-added tracks already on the server playlist and only add missing ones; \`reconcile\` — supported mode, resolved per \`core/sync/playlist_edit.py:normalize_sync_mode\`. An explicit per-request value wins over the configured default; an unrecognized value falls back to \`replace\`. |

**Response**

\`200\` with \`data.message\` (\`"Playlist sync started."\`) and \`data.playlist_id\`
echoing the path id.

**Errors**

| Code | HTTP | Meaning |
|------|------|---------|
| BAD_REQUEST | 400 | Missing \`playlist_name\` or \`tracks\` in the body. |
| CONFLICT | 409 | A sync is already in progress for this \`playlist_id\`. |
| SYNC_FAILED | 500 (or the underlying status) | The sync failed to start; message carries the detail. |
| PLAYLIST_ERROR | 500 | Unexpected failure. |

**Example**

\`\`\`bash
curl -X POST -H "Authorization: Bearer sk_..." \\
  -H "Content-Type: application/json" \\
  -d '{"playlist_name": "My Playlist", "tracks": [{"id": "6rqhFgbbKwnb9MLmUQDw0G", "name": "Midnight City", "artists": ["M83"]}], "sync_mode": "append"}' \\
  http://localhost:8008/api/v1/playlists/37i9dQZF1DXcBWIGoYBM5M/sync
\`\`\`

**Notes**

- There is no job id: progress is tracked under the \`playlist_id\` you passed.
  Calling sync again with the same id while the job runs returns \`409\`
  instead of queueing a second job.
`
        },
        {
            id: 'api-watchlist',
            title: 'Watchlist',
            lede: 'Manage the artists SoulSync is watching, and kick off a scan whenever you like.',
            body: `
# Watchlist API — draft

Base path: \`/api/v1\`. Every endpoint requires an API key, sent as an
\`Authorization: Bearer sk_...\` header (preferred) or as a \`?api_key=\` query
parameter. An API key acts with admin rights.

All responses use the standard envelope:

\`\`\`json
{
  "success": true,
  "data": { "...": "..." },
  "error": null,
  "pagination": null
}
\`\`\`

On errors: \`{"success": false, "data": null, "error": {"code": "WATCHLIST_ERROR", "message": "..."}, "pagination": null}\`.
None of the watchlist endpoints are paginated; \`pagination\` is always \`null\`.

Cross-cutting parameters on these endpoints:

- **Profile scoping:** \`X-Profile-Id\` header or \`?profile_id=\` query parameter.
  Defaults to profile 1 when omitted. Values below 1 are treated as 1;
  non-numeric values fall back to 1 (\`api/helpers.py:parse_profile_id\`).
- **Field selection:** \`?fields=artist_name,source\` trims each returned artist
  object to the named fields (\`api/helpers.py:parse_fields\`).
- **Rate limit:** 60 requests/minute per IP; exceeding it returns \`429\` with
  code \`RATE_LIMITED\`.

### \`GET /api/v1/watchlist\`

List all watched artists for the current profile. Not paginated — returns the
full list.

**Query parameters**

| Name | Type | Required | Default | Description |
|------|------|----------|---------|-------------|
| fields | string | no | all fields | Comma-separated field names to keep on each artist object. |
| profile_id | int | no | 1 | Profile scope (or \`X-Profile-Id\` header). |

**Response**

\`data.artists\` is an array of watchlist artist objects
(\`api/serializers.py:serialize_watchlist_artist\`):

\`\`\`json
{
  "id": 12,
  "source": "spotify",
  "spotify_artist_id": "0OdUWJ0sBjDrqHygGUXeCF",
  "itunes_artist_id": null,
  "deezer_artist_id": null,
  "discogs_artist_id": null,
  "musicbrainz_artist_id": null,
  "amazon_artist_id": null,
  "preferred_metadata_source": null,
  "artist_name": "M83",
  "image_url": "https://...",
  "date_added": "2026-09-20T14:00:00",
  "last_scan_timestamp": "2026-09-30T00:00:00",
  "created_at": "2026-09-20T14:00:00",
  "updated_at": "2026-09-29T18:30:00",
  "profile_id": 1,
  "quality_profile_id": 7,
  "include_albums": true,
  "include_eps": true,
  "include_singles": true,
  "include_live": false,
  "include_remixes": false,
  "include_acoustic": false,
  "include_compilations": false
}
\`\`\`

Notes on fields: \`source\` names the provider that owns the artist id, so a
client never has to infer it from the id's shape. Timestamps are ISO-8601
strings or \`null\`. Content-filter defaults are albums/eps/singles on and
live/remixes/acoustic/compilations off.

**Errors**

| Code | HTTP | Meaning |
|------|------|---------|
| WATCHLIST_ERROR | 500 | Database or unexpected failure; message carries the detail. |

**Example**

\`\`\`bash
curl -H "Authorization: Bearer sk_..." \\
  http://localhost:8008/api/v1/watchlist
\`\`\`

### \`POST /api/v1/watchlist\`

Add an artist to the watchlist. If the artist already exists (matched by
provider id, or by name when no id matches), the existing row is updated with
the new source id instead of creating a duplicate
(\`database/music_database.py:add_artist_to_watchlist\`).

**Request body** (JSON)

| Name | Type | Required | Default | Description |
|------|------|----------|---------|-------------|
| artist_id | string | yes | — | Provider artist id. A JSON number is accepted and normalized to text; booleans, lists, objects, and empty/whitespace-only strings are rejected. |
| artist_name | string | yes | — | Artist display name (surrounding whitespace is stripped). |
| source | string | no | legacy id-shape guess | Provider name. Accepted values (case-insensitive): \`spotify\`, \`itunes\`, \`deezer\`, \`discogs\`, \`musicbrainz\`, \`amazon\`. Aliases: \`apple\`, \`apple_music\`, \`applemusic\`, \`itunes_link\` → \`itunes\`; \`spotify_public\` → \`spotify\`; \`musicbrainz_ng\`, \`mb\` → \`musicbrainz\`; \`amazon_music\` → \`amazon\`. Omitting it falls back to the legacy guess (all-digits means iTunes), which cannot tell Deezer/Discogs ids apart — always send it. An unrecognized value is a \`400\`. |
| quality_profile_id | int | no | current global profile for new rows; existing assignment kept for updates | The quality profile new releases will be downloaded/imported against. Must be a positive integer naming an existing profile: unknown or non-positive is a \`400\`, never a silent fallback. |

Profile scoping (\`X-Profile-Id\` / \`?profile_id=\`, default 1) applies.

**Response**

\`201\` with \`data.message\` (\`"Added <name> to watchlist."\`).

**Errors**

| Code | HTTP | Meaning |
|------|------|---------|
| BAD_REQUEST | 400 | Missing \`artist_id\` or \`artist_name\`; unknown \`source\`; \`quality_profile_id\` not a positive integer or unknown. |
| INTERNAL_ERROR | 500 | The database write failed. |
| WATCHLIST_ERROR | 500 | Unexpected failure. |

**Example**

\`\`\`bash
curl -X POST -H "Authorization: Bearer sk_..." \\
  -H "Content-Type: application/json" \\
  -d '{"artist_id": "0OdUWJ0sBjDrqHygGUXeCF", "artist_name": "M83", "source": "spotify", "quality_profile_id": 7}' \\
  http://localhost:8008/api/v1/watchlist
\`\`\`

### \`DELETE /api/v1/watchlist/{artist_id}\`

Remove an artist from the watchlist for the current profile.

**Path parameters**

| Name | Type | Required | Description |
|------|------|----------|-------------|
| artist_id | string | yes | The provider artist id. It is matched against **every** provider id column (spotify, itunes, deezer, discogs, musicbrainz, amazon), so the id works regardless of which provider column holds it (\`core/watchlist_sources.py:artist_id_match_sql\`). |

Profile scoping (\`X-Profile-Id\` / \`?profile_id=\`, default 1) applies.

**Response**

\`200\` with \`data.message\` (\`"Artist removed from watchlist."\`).

**Errors**

| Code | HTTP | Meaning |
|------|------|---------|
| NOT_FOUND | 404 | No watchlist artist with that id for this profile. |
| WATCHLIST_ERROR | 500 | Unexpected failure. |

**Example**

\`\`\`bash
curl -X DELETE -H "Authorization: Bearer sk_..." \\
  http://localhost:8008/api/v1/watchlist/0OdUWJ0sBjDrqHygGUXeCF
\`\`\`

### \`PATCH /api/v1/watchlist/{artist_id}\`

Update content-type filters (and optionally the quality profile) for a watched
artist. This is a true partial update: **only the fields actually sent in the
body are touched** (\`api/watchlist.py:update_watchlist_filters\`).

**Path parameters**

| Name | Type | Required | Description |
|------|------|----------|-------------|
| artist_id | string | yes | The provider artist id, matched against every provider id column (same as DELETE). |

**Request body** (JSON) — any combination of:

| Name | Type | Required | Default | Description |
|------|------|----------|---------|-------------|
| include_albums | boolean | no | unchanged | Watch full albums. |
| include_eps | boolean | no | unchanged | Watch EPs. |
| include_singles | boolean | no | unchanged | Watch singles. |
| include_live | boolean | no | unchanged | Watch live releases. |
| include_remixes | boolean | no | unchanged | Watch remixes. |
| include_acoustic | boolean | no | unchanged | Watch acoustic releases. |
| include_compilations | boolean | no | unchanged | Watch compilations. |
| quality_profile_id | int | no | unchanged | Replacement quality profile id. Same validation as \`POST /watchlist\`: positive integer naming an existing profile, else \`400\`. |

Booleans are strict (\`core/api_validation.py:parse_strict_bool\`): JSON
\`true\`/\`false\`, or the strings \`"true"\`, \`"1"\`, \`"yes"\`, \`"on"\` (true) and
\`"false"\`, \`"0"\`, \`"no"\`, \`"off"\`, \`""\` (false), case-insensitive. Anything
else — including JSON numbers and \`null\` — is a \`400\` naming the offending
field. Sending none of the allowed fields is a \`400\`.

Profile scoping (\`X-Profile-Id\` / \`?profile_id=\`, default 1) applies.

**Response**

\`200\` with \`data.message\` (\`"Watchlist settings updated."\`) and \`data.updated\`
echoing the parsed updates that were applied:

\`\`\`json
{
  "success": true,
  "data": {
    "message": "Watchlist settings updated.",
    "updated": { "include_live": true, "include_remixes": true }
  },
  "error": null,
  "pagination": null
}
\`\`\`

**Errors**

| Code | HTTP | Meaning |
|------|------|---------|
| BAD_REQUEST | 400 | A filter value is not a boolean; \`quality_profile_id\` invalid; or no valid fields were provided. |
| NOT_FOUND | 404 | No watchlist artist with that id for this profile. |
| WATCHLIST_ERROR | 500 | Unexpected failure. |

**Example**

\`\`\`bash
curl -X PATCH -H "Authorization: Bearer sk_..." \\
  -H "Content-Type: application/json" \\
  -d '{"include_live": true, "include_remixes": true}' \\
  http://localhost:8008/api/v1/watchlist/0OdUWJ0sBjDrqHygGUXeCF
\`\`\`

### \`POST /api/v1/watchlist/scan\`

Trigger a watchlist scan for new releases. Starts the app's scan in a
background thread and returns immediately; the request body is ignored
(\`api/artist_watchlist.py:start_watchlist_scan\`).

**Response**

\`200\` with \`data.message\` (\`"Watchlist scan started."\`).

**Errors**

| Code | HTTP | Meaning |
|------|------|---------|
| BAD_REQUEST | 400 | No music provider available (neither Spotify authenticated nor iTunes reachable). |
| CONFLICT | 409 | A watchlist scan is already in progress. |
| WATCHLIST_ERROR | 500 | Could not start the scan. |

**Example**

\`\`\`bash
curl -X POST -H "Authorization: Bearer sk_..." \\
  http://localhost:8008/api/v1/watchlist/scan
\`\`\`

**Notes**

- The scan reads its profile from the request context
  (\`get_current_profile_id()\`); the endpoint takes no profile or body
  parameters. A second call while a scan is active answers \`409\` — there is
  no queue.
`
        },
        {
            id: 'api-wishlist',
            title: 'Wishlist',
            lede: 'The tracks you want but do not have yet — add, remove, and process them via the API.',
            body: `
# Wishlist API — draft

Base path: \`/api/v1\`. Every endpoint requires an API key, sent as an
\`Authorization: Bearer sk_...\` header (preferred) or as a \`?api_key=\` query
parameter. An API key acts with admin rights.

All responses use the standard envelope:

\`\`\`json
{
  "success": true,
  "data": { "...": "..." },
  "error": null,
  "pagination": { "page": 1, "limit": 50, "total": 128, "total_pages": 3, "has_next": true, "has_prev": false }
}
\`\`\`

On errors: \`{"success": false, "data": null, "error": {"code": "WISHLIST_ERROR", "message": "..."}, "pagination": null}\`.
\`pagination\` is populated only on the paginated list endpoint; everywhere else
it is \`null\`.

Cross-cutting parameters on these endpoints:

- **Profile scoping:** \`X-Profile-Id\` header or \`?profile_id=\` query parameter.
  Defaults to profile 1 when omitted. Values below 1 are treated as 1;
  non-numeric values fall back to 1 (\`api/helpers.py:parse_profile_id\`).
- **Field selection:** \`?fields=id,track_name,artist_name\` trims each returned
  object to the named fields (\`api/helpers.py:parse_fields\`).
- **Pagination:** \`?page=\` (default 1, minimum 1) and \`?limit=\` (default 50,
  minimum 1, maximum 200). Non-numeric values fall back to the defaults
  (\`api/helpers.py:parse_pagination\`).
- **Rate limit:** 60 requests/minute per IP; exceeding it returns \`429\` with
  code \`RATE_LIMITED\`.

### \`GET /api/v1/wishlist\`

List wishlist tracks for the current profile, oldest first, with pagination.

**Query parameters**

| Name | Type | Required | Default | Description |
|------|------|----------|---------|-------------|
| category | string | no | none (no filter) | \`"singles"\` or \`"albums"\`. Any other value is ignored and returns all entries. Categorization is derived from the stored provider payload: rows whose \`spotify_data.album.album_type\` is \`"album"\` count as albums, everything else (including rows with no album type) counts as singles. |
| page | int | no | 1 | Page number (minimum 1). |
| limit | int | no | 50 | Results per page (1–200). |
| fields | string | no | all fields | Comma-separated field names to keep on each track object. |
| profile_id | int | no | 1 | Profile scope (or \`X-Profile-Id\` header). |

**Response**

\`data.tracks\` is an array of wishlist track objects; \`pagination\` is populated
(\`page\`, \`limit\`, \`total\`, \`total_pages\`, \`has_next\`, \`has_prev\`). Each track
object has this shape (\`api/serializers.py:serialize_wishlist_track\`):

\`\`\`json
{
  "id": "6rqhFgbbKwnb9MLmUQDw0G",
  "track_id": "6rqhFgbbKwnb9MLmUQDw0G",
  "spotify_track_id": "6rqhFgbbKwnb9MLmUQDw0G",
  "track_name": "Midnight City",
  "artist_name": "M83",
  "album_name": "Hurry Up, We're Dreaming",
  "track_data": { "...": "provider track payload" },
  "spotify_data": { "...": "same payload, legacy alias" },
  "provider": "spotify",
  "failure_reason": "Download failed",
  "retry_count": 0,
  "last_attempted": "2026-09-30T01:00:00",
  "date_added": "2026-09-29T22:14:00",
  "source_type": "api",
  "source_info": null,
  "profile_id": 1,
  "quality_profile_id": 7
}
\`\`\`

Notes on fields: \`id\` is the wishlist row id (a second album for the same track
is stored as \`<id>::<album>\`). \`track_name\` falls back to \`"Unknown"\` when the
payload has no name. \`artist_name\` joins all credited artists with \`", "\`.
\`last_attempted\` / \`date_added\` are ISO-8601 strings or \`null\`.

**Errors**

| Code | HTTP | Meaning |
|------|------|---------|
| WISHLIST_ERROR | 500 | Database or unexpected failure; message carries the detail. |

**Example**

\`\`\`bash
curl -H "Authorization: Bearer sk_..." \\
  "http://localhost:8008/api/v1/wishlist?category=singles&page=1&limit=25"
\`\`\`

### \`POST /api/v1/wishlist\`

Add a track to the wishlist. Because an API caller is explicit user intent, the
add bypasses the wishlist ignore-list gate and clears any stale ignore entry
for the track, and an existing row is updated authoritatively rather than
silently dropped (\`api/wishlist.py:add_to_wishlist\`,
\`database/music_database.py:add_to_wishlist_detailed\`).

**Request body** (JSON)

| Name | Type | Required | Default | Description |
|------|------|----------|---------|-------------|
| track_data | object | yes | — | Provider track payload. Must be an object containing an \`id\` field. The legacy alias \`spotify_track_data\` is accepted in its place. |
| failure_reason | string | no | \`"Added via API"\` | Stored on the row as the reason it is on the wishlist. |
| source_type | string | no | \`"api"\` | Provenance label stored on the row. |
| quality_profile_id | int | no | app-wide default | The quality profile this item will be downloaded/imported against. Must be a positive integer naming an existing profile: an unknown or non-positive id is a \`400\` — it never silently falls back to the default. When the track is already on the wishlist, an explicit id overwrites the stored one. |

The \`track_data.id\` must be present; a payload without an id is rejected with
\`400\`. Podcast items (track id starting with \`podcast-\`, or
\`source_type: "podcast"\`) are not eligible for the music wishlist and are
rejected with \`400\`. Blocklisted tracks are skipped (\`409\`).

**Response**

- \`201\` — a new row was created. \`data.created\` is \`true\`.
- \`200\` — the wishlist already reflected the request (existing row refreshed,
  or already covered, e.g. by a manual library match). \`data.created\` is
  \`false\`.

Both carry \`data.track\` (the stored, serialized row) so the client can verify
what landed, and \`data.message\`.

\`\`\`json
{
  "success": true,
  "data": {
    "message": "Track added to wishlist.",
    "created": true,
    "track": { "id": "6rqhFgbbKwnb9MLmUQDw0G", "track_name": "Midnight City", "...": "..." }
  },
  "error": null,
  "pagination": null
}
\`\`\`

**Errors**

| Code | HTTP | Meaning |
|------|------|---------|
| BAD_REQUEST | 400 | Missing/non-object \`track_data\`; missing track id; podcast item; \`quality_profile_id\` not a positive integer or unknown. |
| CONFLICT | 409 | Nothing was written — e.g. the track is blocklisted or a duplicate with nothing authoritative to apply. |
| WISHLIST_ERROR | 500 | Unexpected failure. |

**Example**

\`\`\`bash
curl -X POST -H "Authorization: Bearer sk_..." \\
  -H "Content-Type: application/json" \\
  -d '{"track_data": {"id": "6rqhFgbbKwnb9MLmUQDw0G", "name": "Midnight City", "artists": [{"name": "M83"}], "album": {"name": "Hurry Up, We'"'"'re Dreaming"}}, "quality_profile_id": 7}' \\
  http://localhost:8008/api/v1/wishlist
\`\`\`

**Notes**

- To target a profile other than 1, send \`X-Profile-Id\` (or \`?profile_id=\`).
  The response's \`track\` is read back using the id that was actually written
  (\`<id>::<album>\` for second-album rows), so it always reflects the stored row.

### \`DELETE /api/v1/wishlist/{track_id}\`

Remove a track from the wishlist for the current profile.

**Path parameters**

| Name | Type | Required | Description |
|------|------|----------|-------------|
| track_id | string | yes | The wishlist row \`id\` as returned by \`GET /wishlist\` or \`POST /wishlist\` (may be a composite \`<track_id>::<album>\` for second-album rows). |

Profile scoping (\`X-Profile-Id\` / \`?profile_id=\`, default 1) applies.

**Response**

\`200\` with \`data.message\` (\`"Track removed from wishlist."\`).

**Errors**

| Code | HTTP | Meaning |
|------|------|---------|
| NOT_FOUND | 404 | No wishlist row with that id for this profile. |
| WISHLIST_ERROR | 500 | Unexpected failure. |

**Example**

\`\`\`bash
curl -X DELETE -H "Authorization: Bearer sk_..." \\
  http://localhost:8008/api/v1/wishlist/6rqhFgbbKwnb9MLmUQDw0G
\`\`\`

### \`POST /api/v1/wishlist/process\`

Trigger wishlist download processing. Starts the app's wishlist processing in a
background thread and returns immediately — it does not wait for downloads to
finish (\`core/wishlist/routes.py:process_wishlist_api\`). The request body is
ignored.

**Response**

\`200\` with \`data.message\` (\`"Wishlist processing started."\`).

**Errors**

| Code | HTTP | Meaning |
|------|------|---------|
| CONFLICT | 409 | Wishlist processing is already running. |
| NOT_AVAILABLE | 503 | Wishlist processing is not wired up in this install. |
| WISHLIST_ERROR | 500 | Could not start processing. |

**Example**

\`\`\`bash
curl -X POST -H "Authorization: Bearer sk_..." \\
  http://localhost:8008/api/v1/wishlist/process
\`\`\`

**Notes**

- Poll \`GET /api/v1/wishlist\` (or watch the \`wishlist:stats\` real-time event)
  to observe entries being acquired. There is no job id to track; a second
  call while processing is active answers \`409\`.
`
        },
        {
            id: 'api-request',
            title: 'Requests',
            lede: 'Submit a download request and check on it later — the API behind the request flow.',
            body: `
# Requests API — draft

All endpoints live under \`/api/v1\` and require an API key: send it as an
\`Authorization: Bearer sk_...\` header (preferred) or as an \`?api_key=sk_...\`
query parameter. Every response uses the standard envelope
\`{"success": bool, "data": ..., "error": {"code","message"}|null, "pagination": ...|null}\`.
Rate limit: 60 requests/minute per IP (\`429\` / \`RATE_LIMITED\`).
These endpoints do not support \`?fields=\`.

This is the inbound music-request flow used by external sources (Discord
bots, curl, automation): submit a free-text query, SoulSync searches,
matches, and downloads in the background, and you poll for the outcome —
or give it a \`notify_url\` and it will POST you when the request settles.

### \`POST /api/v1/request\`

Accept a music search query and trigger the search → match → download
pipeline in a background thread. Returns immediately with \`202\` and a
\`request_id\`; poll \`GET /request/{request_id}\` (or set \`notify_url\`) for the
outcome.

**Request body**

| Name | Type | Required | Default | Description |
|------|------|----------|---------|-------------|
| query | string | yes | — | Free-text search query, e.g. \`"Miles Davis - So What"\`. Blank or missing is a \`400\`. |
| title | string | no | — | Expected track title. Explicit \`title\`/\`artist\` win over query parsing. |
| artist | string | no | — | Expected artist name. |
| duration_ms | integer | no | \`0\` | Expected duration in milliseconds. Non-numeric values become \`0\`; negative values are floored at \`0\`. |
| notify_url | string | no | — | \`http(s)\` URL the server POSTs the terminal request record to (10s timeout). Must start with \`http://\` or \`https://\` — anything else is a \`400\`. |
| metadata | object | no | \`{}\` | Passthrough dict included in the automation engine's \`webhook_received\` event. Not stored on the request record and not sent to \`notify_url\`. |

How the expected match is derived (so search results are scored instead of
blindly taking the top hit): explicit \`title\`/\`artist\` are used as-is;
otherwise a query containing \`" - "\` is split on the **first** \`" - "\` into
artist and title. If no title can be determined at all, no expected-match
constraint is applied and the top search hit wins.

**Response**

HTTP \`202\`. \`data\`:

\`\`\`json
{
  "request_id": "3fa85f64-5717-4562-b3fc-2c963f66afa6",
  "status": "queued",
  "query": "Miles Davis - So What"
}
\`\`\`

**Request lifecycle**

\`queued\` → \`searching\` → \`downloading\` → one of \`completed\`, \`not_found\`,
\`failed\`.

- \`not_found\`: the search returned no match (\`error\` is \`"No match found"\`).
- \`failed\`: an exception during the pipeline (\`error\` carries the message),
  or no download source is configured (\`error\` is \`"Download source not configured"\`).
- \`downloading\` is transitional: a shared background watcher (sweeps every
  10s, one bulk transfer read for all in-flight requests) settles it to
  \`completed\` or \`failed\` (\`error\` is \`"Download {failed|cancelled}: {state}"\`).

**Errors**

| Status | Code | Meaning |
|--------|------|---------|
| 400 | \`BAD_REQUEST\` | Missing/blank \`query\` (\`"Missing 'query' in request body."\`), or \`notify_url\` is not an \`http(s)\` URL (\`"notify_url must be an http(s) url."\`). |

**Example**

\`\`\`bash
curl -X POST http://localhost:8008/api/v1/request \\
  -H "Authorization: Bearer $SOULSYNC_API_KEY" \\
  -H "Content-Type: application/json" \\
  -d '{"query": "Miles Davis - So What", "notify_url": "https://example.com/soulsync-hook"}'
\`\`\`

**Notes**

- Request records live **in memory only** and expire **1 hour** after
  creation (a cleanup runs on every new request plus a background sweep every
  5 minutes). After expiry — or after a server restart — the \`request_id\` is
  gone and status polling returns \`404\`. Do not treat \`request_id\` as durable.
- Every submission emits a \`webhook_received\` automation-engine event shaped
  like \`{"query", "request_id", "source": "api", "metadata", "download_started_by": "api_request"}\`.
  Automations listening for webhook receipts will fire on API requests too.
- The \`notify_url\` callback fires on every terminal state (\`completed\`,
  \`not_found\`, \`failed\`) — and on the \`timed_out\` settle described below.
  Its JSON payload is the request record **minus** \`created_at\`,
  \`notify_url\`, and \`watch_until\`.

---

### \`GET /api/v1/request/{request_id}\`

Check the status of a previously submitted music request.

**Path parameters**

| Name | Type | Required | Default | Description |
|------|------|----------|---------|-------------|
| request_id | string | yes | — | The UUID returned by \`POST /request\`. |

**Response**

\`data\`:

\`\`\`json
{
  "request_id": "3fa85f64-5717-4562-b3fc-2c963f66afa6",
  "query": "Miles Davis - So What",
  "status": "downloading",
  "download_id": "d8f2a1c4-...",
  "error": null,
  "completed_at": null,
  "timed_out": false
}
\`\`\`

- \`status\`: one of \`queued\`, \`searching\`, \`downloading\`, \`completed\`,
  \`not_found\`, \`failed\`.
- \`download_id\`: the download task id once the transfer has been handed off
  (see \`GET /downloads\`); \`null\` before that.
- \`error\`: set on \`not_found\` / \`failed\`, otherwise \`null\`.
- \`completed_at\`: ISO timestamp when the request reached a terminal state, or
  \`null\` while in flight.
- \`timed_out\`: \`true\` when the transfer was still running after the
  **15-minute watch window** while \`status\` stayed \`downloading\`. This is
  deliberately not a terminal state: the endpoint stopped watching, which is
  not the same as the download stopping — the download may still be running.
  \`completed_at\` is set on timeout and the \`notify_url\` callback fires with
  \`timed_out: true\`.

**Errors**

| Status | Code | Meaning |
|--------|------|---------|
| 404 | \`NOT_FOUND\` | \`"Request not found or expired."\` — unknown id, older than the 1-hour TTL, or lost in a restart. |

**Example**

\`\`\`bash
curl http://localhost:8008/api/v1/request/3fa85f64-5717-4562-b3fc-2c963f66afa6 \\
  -H "Authorization: Bearer $SOULSYNC_API_KEY"
\`\`\`

**Notes**

- Polling cadence is up to you, but faster than every few seconds is
  pointless: the server-side watcher itself only re-evaluates transfers every
  10 seconds.
- \`timed_out: true\` with \`status: downloading\` is your signal to check
  \`GET /downloads\` (or the \`download_id\`) directly — the request tracker has
  handed off responsibility.
`
        },
        {
            id: 'api-discover',
            title: 'Discover',
            lede: 'Programmatic access to the discovery pool, similar artists, and new releases.',
            body: `
# Discover API — draft documentation

Base: \`/api/v1\`. All endpoints require an API key (\`Authorization: Bearer sk_...\` header or \`?api_key=\` query parameter). All responses use the standard envelope \`{"success", "data", "error", "pagination"}\`. Rate limit: 60 requests/minute per IP (\`429\` / \`RATE_LIMITED\`).

The five \`GET\` endpoints that read per-profile discovery state (\`/discover/pool\`, \`/discover/similar-artists\`, \`/discover/recent-releases\`, \`/discover/pool/metadata\`, \`/discover/bubbles\`, \`/discover/bubbles/<snapshot_type>\`) are profile-scoped: send an \`X-Profile-Id\` header or \`?profile_id=\` query parameter (defaults to profile 1 when omitted).

### \`GET /api/v1/discover/pool\`

Returns the current discovery pool — tracks surfaced by the discovery engine for the active profile, newest first (\`added_date\` descending).

**Query parameters**

| Name | Type | Required | Default | Description |
|------|------|----------|---------|-------------|
| \`new_releases_only\` | string | No | \`"false"\` | Set to \`"true"\` (case-insensitive) to return only rows flagged as new releases |
| \`source\` | string | No | all | Filter by provider: \`"spotify"\` or \`"itunes"\`. Any other value is a \`400\` |
| \`page\` | int | No | \`1\` | Page number (clamped to ≥ 1; non-numeric falls back to 1) |
| \`limit\` | int | No | \`100\` | Tracks per page (clamped to 1–500; non-numeric falls back to 100) |
| \`fields\` | string | No | — | Comma-separated field names to keep in each track object |
| \`profile_id\` | int | No | \`1\` | Profile scope (or \`X-Profile-Id\` header) |

**Response**

\`data.tracks\` is an array of discovery-track objects; \`pagination\` is populated (page, limit, total, total_pages, has_next, has_prev).

\`\`\`json
{
  "success": true,
  "data": {
    "tracks": [
      {
        "id": 42,
        "spotify_track_id": "4uLU6hMCjMI75M1A2tKUQ",
        "spotify_album_id": "6JWc4iAiJ9hvRiJ6Z8KQ",
        "spotify_artist_id": "3fMbdgg4jU18AjLCKBh",
        "itunes_track_id": null,
        "itunes_album_id": null,
        "itunes_artist_id": null,
        "source": "spotify",
        "track_name": "Blue in Green",
        "artist_name": "Miles Davis",
        "album_name": "Kind of Blue",
        "album_cover_url": "https://i.scdn.co/image/abc123",
        "duration_ms": 327280,
        "popularity": 72,
        "release_date": "1959-08-17",
        "is_new_release": false,
        "artist_genres": ["jazz", "cool jazz"],
        "added_date": "2026-09-28T14:02:11"
      }
    ]
  },
  "error": null,
  "pagination": { "page": 1, "limit": 100, "total": 312, "total_pages": 4, "has_next": true, "has_prev": false }
}
\`\`\`

**Errors**

| Code | Status | When |
|------|--------|------|
| \`BAD_REQUEST\` | 400 | \`source\` is set to something other than \`spotify\`/\`itunes\` |
| \`DISCOVER_ERROR\` | 500 | Database or unexpected failure |

**Example**

\`\`\`bash
curl -H "Authorization: Bearer sk_..." \\
  "http://localhost:8008/api/v1/discover/pool?new_releases_only=true&source=spotify&limit=50"
\`\`\`

---

### \`GET /api/v1/discover/similar-artists\`

Returns the top similar artists discovered from the watchlist for the active profile, ranked by the discovery engine.

**Query parameters**

| Name | Type | Required | Default | Description |
|------|------|----------|---------|-------------|
| \`limit\` | int | No | \`50\` | Max artists (clamped to 1–200; non-numeric falls back to 50) |
| \`fields\` | string | No | — | Comma-separated field names to keep in each artist object |
| \`profile_id\` | int | No | \`1\` | Profile scope (or \`X-Profile-Id\` header) |

**Response**

\`data.artists\` is an array of similar-artist objects. This endpoint is **not paginated** — \`pagination\` is \`null\`.

\`\`\`json
{
  "success": true,
  "data": {
    "artists": [
      {
        "id": 7,
        "source_artist_id": 12,
        "similar_artist_spotify_id": "3fMbdgg4jU18AjLCKBh",
        "similar_artist_itunes_id": null,
        "similar_artist_musicbrainz_id": "dbd3c3a8-6d1e-4c8b-bb6f-5a2e8f4c1a2b",
        "similar_artist_name": "John Coltrane",
        "similarity_rank": 1,
        "occurrence_count": 14,
        "last_updated": "2026-09-29T08:15:00",
        "last_featured": "2026-09-20T11:00:00"
      }
    ]
  },
  "error": null,
  "pagination": null
}
\`\`\`

**Errors**

| Code | Status | When |
|------|--------|------|
| \`DISCOVER_ERROR\` | 500 | Database or unexpected failure |

**Example**

\`\`\`bash
curl -H "Authorization: Bearer sk_..." \\
  "http://localhost:8008/api/v1/discover/similar-artists?limit=20"
\`\`\`

---

### \`GET /api/v1/discover/recent-releases\`

Lists recent releases from watched artists for the active profile.

**Query parameters**

| Name | Type | Required | Default | Description |
|------|------|----------|---------|-------------|
| \`limit\` | int | No | \`50\` | Max releases (clamped to 1–200; non-numeric falls back to 50) |
| \`fields\` | string | No | — | Comma-separated field names to keep in each release object |
| \`profile_id\` | int | No | \`1\` | Profile scope (or \`X-Profile-Id\` header) |

**Response**

\`data.releases\` is an array of recent-release objects. This endpoint is **not paginated** — \`pagination\` is \`null\`.

\`\`\`json
{
  "success": true,
  "data": {
    "releases": [
      {
        "id": 3,
        "watchlist_artist_id": 12,
        "album_spotify_id": "6JWc4iAiJ9hvRiJ6Z8KQ",
        "album_itunes_id": null,
        "source": "spotify",
        "album_name": "Kind of Blue",
        "release_date": "1959-08-17",
        "album_cover_url": "https://i.scdn.co/image/abc123",
        "track_count": 5,
        "added_date": "2026-09-28T14:02:11"
      }
    ]
  },
  "error": null,
  "pagination": null
}
\`\`\`

**Errors**

| Code | Status | When |
|------|--------|------|
| \`DISCOVER_ERROR\` | 500 | Database or unexpected failure |

**Example**

\`\`\`bash
curl -H "Authorization: Bearer sk_..." \\
  "http://localhost:8008/api/v1/discover/recent-releases?limit=20"
\`\`\`

---

### \`GET /api/v1/discover/pool/metadata\`

Returns pool-level metadata for the active profile: when the discovery pool was last populated and how many tracks it holds. Useful as a cheap freshness check before pulling the full pool.

**Query parameters**

| Name | Type | Required | Default | Description |
|------|------|----------|---------|-------------|
| \`profile_id\` | int | No | \`1\` | Profile scope (or \`X-Profile-Id\` header) |

**Response**

\`pagination\` is \`null\`. When the pool has never been populated for the profile, all three values come back as \`last_populated: null\`, \`track_count: 0\`, \`updated_at: null\` (still \`success: true\`, not a 404).

\`\`\`json
{
  "success": true,
  "data": {
    "last_populated": "2026-09-29T06:00:00",
    "track_count": 312,
    "updated_at": "2026-09-29T06:00:05"
  },
  "error": null,
  "pagination": null
}
\`\`\`

**Errors**

| Code | Status | When |
|------|--------|------|
| \`DISCOVER_ERROR\` | 500 | Database or unexpected failure |

**Example**

\`\`\`bash
curl -H "Authorization: Bearer sk_..." \\
  "http://localhost:8008/api/v1/discover/pool/metadata"
\`\`\`

---

### \`GET /api/v1/discover/bubbles\`

Returns all discovery bubble snapshots for the active profile in one call — one entry per snapshot type (\`artist_bubbles\`, \`search_bubbles\`, \`discover_downloads\`). Types with no stored snapshot come back as \`null\` rather than erroring. These snapshots back the bubble-chart visualizations in the Discover UI.

**Query parameters**

| Name | Type | Required | Default | Description |
|------|------|----------|---------|-------------|
| \`profile_id\` | int | No | \`1\` | Profile scope (or \`X-Profile-Id\` header) |

**Response**

\`data.snapshots\` is an object keyed by snapshot type. Each present snapshot is \`{"data": <opaque JSON bubble-chart payload>, "timestamp": "<when it was captured>"}\`. \`pagination\` is \`null\`.

\`\`\`json
{
  "success": true,
  "data": {
    "snapshots": {
      "artist_bubbles": { "data": { "...": "..." }, "timestamp": "2026-09-29T06:05:00" },
      "search_bubbles": null,
      "discover_downloads": { "data": { "...": "..." }, "timestamp": "2026-09-29T06:05:00" }
    }
  },
  "error": null,
  "pagination": null
}
\`\`\`

**Errors**

| Code | Status | When |
|------|--------|------|
| \`DISCOVER_ERROR\` | 500 | Database or unexpected failure |

**Example**

\`\`\`bash
curl -H "Authorization: Bearer sk_..." \\
  "http://localhost:8008/api/v1/discover/bubbles"
\`\`\`

**Notes**

- The \`data\` blob inside each snapshot is opaque UI payload — its internal structure is not part of the API contract.

---

### \`GET /api/v1/discover/bubbles/{snapshot_type}\`

Returns a single bubble snapshot for the active profile.

**Path parameters**

| Name | Type | Required | Description |
|------|------|----------|-------------|
| \`snapshot_type\` | string | Yes | One of \`artist_bubbles\`, \`search_bubbles\`, \`discover_downloads\` |

**Query parameters**

| Name | Type | Required | Default | Description |
|------|------|----------|---------|-------------|
| \`profile_id\` | int | No | \`1\` | Profile scope (or \`X-Profile-Id\` header) |

**Response**

\`data.snapshot\` is \`{"data": <opaque JSON bubble-chart payload>, "timestamp": "<when it was captured>"}\`. \`pagination\` is \`null\`.

\`\`\`json
{
  "success": true,
  "data": {
    "snapshot": {
      "data": { "...": "..." },
      "timestamp": "2026-09-29T06:05:00"
    }
  },
  "error": null,
  "pagination": null
}
\`\`\`

**Errors**

| Code | Status | When |
|------|--------|------|
| \`BAD_REQUEST\` | 400 | \`snapshot_type\` is not one of the three valid types |
| \`NOT_FOUND\` | 404 | No snapshot of that type is stored for the profile |
| \`DISCOVER_ERROR\` | 500 | Database or unexpected failure |

**Example**

\`\`\`bash
curl -H "Authorization: Bearer sk_..." \\
  "http://localhost:8008/api/v1/discover/bubbles/artist_bubbles"
\`\`\`
`
        },
        {
            id: 'api-profiles',
            title: 'Profiles',
            lede: 'Create, read, update, and delete user profiles — full CRUD under /profiles.',
            body: `
# Profiles API — draft documentation

> All endpoints live under \`/api/v1\` and require an API key: send it as an
> \`Authorization: Bearer <key>\` header (preferred) or as \`?api_key=<key>\`.
> Examples below assume \`API_KEY\` is set in the environment and the server is
> at \`http://localhost:8008\`. Every response uses the standard envelope
> \`{"success": true|false, "data": {...}|null, "error": {"code","message"}|null,
> "pagination": {...}|null}\`. \`pagination\` is \`null\` on all profile endpoints
> (none are paginated). Rate limit: 60 requests/minute per IP (\`429\`, code
> \`RATE_LIMITED\`).

## Endpoint reference

### The profile object

\`GET\` endpoints return profile objects with this shape. The raw PIN is never
returned — only the \`has_pin\` flag. Field presence for the permission/service
flags depends on the install's schema age; missing columns read as the
defaults shown.

| Field | Type | Description |
|-------|------|-------------|
| \`id\` | int | Profile ID. \`1\` is the default admin profile |
| \`name\` | string | Display name (unique) |
| \`avatar_color\` | string | Hex color for the avatar, e.g. \`"#6366f1"\` |
| \`avatar_url\` | string \\| null | Custom avatar image URL |
| \`is_admin\` | bool | Admin privileges |
| \`has_pin\` | bool | Whether a PIN is set (the PIN hash itself is never exposed) |
| \`has_password\` | bool | Whether a password is set |
| \`has_recovery\` | bool | Whether a recovery answer is set |
| \`recovery_question\` | string \\| null | Recovery question text |
| \`home_page\` | string \\| null | Profile's home page override |
| \`allowed_pages\` | array \\| null | Page allow-list (\`null\` = unrestricted) |
| \`allowed_sides\` | string | \`"music"\`, \`"video"\`, or \`"both"\`. Resolves to \`"both"\` for admins and defaults to \`"music"\` for non-admins |
| \`can_download\` | bool | Whether the profile may download (default \`true\`) |
| \`has_listenbrainz\` | bool | Whether a ListenBrainz token is configured |
| \`listenbrainz_username\` | string \\| null | ListenBrainz username |
| \`library_mode\` | string | \`"shared"\` (default) or a private-library mode |
| \`library_root\` | string \\| null | Private library root when not shared |
| \`request_limit\` | int | Music-request quota (default \`0\` = unlimited) |
| \`request_limit_days\` | int | Window in days for the request quota (default \`7\`) |
| \`hide_explicit\` | bool | Hide explicit content for this profile |
| \`max_rating\` | string \\| null | Max content rating allowed |
| \`disabled\` | bool | Whether the profile is disabled |
| \`created_at\` | string | Creation timestamp |
| \`updated_at\` | string | Last-update timestamp |

Note: \`?fields=\` trimming and \`?page=\`/\`?limit=\` pagination are **not**
supported on any profile endpoint.

---

### \`GET /api/v1/profiles\`

List all profiles, ordered by ID. Returns the full list — this endpoint is not
paginated.

**Response** — \`data.profiles\` is an array of profile objects (see above).

\`\`\`json
{
  "success": true,
  "data": {
    "profiles": [
      {
        "id": 1,
        "name": "Admin",
        "avatar_color": "#6366f1",
        "avatar_url": null,
        "is_admin": true,
        "has_pin": false,
        "can_download": true,
        "allowed_sides": "both",
        "library_mode": "shared"
      }
    ]
  },
  "error": null,
  "pagination": null
}
\`\`\`

**Errors**

| Status | Code | Meaning |
|--------|------|---------|
| 500 | \`PROFILE_ERROR\` | Database failure |

**Example**

\`\`\`bash
curl -H "Authorization: Bearer $API_KEY" \\
  http://localhost:8008/api/v1/profiles
\`\`\`

---

### \`GET /api/v1/profiles/{profile_id}\`

Get a single profile by its numeric ID.

**Path parameters**

| Name | Type | Required | Default | Description |
|------|------|----------|---------|-------------|
| \`profile_id\` | int | Yes | — | Profile ID |

**Response** — \`data.profile\` is one profile object (see above), or \`404\` if
the ID does not exist.

\`\`\`json
{
  "success": true,
  "data": {
    "profile": {
      "id": 2,
      "name": "Family Room",
      "avatar_color": "#22c55e",
      "avatar_url": null,
      "is_admin": false,
      "has_pin": true,
      "can_download": true,
      "allowed_sides": "music",
      "library_mode": "shared"
    }
  },
  "error": null,
  "pagination": null
}
\`\`\`

**Errors**

| Status | Code | Meaning |
|--------|------|---------|
| 404 | \`NOT_FOUND\` | No profile with that ID (\`"Profile 2 not found."\`) |
| 500 | \`PROFILE_ERROR\` | Database failure |

**Example**

\`\`\`bash
curl -H "Authorization: Bearer $API_KEY" \\
  http://localhost:8008/api/v1/profiles/2
\`\`\`

---

### \`POST /api/v1/profiles\`

Create a new profile. The name is trimmed and must be non-empty and unique.

**Request body**

| Name | Type | Required | Default | Description |
|------|------|----------|---------|-------------|
| \`name\` | string | Yes | — | Display name; leading/trailing whitespace is stripped. Empty after stripping is a \`400\` |
| \`avatar_color\` | string | No | \`"#6366f1"\` | Hex color for the avatar |
| \`avatar_url\` | string \\| null | No | \`null\` | Custom avatar image URL |
| \`is_admin\` | bool | No | \`false\` | Grant admin privileges |
| \`pin\` | string | No | — | Optional PIN for profile protection. Stored as a pbkdf2:sha256 hash; never returned by the API |

**Response** — \`201\` with \`data.profile\` set to the newly created profile
object (re-read from the database, so all defaults are resolved).

\`\`\`json
{
  "success": true,
  "data": {
    "profile": {
      "id": 3,
      "name": "Kids Room",
      "avatar_color": "#f59e0b",
      "avatar_url": null,
      "is_admin": false,
      "has_pin": false,
      "can_download": true,
      "allowed_sides": "music",
      "library_mode": "shared"
    }
  },
  "error": null,
  "pagination": null
}
\`\`\`

**Errors**

| Status | Code | Meaning |
|--------|------|---------|
| 400 | \`BAD_REQUEST\` | Missing or blank \`name\` (\`"Missing 'name' in body."\`) |
| 409 | \`CONFLICT\` | A profile with that name already exists |
| 500 | \`PROFILE_ERROR\` | Database failure |

**Example**

\`\`\`bash
curl -X POST -H "Authorization: Bearer $API_KEY" \\
  -H "Content-Type: application/json" \\
  -d '{"name": "Kids Room", "avatar_color": "#f59e0b", "pin": "1234"}' \\
  http://localhost:8008/api/v1/profiles
\`\`\`

**Notes**

- The PIN is write-only: it is hashed on the way in and only the \`has_pin\`
  flag is ever readable.

---

### \`PUT /api/v1/profiles/{profile_id}\`

Partially update a profile. Only the fields you send are changed; at least one
recognized field must be present or the request is a \`400\`.

**Path parameters**

| Name | Type | Required | Default | Description |
|------|------|----------|---------|-------------|
| \`profile_id\` | int | Yes | — | Profile ID |

**Request body** — all fields optional:

| Name | Type | Required | Default | Description |
|------|------|----------|---------|-------------|
| \`name\` | string | No | — | New display name; trimmed before saving |
| \`avatar_color\` | string | No | — | New hex avatar color |
| \`avatar_url\` | string \\| null | No | — | New avatar image URL |
| \`is_admin\` | bool | No | — | Grant/revoke admin privileges (stored as 0/1) |
| \`pin\` | string | No | — | New PIN (hashed with pbkdf2:sha256). Send an **empty string** to clear the PIN |

**Response** — \`200\` with \`data.profile\` set to the updated profile object.

\`\`\`json
{
  "success": true,
  "data": {
    "profile": {
      "id": 3,
      "name": "Kids Room",
      "avatar_color": "#f59e0b",
      "avatar_url": null,
      "is_admin": false,
      "has_pin": false,
      "can_download": true,
      "allowed_sides": "music",
      "library_mode": "shared"
    }
  },
  "error": null,
  "pagination": null
}
\`\`\`

**Errors**

| Status | Code | Meaning |
|--------|------|---------|
| 400 | \`BAD_REQUEST\` | No recognized fields in the body (\`"No valid fields to update."\`) |
| 404 | \`NOT_FOUND\` | No profile with that ID (also returned if the update fails, e.g. a duplicate \`name\` violating the unique constraint) |
| 500 | \`PROFILE_ERROR\` | Database failure |

**Example**

\`\`\`bash
curl -X PUT -H "Authorization: Bearer $API_KEY" \\
  -H "Content-Type: application/json" \\
  -d '{"name": "Kids Room", "avatar_color": "#f59e0b"}' \\
  http://localhost:8008/api/v1/profiles/3
\`\`\`

**Notes**

- Clearing the PIN: send \`"pin": ""\` — the stored hash is set to \`null\` and
  \`has_pin\` becomes \`false\`.

---

### \`DELETE /api/v1/profiles/{profile_id}\`

Delete a profile and all of its data. **There is no undo.** Profile 1 (the
default admin profile) cannot be deleted.

**Path parameters**

| Name | Type | Required | Default | Description |
|------|------|----------|---------|-------------|
| \`profile_id\` | int | Yes | — | Profile ID |

**Response** — \`200\` with a confirmation message:

\`\`\`json
{
  "success": true,
  "data": { "message": "Profile 3 deleted." },
  "error": null,
  "pagination": null
}
\`\`\`

**Errors**

| Status | Code | Meaning |
|--------|------|---------|
| 403 | \`FORBIDDEN\` | Attempt to delete profile 1 (\`"Cannot delete the default admin profile."\`) |
| 404 | \`NOT_FOUND\` | No profile with that ID |
| 500 | \`PROFILE_ERROR\` | Database failure |

**Example**

\`\`\`bash
curl -X DELETE -H "Authorization: Bearer $API_KEY" \\
  http://localhost:8008/api/v1/profiles/3
\`\`\`

**Notes**

- The wipe is schema-derived: every table with a \`profile_id\` column loses
  that profile's rows (watchlist, wishlist, settings, history, credentials,
  …), plus signed-in devices, own-library rows, and listening-pile metadata
  keys. A best-effort sweep also clears the profile's video-side rows
  (requests, issues). Open library issues are an exception — they are handed
  to the admin profile instead of being deleted.
`
        },
        {
            id: 'api-settings',
            title: 'Settings & API Keys',
            lede: 'Read and update server settings, and manage API keys themselves.',
            body: `
# Settings

### \`GET /api/v1/settings\`

Return the current server settings with sensitive values redacted. Safe to log or display the response — no secret ever leaves the server through this endpoint.

**Response** — \`data.settings\` is the full settings object; every sensitive value is replaced with the string \`"***REDACTED***"\`:

\`\`\`json
{
  "success": true,
  "data": {
    "settings": {
      "spotify": {
        "client_id": "***REDACTED***",
        "client_secret": "***REDACTED***"
      },
      "plex": {
        "token": "***REDACTED***"
      }
    }
  },
  "error": null,
  "pagination": null
}
\`\`\`

**Redacted keys.** Redaction is recursive and prefix-based: any setting whose dot-path starts with one of these is redacted (so \`spotify.client_id\` and anything nested under such a path are covered):

\`spotify.client_id\`, \`spotify.client_secret\`, \`tidal.client_id\`, \`tidal.client_secret\`, \`tidal_tokens\`, \`tidal_download.session\`, \`qobuz.session\`, \`plex.token\`, \`jellyfin.api_key\`, \`navidrome.password\`, \`soulseek.api_key\`, \`listenbrainz.token\`, \`acoustid.api_key\`, \`lastfm.api_key\`, \`genius.access_token\`, \`hydrabase.api_key\`

**Example:**

\`\`\`bash
curl -H "Authorization: Bearer sk_..." http://localhost:8008/api/v1/settings
\`\`\`

**Errors:** \`401 AUTH_REQUIRED\`, \`403 INVALID_KEY\`, \`429 RATE_LIMITED\`, \`500 SETTINGS_ERROR\`.

**Notes:**
- Non-sensitive settings are returned verbatim, exactly as stored.
- Because secrets come back masked, there is no way to read a stored credential back through the API — to rotate one, \`PATCH\` the new value (see below).

### \`PATCH /api/v1/settings\`

Update settings (partial). Only the keys you send are changed; everything else is left untouched.

**Request body** (\`application/json\`) — an object mapping setting keys to new values:

\`\`\`json
{
  "plex.token": "new-token-value",
  "spotify.client_secret": "new-secret"
}
\`\`\`

**Body semantics:**

| Aspect | Behavior |
|--------|----------|
| Key format | Dot-notation keys accepted (\`"plex.token"\` sets \`plex\` → \`token\`). Intermediate objects are created if missing. |
| Partial update | Only the keys in the body are written. There is no way to reset the whole settings object through this endpoint. |
| \`api_keys\` | The literal key \`"api_keys"\` is **silently skipped** — it never appears in \`updated_keys\` and no error is raised. Manage keys through the \`/api-keys\` endpoints instead. |
| Secrets | To change a secret, send the real new value. Sending an empty string, \`null\`, or the UI's redacted sentinel for a sensitive path is interpreted as "keep the existing value" and the stored secret is left untouched — you cannot wipe a credential by saving an empty value. |
| Empty body | A missing body or \`{}\` is rejected with \`400 BAD_REQUEST\` (\`"Empty body."\`). |

**Response:**

\`\`\`json
{
  "success": true,
  "data": {
    "message": "Settings updated.",
    "updated_keys": ["plex.token", "spotify.client_secret"]
  },
  "error": null,
  "pagination": null
}
\`\`\`

\`updated_keys\` lists the keys that were written, in the order they appeared in the request body. Keys are persisted to disk immediately.

**Example:**

\`\`\`bash
curl -X PATCH http://localhost:8008/api/v1/settings \\
  -H "Authorization: Bearer sk_..." \\
  -H "Content-Type: application/json" \\
  -d '{"plex.token": "new-token-value"}'
\`\`\`

**Errors:** \`400 BAD_REQUEST\` (\`Empty body.\`), \`401 AUTH_REQUIRED\`, \`403 INVALID_KEY\`, \`429 RATE_LIMITED\`, \`500 SETTINGS_ERROR\`.

**Notes:**
- Some settings only take effect after a restart or a service reconnect (e.g. changing a media server's token does not retroactively fix an already-failed connection test). The API reports what was written, not what was applied.
- Because an API key acts with admin rights, this endpoint can change any setting, including security-sensitive ones. Automations that call it should send the smallest possible body.
`
        },
        {
            id: 'api-retag',
            title: 'Retag',
            lede: 'Inspect and manage retag groups — the batches behind library-wide tag fixes.',
            body: `
# Retag API — draft documentation

> All endpoints live under \`/api/v1\` and require an API key: send it as an
> \`Authorization: Bearer <key>\` header (preferred) or as \`?api_key=<key>\`.
> Examples below assume \`API_KEY\` is set in the environment and the server is
> at \`http://localhost:8008\`. Every response uses the standard envelope
> \`{"success": true|false, "data": {...}|null, "error": {"code","message"}|null,
> "pagination": {...}|null}\`. \`pagination\` is \`null\` on all retag endpoints
> (none are paginated). Rate limit: 60 requests/minute per IP (\`429\`, code
> \`RATE_LIMITED\`).

## Endpoint reference

Retag groups collect files whose metadata needs rewriting — the batches behind
library-wide tag fixes. The API is read-and-delete only: inspect the groups,
review what a group caught, delete it when its fixes are done, or clear
everything at once. Groups themselves are created by the retag worker, not by
this API.

### The retag group object

| Field | Type | Description |
|-------|------|-------------|
| \`id\` | int | Group ID |
| \`group_type\` | string | Group kind, e.g. \`"album"\` (default) |
| \`artist_name\` | string | Artist the group belongs to |
| \`album_name\` | string | Album the group belongs to |
| \`image_url\` | string \\| null | Cover art URL |
| \`spotify_album_id\` | string \\| null | Spotify album ID, when known |
| \`itunes_album_id\` | string \\| null | iTunes album ID, when known |
| \`total_tracks\` | int | Expected track count for the album (default \`1\`) |
| \`release_date\` | string \\| null | Release date |
| \`created_at\` | string | When the group was created |
| \`track_count\` | int | Number of tracks currently in the group (computed; not stored) |

### The retag track object

| Field | Type | Description |
|-------|------|-------------|
| \`id\` | int | Track row ID |
| \`group_id\` | int | Owning group ID |
| \`track_number\` | int \\| null | Track number within the disc |
| \`disc_number\` | int | Disc number (default \`1\`) |
| \`title\` | string | Track title |
| \`file_path\` | string | Absolute path of the file needing retagging |
| \`file_format\` | string \\| null | Audio container/codec, e.g. \`"flac"\` |
| \`spotify_track_id\` | string \\| null | Spotify track ID, when known |
| \`itunes_track_id\` | string \\| null | iTunes track ID, when known |
| \`created_at\` | string | When the track row was created |

Note: \`?fields=\` trimming and \`?page=\`/\`?limit=\` pagination are **not**
supported on any retag endpoint.

---

### \`GET /api/v1/retag/groups\`

List all retag groups with their track counts, ordered by artist name (A–Z),
then newest first within an artist.

**Response** — \`data.groups\` is an array of retag group objects (see above).

\`\`\`json
{
  "success": true,
  "data": {
    "groups": [
      {
        "id": 7,
        "group_type": "album",
        "artist_name": "Kendrick Lamar",
        "album_name": "GNX",
        "image_url": "https://i.scdn.co/image/ab67616d0000b273...",
        "spotify_album_id": "3u6lywkhp1dvq8q...",
        "itunes_album_id": null,
        "total_tracks": 12,
        "release_date": "2024-11-22",
        "created_at": "2026-09-28 14:02:11",
        "track_count": 12
      }
    ]
  },
  "error": null,
  "pagination": null
}
\`\`\`

**Errors**

| Status | Code | Meaning |
|--------|------|---------|
| 500 | \`RETAG_ERROR\` | Database failure |

**Example**

\`\`\`bash
curl -H "Authorization: Bearer $API_KEY" \\
  http://localhost:8008/api/v1/retag/groups
\`\`\`

---

### \`GET /api/v1/retag/groups/{group_id}\`

Get one retag group together with all of its tracks, ordered by disc number
then track number.

**Path parameters**

| Name | Type | Required | Default | Description |
|------|------|----------|---------|-------------|
| \`group_id\` | int | Yes | — | Retag group ID |

**Response** — \`data.group\` is the retag group object (including
\`track_count\`); \`data.tracks\` is the array of retag track objects.

\`\`\`json
{
  "success": true,
  "data": {
    "group": {
      "id": 7,
      "group_type": "album",
      "artist_name": "Kendrick Lamar",
      "album_name": "GNX",
      "image_url": "https://i.scdn.co/image/ab67616d0000b273...",
      "spotify_album_id": "3u6lywkhp1dvq8q...",
      "itunes_album_id": null,
      "total_tracks": 12,
      "release_date": "2024-11-22",
      "created_at": "2026-09-28 14:02:11",
      "track_count": 2
    },
    "tracks": [
      {
        "id": 41,
        "group_id": 7,
        "track_number": 1,
        "disc_number": 1,
        "title": "wacced out murals",
        "file_path": "/music/Kendrick Lamar/GNX/01 - wacced out murals.flac",
        "file_format": "flac",
        "spotify_track_id": "3v8Z6r8Z...",
        "itunes_track_id": null,
        "created_at": "2026-09-28 14:02:12"
      }
    ]
  },
  "error": null,
  "pagination": null
}
\`\`\`

**Errors**

| Status | Code | Meaning |
|--------|------|---------|
| 404 | \`NOT_FOUND\` | No retag group with that ID (\`"Retag group 7 not found."\`) |
| 500 | \`RETAG_ERROR\` | Database failure |

**Example**

\`\`\`bash
curl -H "Authorization: Bearer $API_KEY" \\
  http://localhost:8008/api/v1/retag/groups/7
\`\`\`

---

### \`DELETE /api/v1/retag/groups/{group_id}\`

Delete one retag group and all of its tracks. **There is no undo.**

**Path parameters**

| Name | Type | Required | Default | Description |
|------|------|----------|---------|-------------|
| \`group_id\` | int | Yes | — | Retag group ID |

**Response** — \`200\` with a confirmation message:

\`\`\`json
{
  "success": true,
  "data": { "message": "Retag group 7 deleted." },
  "error": null,
  "pagination": null
}
\`\`\`

**Errors**

| Status | Code | Meaning |
|--------|------|---------|
| 500 | \`RETAG_ERROR\` | Database failure |

**Example**

\`\`\`bash
curl -X DELETE -H "Authorization: Bearer $API_KEY" \\
  http://localhost:8008/api/v1/retag/groups/7
\`\`\`

**Notes**

- The underlying delete does not check that the group exists first: deleting
  a nonexistent \`group_id\` still answers \`200\` with the confirmation message.
  (The \`404\` branch in the route is unreachable with the current database
  code, which returns success unconditionally.)

---

### \`DELETE /api/v1/retag/groups\`

Delete **all** retag groups and their tracks at once. **There is no undo.**

> [!WARNING]
> This endpoint is currently broken: it always answers \`500 RETAG_ERROR\`
> because \`api/retag.py\` calls \`db.clear_all_retag_groups()\`, which does not
> exist (the database method is \`delete_all_retag_groups()\`). The contract
> below is the intended behavior once that is fixed.

**Response** — intended \`200\` with the number of groups cleared:

\`\`\`json
{
  "success": true,
  "data": { "message": "Cleared 4 retag groups." },
  "error": null,
  "pagination": null
}
\`\`\`

**Errors**

| Status | Code | Meaning |
|--------|------|---------|
| 500 | \`RETAG_ERROR\` | Database failure — currently also the answer on every call due to the bug described above |

**Example**

\`\`\`bash
curl -X DELETE -H "Authorization: Bearer $API_KEY" \\
  http://localhost:8008/api/v1/retag/groups
\`\`\`

---

### \`GET /api/v1/retag/stats\`

Get retag queue statistics: total groups, total tracks, and the number of
distinct artists across all groups.

**Response** — \`data\` is the stats object directly (not nested under a named
key):

\`\`\`json
{
  "success": true,
  "data": {
    "groups": 4,
    "tracks": 47,
    "artists": 3
  },
  "error": null,
  "pagination": null
}
\`\`\`

| Field | Type | Description |
|-------|------|-------------|
| \`groups\` | int | Total retag groups (\`COUNT(*)\` on \`retag_groups\`) |
| \`tracks\` | int | Total retag tracks (\`COUNT(*)\` on \`retag_tracks\`) |
| \`artists\` | int | Distinct artists (\`COUNT(DISTINCT artist_name)\` on \`retag_groups\`) |

**Errors**

| Status | Code | Meaning |
|--------|------|---------|
| 500 | \`RETAG_ERROR\` | Database failure |

**Example**

\`\`\`bash
curl -H "Authorization: Bearer $API_KEY" \\
  http://localhost:8008/api/v1/retag/stats
\`\`\`
`
        },
        {
            id: 'api-cache',
            title: 'Cache',
            lede: 'Peek at the metadata caches SoulSync keeps warm.',
            body: `
# Cache API — draft documentation

Base: \`/api/v1\`. All endpoints require an API key (\`Authorization: Bearer sk_...\` header or \`?api_key=\` query parameter). All responses use the standard envelope \`{"success", "data", "error", "pagination"}\`. Rate limit: 60 requests/minute per IP (\`429\` / \`RATE_LIMITED\`).

These endpoints are diagnostic: inspect what SoulSync has cached from MusicBrainz lookups and discovery-provider matching. They are **read-only** — all four are \`GET\` and none of them clear, purge, or otherwise modify the caches. They are not profile-scoped.

### \`GET /api/v1/cache/musicbrainz\`

Lists cached MusicBrainz lookups, most recently updated first, with optional filtering by entity type and name.

**Query parameters**

| Name | Type | Required | Default | Description |
|------|------|----------|---------|-------------|
| \`entity_type\` | string | No | — | Filter by entity type (\`"artist"\`, \`"album"\`, or \`"track"\`). Not validated — any other value simply matches nothing |
| \`search\` | string | No | — | Case-insensitive substring filter on the cached entity name |
| \`page\` | int | No | \`1\` | Page number (clamped to ≥ 1; non-numeric falls back to 1) |
| \`limit\` | int | No | \`50\` | Entries per page (clamped to 1–200; non-numeric falls back to 50) |

**Response**

\`data.entries\` is an array of raw cache rows; \`pagination\` is populated. \`metadata_json\` is returned as parsed JSON when it holds a decodable JSON string; otherwise it is returned as-is. A \`musicbrainz_id\` of \`null\` means the lookup was attempted but found no match (a cached miss).

\`\`\`json
{
  "success": true,
  "data": {
    "entries": [
      {
        "id": 881,
        "entity_type": "artist",
        "entity_name": "Miles Davis",
        "artist_name": null,
        "musicbrainz_id": "dbd3c3a8-6d1e-4c8b-bb6f-5a2e8f4c1a2b",
        "spotify_id": "3fMbdgg4jU18AjLCKBh",
        "itunes_id": null,
        "metadata_json": { "country": "US", "type": "Person" },
        "match_confidence": 95,
        "last_updated": "2026-09-29T06:00:00"
      }
    ]
  },
  "error": null,
  "pagination": { "page": 1, "limit": 50, "total": 1204, "total_pages": 25, "has_next": true, "has_prev": false }
}
\`\`\`

**Errors**

| Code | Status | When |
|------|--------|------|
| \`CACHE_ERROR\` | 500 | Database or unexpected failure |

**Example**

\`\`\`bash
curl -H "Authorization: Bearer sk_..." \\
  "http://localhost:8008/api/v1/cache/musicbrainz?entity_type=artist&search=miles"
\`\`\`

---

### \`GET /api/v1/cache/musicbrainz/stats\`

Returns aggregate statistics for the MusicBrainz cache.

**Response**

\`pagination\` is \`null\`. \`by_type\` maps each \`entity_type\` present in the cache to its row count. \`matched\` counts rows with a non-null \`musicbrainz_id\`; \`unmatched\` is \`total - matched\` (cached misses).

\`\`\`json
{
  "success": true,
  "data": {
    "total": 1204,
    "matched": 1102,
    "unmatched": 102,
    "by_type": { "artist": 640, "album": 380, "track": 184 }
  },
  "error": null,
  "pagination": null
}
\`\`\`

**Errors**

| Code | Status | When |
|------|--------|------|
| \`CACHE_ERROR\` | 500 | Database or unexpected failure |

**Example**

\`\`\`bash
curl -H "Authorization: Bearer sk_..." \\
  "http://localhost:8008/api/v1/cache/musicbrainz/stats"
\`\`\`

---

### \`GET /api/v1/cache/discovery-matches\`

Lists cached discovery-provider matches (how SoulSync resolved a discovered title/artist against a metadata provider), most recently used first.

**Query parameters**

| Name | Type | Required | Default | Description |
|------|------|----------|---------|-------------|
| \`provider\` | string | No | — | Filter by provider (\`"spotify"\`, \`"itunes"\`, etc.). Not validated — any other value simply matches nothing |
| \`search\` | string | No | — | Case-insensitive substring filter matched against the original title **or** original artist |
| \`page\` | int | No | \`1\` | Page number (clamped to ≥ 1; non-numeric falls back to 1) |
| \`limit\` | int | No | \`50\` | Entries per page (clamped to 1–200; non-numeric falls back to 50) |

**Response**

\`data.entries\` is an array of raw cache rows; \`pagination\` is populated. \`matched_data_json\` is returned as parsed JSON when it holds a decodable JSON string; otherwise it is returned as-is. \`use_count\` is how many times the cached match has been reused; \`match_confidence\` is the confidence score of the match.

\`\`\`json
{
  "success": true,
  "data": {
    "entries": [
      {
        "id": 55,
        "normalized_title": "blue in green",
        "normalized_artist": "miles davis",
        "provider": "spotify",
        "match_confidence": 0.97,
        "matched_data_json": { "spotify_track_id": "4uLU6hMCjMI75M1A2tKUQ" },
        "original_title": "Blue In Green",
        "original_artist": "Miles Davis",
        "created_at": "2026-09-20T10:00:00",
        "last_used_at": "2026-09-29T06:00:00",
        "use_count": 6
      }
    ]
  },
  "error": null,
  "pagination": { "page": 1, "limit": 50, "total": 312, "total_pages": 7, "has_next": true, "has_prev": false }
}
\`\`\`

**Errors**

| Code | Status | When |
|------|--------|------|
| \`CACHE_ERROR\` | 500 | Database or unexpected failure |

**Example**

\`\`\`bash
curl -H "Authorization: Bearer sk_..." \\
  "http://localhost:8008/api/v1/cache/discovery-matches?provider=spotify&search=miles"
\`\`\`

---

### \`GET /api/v1/cache/discovery-matches/stats\`

Returns aggregate statistics for the discovery match cache.

**Response**

\`pagination\` is \`null\`. \`by_provider\` maps each provider present in the cache to its row count. \`total_uses\` is the sum of \`use_count\` across all rows. \`avg_confidence\` is the mean \`match_confidence\` rounded to 3 decimals, or \`null\` when the cache is empty.

\`\`\`json
{
  "success": true,
  "data": {
    "total": 312,
    "total_uses": 1890,
    "avg_confidence": 0.912,
    "by_provider": { "spotify": 240, "itunes": 72 }
  },
  "error": null,
  "pagination": null
}
\`\`\`

**Errors**

| Code | Status | When |
|------|--------|------|
| \`CACHE_ERROR\` | 500 | Database or unexpected failure |

**Example**

\`\`\`bash
curl -H "Authorization: Bearer sk_..." \\
  "http://localhost:8008/api/v1/cache/discovery-matches/stats"
\`\`\`

**Notes**

- Useful for diagnosing discovery quality: a low \`avg_confidence\` or a provider with a suspiciously small share of \`by_provider\` points at where matching is struggling.
`
        },
        {
            id: 'api-listenbrainz',
            title: 'ListenBrainz',
            lede: 'Read the ListenBrainz playlists SoulSync has imported or generated.',
            body: `
# ListenBrainz API — draft documentation

Base: \`/api/v1\`. All endpoints require an API key (\`Authorization: Bearer sk_...\` header or \`?api_key=\` query parameter). All responses use the standard envelope \`{"success", "data", "error", "pagination"}\`. Rate limit: 60 requests/minute per IP (\`429\` / \`RATE_LIMITED\`).

These endpoints read the ListenBrainz playlists SoulSync has imported or generated. They are not profile-scoped. \`playlist_id\` on the detail endpoint accepts either the internal integer ID or the MusicBrainz playlist MBID.

### \`GET /api/v1/listenbrainz/playlists\`

Lists cached ListenBrainz playlists, most recently updated first, with optional filtering by playlist type.

**Query parameters**

| Name | Type | Required | Default | Description |
|------|------|----------|---------|-------------|
| \`type\` | string | No | — | Filter by \`playlist_type\` (e.g. \`"weekly-jams"\`, \`"weekly-exploration"\`) |
| \`page\` | int | No | \`1\` | Page number (clamped to ≥ 1; non-numeric falls back to 1) |
| \`limit\` | int | No | \`50\` | Playlists per page (clamped to 1–200; non-numeric falls back to 50) |

**Response**

\`data.playlists\` is an array of raw playlist rows; \`pagination\` is populated. \`annotation_data\` is returned as parsed JSON (it is stored as a JSON string and decoded by the endpoint when possible).

\`\`\`json
{
  "success": true,
  "data": {
    "playlists": [
      {
        "id": 5,
        "playlist_mbid": "e9f8a7b6-5c4d-3e2f-1a0b-9c8d7e6f5a4b",
        "title": "Weekly Jams",
        "creator": "listenbrainz",
        "playlist_type": "weekly-jams",
        "track_count": 25,
        "annotation_data": { "description": "Your weekly mix" },
        "last_updated": "2026-09-29T06:00:00",
        "cached_date": "2026-09-29T06:00:00"
      }
    ]
  },
  "error": null,
  "pagination": { "page": 1, "limit": 50, "total": 4, "total_pages": 1, "has_next": false, "has_prev": false }
}
\`\`\`

**Errors**

| Code | Status | When |
|------|--------|------|
| \`LISTENBRAINZ_ERROR\` | 500 | Database or unexpected failure |

**Example**

\`\`\`bash
curl -H "Authorization: Bearer sk_..." \\
  "http://localhost:8008/api/v1/listenbrainz/playlists?type=weekly-jams"
\`\`\`

---

### \`GET /api/v1/listenbrainz/playlists/{playlist_id}\`

Returns one ListenBrainz playlist together with its tracks in playlist order (\`position\` ascending).

**Path parameters**

| Name | Type | Required | Description |
|------|------|----------|-------------|
| \`playlist_id\` | string | Yes | The internal integer playlist ID **or** the MusicBrainz playlist MBID. A value that parses as an integer is looked up by internal ID; anything else is looked up by \`playlist_mbid\` |

**Response**

\`data.playlist\` is the playlist row; \`data.tracks\` is the array of track rows in position order. \`additional_metadata\` on each track is returned as parsed JSON when possible. \`pagination\` is \`null\` (the full track list is always returned — it is not paginated).

\`\`\`json
{
  "success": true,
  "data": {
    "playlist": {
      "id": 5,
      "playlist_mbid": "e9f8a7b6-5c4d-3e2f-1a0b-9c8d7e6f5a4b",
      "title": "Weekly Jams",
      "creator": "listenbrainz",
      "playlist_type": "weekly-jams",
      "track_count": 25,
      "annotation_data": { "description": "Your weekly mix" },
      "last_updated": "2026-09-29T06:00:00",
      "cached_date": "2026-09-29T06:00:00"
    },
    "tracks": [
      {
        "id": 101,
        "playlist_id": 5,
        "position": 1,
        "track_name": "Blue in Green",
        "artist_name": "Miles Davis",
        "album_name": "Kind of Blue",
        "duration_ms": 327280,
        "recording_mbid": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
        "release_mbid": "b2c3d4e5-f6a7-8901-bcde-f12345678901",
        "album_cover_url": "https://coverartarchive.org/release/b2c3d4e5/front",
        "additional_metadata": { "caa_id": 123456 }
      }
    ]
  },
  "error": null,
  "pagination": null
}
\`\`\`

**Errors**

| Code | Status | When |
|------|--------|------|
| \`NOT_FOUND\` | 404 | No playlist matches the given ID or MBID |
| \`LISTENBRAINZ_ERROR\` | 500 | Database or unexpected failure |

**Example**

\`\`\`bash
# By internal ID:
curl -H "Authorization: Bearer sk_..." \\
  "http://localhost:8008/api/v1/listenbrainz/playlists/5"

# By MusicBrainz playlist MBID:
curl -H "Authorization: Bearer sk_..." \\
  "http://localhost:8008/api/v1/listenbrainz/playlists/e9f8a7b6-5c4d-3e2f-1a0b-9c8d7e6f5a4b"
\`\`\`

**Notes**

- Lookup order is fixed: if \`playlist_id\` parses as an integer, only the internal ID is tried — an MBID is never all digits, so the two forms do not collide in practice.
`
        },
        {
            id: 'api-metasync',
            title: 'MetaSync Export',
            lede: 'A read-only, cursor-paged walk of the library\u2019s resolved metadata, built for the MetaSync sidecar.',
            body: `
# MetaSync Export API — draft documentation

Base: \`/api/v1\`. Requires an API key (\`Authorization: Bearer sk_...\` header or \`?api_key=\` query parameter). Responses use the standard envelope \`{"success", "data", "error", "pagination"}\`. Rate limit: 60 requests/minute per IP (\`429\` / \`RATE_LIMITED\`).

MetaSync is a peer-to-peer metadata network that runs as a SoulSync sidecar. This endpoint is how it reads SoulSync's library: a **read-only**, cursor-paged walk of resolved metadata (artists, albums, tracks). It is not profile-scoped.

Two things distinguish it from the \`/library\` endpoints:

- **Keyset pagination, not offset.** Walk with \`cursor\` (\`id > cursor\`, ordered by id ascending). Offset pagination would skip or duplicate rows when the enrichment workers shift rows mid-walk. A full page (\`len(items) == limit\`) means there may be more — keep walking until a short page or an empty \`next_cursor\`.
- **Cross-install identity.** Items carry \`soul_id\` (the identity the MetaSync network keys on) and only *matched* provider IDs — each served provider ID is accompanied by the \`<provider>_match_status\` that governs it, and only IDs with status \`matched\` are published. Install-local data (file paths, thumbnails, lyrics, play counts, local primary keys) is never exported; the serializers are an explicit allowlist. Rows with no usable \`soul_id\` (including \`soul_unnamed_*\` fallback IDs, which embed install-local keys) are excluded from the export.

### \`GET /api/v1/metasync/export\`

Pages through artists, albums, or tracks for the MetaSync sidecar.

**Query parameters**

| Name | Type | Required | Default | Description |
|------|------|----------|---------|-------------|
| \`entity\` | string | Yes | — | One of \`"artist"\`, \`"album"\`, \`"track"\` (case-insensitive; anything else is a \`400\`) |
| \`cursor\` | string | No | — | Opaque base64 page cursor from the previous response's \`next_cursor\`. Omit or send empty on the first page. Malformed base64 is a \`400\` |
| \`limit\` | int | No | \`500\` | Page size (clamped to 1–1000; non-numeric falls back to 500) |
| \`since\` | string | No | — | ISO-8601 timestamp — returns only rows whose own or parent's \`updated_at\` is at or after this instant. Compared as instants (not text), so \`2026-08-19T00:00:00Z\` and offset forms are normalized. Unparseable values are a \`400\` |

**Response**

\`data\` contains \`entity\` (echoed, lowercased), \`items\`, \`next_cursor\`, and \`has_more\`. There is **no** \`pagination\` object — \`pagination\` is \`null\`; \`next_cursor\`/\`has_more\` are the paging mechanism. \`next_cursor\` is \`""\` when the page is empty. \`has_more\` is \`len(items) == limit\`, so a full page means "keep walking".

\`\`\`json
{
  "success": true,
  "data": {
    "entity": "track",
    "items": [
      {
        "soul_id": "soul_9f2c1a4b5d6e",
        "album_soul_id": "soul_1a2b3c4d5e6f",
        "title": "Blue in Green",
        "artist_name": "Miles Davis",
        "album_title": "Kind of Blue",
        "track_number": 3,
        "disc_number": 1,
        "duration": 327,
        "bpm": null,
        "explicit": false,
        "year": 1959,
        "isrc": "USSM15900123",
        "updated_at": "2026-09-28T14:02:11",
        "musicbrainz_recording_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
        "musicbrainz_match_status": "matched",
        "spotify_track_id": "4uLU6hMCjMI75M1A2tKUQ",
        "spotify_match_status": "matched",
        "itunes_track_id": null,
        "itunes_match_status": null,
        "deezer_id": null,
        "deezer_match_status": null,
        "amazon_id": null,
        "amazon_match_status": null,
        "tidal_id": null,
        "tidal_match_status": null,
        "qobuz_id": null,
        "qobuz_match_status": null,
        "audiodb_id": null,
        "audiodb_match_status": null,
        "genius_id": null,
        "genius_match_status": null,
        "jiosaavn_id": null,
        "jiosaavn_match_status": null,
        "bandcamp_url": null,
        "bandcamp_match_status": null
      }
    ],
    "next_cursor": "NDI=",
    "has_more": true
  },
  "error": null,
  "pagination": null
}
\`\`\`

Item shapes by entity (in addition to the provider-ID + \`<provider>_match_status\` pairs shown above):

- **artist**: \`soul_id\`, \`soul_id_path\` (\`'canonical'\` | \`'album'\` | \`'name'\` | \`null\` — only \`'canonical'\` is reproducible on another install), \`name\`, \`genres[]\`, \`updated_at\`, plus IDs: \`musicbrainz_id\`, \`spotify_artist_id\`, \`itunes_artist_id\`, \`deezer_id\`, \`discogs_id\`, \`amazon_id\`, \`tidal_id\`, \`qobuz_id\`, \`audiodb_id\`, \`genius_id\`, \`jiosaavn_id\`.
- **album**: \`soul_id\`, \`title\`, \`artist_name\`, \`year\`, \`release_date\`, \`track_count\`, \`record_type\`, \`label\`, \`genres[]\`, \`updated_at\`, \`canonical_source\`, \`canonical_album_id\`, \`canonical_score\`, plus IDs: \`musicbrainz_release_id\`, \`spotify_album_id\`, \`itunes_album_id\`, \`deezer_id\`, \`discogs_id\`, \`amazon_id\`, \`tidal_id\`, \`qobuz_id\`, \`audiodb_id\`, \`jiosaavn_id\`, \`bandcamp_url\`, \`upc\`. (\`upc\` has no match-status companion.)
- **track**: \`soul_id\`, \`album_soul_id\`, \`title\`, \`artist_name\`, \`album_title\`, \`track_number\`, \`disc_number\`, \`duration\`, \`bpm\`, \`explicit\`, \`year\`, \`isrc\`, \`updated_at\`, plus IDs: \`musicbrainz_recording_id\`, \`spotify_track_id\`, \`itunes_track_id\`, \`deezer_id\`, \`amazon_id\`, \`tidal_id\`, \`qobuz_id\`, \`audiodb_id\`, \`genius_id\`, \`jiosaavn_id\`, \`bandcamp_url\`.

**Errors**

| Code | Status | When |
|------|--------|------|
| \`BAD_REQUEST\` | 400 | \`entity\` missing or not one of \`artist\`/\`album\`/\`track\`; \`cursor\` is not valid base64; \`since\` is not a parseable ISO-8601 timestamp |
| \`EXPORT_ERROR\` | 500 | Database or unexpected failure |

**Example** — full walk of all tracks, then an incremental walk of what changed since a timestamp:

\`\`\`bash
# First page (no cursor):
curl -H "Authorization: Bearer sk_..." \\
  "http://localhost:8008/api/v1/metasync/export?entity=track&limit=500"
# → take data.next_cursor, repeat until has_more is false:
curl -H "Authorization: Bearer sk_..." \\
  "http://localhost:8008/api/v1/metasync/export?entity=track&limit=500&cursor=NDI%3D"

# Incremental: only rows changed at/after the given instant:
curl -H "Authorization: Bearer sk_..." \\
  "http://localhost:8008/api/v1/metasync/export?entity=album&since=2026-09-01T00:00:00Z"
\`\`\`

**Notes**

- The cursor is the last row's primary key, base64-encoded. It is opaque by design — do not parse it or depend on its shape.
- \`since\` matches against the row's *effective* update instant: for albums it is the newer of the album's and its artist's \`updated_at\`; for tracks the newest of track, artist, and album. Renaming an artist therefore re-exports all of its albums and tracks on the next incremental walk.
- \`?fields=\` is **not** supported here (unlike most list endpoints) — items always carry the full allowlisted shape.
- \`soul_id\` is the network key, never a claimable payload ID: it is served as the join key but is not listed among the provider IDs, so network-sourced identity cannot be laundered back in as a local observation.
`
        },
        {
            id: 'api-video',
            title: 'Video',
            lede: 'The full video v1 surface: library, search, wishlist, watchlist, scans, downloads, calendar, and requests.',
            body: `
# SoulSync Video API (v1) — documentation draft

All endpoints live under \`/api/v1\` and require an API key, sent as an
\`Authorization: Bearer sk_...\` header or as the \`?api_key=\` query parameter.
An API key acts with **admin rights**. Rate limit: 60 requests/minute per IP
(\`429\` with code \`RATE_LIMITED\`).

## How the video surface works

Every endpoint below is a thin relay: it runs the app's own internal video
handler in the current request context (same query args, same JSON body) and
wraps the answer in the v1 envelope. Consequences worth knowing:

- **Envelope.** Success: \`{"success": true, "data": {...}, "error": null,
  "pagination": null}\`. Error: \`{"success": false, "data": null, "error":
  {"code": "...", "message": "..."}, "pagination": null}\`.
- **The envelope's top-level \`pagination\` is always \`null\` on the video
  surface.** Paged endpoints embed their own \`pagination\` object inside
  \`data\`, shaped \`{"page", "total_pages", "total_count", "has_prev",
  "has_next"}\`.
- **\`?fields=\` field-trimming is not implemented on the video endpoints.**
  (Verified: no field-selection handling exists in the video relay path or
  the internal video handlers.)
- **Error codes.** The default v1 error code is \`VIDEO_ERROR\`. Wishlist
  endpoints use \`WISHLIST_ERROR\`, watchlist \`WATCHLIST_ERROR\`, scan
  \`SCAN_ERROR\`, requests \`REQUEST_ERROR\`. The human-readable \`message\` comes
  from the internal handler. Any internal \`409\` is mapped to the v1 code
  \`CONFLICT\` (keeping the internal message); extra internal payload fields
  (e.g. \`in_library\`) do not survive the envelope. If the video side is not
  enabled on the server, every endpoint answers \`503\` with code
  \`NOT_AVAILABLE\`.
- Auth failures (all endpoints): \`401\` / code \`AUTH_REQUIRED\` (missing key),
  \`403\` / code \`INVALID_KEY\` (wrong key).

### \`GET /api/v1/video/library\`

What's in the video library: movies, shows, or followed YouTube channels,
one page at a time. Results are scoped to the active video server, so Plex
and Jellyfin libraries never commingle. Every row carries \`size_bytes\`
(summed over its media files) and the response carries \`total_size_bytes\`
for the whole filtered set.

**Query parameters**

| Name | Type | Required | Default | Description |
|------|------|----------|---------|-------------|
| kind | string | no | \`movies\` | \`movies\`, \`shows\`, or \`channels\`. \`channels\` is the followed-YouTube-channels view (ownership comes from the permanent download history, no media server involved). |
| search | string | no | — | Case-insensitive substring filter on title. |
| letter | string | no | — | A–Z filter on the first letter of the sort title; \`#\` matches non-letters; \`all\` disables. |
| sort | string | no | \`title\` | Result ordering (UI sort key). |
| status | string | no | \`all\` | Movies: \`owned\` keeps titles with a file. Shows: \`missing\` keeps partially-owned shows (the "wanted" view). |
| genre | string | no | — | Keep titles in this genre (matched case-insensitively). |
| resolution | string | no | — | Movies only: keep titles that have a file at this resolution. |
| page | int | no | \`1\` | Page number (min 1; non-numeric falls back to 1). |
| limit | int | no | \`75\` | Rows per page (clamped to 1–500; non-numeric falls back to 75). |

**Response**

\`data\` is \`{"items": [...], "total_size_bytes": <int>, "pagination":
{"page", "total_pages", "total_count", "has_prev", "has_next"}}\`. \`items\`
are movie/show/channel rows for the requested kind.

\`\`\`json
{
  "success": true,
  "data": {
    "items": [],
    "total_size_bytes": 0,
    "pagination": {"page": 1, "total_pages": 1, "total_count": 0, "has_prev": false, "has_next": false}
  },
  "error": null,
  "pagination": null
}
\`\`\`

**Errors**

| Status | Code | When |
|--------|------|------|
| 500 | \`VIDEO_ERROR\` | The library query failed (\`"Failed to load video library"\`). |

**Example**

\`\`\`bash
curl -H "Authorization: Bearer $API_KEY" \\
  "http://localhost:8008/api/v1/video/library?kind=shows&status=missing&page=1&limit=50"
\`\`\`

**Notes**

- On a kids/restricted profile the result set is filtered to titles the
  library can vouch for; the shape is the same.
- \`total_size_bytes\` covers the whole filtered set, not just the page.

### \`GET /api/v1/video/library/genres\`

The genre names in use in the video library — the values for the library
page's genre filter dropdown. Read-only.

**Query parameters**

| Name | Type | Required | Default | Description |
|------|------|----------|---------|-------------|
| kind | string | no | \`movies\` | Genres in use for this library kind. |

**Response**

\`data\` is \`{"genres": ["Action", "Comedy", ...]}\`.

\`\`\`json
{
  "success": true,
  "data": {"genres": ["Action", "Comedy", "Drama"]},
  "error": null,
  "pagination": null
}
\`\`\`

**Errors**

None — a backend failure answers \`200\` with \`{"genres": []}\`.

**Example**

\`\`\`bash
curl -H "Authorization: Bearer $API_KEY" \\
  "http://localhost:8008/api/v1/video/library/genres?kind=shows"
\`\`\`

### \`GET /api/v1/video/search\`

Fast multi-search across movies, shows, and people via the metadata
enrichment engine (TMDB). Each movie/show result is annotated with the
library row id when the title is already owned, so clients can link to the
owned detail instead of the TMDB view. (Studio/production-company search is
a separate internal call and is not exposed on this v1 surface.)

**Query parameters**

| Name | Type | Required | Default | Description |
|------|------|----------|---------|-------------|
| q | string | **yes** | — | Search text. Blank or missing is rejected with \`400\`. |

**Response**

\`data\` is \`{"results": [...], "query": "<echo of q>"}\`. Results are
TMDB-shaped result objects for movies, shows, and people, each stamped with
library-ownership info.

\`\`\`json
{
  "success": true,
  "data": {"results": [], "query": "dune"},
  "error": null,
  "pagination": null
}
\`\`\`

**Errors**

| Status | Code | When |
|--------|------|------|
| 400 | \`BAD_REQUEST\` | \`q\` missing or blank (\`"q is required."\`). |

**Example**

\`\`\`bash
curl -H "Authorization: Bearer $API_KEY" \\
  "http://localhost:8008/api/v1/video/search?q=dune"
\`\`\`

**Notes**

- Identical queries within about a minute reuse a cached result, but
  ownership is re-stamped fresh on every call, so in-library badges stay
  current. A metadata-backend failure answers with empty \`results\`, not an
  error.
- On a kids/restricted profile, results are filtered to titles the library
  can vouch for (people pass through).

### \`GET /api/v1/video/trending\`

Trending titles from the metadata provider, annotated with library
ownership like search results. No parameters — this is always the mixed
movie+show weekly chart.

**Response**

\`data\` is \`{"results": [...]}\` — TMDB-shaped title objects with ownership
annotation, order preserved (rank order).

**Errors**

None — a backend failure answers with empty \`results\`.

**Example**

\`\`\`bash
curl -H "Authorization: Bearer $API_KEY" \\
  http://localhost:8008/api/v1/video/trending
\`\`\`

### \`GET /api/v1/video/wishlist\`

The video wishlist. With \`?kind=movie\` or \`?kind=show\` it returns one paged
slice of items; **with no \`kind\` it returns counts only** — the cheap call
for badges.

**Query parameters**

| Name | Type | Required | Default | Description |
|------|------|----------|---------|-------------|
| kind | string | no | — | \`movie\` for movie cards, \`show\` for shows grouped show → season → episode with wanted/done roll-ups. Omit for counts only. |
| search | string | no | \`""\` | Case-insensitive substring filter on title. |
| sort | string | no | \`added\` | Movies: \`added\` (newest first), \`title\`, \`oldest\`. Shows add \`wanted\` (most episodes first). Unknown values fall back to \`added\`. |
| page | int | no | \`1\` | Page number (min 1; non-numeric falls back to 1). |
| limit | int | no | \`60\` | Rows per page (clamped to 1–200; non-numeric falls back to 60). |

**Response**

With \`kind\`: \`data\` is \`{"kind", "counts", "items": [...], "pagination":
{...}}\`. Movie items carry \`tmdb_id\`, \`title\`, \`poster_url\`, \`year\`,
\`status\`, \`library_id\`, \`search_attempts\`, \`last_search_at\`,
\`last_refusal\`, \`last_refusal_quality\`. Show items carry \`tmdb_id\`,
\`title\`, \`poster_url\`, \`library_id\`, \`wanted\`/\`done\` roll-up counts,
\`muted\`, and a \`seasons\` array of \`{season_number, poster_url, episodes}\`
where each episode carries \`episode_number\`, \`title\`, \`still_url\`,
\`overview\`, \`air_date\`, \`status\`, and search-state fields. Every item is
also annotated with live state (\`downloading_count\` / \`upgrade_count\` for
shows) where known.

Without \`kind\`: \`data\` is \`{"counts": {"movie", "show", "episode", "total"}}\`
where \`total\` is movies + episodes and \`show\` is the distinct show count.

\`\`\`json
{
  "success": true,
  "data": {
    "kind": "movie",
    "counts": {"movie": 12, "show": 3, "episode": 40, "total": 52},
    "items": [],
    "pagination": {"page": 1, "total_pages": 1, "total_count": 12, "has_prev": false, "has_next": false}
  },
  "error": null,
  "pagination": null
}
\`\`\`

**Errors**

| Status | Code | When |
|--------|------|------|
| 500 | \`VIDEO_ERROR\` | The wishlist query failed (\`"Failed to load wishlist"\`). |

**Example**

\`\`\`bash
curl -H "Authorization: Bearer $API_KEY" \\
  "http://localhost:8008/api/v1/video/wishlist?kind=show&sort=wanted"
\`\`\`

### \`GET /api/v1/video/wishlist/counts\`

Full wishlist counts, including YouTube. \`movie\`/\`show\`/\`episode\` count the
TMDB wishlist; \`video\`/\`channel\` count the YouTube wishlist; \`total\` is
movies + episodes + YouTube videos — the number behind the header/sidebar
badge.

**Response**

\`data\` is \`{"movie": n, "show": n, "episode": n, "total": n, "video": n,
"channel": n}\`.

**Errors**

| Status | Code | When |
|--------|------|------|
| 500 | \`VIDEO_ERROR\` | Counting failed (\`"Failed"\`). |

**Example**

\`\`\`bash
curl -H "Authorization: Bearer $API_KEY" \\
  http://localhost:8008/api/v1/video/wishlist/counts
\`\`\`

### \`POST /api/v1/video/wishlist\`

Add a movie, or a set of a show's episodes, to the video wishlist (which
feeds the automatic download drain).

**Request body** — one of:

| Name | Type | Required | Description |
|------|------|----------|-------------|
| movie | object | one of movie/show | \`{tmdb_id*, title*, year?, poster_url?, library_id?}\` — \`tmdb_id\` and a non-blank \`title\` are required. |
| show | object | one of movie/show | \`{tmdb_id*, title*, poster_url?, library_id?}\` — \`tmdb_id\` and a non-blank \`title\` are required. |
| episodes | array | with \`show\` | Non-empty list of \`{season_number*, episode_number*, title?, air_date?}\`. |

**Response**

\`data\` is \`{"added": <n>, "counts": {"movie", "show", "episode",
"total"}}\`. For a movie \`added\` is 1 (or 0 if it was already wished); for
a show it is the number of episodes added.

\`\`\`json
{
  "success": true,
  "data": {"added": 1, "counts": {"movie": 13, "show": 3, "episode": 40, "total": 53}},
  "error": null,
  "pagination": null
}
\`\`\`

**Errors**

| Status | Code | When |
|--------|------|------|
| 400 | \`WISHLIST_ERROR\` | Neither a valid \`movie\` nor \`show\` + non-empty \`episodes\` was supplied (\`"movie or show+episodes required"\`). |
| 500 | \`WISHLIST_ERROR\` | The write failed (\`"Failed"\`). |

**Example**

\`\`\`bash
curl -X POST -H "Authorization: Bearer $API_KEY" \\
  -H "Content-Type: application/json" \\
  http://localhost:8008/api/v1/video/wishlist \\
  -d '{"movie": {"tmdb_id": 693134, "title": "Dune: Part Two", "year": 2024}}'
\`\`\`

### \`DELETE /api/v1/video/wishlist\`

Remove from the video wishlist at any granularity: a whole movie, a whole
show, one season, or one episode.

**Request body**

| Name | Type | Required | Description |
|------|------|----------|-------------|
| scope | string | **yes** | \`movie\` \\| \`show\` \\| \`season\` \\| \`episode\`. |
| tmdb_id | int | **yes** | TMDB id of the title. |
| season_number | int | for \`season\`/\`episode\` | Which season. |
| episode_number | int | for \`episode\` | Which episode. |

**Response**

\`data\` is \`{"removed": <n>, "counts": {"movie", "show", "episode",
"total"}}\` — \`removed\` is the number of wishlist rows deleted.

**Errors**

| Status | Code | When |
|--------|------|------|
| 400 | \`WISHLIST_ERROR\` | \`scope\` not one of the four values, or \`tmdb_id\` missing (\`"scope and tmdb_id are required"\`). |
| 500 | \`WISHLIST_ERROR\` | The delete failed (\`"Failed"\`). |

**Example**

\`\`\`bash
curl -X DELETE -H "Authorization: Bearer $API_KEY" \\
  -H "Content-Type: application/json" \\
  http://localhost:8008/api/v1/video/wishlist \\
  -d '{"scope": "season", "tmdb_id": 1399, "season_number": 1}'
\`\`\`

### \`GET /api/v1/video/watchlist\`

The video watchlist: followed shows, people, and studios. Shows include
actively-airing library shows by default, scoped to the active video server.
With \`?kind=\` it returns one paged, searchable slice; with no \`kind\` it
returns the grouped overview (shows/people/studios lists plus counts).

**Query parameters**

| Name | Type | Required | Default | Description |
|------|------|----------|---------|-------------|
| kind | string | no | — | \`show\`, \`person\`, or \`studio\`. Omit for the grouped overview. |
| search | string | no | \`""\` | Substring filter (paged mode). |
| sort | string | no | \`default\` | Ordering (paged mode). |
| page | int | no | \`1\` | Page number (paged mode). |
| limit | int | no | \`60\` | Rows per page (paged mode). |

**Response**

Paged (\`kind\` given): \`data\` is \`{"kind", "counts", "items": [...],
"pagination": {...}}\`. Grouped (no \`kind\`): \`data\` is \`{"shows": [...],
"people": [...], "studios": [...], "counts": {...}}\`.

**Errors**

| Status | Code | When |
|--------|------|------|
| 500 | \`VIDEO_ERROR\` | The watchlist query failed (\`"Failed to load watchlist"\`). |

**Example**

\`\`\`bash
curl -H "Authorization: Bearer $API_KEY" \\
  "http://localhost:8008/api/v1/video/watchlist?kind=show&search=dune"
\`\`\`

### \`POST /api/v1/video/watchlist\`

Follow a show, person, or studio. For shows, \`monitor\` controls what gets
wished at follow time (Sonarr-style semantics): \`future\` (default — only
episodes that have not aired yet), \`all\`, \`first_season\`, \`latest_season\`,
or \`pilot\`. Back-catalog expansion is best-effort; the follow itself always
lands even if the episode expansion fails.

**Request body**

| Name | Type | Required | Description |
|------|------|----------|-------------|
| kind | string | **yes** | \`show\` \\| \`person\` \\| \`studio\`. |
| tmdb_id | int | **yes** | TMDB id. |
| title | string | **yes** | Non-blank display title. |
| poster_url | string | no | Artwork URL. |
| library_id | string | no | Target library id. |
| monitor | string | no | Shows only: \`future\` (default) \\| \`all\` \\| \`first_season\` \\| \`latest_season\` \\| \`pilot\`. |

**Response**

\`data\` is \`{"watched": true, "wished": <n>}\` — \`wished\` is how many
episodes the monitor policy added to the wishlist (0 for people/studios and
for \`monitor: future\`).

**Errors**

| Status | Code | When |
|--------|------|------|
| 400 | \`WATCHLIST_ERROR\` | \`kind\` invalid, \`tmdb_id\` missing, or \`title\` blank (\`"kind, tmdb_id and title are required"\`); also if the follow could not be stored (\`"Could not add to watchlist"\`). |
| 500 | \`WATCHLIST_ERROR\` | The write failed (\`"Failed"\`). |

**Example**

\`\`\`bash
curl -X POST -H "Authorization: Bearer $API_KEY" \\
  -H "Content-Type: application/json" \\
  http://localhost:8008/api/v1/video/watchlist \\
  -d '{"kind": "show", "tmdb_id": 1399, "title": "Game of Thrones", "monitor": "future"}'
\`\`\`

### \`DELETE /api/v1/video/watchlist\`

Unfollow a show, person, or studio.

**Request body**

| Name | Type | Required | Description |
|------|------|----------|-------------|
| kind | string | **yes** | \`show\` \\| \`person\` \\| \`studio\`. |
| tmdb_id | int | **yes** | TMDB id. |

**Response**

\`data\` is \`{"watched": false, "removed": <n>}\` — \`removed\` is the number of
watchlist rows deleted.

**Errors**

| Status | Code | When |
|--------|------|------|
| 400 | \`WATCHLIST_ERROR\` | \`kind\` invalid or \`tmdb_id\` missing (\`"kind and tmdb_id are required"\`). |
| 500 | \`WATCHLIST_ERROR\` | The delete failed (\`"Failed"\`). |

**Example**

\`\`\`bash
curl -X DELETE -H "Authorization: Bearer $API_KEY" \\
  -H "Content-Type: application/json" \\
  http://localhost:8008/api/v1/video/watchlist \\
  -d '{"kind": "studio", "tmdb_id": 2}'
\`\`\`

### \`POST /api/v1/video/scan\`

Ask for a background scan of the video library. The scan *reads* the media
server (the source of truth) into SoulSync's video database; it is not the
same as telling Plex/Jellyfin to rescan its own folders (that is a separate
internal action, not on this v1 surface).

**Request body**

| Name | Type | Required | Default | Description |
|------|------|----------|---------|-------------|
| mode | string | no | \`full\` | \`incremental\` \\| \`full\` \\| \`deep\`. Any other value silently normalizes to \`full\`. (Below a small library size an incremental scan falls back to a full pass.) |
| media_type | string | no | \`all\` | \`movie\` \\| \`show\` \\| \`all\`. Friendly aliases are accepted: \`movies\`/\`film\`/\`films\` → movie; \`shows\`/\`tv\`/\`series\`/\`episode\`/\`episodes\` → show. |

**Response**

\`data\` is \`{"status": "started", "mode": ..., "media_type": ...}\` when a
scan was kicked off, or \`{"status": "in_progress"}\` (HTTP 200, not an
error) when a scan is already running.

\`\`\`json
{
  "success": true,
  "data": {"status": "started", "mode": "full", "media_type": "all"},
  "error": null,
  "pagination": null
}
\`\`\`

**Errors**

Effectively none — an already-running scan is reported as
\`"in_progress"\` (HTTP 200), not rejected. (The relay does wire a
\`409\`/\`CONFLICT\` path with the message \`"A video scan is already running."\`,
but the current scan handler never returns 409, so that path is
unreachable today.)

**Example**

\`\`\`bash
curl -X POST -H "Authorization: Bearer $API_KEY" \\
  -H "Content-Type: application/json" \\
  http://localhost:8008/api/v1/video/scan \\
  -d '{"mode": "incremental", "media_type": "movie"}'
\`\`\`

### \`GET /api/v1/video/scan/status\`

Current scan progress/state. Idle servers answer \`{"state": "idle"}\`. While
scanning, the dict carries \`state\`, \`phase\`, \`mode\`, \`media_type\`,
\`started_at\` (unix time), \`percent\`, and \`movies\`/\`shows\`/\`episodes\`
counters; finished scans add \`finished_at\` (and \`error\` when the scan
failed, e.g. no video server connected).

**Response**

\`data\` is the scanner status dict, e.g.:

\`\`\`json
{
  "success": true,
  "data": {"state": "scanning", "phase": "starting", "mode": "full",
            "media_type": "all", "started_at": 1759276800.0,
            "percent": null, "movies": 0, "shows": 0, "episodes": 0},
  "error": null,
  "pagination": null
}
\`\`\`

**Example**

\`\`\`bash
curl -H "Authorization: Bearer $API_KEY" \\
  http://localhost:8008/api/v1/video/scan/status
\`\`\`

### \`GET /api/v1/video/downloads\`

The active video download queue (also nudges the download monitor to make
sure it is running).

**Response**

\`data\` is \`{"downloads": [...]}\` — one object per active download row
(progress, status, source, media ids, and annotated upgrade-watch/pack
info where applicable).

**Example**

\`\`\`bash
curl -H "Authorization: Bearer $API_KEY" \\
  http://localhost:8008/api/v1/video/downloads
\`\`\`

### \`GET /api/v1/video/downloads/status\`

Lightweight live-tracking lookup for a single download. Look it up by its
SoulSync download row id, or find the most relevant download for a title by
its media id.

**Query parameters**

| Name | Type | Required | Default | Description |
|------|------|----------|---------|-------------|
| id | int | no* | — | The download row id. (*One of \`id\` or \`media_id\` is needed; with neither the answer is \`{"download": null}\`.) |
| media_id | string | no* | — | The title's media id (e.g. TMDB id). Returns the most relevant download for that title — active first, else the most recent. |
| media_source | string | no | — | Narrows the \`media_id\` lookup to one source. |

**Response**

\`data\` is \`{"download": {...}|null}\`.

**Example**

\`\`\`bash
curl -H "Authorization: Bearer $API_KEY" \\
  "http://localhost:8008/api/v1/video/downloads/status?media_id=693134&media_source=tmdb"
\`\`\`

### \`GET /api/v1/video/downloads/history\`

The permanent, paged history of grabs — movies, episodes, and YouTube
videos. \`counts\` is always included, so one call feeds both the table and
the tab badges.

**Query parameters**

| Name | Type | Required | Default | Description |
|------|------|----------|---------|-------------|
| kind | string | no | — | \`movie\` \\| \`show\` \\| \`youtube\`. Omit for all kinds. |
| search | string | no | \`""\` | Substring filter. |
| outcome | string | no | — | Filter by grab outcome. |
| page | int | no | \`1\` | Page number. |
| limit | int | no | \`40\` | Rows per page. |

**Response**

\`data\` is \`{"counts": {...}, "items": [...], "pagination": {"page",
"total_pages", "total_count", "has_prev", "has_next"}}\`, newest first.

**Errors**

| Status | Code | When |
|--------|------|------|
| 500 | \`VIDEO_ERROR\` | The history query failed (\`"Failed to load history"\`). |

**Example**

\`\`\`bash
curl -H "Authorization: Bearer $API_KEY" \\
  "http://localhost:8008/api/v1/video/downloads/history?kind=movie&limit=20"
\`\`\`

### \`GET /api/v1/video/calendar\`

Upcoming and recent episodes plus movie release events in a date window —
the calendar page as data. Episodes default to followed (watchlist) shows;
movie events are wishlisted movies' cinema and home-availability dates.

**Query parameters**

| Name | Type | Required | Default | Description |
|------|------|----------|---------|-------------|
| days | int | no | \`7\` | Window length in days, clamped to 1–31. (There is no \`end\` param: the window ends at \`start + days - 1\`.) |
| start | string | no | today | Window start as \`YYYY-MM-DD\`. Unparseable values, or dates more than 400 days from today, fall back to today. |
| scope | string | no | \`watchlist\` | \`watchlist\` (followed shows) or \`all\` (every airing show in the library). |

**Response**

\`data\` carries \`today\` (real today, for highlighting), \`start\`, \`end\`,
\`days\`, \`scope\`, \`counts_by_date\` (per-date counts that drive the day
strip), \`total\`, \`owned\`, \`acq_counts\` (acquisition-state summary),
\`needs_action\`, \`schedule\` (schedule freshness), \`episodes\` (each with air
date, \`has_file\`, and computed \`acq\` acquisition state), and \`movies\`
(release events).

**Errors**

| Status | Code | When |
|--------|------|------|
| 500 | \`VIDEO_ERROR\` | Calendar generation failed (\`"calendar failed"\`). |

**Example**

\`\`\`bash
curl -H "Authorization: Bearer $API_KEY" \\
  "http://localhost:8008/api/v1/video/calendar?days=14&scope=all"
\`\`\`

**Notes**

- On a kids/restricted profile, episodes are filtered by their show's
  rating and movie events by the library's rating cap.

### \`GET /api/v1/video/requests\`

List video download requests — a viewer asks, an admin approves. Under
API-key auth (admin rights) this returns **everyone's** requests and
\`quota\` is \`null\`. Each row carries \`in_library\` (the title has since
arrived in the video library) and progress annotations, plus \`counts\` for
the status tabs. An arrival sweep runs first, so newly-arrived titles are
stamped before the list is built.

**Query parameters**

| Name | Type | Required | Default | Description |
|------|------|----------|---------|-------------|
| status | string | no | — | Filter by request status (e.g. \`pending\`). Omit for all. |

**Response**

\`data\` is \`{"requests": [...], "counts": {...}, "pending": <n>,
"quota": null}\`.

**Example**

\`\`\`bash
curl -H "Authorization: Bearer $API_KEY" \\
  "http://localhost:8008/api/v1/video/requests?status=pending"
\`\`\`

### \`POST /api/v1/video/requests\`

File a request for a movie or show. \`title\`/\`year\`/\`poster_url\` are resolved
from TMDB from the \`tmdb_id\` — they are **not** taken from the request
body. Idempotent per requester/kind/TMDB id while pending: re-filing an
already-pending title returns success with \`"already": true\` instead of a
duplicate row.

**Request body**

| Name | Type | Required | Description |
|------|------|----------|-------------|
| kind | string | **yes** | \`movie\` \\| \`show\`. |
| tmdb_id | int | **yes** | TMDB id (flexibly parsed). |
| note | string | no | Requester note, truncated to 500 characters. |
| monitor | string | no | Which episodes to want if approved (show semantics; resolved through the monitor policy). |
| quality_profile_id | int | no | A named quality profile id that exists; anything else is ignored (falls back to default). |
| title | string | no | Ignored for metadata — the TMDB title is authoritative. (A request whose TMDB title cannot be resolved is rejected.) |
| year | int | no | Ignored — from TMDB. |
| poster_url | string | no | Ignored — from TMDB. |

**Response**

\`data\` is \`{"id": <request_id>, "already": <bool>}\`.

**Errors**

| Status | Code | When |
|--------|------|------|
| 400 | \`REQUEST_ERROR\` | \`kind\` invalid, \`tmdb_id\` unparseable, or TMDB returned no title (\`"kind, tmdb_id and title are required"\`). |
| 409 | \`CONFLICT\` | The movie is already in the library (\`"That's already in the library."\`). Note: the internal \`in_library: true\` flag does not survive the v1 envelope — \`data\` is \`null\` on errors, only the message carries through. |
| 429 | \`REQUEST_ERROR\` | A limited profile is over its request quota (quota message + quota state). Does not apply to admin/API-key callers. |
| 500 | \`REQUEST_ERROR\` | The request could not be filed (\`"Could not file the request."\`). |

**Example**

\`\`\`bash
curl -X POST -H "Authorization: Bearer $API_KEY" \\
  -H "Content-Type: application/json" \\
  http://localhost:8008/api/v1/video/requests \\
  -d '{"kind": "movie", "tmdb_id": 693134, "note": "for movie night"}'
\`\`\`

**Notes**

- Filing fires the \`video_request_created\` automation trigger.

### \`POST /api/v1/video/requests/{request_id}/approve\`

Admin-only (passes under API-key auth). Approving moves the title into
acquisition: a movie goes to the video wishlist, a show goes to the
watchlist with the monitor policy expanded into wished episodes. **Every**
pending request for the same title is approved together, and every
requester is notified. The admin may override the requester's monitor pick
and attach a response note. Rows are claimed first (pending → approved in
one statement); if the wishlist/watchlist write fails they are unclaimed
and the request stays pending.

**Path parameters**

| Name | Type | Required | Description |
|------|------|----------|-------------|
| request_id | int | **yes** | The request id. |

**Request body** (all optional)

| Name | Type | Description |
|------|------|-------------|
| monitor | string | Override the episode policy for this approval (show only). |
| response | string | Admin note to the requesters, truncated to 500 characters. |
| quality_profile_id | int | Quality profile to acquire with (falls back to the request's own). |

**Response**

\`data\` is \`{"wished": <n>, "kind": "movie"|"show", "approved": <n>}\` —
\`approved\` counts every pending request row for the title that was resolved
together.

**Errors**

| Status | Code | When |
|--------|------|------|
| 403 | \`REQUEST_ERROR\` | Not an admin (\`"Admin only."\`). |
| 404 | \`REQUEST_ERROR\` | Unknown request id (\`"Unknown request."\`). |
| 409 | \`CONFLICT\` | Already resolved (\`"Already resolved."\`) — the relay maps every internal 409 to the \`CONFLICT\` code. |
| 500 | \`REQUEST_ERROR\` | The wishlist/watchlist write failed — the request is left pending (\`"Could not add the title — request left pending."\`). |

**Example**

\`\`\`bash
curl -X POST -H "Authorization: Bearer $API_KEY" \\
  -H "Content-Type: application/json" \\
  http://localhost:8008/api/v1/video/requests/42/approve \\
  -d '{"response": "grabbing it now"}'
\`\`\`

**Notes**

- Approval fires the \`video_request_approved\` automation trigger.

### \`POST /api/v1/video/requests/{request_id}/deny\`

Admin-only (passes under API-key auth). Declines the title for **everyone**
who asked for it, with an optional note; each requester is notified.
Fires the \`video_request_denied\` automation trigger.

**Path parameters**

| Name | Type | Required | Description |
|------|------|----------|-------------|
| request_id | int | **yes** | The request id. |

**Request body**

| Name | Type | Required | Description |
|------|------|----------|-------------|
| response | string | no | Decline note, truncated to 500 characters. |

**Response**

\`data\` is \`{"denied": <n>}\` — the number of pending request rows declined
together.

**Errors**

| Status | Code | When |
|--------|------|------|
| 403 | \`REQUEST_ERROR\` | Not an admin (\`"Admin only."\`). |
| 404 | \`REQUEST_ERROR\` | Unknown or already-resolved request (\`"Unknown or already-resolved request."\`). |

**Example**

\`\`\`bash
curl -X POST -H "Authorization: Bearer $API_KEY" \\
  -H "Content-Type: application/json" \\
  http://localhost:8008/api/v1/video/requests/42/deny \\
  -d '{"response": "not available in our region"}'
\`\`\`
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
