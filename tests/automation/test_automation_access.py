"""An automation is its owner's: another profile can't read, change or run it.

Nov 13 2026 review: the automation routes took an id and acted on it, whoever
asked. Any profile could open another profile's automation (its notification
settings hold webhook urls and bot tokens), edit, toggle, delete or run it,
turn the install's system automations off, and save one that runs a script
on the server. The run_script path check also let "../scripts_old/x.sh"
through, because "/app/scripts_old" starts with "/app/scripts"."""

from __future__ import annotations

import json
import os

import pytest
from flask import Flask

import api.automations as routes
from core.automation import access

MINE = {"id": 10, "profile_id": 2, "is_system": 0, "name": "mine"}
THEIRS = {"id": 11, "profile_id": 3, "is_system": 0, "name": "theirs",
          "notify_config": json.dumps({"webhook_url": "https://discord.example/secret"})}
SYSTEM = {"id": 12, "profile_id": 1, "is_system": 1, "name": "system"}


@pytest.mark.parametrize("automation, read, run, change", [
    (MINE, True, True, True),
    (THEIRS, False, False, False),
    (SYSTEM, True, True, False),
    (None, True, True, True),          # not there: the handler answers 404
])
def test_what_a_non_admin_profile_may_do(automation, read, run, change):
    assert access.may_read(automation, 2, False) is read
    assert access.may_run(automation, 2, False) is run
    assert access.may_change(automation, 2, False) is change


@pytest.mark.parametrize("automation", [MINE, THEIRS, SYSTEM])
def test_an_admin_may_do_anything(automation):
    assert access.may_read(automation, 5, True)
    assert access.may_run(automation, 5, True)
    assert access.may_change(automation, 5, True)


def test_no_profile_owns_nothing():
    assert not access.may_change(MINE, None, False)
    assert not access.may_read(THEIRS, None, False)
    assert not access.may_change({"id": 1, "profile_id": None}, 2, False)


@pytest.mark.parametrize("body, uses", [
    ({"action_type": "run_script"}, True),
    ({"action_type": "process_wishlist", "then_actions": [{"type": "Run_Script", "config": {}}]}, True),
    ({"action_type": "process_wishlist", "notify_type": "run_script"}, True),
    ({"action_type": "process_wishlist", "then_actions": [{"type": "discord_webhook"}]}, False),
    ({"name": "renamed"}, False),
    ({"then_actions": "not a list"}, False),
    (None, False),
])
def test_spotting_the_run_script_action(body, uses):
    assert access.uses_run_script(body) is uses


class _Database:
    def __init__(self):
        self.rows = {a["id"]: dict(a, then_actions="[]", action_type="process_wishlist")
                     for a in (MINE, THEIRS, SYSTEM)}
        self.rows[13] = dict(MINE, id=13, action_type="run_script", then_actions="[]")

    def get_automation(self, automation_id):
        return self.rows.get(int(automation_id))


class _Api:
    """Stands in for core.automation.api: records what got through."""
    def __init__(self):
        self.calls = []

    def __getattr__(self, name):
        def call(*args, **kwargs):
            self.calls.append(name)
            return ({"id": 1, "name": "x"} if name == "get_automation" else ({"success": True}, 200))
        return call


@pytest.fixture
def client(monkeypatch):
    caller = {"pid": 2, "admin": False}
    api = _Api()
    monkeypatch.setattr(routes, "_auto_api", api)
    monkeypatch.setattr(routes, "get_database", lambda: _Database())
    monkeypatch.setattr(routes, "automation_engine", None)
    monkeypatch.setattr(routes, "is_admin_request", lambda: caller["admin"])
    monkeypatch.setattr(routes, "get_current_profile_id", lambda: caller["pid"])
    app = Flask(__name__)
    app.secret_key = "test"
    app.register_blueprint(routes.bp)
    return app.test_client(), api, caller


