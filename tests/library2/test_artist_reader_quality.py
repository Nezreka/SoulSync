"""Artist filters evaluate native, scoped file facts against live profiles."""

import json
import sqlite3

import pytest

from core.library2 import queries
from core.library2.artist_reader import forget_upgradable_counts
from core.library2.schema import ensure_library_v2_schema
from database.music_database import MusicDatabase
from tests.lib2_seed import RowDb, artist, row_conn, track


class ReaderDatabase(RowDb):
    _api_project_lib2 = staticmethod(MusicDatabase._api_project_lib2)


@pytest.fixture
def catalogue(tmp_path):
    path = str(tmp_path / "artists.db")
    conn = row_conn(path)
    ensure_library_v2_schema(conn)
    conn.execute("CREATE TABLE watchlist_artists(spotify_artist_id TEXT, itunes_artist_id TEXT, artist_name TEXT, profile_id INTEGER)")
    yield conn, ReaderDatabase(path)
    conn.close()


def _seed(conn, name, quality, *, policy="until_cutoff", cutoff=1):
    profile = conn.execute(
        "INSERT INTO quality_profiles(name,ranked_targets,upgrade_policy,upgrade_cutoff_index) VALUES(?,?,?,?)",
        (name, json.dumps([
            {"format": "flac", "bit_depth": 24, "min_sample_rate": 96000},
            {"format": "flac", "bit_depth": 16, "min_sample_rate": 44100},
            {"format": "mp3", "min_bitrate": 320},
        ]), policy, cutoff),
    ).lastrowid
    tid = track(conn, name, name + " album", name + " song", credit=name)
    aid = artist(conn, name, legacy_artist_id=1000 + tid,
                 spotify_id="sp-" + name, quality_profile_id=profile,
                 quality_profile_explicit=1)
    fmt, depth, rate, bitrate = quality
    conn.execute("UPDATE lib2_track_files SET format=?,bit_depth=?,sample_rate=?,bitrate=? WHERE track_id=?",
                 (fmt, depth, rate, bitrate, tid))
    return aid, tid


def test_public_quality_filter_uses_cutoff_and_pages_filtered_total(catalogue):
    conn, db = catalogue
    _seed(conn, "A below", ("mp3", None, 44100, 320))
    _seed(conn, "B at cutoff", ("flac", 16, 44100, 900))
    _seed(conn, "C above", ("flac", 24, 96000, 2500))
    _seed(conn, "D below", ("mp3", None, 44100, 128))
    conn.commit()
    page = MusicDatabase.get_library_artists(db, quality_filter="upgradable", limit=1, page=2)
    assert [a["name"] for a in page["artists"]] == ["D below"]
    assert page["pagination"] == {"page": 2, "limit": 1, "total_count": 2,
                                  "total_pages": 2, "has_prev": True, "has_next": False}


@pytest.mark.parametrize("policy,cutoff,expected", [
    ("none", 0, []), ("acceptable", 0, ["Outside"]),
    ("until_top", 2, ["Accepted", "Outside"]),
    ("until_cutoff", 1, ["Outside"]),
])
def test_upgrade_modes_agree_across_artist_surfaces(catalogue, policy, cutoff, expected):
    conn, db = catalogue
    _seed(conn, "Accepted", ("flac", 16, 44100, 900), policy=policy, cutoff=cutoff)
    _seed(conn, "Outside", ("mp3", None, 44100, 128), policy=policy, cutoff=cutoff)
    conn.commit()
    public = MusicDatabase.get_library_artists(db, quality_filter="upgradable")
    assert [a["name"] for a in public["artists"]] == expected
    native, total = queries.list_artists(conn, quality_filter="upgradable")
    compatibility = queries.legacy_api_artists_page(conn, quality_filter="upgradable")
    assert [a["name"] for a in native] == expected
    assert [a["name"] for a in compatibility["artists"]] == expected
    assert total == compatibility["pagination"]["total_count"] == len(expected)


def test_combined_provider_watchlist_and_quality_filters(catalogue):
    conn, db = catalogue
    _seed(conn, "A low", ("mp3", None, 44100, 128))
    _seed(conn, "B low", ("mp3", None, 44100, 128))
    _seed(conn, "C cutoff", ("flac", 16, 44100, 900))
    conn.execute("INSERT INTO watchlist_artists VALUES('sp-A low',NULL,'old name',7)")
    conn.execute("INSERT INTO watchlist_artists VALUES('sp-C cutoff',NULL,'old name',7)")
    conn.commit()
    public = MusicDatabase.get_library_artists(db, quality_filter="upgradable", profile_id=7,
                                               watchlist_filter="watched", source_filter="spotify")
    assert [a["name"] for a in public["artists"]] == ["A low"]
    assert public["artists"][0]["is_watched"] is True
    assert public["pagination"]["total_count"] == 1


