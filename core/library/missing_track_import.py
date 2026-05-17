"""Import an existing library file into a missing album slot.

This module keeps the "I Have This" behavior out of the Flask route layer:
copy the selected source file, post-process it with target album metadata,
inherit album identity tags from target siblings, and write the real DB row.
"""

from __future__ import annotations

from dataclasses import dataclass
import logging
import os
import shutil
import uuid
from typing import Any, Callable, Dict, Optional

from core.library_reorganize import _build_post_process_context


logger = logging.getLogger("soulsync.library.missing_track_import")


class MissingTrackImportError(Exception):
    """Expected import failure that should be surfaced to the API caller."""

    def __init__(self, message: str, status_code: int = 400):
        super().__init__(message)
        self.status_code = status_code


@dataclass
class MissingTrackImportDeps:
    database: Any
    config_manager: Any
    post_process_fn: Callable[[str, dict, str], Any]
    resolve_library_file_path_fn: Callable[[Optional[str]], Optional[str]]
    docker_resolve_path_fn: Callable[[str], str]
    sync_tracks_to_server_fn: Optional[Callable[[list, str], Any]] = None
    service_id_columns: Optional[Dict[str, Dict[str, str]]] = None


_ALBUM_IDENTITY_TAGS = {
    "album",
    "albumartist",
    "album_artist",
    "date",
    "year",
    "tracktotal",
    "totaltracks",
    "totaldiscs",
    "musicbrainz_albumid",
    "musicbrainz_albumartistid",
    "musicbrainz_releasegroupid",
    "barcode",
    "catalognumber",
    "originaldate",
    "releasecountry",
    "releasestatus",
    "releasetype",
    "media",
    "script",
    "copyright",
    "spotify_album_id",
    "deezer_album_id",
    "tidal_album_id",
    "qobuz_album_id",
    "itunes_album_id",
    "audiodb_album_id",
}

_ID3_STANDARD_TAGS = {
    "album": "TALB",
    "albumartist": "TPE2",
    "album_artist": "TPE2",
    "date": "TDRC",
    "year": "TDRC",
}

_ID3_TXXX_DESCS = {
    "musicbrainz_albumid": "MusicBrainz Album Id",
    "musicbrainz_albumartistid": "MusicBrainz Album Artist Id",
    "musicbrainz_releasegroupid": "MusicBrainz Release Group Id",
    "barcode": "BARCODE",
    "catalognumber": "CATALOGNUMBER",
    "originaldate": "ORIGINALDATE",
    "releasecountry": "RELEASECOUNTRY",
    "releasestatus": "RELEASESTATUS",
    "releasetype": "RELEASETYPE",
    "media": "MEDIA",
    "script": "SCRIPT",
    "totaldiscs": "TOTALDISCS",
    "tracktotal": "TOTALTRACKS",
    "totaltracks": "TOTALTRACKS",
    "spotify_album_id": "Spotify Album Id",
    "deezer_album_id": "Deezer Album Id",
    "tidal_album_id": "Tidal Album Id",
    "qobuz_album_id": "Qobuz Album Id",
    "itunes_album_id": "iTunes Album Id",
    "audiodb_album_id": "AudioDB Album Id",
}

_MP4_STANDARD_TAGS = {
    "album": "\xa9alb",
    "albumartist": "aART",
    "album_artist": "aART",
    "date": "\xa9day",
    "year": "\xa9day",
}


