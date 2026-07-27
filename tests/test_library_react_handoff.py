"""/library is served by React; library.js must not repaint behind it.

The vanilla Library page still exists in index.html (its markup and its ~197
functions are removed in the cleanup PR), so both could paint into the same
document. What keeps them apart is a single guard in the one function that
fills the grid, plus one event for the vanilla modal that still changes the
list. Neither is visible from the React side, so they are pinned here.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
_LIBRARY_JS = (_ROOT / "webui" / "static" / "library.js").read_text(encoding="utf-8")
_MANIFEST = (_ROOT / "webui" / "src" / "platform" / "shell" / "route-manifest.ts").read_text(
    encoding="utf-8"
)


def _fn(source: str, name: str) -> str:
    """The body of a top-level function, up to the next top-level declaration."""
    start = source.index(f"function {name}(")
    rest = source[start:]
    end = re.search(r"\n(?:async )?function ", rest[1:])
    return rest[: end.start() + 1] if end else rest


def test_manifest_hands_library_to_react():
    assert "{ pageId: 'library', path: '/library', kind: 'react' }" in _MANIFEST


def test_load_library_artists_bails_out_when_react_owns_the_page():
    """The single choke point.

    loadLibraryArtists is the ONLY thing that writes #library-artists-grid, so
    guarding it makes the vanilla filters, alphabet and pagination buttons
    unreachable rather than merely unused — they all route through here.
    """
    fn = _fn(_LIBRARY_JS, "loadLibraryArtists")
    guard = "if (document.getElementById('webui-react-root')?.classList.contains('active')) return;"
    assert guard in fn, "the React guard is gone — vanilla would repaint under the React page"

    # It must come before the fetch, not after.
    assert fn.index(guard) < fn.index("fetch("), "the guard runs after the request"


def test_watch_all_modal_announces_its_change_to_react():
    """The vanilla "Watch All Unwatched" modal used to refresh by calling
    loadLibraryArtists(), which the guard above now turns into a no-op. Without
    the event the watch badges stay stale until the user navigates away."""
    fn = _fn(_LIBRARY_JS, "closeWatchAllUnwatchedModal")
    assert "ss:library-changed" in fn, "React is never told the list changed"
    assert "needsRefresh" in fn, "the event must stay gated on an actual change"


def test_react_listens_for_that_exact_event():
    """Both halves of the seam, so a rename on either side fails here rather
    than silently going quiet."""
    live = (
        _ROOT / "webui" / "src" / "routes" / "library" / "-library.live.ts"
    ).read_text(encoding="utf-8")
    assert "'ss:library-changed'" in live


def test_the_page_react_renders_claims_no_vanilla_ids():
    """Duplicate ids would make getElementById return whichever comes first in
    the document — the vanilla page's node, since its markup is still in
    index.html. The React page uses the classes only."""
    page = (
        _ROOT / "webui" / "src" / "routes" / "library" / "-ui" / "library-page.tsx"
    ).read_text(encoding="utf-8")
    card = (
        _ROOT / "webui" / "src" / "routes" / "library" / "-ui" / "library-artist-card.tsx"
    ).read_text(encoding="utf-8")
    for name, source in (("library-page.tsx", page), ("library-artist-card.tsx", card)):
        assert " id=" not in source, f"{name} renders an id that the vanilla page still owns"


def test_react_owned_pages_are_declared_once_each():
    """A stray second entry for a pageId would make getShellRouteByPageId's
    answer depend on array order."""
    ids = re.findall(r"\{ pageId: '([a-z-]+)', path:", _MANIFEST)
    assert len(ids) == len(set(ids)), f"duplicate manifest entries: {sorted(ids)}"
    assert json.dumps(ids).count("library") >= 1
