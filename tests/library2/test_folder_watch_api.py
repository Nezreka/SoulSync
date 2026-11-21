"""Live folder watching: the status routes Settings > Library > Folders reads."""

from __future__ import annotations

from flask import Flask

from api import folder_watch as routes
from core.library import folder_watch


def _client(monkeypatch):
    monkeypatch.setattr("core.profile_context.is_admin_request", lambda *a, **k: True, raising=False)
    app = Flask(__name__)
    app.register_blueprint(routes.create_blueprint())
    return app.test_client()


def test_status_without_a_running_watcher(monkeypatch):
    monkeypatch.setattr(folder_watch, "_service", None)
    body = _client(monkeypatch).get("/api/library/folder-watch").get_json()
    assert body["success"] is True and body["roots"] == [] and body["running"] is False


def test_refresh_reads_the_settings_again(monkeypatch):
    calls = []

    class _Service:
        def refresh(self):
            calls.append(1)

        def status(self):
            return {"enabled": True, "running": True, "watching": 1, "roots": [{"path": "/music"}]}

    monkeypatch.setattr(folder_watch, "_service", _Service())
    body = _client(monkeypatch).post("/api/library/folder-watch/refresh").get_json()
    assert calls == [1] and body["watching"] == 1


def test_settings_defaults_turn_it_on():
    from core.settings import ConfigManager
    defaults = ConfigManager._get_default_config(ConfigManager.__new__(ConfigManager))
    library = defaults["library"]
    assert library["watch_folders"] is True and library["watch_poll_minutes"] == 15
    assert library["watch_root_modes"] == {}
