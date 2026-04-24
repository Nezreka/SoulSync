"""Compatibility facade and orchestration for metadata enrichment."""

from __future__ import annotations

import os

from core.metadata_artwork import (
    download_cover_art as _download_cover_art_impl,
    embed_album_art_metadata,
)
from core.metadata_common import (
    _get_config_manager,
    _get_file_lock,
    _get_image_dimensions as _common_get_image_dimensions,
    _get_logger,
    _get_mutagen_symbols,
    _is_ogg_opus as _common_is_ogg_opus,
    _is_vorbis_like,
    _save_audio_file,
    _strip_all_non_audio_tags,
    _verify_metadata_written,
    wipe_source_tags as _common_wipe_source_tags,
)
from core.metadata_lyrics import generate_lrc_file as _generate_lrc_file_impl
from core.metadata_source import embed_source_ids, extract_source_metadata


logger = _get_logger()


def _get_image_dimensions(data: bytes):
    return _common_get_image_dimensions(data)


def _is_ogg_opus(audio_file):
    return _common_is_ogg_opus(audio_file)


def wipe_source_tags(file_path: str) -> bool:
    return _common_wipe_source_tags(file_path)


def generate_lrc_file(file_path: str, context: dict, artist: dict, album_info: dict) -> bool:
    return _generate_lrc_file_impl(file_path, context, artist, album_info)


def download_cover_art(album_info: dict, target_dir: str, context: dict = None):
    return _download_cover_art_impl(album_info, target_dir, context)


