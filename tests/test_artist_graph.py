"""Unit tests for the pure artist-similarity graph builder (Taste Map)."""
from core.graph.artist_graph import build_taste_map
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
