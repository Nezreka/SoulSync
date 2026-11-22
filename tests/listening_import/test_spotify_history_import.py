"""spotify streaming history from the privacy export.

the browser parses the export (webui spotify-history.ts) and sends rows of one
shape. these tests feed those rows to the real MusicDatabase on a tmp file:
what is kept, what is dropped, the pile it lands in, the dedupe against the
same plays from last.fm / listenbrainz, the taste-only switch and the undo.
"""

from __future__ import annotations

import pytest

from core.listening_import import spotify_export
from core.listening_import.dedup import insert_import_events
from core.listening_scope import (
    SHARED_OWNER,
    listening_owner,
    stats_view,
)
from database.music_database import MusicDatabase
from tests import lib2_seed

URI = "spotify:track:4uLU6hMCjMI75M1A2tKUQC"
URI2 = "spotify:track:7ouMYWpwJ422jRcDASZB7P"


@pytest.fixture()
def db(tmp_path):
    return MusicDatabase(str(tmp_path / "music.db"))


def _ext(ended_at, track, artist, ms=200_000, uri=URI, album="Album", **flags):
    """an extended-history play the way the browser sends it"""
    return {"ended_at": ended_at, "ms_played": ms, "track": track, "artist": artist,
            "album": album, "uri": uri, **flags}


def _acct(end_time, track, artist, ms=200_000):
    """an account-data play: minute precision, no id"""
    return {"ended_at": end_time, "ms_played": ms, "track": track, "artist": artist,
            "coarse": True}


def _rows(db, sql, args=()):
    conn = db._get_connection()
    try:
        return [tuple(r) for r in conn.execute(sql, args).fetchall()]
    finally:
        conn.close()


def _spotify_rows(db):
    return _rows(db, "SELECT title, artist, played_at, duration_ms, track_id, profile_id, "
                     "scrobble_eligible FROM listening_history WHERE server_source = 'spotify_export' "
                     "ORDER BY played_at")


def _lastfm(db, title, artist, played_at, owner=SHARED_OWNER, album="Album"):
    insert_import_events(db, [{"track_id": None, "title": title, "artist": artist,
                               "album": album, "played_at": played_at,
                               "scrobble_eligible": 1}], "lastfm", profile_id=owner)


# -- rows ---------------------------------------------------------------------

def test_a_kept_play_is_stored_at_its_start_with_its_track_id(db):
    result = spotify_export.import_batch(
        db, [_ext("2024-01-15T08:23:45Z", "Song", "Band", ms=180_000)], SHARED_OWNER)

    assert result["kept"] == 1 and result["added"] == 1
    # ended 08:23:45 after three minutes: started 08:20:45
    assert _spotify_rows(db) == [
        ("Song", "Band", "2024-01-15 08:20:45", 180_000, "4uLU6hMCjMI75M1A2tKUQC", SHARED_OWNER, 0)]


def test_short_podcast_and_private_plays_never_land(db):
    result = spotify_export.import_batch(db, [
        _ext("2024-01-15T08:00:00Z", "Skipped", "Band", ms=29_999),
        _ext("2024-01-15T09:00:00Z", "Episode", "Show", uri="spotify:episode:abc"),
        _ext("2024-01-15T10:00:00Z", "Episode 2", "Show", uri="", episode=True),
        _ext("2024-01-15T11:00:00Z", "Secret", "Band", incognito=True),
        _ext("not a date", "Broken", "Band"),
        _ext("2024-01-15T12:00:00Z", "", "Band"),
        _ext("2024-01-15T13:00:00Z", "Kept", "Band", ms=30_000),
    ], SHARED_OWNER)

    assert result["dropped"] == {"short": 1, "podcast": 2, "private": 1, "invalid": 2}
    assert [r[0] for r in _spotify_rows(db)] == ["Kept"]


def test_account_data_rows_are_kept_at_minute_precision(db):
    spotify_export.import_batch(db, [_acct("2019-03-02 21:14", "Old Song", "Band", ms=120_000)],
                                SHARED_OWNER)

    assert _spotify_rows(db) == [("Old Song", "Band", "2019-03-02 21:12:00", 120_000, None,
                                  SHARED_OWNER, 0)]


def test_the_same_export_twice_adds_nothing_the_second_time(db):
    rows = [_ext("2024-01-15T08:23:45Z", "Song", "Band"),
            _ext("2024-01-15T09:23:45Z", "Song", "Band"),
            _acct("2019-03-02 21:14", "Old Song", "Band")]
    first = spotify_export.import_batch(db, rows, SHARED_OWNER)
    second = spotify_export.import_batch(db, rows, SHARED_OWNER)

    assert first["added"] == 3
    assert second["added"] == 0 and second["merged"] == 3
    assert len(_spotify_rows(db)) == 3


