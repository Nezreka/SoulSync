"""a followed YouTube channel as a library you manage.

a channel's downloads are never media_files rows: what's on disk is the
download ledger. the channel page's library panel is built from it, and
its deletes keep the ledger's rule (a removed video stays known, pruned,
so no scan fetches it again, unless the person asks to forget it).
"""

from __future__ import annotations

import json
import os

import pytest
from flask import Flask

from core.video import channel_library as cl
from database.video_database import VideoDatabase


# ── the pure builder ─────────────────────────────────────────────────────────

def _row(vid, path, published="2026-01-10", hid=None, quality="1080p"):
    return {"id": hid or hash(vid) % 10_000, "media_id": vid, "title": vid.upper(),
            "dest_path": path, "published_at": published, "completed_at": "2026-01-11 10:00:00",
            "quality_label": quality}


def test_files_sizes_folder_and_ghosts():
    sizes = {"/yt/Chan/Season 2026/a.mp4": 100, "/yt/Chan/Season 2025/b.mp4": 50}
    lib = cl.channel_library(
        [_row("a", "/yt/Chan/Season 2026/a.mp4"), _row("b", "/yt/Chan/Season 2025/b.mp4"),
         _row("gone", "/yt/Chan/Season 2024/gone.mp4")],
        today="2026-11-24", stat=sizes.get)
    assert lib["count"] == 2 and lib["size"] == 150
    assert lib["folder"] == "/yt/Chan"
    assert lib["quality"] == [{"label": "1080p", "count": 2}]
    missing = [a for a in lib["attention"] if a["kind"] == "missing"]
    assert missing and missing[0]["video_ids"] == ["gone"]
    by = {f["video_id"]: f for f in lib["files"]}
    assert by["gone"]["exists"] is False and by["a"]["size"] == 100


def test_one_season_folder_still_finds_the_channel_folder():
    lib = cl.channel_library([_row("a", "/yt/Chan/Season 2026/a.mp4")], today="2026-11-24",
                             stat=lambda p: 1)
    assert lib["folder"] == "/yt/Chan"


def test_attention_waiting_failing_unavailable_and_paused():
    lib = cl.channel_library(
        [_row("got", "/yt/C/Season 2026/got.mp4")],
        settings={"auto_download": False},
        wished_ids=["got", "w1", "w2", "w3"],
        retry_state={"w2": {"strikes": 2}, "w3": {"strikes": 3, "permanent": True}},
        today="2026-11-24", stat=lambda p: 1)
    kinds = {a["kind"]: a for a in lib["attention"]}
    assert lib["waiting"] == 3            # wished, not downloaded
    assert kinds["failing"]["video_ids"] == ["w2", "w3"]
    assert kinds["unavailable"]["video_ids"] == ["w3"]
    assert "paused" in kinds


def test_retention_due_names_what_goes_next():
    files = [_row(f"v{i}", f"/yt/C/Season 2026/v{i}.mp4", published=f"2026-0{i}-01") for i in range(1, 6)]
    lib = cl.channel_library(files, settings={"retention": "count_3"}, today="2026-11-24",
                             stat=lambda p: 1)
    assert lib["retention"] == {"policy": "count_3", "words": "Keeps the newest 3", "due": 2}
    due = [a for a in lib["attention"] if a["kind"] == "retention"][0]
    assert sorted(due["video_ids"]) == ["v1", "v2"]
    assert cl.channel_library(files, today="2026-11-24", stat=lambda p: 1)["retention"]["words"] \
        == "Keeps every video"


def test_remove_files_prunes_or_forgets_and_keeps_failures():
    pruned, forgot = [], []
    rows = [_row("a", "/a", hid=1), _row("b", "/b", hid=2), _row("stuck", "/s", hid=3)]

    def delete(row):
        return (False, 0, None) if row["media_id"] == "stuck" else (True, 10, f"/trash/{row['media_id']}")

    out = cl.remove_files(rows, delete_file=delete, mark_pruned=lambda h, d: pruned.append(h),
                          forget=forgot.append, today="2026-11-24")
    assert out["removed"] == 2 and out["freed"] == 20 and out["failed"] == ["stuck"]
    assert pruned == [1, 2] and forgot == []
    assert out["undo"] == [{"history_id": 1, "trash_path": "/trash/a"},
                           {"history_id": 2, "trash_path": "/trash/b"}]
    out = cl.remove_files(rows[:1], delete_file=delete, mark_pruned=lambda h, d: pruned.append(h),
                          forget=forgot.append, today="2026-11-24", keep_in_ledger=False)
    assert forgot == [1] and out["undo"] == []


