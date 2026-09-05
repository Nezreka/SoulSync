"""Recommended Stations - artist radio cards from the listening history.

spotify's "recommended stations" row: your heaviest recent artists as
one-click radio. ours plays through the artist-radio seam that already
exists (startArtistRadioById -> the library's own tracks + similarity
refill), so a station starts in under a second with zero downloads.

the "With X, Y and more" subtitles come from similar_artists, resolved
through SOURCE ids (never artists.id - the id-smear lesson).
"""

from typing import Any, Dict, List

from utils.logging_config import get_logger

logger = get_logger("discovery.stations")

MAX_STATIONS = 10
WITH_NAMES = 3


def _norm(text: Any) -> str:
    return str(text or "").strip().lower()


def build_stations(database, profile_id: int = 1,
                   max_stations: int = MAX_STATIONS) -> List[Dict[str, Any]]:
    """Station cards for the discover page.

    Recency-weighted top artists, kept only when OWNED (radio needs library
    tracks to start from). Each carries the artist row id the radio seam
    wants, art, and up to three similar-artist names for the subtitle.
    """
    from core.discovery.listening_recommendations import build_recency_weighted_seeds

    top = database.get_top_artists('all', 120) or []
    recent = database.get_top_artists('30d', 120) or []
    seeds = build_recency_weighted_seeds(
        top, {a['name']: a.get('play_count', 0) for a in recent})
    seeds = sorted(seeds, key=lambda s: -s['weight'])

    stations: List[Dict[str, Any]] = []
    seed_names = [_norm(s['name']) for s in seeds]
    if not seed_names:
        return stations

    with database._get_connection() as conn:
        cur = conn.cursor()
        placeholders = ",".join("?" * len(seed_names))
        cur.execute(
            f"""
            SELECT ar.id, ar.name, ar.image_url AS thumb_url,
                   ar.spotify_id AS spotify_artist_id,
                   json_extract(ar.external_ids, '$.itunes') AS itunes_artist_id,
                   json_extract(ar.external_ids, '$.deezer') AS deezer_id,
                   ar.musicbrainz_id,
                   (SELECT COUNT(*)
                      FROM lib2_tracks t
                      JOIN lib2_albums al ON al.id = t.album_id
                      JOIN lib2_track_files f ON f.track_id = t.id
                     WHERE al.primary_artist_id = ar.id
                       AND f.path IS NOT NULL AND TRIM(f.path) != ''
                       AND COALESCE(f.file_state, 'active') = 'active') AS playable
            FROM lib2_artists ar
            WHERE LOWER(ar.name) IN ({placeholders})
            """,
            seed_names)
        by_name: Dict[str, dict] = {}
        source_to_name: Dict[str, str] = {}
        for row in cur.fetchall():
            r = dict(row)
            key = _norm(r["name"])
            if key in by_name and by_name[key]["playable"] >= r["playable"]:
                continue
            by_name[key] = r
            for sid in (r.get("spotify_artist_id"), r.get("itunes_artist_id"),
                        r.get("deezer_id"), r.get("musicbrainz_id")):
                if sid:
                    source_to_name[str(sid)] = key

        withs: Dict[str, List[str]] = {}
        if source_to_name:
            placeholders = ",".join("?" * len(source_to_name))
            cur.execute(
                f"""
                SELECT source_artist_id, similar_artist_name, similarity_rank
                FROM similar_artists
                WHERE profile_id = ? AND source_artist_id IN ({placeholders})
                ORDER BY similarity_rank ASC
                """,
                [profile_id, *source_to_name.keys()])
            for row in cur.fetchall():
                seed = source_to_name.get(str(row[0]))
                sim = str(row[1] or "").strip()
                if not seed or not sim:
                    continue
                bucket = withs.setdefault(seed, [])
                # case-insensitive dedupe: the edges hold Ke$ha AND Kesha,
                # Blanku AND blanku - one spelling per companion
                if _norm(sim) != seed and _norm(sim) not in {_norm(b) for b in bucket}:
                    bucket.append(sim)

    from core.metadata import normalize_image_url
    for s in seeds:
        key = _norm(s['name'])
        row = by_name.get(key)
        # a station needs enough library tracks to actually BE a station
        if not row or (row.get("playable") or 0) < 3:
            continue
        stations.append({
            "artist_id": row["id"],
            "name": row["name"],
            # media-server-relative thumbs need the browser-safe conversion
            "image_url": (normalize_image_url(row.get("thumb_url"))
                          if row.get("thumb_url") else "") or "",
            "with": (withs.get(key) or [])[:WITH_NAMES],
        })
        if len(stations) >= max_stations:
            break
    return stations
