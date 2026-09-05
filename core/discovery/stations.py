"""Recommended Stations - artist radio cards from the listening history.

spotify's "recommended stations" row: your heaviest recent artists as
one-click radio. ours plays through the artist-radio seam that already
exists (startArtistRadioById -> the library's own tracks + similarity
refill), so a station starts in under a second with zero downloads.

the "With X and Y" subtitles come from similar_artists, resolved through
SOURCE ids (never artists.id - the id-smear lesson), and then VERIFIED: a name
only earns "With" if that artist actually has playable tracks in the library.
everything else is offered as a weaker "Related artists" label, because the
queue is built by core/radio/selection.py from genre and mood tiers and never
promised a specific companion.

a station also has a finite side. build_station_snapshot returns up to forty
library tracks for the seed, stored so the selection cannot move under an open
dialog, and it never touches playback: no queue, no player, no audio.
"""

import os
from typing import Any, Dict, List, Optional

from utils.logging_config import get_logger

logger = get_logger("discovery.stations")

MAX_STATIONS = 10
WITH_NAMES = 3
RELATED_NAMES = 3

# the finite preview. forty is a product default, not a discovered optimum -
# it is bounded, explicit, and the response always reports what it really got.
SNAPSHOT_LIMIT = 40
# bump to invalidate stored snapshots whose SHAPE or content rules changed.
# v2 = cover urls pass through normalize_image_url (a raw media-server thumb
# path is not loadable from a browser, so every row rendered art-less)
SNAPSHOT_SCHEMA = 2
SNAPSHOT_ALGORITHM = "station-v1"


def snapshot_key(artist_id: Any) -> str:
    """Where one station's preview lives, per profile."""
    return f"station_snapshot_{artist_id}"


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
            SELECT ar.id, ar.name, ar.thumb_url,
                   ar.spotify_artist_id, ar.itunes_artist_id, ar.deezer_id,
                   ar.musicbrainz_id,
                   (SELECT COUNT(*) FROM tracks t
                    WHERE t.artist_id = ar.id
                      AND t.file_path IS NOT NULL AND t.file_path != '') AS playable
            FROM artists ar
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

        # which companions the library can actually play. a name that is not
        # here has not earned "With" - the radio queue is assembled from genre
        # and mood tiers and never promised that artist would appear.
        playable_companions = set()
        every_companion = {n for names in withs.values() for n in names}
        if every_companion:
            placeholders = ",".join("?" * len(every_companion))
            cur.execute(
                f"""
                SELECT LOWER(ar.name)
                FROM artists ar
                WHERE LOWER(ar.name) IN ({placeholders})
                  AND EXISTS (SELECT 1 FROM tracks t
                              WHERE t.artist_id = ar.id
                                AND t.file_path IS NOT NULL AND t.file_path != '')
                """,
                [_norm(n) for n in every_companion])
            playable_companions = {row[0] for row in cur.fetchall()}

    from core.metadata import normalize_image_url
    for s in seeds:
        key = _norm(s['name'])
        row = by_name.get(key)
        # a station needs enough library tracks to actually BE a station
        if not row or (row.get("playable") or 0) < 3:
            continue
        companions = withs.get(key) or []
        verified = [n for n in companions if _norm(n) in playable_companions]
        unverified = [n for n in companions if _norm(n) not in playable_companions]
        stations.append({
            "artist_id": row["id"],
            "name": row["name"],
            # media-server-relative thumbs need the browser-safe conversion
            "image_url": (normalize_image_url(row.get("thumb_url"))
                          if row.get("thumb_url") else "") or "",
            # only artists this library can actually play
            "with": verified[:WITH_NAMES],
            # named, but not guaranteed by any playback contract
            "related": unverified[:RELATED_NAMES],
            "playable_tracks": int(row.get("playable") or 0),
        })
        if len(stations) >= max_stations:
            break
    return stations


# ── the finite station preview ──────────────────────────────────────────────


def _track_columns(cur) -> set:
    cur.execute("PRAGMA table_info(tracks)")
    return {row[1] for row in cur.fetchall()}


def _snapshot_row(row: Dict[str, Any]) -> Dict[str, Any]:
    """One library track as the preview renders, downloads and syncs it.

    a file path is evidence of a library reference, not proof the file is
    still there, so availability is checked rather than assumed. duration
    stays in the milliseconds the tracks table already stores - the daily-mix
    bug came from multiplying it a second time.
    """
    from core.metadata import normalize_image_url

    path = row.get("file_path") or ""
    available = False
    if path:
        try:
            available = os.path.exists(path)
        except OSError:
            available = False
    return {
        "library_track_id": row.get("id"),
        "track_id": str(row.get("id")),
        # the download/sync converters read `id` when there is no provider id
        "id": str(row.get("id")),
        "track_name": row.get("title") or "",
        "artist_name": row.get("artist") or "",
        "album_name": row.get("album") or "",
        # media-server thumbs are RELATIVE paths (or carry a token); the raw
        # value is not loadable from a browser, so every row came out art-less.
        # the same conversion the station card's own image already used.
        "album_cover_url": (normalize_image_url(row.get("image_url"))
                            if row.get("image_url") else None),
        "duration_ms": int(row.get("duration") or 0),
        "bitrate": row.get("bitrate"),
        "has_file_path": bool(path),
        "available": available,
        "owned": True,
        "source": "library",
    }


