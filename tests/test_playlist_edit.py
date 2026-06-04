"""Extreme battery for sync-editor add/remove planners (#768 Bug C)."""

from __future__ import annotations

from core.sync.playlist_edit import (
    normalize_sync_mode,
    plan_playlist_add,
    plan_playlist_reconcile,
    remove_one_occurrence,
)


# ── plan_playlist_add: link must not duplicate ────────────────────────────

def test_link_to_existing_track_does_not_insert():
    # The reported loop: matching an unmatched source to a track already in
    # the playlist (an "extra") must NOT add a second copy.
    plan = plan_playlist_add(["a", "b", "nv72"], "nv72", is_link=True)
    assert plan["should_insert"] is False
    assert plan["new_ids"] == ["a", "b", "nv72"]  # unchanged


def test_link_to_absent_track_inserts():
    plan = plan_playlist_add(["a", "b"], "nv99", is_link=True, position=1)
    assert plan["should_insert"] is True
    assert plan["new_ids"] == ["a", "nv99", "b"]


def test_non_link_add_always_inserts_even_if_present():
    # A plain add (no source link) may legitimately duplicate.
    plan = plan_playlist_add(["a", "b"], "a", is_link=False)
    assert plan["should_insert"] is True
    assert plan["new_ids"].count("a") == 2


def test_add_appends_when_no_position():
    plan = plan_playlist_add(["a", "b"], "c", is_link=False)
    assert plan["new_ids"] == ["a", "b", "c"]


def test_add_clamps_out_of_range_position():
    assert plan_playlist_add(["a"], "c", is_link=False, position=99)["new_ids"] == ["a", "c"]
    assert plan_playlist_add(["a"], "c", is_link=False, position=-5)["new_ids"] == ["c", "a"]


def test_add_stringifies_ids():
    plan = plan_playlist_add([1, 2, 72], 72, is_link=True)
    assert plan["should_insert"] is False


# ── remove_one_occurrence: remove ONE, not all ────────────────────────────

def test_removes_only_one_of_duplicates():
    # The #768 delete bug: two copies (pos 72, 73) — removing must drop ONE.
    new_ids, removed = remove_one_occurrence(["a", "nv72", "nv72", "b"], "nv72")
    assert removed is True
    assert new_ids == ["a", "nv72", "b"]  # one copy survives


def test_removes_exact_position_when_given():
    new_ids, removed = remove_one_occurrence(["x", "x", "x"], "x", position=1)
    assert removed is True
    assert new_ids == ["x", "x"]


def test_falls_back_to_first_when_position_mismatches():
    new_ids, removed = remove_one_occurrence(["a", "b", "c"], "b", position=0)
    assert removed is True
    assert new_ids == ["a", "c"]


def test_remove_absent_id_reports_not_removed():
    new_ids, removed = remove_one_occurrence(["a", "b"], "zzz")
    assert removed is False
    assert new_ids == ["a", "b"]


def test_remove_single_occurrence():
    new_ids, removed = remove_one_occurrence(["a", "b", "c"], "b")
    assert (new_ids, removed) == (["a", "c"], True)


def test_remove_stringifies():
    new_ids, removed = remove_one_occurrence([1, 2, 2, 3], 2)
    assert removed and new_ids == ["1", "2", "3"]


# ── plan_playlist_reconcile: in-place delta (#792) ────────────────────────

def test_reconcile_adds_new_keeps_existing():
    # 4-track playlist + a 5th in source → add only the 5th, remove nothing.
    plan = plan_playlist_reconcile(['a', 'b', 'c', 'd'], ['a', 'b', 'c', 'd', 'e'])
    assert plan == {'add': ['e'], 'remove': []}


def test_reconcile_removes_gone():
    # Source dropped 'b' → remove it, add nothing.
    plan = plan_playlist_reconcile(['a', 'b', 'c'], ['a', 'c'])
    assert plan == {'add': [], 'remove': ['b']}


def test_reconcile_add_and_remove_together():
    plan = plan_playlist_reconcile(['a', 'b', 'c'], ['a', 'c', 'd', 'e'])
    assert plan['add'] == ['d', 'e']       # desired order preserved
    assert plan['remove'] == ['b']


def test_reconcile_noop_when_identical():
    assert plan_playlist_reconcile(['a', 'b'], ['a', 'b']) == {'add': [], 'remove': []}


def test_reconcile_empty_desired_removes_all():
    assert plan_playlist_reconcile(['a', 'b'], []) == {'add': [], 'remove': ['a', 'b']}


def test_reconcile_empty_current_adds_all():
    assert plan_playlist_reconcile([], ['a', 'b']) == {'add': ['a', 'b'], 'remove': []}


def test_reconcile_stringifies_ids():
    plan = plan_playlist_reconcile([1, 2], [2, 3])
    assert plan == {'add': ['3'], 'remove': ['1']}


def test_reconcile_duplicate_still_desired_not_removed():
    # 'a' appears twice and is still desired → never queued for removal;
    # a gone id is listed once even if it had duplicates.
    plan = plan_playlist_reconcile(['a', 'a', 'b', 'b'], ['a'])
    assert plan == {'add': [], 'remove': ['b']}


# ── normalize_sync_mode: reconcile must survive resolution (#792 regression) ──

def test_normalize_keeps_reconcile_from_config():
    # The bug: a validation list omitting 'reconcile' downgraded the configured
    # setting to 'replace'. No per-request override → configured value wins.
    assert normalize_sync_mode(None, 'reconcile') == 'reconcile'
    assert normalize_sync_mode('', 'reconcile') == 'reconcile'


def test_normalize_request_overrides_config():
    assert normalize_sync_mode('append', 'reconcile') == 'append'
    assert normalize_sync_mode('reconcile', 'replace') == 'reconcile'


def test_normalize_falls_back_for_unknown():
    assert normalize_sync_mode('bogus', 'replace') == 'replace'
    assert normalize_sync_mode(None, None) == 'replace'
    assert normalize_sync_mode(None, 'also-bogus') == 'replace'


def test_normalize_all_real_modes_pass_through():
    for m in ('replace', 'append', 'reconcile'):
        assert normalize_sync_mode(m, 'replace') == m
