# SoulSync API Response Shapes

Reference for the expected response shapes from `SpotifyClient` (which delegates to iTunes when Spotify is not authenticated).

Both `core/spotify_client.py` and `core/itunes_client.py` define identical dataclasses (`Track`, `Artist`, `Album`, `Playlist`). The Spotify client returns Spotify-module versions; iTunes fallback returns iTunes-module versions with the same field names and types.

---

## Dataclasses

### Track

```python
@dataclass
class Track:
    id: str
    name: str
    artists: List[str]          # Artist name strings, not dicts
    album: str                  # Album name string, not a dict
    duration_ms: int
    popularity: int
    preview_url: Optional[str] = None
    external_urls: Optional[Dict[str, str]] = None
    image_url: Optional[str] = None
```

#### Spotify construction (`Track.from_spotify_track`)

| Field | Source | Nullable/Edge Cases |
|---|---|---|
| `id` | `track_data['id']` | Never null from Spotify API |
| `name` | `track_data['name']` | Never null |
| `artists` | `[a['name'] for a in track_data['artists']]` | Can be multi-element list |
| `album` | `track_data['album']['name']` | Never null |
| `duration_ms` | `track_data['duration_ms']` | |
| `popularity` | `track_data.get('popularity', 0)` | Defaults to `0` |
| `preview_url` | `track_data.get('preview_url')` | **Can be `None`** |
| `external_urls` | `track_data.get('external_urls')` | Usually `{"spotify": "https://..."}` |
| `image_url` | `track_data['album']['images'][1]['url']` (medium) or `[0]` | **`None` if album has no images** |

#### iTunes construction (`Track.from_itunes_track`)

| Field | Source | Nullable/Edge Cases |
|---|---|---|
| `id` | `str(track_data.get('trackId', ''))` | **Can be empty string** |
| `name` | `track_data.get('trackName', '')` | **Can be empty string** |
| `artists` | `[clean_artist_name]` or `[artistName]` | Always single-element list |
| `album` | Cleaned `collectionName` (strips " - Single", " - EP") | **Can be empty string** |
| `duration_ms` | `track_data.get('trackTimeMillis', 0)` | Defaults to `0` |
| `popularity` | Always `0` | iTunes doesn't track popularity |
| `preview_url` | `track_data.get('previewUrl')` | **Can be `None`** |
| `external_urls` | `{"itunes": trackViewUrl}` | **`None` if no trackViewUrl** |
| `image_url` | `artworkUrl100` upscaled to 600x600 | **`None` if no artwork** |

---

### Artist

```python
@dataclass
class Artist:
    id: str
    name: str
    popularity: int
    genres: List[str]
    followers: int
    image_url: Optional[str] = None
    external_urls: Optional[Dict[str, str]] = None
```

#### Spotify construction (`Artist.from_spotify_artist`)

| Field | Source | Nullable/Edge Cases |
|---|---|---|
| `id` | `artist_data['id']` | |
| `name` | `artist_data['name']` | |
| `popularity` | `artist_data.get('popularity', 0)` | Defaults to `0` |
| `genres` | `artist_data.get('genres', [])` | **Can be `[]`** |
| `followers` | `artist_data.get('followers', {}).get('total', 0)` | Defaults to `0` |
| `image_url` | `artist_data['images'][0]['url']` (largest) | **`None` if no images** |
| `external_urls` | `artist_data.get('external_urls')` | Usually `{"spotify": "https://..."}` |

#### iTunes construction (`Artist.from_itunes_artist`)

| Field | Source | Nullable/Edge Cases |
|---|---|---|
| `id` | `str(artistId)` | **Can be empty string** |
| `name` | `artistName` | **Can be empty string** |
| `popularity` | Always `0` | |
| `genres` | `[primaryGenreName]` | **`[]` if no genre** |
| `followers` | Always `0` | |
| `image_url` | `artworkUrl100` upscaled | **Usually `None`** (artist search rarely returns artwork) |
| `external_urls` | `{"itunes": artistViewUrl}` | **`None` if no URL** |

---

### Album

```python
@dataclass
class Album:
    id: str
    name: str
    artists: List[str]          # Artist name strings
    release_date: str
    total_tracks: int
    album_type: str
    image_url: Optional[str] = None
    external_urls: Optional[Dict[str, str]] = None
```

