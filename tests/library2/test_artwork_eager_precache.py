"""New entities warm their artwork right away (perf25-04).

The batch precache job only covers the state of its last run, so anything that
joined the library afterwards used to hit the cold path on first view.
"""

from __future__ import annotations

from core.library2 import artwork


def _shim(tmp_path):
    class _DB:
        database_path = str(tmp_path / "music.db")

        def _get_connection(self):
            return object()

    return _DB()


def test_schedules_only_the_uncached_targets(tmp_path, monkeypatch):
    database = _shim(tmp_path)
    artwork.artwork_file(database, "album", 1).write_bytes(b"\xff\xd8\xff")
    artwork.forget_artwork_versions(database)

    scheduled = []
    monkeypatch.setattr(
        artwork,
        "schedule_artwork_build",
        lambda db, cfg, kind, eid: scheduled.append((kind, eid)) or object(),
    )

    count = artwork.schedule_missing_artwork(
        database, None, [("album", 1), ("album", 2), ("artist", 3)]
    )

    assert scheduled == [("album", 2), ("artist", 3)]
    assert count == 2


def test_duplicate_targets_are_requested_once(tmp_path, monkeypatch):
    database = _shim(tmp_path)
    scheduled = []
    monkeypatch.setattr(
        artwork,
        "schedule_artwork_build",
        lambda db, cfg, kind, eid: scheduled.append((kind, eid)) or object(),
    )

    artwork.schedule_missing_artwork(
        database, None, [("artist", 4), ("artist", 4), ("album", 4)]
    )

    assert scheduled == [("artist", 4), ("album", 4)]


def test_bad_targets_never_raise(tmp_path):
    database = _shim(tmp_path)

    assert artwork.schedule_missing_artwork(database, None, []) == 0
    assert artwork.schedule_missing_artwork(database, None, [("artist", None)]) == 0


def test_autolink_warms_the_album_and_its_artist(imported_conn, legacy_db, monkeypatch):
    from core.library2 import autolink

    requests = []
    monkeypatch.setattr(
        artwork,
        "schedule_missing_artwork",
        lambda db, cfg, targets: requests.append(list(targets)),
    )
    row = imported_conn.execute(
        "SELECT id, primary_artist_id FROM lib2_albums ORDER BY id LIMIT 1"
    ).fetchone()

    autolink._warm_new_artwork(legacy_db, imported_conn, row["id"])

    assert requests == [[("album", row["id"]), ("artist", row["primary_artist_id"])]]
