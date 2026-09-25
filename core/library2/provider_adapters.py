"""Typed adapters from the legacy metadata facade into Library v2.

The metadata package still exposes compatibility dictionaries. Library v2
normalizes those dictionaries once at its boundary and only persists the typed
shape. Provider-specific response keys must not leak beyond this module.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass
from dataclasses import asdict, is_dataclass
from inspect import Parameter, signature
from typing import Any, Dict, Mapping, Optional, Tuple

from utils.logging_config import get_logger


# /4: release_group_id rides along, so a MusicBrainz group id stops being
# stored as though it were a release id (see DiscographyRelease).
DISCOGRAPHY_PARSER_VERSION = "library2-discography/4"
TRACKLIST_PARSER_VERSION = "library2-tracklist/4"
ARTWORK_PARSER_VERSION = "library2-artwork/1"
logger = get_logger("library2.provider_adapters")


@dataclass(frozen=True)
class ProviderArtistCredit:
    name: str
    provider_id: Optional[str] = None

    def to_payload(self) -> Dict[str, Optional[str]]:
        return {"name": self.name, "id": self.provider_id}


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


def _artist_credits(value: Any) -> Tuple[ProviderArtistCredit, ...]:
    """Normalize provider artist identities while preserving credited order."""
    if not value:
        return ()
    if isinstance(value, (str, bytes, Mapping)):
        value = [value]
    try:
        entries = list(value)
    except TypeError:
        entries = [value]

    credits = []
    seen = set()
    for entry in entries:
        provider_id = None
        if isinstance(entry, Mapping):
            nested = entry.get("artist")
            if isinstance(nested, Mapping):
                entry = nested
            name = (
                entry.get("name")
                or entry.get("artist_name")
                or entry.get("artistName")
            )
            provider_id = entry.get("id") or entry.get("artist_id") or entry.get("artistId")
        else:
            name = entry
        text = str(name or "").strip()
        key = text.casefold()
        if text and key != "unknown artist" and key not in seen:
            seen.add(key)
            credits.append(ProviderArtistCredit(
                name=text,
                provider_id=str(provider_id).strip() if provider_id else None,
            ))
    return tuple(credits)


def _item_artist_credits(item: Mapping[str, Any]) -> Tuple[ProviderArtistCredit, ...]:
    for key in ("artist_credits", "artists", "contributors", "artist-credit"):
        credits = _artist_credits(item.get(key))
        if credits:
            return credits
    for key in ("artist", "artist_name", "artistName"):
        credits = _artist_credits(item.get(key))
        if credits:
            return credits
    return ()


@dataclass(frozen=True)
class DiscographyRelease:
    provider_id: str
    title: str
    artist_credits: Tuple[ProviderArtistCredit, ...]
    album_type: str
    release_date: Optional[str]
    year: Optional[int]
    track_count: Optional[int]
    image_url: Optional[str]
    secondary_types: Tuple[str, ...]
    explicit: Optional[bool]
    # MusicBrainz has a level above the concrete release — the release GROUP,
    # which several pressings of one record share — and its discography browse
    # returns exactly that level, so ``provider_id`` is then a group mbid. The
    # two are different entities with different ids and neither resolves under
    # the other's URL, so the group travels in its own field instead of being
    # mistaken for the release. Providers without the concept leave it None.
    release_group_id: Optional[str] = None

    @property
    def provider_id_is_release_group(self) -> bool:
        """Is ``provider_id`` itself the group id rather than a release id?

        True for MusicBrainz's discography browse, which projects release
        groups; False when a concrete release was expanded and carries its
        group alongside. Nothing else can tell them apart: both are uuids.
        """
        return bool(self.release_group_id) and self.release_group_id == self.provider_id

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
            artist_credits=_item_artist_credits(card),
            album_type=album_type,
            release_date=release_date,
            year=year,
            track_count=_optional_nonnegative_int(card.get("track_count")),
            image_url=_optional_text(card.get("image_url")),
            secondary_types=secondary_types,
            explicit=explicit,
            release_group_id=_optional_text(
                card.get("release_group_id") or card.get("release-group-id")),
        )

    def to_payload(self) -> Dict[str, Any]:
        payload: Dict[str, Any] = {
            "id": self.provider_id,
            "title": self.title,
            "artists": list(self.artists),
            "artist_credits": [credit.to_payload() for credit in self.artist_credits],
            "album_type": self.album_type,
            "release_date": self.release_date,
            "year": self.year,
            "track_count": self.track_count,
            "image_url": self.image_url,
            "secondary_types": list(self.secondary_types),
            "explicit": self.explicit,
        }
        # Only when there is one: a provider without release groups keeps the
        # payload it always had, so its snapshot hash does not churn.
        if self.release_group_id:
            payload["release_group_id"] = self.release_group_id
        return payload

    @property
    def artists(self) -> Tuple[str, ...]:
        return tuple(credit.name for credit in self.artist_credits)


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
    provider: str
    provider_id: Optional[str]
    isrc: Optional[str] = None
    musicbrainz_id: Optional[str] = None
    artist_credits: Tuple[ProviderArtistCredit, ...] = ()

    @property
    def artists(self) -> Tuple[str, ...]:
        """Credited names only — what compatibility consumers still read."""
        return tuple(credit.name for credit in self.artist_credits)

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
        provider_id = _optional_text(item.get("id"))
        external_ids = item.get("external_ids")
        if not isinstance(external_ids, Mapping):
            external_ids = {}
        isrc = _optional_text(item.get("isrc") or external_ids.get("isrc"))
        musicbrainz_id = _optional_text(
            item.get("musicbrainz_id")
            or item.get("musicbrainz_recording_id")
            or item.get("mbid")
            or external_ids.get("musicbrainz")
            or external_ids.get("mbid")
        )
        if provider == "musicbrainz" and provider_id:
            musicbrainz_id = musicbrainz_id or provider_id
        return cls(
            title=title,
            track_number=number,
            disc_number=disc,
            duration_ms=duration,
            provider=provider,
            provider_id=provider_id,
            isrc=isrc,
            musicbrainz_id=musicbrainz_id,
            # Keep each credit's OWN provider identity. Reducing this to names
            # left every guest on a release id-less, and the unmapped-artist
            # reconciler then resolved them through the album anchor — i.e. to
            # the album's PRIMARY artist, handing a dozen guests one identity.
            artist_credits=_item_artist_credits(item),
        )

    def to_payload(self) -> Dict[str, Any]:
        payload: Dict[str, Any] = {
            "track_number": self.track_number,
            "disc_number": self.disc_number,
            "title": self.title,
        }
        if self.artist_credits:
            payload["artists"] = list(self.artists)
            payload["artist_credits"] = [
                credit.to_payload() for credit in self.artist_credits
            ]
            # The namespace the credit ids belong to. Without it a consumer
            # cannot tell a Deezer artist id from a Spotify one and would file
            # it in the wrong column.
            payload["provider"] = self.provider
        if self.duration_ms is not None:
            payload["duration_ms"] = self.duration_ms
        if self.provider_id:
            # The provider namespace travels with the value.  Compatibility
            # consumers may still read ``spotify_id``, while every provider —
            # including Deezer/iTunes/long-tail adapters — is retained in the
            # canonical mapping.
            payload["external_ids"] = {self.provider: self.provider_id}
            if self.provider == "spotify":
                payload["spotify_id"] = self.provider_id
            elif self.provider == "musicbrainz":
                payload["musicbrainz_id"] = self.provider_id
        if self.isrc:
            payload["isrc"] = self.isrc
        if self.musicbrainz_id:
            payload["musicbrainz_id"] = self.musicbrainz_id
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


@dataclass(frozen=True)
class TrackMetadataProviderResult:
    """Provider-qualified facts used by conservative file maintenance."""

    provider: str
    provider_entity_id: str
    duration_ms: Optional[int]
    image_url: Optional[str]


@dataclass(frozen=True)
class DescriptiveMetadataProviderResult:
    """Provider-qualified descriptive fields for native Library-v2 rows."""

    provider: str
    provider_entity_id: str
    image_url: Optional[str] = None
    genres: Optional[Tuple[str, ...]] = None
    summary: Optional[str] = None
    year: Optional[int] = None
    release_date: Optional[str] = None
    label: Optional[str] = None
    upc: Optional[str] = None
    style: Optional[str] = None
    mood: Optional[str] = None
    explicit: Optional[bool] = None
    duration_ms: Optional[int] = None
    bpm: Optional[float] = None
    lyrics: Optional[str] = None
    copyright: Optional[str] = None
    banner_url: Optional[str] = None


def _normalized_source_ids(
    source_ids: Optional[Mapping[str, str]],
) -> Dict[str, str]:
    return {
        str(source).strip().lower(): str(value).strip()
        for source, value in (source_ids or {}).items()
        if str(source).strip() and str(value).strip()
    }


def _configured_source_order() -> Tuple[str, ...]:
    """Return the user's usable metadata priority, with a safe static fallback."""

    from core.metadata.registry import (
        METADATA_SOURCE_PRIORITY,
        get_primary_source,
        get_source_priority,
    )

    try:
        return tuple(get_source_priority(get_primary_source()))
    except Exception:  # noqa: BLE001 - metadata config is optional in tests/CLI
        return tuple(METADATA_SOURCE_PRIORITY)


