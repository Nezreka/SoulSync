"""Repeated scan imports in Library History (SeadogsBooty: 705k import rows for
256k files, unchanged files logged 130+ times by 3.6.1's refreshes)."""

from contextlib import closing

from core.maintenance import library_history_dedupe as D
from database.music_database import MusicDatabase


def _rows(db):
    with closing(db._get_connection()) as conn:
        return [tuple(r) for r in conn.execute(
            "SELECT id, event_type, file_path, download_source FROM library_history ORDER BY id")]


def test_repeats_go_and_the_first_import_of_each_file_stays(tmp_path):
    db = MusicDatabase(str(tmp_path / "m.db"))
    for _ in range(5):
        db.add_library_history_entry("import", "Cruise", artist_name="FGL", album_name="Here's",
                                     server_source="plex", file_path="/music/a.flac")
    db.add_library_history_entry("import", "Other", artist_name="FGL", server_source="plex",
                                 file_path="/music/b.flac")
    # a download of the same file and a re-identified import are not repeats
    db.add_library_history_entry("download", "Cruise", artist_name="FGL", file_path="/music/a.flac",
                                 download_source="soulseek")
    db.add_library_history_entry("import", "Cruise (Remix)", artist_name="FGL", album_name="Here's",
                                 server_source="plex", file_path="/music/a.flac")

    stats = D.dedupe_scan_import_history(db)

    assert stats["removed"] == 4
    rows = _rows(db)
    assert [r[0] for r in rows if r[2] == "/music/a.flac" and r[1] == "import"][0] == 1
    assert len(rows) == 4  # a once, b, the download, the remix
    assert any(r[1] == "download" for r in rows)


def test_it_runs_once(tmp_path):
    db = MusicDatabase(str(tmp_path / "m.db"))
    D.dedupe_scan_import_history(db)
    for _ in range(3):
        db.add_library_history_entry("import", "Song", server_source="plex", file_path="/m/s.flac")
    assert D.dedupe_scan_import_history(db) == {"checked": 0, "removed": 0}
    assert len(_rows(db)) == 3


def test_a_stopped_pass_leaves_no_marker(tmp_path):
    db = MusicDatabase(str(tmp_path / "m.db"))
    for _ in range(2):
        db.add_library_history_entry("import", "Song", server_source="plex", file_path="/m/s.flac")
    D.dedupe_scan_import_history(db, should_stop=lambda: True)
    assert D.dedupe_scan_import_history(db)["removed"] == 1


def test_a_scan_logs_a_file_it_already_logged_only_once(tmp_path):
    from types import SimpleNamespace

    db = MusicDatabase(str(tmp_path / "m.db"))
    track = SimpleNamespace(title="Song", grandparentTitle="Artist", parentTitle="Album")
    for _ in range(3):
        db._log_scanned_import(track, "/m/s.flac", "plex")
    assert len(_rows(db)) == 1
