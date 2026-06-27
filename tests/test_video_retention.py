"""YouTube channel retention: delete episodes outside a channel's keep window, keep the
history row (so the scan never re-downloads them). Pure retention math + the handler with
all I/O injected + the DB layer (capture channel/upload-date, the prune-but-keep contract)."""

from __future__ import annotations

import json

import pytest

from core.automation.handlers.video_clean_youtube import auto_video_clean_youtube_episodes
from core.video.retention import episode_date, episodes_to_prune, parse_retention


# ── pure retention math ─────────────────────────────────────────────────────────
def test_parse_retention():
    assert parse_retention("all") is None and parse_retention("") is None
    assert parse_retention("count_30") == ("count", 30)
    assert parse_retention("days_90") == ("days", 90)
    assert parse_retention("count_0") is None and parse_retention("junk") is None


def test_episode_date_prefers_published_then_filename():
    assert episode_date({"published_at": "2026-06-22"}) == "2026-06-22"
    assert episode_date({"published_at": "2026-06-22T10:00:00Z"}) == "2026-06-22"
    assert episode_date({"filename": "Chan - 2025-01-15 - Title.mp4"}) == "2025-01-15"   # fallback
    assert episode_date({"published_at": None, "filename": "no date.mp4"}) == ""


def test_episodes_to_prune_by_count_keeps_newest():
    eps = [{"id": i, "published_at": "2026-06-%02d" % (i + 1)} for i in range(5)]   # 01..05
    prune = episodes_to_prune(eps, "count_3", today="2026-06-30")
    assert sorted(e["id"] for e in prune) == [0, 1]                                 # oldest two go


def test_episodes_to_prune_by_days_uses_upload_date():
    eps = [{"id": "old", "published_at": "2026-01-01"}, {"id": "new", "published_at": "2026-06-20"}]
    assert [e["id"] for e in episodes_to_prune(eps, "days_90", today="2026-06-30")] == ["old"]


def test_undated_and_keep_all_never_prune():
    assert episodes_to_prune([{"id": 1}], "count_1", today="2026-06-30") == []       # no date → kept
    assert episodes_to_prune([{"id": 1, "published_at": "2000-01-01"}], "all", today="2026-06-30") == []


# ── handler (I/O injected) ──────────────────────────────────────────────────────
class _Deps:
    def __init__(self):
        self.progress = []

    def update_progress(self, automation_id, **kw):
        self.progress.append(kw)


def _eps():
    return [{"id": 1, "published_at": "2026-06-01", "title": "old"},
            {"id": 2, "published_at": "2026-06-20", "title": "new"}]


def test_handler_deletes_out_of_window_marks_pruned_skips_keep_all():
    deleted, pruned = [], []
    res = auto_video_clean_youtube_episodes(
        {"_automation_id": "a"}, _Deps(),
        fetch_channels=lambda: ["C1", "C2"],
        channel_retention=lambda c: "count_1" if c == "C1" else "all",   # C2 keep-all → skipped
        fetch_episodes=lambda c: _eps(),
        delete_files=lambda ep: (deleted.append(ep["id"]) or True, 1000),
        mark_pruned=lambda hid, when: pruned.append((hid, when)) or True,
        today_fn=lambda: "2026-06-30")
    assert res["status"] == "completed" and res["deleted"] == 1 and res["channels"] == 1
    assert deleted == [1] and pruned == [(1, "2026-06-30")] and res["freed_bytes"] == 1000


def test_handler_keep_all_everywhere_is_a_noop():
    res = auto_video_clean_youtube_episodes(
        {"_automation_id": "a"}, _Deps(), fetch_channels=lambda: ["C1"],
        channel_retention=lambda c: "all", fetch_episodes=lambda c: _eps(),
        delete_files=lambda e: (True, 0), mark_pruned=lambda h, w: True, today_fn=lambda: "2026-06-30")
    assert res["deleted"] == 0 and res["channels"] == 0


def test_handler_delete_failure_is_not_marked_pruned():
    marked = []
    res = auto_video_clean_youtube_episodes(
        {"_automation_id": "a"}, _Deps(), fetch_channels=lambda: ["C1"],
        channel_retention=lambda c: "count_1", fetch_episodes=lambda c: _eps(),
        delete_files=lambda e: (False, 0),                 # couldn't delete → retry next run
        mark_pruned=lambda h, w: marked.append(h), today_fn=lambda: "2026-06-30")
    assert res["deleted"] == 0 and marked == []


# ── DB layer ────────────────────────────────────────────────────────────────────
from database.video_database import VideoDatabase  # noqa: E402


@pytest.fixture()
def db(tmp_path):
    return VideoDatabase(database_path=str(tmp_path / "video_library.db"))


def test_history_captures_channel_and_upload_date_and_prune_keeps_dedup(db):
    db.record_download_history({
        "id": 1, "kind": "youtube", "source": "youtube", "media_id": "v1",
        "status": "completed", "dest_path": "/yt/Chan/Season 2026/Chan - 2026-06-22 - T.mp4",
        "search_ctx": json.dumps({"channel_id": "UC1", "published_at": "2026-06-22"})})
    eps = db.youtube_channel_episodes("UC1")
    assert len(eps) == 1 and eps[0]["published_at"] == "2026-06-22" and eps[0]["media_id"] == "v1"
    assert db.youtube_channels_with_downloads() == ["UC1"]

    assert db.mark_download_pruned(eps[0]["id"], "2026-06-30") is True
    assert db.youtube_channel_episodes("UC1") == []        # gone from the retention view
    assert db.youtube_channels_with_downloads() == []
    assert "v1" in db.downloaded_youtube_video_ids()       # …but still counts as downloaded → no re-grab
