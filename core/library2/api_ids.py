"""v1 API ids across the Library v2 upgrade (#1062 review, 2026-10-28).

Before the upgrade the v1 API answered with the media server's ids as strings
("77", "al1::soulsync"); since then it answers with catalogue ids, still as
strings. A script or MCP setup that saved an old id keeps working: a lookup
takes today's catalogue id or the id the API handed out before, found through
the row's legacy id (the upgrade keeps the old row's id) or the media server
mapping. A Plex id can be a number that is also some catalogue id: when an id
names one entry as a catalogue id and a different one as an old id, the lookup
is refused with both named, never guessed.
"""

from __future__ import annotations

from typing import Any, List, Optional

_TABLES = {
    "artist": ("lib2_artists", "legacy_artist_id", "name"),
    "album": ("lib2_albums", "legacy_album_id", "title"),
    "track": ("lib2_tracks", "legacy_track_id", "title"),
}


class AmbiguousApiId(LookupError):
    """An id that is a catalogue id of one entry and an old id of another."""

    def __init__(self, entity_type: str, raw_id: str, current: Optional[tuple], old: List[tuple]):
        named = [f"{entity_type} {cid} ({name!r})" for cid, name in old]
        if current:
            message = (f"{entity_type.capitalize()} id {raw_id!r} matches two entries: catalogue "
                       f"{entity_type} {current[0]} ({current[1]!r}) and {', '.join(named)}, "
                       f"which had this id before the Library v2 upgrade. Look it up by the "
                       f"catalogue id the API returns now.")
        else:
            message = (f"{entity_type.capitalize()} id {raw_id!r} was the old id of more than one "
                       f"entry ({', '.join(named)}). Look it up by the catalogue id the API "
                       f"returns now.")
        super().__init__(message)


def resolve_api_id(conn: Any, entity_type: str, raw_id: Any) -> Optional[int]:
    """The catalogue id a v1 caller means by ``raw_id``, None when nothing
    has it. Raises AmbiguousApiId when it names two different entries."""
    table, legacy_column, label = _TABLES[entity_type]
    text = str(raw_id if raw_id is not None else "").strip()
    if not text:
        return None
    current = None
    if text.isdigit():
        current = conn.execute(f"SELECT id, {label} FROM {table} WHERE id = ?",
                               (int(text),)).fetchone()
    old = [tuple(row) for row in conn.execute(
        f"SELECT id, {label} FROM {table} WHERE {legacy_column} = ? "
        f"UNION SELECT e.id, e.{label} FROM lib2_media_server_mappings m "
        f"JOIN {table} e ON e.id = m.entity_id WHERE m.entity_type = ? AND m.server_id = ?",
        (text, entity_type, text))
        if not current or row[0] != current[0]]
    if old and (current or len(old) > 1):
        raise AmbiguousApiId(entity_type, text, tuple(current) if current else None, old)
    if current:
        return int(current[0])
    return int(old[0][0]) if old else None


__all__ = ["AmbiguousApiId", "resolve_api_id"]
