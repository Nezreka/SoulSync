"""Listening-driven recommendation core (#913) — pure ranking + candidate aggregation."""

from __future__ import annotations

from core.discovery.listening_recommendations import (
    aggregate_candidate_tracks,
    apply_adventurousness,
    build_genre_taste_profile,
    build_recency_weighted_seeds,
    genre_affinity,
    choose_mix_fetch_source,
    names_match,
    rank_recommended_artists,
    similarity_from_rank,
    to_mix_track,
)


# ── choose_mix_fetch_source (universal Deezer fallback) ───────────────────────
def test_fetch_source_uses_active_when_it_can_fetch():
    assert choose_mix_fetch_source("spotify", True) == "spotify"
    assert choose_mix_fetch_source("deezer", True) == "deezer"


def test_fetch_source_falls_back_to_deezer_for_other_sources():
    # iTunes / Discogs / MusicBrainz can't fetch top tracks -> Deezer public.
    assert choose_mix_fetch_source("itunes", False) == "deezer"
    assert choose_mix_fetch_source("musicbrainz", False) == "deezer"
    assert choose_mix_fetch_source("discogs", False) == "deezer"


def test_fetch_source_falls_back_when_active_client_unavailable():
    # Active source is Spotify but its client isn't usable (not authed) -> Deezer.
    assert choose_mix_fetch_source("spotify", False) == "deezer"
    assert choose_mix_fetch_source(None, False) == "deezer"


# ── names_match (guards the top-tracks fetch against wrong-artist results) ─────
def test_names_match_ignores_case_and_punctuation():
    assert names_match("Tyler, The Creator", "Tyler The Creator")
    assert names_match("BEYONCÉ", "beyoncé")
    assert names_match("AC/DC", "ac dc")


def test_names_match_rejects_near_misses_and_empty():
    assert not names_match("Drake", "Drake Bell")
    assert not names_match("", "anything")
    assert not names_match("X", None)


def _seed(name, weight=1.0):
    return {"name": name, "weight": weight}


# ── similarity_from_rank (1=closest .. 10=farthest -> 1.0 .. 0.1) ─────────────
def test_similarity_from_rank_decays_over_documented_range():
    assert similarity_from_rank(1) == 1.0
    assert similarity_from_rank(5) == 0.6
    assert similarity_from_rank(10) == 0.1


def test_similarity_from_rank_clamps_and_defaults():
    assert similarity_from_rank(0) == 1.0          # <=1 -> full weight
    assert similarity_from_rank(50) == 0.1         # beyond range -> floor
    assert similarity_from_rank(None) == 1.0       # missing -> full weight (no rank info)
    assert similarity_from_rank("nan") == 1.0


# ── build_recency_weighted_seeds (lifetime + factor*recent) ───────────────────
def test_recency_boost_reorders_toward_current_taste():
    # Old-fav has more lifetime plays, but New-fav dominates recently.
    lifetime = [{"name": "OldFav", "play_count": 100}, {"name": "NewFav", "play_count": 40}]
    recent = {"newfav": 60}
    seeds = build_recency_weighted_seeds(lifetime, recent, recency_factor=1.5)
    by = {s["name"]: s["weight"] for s in seeds}
    assert by["OldFav"] == 100.0                   # no recent plays -> unchanged
    assert by["NewFav"] == 40 + 1.5 * 60           # 130 -> now outranks OldFav


def test_recency_factor_zero_is_pure_lifetime():
    seeds = build_recency_weighted_seeds(
        [{"name": "A", "play_count": 7}], {"a": 99}, recency_factor=0)
    assert seeds == [{"name": "A", "weight": 7.0}]


def test_recency_seeds_skip_blank_names_and_tolerate_missing_recent():
    seeds = build_recency_weighted_seeds([{"name": ""}, {"name": "A", "play_count": 3}])
    assert seeds == [{"name": "A", "weight": 3.0}]


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


# ── to_mix_track (source top-track dict -> Discover compact-row dict) ─────────
def _sp_track(tid="t1", name="Song", artist="Artist", album="Album", cover="http://cdn/c.jpg"):
    return {"id": tid, "name": name, "artists": [{"name": artist}],
            "album": {"name": album, "images": ([{"url": cover}] if cover else [])},
            "duration_ms": 210000, "popularity": 55}


def test_to_mix_track_shapes_render_fields():
    out = to_mix_track(_sp_track(), "spotify")
    assert out["track_name"] == "Song" and out["artist_name"] == "Artist"
    assert out["album_name"] == "Album" and out["album_cover_url"] == "http://cdn/c.jpg"
    assert out["duration_ms"] == 210000
    assert out["spotify_track_id"] == "t1" and out["track_id"] == "t1"
    assert out["track_data_json"]["id"] == "t1"      # full payload kept for sync
    assert out["name"] == "Song"                     # kept for aggregate dedup


