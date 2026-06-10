"""Album artwork helpers for metadata enrichment."""

from __future__ import annotations

import errno
import os
import re
import time
import urllib.request
from ipaddress import ip_address
from urllib.parse import quote, urlparse

from core.imports.context import get_import_context_album, get_import_context_artist
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


# Query-string keys whose values must be masked when a media-server
# URL ends up in a log line. Plex uses X-Plex-Token, Jellyfin uses
# X-Emby-Token / api_key, Navidrome's Subsonic auth uses t (token) +
# s (salt) + p (password fallback). Logs end up persisted to disk —
# leaking any of these gives full read access to the user's library.
_REDACT_QUERY_KEYS = (
    'x-plex-token', 'x-emby-token', 'api_key', 'apikey',
    't', 's', 'p', 'token', 'password',
)
_REDACT_KEYS_ALT = '|'.join(re.escape(k) for k in _REDACT_QUERY_KEYS)
# Plain form: `?key=value` or `&key=value`. Anchored on `?` / `&` (or
# string start) so short keys like `t` only match at parameter
# boundaries — not as a substring of `format=Jpg`.
_REDACT_QUERY_RE = re.compile(
    r'(?i)(?P<lead>^|[?&])(?P<key>' + _REDACT_KEYS_ALT + r')=(?P<val>[^&\s]+)'
)
# URL-encoded form: `%3Fkey%3Dvalue` or `%26key%3Dvalue`. The image
# proxy wraps the original URL via `?url=<encoded>`, so the auth
# params end up encoded inside another URL. Without this second pass
# the encoded form survives plain redaction and ships to logs intact.
_REDACT_QUERY_RE_ENCODED = re.compile(
    r'(?i)(?P<lead>%3F|%26)(?P<key>' + _REDACT_KEYS_ALT + r')%3D(?P<val>[^%&\s]+?)(?=%26|&|\s|$)'
)


def _redact_url_secrets(url: str | None) -> str:
    """Mask sensitive query parameters in a URL so the result is safe
    to log. Handles both the plain form (``?token=abc``) and the URL-
    encoded form (``%3Ftoken%3Dabc``) — the latter shows up when an
    auth-bearing URL is wrapped inside another URL's query string
    (e.g. our `/api/image-proxy?url=<encoded-plex-url>` flow).

    Returns ``''`` for None/empty input. Idempotent (safe to call on
    already-redacted strings)."""
    if not url:
        return ''
    out = str(url)
    out = _REDACT_QUERY_RE.sub(
        lambda m: f"{m.group('lead')}{m.group('key')}=***REDACTED***",
        out,
    )
    out = _REDACT_QUERY_RE_ENCODED.sub(
        lambda m: f"{m.group('lead')}{m.group('key')}%3D***REDACTED***",
        out,
    )
    return out


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
                    logger.debug("Fixed URL: %s", _redact_url_secrets(fixed_url))
                    return _browser_safe_image_url(fixed_url)

            elif active_server == 'jellyfin':
                jellyfin_config = cfg.get_jellyfin_config()
                jellyfin_base_url = jellyfin_config.get('base_url', '')
                jellyfin_token = jellyfin_config.get('api_key', '')

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
                    logger.debug("Fixed URL: %s", _redact_url_secrets(fixed_url))
                    return _browser_safe_image_url(fixed_url)

            elif active_server == 'navidrome':
                navidrome_config = cfg.get_navidrome_config()
                navidrome_base_url = navidrome_config.get('base_url', '')
                navidrome_username = navidrome_config.get('username', '')
                navidrome_password = navidrome_config.get('password', '')

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
                    logger.debug("Fixed URL: %s", _redact_url_secrets(fixed_url))
                    return _browser_safe_image_url(fixed_url)

            logger.warning("No configuration found for %s or unsupported server type", active_server)

        # Return a browser-safe URL even if no server-specific rebuild was possible.
        return _browser_safe_image_url(thumb_url)

    except Exception as exc:
        logger.error("Error fixing image URL '%s': %s", _redact_url_secrets(thumb_url), exc)
        return _browser_safe_image_url(thumb_url)


