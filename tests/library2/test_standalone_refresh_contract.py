"""Standalone maintenance must use Library-v2 import and missing lifecycles."""

from __future__ import annotations

from database.music_database import MusicDatabase

# The lifted module resolves the database through its own injected getter.
_db_holder = {}


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
    # The db-update family lives in api/database_admin.py since the 3.3.2 lift
    # and reads its own module-level config_manager/docker_resolve_path, which
    # configure() injects at boot. Point those at the same stubs, or the scan
    # walks the real configured Transfer folder instead of tmp_path.
    from api import database_admin as db_admin

    monkeypatch.setattr(db_admin, "config_manager", web_server.config_manager)
    monkeypatch.setattr(db_admin, "docker_resolve_path", lambda path: path)
    monkeypatch.setattr(db_admin, "get_database", lambda: _db_holder["db"])


def test_standalone_full_refresh_uses_strict_native_import_without_clearing(
    tmp_path, monkeypatch,
):
    import web_server
    from api import database_admin as db_admin

    transfer = tmp_path / "Transfer"
    staging = tmp_path / "Staging"
    transfer.mkdir()
    staging.mkdir()
    audio = transfer / "song.flac"
    audio.write_bytes(b"test")
    db = MusicDatabase(str(tmp_path / "music.db"))
    _db_holder["db"] = db
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
    monkeypatch.setattr(db_admin, "_db_update_phase_callback", lambda *_: None)
    monkeypatch.setattr(db_admin, "add_activity_item", lambda *_: None)
    monkeypatch.setattr(
        db_admin, "_db_update_finished_callback", lambda *args: finished.append(args),
    )
    monkeypatch.setattr(
        db_admin, "_db_update_error_callback", lambda error: errors.append(error),
    )

    db_admin._run_soulsync_full_refresh()

    assert errors == []
    assert imports == [(str(audio), True)]
    assert finished == [(0, 0, 1, 1, 0)]


def test_standalone_deep_scan_routes_missing_files_through_rescan(
    tmp_path, monkeypatch,
):
    import web_server
    from api import database_admin as db_admin

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

    _db_holder["db"] = db
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
    monkeypatch.setattr(db_admin, "_db_update_phase_callback", lambda *_: None)
    monkeypatch.setattr(db_admin, "add_activity_item", lambda *_: None)
    monkeypatch.setattr(
        db_admin, "_db_update_finished_callback", lambda *args: finished.append(args),
    )
    monkeypatch.setattr(
        db_admin, "_db_update_error_callback", lambda error: errors.append(error),
    )

    db_admin._run_soulsync_deep_scan()

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


def test_a_full_refresh_never_imports_a_known_file_twice(tmp_path, monkeypatch):
    """third #1062 review G03-3: autolink on a file the library already held
    made a second file row on the same path, under its track-artist tag, so a
    compilation split into one album per performer. a known file is
    re-indexed in place; a missing one comes back."""
    import web_server
    from api import database_admin as db_admin
    from tests import lib2_seed

    transfer, staging = tmp_path / "Transfer", tmp_path / "Staging"
    transfer.mkdir()
    staging.mkdir()
    known, gone, new = (transfer / "known.flac", transfer / "back.flac", transfer / "new.flac")
    for f in (known, gone, new):
        f.write_bytes(b"test")
    db = MusicDatabase(str(tmp_path / "music.db"))
    _db_holder["db"] = db
    with db._get_connection() as conn:
        lib2_seed.track(conn, "Various Artists", "Now 100", "Known", path=str(known))
        back = lib2_seed.track(conn, "Various Artists", "Now 100", "Back", path=str(gone))
        conn.execute("UPDATE lib2_track_files SET file_state='missing' WHERE track_id=?", (back,))
        conn.commit()
    _configure_paths(monkeypatch, web_server, transfer, staging)
    monkeypatch.setattr(web_server, "get_database", lambda: db)
    imports = []
    monkeypatch.setattr(
        "core.library2.autolink.link_download_into_library_v2",
        lambda context, *, raise_on_error=False: (
            imports.append(context["_final_processed_path"]) or 99))
    for name in ("_db_update_phase_callback", "add_activity_item",
                 "_db_update_finished_callback", "_db_update_error_callback"):
        monkeypatch.setattr(db_admin, name, lambda *_a, **_k: None)

    db_admin._run_soulsync_full_refresh()

    assert imports == [str(new)]
    with db._get_connection() as conn:
        states = {r[0] for r in conn.execute(
            "SELECT file_state FROM lib2_track_files WHERE track_id=?", (back,))}
        rows_on_known = conn.execute(
            "SELECT COUNT(*) FROM lib2_track_files WHERE path=?", (str(known),)).fetchone()[0]
    assert states == {"active"} and rows_on_known == 1
