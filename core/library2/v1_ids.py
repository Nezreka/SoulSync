"""The ids the v1 API hands out and takes, the same ones it always did.

Before Library v2 a library row's id WAS its media server's id ("77", or
"al1::soulsync" on a standalone install), so scripts and MCP setups saved
those. The catalogue has its own integer ids now. v1 keeps speaking the old
language so nothing a client saved breaks:

- out: an imported row answers under its old id (``legacy_<entity>_id``), a
  row born after the upgrade under its media server's id, and a row no server
  knows yet as ``lib2:<n>``. Always a string, as before.
- in: ``lib2:<n>`` is the catalogue row. Anything else is read as an old or
  server id first. Only when nothing has it does a bare number fall back to
  the catalogue id. An old/server id that names two catalogue rows is
  refused, never guessed.
"""

from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional

_TABLES = {"artist": "lib2_artists", "album": "lib2_albums", "track": "lib2_tracks"}
NATIVE_PREFIX = "lib2:"
_CHUNK = 400


class AmbiguousId(ValueError):
    """An old id that more than one catalogue row answers to."""


def _entity(entity: str) -> str:
    key = str(entity or "").strip().lower().rstrip("s")
    if key not in _TABLES:
        raise ValueError(f"Unknown entity: {entity!r}")
    return key


def _active_server() -> str:
    try:
        from core.settings import config_manager
        return config_manager.get_active_media_server() or ""
    except Exception:  # noqa: BLE001 - no server known: legacy ids still answer
        return ""


def public_ids(conn: Any, entity: str, native_ids: Iterable[Any]) -> Dict[int, str]:
    """``{catalogue id: the id v1 shows}`` for every id given."""
    entity = _entity(entity)
    ids = sorted({int(i) for i in native_ids if i is not None and str(i).lstrip("-").isdigit()})
    out: Dict[int, str] = {}
    if not ids:
        return out
    table = _TABLES[entity]
    server = _active_server()
    for start in range(0, len(ids), _CHUNK):
        chunk = ids[start:start + _CHUNK]
        marks = ",".join("?" * len(chunk))
        for row in conn.execute(
                f"SELECT id, legacy_{entity}_id FROM {table} WHERE id IN ({marks})", chunk):
            if row[1] is not None and str(row[1]).strip():
                out[int(row[0])] = str(row[1])
        missing = [i for i in chunk if i not in out]
        if missing and server:
            marks = ",".join("?" * len(missing))
            for row in conn.execute(
                    "SELECT entity_id, server_id FROM lib2_media_server_mappings "
                    f"WHERE entity_type=? AND server_source=? AND server_library_id='' "
                    f"AND entity_id IN ({marks}) ORDER BY last_seen_at DESC, id DESC",
                    [entity, server, *missing]):
                out.setdefault(int(row[0]), str(row[1]))
    for i in ids:
        out.setdefault(i, f"{NATIVE_PREFIX}{i}")
    return out


def resolve(conn: Any, entity: str, raw: Any) -> Optional[int]:
    """The catalogue id a v1 id names, None when nothing has it.

    Raises AmbiguousId when an old/server id names more than one row."""
    entity = _entity(entity)
    value = str(raw if raw is not None else "").strip()
    if not value:
        return None
    table = _TABLES[entity]
    if value.startswith(NATIVE_PREFIX):
        number = value[len(NATIVE_PREFIX):]
        if not number.isdigit():
            return None
        row = conn.execute(f"SELECT id FROM {table} WHERE id=?", (int(number),)).fetchone()
        return int(row[0]) if row else None
    # the legacy columns have INTEGER affinity: a numeric old id is stored as
    # a number, an opaque one ('al1::soulsync') as text
    keys: List[Any] = [value]
    if value.lstrip("-").isdigit():
        keys.append(int(value))
    marks = ",".join("?" * len(keys))
    found = {int(r[0]) for r in conn.execute(
        f"SELECT id FROM {table} WHERE legacy_{entity}_id IN ({marks})", keys)}
    if entity == "track":
        found |= {int(r[0]) for r in conn.execute(
            f"SELECT track_id FROM lib2_track_files WHERE legacy_track_id IN ({marks}) "
            "AND track_id IS NOT NULL", keys)}
    if not found:
        server = _active_server()
        params: List[Any] = [entity, value]
        server_sql = ""
        if server:
            server_sql = " AND server_source=?"
            params.append(server)
        found = {int(r[0]) for r in conn.execute(
            "SELECT entity_id FROM lib2_media_server_mappings "
            f"WHERE entity_type=? AND server_id=?{server_sql}", params)}
    if found:
        from core.library2.catalogue_identity import resolve_native_id
        found = {int(resolve_native_id(conn, entity, i)) for i in found}
        if len(found) > 1:
            raise AmbiguousId(
                f"{entity} id {value} matches more than one library entry; "
                f"use the lib2: form of the one you mean")
        return found.pop()
    if value.isdigit():
        row = conn.execute(f"SELECT id FROM {table} WHERE id=?", (int(value),)).fetchone()
        if row:
            return int(row[0])
    return None


def publicize(conn: Any, entity: str, rows: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Copies of v1 rows with their own id and the artist/album ids they name
    swapped for the ids v1 shows."""
    entity = _entity(entity)
    rows = [dict(r) for r in rows]
    refs = {"artist": set(), "album": set(), "track": set()}
    for row in rows:
        refs[entity].add(row.get("id"))
        if entity in ("album", "track"):
            refs["artist"].add(row.get("artist_id"))
        if entity == "track":
            refs["album"].add(row.get("album_id"))
    maps = {kind: public_ids(conn, kind, ids) for kind, ids in refs.items() if ids}
    for row in rows:
        for key, kind in (("id", entity), ("artist_id", "artist"), ("album_id", "album")):
            if key == "id" or (key == "artist_id" and entity != "artist") or \
                    (key == "album_id" and entity == "track"):
                value = row.get(key)
                if value is not None and str(value).lstrip("-").isdigit():
                    row[key] = maps.get(kind, {}).get(int(value), str(value))
    return rows
