# SoulSync REST API

SoulSync includes a full REST API at `/api/v1/` that lets you control everything from external apps, scripts, Discord bots, Home Assistant, or anything that can make HTTP requests.

## Quick Start

### 1. Generate an API Key

Go to **Settings** in the SoulSync web UI and find the **SoulSync API** section. Click **Generate API Key**, give it a label, and copy the key immediately — it's only shown once.

Alternatively, if no keys exist yet, use the bootstrap endpoint:

```bash
curl -X POST http://localhost:8008/api/v1/api-keys/bootstrap \
  -H "Content-Type: application/json" \
  -d '{"label": "My First Key"}'
```

### 2. Make Requests

Pass your key via the `Authorization` header:

```bash
curl -H "Authorization: Bearer sk_your_key_here" \
  http://localhost:8008/api/v1/system/status
```

Or as a query parameter:

```
http://localhost:8008/api/v1/system/status?api_key=sk_your_key_here
```

### 3. Response Format

Every response follows this format:

```json
{
  "success": true,
  "data": { ... },
  "error": null,
  "pagination": null
}
```

Errors:

```json
{
  "success": false,
  "data": null,
  "error": {
    "code": "NOT_FOUND",
    "message": "Artist 999 not found."
  }
}
```

---

## Authentication

All `/api/v1/` endpoints require an API key (except the bootstrap endpoint).

| Method | Details |
|--------|---------|
| Header | `Authorization: Bearer sk_...` |
| Query  | `?api_key=sk_...` |

Keys are generated as `sk_` followed by a random token. Only the SHA-256 hash is stored — the raw key is shown once at creation.

### Error Codes

| Status | Code | Meaning |
|--------|------|---------|
| 401 | `AUTH_REQUIRED` | No API key provided |
| 403 | `INVALID_KEY` | API key is wrong or revoked |

---

## Rate Limiting

Requests are rate-limited per IP address:

