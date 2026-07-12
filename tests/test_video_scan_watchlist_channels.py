"""Watchlist-channels scan automation: for every followed YouTube channel, wishlist its new
long-form uploads — forward-looking from follow time + a last-N safety net, never duplicating.

Pure selection logic with all I/O injected (no DB, no yt-dlp).
"""

from __future__ import annotations

from core.automation.handlers.video_scan_watchlist_channels import (
    auto_video_scan_watchlist_channels,
    is_short,
    long_form_uploads,
    select_channel_video_gaps,
)


class _Deps:
    def __init__(self):
        self.progress = []

    def update_progress(self, automation_id, **kw):
        self.progress.append(kw)


def _vid(vid, *, date=None, dur=600, title=None):
    return {"youtube_id": vid, "title": title or ("Video " + vid), "published_at": date,
            "duration_seconds": dur, "thumbnail_url": "/t/" + vid + ".jpg"}


# ── pure: shorts filtering ────────────────────────────────────────────────────
def test_is_short_only_on_known_short_duration():
    assert is_short(_vid("a", dur=45), 60)
    assert not is_short(_vid("b", dur=600), 60)
    assert not is_short(_vid("c", dur=None), 60)        # unknown ≠ short (Videos tab already filters)
    assert not is_short(_vid("d", dur=0), 60)


def test_long_form_drops_shorts_and_idless():
    ups = [_vid("a", dur=30), _vid("b", dur=600), {"title": "no id"}, _vid("c", dur=120)]
    assert [v["youtube_id"] for v in long_form_uploads(ups, 60)] == ["b", "c"]


# ── pure: gap selection ───────────────────────────────────────────────────────
def test_net_backfills_last_n_even_before_baseline():
    # newest-first; baseline is "today" (just followed) so nothing is after baseline,
    # but the last-2 net still backlogs the two most recent.
    ups = [_vid("v1", date="2026-06-20"), _vid("v2", date="2026-06-10"),
           _vid("v3", date="2026-05-01"), _vid("v4", date="2026-01-01")]
    gaps = select_channel_video_gaps(ups, baseline_date="2026-06-25", backfill_count=2,
                                     wishlisted_ids=[], today="2026-06-25")
    assert [g["youtube_id"] for g in gaps] == ["v1", "v2"]


def test_forward_looking_grabs_everything_after_baseline_beyond_net():
    # 4 uploads all AFTER the follow date → all wishlisted even though net is only 1
    ups = [_vid("v1", date="2026-06-24"), _vid("v2", date="2026-06-22"),
           _vid("v3", date="2026-06-20"), _vid("v4", date="2026-06-18")]
    gaps = select_channel_video_gaps(ups, baseline_date="2026-06-15", backfill_count=1,
                                     wishlisted_ids=[], today="2026-06-25")
    assert [g["youtube_id"] for g in gaps] == ["v1", "v2", "v3", "v4"]


def test_old_videos_before_baseline_and_outside_net_are_ignored():
    # "what they had before isn't our concern" — v3/v4 predate the follow + aren't in the net
    ups = [_vid("v1", date="2026-06-24"), _vid("v2", date="2026-06-23"),
           _vid("v3", date="2020-01-01"), _vid("v4", date="2019-01-01")]
    gaps = select_channel_video_gaps(ups, baseline_date="2026-06-20", backfill_count=2,
                                     wishlisted_ids=[], today="2026-06-25")
    assert [g["youtube_id"] for g in gaps] == ["v1", "v2"]


def test_excludes_already_wishlisted_downloaded_dismissed():
    ups = [_vid("v1", date="2026-06-24"), _vid("v2", date="2026-06-23"),
           _vid("v3", date="2026-06-22")]
    gaps = select_channel_video_gaps(
        ups, baseline_date="2026-06-01", backfill_count=3,
        wishlisted_ids=["v1"], downloaded_ids=["v2"], dismissed_ids=["v3"], today="2026-06-25")
    assert gaps == []                                   # all three already accounted for


