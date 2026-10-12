"""Library backfill sweep: idempotent wanted-row creation for library videos.

Uses a real VideoDatabase (tmp_path), like the worker tests.
"""

from __future__ import annotations

import os

import pytest

from core.video.subtitles import worker as worker_mod
from database.video_database import VideoDatabase


@pytest.fixture()
def db(tmp_path):
    return VideoDatabase(database_path=str(tmp_path / "video_library.db"))


def _movie_with_file(db, tmp_path, name="Movie.2024.mkv", mid=1):
    p = tmp_path / name
    p.write_bytes(os.urandom(200_000))
    conn = db._get_connection()
    try:
        conn.execute("INSERT INTO movies (id, title) VALUES (?, 'M')", (mid,))
        conn.execute("INSERT INTO media_files (movie_id, relative_path) "
                     "VALUES (?, ?)", (mid, str(p)))
        conn.commit()
    finally:
        conn.close()
    return mid, str(p)


def _settings(**kw):
    s = {"download_subtitles": True, "subtitle_langs": "en,es",
         "subtitle_worker_batch": 10}
    s.update(kw)
    return s


def test_backfill_creates_rows_for_effective_langs(db, tmp_path):
    _movie_with_file(db, tmp_path)
    stats = worker_mod.ensure_wanted_rows_for_library(db, _settings())
    assert stats["videos_seen"] == 1
    assert stats["rows_created"] == 2  # en + es
    rows = db.subtitle_get_for_video("movie", 1)
    assert {(r["language"], r["user_set"]) for r in rows} == \
        {("en", 0), ("es", 0)}


def test_backfill_is_idempotent(db, tmp_path):
    _movie_with_file(db, tmp_path)
    worker_mod.ensure_wanted_rows_for_library(db, _settings())
    stats = worker_mod.ensure_wanted_rows_for_library(db, _settings())
    assert stats["rows_created"] == 0
    assert len(db.subtitle_get_for_video("movie", 1)) == 2


def test_backfill_respects_master_toggle(db, tmp_path):
    _movie_with_file(db, tmp_path)
    stats = worker_mod.ensure_wanted_rows_for_library(
        db, _settings(download_subtitles=False))
    assert stats["skipped"] == "disabled"
    assert db.subtitle_get_for_video("movie", 1) == []


def test_backfill_skips_have_rows(db, tmp_path):
    _movie_with_file(db, tmp_path)
    db.subtitle_want("movie", 1, "en")
    db.subtitle_mark("movie", 1, "en", "have", count_attempt=False)
    stats = worker_mod.ensure_wanted_rows_for_library(db, _settings())
    assert stats["rows_created"] == 1  # only es
    rows = {r["language"]: r["status"]
            for r in db.subtitle_get_for_video("movie", 1)}
    assert rows == {"en": "have", "es": "wanted"}


def test_backfill_marks_on_disk_sidecar_have(db, tmp_path):
    _mid, path = _movie_with_file(db, tmp_path)
    stem = os.path.splitext(os.path.basename(path))[0]
    open(os.path.join(os.path.dirname(path), stem + ".en.srt"),
         "w").write("1\n00:00:01,000 --> 00:00:02,000\nx\n")
    stats = worker_mod.ensure_wanted_rows_for_library(db, _settings())
    assert stats["marked_have"] == 1
    assert stats["rows_created"] == 1  # es still wanted
    rows = {r["language"]: r["status"]
            for r in db.subtitle_get_for_video("movie", 1)}
    assert rows["en"] == "have"


def test_backfill_throttled_by_batch(db, tmp_path):
    for i in range(1, 4):
        _movie_with_file(db, tmp_path, name="M%d.2024.mkv" % i, mid=i)
    stats = worker_mod.ensure_wanted_rows_for_library(
        db, _settings(subtitle_worker_batch=2))
    assert stats["videos_seen"] == 2
    assert stats["rows_created"] == 4


def test_backfill_skips_missing_files(db, tmp_path):
    conn = db._get_connection()
    try:
        conn.execute("INSERT INTO movies (id, title) VALUES (9, 'Gone')")
        conn.execute("INSERT INTO media_files (movie_id, relative_path) "
                     "VALUES (9, '/nope/gone.mkv')")
        conn.commit()
    finally:
        conn.close()
    stats = worker_mod.ensure_wanted_rows_for_library(db, _settings())
    assert stats["videos_seen"] == 1
    assert stats["rows_created"] == 0


def test_backfill_honors_per_title_override(db, tmp_path, monkeypatch):
    _movie_with_file(db, tmp_path)
    # _backfill_one_video does `from . import effective_subtitle_languages`
    # at call time, so patching the package attribute takes effect.
    import core.video.subtitles as subs_pkg
    monkeypatch.setattr(
        subs_pkg, "effective_subtitle_languages",
        lambda db_, kind, vid, settings: ["fr"])
    stats = worker_mod.ensure_wanted_rows_for_library(db, _settings())
    assert stats["rows_created"] == 1
    rows = db.subtitle_get_for_video("movie", 1)
    assert [r["language"] for r in rows] == ["fr"]


def test_start_backfill_refuses_concurrent():
    calls = []

    def _db_provider():
        calls.append(1)
        return None

    # Simulate a running sweep by seeding the state.
    worker_mod._backfill_state.update(state="running")
    try:
        ok, reason = worker_mod.start_backfill(_db_provider)
        assert ok is False
        assert "already running" in reason
    finally:
        worker_mod._backfill_state.update(state="idle")


def test_backfill_status_shape():
    st = worker_mod.backfill_status()
    assert set(st) >= {"state", "videos_seen", "rows_created",
                       "marked_have", "error"}
