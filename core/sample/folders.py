"""Sample Studio — sample output folders (Phase 6).

Saved chops are rendered into user-configured *sample folders*, a list that
mirrors the additional-music-folders pattern (``library.music_paths``):

* ``library.sample_paths`` — the configured folders; the first entry is the
  default destination for new chops.
* Files NEVER move on their own: removing a folder from the list does not
  touch existing chops — the stash keeps each chop's absolute file path, so
  old chops stay playable and deletable.
* Sample folders are deliberately separate from the music library, so
  library scans never pick chops up as albums.

``get_sample_folders()`` always returns at least the Docker-aware default
(see core.settings.default_sample_paths): an empty/missing setting means
"the default", never "nowhere to save".
"""

from __future__ import annotations

import os
from typing import List

from utils.logging_config import get_logger

logger = get_logger("sample.folders")


def _clean_paths(raw) -> List[str]:
    """Strip blanks and dedupe, preserving order."""
    if not isinstance(raw, list):
        return []
    out: List[str] = []
    for p in raw:
        s = str(p or "").strip()
        if s and s not in out:
            out.append(s)
    return out


def _configured_paths() -> List[str]:
    from core.settings import config_manager

    return _clean_paths(config_manager.get("library.sample_paths", None))


def get_sample_folders() -> List[str]:
    """Configured sample folders, or the default when none are set."""
    from core.settings import default_sample_paths

    configured = _configured_paths()
    return configured or default_sample_paths()


def default_sample_folder() -> str:
    """First configured folder — the default destination for new chops."""
    return get_sample_folders()[0]


def _norm(path: str) -> str:
    return os.path.normcase(os.path.abspath(os.path.expanduser(path)))


def resolve_sample_folder(choice: str | None) -> str:
    """Validate a user-chosen destination folder.

    Returns the absolute path of the chosen folder. ``choice`` may be any
    configured sample folder (or None/"" for the default). Raises ValueError
    for anything else — unlisted paths, traversal attempts, and empty
    strings that are not the default all refuse, so the chop endpoint can
    never be steered at an arbitrary location.
    """
    folders = get_sample_folders()
    if not choice:
        target = os.path.abspath(os.path.expanduser(folders[0]))
        os.makedirs(target, exist_ok=True)
        return target
    wanted = _norm(choice)
    for configured in folders:
        if _norm(configured) == wanted:
            target = os.path.abspath(os.path.expanduser(configured))
            os.makedirs(target, exist_ok=True)
            return target
    raise ValueError(
        f"unknown sample folder {choice!r} — pick one of the configured sample folders"
    )