#### Spotify construction (`Album.from_spotify_album`)

| Field | Source | Nullable/Edge Cases |
|---|---|---|
| `id` | `album_data['id']` | |
| `name` | `album_data['name']` | |
| `artists` | `[a['name'] for a in album_data['artists']]` | |
| `release_date` | `album_data.get('release_date', '')` | **Can be `''`**; format varies: `"YYYY"`, `"YYYY-MM"`, `"YYYY-MM-DD"` |
| `total_tracks` | `album_data.get('total_tracks', 0)` | Defaults to `0` |
| `album_type` | `album_data.get('album_type', 'album')` | `"album"`, `"single"`, `"compilation"` |
| `image_url` | `album_data['images'][0]['url']` (largest) | **`None` if no images** |
| `external_urls` | `album_data.get('external_urls')` | |

#### iTunes construction (`Album.from_itunes_album`)

| Field | Source | Nullable/Edge Cases |
|---|---|---|
| `id` | `str(collectionId)` | **Can be empty string** |
| `name` | Cleaned `collectionName` | **Can be empty string** |
| `artists` | `[artistName]` | Always single-element |
| `release_date` | `releaseDate` (full ISO 8601: `"2023-06-02T07:00:00Z"`) | **Not truncated** in dataclass (but truncated in `get_album()` dict) |
| `total_tracks` | `trackCount` | Defaults to `0` |
| `album_type` | Inferred: 1-3 tracks = `"single"`, 4-6 = `"ep"`, 7+ = `"album"`, or `"compilation"` | |
| `image_url` | `artworkUrl100` upscaled to 600x600 | **`None` if no artwork** |
| `external_urls` | `{"itunes": collectionViewUrl}` | **`None` if no URL** |

---

### Playlist

```python
@dataclass
class Playlist:
    id: str
    name: str
    description: Optional[str]   # NOTE: No default value — required positional arg
    owner: str
    public: bool
    collaborative: bool
    tracks: List[Track]
    total_tracks: int
```

**Notable differences from other dataclasses**: No `image_url` or `external_urls` fields.

- **Spotify**: `owner` = `playlist_data['owner']['display_name']`, `total_tracks` from `tracks.total` or `items.total`
- **iTunes**: `owner` = `"iTunes"`, `total_tracks` = `len(tracks)`
- `tracks` can be `[]` (e.g. from `get_user_playlists_metadata_only`)
- `description` is `Optional[str]` but has **no default** — callers must always pass it (even if `None`)

---

## Utility Methods

### `reload_config()`
- **SpotifyClient**: Calls `_setup_client()` to re-read Spotify config and re-authenticate.
- **iTunesClient**: No-op (no auth required).

### `is_authenticated() -> bool`
- **SpotifyClient**: Returns `True` if Spotify is authenticated OR iTunes fallback is available (always `True` in practice).
- **iTunesClient**: Always returns `True` (no auth required).

### `is_spotify_authenticated() -> bool`
- **SpotifyClient only**: Returns `True` only if the Spotify API is actually authenticated (calls `sp.current_user()` to verify). Returns `False` if `self.sp` is `None` or if the auth check fails.

### SpotifyClient Auth Setup

`_setup_client()` (called by `__init__` and `reload_config()`):
- Reads config via `config_manager.get_spotify_config()` — needs `client_id` and `client_secret`
- OAuth scopes: `"user-library-read user-read-private playlist-read-private playlist-read-collaborative user-read-email"`
- Cache path: `config/.spotify_cache`
- Default redirect URI: `http://127.0.0.1:8888/callback`
- On failure: `self.sp = None` (graceful degradation — all methods fall through to iTunes or return empty)
- User ID is NOT fetched at startup (lazy via `_ensure_user_id()` when needed for playlist methods)

---

## Raw Dict Methods

These methods return **raw dicts**, NOT dataclass instances.

### `get_track_features(track_id) -> Optional[Dict]`

Spotify only (no iTunes equivalent). Returns Spotify audio features:

