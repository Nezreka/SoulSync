"""A scan may not demote a file that already stands verified.

Reported sequence for one file, in this order:

    download            -> verified
    scan (before fix)   -> Mismatch
    scan (after fix)    -> unverified

All three are the same defect seen from different angles. The scan reads the
file's verification standing from the EMBEDDED TAG and writes its conclusion to
the CATALOGUE COLUMN (`tracks.verification_status`). When the tag is missing —
the import's tag write did not stick, the file was copied, a later retag
dropped it — the scan sees an untagged file, and its "an untagged file a scan
could not confirm becomes unverified" rule fires against a row the catalogue
says is verified. The tag is then stamped with that same wrong answer.

Before the cross-script fix this was unreachable for these files: the decision
was FAIL, which leaves `verification_status` alone — the "Mismatch" of step
two. Turning that FAIL into an honest SKIP is what walked the file into the
latent downgrade, so the sequence reads as though the fix caused it.

The rule: the tag and the column are two records of one fact, so either one
saying "verified" is the file standing verified. A scan that cannot confirm
adds nothing and must take nothing away.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from core.repair_jobs.acoustid_scanner import AcoustIDScannerJob
from core.repair_jobs.base import JobResult
from database.music_database import MusicDatabase


class _Config:
    def get(self, key, default=None):
        return default

    def set(self, *args, **kwargs):
        pass


class _Client:
    """A fingerprint whose artist is credited in another script — the shape
    that produces an honest SKIP rather than a confirmation."""

    def fingerprint_and_lookup(self, path):
        return {
            "best_score": 1.0,
            "recordings": [{"mbid": "mb-1", "title": "APETITAN",
                            "artist": "澤野弘之", "score": 1.0}],
        }


class _ConfirmingClient:
    """A fingerprint that agrees with the catalogue outright."""

    def fingerprint_and_lookup(self, path):
        return {
            "best_score": 1.0,
            "recordings": [{"mbid": "mb-1", "title": "Apetitan",
                            "artist": "Sawano Hiroyuki", "score": 1.0}],
        }


@pytest.fixture
def scan(tmp_path, monkeypatch):
    """Run one file through the scanner and report what was persisted."""
    counter = {"n": 0}

    def _run(*, db_status, tag_status, client=None):
        counter["n"] += 1
        db = MusicDatabase(str(tmp_path / f"m{counter['n']}.db"))
        path = tmp_path / "02 - Apetitan.flac"
        path.write_bytes(b"audio")

        with db._get_connection() as conn:
            conn.execute("INSERT INTO artists (id, name) VALUES ('a1', 'Sawano Hiroyuki')")
            conn.execute("INSERT INTO albums (id, title, artist_id) "
                         "VALUES ('al1', 'AoT Season 2 OST', 'a1')")
            conn.execute(
                "INSERT INTO tracks (id, title, artist_id, album_id, file_path, "
                "duration, verification_status) VALUES ('t1', 'Apetitan', 'a1', "
                "'al1', ?, 331000, ?)",
                (str(path), db_status))
            conn.commit()

        findings, tags_written = [], []
        context = SimpleNamespace(
            db=db, transfer_folder=str(tmp_path), config_manager=_Config(),
            acoustid_client=None,
            create_finding=lambda **kw: findings.append(kw) or True,
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
        # Alias resolution lives inside the shared verifier, which is where
        # the scan reaches it from — the real one would open a database
        # connection and hit MusicBrainz.
        monkeypatch.setattr(
            "core.acoustid_verification._resolve_expected_artist_aliases",
            lambda _n: [])

        job = AcoustIDScannerJob()
        monkeypatch.setattr(job, "_resolve_path", lambda p, _c: p)
        tracks = job._load_db_tracks(context)
        job._scan_file(str(path), "t1", tracks["t1"], client or _Client(),
                       context, JobResult(), 0.80, 0.70, 0.60)

        with db._get_connection() as conn:
            stored = conn.execute(
                "SELECT verification_status FROM tracks WHERE id = 't1'").fetchone()[0]
        return SimpleNamespace(status=stored, tags_written=tags_written,
                               findings=findings)

    return _run


def test_an_inconclusive_scan_does_not_demote_a_verified_row(scan):
    # The user's exact case: catalogue says verified, the tag is gone.
    out = scan(db_status="verified", tag_status=None)

    assert out.status == "verified"
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
