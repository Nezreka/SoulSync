"""Storage-unreachable guard for library stale-removal (artist sync, #828 pattern)."""

from __future__ import annotations

from core.library.stale_guard import is_implausible_orphan_flood as flood
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


# ── orphan-flood guard: same shape, protects the "move to staging" path ──────

def test_whole_library_flagged_orphan_is_blocked():
    # 4000/5000 files "orphaned" → a path mismatch, not real orphans.
    assert flood(4000, 5000) is True
    assert flood(21, 40) is True        # just over both floors (>20 and >50%)


def test_a_handful_of_real_orphans_still_surface():
    assert flood(3, 4000) is False      # a few stray files — report them
    assert flood(20, 30) is False       # at the absolute floor (not > 20)
    assert flood(2000, 4000) is False   # exactly 50% is NOT over the threshold


def test_orphan_flood_small_folders_never_blocked():
    # A 5-file folder that's all orphans is plausible (manual drop) — don't hide it.
    assert flood(5, 5) is False
    assert flood(20, 20) is False       # below the absolute orphan floor


def test_orphan_flood_edge_inputs():
    assert flood(0, 0) is False
    assert flood(0, 5000) is False      # nothing orphaned
    assert flood(5000, 0) is False      # nonsense totals don't trip it
