"""Daily Mixes - taste-clustered blends of owned + discovery tracks.

the spotify feature, done with the one advantage spotify doesn't have
here: the library is local. each mix is ~80% tracks the user owns
(instantly playable) and ~20% discovery tracks from the cluster's
similar artists (one click from download).

clustering: recency-weighted top artists from listening_history are
greedily grouped by similarity edges (similar_artists, resolved through
SOURCE ids - never artists.id, see the id-smear bug) and shared genres.
each cluster becomes one mix, regenerated daily (deterministic per day,
different tomorrow).

storage: the whole payload - full track dicts, not pool ids - goes into
discovery_curated_playlists under 'daily_mixes_v2'. pool rotation can
never shrink these (the fresh-tape hydration lesson).
"""

import json
import random
from datetime import date, datetime, timezone
from typing import Any, Dict, List, Optional, Sequence

from utils.logging_config import get_logger

logger = get_logger("personalized.daily_mixes")

CURATED_KEY = "daily_mixes_v2"
PAYLOAD_VERSION = 2
MAX_MIXES = 6
MIX_SIZE = 40
DISCOVERY_PER_MIX = 8
MAX_SEEDS = 60
MAX_ARTISTS_PER_MIX = 12
MIN_ARTISTS_PER_MIX = 2
TTL_HOURS = 20


def _norm(text: Any) -> str:
    return str(text or "").strip().lower()


# ── clustering (pure) ─────────────────────────────────────────────────────


def cluster_seeds(
    seeds: Sequence[dict],
    similars_by_seed: Dict[str, List[dict]],
    genres_by_name: Dict[str, List[str]],
    *,
    max_clusters: int = MAX_MIXES,
    max_per: int = MAX_ARTISTS_PER_MIX,
    min_per: int = MIN_ARTISTS_PER_MIX,
) -> List[Dict[str, Any]]:
    """Group seed artists into taste clusters.

    Two seeds connect when one appears in the other's similar list, or when
    they share a genre. Greedy: the heaviest unassigned seed founds a
    cluster, then repeatedly pulls in the heaviest unassigned seed connected
    to any member. Deterministic - no randomness here, the daily variation
    lives in track picking.
    """
    ordered = [s for s in sorted(seeds, key=lambda s: -float(s.get("weight", 0)))
               if _norm(s.get("name"))]
    names = [_norm(s["name"]) for s in ordered]
    display = {_norm(s["name"]): str(s["name"]) for s in ordered}
    weight = {_norm(s["name"]): float(s.get("weight", 0)) for s in ordered}

    similar_sets = {
        seed: {_norm(x.get("name")) for x in (sims or [])}
        for seed, sims in (similars_by_seed or {}).items()
    }
    genre_sets = {n: {_norm(g) for g in (genres_by_name.get(n) or [])} for n in names}

    def connected(a: str, b: str) -> bool:
        if b in similar_sets.get(a, ()) or a in similar_sets.get(b, ()):
            return True
        return bool(genre_sets.get(a) and genre_sets.get(a) & genre_sets.get(b, set()))

    unassigned = list(names)
    clusters: List[List[str]] = []
    while unassigned and len(clusters) < max_clusters:
        nucleus = unassigned.pop(0)
        members = [nucleus]
        grew = True
        while grew and len(members) < max_per:
            grew = False
            for cand in list(unassigned):
                if any(connected(cand, m) for m in members):
                    members.append(cand)
                    unassigned.remove(cand)
                    grew = True
                    if len(members) >= max_per:
                        break
        clusters.append(members)

    kept = [c for c in clusters if len(c) >= min_per]
    # singleton clusters merge into one Mixed Bag rather than each burning a slot
    leftovers = [m for c in clusters if len(c) < min_per for m in c]
    if len(leftovers) >= min_per and len(kept) < max_clusters:
        kept.append(leftovers[:max_per])

    out = []
    for members in kept[:max_clusters]:
        genre_votes: Dict[str, float] = {}
        for m in members:
            for g in genre_sets.get(m, ()):
                genre_votes[g] = genre_votes.get(g, 0) + weight.get(m, 1)
        top_genre = max(genre_votes, key=genre_votes.get) if genre_votes else ""
        out.append({
            "artists": [display[m] for m in members],
            "genre": top_genre.title() if top_genre else "",
        })
    return out


def interleave_owned(
    tracks_by_artist: Dict[str, List[dict]],
    artist_order: Sequence[str],
    size: int,
    rng: random.Random,
) -> List[dict]:
    """Round-robin the cluster's artists so no artist dominates and no two
    same-artist tracks sit adjacent (spotify's spacing rule). Within an
    artist the most-played come first with a daily shuffle jitter."""
    queues = {}
    for artist in artist_order:
        rows = list(tracks_by_artist.get(artist) or [])
        # multiplicative daily noise on the play count, NOT shuffle-then-sort:
        # a stable sort undoes a shuffle completely whenever counts are
        # distinct, which killed the day-to-day variation outright. noise in
        # [0.6, 1.4] reorders neighbours while favourites stay near the top.
        rows.sort(key=lambda r: -(float(r.get("play_count") or 0) + 1.0)
                  * rng.uniform(0.6, 1.4))
        queues[artist] = rows
    picked: List[dict] = []
    while len(picked) < size:
        progressed = False
        for artist in artist_order:
            q = queues.get(artist)
            if q:
                picked.append(q.pop(0))
                progressed = True
                if len(picked) >= size:
                    break
        if not progressed:
            break
    return picked


