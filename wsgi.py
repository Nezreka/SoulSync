"""WSGI entrypoint: HTTP is available while protected database boot runs."""
import os
import sys

from core.request_watchdog import track_requests
from core.startup_application import StartupApplication


def gil_switch_interval_seconds() -> float:
    """How long a thread may hold the GIL before another gets it: 1 ms, not
    CPython's 5 (SOULSYNC_GIL_SWITCH_INTERVAL_MS; 0 keeps Python's default).

    One process runs every request and dozens of background threads. A thread
    that gives the GIL up and wants it back (every row sqlite3 hands to Python,
    every socket read) waits up to the interval each time while a CPU-bound
    thread runs, so a 15k-row query that takes 24 ms alone took 39 s next to
    two busy threads, and status pages that use 50 ms of CPU took seconds.
    At 1 ms the same query took 4.9 s, with the busy threads doing as much work.
    """
    try:
        ms = float(os.environ.get("SOULSYNC_GIL_SWITCH_INTERVAL_MS") or 1)
    except ValueError:
        ms = 1.0
    return ms / 1000 if ms > 0 else sys.getswitchinterval()


sys.setswitchinterval(gil_switch_interval_seconds())


def _load_application():
    from web_server import app, start_runtime_services

    start_runtime_services()
    return app


# outermost: a request counts from the moment it holds a gunicorn thread
application = track_requests(StartupApplication(_load_application))
