"""Batch-healing timer must not start at web_server import time (S14).

``start_batch_healing_timer()`` was a module-level statement in web_server.py:
importing web_server — tests, maintenance scripts, anything — spawned a
daemon thread running ``validate_and_heal_batch_states()`` every 30s forever,
mutating download batch state. The timer belongs to server startup
(``start_runtime_services()``, called by wsgi.py and the ``__main__`` direct
launch), not to import.

Importing web_server in a test is deliberately off-limits (it boots half the
app), so this pins the shape statically with the AST: no module-level call,
and the call lives inside start_runtime_services.
"""

from __future__ import annotations

import ast
from pathlib import Path

WEB_SERVER = Path(__file__).resolve().parent.parent / "web_server.py"


def _tree():
    return ast.parse(WEB_SERVER.read_text())


def test_no_module_level_batch_healing_start():
    """REGRESSION (S14): importing web_server must not start the healing loop."""
    tree = _tree()
    top_level_calls = [
        node for node in tree.body
        if isinstance(node, ast.Expr)
        and isinstance(node.value, ast.Call)
        and isinstance(node.value.func, ast.Name)
        and node.value.func.id == "start_batch_healing_timer"
    ]
    assert not top_level_calls, (
        "start_batch_healing_timer() is still called at web_server module level "
        f"(line(s) {[n.lineno for n in top_level_calls]}) — any importer spawns "
        "the 30s mutating loop"
    )


def test_batch_healing_starts_in_start_runtime_services():
    """The timer still starts exactly once per real server launch."""
    tree = _tree()
    start_fns = [
        node for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef)
        and node.name == "start_runtime_services"
    ]
    assert start_fns, "start_runtime_services() not found in web_server.py"
    calls = [
        node for node in ast.walk(start_fns[0])
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "start_batch_healing_timer"
    ]
    assert calls, (
        "start_batch_healing_timer() is not called from start_runtime_services() "
        "— production would lose the healing loop"
    )
