"""Video quality profile — the pure default/normalize/load/save seam for the
rich-curated (Radarr-class) model: a ranked source×resolution tier ladder +
cutoff + hard rejects + soft codec/HDR/audio/repack preferences + size guard,
isolated from the music side."""

from __future__ import annotations

import json

from core.video.quality_profile import (
    AUDIO_MODES,
    CODECS,
    HDR_MODES,
    MAX_SIZE_CAP_GB,
    REJECTS,
    RESOLUTIONS,
    TIERS,
    default_profile,
    load,
    normalize,
    normalize_tiers,
    save,
)


def _keys(tiers):
    return [t["key"] for t in tiers]


def test_default_shape():
    d = default_profile()
    assert _keys(d["tiers"]) == list(TIERS)              # complete ladder, canonical order
    on = {t["key"] for t in d["tiers"] if t["enabled"]}
    assert "bluray-1080p" in on and "web-720p" in on     # 1080p/720p on
    assert "remux-2160p" not in on and "sdtv" not in on  # 4K + SD off by default
    assert d["cutoff_resolution"] == "1080p"          # loose resolution target
    assert "cam" in d["rejects"] and "x264" not in d["rejects"]   # junk blocked, x264 allowed
    assert d["prefer_codec"] == "hevc" and d["prefer_hdr"] == "prefer"
    assert d["prefer_audio"] == "any" and d["prefer_repack"] is True
    assert d["max_movie_gb"] == 0 and d["max_episode_gb"] == 0


def test_normalize_garbage_returns_default():
    assert normalize(None) == default_profile()
    assert normalize("nope") == default_profile()
    assert normalize(123) == default_profile()


def test_normalize_tiers_preserves_order_completes_and_coerces():
    # caller sends a re-ranked subset with a couple toggled; rest must be appended
    out = normalize_tiers([
        {"key": "web-1080p", "enabled": False},
        {"key": "bogus", "enabled": True},      # junk dropped
        {"key": "remux-2160p", "enabled": True},
        "web-1080p",                            # dupe ignored
    ])
    keys = _keys(out)
    assert keys[0] == "web-1080p" and keys[1] == "remux-2160p"   # caller order kept
    assert set(keys) == set(TIERS) and len(keys) == len(TIERS)   # ladder completed
    by = {t["key"]: t["enabled"] for t in out}
    assert by["web-1080p"] is False and by["remux-2160p"] is True
    assert by["bluray-1080p"] is True            # untouched default stays on


def test_normalize_cutoff_is_a_loose_resolution_target():
    assert normalize({"cutoff_resolution": "2160p"})["cutoff_resolution"] == "2160p"
    assert normalize({"cutoff_resolution": ""})["cutoff_resolution"] == ""        # best/always-upgrade
    assert normalize({"cutoff_resolution": "nonsense"})["cutoff_resolution"] == "1080p"  # falls back
    assert "2160p" in RESOLUTIONS                                                 # 4K always offered


def test_normalize_rejects_keep_canonical_order_and_drop_junk():
    out = normalize({"rejects": ["x264", "bogus", "cam", "x264"]})
    assert out["rejects"] == ["cam", "x264"]     # canonical order, valid only, deduped


def test_normalize_soft_prefs_validate():
    out = normalize({"prefer_codec": "av1", "prefer_hdr": "require",
                     "prefer_audio": "atmos", "prefer_repack": 0})
    assert out["prefer_codec"] == "av1" and out["prefer_hdr"] == "require"
    assert out["prefer_audio"] == "atmos" and out["prefer_repack"] is False
    # unknown enum values fall back to the default
    bad = normalize({"prefer_codec": "vp9", "prefer_hdr": "maybe", "prefer_audio": "8ch"})
    assert bad["prefer_codec"] == "hevc" and bad["prefer_hdr"] == "prefer"
    assert bad["prefer_audio"] == "any"


def test_normalize_size_splits_movie_and_episode_and_clamps():
    assert normalize({"max_movie_gb": -5})["max_movie_gb"] == 0          # negative clamped
    assert normalize({"max_movie_gb": 99999})["max_movie_gb"] == MAX_SIZE_CAP_GB   # capped
    out = normalize({"max_movie_gb": 40, "max_episode_gb": 5})           # independent caps
    assert out["max_movie_gb"] == 40 and out["max_episode_gb"] == 5


def test_constants():
    assert CODECS == ("any", "hevc", "av1")
    assert HDR_MODES == ("off", "prefer", "require")
    assert AUDIO_MODES == ("any", "surround", "lossless", "atmos")
    assert "cam" in REJECTS and "screener" in REJECTS


# ── DB round-trip via an injected fake ────────────────────────────────────────
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
    saved = save(db, {"prefer_codec": "av1", "max_movie_gb": 40, "cutoff_resolution": "2160p",
                      "tiers": [{"key": "remux-2160p", "enabled": True}]})
    assert saved["prefer_codec"] == "av1" and saved["max_movie_gb"] == 40
    assert saved["cutoff_resolution"] == "2160p"
    assert json.loads(db.get_setting("quality_profile"))["prefer_codec"] == "av1"
    assert load(db) == saved


def test_load_recovers_from_corrupt_json():
    db = _FakeDB()
    db.set_setting("quality_profile", "{not json")
    assert load(db) == default_profile()
