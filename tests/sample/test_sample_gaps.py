"""Sample Studio — coverage gaps for the backend.

The phase test files (test_analyze / test_render / test_sample_e2e / test_stems)
cover the happy paths. This file closes the remaining seams found in the
thorough audit:

- API error paths: unknown preview ids, expired previews, missing stash
  files, empty-stash export, render-param validation edges (name too long,
  bad format, start >= end, pitch/BPM out of range), the 600s final-chop cap,
  stem audio/preview/chop for a track that was never separated, invalid
  peaks buckets, analysis GET for an unknown track.
- store.cleanup_previews purge behaviour + preview_file_path unknown ids.
- Worker internals: enqueue idempotency (already-done, invalid id), the
  is_current skip, _process_one raising on unknown tracks, the in-memory
  error status the _run loop records, stems dedup + already-complete.
- Migration: fresh install, re-init idempotency, legacy sample_stash
  without the stem column.
"""

from tests.lib2_seed import file_track
import json
import os
import time

import numpy as np
import pytest
import soundfile as sf
from flask import Blueprint, Flask

from core.sample import store
from core.sample import stems_worker
from core.sample import worker
from core.sample.analyze import ANALYZER_VERSION
from core.sample.stems import StubSeparator, separate_track

SR = 22050


def _click_track(path, seconds=4.0):
    n = int(seconds * SR)
    y = np.zeros(n, dtype=np.float32)
    t = np.arange(n) / SR
    y += 0.05 * np.sin(2 * np.pi * 55 * t).astype(np.float32)
    click = np.exp(-np.arange(int(0.02 * SR)) / (0.004 * SR)).astype(np.float32)
    b = 0.0
    while b < seconds - 0.05:
        i = int(b * SR)
        y[i : i + len(click)] += 0.9 * click
        b += 60.0 / 100.0
    y = (y / np.max(np.abs(y)) * 0.9).astype(np.float32)
    sf.write(str(path), y, SR, subtype="PCM_16")


@pytest.fixture
def client(tmp_path, monkeypatch):
    """Flask client over the REAL api/sample.py blueprint with a seeded track."""
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "music.db"))
    import database.music_database as mdb

    monkeypatch.setattr(mdb, "_database_instances", {})

    import api.sample as sample_api

    monkeypatch.setattr(sample_api, "require_api_key", lambda f: f)

    app = Flask(__name__)
    app.config["TESTING"] = True
    bp = Blueprint("sample_gaps", __name__)
    sample_api.register_routes(bp)
    app.register_blueprint(bp, url_prefix="/api/v1")

    wav = tmp_path / "gap.wav"
    _click_track(wav)

    db = mdb.get_database()
    conn = db._get_connection()
    try:
        conn.execute("INSERT INTO lib2_artists (id, name) VALUES (9001, 'Gap Artist')")
        conn.execute("INSERT INTO lib2_albums (id, primary_artist_id, title) VALUES (9001, 9001, 'Gap Album')")
        file_track(conn, 9001, 9001, 'Gap Track', str(wav),
        )
        conn.commit()
    finally:
        conn.close()

    with app.test_client() as c:
        yield c, db


@pytest.fixture
def db_only(tmp_path, monkeypatch):
    """Just the temp DB (no Flask app) for worker/store-level tests."""
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "music.db"))
    import database.music_database as mdb

    monkeypatch.setattr(mdb, "_database_instances", {})
    yield mdb.get_database()


def _chop(client, **over):
    body = {"track_id": 9001, "start_s": 0, "end_s": 2}
    body.update(over)
    return client.post("/api/v1/sample/chop", json=body)


# ── API error paths ────────────────────────────────────────────────────


def test_analysis_get_unknown_track_is_404(client):
    c, _db = client
    r = c.get("/api/v1/sample/analysis", query_string={"track_id": 987654})
    assert r.status_code == 404


def test_peaks_invalid_buckets_is_400(client):
    c, _db = client
    r = c.get("/api/v1/sample/peaks", query_string={"track_id": 9001, "buckets": 4})
    assert r.status_code == 400


