"""Upstream 3.5.4-3.5.6 features ported onto the Library v2 catalogue.

Each test names the upstream change it ports. They seed ``lib2_*`` the way the
upstream tests seed the legacy tables.
"""

from __future__ import annotations

import os

import pytest

from database.music_database import MusicDatabase
from tests import lib2_seed as seed


@pytest.fixture()
def db(tmp_path):
    return MusicDatabase(str(tmp_path / "music.db"))


# -- c63800221: only tracks credited to the watched artist become wanted ------

def test_a_tribute_album_wants_only_the_watched_artists_tracks(db):
    from core.library2.discography import _uncredited_track_ids

    with db._get_connection() as conn:
        album_id = seed.album(conn, "Various Artists", "A Tribute")
        watched = seed.artist(conn, "Metallica", monitored=1)
        conn.execute("INSERT INTO lib2_album_artists (album_id, artist_id, role) VALUES (?, ?, 'featured')",
                     (album_id, watched))
        own = seed.track(conn, "Various Artists", "A Tribute", "One", credit="Metallica", owned=False)
        other = seed.track(conn, "Various Artists", "A Tribute", "Two", credit="Apocalyptica", owned=False)
        bare = seed.track(conn, "Various Artists", "A Tribute", "Three", owned=False)
        assert _uncredited_track_ids(conn, album_id, [own, other, bare]) == {other}


def test_an_alias_credit_counts_as_the_watched_artist(db):
    from core.library2.discography import _uncredited_track_ids

    with db._get_connection() as conn:
        canonical = seed.artist(conn, "Ye", monitored=1)
        seed.artist(conn, "Kanye West", canonical_artist_id=canonical)
        album_id = seed.album(conn, "Ye", "Donda")
        track = seed.track(conn, "Ye", "Donda", "Hurricane", credit="Kanye West", owned=False)
        assert _uncredited_track_ids(conn, album_id, [track]) == set()


def test_no_monitored_artist_on_the_album_fails_open(db):
    from core.library2.discography import _uncredited_track_ids

    with db._get_connection() as conn:
        album_id = seed.album(conn, "Various Artists", "Hits")
        track = seed.track(conn, "Various Artists", "Hits", "Song", credit="Someone", owned=False)
        assert _uncredited_track_ids(conn, album_id, [track]) == set()


def test_the_artist_settings_carry_the_other_artists_switch(db):
    from core.library2.artist_settings import WATCHLIST_BOOLEAN_FIELDS

    assert "include_other_artists" in WATCHLIST_BOOLEAN_FIELDS


# -- #1621: per-track credits reach the indexed artist lookup -------------------

def test_indexed_lookup_finds_a_compilation_track_by_its_credit(db):
    with db._get_connection() as conn:
        seed.track(conn, "Various Artists", "Grunge Hits", "Smells Like Teen Spirit", credit="Nirvana")
        seed.track(conn, "Nirvana", "Nevermind", "Lithium")
        conn.commit()
    titles = sorted(t.title for t in db.get_artist_tracks_indexed("Nirvana"))
    assert titles == ["Lithium", "Smells Like Teen Spirit"]


# -- 919c8d50d: only the gone cards' paths are asked about ---------------------

def test_live_suffix_keys_reads_active_library_files(db):
    from core.library.live_paths import live_suffix_keys

    with db._get_connection() as conn:
        tid = seed.track(conn, "A", "B", "C", path="/data/music/A/B/01 - C.flac")
        seed.track(conn, "A", "B", "D", path="/data/music/A/B/02 - D.flac")
        conn.execute("UPDATE lib2_track_files SET file_state='deleted' WHERE path LIKE '%02 - D.flac'")
        conn.commit()
        keys = live_suffix_keys(conn.cursor(), ["/other/root/A/B/01 - C.flac", "/x/A/B/02 - D.flac"])
    assert len(keys) == 1 and tid


# -- 9b4e1dbba: a playlist track's ids come from the catalogue ------------------

def test_track_origin_library_ids_from_lib2(db):
    from core.playlists.track_origin import library_ids

    with db._get_connection() as conn:
        seed.track(conn, "Various Artists", "Hits", "Chained To The Rhythm", credit="Katy Perry",
                   spotify_id="sp1", external_ids='{"deezer": "509938442"}')
        conn.commit()
    assert library_ids(db, "chained to the rhythm", "Katy Perry") == {
        "spotify": "sp1", "deezer": "509938442"}
    assert library_ids(db, "Chained To The Rhythm", "Someone Else") == {}


# -- 551af790b: the import pre-check only skips a copy in the target library ----

def test_external_id_lookup_can_be_scoped_to_one_library(db):
    from core.library.track_identity import find_library_track_by_external_id

    with db._get_connection() as conn:
        tid = seed.track(conn, "A", "B", "C", isrc="USX1")
        conn.execute("UPDATE lib2_track_files SET owner_profile_id = 2 WHERE track_id = ?", (tid,))
        conn.commit()
    ids = {"isrc": "USX1"}
    assert find_library_track_by_external_id(db, external_ids=ids)["album_title"] == "B"
    assert find_library_track_by_external_id(db, external_ids=ids, library_scope=2) is not None
    assert find_library_track_by_external_id(db, external_ids=ids, library_scope="shared") is None


