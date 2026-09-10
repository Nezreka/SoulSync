"""MusicBrainz lookup lost everything after a double quote (#1230).

A track called ``Crazy (12" mix)`` reached the search box as ``Crazy (12``.

``escapeHtml`` sets textContent and reads innerHTML back, which escapes & < >
but NOT a double quote — a text node does not need one. Almost every caller
interpolates the result into a double-quoted ATTRIBUTE, where a raw quote ends
the attribute early and the browser discards the rest:

    <input value="Crazy (12" mix)">
                          ^ attribute ends here

The fix escapes the quote as well. Safe in both contexts because the output is
always inserted via innerHTML: &quot; renders as a plain quote in text, and
parses correctly inside an attribute.
"""

import re
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]

# escapeHtml is defined in BOTH files — a duplicate global whose behaviour must
# not depend on script load order.
_DEFINITIONS = (
    "webui/static/downloads.js",
    "webui/static/shared-helpers.js",
)


def _source(rel: str) -> str:
    return (_ROOT / rel).read_text(encoding="utf-8", errors="ignore")


@pytest.mark.parametrize("rel", _DEFINITIONS)
def test_escape_html_escapes_the_double_quote(rel):
    body = _source(rel).split("function escapeHtml(", 1)[1].split("\n}", 1)[0]
    assert "&quot;" in body, f"{rel} still leaves a raw quote in attribute values"


@pytest.mark.parametrize("rel", _DEFINITIONS)
def test_escape_html_still_relies_on_the_browser_for_the_rest(rel):
    # & < > stay the browser's job; only the quote is added on top.
    body = _source(rel).split("function escapeHtml(", 1)[1].split("\n}", 1)[0]
    assert "textContent" in body and "innerHTML" in body


def test_both_definitions_agree():
    """A duplicate global that behaves differently is worse than one that is
    simply wrong: which one wins depends on script load order."""
    bodies = []
    for rel in _DEFINITIONS:
        body = _source(rel).split("function escapeHtml(", 1)[1].split("\n}", 1)[0]
        bodies.append(re.sub(r"\s+|//.*", "", body))
    assert bodies[0] == bodies[1]


def test_the_musicbrainz_search_box_uses_it_for_its_value():
    # The reported symptom: the pre-filled query lost its tail.
    src = _source("webui/static/enrichment.js")
    assert 'id="mb-search-query" class="mb-search-input" value="${escapeHtml(entityName)}"' in src


def test_the_inline_js_escaper_already_handled_quotes():
    # escapeForInlineJs was never the bug — it escapes " to &quot; already.
    # Worth pinning so a future tidy-up does not "simplify" it onto escapeHtml
    # and reintroduce the JS-string half of the problem.
    body = _source("webui/static/downloads.js").split(
        "function escapeForInlineJs(", 1)[1].split("\n}", 1)[0]
    assert "&quot;" in body
    assert "\\\\'" in body


def test_no_caller_double_escapes():
    # escapeForInlineJs already emits &quot;; running escapeHtml over its output
    # would turn that into &amp;quot; and show the entity literally.
    for rel in ("webui/static/enrichment.js", "webui/static/downloads.js",
                "webui/static/shared-helpers.js"):
        src = _source(rel)
        assert "escapeHtml(escapeForInlineJs(" not in src
        assert "escapeForInlineJs(escapeHtml(" not in src
