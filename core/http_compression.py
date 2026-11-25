"""Compressed responses: brotli when the browser takes it, gzip otherwise.

Two parts.

1. Dynamic responses (pages, JSON, SVG...) are compressed in an after_request
   hook when they are text, at least ``MIN_BYTES`` long, and fully in memory.
   Anything streamed or passed straight through is left alone: audio and
   video, file downloads (send_file), SSE event streams, range answers, and
   anything already encoded. socket.io never reaches Flask's hooks (the
   socketio middleware answers it first) and is skipped by path anyway.

2. Static files (/static/...) are served from a precompressed sibling
   (``style.css.br`` / ``style.css.gz``) when one exists and is at least as new
   as the file, which the Docker image builds at maximum compression. Without
   one (a git checkout), the file is compressed once on first request and the
   result kept in memory, keyed by its mtime and size. A request with a Range
   header gets the plain file, so byte ranges always mean the same bytes.

Every compressed answer carries ``Vary: Accept-Encoding`` and its own ETag, so
a cache never hands brotli to a browser that asked for gzip.
"""

from __future__ import annotations

import gzip
import mimetypes
import os
import re
import threading
from collections import OrderedDict
from datetime import datetime, timezone
from typing import Callable, Optional, Tuple

from utils.logging_config import get_logger

logger = get_logger("http_compression")

try:
    import brotli as _brotli  # type: ignore
except Exception:  # noqa: BLE001 - optional: gzip covers everyone
    _brotli = None

MIN_BYTES = 1024
# dynamic: fast levels, the body is built per request
BROTLI_LEVEL = 5
GZIP_LEVEL = 6
# static: compressed once per file per process, so it can afford more
STATIC_BROTLI_LEVEL = 9
STATIC_GZIP_LEVEL = 9
STATIC_CACHE_BYTES = 48 * 1024 * 1024

_TEXT_TYPES = {
    "application/json",
    "application/javascript",
    "application/x-javascript",
    "application/manifest+json",
    "application/xml",
    "application/rss+xml",
    "application/atom+xml",
    "application/ld+json",
    "application/problem+json",
    "image/svg+xml",
}
_NEVER_TYPES = {"text/event-stream"}
STATIC_EXTENSIONS = {
    ".js", ".mjs", ".css", ".svg", ".json", ".html", ".htm", ".txt", ".map",
    ".xml", ".webmanifest",
}


def brotli_available() -> bool:
    return _brotli is not None


def is_compressible_type(mimetype: Optional[str]) -> bool:
    mt = (mimetype or "").split(";", 1)[0].strip().lower()
    if not mt or mt in _NEVER_TYPES:
        return False
    return mt.startswith("text/") or mt in _TEXT_TYPES or mt.endswith("+json")


def _parse_accept(header: str) -> dict:
    out = {}
    for part in (header or "").split(","):
        bits = [b.strip() for b in part.split(";")]
        name = bits[0].lower()
        if not name:
            continue
        q = 1.0
        for b in bits[1:]:
            if b.lower().startswith("q="):
                try:
                    q = float(b[2:])
                except ValueError:
                    q = 0.0
        out[name] = q
    return out


def choose_encoding(accept_encoding: Optional[str], *, brotli_ok: bool = True) -> Optional[str]:
    """'br', 'gzip' or None for an Accept-Encoding header. q=0 refuses one;
    brotli wins a tie."""
    accepted = _parse_accept(accept_encoding or "")
    star = accepted.get("*")

    def q(name):
        if name in accepted:
            return accepted[name]
        return star if star is not None else 0.0

    best, best_q = None, 0.0
    for name in (["br"] if brotli_ok and brotli_available() else []) + ["gzip"]:
        value = q(name)
        if value > best_q:
            best, best_q = name, value
    return best


def compress_bytes(data: bytes, encoding: str, *, static: bool = False) -> bytes:
    if encoding == "br":
        return _brotli.compress(data, quality=STATIC_BROTLI_LEVEL if static else BROTLI_LEVEL)
    if encoding == "gzip":
        # mtime 0: the same bytes every time, so the ETag holds
        return gzip.compress(data, compresslevel=STATIC_GZIP_LEVEL if static else GZIP_LEVEL, mtime=0)
    raise ValueError(encoding)


