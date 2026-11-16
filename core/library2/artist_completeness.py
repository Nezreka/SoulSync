"""How complete each library artist's collection is (#2106, #2107).

The artist list already shows "owned / total" per row, worked out live for
the 75 artists on screen. Sorting or filtering by it needs the same numbers
for every artist, and that is one pass over every track (~5 s on a 311k-track
library), so the answer is kept until the library changes.

The rule is the artist list's own (``artist_reader.read_artist_page``):

- a track counts when it is wanted (or monitored) or has a file that is not
  deleted, so a release the artist's collection settings leave out is never
  "missing";
- it is present when a file is there and not confirmed missing.

An artist is only complete when something is expected, all of it is here and
every release of theirs has a known size. A release nobody has asked a
provider about yet (``expected_track_count`` is NULL) can hide missing tracks,
so that artist reads "not checked", never 100%.

Kept per scope and per member counter (core.library2.member_version), which
moves on every write that can change these numbers. When it has moved, the
old answer is served while a new one is worked out in the background: the
numbers on screen are always the live ones, this only decides the order and
the filters.
"""
from __future__ import annotations

import threading
import time
from contextlib import closing
from dataclasses import dataclass
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple

from utils.logging_config import get_logger

logger = get_logger("library2.artist_completeness")

COMPLETENESS_SORTS = ("completion", "missing", "upgrades", "incomplete_albums")
COMPLETION_FILTERS = ("complete", "incomplete", "none_owned", "unchecked")


@dataclass(frozen=True)
class Completeness:
    total: int = 0
    present: int = 0
    incomplete_albums: int = 0
    unverified_albums: int = 0

    @property
    def missing(self) -> int:
        return max(0, self.total - self.present)

    @property
    def percent(self) -> Optional[float]:
        """owned share of what is expected, or None when nothing is"""
        if self.total <= 0:
            return None
        return min(100.0, self.present * 100.0 / self.total)

    @property
    def state(self) -> str:
        return completion_state(self.total, self.present, self.unverified_albums)


def completion_state(total: int, present: int, unverified_albums: int = 0) -> str:
    """complete | incomplete | unchecked | unknown.

    unknown: nothing is expected, so there is nothing to be complete against.
    unchecked: everything expected is here, but a release's size was never
    looked up, so it may not be everything."""
    total, present = int(total or 0), int(present or 0)
    if total <= 0:
        return "unknown"
    if present < total:
        return "incomplete"
    if int(unverified_albums or 0) > 0:
        return "unchecked"
    return "complete"


def _scope_parts() -> Dict[str, Any]:
    """The scope's pieces of SQL, read on the request thread (a background
    refresh has no request to read them from)."""
    from .sql_util import intent_profile_id, monitored_sql, owner_clause, scope_visibility_sql
    return {
        "intent": int(intent_profile_id()),
        "track_monitored": monitored_sql("track", "t"),
        "album_monitored": monitored_sql("album", "al"),
        "tf_owner": owner_clause(column="tf.owner_profile_id"),
        "album_visible": scope_visibility_sql("album", "al") or "1=1",
    }


# one row per counted track: the same CASEs as read_artist_page's track_stats
_TRACKS_SQL = """
    SELECT t.id, t.album_id,
           MAX(CASE WHEN tf.id IS NOT NULL
                     AND COALESCE(tf.file_state, 'active')
                         NOT IN ('missing_confirmed', 'deleted')
                    THEN 1 ELSE 0 END) AS present
      FROM lib2_tracks t
      LEFT JOIN lib2_wanted_tracks w ON w.track_id = t.id AND w.profile_id = {intent}
      LEFT JOIN lib2_track_files tf ON tf.track_id = t.id{tf_owner}
     GROUP BY t.id
    HAVING MAX(CASE WHEN COALESCE(w.wanted, {track_monitored}) = 1
                      OR (tf.id IS NOT NULL AND COALESCE(tf.file_state, 'active') <> 'deleted')
                    THEN 1 ELSE 0 END) = 1
"""

# who is credited on each track, folded onto the canonical artist
_CREDITS_SQL = """
    SELECT ta.track_id, COALESCE(m.canonical_artist_id, m.id)
      FROM lib2_track_artists ta
      JOIN lib2_artists m ON m.id = ta.artist_id
"""

