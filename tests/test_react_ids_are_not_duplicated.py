"""No id may exist in BOTH the vanilla shell and a React page.

When a page is migrated, its React component renders the same ids the vanilla
markup had — because the rest of the app looks those ids up. `#enhanced-search-
input` is written by the global download widget; `#enhanced-main-results-area` is
where showSearchDownloadBubbles draws. If the vanilla markup is left behind after
the manifest flips, TWO elements answer to each id and `getElementById` resolves
it by document order.

That is not a contract. It happens to work today only because #webui-react-root
sits near the top of index.html; moving it, or wrapping it, silently sends every
one of those lookups to a hidden page instead.

So: an id rendered by a React page must not also appear in index.html.
"""

from __future__ import annotations

import re
from pathlib import Path

WEBUI = Path(__file__).resolve().parent.parent / "webui"
INDEX = WEBUI / "index.html"
ROUTES = WEBUI / "src" / "routes"

# `id="..."` in JSX, allowing the value to be a plain string only — a template
# expression is not a fixed id and cannot collide statically.
JSX_ID = re.compile(r"""\bid=["']([A-Za-z][\w-]*)["']""")
HTML_ID = re.compile(r"""\bid=["']([A-Za-z][\w-]*)["']""")

# Ids the React page renders ON PURPOSE into markup the vanilla still owns.
# There is exactly one: the basic-search panel, which the search page ADOPTS —
# it is the same element, moved, not a copy.
ADOPTED = {"basic-search-section"}

# Collisions that predate this guard.
#
# The artist-detail port (#1097/#1098) left the vanilla bulk-edit markup in
# index.html while its React component renders the same ids. It is latent rather
# than broken: #webui-react-root comes first in the document, so lookups land on
# the React elements, and the library.js functions that read these ids
# (updateBulkBar, showBulkEditModal — still present, still reading
# artistDetailPageState) are no longer reachable from a live page.
#
# Not fixed here because it belongs to that port's cleanup, not to search's.
# Deleting either side without tracing every caller is how a working page goes
# blank. Listed explicitly so the guard protects everything else in the meantime.
KNOWN_PRE_EXISTING = {
    "enhanced-bulk-bar",
    "enhanced-bulk-count",
    "enhanced-bulk-edit-overlay",
    "enhanced-bulk-modal-body",
    "enhanced-bulk-modal-title",
}


def _react_page_ids() -> dict[str, set[str]]:
    """Every literal id rendered by a React page component, by file."""
    found: dict[str, set[str]] = {}
    for path in ROUTES.rglob("-ui/*.tsx"):
        if path.name.endswith(".test.tsx"):
            continue
        source = path.read_text(encoding="utf-8")
        # Strip comments so a documented id is not mistaken for a rendered one.
        source = re.sub(r"/\*.*?\*/", "", source, flags=re.S)
        source = re.sub(r"^\s*//.*$", "", source, flags=re.M)
        ids = set(JSX_ID.findall(source)) - ADOPTED
        if ids:
            found[str(path.relative_to(WEBUI))] = ids
    return found


def _index_ids() -> set[str]:
    source = INDEX.read_text(encoding="utf-8")
    source = re.sub(r"<!--.*?-->", "", source, flags=re.S)
    return set(HTML_ID.findall(source))


def test_no_react_page_id_is_also_in_index_html():
    index_ids = _index_ids()
    collisions: list[str] = []
    for path, ids in _react_page_ids().items():
        for shared in sorted(ids & index_ids - KNOWN_PRE_EXISTING):
            collisions.append(f"{shared} — rendered by {path} and present in index.html")

    assert not collisions, (
        "These ids exist twice, and getElementById will answer with whichever "
        "comes first in the document:\n  " + "\n  ".join(collisions)
    )


def test_the_known_collisions_are_still_real():
    """A stale allowlist hides the next regression behind an id nobody uses.

    When that markup is finally cleaned up, this fails and the entry comes out.
    """
    index_ids = _index_ids()
    react_ids = {i for ids in _react_page_ids().values() for i in ids}
    stale = sorted(KNOWN_PRE_EXISTING - (index_ids & react_ids))
    assert not stale, f"No longer duplicated — drop from KNOWN_PRE_EXISTING: {stale}"


def test_the_guard_can_see_the_ids_it_is_guarding():
    """A regex that matched nothing would make the test above pass vacuously."""
    react_ids = {i for ids in _react_page_ids().values() for i in ids}
    assert "enhanced-search-input" in react_ids
    assert "enhanced-main-results-area" in react_ids
    assert "enh-source-row" in react_ids
    # And the vanilla side is being read at all.
    assert "basic-search-section" in _index_ids()
