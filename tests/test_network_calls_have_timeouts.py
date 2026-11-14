"""No direct `requests` call goes out without a timeout.

Nov 13 2026 review: four Plex artwork uploads had none. `requests` waits
forever by default, so a server that accepts the connection and then stalls
holds that worker thread for the life of the process."""

from __future__ import annotations

import ast
import pathlib

ROOT = pathlib.Path(__file__).resolve().parent.parent
VERBS = {"get", "post", "put", "delete", "head", "patch", "request"}


def test_every_direct_requests_call_passes_a_timeout():
    missing = []
    files = [ROOT / "web_server.py"]
    for folder in ("api", "core", "database", "utils", "services"):
        files += sorted((ROOT / folder).rglob("*.py"))
    for path in files:
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                    and node.func.attr in VERBS
                    and isinstance(node.func.value, ast.Name) and node.func.value.id == "requests"):
                keywords = {k.arg for k in node.keywords}
                if "timeout" not in keywords and None not in keywords:   # None: **kwargs
                    missing.append(f"{path.relative_to(ROOT)}:{node.lineno}")
    assert missing == []
