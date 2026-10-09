"""Regression tests for the Sep 2026 Sample Studio deep-review fixes.

Symptom 1 (duration ms->s) is a frontend mapper — covered in vitest
(-sample-studio.api.test.ts). Symptom 4 (row layout) is CSS.

Covered here:
- Symptom 2: analysis worker errors are sticky so the 2.5s status poll can
  observe them (previously every poll re-enqueued and reset "pending", so the
  UI spun on "Analyzing…" forever); ?retry=1 clears a recorded error.
- The worker thread lazy-starts on enqueue (all server boot paths).
- A hung ffmpeg decode surfaces as a worker error instead of wedging the
  single FIFO analysis thread.
- Symptom 3: free-text search tries the artist interpretation first (indexed
  lookup) and skips the title cascade's full-table fuzzy scan when the page
  fills; results are merged/deduped.
"""

from tests.lib2_seed import file_track
import queue
import subprocess
import time

import pytest

import api.sample as sample_api
from core.sample import worker as sample_worker


@pytest.fixture(autouse=True)
def _clean_worker_state():
    """Reset the module-global worker between tests (shared daemon thread)."""
    with sample_worker._lock:
        sample_worker._status.clear()
        sample_worker._pending.clear()
    while True:
        try:
            sample_worker._task_queue.get_nowait()
        except queue.Empty:
            break
    yield
    with sample_worker._lock:
        sample_worker._status.clear()
        sample_worker._pending.clear()
    while True:
        try:
            sample_worker._task_queue.get_nowait()
        except queue.Empty:
            break


@pytest.fixture
def db_only(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "music.db"))
    import database.music_database as mdb

    monkeypatch.setattr(mdb, "_database_instances", {})
    yield mdb.get_database()


def _seed_track(db, track_id, file_path):
    conn = db._get_connection()
    try:
        conn.execute("INSERT OR IGNORE INTO lib2_artists (id, name) VALUES (1, 'R')")
        conn.execute("INSERT OR IGNORE INTO lib2_albums (id, primary_artist_id, title) VALUES (1, 1, 'A')")
        file_track(conn, track_id, 1, 'T', file_path)
        conn.commit()
    finally:
        conn.close()


def _wait_for_status(track_id, prefix, timeout=10):
    deadline = time.time() + timeout
    while time.time() < deadline:
        status = sample_worker.get_status(track_id)
        if status.startswith(prefix):
            return status
        time.sleep(0.05)
    raise AssertionError(f"timed out waiting for status {prefix!r}; got {sample_worker.get_status(track_id)!r}")


# ── Symptom 2: sticky analysis errors ────────────────────────────────────


def test_poll_observes_worker_error(db_only):
    """The exact Symptom 2 repro: after the worker fails, the next status poll
    must surface the error instead of re-enqueueing into 'pending'."""
    _seed_track(db_only, 4242, "/nonexistent/x.flac")

    payload, status = sample_api.fetch_analysis(4242)
    assert status == 202 and payload["status"] == "pending"

    _wait_for_status(4242, "error:")

    # The 2.5s poll the frontend performs: error must be sticky, not reset.
    payload2, status2 = sample_api.fetch_analysis(4242)
    assert payload2["status"].startswith("error:"), f"error was clobbered by re-enqueue: {payload2['status']!r}"
    assert status2 == 202


def test_retry_clears_recorded_error(db_only):
    """?retry=1 re-queues a failed track instead of returning the stale error
    or silently deduping onto it."""
    _seed_track(db_only, 4243, "/nonexistent/y.flac")

    assert sample_api.fetch_analysis(4243)[0]["status"] == "pending"
    _wait_for_status(4243, "error:")

    # Without retry: sticky error, no re-queue.
    assert sample_api.fetch_analysis(4243)[0]["status"].startswith("error:")
    assert ('shared', 4243) not in sample_worker._pending

    # With retry: re-queued to pending, then fails again (it really re-ran).
    payload, _ = sample_api.fetch_analysis(4243, retry=True)
    assert payload["status"] == "pending"
    assert ('shared', 4243) in sample_worker._pending
    _wait_for_status(4243, "error:")


def test_enqueue_reports_sticky_error_as_422(db_only):
    """POST /sample/analyze after a worker failure must not return 200 with
    success:true — the track did NOT queue."""
    _seed_track(db_only, 4245, "/nonexistent/w.flac")

    payload, status = sample_api.enqueue_track_analysis(4245)
    assert status == 200 and payload["status"] == "pending"

    _wait_for_status(4245, "error:")

    payload, status = sample_api.enqueue_track_analysis(4245)
    assert status == 422
    assert payload["status"].startswith("error:")


