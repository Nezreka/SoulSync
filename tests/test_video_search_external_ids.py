"""Indexer searches carry the external ids they have.

``prowlarr_search`` accepted ``imdb_id`` / ``tmdb_id`` / ``tvdb_id`` from the day it was
written, and ``build_strategies`` turns them into the structured Newznab hints
(``tvdbid=``, ``imdbid=``, ``tmdbid=``) that Prowlarr routes to whichever indexers can
resolve them. That is the id-aware path Sonarr and Radarr lean on so they never depend
on a release title being spelled the way the metadata provider spells it.

Nothing ever passed them. Every automated search ran as free text, which is a quiet way
to be worse than the *arrs at exactly the titles where names are unreliable: a curly
apostrophe ("Tyler Perry's"), a disambiguating year in the show name ("Insomnia (2024)"),
a scene rename. The ids were sitting in the database the whole time — 3,372 of Boulder's
shows have a ``tvdb_id``.

These tests pin the carrier at each hop, because a carrier that silently stops carrying
looks exactly like the bug being fixed and nothing else would notice.
"""

from __future__ import annotations

from core.automation.handlers.video_process_wishlist import search_context
from core.video.prowlarr_search import build_strategies


def _extras(strategies, kind):
    for st_type, _q, extra in strategies:
        if st_type == kind:
            return dict(extra)
    return {}


# ── the ids reach the search context ─────────────────────────────────────────

def test_an_episode_context_carries_the_series_ids():
    ctx = search_context({"show_tmdb_id": 1396, "show_title": "Breaking Bad",
                          "season_number": 2, "episode_number": 3,
                          "tvdb_id": 81189, "imdb_id": "tt0903747"}, "episode")
    assert ctx["tvdb_id"] == 81189
    assert ctx["imdb_id"] == "tt0903747"
    assert ctx["tmdb_id"] == 1396


def test_a_movie_context_carries_the_film_ids_and_no_tvdb():
    ctx = search_context({"tmdb_id": 27205, "title": "Inception", "year": 2010,
                          "imdb_id": "tt1375666"}, "movie")
    assert ctx["tmdb_id"] == 27205 and ctx["imdb_id"] == "tt1375666"
    assert "tvdb_id" not in ctx, "a film has no TVDB id; sending one is noise"


def test_missing_ids_are_omitted_not_sent_as_null():
    """A wished title that isn't in the library has no ids. The strategy builder
    must see 'unknown', not a None it would have to defend against."""
    ctx = search_context({"tmdb_id": 5, "title": "Unowned", "year": 2020}, "movie")
    assert "imdb_id" not in ctx and "tvdb_id" not in ctx
    assert ctx["tmdb_id"] == 5


def test_the_ids_ride_alongside_the_existing_context_keys():
    """Regression guard: the ids were added to a dict several other fields already
    depend on (series_type, absolute, titles, air_date)."""
    ctx = search_context({"show_tmdb_id": 1, "show_title": "Daily Thing",
                          "season_number": 3, "episode_number": 9,
                          "air_date": "2026-07-08", "series_type": "daily",
                          "tvdb_id": 42}, "episode")
    assert ctx["scope"] == "episode" and ctx["season"] == 3 and ctx["episode"] == 9
    assert ctx["air_date"] == "2026-07-08" and ctx["series_type"] == "daily"
    assert ctx["tvdb_id"] == 42


# ── the strategy builder turns them into Newznab hints ───────────────────────

def test_a_tvdb_id_becomes_a_structured_tvsearch_hint():
    strat = build_strategies("episode", "Breaking Bad", season=2, episode=3,
                             tvdb_id=81189, imdb_id="tt0903747")
    extra = _extras(strat, "tvsearch")
    assert extra.get("tvdbid") == 81189
    assert extra.get("season") == 2 and extra.get("ep") == 3
    assert extra.get("imdbid") == "0903747", "Newznab wants the digits, no tt prefix"


def test_a_movie_search_carries_imdb_and_tmdb():
    strat = build_strategies("movie", "Inception", year=2010,
                             imdb_id="tt1375666", tmdb_id=27205)
    extra = _extras(strat, "movie")
    assert extra.get("imdbid") == "1375666" and extra.get("tmdbid") == 27205
    assert extra.get("year") == 2010


def test_the_free_text_search_still_runs_beside_the_structured_one():
    """The ids are an ADDITION. Public trackers that only do text are often
    tighter than a structured query, so losing the scene-formatted search would
    trade one blind spot for another."""
    strat = build_strategies("episode", "Breaking Bad", season=2, episode=3, tvdb_id=81189)
    kinds = [s[0] for s in strat]
    assert "tvsearch" in kinds and "search" in kinds


def test_no_ids_still_produces_a_usable_search():
    strat = build_strategies("movie", "Unowned", year=2020)
    assert strat, "a title with no ids must still search"
    assert _extras(strat, "movie") == {"year": 2020}


# ── the wishlist queries supply them ─────────────────────────────────────────

def test_the_episode_drain_query_selects_the_series_ids():
    """Source guard on the SQL: the ids come from the library's shows row, and a
    refactor that drops the sub-selects would silently return to free-text
    searching with every other test still green."""
    import inspect

    from database.video_database import VideoDatabase
    src = inspect.getsource(VideoDatabase.episode_wishlist_to_download)
    assert "AS tvdb_id" in src and "AS imdb_id" in src


def test_the_movie_drain_query_selects_the_film_id():
    import inspect

    from database.video_database import VideoDatabase
    assert "AS imdb_id" in inspect.getsource(VideoDatabase.movie_wishlist_to_download)


def test_manual_search_rows_carry_the_ids_too():
    """'Search now' must be at least as well-informed as the automated drain."""
    import inspect

    from database.video_database import VideoDatabase
    src = inspect.getsource(VideoDatabase.wishlist_manual_search_items)
    assert src.count("AS imdb_id") == 2, "both the movie and episode branches"
    assert "AS tvdb_id" in src


# ── the API hands whatever the caller knew to Prowlarr ────────────────────────

def test_the_endpoint_forwards_only_the_ids_it_was_given():
    from api.video.downloads import _external_ids
    assert _external_ids({"tmdb_id": 1, "imdb_id": "tt2", "tvdb_id": 3}) == {
        "tmdb_id": 1, "imdb_id": "tt2", "tvdb_id": 3}
    assert _external_ids({"tmdb_id": 1, "imdb_id": None}) == {"tmdb_id": 1}
    assert _external_ids({}) == {}
    assert _external_ids(None) == {}
