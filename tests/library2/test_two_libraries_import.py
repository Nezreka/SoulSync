"""Upgrading an install that already had own libraries (upstream 3.4.4, #1199).

Upstream gave every own library its own artist/album/track rows and a server
id per row. Library v2 keeps one catalogue and puts the library on the file,
so the import has to fold the copies together and still remember whose file,
whose server id and whose intent each one was.
"""

from __future__ import annotations

import json
import os

import pytest

from core import library_scope
from tests.library2.test_two_libraries import lib  # noqa: F401 - the fixture

_LEGACY_DDL = """
CREATE TABLE artists(id TEXT PRIMARY KEY, name TEXT, server_source TEXT,
                     owner_profile_id INTEGER, spotify_artist_id TEXT);
CREATE TABLE albums(id TEXT PRIMARY KEY, artist_id TEXT, title TEXT, track_count INTEGER,
                    spotify_album_id TEXT, server_source TEXT, owner_profile_id INTEGER,
                    year INTEGER);
CREATE TABLE tracks(id TEXT PRIMARY KEY, album_id TEXT, artist_id TEXT, title TEXT,
                    track_number INTEGER, file_path TEXT, bitrate INTEGER,
                    spotify_track_id TEXT, play_count INTEGER, server_source TEXT,
                    owner_profile_id INTEGER, disc_number INTEGER DEFAULT 1);
"""


def _legacy(db, artists=(), albums=(), tracks=()):
    """Legacy rows in the order given -- the order is the rowid order the
    import walks them in."""
    with db._get_connection() as conn:
        conn.executescript(_LEGACY_DDL)
        conn.executemany("INSERT INTO artists VALUES(?,?,'plex',?,?)", artists)
        conn.executemany("INSERT INTO albums VALUES(?,?,?,?,?,'plex',?,?)", albums)
        conn.executemany("INSERT INTO tracks VALUES(?,?,?,?,?,?,1000,?,?,'plex',?,?)", tracks)
        conn.commit()


@pytest.fixture(params=["house_first", "kim_first"])
def upgraded(lib, request):
    """The house and Kim both own "Album B" (Kim also has a bonus track the
    house lacks) and Kim alone owns "Album D"; then the import runs. Both
    walk orders: a full refresh of the house's library re-inserts its rows
    behind Kim's."""
    from core.library2.importer import import_legacy_library
    shared = lambda *p: os.path.join(lib.shared, *p)  # noqa: E731
    kims = lambda *p: os.path.join(lib.kim_root, *p)  # noqa: E731
    k = lib.kim
    house = dict(
        artists=[("100", "Artist A", None, "spA0000000000000000001")],
        albums=[("200", "100", "Album B", 2, "spB0000000000000000001", None, 2001)],
        tracks=[("300", "200", "100", "Song One", 1, shared("A", "B", "01.flac"), "spT1", 7, None, 1),
                ("301", "200", "100", "Song Two", 2, shared("A", "B", "02.flac"), "spT2", 0, None, 1)])
    hers = dict(
        artists=[("1100", "Artist A", k, "spA0000000000000000001"),
                 ("1101", "Artist C", k, None)],
        albums=[("1200", "1100", "Album B", 2, "spB0000000000000000001", k, 2001),
                ("1201", "1101", "Album D", 1, None, k, None)],
        tracks=[("1300", "1200", "1100", "Song One", 1, kims("A", "B", "01.flac"), "spT1", 3, k, 1),
                ("1302", "1200", "1100", "Bonus Cut", 3, kims("A", "B", "03.flac"), None, 0, k, 1),
                ("1301", "1201", "1101", "Only Song", 1, kims("C", "D", "01.flac"), None, 0, k, 1)])
    first, second = (house, hers) if request.param == "house_first" else (hers, house)
    _legacy(lib.db, **{key: first[key] + second[key] for key in house})
    with lib.db._get_connection() as conn:
        conn.execute(
            "INSERT INTO wishlist_tracks(spotify_track_id, spotify_data, source_type, source_info,"
            " profile_id, quality_profile_id) VALUES(?,?,'manual',?,?,1)",
            ("spY0000000000000000001", json.dumps({
                "id": "spY0000000000000000001", "name": "Wanted Y",
                "artists": [{"name": "Artist F"}],
                "album": {"id": "alb-y", "name": "Album FY", "total_tracks": 10,
                          "album_type": "album", "artists": [{"name": "Artist F"}]}}),
             json.dumps({"provider": "spotify"}), k))
        conn.execute("INSERT INTO watchlist_artists(artist_name, profile_id) VALUES('Artist C', ?)",
                     (k,))
        conn.executemany(
            "INSERT INTO listening_history(track_id, title, played_at, server_source, db_track_id)"
            " VALUES(?, 'Song One', '2026-09-01 10:00:00', 'plex', ?)",
            [("300", 300), ("1300", 1300)])
        conn.executemany(
            "INSERT INTO repair_findings(job_id, finding_type, severity, status, entity_type,"
            " entity_id, title) VALUES('album_tag_consistency', 'album_tag_mismatch', 'info',"
            " 'dismissed', ?, ?, 'Tags differ')", [("album", "200"), ("track", "301")])
        conn.execute(
            "INSERT INTO reorganize_queue(queue_id, album_id, status, enqueued_at, payload)"
            " VALUES('q1', '200', 'queued', 1.0, ?)",
            (json.dumps({"queue_id": "q1", "album_id": "200", "artist_id": "100",
                         "status": "queued", "enqueued_at": 1.0}),))
        conn.execute(
            "INSERT INTO reorganize_queue(queue_id, album_id, status, enqueued_at, payload)"
            " VALUES('q2', '999', 'queued', 2.0, ?)",
            (json.dumps({"queue_id": "q2", "album_id": "999", "status": "queued",
                         "enqueued_at": 2.0}),))
        conn.commit()
    lib.stats = import_legacy_library(lib.db)
    return lib


