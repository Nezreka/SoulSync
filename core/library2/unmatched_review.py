"""The files that imported without a match, with what can be guessed about each.

an import whose tags can't be read and that acoustid can't place is filed
under a made-up "Unknown Artist" (#1202). sending the user to that fake
artist's page left them to work out, one track at a time, what each file was.
this lists the files with the evidence a person would use:

* the folder. if the other files in it are matched to one album, that is
  almost certainly this file's album too. a folder of mixed albums (a library
  root) says nothing, so it isn't guessed from.
* the file name: a leading track number, the title, and a copy suffix like
  "(1)" that a duplicate download leaves.
* a duplicate: a matched file in the same folder with the same title. that's
  a second copy, and the useful action is removing it, not identifying it.
"""

from __future__ import annotations

import ntpath
import posixpath
import re
from collections import Counter
from typing import Any, Dict, List, Optional, Tuple

_UNKNOWN = "unknown artist"
# "15 - Suzy", "15. Suzy", "15 Suzy", "1-15 Suzy" (disc-track)
_TRACK_PREFIX = re.compile(r"^\s*(?:\d{1,2}[-.])?(\d{1,3})\s*(?:[-.)_]\s*|\s+)")
# "Suzy (1)", "Suzy (2)", "Suzy - Copy" (what a re-download or a copy leaves)
_COPY_SUFFIX = re.compile(r"\s*(?:\((\d{1,2})\)|-\s*copy(?:\s*\(\d+\))?)\s*$", re.IGNORECASE)
# a folder that dominates by this share speaks for the whole folder
_ALBUM_MAJORITY = 0.8


def _path_mod(path: str):
    return ntpath if "\\" in path or re.match(r"^[A-Za-z]:", path) else posixpath


def parse_file_name(path: str) -> Dict[str, Any]:
    """{'folder', 'folder_name', 'track_number', 'title', 'copy'} from a path."""
    mod = _path_mod(path)
    folder = mod.dirname(path)
    stem = mod.splitext(mod.basename(path))[0]
    track_number = None
    match = _TRACK_PREFIX.match(stem)
    if match and match.end() < len(stem):
        track_number = int(match.group(1))
        stem = stem[match.end():]
    copy = bool(_COPY_SUFFIX.search(stem))
    title = _COPY_SUFFIX.sub("", stem).strip(" -_.") or stem.strip()
    return {
        "folder": folder,
        "folder_name": mod.basename(folder),
        "track_number": track_number,
        "title": title,
        "copy": copy,
    }


def _norm(text: Any) -> str:
    from core.text.normalize import normalize_key
    return normalize_key(str(text or "")) or ""


def _unmatched_rows(conn) -> List[Any]:
    return conn.execute(
        """
        SELECT t.id AS track_id, t.title, t.track_number, t.duration,
               al.id AS album_id, ar.id AS artist_id,
               f.id AS file_id, f.path, f.format, f.bitrate
          FROM lib2_artists ar
          JOIN lib2_albums al ON al.primary_artist_id = ar.id
          JOIN lib2_tracks t ON t.album_id = al.id
          JOIN lib2_track_files f ON f.track_id = t.id
         WHERE LOWER(TRIM(ar.name)) = ?
           AND f.path IS NOT NULL AND TRIM(f.path) <> ''
           AND COALESCE(f.file_state, 'active') = 'active'
         ORDER BY f.path
        """,
        (_UNKNOWN,),
    ).fetchall()


def _folder_neighbours(conn, folder: str) -> List[Dict[str, Any]]:
    """the matched files directly in ``folder`` (not its subfolders)."""
    mod = _path_mod(folder)
    sep = "\\" if mod is ntpath else "/"
    prefix = folder.rstrip("\\/") + sep
    # a path range the path index can serve, and only files directly in the
    # folder: a library root's range is the whole library, and its subfolders
    # are other albums anyway
    rows = conn.execute(
        """
        SELECT t.id AS track_id, t.title, t.track_number, f.path,
               al.id AS album_id, al.title AS album_title,
               ar.id AS artist_id, ar.name AS artist_name
          FROM lib2_track_files f
          JOIN lib2_tracks t ON t.id = f.track_id
          JOIN lib2_albums al ON al.id = t.album_id
          JOIN lib2_artists ar ON ar.id = al.primary_artist_id
         WHERE f.path >= ? AND f.path < ?
           AND instr(substr(f.path, ?), ?) = 0
           AND COALESCE(f.file_state, 'active') = 'active'
           AND LOWER(TRIM(ar.name)) <> ?
        """,
        (prefix, prefix + "\uffff", len(prefix) + 1, sep, _UNKNOWN),
    ).fetchall()
    folder_key = mod.normcase(folder.rstrip("\\/"))
    return [dict(r) for r in rows if mod.normcase(mod.dirname(r["path"])) == folder_key]


def _folder_album(neighbours: List[Dict[str, Any]]) -> Optional[Tuple[str, str]]:
    """(album, artist) when one album holds the folder, else None."""
    if not neighbours:
        return None
    counts = Counter((n["album_title"], n["artist_name"]) for n in neighbours)
    (album, artist), hits = counts.most_common(1)[0]
    return (album, artist) if hits / len(neighbours) >= _ALBUM_MAJORITY else None


def _duplicate_of(parsed: Dict[str, Any], neighbours: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """the matched file in the same folder this one is a copy of"""
    want = _norm(parsed["title"])
    if not want:
        return None
    for n in neighbours:
        if _norm(n["title"]) != want:
            continue
        if parsed["track_number"] and n["track_number"] and parsed["track_number"] != n["track_number"]:
            continue
        return {
            "track_id": n["track_id"],
            "title": n["title"],
            "album_id": n["album_id"],
            "album_title": n["album_title"],
            "artist_id": n["artist_id"],
            "artist_name": n["artist_name"],
        }
    return None


def list_unmatched(conn) -> List[Dict[str, Any]]:
    """Every file under Unknown Artist, each with its best guess."""
    out = []
    neighbours_by_folder: Dict[str, List[Dict[str, Any]]] = {}
    for row in _unmatched_rows(conn):
        path = str(row["path"])
        parsed = parse_file_name(path)
        folder = parsed["folder"]
        if folder not in neighbours_by_folder:
            neighbours_by_folder[folder] = _folder_neighbours(conn, folder) if folder else []
        neighbours = neighbours_by_folder[folder]
        context = _folder_album(neighbours)
        duplicate = _duplicate_of(parsed, neighbours)
        album, artist = context if context else (None, None)
        query = " ".join(part for part in (parsed["title"], artist, album) if part)
        out.append({
            "track_id": row["track_id"],
            "file_id": row["file_id"],
            "album_id": row["album_id"],
            "artist_id": row["artist_id"],
            "file_path": path,
            "file_name": _path_mod(path).basename(path),
            "folder": folder,
            "format": str(row["format"] or "").upper(),
            "bitrate": row["bitrate"],
            "duration": row["duration"],
            "title": row["title"],
            "guess": {
                "title": parsed["title"],
                "track_number": parsed["track_number"],
                "album": album,
                "artist": artist,
                "query": query,
                "copy": parsed["copy"],
            },
            "duplicate_of": duplicate,
        })
    return out
