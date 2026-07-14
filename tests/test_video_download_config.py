"""Video download source-config — pure normalize for mode + hybrid chain
(soulseek/torrent/usenet only), isolated from music."""

from __future__ import annotations

import json

from core.video.download_config import (
    MODES,
    SOURCES,
    load,
    normalize_hybrid_order,
    normalize_mode,
    save,
)


def test_modes_are_video_only():
    assert SOURCES == ("soulseek", "torrent", "usenet")
    assert MODES == ("soulseek", "torrent", "usenet", "hybrid")


def test_normalize_mode():
    assert normalize_mode("torrent") == "torrent"
    assert normalize_mode("HYBRID") == "hybrid"
    assert normalize_mode("spotify") == "soulseek"   # music sources rejected
    assert normalize_mode(None) == "soulseek"
    assert normalize_mode("") == "soulseek"


def test_normalize_hybrid_order_filters_dedupes_defaults():
    assert normalize_hybrid_order(["torrent", "usenet"]) == ["torrent", "usenet"]
    assert normalize_hybrid_order(["torrent", "torrent", "spotify"]) == ["torrent"]
    assert normalize_hybrid_order([]) == ["soulseek"]        # never empty
    assert normalize_hybrid_order("garbage") == ["soulseek"]
    # Accepts a JSON string (as stored in the KV table).
    assert normalize_hybrid_order(json.dumps(["usenet", "soulseek"])) == ["usenet", "soulseek"]


class _FakeDB:
    def __init__(self):
        self._kv = {}

    def get_setting(self, key, default=None):
        return self._kv.get(key, default)

    def set_setting(self, key, value):
        self._kv[key] = value


def test_load_defaults():
    assert load(_FakeDB()) == {"download_mode": "soulseek", "hybrid_order": ["soulseek"]}


def test_save_validates_and_roundtrips():
    db = _FakeDB()
    out = save(db, {"download_mode": "hybrid", "hybrid_order": ["torrent", "bogus", "torrent", "usenet"]})
    assert out == {"download_mode": "hybrid", "hybrid_order": ["torrent", "usenet"]}
    assert load(db) == out                                  # persisted + reloads identically


def test_save_ignores_absent_keys():
    db = _FakeDB()
    save(db, {"download_mode": "usenet"})
    assert load(db)["download_mode"] == "usenet"
    save(db, {"hybrid_order": ["soulseek", "torrent"]})     # mode key absent → unchanged
    assert load(db)["download_mode"] == "usenet"
    assert load(db)["hybrid_order"] == ["soulseek", "torrent"]
