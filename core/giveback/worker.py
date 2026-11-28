"""the background round that gives back, slowly (#2335).

every ``INTERVAL`` seconds, give or take a random few minutes (musicbrainz
asks apps not to fire at fixed clock times), one acoustid round and one isrc
round run, each only when its switch is on. a round sends one request to
acoustid and at most ``isrc_submit.BATCH`` + 1 to musicbrainz, a second
apart. with both switches off the thread only sleeps.
"""

from __future__ import annotations

import random
import threading
import time
from contextlib import closing
from typing import Any, Callable, Dict, Optional

from core.giveback import acoustid_submit, common, isrc_submit, store
from utils.logging_config import get_logger

logger = get_logger("giveback.worker")

INTERVAL = 15 * 60
JITTER = 5 * 60
FIRST_WAIT = 10 * 60

_lock = threading.Lock()
_state: Dict[str, Any] = {'started': False, 'running': False, 'last': {}, 'last_at': None}
_wake = threading.Event()


def run_once(connect: Callable[[], Any], cfg=None) -> Dict[str, Any]:
    """one round of each part that is on. never runs twice at once"""
    if not _lock.acquire(blocking=False):
        return {'busy': True}
    try:
        _state['running'] = True
        out: Dict[str, Any] = {}
        for name, part in (('acoustid', acoustid_submit), ('isrc', isrc_submit)):
            try:
                out[name] = part.run_round(connect, cfg)
            except Exception as e:  # noqa: BLE001 - one part failing leaves the other
                logger.warning("give back %s round failed: %s", name, e)
                out[name] = {'error': 'Something went wrong; it will try again later'}
        _state['last'] = out
        _state['last_at'] = time.time()
        return out
    finally:
        _state['running'] = False
        _lock.release()


def anything_on(cfg=None) -> bool:
    return common.enabled(common.ACOUSTID_SETTING, cfg) or common.enabled(common.ISRC_SETTING, cfg)


def status(connect: Callable[[], Any]) -> Dict[str, Any]:
    with closing(connect()) as conn:
        counts = store.counts(conn)
    return {
        'counts': counts,
        'running': bool(_state['running']),
        'last': _state['last'],
        'last_at': _state['last_at'],
    }


def nudge() -> None:
    """run a round now instead of at the next turn"""
    _wake.set()


def start(get_database: Callable[[], Any], cfg=None, *, version: Optional[str] = None) -> None:
    """once per process, from web_server.start_runtime_services"""
    if version:
        common.set_app_version(version)
    if _state['started']:
        return
    _state['started'] = True

    def connect():
        return get_database()._get_connection()

    def loop():
        _wake.wait(FIRST_WAIT)
        while True:
            _wake.clear()
            if anything_on(cfg):
                run_once(connect, cfg)
            _wake.wait(INTERVAL + random.uniform(-JITTER, JITTER))

    threading.Thread(target=loop, name='giveback', daemon=True).start()
