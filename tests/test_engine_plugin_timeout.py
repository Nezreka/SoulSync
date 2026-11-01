"""The downloads page lists live downloads with a per-client time limit.

One slow download client held every row of the page for ~97s, with nothing in
the log to say which (Oct 31 2026). A display caller passes plugin_timeout:
the slow client sits that refresh out and is named; the rest still show."""

from __future__ import annotations

import asyncio
import logging

from core.download_engine.engine import DownloadEngine


class _Plugin:
    def __init__(self, rows, delay=0.0):
        self.rows, self.delay = rows, delay

    async def get_all_downloads(self):
        await asyncio.sleep(self.delay)
        return list(self.rows)


def _engine(**plugins):
    engine = DownloadEngine()
    engine._plugins = dict(plugins)
    return engine


def test_a_slow_client_sits_the_refresh_out_and_is_named(caplog):
    engine = _engine(fast=_Plugin(["a"]), slow=_Plugin(["b"], delay=5))
    seen = []
    handler = logging.Handler()
    handler.emit = lambda record: seen.append(record.getMessage())
    logging.getLogger("soulsync").addHandler(handler)
    try:
        rows = asyncio.run(engine.get_all_downloads(plugin_timeout=0.2))
    finally:
        logging.getLogger("soulsync").removeHandler(handler)
    assert rows == ["a"]
    assert any("slow took over" in m for m in seen), seen


def test_without_a_limit_every_client_is_waited_for():
    engine = _engine(fast=_Plugin(["a"]), slow=_Plugin(["b"], delay=0.3))
    assert asyncio.run(engine.get_all_downloads()) == ["a", "b"]