def test_a_repeat_listen_is_its_own_play(db):
    """the same song twice in a row is two plays, not a duplicate"""
    spotify_export.import_batch(db, [_ext("2024-01-15T08:03:20Z", "Song", "Band"),
                                     _ext("2024-01-15T08:06:40Z", "Song", "Band")], SHARED_OWNER)

    assert len(_spotify_rows(db)) == 2


# -- dedupe against the other imports ------------------------------------------

def test_a_play_last_fm_already_has_is_linked_not_doubled(db):
    # spotify scrobbled it at its start, 08:20:45
    _lastfm(db, "Song", "Band", "2024-01-15 08:20:45")
    result = spotify_export.import_batch(
        db, [_ext("2024-01-15T08:23:50Z", "Song", "Band", ms=180_000)], SHARED_OWNER)

    assert result["added"] == 0 and result["merged"] == 1
    assert _rows(db, "SELECT server_source FROM listening_history") == [("lastfm",)]
    summary = spotify_export.summary(db, SHARED_OWNER)
    assert summary["linked"] == 1 and summary["plays"] == 0


def test_a_minute_precision_play_still_finds_its_scrobble(db):
    _lastfm(db, "Old Song", "Band", "2019-03-02 21:12:40")
    result = spotify_export.import_batch(db, [_acct("2019-03-02 21:14", "Old Song", "Band",
                                                    ms=120_000)], SHARED_OWNER)

    assert result["added"] == 0


def test_a_play_minutes_away_from_the_scrobble_is_a_different_play(db):
    _lastfm(db, "Song", "Band", "2024-01-15 08:00:00")
    result = spotify_export.import_batch(
        db, [_ext("2024-01-15T08:23:45Z", "Song", "Band", ms=180_000)], SHARED_OWNER)

    assert result["added"] == 1


def test_dedupe_never_reaches_into_another_profiles_pile(db):
    kim = db.create_profile(name="kim")
    _lastfm(db, "Song", "Band", "2024-01-15 08:20:45", owner=SHARED_OWNER)
    result = spotify_export.import_batch(
        db, [_ext("2024-01-15T08:23:45Z", "Song", "Band", ms=180_000)], kim)

    assert result["added"] == 1


# -- whose pile ---------------------------------------------------------------

def test_a_members_import_is_their_own_pile_never_the_shared_one(db):
    kim = db.create_profile(name="kim")
    assert listening_owner(db, kim) == SHARED_OWNER

    spotify_export.import_batch(db, [_ext("2024-01-15T08:23:45Z", "Song", "Band")], kim)

    assert listening_owner(db, kim) == kim
    assert [r[5] for r in _spotify_rows(db)] == [kim]
    assert db.get_top_artists("all", 10, profile_id=SHARED_OWNER) == []
    assert [a["name"] for a in db.get_top_artists("all", 10, profile_id=kim)] == ["Band"]


def test_the_admins_import_is_the_shared_history(db):
    spotify_export.import_batch(db, [_ext("2024-01-15T08:23:45Z", "Song", "Band")], SHARED_OWNER)

    assert [r[5] for r in _spotify_rows(db)] == [SHARED_OWNER]


# -- library match ------------------------------------------------------------

def test_the_spotify_id_matches_exactly_then_title_and_artist(db):
    conn = db._get_connection()
    try:
        by_id = lib2_seed.track(conn, "Band", "Album", "Song (Remastered 2011)",
                                spotify_id="4uLU6hMCjMI75M1A2tKUQC")
        by_name = lib2_seed.track(conn, "Band", "Album", "Other")
        conn.commit()
    finally:
        conn.close()

    spotify_export.import_batch(db, [
        _ext("2024-01-15T08:23:45Z", "Song", "Band"),
        _ext("2024-01-15T09:23:45Z", "Other", "Band", uri=URI2),
        _ext("2024-01-15T10:23:45Z", "Unknown", "Band", uri=""),
    ], SHARED_OWNER)

    linked = dict(_rows(db, "SELECT title, lib2_track_id FROM listening_history"))
    assert linked == {"Song": by_id, "Other": by_name, "Unknown": None}
    assert spotify_export.summary(db, SHARED_OWNER)["matched"] == 2


# -- stats or taste only -------------------------------------------------------