# ── db-facing generation ──────────────────────────────────────────────────


def _seed_edges(database, seed_names: List[str], profile_id: int):
    """similars_by_seed + owned-name set, the smear-proof way (source ids)."""
    from core.discovery.listening_recommendations import group_similars_by_seed

    owned, seed_source_ids, seed_id_to_name = set(), [], {}
    with database._get_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT name, spotify_id, "
            "json_extract(external_ids, '$.itunes'), "
            "json_extract(external_ids, '$.deezer'), "
            "musicbrainz_id FROM lib2_artists "
            "WHERE name IS NOT NULL AND name != ''")
        wanted = set(seed_names)
        for row in cur.fetchall():
            nm = row[0]
            lname = _norm(nm)
            owned.add(lname)
            if lname in wanted:
                for sid in (row[1], row[2], row[3], row[4]):
                    if sid:
                        seed_source_ids.append(str(sid))
                        seed_id_to_name[str(sid)] = nm
        edges = []
        if seed_source_ids:
            placeholders = ",".join("?" * len(seed_source_ids))
            cur.execute(
                f"SELECT source_artist_id, similar_artist_name, similarity_rank "
                f"FROM similar_artists WHERE profile_id = ? "
                f"AND source_artist_id IN ({placeholders})",
                [profile_id, *seed_source_ids])
            edges = [dict(zip(("source_artist_id", "similar_artist_name",
                               "similarity_rank"), r, strict=False))
                     for r in cur.fetchall()]
    seeds_shaped = [{"name": n} for n in seed_names]
    similars = group_similars_by_seed(seeds_shaped, edges, seed_id_to_name,
                                      rank_attr="similarity_rank")
    return similars, owned


def _owned_tracks_for(database, artist_names: Sequence[str]) -> Dict[str, List[dict]]:
    """Playable library tracks per artist (lowercased key), mix-track shaped."""
    out: Dict[str, List[dict]] = {}
    if not artist_names:
        return out
    with database._get_connection() as conn:
        cur = conn.cursor()
        placeholders = ",".join("?" * len(artist_names))
        cur.execute(
            f"""
            SELECT t.title, t.duration, t.play_count,
                   COALESCE(NULLIF(t.track_artist, ''), ar.name) AS artist,
                   al.title AS album,
                   COALESCE(al.image_url, ar.image_url) AS cover
            FROM lib2_tracks t
            JOIN lib2_albums al ON al.id = t.album_id
            JOIN lib2_artists ar ON ar.id = al.primary_artist_id
            WHERE EXISTS (SELECT 1 FROM lib2_track_files f
                          WHERE f.track_id = t.id
                            AND f.path IS NOT NULL AND TRIM(f.path) != ''
                            AND COALESCE(f.file_state, 'active') = 'active')
              AND LOWER(COALESCE(NULLIF(t.track_artist, ''), ar.name)) IN ({placeholders})
            """,
            [_norm(a) for a in artist_names])
        from core.metadata import normalize_image_url
        for row in cur.fetchall():
            r = dict(row)
            # library thumbs are media-server-relative (/library/metadata/...)
            # and render as blank art without the browser-safe conversion
            cover = normalize_image_url(r["cover"]) if r["cover"] else None
            track = {
                "name": r["title"],
                "artists": [{"name": r["artist"]}],
                "album": {"name": r["album"] or "",
                          "images": [{"url": cover}] if cover else []},
                # lib2 keeps duration in MILLIseconds (legacy stored seconds).
                "duration_ms": int(r["duration"] or 0),
                "play_count": r.get("play_count") or 0,
                "owned": True,
            }
            out.setdefault(_norm(r["artist"]), []).append(track)
    return out


def _discovery_tracks_for(database, artist_names: Sequence[str], limit: int,
                          profile_id: int) -> List[dict]:
    """Unowned flavor from the discovery pool for the cluster's similar artists."""
    if not artist_names:
        return []
    out: List[dict] = []
    with database._get_connection() as conn:
        cur = conn.cursor()
        placeholders = ",".join("?" * len(artist_names))
        cur.execute(
            f"""
            SELECT track_name, artist_name, album_name, album_cover_url,
                   duration_ms, track_data_json
            FROM discovery_pool
            WHERE profile_id = ? AND LOWER(artist_name) IN ({placeholders})
            ORDER BY popularity DESC
            LIMIT ?
            """,
            [profile_id, *[_norm(a) for a in artist_names], limit * 3])
        seen = set()
        for row in cur.fetchall():
            r = dict(row)
            key = (_norm(r["artist_name"]), _norm(r["track_name"]))
            if key in seen:
                continue
            seen.add(key)
            track = None
            if r.get("track_data_json"):
                try:
                    track = json.loads(r["track_data_json"])
                except Exception:  # noqa: BLE001 - blob optional
                    track = None
            if not isinstance(track, dict):
                track = {
                    "name": r["track_name"],
                    "artists": [{"name": r["artist_name"]}],
                    "album": {"name": r["album_name"] or "",
                              "images": ([{"url": r["album_cover_url"]}]
                                         if r["album_cover_url"] else [])},
                    "duration_ms": r["duration_ms"] or 0,
                }
            track["owned"] = False
            out.append(track)
            if len(out) >= limit:
                break
    return out


