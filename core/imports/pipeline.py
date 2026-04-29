"""Import/post-processing pipeline for downloads and imported files."""

from __future__ import annotations

import json
import os
import threading
import time
from types import SimpleNamespace
from typing import Any

from config.settings import config_manager
from core.imports.file_ops import (
    cleanup_empty_directories,
    create_lossy_copy,
    downsample_hires_flac,
    get_audio_quality_string,
    get_quality_tier_from_extension,
    safe_move_file,
)
from core.imports.context import (
    build_import_album_info,
    detect_album_info_web,
    extract_artist_name,
    get_import_clean_artist,
    get_import_clean_title,
    get_import_context_artist,
    get_import_has_clean_metadata,
    get_import_original_search,
    get_import_source,
    get_import_track_info,
    normalize_import_context,
)
from core.imports.filename import extract_track_number_from_filename
from core.imports.guards import check_flac_bit_depth, move_to_quarantine
from core.imports.side_effects import (
    emit_track_downloaded,
    record_download_provenance,
    record_library_history_download,
    record_retag_download,
    record_soulsync_library_entry,
)
from core.wishlist.resolution import check_and_remove_from_wishlist
from core.runtime_state import (
    add_activity_item,
    download_batches,
    download_tasks,
    matched_context_lock,
    matched_downloads_context,
    mark_task_completed as _mark_task_completed,
    post_process_locks,
    post_process_locks_lock,
    processed_download_ids,
    tasks_lock,
)
from core.metadata.artwork import download_cover_art
from core.metadata.common import wipe_source_tags
from core.metadata.enrichment import enhance_file_metadata
from core.imports.paths import (
    build_final_path_for_track,
    build_simple_download_destination,
    docker_resolve_path,
)
from core.imports.album_naming import resolve_album_group
from core.metadata.lyrics import generate_lrc_file
from database.music_database import get_database
from utils.logging_config import get_logger


logger = get_logger("imports.pipeline")
pp_logger = get_logger("post_processing")

__all__ = [
    "build_import_pipeline_runtime",
    "post_process_matched_download",
    "post_process_matched_download_with_verification",
]


def build_import_pipeline_runtime(
    *,
    automation_engine: Any | None = None,
    on_download_completed: Any | None = None,
    web_scan_manager: Any | None = None,
    repair_worker: Any | None = None,
) -> SimpleNamespace:
    """Build the runtime object consumed by core.imports.pipeline."""
    return SimpleNamespace(
        automation_engine=automation_engine,
        on_download_completed=on_download_completed,
        web_scan_manager=web_scan_manager,
        repair_worker=repair_worker,
    )


