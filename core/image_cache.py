"""Disk-backed image cache for browser-facing artwork URLs."""

from __future__ import annotations

import gc
import hashlib
import mimetypes
import os
import re
import sqlite3
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional
from urllib.parse import urlparse

import requests

from core.settings import config_manager
from core.metadata.artwork import is_internal_image_host
from utils.logging_config import get_logger

logger = get_logger("image_cache")

DEFAULT_TTL_SECONDS = 30 * 24 * 60 * 60
DEFAULT_FAILED_TTL_SECONDS = 6 * 60 * 60
DEFAULT_MAX_DOWNLOAD_BYTES = 15 * 1024 * 1024

# Ceiling for the cache as a whole. Until this existed the cache had a TTL it
# never enforced and no size limit at all: `expires_at` was written and read,
# but nothing ever deleted a row or a file, so browsing Discover on a large
# library grew storage/image_cache without bound and nothing reclaimed it.
# 0 disables the cap. The default is deliberately generous — a 5,000-album
# library is roughly 500 MB of covers — because evicting art someone is still
# looking at just means fetching it again.
DEFAULT_MAX_CACHE_BYTES = 2 * 1024 * 1024 * 1024

# Pruning walks the index, so it is not free. Amortize it over stores rather
# than paying it on every single one.
_PRUNE_EVERY_N_STORES = 250

# Seconds to wait on an upstream image before giving up and letting the page
# render with a placeholder.
DEFAULT_FETCH_TIMEOUT = 10.0

# Thumbnail sizes, by the job the image is doing. Max WIDTH, aspect preserved —
# covers are square but artist photos and backdrops are not, so forcing a box
# would letterbox or crop them.
DEFAULT_VARIANT_MAX_WIDTH = {
    "rail": 320,     # compact dashboard covers, including high-density screens
    "grid": 240,     # library/discover tiles
    "card": 480,     # shelf cards, search results
    "hero": 1200,    # detail-page headers
}

# Resolved from config at import so a caller can size these to their own
# layout, which is what "configurable thumbnail variants" asked for. A bad or
# missing value falls back to the default for that name rather than dropping
# the variant, so a typo in config cannot make artwork disappear.
def _resolved_variant_widths() -> dict:
    widths = {}
    for name, default in DEFAULT_VARIANT_MAX_WIDTH.items():
        try:
            value = int(config_manager.get(f"image_cache.variant_{name}_px", default))
            widths[name] = value if 32 <= value <= 4000 else default
        except (TypeError, ValueError):
            widths[name] = default
    return widths


VARIANT_MAX_WIDTH = _resolved_variant_widths()


# what sqlite says when the index file itself is damaged. a crash mid-write,
# a second process (a test run from wsl) scribbling on the wal, a full disk.
_CORRUPTION_MARKERS = ("malformed", "not a database", "database disk image", "file is encrypted")


def _is_corruption(exc: BaseException) -> bool:
    if not isinstance(exc, sqlite3.DatabaseError):
        return False
    message = str(exc).lower()
    return any(marker in message for marker in _CORRUPTION_MARKERS)


def _looks_like_image(head: bytes) -> bool:
    """magic-byte sniff for what the cache stores: jpeg, png, gif, webp, bmp,
    and the ftyp box that avif/heif start with."""
    return (head[:2] == b"\xff\xd8" or head[:8] == b"\x89PNG\r\n\x1a\n"
            or head[:4] == b"GIF8" or (head[:4] == b"RIFF" and head[8:12] == b"WEBP")
            or head[:2] == b"BM" or head[4:8] == b"ftyp")


