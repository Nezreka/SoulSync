"""Album artwork helpers for metadata enrichment."""

from __future__ import annotations

import os
import re
import urllib.request
from ipaddress import ip_address
from urllib.parse import quote, urlparse

from core.imports.context import get_import_context_album
from core.metadata.common import (
    get_config_manager,
    get_image_dimensions,
    get_mutagen_symbols,
)
from utils.logging_config import get_logger as _create_logger

__all__ = [
    "embed_album_art_metadata",
    "download_cover_art",
    "is_internal_image_host",
    "is_image_proxy_url",
    "normalize_image_url",
]


logger = _create_logger("metadata.artwork")


def normalize_image_url(thumb_url: str | None) -> str | None:
    """Convert media-server image URLs into browser-safe URLs."""
    if not thumb_url:
        return None

    try:
        if is_image_proxy_url(thumb_url):
            # Already normalized for browser use; avoid wrapping it in another proxy layer.
            return thumb_url

        # Check if it's a localhost URL or relative path that needs fixing
        needs_fixing = (
            thumb_url.startswith('http://localhost:') or
            thumb_url.startswith('https://localhost:') or
            thumb_url.startswith('http://127.0.0.1:') or
            thumb_url.startswith('https://127.0.0.1:') or
            thumb_url.startswith('http://host.docker.internal:') or
            thumb_url.startswith('https://host.docker.internal:') or
            (thumb_url.startswith('http://') and is_internal_image_host(thumb_url)) or
            thumb_url.startswith('/library/') or  # Plex relative paths
            thumb_url.startswith('/Items/') or    # Jellyfin relative paths
            thumb_url.startswith('/api/') or      # Old Navidrome API paths
            thumb_url.startswith('/rest/')        # Navidrome Subsonic API paths
        )

        if needs_fixing:
            cfg = get_config_manager()
            active_server = cfg.get_active_media_server()
            logger.debug("Fixing URL: %s, Active server: %s", thumb_url, active_server)

            if active_server == 'plex':
                plex_config = cfg.get_plex_config()
                plex_base_url = plex_config.get('base_url', '')
                plex_token = plex_config.get('token', '')
                logger.info("Plex config - base_url: %s, token: %s...", plex_base_url, plex_token[:10])

                if plex_base_url and plex_token:
                    # Extract the path from URL
                    if thumb_url.startswith('/library/'):
                        # Already a path
                        path = thumb_url
                    else:
                        # Full localhost URL, extract path
                        parsed = urlparse(thumb_url)
                        path = parsed.path

                    # Construct proper Plex URL with token
                    fixed_url = f"{plex_base_url.rstrip('/')}{path}?X-Plex-Token={plex_token}"
                    logger.info("Fixed URL: %s", fixed_url)
                    return _browser_safe_image_url(fixed_url)

            elif active_server == 'jellyfin':
                jellyfin_config = cfg.get_jellyfin_config()
                jellyfin_base_url = jellyfin_config.get('base_url', '')
                jellyfin_token = jellyfin_config.get('api_key', '')
                logger.info(
                    "Jellyfin config - base_url: %s, token: %s...",
                    jellyfin_base_url,
                    jellyfin_token[:10] if jellyfin_token else 'None',
                )

                if jellyfin_base_url:
                    # Extract the path from URL
                    if thumb_url.startswith('/Items/') or thumb_url.startswith('/api/'):
                        # Already a path
                        path = thumb_url
                    else:
                        # Full localhost URL, extract path
                        parsed = urlparse(thumb_url)
                        path = parsed.path

                    # Construct proper Jellyfin URL with token
                    if jellyfin_token:
                        separator = '&' if '?' in path else '?'
                        fixed_url = f"{jellyfin_base_url.rstrip('/')}{path}{separator}X-Emby-Token={jellyfin_token}"
                    else:
                        fixed_url = f"{jellyfin_base_url.rstrip('/')}{path}"
                    logger.info("Fixed URL: %s", fixed_url)
                    return _browser_safe_image_url(fixed_url)

            elif active_server == 'navidrome':
                navidrome_config = cfg.get_navidrome_config()
                navidrome_base_url = navidrome_config.get('base_url', '')
                navidrome_username = navidrome_config.get('username', '')
                navidrome_password = navidrome_config.get('password', '')
                logger.info("Navidrome config - base_url: %s, username: %s", navidrome_base_url, navidrome_username)

                if navidrome_base_url and navidrome_username and navidrome_password:
                    # Extract the path from URL
                    if thumb_url.startswith('/rest/'):
                        # Already a Subsonic API path
                        path = thumb_url
                    else:
                        # Full localhost URL, extract path
                        parsed = urlparse(thumb_url)
                        path = parsed.path

                    # Generate Subsonic API authentication
                    import hashlib
                    import secrets
                    salt = secrets.token_hex(6)
                    token = hashlib.md5((navidrome_password + salt).encode()).hexdigest()

                    # Add authentication parameters to the URL
                    separator = '&' if '?' in path else '?'
                    auth_params = f"u={navidrome_username}&t={token}&s={salt}&v=1.16.1&c=SoulSync&f=json"

                    # Construct proper Navidrome Subsonic URL
                    fixed_url = f"{navidrome_base_url.rstrip('/')}{path}{separator}{auth_params}"
                    logger.info("Fixed URL: %s", fixed_url)
                    return _browser_safe_image_url(fixed_url)

            logger.warning("No configuration found for %s or unsupported server type", active_server)

        # Return a browser-safe URL even if no server-specific rebuild was possible.
        return _browser_safe_image_url(thumb_url)

    except Exception as exc:
        logger.error("Error fixing image URL '%s': %s", thumb_url, exc)
        return _browser_safe_image_url(thumb_url)


