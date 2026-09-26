"""tell one profile something happened: your request was approved, your issue
got a reply, the album you asked for is here.

two halves, both best-effort. the note is journaled into that profile's
notification history (so someone who wasn't looking finds it later) and pushed
over the socket to their profile room (so someone who is gets a toast). the
socket side is registered by web_server at boot, which keeps this importable
from core/ and database-free tests.
"""

from __future__ import annotations

from typing import Callable, Optional

from utils.logging_config import get_logger

logger = get_logger("profile_notify")

_emitter: Optional[Callable[[str, dict, str], None]] = None
_journal: Optional[Callable[[int, str, str], None]] = None


def register_emitter(emit: Callable[[str, dict, str], None]) -> None:
    """``emit(event, payload, room)``: web_server hands in socketio.emit."""
    global _emitter
    _emitter = emit


def register_journal(journal: Callable[[int, str, str], None]) -> None:
    """``journal(profile_id, type, message)``: overrides the default db write."""
    global _journal
    _journal = journal


def _default_journal(profile_id: int, kind: str, message: str) -> None:
    from database.music_database import get_database

    get_database().add_notifications([{"type": kind, "message": message}], profile_id=profile_id)


def notify_profile(profile_id, message: str, kind: str = "info", link: Optional[str] = None) -> bool:
    """journal + push. returns True when at least the journal took it."""
    try:
        pid = int(profile_id)
    except (TypeError, ValueError):
        return False
    message = str(message or "").strip()[:500]
    if not message:
        return False
    if kind not in ("success", "error", "info", "warning"):
        kind = "info"
    journaled = False
    try:
        (_journal or _default_journal)(pid, kind, message)
        journaled = True
    except Exception:  # noqa: BLE001 - a note is never worth failing the action over
        logger.exception("could not journal a note for profile %s", pid)
    if _emitter is not None:
        try:
            _emitter("profile:notify", {"message": message, "type": kind, "link": link or ""},
                     f"profile:{pid}")
        except Exception:  # noqa: BLE001
            logger.debug("profile notify push failed for %s", pid, exc_info=True)
    return journaled


def _reset_for_tests() -> None:
    global _emitter, _journal
    _emitter = None
    _journal = None


__all__ = ["notify_profile", "register_emitter", "register_journal"]
