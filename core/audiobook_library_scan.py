"""Audiobook library scan — keeps the "you own this" record honest.

SoulSync records a book in ``audiobook_library`` at import, and that record is
what puts an Owned badge on a search result and what stops the grab route
downloading something twice. Records go stale the moment the user touches the
folder themselves, which they do: books get deleted after listening, moved to
a NAS, or restored from a backup taken before an import.

This is the reconciliation. It does two things and no more:

  * Forgets rows whose folder is gone, so an Owned badge never lies.
  * Adopts folders on disk that the database does not know about, by reading
    the asin back out of the sidecars the post-processor wrote.

It never deletes, moves or rewrites a file. The disk is the truth here and the
database is the copy, so the only thing that changes is the database.

Adoption is deliberately limited to folders carrying a sidecar. Matching an
arbitrary folder name back to a catalogue entry is a guess, and a wrong guess
here is worse than no guess at all: it silently marks a book owned that the
user does not have, and the wishlist then refuses to fetch it.
"""

from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from utils.logging_config import get_logger

logger = get_logger("audiobook_library_scan")

# How deep to walk below the library root before giving up. The default
# template is author/series/title, so three is the shape; four gives room for
# an install that shelves by first letter on top of that.
MAX_DEPTH = 4

# A folder holding fewer bytes than this is not a book, it is leftovers.
MIN_BOOK_BYTES = 1024 * 1024


def _audio_extensions() -> frozenset:
    from core.audiobook_organizer import AUDIO_EXTENSIONS
    return AUDIO_EXTENSIONS


def folder_stats(folder: Path) -> Dict[str, Any]:
    """Count and measure the audio directly inside a folder.

    Not recursive on purpose. A book folder holds its own chapters, and
    counting a subfolder's files into it would make a series folder look like
    one enormous book.
    """
    extensions = _audio_extensions()
    count = 0
    size = 0
    formats: List[str] = []

    try:
        entries = list(Path(folder).iterdir())
    except OSError:
        return {"file_count": 0, "size_bytes": 0, "audio_format": ""}

    for entry in entries:
        try:
            if not entry.is_file():
                continue
            suffix = entry.suffix.lower()
            if suffix not in extensions:
                continue
            count += 1
            size += entry.stat().st_size
            token = suffix.lstrip(".")
            if token not in formats:
                formats.append(token)
        except OSError:
            continue

    return {
        "file_count": count,
        "size_bytes": size,
        # Sorted so a mixed folder reports the same string every scan and does
        # not churn the row on every pass.
        "audio_format": "/".join(sorted(formats)),
    }


def is_book_folder(folder: Path) -> bool:
    """True when this folder looks like it holds one book's audio."""
    stats = folder_stats(folder)
    return stats["file_count"] > 0 and stats["size_bytes"] >= MIN_BOOK_BYTES


def iter_book_folders(root: Path, max_depth: int = MAX_DEPTH):
    """Every folder under root that holds audio, without descending into it.

    Stops at the first folder on a branch that holds audio: a book's own
    chapters are the leaves, and anything below them belongs to that book.
    """
    root = Path(root)
    if not root.is_dir():
        return

    stack = [(root, 0)]
    while stack:
        folder, depth = stack.pop()
        if depth > max_depth:
            continue

        if depth > 0 and is_book_folder(folder):
            yield folder
            continue

        try:
            children = [child for child in folder.iterdir() if child.is_dir()]
        except OSError as exc:
            logger.debug("Could not list %s: %s", folder, exc)
            continue

        for child in children:
            stack.append((child, depth + 1))


