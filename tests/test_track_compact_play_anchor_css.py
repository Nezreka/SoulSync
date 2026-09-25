"""#1296 the compact-row play button has to stay inside its thumbnail.

.track-compact-play is position:absolute with inset:0. it only stays a 40px
overlay because .track-compact-image is its containing block. that anchor used
to be scoped to .has-select, so the search playlist modal (play buttons, no
checkboxes) let every button escape to the modal overlay: invisible buttons
covering the whole screen that ate every click and every scroll.

jsdom can't lay anything out, so no render test would ever notice. measured in
chromium: 1280x800 before the fix, 40x40 after.
"""

from __future__ import annotations

import re
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
_CSS = re.sub(
    r"/\*.*?\*/",
    " ",
    (_ROOT / "webui/static/style.css").read_text(encoding="utf-8", errors="replace"),
    flags=re.S,
)


def _rules(selector: str) -> list[str]:
    """bodies of every rule whose selector list names exactly this selector."""
    bodies = []
    for sel, body in re.findall(r"([^{}]+)\{([^{}]*)\}", _CSS):
        if selector in (s.strip() for s in sel.split(",")):
            bodies.append(body)
    return bodies


def test_the_thumb_anchors_the_play_button_on_every_row():
    bodies = _rules(".track-compact-image")
    assert any(re.search(r"position\s*:\s*relative", b) for b in bodies), (
        "the bare .track-compact-image rule lost position:relative, so a row "
        "without .has-select lets its play button cover the whole modal (#1296)"
    )


def test_the_play_button_is_still_the_absolute_overlay_this_guards():
    # if it stops being absolute this whole guard is moot, say so loudly
    bodies = _rules(".track-compact-play")
    assert any(re.search(r"position\s*:\s*absolute", b) for b in bodies)


def test_the_hover_reveal_is_not_scoped_to_selectable_rows():
    assert _rules(".track-compact-image:hover .track-compact-play"), (
        "the play overlay only shows on hover in selectable rows, so the search "
        "playlist modal's play buttons are invisible"
    )