```python
{
    "danceability": float,      # 0.0-1.0
    "energy": float,            # 0.0-1.0
    "key": int,                 # 0-11 (pitch class)
    "loudness": float,          # dB (typically -60 to 0)
    "mode": int,                # 0 = minor, 1 = major
    "speechiness": float,       # 0.0-1.0
    "acousticness": float,      # 0.0-1.0
    "instrumentalness": float,  # 0.0-1.0
    "liveness": float,          # 0.0-1.0
    "valence": float,           # 0.0-1.0 (musical positiveness)
    "tempo": float,             # BPM
    "duration_ms": int,
    "time_signature": int,      # 3, 4, 5, etc.
    "id": str,
    "uri": str,
    "track_href": str,
    "analysis_url": str,
    "type": "audio_features"
}
```

Returns `None` if not Spotify-authenticated or on error. iTunes stub always returns `None`.

---

### `get_user_info() -> Optional[Dict]`

Spotify only. Returns raw Spotify `current_user()` response:

```python
{
    "id": str,                          # Spotify user ID
    "display_name": str,
    "email": str,
    "images": [{"url": str, ...}],
    "product": str,                     # "premium", "free", etc.
    "country": str,                     # ISO 3166-1 alpha-2
    "followers": {"total": int},
    "external_urls": {"spotify": str},
    "uri": str,
    "type": "user"
}
```

Returns `None` if not Spotify-authenticated or on error. iTunes stub always returns `None`.

---

### `get_track_details(track_id) -> Optional[Dict]`

Returns an "enhanced" dict with the same shape from both sources:

```python
{
    "id": str,                        # Track ID
    "name": str,                      # Track name
    "track_number": int,              # Spotify: track_number; iTunes: trackNumber (default 0)
    "disc_number": int,               # Spotify: disc_number; iTunes: discNumber (default 1)
    "duration_ms": int,
    "explicit": bool,                 # Spotify: explicit field; iTunes: trackExplicitness == "explicit"
    "artists": List[str],             # Spotify: multiple; iTunes: single-element
    "primary_artist": Optional[str],  # First artist name; None if artists list empty (Spotify only edge case)
    "album": {
        "id": str,
        "name": str,                  # iTunes: cleaned (strips " - Single", " - EP")
        "total_tracks": int,
        "release_date": str,          # Spotify: "YYYY-MM-DD" etc; iTunes: full ISO datetime (NOT truncated)
        "album_type": str,            # Spotify: "album"/"single"/"compilation"; iTunes: ALWAYS "album"
        "artists": List[str]
    },
    "is_album_track": bool,           # total_tracks > 1
    "raw_data": Dict                  # Complete raw API response
}
```

**Fallback**: iTunes only used if `track_id` is numeric. Returns `None` if Spotify ID without Spotify auth.

**Key difference**: iTunes hardcodes `album.album_type` to `"album"` — does NOT infer from track count like the `Album` dataclass does.

**iTunes artist name handling**: The iTunes path performs a `_get_clean_artist_names()` lookup for each call to get the canonical artist name. Both `artists` and `primary_artist` use this cleaned name, not the raw `artistName` from the search result.

---

### `get_album(album_id) -> Optional[Dict]`

#### Spotify return

Raw Spotify album object (from `spotipy`). Key fields:

```python
{
    "id": str,
    "name": str,
    "artists": [{"name": str, "id": str, ...}],    # List of artist DICTS (not strings)
    "images": [{"url": str, "height": int, "width": int}, ...],  # Largest first
    "release_date": str,                             # "YYYY-MM-DD" or "YYYY"
    "total_tracks": int,
    "album_type": str,                               # "album", "single", "compilation"
    "tracks": {"items": [...], "total": int},        # Included by full album endpoint
    "external_urls": {"spotify": str},
    "uri": str,
    "type": "album"
}
```

#### iTunes return

Normalized to approximate Spotify format:

```python
{
    "id": str,                           # collectionId as string
    "name": str,                         # Cleaned collection name
    "images": [                          # 3 fabricated sizes, or [] if no artwork
        {"url": str, "height": 600, "width": 600},
        {"url": str, "height": 300, "width": 300},
        {"url": str, "height": 100, "width": 100}
    ],
    "artists": [{"name": str, "id": str}],  # Single artist dict in a list
    "release_date": str,                 # Truncated to "YYYY-MM-DD" (unlike dataclass)
    "total_tracks": int,
    "album_type": str,                   # "single"/"ep"/"album" (inferred from track count)
    "external_urls": {"itunes": str},
    "uri": "itunes:album:{collectionId}",
    "_source": "itunes",
    "_raw_data": Dict,                   # Original iTunes response
    "tracks": {                          # Only if include_tracks=True (default)
        "items": [...],                  # Normalized track dicts (see get_album_tracks)
        "total": int
    }
}
```

