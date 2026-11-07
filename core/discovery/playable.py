"""Resolve a mix's tracklist against the library - the play-now bridge.

every discover mix is artist/title pairs from metadata sources. the media
player's window.playTrackList wants library rows with a file_path. this
maps one to the other so any mix can PLAY what the user already owns, with
the missing remainder staying one click from download. that owned+missing
blend is the thing a lidarr companion structurally cannot do.
"""

import re
from typing import Any, Dict, List

from utils.logging_config import get_logger

logger = get_logger("discovery.playable")

MAX_RESOLVE = 250


def _norm(text: str) -> str:
    return (text or "").strip().lower()


# a credit string names several artists: "Cardi B, Grand Theft Auto VI"
_CREDIT_SPLIT = re.compile(r"\s*(?:,|;|&|\bfeat\.?|\bft\.?|\bfeaturing\b)\s*", re.I)
# the media servers a playlist's stored link can name (indexed by source first)
_SERVERS = ("plex", "jellyfin", "navidrome", "emby", "subsonic", "soulsync")


def _artist_keys(*names: Any) -> List[str]:
    """every artist a row can be asked for by: the whole credit, then each
    name in it. a playlist row says "Cardi B", the track credits "Cardi B,
    Grand Theft Auto VI"; the old exact match missed it and the player
    skipped a song the library had."""
    keys: List[str] = []
    for name in names:
        whole = _norm(str(name or ""))
        if not whole:
            continue
        for key in [whole, *(_norm(part) for part in _CREDIT_SPLIT.split(whole))]:
            if key and key not in keys:
                keys.append(key)
    return keys


def _candidate_sql(extra: str, where: str, link: str = "NULL") -> str:
    """one preferred live file per recording, the row the player gets"""
    return f"""
        SELECT t.id, t.title, t.duration, {extra}
               f.path AS file_path,
               al.title AS album,
               COALESCE(al.image_url, ar.image_url) AS image_url,
               COALESCE(NULLIF(t.track_artist, ''), ar.name) AS artist,
               COALESCE(ar.canonical_artist_id, ar.id) AS artist_id,
               t.album_id,
               ar.name AS _album_artist,
               {link} AS _link
        FROM lib2_tracks t
        JOIN lib2_albums al ON al.id = t.album_id
        LEFT JOIN lib2_artists ar ON ar.id = al.primary_artist_id
        JOIN lib2_track_files f ON f.id = (
            SELECT preferred.id
            FROM lib2_track_files preferred
            WHERE preferred.track_id = t.id
              AND preferred.path IS NOT NULL
              AND TRIM(preferred.path) != ''
              AND COALESCE(preferred.file_state, 'active') = 'active'
            ORDER BY preferred.is_primary DESC, preferred.id
            LIMIT 1
        )
        WHERE {where}"""


def _linked_rows(cursor, extra: str, links: List[str]) -> Dict[str, Dict[str, Any]]:
    """the library rows a playlist's stored links name.

    The library check stamps ``library_track_id`` on a mirrored row: the media
    server's id (a plex ratingKey) or a catalogue id. Play read it nowhere and
    guessed again from title + artist. A server id wins (mapping, then the
    row's own); a bare number is a catalogue id only when no server knows it.
    """
    found: Dict[str, Dict[str, Any]] = {}
    if not links:
        return found
    servers = ",".join("?" for _ in _SERVERS)
    for start in range(0, len(links), 400):
        chunk = links[start:start + 400]
        marks = ",".join("?" for _ in chunk)
        cursor.execute(
            _candidate_sql(extra, f"t.id IN (SELECT m.entity_id FROM lib2_media_server_mappings m"
                                  f" WHERE m.server_source IN ({servers}) AND m.entity_type = 'track'"
                                  f" AND m.server_id IN ({marks}))",
                           link="(SELECT m2.server_id FROM lib2_media_server_mappings m2"
                                " WHERE m2.entity_type = 'track' AND m2.entity_id = t.id"
                                f" AND m2.server_id IN ({marks}) LIMIT 1)")
            + " UNION ALL "
            + _candidate_sql(extra, f"t.server_source IN ({servers}) AND t.server_id IN ({marks})",
                             link="t.server_id"),
            [*chunk, *_SERVERS, *chunk, *_SERVERS, *chunk])
        for row in cursor.fetchall():
            row = dict(row)
            if row.get("_link") is not None:
                found.setdefault(str(row["_link"]), row)
    numeric = [int(v) for v in links if v not in found and v.isdigit()]
    for start in range(0, len(numeric), 400):
        chunk = numeric[start:start + 400]
        cursor.execute(
            _candidate_sql(extra, f"t.id IN ({','.join('?' for _ in chunk)})",
                           link="CAST(t.id AS TEXT)"), chunk)
        for row in cursor.fetchall():
            row = dict(row)
            found.setdefault(str(row["_link"]), {**row, "_by_catalogue_id": True})
    return found


