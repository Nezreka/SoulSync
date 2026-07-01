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


def _primary_genre(genres: Any) -> Optional[str]:
    """First genre from an artist's ``genres`` value (a JSON array string, or already a list)."""
    if not genres:
        return None
    arr = genres
    if isinstance(genres, str):
        try:
            arr = json.loads(genres)
        except (ValueError, TypeError):
            return None
    if isinstance(arr, list) and arr:
        first = str(arr[0]).strip()
        return first or None
    return None


def build_genre_grouped_map(
    artists: Iterable[Tuple[Any, Any, Any]],
    rows: Iterable[Row],
    owned_names: set,
    artist_meta: Optional[Dict[str, Dict[str, Any]]] = None,
) -> Dict[str, List[dict]]:
    """Genre-anchored Taste Map: EVERY library artist as a node, grouped by genre + wired by similarity.

    ``artists`` is ``(name, genres, thumb_url[, id, source])`` for every library artist (``genres`` a
    JSON-array string; ``id``/``source`` optional, used by the frontend to link to the artist page).
    This includes the ~3.8k artists that have no owned<->owned similarity edge — they'd be dropped by
    :func:`build_taste_map` — by attaching each artist to a per-genre "hub" node so a force layout
    clusters them by genre. Similarity edges (owned<->owned) are reused verbatim.

    Node kinds:
      * ``artist`` — ``{key, label, kind, owned, primary_genre, popularity, thumb, id, source}``
      * ``genre``  — ``{key, label, kind, genre}`` (one hub per distinct primary genre)
    Edge kinds: ``similarity`` (from :func:`build_taste_map`) and ``membership`` (artist -> its genre).
    """
    base = build_taste_map(rows, owned_names, artist_meta)
    pop_by = {n["key"]: n.get("popularity", 0) for n in base["nodes"]}

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
        prim = _primary_genre(genres)
        nodes.append({
            "key": key,
            "label": name,
            "kind": "artist",
            "owned": True,
            "primary_genre": prim,
            "popularity": pop_by.get(key, 0),
            "thumb": thumb,
            "id": artist_id,
            "source": source,
        })
        if prim:
            gkey = "genre::" + prim.strip().lower()
            genre_labels.setdefault(gkey, prim)
            edges.append({"source": key, "target": gkey, "weight": 1, "kind": "membership"})

    for gkey, glabel in genre_labels.items():
        nodes.append({"key": gkey, "label": glabel, "kind": "genre", "genre": glabel})

    return {"nodes": nodes, "edges": edges}
