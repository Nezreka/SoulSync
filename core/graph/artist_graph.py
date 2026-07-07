"""Build an artist-similarity graph (nodes + edges) from the discovery ``similar_artists`` data.

Pure + side-effect free: the caller loads the rows (and the set of owned library-artist names) and
hands them here; this only shapes them into a ``{nodes, edges}`` payload for the frontend
(graphology -> sigma.js). No DB or Flask deps, so the graph logic is unit-testable in isolation.

The ``similar_artists`` grain is a DIRECTED edge ``source_artist_id -> similar_artist_name``, where the
source is an external ID (Spotify/Deezer/...) and the target is a name (plus its own external IDs). A
source ID is resolved to a name self-referentially — it usually also appears as some target's external
ID — done once in memory here (a SQL self-join over 75k rows is too slow).
"""

from __future__ import annotations

import json
from typing import Any, Dict, Iterable, List, Optional, Tuple

# Row shape from similar_artists:
# (source_artist_id, similar_artist_name, spotify_id, deezer_id, itunes_id, occurrence_count, popularity)
Row = Tuple[Any, Any, Any, Any, Any, Any, Any]


def _norm(name: Any) -> str:
    return str(name or "").strip().lower()


def _as_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def build_taste_map(
    rows: Iterable[Row],
    owned_names: set,
    artist_meta: Optional[Dict[str, Dict[str, Any]]] = None,
) -> Dict[str, List[dict]]:
    """Build the "Taste Map": your library artists wired together by direct similarity.

    ``owned_names`` is the set of library artist names, lowercased. ``artist_meta`` optionally maps a
    lowercased name to ``{thumb_url, genres}`` for richer nodes.

    Returns ``{"nodes": [...], "edges": [...]}``. Only edges where BOTH endpoints are owned library
    artists are kept (owned<->owned). Directed similarity collapses to undirected edges (weights
    summed). A node's ``popularity`` is the max seen for that artist as a similarity target.
    """
    meta = artist_meta or {}
    rows = list(rows)

    # Self-referential ID -> name: a source id is resolvable when it also appears as a target ext id.
    id2name: Dict[str, str] = {}
    for _src, name, sp, dz, it, _occ, _pop in rows:
        for eid in (sp, dz, it):
            if eid and eid not in id2name:
                id2name[eid] = name

    node_label: Dict[str, str] = {}
    node_pop: Dict[str, int] = {}
    edge_weight: Dict[Tuple[str, str], int] = {}

    def _touch(norm_name: str, label: str, pop: Any) -> None:
        node_label.setdefault(norm_name, label)
        p = _as_int(pop)
        if p > node_pop.get(norm_name, 0):
            node_pop[norm_name] = p

    for src, name, _sp, _dz, _it, occ, pop in rows:
        tgt = _norm(name)
        # Skip blank/whitespace names (bad scans put ""-named artists in owned_names, so the
        # `in owned_names` test alone lets them through) — a ""-keyed node/edge produces a
        # dangling edge that makes graphology reject the ENTIRE library graph on import.
        if not tgt or tgt not in owned_names:
            continue
        src_name = id2name.get(src)
        if not src_name:
            continue
        src_norm = _norm(src_name)
        if not src_norm or src_norm not in owned_names or src_norm == tgt:
            continue
        _touch(src_norm, src_name, None)      # source pop filled if it's a target elsewhere
        _touch(tgt, name, pop)
        key = (src_norm, tgt) if src_norm < tgt else (tgt, src_norm)
        edge_weight[key] = edge_weight.get(key, 0) + _as_int(occ, 1)

    nodes = [
        {
            "key": norm_name,
            "label": label,
            "owned": True,
            "popularity": node_pop.get(norm_name, 0),
            "thumb": meta.get(norm_name, {}).get("thumb_url"),
            "genres": meta.get(norm_name, {}).get("genres"),
        }
        for norm_name, label in node_label.items()
    ]
    edges = [{"source": a, "target": b, "weight": w} for (a, b), w in edge_weight.items()]
    return {"nodes": nodes, "edges": edges}


def _genre_list(genres: Any) -> List[str]:
    """An artist's genres as a clean list of strings (from a JSON array string, or already a list)."""
    if not genres:
        return []
    arr = genres
    if isinstance(genres, str):
        try:
            arr = json.loads(genres)
        except (ValueError, TypeError):
            return []
    if not isinstance(arr, list):
        return []
    return [s for s in (str(g).strip() for g in arr) if s]


