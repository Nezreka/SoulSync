"""Per-artist run scope for repair jobs (JobContext.scope)."""

from __future__ import annotations

from core.repair_jobs.base import JobContext


def _ctx(scope=None):
    return JobContext(db=None, transfer_folder="/t", config_manager=None, scope=scope)


def test_scope_artist_name_default_none():
    assert _ctx().scope_artist_name() is None
    assert _ctx(scope={}).scope_artist_name() is None
    assert _ctx(scope={"artist_name": "  "}).scope_artist_name() is None


def test_scope_artist_name_returns_trimmed():
    assert _ctx(scope={"artist_name": " Drake "}).scope_artist_name() == "Drake"


def test_scoped_jobs_declare_support():
    from core.repair_jobs import get_all_jobs
    registry = get_all_jobs()
    for job_id in (
        "metadata_gap_filler",
        "album_tag_consistency",
    ):
        assert registry[job_id].supports_artist_scope is True, job_id


def test_native_jobs_use_neutral_ids():
    from core.repair_jobs import get_all_jobs
    registry = get_all_jobs()
    assert "quality_upgrade_scan" in registry
    assert "skip_audit_cleanup" in registry
    assert "monitored_discography_refresh" in registry
    assert "monitoring_list_reconcile" in registry
    assert not any(job_id.startswith("lib2_") for job_id in registry)
