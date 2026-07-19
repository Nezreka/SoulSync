"""Labels P2 (backend) — the /api/labels/* blueprint.

Purely additive, self-contained. These pin the HTTP contract the search page +
label-detail view will call: search, catalog (grouped by real artist), and the
follow/unfollow/backlog watchlist toggles backed by watchlist_labels. Hermetic
— a real tmp MusicDatabase + a faked MusicBrainz client, no network.
"""

from __future__ import annotations

import pytest
from flask import Flask

from api import labels as labels_api
from database.music_database import MusicDatabase


class _FakeMB:
    def search_labels(self, name, limit=10):
        return [{"id": "mbid-subpop", "name": "Sub Pop",
                 "disambiguation": "US indie", "type": "Production",
                 "area": {"name": "United States"}}]

    def browse_label_releases(self, mbid, limit=100, offset=0):
        if offset:
            return []
        def rel(artist, title, rg, year):
            return {"title": title,
                    "artist-credit": [{"name": artist, "joinphrase": ""}],
                    "release-group": {"id": rg, "title": title,
                                      "primary-type": "Album",
                                      "secondary-types": [],
                                      "first-release-date": year}}
        return [
            rel("Beach House", "Teen Dream", "rg-teen", "2010"),
            rel("Beach House", "Teen Dream", "rg-teen", "2011"),   # reissue, same rg
            rel("Mudhoney", "Superfuzz Bigmuff", "rg-fuzz", "1988"),
            rel("Nirvana", "Bleach", "rg-bleach", "1989"),
        ]


@pytest.fixture()
def client(tmp_path):
    db = MusicDatabase(str(tmp_path / "music.db"))
    labels_api._catalog_cache.clear()
    labels_api.configure(db_getter=lambda: db, mb_getter=lambda: _FakeMB())
    app = Flask(__name__)
    app.register_blueprint(labels_api.create_blueprint())
    app.config.update(TESTING=True)
    c = app.test_client()
    c._db = db
    yield c
    labels_api._catalog_cache.clear()


class TestSearch:
    def test_search_returns_mapped_labels(self, client):
        r = client.post("/api/labels/search", json={"query": "sub pop"})
        assert r.status_code == 200
        labels = r.get_json()["labels"]
        assert labels[0]["id"] == "mbid-subpop" and labels[0]["name"] == "Sub Pop"
        assert labels[0]["area"] == "United States"
        assert labels[0]["is_watching"] is False

    def test_empty_query_no_search(self, client):
        r = client.post("/api/labels/search", json={"query": "   "})
        assert r.get_json()["labels"] == []

    def test_search_marks_watched(self, client):
        client._db.add_watchlist_label("mbid-subpop", "Sub Pop")
        r = client.post("/api/labels/search", json={"query": "sub"})
        assert r.get_json()["labels"][0]["is_watching"] is True


class TestCatalog:
    def test_releases_flat_newest_first(self, client):
        r = client.get("/api/labels/mbid-subpop/catalog?name=Sub+Pop")
        data = r.get_json()
        assert data["label"]["name"] == "Sub Pop"
        assert data["total"] == 3                    # Teen Dream editions collapsed
        assert data["artist_count"] == 3
        rels = data["releases"]
        assert [x["album"] for x in rels] == ["Teen Dream", "Bleach", "Superfuzz Bigmuff"]
        assert rels[0]["artist"] == "Beach House"    # newest first, real artist
        # every release carries a real artist, never the label
        for x in rels:
            assert x["artist"] != "Sub Pop"

    def test_catalog_pagination(self, client):
        p1 = client.get("/api/labels/mbid-subpop/catalog?page=1&page_size=2").get_json()
        assert len(p1["releases"]) == 2 and p1["has_more"] is True and p1["page"] == 1
        p2 = client.get("/api/labels/mbid-subpop/catalog?page=2&page_size=2").get_json()
        assert len(p2["releases"]) == 1 and p2["has_more"] is False
        # no overlap between pages
        a1 = {x["album"] for x in p1["releases"]}
        a2 = {x["album"] for x in p2["releases"]}
        assert a1.isdisjoint(a2)

    def test_catalog_reports_watch_state(self, client):
        client._db.add_watchlist_label("mbid-subpop", "Sub Pop", backlog=True)
        data = client.get("/api/labels/mbid-subpop/catalog").get_json()
        assert data["is_watching"] is True and data["backlog"] is True
        assert data["label"]["name"] == "Sub Pop"   # filled from the watchlist row

    def test_catalog_memoized(self, client):
        # the second GET must hit the TTL cache, not re-walk MB
        client.get("/api/labels/mbid-x/catalog")
        first = dict(labels_api._catalog_cache)
        client.get("/api/labels/mbid-x/catalog")
        assert "mbid-x" in labels_api._catalog_cache
        assert labels_api._catalog_cache["mbid-x"]["at"] == first["mbid-x"]["at"]


class TestWatchlistToggles:
    def test_add_check_remove_cycle(self, client):
        chk = client.post("/api/labels/watchlist/check",
                          json={"musicbrainz_label_id": "m1"}).get_json()
        assert chk["is_watching"] is False

        add = client.post("/api/labels/watchlist/add",
                         json={"musicbrainz_label_id": "m1", "label_name": "Ninja Tune"}).get_json()
        assert add["success"] is True and add["is_watching"] is True

        chk = client.post("/api/labels/watchlist/check",
                          json={"musicbrainz_label_id": "m1"}).get_json()
        assert chk["is_watching"] is True

        rm = client.post("/api/labels/watchlist/remove",
                        json={"musicbrainz_label_id": "m1"}).get_json()
        assert rm["success"] is True and rm["is_watching"] is False
        assert client._db.is_label_in_watchlist("m1") is False

    def test_add_requires_id_and_name(self, client):
        r = client.post("/api/labels/watchlist/add", json={"musicbrainz_label_id": "m"})
        assert r.status_code == 400

    def test_backlog_toggle(self, client):
        client.post("/api/labels/watchlist/add",
                    json={"musicbrainz_label_id": "m2", "label_name": "Warp"})
        r = client.post("/api/labels/watchlist/backlog",
                        json={"musicbrainz_label_id": "m2", "backlog": True})
        assert r.get_json()["backlog"] is True
        chk = client.post("/api/labels/watchlist/check",
                          json={"musicbrainz_label_id": "m2"}).get_json()
        assert chk["backlog"] is True

    def test_watchlist_list(self, client):
        client.post("/api/labels/watchlist/add",
                    json={"musicbrainz_label_id": "m3", "label_name": "Stones Throw"})
        r = client.get("/api/labels/watchlist")
        names = [l["label_name"] for l in r.get_json()["labels"]]
        assert "Stones Throw" in names
