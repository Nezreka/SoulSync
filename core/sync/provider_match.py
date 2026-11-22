"""exact spotify id matching for playlist sync (#1885).

the sync matchers only matched on title/artist/album. a library artist filed
in cyrillic ("Мумий Тролль") never met the source's latin spelling ("Mumiy
Troll"), because unidecode romanizes й as "i". the sync called the track
missing and _record_library_membership cleared the mirror row's saved link.

tracks.spotify_track_id carries the id, but it isn't proof on its own. the
spotify enrichment worker stamps it from a name search (title similarity, no
artist check), so a row can carry some other song's id. a row only counts
when it also agrees on:

  * title, under the keys the codebase already uses (normalize_key and the
    cleaned comparison title) plus cyrillic folds in spotify's own
    romanization (я is "ya", unidecode says "ia")
  * duration, within 2s when both are known
  * artist, loosely, under the same folds. when the two artists are written
    in different scripts there's nothing to compare, so the id, the title and
    a known duration on both sides carry it. that's the #1885 shape.

a row that fails any of these is a miss and the caller falls through to its
normal matching, same as before. nothing here raises.
"""

from __future__ import annotations

import inspect
import math
import re
import unicodedata
from difflib import SequenceMatcher
from typing import Any, Iterator, List, Optional

# cyrillic to latin the way spotify's own metadata spells it, every table
# keyed (core.text.cross_script has the tables and why there are three)
from core.text.cross_script import CYRILLIC_FOLDS as _CYRILLIC_FOLDS
from core.text.cross_script import CYRILLIC_RE as _CYRILLIC_RE
from utils.logging_config import get_logger

logger = get_logger("sync.provider_match")

# the same recording re-tagged differs by well under a second, a different
# edit by seconds to minutes. 2s leaves room for container slop.
_DURATION_TOLERANCE_MS = 2000

# loose on purpose, it only has to tell "Kino" from some other band. same bar
# as the compare view's artist gate (playlist_reconcile).
_ARTIST_AGREE_THRESHOLD = 0.6
_VARIOUS_ARTISTS = {"various artists", "various", "va"}

def _forms(text: str) -> List[str]:
    """the spellings a name is keyed under. cyrillic only goes through the
    folds, never raw: unidecode turns both й and и into "i", so the raw keys
    of Мой and Мои (different songs) would be identical."""
    if _CYRILLIC_RE.search(text):
        lowered = text.lower()
        return [lowered.translate(table) for table in _CYRILLIC_FOLDS]
    return [text]


def _keys(forms: List[str]) -> set:
    from core.text.normalize import normalize_key
    return {key for key in (normalize_key(f) for f in forms) if key}


def _title_keys(title: Any, db: Any) -> set:
    if not isinstance(title, str) or not title.strip():
        return set()
    forms = _forms(title)
    clean = getattr(db, "_clean_track_title_for_comparison", None)
    if callable(clean):
        for form in list(forms):
            try:
                cleaned = clean(form)
            except Exception as e:  # noqa: BLE001 - cleaning is best effort
                logger.debug("title clean failed for %r: %s", form, e)
                continue
            if isinstance(cleaned, str) and cleaned.strip():
                forms.append(cleaned)
    return _keys(forms)


def _same_literal(a: str, b: str) -> bool:
    return a.casefold().strip() == b.casefold().strip()


def _titles_agree(source_title: Any, row_title: Any, db: Any) -> bool:
    if not isinstance(source_title, str) or not isinstance(row_title, str):
        return False
    if not source_title.strip() or not row_title.strip():
        return False
    try:
        source_keys, row_keys = _title_keys(source_title, db), _title_keys(row_title, db)
    except Exception as e:  # noqa: BLE001 - keying must never escape as a crash
        logger.debug("title keys failed for %r/%r: %s", source_title, row_title, e)
        return _same_literal(source_title, row_title)
    if source_keys and row_keys:
        return bool(source_keys & row_keys)
    # a title that keys to nothing can only agree as the literal string
    return _same_literal(source_title, row_title)


def _is_latin(text: str) -> Optional[bool]:
    """True when every letter is latin, False when any isn't, None when the
    name has no letters at all."""
    letters = [ch for ch in text if ch.isalpha()]
    if not letters:
        return None
    return all("LATIN" in unicodedata.name(ch, "") for ch in letters)


def _artist_names(value: Any) -> List[str]:
    names = []
    for artist in value if isinstance(value, (list, tuple)) else [value]:
        if isinstance(artist, dict):
            artist = artist.get("name")
        elif artist is not None and not isinstance(artist, str):
            artist = getattr(artist, "name", None)
        if isinstance(artist, str) and artist.strip() \
                and artist.strip().casefold() not in _VARIOUS_ARTISTS:
            names.append(artist.strip())
    return names


