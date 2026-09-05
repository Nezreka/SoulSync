#!/usr/bin/env python3

"""
taste-aware sourcing for the vibe seasons (summer, spring, autumn,
valentines).

the keyword approach that still runs christmas and halloween falls apart
here: searching albums literally NAMED "beach" gives a shelf of beach-titled
albums nobody asked for. a summer playlist should be built from what the
user actually plays in summer plus music that sounds like summer, so the
legs are:

1. rewind - the user's own most-played tracks during this season's months,
   across every year of listening history. always on-taste by definition.
2. vibe-tagged library albums - albums whose lastfm tags / genres / mood
   match the season's vibe words, for the albums shelf.
3. vibe pool picks - discovery pool tracks by artists the library tags as
   seasonal-sounding. the discovery leg: new music, real ids, real art.
4. lastfm tag chain (optional) - tag top artists the user does NOT know
   yet, their top tracks, art borrowed from the pool. capped hard.

everything returns dicts shaped for SeasonalDiscoveryService._add_seasonal_*
so the storage, curation and endpoints stay untouched.
"""

import hashlib
import json
from typing import Any, Dict, List, Optional

from core.library2.provider_ids import provider_id_sql
from core.library2.sql_util import owned_sql
from utils.logging_config import get_logger

logger = get_logger("seasonal_vibes")

# Where a Last.fm tag list lives on a Library-v2 row. Legacy kept one column
# per provider field; v2 folds the whole provider payload into ``enrichment``
# (core/library2/enrich._ENRICHMENT_PAYLOAD), so the vibe LIKE has to look
# inside the JSON. The extracted value is the array's text, which is exactly
# what a substring match wants.
LASTFM_TAGS_SQL = "json_extract({alias}.enrichment, '$.lastfm.tags')"
LASTFM_PLAYCOUNT_SQL = (
    "CAST(json_extract({alias}.enrichment, '$.lastfm.playcount') AS INTEGER)")

# seasons that get taste-aware sourcing. christmas and halloween keep the
# keyword flow: titles genuinely signal there.
VIBE_SEASONS = {"summer", "spring", "autumn", "valentines"}

# vibe words matched against a lib2 album's lastfm tags / genres / mood /
# style and an artist's tags. lowercase substrings, same LIKE semantics the
# genre playlists use.
SEASON_VIBE_TAGS: Dict[str, List[str]] = {
    "summer": ["summer", "beach", "surf", "tropical", "reggae", "dancehall",
               "feel good", "party", "dance", "sunny", "upbeat"],
    "spring": ["spring", "indie pop", "dream pop", "jangle", "sunshine pop",
               "feel good", "acoustic", "folk", "upbeat", "fresh"],
    "autumn": ["autumn", "folk", "indie folk", "melanchol", "mellow",
               "singer-songwriter", "slowcore", "atmospheric", "cozy",
               "acoustic"],
    "valentines": ["love song", "romantic", "romance", "soul", "r&b", "rnb",
                   "slow jam", "ballad", "smooth", "sensual"],
}

# a couple of lastfm tags per season for the discovery chain. these hit
# tag.gettopartists, so they must be real lastfm tags, not substrings.
SEASON_LASTFM_TAGS: Dict[str, List[str]] = {
    "summer": ["summer", "tropical house"],
    "spring": ["indie pop", "dream pop"],
    "autumn": ["indie folk", "melancholic"],
    "valentines": ["love songs", "slow jams"],
}


# The four album columns a vibe word is matched against, v2 spelling.
_ALBUM_VIBE_COLUMNS = [
    LASTFM_TAGS_SQL.format(alias="al"), "al.genres", "al.mood", "al.style",
]


def _synth_id(prefix: str, artist: str, title: str) -> str:
    """stable fake track id for rows that have no source id. the playlist
    reader resolves by this key and the download flow goes by names, so a
    hash is enough."""
    digest = hashlib.md5(f"{artist}|{title}".lower().encode()).hexdigest()[:16]
    return f"{prefix}_{digest}"


