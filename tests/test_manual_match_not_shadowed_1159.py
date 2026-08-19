"""A fixed bad match came straight back on the next load (#1159, AfonsoG6).

    "Incorrect matches reappear after I have manually changed them to the
    correct ones, and the tracks I manually matched are shown in the extras
    instead. [...] almost as if the 'saved matches list' is not being
    consulted."

His example: "Yesterday (Remastered 2015)" auto-matched to "Hey Jude
(remastered 2009)" at 75%. He fixes it by hand; the reload shows Hey Jude
matched at 75% again and the real "Yesterday" sitting in Extras.

Two bugs compounded, and this file pins the first:

**The compare view treated EVERY sync_match_cache row as a user confirmation.**
The table holds two kinds of row: ``record_manual_match`` writes user picks at
confidence=1.0, and the sync fast-path stores every auto-match it makes at its
real score (>=0.7) purely so the next run can skip re-matching
(services/sync_service.py). Pass 0 documented itself as applying
"user-confirmed match overrides ... persisted at confidence=1.0" — but neither
``resolve_override_server_id`` nor ``build_bulk_override_lookup`` ever checked
the confidence. The sync's own 0.75 row for Yesterday→HeyJude was applied as if
the user had confirmed it, on every single load.

Worse: a cache hit that points into the playlist SHORT-CIRCUITS the durable
manual match — so even where the user's pick had been saved (Find & Add), the
bad auto row shadowed it. That is the "saved matches list is not being
consulted" half, literally.

The second bug (replace-track persisting nothing) is pinned in
``test_replace_track_persists_1159.py``.

The gate trusts a row with no readable confidence: only a numeric score below
1.0 proves the row came from the auto-matcher, and the sync always writes one.
Stub DBs in older tests return rows without the key — those must keep working.
"""

from types import SimpleNamespace

import pytest

from core.sync.match_overrides import (
    _cache_row_is_user_confirmed,
    build_bulk_override_lookup,
    resolve_match_overrides,
    resolve_override_server_id,
)


# ── the gate itself ─────────────────────────────────────────────────────────

@pytest.mark.parametrize("row,expected", [
    ({"confidence": 1.0}, True),          # record_manual_match's write
    ({"confidence": 0.75}, False),        # the sync's auto-match — his exact case
    ({"confidence": 0.7}, False),         # the sync's floor
    ({"confidence": 0.99}, False),        # high is still not confirmed
    ({}, True),                           # stub DBs omit the key — trust
    ({"confidence": None}, True),         # NULL column — can't prove auto
    ({"confidence": "junk"}, True),       # unreadable — can't prove auto
    ({"confidence": "1.0"}, True),        # stringly-typed storage
])
def test_only_a_provable_auto_score_is_distrusted(row, expected):
    assert _cache_row_is_user_confirmed(row) is expected


# ── the bulk lookup (what the compare view actually runs) ───────────────────

class _Db:
    """Bulk-capable stub: one cached row, one durable match."""

    def __init__(self, cache_rows=None, durable=None):
        self._cache = cache_rows or {}
        self._durable = durable or {}

    def read_sync_match_cache_bulk(self, ids, server_source):
        return {i: self._cache[i] for i in ids if i in self._cache}

    def find_manual_library_matches_bulk(self, profile_id, ids, server_source):
        return {i: self._durable[i] for i in ids if i in self._durable}


SOURCE = [{"source_track_id": "sp1", "name": "Yesterday (Remastered 2015)"}]


def _bulk(db, valid_ids):
    return build_bulk_override_lookup(db, 1, "jellyfin", valid_ids, SOURCE)


def test_an_auto_cached_bad_match_no_longer_pins_the_pairing():
    """The keystone. Hey Jude is still in the playlist, so before the gate the
    0.75 row was returned as an override every load."""
    db = _Db(cache_rows={"sp1": {"server_track_id": "heyjude", "confidence": 0.75}})
    assert _bulk(db, {"heyjude", "yesterday"})("sp1") is None


def test_the_shadowed_durable_match_is_consulted_again():
    """His words: 'almost as if the saved matches list is not being consulted.'
    The auto row short-circuited the durable path whenever its target was in
    the playlist — the user's saved pick never even got read."""
    db = _Db(
        cache_rows={"sp1": {"server_track_id": "heyjude", "confidence": 0.75}},
        durable={"sp1": {"library_track_id": "yesterday", "library_file_path": ""}},
    )
    assert _bulk(db, {"heyjude", "yesterday"})("sp1") == "yesterday"


def test_a_user_confirmed_row_still_wins():
    db = _Db(cache_rows={"sp1": {"server_track_id": "yesterday", "confidence": 1.0}})
    assert _bulk(db, {"heyjude", "yesterday"})("sp1") == "yesterday"


def test_a_row_without_confidence_is_still_trusted():
    """Stub compatibility: absence proves nothing, and older callers built rows
    without the key."""
    db = _Db(cache_rows={"sp1": {"server_track_id": "yesterday"}})
    assert _bulk(db, {"yesterday"})("sp1") == "yesterday"


def test_an_auto_row_with_no_durable_match_falls_to_normal_matching():
    """No confirmation anywhere -> pass 0 stays out of it entirely."""
    db = _Db(cache_rows={"sp1": {"server_track_id": "heyjude", "confidence": 0.75}})
    lookup = _bulk(db, {"heyjude"})
    assert lookup("sp1") is None
    assert lookup.dead_match_source_ids == set()


# ── the per-row resolver keeps identical semantics ──────────────────────────

def _per_row(db, valid_ids, cached_row):
    return resolve_override_server_id(
        db, 1, "sp1", "jellyfin", valid_ids, lambda *_a: cached_row)


class _DurableOnlyDb:
    def find_manual_library_match(self, profile_id, source, source_track_id, server_source=None):
        return None


def test_per_row_resolver_applies_the_same_gate():
    assert _per_row(_DurableOnlyDb(), {"heyjude"},
                    {"server_track_id": "heyjude", "confidence": 0.75}) is None
    assert _per_row(_DurableOnlyDb(), {"heyjude"},
                    {"server_track_id": "heyjude", "confidence": 1.0}) == "heyjude"


# ── end to end through resolve_match_overrides ──────────────────────────────

def test_his_playlist_pairs_to_the_saved_match_not_the_cached_auto_one():
    server = [
        {"id": "heyjude", "title": "Hey Jude (remastered 2009)"},
        {"id": "yesterday", "title": "Yesterday"},
    ]
    db = _Db(
        cache_rows={"sp1": {"server_track_id": "heyjude", "confidence": 0.75}},
        durable={"sp1": {"library_track_id": "yesterday", "library_file_path": ""}},
    )
    lookup = _bulk(db, {t["id"] for t in server})
    pairs = resolve_match_overrides(SOURCE, server, lookup)
    assert pairs == {0: 1}, "source row 0 must pair with Yesterday (index 1)"
