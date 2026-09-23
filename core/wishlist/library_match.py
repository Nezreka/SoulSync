"""Deciding that the library already owns a wishlisted track (#1289).

Two places clear wishlist rows on the grounds that the track is already in the
library: the auto-wishlist cleanup in :mod:`core.wishlist.processing` and the
post-batch cleanup in :mod:`core.downloads.cleanup`. Both asked
``check_track_exists`` and both deleted the row on a hit, and neither looked at
what they had actually matched.

The hole that matters is a row whose ``file_path`` points INTO the atomic
staging tree. Those rows are real — ``record_soulsync_library_entry`` writes
one for every staged track on a ``soulsync`` server, which is exactly why the
publish has to repoint them — but the file they name is invisible to the media
server and may never be published at all. Matching against one and deleting the
wishlist row means the request is gone while the audio is still quarantined:
the same dropout the atomic-publish fix closes, arriving by a different door.

What this module deliberately does NOT do is require the matched file to exist
on disk. Rows sourced from Plex/Jellyfin/Navidrome carry those servers' paths,
which routinely do not resolve inside SoulSync's container, so treating absence
as disproof would reject every legitimate match and re-download a whole library.
Absence is not evidence here; a staging path is.
"""

from __future__ import annotations

from typing import Any, Optional, Tuple

from core.downloads.atomic_album_publish import contains_staging_segment
from core.imports.context import extract_artist_name
from utils.logging_config import get_logger

logger = get_logger("wishlist.library_match")


def artist_names(artists: Any) -> list:
    """Every credited artist name, whatever shape the payload arrived in.

    A bare string is one artist, not a list of characters — iterating it would
    hand back 'B', 'a', 'n', 'd'. Per-item normalisation is
    :func:`core.imports.context.extract_artist_name`, which already knows about
    dicts, objects with ``.name`` and plain strings.
    """
    if isinstance(artists, str):
        artists = [artists]
    return [name for name in (extract_artist_name(a) for a in (artists or [])) if name]


def find_owned_match(music_database, track_name: str, artists: Any, album: Optional[str],
                     active_server: str, *, confidence_threshold: float = 0.7,
                     log=None, log_prefix: str = "[Wishlist]"
                     ) -> Optional[Tuple[Any, float, str]]:
    """``(db_track, confidence, matched_artist)`` for a track the library owns.

    None when nothing matched, or when the only match is a staged file that the
    media server cannot see.
    """
    log = log or logger
    for artist_name in artist_names(artists):
        try:
            db_track, confidence = music_database.check_track_exists(
                track_name,
                artist_name,
                confidence_threshold=confidence_threshold,
                server_source=active_server,
                album=album,
            )
        except Exception:  # noqa: BLE001 - one bad artist string must not abort the sweep
            continue

        if not db_track or confidence < confidence_threshold:
            continue

        file_path = getattr(db_track, 'file_path', None)
        if contains_staging_segment(file_path or ''):
            # Single pre-formatted argument on purpose: callers inject their own
            # logger here (the wishlist cleanups pass a job logger, and the tests
            # a fake), and this module must not assume %-style formatting support.
            log.warning(
                f"{log_prefix} Ignoring already-owned match for '{track_name}' by "
                f"'{artist_name}' (confidence: {confidence:.2f}): the matched library row "
                f"still points into atomic-publish staging ({file_path}), so the track is "
                f"not in the library yet — keeping the wishlist entry")
            continue

        log.info(
            f"{log_prefix} Track found in database: '{track_name}' by {artist_name} "
            f"(confidence: {confidence:.2f}) → {file_path or '<no path>'}")
        return db_track, confidence, artist_name

    return None


__all__ = ["artist_names", "find_owned_match"]