def test_fileless_unknown_and_deleted_quality_are_not_upgrade_evidence(catalogue):
    conn, db = catalogue
    for name, state in [("Deleted", "deleted"), ("Missing", "missing_confirmed"), ("Unknown", "active")]:
        aid, tid = _seed(conn, name, ("unknown", None, None, None))
        conn.execute("UPDATE lib2_artists SET monitored=1 WHERE id=?", (aid,))
        conn.execute("UPDATE lib2_track_files SET file_state=? WHERE track_id=?", (state, tid))
    conn.commit()
    public = MusicDatabase.get_library_artists(db, quality_filter="upgradable")
    assert public["artists"] == []
    assert public["pagination"]["total_count"] == 0


def test_quality_filter_evaluates_the_selected_librarys_copy(catalogue, monkeypatch):
    from core import library_scope

    conn, db = catalogue
    aid, tid = _seed(conn, "Same track", ("flac", 24, 96000, 2500))
    conn.execute("INSERT INTO lib2_track_files(track_id,path,format,bitrate,owner_profile_id) VALUES(?,?,'mp3',128,7)",
                 (tid, "/temporary/own.mp3"))
    conn.execute("UPDATE lib2_track_files SET owner_profile_id=7 WHERE path='/temporary/own.mp3'")
    conn.commit()
    monkeypatch.setattr(library_scope, "any_own_library_exists", lambda: True)
    token = library_scope.set_library_scope(7)
    try:
        public = MusicDatabase.get_library_artists(db, quality_filter="upgradable")
        assert [a["id"] for a in public["artists"]] == [aid]
        native, total = queries.list_artists(conn, quality_filter="upgradable")
        assert [a["id"] for a in native] == [aid]
        assert total == 1
    finally:
        library_scope.reset_library_scope(token)
    token = library_scope.set_library_scope("shared")
    try:
        public = MusicDatabase.get_library_artists(db, quality_filter="upgradable")
        assert public["artists"] == []
        assert public["pagination"]["total_count"] == 0
    finally:
        library_scope.reset_library_scope(token)


def test_explicit_track_and_album_profiles_override_artist(catalogue):
    conn, db = catalogue
    _, tid = _seed(conn, "Inherited low", ("mp3", None, 44100, 128))
    disabled = conn.execute("INSERT INTO quality_profiles(name,upgrade_policy) VALUES('Off','none')").lastrowid
    album_id = conn.execute("SELECT album_id FROM lib2_tracks WHERE id=?", (tid,)).fetchone()[0]
    conn.execute("UPDATE lib2_albums SET quality_profile_id=?,quality_profile_explicit=1 WHERE id=?", (disabled, album_id))
    conn.commit()
    assert MusicDatabase.get_library_artists(db, quality_filter="upgradable")["artists"] == []
    upgrade = conn.execute("SELECT quality_profile_id FROM lib2_artists WHERE name='Inherited low'").fetchone()[0]
    conn.execute("UPDATE lib2_tracks SET quality_profile_id=?,quality_profile_explicit=1 WHERE id=?", (upgrade, tid))
    conn.commit()
    # a raw write: assign_quality_profile forgets the cached counts itself
    forget_upgradable_counts()
    assert [a["name"] for a in MusicDatabase.get_library_artists(db, quality_filter="upgradable")["artists"]] == ["Inherited low"]


def test_artist_display_preserves_manual_name_without_changing_watchlist_identity(catalogue):
    from core.library2.metadata_overrides import set_field_override

    conn, db = catalogue
    aid, _ = _seed(conn, "Original", ("mp3", None, 44100, 128))
    conn.execute("INSERT INTO watchlist_artists VALUES(NULL,NULL,'Original',7)")
    set_field_override(conn, entity_type="artist", entity_id=aid, field_name="name", value="My Name")
    conn.commit()
    public = MusicDatabase.get_library_artists(db, watchlist_filter="watched", profile_id=7)
    native, _ = queries.list_artists(conn, watchlist_filter="watched", profile_id=7)
    historical = queries.legacy_api_artists_page(conn, watchlist_filter="watched", profile_id=7)
    assert public["artists"][0]["name"] == native[0]["name"] == historical["artists"][0]["name"] == "My Name"
    assert public["artists"][0]["is_watched"] is True
    assert historical["artists"][0]["is_watched"] is True


