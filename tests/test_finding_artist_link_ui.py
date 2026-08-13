"""Tools-page findings: the artist picture card links to the artist's page.

Findings don't store the library artist id, so the click resolves by EXACT name
via /api/library/artists (works for pre-existing findings too; no fuzzy guess).

The feature lives in REACT now. It was a vanilla renderer in enrichment.js
until the Tools P7 flip moved the findings surface to
``webui/src/routes/tools/-ui/finding-detail.tsx``; the vanilla copy sat
unreachable behind a deleted entry point until the dead-region sweep removed
it. These guards follow the feature — deleting them instead would have quietly
dropped the coverage on the version users actually run.

Source guards (no runner): the behavioural half is covered by the tsx's own
vitest suite, so what is pinned here is the set of decisions that are easy to
lose in a refactor and expensive to lose in production.
"""

from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
_TSX = (_ROOT / "webui" / "src" / "routes" / "tools" / "-ui" / "finding-detail.tsx").read_text(
    encoding="utf-8"
)
_CSS = (_ROOT / "webui" / "static" / "style.css").read_text(encoding="utf-8")


def _open_artist_fn() -> str:
    fn = _TSX[_TSX.index("async function openFindingArtist"):]
    return fn[: fn.index("\n}") + 2]


def test_artist_media_card_is_clickable():
    assert "openFindingArtist(" in _TSX
    assert "repair-finding-media-card--link" in _TSX
    # Clicking the artist card must not ALSO toggle the finding row open.
    before = _TSX.split("void openFindingArtist(")[0][-300:]
    assert "event.stopPropagation()" in before


def test_click_resolves_exact_name_no_fuzzy_guess():
    fn = _open_artist_fn()
    assert "searchLibraryArtists(" in fn
    assert "findExactArtist(" in fn                  # exact match only
    assert "navigateToArtistDetail" in fn
    assert "isn't in your library" in fn             # honest miss, not a guess


def test_click_prefers_stored_artist_id():
    fn = _open_artist_fn()
    # the direct-id navigation comes BEFORE the name-resolve fetch
    assert fn.index("artistId") < fn.index("searchLibraryArtists(")


def test_opaque_server_ids_are_not_coerced_to_numbers():
    """Ids are opaque server keys — numeric on Plex, alphanumeric on
    Navidrome/Jellyfin. Number() on a Navidrome id navigates to "NaN"."""
    fn = _open_artist_fn()
    assert "/^\\d+$/.test(artistId)" in fn


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
