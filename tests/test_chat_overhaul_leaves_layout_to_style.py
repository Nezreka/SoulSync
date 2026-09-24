"""chat-overhaul.css loads after all of style.css, so any layout it declares on the
chat shell beats every breakpoint style.css has for it. it did: the member rail
kept its 240px column after being hidden, narrow windows scrolled sideways and
phones lost the channel list entirely (discord, "chat ui not optimized").

measured in chromium, not here; this only keeps the layout declarations from
creeping back into the overhaul file.
"""

import re
from pathlib import Path

_STATIC = Path(__file__).resolve().parents[1] / "webui" / "static"
_FLOW = {"display", "flex-direction", "align-items", "overflow", "overflow-x", "overflow-y", "padding"}
# what style.css's breakpoints set on each part, so what the overhaul must not
_OWNED_BY_STYLE = {
    ".chat-shell": {"display", "grid-template-columns", "grid-template-rows", "gap", "height"},
    ".chat-guilds": _FLOW,
    ".chat-side": _FLOW,
    ".chat-side-scroll": _FLOW,
    ".chat-rail": {"display"},
}


def _rules(css):
    css = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
    for m in re.finditer(r"([^{}]+)\{([^{}]*)\}", css):
        yield [s.strip() for s in m.group(1).split(",")], m.group(2)


def test_the_overhaul_declares_no_layout_on_the_chat_shell():
    css = (_STATIC / "chat-overhaul.css").read_text(encoding="utf-8")
    offenders = []
    for selectors, body in _rules(css):
        props = {d.split(":", 1)[0].strip() for d in body.split(";") if ":" in d}
        for sel in selectors:
            bad = props & _OWNED_BY_STYLE.get(sel, set())
            if bad:
                offenders.append((sel, sorted(bad)))
    assert not offenders, f"layout belongs to style.css's breakpoints: {offenders}"


def test_style_keeps_the_laptop_slim_columns_with_the_member_rail():
    css = (_STATIC / "style.css").read_text(encoding="utf-8")
    m = re.search(r"@media \(max-width: 1600px\) \{\s*\.chat-shell \{([^}]*)\}", css)
    assert m and "minmax(0, 1fr) 200px" in m.group(1)
