"""Typed adapters from the legacy metadata facade into Library v2.

The metadata package still exposes compatibility dictionaries. Library v2
normalizes those dictionaries once at its boundary and only persists the typed
shape. Provider-specific response keys must not leak beyond this module.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Dict, Mapping, Optional, Tuple

from utils.logging_config import get_logger


DISCOGRAPHY_PARSER_VERSION = "library2-discography/1"
TRACKLIST_PARSER_VERSION = "library2-tracklist/1"
ARTWORK_PARSER_VERSION = "library2-artwork/1"
logger = get_logger("library2.provider_adapters")


def _optional_text(value: Any) -> Optional[str]:
    text = str(value or "").strip()
    return text or None


def _optional_nonnegative_int(value: Any) -> Optional[int]:
    if value in (None, ""):
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed >= 0 else None


@dataclass(frozen=True)
class DiscographyRelease:
    provider_id: str
    title: str
    album_type: str
    release_date: Optional[str]
    year: Optional[int]
    track_count: Optional[int]
    image_url: Optional[str]
    secondary_types: Tuple[str, ...]
    explicit: Optional[bool]

    @classmethod
    def from_card(cls, card: Mapping[str, Any]) -> Optional["DiscographyRelease"]:
        provider_id = str(card.get("id") or "").strip()
        title = str(card.get("title") or card.get("name") or "").strip()
        if not title:
            return None
        album_type = str(card.get("album_type") or "album").strip().lower()
        if album_type not in {
            "album", "single", "ep", "compilation", "live", "appears_on",
            "appears-on",
        }:
            album_type = "album"
        year = _optional_nonnegative_int(card.get("year"))
        release_date = _optional_text(card.get("release_date"))
        if year is None and release_date:
            year = _optional_nonnegative_int(release_date[:4])
        secondary_types = tuple(
            str(value).strip() for value in (card.get("secondary_types") or [])
            if str(value).strip()
        )
        explicit_value = card.get("explicit")
        explicit = bool(explicit_value) if explicit_value is not None else None
        return cls(
            provider_id=provider_id,
            title=title,
            album_type=album_type,
            release_date=release_date,
            year=year,
            track_count=_optional_nonnegative_int(card.get("track_count")),
            image_url=_optional_text(card.get("image_url")),
            secondary_types=secondary_types,
            explicit=explicit,
        )

    def to_payload(self) -> Dict[str, Any]:
        return {
            "id": self.provider_id,
            "title": self.title,
            "album_type": self.album_type,
            "release_date": self.release_date,
            "year": self.year,
            "track_count": self.track_count,
            "image_url": self.image_url,
            "secondary_types": list(self.secondary_types),
            "explicit": self.explicit,
        }


@dataclass(frozen=True)
class DiscographyProviderResult:
    provider: str
    provider_entity_id: Optional[str]
    releases: Tuple[DiscographyRelease, ...]
    is_complete: bool
    cursor: Optional[str]
    page_count: Optional[int]
    etag: Optional[str]
    provider_version: Optional[str]
    parser_version: str = DISCOGRAPHY_PARSER_VERSION

    def snapshot_payload(self) -> Dict[str, Any]:
        return {"releases": [release.to_payload() for release in self.releases]}


@dataclass(frozen=True)
class TracklistTrack:
    title: str
    track_number: Optional[int]
    disc_number: int
    duration_ms: Optional[int]
    spotify_id: Optional[str]

    @classmethod
    def from_item(cls, item: Mapping[str, Any], *, provider: str) -> Optional["TracklistTrack"]:
        title = str(item.get("name") or item.get("title") or "").strip()
        if not title:
            return None
        number = _optional_nonnegative_int(
            item.get("track_number") or item.get("track_position") or item.get("position"))
        disc = _optional_nonnegative_int(item.get("disc_number")) or 1
        duration = _optional_nonnegative_int(item.get("duration_ms"))
        if duration is None and provider == "deezer":
            seconds = _optional_nonnegative_int(item.get("duration"))
            duration = seconds * 1000 if seconds is not None else None
        return cls(
            title=title,
            track_number=number,
            disc_number=disc,
            duration_ms=duration,
            spotify_id=(str(item.get("id"))
                        if provider == "spotify" and item.get("id") else None),
        )

    def to_payload(self) -> Dict[str, Any]:
        payload: Dict[str, Any] = {
            "track_number": self.track_number,
            "disc_number": self.disc_number,
            "title": self.title,
        }
        if self.duration_ms is not None:
            payload["duration_ms"] = self.duration_ms
        if self.spotify_id:
            payload["spotify_id"] = self.spotify_id
        return payload


@dataclass(frozen=True)
class TracklistProviderResult:
    provider: str
    provider_entity_id: str
    tracks: Tuple[TracklistTrack, ...]
    is_complete: bool = True
    parser_version: str = TRACKLIST_PARSER_VERSION

    def track_payloads(self) -> list[Dict[str, Any]]:
        return [track.to_payload() for track in self.tracks]

    def snapshot_payload(self, reference: Mapping[str, Any]) -> Dict[str, Any]:
        return {
            "reference": dict(reference),
            "tracks": self.track_payloads(),
        }


@dataclass(frozen=True)
class ArtworkProviderResult:
    """Normalized result from the shared artist/cover-art resolvers."""

    kind: str
    source: str
    provider_entity_id: Optional[str]
    url: str
    parser_version: str = ARTWORK_PARSER_VERSION


def _normalized_source_ids(
    source_ids: Optional[Mapping[str, str]],
) -> Dict[str, str]:
    return {
        str(source).strip().lower(): str(value).strip()
        for source, value in (source_ids or {}).items()
        if str(source).strip() and str(value).strip()
    }


def _track_items(payload: Any) -> list[Mapping[str, Any]]:
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, Mapping)]
    if not isinstance(payload, Mapping):
        return []
    for key in ("items", "tracks", "data"):
        value = payload.get(key)
        if isinstance(value, Mapping):
            value = value.get("items") or value.get("data")
        if isinstance(value, list):
            return [item for item in value if isinstance(item, Mapping)]
    return []


def _normalize_tracklist(payload: Any, provider: str) -> Tuple[TracklistTrack, ...]:
    tracks = []
    for item in _track_items(payload):
        track = TracklistTrack.from_item(item, provider=provider)
        if track is not None:
            tracks.append(track)
    return tuple(tracks)


def _release_year(value: Any) -> Optional[int]:
    text = str(value or "").strip()
    if len(text) < 4 or not text[:4].isdigit():
        return None
    return int(text[:4])


def _barcode(value: Any) -> Optional[str]:
    normalized = re.sub(r"[^0-9A-Za-z]", "", str(value or "")).upper()
    return normalized or None


def _deezer_search_match(
    metadata: Any,
    *,
    release_date: Optional[str],
    expected_track_count: Optional[int],
    source_ids: Mapping[str, str],
) -> bool:
    """Accept a name-search result only when every known edition fact matches."""
    if not isinstance(metadata, Mapping):
        return not (
            _release_year(release_date)
            or _optional_nonnegative_int(expected_track_count)
            or source_ids.get("upc")
            or source_ids.get("barcode")
        )

    raw = metadata.get("_raw_data")
    if not isinstance(raw, Mapping):
        raw = metadata

    wanted_year = _release_year(release_date)
    if wanted_year is not None:
        actual_year = _release_year(
            metadata.get("release_date") or raw.get("release_date"))
        if actual_year != wanted_year:
            return False

    wanted_count = _optional_nonnegative_int(expected_track_count)
    if wanted_count:
        actual_count = _optional_nonnegative_int(
            metadata.get("total_tracks")
            or metadata.get("nb_tracks")
            or raw.get("nb_tracks")
            or raw.get("total_tracks")
        )
        if actual_count != wanted_count:
            return False

    wanted_upc = _barcode(source_ids.get("upc") or source_ids.get("barcode"))
    if wanted_upc:
        external_ids = metadata.get("external_ids")
        if not isinstance(external_ids, Mapping):
            external_ids = {}
        actual_upc = _barcode(
            metadata.get("upc")
            or metadata.get("barcode")
            or external_ids.get("upc")
            or external_ids.get("barcode")
            or raw.get("upc")
            or raw.get("barcode")
        )
        if actual_upc != wanted_upc:
            return False
    return True


def fetch_album_tracklist(
    album_title: str,
    artist_name: str,
    *,
    source_album_ids: Optional[Mapping[str, str]] = None,
    release_date: Optional[str] = None,
    expected_track_count: Optional[int] = None,
) -> Optional[TracklistProviderResult]:
    """Resolve a canonical tracklist through typed Spotify/Deezer adapters."""
    from core.metadata.registry import get_deezer_client, get_spotify_client

    source_ids = _normalized_source_ids(source_album_ids)
    spotify_id = source_ids.get("spotify")
    if spotify_id:
        try:
            client = get_spotify_client()
            if client:
                tracks = _normalize_tracklist(
                    client.get_album_tracks(spotify_id), "spotify")
                if tracks:
                    return TracklistProviderResult("spotify", spotify_id, tracks)
        except Exception as exc:  # noqa: BLE001
            logger.debug("spotify tracklist lookup failed (%s): %s", spotify_id, exc)

    if artist_name and album_title:
        try:
            client = get_deezer_client()
            if client:
                deezer_id = source_ids.get("deezer")
                if not deezer_id:
                    album = client.search_album(artist_name, album_title)
                    if isinstance(album, Mapping) and album.get("id"):
                        candidate_id = str(album["id"])
                        fetch_metadata = getattr(client, "get_album_metadata", None)
                        metadata = (
                            fetch_metadata(candidate_id, include_tracks=False)
                            if callable(fetch_metadata)
                            else album
                        )
                        if _deezer_search_match(
                            metadata,
                            release_date=release_date,
                            expected_track_count=expected_track_count,
                            source_ids=source_ids,
                        ):
                            deezer_id = candidate_id
                        else:
                            logger.info(
                                "Rejected Deezer name-search edition %s for %s - %s",
                                candidate_id,
                                artist_name,
                                album_title,
                            )
                if deezer_id:
                    tracks = _normalize_tracklist(
                        client.get_album_tracks(deezer_id), "deezer")
                    if tracks:
                        return TracklistProviderResult("deezer", deezer_id, tracks)
        except Exception as exc:  # noqa: BLE001
            logger.debug("deezer tracklist lookup failed (%s): %s", album_title, exc)
    return None


def fetch_artist_discography(
    artist_name: str,
    *,
    source_artist_ids: Optional[Mapping[str, str]] = None,
) -> Optional[DiscographyProviderResult]:
    """Fetch and normalize one full provider discography.

    ``max_pages=0`` explicitly requests an unbounded traversal. Tests and future
    providers can return ``is_complete=False`` plus cursor metadata when a
    provider stops early; callers must then suppress destructive pruning.
    """
    from core.metadata.discography import get_artist_detail_discography
    from core.metadata.lookup import MetadataLookupOptions

    normalized_ids = _normalized_source_ids(source_artist_ids)
    result = get_artist_detail_discography(
        "",
        artist_name=artist_name,
        options=MetadataLookupOptions(
            limit=200,
            max_pages=0,
            artist_source_ids=normalized_ids or None,
        ),
    )
    if not result or not result.get("success"):
        return None

    provider = str(result.get("source") or "unknown").strip().lower()
    releases = []
    for group in ("albums", "eps", "singles"):
        for card in result.get(group) or []:
            if not isinstance(card, Mapping):
                continue
            release = DiscographyRelease.from_card(card)
            if release is not None:
                releases.append(release)
    if not releases:
        return None

    complete_value = result.get("is_complete")
    is_complete = True if complete_value is None else bool(complete_value)
    return DiscographyProviderResult(
        provider=provider,
        provider_entity_id=normalized_ids.get(provider),
        releases=tuple(releases),
        is_complete=is_complete,
        cursor=_optional_text(result.get("cursor")),
        page_count=_optional_nonnegative_int(result.get("page_count")),
        etag=_optional_text(result.get("etag")),
        provider_version=_optional_text(result.get("provider_version")),
    )


def fetch_artwork_url(
    kind: str,
    *,
    artist_name: str,
    album_title: Optional[str] = None,
    source_ids: Optional[Mapping[str, str]] = None,
    source_order: Optional[Tuple[str, ...]] = None,
) -> Optional[ArtworkProviderResult]:
    """Resolve artwork through existing engines and return one typed result.

    Library v2 supplies normalized catalog facts; provider-specific response
    dictionaries stay inside ``core.metadata``. No image bytes or signed
    provider payloads are persisted by this adapter.
    """
    kind = str(kind or "").strip().lower()
    if kind not in {"artist", "album"}:
        raise ValueError("artwork kind must be artist or album")
    normalized_ids = _normalized_source_ids(source_ids)
    artist_name = str(artist_name or "").strip()

    if kind == "artist":
        from core.metadata.artist_image import get_artist_image_url
        preferred = ["spotify", "musicbrainz"]
        preferred.extend(sorted(set(normalized_ids) - set(preferred)))
        for source in preferred:
            provider_id = normalized_ids.get(source)
            if not provider_id:
                continue
            url = get_artist_image_url(
                provider_id,
                source_override=source,
                artist_name=artist_name,
            )
            url = _optional_text(url)
            if url:
                return ArtworkProviderResult(
                    kind="artist",
                    source=source,
                    provider_entity_id=provider_id,
                    url=url,
                )
        return None

    album_title = str(album_title or "").strip()
    if not artist_name or not album_title:
        return None
    from core.metadata.art_lookup import (
        available_art_sources,
        select_preferred_art,
    )
    order = tuple(source_order) if source_order is not None else tuple(
        available_art_sources()
    )
    metadata = {}
    if normalized_ids.get("musicbrainz"):
        metadata["musicbrainz_release_id"] = normalized_ids["musicbrainz"]
    url, source = select_preferred_art(
        artist_name,
        album_title,
        metadata,
        order,
    )
    url = _optional_text(url)
    source = _optional_text(source)
    if not url or not source:
        return None
    return ArtworkProviderResult(
        kind="album",
        source=source,
        provider_entity_id=normalized_ids.get(source),
        url=url,
    )


__all__ = [
    "ARTWORK_PARSER_VERSION",
    "DISCOGRAPHY_PARSER_VERSION",
    "TRACKLIST_PARSER_VERSION",
    "ArtworkProviderResult",
    "DiscographyProviderResult",
    "DiscographyRelease",
    "TracklistProviderResult",
    "TracklistTrack",
    "fetch_album_tracklist",
    "fetch_artwork_url",
    "fetch_artist_discography",
]
