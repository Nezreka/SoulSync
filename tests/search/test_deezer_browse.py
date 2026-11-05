"""Search browse tiles from Deezer's public charts (cremonies, #1984)."""

from __future__ import annotations

import pytest

from core.search import deezer_browse as db


def _track(i, title, artist="A"):
    return {"id": i, "title": title, "duration": 200, "rank": 9,
            "artist": {"name": artist}, "album": {"id": i * 10, "title": f"{title} LP",
                                                  "cover_medium": f"http://c/{i}"}}


def _album(i, title, artist="A", date=None):
    return {"id": i, "title": title, "artist": {"name": artist}, "cover_big": f"http://b/{i}",
            "record_type": "album", **({"release_date": date} if date else {})}


class _Client:
    def __init__(self, answers):
        self.answers, self.calls = answers, []

    def _api_get(self, endpoint, params=None, use_token=True):
        assert use_token is False, "public endpoints never send a token"
        self.calls.append(endpoint)
        return {"data": self.answers.get(endpoint, [])}


@pytest.fixture(autouse=True)
def _fresh_cache(monkeypatch):
    monkeypatch.setattr(db, "_cache", {})


def test_a_two_genre_tile_interleaves_both_charts():
    client = _Client({
        "chart/106/tracks": [_track(1, "Electro 1"), _track(2, "Electro 2")],
        "chart/113/tracks": [_track(3, "Dance 1"), _track(1, "Electro 1")],
        "chart/106/albums": [_album(5, "E LP")],
    })
    out = db.build_tile(client, "genre:electronic")
    assert [t["name"] for t in out["tracks"]] == ["Electro 1", "Dance 1", "Electro 2"]
    assert out["tracks"][0] == {
        "entity_id": "1", "name": "Electro 1", "artist_name": "A", "album_name": "Electro 1 LP",
        "album_id": "10", "duration_ms": 200_000, "image_url": "http://c/1", "source": "deezer",
        "popularity": 9}
    assert [a["name"] for a in out["albums"]] == ["E LP"]


def test_new_releases_sort_newest_first():
    client = _Client({"editorial/0/releases": [_album(1, "Old", date="2026-10-01"),
                                               _album(2, "New", date="2026-11-03")]})
    assert [a["name"] for a in db.build_tile(client, "new")["albums"]] == ["New", "Old"]


@pytest.mark.parametrize("tile", ["genre:ambient", "genre:unknown", ""])
def test_a_tile_deezer_has_no_genre_for_keeps_the_cache(tile):
    client = _Client({})
    assert db.build_tile(client, tile) is None
    assert client.calls == []


def test_nothing_back_is_none_and_is_not_kept():
    client = _Client({})
    assert db.browse_tile(client, "charts", now=1000) is None
    client.answers["chart/0/tracks"] = [_track(1, "Hit")]
    assert db.browse_tile(client, "charts", now=1001)["tracks"][0]["name"] == "Hit"


def test_an_answer_is_kept_for_hours():
    client = _Client({"chart/0/tracks": [_track(1, "Hit")]})
    db.browse_tile(client, "charts", now=0)
    calls = len(client.calls)
    db.browse_tile(client, "charts", now=db.CACHE_SECONDS - 1)
    assert len(client.calls) == calls
    db.browse_tile(client, "charts", now=db.CACHE_SECONDS + 1)
    assert len(client.calls) > calls


def test_no_client_is_no_answer():
    assert db.build_tile(None, "charts") is None