def post_process_matched_download(context_key, context, file_path, runtime, metadata_runtime=None):
    on_download_completed = getattr(runtime, "on_download_completed", None)
    automation_engine = getattr(runtime, "automation_engine", None)
    web_scan_manager = getattr(runtime, "web_scan_manager", None)
    repair_worker = getattr(runtime, "repair_worker", None)
    metadata_runtime = metadata_runtime or runtime

    def _notify_download_completed(batch_id, task_id, success=True):
        if on_download_completed:
            on_download_completed(batch_id, task_id, success=success)

    with post_process_locks_lock:
        if context_key not in post_process_locks:
            post_process_locks[context_key] = threading.Lock()
        file_lock = post_process_locks[context_key]

    file_lock.acquire()
    try:
        if not os.path.exists(file_path):
            existing_final = context.get('_final_processed_path')
            if existing_final and os.path.exists(existing_final):
                logger.info(
                    f"[Race Guard] Source gone but destination exists — already processed by another thread: "
                    f"{os.path.basename(existing_final)}"
                )
                return
            logger.error(
                f"[Race Guard] Source file gone and no known destination — marking as failed: "
                f"{os.path.basename(file_path)}"
            )
            context['_race_guard_failed'] = True
            return

        _basename = os.path.basename(file_path)
        _prev_size = -1
        for _stability_check in range(5):
            try:
                _cur_size = os.path.getsize(file_path)
            except OSError:
                _cur_size = -1
            if _cur_size == _prev_size and _cur_size > 0:
                break
            _prev_size = _cur_size
            if _stability_check == 0:
                logger.info(f"Waiting for file to stabilise: {_basename} ({_cur_size} bytes)")
            time.sleep(1.5)
        else:
            logger.info(f"File may still be writing after stability checks: {_basename} ({_prev_size} bytes)")

        _skip_acoustid = False
        try:
            from core.acoustid_verification import AcoustIDVerification, VerificationResult

            verifier = AcoustIDVerification()
            available, available_reason = verifier.quick_check_available()
            if available and not _skip_acoustid:
                context = normalize_import_context(context)
                track_info = get_import_track_info(context)
                original_search = get_import_original_search(context)
                artist_context = get_import_context_artist(context)

                expected_track = get_import_clean_title(context, default=original_search.get('title', ''))
                expected_artist = ''
                track_artists = track_info.get('artists', [])
                if track_artists:
                    first = track_artists[0]
                    if isinstance(first, dict):
                        expected_artist = first.get('name', '')
                    elif isinstance(first, str):
                        expected_artist = first
                if not expected_artist:
                    expected_artist = extract_artist_name(artist_context) or get_import_clean_artist(context, default='')

                if expected_track and expected_artist:
                    logger.info(f"Running AcoustID verification for: '{expected_track}' by '{expected_artist}'")
                    verification_result, verification_msg = verifier.verify_audio_file(
                        file_path,
                        expected_track,
                        expected_artist,
                        context,
                    )
                    logger.info(f"AcoustID verification result: {verification_result.value} - {verification_msg}")
                    context['_acoustid_result'] = verification_result.value

                    if verification_result == VerificationResult.FAIL:
                        try:
                            quarantine_path = move_to_quarantine(
                                file_path,
                                context,
                                verification_msg,
                                automation_engine,
                            )
                            logger.error(f"File quarantined due to verification failure: {quarantine_path}")
                        except Exception as quarantine_error:
                            logger.error(f"Quarantine failed ({quarantine_error}), deleting wrong file: {file_path}")
                            logger.error(f"Quarantine failed, deleting wrong file: {file_path}")
                            try:
                                os.remove(file_path)
                            except Exception as del_error:
                                logger.error(f"Could not delete wrong file either: {del_error}")

                        context['_acoustid_quarantined'] = True
                        context['_acoustid_failure_msg'] = verification_msg
                        with matched_context_lock:
                            if context_key in matched_downloads_context:
                                del matched_downloads_context[context_key]

                        task_id = context.get('task_id')
                        batch_id = context.get('batch_id')
                        if task_id:
                            with tasks_lock:
                                if task_id in download_tasks:
                                    download_tasks[task_id]['status'] = 'failed'
                                    download_tasks[task_id]['error_message'] = (
                                        f"AcoustID verification failed: {verification_msg}"
                                    )

                        if task_id and batch_id:
                            _notify_download_completed(batch_id, task_id, success=False)
                        return
                else:
                    logger.warning("AcoustID verification skipped: missing track/artist info")
                    context['_acoustid_result'] = 'skip'
            else:
                logger.info(f"ℹ️ AcoustID verification not available: {available_reason}")
                context['_acoustid_result'] = 'disabled'
        except Exception as verify_error:
            logger.error(f"AcoustID verification error (continuing normally): {verify_error}")
            context['_acoustid_result'] = 'error'

        search_result = context.get('search_result', {}) or {}
        if not isinstance(search_result, dict):
            search_result = {}
        is_simple_download = search_result.get('is_simple_download', False)
        if is_simple_download:
            logger.info(f"Processing simple download (no metadata enhancement): {file_path}")

            destination, album_name, filename = build_simple_download_destination(context, file_path)
            if album_name:
                logger.info(f"Moving to album folder: {album_name}")
            else:
                logger.info("Moving to Transfer root (single track)")

            safe_move_file(file_path, destination)
            logger.info(f"Moved simple download to: {destination}")

            with matched_context_lock:
                if context_key in matched_downloads_context:
                    del matched_downloads_context[context_key]

            if web_scan_manager:
                threading.Thread(
                    target=lambda: web_scan_manager.request_scan("Simple download completed"),
                    daemon=True,
                ).start()

            activity_target = f"{album_name}/{filename}" if album_name else filename
            add_activity_item("", "Download Complete", activity_target, "Now")
            logger.info(f"Simple download post-processing complete: {activity_target}")
            context['_simple_download_completed'] = True
            context['_final_path'] = str(destination)
            emit_track_downloaded(context, automation_engine)
            record_library_history_download(context)
            record_download_provenance(context)
            try:
                check_and_remove_from_wishlist(context)
            except Exception as wishlist_error:
                logger.error(f"[Simple Download] Error checking wishlist removal: {wishlist_error}")
            return

        logger.info(f"Starting robust post-processing for: {context_key}")

        context = normalize_import_context(context)
        artist_context = get_import_context_artist(context)
        track_info = get_import_track_info(context)
        original_search = get_import_original_search(context)
        has_clean_metadata = get_import_has_clean_metadata(context)

        if not artist_context:
            logger.error("Post-processing failed: Missing artist context.")
            return

        _junk_artist_names = {'', 'unknown', 'unknown artist', 'various artists', 'none', 'null'}
        _artist_name = (artist_context.get('name', '') if isinstance(artist_context, dict) else '').strip()
        if _artist_name.lower() in _junk_artist_names:
            logger.info(f"[Unknown Artist Guard] Artist name is '{_artist_name}' — attempting to resolve")
            _resolved = False
            track_info_guard = track_info or {}
            original_search_guard = original_search or {}

            _ti_artists = track_info_guard.get('artists', [])
            if isinstance(_ti_artists, list) and _ti_artists:
                _first = _ti_artists[0]
                _name = _first.get('name', '') if isinstance(_first, dict) else str(_first)
                if _name and _name.strip().lower() not in _junk_artist_names:
                    artist_context['name'] = _name.strip()
                    logger.info(f"[Unknown Artist Guard] Resolved from track_info.artists: '{_name}'")
                    _resolved = True

            if not _resolved:
                _os_artist = original_search_guard.get('artist') or original_search_guard.get('artist_name') or ''
                if isinstance(_os_artist, str) and _os_artist.strip().lower() not in _junk_artist_names:
                    artist_context['name'] = _os_artist.strip()
                    logger.info(f"[Unknown Artist Guard] Resolved from original_search_result: '{_os_artist}'")
                    _resolved = True

            if not _resolved:
                _track_id = track_info_guard.get('id') or track_info_guard.get('track_id') or ''
                if _track_id:
                    try:
                        from core.metadata_service import get_client_for_source, get_primary_source

                        _guard_source = get_import_source(context) or get_primary_source()
                        _fb_client = get_client_for_source(_guard_source) or get_client_for_source(get_primary_source())
                        if hasattr(_fb_client, 'get_track_details'):
                            _details = _fb_client.get_track_details(str(_track_id))
                            if _details and isinstance(_details, dict):
                                _d_artists = _details.get('artists', [])
                                if isinstance(_d_artists, list) and _d_artists:
                                    _d_first = _d_artists[0]
                                    _d_name = _d_first.get('name', '') if isinstance(_d_first, dict) else str(_d_first)
                                    if _d_name and _d_name.strip().lower() not in _junk_artist_names:
                                        artist_context['name'] = _d_name.strip()
                                        logger.info(f"[Unknown Artist Guard] Resolved from metadata API: '{_d_name}'")
                                        _resolved = True
                    except Exception as _guard_err:
                        logger.error(f"[Unknown Artist Guard] Metadata re-fetch failed: {_guard_err}")

            if not _resolved:
                logger.error(f"[Unknown Artist Guard] Could not resolve artist — proceeding with '{_artist_name}'")

            context['artist'] = artist_context

        playlist_folder_mode = track_info.get("_playlist_folder_mode", False)
        logger.debug(f"[Debug] Post-processing - track_info type: {type(track_info)}, is None: {track_info is None}, is empty: {not track_info}")
        logger.debug(f"[Debug] Post-processing - playlist_folder_mode: {playlist_folder_mode}")
        if track_info:
            logger.debug(f"[Debug] Post-processing - track_info keys: {list(track_info.keys())}")

        if playlist_folder_mode:
            playlist_name = track_info.get("_playlist_name", "Unknown Playlist")
            logger.info(f"[Playlist Folder Mode] Organizing in playlist folder: {playlist_name}")

            file_ext = os.path.splitext(file_path)[1]
            final_path, _ = build_final_path_for_track(context, artist_context, None, file_ext)
            logger.info(f"Playlist mode final path: '{final_path}'")

            if not os.path.exists(file_path):
                if os.path.exists(final_path):
                    logger.info(
                        f"[Playlist Folder Mode] Source gone but destination exists — already processed by another thread: "
                        f"{os.path.basename(final_path)}"
                    )
                    context['_final_processed_path'] = final_path
                    return
                pp_logger.info(f"[inner] EXCEPTION in post-processing for {context_key}: Source file not found and destination does not exist: {file_path}")
                raise FileNotFoundError(f"Source file not found and destination does not exist: {file_path}")

            context['_audio_quality'] = get_audio_quality_string(file_path)
            if context['_audio_quality']:
                logger.info(f"Audio quality detected: {context['_audio_quality']}")

            rejection_reason = check_flac_bit_depth(file_path, context)
            if rejection_reason:
                try:
                    quarantine_path = move_to_quarantine(
                        file_path,
                        context,
                        rejection_reason,
                        automation_engine,
                    )
                    logger.info(f"File quarantined due to bit depth filter: {quarantine_path}")
                except Exception as quarantine_error:
                    logger.error(f"Quarantine failed ({quarantine_error}), deleting file: {file_path}")
                    try:
                        os.remove(file_path)
                    except Exception:
                        pass

                context['_bitdepth_rejected'] = True
                with matched_context_lock:
                    if context_key in matched_downloads_context:
                        del matched_downloads_context[context_key]

                task_id = context.get('task_id')
                batch_id = context.get('batch_id')
                if task_id:
                    with tasks_lock:
                        if task_id in download_tasks:
                            download_tasks[task_id]['status'] = 'failed'
                            download_tasks[task_id]['error_message'] = f"Bit depth filter: {rejection_reason}"
                if task_id and batch_id:
                    _notify_download_completed(batch_id, task_id, success=False)
                return

            try:
                logger.warning(
                    f"[Metadata Input] Playlist mode - artist: '{artist_context.get('name', 'MISSING')}' "
                    f"(id: {artist_context.get('id', 'MISSING')})"
                )
                enhance_file_metadata(file_path, context, artist_context, None, runtime=metadata_runtime)
            except Exception as meta_err:
                import traceback
                pp_logger.info(f"[inner] Metadata enhancement FAILED for {context_key}: {meta_err}\n{traceback.format_exc()}")
                wipe_source_tags(file_path)

            logger.info(f"Moving '{os.path.basename(file_path)}' to '{final_path}'")
            safe_move_file(file_path, final_path)
            context['_final_processed_path'] = final_path

            if config_manager.get('post_processing.replaygain_enabled', False):
                try:
                    from core.replaygain import analyze_track as _rg_analyze, write_replaygain_tags as _rg_write, is_ffmpeg_available as _rg_ffmpeg_ok, RG_REFERENCE_LUFS as _RG_REF
                    if _rg_ffmpeg_ok():
                        lufs, peak_dbfs = _rg_analyze(final_path)
                        gain_db = _RG_REF - lufs
                        _rg_write(final_path, gain_db, peak_dbfs)
                        pp_logger.info(f"ReplayGain: {gain_db:+.2f} dB — {os.path.basename(final_path)}")
                except Exception as rg_err:
                    pp_logger.debug(f"ReplayGain analysis skipped: {rg_err}")

            downsampled_path = downsample_hires_flac(final_path, context)
            if downsampled_path:
                final_path = downsampled_path
                context['_final_processed_path'] = final_path

            blasphemy_path = create_lossy_copy(final_path)
            if blasphemy_path:
                context['_final_processed_path'] = blasphemy_path

            downloads_path = docker_resolve_path(config_manager.get('soulseek.download_path', './downloads'))
            cleanup_empty_directories(downloads_path, file_path)

            logger.info(f"[Playlist Folder Mode] Post-processing complete: {final_path}")

            try:
                check_and_remove_from_wishlist(context)
            except Exception as wishlist_error:
                logger.error(f"[Playlist Folder] Error checking wishlist removal: {wishlist_error}")

            emit_track_downloaded(context, automation_engine)
            record_library_history_download(context)
            record_download_provenance(context)

            task_id = context.get('task_id')
            batch_id = context.get('batch_id')
            if task_id and batch_id:
                with tasks_lock:
                    if task_id in download_tasks:
                        download_tasks[task_id]['stream_processed'] = True
                        download_tasks[task_id]['status'] = 'completed'
                        logger.info(f"[Playlist Folder Mode] Marked task {task_id} as completed")
                _notify_download_completed(batch_id, task_id, success=True)
            return

        is_album_download = bool(context.get("is_album_download", False))
        album_info = build_import_album_info(context, force_album=is_album_download)

        if is_album_download:
            if has_clean_metadata:
                logger.info("Album context with clean metadata found - using normalized album info")
            else:
                logger.warning("Album context found without clean metadata - using normalized album info")
        elif not album_info.get('is_album'):
            logger.info("Single track download - attempting album detection")
            detected_album_info = detect_album_info_web(context, artist_context)
            if detected_album_info:
                album_info = detected_album_info

        if album_info and album_info['is_album'] and not is_album_download:
            logger.info(
                "SMART ALBUM GROUPING for track=%r original_album=%r",
                album_info.get('clean_track_name', 'Unknown'),
                album_info.get('album_name', 'None'),
            )
            original_album = original_search.get("album") if original_search.get("album") else None
            consistent_album_name = resolve_album_group(artist_context, album_info, original_album)
            album_info['album_name'] = consistent_album_name
            logger.info("Album grouping complete: final_album=%r", consistent_album_name)
        elif album_info and album_info['is_album'] and is_album_download:
            logger.info(
                "EXPLICIT ALBUM DOWNLOAD - preserving album name=%r; skipping smart grouping",
                album_info.get('album_name', 'None'),
            )

        context['_audio_quality'] = get_audio_quality_string(file_path)
        if context['_audio_quality']:
            logger.info(f"Audio quality detected: {context['_audio_quality']}")

            rejection_reason = check_flac_bit_depth(file_path, context)
            if rejection_reason:
                try:
                    quarantine_path = move_to_quarantine(
                        file_path,
                        context,
                        rejection_reason,
                        automation_engine,
                    )
                    logger.info(f"File quarantined due to bit depth filter: {quarantine_path}")
                except Exception as quarantine_error:
                    logger.error(f"Quarantine failed ({quarantine_error}), deleting file: {file_path}")
                    try:
                        os.remove(file_path)
                    except Exception:
                        pass

                context['_bitdepth_rejected'] = True
                with matched_context_lock:
                    if context_key in matched_downloads_context:
                        del matched_downloads_context[context_key]

                task_id = context.get('task_id')
                batch_id = context.get('batch_id')
                if task_id:
                    with tasks_lock:
                        if task_id in download_tasks:
                            download_tasks[task_id]['status'] = 'failed'
                            download_tasks[task_id]['error_message'] = f"Bit depth filter: {rejection_reason}"
                if task_id and batch_id:
                    _notify_download_completed(batch_id, task_id, success=False)
                return

        file_ext = os.path.splitext(file_path)[1]
        clean_track_name = get_import_clean_title(
            context,
            album_info=album_info,
            default=original_search.get('title', 'Unknown Track'),
        )
        track_number = album_info.get('track_number', 1)
        logger.debug(
            "Final track_number processing: source=%s album_info_track_number=%s track_number=%s",
            album_info.get('source', 'unknown'),
            album_info.get('track_number', 'NOT_FOUND'),
            track_number,
        )
        if track_number is None:
            track_number = extract_track_number_from_filename(file_path)
            logger.info(
                "Track number was None; extracted from filename=%r -> %s",
                os.path.basename(file_path),
                track_number,
            )
        if not isinstance(track_number, int) or track_number < 1:
            logger.error(f"Invalid track number ({track_number}), defaulting to 1")
            track_number = 1

        logger.debug(f"FINAL track_number used for filename: {track_number}")
        album_info['track_number'] = track_number
        album_info['clean_track_name'] = clean_track_name
        logger.info(f"[FIX] Updated album_info track_number to {track_number} for consistent metadata")

        final_path, _ = build_final_path_for_track(context, artist_context, album_info, file_ext)
        logger.info(f"Resolved path: '{final_path}'")
        context['_final_processed_path'] = final_path

        try:
            logger.warning(f"[Metadata Input] artist: '{artist_context.get('name', 'MISSING')}' (id: {artist_context.get('id', 'MISSING')})")
            if album_info:
                logger.warning(
                    f"[Metadata Input] album: '{album_info.get('album_name', 'MISSING')}', "
                    f"track#: {album_info.get('track_number', 'MISSING')}, disc#: {album_info.get('disc_number', 'MISSING')}, "
                    f"source: {album_info.get('source', 'unknown')}"
                )
            else:
                logger.info("[Metadata Input] album_info: None (single track)")
            enhance_file_metadata(file_path, context, artist_context, album_info, runtime=metadata_runtime)
        except Exception as meta_err:
            import traceback
            pp_logger.info(f"[inner] Metadata enhancement FAILED for {context_key}: {meta_err}\n{traceback.format_exc()}")
            wipe_source_tags(file_path)

        _enhance_source_info = context.get('track_info', {}).get('source_info') or {}
        if isinstance(_enhance_source_info, str):
            try:
                _enhance_source_info = json.loads(_enhance_source_info)
            except (json.JSONDecodeError, TypeError):
                _enhance_source_info = {}
        is_enhance_download = _enhance_source_info.get('enhance', False)

        logger.info(f"Moving '{os.path.basename(file_path)}' to '{final_path}'")
        if os.path.exists(final_path):
            if not os.path.exists(file_path):
                logger.info(f"[Protection] Destination exists and source already gone - file already transferred: {os.path.basename(final_path)}")
                return
            try:
                from mutagen import File as MutagenFile
                existing_file = MutagenFile(final_path)
                has_metadata = existing_file is not None and len(existing_file.tags or {}) > 2
                if has_metadata and not is_enhance_download:
                    _replace_lower = config_manager.get('import.replace_lower_quality', False)
                    if _replace_lower:
                        _existing_tier = get_quality_tier_from_extension(final_path)
                        _incoming_tier = get_quality_tier_from_extension(file_path)
                        if _incoming_tier[1] < _existing_tier[1]:
                            logger.info(f"[Quality Replace] Replacing {_existing_tier[0]} with {_incoming_tier[0]}: {os.path.basename(final_path)}")
                            try:
                                os.remove(final_path)
                            except Exception as e:
                                logger.error(f"[Quality Replace] Could not remove existing file: {e}")
                        else:
                            logger.info(
                                f"[Protection] Existing file is same or better quality ({_existing_tier[0]} vs {_incoming_tier[0]}) - skipping: "
                                f"{os.path.basename(final_path)}"
                            )
                            try:
                                os.remove(file_path)
                            except FileNotFoundError:
                                pass
                            except Exception as e:
                                logger.error(f"[Protection] Error removing redundant file: {e}")
                            return
                    else:
                        logger.info(f"[Protection] Existing file already has metadata enhancement - skipping overwrite: {os.path.basename(final_path)}")
                        logger.info(f"[Protection] Removing redundant download file: {os.path.basename(file_path)}")
                        try:
                            os.remove(file_path)
                        except FileNotFoundError:
                            logger.error(f"[Protection] Could not remove redundant file (already gone): {file_path}")
                        except Exception as e:
                            logger.error(f"[Protection] Error removing redundant file: {e}")
                        return
                elif is_enhance_download:
                    logger.info(f"[Enhance] Quality enhance mode — replacing existing file: {os.path.basename(final_path)}")
                    try:
                        os.remove(final_path)
                    except Exception as e:
                        logger.error(f"[Enhance] Could not remove existing file for replacement: {e}")
                else:
                    logger.info(f"[Protection] Existing file lacks metadata - safe to overwrite: {os.path.basename(final_path)}")
                    try:
                        os.remove(final_path)
                    except FileNotFoundError:
                        pass
            except Exception as check_error:
                logger.error(f"[Protection] Error checking existing file metadata, proceeding with overwrite: {check_error}")
                try:
                    if os.path.exists(final_path):
                        os.remove(final_path)
                except Exception as e:
                    logger.error(f"[Protection] Failed to remove existing file for overwrite: {e}")

        if not os.path.exists(file_path):
            if os.path.exists(final_path):
                logger.info(f"[Pre-Move] Source already gone and destination exists - another thread completed transfer: {os.path.basename(final_path)}")
                download_cover_art(album_info, os.path.dirname(final_path), context)
                generate_lrc_file(final_path, context, artist_context, album_info)
                return
            expected_dir = os.path.dirname(final_path)
            expected_stem = os.path.splitext(os.path.basename(final_path))[0]
            expected_ext = os.path.splitext(final_path)[1]
            found_variant = None
            check_exts = {expected_ext}
            if expected_ext == '.flac' and config_manager.get('lossy_copy.enabled', False) and config_manager.get('lossy_copy.delete_original', False):
                _lossy_ext_map = {'mp3': '.mp3', 'opus': '.opus', 'aac': '.m4a'}
                _lossy_codec = config_manager.get('lossy_copy.codec', 'mp3')
                check_exts.add(_lossy_ext_map.get(_lossy_codec, '.mp3'))
            if os.path.exists(expected_dir):
                for f in os.listdir(expected_dir):
                    f_ext = os.path.splitext(f)[1].lower()
                    if f_ext in check_exts and os.path.splitext(f)[0].startswith(expected_stem):
                        found_variant = os.path.join(expected_dir, f)
                        break
            if found_variant:
                logger.debug(f"[Pre-Move] Source gone but found variant in destination (stream processor handled it): {os.path.basename(found_variant)}")
                context['_final_processed_path'] = found_variant
                download_cover_art(album_info, expected_dir, context)
                generate_lrc_file(found_variant, context, artist_context, album_info)
                return
            logger.warning(f"[Pre-Move] Source file gone and no matching file in destination: {os.path.basename(file_path)}")
            raise FileNotFoundError(f"Source file vanished before move and destination does not exist: {file_path}")

        safe_move_file(file_path, final_path)

        if is_enhance_download and _enhance_source_info.get('original_file_path'):
            original_enhance_path = _enhance_source_info['original_file_path']
            if os.path.normpath(original_enhance_path) != os.path.normpath(final_path) and os.path.exists(original_enhance_path):
                try:
                    os.remove(original_enhance_path)
                    old_fmt = os.path.splitext(original_enhance_path)[1]
                    new_fmt = os.path.splitext(final_path)[1]
                    logger.info(f"[Enhance] Upgraded {old_fmt} → {new_fmt}: {os.path.basename(final_path)}")
                except Exception as e:
                    logger.error(f"[Enhance] Could not remove old-format file: {e}")
            elif is_enhance_download:
                old_fmt = _enhance_source_info.get('original_format', 'unknown')
                new_fmt = os.path.splitext(final_path)[1]
                logger.info(f"[Enhance] Replaced in-place ({old_fmt} → {new_fmt}): {os.path.basename(final_path)}")

        download_cover_art(album_info, os.path.dirname(final_path), context)
        generate_lrc_file(final_path, context, artist_context, album_info)

        if config_manager.get('post_processing.replaygain_enabled', False):
            try:
                from core.replaygain import analyze_track as _rg_analyze, write_replaygain_tags as _rg_write, is_ffmpeg_available as _rg_ffmpeg_ok, RG_REFERENCE_LUFS as _RG_REF
                if _rg_ffmpeg_ok():
                    lufs, peak_dbfs = _rg_analyze(final_path)
                    gain_db = _RG_REF - lufs
                    _rg_write(final_path, gain_db, peak_dbfs)
                    pp_logger.info(f"ReplayGain: {gain_db:+.2f} dB, peak {peak_dbfs:.2f} dBFS — {os.path.basename(final_path)}")
            except Exception as rg_err:
                pp_logger.debug(f"ReplayGain analysis skipped: {rg_err}")

        downsampled_path = downsample_hires_flac(final_path, context)
        if downsampled_path:
            final_path = downsampled_path
            context['_final_processed_path'] = final_path

        blasphemy_path = create_lossy_copy(final_path)
        if blasphemy_path:
            context['_final_processed_path'] = blasphemy_path

        downloads_path = docker_resolve_path(config_manager.get('soulseek.download_path', './downloads'))
        cleanup_empty_directories(downloads_path, file_path)

        logger.info(f"Post-processing complete for: {context.get('_final_processed_path', final_path)}")

        emit_track_downloaded(context, automation_engine)
        record_library_history_download(context)
        record_download_provenance(context)
        record_soulsync_library_entry(context, artist_context, album_info)

        try:
            if not playlist_folder_mode:
                completed_path = context.get('_final_processed_path', final_path)
                record_retag_download(context, artist_context, album_info, completed_path)
        except Exception as retag_err:
            logger.error(f"[Post-Process] Retag data capture failed (non-fatal): {retag_err}")

        try:
            completed_path = context.get('_final_processed_path', final_path)
            batch_id_for_repair = context.get('batch_id')
            if completed_path and batch_id_for_repair and repair_worker:
                album_folder = os.path.dirname(str(completed_path))
                if album_folder:
                    repair_worker.register_folder(batch_id_for_repair, album_folder)
        except Exception as repair_err:
            logger.error(f"[Post-Process] Repair folder registration failed: {repair_err}")

        try:
            completed_path = context.get('_final_processed_path', final_path)
            batch_id_for_consistency = context.get('batch_id')
            if completed_path and batch_id_for_consistency and album_info and album_info.get('is_album'):
                _file_info = {
                    'path': str(completed_path),
                    'track_number': album_info.get('track_number', 1),
                    'disc_number': album_info.get('disc_number', 1),
                    'title': get_import_clean_title(
                        context,
                        album_info=album_info,
                        default=album_info.get('clean_track_name', ''),
                    ),
                }
                with tasks_lock:
                    if batch_id_for_consistency in download_batches:
                        download_batches[batch_id_for_consistency].setdefault('_consistency_files', []).append(_file_info)
        except Exception as cons_err:
            logger.error(f"[Post-Process] Album consistency registration failed: {cons_err}")

        try:
            check_and_remove_from_wishlist(context)
        except Exception as wishlist_error:
            logger.error(f"[Post-Process] Error checking wishlist removal: {wishlist_error}")

        task_id = context.get('task_id')
        batch_id = context.get('batch_id')
        if task_id and batch_id:
            logger.info(f"[Post-Process] Calling completion callback for task {task_id} in batch {batch_id}")
            with tasks_lock:
                if task_id in download_tasks:
                    download_tasks[task_id]['stream_processed'] = True
                    download_tasks[task_id]['status'] = 'completed'
                    logger.info(f"[Post-Process] Marked task {task_id} as completed")
            _notify_download_completed(batch_id, task_id, success=True)

    except Exception as e:
        import traceback
        pp_logger.info(f"[inner] EXCEPTION in post-processing for {context_key}: {e}")
        pp_logger.info(traceback.format_exc())
        logger.error(f"\nCRITICAL ERROR in post-processing for {context_key}: {e}")
        traceback.print_exc()

        source_exists = os.path.exists(file_path) if file_path else False
        if source_exists:
            if context_key in processed_download_ids:
                processed_download_ids.remove(context_key)
                logger.warning(f"Removed {context_key} from processed set - will retry on next check")
            with matched_context_lock:
                if context_key not in matched_downloads_context:
                    matched_downloads_context[context_key] = context
                    logger.warning(f"Re-added {context_key} to context for retry")
        else:
            logger.warning(f"Source file gone, not retrying: {context_key}")
    finally:
        file_lock.release()
        with post_process_locks_lock:
            post_process_locks.pop(context_key, None)