def test_watchlist_report_uses_the_same_sql_membership_as_the_filter(catalogue):
    conn, db = catalogue
    _seed(conn, "Étoiles", ("mp3", None, 44100, 128))
    conn.execute("INSERT INTO watchlist_artists VALUES(NULL,NULL,'Étoiles',7)")
    conn.commit()
    public = MusicDatabase.get_library_artists(db, watchlist_filter="watched", profile_id=7)
    assert [a["name"] for a in public["artists"]] == ["Étoiles"]
    assert public["artists"][0]["is_watched"] is True


def test_alias_owned_upgrade_is_counted_under_the_canonical_artist(catalogue):
    conn, db = catalogue
    canonical = artist(conn, "Canonical", legacy_artist_id=9000)
    alias, _ = _seed(conn, "Alias", ("mp3", None, 44100, 128))
    conn.execute("UPDATE lib2_artists SET canonical_artist_id=? WHERE id=?", (canonical, alias))
    conn.commit()
    public = MusicDatabase.get_library_artists(db, quality_filter="upgradable", search_query="Alias")
    native, total = queries.list_artists(conn, quality_filter="upgradable", search="Alias")
    compatibility = queries.legacy_api_artists_page(conn, quality_filter="upgradable", search_query="Alias")
    assert [a["id"] for a in public["artists"]] == [canonical]
    assert [a["id"] for a in native] == [canonical]
    assert [a["lib2_artist_id"] for a in compatibility["artists"]] == [canonical]
    assert total == public["pagination"]["total_count"] == compatibility["pagination"]["total_count"] == 1
    assert public["artists"][0]["track_count"] == native[0]["track_count"] == compatibility["artists"][0]["track_count"] == 1


def test_intentionally_retained_quality_does_not_repeat_the_same_upgrade(catalogue):
    conn, db = catalogue
    _, tid = _seed(conn, "Retained MP3", ("mp3", None, 44100, 320), policy="until_top")
    conn.execute("UPDATE lib2_track_files SET acquired_quality_json=?,retention_json=? WHERE track_id=?",
                 (json.dumps({"format": "flac", "bit_depth": 24, "sample_rate": 96000, "bitrate": 2500}),
                  json.dumps([{"source_replaced": True, "action": "lossy_retention"}]), tid))
    conn.commit()
    assert MusicDatabase.get_library_artists(db, quality_filter="upgradable")["artists"] == []


def test_global_profile_is_used_when_no_entity_owns_an_assignment(catalogue):
    conn, db = catalogue
    aid, tid = _seed(conn, "Global", ("mp3", None, 44100, 128))
    upgrade_profile = conn.execute("SELECT quality_profile_id FROM lib2_artists WHERE id=?", (aid,)).fetchone()[0]
    conn.execute("UPDATE quality_profiles SET is_default=0")
    conn.execute("UPDATE quality_profiles SET is_default=1 WHERE id=?", (upgrade_profile,))
    conn.execute("UPDATE lib2_artists SET quality_profile_explicit=0 WHERE id=?", (aid,))
    conn.commit()
    assert [a["name"] for a in MusicDatabase.get_library_artists(db, quality_filter="upgradable")["artists"]] == ["Global"]
    conn.execute("UPDATE quality_profiles SET upgrade_policy='none' WHERE id=?", (upgrade_profile,))
    conn.commit()
    assert MusicDatabase.get_library_artists(db, quality_filter="upgradable")["artists"] == []


def test_source_filter_preserves_other_native_provider_namespaces(catalogue):
    conn, db = catalogue
    aid, _ = _seed(conn, "Bandcamp", ("mp3", None, 44100, 128))
    _seed(conn, "Other", ("mp3", None, 44100, 128))
    conn.execute("UPDATE lib2_artists SET external_ids=? WHERE id=?", (json.dumps({"bandcamp": "artist-url"}), aid))
    conn.commit()
    page = MusicDatabase.get_library_artists(db, source_filter="bandcamp", quality_filter="upgradable")
    assert [a["name"] for a in page["artists"]] == ["Bandcamp"]
    negative = MusicDatabase.get_library_artists(db, source_filter="!bandcamp", quality_filter="upgradable")
    assert [a["name"] for a in negative["artists"]] == ["Other"]


