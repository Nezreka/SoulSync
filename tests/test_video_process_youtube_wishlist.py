"""Drain side of the YouTube fulfillment lane: queue the WHOLE wishlist (no total cap),
start up to a concurrency limit, and let finished downloads start the next. Pure selection +
pump with all I/O injected, plus the DB queue methods that back it.
"""

from __future__ import annotations

import pytest

from core.automation.handlers.video_process_youtube_wishlist import (
    auto_video_process_youtube_wishlist,
    slots_free,
    videos_to_enqueue,
)


class _Deps:
    def __init__(self):
        self.progress = []

    def update_progress(self, automation_id, **kw):
        self.progress.append(kw)


def _v(vid, title="T", date="2024-01-01"):
    return {"video_id": vid, "channel_title": "Chan", "video_title": title,
            "thumbnail_url": "/t.jpg", "published_at": date}


# ── pure ──────────────────────────────────────────────────────────────────────
def test_videos_to_enqueue_skips_already_queued_no_cap():
    wanted = [_v("a"), _v("b"), _v("c"), _v("d")]
    out = videos_to_enqueue(wanted, already_ids=["b"])
    assert [v["video_id"] for v in out] == ["a", "c", "d"]   # b in flight; rest ALL kept


def test_videos_to_enqueue_drops_idless():
    assert videos_to_enqueue([{"video_title": "no id"}], []) == []


def test_slots_free():
    assert slots_free(running=0, max_concurrent=3) == 3
    assert slots_free(running=2, max_concurrent=3) == 1
    assert slots_free(running=5, max_concurrent=3) == 0     # over the limit → no new starts


# ── handler: queue everything, start up to the limit ──────────────────────────
def _run(wanted, *, active=None, running=0, root="/yt", max_concurrent=3, start_results=None):
    enq, starts = [], {"n": 0}

    def enqueue(video, r):
        enq.append((video["video_id"], r))
        return len(enq)

    # start_next returns an id until the (simulated) queue is exhausted
    seq = list(start_results) if start_results is not None else [1] * 999

    def start_next():
        if starts["n"] < len(seq) and seq[starts["n"]] is not None:
            starts["n"] += 1
            return seq[starts["n"] - 1]
        return None

    deps = _Deps()
    res = auto_video_process_youtube_wishlist(
        {"_automation_id": "a", "max_concurrent": max_concurrent}, deps,
        youtube_root=lambda: root, fetch_wanted=lambda: wanted,
        active_ids=lambda: list(active or []), running_count=lambda: running,
        enqueue=enqueue, start_next=start_next, reap=lambda: 0)
    return res, enq, starts["n"], deps


def test_queues_entire_wishlist_and_starts_up_to_the_limit():
    wanted = [_v(c) for c in "abcdefgh"]          # 8 wished
    res, enq, started, _ = _run(wanted, running=0, max_concurrent=3)
    assert res["status"] == "completed"
    assert res["queued"] == 8                       # the WHOLE wishlist is queued (no cap)
    assert [vid for vid, _ in enq] == list("abcdefgh")
    assert started == 3 and res["started"] == 3     # only 3 start now; rest drain via workers
    assert enq[0][1] == "/yt"


def test_does_not_exceed_concurrency_when_some_already_running():
    wanted = [_v(c) for c in "abcde"]
    res, enq, started, _ = _run(wanted, running=2, max_concurrent=3)
    assert res["queued"] == 5                        # still queues everything
    assert started == 1                              # only 1 free slot (3 - 2 already running)


def test_full_pipeline_starts_nothing_new():
    wanted = [_v("a")]
    res, enq, started, _ = _run(wanted, active=["a"], running=3, max_concurrent=3)
    assert enq == []                                 # 'a' already in flight → not re-queued
    assert started == 0


def test_stops_starting_when_queue_drains_early():
    # only 2 things can actually start even though 3 slots are free
    res, enq, started, _ = _run([_v("a"), _v("b")], running=0, max_concurrent=3,
                                start_results=[10, 11, None])
    assert started == 2


def test_missing_youtube_folder_is_a_quiet_skip():
    # always-on automation: no folder set → skip cleanly (not an error every run)
    res, enq, started, deps = _run([_v("a")], root="")
    assert res["status"] == "completed" and res.get("skipped") == "no_youtube_folder"
    assert enq == [] and started == 0
    assert not any(p.get("status") == "error" for p in deps.progress)


