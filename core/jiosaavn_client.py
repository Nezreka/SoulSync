"""JioSaavn metadata client backed by the saavn.sumit.co REST API.

Wraps the unofficial JioSaavn API documented at https://saavn.sumit.co/docs
and normalises responses into the same Track / Artist / Album dataclass shape
used by DeezerClient, iTunesClient, and MusicBrainzSearchClient.

Endpoints used:
    GET /api/search
    GET /api/search/songs
    GET /api/search/albums
    GET /api/search/artists
    GET /api/songs/{id}
    GET /api/albums
    GET /api/artists/{id}
    GET /api/artists/{id}/albums

Config keys (all optional):
    jiosaavn.base_url   API base URL (default: https://saavn.sumit.co)

Discography / singles
---------------------
The upstream API does not expose a Spotify-style singles release type. Every
release in ``/api/artists/{id}/albums`` is labelled ``type: album`` even when
it is a one-track single. Many standalone singles only appear under
``/api/artists/{id}/songs`` (individual tracks), not in the albums feed.

SoulSync does not synthesize a singles section from the songs feed. Artist
discography for JioSaavn therefore surfaces **albums only** — the Singles tab
on artist detail will be empty. Individual songs are still searchable via
``/api/search/songs`` and openable as tracks; this limitation applies only to
the artist discography view.

Artist discography fetch
------------------------
``get_artist_albums`` loads releases via ``/api/search/albums?query=<name>``
(up to ``limit``, max 200) instead of paginating ``/api/artists/{id}/albums``,
which returns only **10 releases per page** no matter how high ``limit`` is set.
Search hits are text-matched albums (soundtracks and compilations may appear)
and the upstream API often returns fewer rows than the requested ``limit`` in
one response. When the artist name cannot be resolved, the client falls back to
the first page of ``/api/artists/{id}/albums``.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

import requests

from config.settings import config_manager
from core.api_call_tracker import api_call_tracker
from core.metadata.cache import get_metadata_cache
from utils.logging_config import get_logger

logger = get_logger("jiosaavn_client")

DEFAULT_BASE_URL = "https://saavn.sumit.co"
MIN_API_INTERVAL = 1.0  # 1 second between API calls (same cap as MusicBrainz)

_last_api_call_time = 0.0
_api_call_lock = threading.Lock()

_IMAGE_QUALITY_ORDER = ("500x500", "150x150", "50x50")


def _rate_limit() -> None:
    """Enforce at most one JioSaavn API call per second."""
    global _last_api_call_time
    with _api_call_lock:
        current_time = time.time()
        time_since_last_call = current_time - _last_api_call_time
        if time_since_last_call < MIN_API_INTERVAL:
            time.sleep(MIN_API_INTERVAL - time_since_last_call)
        _last_api_call_time = time.time()
    api_call_tracker.record_call("jiosaavn")


def _best_image(images: Optional[List[Dict[str, Any]]]) -> Optional[str]:
    if not images:
        return None
    by_quality = {
        str(item.get("quality") or ""): str(item.get("url") or "")
        for item in images
        if isinstance(item, dict) and item.get("url")
    }
    for quality in _IMAGE_QUALITY_ORDER:
        url = by_quality.get(quality)
        if url:
            return url
    return next(iter(by_quality.values()), None)


def _artist_names(artists_block: Optional[Dict[str, Any]]) -> List[str]:
    if not isinstance(artists_block, dict):
        return []
    names: List[str] = []
    seen: set[str] = set()
    for bucket in ("primary", "featured"):
        for artist in artists_block.get(bucket) or []:
            if not isinstance(artist, dict):
                continue
            name = (artist.get("name") or "").strip()
            key = name.lower()
            if name and key not in seen:
                seen.add(key)
                names.append(name)
    return names


def _release_date(year: Any, release_date: Any = None) -> str:
    if release_date:
        return str(release_date)
    if year in (None, ""):
        return ""
    return str(year)


def _duration_ms(seconds: Any) -> int:
    try:
        return int(float(seconds or 0) * 1000)
    except (TypeError, ValueError):
        return 0


def _popularity(play_count: Any) -> int:
    try:
        return int(play_count or 0)
    except (TypeError, ValueError):
        return 0


@dataclass
class Track:
    id: str
    name: str
    artists: List[str]
    album: str
    duration_ms: int
    popularity: int
    preview_url: Optional[str] = None
    external_urls: Optional[Dict[str, str]] = None
    image_url: Optional[str] = None
    release_date: Optional[str] = None
    track_number: Optional[int] = None
    disc_number: Optional[int] = None
    album_type: Optional[str] = None
    total_tracks: Optional[int] = None
    album_id: Optional[str] = None

    @classmethod
    def from_api(cls, data: Dict[str, Any]) -> "Track":
        album = data.get("album") if isinstance(data.get("album"), dict) else {}
        album_id = str(album.get("id") or "") or None
        album_name = str(album.get("name") or "")
        artists = _artist_names(data.get("artists"))
        if not artists:
            artists = ["Unknown Artist"]
        url = str(data.get("url") or "")
        external_urls = {"jiosaavn": url} if url else {}
        return cls(
            id=str(data.get("id") or ""),
            name=str(data.get("name") or data.get("title") or ""),
            artists=artists,
            album=album_name,
            duration_ms=_duration_ms(data.get("duration")),
            popularity=_popularity(data.get("playCount")),
            external_urls=external_urls,
            image_url=_best_image(data.get("image")),
            release_date=_release_date(data.get("year"), data.get("releaseDate")) or None,
            album_type="album",
            album_id=album_id,
        )


@dataclass
class Artist:
    id: str
    name: str
    popularity: int
    genres: List[str]
    followers: int
    image_url: Optional[str] = None
    external_urls: Optional[Dict[str, str]] = None

    @classmethod
    def from_api(cls, data: Dict[str, Any]) -> "Artist":
        name = str(data.get("name") or data.get("title") or "").strip()
        url = str(data.get("url") or "")
        external_urls = {"jiosaavn": url} if url else {}
        return cls(
            id=str(data.get("id") or ""),
            name=name,
            popularity=0,
            genres=[],
            followers=0,
            image_url=_best_image(data.get("image")),
            external_urls=external_urls,
        )


@dataclass
class Album:
    id: str
    name: str
    artists: List[str]
    release_date: str
    total_tracks: int
    album_type: str
    image_url: Optional[str] = None
    external_urls: Optional[Dict[str, str]] = None
    format: Optional[str] = None
    country: Optional[str] = None
    status: Optional[str] = None
    label: Optional[str] = None
    disambiguation: Optional[str] = None
    release_group_id: Optional[str] = None

    @classmethod
    def from_api(cls, data: Dict[str, Any]) -> "Album":
        artists = _artist_names(data.get("artists"))
        if not artists:
            artist = str(data.get("artist") or "").strip()
            if artist:
                artists = [artist]
            else:
                artists = ["Unknown Artist"]
        url = str(data.get("url") or "")
        external_urls = {"jiosaavn": url} if url else {}
        song_count = data.get("songCount")
        if song_count is None:
            song_ids = data.get("songIds")
            if isinstance(song_ids, str) and song_ids.strip():
                song_count = len([part for part in song_ids.split(",") if part.strip()])
            else:
                song_count = 0
        return cls(
            id=str(data.get("id") or ""),
            name=str(data.get("name") or data.get("title") or ""),
            artists=artists,
            release_date=_release_date(data.get("year")),
            total_tracks=int(song_count or 0),
            album_type=str(data.get("type") or "album"),
            image_url=_best_image(data.get("image")),
            external_urls=external_urls,
            label=str(data.get("label") or "") or None,
        )


class JioSaavnClient:
    """REST client for the unofficial JioSaavn metadata API."""

    def __init__(
        self,
        base_url: Optional[str] = None,
        timeout: int = 20,
        session: Optional[Any] = None,
    ) -> None:
        self.base_url = (base_url or config_manager.get("jiosaavn.base_url", DEFAULT_BASE_URL)).rstrip("/")
        self.timeout = timeout
        self.session: Any = session or requests.Session()
        if isinstance(self.session, requests.Session):
            self.session.headers.update({
                "Accept": "application/json",
                "User-Agent": "SoulSync/1.0",
            })

    def reload_config(self) -> None:
        self.base_url = config_manager.get("jiosaavn.base_url", DEFAULT_BASE_URL).rstrip("/")

    def is_authenticated(self) -> bool:
        """JioSaavn proxy requires no credentials."""
        return True

    def _get_json(self, path: str, params: Optional[Dict[str, Any]] = None) -> Any:
        _rate_limit()
        url = f"{self.base_url}{path}"
        try:
            response = self.session.get(url, params=params or {}, timeout=self.timeout)
            response.raise_for_status()
            payload = response.json()
        except Exception as exc:
            if "rate limit" in str(exc).lower() or "429" in str(exc) or "503" in str(exc):
                logger.warning("JioSaavn rate limit hit, implementing backoff: %s", exc)
                time.sleep(2.0)
            raise
        if isinstance(payload, dict) and payload.get("success") is False:
            raise RuntimeError(payload.get("message") or "JioSaavn API request failed")
        return payload

    @staticmethod
    def _unwrap_results(payload: Any) -> List[Dict[str, Any]]:
        if not isinstance(payload, dict):
            return []
        data = payload.get("data")
        if isinstance(data, list):
            return [item for item in data if isinstance(item, dict)]
        if isinstance(data, dict):
            results = data.get("results")
            if isinstance(results, list):
                return [item for item in results if isinstance(item, dict)]
        return []

    def _search(
        self,
        search_type: str,
        endpoint: str,
        query: str,
        limit: int,
        *,
        dataclass_from_api,
    ) -> List[Any]:
        query = (query or "").strip()
        if not query:
            return []

        cache = get_metadata_cache()
        cached_results = cache.get_search_results("jiosaavn", search_type, query, limit)
        if cached_results is not None:
            parsed = []
            for raw in cached_results:
                try:
                    parsed.append(dataclass_from_api(raw))
                except Exception as exc:
                    logger.debug("JioSaavn cache parse failed for %s: %s", search_type, exc)
            if parsed:
                return parsed

        payload = self._get_json(
            endpoint,
            {"query": query, "page": 0, "limit": min(limit, 200)},
        )
        raw_items = self._unwrap_results(payload)[:limit]
        parsed_items = [dataclass_from_api(item) for item in raw_items if item.get("id")]

        entries = [(str(item.get("id")), item) for item in raw_items if item.get("id")]
        if entries:
            # Search hits are summary stubs (songIds, no track list). Don't
            # overwrite a fuller album entity already cached by get_album().
            cache.store_entities_bulk(
                "jiosaavn",
                search_type,
                entries,
                skip_if_exists=(search_type == "album"),
            )
            cache.store_search_results(
                "jiosaavn",
                search_type,
                query,
                limit,
                [entity_id for entity_id, _ in entries],
            )
        return parsed_items

    def search_tracks(self, query: str, limit: int = 20) -> List[Track]:
        return self._search("track", "/api/search/songs", query, limit, dataclass_from_api=Track.from_api)

    def search_artists(self, query: str, limit: int = 20) -> List[Artist]:
        return self._search("artist", "/api/search/artists", query, limit, dataclass_from_api=Artist.from_api)

    def search_albums(self, query: str, limit: int = 20) -> List[Album]:
        return self._search("album", "/api/search/albums", query, limit, dataclass_from_api=Album.from_api)

    def get_track_details(self, track_id: str) -> Optional[Dict[str, Any]]:
        track_id = str(track_id or "").strip()
        if not track_id:
            return None

        cache = get_metadata_cache()
        cached = cache.get_entity("jiosaavn", "track", track_id)
        if cached:
            track = Track.from_api(cached)
            return self._track_to_enhanced_dict(track, cached)

        payload = self._get_json(f"/api/songs/{track_id}")
        items = self._unwrap_results(payload)
        if not items:
            return None
        raw = items[0]
        cache.store_entity("jiosaavn", "track", track_id, raw)
        track = Track.from_api(raw)
        return self._track_to_enhanced_dict(track, raw)

    @staticmethod
    def _names_to_artist_dicts(names: List[str]) -> List[Dict[str, str]]:
        return [{"name": name} for name in names if name]

    @staticmethod
    def _image_url_to_images(image_url: Optional[str]) -> List[Dict[str, str]]:
        if not image_url:
            return []
        return [{"url": image_url}]

    def get_artist(self, artist_id: str) -> Optional[Dict[str, Any]]:
        artist_id = str(artist_id or "").strip()
        if not artist_id:
            return None

        cache = get_metadata_cache()
        cached = cache.get_entity("jiosaavn", "artist", artist_id)
        if cached:
            artist = Artist.from_api(cached)
            return self._artist_to_enhanced_dict(artist, cached)

        payload = self._get_json(f"/api/artists/{artist_id}")
        data = payload.get("data") if isinstance(payload, dict) else None
        if not isinstance(data, dict):
            return None
        cache.store_entity("jiosaavn", "artist", artist_id, data)
        artist = Artist.from_api(data)
        return self._artist_to_enhanced_dict(artist, data)

    def get_artist_info(self, artist_id: str) -> Optional[Dict[str, Any]]:
        """Deezer-compatible alias used by shared metadata helpers."""
        return self.get_artist(artist_id)

    def get_artist_albums(
        self,
        artist_id: str,
        album_type: str = "album,single",
        limit: int = 50,
        **kwargs,
    ) -> List[Album]:
        """Return album releases for an artist via album search by name.

        Uses ``/api/search/albums`` (see module docstring) because the artist
        albums feed is capped at 10 per page. ``album_type`` is ignored.
        """
        artist_id = str(artist_id or "").strip()
        if not artist_id:
            return []

        limit = max(1, min(int(limit or 50), 200))
        artist_name = (kwargs.get("artist_name") or "").strip()
        if not artist_name:
            artist_info = self.get_artist(artist_id)
            if artist_info:
                artist_name = (artist_info.get("name") or "").strip()

        if artist_name:
            return self.search_albums(artist_name, limit=limit)

        return self._get_artist_albums_page(artist_id, limit=limit)

    def _get_artist_albums_page(self, artist_id: str, *, limit: int = 50, page: int = 0) -> List[Album]:
        """First page of ``/api/artists/{id}/albums`` — fallback when name is unknown."""
        payload = self._get_json(
            f"/api/artists/{artist_id}/albums",
            {"page": page, "limit": limit},
        )
        data = payload.get("data") if isinstance(payload, dict) else None
        if not isinstance(data, dict):
            return []

        raw_albums = data.get("albums") or []
        if not isinstance(raw_albums, list):
            return []

        cache = get_metadata_cache()
        albums: List[Album] = []
        entries = []
        for raw in raw_albums:
            if not isinstance(raw, dict) or not raw.get("id"):
                continue
            album_id = str(raw["id"])
            entries.append((album_id, raw))
            albums.append(Album.from_api(raw))
        if entries:
            cache.store_entities_bulk(
                "jiosaavn",
                "album",
                entries,
                skip_if_exists=True,
            )
        return albums

    def get_album_tracks(self, album_id: str) -> Optional[Dict[str, Any]]:
        album_data = self.get_album(album_id)
        if not album_data:
            return None
        tracks = album_data.get("tracks") or []
        if not isinstance(tracks, list):
            tracks = []
        return {"items": tracks, "total": len(tracks)}

    @staticmethod
    def _artist_to_enhanced_dict(artist: Artist, raw: Dict[str, Any]) -> Dict[str, Any]:
        follower_count = raw.get("followerCount")
        try:
            followers_total = int(follower_count or 0)
        except (TypeError, ValueError):
            followers_total = 0
        return {
            "id": artist.id,
            "name": artist.name,
            "image_url": artist.image_url,
            "images": JioSaavnClient._image_url_to_images(artist.image_url),
            "genres": [],
            "followers": {"total": followers_total},
            "external_urls": artist.external_urls or {},
        }

    @staticmethod
    def _album_raw_has_songs(raw: Dict[str, Any]) -> bool:
        """True when cached/API album payload includes an actual track list."""
        songs = raw.get("songs")
        return isinstance(songs, list) and bool(songs)

    def get_album(self, album_id: str) -> Optional[Dict[str, Any]]:
        album_id = str(album_id or "").strip()
        if not album_id:
            return None

        cache = get_metadata_cache()
        cached = cache.get_entity("jiosaavn", "album", album_id)
        if cached and self._album_raw_has_songs(cached):
            album = Album.from_api(cached)
            return self._album_to_enhanced_dict(album, cached)

        payload = self._get_json("/api/albums", {"id": album_id})
        data = payload.get("data") if isinstance(payload, dict) else None
        if not isinstance(data, dict):
            return None
        cache.store_entity("jiosaavn", "album", album_id, data)
        album = Album.from_api(data)
        return self._album_to_enhanced_dict(album, data)

    @staticmethod
    def _track_to_enhanced_dict(track: Track, raw: Dict[str, Any]) -> Dict[str, Any]:
        album_block = raw.get("album") if isinstance(raw.get("album"), dict) else {}
        album_id = track.album_id or album_block.get("id")
        return {
            "id": track.id,
            "name": track.name,
            "artists": JioSaavnClient._names_to_artist_dicts(track.artists),
            "album": {
                "id": album_id,
                "name": track.album,
                "images": JioSaavnClient._image_url_to_images(track.image_url),
                "release_date": track.release_date,
            },
            "album_id": album_id,
            "duration_ms": track.duration_ms,
            "popularity": track.popularity,
            "image_url": track.image_url,
            "release_date": track.release_date,
            "external_urls": track.external_urls or {},
            "language": raw.get("language"),
            "label": raw.get("label"),
            "has_lyrics": raw.get("hasLyrics"),
        }

    @staticmethod
    def _album_to_enhanced_dict(album: Album, raw: Dict[str, Any]) -> Dict[str, Any]:
        songs = raw.get("songs") or []
        tracks = []
        if isinstance(songs, list):
            for idx, song in enumerate(songs, start=1):
                if not isinstance(song, dict):
                    continue
                track = Track.from_api(song)
                tracks.append({
                    "id": track.id,
                    "name": track.name,
                    "artists": JioSaavnClient._names_to_artist_dicts(track.artists),
                    "duration_ms": track.duration_ms,
                    "track_number": idx,
                    "disc_number": 1,
                })
        return {
            "id": album.id,
            "name": album.name,
            "artists": JioSaavnClient._names_to_artist_dicts(album.artists),
            "release_date": album.release_date,
            "total_tracks": album.total_tracks or len(tracks),
            "album_type": album.album_type,
            "image_url": album.image_url,
            "images": JioSaavnClient._image_url_to_images(album.image_url),
            "external_urls": album.external_urls or {},
            "language": raw.get("language"),
            "tracks": tracks,
        }
