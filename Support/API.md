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

Every response follows this envelope:

```json
{
  "success": true,
  "data": { ... },
  "error": null,
  "pagination": null
}
```

Error responses:

```json
{
  "success": false,
  "data": null,
  "error": {
    "code": "NOT_FOUND",
    "message": "Artist 999 not found."
  },
  "pagination": null
}
```

Paginated responses include:

```json
{
  "pagination": {
    "page": 1,
    "limit": 50,
    "total": 347,
    "total_pages": 7,
    "has_next": true,
    "has_prev": false
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

Requests are rate-limited to **60 per minute** per IP address.

Exceeding the limit returns `429 RATE_LIMITED`.

---

## Global Query Parameters

These optional parameters work on all endpoints that return entity data:

| Param | Type | Description |
|-------|------|-------------|
| `fields` | string | Comma-separated list of fields to return (e.g. `?fields=id,name,thumb_url`). Omit to return all fields. |

---

## Multi-Profile Support

SoulSync supports multiple user profiles. Profile-scoped endpoints (watchlist, wishlist, discovery) accept a profile identifier:

| Method | Details |
|--------|---------|
| Header | `X-Profile-Id: 2` |
| Query  | `?profile_id=2` |

If omitted, defaults to profile 1 (admin). Profile scoping applies to: watchlist, wishlist, and discovery endpoints.

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

```json
{
  "data": {
    "activities": [
      { "type": "download", "message": "Downloaded Track Name", "timestamp": "..." },
      ...
    ]
  }
}
```

#### `GET /api/v1/system/stats`

Combined library and download statistics.

```json
{
  "data": {
    "library": {
      "artists": 1250,
      "albums": 4830,
      "tracks": 52100
    },
    "database": {
      "size_mb": 145.2,
      "last_update": "2026-03-04T09:00:00"
    },
    "downloads": {
      "active": 3
    }
  }
}
```

---

### Library — Artists

#### `GET /api/v1/library/artists`

List library artists with search, letter filtering, and pagination.

| Param | Type | Default | Description |
|-------|------|---------|-------------|
| `search` | string | | Filter by name |
| `letter` | string | `all` | Filter by first letter (a-z, `#` for non-alpha) |
| `page` | int | 1 | Page number |
| `limit` | int | 50 | Items per page (max 200) |
| `watchlist` | string | `all` | `all`, `watched`, or `unwatched` |
| `fields` | string | | Comma-separated field list |

**Response:**

```json
{
  "data": {
    "artists": [
      {
        "id": 42,
        "name": "Radiohead",
        "thumb_url": null,
        "banner_url": null,
        "genres": ["alternative rock", "art rock"],
        "summary": null,
        "style": null,
        "mood": null,
        "label": null,
        "server_source": null,
        "created_at": null,
        "updated_at": null,
        "musicbrainz_id": "a74b1b7f-71a5-4011-9441-d0b5e4122711",
        "spotify_artist_id": "4Z8W4fKeB5YxbusRsdQVPb",
        "itunes_artist_id": "657515",
        "audiodb_id": "111239",
        "deezer_id": "399",
        "musicbrainz_match_status": null,
        "spotify_match_status": null,
        "itunes_match_status": null,
        "audiodb_match_status": null,
        "deezer_match_status": null,
        "musicbrainz_last_attempted": null,
        "spotify_last_attempted": null,
        "itunes_last_attempted": null,
        "audiodb_last_attempted": null,
        "deezer_last_attempted": null,
        "album_count": 9,
        "track_count": 101,
        "is_watched": true,
        "image_url": "https://..."
      }
    ]
  },
  "pagination": { "page": 1, "limit": 50, "total": 1250, "total_pages": 25, "has_next": true, "has_prev": false }
}
```

> **Note:** The list endpoint returns a subset of metadata fields. Some fields like `summary`, `style`, `mood`, `label`, `banner_url`, and all `*_match_status` / `*_last_attempted` timestamps may be `null` in list view. Use the detail endpoint below for the complete record.

#### `GET /api/v1/library/artists/<artist_id>`

Get a single artist by ID with **all metadata** and their album list.

```json
{
  "data": {
    "artist": {
      "id": 42,
      "name": "Radiohead",
      "thumb_url": "https://i.scdn.co/image/abc123...",
      "banner_url": "https://www.theaudiodb.com/images/media/artist/fanart/...",
      "genres": ["alternative rock", "art rock", "experimental"],
      "summary": "Radiohead are an English rock band formed in Abingdon...",
      "style": "Alternative/Indie",
      "mood": "Melancholy",
      "label": "XL Recordings",
      "server_source": "plex",
      "created_at": "2025-12-01T14:30:00",
      "updated_at": "2026-02-15T09:12:00",
      "musicbrainz_id": "a74b1b7f-71a5-4011-9441-d0b5e4122711",
      "spotify_artist_id": "4Z8W4fKeB5YxbusRsdQVPb",
      "itunes_artist_id": "657515",
      "audiodb_id": "111239",
      "deezer_id": "399",
      "musicbrainz_match_status": "matched",
      "spotify_match_status": "matched",
      "itunes_match_status": "matched",
      "audiodb_match_status": "matched",
      "deezer_match_status": "matched",
      "musicbrainz_last_attempted": "2026-01-10T08:00:00",
      "spotify_last_attempted": "2026-01-10T08:00:00",
      "itunes_last_attempted": "2026-01-10T08:00:00",
      "audiodb_last_attempted": "2026-01-10T08:00:00",
      "deezer_last_attempted": "2026-01-10T08:00:00"
    },
    "albums": [
      {
        "id": 87,
        "artist_id": 42,
        "title": "OK Computer",
        "year": 1997,
        "...": "..."
      }
    ]
  }
}
```

