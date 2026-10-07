"""Import post-processing side effects that do not need web runtime state."""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from typing import Any, Dict

from core.settings import config_manager
from core.imports.context import (
    extract_artist_name,
    get_import_clean_album,
    get_import_clean_artist,
    get_import_clean_title,
    get_import_context_album,
    get_import_context_artist,
    get_import_original_search,
    get_import_search_result,
    get_import_source,
    get_import_source_ids,
    get_import_track_info,
    normalize_import_context,
    get_library_source_id_columns,
)
from database.music_database import get_database
from utils.logging_config import get_logger


logger = get_logger("imports.side_effects")


def _get_config_manager():
    return config_manager


def _primary_track_artist_name(track_info: Dict[str, Any]) -> str:
    artists = (track_info or {}).get("artists", [])
    if isinstance(artists, list) and artists:
        first = artists[0]
        if isinstance(first, dict):
            return str(first.get("name", "") or "")
        return str(first or "")
    if isinstance(artists, str):
        return artists
    return str((track_info or {}).get("artist", "") or "")


def _recording_identity(context: Dict[str, Any]) -> tuple[str | None, str | None]:
    """Return a recording MBID and its comment only when they belong together."""
    track_info = get_import_track_info(context) or get_import_search_result(context)
    source_ids = get_import_source_ids(context)
    source_mbid = source_ids["track_id"] if get_import_source(context).lower() == "musicbrainz" else ""
    embedded_mbid = (context.get("_embedded_id_tags") or {}).get("MUSICBRAINZ_RECORDING_ID")
    candidate = embedded_mbid or track_info.get("musicbrainz_recording_id") or source_mbid
    track_mbid = str(candidate or "").strip().lower() or None
    if not track_mbid:
        return None, None

    disambiguation = context.get("_recording_disambiguation")
    if disambiguation is None and str(
        track_info.get("musicbrainz_recording_id") or source_mbid or ""
    ).strip().lower() == track_mbid:
        disambiguation = track_info.get("disambiguation")
    return track_mbid, str(disambiguation or "").strip() or None


def _stable_soulsync_id(text: str) -> str:
    return str(abs(int(hashlib.md5(text.encode("utf-8", errors="replace")).hexdigest(), 16)) % (10 ** 9))


def _retention_provenance_json(context: Dict[str, Any]) -> tuple[str | None, str | None]:
    """Serialize acquisition/retention truth for either persistence path."""
    from core.quality.model import AudioQuality
    from core.quality.retention import quality_json, transforms_json

    acquired_value = context.get("_acquired_audio_quality")
    try:
        acquired_quality = AudioQuality.from_dict(acquired_value) if acquired_value else None
    except (TypeError, ValueError):
        acquired_quality = None
    return (
        quality_json(acquired_quality),
        transforms_json(context.get("_retention_transforms")),
    )


# Tiny SQL allowlist for the fill-empty helpers — prevents accidental
# SQL injection through the f-string column-name interpolation. Only
# columns the soulsync library write path ever updates are listed.
_SOULSYNC_FILLABLE_COLUMNS = {
    "artists": frozenset({"thumb_url", "genres", "summary", "spotify_artist_id",
                          "itunes_artist_id", "deezer_id", "discogs_id", "soul_id",
                          "hifi_artist_id"}),
    "albums": frozenset({"thumb_url", "genres", "year", "track_count", "duration",
                         "record_type",
                         "spotify_album_id", "itunes_album_id", "deezer_id",
                         "discogs_id", "soul_id", "hifi_album_id"}),
}


def _fill_empty_mb_release_id(cursor, album_id: Any, release_id: str) -> None:
    """Record the musicbrainz release an imported album is, when the row has none.

    marked matched the way the embedded-id reconcile does, so the enrichment
    worker doesn't go looking and land on a same-named sibling release (#1299).
    never overwrites an id that's already there.
    """
    try:
        cursor.execute(
            "UPDATE albums SET musicbrainz_release_id = ?, musicbrainz_match_status = 'matched' "
            "WHERE id = ? AND (musicbrainz_release_id IS NULL OR musicbrainz_release_id = '')",
            (release_id, album_id),
        )
    except Exception as e:
        logger.debug("album musicbrainz release id fill failed: %s", e)