def _q(db, sql, *args):
    with db._get_connection() as conn:
        return [tuple(r) for r in conn.execute(sql, args).fetchall()]


def _track_id(db, title):
    return _q(db, "SELECT id FROM lib2_tracks WHERE title=?", title)[0][0]


def test_a_release_both_libraries_own_is_one_album(upgraded):
    db = upgraded.db
    assert _q(db, "SELECT COUNT(*) FROM lib2_albums WHERE title='Album B'") == [(1,)]
    song_one = _track_id(db, "Song One")
    owners = {r[0] for r in _q(db, "SELECT owner_profile_id FROM lib2_track_files"
                                   " WHERE track_id=?", song_one)}
    assert owners == {None, upgraded.kim}
    assert _q(db, "SELECT COUNT(*) FROM lib2_tracks WHERE title='Song One'") == [(1,)]
    # the track the house lacks is one more track of the same album
    album = _q(db, "SELECT id FROM lib2_albums WHERE title='Album B'")[0][0]
    assert _q(db, "SELECT album_id FROM lib2_tracks WHERE title='Bonus Cut'") == [(album,)]
    # the copy's artist row is folded into the house's
    assert _q(db, "SELECT COUNT(*) FROM lib2_artists WHERE name='Artist A'") == [(1,)]


def test_kims_files_are_her_intent_not_the_houses(upgraded):
    db, kim = upgraded.db, upgraded.kim
    only = _track_id(db, "Only Song")
    bonus = _track_id(db, "Bonus Cut")
    flags = dict(_q(db, "SELECT id, monitored FROM lib2_tracks"))
    assert flags[only] == 0 and flags[bonus] == 0
    rules = {(r[0], r[1]): r[2] for r in _q(
        db, "SELECT entity_id, profile_id, provenance FROM lib2_monitor_rules"
            " WHERE entity_type='track' AND monitored=1")}
    assert (only, 1) not in rules and (bonus, 1) not in rules
    assert rules[(only, kim)] == "file_import" and rules[(bonus, kim)] == "file_import"
    wanted = {(r[0], r[1]) for r in _q(
        db, "SELECT track_id, profile_id FROM lib2_wanted_tracks WHERE wanted=1")}
    assert (only, kim) in wanted and (only, 1) not in wanted


