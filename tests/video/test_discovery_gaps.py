"""Video discovery gap engine — the "what am I missing?" diffs.

Pins: collection gaps keep franchise order + drop owned; filmography gaps dedup,
filter by kind + vote_count, rank by popularity, and respect the owned set.
"""

from __future__ import annotations

from core.video.discovery_gaps import collection_gaps, filmography_gaps


def _m(tid, **kw):
    d = {"tmdb_id": tid, "kind": "movie"}
    d.update(kw)
    return d


# ── collection gaps ──────────────────────────────────────────────────────────

def test_collection_returns_unowned_in_order():
    coll = [_m(1, title="A"), _m(2, title="B"), _m(3, title="C")]
    out = collection_gaps({2}, coll)
    assert [x["title"] for x in out] == ["A", "C"]


def test_collection_all_owned_is_empty():
    coll = [_m(1), _m(2)]
    assert collection_gaps({1, 2}, coll) == []


def test_collection_dedups_and_skips_bad_ids():
    coll = [_m(1, title="A"), _m(1, title="dup"), _m(None, title="noid"), {"title": "nokey"}]
    out = collection_gaps(set(), coll)
    assert [x["title"] for x in out] == ["A"]


def test_collection_owned_ids_coerced():
    # owned ids may arrive as strings from the DB
    assert collection_gaps(["2"], [_m(1), _m(2)]) == [_m(1)]


# ── filmography gaps ─────────────────────────────────────────────────────────

def test_filmography_unowned_ranked_by_popularity():
    creds = [_m(1, popularity=5), _m(2, popularity=50), _m(3, popularity=20)]
    out = filmography_gaps({99}, creds)
    assert [x["tmdb_id"] for x in out] == [2, 3, 1]


def test_filmography_drops_owned_and_dupes():
    creds = [_m(1, popularity=9), _m(1, popularity=9), _m(2, popularity=1)]
    out = filmography_gaps({2}, creds)
    assert [x["tmdb_id"] for x in out] == [1]


def test_filmography_filters_by_kind():
    creds = [_m(1, popularity=9), {"tmdb_id": 2, "kind": "show", "popularity": 99}]
    assert [x["tmdb_id"] for x in filmography_gaps(set(), creds, kinds=("movie",))] == [1]
    assert [x["tmdb_id"] for x in filmography_gaps(set(), creds, kinds=("movie", "show"))] == [2, 1]


def test_filmography_min_vote_count_filters_obscure():
    creds = [_m(1, popularity=9, vote_count=2), _m(2, popularity=8, vote_count=500)]
    out = filmography_gaps(set(), creds, min_vote_count=50)
    assert [x["tmdb_id"] for x in out] == [2]


def test_filmography_limit():
    creds = [_m(i, popularity=i) for i in range(1, 6)]
    out = filmography_gaps(set(), creds, limit=2)
    assert [x["tmdb_id"] for x in out] == [5, 4]
