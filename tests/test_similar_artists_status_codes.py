"""Regression seam tests for MusicMap fetch status-code classification.

Root cause (found via the worker's WARNING observability): MusicMap returns
HTTP 404 when an artist simply has no map page — a *not-found*, not a failure.
`_fetch_musicmap_similar_artist_names` calls `response.raise_for_status()`,
which raises `requests.exceptions.HTTPError` carrying the real 404. The error
handler in `iter_musicmap_similar_artist_events` used to flatten EVERY network
error to `status_code: 502`, so the worker (which maps 400/404 → not_found,
everything else → error) miscounted these as errors.

These tests pin the fix: the real HTTP status is surfaced, so a 404 reads as
404 (→ not_found downstream) while response-less failures (timeout, connection
drop) still fall back to 502 (→ error, eligible for retry).
"""

from __future__ import annotations

import requests

import core.metadata.similar_artists as sa


def _force_reach_fetch(monkeypatch):
    """Get past the source-chain / provider-availability guard so the test
    exercises the fetch error path, not the 'no providers' branch."""
    monkeypatch.setattr(sa, "_get_source_chain_for_lookup", lambda _opts: ["spotify"])
    monkeypatch.setattr(sa.metadata_registry, "get_client_for_source", lambda _src: object())


def _http_error(status_code):
    """A requests.HTTPError carrying a response with the given status — exactly
    what response.raise_for_status() raises on a 4xx/5xx."""
    resp = requests.Response()
    resp.status_code = status_code
    return requests.exceptions.HTTPError(f"{status_code} Client Error", response=resp)


def test_musicmap_404_surfaced_as_404(monkeypatch):
    """A 404 from MusicMap → status_code 404 (so the worker calls it not_found)."""
    _force_reach_fetch(monkeypatch)

    def _raise_404(_name):
        raise _http_error(404)

    monkeypatch.setattr(sa, "_fetch_musicmap_similar_artist_names", _raise_404)

    result = sa.get_musicmap_similar_artists("Pharooo")
    assert result["success"] is False
    assert result["status_code"] == 404   # was wrongly 502 before the fix


def test_musicmap_timeout_falls_back_to_502(monkeypatch):
    """A response-less failure (timeout) has no status → 502 (stays an error)."""
    _force_reach_fetch(monkeypatch)

    def _raise_timeout(_name):
        raise requests.exceptions.Timeout("timed out")

    monkeypatch.setattr(sa, "_fetch_musicmap_similar_artist_names", _raise_timeout)

    result = sa.get_musicmap_similar_artists("Some Artist")
    assert result["success"] is False
    assert result["status_code"] == 502


def test_musicmap_500_stays_an_error(monkeypatch):
    """A real upstream 5xx is surfaced as-is → not in (400,404) → error/retry."""
    _force_reach_fetch(monkeypatch)

    def _raise_500(_name):
        raise _http_error(500)

    monkeypatch.setattr(sa, "_fetch_musicmap_similar_artist_names", _raise_500)

    result = sa.get_musicmap_similar_artists("Some Artist")
    assert result["success"] is False
    assert result["status_code"] == 500


def test_worker_classifies_404_as_not_found():
    """End-to-end seam: a 404 fetch result → the worker marks 'not_found',
    not 'error' (closing the loop the upstream fix enables)."""
    import core.similar_artists_worker as w

    def fake_fetch(_name, limit=25):
        return {"success": False, "status_code": 404, "error": "no map page"}

    def fake_store(**_kwargs):
        raise AssertionError("must not store anything on a not-found")

    status, count, _detail = w.process_artist("sp1", "Pharooo", fake_fetch, fake_store)
    assert status == "not_found"
    assert count == 0