def test_to_mix_track_source_id_field_per_source():
    assert to_mix_track(_sp_track(), "deezer")["deezer_track_id"] == "t1"
    assert to_mix_track(_sp_track(), "itunes")["itunes_track_id"] == "t1"


def test_to_mix_track_rejects_unusable_and_tolerates_missing_album():
    assert to_mix_track({"id": "x"}, "spotify") is None        # no title
    assert to_mix_track({"name": "y"}, "spotify") is None       # no id
    assert to_mix_track("garbage", "spotify") is None
    bare = to_mix_track({"id": "z", "name": "Z"}, "spotify")    # no artists/album
    assert bare["artist_name"] == "" and bare["album_cover_url"] is None


def test_to_mix_track_feeds_aggregate_end_to_end():
    # The real pipeline: shape source tracks, then aggregate by recommended artist.
    recs = _recs("A")  # recommends 'sim-A'
    shaped = [to_mix_track(_sp_track(tid="1", name="One", artist="sim-A"), "spotify"),
              to_mix_track(_sp_track(tid="2", name="Two", artist="sim-A"), "spotify")]
    out = aggregate_candidate_tracks(recs, {"sim-a": shaped}, per_artist=5, limit=10)
    assert [t["track_name"] for t in out] == ["One", "Two"]
    assert out[0]["spotify_track_id"] == "1"


# ── group_similars_by_seed (id->name join) ───────────────────────────────────
from dataclasses import dataclass as _dc  # noqa: E402

from core.discovery.listening_recommendations import group_similars_by_seed  # noqa: E402


@_dc
class _Row:
    source_artist_id: str
    similar_artist_name: str


def test_group_resolves_source_id_to_seed_name():
    seeds = [_seed("Radiohead"), _seed("Bjork")]
    rows = [
        _Row("id-rh", "Muse"),
        _Row("id-rh", "Coldplay"),
        _Row("id-bj", "Portishead"),
        _Row("id-unknown", "Nobody"),   # id not in map -> dropped
    ]
    id_to_name = {"id-rh": "Radiohead", "id-bj": "Bjork"}
    out = group_similars_by_seed(seeds, rows, id_to_name)
    assert {n["name"] for n in out["radiohead"]} == {"Muse", "Coldplay"}
    assert [n["name"] for n in out["bjork"]] == ["Portishead"]
    assert "id-unknown" not in out and "Nobody" not in str(out)


def test_group_keeps_only_rows_for_actual_seeds():
    # id resolves to a name, but that name isn't a seed -> dropped.
    seeds = [_seed("A")]
    rows = [_Row("id-a", "SimA"), _Row("id-x", "SimX")]
    out = group_similars_by_seed(seeds, rows, {"id-a": "A", "id-x": "X"})
    assert list(out.keys()) == ["a"]


def test_group_accepts_dict_rows():
    seeds = [_seed("A")]
    rows = [{"source_artist_id": "id-a", "similar_artist_name": "SimA"}]
    out = group_similars_by_seed(seeds, rows, {"id-a": "A"})
    assert out["a"] == [{"name": "SimA"}]


def test_group_then_rank_end_to_end():
    # The two-step the scanner will run: group rows, then rank.
    seeds = [_seed("A", weight=2), _seed("B", weight=1)]
    rows = [_Row("ia", "Common"), _Row("ia", "Solo"), _Row("ib", "Common")]
    grouped = group_similars_by_seed(seeds, rows, {"ia": "A", "ib": "B"})
    ranked = rank_recommended_artists(seeds, grouped, owned_artist_names={"solo"})
    assert ranked[0].name == "Common" and ranked[0].seed_count == 2
    assert all(r.name != "Solo" for r in ranked)   # owned excluded


# ── rank-aware grouping (similarity_rank -> score) ────────────────────────────
@_dc
class _RankRow:
    source_artist_id: str
    similar_artist_name: str
    similarity_rank: int


def test_group_with_rank_attr_carries_similarity_score():
    seeds = [_seed("A")]
    rows = [_RankRow("ia", "Close", 1), _RankRow("ia", "Far", 10)]
    out = group_similars_by_seed(seeds, rows, {"ia": "A"}, rank_attr="similarity_rank")
    by = {e["name"]: e["score"] for e in out["a"]}
    assert by == {"Close": 1.0, "Far": 0.1}


