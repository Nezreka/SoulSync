"""Blended 'Recommended for you' aggregation (#discover phase 2)."""
from __future__ import annotations

from core.video.discovery_recs import blend_recommendations


def _it(tid, kind="movie", rating=0, pop=0, owned=False):
    d = {"tmdb_id": tid, "kind": kind, "rating": rating, "popularity": pop}
    if owned:
        d["library_id"] = 99
    return d


def test_consensus_ranks_higher():
    # title 2 recommended by 3 seeds, title 1 by 1 -> title 2 first
    lists = [[_it(1), _it(2)], [_it(2)], [_it(2), _it(3)]]
    out = blend_recommendations(lists)
    assert [i["tmdb_id"] for i in out][0] == 2


def test_excludes_owned_and_seeds():
    lists = [[_it(1), _it(2, owned=True), _it(3)]]
    out = blend_recommendations(lists, exclude_ids=[1])
    assert [i["tmdb_id"] for i in out] == [3]   # 1 = seed, 2 = owned


def test_ties_break_by_rating_then_popularity():
    lists = [[_it(1, rating=7, pop=10), _it(2, rating=9, pop=5), _it(3, rating=9, pop=50)]]
    # all count=1 -> rating desc (3,2 tie at 9 -> pop desc: 3 then 2), then 1
    assert [i["tmdb_id"] for i in blend_recommendations(lists)] == [3, 2, 1]


def test_dedup_same_title_across_lists_counts_once_per_list():
    lists = [[_it(5)], [_it(5)], [_it(5)]]
    out = blend_recommendations(lists)
    assert len(out) == 1 and out[0]["tmdb_id"] == 5


def test_kind_distinguishes_same_tmdb_id():
    lists = [[_it(7, kind="movie"), _it(7, kind="show")]]
    assert len(blend_recommendations(lists)) == 2


def test_limit():
    lists = [[_it(i, pop=i) for i in range(1, 11)]]
    assert len(blend_recommendations(lists, limit=3)) == 3


def test_empty():
    assert blend_recommendations([]) == []
    assert blend_recommendations([[], None]) == []
