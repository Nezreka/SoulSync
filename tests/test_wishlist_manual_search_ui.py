"""Music wishlist 'Search manually' must land on the Soulseek (basic) surface —
the user is there because auto-downloads failed; they want the FILE, not the
default metadata source. Source guard (vanilla JS, no runner)."""

from pathlib import Path

_JS = (Path(__file__).resolve().parent.parent / "webui" / "static" / "api-monitor.js").read_text(encoding="utf-8")


def _function_body(source: str, name: str) -> str:
    """Slice ONE top-level function out of the file. The boundary must accept
    both `function` and `async function` openers — slicing only to the next
    bare `\nfunction ` silently swallowed the async neighbours below the
    target, and this guard then failed on THEIR contents (the CI break where
    _navigateToArtistFromWishlist's metadata-search fallback — a legitimate
    use of the preserved #enhanced-search-input contract — was blamed on
    _searchWishlistTrackManually)."""
    fn = source[source.index(name):]
    ends = [i for i in (fn.find("\nfunction ", 10), fn.find("\nasync function ", 10))
            if i != -1]
    return fn[:min(ends)] if ends else fn


def test_manual_search_jump_targets_soulseek_surface():
    fn = _function_body(_JS, "function _searchWishlistTrackManually")
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
