"""Resolve a YouTube Music album to its tracks, for the album-bundle flow.

The per-track YouTube path searches the catalogue for songs. Some albums list
every track as unavailable there (``isAvailable: False``, no ``videoId``): the
songs never show up in a song search, so no track-by-track query finds them.
The album's own playlist is a different matter, yt-dlp reads it fine, and
YouTube Music links it from the album page. So the album flow goes
album search -> playlist id -> playlist entries, which does not care how the
songs are indexed.

Everything here is a plain function over injectable clients so it can be tested
without the network. Nothing raises: a failure at any step means "this source
has no answer for this album", and the caller falls back to the per-track flow.
"""

from __future__ import annotations

import json
import re
import unicodedata
from difflib import SequenceMatcher
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence, Tuple

from utils.logging_config import get_logger

logger = get_logger("youtube_album")

# Album titles must agree this closely once normalised. High on purpose: the
# whole point is to NOT fetch a different album that merely shares words.
_TITLE_MATCH_FLOOR = 0.92

# The playlist has to hold roughly what was asked for. Only a floor: a
# playlist with more than was requested (a deluxe edition, a hidden bonus
# track) is still the right album, one with far less is a different release.
_DURATION_FLOOR = 0.8

_PLAYLIST_ID = re.compile(r"OLAK5uy_[A-Za-z0-9_-]{30,40}")


def _norm(text: Any) -> str:
    """Lowercase, fold accents, turn punctuation into spaces, squeeze spaces."""
    if not text:
        return ""
    folded = "".join(
        ch for ch in unicodedata.normalize("NFKD", str(text)) if not unicodedata.combining(ch)
    )
    return " ".join(re.sub(r"[^a-z0-9]+", " ", folded.lower()).split())


def title_matches(wanted: Any, candidate: Any) -> bool:
    """Same album title, ignoring case, accents and punctuation."""
    left, right = _norm(wanted), _norm(candidate)
    if not left or not right:
        return False
    return left == right or SequenceMatcher(None, left, right).ratio() >= _TITLE_MATCH_FLOOR


def artist_matches(wanted: Any, credited: Sequence[Any]) -> bool:
    """The wanted artist is one of the album's credited artists."""
    target = _norm(wanted)
    if not target:
        return False
    for name in credited or []:
        if isinstance(name, Mapping):
            name = name.get("name")
        if _norm(name) == target:
            return True
    return False


def find_album(
    artist: str,
    album: str,
    ytmusic: Any,
) -> Optional[Dict[str, Any]]:
    """The album search hit whose artist AND title both agree, or None.

    ``ytmusic`` is a ``ytmusicapi.YTMusic``. Artist and title are both
    required, with no "best of a bad lot" fallback: a wrong album downloaded
    whole is the expensive mistake here.
    """
    if not artist or not album:
        return None
    try:
        hits = ytmusic.search(f"{artist} {album}", filter="albums", limit=10) or []
    except Exception as exc:  # noqa: BLE001 - any failure means "no answer"
        logger.info("YouTube Music album search failed for %r: %s", album, exc)
        return None
    for hit in hits:
        if not isinstance(hit, Mapping) or not hit.get("browseId"):
            continue
        if title_matches(album, hit.get("title")) and artist_matches(artist, hit.get("artists")):
            return {
                "browse_id": hit["browseId"],
                "title": hit.get("title") or album,
                "artists": [a.get("name") for a in hit.get("artists") or [] if isinstance(a, Mapping)],
            }
    return None


def album_playlist_id(browse_id: str, ytmusic: Any) -> Optional[str]:
    """The ``OLAK5uy_`` playlist behind an album page.

    ``get_album`` exposes it as ``audioPlaylistId``, but leaves it empty for
    albums whose songs the catalogue marks unavailable, which is the case this
    flow exists for. The album page's canonical URL still names it, so read the
    raw page as the second try.
    """
    if not browse_id:
        return None
    try:
        playlist = (ytmusic.get_album(browse_id) or {}).get("audioPlaylistId")
        if playlist:
            return str(playlist)
    except Exception as exc:  # noqa: BLE001
        logger.debug("get_album(%s) failed: %s", browse_id, exc)
    try:
        raw = ytmusic._send_request("browse", {"browseId": browse_id})  # noqa: SLF001
    except Exception as exc:  # noqa: BLE001 - private API; treat any change as "unknown"
        logger.debug("raw album page %s failed: %s", browse_id, exc)
        return None
    canonical = (
        ((raw or {}).get("microformat") or {}).get("microformatDataRenderer") or {}
    ).get("urlCanonical") or ""
    match = _PLAYLIST_ID.search(canonical) or _PLAYLIST_ID.search(json.dumps(raw or {}))
    return match.group(0) if match else None


def playlist_entries(
    playlist_id: str,
    cookie_opts: Optional[Mapping[str, Any]] = None,
    extractor: Optional[Callable[[str, Dict[str, Any]], Any]] = None,
) -> List[Dict[str, Any]]:
    """The playlist's tracks as ``{id, title, duration}``, in album order."""
    if not playlist_id:
        return []
    options: Dict[str, Any] = {
        "quiet": True, "no_warnings": True, "extract_flat": True, "skip_download": True,
    }
    options.update(cookie_opts or {})
    url = f"https://music.youtube.com/playlist?list={playlist_id}"
    try:
        if extractor is None:
            import yt_dlp

            def extractor(target, opts):  # noqa: E306
                with yt_dlp.YoutubeDL(opts) as ydl:
                    return ydl.extract_info(target, download=False)
        info = extractor(url, options) or {}
    except Exception as exc:  # noqa: BLE001
        logger.info("Could not read YouTube playlist %s: %s", playlist_id, exc)
        return []
    entries = []
    for entry in info.get("entries") or []:
        if not isinstance(entry, Mapping) or not entry.get("id"):
            continue
        entries.append({
            "id": str(entry["id"]),
            "title": str(entry.get("title") or "").strip(),
            "duration": entry.get("duration"),
        })
    return entries


def check_entries(
    entries: Sequence[Mapping[str, Any]],
    expected_duration_seconds: Optional[float] = None,
) -> Tuple[bool, str]:
    """Whether this playlist can stand in for the album that was asked for."""
    if not entries:
        return False, "The YouTube Music album has no playable tracks"
    if expected_duration_seconds:
        durations = [e.get("duration") for e in entries]
        # A missing duration makes the total unknown, and an unknown total
        # proves nothing, so it neither passes nor fails the check.
        if all(isinstance(d, (int, float)) and d > 0 for d in durations):
            total = float(sum(durations))
            if total < float(expected_duration_seconds) * _DURATION_FLOOR:
                return False, (
                    f"YouTube Music album is {total / 60:.0f} min, "
                    f"{float(expected_duration_seconds) / 60:.0f} min were expected"
                )
    return True, ""