# the artist's own releases, as the list counts them (album artist or a
# primary album credit), that the library holds or wants
_OWN_RELEASES_SQL = """
    SELECT COALESCE(m.canonical_artist_id, m.id), al.id, al.expected_track_count IS NULL
      FROM lib2_album_artists aa
      JOIN lib2_artists m ON m.id = aa.artist_id
      JOIN lib2_albums al ON al.id = aa.album_id
     WHERE aa.role = 'primary' AND (al.origin = 'library' OR {album_monitored} = 1)
       AND {album_visible}
    UNION
    SELECT COALESCE(m.canonical_artist_id, m.id), al.id, al.expected_track_count IS NULL
      FROM lib2_albums al
      JOIN lib2_artists m ON m.id = al.primary_artist_id
     WHERE (al.origin = 'library' OR {album_monitored} = 1) AND {album_visible}
"""


@dataclass(frozen=True)
class LibraryFacts:
    artists: Dict[int, Completeness]
    owned_tracks: int
    missing_tracks: int


def compute(conn, parts: Optional[Dict[str, Any]] = None) -> LibraryFacts:
    """Every artist's completeness, worked out now."""
    parts = parts or _scope_parts()
    tracks: Dict[int, Tuple[int, int]] = {}
    album_total: Dict[int, int] = {}
    album_present: Dict[int, int] = {}
    owned = 0
    for track_id, album_id, present in conn.execute(_TRACKS_SQL.format(**parts)):
        present = 1 if present else 0
        tracks[int(track_id)] = (int(album_id or 0), present)
        owned += present
        if album_id is not None:
            album_total[album_id] = album_total.get(album_id, 0) + 1
            album_present[album_id] = album_present.get(album_id, 0) + present

    total: Dict[int, int] = {}
    present_by: Dict[int, int] = {}
    seen = set()
    for track_id, artist_id in conn.execute(_CREDITS_SQL):
        if artist_id is None:
            continue
        fact = tracks.get(int(track_id))
        if fact is None:
            continue
        pair = (int(artist_id), int(track_id))
        # an alias member and its canonical artist both credited: one track
        if pair in seen:
            continue
        seen.add(pair)
        total[pair[0]] = total.get(pair[0], 0) + 1
        present_by[pair[0]] = present_by.get(pair[0], 0) + fact[1]

    incomplete: Dict[int, int] = {}
    unverified: Dict[int, int] = {}
    for artist_id, album_id, unknown_size in conn.execute(_OWN_RELEASES_SQL.format(**parts)):
        if artist_id is None:
            continue
        artist_id = int(artist_id)
        expected = album_total.get(album_id, 0)
        if expected and album_present.get(album_id, 0) < expected:
            incomplete[artist_id] = incomplete.get(artist_id, 0) + 1
        if unknown_size:
            unverified[artist_id] = unverified.get(artist_id, 0) + 1

    artists = {
        artist_id: Completeness(
            total=total.get(artist_id, 0),
            present=present_by.get(artist_id, 0),
            incomplete_albums=incomplete.get(artist_id, 0),
            unverified_albums=unverified.get(artist_id, 0))
        for artist_id in set(total) | set(incomplete) | set(unverified)
    }
    return LibraryFacts(artists=artists, owned_tracks=owned,
                        missing_tracks=len(tracks) - owned)


# -- the kept answer ---------------------------------------------------------

_lock = threading.Lock()
_cache: Dict[Any, Tuple[Any, LibraryFacts]] = {}
_refreshing: set = set()
_cold: Dict[Any, threading.Lock] = {}


