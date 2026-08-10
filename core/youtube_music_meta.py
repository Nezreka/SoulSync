"""Read a music.youtube.com playlist from the YouTube Music catalog API.

``parse_youtube_playlist`` gets its data from yt-dlp's *flat* playlist
extraction, which is a video-oriented view: per entry it returns a title, an
id, a duration and a channel. That is enough to guess an artist (see
``core.youtube_track_meta``) but it can never produce an **album**, because
videos don't have one — and the whole downstream pipeline (library matching,
Soulseek search, MusicBrainz release preflight) is album-shaped.

YouTube Music serves the same playlist from a music catalog that already has
those fields. ``ytmusicapi`` speaks that API. Measured on one user's library:

    playlist        yt-dlp flat                 YT Music catalog
    a 69-track      68/69 artists, 0 albums     69/69 artists, 69 albums
    liked songs     210 tracks (page-capped)    1421 tracks, 0 unknown artists

so this is both a metadata and a *completeness* win — the InnerTube paging
ytmusicapi uses isn't subject to the flat-extraction page cap that #908 works
around.

Scope, deliberately narrow:

- **music.youtube.com only.** A youtube.com playlist is a video playlist; it
  has no catalog entry and asking for one would 404. The caller gates on
  ``is_music_youtube_url`` and youtube.com keeps the yt-dlp path untouched.
- **Best-effort.** ytmusicapi is an OPTIONAL dependency and the API can fail
  (private playlist, no cookies, upstream shape change — it raises ``KeyError``
  on a response it can't walk). Every failure returns ``None`` so the caller
  falls back to yt-dlp. The feature can only add data, never remove it.
- **User-generated content still works.** A UGC track in a YT Music playlist
  comes back with the uploader's channel as the artist and no album — exactly
  what the yt-dlp path would have produced, so those entries are no worse.

The projection (``ytmusic_playlist_to_payload``) is a pure function over the
API's response so the field mapping is unit-testable without network.
"""

from __future__ import annotations

from typing import Any, Dict, List, Mapping, Optional
from urllib.parse import parse_qs, urlparse

from core.youtube_track_meta import strip_topic_suffix
from utils.logging_config import get_logger

logger = get_logger("youtube_music_meta")

# YouTube Music's own id for "the signed-in user's Liked Music". It is not a
# real playlist id and never appears in a ``list=`` parameter as anything else.
LIKED_MUSIC_ID = "LM"


def playlist_id_from_url(url: Any) -> str:
    """Extract the ``list=`` playlist id from a YouTube URL, or ``''``.

    ytmusicapi wants the bare id, not the URL.
    """
    if not isinstance(url, str) or not url.strip():
        return ""
    try:
        parsed = urlparse(url.strip())
    except ValueError:
        return ""
    values = parse_qs(parsed.query or "").get("list") or []
    return (values[0] or "").strip() if values else ""


def _artist_names(track: Mapping[str, Any]) -> List[str]:
    """Non-empty artist names, in the API's order (primary first).

    Tracks whose artist has no canonical catalog entry come back with the raw
    auto-generated channel name instead — ``"Example Band - Topic"``. Left alone that
    reaches the mirror verbatim and never matches the ``Example Band`` already in the
    library, so the suffix is stripped with the same helper the yt-dlp path
    uses. Observed on 10 of 1995 tracks across one user's playlists.
    """
    names = []
    for entry in track.get("artists") or []:
        if isinstance(entry, Mapping):
            name = str(entry.get("name") or "").strip()
        else:
            name = str(entry or "").strip()
        name = strip_topic_suffix(name)
        if name and name not in names:
            names.append(name)
    return names


def _album_name(track: Mapping[str, Any]) -> str:
    """Album name, or ``''``. Singles and UGC legitimately have none."""
    album = track.get("album")
    if isinstance(album, Mapping):
        return str(album.get("name") or "").strip()
    return str(album or "").strip()