def build_station_snapshot(database, artist_id: Any, profile_id: int = 1,
                           limit: int = SNAPSHOT_LIMIT,
                           refresh: bool = False) -> Dict[str, Any]:
    """A finite, immutable preview of one station. NEVER touches playback.

    the card used to offer exactly one thing: start endless radio. there was
    no selection to inspect, download or sync, because the queue only existed
    inside the player and kept refilling. this builds a bounded list instead,
    stores it, and hands back the same revision until something asks for a new
    one - so a checkbox cannot move under an open download dialog.

    generating a preview does not start audio, pause audio, or modify the
    current queue. "Play radio" is still the other, separate action.
    """
    key = snapshot_key(artist_id)
    existing = None
    try:
        stored = database.get_curated_playlist(key, profile_id=profile_id)
        if isinstance(stored, list) and stored and isinstance(stored[0], dict):
            existing = stored[0]
    except Exception as e:  # noqa: BLE001
        logger.debug("station snapshot read failed: %s", e)
    # a snapshot from an older schema is REBUILT, not served: it was written
    # under different content rules and would keep serving them until someone
    # thought to hit Refresh.
    stale_schema = bool(existing) and existing.get("schema") != SNAPSHOT_SCHEMA
    if existing and not refresh and not stale_schema:
        return existing

    revision = int((existing or {}).get("revision") or 0) + 1

    with database._get_connection() as conn:
        cur = conn.cursor()
        cur.execute("SELECT id, name, thumb_url FROM artists WHERE id = ?", (artist_id,))
        artist = cur.fetchone()
        if not artist:
            return _unavailable(artist_id, revision, profile_id,
                                "unknown-artist", "That artist is not in your library.")
        artist = dict(artist)

        cols = _track_columns(cur)
        order = "COALESCE(t.play_count, 0) DESC, t.id ASC" if "play_count" in cols else "t.id ASC"
        cur.execute(f"""
            SELECT t.id, t.title, t.duration, t.file_path, t.bitrate,
                   al.title AS album,
                   COALESCE(al.thumb_url, ar.thumb_url) AS image_url,
                   ar.name AS artist
            FROM tracks t
            JOIN artists ar ON ar.id = t.artist_id
            LEFT JOIN albums al ON al.id = t.album_id
            WHERE t.artist_id = ?
              AND t.file_path IS NOT NULL AND t.file_path != ''
            ORDER BY {order}
            LIMIT 1
        """, (artist_id,))
        seed = cur.fetchone()

    if not seed:
        return _unavailable(artist_id, revision, profile_id, "no-playable-tracks",
                            f"No playable {artist['name']} tracks in your library.",
                            name=artist["name"])

    seed = dict(seed)
    rows = [_snapshot_row(seed)]
    try:
        radio = database.get_radio_tracks(seed["id"], limit=max(0, limit - 1))
        # a refusal is not an exception, and a preview that silently contains
        # only its seed track looks like a small library rather than a broken
        # selection. say which one it was.
        if not radio.get("success"):
            logger.warning("station radio selection refused for %s: %s",
                           artist["name"], radio.get("error"))
        for row in (radio.get("tracks") or []):
            rows.append(_snapshot_row(dict(row)))
    except Exception as e:  # noqa: BLE001 - a short preview beats no preview
        logger.debug("station radio selection failed: %s", e)

    from core.metadata import normalize_image_url
    snapshot = {
        "schema": SNAPSHOT_SCHEMA,
        "algorithm": SNAPSHOT_ALGORITHM,
        "snapshot_id": f"{artist_id}-r{revision}",
        "revision": revision,
        "profile_id": profile_id,
        "station": {
            "artist_id": artist["id"],
            "name": artist["name"],
            "image_url": (normalize_image_url(artist.get("thumb_url"))
                          if artist.get("thumb_url") else "") or "",
        },
        "generated_at": _now(),
        "requested": limit,
        "tracks": rows[:limit],
        "counts": {
            "returned": len(rows[:limit]),
            "available": sum(1 for r in rows[:limit] if r["available"]),
            "unavailable": sum(1 for r in rows[:limit] if not r["available"]),
        },
        # every action the preview supports. the client must not invent one.
        "actions": ["play", "download", "sync"],
        "status": "ok",
        "reason": None,
        "message": None,
    }
    if len(snapshot["tracks"]) < limit:
        count = len(snapshot["tracks"])
        snapshot["message"] = (
            f"{count} track{'' if count == 1 else 's'} - that is everything your "
            f"library has for this station.")
    _store_snapshot(database, artist_id, snapshot, profile_id)
    return snapshot


def _now() -> str:
    from datetime import datetime
    return datetime.now().isoformat(timespec="seconds")


def _unavailable(artist_id: Any, revision: int, profile_id: int, reason: str,
                 message: str, name: str = "") -> Dict[str, Any]:
    """An honest empty preview. it says why, and it is not stored: nothing
    about it is worth keeping stable."""
    return {
        "schema": SNAPSHOT_SCHEMA,
        "algorithm": SNAPSHOT_ALGORITHM,
        "snapshot_id": f"{artist_id}-r{revision}",
        "revision": revision,
        "profile_id": profile_id,
        "station": {"artist_id": artist_id, "name": name, "image_url": ""},
        "generated_at": _now(),
        "requested": 0,
        "tracks": [],
        "counts": {"returned": 0, "available": 0, "unavailable": 0},
        "actions": [],
        "status": "unavailable",
        "reason": reason,
        "message": message,
    }


def _store_snapshot(database, artist_id: Any, snapshot: Dict[str, Any],
                    profile_id: int) -> bool:
    try:
        return bool(database.save_curated_playlist(
            snapshot_key(artist_id), [snapshot], profile_id=profile_id))
    except Exception as e:  # noqa: BLE001
        logger.debug("station snapshot store failed: %s", e)
        return False
