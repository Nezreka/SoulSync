"""Regression: album consistency must PIN one MusicBrainz release per album.

User report (Maitresinh / 5BILLION, Meshuggah "Catch Thirtythree"): an album
downloaded from a single source split into 3 albums in Navidrome. The logs show
``album_consistency`` ran on the album across multiple album-completeness cycles
and ``_find_best_release`` picked a DIFFERENT release each run (becf5f05 one day,
b01e730d the next — the album has several close-scoring MB reissues and MB API
timeouts vary the candidate set). Because consistency re-tags on every run,
tracks got different ``MUSICBRAINZ_ALBUMID`` values across runs → Navidrome split.

The fix pins the release via the persistent album->release-MBID cache (the same
store per-track enrichment uses) so re-runs reuse the same release. These tests
pin that behavior.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

import core.album_consistency as ac


@pytest.fixture()
def fake_cache(monkeypatch):
    """Dict-backed stand-in for the persistent album_mbid_cache."""
    store = {}
    monkeypatch.setattr("core.metadata.album_mbid_cache.lookup",
                        lambda a, ar: store.get((a, ar)))

    def _record(a, ar, mbid):
        store[(a, ar)] = mbid
        return True

    monkeypatch.setattr("core.metadata.album_mbid_cache.record", _record)
    return store


def _mb(get_release=None):
    def _default(mid, includes=None):
        return {"id": mid, "title": "X"}
    return SimpleNamespace(mb_client=SimpleNamespace(get_release=get_release or _default))


def test_release_is_pinned_across_runs(monkeypatch, fake_cache):
    # _find_best_release WOULD flip between two releases on successive runs.
    picks = iter([{"id": "REL-A", "title": "X"}, {"id": "REL-B", "title": "X"}])
    monkeypatch.setattr(ac, "_find_best_release", lambda *a, **k: next(picks))

    mb = _mb()
    r1 = ac._resolve_album_release("Catch Thirtythree", "Meshuggah", 13, mb)
    r2 = ac._resolve_album_release("Catch Thirtythree", "Meshuggah", 13, mb)

    assert r1["id"] == "REL-A"          # first run scores + pins REL-A
    assert r2["id"] == "REL-A"          # second run REUSES the pin, not REL-B
    assert fake_cache[("catch thirtythree", "meshuggah")] == "REL-A"


def test_first_resolution_is_recorded(monkeypatch, fake_cache):
    monkeypatch.setattr(ac, "_find_best_release", lambda *a, **k: {"id": "REL-X"})
    ac._resolve_album_release("Some Album", "Some Artist", 10, _mb())
    assert fake_cache[("some album", "some artist")] == "REL-X"


def test_edition_parentheticals_share_a_pin(monkeypatch, fake_cache):
    # "Album (Deluxe Edition)" and "Album" normalize to the same cache key, so a
    # deluxe re-tag reuses the standard's pinned release.
    picks = iter([{"id": "REL-A"}, {"id": "REL-B"}])
    monkeypatch.setattr(ac, "_find_best_release", lambda *a, **k: next(picks))
    r1 = ac._resolve_album_release("Nevermind", "Nirvana", 12, _mb())
    r2 = ac._resolve_album_release("Nevermind (Deluxe Edition)", "Nirvana", 20, _mb())
    assert r1["id"] == "REL-A" and r2["id"] == "REL-A"


def test_cache_unavailable_falls_back_to_search(monkeypatch):
    # lookup always misses + record no-ops → behaves exactly like today (each run
    # searches independently). Proves the fix degrades safely.
    monkeypatch.setattr("core.metadata.album_mbid_cache.lookup", lambda a, ar: None)
    monkeypatch.setattr("core.metadata.album_mbid_cache.record", lambda a, ar, m: False)
    n = {"i": 0}

    def _fbr(*a, **k):
        n["i"] += 1
        return {"id": "REL-%d" % n["i"]}

    monkeypatch.setattr(ac, "_find_best_release", _fbr)
    r1 = ac._resolve_album_release("A", "B", 5, _mb())
    r2 = ac._resolve_album_release("A", "B", 5, _mb())
    assert r1["id"] == "REL-1" and r2["id"] == "REL-2"
    assert n["i"] == 2


def test_pinned_fetch_failure_falls_back_to_search(monkeypatch, fake_cache):
    fake_cache[("catch thirtythree", "meshuggah")] = "PINNED"

    def _boom(mid, includes=None):
        raise RuntimeError("MB unavailable")

    monkeypatch.setattr(ac, "_find_best_release", lambda *a, **k: {"id": "FALLBACK"})
    r = ac._resolve_album_release("Catch Thirtythree", "Meshuggah", 13, _mb(get_release=_boom))
    assert r["id"] == "FALLBACK"   # bad pin → re-search rather than crash


def test_no_release_found_records_nothing(monkeypatch, fake_cache):
    monkeypatch.setattr(ac, "_find_best_release", lambda *a, **k: None)
    r = ac._resolve_album_release("Ghost Album", "Nobody", 8, _mb())
    assert r is None
    assert ("ghost album", "nobody") not in fake_cache
