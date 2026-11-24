"""Standalone maintenance must use Library-v2 import and missing lifecycles."""

from __future__ import annotations

from database.music_database import MusicDatabase

# The lifted module resolves the database through its own injected getter.
_db_holder = {}


def _configure_paths(monkeypatch, web_server, transfer, staging, **more):
    values = {
        "soulseek.transfer_path": str(transfer),
        "import.staging_path": str(staging),
        "import.transfer_is_permanent": False,
        **{k.replace("__", "."): v for k, v in more.items()},
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
    # review 4 R4-D-3: a file back at the path of a row SoulSync deleted
    returned = transfer / "returned.flac"
    for f in (known, gone, new, returned):
        f.write_bytes(b"test")
    db = MusicDatabase(str(tmp_path / "music.db"))
    _db_holder["db"] = db
    with db._get_connection() as conn:
        lib2_seed.track(conn, "Various Artists", "Now 100", "Known", path=str(known))
        back = lib2_seed.track(conn, "Various Artists", "Now 100", "Back", path=str(gone))
        # review 4: 'missing' was a state the app never writes
        conn.execute("UPDATE lib2_track_files SET file_state='missing_confirmed' WHERE track_id=?", (back,))
        retired = lib2_seed.track(conn, "Various Artists", "Now 100", "Returned", path=str(returned))
        conn.execute("UPDATE lib2_track_files SET file_state='deleted' WHERE track_id=?", (retired,))
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

    # a deleted row isn't a known file: autolink re-activates it
    assert sorted(imports) == sorted([str(new), str(returned)])
    with db._get_connection() as conn:
        states = {r[0] for r in conn.execute(
            "SELECT file_state FROM lib2_track_files WHERE track_id=?", (back,))}
        rows_on_known = conn.execute(
            "SELECT COUNT(*) FROM lib2_track_files WHERE path=?", (str(known),)).fetchone()[0]
    assert states == {"active"} and rows_on_known == 1


def test_a_symlinked_transfer_moves_new_files_into_staging(tmp_path, monkeypatch):
    """third #1062 review G03-6: the walk yields realpaths but rel_path was
    taken against the unresolved Transfer, so through a symlink every move
    target was "../../real/..." and left Staging"""
    import os
    import web_server
    from api import database_admin as db_admin

    real = tmp_path / "disk1" / "music"
    (real / "Artist" / "Album").mkdir(parents=True)
    (real / "Artist" / "Album" / "new.flac").write_bytes(b"fLaC")
    transfer = tmp_path / "Transfer"
    os.symlink(real, transfer)
    staging = tmp_path / "Staging"
    staging.mkdir()
    db = MusicDatabase(str(tmp_path / "music.db"))
    _db_holder["db"] = db
    _configure_paths(monkeypatch, web_server, transfer, staging)
    monkeypatch.setattr(web_server, "get_database", lambda: db)
    monkeypatch.setattr("core.library2.scan.rescan_files", lambda database, *, file_ids: {})
    errors = []
    monkeypatch.setattr(db_admin, "_db_update_phase_callback", lambda *_: None)
    monkeypatch.setattr(db_admin, "add_activity_item", lambda *_: None)
    monkeypatch.setattr(db_admin, "_db_update_finished_callback", lambda *args: None)
    monkeypatch.setattr(db_admin, "_db_update_error_callback", lambda error: errors.append(error))

    db_admin._run_soulsync_deep_scan()

    assert errors == []
    assert (staging / "Artist" / "Album" / "new.flac").is_file()
    assert not (real / "Artist" / "Album" / "new.flac").exists()


def _quiet(monkeypatch, db_admin, errors):
    monkeypatch.setattr(db_admin, "_db_update_phase_callback", lambda *_: None)
    monkeypatch.setattr(db_admin, "add_activity_item", lambda *_: None)
    monkeypatch.setattr(db_admin, "_db_update_finished_callback", lambda *args: None)
    monkeypatch.setattr(db_admin, "_db_update_error_callback", lambda error: errors.append(error))


def test_a_refresh_never_indexes_an_album_that_is_still_downloading(tmp_path, monkeypatch):
    """an album downloads into <music folder>/.soulsync_atomic_staging and is
    published whole. the standalone refresh walked hidden folders, so it
    could index half an album as library tracks."""
    import web_server
    from api import database_admin as db_admin

    transfer = tmp_path / "Transfer"
    staging = tmp_path / "Staging"
    (transfer / "Artist" / "Album").mkdir(parents=True)
    (transfer / ".soulsync_atomic_staging" / "job1").mkdir(parents=True)
    staging.mkdir()
    real = transfer / "Artist" / "Album" / "01.flac"
    real.write_bytes(b"fLaC")
    (transfer / ".soulsync_atomic_staging" / "job1" / "02.flac").write_bytes(b"fLaC")
    db = MusicDatabase(str(tmp_path / "music.db"))
    _db_holder["db"] = db
    _configure_paths(monkeypatch, web_server, transfer, staging)
    monkeypatch.setattr(web_server, "get_database", lambda: db)
    imports = []
    monkeypatch.setattr(
        "core.library2.autolink.link_download_into_library_v2",
        lambda context, *, raise_on_error=False: imports.append(context["_final_processed_path"]) or 1,
    )
    errors = []
    _quiet(monkeypatch, db_admin, errors)

    db_admin._run_soulsync_full_refresh()
    db_admin._run_soulsync_full_refresh(incremental=True)

    assert errors == []
    assert set(imports) == {str(real)}


def test_a_deep_scan_never_moves_an_album_that_is_still_downloading(tmp_path, monkeypatch):
    import web_server
    from api import database_admin as db_admin

    transfer = tmp_path / "Transfer"
    staging = tmp_path / "Staging"
    (transfer / "Artist" / "Album").mkdir(parents=True)
    (transfer / ".soulsync_atomic_staging" / "job1").mkdir(parents=True)
    staging.mkdir()
    (transfer / "Artist" / "Album" / "new.flac").write_bytes(b"fLaC")
    partial = transfer / ".soulsync_atomic_staging" / "job1" / "02.flac"
    partial.write_bytes(b"fLaC")
    db = MusicDatabase(str(tmp_path / "music.db"))
    _db_holder["db"] = db
    _configure_paths(monkeypatch, web_server, transfer, staging)
    monkeypatch.setattr(web_server, "get_database", lambda: db)
    monkeypatch.setattr("core.library2.scan.rescan_files", lambda database, *, file_ids: {})
    errors = []
    _quiet(monkeypatch, db_admin, errors)

    db_admin._run_soulsync_deep_scan()

    assert errors == []
    assert partial.is_file()                                    # left where the download put it
    assert (staging / "Artist" / "Album" / "new.flac").is_file()   # a real untracked file still moves
    assert not any(p.name == "02.flac" for p in staging.rglob("*"))


# ── deep scan adds what was missed; every scan reads every music folder ──────

def _standalone(tmp_path, monkeypatch, **settings):
    import web_server
    from api import database_admin as db_admin

    transfer = tmp_path / "Transfer"
    staging = tmp_path / "Staging"
    transfer.mkdir()
    staging.mkdir()
    db = MusicDatabase(str(tmp_path / "music.db"))
    _db_holder["db"] = db
    _configure_paths(monkeypatch, web_server, transfer, staging, **settings)
    monkeypatch.setattr(web_server, "get_database", lambda: db)
    said = {"errors": [], "summary": [], "finished": []}
    monkeypatch.setattr(db_admin, "_db_update_phase_callback", lambda *_: None)
    monkeypatch.setattr(db_admin, "add_activity_item", lambda *a: said["summary"].append(a[2]))
    monkeypatch.setattr(db_admin, "_db_update_finished_callback", lambda *a: said["finished"].append(a))
    monkeypatch.setattr(db_admin, "_db_update_error_callback", lambda e: said["errors"].append(e))
    monkeypatch.setattr("core.library2.scan.rescan_files", lambda database, **k: {})
    imports = []
    monkeypatch.setattr(
        "core.library2.autolink.link_download_into_library_v2",
        lambda context, *, raise_on_error=False: imports.append(context["_final_processed_path"]) or len(imports),
    )
    return db_admin, db, transfer, staging, said, imports


def _known_file(db, path):
    with db._get_connection() as conn:
        artist = conn.execute("INSERT INTO lib2_artists(name,name_key) VALUES('A','a')").lastrowid
        album = conn.execute("INSERT INTO lib2_albums(primary_artist_id,title,origin) "
                             "VALUES(?,'Album','library')", (artist,)).lastrowid
        track = conn.execute("INSERT INTO lib2_tracks(album_id,title) VALUES(?,'Song')", (album,)).lastrowid
        conn.execute("INSERT INTO lib2_track_files(track_id,path,is_primary,file_state) "
                     "VALUES(?,?,1,'active')", (track, str(path)))
        conn.commit()


def test_a_deep_scan_adds_the_files_it_finds_in_a_permanent_library(tmp_path, monkeypatch):
    """the music folder is the library (the default): a file in it the
    library doesn't know is one a scan missed. the deep scan used to warn
    "nothing was moved" and leave it out of the library."""
    import os
    db_admin, db, transfer, staging, said, imports = _standalone(
        tmp_path, monkeypatch, import__transfer_is_permanent=True)
    (transfer / "Artist" / "Album").mkdir(parents=True)
    known = transfer / "Artist" / "Album" / "01.flac"
    missed = transfer / "Artist" / "Album" / "02.flac"
    known.write_bytes(b"fLaC")
    missed.write_bytes(b"fLaC")
    _known_file(db, known)

    db_admin._run_soulsync_deep_scan()

    assert said["errors"] == []
    assert imports == [os.path.realpath(str(missed))]          # the known one isn't imported again
    assert missed.is_file() and not any(staging.rglob("*.flac"))   # indexed where it sits, never moved
    assert "1 new files added" in said["summary"][-1] and "moved" not in said["summary"][-1]
    assert said["finished"][-1][3] == 1


def test_every_scan_reads_every_music_folder(tmp_path, monkeypatch):
    """a library on more than one drive is one library. the scans read only
    the main music folder; the others were only read by the folder watch,
    which is off by default."""
    from core.library2 import folder_rescan
    second = tmp_path / "Drive2"
    nested_root = tmp_path / "Transfer" / "Inside"
    gone = tmp_path / "Unplugged"
    db_admin, db, transfer, staging, said, imports = _standalone(
        tmp_path, monkeypatch, import__transfer_is_permanent=True,
        library__music_paths=[str(second), str(nested_root), str(gone)])
    (second / "B" / "Album").mkdir(parents=True)
    (second / ".hidden").mkdir()
    new = second / "B" / "Album" / "new.flac"
    old = second / "B" / "Album" / "old.flac"
    new.write_bytes(b"fLaC")
    old.write_bytes(b"fLaC")
    (second / ".hidden" / "partial.flac").write_bytes(b"fLaC")
    nested_root.mkdir(parents=True)
    _known_file(db, old)

    adopted, reconciled = [], []
    monkeypatch.setattr(folder_rescan, "_adopt", lambda database, paths: adopted.append(list(paths)) or [1] * len(paths))
    real = folder_rescan.process_changes

    def process(database, root, units, moves=None, **kw):
        if root == str(gone) or not __import__("os").path.isdir(root):
            return real(database, root, units, moves, **kw)       # the real outage answer
        reconciled.append((root, units))
        return {"added": 2, "missing": 1, "moved": 0}

    monkeypatch.setattr(folder_rescan, "process_changes", process)

    # incremental: only what the library doesn't know, hidden folders left alone
    db_admin._run_soulsync_full_refresh(incremental=True)
    assert adopted == [[str(new)]] and reconciled == []
    assert "1 other music folder read (1 new)" in said["summary"][-1]
    assert "1 music folder(s) not reachable, skipped" in said["summary"][-1]

    # full and deep: the whole folder, the way the folder watch reads one
    db_admin._run_soulsync_full_refresh()
    db_admin._run_soulsync_deep_scan()
    assert reconciled == [(str(second), [(str(second), True)])] * 2
    assert "1 other music folder read (2 new, 1 missing)" in said["summary"][-1]
    # a folder inside the main one is read with it, never twice; the unplugged
    # one is skipped and said, and nothing failed
    assert said["errors"] == []
    assert all("Inside" not in root for root, _ in reconciled)


def test_one_music_folder_scans_as_before(tmp_path, monkeypatch):
    from core.library2 import folder_rescan
    db_admin, db, transfer, staging, said, imports = _standalone(tmp_path, monkeypatch)
    (transfer / "a.flac").write_bytes(b"fLaC")
    monkeypatch.setattr(folder_rescan, "process_changes",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("no other folder to read")))
    monkeypatch.setattr(folder_rescan, "_adopt",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("no other folder to read")))
    db_admin._run_soulsync_full_refresh(incremental=True)
    db_admin._run_soulsync_full_refresh()
    assert said["errors"] == [] and "other music folder" not in said["summary"][-1]
