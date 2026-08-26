"""Import post-processing side effects that do not need web runtime state."""

from __future__ import annotations

import hashlib
import json
import os
from typing import Any, Dict, Optional

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
    # The catalogue columns a re-import may FILL when they are still empty.
    # Never an overwrite — a later provider pass owns whatever it wrote.
    "lib2_artists": frozenset({"image_url", "genres", "summary"}),
    "lib2_albums": frozenset({"image_url", "genres", "year", "track_count", "duration"}),
    "lib2_tracks": frozenset({"isrc", "musicbrainz_id", "track_artist"}),
}


def _fill_external_id(cursor, table: str, row_id: Any, source: Optional[str],
                      value: Optional[str]) -> None:
    """Record the provider id this import came from, without clobbering.

    Spotify and MusicBrainz have promoted columns in v2; everything else lives
    in ``external_ids``, so the write is a JSON merge that leaves an existing
    value for the same provider alone.
    """
    provider = (source or "").strip().lower()
    identifier = str(value or "").strip()
    if not provider or not identifier or table not in _SOULSYNC_FILLABLE_COLUMNS:
        return
    try:
        if provider in ("spotify", "musicbrainz"):
            column = "spotify_id" if provider == "spotify" else "musicbrainz_id"
            cursor.execute(
                f"UPDATE {table} SET {column} = ? "
                f" WHERE id = ? AND ({column} IS NULL OR {column} = '')",
                (identifier, row_id))
            return
        cursor.execute(
            f"UPDATE {table} SET external_ids = json_set("
            f"           CASE WHEN json_valid(external_ids) THEN external_ids ELSE '{{}}' END,"
            f"           '$.{provider}', ?)"
            f" WHERE id = ? AND json_extract("
            f"           CASE WHEN json_valid(external_ids) THEN external_ids ELSE '{{}}' END,"
            f"           '$.{provider}') IS NULL",
            (identifier, row_id))
    except Exception as e:
        logger.debug("external-id fill on %s failed: %s", table, e)


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
            # F-10: that stash is in-memory only and gone by the time a user
            # approves or rejects the file. Persist the acquisition
            # correlation on the row itself so the later decision can still
            # be journaled. Fail-open — an ordinary import writes nothing.
            try:
                from core.acquisition.pipeline_callback import (
                    persist_history_correlation,
                )
                persist_history_correlation(context, _history_id)
            except Exception as exc:  # noqa: BLE001
                logger.debug("history correlation persist failed: %s", exc)
    except Exception as e:
        logger.debug("library history record failed: %s", e)


def registered_lib2_file_id(context: Dict[str, Any]) -> Optional[int]:
    """The live Library-v2 file row for this import's final path, if any."""
    path = context.get("_final_processed_path") or context.get("_final_path")
    if not path:
        return None
    conn = None
    try:
        conn = get_database()._get_connection()
        row = conn.execute(
            "SELECT id FROM lib2_track_files WHERE path=?"
            " AND COALESCE(file_state,'active')='active' LIMIT 1", (str(path),),
        ).fetchone()
        return int(row[0]) if row else None
    except Exception as e:
        logger.debug("library v2 file lookup failed: %s", e)
        return None
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception as close_err:
                logger.debug("library v2 file lookup close failed: %s", close_err)


def require_library_v2_registration(context: Dict[str, Any]) -> None:
    """An import may only complete once its file exists in the catalogue.

    §37/LV2-PAUD-03 made registration part of success. The gate belongs after
    *every* writer that can satisfy it, not after only the first one: the
    autolink returns None for a file whose identity it cannot derive, while
    ``record_soulsync_library_entry`` derives the same identity from
    ``artist_context`` and writes the same rows. So the question this asks is
    what the catalogue holds, not what one writer returned.
    """
    if registered_lib2_file_id(context) is None:
        raise RuntimeError(
            "The file was written, but Library v2 did not create a file row"
        )


