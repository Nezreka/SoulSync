"""Automations visual redesign — contract safety net (Boulder's overnight).

The redesign is a pure reskin: a stylesheet loaded after style.css/mobile.css
that restyles ONLY existing markup. These tests hold the three promises that
make it functionally risk-free:

1. It's actually loaded (index.html links it after mobile.css).
2. Every class it styles exists in the real markup sources — a typo'd
   selector silently no-ops, which would ship an unstyled element.
3. Every rule is scoped to automations surfaces — nothing can leak into
   other pages that happen to share generic class names (.config-row,
   .toggle-slider live elsewhere too).

Plus a structural parse: balanced braces, non-empty rules.
"""

from __future__ import annotations

import re
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
_CSS = (_ROOT / "webui" / "static" / "automations-redesign.css").read_text(encoding="utf-8")
_HTML = (_ROOT / "webui" / "index.html").read_text(encoding="utf-8")
_JS = (_ROOT / "webui" / "static" / "stats-automations.js").read_text(encoding="utf-8")
_VJS = (_ROOT / "webui" / "static" / "video" / "video-automations.js").read_text(encoding="utf-8")

# The MUSIC automations list is a React page now (webui/src/routes/automations),
# so its markup lives in JSX rather than in index.html or stats-automations.js.
# The video page still renders from the shared vanilla builders, which is why
# those two sources stay. Without the React source this check reports every
# music-side class as an unused selector.
_REACT = "".join(
    p.read_text(encoding="utf-8")
    for p in sorted((_ROOT / "webui" / "src" / "routes" / "automations").rglob("*.ts*"))
    if ".test." not in p.name
)
_SOURCES = _HTML + _JS + _VJS + _REACT

# Ancestors that guarantee a rule only applies on the automations surfaces.
_SCOPES = (
    ".automations-list-view", ".automations-builder-view", ".automations-section",
    ".automations-stats", ".automation-card", ".automations-empty",
    ".auto-filter-bar", ".automations-section-header", ".automations-section-body",
    "#automations-stats",
    # satellite surfaces that mount on document.body — scoped by their own
    # automations-specific class names
    ".automation-history-modal", ".auto-group-dropdown",
)


def _strip_comments(css: str) -> str:
    return re.sub(r"/\*.*?\*/", "", css, flags=re.S)


def _selectors():
    css = _strip_comments(_CSS)
    # drop @media wrappers but keep their inner rules
    css = re.sub(r"@media[^{]+\{", "", css)
    out = []
    for m in re.finditer(r"([^{}]+)\{[^{}]*\}", css):
        sel = m.group(1).strip()
        if sel.startswith("@") or not sel:
            continue
        out.extend(p.strip() for p in sel.split(",") if p.strip())
    return out


def test_stylesheet_is_linked_after_mobile_css():
    assert "automations-redesign.css" in _HTML
    assert _HTML.index("mobile.css") < _HTML.index("automations-redesign.css")


def test_braces_balance():
    css = _strip_comments(_CSS)
    assert css.count("{") == css.count("}")
    assert css.count("{") > 60          # a real stylesheet, not a stub


def test_every_rule_is_scoped_to_automations_surfaces():
    for sel in _selectors():
        if sel.startswith("@keyframes") or re.match(r"^\d+%|^from$|^to$", sel):
            continue
        assert any(scope in sel for scope in _SCOPES), f"unscoped selector: {sel}"


def test_every_styled_class_exists_in_the_markup():
    """A selector styling a class nothing renders is a silent no-op — a typo."""
    # Classes composed at runtime (class="history-log-" + log.type) never
    # appear as literals in source; the composing prefix proves the family.
    _DYNAMIC_PREFIXES = ("history-log-",)
    missing = []
    for sel in _selectors():
        for cls in re.findall(r"\.([a-zA-Z][\w-]*)", sel):
            if any(cls.startswith(p) and p in _SOURCES for p in _DYNAMIC_PREFIXES):
                continue
            # pseudo-selector fragments and state classes toggled by JS count
            # as present if the STRING appears anywhere in markup or JS.
            if f"{cls}" not in _SOURCES:
                missing.append((sel, cls))
    assert not missing, f"selectors referencing unknown classes: {missing[:8]}"


def test_live_status_hooks_are_styled():
    """The states JS toggles at runtime must all have visual treatments."""
    for needle in (
        ".automation-status.enabled", ".automation-status.disabled",
        ".automation-status.running", ".automation-card.running",
        ".automation-card.dragging", ".automation-output.visible",
        ".automation-output.finished", ".automation-output.error",
        ".automations-section.collapsed", ".automations-section-body.drop-target",
        ".automations-section.no-drop", ".auto-master-toggle.on",
        ".flow-slot.drag-over", ".automation-toggle input:checked",
    ):
        assert needle in _strip_comments(_CSS), f"missing state styling: {needle}"


def test_reduced_motion_is_respected():
    assert "prefers-reduced-motion" in _CSS


def test_section_body_stays_a_plain_block():
    """The layout-break regression (Boulder's morning screenshots): the card
    grid was put on .automations-section-body, but the REAL card container
    (.automations-grid) is NESTED inside it — so the inner grid landed in one
    auto-fill track (dead right half of the screen) and the Hub's children
    became stretched grid items. The body hosts non-card content and must
    never be a grid/flex container; the grid belongs to .automations-grid."""
    css = _strip_comments(_CSS)
    for m in re.finditer(r"([^{}]+)\{([^}]*)\}", css):
        sel, body = m.group(1).strip(), m.group(2)
        if sel.endswith(".automations-section-body") and "display" in body:
            assert "grid" not in body and "flex" not in body, (
                f"section body must stay a block: {sel} {{ {body.strip()} }}")
    assert ".automations-section .automations-grid" in css
    assert "repeat(auto-fill" in css


def test_no_page_ambient_background():
    """The purple container glow is gone (no other page has one)."""
    assert ".automations-container::before" not in _CSS


def test_card_noise_reduction_in_renderer():
    """Last-run summaries show only non-zero facts and names get tooltips.
    (The chip-dedup experiment is REVERTED: every card, system included,
    reads WHEN -> WHAT — a trigger-only flow line looks broken.)"""
    assert "showActionChip" not in _JS
    assert "v !== 0" in _JS and "v !== '0'" in _JS
    assert 'title="${_escAttr(a.name)}"' in _JS


def test_one_uniform_card_design_for_all_sections():
    """Boulder: system and user automations are the same thing to the eye —
    no per-section geometry splits. The rows experiment stays dead."""
    css = _strip_comments(_CSS)
    assert "section-protected .automation-card" not in css
    assert "section-protected .automations-grid" not in css


def test_meta_line_is_inline_flow_not_flex():
    """The meta is plain block flow that WRAPS — never flex (anonymous text
    items crush into vertical stacks) and never nowrap/ellipsis (hiding
    information; Boulder: a second line beats invisible text). No
    system-only surface tint may exist either (uniform cards, full stop)."""
    css = _strip_comments(_CSS)
    idx = css.index(".automation-card .automation-meta {")
    body = css[idx:css.index("}", idx)]
    assert "display: block" in body
    assert "flex" not in body
    assert "nowrap" not in body and "ellipsis" not in body
    assert ".automation-card.system {" not in css
