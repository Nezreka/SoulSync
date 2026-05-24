"""Disk-backed image cache for browser-facing artwork URLs."""

from __future__ import annotations

import hashlib
import mimetypes
import os
import sqlite3
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional
from urllib.parse import urlparse

import requests

from config.settings import config_manager
from core.metadata.artwork import is_internal_image_host
from utils.logging_config import get_logger

logger = get_logger("image_cache")

DEFAULT_TTL_SECONDS = 30 * 24 * 60 * 60
DEFAULT_FAILED_TTL_SECONDS = 6 * 60 * 60
DEFAULT_MAX_DOWNLOAD_BYTES = 15 * 1024 * 1024


class ImageCacheError(Exception):
    """Raised when an image cannot be served from the cache."""


@dataclass
class CachedImage:
    key: str
    path: Path
    mime_type: str
    size: int
    status: str


class ImageCache:
    def __init__(
        self,
        cache_dir: str | os.PathLike[str],
        *,
        ttl_seconds: int = DEFAULT_TTL_SECONDS,
        failed_ttl_seconds: int = DEFAULT_FAILED_TTL_SECONDS,
        max_download_bytes: int = DEFAULT_MAX_DOWNLOAD_BYTES,
        fetcher: Optional[Callable[..., requests.Response]] = None,
    ):
        self.cache_dir = Path(cache_dir)
        self.ttl_seconds = int(ttl_seconds)
        self.failed_ttl_seconds = int(failed_ttl_seconds)
        self.max_download_bytes = int(max_download_bytes)
        self.fetcher = fetcher or requests.get
        self.db_path = self.cache_dir / "image_cache.sqlite3"
        self._db_lock = threading.RLock()
        self._key_locks: dict[str, threading.Lock] = {}
        self._key_locks_lock = threading.Lock()
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def cache_url_for(self, url: str | None) -> str | None:
        """Register a URL and return its browser-facing cached path."""
        if not url:
            return None
        if str(url).startswith("/api/image-cache/"):
            return str(url)
        if not self.is_cacheable_url(str(url)):
            return str(url)

        key = self.key_for_url(str(url))
        now = time.time()
        with self._db_lock:
            with self._connect() as conn:
                conn.execute(
                    """
                    INSERT INTO image_cache
                        (key, original_url, status, created_at, updated_at, last_accessed,
                         expires_at, size, mime_type, file_path, last_error)
                    VALUES (?, ?, 'pending', ?, ?, ?, 0, 0, '', '', '')
                    ON CONFLICT(key) DO UPDATE SET
                        original_url=excluded.original_url,
                        last_accessed=excluded.last_accessed
                    """,
                    (key, str(url), now, now, now),
                )
        return f"/api/image-cache/{key}"

    def get(self, key: str) -> CachedImage:
        row = self._get_row(key)
        if not row:
            raise ImageCacheError("Image cache key not found")
        return self.get_url(row["original_url"])

    def get_url(self, url: str) -> CachedImage:
        if not self.is_cacheable_url(url):
            raise ImageCacheError("URL is not cacheable")

        key = self.key_for_url(url)
        lock = self._lock_for_key(key)
        with lock:
            row = self._get_row(key)
            now = time.time()
            if row and row["status"] == "ok" and row["file_path"]:
                path = Path(row["file_path"])
                if path.exists():
                    self._touch(key, now)
                    if float(row["expires_at"] or 0) > now:
                        return CachedImage(key, path, row["mime_type"] or "image/jpeg", int(row["size"] or 0), "hit")

            try:
                return self._fetch_and_store(url, key, now)
            except Exception as exc:
                if row and row["status"] == "ok" and row["file_path"]:
                    stale_path = Path(row["file_path"])
                    if stale_path.exists():
                        logger.warning("Serving stale cached image for %s after refresh failed: %s", key, exc)
                        self._record_error(key, str(exc), now, keep_status=True)
                        return CachedImage(
                            key,
                            stale_path,
                            row["mime_type"] or "image/jpeg",
                            int(row["size"] or 0),
                            "stale",
                        )
                self._record_error(key, str(exc), now)
                raise ImageCacheError(str(exc)) from exc

    @staticmethod
    def key_for_url(url: str) -> str:
        return hashlib.sha256(url.encode("utf-8")).hexdigest()

    @staticmethod
    def is_cacheable_url(url: str) -> bool:
        try:
            parsed = urlparse(url)
            if parsed.scheme not in {"http", "https"}:
                return False
            if parsed.username or parsed.password:
                return False
            if not parsed.hostname:
                return False
            return True
        except Exception:
            return False

    def _fetch_and_store(self, url: str, key: str, now: float) -> CachedImage:
        if not self._is_fetch_allowed(url):
            raise ImageCacheError("Image host is not allowed")

        response = self.fetcher(
            url,
            timeout=10,
            stream=True,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
                ),
                "Accept": "image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8",
                "Referer": "https://www.deezer.com/",
            },
        )
        try:
            if response.status_code != 200:
                raise ImageCacheError(f"Upstream image returned HTTP {response.status_code}")

            mime_type = (response.headers.get("Content-Type") or "image/jpeg").split(";", 1)[0].strip()
            if not mime_type.startswith("image/"):
                raise ImageCacheError(f"Upstream response is not an image: {mime_type}")

            declared_size = response.headers.get("Content-Length")
            try:
                if declared_size and int(declared_size) > self.max_download_bytes:
                    raise ImageCacheError("Image exceeds configured size limit")
            except ValueError:
                pass

            ext = mimetypes.guess_extension(mime_type) or ".img"
            if ext == ".jpe":
                ext = ".jpg"
            path = self._path_for_key(key, ext)
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp_path = path.with_suffix(path.suffix + ".tmp")

            total = 0
            try:
                with open(tmp_path, "wb") as handle:
                    for chunk in response.iter_content(chunk_size=64 * 1024):
                        if not chunk:
                            continue
                        total += len(chunk)
                        if total > self.max_download_bytes:
                            raise ImageCacheError("Image exceeds configured size limit")
                        handle.write(chunk)
            except Exception:
                try:
                    tmp_path.unlink(missing_ok=True)
                except Exception as cleanup_exc:
                    logger.debug("image_cache tmp cleanup failed: %s", cleanup_exc)
                raise

            if total <= 0:
                raise ImageCacheError("Image response was empty")

            os.replace(tmp_path, path)
            expires_at = now + self.ttl_seconds
            with self._db_lock:
                with self._connect() as conn:
                    conn.execute(
                        """
                        INSERT INTO image_cache
                            (key, original_url, status, created_at, updated_at, last_accessed,
                             expires_at, size, mime_type, file_path, last_error)
                        VALUES (?, ?, 'ok', ?, ?, ?, ?, ?, ?, ?, '')
                        ON CONFLICT(key) DO UPDATE SET
                            original_url=excluded.original_url,
                            status='ok',
                            updated_at=excluded.updated_at,
                            last_accessed=excluded.last_accessed,
                            expires_at=excluded.expires_at,
                            size=excluded.size,
                            mime_type=excluded.mime_type,
                            file_path=excluded.file_path,
                            last_error=''
                        """,
                        (key, url, now, now, now, expires_at, total, mime_type, str(path)),
                    )
            return CachedImage(key, path, mime_type, total, "miss")
        finally:
            response.close()

    def _path_for_key(self, key: str, extension: str) -> Path:
        return self.cache_dir / key[:2] / key[2:4] / f"{key}{extension}"

    def _is_fetch_allowed(self, url: str) -> bool:
        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https"}:
            return False
        if parsed.username or parsed.password:
            return False
        if not parsed.hostname:
            return False

        # Internal hosts are explicitly supported because Plex/Jellyfin/Navidrome
        # artwork often lives behind Docker/LAN-only URLs. Public hosts are allowed
        # as image-only responses with size limits.
        return bool(parsed.hostname) or is_internal_image_host(url)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        with self._db_lock:
            with self._connect() as conn:
                conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS image_cache (
                        key TEXT PRIMARY KEY,
                        original_url TEXT NOT NULL,
                        status TEXT NOT NULL DEFAULT 'pending',
                        created_at REAL NOT NULL,
                        updated_at REAL NOT NULL,
                        last_accessed REAL NOT NULL,
                        expires_at REAL NOT NULL DEFAULT 0,
                        size INTEGER NOT NULL DEFAULT 0,
                        mime_type TEXT NOT NULL DEFAULT '',
                        file_path TEXT NOT NULL DEFAULT '',
                        last_error TEXT NOT NULL DEFAULT ''
                    )
                    """
                )
                conn.execute("CREATE INDEX IF NOT EXISTS idx_image_cache_accessed ON image_cache(last_accessed)")

    def _get_row(self, key: str) -> Optional[sqlite3.Row]:
        with self._db_lock:
            with self._connect() as conn:
                return conn.execute("SELECT * FROM image_cache WHERE key = ?", (key,)).fetchone()

    def _touch(self, key: str, now: float) -> None:
        with self._db_lock:
            with self._connect() as conn:
                conn.execute("UPDATE image_cache SET last_accessed = ? WHERE key = ?", (now, key))

    def _record_error(self, key: str, error: str, now: float, *, keep_status: bool = False) -> None:
        status_sql = "status" if keep_status else "'failed'"
        with self._db_lock:
            with self._connect() as conn:
                conn.execute(
                    f"""
                    UPDATE image_cache
                    SET status = {status_sql},
                        updated_at = ?,
                        last_accessed = ?,
                        expires_at = ?,
                        last_error = ?
                    WHERE key = ?
                    """,
                    (now, now, now + self.failed_ttl_seconds, error[:500], key),
                )

    def _lock_for_key(self, key: str) -> threading.Lock:
        with self._key_locks_lock:
            lock = self._key_locks.get(key)
            if lock is None:
                lock = threading.Lock()
                self._key_locks[key] = lock
            return lock


_image_cache: Optional[ImageCache] = None
_image_cache_lock = threading.Lock()


def get_image_cache() -> ImageCache:
    global _image_cache
    with _image_cache_lock:
        if _image_cache is None:
            cache_dir = config_manager.get("image_cache.path", "storage/image_cache")
            if not os.path.isabs(cache_dir):
                cache_dir = str(config_manager.base_dir / cache_dir)
            _image_cache = ImageCache(
                cache_dir,
                ttl_seconds=int(config_manager.get("image_cache.ttl_seconds", DEFAULT_TTL_SECONDS)),
                failed_ttl_seconds=int(config_manager.get("image_cache.failed_ttl_seconds", DEFAULT_FAILED_TTL_SECONDS)),
                max_download_bytes=int(config_manager.get("image_cache.max_download_mb", 15)) * 1024 * 1024,
            )
        return _image_cache


def cached_image_url(url: str | None) -> str | None:
    if not url or config_manager.get("image_cache.enabled", True) is False:
        return url
    try:
        return get_image_cache().cache_url_for(url)
    except Exception as exc:
        logger.debug("image cache registration failed: %s", exc)
        return url
