"""Pure planners for sync-editor playlist mutations (#768 Bug C).

The sync editor's "Find & add" and remove actions rewrite the whole server
playlist from a flat list of track IDs (Subsonic/Navidrome + Jellyfin have no
position-level ops). Two bugs lived in the inline endpoint logic:

* **Duplicate on manual match.** "Find & add" always *inserted* the chosen
  track — but when the user is matching an UNMATCHED source to a server track
  that's already in the playlist (an orphan "extra"), the intent is to LINK
  them, not add a second copy. Each attempt appended another duplicate
  (positions 72, 73, 74…). ``plan_playlist_add`` skips the insert when it's a
  link to an already-present track (the caller still persists the override).

* **Delete removes ALL copies.** The inline remove filtered out *every* entry
  with the target ID. With duplicates present, deleting one removed them all.
  ``remove_one_occurrence`` drops a single entry (duplicates are the same
  track, so removing any one is correct).

Pure, no I/O — the caller fetches the current track-id list and applies the
returned plan to the media-server client.
"""

from __future__ import annotations

from typing import List, Optional, Tuple


def plan_playlist_add(
    current_ids: List[str],
    track_id: str,
    *,
    is_link: bool,
    position: Optional[int] = None,
) -> dict:
    """Plan a "Find & add" against a flat track-id playlist.

    ``is_link`` is True when the add carries a ``source_track_id`` (i.e. the
    user is matching an unmatched source to this server track). In that case,
    if the track is ALREADY in the playlist, return ``should_insert=False`` so
    the caller only records the override and never duplicates it.

    Returns ``{'should_insert': bool, 'new_ids': [...]}``. ``new_ids`` equals
    the input (stringified) when no insert is needed."""
    tid = str(track_id)
    current = [str(t) for t in current_ids]
    if is_link and tid in current:
        return {"should_insert": False, "new_ids": current}
    pos = len(current) if position is None else max(0, min(int(position), len(current)))
    new_ids = current[:pos] + [tid] + current[pos:]
    return {"should_insert": True, "new_ids": new_ids}


def remove_one_occurrence(
    track_ids: List[str],
    target_id: str,
    position: Optional[int] = None,
) -> Tuple[List[str], bool]:
    """Remove a SINGLE occurrence of ``target_id`` from a flat id list.

    If ``position`` is given and the id there matches, that exact entry is
    removed (so the user removes the row they clicked); otherwise the first
    matching id is removed. Returns ``(new_ids, removed)``. ``removed`` is
    False when the id isn't present (caller should 404)."""
    target = str(target_id)
    ids = [str(t) for t in track_ids]
    if position is not None and 0 <= position < len(ids) and ids[position] == target:
        return ids[:position] + ids[position + 1:], True
    for idx, tid in enumerate(ids):
        if tid == target:
            return ids[:idx] + ids[idx + 1:], True
    return ids, False


__all__ = ["plan_playlist_add", "remove_one_occurrence"]
