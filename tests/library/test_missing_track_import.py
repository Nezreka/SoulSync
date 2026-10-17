"""Tests for the Enhanced Library "I Have This" import service."""

from __future__ import annotations

import os
import shutil
import json
import sqlite3

from core.library2.schema import ensure_library_v2_schema
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
    """The catalogue, with the two DAMN. editions the tests import into.

    ``include_disc_number`` is kept for the caller's shape but no longer means
    anything: v2 has a disc number on every track by construction (the column
    migration the legacy path had to run at import time is gone).
    """
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    ensure_library_v2_schema(conn)
    cur = conn.cursor()
    cur.execute("INSERT INTO lib2_artists (id, name, name_key, server_source, server_id)"
                " VALUES (1, 'Kendrick Lamar', 'kendrick lamar', 'navidrome', 'artist-1')")
    cur.execute(
        "INSERT INTO lib2_albums (id, primary_artist_id, title, year, track_count,"
        "                         origin, server_source, server_id, external_ids)"
        " VALUES (1, 1, 'DAMN.', 2017, 14, 'library', 'navidrome', 'album-basic',"
        "         '{\"deezer\": \"302127\"}')")
    cur.execute(
        "INSERT INTO lib2_albums (id, primary_artist_id, title, year, track_count,"
        "                         origin, server_source, server_id, external_ids)"
        " VALUES (2, 1, 'DAMN. COLLECTORS EDITION', 2017, 14, 'library', 'navidrome',"
        "         'album-deluxe', '{\"deezer\": \"999999\"}')")
    conn.commit()
    return _FakeDB(conn), conn


def _insert_track(conn, *, track_id, album_id, title, track_number, file_path, disc_number=1):
    track = conn.execute(
        """
        INSERT INTO lib2_tracks (album_id, title, track_number, disc_number, duration,
                                 server_source, server_id)
        VALUES (?, ?, ?, ?, 177000, 'navidrome', ?)
        """,
        (album_id, title, track_number, disc_number, str(track_id)),
    ).lastrowid
    conn.execute(
        "INSERT INTO lib2_track_artists(track_id, artist_id, role, position)"
        " VALUES(?, 1, 'primary', 0)", (track,))
    conn.execute(
        "INSERT INTO lib2_track_files(track_id, path, bitrate, size, is_primary)"
        " VALUES(?, ?, 900, 1234, 1)", (track, str(file_path)))
    conn.commit()
    return track


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


