"""core.downloads.task_worker._candidate_ordering — decides whether the
download walk is ordered quality-first or confidence-first.

Quality-first applies when:
  - best_quality search mode is active (always), OR
  - priority mode AND the rank_candidates_by_quality toggle is on.

Default (priority mode, toggle off) → confidence-first, the byte-for-byte old
behaviour. Any error fails closed to confidence-first so a DB hiccup never
blocks a download.
"""

import core.quality.selection as selection
import core.downloads.task_worker as task_worker

_TARGETS = ["t1", "t2"]


def _patch(monkeypatch, *, mode, rank_toggle):
    # _candidate_ordering now resolves a profile via load_profile_by_id (the
    # global default when no track_info/quality_profile_id is passed) and
    # converts it with targets_from_profile — patch both seams directly so
    # this test stays focused on the quality-first DECISION logic, not the
    # real ranked_targets dict-parsing (covered by tests/quality/test_selection.py).
    monkeypatch.setattr(
        selection, "load_profile_by_id",
        lambda profile_id=None: {"search_mode": mode, "rank_candidates_by_quality": rank_toggle},
    )
    monkeypatch.setattr(selection, "targets_from_profile", lambda profile: (_TARGETS, True))


def test_priority_mode_toggle_off_is_confidence_first(monkeypatch):
    _patch(monkeypatch, mode="priority", rank_toggle=False)
    assert task_worker._candidate_ordering() == (False, None)


def test_priority_mode_toggle_on_is_quality_first(monkeypatch):
    _patch(monkeypatch, mode="priority", rank_toggle=True)
    assert task_worker._candidate_ordering() == (True, _TARGETS)


def test_best_quality_mode_is_quality_first_regardless_of_toggle(monkeypatch):
    _patch(monkeypatch, mode="best_quality", rank_toggle=False)
    assert task_worker._candidate_ordering() == (True, _TARGETS)


def test_fails_closed_to_confidence_first_on_error(monkeypatch):
    def _boom(profile_id=None):
        raise RuntimeError("db down")

    monkeypatch.setattr(selection, "load_profile_by_id", _boom)
    assert task_worker._candidate_ordering() == (False, None)


def test_uses_track_infos_own_quality_profile_id(monkeypatch):
    """A wishlist row's own quality_profile_id (denormalized at insert time)
    drives ordering, not just the global default."""
    seen_profile_ids = []

    def _fake_load(profile_id=None):
        seen_profile_ids.append(profile_id)
        return {"search_mode": "best_quality", "rank_candidates_by_quality": False}

    monkeypatch.setattr(selection, "load_profile_by_id", _fake_load)
    monkeypatch.setattr(selection, "targets_from_profile", lambda profile: (_TARGETS, True))

    result = task_worker._candidate_ordering({"quality_profile_id": 7})
    assert result == (True, _TARGETS)
    assert seen_profile_ids == [7]