def _normalize_art(url: Optional[str]) -> Optional[str]:
    """library thumb urls are media-server relative; route them through the
    image cache like every other discover surface."""
    from core.metadata.artwork import normalize_image_url
    return normalize_image_url(url)


def real_months_for(active_months: List[int], hemisphere: str, holiday: bool) -> List[int]:
    """the config months are northern calendar months. a southern user's
    summer runs december-february, so the rewind query needs the shifted
    real months. holidays stay calendar-fixed."""
    if hemisphere == "southern" and not holiday:
        return [((m - 1 + 6) % 12) + 1 for m in active_months]
    return list(active_months)


def rewind_tracks(database, months: List[int], limit: int = 60) -> List[Dict[str, Any]]:
    """the user's most-played tracks during these calendar months, any year.
    art and duration come from the library when the track is owned.

    the library lookup pre-filters by the candidate ARTISTS: a naive
    per-track LOWER() match scans 300k tracks per candidate and never
    finishes on a real install."""
    try:
        placeholders = ",".join("?" for _ in months)
        with database._get_connection() as conn:
            cursor = conn.cursor()
            # fetch deep, then diversify: the raw top of the window is a
            # handful of heavy-rotation artists, and 60 tracks by 10 artists
            # collapses to 30 after the artist cap downstream
            cursor.execute(f"""
                SELECT artist, title, album, COUNT(*) AS plays
                FROM listening_history
                WHERE CAST(strftime('%m', played_at) AS INTEGER) IN ({placeholders})
                  AND artist IS NOT NULL AND artist != ''
                  AND title IS NOT NULL AND title != ''
                GROUP BY LOWER(artist), LOWER(title)
                ORDER BY plays DESC
                LIMIT 400
            """, months)
            candidates = cursor.fetchall()
            per_artist_seen: Dict[str, int] = {}
            plays_rows = []
            for row in candidates:
                key = str(row["artist"]).lower()
                if per_artist_seen.get(key, 0) >= 3:
                    continue
                per_artist_seen[key] = per_artist_seen.get(key, 0) + 1
                plays_rows.append(row)
                if len(plays_rows) >= limit:
                    break

            # history rows carry joined credits ("Bad Bunny, Jhayco"); the
            # library files under the primary artist, so look up both forms
            def _primary(name: str) -> str:
                return name.split(",")[0].strip().lower()

            artists = set()
            for r in plays_rows:
                name = str(r["artist"]).lower()
                artists.add(name)
                artists.add(_primary(name))
            artists = sorted(artists)
            lib = {}
            if artists:
                marks = ",".join("?" for _ in artists)
                cursor.execute(f"""
                    SELECT LOWER(t.title) AS tt, LOWER(ar.name) AS an,
                           al.image_url AS thumb_url, t.duration
                    FROM lib2_tracks t
                    JOIN lib2_albums al ON al.id = t.album_id
                    JOIN lib2_artists ar ON ar.id = al.primary_artist_id
                    WHERE LOWER(ar.name) IN ({marks})
                      AND {owned_sql('track', 't')}
                """, artists)
                artist_art: Dict[str, Any] = {}
                for row in cursor.fetchall():
                    lib.setdefault((row["an"], row["tt"]), row)
                    if row["thumb_url"] and row["an"] not in artist_art:
                        artist_art[row["an"]] = row["thumb_url"]
            else:
                artist_art = {}

            out = []
            for row in plays_rows:
                artist, title, album, plays = row["artist"], row["title"], row["album"], row["plays"]
                owned = (lib.get((artist.lower(), title.lower()))
                         or lib.get((_primary(artist), title.lower())))
                # exact track first; any owned album by the artist beats a
                # blank circle when the specific single is not in the library
                art = _normalize_art(
                    (owned["thumb_url"] if owned and owned["thumb_url"] else None)
                    or artist_art.get(artist.lower())
                    or artist_art.get(_primary(artist)))
                # lib2 stores duration in MILLIseconds already (legacy was seconds).
                duration = (owned["duration"] or 0) if owned else 0
                out.append({
                    "spotify_track_id": _synth_id("seasonal_rewind", artist, title),
                    "track_name": title,
                    "artist_name": artist,
                    "album_name": album or "",
                    "album_cover_url": art,
                    "duration_ms": duration,
                    # plays map into the popular tier so the 60/30/10 mix
                    # leads with them
                    "popularity": min(100, 60 + plays),
                    "track_data_json": {},
                })
            return out
    except Exception as e:
        logger.error(f"rewind query failed: {e}")
        return []


