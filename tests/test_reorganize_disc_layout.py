"""Library Organize must not stamp a bogus disc prefix on a single-disc album
(#1080 QT3496: 'Oldorado' single-disc, track 11, re-matched a 2-disc source
edition → proposed '0211 - Oldorado').

The plan reads the user's REAL disc layout from their own track numbers and
only caps to single-disc when it's unambiguous, so genuine multi-disc is never
flattened:
  * repeating track numbers (a box set: 1..13 / 1..14) stay multi-disc (#1009);
  * continuously-numbered multi-disc (1..25 > disc-1 count) stays multi-disc.
Gated on the same preserve-my-organization setting as casing/year.
"""

from __future__ import annotations

import pytest

import core.library_reorganize as lr


@pytest.fixture(autouse=True)
def _preserve_on(monkeypatch):
    monkeypatch.setattr(lr, "_preserve_casing_enabled", lambda: True)


@pytest.fixture()
def plan_with(monkeypatch):
    """Run plan_album_reorganize with a stubbed source resolver — monkeypatch
    so the stub is auto-restored and never leaks into the next test file."""
    def _run(user_tracks, api_tracks):
        album_data = {"id": "AL1", "title": "A", "artist_name": "X",
                      "artist_id": "AR1", "spotify_album_id": "sp1"}
        api_album = {"id": "sp1", "name": "A", "release_date": "2018",
                     "total_tracks": len(api_tracks), "images": [{"url": ""}]}
        monkeypatch.setattr(lr, "_resolve_source",
                            lambda ad, ps, strict_source=False, **kw: ("spotify", api_album, api_tracks))
        return lr.plan_album_reorganize(album_data, user_tracks, "spotify")
    return _run


def _u(nums):
    return [{"id": "T%d" % i, "title": "S%d" % n, "track_number": n} for i, n in enumerate(nums)]


def _a(pairs):   # (track_number, disc_number)
    return [{"name": "S%d" % tn, "track_number": tn, "disc_number": dn,
             "artists": [{"name": "X"}]} for tn, dn in pairs]


# ── the fix ──────────────────────────────────────────────────────────────────

def test_single_disc_user_album_not_given_a_disc_prefix(plan_with):
    """User 1..11 (unique), source is 2-disc with disc 1 = 13 tracks → the user
    clearly has the single-disc version → capped to one disc, matched discs = 1."""
    user = _u(range(1, 12))
    api = _a([(n, 1) for n in range(1, 14)] + [(n, 2) for n in range(1, 6)])
    plan = plan_with(user, api)
    assert plan["total_discs"] == 1
    assert all(it["api_track"]["disc_number"] == 1 for it in plan["items"] if it["matched"])


def test_matched_disc2_track_is_flattened_to_disc1(plan_with):
    """A same-titled track existing on disc 2 (the reason the matcher grabbed a
    disc-2 entry in the first place) is still flattened once the album is
    detected as single-disc — so the path renders '11', not '0211'."""
    user = _u(range(1, 12))                       # 1..11, fits the 13-track disc 1
    api = _a([(n, 1) for n in range(1, 14)]) + _a([(11, 2)])   # a dup 'S11' on disc 2
    plan = plan_with(user, api)
    assert plan["total_discs"] == 1
    assert all(it["api_track"]["disc_number"] == 1 for it in plan["items"] if it["matched"])


def test_setting_off_keeps_source_disc_structure(monkeypatch, plan_with):
    monkeypatch.setattr(lr, "_preserve_casing_enabled", lambda: False)
    user = _u(range(1, 12))
    api = _a([(n, 1) for n in range(1, 14)] + [(n, 2) for n in range(1, 6)])
    assert plan_with(user, api)["total_discs"] == 2


# ── no regression: genuine multi-disc is never flattened ─────────────────────

def test_box_set_repeating_numbers_stays_multi_disc(plan_with):
    """Per-disc numbering (1..3 / 1..3) REPEATS → box set → left multi-disc
    (protects #1009)."""
    user = _u([1, 2, 3, 1, 2, 3])
    api = _a([(1, 1), (2, 1), (3, 1), (1, 2), (2, 2), (3, 2)])
    assert plan_with(user, api)["total_discs"] == 2


def test_continuously_numbered_multi_disc_stays_multi_disc(plan_with):
    """1..6 unique but disc 1 only holds 3 → the tracks spill past disc 1 → a
    genuine 2-disc set, not flattened."""
    user = _u(range(1, 7))
    api = _a([(n, 1) for n in range(1, 4)] + [(n, 2) for n in range(4, 7)])
    assert plan_with(user, api)["total_discs"] == 2


def test_genuinely_single_disc_source_is_untouched(plan_with):
    """A single-disc source (total_discs already 1) is a no-op for the cap."""
    user = _u(range(1, 6))
    api = _a([(n, 1) for n in range(1, 6)])
    assert plan_with(user, api)["total_discs"] == 1
