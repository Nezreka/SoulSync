"""Auto-link freshly imported downloads into the Library v2 tables.

Called from the post-processing side effects (``core/imports/side_effects.py``)
once a download has its final processed path. Best-effort and strictly additive:
it never raises into the pipeline and does nothing unless ``features.library_v2``
is enabled.

This closes the wanted-loop: monitor a discography release → tracks mirror into
the wishlist → the download pipeline fetches a file → the file appears in
Library v2 immediately (no full re-import needed).

Matching prefers existing rows (including fileless rows materialized from a
provider tracklist — attaching a file to one flips it from "missing" to
"present") and only creates artist/album/track rows when genuinely new.
"""

from __future__ import annotations

import os
from typing import Any, Dict, Optional

from utils.logging_config import get_logger

from .importer import normalize_name

logger = get_logger("library2.autolink")


def _get(ti: Dict[str, Any], *keys: str) -> str:
    for key in keys:
        val = ti.get(key)
        if isinstance(val, dict):
            val = val.get("name")
        if val:
            return str(val)
    return ""


def _primary_artist_name(ti: Dict[str, Any]) -> str:
    artists = ti.get("artists")
    if isinstance(artists, list) and artists:
        first = artists[0]
        if isinstance(first, dict) and first.get("name"):
            return str(first["name"])
        if isinstance(first, str) and first:
            return first
    return _get(ti, "artist")


def _find_or_create_artist(conn, name: str) -> Optional[int]:
    key = normalize_name(name)
    if not key:
        return None
    # Fast path: SQL case-insensitive match covers almost every real name and
    # avoids scanning the whole artist table per finished download.
    row = conn.execute(
        "SELECT id FROM lib2_artists WHERE lower(name) = ? LIMIT 1", (key,)
    ).fetchone()
    if row:
        return row["id"]
    # Slow path: python normalization also collapses whitespace/casefolds.
    for row in conn.execute("SELECT id, name FROM lib2_artists"):
        if normalize_name(row["name"]) == key:
            return row["id"]
    from core.library2.profile_lookup import default_quality_profile_id
    cur = conn.execute(
        "INSERT INTO lib2_artists(name, sort_name, quality_profile_id) VALUES(?, ?, ?)",
        (name, name, default_quality_profile_id(conn)))
    return cur.lastrowid


def _find_or_create_album(conn, artist_id: int, title: str, *,
                          album_type: str, spotify_album_id: Optional[str]) -> int:
    key = normalize_name(title)
    rows = conn.execute(
        """SELECT al.id, al.title, al.spotify_id FROM lib2_album_artists aa
           JOIN lib2_albums al ON al.id = aa.album_id WHERE aa.artist_id=?""",
        (artist_id,),
    ).fetchall()
    for row in rows:
        if spotify_album_id and row["spotify_id"] == spotify_album_id:
            return row["id"]
    for row in rows:
        if normalize_name(row["title"]) == key:
            return row["id"]
    # New albums inherit the artist's quality-profile assignment (cascade),
    # mirroring what the explicit assign endpoint does.
    from core.library2.profile_lookup import default_quality_profile_id
    artist_profile = conn.execute(
        "SELECT quality_profile_id FROM lib2_artists WHERE id=?", (artist_id,)
    ).fetchone()
    profile_id = ((artist_profile["quality_profile_id"] if artist_profile else None)
                  or default_quality_profile_id(conn))
    cur = conn.execute(
        """INSERT INTO lib2_albums(primary_artist_id, title, album_type, spotify_id,
               quality_profile_id)
           VALUES(?,?,?,?,?)""",
        (artist_id, title, album_type, spotify_album_id, profile_id),
    )
    album_id = cur.lastrowid
    conn.execute(
        "INSERT OR IGNORE INTO lib2_album_artists(album_id, artist_id, role) "
        "VALUES(?,?, 'primary')", (album_id, artist_id))
    return album_id


def _find_or_create_track(conn, album_id: int, artist_id: int, title: str, *,
                          track_number: Optional[int],
                          spotify_track_id: Optional[str]) -> int:
    key = normalize_name(title)
    rows = conn.execute(
        "SELECT id, title, track_number, spotify_id FROM lib2_tracks WHERE album_id=?",
        (album_id,),
    ).fetchall()
    for row in rows:
        if spotify_track_id and row["spotify_id"] == spotify_track_id:
            return row["id"]
    for row in rows:
        if normalize_name(row["title"]) == key:
            return row["id"]
    from core.library2.profile_lookup import default_quality_profile_id
    album_profile = conn.execute(
        "SELECT quality_profile_id FROM lib2_albums WHERE id=?", (album_id,)
    ).fetchone()
    profile_id = ((album_profile["quality_profile_id"] if album_profile else None)
                  or default_quality_profile_id(conn))
    cur = conn.execute(
        """INSERT INTO lib2_tracks(album_id, title, track_number, spotify_id,
               quality_profile_id)
           VALUES(?,?,?,?,?)""",
        (album_id, title, track_number, spotify_track_id, profile_id),
    )
    track_id = cur.lastrowid
    conn.execute(
        "INSERT OR IGNORE INTO lib2_track_artists(track_id, artist_id, role, position) "
        "VALUES(?,?, 'primary', 0)", (track_id, artist_id))
    return track_id


