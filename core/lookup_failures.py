"""Count the lookups an outside service could not answer.

The metadata clients mostly turn a failure (a throttle, an outage, a refused
key, a dropped connection) into ``None`` or ``[]`` instead of raising, so a job
cannot tell "nothing there" from "could not ask" by watching for exceptions. A
job wraps its lookups in ``watch_lookup_failures()`` and checks the count.

Two sources feed it. Every HTTP client sends through ``requests``, so the
adapter is wrapped once (on first use) and counts a failed request itself. A
client that does not use ``requests`` (Hydrabase talks over a websocket) calls
``report_lookup_failure()`` where it gives up. Only the thread that opened the
watch is counted.
"""

from __future__ import annotations

import json
import threading
from contextlib import contextmanager
from typing import Iterator

import requests

from utils.logging_config import get_logger

logger = get_logger("lookup_failures")

_active = threading.local()
_install_lock = threading.Lock()
_installed = False
_send = None


class LookupFailures:
    """How many requests in the watched block could not be answered."""

    def __init__(self):
        self.count = 0


def _could_not_answer(response) -> bool:
    """A reply that says nothing about whether the item exists: a throttle, an
    outage, a refused key. A 404 and a normal empty result are answers."""
    status = getattr(response, 'status_code', 200)
    if status >= 400:
        return status != 404
    # Deezer reports a spent quota as a 200 with an error body. "Not found" is
    # a DataException and is an answer.
    try:
        content = response.content
        if len(content) < 2048 and b'"error"' in content:
            error = json.loads(content).get('error')
            return isinstance(error, dict) and error.get('type') != 'DataException'
    except Exception as e:  # an unreadable body is not evidence of a failure
        logger.debug("could not read a reply body to judge it: %s", e)
    return False


def _install() -> None:
    global _installed, _send
    with _install_lock:
        if _installed:
            return
        _send = requests.adapters.HTTPAdapter.send

        def send(self, request, *args, **kwargs):
            watcher = getattr(_active, 'watcher', None)
            try:
                response = _send(self, request, *args, **kwargs)
            except Exception:
                if watcher is not None:
                    watcher.count += 1
                raise
            if watcher is not None and _could_not_answer(response):
                watcher.count += 1
            return response

        requests.adapters.HTTPAdapter.send = send
        _installed = True


@contextmanager
def watch_lookup_failures() -> Iterator[LookupFailures]:
    """Count the requests this thread makes that could not be answered.

    Every metadata client sends through ``requests``, so this sees a 503 or a
    403 throttle that the client then swallows into an empty result. Requests
    from other threads are not counted.
    """
    _install()
    watcher = LookupFailures()
    previous = getattr(_active, 'watcher', None)
    _active.watcher = watcher
    try:
        yield watcher
    finally:
        _active.watcher = previous


def report_lookup_failure() -> None:
    """For a client that does not send through ``requests``: say that a lookup
    could not be answered. Costs nothing when nobody is watching."""
    watcher = getattr(_active, 'watcher', None)
    if watcher is not None:
        watcher.count += 1