**Artist fields:**

| Field | Type | Description |
|-------|------|-------------|
| `id` | int | Internal database ID |
| `name` | string | Artist name |
| `thumb_url` | string? | Artist thumbnail/profile image URL |
| `banner_url` | string? | Artist banner/fanart image URL (from AudioDB) |
| `genres` | string[] | List of genre tags |
| `summary` | string? | Artist biography/description |
| `style` | string? | Musical style (from AudioDB) |
| `mood` | string? | Musical mood (from AudioDB) |
| `label` | string? | Record label (from AudioDB) |
| `server_source` | string? | Media server source (`plex`, `jellyfin`, `navidrome`) |
| `created_at` | string? | ISO 8601 timestamp when added to library |
| `updated_at` | string? | ISO 8601 timestamp of last update |
| `musicbrainz_id` | string? | MusicBrainz artist MBID |
| `spotify_artist_id` | string? | Spotify artist ID |
| `itunes_artist_id` | string? | Apple Music / iTunes artist ID |
| `audiodb_id` | string? | TheAudioDB artist ID |
| `deezer_id` | string? | Deezer artist ID |
| `musicbrainz_match_status` | string? | MusicBrainz enrichment status (`matched`, `not_found`, `error`) |
| `spotify_match_status` | string? | Spotify enrichment status |
| `itunes_match_status` | string? | iTunes enrichment status |
| `audiodb_match_status` | string? | AudioDB enrichment status |
| `deezer_match_status` | string? | Deezer enrichment status |
| `musicbrainz_last_attempted` | string? | ISO 8601 timestamp of last MusicBrainz lookup |
| `spotify_last_attempted` | string? | ISO 8601 timestamp of last Spotify lookup |
| `itunes_last_attempted` | string? | ISO 8601 timestamp of last iTunes lookup |
| `audiodb_last_attempted` | string? | ISO 8601 timestamp of last AudioDB lookup |
| `deezer_last_attempted` | string? | ISO 8601 timestamp of last Deezer lookup |

> Fields marked `?` may be `null` if the data hasn't been enriched from that provider yet.

#### `GET /api/v1/library/artists/<artist_id>/albums`

List all albums for a specific artist.

---

### Library — Albums

#### `GET /api/v1/library/albums`

List/search all albums with pagination and optional filters.

| Param | Type | Default | Description |
|-------|------|---------|-------------|
| `search` | string | | Filter by album title |
| `artist_id` | int | | Filter by artist ID |
| `year` | int | | Filter by release year |
| `page` | int | 1 | Page number |
| `limit` | int | 50 | Items per page (max 200) |
| `fields` | string | | Comma-separated field list |

#### `GET /api/v1/library/albums/<album_id>`

Get a single album by ID with **all metadata** and embedded track list.

```json
{
  "data": {
    "album": {
      "id": 87,
      "artist_id": 42,
      "title": "OK Computer",
      "year": 1997,
      "thumb_url": "https://i.scdn.co/image/...",
      "genres": ["alternative rock"],
      "track_count": 12,
      "duration": 3198000,
      "style": "Art Rock",
      "mood": "Anxious",
      "label": "Parlophone",
      "explicit": false,
      "record_type": "album",
      "server_source": "plex",
      "created_at": "2025-12-01T14:30:00",
      "updated_at": "2026-02-15T09:12:00",
      "musicbrainz_release_id": "a1c35a51-d102-4ce7-b7b0-8a4f68385bb2",
      "spotify_album_id": "6dVIqQ8qmQ5GBnJ9shOYGE",
      "itunes_album_id": "1097862703",
      "audiodb_id": "2110483",
      "deezer_id": "6575789",
      "musicbrainz_match_status": "matched",
      "spotify_match_status": "matched",
      "itunes_match_status": "matched",
      "audiodb_match_status": "matched",
      "deezer_match_status": "matched",
      "musicbrainz_last_attempted": "2026-01-10T08:00:00",
      "spotify_last_attempted": "2026-01-10T08:00:00",
      "itunes_last_attempted": "2026-01-10T08:00:00",
      "audiodb_last_attempted": "2026-01-10T08:00:00",
      "deezer_last_attempted": "2026-01-10T08:00:00"
    },
    "tracks": [
      {
        "id": 510,
        "title": "Airbag",
        "track_number": 1,
        "...": "..."
      }
    ]
  }
}
```

**Album fields:**

| Field | Type | Description |
|-------|------|-------------|
| `id` | int | Internal database ID |
| `artist_id` | int | Parent artist ID |
| `title` | string | Album title |
| `year` | int? | Release year |
| `thumb_url` | string? | Album cover art URL |
| `genres` | string[] | Genre tags |
| `track_count` | int? | Number of tracks |
| `duration` | int? | Total duration in milliseconds |
| `style` | string? | Musical style (from AudioDB) |
| `mood` | string? | Musical mood (from AudioDB) |
| `label` | string? | Record label |
| `explicit` | bool? | Whether album contains explicit content |
| `record_type` | string? | Album type (`album`, `single`, `ep`, `compilation`) |
| `server_source` | string? | Media server source |
| `created_at` | string? | ISO 8601 timestamp |
| `updated_at` | string? | ISO 8601 timestamp |
| `musicbrainz_release_id` | string? | MusicBrainz release MBID |
| `spotify_album_id` | string? | Spotify album ID |
| `itunes_album_id` | string? | Apple Music / iTunes album ID |
| `audiodb_id` | string? | TheAudioDB album ID |
| `deezer_id` | string? | Deezer album ID |
| `musicbrainz_match_status` | string? | MusicBrainz enrichment status |
| `spotify_match_status` | string? | Spotify enrichment status |
| `itunes_match_status` | string? | iTunes enrichment status |
| `audiodb_match_status` | string? | AudioDB enrichment status |
| `deezer_match_status` | string? | Deezer enrichment status |
| `musicbrainz_last_attempted` | string? | ISO 8601 timestamp |
| `spotify_last_attempted` | string? | ISO 8601 timestamp |
| `itunes_last_attempted` | string? | ISO 8601 timestamp |
| `audiodb_last_attempted` | string? | ISO 8601 timestamp |
| `deezer_last_attempted` | string? | ISO 8601 timestamp |