def is_image_proxy_url(url: str) -> bool:
    """Return True for SoulSync image proxy/cache URLs, absolute or relative."""
    if not url:
        return False

    try:
        parsed = urlparse(url)
        return parsed.path == '/api/image-proxy' or parsed.path.startswith('/api/image-cache/')
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

    if url.startswith('/api/image-proxy?url=') or url.startswith('/api/image-cache/'):
        return url

    if url.startswith('http://') or url.startswith('https://'):
        try:
            from core.image_cache import cached_image_url

            cached_url = cached_image_url(url)
            if cached_url:
                return cached_url
        except Exception as exc:
            logger.debug("image cache URL registration failed: %s", exc)

        if is_internal_image_host(url):
            return f"/api/image-proxy?url={quote(url, safe='')}"
        return url

    # Relative media-server paths should already have been expanded before this point.
    return url


def _upgrade_art_url(art_url: str) -> str:
    """Rewrite a source CDN art URL to the highest resolution that source
    serves, so embedded tag art is as sharp as the cover.jpg in the folder.

    - Spotify (i.scdn.co): request the original uploaded master (~2000px+).
    - iTunes (mzstatic.com): bump the size segment to 3000x3000.
    - Deezer (dzcdn): rewrite to 1900x1900 (CDN serves larger than the API's
      1000px cover_xl).

    Unrecognized URLs are returned unchanged. Both the embed and cover.jpg
    paths call this so the two never diverge in quality again.
    """
    if not art_url:
        return art_url
    if "i.scdn.co" in art_url:
        try:
            from core.spotify_client import _upgrade_spotify_image_url

            return _upgrade_spotify_image_url(art_url)
        except Exception as e:
            logger.debug("upgrade spotify image url failed: %s", e)
    elif "mzstatic.com" in art_url:
        return re.sub(r"\d+x\d+bb", "3000x3000bb", art_url)
    elif "dzcdn" in art_url:
        try:
            from core.deezer_client import _upgrade_deezer_cover_url

            return _upgrade_deezer_cover_url(art_url)
        except Exception as e:
            logger.debug("upgrade deezer image url failed: %s", e)
    elif "coverartarchive.org" in art_url:
        # MusicBrainz art arrives as Cover Art Archive thumbnails
        # (/front-250 — see musicbrainz_search._cover_art_url). Upgrade to the
        # bare /front ORIGINAL — native resolution, frequently 3000px+ (#806:
        # the old /front-1200 cap left MusicBrainz as the one source still
        # below native while iTunes already shipped 3000x3000 — and bare
        # /front URLs from release-group lookups bypassed the cap anyway,
        # so the policy was inconsistent in practice). The original redirects
        # to archive.org, which can be flaky, so `_fetch_art_bytes` inserts a
        # /front-1200 midpoint fallback before the original-size URL:
        # flakiness degrades to the old 1200px behavior, never below it.
        return re.sub(r"/front(-\d+)?$", "/front", art_url)
    return art_url


# Negative cache for CAA originals: art is fetched PER TRACK, and the bare
# /front original rides archive.org. During an archive.org outage every track
# would otherwise pay a 10s timeout before falling back — a 12-track album
# would eat +2 minutes. One failure puts originals on cooldown; fetches go
# straight to the 1200px CDN (the pre-#806 behavior, full speed) until then.
_caa_original_down_until = 0.0
_CAA_ORIGINAL_COOLDOWN_S = 600