def _version(conn) -> Any:
    from .member_version import current_version
    version = current_version(conn)
    # no counter: a minute's answer at most
    return version if version is not None else ("t", int(time.monotonic() // 60))


def _key(conn, parts: Dict[str, Any]) -> Optional[Tuple[Any, ...]]:
    from .member_version import _file_ident
    ident = _file_ident(conn)
    if ident is None:
        return None
    return (ident, tuple(sorted(parts.items())))


def _spawn(target: Callable[[], None]) -> None:
    threading.Thread(target=target, name="lib2-completeness-refresh", daemon=True).start()


def _refresh_in_background(key, parts: Dict[str, Any]) -> None:
    with _lock:
        if key in _refreshing:
            return
        _refreshing.add(key)

    def run():
        try:
            from database.music_database import get_database
            with closing(get_database()._get_connection()) as conn:
                if _key(conn, parts) != key:
                    return
                version = _version(conn)
                facts = compute(conn, parts)
                _cache[key] = (version, facts)
        except Exception as exc:  # noqa: BLE001 - the kept answer stays until the next try
            logger.debug("artist completeness refresh failed: %s", exc)
        finally:
            with _lock:
                _refreshing.discard(key)

    _spawn(run)


def library_facts(conn) -> LibraryFacts:
    """Every artist's completeness: kept, refreshed behind the page when the
    library has changed, worked out on the spot the first time."""
    parts = _scope_parts()
    key = _key(conn, parts)
    if key is None:
        return compute(conn, parts)
    version = _version(conn)
    hit = _cache.get(key)
    if hit is not None:
        if hit[0] != version:
            _refresh_in_background(key, parts)
        return hit[1]
    # one cold pass at a time: the rest wait and read what it kept
    with _lock:
        cold = _cold.setdefault(key, threading.Lock())
    with cold:
        hit = _cache.get(key)
        if hit is not None:
            return hit[1]
        facts = compute(conn, parts)
        _cache[key] = (version, facts)
        return facts


def artist_completeness(conn) -> Dict[int, Completeness]:
    return library_facts(conn).artists


def forget() -> None:
    _cache.clear()


# -- filters and sorts -------------------------------------------------------

def _as_int(value: Any) -> Optional[int]:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def completeness_filters(*, completion: str = "", missing_min: Any = None,
                         missing_max: Any = None, pct_min: Any = None,
                         pct_max: Any = None) -> Dict[str, Any]:
    """The completeness filters asked for, cleaned; empty when there are none."""
    out: Dict[str, Any] = {}
    if completion in COMPLETION_FILTERS:
        out["completion"] = completion
    for name, value in (("missing_min", missing_min), ("missing_max", missing_max)):
        number = _as_int(value)
        if number is not None and number >= 0:
            out[name] = number
    for name, value in (("pct_min", pct_min), ("pct_max", pct_max)):
        number = _as_int(value)
        if number is not None:
            out[name] = max(0, min(100, number))
    # an open range is no filter at all
    if out.get("pct_min") == 0 and "pct_max" not in out:
        out.pop("pct_min")
    if out.get("pct_max") == 100 and "pct_min" not in out:
        out.pop("pct_max")
    return out


def matches(fact: Completeness, filters: Dict[str, Any]) -> bool:
    completion = filters.get("completion")
    if completion == "complete" and fact.state != "complete":
        return False
    if completion == "incomplete" and fact.state != "incomplete":
        return False
    if completion == "none_owned" and not (fact.total > 0 and fact.present == 0):
        return False
    # incomplete catalogue data: nothing expected, or a release of unknown size
    if completion == "unchecked" and not (fact.total <= 0 or fact.unverified_albums > 0):
        return False
    if "missing_min" in filters and fact.missing < filters["missing_min"]:
        return False
    if "missing_max" in filters and fact.missing > filters["missing_max"]:
        return False
    if "pct_min" in filters or "pct_max" in filters:
        percent = fact.percent
        if percent is None:
            return False
        # whole percents, the way the slider reads: 99.6% is not 100
        whole = int(percent)
        if whole < filters.get("pct_min", 0) or whole > filters.get("pct_max", 100):
            return False
    return True


# natural direction per sort: least complete first, most missing first
_NATURAL_DESC = {"completion": False, "missing": True, "upgrades": True,
                 "incomplete_albums": True}


def order_ids(ids: Iterable[int], sort: str, direction: str,
              facts: Dict[int, Completeness],
              upgrades: Optional[Dict[int, int]] = None) -> List[int]:
    """``ids`` (already in name order) by a completeness sort. A stable sort,
    so ties stay in name order; an artist with nothing to measure is last
    either way."""
    ids = list(ids)
    descending = _NATURAL_DESC.get(sort, False)
    if direction == "asc":
        descending = False
    elif direction == "desc":
        descending = True
    empty = Completeness()

    def value(artist_id: int) -> Optional[float]:
        fact = facts.get(artist_id, empty)
        if sort == "completion":
            return fact.percent
        if sort == "missing":
            return float(fact.missing) if fact.total > 0 else None
        if sort == "incomplete_albums":
            return float(fact.incomplete_albums)
        if sort == "upgrades":
            return float((upgrades or {}).get(artist_id, 0))
        return None

    keyed = [(value(i), i) for i in ids]
    measured = [pair for pair in keyed if pair[0] is not None]
    unmeasured = [i for v, i in keyed if v is None]
    measured.sort(key=lambda pair: pair[0], reverse=descending)
    return [i for _v, i in measured] + unmeasured


__all__ = [
    "COMPLETENESS_SORTS", "COMPLETION_FILTERS", "Completeness", "LibraryFacts",
    "artist_completeness", "completeness_filters", "completion_state", "compute",
    "forget", "library_facts", "matches", "order_ids",
]
