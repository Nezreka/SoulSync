"""Video Library Maintenance — job registry (mirrors core/repair_jobs)."""

from __future__ import annotations

from core.video.repair.base import (JobCancelled, JobContext, JobResult,  # noqa: F401
                                    VideoRepairJob)

JOB_REGISTRY: dict = {}

# Every job module (registration is an import side-effect, like music's list).
_JOB_MODULES = [
    "missing_episodes",
    "movie_collections",
    "quality_upgrade",
    "broken_files",
    "metadata_gaps",
    "duplicate_movies",
    "wishlist_audit",
    "youtube_ghosts",
]


def register_job(cls):
    JOB_REGISTRY[cls.job_id] = cls
    return cls


def get_all_jobs() -> dict:
    """job_id -> job class, importing every job module once."""
    import importlib
    for mod in _JOB_MODULES:
        importlib.import_module(f"core.video.repair.{mod}")
    return dict(JOB_REGISTRY)
