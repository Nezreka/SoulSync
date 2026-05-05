"""Normalization helpers for provider payloads."""

from __future__ import annotations

import re
from typing import Any, Iterable, Optional

from core.metadata.models import MetadataAlbum, MetadataArtist, MetadataTrack


def _clean_itunes_album_name(album_name: str) -> str:
    if not album_name:
        return album_name

    for suffix in (" - Single", " - EP"):
        if album_name.endswith(suffix):
            return album_name[: -len(suffix)]
    return album_name


def _as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, (str, bytes)):
        return [value]
    if isinstance(value, dict):
        return [value]
    try:
        return list(value)
    except TypeError:
        return [value]


def _extract_image_url(raw: Any, *keys: str) -> Optional[str]:
    for key in keys:
        if isinstance(raw, dict) and raw.get(key):
            return raw.get(key)
    return None


def _pick_first_image(images: Any) -> Optional[str]:
    images = _as_list(images)
    if not images:
        return None
    first = images[0]
    if isinstance(first, dict):
        return first.get("url") or first.get("uri")
    return None


def _first_non_empty(*values: Any) -> Any:
    for value in values:
        if value not in (None, "", [], {}):
            return value
    return None


def _normalize_external_urls(raw: Any, provider: str, fallback_key: str = "link") -> dict[str, str]:
    if isinstance(raw, dict):
        external_urls = raw.get("external_urls")
        if isinstance(external_urls, dict) and external_urls:
            return dict(external_urls)
        url = raw.get(fallback_key)
        if url:
            return {provider: str(url)}
    return {}


def _normalize_artists(raw_artists: Any) -> list[str]:
    artists: list[str] = []
    for artist in _as_list(raw_artists):
        if isinstance(artist, dict):
            name = artist.get("name") or artist.get("artist_name") or artist.get("title")
        else:
            name = artist
        if name:
            artists.append(str(name))
    return artists or ["Unknown Artist"]


def _infer_album_type_from_track_count(track_count: int) -> str:
    if track_count <= 3:
        return "single"
    if track_count <= 6:
        return "ep"
    return "album"


def _normalize_itunes_album_type(collection_type: str, track_count: int) -> str:
    collection_type = (collection_type or "").lower()
    if "compilation" in collection_type:
        return "compilation"
    if track_count <= 3:
        return "single"
    if track_count <= 6:
        return "ep"
    return "album"


def _normalize_discogs_album_type(raw: dict[str, Any]) -> str:
    formats = raw.get("formats", []) or []
    format_name = str(formats[0].get("name", "")).lower() if formats else ""
    descriptions = [str(desc).lower() for desc in (formats[0].get("descriptions", []) if formats else [])]
    raw_format = raw.get("format") or ""
    if isinstance(raw_format, list):
        format_str = ", ".join(raw_format).lower()
    else:
        format_str = str(raw_format).lower()

    if "single" in descriptions or "single" in format_name or "single" in format_str:
        return "single"
    if "ep" in descriptions or ", ep" in format_str or format_str.endswith("ep"):
        return "ep"
    if "compilation" in descriptions or "compilation" in format_str:
        return "compilation"
    return "album"


def normalize_spotify_artist(raw: dict[str, Any]) -> MetadataArtist:
    images = _as_list(raw.get("images"))
    image_url = _pick_first_image(images)
    return MetadataArtist(
        id=str(raw.get("id", "")),
        name=str(raw.get("name", "")),
        popularity=int(raw.get("popularity", 0) or 0),
        genres=list(raw.get("genres") or []),
        followers=int(_first_non_empty((raw.get("followers") or {}).get("total"), 0) or 0),
        image_url=image_url,
        external_urls=dict(raw.get("external_urls") or {}),
        source="spotify",
        source_id=str(raw.get("id", "")),
        raw_data=raw,
    )


def normalize_spotify_album(raw: dict[str, Any]) -> MetadataAlbum:
    images = _as_list(raw.get("images"))
    image_url = _pick_first_image(images)
    artists = _normalize_artists(raw.get("artists"))
    return MetadataAlbum(
        id=str(raw.get("id", "")),
        name=str(raw.get("name", "")),
        artists=artists,
        release_date=str(raw.get("release_date", "") or ""),
        total_tracks=int(raw.get("total_tracks", 0) or 0),
        album_type=str(raw.get("album_type", "album") or "album"),
        image_url=image_url,
        external_urls=dict(raw.get("external_urls") or {}),
        source="spotify",
        source_id=str(raw.get("id", "")),
        raw_data=raw,
    )


