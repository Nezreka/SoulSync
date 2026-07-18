"""Owned TV episodes keep their acquisition actions.

The episode row used to render EITHER the Owned badge OR the three action
buttons (auto-grab / manual search / wishlist) — ownership made the actions
vanish, even though the acquisition stack treats owned rows as first-class
upgrade candidates (upgrade-until-cutoff). Now the badge and the actions
coexist; the no-VideoGrab (member) rendering is unchanged.
"""

from __future__ import annotations

import re
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
_JS = (_ROOT / "webui" / "static" / "video" / "video-detail.js").read_text(
    encoding="utf-8", errors="replace")


def test_owned_no_longer_forces_the_badge_only_branch():
    # the old gate rendered a bare badge for owned rows and skipped the actions
    assert "(ep.owned || !window.VideoGrab" not in _JS


def test_owned_rows_render_badge_and_actions_together():
    # badge still exists for owned…
    assert "'<div class=\"vd-ep-badge\">Owned'" in _JS
    # …and the action cluster is gated only on VideoGrab, not ownership
    assert "(!window.VideoGrab" in _JS
    for attr in ("data-vd-ep-grab", "data-vd-ep-search", "data-vd-ep-wish"):
        assert attr in _JS


def test_owned_actions_carry_upgrade_wording():
    assert "Search &amp; download again (upgrade)" in _JS
    assert "Wishlist for an upgrade" in _JS


def test_click_handlers_are_ownership_agnostic():
    # the delegated handlers dispatch straight to the inline actions with no
    # owned check — an owned row's buttons must actually work
    grab = re.search(r"closest\('\[data-vd-ep-grab\]'\);\s*\n\s*if \(epGrab", _JS)
    assert grab is not None
    handler_zone = _JS[grab.start():grab.start() + 500]
    assert "owned" not in handler_zone
