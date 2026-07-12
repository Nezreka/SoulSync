"""Per-channel content filters (ytdl-sub match_filters parity).

apply_channel_filters runs before gap selection in the channel scan: title
include/exclude (comma-separated; /…/ = regex, else case-insensitive contains)
and a minimum duration. Broken regexes fail OPEN per-pattern (a typo must not
blank a channel's feed); unknown durations pass. Stored in the channel's cog
settings (the free-form KV dict — no schema change).
"""

from __future__ import annotations

from pathlib import Path

from core.automation.handlers.video_scan_watchlist_channels import (
    apply_channel_filters,
    auto_video_scan_watchlist_channels,
)

_ROOT = Path(__file__).resolve().parent.parent.parent
_YT_JS = (_ROOT / "webui" / "static" / "video" / "video-youtube.js").read_text(encoding="utf-8")


def _v(vid, title, dur=600):
    return {"youtube_id": vid, "title": title, "duration_seconds": dur,
            "published_at": "2026-07-01"}


UPLOADS = [_v("a", "GPU Review: RTX 5090"), _v("b", "Podcast #12 with a guest"),
           _v("c", "HW News 42", dur=None), _v("d", "Quick teaser", dur=90)]


def test_no_settings_pass_everything_through():
    assert apply_channel_filters(UPLOADS, {}) == UPLOADS
    assert apply_channel_filters(UPLOADS, None) == UPLOADS


def test_include_is_any_of_and_case_insensitive():
    out = apply_channel_filters(UPLOADS, {"title_include": "review, hw news"})
    assert [v["youtube_id"] for v in out] == ["a", "c"]


def test_regex_patterns_and_exclude():
    out = apply_channel_filters(UPLOADS, {"title_exclude": "/podcast|teaser/"})
    assert [v["youtube_id"] for v in out] == ["a", "c"]


def test_broken_regex_fails_open_per_pattern():
    # the bad pattern matches nothing; as an INCLUDE that means it contributes
    # no keeps — but a second good pattern still works
    out = apply_channel_filters(UPLOADS, {"title_include": "/([bad/, review"})
    assert [v["youtube_id"] for v in out] == ["a"]
    # as an EXCLUDE, a broken pattern drops nothing
    out2 = apply_channel_filters(UPLOADS, {"title_exclude": "/([bad/"})
    assert out2 == UPLOADS


def test_min_minutes_skips_known_short_keeps_unknown():
    out = apply_channel_filters(UPLOADS, {"min_minutes": 5})
    ids = [v["youtube_id"] for v in out]
    assert "d" not in ids            # 90s < 5min
    assert "c" in ids                # unknown duration passes


def test_scan_applies_the_channel_filters(monkeypatch):
    """End-to-end through the handler seams: the filtered video never reaches
    add_videos_to_wishlist."""
    added = []

    class _Deps:
        def update_progress(self, *a, **k):
            pass

    res = auto_video_scan_watchlist_channels(
        {"backfill_count": 10},
        _Deps(),
        fetch_channels=lambda: [{"youtube_id": "UC1", "title": "GN", "date_added": "2026-01-01"}],
        fetch_uploads=lambda cid, limit: list(UPLOADS),
        channel_settings=lambda cid: {"title_exclude": "podcast"},
        wishlisted_ids=lambda cid: [],
        dismissed_ids=lambda cid: [],
        downloaded_ids=lambda cid: [],
        add_videos=lambda ch, vids: added.extend(vids) or len(vids),
        today_fn=lambda: "2026-07-11",
    )
    assert res["status"] == "completed"
    ids = [v["youtube_id"] for v in added]
    assert "b" not in ids and "a" in ids


def test_cog_modal_has_the_filter_fields():
    for frag in ("data-cset-inc", "data-cset-exc", "data-cset-minm",
                 "title_include", "title_exclude", "min_minutes"):
        assert frag in _YT_JS, frag