def test_preview_audio_unknown_id_is_404(client):
    c, _db = client
    r = c.get("/api/v1/sample/preview/does-not-exist")
    assert r.status_code == 404


def test_preview_expired_file_is_404_and_purged(client):
    """A preview whose .wav was deleted reads as expired (404), not a 500."""
    c, _db = client
    r = c.post(
        "/api/v1/sample/preview",
        json={"track_id": 9001, "start_s": 0, "end_s": 1},
    )
    assert r.status_code == 200, r.get_json()
    preview_id = r.get_json()["data"]["preview_id"]
    os.remove(os.path.join(store.previews_dir(), preview_id))

    r = c.get(f"/api/v1/sample/preview/{preview_id}")
    assert r.status_code == 404


def test_stash_audio_missing_file_is_404(client):
    c, _db = client
    r = _chop(client=c, name="orphan")
    entry_id = r.get_json()["data"]["id"]
    os.remove(r.get_json()["data"]["file_path"])

    r = c.get(f"/api/v1/sample/stash/{entry_id}/audio")
    assert r.status_code == 404


def test_stash_delete_unknown_id_is_404(client):
    c, _db = client
    r = c.delete("/api/v1/sample/stash/424242")
    assert r.status_code == 404


def test_stash_export_empty_is_400(client):
    c, _db = client
    r = c.get("/api/v1/sample/stash/export")
    assert r.status_code == 400


def test_chop_name_too_long_is_400(client):
    c, _db = client
    r = _chop(client=c, name="x" * 121)
    assert r.status_code == 400


def test_chop_bad_format_is_400(client):
    c, _db = client
    r = _chop(client=c, name="bad fmt", format="mp3")
    assert r.status_code == 400


def test_chop_start_after_end_is_400(client):
    c, _db = client
    r = _chop(client=c, name="backwards", start_s=2, end_s=1)
    assert r.status_code == 400


def test_chop_pitch_out_of_range_is_400(client):
    c, _db = client
    r = _chop(client=c, name="too bendy", pitch_st=25)
    assert r.status_code == 400


def test_chop_target_bpm_out_of_range_is_400(client):
    c, _db = client
    # Track needs an analysis row for target_bpm to be accepted at all.
    store.save_analysis(
        9001, {"bpm": 100.0, "onsets": [0.5], "duration_s": 4.0, "analyzer_version": ANALYZER_VERSION}
    )
    r = _chop(client=c, name="too slow", target_bpm=10)
    assert r.status_code == 400


def test_chop_over_600s_is_400(client, tmp_path):
    """The 600s cap applies to the real slice: use a long track so 0..601
    is really 601s. (render.py clamps end_s to the track length first, so a
    short track can never trip the cap.)"""
    c, db = client
    long_wav = str(tmp_path / "verylong.wav")
    _click_track(long_wav, seconds=605.0)
    conn = db._get_connection()
    try:
        file_track(conn, 9003, 9001, 'Very Long', long_wav)
        conn.commit()
    finally:
        conn.close()
    r = c.post(
        "/api/v1/sample/chop",
        json={"track_id": 9003, "start_s": 0, "end_s": 601, "name": "ten minutes plus"},
    )
    assert r.status_code == 400, r.get_json()


def test_stem_audio_never_separated_is_404(client):
    c, _db = client
    r = c.get("/api/v1/sample/stems/9001/drums/audio")
    assert r.status_code == 404


def test_preview_from_missing_stem_is_409(client):
    c, _db = client
    r = c.post(
        "/api/v1/sample/preview",
        json={"track_id": 9001, "start_s": 0, "end_s": 1, "stem": "vocals"},
    )
    assert r.status_code == 409


def test_chop_from_missing_stem_is_409(client):
    c, _db = client
    r = _chop(client=c, name="stem chop", stem="vocals")
    assert r.status_code == 409


# ── store gaps ─────────────────────────────────────────────────────────


