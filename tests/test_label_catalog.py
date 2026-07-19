"""Labels P1 — the label catalog data layer + watchlist table.

Purely additive: a new module + a new DB table. These pin the two things that
matter: the watchlist CRUD is self-contained, and the catalog collapses a
label's releases into DISTINCT albums that each carry the REAL artist (never
the label). Hermetic — the MusicBrainz client is faked, no network.
"""

from __future__ import annotations

import pytest

from core.metadata import label_catalog as lc
from database.music_database import MusicDatabase


@pytest.fixture()
def db(tmp_path):
    return MusicDatabase(str(tmp_path / "music.db"))


class TestWatchlistTable:
    def test_add_list_remove_is_watched(self, db):
        assert db.is_label_in_watchlist("mbid-1") is False
        assert db.add_watchlist_label("mbid-1", "Sub Pop") is True
        assert db.is_label_in_watchlist("mbid-1") is True
        rows = db.get_watchlist_labels()
        assert len(rows) == 1 and rows[0]["label_name"] == "Sub Pop"
        assert rows[0]["backlog"] is False
        assert db.remove_watchlist_label("mbid-1") is True
        assert db.is_label_in_watchlist("mbid-1") is False

    def test_idempotent_follow_updates_not_dupes(self, db):
        db.add_watchlist_label("m", "Ninja Tune")
        db.add_watchlist_label("m", "Ninja Tune Ltd", backlog=True)   # same mbid
        rows = db.get_watchlist_labels()
        assert len(rows) == 1
        assert rows[0]["label_name"] == "Ninja Tune Ltd" and rows[0]["backlog"] is True

    def test_backlog_toggle_and_scan_stamp(self, db):
        db.add_watchlist_label("m", "Stones Throw")
        assert db.set_watchlist_label_backlog("m", True) is True
        assert db.get_watchlist_labels()[0]["backlog"] is True
        db.mark_watchlist_label_scanned("m")
        assert db.get_watchlist_labels()[0]["last_scan_timestamp"] is not None

    def test_junk_never_inserts(self, db):
        assert db.add_watchlist_label("", "x") is False
        assert db.add_watchlist_label("m", "") is False
        assert db.get_watchlist_labels() == []

    def test_table_is_additive_only(self, db):
        # the labels table must not have touched artists/albums/tracks/watchlist_artists
        with db._get_connection() as c:
            names = {r[0] for r in c.execute(
                "SELECT name FROM sqlite_master WHERE type='table'")}
        assert "watchlist_labels" in names
        assert {"artists", "albums", "tracks", "watchlist_artists"} <= names


class _FakeMB:
    def __init__(self, pages):
        self._pages = pages          # list of release-lists, one per page
        self.searched = []

    def search_labels(self, name, limit=10):
        self.searched.append(name)
        return [{"id": "mbid-subpop", "name": "Sub Pop",
                 "disambiguation": "US indie", "type": "Production",
                 "area": {"name": "United States"}}]

    def browse_label_releases(self, mbid, limit=100, offset=0):
        idx = offset // 100
        return self._pages[idx] if idx < len(self._pages) else []


def _rel(artist, title, rg_id, year, primary="Album", secondary=None, join=None):
    credit = [{"name": artist, "joinphrase": join or ""}]
    if join:
        credit.append({"name": "Guest"})
    return {"title": title, "artist-credit": credit,
            "release-group": {"id": rg_id, "title": title, "primary-type": primary,
                              "secondary-types": secondary or [],
                              "first-release-date": year}}


class TestCatalog:
    def test_search_maps_labels(self):
        fake = _FakeMB([])
        out = lc.search_labels("sub pop", mb_getter=lambda: fake)
        assert out[0]["id"] == "mbid-subpop" and out[0]["name"] == "Sub Pop"
        assert out[0]["area"] == "United States"
        assert fake.searched == ["sub pop"]

    def test_collapses_editions_to_distinct_albums(self):
        # two editions of ONE album (same release-group) → one entry
        fake = _FakeMB([[
            _rel("Beach House", "Teen Dream", "rg-teen", "2010-01-26"),
            _rel("Beach House", "Teen Dream", "rg-teen", "2011-05-01"),  # reissue, same rg
            _rel("Mudhoney", "Superfuzz Bigmuff", "rg-fuzz", "1988"),
        ]])
        cat = lc.label_catalog("mbid", mb_getter=lambda: fake)
        assert len(cat) == 2                       # deduped by release-group
        teen = [c for c in cat if c["album"] == "Teen Dream"][0]
        assert teen["artist"] == "Beach House" and teen["year"] == "2010"  # earliest kept

    def test_items_carry_real_artist_never_label(self):
        fake = _FakeMB([[_rel("Nirvana", "Bleach", "rg-bleach", "1989")]])
        cat = lc.label_catalog("mbid", mb_getter=lambda: fake)
        assert cat[0]["artist"] == "Nirvana"       # the real artist, not 'Sub Pop'

    def test_captures_artist_mbid_when_present(self):
        rel = {"title": "Bleach",
               "artist-credit": [{"name": "Nirvana", "joinphrase": "",
                                  "artist": {"id": "5b11f4ce-a62d-471e-81fc-a69a8278c7da",
                                             "name": "Nirvana"}}],
               "release-group": {"id": "rg-b", "title": "Bleach",
                                 "primary-type": "Album", "secondary-types": [],
                                 "first-release-date": "1989"}}
        cat = lc.label_catalog("mbid", mb_getter=lambda: _FakeMB([[rel]]))
        assert cat[0]["artist_id"] == "5b11f4ce-a62d-471e-81fc-a69a8278c7da"
        # and a credit with no artist object degrades gracefully to ''
        cat2 = lc.label_catalog("mbid", mb_getter=lambda: _FakeMB([[_rel("X", "Y", "rg-y", "2020")]]))
        assert cat2[0]["artist_id"] == ""

    def test_multi_artist_credit_joined(self):
        fake = _FakeMB([[_rel("A", "Split", "rg-split", "2000", join=" / ")]])
        cat = lc.label_catalog("mbid", mb_getter=lambda: fake)
        assert cat[0]["artist"] == "A / Guest"

    def test_filters_non_albums_and_comps(self):
        fake = _FakeMB([[
            _rel("X", "A Single", "rg-s", "2020", primary="Single"),
            _rel("X", "Best Of", "rg-c", "2021", secondary=["Compilation"]),
            _rel("X", "Live at Y", "rg-l", "2019", secondary=["Live"]),
            _rel("X", "Real EP", "rg-ep", "2022", primary="EP"),
            _rel("X", "Real Album", "rg-a", "2023"),
        ]])
        cat = lc.label_catalog("mbid", mb_getter=lambda: fake)
        titles = {c["album"] for c in cat}
        assert titles == {"Real EP", "Real Album"}

    def test_newest_first(self):
        fake = _FakeMB([[
            _rel("X", "Old", "rg-o", "1999"),
            _rel("X", "New", "rg-n", "2024"),
        ]])
        cat = lc.label_catalog("mbid", mb_getter=lambda: fake)
        assert [c["album"] for c in cat] == ["New", "Old"]

    def test_paging_stops_on_short_page(self):
        calls = []

        class _Counting(_FakeMB):
            def browse_label_releases(self, mbid, limit=100, offset=0):
                calls.append(offset)
                return super().browse_label_releases(mbid, limit, offset)
        fake = _Counting([[_rel("X", "A", "rg-a", "2020")]])   # one short page
        lc.label_catalog("mbid", mb_getter=lambda: fake)
        assert calls == [0]        # stopped after the short page, didn't keep walking