def link_download_into_library_v2(context: Dict[str, Any]) -> Optional[int]:
    """Link a finished download's file into ``lib2_*``. Returns the file-row id.

    Safe to call unconditionally from the pipeline: gated on the feature flag,
    never raises, and idempotent (an existing path on the same track is updated,
    not duplicated).
    """
    try:
        from config.settings import config_manager
        if config_manager.get("features.library_v2", False) is not True:
            return None

        file_path = context.get("_final_processed_path") or context.get("_final_path")
        if not file_path:
            return None

        # A grab that started from Library v2 carries the server-resolved
        # entity (audit P1-16) — the file links to that exact row, no
        # heuristic re-matching. Without it, fall back to name matching.
        ti = context.get("track_info") or context.get("search_result") or {}
        lib2_ctx = context.get("lib2_entity") or ti.get("lib2_entity") or {}
        direct_track_id = lib2_ctx.get("track_id")
        direct_album_id = lib2_ctx.get("album_id")

        title = _get(ti, "name", "title")
        artist_name = _primary_artist_name(ti)
        if not direct_track_id and not direct_album_id and (not title or not artist_name):
            return None
        album_name = _get(ti, "album") or title

        embedded = context.get("_embedded_id_tags") or {}
        spotify_track_id = str(embedded.get("SPOTIFY_TRACK_ID") or "") or (
            str(ti.get("id")) if ti.get("provider") == "spotify" and ti.get("id") else None)
        album_raw = ti.get("album") if isinstance(ti.get("album"), dict) else {}
        spotify_album_id = str(album_raw.get("id") or "") or None
        total_tracks = album_raw.get("total_tracks")
        album_type = str(album_raw.get("album_type") or "").lower() or (
            "single" if (normalize_name(album_name) == normalize_name(title)
                         or total_tracks in (1, "1")) else "album")
        track_number = ti.get("track_number")
        try:
            track_number = int(track_number) if track_number else None
        except (TypeError, ValueError):
            track_number = None

        from database.music_database import get_database
        db = get_database()
        conn = db._get_connection()
        try:
            track_id = album_id = None
            if direct_track_id:
                row = conn.execute(
                    "SELECT id, album_id FROM lib2_tracks WHERE id=?",
                    (direct_track_id,)).fetchone()
                if row:
                    track_id, album_id = row["id"], row["album_id"]
            elif direct_album_id:
                row = conn.execute(
                    "SELECT id, primary_artist_id FROM lib2_albums WHERE id=?",
                    (direct_album_id,)).fetchone()
                if row and title:
                    album_id = row["id"]
                    track_id = _find_or_create_track(
                        conn, album_id, row["primary_artist_id"], title,
                        track_number=track_number, spotify_track_id=spotify_track_id)
            if track_id is None:
                # Entity gone or absent — heuristic name matching as before.
                if not title or not artist_name:
                    return None
                artist_id = _find_or_create_artist(conn, artist_name)
                if artist_id is None:
                    return None
                album_id = _find_or_create_album(
                    conn, artist_id, album_name,
                    album_type=album_type, spotify_album_id=spotify_album_id)
                track_id = _find_or_create_track(
                    conn, album_id, artist_id, title,
                    track_number=track_number, spotify_track_id=spotify_track_id)

            fmt = file_path.rsplit(".", 1)[-1].lower() if "." in file_path else None
            bitrate = sample_rate = bit_depth = None
            tier = None
            try:
                from core.imports.file_ops import probe_audio_quality
                from core.library2.status import quality_tier
                quality = probe_audio_quality(file_path)
                if quality:
                    fmt = quality.format or fmt
                    bitrate = quality.bitrate
                    sample_rate = quality.sample_rate
                    bit_depth = quality.bit_depth
                    tier = quality_tier(fmt, bitrate, bit_depth)
            except Exception as e:  # noqa: BLE001
                logger.debug("autolink quality probe failed (%s): %s", file_path, e)
            try:
                size = os.path.getsize(file_path)
            except OSError:
                size = None
            source = str(context.get("username") or "") or None

            existing = conn.execute(
                "SELECT id FROM lib2_track_files WHERE track_id=? AND path=?",
                (track_id, file_path),
            ).fetchone()
            if existing:
                conn.execute(
                    """UPDATE lib2_track_files SET size=COALESCE(?, size),
                           bitrate=COALESCE(?, bitrate), sample_rate=COALESCE(?, sample_rate),
                           bit_depth=COALESCE(?, bit_depth), format=COALESCE(?, format),
                           quality_tier=COALESCE(?, quality_tier),
                           updated_at=CURRENT_TIMESTAMP WHERE id=?""",
                    (size, bitrate, sample_rate, bit_depth, fmt, tier, existing["id"]),
                )
                file_id = existing["id"]
            else:
                cur = conn.execute(
                    """INSERT INTO lib2_track_files(track_id, path, size, bitrate,
                           sample_rate, bit_depth, format, quality_tier, source,
                           import_status)
                       VALUES(?,?,?,?,?,?,?,?,?, 'imported')""",
                    (track_id, file_path, size, bitrate, sample_rate, bit_depth,
                     fmt, tier, source),
                )
                file_id = cur.lastrowid
            # The album now owns a real file — a provider-only discography row
            # must graduate to the library, or "My Library" (which filters on
            # origin/monitored) would hide an album whose file exists.
            conn.execute(
                "UPDATE lib2_albums SET origin='library', updated_at=CURRENT_TIMESTAMP "
                "WHERE id=? AND origin='discography'", (album_id,))
            # Heuristic auto-link can create a catalog track outside importer/
            # tracklist flows; materialize its wanted state before commit so
            # projection consumers never silently miss the new row.
            from core.library2.wanted import recompute_wanted
            recompute_wanted(conn, track_ids=[track_id])
            conn.commit()
            logger.info("Library v2 auto-linked download: %s → track %s (file %s)",
                        os.path.basename(str(file_path)), track_id, file_id)
            return file_id
        finally:
            conn.close()
    except Exception as e:  # noqa: BLE001
        logger.debug("library v2 autolink failed: %s", e)
        return None


__all__ = ["link_download_into_library_v2"]