def _book_from_sidecar(folder: Path, asin: str) -> Dict[str, Any]:
    """The minimum a library row needs, read off disk.

    Only reached for a folder the database has never seen, so there is nothing
    better to fall back on. The catalogue is not consulted: a scan that fires
    one Audible request per unknown folder would hammer them on the first run
    over an existing library, and every field it would fill is cosmetic.
    """
    from core.audiobook_post_processor import NFO_NAME, OPF_NAME

    title = ""
    author = ""
    narrator = ""

    for name, opening, closing in (
        (OPF_NAME, "<dc:title>", "</dc:title>"),
        (NFO_NAME, "<title>", "</title>"),
    ):
        try:
            text = (folder / name).read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        if opening in text:
            title = text.split(opening, 1)[1].split(closing, 1)[0].strip()
        if not author and 'opf:role="aut"' in text:
            author = text.split('opf:role="aut"', 1)[1].split(">", 1)[1] \
                .split("<", 1)[0].strip()
        if not narrator and 'opf:role="nrt"' in text:
            narrator = text.split('opf:role="nrt"', 1)[1].split(">", 1)[1] \
                .split("<", 1)[0].strip()
        if title:
            break

    return {
        "asin": asin,
        # The folder name is the honest fallback: it is what the user sees.
        "title": title or folder.name,
        "author_names": [author] if author else [],
        "narrator_names": [narrator] if narrator else [],
        "series": [],
    }


def scan(root: Optional[str] = None, db: Any = None) -> Dict[str, Any]:
    """Reconcile the library table against the folder on disk.

    Returns ``{checked, removed, adopted, updated, missing_root}``. Never
    raises: this runs from the automation engine, and a scan that throws would
    take the whole run down with it.
    """
    summary = {"checked": 0, "removed": 0, "adopted": 0, "updated": 0,
               "missing_root": False, "started_at": time.time()}

    from core.audiobook_database import get_audiobook_db
    from core.audiobook_organizer import library_root

    database = db if db is not None else get_audiobook_db()
    library_path = Path(str(root or library_root()))

    try:
        rows = database.get_library()
    except Exception as exc:                                # noqa: BLE001
        logger.warning("Could not read the audiobook library: %s", exc)
        return summary

    if not library_path.is_dir():
        # A missing root is almost always an unmounted share or a container
        # started without its volume. Forgetting the whole library over that
        # would be catastrophic and unrecoverable, so nothing is touched.
        summary["missing_root"] = True
        logger.warning("Audiobook library root %s is not there; skipping the scan",
                       library_path)
        return summary

    known_paths = set()

    for row in rows:
        summary["checked"] += 1
        asin = str(row.get("asin") or "")
        path = Path(str(row.get("path") or ""))

        if not asin:
            continue

        if not path.is_dir() or not is_book_folder(path):
            try:
                if database.remove_from_library(asin):
                    summary["removed"] += 1
                    logger.info("Forgot %s: %s is gone", asin, path)
            except Exception as exc:                        # noqa: BLE001
                logger.warning("Could not forget %s: %s", asin, exc)
            continue

        known_paths.add(os.path.normcase(str(path.resolve())))

        stats = folder_stats(path)
        if (stats["file_count"] != int(row.get("file_count") or 0)
                or stats["size_bytes"] != int(row.get("size_bytes") or 0)):
            try:
                if database.update_library_entry(asin, **stats):
                    summary["updated"] += 1
            except Exception as exc:                        # noqa: BLE001
                logger.debug("Could not refresh %s: %s", asin, exc)

    from core.audiobook_post_processor import read_asin_from_folder

    for folder in iter_book_folders(library_path):
        try:
            resolved = os.path.normcase(str(folder.resolve()))
        except OSError:
            continue
        if resolved in known_paths:
            continue

        asin = read_asin_from_folder(folder)
        if not asin:
            # No sidecar, so no reliable identity. Left alone rather than
            # guessed at, because a wrong Owned badge blocks a download the
            # user actually wants.
            continue

        try:
            if database.is_owned(asin):
                continue
            if database.add_to_library(_book_from_sidecar(folder, asin),
                                       str(folder), **folder_stats(folder)):
                summary["adopted"] += 1
                logger.info("Adopted %s from %s", asin, folder)
        except Exception as exc:                            # noqa: BLE001
            logger.warning("Could not adopt %s: %s", folder, exc)

    summary["duration"] = round(time.time() - summary["started_at"], 2)
    logger.info("Audiobook library scan: %d checked, %d removed, %d adopted, %d updated",
                summary["checked"], summary["removed"], summary["adopted"],
                summary["updated"])
    return summary
