"""Typed adapters from the legacy metadata facade into Library v2.

The metadata package still exposes compatibility dictionaries. Library v2
normalizes those dictionaries once at its boundary and only persists the typed
shape. Provider-specific response keys must not leak beyond this module.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Mapping, Optional, Tuple


DISCOGRAPHY_PARSER_VERSION = "library2-discography/1"


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

    normalized_ids = {
        str(source).strip().lower(): str(value).strip()
        for source, value in (source_artist_ids or {}).items()
        if str(source).strip() and str(value).strip()
    }
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


__all__ = [
    "DISCOGRAPHY_PARSER_VERSION",
    "DiscographyProviderResult",
    "DiscographyRelease",
    "fetch_artist_discography",
]