#### `GET /api/v1/library/albums/<album_id>/tracks`

List all tracks in an album with full metadata.

---

### Library — Tracks

#### `GET /api/v1/library/tracks/<track_id>`

Get a single track by ID with **all metadata**.

```json
{
  "data": {
    "track": {
      "id": 512,
      "album_id": 87,
      "artist_id": 42,
      "title": "Paranoid Android",
      "artist_name": "Radiohead",
      "album_title": "OK Computer",
      "track_number": 2,
      "duration": 383000,
      "file_path": "/music/Radiohead/OK Computer/02 - Paranoid Android.flac",
      "bitrate": 1024,
      "bpm": 82.5,
      "explicit": false,
      "style": "Art Rock",
      "mood": "Anxious",
      "repair_status": null,
      "repair_last_checked": null,
      "server_source": "plex",
      "created_at": "2025-12-01T14:30:00",
      "updated_at": "2026-02-15T09:12:00",
      "musicbrainz_recording_id": "b3e2b7e0-a147-4b3c-8eab-fd90bfff7e74",
      "spotify_track_id": "6LgJvl0Xdtc73RJ1mN1a7Z",
      "itunes_track_id": "1097863011",
      "audiodb_id": null,
      "deezer_id": "119606528",
      "musicbrainz_match_status": "matched",
      "spotify_match_status": "matched",
      "itunes_match_status": "matched",
      "audiodb_match_status": null,
      "deezer_match_status": "matched",
      "musicbrainz_last_attempted": "2026-01-10T08:00:00",
      "spotify_last_attempted": "2026-01-10T08:00:00",
      "itunes_last_attempted": "2026-01-10T08:00:00",
      "audiodb_last_attempted": null,
      "deezer_last_attempted": "2026-01-10T08:00:00"
    }
  }
}
```

**Track fields:**

| Field | Type | Description |
|-------|------|-------------|
| `id` | int | Internal database ID |
| `album_id` | int | Parent album ID |
| `artist_id` | int | Parent artist ID |
| `title` | string | Track title |
| `artist_name` | string? | Artist name (joined from artists table) |
| `album_title` | string? | Album title (joined from albums table) |
| `track_number` | int? | Track number on the album |
| `duration` | int? | Duration in milliseconds |
| `file_path` | string? | File path on the media server |
| `bitrate` | int? | Audio bitrate in kbps |
| `bpm` | float? | Beats per minute |
| `explicit` | bool? | Whether track contains explicit content |
| `style` | string? | Musical style (from AudioDB) |
| `mood` | string? | Musical mood (from AudioDB) |
| `repair_status` | string? | Track repair status |
| `repair_last_checked` | string? | ISO 8601 timestamp of last repair check |
| `server_source` | string? | Media server source |
| `created_at` | string? | ISO 8601 timestamp |
| `updated_at` | string? | ISO 8601 timestamp |
| `musicbrainz_recording_id` | string? | MusicBrainz recording MBID |
| `spotify_track_id` | string? | Spotify track ID |
| `itunes_track_id` | string? | Apple Music / iTunes track ID |
| `audiodb_id` | string? | TheAudioDB track ID |
| `deezer_id` | string? | Deezer track ID |
| `musicbrainz_match_status` | string? | MusicBrainz enrichment status |
| `spotify_match_status` | string? | Spotify enrichment status |
| `itunes_match_status` | string? | iTunes enrichment status |
| `audiodb_match_status` | string? | AudioDB enrichment status |
| `deezer_match_status` | string? | Deezer enrichment status |
| `musicbrainz_last_attempted` | string? | ISO 8601 timestamp |
| `spotify_last_attempted` | string? | ISO 8601 timestamp |
| `itunes_last_attempted` | string? | ISO 8601 timestamp |
| `audiodb_last_attempted` | string? | ISO 8601 timestamp |
| `deezer_last_attempted` | string? | ISO 8601 timestamp |

#### `GET /api/v1/library/tracks`

Search tracks by title and/or artist. At least one of `title` or `artist` is required.

| Param | Type | Default | Description |
|-------|------|---------|-------------|
| `title` | string | | Track title to search |
| `artist` | string | | Artist name to search |
| `limit` | int | 50 | Max results (max 200) |
| `fields` | string | | Comma-separated field list |

---

### Library — Genres

#### `GET /api/v1/library/genres`

List all genres in the library with occurrence counts.

| Param | Type | Default | Description |
|-------|------|---------|-------------|
| `source` | string | `artists` | Table to aggregate from: `artists` or `albums` |