# ── the routes, on a real video db and real files ────────────────────────────

@pytest.fixture()
def env(tmp_path, monkeypatch):
    import api.video as videoapi
    from core.video import organization
    db = VideoDatabase(database_path=str(tmp_path / "video_library.db"))
    videoapi._video_db = db
    root = tmp_path / "YouTube" / "Kurzgesagt"
    files = {}
    for i, (vid, year) in enumerate([("v1", 2026), ("v2", 2026), ("v3", 2025)], start=1):
        p = root / f"Season {year}" / f"{vid}.mp4"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"x" * (1000 * i))
        files[vid] = str(p)
        db.record_download_history({
            "id": i, "kind": "youtube", "source": "youtube", "media_source": "youtube",
            "media_id": vid, "title": vid, "status": "completed", "dest_path": str(p),
            "search_ctx": json.dumps({"channel_id": "UC1", "published_at": f"{year}-0{i}-01"})})
    trash = tmp_path / "trash"
    # the recycle bin as a setting, so restore checks it like the real one
    monkeypatch.setattr(organization, "load",
                        lambda _db: {"recycle_deletes": True, "recycle_path": str(trash)})
    app = Flask(__name__)
    app.register_blueprint(videoapi.create_video_blueprint(), url_prefix="/api/video")
    yield app.test_client(), db, files
    videoapi._video_db = None


def test_the_library_lists_what_is_on_disk(env):
    c, db, files = env
    d = c.get("/api/video/youtube/library/UC1").get_json()
    assert d["success"] and d["count"] == 3 and d["size"] == 6000
    assert d["folder"].endswith(os.path.join("YouTube", "Kurzgesagt"))


def test_delete_recycles_keeps_it_known_and_undo_puts_it_back(env):
    c, db, files = env
    d = c.post("/api/video/youtube/library/UC1/delete", json={"video_ids": ["v2"]}).get_json()
    assert d["removed"] == 1 and d["count"] == 2
    assert not os.path.exists(files["v2"])
    # still known: the scan never fetches it again
    assert "v2" in db.downloaded_youtube_video_ids()
    assert "v2" not in db.owned_youtube_video_ids()
    d = c.post("/api/video/youtube/library/UC1/undo", json={"undo": d["undo"]}).get_json()
    assert d["restored"] == 1 and d["count"] == 3
    assert os.path.exists(files["v2"])
    assert "v2" in db.owned_youtube_video_ids()


def test_forget_lets_it_download_again(env):
    c, db, files = env
    c.post("/api/video/youtube/library/UC1/delete", json={"video_ids": ["v1"], "forget": True})
    assert "v1" not in db.downloaded_youtube_video_ids()


def test_clean_now_applies_retention(env):
    c, db, files = env
    db.set_channel_settings("UC1", {"retention": "count_1"})
    d = c.post("/api/video/youtube/library/UC1/clean").get_json()
    assert d["removed"] == 2 and d["count"] == 1
    assert os.path.exists(files["v2"]) is False or os.path.exists(files["v1"]) is False


def test_delete_needs_something_picked(env):
    c, _db, _files = env
    assert c.post("/api/video/youtube/library/UC1/delete", json={}).status_code == 400


def test_the_library_is_the_admins():
    from api.video.access import route_access
    for path, method in (("/api/video/youtube/library/UC1", "GET"),
                         ("/api/video/youtube/library/UC1/delete", "POST"),
                         ("/api/video/youtube/library/UC1/undo", "POST"),
                         ("/api/video/youtube/library/UC1/clean", "POST")):
        assert route_access(path, method) == ("admin", None), path
