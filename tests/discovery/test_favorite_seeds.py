"""what you said you love feeds taste: Liked Songs, server stars and ratings,
and Watchlist adds become recommendation seeds next to what you play.

real MusicDatabase on a tmp file; the seed builders are the real ones."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from core.discovery import favorite_seeds as fs
from core.discovery.listening_recommendations import build_recency_weighted_seeds
from core.listening_import.dedup import insert_import_events
from core.listening_scope import SHARED_OWNER
from database.music_database import MusicDatabase
from tests import lib2_seed


@pytest.fixture()
def db(tmp_path):
    fs.clear_cache()
    return MusicDatabase(str(tmp_path / "music.db"))


def _like(db, profile_id, *songs):
    from core.playlists import user_playlists
    user_playlists.like(db, profile_id, [{"name": title, "artist": artist}
                                         for artist, title in songs])


def _follow(db, profile_id, name, added=None):
    conn = db._get_connection()
    try:
        conn.execute("INSERT INTO watchlist_artists (artist_name, profile_id, date_added) "
                     "VALUES (?, ?, ?)", (name, profile_id,
                                          added or datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")))
        conn.commit()
    finally:
        conn.close()


def _plays(db, artist, n, owner=SHARED_OWNER):
    insert_import_events(db, [{"track_id": f"p:{artist}:{i}", "title": f"Song {i}", "artist": artist,
                               "played_at": f"2024-01-{i % 27 + 1:02d} 10:{i % 60:02d}:00",
                               "scrobble_eligible": 1} for i in range(n)], "plex", profile_id=owner)


# -- the signals --------------------------------------------------------------

def test_a_liked_songs_artist_is_a_favourite(db):
    _like(db, SHARED_OWNER, ("Liked Band", "One"), ("Liked Band", "Two"), ("Other", "Three"))

    units = fs.favorite_artist_units(db, SHARED_OWNER, admin_navidrome="")

    assert units == {"Liked Band": 2.0, "Other": 1.0}


def test_likes_are_per_profile(db):
    kim = db.create_profile(name="kim")
    _like(db, kim, ("Kim Band", "One"))

    assert fs.favorite_artist_units(db, SHARED_OWNER, admin_navidrome="") == {}
    assert fs.favorite_artist_units(db, kim, admin_navidrome="") == {"Kim Band": 1.0}


def test_one_artists_likes_are_capped(db):
    _like(db, SHARED_OWNER, *[("Liked Band", f"Song {i}") for i in range(40)])

    assert fs.favorite_artist_units(db, SHARED_OWNER, admin_navidrome="")["Liked Band"] == fs.LIKE_CAP


def test_a_watchlist_add_counts_more_when_recent(db):
    old = (datetime.now(timezone.utc) - timedelta(days=400)).strftime("%Y-%m-%d %H:%M:%S")
    _follow(db, SHARED_OWNER, "New Follow")
    _follow(db, SHARED_OWNER, "Old Follow", added=old)

    units = fs.favorite_artist_units(db, SHARED_OWNER, admin_navidrome="")

    assert units["New Follow"] > units["Old Follow"] == fs.WATCHLIST_UNITS


def test_server_stars_and_high_ratings_for_your_account(db):
    conn = db._get_connection()
    try:
        lib2_seed.track(conn, "Starred Band", "Album", "Hit", path="/music/Starred Band/Album/01 Hit.flac")
        lib2_seed.track(conn, "Rated Band", "Album", "Gem", path="/music/Rated Band/Album/02 Gem.flac")
        lib2_seed.track(conn, "Meh Band", "Album", "Fine", path="/music/Meh Band/Album/03 Fine.flac")
        lib2_seed.track(conn, "Kim Band", "Album", "Hers", path="/music/Kim Band/Album/04 Hers.flac")
        conn.commit()
    finally:
        conn.close()
    kim = db.create_profile(name="kim")
    conn = db._get_connection()
    try:
        conn.execute("UPDATE profiles SET navidrome_username = 'kim' WHERE id = ?", (kim,))
        conn.commit()
    finally:
        conn.close()
    # the server's own paths differ from the library's; the last two parts match
    db.replace_curation_signals("navidrome", "boss", [
        {"track_key": "album/01 hit.flac", "favorite": True, "source_path": "/data/x/Album/01 Hit.flac"},
        {"track_key": "album/02 gem.flac", "favorite": False, "rating": 5},
        {"track_key": "album/03 fine.flac", "favorite": False, "rating": 2},
    ])
    db.replace_curation_signals("navidrome", "kim", [{"track_key": "album/04 hers.flac", "favorite": True}])

    admin = fs.favorite_artist_units(db, SHARED_OWNER, admin_navidrome="boss")
    fs.clear_cache()
    hers = fs.favorite_artist_units(db, kim, admin_navidrome="boss")

    assert admin == {"Starred Band": 1.0, "Rated Band": 1.0}
    assert hers == {"Kim Band": 1.0}


# -- into the seeds -----------------------------------------------------------

def test_a_liked_artist_becomes_a_seed_even_with_no_plays_of_them():
    seeds = build_recency_weighted_seeds(
        [{"name": "Played", "play_count": 40}], {}, favorites={"Liked Band": 2.0})

    by_name = {s["name"]: s["weight"] for s in seeds}
    assert set(by_name) == {"Played", "Liked Band"}
    # a unit is the median play seed: two units weigh twice the artist you play
    assert by_name["Liked Band"] == pytest.approx(2 * by_name["Played"])


def test_a_favourite_lifts_an_artist_you_also_play():
    seeds = build_recency_weighted_seeds(
        [{"name": "A", "play_count": 10}, {"name": "B", "play_count": 10}], {},
        favorites={"b": 1.0})
    by_name = {s["name"]: s["weight"] for s in seeds}
    assert by_name["B"] > by_name["A"]
    assert len(seeds) == 2  # matched case-insensitively, not added twice


def test_a_long_watchlist_adds_only_its_strongest_few():
    from core.discovery.listening_recommendations import MAX_FAVORITE_ONLY_SEEDS
    favorites = {f"Follow {i:03d}": 1.0 for i in range(200)}
    favorites["Loved"] = 5.0
    seeds = build_recency_weighted_seeds([{"name": "Played", "play_count": 10}], {},
                                         favorites=favorites)
    names = [s["name"] for s in seeds]
    assert len(names) == 1 + MAX_FAVORITE_ONLY_SEEDS
    assert "Loved" in names


def test_no_favourites_leaves_the_seeds_as_they_were():
    top = [{"name": "A", "play_count": 10}]
    assert build_recency_weighted_seeds(top, {"a": 2}) == build_recency_weighted_seeds(
        top, {"a": 2}, favorites={})


def test_a_light_listeners_likes_still_count():
    seeds = build_recency_weighted_seeds([], {}, favorites={"Liked Band": 1.0})
    assert seeds == [{"name": "Liked Band", "weight": fs.UNIT_FLOOR}]
    # one play of someone else doesn't shrink a like to nothing
    seeds = build_recency_weighted_seeds([{"name": "A", "play_count": 1}], {},
                                         favorites={"Liked Band": 1.0})
    by_name = {s["name"]: s["weight"] for s in seeds}
    assert by_name["Liked Band"] == by_name["A"] >= fs.UNIT_FLOOR


def test_daily_mixes_seed_from_a_liked_artist(db, monkeypatch):
    """a liked, owned artist founds a mix with no plays of theirs at all"""
    from core.personalized import daily_mixes as dm

    conn = db._get_connection()
    try:
        for i in range(12):
            lib2_seed.track(conn, "Liked Band", "Album", f"Song {i}")
        conn.commit()
    finally:
        conn.close()
    _plays(db, "Someone Else", 3)
    _like(db, SHARED_OWNER, ("Liked Band", "Song 1"))
    seen = {}
    real = dm._seed_edges

    def spy(database, names, profile_id, *a, **k):
        seen.setdefault("names", list(names))
        return real(database, names, profile_id, *a, **k)

    monkeypatch.setattr(dm, "_seed_edges", spy)
    monkeypatch.setattr(fs, "_admin_navidrome_user", lambda: "")
    dm.generate_daily_mixes(db, SHARED_OWNER)

    assert "liked band" in seen["names"]


def test_recommendations_come_from_a_liked_artists_similar_artists(db, monkeypatch):
    """the listening recommendations seed on the liked artist, and its similar
    artist comes out recommended"""
    import core.discovery.listening_recommendations as lr
    from core import watchlist_scanner as ws

    monkeypatch.setattr(fs, "_admin_navidrome_user", lambda: "")
    conn = db._get_connection()
    try:
        lib2_seed.track(conn, "Played Band", "Album", "Song")
        lib2_seed.track(conn, "Liked Band", "Album", "Song")
        conn.execute("UPDATE lib2_artists SET spotify_id = 'sp-liked' WHERE name = 'Liked Band'")
        conn.execute("INSERT INTO similar_artists (source_artist_id, similar_artist_name, "
                     "similarity_rank, profile_id) VALUES ('sp-liked', 'Their Friend', 1, 1)")
        conn.commit()
    finally:
        conn.close()
    _plays(db, "Played Band", 5)
    _like(db, SHARED_OWNER, ("Liked Band", "Song"))
    captured = {}
    real = lr.rank_recommended_artists

    def spy(seeds, *a, **k):
        captured["seeds"] = [s["name"] for s in seeds]
        captured["recs"] = [r.name for r in real(seeds, *a, **k)]
        return []  # stop before the track fetch, which goes to the network

    monkeypatch.setattr(lr, "rank_recommended_artists", spy)
    scanner = ws.WatchlistScanner.__new__(ws.WatchlistScanner)
    scanner._database = db
    scanner._build_listening_recommendations(SHARED_OWNER, [])

    assert "Liked Band" in captured["seeds"]
    assert "Their Friend" in captured["recs"]
