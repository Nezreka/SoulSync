"""Edition-aware bundle inventory for completed acquisition downloads.

The download client reports where it stored the finished bundle from ITS OWN
filesystem view. This module owns everything between that raw client path and
a structured, matchable inventory: remote path mapping, path health, the safe
extraction/walk step, and per-file tag facts (audit §13.4 steps 2-6).

Filesystem and mutagen I/O happen here, before any database transaction is
opened. Persistence lives in :mod:`core.acquisition.imports`; the caller runs
this collector first and stores the outcome in a separate short transaction.

Outcomes are deliberately split by retryability: an unreadable path is
``path_unreadable`` (transient — mounts and mappings can be fixed while the
import stays pending), while a readable bundle without audio is
``no_audio_files`` (a broken candidate, terminal for this grab).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, Mapping, Optional, Tuple

from utils.logging_config import get_logger


logger = get_logger("acquisition.bundle_inventory")


INVENTORY_OK = "ok"
INVENTORY_PATH_UNREADABLE = "path_unreadable"
INVENTORY_NO_AUDIO_FILES = "no_audio_files"


@dataclass(frozen=True)
class InventoryFile:
    """Matchable facts about one audio file inside a completed bundle."""

    relative_path: str
    size_bytes: int
    container: Optional[str]
    bitrate: Optional[int]
    duration_seconds: Optional[float]
    title: Optional[str]
    artist: Optional[str]
    album: Optional[str]
    track_number: Optional[int]
    disc_number: Optional[int]
    tags_available: bool

    def to_dict(self) -> Dict[str, Any]:
        return {
            "relative_path": self.relative_path,
            "size_bytes": self.size_bytes,
            "container": self.container,
            "bitrate": self.bitrate,
            "duration_seconds": self.duration_seconds,
            "title": self.title,
            "artist": self.artist,
            "album": self.album,
            "track_number": self.track_number,
            "disc_number": self.disc_number,
            "tags_available": self.tags_available,
        }


@dataclass(frozen=True)
class BundleInventoryResult:
    status: str
    reported_path: str
    resolved_path: Optional[str]
    files: Tuple[InventoryFile, ...]
    error: Optional[str] = None

    @property
    def ok(self) -> bool:
        return self.status == INVENTORY_OK

    @property
    def retryable(self) -> bool:
        return self.status == INVENTORY_PATH_UNREADABLE


def parse_position(value: Any) -> Optional[int]:
    """Parse a tag position like ``7``, ``'07'`` or ``'7/12'`` to an int."""
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value > 0 else None
    text = str(value).strip()
    if not text:
        return None
    head = text.split("/", 1)[0].strip()
    try:
        number = int(head)
    except ValueError:
        return None
    return number if number > 0 else None


def _clean_text(value: Any) -> Optional[str]:
    text = str(value).strip() if value is not None else ""
    return text or None


def _read_file_facts(
    path: Path,
    base: Path,
    tag_reader: Callable[[str], Mapping[str, Any]],
) -> InventoryFile:
    try:
        size_bytes = int(path.stat().st_size)
    except OSError:
        size_bytes = 0
    try:
        data: Mapping[str, Any] = tag_reader(str(path)) or {}
    except Exception as exc:  # noqa: BLE001 - reader contract is non-raising
        logger.debug("Tag read failed for %s: %s", path, exc)
        data = {}
    raw_tags = data.get("tags")
    tags: Mapping[str, Any] = raw_tags if isinstance(raw_tags, Mapping) else {}
    try:
        relative = path.relative_to(base).as_posix()
    except ValueError:
        relative = path.name
    duration = data.get("duration")
    try:
        duration_seconds = float(duration) if duration else None
    except (TypeError, ValueError):
        duration_seconds = None
    try:
        bitrate = int(data.get("bitrate") or 0) or None
    except (TypeError, ValueError):
        bitrate = None
    return InventoryFile(
        relative_path=relative,
        size_bytes=size_bytes,
        container=_clean_text(data.get("format")),
        bitrate=bitrate,
        duration_seconds=duration_seconds,
        title=_clean_text(tags.get("title")),
        artist=_clean_text(tags.get("artist")),
        album=_clean_text(tags.get("album")),
        track_number=parse_position(tags.get("tracknumber")),
        disc_number=parse_position(tags.get("discnumber")),
        tags_available=bool(data.get("available")),
    )


def collect_bundle_inventory(
    reported_path: Optional[str],
    *,
    config_get: Optional[Callable[..., Any]] = None,
    path_resolver: Optional[Callable[..., Optional[str]]] = None,
    audio_collector: Optional[Callable[[Path], Iterable[Path]]] = None,
    tag_reader: Optional[Callable[[str], Mapping[str, Any]]] = None,
) -> BundleInventoryResult:
    """Resolve, health-check and inventory one completed download bundle.

    The persisted ``output_path`` stays the immutable client-side correlation;
    mapping is applied here on every attempt so a corrected
    ``download_source.usenet_path_mappings`` entry heals pending imports
    without touching the database row.
    """
    reported = str(reported_path or "").strip()
    if not reported:
        return BundleInventoryResult(
            status=INVENTORY_PATH_UNREADABLE,
            reported_path=reported,
            resolved_path=None,
            files=(),
            error="Download completed without an output path",
        )

    if path_resolver is None:
        from core.download_plugins.album_bundle import resolve_reported_save_path
        path_resolver = resolve_reported_save_path
    resolved = str(path_resolver(reported, config_get) or reported)
    root = Path(resolved)

    from core.archive_pipeline import AUDIO_EXTENSIONS

    try:
        if root.is_file():
            # Some clients report a file path for single-NZB jobs.
            base = root.parent
            audio_paths = [root] if root.suffix.lower() in AUDIO_EXTENSIONS else []
        elif root.is_dir():
            base = root
            if audio_collector is None:
                from core.archive_pipeline import collect_audio_after_extraction
                audio_collector = collect_audio_after_extraction
            audio_paths = [Path(item) for item in audio_collector(root)]
        else:
            return BundleInventoryResult(
                status=INVENTORY_PATH_UNREADABLE,
                reported_path=reported,
                resolved_path=None,
                files=(),
                error=(
                    "Completed download path is not readable from this "
                    "process; check remote path mappings"
                ),
            )
    except OSError as exc:
        return BundleInventoryResult(
            status=INVENTORY_PATH_UNREADABLE,
            reported_path=reported,
            resolved_path=str(root),
            files=(),
            error=f"Completed download path could not be walked: {exc}",
        )

    if not audio_paths:
        return BundleInventoryResult(
            status=INVENTORY_NO_AUDIO_FILES,
            reported_path=reported,
            resolved_path=str(root),
            files=(),
            error="Completed download contains no audio files",
        )

    if tag_reader is None:
        from core.library.file_tags import read_embedded_tags
        tag_reader = read_embedded_tags
    files = tuple(
        _read_file_facts(path, base, tag_reader)
        for path in sorted(set(audio_paths))
    )
    return BundleInventoryResult(
        status=INVENTORY_OK,
        reported_path=reported,
        resolved_path=str(root),
        files=files,
    )


__all__ = [
    "INVENTORY_NO_AUDIO_FILES",
    "INVENTORY_OK",
    "INVENTORY_PATH_UNREADABLE",
    "BundleInventoryResult",
    "InventoryFile",
    "collect_bundle_inventory",
    "parse_position",
]
