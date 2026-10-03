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
from core.matching.audio_verification import normalize
from core.text.normalize import normalize_key
from core.text.title_match import (
    is_trailing_version_qualifier,
    recording_version_markers,
    strip_redundant_context_qualifiers,
    strip_subtitle_qualifiers,
)
from utils.logging_config import get_logger

logger = get_logger("wishlist.library_match")

_BRACKET_QUALIFIER = re.compile(r'\(([^()]*)\)|\[([^\[\]]*)\]|<([^<>]*)>')
_DASH_QUALIFIER = re.compile(r'(?:\s[-–—]\s*|\s*[-–—]\s)([^–—-]+)$')
_FEATURED_CREDIT = re.compile(r'^(?:feat\.?|ft\.?|featuring)\s', re.I)
_EDITION_ONLY = re.compile(
    r'(?:super\s+)?deluxe|expanded|(?:\d+(?:st|nd|rd|th)\s+)?anniversary',
    re.I,
)


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


def _is_metadata_annotation(text: str) -> bool:
    # A featured artist named Live does not make this a live recording.
    return bool(_FEATURED_CREDIT.match(text.strip())
                or ((_EDITION_ONLY.fullmatch(text.strip()) or is_trailing_version_qualifier(text))
                    and not recording_version_markers(f'({text})')))


def _normalized_title(title: str) -> str:
    # Remove metadata noise, then keep actual subtitles, numbered parts and
    # recording details by unwrapping them before using the verifier's
    # normalizer. Category parity alone would merge two different live years
    # or two named remixes. Preserve dash-form recording details as well.
    def preserve_subtitle(match):
        inner = next(group for group in match.groups() if group is not None)
        return f' {inner} '

    title = _BRACKET_QUALIFIER.sub(preserve_subtitle, _strip_metadata_annotations(title))
    return normalize(title, strip_version_tail=False)


def _strip_metadata_annotations(title: str) -> str:
    """Strip harmless metadata, retaining punctuation for album SQL searches."""
    base_key = _identity_key(normalize(title))

    def strip_edition(match):
        inner = next(group for group in match.groups() if group is not None)
        if _identity_key(inner) == base_key:
            return f' {inner} '  # Brackets can contain the whole title.
        return ' ' if _is_metadata_annotation(inner) else match.group()

    base = _BRACKET_QUALIFIER.sub(strip_edition, title)
    while (tail := _DASH_QUALIFIER.search(base)) and _is_metadata_annotation(tail.group(1)):
        base = base[:tail.start()]
    return ' '.join(base.split()) or title


def _same_title(requested: str, owned: str, album_context: str = '',
                *, allow_subtitles: bool = False) -> bool:
    """Accept metadata wording without discarding a distinct recording version."""
    requested_key = _identity_key(requested)
    if requested_key and requested_key == _identity_key(owned):
        return True
    if not requested_key or not _identity_key(owned):
        # A punctuation-only title has no word key: "-" and "&" must not
        # become identical merely because both reduce to the empty string.
        return requested.casefold().strip() == owned.casefold().strip()
    pairs = [(requested, owned)]
    requested_context = strip_redundant_context_qualifiers(requested, album_context)
    owned_context = strip_redundant_context_qualifiers(owned, album_context)
    # If both sides lose different qualifiers, their shared base is not enough
    # evidence of ownership ("Song (Verse)" is not "Song (Chorus)").
    if requested_context == requested or owned_context == owned:
        pairs.append((requested_context, owned_context))
    for wanted, found in pairs:
        key = _identity_key(_normalized_title(wanted))
        if key and key == _identity_key(_normalized_title(found)):
            return True
    if allow_subtitles:
        # The shared subtitle helper expects accent-folded input. Keep the
        # brackets, and check recording markers against the original scripts.
        wanted_raw = _strip_metadata_annotations(requested)
        found_raw = _strip_metadata_annotations(owned)
        wanted, found = (
            ''.join(c for c in unicodedata.normalize('NFKD', title)
                    if not unicodedata.combining(c))
            for title in (wanted_raw, found_raw)
        )
        wanted_base = strip_subtitle_qualifiers(wanted, found)
        found_base = strip_subtitle_qualifiers(found, wanted)
        # Preserve #825's one-sided subtitle compatibility. Two conflicting
        # subtitles are not evidence of ownership. The shared marker detector
        # also protects non-English versions the subtitle token list misses.
        if ((wanted_base == wanted or found_base == found)
                and recording_version_markers(wanted_raw) == recording_version_markers(wanted_base)
                and recording_version_markers(found_raw) == recording_version_markers(found_base)):
            key = _identity_key(_normalized_title(wanted_base))
            if key and key == _identity_key(_normalized_title(found_base)):
                return True
    return False


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
    def artist_key(name):
        # check_track_exists already searches both leading-"The" spellings.
        return normalize_key(re.sub(r'^the\s+', '', name.strip(), flags=re.I))

    target = artist_key(requested)
    parts = re.split(
        r'\s*(?:[,;&]|\bfeat\.?\b|\bft\.?\b|\bfeaturing\b|\bvs\.?\b)\s*',
        credit, flags=re.I,
    )
    # Split explicit credits first, retaining "Lil Nas X" as a complete name
    # before considering the whitespace-delimited collaboration separator.
    collaborations = [name for part in parts for name in re.split(r'\s+x\s+', part.strip(), flags=re.I)]
    return bool(target and any(artist_key(part) == target for part in [credit, *parts, *collaborations]))


def _strict_identity_matches(db_track: Any, track_name: str, artist_name: str,
                             album: Optional[str], require_album: bool) -> bool:
    matched_title = getattr(db_track, 'title', None)
    matched_album = getattr(db_track, 'album_title', None)
    same_album = bool(album and matched_album and _same_title(album, matched_album))
    if require_album and not same_album:
        return False
    album_context = matched_album if same_album else ''
    return bool(matched_title and _same_title(track_name, matched_title, album_context,
                                              allow_subtitles=True)
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
                title=_strip_metadata_annotations(album), artist='', limit=500, server_source=active_server)
            album_ids = [candidate.id for candidate in albums
                         if _same_title(album, candidate.title)]
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


ALBUM_SCOPED_SOURCE_TYPES = frozenset({'album', 'discography', 'watchlist', 'watchlist_label'})


def wishlist_row_requires_album(track) -> bool:
    """True when the wishlist row names a specific release whose ownership —
    not just the song's — must be proven before cleanup (#1447).

    Download Discography rows carry source_type="discography", watchlist rows
    "watchlist"/"watchlist_label", and each names the requested release in
    ``track['album']['name']``. Owning the same song on a single/EP/live
    session must not clear the request for the unowned album.
    """
    source_type = (track or {}).get('source_type')
    if source_type == 'album':
        return True  # historical behavior, unchanged
    if source_type in ALBUM_SCOPED_SOURCE_TYPES:
        album = track.get('album', {})
        album_name = album.get('name') if isinstance(album, dict) else album
        return bool(album_name)  # album-less rows keep old behavior: never stuck
    return False


__all__ = ["artist_names", "find_owned_match", "wishlist_row_requires_album",
           "ALBUM_SCOPED_SOURCE_TYPES"]
