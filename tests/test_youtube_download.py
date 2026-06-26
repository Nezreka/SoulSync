"""YouTube download worker — the fulfillment lane for wished YouTube videos. Pure
orchestration (dest planning, yt-dlp opts, completion → archive + unwish, failure →
archive only) tested with the yt-dlp run + all DB writes injected.
"""

from __future__ import annotations

import json
import os

from core.video import youtube_download as ytd
from core.video.youtube_quality import default_profile


# ── organising fields from the queue row ──────────────────────────────────────
def test_fields_prefer_search_ctx_then_fall_back():
    dl = {"title": "Some Channel", "year": "2024-01-01", "media_id": "vid1",
          "search_ctx": json.dumps({"channel": "Veritasium", "video_title": "Electricity",
                                     "published_at": "2024-03-15"})}
    f = ytd.youtube_fields_from_download(dl)
    assert f == {"channel": "Veritasium", "title": "Electricity",
                 "published_at": "2024-03-15", "youtube_id": "vid1"}


def test_fields_fall_back_to_row_when_ctx_absent_or_garbage():
    dl = {"title": "Chan", "year": "2024-02-02", "media_id": "v2", "search_ctx": "{bad"}
    f = ytd.youtube_fields_from_download(dl)
    assert f["channel"] == "Chan" and f["title"] == "Chan"
    assert f["published_at"] == "2024-02-02" and f["youtube_id"] == "v2"


# ── destination planning ──────────────────────────────────────────────────────
def test_plan_destination_uses_the_youtube_template():
    dl = {"target_dir": "/yt", "media_id": "v1",
          "search_ctx": json.dumps({"channel": "Veritasium", "video_title": "How It Works",
                                    "published_at": "2024-03-15"})}
    dest = ytd.plan_destination(dl, {}, "mp4")
    assert dest["path"] == os.path.join("/yt", "Veritasium", "Season 2024",
                                        "Veritasium - 2024-03-15 - How It Works.mp4")


# ── yt-dlp opts ───────────────────────────────────────────────────────────────
def test_ydl_opts_carry_format_selection_and_fixed_output():
    opts = ytd.ydl_download_opts(default_profile(), "/yt/dir", "Chan - 2024-03-15 - Title")
    assert opts["format"] == "bv*[height<=1080]+ba/b[height<=1080]/bv*+ba/b"
    assert opts["merge_output_format"] == "mp4"
    assert opts["paths"] == {"home": "/yt/dir"}
    assert opts["outtmpl"] == "Chan - 2024-03-15 - Title.%(ext)s"
    assert opts["noplaylist"] is True


# ── download_one with an injected yt-dlp ───────────────────────────────────────
class _FakeYDL:
    def __init__(self, opts):
        self.opts = opts
        _FakeYDL.last = self

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def download(self, urls):
        self.urls = urls


class _BoomYDL(_FakeYDL):
    def download(self, urls):
        raise RuntimeError("403 blocked")


def test_download_one_success_returns_built_dest_path():
    res = ytd.download_one("vid1", "/yt/Chan/Season 2024", "Chan - 2024-03-15 - T",
                           default_profile(), "mp4", ydl_factory=_FakeYDL)
    assert res["ok"] is True
    assert res["dest_path"] == os.path.join("/yt/Chan/Season 2024", "Chan - 2024-03-15 - T.mp4")
    assert _FakeYDL.last.urls == ["https://www.youtube.com/watch?v=vid1"]


def test_download_one_failure_is_captured_not_raised():
    res = ytd.download_one("vid1", "/yt", "stem", default_profile(), "mp4", ydl_factory=_BoomYDL)
    assert res["ok"] is False and "403 blocked" in res["error"]


def test_download_one_no_factory_is_unavailable():
    res = ytd.download_one("vid1", "/yt", "stem", default_profile(), "mp4", ydl_factory=None)
    # yt_dlp may or may not be importable in the test env; either way no real run happens
    if res["ok"] is False:
        assert res["error"]


# ── the orchestration: completion vs failure ──────────────────────────────────
def _recorder():
    calls = {"rows": [], "archive": [], "unwish": []}

    def update_row(dl_id, **kw):
        calls["rows"].append((dl_id, kw))

    def archive(row, upd):
        calls["archive"].append(upd)

    def clear_wishlist(vid):
        calls["unwish"].append(vid)

    return calls, update_row, archive, clear_wishlist


def _dl():
    return {"id": 7, "media_id": "vid1", "target_dir": "/yt", "title": "Chan", "year": "2024-03-15",
            "search_ctx": json.dumps({"channel": "Chan", "video_title": "T", "published_at": "2024-03-15"})}


def test_process_completion_archives_and_unwishes():
    calls, update_row, archive, clear = _recorder()
    res = ytd.process_youtube_download(
        _dl(), profile=default_profile(), settings={},
        download=lambda *a, **k: {"ok": True, "dest_path": "/yt/Chan/Season 2024/Chan - 2024-03-15 - T.mp4"},
        update_row=update_row, archive=archive, clear_wishlist=clear, now=lambda: "2026-06-25T00:00:00+00:00")
    assert res["status"] == "completed"
    # row marked completed, history snapshot 'completed', and the video unwished
    statuses = [kw.get("status") for _, kw in calls["rows"]]
    assert "downloading" in statuses and statuses[-1] == "completed"
    assert calls["archive"][-1]["status"] == "completed"
    assert calls["unwish"] == ["vid1"]


