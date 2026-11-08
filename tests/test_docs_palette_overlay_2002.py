"""#2002: the docs search palette closes by dropping its `open` class, so it
stays in the DOM as a full-screen fixed layer. faded to opacity 0 it was still
on top of the page, eating every click and scroll until a reload. checked in
chromium: elementFromPoint hit the overlay after close before this, the page
after."""

import re
from pathlib import Path

CSS = Path(__file__).resolve().parents[1] / "webui" / "static" / "docs.css"


def _rule(selector: str) -> str:
    text = re.sub(r"/\*.*?\*/", "", CSS.read_text(encoding="utf-8"), flags=re.S)
    match = re.search(re.escape(selector) + r"\s*\{([^}]*)\}", text)
    assert match, selector
    return match.group(1)


def test_a_closed_palette_catches_nothing():
    closed = _rule(".dd-palette-overlay ")
    assert re.search(r"pointer-events:\s*none", closed)
    assert re.search(r"visibility:\s*hidden", closed)


def test_an_open_palette_takes_input_again():
    opened = _rule(".dd-palette-overlay.open ")
    assert re.search(r"pointer-events:\s*auto", opened)
    assert re.search(r"visibility:\s*visible", opened)
