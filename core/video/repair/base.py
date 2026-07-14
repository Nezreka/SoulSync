"""Video Library Maintenance — job base contract.

A 1:1 clone of the music side's repair-job standard (core/repair_jobs/base.py):
a job is a registered class with UI-driving attributes and one ``scan(context)``
that inspects the library and creates FINDINGS the user reviews on the Tools
page. Approving a finding runs the job's ``fix`` (approve == fix == resolved);
dismissing rejects it. Dedup lives in the DB layer — a re-scan never resurrects
a finding the user already handled.
"""

from __future__ import annotations

import threading
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Callable, Optional


class JobCancelled(Exception):
    """Raised by context.check_stop() when the user stopped the job."""


@dataclass
class JobResult:
    scanned: int = 0
    findings_created: int = 0
    findings_skipped_dedup: int = 0
    auto_fixed: int = 0
    errors: int = 0
    skipped: int = 0


@dataclass
class JobContext:
    """What a running job gets: the DB, its effective settings, a stop signal,
    and callbacks for findings + live progress (same names as music)."""

    db: object
    settings: dict = field(default_factory=dict)
    stop_event: Optional[threading.Event] = None
    create_finding: Optional[Callable[..., bool]] = None
    update_progress: Optional[Callable[..., None]] = None

    def should_stop(self) -> bool:
        return bool(self.stop_event is not None and self.stop_event.is_set())

    def check_stop(self) -> None:
        if self.should_stop():
            raise JobCancelled()

    def report(self, *, processed=None, total=None, phase=None, current_item=None) -> None:
        if self.update_progress:
            self.update_progress(processed=processed, total=total, phase=phase,
                                 current_item=current_item)


class VideoRepairJob(ABC):
    """Subclass + @register_job. Class attributes drive the whole UI."""

    job_id: str = ""
    display_name: str = ""
    description: str = ""
    help_text: str = ""
    icon: str = "🧰"
    default_enabled: bool = False
    default_interval_hours: int = 24
    default_settings: dict = {}
    setting_options: dict = {}
    auto_fix: bool = False
    # finding_type strings this job produces AND can fix (dispatch keys).
    finding_types: tuple = ()

    @abstractmethod
    def scan(self, context: JobContext) -> JobResult:
        """Inspect the library, create findings via context.create_finding."""

    def estimate_scope(self, context: JobContext) -> int:
        """Optional pre-count for the progress bar (0 = unknown)."""
        return 0

    def fix(self, context: JobContext, finding: dict, fix_action=None) -> dict:
        """Approve one finding. Return {'success': bool, 'action': str,
        'message'|'error': str} (the music fix-handler contract)."""
        return {"success": False, "error": "This finding type has no fix"}
