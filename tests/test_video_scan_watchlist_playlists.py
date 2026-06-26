"""Watchlist-playlists scan: MIRROR a followed YouTube playlist — wishlist every long-form
video in it you don't have (plus new additions), filed playlist-as-show. Differs from the
channel scan (no forward-looking baseline, no last-N net). Pure selection, I/O injected.
"""

from __future__ import annotations

from core.automation.handlers.video_scan_watchlist_playlists import (
    auto_video_scan_watchlist_playlists,
    select_playlist_video_gaps,
)


class _Deps:
    def __init__(self):
        self.progress = []

    def update_progress(self, automation_id, **kw):
        self.progress.append(kw)


def _vid(vid, *, date=None, dur=600):
    return {"youtube_id": vid, "title": "Video " + vid, "published_at": date,
            "duration_seconds": dur, "thumbnail_url": "/t/" + vid + ".jpg"}


# ── pure: mirror the whole list (no baseline / no net) ────────────────────────
def test_mirrors_every_unowned_long_form_video():
    # old AND new alike — a curated playlist is wanted in full
    vids = [_vid("a", date="2024-06-01"), _vid("b", date="2018-01-01"),
            _vid("c", date="2010-05-05")]
    gaps = select_playlist_video_gaps(vids, today="2026-06-25")
    assert [g["youtube_id"] for g in gaps] == ["a", "b", "c"]   # all of them, even ancient


def test_excludes_already_wishlisted_downloaded_dismissed():
    vids = [_vid("a"), _vid("b"), _vid("c")]
    gaps = select_playlist_video_gaps(
        vids, wishlisted_ids=["a"], downloaded_ids=["b"], dismissed_ids=["c"], today="2026-06-25")
    assert gaps == []


def test_excludes_shorts_and_future_premieres():
    vids = [_vid("short", dur=20), _vid("real", dur=600, date="2024-01-01"),
            _vid("premiere", dur=600, date="2099-01-01")]
    gaps = select_playlist_video_gaps(vids, today="2026-06-25")
    assert [g["youtube_id"] for g in gaps] == ["real"]


# ── handler ───────────────────────────────────────────────────────────────────
def _run(playlists, videos_by_pl, *, wished=None, today="2026-06-25"):
    adds = []

    def add_videos(playlist, videos):
        adds.append((playlist["youtube_id"], playlist["title"], [v["youtube_id"] for v in videos]))
        return len(videos)

    deps = _Deps()
    res = auto_video_scan_watchlist_playlists(
        {"_automation_id": "a"}, deps,
        fetch_playlists=lambda: playlists,
        fetch_videos=lambda pid: videos_by_pl.get(pid, []),
        wishlisted_ids=lambda pid: (wished or {}).get(pid, []),
        downloaded_ids=lambda pid: [], dismissed_ids=lambda pid: [],
        add_videos=add_videos, today_fn=lambda: today)
    return res, adds, deps


def test_wishlists_under_the_playlist_as_the_show():
    playlists = [{"playlist_id": "PL1", "title": "Best Sci-Fi", "poster_url": "/p.jpg"}]
    vids = [_vid("a", date="2024-01-01"), _vid("b", date="2023-01-01")]
    res, adds, _ = _run(playlists, {"PL1": vids})
    assert res["status"] == "completed" and res["playlists"] == 1 and res["videos_added"] == 2
    # the PLAYLIST id + title travel to add_videos → playlist-as-show organisation
    assert adds == [("PL1", "Best Sci-Fi", ["a", "b"])]


def test_rerun_only_adds_new_additions():
    playlists = [{"playlist_id": "PL1", "title": "Mix"}]
    vids = [_vid("new", date="2026-06-20"), _vid("old1"), _vid("old2")]
    res, adds, _ = _run(playlists, {"PL1": vids}, wished={"PL1": ["old1", "old2"]})
    assert adds == [("PL1", "Mix", ["new"])] and res["videos_added"] == 1


def test_multiple_playlists_independent():
    playlists = [{"playlist_id": "PL1", "title": "A"}, {"playlist_id": "PL2", "title": "B"}]
    videos = {"PL1": [_vid("a1")], "PL2": [_vid("b1")]}
    res, adds, _ = _run(playlists, videos)
    assert res["playlists"] == 2 and res["videos_added"] == 2


def test_one_unreachable_playlist_does_not_abort():
    playlists = [{"playlist_id": "PL1", "title": "Breaks"}, {"playlist_id": "PL2", "title": "Works"}]

    def fetch_videos(pid):
        if pid == "PL1":
            raise RuntimeError("yt-dlp blew up")
        return [_vid("ok")]

    adds = []
    res = auto_video_scan_watchlist_playlists(
        {"_automation_id": "a"}, _Deps(),
        fetch_playlists=lambda: playlists, fetch_videos=fetch_videos,
        wishlisted_ids=lambda pid: [], downloaded_ids=lambda pid: [],
        dismissed_ids=lambda pid: [], add_videos=lambda pl, v: len(v), today_fn=lambda: "2026-06-25")
    assert res["status"] == "completed" and res["videos_added"] == 1


def test_empty_watchlist_is_a_clean_noop():
    res, adds, _ = _run([], {})
    assert res["status"] == "completed" and res["playlists"] == 0 and adds == []


def test_top_level_error_is_caught():
    def boom():
        raise RuntimeError("watchlist read failed")
    deps = _Deps()
    res = auto_video_scan_watchlist_playlists({"_automation_id": "a"}, deps, fetch_playlists=boom)
    assert res["status"] == "error" and "watchlist read failed" in res["error"]
    assert any(p.get("status") == "error" for p in deps.progress)
