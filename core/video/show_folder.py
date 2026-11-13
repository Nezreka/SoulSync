"""Which folder a show that the library does not own yet is filed under.

Two shows can share a title: The Office (2005) and The Office (2001), Dark
Matter, Castle, Shameless. The episode template names a show's folder from its
title, so the second one landed in the first one's folder, its S01E01 was read
as a better copy of the other show's S01E01, and it replaced it (#2052).

The first show to arrive keeps the plain folder. A later show whose title
renders to a folder that is already another show's is filed as "Title (Year)"
instead, or by its TMDB id when the year does not tell them apart either.

Three things can say a folder is another show's, because the media server may
not have scanned any of it in yet:

  - the library: another owned show with the same title has files under a
    folder of that name
  - SoulSync's own finished downloads of another same-titled show
  - the folder's ``tvshow.nfo``, which SoulSync, Sonarr, Kodi and Jellyfin all
    write with the show's ids

And a show's own earlier downloads say where IT lives, so its next episode
follows them rather than being judged again.

Isolated: stdlib + sibling video modules only.
"""

from __future__ import annotations

import os
import re
from typing import Any, Callable, Optional

from core.video import organization

__all__ = ["series_folder_name"]

_NFO_TMDB = re.compile(
    r'<uniqueid[^>]*type\s*=\s*["\']tmdb["\'][^>]*>\s*(\d+)\s*</uniqueid>|<tmdbid>\s*(\d+)\s*</tmdbid>',
    re.IGNORECASE)
_NFO_READ_BYTES = 256 * 1024
# how far up from a file its show folder can be: Show/Season/file, or one more
# for a template with a folder between them
_FOLDER_DEPTH = 3


def _fold(name: Any) -> str:
    return str(name or "").strip().casefold()


def _folders(path: Any) -> list:
    """The folder names above a stored or placed file, nearest first."""
    parts = [p for p in re.split(r"[\\/]+", str(path or "")) if p]
    return [_fold(p) for p in parts[:-1][::-1][:_FOLDER_DEPTH]]


def _series_dir(root: str, name: str, year: Any, settings: Any) -> Optional[str]:
    """The series folder ``name`` renders to under ``root``, or None when the
    episode template has no series folder at all."""
    fields = {"series": name, "title": name, "year": year, "season": 1, "episode": 1}
    below = organization.episode_folders_below_series(settings, fields)
    if below is None:
        return None
    folder = organization.render_path("episode", root or "", fields, settings, ".mkv")["dir"]
    for _ in below:
        folder = os.path.dirname(folder)
    return folder


def _read_nfo_tmdb(folder: str, read_text: Optional[Callable[[str], str]]) -> Optional[int]:
    path = os.path.join(folder, "tvshow.nfo")
    try:
        if read_text is not None:
            text = read_text(path)
        else:
            with open(path, encoding="utf-8", errors="replace") as handle:
                text = handle.read(_NFO_READ_BYTES)
    except (OSError, ValueError):
        return None
    hit = _NFO_TMDB.search(text or "")
    if not hit:
        return None
    try:
        return int(hit.group(1) or hit.group(2))
    except (TypeError, ValueError):
        return None


def _taken(folder: Optional[str], tmdb_id: int, others: list, read_text) -> bool:
    """Is this series folder another show's?"""
    if not folder:
        return False
    name = _fold(os.path.basename(folder.rstrip("\\/")))
    if not name:
        return False
    if any(name in _folders(path) for path in others):
        return True
    owner = _read_nfo_tmdb(folder, read_text)
    return owner is not None and owner != tmdb_id


def series_folder_name(db: Any, settings: Any, *, tmdb_id: Any, title: Any, year: Any = None,
                       root: Any = None, tmdb_of: Optional[Callable[[dict], Any]] = None,
                       read_text: Optional[Callable[[str], str]] = None) -> Optional[str]:
    """The name $series should render as for this not-yet-owned show, or None
    to use its title as it is.

    ``tmdb_of(download_row)`` resolves a download row to its show's TMDB id.
    ``read_text(path)`` reads a sidecar (injectable for tests)."""
    title = str(title or "").strip()
    try:
        tmdb_id = int(tmdb_id)
    except (TypeError, ValueError):
        return None
    if not title or not tmdb_id:
        return None

    mine, others = [], []
    for row in db.show_download_placements(title) or []:
        try:
            owner = int(tmdb_of(row)) if tmdb_of else None
        except (TypeError, ValueError):
            owner = None
        if owner is None:
            continue
        (mine if owner == tmdb_id else others).append(row.get("dest_path"))
    others += [row["path"] for row in db.same_titled_show_files(title, exclude_tmdb_id=tmdb_id) or []]

    try:
        plausible_year = int(year) if year and 1880 <= int(year) <= 2200 else None
    except (TypeError, ValueError):
        plausible_year = None
    names = [title]
    if plausible_year:
        names.append("%s (%d)" % (title, plausible_year))
    names.append("%s [tmdb-%d]" % (title, tmdb_id))

    # where this show's own earlier episodes went, before any scan knew of them
    for name in names[1:]:
        folder = _fold(os.path.basename((_series_dir(str(root or ""), name, year, settings) or "").rstrip("\\/")))
        if folder and any(folder in _folders(path) for path in mine):
            return name

    for name in names:
        if not _taken(_series_dir(str(root or ""), name, year, settings), tmdb_id, others, read_text):
            return None if name == title else name
    return names[-1]