def test_taste_only_keeps_the_import_out_of_stats_but_not_out_of_taste(db):
    spotify_export.import_batch(db, [_ext("2024-01-15T08:23:45Z", "Song", "Spotify Band")],
                                SHARED_OWNER, include_in_stats=False)
    insert_import_events(db, [{"track_id": "p1", "title": "Live", "artist": "Plex Band",
                               "played_at": "2024-01-16 08:00:00", "scrobble_eligible": 1}],
                         "plex", profile_id=SHARED_OWNER)

    # taste readers (seeds, mixes) see everything
    taste = [a["name"] for a in db.get_top_artists("all", 10, profile_id=SHARED_OWNER)]
    assert sorted(taste) == ["Plex Band", "Spotify Band"]
    # stats leave the taste-only plays out
    with stats_view(db):
        stats = [a["name"] for a in db.get_top_artists("all", 10, profile_id=SHARED_OWNER)]
        overview = db.get_listening_stats("all", profile_id=SHARED_OWNER)
        year = db.get_top_tracks("all", 10, profile_id=SHARED_OWNER)
    assert stats == ["Plex Band"]
    assert overview["total_plays"] == 1
    assert [t["name"] for t in year] == ["Live"]

    # switched back on, stats count it again
    assert spotify_export.set_include_in_stats(db, SHARED_OWNER, True)
    with stats_view(db):
        stats = [a["name"] for a in db.get_top_artists("all", 10, profile_id=SHARED_OWNER)]
    assert sorted(stats) == ["Plex Band", "Spotify Band"]


def test_taste_only_is_per_pile(db):
    kim = db.create_profile(name="kim")
    spotify_export.import_batch(db, [_ext("2024-01-15T08:23:45Z", "Song", "Kim Band")], kim,
                                include_in_stats=True)
    spotify_export.import_batch(db, [_ext("2024-01-15T08:23:45Z", "Song", "Admin Band")],
                                SHARED_OWNER, include_in_stats=False)

    with stats_view(db):
        assert [a["name"] for a in db.get_top_artists("all", 10, profile_id=kim)] == ["Kim Band"]
        assert db.get_top_artists("all", 10, profile_id=SHARED_OWNER) == []


def test_the_year_story_leaves_taste_only_plays_out(db):
    from datetime import datetime, timedelta, timezone
    recent = (datetime.now(timezone.utc) - timedelta(days=3)).strftime("%Y-%m-%dT%H:%M:%SZ")
    spotify_export.import_batch(db, [_ext(recent, "Song", "Band")], SHARED_OWNER,
                                include_in_stats=False)

    assert db.get_year_in_listening(profile_id=SHARED_OWNER)["has_data"] is True
    with stats_view(db):
        assert db.get_year_in_listening(profile_id=SHARED_OWNER)["has_data"] is False


# -- undo ---------------------------------------------------------------------

def test_removing_the_import_takes_its_plays_and_keeps_everything_else(db):
    _lastfm(db, "Song", "Band", "2024-01-15 08:20:45")
    spotify_export.import_batch(db, [
        _ext("2024-01-15T08:23:45Z", "Song", "Band", ms=180_000),   # linked to last.fm
        _ext("2024-02-01T10:00:00Z", "New", "Band"),                # added
    ], SHARED_OWNER)

    removed = spotify_export.remove_import(db, SHARED_OWNER)

    assert removed == 1
    assert _rows(db, "SELECT title, server_source FROM listening_history") == [("Song", "lastfm")]
    assert _rows(db, "SELECT COUNT(*) FROM listening_import_events WHERE source = 'spotify_export'") == [(0,)]
    assert spotify_export.summary(db, SHARED_OWNER)["imported"] is False
    # and it can come in again afterwards
    again = spotify_export.import_batch(db, [_ext("2024-02-01T10:00:00Z", "New", "Band")],
                                        SHARED_OWNER)
    assert again["added"] == 1


def test_removing_a_members_only_history_hands_the_pile_back(db):
    kim = db.create_profile(name="kim")
    spotify_export.import_batch(db, [_ext("2024-01-15T08:23:45Z", "Song", "Band")], kim)
    assert listening_owner(db, kim) == kim

    spotify_export.remove_import(db, kim)

    assert listening_owner(db, kim) == SHARED_OWNER


def test_removing_keeps_the_pile_when_it_has_other_plays(db):
    kim = db.create_profile(name="kim")
    spotify_export.import_batch(db, [_ext("2024-01-15T08:23:45Z", "Song", "Band")], kim)
    insert_import_events(db, [{"track_id": "w1", "title": "Web", "artist": "Band",
                               "played_at": "2024-03-01 08:00:00"}], "soulsync", profile_id=kim)

    spotify_export.remove_import(db, kim)

    assert listening_owner(db, kim) == kim
    assert _rows(db, "SELECT title FROM listening_history WHERE profile_id = ?", (kim,)) == [("Web",)]


