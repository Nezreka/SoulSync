"""Exact provider-ID matching for playlist sync (#1885).

A source track arrives carrying its Spotify ID, and a library row that was
downloaded or imported with that provenance carries the same ID in
``tracks.spotify_track_id``. The sync matchers never looked at it: they
matched on title/artist/album only, so a track whose library artist is
filed in a different script than the source's (Latin on Spotify, Cyrillic
in the local tags) scored below threshold on artist evidence, the sync
reported it missing, and ``_record_library_membership`` cleared the
mirrored row's ``in_library`` / ``library_track_id`` — a valid link,
deleted by a spelling difference.

An exact provider ID is identity, not similarity, so it is honored
WITHOUT artist evidence — but not blindly. The ID alone is stamped by
provenance plumbing that has been wrong before (the #1715/#1721 poison
shape was a confident pairing to the wrong song), so a row the ID names
must still corroborate itself:

  * its title must key the same as the source title under any of the
    forms the codebase already treats as the same title: the raw
    ``normalize_key``, the cleaned title (feat./remaster/parenthetical
    tails stripped, the way ``_clean_track_title_for_comparison`` scores
    them), or a Cyrillic-folded key (Cyrillic letters folded with the
    romanization Spotify itself uses — я→"ya" — where Unidecode's
    я→"ia" would split one title into two keys). A Cyrillic title is
    keyed only through the folds, never the raw form: Unidecode
    merges й and и into "i", so Мой and Мои would otherwise key
    identical. A merely *similar* title is the fuzzy matcher's job,
    not this one's; and
  * when both durations are known they must agree within
    ``_DURATION_TOLERANCE_MS`` (the affected tracks differed by less
    than a second; a different recording/edit differs by far more).

The ID column is not unique: the same recording can sit on an album and
a compilation, and a poison-stamped duplicate is precisely a row that
fails corroboration while its sibling passes. Every row carrying the ID
is considered, in track-id order, and every corroborated one is
returned — the first may still be the row the user unmatched or the
one whose server key went stale, so callers iterate until one row
survives their own checks too.

A row that fails either check is NOT a match here — the caller falls
through to its normal matching, exactly as before. Anything unreadable
(a stub db, a row without a real id/title) is likewise a miss: this
helper never raises and never guesses.
"""

from __future__ import annotations

import inspect
import math
import re
from typing import Any, List, Optional

from utils.logging_config import get_logger

logger = get_logger("sync.provider_match")

# Two recordings of different edits/versions of a song differ by seconds
# to minutes; the same recording re-tagged differs by well under a
# second. 2s leaves room for container/trim slop without admitting a
# different edit.
_DURATION_TOLERANCE_MS = 2000