CHANGES = [
    ("put", "/api/automations/{id}", {"name": "x"}),
    ("delete", "/api/automations/{id}", None),
    ("post", "/api/automations/{id}/toggle", None),
    ("put", "/api/automations/group", {"automation_ids": ["{id}"], "group_name": "g"}),
    ("post", "/api/automations/bulk-toggle", {"automation_ids": ["{id}"], "enabled": False}),
]
READS = [
    ("get", "/api/automations/{id}", None),
    ("get", "/api/automations/{id}/history", None),
    ("post", "/api/automations/{id}/duplicate", None),
    ("post", "/api/automations/{id}/run", None),
]


def _send(http, method, url, body, automation_id):
    url = url.replace("{id}", str(automation_id))
    if body is not None:
        body = json.loads(json.dumps(body).replace('"{id}"', str(automation_id)))
    return getattr(http, method)(url, json=body)


@pytest.mark.parametrize("method, url, body", CHANGES + READS)
def test_another_profiles_automation_is_refused_on_every_route(client, method, url, body):
    http, api, _caller = client
    response = _send(http, method, url, body, THEIRS["id"])
    assert response.status_code == 403, response.get_data(as_text=True)
    assert api.calls == []
    assert "secret" not in response.get_data(as_text=True)


@pytest.mark.parametrize("method, url, body", CHANGES + READS)
def test_your_own_automation_goes_through(client, method, url, body):
    http, api, _caller = client
    assert _send(http, method, url, body, MINE["id"]).status_code == 200
    assert len(api.calls) == 1


@pytest.mark.parametrize("method, url, body", CHANGES)
def test_a_system_automation_is_changed_by_an_admin_only(client, method, url, body):
    http, api, caller = client
    assert _send(http, method, url, body, SYSTEM["id"]).status_code == 403
    assert api.calls == []
    caller["admin"] = True
    assert _send(http, method, url, body, SYSTEM["id"]).status_code == 200


@pytest.mark.parametrize("method, url, body", READS)
def test_a_system_automation_is_read_and_run_by_anyone(client, method, url, body):
    http, _api, _caller = client
    assert _send(http, method, url, body, SYSTEM["id"]).status_code == 200


@pytest.mark.parametrize("method, url, body", CHANGES + READS)
def test_an_admin_reaches_another_profiles_automation(client, method, url, body):
    http, _api, caller = client
    caller.update(pid=5, admin=True)
    assert _send(http, method, url, body, THEIRS["id"]).status_code == 200


def test_only_an_admin_saves_an_automation_that_runs_a_script(client):
    http, api, caller = client
    body = {"name": "x", "action_type": "run_script", "action_config": {"script_name": "a.sh"}}
    assert http.post("/api/automations", json=body).status_code == 403
    assert http.put(f"/api/automations/{MINE['id']}", json=body).status_code == 403
    assert http.post("/api/automations/13/duplicate").status_code == 403
    assert api.calls == []
    assert http.post("/api/automations", json={"name": "x", "action_type": "process_wishlist"}).status_code == 200
    caller["admin"] = True
    assert http.post("/api/automations", json=body).status_code == 200


def test_a_script_outside_the_scripts_folder_is_refused(tmp_path):
    from types import SimpleNamespace

    from core.automation.handlers.run_script import auto_run_script

    scripts = tmp_path / "scripts"
    sibling = tmp_path / "scripts_old"
    scripts.mkdir()
    sibling.mkdir()
    (sibling / "x.py").write_text("print('ran')")
    (tmp_path / "y.py").write_text("print('ran')")
    deps = SimpleNamespace(
        docker_resolve_path=lambda p: p,
        config_manager=SimpleNamespace(get=lambda key, default=None: str(scripts)),
        update_progress=lambda *a, **k: None)
    for name in (os.path.join("..", "scripts_old", "x.py"), os.path.join("..", "y.py"), "..", "."):
        result = auto_run_script({"script_name": name}, deps)
        assert result == {"status": "error", "error": "Script path traversal blocked"}, name
    missing = auto_run_script({"script_name": "not_there.py"}, deps)
    assert missing["error"].startswith("Script not found")
