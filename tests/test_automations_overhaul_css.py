"""Automations overhaul — contract safety net.

The overhaul is the sidebar + overview + collection-library navigation for the
music automations page, styled by webui/static/automations-overhaul.css. These
tests hold its four promises:

1. It's actually loaded, after automations-redesign.css (so it wins ties).
2. Every rule is scoped under .automx — nothing can leak into the video
   automations page or any other surface.
3. Every automx-* class it styles exists in the real JSX — a typo'd selector
   silently no-ops, which ships an unstyled element.
4. The design-token definitions live on a selector that actually matches
   rendered markup. (Regression: they once sat on `.autmx`, a typo that
   matched nothing, silently killing every var(--automx-*) on the page.)

Plus a structural parse: balanced braces, non-empty rules.
"""

from __future__ import annotations

import re
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
_CSS = (_ROOT / "webui" / "static" / "automations-overhaul.css").read_text(encoding="utf-8")
_HTML = (_ROOT / "webui" / "index.html").read_text(encoding="utf-8")

# The overhaul styles the music page's React tree only; the video page still
# renders from the shared vanilla builders and must stay untouched.
_REACT = "".join(
    p.read_text(encoding="utf-8")
    for p in sorted((_ROOT / "webui" / "src" / "routes" / "automations").rglob("*.ts*"))
    if ".test." not in p.name
)
_VANILLA = (_ROOT / "webui" / "static" / "stats-automations.js").read_text(encoding="utf-8")
_SOURCES = _REACT + _VANILLA


def _strip_comments(css: str) -> str:
    return re.sub(r"/\*.*?\*/", "", css, flags=re.S)


def _is_keyframe_selector(sel: str) -> bool:
    return all(
        re.match(r"^\d+%$|^from$|^to$", part.strip())
        for part in sel.split(",")
        if part.strip()
    ) and bool(sel.strip())


def _selectors(css: str | None = None):
    css = _strip_comments(_CSS if css is None else css)
    # drop @media wrappers but keep their inner rules
    css = re.sub(r"@media[^{]+\{", "", css)
    out = []
    for m in re.finditer(r"([^{}]+)\{[^{}]*\}", css):
        sel = m.group(1).strip()
        if sel.startswith("@") or not sel:
            continue
        if _is_keyframe_selector(sel):
            continue
        out.extend(p.strip() for p in sel.split(",") if p.strip())
    return out


def _rules():
    css = _strip_comments(_CSS)
    css = re.sub(r"@media[^{]+\{", "", css)
    return [
        (m.group(1).strip(), m.group(2))
        for m in re.finditer(r"([^{}]+)\{([^{}]*)\}", css)
        if not m.group(1).strip().startswith("@")
        and m.group(1).strip()
        and not re.match(r"^\d+%$|^from$|^to$", m.group(1).strip())
    ]


def test_stylesheet_is_linked_after_the_redesign_css():
    assert "automations-overhaul.css" in _HTML
    assert _HTML.index("automations-redesign.css") < _HTML.index("automations-overhaul.css")


def test_braces_balance():
    css = _strip_comments(_CSS)
    assert css.count("{") == css.count("}")
    assert css.count("{") > 60  # a real stylesheet, not a stub


def test_every_rule_is_scoped_under_automx():
    for sel in _selectors():
        assert ".automx" in sel, f"unscoped selector leaks past the music page: {sel}"


def test_every_automx_class_exists_in_the_markup():
    """A selector styling a class nothing renders is a silent no-op — a typo."""
    missing = []
    for sel in _selectors():
        for cls in re.findall(r"\.([a-zA-Z][\w-]*)", sel):
            if "automx" not in cls:
                continue
            if cls not in _SOURCES:
                missing.append((sel, cls))
    assert not missing, f"selectors referencing unknown classes: {missing[:8]}"


def test_token_definitions_live_on_a_rendered_selector():
    """The --automx-* custom properties must be defined on a class the page
    actually renders, or every var(--automx-*) on the page silently dies."""
    defining = [sel for sel, body in _rules() if re.search(r"--automx-[\w-]+\s*:", body)]
    assert defining, "no rule defines the --automx-* design tokens"
    for sel in defining:
        classes = re.findall(r"\.([a-zA-Z][\w-]*)", sel)
        assert classes, f"token rule has no class selector: {sel}"
        assert any(c in _SOURCES for c in classes), (
            f"design tokens are defined on {sel}, which nothing renders — "
            "every var(--automx-*) on the page resolves to nothing"
        )


def test_no_video_page_selectors():
    """The overhaul must not restyle the video automations surface."""
    css = _strip_comments(_CSS)
    for needle in ("video-automations", "vauto-", ".video-subpage"):
        assert needle not in css, f"overhaul reaches the video page: {needle}"


def test_reduced_motion_is_respected():
    assert "prefers-reduced-motion" in _CSS