def test_server_ids_land_in_the_library_that_reported_them(upgraded):
    db, kim = upgraded.db, upgraded.kim
    album = _q(db, "SELECT id FROM lib2_albums WHERE title='Album B'")[0][0]
    mapped = {(r[0], r[1]) for r in _q(
        db, "SELECT server_library_id, server_id FROM lib2_media_server_mappings"
            " WHERE entity_type='album' AND entity_id=?", album)}
    assert mapped == {("", "200"), (f"own:{kim}", "1200")}
    only = _track_id(db, "Only Song")
    assert _q(db, "SELECT server_library_id FROM lib2_media_server_mappings"
                  " WHERE entity_type='track' AND entity_id=?", only) == [(f"own:{kim}",)]
    # the compatibility column keeps the house's id
    assert _q(db, "SELECT server_id FROM lib2_albums WHERE id=?", album) == [("200",)]
    artist = _q(db, "SELECT id FROM lib2_artists WHERE name='Artist A'")[0][0]
    assert {r[0] for r in _q(db, "SELECT server_library_id FROM lib2_media_server_mappings"
                                 " WHERE entity_type='artist' AND entity_id=?", artist)} \
        == {"", f"own:{kim}"}


def test_kims_wishlist_becomes_her_intent(upgraded):
    db, kim = upgraded.db, upgraded.kim
    wanted_y = _track_id(db, "Wanted Y")
    assert _q(db, "SELECT monitored FROM lib2_tracks WHERE id=?", wanted_y) == [(0,)]
    assert _q(db, "SELECT profile_id, provenance FROM lib2_monitor_rules"
                  " WHERE entity_type='track' AND entity_id=? AND monitored=1",
              wanted_y) == [(kim, "wishlist_import")]


def test_kims_watchlist_is_her_intent_right_after_the_import(upgraded):
    db, kim = upgraded.db, upgraded.kim
    artist_c = _q(db, "SELECT id FROM lib2_artists WHERE name='Artist C'")[0][0]
    assert _q(db, "SELECT monitored FROM lib2_monitor_rules WHERE entity_type='artist'"
                  " AND entity_id=? AND profile_id=?", artist_c, kim) == [(1,)]
    # the house does not watch her artist
    assert _q(db, "SELECT monitored FROM lib2_artists WHERE id=?", artist_c) == [(0,)]


def test_the_houses_row_speaks_for_the_album_whoever_came_first(upgraded):
    db = upgraded.db
    song_one = _track_id(db, "Song One")
    assert _q(db, "SELECT play_count FROM lib2_tracks WHERE id=?", song_one) == [(7,)]
    album = _q(db, "SELECT id, primary_artist_id FROM lib2_albums WHERE title='Album B'")[0]
    assert _q(db, "SELECT name FROM lib2_artists WHERE id=?", album[1]) == [("Artist A",)]


def test_history_findings_and_backlog_follow_the_rows(upgraded):
    db = upgraded.db
    album = _q(db, "SELECT id, primary_artist_id FROM lib2_albums WHERE title='Album B'")[0]
    song_one = _track_id(db, "Song One")
    # both plays, the house's and Kim's of her copy, count for the one track
    assert _q(db, "SELECT lib2_track_id FROM listening_history") == [(song_one,), (song_one,)]
    # a dismissal keeps meaning what it meant
    assert sorted(_q(db, "SELECT entity_type, entity_id FROM repair_findings")) == [
        ("album", f"lib2:{album[0]}"), ("track", f"lib2:{_track_id(db, 'Song Two')}")]
    # the reorganize backlog names the imported album; one that did not come over goes
    rows = _q(db, "SELECT queue_id, album_id, payload FROM reorganize_queue")
    assert [(r[0], r[1]) for r in rows] == [("q1", str(album[0]))]
    payload = json.loads(rows[0][2])
    assert payload["album_id"] == str(album[0]) and payload["artist_id"] == str(album[1])
    assert payload["library"] == "shared"  # the house asked, Kim's copy stays put


def test_running_the_import_again_changes_nothing(upgraded):
    from core.library2.importer import import_legacy_library
    db = upgraded.db
    shape = "SELECT (SELECT COUNT(*) FROM lib2_albums), (SELECT COUNT(*) FROM lib2_tracks)," \
            " (SELECT COUNT(*) FROM lib2_track_files), (SELECT COUNT(*) FROM lib2_media_server_mappings)"
    before = _q(db, shape)
    anchor = _q(db, "SELECT legacy_album_id FROM lib2_albums WHERE title='Album B'")
    import_legacy_library(db)
    assert _q(db, shape) == before
    assert _q(db, "SELECT legacy_album_id FROM lib2_albums WHERE title='Album B'") == anchor