def _names_close(a: str, b: str) -> bool:
    a_keys, b_keys = _keys(_forms(a)), _keys(_forms(b))
    for ak in a_keys:
        for bk in b_keys:
            if ak == bk or (min(len(ak), len(bk)) >= 3 and (ak in bk or bk in ak)):
                return True
            if SequenceMatcher(None, ak, bk).ratio() >= _ARTIST_AGREE_THRESHOLD:
                return True
    return False


def artists_agree(source_artists: Any, row_artists: Any) -> Optional[str]:
    """"close" when some pair of names agrees under the folds, "cross_script"
    when they can't be compared (latin on one side, another script on the
    other), None when they're comparable and disagree, or one side is
    missing. "Various Artists" is no evidence and doesn't count."""
    source, row = _artist_names(source_artists), _artist_names(row_artists)
    if not source or not row:
        return None
    try:
        if any(_names_close(s, r) for s in source for r in row):
            return "close"
    except Exception as e:  # noqa: BLE001 - unreadable is no evidence
        logger.debug("artist compare failed for %r/%r: %s", source, row, e)
        return None
    for s in source:
        for r in row:
            s_latin, r_latin = _is_latin(s), _is_latin(r)
            if s_latin is not None and r_latin is not None and s_latin != r_latin:
                return "cross_script"
    return None


def _row_value(row: Any, name: str) -> Any:
    if isinstance(row, dict):
        return row.get(name)
    return getattr(row, name, None)


def _as_ms(value: Any) -> Optional[float]:
    if isinstance(value, bool) or value is None:
        return None
    try:
        ms = float(value)
    except (TypeError, ValueError):
        return None
    return ms if ms > 0 and math.isfinite(ms) else None


def durations_agree_ms(source_ms_value: Any, row_ms_value: Any) -> bool:
    """both durations (ms) within the tolerance. a numeric string counts as
    its number. anything that isn't a positive finite number is unknown, and
    unknown is no evidence either way, so it agrees."""
    source_ms = _as_ms(source_ms_value)
    row_ms = _as_ms(row_ms_value)
    if source_ms is None or row_ms is None:
        return True
    return abs(source_ms - row_ms) <= _DURATION_TOLERANCE_MS


def _row_is_corroborated(row: Any, lookup_id: str, track: Any, db: Any) -> bool:
    row_id = _row_value(row, "id")
    # a real library id is a string or int. anything else is a stub shape
    if not isinstance(row_id, (str, int)) or isinstance(row_id, bool):
        return False
    if not str(row_id).strip():
        return False
    # a getter that ignored its argument must not invent a match
    stored_id = _row_value(row, "spotify_track_id")
    if stored_id is not None and str(stored_id).strip() != lookup_id:
        return False
    if not _titles_agree(getattr(track, "name", None), _row_value(row, "title"), db):
        return False
    source_ms = _row_value(track, "duration_ms")
    row_ms = _row_value(row, "duration")
    if not durations_agree_ms(source_ms, row_ms):
        return False
    artists = artists_agree(_source_artists_of(track),
                            [_row_value(row, "artist_name"), _row_value(row, "track_artist")])
    if artists == "close":
        return True
    if artists == "cross_script":
        # no name evidence at all, so the duration has to be really there
        return _as_ms(source_ms) is not None and _as_ms(row_ms) is not None
    return False


def provider_titles_agree(db: Any, source_title: Any, row_title: Any) -> bool:
    """the corroboration title test, for a candidate found another way (the
    plex stale key rescue) that has to prove it's the same title."""
    return _titles_agree(source_title, row_title, db)


def provider_match_rejected(db: Any, profile_id: Any, server_source: str,
                            track: Any, library_track_id: Any) -> bool:
    """True when the user unmatched this pairing, and also True when that
    can't be told. the lenient is_rejected_match fails open for the compare
    view. honoring a maybe-unmatched pairing at 1.0 would quietly undo the
    user's unmatch, so here unknown means no."""
    try:
        from core.sync.match_overrides import match_lookup_ids
        source_ids = match_lookup_ids(track)
    except Exception as e:  # noqa: BLE001 - no ids, can't prove it's safe
        logger.debug("provider rejection check: lookup ids failed: %s", e)
        return True
    # without a profile or ids the query can only say "nothing found",
    # which is unknown, not safe
    if not profile_id or not source_ids:
        return True
    # strict getter only. the lenient one turns a db error into an empty set,
    # which reads as "no rejections" exactly when we don't know
    getter = getattr(db, "get_server_match_rejections_strict", None)
    if getter is None:
        return True
    try:
        rejected = getter(profile_id, server_source, source_ids)
    except Exception as e:  # noqa: BLE001 - unknown means don't honor
        logger.debug("provider rejection check failed: %s", e)
        return True
    if rejected is None:
        return True
    try:
        return any((str(s), str(library_track_id)) in rejected for s in source_ids)
    except Exception as e:  # noqa: BLE001 - unreadable means don't honor
        logger.debug("provider rejection result unreadable: %s", e)
        return True