def _vibe_like_clause(columns: List[str], tags: List[str]):
    conds, params = [], []
    for col in columns:
        for tag in tags:
            conds.append(f"LOWER({col}) LIKE ?")
            params.append(f"%{tag}%")
    return " OR ".join(conds), params


def vibe_library_albums(database, season_key: str, months: List[int], source: str,
                        limit: int = 40) -> List[Dict[str, Any]]:
    """albums for the seasonal shelf: the ones the user actually played in
    this window plus vibe-tagged ones from the library. only albums with a
    real id for the active source make it - the shelf click fetches the
    album from that source by id."""
    id_col = {"spotify": "spotify_album_id", "itunes": "itunes_album_id",
              "deezer": "deezer_id"}.get(source, "spotify_album_id")
    # v2 promotes Spotify to a column and keeps the rest in `external_ids`, so
    # the provider column legacy interpolated becomes an expression — aliased
    # back to the legacy name the reader below still speaks.
    id_sql = provider_id_sql(
        {"spotify_album_id": "spotify", "itunes_album_id": "itunes",
         "deezer_id": "deezer"}[id_col], alias="al")
    tags = SEASON_VIBE_TAGS.get(season_key, [])
    if not tags:
        return []
    try:
        out, seen = [], set()

        def add(row, popularity):
            key = (str(row["title"]).lower(), str(row["name"]).lower())
            if key in seen or not row[id_col]:
                return
            seen.add(key)
            out.append({
                "spotify_album_id": row[id_col],
                "album_name": row["title"],
                "artist_name": row["name"],
                "album_cover_url": _normalize_art(row["thumb_url"]),
                "release_date": row["release_date"],
                "popularity": popularity,
            })

        with database._get_connection() as conn:
            cursor = conn.cursor()

            # leg a: most-played albums in the season window. group the
            # history first and join through the artist set in python - a
            # LOWER() join of history against albums is quadratic.
            placeholders = ",".join("?" for _ in months)
            cursor.execute(f"""
                SELECT artist, album, COUNT(*) AS plays
                FROM listening_history
                WHERE CAST(strftime('%m', played_at) AS INTEGER) IN ({placeholders})
                  AND album IS NOT NULL AND album != ''
                  AND artist IS NOT NULL AND artist != ''
                GROUP BY LOWER(artist), LOWER(album)
                ORDER BY plays DESC
                LIMIT 300
            """, months)
            played = cursor.fetchall()

            artist_names = sorted({str(r["artist"]).lower() for r in played})
            owned = {}
            if artist_names:
                marks = ",".join("?" for _ in artist_names)
                cursor.execute(f"""
                    SELECT al.title, ar.name, al.image_url AS thumb_url,
                           al.release_date, {id_sql} AS {id_col}
                    FROM lib2_albums al
                    JOIN lib2_artists ar ON ar.id = al.primary_artist_id
                    WHERE LOWER(ar.name) IN ({marks})
                      AND {owned_sql('album', 'al')}
                """, artist_names)
                for row in cursor.fetchall():
                    owned.setdefault(
                        (str(row["name"]).lower(), str(row["title"]).lower()), row)

            picked = 0
            for r in played:
                row = owned.get((str(r["artist"]).lower(), str(r["album"]).lower()))
                if not row:
                    continue
                add(row, min(100, 60 + r["plays"]))
                picked += 1
                if picked >= limit // 2:
                    break

            # leg b: vibe-tagged albums, most-listened first
            like, params = _vibe_like_clause(_ALBUM_VIBE_COLUMNS, tags)
            cursor.execute(f"""
                SELECT al.title, ar.name, al.image_url AS thumb_url,
                       al.release_date, {id_sql} AS {id_col}
                FROM lib2_albums al
                JOIN lib2_artists ar ON ar.id = al.primary_artist_id
                WHERE ({like}) AND {id_sql} IS NOT NULL
                  AND {owned_sql('album', 'al')}
                ORDER BY {LASTFM_PLAYCOUNT_SQL.format(alias='al')} DESC NULLS LAST
                LIMIT ?
            """, [*params, limit])
            for row in cursor.fetchall():
                add(row, 50)

        return out[:limit]
    except Exception as e:
        logger.error(f"vibe album query failed: {e}")
        return []


