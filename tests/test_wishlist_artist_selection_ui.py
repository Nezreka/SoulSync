"""#1065 (QT3496) — artist-level selection + actions in the wishlist UI.

Source-contract pins for the frontend (the backend removal logic is tested
for real in tests/wishlist/test_routes.py): the category views render
per-artist sections with a tri-state cascade checkbox, per-artist remove
(confirm-gated, house modal) and per-artist download that pre-filters the
existing modal — all riding the .wishlist-select-cb substrate that
downloadSelectedCategory already consumes.
"""

from __future__ import annotations

from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
_JS = (_ROOT / "webui" / "static" / "downloads.js").read_text(encoding="utf-8")
_CSS = (_ROOT / "webui" / "static" / "style.css").read_text(encoding="utf-8")
_WS = (_ROOT / "web_server.py").read_text(encoding="utf-8")


def test_both_category_views_render_artist_sections():
    assert _JS.count("wishlist-artist-section") >= 4      # albums + singles render + handlers
    assert _JS.count("wishlist-artist-select-all-cb") >= 3
    assert "wishlist-artist-section" in _CSS


def test_artist_checkbox_cascades_and_tristates():
    assert "toggleWishlistArtistSelection" in _JS
    assert "_updateArtistTriState" in _JS
    assert "indeterminate" in _JS
    # album/track changes keep the artist checkbox honest
    body = _JS[_JS.index("wishlist-album-select-all-cb')) {"):]
    assert "_updateArtistTriState" in body[:400]


def test_artist_remove_is_confirm_gated_and_wired():
    assert "'/api/wishlist/remove-artist'" in _JS
    fn = _JS[_JS.index("async function removeArtistFromWishlist"):]
    assert "showConfirmDialog" in fn[:2200]               # house modal, never window.confirm
    assert "window.confirm" not in fn[:2200]
    assert "/api/wishlist/remove-artist" in _WS           # endpoint registered


def test_artist_download_prefilters_existing_modal():
    fn = _JS[_JS.index("async function downloadArtistFromWishlist"):]
    assert "openDownloadMissingWishlistModal" in fn[:800]
    assert "wishlist-select-cb" in fn[:800]               # rides the existing substrate
