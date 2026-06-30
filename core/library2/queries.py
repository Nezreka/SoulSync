"""Read queries for the Library v2 API.

All functions take an open sqlite3 connection (``row_factory = sqlite3.Row``) and
return plain dicts/lists ready to serialize. Roll-up counts go through the
``lib2_album_artists`` / ``lib2_track_artists`` junctions so a release or track that
credits multiple artists is counted under *each* of them (a song by two artists
shows under both, but is stored once).
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional, Tuple

from .status import compute_metadata_gaps, file_status, quality_tier

_SORTS = {
    "name": "a.sort_name COLLATE NOCASE, a.name COLLATE NOCASE",
    "added": "a.added_at DESC",
    "albums": "album_count DESC, a.name COLLATE NOCASE",
    "tracks": "track_count DESC, a.name COLLATE NOCASE",
}

_ARTIST_STATS = """
    (SELECT COUNT(DISTINCT al.id) FROM lib2_album_artists aa
       JOIN lib2_albums al ON al.id = aa.album_id
       WHERE aa.artist_id = a.id AND al.album_type <> 'single') AS album_count,
    (SELECT COUNT(DISTINCT al.id) FROM lib2_album_artists aa
       JOIN lib2_albums al ON al.id = aa.album_id
       WHERE aa.artist_id = a.id AND al.album_type = 'single') AS single_count,
    (SELECT COUNT(DISTINCT ta.track_id) FROM lib2_track_artists ta
       WHERE ta.artist_id = a.id) AS track_count,
    (SELECT COUNT(DISTINCT ta.track_id) FROM lib2_track_artists ta
       JOIN lib2_track_files tf ON tf.track_id = ta.track_id
       WHERE ta.artist_id = a.id) AS track_files_present
"""


def _json_dict(raw: Any) -> Dict[str, Any]:
    if not raw:
        return {}
    try:
        val = json.loads(raw)
        return val if isinstance(val, dict) else {}
    except (ValueError, TypeError):
        return {}


def _quality_profile_dict(row: Any) -> Optional[Dict[str, Any]]:
    if row is None:
        return None
    return {
        "id": row["id"],
        "name": row["name"],
        "description": row["description"],
        "upgrade_policy": row["upgrade_policy"],
        "repair_job_id": row["repair_job_id"],
        "repair_settings": _json_dict(row["repair_settings"]),
        "is_default": bool(row["is_default"]),
    }


def _json_list(raw: Any) -> List[str]:
    if not raw:
        return []
    try:
        val = json.loads(raw)
        return val if isinstance(val, list) else []
    except (ValueError, TypeError):
        return []


def list_artists(conn, *, search: str = "", sort: str = "name", monitored: str = "all",
                 page: int = 1, limit: int = 75) -> Tuple[List[Dict[str, Any]], int]:
    """Paginated artist overview with per-artist roll-up stats.

    ``monitored`` filters the list: ``'all'`` (default), ``'monitored'``, or
    ``'unmonitored'``.
    """
    order = _SORTS.get(sort, _SORTS["name"])
    page = max(1, int(page))
    limit = max(1, min(int(limit), 500))
    offset = (page - 1) * limit
    clauses, params = [], {}
    if search:
        clauses.append("a.name LIKE :like")
        params["like"] = f"%{search}%"
    if monitored == "monitored":
        clauses.append("a.monitored = 1")
    elif monitored == "unmonitored":
        clauses.append("a.monitored = 0")
    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""

    total = conn.execute(
        f"SELECT COUNT(*) AS c FROM lib2_artists a {where}", params
    ).fetchone()["c"]

    rows = conn.execute(
        f"""
        SELECT a.id, a.name, a.sort_name, a.image_url, a.genres,
               a.monitored, a.monitor_new_items, a.quality_profile_id, a.added_at,
               {_ARTIST_STATS}
        FROM lib2_artists a
        {where}
        ORDER BY {order}
        LIMIT :limit OFFSET :offset
        """,
        {**params, "limit": limit, "offset": offset},
    ).fetchall()

    artists = []
    for r in rows:
        track_count = r["track_count"] or 0
        present = r["track_files_present"] or 0
        artists.append({
            "id": r["id"],
            "name": r["name"],
            "image_url": r["image_url"],
            "genres": _json_list(r["genres"]),
            "monitored": bool(r["monitored"]),
            "monitor_new_items": r["monitor_new_items"],
            "quality_profile_id": r["quality_profile_id"],
            "added_at": r["added_at"],
            "album_count": r["album_count"] or 0,
            "single_count": r["single_count"] or 0,
            "track_count": track_count,
            "tracks_present": present,
            "tracks_missing": max(0, track_count - present),
        })
    return artists, total


def get_artist(conn, artist_id: int) -> Optional[Dict[str, Any]]:
    """Artist detail: header + albums and singles grouped separately."""
    a = conn.execute("SELECT * FROM lib2_artists WHERE id = ?", (artist_id,)).fetchone()
    if a is None:
        return None
    qp = conn.execute(
        "SELECT * FROM lib2_quality_profiles WHERE id = ?", (a["quality_profile_id"],)
    ).fetchone()

    album_rows = conn.execute(
        """
        SELECT DISTINCT al.id, al.title, al.album_type, al.release_date, al.year,
               al.image_url, al.monitored, al.quality_profile_id, al.track_count, al.expected_track_count,
               (SELECT COUNT(*) FROM lib2_tracks t WHERE t.album_id = al.id) AS db_track_count,
               (SELECT COUNT(DISTINCT t.id) FROM lib2_tracks t
                  JOIN lib2_track_files tf ON tf.track_id = t.id
                  WHERE t.album_id = al.id) AS files_present
        FROM lib2_album_artists aa
        JOIN lib2_albums al ON al.id = aa.album_id
        WHERE aa.artist_id = ?
        ORDER BY al.year DESC, al.title COLLATE NOCASE
        """,
        (artist_id,),
    ).fetchall()

    albums, singles = [], []
    for r in album_rows:
        present = r["files_present"] or 0
        # Total = the metadata's true track count when known, so partial albums
        # show "have / total" and the missing count is visible (Lidarr-style).
        total = max(r["expected_track_count"] or 0, r["db_track_count"] or 0,
                    r["track_count"] or 0, present)
        entry = {
            "id": r["id"],
            "title": r["title"],
            "album_type": r["album_type"],
            "release_date": r["release_date"],
            "year": r["year"],
            "image_url": r["image_url"],
            "monitored": bool(r["monitored"]),
            "quality_profile_id": r["quality_profile_id"],
            "track_count": total,
            "tracks_present": present,
            "tracks_missing": max(0, total - present),
        }
        (singles if r["album_type"] == "single" else albums).append(entry)

    return {
        "id": a["id"],
        "name": a["name"],
        "image_url": a["image_url"],
        "summary": a["summary"],
        "genres": _json_list(a["genres"]),
        "monitored": bool(a["monitored"]),
        "monitor_new_items": a["monitor_new_items"],
        "quality_profile": _quality_profile_dict(qp),
        "albums": albums,
        "singles": singles,
        "album_count": len(albums),
        "single_count": len(singles),
    }


def _track_artists(conn, track_id: int) -> List[Dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT ar.id, ar.name, ta.role, ta.position
        FROM lib2_track_artists ta
        JOIN lib2_artists ar ON ar.id = ta.artist_id
        WHERE ta.track_id = ?
        ORDER BY ta.position
        """,
        (track_id,),
    ).fetchall()
    return [{"id": r["id"], "name": r["name"], "role": r["role"]} for r in rows]


