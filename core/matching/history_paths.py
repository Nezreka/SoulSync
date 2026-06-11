"""Resolve a ``library_history`` row to a playable on-disk file.

Lifted out of ``web_server`` so the fallback chain — and its collision-safety —
is an importable, unit-tested seam. This matters because a *destructive* delete
(``/api/verification/<id>/delete`` → ``os.remove``) trusts the path this returns:
if the tracks-table fallback guessed the wrong same-title file, delete would
remove the wrong track. The rules below are exactly that guard, and the tests
lock them.

Side effects are injected so the decision logic is pure:
  - ``exists(path) -> bool``                     (os.path.exists)
  - ``resolve_library_path(raw) -> str | None``  (transfer/download/library prefix swap)
  - ``lookup_titled_paths(title) -> list[str]``  (tracks.file_path WHERE LOWER(title)=LOWER(?))
"""

from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional


def resolve_history_audio_path(
    row: Dict[str, Any],
    *,
    exists: Callable[[str], bool],
    resolve_library_path: Callable[[str], Optional[str]],
    lookup_titled_paths: Callable[[str], List[str]],
) -> Optional[str]:
    """Return the on-disk path for a history row, or None. Fallback chain:
    1. the recorded path as-is,
    2. the prefix-swap resolver (Docker↔host / transfer→library),
    3. the tracks-table mirror by title (knows the CURRENT path after a rename),
       resolved the same way — but only when it can be picked UNAMBIGUOUSLY.
    """
    raw_path = (row.get("file_path") or "").strip()
    if raw_path and exists(raw_path):
        return raw_path

    resolved = resolve_library_path(raw_path) if raw_path else None
    if resolved and exists(resolved):
        return resolved

    title = (row.get("title") or "").strip()
    if not title:
        return None

    artist = (row.get("artist_name") or "").strip()
    candidates = [p for p in (lookup_titled_paths(title) or []) if p]

    # Same-title collisions across artists exist, and delete() trusts this path,
    # so be strict: when the row names an artist, only accept candidates whose
    # path mentions it; with no artist, only an unambiguous single candidate.
    artist_l = artist.lower()
    if artist_l:
        candidates = [p for p in candidates if artist_l in p.lower()]
    elif len(candidates) != 1:
        return None

    for cand in candidates:
        cand_resolved = resolve_library_path(cand)
        if cand_resolved and exists(cand_resolved):
            return cand_resolved
    return None


__all__ = ["resolve_history_audio_path"]