def ytmusic_playlist_to_payload(
    raw: Optional[Mapping[str, Any]], url: str
) -> Optional[Dict[str, Any]]:
    """Project a ytmusicapi ``get_playlist`` response into the dict shape
    ``parse_youtube_playlist`` returns, so callers can't tell which path ran.

    Pure — no network, no config. Returns ``None`` when the response carries no
    usable track, so the caller can fall back rather than mirror an empty
    playlist over a good one.

    Tracks with neither a title nor a videoId are dropped: they're the API's
    placeholder rows for deleted/region-blocked entries and would land in the
    mirror as empty rows (which ``mirror_playlist`` rejects wholesale).
    """
    if not isinstance(raw, Mapping):
        return None

    tracks: List[Dict[str, Any]] = []
    for track in raw.get("tracks") or []:
        if not isinstance(track, Mapping):
            continue
        title = str(track.get("title") or "").strip()
        video_id = str(track.get("videoId") or "").strip()
        if not title and not video_id:
            continue

        names = _artist_names(track)
        seconds = track.get("duration_seconds")
        try:
            duration_ms = int(seconds) * 1000 if seconds else 0
        except (TypeError, ValueError):
            duration_ms = 0

        tracks.append({
            "id": video_id,
            "name": title or "Unknown Track",
            # Same shape as the yt-dlp path: a list whose FIRST entry is the
            # primary artist. Extra names are kept for callers that want the
            # full credit rather than just artists[0].
            "artists": names or ["Unknown Artist"],
            "album": _album_name(track),
            "duration_ms": duration_ms,
            "raw_title": title,
            "raw_artist": names[0] if names else "",
            # ATV = catalog audio, OMV = official video, UGC = user upload.
            # Passed through so a caller can weight or filter by provenance.
            "video_type": str(track.get("videoType") or ""),
            "url": f"https://www.youtube.com/watch?v={video_id}" if video_id else "",
        })

    if not tracks:
        return None

    thumbnails = raw.get("thumbnails") or []
    image_url = ""
    if thumbnails and isinstance(thumbnails[-1], Mapping):
        image_url = str(thumbnails[-1].get("url") or "")

    return {
        "id": str(raw.get("id") or playlist_id_from_url(url)),
        "name": str(raw.get("title") or "YouTube Music Playlist"),
        "tracks": tracks,
        "track_count": len(tracks),
        "url": url,
        "source": "youtube",
        "image_url": image_url,
    }


def fetch_ytmusic_playlist(
    url: str, auth: Optional[Dict[str, str]] = None
) -> Optional[Dict[str, Any]]:
    """Fetch ``url`` from the YouTube Music catalog, or ``None`` on any failure.

    ``auth`` is a ytmusicapi browser-auth header dict; without it only public
    playlists resolve — notably Liked Music (``list=LM``) needs it to exist at
    all.

    Never raises: a missing ytmusicapi, a private playlist, a network blip and
    an upstream response-shape change (ytmusicapi raises a bare ``KeyError``
    while walking the JSON) all mean the same thing to the caller — use yt-dlp.
    """
    playlist_id = playlist_id_from_url(url)
    if not playlist_id:
        return None

    try:
        from ytmusicapi import YTMusic
    except ImportError:
        logger.debug("ytmusicapi not installed — using yt-dlp for %s", url)
        return None

    try:
        client = YTMusic(auth) if auth else YTMusic()
        # limit=None pages the whole playlist; the default stops at 100.
        raw = client.get_playlist(playlist_id, limit=None)
    except Exception as e:  # noqa: BLE001 - see docstring: all failures fall back
        logger.info(
            "YouTube Music lookup failed for %s (%s: %s) — falling back to yt-dlp",
            playlist_id, type(e).__name__, e,
        )
        return None

    payload = ytmusic_playlist_to_payload(raw, url)
    if payload is None:
        logger.info("YouTube Music returned no usable tracks for %s — falling back", playlist_id)
        return None

    logger.info(
        "YouTube Music: '%s' — %d tracks, %d with an album",
        payload["name"], len(payload["tracks"]),
        sum(1 for t in payload["tracks"] if t["album"]),
    )
    return payload


__all__ = [
    "LIKED_MUSIC_ID",
    "playlist_id_from_url",
    "ytmusic_playlist_to_payload",
    "fetch_ytmusic_playlist",
]