def _row_get(row, column: str, default=""):
    """sqlite3.Row raises IndexError for a column the schema lacks."""
    try:
        return row[column] or default
    except (IndexError, KeyError):
        return default


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
        max_cache_bytes: int = DEFAULT_MAX_CACHE_BYTES,
        fetch_timeout: float = DEFAULT_FETCH_TIMEOUT,
        fetcher: Optional[Callable[..., requests.Response]] = None,
    ):
        self.cache_dir = Path(cache_dir)
        self.ttl_seconds = int(ttl_seconds)
        self.failed_ttl_seconds = int(failed_ttl_seconds)
        self.max_download_bytes = int(max_download_bytes)
        self.max_cache_bytes = int(max_cache_bytes)
        # A slow CDN must never hold a page open. Short by default, and
        # configurable — "short fetch timeouts ... so a slow image CDN never
        # blocks page rendering" (#1141).
        self.fetch_timeout = float(fetch_timeout)
        self._stores_since_prune = 0
        self.fetcher = fetcher or requests.get
        self.db_path = self.cache_dir / "image_cache.sqlite3"
        self._db_lock = threading.RLock()
        self._registrations: dict[str, float] = {}
        # last_accessed bumps waiting to be written, key -> time. a hit used to
        # write its own timestamp (an fsync each, behind the process lock), so
        # seventy-five cards on a page queued ~1 s apiece on the timestamp
        # alone. they collect here and go down in one transaction.
        self._pending_touches: dict[str, float] = {}
        self._pending_touches_lock = threading.Lock()
        self._pending_touches_since = 0.0
        # registrations waiting to be written, key -> (url, variant, time).
        # a page that fixes seventy-five artist urls used to write each one
        # in its own transaction behind the process lock before it could
        # answer; they collect here and go down together, and a serve that
        # arrives first flushes them.
        self._pending_registrations: dict[str, tuple[str, str, float]] = {}
        self._pending_registrations_since = 0.0
        self._key_locks: dict[str, threading.RLock] = {}
        self._key_locks_lock = threading.Lock()
        # when the index was last rebuilt after corruption; a damaged file we
        # could not move aside must not have us rebuilding on every request
        self._rebuilt_at = 0.0
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        try:
            self._init_db()
        except sqlite3.DatabaseError as exc:
            if not _is_corruption(exc):
                raise
            self._rebuild_index(str(exc))
        self._verify_on_start()

    def _verify_on_start(self) -> None:
        """quick_check the index once, at construction. a restart is the one
        moment nothing has the file open, so a rebuild that failed with
        WinError 32 while the server was busy goes through here."""
        try:
            with self._db() as conn:
                verdict = conn.execute("PRAGMA quick_check").fetchone()
            if verdict and str(verdict[0]).lower() == "ok":
                return
            reason = f"quick_check: {verdict[0] if verdict else 'no answer'}"
        except sqlite3.DatabaseError as exc:
            if not _is_corruption(exc):
                logger.debug("image cache startup check skipped: %s", exc)
                return
            reason = str(exc)
        self._rebuild_index(reason)

    def cache_url_for(self, url: str | None, variant: str = "") -> str | None:
        """Register a URL and return its browser-facing cached path."""
        if not url:
            return None
        if str(url).startswith("/api/image-cache/"):
            return str(url)
        if not self.is_cacheable_url(str(url)):
            return str(url)

        if variant not in VARIANT_MAX_WIDTH:
            variant = ""
        key = self.key_for_url(str(url), variant)
        now = time.time()
        flush = False
        with self._pending_touches_lock:
            # Coalesce repeat page registrations, with bounded memory. Serving
            # still touches LRU timestamps; registration refreshes once/minute.
            registered = self._registrations.get(key)
            if registered is not None and 0 <= now - registered < 60:
                return f"/api/image-cache/{key}"
            if len(self._registrations) >= 4096:
                self._registrations.pop(next(iter(self._registrations)))
            self._registrations[key] = now
            if not self._pending_registrations:
                self._pending_registrations_since = now
            self._pending_registrations[key] = (str(url), variant, now)
            if (len(self._pending_registrations) >= self.TOUCH_FLUSH_MAX
                    or now - self._pending_registrations_since >= self.TOUCH_FLUSH_SECONDS):
                flush = True
        if flush:
            self._flush_registrations()
        return f"/api/image-cache/{key}"

    def _flush_registrations(self) -> None:
        """write every pending registration in one transaction."""
        with self._pending_touches_lock:
            pending = self._pending_registrations
            self._pending_registrations = {}
        if not pending:
            return
        try:
            with self._db_lock:
                with self._db() as conn:
                    conn.executemany(
                        """
                        INSERT INTO image_cache
                            (key, original_url, status, created_at, updated_at, last_accessed,
                             expires_at, size, mime_type, file_path, last_error, variant)
                        VALUES (?, ?, 'pending', ?, ?, ?, 0, 0, '', '', '', ?)
                        ON CONFLICT(key) DO UPDATE SET
                            original_url=excluded.original_url,
                            last_accessed=excluded.last_accessed,
                            variant=COALESCE(NULLIF(excluded.variant, ''), variant)
                        """,
                        [(key, url, ts, ts, ts, variant) for key, (url, variant, ts) in pending.items()])
        except Exception as e:
            logger.debug("image cache registration flush failed (%d entries): %s", len(pending), e)
            # a lost registration only costs a 404 until the page registers it again
            if _is_corruption(e):
                self._rebuild_index(str(e))

    def _healing(self, operation: Callable[[], CachedImage]) -> CachedImage:
        """run a serve; if the index turns out to be corrupt, rebuild it from
        the files on disk and run the serve once more against the new one."""
        try:
            return operation()
        except sqlite3.DatabaseError as exc:
            if not _is_corruption(exc):
                raise
            reason = str(exc)
        # deliberately outside the except block: python drops `exc` here, and
        # with it the traceback whose frames still hold the connection that
        # raised. rebuilding inside the block kept that handle open, and
        # windows refused the rename.
        self._rebuild_index(reason)
        return operation()

    def get(self, key: str) -> CachedImage:
        return self._healing(lambda: self._get(key))

    def get_url(self, url: str) -> CachedImage:
        return self._healing(lambda: self._get_url(url))

    def _get(self, key: str) -> CachedImage:
        row = self._get_row(key)
        if not row:
            raise ImageCacheError("Image cache key not found")

        variant = _row_get(row, "variant")
        if not variant:
            if not row["original_url"]:
                # re-indexed from disk after a rebuild: the file is known, the
                # url is not until the page registers it again. serve the file.
                return self._recovered(row, key)
            return self.get_url(row["original_url"])

        now = time.time()
        if row["status"] == "ok" and row["file_path"]:
            path = Path(row["file_path"])
            if path.exists() and float(row["expires_at"] or 0) > now:
                self._touch(key, now, row["last_accessed"])
                return CachedImage(key, path, row["mime_type"] or "image/jpeg",
                                   int(row["size"] or 0), "hit")
        with self._lock_for_key(key):
            return self._build_variant(row, now)

    def _recovered(self, row, key: str) -> CachedImage:
        path = Path(row["file_path"] or "")
        if row["status"] == "ok" and row["file_path"] and path.exists():
            self._touch(key, time.time(), row["last_accessed"])
            return CachedImage(key, path, row["mime_type"] or "image/jpeg", int(row["size"] or 0), "hit")
        raise ImageCacheError("Image cache key not found")

    def _get_url(self, url: str) -> CachedImage:
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
                    self._touch(key, now, row["last_accessed"])
                    if not row["original_url"]:
                        # a row re-indexed from disk just learned its url;
                        # write it now so variants and refreshes work again
                        self.cache_url_for(url)
                        self._flush_registrations()
                    if float(row["expires_at"] or 0) > now:
                        return CachedImage(key, path, row["mime_type"] or "image/jpeg", int(row["size"] or 0), "hit")

            try:
                return self._fetch_and_store(url, key, now)
            except Exception as exc:
                if _is_corruption(exc):
                    # the download worked and the file is on disk; what failed
                    # is recording it. let _healing rebuild the index and redo
                    # the serve rather than logging "stale" forever.
                    raise
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

    # ── size management ──────────────────────────────────────────────────────

    def stats(self) -> dict:
        """What the cache is holding, for the Settings panel."""
        self._flush_registrations()
        self._flush_touches()
        with self._db_lock:
            with self._db() as conn:
                row = conn.execute(
                    "SELECT COUNT(*) AS entries, "
                    "       COALESCE(SUM(CASE WHEN status='ok' THEN size ELSE 0 END), 0) AS bytes, "
                    "       COALESCE(SUM(status='ok'), 0) AS ok, "
                    "       COALESCE(SUM(status='failed'), 0) AS failed "
                    "FROM image_cache"
                ).fetchone()
        return {
            "entries": int(row["entries"] or 0),
            "bytes": int(row["bytes"] or 0),
            "ok": int(row["ok"] or 0),
            "failed": int(row["failed"] or 0),
            "max_bytes": self.max_cache_bytes,
            "ttl_seconds": self.ttl_seconds,
        }

    def _delete_rows(self, conn, keys: list[str]) -> int:
        """Drop rows and their files. The file goes first: a row without a file
        re-fetches harmlessly, but a file without a row is unreachable garbage
        that nothing will ever clean up."""
        removed = 0
        for key in keys:
            self._registrations.pop(key, None)
            row = conn.execute("SELECT file_path FROM image_cache WHERE key = ?", (key,)).fetchone()
            path = (row["file_path"] if row else "") or ""
            if path:
                try:
                    Path(path).unlink(missing_ok=True)
                except OSError as exc:
                    logger.debug("image_cache could not unlink %s: %s", path, exc)
            conn.execute("DELETE FROM image_cache WHERE key = ?", (key,))
            removed += 1
        return removed

    def prune(self, now: Optional[float] = None) -> dict:
        """Enforce the TTL and the size cap. Safe to call at any time.

        Expired entries go first (that is what the TTL was always supposed to
        mean), then least-recently-used entries until the total fits. Eviction
        is by ``last_accessed``, which the serve path already maintains, so the
        art someone actually browses is the art that survives."""
        self._flush_registrations()
        self._flush_touches()
        now = time.time() if now is None else now
        expired = evicted = 0
        with self._db_lock:
            with self._db() as conn:
                expired = self._delete_rows(conn, [
                    r["key"] for r in conn.execute(
                        "SELECT key FROM image_cache WHERE expires_at > 0 AND expires_at < ?",
                        (now,)).fetchall()
                ])

                if self.max_cache_bytes > 0:
                    total = int(conn.execute(
                        "SELECT COALESCE(SUM(size), 0) AS t FROM image_cache WHERE status='ok'"
                    ).fetchone()["t"] or 0)
                    if total > self.max_cache_bytes:
                        doomed = []
                        for row in conn.execute(
                            "SELECT key, size FROM image_cache WHERE status='ok' "
                            "ORDER BY last_accessed ASC"
                        ).fetchall():
                            if total <= self.max_cache_bytes:
                                break
                            doomed.append(row["key"])
                            total -= int(row["size"] or 0)
                        evicted = self._delete_rows(conn, doomed)
                conn.commit()

        if expired or evicted:
            logger.info("image_cache prune: %d expired, %d evicted for size", expired, evicted)
        return {"expired": expired, "evicted": evicted}

    def clear(self) -> dict:
        """Empty the cache completely (the Settings button). Files and rows."""
        with self._db_lock:
            with self._db() as conn:
                keys = [r["key"] for r in conn.execute("SELECT key FROM image_cache").fetchall()]
                removed = self._delete_rows(conn, keys)
                conn.commit()
        logger.info("image_cache cleared: %d entries removed", removed)
        return {"removed": removed}

    def _maybe_prune(self) -> None:
        """Amortized upkeep, called after a successful store."""
        self._stores_since_prune += 1
        if self._stores_since_prune < _PRUNE_EVERY_N_STORES:
            return
        self._stores_since_prune = 0
        try:
            self.prune()
        except Exception as exc:
            # Never let housekeeping break the image the user is waiting for.
            logger.debug("image_cache prune failed: %s", exc)

    # ── thumbnail variants ───────────────────────────────────────────────────

    def variant_url_for(self, url: str | None, variant: str) -> str | None:
        """Browser path for a resized copy, or the plain cached path if the
        variant is unknown or thumbnails are switched off."""
        if not url or variant not in VARIANT_MAX_WIDTH:
            return self.cache_url_for(url)
        return self.cache_url_for(url, variant=variant)

    def get_variant_of(self, original_key: str, variant: str) -> CachedImage:
        """Serve ``variant`` of an already-registered original, by its key.

        Lets the BROWSER choose the size (``/api/image-cache/<key>?v=grid``)
        instead of every URL-producing call site having to know what it will be
        drawn into. Pages can adopt thumbnails one grid at a time, and an old
        page that asks for nothing keeps getting the original."""
        if variant not in VARIANT_MAX_WIDTH:
            return self.get(original_key)
        row = self._get_row(original_key)
        if not row:
            raise ImageCacheError("Image cache key not found")
        if _row_get(row, "variant"):
            return self.get(original_key)      # already a variant; don't nest
        if not row["original_url"]:
            # re-indexed after a rebuild and not registered again yet: the
            # variant key comes from the url, which nobody has told us. the
            # original as it is beats a 404.
            return self._healing(lambda: self._recovered(row, original_key))
        self.cache_url_for(row["original_url"], variant=variant)
        return self.get(self.key_for_url(row["original_url"], variant))

    def _build_variant(self, row, now: float) -> CachedImage:
        """Resize the original into this row's variant, fetching the original
        first if it is not cached yet.

        This is the part the reporter measured: their grid cells were being
        filled by full-size CDN originals, so a 5,567-album library paid for
        1400px masters to draw 200px tiles."""
        variant = row["variant"]
        max_width = VARIANT_MAX_WIDTH[variant]
        original = self.get_url(row["original_url"])      # cached or freshly fetched

        key = row["key"]
        path = self._path_for_key(key, ".jpg")
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = path.with_suffix(".jpg.tmp")

        try:
            from PIL import Image

            with Image.open(original.path) as img:
                img.load()                                 # decode inside the guard
                if img.mode not in ("RGB", "L"):
                    img = img.convert("RGB")
                if img.width > max_width:
                    height = max(1, round(img.height * (max_width / img.width)))
                    img = img.resize((max_width, height), Image.LANCZOS)
                img.save(tmp_path, format="JPEG", quality=85, optimize=True)
        except Exception as exc:
            try:
                tmp_path.unlink(missing_ok=True)
            except OSError:
                pass
            # A cover we cannot decode is not worth failing the page over —
            # serve the original and let the browser scale it, as before.
            logger.debug("image_cache variant %s failed for %s: %s", variant, key, exc)
            self._record_error(key, str(exc), now)
            return original

        os.replace(tmp_path, path)
        size = path.stat().st_size
        with self._db_lock:
            with self._db() as conn:
                conn.execute(
                    "UPDATE image_cache SET status='ok', updated_at=?, last_accessed=?, "
                    "expires_at=?, size=?, mime_type='image/jpeg', file_path=?, last_error='' "
                    "WHERE key = ?",
                    (now, now, now + self.ttl_seconds, size, str(path), key))
                conn.commit()
        self._maybe_prune()
        return CachedImage(key, path, "image/jpeg", size, "miss")

    @staticmethod
    def key_for_url(url: str, variant: str = "") -> str:
        """One key per (url, variant). A variant is just another cache entry, so
        it ages out and gets evicted by exactly the same rules as an original."""
        material = url if not variant else f"{url}#{variant}"
        return hashlib.sha256(material.encode("utf-8")).hexdigest()

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

    @staticmethod
    def _referer_for(url: str) -> str:
        """Referer header to send when fetching an image, per source.

        Hotlink-protected CDNs differ in what referer they accept:
          * Deezer's CDN (dzcdn.net) checks against the SITE origin — it wants
            ``https://www.deezer.com/`` (the value this cache hardcoded for
            years). A per-origin ``https://…dzcdn.net/`` referer risks a 403 on
            every cover, so Deezer keeps its known-good site referer.
          * Everything else uses a SAME-ORIGIN referer — what Bandcamp's bcbits
            CDN needs, and harmless for referer-agnostic CDNs (Spotify, iTunes).
        """
        parsed = urlparse(url)
        host = (parsed.hostname or "").lower()
        if host == "deezer.com" or host.endswith(".deezer.com") or host.endswith(".dzcdn.net"):
            return "https://www.deezer.com/"
        if parsed.scheme and parsed.netloc:
            return f"{parsed.scheme}://{parsed.netloc}/"
        return url

    def store_bytes(self, url: str, data: bytes, mime_type: str = "image/jpeg") -> CachedImage:
        """Store pre-fetched image bytes directly into the cache."""
        if not self.is_cacheable_url(url):
            raise ImageCacheError("URL is not cacheable")
        if not data:
            raise ImageCacheError("Image data is empty")
        if len(data) > self.max_download_bytes:
            raise ImageCacheError("Image exceeds configured size limit")

        key = self.key_for_url(url)
        now = time.time()
        ext = mimetypes.guess_extension(mime_type) or ".jpg"
        if ext == ".jpe":
            ext = ".jpg"
        path = self._path_for_key(key, ext)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = path.with_suffix(path.suffix + ".tmp")

        try:
            with open(tmp_path, "wb") as handle:
                handle.write(data)
            os.replace(tmp_path, path)
        except Exception:
            try:
                tmp_path.unlink(missing_ok=True)
            except Exception as cleanup_err:
                # the write failure below is the real error; a leftover .tmp is a note
                logger.debug("could not remove partial cache file %s: %s", tmp_path, cleanup_err)
            raise

        expires_at = now + self.ttl_seconds
        total = len(data)
        with self._db_lock:
            with self._db() as conn:
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
        self._maybe_prune()
        return CachedImage(key, path, mime_type, total, "miss")

    def precache_url(self, url: str) -> Optional[CachedImage]:
        """Pre-warm a URL into the image cache without raising. Returns CachedImage or None."""
        if not url or not self.is_cacheable_url(url):
            return None
        try:
            return self.get_url(url)
        except Exception as exc:
            logger.debug("precache_url failed for %s: %s", url, exc)
            return None

    def _fetch_and_store(self, url: str, key: str, now: float) -> CachedImage:
        if not self._is_fetch_allowed(url):
            raise ImageCacheError("Image host is not allowed")

        referer = self._referer_for(url)
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            ),
            "Accept": "image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8",
            "Referer": referer,
        }
        kwargs = {
            "timeout": self.fetch_timeout,
            "stream": True,
            "headers": headers,
        }

        parsed = urlparse(url)
        host = (parsed.hostname or "").lower()
        if host == "ext.to" or host.endswith(".ext.to") or host.endswith("extto.com"):
            try:
                from core.video.extto_search import clearance
                cookies, ua = clearance()
                if cookies:
                    kwargs["cookies"] = cookies
                if ua:
                    headers["User-Agent"] = ua
                headers["Referer"] = "https://ext.to/"
            except Exception as clearance_err:
                # no clearance is fine, the fetch just goes without it
                logger.debug("ext.to clearance unavailable for poster fetch: %s", clearance_err)

        response = self.fetcher(url, **kwargs)
        try:
            if response.status_code != 200:
                raise ImageCacheError(f"Upstream image returned HTTP {response.status_code}")

            mime_type = (response.headers.get("Content-Type") or "image/jpeg").split(";", 1)[0].strip()
            if not mime_type.startswith("image/"):
                raise ImageCacheError(f"Upstream response is not an image: {mime_type}")

            declared_size = response.headers.get("Content-Length")
            expected_bytes = None
            try:
                if declared_size:
                    expected_bytes = int(declared_size)
                    if expected_bytes > self.max_download_bytes:
                        raise ImageCacheError("Image exceeds configured size limit")
            except ValueError:
                expected_bytes = None

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

            # Truncation guard (#750): a dropped/short connection makes
            # iter_content end early WITHOUT raising, so a partial image would
            # otherwise be committed as status='ok' and cached permanently —
            # rendering as a half-decoded cover (top strip, rest grey). If the
            # server declared a Content-Length and we got fewer bytes, treat it
            # as a failed download: discard the tmp file and don't cache it, so
            # the next request retries fresh instead of serving a broken file.
            if expected_bytes is not None and total < expected_bytes:
                try:
                    tmp_path.unlink(missing_ok=True)
                except Exception as cleanup_exc:
                    logger.debug("image_cache tmp cleanup failed: %s", cleanup_exc)
                raise ImageCacheError(
                    f"Truncated image download: got {total} of {expected_bytes} bytes"
                )

            os.replace(tmp_path, path)
            expires_at = now + self.ttl_seconds
            with self._db_lock:
                with self._db() as conn:
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
            self._maybe_prune()
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
        conn = sqlite3.connect(self.db_path, timeout=10.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout = 10000")
        return conn

    @contextmanager
    def _db(self):
        """a connection that is CLOSED on the way out, not just committed.

        sqlite3's own context manager commits or rolls back and leaves the
        handle open until the object is collected. on windows an open handle
        (and the mmap'd -shm behind it) is exactly what stops the corrupt
        index from being moved aside: WinError 32 on boulder's server,
        sept 15 2026, one minute after the heal first fired.
        """
        conn = self._connect()
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    def _init_db(self) -> None:
        with self._db_lock:
            with self._db() as conn:
                # wal + synchronous=NORMAL: a hit used to cost a rollback-journal
                # commit (an fsync) to bump last_accessed, serialized behind the
                # process lock, so seventy-five cards on a page queued for
                # ~1 s each on nothing but the timestamp. wal lets reads run
                # alongside a write and drops the per-commit fsync; the cache
                # is rebuilt from disk if a crash ever loses a timestamp.
                try:
                    conn.execute("PRAGMA journal_mode = WAL")
                    conn.execute("PRAGMA synchronous = NORMAL")
                except sqlite3.Error as e:
                    logger.debug("image cache wal setup skipped: %s", e)
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
                # Additive: existing caches predate variants. A variant is a
                # normal row with its own key, so stats, LRU and pruning all
                # keep working without knowing variants exist.
                cols = {r[1] for r in conn.execute("PRAGMA table_info(image_cache)").fetchall()}
                if "variant" not in cols:
                    conn.execute("ALTER TABLE image_cache ADD COLUMN variant TEXT NOT NULL DEFAULT ''")

    # pending touches are written after this long, or when this many collect
    TOUCH_FLUSH_SECONDS = 5.0
    TOUCH_FLUSH_MAX = 200

    def _get_row(self, key: str) -> Optional[sqlite3.Row]:
        # a serve that arrives before the page's registrations were written
        # writes them now; the browser asks within milliseconds of the render
        with self._pending_touches_lock:
            pending = key in self._pending_registrations
        if pending:
            self._flush_registrations()
        # a read on its own connection needs no process lock under wal
        with self._db() as conn:
            return conn.execute("SELECT * FROM image_cache WHERE key = ?", (key,)).fetchone()

    def _touch(self, key: str, now: float, last_accessed: Optional[float] = None) -> None:
        """record a hit; the write happens in _flush_touches, batched."""
        flush = False
        with self._pending_touches_lock:
            if not self._pending_touches:
                self._pending_touches_since = now
            self._pending_touches[key] = now
            if (len(self._pending_touches) >= self.TOUCH_FLUSH_MAX
                    or now - self._pending_touches_since >= self.TOUCH_FLUSH_SECONDS):
                flush = True
        if flush:
            self._flush_touches()

    def _flush_touches(self) -> None:
        """write every pending last_accessed in one transaction. called by the
        hit that crosses the batch threshold, and by anything that is about to
        read the timestamps (prune, stats), so eviction order is exact."""
        with self._pending_touches_lock:
            pending = self._pending_touches
            self._pending_touches = {}
        if not pending:
            return
        try:
            with self._db_lock:
                with self._db() as conn:
                    conn.executemany(
                        "UPDATE image_cache SET last_accessed = ? WHERE key = ? AND last_accessed < ?",
                        [(ts, key, ts) for key, ts in pending.items()])
        except Exception as e:
            logger.debug("image cache touch flush failed (%d entries): %s", len(pending), e)
            if _is_corruption(e):
                self._rebuild_index(str(e))

    def _record_error(self, key: str, error: str, now: float, *, keep_status: bool = False) -> None:
        status_sql = "status" if keep_status else "'failed'"
        try:
            with self._db_lock:
                with self._db() as conn:
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
        except sqlite3.Error as exc:
            # bookkeeping. this runs inside the "serve the stale file" path,
            # and raising here turned a stale hit into a 404 (the broken tiles
            # in boulder's recently played rail while the index was corrupt).
            logger.debug("image cache could not record an error for %s: %s", key, exc)
            if _is_corruption(exc):
                self._rebuild_index(str(exc))

    # ── corruption recovery ──────────────────────────────────────────────────

    REBUILD_COOLDOWN_SECONDS = 60.0

    def _rebuild_index(self, reason: str) -> bool:
        """quarantine a corrupt index and rebuild it from the files on disk.

        the index is the only thing that broke; the images are fine, and
        there can be gigabytes of them. re-indexing keeps every one of them
        serving instead of re-downloading the lot, which is what an empty
        index would mean. rows come back with no url: a page registers the
        url again on its next render, and until then the file is served as
        it is (see _recovered).

        returns False when the damaged file could not be moved aside; the
        cache then keeps limping (stale serves work, records don't) and tries
        again after the cooldown, so a wedged file cannot have every request
        doing rename work.
        """
        with self._db_lock:
            now = time.time()
            if now - self._rebuilt_at < self.REBUILD_COOLDOWN_SECONDS:
                return False
            self._rebuilt_at = now
            logger.error("image cache index is corrupt (%s); moving it aside and "
                         "rebuilding it from the files on disk", reason)
            stamp = time.strftime("%Y%m%d-%H%M%S", time.localtime(now))
            # any handle still open on the index (a reader on another thread,
            # a connection waiting on the collector) blocks the rename on
            # windows. collect, then give in-flight readers a moment.
            gc.collect()
            for suffix in ("", "-wal", "-shm"):
                damaged = Path(f"{self.db_path}{suffix}")
                if not damaged.exists():
                    continue
                if not self._move_aside(damaged, Path(f"{self.db_path}.corrupt-{stamp}{suffix}")):
                    return False
            with self._pending_touches_lock:
                self._pending_touches.clear()
                self._pending_registrations.clear()
                self._registrations.clear()
            self._init_db()
            count = self._reindex_files(now)
            logger.warning("image cache index rebuilt: %d cached images re-indexed", count)
            return True

    MOVE_ASIDE_ATTEMPTS = 10
    MOVE_ASIDE_WAIT_SECONDS = 0.2

    def _move_aside(self, damaged: Path, target: Path) -> bool:
        """rename the damaged file, retrying while another thread's handle
        closes; delete it if it will not rename. False only when both fail."""
        last_exc: Optional[BaseException] = None
        for attempt in range(self.MOVE_ASIDE_ATTEMPTS):
            try:
                os.replace(damaged, target)
                return True
            except OSError as exc:
                last_exc = exc
            try:
                damaged.unlink()
                return True
            except OSError as exc:
                last_exc = exc
            if attempt < self.MOVE_ASIDE_ATTEMPTS - 1:
                time.sleep(self.MOVE_ASIDE_WAIT_SECONDS)
                gc.collect()
        logger.error("image cache could not move the corrupt index aside (%s); "
                     "serving what it can until the next try", last_exc)
        return False

    def _reindex_files(self, now: float) -> int:
        """one row per image file already on disk, url unknown.

        a file that doesn't start like an image is dropped, not indexed:
        boulder's cache had four zero-filled files (a lost write) and three
        one-byte fakes (a test run). indexed, they would serve as broken
        tiles until their ttl; nothing in them is worth keeping."""
        rows = []
        junk = 0
        for path in self.cache_dir.rglob("*"):
            if not path.is_file():
                continue
            name = path.name
            if name.endswith(".tmp") or ".sqlite3" in name or ".corrupt-" in name:
                continue
            key = path.stem
            if not re.fullmatch(r"[a-f0-9]{64}", key):
                continue
            try:
                size = path.stat().st_size
                with open(path, "rb") as handle:
                    head = handle.read(16)
            except OSError:
                continue
            if size <= 0 or not _looks_like_image(head):
                try:
                    path.unlink()
                    junk += 1
                except OSError:
                    pass
                continue
            mime_type = mimetypes.guess_type(name)[0] or "image/jpeg"
            rows.append((key, "", "ok", now, now, now, now + self.ttl_seconds,
                         size, mime_type, str(path), "", ""))
        if junk:
            logger.warning("image cache rebuild dropped %d files that were not images", junk)
        if not rows:
            return 0
        with self._db_lock:
            with self._db() as conn:
                conn.executemany(
                    """
                    INSERT OR IGNORE INTO image_cache
                        (key, original_url, status, created_at, updated_at, last_accessed,
                         expires_at, size, mime_type, file_path, last_error, variant)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    rows,
                )
        return len(rows)

    def _lock_for_key(self, key: str) -> threading.RLock:
        """Per-image lock so concurrent requests fetch once.

        REENTRANT on purpose. Serving a variant holds this lock and then calls
        get_url() for the original, which takes the lock for ITS key — normally
        a different key, but any path that makes the two coincide would
        self-deadlock on a plain Lock and hang the request forever, with no
        error and no timeout. A mutation run proved it: a mutant that collapsed
        the variant key onto the original's hung the whole test process for
        six hours instead of failing. A deadlock is strictly worse than an
        exception, and an RLock costs nothing to rule it out."""
        with self._key_locks_lock:
            lock = self._key_locks.get(key)
            if lock is None:
                lock = threading.RLock()
                self._key_locks[key] = lock
            return lock


_image_cache: Optional[ImageCache] = None
_image_cache_lock = threading.Lock()


def get_image_cache() -> ImageCache:
    global _image_cache
    with _image_cache_lock:
        if _image_cache is None:
            # the env override is the test suite's seam: without it a test
            # that reaches any image endpoint writes into the developer's
            # LIVE cache from a second process. from wsl that means a linux
            # sqlite scribbling on a wal the windows server has open, which
            # is how boulder's index got corrupted on sept 15 2026.
            cache_dir = (os.environ.get("SOULSYNC_IMAGE_CACHE_DIR")
                         or config_manager.get("image_cache.path", "storage/image_cache"))
            if not os.path.isabs(cache_dir):
                cache_dir = str(config_manager.base_dir / cache_dir)
            _image_cache = ImageCache(
                cache_dir,
                ttl_seconds=int(config_manager.get("image_cache.ttl_seconds", DEFAULT_TTL_SECONDS)),
                failed_ttl_seconds=int(config_manager.get("image_cache.failed_ttl_seconds", DEFAULT_FAILED_TTL_SECONDS)),
                max_download_bytes=int(config_manager.get("image_cache.max_download_mb", 15)) * 1024 * 1024,
                max_cache_bytes=int(config_manager.get(
                    "image_cache.max_cache_mb", DEFAULT_MAX_CACHE_BYTES // (1024 * 1024))) * 1024 * 1024,
                fetch_timeout=float(config_manager.get("image_cache.fetch_timeout", DEFAULT_FETCH_TIMEOUT)),
            )
            # Reclaim on startup too: a cache that only prunes while it is being
            # written never shrinks for someone who has stopped browsing.
            try:
                _image_cache.prune()
            except Exception as exc:
                logger.debug("image_cache startup prune failed: %s", exc)
        return _image_cache


def reset_image_cache() -> None:
    """Drop the singleton so the next use re-reads config.

    Settings are applied by rebuilding, not by mutating a live object: the
    cache is constructed from config once, so without this a size limit saved
    in Settings would sit there doing nothing until the next restart."""
    global _image_cache, VARIANT_MAX_WIDTH
    with _image_cache_lock:
        _image_cache = None
    VARIANT_MAX_WIDTH = _resolved_variant_widths()


def thumbnails_enabled() -> bool:
    """Server-side resizing is OPT-IN and off by default (Boulder's call).

    Distinct from `image_cache.enabled`, which has been on for every install
    since the cache shipped — turning THAT off by default would silently slow
    down everyone who is already benefiting from it. This flag gates only the
    new work: decoding and re-encoding every image the browser asks for."""
    return config_manager.get("image_cache.thumbnails", False) is True


def cached_image_url(url: str | None) -> str | None:
    if not url or config_manager.get("image_cache.enabled", True) is False:
        return url
    try:
        return get_image_cache().cache_url_for(url)
    except Exception as exc:
        logger.debug("image cache registration failed: %s", exc)
        return url
