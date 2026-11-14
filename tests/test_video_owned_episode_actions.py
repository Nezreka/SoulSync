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
_LIST = (_ROOT / "webui" / "src" / "features" / "video-library" / "episode-list.tsx").read_text(
    encoding="utf-8")   # a show's season and its rows are React now


def test_owned_no_longer_forces_the_badge_only_branch():
    # the old gate rendered a bare badge for owned rows and skipped the actions
    assert "(ep.owned || !window.VideoGrab" not in _JS


def test_owned_rows_render_badge_and_actions_together():
    # Oct 2026: owned rows dropped the "Owned" pill (only a gap gets a badge)
    # but still emit the badge cell, and their actions sit in the row's ⋯ menu
    assert "vd-ep-badge vd-ep-badge--none" in _LIST
    assert "if (ep.owned) {" in _LIST and "Download again (upgrade)" in _LIST
    # …and the action cluster is gated on the right to download, not ownership
    menu = _LIST.split("if (canDownload) {")[1].split("if (canManage && lib) {")[0]
    for label in ("Manual search", "Wishlist for an upgrade", "Add to wishlist"):
        assert label in menu
    # manual search and the wishlist sit outside the owned check
    assert "key: 'search'" in menu.split("if (ep.owned) {")[1].split("}\n", 1)[1]


def test_owned_actions_carry_upgrade_wording():
    assert "Download again (upgrade)" in _LIST
    assert "ep.owned ? 'Wishlist for an upgrade' : 'Add to wishlist'" in _LIST


def test_click_handlers_are_ownership_agnostic():
    # the delegated handlers dispatch straight to the inline actions with no
    # owned check — an owned row's buttons must actually work
    # (the list calls the page's bridge, which runs the same inline actions)
    for name in ("grabEpisode", "wishEpisode"):
        fn = re.search(name + r": function \(en\) \{[^}]*\}", _JS)
        assert fn is not None, name
        assert "owned" not in fn.group(0)
    assert "b.grabEpisode?.(ep.episode_number)" in _LIST
