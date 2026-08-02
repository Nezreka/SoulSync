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
        "stats-automations.js", "auto-sync.js",
        "pages-extra.js", "wishlist-tools.js",
    },
    # search.js was deleted when /search became React. The two entries below
    # were updated DELIBERATELY, which is what this test exists to force:
    #   - playLibraryTrack's coupling did NOT go away, it moved. The React
    #     search page calls the same global to play an owned track, so it is
    #     recorded where it lives now (same treatment as label-detail below).
    #   - navigateToArtistDetail's coupling genuinely IS gone: the React page
    #     renders /artist-detail/$source/$id hrefs and lets the router handle
    #     it instead of calling the global.
    "playLibraryTrack": {
        "stats-automations.js", "shell-bridge.js", "downloads.js",
        "enrichment.js", "src/routes/search/-search.actions.ts",
    },
    "navigateToArtistDetail": {
        "shell-bridge.js", "enrichment.js",
    },
    "artistDetailPageState": {"stats-automations.js"},
    "_updateSidebarLibraryBreadcrumb": {"shell-bridge.js"},
    # label-detail.js was this one's only consumer. The coupling did not go
    # away with it — the React label page calls the same global as its
    # no-modal fallback — so it is recorded where it lives now.
    "_handoffLibrarySearchToEnhancedSearch": {
        "src/routes/label-detail/-label-detail.open-release.ts",
    },
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
        # A bare name is a vanilla script; a path is a React module. Pages that
        # move to React keep their coupling to library.js — recording it here
        # rather than deleting the entry is what stops the port quietly
        # dropping a guard.
        path = (_STATIC / filename) if "/" not in filename else (_STATIC.parent / filename)
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


_LEGACY_PAGE_ENTRY_POINTS = (
    "loadArtistDetailData",
    "initializeArtistDetailPage",
    "populateArtistDetailPage",
    "applyDiscographyFilters",
    "_updateArtistDetailBackButtonLabel",
)


def test_navigate_to_artist_detail_only_hands_off_and_never_renders():
    """`navigateToArtistDetail` must not render an artist-detail page itself.

    Search, label-detail and enrichment still call this function, and it used to
    fall straight through into `loadArtistDetailData`. Once artist-detail became
    React-owned that meant two pages loading over each other: the legacy load
    targets DOM by id and class, the React page reuses those same ids, and
    `applyDiscographyFilters` hid every `.release-card` on the document using
    the legacy filter state -- so the whole discography vanished on any artist
    reached from Search.

    That path first went behind a manifest check, and the cleanup deleted it
    outright. What survives is the handoff plus the state writes above it, which
    React and the still-vanilla Enhanced modals read back out
    (currentArtistId / currentArtistName / currentArtistSource).
    """
    source = (Path(__file__).resolve().parents[1] / "webui/static/library.js").read_text(
        encoding="utf-8"
    )
    start = source.index("function navigateToArtistDetail(")
    end = source.index("\nfunction ", start + 1)
    body = _strip_comments(source[start:end])

    for name in _LEGACY_PAGE_ENTRY_POINTS:
        assert name not in body, (
            f"navigateToArtistDetail reaches {name}: the vanilla page is gone, so this "
            f"either renders nothing or fights the React page for the same DOM ids"
        )
    # Pin the TAIL handoff specifically. There is a second, earlier
    # navigateToPage in the already-on-this-artist short circuit, so merely
    # finding the call somewhere in the body proves nothing -- drop the final
    # one and the function would write state and then navigate nowhere.
    assert body.rindex("navigateToPage('artist-detail'") > body.rindex("selectedTracks.clear()"), (
        "navigateToArtistDetail writes the artist state but never hands off to the "
        "React route -- callers from Search/label-detail/enrichment would go nowhere"
    )


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