def _payload(source_track_id="deluxe-humble"):
    return {
        "source_track_id": source_track_id,
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
    _insert_track(conn, track_id="basic-blood", album_id=1, title="BLOOD.", track_number=1, file_path=sibling_file)
    source_track = _insert_track(conn, track_id="deluxe-humble", album_id=2,
                                 title="HUMBLE.", track_number=8, file_path=source_file)

    inherited = []
    monkeypatch.setattr(mti, "read_album_identity_tags", lambda path: {"musicbrainz_albumid": "target-release"} if path == str(sibling_file) else {})
    monkeypatch.setattr(mti, "write_album_identity_tags", lambda path, tags: inherited.append((path, tags)) or True)

    sync_calls = []
    result = mti.import_existing_track_for_album_slot(1, _payload(source_track), _deps(tmp_path, db, sync_calls=sync_calls))

    assert source_file.read_bytes() == b"source audio"
    assert os.path.exists(result["final_path"])
    assert inherited == [(result["final_path"], {"musicbrainz_albumid": "target-release"})]

    row = conn.execute(
        "SELECT t.*, f.path AS file_path, f.bitrate, f.size AS file_size"
        "  FROM lib2_tracks t LEFT JOIN lib2_track_files f"
        "         ON f.track_id = t.id AND f.is_primary = 1"
        " WHERE t.album_id = 1 AND t.track_number = 8").fetchone()
    assert row is not None
    assert row["title"] == "HUMBLE."
    assert row["disc_number"] == 1
    assert row["file_path"] == result["final_path"]
    # The provider id lands in the catalogue's `external_ids` — Deezer has no
    # promoted column, only Spotify and MusicBrainz do.
    assert json.loads(row["external_ids"])["deezer"] == "350171311"
    assert sync_calls and sync_calls[0][1] == "navidrome"


def test_import_writes_the_disc_number_the_slot_asks_for(tmp_path, monkeypatch):
    db, conn = _make_db(include_disc_number=False)
    source_file = tmp_path / "deluxe" / "08 - HUMBLE.flac"
    source_file.parent.mkdir()
    source_file.write_bytes(b"source audio")
    sibling_file = tmp_path / "basic" / "01 - BLOOD.flac"
    sibling_file.parent.mkdir()
    sibling_file.write_bytes(b"sibling audio")
    _insert_track(conn, track_id="basic-blood", album_id=1, title="BLOOD.", track_number=1, file_path=sibling_file)
    source_track = _insert_track(conn, track_id="deluxe-humble", album_id=2,
                                 title="HUMBLE.", track_number=8, file_path=source_file)

    write_calls = []
    monkeypatch.setattr(mti, "read_album_identity_tags", lambda path: {"musicbrainz_albumid": "target-release"} if path == str(sibling_file) else {})
    monkeypatch.setattr(mti, "write_album_identity_tags", lambda path, tags: write_calls.append((path, tags)) or True)

    result = mti.import_existing_track_for_album_slot(1, _payload(source_track), _deps(tmp_path, db))

    # The migration this test used to pin is gone with the legacy table: the
    # catalogue has a disc number on every track by construction.
    columns = [row[1] for row in conn.execute("PRAGMA table_info(lib2_tracks)").fetchall()]
    assert "disc_number" in columns
    row = conn.execute(
        "SELECT t.title, t.disc_number, f.path AS file_path"
        "  FROM lib2_tracks t LEFT JOIN lib2_track_files f"
        "         ON f.track_id = t.id AND f.is_primary = 1"
        " WHERE t.album_id = 1 AND t.track_number = 8").fetchone()
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
    _insert_track(conn, track_id="basic-blood", album_id=1, title="BLOOD.", track_number=1, file_path=sibling_file)

    monkeypatch.setattr(mti, "read_album_identity_tags", lambda path: {"musicbrainz_albumid": "target-release", "barcode": "target-barcode"})
    writes = []
    monkeypatch.setattr(mti, "write_album_identity_tags", lambda path, tags: writes.append((path, tags)) or True)

    copied = mti.copy_album_identity_from_target_sibling(
        db,
        1,
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
        mti.import_existing_track_for_album_slot(1, {"source_track_id": "x", "expected_track": {}}, _deps(tmp_path, db))

    assert exc.value.status_code == 400
    assert "expected_track" in str(exc.value)


# ── #917: recover album year from existing folder so "I have this" reuses the dir ──
def _year_db(rows):
    """Sibling tracks of album 1, with their files. The year lives on the
    ALBUM in v2, so a "year" in a row seeds the album's."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    ensure_library_v2_schema(conn)
    album_year = next((r.get("year") for r in rows if r.get("year")), None)
    conn.execute("INSERT INTO lib2_artists(id, name, name_key) VALUES(1,'A','a')")
    conn.execute(
        "INSERT INTO lib2_albums(id, primary_artist_id, title, year, origin)"
        " VALUES(1, 1, 'Album', ?, 'library')", (album_year,))
    for row in rows:
        track = conn.execute(
            "INSERT INTO lib2_tracks(album_id, title, track_number, disc_number)"
            " VALUES(1, 'T', ?, ?)",
            (row.get("track_number", 1), row.get("disc_number", 1))).lastrowid
        conn.execute(
            "INSERT INTO lib2_track_files(track_id, path, is_primary) VALUES(?,?,1)",
            (track, row.get("file_path")))
    conn.commit()
    return _FakeDB(conn)


def _ident(p):
    return p


def test_year_recovered_from_sibling_year_column():
    db = _year_db([{"track_number": 3, "file_path": "/m/Artist/Album/03.flac", "year": 2024}])
    assert mti._existing_album_year_from_sibling(db, 1, _ident, 1, 5) == "2024"


def test_year_recovered_from_paren_folder_name():
    db = _year_db([{"track_number": 3, "file_path": "/music/Artist/Album (2019)/03 - Song.flac", "year": None}])
    assert mti._existing_album_year_from_sibling(db, 1, _ident, 1, 5) == "2019"


def test_year_recovered_from_bracket_folder_name():
    db = _year_db([{"track_number": 3, "file_path": "/music/Artist/Album [2008]/03.flac", "year": None}])
    assert mti._existing_album_year_from_sibling(db, 1, _ident, 1, 5) == "2008"


def test_year_none_when_no_signal():
    db = _year_db([{"track_number": 3, "file_path": "/music/Artist/Album/03.flac", "year": None}])
    assert mti._existing_album_year_from_sibling(db, 1, _ident, 1, 5) is None


def test_year_ignores_the_target_slot_itself():
    # The only sibling row IS the slot being imported -> excluded -> no year.
    db = _year_db([{"disc_number": 1, "track_number": 5, "file_path": "/m/Album (2030)/05.flac", "year": 2030}])
    assert mti._existing_album_year_from_sibling(db, 1, _ident, 1, 5) is None