def enhance_file_metadata(file_path: str, context: dict, artist: dict, album_info: dict) -> bool:
    cfg = _get_config_manager()
    logger_ = _get_logger()
    if cfg.get("metadata_enhancement.enabled", True) is False:
        logger_.warning("Metadata enhancement disabled in config.")
        return True

    if album_info is None:
        album_info = {}

    symbols = _get_mutagen_symbols()
    if not symbols:
        logger_.error("Mutagen is unavailable, cannot enhance metadata.")
        return False

    file_lock = _get_file_lock(file_path)
    with file_lock:
        logger_.info("Enhancing metadata for: %s", os.path.basename(file_path))
        try:
            _strip_all_non_audio_tags(file_path)
            audio_file = symbols.File(file_path)
            if audio_file is None:
                logger_.error("Could not load audio file with Mutagen: %s", file_path)
                return False

            if hasattr(audio_file, "clear_pictures"):
                audio_file.clear_pictures()

            if audio_file.tags is not None:
                if len(audio_file.tags) > 0:
                    tag_keys = list(audio_file.tags.keys())[:15]
                    logger_.info("Clearing %s existing tags: %s", len(audio_file.tags), ", ".join(str(k) for k in tag_keys))
                audio_file.tags.clear()
            else:
                audio_file.add_tags()

            _save_audio_file(audio_file, symbols)

            metadata = extract_source_metadata(context, artist, album_info)
            if not metadata:
                logger_.error("Could not extract source metadata, saving with cleared tags.")
                _save_audio_file(audio_file, symbols)
                return True

            track_num_str = f"{metadata.get('track_number', 1)}/{metadata.get('total_tracks', 1)}"
            write_multi = cfg.get("metadata_enhancement.tags.write_multi_artist", False)
            artists_list = metadata.get("_artists_list", [])

            if isinstance(audio_file.tags, symbols.ID3):
                if metadata.get("title"):
                    audio_file.tags.add(symbols.TIT2(encoding=3, text=[metadata["title"]]))
                if metadata.get("artist"):
                    audio_file.tags.add(symbols.TPE1(encoding=3, text=[metadata["artist"]]))
                    if write_multi and len(artists_list) > 1:
                        audio_file.tags.add(symbols.TPE1(encoding=3, text=artists_list))
                if metadata.get("album_artist"):
                    audio_file.tags.add(symbols.TPE2(encoding=3, text=[metadata["album_artist"]]))
                if metadata.get("album"):
                    audio_file.tags.add(symbols.TALB(encoding=3, text=[metadata["album"]]))
                if metadata.get("date"):
                    audio_file.tags.add(symbols.TDRC(encoding=3, text=[metadata["date"]]))
                if metadata.get("genre"):
                    audio_file.tags.add(symbols.TCON(encoding=3, text=[metadata["genre"]]))
                audio_file.tags.add(symbols.TRCK(encoding=3, text=[track_num_str]))
                if metadata.get("disc_number"):
                    audio_file.tags.add(symbols.TPOS(encoding=3, text=[str(metadata["disc_number"])]))
            elif _is_vorbis_like(audio_file, symbols):
                if metadata.get("title"):
                    audio_file["title"] = [metadata["title"]]
                if metadata.get("artist"):
                    audio_file["artist"] = [metadata["artist"]]
                    if write_multi and len(artists_list) > 1:
                        audio_file["artists"] = artists_list
                if metadata.get("album_artist"):
                    audio_file["albumartist"] = [metadata["album_artist"]]
                if metadata.get("album"):
                    audio_file["album"] = [metadata["album"]]
                if metadata.get("date"):
                    audio_file["date"] = [metadata["date"]]
                if metadata.get("genre"):
                    audio_file["genre"] = [metadata["genre"]]
                audio_file["tracknumber"] = [track_num_str]
                if metadata.get("disc_number"):
                    audio_file["discnumber"] = [str(metadata["disc_number"])]
            elif isinstance(audio_file, symbols.MP4):
                if metadata.get("title"):
                    audio_file["\xa9nam"] = [metadata["title"]]
                if metadata.get("artist"):
                    audio_file["\xa9ART"] = artists_list if (write_multi and len(artists_list) > 1) else [metadata["artist"]]
                if metadata.get("album_artist"):
                    audio_file["aART"] = [metadata["album_artist"]]
                if metadata.get("album"):
                    audio_file["\xa9alb"] = [metadata["album"]]
                if metadata.get("date"):
                    audio_file["\xa9day"] = [metadata["date"]]
                if metadata.get("genre"):
                    audio_file["\xa9gen"] = [metadata["genre"]]
                audio_file["trkn"] = [(metadata.get("track_number", 1), metadata.get("total_tracks", 1))]
                if metadata.get("disc_number"):
                    audio_file["disk"] = [(metadata["disc_number"], 0)]

            embed_source_ids(audio_file, metadata, context)

            if album_info is not None and metadata.get("musicbrainz_release_id"):
                album_info["musicbrainz_release_id"] = metadata["musicbrainz_release_id"]

            if cfg.get("metadata_enhancement.embed_album_art", True):
                embed_album_art_metadata(audio_file, metadata)

            quality = context.get("_audio_quality", "")
            if quality and cfg.get("metadata_enhancement.tags.quality_tag", True) is not False:
                if isinstance(audio_file.tags, symbols.ID3):
                    audio_file.tags.add(symbols.TXXX(encoding=3, desc="QUALITY", text=[quality]))
                elif _is_vorbis_like(audio_file, symbols):
                    audio_file["quality"] = [quality]
                elif isinstance(audio_file, symbols.MP4):
                    audio_file["----:com.apple.iTunes:QUALITY"] = [symbols.MP4FreeForm(quality.encode("utf-8"))]

            _save_audio_file(audio_file, symbols)

            verified = _verify_metadata_written(file_path)
            if verified:
                logger_.info("Metadata enhanced successfully.")
            else:
                logger_.info("Metadata saved but verification found issues (see above).")
            return True
        except Exception as exc:
            import traceback

            logger_.error("Error enhancing metadata for %s: %s", file_path, exc)
            logger_.error("[Metadata Debug] Exception type: %s", type(exc).__name__)
            logger_.info("[Metadata Debug] File exists: %s", os.path.exists(file_path))
            logger_.warning("[Metadata Debug] Artist: %s", artist.get("name", "MISSING") if artist else "None")
            logger_.warning("[Metadata Debug] Album info: %s", album_info.get("album_name", "MISSING") if album_info else "None")
            logger_.error("[Metadata Debug] Traceback:\n%s", traceback.format_exc())
            return False