def _fill_empty_columns(cursor, table: str, row_id: Any, fields: Dict[str, Any]) -> None:
    """UPDATE only the columns whose current value is NULL or empty.

    Conservative: never overwrites populated values. Lets a re-import
    fill metadata gaps (e.g. cover art that wasn't available the first
    time) without trampling enrichment data the metadata workers wrote
    later. Mirrors how the media-server scanner refreshes rows on each
    pass, but with the safety belt of "don't clobber".

    Empty-check happens in Python (not SQL) because SQLite's
    `NULLIF(text_col, 0)` returns the original text value instead of
    NULL — type-coercion mismatch makes the SQL-only conditional
    unreliable. Reading the row first, comparing in Python, then
    issuing only the necessary SET clauses sidesteps that entirely.

    Column names are validated against `_SOULSYNC_FILLABLE_COLUMNS`
    before any f-string interpolation — defense against accidental
    misuse adding new columns without an allowlist update.
    """
    allowed = _SOULSYNC_FILLABLE_COLUMNS.get(table, frozenset())
    safe_fields = {col: val for col, val in fields.items() if col in allowed}
    if not safe_fields:
        return
    # Read current values so we can decide per-column whether a fill
    # is needed. Single SELECT instead of one-per-column saves
    # round-trips.
    col_list = ", ".join(safe_fields.keys())
    try:
        cursor.execute(f"SELECT {col_list} FROM {table} WHERE id = ?", (row_id,))
    except Exception as e:
        logger.debug("fill-empty SELECT on %s failed: %s", table, e)
        return
    row = cursor.fetchone()
    if not row:
        return
    set_clauses: list[str] = []
    values: list[Any] = []
    for col, new_value in safe_fields.items():
        # Skip when payload itself is empty — no point writing NULL → NULL.
        # For numeric columns (year, duration, track_count) 0 means
        # "unknown" so treat as no-op too.
        if new_value in (None, "", 0):
            continue
        # Read current value; only fill when it's empty/zero.
        try:
            current = row[col]
        except (KeyError, IndexError):
            continue
        if current not in (None, "", 0):
            continue
        set_clauses.append(f"{col} = ?")
        values.append(new_value)
    if not set_clauses:
        return
    values.append(row_id)
    try:
        cursor.execute(
            f"UPDATE {table} SET {', '.join(set_clauses)}, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            values,
        )
    except Exception as e:
        logger.debug("fill-empty UPDATE on %s failed: %s", table, e)


def _fill_empty_source_id(cursor, table: str, column: str, value: str, row_id: Any) -> None:
    """Single-column variant of _fill_empty_columns for the
    `<source>_<entity>_id` columns whose names come from
    `get_library_source_id_columns(source)`."""
    if column not in _SOULSYNC_FILLABLE_COLUMNS.get(table, frozenset()):
        logger.debug("skipping non-allowlisted source-id column %s.%s", table, column)
        return
    if not value:
        return
    try:
        cursor.execute(f"SELECT {column} FROM {table} WHERE id = ?", (row_id,))
        row = cursor.fetchone()
    except Exception as e:
        logger.debug("fill-empty source-id SELECT on %s.%s failed: %s", table, column, e)
        return
    if not row:
        return
    try:
        current = row[column]
    except (KeyError, IndexError):
        return
    if current not in (None, ""):
        return
    try:
        cursor.execute(
            f"UPDATE {table} SET {column} = ? WHERE id = ?",
            (value, row_id),
        )
    except Exception as e:
        logger.debug("fill-empty source-id UPDATE on %s.%s failed: %s", table, column, e)


def emit_track_downloaded(context: Dict[str, Any], automation_engine=None) -> None:
    """Emit the track_downloaded automation event."""
    try:
        if not automation_engine:
            return

        ti = context.get("track_info") or context.get("search_result") or {}
        artist_name = ""
        artists = ti.get("artists", [])
        if artists:
            first = artists[0]
            artist_name = first.get("name", str(first)) if isinstance(first, dict) else str(first)

        automation_engine.emit(
            "track_downloaded",
            {
                "artist": artist_name,
                "title": ti.get("name", ti.get("title", "")),
                "album": ti.get("album", ""),
                "quality": context.get("_audio_quality", "Unknown"),
            },
        )
    except Exception as e:
        logger.debug("track_downloaded emit failed: %s", e)


