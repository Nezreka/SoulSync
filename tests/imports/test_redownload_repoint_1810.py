"""#1810: a redownload that lands at a new filename keeps ONE library row.

the import writer used to insert a fresh row for the new path, then the
redownload hook repointed the original at the same file, leaving two rows.
the redownload task now names the row it replaces and the writer repoints
that row instead.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import Mock

from flask import Flask

from core.imports import side_effects
from core.library import redownload as rd
from tests.imports.test_import_side_effects import _FakeDB, _make_soulsync_db


def _wire(monkeypatch, conn):
    monkeypatch.setattr(side_effects, "get_database", lambda: _FakeDB(conn))
    monkeypatch.setattr(
        side_effects, "_get_config_manager",
        lambda: SimpleNamespace(get_active_media_server=lambda: "soulsync"),
    )
    import core.genre_filter as genre_filter
    monkeypatch.setattr(genre_filter, "filter_genres", lambda genres, _cfg: genres)
    import core.sample.worker as sample_worker
    monkeypatch.setattr(sample_worker, "enqueue_analysis", lambda *_a, **_k: "pending")


def _seed(conn, path, server_source="soulsync", track_id="orig-1"):
    conn.execute(
        "INSERT INTO tracks (id, title, file_path, bitrate, file_size, duration, "
        "server_source) VALUES (?, 'Song One', ?, 128, 1, 200000, ?)",
        (track_id, path, server_source),
    )
    conn.commit()


def _context(final_path, redownload_id=None):
    track_info = {
        "id": "sp-track", "name": "Song One", "track_number": 1,
        "duration_ms": 210000, "artists": [{"name": "Artist One"}],
        "_source": "spotify",
    }
    if redownload_id is not None:
        track_info["_redownload_library_track_id"] = redownload_id
    return {
        "source": "spotify",
        "artist": {"id": "sp-artist", "name": "Artist One"},
        "album": {"id": "sp-album", "name": "Album One", "total_tracks": 1},
        "track_info": track_info,
        "original_search_result": {"title": "Song One", "_source": "spotify"},
        "_final_processed_path": str(final_path),
    }


def _write(context):
    side_effects.record_soulsync_library_entry(
        context, {"name": "Artist One", "genres": []},
        {"is_album": True, "album_name": "Album One", "track_number": 1},
    )


def test_redownload_to_new_filename_repoints_the_original_row(tmp_path, monkeypatch):
    conn = _make_soulsync_db()
    _wire(monkeypatch, conn)
    _seed(conn, str(tmp_path / "01 - song one.mp3"))
    new_file = tmp_path / "01 - Song One.flac"
    new_file.write_bytes(b"new audio bytes")

    _write(_context(new_file, redownload_id="orig-1"))

    rows = conn.execute("SELECT * FROM tracks").fetchall()
    assert len(rows) == 1
    row = rows[0]
    assert row["id"] == "orig-1"
    assert row["file_path"] == str(new_file)
    assert row["file_size"] == len(b"new audio bytes")
    assert row["duration"] == 210000


def test_same_path_redownload_refreshes_file_facts(tmp_path, monkeypatch):
    conn = _make_soulsync_db()
    _wire(monkeypatch, conn)
    new_file = tmp_path / "01 - Song One.flac"
    new_file.write_bytes(b"replacement")
    _seed(conn, str(new_file))

    _write(_context(new_file, redownload_id="orig-1"))

    rows = conn.execute("SELECT * FROM tracks").fetchall()
    assert len(rows) == 1
    assert rows[0]["id"] == "orig-1"
    assert rows[0]["file_size"] == len(b"replacement")


def test_plain_import_still_inserts_its_own_row(tmp_path, monkeypatch):
    conn = _make_soulsync_db()
    _wire(monkeypatch, conn)
    _seed(conn, str(tmp_path / "other.mp3"))
    new_file = tmp_path / "01 - Song One.flac"
    new_file.write_bytes(b"audio")

    _write(_context(new_file))

    paths = sorted(r["file_path"] for r in conn.execute("SELECT file_path FROM tracks"))
    assert paths == sorted([str(tmp_path / "other.mp3"), str(new_file)])


def test_redownload_never_repoints_another_servers_row(tmp_path, monkeypatch):
    conn = _make_soulsync_db()
    _wire(monkeypatch, conn)
    old = str(tmp_path / "old.mp3")
    _seed(conn, old, server_source="plex")
    new_file = tmp_path / "01 - Song One.flac"
    new_file.write_bytes(b"audio")

    _write(_context(new_file, redownload_id="orig-1"))

    plex = conn.execute("SELECT file_path FROM tracks WHERE id = 'orig-1'").fetchone()
    assert plex["file_path"] == old
    assert conn.execute(
        "SELECT COUNT(*) FROM tracks WHERE file_path = ?", (str(new_file),)
    ).fetchone()[0] == 1


def test_redownload_task_names_the_row_it_replaces(monkeypatch):
    batches, tasks = {}, {}
    monkeypatch.setattr(rd, "download_batches", batches)
    monkeypatch.setattr(rd, "download_tasks", tasks)
    database = Mock()
    database._get_connection.return_value.cursor.return_value.fetchone.return_value = {
        "file_path": "/library/old.flac"}
    monkeypatch.setattr(rd, "get_database", lambda: database)
    monkeypatch.setattr(rd, "_resolve_library_file_path", lambda path: path)
    monkeypatch.setattr(rd, "download_monitor", Mock())
    monkeypatch.setattr(rd, "missing_download_executor", Mock())
    app = Flask(__name__)
    with app.test_request_context(json={"metadata": {"name": "Song", "artist": "Artist"},
                                       "candidate": {"username": "soulseek", "filename": "s.flac"}}):
        assert rd.redownload_start(123).get_json()["success"] is True
    (task,) = tasks.values()
    assert task["track_info"]["_redownload_library_track_id"] == 123
