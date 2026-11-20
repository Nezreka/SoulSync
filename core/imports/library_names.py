"""The name a download is filed and tagged under: the library's (#2165).

nstrelow's library files Machine Gun Kelly as "mgk". Deezer, the download
source, still says "Machine Gun Kelly". The importer already knew it was one
artist: it finds the library artist by provider id first (importer
``_ArtistResolver``), so the new tracks joined the mgk row. Only the folder
and the tags were built from Deezer's name, so every release landed in a
second "Machine Gun Kelly/" folder and media servers showed two artists.

The rule: a download is named after the library artist it belongs to, found
the way the importer finds it.

  * by id. Any provider id the download carries (Deezer, Spotify,
    MusicBrainz, ...) that a library artist holds says it is that artist,
    whatever the source calls them. Automatic, no setup.
  * by name, only for a pinned artist. A name says nothing new on its own
    (the source's name already is the folder), but someone who pinned a
    spelling on the artist page wants that spelling, and the importer files
    a same-named download under that artist. Only when exactly one library
    artist goes by that name, so two Nirvanas are never guessed between.
  * by another spelling of the name (#2222), when no library artist goes by
    the source's: itunes says "Δέσποινα Βανδή", the library holds "Despina
    Vandi" (the greek romanized, or a musicbrainz alias the library stored,
    core.library2.artist_equivalence). Only when exactly one library artist
    answers, so the download joins it instead of a second folder.

The library's name is its effective name: a name set on the artist page
(the ``name`` metadata override, ARCH-04) wins over the scanned one, the same
value the artist page, retag and reorganize already use.

Never a merge. The answer is always the name of the artist the file is
catalogued under, the folder Preview rename already moves that artist's
files to. A source name that belongs to a different library artist (one
holding a different id from the same provider) is left alone. A case-only
difference is left to the folder casing rule (#1091), so a source that
spells "MGK" doesn't retag every file of an artist the library calls "mgk".

A library reorganize never comes through here: its files belong to a known
library artist already, and it names them by that artist's own name.
"""

from __future__ import annotations

import json
import re
import threading
import time
from typing import Any, Dict, Iterable, List, Optional, Tuple

from utils.logging_config import get_logger

logger = get_logger("imports.library_names")

_CACHE_TTL = 30.0
_cache: Dict[tuple, Tuple[float, Optional[str]]] = {}
_cache_lock = threading.Lock()

# the providers a library artist's id can be looked up by on an index
# (lib2_artists.spotify_id / musicbrainz_id, idx_lib2_artists_ext_<provider>)
_PROVIDERS = ("spotify", "musicbrainz", "deezer", "itunes", "audiodb", "discogs",
              "genius", "tidal", "qobuz", "amazon", "jiosaavn")
# what download contexts put in an artist's id slot when they have none
_PLACEHOLDER_IDS = frozenset({"explicit_artist", "from_sync_modal", "unknown", "none",
                              "null", "undefined", "0"})


def _get_db():
    from database.music_database import get_database
    return get_database()


def clear_cache() -> None:
    with _cache_lock:
        _cache.clear()


def _fold(name: Any) -> str:
    return re.sub(r"\s+", " ", str(name or "").strip()).casefold()


def provider_of(source: Any) -> str:
    """the library's provider key for a source name, '' when it has none."""
    text = str(source or "").strip().lower()
    for prefix, provider in (("spotify", "spotify"), ("deezer", "deezer"),
                             ("musicbrainz", "musicbrainz"), ("itunes", "itunes"),
                             ("apple", "itunes")):
        if text.startswith(prefix):
            return provider
    return text if text in _PROVIDERS else ""


def _id_clause(provider: str) -> Optional[str]:
    if provider == "spotify":
        return "spotify_id = ?"
    if provider == "musicbrainz":
        return "musicbrainz_id = ?"
    if provider in _PROVIDERS:
        from core.library2.provider_ids import external_id_sql
        return f"{external_id_sql('external_ids', provider)} = ?"
    return None