# Cyrillic → Latin in the romanization Spotify's own metadata uses
# (я→"ya", ю→"yu", х→"kh"). Unidecode (normalize_key's folder) picks a
# different scholarly romanization for several letters (я→"ia"), so a
# Cyrillic-tagged title and its Latin source title key differently under
# normalize_key alone. Folding Cyrillic through this table FIRST makes
# both sides land on the same key; Latin text passes through untouched.
# Covers the Russian, Ukrainian, Belarusian and Serbian Cyrillic
# letters. The ALT table differs only for ё, which Spotify romanizes
# both ways ("Yolka" the artist, "Elka" the tree) — both folds are
# keyed, so either spelling agrees.
_CYRILLIC_FOLD = str.maketrans({
    "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e",
    "ё": "yo", "ж": "zh", "з": "z", "и": "i", "й": "y", "к": "k",
    "л": "l", "м": "m", "н": "n", "о": "o", "п": "p", "р": "r",
    "с": "s", "т": "t", "у": "u", "ф": "f", "х": "kh", "ц": "ts",
    "ч": "ch", "ш": "sh", "щ": "shch", "ъ": "", "ы": "y", "ь": "",
    "э": "e", "ю": "yu", "я": "ya",
    "і": "i", "ї": "yi", "є": "ye", "ґ": "g", "ў": "u",
    "ђ": "dj", "ј": "j", "љ": "lj", "њ": "nj", "ћ": "c", "џ": "dzh",
})
_CYRILLIC_FOLD_ALT = str.maketrans({
    "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e",
    "ё": "e", "ж": "zh", "з": "z", "и": "i", "й": "y", "к": "k",
    "л": "l", "м": "m", "н": "n", "о": "o", "п": "p", "р": "r",
    "с": "s", "т": "t", "у": "u", "ф": "f", "х": "kh", "ц": "ts",
    "ч": "ch", "ш": "sh", "щ": "shch", "ъ": "", "ы": "y", "ь": "",
    "э": "e", "ю": "yu", "я": "ya",
    "і": "i", "ї": "yi", "є": "ye", "ґ": "g", "ў": "u",
    "ђ": "dj", "ј": "j", "љ": "lj", "њ": "nj", "ћ": "c", "џ": "dzh",
})
# The main table with only х→"h": the romanization family that keeps
# и→"i" and г→"g" but spells х with a single letter — Хорошо is
# "Horosho" (not only "Khorosho"), Хелло is "Hello", Михаил is
# "Mihail", Хи is "Hi". Keyed alongside the others, like every fold
# here: agreement under ONE table is enough, and a romanization no
# table covers still falls through to the fuzzy matcher, exactly as
# titles did before #1885.
# Deliberately NO table folds г→"h" or ґ→"h" (х's single letter), or
# щ→"sh" (ш's digraph): those whole-letter mappings would make two
# DIFFERENT titles key the same — Голод (hunger) and Холод (cold)
# both become "holod" — and a fold that merges distinct letters
# defeats the title corroboration this module exists to enforce.
# Equivalences the romanizations themselves share are intended:
# е/э both romanize to "e", и/і to "i", and the ALT table's ё→"e"
# matches a spelling Spotify itself uses. (Digraph boundaries can
# coincide, as in any transliteration — the duration check behind
# the title is the guard there.) The Ukrainian-style spellings that
# would need the merging folds fall through to the fuzzy matcher.
_CYRILLIC_FOLD_H = str.maketrans({
    "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e",
    "ё": "yo", "ж": "zh", "з": "z", "и": "i", "й": "y", "к": "k",
    "л": "l", "м": "m", "н": "n", "о": "o", "п": "p", "р": "r",
    "с": "s", "т": "t", "у": "u", "ф": "f", "х": "h", "ц": "ts",
    "ч": "ch", "ш": "sh", "щ": "shch", "ъ": "", "ы": "y", "ь": "",
    "э": "e", "ю": "yu", "я": "ya",
    "і": "i", "ї": "yi", "є": "ye", "ґ": "g", "ў": "u",
    "ђ": "dj", "ј": "j", "љ": "lj", "њ": "nj", "ћ": "c", "џ": "dzh",
})


def _fold_cyrillic(text: str) -> str:
    # The tables are keyed by lowercase letters; normalize_key lowercases
    # its input anyway, so folding the lowered text loses nothing.
    return text.lower().translate(_CYRILLIC_FOLD)


_CYRILLIC_RE = re.compile(r"[Ѐ-ӿ]")  # the Cyrillic block, U+0400–U+04FF


def _title_keys(title: Any, db: Any) -> set:
    """Every key under which this title should be considered 'the same
    title' as another: raw, cleaned, and Cyrillic-folded forms.

    A title containing Cyrillic is keyed ONLY through the fold
    tables, never through the raw ``normalize_key`` form: Unidecode
    (which ``normalize_key`` uses in production) romanizes both й
    and и to "i", so the raw keys of Мой and Мои — different words,
    different songs — are identical, and a raw key would let them
    corroborate each other. The folds keep й ("y") and и ("i")
    apart. A Latin title's raw key still meets a Cyrillic title's
    fold keys in the shared Latin space ("Moy" = Мой's fold, "Moi"
    = Мои's), which is exactly the cross-script agreement #1885
    needs."""
    if not isinstance(title, str) or not title.strip():
        return set()
    try:
        from core.text.normalize import normalize_key
    except Exception as e:  # noqa: BLE001 - fall back to the raw title
        logger.debug("title key failed for %r: %s", title, e)
        return {title.casefold().strip()}
    lowered = title.lower()
    if _CYRILLIC_RE.search(title):
        forms = [_fold_cyrillic(title),
                 lowered.translate(_CYRILLIC_FOLD_ALT),
                 lowered.translate(_CYRILLIC_FOLD_H)]
    else:
        forms = [title]
    clean = getattr(db, "_clean_track_title_for_comparison", None)
    if callable(clean):
        for form in list(forms):
            try:
                cleaned = clean(form)
            except Exception as e:  # noqa: BLE001 - cleaning is best-effort
                logger.debug("title clean failed for %r: %s", form, e)
                continue
            if isinstance(cleaned, str) and cleaned.strip():
                forms.append(cleaned)
    return {key for key in (normalize_key(f) for f in forms) if key}


