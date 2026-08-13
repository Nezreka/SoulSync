"""The label-detail stylesheet hangs off ONE class. Keep it attached.

static/label-detail.js used to inject these rules into <head> at runtime,
scoped to the #label-detail-page container. The React page renders inside
#webui-react-root instead, so the rules moved into style.css and now name the
.label-detail-page wrapper the page puts around itself.

Nothing enforces that pairing on its own: rename or drop the className and
every rule stops matching, with no error and no failing render test, because
jsdom does not apply CSS at all. The same guard artist-detail needed after the
same move (tests/test_artist_detail_css_hook.py) — written up front this time
rather than after a source artist shipped with library-only buttons showing.
"""

from __future__ import annotations

import re
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent


def _without_comments(css: str) -> str:
    """A selector named only in prose is not a rule."""
    return re.sub(r"/\*.*?\*/", " ", css, flags=re.S)


_CSS = _without_comments(
    (_ROOT / "webui/static/style.css").read_text(encoding="utf-8", errors="replace")
)
_PAGE = (
    _ROOT / "webui/src/routes/label-detail/-ui/label-detail-page.tsx"
).read_text(encoding="utf-8")

HOOK = "label-detail-page"


def test_the_page_still_renders_the_class_the_stylesheet_targets():
    assert f'className="{HOOK}"' in _PAGE, (
        f"the page no longer renders className={HOOK!r}, so every rule scoped to it is "
        f"dead — the hero, the toolbar and the full-bleed release cards all lose their "
        f"styling silently"
    )


def test_the_stylesheet_carries_the_rules_that_used_to_be_injected():
    # label-detail.js injected ~35 rules; they must have LANDED here, not just
    # stopped being injected.
    scoped = len(re.findall(rf"\.{HOOK}\b[^{{}}]*\{{", _CSS))
    assert scoped >= 30, f"only {scoped} rules scoped to .{HOOK}; the moved block is incomplete"


def test_the_card_container_overrides_survived_the_move():
    """The one part of the move that is easy to lose and hard to notice.

    These rules turn the shared .release-card/.album-card into this page's
    full-bleed square. Without them label releases render as the artist-detail
    300px stacked card, which still LOOKS like a card — just the wrong one.
    """
    assert re.search(rf"\.{HOOK} \.release-card\.album-card\b", _CSS)
    assert re.search(rf"\.{HOOK} \.album-card \.label-card-artist-btn\b", _CSS)


def test_no_rule_still_names_the_deleted_container():
    dead = [
        rule.strip()[:80]
        for rule in re.findall(r"[^}\n][^}]*#label-detail-page[^{]*\{", _CSS)
    ]
    assert not dead, f"rules still scoped to the deleted container: {dead}"
