"""The Guides tab of the automations page is vanilla markup on both sides.

The automations page is React (webui/src/routes/automations), music on
/automations and video on /automations?media=video. Its Guides tab is not:
automation-hub.tsx mounts whatever window._buildAutomationHub returns, and
that builder reads the side-aware hub datasets through the _hub* getters in
stats-automations.js. The dependency is implicit, nothing but a prose comment
records it.

That makes it exactly the kind of thing a cleanup deletes by accident. If the
vanilla file is pruned, these functions must survive or the Guides tab renders
nothing at all, with no error that points at the cause.

This pins the contract itself, not a particular file: each name must be
defined by SOME script the page loads. So the functions may be moved or
extracted freely, they just may not vanish.

(Until Nov 12 2026 the video page was a vanilla adapter,
webui/static/video/video-automations.js, that borrowed the music page's card
and section builders too. It is gone: the video side is the React page.)
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
_STATIC = _ROOT / "webui" / "static"
_INDEX = (_ROOT / "webui" / "index.html").read_text(encoding="utf-8")
_HUB_TSX = (_ROOT / "webui" / "src" / "routes" / "automations" / "-ui" / "automation-hub.tsx").read_text(encoding="utf-8")

# Hardcoded on purpose: deriving it from the current source would shrink
# whenever a function is deleted and let the test pass vacuously on the very
# regression it exists to catch.
_CONTRACT = (
    "_buildAutomationHub",
    "_hubGroups",
    "_hubGuides",
    "_hubRecipes",
    "_hubReference",
    "_hubTips",
)

_DEF = re.compile(r"^(?:async\s+)?function\s+([A-Za-z_$][\w$]*)\s*\(", re.M)


def _definitions() -> dict[str, str]:
    """name -> filename, for every top-level function in the static tree."""
    out: dict[str, str] = {}
    for js in sorted(_STATIC.glob("*.js")) + sorted((_STATIC / "video").glob("*.js")):
        for name in _DEF.findall(js.read_text(encoding="utf-8", errors="replace")):
            out.setdefault(name, js.name)
    return out


@pytest.mark.parametrize("name", _CONTRACT)
def test_hub_builder_still_exists(name):
    defs = _definitions()
    assert name in defs, (
        "the automations page's Guides tab is built by %s(), but nothing in "
        "webui/static defines it any more. Deleting it blanks that tab on both "
        "sides silently. Keep it (moving it to another loaded script is fine), "
        "or port the hub to React first." % name
    )
    m = re.search(r"filename='(?:video/)?%s'" % re.escape(defs[name]), _INDEX)
    assert m, "%s defines %s but index.html does not load it" % (defs[name], name)


def test_the_react_page_mounts_the_vanilla_hub():
    assert "window._buildAutomationHub" in _HUB_TSX


def test_the_vanilla_video_adapter_is_gone():
    assert not (_STATIC / "video" / "video-automations.js").exists()
    assert "video/video-automations.js" not in _INDEX