def normalize_spotify_track(raw: dict[str, Any]) -> MetadataTrack:
    album = raw.get("album") or {}
    album_name = str(album.get("name", "") or "")
    artists = _normalize_artists(raw.get("artists"))
    image_url = _pick_first_image(album.get("images"))
    return MetadataTrack(
        id=str(raw.get("id", "")),
        name=str(raw.get("name", "")),
        artists=artists,
        album=album_name,
        duration_ms=int(raw.get("duration_ms", 0) or 0),
        popularity=int(raw.get("popularity", 0) or 0),
        preview_url=raw.get("preview_url"),
        external_urls=dict(raw.get("external_urls") or {}),
        image_url=image_url,
        release_date=str(album.get("release_date", "") or raw.get("release_date", "") or ""),
        track_number=raw.get("track_number"),
        disc_number=raw.get("disc_number"),
        album_type=str(album.get("album_type", "") or raw.get("album_type", "") or "") or None,
        total_tracks=int(album.get("total_tracks", 0) or 0) or None,
        source="spotify",
        source_id=str(raw.get("id", "")),
        raw_data=raw,
    )


def normalize_deezer_artist(raw: dict[str, Any]) -> MetadataArtist:
    image_url = _first_non_empty(raw.get("picture_xl"), raw.get("picture_big"), raw.get("picture_medium"))
    return MetadataArtist(
        id=str(raw.get("id", "")),
        name=str(raw.get("name", "")),
        popularity=int(raw.get("rank", 0) or 0),
        genres=[],
        followers=int(raw.get("nb_fan", 0) or 0),
        image_url=image_url,
        external_urls=_normalize_external_urls(raw, "deezer"),
        source="deezer",
        source_id=str(raw.get("id", "")),
        raw_data=raw,
    )


def normalize_deezer_album(raw: dict[str, Any]) -> MetadataAlbum:
    image_url = _first_non_empty(raw.get("cover_xl"), raw.get("cover_big"), raw.get("cover_medium"))
    artist = raw.get("artist") or {}
    artists = _normalize_artists([artist] if artist else [])
    record_type = str(raw.get("record_type", "album") or "album").lower()
    if record_type == "single":
        album_type = "single"
    elif record_type == "ep":
        album_type = "ep"
    elif record_type == "compile":
        album_type = "compilation"
    else:
        album_type = _infer_album_type_from_track_count(int(raw.get("nb_tracks", 0) or 0))
    return MetadataAlbum(
        id=str(raw.get("id", "")),
        name=str(raw.get("title", "")),
        artists=artists,
        release_date=str(raw.get("release_date", "") or ""),
        total_tracks=int(raw.get("nb_tracks", 0) or 0),
        album_type=album_type,
        image_url=image_url,
        external_urls=_normalize_external_urls(raw, "deezer"),
        source="deezer",
        source_id=str(raw.get("id", "")),
        raw_data=raw,
    )


def normalize_deezer_track(raw: dict[str, Any]) -> MetadataTrack:
    album = raw.get("album") or {}
    artist = raw.get("artist") or {}
    contributors = raw.get("contributors") or []
    artists = _normalize_artists(contributors if len(_as_list(contributors)) > 1 else [artist] if artist else [])
    image_url = _first_non_empty(album.get("cover_xl"), album.get("cover_big"), album.get("cover_medium"))
    album_type = raw.get("type") or album.get("type")
    if not album_type:
        nb_tracks = int(album.get("nb_tracks", 0) or 0)
        album_type = _infer_album_type_from_track_count(nb_tracks)
    return MetadataTrack(
        id=str(raw.get("id", "")),
        name=str(raw.get("title", "")),
        artists=artists,
        album=str(album.get("title", "") or ""),
        duration_ms=int(raw.get("duration", 0) or 0) * 1000,
        popularity=int(raw.get("rank", 0) or 0),
        preview_url=raw.get("preview"),
        external_urls=_normalize_external_urls(raw, "deezer"),
        image_url=image_url,
        release_date=str(raw.get("release_date", "") or album.get("release_date", "") or ""),
        track_number=raw.get("track_position"),
        disc_number=raw.get("disk_number", 1) or 1,
        album_type=str(album_type or "album"),
        total_tracks=int(album.get("nb_tracks", 0) or 0) or None,
        source="deezer",
        source_id=str(raw.get("id", "")),
        raw_data=raw,
    )


