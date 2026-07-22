"""Standalone Deep Scan planner — the #904 data-loss guard.

The scan relocates Transfer files the DB doesn't know about into Staging. With a
path-only diff, an empty/desynced DB makes the WHOLE library look untracked and the
scan moved all of it (reporter lost ~1,500 tracks into Staging). These pin the guard:
a normal batch of new arrivals still moves; an implausibly large untracked share (the
desync signature) or a 'permanent library' opt-out blocks the move instead.
"""

from __future__ import annotations

from core.library.standalone_scan import (
    BLOCK_DESYNC,
    BLOCK_NONE,
    BLOCK_TRANSFER_PERMANENT,
    diff_untracked,
    plan_standalone_deep_scan,
)


def _files(prefix, n, start=0):
    return {f"{prefix}/track{i}.flac" for i in range(start, start + n)}


# ── diff_untracked (pure path diff) ──────────────────────────────────────────

def test_diff_basic():
    transfer = {"/m/a.flac", "/m/b.flac", "/m/c.flac"}
    db = {"/m/a.flac", "/m/b.flac"}
    assert diff_untracked(transfer, db) == {"/m/c.flac"}


def test_diff_is_separator_normalized():
    # DB stored a Windows-style path; the on-disk path uses forward slashes → still a match
    transfer = {"/m/Artist/x.flac"}
    db = {"\\m\\Artist\\x.flac"}
    assert diff_untracked(transfer, db) == set()


def test_diff_all_untracked_when_db_empty():
    transfer = _files("/lib", 5)
    assert diff_untracked(transfer, set()) == transfer


# ── plan: normal (move allowed) ──────────────────────────────────────────────

def test_clean_library_not_blocked():
    transfer = _files("/lib", 1000)
    plan = plan_standalone_deep_scan(transfer, transfer)  # all known
    assert plan["untracked"] == set()
    assert plan["move_blocked"] is False
    assert plan["block_reason"] == BLOCK_NONE


def test_normal_new_arrivals_move():
    # DB knows 990; 10 new files dropped in → small share, moves as before
    known = _files("/lib", 990)
    transfer = known | _files("/lib", 10, start=990)
    plan = plan_standalone_deep_scan(transfer, known)
    assert len(plan["untracked"]) == 10
    assert plan["move_blocked"] is False


def test_small_fresh_import_under_floor_moves():
    # A tiny brand-new folder (under the absolute floor) isn't second-guessed
    transfer = _files("/lib", 5)
    plan = plan_standalone_deep_scan(transfer, set())
    assert len(plan["untracked"]) == 5
    assert plan["move_blocked"] is False


# ── plan: the #904 guard (move blocked) ──────────────────────────────────────

def test_regression_904_empty_db_full_library_blocks():
    # Empty DB + a real 1,500-track library → 100% untracked → BLOCKED, nothing moved
    transfer = _files("/library", 1500)
    plan = plan_standalone_deep_scan(transfer, set())
    assert len(plan["untracked"]) == 1500
    assert plan["move_blocked"] is True
    assert plan["block_reason"] == BLOCK_DESYNC


def test_majority_untracked_blocks():
    # 600 of 1000 unknown (60%, over the 50% line) → desync, blocked
    known = _files("/lib", 400)
    transfer = known | _files("/lib", 600, start=400)
    plan = plan_standalone_deep_scan(transfer, known)
    assert plan["move_blocked"] is True
    assert plan["block_reason"] == BLOCK_DESYNC


def test_minority_untracked_just_under_threshold_moves():
    # 400 of 1000 unknown (40%, under 50%) → still treated as a batch, not a desync
    known = _files("/lib", 600)
    transfer = known | _files("/lib", 400, start=600)
    plan = plan_standalone_deep_scan(transfer, known)
    assert plan["move_blocked"] is False


def test_never_move_blocks_even_small_sets():
    # Permanent-library opt-out: block regardless of fraction
    known = _files("/lib", 990)
    transfer = known | _files("/lib", 10, start=990)
    plan = plan_standalone_deep_scan(transfer, known, never_move=True)
    assert len(plan["untracked"]) == 10
    assert plan["move_blocked"] is True
    assert plan["block_reason"] == BLOCK_TRANSFER_PERMANENT


def test_never_move_with_nothing_untracked_is_not_blocked():
    # Nothing to move → not a "blocked" outcome even with the toggle on
    transfer = _files("/lib", 100)
    plan = plan_standalone_deep_scan(transfer, transfer, never_move=True)
    assert plan["untracked"] == set()
    assert plan["move_blocked"] is False