def test_one_profiles_undo_never_touches_another_pile(db):
    kim = db.create_profile(name="kim")
    spotify_export.import_batch(db, [_ext("2024-01-15T08:23:45Z", "Song", "Band")], kim)
    spotify_export.import_batch(db, [_ext("2024-01-15T08:23:45Z", "Song", "Band")], SHARED_OWNER)

    spotify_export.remove_import(db, kim)

    assert [r[5] for r in _spotify_rows(db)] == [SHARED_OWNER]


# -- summary ------------------------------------------------------------------

def test_the_summary_says_what_came_in(db):
    spotify_export.import_batch(db, [
        _ext("2015-06-01T10:00:00Z", "A", "One"),
        _ext("2020-06-01T10:00:00Z", "B", "Two"),
        _ext("2024-06-01T10:00:00Z", "C", "Two"),
    ], SHARED_OWNER)

    s = spotify_export.summary(db, SHARED_OWNER)
    assert s["imported"] is True and s["include_in_stats"] is True
    assert s["plays"] == 3 and s["artists"] == 2
    assert s["first_played"].startswith("2015-06-01")
    assert s["last_played"].startswith("2024-06-01")


def test_a_deleted_profile_takes_its_import_record_along(db):
    kim = db.create_profile(name="kim")
    spotify_export.import_batch(db, [_ext("2024-01-15T08:23:45Z", "Song", "Band")], kim)

    assert db.delete_profile(kim)

    assert _rows(db, "SELECT COUNT(*) FROM spotify_history_imports") == [(0,)]
    assert _spotify_rows(db) == []


def test_the_stats_cache_leaves_taste_only_plays_out(db):
    """the stats page reads a cache the worker builds: built as stats"""
    import json

    from core.listening_stats_worker import ListeningStatsWorker

    spotify_export.import_batch(db, [_ext("2024-01-15T08:23:45Z", "Song", "Spotify Band")],
                                SHARED_OWNER, include_in_stats=False)
    insert_import_events(db, [{"track_id": "p1", "title": "Live", "artist": "Plex Band",
                               "played_at": "2024-01-16 08:00:00", "scrobble_eligible": 1}],
                         "plex", profile_id=SHARED_OWNER)
    worker = ListeningStatsWorker(db, None)
    worker._enrich_stats_items = lambda cache: None

    worker._build_stats_cache(owners=[SHARED_OWNER])

    cache = json.loads(_rows(db, "SELECT value FROM metadata WHERE key = 'stats_cache_all'")[0][0])
    assert [a["name"] for a in cache["top_artists"]] == ["Plex Band"]
    recent = json.loads(_rows(db, "SELECT value FROM metadata WHERE key = 'stats_cache_recent'")[0][0])
    assert [r["artist"] for r in recent] == ["Plex Band"]


# -- the two export shapes hold the same plays --------------------------------

def test_account_data_then_extended_history_is_one_play_each(db):
    spotify_export.import_batch(db, [_acct("2024-01-15 08:23", "Song", "Band", ms=180_000)],
                                SHARED_OWNER)
    result = spotify_export.import_batch(
        db, [_ext("2024-01-15T08:23:45Z", "Song", "Band", ms=180_000)], SHARED_OWNER)

    assert result["added"] == 0
    # the stored play took the extended one's second and track id
    assert _spotify_rows(db) == [("Song", "Band", "2024-01-15 08:20:45", 180_000,
                                  "4uLU6hMCjMI75M1A2tKUQC", SHARED_OWNER, 0)]
    # and both exports imported again add nothing
    again = spotify_export.import_batch(db, [
        _ext("2024-01-15T08:23:45Z", "Song", "Band", ms=180_000),
        _acct("2024-01-15 08:23", "Song", "Band", ms=180_000)], SHARED_OWNER)
    assert again["added"] == 0
    assert len(_spotify_rows(db)) == 1


def test_extended_history_then_account_data_adds_nothing_twice(db):
    spotify_export.import_batch(db, [_ext("2024-01-15T08:23:45Z", "Song", "Band", ms=180_000)],
                                SHARED_OWNER)
    result = spotify_export.import_batch(
        db, [_acct("2024-01-15 08:23", "Song", "Band", ms=180_000),
             _acct("2024-01-16 08:23", "Song", "Band", ms=180_000)], SHARED_OWNER)

    assert result["added"] == 1  # only the play the extended export didn't have
    assert len(_spotify_rows(db)) == 2
