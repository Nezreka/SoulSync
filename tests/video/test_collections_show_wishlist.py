"""Show-side wishlist tie-in: a list collection's missing SHOWS expand into
aired-episode rows with the same TMDB season metadata a manual add stores
(stills / overviews / season posters — the write-parity rules), capped per run
so a 100-show chart can't flood the wishlist in one pass."""

from __future__ import annotations

import pytest

from core.video.collections.sync import (wishlist_missing_members,
                                         wishlist_missing_shows)
from database.video_database import VideoDatabase


@pytest.fixture()
def db(tmp_path):
    return VideoDatabase(database_path=str(tmp_path / "video_library.db"))


class _Engine:
    """Two seasons; S2E2 hasn't aired yet."""

    def tmdb_full_detail(self, kind, tmdb_id):
        return {"seasons": [{"season_number": 0}, {"season_number": 1},
                            {"season_number": 2}]}

    def tmdb_season(self, tid, sn):
        eps = {
            1: [{"episode_number": 1, "title": "Pilot", "air_date": "2020-01-01",
                 "overview": "It begins.", "still_url": "https://img/still-1-1.jpg"},
                {"episode_number": 2, "title": "Two", "air_date": "2020-01-08",
                 "overview": "More.", "still_url": "https://img/still-1-2.jpg"}],
            2: [{"episode_number": 1, "title": "Back", "air_date": "2021-01-01",
                 "overview": None, "still_url": None},
                {"episode_number": 2, "title": "Future", "air_date": "2099-01-01",
                 "overview": "Not yet.", "still_url": None}],
        }
        return {"season_number": sn, "poster_url": f"https://img/season-{sn}.jpg",
                "episodes": eps.get(sn, [])}


def _definition(media_type="show", wishlist=True):
    return {"id": 1, "kind": "list", "media_type": media_type,
            "wishlist_missing": wishlist, "definition": {"source": "tmdb_chart"}}


def _missing(*ids):
    return [{"tmdb_id": i, "title": f"Show {i}", "year": 2020,
             "poster_url": f"https://img/poster-{i}.jpg"} for i in ids]


def test_show_expansion_aired_only_with_parity_fields(db):
    n = wishlist_missing_shows(db, _definition(), _missing(700),
                               engine=_Engine(), today="2026-07-10")
    assert n == 3                                    # S1E1, S1E2, S2E1 — not future S2E2
    conn = db._get_connection()
    rows = [dict(r) for r in conn.execute(
        "SELECT * FROM video_wishlist WHERE kind='episode' ORDER BY season_number, episode_number")]
    conn.close()
    assert len(rows) == 3
    r = rows[0]
    assert r["tmdb_id"] == 700 and r["title"] == "Show 700"
    assert r["poster_url"] == "https://img/poster-700.jpg"          # show art (parity)
    assert r["season_poster_url"] == "https://img/season-1.jpg"     # season art (parity)
    assert r["still_url"] == "https://img/still-1-1.jpg"
    assert r["episode_title"] == "Pilot" and r["episode_overview"] == "It begins."
    assert rows[2]["season_number"] == 2 and rows[2]["episode_number"] == 1


def test_cap_and_already_wishlisted_skip(db):
    # Cap 2: only the first two shows expand this run.
    n = wishlist_missing_shows(db, _definition(), _missing(700, 701, 702),
                               engine=_Engine(), today="2026-07-10", cap=2)
    assert n == 6
    assert sorted(db.wishlisted_show_tmdb_ids()) == [700, 701]
    # Next run: expanded shows skip, the capped-out one lands.
    n = wishlist_missing_shows(db, _definition(), _missing(700, 701, 702),
                               engine=_Engine(), today="2026-07-10", cap=2)
    assert n == 3
    assert sorted(db.wishlisted_show_tmdb_ids()) == [700, 701, 702]


def test_guards(db):
    eng = _Engine()
    assert wishlist_missing_shows(db, _definition(wishlist=False), _missing(700), engine=eng) == 0
    assert wishlist_missing_shows(db, _definition(media_type="movie"), _missing(700), engine=eng) == 0
    smart = {"kind": "smart", "media_type": "show", "wishlist_missing": True, "definition": {}}
    assert wishlist_missing_shows(db, smart, _missing(700), engine=eng) == 0


def test_dispatcher_routes_by_media_type(db, monkeypatch):
    calls = []
    monkeypatch.setattr("core.video.collections.sync.wishlist_missing_shows",
                        lambda dbb, d, m, **kw: calls.append("show") or 1)
    monkeypatch.setattr("core.video.collections.sync.wishlist_missing_movies",
                        lambda dbb, d, m: calls.append("movie") or 1)
    wishlist_missing_members(db, {"media_type": "show"}, [])
    wishlist_missing_members(db, {"media_type": "movie"}, [])
    wishlist_missing_members(db, {}, [])
    assert calls == ["show", "movie", "movie"]
