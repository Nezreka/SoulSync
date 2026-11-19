"""movies.server_added_at -- when the media server added a movie (Plex addedAt,
Jellyfin DateCreated). movies.added_at is when SoulSync first saw the row, so a
fresh install reads every film as added that week."""

from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace

import pytest

from core.video.sources import _JF_MOVIE_FIELDS, JellyfinVideoSource, PlexVideoSource
from database.video_database import VideoDatabase


@pytest.fixture()
def db(tmp_path):
    return VideoDatabase(database_path=str(tmp_path / "video_library.db"))


def _plex_movie(**over):
    base = dict(ratingKey=101, title="Heat", year=1995, summary=None, thumb=None, contentRating=None,
                studio=None, tagline=None, audienceRating=8.3, rating=None, viewCount=0,
                lastViewedAt=None, viewOffset=0, genres=[], duration=None, media=[], guids=[],
                addedAt=datetime(2019, 3, 4, 5, 6, 7))
    base.update(over)
    return SimpleNamespace(**base)


def _jf_movie(**over):
    base = {"Id": "abc", "Name": "Heat", "ProductionYear": 1995, "CommunityRating": 8.3,
            "UserData": {"PlayCount": 0}, "DateCreated": "2019-03-04T05:06:07.0000000Z"}
    base.update(over)
    return base


def test_plex_movie_carries_its_server_add_date():
    d = PlexVideoSource._movie(object.__new__(PlexVideoSource), _plex_movie())
    assert d["server_added_at"] == "2019-03-04 05:06:07"
    assert PlexVideoSource._movie(object.__new__(PlexVideoSource), _plex_movie(addedAt=None))["server_added_at"] is None


def test_jellyfin_movie_carries_its_server_add_date_and_asks_for_it():
    # Jellyfin only returns DateCreated when the request names it
    assert "DateCreated" in _JF_MOVIE_FIELDS.split(",")
    d = JellyfinVideoSource._movie(object.__new__(JellyfinVideoSource), _jf_movie())
    assert d["server_added_at"] == "2019-03-04 05:06:07"
    d = JellyfinVideoSource._movie(object.__new__(JellyfinVideoSource), _jf_movie(DateCreated=None))
    assert d["server_added_at"] is None


def _row(db, mid):
    conn = db._get_connection()
    try:
        return conn.execute("SELECT added_at, server_added_at FROM movies WHERE id=?", (mid,)).fetchone()
    finally:
        conn.close()


@pytest.mark.parametrize("source", ["plex", "jellyfin"])
def test_upsert_stores_it_beside_added_at(db, source):
    mid = db.upsert_movie(source, {"server_id": "1", "title": "Heat", "server_added_at": "2019-03-04 05:06:07"})
    r = _row(db, mid)
    assert r["server_added_at"] == "2019-03-04 05:06:07"
    assert r["added_at"] and not r["added_at"].startswith("2019")    # first seen by SoulSync


def test_a_scan_without_it_never_blanks_a_stored_date(db):
    mid = db.upsert_movie("plex", {"server_id": "1", "title": "Heat", "server_added_at": "2019-03-04 05:06:07"})
    db.upsert_movie("plex", {"server_id": "1", "title": "Heat"})
    db.upsert_movie("plex", {"server_id": "1", "title": "Heat", "server_added_at": None},
                    preserve_enrichment=False)
    assert _row(db, mid)["server_added_at"] == "2019-03-04 05:06:07"


def test_an_existing_row_gets_it_on_its_next_scan(db):
    mid = db.upsert_movie("jellyfin", {"server_id": "1", "title": "Heat"})
    assert _row(db, mid)["server_added_at"] is None
    db.upsert_movie("jellyfin", {"server_id": "1", "title": "Heat", "server_added_at": "2019-03-04 05:06:07"})
    assert _row(db, mid)["server_added_at"] == "2019-03-04 05:06:07"