def _download_provenance_for_path(conn, path: Optional[str], *,
                                  track: Any = None,
                                  album: Optional[Dict[str, Any]] = None,
                                  artists: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
    """Most recent quality/provenance row for a file path, if the old table exists."""
    try:
        row = None
        if path:
            row = conn.execute(
                "SELECT * FROM track_downloads WHERE file_path = ? ORDER BY id DESC LIMIT 1",
                (path,),
            ).fetchone()
            fname = str(path).replace("\\", "/").rsplit("/", 1)[-1]
            if row is None and fname:
                row = conn.execute(
                    "SELECT * FROM track_downloads WHERE file_path LIKE ? OR file_path LIKE ? "
                    "ORDER BY id DESC LIMIT 1",
                    (f"%/{fname}", f"%\\{fname}"),
                ).fetchone()
        if row is not None:
            return dict(row)

        if track is not None:
            for column, value in (
                ("spotify_track_id", track["spotify_id"] if "spotify_id" in track.keys() else None),
                ("musicbrainz_recording_id",
                 track["musicbrainz_id"] if "musicbrainz_id" in track.keys() else None),
                ("isrc", track["isrc"] if "isrc" in track.keys() else None),
            ):
                if not value:
                    continue
                row = conn.execute(
                    f"SELECT * FROM track_downloads WHERE {column} = ? ORDER BY id DESC LIMIT 1",
                    (value,),
                ).fetchone()
                if row is not None:
                    return dict(row)

        title = track["title"] if track is not None and "title" in track.keys() else None
        album_title = album.get("title") if album else None
        artist_names = [a.get("name") for a in (artists or []) if a.get("name")]
        if album and album.get("primary_artist_name"):
            artist_names.append(album["primary_artist_name"])
        unique_artist_names = []
        seen_artists = set()
        for name in artist_names:
            folded = name.casefold()
            if folded not in seen_artists:
                seen_artists.add(folded)
                unique_artist_names.append(name)
        if title:
            candidates: List[List[Tuple[str, Any]]] = []
            for artist_name in unique_artist_names:
                if album_title:
                    candidates.append([
                        ("lower(track_title) = lower(?)", title),
                        ("lower(track_artist) = lower(?)", artist_name),
                        ("lower(track_album) = lower(?)", album_title),
                    ])
                candidates.append([
                    ("lower(track_title) = lower(?)", title),
                    ("lower(track_artist) = lower(?)", artist_name),
                ])
            if album_title:
                candidates.append([
                    ("lower(track_title) = lower(?)", title),
                    ("lower(track_album) = lower(?)", album_title),
                ])
            for candidate in candidates:
                clauses = [part[0] for part in candidate]
                params = [part[1] for part in candidate]
                row = conn.execute(
                    "SELECT * FROM track_downloads WHERE "
                    + " AND ".join(clauses)
                    + " ORDER BY id DESC LIMIT 1",
                    params,
                ).fetchone()
                if row is not None:
                    return dict(row)

        return dict(row) if row else {}
    except Exception:
        return {}


def _first_present(*values: Any) -> Any:
    for value in values:
        if value not in (None, "", 0):
            return value
    return None


def _bitrate_kbps(value: Any) -> Any:
    if value in (None, "", 0):
        return None
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return value
    if numeric > 10000:
        numeric = numeric / 1000
    return int(round(numeric))


def _serialize_track(conn, t, album=None) -> Dict[str, Any]:
    """Build a track dict with linked artists, primary file, and computed status."""
    row = conn.execute(
        "SELECT * FROM lib2_track_files WHERE track_id = ? ORDER BY id LIMIT 1", (t["id"],)
    ).fetchone()
    file_row = dict(row) if row else None
    artists = _track_artists(conn, t["id"])
    track_meta = {
        "title": t["title"],
        "track_number": t["track_number"],
        "disc_number": t["disc_number"],
        "isrc": t["isrc"],
        "album_title": album["title"] if album else None,
        "album_artist_name": album["primary_artist_name"] if album and "primary_artist_name" in album.keys() else None,
        "album_year": album["year"] if album else None,
        "album_image_url": album["image_url"] if album else None,
        "album_genres": album["genres"] if album else None,
    }
    gaps = compute_metadata_gaps(track_meta, file_row, artist_count=len(artists))
    fstat = file_status(file_row, t["canonical_track_id"])
    file_info = None
    if file_row:
        prov = {}
        if not file_row["bitrate"] or not file_row["sample_rate"] or not file_row["bit_depth"]:
            prov = _download_provenance_for_path(
                conn, file_row["path"], track=t, album=album, artists=artists
            )
        bitrate = _bitrate_kbps(_first_present(file_row["bitrate"], prov.get("bitrate")))
        sample_rate = _first_present(file_row["sample_rate"], prov.get("sample_rate"))
        bit_depth = _first_present(file_row["bit_depth"], prov.get("bit_depth"))
        source = _first_present(file_row["source"], prov.get("source_service"))
        file_info = {
            "path": file_row["path"],
            "format": file_row["format"],
            "bitrate": bitrate,
            "sample_rate": sample_rate,
            "bit_depth": bit_depth,
            "size": file_row["size"],
            "quality_tier": quality_tier(file_row["format"], bitrate, bit_depth),
            "import_status": file_row["import_status"],
            "verification_status": file_row["verification_status"],
            "source": source,
        }
    return {
        "id": t["id"],
        "title": t["title"],
        "track_number": t["track_number"],
        "disc_number": t["disc_number"],
        "duration": t["duration"],
        "isrc": t["isrc"],
        "monitored": bool(t["monitored"]),
        "quality_profile_id": t["quality_profile_id"],
        "canonical_track_id": t["canonical_track_id"],
        "artists": artists,
        "file": file_info,
        "file_status": fstat,
        "metadata_gaps": gaps,
    }


def _missing_track_placeholder(track_number: int, *, disc_number: int = 1,
                               album=None, title: Optional[str] = None) -> Dict[str, Any]:
    """Expected-but-not-owned track row, mirroring Lidarr's missing rows."""
    artists = []
    if album and album.get("primary_artist_id") and album.get("primary_artist_name"):
        artists.append({
            "id": album["primary_artist_id"],
            "name": album["primary_artist_name"],
            "role": "primary",
        })
    return {
        "id": None,
        "title": title,
        "track_number": track_number,
        "disc_number": disc_number,
        "duration": None,
        "isrc": None,
        "monitored": bool(album["monitored"]) if album and "monitored" in album else False,
        "quality_profile_id": album["quality_profile_id"] if album and "quality_profile_id" in album else None,
        "canonical_track_id": None,
        "artists": artists,
        "file": None,
        "file_status": "missing",
        "metadata_gaps": [],
        "is_missing": True,
    }


def get_album(conn, album_id: int) -> Optional[Dict[str, Any]]:
    """Album/single detail: header + track table with per-track status."""
    al = conn.execute("SELECT * FROM lib2_albums WHERE id = ?", (album_id,)).fetchone()
    if al is None:
        return None
    qp = conn.execute(
        "SELECT * FROM lib2_quality_profiles WHERE id = ?", (al["quality_profile_id"],)
    ).fetchone()
    artist = conn.execute(
        "SELECT id, name FROM lib2_artists WHERE id = ?", (al["primary_artist_id"],)
    ).fetchone()
    track_rows = conn.execute(
        "SELECT * FROM lib2_tracks WHERE album_id = ? ORDER BY disc_number, track_number, id",
        (album_id,),
    ).fetchall()
    album_for_tracks = dict(al)
    if artist:
        album_for_tracks["primary_artist_name"] = artist["name"]
        album_for_tracks["primary_artist_id"] = artist["id"]
    tracks = [_serialize_track(conn, t, album_for_tracks) for t in track_rows]
    present_count = sum(1 for t in tracks if t["file"] is not None)

    # Evaluate each present file against the album's quality profile (meets /
    # upgrade-available), reusing core/quality. Missing rows stay neutral.
    from core.library2.quality_eval import evaluate_file, profile_targets
    targets, upgrade_policy = profile_targets(dict(qp) if qp else None)
    upgrades_available = 0
    for t in tracks:
        if t.get("file"):
            ev = evaluate_file(t["file"], targets, upgrade_policy)
            t["meets_profile"] = ev["meets_profile"]
            t["upgrade_candidate"] = ev["upgrade_candidate"]
            if ev["upgrade_candidate"]:
                upgrades_available += 1
        else:
            t["meets_profile"] = None
            t["upgrade_candidate"] = False

    # Lidarr keeps expected missing recordings visible in the track table. When
    # we only know the album's expected size, expose those slots as missing rows
    # without pretending we know their title or tag gaps.
    expected = al["expected_track_count"] or 0
    known_count = len(tracks)
    total = max(expected, known_count, present_count)
    known_numbers = {
        (t.get("disc_number") or 1, t.get("track_number"))
        for t in tracks
        if t.get("track_number") is not None
    }
    # Titles for the missing slots, when we've cached the album's canonical
    # tracklist from a metadata provider (see core/library2/completeness.py).
    missing_titles: Dict[int, str] = {}
    try:
        tl_raw = al["tracklist_json"] if "tracklist_json" in al.keys() else None
        for entry in (json.loads(tl_raw) if tl_raw else []):
            num = entry.get("track_number")
            if num and entry.get("title"):
                missing_titles[int(num)] = entry["title"]
    except (ValueError, TypeError):
        pass
    if total > known_count:
        for number in range(1, total + 1):
            key = (1, number)
            if key not in known_numbers:
                tracks.append(_missing_track_placeholder(
                    number, album=album_for_tracks, title=missing_titles.get(number)))
    tracks.sort(key=lambda t: (t.get("disc_number") or 1, t.get("track_number") or 0,
                              t.get("id") or 0))

    return {
        "id": al["id"],
        "title": al["title"],
        "album_type": al["album_type"],
        "release_date": al["release_date"],
        "year": al["year"],
        "image_url": al["image_url"],
        "genres": _json_list(al["genres"]),
        "monitored": bool(al["monitored"]),
        "quality_profile": _quality_profile_dict(qp),
        "primary_artist": {"id": artist["id"], "name": artist["name"]} if artist else None,
        "tracks": tracks,
        "track_count": total,
        "tracks_present": present_count,
        "tracks_missing": max(0, total - present_count),
        "upgrades_available": upgrades_available,
    }


def get_track(conn, track_id: int) -> Optional[Dict[str, Any]]:
    """Single-track detail incl. linked album + artists + file + status."""
    t = conn.execute("SELECT * FROM lib2_tracks WHERE id = ?", (track_id,)).fetchone()
    if t is None:
        return None
    album = conn.execute("SELECT * FROM lib2_albums WHERE id = ?", (t["album_id"],)).fetchone()
    data = _serialize_track(conn, t, album)
    data["album"] = {
        "id": album["id"], "title": album["title"], "album_type": album["album_type"],
    } if album else None
    return data


def list_quality_profiles(conn) -> List[Dict[str, Any]]:
    rows = conn.execute(
        "SELECT * FROM lib2_quality_profiles ORDER BY is_default DESC, id"
    ).fetchall()
    return [_quality_profile_dict(row) for row in rows if row is not None]


__all__ = ["list_artists", "get_artist", "get_album", "get_track", "list_quality_profiles"]
