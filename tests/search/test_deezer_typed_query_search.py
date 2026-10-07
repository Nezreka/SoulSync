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


# ── artist not among the plain results ("Taylor Swift Love Story") ───────────

from core.deezer_track_query import artist_lookup_phrases, credits_artist, exact_artist_match  # noqa: E402

COVER = _track(1, "Love Story (Bonus Track)", "Vitamin String Quartet")
TS_VERSION = _track(2, "Love Story (Taylor's Version)", "Taylor Swift")
TS_ORIGINAL = _track(3, "Love Story", "Taylor Swift")


class FakeDeezerNoArtist(DeezerClient):
    """Plain results are all covers; the artist is only found by a lookup."""

    def __init__(self, artists=("Taylor Swift",), scoped=None):  # noqa: D401
        self.queries, self.artist_lookups = [], []
        self._artists = list(artists)
        self._scoped = scoped if scoped is not None else [COVER, TS_VERSION, TS_ORIGINAL]

    def search_tracks(self, query="", limit=20, **kwargs):
        self.queries.append(query)
        return list(self._scoped) if query.startswith("track:") else [COVER]

    def search_artists(self, query, limit=20):
        self.artist_lookups.append(query)
        return [types.SimpleNamespace(name=n) for n in self._artists if n.lower().startswith(query.lower())]


def test_helpers_for_artist_lookup():
    assert artist_lookup_phrases("Taylor Swift Love Story") == ["Taylor Swift", "Love Story", "Taylor", "Story"]
    assert artist_lookup_phrases("Halo") == []
    assert len(artist_lookup_phrases("a b c d e f g h")) <= 4
    assert exact_artist_match("taylor swift", ["Taylor Swift - Piano Covers", "Taylor Swift"]) == "Taylor Swift"
    assert exact_artist_match("Taylor Swif", ["Taylor Swift"]) is None
    assert credits_artist(["Beyoncé"], "beyonce") and not credits_artist(["Beyonce Experience"], "beyonce")


def test_artist_found_by_lookup_puts_her_own_tracks_first():
    client = FakeDeezerNoArtist()
    out = search_typed_query(client, "Taylor Swift Love Story", limit=10)

    assert [t.id for t in out[:2]] == ["2", "3"]  # Taylor Swift's own tracks lead
    assert out[2].id == "1"                       # the cover is still listed, after
    assert client.artist_lookups == ["Taylor Swift"]  # stopped at the first hit


def test_an_artist_lookup_that_hits_the_wrong_name_is_dropped():
    # an artist literally named "Love Story" exists, but nothing is credited to it
    client = FakeDeezerNoArtist(artists=("Love Story",), scoped=[COVER, TS_ORIGINAL])
    out = search_typed_query(client, "Taylor Swift Love Story", limit=10)
    assert out == [COVER]  # plain results unchanged


def test_no_artist_anywhere_costs_at_most_four_lookups_and_changes_nothing():
    client = FakeDeezerNoArtist(artists=())
    out = search_typed_query(client, "How Far I'll Go Tonight", limit=10)
    assert out == [COVER]
    assert 0 < len(client.artist_lookups) <= 4
    assert not any(q.startswith("track:") for q in client.queries)


def test_a_failing_artist_lookup_returns_the_plain_results():
    client = FakeDeezerNoArtist()
    client.search_artists = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom"))
    assert search_typed_query(client, "Taylor Swift Love Story", limit=10) == [COVER]


def test_library_match_modal_finds_the_artist_by_lookup_and_lists_her_first(monkeypatch):
    import requests

    from core.library import service_search

    cover = _item(1, "Love Story (Bonus Track)", "Vitamin String Quartet")
    ts_version = _item(2, "Love Story (Taylor's Version)", "Taylor Swift")
    ts_orig = _item(3, "Love Story", "Taylor Swift")
    seen = []

    class Resp:
        def __init__(self, data):
            self._d = data

        def json(self):
            return {"data": self._d}

    def get(url, params=None, timeout=None):
        seen.append((url.rsplit("/", 1)[-1], params["q"]))
        if url.endswith("/artist"):
            return Resp([{"id": 9, "name": "Taylor Swift"}] if params["q"] == "Taylor Swift" else [])
        if params["q"].startswith("track:"):
            return Resp([cover, ts_version, ts_orig])
        return Resp([cover])

    monkeypatch.setattr(requests, "get", get)
    import core.deezer_throttle as throttle

    monkeypatch.setattr(throttle, "wait_for_slot", lambda *a, **k: True)

    out = service_search._search_service("deezer", "track", "Taylor Swift Love Story")

    assert [r["id"] for r in out] == ["2", "3", "1"]
    assert ("artist", "Taylor Swift") in seen