def record_library_history_download(context: Dict[str, Any]) -> None:
    """Record a completed download to the library_history table."""
    try:
        search_result = context.get("original_search_result") or context.get("search_result") or {}
        username = search_result.get("username", context.get("_download_username", ""))
        # One canonical username→label map (core/downloads/live_detail.py),
        # shared with the live status payloads so a live row and its later
        # history row can never disagree on a source's name (#1156).
        from core.downloads.live_detail import SOURCE_LABELS
        download_source = SOURCE_LABELS.get(username, "Soulseek")

        ti = context.get("track_info") or context.get("search_result") or {}
        artist_name = _primary_track_artist_name(ti)
        if not artist_name:
            artist_name = ti.get("artist", "")

        album_raw = ti.get("album", "")
        album_name = album_raw.get("name", "") if isinstance(album_raw, dict) else str(album_raw or "")
        title = ti.get("name", ti.get("title", ""))
        quality = context.get("_audio_quality", "")
        file_path = context.get("_final_processed_path", context.get("_final_path", ""))

        thumb_url = ""
        album_context = get_import_context_album(context)
        if album_context:
            thumb_url = album_context.get("image_url", "")
            if not thumb_url:
                images = album_context.get("images", [])
                if images:
                    thumb_url = images[0].get("url", "")
        if not thumb_url:
            album_info = context.get("album_info", {})
            if isinstance(album_info, dict):
                thumb_url = album_info.get("album_image_url", "")

        source_filename = search_result.get("filename", "")
        source_track_id = search_result.get("track_id", "") or search_result.get("id", "") or ti.get("id", "")
        source_track_title = search_result.get("title", "") or search_result.get("name", "")
        source_artist = search_result.get("artist", "")
        if source_filename and "||" in source_filename and username in ("tidal", "youtube", "qobuz", "hifi", "deezer_dl", "lidarr", "soundcloud", "amazon"):
            stream_id = source_filename.split("||")[0]
            if stream_id and not source_track_id:
                source_track_id = stream_id

        acoustid_result = context.get("_acoustid_result", "")

        # What TRIGGERED this download (watchlist scan / playlist sync) —
        # feeds the origin-history modal. None for manual/unclassified.
        from core.downloads.origin import derive_download_origin
        origin, origin_context = derive_download_origin(context)

        db = get_database()
        _history_id = db.add_library_history_entry(
            event_type="download",
            title=title,
            artist_name=artist_name,
            album_name=album_name,
            quality=quality,
            file_path=file_path,
            thumb_url=thumb_url,
            download_source=download_source,
            source_track_id=source_track_id,
            source_track_title=source_track_title,
            source_filename=source_filename,
            acoustid_result=acoustid_result,
            source_artist=source_artist,
            origin=origin,
            origin_context=origin_context,
            verification_status=context.get("_verification_status"),
        )
        # Stash the row id so the live download task can link to its
        # library_history row (the Unverified review queue needs it).
        if isinstance(_history_id, int) and _history_id > 0:
            context["_history_id"] = _history_id
    except Exception as e:
        logger.debug("library history record failed: %s", e)


def record_download_provenance(context: Dict[str, Any]) -> None:
    """Record source provenance for a completed download."""
    try:
        search_result = context.get("original_search_result") or context.get("search_result") or {}
        username = search_result.get("username", context.get("_download_username", ""))
        filename = search_result.get("filename", "")
        source_service = {
            "youtube": "youtube",
            "tidal": "tidal",
            "qobuz": "qobuz",
            "hifi": "hifi",
            "deezer_dl": "deezer",
            "lidarr": "lidarr",
            "soundcloud": "soundcloud",
            "amazon": "amazon",
            # Auto-import: surfaced in provenance so the redownload modal
            # can tell the user "this came from staging on <date>" instead
            # of falsely listing soulseek as the source. The underlying
            # metadata source (spotify / deezer / itunes) is recorded
            # separately via the source-aware ID columns on the tracks
            # row itself.
            "auto_import": "auto_import",
            # Generic staging-match (user dropped files manually OR a
            # source we don't have a more specific label for). Better
            # than defaulting to 'soulseek' which would falsely tag the
            # provenance.
            "staging": "staging",
            # Torrent / usenet album-bundle flow — the staging matcher
            # overrides 'staging' with the bundle source so the history
            # shows where the files actually came from.
            "torrent": "torrent",
            "usenet": "usenet",
        }.get(username, "soulseek")

        ti = context.get("track_info") or context.get("search_result") or {}
        artist_name = _primary_track_artist_name(ti)
        if not artist_name:
            artist_name = ti.get("artist", "")

        album_raw = ti.get("album", "")
        album_name = album_raw.get("name", "") if isinstance(album_raw, dict) else str(album_raw or "")
        title = ti.get("name", ti.get("title", ""))

        file_path = context.get("_final_processed_path", context.get("_final_path", ""))
        quality = context.get("_audio_quality", "")
        size = search_result.get("size", 0)

        bit_depth = None
        sample_rate = None
        bitrate = None
        try:
            if file_path and os.path.isfile(file_path):
                from mutagen import File as MutagenFile

                audio = MutagenFile(file_path)
                if audio and audio.info:
                    sample_rate = getattr(audio.info, "sample_rate", None)
                    bitrate = getattr(audio.info, "bitrate", None)
                    bit_depth = getattr(audio.info, "bits_per_sample", None)
        except Exception as e:
            logger.debug("audio info probe failed: %s", e)

        # Pull the metadata-source IDs out of context. ``embed_source_ids``
        # in core/metadata/source.py wrote them to ``_embedded_id_tags``
        # at the end of post-processing — we persist them here so the
        # watchlist scanner can recognize freshly downloaded files
        # without waiting for the async enrichment workers.
        embedded = context.get("_embedded_id_tags") or {}

        def _embedded(*keys):
            for k in keys:
                v = embedded.get(k)
                if v:
                    return str(v)
            return None

        spotify_track_id = _embedded("SPOTIFY_TRACK_ID")
        itunes_track_id = _embedded("ITUNES_TRACK_ID")
        deezer_track_id = _embedded("DEEZER_TRACK_ID")
        tidal_track_id = _embedded("TIDAL_TRACK_ID")
        qobuz_track_id = _embedded("QOBUZ_TRACK_ID")
        musicbrainz_recording_id, recording_disambiguation = _recording_identity(context)
        audiodb_id = _embedded("AUDIODB_TRACK_ID")
        soul_id = _embedded("SOUL_ID")
        isrc = context.get("_isrc")
        acquired_quality_json, retention_json = _retention_provenance_json(context)

        db = get_database()
        download_row_id = db.record_track_download(
            file_path=file_path,
            source_service=source_service,
            source_username=username,
            source_filename=filename,
            source_size=size or 0,
            audio_quality=quality,
            track_title=title,
            track_artist=artist_name,
            track_album=album_name,
            bit_depth=bit_depth,
            sample_rate=sample_rate,
            bitrate=bitrate,
            spotify_track_id=spotify_track_id,
            itunes_track_id=itunes_track_id,
            deezer_track_id=deezer_track_id,
            tidal_track_id=tidal_track_id,
            qobuz_track_id=qobuz_track_id,
            musicbrainz_recording_id=musicbrainz_recording_id,
            recording_disambiguation=recording_disambiguation,
            audiodb_id=audiodb_id,
            soul_id=soul_id,
            isrc=isrc,
            acquired_quality_json=acquired_quality_json,
            retention_json=retention_json,
        )
        # the "why this file" record for the task that fetched it
        if download_row_id and context.get("task_id"):
            db.link_download_decision(context["task_id"], download_row_id)
    except Exception as e:
        logger.debug("record_download_provenance failed: %s", e)


