"""Video quality profile — the pure default/normalize/load/save seam (resolution
tiers + source/codec/HDR + size cap), isolated from the music side."""

from __future__ import annotations

import json

from core.video.quality_profile import (
    CODECS,
    MAX_SIZE_CAP_GB,
    RESOLUTIONS,
    SOURCES,
    default_profile,
    load,
    normalize,
    save,
)


def test_default_shape():
    d = default_profile()
    assert set(d["resolutions"]) == set(RESOLUTIONS)
    assert d["resolutions"]["1080p"]["enabled"] is True
    assert d["resolutions"]["2160p"]["enabled"] is False   # 4K off by default (size)
    assert d["source_priority"] == list(SOURCES)
    assert d["codec"] == "any" and d["prefer_hdr"] is False
    assert d["max_size_gb"] == 0 and d["fallback_enabled"] is True


def test_normalize_garbage_returns_default():
    assert normalize(None) == default_profile()
    assert normalize("nope") == default_profile()
    assert normalize(123) == default_profile()


def test_normalize_fills_gaps_and_coerces():
    out = normalize({
        "resolutions": {"2160p": {"enabled": True, "priority": "1"}},  # str priority coerced
        "codec": "x265",
        "prefer_hdr": 1,
        "max_size_gb": "75",
        "fallback_enabled": False,
    })
    assert out["resolutions"]["2160p"] == {"enabled": True, "priority": 1}
    assert out["resolutions"]["1080p"]["enabled"] is True   # untouched tier kept from default
    assert out["codec"] == "x265" and out["prefer_hdr"] is True
    assert out["max_size_gb"] == 75 and out["fallback_enabled"] is False


def test_normalize_rejects_bad_codec_and_clamps_size():
    assert normalize({"codec": "vp9"})["codec"] == "any"         # unknown codec rejected
    assert normalize({"max_size_gb": -5})["max_size_gb"] == 0    # negative clamped
    assert normalize({"max_size_gb": 99999})["max_size_gb"] == MAX_SIZE_CAP_GB   # capped


def test_normalize_source_priority_dedupes_and_completes():
    out = normalize({"source_priority": ["web-dl", "bogus", "web-dl"]})
    # known one first, rest appended in canonical order, no dupes, no junk
    assert out["source_priority"][0] == "web-dl"
    assert set(out["source_priority"]) == set(SOURCES)
    assert len(out["source_priority"]) == len(SOURCES)


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
    saved = save(db, {"codec": "x264", "max_size_gb": 40,
                      "resolutions": {"480p": {"enabled": True, "priority": 4}}})
    assert saved["codec"] == "x264" and saved["max_size_gb"] == 40
    assert json.loads(db.get_setting("quality_profile"))["codec"] == "x264"
    assert load(db) == saved


def test_load_recovers_from_corrupt_json():
    db = _FakeDB()
    db.set_setting("quality_profile", "{not json")
    assert load(db) == default_profile()


def test_codecs_constant():
    assert CODECS == ("any", "x265", "x264")