def _primary_genre(genres: Any) -> Optional[str]:
    """First genre from an artist's ``genres`` value."""
    gl = _genre_list(genres)
    return gl[0] if gl else None


OTHER_GENRE = "Other"


def build_genre_grouped_map(
    artists: Iterable[Tuple[Any, Any, Any]],
    rows: Iterable[Row],
    owned_names: set,
    artist_meta: Optional[Dict[str, Dict[str, Any]]] = None,
    max_hubs: int = 16,
) -> Dict[str, List[dict]]:
    """Genre-anchored Taste Map: EVERY library artist as a node, grouped by genre + wired by similarity.

    ``artists`` is ``(name, genres, thumb_url[, id, source])`` for every library artist (``genres`` a
    JSON-array string; ``id``/``source`` optional, used by the frontend to link to the artist page).
    This includes the ~3.8k artists that have no owned<->owned similarity edge — they'd be dropped by
    :func:`build_taste_map` — by attaching each artist to a genre "hub" node so a force layout clusters
    them into legible islands. Similarity edges (owned<->owned) are reused verbatim.

    To keep the layout readable we DON'T make a hub per distinct genre (there are ~436, mostly tiny).
    Instead we keep the ``max_hubs`` most common primary genres as anchors and route every artist to
    the first of *its* genres that is an anchor (so a "Lo-fi house / Rap/Hip Hop" artist lands in the
    Rap/Hip Hop island even though its primary is the rare one). Anything with genres but no anchor
    match falls into a single ``Other`` hub; artists with no genre at all get no hub.

    Node kinds:
      * ``artist`` — ``{key, label, kind, owned, primary_genre, cluster, popularity, thumb, id, source}``
        where ``cluster`` is the anchor genre it was grouped under (drives color + membership).
      * ``genre``  — ``{key, label, kind, genre}`` (one hub per anchor genre, plus ``Other``)
    Edge kinds: ``similarity`` (from :func:`build_taste_map`) and ``membership`` (artist -> its cluster).
    """
    base = build_taste_map(rows, owned_names, artist_meta)
    pop_by = {n["key"]: n.get("popularity", 0) for n in base["nodes"]}
    artists = list(artists)

    # Pass 1: rank primary genres by frequency; the top ``max_hubs`` become the anchor set.
    prim_counts: Dict[str, int] = {}
    for row in artists:
        prim = _primary_genre(row[1])
        if prim:
            prim_counts[prim] = prim_counts.get(prim, 0) + 1
    anchor_set = set(sorted(prim_counts, key=lambda g: (-prim_counts[g], g))[:max_hubs])

    nodes: List[dict] = []
    edges: List[dict] = [{**e, "kind": "similarity"} for e in base["edges"]]
    seen_artist: set = set()
    genre_labels: Dict[str, str] = {}

    for row in artists:
        name, genres, thumb = row[0], row[1], row[2]
        artist_id = row[3] if len(row) > 3 else None
        source = row[4] if len(row) > 4 else None
        key = _norm(name)
        if not key or key in seen_artist:
            continue
        seen_artist.add(key)

        gl = _genre_list(genres)
        prim = gl[0] if gl else None
        # Cluster = first of the artist's genres that's an anchor; else Other (only if it has genres).
        cluster = next((g for g in gl if g in anchor_set), None)
        if cluster is None and gl:
            cluster = OTHER_GENRE

        nodes.append({
            "key": key,
            "label": name,
            "kind": "artist",
            "owned": True,
            "primary_genre": prim,
            "cluster": cluster,
            "popularity": pop_by.get(key, 0),
            "thumb": thumb,
            "id": artist_id,
            "source": source,
        })
        if cluster:
            gkey = "genre::" + cluster.strip().lower()
            genre_labels.setdefault(gkey, cluster)
            edges.append({"source": key, "target": gkey, "weight": 1, "kind": "membership"})

    for gkey, glabel in genre_labels.items():
        nodes.append({"key": gkey, "label": glabel, "kind": "genre", "genre": glabel})

    return {"nodes": nodes, "edges": edges}


