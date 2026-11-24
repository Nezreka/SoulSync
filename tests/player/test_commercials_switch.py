"""retro commercials only download while the admin has them on.

any profile or paired phone asking for a break used to start an
archive.org download into a cache of up to 500 MB, switch or no switch.
"""

from __future__ import annotations

import time

from core.player import commercials


def _settle():
    for _ in range(100):
        if not commercials._warming:
            return
        time.sleep(0.01)


def test_off_downloads_nothing(monkeypatch):
    calls = []
    monkeypatch.setattr(commercials, "_server_enabled", lambda: False)
    monkeypatch.setattr(commercials, "_download_archive_era", lambda *a, **k: calls.append(a))
    assert commercials.warm_async("80s") is False
    _settle()
    assert calls == []


def test_on_downloads(monkeypatch):
    calls = []
    monkeypatch.setattr(commercials, "_server_enabled", lambda: True)
    monkeypatch.setattr(commercials, "_download_archive_era", lambda *a, **k: calls.append(a))
    monkeypatch.setattr(commercials, "_clips_cache", {})
    assert commercials.warm_async("80s") is True
    _settle()
    assert calls


def test_an_unreadable_switch_counts_as_off(monkeypatch):
    def boom():
        raise RuntimeError("no db")
    monkeypatch.setattr("database.player_database.get_player_database", boom)
    assert commercials._server_enabled() is False


def test_a_break_with_an_empty_cache_does_not_download_when_off(monkeypatch):
    calls = []
    monkeypatch.setattr(commercials, "_server_enabled", lambda: False)
    monkeypatch.setattr(commercials, "_download_archive_era", lambda *a, **k: calls.append(a))
    monkeypatch.setattr(commercials, "_clips_cache", {})
    monkeypatch.setattr(commercials, "_scan_local_locked", lambda: None)
    monkeypatch.setattr(commercials, "_load_locked", lambda: None)
    assert commercials.pick_break(era="80s") == []
    _settle()
    assert calls == []
