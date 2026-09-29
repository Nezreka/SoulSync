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

import re
import unicodedata
from typing import Any, Optional, Tuple

from core.downloads.atomic_album_publish import contains_staging_segment
from core.imports.context import extract_artist_name
from core.text.normalize import normalize_key
from core.text.title_match import strip_redundant_context_qualifiers
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


def _identity_key(value: str) -> str:
    """Ignore spelling punctuation while retaining recording/version words."""
    decomposed = unicodedata.normalize('NFKD', value or '')
    unaccented = ''.join(c for c in decomposed if not unicodedata.combining(c))
    # Apostrophes do not separate words ("Don't" and "Dont"); underscores do.
    unaccented = re.sub(r"['’]", '', unaccented)
    return re.sub(r'[\W_]+', ' ', unaccented.casefold()).strip()


def _same_title(requested: str, owned: str, album_context: str = '') -> bool:
    """Accept metadata wording without discarding a distinct recording version."""
    requested_key = _identity_key(requested)
    if requested_key and requested_key == _identity_key(owned):
        return True
    if not requested_key or not _identity_key(owned):
        # A punctuation-only title has no word key: "-" and "&" must not
        # become identical merely because both reduce to the empty string.
        return requested.casefold().strip() == owned.casefold().strip()
    requested_context = strip_redundant_context_qualifiers(requested, album_context, owned)
    owned_context = strip_redundant_context_qualifiers(owned, album_context, requested)
    # If both sides lose different qualifiers, their shared base is not enough
    # evidence of ownership ("Song (Verse)" is not "Song (Chorus)").
    if requested_context != requested and owned_context != owned:
        return False
    key = _identity_key(requested_context)
    return bool(key and key == _identity_key(owned_context))


def _same_artist(requested: str, db_track: Any) -> bool:
    """Guard the database matcher's album fallback, which scores title alone."""
    # A per-track credit is more specific than the album artist. Checking both
    # would mistake a different performer's song on an artist compilation for
    # the requested artist's recording.
    credit = getattr(db_track, 'track_artist', None) or getattr(db_track, 'artist_name', None)
    if not credit:
        return True  # Older database adapters do not expose artist credits.
    # Use the artist key already used by library search: AC/DC and ACDC are
    # the same credit even though their word boundaries differ.
    target = normalize_key(requested)
    parts = re.split(
        r'\s*(?:[,;&]|\bfeat\.?\b|\bft\.?\b|\bfeaturing\b|\bvs\.?\b|\bx\b)\s*',
        credit, flags=re.I,
    )
    return bool(target and any(normalize_key(part) == target for part in [credit, *parts]))


def _strict_identity_matches(db_track: Any, track_name: str, artist_name: str,
                             album: Optional[str], require_album: bool) -> bool:
    matched_title = getattr(db_track, 'title', None)
    matched_album = getattr(db_track, 'album_title', None)
    album_key = _identity_key(album)
    if require_album and (not album_key or not matched_album or album_key != _identity_key(matched_album)):
        return False
    album_context = matched_album if album_key and matched_album and album_key == _identity_key(matched_album) else ''
    return bool(matched_title and _same_title(track_name, matched_title, album_context)
                and _same_artist(artist_name, db_track))


def find_owned_match(music_database, track_name: str, artists: Any, album: Optional[str],
                     active_server: str, *, confidence_threshold: float = 0.7,
                     strict_identity: bool = False, require_album: bool = False,
                     log=None, log_prefix: str = "[Wishlist]"
                     ) -> Optional[Tuple[Any, float, str]]:
    """``(db_track, confidence, matched_artist)`` for a track the library owns.

    None when nothing matched, or when the only match is a staged file that the
    media server cannot see.
    """
    log = log or logger
    names = artist_names(artists)
    rejected_match = False
    for artist_name in names:
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

        if strict_identity:
            # check_track_exists can return a same-artist song with a merely
            # similar title ("Runaway Train" -> "The Sun Maid" at 0.74).
            # That is useful for search suggestions, but is not proof that a
            # wishlist request has been fulfilled. Keep version words, too:
            # an acoustic/demo/live recording may be a distinct target.
            if not _strict_identity_matches(db_track, track_name, artist_name, album, require_album):
                rejected_match = True
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
            rejected_match = True
            continue

        log.info(
            f"{log_prefix} Track found in database: '{track_name}' by {artist_name} "
            f"(confidence: {confidence:.2f}) → {file_path or '<no path>'}")
        return db_track, confidence, artist_name

    # The fuzzy matcher returns one winner and does not rank by album. When it
    # chose another release, check the requested album's tracks before leaving
    # an already-owned album wish queued. This runs only after the cheap check
    # failed, and uses the existing album/candidate queries.
    if strict_identity and require_album and album and names and rejected_match:
        try:
            albums = music_database.search_albums(
                title=album, artist='', limit=500, server_source=active_server)
            album_key = _identity_key(album)
            album_ids = [candidate.id for candidate in albums
                         if album_key and _identity_key(candidate.title) == album_key]
            if album_ids:
                for candidate in music_database.get_candidate_tracks_for_albums(album_ids):
                    if getattr(candidate, 'server_source', active_server) != active_server:
                        continue
                    if contains_staging_segment(getattr(candidate, 'file_path', None) or ''):
                        continue
                    for artist_name in names:
                        if _strict_identity_matches(candidate, track_name, artist_name, album, True):
                            log.info(f"{log_prefix} Track found on requested album: '{track_name}' by {artist_name}")
                            return candidate, 1.0, artist_name
        except Exception as exc:  # noqa: BLE001 - cleanup must leave the wish intact on lookup failure
            log.warning(f"{log_prefix} Album-scoped ownership lookup failed for '{track_name}': {exc}")

    return None


__all__ = ["artist_names", "find_owned_match"]
