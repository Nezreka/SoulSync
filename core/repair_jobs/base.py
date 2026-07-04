"""Base classes for the multi-job Library Maintenance Worker."""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
import os
import threading
from typing import Any, Callable, Dict, List, Optional


def get_scope_artist(context: Any) -> Optional[str]:
    """The artist a user-triggered run is scoped to, or None for library-wide.

    Free function (not a JobContext method) so jobs stay compatible with the
    SimpleNamespace context fakes the test suite builds.
    """
    scope = getattr(context, "scope", None)
    if isinstance(scope, dict):
        name = str(scope.get("artist_name") or "").strip()
        return name or None
    return None


def skip_deleted_quarantine(root: str, dirs: list, transfer_folder: str) -> None:
    """In-place prune of the ``<transfer>/deleted`` quarantine from an ``os.walk``
    ``dirs`` list (topdown walks only).

    Removed duplicates / dead files are MOVED into ``<transfer>/deleted`` rather
    than hard-deleted (recoverable; the reorganizer already skips it, #746). The
    transfer-walking repair jobs must not re-scan that quarantine, or a
    just-de-duplicated file immediately reappears as an orphan/finding on the next
    pass. Anchored to the top-level ``<transfer>/deleted`` so a legitimately-named
    ``deleted`` folder deeper in the library is untouched."""
    deleted_root = os.path.normpath(os.path.join(transfer_folder, 'deleted'))
    dirs[:] = [d for d in dirs
               if os.path.normpath(os.path.join(root, d)) != deleted_root]


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

    # Optional run scope for user-triggered runs (e.g. {'artist_name': 'Drake'}
    # from a Library artist page). Only jobs declaring supports_artist_scope
    # honor it; scheduled runs never carry one.
    scope: Optional[Dict[str, Any]] = None

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

    def scope_artist_name(self) -> Optional[str]:
        """The artist this run is scoped to, or None for a full-library run."""
        return get_scope_artist(self)

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
    # Whether this job's scan honors JobContext.scope['artist_name'] (user-
    # triggered runs from a Library artist page). Library-wide otherwise.
    supports_artist_scope: bool = False

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
