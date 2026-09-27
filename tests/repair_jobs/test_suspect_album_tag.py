"""Unit tests for SuspectAlbumTagDetector (Issue #1335).

Covers:
- Job discovery & registration
- Category & Finding type metadata registration
- Detection signals:
  1. Lone track + missing cover art
  2. Compilation-like album title + individual artist
  3. Lone track + large full release (track_count >= 4)
- Normal albums (single release, has art) are not falsely flagged
- Finding details payload includes reidentify_query
- Scope estimation
"""

import pytest
from unittest.mock import MagicMock

from core.repair_jobs import get_all_jobs
from core.repair_jobs.base import JobContext
from core.repair_jobs.suspect_album_tag import SuspectAlbumTagDetector, _is_various_artist, _COMPILATION_PATTERNS
from core.repair_worker import FINDING_TYPE_META, JOB_CATEGORIES, RepairWorker
from core.issues.activity import FIX_ACTIONS


class _FakeCursor:
    def __init__(self, rows, scalar=0):
        self._rows = rows
        self._scalar = scalar

    def execute(self, *_a, **_k):
        return self

    def fetchall(self):
        return self._rows

    def fetchone(self):
        if self._rows:
            return self._rows[0]
        return (self._scalar,)


class _FakeConn:
    def __init__(self, rows, scalar=0):
        self._rows = rows
        self._scalar = scalar

    def cursor(self):
        return _FakeCursor(self._rows, self._scalar)

    def close(self):
        pass


class _FakeDB:
    def __init__(self, rows=None, scalar=0):
        self._rows = rows or []
        self._scalar = scalar

    def _get_connection(self):
        return _FakeConn(self._rows, self._scalar)


def _row_dict(
    album_id=1,
    album_title="Some Album",
    thumb_url=None,
    full_track_count=1,
    artist_id=10,
    artist_name="Daughtry",
    track_id=100,
    track_title="It's Not Over",
    file_path="/music/Daughtry/Hitzone 43/01 - It's Not Over.flac",
):
    return {
        "album_id": album_id,
        "album_title": album_title,
        "thumb_url": thumb_url,
        "full_track_count": full_track_count,
        "artist_id": artist_id,
        "artist_name": artist_name,
        "track_id": track_id,
        "track_title": track_title,
        "file_path": file_path,
    }


def _context(rows, scalar=0):
    cfg = MagicMock()
    cfg.get.return_value = {}

    findings = []

    def create_finding(**kwargs):
        findings.append(kwargs)
        return True

    ctx = JobContext(
        db=_FakeDB(rows, scalar=scalar),
        transfer_folder="/music",
        config_manager=cfg,
        create_finding=create_finding,
    )
    return ctx, findings


def test_job_registration():
    jobs = get_all_jobs()
    assert "suspect_album_tag_detector" in jobs
    assert jobs["suspect_album_tag_detector"] is SuspectAlbumTagDetector
    assert SuspectAlbumTagDetector.job_id == "suspect_album_tag_detector"
    assert SuspectAlbumTagDetector.display_name == "Suspect Album Tags"


def test_metadata_and_categories_registration():
    assert "suspect_album_tag" in FINDING_TYPE_META
    assert FINDING_TYPE_META["suspect_album_tag"]["label"] == "Suspect Album Tags"
    assert FINDING_TYPE_META["suspect_album_tag"]["verb"] == "Re-identify"

    assert "suspect_album_tag_detector" in JOB_CATEGORIES
    assert JOB_CATEGORIES["suspect_album_tag_detector"] == "Tags & metadata"

    assert "suspect_album_tag" in FIX_ACTIONS
    assert FIX_ACTIONS["suspect_album_tag"] == ("reidentify", "Re-identify track")


def test_various_artist_detection():
    assert _is_various_artist("Various Artists")
    assert _is_various_artist("various")
    assert _is_various_artist("VA")
    assert _is_various_artist("v.a.")
    assert not _is_various_artist("Daughtry")
    assert not _is_various_artist("Taylor Swift")