def test_nothing_wanted_and_empty_queue_is_a_clean_noop():
    res, enq, started, _ = _run([], start_results=[None])     # nothing wanted, queue empty
    assert res["status"] == "completed" and res["queued"] == 0 and enq == [] and started == 0


def test_drains_leftover_queue_even_with_nothing_new_wanted():
    # a prior run queued items; this run adds nothing new but still fills the slots
    res, enq, started, _ = _run([], running=0, max_concurrent=3, start_results=[1, 2, 3])
    assert res["queued"] == 0 and enq == [] and started == 3


def test_one_bad_enqueue_does_not_stop_the_rest():
    def enqueue(video, r):
        if video["video_id"] == "a":
            raise RuntimeError("disk full")
        return 1

    res = auto_video_process_youtube_wishlist(
        {"_automation_id": "x", "max_concurrent": 5}, _Deps(),
        youtube_root=lambda: "/yt", fetch_wanted=lambda: [_v("a"), _v("b")],
        active_ids=lambda: [], running_count=lambda: 0, enqueue=enqueue,
        start_next=lambda: None, reap=lambda: 0)
    assert res["status"] == "completed" and res["queued"] == 1   # b still queued


def test_top_level_error_is_caught():
    def boom():
        raise RuntimeError("db down")
    res = auto_video_process_youtube_wishlist({"_automation_id": "x"}, _Deps(),
                                              youtube_root=lambda: "/yt", fetch_wanted=boom,
                                              reap=lambda: 0)
    assert res["status"] == "error" and "db down" in res["error"]


def test_reaper_runs_and_is_reported():
    # the drain recovers restart-orphaned downloads before pumping, and logs the count
    deps = _Deps()
    res = auto_video_process_youtube_wishlist(
        {"_automation_id": "a", "max_concurrent": 3}, deps,
        youtube_root=lambda: "/yt", fetch_wanted=lambda: [], active_ids=lambda: [],
        running_count=lambda: 0, enqueue=lambda v, r: 1, start_next=lambda: None,
        reap=lambda: 2)
    assert res["status"] == "completed"
    assert any("Recovered 2 stalled" in (p.get("log_line") or "") for p in deps.progress)


# ── the DB queue methods ──────────────────────────────────────────────────────
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


def test_downloaded_youtube_video_ids_only_completed_youtube(db):
    # the dedup the scans use so a downloaded video (removed from the wishlist) isn't re-added
    db.record_download_history({"id": 1, "kind": "youtube", "source": "youtube",
                                "media_id": "v1", "status": "completed", "dest_path": "/a.mp4"})
    db.record_download_history({"id": 2, "kind": "youtube", "source": "youtube",
                                "media_id": "v2", "status": "failed"})          # failed → not counted
    db.record_download_history({"id": 3, "kind": "movie", "source": "soulseek",
                                "media_id": "99", "status": "completed"})        # not youtube
    assert set(db.downloaded_youtube_video_ids()) == {"v1"}


def test_count_and_claim_queue(db):
    a = db.add_video_download({"kind": "youtube", "source": "youtube", "media_id": "v1",
                               "title": "A", "status": "queued"})
    db.add_video_download({"kind": "youtube", "source": "youtube", "media_id": "v2",
                           "title": "B", "status": "queued"})
    assert db.count_active_youtube_downloads() == 0           # nothing fetching yet

    claimed = db.claim_next_youtube_queued()
    assert claimed["id"] == a and claimed["media_id"] == "v1"  # oldest first
    assert db.count_active_youtube_downloads() == 1           # now one is 'downloading'

    db.claim_next_youtube_queued()
    assert db.count_active_youtube_downloads() == 2
    assert db.claim_next_youtube_queued() is None             # queue empty


def test_claim_ignores_non_youtube_and_terminal(db):
    db.add_video_download({"kind": "movie", "source": "soulseek", "media_id": "m1",
                           "title": "Movie", "status": "queued"})
    assert db.claim_next_youtube_queued() is None             # soulseek queue isn't ours
    assert db.count_active_youtube_downloads() == 0
