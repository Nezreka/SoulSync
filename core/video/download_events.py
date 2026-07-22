"""Video events — a tiny publish/subscribe bridge to the automation engine.

ISOLATION: ``core/video`` (and ``database/``) must not import the automation
engine (that's music-side shared infra). Video seams publish typed events to
this registry instead; the shared side (web_server) registers ONE forwarder
that relays every event to ``automation_engine.emit(event_type, data)`` — the
same one-way bridge the music ``web_scan_manager`` uses for
``library_scan_completed``. Event types must match a trigger block in
``core/automation/blocks.py`` or they fire into the void (harmless).

Publishing is best-effort and synchronous-cheap: with no forwarder registered
(tests, early startup) it's a no-op; the engine side threads per event.
"""

from __future__ import annotations

from typing import Callable

from utils.logging_config import get_logger

logger = get_logger("video_download_events")

_forwarders: list[Callable[[str, dict], None]] = []


def register_event_forwarder(cb: Callable[[str, dict], None]) -> None:
    """Subscribe to every published video event. Idempotent."""
    if cb not in _forwarders:
        _forwarders.append(cb)


def publish(event_type: str, data: dict | None = None) -> None:
    """Fire every registered forwarder (best-effort; one failing subscriber
    never blocks the others or the publishing seam)."""
    payload = data or {}
    for cb in list(_forwarders):
        try:
            cb(event_type, payload)
        except Exception:
            logger.exception("video event forwarder failed for %s", event_type)


def notify_batch_complete(data: dict | None = None) -> None:
    """'A batch of video downloads just finished' — kept as a named wrapper for
    the monitor; routes through the generic bus like every other event."""
    publish("video_batch_complete", data)


def _reset_for_tests() -> None:
    _forwarders.clear()
