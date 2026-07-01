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
        if tgt not in owned_names:
            continue
        src_name = id2name.get(src)
        if not src_name:
            continue
        src_norm = _norm(src_name)
        if src_norm not in owned_names or src_norm == tgt:
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
