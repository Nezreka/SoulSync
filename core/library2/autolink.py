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

import json
import os
from typing import Any, Dict, Optional

from utils.logging_config import get_logger

from .importer import (
    dedup_title_key,
    looks_like_foreign_provider_id,
    normalize_name,
    release_title_key,
)

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


def _primary_artist_provider_id(ti: Dict[str, Any]) -> Optional[str]:
    # `artists[0]["id"]` is populated by every client with its provider-local
    # id. §62.4: the namespace decision (spotify column vs. external_ids vs.
    # match-only) lives in `_provider_namespace`, driven by ti["provider"] and
    # the id's shape — non-Spotify ids never reach the spotify_id column.
    artists = ti.get("artists")
    if isinstance(artists, list) and artists:
        first = artists[0]
        if isinstance(first, dict) and first.get("id"):
            return str(first["id"])
    return None


def _provider_namespace(provider_id: Optional[str],
                        source: Optional[str]) -> Optional[str]:
    """Which external-id namespace an incoming id belongs to.

    - a non-Spotify provider marker is authoritative for its own id;
    - an unmarked/'spotify' id counts as Spotify UNLESS its shape rules that
      out (numeric = Deezer/iTunes, UUID = MusicBrainz — §62.4's poison) —
      then it is used for matching only (``None``), never persisted into a
      namespace it may not belong to.
    """
    if not provider_id:
        return None
    src = str(source or "").strip().lower() or None
    if src and src != "spotify":
        return src
    if looks_like_foreign_provider_id(provider_id):
        return None
    return "spotify"


def _row_external_ids(raw: Any) -> Dict[str, str]:
    try:
        value = json.loads(raw or "{}")
    except (TypeError, ValueError):
        return {}
    if not isinstance(value, dict):
        return {}
    return {
        str(source).strip().lower(): str(pid).strip()
        for source, pid in value.items()
        if str(source).strip() and str(pid).strip()
    }


def _adopt_external_id(conn, table: str, row_id: int, namespace: str,
                       provider_id: str) -> None:
    """setdefault-style: record the id under its namespace, never overwrite."""
    row = conn.execute(
        f"SELECT external_ids FROM {table} WHERE id=?", (row_id,)).fetchone()
    if row is None:
        return
    ids = _row_external_ids(row["external_ids"])
    if ids.get(namespace):
        return
    ids[namespace] = provider_id
    conn.execute(
        f"UPDATE {table} SET external_ids=?, updated_at=CURRENT_TIMESTAMP WHERE id=?",
        (json.dumps(ids, sort_keys=True, separators=(",", ":")), row_id))


def _find_or_create_artist(conn, name: str, *, spotify_id: Optional[str] = None,
                           source: Optional[str] = None) -> Optional[int]:
    # ID match first: cheap (indexed) and — unlike name matching — survives
    # name-spelling variants of the same provider identity (e.g. a kanji vs.
    # romaji release credit), the case G8's alias-awareness gap calls out.
    provider_id = str(spotify_id).strip() if spotify_id else None
    namespace = _provider_namespace(provider_id, source)
    if provider_id:
        if namespace == "spotify":
            row = conn.execute(
                "SELECT id FROM lib2_artists WHERE spotify_id = ? LIMIT 1",
                (provider_id,)).fetchone()
            if row:
                return row["id"]
        else:
            # Value match for non-Spotify/unmarked ids: the proper namespace
            # in external_ids, plus (compat, §62.4) rows whose spotify_id
            # column was poisoned with this very id before the shape guard.
            for candidate in conn.execute(
                    "SELECT id, spotify_id, external_ids FROM lib2_artists "
                    "WHERE spotify_id = ? OR external_ids LIKE ?",
                    (provider_id, f"%{provider_id}%")):
                if candidate["spotify_id"] == provider_id:
                    return candidate["id"]
                ids = _row_external_ids(candidate["external_ids"])
                if namespace is not None and ids.get(namespace) == provider_id:
                    return candidate["id"]
                if namespace is None and provider_id in ids.values():
                    return candidate["id"]

    key = normalize_name(name)
    if not key:
        return None
    # Fast path: SQL case-insensitive match covers almost every real name and
    # avoids scanning the whole artist table per finished download.
    row = conn.execute(
        "SELECT id FROM lib2_artists WHERE lower(name) = ? LIMIT 1", (key,)
    ).fetchone()
    if row is None:
        # Slow path: python normalization also collapses whitespace/casefolds
        # (SQLite's lower() is ASCII-only, so this also covers non-ASCII names
        # the fast path's lower() comparison misses).
        for candidate in conn.execute("SELECT id, name FROM lib2_artists"):
            if normalize_name(candidate["name"]) == key:
                row = candidate
                break
    if row is not None:
        if namespace == "spotify":
            # Backfill so the next finished download for this artist can take
            # the indexed ID path above instead of falling through to here.
            conn.execute(
                "UPDATE lib2_artists SET spotify_id=?, updated_at=CURRENT_TIMESTAMP "
                "WHERE id=? AND spotify_id IS NULL", (provider_id, row["id"]))
        elif namespace is not None:
            _adopt_external_id(conn, "lib2_artists", row["id"], namespace, provider_id)
        return row["id"]

    from core.library2.profile_lookup import default_quality_profile_id
    from core.library2.monitor_sync import artist_is_watchlisted
    external_json = (json.dumps({namespace: provider_id})
                     if namespace not in (None, "spotify") else "{}")
    provider_ids = {namespace: provider_id} if namespace and provider_id else {}
    monitored = int(artist_is_watchlisted(conn, name, provider_ids, profile_id=1))
    cur = conn.execute(
        "INSERT INTO lib2_artists(name, sort_name, spotify_id, external_ids, "
        "quality_profile_id, monitored) VALUES(?, ?, ?, ?, ?, ?)",
        (name, name, provider_id if namespace == "spotify" else None,
         external_json, default_quality_profile_id(conn), monitored))
    return cur.lastrowid