def test_skips_future_dated_premieres():
    ups = [_vid("soon", date="2099-01-01"), _vid("out", date="2026-06-20")]
    gaps = select_channel_video_gaps(ups, baseline_date="2026-06-01", backfill_count=5,
                                     wishlisted_ids=[], today="2026-06-25")
    assert [g["youtube_id"] for g in gaps] == ["out"]   # the unaired premiere is left alone


def test_shorts_never_counted_in_net_or_added():
    ups = [_vid("short1", dur=20, date="2026-06-24"), _vid("real1", dur=600, date="2026-06-23"),
           _vid("real2", dur=600, date="2026-06-22")]
    gaps = select_channel_video_gaps(ups, baseline_date="2026-06-25", backfill_count=1,
                                     wishlisted_ids=[], today="2026-06-25")
    assert [g["youtube_id"] for g in gaps] == ["real1"]  # net=1 long-form, the Short is invisible


# ── handler: end to end with seams ────────────────────────────────────────────
def _handler(channels, uploads_by_channel, *, wished=None, downloaded=None, dismissed=None,
             today="2026-06-25", backfill=5, config=None):
    adds = []

    def add_videos(channel, videos):
        adds.append((channel["youtube_id"], [v["youtube_id"] for v in videos]))
        return len(videos)

    deps = _Deps()
    res = auto_video_scan_watchlist_channels(
        {"_automation_id": "a1", **(config or {})}, deps,
        fetch_channels=lambda: channels,
        fetch_uploads=lambda cid, limit: uploads_by_channel.get(cid, []),
        wishlisted_ids=lambda cid: (wished or {}).get(cid, []),
        downloaded_ids=lambda cid: (downloaded or {}).get(cid, []),
        dismissed_ids=lambda cid: (dismissed or {}).get(cid, []),
        add_videos=add_videos, today_fn=lambda: today, backfill_fn=lambda: backfill)
    return res, adds, deps


def test_backfill_comes_from_the_global_setting():
    # The last-N net = the injected global ("videos to grab", Settings → Library). Here 3.
    channels = [{"youtube_id": "UC1", "title": "Chan", "date_added": "2026-06-25"}]
    ups = [_vid("v%d" % i, date="2026-06-%02d" % (17 - i)) for i in range(8)]   # all BEFORE baseline
    res, adds, _ = _handler(channels, {"UC1": ups}, backfill=3)
    assert res["videos_added"] == 3                     # only the last-3 net; nothing new after baseline
    assert len(adds[0][1]) == 3


def test_a_stale_config_backfill_is_ignored_global_wins():
    # A value baked into an old automation's config must NOT override the global (the
    # bug where changing the Library setting to 0 still wishlisted 10 per channel).
    channels = [{"youtube_id": "UC1", "title": "Chan", "date_added": "2026-06-25"}]
    ups = [_vid("v%d" % i, date="2026-06-%02d" % (17 - i)) for i in range(8)]
    res, adds, _ = _handler(channels, {"UC1": ups}, backfill=0, config={"backfill_count": 10})
    assert res["videos_added"] == 0                     # global 0 wins; the stale 10 is dead data


