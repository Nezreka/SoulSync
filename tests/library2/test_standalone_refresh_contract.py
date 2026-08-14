"""Standalone maintenance must use Library-v2 import and missing lifecycles."""

from __future__ import annotations

from database.music_database import MusicDatabase


def _configure_paths(monkeypatch, web_server, transfer, staging):
    values = {
        "soulseek.transfer_path": str(transfer),
        "import.staging_path": str(staging),
        "import.transfer_is_permanent": False,
    }
    monkeypatch.setattr(
        web_server.config_manager,
        "get",
        lambda key, default=None: values.get(key, default),
    )
    monkeypatch.setattr(web_server, "docker_resolve_path", lambda path: path)


def test_standalone_full_refresh_uses_strict_native_import_without_clearing(
    tmp_path, monkeypatch,
):
    import web_server

    transfer = tmp_path / "Transfer"
    staging = tmp_path / "Staging"
    transfer.mkdir()
    staging.mkdir()
    audio = transfer / "song.flac"
    audio.write_bytes(b"test")
    db = MusicDatabase(str(tmp_path / "music.db"))
    _configure_paths(monkeypatch, web_server, transfer, staging)
    monkeypatch.setattr(web_server, "get_database", lambda: db)
    monkeypatch.setattr(
        db,
        "clear_server_data",
        lambda _source: (_ for _ in ()).throw(AssertionError("must not clear")),
    )

    imports = []
    monkeypatch.setattr(
        "core.library2.autolink.link_download_into_library_v2",
        lambda context, *, raise_on_error=False: (
            imports.append((context["_final_processed_path"], raise_on_error)) or 17
        ),
    )
    finished, errors = [], []
    monkeypatch.setattr(web_server, "_db_update_phase_callback", lambda *_: None)
    monkeypatch.setattr(web_server, "add_activity_item", lambda *_: None)
    monkeypatch.setattr(
        web_server, "_db_update_finished_callback", lambda *args: finished.append(args),
    )
    monkeypatch.setattr(
        web_server, "_db_update_error_callback", lambda error: errors.append(error),
    )

    web_server._run_soulsync_full_refresh()

    assert errors == []
    assert imports == [(str(audio), True)]
    assert finished == [(0, 0, 1, 1, 0)]


def test_standalone_deep_scan_routes_missing_files_through_rescan(
    tmp_path, monkeypatch,
):
    import web_server

    transfer = tmp_path / "Transfer"
    staging = tmp_path / "Staging"
    transfer.mkdir()
    staging.mkdir()
    missing = transfer / "gone.flac"
    db = MusicDatabase(str(tmp_path / "music.db"))
    with db._get_connection() as conn:
        artist = conn.execute(
            "INSERT INTO lib2_artists(name,name_key) VALUES('A','a')"
        ).lastrowid
        album = conn.execute(
            "INSERT INTO lib2_albums(primary_artist_id,title,origin) "
            "VALUES(?,'Album','library')", (artist,),
        ).lastrowid
        track = conn.execute(
            "INSERT INTO lib2_tracks(album_id,title) VALUES(?,'Song')", (album,),
        ).lastrowid
        file_id = conn.execute(
            "INSERT INTO lib2_track_files(track_id,path,is_primary,file_state) "
            "VALUES(?,?,1,'active')", (track, str(missing)),
        ).lastrowid

    _configure_paths(monkeypatch, web_server, transfer, staging)
    monkeypatch.setattr(web_server, "get_database", lambda: db)
    rescans = []
    monkeypatch.setattr(
        "core.library2.scan.rescan_files",
        lambda database, *, file_ids: (
            rescans.append((database, file_ids)) or {"missing": len(file_ids)}
        ),
    )
    finished, errors = [], []
    monkeypatch.setattr(web_server, "_db_update_phase_callback", lambda *_: None)
    monkeypatch.setattr(web_server, "add_activity_item", lambda *_: None)
    monkeypatch.setattr(
        web_server, "_db_update_finished_callback", lambda *args: finished.append(args),
    )
    monkeypatch.setattr(
        web_server, "_db_update_error_callback", lambda error: errors.append(error),
    )

    web_server._run_soulsync_deep_scan()

    assert errors == []
    assert rescans == [(db, [file_id])]
    assert finished == [(0, 0, 0, 1, 0)]
    with db._get_connection() as conn:
        assert conn.execute(
            "SELECT COUNT(*) FROM lib2_tracks WHERE id=?", (track,),
        ).fetchone()[0] == 1
        assert conn.execute(
            "SELECT COUNT(*) FROM lib2_track_files WHERE id=?", (file_id,),
        ).fetchone()[0] == 1