def _merge_target(bucket: Dict[str, list], name: str, sp: Any, dz: Any, it: Any, occ: Any, pop: Any,
                  image_url: Any = None, genres: Any = None) -> None:
    """Fold one similar-artist row into a per-target bucket, keyed by normalized name.

    The same target often appears in several rows (the anchor was discovered against Spotify AND
    Deezer), so we merge: max occurrence/popularity (a target ranks by its STRONGEST evidence, not
    whichever row happened first), the union of external ids, and the first non-empty image/genres
    (similar_artists rows carry their own enrichment — ~99% have image_url + popularity).
    Bucket value: [label, sp, dz, it, occ, pop, image_url, genres].
    """
    tkey = _norm(name)
    cur = bucket.get(tkey)
    if cur is None:
        bucket[tkey] = [name, sp, dz, it, _as_int(occ, 1), _as_int(pop), image_url or None, genres or None]
        return
    cur[1] = cur[1] or sp
    cur[2] = cur[2] or dz
    cur[3] = cur[3] or it
    cur[4] = max(cur[4], _as_int(occ, 1))
    cur[5] = max(cur[5], _as_int(pop))
    cur[6] = cur[6] or image_url or None
    cur[7] = cur[7] or genres or None


def _id_pairs(sp: Any, dz: Any, it: Any) -> List[List[Any]]:
    """External ids as ``[source, id]`` pairs — the source matters: Deezer and iTunes ids are both
    numeric and can collide, so consumers (cache enrichment, watchlist add) must know which is which.
    """
    return [[s, e] for s, e in (("spotify", sp), ("deezer", dz), ("itunes", it)) if e]


def _row7(row: Any) -> Tuple[Any, Any, Any, Any, Any, Any, Any]:
    """The 7 core columns of a similar_artists row; rows may carry image_url/genres at [7]/[8]."""
    return row[0], row[1], row[2], row[3], row[4], row[5], row[6]


def _row_enrich(row: Any) -> Tuple[Any, Any]:
    """Optional row-level enrichment columns: (image_url, genres). Absent on 7-tuples."""
    return (row[7] if len(row) > 7 else None), (row[8] if len(row) > 8 else None)


def build_discovery_map(
    rows: Iterable[Row],
    owned_names: set,
    owned_meta: Optional[Dict[str, Dict[str, Any]]] = None,
    seed_count: Optional[int] = None,
    per_anchor: Optional[int] = None,
) -> Dict[str, List[dict]]:
    """Discovery map: owned artists as anchors, their UNOWNED similar artists as discovery candidates.

    The inverse of :func:`build_taste_map`'s filter — instead of owned<->owned, we keep owned->UNowned
    edges (new artists to find). By default the WHOLE frontier is returned (real-world size is modest:
    only artists whose similars were fetched can anchor). ``seed_count`` optionally keeps just the top
    anchors (ranked by distinct unowned neighbors) and ``per_anchor`` each anchor's top candidates (by
    max consensus, then popularity — duplicate multi-source rows are merged, see :func:`_merge_target`).

    Rows may be 7-tuples or extended with ``image_url, genres`` at [7]/[8] — similar_artists rows
    carry their own enrichment (~99% have image_url + real popularity), so no extra lookup is needed.

    Node kinds: ``owned`` (anchor: ``{key,label,owned,kind,id,thumb,genres}``) and ``discovery``
    (candidate: ``{key,label,owned,kind,popularity,image_url,genres,ids}`` where ``ids`` is a list of
    ``[source, external_id]`` pairs).
    """
    owned_meta = owned_meta or {}
    rows = list(rows)

    # Same self-referential resolution as build_taste_map: source ext id -> name.
    id2name: Dict[str, str] = {}
    for row in rows:
        _src, name, sp, dz, it, _occ, _pop = _row7(row)
        for eid in (sp, dz, it):
            if eid and eid not in id2name:
                id2name[eid] = name

    # Gather each OWNED anchor's UNOWNED similar targets, merged per target name.
    anchors: Dict[str, Dict[str, Any]] = {}
    for row in rows:
        src, name, sp, dz, it, occ, pop = _row7(row)
        src_name = id2name.get(src)
        if not src_name:
            continue
        src_norm = _norm(src_name)
        if not src_norm or src_norm not in owned_names:
            continue                              # anchor must be owned (and non-blank)
        tgt_norm = _norm(name)
        if not tgt_norm or tgt_norm in owned_names:
            continue                              # discovery = UNowned target only
        a = anchors.setdefault(src_norm, {"label": src_name, "targets": {}})
        img, gen = _row_enrich(row)
        _merge_target(a["targets"], name, sp, dz, it, occ, pop, img, gen)

    ranked = sorted(anchors.items(), key=lambda kv: len(kv[1]["targets"]), reverse=True)
    if seed_count is not None:
        ranked = ranked[:seed_count]

    nodes: Dict[str, dict] = {}
    edges: List[dict] = []
    edge_seen: set = set()

    for anchor_norm, info in ranked:
        if anchor_norm not in nodes:
            meta = owned_meta.get(anchor_norm, {})
            nodes[anchor_norm] = {
                "key": anchor_norm, "label": info["label"], "owned": True, "kind": "owned",
                "id": meta.get("id"), "thumb": meta.get("thumb_url"), "genres": meta.get("genres"),
            }
        top = sorted(info["targets"].items(), key=lambda kv: (kv[1][4], kv[1][5]), reverse=True)
        if per_anchor is not None:
            top = top[:per_anchor]
        for tkey, (tname, sp, dz, it, occ, pop, img, gen) in top:
            if tkey not in nodes:
                nodes[tkey] = {
                    "key": tkey, "label": tname, "owned": False, "kind": "discovery",
                    "popularity": pop, "image_url": img, "genres": gen,
                    "ids": _id_pairs(sp, dz, it),
                }
            ekey = (anchor_norm, tkey)
            if ekey not in edge_seen:
                edge_seen.add(ekey)
                edges.append({"source": anchor_norm, "target": tkey, "weight": occ})

    return {"nodes": list(nodes.values()), "edges": edges}


