"""/library is served by React; library.js must not repaint behind it.

The vanilla Library list is now DELETED — markup and functions both — so the
two can no longer paint into the same document. What survives the handoff and
is not visible from either side alone: the ids the React page inherited (the
guided tour anchors to them), and the one event the still-vanilla "Watch All"
modal uses to tell React its list changed.
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
        _ROOT / "webui" / "src" / "routes" / "library" / "-library-v2.live.ts"
    ).read_text(encoding="utf-8")
    assert "'ss:library-changed'" in live
    page = (
        _ROOT / "webui" / "src" / "routes" / "library" / "-ui" / "library-v2-page.tsx"
    ).read_text(encoding="utf-8")
    assert "useLibraryChanged()" in page, "the hook exists but the page never mounts it"


def test_the_vanilla_library_ids_are_claimed_by_nobody():
    """Inverted twice.

    While both pages existed the React port rendered classes only, because
    getElementById returns whichever node comes first in the document and that
    was the vanilla one. Then the vanilla markup went and the port took the ids
    over. Library v2 replaced that port outright, and it is CSS-module scoped —
    it renders none of those ids, and nothing reads them any more: helper.js
    anchors the library tour on `.nav-button[data-page="library"]`, not on the
    page's internals.

    What still matters is the half that outlived the ids: nothing in index.html
    may claim them either, because the VIDEO library page lives in there and a
    revived music id would resolve by document order all over again.
    """
    index = (_ROOT / "webui" / "index.html").read_text(encoding="utf-8")
    for anchor in ("library-page", "library-search-input", "library-artists-grid"):
        assert f'id="{anchor}"' not in index, (
            f"#{anchor} is back in index.html — the music library is React-only, "
            "so a vanilla element answering to it can only be the wrong one"
        )

    helper = (_ROOT / "webui" / "static" / "helper.js").read_text(encoding="utf-8")
    assert '.nav-button[data-page="library"]' in helper, (
        "the library tour lost its only anchor"
    )


def test_vanilla_library_list_is_gone():
    """The list functions and their markup, deleted together — a leftover
    definition would be unreachable code that still looks live."""
    for name in (
        "initializeLibraryPage",
        "loadLibraryArtists",
        "displayLibraryArtists",
        "buildLibraryArtistCardHTML",
        "toggleLibraryCardWatchlist",
        "showLibraryEmpty",
    ):
        assert f"function {name}(" not in _LIBRARY_JS, f"{name} survived the cleanup"
        assert name not in _LIBRARY_JS, f"{name} is still referenced somewhere in library.js"

    index = (_ROOT / "webui" / "index.html").read_text(encoding="utf-8")
    assert 'id="library-page"' not in index
    # ...but the VIDEO library must be untouched. It shares nothing with the
    # music side except a similar name, which is exactly why it is pinned.
    assert 'id="video-library-page"' in index
    # The artist-detail container was pinned here too, because the library-list
    # cleanup nearly took it as collateral. Artist detail has since been ported
    # and its markup deliberately deleted, so that guard has done its job —
    # test_artist_detail_css_hook covers what replaced it.
    assert 'id="artist-detail-page"' not in index


def test_react_owned_pages_are_declared_once_each():
    """A stray second entry for a pageId would make getShellRouteByPageId's
    answer depend on array order."""
    ids = re.findall(r"\{ pageId: '([a-z-]+)', path:", _MANIFEST)
    assert len(ids) == len(set(ids)), f"duplicate manifest entries: {sorted(ids)}"
    assert json.dumps(ids).count("library") >= 1
