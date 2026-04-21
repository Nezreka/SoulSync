"""Gunicorn logger tweaks for SoulSync."""

from __future__ import annotations

import logging

from gunicorn.glogging import Logger as GunicornLogger
from utils.logging_config import ColoredFormatter


class FilteredGunicornLogger(GunicornLogger):
    """Gunicorn logger that skips noisy static and Socket.IO access logs."""

    _STATIC_PREFIXES = (
        "/static/",
        "/assets/",
        "/socket.io",
        "/favicon.ico",
        "/robots.txt",
    )

    _STATIC_SUFFIXES = (
        ".css",
        ".js",
        ".map",
        ".png",
        ".jpg",
        ".jpeg",
        ".gif",
        ".svg",
        ".ico",
        ".woff",
        ".woff2",
        ".ttf",
        ".eot",
    )

    def _should_skip_access_log(self, environ) -> bool:
        path = environ.get("PATH_INFO") or ""
        if not path:
            return False

        normalized = path if path.startswith("/") else f"/{path}"
        lower_path = normalized.lower()

        if any(
            lower_path == prefix.rstrip("/") or lower_path.startswith(prefix)
            for prefix in self._STATIC_PREFIXES
        ):
            return True

        return any(lower_path.endswith(suffix) for suffix in self._STATIC_SUFFIXES)

    def access(self, resp, req, environ, request_time):
        if self._should_skip_access_log(environ):
            return
        super().access(resp, req, environ, request_time)

    def setup(self, cfg):
        super().setup(cfg)

        app_like_formatter = ColoredFormatter(
            fmt="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
        error_level = getattr(logging, cfg.loglevel.upper(), logging.INFO)

        for handler in self.access_log.handlers:
            handler.setFormatter(app_like_formatter)
            handler.setLevel(logging.INFO)

        for handler in self.error_log.handlers:
            handler.setFormatter(app_like_formatter)
            handler.setLevel(error_level)
