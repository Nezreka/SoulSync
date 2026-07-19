"""Metadata edit engine: local write + lock first, best-effort server push
second (Plex per-field locks / Jellyfin LockedFields) — an unreachable server
never loses an edit."""

from __future__ import annotations

import pytest

from core.video import metadata as med
from core.video.sources import JellyfinVideoSource, PlexVideoSource
from database.video_database import VideoDatabase


@pytest.fixture()
def db(tmp_path):
    return VideoDatabase(database_path=str(tmp_path / "video_library.db"))


def _seed_movie(db, server="plex"):
    return db.upsert_movie(server, {"server_id": "m1", "title": "Server Title", "year": 1999,
                                    "genres": ["Action"], "file": {"path": "/x.mkv"}})


def _seed_show(db):
    return db.upsert_show_tree("plex", {
        "server_id": "s1", "title": "Show", "seasons": [
            {"season_number": 1, "episodes": [
                {"episode_number": 1, "title": "Ep1", "server_id": "e1"},
                {"episode_number": 2, "title": "Ep2", "server_id": "e2"}]}]})


class FakeSource:
    server_name = "plex"

    def __init__(self, ok=True):
        self.ok = ok
        self.edits = []
        self.watched = []

    def edit_item_metadata(self, server_id, changes, kind="movie", unlock_fields=None):
        self.edits.append((server_id, dict(changes), kind, list(unlock_fields or [])))
        return {"ok": self.ok} if self.ok else {"ok": False, "error": "boom"}

    def set_watched(self, server_id, watched, kind="movie"):
        self.watched.append((server_id, watched, kind))
        return {"ok": self.ok}


# ── service orchestration ────────────────────────────────────────────────────
def test_edit_writes_locks_and_pushes(db):
    mid = _seed_movie(db)
    src = FakeSource()
    res = med.edit_item(db, "movie", mid, {"title": "Mine", "year": 2001}, source=src)
    assert res["ok"] and res["pushed"] and "title" in res["locked"]
    assert src.edits == [("m1", {"title": "Mine", "year": 2001}, "movie", [])]


def test_push_failure_keeps_the_local_edit(db):
    mid = _seed_movie(db)
    res = med.edit_item(db, "movie", mid, {"title": "Mine"}, source=FakeSource(ok=False))
    assert res["ok"] and res["pushed"] is False and res["push_error"] == "boom"
    assert db.get_locked_fields("movie", mid) == ["sort_title", "title"]


def test_wrong_or_missing_server_skips_push(db):
    mid = _seed_movie(db)
    jf = FakeSource()
    jf.server_name = "jellyfin"                       # item lives on plex
    res = med.edit_item(db, "movie", mid, {"title": "Mine"}, source=jf)
    assert res["ok"] and res["pushed"] is False and jf.edits == []
    res = med.edit_item(db, "movie", mid, {"tagline": "x"}, source=None)
    assert res["ok"] and res["pushed"] is False


def test_release_lock_pushes_server_unlock(db):
    mid = _seed_movie(db)
    src = FakeSource()
    med.edit_item(db, "movie", mid, {"title": "Mine"}, source=src)
    res = med.release_lock(db, "movie", mid, "title", source=src)
    assert res["ok"] and res["locked"] == ["sort_title"]
    assert src.edits[-1] == ("m1", {}, "movie", ["title"])


def test_set_watched_movie_and_show(db):
    mid, sid = _seed_movie(db), _seed_show(db)
    src = FakeSource()
    assert med.set_watched(db, "movie", mid, True, source=src)["pushed"]
    assert med.set_watched(db, "show", sid, True, source=src)["ok"]
    conn = db._get_connection()
    try:
        assert conn.execute("SELECT play_count FROM movies WHERE id=?", (mid,)).fetchone()[0] == 1
        assert conn.execute("SELECT watched_episodes FROM shows WHERE id=?",
                            (sid,)).fetchone()[0] == 2
    finally:
        conn.close()
    med.set_watched(db, "movie", mid, False, source=src)
    conn = db._get_connection()
    try:
        assert conn.execute("SELECT play_count FROM movies WHERE id=?", (mid,)).fetchone()[0] == 0
    finally:
        conn.close()
    assert src.watched == [("m1", True, "movie"), ("s1", True, "show"), ("m1", False, "movie")]