def _fetch_art_bytes(art_url: str):
    """Fetch artwork bytes at the highest resolution the source serves.

    Upgrades the URL via `_upgrade_art_url`, then walks a fallback chain so a
    refused size degrades gracefully and never regresses below the original
    URL's behavior. For Cover Art Archive that chain is
    original (/front) -> 1200px CDN thumbnail -> the original sized URL.

    Returns `(image_data, mime_type)` or `(None, None)` on failure.
    """
    global _caa_original_down_until
    if not art_url:
        return None, None
    upgraded = _upgrade_art_url(art_url)
    is_caa_original = "coverartarchive.org" in upgraded and upgraded.endswith("/front")

    attempts = []
    if not (is_caa_original and time.time() < _caa_original_down_until):
        attempts.append(upgraded)
    if is_caa_original:
        # Midpoint fallback: the 1200px CDN thumbnail (the pre-#806 behavior),
        # tried BEFORE the original sized URL so a flaky archive.org degrades
        # to 1200px — never all the way down to the 250px thumbnail.
        attempts.append(upgraded + "-1200")
    if art_url not in attempts:
        attempts.append(art_url)

    last_err = None
    for i, candidate in enumerate(attempts):
        try:
            with urllib.request.urlopen(candidate, timeout=10) as response:
                return response.read(), (response.info().get_content_type() or "image/jpeg")
        except Exception as fetch_err:
            last_err = fetch_err
            if is_caa_original and candidate == upgraded:
                # archive.org refused the original — cool down so the next
                # tracks of this batch skip straight to the CDN thumbnail.
                _caa_original_down_until = time.time() + _CAA_ORIGINAL_COOLDOWN_S
                logger.info(
                    "CAA original refused (%s); using 1200px CDN for the next %d min",
                    fetch_err, _CAA_ORIGINAL_COOLDOWN_S // 60,
                )
            elif i < len(attempts) - 1:
                logger.info("Art URL refused (%s); falling back to next size", fetch_err)
    logger.error("Art fetch failed after %d attempt(s): %s", len(attempts), last_err)
    return None, None


def _min_size_art_validator(min_px):
    """Build a ``(validate, cache)`` pair for the preferred-art resolver.

    ``validate(source, url)`` fetches the candidate cover, caches its bytes (so
    the winning source isn't fetched twice), and accepts it only when its
    shortest side is at least ``min_px``. A too-small cover — e.g. a low-res
    Cover Art Archive upload — is rejected so the resolver falls through to the
    next source instead of letting it win on priority alone. Images whose
    dimensions can't be read are accepted (don't over-reject; the fallback is
    still today's art). ``min_px <= 0`` disables the size gate entirely.
    """
    cache = {}

    def validate(_source, url):
        res = _fetch_art_bytes(url)
        cache[url] = res
        data = res[0] if res else None
        if not data:
            return False
        if not min_px or min_px <= 0:
            return True
        dims = get_image_dimensions(data)
        if not dims:
            return True
        return min(dims[0] or 0, dims[1] or 0) >= min_px

    return validate, cache


def embed_album_art_metadata(audio_file, metadata: dict):
    cfg = get_config_manager()
    symbols = get_mutagen_symbols()
    if not symbols:
        return False

    try:
        image_data = None
        mime_type = None

        # User-preferred cover-art source. When album_art_order is a non-empty
        # list it is the SOLE authority for preferred art (put 'caa' in it to use
        # Cover Art Archive), and the legacy prefer_caa_art toggle below is
        # skipped. With no list this is a no-op and behavior is exactly as before.
        album_art_order = cfg.get("metadata_enhancement.album_art_order")
        art_list_active = isinstance(album_art_order, (list, tuple)) and len(album_art_order) > 0
        try:
            from core.metadata.art_lookup import select_preferred_art_url
            _validate, _art_cache = _min_size_art_validator(
                cfg.get("metadata_enhancement.min_art_size", 1000))
            preferred_url = select_preferred_art_url(
                metadata.get("album_artist") or metadata.get("artist"),
                metadata.get("album"),
                metadata,
                album_art_order,
                validate=_validate,
            )
            if preferred_url:
                cached = _art_cache.get(preferred_url)
                image_data, mime_type = cached if (cached and cached[0]) else _fetch_art_bytes(preferred_url)
        except Exception as exc:
            logger.debug("Preferred art-source selection failed: %s", exc)

        release_mbid = metadata.get("musicbrainz_release_id")
        if not image_data and not art_list_active and release_mbid and cfg.get("metadata_enhancement.prefer_caa_art", False):
            try:
                # 1200px CDN thumbnail, not the flaky bare /front original.
                caa_url = f"https://coverartarchive.org/release/{release_mbid}/front-1200"
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
                return False
            image_data, mime_type = _fetch_art_bytes(art_url)

        if not image_data:
            logger.error("Failed to download album art data.")
            return False

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
        return True
    except Exception as exc:
        logger.error("Error embedding album art: %s", exc)
        return False