def import_existing_track_for_album_slot(album_id: str, payload: dict, deps: MissingTrackImportDeps) -> dict:
    source_track_id = payload.get("source_track_id") or payload.get("linked_track_id")
    expected = payload.get("expected_track") or {}
    if not source_track_id:
        raise MissingTrackImportError("source_track_id is required", 400)
    if not expected.get("track_number") or not (expected.get("title") or expected.get("name")):
        raise MissingTrackImportError("expected_track with title and track_number is required", 400)

    database = deps.database
    album_data, source_track = _load_album_and_source_track(database, album_id, source_track_id)
    if album_data.get("server_source") and source_track.get("server_source") and album_data["server_source"] != source_track["server_source"]:
        raise MissingTrackImportError("Selected track belongs to a different library source", 400)

    source_path = deps.resolve_library_file_path_fn(source_track.get("file_path"))
    if not source_path:
        raise MissingTrackImportError(_file_not_found_message(source_track.get("file_path")), 404)

    staging_path = _copy_source_to_staging(source_path, album_id, expected, deps)
    metadata_source = (expected.get("source") or payload.get("source") or "").strip().lower() or "library"
    expected_title = expected.get("title") or expected.get("name") or "Unknown Track"
    expected_track_id = _expected_track_id(expected)
    album_source_id = _album_source_id(payload, expected, album_data, album_id)

    api_track = _build_api_track(expected, expected_title, expected_track_id, album_source_id, metadata_source, album_data)
    context = _build_context(payload, album_data, source_path, source_track_id, api_track, album_source_id, metadata_source)

    context_key = f"existing_import_{album_id}_{api_track['disc_number']}_{api_track['track_number']}_{uuid.uuid4().hex[:8]}"
    deps.post_process_fn(context_key, context, staging_path)
    final_path = context.get("_final_processed_path")
    if not final_path or not os.path.exists(final_path):
        raise MissingTrackImportError("Post-processing did not produce a final file", 500)

    copy_album_identity_from_target_sibling(
        database,
        album_id,
        final_path,
        api_track["disc_number"],
        api_track["track_number"],
        deps.resolve_library_file_path_fn,
    )

    target_track_id = _upsert_target_track(
        database,
        deps,
        album_id,
        album_data,
        source_track,
        final_path,
        expected_title,
        expected_track_id,
        metadata_source,
        api_track,
    )
    _sync_imported_track(deps, target_track_id, expected_title, album_data)

    return {
        "track_id": target_track_id,
        "final_path": final_path,
        "artist_id": album_data.get("target_artist_id"),
    }