# ── Plex adapter mapping ─────────────────────────────────────────────────────
class _Tag:
    def __init__(self, tag):
        self.tag = tag


class _PlexItem:
    def __init__(self):
        self.edit_calls = []
        self.genres = [_Tag("Action"), _Tag("Horror")]
        self.tag_calls = []
        self.played = []

    def edit(self, **kw):
        self.edit_calls.append(kw)

    def addGenre(self, genres, locked=True):
        self.tag_calls.append(("add", list(genres), locked))

    def removeGenre(self, genres, locked=True):
        self.tag_calls.append(("remove", list(genres), locked))

    def markPlayed(self):
        self.played.append(True)

    def markUnplayed(self):
        self.played.append(False)


class _PlexServer:
    def __init__(self, item):
        self._item = item

    def fetchItem(self, rk):
        assert rk == 42
        return self._item


def test_plex_edit_maps_fields_and_locks():
    item = _PlexItem()
    src = PlexVideoSource(_PlexServer(item))
    res = src.edit_item_metadata(42, {"title": "T", "sort_title": "t", "year": 2001,
                                      "content_rating": "PG-13", "overview": "o",
                                      "tagline": "tag", "network": "HBO",
                                      "genres": ["Action", "Comfort"]},
                                 kind="movie", unlock_fields=["overview"])
    assert res["ok"] and res["skipped"] == ["network"]           # Plex can't edit network
    kw = item.edit_calls[0]
    assert kw["title.value"] == "T" and kw["title.locked"] == 1
    assert kw["titleSort.value"] == "t" and kw["contentRating.value"] == "PG-13"
    assert kw["summary.locked"] == 0                             # unlock wins over edit
    assert item.tag_calls == [("remove", ["Horror"], True), ("add", ["Comfort"], True)]


def test_plex_set_watched():
    item = _PlexItem()
    src = PlexVideoSource(_PlexServer(item))
    assert src.set_watched(42, True)["ok"] and src.set_watched(42, False)["ok"]
    assert item.played == [True, False]


# ── Jellyfin adapter mapping ─────────────────────────────────────────────────
class _JfClient:
    user_id = "u1"
    base_url = "http://jf"
    api_key = "k"

    def __init__(self, dto):
        self._dto = dto

    def _make_request(self, path, params=None):
        return dict(self._dto)


def test_jellyfin_edit_full_dto_roundtrip(monkeypatch):
    posted = {}

    def fake_post(url, json=None, headers=None, timeout=None):
        posted["url"], posted["dto"] = url, json

        class R:
            def raise_for_status(self):
                pass
        return R()

    import requests
    monkeypatch.setattr(requests, "post", fake_post)
    src = JellyfinVideoSource(_JfClient({"Id": "j1", "Name": "Old",
                                         "LockedFields": ["Overview"]}))
    res = src.edit_item_metadata("j1", {"title": "New", "sort_title": "new", "year": 2001,
                                        "content_rating": "TV-MA", "tagline": "t",
                                        "genres": ["Drama"], "network": "HBO"},
                                 kind="show", unlock_fields=["overview"])
    assert res["ok"] and res["skipped"] == []
    dto = posted["dto"]
    assert posted["url"] == "http://jf/Items/j1"
    assert dto["Name"] == "New" and dto["ForcedSortName"] == "new"
    assert dto["ProductionYear"] == 2001 and dto["OfficialRating"] == "TV-MA"
    assert dto["Taglines"] == ["t"] and dto["Genres"] == ["Drama"]
    assert dto["Studios"] == [{"Name": "HBO"}]
    # Name/Genres/Studios/OfficialRating locked; Overview lock RELEASED.
    assert dto["LockedFields"] == ["Genres", "Name", "OfficialRating", "Studios"]


def test_jellyfin_set_watched(monkeypatch):
    calls = []

    def fake(url, headers=None, timeout=None):
        calls.append(url)

        class R:
            def raise_for_status(self):
                pass
        return R()

    import requests
    monkeypatch.setattr(requests, "post", fake)
    monkeypatch.setattr(requests, "delete", fake)
    src = JellyfinVideoSource(_JfClient({"Id": "j1"}))
    assert src.set_watched("j1", True)["ok"] and src.set_watched("j1", False)["ok"]
    assert calls == ["http://jf/Users/u1/PlayedItems/j1"] * 2
