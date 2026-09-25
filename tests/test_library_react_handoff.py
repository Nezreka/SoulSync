"""/library is served by React, and library.js itself is now deleted.

The vanilla Library list went first; the manage layer and the Watch All modal
followed in the enhanced-view port, and the file's last residents moved to
library-globals.js. What survives the handoff and is not visible from either
side alone: the ids the React page inherited (the guided tour anchors to
them), and the ss:library-changed seam — dispatch and listener both live in
React now, pinned here so a rename on either side fails loudly.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
_MANIFEST = (_ROOT / "webui" / "src" / "platform" / "shell" / "route-manifest.ts").read_text(
    encoding="utf-8"
)


def test_manifest_hands_library_to_react():
    assert "{ pageId: 'library', path: '/library', kind: 'react' }" in _MANIFEST


def test_watch_all_modal_announces_its_change_to_react():
    """The Watch All modal is React now (watch-all-modal.tsx), but the seam is
    the same: closing after a successful add announces the change. Without the
    event the watch badges stay stale until the user navigates away."""
    modal = (
        _ROOT / "webui" / "src" / "routes" / "library" / "-ui" / "watch-all-modal.tsx"
    ).read_text(encoding="utf-8")
    assert "ss:library-changed" in modal, "React is never told the list changed"
    assert "if (result)" in modal, "the event must stay gated on an actual change"


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
        for js in (_ROOT / "webui" / "static").glob("*.js"):
            source = js.read_text(encoding="utf-8", errors="replace")
            assert f"function {name}(" not in source, f"{name} survived the cleanup in {js.name}"
            # Call syntax only — a prose mention in a comment (init.js explains
            # WHY its old call site is gone) is not a live reference.
            assert f"{name}(" not in source, f"{name} is still called in {js.name}"

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
