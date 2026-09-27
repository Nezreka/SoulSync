"""app events for the automation engine, from anywhere in core/ or api/.

the video side has core/video/download_events for this; issues and music
requests needed the same without importing the engine (music-side infra that
web_server owns). web_server registers ONE forwarder to automation_engine.emit
at boot; with none registered (tests, early startup) publishing is a no-op.
event types must match a trigger in core/automation/blocks.py.
"""

from __future__ import annotations

from typing import Callable

from utils.logging_config import get_logger

logger = get_logger("app_events")

_forwarders: list[Callable[[str, dict], None]] = []


def register_forwarder(cb: Callable[[str, dict], None]) -> None:
    if cb not in _forwarders:
        _forwarders.append(cb)


def publish(event_type: str, data: dict | None = None) -> None:
    payload = data or {}
    for cb in list(_forwarders):
        try:
            cb(event_type, payload)
        except Exception:  # noqa: BLE001 - one bad subscriber never blocks the seam
            logger.exception("app event forwarder failed for %s", event_type)


def _reset_for_tests() -> None:
    _forwarders.clear()


__all__ = ["register_forwarder", "publish"]
