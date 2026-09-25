"""an amazon search must not freeze the rest of the app.

the amazon source's coroutines run on the one event loop the whole app
shares (utils.async_helpers.run_async), next to search, downloads, the slskd
status poll and chat. the t2tunes calls underneath are blocking http with
three tries at a 30s timeout, so a slow proxy held that loop for up to ~90s
and everything else waited. they run on a worker thread now.

real plugin, real shared loop, a t2tunes stand-in that takes real time.
"""

from __future__ import annotations

import asyncio
import threading
import time
from unittest.mock import Mock

from core.amazon_download_client import AmazonDownloadClient
from utils.async_helpers import run_async

SLOW = 0.6


def _slow(result):
    def call(*_a, **_k):
        time.sleep(SLOW)
        return result
    return call


def _plugin():
    # the first database open in a process runs the schema setup; the server
    # does that at boot, so warm it here rather than time it
    from core.quality.source_map import quality_tier_for_source
    quality_tier_for_source("amazon", default="flac")

    plugin = AmazonDownloadClient.__new__(AmazonDownloadClient)
    plugin._client = Mock()
    plugin._client.search_raw.side_effect = _slow([])
    plugin._client.is_authenticated.side_effect = _slow(True)
    plugin._quality = "flac"
    return plugin


def _worst_wait_while(coro_factory):
    """run the coroutine through run_async on a thread, and time a trivial
    call through the same loop while it's busy."""
    out = {}
    worker = threading.Thread(target=lambda: out.setdefault("r", run_async(coro_factory())))
    worker.start()
    time.sleep(0.1)
    worst = 0.0
    while worker.is_alive():
        started = time.monotonic()
        run_async(asyncio.sleep(0))
        worst = max(worst, time.monotonic() - started)
        time.sleep(0.02)
    worker.join()
    return out["r"], worst


def test_a_slow_search_leaves_the_loop_free():
    plugin = _plugin()
    result, worst = _worst_wait_while(lambda: plugin.search("adele hello"))
    assert result == ([], [])
    assert worst < 0.2, f"search held the shared loop for {worst:.2f}s"


def test_a_slow_connection_check_leaves_the_loop_free():
    plugin = _plugin()
    result, worst = _worst_wait_while(plugin.check_connection)
    assert result is True
    assert worst < 0.2, f"check_connection held the shared loop for {worst:.2f}s"
