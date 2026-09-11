"""Index the configured audiobook folder, including books acquired elsewhere.

The scan reads audio and sidecars; it never moves, tags, renames or deletes files.
Unidentified books receive local keys, never guessed catalogue ownership.
"""
from __future__ import annotations

import hashlib
import os
import re
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from utils.logging_config import get_logger

logger = get_logger("audiobook_library_scan")
MAX_DEPTH = 32
MIN_BOOK_BYTES = 1024 * 1024
_SCAN_LOCK = threading.Lock()
_DISC = re.compile(r"^(?:cd|disc|disk|part)\s*[-_ ]*\d+$", re.I)


def _audio_extensions() -> frozenset:
    from core.audiobook_organizer import AUDIO_EXTENSIONS
    return AUDIO_EXTENSIONS


def _stats(files: list[Path]) -> dict:
    return {"file_count": len(files), "size_bytes": sum(p.stat().st_size for p in files),
            "audio_format": "/".join(sorted({p.suffix.lower().lstrip('.') for p in files}))}


def folder_stats(folder: Path) -> Dict[str, Any]:
    """Measure direct audio children; retained for organizer callers."""
    try:
        files = [p for p in Path(folder).iterdir()
                 if not p.is_symlink() and p.is_file() and p.suffix.lower() in _audio_extensions()]
        return _stats(files)
    except OSError:
        return {"file_count": 0, "size_bytes": 0, "audio_format": ""}


def is_book_folder(folder: Path) -> bool:
    stats = folder_stats(folder)
    return stats["file_count"] > 0 and stats["size_bytes"] >= MIN_BOOK_BYTES


def iter_book_folders(root: Path, max_depth: int = MAX_DEPTH):
    """Compatibility iterator for book directories (never the library root)."""
    if not Path(root).is_dir():
        return
    for path, _ in _inventory(Path(root), max_depth, lambda *_: None):
        if path.is_dir():
            yield path


def _inventory(root: Path, max_depth: int, error):
    stack = [(root, 0)]
    while stack:
        folder, depth = stack.pop()
        if depth > max_depth:
            error(folder, "Folder nesting exceeds the scan limit")
            continue
        try:
            entries = sorted(folder.iterdir())
            visible = [p for p in entries if not p.name.startswith('.') and not p.is_symlink()]
            files = [p for p in visible if p.is_file() and p.suffix.lower() in _audio_extensions()]
            children = [p for p in visible if p.is_dir()]
            # CD1/CD2 are parts of a book, not independent titles.
            discs = [p for p in children if _DISC.fullmatch(p.name)]
            if folder != root and discs and len(discs) == len(children):
                for disc in discs:
                    files.extend(p for p in sorted(disc.iterdir())
                                 if p.is_file() and not p.is_symlink()
                                 and p.suffix.lower() in _audio_extensions())
                children = []
            if files and _stats(files)["size_bytes"] >= MIN_BOOK_BYTES:
                if folder == root or (len(files) > 1 and all(p.suffix.lower() == ".m4b" for p in files)
                                      and not (folder / "metadata.opf").exists()
                                      and not (folder / "book.nfo").exists()):
                    # A flat library is many files, never one deletable root directory.
                    for file in files:
                        if file.stat().st_size >= MIN_BOOK_BYTES:
                            yield file, [file]
                else:
                    yield folder, files
                    continue
            stack.extend((p, depth + 1) for p in reversed(children))
        except OSError as exc:
            error(folder, str(exc))


def _key(path: Path) -> str:
    return os.path.normcase(str(path.resolve()))


def _local_id(path: Path) -> str:
    return "local:" + hashlib.sha256(_key(path).encode()).hexdigest()[:24]


def _signature(path: Path, files: list[Path]) -> str:
    sidecars = [path / name for name in ("metadata.opf", "book.nfo", "metadata.json")] \
        if path.is_dir() else [path.with_suffix('.opf'), path.with_suffix('.nfo')]
    parts = []
    for file in files + sidecars:
        try:
            stat = file.stat()
        except FileNotFoundError:
            continue
        parts.append(f"{file}:{stat.st_size}:{stat.st_mtime_ns}")
    return hashlib.sha256("\n".join(parts).encode()).hexdigest()


def scan_status(db=None) -> dict:
    from core.audiobook_database import get_audiobook_db
    database = db if db is not None else get_audiobook_db()
    state = database.get_library_scan_state()
    if state.get("status") == "running" and not _SCAN_LOCK.locked():
        state = {**state, "status": "interrupted", "error": "The previous scan was interrupted. Run it again."}
    return state