def test_compilation_regex_patterns():
    assert _COMPILATION_PATTERNS.search("Hitzone 43")
    assert _COMPILATION_PATTERNS.search("Now That's What I Call Music 80")
    assert _COMPILATION_PATTERNS.search("Now 45")
    assert _COMPILATION_PATTERNS.search("Now 114")
    assert _COMPILATION_PATTERNS.search("NOW 80")
    assert _COMPILATION_PATTERNS.search("Top 40 Summer Hits")
    assert _COMPILATION_PATTERNS.search("The Best Of 2000s")
    assert _COMPILATION_PATTERNS.search("Greatest Hits")
    assert not _COMPILATION_PATTERNS.search("Leave This Town")
    assert not _COMPILATION_PATTERNS.search("The Now Show")


def test_flags_lone_track_missing_cover_art():
    row = _row_dict(
        album_title="Normal Title",
        thumb_url=None,  # Missing art
        full_track_count=1,
        artist_name="Daughtry",
    )
    ctx, findings = _context([row])
    result = SuspectAlbumTagDetector().scan(ctx)

    assert result.scanned == 1
    assert result.findings_created == 1
    assert len(findings) == 1
    f = findings[0]
    assert f["finding_type"] == "suspect_album_tag"
    assert "no cover art" in f["details"]["reasons"]
    assert f["details"]["reidentify_query"] == "It's Not Over Daughtry"


def test_flags_compilation_pattern_with_named_artist():
    row = _row_dict(
        album_title="Hitzone 43",
        thumb_url="http://art.jpg",  # has art
        full_track_count=1,
        artist_name="Daughtry",
    )
    ctx, findings = _context([row])
    result = SuspectAlbumTagDetector().scan(ctx)

    assert result.findings_created == 1
    f = findings[0]
    assert any("looks like a compilation" in r for r in f["details"]["reasons"])


def test_flags_lone_track_in_various_artists_album_with_named_track_artist():
    row = _row_dict(
        album_title="Random Party Album",
        thumb_url="http://art.jpg",
        full_track_count=1,
        artist_name="Daughtry",
    )
    row["album_artist_name"] = "Various Artists"
    ctx, findings = _context([row])
    result = SuspectAlbumTagDetector().scan(ctx)

    assert result.findings_created == 1
    f = findings[0]
    assert any("lone track in Various Artists compilation" in r for r in f["details"]["reasons"])
    assert f["details"]["reidentify_query"] == "It's Not Over Daughtry"


def test_skips_compilation_if_artist_is_various():
    row = _row_dict(
        album_title="Hitzone 43",
        thumb_url="http://art.jpg",
        full_track_count=1,
        artist_name="Various Artists",
    )
    ctx, findings = _context([row])
    result = SuspectAlbumTagDetector().scan(ctx)

    assert result.findings_created == 0
    assert len(findings) == 0


def test_flags_lone_track_when_full_release_has_many_tracks():
    row = _row_dict(
        album_title="Ordinary Album",
        thumb_url="http://art.jpg",
        full_track_count=12,  # Expected full release is 12 tracks, but we only have 1
        artist_name="Coldplay",
        track_title="Yellow",
    )
    ctx, findings = _context([row])
    result = SuspectAlbumTagDetector().scan(ctx)

    assert result.findings_created == 1
    f = findings[0]
    assert any("only 1 of 12 tracks locally owned" in r for r in f["details"]["reasons"])
    assert f["details"]["reidentify_query"] == "Yellow Coldplay"


def test_normal_single_with_art_not_flagged():
    row = _row_dict(
        album_title="Single Title",
        thumb_url="http://art.jpg",
        full_track_count=1,
        artist_name="Coldplay",
        track_title="Single Title",
    )
    ctx, findings = _context([row])
    result = SuspectAlbumTagDetector().scan(ctx)

    assert result.scanned == 1
    assert result.findings_created == 0
    assert result.skipped == 1
    assert len(findings) == 0


def test_scope_estimation():
    ctx, _ = _context([], scalar=5)
    scope = SuspectAlbumTagDetector().estimate_scope(ctx)
    assert scope == 5


def test_fix_finding_requires_reidentify_target():
    worker = MagicMock()
    # Test unbound method directly
    res = RepairWorker._fix_suspect_album_tag(
        worker,
        entity_type="track",
        entity_id="123",
        file_path="/path/track.mp3",
        details={"reidentify_query": "Test Track Artist"},
    )
    assert res["success"] is False
    assert "Re-identify button" in res["error"]
