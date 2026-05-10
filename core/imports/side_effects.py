"""Import post-processing side effects that do not need web runtime state."""

from __future__ import annotations

import hashlib
import json
import os
from typing import Any, Dict

from config.settings import config_manager
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


def _stable_soulsync_id(text: str) -> str:
    return str(abs(int(hashlib.md5(text.encode("utf-8", errors="replace")).hexdigest(), 16)) % (10 ** 9))


# Tiny SQL allowlist for the fill-empty helpers — prevents accidental
# SQL injection through the f-string column-name interpolation. Only
# columns the soulsync library write path ever updates are listed.
_SOULSYNC_FILLABLE_COLUMNS = {
    "artists": frozenset({"thumb_url", "genres", "summary", "spotify_artist_id",
                          "itunes_artist_id", "deezer_id", "discogs_id", "soul_id",
                          "hifi_artist_id"}),
    "albums": frozenset({"thumb_url", "genres", "year", "track_count", "duration",
                         "spotify_album_id", "itunes_album_id", "deezer_id",
                         "discogs_id", "soul_id", "hifi_album_id"}),
}


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
        source_map = {
            "youtube": "YouTube",
            "tidal": "Tidal",
            "qobuz": "Qobuz",
            "hifi": "HiFi",
            "deezer_dl": "Deezer",
            "lidarr": "Lidarr",
            "soundcloud": "SoundCloud",
            # Auto-import isn't a download source, but flows through the
            # same post-process pipeline (file lands → record provenance
            # + history → write to library DB). Tagging it as "Auto-Import"
            # in history avoids mislabeling staging-folder imports as
            # Soulseek downloads.
            "auto_import": "Auto-Import",
        }
        download_source = source_map.get(username, "Soulseek")

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
        if source_filename and "||" in source_filename and username in ("tidal", "youtube", "qobuz", "hifi", "deezer_dl", "lidarr", "soundcloud"):
            stream_id = source_filename.split("||")[0]
            if stream_id and not source_track_id:
                source_track_id = stream_id

        acoustid_result = context.get("_acoustid_result", "")

        db = get_database()
        db.add_library_history_entry(
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
        )
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
            # Auto-import: surfaced in provenance so the redownload modal
            # can tell the user "this came from staging on <date>" instead
            # of falsely listing soulseek as the source. The underlying
            # metadata source (spotify / deezer / itunes) is recorded
            # separately via the source-aware ID columns on the tracks
            # row itself.
            "auto_import": "auto_import",
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
        musicbrainz_recording_id = _embedded("MUSICBRAINZ_RECORDING_ID")
        audiodb_id = _embedded("AUDIODB_TRACK_ID")
        soul_id = _embedded("SOUL_ID")
        isrc = context.get("_isrc")

        db = get_database()
        db.record_track_download(
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
            audiodb_id=audiodb_id,
            soul_id=soul_id,
            isrc=isrc,
        )
    except Exception as e:
        logger.debug("record_download_provenance failed: %s", e)


