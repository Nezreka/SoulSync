"""Video Prowlarr search: the multi-strategy (structured tvsearch/movie + scene-text)
query builder, plus the merge/dedupe/project/protocol-filter of the results. Pure — the
shared ProwlarrClient is injected."""

from __future__ import annotations

import core.video.prowlarr_search as ps


# ── build_strategies (pure) ───────────────────────────────────────────────────
def test_movie_strategies_are_structured_plus_text():
    strat = ps.build_strategies("movie", "The Movie", year=2020)
    assert ("movie", "The Movie", [("year", 2020)]) in strat
    assert ("search", "The Movie 2020", []) in strat


def test_episode_strategies_carry_season_ep_structured_and_scene_text():
    strat = ps.build_strategies("episode", "The Show", season=1, episode=2)
    types = {(t, q) for t, q, _ in strat}
    assert ("tvsearch", "The Show") in types                 # structured, season/ep in extra
    assert ("search", "The Show S01E02") in types            # scene text
    tv_extra = dict(next(e for t, q, e in strat if t == "tvsearch"))
    assert tv_extra["season"] == 1 and tv_extra["ep"] == 2


def test_ids_are_included_and_imdb_is_stripped_of_tt():
    strat = ps.build_strategies("episode", "S", season=3, episode=4,
                                tvdb_id=555, imdb_id="tt0111161", tmdb_id=99)
    tv_extra = dict(next(e for t, q, e in strat if t == "tvsearch"))
    assert tv_extra["tvdbid"] == 555 and tv_extra["imdbid"] == "0111161"


def test_movie_uses_tmdbid():
    strat = ps.build_strategies("movie", "M", year=2021, tmdb_id=42)
    mv_extra = dict(next(e for t, q, e in strat if t == "movie"))
    assert mv_extra["tmdbid"] == 42 and mv_extra["year"] == 2021


def test_identical_strategies_collapse():
    # A movie with no year: structured 'movie' query and the text 'search' query are both
    # just "M" — but they're different TYPES so both run; a blank query is dropped though.
    strat = ps.build_strategies("movie", "M")
    assert ("movie", "M", []) in strat and ("search", "M", []) in strat
    assert all(q.strip() for _, q, _ in strat)


# ── prowlarr_search (client injected) ─────────────────────────────────────────
class _R:
    def __init__(self, **kw):
        self.__dict__.update(kw)


def _patch(monkeypatch, per_type, configured=True):
    """per_type: {search_type: [results]} — each strategy call returns its type's list."""
    calls = []

    class _Client:
        def is_configured(self):
            return configured

        def _search_sync(self, q, cats, ids, limit, search_type="search", extra_params=None,
                         max_wait_seconds=None):
            calls.append({"q": q, "type": search_type, "cats": cats, "extra": list(extra_params or [])})
            return list(per_type.get(search_type, []))

    monkeypatch.setattr(ps, "_client", lambda: _Client())
    monkeypatch.setattr(ps, "_indexer_ids", lambda: [])
    return calls


def test_merges_and_dedupes_across_strategies(monkeypatch):
    # structured finds g1+g2; text finds g2 (dup) + g3 — merged unique set is g1,g2,g3.
    tv = [_R(title="a", size=1, seeders=5, leechers=0, grabs=0, indexer_name="i", indexer_id=1,
             protocol="torrent", magnet_uri="magnet:1", download_url=None, guid="g1"),
          _R(title="b", size=2, seeders=9, leechers=1, grabs=0, indexer_name="i", indexer_id=1,
             protocol="torrent", magnet_uri="magnet:2", download_url=None, guid="g2")]
    txt = [_R(title="b", size=2, seeders=9, leechers=1, grabs=0, indexer_name="i", indexer_id=1,
              protocol="torrent", magnet_uri="magnet:2", download_url=None, guid="g2"),
           _R(title="c", size=3, seeders=1, leechers=0, grabs=0, indexer_name="i", indexer_id=1,
              protocol="torrent", magnet_uri="magnet:3", download_url=None, guid="g3")]
    calls = _patch(monkeypatch, {"tvsearch": tv, "search": txt})
    out = ps.prowlarr_search("episode", "Show", season=1, episode=2, source="torrent")
    guids = sorted(h["guid"] for h in out["hits"])
    assert guids == ["g1", "g2", "g3"]                        # g2 deduped
    assert {c["type"] for c in calls} == {"tvsearch", "search"}   # both strategies ran
    assert 5000 in calls[0]["cats"]                          # TV categories


