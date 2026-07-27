"""The video Automations page borrows its renderer from the music page.

webui/static/video/video-automations.js is a ~200-line adapter with no
renderer of its own: it fetches /api/automations, filters to owned_by='video',
and then hands the rows to builders that live in the MUSIC page's
stats-automations.js. The dependency is one-directional (music exports, video
consumes) and entirely implicit — nothing but a prose comment records it.

That makes it exactly the kind of thing a cleanup deletes by accident. The
music Automations page is being migrated to React; when the vanilla file is
finally pruned, these functions must survive or the video page renders
nothing at all, with no error that points at the cause.

This pins the contract itself, not a particular file: each name must be
defined by SOME script the page loads, and its definer must load BEFORE the
video adapter. So the functions may be moved or extracted freely — they just
may not vanish.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
_STATIC = _ROOT / "webui" / "static"
_ADAPTER = _STATIC / "video" / "video-automations.js"
_INDEX = (_ROOT / "webui" / "index.html").read_text(encoding="utf-8")

# The renderer surface the video page consumes. Hardcoded on purpose: deriving
# it from the current source would shrink whenever a function is deleted and
# let the test pass vacuously on the very regression it exists to catch.
_CONTRACT = (
    "renderAutomationCard",
    "renderAutomationsMasterToggle",
    "_buildAutomationSection",
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
def test_borrowed_renderer_still_exists(name):
    defs = _definitions()
    assert name in defs, (
        "video-automations.js calls %s(), but nothing in webui/static defines it "
        "any more. The video Automations page renders via the music page's "
        "builders — deleting one blanks that page silently. Keep it (moving it "
        "to another loaded script is fine), or port the video page off it first."
        % name
    )


@pytest.mark.parametrize("name", _CONTRACT)
def test_adapter_actually_uses_it(name):
    """Guards the other direction: if the video page stops needing one of these,
    this list is stale and should shrink deliberately rather than by drift."""
    src = _ADAPTER.read_text(encoding="utf-8")
    assert re.search(r"\b%s\b" % re.escape(name), src), (
        "%s is listed in this test's contract but video-automations.js no longer "
        "references it — drop it from _CONTRACT so the pin stays honest." % name
    )


def test_definers_load_before_the_video_adapter():
    """Order matters: these are plain globals, so the defining script must have
    executed by the time the adapter runs."""
    defs = _definitions()
    def pos(filename: str) -> int:
        m = re.search(r"filename='(?:video/)?%s'" % re.escape(filename), _INDEX)
        assert m, "%s is not loaded by index.html" % filename
        return m.start()

    adapter_at = pos("video-automations.js")
    for name in _CONTRACT:
        owner = defs[name]
        assert pos(owner) < adapter_at, (
            "%s defines %s but loads AFTER video-automations.js — the adapter "
            "would see it undefined." % (owner, name)
        )
