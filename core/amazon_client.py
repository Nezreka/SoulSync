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

import re
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

# Strips featuring credits like "Artist feat. X", "Artist ft. Y" so artist
# deduplication works on the primary artist name only.
_FEAT_RE = re.compile(r'\s+(?:feat(?:uring)?\.?|ft\.?)\s+.*', re.IGNORECASE)

# Strips the Explicit marker — explicit is treated as the default version.
# Clean/Edited/Censored stay in the name so users can distinguish them.
_EDITION_RE = re.compile(r'\s*[\[\(]explicit[\]\)]', re.IGNORECASE)


def _primary_artist(name: str) -> str:
    return _FEAT_RE.sub('', name).strip()


def _strip_edition(name: str) -> str:
    return _EDITION_RE.sub('', name).strip()


def _unslugify(name: str) -> str:
    """Convert a slug-form artist ID (e.g. 'kendrick_lamar') to a search name."""
    return name.replace('_', ' ')


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
            release_date=str(album_meta.get("release_date") or album_meta.get("releaseDate") or ""),
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
        items = self.search_raw(query, types="track,album")
        track_pairs: List[tuple] = []   # (Track, album_asin)
        seen_album_asins: List[str] = []
        for item in items:
            if not item.is_track:
                continue
            track = Track.from_search_hit({
                "asin": item.asin,
                "title": _strip_edition(item.title),
                "artistName": _primary_artist(item.artist_name),
                "albumName": _strip_edition(item.album_name),
                "albumAsin": item.album_asin,
                "duration": item.duration_seconds,
                "isrc": item.isrc,
            })
            track_pairs.append((track, item.album_asin))
            if item.album_asin and item.album_asin not in seen_album_asins:
                seen_album_asins.append(item.album_asin)
            if len(track_pairs) >= limit:
                break
        album_metas = self._fetch_album_metas(seen_album_asins[:5])
        tracks: List[Track] = []
        for track, album_asin in track_pairs:
            if album_asin and album_asin in album_metas:
                meta = album_metas[album_asin]
                track.image_url = meta.get("image")
                track.release_date = str(meta.get("release_date") or meta.get("releaseDate") or "")
                track.total_tracks = meta.get("trackCount")
            tracks.append(track)
        return tracks

    def search_artists(self, query: str, limit: int = 20) -> List[Artist]:
        _rate_limit()
        items = self.search_raw(query, types="track,album")
        seen: Dict[str, Artist] = {}
        artist_album_asin: Dict[str, str] = {}  # artist name → first album ASIN seen
        for item in items:
            name = _primary_artist(item.artist_name)
            if not name:
                continue
            if name not in seen:
                seen[name] = Artist.from_name(name)
            if name not in artist_album_asin and item.album_asin:
                artist_album_asin[name] = item.album_asin
            if len(seen) >= limit:
                break
        # T2Tunes has no artist images — use an album cover as stand-in.
        unique_asins = list({v for v in artist_album_asin.values()})[:5]
        album_metas = self._fetch_album_metas(unique_asins)
        for name, artist in seen.items():
            asin = artist_album_asin.get(name)
            if asin and asin in album_metas:
                artist.image_url = album_metas[asin].get("image")
        return list(seen.values())

    def search_albums(self, query: str, limit: int = 20) -> List[Album]:
        _rate_limit()
        items = self.search_raw(query, types="track,album")
        album_candidates: List[tuple] = []  # (Album, asin)
        seen_keys: set = set()
        for item in items:
            if not item.is_album:
                continue
            album_asin = item.album_asin or item.asin
            raw_name = item.album_name or item.title
            display_name = _strip_edition(raw_name)
            artist = _primary_artist(item.artist_name)
            # Collapse Explicit/Clean variants: same normalised name + artist = same album
            dedup_key = (display_name.lower(), artist.lower())
            if dedup_key in seen_keys:
                continue
            seen_keys.add(dedup_key)
            album = Album.from_search_hit({
                "albumAsin": album_asin,
                "albumName": display_name,
                "artistName": artist,
            })
            album_candidates.append((album, album_asin))
            if len(album_candidates) >= limit:
                break
        album_metas = self._fetch_album_metas([a for _, a in album_candidates[:10]])
        albums: List[Album] = []
        for album, asin in album_candidates:
            if asin in album_metas:
                meta = album_metas[asin]
                album.image_url = meta.get("image")
                album.release_date = str(meta.get("release_date") or meta.get("releaseDate") or "")
                album.total_tracks = int(meta.get("trackCount") or 0)
            albums.append(album)
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
            "name": _strip_edition(s.title),
            "artists": [{"name": _primary_artist(s.artist), "id": ""}],
            "album": {
                "id": album_data.get("asin", ""),
                "name": _strip_edition(s.album),
                "images": [{"url": album_data["image"]}] if album_data.get("image") else [],
                "release_date": album_data.get("release_date") or album_data.get("releaseDate") or s.date or "",
                "total_tracks": album_data.get("trackCount", 0),
            },
            "duration_ms": 0,
            "popularity": 0,
            "external_urls": {"amazon": f"https://music.amazon.com/albums/{asin}"},
            "track_number": s.track_number,
            "disc_number": s.disc_number,
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
            "name": _strip_edition(album.get("title", "")),
            "artists": [{"name": _primary_artist(album.get("artistName", "")), "id": ""}],
            "release_date": album.get("release_date") or album.get("releaseDate") or "",
            "total_tracks": album.get("trackCount", 0),
            "album_type": "album",
            "images": [{"url": album["image"]}] if album.get("image") else [],
            "external_urls": {"amazon": f"https://music.amazon.com/albums/{asin}"},
            "label": album.get("label", ""),
        }
        if include_tracks:
            tracks_data = self.get_album_tracks(asin) or {
                "items": [], "total": 0, "limit": 50, "next": None,
            }
            result["tracks"] = tracks_data
            # Backfill release_date from stream tags when album metadata lacks it.
            if not result["release_date"]:
                items = tracks_data.get("items") or []
                for item in items:
                    rd = item.get("release_date") or ""
                    if rd and len(rd) >= 4:
                        result["release_date"] = rd
                        break
        return result

    def get_album_tracks(self, asin: str) -> Optional[Dict[str, Any]]:
        """Return album tracks in Spotify pagination format."""
        _rate_limit()
        try:
            streams = self.media_from_asin(asin)
        except AmazonClientError:
            return None

        # media_from_asin has no duration — enrich from search results which do.
        duration_map: Dict[str, int] = {}  # track asin → duration_ms
        if streams:
            album_name = _strip_edition(streams[0].album)
            artist_name = _primary_artist(streams[0].artist)
            try:
                search_items = self.search_raw(
                    f"{album_name} {artist_name}", types="track,album"
                )
                for item in search_items:
                    if item.album_asin == asin and item.duration_seconds:
                        duration_map[item.asin] = item.duration_seconds * 1000
            except Exception:
                pass

        items = [
            {
                "id": s.asin,
                "name": _strip_edition(s.title),
                "artists": [{"name": _primary_artist(s.artist), "id": ""}],
                "duration_ms": duration_map.get(s.asin, 0),
                "track_number": s.track_number,
                "disc_number": s.disc_number,
                "release_date": s.date or "",
                "isrc": s.isrc,
            }
            for s in streams
        ]
        return {"items": items, "total": len(items), "limit": 50, "next": None}

    def get_artist(self, artist_name: str) -> Optional[Dict[str, Any]]:
        """Return a Spotify-compatible artist dict inferred from search results."""
        _rate_limit()
        search_name = _unslugify(artist_name)
        try:
            items = self.search_raw(search_name, types="track,album")
        except AmazonClientError:
            return None
        name_lower = search_name.lower()
        match = next(
            (i for i in items if _primary_artist(i.artist_name).lower() == name_lower),
            next((i for i in items if name_lower in _primary_artist(i.artist_name).lower()), None),
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
        search_name = _unslugify(artist_name)
        try:
            items = self.search_raw(f"{search_name} album", types="track,album")
        except AmazonClientError:
            return []
        album_candidates: List[tuple] = []  # (Album, asin)
        seen_asins: set = set()
        name_lower = search_name.lower()
        for item in items:
            if not item.is_album:
                continue
            if _primary_artist(item.artist_name).lower() != name_lower:
                continue
            album_asin = item.album_asin or item.asin
            if album_asin in seen_asins:
                continue
            seen_asins.add(album_asin)
            album = Album.from_search_hit({
                "albumAsin": album_asin,
                "albumName": _strip_edition(item.album_name or item.title),
                "artistName": _primary_artist(item.artist_name),
            })
            album_candidates.append((album, album_asin))
            if len(album_candidates) >= limit:
                break

        # Fetch metadata for art, release_date, track_count, and type inference.
        # Cap at 10 parallel fetches — discography views don't need full coverage.
        asins_to_fetch = [a for _, a in album_candidates[:10]]
        metas = self._fetch_album_metas(asins_to_fetch)

        albums: List[Album] = []
        for album, asin in album_candidates:
            meta = metas.get(asin, {})
            if meta:
                album.image_url = meta.get("image")
                album.release_date = str(meta.get("release_date") or meta.get("releaseDate") or "")
                total = int(meta.get("trackCount") or 0)
                album.total_tracks = total
                # T2Tunes doesn't expose release type — infer from track count.
                # 1-track releases are singles; keep default "album" otherwise.
                if total == 1:
                    album.album_type = "single"
            albums.append(album)
        return albums

    def get_track_features(self, track_id: str) -> Optional[Dict[str, Any]]:
        """Not available from Amazon Music — returns None for compatibility."""
        return None

    def _get_artist_image_from_albums(self, artist_id: str) -> Optional[str]:
        """Return an album cover as artist image stand-in (T2Tunes has no artist images)."""
        search_name = _unslugify(artist_id)
        try:
            items = self.search_raw(search_name, types="track,album")
        except AmazonClientError:
            return None
        name_lower = search_name.lower()
        for item in items:
            if _primary_artist(item.artist_name).lower() != name_lower:
                continue
            asin = item.album_asin or item.asin
            if not asin:
                continue
            metas = self._fetch_album_metas([asin])
            if asin in metas and metas[asin].get("image"):
                return metas[asin]["image"]
        return None

    # ==================== Interface Aliases (match DeezerClient method names) ====================
    get_album_metadata = get_album
    get_artist_info = get_artist
    get_artist_albums_list = get_artist_albums

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _fetch_album_metas(self, asins: List[str]) -> Dict[str, Dict[str, Any]]:
        """Parallel-fetch album metadata for up to N ASINs. Returns {asin: albumList[0]}."""
        if not asins:
            return {}
        metas: Dict[str, Dict[str, Any]] = {}

        def _fetch(asin: str) -> None:
            _rate_limit()
            try:
                raw = self.album_metadata(asin)
                lst = raw.get("albumList")
                if isinstance(lst, list) and lst and isinstance(lst[0], dict):
                    metas[asin] = lst[0]
            except Exception:
                pass

        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=min(len(asins), 5)) as pool:
            list(pool.map(_fetch, asins))
        return metas

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