def _find_or_create_album(conn, artist_id: int, title: str, *,
                          album_type: str, spotify_album_id: Optional[str] = None,
                          source: Optional[str] = None) -> int:
    provider_id = str(spotify_album_id).strip() if spotify_album_id else None
    namespace = _provider_namespace(provider_id, source)
    key = release_title_key(title)
    rows = conn.execute(
        """SELECT al.id, al.title, al.spotify_id, al.external_ids
           FROM lib2_album_artists aa
           JOIN lib2_albums al ON al.id = aa.album_id WHERE aa.artist_id=?""",
        (artist_id,),
    ).fetchall()
    if provider_id:
        for row in rows:
            # spotify_id equality doubles as the §62.4 compat path for rows
            # whose column was poisoned with a non-Spotify id earlier.
            if row["spotify_id"] == provider_id:
                return row["id"]
            ids = _row_external_ids(row["external_ids"])
            if namespace is not None and ids.get(namespace) == provider_id:
                return row["id"]
            if namespace is None and provider_id in ids.values():
                return row["id"]
    for row in rows:
        if release_title_key(row["title"]) == key:
            if namespace is not None and namespace != "spotify" and provider_id:
                _adopt_external_id(conn, "lib2_albums", row["id"], namespace,
                                   provider_id)
            return row["id"]
    # New albums inherit the artist's quality-profile assignment (cascade),
    # mirroring what the explicit assign endpoint does.
    from core.library2.profile_lookup import default_quality_profile_id
    artist_profile = conn.execute(
        "SELECT quality_profile_id FROM lib2_artists WHERE id=?", (artist_id,)
    ).fetchone()
    profile_id = ((artist_profile["quality_profile_id"] if artist_profile else None)
                  or default_quality_profile_id(conn))
    external_json = (json.dumps({namespace: provider_id})
                     if namespace not in (None, "spotify") else "{}")
    cur = conn.execute(
        """INSERT INTO lib2_albums(primary_artist_id, title, album_type, spotify_id,
               external_ids, quality_profile_id)
           VALUES(?,?,?,?,?,?)""",
        (artist_id, title, album_type,
         provider_id if namespace == "spotify" else None,
         external_json, profile_id),
    )
    album_id = cur.lastrowid
    conn.execute(
        "INSERT OR IGNORE INTO lib2_album_artists(album_id, artist_id, role) "
        "VALUES(?,?, 'primary')", (album_id, artist_id))
    return album_id


