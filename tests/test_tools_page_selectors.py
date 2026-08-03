"""Guards for the DOM contract between the vanilla Tools page and its JS.

Every bug these lock in was a silent one: no error, no failing test, just a
control that quietly stopped working. They are all the same shape — a string
literal in JS that names something the markup does not actually have.

Written alongside the Tools-page bugfix PR; see tools-p0-notes for the full read.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

WEBUI = Path(__file__).resolve().parent.parent / "webui"
INDEX = WEBUI / "index.html"
STATIC = WEBUI / "static"

# The Tools page markup region in index.html (`<div class="page" id="tools-page">`).
TOOLS_PAGE_ID = "tools-page"

# Files that own Tools-page behaviour (the call closure of #tools-page).
TOOLS_CLOSURE_FILES = {
    "enrichment.js",
    "wishlist-tools.js",
    "api-monitor.js",
    "media-player.js",
    "config-migration.js",
    "manual-library-match.js",
    "stats-automations.js",
    "sync-services.js",
}

# Pre-existing `getElementById('<a class name>')` calls in OTHER features. Each
# was verified the same way the media-scan one was: the token is never assigned
# as an id anywhere (markup, JS templates, `.id =`, setAttribute, or the React
# sources), so every one of these lookups returns null and whatever it guards
# silently does nothing.
#
# They are NOT fixed here — they belong to the track-detail modal, the retag
# modal, the downloads stat row and the automations list, none of which this PR
# audited. Allowlisted so this test is a ratchet rather than a blocker.
# Shrink this list; never grow it.
KNOWN_CLASS_AS_ID = {
    # track-detail modal: audio element, artwork, badges, provenance, actions
    ("track-detail.js", "td-audio"),
    ("track-detail.js", "td-thumb"),
    ("track-detail.js", "td-thumb-ph"),
    ("track-detail.js", "td-status-badge"),
    ("track-detail.js", "td-provenance"),
    ("track-detail.js", "td-reason"),
    ("track-detail.js", "td-actions"),
    # retag modal
    ("wishlist-tools.js", "retag-batch-bar"),
    ("wishlist-tools.js", "retag-batch-count"),
    ("wishlist-tools.js", "retag-clear-all-btn"),
    ("wishlist-tools.js", "retag-modal-body"),
    ("wishlist-tools.js", "retag-search-results"),
    # downloads stat row
    ("downloads.js", "stat-found"),
    ("downloads.js", "stat-missing"),
    ("downloads.js", "stat-downloaded"),
    ("downloads.js", "artists-grid"),
    # automations list + artist tooling
    ("stats-automations.js", "automations-list"),
    ("stats-automations.js", "automations-empty"),
    ("stats-automations.js", "automations-stats"),
    ("stats-automations.js", "library-artist-write-image-btn"),
    ("shared-helpers.js", "artists-search-state"),
    ("sync-services.js", "expand-indicator"),
}

# Pre-existing dangling class selectors, same deal.
KNOWN_DEAD_CLASS_SELECTORS = {
    ("stats-automations.js", "write-image-text"),
}


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def _static_js() -> dict[str, str]:
    return {p.name: _read(p) for p in sorted(STATIC.glob("*.js"))}


def _html() -> str:
    return _read(INDEX)


def _all_ids() -> set[str]:
    """Every id the app can actually produce — markup, JS templates (either quote
    style), direct `.id =` assignment, setAttribute, and the React sources."""
    blob = _html() + "".join(_static_js().values())
    for pattern in ("src/**/*.ts", "src/**/*.tsx"):
        for path in WEBUI.glob(pattern):
            blob += _read(path)

    ids: set[str] = set()
    ids |= set(re.findall(r"""\bid=["']([^"'$<>]+)["']""", blob))
    ids |= set(re.findall(r"""\bid=\{?["'`]([\w-]+)["'`]\}?""", blob))
    ids |= set(re.findall(r"""\.id\s*=\s*["'`]([\w-]+)["'`]""", blob))
    ids |= set(re.findall(r"""setAttribute\(\s*["']id["']\s*,\s*["']([\w-]+)["']""", blob))
    return ids


def _all_classes() -> set[str]:
    """Class tokens present in markup, JS templates, classList calls or CSS."""
    blob = _html() + "".join(_static_js().values())
    classes: set[str] = set()
    for group in re.findall(r"""\bclass(?:Name)?\s*=\s*["']([^"']+)["']""", blob):
        classes.update(w for w in group.split() if w and "$" not in w)
    for args in re.findall(r"classList\.(?:add|toggle|remove|contains)\(([^)]*)\)", blob):
        classes.update(re.findall(r"""['"]([\w-]+)['"]""", args))
    for css in STATIC.glob("*.css"):
        classes.update(re.findall(r"\.([a-zA-Z][\w-]*)\s*[\{,:]", _read(css)))
    return classes


def _tools_region() -> str:
    """The #tools-page markup, start tag through its matching close."""
    html = _html()
    start = html.index(f'<div class="page" id="{TOOLS_PAGE_ID}">')
    # The region ends at the next sibling at the same indentation.
    end = html.index("\n            <!--", start)
    return html[start:end]


def test_get_element_by_id_never_names_a_css_class():
    """`getElementById('x')` where x is only ever a CLASS is always a dead lookup.

    This is exactly how the media-scan button broke: the websocket completion
    handler looked up `media-scan-btn` (the class) instead of `media-scan-button`
    (the id), so it never re-enabled the button and Scan Library died after one
    click.
    """
    ids, classes = _all_ids(), _all_classes()
    offenders = []
    for name, src in _static_js().items():
        for lineno, line in enumerate(src.split("\n"), 1):
            for match in re.finditer(r"""getElementById\(\s*['"]([\w-]+)['"]""", line):
                token = match.group(1)
                if token in ids or token not in classes:
                    continue
                if (name, token) in KNOWN_CLASS_AS_ID:
                    continue
                offenders.append(f"{name}:{lineno} getElementById('{token}') — that's a CSS class, not an id")
    assert not offenders, "getElementById called with a class name:\n  " + "\n  ".join(offenders)


def test_tools_closure_class_selectors_resolve():
    """A `.foo` selector in the Tools closure must match a class that exists.

    `openRepairModal` scrolled to `.tools-maintenance-section` for however long;
    the hero's class is `tools-maintenance-hero`, so the scroll never happened.
    """
    classes = _all_classes()
    offenders = []
    for name, src in _static_js().items():
        if name not in TOOLS_CLOSURE_FILES:
            continue
        for lineno, line in enumerate(src.split("\n"), 1):
            for match in re.finditer(r"""querySelector(?:All)?\(\s*['"]([^'"$]*)['"]\s*\)""", line):
                selector = match.group(1)
                for token in re.findall(r"\.([a-zA-Z][\w-]*)", selector):
                    if token in classes or (name, token) in KNOWN_DEAD_CLASS_SELECTORS:
                        continue
                    offenders.append(f"{name}:{lineno} querySelector('{selector}') — no such class '.{token}'")
    assert not offenders, "dangling class selector in the Tools closure:\n  " + "\n  ".join(offenders)


def test_repair_hero_selector_is_scoped_to_the_music_tools_page():
    """`.tools-maintenance-hero` exists TWICE — video tools uses it too, and comes
    first in the document. An unscoped query lands on the wrong hero."""
    html = _html()
    assert html.count('class="tools-maintenance-hero"') == 2, (
        "expected exactly two maintenance heroes (music + video); update this guard if that changed"
    )
    enrichment = _read(STATIC / "enrichment.js")
    assert "'#tools-page .tools-maintenance-hero'" in enrichment, (
        "openRepairModal must scope its hero lookup to #tools-page, or it scrolls to the video one"
    )


def test_every_tool_help_button_has_help_content():
    """A `?` button whose data-tool has no TOOL_HELP_CONTENT entry does nothing
    but log a console warning — a visibly dead control."""
    region = _tools_region()
    declared = sorted(set(re.findall(r'data-tool="([^"]+)"', region)))
    assert declared, "expected the Tools page to carry data-tool help buttons"

    src = _read(STATIC / "wishlist-tools.js")
    start = src.index("const TOOL_HELP_CONTENT")
    brace = src.index("{", start)
    depth, idx = 0, brace
    while idx < len(src):
        if src[idx] == "{":
            depth += 1
        elif src[idx] == "}":
            depth -= 1
            if depth == 0:
                break
        idx += 1
    keys = set(re.findall(r"^\s{4}'([^']+)':", src[brace:idx], re.M))

    missing = [tool for tool in declared if tool not in keys]
    assert not missing, f"tools page help buttons with no TOOL_HELP_CONTENT entry: {missing}"


def test_helper_page_hints_never_route_to_a_page_without_the_element():
    """helper.js's search jumps to `_guessPageFromSelector(selector)` and then
    looks for the element there. All eight tool cards were listed under
    'dashboard' while living in #tools-page, so every hit navigated away and
    found nothing.

    A selector with no hint at all is tolerated (it just doesn't navigate); a
    hint pointing at the WRONG page is not.
    """
    src = _read(STATIC / "helper.js")
    hints_block = re.search(r"const pageHints = \{(.*?)\n    \};", src, re.S)
    assert hints_block, "could not find pageHints in helper.js"

    hints: list[tuple[str, list[str]]] = []
    for line in hints_block.group(1).strip().split("\n"):
        match = re.match(r"\s*'([^']+)':\s*\[(.*)\],?\s*$", line)
        if match:
            patterns = [p.strip().strip("'") for p in match.group(2).split(",") if p.strip()]
            hints.append((match.group(1), patterns))
    assert hints, "parsed no pageHints entries"

    def guess(selector: str) -> str | None:
        lowered = selector.lower()
        for page, patterns in hints:
            for pattern in patterns:
                if pattern.lower() in lowered:
                    return page
        return None

    # Map each id in index.html to the .page that contains it.
    html_lines = _html().split("\n")
    page_of: dict[str, str] = {}
    current: str | None = None
    for line in html_lines:
        page = re.search(r'<div class="page" id="([^"]+)-page"', line)
        if page:
            current = page.group(1)
        for found in re.findall(r'\bid="([^"]+)"', line):
            page_of.setdefault(found, current)

    offenders = []
    for selector in re.findall(r"^\s{4}'(#[^']+)':\s*\{", src, re.M):
        base = selector[1:].split(" ")[0].split(".")[0]
        actual = page_of.get(base)
        if actual is None:
            continue  # not a page-scoped element (global chrome, or JS-created)
        guessed = guess(selector)
        if guessed is not None and guessed != actual:
            offenders.append(f"{selector} routes to '{guessed}' but lives on '{actual}'")
    assert not offenders, "helper.js page hints point at the wrong page:\n  " + "\n  ".join(offenders)


def test_setup_step_selectors_live_on_the_page_the_step_names():
    """SETUP_STEPS navigates to `page` then highlights `selector`; the first-scan
    step pointed at the dashboard while #db-updater-card is on the tools page."""
    src = _read(STATIC / "helper.js")
    html_lines = _html().split("\n")
    page_of: dict[str, str] = {}
    current: str | None = None
    for line in html_lines:
        page = re.search(r'<div class="page" id="([^"]+)-page"', line)
        if page:
            current = page.group(1)
        for found in re.findall(r'\bid="([^"]+)"', line):
            page_of.setdefault(found, current)

    steps = re.search(r"const SETUP_STEPS = \[(.*?)\n\];", src, re.S)
    assert steps, "could not find SETUP_STEPS in helper.js"

    offenders = []
    for line in steps.group(1).split("\n"):
        page = re.search(r"page:\s*'([^']+)'", line)
        selector = re.search(r"selector:\s*'#([\w-]+)'", line)
        if not (page and selector):
            continue
        actual = page_of.get(selector.group(1))
        if actual is not None and actual != page.group(1):
            offenders.append(
                f"step selector #{selector.group(1)} is on '{actual}' but the step navigates to '{page.group(1)}'"
            )
    assert not offenders, "SETUP_STEPS navigates to the wrong page:\n  " + "\n  ".join(offenders)


# Files this PR converted end to end. stats-automations.js is deliberately NOT
# here: it still has one native confirm() at the artist.jpg overwrite prompt
# (~:5944), which belongs to the artist-image writer, not the Tools page.
CONFIRM_CLEAN_FILES = {"enrichment.js", "wishlist-tools.js", "config-migration.js", "api-monitor.js"}


def test_tools_page_uses_no_native_confirm():
    """SoulSync uses showConfirmDialog everywhere; window.confirm is a hard no.

    config-migration.js keeps ONE guarded fallback for the case where core.js
    hasn't defined showConfirmDialog — that's the only permitted use, and it is
    recognised by the `typeof showConfirmDialog === 'function'` test guarding it.
    """
    offenders = []
    for name, src in _static_js().items():
        if name not in CONFIRM_CLEAN_FILES:
            continue
        lines = src.split("\n")
        for lineno, line in enumerate(lines, 1):
            if not re.search(r"(?<![.\w])confirm\s*\(", line) and "window.confirm" not in line:
                continue
            if "showConfirmDialog" in line or "confirmText" in line:
                continue
            # Permitted: the `else` arm of an explicit availability check. The
            # guard sits at the head of the if/else chain, so look back far
            # enough to clear the showConfirmDialog({...}) call in between.
            window_before = "\n".join(lines[max(0, lineno - 15) : lineno])
            if "typeof showConfirmDialog" in window_before:
                continue
            offenders.append(f"{name}:{lineno} {line.strip()[:90]}")
    assert not offenders, "native confirm() where showConfirmDialog is required:\n  " + "\n  ".join(offenders)


@pytest.mark.parametrize(
    "helper_name",
    ["initializeToolHelpButtons"],
)
def test_repeat_bound_initialisers_are_idempotent(helper_name: str):
    """initializeToolsPage() re-runs on every visit to the Tools page, so anything
    it calls must guard its listener binding. This one used to add a fresh
    document-level keydown handler per visit."""
    src = _read(STATIC / "wishlist-tools.js")
    start = src.index(f"function {helper_name}(")
    brace = src.index("{", start)
    depth, idx = 0, brace
    while idx < len(src):
        if src[idx] == "{":
            depth += 1
        elif src[idx] == "}":
            depth -= 1
            if depth == 0:
                break
        idx += 1
    body = src[brace : idx + 1]

    assert "document.addEventListener" not in body, (
        f"{helper_name} binds a document-level listener but runs on every Tools visit — "
        "the handlers accumulate. Bind it once at module scope instead."
    )
    assert "_toolsWired" in body, (
        f"{helper_name} runs on every Tools visit; guard its bindings with a _toolsWired flag"
    )
