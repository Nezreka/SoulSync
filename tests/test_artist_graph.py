"""Unit tests for the pure artist-similarity graph builder (Taste Map)."""
from core.graph.artist_graph import build_taste_map, build_genre_grouped_map, build_discovery_map
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


def test_grouped_carries_id_and_source_when_provided():
    artists = [("A", '["Rock"]', "http://t", 42, "spotify"), ("B", '["Rock"]', None)]  # 5-tuple + 3-tuple
    g = build_genre_grouped_map(artists, [], {"a", "b"})
    a = next(n for n in g["nodes"] if n["key"] == "a")
    b = next(n for n in g["nodes"] if n["key"] == "b")
    assert a["id"] == 42 and a["source"] == "spotify" and a["thumb"] == "http://t"
    assert b["id"] is None and b["source"] is None       # 3-tuple still works (back-compat)


def test_grouped_consolidates_to_top_hubs_and_reroutes_via_secondary():
    # Rock x3, Pop x2 are the top-2; Jazz/Blues are long-tail. max_hubs=2.
    artists = [
        ("A", '["Rock"]', None), ("B", '["Rock"]', None), ("C", '["Rock"]', None),
        ("D", '["Pop"]', None), ("E", '["Pop"]', None),
        ("F", '["Lofi", "Rock"]', None),   # primary Lofi (tail) -> reroute to Rock (anchor)
        ("G", '["Jazz"]', None),           # no anchor in list -> Other
    ]
    g = build_genre_grouped_map(artists, [], {"a", "b", "c", "d", "e", "f", "gg", "g"}, max_hubs=2)
    hubs = {n["genre"] for n in g["nodes"] if n["kind"] == "genre"}
    assert hubs == {"Rock", "Pop", "Other"}                 # only top-2 anchors + Other
    clusters = {n["key"]: n["cluster"] for n in g["nodes"] if n["kind"] == "artist"}
    assert clusters["f"] == "Rock"                          # rerouted via its secondary genre
    assert clusters["g"] == "Other"                         # long-tail-only -> Other
    assert clusters["a"] == "Rock" and clusters["d"] == "Pop"
    # F's true primary is preserved even though it clustered under Rock.
    f = next(n for n in g["nodes"] if n["key"] == "f")
    assert f["primary_genre"] == "Lofi"


def test_grouped_shares_genre_hub_and_dedups_artists():
    artists = [("A", '["Rock"]', None), ("B", '["Rock"]', None), ("A", '["Rock"]', None)]  # dup A
    g = build_genre_grouped_map(artists, [], {"a", "b"})
    art, gen = _nodes_by_kind(g)
    assert len(art) == 2                                            # dup A collapsed
    assert len(gen) == 1 and gen[0]["genre"] == "Rock"             # one shared hub
    assert len([e for e in g["edges"] if e["kind"] == "membership"]) == 2


# ---- discovery map: owned anchors -> UNOWNED similar candidates -------------------------------

def test_discovery_keeps_owned_to_unowned_only():
    rows = [
        ("aid", "X", "xid", None, None, 5, 70),   # A -> X (unowned)
        ("aid", "Y", "yid", None, None, 3, 60),   # A -> Y (unowned)
        ("aid", "B", "bid", None, None, 2, 50),   # A -> B (OWNED → excluded from discovery)
        ("bid", "A", "aid", None, None, 1, 40),   # gives 'aid' the name A
    ]
    g = build_discovery_map(rows, {"a", "b"})
    kinds = {n["key"]: n["kind"] for n in g["nodes"]}
    assert kinds["a"] == "owned"
    assert kinds["x"] == "discovery" and kinds["y"] == "discovery"
    assert "b" not in kinds                       # owned target is not a discovery node
    e = {(x["source"], x["target"]) for x in g["edges"]}
    assert ("a", "x") in e and ("a", "y") in e and ("a", "b") not in e


def test_discovery_enriches_from_cache_source_scoped():
    from core.graph.artist_graph import enrich_discovery_nodes
    rows = [
        ("aid", "X", None, "12345", None, 5, 70),   # X has Deezer id 12345
        ("bid", "A", "aid", None, None, 1, 40),
    ]
    g = build_discovery_map(rows, {"a"})
    x = next(n for n in g["nodes"] if n["key"] == "x")
    assert x["ids"] == [["deezer", "12345"]]        # ids carry their source
    # The cache has an iTunes artist with the SAME numeric id — it must NOT win (source-scoped keys).
    cache = {
        ("itunes", "12345"): {"image_url": "http://WRONG", "genres": ["Wrong"], "popularity": 1},
        ("deezer", "12345"): {"image_url": "http://img", "genres": ["Pop"], "popularity": 88},
    }
    enrich_discovery_nodes(g["nodes"], cache)
    assert x["image_url"] == "http://img" and x["genres"] == ["Pop"] and x["popularity"] == 88


