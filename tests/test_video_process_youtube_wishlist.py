"""Drain side of the YouTube fulfillment lane: enqueue wished videos into the download
queue in polite batches, skipping in-flight ones. Pure selection + handler with all I/O
injected, plus the DB query that feeds it.
"""

from __future__ import annotations

import pytest

from core.automation.handlers.video_process_youtube_wishlist import (
    auto_video_process_youtube_wishlist,
    select_to_enqueue,
)


class _Deps:
    def __init__(self):
        self.progress = []

    def update_progress(self, automation_id, **kw):
        self.progress.append(kw)


def _v(vid, title="T", date="2024-01-01"):
    return {"video_id": vid, "channel_title": "Chan", "video_title": title,
            "thumbnail_url": "/t.jpg", "published_at": date}


# ── pure batching ─────────────────────────────────────────────────────────────
def test_select_skips_inflight_and_caps_batch():
    wanted = [_v("a"), _v("b"), _v("c"), _v("d")]
    picks = select_to_enqueue(wanted, active_ids=["b"], batch_size=2)
    assert [p["video_id"] for p in picks] == ["a", "c"]   # b skipped (in flight), capped at 2


def test_select_zero_batch_means_no_cap():
    wanted = [_v("a"), _v("b"), _v("c")]
    assert len(select_to_enqueue(wanted, [], 0)) == 3


def test_select_drops_idless():
    assert select_to_enqueue([{"video_title": "no id"}], [], 5) == []


# ── handler ───────────────────────────────────────────────────────────────────
def _run(wanted, *, active=None, root="/yt", batch=3):
    enq = []

    def enqueue(video, r):
        enq.append((video["video_id"], r))
        return len(enq)

    deps = _Deps()
    res = auto_video_process_youtube_wishlist(
        {"_automation_id": "a", "batch_size": batch}, deps,
        youtube_root=lambda: root, fetch_wanted=lambda: wanted,
        active_ids=lambda: list(active or []), enqueue=enqueue)
    return res, enq, deps


def test_enqueues_a_batch_and_reports_remaining():
    wanted = [_v("a"), _v("b"), _v("c"), _v("d"), _v("e")]
    res, enq, _ = _run(wanted, batch=2)
    assert res["status"] == "completed" and res["queued"] == 2
    assert [vid for vid, _ in enq] == ["a", "b"]
    assert res["remaining"] == 3                          # 5 wanted - 2 queued
    assert enq[0][1] == "/yt"                             # root passed through


def test_inflight_videos_are_not_requeued():
    wanted = [_v("a"), _v("b")]
    res, enq, _ = _run(wanted, active=["a", "b"], batch=5)
    assert enq == [] and res["queued"] == 0


def test_missing_youtube_folder_is_an_error():
    res, enq, deps = _run([_v("a")], root="")
    assert res["status"] == "error" and "library folder" in res["error"]
    assert enq == [] and any(p.get("status") == "error" for p in deps.progress)


def test_nothing_wanted_is_a_clean_noop():
    res, enq, _ = _run([])
    assert res["status"] == "completed" and res["queued"] == 0 and enq == []


def test_one_bad_enqueue_does_not_stop_the_batch():
    def enqueue(video, r):
        if video["video_id"] == "a":
            raise RuntimeError("queue full")
        return 1

    res = auto_video_process_youtube_wishlist(
        {"_automation_id": "x", "batch_size": 5}, _Deps(),
        youtube_root=lambda: "/yt", fetch_wanted=lambda: [_v("a"), _v("b")],
        active_ids=lambda: [], enqueue=enqueue)
    assert res["status"] == "completed" and res["queued"] == 1   # b still queued


def test_top_level_error_is_caught():
    def boom():
        raise RuntimeError("db down")
    res = auto_video_process_youtube_wishlist({"_automation_id": "x"}, _Deps(),
                                              youtube_root=lambda: "/yt", fetch_wanted=boom)
    assert res["status"] == "error" and "db down" in res["error"]


# ── the DB query that feeds it ────────────────────────────────────────────────
from database.video_database import VideoDatabase  # noqa: E402


@pytest.fixture()
def db(tmp_path):
    return VideoDatabase(database_path=str(tmp_path / "video_library.db"))


def test_youtube_wishlist_to_download_shape(db):
    db.add_videos_to_wishlist(
        {"youtube_id": "UC1", "title": "Cool Channel", "avatar_url": "/a.jpg"},
        [{"youtube_id": "v1", "title": "First", "published_at": "2024-03-01", "thumbnail_url": "/1.jpg"},
         {"youtube_id": "v2", "title": "Second", "published_at": "2024-05-01", "thumbnail_url": "/2.jpg"}])
    rows = db.youtube_wishlist_to_download()
    assert [r["video_id"] for r in rows] == ["v2", "v1"]      # newest upload first
    top = rows[0]
    assert top["channel_id"] == "UC1" and top["channel_title"] == "Cool Channel"
    assert top["video_title"] == "Second" and top["published_at"] == "2024-05-01"
    assert top["thumbnail_url"] == "/2.jpg"


def test_youtube_wishlist_to_download_excludes_movies(db):
    db.add_movie_to_wishlist(99, "A Movie")
    db.add_videos_to_wishlist({"youtube_id": "UC1", "title": "Ch"},
                              [{"youtube_id": "v1", "title": "Vid", "published_at": "2024-01-01"}])
    rows = db.youtube_wishlist_to_download()
    assert [r["video_id"] for r in rows] == ["v1"]           # the movie isn't a youtube video
