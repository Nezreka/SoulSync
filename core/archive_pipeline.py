"""Archive extraction + audio-file discovery for torrent / usenet downloads.

The torrent and usenet download plugins need a uniform way to:

1. Walk the downloader's save directory and find every audio file in it.
2. If the directory contains an archive (``.zip`` / ``.rar`` / ``.tar`` /
   ``.7z``), extract it first so the audio files inside become walkable.

This module is intentionally narrow — no matching, no tagging, no
import. The download plugin layer composes this with the existing
post-processing / matching pipeline. Lidarr does NOT use this module:
Lidarr extracts archives in its own import step before SoulSync sees
the files at all. Usenet downloaders (SABnzbd, NZBGet) also auto-
extract by default. Torrents are the main case where SoulSync may
need to do the extract step itself — most music torrents ship loose,
but some bundle the album in a ``.rar`` archive.

``rarfile`` is an optional dependency. If it isn't installed, archives
with ``.rar`` content are skipped with a single warning rather than
crashing the download.
"""

from __future__ import annotations

import tarfile
import zipfile
from pathlib import Path
from typing import List, Optional

from utils.logging_config import get_logger

logger = get_logger("archive_pipeline")


# Same audio-extension set as ``core/imports/file_ops.py`` ``quality_tiers``.
# Keep them in sync — if a new format is added to file_ops, add it here too
# or the walker will skip it and the download plugin will mark the download
# failed even when files arrived.
AUDIO_EXTENSIONS = frozenset([
    # lossless
    '.flac', '.ape', '.wav', '.alac', '.dsf', '.dff', '.aiff', '.aif',
    # high lossy
    '.opus', '.ogg',
    # standard lossy
    '.m4a', '.aac',
    # low lossy
    '.mp3', '.wma',
])

ARCHIVE_EXTENSIONS = frozenset(['.zip', '.rar', '.tar', '.tar.gz', '.tgz', '.7z'])


def is_archive(path: Path) -> bool:
    """True if the file extension looks like a supported archive.

    Compound extensions (``.tar.gz``, ``.tar.bz2``) are detected by
    checking the last two suffixes joined together — Path.suffix
    only returns the final suffix.
    """
    if not path.is_file():
        return False
    name = path.name.lower()
    if name.endswith(('.tar.gz', '.tar.bz2', '.tar.xz')):
        return True
    return path.suffix.lower() in ARCHIVE_EXTENSIONS


def walk_audio_files(directory: Path) -> List[Path]:
    """Recursively scan ``directory`` for audio files. Returns
    a sorted list of absolute paths. Empty list if the directory
    doesn't exist or contains no audio.
    """
    if not directory or not directory.exists() or not directory.is_dir():
        return []
    out: List[Path] = []
    for child in directory.rglob('*'):
        if not child.is_file():
            continue
        if child.suffix.lower() in AUDIO_EXTENSIONS:
            out.append(child.resolve())
    out.sort()
    return out


def find_archives_in_dir(directory: Path) -> List[Path]:
    """Find every archive file directly inside ``directory`` (one
    level deep — torrents normally put the archive at the root of
    their folder; we don't search nested dirs to avoid extracting
    something we shouldn't).
    """
    if not directory or not directory.exists() or not directory.is_dir():
        return []
    return sorted(p for p in directory.iterdir() if is_archive(p))


def extract_archive(archive_path: Path, extract_to: Optional[Path] = None) -> Optional[Path]:
    """Extract a single archive in-place (or to ``extract_to`` if
    given). Returns the directory the archive was extracted into,
    or ``None`` on failure.

    Supports ``.zip``, ``.tar``/``.tar.gz``/``.tar.bz2``/``.tar.xz``,
    and ``.rar`` (only when the optional ``rarfile`` library is
    installed). ``.7z`` is recognised but extraction requires
    ``py7zr``; without it, the call logs and returns None.
    """
    if not archive_path or not archive_path.exists():
        logger.warning("archive_pipeline: %s does not exist", archive_path)
        return None
    dest = extract_to or archive_path.parent
    dest.mkdir(parents=True, exist_ok=True)

    name = archive_path.name.lower()
    try:
        if name.endswith('.zip'):
            with zipfile.ZipFile(archive_path) as zf:
                _safe_extract_zip(zf, dest)
            return dest
        if name.endswith(('.tar', '.tar.gz', '.tgz', '.tar.bz2', '.tar.xz')):
            with tarfile.open(archive_path) as tf:
                _safe_extract_tar(tf, dest)
            return dest
        if name.endswith('.rar'):
            return _extract_rar(archive_path, dest)
        if name.endswith('.7z'):
            return _extract_7z(archive_path, dest)
    except (zipfile.BadZipFile, tarfile.TarError, OSError) as e:
        logger.error("archive_pipeline: failed to extract %s: %s", archive_path, e)
        return None
    logger.warning("archive_pipeline: unknown archive type for %s", archive_path)
    return None


