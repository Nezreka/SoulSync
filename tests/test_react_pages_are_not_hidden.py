"""A React page must not wear the shell's `.page` class.

`.page { display: none }` in style.css; only `.page.active` is visible, and the
shell adds `.active` for LEGACY pages it owns. A React route renders inside
#webui-react-root, which the shell shows as a whole — so a React page component
that copies the vanilla markup's `class="page"` is invisible. Every pixel of it.

This is not hypothetical. The label-detail port shipped `className="page
label-detail-page"`, lifted straight from the markup it replaced, and the page
rendered nothing at all. Its 86 unit tests passed, because jsdom applies no CSS
and the elements were all present and correct in the DOM — the same blind spot
that hid the artist-detail CSS scoping and the trapped DB-record modal.

So it is checked here, where CSS is just text.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
_ROUTES = _ROOT / "webui" / "src" / "routes"
_CSS = (_ROOT / "webui/static/style.css").read_text(encoding="utf-8", errors="replace")

# Every top-level page component a React route renders.
_PAGE_COMPONENTS = sorted(
    p
    for p in _ROUTES.rglob("-ui/*-page.tsx")
    if ".test." not in p.name
)


def test_the_shell_page_class_is_still_hidden_by_default():
    """If this stops being true the rule below is guarding nothing."""
    block = re.search(r"^\.page \{(.*?)\}", _CSS, re.S | re.M)
    assert block, ".page rule not found in style.css"
    assert "display: none" in block.group(1), (
        "`.page` is no longer display:none — the reason React pages must avoid "
        "the class has changed, so re-derive this test rather than deleting it"
    )


def test_react_page_components_exist_to_check():
    assert _PAGE_COMPONENTS, "found no React page components; the glob is wrong"


def _without_comments(source: str) -> str:
    """A className named in prose is not a rendered class.

    The first version of this test flagged the very comment that explains the
    rule — `// NOT className="page": ...` — which is the third time a checker of
    mine has matched documentation instead of code.
    """
    source = re.sub(r"/\*.*?\*/", " ", source, flags=re.S)
    return re.sub(r"//[^\n]*", " ", source)


@pytest.mark.parametrize("component", _PAGE_COMPONENTS, ids=lambda p: p.name)
def test_no_react_page_renders_the_shell_page_class(component: Path):
    source = _without_comments(component.read_text(encoding="utf-8"))
    # `className="page ..."` or `className="... page"` on any element — the
    # word on its own, not `page-shell` or `label-detail-page`.
    offenders = [
        match.group(0)
        for match in re.finditer(r'className="[^"]*"', source)
        if "page" in re.split(r"[^a-z-]+", match.group(0))
    ]
    assert not offenders, (
        f"{component.name} renders the shell's `.page` class ({offenders}), which is "
        f"display:none until the shell adds .active — and it never does for a React "
        f"route. The page will be completely invisible with every unit test passing."
    )