def is_image_proxy_url(url: str) -> bool:
    """Return True for SoulSync image-proxy URLs, absolute or relative."""
    if not url:
        return False

    try:
        parsed = urlparse(url)
        return parsed.path == '/api/image-proxy'
    except Exception:
        return False


def is_internal_image_host(url: str) -> bool:
    """Return True when an image URL points at a host the browser likely cannot reach directly."""
    try:
        parsed = urlparse(url)
        host = (parsed.hostname or '').strip('[]').lower()
        if not host:
            return False

        if host in {'localhost', '127.0.0.1', '::1', 'host.docker.internal'}:
            return True

        # Single-label hosts are usually Docker service names or local LAN aliases.
        if '.' not in host:
            return True

        try:
            ip = ip_address(host)
            return ip.is_loopback or ip.is_private or ip.is_link_local or ip.is_reserved
        except ValueError:
            return False
    except Exception:
        return False


def _browser_safe_image_url(url: str) -> str:
    """Return a browser-safe image URL, proxying internal hosts through SoulSync."""
    if not url:
        return url

    if is_image_proxy_url(url):
        return url

    if url.startswith('/api/image-proxy?url='):
        return url

    if url.startswith('http://') or url.startswith('https://'):
        if is_internal_image_host(url):
            return f"/api/image-proxy?url={quote(url, safe='')}"
        return url

    # Relative media-server paths should already have been expanded before this point.
    return url


def embed_album_art_metadata(audio_file, metadata: dict):
    cfg = get_config_manager()
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
                logger.warning("No album art URL available for embedding.")
                return
            with urllib.request.urlopen(art_url, timeout=10) as response:
                image_data = response.read()
                mime_type = response.info().get_content_type() or "image/jpeg"

        if not image_data:
            logger.error("Failed to download album art data.")
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

        logger.info("Album art successfully embedded.")
    except Exception as exc:
        logger.error("Error embedding album art: %s", exc)


def download_cover_art(album_info: dict, target_dir: str, context: dict = None):
    cfg = get_config_manager()
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
            logger.error("CAA upgrade failed - keeping existing cover.jpg")
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
                    logger.info("Using cover art URL from album context")
            if art_url and "i.scdn.co" in art_url:
                try:
                    from core.spotify_client import _upgrade_spotify_image_url

                    art_url = _upgrade_spotify_image_url(art_url)
                except Exception:
                    pass
            elif art_url and "mzstatic.com" in art_url:
                art_url = re.sub(r"\d+x\d+bb", "3000x3000bb", art_url)
            if not art_url:
                logger.warning("No cover art URL available for download.")
                return
            with urllib.request.urlopen(art_url, timeout=10) as response:
                image_data = response.read()

        if not image_data:
            return

        with open(cover_path, "wb") as handle:
            handle.write(image_data)
        logger.info("Cover art downloaded to: %s", cover_path)
    except Exception as exc:
        logger.error("Error downloading cover.jpg: %s", exc)