def test_discovery_merges_duplicate_multisource_rows():
    """Reviewer finding: an anchor discovered against Spotify AND Deezer repeats each target; dupes
    must merge (max occ/pop, ids unioned) instead of eating per_anchor slots and inflating rank."""
    rows = [
        ("aid_sp", "X", "x_sp", None, None, 1, 10),   # X via the Spotify run (weak row)
        ("aid_dz", "X", None, "x_dz", None, 5, 70),   # X again via the Deezer run (strong row)
        ("aid_sp", "Y", "y_sp", None, None, 2, 20),
        ("bid", "A", "aid_sp", "aid_dz", None, 1, 40),  # both source ids resolve to A
    ]
    g = build_discovery_map(rows, {"a"}, per_anchor=2)
    disc = {n["key"]: n for n in g["nodes"] if n["kind"] == "discovery"}
    assert set(disc) == {"x", "y"}                  # X once, not twice — Y still gets its slot
    assert disc["x"]["ids"] == [["spotify", "x_sp"], ["deezer", "x_dz"]]   # ids unioned
    xedge = next(e for e in g["edges"] if e["target"] == "x")
    assert xedge["weight"] == 5                     # strongest evidence wins, not first-seen


def test_discovery_per_anchor_limit_keeps_strongest():
    rows = [("aid", f"T{i}", f"t{i}", None, None, i + 1, 0) for i in range(10)]  # occ 1..10
    rows.append(("t0", "A", "aid", None, None, 1, 0))                            # 'aid' → A
    g = build_discovery_map(rows, {"a"}, per_anchor=3)
    disc = sorted(n["label"] for n in g["nodes"] if n["kind"] == "discovery")
    assert disc == ["T7", "T8", "T9"]             # top 3 by occurrence_count


def test_discovery_seed_count_ranks_by_neighbor_count():
    rows = [
        ("aid", "X", "xid", None, None, 1, 0), ("aid", "Y", "yid", None, None, 1, 0),
        ("aid", "Z", "zid", None, None, 1, 0), ("bid", "W", "wid", None, None, 1, 0),
        ("xid", "A", "aid", None, None, 1, 0),    # 'aid' → A
        ("wid", "B", "bid", None, None, 1, 0),    # 'bid' → B
    ]
    g = build_discovery_map(rows, {"a", "b"}, seed_count=1)
    anchors = [n["key"] for n in g["nodes"] if n["kind"] == "owned"]
    assert anchors == ["a"]                       # A (3 neighbors) outranks B (1); only 1 seeded


# ---- expand-on-click: one node's similar artists, minus what's already on screen ---------------

def test_expand_matches_by_name_and_by_id():
    from core.graph.artist_graph import expand_discovery_node
    rows = [
        ("aid", "X", "xid", None, None, 5, 0),    # source resolves to A (by name)
        ("cid", "Y", "yid", None, None, 3, 0),    # source is cid — matches via node_ids
        ("bid", "A", "aid", None, None, 1, 0),    # teaches id2name: aid -> A
        ("bid", "Z", "zid", None, None, 9, 0),    # unrelated source -> excluded
    ]
    g = expand_discovery_node(rows, {"a"}, "A", node_ids=["cid"])
    keys = {n["key"] for n in g["nodes"]}
    assert keys == {"x", "y"}                     # Z's source matches neither name nor ids
    assert all(e["source"] == "a" for e in g["edges"])


def test_expand_skips_excluded_and_limits_by_strength():
    from core.graph.artist_graph import expand_discovery_node
    rows = [("aid", f"T{i}", f"t{i}", None, None, i, 0) for i in range(1, 6)]  # occ 1..5
    rows.append(("bid", "A", "aid", None, None, 1, 0))
    g = expand_discovery_node(rows, {"a"}, "A", per=2, exclude={"t5"})
    labels = sorted(n["label"] for n in g["nodes"])
    assert labels == ["T3", "T4"]                 # T5 excluded (on screen), then top-2 by occ


def test_expand_owned_target_comes_back_as_owned_node():
    from core.graph.artist_graph import expand_discovery_node
    rows = [
        ("aid", "B", "bid", None, None, 4, 0),    # B is OWNED -> comes back kind=owned
        ("aid", "X", "xid", None, None, 2, 0),    # X unowned -> discovery
        ("bid", "A", "aid", None, None, 1, 0),
    ]
    g = expand_discovery_node(rows, {"a", "b"}, "A", owned_meta={"b": {"id": 7, "thumb_url": "t"}})
    kinds = {n["key"]: n["kind"] for n in g["nodes"]}
    assert kinds == {"b": "owned", "x": "discovery"}
    b = next(n for n in g["nodes"] if n["key"] == "b")
    assert b["id"] == 7 and b["thumb"] == "t"


def test_expand_ranks_duplicate_target_by_strongest_row():
    """Reviewer finding: first-seen dedupe let a strong target rank by its weakest row."""
    from core.graph.artist_graph import expand_discovery_node
    rows = [
        ("aid", "Foo", None, "f_dz", None, 1, 0),   # Foo's weak Deezer row comes FIRST
        ("aid", "Foo", "f_sp", None, None, 5, 0),   # Foo's strong Spotify row
        ("aid", "Bar", "b_sp", None, None, 2, 0),
        ("bid", "A", "aid", None, None, 1, 0),
    ]
    g = expand_discovery_node(rows, {"a"}, "A", per=1)
    assert [n["label"] for n in g["nodes"]] == ["Foo"]   # occ 5 beats Bar's 2 (old code: occ 1 lost)
    assert g["edges"][0]["weight"] == 5
