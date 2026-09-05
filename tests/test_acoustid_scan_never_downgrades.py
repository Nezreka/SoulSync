"""A scan may not demote a file that already stands verified.

Reported sequence for one file, in this order:

    download            -> verified
    scan (before fix)   -> Mismatch
    scan (after fix)    -> unverified

All three are the same defect seen from different angles. The scan reads the
file's verification standing from the EMBEDDED TAG and writes its conclusion to
the CATALOGUE COLUMN. When the tag is missing — the import's tag write did not
stick, the file was copied, a later retag dropped it — the scan sees an
untagged file, and its "an untagged file a scan could not confirm becomes
unverified" rule fires against a row the catalogue says is verified. The tag is
then stamped with that same wrong answer.

Before the cross-script fix this was unreachable for these files: the decision
was FAIL, which leaves ``verification_status`` alone and only records
``acoustid_status='fail'`` — the "Mismatch" of step two. Turning that FAIL into
an honest SKIP is what walked the file into the latent downgrade.

The rule: the tag and the column are two records of one fact, so either one
saying "verified" is the file standing verified. A scan that cannot confirm
adds nothing and must take nothing away.
"""

from __future__ import annotations

import json
import sqlite3
from types import SimpleNamespace

import pytest

from core.repair_jobs.acoustid_scanner import AcoustIDScannerJob
from core.repair_jobs.base import JobResult


class _Config:
    def get(self, key, default=None):
        return True if key == "features.library_v2" else default

    def set(self, *args, **kwargs):
        pass


class _Client:
    """A fingerprint whose artist is credited in another script — the shape
    that produces an honest SKIP rather than a confirmation."""

    def lookup_with_status(self, path):
        return {
            "status": "ok", "best_score": 1.0, "recording_mbids": ["mb-1"],
            "recordings": [{"mbid": "mb-1", "title": "APETITAN",
                            "artist": "澤野弘之", "score": 1.0}],
        }


class _ConfirmingClient:
    """A fingerprint that agrees with the catalogue outright."""

    def lookup_with_status(self, path):
        return {
            "status": "ok", "best_score": 1.0, "recording_mbids": ["mb-1"],
            "recordings": [{"mbid": "mb-1", "title": "Apetitan",
                            "artist": "Sawano Hiroyuki", "score": 1.0}],
        }


@pytest.fixture
def scan(tmp_path, monkeypatch):
    """Run one file through the scanner and report what was persisted."""
    def _run(*, db_status, tag_status, client=None):
        db_path = tmp_path / f"scan-{db_status}-{tag_status}.db"
        path = tmp_path / "02 - Apetitan.flac"
        path.write_bytes(b"audio")

        from core.library2.schema import ensure_library_v2_schema

        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        ensure_library_v2_schema(conn)
        conn.execute("INSERT INTO lib2_artists (id, name) VALUES (7, 'Sawano Hiroyuki')")
        conn.execute("INSERT INTO lib2_albums (id, primary_artist_id, title) "
                     "VALUES (1, 7, 'AoT Season 2 OST')")
        conn.execute("INSERT INTO lib2_tracks (id, album_id, title, duration) "
                     "VALUES (42, 1, 'Apetitan', 331000)")
        conn.execute(
            "INSERT INTO lib2_track_files (id, track_id, path, format, is_primary, "
            "verification_status, pipeline_result_json) VALUES (1, 42, ?, 'flac', 1, ?, ?)",
            (str(path), db_status, json.dumps({})),
        )
        conn.commit()
        conn.close()

        class _DB:
            def _get_connection(self):
                c = sqlite3.connect(str(db_path))
                c.row_factory = sqlite3.Row
                return c

        findings, tags_written = [], []
        context = SimpleNamespace(
            db=_DB(), transfer_folder=str(tmp_path), config_manager=_Config(),
            acoustid_client=None,
            create_finding=lambda **kw: findings.append(kw) or True,
            report_change=None,
            report_progress=lambda **kw: None,
            update_progress=lambda *a, **kw: None,
            check_stop=lambda: False, wait_if_paused=lambda: False,
            sleep_or_stop=lambda *a, **kw: False,
        )

        monkeypatch.setattr(
            "core.tag_writer.read_file_tags",
            lambda _p: {"verification_status": tag_status} if tag_status else {})
        monkeypatch.setattr(
            "core.tag_writer.write_verification_status",
            lambda _p, status: tags_written.append(status) or True)
        monkeypatch.setattr(
            "core.acoustid_verification._resolve_expected_artist_aliases",
            lambda _n: [])

        job = AcoustIDScannerJob()
        monkeypatch.setattr(job, "_resolve_path", lambda p, _c: p)
        tracks = job._load_db_tracks(context)
        job._scan_file(str(path), "lib2:42", tracks["lib2:42"],
                       client or _Client(), context, JobResult(),
                       0.80, 0.70, 0.60)

        c = sqlite3.connect(str(db_path))
        stored = c.execute(
            "SELECT verification_status, acoustid_status FROM lib2_track_files "
            "WHERE id = 1").fetchone()
        c.close()
        return SimpleNamespace(status=stored[0], acoustid=stored[1],
                               tags_written=tags_written, findings=findings)

    return _run


def test_an_inconclusive_scan_does_not_demote_a_verified_row(scan):
    # The user's exact case: catalogue says verified, the tag is gone.
    out = scan(db_status="verified", tag_status=None)

    assert out.status == "verified"
    assert out.acoustid == "skip"
    assert "unverified" not in out.tags_written


def test_the_missing_tag_is_healed_rather_than_overwritten(scan):
    out = scan(db_status="verified", tag_status=None)

    assert out.tags_written == ["verified"]


def test_a_genuinely_unconfirmed_file_still_becomes_unverified(scan):
    # Nothing has ever verified this one, so there is no standing to protect.
    out = scan(db_status=None, tag_status=None)

    assert out.status == "unverified"


def test_a_human_decision_in_the_catalogue_is_respected_without_a_tag(scan):
    out = scan(db_status="human_verified", tag_status=None)

    assert out.status == "human_verified"
    assert out.tags_written == []
    assert out.findings == []


def test_a_force_import_is_not_promoted_by_a_confirming_scan(scan):
    out = scan(db_status="force_imported", tag_status=None,
               client=_ConfirmingClient())

    assert out.status == "force_imported"


def test_an_unverified_row_is_still_promoted_by_a_confirming_scan(scan):
    out = scan(db_status="unverified", tag_status=None,
               client=_ConfirmingClient())

    assert out.status == "verified"


def test_the_tag_still_wins_when_the_catalogue_has_nothing(scan):
    """The tag travels with the file, so it is authoritative on a row the
    catalogue never recorded a standing for."""
    out = scan(db_status=None, tag_status="verified")

    assert out.status == "verified"
    assert out.tags_written == []
