"""#1452: the Discovery Pool playlist filter must scope the matched list and the stats.

Server-side regression test for the Tools > Discovery Pool modal's playlist dropdown.
Before the fix, GET /api/discovery-pool only filtered the *failed* list: the matched
list came from the global discovery_match_cache with no playlist filter, and the
header stats ignored playlist_id, so selecting a playlist visibly did nothing.

These tests exercise the DB layer behind the endpoint. They use the same
normalization for cache writes that the filter uses (the real matching engine when
importable, otherwise a stubbed engine standing in for it).
"""
import json

import pytest

import database.music_database as mdb
from database.music_database import MusicDatabase


class _StubEngine:
    """Stands in for MusicMatchingEngine when its third-party deps are missing."""

    def clean_title(self, title):
        return (title or "").lower().strip()

    def clean_artist(self, artist):
        return (artist or "").lower().strip()


def _engine(monkeypatch):
    engine = mdb._matching_engine
    if engine is None:
        engine = _StubEngine()
        monkeypatch.setattr(mdb, "_matching_engine", engine)
    return engine


@pytest.fixture()
def pool_db(tmp_path, monkeypatch):
    db = MusicDatabase(str(tmp_path / "music.db"))
    engine = _engine(monkeypatch)

    pid_a = db.mirror_playlist(
        source="spotify_public",
        source_playlist_id="hash-a",
        name="Release Radar",
        tracks=[
            {"track_name": "Alpha", "artist_name": "Artist One",
             "extra_data": {"discovery_attempted": True}},       # failed
            {"track_name": "Beta", "artist_name": "Artist Two",
             "extra_data": {"discovered": True}},                 # matched, not failed
        ],
        profile_id=1,
    )
    pid_b = db.mirror_playlist(
        source="spotify_public",
        source_playlist_id="hash-b",
        name="Discover Weekly",
        tracks=[
            {"track_name": "Gamma", "artist_name": "Artist Three",
             "extra_data": {"discovery_attempted": True}},       # failed
        ],
        profile_id=1,
    )

    for title, artist in (("Alpha", "Artist One"), ("Beta", "Artist Two"),
                          ("Gamma", "Artist Three")):
        assert db.save_discovery_cache_match(
            engine.clean_title(title),
            engine.clean_artist(artist),
            "spotify",
            0.99,
            {"name": title, "artists": [artist]},
            title,
            artist,
        ), "cache insert failed"

    return db, pid_a, pid_b


def test_matched_default_path_returns_everything(pool_db):
    db, pid_a, pid_b = pool_db
    titles = {r["original_title"] for r in db.get_discovery_pool_matched()}
    assert titles == {"Alpha", "Beta", "Gamma"}


def test_matched_filters_to_playlist(pool_db):
    db, pid_a, pid_b = pool_db
    titles_a = {r["original_title"] for r in db.get_discovery_pool_matched(playlist_id=pid_a)}
    titles_b = {r["original_title"] for r in db.get_discovery_pool_matched(playlist_id=pid_b)}
    assert titles_a == {"Alpha", "Beta"}
    assert titles_b == {"Gamma"}


def test_matched_playlist_scoped_by_profile(pool_db):
    db, pid_a, pid_b = pool_db
    # pid_a belongs to profile 1; a different profile must see nothing
    assert db.get_discovery_pool_matched(profile_id=2, playlist_id=pid_a) == []


def test_stats_unfiltered(pool_db):
    db, pid_a, pid_b = pool_db
    assert db.get_discovery_pool_stats() == {"matched": 3, "failed": 2}


def test_stats_filtered_by_playlist(pool_db):
    db, pid_a, pid_b = pool_db
    assert db.get_discovery_pool_stats(playlist_id=pid_a) == {"matched": 2, "failed": 1}
    assert db.get_discovery_pool_stats(playlist_id=pid_b) == {"matched": 1, "failed": 1}


def test_failed_still_filters(pool_db):
    db, pid_a, pid_b = pool_db
    failed_a = db.get_discovery_pool_failed(playlist_id=pid_a)
    assert [r["track_name"] for r in failed_a] == ["Alpha"]


