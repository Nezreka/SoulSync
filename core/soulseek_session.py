"""Is slskd logged in to the Soulseek server right now?

One process-wide answer, shared by the download path, the Downloads and
dashboard banner, and Soulseek chat. A poller asks slskd
(``/api/v0/server``, falling back to the application state) every minute,
every 15 seconds while logged out so downloads resume quickly.

While slskd is logged out (a temporary server ban, a reconnect), Soulseek
download tasks wait as "Waiting for Soulseek login" instead of burning
their retries and falling through to worse sources. They resume with the
same candidate once logged back in. After ``soulseek.login_wait_minutes``
(default 45) of waiting a task moves on to its next source.

Only slskd's own "not logged in" answer counts as logged out: slskd being
unreachable is a different problem, and reads as unknown here.

The ``soulseek_logged_out`` and ``soulseek_logged_in`` automation events
fire once per change, so users can be notified.
"""

from __future__ import annotations

import threading
import time
from typing import Any, Callable, Dict, Optional

from utils.logging_config import get_logger

logger = get_logger("soulseek_session")

LOGGED_IN = "logged_in"
LOGGED_OUT = "logged_out"
UNKNOWN = "unknown"

DEFAULT_WAIT_MINUTES = 45
WAITING_LABEL = "Waiting for Soulseek login"

POLL_SECONDS = 60
POLL_SECONDS_LOGGED_OUT = 15

_lock = threading.Lock()
_state: Dict[str, Any] = {"state": UNKNOWN, "since": None, "checked_at": None}
_emit: Optional[Callable[[str, dict], None]] = None
_poller: Optional[threading.Thread] = None
_poller_stop = threading.Event()


def set_event_emitter(emit: Optional[Callable[[str, dict], None]]) -> None:
    """Where the logged out / logged in events go (the automation engine)."""
    global _emit
    _emit = emit
    with _lock:
        pending = _state["state"] == LOGGED_OUT
    if pending and emit:
        _send(emit, "soulseek_logged_out", {"reason": "not_logged_in"})


def _send(emit, event: str, data: dict) -> None:
    try:
        emit(event, data)
    except Exception as e:  # noqa: BLE001 - an alert never breaks the download path
        logger.debug("soulseek session event failed: %s", e)


def note(logged_in: Optional[bool], *, now: Optional[float] = None) -> Optional[str]:
    """Record what slskd said. ``None`` (slskd didn't answer) changes nothing.

    Returns the event sent, if this answer was a change.
    """
    if logged_in is None:
        with _lock:
            _state["checked_at"] = now or time.time()
        return None
    now = now or time.time()
    event = None
    with _lock:
        previous = _state["state"]
        _state["checked_at"] = now
        if logged_in:
            if previous == LOGGED_OUT:
                event = "soulseek_logged_in"
                data = {"was_out_minutes": round((now - (_state["since"] or now)) / 60, 1)}
            _state["state"] = LOGGED_IN
            _state["since"] = None
        else:
            if previous != LOGGED_OUT:
                event = "soulseek_logged_out"
                data = {"reason": "not_logged_in"}
                _state["since"] = now
            _state["state"] = LOGGED_OUT
    if event:
        logger.warning("Soulseek: %s", "logged back in" if logged_in else
                       "slskd is not logged in to the Soulseek server; downloads will wait")
        if _emit:
            _send(_emit, event, data)
    return event


def snapshot() -> Dict[str, Any]:
    """``{state, since, checked_at, logged_out}`` for the status payload."""
    with _lock:
        out = dict(_state)
    out["logged_out"] = out["state"] == LOGGED_OUT
    return out


def is_logged_out() -> bool:
    with _lock:
        return _state["state"] == LOGGED_OUT


def wait_seconds(config_manager: Any = None) -> float:
    """How long a task waits for the login before moving on (seconds)."""
    cfg = config_manager
    if cfg is None:
        try:
            from core.settings import config_manager as cfg
        except Exception:  # noqa: BLE001
            cfg = None
    minutes = DEFAULT_WAIT_MINUTES
    if cfg is not None:
        try:
            raw = cfg.get("soulseek.login_wait_minutes", DEFAULT_WAIT_MINUTES)
            minutes = float(DEFAULT_WAIT_MINUTES if raw is None else raw)
        except (TypeError, ValueError):
            minutes = DEFAULT_WAIT_MINUTES
    return max(0.0, minutes) * 60.0


