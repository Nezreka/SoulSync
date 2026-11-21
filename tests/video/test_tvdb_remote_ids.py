"""TVDB's series record lists the ids it links the show to (remoteIds). A show
TMDB has no IMDb id for still gets one from there, and a show the server gave
no TMDB id gets that too, so TMDB can enrich it by id. Enrichment only fills
empty columns, so nothing a show already has changes."""

from __future__ import annotations

import sqlite3

import pytest

from core.video.enrichment.clients import TVDBClient
from core.video.enrichment.worker import VideoEnrichmentWorker
from database.video_database import SCHEMA_VERSION, VideoDatabase

# what TVDB's /series/80009/extended carries for Tenchi Muyo! Ryo-Ohki
_TENCHI_REMOTE = [{"id": "tt0105559", "type": 2, "sourceName": "IMDB"},
                  {"id": "43004", "type": 12, "sourceName": "TheMovieDB.com"}]


@pytest.fixture()
def db(tmp_path):
    return VideoDatabase(database_path=str(tmp_path / "video_library.db"))


def test_remote_ids_reads_imdb_and_tmdb():
    from core.video.enrichment.clients import _tvdb_remote_ids
    assert _tvdb_remote_ids(_TENCHI_REMOTE) == {"imdb_id": "tt0105559", "tmdb_id": 43004}
    assert _tvdb_remote_ids(None) == {}
    # a malformed imdb id, an unknown source and junk entries give nothing
    assert _tvdb_remote_ids([{"id": "0105559", "sourceName": "IMDB"},
                             {"id": "x", "sourceName": "TheMovieDB.com"},
                             {"id": "123", "sourceName": "Zap2It"}, "junk"]) == {}


def test_a_source_listed_twice_with_different_values_gives_neither():
    from core.video.enrichment.clients import _tvdb_remote_ids
    two = [{"id": "tt0105559", "sourceName": "IMDB"}, {"id": "tt0000001", "sourceName": "IMDB"},
           {"id": "43004", "sourceName": "TheMovieDB.com"}]
    assert _tvdb_remote_ids(two) == {"tmdb_id": 43004}


def test_the_known_id_match_carries_the_remote_ids(monkeypatch):
    c = TVDBClient("KEY")
    monkeypatch.setattr(c, "_authed_get", lambda path, params=None: {"data": {
        "overview": "O", "remoteIds": _TENCHI_REMOTE}})
    res = c.match("show", "Tenchi Muyo! Ryo-Ohki", None, known_id=80009)
    assert res["id"] == 80009
    assert res["metadata"]["imdb_id"] == "tt0105559" and res["metadata"]["tmdb_id"] == 43004


class _Client:
    enabled = True

    def __init__(self, result):
        self._result = result

    def match(self, kind, title, year, known_id=None):
        return self._result


def test_the_tvdb_worker_fills_a_missing_imdb_id_and_keeps_what_is_there(db):
    a = db.upsert_show_tree("plex", {"server_id": "s1", "title": "Tenchi Muyo! Ryo-Ohki", "tvdb_id": 80009,
                                     "tmdb_id": 43004, "seasons": []})
    b = db.upsert_show_tree("plex", {"server_id": "s2", "title": "Other", "tvdb_id": 1, "tmdb_id": 2,
                                     "imdb_id": "tt9999999", "seasons": []})
    w = VideoEnrichmentWorker(db, "tvdb", _Client({"id": 80009, "metadata": {
        "imdb_id": "tt0105559", "tmdb_id": 43004}}))
    assert w.process_one() is True
    w.client = _Client({"id": 1, "metadata": {"imdb_id": "tt0105559", "tmdb_id": 777}})
    assert w.process_one() is True
    with db.connect() as c:
        rows = {r["id"]: (r["imdb_id"], r["tmdb_id"]) for r in c.execute("SELECT id, imdb_id, tmdb_id FROM shows")}
    assert rows[a] == ("tt0105559", 43004)
    assert rows[b] == ("tt9999999", 2)          # never overwritten


def test_a_tmdb_id_tvdb_fills_sends_the_show_back_through_tmdb(db):
    sid = db.upsert_show_tree("plex", {"server_id": "s1", "title": "The Body (2026)", "tvdb_id": 463695,
                                       "seasons": []})
    with db.connect() as c:
        c.execute("UPDATE shows SET tmdb_match_status='not_found', tmdb_last_attempted=CURRENT_TIMESTAMP")
        c.commit()
    db.enrichment_apply("tvdb", "show", sid, matched=True, external_id=463695,
                        metadata={"tmdb_id": 300100, "imdb_id": "tt8014982"})
    with db.connect() as c:
        row = c.execute("SELECT tmdb_id, imdb_id, tmdb_match_status FROM shows WHERE id=?", (sid,)).fetchone()
    assert (row["tmdb_id"], row["imdb_id"], row["tmdb_match_status"]) == (300100, "tt8014982", None)
    item = db.enrichment_next("tmdb")
    assert (item["id"], item["known_id"]) == (sid, 300100)