def scan(root: Optional[str] = None, db: Any = None, progress=None) -> Dict[str, Any]:
    """One shared, non-overlapping pass for the library automation.

    Missing roots and incomplete walks never prune records. Local rows are
    promoted to a catalogue identity only when an explicit ASIN is discovered.
    """
    if not _SCAN_LOCK.acquire(blocking=False):
        return {"status": "skipped", "skipped": "An audiobook library scan is already running"}
    from core.audiobook_database import get_audiobook_db
    from core.audiobook_organizer import library_root
    from core.audiobook_library_metadata import read_metadata
    from core.audiobook_post_processor import read_asin_from_folder

    summary = {"status": "running", "checked": 0, "removed": 0, "adopted": 0,
               "updated": 0, "local": 0, "errors": 0, "missing_root": False,
               "started_at": time.time(), "error": ""}
    database = None

    def report():
        if database is not None:
            database.set_library_scan_state(summary)
        if progress:
            progress(dict(summary))

    def error(path, detail):
        summary["errors"] += 1
        summary["error"] = f"Could not fully scan {path}: {detail}"
        logger.warning(summary["error"])

    try:
        library_path = Path(str(root or library_root())).resolve()
        summary["root"] = str(library_path)
        database = db if db is not None else get_audiobook_db()
        report()
        if not library_path.is_dir():
            summary.update(missing_root=True, status="error", error="The audiobook folder is not reachable. Check its setting and mount.")
            return summary
        rows = database.get_library()
        by_path = {_key(Path(row["path"])): row for row in rows if row.get("path")}
        ids = {row["asin"] for row in rows}
        seen = set()
        adopted_ids = set()
        # Inventory first. An unreadable branch must not look like deleted books.
        candidates = list(_inventory(library_path, MAX_DEPTH, error))
        summary["found"] = len(candidates)
        for index, (path, files) in enumerate(candidates):
            summary["checked"] += 1
            summary["current"] = path.name
            seen.add(_key(path))
            try:
                previous = by_path.get(_key(path))
                stats = _stats(files)
                signature = _signature(path, files)
                if previous and previous.get("scan_signature") == signature:
                    summary["local"] += int(previous["asin"].startswith("local:"))
                    continue
                facts = read_metadata(path, files)
                asin = facts["asin"]
                if not asin and path.is_dir():
                    asin = read_asin_from_folder(path)
                if previous and not previous["asin"].startswith("local:"):
                    asin = previous["asin"]
                # Keep multiple physical copies visible without overwriting an owned copy.
                if asin in ids and (not previous or previous["asin"] != asin):
                    old = next((r for r in rows if r["asin"] == asin), None)
                    if old is None or Path(old["path"]).exists():
                        asin = ""
                asin = asin or _local_id(path)
                summary["local"] += int(asin.startswith("local:"))
                if previous and previous["asin"] == asin:
                    fields = {**stats, "scan_signature": signature}
                    if previous.get("source") == "scan":
                        fields.update({k: facts[k] for k in ("title", "author", "narrator", "series_title", "series_sequence", "runtime_minutes")})
                    if not database.update_library_entry(asin, **fields):
                        raise RuntimeError("Could not update the library record")
                    summary["updated"] += 1
                else:
                    book = {"asin": asin, "title": facts["title"],
                            "author_names": [facts["author"]] if facts["author"] else [],
                            "narrator_names": [facts["narrator"]] if facts["narrator"] else [],
                            "series": [{"title": facts["series_title"], "sequence": facts["series_sequence"]}],
                            "runtime_minutes": facts["runtime_minutes"]}
                    if not database.add_to_library(book, str(path), source="scan", scan_signature=signature, **stats):
                        raise RuntimeError("Could not save the library record")
                    ids.add(asin)
                    adopted_ids.add(asin)
                    if previous:
                        database.remove_from_library(previous["asin"])
                    summary["adopted"] += 1
            except Exception as exc:
                error(path, str(exc))
            finally:
                if index % 10 == 0:
                    report()
        # A mount disappearing during enumeration is also an incomplete scan.
        if not library_path.is_dir():
            error(library_path, "Folder became unavailable during the scan")
        if not summary["errors"]:
            for row in rows:
                if row["asin"] in adopted_ids:
                    continue
                path = Path(str(row.get("path") or ""))
                if not row.get("path") or not path.resolve().is_relative_to(library_path):
                    continue
                if _key(path) in seen:
                    continue
                try:
                    # Permission errors and existing audio outside the inventory are
                    # preserved; only demonstrably missing/empty paths are forgotten.
                    if path.exists() and (path.is_file() or is_book_folder(path)):
                        continue
                    if database.remove_from_library(row["asin"]):
                        summary["removed"] += 1
                except Exception as exc:
                    error(path, str(exc))
        summary["status"] = "error" if summary["errors"] else "completed"
        return summary
    except Exception as exc:
        summary.update(status="error", error=str(exc))
        summary["errors"] += 1
        logger.exception("Audiobook library scan failed")
        return summary
    finally:
        summary["finished_at"] = time.time()
        summary["duration"] = round(time.time() - summary["started_at"], 2)
        summary.pop("current", None)
        try:
            report()
        finally:
            _SCAN_LOCK.release()
