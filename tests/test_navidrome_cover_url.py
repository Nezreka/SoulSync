"""Tests for Navidrome cover-art URL building (#766).

The sync editor + modals referenced /api/navidrome/cover/<id> but no route
served it, and the URL behind it had to be a fully-authenticated Subsonic
getCoverArt URL. build_cover_art_url is that builder — these pin its shape and
the not-connected guards (the token/salt are random per call, so we assert
structure + required params rather than an exact string).
"""

from __future__ import annotations

from urllib.parse import parse_qs, urlsplit

from core.navidrome_client import NavidromeClient


def _connected_client():
    c = NavidromeClient()
    c.base_url = "https://nav.example.com"
    c.username = "boulder"
    c.password = "hunter2"
    return c


def test_builds_authenticated_cover_url():
    url = _connected_client().build_cover_art_url("al-123")
    parts = urlsplit(url)
    assert parts.scheme == "https"
    assert parts.netloc == "nav.example.com"
    assert parts.path == "/rest/getCoverArt"
    q = parse_qs(parts.query)
    assert q["id"] == ["al-123"]
    assert q["u"] == ["boulder"]
    # Subsonic token auth: salted md5, never the raw password.
    assert q["t"] and q["t"][0] != "hunter2"
    assert q["s"]            # salt present
    assert "hunter2" not in url
    for required in ("t", "s", "v", "c"):
        assert required in q


def test_cover_url_is_deterministic_so_the_cache_hits():
    # #766 review: the URL must be stable for a given (server, password,
    # cover_id) — otherwise the image cache keys on a rotating salt and misses
    # every request, re-fetching Navidrome each time + leaking dead rows.
    c = _connected_client()
    assert c.build_cover_art_url("al-123") == c.build_cover_art_url("al-123")
    # ...and different covers still produce different URLs.
    assert c.build_cover_art_url("al-123") != c.build_cover_art_url("al-999")


def test_cover_url_changes_with_password():
    # A password change must invalidate the cached URL (new token).
    c1 = _connected_client()
    c2 = _connected_client()
    c2.password = "different"
    assert c1.build_cover_art_url("al-1") != c2.build_cover_art_url("al-1")


def test_size_param_optional():
    assert "size" not in (_connected_client().build_cover_art_url("x") or "")
    assert "size=300" in _connected_client().build_cover_art_url("x", size=300)


def test_cover_id_is_stringified():
    url = _connected_client().build_cover_art_url(12345)
    assert "id=12345" in url


def test_returns_none_when_not_connected():
    c = NavidromeClient()  # base_url is None
    assert c.build_cover_art_url("al-1") is None


def test_returns_none_for_empty_cover_id():
    assert _connected_client().build_cover_art_url("") is None
    assert _connected_client().build_cover_art_url(None) is None


def test_returns_none_without_credentials():
    c = NavidromeClient()
    c.base_url = "https://nav.example.com"  # but no username/password
    assert c.build_cover_art_url("al-1") is None