def test_a_single_library_install_imports_as_before(lib):
    """No owner on any row: nothing is folded and nothing is mapped early."""
    from core.library2.importer import import_legacy_library
    _legacy(lib.db, artists=[("1", "A", None, None)],
            albums=[("10", "1", "Same", 1, None, None, None), ("11", "1", "Same", 1, None, None, None)],
            tracks=[("100", "10", "1", "One", 1, os.path.join(lib.shared, "a.flac"), None, 0, None, 1),
                    ("101", "11", "1", "One", 1, os.path.join(lib.shared, "b.flac"), None, 0, None, 1)])
    import_legacy_library(lib.db)
    assert _q(lib.db, "SELECT COUNT(*) FROM lib2_tracks") == [(2,)]


def test_same_named_releases_fold_by_their_tracks(lib):
    """Two "Greatest Hits" of the house, Kim owns the second: her copy joins
    the one it shares songs with, not the first of the name."""
    from core.library2.importer import import_legacy_library
    k, shared = lib.kim, lib.shared
    _legacy(lib.db,
            artists=[("1", "A", None, None), ("2", "A", k, None)],
            albums=[("10", "1", "Greatest Hits", 2, None, None, None),
                    ("11", "1", "Greatest Hits", 2, None, None, None),
                    ("20", "2", "Greatest Hits", 2, None, k, None)],
            tracks=[("100", "10", "1", "Old One", 1, os.path.join(shared, "o1.flac"), None, 0, None, 1),
                    ("110", "11", "1", "New One", 1, os.path.join(shared, "n1.flac"), None, 0, None, 1),
                    ("200", "20", "2", "New One", 1, os.path.join(lib.kim_root, "n1.flac"),
                     None, 0, k, 1)])
    import_legacy_library(lib.db)
    new_one = _q(lib.db, "SELECT id, album_id FROM lib2_tracks WHERE title='New One'")
    assert len(new_one) == 1
    assert _q(lib.db, "SELECT COUNT(*) FROM lib2_track_files WHERE track_id=?",
              new_one[0][0]) == [(2,)]
    assert _q(lib.db, "SELECT legacy_album_id FROM lib2_albums WHERE id=?",
              new_one[0][1]) == [(11,)]


def test_a_disc_number_left_at_one_still_joins(lib):
    """Upstream added disc_number with DEFAULT 1: a row no scan touched since
    reads disc 1 where its twin knows disc 2."""
    from core.library2.importer import import_legacy_library
    k = lib.kim
    _legacy(lib.db,
            artists=[("1", "A", None, None), ("2", "A", k, None)],
            albums=[("10", "1", "Double", 2, None, None, None), ("20", "2", "Double", 2, None, k, None)],
            tracks=[("100", "10", "1", "Three", 1, os.path.join(lib.shared, "3.flac"), None, 0, None, 2),
                    ("200", "20", "2", "Three", 1, os.path.join(lib.kim_root, "3.flac"),
                     None, 0, k, 1)])
    import_legacy_library(lib.db)
    assert _q(lib.db, "SELECT COUNT(*) FROM lib2_tracks WHERE title='Three'") == [(1,)]


def test_the_houses_rescan_fixes_what_kims_older_rows_said(lib):
    """Kim's rows first, then the house's after a full refresh: its disc
    numbers are the fresh ones, and they are what the catalogue keeps."""
    from core.library2.importer import import_legacy_library
    k = lib.kim
    _legacy(lib.db,
            artists=[("2", "A", k, None), ("1", "A", None, None)],
            albums=[("20", "2", "Double", 2, None, k, None), ("10", "1", "Double", 2, None, None, None)],
            tracks=[("200", "20", "2", "Three", 1, os.path.join(lib.kim_root, "3.flac"),
                     None, 0, k, 1),
                    ("100", "10", "1", "Three", 1, os.path.join(lib.shared, "3.flac"),
                     None, 0, None, 2)])
    import_legacy_library(lib.db)
    assert _q(lib.db, "SELECT disc_number, track_number FROM lib2_tracks") == [(2, 1)]