def should_wait(task: Dict[str, Any], now: Optional[float] = None, config_manager: Any = None) -> bool:
    """Whether a Soulseek task should wait for the login right now.

    Marks the task while it waits (``waiting_for_login`` and the time it
    began; the Downloads page shows "Waiting for Soulseek login") and clears
    the mark once the login is back. A task that has waited longer than the deadline stops
    waiting and is marked ``_soulseek_login_gave_up`` so its next search
    skips Soulseek. Call with the task's own dict (under tasks_lock where
    the caller holds it).
    """
    now = now or time.time()
    if not is_logged_out():
        # a new logout later is a new wait, with its own deadline
        task.pop("_soulseek_login_gave_up", None)
        task.pop("_login_wait_started", None)
        task.pop("waiting_for_login", None)
        return False
    if task.get("_soulseek_login_gave_up"):
        return False
    started = task.get("_login_wait_started")
    if not started:
        started = now
        task["_login_wait_started"] = now
    if now - started >= wait_seconds(config_manager):
        task["_soulseek_login_gave_up"] = True
        task.pop("waiting_for_login", None)
        logger.warning("Soulseek still logged out after %.0f min; task moves on to its next source",
                       (now - started) / 60)
        return False
    task["waiting_for_login"] = True
    return True


def search_plan(orchestrator: Any, exclude: Optional[list] = None) -> str:
    """What a task's next search does about Soulseek right now.

    ``'search'`` logged in (or not using Soulseek): search as usual.
    ``'wait'`` logged out and Soulseek is the first source the search tries:
    the task waits for the login (its first choice of source is kept).
    ``'skip'`` logged out and another source comes first: Soulseek is left
    out of this search, so no query is spent on a server that can't answer.
    """
    if not is_logged_out():
        return "search"
    mode = str(getattr(orchestrator, "mode", "") or "").lower()
    if mode == "soulseek":
        return "wait"
    if mode != "hybrid":
        return "search"
    try:
        chain = list(orchestrator._resolve_source_chain())
    except Exception:  # noqa: BLE001 - an unreadable chain: the configured order
        chain = list(getattr(orchestrator, "hybrid_order", None) or [])
    blocked = {str(s).lower() for s in (exclude or []) if s}
    chain = [str(s).lower() for s in chain if str(s).lower() not in blocked]
    if "soulseek" not in chain:
        return "search"
    return "wait" if chain[0] == "soulseek" else "skip"


# ── the poller ──────────────────────────────────────────────────────────

def probe(client: Any, run_async: Callable) -> Optional[bool]:
    """Ask slskd once and record the answer. Never raises."""
    getter = getattr(client, "get_login_state", None)
    if getter is None or not getattr(client, "base_url", None):
        return None
    try:
        answer = run_async(getter())
    except Exception as e:  # noqa: BLE001 - unreachable is unknown
        logger.debug("soulseek login probe failed: %s", e)
        answer = None
    note(answer)
    return answer


def start_poller(get_client: Callable[[], Any], run_async: Callable) -> None:
    """Start the one background poller (idempotent)."""
    global _poller
    if _poller is not None and _poller.is_alive():
        return
    _poller_stop.clear()

    def _loop():
        while not _poller_stop.is_set():
            try:
                client = get_client()
                if client is not None:
                    probe(client, run_async)
            except Exception as e:  # noqa: BLE001
                logger.debug("soulseek session poll failed: %s", e)
            wait = POLL_SECONDS_LOGGED_OUT if is_logged_out() else POLL_SECONDS
            _poller_stop.wait(wait)

    _poller = threading.Thread(target=_loop, name="soulseek-session", daemon=True)
    _poller.start()


def stop_poller() -> None:
    _poller_stop.set()


def _reset_for_tests() -> None:
    global _emit
    with _lock:
        _state.update(state=UNKNOWN, since=None, checked_at=None)
    _emit = None