def _stored_ids(conn, artist_ids: Iterable[int]) -> Dict[int, Dict[str, str]]:
    ids = sorted({int(i) for i in artist_ids})
    if not ids:
        return {}
    marks = ",".join("?" for _ in ids)
    out: Dict[int, Dict[str, str]] = {}
    for row in conn.execute(
            f"SELECT id, spotify_id, musicbrainz_id, external_ids FROM lib2_artists "  # noqa: S608 - placeholders only
            f"WHERE id IN ({marks})", ids):
        stored: Dict[str, str] = {}
        try:
            raw = json.loads(row[3] or "{}")
        except (TypeError, ValueError):
            raw = {}
        if isinstance(raw, dict):
            for key, value in raw.items():
                if value not in (None, "") and str(value).strip():
                    stored[str(key).strip().lower()] = str(value).strip()
        if row[1]:
            stored["spotify"] = str(row[1]).strip()
        if row[2]:
            stored["musicbrainz"] = str(row[2]).strip()
        out[int(row[0])] = stored
    return out


def _different(a: Dict[str, str], b: Dict[str, str]) -> bool:
    """two id sets that prove two artists: providers both have, never agreeing."""
    shared = set(a) & set(b)
    return bool(shared) and not any(a[p] == b[p] for p in shared)


def _pinned(conn, artist_ids: Iterable[int]) -> Dict[int, str]:
    """``{artist id: pinned name}`` for the artists with a name set on their page."""
    ids = sorted({int(i) for i in artist_ids})
    if not ids:
        return {}
    marks = ",".join("?" for _ in ids)
    try:
        rows = conn.execute(
            f"SELECT entity_id, value_json FROM lib2_metadata_overrides "  # noqa: S608 - placeholders only
            f"WHERE entity_type='artist' AND field_name='name' AND entity_id IN ({marks})",
            ids).fetchall()
    except Exception:  # noqa: BLE001 - an install without the override table pins nothing
        return {}
    out: Dict[int, str] = {}
    for row in rows:
        try:
            value = json.loads(row[1])
        except (TypeError, ValueError):
            continue
        if isinstance(value, str) and value.strip():
            out[int(row[0])] = value.strip()
    return out


def _effective_names(conn, artist_ids: Iterable[int]) -> Dict[int, str]:
    from core.library2.metadata_overrides import effective_artist_names
    return {int(k): str(v or "") for k, v in effective_artist_names(conn, list(artist_ids)).items()}


def _baseline_names(conn, artist_ids: Iterable[int]) -> Dict[int, str]:
    ids = sorted({int(i) for i in artist_ids})
    if not ids:
        return {}
    marks = ",".join("?" for _ in ids)
    return {int(r[0]): str(r[1] or "") for r in conn.execute(
        f"SELECT id, name FROM lib2_artists WHERE id IN ({marks})", ids)}  # noqa: S608 - placeholders only


def _going_by(conn, name: str) -> List[int]:
    """every library artist called ``name``, by its scanned name (what the
    importer matches on) or the name pinned on its page."""
    key = _fold(name)
    found = {int(r[0]) for r in conn.execute(
        "SELECT id FROM lib2_artists WHERE LOWER(name) = LOWER(?)", (name,))}
    found = {i for i, n in _baseline_names(conn, found).items() if _fold(n) == key}
    try:
        pinned = {int(r[0]) for r in conn.execute(
            "SELECT entity_id FROM lib2_metadata_overrides WHERE entity_type='artist' "
            "AND field_name='name' AND LOWER(value_json) = LOWER(?)",
            (json.dumps(name, ensure_ascii=False, sort_keys=True, separators=(",", ":")),))}
    except Exception:  # noqa: BLE001 - no override table, no pinned names
        pinned = set()
    found |= {i for i, n in _pinned(conn, pinned).items() if _fold(n) == key}
    return sorted(found)