def expand_discovery_node(
    rows: Iterable[Row],
    owned_names: set,
    node_key: str,
    node_ids: Optional[Iterable[Any]] = None,
    owned_meta: Optional[Dict[str, Dict[str, Any]]] = None,
    per: int = 10,
    exclude: Optional[set] = None,
) -> Dict[str, List[dict]]:
    """One expand-on-click step for the Discovery map: the clicked node's similar artists.

    The clicked node is identified by ``node_key`` (normalized name) and/or ``node_ids`` (its external
    ids, flat) — a row matches when its source id resolves to that name or is one of those ids.
    Targets in ``exclude`` (keys already on screen) are skipped; duplicate multi-source rows merge to
    the target's strongest evidence (see :func:`_merge_target`); the strongest ``per`` are returned.
    Unowned targets become ``discovery`` nodes (image/genres from the rows themselves); owned targets
    become ``owned`` nodes, so a trail can also reveal how a candidate connects back into your
    library. Edges run ``node_key -> target``.
    """
    owned_meta = owned_meta or {}
    exclude = exclude or set()
    ids_set = {i for i in (node_ids or []) if i}
    node_key = _norm(node_key)
    rows = list(rows)

    id2name: Dict[str, str] = {}
    for row in rows:
        _src, name, sp, dz, it, _occ, _pop = _row7(row)
        for eid in (sp, dz, it):
            if eid and eid not in id2name:
                id2name[eid] = name

    targets: Dict[str, list] = {}
    for row in rows:
        src, name, sp, dz, it, occ, pop = _row7(row)
        if src not in ids_set and _norm(id2name.get(src, "")) != node_key:
            continue                              # row isn't about the clicked node
        tkey = _norm(name)
        if not tkey or tkey == node_key or tkey in exclude:
            continue
        img, gen = _row_enrich(row)
        _merge_target(targets, name, sp, dz, it, occ, pop, img, gen)

    ranked = sorted(targets.items(), key=lambda kv: (kv[1][4], kv[1][5]), reverse=True)

    nodes: List[dict] = []
    edges: List[dict] = []
    for tkey, (tname, sp, dz, it, occ, pop, img, gen) in ranked[:per]:
        if tkey in owned_names:
            meta = owned_meta.get(tkey, {})
            nodes.append({
                "key": tkey, "label": tname, "owned": True, "kind": "owned",
                "id": meta.get("id"), "thumb": meta.get("thumb_url"), "genres": meta.get("genres"),
            })
        else:
            nodes.append({
                "key": tkey, "label": tname, "owned": False, "kind": "discovery",
                "popularity": pop, "image_url": img, "genres": gen,
                "ids": _id_pairs(sp, dz, it),
            })
        edges.append({"source": node_key, "target": tkey, "weight": occ})

    return {"nodes": nodes, "edges": edges}
