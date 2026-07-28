"""What other files borrow from library.js — pinned BEFORE the artist-detail port.

The automations migration taught this lesson once: the page being replaced was
not the only consumer of the code behind it, and the cleanup PR nearly deleted
things the video side still called. The library-list cleanup then taught it
again the hard way, by deleting four declarations that the artist-detail page
still needed.

So this file records, up front, every name that leaves library.js. When the
artist-detail cleanup eventually runs, anything listed here MUST survive it, or
be migrated deliberately along with its callers.

The surprising ones, which is exactly why this is written down:

  * `artistDetailPageState` is read by **stats-automations.js**. Two of the
    artist-detail page's own buttons -- Play Artist Radio, and writing the
    artist photo to disk -- are implemented over there and reach back into this
    page's state. The page's behaviour is split across two files.

  * `playLibraryTrack` (144 lines, and on the shared spine of the artist-detail
    call graph) is called by five other files. It cannot move into a React
    component; it has to stay reachable as a global.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
_STATIC = _ROOT / "webui" / "static"
_LIBRARY_JS = (_STATIC / "library.js").read_text(encoding="utf-8")

# name -> the files that reach for it. Hardcoded on purpose: deriving this from
# the current source would shrink whenever a consumer is deleted, and the test
# would pass vacuously at exactly the moment it should fail.
_CONTRACT = {
    "_esc": {
        "stats-automations.js", "auto-sync.js", "discover.js",
        "pages-extra.js", "label-detail.js", "wishlist-tools.js",
    },
    "playLibraryTrack": {
        "stats-automations.js", "shell-bridge.js", "downloads.js",
        "enrichment.js", "search.js",
    },
    "navigateToArtistDetail": {
        "label-detail.js", "shell-bridge.js", "enrichment.js", "search.js",
    },
    "artistDetailPageState": {"stats-automations.js"},
    "_updateSidebarLibraryBreadcrumb": {"shell-bridge.js"},
    "_handoffLibrarySearchToEnhancedSearch": {"label-detail.js"},
}


def _strip_comments(source: str) -> str:
    """Length-preserving, so a name only mentioned in a comment does not count
    as a real reference -- the mistake that made a dead modal look alive."""
    out = list(source)
    i, n, mode = 0, len(source), None
    while i < n:
        char, pair = source[i], source[i : i + 2]
        if mode is None:
            if pair == "//":
                mode = "line"; out[i] = out[i + 1] = " "; i += 2; continue
            if pair == "/*":
                mode = "block"; out[i] = out[i + 1] = " "; i += 2; continue
            if char in "\"'`":
                mode = char; i += 1; continue
            i += 1
        elif mode == "line":
            if char == "\n": mode = None
            else: out[i] = " "
            i += 1
        elif mode == "block":
            if pair == "*/":
                out[i] = out[i + 1] = " "; mode = None; i += 2; continue
            if char != "\n": out[i] = " "
            i += 1
        else:
            if char == "\\": i += 2; continue
            if char == mode: mode = None
            i += 1
    return "".join(out)


@pytest.mark.parametrize("name", sorted(_CONTRACT))
def test_library_js_still_declares_the_name(name):
    """It must exist. This is the check the library-list cleanup did not have:
    `artistDetailPageState` was deleted as collateral and 177 references were
    left pointing at nothing, while the file still parsed cleanly."""
    assert re.search(rf"^(?:async )?function {re.escape(name)}\b", _LIBRARY_JS, re.M) or re.search(
        rf"^(?:const|let|var)\s+{re.escape(name)}\b", _LIBRARY_JS, re.M
    ), f"library.js no longer declares {name}, which other files still call"


@pytest.mark.parametrize("name,consumers", sorted((k, v) for k, v in _CONTRACT.items()))
def test_every_recorded_consumer_still_uses_it(name, consumers):
    """The other half: if a consumer stops needing a name, this fails and the
    contract shrinks DELIBERATELY rather than the entry quietly going stale."""
    still_using = set()
    for filename in consumers:
        path = _STATIC / filename
        if not path.exists():
            continue
        source = _strip_comments(path.read_text(encoding="utf-8", errors="replace"))
        if re.search(rf"\b{re.escape(name)}\b", source):
            still_using.add(filename)

    assert still_using == consumers, (
        f"{name}: recorded consumers {sorted(consumers)}, actually using "
        f"{sorted(still_using)}. Update _CONTRACT deliberately."
    )


def test_artist_detail_state_is_reached_from_stats_automations():
    """Spelled out separately because it is the least obvious coupling in the
    codebase: the artist-detail page's Radio and artist-photo buttons live in
    stats-automations.js and read this page's module state directly. Porting
    the page to React without rehoming these leaves them reading a state object
    nothing updates any more."""
    source = _strip_comments((_STATIC / "stats-automations.js").read_text(encoding="utf-8"))
    assert "artistDetailPageState.currentArtistId" in source
    for fn in ("playArtistRadio",):
        assert re.search(rf"function {fn}\b", source), f"{fn} moved; recheck the coupling"


# ---------------------------------------------------------------------------
# The flip: only ONE page may load
# ---------------------------------------------------------------------------


def test_navigate_to_artist_detail_stops_before_the_legacy_load_when_react_owns_it():
    """`navigateToArtistDetail` must not run the vanilla page load under React.

    Search, label-detail and enrichment still call this function, and it used to
    fall straight through into `loadArtistDetailData`. With artist-detail
    React-owned that meant two pages loading over each other: the legacy load
    targets DOM by id and class, the React page reuses those same ids, and
    `applyDiscographyFilters` hides every `.release-card` on the document using
    the legacy filter state -- so the whole discography vanished on any artist
    reached from Search.

    The state assignments above the guard are deliberate: React reads
    currentArtistId / currentArtistName / currentArtistSource back out of them.
    """
    source = (Path(__file__).resolve().parents[1] / "webui/static/library.js").read_text(
        encoding="utf-8"
    )
    start = source.index("function navigateToArtistDetail(")
    end = source.index("\nfunction ", start + 1)
    body = source[start:end]

    guard = body.index("routeManifest")
    guard_return = body.index("return;", guard)
    legacy_load = body.index("loadArtistDetailData(")

    assert guard_return < legacy_load, (
        "navigateToArtistDetail reaches loadArtistDetailData even when the React "
        "page owns the route — two pages will load over each other"
    )
    assert "kind === 'react'" in body[guard:guard_return]


def test_the_label_stack_is_cleared_in_place_not_reassigned():
    """React holds a reference to the same array via window.artistDetailLabelStack.

    Reassigning `_artistDetailLabelStack = []` would leave React reading a
    detached copy, and the Back button would keep naming a page you had already
    left.
    """
    source = (Path(__file__).resolve().parents[1] / "webui/static/library.js").read_text(
        encoding="utf-8"
    )
    assert "_artistDetailLabelStack.length = 0" in source
    assert not re.search(r"_artistDetailLabelStack\s*=\s*\[\]\s*;(?!\s*//\s*declaration)", 
                         source.split("let _artistDetailLabelStack")[1])


def test_selected_tracks_set_identity_survives_navigation():
    """The vanilla deletes from this Set after a track delete.

    React mirrors its selection into the SAME object, so replacing it here would
    leave those writes landing on a Set nobody reads.
    """
    source = (Path(__file__).resolve().parents[1] / "webui/static/library.js").read_text(
        encoding="utf-8"
    )
    start = source.index("function navigateToArtistDetail(")
    end = source.index("\nfunction ", start + 1)
    body = source[start:end]
    assert "selectedTracks.clear()" in body
    assert "selectedTracks = new Set()" not in body
