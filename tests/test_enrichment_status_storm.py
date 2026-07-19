"""The post-restart enrichment status storm (Boulder's 30s page loads).

Mechanism: the frontend's per-source HTTP pollers are only a FALLBACK (each is
guarded by `if (socketConnected) return`), but at 2s x 14 sources — each status
call running live COUNT queries that contend with the just-resumed workers —
they saturated the request queue, which delayed the socket.io handshake that
would have silenced them. A vicious circle that only broke when the socket
finally connected.

Two-sided fix, both covered here:
  · server: /api/enrichment/<id>/status serves worker.get_stats() through a
    2s TTL cache (stacked polls cost ONE stats read per window); pause/resume
    invalidate so clicks reflect instantly
  · client: the fallback pollers run at 10s (the websocket push owns the 2s
    freshness), guards stay in place
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict

import pytest
from flask import Flask

from core.enrichment import api as eapi
from core.enrichment.services import EnrichmentService, clear_registry, register_services

_ROOT = Path(__file__).resolve().parent.parent
_ENRICH_JS = (_ROOT / "webui" / "static" / "enrichment.js").read_text(encoding="utf-8")


class _CountingWorker:
    def __init__(self):
        self.calls = 0
        self.paused = False

    def get_stats(self) -> Dict[str, Any]:
        self.calls += 1
        return {"enabled": True, "running": True, "paused": self.paused, "calls": self.calls}

    def pause(self):
        self.paused = True

    def resume(self):
        self.paused = False


@pytest.fixture()
def client():
    clear_registry()
    eapi._invalidate_status_cache()
    app = Flask(__name__)
    app.register_blueprint(eapi.create_blueprint())
    yield app.test_client()
    clear_registry()
    eapi._invalidate_status_cache()


def test_stacked_status_polls_cost_one_stats_read(client):
    w = _CountingWorker()
    register_services([EnrichmentService(id="amazon", display_name="Amazon",
                                         worker_getter=lambda: w)])
    for _ in range(6):   # a poll pile-up inside one TTL window
        assert client.get("/api/enrichment/amazon/status").status_code == 200
    assert w.calls == 1, f"the TTL cache should collapse stacked polls, got {w.calls} reads"


def test_cache_expires_after_ttl(client, monkeypatch):
    w = _CountingWorker()
    register_services([EnrichmentService(id="amazon", display_name="Amazon",
                                         worker_getter=lambda: w)])
    t = [1000.0]
    monkeypatch.setattr(eapi.time, "monotonic", lambda: t[0])
    client.get("/api/enrichment/amazon/status")
    t[0] += eapi._STATUS_TTL_SECONDS + 0.1
    client.get("/api/enrichment/amazon/status")
    assert w.calls == 2


def test_cache_is_per_service(client):
    a, b = _CountingWorker(), _CountingWorker()
    register_services([
        EnrichmentService(id="amazon", display_name="Amazon", worker_getter=lambda: a),
        EnrichmentService(id="bandcamp", display_name="Bandcamp", worker_getter=lambda: b),
    ])
    client.get("/api/enrichment/amazon/status")
    client.get("/api/enrichment/bandcamp/status")
    assert (a.calls, b.calls) == (1, 1)


def test_pause_and_resume_reflect_instantly_despite_cache(client):
    w = _CountingWorker()
    register_services([EnrichmentService(id="amazon", display_name="Amazon",
                                         worker_getter=lambda: w)])
    assert client.get("/api/enrichment/amazon/status").get_json()["paused"] is False
    assert client.post("/api/enrichment/amazon/pause").status_code == 200
    assert client.get("/api/enrichment/amazon/status").get_json()["paused"] is True
    assert client.post("/api/enrichment/amazon/resume").status_code == 200
    assert client.get("/api/enrichment/amazon/status").get_json()["paused"] is False


# ---------------------------------------------------------------------------
# Client contract: fallback pollers slowed, websocket stays primary
# ---------------------------------------------------------------------------

def test_fallback_pollers_are_10s_and_guarded():
    import re
    assert not re.search(r"setInterval\(update\w+Status, 2000\)", _ENRICH_JS), \
        "a 2s status poller survived — the fallback must not race the websocket"
    assert _ENRICH_JS.count("setInterval(update") == _ENRICH_JS.count(", 10000)") \
        or "10000); // fallback only" in _ENRICH_JS
    # every poller still defers to the socket + hidden tabs
    assert _ENRICH_JS.count("if (socketConnected) return") >= 14
    assert "if (document.hidden) return" in _ENRICH_JS