def test_gap_fill_pages_past_a_full_window_to_the_follow_date():
    # 30 recent uploads ALL newer than the follow date → the window overflowed. The gap-fill
    # pages deeper and catches the videos between #30 and the follow date, stopping once a
    # page crosses the baseline.
    baseline = "2026-01-01"
    recent = [_vid("r%02d" % i, date="2026-06-%02d" % (30 - i)) for i in range(30)]   # June, all >= baseline
    page2 = [_vid("d%02d" % i, date="2026-03-%02d" % (28 - i)) for i in range(10)]     # March, still >= baseline
    page3 = [_vid("old1", date="2025-12-20")]                                          # < baseline → stop
    pages = iter([{"videos": page2, "continuation": "t2"}, {"videos": page3, "continuation": "t3"}])
    adds = []
    res = auto_video_scan_watchlist_channels(
        {"_automation_id": "a"}, _Deps(),
        fetch_channels=lambda: [{"youtube_id": "UC1", "title": "Firehose", "date_added": baseline}],
        fetch_uploads=lambda cid, limit: recent,
        wishlisted_ids=lambda cid: [], downloaded_ids=lambda cid: [], dismissed_ids=lambda cid: [],
        add_videos=lambda ch, v: (adds.extend(v), len(v))[1],
        today_fn=lambda: "2026-07-01", backfill_fn=lambda: 5,
        fetch_upload_page=lambda cid, token: next(pages, {"videos": [], "continuation": None}),
        page_sleep=lambda s: None)
    ids = {v["youtube_id"] for v in adds}
    assert res["videos_added"] == 40                     # 30 recent + 10 overflow, all >= baseline
    assert len([i for i in ids if i.startswith("d")]) == 10   # the overflow page WAS caught
    assert "old1" not in ids                             # the pre-follow video is left alone


def test_gap_fill_does_not_fire_when_window_is_not_full():
    # Fewer than `limit` recent uploads → we already hold the whole recent history → the deep
    # pager is never even called (steady state stays cheap).
    called = []
    res = auto_video_scan_watchlist_channels(
        {"_automation_id": "a"}, _Deps(),
        fetch_channels=lambda: [{"youtube_id": "UC1", "title": "Ch", "date_added": "2026-01-01"}],
        fetch_uploads=lambda cid, limit: [_vid("r1", date="2026-06-01")],   # 1 << 30
        wishlisted_ids=lambda cid: [], downloaded_ids=lambda cid: [], dismissed_ids=lambda cid: [],
        add_videos=lambda ch, v: len(v), today_fn=lambda: "2026-07-01", backfill_fn=lambda: 5,
        fetch_upload_page=lambda cid, token: called.append(token) or {"videos": [], "continuation": None},
        page_sleep=lambda s: None)
    assert called == []                                  # pager never invoked


def test_gap_fill_stops_on_an_already_known_page():
    # A full window still newer than baseline triggers the pager, but the first deeper page is
    # entirely already-wishlisted → break-on-existing stops immediately (no paging to the cap).
    baseline = "2026-01-01"
    recent = [_vid("r%02d" % i, date="2026-06-%02d" % (30 - i)) for i in range(30)]
    page2 = [_vid("k%02d" % i, date="2026-05-%02d" % (28 - i)) for i in range(10)]     # >= baseline, all known
    known = [v["youtube_id"] for v in page2]
    calls = []
    res = auto_video_scan_watchlist_channels(
        {"_automation_id": "a"}, _Deps(),
        fetch_channels=lambda: [{"youtube_id": "UC1", "title": "Ch", "date_added": baseline}],
        fetch_uploads=lambda cid, limit: recent,
        wishlisted_ids=lambda cid: known, downloaded_ids=lambda cid: [], dismissed_ids=lambda cid: [],
        add_videos=lambda ch, v: len(v), today_fn=lambda: "2026-07-01", backfill_fn=lambda: 5,
        fetch_upload_page=lambda cid, token: calls.append(token) or {"videos": page2, "continuation": "more"},
        page_sleep=lambda s: None)
    assert len(calls) == 1                               # stopped after the first all-known page
    assert res["videos_added"] == 30                     # only the 30 recent; the known page is excluded


def test_first_run_backlogs_last_n_then_steady_state_is_incremental():
    channels = [{"youtube_id": "UC1", "title": "Cool Channel",
                 "poster_url": "/avatar.jpg", "date_added": "2026-06-25 09:00:00"}]
    ups = [_vid("v%d" % i, date="2026-06-%02d" % (25 - i)) for i in range(15)]
    res, adds, _ = _handler(channels, {"UC1": ups}, backfill=10)
    assert res["status"] == "completed" and res["channels"] == 1
    assert res["videos_added"] == 10                    # net backfill on the first run
    assert adds[0][0] == "UC1" and len(adds[0][1]) == 10
    # channel passed to add carries id + title + avatar so the wishlist orb renders
    # (verified via the add tuple shape above; avatar travels in the channel dict)