def _find_or_create_track(conn, album_id: int, artist_id: int, title: str, *,
                          track_number: Optional[int],
                          spotify_track_id: Optional[str] = None,
                          disc_number: Optional[int] = None,
                          source: Optional[str] = None) -> int:
    provider_id = str(spotify_track_id).strip() if spotify_track_id else None
    namespace = _provider_namespace(provider_id, source)
    key = dedup_title_key(title)
    rows = conn.execute(
        "SELECT id, title, track_number, disc_number, spotify_id, external_ids "
        "FROM lib2_tracks WHERE album_id=?",
        (album_id,),
    ).fetchall()
    for row in rows:
        if not provider_id:
            continue
        if namespace == "spotify" and row["spotify_id"] == provider_id:
            return row["id"]
        ids = _row_external_ids(row["external_ids"])
        if namespace is not None and ids.get(namespace) == provider_id:
            return row["id"]
        if namespace is None and provider_id in ids.values():
            return row["id"]
    for row in rows:
        # dedup_title_key (§39) drops feat.-annotations so a finished
        # download's title matches a fileless wanted-row that spells the
        # credit differently — without it, a bare exact-title match misses
        # this (the most common real-world case) and creates a duplicate
        # track row whose wanted-row keeps re-downloading forever (G4).
        if dedup_title_key(row["title"]) == key:
            if namespace == "spotify" and provider_id and not row["spotify_id"]:
                conn.execute(
                    "UPDATE lib2_tracks SET spotify_id=?, updated_at=CURRENT_TIMESTAMP "
                    "WHERE id=?", (provider_id, row["id"]),
                )
            elif namespace not in (None, "spotify") and provider_id:
                _adopt_external_id(
                    conn, "lib2_tracks", row["id"], namespace, provider_id,
                )
            return row["id"]
    if track_number is not None:
        wanted_disc = disc_number if disc_number is not None else 1
        for row in rows:
            row_disc = row["disc_number"] if row["disc_number"] is not None else 1
            if row["track_number"] == track_number and row_disc == wanted_disc:
                return row["id"]
    from core.library2.profile_lookup import default_quality_profile_id
    album_profile = conn.execute(
        "SELECT quality_profile_id FROM lib2_albums WHERE id=?", (album_id,)
    ).fetchone()
    profile_id = ((album_profile["quality_profile_id"] if album_profile else None)
                  or default_quality_profile_id(conn))
    cur = conn.execute(
        """INSERT INTO lib2_tracks(album_id, title, track_number, spotify_id,
               external_ids, quality_profile_id)
           VALUES(?,?,?,?,?,?)""",
        (
            album_id,
            title,
            track_number,
            provider_id if namespace == "spotify" else None,
            json.dumps({namespace: provider_id})
            if namespace not in (None, "spotify") else "{}",
            profile_id,
        ),
    )
    track_id = cur.lastrowid
    conn.execute(
        "INSERT OR IGNORE INTO lib2_track_artists(track_id, artist_id, role, position) "
        "VALUES(?,?, 'primary', 0)", (track_id, artist_id))
    return track_id


# Public aliases for reuse outside this module (§52.8 early materialization,
# core/library2/materialize.py) — same resolve-or-create semantics the
# post-download autolink path above already relies on.
find_or_create_artist = _find_or_create_artist
find_or_create_album = _find_or_create_album
find_or_create_track = _find_or_create_track


def _acoustid_status_for(raw: Any) -> Optional[str]:
    """Map the pipeline's raw AcoustID outcome to the schema's narrower
    ``acoustid_status`` vocabulary. 'disabled'/'error'/unset make no claim
    either way (None) — a hard FAIL never reaches here: it quarantines the
    file and returns before this callback runs."""
    return raw if raw in ("pass", "skip") else None