def _call_id_getter(fn: Any, lookup_id: str, server_source: str) -> Any:
    """call a getter the way its signature takes it. decided from the
    signature, not by catching TypeError, so a TypeError inside a real getter
    is never mistaken for a signature mismatch."""
    try:
        params = inspect.signature(fn).parameters
        accepts_kwarg = "server_source" in params or any(
            p.kind == inspect.Parameter.VAR_KEYWORD for p in params.values())
    except (TypeError, ValueError):  # uninspectable, assume the real shape
        accepts_kwarg = True
    if accepts_kwarg:
        return fn(lookup_id, server_source=server_source)
    return fn(lookup_id)


def _rows_for_id(db: Any, lookup_id: str, server_source: str) -> List[Any]:
    """every library row carrying this id. a plural getter that answers, even
    with an empty list, is the answer. the singular getter is only a fallback
    for a plural that's missing, raises or returns None."""
    plural = getattr(db, "get_tracks_by_spotify_id", None)
    if callable(plural):
        try:
            result = _call_id_getter(plural, lookup_id, server_source)
        except Exception as e:  # noqa: BLE001 - fall through to singular
            logger.debug("provider match plural lookup failed for %s: %s", lookup_id, e)
            result = None
        if isinstance(result, dict):
            return [result]
        if result is not None:
            try:
                return list(result)
            except Exception as e:  # noqa: BLE001 - unreadable, fall through
                logger.debug("provider match plural result unreadable for %s: %s", lookup_id, e)
    singular = getattr(db, "get_track_by_spotify_id", None)
    if singular is None:
        return []
    try:
        row = _call_id_getter(singular, lookup_id, server_source)
    except Exception as e:  # noqa: BLE001 - a failed lookup is a miss
        logger.debug("provider match lookup failed for %s: %s", lookup_id, e)
        return []
    return [row] if row is not None else []


def provider_id_library_matches(db: Any, track: Any, server_source: str = None) -> List[Any]:
    """every library row carrying one of this track's ids that corroborates,
    in lookup order. the id isn't unique (album and compilation copies, or a
    wrong stamp next to the right one), so callers try each row in turn.
    wing-it stub ids are skipped, they name nothing at spotify."""
    if getattr(db, "get_tracks_by_spotify_id", None) is None and \
            getattr(db, "get_track_by_spotify_id", None) is None:
        return []
    try:
        from core.discovery.wing_it import is_stub_id
        from core.sync.match_overrides import match_lookup_ids
        lookup_ids = match_lookup_ids(track)
    except Exception as e:  # noqa: BLE001 - no ids, no provider match
        logger.debug("provider match: lookup ids unavailable: %s", e)
        return []
    matches: List[Any] = []
    for lookup_id in lookup_ids:
        if is_stub_id(lookup_id):
            continue
        for row in _rows_for_id(db, lookup_id, server_source):
            try:
                corroborated = _row_is_corroborated(row, lookup_id, track, db)
            except Exception as e:  # noqa: BLE001 - an unreadable row is a miss
                logger.debug("provider match: row unreadable for %s: %s", lookup_id, e)
                continue
            if corroborated:
                matches.append(row)
            else:
                logger.debug("provider id %s row %s didn't corroborate",
                             lookup_id, _row_value(row, "id"))
    return matches


def provider_id_library_match(db: Any, track: Any, server_source: str = None) -> Optional[Any]:
    """the first corroborated row, see provider_id_library_matches."""
    matches = provider_id_library_matches(db, track, server_source)
    return matches[0] if matches else None


def honorable_provider_rows(db: Any, track: Any, server_source: str,
                            profile_id: Any) -> Iterator[Any]:
    """the corroborated rows the user hasn't unmatched, in order. both sync
    matchers walk this and take the first row they can turn into a track."""
    for row in provider_id_library_matches(db, track, server_source):
        row_id = _row_value(row, "id")
        if provider_match_rejected(db, profile_id, server_source, track, row_id):
            logger.info("provider id match for '%s' -> %s skipped, unmatched by the user "
                        "or the rejection state is unknown", getattr(track, "name", "?"), row_id)
            continue
        yield row


