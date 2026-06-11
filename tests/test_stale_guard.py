"""Storage-unreachable guard for library stale-removal (artist sync, #828 pattern)."""

from __future__ import annotations

from core.library.stale_guard import is_implausible_stale_removal as g


def test_all_missing_in_a_real_collection_is_blocked():
    # 40/40 missing → almost certainly a down mount, not 40 real deletions.
    assert g(40, 40) is True
    assert g(30, 40) is True            # 75% missing — still implausible


def test_a_few_genuinely_missing_files_are_allowed():
    assert g(3, 40) is False            # normal cleanup of a few gone files
    assert g(20, 40) is False           # exactly 50% is NOT over the threshold


def test_tiny_sets_are_never_blocked():
    # A 2-track artist legitimately losing both must still clean up.
    assert g(2, 2) is False
    assert g(4, 4) is False             # below min_total (5)


def test_edge_inputs():
    assert g(0, 0) is False
    assert g(0, 100) is False           # nothing missing
    assert g(5, 5) is True              # min_total met, all missing
