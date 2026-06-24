"""Listening-driven recommendation core (#913) — pure ranking + candidate aggregation."""

from __future__ import annotations

from core.discovery.listening_recommendations import (
    aggregate_candidate_tracks,
    rank_recommended_artists,
)


def _seed(name, weight=1.0):
    return {"name": name, "weight": weight}


# ── rank_recommended_artists ─────────────────────────────────────────────────
def test_consensus_outranks_single_endorsement():
    # 'Common' is similar to BOTH seeds; 'Solo' to one. Equal weights/scores.
    seeds = [_seed("A"), _seed("B")]
    sims = {
        "a": [{"name": "Common"}, {"name": "Solo"}],
        "b": [{"name": "Common"}],
    }
    out = rank_recommended_artists(seeds, sims)
    assert [r.name for r in out] == ["Common", "Solo"]
    assert out[0].seed_count == 2
    assert sorted(out[0].seeds) == ["A", "B"]
    assert out[1].seed_count == 1


def test_play_weight_boosts_a_seeds_similars():
    seeds = [_seed("Fav", weight=100), _seed("Minor", weight=1)]
    sims = {"fav": [{"name": "FromFav"}], "minor": [{"name": "FromMinor"}]}
    out = rank_recommended_artists(seeds, sims)
    assert out[0].name == "FromFav"          # heavier seed's similar wins


def test_similarity_score_weights_within_a_seed():
    seeds = [_seed("A")]
    sims = {"a": [{"name": "Close", "score": 0.9}, {"name": "Far", "score": 0.1}]}
    out = rank_recommended_artists(seeds, sims)
    assert [r.name for r in out] == ["Close", "Far"]


def test_owned_and_seed_artists_are_excluded():
    seeds = [_seed("A"), _seed("B")]
    sims = {"a": [{"name": "Owned"}, {"name": "B"}, {"name": "New"}]}  # 'B' is a seed
    out = rank_recommended_artists(seeds, sims, owned_artist_names={"owned"})
    assert [r.name for r in out] == ["New"]   # Owned dropped, seed B dropped


def test_min_seed_count_filters_low_consensus():
    seeds = [_seed("A"), _seed("B")]
    sims = {"a": [{"name": "Common"}, {"name": "Solo"}], "b": [{"name": "Common"}]}
    out = rank_recommended_artists(seeds, sims, min_seed_count=2)
    assert [r.name for r in out] == ["Common"]   # 'Solo' (1 seed) dropped


def test_case_insensitive_dedup_and_matching():
    seeds = [_seed("Radiohead")]
    sims = {"radiohead": [{"name": "Muse"}, {"name": "MUSE"}]}  # same artist twice
    out = rank_recommended_artists(seeds, sims)
    assert len(out) == 1 and out[0].name in ("Muse", "MUSE")
    assert out[0].score == 2.0                # accumulated (still one seed)
    assert out[0].seed_count == 1


def test_empty_and_limit():
    assert rank_recommended_artists([], {}) == []
    seeds = [_seed("A")]
    sims = {"a": [{"name": f"S{i}"} for i in range(10)]}
    assert len(rank_recommended_artists(seeds, sims, limit=3)) == 3


# ── aggregate_candidate_tracks ───────────────────────────────────────────────
def _recs(*names):
    return rank_recommended_artists(
        [_seed(n) for n in names],
        {n.lower(): [{"name": f"sim-{n}"}] for n in names},
    )


def test_aggregate_caps_per_artist_and_total_in_rank_order():
    recs = _recs("A", "B")  # -> recommended sim-A, sim-B
    tracks = {
        "sim-a": [{"name": "a1"}, {"name": "a2"}, {"name": "a3"}],
        "sim-b": [{"name": "b1"}, {"name": "b2"}],
    }
    out = aggregate_candidate_tracks(recs, tracks, per_artist=2, limit=10)
    names = [t["name"] for t in out]
    assert names == ["a1", "a2", "b1", "b2"]          # per_artist=2, rank order
    assert all(t["_seed_artist"].startswith("sim-") for t in out)


def test_aggregate_excludes_owned_when_requested():
    recs = _recs("A")
    tracks = {"sim-a": [{"name": "Owned Song"}, {"name": "New Song"}]}
    owned = {("sim-a", "owned song")}
    out = aggregate_candidate_tracks(recs, tracks, owned, per_artist=5, exclude_owned=True)
    assert [t["name"] for t in out] == ["New Song"]
    # replay flavor keeps owned
    keep = aggregate_candidate_tracks(recs, tracks, owned, per_artist=5, exclude_owned=False)
    assert [t["name"] for t in keep] == ["Owned Song", "New Song"]


def test_aggregate_dedups_and_respects_total_limit():
    recs = _recs("A", "B")
    tracks = {"sim-a": [{"name": "dup"}], "sim-b": [{"name": "dup"}, {"name": "x"}]}
    # 'dup' under sim-a and sim-b are different (artist,title) keys -> both kept;
    # within an artist a repeat would dedup. Here check the total limit instead.
    out = aggregate_candidate_tracks(recs, tracks, per_artist=5, limit=2)
    assert len(out) == 2


def test_aggregate_skips_artist_with_no_tracks():
    recs = _recs("A", "B")
    out = aggregate_candidate_tracks(recs, {"sim-a": [{"name": "only"}]}, per_artist=5)
    assert [t["name"] for t in out] == ["only"]   # sim-b had no tracks -> skipped
