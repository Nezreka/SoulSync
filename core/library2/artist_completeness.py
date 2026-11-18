"""How complete each library artist's collection is (#2106, #2107).

The artist list already shows "owned / total" per row, worked out live for
the 75 artists on screen. Sorting or filtering by it needs the same numbers
for every artist, and that is one pass over every track (~6 s on a 311k-track
library), so the answer is kept until the library changes.

One rule (``rule_ctes``), read by the list's live numbers
(``artist_reader.read_artist_page``) and by the kept pass here:

- an artist on the Watchlist: a track counts when it is wanted (or
  monitored) or has a file that is not deleted, so a release their collection
  settings leave out is never "missing";
- an artist nobody watches wants nothing, so for them it's the albums you
  have: every release of theirs with anything on disk counts whole (#2106
  follow-up), plus anything wanted;
- a track is present when a file is there and not confirmed missing.

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
        "artist_monitored": monitored_sql("artist", "wa"),
        "track_monitored": monitored_sql("track", "t"),
        "album_monitored": monitored_sql("album", "al"),
        "tf_owner": owner_clause(column="tf.owner_profile_id"),
        "album_visible": scope_visibility_sql("album", "al") or "1=1",
    }


# The rule, as SQL, read by the list's live numbers (artist_reader's page) and
# by the kept pass below, so the cards and the sorts can't disagree.
#
# A track is counted when it is wanted (or monitored) or has a file that is
# not deleted, and present when that file is not confirmed missing. That is
# the whole rule for an artist on the Watchlist: their collection settings
# say what is wanted.
#
# An artist nobody watches wants nothing, so that rule only ever counted what
# is owned ("1 / 1, complete" beside a 14 track album with one track here).
# For them it's the albums you have: every release of theirs (the album's
# primary artist) with anything on disk counts whole, its size the larger of
# what the provider said and the rows we hold, and every track of theirs on
# another album they hold something from counts too. A release they hold
# nothing of only counts what is wanted on it.
#
# Watched means the artist, or any alias in its group, is monitored the way
# this library reads it (the Watchlist mirror for the shared library, the
# profile's own rule for an own library).
#
# ``{members}`` is a CTE of (member_id, canonical_id): every artist for the
# kept pass, the page's alias groups for the list. ``{scope_tracks}`` picks
# which tracks to look at: all of them for the kept pass, the ones the page's
# artists are credited on or hold a release of for the list.
_RULE_CTES = """
    cc_watch AS MATERIALIZED (
        SELECT cm.canonical_id AS artist_id, COALESCE(MAX({artist_monitored}), 0) AS watched
          FROM {members} cm
          CROSS JOIN lib2_artists wa ON wa.id = cm.member_id
         GROUP BY cm.canonical_id
    ),
    -- the artist's own releases, as the list counts them (album artist or a
    -- primary album credit). lead: the album's primary artist, the only
    -- claim strong enough to count the whole release (the junction's
    -- 'primary' role also sits on guests of compilations)
    cc_release AS MATERIALIZED (
        SELECT r.artist_id, r.album_id, MAX(r.lead) AS lead,
               MAX(al.expected_track_count IS NULL) AS unknown_size,
               MAX(COALESCE(al.expected_track_count, 0)) AS expected,
               MAX(al.origin = 'library' OR {album_monitored} = 1) AS listed
          FROM (SELECT cm.canonical_id AS artist_id, aa.album_id, 0 AS lead
                  FROM {members} cm
                  CROSS JOIN lib2_album_artists aa ON aa.artist_id = cm.member_id
                 WHERE aa.role = 'primary'
                UNION ALL
                SELECT cm.canonical_id, al.id, 1
                  FROM {members} cm
                  CROSS JOIN lib2_albums al ON al.primary_artist_id = cm.member_id) r
          CROSS JOIN lib2_albums al ON al.id = r.album_id
         WHERE {album_visible}
         GROUP BY r.artist_id, r.album_id
    ),
    -- one row per track looked at: counted by the wanted rule, held (a file
    -- that is not deleted), present (and not confirmed missing)
    cc_track AS MATERIALIZED (
        SELECT t.id AS track_id, t.album_id,
               MAX(CASE WHEN COALESCE(w.wanted, {track_monitored}) = 1
                          OR (tf.id IS NOT NULL AND COALESCE(tf.file_state, 'active') <> 'deleted')
                        THEN 1 ELSE 0 END) AS counted,
               MAX(CASE WHEN tf.id IS NOT NULL
                         AND COALESCE(tf.file_state, 'active') <> 'deleted'
                        THEN 1 ELSE 0 END) AS held,
               MAX(CASE WHEN tf.id IS NOT NULL
                         AND COALESCE(tf.file_state, 'active')
                             NOT IN ('missing_confirmed', 'deleted')
                        THEN 1 ELSE 0 END) AS present
          FROM {scope_tracks}
          LEFT JOIN lib2_wanted_tracks w ON w.track_id = t.id AND w.profile_id = {intent}
          LEFT JOIN lib2_track_files tf ON tf.track_id = t.id{tf_owner}
         GROUP BY t.id
    ),
    cc_album AS MATERIALIZED (
        SELECT album_id, COUNT(*) AS track_rows, SUM(present) AS present,
               SUM(counted) AS counted, SUM(counted * present) AS counted_present,
               MAX(held) AS held
          FROM cc_track
         WHERE album_id IS NOT NULL
         GROUP BY album_id
    ),
    -- an unwatched artist's releases that count whole
    cc_whole AS MATERIALIZED (
        SELECT r.artist_id, r.album_id,
               MAX(r.expected, a.track_rows) AS size, a.present
          FROM cc_release r
          JOIN cc_watch cw ON cw.artist_id = r.artist_id AND cw.watched = 0
          JOIN cc_album a ON a.album_id = r.album_id AND a.held = 1
         WHERE r.lead = 1
    ),
    -- the artist's credited tracks per album, an alias and its canonical
    -- artist both credited being one track
    cc_credit AS (
        SELECT c.artist_id, ct.album_id, COUNT(*) AS credited,
               SUM(ct.counted) AS counted, SUM(ct.present) AS present, MAX(ct.held) AS held
          FROM (SELECT DISTINCT cm.canonical_id AS artist_id, ta.track_id
                  FROM {members} cm
                  CROSS JOIN lib2_track_artists ta ON ta.artist_id = cm.member_id) c
          JOIN cc_track ct ON ct.track_id = c.track_id
         GROUP BY c.artist_id, ct.album_id
    ),
    cc_tally AS (
        SELECT c.artist_id,
               SUM(CASE WHEN cw.watched = 0 AND c.held = 1 AND c.album_id IS NOT NULL
                        THEN c.credited ELSE c.counted END) AS total,
               SUM(c.present) AS present
          FROM cc_credit c
          JOIN cc_watch cw ON cw.artist_id = c.artist_id
         WHERE NOT EXISTS (SELECT 1 FROM cc_whole wh
                            WHERE wh.artist_id = c.artist_id AND wh.album_id = c.album_id)
         GROUP BY c.artist_id
        UNION ALL
        SELECT artist_id, SUM(size), SUM(present) FROM cc_whole GROUP BY artist_id
    ),
    -- per release: one whose size nobody has looked up can hide missing
    -- tracks, so the artist is not "complete"; and how many are short
    cc_release_stats AS (
        SELECT r.artist_id,
               SUM(r.listed * r.unknown_size) AS unverified_albums,
               SUM(CASE WHEN wh.album_id IS NOT NULL THEN wh.present < wh.size
                        WHEN r.listed = 1 AND a.counted > 0 THEN a.counted_present < a.counted
                        ELSE 0 END) AS incomplete_albums
          FROM cc_release r
          LEFT JOIN cc_whole wh ON wh.artist_id = r.artist_id AND wh.album_id = r.album_id
          LEFT JOIN cc_album a ON a.album_id = r.album_id
         GROUP BY r.artist_id
    ),
    cc_completeness AS (
        SELECT cw.artist_id,
               COALESCE(tl.total, 0) AS total, COALESCE(tl.present, 0) AS present,
               COALESCE(rs.unverified_albums, 0) AS unverified_albums,
               COALESCE(rs.incomplete_albums, 0) AS incomplete_albums,
               cw.watched
          FROM cc_watch cw
          LEFT JOIN (SELECT artist_id, SUM(total) AS total, SUM(present) AS present
                       FROM cc_tally GROUP BY artist_id) tl ON tl.artist_id = cw.artist_id
          LEFT JOIN cc_release_stats rs ON rs.artist_id = cw.artist_id
    )"""

# the kept pass: every artist, every track
_ALL_MEMBERS = """
    cc_members AS MATERIALIZED (
        SELECT id AS member_id, COALESCE(canonical_artist_id, id) AS canonical_id
          FROM lib2_artists
    ),"""

# the list's page: the tracks its artists are credited on, and every track of
# the releases that are theirs. each step is an index seek from the page
_PAGE_SCOPE_TRACKS = """(
            SELECT ta.track_id AS id
              FROM {members} cm
              CROSS JOIN lib2_track_artists ta ON ta.artist_id = cm.member_id
            UNION
            SELECT st.id
              FROM cc_release r
              CROSS JOIN lib2_tracks st ON st.album_id = r.album_id
          ) s
          CROSS JOIN lib2_tracks t ON t.id = s.id"""


def rule_ctes(members: str, parts: Optional[Dict[str, Any]] = None, *,
              whole_library: bool = False) -> str:
    """The completeness rule as CTEs ending in ``cc_completeness`` (artist_id,
    total, present, unverified_albums, incomplete_albums, watched), one row per
    canonical artist in ``members``. Splice after ``WITH`` (or after the
    caller's own CTEs and a comma)."""
    parts = parts or _scope_parts()
    scope_tracks = "lib2_tracks t" if whole_library else _PAGE_SCOPE_TRACKS.format(members=members)
    return _RULE_CTES.format(members=members, scope_tracks=scope_tracks, **parts)


_LIBRARY_SQL = "WITH" + _ALL_MEMBERS + "{rule}" + """
    SELECT artist_id, total, present, incomplete_albums, unverified_albums
      FROM cc_completeness
    UNION ALL
    -- the whole library's wanted tracks, owned and not (the overview, which
    -- opens the Wanted list on the missing ones)
    SELECT NULL, SUM(counted), SUM(counted * present), 0, 0 FROM cc_track
"""


def library_sql(parts: Optional[Dict[str, Any]] = None) -> str:
    return _LIBRARY_SQL.format(rule=rule_ctes("cc_members", parts, whole_library=True))


@dataclass(frozen=True)
class LibraryFacts:
    artists: Dict[int, Completeness]
    owned_tracks: int
    missing_tracks: int


def compute(conn, parts: Optional[Dict[str, Any]] = None) -> LibraryFacts:
    """Every artist's completeness, worked out now."""
    artists: Dict[int, Completeness] = {}
    counted = owned = 0
    for artist_id, total, present, incomplete, unverified in conn.execute(library_sql(parts)):
        if artist_id is None:
            counted, owned = int(total or 0), int(present or 0)
            continue
        if not (total or incomplete or unverified):
            continue
        artists[int(artist_id)] = Completeness(
            total=int(total or 0), present=int(present or 0),
            incomplete_albums=int(incomplete or 0), unverified_albums=int(unverified or 0))
    return LibraryFacts(artists=artists, owned_tracks=owned, missing_tracks=counted - owned)


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
