"""Database-backed Spotify token cache (wolf39us's daily-deauth fix).

Spotipy's default cache is a loose file — ours lived at
``config/.spotify_cache``. In Docker, ``/app/config`` is a declared VOLUME,
but a compose file that doesn't map it explicitly gets an ANONYMOUS volume,
and anonymous volumes don't survive container recreation. Net effect: a
nightly Watchtower pull kept the user's settings (config now lives in the
database) but silently dropped the OAuth tokens — "it keeps unauthenticating"
every day, while a manual re-auth always "fixed" it until the next pull.

This handler stores the token payload in the same database-backed config
store as every other setting (``spotify.token_info``), so tokens survive
exactly as long as the rest of the configuration does. The legacy cache file
is imported once if the store is empty, then left in place for rollback.
"""

from __future__ import annotations

import json
import os
from typing import Any, Dict, Optional

from spotipy.cache_handler import CacheHandler

from utils.logging_config import get_logger

logger = get_logger("spotify_token_cache")

_CONFIG_KEY = "spotify.token_info"
LEGACY_CACHE_PATH = "config/.spotify_cache"


class DatabaseTokenCache(CacheHandler):
    """Spotipy CacheHandler persisting the token in the config database."""

    def __init__(self, config_manager, legacy_path: str = LEGACY_CACHE_PATH):
        self._config = config_manager
        self._legacy_path = legacy_path

    def get_cached_token(self) -> Optional[Dict[str, Any]]:
        try:
            token = self._config.get(_CONFIG_KEY, None)
            if isinstance(token, str):
                token = json.loads(token)
            if isinstance(token, dict) and token.get("access_token"):
                return token
        except Exception as e:
            logger.debug("token cache read failed: %s", e)

        # One-time import from the legacy file cache, so an upgrade doesn't
        # force a re-auth when the file happens to still be around.
        try:
            if self._legacy_path and os.path.isfile(self._legacy_path):
                with open(self._legacy_path, "r", encoding="utf-8") as fh:
                    legacy = json.load(fh)
                if isinstance(legacy, dict) and legacy.get("access_token"):
                    logger.info(
                        "Imported Spotify token from legacy file cache into the "
                        "database store (tokens now survive container recreation)")
                    self.save_token_to_cache(legacy)
                    return legacy
        except Exception as e:
            logger.debug("legacy token import failed: %s", e)
        return None

    def save_token_to_cache(self, token_info: Dict[str, Any]) -> None:
        try:
            self._config.set(_CONFIG_KEY, token_info)
        except Exception as e:
            # Never let a cache write break an API call — worst case the
            # refreshed token isn't persisted and the next run refreshes again.
            logger.warning("token cache write failed: %s", e)

    def clear(self) -> None:
        """Logout: drop the stored token (and the legacy file if present)."""
        try:
            self._config.set(_CONFIG_KEY, None)
        except Exception as e:
            logger.debug("token cache clear failed: %s", e)
        try:
            if self._legacy_path and os.path.isfile(self._legacy_path):
                os.remove(self._legacy_path)
        except OSError as e:
            logger.debug("legacy cache remove failed: %s", e)
