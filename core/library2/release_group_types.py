"""What kind of release an album is, as MusicBrainz files its release group.

MusicBrainz gives every release group a primary type (Album, EP, Single) and
any number of secondary ones: Live, Compilation, Soundtrack, Remix, DJ-mix,
Spokenword, Interview, Audiobook, Audio drama, Demo, Mixtape/Street. The
artist page sorts its discography into sections by them (#2055).

They are kept apart from ``lib2_albums.secondary_types``. That column is what a
download was FILED with, and ``$atypes`` in a path template reads it: filling
it in later for an album already on disk would have the next reorganize move
the folder to a "[Live]" one nobody asked for. This one is only ever read to
draw the page.

The value is stored with the group it was read from, ``{"group": id, "types":
[...]}``, and counts only while the album still has that group. An album
re-matched to another release is then simply unknown again, whoever changed
the id. NULL is "never asked", an empty list is "asked, a plain studio
release": the difference is what lets a known album called "Live Through This"
stay out of the Live section.
"""

from __future__ import annotations

import json
from typing import Any, List, Optional

COLUMN = "release_group_types"


def group_types(group: Any) -> Optional[List[str]]:
    """The secondary types in a MusicBrainz release-group object, lower-cased.

    None when the object does not say (a bare ``{"id": ...}`` stub some
    answers carry), so "not told" is never stored as "none".
    """
    if not isinstance(group, dict) or "secondary-types" not in group:
        return None
    raw = group.get("secondary-types") or []
    if not isinstance(raw, (list, tuple)):
        return None
    seen: List[str] = []
    for value in raw:
        name = str(value or "").strip().lower()
        if name and name not in seen:
            seen.append(name)
    return seen


def stored_types(raw: Any, group_id: Any) -> Optional[List[str]]:
    """The types kept for an album, or None when they are not known for the
    release group it has now."""
    current = str(group_id or "").strip().lower()
    if not raw or not current:
        return None
    try:
        data = json.loads(raw) if isinstance(raw, str) else raw
    except (TypeError, ValueError):
        return None
    if not isinstance(data, dict) or str(data.get("group") or "").strip().lower() != current:
        return None
    types = data.get("types")
    if not isinstance(types, list):
        return None
    return [str(value) for value in types if value]


def stamp(conn: Any, album_id: Any, group_id: Any, types: Optional[List[str]]) -> bool:
    """Keep ``types`` for the album, as read from ``group_id``. False when
    there is nothing to say or it already says so."""
    group = str(group_id or "").strip().lower()
    if types is None or not group or not album_id:
        return False
    value = json.dumps({"group": group, "types": list(types)}, sort_keys=True)
    cur = conn.execute(
        f"UPDATE lib2_albums SET {COLUMN}=? WHERE id=? AND {COLUMN} IS NOT ?",
        (value, int(album_id), value))
    return bool(cur.rowcount)
