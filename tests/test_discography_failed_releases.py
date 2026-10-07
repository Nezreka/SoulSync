"""Download Discography: failed releases must not vanish silently.

A release that fails resolution entirely ("Album not found", a source error,
an empty tracklist) has no tracks to add to the wishlist, so without an
explicit record it disappears — the per-album NDJSON error line scrolls by
and nothing is retryable. The endpoint collects every such release into
``failed_releases`` and yields it on the completion line with everything the
client needs to re-run just those releases (album_id, name, source,
album_type).

This pins the three error paths and the completion payload at source level:
the generator is too entangled (DB, metadata providers, config) for a cheap
endpoint test, and the client-side parsing is covered by vitest.
"""

from __future__ import annotations

from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
_ENDPOINT = (_ROOT / "api" / "artist_detail.py").read_text(encoding="utf-8")


def _endpoint_fn() -> str:
    start = _ENDPOINT.index("def download_discography")
    # End at the next route: the return statement's exact shape changed under
    # #1199 (scoped_stream wrapper), so don't anchor on its text.
    end = _ENDPOINT.index("@bp.route('/api/artist/<artist_id>/completion'", start)
    return _ENDPOINT[start:end]


def _generate_fn() -> str:
    body = _endpoint_fn()
    return body[body.index("def generate_ndjson():"):]


def test_failed_releases_list_exists():
    assert "failed_releases = []" in _endpoint_fn()


def test_resolution_failure_is_recorded():
    gen = _generate_fn()
    # "Album not found" path
    assert '"error": message' in gen or "'error': message" in gen
    assert gen.count("failed_releases.append(") == 3


def test_failed_record_carries_retry_fields():
    gen = _generate_fn()
    for field in ('"album_id"', '"name"', '"source"', '"album_type"', '"error"'):
        assert field in gen, f"failed_releases record missing {field}"


def test_completion_line_yields_failed_releases():
    gen = _generate_fn()
    assert '"failed_releases": failed_releases' in gen


def test_client_parses_failed_releases():
    module = (
        _ROOT / "webui" / "src" / "routes" / "artist-detail"
        / "-artist-detail.discography-modal.ts"
    ).read_text(encoding="utf-8")
    assert "failed_releases: FailedRelease[]" in module
    assert "Array.isArray(data.failed_releases)" in module


def test_client_offers_retry():
    component = (
        _ROOT / "webui" / "src" / "routes" / "artist-detail" / "-ui"
        / "discography-modal.tsx"
    ).read_text(encoding="utf-8")
    assert "retryFailed" in component
    assert "discog-retry-btn" in component
