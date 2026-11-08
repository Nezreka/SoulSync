"""A show card's x/y eps (Library grid, Watchlist): the total is what you
have plus what you still want. Unaired episodes and unmonitored ones (an
unmonitored Specials season, say) are not gaps."""

from __future__ import annotations

from database.video_database import VideoDatabase

PAST, FUTURE = "2020-01-01", "2999-01-01"


def _db(tmp_path):
    return VideoDatabase(database_path=str(tmp_path / "video_library.db"))


def _ahsoka(db):
    """S1 owned (2 eps), S2 announced but unaired (2), Specials aired, not owned (2)."""
    return db.upsert_show_tree("plex", {"server_id": "s1", "title": "Ahsoka", "tmdb_id": 114461,
                                        "seasons": [
        {"season_number": 1, "server_id": "se1", "episodes": [
            {"episode_number": n, "server_id": "e1%d" % n, "air_date": PAST,
             "file": {"relative_path": "s1e%d.mkv" % n}} for n in (1, 2)]},
        {"season_number": 2, "server_id": "se2", "episodes": [
            {"episode_number": n, "server_id": "e2%d" % n, "air_date": FUTURE} for n in (1, 2)]},
        {"season_number": 0, "server_id": "se0", "episodes": [
            {"episode_number": n, "server_id": "e0%d" % n, "air_date": PAST} for n in (1, 2)]}]})


def _card(db):
    it = db.query_library("shows", search="Ahsoka")["items"][0]
    return it["owned_count"], it["episode_count"]


def test_unaired_episodes_are_not_counted(tmp_path):
    db = _db(tmp_path)
    _ahsoka(db)
    # S1 owned + the aired, monitored Specials still wanted; S2 has not aired
    assert _card(db) == (2, 4)


def test_unmonitored_episodes_are_not_counted(tmp_path):
    db = _db(tmp_path)
    sid = _ahsoka(db)
    db.set_season_monitored(sid, 0, False)
    assert _card(db) == (2, 2)                     # was 2/6 before this change


def test_an_aired_monitored_gap_still_counts(tmp_path):
    db = _db(tmp_path)
    sid = _ahsoka(db)
    db.set_season_monitored(sid, 0, False)
    db.backfill_episodes(sid, 1, [{"episode_number": 3, "title": "E3", "air_date": PAST}])
    assert _card(db) == (2, 3)


def test_an_owned_file_in_an_unmonitored_season_still_counts(tmp_path):
    db = _db(tmp_path)
    sid = _ahsoka(db)
    db.set_season_monitored(sid, 1, False)
    assert _card(db) == (2, 4)                     # owned never outruns the total


def test_the_watchlist_card_counts_the_same_way(tmp_path):
    db = _db(tmp_path)
    sid = _ahsoka(db)
    db.set_season_monitored(sid, 0, False)
    db.add_to_watchlist("show", 114461, "Ahsoka", library_id=sid)
    show = next(w for w in db.list_watchlist("show") if w["tmdb_id"] == 114461)
    assert (show["owned_count"], show["episode_count"]) == (2, 2)
