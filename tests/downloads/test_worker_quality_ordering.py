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
    monkeypatch.setattr(selection, "load_search_mode", lambda: mode)
    monkeypatch.setattr(
        selection, "load_rank_candidates_by_quality", lambda: rank_toggle
    )
    monkeypatch.setattr(selection, "load_profile_targets", lambda: (_TARGETS, True))


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
    def _boom():
        raise RuntimeError("db down")

    monkeypatch.setattr(selection, "load_search_mode", _boom)
    assert task_worker._candidate_ordering() == (False, None)
