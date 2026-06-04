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


def plan_playlist_reconcile(
    current_ids: List[str],
    desired_ids: List[str],
) -> dict:
    """Plan an in-place reconcile of a server playlist toward a desired tracklist.

    Used by ``sync_mode='reconcile'`` (#792): instead of deleting + recreating
    the playlist (which destroys its custom image, description, and identity),
    the caller keeps the existing playlist object and applies only the delta —
    adding the tracks that are missing and removing the ones no longer in the
    source. Pure, no I/O.

    Returns ``{'add': [...], 'remove': [...]}`` (both lists of string ids):
      - ``add``    — desired ids not currently present, in desired order.
      - ``remove`` — current ids no longer desired (each occurrence kept once;
                     duplicates of a still-desired id are left for the caller's
                     dedupe to handle, never mass-removed).

    Order-preserving and duplicate-safe: a desired id already present is not
    re-added; a current id that's still desired is not removed even if it
    appears more than once.
    """
    desired = [str(t) for t in desired_ids]
    current = [str(t) for t in current_ids]
    current_set = set(current)
    desired_set = set(desired)
    add = [d for d in desired if d not in current_set]
    # Preserve order of removal as it appears in the current list; one entry per
    # id (the caller maps ids back to concrete playlist entries to delete).
    seen_remove = set()
    remove = []
    for c in current:
        if c not in desired_set and c not in seen_remove:
            seen_remove.add(c)
            remove.append(c)
    return {"add": add, "remove": remove}


VALID_SYNC_MODES = ("replace", "append", "reconcile")


def normalize_sync_mode(requested, configured, default: str = "replace") -> str:
    """Resolve the effective playlist sync mode.

    An explicit per-request value wins; otherwise the configured default
    (Settings > Playlist sync mode); anything unrecognized falls back to
    ``default``. Keeping ``reconcile`` in ``VALID_SYNC_MODES`` is load-bearing —
    a validation list that omits it silently downgrades reconcile to replace,
    which is exactly the #792 regression this helper exists to prevent.
    """
    mode = (requested or "") or (configured or "") or default
    return mode if mode in VALID_SYNC_MODES else default


__all__ = [
    "plan_playlist_add",
    "remove_one_occurrence",
    "plan_playlist_reconcile",
    "normalize_sync_mode",
    "VALID_SYNC_MODES",
]