def is_active_media_server_ready() -> tuple[bool, str]:
    """Standalone ('soulsync') is always ready — no external connection needed.

    Otherwise the active media server must actually be connected, or an
    import copies files into place fully tagged but never registers them:
    record_soulsync_library_entry() below only writes the local DB when
    active_media_server == 'soulsync', and the alternative path (a
    Plex/Jellyfin/Navidrome library scan syncing into the DB) silently
    no-ops without a live connection too. Files land on disk; the Library
    view never learns about them. Shared by the HTTP import routes
    (core.imports.routes) and the auto-import background worker
    (core.auto_import_worker) so neither path can leave files stranded."""
    active = _get_config_manager().get_active_media_server()
    if active == "soulsync":
        return True, ""

    from core.media_server.engine import get_media_server_engine

    if get_media_server_engine().is_connected():
        return True, ""

    label = active.replace("_", " ").title() if active else "Your media server"
    return False, (
        f"{label} isn't connected, so importing now would copy files into "
        f"place without adding them to your Library. Connect {label} in "
        f"Settings, or switch to Standalone mode, then try again."
    )


def record_soulsync_library_entry(context: Dict[str, Any], artist_context: Dict[str, Any], album_info: Dict[str, Any]) -> None:
    """Write imported media to the SoulSync library tables when the active server is SoulSync."""
    try:
        if _get_config_manager().get_active_media_server() != "soulsync":
            return

        context = normalize_import_context(context)
        final_path = context.get("_final_processed_path")
        if not final_path:
            return

        # #1504: owning user profile for the new rows (from auto-import
        # breadcrumb, download request, etc.). None = shared.
        owner_pid = context.get('profile_id')
        try:
            owner_pid = int(owner_pid) if owner_pid else None
        except (TypeError, ValueError):
            owner_pid = None

        album_ctx = get_import_context_album(context)
        track_info = get_import_track_info(context)
        original_search = get_import_original_search(context)
        source = get_import_source(context)
        source_ids = get_import_source_ids(context)
        source_columns = get_library_source_id_columns(source)

        artist_name = extract_artist_name(artist_context) or get_import_clean_artist(context, default="")
        if not artist_name or artist_name in ("Unknown", "Unknown Artist"):
            return

        # A compilation is imported one track at a time, and `artist_name` here
        # is whoever the DOWNLOAD was for — so one contributor's track filed the
        # whole soundtrack under them, over and over, until 45 compilations sat
        # on one guitarist (sassmastawillis). When the album's own metadata says
        # various artists, the ALBUM goes there; the track keeps its real artist
        # through `track_artist` below, which exists for exactly this.
        from core.imports.compilation import compilation_album_artist
        _va_artist = compilation_album_artist(album_ctx, artist_name)
        if _va_artist:
            logger.info(
                "[Import] '%s' is a various-artists release — filing the album "
                "under %s instead of %s (the track keeps its own artist)",
                album_ctx.get("name", "") or "album", _va_artist, artist_name,
            )
            artist_name = _va_artist

        album_name = ""
        if album_info and isinstance(album_info, dict):
            album_name = album_info.get("album_name", "")
        if not album_name:
            album_name = album_ctx.get("name", "") or original_search.get("album", "")
        if not album_name:
            album_name = track_info.get("name", "Unknown")

        # #1562: the release kind the download pipeline already carries
        # (post_processing stamps album_info['record_type']). Before this,
        # the import wrote no kind at all — record_type was only backfilled
        # by the enrichment sweep, so freshly imported releases were
        # kind-blind (and the single/album gates lenient-always) until the
        # workers ran. album_type is the fallback; '' stays unknown.
        _import_record_type = ""
        if isinstance(album_info, dict):
            _import_record_type = (
                album_info.get("record_type") or album_info.get("album_type") or ""
            )
        if not _import_record_type:
            _import_record_type = (
                album_ctx.get("record_type") or album_ctx.get("album_type") or ""
            )
        _import_record_type = str(_import_record_type).strip().lower()

        track_name = get_import_clean_title(
            context,
            album_info=album_info,
            default=track_info.get("name", "") or original_search.get("title", ""),
        )
        track_number = (track_info.get("track_number") or (album_info.get("track_number") if isinstance(album_info, dict) else None)) or 1
        duration_ms = track_info.get("duration_ms", 0) or 0

        year = None
        release_date = album_ctx.get("release_date", "")
        if release_date and len(release_date) >= 4:
            try:
                year = int(release_date[:4])
            except ValueError:
                pass

        image_url = album_ctx.get("image_url", "")
        if not image_url:
            images = album_ctx.get("images", [])
            if images and isinstance(images, list) and len(images) > 0:
                img = images[0]
                image_url = img.get("url", "") if isinstance(img, dict) else str(img)

        artist_source_id = source_ids.get("artist_id", "")
        album_source_id = source_ids.get("album_id", "")
        track_source_id = source_ids.get("track_id", "")
        for key in ("auto_import", "from_sync_modal", "explicit_artist", "explicit_album", ""):
            if artist_source_id == key:
                artist_source_id = ""
            if album_source_id == key:
                album_source_id = ""
            if track_source_id == key:
                track_source_id = ""

        genres = (artist_context or {}).get("genres", []) if isinstance(artist_context, dict) else []
        if genres:
            from core.genre_filter import filter_genres as _filter_genres

            genres = _filter_genres(genres, _get_config_manager())
        genres_json = json.dumps(genres) if genres else ""

        # File size on disk (powers Library Disk Usage card on Stats).
        file_size = None
        try:
            file_size = os.path.getsize(final_path) or None
        except OSError:
            pass

        bitrate = 0
        try:
            from mutagen import File as MutagenFile
            from core.imports.file_ops import _kbps_from_stream_info, estimate_bitrate_kbps

            audio = MutagenFile(final_path)
            info = audio.info if audio is not None and getattr(audio, "info", None) else None
            kbps = _kbps_from_stream_info(info, final_path)
            if not kbps:
                kbps = estimate_bitrate_kbps(size_bytes=file_size, duration_ms=duration_ms)
            if kbps:
                bitrate = int(kbps)
        except Exception as e:
            logger.debug("bitrate read failed: %s", e)

        artist_id = _stable_soulsync_id(artist_name.lower().strip())
        album_id = _stable_soulsync_id(f"{artist_name}::{album_name}".lower().strip())
        track_id = _stable_soulsync_id(final_path)
        total_tracks = album_ctx.get("total_tracks", 0) or 0
        # Album total duration — auto-import passes the sum of every
        # matched track's duration via `album.duration_ms`, mirroring
        # what soulsync_client's deep scan computes. Falls back to
        # the per-track duration for callers that don't provide an
        # album total (legacy direct-download flow).
        album_total_duration_ms = int(
            album_ctx.get("duration_ms") or duration_ms or 0
        )

        db = get_database()
        with db._get_connection() as conn:
            cursor = conn.cursor()

            # ── Artist row: insert-or-fill-empty-fields ────────────
            #
            # Pre-refactor was insert-only: subsequent imports of the
            # same artist (same name, second album) found the existing
            # row via the name-fallback SELECT and skipped completely.
            # That meant artist genres / thumb / source-id reflected
            # whatever the FIRST imported album supplied, never
            # refreshing as more albums by that artist landed.
            #
            # Conservative fix: when an existing row matches, run an
            # UPDATE that only fills NULL/empty fields (`thumb_url IS
            # NULL OR thumb_url = ''`). Never overwrites populated
            # values — protects manual edits + enrichment-worker
            # writes.
            artist_source_col = source_columns.get("artist")

            cursor.execute(
                "SELECT id FROM artists WHERE id = ? AND server_source = 'soulsync'",
                (artist_id,),
            )
            row = cursor.fetchone()
            if not row:
                cursor.execute(
                    "SELECT id FROM artists WHERE name COLLATE NOCASE = ? AND server_source = 'soulsync' LIMIT 1",
                    (artist_name,),
                )
                row = cursor.fetchone()
                if row:
                    artist_id = row[0]

            if row:
                _fill_empty_columns(
                    cursor,
                    table="artists",
                    row_id=artist_id,
                    fields={
                        "thumb_url": image_url,
                        "genres": genres_json,
                    },
                )
                if artist_source_col and artist_source_id:
                    _fill_empty_source_id(cursor, "artists", artist_source_col, artist_source_id, artist_id)
            else:
                # Hash collision protection — if the stable ID is
                # already in use by a different server's row, mint a
                # soulsync-suffixed ID so we don't trample.
                cursor.execute("SELECT id FROM artists WHERE id = ?", (artist_id,))
                if cursor.fetchone():
                    artist_id = _stable_soulsync_id(artist_name.lower().strip() + "::soulsync")
                # #1504: stamp owner_profile_id only when the column exists —
                # minimal test schemas / pre-migration DBs don't have it,
                # and a missing column must not fail the whole insert.
                try:
                    _artist_cols = {c[1] for c in cursor.execute("PRAGMA table_info(artists)").fetchall()}
                except Exception:
                    _artist_cols = set()
                _a_cols = ["id", "name", "genres", "thumb_url", "server_source"]
                _a_vals = ["?", "?", "?", "?", "'soulsync'"]
                _a_params: list = [artist_id, artist_name, genres_json, image_url]
                if "owner_profile_id" in _artist_cols:
                    _a_cols.append("owner_profile_id")
                    _a_vals.append("?")
                    _a_params.append(owner_pid)
                _a_cols += ["created_at", "updated_at"]
                _a_vals += ["CURRENT_TIMESTAMP", "CURRENT_TIMESTAMP"]
                cursor.execute(
                    f"INSERT INTO artists ({', '.join(_a_cols)}) VALUES ({', '.join(_a_vals)})",
                    _a_params,
                )
                if artist_source_col and artist_source_id:
                    try:
                        cursor.execute(
                            f"UPDATE artists SET {artist_source_col} = ? WHERE id = ?",
                            (artist_source_id, artist_id),
                        )
                    except Exception as e:
                        logger.debug("artist source-id update failed: %s", e)

            # ── Album row: same insert-or-fill-empty-fields shape ──
            album_source_col = source_columns.get("album")

            # Group by CANONICAL release id when we have one (not just the name
            # string), so differently-named imports of the SAME release land in
            # one album row instead of splitting — which left the repair jobs
            # dressing each split row in its own cover art (Sokhi). Precedence:
            # name-hash id -> source release id -> (title, artist). Falls back to
            # the legacy name match, so nothing that grouped before stops now.
            #
            # a musicbrainz release id is the sharpest edition identity we get, and
            # the only one a musicbrainz import has (that source has no album
            # column of its own). two releases can share a name (#1299), so it
            # keys the grouping whenever the import carries one.
            from core.imports.album_grouping import find_existing_soulsync_album_id
            from core.metadata.musicbrainz_tags import selected_release_id
            mb_release_id = selected_release_id(album_ctx)
            group_col, group_id = album_source_col, album_source_id
            if mb_release_id:
                group_col, group_id = "musicbrainz_release_id", mb_release_id
            existing_album_id = find_existing_soulsync_album_id(
                cursor, name_key_id=album_id, artist_id=artist_id, album_name=album_name,
                album_source_col=group_col, album_source_id=group_id,
            )
            if existing_album_id is not None:
                album_id = existing_album_id
                row = (album_id,)
            else:
                row = None

            if row:
                _fill_empty_columns(
                    cursor,
                    table="albums",
                    row_id=album_id,
                    fields={
                        "thumb_url": image_url,
                        "genres": genres_json,
                        "year": year,
                        "track_count": total_tracks,
                        "duration": album_total_duration_ms,
                        # Fill-only: never overwrites a kind the enrichment
                        # workers already wrote.
                        "record_type": _import_record_type,
                    },
                )
                if album_source_col and album_source_id:
                    _fill_empty_source_id(cursor, "albums", album_source_col, album_source_id, album_id)
            else:
                cursor.execute("SELECT id FROM albums WHERE id = ?", (album_id,))
                if cursor.fetchone():
                    # the name-hash id is taken, by another server's row or by a
                    # different release of the same name. a release id gives each
                    # release its own stable id, so a third one can't collide.
                    _scope = group_id or "soulsync"
                    album_id = _stable_soulsync_id(f"{artist_name}::{album_name}::{_scope}".lower().strip())
                # #1504: stamp owner_profile_id only when the column exists —
                # minimal test schemas / pre-migration DBs don't have it,
                # and a missing column must not fail the whole insert.
                try:
                    _album_cols = {c[1] for c in cursor.execute("PRAGMA table_info(albums)").fetchall()}
                except Exception:
                    _album_cols = set()
                _al_cols = ["id", "artist_id", "title", "year", "thumb_url", "genres",
                            "track_count", "duration", "server_source"]
                _al_vals = ["?", "?", "?", "?", "?", "?", "?", "?", "'soulsync'"]
                _al_params: list = [album_id, artist_id, album_name, year, image_url,
                                    genres_json, total_tracks, album_total_duration_ms]
                if "owner_profile_id" in _album_cols:
                    _al_cols.append("owner_profile_id")
                    _al_vals.append("?")
                    _al_params.append(owner_pid)
                # #1562: stamp the kind at import time (same PRAGMA guard as
                # owner_profile_id — minimal test schemas may lack it).
                if "record_type" in _album_cols:
                    _al_cols.append("record_type")
                    _al_vals.append("?")
                    _al_params.append(_import_record_type or None)
                _al_cols += ["created_at", "updated_at"]
                _al_vals += ["CURRENT_TIMESTAMP", "CURRENT_TIMESTAMP"]
                cursor.execute(
                    f"INSERT INTO albums ({', '.join(_al_cols)}) VALUES ({', '.join(_al_vals)})",
                    _al_params,
                )
                if album_source_col and album_source_id:
                    try:
                        cursor.execute(
                            f"UPDATE albums SET {album_source_col} = ? WHERE id = ?",
                            (album_source_id, album_id),
                        )
                    except Exception as e:
                        logger.debug("album source-id update failed: %s", e)

            if mb_release_id:
                _fill_empty_mb_release_id(cursor, album_id, mb_release_id)

            track_artist = None
            track_artists_list = track_info.get("artists", []) or original_search.get("artists", [])
            if track_artists_list:
                first_track_artist = track_artists_list[0]
                if isinstance(first_track_artist, dict):
                    ta_name = first_track_artist.get("name", "")
                else:
                    ta_name = str(first_track_artist)
                if ta_name and ta_name.lower() != artist_name.lower():
                    track_artist = ta_name

            # Per-recording identifiers — scanner picks `musicbrainz_recording_id`
            # off the Navidrome track wrapper; auto-import has the same field
            # available from the metadata-source response (Spotify exposes
            # `musicbrainz_recording_id` via the MusicBrainz client, Picard-
            # tagged files surface it via `_read_file_tags`). `isrc` is even
            # better signal for cross-source dedup — it's the per-recording
            # ID labels embed in the audio. Both land in dedicated columns
            # so the watchlist scanner's stable-ID match path recognises
            # auto-imported tracks the next time the user adds the artist
            # to a watchlist.
            track_mbid, recording_disambiguation = _recording_identity(context)
            track_isrc = (track_info.get("isrc") or "").strip().upper() or None
            # Carries whatever the pipeline resolved for this item (a wishlist
            # row's or Auto-Import's own override, or None for "follow the
            # app-wide default") — see `_resolve_context_quality_profile` in
            # core/imports/pipeline.py. Without this, later Quality Check /
            # Quality Upgrade Finder passes re-resolve the track against the
            # default profile instead of the one it was actually imported under.
            track_quality_profile_id = track_info.get("quality_profile_id")
            acquired_quality_json, retention_json = _retention_provenance_json(context)
            try:
                track_columns = {
                    column[1] for column in cursor.execute(
                        "PRAGMA table_info(tracks)").fetchall()
                }
            except Exception:  # pragma is best-effort for non-SQLite test doubles
                track_columns = set()
            has_retention_columns = {
                "acquired_quality_json", "retention_json"
            }.issubset(track_columns)
            # #1504: stamp owner_profile_id only when the column exists —
            # minimal test schemas / pre-migration DBs don't have it,
            # and a missing column must not fail the whole insert.
            has_track_owner = "owner_profile_id" in track_columns

            cursor.execute("SELECT id FROM tracks WHERE file_path = ?", (final_path,))
            existing_track = cursor.fetchone()
            if not existing_track:
                base_values = (
                        track_id,
                        album_id,
                        artist_id,
                        track_name,
                        track_number,
                        duration_ms,
                        final_path,
                        bitrate,
                        file_size,
                        track_artist,
                        track_mbid,
                        track_isrc,
                        track_quality_profile_id,
                )
                # C4: _stable_soulsync_id is MD5 mod 1e9, so two different
                # paths can share an id (the artist/album paths above mint
                # a suffixed id on collision; the track path did not, and
                # the IntegrityError silently dropped the second track).
                # Retry the INSERT with a deterministic discriminator. The
                # post-insert source-id UPDATE below reads `track_id`, so
                # it follows the reminted id automatically.
                # Build the track INSERT dynamically: retention columns and
                # owner_profile_id are each included only when present.
                _t_cols = ["id", "album_id", "artist_id", "title", "track_number",
                           "duration", "file_path", "bitrate", "file_size", "track_artist",
                           "musicbrainz_recording_id", "isrc", "quality_profile_id"]
                _t_vals = ["?"] * 13
                _t_params: list = list(base_values)
                if has_retention_columns:
                    _t_cols += ["acquired_quality_json", "retention_json"]
                    _t_vals += ["?", "?"]
                    _t_params += [acquired_quality_json, retention_json]
                if "recording_disambiguation" in track_columns:
                    _t_cols.append("recording_disambiguation")
                    _t_vals.append("?")
                    _t_params.append(recording_disambiguation)
                _t_cols.append("server_source")
                _t_vals.append("'soulsync'")
                if has_track_owner:
                    _t_cols.append("owner_profile_id")
                    _t_vals.append("?")
                    _t_params.append(owner_pid)
                _t_cols += ["created_at", "updated_at"]
                _t_vals += ["CURRENT_TIMESTAMP", "CURRENT_TIMESTAMP"]
                _t_sql = (f"INSERT INTO tracks ({', '.join(_t_cols)}) "
                          f"VALUES ({', '.join(_t_vals)})")
                for _collision_attempt in range(10):
                    try:
                        _t_params[0] = track_id
                        cursor.execute(_t_sql, _t_params)
                        break
                    except sqlite3.IntegrityError:
                        track_id = _stable_soulsync_id(
                            f"{final_path}::soulsync::{_collision_attempt + 1}"
                        )
                        logger.warning(
                            "[SoulSync Library] track id collision for %s — "
                            "reminted as %s",
                            final_path,
                            track_id,
                        )
                else:
                    raise sqlite3.IntegrityError(
                        f"track id still collides after retries: {final_path}"
                    )
                track_source_col = source_columns.get("track")
                if track_source_col and track_source_id:
                    try:
                        cursor.execute(
                            f"UPDATE tracks SET {track_source_col} = ? WHERE id = ?",
                            (track_source_id, track_id),
                        )
                        track_album_col = source_columns.get("track_album")
                        if track_album_col and album_source_id:
                            cursor.execute(
                                f"UPDATE tracks SET {track_album_col} = ? WHERE id = ?",
                                (album_source_id, track_id),
                            )
                    except Exception as e:
                        logger.debug("track source-id update failed: %s", e)
            else:
                # A repeated/import-resume write refreshes transformation
                # provenance for the existing physical row.  Never retain an
                # old destructive-policy claim after a clean untransformed
                # import of that same path.
                if has_retention_columns:
                    cursor.execute(
                        """UPDATE tracks
                              SET acquired_quality_json=?, retention_json=?,
                                  updated_at=CURRENT_TIMESTAMP
                            WHERE id=?""",
                        (acquired_quality_json, retention_json, existing_track[0]),
                    )
                if recording_disambiguation and "recording_disambiguation" in track_columns:
                    cursor.execute(
                        "UPDATE tracks SET recording_disambiguation = ?, "
                        "musicbrainz_recording_id = COALESCE(NULLIF(musicbrainz_recording_id, ''), ?) "
                        "WHERE id = ? AND (musicbrainz_recording_id IS NULL "
                        "OR musicbrainz_recording_id = '' "
                        "OR LOWER(musicbrainz_recording_id) = ?)",
                        (recording_disambiguation, track_mbid, existing_track[0], track_mbid),
                    )

            conn.commit()
            logger.info("[SoulSync Library] Added: %s / %s / %s", artist_name, album_name, track_name)
            if not existing_track:
                # Fresh import — queue Sample Studio background analysis.
                # Lazy import + never raises: analysis must not break imports.
                try:
                    from core.sample.worker import enqueue_analysis as _enqueue_sample_analysis
                    _enqueue_sample_analysis(track_id)
                except Exception as hook_exc:  # noqa: BLE001
                    logger.debug("Sample analysis enqueue failed for track %s: %s",
                                 track_id, hook_exc)
    except Exception as exc:
        logger.error("[SoulSync Library] Could not record library entry: %s", exc)
