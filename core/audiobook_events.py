"""Automation events from the audiobook subsystem.

The music importers tell the automation engine what happened (a download
failed, a book was added to the wishlist, an import finished). The audiobook
modules did not, so none of those triggers ever fired for a book. This is the
one place they publish from.

Events go out on the shared app-event bus (``core.app_events``) rather than to
the engine itself: the audiobook modules must not reach into music-side state.
With no forwarder registered (tests, early startup) publishing is a no-op, and
a failed publish never fails the work that was being announced.

Event types must match a trigger in ``core/automation/blocks.py``. Values are
sent as strings, like every other event the engine carries.
"""

from __future__ import annotations

from typing import Any, Dict, Iterable

from utils.logging_config import get_logger

logger = get_logger("audiobook_events")


def announce(event_type: str, **data: Any) -> None:
    """Publish ``event_type`` with ``data`` (best-effort, never raises)."""
    try:
        from core.app_events import publish

        publish(event_type, {key: "" if value is None else str(value) for key, value in data.items()})
    except Exception as exc:                                # noqa: BLE001
        logger.debug("Could not announce %s: %s", event_type, exc)


def first_author(book: Dict[str, Any], fallback: str = "") -> str:
    """The first author of a catalogue-shaped book dict."""
    names = book.get("author_names") or []
    return str(names[0] if names else fallback or "")


def series_title(book: Dict[str, Any]) -> str:
    series = book.get("series") or []
    return str((series[0] or {}).get("title") or "") if series else ""


def join_titles(titles: Iterable[str], limit: int = 5) -> str:
    """A short, notification-sized list of titles."""
    titles = [str(t) for t in titles if t]
    shown = ", ".join(titles[:limit])
    return shown + (f" (+{len(titles) - limit} more)" if len(titles) > limit else "")