def test_group_without_rank_attr_is_scoreless_backcompat():
    seeds = [_seed("A")]
    rows = [_RankRow("ia", "X", 3)]
    out = group_similars_by_seed(seeds, rows, {"ia": "A"})
    assert out["a"] == [{"name": "X"}]            # no score key -> original behavior


def test_rank_threading_changes_winner_within_a_seed():
    # The production fix: a CLOSER match (rank 1) on a heavy seed beats a far match (rank 9),
    # even though both come from the same seed. Without rank threading they'd tie.
    seeds = [_seed("Fav", weight=10)]
    rows = [_RankRow("if", "Close", 1), _RankRow("if", "Far", 9)]
    grouped = group_similars_by_seed(seeds, rows, {"if": "Fav"}, rank_attr="similarity_rank")
    ranked = rank_recommended_artists(seeds, grouped)
    assert [r.name for r in ranked] == ["Close", "Far"]
    assert ranked[0].score > ranked[1].score


# ── apply_adventurousness (aurral-style popularity-penalty re-rank) ───────────
def test_adventurousness_zero_is_noop_but_copies():
    items = [{"name": "A", "score": 5.0, "popularity": 90},
             {"name": "B", "score": 4.0, "popularity": 10}]
    out = apply_adventurousness(items, 0.0)
    assert [i["name"] for i in out] == ["A", "B"]   # order unchanged
    assert out == items                              # same content
    assert out is not items                          # but a fresh list (additive)


def test_adventurousness_demotes_the_popular_one():
    # Same score; at full adventurousness the obscure pick (pop 10) overtakes the giant (pop 95).
    items = [{"name": "Giant", "score": 5.0, "popularity": 95},
             {"name": "Obscure", "score": 5.0, "popularity": 10}]
    assert [i["name"] for i in apply_adventurousness(items, 1.0)] == ["Obscure", "Giant"]


def test_adventurousness_penalty_is_proportional_not_absolute():
    # A much stronger score still wins despite being more popular — the penalty scales the score.
    items = [{"name": "StrongPopular", "score": 10.0, "popularity": 80},
             {"name": "WeakObscure", "score": 1.0, "popularity": 0}]
    assert apply_adventurousness(items, 0.5)[0]["name"] == "StrongPopular"


def test_adventurousness_missing_popularity_is_unpenalized():
    items = [{"name": "Popular", "score": 5.0, "popularity": 100},
             {"name": "NoPop", "score": 5.0}]
    assert apply_adventurousness(items, 1.0)[0]["name"] == "NoPop"


def test_adventurousness_clamps_level():
    items = [{"name": "A", "score": 5.0, "popularity": 100},
             {"name": "B", "score": 5.0, "popularity": 0}]
    assert [i["name"] for i in apply_adventurousness(items, 5.0)] == ["B", "A"]    # >1 clamps to 1
    assert [i["name"] for i in apply_adventurousness(items, -2.0)] == ["A", "B"]   # <0 clamps to 0 (no-op)


# ── genre affinity (aurral's missing tag signal) ─────────────────────────────
def test_taste_profile_aggregates_and_normalizes():
    profile = build_genre_taste_profile([
        (["Indie Rock", "Shoegaze"], 10),   # heavier artist
        (["Indie Rock", "Pop"], 4),         # lighter
    ])
    assert profile["indie rock"] == 1.0                    # 10+4=14 is the heaviest -> normalized to 1
    assert round(profile["shoegaze"], 4) == round(10 / 14, 4)
    assert round(profile["pop"], 4) == round(4 / 14, 4)


def test_taste_profile_empty_inputs():
    assert build_genre_taste_profile([]) == {}
    assert build_genre_taste_profile([([], 5), (None, 3)]) == {}
    assert build_genre_taste_profile([(["rock"], 0)]) == {}   # zero weight -> nothing learned


def test_genre_affinity_takes_the_best_matching_genre():
    profile = {"indie rock": 1.0, "shoegaze": 0.7, "pop": 0.3}
    assert genre_affinity(["Indie Rock"], profile) == 1.0          # your top genre
    assert genre_affinity(["Shoegaze", "Metal"], profile) == 0.7   # best of the candidate's genres
    assert genre_affinity(["Metal", "Jazz"], profile) == 0.0       # no overlap with your taste


def test_genre_affinity_is_additive_safe():
    # 0 whenever either side is empty -> a genreless candidate (or no taste data) is never penalized.
    assert genre_affinity([], {"rock": 1.0}) == 0.0
    assert genre_affinity(["rock"], {}) == 0.0
    assert genre_affinity(None, {"rock": 1.0}) == 0.0
