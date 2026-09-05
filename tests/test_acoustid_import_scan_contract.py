"""What the download verified, the scan may not overturn for free.

The user's report: files whose import-time AcoustID check passed came back as
"Wrong download" from the library scan days later. Both paths share one
decision core, so the flip came from their INPUTS — the download compares
against the provider's title/artist, the scan against whatever the catalogue
row happens to say now, and for cross-script metadata those are different
strings describing the same thing.

Strings are the noisy channel. The recording MBIDs AcoustID returns are not:
the same audio yields the same set every time. So the contract is an identity
one rather than a tolerance — if the fingerprint still identifies the same
recording it identified when the file was verified, the scan has learned
nothing new and has no standing to reverse that verdict. A fingerprint that
lands on a genuinely different recording is new information, and still fails.
"""

from __future__ import annotations

import json
import sqlite3
from types import SimpleNamespace

from core.repair_jobs.acoustid_scanner import AcoustIDScannerJob


class _Config:
    def get(self, key, default=None):
        return True if key == "features.library_v2" else default

    def set(self, *args, **kwargs):
        pass


def _make_context(tmp_path, *, verification_status, pipeline_result, findings):
    """One verified track on disk, with the import-time verdict recorded."""
    from core.library2.schema import ensure_library_v2_schema

    db_path = tmp_path / "contract.db"
    path = tmp_path / "02 - Apetitan.flac"
    path.write_bytes(b"audio")

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    ensure_library_v2_schema(conn)
    conn.execute("INSERT INTO lib2_artists (id, name) VALUES (7, 'Sawano Hiroyuki')")
    conn.execute("INSERT INTO lib2_albums (id, primary_artist_id, title) "
                 "VALUES (1, 7, 'Attack on Titan Season 2 OST')")
    conn.execute("INSERT INTO lib2_tracks (id, album_id, title, track_number, duration) "
                 "VALUES (42, 1, 'Apetitan', 2, 331000)")
    conn.execute(
        "INSERT INTO lib2_track_files "
        "(id, track_id, path, format, is_primary, verification_status, "
        " pipeline_result_json) VALUES (1, 42, ?, 'flac', 1, ?, ?)",
        (str(path), verification_status, json.dumps(pipeline_result)),
    )
    conn.commit()
    conn.close()

    class _DB:
        def _get_connection(self):
            c = sqlite3.connect(str(db_path))
            c.row_factory = sqlite3.Row
            return c

    def _create_finding(**kwargs):
        findings.append(kwargs)
        return True

    return SimpleNamespace(
        db=_DB(),
        transfer_folder=str(tmp_path),
        config_manager=_Config(),
        acoustid_client=None,
        create_finding=_create_finding,
        report_change=None,
        report_progress=lambda **kwargs: None,
        update_progress=lambda *args, **kwargs: None,
        check_stop=lambda: False,
        wait_if_paused=lambda: False,
        sleep_or_stop=lambda *args, **kwargs: False,
    ), str(path)


class _Client:
    """AcoustID identifying the file as its kanji-credited recording."""

    def __init__(self, mbid="mbid-apetitan"):
        self._mbid = mbid

    def lookup_with_status(self, path):
        return {
            "status": "ok",
            "best_score": 1.0,
            "recordings": [{
                "mbid": self._mbid, "title": "APETITAN",
                "artist": "Some Unrelated Band", "score": 1.0,
            }],
            "recording_mbids": [self._mbid],
        }


def _run(context, path, client, monkeypatch):
    job = AcoustIDScannerJob()
    monkeypatch.setattr(job, "_resolve_path", lambda p, _c: p)
    monkeypatch.setattr("core.tag_writer.read_file_tags", lambda _p: {})
    monkeypatch.setattr("core.tag_writer.write_verification_status",
                        lambda *_a, **_k: None)
    monkeypatch.setattr("core.acoustid_verification._resolve_expected_artist_aliases",
                        lambda _name: [])
    tracks = job._load_db_tracks(context)
    expected = tracks["lib2:42"]
    from core.repair_jobs.base import JobResult
    result = JobResult()
    job._scan_file(path, "lib2:42", expected, client, context, result,
                   0.80, 0.70, 0.60)
    return result


def test_same_recording_as_at_import_is_not_reopened(tmp_path, monkeypatch):
    findings = []
    context, path = _make_context(
        tmp_path,
        verification_status="verified",
        pipeline_result={"acoustid_recording_mbids": ["mbid-apetitan"]},
        findings=findings,
    )

    _run(context, path, _Client(), monkeypatch)

    assert findings == []


def test_a_different_recording_is_new_information_and_still_fails(tmp_path, monkeypatch):
    findings = []
    context, path = _make_context(
        tmp_path,
        verification_status="verified",
        pipeline_result={"acoustid_recording_mbids": ["mbid-something-else"]},
        findings=findings,
    )

    _run(context, path, _Client(), monkeypatch)

    assert len(findings) == 1
    assert findings[0]["finding_type"] == "acoustid_mismatch"


def test_without_a_recorded_import_verdict_the_scan_decides_alone(tmp_path, monkeypatch):
    findings = []
    context, path = _make_context(
        tmp_path,
        verification_status="verified",
        pipeline_result={},
        findings=findings,
    )

    _run(context, path, _Client(), monkeypatch)

    assert len(findings) == 1


def test_an_unverified_file_gets_no_protection(tmp_path, monkeypatch):
    # The MBIDs match, but nothing ever verified this file — there is no
    # verdict to preserve, so the scan's own judgement stands.
    findings = []
    context, path = _make_context(
        tmp_path,
        verification_status="unverified",
        pipeline_result={"acoustid_recording_mbids": ["mbid-apetitan"]},
        findings=findings,
    )

    _run(context, path, _Client(), monkeypatch)

    assert len(findings) == 1