def generate_daily_mixes(database, profile_id: int = 1, *,
                         max_mixes: int = MAX_MIXES,
                         mix_size: int = MIX_SIZE,
                         discovery_per_mix: int = DISCOVERY_PER_MIX,
                         today: Optional[date] = None) -> Dict[str, Any]:
    from core.discovery.listening_recommendations import build_recency_weighted_seeds

    today = today or date.today()
    top = database.get_top_artists('all', 200) or []
    recent = database.get_top_artists('30d', 200) or []
    seeds = build_recency_weighted_seeds(
        top, {a['name']: a.get('play_count', 0) for a in recent})
    seeds = sorted(seeds, key=lambda s: -s['weight'])[:MAX_SEEDS]
    seed_names = [_norm(s['name']) for s in seeds]

    similars, owned = _seed_edges(database, seed_names, profile_id)
    # mixes are built from OWNED artists - a heard-but-unowned artist has no
    # tracks to play and belongs to the discovery side instead
    owned_seeds = [s for s in seeds if _norm(s['name']) in owned]
    genres_by_name = {}
    try:
        genres_by_name = database.get_artist_genres_by_name(
            [s['name'] for s in owned_seeds]) or {}
    except Exception as e:
        logger.debug(f"genre lookup failed: {e}")

    clusters = cluster_seeds(owned_seeds, similars, genres_by_name,
                             max_clusters=max_mixes)
    mixes = []
    for i, cluster in enumerate(clusters, start=1):
        rng = random.Random(f"{today.isoformat()}:{profile_id}:{i}")
        artist_keys = [_norm(a) for a in cluster['artists']]
        tracks_by_artist = _owned_tracks_for(database, cluster['artists'])
        owned_tracks = interleave_owned(
            tracks_by_artist, artist_keys, mix_size - discovery_per_mix, rng)
        if len(owned_tracks) < 5:
            continue    # a mix that can barely play isn't a mix
        similar_names = []
        for a in artist_keys:
            for s in (similars.get(a) or []):
                nm = _norm(s.get('name'))
                if nm and nm not in owned and nm not in similar_names:
                    similar_names.append(nm)
        discovery = _discovery_tracks_for(
            database, similar_names[:30], discovery_per_mix, profile_id)
        # weave discovery every ~5th slot so it flavors rather than clumps
        tracks = list(owned_tracks)
        step = max(4, len(tracks) // (len(discovery) + 1)) if discovery else 0
        for j, d in enumerate(discovery):
            tracks.insert(min(len(tracks), (j + 1) * step + j), d)
        mixes.append({
            "key": f"daily_mix_{len(mixes) + 1}",
            "name": f"Daily Mix {len(mixes) + 1}",
            "subtitle": ", ".join(cluster['artists'][:3]) + (
                " and more" if len(cluster['artists']) > 3 else ""),
            "genre": cluster.get('genre') or "",
            "artists": cluster['artists'],
            "tracks": tracks,
            "owned_count": len(owned_tracks),
            "total": len(tracks),
        })
    return {
        "mixes": mixes,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "profile_id": profile_id,
        # payload version: bump to invalidate stored payloads whose SHAPE or
        # content rules changed (v2 = browser-safe art urls)
        "v": PAYLOAD_VERSION,
    }


def get_or_build_daily_mixes(database, profile_id: int = 1, *,
                             ttl_hours: float = TTL_HOURS,
                             force: bool = False) -> Dict[str, Any]:
    """The endpoint's entry: serve the stored payload while it's fresh,
    rebuild once it ages past the TTL (so the mixes are 'daily' without an
    automation to wire)."""
    if not force:
        stored = None
        try:
            stored = database.get_curated_playlist(CURATED_KEY, profile_id)
        except Exception as e:
            logger.debug(f"stored daily mixes unreadable: {e}")
        if (isinstance(stored, dict) and stored.get("mixes")
                and stored.get("v") == PAYLOAD_VERSION):
            try:
                age = datetime.now(timezone.utc) - datetime.fromisoformat(
                    stored.get("generated_at", ""))
                if age.total_seconds() < ttl_hours * 3600:
                    return stored
            except ValueError:
                pass
    payload = generate_daily_mixes(database, profile_id)
    try:
        database.save_curated_playlist(CURATED_KEY, payload, profile_id)
    except Exception as e:
        logger.warning(f"daily mixes save failed: {e}")
    return payload