def _titles_agree(source_title: Any, row_title: Any, db: Any) -> bool:
    if not isinstance(source_title, str) or not isinstance(row_title, str):
        return False
    if not source_title.strip() or not row_title.strip():
        return False
    try:
        source_keys, row_keys = _title_keys(source_title, db), _title_keys(row_title, db)
    except Exception as e:  # noqa: BLE001 - keying must never escape as a crash
        logger.debug("title keys failed for %r/%r: %s", source_title, row_title, e)
        return source_title.casefold().strip() == row_title.casefold().strip()
    if source_keys and row_keys:
        return bool(source_keys & row_keys)
    # A title that keys to nothing (no transliteration available at all)
    # can only agree as the literal string.
    return source_title.casefold().strip() == row_title.casefold().strip()


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
    """Both durations (milliseconds) within the tolerance, under one
    shared coercion: a numeric string counts as its number; anything
    that does not coerce to a positive finite number (None, a bool, a
    non-numeric string, zero/negative, NaN, inf) is UNKNOWN — and an
    unknown duration is no evidence either way, so it agrees. Callers
    guarding a rescue use this instead of hand-rolling the comparison
    (and its TypeError traps) at the call site."""
    source_ms = _as_ms(source_ms_value)
    row_ms = _as_ms(row_ms_value)
    if source_ms is None or row_ms is None:
        return True
    return abs(source_ms - row_ms) <= _DURATION_TOLERANCE_MS


def _durations_agree(track: Any, row: Any) -> bool:
    return durations_agree_ms(_row_value(track, "duration_ms"),
                              _row_value(row, "duration"))


def _row_is_corroborated(row: Any, lookup_id: str, track: Any, db: Any) -> bool:
    """The row the getter returned really is the provider-ID match."""
    row_id = _row_value(row, "id")
    # A real library id is a string/int (Plex ratingKey, Jellyfin GUID,
    # SoulSync row id). Anything else is a stub shape that proves nothing.
    if not isinstance(row_id, (str, int)) or isinstance(row_id, bool):
        return False
    if not str(row_id).strip():
        return False
    # When the row carries the ID it was found by, it must be THIS id —
    # a getter that ignored its argument must not invent a match.
    stored_id = _row_value(row, "spotify_track_id")
    if stored_id is not None and str(stored_id).strip() != lookup_id:
        return False
    if not _titles_agree(getattr(track, "name", None), _row_value(row, "title"), db):
        return False
    return _durations_agree(track, row)


def provider_titles_agree(db: Any, source_title: Any, row_title: Any) -> bool:
    """The corroboration title test, for callers holding a candidate
    from another route (e.g. a live Plex rescue for a stale provider
    row) that must prove it is the same title before riding on the
    provider ID's authority."""
    return _titles_agree(source_title, row_title, db)


