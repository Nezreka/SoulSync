"""Base classes for the multi-job Library Maintenance Worker."""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
import os
import threading
from typing import Any, Callable, Dict, List, Optional

from utils.logging_config import get_logger

logger = get_logger("repair_job.base")

# The recoverable quarantine removed duplicates and dead files move into. Dot-
# prefixed ON PURPOSE: Navidrome, Plex and Jellyfin all skip hidden folders by
# default, so the quarantine stays out of everyone's library with no per-server
# setup. The old bare name put deleted tracks straight back on people's media
# servers (Discord, Jose: "the deleted folder that navidrome still picks up").
DELETED_QUARANTINE_DIRNAME = '.deleted'
LEGACY_DELETED_DIRNAME = 'deleted'


def deleted_quarantine_root(transfer_folder: str) -> str:
    """The quarantine folder under ``transfer_folder`` — always the hidden
    spelling, migrating a legacy bare ``deleted`` folder when one exists.

    The migration is a single ``os.rename``, so every file a user already has in
    quarantine disappears from their media server the moment any tool touches the
    quarantine again — no manual cleanup. If both spellings exist (someone made
    ``.deleted`` by hand next to an old ``deleted``) the legacy folder is left
    alone rather than merged: both are still recognised by every walker skip, and
    merging directory trees is how files get clobbered. A failed rename (a media
    server holding the handle, a read-only mount) falls back to the legacy path so
    quarantining KEEPS WORKING exactly as before rather than failing the tool.
    """
    canonical = os.path.join(transfer_folder, DELETED_QUARANTINE_DIRNAME)
    legacy = os.path.join(transfer_folder, LEGACY_DELETED_DIRNAME)
    if os.path.isdir(legacy) and not os.path.exists(canonical):
        try:
            os.rename(legacy, canonical)
            logger.info("Renamed quarantine folder %s -> %s (hidden from media servers)",
                        legacy, canonical)
        except OSError as e:
            logger.warning("Could not rename %s to %s (%s) — using the legacy folder",
                           legacy, canonical, e)
            return legacy
    if os.path.isdir(legacy) and os.path.isdir(canonical):
        logger.info("Both %s and %s exist — new quarantined files go to the hidden one",
                    LEGACY_DELETED_DIRNAME, DELETED_QUARANTINE_DIRNAME)
    return canonical


def is_internal_transfer_dir(path: str, transfer_folder: str) -> bool:
    """True for a SoulSync-owned folder inside the library that no maintenance
    job may treat as library content.

    Two of them, for different reasons:

    * ``<transfer>/deleted`` — the recoverable quarantine removed duplicates and
      dead files are moved into (#746). Re-scanning it makes a just-removed file
      reappear as a finding on the next pass.
    * ``<transfer>/.soulsync_atomic_staging`` — a half-downloaded album, mid
      atomic publish. These files are deliberately not in the database yet, so
      the orphan detector would call every one of them an orphan, and the empty
      folder cleaner would delete the tree out from under an in-flight publish.

    Path-based rather than name-based so it works for bottom-up walks too, where
    pruning ``dirs`` in place does nothing.
    """
    try:
        target = os.path.normpath(os.path.abspath(path))
        base = os.path.normpath(os.path.abspath(transfer_folder))
    except (OSError, ValueError):
        return False
    # both quarantine spellings: '.deleted' is what SoulSync writes now, bare
    # 'deleted' is what older installs still carry until the rename migration
    # in deleted_quarantine_root() runs
    for name in (DELETED_QUARANTINE_DIRNAME, LEGACY_DELETED_DIRNAME,
                 '.soulsync_atomic_staging'):
        owned = os.path.join(base, name)
        if target == owned or target.startswith(owned + os.sep):
            return True
    return False


def skip_deleted_quarantine(root: str, dirs: list, transfer_folder: str) -> None:
    """In-place prune of SoulSync's own folders from an ``os.walk`` ``dirs``
    list (topdown walks only).

    Named for the quarantine it originally guarded; it now also prunes the
    atomic-publish staging tree, which lives inside the transfer dir since the
    sibling location proved unwritable on Docker. Both are anchored to the
    top level, so a legitimately-named ``deleted`` folder deeper in the library
    is untouched.
    """
    dirs[:] = [d for d in dirs
               if not is_internal_transfer_dir(os.path.join(root, d), transfer_folder)]