def extract_all_in_dir(directory: Path) -> List[Path]:
    """Find every archive in ``directory`` and extract each in place.
    Returns the list of directories archives were extracted into
    (usually all the same — ``directory`` itself). Archives that
    failed to extract are skipped silently after a warning.
    """
    out: List[Path] = []
    for archive in find_archives_in_dir(directory):
        result = extract_archive(archive)
        if result is not None:
            out.append(result)
    return out


def collect_audio_after_extraction(directory: Path) -> List[Path]:
    """One-shot helper for the download plugins: extract any archives
    in the directory, then return the walked audio file list. This is
    the common pattern — torrent / usenet plugin gets a save_path,
    calls this, hands the resulting files to the matching pipeline.
    """
    extract_all_in_dir(directory)
    return walk_audio_files(directory)


# ---------------------------------------------------------------------------
# Safety helpers
# ---------------------------------------------------------------------------


def _safe_extract_zip(zf: zipfile.ZipFile, dest: Path) -> None:
    """Extract a zipfile after rejecting any member whose resolved
    path escapes ``dest`` (path traversal protection).
    """
    dest = dest.resolve()
    for member in zf.namelist():
        target = (dest / member).resolve()
        if dest not in target.parents and target != dest:
            logger.error("archive_pipeline: refusing path-traversal member %r", member)
            return
    zf.extractall(dest)


def _safe_extract_tar(tf: tarfile.TarFile, dest: Path) -> None:
    """Same path-traversal protection for tarfiles."""
    dest = dest.resolve()
    for member in tf.getmembers():
        target = (dest / member.name).resolve()
        if dest not in target.parents and target != dest:
            logger.error("archive_pipeline: refusing path-traversal member %r", member.name)
            return
    # ``filter='data'`` is the Python 3.12+ safe extractor; fall back
    # to the legacy call on older runtimes.
    try:
        tf.extractall(dest, filter='data')  # type: ignore[call-arg]
    except TypeError:
        tf.extractall(dest)


def _extract_rar(archive_path: Path, dest: Path) -> Optional[Path]:
    try:
        import rarfile  # type: ignore[import-untyped]
    except ImportError:
        logger.warning(
            "archive_pipeline: cannot extract %s — rarfile library not installed. "
            "Install with: pip install rarfile (and ensure unrar is on PATH).",
            archive_path,
        )
        return None
    try:
        with rarfile.RarFile(archive_path) as rf:
            dest_resolved = dest.resolve()
            for name in rf.namelist():
                target = (dest_resolved / name).resolve()
                if dest_resolved not in target.parents and target != dest_resolved:
                    logger.error("archive_pipeline: refusing path-traversal rar member %r", name)
                    return None
            rf.extractall(dest)
        return dest
    except Exception as e:
        logger.error("archive_pipeline: rar extract failed for %s: %s", archive_path, e)
        return None


def _extract_7z(archive_path: Path, dest: Path) -> Optional[Path]:
    try:
        import py7zr  # type: ignore[import-untyped]
    except ImportError:
        logger.warning(
            "archive_pipeline: cannot extract %s — py7zr library not installed. "
            "Install with: pip install py7zr.",
            archive_path,
        )
        return None
    try:
        with py7zr.SevenZipFile(archive_path, 'r') as sz:
            sz.extractall(path=dest)
        return dest
    except Exception as e:
        logger.error("archive_pipeline: 7z extract failed for %s: %s", archive_path, e)
        return None
