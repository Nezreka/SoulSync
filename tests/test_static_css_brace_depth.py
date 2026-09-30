"""every stylesheet in webui/static has to close exactly what it opens.

sept 29 2026: mobile.css had one stray `}` after the hydrabase block. browsers
don't error on that, they read the `}` as the start of the NEXT rule's
selector, so the whole watchlist / enhance buttons @media block after it was
thrown away. nothing looked broken, the phone styles just never applied.

counting `{` against `}` isn't enough (a stray close and a missing close
cancel out), so this walks the depth and fails the moment it goes negative.
"""

import re
from pathlib import Path

import pytest

STATIC = Path(__file__).resolve().parent.parent / "webui" / "static"
SHEETS = sorted(p for p in STATIC.glob("*.css"))


def _strip(css: str) -> str:
    css = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
    # strings can hold braces (content: '{'), blank them out
    return re.sub(r"'(?:\\.|[^'\\])*'|\"(?:\\.|[^\"\\])*\"", '""', css)


def _first_bad_line(css: str):
    depth = 0
    for lineno, line in enumerate(_strip(css).split("\n"), 1):
        for ch in line:
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth < 0:
                    return lineno, "closes a block that was never opened"
    return (None, f"{depth} block(s) never closed") if depth else None


def test_there_are_sheets_to_check():
    assert len(SHEETS) > 5


@pytest.mark.parametrize("sheet", SHEETS, ids=lambda p: p.name)
def test_braces_never_go_negative_and_all_close(sheet):
    assert _first_bad_line(sheet.read_text(encoding="utf-8")) is None


def test_a_stray_close_is_caught_even_when_counts_balance():
    # the mobile.css shape: one extra close, one missing close later
    css = ".a { color: red; } }\n.b { color: blue;\n"
    assert css.count("{") == css.count("}")
    assert _first_bad_line(css) == (1, "closes a block that was never opened")


def test_braces_inside_comments_and_strings_do_not_count():
    assert _first_bad_line("/* } */ .a::after { content: '}'; }") is None