def _resolve(conn, name: str, ids: Dict[str, str]) -> Optional[str]:
    hits: set = set()
    for provider, value in ids.items():
        clause = _id_clause(provider)
        if clause:
            hits |= {int(r[0]) for r in conn.execute(
                f"SELECT id FROM lib2_artists WHERE {clause}", (value,))}  # noqa: S608 - clause from a fixed map

    if not hits:
        # by name: only a pin changes anything, and only when one library
        # artist goes by that name
        same = _going_by(conn, name)
        if not same:
            return _equivalent(conn, name, ids)
        if len(same) != 1:
            return None
        if _different(_stored_ids(conn, same).get(same[0], {}), ids):
            return None   # that artist holds another id from this source
        pin = _pinned(conn, same).get(same[0])
        return pin if pin and pin != name else None

    names = _effective_names(conn, hits)
    pins = _pinned(conn, hits)
    if pins:
        target = pins[min(pins)]
        if target == name:
            return None
    else:
        if any(_fold(n) == _fold(name) for n in names.values()):
            # already a name the library has for this artist (and a case-only
            # difference is the folder casing rule's, #1091)
            return None
        target = names.get(min(hits), "").strip()
        if not target:
            return None

    # the source calls them something the library has never called them. if
    # that name belongs to another library artist, proven another by its ids,
    # the download paired that artist's credit with this one's id: leave it
    if not any(_fold(n) == _fold(name) for n in _baseline_names(conn, hits).values()):
        others = [i for i in _going_by(conn, name) if i not in hits]
        if others:
            stored = _stored_ids(conn, list(hits) + others)
            if any(_different(stored.get(o, {}), stored.get(h, {}))
                   for o in others for h in hits):
                return None
    return target


def _equivalent(conn, name: str, ids: Dict[str, str]) -> Optional[str]:
    """the library artist ``name`` is under another script or musicbrainz
    name (#2222): itunes' "Δέσποινα Βανδή" is the library's "Despina
    Vandi". only when exactly one library artist answers, and it holds no
    id from these providers that disagrees."""
    from core.library2.artist_equivalence import equivalent_artist_names
    found: set = set()
    for other in equivalent_artist_names(conn, name):
        found.update(_going_by(conn, other))
    if len(found) != 1:
        return None
    artist_id = next(iter(found))
    if _different(_stored_ids(conn, [artist_id]).get(artist_id, {}), ids):
        return None
    target = _effective_names(conn, [artist_id]).get(artist_id, "").strip()
    return target if target and _fold(target) != _fold(name) else None


def name_taken_by(conn, artist_id: int, name: Any) -> Optional[Dict[str, Any]]:
    """The other library artist a name would be shared with, when that artist
    is proven a different one (a different id from the same provider), else
    None. A name pinned on an artist names its folder, so two different
    artists under one name would share a folder. One with no ids, or the same
    ones, is likely the same artist split in two, and sharing is the point."""
    name = str(name or "").strip()
    if not name:
        return None
    own = _baseline_names(conn, [artist_id]).get(int(artist_id), "")
    if _fold(own) == _fold(name):
        return None   # its own scanned name back
    others = [i for i in _going_by(conn, name) if i != int(artist_id)]
    if not others:
        return None
    stored = _stored_ids(conn, [artist_id, *others])
    mine = stored.get(int(artist_id), {})
    for other in others:
        if _different(stored.get(other, {}), mine):
            names = _effective_names(conn, [other])
            return {"id": other, "name": names.get(other) or name}
    return None


def library_artist_name(name: Any, ids: Optional[Dict[str, Any]] = None) -> Optional[str]:
    """The library's name for an artist a source calls ``name``, None when it
    is the same (or the library can't tell). ``ids`` maps provider to id."""
    name = str(name or "").strip()
    if not name or name.casefold() in ("unknown artist", "unknown", "various artists"):
        return None
    clean: Dict[str, str] = {}
    for provider, value in (ids or {}).items():
        provider = provider_of(provider)
        value = str(value or "").strip()
        if provider and value and value.lower() not in _PLACEHOLDER_IDS:
            clean[provider] = value
    key = (name, tuple(sorted(clean.items())))
    now = time.monotonic()
    with _cache_lock:
        hit = _cache.get(key)
        if hit and now - hit[0] < _CACHE_TTL:
            return hit[1]
    conn = None
    try:
        conn = _get_db()._get_connection()
        result = _resolve(conn, name, clean)
    except Exception as exc:  # noqa: BLE001 - naming never fails over a lookup
        logger.debug("library artist name lookup failed for %r: %s", name, exc)
        result = None
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:  # noqa: S110 - cleanup only
                pass
    if result:
        logger.info("[Library Name] %r is %r in your library (ids: %s)",
                    name, result, clean or "none, pinned name")
    with _cache_lock:
        if len(_cache) > 4096:
            _cache.clear()
        _cache[key] = (now, result)
    return result