def record_soulsync_library_entry(context: Dict[str, Any], artist_context: Dict[str, Any], album_info: Dict[str, Any]) -> None:
    """Write imported media to the SoulSync library tables when the active server is SoulSync."""
    try:
        if _get_config_manager().get_active_media_server() != "soulsync":
            return

        context = normalize_import_context(context)
        final_path = context.get("_final_processed_path")
        if not final_path:
            return

        album_ctx = get_import_context_album(context)
        track_info = get_import_track_info(context)
        original_search = get_import_original_search(context)
        source = get_import_source(context)
        source_ids = get_import_source_ids(context)
        source_columns = get_library_source_id_columns(source)

        artist_name = extract_artist_name(artist_context) or get_import_clean_artist(context, default="")
        if not artist_name or artist_name in ("Unknown", "Unknown Artist"):
            return

        album_name = ""
        if album_info and isinstance(album_info, dict):
            album_name = album_info.get("album_name", "")
        if not album_name:
            album_name = album_ctx.get("name", "") or original_search.get("album", "")
        if not album_name:
            album_name = track_info.get("name", "Unknown")

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

        bitrate = 0
        try:
            from mutagen import File as MutagenFile

            audio = MutagenFile(final_path)
            if audio and hasattr(audio, "info") and audio.info and hasattr(audio.info, "bitrate"):
                bitrate = int(audio.info.bitrate / 1000) if audio.info.bitrate else 0
        except Exception as e:
            logger.debug("bitrate read failed: %s", e)

        # File size on disk (powers Library Disk Usage card on Stats).
        # SoulSync standalone is the only path where the file is local
        # at insert time, so we read it directly via os.path.getsize.
        # Mirrors what JellyfinTrack/NavidromeTrack pull from API
        # responses for the media-server flows.
        file_size = None
        try:
            file_size = os.path.getsize(final_path) or None
        except OSError:
            pass

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
                cursor.execute(
                    """
                    INSERT INTO artists (id, name, genres, thumb_url, server_source, created_at, updated_at)
                    VALUES (?, ?, ?, ?, 'soulsync', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                    """,
                    (artist_id, artist_name, genres_json, image_url),
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

            cursor.execute(
                "SELECT id FROM albums WHERE id = ? AND server_source = 'soulsync'",
                (album_id,),
            )
            row = cursor.fetchone()
            if not row:
                cursor.execute(
                    "SELECT id FROM albums WHERE title COLLATE NOCASE = ? AND artist_id = ? AND server_source = 'soulsync' LIMIT 1",
                    (album_name, artist_id),
                )
                row = cursor.fetchone()
                if row:
                    album_id = row[0]

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
                    },
                )
                if album_source_col and album_source_id:
                    _fill_empty_source_id(cursor, "albums", album_source_col, album_source_id, album_id)
            else:
                cursor.execute("SELECT id FROM albums WHERE id = ?", (album_id,))
                if cursor.fetchone():
                    album_id = _stable_soulsync_id(f"{artist_name}::{album_name}::soulsync".lower().strip())
                cursor.execute(
                    """
                    INSERT INTO albums (id, artist_id, title, year, thumb_url, genres, track_count,
                                        duration, server_source, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'soulsync', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                    """,
                    (album_id, artist_id, album_name, year, image_url, genres_json, total_tracks, album_total_duration_ms),
                )
                if album_source_col and album_source_id:
                    try:
                        cursor.execute(
                            f"UPDATE albums SET {album_source_col} = ? WHERE id = ?",
                            (album_source_id, album_id),
                        )
                    except Exception as e:
                        logger.debug("album source-id update failed: %s", e)

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
            track_mbid = (track_info.get("musicbrainz_recording_id") or "").strip().lower() or None
            track_isrc = (track_info.get("isrc") or "").strip().upper() or None

            cursor.execute("SELECT id FROM tracks WHERE file_path = ?", (final_path,))
            if not cursor.fetchone():
                cursor.execute(
                    """
                    INSERT INTO tracks (id, album_id, artist_id, title, track_number,
                                        duration, file_path, bitrate, file_size, track_artist,
                                        musicbrainz_recording_id, isrc, server_source,
                                        created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'soulsync', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                    """,
                    (
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
                    ),
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

            conn.commit()
            logger.info("[SoulSync Library] Added: %s / %s / %s", artist_name, album_name, track_name)
    except Exception as exc:
        logger.error("[SoulSync Library] Could not record library entry: %s", exc)


def record_retag_download(context: Dict[str, Any], artist_context: Dict[str, Any], album_info: Dict[str, Any], final_path: str) -> None:
    """Record a completed download for later re-tagging."""
    try:
        db = get_database()

        context = normalize_import_context(context)
        artist_context = get_import_context_artist(context) or (artist_context if isinstance(artist_context, dict) else {})
        album_context = get_import_context_album(context)
        track_info = get_import_track_info(context)
        original_search = get_import_original_search(context)
        source = get_import_source(context)
        source_ids = get_import_source_ids(context)

        artist_name = extract_artist_name(artist_context) or get_import_clean_artist(context, default="Unknown Artist")
        is_album = album_info and album_info.get("is_album", False)
        group_type = "album" if is_album else "single"
        album_name = album_info.get("album_name", "") if album_info else get_import_clean_album(context, default=original_search.get("album", "Unknown"))

        image_url = album_info.get("album_image_url") if album_info else None
        if not image_url:
            image_url = album_context.get("image_url", "")
            if not image_url and album_context.get("images"):
                images = album_context.get("images", [])
                if images and isinstance(images[0], dict):
                    image_url = images[0].get("url", "")

        total_tracks = album_context.get("total_tracks", 1) if album_context else 1
        release_date = album_context.get("release_date", "") if album_context else ""

        spotify_album_id = None
        itunes_album_id = None
        if source == "spotify":
            spotify_album_id = source_ids.get("album_id", "") or None
        elif source == "itunes":
            itunes_album_id = source_ids.get("album_id", "") or None

        group_id = db.find_retag_group(artist_name, album_name)
        if group_id is None:
            group_id = db.add_retag_group(
                group_type=group_type,
                artist_name=artist_name,
                album_name=album_name,
                image_url=image_url,
                spotify_album_id=spotify_album_id,
                itunes_album_id=itunes_album_id,
                total_tracks=total_tracks,
                release_date=release_date,
            )
        if group_id is None:
            return

        track_number = album_info.get("track_number", 1) if album_info else (track_info.get("track_number", 1) or 1)
        disc_number = original_search.get("disc_number") or (album_info.get("disc_number", 1) if album_info else track_info.get("disc_number", 1) or 1)
        title = get_import_clean_title(
            context,
            album_info=album_info,
            default=album_info.get("clean_track_name", "Unknown Track") if album_info else "Unknown Track",
        )
        file_format = os.path.splitext(str(final_path))[1].lstrip(".").lower()

        source_track_id = None
        itunes_track_id = None
        if source == "spotify":
            source_track_id = source_ids.get("track_id", "") or None
        elif source == "itunes":
            itunes_track_id = source_ids.get("track_id", "") or None

        if not db.retag_track_exists(group_id, str(final_path)):
            db.add_retag_track(
                group_id=group_id,
                track_number=track_number,
                disc_number=disc_number,
                title=title,
                file_path=str(final_path),
                file_format=file_format,
                spotify_track_id=source_track_id,
                itunes_track_id=itunes_track_id,
            )
            logger.info("[Retag] Recorded track for retag: '%s' in '%s'", title, album_name)

        db.trim_retag_groups(100)
    except Exception as exc:
        logger.error("[Retag] Could not record track for retag: %s", exc)
