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


def test_every_thumb_attaching_job_also_stores_artist_id():
    """Sweep guard: any repair job that puts artist_thumb_url into finding
    details must store artist_id beside it, so new findings navigate exactly
    (the name-resolve stays as the fallback for pre-sweep findings)."""
    jobs_dir = _ROOT / "core" / "repair_jobs"
    offenders = []
    for f in sorted(jobs_dir.glob("*.py")):
        src = f.read_text(encoding="utf-8")
        # look for the DETAILS KEY, not the SQL join (t.artist_id would false-pass)
        if "artist_thumb_url" in src and "'artist_id'" not in src and '"artist_id"' not in src:
            offenders.append(f.name)
    assert offenders == [], f"jobs attach artist art without the artist_id details key: {offenders}"


def test_click_prefers_stored_artist_id():
    fn = _JS[_JS.index("async function openFindingArtist"):]
    fn = fn[:fn.index("\n}") + 2]
    assert "data-artist-id" in fn
    # the direct-id navigation comes BEFORE the name-resolve fetch
    assert fn.index("data-artist-id") < fn.index("/api/library/artists?search=")
