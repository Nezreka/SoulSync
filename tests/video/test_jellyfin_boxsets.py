"""jellyfin's "group movies into collections" (and the shows twin) is a server
setting. when it's on, a /Users/{id}/Items query that doesn't send
CollapseBoxSetItems gets one BoxSet in place of the films in it, so a scan
stored "Alien Collection" and never saw the films."""

from __future__ import annotations

import pytest

from core.video.scanner import VideoLibraryScanner
from core.video.sources import JellyfinVideoSource
from database.video_database import VideoDatabase

VIEWS = {"Items": [{"Id": "mv", "Name": "Movies", "CollectionType": "movies"},
                   {"Id": "tv", "Name": "Shows", "CollectionType": "tvshows"}]}

FILMS = [{"Id": "m1", "Name": "Alien", "Type": "Movie", "ProductionYear": 1979},
         {"Id": "m2", "Name": "Aliens", "Type": "Movie", "ProductionYear": 1986},
         {"Id": "m3", "Name": "Heat", "Type": "Movie", "ProductionYear": 1995}]
MOVIE_SET = {"Id": "bs1", "Name": "Alien Collection", "Type": "BoxSet"}

SERIES = [{"Id": "s1", "Name": "Stargate SG-1", "Type": "Series"},
          {"Id": "s2", "Name": "Stargate Atlantis", "Type": "Series"},
          {"Id": "s3", "Name": "Silo", "Type": "Series"}]
SHOW_SET = {"Id": "bs2", "Name": "Stargate Collection", "Type": "BoxSet"}


class GroupingJellyfin:
    """a jellyfin with both grouping settings on: it collapses unless the
    query says CollapseBoxSetItems=false, the way Folder.CollapseBoxSetItems
    does. ``honor_flag=False`` plays an old scan that never sent it."""

    def __init__(self, honor_flag=True):
        self.user_id = "u1"
        self.base_url = "http://jf"
        self.api_key = "k"
        self.last_error = None
        self.honor_flag = honor_flag
        self.calls = []

    def _listing(self, params):
        kind = params.get("IncludeItemTypes")
        collapse = not (self.honor_flag and params.get("CollapseBoxSetItems") == "false")
        if kind == "Movie":
            items = [MOVIE_SET, FILMS[2]] if collapse else list(FILMS)
        elif kind == "Series":
            items = [SHOW_SET, SERIES[2]] if collapse else list(SERIES)
        else:
            items = []
        total = len(items)
        if params.get("Limit") == "0":
            items = []
        return {"Items": items, "TotalRecordCount": total}

    def _make_request(self, path, params=None):
        params = dict(params or {})
        self.calls.append((path, params))
        if path.endswith("/Views"):
            return VIEWS
        if path.endswith("/Items"):
            return self._listing(params)
        if "/Shows/" in path:
            return {"Items": []}
        return None


@pytest.fixture()
def db(tmp_path):
    return VideoDatabase(database_path=str(tmp_path / "video_library.db"))


def _source(jf):
    return JellyfinVideoSource(jf, movies_lib="Movies", tv_lib="Shows")


def _scan(db, jf, mode="deep", media_type="all"):
    return VideoLibraryScanner(db).scan_sync(lambda: _source(jf), mode=mode, media_type=media_type)


def _titles(db, table):
    conn = db._get_connection()
    try:
        return sorted(r[0] for r in conn.execute(f"SELECT title FROM {table}"))
    finally:
        conn.close()


def test_a_scan_stores_the_films_not_the_collection(db):
    st = _scan(db, GroupingJellyfin(), media_type="movie")
    assert st["state"] == "done"
    assert _titles(db, "movies") == ["Alien", "Aliens", "Heat"]


def test_a_scan_stores_the_shows_not_the_collection(db):
    st = _scan(db, GroupingJellyfin(), media_type="show")
    assert st["state"] == "done"
    assert _titles(db, "shows") == ["Silo", "Stargate Atlantis", "Stargate SG-1"]


def test_the_progress_total_counts_the_films(db):
    assert _source(GroupingJellyfin()).counts() == {"movies": 3, "shows": 3}


@pytest.mark.parametrize("incremental, since", [(False, None), (True, None), (True, "delta")])
def test_every_listing_asks_for_the_films(incremental, since):
    from datetime import datetime, timezone
    jf = GroupingJellyfin()
    when = datetime(2026, 11, 1, tzinfo=timezone.utc) if since else None
    src = _source(jf)
    list(src.iter_movies(incremental=incremental, since=when))
    list(src.iter_shows(incremental=incremental, since=when))
    listings = [p for path, p in jf.calls
                if path.endswith("/Items") and p.get("IncludeItemTypes") in ("Movie", "Series")]
    assert listings
    assert all(p.get("CollapseBoxSetItems") == "false" for p in listings)


def test_a_collection_row_from_an_older_scan_goes_on_the_next_deep_scan(db):
    _scan(db, GroupingJellyfin(honor_flag=False), media_type="movie")
    assert _titles(db, "movies") == ["Alien Collection", "Heat"]
    _scan(db, GroupingJellyfin(), media_type="movie")
    assert _titles(db, "movies") == ["Alien", "Aliens", "Heat"]