| Endpoint Type | Limit |
|---------------|-------|
| Read (GET) | 60/min |
| Search (POST /search/*) | 20/min |
| Write (POST/DELETE/PATCH) | 30/min |
| Downloads (POST /downloads) | 10/min |
| System polling (GET /system/*) | 120/min |

Exceeding the limit returns `429 RATE_LIMITED`.

---

## Endpoints

### System

#### `GET /api/v1/system/status`

Server status, uptime, and service connectivity.

```json
{
  "data": {
    "uptime": "2h 15m 30s",
    "uptime_seconds": 8130,
    "services": {
      "spotify": true,
      "soulseek": true,
      "hydrabase": false
    }
  }
}
```

#### `GET /api/v1/system/activity`

Recent activity feed.

#### `GET /api/v1/system/stats`

Combined library and download statistics.

---

### Library

#### `GET /api/v1/library/artists`

List library artists with search and pagination.

| Param | Type | Default | Description |
|-------|------|---------|-------------|
| `search` | string | | Filter by name |
| `letter` | string | `all` | Filter by first letter (a-z, #) |
| `page` | int | 1 | Page number |
| `limit` | int | 50 | Items per page (max 200) |
| `watchlist` | string | `all` | `all`, `watched`, or `unwatched` |

#### `GET /api/v1/library/artists/<artist_id>`

Get artist details with album list.

#### `GET /api/v1/library/artists/<artist_id>/albums`

List albums for an artist.

#### `GET /api/v1/library/albums/<album_id>/tracks`

List tracks in an album.

#### `GET /api/v1/library/tracks`

Search tracks by title and/or artist.

| Param | Type | Description |
|-------|------|-------------|
| `title` | string | Track title to search |
| `artist` | string | Artist name to search |
| `limit` | int | Max results (default 50, max 200) |

#### `GET /api/v1/library/stats`

Library statistics (artist/album/track counts, database info).

---

### Search

Search external music sources (Spotify, iTunes, Hydrabase).

#### `POST /api/v1/search/tracks`

```json
{
  "query": "Daft Punk Around the World",
  "source": "auto",
  "limit": 20
}
```

`source`: `"auto"` (default — tries Hydrabase, then Spotify, then iTunes), `"spotify"`, or `"itunes"`.

#### `POST /api/v1/search/albums`

```json
{
  "query": "Discovery",
  "limit": 10
}
```

#### `POST /api/v1/search/artists`

```json
{
  "query": "Daft Punk",
  "limit": 10
}
```

---

### Downloads

#### `GET /api/v1/downloads`

List active and recent download tasks.

#### `POST /api/v1/downloads/<download_id>/cancel`

Cancel a specific download.

```json
{
  "username": "soulseek_username"
}
```

#### `POST /api/v1/downloads/cancel-all`

Cancel all active downloads and clear completed ones.

---

### Wishlist

Tracks that failed to download, queued for retry.

#### `GET /api/v1/wishlist`

List wishlist tracks.

| Param | Type | Description |
|-------|------|-------------|
| `category` | string | `singles` or `albums` |
| `page` | int | Page number |
| `limit` | int | Items per page |

#### `POST /api/v1/wishlist`

Add a track to the wishlist.

```json
{
  "spotify_track_data": { "id": "...", "name": "...", "artists": [...] },
  "failure_reason": "No sources found",
  "source_type": "api"
}
```

#### `DELETE /api/v1/wishlist/<track_id>`

Remove a track from the wishlist.

#### `POST /api/v1/wishlist/process`

Trigger wishlist download processing.

---

### Watchlist

Artists being monitored for new releases.

#### `GET /api/v1/watchlist`

List all watched artists.

#### `POST /api/v1/watchlist`

Add an artist to the watchlist.

```json
{
  "artist_id": "4tZwfgrHOc3mvqYlEYSvnL",
  "artist_name": "Daft Punk"
}
```

#### `DELETE /api/v1/watchlist/<artist_id>`

Remove an artist from the watchlist.

#### `POST /api/v1/watchlist/scan`

Trigger a watchlist scan for new releases.

---

### Playlists

#### `GET /api/v1/playlists`

List user playlists.

| Param | Type | Default | Description |
|-------|------|---------|-------------|
| `source` | string | `spotify` | `spotify` or `tidal` |

#### `GET /api/v1/playlists/<playlist_id>`

Get playlist details with tracks.

#### `POST /api/v1/playlists/<playlist_id>/sync`

Trigger playlist sync/download.

```json
{
  "playlist_name": "My Playlist",
  "tracks": [...]
}
```

---

### Settings

#### `GET /api/v1/settings`

Get current settings (sensitive values like passwords and tokens are redacted).

#### `PATCH /api/v1/settings`

Update settings (partial update).

```json
{
  "soulseek.search_timeout": 90,
  "logging.level": "DEBUG"
}
```

---

### API Key Management

#### `GET /api/v1/api-keys`

List all API keys (shows prefix and label only, never the full key).

#### `POST /api/v1/api-keys`

Generate a new API key.

```json
{
  "label": "Discord Bot"
}
```

Response includes the raw key (shown only once):

```json
{
  "data": {
    "key": "sk_a3Bf9x2Kp7...",
    "id": "uuid",
    "label": "Discord Bot",
    "key_prefix": "sk_a3Bf9x2",
    "created_at": "2026-03-03T12:00:00Z"
  }
}
```

#### `DELETE /api/v1/api-keys/<key_id>`

Revoke an API key.

#### `POST /api/v1/api-keys/bootstrap`

Generate the first API key when none exist (no auth required). Returns 403 if keys already exist.

---

## Examples

### Python

```python
import requests

API_URL = "http://localhost:8008/api/v1"
API_KEY = "sk_your_key_here"

headers = {"Authorization": f"Bearer {API_KEY}"}

# Search for tracks
resp = requests.post(f"{API_URL}/search/tracks",
    headers=headers,
    json={"query": "Daft Punk", "limit": 5})
tracks = resp.json()["data"]["tracks"]

# Add artist to watchlist
requests.post(f"{API_URL}/watchlist",
    headers=headers,
    json={"artist_id": "4tZwfgrHOc3mvqYlEYSvnL", "artist_name": "Daft Punk"})

# Check system status
status = requests.get(f"{API_URL}/system/status", headers=headers).json()
print(f"Uptime: {status['data']['uptime']}")
```

### JavaScript

```javascript
const API_URL = 'http://localhost:8008/api/v1';
const API_KEY = 'sk_your_key_here';

const headers = {
  'Authorization': `Bearer ${API_KEY}`,
  'Content-Type': 'application/json'
};

// Browse library
const artists = await fetch(`${API_URL}/library/artists?page=1&limit=25`, { headers })
  .then(r => r.json());

// Trigger watchlist scan
await fetch(`${API_URL}/watchlist/scan`, { method: 'POST', headers });
```

### curl

```bash
# System status
curl -H "Authorization: Bearer sk_..." http://localhost:8008/api/v1/system/status

# Search tracks
curl -X POST http://localhost:8008/api/v1/search/tracks \
  -H "Authorization: Bearer sk_..." \
  -H "Content-Type: application/json" \
  -d '{"query": "Boards of Canada", "limit": 5}'

# Library artists page 1
curl -H "Authorization: Bearer sk_..." \
  "http://localhost:8008/api/v1/library/artists?page=1&limit=25"
```