def normalize_itunes_artist(raw: dict[str, Any]) -> MetadataArtist:
    image_url = raw.get("artworkUrl100")
    if image_url:
        image_url = image_url.replace("100x100bb", "3000x3000bb")
    return MetadataArtist(
        id=str(raw.get("artistId", "")),
        name=str(raw.get("artistName", "")),
        popularity=0,
        genres=[raw["primaryGenreName"]] if raw.get("primaryGenreName") else [],
        followers=0,
        image_url=image_url,
        external_urls=_normalize_external_urls(raw, "itunes", "artistViewUrl"),
        source="itunes",
        source_id=str(raw.get("artistId", "")),
        raw_data=raw,
    )


def normalize_itunes_album(raw: dict[str, Any]) -> MetadataAlbum:
    artwork = raw.get("artworkUrl100")
    image_url = artwork.replace("100x100bb", "3000x3000bb") if artwork else None
    track_count = int(raw.get("trackCount", 0) or 0)
    return MetadataAlbum(
        id=str(raw.get("collectionId", "")),
        name=_clean_itunes_album_name(str(raw.get("collectionName", "") or "")),
        artists=[str(raw.get("artistName", "Unknown Artist") or "Unknown Artist")],
        release_date=str(raw.get("releaseDate", "") or ""),
        total_tracks=track_count,
        album_type=_normalize_itunes_album_type(str(raw.get("collectionType", "") or ""), track_count),
        image_url=image_url,
        external_urls=_normalize_external_urls(raw, "itunes", "collectionViewUrl"),
        source="itunes",
        source_id=str(raw.get("collectionId", "")),
        raw_data=raw,
    )


def normalize_itunes_track(raw: dict[str, Any], *, clean_artist_name: Optional[str] = None) -> MetadataTrack:
    artwork = raw.get("artworkUrl100")
    image_url = artwork.replace("100x100bb", "3000x3000bb") if artwork else None
    track_count = int(raw.get("trackCount", 0) or 0)
    if clean_artist_name:
        artists = [clean_artist_name]
    else:
        artists = [str(raw.get("artistName", "Unknown Artist") or "Unknown Artist")]
    return MetadataTrack(
        id=str(raw.get("trackId", "")),
        name=str(raw.get("trackName", "")),
        artists=artists,
        album=_clean_itunes_album_name(str(raw.get("collectionName", "") or "")),
        duration_ms=int(raw.get("trackTimeMillis", 0) or 0),
        popularity=0,
        preview_url=raw.get("previewUrl"),
        external_urls=_normalize_external_urls(raw, "itunes", "trackViewUrl"),
        image_url=image_url,
        release_date=str(raw.get("releaseDate", "") or "").split("T")[0] if raw.get("releaseDate") else None,
        track_number=raw.get("trackNumber"),
        disc_number=raw.get("discNumber", 1) or 1,
        album_type=_normalize_itunes_album_type(str(raw.get("collectionType", "") or ""), track_count),
        total_tracks=track_count or None,
        source="itunes",
        source_id=str(raw.get("trackId", "")),
        raw_data=raw,
    )


def normalize_discogs_artist(raw: dict[str, Any]) -> MetadataArtist:
    images = _as_list(raw.get("images"))
    image_url = _pick_first_image(images)
    if not image_url:
        image_url = raw.get("cover_image") or raw.get("thumb")
        if image_url and "spacer.gif" in str(image_url):
            image_url = None
    external_urls = {}
    if raw.get("uri"):
        uri = str(raw["uri"])
        external_urls["discogs"] = f"https://www.discogs.com{uri}" if uri.startswith("/") else uri
    elif raw.get("resource_url"):
        external_urls["discogs_api"] = str(raw["resource_url"])
    return MetadataArtist(
        id=str(raw.get("id", "")),
        name=str(raw.get("name", raw.get("title", "")) or ""),
        popularity=0,
        genres=[],
        followers=0,
        image_url=image_url,
        external_urls=external_urls,
        source="discogs",
        source_id=str(raw.get("id", "")),
        raw_data=raw,
    )


