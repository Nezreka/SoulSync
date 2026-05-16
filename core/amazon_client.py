"""Amazon Music metadata client backed by a T2Tunes proxy instance.

T2Tunes exposes the Amazon Music catalog (search, album metadata, stream info)
through a simple REST API. This module wraps those endpoints and normalises the
responses into the same Track / Artist / Album dataclass shape used by
DeezerClient and iTunesClient.

Endpoints used:
    GET /api/status
    GET /api/amazon-music/search
    GET /api/amazon-music/metadata
    GET /api/amazon-music/media-from-asin

Config keys (all optional — fall back to public defaults):
    amazon.base_url        T2Tunes instance URL  (default: https://t2tunes.site)
    amazon.country         ISO-3166 country code (default: US)
    amazon.preferred_codec Preferred audio codec (default: flac)
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Any, Dict, Iterator, List, Optional
from urllib.parse import urljoin

import requests

from config.settings import config_manager
from core.api_call_tracker import api_call_tracker
from utils.logging_config import get_logger

logger = get_logger("amazon_client")

DEFAULT_BASE_URL = "https://t2tunes.site"
DEFAULT_COUNTRY = "US"
DEFAULT_CODEC = "flac"
MIN_API_INTERVAL = 0.5  # seconds — T2Tunes has no published rate limit

_last_api_call: float = 0.0
_api_call_lock = threading.Lock()


class AmazonClientError(RuntimeError):
    """Raised on unrecoverable T2Tunes API errors."""


# ---------------------------------------------------------------------------
# Dataclasses — field layout matches DeezerClient / iTunesClient exactly
# ---------------------------------------------------------------------------

@dataclass
class Track:
    id: str           # Amazon track ASIN
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
    isrc: Optional[str] = None

    @classmethod
    def from_search_hit(cls, doc: Dict[str, Any]) -> "Track":
        return cls(
            id=str(doc.get("asin") or ""),
            name=str(doc.get("title") or ""),
            artists=[str(doc.get("artistName") or "Unknown Artist")],
            album=str(doc.get("albumName") or ""),
            duration_ms=int(doc.get("duration") or 0) * 1000,
            popularity=0,
            isrc=str(doc.get("isrc") or "") or None,
        )

    @classmethod
    def from_stream_info(
        cls,
        stream: "T2TunesStreamInfo",
        album_meta: Optional[Dict[str, Any]] = None,
    ) -> "Track":
        album = album_meta or {}
        return cls(
            id=stream.asin,
            name=stream.title,
            artists=[stream.artist] if stream.artist else ["Unknown Artist"],
            album=stream.album,
            duration_ms=0,
            popularity=0,
            image_url=album.get("image"),
            release_date=album.get("release_date"),
            total_tracks=album.get("trackCount"),
            isrc=stream.isrc or None,
        )


@dataclass
class Artist:
    id: str           # Slugified artist name — T2Tunes exposes no artist IDs
    name: str
    popularity: int
    genres: List[str]
    followers: int
    image_url: Optional[str] = None
    external_urls: Optional[Dict[str, str]] = None

    @classmethod
    def from_name(cls, name: str) -> "Artist":
        slug = name.lower().replace(" ", "_")
        return cls(id=slug, name=name, popularity=0, genres=[], followers=0)


@dataclass
class Album:
    id: str           # Amazon album ASIN
    name: str
    artists: List[str]
    release_date: str
    total_tracks: int
    album_type: str
    image_url: Optional[str] = None
    external_urls: Optional[Dict[str, str]] = None
    explicit: Optional[bool] = None

    @classmethod
    def from_search_hit(cls, doc: Dict[str, Any]) -> "Album":
        return cls(
            id=str(doc.get("albumAsin") or doc.get("asin") or ""),
            name=str(doc.get("albumName") or doc.get("title") or ""),
            artists=[str(doc.get("artistName") or "Unknown Artist")],
            release_date="",
            total_tracks=0,
            album_type="album",
        )

    @classmethod
    def from_metadata(cls, album_meta: Dict[str, Any], asin: str = "") -> "Album":
        return cls(
            id=str(album_meta.get("asin") or asin or ""),
            name=str(album_meta.get("title") or ""),
            artists=[str(album_meta.get("artistName") or "Unknown Artist")],
            release_date=str(album_meta.get("release_date") or ""),
            total_tracks=int(album_meta.get("trackCount") or 0),
            album_type="album",
            image_url=album_meta.get("image"),
            explicit=album_meta.get("explicit"),
        )


# ---------------------------------------------------------------------------
# Internal dataclasses for raw T2Tunes response parsing
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class T2TunesSearchItem:
    asin: str
    title: str
    artist_name: str
    item_type: str
    album_name: str = ""
    album_asin: str = ""
    duration_seconds: int = 0
    isrc: str = ""

    @property
    def is_album(self) -> bool:
        return "album" in self.item_type.lower()

    @property
    def is_track(self) -> bool:
        return "track" in self.item_type.lower()


@dataclass(frozen=True)
class T2TunesStreamInfo:
    asin: str
    streamable: bool
    codec: str
    format: str
    sample_rate: Optional[int]
    stream_url: str
    decryption_key: Optional[str]   # hex-encoded AES key; None when stream is clear
    title: str = ""
    artist: str = ""
    album: str = ""
    isrc: str = ""
    cover_url: str = ""
    track_number: Optional[int] = None
    disc_number: Optional[int] = None
    genre: str = ""
    label: str = ""
    date: str = ""

    @property
    def has_decryption_key(self) -> bool:
        return bool(self.decryption_key)


# ---------------------------------------------------------------------------
# Rate-limit enforcement
# ---------------------------------------------------------------------------

def _rate_limit() -> None:
    global _last_api_call
    with _api_call_lock:
        elapsed = time.monotonic() - _last_api_call
        if elapsed < MIN_API_INTERVAL:
            time.sleep(MIN_API_INTERVAL - elapsed)
        _last_api_call = time.monotonic()
    api_call_tracker.record_call("amazon")


# ---------------------------------------------------------------------------
# Main client
# ---------------------------------------------------------------------------

class AmazonClient:
    """T2Tunes-backed Amazon Music metadata and stream-info client."""

    def __init__(
        self,
        base_url: Optional[str] = None,
        country: Optional[str] = None,
        preferred_codec: Optional[str] = None,
        timeout: int = 30,
        session: Optional[Any] = None,
    ) -> None:
        self.base_url = (base_url or config_manager.get("amazon.base_url", DEFAULT_BASE_URL)).rstrip("/")
        self.country = (country or config_manager.get("amazon.country", DEFAULT_COUNTRY)).upper()
        self.preferred_codec = (
            preferred_codec or config_manager.get("amazon.preferred_codec", DEFAULT_CODEC)
        ).lower()
        self.timeout = timeout
        self.session: Any = session or requests.Session()
        if isinstance(self.session, requests.Session):
            self.session.headers.update({
                "Accept": "application/json",
                "User-Agent": "SoulSync/1.0",
                "Referer": self.base_url,
            })

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def reload_config(self) -> None:
        self.base_url = config_manager.get("amazon.base_url", DEFAULT_BASE_URL).rstrip("/")
        self.country = config_manager.get("amazon.country", DEFAULT_COUNTRY).upper()
        self.preferred_codec = config_manager.get("amazon.preferred_codec", DEFAULT_CODEC).lower()

    def is_authenticated(self) -> bool:
        """Return True when the T2Tunes instance reports Amazon Music as up."""
        try:
            return str(self.status().get("amazonMusic", "")).lower() == "up"
        except AmazonClientError:
            return False

    # ------------------------------------------------------------------
    # Low-level API wrappers
    # ------------------------------------------------------------------

    def status(self) -> Dict[str, Any]:
        return self._get_json("/api/status")

    def search_raw(self, query: str, *, types: str = "track,album") -> List[T2TunesSearchItem]:
        data = self._get_json(
            "/api/amazon-music/search",
            params={"query": query, "types": types, "country": self.country},
        )
        return list(self._iter_search_items(data))

    def album_metadata(self, asin: str) -> Dict[str, Any]:
        return self._get_json(
            "/api/amazon-music/metadata",
            params={"asin": asin, "country": self.country},
        )

    def media_from_asin(self, asin: str, codec: Optional[str] = None) -> List[T2TunesStreamInfo]:
        effective_codec = (codec or self.preferred_codec).lower()
        data = self._get_json(
            "/api/amazon-music/media-from-asin",
            params={"asin": asin, "country": self.country, "codec": effective_codec},
        )
        if isinstance(data, list):
            return [self._parse_stream_info(item) for item in data if isinstance(item, dict)]
        if isinstance(data, dict):
            return [self._parse_stream_info(data)]
        raise AmazonClientError(f"Unexpected media-from-asin response type: {type(data).__name__}")

    # ------------------------------------------------------------------
    # Metadata interface — mirrors DeezerClient / iTunesClient signatures
    # ------------------------------------------------------------------

    def search_tracks(self, query: str, limit: int = 20) -> List[Track]:
        _rate_limit()
        items = self.search_raw(query, types="track")
        tracks: List[Track] = []
        for item in items:
            if not item.is_track:
                continue
            tracks.append(Track.from_search_hit({
                "asin": item.asin,
                "title": item.title,
                "artistName": item.artist_name,
                "albumName": item.album_name,
                "albumAsin": item.album_asin,
                "duration": item.duration_seconds,
                "isrc": item.isrc,
            }))
            if len(tracks) >= limit:
                break
        return tracks

    def search_artists(self, query: str, limit: int = 20) -> List[Artist]:
        _rate_limit()
        items = self.search_raw(query, types="track")
        seen: Dict[str, Artist] = {}
        for item in items:
            name = item.artist_name
            if name and name not in seen:
                seen[name] = Artist.from_name(name)
            if len(seen) >= limit:
                break
        return list(seen.values())

    def search_albums(self, query: str, limit: int = 20) -> List[Album]:
        _rate_limit()
        items = self.search_raw(query, types="album")
        albums: List[Album] = []
        seen_asins: set = set()
        for item in items:
            if not item.is_album:
                continue
            album_asin = item.album_asin or item.asin
            if album_asin in seen_asins:
                continue
            seen_asins.add(album_asin)
            albums.append(Album.from_search_hit({
                "albumAsin": album_asin,
                "albumName": item.album_name or item.title,
                "artistName": item.artist_name,
            }))
            if len(albums) >= limit:
                break
        return albums

    def get_track_details(self, asin: str) -> Optional[Dict[str, Any]]:
        """Return a Spotify-compatible dict for a single track ASIN."""
        _rate_limit()
        try:
            streams = self.media_from_asin(asin)
        except AmazonClientError:
            return None
        if not streams:
            return None
        s = streams[0]

        album_data: Dict[str, Any] = {}
        try:
            meta = self.album_metadata(asin)
            albums = meta.get("albumList")
            if isinstance(albums, list) and albums and isinstance(albums[0], dict):
                album_data = albums[0]
        except AmazonClientError:
            pass

        return {
            "id": s.asin,
            "name": s.title,
            "artists": [{"name": s.artist, "id": ""}],
            "album": {
                "id": album_data.get("asin", ""),
                "name": s.album,
                "images": [{"url": album_data["image"]}] if album_data.get("image") else [],
                "release_date": album_data.get("release_date", ""),
                "total_tracks": album_data.get("trackCount", 0),
            },
            "duration_ms": 0,
            "popularity": 0,
            "external_urls": {"amazon": f"https://music.amazon.com/albums/{asin}"},
            "track_number": None,
            "disc_number": None,
            "isrc": s.isrc,
            "is_album_track": True,
            "raw_data": {
                "codec": s.codec,
                "format": s.format,
                "sample_rate": s.sample_rate,
                "streamable": s.streamable,
                "has_decryption_key": s.has_decryption_key,
            },
        }

    def get_album(self, asin: str, include_tracks: bool = True) -> Optional[Dict[str, Any]]:
        """Return a Spotify-compatible album dict."""
        _rate_limit()
        try:
            meta = self.album_metadata(asin)
        except AmazonClientError:
            return None

        albums = meta.get("albumList")
        if not isinstance(albums, list) or not albums:
            return None
        album = albums[0] if isinstance(albums[0], dict) else {}

        result: Dict[str, Any] = {
            "id": asin,
            "name": album.get("title", ""),
            "artists": [{"name": album.get("artistName", ""), "id": ""}],
            "release_date": album.get("release_date", ""),
            "total_tracks": album.get("trackCount", 0),
            "album_type": "album",
            "images": [{"url": album["image"]}] if album.get("image") else [],
            "external_urls": {"amazon": f"https://music.amazon.com/albums/{asin}"},
            "label": album.get("label", ""),
        }
        if include_tracks:
            result["tracks"] = self.get_album_tracks(asin) or {
                "items": [],
                "total": 0,
                "limit": 50,
                "next": None,
            }
        return result

    def get_album_tracks(self, asin: str) -> Optional[Dict[str, Any]]:
        """Return album tracks in Spotify pagination format."""
        _rate_limit()
        try:
            streams = self.media_from_asin(asin)
        except AmazonClientError:
            return None
        items = [
            {
                "id": s.asin,
                "name": s.title,
                "artists": [{"name": s.artist, "id": ""}],
                "duration_ms": 0,
                "track_number": None,
                "disc_number": None,
                "isrc": s.isrc,
            }
            for s in streams
        ]
        return {"items": items, "total": len(items), "limit": 50, "next": None}

    def get_artist(self, artist_name: str) -> Optional[Dict[str, Any]]:
        """Return a Spotify-compatible artist dict inferred from search results."""
        _rate_limit()
        try:
            items = self.search_raw(artist_name, types="track")
        except AmazonClientError:
            return None
        name_lower = artist_name.lower()
        match = next(
            (i for i in items if i.artist_name.lower() == name_lower),
            next((i for i in items if name_lower in i.artist_name.lower()), None),
        )
        if not match:
            return None
        return {
            "id": match.artist_name.lower().replace(" ", "_"),
            "name": match.artist_name,
            "genres": [],
            "popularity": 0,
            "followers": {"total": 0},
            "images": [],
            "external_urls": {},
        }

    def get_artist_albums(
        self,
        artist_name: str,
        album_type: str = "album,single",
        limit: int = 200,
    ) -> List[Album]:
        """Return albums for an artist inferred from search results."""
        _rate_limit()
        try:
            items = self.search_raw(f"{artist_name} album", types="album")
        except AmazonClientError:
            return []
        albums: List[Album] = []
        seen_asins: set = set()
        name_lower = artist_name.lower()
        for item in items:
            if item.artist_name.lower() != name_lower:
                continue
            album_asin = item.album_asin or item.asin
            if album_asin in seen_asins:
                continue
            seen_asins.add(album_asin)
            albums.append(Album.from_search_hit({
                "albumAsin": album_asin,
                "albumName": item.album_name or item.title,
                "artistName": item.artist_name,
            }))
            if len(albums) >= limit:
                break
        return albums

    def get_track_features(self, track_id: str) -> Optional[Dict[str, Any]]:
        """Not available from Amazon Music — returns None for compatibility."""
        return None

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _get_json(self, path: str, params: Optional[Dict[str, Any]] = None) -> Any:
        url = urljoin(f"{self.base_url}/", path.lstrip("/"))
        try:
            resp = self.session.get(url, params=params, timeout=self.timeout)
            resp.raise_for_status()
        except requests.HTTPError as exc:
            raise AmazonClientError(
                f"HTTP {exc.response.status_code} for {url}"
            ) from exc
        except requests.RequestException as exc:
            raise AmazonClientError(f"Request failed for {url}: {exc}") from exc
        try:
            return resp.json()
        except ValueError as exc:
            preview = resp.text[:200].replace("\n", " ")
            raise AmazonClientError(f"Response not JSON for {url}: {preview!r}") from exc

    @staticmethod
    def _iter_search_items(response: Any) -> Iterator[T2TunesSearchItem]:
        if not isinstance(response, dict):
            raise AmazonClientError(
                f"Unexpected search response type: {type(response).__name__}"
            )
        for result in response.get("results") or []:
            if not isinstance(result, dict):
                continue
            for hit in result.get("hits") or []:
                if not isinstance(hit, dict):
                    continue
                doc = hit.get("document")
                if not isinstance(doc, dict):
                    continue
                asin = str(doc.get("asin") or "")
                if not asin:
                    continue
                yield T2TunesSearchItem(
                    asin=asin,
                    title=str(doc.get("title") or ""),
                    artist_name=str(doc.get("artistName") or ""),
                    item_type=str(doc.get("__type") or ""),
                    album_name=str(doc.get("albumName") or ""),
                    album_asin=str(doc.get("albumAsin") or ""),
                    duration_seconds=int(doc.get("duration") or 0),
                    isrc=str(doc.get("isrc") or ""),
                )

    @staticmethod
    def _parse_stream_info(item: Dict[str, Any]) -> T2TunesStreamInfo:
        stream_info = item.get("streamInfo") if isinstance(item.get("streamInfo"), dict) else {}
        tags = item.get("tags") if isinstance(item.get("tags"), dict) else {}
        # T2Tunes API has a typo: "stremeable" in some responses
        streamable = item.get("streamable")
        if streamable is None:
            streamable = item.get("stremeable")
        raw_key = item.get("decryptionKey")
        decryption_key = str(raw_key) if raw_key else None

        def _int_tag(key: str) -> Optional[int]:
            v = tags.get(key)
            try:
                return int(v) if v is not None else None
            except (TypeError, ValueError):
                return None

        return T2TunesStreamInfo(
            asin=str(item.get("asin") or ""),
            streamable=bool(streamable),
            codec=str(stream_info.get("codec") or ""),
            format=str(stream_info.get("format") or ""),
            sample_rate=(
                stream_info.get("sampleRate")
                if isinstance(stream_info.get("sampleRate"), int)
                else None
            ),
            stream_url=str(stream_info.get("streamUrl") or ""),
            decryption_key=decryption_key,
            title=str(tags.get("title") or ""),
            artist=str(tags.get("artist") or ""),
            album=str(tags.get("album") or ""),
            isrc=str(tags.get("isrc") or ""),
            cover_url=str(item.get("coverUrl") or ""),
            track_number=_int_tag("trackNumber"),
            disc_number=_int_tag("discNumber"),
            genre=str(tags.get("genre") or ""),
            label=str(tags.get("label") or ""),
            date=str(tags.get("date") or ""),
        )
