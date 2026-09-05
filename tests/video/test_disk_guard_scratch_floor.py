"""The scratch drive is judged by what the download needs, not by the library floor.

From Boulder's live install:

    Only 13.6 GB free on the temporary/working drive
    (C:\\Users\\nezre\\AppData\\Local\\Temp) — under your 100 GB minimum

The guard was working; the number was wrong. ``min_free_disk_gb`` answers "how
much headroom do I want left on my 18TB media drive". Applying it to a temp
directory refused a movie on a volume with room for it several times over.

Two different questions, so two different floors:

  library — the user's ``min_free_disk_gb``, unchanged.
  scratch — enough to ASSEMBLE this release: its size plus margin, never less
            than a small constant, and nothing to do with the library setting.

The margin exists because a download is remuxed and sometimes re-encoded in
place, so the working set runs bigger than the finished file.
"""

from __future__ import annotations

import pytest

from core.video import disk_guard


@pytest.fixture()
def drives(monkeypatch):
    """Two separate volumes with settable free space."""
    state = {"library": 5000.0, "scratch": 13.6}
    monkeypatch.setattr(disk_guard, "scratch_dir", lambda: "/scratch")
    monkeypatch.setattr(disk_guard, "free_gb",
                        lambda p: state["scratch"] if str(p).startswith("/scratch")
                        else state["library"])
    monkeypatch.setattr(disk_guard, "_same_volume", lambda a, b: False)
    return state


SETTINGS = {"min_free_disk_gb": 100}


# ── the reported case ────────────────────────────────────────────────────────
def test_a_big_library_floor_no_longer_refuses_a_roomy_scratch_drive(drives):
    """13.6GB of temp against a 100GB library floor used to refuse every grab."""
    res = disk_guard.check_room("/library", SETTINGS, needed_gb=2.0)
    assert res["ok"] is True, disk_guard.shortfall_message(res, "/library")


def test_the_library_floor_still_applies_to_the_library(drives):
    """The setting is not weakened - it just stops leaking onto the other drive."""
    drives["library"] = 50.0
    res = disk_guard.check_room("/library", SETTINGS, needed_gb=2.0)
    assert res["ok"] is False
    assert res["where"] == "library"
    assert res["floor"] == 100


# ── the scratch floor itself ─────────────────────────────────────────────────
def test_scratch_needs_room_for_the_release_plus_margin(drives):
    """Assembling, remuxing and re-encoding all happen in place, so the working
    set runs bigger than the finished file."""
    drives["scratch"] = 20.0
    # a 40GB remux needs 60GB of working room, and 20 is not enough
    res = disk_guard.check_room("/library", SETTINGS, needed_gb=40.0)
    assert res["ok"] is False and res["where"] == "scratch"
    assert res["floor"] == pytest.approx(60.0)

    # ...the same drive is fine for a 2GB movie
    assert disk_guard.check_room("/library", SETTINGS, needed_gb=2.0)["ok"] is True


def test_an_unknown_size_still_demands_a_workable_minimum(drives):
    """Callers that do not know the release size must not get a floor of zero:
    a scratch volume with 100MB free cannot assemble anything."""
    drives["scratch"] = 1.0
    res = disk_guard.check_room("/library", SETTINGS)
    assert res["ok"] is False and res["where"] == "scratch"
    assert res["floor"] == disk_guard.SCRATCH_MIN_GB


def test_the_floor_never_drops_below_the_minimum_for_a_tiny_release(drives):
    # a 200MB release still needs somewhere to work
    assert disk_guard.scratch_floor(0.2) == disk_guard.SCRATCH_MIN_GB
    assert disk_guard.scratch_floor(None) == disk_guard.SCRATCH_MIN_GB
    assert disk_guard.scratch_floor("junk") == disk_guard.SCRATCH_MIN_GB


def test_the_floor_scales_once_the_release_is_big_enough_to_matter(drives):
    assert disk_guard.scratch_floor(10.0) == pytest.approx(15.0)
    assert disk_guard.scratch_floor(80.0) == pytest.approx(120.0)


# ── the switches that must keep working ──────────────────────────────────────
def test_a_zero_library_floor_still_disables_the_whole_guard(drives):
    """The documented off switch. It must not be resurrected by the scratch
    floor having a non-zero default."""
    drives["library"] = 0.1
    drives["scratch"] = 0.1
    assert disk_guard.check_room("/library", {"min_free_disk_gb": 0}, needed_gb=50)["ok"] is True


def test_one_volume_is_reported_once(drives, monkeypatch):
    """Library and scratch on the same disk is one number, already judged."""
    monkeypatch.setattr(disk_guard, "_same_volume", lambda a, b: True)
    drives["library"] = 5000.0
    drives["scratch"] = 1.0        # would fail the scratch floor if judged again
    assert disk_guard.check_room("/library", SETTINGS, needed_gb=2.0)["ok"] is True


def test_an_unreadable_probe_never_wedges_a_download(drives, monkeypatch):
    monkeypatch.setattr(disk_guard, "free_gb", lambda p: None)
    assert disk_guard.check_room("/library", SETTINGS, needed_gb=2.0)["ok"] is True


# ── the message ──────────────────────────────────────────────────────────────
def test_the_message_names_the_scratch_drive_and_its_own_floor(drives):
    drives["scratch"] = 1.0
    res = disk_guard.check_room("/library", SETTINGS, needed_gb=2.0)
    msg = disk_guard.shortfall_message(res, "/library")
    assert "temporary/working drive" in msg
    # the number quoted must be the floor actually applied, not the library's
    assert "100" not in msg
    assert "5" in msg
