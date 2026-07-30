"""Music wishlist 'Search manually' must land on the Soulseek (basic) surface —
the user is there because auto-downloads failed; they want the FILE, not the
default metadata source. Source guard (vanilla JS, no runner)."""

from pathlib import Path

_JS = (Path(__file__).resolve().parent.parent / "webui" / "static" / "api-monitor.js").read_text(encoding="utf-8")


def test_manual_search_jump_targets_soulseek_surface():
    fn = _JS[_JS.index("function _searchWishlistTrackManually"):]
    fn = fn[:fn.index("\nfunction ", 10)]
    assert '[data-source="soulseek"]' in fn                   # clicks the Soulseek source icon
    assert "enhanced-search-input" not in fn                  # no longer lands on metadata search

    # The query must be SYNCED into the search page before that click. The page
    # keeps its query across navigation, and the icon click hands off whatever
    # the page is holding — so without this the wishlist's track loses to the
    # last thing searched on /search. This replaced writing
    # #downloads-search-input directly and calling performDownloadsSearch, both
    # of which belonged to the vanilla basic panel that no longer exists.
    assert "_searchPageSetQuery" in fn
    assert fn.index("_searchPageSetQuery") < fn.index("soulseekIcon.click()")
