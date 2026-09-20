"""Downloads cancel endpoint keeps Acquisition correlation observational."""

from __future__ import annotations

import sys
import types
from unittest.mock import patch


from flask import Blueprint, Flask

from api import downloads as downloads_mod


class _Orchestrator:
    async def cancel_download(self, download_id, username, remove=False):
        assert (download_id, username, remove) == ("legacy-1", "peer", True)
        return True


def _app():
    app = Flask(__name__)
    app.soulsync = {"download_orchestrator": _Orchestrator()}
    bp = Blueprint("v1", __name__, url_prefix="/api/v1")
    with patch.object(downloads_mod, "require_api_key", lambda func: func):
        downloads_mod.register_routes(bp)
    app.register_blueprint(bp)
    return app


def test_cancel_endpoint_stays_successful_when_correlation_callback_fails(monkeypatch):
    import core.acquisition.pipeline_callback as callback

    monkeypatch.setattr(
        callback, "notify_correlated_grab_cancelled",
        lambda _download_id: (_ for _ in ()).throw(RuntimeError("db unavailable")),
    )
    with _app().test_client() as client:
        response = client.post(
            "/api/v1/downloads/legacy-1/cancel", json={"username": "peer"})

    assert response.status_code == 200
    assert response.get_json()["success"] is True