**Fallback**: iTunes only used if `album_id` is numeric. Returns `None` if Spotify ID without Spotify auth.

---

### `get_album_tracks(album_id) -> Optional[Dict]`

#### Spotify return

Modified paging object with ALL tracks collected across pages:

```python
{
    "items": [
        {
            "id": str,
            "name": str,
            "artists": [{"name": str, "id": str, ...}],  # List of artist DICTS
            "track_number": int,
            "disc_number": int,
            "duration_ms": int,
            "explicit": bool,
            "preview_url": str or None,
            # NOTE: NO "album" sub-object on Spotify's album_tracks endpoint
            # NOTE: NO "popularity" field
        },
        ...
    ],
    "total": int,
    "limit": int,                    # Set to len(all_tracks)
    "next": None                     # Always None (all pages collected)
}
```

#### iTunes return

```python
{
    "items": [
        {
            "id": str,                    # trackId as string
            "name": str,                  # trackName
            "artists": [{"name": str}],   # Single-element list of DICTS (Spotify-compatible)
            "album": {                    # PRESENT (unlike Spotify's album_tracks!)
                "id": str,
                "name": str,              # Cleaned
                "images": [...],          # 3-size image array
                "release_date": str       # "YYYY-MM-DD"
            },
            "duration_ms": int,
            "track_number": int,
            "disc_number": int,           # Defaults to 1
            "explicit": bool,
            "preview_url": str or None,
            "uri": "itunes:track:{trackId}",
            "external_urls": {"itunes": str},
            "_source": "itunes"
        },
        ...
    ],
    "total": int,
    "limit": int,
    "next": None
}
```

**Critical difference**: iTunes track items include an `album` sub-object. Spotify's `album_tracks` endpoint does NOT. Code that accesses `item['album']` will fail on Spotify results.

Items sorted by `(disc_number, track_number)` for iTunes.

**iTunes artist name handling**: Artist names in each track item come from `_get_clean_artist_names()` batch lookup (not raw `artistName`), falling back to `'Unknown Artist'` if lookup fails.

---

### `get_artist(artist_id) -> Optional[Dict]`

#### Spotify return

Raw Spotify artist object:

```python
{
    "id": str,
    "name": str,
    "images": [{"url": str, "height": int, "width": int}, ...],
    "genres": [str, ...],            # Can be []
    "popularity": int,               # 0-100
    "followers": {"total": int},
    "external_urls": {"spotify": str},
    "uri": str,
    "type": "artist"
}
```

#### iTunes return

```python
{
    "id": str,                       # artistId as string
    "name": str,
    "images": [                      # 3 sizes, or [] if no artwork found
        {"url": str, "height": 600, "width": 600},
        {"url": str, "height": 300, "width": 300},
        {"url": str, "height": 100, "width": 100}
    ],                               # Falls back to first album's artwork
    "genres": [str],                 # [primaryGenreName] or []
    "popularity": 0,                 # Always 0
    "followers": {"total": 0},       # Always 0
    "external_urls": {"itunes": str},
    "uri": "itunes:artist:{artistId}",
    "_source": "itunes",
    "_raw_data": Dict
}
```

---

## Dataclass Methods (return dataclass instances)

### `search_tracks(query, limit=20) -> List[Track]`
- Spotify: `GET /v1/search?type=track`
- iTunes fallback: `GET itunes.apple.com/search?entity=song`
- Returns `List[Track]` dataclass instances
- Returns `[]` on failure

### `search_albums(query, limit=20) -> List[Album]`
- Spotify: `GET /v1/search?type=album`
- iTunes fallback: `GET itunes.apple.com/search?entity=album` (fetches `limit*2`, deduplicates, prefers explicit)
- Returns `List[Album]` dataclass instances

### `search_artists(query, limit=20) -> List[Artist]`
- Spotify: `GET /v1/search?type=artist`
- iTunes fallback: `GET itunes.apple.com/search?entity=musicArtist`
- Returns `List[Artist]` dataclass instances