def test_protocol_filter_and_url_fallback(monkeypatch):
    mixed = [_R(title="t", size=1, seeders=2, leechers=0, grabs=0, indexer_name="i", indexer_id=1,
                protocol="usenet", magnet_uri=None, download_url="http://nzb/1", guid="u1"),
             _R(title="t2", size=1, seeders=2, leechers=0, grabs=0, indexer_name="i", indexer_id=1,
                protocol="torrent", magnet_uri=None, download_url=None, guid="t2")]   # no url → dropped
    _patch(monkeypatch, {"movie": mixed, "search": mixed})
    out = ps.prowlarr_search("movie", "X", year=2020, source="usenet")
    assert len(out["hits"]) == 1 and out["hits"][0]["download_url"] == "http://nzb/1"


def test_one_strategy_failing_still_returns_the_other(monkeypatch):
    good = [_R(title="g", size=1, seeders=3, leechers=0, grabs=0, indexer_name="i", indexer_id=1,
               protocol="torrent", magnet_uri="magnet:g", download_url=None, guid="g")]

    class _Client:
        def is_configured(self):
            return True

        def _search_sync(self, q, cats, ids, limit, search_type="search", extra_params=None,
                         max_wait_seconds=None):
            if search_type == "tvsearch":
                raise RuntimeError("indexer timeout")
            return list(good)

    monkeypatch.setattr(ps, "_client", lambda: _Client())
    monkeypatch.setattr(ps, "_indexer_ids", lambda: [])
    out = ps.prowlarr_search("episode", "S", season=1, episode=1, source="torrent")
    assert [h["guid"] for h in out["hits"]] == ["g"]         # text strategy survived


def test_unconfigured_returns_not_configured(monkeypatch):
    _patch(monkeypatch, {}, configured=False)
    out = ps.prowlarr_search("movie", "X", source="torrent")
    assert out["configured"] is False and out["hits"] == []


def test_provider_name_filter_resolves_matching_indexer(monkeypatch):
    good = [_R(title="g", size=1, seeders=3, leechers=0, grabs=0, indexer_name="1337x", indexer_id=7,
               protocol="torrent", magnet_uri="magnet:g", download_url=None, guid="g")]
    calls = []

    class _Client:
        def is_configured(self):
            return True

        def _get_indexers_sync(self):
            return [_R(id=7, name="1337x", protocol="torrent", enable=True),
                    _R(id=8, name="NZB Geek", protocol="usenet", enable=True)]

        def _search_sync(self, q, cats, ids, limit, search_type="search", extra_params=None,
                         max_wait_seconds=None):
            calls.append(ids)
            return list(good)

    monkeypatch.setattr(ps, "_client", lambda: _Client())
    monkeypatch.setattr(ps, "_indexer_ids", lambda: [])
    out = ps.prowlarr_search("movie", "X", source="torrent", indexer_names=["1337x"])
    assert out["hits"][0]["indexer_id"] == 7
    assert calls and all(ids == [7] for ids in calls)


def test_provider_name_filter_reports_missing_indexer(monkeypatch):
    class _Client:
        def is_configured(self):
            return True

        def _get_indexers_sync(self):
            return [_R(id=8, name="NZB Geek", protocol="usenet", enable=True)]

    monkeypatch.setattr(ps, "_client", lambda: _Client())
    out = ps.prowlarr_search("movie", "X", source="torrent", indexer_names=["1337x"])
    assert out["configured"] is True and out["hits"] == []
    assert "No enabled Prowlarr indexer matches" in out["error"]


def test_provider_name_filter_uses_plain_tracker_search_categories(monkeypatch):
    good = [_R(title="g", size=1, seeders=3, leechers=0, grabs=0, indexer_name="The Pirate Bay", indexer_id=1,
               protocol="torrent", magnet_uri="magnet:g", download_url=None, guid="g")]
    calls = []

    class _Client:
        def is_configured(self):
            return True

        def _get_indexers_sync(self):
            return [_R(id=1, name="The Pirate Bay", protocol="torrent", enable=True)]

        def _search_sync(self, q, cats, ids, limit, search_type="search", extra_params=None,
                         max_wait_seconds=None):
            calls.append({"cats": cats, "ids": ids, "type": search_type})
            return list(good)

    monkeypatch.setattr(ps, "_client", lambda: _Client())
    monkeypatch.setattr(ps, "_indexer_ids", lambda: [])
    out = ps.prowlarr_search("movie", "Interstellar", source="torrent", indexer_names=["the pirate bay"])
    assert out["hits"][0]["username"] == "The Pirate Bay"
    assert calls and all(c["ids"] == [1] for c in calls)
    assert all(c["cats"] == [] for c in calls)


def test_manual_query_summary_uses_prowlarr_strategies():
    from api.video.downloads import _search_queries

    got = _search_queries({"year": 2020, "tvdb_id": 555}, "torrent", "episode", "The Show", 1, 2)
    assert "tvsearch: The Show season=1 ep=2 tvdbid=555" in got
    assert "search: The Show S01E02" in got


def test_manual_query_summary_uses_text_query_for_slskd():
    from api.video.downloads import _search_queries

    assert _search_queries({}, "soulseek", "season", "The Show", 2, None) == ["The Show S02"]