def test_wing_it_stats_filtered_by_playlist(pool_db, monkeypatch):
    db, pid_a, pid_b = pool_db
    _engine(monkeypatch)  # no-op once fixture ran; kept for standalone clarity
    # Seed one unverified wing-it stub on pid_a and one resolved one on pid_b
    pid = pid_a
    conn = db._get_connection()
    cur = conn.cursor()
    cur.execute("SELECT id FROM mirrored_playlist_tracks WHERE playlist_id = ? LIMIT 1", (pid,))
    tid_a = cur.fetchone()["id"]
    cur.execute("SELECT id FROM mirrored_playlist_tracks WHERE playlist_id = ? LIMIT 1", (pid_b,))
    tid_b = cur.fetchone()["id"]
    cur.execute(
        "UPDATE mirrored_playlist_tracks SET extra_data = ? WHERE id = ?",
        (json.dumps({"wing_it_fallback": True,
                     "matched_data": {"id": "wing_it_abc"}}), tid_a),
    )
    cur.execute(
        "UPDATE mirrored_playlist_tracks SET extra_data = ? WHERE id = ?",
        (json.dumps({"wing_it_fallback": True, "manual_match": True,
                     "matched_data": {"id": "spotify:track:real"}}), tid_b),
    )
    conn.commit()
    conn.close()

    assert db.get_wing_it_pool_stats() == {"wing_it": 1, "matched": 1}
    assert db.get_wing_it_pool_stats(playlist_id=pid_a) == {"wing_it": 1, "matched": 0}
    assert db.get_wing_it_pool_stats(playlist_id=pid_b) == {"wing_it": 0, "matched": 1}


def test_filter_keys_match_write_path_keys(pool_db, monkeypatch):
    """The per-playlist filter must re-key tracks exactly the way the cache
    write path does (#1452 review follow-up).

    The fixture writes cache rows with the engine methods directly; the real
    writer is ``_get_discovery_cache_key`` in api.source_playlists (it also
    runs artist names through ``_extract_artist_name``). If the writer ever
    changes its normalization, the in-memory filter would silently stop
    matching — this test pins the two together. Skipped where the full app
    stack (spotipy et al) isn't importable.
    """
    source_playlists = pytest.importorskip(
        "api.source_playlists", reason="write path needs the full app stack"
    )
    db, pid_a, _pid_b = pool_db
    # Route dependencies are normally injected by web_server at registration.
    monkeypatch.setattr(source_playlists, "_matching_engine", lambda: mdb._matching_engine)
    write_keys = {
        source_playlists._get_discovery_cache_key(title, artist)
        for title, artist in (("Alpha", "Artist One"), ("Beta", "Artist Two"))
    }
    filter_keys = db._get_playlist_discovery_keys(pid_a, profile_id=1)
    assert write_keys <= filter_keys


def _cache_titles(db):
    return {r["original_title"] for r in db.get_discovery_pool_matched()}


def test_clear_all_empties_cache(pool_db):
    db, pid_a, pid_b = pool_db
    assert _cache_titles(db) == {"Alpha", "Beta", "Gamma"}
    assert db.clear_discovery_cache() == 3
    assert _cache_titles(db) == set()


def test_clear_scoped_to_playlist(pool_db):
    db, pid_a, pid_b = pool_db
    # pid_a's tracks re-key to the Alpha + Beta cache rows; Gamma belongs to pid_b
    assert db.clear_discovery_cache(playlist_id=pid_a) == 2
    assert _cache_titles(db) == {"Gamma"}


def test_clear_scoped_to_other_playlist(pool_db):
    db, pid_a, pid_b = pool_db
    assert db.clear_discovery_cache(playlist_id=pid_b) == 1
    assert _cache_titles(db) == {"Alpha", "Beta"}


def test_clear_respects_profile_scoping(pool_db):
    db, pid_a, pid_b = pool_db
    # pid_a belongs to profile 1; clearing it as profile 2 must touch nothing
    assert db.clear_discovery_cache(playlist_id=pid_a, profile_id=2) == 0
    assert _cache_titles(db) == {"Alpha", "Beta", "Gamma"}


def test_clear_unknown_playlist_deletes_nothing(pool_db):
    db, pid_a, pid_b = pool_db
    assert db.clear_discovery_cache(playlist_id=99999) == 0
    assert _cache_titles(db) == {"Alpha", "Beta", "Gamma"}