def _provider_query_order(
    source_order: Tuple[str, ...], source_ids: Mapping[str, str],
) -> list[str]:
    """Translate resolver aliases to provider namespaces without losing order."""
    ordered: list[str] = []
    for raw_source in source_order:
        source = str(raw_source or "").strip().lower()
        # Cover Art Archive is addressed by a MusicBrainz release ID.  The UI
        # calls the artwork source ``caa`` while the stored identity remains
        # correctly namespaced as ``musicbrainz``.
        provider = "musicbrainz" if source == "caa" else source
        if provider and provider not in ordered:
            ordered.append(provider)
    ordered.extend(sorted(set(source_ids) - set(ordered)))
    return ordered


def _track_items(payload: Any) -> list[Mapping[str, Any]]:
    if isinstance(payload, list):
        items = payload
    elif not isinstance(payload, Mapping):
        return []
    else:
        items = []
        for key in ("items", "tracks", "data"):
            value = payload.get(key)
            if isinstance(value, Mapping):
                value = value.get("items") or value.get("data")
            if isinstance(value, list):
                items = value
                break
    normalized = []
    for item in items:
        if isinstance(item, Mapping):
            normalized.append(item)
        elif is_dataclass(item):
            normalized.append(asdict(item))
        elif hasattr(item, "__dict__"):
            normalized.append(vars(item))
    return normalized