### `get_artist_albums(artist_id, album_type='album,single', limit=50) -> List[Album]`
- Spotify: `GET /v1/artists/{id}/albums` (paginated)
- iTunes fallback: `GET itunes.apple.com/lookup?entity=album` (deduplicates, prefers explicit)
- Returns `List[Album]` dataclass instances
- Returns `[]` if Spotify ID without Spotify auth
- iTunes `album_type` filtering: when not the default `'album,single'`, parses comma-separated types and filters. Accepts `'ep'` when `'single'` is requested (backward compatibility).

### `get_user_playlists() -> List[Playlist]`
- Spotify only (no iTunes fallback)
- Returns `List[Playlist]` with fully populated `tracks`
- Returns `[]` if not authenticated

### `get_user_playlists_metadata_only() -> List[Playlist]`
- Spotify only (no iTunes fallback)
- Returns `List[Playlist]` with `tracks = []` (empty)
- Returns `[]` if not authenticated
- Patches missing owner data: if `owner` is missing, uses `"Unknown Owner"`; if `display_name` is missing, uses `"Unknown"`. (`get_user_playlists()` does NOT do this patching.)
- Returns partial results if an error occurs mid-pagination (unlike all other methods which return `[]` on error)

### `get_saved_tracks_count() -> int`
- Spotify only (no iTunes fallback)
- Returns `0` if not authenticated
- Fetches just the first page (`limit=1`) and reads `results['total']`

### `get_saved_tracks() -> List[Track]`
- Spotify only
- Returns `[]` if not authenticated
- Skips items where `item['track']` or `item['track']['id']` is falsy

### `get_playlist_by_id(playlist_id) -> Optional[Playlist]`
- Spotify only (no iTunes fallback)
- Returns `None` if not authenticated
- Fetches playlist metadata + all tracks via `_get_playlist_tracks()`
- Returns a fully populated `Playlist` dataclass

---

## Fallback Rules

| Method | iTunes Fallback? | Condition |
|---|---|---|
| `search_tracks` | Yes | Always (tries Spotify first, falls through on failure) |
| `search_albums` | Yes | Always |
| `search_artists` | Yes | Always |
| `get_track_details` | Yes | Only if `track_id` is numeric |
| `get_album` | Yes | Only if `album_id` is numeric |
| `get_album_tracks` | Yes | Only if `album_id` is numeric |
| `get_artist` | Yes | Only if `artist_id` is numeric |
| `get_artist_albums` | Yes | Only if `artist_id` is numeric |
| `get_user_playlists` | No | Returns `[]` |
| `get_user_playlists_metadata_only` | No | Returns `[]` |
| `get_playlist_by_id` | No | Returns `None` |
| `get_saved_tracks` | No | Returns `[]` |
| `get_saved_tracks_count` | No | Returns `0` |
| `get_track_features` | No | Returns `None` |
| `get_user_info` | No | Returns `None` |

**ID format detection**: Spotify IDs are alphanumeric (base62, contain letters). iTunes IDs are purely numeric. `_is_itunes_id()` checks `id_str.isdigit()`.

---

## Return Type Inconsistencies

| Method | Returns |
|---|---|
| `search_tracks/albums/artists` | **Dataclass** instances (`Track`, `Album`, `Artist`) |
| `get_track_details` | **Dict** (enhanced, same shape both sources) |
| `get_album` | **Dict** (Spotify raw / iTunes normalized) |
| `get_album_tracks` | **Dict** with `items` list |
| `get_artist` | **Dict** (Spotify raw / iTunes normalized) |
| `get_artist_albums` | **Dataclass** instances (`List[Album]`) |

Consumers must handle both:
- Dataclass attribute access: `track.name`, `track.artists`
- Dict key access: `track_details['name']`, `track_details['album']['name']`

---

## Rate Limiting

### Spotify
- `MIN_API_INTERVAL = 0.2` (200ms between calls)
- `rate_limited` decorator enforces interval via global lock
- On HTTP 429 (rate limit): 3s backoff, then re-raises
- On HTTP 502/503 (service error): 2s backoff, then re-raises

### iTunes
- `MIN_API_INTERVAL = 3.0` (3s between calls, ~20 calls/minute limit)
- `_search()` IS rate-limited (uses `@rate_limited` decorator)
- `_lookup()` is **NOT rate-limited** (appears unlimited per Apple docs)
- On HTTP 403 (rate limit): 60s backoff, then re-raises

