"""YouTube download quality profile — the small, yt-dlp-shaped seam (resolution
ceiling + codec + container + 60fps/HDR flags), isolated from the music side and
separate from the main Radarr-style video quality profile."""

from __future__ import annotations

import json

from core.video.youtube_quality import (
    CODECS,
    CONTAINERS,
    RESOLUTIONS,
    default_profile,
    format_selection,
    load,
    normalize,
    save,
)


def test_default_shape():
    d = default_profile()
    assert d["max_resolution"] == "1080p" and d["video_codec"] == "any"
    assert d["container"] == "mp4"
    assert d["prefer_60fps"] is True and d["allow_hdr"] is False


def test_constants():
    assert "best" in RESOLUTIONS and "2160p" in RESOLUTIONS   # no-cap + 4K offered
    assert CODECS == ("any", "av1", "vp9", "h264")
    assert CONTAINERS == ("mp4", "mkv", "webm")


def test_normalize_garbage_returns_default():
    assert normalize(None) == default_profile()
    assert normalize("nope") == default_profile()
    assert normalize(42) == default_profile()


def test_normalize_validates_enums_and_coerces_flags():
    out = normalize({"max_resolution": "2160p", "video_codec": "av1",
                     "container": "mkv", "prefer_60fps": 0, "allow_hdr": 1})
    assert out["max_resolution"] == "2160p" and out["video_codec"] == "av1"
    assert out["container"] == "mkv"
    assert out["prefer_60fps"] is False and out["allow_hdr"] is True


def test_normalize_rejects_unknown_values():
    bad = normalize({"max_resolution": "9000p", "video_codec": "theora", "container": "avi"})
    assert bad["max_resolution"] == "1080p"      # falls back to default
    assert bad["video_codec"] == "any"
    assert bad["container"] == "mp4"


class _FakeDB:
    def __init__(self):
        self._kv = {}

    def get_setting(self, key, default=None):
        return self._kv.get(key, default)

    def set_setting(self, key, value):
        self._kv[key] = value


def test_load_default_when_unset():
    assert load(_FakeDB()) == default_profile()


def test_save_then_load_roundtrips_normalized():
    db = _FakeDB()
    saved = save(db, {"max_resolution": "best", "container": "webm", "allow_hdr": True})
    assert saved["max_resolution"] == "best" and saved["container"] == "webm"
    assert saved["allow_hdr"] is True
    assert json.loads(db.get_setting("youtube_quality_profile"))["container"] == "webm"
    assert load(db) == saved


def test_load_recovers_from_corrupt_json():
    db = _FakeDB()
    db.set_setting("youtube_quality_profile", "{nope")
    assert load(db) == default_profile()


# ── yt-dlp format mapping (what the downloader passes through) ────────────────
def test_format_selection_default_caps_to_1080_sdr_60fps_mp4():
    sel = format_selection(default_profile())
    assert sel["format"] == "bv*[height<=1080]+ba/b[height<=1080]/bv*+ba/b"
    assert sel["merge_output_format"] == "mp4"
    # default: any-codec (no vcodec pref), prefer the cap res, 60fps, SDR
    assert sel["format_sort"] == ["res:1080", "fps", "hdr:SDR"]


def test_format_selection_best_has_no_height_cap():
    sel = format_selection({"max_resolution": "best", "video_codec": "any",
                            "prefer_60fps": False, "allow_hdr": True})
    assert sel["format"] == "bv*+ba/b"
    assert sel["format_sort"] == ["res"]          # no codec/fps/sdr prefs → just highest res


def test_format_selection_codec_pref_leads_the_sort():
    sel = format_selection({"max_resolution": "2160p", "video_codec": "av1",
                            "container": "mkv", "prefer_60fps": True, "allow_hdr": False})
    assert sel["format"].startswith("bv*[height<=2160]")
    assert sel["format_sort"] == ["vcodec:av1", "res:2160", "fps", "hdr:SDR"]
    assert sel["merge_output_format"] == "mkv"


def test_format_selection_allow_hdr_drops_the_sdr_rank():
    sel = format_selection({"max_resolution": "1080p", "video_codec": "any",
                            "prefer_60fps": True, "allow_hdr": True})
    assert "hdr:SDR" not in sel["format_sort"]


def test_format_selection_normalizes_garbage_input():
    # a junk profile still yields a valid default selection (never raises)
    assert format_selection("nope")["format"] == "bv*[height<=1080]+ba/b[height<=1080]/bv*+ba/b"