def _normalize_tracklist(payload: Any, provider: str) -> Tuple[TracklistTrack, ...]:
    tracks = []
    for item in _track_items(payload):
        track = TracklistTrack.from_item(item, provider=provider)
        if track is not None:
            tracks.append(track)
    return tuple(tracks)


def _track_art_url(details: Mapping[str, Any]) -> Optional[str]:
    raw = details.get("raw_data") or details.get("_raw_data") or details
    if not isinstance(raw, Mapping):
        return None
    album = raw.get("album")
    if isinstance(album, Mapping):
        images = album.get("images")
        if isinstance(images, list):
            for image in images:
                if isinstance(image, Mapping) and _optional_text(image.get("url")):
                    return _optional_text(image.get("url"))
        for key in ("cover_xl", "cover_big", "cover_medium", "image_url"):
            if _optional_text(album.get(key)):
                return _optional_text(album.get(key))
    for key in (
        "artworkUrl100", "artworkUrl60", "artworkUrl30", "cover_xl",
        "cover_big", "image_url", "artwork_url",
    ):
        art = _optional_text(raw.get(key))
        if art:
            for small in ("100x100bb", "60x60bb", "30x30bb"):
                art = art.replace(small, "600x600bb")
            return art
    return None


def _mapping_payload(payload: Any) -> Optional[Dict[str, Any]]:
    if isinstance(payload, Mapping):
        normalized = dict(payload)
    elif is_dataclass(payload):
        normalized = asdict(payload)
    elif hasattr(payload, "__dict__"):
        normalized = dict(vars(payload))
    else:
        return None
    raw = normalized.get("raw_data") or normalized.get("_raw_data")
    if isinstance(raw, Mapping):
        return {**raw, **normalized}
    return normalized


