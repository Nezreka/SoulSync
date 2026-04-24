"""Album artwork helpers for metadata enrichment."""

from __future__ import annotations

import os
import re
import urllib.request

from core.import_context import get_import_context_album
from core.metadata_common import (
    get_config_manager,
    get_image_dimensions,
    get_logger,
    get_mutagen_symbols,
)

__all__ = [
    "embed_album_art_metadata",
    "download_cover_art",
]


def embed_album_art_metadata(audio_file, metadata: dict):
    cfg = get_config_manager()
    logger_ = get_logger()
    symbols = get_mutagen_symbols()
    if not symbols:
        return

    try:
        image_data = None
        mime_type = None

        release_mbid = metadata.get("musicbrainz_release_id")
        if release_mbid and cfg.get("metadata_enhancement.prefer_caa_art", False):
            try:
                caa_url = f"https://coverartarchive.org/release/{release_mbid}/front"
                req = urllib.request.Request(caa_url, headers={"Accept": "image/*"})
                with urllib.request.urlopen(req, timeout=10) as response:
                    image_data = response.read()
                    mime_type = response.info().get_content_type() or "image/jpeg"
                if not image_data or len(image_data) <= 1000:
                    image_data = None
            except Exception:
                image_data = None

        if not image_data:
            art_url = metadata.get("album_art_url")
            if not art_url:
                logger_.warning("No album art URL available for embedding.")
                return
            with urllib.request.urlopen(art_url, timeout=10) as response:
                image_data = response.read()
                mime_type = response.info().get_content_type() or "image/jpeg"

        if not image_data:
            logger_.error("Failed to download album art data.")
            return

        if isinstance(audio_file.tags, symbols.ID3):
            audio_file.tags.add(symbols.APIC(encoding=3, mime=mime_type, type=3, desc="Cover", data=image_data))
        elif isinstance(audio_file, symbols.FLAC):
            picture = symbols.Picture()
            picture.data = image_data
            picture.type = 3
            picture.mime = mime_type
            width, height = get_image_dimensions(image_data)
            picture.width = width or 640
            picture.height = height or 640
            picture.depth = 24
            audio_file.add_picture(picture)
        elif isinstance(audio_file, symbols.MP4):
            fmt = symbols.MP4Cover.FORMAT_JPEG if "jpeg" in mime_type else symbols.MP4Cover.FORMAT_PNG
            audio_file["covr"] = [symbols.MP4Cover(image_data, imageformat=fmt)]

        logger_.info("Album art successfully embedded.")
    except Exception as exc:
        logger_.error("Error embedding album art: %s", exc)


def download_cover_art(album_info: dict, target_dir: str, context: dict = None):
    cfg = get_config_manager()
    logger_ = get_logger()
    if cfg.get("metadata_enhancement.cover_art_download", True) is False:
        return

    try:
        cover_path = os.path.join(target_dir, "cover.jpg")
        album_info = album_info or {}
        release_mbid = album_info.get("musicbrainz_release_id")
        prefer_caa = cfg.get("metadata_enhancement.prefer_caa_art", False)

        if os.path.exists(cover_path):
            if release_mbid and prefer_caa:
                try:
                    existing_size = os.path.getsize(cover_path)
                    if existing_size > 200_000:
                        return
                    is_upgrade = True
                except Exception:
                    return
            else:
                return
        else:
            is_upgrade = False

        image_data = None
        if release_mbid and prefer_caa:
            try:
                caa_url = f"https://coverartarchive.org/release/{release_mbid}/front"
                req = urllib.request.Request(caa_url, headers={"Accept": "image/*"})
                with urllib.request.urlopen(req, timeout=10) as response:
                    image_data = response.read()
                if not image_data or len(image_data) <= 1000:
                    image_data = None
            except Exception:
                image_data = None

        if is_upgrade and not image_data:
            logger_.error("CAA upgrade failed - keeping existing cover.jpg")
            return

        if not image_data:
            art_url = album_info.get("album_image_url")
            if not art_url and context:
                album_ctx = get_import_context_album(context)
                art_url = album_ctx.get("image_url")
                if not art_url and album_ctx.get("images"):
                    images = album_ctx.get("images", [])
                    if images and isinstance(images[0], dict):
                        art_url = images[0].get("url", "")
                if art_url:
                    logger_.info("Using cover art URL from album context")
            if art_url and "i.scdn.co" in art_url:
                try:
                    from core.spotify_client import _upgrade_spotify_image_url

                    art_url = _upgrade_spotify_image_url(art_url)
                except Exception:
                    pass
            elif art_url and "mzstatic.com" in art_url:
                art_url = re.sub(r"\d+x\d+bb", "3000x3000bb", art_url)
            if not art_url:
                logger_.warning("No cover art URL available for download.")
                return
            with urllib.request.urlopen(art_url, timeout=10) as response:
                image_data = response.read()

        if not image_data:
            return

        with open(cover_path, "wb") as handle:
            handle.write(image_data)
        logger_.info("Cover art downloaded to: %s", cover_path)
    except Exception as exc:
        logger_.error("Error downloading cover.jpg: %s", exc)
