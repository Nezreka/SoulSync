"""Quality evaluation seam — owned-copy-vs-profile verdict (resolution rank +
loose cutoff + codec reject), isolated from music. Shared by the Download modal
and the later-phase download engine."""

from __future__ import annotations

from core.video.quality_eval import (
    evaluate_owned,
    evaluate_release,
    meets_cutoff,
    resolution_label,
    resolution_rank,
    tier_key,
)
from core.video.quality_profile import default_profile
from core.video.release_parse import parse_release


def test_resolution_rank_agrees_across_formats():
    assert resolution_rank("2160p") == resolution_rank("4K") == resolution_rank("3840x2160")
    assert resolution_rank("1080p") == resolution_rank("1920x1080") == 3
    assert resolution_rank("720p") == 2
    assert resolution_rank("480p") == resolution_rank("576p") == 1
    assert resolution_rank("garbage") == 0


def test_resolution_label():
    assert resolution_label("1920x1080") == "1080p"
    assert resolution_label("2160p") == "4K"
    assert resolution_label(None) == ""


def test_meets_cutoff_loose_target():
    assert meets_cutoff("1080p", {"cutoff_resolution": "1080p"}) is True
    assert meets_cutoff("4K", {"cutoff_resolution": "1080p"}) is True       # better than target
    assert meets_cutoff("720p", {"cutoff_resolution": "1080p"}) is False    # below target
    assert meets_cutoff("4K", {"cutoff_resolution": ""}) is False           # always-upgrade


def test_evaluate_owned_meets_target():
    out = evaluate_owned({"resolution": "1080p", "video_codec": "x265"},
                         {"cutoff_resolution": "1080p", "rejects": ["cam"]})
    assert out["meets"] is True
    assert out["resolution_label"] == "1080p"
    assert out["reasons"][0]["ok"] is True


def test_evaluate_owned_below_target_is_upgradeable():
    out = evaluate_owned({"resolution": "720p", "video_codec": "x265"},
                         {"cutoff_resolution": "1080p", "rejects": []})
    assert out["meets"] is False
    assert any(not r["ok"] and "Below your 1080p target" in r["text"] for r in out["reasons"])


def test_evaluate_owned_flags_rejected_codec_even_if_resolution_ok():
    out = evaluate_owned({"resolution": "1080p", "video_codec": "AVC (H.264)"},
                         {"cutoff_resolution": "1080p", "rejects": ["x264"]})
    assert out["meets"] is False        # resolution fine, but codec is rejected
    assert any("x264 codec is on your reject list" in r["text"] for r in out["reasons"])


def test_evaluate_owned_always_upgrade_when_cutoff_empty():
    out = evaluate_owned({"resolution": "4K"}, {"cutoff_resolution": ""})
    assert out["meets"] is False
    assert "always chase the best" in out["reasons"][0]["text"]


def test_evaluate_owned_handles_garbage_inputs():
    assert evaluate_owned(None, None)["meets"] in (True, False)   # never raises
    assert evaluate_owned("nope", 42)["resolution_label"] == ""


def test_resolution_only_release_still_grabbable():
    # a known resolution with NO recognised source assumes web → lands on a tier
    # (instead of being rejected as 'unknown quality'); ffprobe verifies after.
    assert tier_key(None, "1080p") == "web-1080p"
    assert tier_key("web-dl", "720p") == "web-720p"          # plain WEB now resolves here
    assert tier_key(None, None) == ""                        # no res + no source → still unknown
    v = evaluate_release(parse_release("Show S01E01 720p"), default_profile(),
                         scope="episode", want_season=1, want_episode=1)
    assert v["accepted"] is True