def test_could_be_better_is_kept_and_an_assignment_lands_at_once(catalogue):
    # the filter graded every file in the library on every click (26s, timed
    # out). it's cached now, and what the user decides still shows at once
    from core.library2.profile_lookup import assign_quality_profile

    conn, db = catalogue
    aid, tid = _seed(conn, "Low", ("mp3", None, 44100, 128))
    conn.commit()
    names = lambda: [a["name"] for a in MusicDatabase.get_library_artists(  # noqa: E731
        db, quality_filter="upgradable")["artists"]]
    assert names() == ["Low"]
    # a file change on its own waits for the background recheck
    conn.execute("UPDATE lib2_track_files SET format='flac',bit_depth=24,sample_rate=96000 WHERE track_id=?", (tid,))
    conn.commit()
    assert names() == ["Low"]
    conn.execute("UPDATE lib2_track_files SET format='mp3',bit_depth=NULL,sample_rate=44100 WHERE track_id=?", (tid,))
    off = conn.execute("INSERT INTO quality_profiles(name,upgrade_policy) VALUES('Off','none')").lastrowid
    conn.commit()
    assert names() == ["Low"]
    assign_quality_profile(conn, "artists", aid, off)
    conn.commit()
    assert names() == []
    # a profile's own settings are read every time
    conn.execute("UPDATE quality_profiles SET upgrade_policy='until_cutoff', ranked_targets=? WHERE id=?",
                 (json.dumps([{"format": "flac", "bit_depth": 16, "min_sample_rate": 44100}]), off))
    conn.commit()
    assert names() == ["Low"]



def test_concurrent_cold_filter_clicks_count_the_library_once(catalogue, monkeypatch):
    # every click before the first cold count finished started its own ~40s
    # count; under load one request took 21 minutes (Nov 3 2026)
    import sqlite3
    import threading
    import time

    from core.library2 import artist_reader

    calls = []

    def slow_count(conn, predicate, params, owner):
        calls.append(1)
        time.sleep(0.3)
        return {1: 2}

    monkeypatch.setattr(artist_reader, "_count_upgradable", slow_count)
    conn, _ = catalogue
    conn.commit()
    path = conn.execute("PRAGMA database_list").fetchone()[2]
    results = []

    def click():
        conn = sqlite3.connect(path)
        try:
            results.append(artist_reader.library_upgradable_counts(conn))
        finally:
            conn.close()

    threads = [threading.Thread(target=click) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(calls) == 1
    assert results == [{1: 2}] * 4


# sqlite_stat1 rows from a 300k-track library (review 5 smoke copy): the plan
# a real library gets, which a tiny test catalogue alone never shows
_BIG_LIBRARY_STATS = (
    ("lib2_albums", "idx_lib2_albums_artist", "71129 14"),
    ("lib2_album_artists", "idx_lib2_album_artists_artist", "131701 19"),
    ("lib2_album_artists", "sqlite_autoindex_lib2_album_artists_1", "131701 2 1"),
    ("lib2_artists", "idx_lib2_artists_canonical", "28919 335"),
    ("lib2_track_artists", "idx_lib2_track_artists_artist", "423965 72"),
    ("lib2_track_artists", "sqlite_autoindex_lib2_track_artists_1", "423965 2 1"),
    ("lib2_track_files", "idx_lib2_track_files_primary", "310791 1 1"),
    ("lib2_track_files", "idx_lib2_track_files_track", "310791 1"),
    ("lib2_wanted_tracks", "sqlite_autoindex_lib2_wanted_tracks_1", "310671 1001 1"),
    ("lib2_monitor_rules", "sqlite_autoindex_lib2_monitor_rules_1", "410729 1001 1 1"),
)


def test_artist_page_counts_albums_by_seeking_the_page_albums(catalogue, monkeypatch):
    """review 5 perf: album_stats joined artist_albums to lib2_albums with a
    plain JOIN, and on a big library the planner scanned every album against
    the page's few (half of /api/library/artists). it seeks them now."""
    conn, db = catalogue
    _seed(conn, "A", ("flac", 16, 44100, 900))
    _seed(conn, "B", ("mp3", None, 44100, 320))
    conn.execute("ANALYZE")
    names = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='index'")}
    conn.execute("DELETE FROM sqlite_stat1")
    conn.executemany("INSERT INTO sqlite_stat1(tbl, idx, stat) VALUES (?,?,?)",
                     [s for s in _BIG_LIBRARY_STATS if s[1] in names])
    conn.commit()

    seen = []
    traced = sqlite3.connect(db.path)
    traced.row_factory = sqlite3.Row
    traced.set_trace_callback(seen.append)
    monkeypatch.setattr(db, "_get_connection", lambda: traced)
    page = MusicDatabase.get_library_artists(db, limit=10)
    assert [a["name"] for a in page["artists"]] == ["A", "B"]
    assert all(a["album_count"] == 1 for a in page["artists"])

    query = next(s for s in seen if "album_stats AS" in s)
    plan = [r[3] for r in traced.execute("EXPLAIN QUERY PLAN " + query)]
    assert "SCAN al" not in plan, plan
    assert any(p.startswith("SEARCH al USING INTEGER PRIMARY KEY") for p in plan), plan