def _vibe_artist_names(database, season_key: str) -> set:
    """artists the library tags as seasonal-sounding, matched two ways:
    their own tags AND their albums' tags. album tagging covers 5x more of
    the library, and the pool has so few distinct artists that every extra
    name matters."""
    tags = SEASON_VIBE_TAGS.get(season_key, [])
    if not tags:
        return set()
    with database._get_connection() as conn:
        cursor = conn.cursor()
        like, params = _vibe_like_clause(
            [LASTFM_TAGS_SQL.format(alias="ar"), "ar.genres", "ar.mood", "ar.style"],
            tags)
        cursor.execute(
            f"SELECT ar.name FROM lib2_artists ar WHERE {like}", params)
        names = {str(r["name"]).lower() for r in cursor.fetchall()}
        like, params = _vibe_like_clause(_ALBUM_VIBE_COLUMNS, tags)
        cursor.execute(f"""
            SELECT DISTINCT ar.name FROM lib2_albums al
            JOIN lib2_artists ar ON ar.id = al.primary_artist_id
            WHERE {like}
        """, params)
        names.update(str(r["name"]).lower() for r in cursor.fetchall())
    return names


def vibe_owned_tracks(database, season_key: str, limit: int = 40,
                      per_album: int = 2) -> List[Dict[str, Any]]:
    """owned tracks from the most-listened vibe-tagged albums. this is the
    depth leg: it widens the artist spread and lands in the mid tier so the
    60/30/10 mix has something below the rewind heavy-hitters."""
    tags = SEASON_VIBE_TAGS.get(season_key, [])
    if not tags:
        return []
    try:
        with database._get_connection() as conn:
            cursor = conn.cursor()
            like, params = _vibe_like_clause(_ALBUM_VIBE_COLUMNS, tags)
            cursor.execute(f"""
                SELECT al.id, al.title AS album_title, ar.name AS artist_name,
                       al.image_url AS thumb_url
                FROM lib2_albums al
                JOIN lib2_artists ar ON ar.id = al.primary_artist_id
                WHERE ({like}) AND {owned_sql('album', 'al')}
                ORDER BY {LASTFM_PLAYCOUNT_SQL.format(alias='al')} DESC NULLS LAST
                LIMIT ?
            """, [*params, limit])
            albums = cursor.fetchall()

            out = []
            for album in albums:
                cursor.execute(f"""
                    SELECT t.title, t.duration FROM lib2_tracks t
                    WHERE t.album_id = ? AND {owned_sql('track', 't')}
                    ORDER BY t.track_number
                    LIMIT ?
                """, (album["id"], per_album))
                art = _normalize_art(album["thumb_url"])
                for track in cursor.fetchall():
                    out.append({
                        "spotify_track_id": _synth_id(
                            "seasonal_lib", album["artist_name"], track["title"]),
                        "track_name": track["title"],
                        "artist_name": album["artist_name"],
                        "album_name": album["album_title"],
                        "album_cover_url": art,
                        "duration_ms": track["duration"] or 0,
                        "popularity": 50,
                        "track_data_json": {},
                    })
                    if len(out) >= limit:
                        return out
            return out
    except Exception as e:
        logger.error(f"owned vibe query failed: {e}")
        return []