```json
{
  "data": {
    "genres": [
      { "name": "rock", "count": 234 },
      { "name": "alternative rock", "count": 189 },
      { "name": "indie rock", "count": 156 },
      { "name": "electronic", "count": 98 },
      { "name": "pop", "count": 87 }
    ],
    "source": "artists"
  }
}
```

---

### Library — Recently Added

#### `GET /api/v1/library/recently-added`

Get recently added content, ordered by creation date.

| Param | Type | Default | Description |
|-------|------|---------|-------------|
| `type` | string | `albums` | Entity type: `albums`, `artists`, or `tracks` |
| `limit` | int | 50 | Max items (max 200) |
| `fields` | string | | Comma-separated field list |

```json
{
  "data": {
    "items": [
      {
        "id": 4831,
        "artist_id": 42,
        "title": "A Moon Shaped Pool",
        "year": 2016,
        "thumb_url": "https://...",
        "genres": ["art rock"],
        "...": "..."
      }
    ],
    "type": "albums"
  }
}
```

---

### Library — External ID Lookup

#### `GET /api/v1/library/lookup`

Look up a library entity by its external provider ID. Useful for cross-referencing with Spotify, MusicBrainz, iTunes, Deezer, or AudioDB.

| Param | Type | Required | Description |
|-------|------|----------|-------------|
| `type` | string | Yes | `artist`, `album`, or `track` |
| `provider` | string | Yes | `spotify`, `musicbrainz`, `itunes`, `deezer`, or `audiodb` |
| `id` | string | Yes | The external ID value |
| `fields` | string | No | Comma-separated field list |

**Example — find an artist by Spotify ID:**

```
GET /api/v1/library/lookup?type=artist&provider=spotify&id=4Z8W4fKeB5YxbusRsdQVPb
```

```json
{
  "data": {
    "artist": {
      "id": 42,
      "name": "Radiohead",
      "spotify_artist_id": "4Z8W4fKeB5YxbusRsdQVPb",
      "...": "..."
    }
  }
}
```

**Example — find a track by MusicBrainz recording ID:**

```
GET /api/v1/library/lookup?type=track&provider=musicbrainz&id=b3e2b7e0-a147-4b3c-8eab-fd90bfff7e74
```

Returns `404 NOT_FOUND` if no matching entity exists in the library.

---

### Library — Stats

#### `GET /api/v1/library/stats`

Library statistics (counts and database info).

```json
{
  "data": {
    "artists": 1250,
    "albums": 4830,
    "tracks": 52100,
    "database_size_mb": 145.2,
    "last_update": "2026-03-04T09:00:00"
  }
}
```

---

### Search

Search external music sources (Spotify, iTunes, Hydrabase). These endpoints search **external services**, not your local library (use `/library/tracks` or `/library/lookup` for that).

#### `POST /api/v1/search/tracks`

```json
{
  "query": "Daft Punk Around the World",
  "source": "auto",
  "limit": 20
}
```

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `query` | string | *required* | Search query |
| `source` | string | `auto` | `auto` (Hydrabase > Spotify > iTunes), `spotify`, or `itunes` |
| `limit` | int | 20 | Max results (max 50) |

**Response:**

```json
{
  "data": {
    "tracks": [
      {
        "id": "2cGxRwrMyEAp8dEbuZaVv6",
        "name": "Around the World",
        "artists": ["Daft Punk"],
        "album": "Homework",
        "duration_ms": 428000,
        "popularity": 78,
        "preview_url": "https://...",
        "image_url": "https://i.scdn.co/image/...",
        "release_date": "1997-01-17"
      }
    ],
    "source": "spotify"
  }
}
```

#### `POST /api/v1/search/albums`

```json
{
  "query": "Discovery",
  "limit": 10
}
```

**Response:**

```json
{
  "data": {
    "albums": [
      {
        "id": "2noRn2Aes5aoNVsU6iWThc",
        "name": "Discovery",
        "artists": ["Daft Punk"],
        "release_date": "2001-03-12",
        "total_tracks": 14,
        "album_type": "album",
        "image_url": "https://..."
      }
    ],
    "source": "spotify"
  }
}
```

#### `POST /api/v1/search/artists`

```json
{
  "query": "Daft Punk",
  "limit": 10
}
```

**Response:**

```json
{
  "data": {
    "artists": [
      {
        "id": "4tZwfgrHOc3mvqYlEYSvnL",
        "name": "Daft Punk",
        "popularity": 82,
        "genres": ["electro", "french house"],
        "followers": 21000000,
        "image_url": "https://..."
      }
    ],
    "source": "spotify"
  }
}
```

---

### Downloads

#### `GET /api/v1/downloads`

List active and recent download tasks.

```json
{
  "data": {
    "downloads": [
      {
        "id": "task_abc123",
        "status": "downloading",
        "track_name": "Paranoid Android",
        "artist_name": "Radiohead",
        "album_name": "OK Computer",
        "username": "soulseek_user_42",
        "filename": "02 - Paranoid Android.flac",
        "progress": 67,
        "size": 45000000,
        "error": null,
        "batch_id": "batch_xyz",
        "track_index": 2,
        "retry_count": 0,
        "metadata_enhanced": false,
        "status_change_time": 1709550000.123
      }
    ]
  }
}
```

**Download fields:**

