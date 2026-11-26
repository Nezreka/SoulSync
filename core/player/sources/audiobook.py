"""an audiobook on disk, by its library key (an asin, or 'local:...' for a
book the scan found but couldn't match).

a book is one or more files played back to back. the player gets the
book's timeline: each part (file) with its length and where it starts,
and the chapters, from the files' own chapter marks (an m4b/m4a chapter
list, mp3 CHAP frames) or one per file when they have none. a part is
streamed on its own: ``part`` picks it (0 based).

reading a file's length and chapters opens it, so the result is kept per
file (path, size and mtime: a changed file is read again).
"""

from __future__ import annotations

import os
from functools import lru_cache
from typing import Any, Dict, List, Optional, Tuple

from core.player.playable import Refused, Source
from core.player.sources import audio_mime
from utils.logging_config import get_logger

logger = get_logger("player.audiobook")


def _chapters_mp4(path: str) -> List[Tuple[float, str]]:
    from mutagen.mp4 import MP4
    audio = MP4(path)
    marks = getattr(audio, "chapters", None) or []
    return [(float(c.start), str(c.title or "")) for c in marks]


def _chapters_id3(path: str) -> List[Tuple[float, str]]:
    from mutagen.id3 import ID3
    out = []
    for frame in ID3(path).getall("CHAP"):
        title = ""
        for sub in frame.sub_frames.values() if hasattr(frame, "sub_frames") else []:
            if getattr(sub, "FrameID", "") == "TIT2":
                title = str(sub.text[0]) if sub.text else ""
        out.append((frame.start_time / 1000.0, title))
    return sorted(out)


@lru_cache(maxsize=2048)
def _read(path: str, size: int, mtime: float) -> Tuple[float, Tuple[Tuple[float, str], ...]]:
    """(seconds, ((start, title), ...)) for one file. size and mtime are the
    cache key: a replaced file is read again."""
    import mutagen
    length = 0.0
    try:
        f = mutagen.File(path)
        length = float(getattr(getattr(f, "info", None), "length", 0) or 0)
    except Exception:  # noqa: BLE001 - an unreadable header still plays; it just has no length
        logger.debug("no length read from %s", path, exc_info=True)
    marks: List[Tuple[float, str]] = []
    ext = os.path.splitext(path)[1].lower()
    try:
        if ext in (".m4b", ".m4a", ".mp4", ".aac"):
            marks = _chapters_mp4(path)
        elif ext == ".mp3":
            marks = _chapters_id3(path)
    except Exception:  # noqa: BLE001 - no chapter list is one chapter
        logger.debug("no chapters read from %s", path, exc_info=True)
        marks = []
    return length, tuple(marks)


def _file_facts(path: str) -> Tuple[float, Tuple[Tuple[float, str], ...]]:
    st = os.stat(path)
    return _read(path, st.st_size, st.st_mtime)


# how many chapters a book had when it was last played, by its files (the
# info sheet reads this and never opens a file)
_chapter_counts: Dict[Tuple[str, ...], int] = {}


def known_chapters(paths: List[str]) -> Optional[int]:
    return _chapter_counts.get(tuple(paths))


def timeline(paths: List[str]) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], float]:
    """the parts and chapters of a book made of these files, in order"""
    parts: List[Dict[str, Any]] = []
    chapters: List[Dict[str, Any]] = []
    at = 0.0
    for index, path in enumerate(paths):
        length, marks = _file_facts(path)
        parts.append({"index": index, "start": round(at, 3), "duration": round(length, 3),
                      "mime": audio_mime(path)})
        if marks:
            for n, (start, title) in enumerate(marks):
                chapters.append({"start": round(at + start, 3), "title": title or f"Chapter {len(chapters) + 1}",
                                 "part": index, "offset": round(start, 3), "n": n})
        else:
            stem = os.path.splitext(os.path.basename(path))[0]
            chapters.append({"start": round(at, 3), "title": stem, "part": index, "offset": 0.0})
        at += length
    if len(_chapter_counts) > 2000:
        _chapter_counts.clear()
    _chapter_counts[tuple(paths)] = len(chapters)
    return parts, chapters, at


def _book_files(key: str) -> List[str]:
    from core.audiobook_database import get_audiobook_db
    row = get_audiobook_db().get_library_entry(key)
    if not row:
        raise Refused(404, "not_found", "That book isn't in your library.")
    files = [p for p in (row.get("file_paths") or []) if isinstance(p, str)]
    files = sorted(files, key=lambda p: os.path.basename(p).lower())
    present = [p for p in files if os.path.isfile(p)]
    if not present:
        raise Refused(404, "no_file", "SoulSync can't reach that book's files.")
    return present


def locate(item_id: str, part: Any = None) -> Source:
    files = _book_files(item_id)
    parts, chapters, total = timeline(files)
    index = 0
    if part not in (None, ""):
        try:
            index = int(part)
        except (TypeError, ValueError):
            raise Refused(400, "bad_part", "That isn't one of the book's parts.") from None
        if not 0 <= index < len(files):
            raise Refused(404, "bad_part", "That isn't one of the book's parts.")
    path: Optional[str] = files[index]
    return Source("file", audio_mime(path), path=path, duration=total or None,
                  parts=parts, chapters=chapters, extra={"part": index})
