"""Searching "Auli'i Cravalho How Far I'll Go" in a search box must offer the original.

Deezer's plain search returns the Reprise and karaoke copies and leaves the
original (136340808) out of the first 8-10 results, so the library match modal
and the manual search page could not show it. When the query names an artist
(found from the plain results' own artist names) the exact-title search
`track:"title" artist` runs too and its results go first.
"""

from __future__ import annotations

import types

import pytest

from core.deezer_client import DeezerClient
from core.deezer_track_query import artist_scoped_query, merge_by_id
from core.metadata.song_search import search_typed_query

QUERY = "Auli'i Cravalho How Far I'll Go"


def _track(track_id, name, artist):
    return types.SimpleNamespace(
        id=str(track_id), name=name, artists=[artist], album="Moana", image_url="",
        duration_ms=163000, external_urls={}, release_date="2016-11-18",
    )


REPRISE = _track(136340812, "How Far I'll Go (Reprise)", "Auli'i Cravalho")
KARAOKE = _track(3890762711, "How Far I'll Go (Karaoke Version)", "karaoke SESH")
ORIGINAL = _track(136340808, "How Far I'll Go", "Auli'i Cravalho")


class FakeDeezer(DeezerClient):
    """A DeezerClient whose network search is scripted."""

    def __init__(self, scoped_raises=False):  # noqa: D401 - no super().__init__: no network, no config
        self.queries = []
        self._scoped_raises = scoped_raises

    def search_tracks(self, query="", limit=20, **kwargs):
        self.queries.append(query)
        if query.startswith("track:"):
            if self._scoped_raises:
                raise RuntimeError("boom")
            return [ORIGINAL, REPRISE]
        return [REPRISE, KARAOKE]


# ── helpers ──────────────────────────────────────────────────────────────────

def test_scoped_query_only_when_the_query_names_an_artist():
    assert artist_scoped_query(QUERY, ["Auli'i Cravalho"]) == 'track:"how far ill go" aulii cravalho'
    assert artist_scoped_query("How Far I'll Go", ["Auli'i Cravalho"]) is None


def test_a_query_that_is_only_an_artist_name_is_left_alone():
    # "Taylor Swift" must not become track:"taylor swift"
    assert artist_scoped_query("Taylor Swift", ["Taylor Swift"]) is None


def test_merge_by_id_keeps_the_first_of_each_and_honours_limit():
    a, b = {"id": 1}, {"id": 2}
    assert merge_by_id([a], [{"id": 1}, b]) == [a, b]
    assert merge_by_id([a], [b], limit=1) == [a]
    objs = merge_by_id([ORIGINAL], [REPRISE, ORIGINAL])
    assert [o.id for o in objs] == ["136340808", "136340812"]


# ── search_typed_query ───────────────────────────────────────────────────────

def test_original_comes_first_when_the_query_names_the_artist():
    client = FakeDeezer()
    out = search_typed_query(client, QUERY, limit=8)

    assert out[0].id == "136340808"
    assert client.queries == [QUERY, 'track:"how far ill go" aulii cravalho']
    ids = [t.id for t in out]
    assert len(ids) == len(set(ids))
    assert "3890762711" in ids  # the plain results are still there


def test_title_only_query_is_unchanged_and_costs_no_extra_request():
    client = FakeDeezer()
    out = search_typed_query(client, "How Far I'll Go", limit=8)
    assert out == [REPRISE, KARAOKE]
    assert client.queries == ["How Far I'll Go"]


def test_a_failing_exact_title_search_returns_the_plain_results():
    client = FakeDeezer(scoped_raises=True)
    assert search_typed_query(client, QUERY, limit=8) == [REPRISE, KARAOKE]


def test_other_sources_are_untouched():
    calls = []

    class Other:
        def search_tracks(self, query, limit=10, **kw):
            calls.append((query, kw))
            return [REPRISE]

    assert search_typed_query(Other(), QUERY, limit=10, prefer_free=True) == [REPRISE]
    assert calls == [(QUERY, {"prefer_free": True})]


def test_extra_kwargs_skip_the_deezer_extension():
    client = FakeDeezer()
    search_typed_query(client, QUERY, limit=8, track="x")
    assert client.queries == [QUERY]


# ── the manual search page ───────────────────────────────────────────────────

def test_manual_search_page_lists_the_original_first():
    from core.search.sources import search_kind

    out = search_kind(FakeDeezer(), QUERY, "tracks")
    assert out[0]["id"] == "136340808"
    assert out[0]["name"] == "How Far I'll Go"


