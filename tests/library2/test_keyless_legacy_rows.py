"""The library upgrade on a legacy library with rows that have no id.

The legacy ``tracks.id`` is ``TEXT PRIMARY KEY`` (the media-server id rebuild),
which SQLite lets hold NULL. One such row used to stop the whole upgrade with
"NOT NULL constraint failed: lib2_file_origins.legacy_track_id", and with it
downloads, sync and automations (in-app chat report, Nov 16).
"""

from __future__ import annotations

import sqlite3
from contextlib import closing

from core.library2 import bootstrap as lib2_bootstrap
from core.library2 import importer
from core.library2.importer import import_legacy_library


def _enabled(_key, _default=None):
    return True


def _add_keyless_rows(path):
    with closing(sqlite3.connect(path)) as conn:
        conn.execute("UPDATE tracks SET server_source='jellyfin'")
        rows = [
            # two id-less rows on the same album, each with its own file
            (None, "10", "1", "Keyless One", 3, "/m/keyless1.flac"),
            (None, "10", "1", "Keyless Two", 4, "/m/keyless2.flac"),
            # no id and no file: nothing to address and nothing to keep
            (None, "10", "1", "Keyless Ghost", 5, None),
            # a text media-server id (jellyfin guid) next to them
            ("4f8e2c1a9b7d4e6f", "10", "1", "Guid Track", 6, "/m/guid.flac"),
        ]
        for rid, album, artist, title, number, path_ in rows:
            conn.execute(
                "INSERT INTO tracks(id, album_id, artist_id, title, track_number, "
                "duration, file_path, server_source) VALUES(?,?,?,?,?,1000,?,'jellyfin')",
                (rid, album, artist, title, number, path_))
        # id-less artists and albums: nothing can point at them
        conn.execute("INSERT INTO artists(id, name, server_source) VALUES(NULL,'Nobody','jellyfin')")
        conn.execute("INSERT INTO albums(id, artist_id, title, server_source) "
                     "VALUES(NULL,'1','Ghost Album','jellyfin')")
        conn.commit()
        assert conn.execute("SELECT COUNT(*) FROM tracks WHERE id IS NULL").fetchone()[0] == 3


def _snapshot(db):
    with closing(db._get_connection()) as conn:
        files = {r["path"]: (r["track_id"], r["legacy_track_id"]) for r in conn.execute(
            "SELECT path, track_id, legacy_track_id FROM lib2_track_files")}
        titles = [r[0] for r in conn.execute("SELECT title FROM lib2_tracks ORDER BY title")]
        titles += [r[0] for r in conn.execute("SELECT title FROM lib2_albums ORDER BY title")]
        titles += [r[0] for r in conn.execute("SELECT name FROM lib2_artists ORDER BY name")]
        origins = [r[0] for r in conn.execute(
            "SELECT legacy_track_id FROM lib2_file_origins ORDER BY legacy_track_id")]
        bad_server_ids = conn.execute(
            "SELECT COUNT(*) FROM lib2_tracks WHERE server_id='None'").fetchone()[0]
    return files, titles, origins, bad_server_ids


def test_upgrade_completes_and_keeps_the_files_of_rows_with_no_id(migrated_legacy_db):
    """chat report: one id-less legacy track failed the whole library upgrade."""
    db = migrated_legacy_db
    _add_keyless_rows(db.path)

    result = lib2_bootstrap.run_bootstrap_if_needed(db, _enabled)

    assert result.get("success") is True, result
    assert lib2_bootstrap.get_state(db)["status"] == "done"
    files, titles, origins, bad_server_ids = _snapshot(db)
    # each id-less file is imported, on its own track, with no legacy back-reference
    assert files["/m/keyless1.flac"][1] is None
    assert files["/m/keyless2.flac"][1] is None
    assert files["/m/keyless1.flac"][0] != files["/m/keyless2.flac"][0]
    assert titles.count("Keyless One") == 1 and titles.count("Keyless Two") == 1
    assert "Keyless Ghost" not in titles
    assert "Ghost Album" not in titles and "Nobody" not in titles
    # the text id keeps its provenance like any other row
    assert str(files["/m/guid.flac"][1]) == "4f8e2c1a9b7d4e6f"
    assert "4f8e2c1a9b7d4e6f" in [str(o) for o in origins]
    assert None not in origins
    # a missing id is not a server id called "None"
    assert bad_server_ids == 0


def test_reimport_does_not_duplicate_rows_with_no_id(migrated_legacy_db):
    """chat report: a re-run finds an id-less row again by its file."""
    db = migrated_legacy_db
    _add_keyless_rows(db.path)
    import_legacy_library(db)
    first = _snapshot(db)

    again = import_legacy_library(db)

    # found again, not written twice and left for the dedup repair to fold
    assert again["files"] == 0
    assert again["dedup_repair"]["shared_files_folded"] == 0
    assert _snapshot(db) == first


def test_failed_upgrade_resumes_past_the_row_that_stopped_it(migrated_legacy_db, monkeypatch):
    """chat report: after updating, the next start finishes the upgrade from
    where the failed one stopped, with nothing doubled."""
    db = migrated_legacy_db
    _add_keyless_rows(db.path)
    # checkpoint every row, so the failed attempt leaves committed work behind
    monkeypatch.setattr(importer, "IMPORT_BATCH_SIZE", 1)
    real_identity = importer._server_identity

    def broken(row):
        if "file_path" in row.keys() and row["title"] == "Keyless Two":
            raise sqlite3.IntegrityError(
                "NOT NULL constraint failed: lib2_file_origins.legacy_track_id")
        return real_identity(row)

    monkeypatch.setattr(importer, "_server_identity", broken)
    assert lib2_bootstrap.run_bootstrap_if_needed(db, _enabled)["success"] is False
    state = lib2_bootstrap.get_state(db)
    assert state["status"] == "failed"
    assert state["resume_stage"] == "tracks" and state["resume_rowid"] > 0

    monkeypatch.setattr(importer, "_server_identity", real_identity)
    assert lib2_bootstrap.run_bootstrap_if_needed(db, _enabled)["success"] is True

    assert lib2_bootstrap.get_state(db)["status"] == "done"
    files, titles, _origins, _bad = _snapshot(db)
    for path in ("/m/01.flac", "/m/single.flac", "/m/keyless1.flac",
                 "/m/keyless2.flac", "/m/guid.flac"):
        assert path in files, path
    for title in ("Keyless One", "Keyless Two", "Guid Track", "Hotline Bling"):
        assert titles.count(title) == 1, (title, titles)