def provider_match_rejected(db: Any, profile_id: Any, server_source: str,
                            track: Any, library_track_id: Any) -> bool:
    """Strict rejection test for a provider-ID pairing: True when the
    user unmatched this source track from this library track — AND True
    when that cannot be determined (no rejection reader, a failed
    query, an unreadable result). The lenient ``is_rejected_match``
    fails open by design for its compare-view callers; a provider match
    honored at 1.0 on a maybe-unmatched pairing would silently undo a
    user's unmatch, so here uncertainty means "do not honor". The
    caller falls through to its normal matching either way. Never
    raises."""
    try:
        from core.sync.match_overrides import match_lookup_ids
        source_ids = match_lookup_ids(track)
    except Exception as e:  # noqa: BLE001 - no ids, cannot prove safety
        logger.debug("provider rejection check: lookup ids failed: %s", e)
        return True
    # No profile or no source ids means the rejection query can only
    # ever answer "nothing found" — that is unknown, not safe, so it
    # must not be asked and dressed up as an answer.
    if not profile_id or not source_ids:
        return True
    # The STRICT getter only: the lenient get_server_match_rejections
    # swallows database errors into an empty set, which would read as
    # "no rejections" precisely when the state is unknown — fail-open
    # in a trench coat. A db without the strict getter cannot prove
    # anything either, so that is also "rejected".
    getter = getattr(db, "get_server_match_rejections_strict", None)
    if getter is None:
        return True
    try:
        rejected = getter(profile_id, server_source, source_ids)
    except Exception as e:  # noqa: BLE001 - unknown == do not honor
        logger.debug("provider rejection check failed: %s", e)
        return True
    if rejected is None:
        return True
    try:
        return any((str(s), str(library_track_id)) in rejected for s in source_ids)
    except Exception as e:  # noqa: BLE001 - unreadable == do not honor
        logger.debug("provider rejection result unreadable: %s", e)
        return True


def _call_id_getter(fn: Any, lookup_id: str, server_source: str) -> Any:
    """Call a provider-ID getter the way its signature accepts: the real
    database's getters take ``server_source``, but a foreign/stub getter
    that only accepts the ID must still be asked — with the ID alone.
    Decided from the signature, not from catching TypeError, so a
    TypeError raised INSIDE a real getter is never masked as a
    signature mismatch."""
    try:
        params = inspect.signature(fn).parameters
        accepts_kwarg = "server_source" in params or any(
            p.kind == inspect.Parameter.VAR_KEYWORD for p in params.values())
    except (TypeError, ValueError):  # uninspectable callable: assume the real shape
        accepts_kwarg = True
    if accepts_kwarg:
        return fn(lookup_id, server_source=server_source)
    return fn(lookup_id)


def _rows_for_id(db: Any, lookup_id: str, server_source: str) -> List[Any]:
    """Every library row carrying this ID. A plural getter that answers
    — even with an empty list — is authoritative (the real database's
    singular getter is the same query, so re-asking would just double
    the cost of every miss). The singular getter is only the fallback
    for a plural that is absent, raises, or returns None: a broken
    foreign/stub shape must not hide a row the other shape would have
    found. (A getter that swallows its own errors into an empty list
    — the real database does — reads as an authoritative miss: safe,
    just a miss for that run.) Never raises."""
    plural = getattr(db, "get_tracks_by_spotify_id", None)
    if callable(plural):
        try:
            result = _call_id_getter(plural, lookup_id, server_source)
        except Exception as e:  # noqa: BLE001 - fall through to singular
            logger.debug("provider match plural lookup failed for %s: %s", lookup_id, e)
            result = None
        if isinstance(result, dict):
            return [result]  # one row wearing the wrong shape is still a row
        if result is not None:
            try:
                return list(result)
            except Exception as e:  # noqa: BLE001 - unreadable == fall through
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
    """Every library row whose stored Spotify ID is one of this track's
    IDs and that corroborates itself, in lookup order.

    Tries each of the track's lookup ids (the discovered catalogue id,
    then the playlist's own ``source_track_id`` — see ``match_lookup_ids``)
    in order, and for each ID every library row carrying it, in the
    order the db returns them. Wing-It stub ids are skipped: a stub is
    minted locally and names nothing at Spotify. Callers that act on a
    row (rejection check, materialize, rescue) iterate the list: the
    first corroborated row may be the unmatched or stale one while a
    duplicate further down is the live match. Never raises."""
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
                logger.info(
                    "Provider-ID match: '%s' -> library track %s (spotify id %s)",
                    getattr(track, "name", "?"), _row_value(row, "id"), lookup_id)
                matches.append(row)
            else:
                logger.debug(
                    "Provider-ID candidate for spotify id %s rejected on "
                    "title/duration corroboration", lookup_id)
    return matches


def provider_id_library_match(db: Any, track: Any, server_source: str = None) -> Optional[Any]:
    """The first corroborated row — see ``provider_id_library_matches``."""
    matches = provider_id_library_matches(db, track, server_source)
    return matches[0] if matches else None


__all__ = ["provider_id_library_match", "provider_id_library_matches",
           "provider_match_rejected", "provider_titles_agree",
           "durations_agree_ms"]