def test_enqueue_starts_worker_thread(db_only):
    """The lazy worker daemon must be alive after enqueue in every boot path
    (it is NOT explicitly started by dev.py or web_server.py)."""
    _seed_track(db_only, 4244, "/nonexistent/z.flac")
    assert sample_worker.enqueue_analysis(4244) == "pending"
    assert sample_worker._thread is not None and sample_worker._thread.is_alive()


def test_ffmpeg_timeout_surfaces_as_error(monkeypatch):
    """A hung ffmpeg must not wedge the single FIFO analysis thread."""
    import core.sample.analyze as analyze_mod

    def _hang(*a, **k):
        raise subprocess.TimeoutExpired("ffmpeg", 180)

    # ci has no ffmpeg; this test is about the timeout, not the lookup
    monkeypatch.setattr(analyze_mod, "ffmpeg_bin", lambda: "ffmpeg")
    monkeypatch.setattr(analyze_mod.subprocess, "run", _hang)
    with pytest.raises(RuntimeError, match="timed out"):
        analyze_mod._decode_via_ffmpeg("/music/x.flac")


# ── Symptom 3: slow free-text search ──────────────────────────────────
# The old artist-first double cascade (api_search_tracks twice, each with
# basic + base-title + fuzzy passes) is gone: one normalized query hits
# MusicDatabase.search_tracks_interactive in a single statement.


@pytest.fixture
def fake_db(monkeypatch):
    calls = []

    class FakeDB:
        def search_tracks_interactive(self, query, limit=50):
            calls.append(("interactive", query, limit))
            return list(self.rows.pop(0))

        def api_search_tracks(self, title="", artist="", limit=50):
            calls.append(("dual", title, artist, limit))
            return list(self.rows.pop(0))

        rows = []

    db = FakeDB()
    monkeypatch.setattr(sample_api, "get_database", lambda: db)
    return db, calls


def _row(track_id, title="T", artist="A", track_artist=None):
    row = {"id": track_id, "title": title, "artist_name": artist, "duration": 206000}
    if track_artist is not None:
        row["track_artist"] = track_artist
    return row


def test_free_text_single_db_call_no_double_cascade(fake_db):
    """q is one normalized query: exactly one DB call, no artist-then-title
    double cascade (the old second cascade's full-table fuzzy scan is what
    made library search feel stuck)."""
    db, calls = fake_db
    db.rows = [[_row(i, artist="Virtual Mage") for i in range(50)]]
    tracks = sample_api.search_library_tracks(q="Virtual Mage")
    assert calls == [("interactive", "Virtual Mage", 50)], f"extra DB calls: {calls}"
    assert len(tracks) == 50
    assert all(t["id"] == i for i, t in enumerate(tracks))


def test_free_text_result_order_is_db_order(fake_db):
    """Rows come back in the DB's relevance order (exact title first, then
    prefixes, then artist hits) — no client-side re-merge."""
    db, calls = fake_db
    db.rows = [[_row(9, title="Track 0007-03"), _row(1), _row(2)]]
    tracks = sample_api.search_library_tracks(q="Track 0007-03", limit=50)
    assert calls == [("interactive", "Track 0007-03", 50)]
    assert [t["id"] for t in tracks] == [9, 1, 2]


def test_free_text_dedupes_and_caps_at_limit(fake_db):
    db, _ = fake_db
    db.rows = [[_row(1), _row(1), _row(2), _row(3)]]
    tracks = sample_api.search_library_tracks(q="x", limit=2)
    assert [t["id"] for t in tracks] == [1, 2]


def test_search_title_artist_params(fake_db):
    db, calls = fake_db
    db.rows = [[_row(9)]]
    tracks = sample_api.search_library_tracks(title="Windowlicker", artist="")
    assert calls == [("dual", "Windowlicker", "", 50)]
    assert [t["id"] for t in tracks] == [9]


def test_search_title_and_artist_keeps_dual_constraint(fake_db):
    db, calls = fake_db
    db.rows = [[_row(9)]]
    tracks = sample_api.search_library_tracks(title="Windowlicker", artist="Aphex Twin")
    assert calls == [("dual", "Windowlicker", "Aphex Twin", 50)]
    assert [t["id"] for t in tracks] == [9]


def test_search_requires_a_query():
    with pytest.raises(sample_api.SampleHttpError) as exc_info:
        sample_api.search_library_tracks()
    assert exc_info.value.status == 400
