"""Music min-free-disk guard (Discord: Kazimir Iskander's hung LXC).

A fresh non-Docker install left on the default ./downloads path downloads onto
the install disk — a Proxmox LXC's 8GB root — until the container hangs. The
video side has had a guard since its downloads phase; the music side had none.
Every music download (Soulseek AND streaming) funnels through
DownloadOrchestrator.download(), which now refuses when the download disk is
below ``soulseek.min_free_disk_gb`` (default 5, 0 = off). Probe failures never
block (a guard error must not wedge downloads).

Also: /api/settings carries ``_environment.docker`` so the paths UI can tell
the Docker story vs the bare-metal one and warn on the default-path landmine.
"""

from __future__ import annotations

from pathlib import Path

import pytest

import core.disk_guard as dg

_ROOT = Path(__file__).resolve().parent.parent
_SETTINGS_JS = (_ROOT / "webui" / "static" / "settings.js").read_text(encoding="utf-8")
_INDEX = (_ROOT / "webui" / "index.html").read_text(encoding="utf-8")


@pytest.fixture()
def real_floor(monkeypatch):
    """The suite pins the guard off (conftest); these tests turn it back on."""
    monkeypatch.setattr(dg, "_floor_override", None)


def test_guard_refuses_below_floor(real_floor, monkeypatch):
    monkeypatch.setattr(dg, "free_gb", lambda p: 1.2)
    ok, free, floor = dg.music_has_room()
    assert ok is False and free == 1.2 and floor == 5.0


def test_guard_passes_above_floor_and_when_disabled(real_floor, monkeypatch):
    monkeypatch.setattr(dg, "free_gb", lambda p: 42.0)
    assert dg.music_has_room()[0] is True
    monkeypatch.setattr(dg, "_floor_override", 0.0)
    monkeypatch.setattr(dg, "free_gb", lambda p: (_ for _ in ()).throw(AssertionError("must not probe when off")))
    assert dg.music_has_room() == (True, None, 0.0)


def test_probe_failure_never_blocks(real_floor, monkeypatch):
    monkeypatch.setattr(dg, "free_gb", lambda p: None)
    ok, free, _floor = dg.music_has_room()
    assert ok is True and free is None


def test_free_gb_walks_to_nearest_existing_ancestor(tmp_path):
    val = dg.free_gb(str(tmp_path / "does" / "not" / "exist" / "yet"))
    assert isinstance(val, float) and val > 0


def test_orchestrator_refuses_downloads_when_disk_full(monkeypatch):
    import asyncio

    from core.download_orchestrator import DownloadOrchestrator
    monkeypatch.setattr(dg, "_floor_override", None)
    monkeypatch.setattr(dg, "free_gb", lambda p: 0.5)
    orch = DownloadOrchestrator.__new__(DownloadOrchestrator)   # no client init
    with pytest.raises(RuntimeError, match="Download refused: only 0.5 GB free"):
        asyncio.run(orch.download("someuser", "some\\file.flac", 1000))


# ---------------------------------------------------------------------------
# Environment-aware settings UI contracts
# ---------------------------------------------------------------------------

def test_settings_ui_carries_the_guard_knob_and_environment_awareness():
    assert 'id="min-free-disk-gb"' in _INDEX
    assert "min_free_disk_gb" in _SETTINGS_JS                    # saved with soulseek block
    assert "function applyPathsEnvironment" in _SETTINGS_JS
    assert "_environment" in _SETTINGS_JS
    assert 'data-paths-guide="docker"' in _INDEX
    assert 'data-paths-guide="native"' in _INDEX
    assert "data-paths-default-warning" in _INDEX
    # the landmine banner only fires for non-Docker installs still on defaults
    assert "docker || !isDefault" in _SETTINGS_JS
