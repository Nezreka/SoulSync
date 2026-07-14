"""Permanent video download history — the archive that powers the History modal
and the smart post-download scan. video_downloads is the transient queue; this
table survives the cleanup, so it's snapshotted at terminal status."""

from __future__ import annotations

import json

import pytest

from database.video_database import VideoDatabase


@pytest.fixture()
def db(tmp_path):
    return VideoDatabase(database_path=str(tmp_path / "video_library.db"))


def _movie(**over):
    row = {"id": 1, "kind": "movie", "title": "Dune", "year": 2024, "status": "completed",
           "release_title": "Dune.2024.2160p.UHD.BluRay.x265-GRP", "source": "soulseek",
           "username": "bob", "filename": "Dune.2024.2160p.x265.mkv",
           "dest_path": "/movies/Dune (2024)/Dune (2024).mkv", "size_bytes": 9_000_000_000,
           "quality_label": "2160p", "media_id": "55", "media_source": "library",
           "poster_url": "/p/dune.jpg", "created_at": "2026-06-20 10:00:00",
           "completed_at": "2026-06-20 10:30:00"}
    row.update(over)
    return row


def _episode(**over):
    row = {"id": 2, "kind": "show", "title": "Severance", "year": 2025, "status": "completed",
           "release_title": "Severance.S02E05.1080p.WEB.h264", "source": "soulseek",
           "dest_path": "/tv/Severance/Season 02/Severance - S02E05.mkv", "size_bytes": 2_000_000_000,
           "search_ctx": json.dumps({"scope": "episode", "title": "Severance", "season": 2, "episode": 5}),
           "media_id": "9", "media_source": "library", "completed_at": "2026-06-21 02:00:00"}
    row.update(over)
    return row


def test_records_a_completed_movie_with_parsed_quality(db):
    hid = db.record_download_history(_movie())
    assert hid > 0
    d = db.download_history_detail(hid)
    assert d["title"] == "Dune" and d["outcome"] == "completed" and d["media_type"] == "movie"
    assert d["resolution"] == "2160p" and d["video_codec"] == "x265"   # sniffed from the release name
    assert d["size_bytes"] == 9_000_000_000 and d["dest_path"].endswith("Dune (2024).mkv")


def test_records_an_episode_with_season_episode_from_search_ctx(db):
    hid = db.record_download_history(_episode())
    d = db.download_history_detail(hid)
    assert (d["kind"], d["media_type"]) == ("show", "show")
    assert d["season_number"] == 2 and d["episode_number"] == 5
    assert d["resolution"] == "1080p" and d["video_codec"] == "x264"


def test_history_is_idempotent_per_terminal_download(db):
    first = db.record_download_history(_movie())
    again = db.record_download_history(_movie())     # same download_id/outcome/dest_path
    assert first > 0 and again == 0                  # INSERT OR IGNORE → no dupe
    assert db.download_history_counts()["movie"] == 1


def test_query_filters_by_kind_and_search(db):
    db.record_download_history(_movie())
    db.record_download_history(_episode())
    assert db.query_download_history(kind="movie")["pagination"]["total_count"] == 1
    assert db.query_download_history(kind="show")["items"][0]["title"] == "Severance"
    hits = db.query_download_history(search="dune")["items"]
    assert len(hits) == 1 and hits[0]["title"] == "Dune"


def test_counts_only_count_completed(db):
    db.record_download_history(_movie())
    db.record_download_history(_movie(id=3, status="failed", dest_path=None,
                                      error="no release found"))
    c = db.download_history_counts()
    assert c == {"movie": 1, "show": 0, "total": 1}   # the failed one isn't counted


def test_latest_completed_download_is_the_probe_target(db):
    db.record_download_history(_movie(id=1, completed_at="2026-06-20 10:30:00"))
    db.record_download_history(_movie(id=4, title="Wicked",
                                      dest_path="/movies/Wicked (2024)/Wicked.mkv",
                                      completed_at="2026-06-22 09:00:00"))
    db.record_download_history(_episode())
    assert db.latest_completed_download("movie")["title"] == "Wicked"   # newest movie
    assert db.latest_completed_download("show")["title"] == "Severance"
    assert db.latest_completed_download("all")["title"] == "Wicked"     # newest overall


def test_newest_first_ordering_in_the_feed(db):
    db.record_download_history(_movie(id=1, title="Old", dest_path="/m/old.mkv",
                                      completed_at="2026-01-01 00:00:00"))
    db.record_download_history(_movie(id=2, title="New", dest_path="/m/new.mkv",
                                      completed_at="2026-06-01 00:00:00"))
    titles = [i["title"] for i in db.query_download_history()["items"]]
    assert titles == ["New", "Old"]
