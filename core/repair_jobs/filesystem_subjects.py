"""Filesystem coverage for repair tools that remain useful before import."""

from __future__ import annotations

import os
from typing import Any, Iterable

from core.repair_jobs.base import skip_deleted_quarantine

AUDIO_EXTENSIONS = {
    ".mp3", ".flac", ".ogg", ".opus", ".m4a", ".aac", ".wav",
    ".wma", ".aiff", ".aif", ".ape", ".wv", ".dsf", ".dff",
}


def _path_key(path: Any) -> str:
    return os.path.normcase(os.path.realpath(os.path.normpath(str(path or ""))))


def _suffix_key(path: Any, depth: int = 4) -> str:
    parts = str(path or "").replace("\\", "/").lower().split("/")
    return "/".join(parts[-depth:])


def repair_scan_roots(context: Any) -> list[str]:
    """Explicit transfer and configured music roots, de-nested and existing."""
    roots: list[str] = []
    candidates: list[str] = [str(getattr(context, "transfer_folder", "") or "")]
    if getattr(context, "config_manager", None) is not None:
        from core.library2.file_delete import _library_roots

        candidates.extend(_library_roots(context.config_manager))
    for candidate in candidates:
        resolved = _path_key(candidate)
        if resolved and os.path.isdir(resolved) and resolved not in roots:
            roots.append(resolved)
    def contains(outer: str, inner: str) -> bool:
        try:
            return os.path.commonpath([outer, inner]) == outer
        except ValueError:
            return False

    return [
        root for root in roots
        if not any(other != root and contains(other, root) for other in roots)
    ]


def filesystem_audio_files(
    context: Any,
    *,
    include_indexed: bool = False,
    extensions: Iterable[str] = AUDIO_EXTENSIONS,
) -> list[str]:
    """Walk repair roots and return audio paths, excluding indexed files by default."""
    allowed = {str(ext).lower() for ext in extensions}
    indexed_exact: set[str] = set()
    indexed_suffixes: set[str] = set()
    if not include_indexed:
        try:
            from core.library2.maintenance_subjects import active_file_subjects
            from core.library2.paths import resolve_lib2_path

            for subject in active_file_subjects(
                context.db, context.config_manager, include_missing=True,
            ):
                raw = str(subject.get("path") or "")
                resolved = raw if os.path.isfile(raw) else resolve_lib2_path(
                    raw, config_manager=context.config_manager,
                )
                indexed_exact.add(_path_key(resolved or raw))
                indexed_suffixes.add(_suffix_key(raw))
        except Exception:
            # The filesystem scan is specifically the no-catalogue fallback.
            pass

    found: list[str] = []
    seen: set[str] = set()
    for root_dir in repair_scan_roots(context):
        for root, dirs, files in os.walk(root_dir):
            skip_deleted_quarantine(root, dirs, root_dir)
            if context.check_stop():
                return found
            for name in files:
                if os.path.splitext(name)[1].lower() not in allowed:
                    continue
                path = os.path.join(root, name)
                key = _path_key(path)
                if key in seen:
                    continue
                seen.add(key)
                if not include_indexed and (
                    key in indexed_exact or _suffix_key(path) in indexed_suffixes
                ):
                    continue
                found.append(path)
    return found


__all__ = ["AUDIO_EXTENSIONS", "filesystem_audio_files", "repair_scan_roots"]