def resolve_playable_tracks(db, wanted: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Match [{'artist','title'}, ...] against owned tracks.

    Returns {'tracks': [radio-shaped rows], 'matched': n, 'total': m} with
    rows in the INPUT order (a mix's order is part of the mix). Lookup is
    case-insensitive on title + artist; a title that appears under several
    artists never matches on title alone.
    """
    wanted = list(wanted or [])[:MAX_RESOLVE]
    rows: List[Dict[str, Any]] = []
    queue_rows: List[Dict[str, Any]] = []
    if not wanted:
        return {"tracks": rows, "queue_tracks": queue_rows, "matched": 0, "total": 0}

    conn = db._get_connection()
    try:
        cursor = conn.cursor()
        # migration-added columns may be absent on old installs. In v2 the
        # audio facts live on the FILE row, not the recording.
        cursor.execute("PRAGMA table_info(lib2_track_files)")
        file_cols = {r[1] for r in cursor.fetchall()}
        extra = "".join(
            f"f.{c}, " for c in ("bitrate", "sample_rate") if c in file_cols
        )
        # A mix used to run one full LOWER(title) scan for every entry. Read
        # candidate titles once, then disambiguate by artist without losing
        # order. (Upstream's optimisation, ported onto the v2 catalogue: the
        # win is in the number of scans, and it is the same win here.)
        titles = sorted({_norm(str(item.get("title") or item.get("name") or ""))
                         for item in wanted} - {""})
        links = sorted({str(item.get("library_track_id") or "").strip()
                        for item in wanted} - {""})
        linked = _linked_rows(cursor, extra, links)
        candidates = {}
        if titles:
            placeholders = ",".join("?" for _ in titles)
            cursor.execute(
                _candidate_sql(extra, f"LOWER(t.title) IN ({placeholders})") + " ORDER BY t.id",
                titles,
            )
            for candidate in cursor.fetchall():
                candidate = dict(candidate)
                title_key = _norm(candidate["title"])
                for artist_key in _artist_keys(candidate["artist"], candidate.get("_album_artist")):
                    candidates.setdefault((title_key, artist_key), candidate)
        seen_paths = set()
        for item in wanted:
            title = _norm(str(item.get("title") or item.get("name") or ""))
            artist = _norm(str(item.get("artist") or ""))
            if not title or not artist:
                continue
            row = None
            link = str(item.get("library_track_id") or "").strip()
            if link and link in linked:
                row = linked[link]
                # a bare number might be another track's catalogue id: only
                # when its title agrees
                if row.get("_by_catalogue_id") and _norm(row["title"]) != title:
                    row = None
            if row is None:
                row = next((candidates[(title, key)] for key in _artist_keys(artist)
                            if (title, key) in candidates), None)
            if not row:
                missing = dict(item)
                missing.update(
                    {
                        "title": str(item.get("title") or item.get("name") or "").strip(),
                        "name": str(item.get("title") or item.get("name") or "").strip(),
                        "artist": str(item.get("artist") or "").strip(),
                        "artists": item.get("artists") or [{"name": str(item.get("artist") or "").strip()}],
                        "album": item.get("album") or item.get("album_title") or "",
                        "file_path": "",
                        "is_library": False,
                        "playback_status": "missing",
                    }
                )
                queue_rows.append(missing)
                continue
            track = {k: v for k, v in row.items() if not k.startswith("_")}
            if track.get("image_url"):
                from core.metadata import normalize_image_url
                track["image_url"] = normalize_image_url(track["image_url"]) or track["image_url"]
            track["is_library"] = True
            queue_rows.append(dict(track))
            # one copy per file - a mix repeating a track should not repeat it
            if track["file_path"] in seen_paths:
                continue
            seen_paths.add(track["file_path"])
            rows.append(track)
    except Exception as e:
        logger.error(f"resolve_playable_tracks failed: {e}")
        return {
            "tracks": [],
            "queue_tracks": [],
            "matched": 0,
            "total": len(wanted),
            "error": str(e),
        }
    finally:
        try:
            conn.close()
        except Exception:  # noqa: BLE001, S110 - best effort
            pass
    return {
        "tracks": rows,
        "queue_tracks": queue_rows,
        "matched": len(rows),
        "total": len(wanted),
    }
