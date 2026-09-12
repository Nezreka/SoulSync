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
    from core.audiobook_library_inventory import discover
    class MemoryCache:
        def cached_library_file(self, *args): return None
        def cache_library_file(self, *args): pass
    if Path(root).is_dir():
        for group in discover(Path(root), MemoryCache(), lambda *_: None, max_depth):
            if group.scope == "folder":
                yield group.path


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


def _provenance(group, facts, previous, downloads):
    if previous and previous.get("origin") == "soulsync":
        return "soulsync", previous.get("download_id", "")
    marker = ""
    if group.scope == "folder":
        try:
            marker = (group.path / ".soulsync-release").read_text(encoding="utf-8").strip()
        except OSError:
            pass
    for download in downloads:
        if download.get("status") != "completed":
            continue
        exact_path = download.get("imported_path") and _key(Path(download["imported_path"])) == _key(group.path)
        marked = marker and marker in (download.get("release_title"), download.get("download_id"))
        if exact_path or (marked and download.get("asin") == facts.get("asin")):
            return "soulsync", download["download_id"]
    if marker:
        return "soulsync", ""
    return (previous.get("origin", "unknown") if previous else "disk"), ""


def scan(root: Optional[str] = None, db: Any = None, progress=None,
         match_catalog: bool = False, match_limit: int = 25, client=None) -> Dict[str, Any]:
    """Read files, reconcile inventory, then optionally match a bounded catalogue batch."""
    from core.audiobook_database import get_audiobook_db
    from core.audiobook_organizer import library_root
    from core.audiobook_library_metadata import read_metadata
    from core.audiobook_library_inventory import discover
    if not _SCAN_LOCK.acquire(blocking=False):
        return {"status": "skipped", "skipped": "An audiobook library scan is already running"}
    summary = {"status": "running", "phase": "scanning", "checked": 0, "removed": 0,
               "adopted": 0, "updated": 0, "moved": 0, "local": 0, "errors": 0,
               "missing_root": False, "started_at": time.time(), "error": ""}
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
        root_path = Path(str(root or library_root())).resolve()
        summary["root"] = str(root_path)
        database = db if db is not None else get_audiobook_db()
        report()
        if not root_path.is_dir():
            summary.update(missing_root=True, status="error", error="The audiobook folder is not reachable. Check its setting and mount.")
            return summary
        rows = database.get_library()
        downloads = database.get_downloads()
        by_path = {_key(Path(r["path"])): r for r in rows if r.get("path")}
        ids = {r["asin"] for r in rows}
        groups = discover(root_path, database, error)
        summary["found"] = len(groups)
        seen_ids, seen_files = set(), set()
        for index, group in enumerate(groups):
            summary["checked"] += 1
            summary["current"] = group.path.name
            try:
                previous = by_path.get(_key(group.path))
                if previous is None:
                    possible = [r for r in rows if r.get("fingerprint") == group.fingerprint
                                and r["asin"] not in seen_ids and not Path(r["path"]).exists()]
                    if len(possible) == 1:
                        previous = possible[0]
                        summary["moved"] += 1
                paths = [str(p) for p in group.files]
                seen_files.update(_key(p) for p in group.files)
                facts = (previous.get("metadata_json") if previous and previous.get("scan_signature") == group.signature
                         else None)
                if not facts:
                    facts = read_metadata(group.path, group.files, probes=group.probes)
                explicit_ids = {p.get("asin") for p in group.probes if p.get("asin")}
                if facts.get("asin"):
                    explicit_ids.add(facts["asin"])
                facts["metadata_conflicts"] = ["Conflicting identifiers in the sidecar and audio files"] if len(explicit_ids)>1 else []
                if facts["metadata_conflicts"]:
                    facts["asin"] = ""
                catalog_asin = facts.get("asin") or ""
                key = previous["asin"] if previous else catalog_asin if catalog_asin and catalog_asin not in ids else _local_id(group.path)
                origin, download_id = _provenance(group, facts, previous, downloads)
                stats = _stats(group.files)
                fields = {**stats, "path": str(group.path), "file_paths": paths,
                          "file_scope": group.scope, "fingerprint": group.fingerprint,
                          "scan_signature": group.signature, "metadata_json": facts,
                          "grouping": group.grouping, "origin": origin, "download_id": download_id}
                if previous:
                    changed = previous.get("scan_signature") != group.signature
                    # A previous manual decision is never silently replaced by a search.
                    pinned = previous.get("match_status") in ("confirmed", "ignored", "changed")
                    if changed and pinned and previous.get("match_status") != "ignored" and previous.get("fingerprint") and previous["fingerprint"] != group.fingerprint:
                        fields.update(match_status="changed", match_evidence=["Files changed since confirmation; please review the saved match"])
                    elif not pinned and (changed or previous.get("match_status") == "identifier"):
                        preserve_import = (previous.get("origin") == "soulsync" or previous.get("match_status") == "identifier") and not facts["metadata_conflicts"] and not catalog_asin and (not previous.get("fingerprint") or previous["fingerprint"] == group.fingerprint)
                        catalog_asin = catalog_asin or (previous.get("catalog_asin", "") if preserve_import else "")
                        fields.update(catalog_asin=catalog_asin, match_status="identifier" if catalog_asin else "unmatched",
                                      match_checked_at=0, match_candidates=[], catalog_book={})
                    if previous.get("source") == "scan":
                        fields.update({k:facts[k] for k in ("title","author","narrator","series_title","series_sequence","runtime_minutes")})
                    # No repeated DB writes or tag probes for an unchanged entry.
                    if any(previous.get(k) != v for k,v in fields.items()):
                        if not database.update_library_entry(key, **fields):
                            raise RuntimeError("Could not update the library record")
                        summary["updated"] += 1
                else:
                    book = {"asin":key, "title":facts["title"], "author_names":[facts["author"]] if facts["author"] else [],
                            "narrator_names":[facts["narrator"]] if facts["narrator"] else [],
                            "runtime_minutes":facts["runtime_minutes"],
                            "series":[{"title":facts["series_title"],"sequence":facts["series_sequence"]}]}
                    if not database.add_to_library(book,str(group.path),source="scan", catalog_asin=catalog_asin, **{k:v for k,v in fields.items() if k!='path'}):
                        raise RuntimeError("Could not save the library record")
                    summary["adopted"] += 1
                seen_ids.add(key)
                ids.add(key)
                summary["local"] += int(not catalog_asin)
            except Exception as exc:
                error(group.path, str(exc))
            finally:
                if index % 10 == 0:
                    report()
        if not root_path.is_dir():
            error(root_path,"Folder became unavailable during the scan")
        if not summary["errors"]:
            for row in rows:
                if row["asin"] in seen_ids or not row.get("path"):
                    continue
                path = Path(row["path"])
                if not path.resolve().is_relative_to(root_path):
                    continue
                try:
                    old_files = row.get("file_paths") or []
                    regrouped = old_files and all(_key(Path(p)) in seen_files for p in old_files)
                    if not regrouped and path.exists() and (path.is_file() or is_book_folder(path)):
                        continue
                    if database.remove_from_library(row["asin"]):
                        summary["removed"] += 1
                except Exception as exc:
                    error(path,str(exc))
        if match_catalog:
            from core.audiobook_library_matching import match_library
            summary["phase"] = "matching"
            report()
            def matching_progress(counts,title):
                summary.update(counts, current=title)
                report()
            summary.update(match_library(database,client=client,limit=match_limit,progress=matching_progress))
        summary["status"] = "error" if summary["errors"] else "completed"
        return summary
    except Exception as exc:
        summary.update(status="error",error=str(exc))
        summary["errors"] += 1
        logger.exception("Audiobook scan failed")
        return summary
    finally:
        summary["finished_at"] = time.time()
        summary["duration"] = round(time.time()-summary["started_at"],2)
        summary.pop("current",None)
        try:
            report()
        finally:
            _SCAN_LOCK.release()