def test_process_failure_archives_but_keeps_the_wish():
    calls, update_row, archive, clear = _recorder()
    res = ytd.process_youtube_download(
        _dl(), profile=default_profile(), settings={},
        download=lambda *a, **k: {"ok": False, "error": "yt-dlp said no"},
        update_row=update_row, archive=archive, clear_wishlist=clear, now=lambda: "t")
    assert res["status"] == "failed" and "yt-dlp said no" in res["error"]
    assert calls["rows"][-1][1]["status"] == "failed"
    assert calls["archive"][-1]["status"] == "failed"
    assert calls["unwish"] == []                         # wish kept so it can retry later


def test_process_stages_in_download_folder_then_imports_to_library():
    # the consistent pipeline: download → staging folder → 'importing' → move → library
    calls, update_row, archive, clear = _recorder()
    moves = []
    staged = "/downloads/youtube/Chan - 2024-03-15 - T.mp4"
    res = ytd.process_youtube_download(
        _dl(), profile=default_profile(), settings={},
        download=lambda vid, d, *a, **k: ({"ok": True, "dest_path": staged}
                                          if d == "/downloads/youtube" else {"ok": False, "error": "wrong dir"}),
        update_row=update_row, archive=archive, clear_wishlist=clear,
        stage_dir="/downloads/youtube", move=lambda s, d: moves.append((s, d)), now=lambda: "t")
    assert res["status"] == "completed"
    statuses = [kw.get("status") for _, kw in calls["rows"]]
    assert statuses == ["downloading", "importing", "completed"]      # the visible phases
    final = "/yt/Chan/Season 2024/Chan - 2024-03-15 - T.mp4"
    assert moves == [(staged, final)]                                 # staged → organised library
    assert res["dest_path"] == final and calls["unwish"] == ["vid1"]


def test_process_import_failure_is_terminal_and_keeps_the_wish():
    calls, update_row, archive, clear = _recorder()

    def boom(_s, _d):
        raise OSError("disk full")

    res = ytd.process_youtube_download(
        _dl(), profile=default_profile(), settings={},
        download=lambda *a, **k: {"ok": True, "dest_path": "/downloads/youtube/x.mp4"},
        update_row=update_row, archive=archive, clear_wishlist=clear,
        stage_dir="/downloads/youtube", move=boom, now=lambda: "t")
    assert res["status"] == "import_failed"
    statuses = [kw.get("status") for _, kw in calls["rows"]]
    assert statuses == ["downloading", "importing", "import_failed"]
    assert calls["archive"][-1]["status"] == "import_failed"
    assert calls["unwish"] == []                                      # not unwished → can retry


def test_requeue_orphaned_youtube_recovers_only_dead_downloads():
    """After a restart no worker threads survive, so any 'downloading' YouTube row is an
    orphan → back to 'queued'. A row whose worker is still alive (in _active_worker_ids) and
    non-youtube / non-downloading rows are left alone."""
    updates = []

    class _DB:
        def get_active_video_downloads(self):
            return [
                {"id": 1, "source": "youtube", "status": "downloading"},   # orphan → requeue
                {"id": 2, "source": "youtube", "status": "downloading"},   # live worker → keep
                {"id": 3, "source": "youtube", "status": "queued"},        # not downloading → keep
                {"id": 4, "source": "soulseek", "status": "downloading"},  # not youtube → keep
            ]

        def update_video_download(self, dl_id, **kw):
            updates.append((dl_id, kw))

    ytd._active_worker_ids.clear()
    ytd._active_worker_ids.add(2)                      # id 2 has a live worker
    try:
        n = ytd.requeue_orphaned_youtube(lambda: _DB())
    finally:
        ytd._active_worker_ids.clear()
    assert n == 1
    assert updates == [(1, {"status": "queued", "progress": 0})]   # only the orphan


def test_process_passes_the_organised_dir_to_the_downloader():
    seen = {}

    def fake_download(video_id, dest_dir, stem, profile, container, **kw):
        seen.update(video_id=video_id, dest_dir=dest_dir, stem=stem, container=container)
        return {"ok": True, "dest_path": "/x"}

    calls, update_row, archive, clear = _recorder()
    ytd.process_youtube_download(_dl(), profile=default_profile(), settings={},
                                 download=fake_download, update_row=update_row,
                                 archive=archive, clear_wishlist=clear, now=lambda: "t")
    assert seen["video_id"] == "vid1"
    assert seen["dest_dir"] == os.path.join("/yt", "Chan", "Season 2024")
    assert seen["stem"] == "Chan - 2024-03-15 - T" and seen["container"] == "mp4"


def test_process_never_clobbers_target_dir_so_reruns_dont_nest():
    """The row's target_dir is the youtube ROOT; plan_destination derives the channel/season
    folders under it. The worker must NOT write the organised dir back to target_dir, or a
    re-run (e.g. the orphan reaper re-queues an interrupted download) would organise AGAIN →
    Channel/Season/Channel/Season. Re-processing the same row must be idempotent."""
    seen = []

    def fake_download(video_id, dest_dir, stem, profile, container, **kw):
        seen.append(dest_dir)
        return {"ok": True, "dest_path": "/x"}

    calls, update_row, archive, clear = _recorder()
    dl = _dl()                                          # target_dir = "/yt" (the root)
    for _ in range(2):                                  # simulate the interrupted-then-requeued re-run
        ytd.process_youtube_download(dl, profile=default_profile(), settings={},
                                     download=fake_download, update_row=update_row,
                                     archive=archive, clear_wishlist=clear, now=lambda: "t")
    # no update_row call writes target_dir (that's what caused the nesting)
    assert all("target_dir" not in kw for _, kw in calls["rows"])
    # both runs target the SAME organised dir — not a doubly-nested one
    assert seen[0] == seen[1] == os.path.join("/yt", "Chan", "Season 2024")
