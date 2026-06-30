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


def _coerce_float(value: object, default: float = 0.0) -> float:
    """Plain float coercion that keeps 0 / negatives (unlike _positive_float) — popularity can be 0."""
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default


def _get(row: object, attr: str):
    """Read a field from a dataclass row or a dict row."""
    if isinstance(row, dict):
        return row.get(attr)
    return getattr(row, attr, None)


def choose_mix_fetch_source(active_source: object, active_can_fetch: bool) -> str:
    """Pick which source to fetch the "Listening Mix" top tracks from.

    The mix is a list of (artist, title) pairs acquired via Soulseek, so the fetch source need
    NOT match the user's active metadata source. Use the active source when it can fetch top
    tracks itself (Spotify/Deezer); otherwise fall back to Deezer, whose public ``artist/{id}/top``
    needs no auth and is available to every user — so iTunes / Discogs / MusicBrainz users still
    get a full mix without switching sources. Pure.
    """
    if str(active_source or "").lower() in ("spotify", "deezer") and active_can_fetch:
        return str(active_source).lower()
    return "deezer"


def names_match(a: object, b: object) -> bool:
    """Strict artist-name equality after stripping case + non-alphanumerics.

    Used to verify a name-search result before fetching that artist's top tracks, so the
    "Listening Mix" can never pull the WRONG artist's songs (e.g. a same-name act). Exact
    alphanumeric match: "Tyler, The Creator" == "Tyler The Creator", but "Drake" != "Drake Bell".
    Pure.
    """
    def _alnum(x: object) -> str:
        return "".join(ch for ch in str(x or "").lower() if ch.isalnum())
    na, nb = _alnum(a), _alnum(b)
    return bool(na) and na == nb


def similarity_from_rank(rank: object, max_rank: int = 10) -> float:
    """Turn a stored ``similarity_rank`` (1 = most similar … 10 = least) into a 0–1 weight.

    SoulSync stores each ``(seed → similar)`` edge with a 1–10 rank (``1`` is the closest
    match). The ranker multiplies this into the score so a seed's *closest* matches count
    for more than its long-tail ones. Linear decay over the documented range: rank 1 → 1.0,
    rank 5 → 0.6, rank 10 → 0.1, with a 0.1 floor so a far match still contributes. A
    missing/garbage rank falls back to 1.0 (treat as "no rank info, full weight"). Pure.
    """
    try:
        r = int(rank)
    except (TypeError, ValueError):
        return 1.0
    floor = round(1.0 / max_rank, 4)
    if r <= 1:
        return 1.0
    if r >= max_rank:
        return floor
    return round((max_rank - r + 1) / max_rank, 4)


def build_recency_weighted_seeds(
    top_artists: Sequence[dict],
    recent_play_counts: Optional[Dict[str, float]] = None,
    *,
    recency_factor: float = 1.5,
) -> List[dict]:
    """Blend lifetime + recent play counts into seed weights — "what you're into NOW".

    ``weight = lifetime_plays + recency_factor × recent_plays``. An artist you've played a
    lot *recently* outranks one you played a lot years ago, so the recommendations track
    your current taste instead of your all-time history. ``recency_factor`` is the dial
    (0 = pure lifetime). Returns ``[{'name', 'weight'}]`` for :func:`rank_recommended_artists`.
    Pure — the caller supplies both play-count maps from the listening history.
    """
    recent = {_norm(k): _positive_float(v, 0.0) for k, v in (recent_play_counts or {}).items()}
    out: List[dict] = []
    for a in top_artists or ():
        name = str(a.get("name") or "").strip()
        if not name:
            continue
        lifetime = _positive_float(a.get("play_count", a.get("weight", 1.0)))
        boost = recency_factor * recent.get(_norm(name), 0.0)
        out.append({"name": name, "weight": lifetime + boost})
    return out