| Field | Type | Description |
|-------|------|-------------|
| `id` | string | Unique task identifier |
| `status` | string | `pending`, `searching`, `downloading`, `completed`, `failed` |
| `track_name` | string? | Track being downloaded |
| `artist_name` | string? | Artist name |
| `album_name` | string? | Album name |
| `username` | string? | Soulseek peer username |
| `filename` | string? | Remote filename |
| `progress` | int | Download progress percentage (0-100) |
| `size` | int? | File size in bytes |
| `error` | string? | Error message if failed |
| `batch_id` | string? | Batch download group ID |
| `track_index` | int? | Track position in batch |
| `retry_count` | int | Number of retry attempts |
| `metadata_enhanced` | bool | Whether metadata was enhanced post-download |
| `status_change_time` | float? | Unix timestamp of last status change |

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

Tracks that failed to download, queued for retry. Profile-scoped via `X-Profile-Id`.

#### `GET /api/v1/wishlist`

List wishlist tracks with standardized format.

| Param | Type | Default | Description |
|-------|------|---------|-------------|
| `category` | string | | `singles` or `albums` |
| `page` | int | 1 | Page number |
| `limit` | int | 50 | Items per page (max 200) |
| `fields` | string | | Comma-separated field list |

```json
{
  "data": {
    "tracks": [
      {
        "id": 15,
        "spotify_track_id": "6LgJvl0Xdtc73RJ1mN1a7Z",
        "track_name": "Paranoid Android",
        "artist_name": "Radiohead",
        "album_name": "OK Computer",
        "spotify_data": { "...full Spotify track object..." },
        "failure_reason": "No sources found",
        "retry_count": 3,
        "last_attempted": "2026-03-03T15:30:00",
        "date_added": "2026-03-01T10:00:00",
        "source_type": "playlist",
        "source_info": { "playlist_name": "My Playlist", "playlist_id": "..." },
        "profile_id": 1
      }
    ]
  },
  "pagination": { "..." }
}
```

**Wishlist track fields:**

| Field | Type | Description |
|-------|------|-------------|
| `id` | int | Internal database ID |
| `spotify_track_id` | string | Spotify track ID |
| `track_name` | string | Extracted track name |
| `artist_name` | string | Extracted artist name(s) |
| `album_name` | string? | Extracted album name |
| `spotify_data` | object | Full Spotify track metadata object |
| `failure_reason` | string? | Why the download failed |
| `retry_count` | int | Number of retry attempts |
| `last_attempted` | string? | ISO 8601 timestamp of last attempt |
| `date_added` | string? | ISO 8601 timestamp when added |
| `source_type` | string? | How it was added: `playlist`, `album`, `manual`, `api` |
| `source_info` | object? | Context about the source (playlist name, etc.) |
| `profile_id` | int? | Profile this track belongs to |

#### `POST /api/v1/wishlist`

Add a track to the wishlist.

```json
{
  "spotify_track_data": {
    "id": "6LgJvl0Xdtc73RJ1mN1a7Z",
    "name": "Paranoid Android",
    "artists": [{ "name": "Radiohead" }],
    "album": { "name": "OK Computer", "album_type": "album" }
  },
  "failure_reason": "No sources found",
  "source_type": "api"
}
```

#### `DELETE /api/v1/wishlist/<spotify_track_id>`

Remove a track from the wishlist by its Spotify track ID.

#### `POST /api/v1/wishlist/process`

Trigger wishlist download processing (retries all failed tracks).

---

### Watchlist

Artists being monitored for new releases. Profile-scoped via `X-Profile-Id`.

#### `GET /api/v1/watchlist`

List all watched artists for the current profile.

```json
{
  "data": {
    "artists": [
      {
        "id": 5,
        "spotify_artist_id": "4tZwfgrHOc3mvqYlEYSvnL",
        "itunes_artist_id": "5468295",
        "artist_name": "Daft Punk",
        "image_url": "https://i.scdn.co/image/...",
        "date_added": "2026-01-15T10:00:00",
        "last_scan_timestamp": "2026-03-04T06:00:00",
        "created_at": "2026-01-15T10:00:00",
        "updated_at": "2026-03-04T06:00:00",
        "profile_id": 1,
        "include_albums": true,
        "include_eps": true,
        "include_singles": true,
        "include_live": false,
        "include_remixes": false,
        "include_acoustic": false,
        "include_compilations": false
      }
    ]
  }
}
```

**Watchlist artist fields:**

| Field | Type | Description |
|-------|------|-------------|
| `id` | int | Internal database ID |
| `spotify_artist_id` | string? | Spotify artist ID |
| `itunes_artist_id` | string? | iTunes artist ID |
| `artist_name` | string | Artist name |
| `image_url` | string? | Artist image URL |
| `date_added` | string? | ISO 8601 timestamp |
| `last_scan_timestamp` | string? | ISO 8601 timestamp of last scan |
| `created_at` | string? | ISO 8601 timestamp |
| `updated_at` | string? | ISO 8601 timestamp |
| `profile_id` | int? | Profile this entry belongs to |
| `include_albums` | bool | Monitor for new albums |
| `include_eps` | bool | Monitor for new EPs |
| `include_singles` | bool | Monitor for new singles |
| `include_live` | bool | Include live recordings |
| `include_remixes` | bool | Include remixes |
| `include_acoustic` | bool | Include acoustic versions |
| `include_compilations` | bool | Include compilations |

#### `POST /api/v1/watchlist`

Add an artist to the watchlist.

```json
{
  "artist_id": "4tZwfgrHOc3mvqYlEYSvnL",
  "artist_name": "Daft Punk"
}
```

#### `PATCH /api/v1/watchlist/<artist_id>`

Update content type filters for a watched artist without having to remove and re-add them. Only the fields you include in the body will be updated.

```json
{
  "include_live": true,
  "include_remixes": true,
  "include_compilations": false
}
```