---

## iTunes-Specific Internals

### iTunes API Base URLs

```
SEARCH_URL = "https://itunes.apple.com/search"
LOOKUP_URL = "https://itunes.apple.com/lookup"
```

### iTunes Constructor

```python
iTunesClient(country: str = "US")
```

- `country` is passed to every `_search()` and `_lookup()` call — determines which regional iTunes catalog is queried
- Creates a `requests.Session` with `User-Agent: SoulSync/1.0` header
- No authentication required

### `_clean_itunes_album_name(album_name)`
Strips only two suffixes:
- `" - Single"` → removed
- `" - EP"` → removed

No other transformations. Applied in: `Track.from_itunes_track`, `Album.from_itunes_album`, `get_track_details`, `get_album`, `get_album_tracks`.

### iTunes `_search()` Behavior
- Always passes `'explicit': 'Yes'` in query params — includes explicit content in results (prefers over clean)
- Caps `limit` at 200 (iTunes API max): `min(limit, 200)`
- Returns `[]` on HTTP 403 (after a 60s sleep) or any other non-200 status

### iTunes `_lookup()` Behavior
- NOT rate-limited (no decorator)
- Accepts keyword args that become query params (e.g. `id=`, `entity=`, `limit=`)
- Returns `[]` on any error

### Double Rate Limiting on iTunes Search Methods
**Gotcha**: `search_tracks()`, `search_albums()`, and `search_artists()` on iTunesClient each have their own `@rate_limited` decorator AND they call `self._search()` which also has `@rate_limited`. This means each call sleeps **twice** — minimum 6s per search call instead of the expected 3s. `get_track_details()` and `get_album_tracks()` avoid this because they use `_lookup()` (not rate-limited) instead of `_search()`.

### `_get_clean_artist_names(artist_ids)`
Batch lookup of artist IDs via `_lookup()` (not rate-limited) to get canonical artist names. iTunes search results sometimes append featured artists to the artist name field (e.g. `"Drake & 21 Savage"`), but the lookup endpoint returns the canonical name. Used by `search_tracks()` and `get_album_tracks()`.

- Batches up to 50 IDs per lookup call (comma-separated)
- Returns `{artist_id: clean_name}` dict

### `get_artist_albums()` Deduplication
Heavy dedup logic: normalizes album names (strips edition suffixes, brackets), then deduplicates. Prefers explicit versions over clean — but validates explicit albums by actually looking up their tracks via `_lookup()` to confirm they have tracks (some iTunes explicit albums are broken and report 0 tracks).

### iTunes Stub Methods
These methods exist for API parity with SpotifyClient but always return empty/None:

| Method | Returns | Reason |
|---|---|---|
| `get_user_playlists()` | `[]` | iTunes has no user playlists API |
| `get_user_playlists_metadata_only()` | `[]` | Same |
| `get_playlist_by_id()` | `None` | Same |
| `get_saved_tracks_count()` | `0` | iTunes has no saved tracks concept |
| `get_saved_tracks()` | `[]` | Same |
| `get_user_info()` | `None` | iTunes has no authentication |
| `get_track_features()` | `None` | iTunes has no audio features API |
| `reload_config()` | `None` | No-op (no auth to reload) |

---

## Implementing a New Metadata Source

### Required Interface

A new client must implement all of these public methods. Use iTunesClient as the reference — it shows both the real implementations and the stubs for unsupported features.

```
# Constructor
__init__(self, ...)
is_authenticated(self) -> bool
reload_config(self)

# Search (return dataclass instances)
search_tracks(query, limit=20) -> List[Track]
search_albums(query, limit=20) -> List[Album]
search_artists(query, limit=20) -> List[Artist]

# Detail lookups (return raw dicts — see shapes above)
get_track_details(track_id) -> Optional[Dict]
get_album(album_id) -> Optional[Dict]       # See note on include_tracks
get_album_tracks(album_id) -> Optional[Dict]
get_artist(artist_id) -> Optional[Dict]
get_track_features(track_id) -> Optional[Dict]
get_user_info() -> Optional[Dict]

# Collection methods (return dataclass instances)
get_artist_albums(artist_id, album_type='album,single', limit=50) -> List[Album]
get_user_playlists() -> List[Playlist]
get_user_playlists_metadata_only() -> List[Playlist]
get_playlist_by_id(playlist_id) -> Optional[Playlist]
get_saved_tracks() -> List[Track]
get_saved_tracks_count() -> int
```

