"""Video Prowlarr search adapter: projects shared ProwlarrClient results into the video hit
shape, filters by protocol (torrent vs usenet), carries the download URL for the grab, and
picks movie/TV categories by scope. Pure — the client is injected."""

from __future__ import annotations

import core.video.prowlarr_search as ps


class _R:
    """Minimal stand-in for ProwlarrSearchResult (only the attrs the adapter reads)."""
    def __init__(self, **kw):
        self.__dict__.update(kw)


def _results():
    return [
        _R(title="The Movie 2020 1080p BluRay x264-GRP", size=8 * 1024 ** 3, seeders=42,
           leechers=3, grabs=100, indexer_name="RARBG", indexer_id=1, protocol="torrent",
           magnet_uri="magnet:?xt=urn:1", download_url=None, guid="g1"),
        _R(title="The Movie 2020 1080p WEB-DL", size=6 * 1024 ** 3, seeders=None, leechers=None,
           grabs=50, indexer_name="NZBgeek", indexer_id=2, protocol="usenet",
           magnet_uri=None, download_url="http://nzb/get/1", guid="g2"),
        _R(title="The Movie no url", size=1, seeders=1, leechers=0, grabs=0, indexer_name="x",
           indexer_id=3, protocol="torrent", magnet_uri=None, download_url=None, guid="g3"),
    ]


def _patch_client(monkeypatch, configured=True):
    captured = {}

    class _Client:
        def is_configured(self):
            return configured

        def _search_sync(self, q, cats, ids, limit):
            captured["query"], captured["cats"] = q, cats
            return _results()

    monkeypatch.setattr(ps, "_client", lambda: _Client())
    monkeypatch.setattr(ps, "_indexer_ids", lambda: [])
    return captured


def test_torrent_search_keeps_only_torrent_hits_with_a_url(monkeypatch):
    cap = _patch_client(monkeypatch)
    out = ps.prowlarr_search("movie", "The Movie", year=2020, source="torrent")
    assert out["configured"] and len(out["hits"]) == 1          # url-less torrent dropped, usenet filtered out
    h = out["hits"][0]
    assert h["download_url"] == "magnet:?xt=urn:1" and h["protocol"] == "torrent"
    assert h["seeders"] == 42 and h["username"] == "RARBG" and h["availability"] == 42
    assert cap["query"] == "The Movie 2020" and 2000 in cap["cats"]   # movie categories, year query


def test_usenet_search_keeps_only_usenet_and_falls_back_to_grabs(monkeypatch):
    _patch_client(monkeypatch)
    out = ps.prowlarr_search("movie", "The Movie", year=2020, source="usenet")
    assert len(out["hits"]) == 1
    h = out["hits"][0]
    assert h["download_url"] == "http://nzb/get/1" and h["protocol"] == "usenet"
    assert h["seeders"] is None and h["availability"] == 50            # no seeders → grabs


def test_tv_scope_uses_tv_categories(monkeypatch):
    cap = _patch_client(monkeypatch)
    ps.prowlarr_search("episode", "The Show", season=1, episode=2, source="torrent")
    assert 5000 in cap["cats"] and 2000 not in cap["cats"]
    assert cap["query"] == "The Show S01E02"


def test_unconfigured_returns_not_configured(monkeypatch):
    _patch_client(monkeypatch, configured=False)
    out = ps.prowlarr_search("movie", "X", source="torrent")
    assert out["configured"] is False and out["hits"] == []
