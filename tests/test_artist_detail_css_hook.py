"""The artist-detail stylesheet hangs off ONE class. Keep it attached.

The React page renders inside #webui-react-root, not the old #artist-detail-page
container, so 17 rules in style.css are scoped to `.artist-detail-page` -- the
wrapper the page puts around itself. Nothing enforces that pairing: rename or
drop the className and every one of those rules stops matching. There is no
error and no failing render test, because jsdom does not apply CSS at all. That
is exactly how a SOURCE artist once ended up offering Artist Radio and Enhance
Quality -- the rules that hide them had stopped applying.

This started as scripts/artist_detail_css_audit.py, a manual script from the
port. Its other half ("every rule naming the legacy container must also name the
React class") retired with the vanilla page: no rule names that container any
more. This half outlives the port, so it belongs in the suite where it runs on
its own.
"""

from __future__ import annotations

import re
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
def _without_comments(css: str) -> str:
    """A selector named only in prose is not a rule.

    Without this the dead-container check matches from inside a `/* ... */`
    explaining the history straight through to the next rule's `{`, and reports
    a rule that does not exist.
    """
    return re.sub(r"/\*.*?\*/", " ", css, flags=re.S)


_CSS = _without_comments(
    (_ROOT / "webui/static/style.css").read_text(encoding="utf-8", errors="replace")
)
_PAGE = (_ROOT / "webui/src/routes/artist-detail/-ui/artist-detail-page.tsx").read_text(
    encoding="utf-8"
)

HOOK = "artist-detail-page"


def test_the_page_still_renders_the_class_the_stylesheet_targets():
    assert f'className="{HOOK}"' in _PAGE, (
        f"the page no longer renders className={HOOK!r}, so every rule scoped to it "
        f"is dead — the hero, the release cards' big-photo treatment and the "
        f"source-artist hides all stop applying, silently"
    )


def test_the_stylesheet_still_has_rules_that_depend_on_it():
    # If this ever drops to zero the test above is guarding nothing, and the
    # pair would pass while meaning nothing at all.
    scoped = len(re.findall(rf"\.{HOOK}\b[^{{}}]*\{{", _CSS))
    assert scoped >= 10, f"only {scoped} rules scoped to .{HOOK}; expected the page's whole block"


def test_no_rule_still_names_the_deleted_container():
    """`#artist-detail-page` was the vanilla page's div. It is gone.

    A rule naming it matches nothing, and — worse — reads as live styling for
    anyone maintaining this file.
    """
    dead = [
        rule.strip()[:80]
        for rule in re.findall(r"[^}\n][^}]*#artist-detail-page[^{]*\{", _CSS)
    ]
    assert not dead, f"rules still scoped to the deleted container: {dead}"