def _first(payload: Mapping[str, Any], *keys: str) -> Any:
    for key in keys:
        if payload.get(key) not in (None, ""):
            return payload[key]
    return None


def _image_url(payload: Mapping[str, Any]) -> Optional[str]:
    for key in (
        "image_url", "artwork_url", "artworkUrl100", "cover_xl",
        "cover_big", "cover_medium", "picture_xl", "picture_big",
        "strArtistThumb", "strAlbumThumb",
    ):
        url = _optional_text(payload.get(key))
        if url:
            for small in ("100x100bb", "60x60bb", "30x30bb"):
                url = url.replace(small, "600x600bb")
            return url
    images = payload.get("images")
    if isinstance(images, list):
        for image in images:
            if isinstance(image, Mapping):
                url = _optional_text(image.get("url"))
                if url:
                    return url
    return _track_art_url(payload)


def _string_values(value: Any) -> Optional[Tuple[str, ...]]:
    if isinstance(value, Mapping):
        value = value.get("data") or value.get("items") or value.get("values")
    if isinstance(value, str):
        values = value.split(",")
    elif isinstance(value, (list, tuple, set)):
        values = value
    else:
        return None
    result = tuple(
        text for item in values if (text := _optional_text(
            item.get("name") if isinstance(item, Mapping) else item
        ))
    )
    return result or None


def _optional_bool(value: Any) -> Optional[bool]:
    if value is None or value == "":
        return None
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"true", "yes", "1", "explicit"}:
            return True
        if normalized in {"false", "no", "0", "clean", "notexplicit"}:
            return False
        return None
    return bool(value)


def _optional_float(value: Any) -> Optional[float]:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _descriptive_getter(client: Any, entity_type: str) -> Optional[Any]:
    names = {
        "artist": ("get_artist", "get_artist_details", "get_artist_metadata"),
        "album": ("get_album_metadata", "get_album", "get_album_details"),
        "track": ("get_track_details", "get_track", "get_track_metadata"),
    }[entity_type]
    return next(
        (getter for name in names if callable(getter := getattr(client, name, None))),
        None,
    )


def _call_descriptive_getter(getter: Any, entity_type: str, provider_id: str) -> Any:
    try:
        return getter(provider_id, allow_fallback=False)
    except TypeError:
        if entity_type == "album":
            try:
                return getter(provider_id, include_tracks=False)
            except TypeError:
                pass
        return getter(provider_id)


def fetch_descriptive_metadata(
    entity_type: str,
    source_ids: Mapping[str, str],
    *,
    source_order: Optional[Tuple[str, ...]] = None,
    clients: Optional[Mapping[str, Any]] = None,
) -> Optional[DescriptiveMetadataProviderResult]:
    """Fetch normalized metadata without crossing provider-id namespaces."""

    from core.metadata.registry import get_client_for_source

    canonical = str(entity_type or "").strip().lower()
    if canonical not in {"artist", "album", "track"}:
        raise ValueError("entity_type must be artist, album, or track")
    normalized_ids = _normalized_source_ids(source_ids)
    order = list(source_order or _configured_source_order())
    order.extend(sorted(set(normalized_ids) - set(order)))
    for provider in order:
        provider_id = normalized_ids.get(provider)
        if not provider_id:
            continue
        try:
            client = (clients or {}).get(provider) or get_client_for_source(provider)
            getter = _descriptive_getter(client, canonical) if client else None
            if getter is None:
                continue
            payload = _mapping_payload(
                _call_descriptive_getter(getter, canonical, provider_id)
            )
            if payload is None:
                continue
            release_date = _optional_text(_first(
                payload, "release_date", "releaseDate", "date", "released",
            ))
            year = _optional_nonnegative_int(_first(
                payload, "year", "release_year", "releaseYear",
            ))
            if year is None and release_date:
                year = _optional_nonnegative_int(release_date[:4])
            duration = _optional_nonnegative_int(_first(
                payload, "duration_ms", "trackTimeMillis", "durationMillis",
            ))
            if duration is None and provider == "deezer":
                seconds = _optional_nonnegative_int(payload.get("duration"))
                duration = seconds * 1000 if seconds is not None else None
            result = DescriptiveMetadataProviderResult(
                provider=provider,
                provider_entity_id=provider_id,
                image_url=_image_url(payload),
                genres=_string_values(_first(
                    payload, "genres", "genre", "primaryGenreName", "strGenre", "tags",
                )),
                summary=_optional_text(_first(
                    payload, "summary", "bio", "biography", "description",
                    "strBiographyEN",
                )),
                year=year,
                release_date=release_date,
                label=_optional_text(_first(
                    payload, "label", "label_name", "recordLabel", "strLabel",
                )),
                upc=_optional_text(_first(payload, "upc", "barcode")),
                style=_optional_text(_first(payload, "style", "strStyle")),
                mood=_optional_text(_first(payload, "mood", "strMood")),
                explicit=_optional_bool(_first(
                    payload, "explicit", "explicit_lyrics", "trackExplicitness",
                )),
                duration_ms=duration,
                bpm=_optional_float(_first(payload, "bpm", "tempo")),
                lyrics=_optional_text(_first(
                    payload, "genius_lyrics", "lyrics", "strTrackLyrics",
                )),
                copyright=_optional_text(_first(
                    payload, "copyright", "copyright_text", "copyrightNotice",
                )),
                banner_url=_optional_text(_first(
                    payload, "banner_url", "banner", "strArtistBanner",
                )),
            )
            if any(
                value is not None
                for name, value in vars(result).items()
                if name not in {"provider", "provider_entity_id"}
            ):
                return result
        except Exception as exc:  # noqa: BLE001
            logger.debug(
                "%s %s lookup failed (%s): %s",
                provider, canonical, provider_id, exc,
            )
    return None


