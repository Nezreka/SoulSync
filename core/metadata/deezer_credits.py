"""Who Deezer credits on a track, beyond the one name its tracklists carry.

Deezer's album tracklist (``/album/<id>/tracks``, and ``tracks.data`` on the
album) names only the FIRST artist of each track. The whole credit is on the
album (``contributors``) and on the track's own record (``/track/<id>``).

So a check that reads the tracklist to ask "is this artist on the track" said
no for every release where the artist is a co-main artist and not the first
one named: Bad Bunny's joint album with J Balvin lost all eight tracks as
"artist mismatch", and a watchlist of about thirty artists lost some 1,300
(#2104).

This is the second opinion, asked only for a track the tracklist already
turned down: the album's contributors first when it is a joint release (one
cached lookup for the whole release), then the track's own record. Never raises; with nothing to ask it
answers no, which leaves the caller where it was.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from utils.logging_config import get_logger

logger = get_logger("metadata.deezer_credits")


def _client():
    from core.metadata import get_deezer_client
    return get_deezer_client()


def _names(entries: Any) -> List[str]:
    names = []
    for entry in entries if isinstance(entries, (list, tuple)) else []:
        name = entry.get("name") if isinstance(entry, dict) else entry
        if name:
            names.append(str(name))
    return names


# an album credited to more artists than this is a compilation in all but
# name: being one of its contributors says nothing about any one track
_JOINT_RELEASE_MAX = 4


def album_contributor_names(album_id: Any, client: Any = None) -> List[str]:
    """The artists a joint release is credited to, or nothing.

    Being credited on the album vouches for its tracks only when the album is
    a few artists' release together. A compilation lists everyone on it, and
    keeping all its tracks for one of them is the contamination #559 fixed, so
    for those each track is asked about on its own."""
    if not album_id:
        return []
    try:
        album = (client or _client()).get_album_metadata(str(album_id), include_tracks=False)
    except Exception as exc:  # noqa: BLE001
        logger.debug("deezer album credits not read (%s): %s", album_id, exc)
        return []
    raw = (album or {}).get("_raw_data") or {}
    names = _names(raw.get("contributors"))
    if raw.get("record_type") == "compile" or len(names) > _JOINT_RELEASE_MAX:
        return []
    return names


def track_contributor_names(track_id: Any, client: Any = None) -> List[str]:
    """Every artist Deezer credits on the track's own record."""
    if not track_id:
        return []
    try:
        track = (client or _client()).get_track_details(str(track_id))
    except Exception as exc:  # noqa: BLE001
        logger.debug("deezer track credits not read (%s): %s", track_id, exc)
        return []
    return _names((track or {}).get("artists"))


def credits_artist(artist_name: str, *, album_id: Any = None, track_id: Any = None,
                   memo: Optional[Dict[str, List[str]]] = None, client: Any = None) -> bool:
    """Whether Deezer credits ``artist_name`` on this track of this album.

    ``memo`` holds each album's contributors across the tracks of one run, so
    a release is asked about once however many of its tracks were turned down.
    """
    if not artist_name:
        return False
    from core.metadata.discography_filters import track_artist_matches

    try:
        client = client or _client()
    except Exception as exc:  # noqa: BLE001
        logger.debug("no deezer client for the credits check: %s", exc)
        return False
    if client is None:
        return False
    if album_id:
        key = str(album_id)
        if memo is not None and key in memo:
            contributors = memo[key]
        else:
            contributors = album_contributor_names(key, client)
            if memo is not None:
                memo[key] = contributors
        if contributors and track_artist_matches(contributors, artist_name):
            return True
    credited = track_contributor_names(track_id, client)
    return bool(credited) and track_artist_matches(credited, artist_name)


__all__ = ["album_contributor_names", "credits_artist", "track_contributor_names"]
