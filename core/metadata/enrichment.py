"""Compatibility facade and orchestration for metadata enrichment."""

from __future__ import annotations

import os
from types import SimpleNamespace
from typing import Any

from core.metadata.artwork import embed_album_art_metadata
from core.metadata.common import (
    get_config_manager,
    get_file_lock,
    get_mutagen_symbols,
    is_vorbis_like,
    save_audio_file,
    strip_all_non_audio_tags,
    verify_metadata_written,
)
from core.metadata.source import embed_source_ids, extract_source_metadata
from utils.logging_config import get_logger as _create_logger


__all__ = [
    "build_metadata_enrichment_runtime",
    "enhance_file_metadata",
    "extract_source_metadata",
    "embed_source_ids",
]


logger = _create_logger("metadata.enrichment")


def build_metadata_enrichment_runtime(
    *,
    mb_worker: Any | None = None,
    deezer_worker: Any | None = None,
    audiodb_worker: Any | None = None,
    tidal_client: Any | None = None,
    hifi_client: Any | None = None,
    qobuz_enrichment_worker: Any | None = None,
    lastfm_worker: Any | None = None,
    genius_worker: Any | None = None,
    spotify_enrichment_worker: Any | None = None,
    itunes_enrichment_worker: Any | None = None,
) -> SimpleNamespace:
    """Build the runtime object consumed by core.metadata.enrichment/source."""
    return SimpleNamespace(
        mb_worker=mb_worker,
        deezer_worker=deezer_worker,
        audiodb_worker=audiodb_worker,
        tidal_client=tidal_client,
        hifi_client=hifi_client,
        qobuz_enrichment_worker=qobuz_enrichment_worker,
        lastfm_worker=lastfm_worker,
        genius_worker=genius_worker,
        spotify_enrichment_worker=spotify_enrichment_worker,
        itunes_enrichment_worker=itunes_enrichment_worker,
    )


def enhance_file_metadata(file_path: str, context: dict, artist: dict, album_info: dict, runtime=None) -> bool:
    cfg = get_config_manager()
    if cfg.get("metadata_enhancement.enabled", True) is False:
        logger.warning("Metadata enhancement disabled in config.")
        return True

    if album_info is None:
        album_info = {}

    symbols = get_mutagen_symbols()
    if not symbols:
        logger.error("Mutagen is unavailable, cannot enhance metadata.")
        return False

    file_lock = get_file_lock(file_path)
    with file_lock:
        logger.info("Enhancing metadata for: %s", os.path.basename(file_path))
        try:
            strip_all_non_audio_tags(file_path)
            audio_file = symbols.File(file_path)
            if audio_file is None:
                logger.error("Could not load audio file with Mutagen: %s", file_path)
                return False

            if hasattr(audio_file, "clear_pictures"):
                audio_file.clear_pictures()

            if audio_file.tags is not None:
                if len(audio_file.tags) > 0:
                    tag_keys = list(audio_file.tags.keys())[:15]
                    logger.info("Clearing %s existing tags: %s", len(audio_file.tags), ", ".join(str(k) for k in tag_keys))
                audio_file.tags.clear()
            else:
                audio_file.add_tags()

            save_audio_file(audio_file, symbols)

            metadata = extract_source_metadata(context, artist, album_info)
            if not metadata:
                logger.error("Could not extract source metadata, saving with cleared tags.")
                save_audio_file(audio_file, symbols)
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
            elif is_vorbis_like(audio_file, symbols):
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

            embed_source_ids(audio_file, metadata, context, runtime=runtime)

            if album_info is not None and metadata.get("musicbrainz_release_id"):
                album_info["musicbrainz_release_id"] = metadata["musicbrainz_release_id"]

            if cfg.get("metadata_enhancement.embed_album_art", True):
                embed_album_art_metadata(audio_file, metadata)

            quality = context.get("_audio_quality", "")
            if quality and cfg.get("metadata_enhancement.tags.quality_tag", True) is not False:
                if isinstance(audio_file.tags, symbols.ID3):
                    audio_file.tags.add(symbols.TXXX(encoding=3, desc="QUALITY", text=[quality]))
                elif is_vorbis_like(audio_file, symbols):
                    audio_file["quality"] = [quality]
                elif isinstance(audio_file, symbols.MP4):
                    audio_file["----:com.apple.iTunes:QUALITY"] = [symbols.MP4FreeForm(quality.encode("utf-8"))]

            save_audio_file(audio_file, symbols)

            verified = verify_metadata_written(file_path)
            if verified:
                logger.info("Metadata enhanced successfully.")
            else:
                logger.info("Metadata saved but verification found issues (see above).")
            return True
        except Exception as exc:
            import traceback

            logger.error("Error enhancing metadata for %s: %s", file_path, exc)
            logger.error("[Metadata Debug] Exception type: %s", type(exc).__name__)
            logger.info("[Metadata Debug] File exists: %s", os.path.exists(file_path))
            logger.warning("[Metadata Debug] Artist: %s", artist.get("name", "MISSING") if artist else "None")
            logger.warning("[Metadata Debug] Album info: %s", album_info.get("album_name", "MISSING") if album_info else "None")
            logger.error("[Metadata Debug] Traceback:\n%s", traceback.format_exc())
            return False
