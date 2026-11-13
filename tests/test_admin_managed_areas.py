"""Four areas are the admin's to change, whatever profile is signed in.

Boulder, Nov 13 2026: "non admins should not be able to purge deleted files,
or manage downloads page, or edit quality profiles, or act on any findings."
None of these routes asked who was calling."""

from __future__ import annotations

import ast
import pathlib

import pytest
from flask import Flask

ROOT = pathlib.Path(__file__).resolve().parent.parent
MUTATING = {"POST", "PUT", "PATCH", "DELETE"}


def _blueprints():
    import api.clients as clients
    import api.deleted_files as deleted_files
    import api.quality_profiles as quality_profiles
    import api.quarantine as quarantine
    import api.repair as repair

    return {
        "repair": lambda: repair.bp,
        "quality_profiles": lambda: quality_profiles.bp,
        "quarantine": lambda: quarantine.bp,
        "deleted_files": deleted_files.create_blueprint,
        "clients": clients.create_blueprint,
    }


def _app(name):
    app = Flask(__name__)
    app.register_blueprint(_blueprints()[name]())
    return app


def _calls(app):
    for rule in app.url_map.iter_rules():
        if rule.endpoint == "static":
            continue
        url = rule.rule
        for arg in rule.arguments:
            url = url.replace(f"<int:{arg}>", "1").replace(f"<{arg}>", "x")
        for method in sorted(rule.methods - {"HEAD", "OPTIONS"}):
            yield method, url


@pytest.mark.parametrize("name", ["repair", "quality_profiles", "quarantine", "deleted_files", "clients"])
def test_a_non_admin_cannot_change_anything_here(name, monkeypatch):
    import core.profile_context as profile_context
    monkeypatch.setattr(profile_context, "is_admin_request", lambda: False)
    app = _app(name)
    client = app.test_client()
    changed = 0
    for method, url in _calls(app):
        if method not in MUTATING or url.endswith(("/play", "/compare-stream")):
            continue
        response = client.open(url, method=method, json={})
        assert response.status_code == 403, f"{method} {url} answered {response.status_code}"
        assert response.get_json()["error"] == "Admin access required"
        changed += 1
    assert changed >= 3, f"{name}: the routes this test is about were not found"


@pytest.mark.parametrize("name", ["repair", "quality_profiles", "quarantine", "deleted_files", "clients"])
def test_the_gate_is_not_reached_by_an_admin_or_by_a_read(name, monkeypatch):
    import core.profile_context as profile_context
    app = _app(name)
    gate = app.before_request_funcs[next(iter(app.blueprints))][0]
    monkeypatch.setattr(profile_context, "is_admin_request", lambda: True)
    with app.test_request_context("/x", method="POST"):
        assert gate() is None
    monkeypatch.setattr(profile_context, "is_admin_request", lambda: False)
    with app.test_request_context("/x", method="GET"):
        assert gate() is None
    with app.test_request_context("/x", method="DELETE"):
        assert gate()[1] == 403


def test_listening_to_a_quarantined_file_is_not_managing_it(monkeypatch):
    import core.profile_context as profile_context
    monkeypatch.setattr(profile_context, "is_admin_request", lambda: False)
    app = _app("quarantine")
    gate = app.before_request_funcs["quarantine"][0]
    for path in ("/api/quarantine/abc/play", "/api/quarantine/abc/compare-stream"):
        with app.test_request_context(path, method="POST"):
            assert gate() is None
    with app.test_request_context("/api/quarantine/abc/approve", method="POST"):
        assert gate()[1] == 403


DOWNLOADS_PAGE = {
    "cancel_download", "cancel_all_downloads", "clear_finished_downloads",
    "download_selected_candidate", "manual_search_for_task", "clear_all_searches",
    "delete_download_origins", "inspect_task_sources", "clear_completed_downloads",
    "remove_download_from_list", "prioritize_download_task_next",
    "prioritize_download_batch_next", "cancel_download_task", "cancel_task_v2", "cancel_batch",
}


def test_managing_the_downloads_page_is_admin_only():
    tree = ast.parse((ROOT / "web_server.py").read_text(encoding="utf-8"))
    seen = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name in DOWNLOADS_PAGE:
            decorators = [ast.unparse(d) for d in node.decorator_list]
            if any(d.startswith("app.route(") for d in decorators):
                seen[node.name] = decorators
    assert set(seen) == DOWNLOADS_PAGE
    for name, decorators in seen.items():
        assert "admin_only" in decorators, name
        # the route decorator is outermost, so the check runs inside the route
        assert decorators.index("admin_only") > max(
            i for i, d in enumerate(decorators) if d.startswith("app.route(")), name
