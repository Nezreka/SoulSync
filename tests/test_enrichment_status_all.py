"""Bundled enrichment status hydrate (request-flood P2).

Page load fired ~13 music + ~15 video per-service /status GETs; each side now
has a /status-all bundle collected with per-service isolation (one failing
collector degrades to its own error field, never the whole response), and the
frontends hydrate from the bundle with per-service fallback.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from flask import Flask

from core.enrichment.api import create_blueprint
from core.enrichment.services import (
    EnrichmentService,
    clear_registry,
    register_services,
)

_ROOT = Path(__file__).resolve().parent.parent


class _Worker:
    def __init__(self, stats=None, raises=None):
        self._stats = stats or {"enabled": True, "running": True, "paused": False}
        self._raises = raises

    def get_stats(self):
        if self._raises:
            raise self._raises
        return self._stats


@pytest.fixture()
def client():
    clear_registry()
    register_services([
        EnrichmentService(id="alpha", display_name="Alpha",
                          worker_getter=lambda: _Worker({"enabled": True, "id": "a"})),
        EnrichmentService(id="beta", display_name="Beta",
                          worker_getter=lambda: _Worker({"enabled": True, "id": "b"})),
        EnrichmentService(id="broken", display_name="Broken",
                          worker_getter=lambda: _Worker(raises=RuntimeError("db locked"))),
    ])
    app = Flask(__name__)
    app.register_blueprint(create_blueprint())
    yield app.test_client()
    clear_registry()


def test_bundle_returns_every_service(client):
    body = client.get("/api/enrichment/status-all").get_json()
    assert set(body["services"]) == {"alpha", "beta", "broken"}
    assert body["services"]["alpha"]["id"] == "a"
    assert body["services"]["beta"]["id"] == "b"


def test_one_failing_collector_never_breaks_the_bundle(client):
    res = client.get("/api/enrichment/status-all")
    assert res.status_code == 200
    services = res.get_json()["services"]
    assert "error" in services["broken"]
    assert "error" not in services["alpha"]


def test_bundle_agrees_with_the_per_service_route(client):
    bundle = client.get("/api/enrichment/status-all").get_json()["services"]
    single = client.get("/api/enrichment/alpha/status").get_json()
    assert bundle["alpha"] == single


# ── wiring pins ───────────────────────────────────────────────────────────────

def test_music_frontend_hydrates_through_the_shim():
    js = (_ROOT / "webui" / "static" / "enrichment.js").read_text(encoding="utf-8", errors="replace")
    assert "function _enrichmentStatusFetch" in js
    assert "'/api/enrichment/status-all'" in js
    # every per-service loader was rewired — no raw per-service status fetch
    # remains outside the shim's own fallback
    import re
    assert not re.search(r"fetch\('/api/enrichment/[a-z_]+/status'\)", js)
    mgr = (_ROOT / "webui" / "static" / "enrichment-manager.js").read_text(encoding="utf-8", errors="replace")
    assert "_enrichmentStatusFetch(w.id)" in mgr


def test_video_backend_and_frontend_bundle():
    api = (_ROOT / "api" / "video" / "enrichment.py").read_text(encoding="utf-8", errors="replace")
    assert '"/enrichment/status-all"' in api
    mgr = (_ROOT / "webui" / "static" / "video" / "video-enrichment-manager.js").read_text(
        encoding="utf-8", errors="replace")
    assert "'/api/video/enrichment/status-all'" in mgr
    dash = (_ROOT / "webui" / "static" / "video" / "video-enrichment.js").read_text(
        encoding="utf-8", errors="replace")
    assert "'/api/video/enrichment/status-all'" in dash
    # per-service fallback survives on both surfaces
    assert "pollOne" in dash