def test_a_title_twice_on_a_copy_is_placed_by_position(lib):
    """Kim's deluxe copy carries a live "Song" (2.2) before its studio one
    (1.2): the live cut must not take the house's studio track."""
    from core.library2.importer import import_legacy_library
    k = lib.kim
    _legacy(lib.db,
            artists=[("1", "A", None, None), ("2", "A", k, None)],
            albums=[("10", "1", "Record", 2, None, None, None), ("20", "2", "Record", 4, None, k, None)],
            tracks=[("100", "10", "1", "Intro", 1, os.path.join(lib.shared, "1.flac"), None, 0, None, 1),
                    ("101", "10", "1", "Song", 2, os.path.join(lib.shared, "2.flac"), None, 0, None, 1),
                    ("200", "20", "2", "Song", 2, os.path.join(lib.kim_root, "live.flac"),
                     None, 0, k, 2),
                    ("201", "20", "2", "Song", 2, os.path.join(lib.kim_root, "2.flac"),
                     None, 0, k, 1)])
    import_legacy_library(lib.db)
    studio = _q(lib.db, "SELECT id FROM lib2_tracks WHERE title='Song' AND disc_number=1")
    assert len(studio) == 1
    assert sorted(r[0] for r in _q(lib.db, "SELECT path FROM lib2_track_files WHERE track_id=?",
                                   studio[0][0])) == sorted([os.path.join(lib.kim_root, "2.flac"),
                                                             os.path.join(lib.shared, "2.flac")])


def test_a_library_that_is_gone_imports_as_shared(lib):
    """Kim went back to the shared library before the upgrade: her old rows
    are the house's files, mapped where a scan reads them."""
    from core.library2.importer import import_legacy_library
    from core.library2 import library_roots
    assert lib.db.set_profile_library(lib.kim, "shared", None)
    library_scope.invalidate_library_scope_cache()
    library_roots.sync_library_roots(lib.db)
    k = lib.kim
    _legacy(lib.db, artists=[("2", "C", k, None)],
            albums=[("20", "2", "D", 1, None, k, None)],
            tracks=[("200", "20", "2", "Only", 1, os.path.join(lib.kim_root, "1.flac"),
                     None, 0, k, 1)])
    import_legacy_library(lib.db)
    assert _q(lib.db, "SELECT owner_profile_id FROM lib2_track_files") == [(None,)]
    assert {r[0] for r in _q(lib.db, "SELECT server_library_id FROM lib2_media_server_mappings")} \
        == {""}


def test_a_recovered_staging_album_is_repointed_where_the_import_reads_it(lib, monkeypatch):
    from core.downloads import atomic_recovery
    from core.library2 import migration_gate
    staged = os.path.join(lib.shared, ".soulsync_atomic_staging", "b1", "01.flac")
    final = os.path.join(lib.shared, "A", "B", "01.flac")
    _legacy(lib.db, tracks=[("1", "1", "1", "One", 1, staged, None, 0, None, 1)])
    monkeypatch.setattr(migration_gate, "migration_required", lambda db: True)
    atomic_recovery.make_db_path_updater(lib.db, count_is_evidence=False)(staged, final)
    assert _q(lib.db, "SELECT file_path FROM tracks") == [(final,)]


def test_the_reorganize_backlog_waits_for_the_import(lib, monkeypatch):
    import core.reorganize_queue as rq
    from core.library2 import migration_gate
    rows = {"q1": {"queue_id": "q1", "album_id": "200", "status": "queued", "enqueued_at": 1.0}}

    class Store:
        def load_pending(self):
            return [dict(v) for v in rows.values()]

        def save(self, snap):
            rows[snap["queue_id"]] = dict(snap)

        def delete(self, queue_id):
            rows.pop(queue_id, None)

    pending = {"value": True}
    monkeypatch.setattr(migration_gate, "migration_required", lambda db: pending["value"])
    monkeypatch.setattr(rq, "_DatabaseQueueStore", Store)
    rq.reset_queue_for_tests()
    try:
        queue = rq.get_queue()
        assert queue.snapshot()["totals"]["queued"] == 0
        pending["value"] = False
        migration_gate.start_deferred_workers(lib.db)
        assert queue.snapshot()["totals"]["queued"] == 1
    finally:
        rq.get_queue().stop()
        with rq._singleton_lock:
            rq._singleton = None
        library_scope.invalidate_library_scope_cache()