def record_download_provenance(context: Dict[str, Any],
                               require_library: bool = False) -> Optional[int]:
    """Record provenance and register the imported file in Library v2.

    ``require_library`` is used by terminal pipeline paths: registration then
    becomes part of success instead of a debug-only best effort.
    """
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
        musicbrainz_recording_id = _embedded("MUSICBRAINZ_RECORDING_ID")
        audiodb_id = _embedded("AUDIODB_TRACK_ID")
        soul_id = _embedded("SOUL_ID")
        isrc = context.get("_isrc")
        acquired_quality_json, retention_json = _retention_provenance_json(context)

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
            acquired_quality_json=acquired_quality_json,
            retention_json=retention_json,
        )
    except Exception as e:
        logger.debug("record_download_provenance failed: %s", e)

    # Register the imported file in the native catalogue immediately. Repair
    # callers may keep this best-effort; terminal completion paths make it a
    # required part of success via ``require_library``.
    linked_lib2_file_id = None
    try:
        from core.library2.autolink import link_download_into_library_v2
        linked_lib2_file_id = link_download_into_library_v2(
            context, raise_on_error=require_library)
    except Exception as e:
        if require_library:
            raise RuntimeError(
                "The file was written, but Library v2 could not register it"
            ) from e
        logger.debug("library v2 autolink skipped: %s", e)
    if require_library and linked_lib2_file_id is None:
        require_library_v2_registration(context)

    # The canonical tracklist deliberately comes from one provider, but a
    # confirmed album can carry several provider release IDs. Reconcile those
    # exact lists after the import so a newly landed track immediately gains
    # every safe provider ID without making the file pipeline wait on network
    # calls. Album-level debounce collapses a multi-file import into one run.
    if linked_lib2_file_id is not None:
        try:
            from core.library2.track_reconcile_trigger import (
                schedule_file_track_reconcile,
            )
            schedule_file_track_reconcile(
                get_database(),
                linked_lib2_file_id,
                _get_config_manager(),
            )
        except Exception as e:
            logger.debug("library v2 track-identity reconcile not scheduled: %s", e)

    # ...and heal the artists that link just created. Featured credits and
    # wishlist/discography rows are born without a provider id, so their chips
    # stay 'pending' and they carry no artwork until something resolves them
    # (status.md §28). Debounced — a 30-track album import fires this hook 30
    # times and gets one run — and cooldown-guarded so unresolvable names are
    # not re-asked at every import (issues.md §16 Finding 2).
    if linked_lib2_file_id is not None:
        try:
            from core.library2.unmapped_trigger import schedule_unmapped_artist_reconcile
            schedule_unmapped_artist_reconcile(_get_config_manager())
        except Exception as e:
            logger.debug("library v2 unmapped-artist reconcile not scheduled: %s", e)

    # Persistent acquisition completion is intentionally downstream of every
    # shared pipeline guard and the Library-v2 autolink. Quarantined files never
    # reach this point; a later manual approval re-enters the same pipeline and
    # carries these markers in its serialized context.
    try:
        from core.acquisition.pipeline_callback import notify_pipeline_import_success
        notify_pipeline_import_success(context)
    except Exception as e:
        logger.debug("acquisition pipeline callback skipped: %s", e)

    # Correlated legacy manual grabs close their request/grab here too; the
    # callback is a no-op for downloads without the manual-grab marker.
    try:
        from core.acquisition.pipeline_callback import notify_manual_grab_import_success
        notify_manual_grab_import_success(context)
    except Exception as e:
        logger.debug("manual grab callback skipped: %s", e)
    return linked_lib2_file_id