def _source_artists_of(track: Any) -> List[Any]:
    """the track's artists plus the mirrored playlist's own names for them
    (#2222: itunes "Δέσποινα Βανδή", the playlist "Despina Vandi")."""
    names = list(getattr(track, "artists", None) or [])
    extra = getattr(track, "source_artists", None)
    if isinstance(extra, (list, tuple)):
        names += list(extra)
    return names


def _isrc_row_is_corroborated(row: Any, isrc: str, track: Any, db: Any) -> bool:
    row_id = _row_value(row, "id")
    if not isinstance(row_id, (str, int)) or isinstance(row_id, bool) or not str(row_id).strip():
        return False
    if not _titles_agree(getattr(track, "name", None), _row_value(row, "title"), db):
        return False
    if not durations_agree_ms(_row_value(track, "duration_ms"), _row_value(row, "duration")):
        return False
    # an isrc names one recording on every provider, so unlike a spotify id
    # stamped from a name search it needs no duration to cross scripts. it
    # still has to agree on the artist when the names can be compared: a
    # compilation can carry a wrong isrc
    return artists_agree(_source_artists_of(track),
                         [_row_value(row, "artist_name"), _row_value(row, "track_artist")]) \
        in ("close", "cross_script")


def isrc_library_matches(db: Any, track: Any, server_source: str = None) -> List[Any]:
    """every library row carrying the track's isrc that corroborates (#2222).
    empty when the track has no isrc or the db can't look one up."""
    isrc = str(getattr(track, "isrc", None) or "").strip().upper()
    getter = getattr(db, "get_tracks_by_isrc", None)
    if not isrc or not callable(getter):
        return []
    try:
        rows = list(_call_id_getter(getter, isrc, server_source) or [])
    except Exception as e:  # noqa: BLE001 - a failed lookup is a miss
        logger.debug("isrc lookup failed for %s: %s", isrc, e)
        return []
    matches = []
    for row in rows:
        try:
            if _isrc_row_is_corroborated(row, isrc, track, db):
                matches.append(row)
            else:
                logger.debug("isrc %s row %s didn't corroborate", isrc, _row_value(row, "id"))
        except Exception as e:  # noqa: BLE001 - an unreadable row is a miss
            logger.debug("isrc match: row unreadable for %s: %s", isrc, e)
    return matches


def honorable_isrc_rows(db: Any, track: Any, server_source: str,
                        profile_id: Any) -> Iterator[Any]:
    """the corroborated isrc rows the user hasn't unmatched, in order."""
    for row in isrc_library_matches(db, track, server_source):
        row_id = _row_value(row, "id")
        if provider_match_rejected(db, profile_id, server_source, track, row_id):
            logger.info("isrc match for '%s' -> %s skipped, unmatched by the user "
                        "or the rejection state is unknown", getattr(track, "name", "?"), row_id)
            continue
        yield row


def rescue_is_the_provider_row(db: Any, track: Any, provider_row: Any, rescued: Any,
                               profile_id: Any, server_source: str) -> bool:
    """a plex stale key rescue is a title+artist search, its top hit isn't the
    id's row. it only rides on the id when it agrees with the provider row on
    title and artist (when both have one), with every known duration, and
    isn't itself a pairing the user unmatched."""
    if rescued is None:
        return False
    if not provider_titles_agree(db, _row_value(provider_row, "title"),
                                 getattr(rescued, "title", None)):
        return False
    rescued_artist = getattr(rescued, "grandparentTitle", None)
    if not isinstance(rescued_artist, str) or not rescued_artist:
        # plexapi's Track.artist is a method, not a name. a non-string is no evidence
        rescued_artist = getattr(rescued, "artist", None)
    # both names come off the same server, so they should key the same. a
    # namesake track by another artist must not ride on the id
    row_names, rescued_names = (_artist_names(_row_value(provider_row, "artist_name")),
                                _artist_names(rescued_artist))
    if row_names and rescued_names \
            and not provider_titles_agree(db, row_names[0], rescued_names[0]):
        return False
    rescued_ms = getattr(rescued, "duration", None)
    if not (durations_agree_ms(_row_value(track, "duration_ms"), rescued_ms)
            and durations_agree_ms(_row_value(provider_row, "duration"), rescued_ms)):
        return False
    return not provider_match_rejected(db, profile_id, server_source, track,
                                       getattr(rescued, "ratingKey", None))


__all__ = ["artists_agree", "durations_agree_ms", "honorable_provider_rows",
           "provider_id_library_match", "provider_id_library_matches",
           "provider_match_rejected", "provider_titles_agree",
           "rescue_is_the_provider_row"]
