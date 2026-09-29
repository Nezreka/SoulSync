"""H7: a healthy multi-hour watchlist scan must not be declared stuck.

The stuck detector uses a fixed 900s timeout, but a real scan's mandatory
per-artist sleeps exceed that — so the flag was auto-reset mid-scan and a
second scan started on top of the first. The fix: the scan thread heartbeats
while alive, and the stuck detector only resets when the heartbeat itself is
stale.

The web_server.py stuck-detection functions are exercised VERBATIM
(AST-extracted from the real file); the heartbeat helper is the real one
from core/watchlist/auto_scan.py.
"""
import ast
import logging
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import core.watchlist.auto_scan as autosc

REPO_ROOT = Path(__file__).resolve().parents[2]
_WS_SRC = (REPO_ROOT / "web_server.py").read_text()
_WS_TREE = ast.parse(_WS_SRC)

logger = logging.getLogger("tests")


def _extract_ws_func(name):
    for node in ast.walk(_WS_TREE):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    raise AssertionError(f"{name} not found in web_server.py")


def _stuck_detection_ns():
    """Namespace with the fakes the two stuck-detection functions need."""
    ns = {
        "logger": logger,
        "watchlist_timer_lock": threading.Lock(),
        "_rt_state": SimpleNamespace(
            wishlist_auto_processing=False,
            wishlist_auto_processing_timestamp=0,
        ),
        "_reset_wishlist_flag_if_stuck": lambda *a, **k: False,
        "_is_wishlist_actually_processing": lambda *a, **k: False,
        "watchlist_auto_scanning": False,
        "watchlist_auto_scanning_timestamp": 0,
        "watchlist_auto_scanning_heartbeat": 0,
    }
    for name in ("check_and_recover_stuck_flags", "is_watchlist_actually_scanning"):
        exec(
            compile(ast.Module(body=[_extract_ws_func(name)], type_ignores=[]),
                    f"<web_server:{name}>", "exec"),
            ns,
        )
    return ns


def test_h7_live_scan_with_fresh_heartbeat_is_not_stuck():
    """Flag older than 900s BUT heartbeat fresh → still scanning, no reset."""
    ns = _stuck_detection_ns()
    now = time.time()
    ns["watchlist_auto_scanning"] = True
    ns["watchlist_auto_scanning_timestamp"] = now - 1000  # started >15 min ago
    ns["watchlist_auto_scanning_heartbeat"] = now  # ...but alive right now

    assert ns["is_watchlist_actually_scanning"]() is True
    assert ns["check_and_recover_stuck_flags"]() is False
    assert ns["watchlist_auto_scanning"] is True, "live scan's flag was reset"


def test_h7_dead_scan_with_stale_heartbeat_is_recovered():
    """Flag AND heartbeat older than 900s → stuck, flag reset (control)."""
    ns = _stuck_detection_ns()
    now = time.time()
    ns["watchlist_auto_scanning"] = True
    ns["watchlist_auto_scanning_timestamp"] = now - 1000
    ns["watchlist_auto_scanning_heartbeat"] = now - 1000

    # is_watchlist_actually_scanning() recovers the stuck flag itself
    assert ns["is_watchlist_actually_scanning"]() is False
    assert ns["watchlist_auto_scanning"] is False
    assert ns["watchlist_auto_scanning_heartbeat"] == 0

    # and the standalone recovery path also resets a stale flag
    ns["watchlist_auto_scanning"] = True
    ns["watchlist_auto_scanning_timestamp"] = now - 1000
    ns["watchlist_auto_scanning_heartbeat"] = now - 1000
    assert ns["check_and_recover_stuck_flags"]() is True
    assert ns["watchlist_auto_scanning"] is False
    assert ns["watchlist_auto_scanning_heartbeat"] == 0


def test_h7_fresh_scan_without_heartbeat_yet_is_not_stuck():
    """A scan that just started (heartbeat not yet ticked) must not look stuck."""
    ns = _stuck_detection_ns()
    now = time.time()
    ns["watchlist_auto_scanning"] = True
    ns["watchlist_auto_scanning_timestamp"] = now
    ns["watchlist_auto_scanning_heartbeat"] = 0

    assert ns["is_watchlist_actually_scanning"]() is True
    assert ns["check_and_recover_stuck_flags"]() is False
    assert ns["watchlist_auto_scanning"] is True


def test_h7_heartbeat_ticks_while_scan_is_alive():
    deps = SimpleNamespace(
        watchlist_timer_lock=threading.Lock(),
        watchlist_auto_scanning=True,
        watchlist_auto_scanning_heartbeat=0.0,
    )
    stop = autosc._start_scan_heartbeat(deps, interval=0.05)
    try:
        deadline = time.time() + 5
        while deps.watchlist_auto_scanning_heartbeat == 0.0 and time.time() < deadline:
            time.sleep(0.02)
        assert deps.watchlist_auto_scanning_heartbeat > 0, "heartbeat never ticked"
        assert abs(deps.watchlist_auto_scanning_heartbeat - time.time()) < 5
    finally:
        stop()
    frozen = deps.watchlist_auto_scanning_heartbeat
    time.sleep(0.2)
    assert deps.watchlist_auto_scanning_heartbeat == frozen, "heartbeat kept ticking after stop()"


def test_h7_heartbeat_does_not_tick_when_flag_cleared():
    deps = SimpleNamespace(
        watchlist_timer_lock=threading.Lock(),
        watchlist_auto_scanning=False,
        watchlist_auto_scanning_heartbeat=0.0,
    )
    stop = autosc._start_scan_heartbeat(deps, interval=0.05)
    try:
        time.sleep(0.25)
        assert deps.watchlist_auto_scanning_heartbeat == 0.0
    finally:
        stop()
