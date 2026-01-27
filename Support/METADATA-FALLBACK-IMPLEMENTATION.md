# Metadata Fallback Implementation Guide

This document details two alternative approaches for fetching music metadata without requiring users to provide Spotify API credentials or complete OAuth authentication.

---

## Table of Contents

1. [Overview](#overview)
2. [Current Implementation](#current-implementation)
3. [Option A: SpotiFLAC-Style Anonymous Spotify Access](#option-a-spotiflac-style-anonymous-spotify-access)
4. [Option B: iTunes Search API](#option-b-itunes-search-api)
5. [Implementation Strategy](#implementation-strategy)
6. [Comparison Matrix](#comparison-matrix)
7. [Recommended Approach](#recommended-approach)

---

## Overview

### The Problem

Currently, SoulSync requires users to:
1. Register at Spotify Developer Dashboard
2. Create an application to get `client_id` and `client_secret`
3. Enter credentials in settings
4. Complete OAuth flow to authenticate

This creates friction for users who just want search/metadata functionality without syncing playlists.

### The Goal

Implement a fallback system that allows search and metadata operations to work immediately without any user-provided credentials, while still supporting full Spotify OAuth for users who want playlist sync features.

### Priority Order

```
1. Spotify OAuth (full features - playlists, library, search, metadata)
2. Spotify Client Credentials (search, metadata - requires app credentials)
3. Anonymous Spotify Access (search, metadata - no credentials needed)
4. iTunes Search API (search, metadata - no credentials needed, different data source)
```

---

## Current Implementation

**File:** `core/spotify_client.py`

The current `SpotifyClient` class uses `SpotifyOAuth` exclusively:

```python
auth_manager = SpotifyOAuth(
    client_id=config['client_id'],
    client_secret=config['client_secret'],
    redirect_uri=config.get('redirect_uri', "http://127.0.0.1:8888/callback"),
    scope="user-library-read user-read-private playlist-read-private playlist-read-collaborative user-read-email",
    cache_path='config/.spotify_cache'
)
self.sp = spotipy.Spotify(auth_manager=auth_manager)
```

**Limitations:**
- Requires valid `client_id` and `client_secret`
- Requires user to complete OAuth flow
- All methods check `is_authenticated()` before making API calls

---

## Option A: SpotiFLAC-Style Anonymous Spotify Access

### How It Works

SpotiFLAC reverse-engineered Spotify's web player authentication to obtain anonymous access tokens without any developer credentials.

### Technical Details

#### 1. TOTP-Based Token Generation

Spotify's web player uses a Time-based One-Time Password (TOTP) mechanism for anonymous session tokens.

**Source:** `https://github.com/afkarxyz/SpotiFLAC/blob/main/backend/spotfetch.go`

```go
// Hardcoded TOTP secrets (XOR-encoded for obfuscation)
// These have been updated multiple times (v59, v60, v61) as Spotify patches them
var totpSecretV61 = []byte{...} // Current working version

// Generate TOTP code
func generateTOTP(secret []byte) string {
    // 1. XOR transform the secret with calculated byte
    // 2. Convert to hex, then base32 encode
    // 3. Generate standard TOTP code
}
```

#### 2. Token Acquisition Flow

```
Step 1: Generate TOTP Code
         |
         v
Step 2: GET https://open.spotify.com/api/token?totp={code}
         |
         v
Step 3: Response contains:
        - accessToken (for API calls)
        - clientId (Spotify's internal client ID)
        - sp_t cookie (device ID)
         |
         v
Step 4: POST https://clienttoken.spotify.com/v1/clienttoken
        Body: device info, client version
         |
         v
Step 5: Response contains:
        - granted_token.token (client token for API calls)
```

#### 3. API Endpoints Used

SpotiFLAC uses Spotify's **internal GraphQL API**, not the public REST API:

```
Base URL: https://api-partner.spotify.com/pathfinder/v2/query

Headers:
  - Authorization: Bearer {accessToken}
  - Client-Token: {clientToken}
  - User-Agent: {randomized browser UA}
```

**GraphQL Queries use persisted query hashes:**
```json
{
  "operationName": "searchTracks",
  "variables": {"searchTerm": "radiohead", "limit": 20},
  "extensions": {
    "persistedQuery": {
      "sha256Hash": "612585ae06ba435ad26369870deaae23b5c8800a256cd8a57e08eddc25a37294"
    }
  }
}
```

### Python Implementation

```python
import time
import hmac
import struct
import hashlib
import base64
import requests
from typing import Optional, Dict, Any, Tuple

class SpotifyAnonymousClient:
    """
    Anonymous Spotify client using reverse-engineered web player authentication.

    WARNING: This is unofficial and may break at any time.
    Spotify actively patches these methods.
    """

    TOKEN_URL = "https://open.spotify.com/api/token"
    CLIENT_TOKEN_URL = "https://clienttoken.spotify.com/v1/clienttoken"
    API_BASE = "https://api-partner.spotify.com/pathfinder/v2/query"

    # TOTP secret (XOR-encoded) - Version 61
    # This will need updating when Spotify patches it
    TOTP_SECRET_V61 = bytes([
        # ... byte array from SpotiFLAC source
        # Omitted here - copy from actual SpotiFLAC source
    ])

    def __init__(self):
        self.access_token: Optional[str] = None
        self.client_token: Optional[str] = None
        self.client_id: Optional[str] = None
        self.token_expiry: float = 0
        self.session = requests.Session()
        self._setup_session()

    def _setup_session(self):
        """Configure session with browser-like headers"""
        self.session.headers.update({
            'User-Agent': self._random_user_agent(),
            'Accept': 'application/json',
            'Accept-Language': 'en-US,en;q=0.9',
            'Origin': 'https://open.spotify.com',
            'Referer': 'https://open.spotify.com/',
        })

    def _random_user_agent(self) -> str:
        """Generate randomized browser User-Agent"""
        import random
        chrome_versions = ['120.0.0.0', '121.0.0.0', '122.0.0.0', '123.0.0.0']
        version = random.choice(chrome_versions)
        return f'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/{version} Safari/537.36'

    def _decode_totp_secret(self) -> bytes:
        """Decode the XOR-obfuscated TOTP secret"""
        secret = bytearray(self.TOTP_SECRET_V61)
        # XOR transformation (from SpotiFLAC)
        xor_byte = (secret[0] ^ 0x47) & 0xFF
        for i in range(len(secret)):
            secret[i] ^= xor_byte
        return bytes(secret)

    def _generate_totp(self) -> str:
        """Generate TOTP code for Spotify authentication"""
        secret = self._decode_totp_secret()

        # Convert to base32 for TOTP
        secret_hex = secret.hex()
        secret_b32 = base64.b32encode(bytes.fromhex(secret_hex)).decode()

        # Standard TOTP generation (30-second window)
        counter = int(time.time()) // 30
        counter_bytes = struct.pack('>Q', counter)

        hmac_hash = hmac.new(
            base64.b32decode(secret_b32),
            counter_bytes,
            hashlib.sha1
        ).digest()

        offset = hmac_hash[-1] & 0x0F
        code = struct.unpack('>I', hmac_hash[offset:offset + 4])[0]
        code = (code & 0x7FFFFFFF) % 1000000

        return str(code).zfill(6)

    def _fetch_access_token(self) -> bool:
        """Fetch anonymous access token from Spotify"""
        try:
            totp_code = self._generate_totp()

            response = self.session.get(
                self.TOKEN_URL,
                params={'totp': totp_code}
            )

            if response.status_code != 200:
                return False

            data = response.json()
            self.access_token = data.get('accessToken')
            self.client_id = data.get('clientId')

            # Token typically valid for 1 hour
            self.token_expiry = time.time() + 3600

            return self.access_token is not None

        except Exception as e:
            print(f"Failed to fetch access token: {e}")
            return False

    def _fetch_client_token(self) -> bool:
        """Fetch client token required for API calls"""
        if not self.access_token:
            return False

        try:
            # Get Spotify web player version from homepage
            homepage = self.session.get('https://open.spotify.com/')
            # Extract version from HTML (simplified)
            client_version = "1.2.48.255"  # Fallback version

            payload = {
                "client_data": {
                    "client_version": client_version,
                    "client_id": self.client_id,
                    "js_sdk_data": {
                        "device_brand": "unknown",
                        "device_model": "unknown",
                        "os": "windows",
                        "os_version": "NT 10.0"
                    }
                }
            }

            response = self.session.post(
                self.CLIENT_TOKEN_URL,
                json=payload,
                headers={'Authorization': f'Bearer {self.access_token}'}
            )

            if response.status_code != 200:
                return False

            data = response.json()
            self.client_token = data.get('granted_token', {}).get('token')

            return self.client_token is not None

        except Exception as e:
            print(f"Failed to fetch client token: {e}")
            return False

    def ensure_authenticated(self) -> bool:
        """Ensure we have valid tokens"""
        if self.access_token and time.time() < self.token_expiry:
            return True

        if not self._fetch_access_token():
            return False

        if not self._fetch_client_token():
            return False

        return True

    def _graphql_request(self, operation: str, variables: Dict, query_hash: str) -> Optional[Dict]:
        """Make a GraphQL request to Spotify's internal API"""
        if not self.ensure_authenticated():
            return None

        try:
            payload = {
                "operationName": operation,
                "variables": variables,
                "extensions": {
                    "persistedQuery": {
                        "version": 1,
                        "sha256Hash": query_hash
                    }
                }
            }

            response = self.session.post(
                self.API_BASE,
                json=payload,
                headers={
                    'Authorization': f'Bearer {self.access_token}',
                    'Client-Token': self.client_token,
                    'Content-Type': 'application/json'
                }
            )

            if response.status_code != 200:
                return None

            return response.json()

        except Exception as e:
            print(f"GraphQL request failed: {e}")
            return None

    # === Public API Methods ===

    def search_tracks(self, query: str, limit: int = 20) -> list:
        """Search for tracks"""
        # Query hash for searchTracks operation
        SEARCH_HASH = "612585ae06ba435ad26369870deaae23b5c8800a256cd8a57e08eddc25a37294"

        result = self._graphql_request(
            "searchTracks",
            {"searchTerm": query, "limit": limit, "offset": 0},
            SEARCH_HASH
        )

        if not result:
            return []

        # Parse and return tracks
        # Structure depends on actual GraphQL response
        tracks = []
        # ... parse result['data']['searchV2']['tracksV2']['items']
        return tracks

    def search_artists(self, query: str, limit: int = 20) -> list:
        """Search for artists"""
        SEARCH_HASH = "..."  # Different hash for artist search
        # Similar implementation
        pass

    def search_albums(self, query: str, limit: int = 20) -> list:
        """Search for albums"""
        SEARCH_HASH = "..."  # Different hash for album search
        # Similar implementation
        pass

    def get_track(self, track_id: str) -> Optional[Dict]:
        """Get track details by ID"""
        TRACK_HASH = "..."
        # Similar implementation
        pass

    def get_album(self, album_id: str) -> Optional[Dict]:
        """Get album details by ID"""
        ALBUM_HASH = "..."
        # Similar implementation
        pass

    def get_artist(self, artist_id: str) -> Optional[Dict]:
        """Get artist details by ID"""
        ARTIST_HASH = "..."
        # Similar implementation
        pass
```

### Required GraphQL Query Hashes

These hashes correspond to Spotify's internal persisted queries. They may change when Spotify updates their web player.

| Operation | Hash | Notes |
|-----------|------|-------|
| searchTracks | `612585ae...` | Search for tracks |
| searchArtists | `...` | Search for artists |
| searchAlbums | `...` | Search for albums |
| getTrack | `...` | Get track by ID |
| getAlbum | `...` | Get album by ID |
| getArtist | `...` | Get artist by ID |
| getAlbumTracks | `...` | Get album's track listing |
| getArtistDiscography | `...` | Get artist's albums |

**Note:** Extract current hashes from SpotiFLAC source or by inspecting Spotify web player network requests.

### Risks and Considerations

| Risk | Impact | Mitigation |
|------|--------|------------|
| TOTP secret changes | Auth breaks completely | Monitor SpotiFLAC updates, implement version detection |
| Query hashes change | Specific operations fail | Keep hashes configurable, monitor for changes |
| Rate limiting | 403 errors | Implement backoff, respect implicit limits |
| IP bans | Service unavailable | Unlikely for normal usage, but possible |
| Legal/TOS | Account/service issues | This violates Spotify TOS |

### Maintenance Requirements

1. **Monitor SpotiFLAC repository** for TOTP secret updates
2. **Track Spotify web player updates** that may change query hashes
3. **Implement version detection** to automatically try multiple TOTP versions
4. **Add fallback** to iTunes API when Spotify anonymous access fails

---

## Option B: iTunes Search API

### How It Works

Apple provides a free, public API for searching the iTunes/Apple Music catalog. No authentication required.

### API Documentation

**Official Docs:** https://developer.apple.com/library/archive/documentation/AudioVideo/Conceptual/iTuneSearchAPI/

### Endpoints

#### Search Endpoint

```
GET https://itunes.apple.com/search
```

**Parameters:**

| Parameter | Required | Description | Example |
|-----------|----------|-------------|---------|
| `term` | Yes | URL-encoded search text | `jack+johnson` |
| `country` | Yes | ISO 2-letter country code | `US` |
| `media` | No | Media type | `music` |
| `entity` | No | Result type | `song`, `album`, `musicArtist` |
| `limit` | No | Max results (1-200) | `25` |
| `lang` | No | Language | `en_us` |

**Entity Types for Music:**
- `musicArtist` - Artists only
- `song` - Tracks/songs only
- `album` - Albums only
- `musicTrack` - Same as song
- `mix` - Mixes
- `musicVideo` - Music videos

#### Lookup Endpoint

```
GET https://itunes.apple.com/lookup
```

**Parameters:**

| Parameter | Description | Example |
|-----------|-------------|---------|
| `id` | iTunes ID | `909253` |
| `upc` | Album UPC code | `720642462928` |
| `amgArtistId` | AMG artist ID | `468749` |
| `entity` | Include related entities | `song` (for album tracks) |

### Response Format

```json
{
  "resultCount": 1,
  "results": [
    {
      "wrapperType": "track",
      "kind": "song",
      "artistId": 909253,
      "collectionId": 1440857781,
      "trackId": 1440857786,
      "artistName": "Jack Johnson",
      "collectionName": "In Between Dreams (Bonus Track Version)",
      "trackName": "Better Together",
      "collectionCensoredName": "In Between Dreams (Bonus Track Version)",
      "trackCensoredName": "Better Together",
      "artistViewUrl": "https://music.apple.com/us/artist/jack-johnson/909253",
      "collectionViewUrl": "https://music.apple.com/us/album/better-together/1440857781?i=1440857786",
      "trackViewUrl": "https://music.apple.com/us/album/better-together/1440857781?i=1440857786",
      "previewUrl": "https://audio-ssl.itunes.apple.com/...",
      "artworkUrl30": "https://is1-ssl.mzstatic.com/.../30x30bb.jpg",
      "artworkUrl60": "https://is1-ssl.mzstatic.com/.../60x60bb.jpg",
      "artworkUrl100": "https://is1-ssl.mzstatic.com/.../100x100bb.jpg",
      "releaseDate": "2005-03-01T08:00:00Z",
      "collectionExplicitness": "notExplicit",
      "trackExplicitness": "notExplicit",
      "discCount": 1,
      "discNumber": 1,
      "trackCount": 16,
      "trackNumber": 1,
      "trackTimeMillis": 207679,
      "country": "USA",
      "currency": "USD",
      "primaryGenreName": "Rock",
      "isStreamable": true
    }
  ]
}
```

### Python Implementation

```python
import requests
from typing import Optional, List, Dict, Any
from dataclasses import dataclass
from urllib.parse import quote_plus
import time

@dataclass
class iTunesTrack:
    id: str
    name: str
    artists: List[str]
    album: str
    duration_ms: int
    track_number: int
    disc_number: int
    release_date: str
    preview_url: Optional[str] = None
    image_url: Optional[str] = None
    genre: Optional[str] = None
    explicit: bool = False
    isrc: Optional[str] = None  # iTunes doesn't provide ISRC

    @classmethod
    def from_itunes_result(cls, data: Dict[str, Any]) -> 'iTunesTrack':
        # Get highest quality artwork (replace 100x100 with larger size)
        artwork_url = data.get('artworkUrl100', '')
        if artwork_url:
            artwork_url = artwork_url.replace('100x100bb', '600x600bb')

        return cls(
            id=str(data.get('trackId', '')),
            name=data.get('trackName', ''),
            artists=[data.get('artistName', '')],
            album=data.get('collectionName', ''),
            duration_ms=data.get('trackTimeMillis', 0),
            track_number=data.get('trackNumber', 0),
            disc_number=data.get('discNumber', 1),
            release_date=data.get('releaseDate', ''),
            preview_url=data.get('previewUrl'),
            image_url=artwork_url,
            genre=data.get('primaryGenreName'),
            explicit=data.get('trackExplicitness') == 'explicit'
        )

@dataclass
class iTunesArtist:
    id: str
    name: str
    genre: Optional[str] = None
    image_url: Optional[str] = None  # iTunes artist search doesn't return images

    @classmethod
    def from_itunes_result(cls, data: Dict[str, Any]) -> 'iTunesArtist':
        return cls(
            id=str(data.get('artistId', '')),
            name=data.get('artistName', ''),
            genre=data.get('primaryGenreName')
        )

@dataclass
class iTunesAlbum:
    id: str
    name: str
    artists: List[str]
    release_date: str
    total_tracks: int
    genre: Optional[str] = None
    image_url: Optional[str] = None
    explicit: bool = False

    @classmethod
    def from_itunes_result(cls, data: Dict[str, Any]) -> 'iTunesAlbum':
        artwork_url = data.get('artworkUrl100', '')
        if artwork_url:
            artwork_url = artwork_url.replace('100x100bb', '600x600bb')

        return cls(
            id=str(data.get('collectionId', '')),
            name=data.get('collectionName', ''),
            artists=[data.get('artistName', '')],
            release_date=data.get('releaseDate', ''),
            total_tracks=data.get('trackCount', 0),
            genre=data.get('primaryGenreName'),
            image_url=artwork_url,
            explicit=data.get('collectionExplicitness') == 'explicit'
        )


class iTunesClient:
    """
    iTunes Search API client for music metadata.

    Free, no authentication required.
    Rate limit: ~20 calls/minute on /search, /lookup appears unlimited.
    """

    SEARCH_URL = "https://itunes.apple.com/search"
    LOOKUP_URL = "https://itunes.apple.com/lookup"

    # Rate limiting
    MIN_SEARCH_INTERVAL = 3.0  # 20 calls/min = 1 call per 3 seconds

    def __init__(self, country: str = "US"):
        self.country = country
        self.session = requests.Session()
        self._last_search_time = 0

    def _rate_limit_search(self):
        """Enforce rate limiting for search endpoint"""
        elapsed = time.time() - self._last_search_time
        if elapsed < self.MIN_SEARCH_INTERVAL:
            time.sleep(self.MIN_SEARCH_INTERVAL - elapsed)
        self._last_search_time = time.time()

    def _search(self, term: str, entity: str, limit: int = 25) -> List[Dict]:
        """Generic search method"""
        self._rate_limit_search()

        try:
            response = self.session.get(
                self.SEARCH_URL,
                params={
                    'term': term,
                    'country': self.country,
                    'media': 'music',
                    'entity': entity,
                    'limit': min(limit, 200)
                },
                timeout=10
            )

            if response.status_code == 403:
                # Rate limited
                time.sleep(60)  # Wait a minute
                return []

            if response.status_code != 200:
                return []

            data = response.json()
            return data.get('results', [])

        except Exception as e:
            print(f"iTunes search error: {e}")
            return []

    def _lookup(self, **params) -> List[Dict]:
        """Generic lookup method (not rate limited)"""
        try:
            params['country'] = self.country

            response = self.session.get(
                self.LOOKUP_URL,
                params=params,
                timeout=10
            )

            if response.status_code != 200:
                return []

            data = response.json()
            return data.get('results', [])

        except Exception as e:
            print(f"iTunes lookup error: {e}")
            return []

    # === Public API Methods ===

    def search_tracks(self, query: str, limit: int = 25) -> List[iTunesTrack]:
        """Search for tracks/songs"""
        results = self._search(query, 'song', limit)
        return [
            iTunesTrack.from_itunes_result(r)
            for r in results
            if r.get('wrapperType') == 'track'
        ]

    def search_artists(self, query: str, limit: int = 25) -> List[iTunesArtist]:
        """Search for artists"""
        results = self._search(query, 'musicArtist', limit)
        return [
            iTunesArtist.from_itunes_result(r)
            for r in results
            if r.get('wrapperType') == 'artist'
        ]

    def search_albums(self, query: str, limit: int = 25) -> List[iTunesAlbum]:
        """Search for albums"""
        results = self._search(query, 'album', limit)
        return [
            iTunesAlbum.from_itunes_result(r)
            for r in results
            if r.get('wrapperType') == 'collection'
        ]

    def get_track(self, track_id: str) -> Optional[iTunesTrack]:
        """Get track by iTunes ID"""
        results = self._lookup(id=track_id)
        for r in results:
            if r.get('wrapperType') == 'track':
                return iTunesTrack.from_itunes_result(r)
        return None

    def get_album(self, album_id: str) -> Optional[iTunesAlbum]:
        """Get album by iTunes ID"""
        results = self._lookup(id=album_id)
        for r in results:
            if r.get('wrapperType') == 'collection':
                return iTunesAlbum.from_itunes_result(r)
        return None

    def get_album_tracks(self, album_id: str) -> List[iTunesTrack]:
        """Get all tracks for an album"""
        results = self._lookup(id=album_id, entity='song')
        tracks = []
        for r in results:
            if r.get('wrapperType') == 'track':
                tracks.append(iTunesTrack.from_itunes_result(r))
        # Sort by disc and track number
        tracks.sort(key=lambda t: (t.disc_number, t.track_number))
        return tracks

    def get_artist(self, artist_id: str) -> Optional[iTunesArtist]:
        """Get artist by iTunes ID"""
        results = self._lookup(id=artist_id)
        for r in results:
            if r.get('wrapperType') == 'artist':
                return iTunesArtist.from_itunes_result(r)
        return None

    def get_artist_albums(self, artist_id: str, limit: int = 50) -> List[iTunesAlbum]:
        """Get all albums by an artist"""
        results = self._lookup(id=artist_id, entity='album')
        albums = []
        for r in results:
            if r.get('wrapperType') == 'collection':
                albums.append(iTunesAlbum.from_itunes_result(r))
        return albums[:limit]

    def lookup_by_upc(self, upc: str) -> Optional[iTunesAlbum]:
        """Look up album by UPC barcode"""
        results = self._lookup(upc=upc)
        for r in results:
            if r.get('wrapperType') == 'collection':
                return iTunesAlbum.from_itunes_result(r)
        return None
```

### Rate Limiting Details

| Endpoint | Rate Limit | Behavior |
|----------|------------|----------|
| `/search` | ~20 calls/minute | Returns 403 Forbidden when exceeded |
| `/lookup` | Appears unlimited | No observed throttling |

**Best Practices:**
1. Cache search results aggressively
2. Use `/lookup` for subsequent detail fetches (not rate limited)
3. Implement exponential backoff on 403 errors
4. Consider using iTunes IDs for cross-referencing after initial search

### Metadata Comparison: iTunes vs Spotify

| Field | iTunes | Spotify |
|-------|--------|---------|
| Track name | ✅ | ✅ |
| Artist name | ✅ | ✅ |
| Album name | ✅ | ✅ |
| Duration | ✅ | ✅ |
| Track/disc number | ✅ | ✅ |
| Release date | ✅ | ✅ |
| Artwork | ✅ (up to 600x600) | ✅ (up to 640x640) |
| Preview URL | ✅ | ✅ |
| Genre | ✅ (primary only) | ✅ (multiple) |
| Explicit flag | ✅ | ✅ |
| ISRC | ❌ | ✅ |
| Popularity | ❌ | ✅ |
| Audio features | ❌ | ✅ |
| Artist followers | ❌ | ✅ |
| Artist genres | ❌ | ✅ (multiple) |

---

## Implementation Strategy

### Unified Metadata Client

Create a unified client that abstracts the underlying data source:

```python
from abc import ABC, abstractmethod
from typing import Optional, List
from dataclasses import dataclass
from enum import Enum

class MetadataSource(Enum):
    SPOTIFY_OAUTH = "spotify_oauth"
    SPOTIFY_CLIENT_CREDENTIALS = "spotify_client_credentials"
    SPOTIFY_ANONYMOUS = "spotify_anonymous"
    ITUNES = "itunes"

@dataclass
class UnifiedTrack:
    """Unified track representation across all sources"""
    id: str
    source: MetadataSource
    name: str
    artists: List[str]
    album: str
    duration_ms: int
    track_number: Optional[int] = None
    disc_number: Optional[int] = None
    release_date: Optional[str] = None
    preview_url: Optional[str] = None
    image_url: Optional[str] = None
    genre: Optional[str] = None
    explicit: bool = False
    popularity: Optional[int] = None  # Spotify only
    isrc: Optional[str] = None  # Spotify only

    # Original source IDs for cross-referencing
    spotify_id: Optional[str] = None
    itunes_id: Optional[str] = None

@dataclass
class UnifiedArtist:
    """Unified artist representation"""
    id: str
    source: MetadataSource
    name: str
    genres: List[str] = None
    image_url: Optional[str] = None
    popularity: Optional[int] = None  # Spotify only
    followers: Optional[int] = None  # Spotify only

    spotify_id: Optional[str] = None
    itunes_id: Optional[str] = None

@dataclass
class UnifiedAlbum:
    """Unified album representation"""
    id: str
    source: MetadataSource
    name: str
    artists: List[str]
    release_date: Optional[str] = None
    total_tracks: Optional[int] = None
    image_url: Optional[str] = None
    genre: Optional[str] = None
    explicit: bool = False

    spotify_id: Optional[str] = None
    itunes_id: Optional[str] = None


class MetadataClient:
    """
    Unified metadata client with automatic fallback.

    Priority order:
    1. Spotify OAuth (if authenticated)
    2. Spotify Client Credentials (if credentials configured)
    3. Spotify Anonymous (if enabled)
    4. iTunes Search API (always available)
    """

    def __init__(self):
        self.spotify_client: Optional[SpotifyClient] = None
        self.spotify_anon_client: Optional[SpotifyAnonymousClient] = None
        self.itunes_client: Optional[iTunesClient] = None

        self._active_source: Optional[MetadataSource] = None
        self._initialize_clients()

    def _initialize_clients(self):
        """Initialize available clients in priority order"""
        # Try Spotify OAuth first
        try:
            from core.spotify_client import SpotifyClient
            self.spotify_client = SpotifyClient()
            if self.spotify_client.is_authenticated():
                self._active_source = MetadataSource.SPOTIFY_OAUTH
                return
        except Exception:
            pass

        # Try Spotify Anonymous
        try:
            self.spotify_anon_client = SpotifyAnonymousClient()
            if self.spotify_anon_client.ensure_authenticated():
                self._active_source = MetadataSource.SPOTIFY_ANONYMOUS
                return
        except Exception:
            pass

        # Fallback to iTunes (always available)
        self.itunes_client = iTunesClient()
        self._active_source = MetadataSource.ITUNES

    @property
    def active_source(self) -> MetadataSource:
        return self._active_source

    def search_tracks(self, query: str, limit: int = 20) -> List[UnifiedTrack]:
        """Search for tracks using best available source"""

        # Try Spotify OAuth
        if self.spotify_client and self.spotify_client.is_authenticated():
            try:
                tracks = self.spotify_client.search_tracks(query, limit)
                return [self._spotify_track_to_unified(t) for t in tracks]
            except Exception:
                pass

        # Try Spotify Anonymous
        if self.spotify_anon_client:
            try:
                tracks = self.spotify_anon_client.search_tracks(query, limit)
                if tracks:
                    return [self._spotify_anon_track_to_unified(t) for t in tracks]
            except Exception:
                pass

        # Fallback to iTunes
        if self.itunes_client:
            tracks = self.itunes_client.search_tracks(query, limit)
            return [self._itunes_track_to_unified(t) for t in tracks]

        return []

    def search_artists(self, query: str, limit: int = 20) -> List[UnifiedArtist]:
        """Search for artists using best available source"""
        # Similar implementation with fallback chain
        pass

    def search_albums(self, query: str, limit: int = 20) -> List[UnifiedAlbum]:
        """Search for albums using best available source"""
        # Similar implementation with fallback chain
        pass

    # === Conversion Methods ===

    def _spotify_track_to_unified(self, track) -> UnifiedTrack:
        """Convert Spotify Track to UnifiedTrack"""
        return UnifiedTrack(
            id=track.id,
            source=MetadataSource.SPOTIFY_OAUTH,
            name=track.name,
            artists=track.artists,
            album=track.album,
            duration_ms=track.duration_ms,
            preview_url=track.preview_url,
            image_url=track.image_url,
            popularity=track.popularity,
            spotify_id=track.id
        )

    def _itunes_track_to_unified(self, track: iTunesTrack) -> UnifiedTrack:
        """Convert iTunes Track to UnifiedTrack"""
        return UnifiedTrack(
            id=f"itunes:{track.id}",
            source=MetadataSource.ITUNES,
            name=track.name,
            artists=track.artists,
            album=track.album,
            duration_ms=track.duration_ms,
            track_number=track.track_number,
            disc_number=track.disc_number,
            release_date=track.release_date,
            preview_url=track.preview_url,
            image_url=track.image_url,
            genre=track.genre,
            explicit=track.explicit,
            itunes_id=track.id
        )
```

### Integration with Existing SpotifyClient

Modify `core/spotify_client.py` to support the fallback chain:

```python
class SpotifyClient:
    def __init__(self):
        self.sp: Optional[spotipy.Spotify] = None
        self.sp_anon: Optional[SpotifyAnonymousClient] = None
        self.itunes: Optional[iTunesClient] = None
        self.user_id: Optional[str] = None
        self._auth_mode: str = "none"  # "oauth", "client_credentials", "anonymous", "itunes", "none"
        self._setup_client()

    def _setup_client(self):
        config = config_manager.get_spotify_config()

        client_id = config.get('client_id', '')
        client_secret = config.get('client_secret', '')

        # Check if credentials are placeholder values
        has_valid_credentials = (
            client_id and
            client_secret and
            client_id != 'SpotifyClientID' and
            client_secret != 'SpotifyClientSecret'
        )

        if has_valid_credentials:
            # Try OAuth first
            try:
                auth_manager = SpotifyOAuth(
                    client_id=client_id,
                    client_secret=client_secret,
                    redirect_uri=config.get('redirect_uri', "http://127.0.0.1:8888/callback"),
                    scope="user-library-read user-read-private playlist-read-private playlist-read-collaborative user-read-email",
                    cache_path='config/.spotify_cache'
                )
                self.sp = spotipy.Spotify(auth_manager=auth_manager)

                # Check if we have a valid token
                if self._has_valid_oauth_token():
                    self._auth_mode = "oauth"
                    logger.info("Spotify client initialized with OAuth")
                    return
            except Exception as e:
                logger.warning(f"OAuth setup failed: {e}")

            # Fallback to Client Credentials (no user data, but search works)
            try:
                auth_manager = SpotifyClientCredentials(
                    client_id=client_id,
                    client_secret=client_secret
                )
                self.sp = spotipy.Spotify(auth_manager=auth_manager)
                self._auth_mode = "client_credentials"
                logger.info("Spotify client initialized with Client Credentials")
                return
            except Exception as e:
                logger.warning(f"Client Credentials setup failed: {e}")

        # No valid credentials - try anonymous access
        try:
            self.sp_anon = SpotifyAnonymousClient()
            if self.sp_anon.ensure_authenticated():
                self._auth_mode = "anonymous"
                logger.info("Spotify client initialized with anonymous access")
                return
        except Exception as e:
            logger.warning(f"Anonymous Spotify access failed: {e}")

        # Final fallback - iTunes
        try:
            self.itunes = iTunesClient()
            self._auth_mode = "itunes"
            logger.info("Using iTunes as metadata fallback")
        except Exception as e:
            logger.error(f"All metadata sources failed: {e}")
            self._auth_mode = "none"

    def _has_valid_oauth_token(self) -> bool:
        """Check if we have a valid OAuth token (not just credentials)"""
        try:
            self.sp.current_user()
            return True
        except:
            return False

    @property
    def auth_mode(self) -> str:
        """Return current authentication mode"""
        return self._auth_mode

    def is_authenticated(self) -> bool:
        """Check if any authentication method is working"""
        return self._auth_mode != "none"

    def has_user_access(self) -> bool:
        """Check if we have access to user data (playlists, library)"""
        return self._auth_mode == "oauth"

    def search_tracks(self, query: str, limit: int = 20) -> List[Track]:
        """Search tracks using available source"""
        if self._auth_mode in ("oauth", "client_credentials") and self.sp:
            # Use official Spotify API
            try:
                results = self.sp.search(q=query, type='track', limit=limit)
                return [Track.from_spotify_track(t) for t in results['tracks']['items']]
            except Exception as e:
                logger.error(f"Spotify search failed: {e}")

        if self._auth_mode == "anonymous" and self.sp_anon:
            # Use anonymous Spotify access
            try:
                return self.sp_anon.search_tracks(query, limit)
            except Exception as e:
                logger.error(f"Anonymous Spotify search failed: {e}")

        if self.itunes:
            # Fallback to iTunes
            try:
                itunes_tracks = self.itunes.search_tracks(query, limit)
                return [self._itunes_to_track(t) for t in itunes_tracks]
            except Exception as e:
                logger.error(f"iTunes search failed: {e}")

        return []

    def _itunes_to_track(self, itunes_track: iTunesTrack) -> Track:
        """Convert iTunes track to Spotify-compatible Track dataclass"""
        return Track(
            id=f"itunes:{itunes_track.id}",
            name=itunes_track.name,
            artists=itunes_track.artists,
            album=itunes_track.album,
            duration_ms=itunes_track.duration_ms,
            popularity=0,  # iTunes doesn't have popularity
            preview_url=itunes_track.preview_url,
            image_url=itunes_track.image_url
        )
```

---

## Comparison Matrix

| Feature | Spotify OAuth | Spotify Client Creds | Spotify Anonymous | iTunes |
|---------|--------------|---------------------|-------------------|--------|
| **Setup Required** | Developer account + User OAuth | Developer account | None | None |
| **User Playlists** | ✅ | ❌ | ❌ | ❌ |
| **User Library** | ✅ | ❌ | ❌ | ❌ |
| **Search** | ✅ | ✅ | ✅ | ✅ |
| **Track Metadata** | ✅ Full | ✅ Full | ✅ Full | ✅ Basic |
| **Audio Features** | ✅ | ✅ | ❌ | ❌ |
| **Artist Genres** | ✅ | ✅ | ✅ | ❌ |
| **Popularity** | ✅ | ✅ | ✅ | ❌ |
| **ISRC Codes** | ✅ | ✅ | ✅ | ❌ |
| **Stability** | Stable | Stable | Unstable | Stable |
| **TOS Compliant** | ✅ | ✅ | ❌ | ✅ |
| **Rate Limits** | 180 req/min | 180 req/min | Unknown | ~20 search/min |

---

## Recommended Approach

### For SoulSync

1. **Primary:** Keep Spotify OAuth for users who want full features (playlist sync)
2. **Secondary:** Add Spotify Client Credentials fallback for search/metadata when OAuth not completed
3. **Tertiary:** Implement iTunes fallback for when no Spotify credentials configured
4. **Optional:** Add anonymous Spotify as experimental option (with clear warnings about instability)

### Implementation Priority

```
Phase 1: Add Spotify Client Credentials fallback
         - Simple change, stable, TOS compliant
         - Search/metadata works after entering app credentials

Phase 2: Add iTunes fallback
         - Works with zero configuration
         - Good enough for basic search/matching

Phase 3 (Optional): Add anonymous Spotify
         - Experimental/advanced feature
         - Requires ongoing maintenance
         - Add toggle in settings with warning
```

### File Changes Required

1. `core/spotify_client.py` - Add fallback logic
2. `core/itunes_client.py` - New file for iTunes API
3. `config/settings.py` - Add iTunes/anonymous settings
4. `web_server.py` - Update status endpoints to show active source
5. `templates/settings.html` - UI for fallback options

---

## References

- [Spotify Web API Documentation](https://developer.spotify.com/documentation/web-api)
- [Spotipy Library](https://spotipy.readthedocs.io/)
- [iTunes Search API Documentation](https://developer.apple.com/library/archive/documentation/AudioVideo/Conceptual/iTuneSearchAPI/)
- [SpotiFLAC Source Code](https://github.com/afkarxyz/SpotiFLAC)
- [SpotiFLAC-Mobile Source Code](https://github.com/zarzet/SpotiFLAC-Mobile)
