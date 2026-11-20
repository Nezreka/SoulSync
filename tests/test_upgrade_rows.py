"""Quality upgrades say what they replace: "MP3 320 → FLAC" on the Downloads
row, an Upgrades filter, and "upgrade queued" on the artist page."""

from __future__ import annotations

import pytest

from core.downloads import upgrade_rows as ur
from core.imports.upgrade_intent import CONTEXT_KEY, issue_upgrade_intent
from core.runtime_state import download_tasks, tasks_lock


@pytest.mark.parametrize("fmt,bitrate,depth,label", [
    ("mp3", 320, None, "MP3 320"),
    ("mp3", 320000, None, "MP3 320"),          # a media server's bps
    ("aac", 256, None, "AAC 256"),
    ("flac", 1411, 16, "FLAC"),
    ("flac", None, 24, "FLAC 24-bit"),
    ("mp3", None, None, "MP3"),
    ("unknown", 128, None, ""),
    ("", None, None, ""),
])
def test_short_label(fmt, bitrate, depth, label):
    assert ur.short_label(fmt, bitrate, depth) == label


def test_upgrade_of_reads_the_file_row():
    got = ur.upgrade_of_file({"format": "MP3", "bitrate": 320000, "bit_depth": None,
                              "sample_rate": 44100})
    assert got == {"format": "mp3", "bitrate": 320, "bit_depth": None,
                   "sample_rate": 44100, "label": "MP3 320"}
    assert ur.upgrade_of_file(None) is None
    assert ur.upgrade_of_file({"format": None}) is None


class _Db:
    def __init__(self, rows):
        self.rows = rows

    def _get_connection(self):
        rows = self.rows

        class _C:
            def close(self):
                pass
        conn = _C()
        conn.rows = rows
        return conn


@pytest.fixture
def tasks():
    ids = []

    def add(task_id, task):
        with tasks_lock:
            download_tasks[task_id] = task
        ids.append(task_id)
        return task
    yield add
    with tasks_lock:
        for task_id in ids:
            download_tasks.pop(task_id, None)


def test_stamp_tasks_gives_each_upgrade_its_file(tasks, monkeypatch):
    monkeypatch.setattr("core.library2.track_files.primary_file_rows",
                        lambda conn, ids: {7: {"format": "mp3", "bitrate": 320}})
    task = tasks("t-up", {"status": "pending"})
    other = tasks("t-up2", {"status": "pending"})
    assert ur.stamp_tasks(_Db({}), [("t-up", 7), ("t-up2", 8)]) == 1
    assert task["upgrade_of"]["label"] == "MP3 320"
    assert task["upgrade_of"]["lib2_track_id"] == 7
    assert "upgrade_of" not in other           # no file, nothing claimed


def test_queued_track_ids_are_the_live_upgrades(tasks):
    tasks("a", {"status": "downloading", CONTEXT_KEY: issue_upgrade_intent(11, origin="wishlist")})
    tasks("b", {"status": "searching", "upgrade_of": {"lib2_track_id": 12, "label": "MP3"}})
    tasks("c", {"status": "completed", CONTEXT_KEY: issue_upgrade_intent(13, origin="wishlist")})
    tasks("d", {"status": "queued"})
    found = ur.queued_upgrade_track_ids()
    assert {11, 12} <= found and 13 not in found


def test_the_artist_badges_say_upgrade_queued(tasks, monkeypatch):
    from core.quality import upgrades
    tasks("a", {"status": "downloading", CONTEXT_KEY: issue_upgrade_intent(21, origin="wishlist")})
    payload = {"albums": [{"tracks": [{"id": 21}, {"id": 22}]}]}
    monkeypatch.setattr(upgrades, "_enhanced_upgrades", lambda db, albums: {
        id(albums[0]["tracks"][0]): {"current": "MP3 320"},
        id(albums[0]["tracks"][1]): {"current": "MP3 128"}})
    upgrades.annotate_enhanced_payload(object(), payload)
    album = payload["albums"][0]
    assert album["tracks"][0]["quality_upgrade"] == {"current": "MP3 320", "queued": True}
    assert "queued" not in album["tracks"][1]["quality_upgrade"]
    assert album["upgradable_count"] == 2 and album["upgrade_queued_count"] == 1


def test_status_rows_carry_upgrade_of(tasks):
    from core.downloads import status
    assert status._row_extras({"upgrade_of": {"label": "MP3 320"}}) == {
        "upgrade_of": {"label": "MP3 320"}}
    assert status._row_extras({}) == {"upgrade_of": None}


def test_stamp_reads_the_real_primary_file(tmp_path, tasks):
    from contextlib import closing

    from database.music_database import MusicDatabase
    from tests import lib2_seed
    db = MusicDatabase(str(tmp_path / "m.db"))
    with closing(db._get_connection()) as conn:
        low = lib2_seed.track(conn, "Artist", "Album", "Low", path="/m/low.mp3")
        conn.execute("UPDATE lib2_track_files SET format='mp3', bitrate=320 WHERE path='/m/low.mp3'")
        conn.commit()
    task = tasks("t-real", {"status": "pending"})
    assert ur.stamp_tasks(db, [("t-real", low)]) == 1
    assert task["upgrade_of"]["label"] == "MP3 320"