def fetch_track_metadata(
    source_track_ids: Mapping[str, str],
    *,
    source_order: Optional[Tuple[str, ...]] = None,
    clients: Optional[Mapping[str, Any]] = None,
) -> Optional[TrackMetadataProviderResult]:
    """Fetch duration/artwork using every explicitly matched provider in order.

    The returned provider is the one that actually answered. A failed Spotify
    attempt followed by a Deezer/iTunes answer therefore never records or
    reports the fallback value as Spotify metadata.
    """

    from core.metadata.registry import get_client_for_source

    source_ids = _normalized_source_ids(source_track_ids)
    order = list(source_order or _configured_source_order())
    order.extend(sorted(set(source_ids) - set(order)))
    for provider in order:
        provider_id = source_ids.get(provider)
        if not provider_id:
            continue
        try:
            # Repair jobs already carry initialized Spotify/iTunes clients in
            # their context. Prefer those exact provider clients when supplied;
            # every other provider still comes from the shared registry.
            client = (clients or {}).get(provider)
            if client is None:
                client = get_client_for_source(provider)
            getter = getattr(client, "get_track_details", None) if client else None
            if not callable(getter):
                continue
            try:
                details = getter(provider_id, allow_fallback=False)
            except TypeError:
                details = getter(provider_id)
            if not isinstance(details, Mapping):
                continue
            duration = _optional_nonnegative_int(
                details.get("duration_ms") or details.get("trackTimeMillis")
            )
            if duration is None and provider == "deezer":
                seconds = _optional_nonnegative_int(details.get("duration"))
                duration = seconds * 1000 if seconds is not None else None
            if duration is None:
                continue
            return TrackMetadataProviderResult(
                provider=provider,
                provider_entity_id=provider_id,
                duration_ms=duration,
                image_url=_track_art_url(details),
            )
        except Exception as exc:  # noqa: BLE001
            logger.debug("%s track lookup failed (%s): %s", provider, provider_id, exc)
    return None


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


