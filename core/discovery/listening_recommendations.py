"""Listening-driven recommendation core (#913).

PURE, side-effect-free ranking that turns "the artists you listen to most" plus
"who's similar to each" into:

  1. a consensus-ranked list of artists you'd probably love but don't own, and
  2. an aggregated candidate-track list for a generated playlist.

No DB / network / config here. The caller (the watchlist scanner) supplies the
seeds (top-played artists), the ``similar_artists`` rows per seed, and the
owned-artist set, then fetches top tracks for the winners. Keeping the decision
logic in one pure place makes it fully unit-testable without the live stack and
keeps the scan wiring thin — and additive, so it can't disturb existing flows.

Scoring rationale (the "best in class" bit): a recommended artist's score is
``Σ over the seeds that recommend it of (seed_weight × similarity)``. That single
sum rewards all three signals at once — **consensus** (an artist endorsed by many
of your seeds accumulates more terms), your **play weight** (heavier seeds push
harder), and **similarity strength** — instead of a flat "appears in N lists".
``seed_count`` is exposed separately for display ("because you like A, B, C") and
as the adventurousness dial's lever (``min_seed_count``).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Set


def _norm(name: object) -> str:
    return str(name or "").strip().lower()


def _positive_float(value: object, default: float = 1.0) -> float:
    try:
        f = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default
    return f if f > 0 else default


def _get(row: object, attr: str):
    """Read a field from a dataclass row or a dict row."""
    if isinstance(row, dict):
        return row.get(attr)
    return getattr(row, attr, None)


def group_similars_by_seed(
    seeds: Sequence[dict],
    similar_rows: Sequence,
    id_to_name: Dict[str, str],
    *,
    source_id_attr: str = "source_artist_id",
    similar_name_attr: str = "similar_artist_name",
) -> Dict[str, List[dict]]:
    """Reshape flat ``similar_artists`` rows into ``{seed_name_lower: [{'name': similar}]}``.

    The stored rows key the similar artist by the SEED's source id (``source_artist_id``),
    not its name, so :func:`rank_recommended_artists` can't consume them directly. This
    resolves each row's source id to a name via ``id_to_name`` (``{source_artist_id:
    artist_name}`` for the library, built by the caller) and keeps only rows that resolve
    to one of the ``seeds``. Rows may be dataclass objects or dicts. Pure — no I/O.
    """
    seed_names = {_norm(s.get("name")) for s in seeds}
    seed_names.discard("")
    id_to_norm = {str(k): _norm(v) for k, v in (id_to_name or {}).items()}

    out: Dict[str, List[dict]] = {}
    for row in similar_rows or ():
        seed_name = id_to_norm.get(str(_get(row, source_id_attr) or ""), "")
        if not seed_name or seed_name not in seed_names:
            continue
        sim_name = str(_get(row, similar_name_attr) or "").strip()
        if sim_name:
            out.setdefault(seed_name, []).append({"name": sim_name})
    return out


@dataclass
class RecommendedArtist:
    """One artist recommended from your listening, with the why."""
    name: str                                       # display name (first-seen casing)
    score: float                                    # Σ seed_weight × similarity
    seed_count: int                                 # distinct seeds endorsing it (consensus)
    seeds: List[str] = field(default_factory=list)  # display names of those seeds


def rank_recommended_artists(
    seeds: Sequence[dict],
    similars_by_seed: Dict[str, Sequence[dict]],
    owned_artist_names: Optional[Set[str]] = None,
    *,
    limit: int = 30,
    min_seed_count: int = 1,
) -> List[RecommendedArtist]:
    """Rank artists similar to your most-played by consensus + play weight + similarity.

    Args:
        seeds: ``[{'name': str, 'weight': float}]`` — your top-played artists.
            ``weight`` (play count or any positive number) defaults to 1.0.
        similars_by_seed: ``{seed_name_lower: [{'name': str, 'score': float}]}`` — the
            similar-artist rows for each seed. ``score`` is optional (defaults 1.0).
        owned_artist_names: lowercased names already in the library — excluded so the
            result is artists you DON'T have. The seeds themselves are always excluded.
        limit: max results.
        min_seed_count: drop recommendations endorsed by fewer than N seeds — the
            adventurousness dial's "Safer" end raises this for higher-confidence picks.

    Returns up to ``limit`` :class:`RecommendedArtist`, highest score first.
    """
    owned = {_norm(a) for a in (owned_artist_names or set())}
    seed_norms = {_norm(s.get("name")) for s in seeds}
    seed_norms.discard("")
    exclude = owned | seed_norms

    acc: Dict[str, dict] = {}
    for seed in seeds:
        s_name = _norm(seed.get("name"))
        if not s_name:
            continue
        s_display = str(seed.get("name") or "").strip()
        weight = _positive_float(seed.get("weight", 1.0))
        for sim in similars_by_seed.get(s_name, ()) or ():
            a_norm = _norm(sim.get("name"))
            if not a_norm or a_norm in exclude:
                continue
            sim_score = _positive_float(sim.get("score", 1.0))
            row = acc.setdefault(
                a_norm, {"name": str(sim.get("name") or "").strip(), "score": 0.0, "seeds": {}}
            )
            row["score"] += weight * sim_score
            row["seeds"].setdefault(s_name, s_display)   # one seed counts once

    out: List[RecommendedArtist] = []
    floor = max(1, int(min_seed_count))
    for row in acc.values():
        seed_count = len(row["seeds"])
        if seed_count < floor:
            continue
        out.append(RecommendedArtist(
            name=row["name"],
            score=round(row["score"], 6),
            seed_count=seed_count,
            seeds=list(row["seeds"].values()),
        ))
    out.sort(key=lambda r: (-r.score, -r.seed_count, r.name.lower()))
    return out[:limit]


def aggregate_candidate_tracks(
    recommended_artists: Sequence[RecommendedArtist],
    top_tracks_by_artist: Dict[str, Sequence[dict]],
    owned_track_keys: Optional[Set] = None,
    *,
    per_artist: int = 3,
    limit: int = 50,
    exclude_owned: bool = True,
) -> List[dict]:
    """Build the candidate track list for the generated playlist.

    Takes the top ``per_artist`` tracks from each recommended artist **in artist-rank
    order**, dedups by ``(artist, title)``, optionally drops owned tracks (the
    "discovery" flavor) and caps at ``limit``. Each returned track dict is the source
    track plus ``_seed_artist`` (which recommended artist it came from).

    Args:
        recommended_artists: ranked output of :func:`rank_recommended_artists`.
        top_tracks_by_artist: ``{artist_name_lower: [track_dict, ...]}`` — fetched by
            the caller (Last.fm / source top tracks), NOT limited to a curated pool.
        owned_track_keys: set of ``(artist_lower, title_lower)`` already in the library.
        exclude_owned: drop tracks in ``owned_track_keys`` (discovery flavor). Set False
            for a "replay" playlist of tracks you already own.
    """
    owned = owned_track_keys or set()
    seen: Set = set()
    out: List[dict] = []
    for art in recommended_artists:
        tracks = top_tracks_by_artist.get(_norm(art.name), ()) or ()
        taken = 0
        for t in tracks:
            if taken >= per_artist:
                break
            title = str(t.get("name") or t.get("title") or "").strip()
            if not title:
                continue
            key = (_norm(art.name), _norm(title))
            if key in seen:
                continue
            if exclude_owned and key in owned:
                continue
            seen.add(key)
            out.append({**t, "_seed_artist": art.name})
            taken += 1
        if len(out) >= limit:
            break
    return out[:limit]


__all__ = [
    "RecommendedArtist",
    "group_similars_by_seed",
    "rank_recommended_artists",
    "aggregate_candidate_tracks",
]