# ── the library match modal (direct Deezer branch) ───────────────────────────

def _item(track_id, title, artist):
    return {"id": track_id, "title": title, "artist": {"name": artist},
            "album": {"title": "Moana", "cover_medium": None}}


def test_library_match_modal_lists_the_original_first(monkeypatch):
    import requests

    from core.library import service_search

    seen = []

    class Resp:
        def __init__(self, data):
            self._d = data

        def json(self):
            return {"data": self._d}

    def get(url, params=None, timeout=None):
        seen.append(params["q"])
        if params["q"].startswith("track:"):
            return Resp([_item(136340808, "How Far I'll Go", "Auli'i Cravalho"),
                         _item(136340812, "How Far I'll Go (Reprise)", "Auli'i Cravalho")])
        return Resp([_item(136340812, "How Far I'll Go (Reprise)", "Auli'i Cravalho"),
                     _item(3890762711, "How Far I'll Go (Karaoke Version)", "karaoke SESH")])

    monkeypatch.setattr(requests, "get", get)
    import core.deezer_throttle as throttle

    monkeypatch.setattr(throttle, "wait_for_slot", lambda *a, **k: True)

    out = service_search._search_service("deezer", "track", QUERY)

    assert out[0]["id"] == "136340808"
    assert [r["id"] for r in out] == ["136340808", "136340812", "3890762711"]
    assert seen == [QUERY, 'track:"how far ill go" aulii cravalho']


def test_library_match_modal_leaves_artist_and_album_searches_alone(monkeypatch):
    import requests

    from core.library import service_search

    seen = []

    class Resp:
        def json(self):
            return {"data": [{"id": 1, "name": "Auli'i Cravalho", "picture_medium": None, "nb_fan": 5}]}

    monkeypatch.setattr(requests, "get", lambda url, params=None, timeout=None: seen.append(params["q"]) or Resp())
    import core.deezer_throttle as throttle

    monkeypatch.setattr(throttle, "wait_for_slot", lambda *a, **k: True)

    service_search._search_service("deezer", "artist", QUERY)
    assert seen == [QUERY]


# ── the artist's own tracks lead the scoped list ─────────────────────────────

from core.deezer_track_query import credits_artist  # noqa: E402

COVER = _track(1, "Love Story (Bonus Track)", "Vitamin String Quartet")
TS_VERSION = _track(2, "Love Story (Taylor's Version)", "Taylor Swift")
TS_ORIGINAL = _track(3, "Love Story", "Taylor Swift")


class FakeDeezerScopedCovers(DeezerClient):
    """Plain results include the artist; Deezer's scoped list still has a cover first."""

    def __init__(self):  # noqa: D401
        self.queries = []

    def search_tracks(self, query="", limit=20, **kwargs):
        self.queries.append(query)
        if query.startswith("track:"):
            return [COVER, TS_VERSION, TS_ORIGINAL]
        return [COVER, TS_VERSION]


def test_credits_artist_matches_whole_names_only():
    assert credits_artist(["Beyoncé"], "beyonce")
    assert not credits_artist(["Beyonce Experience"], "beyonce")


def test_the_named_artists_own_tracks_lead_the_scoped_list():
    client = FakeDeezerScopedCovers()
    out = search_typed_query(client, "Taylor Swift Love Story", limit=10)
    assert [t.id for t in out] == ["2", "3", "1"]
    assert len(client.queries) == 2  # the plain search and one scoped search, no lookups


def test_scoped_results_not_credited_to_the_artist_are_dropped():
    class Wrong(FakeDeezerScopedCovers):
        def search_tracks(self, query="", limit=20, **kwargs):
            self.queries.append(query)
            return [COVER] if query.startswith("track:") else [COVER, TS_VERSION]

    out = search_typed_query(Wrong(), "Taylor Swift Love Story", limit=10)
    assert [t.id for t in out] == ["1", "2"]  # plain order, scoped list ignored


def test_no_artist_in_the_plain_results_means_no_extra_request():
    client = FakeDeezerScopedCovers()
    client.search_tracks = lambda query="", limit=20, **kw: client.queries.append(query) or [COVER]
    out = search_typed_query(client, "Taylor Swift Love Story", limit=10)
    assert out == [COVER]
    assert client.queries == ["Taylor Swift Love Story"]
