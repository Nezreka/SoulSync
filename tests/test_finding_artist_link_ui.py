"""Tools-page findings: the artist picture card links to the artist's page.
Findings don't store the library artist id, so the click resolves by EXACT name
via /api/library/artists (works for pre-existing findings too; no fuzzy guess).
Source guards (vanilla JS, no runner)."""

from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
_JS = (_ROOT / "webui" / "static" / "enrichment.js").read_text(encoding="utf-8")
_CSS = (_ROOT / "webui" / "static" / "style.css").read_text(encoding="utf-8")


def test_artist_media_card_is_clickable():
    assert "openFindingArtist(this)" in _JS
    assert "repair-finding-media-card--link" in _JS
    assert "event.stopPropagation()" in _JS.split("openFindingArtist(this)")[0][-200:]  # doesn't toggle the card


def test_click_resolves_exact_name_no_fuzzy_guess():
    fn = _JS[_JS.index("async function openFindingArtist"):]
    fn = fn[:fn.index("\n}") + 2]
    assert "/api/library/artists?search=" in fn
    assert ".toLowerCase() === name.toLowerCase()" in fn     # exact match only
    assert "navigateToArtistDetail" in fn
    assert "isn't in your library" in fn                     # honest miss, not a guess


def test_hover_affordance_styled():
    assert ".repair-finding-media-card--link" in _CSS