def vibe_pool_tracks(database, season_key: str, source: str,
                     limit: int = 40, per_artist: int = 2) -> List[Dict[str, Any]]:
    """discovery pool tracks by artists the library tags as
    seasonal-sounding. this is the new-music leg: pool rows carry real
    source ids and art."""
    names = _vibe_artist_names(database, season_key)
    if not names:
        return []
    id_col = "spotify_track_id" if source == "spotify" else "itunes_track_id"
    try:
        with database._get_connection() as conn:
            cursor = conn.cursor()

            # stage the names in a temp table: the pool is six figures of
            # rows with json blobs, so the filter has to happen in sql
            cursor.execute("DROP TABLE IF EXISTS temp.seasonal_vibe_names")
            cursor.execute("CREATE TEMP TABLE seasonal_vibe_names (name TEXT PRIMARY KEY)")
            cursor.executemany(
                "INSERT OR IGNORE INTO temp.seasonal_vibe_names VALUES (?)",
                [(n,) for n in names])
            cursor.execute(f"""
                SELECT {id_col} AS track_id, track_name, artist_name, album_name,
                       album_cover_url, duration_ms, popularity, track_data_json
                FROM discovery_pool
                WHERE source = ? AND {id_col} IS NOT NULL
                  AND LOWER(artist_name) IN (SELECT name FROM temp.seasonal_vibe_names)
                ORDER BY popularity DESC
                LIMIT ?
            """, (source, limit * 8))

            out, counts = [], {}
            for row in cursor.fetchall():
                artist_lower = str(row["artist_name"] or "").lower()
                if counts.get(artist_lower, 0) >= per_artist:
                    continue
                counts[artist_lower] = counts.get(artist_lower, 0) + 1
                data = row["track_data_json"]
                if isinstance(data, str):
                    try:
                        data = json.loads(data)
                    except Exception:
                        data = {}
                out.append({
                    "spotify_track_id": row["track_id"],
                    "track_name": row["track_name"],
                    "artist_name": row["artist_name"],
                    "album_name": row["album_name"],
                    "album_cover_url": row["album_cover_url"],
                    "duration_ms": row["duration_ms"],
                    "popularity": row["popularity"],
                    "track_data_json": data or {},
                })
                if len(out) >= limit:
                    break
            return out
    except Exception as e:
        logger.error(f"vibe pool query failed: {e}")
        return []


def lastfm_tag_tracks(database, lastfm_client, season_key: str,
                      limit: int = 20) -> List[Dict[str, Any]]:
    """fresh-artist leg: lastfm tag top artists the library does not have,
    their top tracks. art is borrowed from the pool when the artist shows
    up there; artless rows are dropped rather than shipped as grey circles."""
    if not lastfm_client:
        return []
    tags = SEASON_LASTFM_TAGS.get(season_key, [])
    if not tags:
        return []
    try:
        with database._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT LOWER(name) AS n FROM lib2_artists")
            known = {r["n"] for r in cursor.fetchall()}

        out = []
        for tag in tags:
            artists = lastfm_client.get_tag_top_artists(tag, limit=12) or []
            fresh = [a.get("name") for a in artists
                     if a.get("name") and a["name"].lower() not in known][:5]
            for name in fresh:
                for track in (lastfm_client.get_artist_top_tracks(name, limit=2) or []):
                    title = track.get("name")
                    if not title:
                        continue
                    art = _pool_art_for_artist(database, name)
                    if not art:
                        continue
                    out.append({
                        "spotify_track_id": _synth_id("seasonal_tag", name, title),
                        "track_name": title,
                        "artist_name": name,
                        "album_name": "",
                        "album_cover_url": art,
                        "duration_ms": 0,
                        "popularity": 45,
                        "track_data_json": {},
                    })
                    if len(out) >= limit:
                        return out
        return out
    except Exception as e:
        logger.error(f"lastfm tag chain failed: {e}")
        return []


def _pool_art_for_artist(database, artist_name: str) -> Optional[str]:
    try:
        with database._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT album_cover_url FROM discovery_pool
                WHERE LOWER(artist_name) = LOWER(?)
                  AND album_cover_url IS NOT NULL AND album_cover_url != ''
                LIMIT 1
            """, (artist_name,))
            row = cursor.fetchone()
            return row["album_cover_url"] if row else None
    except Exception:
        return None