def normalize_discogs_album(raw: dict[str, Any]) -> MetadataAlbum:
    title = str(raw.get("title", "") or "")
    artists = []
    if raw.get("artists"):
        artists = [str(a.get("name", "") or "") for a in raw["artists"] if a.get("name")]
    elif raw.get("artist"):
        artists = [str(raw.get("artist") or "")]
    elif " - " in title:
        artists = [title.split(" - ", 1)[0].strip()]
        title = title.split(" - ", 1)[1].strip()
    if not artists:
        artists = ["Unknown Artist"]
    images = _as_list(raw.get("images"))
    image_url = _pick_first_image(images)
    if not image_url:
        image_url = raw.get("cover_image") or raw.get("thumb")
        if image_url and "spacer.gif" in str(image_url):
            image_url = None
    external_urls = {}
    if raw.get("uri"):
        uri = str(raw["uri"])
        external_urls["discogs"] = f"https://www.discogs.com{uri}" if uri.startswith("/") else uri
    elif raw.get("resource_url"):
        external_urls["discogs_api"] = str(raw["resource_url"])
    total_tracks = int(_first_non_empty(len(raw.get("tracklist", []) or []), raw.get("format_quantity", 0), 0) or 0)
    release_date = str(_first_non_empty(raw.get("year"), raw.get("released"), "") or "")
    return MetadataAlbum(
        id=str(raw.get("id", "")),
        name=title,
        artists=artists,
        release_date=release_date,
        total_tracks=total_tracks,
        album_type=_normalize_discogs_album_type(raw),
        image_url=image_url,
        external_urls=external_urls,
        source="discogs",
        source_id=str(raw.get("id", "")),
        raw_data=raw,
    )


def normalize_discogs_track(raw: dict[str, Any], release_raw: Optional[dict[str, Any]] = None) -> MetadataTrack:
    release = release_raw or {}
    position = str(raw.get("position", "") or "")
    track_number = None
    disc_number = 1
    if position:
        if "-" in position and position.replace("-", "").isdigit():
            parts = position.split("-")
            disc_number = int(parts[0])
            track_number = int(parts[1])
        elif position.isdigit():
            track_number = int(position)
        else:
            digits = "".join(c for c in position if c.isdigit())
            if digits:
                track_number = int(digits)

    duration_ms = 0
    dur_str = str(raw.get("duration", "") or "")
    if ":" in dur_str:
        parts = dur_str.split(":")
        try:
            duration_ms = (int(parts[0]) * 60 + int(parts[1])) * 1000
        except (ValueError, IndexError):
            duration_ms = 0

    track_artists = []
    if raw.get("artists"):
        track_artists = [a.get("name", "") for a in raw["artists"] if a.get("name")]
    if not track_artists and release.get("artists"):
        track_artists = [a.get("name", "") for a in release["artists"] if a.get("name")]
    if not track_artists:
        track_artists = ["Unknown Artist"]

    image_url = None
    images = release.get("images", [])
    if images:
        primary = next((img for img in images if img.get("type") == "primary"), None)
        image_url = (primary or images[0]).get("uri")
    external_urls = {}
    if release.get("uri"):
        uri = str(release["uri"])
        external_urls["discogs"] = f"https://www.discogs.com{uri}" if uri.startswith("/") else uri

    return MetadataTrack(
        id=f"{release.get('id', '')}_t{track_number or 0}",
        name=str(raw.get("title", "") or ""),
        artists=track_artists,
        album=str(release.get("title", "") or ""),
        duration_ms=duration_ms,
        popularity=int(_first_non_empty(release.get("community", {}).get("have"), 0) or 0),
        preview_url=None,
        external_urls=external_urls,
        image_url=image_url,
        release_date=str(release.get("year", "") or "") if release.get("year") else None,
        track_number=track_number,
        disc_number=disc_number,
        album_type="album",
        total_tracks=int(len(release.get("tracklist", []) or [])) or None,
        source="discogs",
        source_id=f"{release.get('id', '')}_t{track_number or 0}",
        raw_data=raw,
    )
