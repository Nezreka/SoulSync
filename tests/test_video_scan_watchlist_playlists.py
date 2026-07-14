"""Watchlist-playlists scan: the SAME 'cap + new' rule as channels. The first scan wishlists
only the newest N and baselines the whole current membership; later scans wishlist only
genuine additions (members not yet seen). Pure selection, all I/O injected.
"""

from __future__ import annotations

from core.automation.handlers.video_scan_watchlist_playlists import (
    auto_video_scan_watchlist_playlists,
    select_playlist_additions,
)


class _Deps:
    def __init__(self):
        self.progress = []

    def update_progress(self, automation_id, **kw):
        self.progress.append(kw)


def _vid(vid, *, date=None, dur=600):
    return {"youtube_id": vid, "title": "Video " + vid, "published_at": date,
            "duration_seconds": dur, "thumbnail_url": "/t/" + vid + ".jpg"}


# ── pure: first pass caps + baselines, later passes add only new ──────────────
def test_first_pass_caps_to_newest_n_and_baselines_everything():
    # empty seen → wishlist only the newest N (here 2), but baseline ALL current members
    vids = [_vid("a"), _vid("b"), _vid("c"), _vid("d")]
    picks, baseline = select_playlist_additions(vids, seen_ids=[], backfill_count=2, today="2026-06-25")
    assert [p["youtube_id"] for p in picks] == ["a", "b"]          # capped to the newest 2
    assert set(baseline) == {"a", "b", "c", "d"}                   # the WHOLE list is remembered


def test_first_pass_backfill_zero_grabs_nothing_but_still_baselines():
    vids = [_vid("a"), _vid("b")]
    picks, baseline = select_playlist_additions(vids, seen_ids=[], backfill_count=0, today="2026-06-25")
    assert picks == []                                            # 0 = no backfill
    assert set(baseline) == {"a", "b"}                            # still baselined → only additions later


def test_steady_state_adds_only_new_members():
    # seen = a,b,c (baselined earlier). Playlist now also has 'd' → only 'd' is added.
    vids = [_vid("a"), _vid("b"), _vid("c"), _vid("d")]
    picks, baseline = select_playlist_additions(
        vids, seen_ids=["a", "b", "c"], backfill_count=2, today="2026-06-25")
    assert [p["youtube_id"] for p in picks] == ["d"]
    assert baseline == ["d"]                                      # remember the addition


def test_flooded_playlist_self_migrates_without_re_adding():
    # An old mirror-flooded playlist: seen empty, but everything is already owned. First pass
    # adds nothing (all excluded) and just baselines the list → only future additions after.
    vids = [_vid("a"), _vid("b"), _vid("c")]
    picks, baseline = select_playlist_additions(
        vids, seen_ids=[], backfill_count=5,
        wishlisted_ids=["a", "b"], downloaded_ids=["c"], today="2026-06-25")
    assert picks == []                                            # nothing re-added
    assert set(baseline) == {"a", "b", "c"}                       # but the whole list is now baselined


def test_excludes_shorts_and_future_premieres_from_picks_and_baseline():
    vids = [_vid("short", dur=20), _vid("real", dur=600, date="2024-01-01"),
            _vid("premiere", dur=600, date="2099-01-01")]
    picks, baseline = select_playlist_additions(vids, seen_ids=[], backfill_count=5, today="2026-06-25")
    assert [p["youtube_id"] for p in picks] == ["real"]           # short + unaired excluded
    assert set(baseline) == {"real"}                             # premiere NOT baselined → grabs it once it airs


# ── handler ───────────────────────────────────────────────────────────────────
def _run(playlists, videos_by_pl, *, wished=None, seen=None, backfill=5, today="2026-06-25"):
    adds = []
    marked = {}

    def add_videos(playlist, videos):
        adds.append((playlist["youtube_id"], playlist["title"], [v["youtube_id"] for v in videos]))
        return len(videos)

    def mark_seen(pid, ids):
        marked.setdefault(pid, []).extend(ids)

    deps = _Deps()
    res = auto_video_scan_watchlist_playlists(
        {"_automation_id": "a"}, deps,
        fetch_playlists=lambda: playlists,
        fetch_videos=lambda pid: videos_by_pl.get(pid, []),
        wishlisted_ids=lambda pid: (wished or {}).get(pid, []),
        downloaded_ids=lambda pid: [], dismissed_ids=lambda pid: [],
        seen_ids=lambda pid: (seen or {}).get(pid, []),
        mark_seen=mark_seen, backfill_fn=lambda: backfill,
        add_videos=add_videos, today_fn=lambda: today)
    return res, adds, marked, deps


def test_handler_first_follow_caps_and_baselines():
    playlists = [{"playlist_id": "PL1", "title": "Best Sci-Fi", "poster_url": "/p.jpg"}]
    vids = [_vid("a"), _vid("b"), _vid("c")]
    res, adds, marked, _ = _run(playlists, {"PL1": vids}, backfill=2)
    assert res["status"] == "completed" and res["playlists"] == 1 and res["videos_added"] == 2
    assert adds == [("PL1", "Best Sci-Fi", ["a", "b"])]           # playlist-as-show, capped to 2
    assert set(marked["PL1"]) == {"a", "b", "c"}                  # whole list baselined


def test_handler_rerun_adds_only_new_additions():
    playlists = [{"playlist_id": "PL1", "title": "Mix"}]
    vids = [_vid("new"), _vid("old1"), _vid("old2")]
    res, adds, marked, _ = _run(playlists, {"PL1": vids}, seen={"PL1": ["old1", "old2"]})
    assert adds == [("PL1", "Mix", ["new"])] and res["videos_added"] == 1
    assert marked["PL1"] == ["new"]


def test_handler_multiple_playlists_independent():
    playlists = [{"playlist_id": "PL1", "title": "A"}, {"playlist_id": "PL2", "title": "B"}]
    videos = {"PL1": [_vid("a1")], "PL2": [_vid("b1")]}
    res, adds, _, _ = _run(playlists, videos, backfill=5)
    assert res["playlists"] == 2 and res["videos_added"] == 2


def test_one_unreachable_playlist_does_not_abort():
    playlists = [{"playlist_id": "PL1", "title": "Breaks"}, {"playlist_id": "PL2", "title": "Works"}]

    def fetch_videos(pid):
        if pid == "PL1":
            raise RuntimeError("yt-dlp blew up")
        return [_vid("ok")]

    res = auto_video_scan_watchlist_playlists(
        {"_automation_id": "a"}, _Deps(),
        fetch_playlists=lambda: playlists, fetch_videos=fetch_videos,
        wishlisted_ids=lambda pid: [], downloaded_ids=lambda pid: [], dismissed_ids=lambda pid: [],
        seen_ids=lambda pid: [], mark_seen=lambda pid, ids: None, backfill_fn=lambda: 5,
        add_videos=lambda pl, v: len(v), today_fn=lambda: "2026-06-25")
    assert res["status"] == "completed" and res["videos_added"] == 1


def test_empty_watchlist_is_a_clean_noop():
    res, adds, _, _ = _run([], {})
    assert res["status"] == "completed" and res["playlists"] == 0 and adds == []


def test_top_level_error_is_caught():
    def boom():
        raise RuntimeError("watchlist read failed")
    deps = _Deps()
    res = auto_video_scan_watchlist_playlists({"_automation_id": "a"}, deps, fetch_playlists=boom)
    assert res["status"] == "error" and "watchlist read failed" in res["error"]
    assert any(p.get("status") == "error" for p in deps.progress)