def add_vary(headers, value: str = "Accept-Encoding") -> None:
    current = headers.get("Vary", "")
    parts = [p.strip() for p in current.split(",") if p.strip()]
    if "*" in parts or any(p.lower() == value.lower() for p in parts):
        return
    parts.append(value)
    headers["Vary"] = ", ".join(parts)


def _weak_etag(etag: Optional[str]) -> Optional[str]:
    """a compressed body is the same content in other bytes: a weak ETag of
    the same value, as nginx does. If-None-Match compares weakly, so a route
    that answers 304 on its own ETag keeps doing so"""
    if not etag:
        return None
    return etag if etag.startswith("W/") else "W/" + etag


# ---------------------------------------------------------------- dynamic
def should_compress(request, response) -> bool:
    if request.method == "HEAD":
        return False
    if response.status_code < 200 or response.status_code in (204, 206, 304):
        return False
    if response.direct_passthrough or response.is_streamed:
        return False
    if response.headers.get("Content-Encoding"):
        return False
    if response.headers.get("Content-Range") or request.headers.get("Range"):
        return False
    if "no-transform" in (response.headers.get("Cache-Control") or "").lower():
        return False
    if "attachment" in (response.headers.get("Content-Disposition") or "").lower():
        return False
    path = request.path or ""
    if path.startswith("/socket.io") or "/socket.io/" in path:
        return False
    return is_compressible_type(response.mimetype)


def compress_response(request, response):
    """the after_request body: compress ``response`` in place when it should be"""
    try:
        if not should_compress(request, response):
            if is_compressible_type(response.mimetype) and not response.headers.get("Content-Encoding"):
                # a cache must still key text on the encoding asked for
                add_vary(response.headers)
            return response
        add_vary(response.headers)
        encoding = choose_encoding(request.headers.get("Accept-Encoding"))
        if not encoding:
            return response
        data = response.get_data()
        if len(data) < MIN_BYTES:
            return response
        packed = compress_bytes(data, encoding)
        if len(packed) >= len(data):
            return response
        response.set_data(packed)
        response.headers["Content-Encoding"] = encoding
        response.headers["Content-Length"] = str(len(packed))
        etag = response.headers.get("ETag")
        if etag:
            response.headers["ETag"] = _weak_etag(etag)
    except Exception as e:  # noqa: BLE001 - a failed squeeze sends the plain body
        logger.debug("response compression skipped: %s", e)
    return response


# ---------------------------------------------------------------- static
class _StaticCache:
    """compressed static files, keyed by (path, encoding, mtime, size), held
    to a byte budget, oldest out first"""

    def __init__(self, budget: int = STATIC_CACHE_BYTES):
        self.budget = budget
        self.used = 0
        self.items: "OrderedDict[tuple, bytes]" = OrderedDict()
        self.lock = threading.Lock()

    def get(self, key):
        with self.lock:
            data = self.items.get(key)
            if data is not None:
                self.items.move_to_end(key)
            return data

    def put(self, key, data: bytes):
        if len(data) > self.budget:
            return
        with self.lock:
            old = self.items.pop(key, None)
            if old is not None:
                self.used -= len(old)
            self.items[key] = data
            self.used += len(data)
            while self.used > self.budget and self.items:
                _k, v = self.items.popitem(last=False)
                self.used -= len(v)

    def clear(self):
        with self.lock:
            self.items.clear()
            self.used = 0


_static_cache = _StaticCache()


def precompressed_path(full: str, encoding: str) -> str:
    return full + (".br" if encoding == "br" else ".gz")


def static_variant(full: str, encoding: str) -> Optional[Tuple[str, Optional[bytes], int, int]]:
    """(source of the encoded bytes, bytes or None when a file on disk holds
    them, mtime_ns, size of the original) for a static file, or None when it
    shouldn't or can't be compressed"""
    try:
        st = os.stat(full)
    except OSError:
        return None
    if st.st_size < MIN_BYTES:
        return None
    side = precompressed_path(full, encoding)
    try:
        sst = os.stat(side)
        if sst.st_mtime_ns >= st.st_mtime_ns and sst.st_size > 0:
            return side, None, st.st_mtime_ns, st.st_size
    except OSError:
        pass
    key = (full, encoding, st.st_mtime_ns, st.st_size)
    data = _static_cache.get(key)
    if data is None:
        try:
            with open(full, "rb") as fh:
                raw = fh.read()
        except OSError:
            return None
        data = compress_bytes(raw, encoding, static=True)
        if len(data) >= len(raw):
            return None
        _static_cache.put(key, data)
    return full, data, st.st_mtime_ns, st.st_size