def _fetch_direct_album_tracklist(
    provider: str,
    provider_id: str,
) -> Optional[TracklistProviderResult]:
    """Fetch one exact, already-matched release without any name fallback."""
    from core.metadata.registry import get_client_for_source

    try:
        client = get_client_for_source(provider)
        getter = getattr(client, "get_album_tracks", None) if client else None
        if not callable(getter):
            return None
        try:
            parameters = signature(getter).parameters.values()
            accepts_no_fallback = any(
                parameter.kind is Parameter.VAR_KEYWORD
                or (
                    parameter.name == "allow_fallback"
                    and parameter.kind
                    in {Parameter.POSITIONAL_OR_KEYWORD, Parameter.KEYWORD_ONLY}
                )
                for parameter in parameters
            )
        except (TypeError, ValueError):
            if provider == "spotify":
                logger.debug(
                    "Cannot verify no-fallback support for exact Spotify "
                    "tracklist lookup (%s)",
                    provider_id,
                )
                return None
            accepts_no_fallback = False
        if accepts_no_fallback:
            payload = getter(provider_id, allow_fallback=False)
        else:
            payload = getter(provider_id)
        tracks = _normalize_tracklist(payload, provider)
        if tracks:
            return TracklistProviderResult(provider, provider_id, tracks)
    except Exception as exc:  # noqa: BLE001
        logger.debug(
            "%s tracklist lookup failed (%s): %s", provider, provider_id, exc,
        )
    return None


def fetch_matched_album_tracklists(
    source_album_ids: Mapping[str, str],
    *,
    source_order: Optional[Tuple[str, ...]] = None,
) -> Tuple[TracklistProviderResult, ...]:
    """Fetch tracklists for every explicitly matched release provider.

    Unlike :func:`fetch_album_tracklist`, this never searches by title and
    does not stop after the first successful provider. It is the conservative
    identity-reconcile boundary: all requested IDs already belong to the same
    local release, and each returned track ID keeps its provider namespace.
    """
    source_ids = _normalized_source_ids(source_album_ids)
    configured = (
        tuple(source_order) if source_order is not None else _configured_source_order()
    )
    order = _provider_query_order(configured, source_ids)
    results = []
    for provider in order:
        provider_id = source_ids.get(provider)
        if not provider_id or provider in {"upc", "barcode", "isrc"}:
            continue
        result = _fetch_direct_album_tracklist(provider, provider_id)
        if result is not None:
            results.append(result)
    return tuple(results)