def test_cleanup_previews_purges_only_old_wavs(db_only):
    d = store.previews_dir()
    old = os.path.join(d, "oldpreview.wav")
    new = os.path.join(d, "newpreview.wav")
    other = os.path.join(d, "keepme.txt")
    for p, payload in ((old, b"a"), (new, b"b"), (other, b"c")):
        with open(p, "wb") as fh:
            fh.write(payload)
    ancient = time.time() - 7200
    os.utime(old, (ancient, ancient))

    removed = store.cleanup_previews(max_age_s=3600)

    assert removed == 1
    assert not os.path.exists(old)
    assert os.path.exists(new)
    assert os.path.exists(other)


def test_preview_file_path_rejects_sketchy_and_missing_ids(db_only):
    """api/sample.py preview_file_path: 404 on traversal-ish ids AND on
    well-formed ids whose file is gone (the short-lived cache expired)."""
    import api.sample as sample_api

    with pytest.raises(sample_api.SampleHttpError) as exc_info:
        sample_api.preview_file_path("../evil.wav")
    assert exc_info.value.status == 404

    with pytest.raises(sample_api.SampleHttpError) as exc_info:
        sample_api.preview_file_path("p9001_deadbeef.wav")
    assert exc_info.value.status == 404


# ── worker gaps ────────────────────────────────────────────────────────


def test_enqueue_analysis_already_done_returns_done(db_only):
    store.save_analysis(
        900101,
        {"bpm": 100.0, "onsets": [], "duration_s": 4.0, "analyzer_version": ANALYZER_VERSION},
    )
    assert worker.enqueue_analysis(900101) == "done"
    assert 900101 not in worker._pending


def test_enqueue_analysis_invalid_id_never_raises():
    # ids are text (jellyfin guids, navidrome ids); only one that could
    # escape a path is invalid
    assert worker.enqueue_analysis("../not-a-track").startswith("error:")
    assert worker.enqueue_analysis(None).startswith("error:")


def test_process_one_unknown_track_raises():
    with pytest.raises(RuntimeError, match="unknown track_id"):
        worker._process_one(900102)


def test_track_file_path_respects_active_library_scope(db_only, monkeypatch, tmp_path):
    """Sample Studio must resolve the file owned by the selected profile;
    primary-file ordering alone can return another library's copy."""
    db = db_only
    conn = db._get_connection()
    try:
        conn.execute("INSERT INTO lib2_artists (id, name) VALUES (9007, 'Scoped Artist')")
        conn.execute("INSERT INTO lib2_albums (id, primary_artist_id, title) VALUES (9007, 9007, 'Scoped Album')")
        conn.execute("INSERT INTO lib2_tracks (id, album_id, title) VALUES (9007, 9007, 'Scoped Track')")
        conn.execute(
            "INSERT INTO lib2_track_files (track_id, path, is_primary, owner_profile_id) VALUES (9007, ?, 1, 1)",
            (str(tmp_path / "shared.flac"),),
        )
        conn.execute(
            "INSERT INTO lib2_track_files (track_id, path, is_primary, owner_profile_id) VALUES (9007, ?, 1, 2)",
            (str(tmp_path / "own.flac"),),
        )
        conn.commit()
    finally:
        conn.close()

    monkeypatch.setattr(
        "core.library2.sql_util.owner_clause",
        lambda scope=None, column="owned_f.owner_profile_id":
            " AND +f.owner_profile_id = 2" if column == "f.owner_profile_id" else "",
    )
    assert store.get_track_file_path(9007) == str(tmp_path / "own.flac")


def test_process_one_skips_current_analysis(db_only, monkeypatch):
    store.save_analysis(
        900103,
        {"bpm": 100.0, "onsets": [], "duration_s": 4.0, "analyzer_version": ANALYZER_VERSION},
    )

    def _boom(_path):
        raise AssertionError("analyze_track must not run for a current row")

    monkeypatch.setattr("core.sample.analyze.analyze_track", _boom)
    worker._process_one(900103)  # returns quietly, no re-analysis


def test_worker_records_error_status_for_unreachable_track():
    """The _run loop catches _process_one failures into _status (in memory)."""
    track_id = 900104
    assert worker.enqueue_analysis(track_id) == "pending"
    deadline = time.time() + 10
    while time.time() < deadline:
        status = worker.get_status(track_id)
        if status.startswith("error:"):
            break
        time.sleep(0.05)
    else:
        raise AssertionError("worker never recorded the error status")
    assert "unknown track_id" in status


