"""Regression: POST /api/auto-import/settings with {"enabled": ...} must
start/stop the worker thread, not just flip the flag.

The settings form previously wrote ``auto_import.enabled`` without touching
the worker — only the separate /toggle endpoint managed the thread — so the
flag and the thread could disagree silently (worker "enabled" but never
scanning, or "disabled" but still running)."""
from __future__ import annotations

import pytest
from flask import Flask

import api.auto_import as auto_import_api


class _FakeCfg:
    def __init__(self):
        self.store = {}

    def get(self, key, default=None):
        return self.store.get(key, default)

    def set(self, key, value):
        self.store[key] = value


class _FakeWorker:
    def __init__(self):
        self.running = False
        self.starts = 0
        self.stops = 0

    def start(self):
        self.starts += 1
        self.running = True

    def stop(self):
        self.stops += 1
        self.running = False


@pytest.fixture()
def client():
    cfg = _FakeCfg()
    worker = _FakeWorker()
    auto_import_api.configure(
        get_database=lambda: None,
        config_manager=cfg,
        _auto_import_worker=lambda: worker,
    )
    app = Flask(__name__)
    app.register_blueprint(auto_import_api.create_blueprint())
    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c, cfg, worker


def test_settings_enable_starts_worker(client):
    c, cfg, worker = client
    resp = c.post("/api/auto-import/settings", json={"enabled": True})
    assert resp.status_code == 200
    assert cfg.get("auto_import.enabled") is True
    assert worker.running is True
    assert worker.starts == 1


def test_settings_disable_stops_worker(client):
    c, cfg, worker = client
    worker.running = True  # was started via the toggle endpoint earlier
    resp = c.post("/api/auto-import/settings", json={"enabled": False})
    assert resp.status_code == 200
    assert cfg.get("auto_import.enabled") is False
    assert worker.running is False
    assert worker.stops == 1


def test_settings_enable_does_not_restart_running_worker(client):
    c, cfg, worker = client
    worker.running = True
    resp = c.post("/api/auto-import/settings", json={"enabled": True})
    assert resp.status_code == 200
    assert worker.starts == 0  # no redundant start


def test_settings_without_enabled_leaves_worker_alone(client):
    c, cfg, worker = client
    resp = c.post("/api/auto-import/settings", json={"scan_interval": 120})
    assert resp.status_code == 200
    assert cfg.get("auto_import.scan_interval") == 120
    assert worker.starts == 0 and worker.stops == 0