Accepts any combination of: `include_albums`, `include_eps`, `include_singles`, `include_live`, `include_remixes`, `include_acoustic`, `include_compilations`.

**Response:**

```json
{
  "data": {
    "message": "Watchlist filters updated.",
    "updated": {
      "include_live": true,
      "include_remixes": true,
      "include_compilations": false
    }
  }
}
```

#### `DELETE /api/v1/watchlist/<artist_id>`

Remove an artist from the watchlist. `artist_id` can be a Spotify or iTunes artist ID.

#### `POST /api/v1/watchlist/scan`

Trigger a watchlist scan for new releases. Returns `409 CONFLICT` if a scan is already running.

---

### Discovery

Browse discovery pool, similar artists, and recent releases. Profile-scoped via `X-Profile-Id`.

#### `GET /api/v1/discover/pool`

List discovery pool tracks with pagination and optional filters.

| Param | Type | Default | Description |
|-------|------|---------|-------------|
| `new_releases_only` | string | `false` | Set to `true` to filter to new releases only |
| `source` | string | | `spotify` or `itunes` (omit for all) |
| `page` | int | 1 | Page number |
| `limit` | int | 100 | Items per page (max 500) |
| `fields` | string | | Comma-separated field list |

```json
{
  "data": {
    "tracks": [
      {
        "id": 1024,
        "spotify_track_id": "3n3Ppam7vgaVa1iaRUc9Lp",
        "spotify_album_id": "2noRn2Aes5aoNVsU6iWThc",
        "spotify_artist_id": "4tZwfgrHOc3mvqYlEYSvnL",
        "itunes_track_id": null,
        "itunes_album_id": null,
        "itunes_artist_id": null,
        "source": "spotify",
        "track_name": "Something About Us",
        "artist_name": "Daft Punk",
        "album_name": "Discovery",
        "album_cover_url": "https://i.scdn.co/image/...",
        "duration_ms": 232000,
        "popularity": 76,
        "release_date": "2001-03-12",
        "is_new_release": false,
        "artist_genres": ["electro", "french house"],
        "added_date": "2026-03-01T12:00:00"
      }
    ]
  },
  "pagination": { "page": 1, "limit": 100, "total": 450, "..." }
}
```

#### `GET /api/v1/discover/similar-artists`

List top similar artists discovered from watchlist analysis.

| Param | Type | Default | Description |
|-------|------|---------|-------------|
| `limit` | int | 50 | Max artists (max 200) |
| `fields` | string | | Comma-separated field list |

```json
{
  "data": {
    "artists": [
      {
        "id": 88,
        "source_artist_id": "4tZwfgrHOc3mvqYlEYSvnL",
        "similar_artist_spotify_id": "12Chz98pHFMPJEknJQMWvI",
        "similar_artist_itunes_id": null,
        "similar_artist_name": "Justice",
        "similarity_rank": 1,
        "occurrence_count": 5,
        "last_updated": "2026-03-01T12:00:00",
        "last_featured": "2026-03-03T08:00:00"
      }
    ]
  }
}
```

#### `GET /api/v1/discover/recent-releases`

List recent releases from watched artists.

| Param | Type | Default | Description |
|-------|------|---------|-------------|
| `limit` | int | 50 | Max releases (max 200) |
| `fields` | string | | Comma-separated field list |

```json
{
  "data": {
    "releases": [
      {
        "id": 12,
        "watchlist_artist_id": 5,
        "album_spotify_id": "2noRn2Aes5aoNVsU6iWThc",
        "album_itunes_id": null,
        "source": "spotify",
        "album_name": "Random Access Memories (10th Anniversary Edition)",
        "release_date": "2023-05-12",
        "album_cover_url": "https://...",
        "track_count": 22,
        "added_date": "2026-03-01T06:00:00"
      }
    ]
  }
}
```

#### `GET /api/v1/discover/pool/metadata`

Get discovery pool metadata (when it was last populated, track count).

```json
{
  "data": {
    "last_populated": "2026-03-04T06:00:00",
    "track_count": 450,
    "updated_at": "2026-03-04T06:00:00"
  }
}
```

---

### Playlists

#### `GET /api/v1/playlists`

List user playlists from Spotify or Tidal.

| Param | Type | Default | Description |
|-------|------|---------|-------------|
| `source` | string | `spotify` | `spotify` or `tidal` |

```json
{
  "data": {
    "playlists": [
      {
        "id": "37i9dQZF1DXcBWIGoYBM5M",
        "name": "Today's Top Hits",
        "owner": "spotify",
        "track_count": 50,
        "image_url": "https://..."
      }
    ],
    "source": "spotify"
  }
}
```

#### `GET /api/v1/playlists/<playlist_id>`

Get playlist details with full track list.

| Param | Type | Default | Description |
|-------|------|---------|-------------|
| `source` | string | `spotify` | Currently only `spotify` supported for detail view |

```json
{
  "data": {
    "playlist": {
      "id": "37i9dQZF1DXcBWIGoYBM5M",
      "name": "Today's Top Hits",
      "owner": "Spotify",
      "total_tracks": 50,
      "tracks": [
        {
          "id": "2cGxRwrMyEAp8dEbuZaVv6",
          "name": "Around the World",
          "artists": ["Daft Punk"],
          "album": "Homework",
          "duration_ms": 428000,
          "image_url": "https://..."
        }
      ]
    },
    "source": "spotify"
  }
}
```

#### `POST /api/v1/playlists/<playlist_id>/sync`

Trigger playlist sync/download.