def _pipeline_result_json(context: Dict[str, Any]) -> str:
    """Deep-dive A7/C4: compact per-file detail that has no dedicated column
    — the AcoustID reason and whether a quality-profile fallback (downsample /
    lossy copy) fired for this file. Built from context keys the pipeline
    already sets; empty when none apply so most rows stay `'{}'`."""
    result: Dict[str, Any] = {}
    message = context.get("_acoustid_message")
    if message:
        result["acoustid_message"] = str(message)
    version = context.get("_version_mismatch_fallback")
    if version:
        result["version_mismatch_fallback"] = str(version)
    fallbacks = [
        name for name, key in (
            ("downsample", "_quality_fallback_downsample"),
            ("lossy_copy", "_quality_fallback_lossy_copy"),
        ) if context.get(key)
    ]
    if fallbacks:
        result["quality_fallback"] = fallbacks
    return json.dumps(result) if result else "{}"


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
        # heuristic re-matching. Scheduled Wishlist downloads carry the same
        # ids in source_info; that object survives into this pipeline context.
        ti = context.get("track_info") or context.get("search_result") or {}
        lib2_ctx = context.get("lib2_entity") or ti.get("lib2_entity") or {}
        if not isinstance(lib2_ctx, dict):
            lib2_ctx = {}
        from core.downloads.origin import _parse_source_info
        source_info = _parse_source_info(ti.get("source_info"))
        direct_track_id = lib2_ctx.get("track_id") or source_info.get("lib2_track_id")
        direct_album_id = lib2_ctx.get("album_id") or source_info.get("lib2_album_id")

        title = _get(ti, "name", "title")
        artist_name = _primary_artist_name(ti)
        if not direct_track_id and not direct_album_id and (not title or not artist_name):
            return None
        album_name = _get(ti, "album") or title

        embedded = context.get("_embedded_id_tags") or {}
        embedded_spotify_id = str(embedded.get("SPOTIFY_TRACK_ID") or "") or None
        spotify_track_id = embedded_spotify_id or (
            str(ti.get("id")) if ti.get("id") else None
        )
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
        disc_number = ti.get("disc_number")
        try:
            disc_number = int(disc_number) if disc_number else None
        except (TypeError, ValueError):
            disc_number = None

        from database.music_database import get_database
        db = get_database()
        conn = db._get_connection()
        try:
            track_id = album_id = None
            ti_provider = str(ti.get("provider") or "").strip().lower() or None
            track_identity_source = "spotify" if embedded_spotify_id else ti_provider
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
                        track_number=track_number, spotify_track_id=spotify_track_id,
                        disc_number=disc_number, source=track_identity_source)
            if track_id is None:
                # Entity gone or absent — heuristic name matching as before.
                if not title or not artist_name:
                    return None
                artist_id = _find_or_create_artist(
                    conn, artist_name, spotify_id=_primary_artist_provider_id(ti),
                    source=ti_provider)
                if artist_id is None:
                    return None
                album_id = _find_or_create_album(
                    conn, artist_id, album_name,
                    album_type=album_type, spotify_album_id=spotify_album_id,
                    source=ti_provider)
                track_id = _find_or_create_track(
                    conn, album_id, artist_id, title,
                    track_number=track_number, spotify_track_id=spotify_track_id,
                    disc_number=disc_number, source=track_identity_source)

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
            # Deep-dive A7/C4: the ONE callback every finished download (grabbed
            # via wishlist, manual search, or watchlist) passes through — the
            # verification badge and AcoustID/quality-fallback detail were
            # computed upstream this same pipeline run but never made it onto
            # the file row for autolink-created files, leaving the Info-tab
            # lifecycle UI permanently empty for "the normal case today".
            verification_status = context.get("_verification_status")
            acoustid_status = _acoustid_status_for(context.get("_acoustid_result"))
            pipeline_result_json = _pipeline_result_json(context)

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
                           verification_status=COALESCE(?, verification_status),
                           acoustid_status=COALESCE(?, acoustid_status),
                           pipeline_result_json=?,
                           updated_at=CURRENT_TIMESTAMP WHERE id=?""",
                    (size, bitrate, sample_rate, bit_depth, fmt, tier,
                     verification_status, acoustid_status, pipeline_result_json,
                     existing["id"]),
                )
                file_id = existing["id"]
            else:
                cur = conn.execute(
                    """INSERT INTO lib2_track_files(track_id, path, size, bitrate,
                           sample_rate, bit_depth, format, quality_tier, source,
                           verification_status, acoustid_status, pipeline_result_json,
                           import_status)
                       VALUES(?,?,?,?,?,?,?,?,?,?,?,?, 'imported')""",
                    (track_id, file_path, size, bitrate, sample_rate, bit_depth,
                     fmt, tier, source,
                     verification_status, acoustid_status, pipeline_result_json),
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
            # projection consumers never silently miss the new row. No
            # request context exists in this pipeline callback, so resolve
            # the live default profile (G8) instead of hardcoding 1 (§1
            # invariant) — same lookup already used above for new rows.
            from core.library2.profile_lookup import default_quality_profile_id
            from core.library2.wanted import recompute_wanted
            recompute_wanted(conn, profile_id=default_quality_profile_id(conn),
                             track_ids=[track_id])
            conn.commit()
            logger.info("Library v2 auto-linked download: %s → track %s (file %s)",
                        os.path.basename(str(file_path)), track_id, file_id)
            return file_id
        finally:
            conn.close()
    except Exception as e:  # noqa: BLE001
        logger.debug("library v2 autolink failed: %s", e)
        return None


__all__ = [
    "find_or_create_album",
    "find_or_create_artist",
    "find_or_create_track",
    "link_download_into_library_v2",
]
