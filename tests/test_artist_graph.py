"""Unit tests for the pure artist-similarity graph builder (Taste Map)."""
from core.graph.artist_graph import build_taste_map, build_genre_grouped_map
# Row: (source_id, similar_name, spotify_id, deezer_id, itunes_id, occurrence_count, popularity)


def test_basic_owned_edge_collapses_and_sums():
    rows = [
        ("aid", "B", "bid", None, None, 3, 80),   # A -> B (B's ext id = bid, pop 80)
        ("bid", "A", "aid", None, None, 2, 60),   # B -> A (A's ext id = aid, pop 60)
    ]
    g = build_taste_map(rows, {"a", "b"})
    assert {n["key"] for n in g["nodes"]} == {"a", "b"}
    assert len(g["edges"]) == 1 and g["edges"][0]["weight"] == 5   # undirected, 3+2
    pops = {n["key"]: n["popularity"] for n in g["nodes"]}
    assert pops["b"] == 80 and pops["a"] == 60


def test_unowned_target_skipped():
    rows = [("aid", "Zztop", "zid", None, None, 1, 50), ("zid", "A", "aid", None, None, 1, 40)]
    g = build_taste_map(rows, {"a"})
    assert g["nodes"] == [] and g["edges"] == []


def test_unresolvable_source_skipped():
    g = build_taste_map([("unknownid", "A", "aid", None, None, 1, 40)], {"a"})
    assert g["edges"] == []


def test_self_loop_skipped():
    g = build_taste_map([("aid", "A", "aid", None, None, 1, 40)], {"a"})
    assert g["edges"] == []


def test_meta_enrichment():
    rows = [("aid", "B", "bid", None, None, 1, 80), ("bid", "A", "aid", None, None, 1, 60)]
    g = build_taste_map(rows, {"a", "b"}, artist_meta={"a": {"thumb_url": "u", "genres": ["rock"]}})
    a = next(n for n in g["nodes"] if n["key"] == "a")
    assert a["thumb"] == "u" and a["genres"] == ["rock"]


# ---- genre-grouped map: EVERY artist included, wired to a per-genre hub -----------------------

def _nodes_by_kind(g):
    return ([n for n in g["nodes"] if n["kind"] == "artist"],
            [n for n in g["nodes"] if n["kind"] == "genre"])


def test_grouped_includes_isolated_artists():
    """The bug this fixes: an artist with no owned<->owned edge must STILL appear (via its genre)."""
    artists = [("A", '["Rock"]', None), ("B", '["Rock"]', None), ("Lonely", '["Jazz"]', None)]
    rows = [("aid", "B", "bid", None, None, 1, 80), ("bid", "A", "aid", None, None, 1, 60)]
    g = build_genre_grouped_map(artists, rows, {"a", "b", "lonely"})
    art, gen = _nodes_by_kind(g)
    assert {n["key"] for n in art} == {"a", "b", "lonely"}          # Lonely is NOT dropped
    assert {n["genre"] for n in gen} == {"Rock", "Jazz"}
    # Lonely has no similarity edge but IS attached to its genre hub.
    membership = [e for e in g["edges"] if e["kind"] == "membership"]
    assert {"source": "lonely", "target": "genre::jazz", "weight": 1, "kind": "membership"} in membership


def test_grouped_reuses_similarity_edges():
    artists = [("A", '["Rock"]', None), ("B", '["Rock"]', None)]
    rows = [("aid", "B", "bid", None, None, 3, 80), ("bid", "A", "aid", None, None, 2, 60)]
    g = build_genre_grouped_map(artists, rows, {"a", "b"})
    sim = [e for e in g["edges"] if e["kind"] == "similarity"]
    assert len(sim) == 1 and sim[0]["weight"] == 5                  # same collapse+sum as build_taste_map


def test_grouped_artist_without_genre_has_no_hub_edge():
    artists = [("A", None, None), ("B", "[]", None), ("C", "not-json", None)]
    g = build_genre_grouped_map(artists, [], {"a", "b", "c"})
    art, gen = _nodes_by_kind(g)
    assert {n["key"] for n in art} == {"a", "b", "c"} and gen == []
    assert [e for e in g["edges"] if e["kind"] == "membership"] == []


def test_grouped_shares_genre_hub_and_dedups_artists():
    artists = [("A", '["Rock"]', None), ("B", '["Rock"]', None), ("A", '["Rock"]', None)]  # dup A
    g = build_genre_grouped_map(artists, [], {"a", "b"})
    art, gen = _nodes_by_kind(g)
    assert len(art) == 2                                            # dup A collapsed
    assert len(gen) == 1 and gen[0]["genre"] == "Rock"             # one shared hub
    assert len([e for e in g["edges"] if e["kind"] == "membership"]) == 2
