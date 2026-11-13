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
    "prioritize_download_batch_next",
}
# stopping a download is the admin's, or the profile's that started it
OWN_CANCEL = {"cancel_download_task", "cancel_task_v2", "cancel_batch"}


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


def test_a_profile_may_cancel_what_it_started_and_nothing_else():
    from core.permissions import may_cancel_download

    assert may_cancel_download(3, 3, False)
    assert may_cancel_download("3", 3, False)
    assert not may_cancel_download(4, 3, False)        # somebody else's
    assert not may_cancel_download(1, 3, False)        # the admin's
    assert not may_cancel_download(None, 3, False)     # nobody's on record
    assert not may_cancel_download(3, None, False)     # no profile picked
    assert not may_cancel_download("x", 3, False)
    for owner in (1, 3, 4, None):
        assert may_cancel_download(owner, 2, True)     # any admin, any download


def test_the_three_cancel_routes_ask_whose_download_it_is():
    source = (ROOT / "web_server.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    seen = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name in OWN_CANCEL:
            decorators = [ast.unparse(d) for d in node.decorator_list]
            if any(d.startswith("app.route(") for d in decorators):
                seen[node.name] = (decorators, ast.get_source_segment(source, node))
    assert set(seen) == OWN_CANCEL
    for name, (decorators, body) in seen.items():
        assert "admin_only" not in decorators, name
        check = body.index("_cancel_refused(_download_owner_profile(")
        # asked before anything is cancelled
        for act in ("_atomic_cancel_task(", "cancel_audiobooks(", "download_tasks[task_id]", "download_batches[batch_id]"):
            if act in body:
                assert check < body.index(act), (name, act)


def test_the_owner_of_a_download_is_its_task_then_its_batch():
    """the helper the routes use, run on its own with the app's two tables"""
    import threading
    source = (ROOT / "web_server.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    fn = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "_download_owner_profile")
    scope = {"tasks_lock": threading.Lock(),
             "download_tasks": {"t1": {"profile_id": 3, "batch_id": "b1"},
                                "t2": {"batch_id": "b1"}, "t3": {}},
             "download_batches": {"b1": {"profile_id": 5}, "b2": {}}}
    exec(compile(ast.Module(body=[fn], type_ignores=[]), "web_server.py", "exec"), scope)  # noqa: S102
    owner = scope["_download_owner_profile"]
    assert owner(task_id="t1") == 3
    assert owner(task_id="t2") == 5
    assert owner(task_id="t3") is None
    assert owner(task_id="missing") is None
    assert owner(batch_id="b1") == 5
    assert owner(batch_id="b2") is None
    assert owner() is None


def _route_decorators(relative, names):
    source = (ROOT / relative).read_text(encoding="utf-8")
    found = {}
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.FunctionDef) and node.name in names:
            found[node.name] = [ast.unparse(d) for d in node.decorator_list]
    return found


def test_a_database_backup_is_listed_and_downloaded_by_an_admin_only():
    """nov 13 2026 review: making, deleting and restoring a backup were admin
    only, listing and downloading one were not. a backup is the whole database:
    every profile's password hash, recovery answer and data."""
    names = {"list_backups_endpoint", "download_backup_endpoint", "backup_database_endpoint",
             "delete_backup_endpoint", "restore_backup_endpoint", "database_vacuum",
             "enable_incremental_vacuum"}
    found = _route_decorators("api/database_admin.py", names)
    assert set(found) == names
    for name, decorators in found.items():
        assert "admin_only" in decorators, name
        assert decorators[0].startswith("bp.route("), name   # the check runs inside the route


def test_the_memory_diagnostics_are_admin_only():
    """they start tracemalloc or walk every live object: slow, and for one person"""
    names = {"debug_memory_start", "debug_memory_report", "debug_memory_stop", "debug_memory_objects"}
    found = _route_decorators("web_server.py", names)
    assert set(found) == names
    for name, decorators in found.items():
        assert "admin_only" in decorators, name


def test_auto_import_and_the_watchlist_global_rules_are_changed_by_an_admin_only():
    """boulder, nov 13 2026: "non admins shouldn't be able to change auto
    import or global settings". reading them stays open, the pages show them."""
    for relative, names in (("api/auto_import.py", {"auto_import_toggle", "auto_import_settings"}),
                            ("api/artist_watchlist.py", {"watchlist_global_config"})):
        source = (ROOT / relative).read_text(encoding="utf-8")
        for node in ast.walk(ast.parse(source)):
            if isinstance(node, ast.FunctionDef) and node.name in names:
                body = ast.get_source_segment(source, node)
                gate = body.index("if not is_admin_request():")
                assert '"Admin access required"}), 403' in body[gate:gate + 160], node.name
                # nothing is written before the caller is checked
                first_write = min((body.index(w) for w in ("config_manager.set(", ".stop()", "defer_or_start(")
                                   if w in body), default=len(body))
                assert gate < first_write, node.name
                names = names - {node.name}
        assert not names, names


def test_a_non_admin_reads_the_auto_import_settings_and_cannot_write_them(monkeypatch):
    import api.auto_import as auto_import
    import core.profile_context as profile_context

    written = []
    values = {}
    monkeypatch.setattr(auto_import, "config_manager", type("C", (), {
        "get": staticmethod(lambda key, default=None: values.get(key, default)),
        "set": staticmethod(lambda key, value: written.append(key))})())
    app = Flask(__name__)
    app.register_blueprint(auto_import.bp)
    client = app.test_client()
    monkeypatch.setattr(profile_context, "is_admin_request", lambda: False)
    assert client.get("/api/auto-import/settings").status_code == 200
    assert client.post("/api/auto-import/settings", json={"enabled": True}).status_code == 403
    assert client.post("/api/auto-import/toggle", json={"enabled": False}).status_code == 403
    assert written == []
    monkeypatch.setattr(profile_context, "is_admin_request", lambda: True)
    assert client.post("/api/auto-import/settings", json={"scan_interval": 30}).status_code == 200
    assert written == ["auto_import.scan_interval"]