def fetch_album_tracklist(
    album_title: str,
    artist_name: str,
    *,
    source_album_ids: Optional[Mapping[str, str]] = None,
    release_date: Optional[str] = None,
    expected_track_count: Optional[int] = None,
) -> Optional[TracklistProviderResult]:
    """Resolve a canonical tracklist from every explicitly matched provider.

    Direct IDs are authoritative and tried in metadata priority order.  The
    returned track IDs keep the provider that actually supplied the list; no
    numeric/string ID is ever re-labelled as Spotify.  A conservative Deezer
    name search remains the last fallback for older rows without release IDs.
    """
    from core.metadata.registry import get_deezer_client

    source_ids = _normalized_source_ids(source_album_ids)
    order = _provider_query_order(_configured_source_order(), source_ids)
    for provider in order:
        provider_id = source_ids.get(provider)
        if not provider_id or provider in {"upc", "barcode", "isrc"}:
            continue
        result = _fetch_direct_album_tracklist(provider, provider_id)
        if result is not None:
            return result

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
    deadline: Optional[float] = None,
    release_group_id: Optional[str] = None,
) -> Optional[ArtworkProviderResult]:
    """Resolve artwork through existing engines and return one typed result.

    ``release_group_id`` is the MusicBrainz release GROUP, which is a separate
    entity from the release in ``source_ids['musicbrainz']`` and has its own
    Cover Art Archive endpoint. It is passed separately rather than as another
    entry in ``source_ids`` because that mapping is provider-keyed: two
    different MusicBrainz entities cannot share the ``musicbrainz`` key without
    one of them ending up addressed under the other's URL. Group art covers
    every edition, so it is the right fallback when no release id is known.

    Library v2 supplies normalized catalog facts; provider-specific response
    dictionaries stay inside ``core.metadata``. No image bytes or signed
    provider payloads are persisted by this adapter.

    ``deadline`` is an optional ``time.monotonic()`` stamp after which no
    *further* provider is queried (dd28-04).  The walk is sequential over every
    configured source, so without a budget one slow provider could hold a
    caller — and, through it, the per-entity artwork build lock — for minutes.
    The check sits between attempts, so the bound is the deadline plus at most
    one in-flight provider call.
    """
    kind = str(kind or "").strip().lower()
    if kind not in {"artist", "album"}:
        raise ValueError("artwork kind must be artist or album")
    normalized_ids = _normalized_source_ids(source_ids)
    artist_name = str(artist_name or "").strip()

    def _out_of_budget() -> bool:
        if deadline is None:
            return False
        if time.monotonic() < deadline:
            return False
        logger.debug("artwork provider walk hit its budget (%s)", kind)
        return True

    if kind == "artist":
        from core.metadata.artist_image import get_artist_image_url
        preferred = list(source_order or _configured_source_order())
        preferred.extend(sorted(set(normalized_ids) - set(preferred)))
        for source in preferred:
            provider_id = normalized_ids.get(source)
            if not provider_id:
                continue
            if _out_of_budget():
                return None
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
    # Prefer exact release IDs before any title search.  This is both faster
    # and prevents a Deezer/iTunes/Discogs ID from being silently ignored while
    # a different provider's fuzzy top result wins.
    from core.metadata.registry import get_client_for_source

    direct_order = _provider_query_order(
        tuple(source_order or _configured_source_order()), normalized_ids,
    )
    for source in direct_order:
        provider_id = normalized_ids.get(source)
        if not provider_id or source in {"upc", "barcode", "isrc"}:
            continue
        if _out_of_budget():
            return None
        if source == "musicbrainz":
            return ArtworkProviderResult(
                kind="album",
                source="caa",
                provider_entity_id=provider_id,
                url=f"https://coverartarchive.org/release/{provider_id}/front-1200",
            )
        try:
            client = get_client_for_source(source)
            getter = getattr(client, "get_album", None) if client else None
            if not callable(getter) and client:
                # Deezer names its exact album endpoint ``get_album_metadata``;
                # treating only ``get_album`` as valid would silently discard
                # a confirmed Deezer release identity and fall back to search.
                getter = getattr(client, "get_album_metadata", None)
            if not callable(getter):
                continue
            try:
                payload = getter(provider_id, include_tracks=False)
            except TypeError:
                try:
                    payload = getter(provider_id, allow_fallback=False)
                except TypeError:
                    payload = getter(provider_id)
            if not isinstance(payload, Mapping):
                continue
            images = payload.get("images")
            url = None
            if isinstance(images, list):
                for item in images:
                    if isinstance(item, Mapping) and _optional_text(item.get("url")):
                        url = _optional_text(item.get("url"))
                        break
            if not url:
                for key in (
                    "image_url", "cover_xl", "cover_big", "artworkUrl100", "image",
                ):
                    url = _optional_text(payload.get(key))
                    if url:
                        break
            if url:
                for small in ("100x100bb", "60x60bb", "30x30bb"):
                    url = url.replace(small, "1200x1200bb")
                return ArtworkProviderResult(
                    kind="album",
                    source=source,
                    provider_entity_id=provider_id,
                    url=url,
                )
        except Exception as exc:  # noqa: BLE001
            logger.debug("%s direct artwork lookup failed: %s", source, exc)
    # No exact release id answered. A MusicBrainz release GROUP is still an
    # exact statement about THIS record — and its Cover Art Archive entry
    # covers every edition, so it hits more often than a per-release one —
    # whereas everything below here is a title search, i.e. a guess about
    # which record is meant. Its endpoint is /release-group/, not /release/:
    # a group id requested under the release path is a 404, which is what the
    # discography rows that stored a group id as their release id all got.
    group_id = _optional_text(release_group_id)
    if group_id:
        if _out_of_budget():
            return None
        return ArtworkProviderResult(
            kind="album",
            source="caa",
            provider_entity_id=group_id,
            url=f"https://coverartarchive.org/release-group/{group_id}/front-1200",
        )
    from core.metadata.art_lookup import (
        available_art_sources,
        select_preferred_art,
    )
    if _out_of_budget():
        return None
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
    "DescriptiveMetadataProviderResult",
    "DiscographyProviderResult",
    "DiscographyRelease",
    "TracklistProviderResult",
    "TracklistTrack",
    "TrackMetadataProviderResult",
    "fetch_album_tracklist",
    "fetch_matched_album_tracklists",
    "fetch_artwork_url",
    "fetch_artist_discography",
    "fetch_descriptive_metadata",
    "fetch_track_metadata",
]