def make_static_view(app, original_view: Callable, *, immutable: Callable[[str], bool],
                     enabled: Callable[[], bool] = lambda: True):
    """wrap Flask's static view: precompressed or cached compressed bytes when
    the browser takes them, the plain file (with ranges) otherwise"""
    from flask import Response, request, send_file
    from werkzeug.security import safe_join

    def static_view(filename):
        resp = None
        try:
            ext = os.path.splitext(filename)[1].lower()
            if ext in STATIC_EXTENSIONS and not request.headers.get("Range") and enabled():
                encoding = choose_encoding(request.headers.get("Accept-Encoding"))
                full = safe_join(app.static_folder, filename)
                if encoding and full and os.path.isfile(full):
                    variant = static_variant(full, encoding)
                    if variant is not None:
                        source, data, mtime_ns, size = variant
                        mimetype = mimetypes.guess_type(filename)[0] or "application/octet-stream"
                        if mimetype.startswith("text/") or mimetype in ("application/javascript", "image/svg+xml"):
                            mimetype_full = mimetype + ("; charset=utf-8" if "charset" not in mimetype else "")
                        else:
                            mimetype_full = mimetype
                        if data is None:
                            resp = send_file(source, mimetype=mimetype, conditional=False, etag=False,
                                             max_age=app.get_send_file_max_age(filename))
                            resp.direct_passthrough = True
                        else:
                            resp = Response(data, mimetype=mimetype)
                            max_age = app.get_send_file_max_age(filename)
                            if max_age is not None:
                                resp.cache_control.public = True
                                resp.cache_control.max_age = max_age
                        resp.headers["Content-Type"] = mimetype_full
                        resp.headers["Content-Encoding"] = encoding
                        resp.set_etag(f"{mtime_ns:x}-{size:x}-{encoding}")
                        resp.last_modified = datetime.fromtimestamp(mtime_ns / 1e9, tz=timezone.utc)
                        add_vary(resp.headers)
                        resp = resp.make_conditional(request)
        except Exception as e:  # noqa: BLE001 - anything odd: the plain file
            logger.debug("compressed static %s skipped: %s", filename, e)
            resp = None
        if resp is None:
            resp = original_view(filename=filename)
            if os.path.splitext(filename)[1].lower() in STATIC_EXTENSIONS:
                add_vary(resp.headers)
        if immutable(filename) and resp.status_code in (200, 304):
            resp.cache_control.public = True
            resp.cache_control.max_age = 31536000
            resp.cache_control.immutable = True
        return resp

    return static_view


_HASHED = re.compile(r"-[A-Za-z0-9_-]{8}\.(js|mjs|css)$")


def is_hashed_dist_asset(filename: str) -> bool:
    """vite's content-hashed output (dist/assets/name-HASH.js): the name
    changes with the content, so it can be cached for good"""
    name = filename.replace("\\", "/")
    return name.startswith("dist/assets/") and bool(_HASHED.search(name))


def env_enabled() -> bool:
    """SOULSYNC_COMPRESSION=0 turns it all off (a proxy that insists on doing
    it itself, or to rule it out while chasing a problem)"""
    return os.environ.get("SOULSYNC_COMPRESSION", "1").strip().lower() not in ("0", "false", "no", "off")


def install(app, *, enabled: Callable[[], bool] = env_enabled) -> None:
    """compress dynamic responses and serve static files compressed"""

    @app.after_request
    def _compress(response):
        from flask import request
        if not enabled():
            return response
        return compress_response(request, response)

    original = app.view_functions.get("static")
    if original is not None:
        app.view_functions["static"] = make_static_view(app, original, immutable=is_hashed_dist_asset,
                                                        enabled=enabled)


__all__ = [
    "MIN_BYTES", "choose_encoding", "compress_bytes", "is_compressible_type",
    "should_compress", "compress_response", "static_variant", "make_static_view",
    "is_hashed_dist_asset", "install", "brotli_available", "add_vary",
]