def group_similars_by_seed(
    seeds: Sequence[dict],
    similar_rows: Sequence,
    id_to_name: Dict[str, str],
    *,
    source_id_attr: str = "source_artist_id",
    similar_name_attr: str = "similar_artist_name",
    rank_attr: Optional[str] = None,
) -> Dict[str, List[dict]]:
    """Reshape flat ``similar_artists`` rows into ``{seed_name_lower: [{'name', 'score'?}]}``.

    The stored rows key the similar artist by the SEED's source id (``source_artist_id``),
    not its name, so :func:`rank_recommended_artists` can't consume them directly. This
    resolves each row's source id to a name via ``id_to_name`` (``{source_artist_id:
    artist_name}`` for the library, built by the caller) and keeps only rows that resolve
    to one of the ``seeds``. Rows may be dataclass objects or dicts. Pure — no I/O.

    ``id_to_name`` MUST be keyed by whatever id the edges actually store — for SoulSync that
    is the artist's SOURCE id (Spotify/iTunes/Deezer/MusicBrainz), NOT the internal row id.
    When ``rank_attr`` is given, each row's rank is converted via :func:`similarity_from_rank`
    and carried as ``score`` so closer matches weigh more; without it every similar comes out
    score-less (the ranker then treats similarity as 1.0 — original behavior).
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
        if not sim_name:
            continue
        entry = {"name": sim_name}
        if rank_attr is not None:
            entry["score"] = similarity_from_rank(_get(row, rank_attr))
        out.setdefault(seed_name, []).append(entry)
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


# ── Adventurousness re-rank (aurral-style popularity penalty) ────────────────────────────────
# Both Discover rec rows already EXCLUDE what you own / watch, so "novelty" is baked in; the lever we
# were missing is a POPULARITY PENALTY. At higher adventurousness, globally-popular candidates are
# pushed down so the obscure / non-obvious picks surface. Pure + reusable across both rec rows.
_MAX_POP_PENALTY = 0.7  # at level 1.0 a popularity-100 candidate loses 70% of its score


def apply_adventurousness(
    items: Sequence[dict],
    level: object,
    *,
    score_key: str = "score",
    pop_key: str = "popularity",
    tiebreak_key: str = "seed_count",
) -> List[dict]:
    """Re-rank ``items`` (dicts with a numeric score + an optional 0–100 popularity) by an
    adventurousness-scaled popularity penalty. Returns a NEW list, most-adventurous first.

    ``level`` is clamped to 0..1. At ``level <= 0`` the input order is returned **unchanged** (a
    copy), so the feature is fully additive / no-regression. Items missing a popularity are never
    penalised. Adjusted score = ``score × (1 − level × MAX_POP_PENALTY × popularity/100)``.
    """
    lvl = max(0.0, min(1.0, _coerce_float(level, 0.0)))
    if lvl <= 0.0:
        return list(items)

    def _adjusted(it: object) -> float:
        score = _coerce_float(_get(it, score_key), 0.0)
        pop = _get(it, pop_key)
        if pop is None:
            return score
        pop_norm = max(0.0, min(1.0, _coerce_float(pop, 0.0) / 100.0))
        return score * (1.0 - lvl * _MAX_POP_PENALTY * pop_norm)

    return sorted(
        items,
        key=lambda it: (-_adjusted(it), -_coerce_float(_get(it, tiebreak_key), 0.0), _norm(_get(it, "name"))),
    )


# ── Genre / tag affinity (aurral's missing signal) ──────────────────────────────────────────
# Rank candidates whose genres match the genres you actually PLAY higher. Always-on, data we already
# store (no popularity-style backfill needed). Built additively: affinity is 0 when there's no genre
# data on either side, so it can only ever BOOST a taste-match — never penalise a genreless candidate.

def build_genre_taste_profile(weighted_artists: Sequence) -> Dict[str, float]:
    """Turn the user's played/owned artists into a normalised genre-taste profile.

    ``weighted_artists``: iterable of ``(genres, weight)`` — ``genres`` is one artist's list of genre
    strings, ``weight`` that artist's importance (e.g. play count). Returns ``{genre_lower: 0..1}``
    with the heaviest genre at 1.0. Pure; ``{}`` when there's nothing to learn from.
    """
    totals: Dict[str, float] = {}
    for entry in weighted_artists or ():
        try:
            genres, weight = entry
        except (TypeError, ValueError):
            continue
        w = _coerce_float(weight, 1.0)   # missing weight -> 1.0, but a zero/negative one doesn't shape taste
        if w <= 0:
            continue
        for g in genres or ():
            key = _norm(g)
            if key:
                totals[key] = totals.get(key, 0.0) + w
    if not totals:
        return {}
    mx = max(totals.values())
    if mx <= 0:
        return {}
    return {g: v / mx for g, v in totals.items()}


def genre_affinity(candidate_genres: Sequence, taste_profile: Dict[str, float]) -> float:
    """0..1 — how well a candidate's genres match the user's taste profile (from
    :func:`build_genre_taste_profile`): the candidate's single strongest taste-matching genre. Pure;
    returns 0.0 when either side is empty, so a genreless candidate is never penalised.
    """
    if not candidate_genres or not taste_profile:
        return 0.0
    best = 0.0
    for g in candidate_genres:
        best = max(best, _coerce_float(taste_profile.get(_norm(g)), 0.0))
    return best


# ── Novelty (aurral's "unheard" signal) ─────────────────────────────────────────────────────
# Both rows already exclude what you OWN, so the extra lever is demoting recs you've already HEARD
# (played but never added). 0 plays -> fully novel (1.0); the more you've played a candidate, the less
# novel it is. The caller applies it as a soft penalty so an unheard candidate is the baseline.

def novelty_score(play_count: object, *, half_at: float = 8.0) -> float:
    """0..1 — how unheard a candidate is to you. ``0`` plays -> ``1.0`` (fully novel); ``half_at``
    plays -> ``0.5``; heavy rotation -> ~0. Negative / non-numeric play counts are treated as 0
    (fully novel), so a candidate with no play data is never penalised. Pure."""
    p = max(0.0, _coerce_float(play_count, 0.0))
    h = max(1e-9, float(half_at))
    return h / (h + p)


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


def to_mix_track(track: object, source: str) -> Optional[dict]:
    """Shape one source "top tracks" API dict into the flat dict the Discover compact
    playlist row renders + syncs (the "Listening Mix" #913 playlist).

    Spotify's ``artist_top_tracks`` and Deezer's ``get_artist_top_tracks`` both return the
    same Spotify-shape object (``id, name, artists[], album{name,images[]}, duration_ms``).
    This flattens that into the renderer's field names (``track_name/artist_name/album_name/
    album_cover_url/duration_ms``), keeps the original under ``track_data_json`` for sync, and
    sets the source-specific id field. Returns None for anything without a usable id/title so
    the caller can filter. A ``name`` key is kept so :func:`aggregate_candidate_tracks` can
    dedup by title. Pure — no I/O.
    """
    if not isinstance(track, dict):
        return None
    tid = track.get("id")
    name = str(track.get("name") or "").strip()
    if not tid or not name:
        return None
    artists = track.get("artists") or []
    artist_name = ""
    if artists and isinstance(artists[0], dict):
        artist_name = str(artists[0].get("name") or "").strip()
    album = track.get("album") if isinstance(track.get("album"), dict) else {}
    album_name = str(album.get("name") or "").strip()
    images = album.get("images") or []
    cover = images[0].get("url") if images and isinstance(images[0], dict) else None
    out = {
        "track_id": str(tid),
        "name": name,                  # for aggregate_candidate_tracks dedup
        "track_name": name,            # for the renderer
        "artist_name": artist_name,
        "album_name": album_name,
        "album_cover_url": cover,
        "duration_ms": track.get("duration_ms") or 0,
        "track_data_json": track,      # full payload for sync/download
        "source": source,
    }
    id_field = {"spotify": "spotify_track_id", "deezer": "deezer_track_id",
                "itunes": "itunes_track_id"}.get(source)
    if id_field:
        out[id_field] = str(tid)
    return out


__all__ = [
    "RecommendedArtist",
    "choose_mix_fetch_source",
    "names_match",
    "similarity_from_rank",
    "build_recency_weighted_seeds",
    "to_mix_track",
    "group_similars_by_seed",
    "rank_recommended_artists",
    "aggregate_candidate_tracks",
]
