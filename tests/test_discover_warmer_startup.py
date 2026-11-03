"""The discover warmer must start from start_runtime_services(), not at import.

It used to be started by a module-level statement in the middle of web_server.py.
After 15 s it sends real requests through ``app.test_client()``. If the import
was still running by then (a slow boot: a big library upgrade, a slow disk),
that request was served before the blueprints further down the module were
registered, and Flask refused every later ``register_blueprint()`` -- "The setup
method 'register_blueprint' can no longer be called on the application. It has
already handled its first request". The app never finished starting.

``start_runtime_services()`` runs once the import is complete (wsgi.py and the
``__main__`` launch both call it), so starting the warmer there removes the race.

Importing web_server in a test is deliberately off-limits (it boots half the
app), so this pins the shape statically with the AST, as
test_batch_healing_startup.py does for the healing timer.
"""

from __future__ import annotations

import ast
from pathlib import Path

WEB_SERVER = Path(__file__).resolve().parent.parent / "web_server.py"


def _tree():
    return ast.parse(WEB_SERVER.read_text())


def _calls(node, name):
    return [
        n for n in ast.walk(node)
        if isinstance(n, ast.Call)
        and isinstance(n.func, ast.Name)
        and n.func.id == name
    ]


def _module_level_calls(tree, name):
    """calls to ``name`` outside any function or class body"""
    found = []
    for stmt in tree.body:
        if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            continue
        found.extend(_calls(stmt, name))
    return found


def test_no_module_level_discover_warmer_start():
    """REGRESSION: importing web_server must not start the warmer."""
    calls = _module_level_calls(_tree(), "_start_discover_warmer")
    assert not calls, (
        "_start_discover_warmer() is still called at web_server module level "
        f"(line(s) {[c.lineno for c in calls]}) -- its first request can land "
        "before the remaining register_blueprint() calls and abort startup"
    )


def test_discover_warmer_starts_in_start_runtime_services():
    """The warmer still starts once per real server launch."""
    start_fns = [
        n for n in ast.walk(_tree())
        if isinstance(n, ast.FunctionDef) and n.name == "start_runtime_services"
    ]
    assert start_fns, "start_runtime_services() not found in web_server.py"
    assert _calls(start_fns[0], "_start_discover_warmer"), (
        "_start_discover_warmer() is not called from start_runtime_services() "
        "-- production would lose the discover warmer"
    )


def test_warmer_opt_out_env_is_kept():
    """SOULSYNC_NO_DISCOVER_WARMUP still turns it off."""
    start_fns = [
        n for n in ast.walk(_tree())
        if isinstance(n, ast.FunctionDef) and n.name == "start_runtime_services"
    ]
    src = ast.get_source_segment(WEB_SERVER.read_text(), start_fns[0])
    assert "SOULSYNC_NO_DISCOVER_WARMUP" in src
