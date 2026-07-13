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


def test_movie_wrong_year_is_rejected():
    """A text search for a 2026 film must not match a differently-yeared movie whose title
    merely contains the words (Boulder's 'Troy The Odyssey 2017' / 'Moana 2 2024' cases)."""
    prof = default_profile()
    troy = evaluate_release(parse_release("Troy.The.Odyssey.2017.1080p.BluRay.x265-TGx"),
                            prof, scope="movie", want_year=2026)
    assert not troy["accepted"] and "year" in troy["rejected"].lower()
    moana = evaluate_release(parse_release("Moana 2 2024 1080p BluRay x265-PSA"),
                             prof, scope="movie", want_year=2026)
    assert not moana["accepted"] and "2024" in moana["rejected"]
    # the correct-year release still passes
    ok = evaluate_release(parse_release("The.Odyssey.2026.1080p.WEB-DL.H.264-GRP"),
                          prof, scope="movie", want_year=2026)
    assert ok["accepted"]
    # ±1 year slop is tolerated; a release with no year is not rejected on year
    assert evaluate_release(parse_release("Movie.2025.1080p.WEB-DL-GRP"), prof,
                            scope="movie", want_year=2026)["accepted"]
    assert evaluate_release(parse_release("Movie.1080p.WEB-DL-GRP"), prof,
                            scope="movie", want_year=2026)["accepted"]


def test_parse_release_year_uses_the_release_year_not_a_title_year():
    from core.video.release_parse import parse_release
    assert parse_release("Blade.Runner.2049.2017.1080p.BluRay")["year"] == 2017   # title year first, release last
    assert parse_release("Movie.2160p.WEB")["year"] is None                        # resolution ≠ year
