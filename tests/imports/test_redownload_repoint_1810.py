"""#1810: a redownload that lands at a new filename keeps ONE library row.

the import writer used to insert a fresh row for the new path, then the
redownload hook repointed the original at the same file, leaving two rows.
the redownload task now names the row it replaces and the writer repoints
that row instead.

Library v2 port: the row is a catalogue track, the file a ``lib2_track_files``
row. The standalone writer files the new file on the named track; the
autolink gets the same track as ``lib2_entity``.
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


def _seed(conn, path, title="Song One (2009 Remaster)", album="Old Compilation"):
    """the track being replaced. its own title and album, so nothing but the
    redownload id can tie the new file to it."""
    artist = conn.execute(
        "INSERT INTO lib2_artists (name, name_key) VALUES ('Artist One', 'artist one')").lastrowid
    album_id = conn.execute(
        "INSERT INTO lib2_albums (primary_artist_id, title, origin) VALUES (?, ?, 'library')",
        (artist, album)).lastrowid
    track = conn.execute(
        "INSERT INTO lib2_tracks (album_id, title, track_number, server_source, server_id)"
        " VALUES (?, ?, 1, 'soulsync', 'orig-hash')", (album_id, title)).lastrowid
    conn.execute(
        "INSERT INTO lib2_track_files (track_id, path, size, file_state, is_primary)"
        " VALUES (?, ?, 1, 'active', 1)", (track, path))
    conn.commit()
    return track


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


def _files(conn):
    return [dict(r) for r in conn.execute(
        "SELECT track_id, path, size FROM lib2_track_files"
        " WHERE COALESCE(file_state, 'active') = 'active' ORDER BY id")]


def test_redownload_to_new_filename_lands_on_the_original_track(tmp_path, monkeypatch):
    conn = _make_soulsync_db()
    _wire(monkeypatch, conn)
    original = _seed(conn, str(tmp_path / "01 - song one.mp3"))
    new_file = tmp_path / "01 - Song One.flac"
    new_file.write_bytes(b"new audio bytes")

    _write(_context(new_file, redownload_id=original))

    assert [r["id"] for r in conn.execute("SELECT id FROM lib2_tracks")] == [original]
    new_row = next(f for f in _files(conn) if f["path"] == str(new_file))
    assert new_row["track_id"] == original
    assert new_row["size"] == len(b"new audio bytes")


def test_same_path_redownload_keeps_one_track(tmp_path, monkeypatch):
    conn = _make_soulsync_db()
    _wire(monkeypatch, conn)
    new_file = tmp_path / "01 - Song One.flac"
    new_file.write_bytes(b"replacement")
    original = _seed(conn, str(new_file))

    _write(_context(new_file, redownload_id=original))

    assert [r["id"] for r in conn.execute("SELECT id FROM lib2_tracks")] == [original]
    assert [f["track_id"] for f in _files(conn)] == [original]


def test_plain_import_still_gets_its_own_track(tmp_path, monkeypatch):
    conn = _make_soulsync_db()
    _wire(monkeypatch, conn)
    original = _seed(conn, str(tmp_path / "other.mp3"))
    new_file = tmp_path / "01 - Song One.flac"
    new_file.write_bytes(b"audio")

    _write(_context(new_file))

    tracks = [r["id"] for r in conn.execute("SELECT id FROM lib2_tracks ORDER BY id")]
    assert len(tracks) == 2 and original in tracks
    assert next(f for f in _files(conn) if f["path"] == str(new_file))["track_id"] != original


def test_a_redownload_id_naming_no_track_imports_normally(tmp_path, monkeypatch):
    conn = _make_soulsync_db()
    _wire(monkeypatch, conn)
    new_file = tmp_path / "01 - Song One.flac"
    new_file.write_bytes(b"audio")

    _write(_context(new_file, redownload_id=9999))

    assert conn.execute("SELECT COUNT(*) FROM lib2_tracks").fetchone()[0] == 1
    assert [f["path"] for f in _files(conn)] == [str(new_file)]


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
    # the autolink files the replacement on that exact catalogue track
    assert task["track_info"]["lib2_entity"] == {"track_id": 123}