def post_process_matched_download_with_verification(context_key, context, file_path, task_id, batch_id, runtime, metadata_runtime=None):
    on_download_completed = getattr(runtime, "on_download_completed", None)

    def _notify_download_completed(batch_id, task_id, success=True):
        if on_download_completed:
            on_download_completed(batch_id, task_id, success=success)

    logger = pp_logger
    try:
        original_task_id = context.pop('task_id', None)
        original_batch_id = context.pop('batch_id', None)
        post_process_matched_download(context_key, context, file_path, runtime, metadata_runtime=metadata_runtime)
        if original_task_id:
            context['task_id'] = original_task_id
        if original_batch_id:
            context['batch_id'] = original_batch_id

        if context.get('_race_guard_failed'):
            logger.info(f"Race guard: source file gone for task {task_id} — marking as failed")
            with tasks_lock:
                if task_id in download_tasks:
                    download_tasks[task_id]['status'] = 'failed'
                    download_tasks[task_id]['error_message'] = 'Source file was already processed or removed by another task'
            with matched_context_lock:
                if context_key in matched_downloads_context:
                    del matched_downloads_context[context_key]
            _notify_download_completed(batch_id, task_id, success=False)
            return

        if context.get('_acoustid_quarantined'):
            failure_msg = context.get('_acoustid_failure_msg', 'AcoustID verification failed')
            logger.info(f"File was quarantined by AcoustID verification (task={task_id}): {failure_msg}")
            with tasks_lock:
                if task_id in download_tasks:
                    download_tasks[task_id]['status'] = 'failed'
                    download_tasks[task_id]['error_message'] = f"AcoustID verification failed: {failure_msg}"
            with matched_context_lock:
                if context_key in matched_downloads_context:
                    del matched_downloads_context[context_key]
            _notify_download_completed(batch_id, task_id, success=False)
            return

        if context.get('_simple_download_completed'):
            expected_final_path = context.get('_final_path')
            if expected_final_path and os.path.exists(expected_final_path):
                with tasks_lock:
                    if task_id in download_tasks:
                        _mark_task_completed(task_id, context.get('track_info'))
                with matched_context_lock:
                    if context_key in matched_downloads_context:
                        del matched_downloads_context[context_key]
                _notify_download_completed(batch_id, task_id, success=True)
                return
            logger.info(
                f"FAILED simple download file not found at: {expected_final_path} "
                f"(task={task_id}, context={context_key})"
            )
            with tasks_lock:
                if task_id in download_tasks:
                    download_tasks[task_id]['status'] = 'failed'
                    download_tasks[task_id]['error_message'] = (
                        f"Downloaded file not found at expected location: {os.path.basename(expected_final_path)}"
                    )
            with matched_context_lock:
                if context_key in matched_downloads_context:
                    del matched_downloads_context[context_key]
            _notify_download_completed(batch_id, task_id, success=False)
            return

        expected_final_path = context.get('_final_processed_path')
        if not expected_final_path:
            logger.info(f"No _final_processed_path in context for task {task_id} — cannot verify, assuming success")
            with tasks_lock:
                if task_id in download_tasks:
                    _mark_task_completed(task_id, context.get('track_info'))
            with matched_context_lock:
                if context_key in matched_downloads_context:
                    del matched_downloads_context[context_key]
            _notify_download_completed(batch_id, task_id, success=True)
            return

        if os.path.exists(expected_final_path):
            redownload_ctx = None
            with tasks_lock:
                if task_id in download_tasks:
                    _mark_task_completed(task_id, context.get('track_info'))
                    download_tasks[task_id]['metadata_enhanced'] = True
                    redownload_ctx = download_tasks[task_id].get('_redownload_context')

            with matched_context_lock:
                if context_key in matched_downloads_context:
                    del matched_downloads_context[context_key]

            if redownload_ctx:
                try:
                    old_path = redownload_ctx.get('old_file_path')
                    lib_track_id = redownload_ctx.get('library_track_id')
                    if redownload_ctx.get('delete_old_file') and old_path and os.path.exists(old_path):
                        if os.path.normpath(old_path) != os.path.normpath(expected_final_path):
                            os.remove(old_path)
                            logger.info(f"[Redownload] Deleted old file: {old_path}")
                    if lib_track_id and expected_final_path:
                        _rd_db = get_database()
                        _rd_conn = _rd_db._get_connection()
                        _rd_cursor = _rd_conn.cursor()
                        _rd_cursor.execute(
                            """
                            UPDATE tracks SET file_path = ?, updated_at = CURRENT_TIMESTAMP
                            WHERE id = ?
                            """,
                            (expected_final_path, lib_track_id),
                        )
                        _rd_conn.commit()
                        _rd_conn.close()
                        logger.info(f"[Redownload] Updated DB path for track {lib_track_id}")
                except Exception as e:
                    logger.error(f"[Redownload] Post-processing hook error: {e}")

            _notify_download_completed(batch_id, task_id, success=True)
        else:
            track_name = get_import_clean_title(context, default=context_key)
            logger.info(f"FAILED verification for '{track_name}' (task={task_id})")
            logger.info(f"  expected_final_path: {expected_final_path}")
            logger.info(f"  file_path (source): {file_path}, exists={os.path.exists(file_path)}")
            logger.info(
                f"  is_album={context.get('is_album_download', False)}, "
                f"has_clean_data={get_import_has_clean_metadata(context)}"
            )
            expected_dir = os.path.dirname(expected_final_path)
            if os.path.exists(expected_dir):
                dir_contents = os.listdir(expected_dir)
                logger.info(f"  directory contains {len(dir_contents)} files: {dir_contents[:20]}")
            else:
                logger.info(f"  directory does not exist: {expected_dir}")

            with tasks_lock:
                if task_id in download_tasks:
                    download_tasks[task_id]['status'] = 'failed'
                    download_tasks[task_id]['error_message'] = (
                        f'File verification failed: expected file at {os.path.basename(expected_final_path)} but it was not found after processing'
                    )

            with matched_context_lock:
                if context_key in matched_downloads_context:
                    del matched_downloads_context[context_key]

            _notify_download_completed(batch_id, task_id, success=False)
    except Exception as e:
        import traceback
        logger.info(f"EXCEPTION in post-processing for '{context_key}' (task={task_id}): {e}")
        logger.info(traceback.format_exc())
        with tasks_lock:
            if task_id in download_tasks:
                download_tasks[task_id]['status'] = 'failed'
                download_tasks[task_id]['error_message'] = f"Post-processing verification failed: {str(e)}"
        with matched_context_lock:
            if context_key in matched_downloads_context:
                del matched_downloads_context[context_key]
        _notify_download_completed(batch_id, task_id, success=False)