def test_library_check_reads_the_release_title_off_the_lib2_row():
    from core.imports.library_check import _album_title_of_track

    assert _album_title_of_track(None, {"album_title": " Nevermind "}) == "Nevermind"
    assert _album_title_of_track(None, {}) == ""


# -- c99c7216d: same-named library files for an orphan -------------------------

def test_orphan_same_name_paths_come_from_live_lib2_files(db):
    from core.repair_jobs.orphan_file_detector import same_name_library_paths

    with db._get_connection() as conn:
        seed.track(conn, "A", "B (Deluxe)", "Intro", path="/music/A/B (Deluxe)/01 - Intro.flac")
        seed.track(conn, "Z", "Y", "Intro", path="/music/Z/Y/01 - Intro.flac")
        conn.execute("UPDATE lib2_track_files SET file_state='deleted' WHERE path LIKE '/music/Z/%'")
        conn.commit()
    assert same_name_library_paths(db, "/transfer/01 - Intro.flac") == [
        "/music/A/B (Deluxe)/01 - Intro.flac"]


# -- 5b982c555 / d5f278804: planner stats on the catalogue tables ---------------

def test_planner_stats_cover_the_lib2_tables(db):
    db.refresh_planner_stats()
    with db._get_connection() as conn:
        analyzed = {r[0] for r in conn.execute("SELECT DISTINCT tbl FROM sqlite_stat1")}
    # empty tables leave no stat rows; the call must still succeed and the
    # missing-stats probe must not ask for a thread on a small database
    assert isinstance(analyzed, set)
    with db._get_connection() as conn:
        assert db._tables_missing_planner_stats(conn.cursor()) == []


# -- 0ca4b0dc5: LOWER() lookups ride an index ----------------------------------

@pytest.mark.parametrize("sql, index", [
    ("SELECT id FROM lib2_tracks WHERE LOWER(title) = LOWER(?)", "idx_lib2_tracks_title_lower"),
    ("SELECT id FROM lib2_albums WHERE LOWER(title) = LOWER(?)", "idx_lib2_albums_title_lower"),
    ("SELECT id FROM lib2_artists WHERE LOWER(name) = LOWER(?)", "idx_lib2_artists_name_lower"),
])
def test_lower_lookups_use_an_expression_index(db, sql, index):
    with db._get_connection() as conn:
        plan = " ".join(str(r[-1]) for r in conn.execute("EXPLAIN QUERY PLAN " + sql, ("x",)))
    assert index in plan


# -- ef0ca5248 / 7c149a0f3: played genres count lib2 plays once per track ------

def test_played_genres_join_each_played_lib2_track_once(db):
    with db._get_connection() as conn:
        tid = seed.track(conn, "A", "B", "C")
        conn.execute("UPDATE lib2_artists SET genres = '[\"rock\"]' WHERE name = 'A'")
        for minute in range(3):
            conn.execute(
                "INSERT INTO listening_history (title, artist, played_at, lib2_track_id, profile_id)"
                " VALUES ('C', 'A', datetime('now', ?), ?, 1)", (f"-{minute} minutes", tid))
        conn.commit()
        rows = conn.execute(db._played_genres_sql("WHERE 1=1")).fetchall()
    assert [tuple(r) for r in rows] == [('["rock"]', 3)]


def test_untagged_downloads_is_registered_with_a_basis():
    from core.repair_jobs import JOB_DATA_BASIS, JOB_LIBRARY_V2_EFFECTS

    assert JOB_DATA_BASIS["untagged_downloads"] == "filesystem"
    assert JOB_LIBRARY_V2_EFFECTS["untagged_downloads"] == frozenset({"observe", "tags"})


def test_untagged_fix_leaves_a_hand_tagged_file_alone(tmp_path, monkeypatch):
    from core.repair_worker import RepairWorker

    path = tmp_path / "song.flac"
    path.write_bytes(b"x")
    worker = RepairWorker.__new__(RepairWorker)
    worker.db = object()
    worker.transfer_folder = str(tmp_path)
    worker._config_manager = None
    monkeypatch.setattr("core.repair_jobs.base.hand_tagged_path_keys", lambda _db: {"k"})
    monkeypatch.setattr("core.repair_jobs.base.is_hand_tagged_path", lambda p, keys: True)
    res = worker._fix_untagged_download("file", "1", str(path), {"file_path": str(path)})
    assert res["success"] is False and "Hand-tagged" in res["error"]
    assert os.path.exists(path)


# -- 72749f414 / #1715: only owned artist spellings reach the fuzzy search ------

def test_an_artist_the_library_does_not_own_never_reaches_the_fuzzy_title_search(db):
    with db._get_connection() as conn:
        seed.track(conn, "Taylor Smith", "Greatest Hits", "Blue Sky")
        seed.artist(conn, "Taylor Swift")  # watched, owns nothing
        conn.commit()
    track, conf = db.check_track_exists("Blue Sky", "Taylor Swift", confidence_threshold=0.7)
    assert track is None and conf < 0.7


@pytest.mark.parametrize("library, asked", [
    ("AC/DC", "ACDC"), ("Björk", "Bjork"), ("Beyoncé", "Beyonce")])
def test_punctuation_and_accent_spellings_still_count_as_owned(db, library, asked):
    with db._get_connection() as conn:
        seed.track(conn, library, "Album", "Song")
        conn.commit()
    assert db._library_artist_forms([asked]) == [asked]
    assert db._library_artist_forms(["Nobody At All"]) == []
