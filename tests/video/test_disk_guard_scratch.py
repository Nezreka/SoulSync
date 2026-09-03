"""The disk guard has to read BOTH drives a download touches.

Read out of Boulder's install on Sep 2 2026. Eighteen YouTube downloads died
with ``[Errno 28] No space left on device`` while the destination the guard was
checking — H: — had thirteen terabytes free. The volume the file was actually
being BUILT on (subtitle writes, the ffmpeg remux, ordinary Python temp) had
500MB, and nothing looked at it.

A guard that reads the wrong drive is worse than no guard: it answers the
question confidently, and the error it lets through names a drive with terabytes
spare, which reads as a bug in SoulSync rather than a full disk.
"""

from __future__ import annotations

import pytest

from core.video import disk_guard as g


@pytest.fixture()
def free(monkeypatch):
    """Set free space per path prefix; anything unlisted probes as unknown."""
    table = {}

    def fake(path):
        for prefix, gb in table.items():
            if str(path).startswith(prefix):
                return gb
        return None

    monkeypatch.setattr(g, "free_gb", fake)
    monkeypatch.setattr(g, "scratch_dir", lambda: "/scratch")
    # Different volumes unless a test says otherwise.
    monkeypatch.setattr(g, "_same_volume", lambda a, b: False)
    return table


def test_a_full_scratch_drive_stops_the_grab(free):
    """The exact live case: destination enormous, scratch nearly full."""
    free.update({"/library": 13000.0, "/scratch": 0.5})
    res = g.check_room("/library/tv", {"min_free_disk_gb": 5})
    assert res["ok"] is False
    assert res["where"] == "scratch"
    assert res["free"] == 0.5


def test_the_message_names_the_drive_that_is_actually_short(free):
    """'Only 0.5 GB free on /library/tv' when that drive has terabytes spare
    gets dismissed as a bug. It has to point at the right disk."""
    free.update({"/library": 13000.0, "/scratch": 0.5})
    msg = g.shortfall_message(g.check_room("/library/tv", {"min_free_disk_gb": 5}), "/library/tv")
    assert "temporary" in msg and "/scratch" in msg
    assert "/library/tv" not in msg


def test_a_full_library_drive_still_stops_it_and_says_so(free):
    free.update({"/library": 1.0, "/scratch": 500.0})
    res = g.check_room("/library/tv", {"min_free_disk_gb": 5})
    assert res["ok"] is False and res["where"] == "library"
    assert "/library/tv" in g.shortfall_message(res, "/library/tv")


def test_room_on_both_drives_passes(free):
    free.update({"/library": 500.0, "/scratch": 500.0})
    assert g.check_room("/library/tv", {"min_free_disk_gb": 5})["ok"] is True


def test_the_guard_stays_off_when_the_user_turned_it_off(free):
    """A floor of 0 means off, and adding a second drive must not quietly
    re-enable it for people who deliberately disabled the check."""
    free.update({"/library": 0.1, "/scratch": 0.1})
    for setting in ({"min_free_disk_gb": 0}, {}, None, {"min_free_disk_gb": "junk"}):
        assert g.check_room("/library/tv", setting)["ok"] is True


def test_an_unreadable_drive_answers_has_room(free):
    """Failure discipline, unchanged: a probe error must never wedge downloads."""
    free.update({"/scratch": 0.1})          # library unlisted -> unknown
    assert g.check_room("/nowhere", {"min_free_disk_gb": 5})["ok"] is False, \
        "an unknown library still lets the scratch check run"
    free.clear()                            # both unknown
    assert g.check_room("/nowhere", {"min_free_disk_gb": 5})["ok"] is True


def test_one_drive_is_not_reported_twice(monkeypatch, free):
    """When scratch and library are the same volume it is one number, already
    judged. Checking it again would just be noise."""
    free.update({"/library": 500.0, "/scratch": 500.0})
    monkeypatch.setattr(g, "_same_volume", lambda a, b: True)
    res = g.check_room("/library/tv", {"min_free_disk_gb": 5})
    assert res["ok"] is True and res["where"] == "library"


def test_the_old_two_value_form_still_works(free):
    """has_room is still called from older paths; it must keep its shape."""
    free.update({"/library": 13000.0, "/scratch": 0.5})
    ok, gb = g.has_room("/library/tv", {"min_free_disk_gb": 5})
    assert ok is False and gb == 0.5


def test_same_volume_is_false_when_it_cannot_be_told(monkeypatch):
    """An extra check on one drive is cheap; a skipped check on two is the bug,
    so an unanswerable question must not skip the scratch read.

    Patches the MODULE's own `os` reference, not the real os.stat. Breaking
    os.stat globally takes pytest down with it - it calls os.stat internally with
    follow_symlinks= - and the runner then dies with INTERNALERROR and reports
    zero failures, which reads exactly like a passing test.
    """
    import os as real_os

    class _BrokenStat:
        def __getattr__(self, name):
            return getattr(real_os, name)

        def stat(self, *a, **kw):
            raise OSError("cannot stat")

    monkeypatch.setattr(g, "os", _BrokenStat())
    assert g._same_volume("/a", "/b") is False
