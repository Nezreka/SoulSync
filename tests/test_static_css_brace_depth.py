"""every stylesheet in webui/static has to close exactly what it opens.

sept 29 2026: mobile.css had one stray `}` after the hydrabase block. browsers
don't error on that, they read the `}` as the start of the NEXT rule's
selector, so the whole watchlist / enhance buttons @media block after it was
thrown away. nothing looked broken, the phone styles just never applied.

counting `{` against `}` isn't enough (a stray close and a missing close
cancel out), so this walks the depth and fails the moment it goes negative.

oct 18 2026: video/video-side.css had the same shape (a leftover
`background: …; color: #fff; }` after a rule) and lost its phone @media
block, but this test only globbed webui/static/*.css and never looked in
video/. it now checks every sheet under webui/static, and also fails on a
declaration sitting outside any block, which browsers drop together with
whatever rule follows it.
"""

import re
from pathlib import Path

import pytest

STATIC = Path(__file__).resolve().parent.parent / "webui" / "static"
SHEETS = sorted(STATIC.rglob("*.css"))


def _strip(css: str) -> str:
    # keep a comment's newlines so reported line numbers stay right
    css = re.sub(r"/\*.*?\*/", lambda m: "\n" * m.group().count("\n"), css, flags=re.S)
    # strings can hold braces (content: '{'), blank them out
    return re.sub(r"'(?:\\.|[^'\\])*'|\"(?:\\.|[^\"\\])*\"", '""', css)


def _first_bad_line(css: str):
    depth = 0
    prelude = ""  # top-level text since the last rule or statement ended
    for lineno, line in enumerate(_strip(css).split("\n"), 1):
        for ch in line:
            if ch == "{":
                depth += 1
                prelude = ""
            elif ch == "}":
                depth -= 1
                if depth < 0:
                    return lineno, "closes a block that was never opened"
                prelude = ""
            elif depth == 0 and ch == ";":
                # only statement at-rules (@import, @charset, @layer a;) end
                # in `;` at the top level, anything else is a lost declaration
                if not prelude.strip().startswith("@"):
                    return lineno, "declaration outside any block"
                prelude = ""
            elif depth == 0:
                prelude += ch
        if depth == 0:
            prelude += "\n"
    return (None, f"{depth} block(s) never closed") if depth else None


def test_there_are_sheets_to_check():
    assert len(SHEETS) > 5
    assert STATIC / "video" / "video-side.css" in SHEETS


@pytest.mark.parametrize("sheet", SHEETS, ids=lambda p: p.name)
def test_braces_never_go_negative_and_all_close(sheet):
    assert _first_bad_line(sheet.read_text(encoding="utf-8")) is None


def test_a_stray_close_is_caught_even_when_counts_balance():
    # the mobile.css shape: one extra close, one missing close later
    css = ".a { color: red; } }\n.b { color: blue;\n"
    assert css.count("{") == css.count("}")
    assert _first_bad_line(css) == (1, "closes a block that was never opened")


def test_line_numbers_count_lines_inside_comments():
    assert _first_bad_line("/* one\ntwo */\n.a { }\n}") == (4, "closes a block that was never opened")


def test_braces_inside_comments_and_strings_do_not_count():
    assert _first_bad_line("/* } */ .a::after { content: '}'; }") is None


def test_a_leftover_declaration_after_a_rule_is_caught():
    # the video-side.css shape: the stray close comes after a stray declaration
    css = ".a { color: red; }\n    color: #fff;\n}\n@media (max-width: 600px) { .b { gap: 1px; } }\n"
    assert _first_bad_line(css) == (2, "declaration outside any block")


def test_top_level_statement_at_rules_are_fine():
    css = '@charset "utf-8";\n@import url("a.css");\n@layer base, theme;\n.a { color: red; }\n'
    assert _first_bad_line(css) is None