@dataclass
class JobResult:
    """Result of a single job scan run."""
    scanned: int = 0
    findings_created: int = 0
    findings_skipped_dedup: int = 0  # Findings the worker already had a row for
    auto_fixed: int = 0
    errors: int = 0
    skipped: int = 0


@dataclass
class JobContext:
    """Shared resources passed to every repair job during execution."""

    db: Any                          # MusicDatabase instance
    transfer_folder: str             # Resolved transfer folder path
    config_manager: Any              # ConfigManager instance

    # API clients (may be None if unavailable)
    spotify_client: Any = None
    itunes_client: Any = None
    mb_client: Any = None
    acoustid_client: Any = None
    metadata_cache: Any = None
    stop_event: Optional[threading.Event] = None

    # Callbacks
    create_finding: Optional[Callable] = None
    should_stop: Optional[Callable[[], bool]] = None
    is_paused: Optional[Callable[[], bool]] = None
    update_progress: Optional[Callable[[int, int], None]] = None
    report_progress: Optional[Callable] = None  # Rich progress: (phase, log_line, log_type, scanned, total)

    def check_stop(self) -> bool:
        """Return True if the worker should stop."""
        if self.stop_event and self.stop_event.is_set():
            return True
        return self.should_stop() if self.should_stop else False

    def is_spotify_rate_limited(self) -> bool:
        """Check if Spotify is currently under a global rate limit ban.

        Jobs should call this before making Spotify API calls in their
        scan loops to avoid churning through items uselessly.
        """
        try:
            from core.spotify_client import SpotifyClient
            return SpotifyClient.is_rate_limited()
        except Exception:
            return False

    def wait_if_paused(self):
        """Block until unpaused or stopped. Returns True if should stop."""
        while self.is_paused and self.is_paused():
            if self.check_stop():
                return True
            if self.stop_event:
                self.stop_event.wait(0.2)
            else:
                import time
                time.sleep(0.2)
        return self.check_stop()

    def sleep_or_stop(self, seconds: float, step: float = 0.2) -> bool:
        """Sleep in small increments so stop requests can interrupt quickly."""
        if seconds <= 0:
            return self.check_stop()
        remaining = seconds
        while remaining > 0:
            if self.check_stop():
                return True
            chunk = min(step, remaining)
            if self.stop_event:
                self.stop_event.wait(chunk)
            else:
                import time
                time.sleep(chunk)
            remaining -= chunk
        return self.check_stop()


class RepairJob(ABC):
    """Abstract base class for all repair jobs."""

    # Subclasses MUST set these class attributes
    job_id: str = ''
    display_name: str = ''
    description: str = ''
    help_text: str = ''  # Extended explanation shown in the info modal
    icon: str = ''
    default_enabled: bool = False
    default_interval_hours: int = 24
    default_settings: Dict[str, Any] = {}
    # Optional {setting_key: [allowed values]} — the UI renders a dropdown for
    # these instead of a free-text box. Keys not listed render by value type.
    setting_options: Dict[str, list] = {}
    auto_fix: bool = False
    # True for jobs that MOVE or REWRITE real library files based on catalogue
    # paths (reorganize, retag, track numbers, unknown-artist moves, artist
    # splits). When such a job runs LIVE (dry_run off), the worker first proves
    # it can actually SEE the library: with 'Report Real Path' off in Navidrome
    # (or a broken Docker mount) the catalogue holds paths that resolve to
    # nothing — or worse, to the WRONG files via the pattern-probing resolver —
    # and a live run then rearranges a library it is effectively blind to
    # (Discord, Jose: good tracks swept into the deleted quarantine).
    writes_library_files: bool = False

    @abstractmethod
    def scan(self, context: JobContext) -> JobResult:
        """Execute the job scan. Must be implemented by each job.

        Should periodically call context.check_stop() and
        context.wait_if_paused() to respect worker lifecycle.
        """
        ...

    def estimate_scope(self, context: JobContext) -> int:
        """Optional: return estimated total items for progress bar.
        Return 0 if unknown."""
        return 0

    def get_config_key(self, setting: str) -> str:
        """Get the full config key path for a job setting."""
        return f"repair.jobs.{self.job_id}.{setting}"