def test_a_matched_tmdb_show_is_left_matched(db):
    sid = db.upsert_show_tree("plex", {"server_id": "s1", "title": "S", "tvdb_id": 5, "tmdb_id": 6,
                                       "seasons": []})
    with db.connect() as c:
        c.execute("UPDATE shows SET tmdb_match_status='matched'")
        c.commit()
    db.enrichment_apply("tvdb", "show", sid, matched=True, external_id=5, metadata={"tmdb_id": 6})
    with db.connect() as c:
        assert c.execute("SELECT tmdb_match_status FROM shows").fetchone()[0] == "matched"


def test_matched_shows_missing_an_id_are_queued_once(tmp_path):
    path = tmp_path / "video_library.db"
    db = VideoDatabase(database_path=str(path))
    gap = db.upsert_show_tree("plex", {"server_id": "s1", "title": "Gap", "tvdb_id": 80009, "tmdb_id": 43004,
                                       "seasons": []})
    full = db.upsert_show_tree("plex", {"server_id": "s2", "title": "Full", "tvdb_id": 7, "tmdb_id": 8,
                                        "imdb_id": "tt0000007", "seasons": []})
    conn = sqlite3.connect(path)
    conn.execute("UPDATE shows SET tvdb_match_status='matched'")
    conn.execute("DELETE FROM video_settings WHERE key='tvdb_remote_ids_backfill_done'")
    conn.commit()
    conn.close()
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    VideoDatabase._run_data_migrations(conn, SCHEMA_VERSION)   # an existing DB: no numbered step runs
    conn.commit()
    status = {r["id"]: r["tvdb_match_status"] for r in conn.execute("SELECT id, tvdb_match_status FROM shows")}
    conn.execute("UPDATE shows SET tvdb_match_status='matched'")
    VideoDatabase._run_data_migrations(conn, SCHEMA_VERSION)   # the marker holds: once only
    again = {r["id"]: r["tvdb_match_status"] for r in conn.execute("SELECT id, tvdb_match_status FROM shows")}
    conn.close()
    assert status == {gap: None, full: "matched"}
    assert again == {gap: "matched", full: "matched"}


# ── review follow-ups ────────────────────────────────────────────────────────

def test_a_series_years_off_the_show_gives_no_ids(monkeypatch):
    # a stored tvdb id that was a title-search guess can point at a remake:
    # its linked ids would name the wrong show
    c = TVDBClient("KEY")
    monkeypatch.setattr(c, "_authed_get", lambda path, params=None: {"data": {
        "overview": "O", "year": "1992", "remoteIds": _TENCHI_REMOTE}})
    assert "imdb_id" not in c.match("show", "Tenchi Muyo!", 2023, known_id=80009)["metadata"]
    assert c.match("show", "Tenchi Muyo!", 1993, known_id=80009)["metadata"]["imdb_id"] == "tt0105559"
    assert c.match("show", "Tenchi Muyo!", None, known_id=80009)["metadata"]["tmdb_id"] == 43004


def test_linked_ids_that_disagree_with_the_shows_fill_nothing(db):
    # the show already has TMDB 6; TVDB's record names TMDB 777: it's another
    # show, so its IMDb id isn't taken either, and TMDB isn't re-queued
    sid = db.upsert_show_tree("plex", {"server_id": "s1", "title": "S", "tvdb_id": 5, "tmdb_id": 6,
                                       "seasons": []})
    with db.connect() as c:
        c.execute("UPDATE shows SET tmdb_match_status='not_found'")
        c.commit()
    db.enrichment_apply("tvdb", "show", sid, matched=True, external_id=5,
                        metadata={"tmdb_id": 777, "imdb_id": "tt0105559", "overview": "kept"})
    with db.connect() as c:
        row = c.execute("SELECT tmdb_id, imdb_id, overview, tmdb_match_status FROM shows").fetchone()
    assert tuple(row) == (6, None, "kept", "not_found")


def test_a_matching_id_lets_the_other_fill(db):
    sid = db.upsert_show_tree("plex", {"server_id": "s1", "title": "S", "tvdb_id": 5, "tmdb_id": 43004,
                                       "seasons": []})
    db.enrichment_apply("tvdb", "show", sid, matched=True, external_id=5,
                        metadata={"tmdb_id": 43004, "imdb_id": "tt0105559"})
    with db.connect() as c:
        assert c.execute("SELECT imdb_id FROM shows").fetchone()[0] == "tt0105559"


def test_a_tmdb_title_carries_only_an_imdb_id_that_looks_like_one():
    from core.video.enrichment.clients import parse_title_identity
    d = {"id": 1, "title": "X", "release_date": "2000-01-01"}
    assert parse_title_identity("movie", {**d, "imdb_id": "tt0113277"})["imdb_id"] == "tt0113277"
    assert "imdb_id" not in parse_title_identity("movie", {**d, "imdb_id": ""})
    assert "imdb_id" not in parse_title_identity("movie", {**d, "imdb_id": "0113277"})
