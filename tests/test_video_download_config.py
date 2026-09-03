"""Video download source-config — pure normalize for mode + hybrid chain
(soulseek/torrent/usenet only), isolated from music."""

from __future__ import annotations

import json

from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]

from core.video.download_config import (
    MODES,
    SOURCES,
    load,
    normalize_hybrid_order,
    normalize_mode,
    save,
)


def test_modes_are_video_only():
    """No streaming sources — those are music-only. EXT.to joined the list in
    861936c7a so it can be named explicitly in a hybrid chain; it is still a
    torrent underneath (the grab files it as one), it is just discoverable as
    its own lane."""
    assert SOURCES == ("soulseek", "torrent", "usenet", "extto")
    assert MODES == ("soulseek", "torrent", "usenet", "extto", "hybrid")
    for music_only in ("spotify", "tidal", "qobuz", "deezer", "youtube"):
        assert music_only not in SOURCES


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


# seeding lifecycle keys (arr-parity P5) ride the same config payload
_SEED_DEFAULTS = {"seed_ratio_goal": 0.0, "seed_time_goal_hours": 0, "seed_remove_data": True,
                  "seed_mode": "soulsync",
                  # per indexer rules, empty until someone sets one
                  "seed_overrides": {}}


def test_load_defaults():
    assert load(_FakeDB()) == {"download_mode": "soulseek", "hybrid_order": ["soulseek"],
                               **_SEED_DEFAULTS}


def test_save_validates_and_roundtrips():
    db = _FakeDB()
    out = save(db, {"download_mode": "hybrid", "hybrid_order": ["torrent", "bogus", "torrent", "usenet"]})
    assert out == {"download_mode": "hybrid", "hybrid_order": ["torrent", "usenet"],
                   **_SEED_DEFAULTS}
    assert load(db) == out                                  # persisted + reloads identically


def test_save_ignores_absent_keys():
    db = _FakeDB()
    save(db, {"download_mode": "usenet"})
    assert load(db)["download_mode"] == "usenet"
    save(db, {"hybrid_order": ["soulseek", "torrent"]})     # mode key absent → unchanged
    assert load(db)["download_mode"] == "usenet"
    assert load(db)["hybrid_order"] == ["soulseek", "torrent"]


def test_the_config_module_stays_cheap_to_import():
    """It is read on the sidebar service-status path. An earlier version pulled
    seed_rules from core.torrent_clients, whose __init__ imports every client
    adapter + the config manager — 479 modules to normalise a dict."""
    import subprocess
    import sys
    code = ("import sys; before=len(sys.modules);"
            "import core.video.download_config;"
            "print(len(sys.modules)-before)")
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                         cwd=str(_ROOT))
    pulled = int((out.stdout or "0").strip().splitlines()[-1])
    assert pulled < 120, "download_config now drags in %d modules" % pulled
