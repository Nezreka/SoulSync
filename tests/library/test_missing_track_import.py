"""Tests for the Enhanced Library "I Have This" import service."""

from __future__ import annotations

import os
import shutil
import sqlite3
from dataclasses import dataclass

import pytest

from core.library import missing_track_import as mti


class _ConnCtx:
    def __init__(self, conn):
        self.conn = conn

    def __enter__(self):
        return self.conn

    def __exit__(self, exc_type, exc, tb):
        return False


class _FakeDB:
    def __init__(self, conn):
        self.conn = conn

    def _get_connection(self):
        return _ConnCtx(self.conn)


@dataclass
class _FakeConfig:
    download_path: str
    active_server: str = "navidrome"

    def get(self, key, default=None):
        if key == "soulseek.download_path":
            return self.download_path
        return default

    def get_active_media_server(self):
        return self.active_server


def _make_db(*, include_disc_number: bool = True) -> tuple[_FakeDB, sqlite3.Connection]:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute(
        """
        CREATE TABLE artists (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE albums (
            id TEXT PRIMARY KEY,
            artist_id TEXT NOT NULL,
            title TEXT NOT NULL,
            year INTEGER,
            track_count INTEGER,
            server_source TEXT,
            deezer_id TEXT,
            thumb_url TEXT
        )
        """
    )
    disc_col = ", disc_number INTEGER DEFAULT 1" if include_disc_number else ""
    cur.execute(
        f"""
        CREATE TABLE tracks (
            id TEXT PRIMARY KEY,
            album_id TEXT NOT NULL,
            artist_id TEXT NOT NULL,
            title TEXT NOT NULL,
            track_number INTEGER{disc_col},
            duration INTEGER,
            file_path TEXT,
            bitrate INTEGER,
            file_size INTEGER,
            server_source TEXT,
            deezer_id TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    cur.execute("INSERT INTO artists (id, name) VALUES ('artist-1', 'Kendrick Lamar')")
    cur.execute(
        """
        INSERT INTO albums (id, artist_id, title, year, track_count, server_source, deezer_id)
        VALUES ('album-basic', 'artist-1', 'DAMN.', 2017, 14, 'navidrome', '302127')
        """
    )
    cur.execute(
        """
        INSERT INTO albums (id, artist_id, title, year, track_count, server_source, deezer_id)
        VALUES ('album-deluxe', 'artist-1', 'DAMN. COLLECTORS EDITION', 2017, 14, 'navidrome', '999999')
        """
    )
    conn.commit()
    return _FakeDB(conn), conn


def _insert_track(conn, *, track_id, album_id, title, track_number, file_path, disc_number=1):
    columns = [row[1] for row in conn.execute("PRAGMA table_info(tracks)").fetchall()]
    if "disc_number" in columns:
        conn.execute(
            """
            INSERT INTO tracks (id, album_id, artist_id, title, track_number, disc_number, duration, file_path, bitrate, file_size, server_source)
            VALUES (?, ?, 'artist-1', ?, ?, ?, 177000, ?, 900, 1234, 'navidrome')
            """,
            (track_id, album_id, title, track_number, disc_number, str(file_path)),
        )
    else:
        conn.execute(
            """
            INSERT INTO tracks (id, album_id, artist_id, title, track_number, duration, file_path, bitrate, file_size, server_source)
            VALUES (?, ?, 'artist-1', ?, ?, 177000, ?, 900, 1234, 'navidrome')
            """,
            (track_id, album_id, title, track_number, str(file_path)),
        )
    conn.commit()


def _deps(tmp_path, db, *, post_process_fn=None, sync_calls=None):
    sync_calls = sync_calls if sync_calls is not None else []

    def _default_post_process(_key, context, staged_path):
        final_dir = tmp_path / "Library" / "Kendrick Lamar - 2017 DAMN"
        final_dir.mkdir(parents=True, exist_ok=True)
        final_path = final_dir / "08 - HUMBLE [FLAC 16bit].flac"
        shutil.copy2(staged_path, final_path)
        context["_final_processed_path"] = str(final_path)

    return mti.MissingTrackImportDeps(
        database=db,
        config_manager=_FakeConfig(str(tmp_path / "downloads")),
        post_process_fn=post_process_fn or _default_post_process,
        resolve_library_file_path_fn=lambda path: str(path) if path and os.path.exists(path) else None,
        docker_resolve_path_fn=lambda path: path,
        sync_tracks_to_server_fn=lambda rows, server: sync_calls.append((rows, server)),
        service_id_columns={"deezer": {"track": "deezer_id"}},
    )


def _payload():
    return {
        "source_track_id": "deluxe-humble",
        "album_source_id": "302127",
        "total_discs": 1,
        "expected_track": {
            "title": "HUMBLE.",
            "track_number": 8,
            "disc_number": 1,
            "duration": 177000,
            "source": "deezer",
            "track_id": "350171311",
            "deezer_id": "350171311",
            "artists": ["Kendrick Lamar"],
        },
    }


def test_import_existing_track_copies_file_and_writes_target_album_row(tmp_path, monkeypatch):
    db, conn = _make_db(include_disc_number=True)
    source_file = tmp_path / "deluxe" / "08 - HUMBLE.flac"
    source_file.parent.mkdir()
    source_file.write_bytes(b"source audio")
    sibling_file = tmp_path / "basic" / "01 - BLOOD.flac"
    sibling_file.parent.mkdir()
    sibling_file.write_bytes(b"sibling audio")
    _insert_track(conn, track_id="basic-blood", album_id="album-basic", title="BLOOD.", track_number=1, file_path=sibling_file)
    _insert_track(conn, track_id="deluxe-humble", album_id="album-deluxe", title="HUMBLE.", track_number=8, file_path=source_file)

    inherited = []
    monkeypatch.setattr(mti, "read_album_identity_tags", lambda path: {"musicbrainz_albumid": "target-release"} if path == str(sibling_file) else {})
    monkeypatch.setattr(mti, "write_album_identity_tags", lambda path, tags: inherited.append((path, tags)) or True)

    sync_calls = []
    result = mti.import_existing_track_for_album_slot("album-basic", _payload(), _deps(tmp_path, db, sync_calls=sync_calls))

    assert source_file.read_bytes() == b"source audio"
    assert os.path.exists(result["final_path"])
    assert inherited == [(result["final_path"], {"musicbrainz_albumid": "target-release"})]

    row = conn.execute("SELECT * FROM tracks WHERE album_id = 'album-basic' AND track_number = 8").fetchone()
    assert row is not None
    assert row["title"] == "HUMBLE."
    assert row["disc_number"] == 1
    assert row["file_path"] == result["final_path"]
    assert row["deezer_id"] == "350171311"
    assert sync_calls and sync_calls[0][1] == "navidrome"


def test_import_adds_disc_number_column_for_older_track_tables(tmp_path, monkeypatch):
    db, conn = _make_db(include_disc_number=False)
    source_file = tmp_path / "deluxe" / "08 - HUMBLE.flac"
    source_file.parent.mkdir()
    source_file.write_bytes(b"source audio")
    sibling_file = tmp_path / "basic" / "01 - BLOOD.flac"
    sibling_file.parent.mkdir()
    sibling_file.write_bytes(b"sibling audio")
    _insert_track(conn, track_id="basic-blood", album_id="album-basic", title="BLOOD.", track_number=1, file_path=sibling_file)
    _insert_track(conn, track_id="deluxe-humble", album_id="album-deluxe", title="HUMBLE.", track_number=8, file_path=source_file)

    write_calls = []
    monkeypatch.setattr(mti, "read_album_identity_tags", lambda path: {"musicbrainz_albumid": "target-release"} if path == str(sibling_file) else {})
    monkeypatch.setattr(mti, "write_album_identity_tags", lambda path, tags: write_calls.append((path, tags)) or True)

    result = mti.import_existing_track_for_album_slot("album-basic", _payload(), _deps(tmp_path, db))

    columns = [row[1] for row in conn.execute("PRAGMA table_info(tracks)").fetchall()]
    assert "disc_number" in columns
    row = conn.execute("SELECT title, disc_number, file_path FROM tracks WHERE album_id = 'album-basic' AND track_number = 8").fetchone()
    assert row["title"] == "HUMBLE."
    assert row["disc_number"] == 1
    assert row["file_path"] == result["final_path"]
    assert write_calls, "album identity inheritance should still run after old DB migration"


def test_copy_album_identity_uses_target_sibling_and_leaves_track_tags_to_imported_file(tmp_path, monkeypatch):
    db, conn = _make_db(include_disc_number=True)
    sibling_file = tmp_path / "basic" / "01 - BLOOD.flac"
    sibling_file.parent.mkdir()
    sibling_file.write_bytes(b"sibling")
    final_file = tmp_path / "basic" / "08 - HUMBLE.flac"
    final_file.write_bytes(b"imported")
    _insert_track(conn, track_id="basic-blood", album_id="album-basic", title="BLOOD.", track_number=1, file_path=sibling_file)

    monkeypatch.setattr(mti, "read_album_identity_tags", lambda path: {"musicbrainz_albumid": "target-release", "barcode": "target-barcode"})
    writes = []
    monkeypatch.setattr(mti, "write_album_identity_tags", lambda path, tags: writes.append((path, tags)) or True)

    copied = mti.copy_album_identity_from_target_sibling(
        db,
        "album-basic",
        str(final_file),
        1,
        8,
        lambda path: str(path) if os.path.exists(path) else None,
    )

    assert copied is True
    assert writes == [(str(final_file), {"musicbrainz_albumid": "target-release", "barcode": "target-barcode"})]


def test_import_rejects_missing_expected_track_context(tmp_path):
    db, _conn = _make_db(include_disc_number=True)
    with pytest.raises(mti.MissingTrackImportError) as exc:
        mti.import_existing_track_for_album_slot("album-basic", {"source_track_id": "x", "expected_track": {}}, _deps(tmp_path, db))

    assert exc.value.status_code == 400
    assert "expected_track" in str(exc.value)
