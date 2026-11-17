"""tracks no catalogue has: bootlegs, dj edits, unofficial remixes (#2097).

a playlist track discovery cannot match gets a wing it stub and waits in the
pool for a person. for a bootleg no catalogue match is ever coming, so it
waited forever. a playlist with "find uncatalogued tracks" on lets its stubs
through instead:

* the stub goes on the wishlist and is searched by the playlist's own artist
  and title, on every source the downloads use, and again on later runs
* a candidate has to pass ``strict_match`` on top of the usual scoring: the
  whole title, every credit in its brackets ("MIZE Remix") and an artist in
  the file's name, no version the playlist did not ask for, and the length
  close when both are known. the original recording never stands in for a
  remix of it
* the file is tagged from the playlist's own text and nothing looks it up by
  name afterwards (core/metadata/manual.py), so it is not retagged as the
  studio release

which stubs are allowed is read from the playlists, not carried on the
track: turning the switch off stops the searching at once.
"""

from __future__ import annotations

import re
import threading
import time
import unicodedata
from typing import Any, FrozenSet, Iterable, List, Tuple

from utils.logging_config import get_logger
from core.matching.artist_credit import split_names

logger = get_logger("playlists.uncatalogued")

COLUMN = "allow_uncatalogued"
_TTL = 30.0
_lock = threading.Lock()
_cache: dict = {"at": 0.0, "ids": frozenset(), "db": None}

# a version the file names and the playlist did not ask for: a different recording
_OTHER_VERSION = frozenset({
    "remix", "rmx", "bootleg", "flip", "vip", "mashup", "rework", "refix", "live",
    "acoustic", "instrumental", "karaoke", "cover", "slowed", "sped", "nightcore",
    "reverb", "8d", "demo", "acapella", "acappella",
})
# said in a credit without naming anything
_FILLER = frozenset({"feat", "ft", "featuring", "with", "prod", "by", "the", "a", "an", "and"})
_BRACKETS = re.compile(r"[\(\[\{]([^\)\]\}]*)[\)\]\}]")

# how far a file's length may be from the playlist's
LENGTH_SLACK_SECONDS = 7.0
LENGTH_SLACK_SHARE = 0.03


def _fold(text: Any) -> str:
    plain = unicodedata.normalize("NFKD", str(text or ""))
    plain = "".join(c for c in plain if not unicodedata.combining(c))
    return plain.casefold()


def tokens(text: Any) -> List[str]:
    """the words of a name: folded, accents off, punctuation gone"""
    return re.findall(r"[a-z0-9]+", _fold(text).replace("'", "").replace("’", ""))


def title_parts(title: Any) -> Tuple[List[str], List[List[str]]]:
    """(the words of the title itself, the words of each credit in brackets
    or after a dash)"""
    text = str(title or "")
    credits = [tokens(group) for group in _BRACKETS.findall(text)]
    core = _BRACKETS.sub(" ", text)
    if " - " in core:
        core, _dash, tail = core.partition(" - ")
        credits.append(tokens(tail))
    return tokens(core), [c for c in credits if c]


def _artists(artist: Any) -> List[List[str]]:
    names: Iterable[Any] = artist if isinstance(artist, (list, tuple)) else [artist]
    out = []
    for name in names:
        if isinstance(name, dict):
            name = name.get("name")
        for part in split_names(str(name or "")):
            words = tokens(part)
            if words:
                out.append(words)
    return out


def strict_match(artist: Any, title: Any, candidate_text: Any, *,
                 wanted_ms: Any = 0, candidate_ms: Any = 0) -> Tuple[bool, str]:
    """is this file the recording the playlist names? (yes or no, why not).

    ``candidate_text`` is everything the candidate says about itself: a
    soulseek path, or a stream's artist and title."""
    have = set(tokens(candidate_text))
    core, credits = title_parts(title)
    if not core and not credits:
        return False, "the playlist gives no title"
    missing = [w for w in core if w not in have]
    if missing:
        return False, f"title word missing: {' '.join(missing)}"
    for credit in credits:
        gone = [w for w in credit if w not in _FILLER and w not in have]
        if gone:
            return False, f"credit missing: {' '.join(credit)}"
    wanted_words = set(core) | {w for c in credits for w in c}
    extra = sorted((have & _OTHER_VERSION) - wanted_words)
    if extra:
        return False, f"a different version: {' '.join(extra)}"
    artists = _artists(artist)
    if artists and not any(all(w in have for w in words) for words in artists):
        return False, "none of the artists is in the name"
    try:
        want, got = float(wanted_ms or 0), float(candidate_ms or 0)
    except (TypeError, ValueError):
        want = got = 0.0
    if want > 0 and got > 0:
        slack = max(LENGTH_SLACK_SECONDS * 1000.0, want * LENGTH_SLACK_SHARE)
        if abs(want - got) > slack:
            return False, f"length is {abs(want - got) / 1000.0:.0f}s off"
    return True, ""