def download_cover_art(album_info: dict, target_dir: str, context: dict = None, force: bool = False):
    """Write cover.jpg into ``target_dir``.

    ``force`` bypasses the import-time "Download cover.jpg to album folder"
    toggle — used by the Cover Art Filler, whose whole job is to add cover art
    (if you explicitly run the filler you want the sidecar regardless of the
    auto-import preference). The import pipeline calls this WITHOUT force, so it
    still honors the user's setting.
    """
    cfg = get_config_manager()
    if not force and cfg.get("metadata_enhancement.cover_art_download", True) is False:
        return

    try:
        cover_path = os.path.join(target_dir, "cover.jpg")
        album_info = album_info or {}
        release_mbid = album_info.get("musicbrainz_release_id")
        # When a preferred-art priority list is configured it is the sole
        # authority, so the legacy CAA toggle is neutralized for this whole
        # function (it gates the existing-file upgrade logic too).
        _art_order = cfg.get("metadata_enhancement.album_art_order")
        _art_list_active = isinstance(_art_order, (list, tuple)) and len(_art_order) > 0
        prefer_caa = cfg.get("metadata_enhancement.prefer_caa_art", False) and not _art_list_active

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

        # User-preferred cover-art source (no-op unless album_art_order is set).
        # cover.jpg only supports the artist+album sources here (no MBID in
        # album_info), which matches today's CAA-only special-casing.
        try:
            from core.metadata.art_lookup import select_preferred_art_url
            artist_ctx = get_import_context_artist(context) if context else {}
            _validate, _art_cache = _min_size_art_validator(
                cfg.get("metadata_enhancement.min_art_size", 1000))
            preferred_url = select_preferred_art_url(
                (artist_ctx or {}).get("name"),
                album_info.get("album_name"),
                album_info,
                cfg.get("metadata_enhancement.album_art_order"),
                validate=_validate,
            )
            if preferred_url:
                cached = _art_cache.get(preferred_url)
                pref_data = cached[0] if (cached and cached[0]) else _fetch_art_bytes(preferred_url)[0]
                if pref_data and len(pref_data) > 1000:
                    image_data = pref_data
        except Exception as exc:
            logger.debug("Preferred art-source selection failed: %s", exc)

        if not image_data and release_mbid and prefer_caa:
            try:
                # 1200px CDN thumbnail, not the flaky bare /front original.
                caa_url = f"https://coverartarchive.org/release/{release_mbid}/front-1200"
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
            if not art_url:
                logger.warning("No cover art URL available for download.")
                return
            # Upgrade to the source's highest resolution (Spotify master /
            # iTunes 3000 / Deezer 1900) with a one-level fallback — shared
            # with the tag-embed path so cover.jpg and embedded art match.
            image_data, _ = _fetch_art_bytes(art_url)

        if not image_data:
            return

        with open(cover_path, "wb") as handle:
            handle.write(image_data)
        logger.info("Cover art downloaded to: %s", cover_path)
    except Exception as exc:
        # A read-only mount (EROFS) is a "can't write" condition the caller
        # needs to surface (cover-art filler #804/Tim/Sokhi) — but we must NOT
        # re-raise (import callers aren't wrapped here). Record it on the
        # context so callers that care can detect it, instead of just spamming
        # the log with a swallowed error.
        if getattr(exc, "errno", None) == errno.EROFS:
            if isinstance(context, dict):
                context["_cover_read_only"] = True
            logger.warning("cover.jpg write blocked — read-only filesystem: %s", cover_path)
        else:
            logger.error("Error downloading cover.jpg: %s", exc)