### Classmethod Signatures for Dataclasses

If you add `from_yoursource_*` classmethods to the dataclasses:

```python
Track.from_yoursource_track(cls, track_data: Dict, clean_artist_name: Optional[str] = None) -> Track
Artist.from_yoursource_artist(cls, artist_data: Dict) -> Artist
Album.from_yoursource_album(cls, album_data: Dict) -> Album
Playlist.from_yoursource_playlist(cls, playlist_data: Dict, tracks: List[Track]) -> Playlist
#                                                          ^^^^^^^^^^^^^^^^^^^^
#                       NOTE: Playlist takes tracks as a SEPARATE arg (not inside the dict)
```

`Track.from_itunes_track` accepts an optional `clean_artist_name` parameter — when provided, it overrides `artistName` from the raw data. This exists because iTunes search results sometimes append featured artists to the name. If your source has clean artist names, you don't need this parameter.

### Method Signature Differences to Watch

| Detail | SpotifyClient | iTunesClient |
|---|---|---|
| `get_album()` signature | `get_album(album_id)` | `get_album(album_id, include_tracks=True)` |
| `get_album()` when called via SpotifyClient fallback | — | Always called without `include_tracks` (defaults to `True`) |

SpotifyClient always returns tracks as part of the album object from the Spotify API. iTunesClient has an `include_tracks` param because it requires a separate lookup call. When SpotifyClient delegates to iTunes, it calls `self._itunes.get_album(album_id)` without passing `include_tracks`, so the default `True` applies.

### Delegation Pattern

`SpotifyClient` has a lazy `_itunes` property that instantiates `iTunesClient()` on first access. Fallback delegation works like this:

- **Search methods** (`search_tracks`, `search_albums`, `search_artists`): Try Spotify first, fall through to iTunes on **any exception** (even if Spotify is authenticated).
- **ID-based methods** (`get_track_details`, `get_album`, `get_album_tracks`, `get_artist`, `get_artist_albums`): Try Spotify first, then fall through to iTunes **only if the ID is numeric** (`id_str.isdigit()`). If the ID looks like a Spotify ID (alphanumeric) but Spotify auth failed, returns `None`/`[]` — does NOT try iTunes.
- **User-specific methods** (`get_user_playlists`, `get_saved_tracks`, etc.): Spotify only, no fallback.

### Error Handling Convention

- Methods returning `List[...]` return `[]` on error
- Methods returning `Optional[...]` return `None` on error
- `get_saved_tracks_count()` returns `0` on error
- Exception: `get_user_playlists_metadata_only()` returns **partial results** if an error occurs mid-pagination (unique to this method)

### Result Filtering

iTunes API returns mixed result types. All iTunes methods filter by `wrapperType`:
- Track methods: `wrapperType == 'track'` and `kind == 'song'`
- Album methods: `wrapperType == 'collection'`
- Artist methods: `wrapperType == 'artist'`

If your source returns clean typed results, you won't need this filtering.

### Playlist Track Item Quirk

The internal `_get_playlist_tracks()` in SpotifyClient handles a Spotify API change (Feb 2026) where playlist items may use either `item['track']` or `item['item']` as the key for the track object:
```python
track_data = item.get('track') or item.get('item')
```
Items where the track data is falsy (e.g., local files) are silently skipped.

### Artist Name Defaults

| Source | Default when artist name is missing |
|---|---|
| iTunes `Track.from_itunes_track` | `'Unknown Artist'` (via `track_data.get('artistName', 'Unknown Artist')`) |
| iTunes `get_track_details` | `'Unknown Artist'` |
| iTunes `get_album_tracks` | `'Unknown Artist'` |
| Spotify | No default — relies on Spotify always providing artist names |

### iTunes Album Type Inconsistency

The `Album.from_itunes_album` dataclass constructor infers album type from track count AND checks `collectionType` for compilation. But `get_album()` (raw dict method) only infers from track count — it does **not** check `collectionType`. So the same iTunes album can produce different `album_type` values depending on which code path created it.
