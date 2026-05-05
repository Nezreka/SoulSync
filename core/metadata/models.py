"""Canonical typed metadata entities.

The metadata layer treats ``source_id`` as the authoritative external
identifier. ``id`` stays as a compatibility alias for older callers.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional


class MetadataRecord(dict):
    """Dictionary payload with attribute access for compatibility.

    ``source_id`` is the primary external identifier. ``id`` mirrors it
    so older call sites keep working while new code can depend on the
    explicit source-scoped field.
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._sync_identity_aliases()

    def __getattr__(self, item: str) -> Any:
        try:
            return self[item]
        except KeyError as exc:  # pragma: no cover - standard attribute fallback
            raise AttributeError(item) from exc

    def __setattr__(self, key: str, value: Any) -> None:
        self[key] = value

    def __setitem__(self, key: str, value: Any) -> None:
        if key == "id":
            source_id = dict.get(self, "source_id")
            if source_id not in (None, ""):
                value = source_id
        dict.__setitem__(self, key, value)
        self._sync_identity_aliases()

    def update(self, *args, **kwargs) -> None:  # pragma: no cover - thin dict wrapper
        dict.update(self, *args, **kwargs)
        self._sync_identity_aliases()

    def _sync_identity_aliases(self) -> None:
        source_id = dict.get(self, "source_id")
        entity_id = dict.get(self, "id")

        if source_id not in (None, ""):
            dict.__setitem__(self, "source_id", source_id)
            dict.__setitem__(self, "id", source_id)
            return

        if entity_id not in (None, ""):
            dict.__setitem__(self, "source_id", entity_id)
            dict.__setitem__(self, "id", entity_id)

    def copy(self):  # pragma: no cover - dict compatibility helper
        return MetadataRecord(super().copy())


def as_metadata_record(data: Optional[dict[str, Any]] = None, **extra: Any) -> MetadataRecord:
    payload = MetadataRecord(data or {})
    payload.update(extra)
    return payload


@dataclass(frozen=True)
class MetadataTrack:
    id: str
    name: str
    artists: list[str]
    album: str
    duration_ms: int
    popularity: int
    preview_url: Optional[str] = None
    external_urls: Optional[dict[str, str]] = None
    image_url: Optional[str] = None
    release_date: Optional[str] = None
    track_number: Optional[int] = None
    disc_number: Optional[int] = None
    album_type: Optional[str] = None
    total_tracks: Optional[int] = None
    source: Optional[str] = None
    source_id: Optional[str] = None
    raw_data: Optional[dict[str, Any]] = None

    def to_dict(self) -> dict[str, Any]:
        source_id = self.source_id or self.id or ""
        compat_id = source_id or self.id or ""
        data = {
            "source_id": source_id,
            "id": compat_id,
            "name": self.name,
            "artists": list(self.artists),
            "album": self.album,
            "duration_ms": self.duration_ms,
            "popularity": self.popularity,
            "preview_url": self.preview_url,
            "external_urls": dict(self.external_urls or {}),
            "image_url": self.image_url,
            "release_date": self.release_date,
            "track_number": self.track_number,
            "disc_number": self.disc_number,
            "album_type": self.album_type,
            "total_tracks": self.total_tracks,
        }
        if self.source:
            data["source"] = self.source
        if self.raw_data is not None:
            data["raw_data"] = self.raw_data
        return data

    def to_record(self) -> MetadataRecord:
        return as_metadata_record(self.to_dict())


@dataclass(frozen=True)
class MetadataArtist:
    id: str
    name: str
    popularity: int
    genres: list[str]
    followers: int
    image_url: Optional[str] = None
    external_urls: Optional[dict[str, str]] = None
    source: Optional[str] = None
    source_id: Optional[str] = None
    raw_data: Optional[dict[str, Any]] = None

    def to_dict(self) -> dict[str, Any]:
        source_id = self.source_id or self.id or ""
        compat_id = source_id or self.id or ""
        data = {
            "source_id": source_id,
            "id": compat_id,
            "name": self.name,
            "popularity": self.popularity,
            "genres": list(self.genres),
            "followers": self.followers,
            "image_url": self.image_url,
            "external_urls": dict(self.external_urls or {}),
        }
        if self.source:
            data["source"] = self.source
        if self.raw_data is not None:
            data["raw_data"] = self.raw_data
        return data

    def to_record(self) -> MetadataRecord:
        return as_metadata_record(self.to_dict())


@dataclass(frozen=True)
class MetadataAlbum:
    id: str
    name: str
    artists: list[str]
    release_date: str
    total_tracks: int
    album_type: str
    image_url: Optional[str] = None
    external_urls: Optional[dict[str, str]] = None
    source: Optional[str] = None
    source_id: Optional[str] = None
    raw_data: Optional[dict[str, Any]] = None

    def to_dict(self) -> dict[str, Any]:
        source_id = self.source_id or self.id or ""
        compat_id = source_id or self.id or ""
        data = {
            "source_id": source_id,
            "id": compat_id,
            "name": self.name,
            "artists": list(self.artists),
            "release_date": self.release_date,
            "total_tracks": self.total_tracks,
            "album_type": self.album_type,
            "image_url": self.image_url,
            "external_urls": dict(self.external_urls or {}),
        }
        if self.source:
            data["source"] = self.source
        if self.raw_data is not None:
            data["raw_data"] = self.raw_data
        return data

    def to_record(self) -> MetadataRecord:
        return as_metadata_record(self.to_dict())


@dataclass(frozen=True)
class MetadataPlaylist:
    id: str
    name: str
    description: Optional[str]
    owner: str
    public: bool
    collaborative: bool
    tracks: list[MetadataTrack] = field(default_factory=list)
    total_tracks: int = 0
