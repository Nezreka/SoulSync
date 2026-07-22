"""Music wishlist 'Search manually' must land on the Soulseek (basic) surface —
the user is there because auto-downloads failed; they want the FILE, not the
default metadata source. Source guard (vanilla JS, no runner)."""

from pathlib import Path

_JS = (Path(__file__).resolve().parent.parent / "webui" / "static" / "api-monitor.js").read_text(encoding="utf-8")


def test_manual_search_jump_targets_soulseek_surface():
    fn = _JS[_JS.index("function _searchWishlistTrackManually"):]
    fn = fn[:fn.index("\nfunction ", 10)]
    assert "downloads-search-input" in fn                     # basic-search input, not enhanced
    assert '[data-source="soulseek"]' in fn                   # clicks the Soulseek source icon
    assert "performDownloadsSearch" in fn                     # fallback runs the slskd search
    assert "enhanced-search-input" not in fn                  # no longer lands on metadata search
