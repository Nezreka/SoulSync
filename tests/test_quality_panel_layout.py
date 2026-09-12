"""The Quality tab's profile panel has to fit inside the settings column.

It did not, for a while. The panel was written to hang OUTSIDE the column:
``.qp-layout`` was one panel-width wider than its container, so the panel spilled
into the empty margin to the right. That was a reasonable trade when the settings
column was 920px, because giving 320px of it to a panel would have left the tiles
too narrow.

Then the settings overhaul widened the column to --settings-max-width. The panel
rule still added its width on top of whatever the column was, so instead of
landing in empty margin it landed past the settings card's border, floating
outside the page with nothing behind it. A user reported it.

The number that broke it lived in a comment ("the normal centered 920px settings
column") rather than anywhere a change to the column width would have touched.
That is what this test is really for: it ties the two together so widening the
column again cannot silently push the panel back out.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

_CSS = Path(__file__).resolve().parents[1] / "webui" / "static" / "style.css"


def _css():
    return _CSS.read_text(encoding="utf-8")


def _rule_body(selector: str, source: str) -> str:
    """The declarations of the first rule for this exact selector."""
    marker = selector + " {"
    assert marker in source, f"no rule for {selector}"
    start = source.index(marker)
    return source[start:source.index("}", start)]


def _strip_comments(text: str) -> str:
    """A guard must never pass on the text of a comment explaining it."""
    return re.sub(r"/\*.*?\*/", "", text, flags=re.S)


def test_the_layout_is_not_wider_than_its_container():
    body = _strip_comments(_rule_body(".qp-layout", _css()))
    # anchored to line start: "\bwidth:" also matches --qp-side-width
    width = re.search(r"^\s*width:\s*([^;]+);", body, re.M)
    assert width, "no width declaration on .qp-layout"
    value = width.group(1).strip()
    assert value == "100%", (
        f".qp-layout width is {value!r}. Anything that adds the panel width on "
        f"top of 100% makes the panel hang outside the settings card again."
    )


def test_the_two_columns_add_up_to_the_whole_width():
    css = _strip_comments(_css())
    layout = _rule_body(".qp-layout", css)
    main = _rule_body(".qp-main", css)

    panel = int(re.search(r"--qp-side-width:\s*(\d+)px", layout).group(1))
    gap = int(re.search(r"--qp-side-gap:\s*(\d+)px", layout).group(1))

    # the tiles claim everything the panel and the gap do not
    basis = re.search(r"^\s*flex:\s*([^;]+);", main, re.M).group(1)
    assert "100%" in basis
    assert "--qp-side-width" in basis and "--qp-side-gap" in basis, (
        "the tile column must be derived from the panel width and gap, so the "
        "two can never be set to values that overflow"
    )

    # and at the real column width the split leaves the tiles usable
    column = int(re.search(r"--settings-max-width:\s*(\d+)px", css).group(1))
    tiles = column - panel - gap
    assert tiles + gap + panel == column
    assert tiles >= 920, (
        f"tiles get {tiles}px. The panel used to hang outside precisely so the "
        f"tiles could keep the full 920px column, so dropping below that would "
        f"be a regression on the reason it was built that way."
    )


def test_it_stacks_before_the_two_columns_get_tight():
    """The stacking breakpoint has to be about the column, not the old margin.

    It was 1800px, which was the width the viewport needed for a 920px column
    PLUS a panel hanging off it. Nothing hangs off it now, so a 1440p screen
    should get the two columns rather than stacking for a reason that is gone.
    """
    css = _css()
    stacking = [int(m) for m in re.findall(r"@media \(max-width:\s*(\d+)px\)[^{]*\{\s*\.qp-layout", css)]
    assert stacking, "no stacking breakpoint for .qp-layout"
    assert max(stacking) <= 1200, (
        f"stacks at {max(stacking)}px. That is wide enough to stack screens that "
        f"have room for both columns."
    )


def test_the_stale_920_comment_is_gone():
    """The comment asserting a 920px column outlived the column being 920px.

    It is the thing that made the bug hard to see, so it should not come back
    while the panel lives inside the column.
    """
    layout_area = _css()
    idx = layout_area.index(".qp-layout {")
    preamble = layout_area[max(0, idx - 1200):idx]
    assert "centered 920px" not in preamble, (
        "the comment above .qp-layout still claims the column is 920px"
    )
