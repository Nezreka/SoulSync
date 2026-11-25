"""the other names the library holds an artist under (#2222).

itunes names Despina Vandi "Δέσποινα Βανδή". the library (and plex) file her
as "Despina Vandi". every check that asks "does the library have this song
by this artist" searched for the greek name, found nothing and said no: the
playlist sync left the track off the server playlist, and the download that
followed filed a second copy under a greek folder.

the library already knows the two are one artist, in two ways:

  * musicbrainz aliases. the enrichment worker stores each artist's
    musicbrainz names on ``lib2_artists.aliases`` (canonical name, sort name,
    every alias), and musicbrainz lists both scripts for an artist like this.
  * spelling. a greek or cyrillic name romanizes to the latin spelling the
    library uses (core.text.cross_script), looked up on the indexed
    ``name_key``.

``equivalent_artist_names`` returns the library artist names a name stands
for under either, never the name itself. the answers are candidate names to
search under: callers still need the title to agree before calling anything
owned.

the alias side needs every artist's alias list, which no index can answer
("whose aliases list this name?"). it is read once into a map and kept for
``INDEX_TTL`` seconds per database, the way the quarantine name maps are.
"""

from __future__ import annotations

import json
import threading
import time
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

from utils.logging_config import get_logger

logger = get_logger("library2.artist_equivalence")

INDEX_TTL = 60.0
_index_cache: Dict[str, Tuple[float, Dict[str, Set[str]]]] = {}
_index_lock = threading.Lock()


def clear_cache() -> None:
    with _index_lock:
        _index_cache.clear()


def _key(name: Any) -> str:
    from core.library2.importer import normalize_name
    return normalize_name(str(name or ""))


def _alias_list(raw: Any) -> List[str]:
    try:
        parsed = json.loads(raw) if isinstance(raw, str) else raw
    except (TypeError, ValueError):
        return []
    if not isinstance(parsed, list):
        return []
    return [str(n).strip() for n in parsed if isinstance(n, str) and n.strip()]


def _build_index(conn) -> Dict[str, Set[str]]:
    """``{name key: library artist names}``: each artist under every
    musicbrainz alias it carries, and every greek/cyrillic artist under its
    latin spellings."""
    from core.text.cross_script import has_romanizable_letters, romanized_forms
    index: Dict[str, Set[str]] = {}

    def add(key: str, name: str) -> None:
        if key and name and key != _key(name):
            index.setdefault(key, set()).add(name)

    try:
        rows = conn.execute(
            "SELECT name, aliases FROM lib2_artists "
            "WHERE aliases IS NOT NULL AND aliases NOT IN ('', '[]')").fetchall()
    except Exception as exc:  # noqa: BLE001 - a library from before the aliases column
        logger.debug("artist alias index: aliases unreadable: %s", exc)
        rows = []
    for name, aliases in rows:
        for alias in _alias_list(aliases):
            add(_key(alias), name)
    # names with a non-ascii letter only; the GLOB runs in sqlite, so a latin
    # library never reaches python here
    for (name,) in conn.execute(
            "SELECT name FROM lib2_artists WHERE name GLOB ?", ("*[^ -~]*",)).fetchall():
        if has_romanizable_letters(name):
            for form in romanized_forms(name):
                add(_key(form), name)
    return index


def _alias_index(conn, cache_key: Optional[str]) -> Dict[str, Set[str]]:
    now = time.monotonic()
    if cache_key:
        with _index_lock:
            hit = _index_cache.get(cache_key)
            if hit and now - hit[0] < INDEX_TTL:
                return hit[1]
    index = _build_index(conn)
    if cache_key and INDEX_TTL > 0:
        with _index_lock:
            if len(_index_cache) > 64:
                _index_cache.clear()
            _index_cache[cache_key] = (now, index)
    return index


def _names_for_keys(conn, keys: Iterable[str]) -> List[str]:
    keys = [k for k in dict.fromkeys(keys) if k]
    if not keys:
        return []
    marks = ",".join("?" for _ in keys)
    return [r[0] for r in conn.execute(
        f"SELECT name FROM lib2_artists WHERE name_key IN ({marks}) ORDER BY id",  # noqa: S608 - placeholders only
        keys).fetchall() if r[0]]


def equivalent_artist_names(conn, name: Any, *, cache_key: Optional[str] = None) -> List[str]:
    """the library artist names ``name`` is the same artist as, under another
    spelling or script. empty when there are none, and on any error."""
    from core.text.cross_script import romanized_forms
    key = _key(name)
    if not key:
        return []
    found: List[str] = []
    try:
        spellings = [_key(form) for form in romanized_forms(str(name))]
        # the library artist under the latin spelling
        found += _names_for_keys(conn, spellings)
        # the musicbrainz names this artist's own row carries
        own_aliases: List[str] = []
        for (raw,) in conn.execute(
                "SELECT aliases FROM lib2_artists WHERE name_key = ?", (key,)).fetchall():
            own_aliases += _alias_list(raw)
        found += _names_for_keys(conn, [_key(a) for a in own_aliases])
        # artists whose musicbrainz names include this one, or whose own
        # name romanizes to it
        index = _alias_index(conn, cache_key)
        for lookup in (key, *spellings):
            found += sorted(index.get(lookup, ()))
    except Exception as exc:  # noqa: BLE001 - no equivalence is the old behaviour
        logger.debug("artist equivalence for %r skipped: %s", name, exc)
        return []
    return [n for n in dict.fromkeys(found) if n and _key(n) != key]


__all__ = ["INDEX_TTL", "clear_cache", "equivalent_artist_names"]
