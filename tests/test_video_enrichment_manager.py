"""Wiring guards for the video "Manage Workers" modal (video-enrichment-manager.js).

Pins two behaviours the user asked for:
  - clicking a coverage card just SWITCHES THE VIEW — it must not change the global
    "process first" priority or silently re-queue failed items (those are the top
    Movies/Shows/Auto tabs and the explicit "Retry all failed" button);
  - "Retry all failed" re-queues EVERY coverage kind the worker handles, not just
    the tab being viewed.
"""

from __future__ import annotations

import re
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
_JS = (_ROOT / "webui" / "static" / "video" / "video-enrichment-manager.js").read_text(encoding="utf-8")


def _func(name: str) -> str:
    """Return the body of `function name(` up to the next top-level `function `."""
    i = _JS.index("function " + name + "(")
    nxt = _JS.find("\n    function ", i + 1)
    return _JS[i:nxt if nxt != -1 else len(_JS)]


def test_switch_kind_only_switches_view():
    body = _func("switchKind")
    assert "state.kind = kind" in body
    # must NOT reach across to the global priority or auto-retry
    assert "setPriority(" not in body
    assert "requeueFailed(" not in body


def test_priority_changes_only_from_top_tabs():
    # setPriority is still wired — but only to the data-em-priority (top tabs) click.
    assert "function setPriority(" in _JS
    assert "data-em-priority')) setPriority(" in _JS
    # the dead per-coverage requeue helper is gone
    assert "function requeueFailed(" not in _JS


def test_retry_all_failed_covers_every_kind_of_the_worker():
    body = _func("retryAllFailed")
    # iterate the worker's kinds (movie+show / show / video …), retry each
    assert "workerDef(state.selected)" in body
    assert "w.kinds" in body
    assert re.search(r"kinds\.map\(", body)
    # the button is wired to the multi-kind handler, not the single-kind retry
    assert "data-em-retry-all')) retryAllFailed()" in _JS