```json
{
  "playlist_name": "My Playlist",
  "tracks": [
    {
      "id": "2cGxRwrMyEAp8dEbuZaVv6",
      "name": "Around the World",
      "artists": [{ "name": "Daft Punk" }]
    }
  ]
}
```

---

### Settings

#### `GET /api/v1/settings`

Get current settings. Sensitive values (passwords, tokens, secrets) are redacted.

#### `PATCH /api/v1/settings`

Update settings (partial update). Uses dot-notation keys.

```json
{
  "soulseek.search_timeout": 90,
  "logging.level": "DEBUG"
}
```

**Response:**

```json
{
  "data": {
    "message": "Settings updated.",
    "updated_keys": ["soulseek.search_timeout", "logging.level"]
  }
}
```

---

### API Key Management

#### `GET /api/v1/api-keys`

List all API keys (shows prefix and label only, never the full key).

```json
{
  "data": {
    "keys": [
      {
        "id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
        "label": "Discord Bot",
        "key_prefix": "sk_a3Bf9x2",
        "created_at": "2026-03-01T12:00:00",
        "last_used_at": "2026-03-04T09:15:00"
      }
    ]
  }
}
```

#### `POST /api/v1/api-keys`

Generate a new API key. The raw key is returned **once** — save it immediately.

```json
{
  "label": "Discord Bot"
}
```

**Response:**

```json
{
  "data": {
    "key": "sk_a3Bf9x2Kp7Qm4Rn8Yt6Wv0Xz1Cb5Dj9Fg",
    "id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
    "label": "Discord Bot",
    "key_prefix": "sk_a3Bf9x2",
    "created_at": "2026-03-04T10:00:00"
  }
}
```

#### `DELETE /api/v1/api-keys/<key_id>`

Revoke an API key by its UUID.

#### `POST /api/v1/api-keys/bootstrap`

Generate the first API key when none exist. **No authentication required.** Returns `403` if keys already exist.

```json
{
  "label": "My First Key"
}
```

---

## Field Filtering

All library, watchlist, wishlist, and discovery endpoints support the `?fields=` parameter to request only specific fields. This reduces response size when you only need a few fields.

```
GET /api/v1/library/artists/42?fields=id,name,genres,spotify_artist_id
```

```json
{
  "data": {
    "artist": {
      "id": 42,
      "name": "Radiohead",
      "genres": ["alternative rock", "art rock"],
      "spotify_artist_id": "4Z8W4fKeB5YxbusRsdQVPb"
    }
  }
}
```

---

## Examples

### Python

```python
import requests

API_URL = "http://localhost:8008/api/v1"
API_KEY = "sk_your_key_here"

headers = {"Authorization": f"Bearer {API_KEY}"}

# Get full artist details with all enrichment metadata
artist = requests.get(f"{API_URL}/library/artists/42", headers=headers).json()
print(f"Artist: {artist['data']['artist']['name']}")
print(f"Spotify: {artist['data']['artist']['spotify_artist_id']}")
print(f"MusicBrainz: {artist['data']['artist']['musicbrainz_id']}")
print(f"Albums: {len(artist['data']['albums'])}")

# Get a specific album with tracks
album = requests.get(f"{API_URL}/library/albums/87", headers=headers).json()
for track in album["data"]["tracks"]:
    print(f"  {track['track_number']}. {track['title']} ({track['duration']}ms)")

# Look up by Spotify ID
result = requests.get(f"{API_URL}/library/lookup",
    headers=headers,
    params={"type": "artist", "provider": "spotify", "id": "4Z8W4fKeB5YxbusRsdQVPb"}
).json()

# Browse genres
genres = requests.get(f"{API_URL}/library/genres", headers=headers).json()
for g in genres["data"]["genres"][:10]:
    print(f"  {g['name']}: {g['count']} artists")

# Recently added albums
recent = requests.get(f"{API_URL}/library/recently-added?type=albums&limit=10",
    headers=headers).json()

# Search external sources
search = requests.post(f"{API_URL}/search/tracks",
    headers=headers,
    json={"query": "Daft Punk", "limit": 5})

# Add to watchlist (as profile 2)
requests.post(f"{API_URL}/watchlist",
    headers={**headers, "X-Profile-Id": "2"},
    json={"artist_id": "4tZwfgrHOc3mvqYlEYSvnL", "artist_name": "Daft Punk"})

# Update watchlist filters
requests.patch(f"{API_URL}/watchlist/4tZwfgrHOc3mvqYlEYSvnL",
    headers=headers,
    json={"include_live": True, "include_remixes": True})

# Get discovery pool
pool = requests.get(f"{API_URL}/discover/pool?limit=50", headers=headers).json()

# Get only specific fields to reduce payload
minimal = requests.get(
    f"{API_URL}/library/artists?fields=id,name,thumb_url&limit=100",
    headers=headers).json()
```

### JavaScript

```javascript
const API_URL = 'http://localhost:8008/api/v1';
const API_KEY = 'sk_your_key_here';

const headers = {
  'Authorization': `Bearer ${API_KEY}`,
  'Content-Type': 'application/json'
};

// Browse library artists
const artists = await fetch(`${API_URL}/library/artists?page=1&limit=25`, { headers })
  .then(r => r.json());

// Get album with full metadata and tracks
const album = await fetch(`${API_URL}/library/albums/87`, { headers })
  .then(r => r.json());

// Look up by external ID
const lookup = await fetch(
  `${API_URL}/library/lookup?type=track&provider=spotify&id=6LgJvl0Xdtc73RJ1mN1a7Z`,
  { headers }
).then(r => r.json());

// Trigger watchlist scan
await fetch(`${API_URL}/watchlist/scan`, { method: 'POST', headers });

// Get discovery similar artists for profile 2
const similar = await fetch(`${API_URL}/discover/similar-artists?limit=20`, {
  headers: { ...headers, 'X-Profile-Id': '2' }
}).then(r => r.json());
```

