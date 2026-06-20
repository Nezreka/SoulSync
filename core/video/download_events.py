"""Video download events — a tiny publish/subscribe bridge.

ISOLATION: ``core/video`` must not import the automation engine (that's music-side
shared infra). So when a batch of video downloads finishes, the monitor publishes
to this callback registry instead of touching the engine directly. The shared side
(web_server) registers a callback that forwards to
``automation_engine.emit('video_batch_complete', …)`` — the same one-way bridge the
music ``web_scan_manager`` uses for ``library_scan_completed``.
"""

from __future__ import annotations

from typing import Callable

from utils.logging_config import get_logger

logger = get_logger("video_download_events")

_batch_complete_callbacks: list[Callable[[dict], None]] = []


def register_batch_complete_callback(cb: Callable[[dict], None]) -> None:
    """Subscribe to 'a batch of video downloads just finished'. Idempotent."""
    if cb not in _batch_complete_callbacks:
        _batch_complete_callbacks.append(cb)


def notify_batch_complete(data: dict | None = None) -> None:
    """Fire every registered batch-complete callback (best-effort; one failing
    subscriber never blocks the others or the monitor)."""
    payload = data or {}
    for cb in list(_batch_complete_callbacks):
        try:
            cb(payload)
        except Exception:
            logger.exception("video batch-complete callback failed")


def _reset_for_tests() -> None:
    _batch_complete_callbacks.clear()
