"""Library Re-tag as a job again — scoped, and honest about hand-set fields.

On dev this job existed but read `albums`/`artists`/`tracks`, and it scanned
the WHOLE albums table on every run with no way to narrow it. Library v2
deleted the tables it read, and its replacement — `core/library2/retag.py` —
came back as a dialog only: no schedule, no findings, no dry run. Nothing
noticed tag drift on its own any more; you had to open an album and look.

So the job returns, on top of the same engine the dialog uses:

* scoped, via the file allowlist a user-triggered artist run resolves. The
  unscoped album query is exactly how "run this for one artist" produced
  library-wide findings, one Fix All away from touching everything.
* dry-run by design. The scan reports; nothing touches a file until a finding
  is applied.
* a hand-set field is reported as a CHOICE, never quietly overwritten. The
  finding carries what the catalogue wanted so the user can settle it.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from core.repair_jobs.base import JobContext
from core.repair_jobs.library_retag import LibraryRetagJob


@pytest.fixture()
def subjects(monkeypatch):
    """Two catalogue files; the scan sees them through the shared enumerator."""
    rows = [
        {"track_id": 1, "path": "/music/A/Alb/01 - One.flac", "title": "One",
         "artist_name": "Drake", "album_title": "Views", "duration": 200},
        {"track_id": 2, "path": "/music/A/Alb/02 - Two.flac", "title": "Two",
         "artist_name": "Drake", "album_title": "Views", "duration": 200},
    ]
    monkeypatch.setattr(
        "core.library2.maintenance_subjects.active_file_subjects",
        lambda *_a, **_k: [dict(r) for r in rows])
    return rows


def _context(scope=None):
    cfg = MagicMock()
    cfg.get.side_effect = lambda key, default=None: default
    findings = []
    ctx = JobContext(
        db=SimpleNamespace(_get_connection=lambda: SimpleNamespace(close=lambda: None)),
        transfer_folder="/music",
        config_manager=cfg,
        create_finding=lambda **kw: (findings.append(kw) or True),
    )
    if scope is not None:
        ctx.scope = scope
    return ctx, findings


def _preview(monkeypatch, entries):
    monkeypatch.setattr("core.library2.retag.track_contexts",
                        lambda _conn, ids: [{"id": i} for i in ids])
    monkeypatch.setattr("core.library2.retag.tag_preview",
                        lambda contexts: [entries[c["id"]] for c in contexts
                                          if c["id"] in entries])


def _entry(track_id, *, diff, manual=False, error=None):
    return {"track_id": track_id, "title": f"T{track_id}", "track_number": track_id,
            "album_id": 9, "album_title": "Views", "album_type": "album",
            "file_path": f"/music/A/Alb/0{track_id}.flac",
            "diff": diff, "has_changes": bool(diff),
            "has_manual_conflict": manual, "error": error}


def _row(field="Title", *, manual=False, provider=None):
    row = {"field": field, "file_key": field.lower(), "file_value": "old",
           "db_value": "new", "changed": True, "manual": manual}
    if provider is not None:
        row["provider_value"] = provider
    return row


def test_a_file_whose_tags_are_behind_raises_one_finding(subjects, monkeypatch):
    _preview(monkeypatch, {1: _entry(1, diff=[_row()]),
                           2: _entry(2, diff=[])})
    ctx, findings = _context()

    result = LibraryRetagJob().scan(ctx)

    assert result.findings_created == 1
    assert findings[0]["finding_type"] == "library_retag"
    assert findings[0]["entity_id"] == "lib2:1"
    assert findings[0]["entity_type"] == "track"


def test_a_file_whose_tags_match_raises_nothing(subjects, monkeypatch):
    _preview(monkeypatch, {1: _entry(1, diff=[]), 2: _entry(2, diff=[])})
    ctx, findings = _context()

    assert LibraryRetagJob().scan(ctx).findings_created == 0
    assert findings == []


def test_an_unreadable_file_is_an_error_not_a_finding(subjects, monkeypatch):
    """A finding promises a fix. Nothing can be written to a file that cannot
    be read, so raising one would create a row whose button can only fail."""
    _preview(monkeypatch, {1: _entry(1, diff=[], error="File not found on disk"),
                           2: _entry(2, diff=[])})
    ctx, findings = _context()

    result = LibraryRetagJob().scan(ctx)

    assert findings == []
    assert result.errors == 1


def test_the_finding_carries_the_conflict_so_the_user_can_settle_it(
        subjects, monkeypatch):
    _preview(monkeypatch, {
        1: _entry(1, diff=[_row(manual=True, provider="from the catalogue")],
                  manual=True),
        2: _entry(2, diff=[])})
    ctx, findings = _context()

    LibraryRetagJob().scan(ctx)

    details = findings[0]["details"]
    assert details["has_manual_conflict"] is True
    assert details["manual_fields"] == ["Title"]
    assert details["diff"][0]["provider_value"] == "from the catalogue"
    assert "set by hand" in findings[0]["description"]


def test_a_scoped_run_only_looks_at_the_files_it_was_given(subjects, monkeypatch):
    """An unscoped album query is how "run this for one artist" set the whole
    library moving. A declared scope has to actually narrow the scan."""
    _preview(monkeypatch, {1: _entry(1, diff=[_row()]),
                           2: _entry(2, diff=[_row()])})
    ctx, findings = _context(scope={"file_paths": ["/music/A/Alb/02 - Two.flac"]})

    result = LibraryRetagJob().scan(ctx)

    assert result.findings_created == 1
    assert findings[0]["entity_id"] == "lib2:2"


def test_the_job_declares_that_it_honours_a_file_scope():
    """A job that does not declare this has the scope REFUSED rather than
    silently widened — so the declaration is the contract, not a label."""
    assert LibraryRetagJob.supports_file_scope is True


def test_the_scan_writes_nothing(subjects, monkeypatch):
    _preview(monkeypatch, {1: _entry(1, diff=[_row()]), 2: _entry(2, diff=[])})
    monkeypatch.setattr(
        "core.library2.retag.write_tags",
        lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("scan must not write")))
    ctx, _findings = _context()

    LibraryRetagJob().scan(ctx)