def _artist_entries(value) -> List[Any]:
    if isinstance(value, (list, tuple)):
        return list(value)
    return [value] if value else []


def library_names(context: Any, artist: Any = None) -> Dict[str, str]:
    """``{folded source name: library name}`` for every artist a download
    context credits that the library knows by another name. Empty for a
    library reorganize (its names are the library's already)."""
    if not isinstance(context, dict) or context.get("_library_reorganize"):
        return {}
    from core.imports.context import (
        get_import_context_album,
        get_import_original_search,
        get_import_source,
        get_import_track_info,
    )
    source = get_import_source(context)
    album = get_import_context_album(context) or {}
    track_info = get_import_track_info(context) or {}
    search = get_import_original_search(context) or {}

    entries: List[Any] = [artist, context.get("artist")]
    entries += _artist_entries(album.get("artists") if isinstance(album, dict) else None)
    entries += _artist_entries(track_info.get("artists"))
    entries += _artist_entries(search.get("artists"))
    entries.append(track_info.get("_explicit_artist_context"))

    with_ids: Dict[str, Tuple[str, Dict[str, str]]] = {}
    names_only: Dict[str, str] = {}
    for entry in entries:
        if isinstance(entry, dict):
            name = str(entry.get("name") or "").strip()
            if not name:
                continue
            provider = provider_of(entry.get("source") or entry.get("_source")
                                   or entry.get("provider") or source)
            ids: Dict[str, str] = {}
            value = str(entry.get("id") or "").strip()
            if provider and value and value.lower() not in _PLACEHOLDER_IDS:
                ids[provider] = value
            mbid = str(entry.get("musicbrainz_id") or entry.get("mbid") or "").strip()
            if mbid:
                ids["musicbrainz"] = mbid
            if ids:
                # every id this download gives the same name, together
                held = with_ids.setdefault(_fold(name), (name, {}))[1]
                for provider_key, provider_value in ids.items():
                    held.setdefault(provider_key, provider_value)
                continue
            names_only.setdefault(_fold(name), name)
        elif isinstance(entry, str) and entry.strip():
            names_only.setdefault(_fold(entry), entry.strip())

    out: Dict[str, str] = {}
    for key, (name, ids) in with_ids.items():
        resolved = library_artist_name(name, ids)
        if resolved:
            out[key] = resolved
    for key, name in names_only.items():
        # a name this download gave an id for was decided by that id
        if key in with_ids:
            continue
        resolved = library_artist_name(name, {})
        if resolved:
            out[key] = resolved
    return out


def swap_credit(credit: Any, names: Optional[Dict[str, str]]) -> Any:
    """``credit`` with each artist the library knows by another name renamed.
    only whole credits: "Machine Gun Kelly feat. X" becomes "mgk feat. X",
    "Machine Gun Kelly Tribute Band" stays."""
    if not names or not isinstance(credit, str) or not credit.strip():
        return credit
    whole = names.get(_fold(credit))
    if whole:
        return whole
    from core.imports.collab_artist import credit_segments
    segments = credit_segments(credit)
    if len(segments) < 2:
        return credit
    out, pos = [], 0
    for segment in segments:
        found = credit.find(segment, pos)
        if found < 0:
            return credit
        out.append(credit[pos:found])
        out.append(names.get(_fold(segment), segment))
        pos = found + len(segment)
    out.append(credit[pos:])
    return "".join(out)


def swap_names(values: Any, names: Optional[Dict[str, str]]) -> Any:
    """a list of artist names (or artist dicts) with :func:`swap_credit` on each."""
    if not names or not isinstance(values, list):
        return values
    swapped = []
    for value in values:
        if isinstance(value, dict) and isinstance(value.get("name"), str):
            swapped.append({**value, "name": swap_credit(value["name"], names)})
        else:
            swapped.append(swap_credit(value, names))
    return swapped


__all__ = ["clear_cache", "library_artist_name", "library_names", "name_taken_by",
           "provider_of", "swap_credit", "swap_names"]
