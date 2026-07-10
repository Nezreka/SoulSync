"""Show-side acquisition: missing MOVIES wishlist (one-shot gets); missing
SHOWS get FOLLOWED on the watchlist — the airing automation then wishes new
episodes. Ended shows are skipped (nothing will air; the nightly watchlist
prune would remove them anyway) and 'mute' tombstones are never overridden."""

from __future__ import annotations

import pytest

from core.video.collections.sync import (watchlist_missing_shows,
                                         wishlist_missing_members)
from database.video_database import VideoDatabase


@pytest.fixture()
def db(tmp_path):
    return VideoDatabase(database_path=str(tmp_path / "video_library.db"))


class _Engine:
    """Statuses: 700/702 airing, 701 ended, 703 unknown."""

    def tmdb_full_detail(self, kind, tmdb_id):
        return {700: {"status": "Returning Series"},
                701: {"status": "Ended"},
                702: {"status": "In Production"},
                703: {}}.get(int(tmdb_id), {})


def _definition(media_type="show", wishlist=True):
    return {"id": 1, "kind": "list", "media_type": media_type,
            "wishlist_missing": wishlist, "definition": {"source": "tmdb_chart"}}


def _missing(*ids):
    return [{"tmdb_id": i, "title": f"Show {i}", "year": 2020,
             "poster_url": f"https://img/poster-{i}.jpg"} for i in ids]


def test_missing_shows_get_followed_ended_skipped(db):
    n = watchlist_missing_shows(db, _definition(), _missing(700, 701, 702, 703),
                                engine=_Engine())
    assert n == 3                                    # 701 (Ended) skipped; unknown 703 follows
    states = db.watchlist_states("show")
    assert set(states) == {700, 702, 703} and set(states.values()) == {"follow"}
    # Parity: the follow row carries the show's poster for the watchlist page.
    row = [w for w in db.list_watchlist("show") if w.get("tmdb_id") == 700][0]
    assert row["poster_url"] == "https://img/poster-700.jpg"


def test_muted_and_already_followed_shows_are_respected(db):
    db.add_to_watchlist("show", 700, "Already Followed")
    conn = db._get_connection()
    conn.execute("INSERT INTO video_watchlist (kind, tmdb_id, title, state) "
                 "VALUES ('show', 702, 'Muted One', 'mute')")
    conn.commit(); conn.close()

    n = watchlist_missing_shows(db, _definition(), _missing(700, 702, 703),
                                engine=_Engine())
    assert n == 1                                    # only 703 is new
    assert db.watchlist_states("show")[702] == "mute"   # never re-followed


def test_cap_limits_status_lookups_per_run(db):
    lookups = []

    class _CountingEngine(_Engine):
        def tmdb_full_detail(self, kind, tmdb_id):
            lookups.append(tmdb_id)
            return super().tmdb_full_detail(kind, tmdb_id)

    n = watchlist_missing_shows(db, _definition(), _missing(700, 702, 703),
                                engine=_CountingEngine(), cap=2)
    assert n == 2 and len(lookups) == 2
    # Next run: the followed two skip without lookups, the third lands.
    lookups.clear()
    n = watchlist_missing_shows(db, _definition(), _missing(700, 702, 703),
                                engine=_CountingEngine(), cap=2)
    assert n == 1 and lookups == [703]


def test_guards(db):
    eng = _Engine()
    assert watchlist_missing_shows(db, _definition(wishlist=False), _missing(700), engine=eng) == 0
    assert watchlist_missing_shows(db, _definition(media_type="movie"), _missing(700), engine=eng) == 0
    smart = {"kind": "smart", "media_type": "show", "wishlist_missing": True, "definition": {}}
    assert watchlist_missing_shows(db, smart, _missing(700), engine=eng) == 0


def test_dispatcher_routes_by_media_type(db, monkeypatch):
    calls = []
    monkeypatch.setattr("core.video.collections.sync.watchlist_missing_shows",
                        lambda dbb, d, m, **kw: calls.append("show") or 1)
    monkeypatch.setattr("core.video.collections.sync.wishlist_missing_movies",
                        lambda dbb, d, m: calls.append("movie") or 1)
    wishlist_missing_members(db, {"media_type": "show"}, [])
    wishlist_missing_members(db, {"media_type": "movie"}, [])
    wishlist_missing_members(db, {}, [])
    assert calls == ["show", "movie", "movie"]