### curl

```bash
# System status
curl -H "Authorization: Bearer sk_..." http://localhost:8008/api/v1/system/status

# Get artist with full metadata
curl -H "Authorization: Bearer sk_..." \
  http://localhost:8008/api/v1/library/artists/42

# Get album with tracks
curl -H "Authorization: Bearer sk_..." \
  http://localhost:8008/api/v1/library/albums/87

# Get single track
curl -H "Authorization: Bearer sk_..." \
  http://localhost:8008/api/v1/library/tracks/512

# Look up by Spotify ID
curl -H "Authorization: Bearer sk_..." \
  "http://localhost:8008/api/v1/library/lookup?type=artist&provider=spotify&id=4Z8W4fKeB5YxbusRsdQVPb"

# Browse genres
curl -H "Authorization: Bearer sk_..." \
  http://localhost:8008/api/v1/library/genres

# Recently added albums
curl -H "Authorization: Bearer sk_..." \
  "http://localhost:8008/api/v1/library/recently-added?type=albums&limit=10"

# Search external tracks
curl -X POST http://localhost:8008/api/v1/search/tracks \
  -H "Authorization: Bearer sk_..." \
  -H "Content-Type: application/json" \
  -d '{"query": "Boards of Canada", "limit": 5}'

# Watchlist with profile
curl -H "Authorization: Bearer sk_..." \
  -H "X-Profile-Id: 2" \
  http://localhost:8008/api/v1/watchlist

# Update watchlist filters
curl -X PATCH http://localhost:8008/api/v1/watchlist/4tZwfgrHOc3mvqYlEYSvnL \
  -H "Authorization: Bearer sk_..." \
  -H "Content-Type: application/json" \
  -d '{"include_live": true, "include_remixes": true}'

# Discovery pool
curl -H "Authorization: Bearer sk_..." \
  "http://localhost:8008/api/v1/discover/pool?limit=50&new_releases_only=true"

# Field filtering — only get id, name, and Spotify ID
curl -H "Authorization: Bearer sk_..." \
  "http://localhost:8008/api/v1/library/artists?fields=id,name,spotify_artist_id&limit=100"
```

---

## Endpoint Reference

| Method | Endpoint | Description |
|--------|----------|-------------|
| **System** | | |
| GET | `/system/status` | Server status and service connectivity |
| GET | `/system/activity` | Recent activity feed |
| GET | `/system/stats` | Combined library + download stats |
| **Library — Artists** | | |
| GET | `/library/artists` | List/search artists (paginated) |
| GET | `/library/artists/<id>` | Artist detail + albums |
| GET | `/library/artists/<id>/albums` | Albums for an artist |
| **Library — Albums** | | |
| GET | `/library/albums` | List/search albums (paginated) |
| GET | `/library/albums/<id>` | Album detail + tracks |
| GET | `/library/albums/<id>/tracks` | Tracks in an album |
| **Library — Tracks** | | |
| GET | `/library/tracks/<id>` | Track detail |
| GET | `/library/tracks` | Search tracks by title/artist |
| **Library — Browse** | | |
| GET | `/library/genres` | Genre listing with counts |
| GET | `/library/recently-added` | Recently added content |
| GET | `/library/lookup` | External ID lookup |
| GET | `/library/stats` | Library statistics |
| **Search** | | |
| POST | `/search/tracks` | Search external track sources |
| POST | `/search/albums` | Search external album sources |
| POST | `/search/artists` | Search external artist sources |
| **Downloads** | | |
| GET | `/downloads` | List download tasks |
| POST | `/downloads/<id>/cancel` | Cancel a download |
| POST | `/downloads/cancel-all` | Cancel all downloads |
| **Wishlist** | | |
| GET | `/wishlist` | List wishlist tracks |
| POST | `/wishlist` | Add to wishlist |
| DELETE | `/wishlist/<track_id>` | Remove from wishlist |
| POST | `/wishlist/process` | Trigger processing |
| **Watchlist** | | |
| GET | `/watchlist` | List watched artists |
| POST | `/watchlist` | Add artist to watchlist |
| PATCH | `/watchlist/<artist_id>` | Update content filters |
| DELETE | `/watchlist/<artist_id>` | Remove from watchlist |
| POST | `/watchlist/scan` | Trigger scan |
| **Discovery** | | |
| GET | `/discover/pool` | Discovery pool tracks |
| GET | `/discover/similar-artists` | Similar artists |
| GET | `/discover/recent-releases` | Recent releases |
| GET | `/discover/pool/metadata` | Pool metadata |
| **Playlists** | | |
| GET | `/playlists` | List playlists |
| GET | `/playlists/<id>` | Playlist detail + tracks |
| POST | `/playlists/<id>/sync` | Trigger playlist sync |
| **Settings** | | |
| GET | `/settings` | Get settings (redacted) |
| PATCH | `/settings` | Update settings |
| **API Keys** | | |
| GET | `/api-keys` | List API keys |
| POST | `/api-keys` | Generate new key |
| DELETE | `/api-keys/<id>` | Revoke key |
| POST | `/api-keys/bootstrap` | Bootstrap first key (no auth) |