def test_stems_enqueue_dedupes_while_pending():
    """A second enqueue while the first is queued/running must not start a
    second separation — it returns the current in-flight status."""
    track_id = 900105
    first = stems_worker.enqueue_separation(track_id, "stub")
    assert first == "queued"
    second = stems_worker.enqueue_separation(track_id, "stub")
    # The daemon may have picked the item up between the two calls; either
    # way the second call dedupes onto the in-flight status, never a new queue.
    assert second in ("queued", "running")


def test_stems_enqueue_invalid_id_never_raises():
    assert stems_worker.enqueue_separation("no/pe").startswith("error:")


def test_stems_process_one_unknown_track_raises():
    with pytest.raises(RuntimeError, match="unknown track_id"):
        stems_worker._process_one(900106, "stub")


def test_stems_enqueue_already_complete_returns_done(db_only, tmp_path):
    wav = tmp_path / "stemsrc.wav"
    _click_track(wav)
    db = db_only
    conn = db._get_connection()
    try:
        conn.execute("INSERT INTO lib2_artists (id, name) VALUES (9002, 'Stem Artist')")
        conn.execute("INSERT INTO lib2_albums (id, primary_artist_id, title) VALUES (9002, 9002, 'Stem Album')")
        file_track(conn, 9002, 9002, 'Stem Track', str(wav),
        )
        conn.commit()
    finally:
        conn.close()

    separate_track(9002, backend=StubSeparator())
    assert store.stems_complete(9002)
    assert stems_worker.enqueue_separation(9002, "stub") == "done"


# ── migration gaps ─────────────────────────────────────────────────────


def test_migration_creates_tables_and_ledger(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "mig.db"))
    import database.music_database as mdb

    monkeypatch.setattr(mdb, "_database_instances", {})
    db = mdb.get_database()
    conn = db._get_connection()
    try:
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert {"sample_analysis", "sample_stash", "sample_stems"} <= tables
        ledger = {
            r[0]
            for r in conn.execute(
                "SELECT name FROM schema_migrations WHERE name IN "
                "('sample_analysis_v1', 'sample_stash_v1', 'sample_stems_v1')"
            )
        }
        assert ledger == {"sample_analysis_v1", "sample_stash_v1", "sample_stems_v1"}
    finally:
        conn.close()


def test_migration_reinit_is_idempotent(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "mig2.db"))
    import database.music_database as mdb

    monkeypatch.setattr(mdb, "_database_instances", {})
    mdb.get_database()  # first boot creates everything
    monkeypatch.setattr(mdb, "_database_instances", {})
    db = mdb.get_database()  # second boot must not raise or duplicate
    conn = db._get_connection()
    try:
        count = conn.execute(
            "SELECT COUNT(*) FROM schema_migrations WHERE name = 'sample_stems_v1'"
        ).fetchone()[0]
        assert count == 1
        cols = {r[1] for r in conn.execute("PRAGMA table_info(sample_stash)")}
        assert "stem" in cols
    finally:
        conn.close()


def test_migration_adds_stem_column_to_legacy_stash(tmp_path, monkeypatch):
    """Upgrades from before Phase 4: sample_stash exists without the stem column."""
    import sqlite3

    db_path = str(tmp_path / "legacy.db")
    conn = sqlite3.connect(db_path)
    conn.execute(
        """CREATE TABLE sample_stash (
               id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL,
               file_path TEXT NOT NULL)"""
    )
    conn.commit()
    conn.close()

    monkeypatch.setenv("DATABASE_PATH", db_path)
    import database.music_database as mdb

    monkeypatch.setattr(mdb, "_database_instances", {})
    db = mdb.get_database()
    conn = db._get_connection()
    try:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(sample_stash)")}
        assert "stem" in cols
    finally:
        conn.close()


def test_peaks_path_rejects_ids_that_could_escape_the_folder():
    with pytest.raises((TypeError, ValueError)):
        store.peaks_path("../abc")
    assert store.peaks_path("5f1c0a3e9b7d").endswith(".json")   # a jellyfin id is fine
