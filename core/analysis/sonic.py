"""Sonic similarity & Sonic Adventure engine over ``sonic_embeddings``.

Computes cosine similarity between L2-normalized 32-dim audio embeddings with
gentle harmonic (Camelot wheel) and tempo (BPM) compatibility bonuses for
Plexamp-style "Sonically Similar" Track Radio and smooth Sonic Adventures.
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional, Tuple

from core.analysis.music_analysis import ensure_tables


def _parse_camelot(code: Optional[str]) -> Optional[Tuple[int, str]]:
    if not code or len(code) < 2:
        return None
    mode = code[-1].upper()
    if mode not in ("A", "B"):
        return None
    try:
        num = int(code[:-1])
        if 1 <= num <= 12:
            return num, mode
    except ValueError:
        pass
    return None


def camelot_compatible(a: Optional[str], b: Optional[str]) -> float:
    """Return 1.0 for exact/relative/adjacent Camelot keys, 0.5 for 2 steps, else 0.0."""
    pa, pb = _parse_camelot(a), _parse_camelot(b)
    if not pa or not pb:
        return 0.0
    na, ma = pa
    nb, mb = pb
    if na == nb:
        return 1.0  # exact match or relative major/minor
    dist = min((na - nb) % 12, (nb - na) % 12)
    if ma == mb and dist == 1:
        return 1.0
    if ma == mb and dist == 2:
        return 0.5
    return 0.0


def _cosine(va: List[float], vb: List[float]) -> float:
    if len(va) != len(vb) or not va:
        return 0.0
    return sum(x * y for x, y in zip(va, vb))


def _load_all_embeddings(db: Any) -> Dict[int, Dict[str, Any]]:
    ensure_tables(db)
    out: Dict[int, Dict[str, Any]] = {}
    with db._get_connection() as conn:
        rows = conn.execute("SELECT track_id, vector_json, bpm, camelot FROM sonic_embeddings").fetchall()
    for r in rows:
        try:
            vec = json.loads(r["vector_json"])
            if isinstance(vec, list) and vec:
                out[int(r["track_id"])] = {
                    "track_id": int(r["track_id"]),
                    "vec": [float(x) for x in vec],
                    "bpm": float(r["bpm"]) if r["bpm"] is not None else None,
                    "camelot": r["camelot"],
                }
        except (TypeError, ValueError):
            continue
    return out


def _pair_score(a: Dict[str, Any], b: Dict[str, Any]) -> float:
    sim = _cosine(a["vec"], b["vec"])
    harm = camelot_compatible(a.get("camelot"), b.get("camelot"))
    bpm_bonus = 0.0
    if a.get("bpm") and b.get("bpm"):
        diff = abs(a["bpm"] - b["bpm"])
        if diff <= 12.0:
            bpm_bonus = 1.0 - (diff / 12.0)
    return 0.80 * sim + 0.12 * harm + 0.08 * bpm_bonus


def similar_tracks(db: Any, track_id: int, *, limit: int = 20) -> List[Dict[str, Any]]:
    """Return the most sonically similar tracks to ``track_id``."""
    all_emb = _load_all_embeddings(db)
    seed = all_emb.get(int(track_id))
    if not seed:
        return []
    scored: List[Dict[str, Any]] = []
    for tid, item in all_emb.items():
        if tid == int(track_id):
            continue
        score = _pair_score(seed, item)
        scored.append({
            "track_id": tid,
            "score": round(score, 4),
            "bpm": item.get("bpm"),
            "camelot": item.get("camelot"),
        })
    scored.sort(key=lambda x: x["score"], reverse=True)
    return scored[: max(1, min(100, int(limit or 20)))]


def sonic_adventure(
    db: Any,
    start_track_id: int,
    end_track_id: int,
    *,
    steps: int = 8,
) -> List[Dict[str, Any]]:
    """Build a smooth sonic path from ``start_track_id`` to ``end_track_id`` by
    stepping along linearly interpolated target embeddings."""
    all_emb = _load_all_embeddings(db)
    start = all_emb.get(int(start_track_id))
    end = all_emb.get(int(end_track_id))
    if not start or not end:
        return []
    total_steps = max(2, min(30, int(steps or 8)))
    used = {int(start_track_id), int(end_track_id)}
    path: List[Dict[str, Any]] = [
        {"track_id": int(start_track_id), "step": 0, "bpm": start.get("bpm"), "camelot": start.get("camelot")}
    ]

    for s in range(1, total_steps - 1):
        t = s / float(total_steps - 1)
        target_vec = [(1.0 - t) * x + t * y for x, y in zip(start["vec"], end["vec"])]
        best_tid = None
        best_sim = -1e9
        for tid, item in all_emb.items():
            if tid in used:
                continue
            sim = _cosine(target_vec, item["vec"])
            if sim > best_sim:
                best_sim = sim
                best_tid = tid
        if best_tid is not None:
            used.add(best_tid)
            chosen = all_emb[best_tid]
            path.append({
                "track_id": best_tid,
                "step": s,
                "score": round(best_sim, 4),
                "bpm": chosen.get("bpm"),
                "camelot": chosen.get("camelot"),
            })

    path.append({
        "track_id": int(end_track_id),
        "step": len(path),
        "bpm": end.get("bpm"),
        "camelot": end.get("camelot"),
    })
    return path