def _load_album_and_source_track(database, album_id: str, source_track_id: str) -> tuple[dict, dict]:
    with database._get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT al.*, ar.name AS artist_name, ar.id AS target_artist_id
            FROM albums al
            JOIN artists ar ON ar.id = al.artist_id
            WHERE al.id = ?
            """,
            (album_id,),
        )
        album_row = cursor.fetchone()
        if not album_row:
            raise MissingTrackImportError("Album not found", 404)

        cursor.execute("SELECT * FROM tracks WHERE id = ?", (source_track_id,))
        source_row = cursor.fetchone()
        if not source_row:
            raise MissingTrackImportError("Selected library track not found", 404)

    return dict(album_row), dict(source_row)


def _copy_source_to_staging(source_path: str, album_id: str, expected: dict, deps: MissingTrackImportDeps) -> str:
    download_dir = deps.docker_resolve_path_fn(deps.config_manager.get("soulseek.download_path", "./downloads"))
    staging_root = os.path.join(download_dir, "ssync_existing_import")
    os.makedirs(staging_root, exist_ok=True)
    source_ext = os.path.splitext(source_path)[1] or ".audio"
    staging_name = (
        f"existing_{album_id}_{expected.get('disc_number') or 1}_"
        f"{expected.get('track_number')}_{uuid.uuid4().hex[:8]}{source_ext}"
    )
    staging_path = os.path.join(staging_root, staging_name)
    shutil.copy2(source_path, staging_path)
    return staging_path


def _build_api_track(
    expected: dict,
    expected_title: str,
    expected_track_id: str,
    album_source_id: str,
    metadata_source: str,
    album_data: dict,
) -> dict:
    return {
        "id": expected_track_id,
        "track_id": expected_track_id,
        "name": expected_title,
        "title": expected_title,
        "track_number": int(expected.get("track_number") or 1),
        "disc_number": int(expected.get("disc_number") or 1),
        "duration_ms": int(expected.get("duration") or expected.get("duration_ms") or 0),
        "artists": expected.get("artists") or [album_data.get("artist_name") or ""],
        "source": metadata_source,
        "album_id": album_source_id,
        "spotify_track_id": expected.get("spotify_track_id") or "",
        "deezer_id": expected.get("deezer_id") or "",
        "itunes_track_id": expected.get("itunes_track_id") or "",
        "musicbrainz_recording_id": expected.get("musicbrainz_recording_id") or "",
    }


def _build_context(
    payload: dict,
    album_data: dict,
    source_path: str,
    source_track_id: str,
    api_track: dict,
    album_source_id: str,
    metadata_source: str,
) -> dict:
    api_album = {
        "id": album_source_id,
        "name": album_data.get("title") or "",
        "title": album_data.get("title") or "",
        "release_date": f"{album_data.get('year')}-01-01" if album_data.get("year") else "",
        "total_tracks": album_data.get("api_track_count") or album_data.get("track_count") or 0,
        "image_url": album_data.get("thumb_url") or "",
        "source": metadata_source,
    }
    context = _build_post_process_context(
        api_album,
        api_track,
        album_data.get("artist_name") or "",
        album_data.get("title") or "",
        int(payload.get("total_discs") or payload.get("expected_track", {}).get("total_discs") or 1),
    )
    context["source"] = metadata_source
    context["source_service"] = "existing_library"
    context["source_filename"] = os.path.basename(source_path)
    context["source_size"] = os.path.getsize(source_path) if os.path.exists(source_path) else 0
    context["explicit_album_context"] = True
    context["from_existing_library_track"] = True
    context["batch_id"] = f"existing_import_{album_data.get('id')}_{uuid.uuid4().hex[:8]}"
    context["task_id"] = f"existing_import_{source_track_id}"
    return context


def _upsert_target_track(
    database,
    deps: MissingTrackImportDeps,
    album_id: str,
    album_data: dict,
    source_track: dict,
    final_path: str,
    expected_title: str,
    expected_track_id: str,
    metadata_source: str,
    api_track: dict,
):
    file_size, bitrate = _read_file_stats(final_path, source_track)
    server_source = album_data.get("server_source") or source_track.get("server_source") or deps.config_manager.get_active_media_server()

    with database._get_connection() as conn:
        cursor = conn.cursor()
        _ensure_disc_number_column(cursor, conn)

        cursor.execute("SELECT id FROM tracks WHERE file_path = ? LIMIT 1", (final_path,))
        existing_by_path = cursor.fetchone()
        cursor.execute(
            """
            SELECT id FROM tracks
            WHERE album_id = ? AND COALESCE(disc_number, 1) = ? AND track_number = ?
            LIMIT 1
            """,
            (album_id, api_track["disc_number"], api_track["track_number"]),
        )
        existing_target = cursor.fetchone()

        if existing_by_path:
            target_track_id = existing_by_path["id"]
            cursor.execute(
                """
                UPDATE tracks
                SET album_id = ?, artist_id = ?, title = ?, track_number = ?, disc_number = ?,
                    duration = ?, file_path = ?, bitrate = ?, file_size = ?,
                    server_source = COALESCE(server_source, ?),
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (
                    album_id,
                    album_data.get("target_artist_id"),
                    expected_title,
                    api_track["track_number"],
                    api_track["disc_number"],
                    api_track["duration_ms"],
                    final_path,
                    bitrate,
                    file_size,
                    server_source,
                    target_track_id,
                ),
            )
        elif existing_target:
            target_track_id = existing_target["id"]
            cursor.execute(
                """
                UPDATE tracks
                SET title = ?, duration = ?, file_path = ?, bitrate = ?, file_size = ?,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (expected_title, api_track["duration_ms"], final_path, bitrate, file_size, target_track_id),
            )
        else:
            cursor.execute("SELECT COALESCE(MAX(CAST(id AS INTEGER)), 0) + 1 AS next_id FROM tracks")
            target_track_id = cursor.fetchone()["next_id"]
            cursor.execute(
                """
                INSERT INTO tracks (
                    id, album_id, artist_id, title, track_number, disc_number, duration,
                    file_path, bitrate, file_size, server_source, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                """,
                (
                    target_track_id,
                    album_id,
                    album_data.get("target_artist_id"),
                    expected_title,
                    api_track["track_number"],
                    api_track["disc_number"],
                    api_track["duration_ms"],
                    final_path,
                    bitrate,
                    file_size,
                    server_source,
                ),
            )

        track_source_col = (deps.service_id_columns or {}).get(metadata_source, {}).get("track")
        if track_source_col and expected_track_id:
            try:
                cursor.execute(f"UPDATE tracks SET {track_source_col} = ? WHERE id = ?", (expected_track_id, target_track_id))
            except Exception as source_err:
                logger.debug("Imported track source-id update failed: %s", source_err)

        conn.commit()

    return target_track_id


def _ensure_disc_number_column(cursor, conn) -> None:
    cursor.execute("PRAGMA table_info(tracks)")
    track_columns = {row[1] for row in cursor.fetchall()}
    if "disc_number" not in track_columns:
        cursor.execute("ALTER TABLE tracks ADD COLUMN disc_number INTEGER DEFAULT 1")
        conn.commit()


def _read_file_stats(final_path: str, source_track: dict) -> tuple[Optional[int], int]:
    file_size = None
    bitrate = source_track.get("bitrate") or 0
    try:
        file_size = os.path.getsize(final_path)
        from mutagen import File as MutagenFile

        audio = MutagenFile(final_path)
        if audio and getattr(audio, "info", None) and getattr(audio.info, "bitrate", None):
            bitrate = int(audio.info.bitrate / 1000)
    except Exception as meta_err:
        logger.debug("Existing-track import metadata read failed: %s", meta_err)
    return file_size, bitrate


def _sync_imported_track(deps: MissingTrackImportDeps, track_id, expected_title: str, album_data: dict) -> None:
    try:
        active_server = deps.config_manager.get_active_media_server()
        if deps.sync_tracks_to_server_fn and active_server in ("jellyfin", "navidrome"):
            deps.sync_tracks_to_server_fn(
                [
                    {
                        "id": track_id,
                        "title": expected_title,
                        "artist_name": album_data.get("artist_name"),
                        "album_title": album_data.get("title"),
                        "year": album_data.get("year"),
                        "server_source": album_data.get("server_source"),
                    }
                ],
                active_server,
            )
    except Exception as sync_err:
        logger.debug("Existing-track import server sync skipped/failed: %s", sync_err)


def copy_album_identity_from_target_sibling(
    database,
    album_id: str,
    final_path: str,
    target_disc: int,
    target_track: int,
    resolve_library_file_path_fn: Callable[[Optional[str]], Optional[str]],
) -> bool:
    try:
        with database._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT file_path FROM tracks
                WHERE album_id = ?
                  AND file_path IS NOT NULL
                  AND file_path != ''
                  AND NOT (COALESCE(disc_number, 1) = ? AND track_number = ?)
                ORDER BY COALESCE(disc_number, 1), track_number
                LIMIT 12
                """,
                (album_id, target_disc, target_track),
            )
            sibling_rows = cursor.fetchall()

        for row in sibling_rows:
            sibling_path = resolve_library_file_path_fn(row["file_path"])
            if not sibling_path or not os.path.exists(sibling_path):
                continue
            tags = read_album_identity_tags(sibling_path)
            if not tags:
                continue
            if write_album_identity_tags(final_path, tags):
                logger.info("Imported track inherited album identity tags from sibling: %s", os.path.basename(sibling_path))
                return True
    except Exception as exc:
        logger.warning("Failed to inherit album identity tags for imported track: %s", exc)
    return False


def read_album_identity_tags(file_path: str) -> dict:
    try:
        from mutagen import File as MutagenFile
        from mutagen.id3 import ID3, TXXX
        from mutagen.mp4 import MP4

        audio = MutagenFile(file_path)
        if not audio:
            return {}

        tags = {}
        if isinstance(getattr(audio, "tags", None), ID3):
            for tag_key, frame_id in _ID3_STANDARD_TAGS.items():
                frames = audio.tags.getall(frame_id)
                if frames and getattr(frames[0], "text", None):
                    tags[tag_key] = str(frames[0].text[0]).strip()
            desc_to_key = {desc: key for key, desc in _ID3_TXXX_DESCS.items()}
            for frame in audio.tags.getall("TXXX"):
                if isinstance(frame, TXXX) and frame.desc in desc_to_key and frame.text:
                    tags[desc_to_key[frame.desc]] = str(frame.text[0]).strip()
        elif isinstance(audio, MP4):
            for tag_key, mp4_key in _MP4_STANDARD_TAGS.items():
                value = _first_tag_value(audio, mp4_key)
                if value:
                    tags[tag_key] = value
            for tag_key, desc in _ID3_TXXX_DESCS.items():
                value = _first_tag_value(audio, f"----:com.apple.iTunes:{desc}")
                if value:
                    tags[tag_key] = value
        else:
            for tag_key in _ALBUM_IDENTITY_TAGS:
                value = _first_tag_value(audio, tag_key)
                if value:
                    tags[tag_key] = value
        return {k: v for k, v in tags.items() if v}
    except Exception as exc:
        logger.debug("Failed reading album identity tags from %s: %s", file_path, exc)
        return {}


def write_album_identity_tags(file_path: str, tags: dict) -> bool:
    if not tags:
        return False
    try:
        from mutagen import File as MutagenFile
        from mutagen.id3 import ID3, TALB, TDRC, TPE2, TXXX
        from mutagen.mp4 import MP4, MP4FreeForm

        audio = MutagenFile(file_path)
        if not audio:
            return False

        tags = {k: str(v).strip() for k, v in tags.items() if k in _ALBUM_IDENTITY_TAGS and str(v).strip()}
        if not tags:
            return False

        if isinstance(getattr(audio, "tags", None), ID3):
            standard_frames = {"TALB": TALB, "TPE2": TPE2, "TDRC": TDRC}
            written_standard = set()
            for tag_key, frame_id in _ID3_STANDARD_TAGS.items():
                value = tags.get(tag_key)
                if not value or frame_id in written_standard:
                    continue
                audio.tags.delall(frame_id)
                audio.tags.add(standard_frames[frame_id](encoding=3, text=[value]))
                written_standard.add(frame_id)
            for tag_key, desc in _ID3_TXXX_DESCS.items():
                value = tags.get(tag_key)
                if not value:
                    continue
                for existing in list(audio.tags.getall("TXXX")):
                    if getattr(existing, "desc", None) == desc:
                        audio.tags.remove(existing.HashKey)
                audio.tags.add(TXXX(encoding=3, desc=desc, text=[value]))
        elif isinstance(audio, MP4):
            for tag_key, mp4_key in _MP4_STANDARD_TAGS.items():
                if tags.get(tag_key):
                    audio[mp4_key] = [tags[tag_key]]
            for tag_key, desc in _ID3_TXXX_DESCS.items():
                if tags.get(tag_key):
                    audio[f"----:com.apple.iTunes:{desc}"] = [MP4FreeForm(tags[tag_key].encode("utf-8"))]
        else:
            for tag_key, value in tags.items():
                audio[tag_key] = [value]

        audio.save()
        return True
    except Exception as exc:
        logger.warning("Failed writing album identity tags to %s: %s", file_path, exc)
        return False


def _first_tag_value(audio, key: str) -> Optional[str]:
    try:
        values = audio.get(key)
        if not values:
            return None
        value = values[0] if isinstance(values, (list, tuple)) else values
        if isinstance(value, bytes):
            value = value.decode("utf-8", errors="ignore")
        return str(value).strip() or None
    except Exception:
        return None


def _expected_track_id(expected: dict) -> str:
    return (
        expected.get("track_id")
        or expected.get("id")
        or expected.get("source_track_id")
        or expected.get("spotify_track_id")
        or expected.get("deezer_id")
        or expected.get("itunes_track_id")
        or expected.get("musicbrainz_recording_id")
        or ""
    )


def _album_source_id(payload: dict, expected: dict, album_data: dict, album_id: str) -> str:
    return (
        payload.get("album_source_id")
        or expected.get("album_id")
        or album_data.get("spotify_album_id")
        or album_data.get("deezer_id")
        or album_data.get("itunes_album_id")
        or album_data.get("musicbrainz_release_id")
        or album_data.get("discogs_id")
        or album_data.get("tidal_id")
        or album_data.get("qobuz_id")
        or str(album_id)
    )


def _file_not_found_message(file_path: Optional[str]) -> str:
    if file_path:
        return f"File not found: {file_path}"
    return "Selected library track does not have a file path"