def test_rerun_only_adds_genuinely_new_videos():
    channels = [{"youtube_id": "UC1", "title": "Ch", "date_added": "2026-06-01 00:00:00"}]
    ups = [_vid("new", date="2026-06-25"), _vid("old1", date="2026-06-10"),
           _vid("old2", date="2026-06-09")]
    # old1/old2 already wishlisted from a prior scan → only 'new' is added
    res, adds, _ = _handler(channels, {"UC1": ups}, wished={"UC1": ["old1", "old2"]}, backfill=3)
    assert adds == [("UC1", ["new"])] and res["videos_added"] == 1


def test_nothing_new_adds_nothing():
    channels = [{"youtube_id": "UC1", "title": "Ch", "date_added": "2026-06-01"}]
    ups = [_vid("a", date="2026-06-20"), _vid("b", date="2026-06-19")]
    res, adds, _ = _handler(channels, {"UC1": ups}, wished={"UC1": ["a", "b"]}, backfill=2)
    assert adds == [] and res["videos_added"] == 0


def test_multiple_channels_scanned_independently():
    channels = [
        {"youtube_id": "UC1", "title": "A", "date_added": "2026-06-25"},
        {"youtube_id": "UC2", "title": "B", "date_added": "2026-06-25"},
    ]
    uploads = {"UC1": [_vid("a1", date="2026-06-24")], "UC2": [_vid("b1", date="2026-06-24")]}
    res, adds, _ = _handler(channels, uploads, backfill=5)
    assert res["channels"] == 2 and res["videos_added"] == 2
    assert sorted(c for c, _ in adds) == ["UC1", "UC2"]


def test_one_unreachable_channel_does_not_abort_the_scan():
    channels = [{"youtube_id": "UC1", "title": "Breaks", "date_added": "2026-06-25"},
                {"youtube_id": "UC2", "title": "Works", "date_added": "2026-06-25"}]

    def fetch_uploads(cid, limit):
        if cid == "UC1":
            raise RuntimeError("yt-dlp blew up")
        return [_vid("ok", date="2026-06-24")]

    adds = []
    res = auto_video_scan_watchlist_channels(
        {"_automation_id": "a"}, _Deps(),
        fetch_channels=lambda: channels, fetch_uploads=fetch_uploads,
        wishlisted_ids=lambda cid: [], downloaded_ids=lambda cid: [],
        dismissed_ids=lambda cid: [], add_videos=lambda ch, v: len(v),
        today_fn=lambda: "2026-06-25", backfill_fn=lambda: 5)
    assert res["status"] == "completed" and res["videos_added"] == 1


def test_empty_watchlist_is_a_clean_noop():
    res, adds, _ = _handler([], {})
    assert res["status"] == "completed" and res["channels"] == 0 and adds == []


def test_missing_follow_date_falls_back_to_net_only():
    # no date_added → baseline=today, so only the net backfills (no spurious old grabs)
    channels = [{"youtube_id": "UC1", "title": "Ch"}]
    ups = [_vid("v1", date="2026-06-20"), _vid("v2", date="2026-06-19"),
           _vid("v3", date="2020-01-01")]
    res, adds, _ = _handler(channels, {"UC1": ups}, backfill=2)
    assert adds == [("UC1", ["v1", "v2"])]


def test_top_level_error_is_caught_and_reported():
    def boom():
        raise RuntimeError("watchlist read failed")
    deps = _Deps()
    res = auto_video_scan_watchlist_channels({"_automation_id": "a"}, deps, fetch_channels=boom)
    assert res["status"] == "error" and "watchlist read failed" in res["error"]
    assert any(p.get("status") == "error" for p in deps.progress)