def is_active_media_server_ready() -> tuple[bool, str]:
    """Imports are local and never depend on Plex/Jellyfin/Navidrome uptime.

    Library v2 is registered synchronously by the import pipeline.  A media
    server may recognise that row later and add its scoped mapping, but being
    offline cannot make a valid local import unsafe.
    """
    return True, ""


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
        # No `or 1` fallback: the writer defaults a *new* row to 1 itself, and a
        # guessed 1 handed to an existing row would overwrite its real number.
        track_number = track_info.get("track_number") or (
            album_info.get("track_number") if isinstance(album_info, dict) else None)
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

        # Per-recording identifiers — `isrc` is the better cross-source dedup
        # signal (labels embed it in the audio), `musicbrainz_recording_id`
        # comes off the provider response or a Picard-tagged file.
        track_mbid = (track_info.get("musicbrainz_recording_id") or "").strip().lower() or None
        track_isrc = (track_info.get("isrc") or "").strip().upper() or None
        # Whatever the pipeline resolved for this item (a wishlist row's or
        # Auto-Import's own override, or None for "follow the app-wide
        # default") — without it, later Quality Check / Upgrade passes
        # re-resolve the track against the default profile instead.
        track_quality_profile_id = track_info.get("quality_profile_id")

        db = get_database()
        with db._get_connection() as conn:
            cursor = conn.cursor()

            # A completed import is an ownership path.  It alone opts into
            # catalogue/file creation; media-server scans use these helpers in
            # mapping-only mode.
            from core.library2.media_server_sync import (
                upsert_album, upsert_artist, upsert_track,
            )

            catalogue_artist = upsert_artist(
                cursor, server_source="soulsync", server_id=artist_id,
                name=artist_name, image_url=image_url or None,
                genres_json=genres_json or None, overwrite=False,
                allow_create=True)
            _fill_external_id(cursor, "lib2_artists", catalogue_artist, source, artist_source_id)

            # Group by CANONICAL release id when we have one (not just the name
            # string), so differently-named imports of the SAME release land in
            # one album row instead of splitting — which left the repair jobs
            # dressing each split row in its own cover art (Sokhi).
            from core.imports.album_grouping import find_existing_soulsync_album_id
            existing_album = find_existing_soulsync_album_id(
                cursor, name_key_id=album_id, artist_id=catalogue_artist,
                album_name=album_name, album_source_id=album_source_id, source=source)
            if existing_album is not None:
                cursor.execute(
                    "UPDATE lib2_albums SET server_source='soulsync', server_id=?,"
                    "                       origin='library', updated_at=CURRENT_TIMESTAMP"
                    " WHERE id=? AND (server_id IS NULL OR server_source='soulsync')",
                    (str(album_id), existing_album))
                catalogue_album = existing_album
                _fill_empty_columns(
                    cursor, table="lib2_albums", row_id=catalogue_album,
                    fields={"image_url": image_url, "genres": genres_json,
                            "year": year, "track_count": total_tracks,
                            "duration": album_total_duration_ms})
            else:
                catalogue_album = upsert_album(
                    cursor, server_source="soulsync", server_id=album_id,
                    artist_id=catalogue_artist, title=album_name, year=year,
                    image_url=image_url or None, genres_json=genres_json or None,
                    track_count=total_tracks or None,
                    duration=album_total_duration_ms or None,
                    allow_create=True)
            _fill_external_id(cursor, "lib2_albums", catalogue_album, source, album_source_id)

            catalogue_track = upsert_track(
                cursor, server_source="soulsync", server_id=track_id,
                album_id=catalogue_album, artist_id=catalogue_artist,
                title=track_name, track_number=track_number, duration=duration_ms,
                track_artist=track_artist, musicbrainz_id=track_mbid,
                file_path=final_path, file_size=file_size, bitrate=bitrate,
                allow_create=True, file_source="import")
            cursor.execute("UPDATE lib2_tracks SET isrc=COALESCE(?, isrc) WHERE id=?",
                           (track_isrc, catalogue_track))
            if track_quality_profile_id:
                # v2 says "which profile" and "was it chosen" separately, so an
                # item imported under an explicit override keeps that on record
                # instead of being re-judged against the default later. The
                # catalogue rejects a pointer to a profile that no longer
                # exists (a deleted profile, a stale wishlist row) — that must
                # cost the stamp, never the import.
                try:
                    cursor.execute(
                        "UPDATE lib2_tracks SET quality_profile_id=?,"
                        "                       quality_profile_explicit=1"
                        " WHERE id=?", (track_quality_profile_id, catalogue_track))
                except Exception as e:
                    logger.debug("quality-profile stamp skipped: %s", e)
            _fill_external_id(cursor, "lib2_tracks", catalogue_track, source, track_source_id)

            conn.commit()
            logger.info("[SoulSync Library] Added: %s / %s / %s", artist_name, album_name, track_name)
    except Exception as exc:
        logger.error("[SoulSync Library] Could not record library entry: %s", exc)
