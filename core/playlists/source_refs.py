"""Helpers for mirrored-playlist upstream source references.

Mirrored playlist rows have two legacy fields:
- ``source_playlist_id``: the stable lookup key used for uniqueness.
- ``description``: for URL-backed mirrors, the original/canonical URL.

Keeping the normalization here prevents the refresh worker, API endpoint,
and UI repair flow from each inventing a slightly different meaning.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import Mapping, Optional
from urllib.parse import parse_qs, urlparse


_SPOTIFY_ID_RE = re.compile(r"^[A-Za-z0-9]{16,32}$")


@dataclass(frozen=True)
class MirroredSourceRef:
    source_playlist_id: str
    description: Optional[str]


@dataclass(frozen=True)
class MirroredSourceRefView:
    source_ref: str
    source_ref_kind: str
    source_ref_status: str
    source_ref_error: Optional[str] = None


def normalize_mirrored_source_ref(
    source: str,
    source_ref: str,
    existing_description: str = "",
) -> MirroredSourceRef:
    """Normalize a user-provided source URL/ID for storage.

    URL-backed sources keep a deterministic hash in ``source_playlist_id`` and
    store the canonical URL in ``description``. Direct-ID sources store the ID
    directly and preserve the existing description unless a source-specific URL
    parser says otherwise.
    """
    source = (source or "").strip().lower()
    source_ref = (source_ref or "").strip()
    existing_description = (existing_description or "").strip()

    if not source_ref:
        raise ValueError("Source link or ID is required")

    if source == "spotify_public":
        canonical_url = _canonical_spotify_url(source_ref)
        return MirroredSourceRef(_short_hash(canonical_url), canonical_url)

    if source == "youtube":
        canonical_url = _canonical_youtube_url(source_ref)
        return MirroredSourceRef(_short_hash(canonical_url), canonical_url)

    if source == "deezer" and source_ref.startswith(("http://", "https://")):
        from core.deezer_client import DeezerClient

        parsed_id = DeezerClient.parse_playlist_url(source_ref)
        if not parsed_id:
            raise ValueError("Use a valid Deezer playlist URL or playlist ID")
        return MirroredSourceRef(str(parsed_id), existing_description or None)

    return MirroredSourceRef(source_ref, existing_description or None)


def require_refresh_url(source: str, description: str, playlist_name: str = "") -> str:
    """Return a URL required by hash-backed refresh sources, or raise clearly."""
    source = (source or "").strip().lower()
    description = (description or "").strip()
    if source in {"spotify_public", "youtube"}:
        if not description.startswith(("http://", "https://")):
            label = f" '{playlist_name}'" if playlist_name else ""
            raise ValueError(f"{source} mirror{label} is missing its original source URL")
    return description


def describe_mirrored_source_ref(playlist: Mapping[str, object]) -> MirroredSourceRefView:
    """Build a UI/API friendly view of a mirrored playlist's refresh ref."""
    source = str(playlist.get("source") or "").strip().lower()
    source_playlist_id = str(playlist.get("source_playlist_id") or "").strip()
    description = str(playlist.get("description") or "").strip()
    name = str(playlist.get("name") or "")

    if source in {"spotify_public", "youtube"}:
        if description.startswith(("http://", "https://")):
            return MirroredSourceRefView(description, "url", "ok")
        try:
            require_refresh_url(source, description, name)
        except ValueError as exc:
            return MirroredSourceRefView(
                source_playlist_id,
                "url",
                "missing",
                str(exc),
            )

    return MirroredSourceRefView(source_playlist_id, "id", "ok" if source_playlist_id else "missing")


def _canonical_spotify_url(source_ref: str) -> str:
    parsed = _parse_spotify_ref(source_ref)
    if parsed:
        return f"https://open.spotify.com/{parsed['type']}/{parsed['id']}"

    # Repair flow convenience: if the user pastes only a Spotify ID, assume
    # playlist. Album URLs still need their URL/URI so the type is explicit.
    if _SPOTIFY_ID_RE.match(source_ref):
        return f"https://open.spotify.com/playlist/{source_ref}"

    raise ValueError("Use a valid open.spotify.com playlist/album URL, Spotify URI, or playlist ID")


def _parse_spotify_ref(source_ref: str) -> Optional[dict]:
    uri_match = re.match(r"spotify:(playlist|album):([A-Za-z0-9]+)", source_ref)
    if uri_match:
        return {"type": uri_match.group(1), "id": uri_match.group(2)}

    url_match = re.search(
        r"https?://open\.spotify\.com/(?:embed/)?(playlist|album)/([A-Za-z0-9]+)",
        source_ref,
    )
    if url_match:
        return {"type": url_match.group(1), "id": url_match.group(2)}

    return None


def _canonical_youtube_url(source_ref: str) -> str:
    parsed_url = urlparse(source_ref)
    playlist_id = ""

    if parsed_url.scheme and parsed_url.netloc:
        host = parsed_url.netloc.lower()
        if not ("youtube.com" in host or "music.youtube.com" in host):
            raise ValueError("Use a valid YouTube playlist URL")
        playlist_id = parse_qs(parsed_url.query).get("list", [""])[0]
    else:
        playlist_id = source_ref

    if not playlist_id:
        raise ValueError("YouTube playlist URL must include a list= playlist id")

    return f"https://youtube.com/playlist?list={playlist_id}"


def _short_hash(value: str) -> str:
    return hashlib.md5(value.encode()).hexdigest()[:12]