def candidate_text(candidate: Any) -> str:
    """what a download candidate says about itself"""
    parts = [getattr(candidate, name, None) for name in ("artist", "title", "filename", "album")]
    return " ".join(str(p) for p in parts if p)


def candidate_ms(candidate: Any) -> float:
    """a candidate's length in milliseconds, 0 when it gives none. sources
    report seconds or milliseconds; a track is never 20 minutes of seconds."""
    try:
        value = float(getattr(candidate, "duration", 0) or 0)
    except (TypeError, ValueError):
        return 0.0
    if value <= 0:
        return 0.0
    return value if value > 20000 else value * 1000.0


def filter_candidates(candidates: Iterable[Any], track: Any) -> List[Any]:
    """the candidates that are the recording the playlist names"""
    artists = list(getattr(track, "artists", None) or [])
    title = getattr(track, "name", "")
    wanted_ms = getattr(track, "duration_ms", 0) or 0
    kept = []
    for candidate in candidates or []:
        ok, why = strict_match(artists, title, candidate_text(candidate),
                               wanted_ms=wanted_ms, candidate_ms=candidate_ms(candidate))
        if ok:
            kept.append(candidate)
        else:
            logger.info("uncatalogued '%s': not %s (%s)", title,
                        getattr(candidate, "filename", None) or getattr(candidate, "title", "?"), why)
    return kept


# ── which stubs are allowed ──────────────────────────────────────────────────

def forget() -> None:
    """a playlist's switch changed: read the allowed stubs again"""
    with _lock:
        _cache.update(at=0.0, ids=frozenset(), db=None)


def _read(db: Any) -> FrozenSet[str]:
    with db._get_connection() as conn:
        rows = conn.execute(
            "SELECT DISTINCT json_extract(t.extra_data, '$.matched_data.id') "
            "FROM mirrored_playlist_tracks t "
            "JOIN mirrored_playlists p ON p.id = t.playlist_id "
            f"WHERE p.{COLUMN} = 1 "
            "AND t.extra_data LIKE '%wing_it_%' "
            "AND json_valid(t.extra_data)"
        ).fetchall()
    return frozenset(str(r[0]) for r in rows if r[0] and str(r[0]).startswith("wing_it_"))


def allowed_stub_ids(db: Any = None) -> FrozenSet[str]:
    """the wing it stubs that sit in a playlist with the switch on. read
    again every half minute, and at once after ``forget``."""
    now = time.monotonic()
    with _lock:
        if _cache["db"] is not None and (db is None or _cache["db"] is db) \
                and now - _cache["at"] < _TTL:
            return _cache["ids"]
    try:
        if db is None:
            from database.music_database import get_database
            db = get_database()
        ids = _read(db)
    except Exception as e:  # noqa: BLE001 - unreadable means nothing is allowed
        logger.debug("allowed uncatalogued stubs unreadable: %s", e)
        return frozenset()
    with _lock:
        _cache.update(at=now, ids=ids, db=db)
    return ids


def is_allowed(stub_id: Any, db: Any = None) -> bool:
    """is this the stub of a track in a playlist that finds uncatalogued tracks?"""
    sid = str(stub_id or "")
    return sid.startswith("wing_it_") and sid in allowed_stub_ids(db)


def track_is_uncatalogued(track: Any, db: Any = None) -> bool:
    """a download's track (object or dict) is one of those"""
    if isinstance(track, dict):
        return is_allowed(track.get("id"), db)
    return is_allowed(getattr(track, "id", None), db)


__all__ = [
    "COLUMN", "allowed_stub_ids", "candidate_ms", "candidate_text", "filter_candidates",
    "forget", "is_allowed", "strict_match", "title_parts", "tokens", "track_is_uncatalogued",
]
