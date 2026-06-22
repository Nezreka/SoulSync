"""Special-edition cover art: prefer the pinned release's OWN cover over the
release-group representative.

A MusicBrainz release-group 'front' on the Cover Art Archive is a single
representative cover (usually the standard edition), so a special edition (e.g.
"Gustave Edition") was getting the standard art. The download/embed art paths now
try the specific release's own cover first and fall back to the group/provider
URL only when the release has none — so coverage never regresses.
"""

from __future__ import annotations

from core.metadata.caa_art import caa_front_url, fetch_release_preferred_art


def test_caa_front_url_scopes_and_size():
    assert caa_front_url("abc", "release") == "https://coverartarchive.org/release/abc/front-1200"
    assert caa_front_url("rg", "release-group") == "https://coverartarchive.org/release-group/rg/front-1200"
    assert caa_front_url("abc", "release", size=250).endswith("/front-250")
    assert caa_front_url("abc", "release", size=0).endswith("/abc/front")
    assert caa_front_url("", "release") is None
    assert caa_front_url(None) is None
    # unknown scope coerces to release
    assert "/release/x/" in caa_front_url("x", "bogus")


def _fetcher(table):
    """table: {url: bytes|None}. Returns (bytes, mime) or (None, None)."""
    calls = []
    def fetch(url):
        calls.append(url)
        data = table.get(url)
        return (data, "image/jpeg") if data else (None, None)
    fetch.calls = calls
    return fetch


def test_prefers_release_specific_art_over_fallback():
    rel = "https://coverartarchive.org/release/REL/front-1200"
    fb = "https://provider.example/standard.jpg"
    fetch = _fetcher({rel: b"X" * 5000, fb: b"Y" * 5000})
    data, mime, used = fetch_release_preferred_art("REL", fb, fetch_fn=fetch)
    assert data == b"X" * 5000 and used == rel
    assert fetch.calls[0] == rel        # release tried FIRST


def test_falls_back_when_release_has_no_own_art():
    rel = "https://coverartarchive.org/release/REL/front-1200"
    fb = "https://provider.example/standard.jpg"
    fetch = _fetcher({rel: None, fb: b"Y" * 5000})   # release 404s
    data, _, used = fetch_release_preferred_art("REL", fb, fetch_fn=fetch)
    assert data == b"Y" * 5000 and used == fb        # never regresses: keeps the old cover
    assert fetch.calls == [rel, fb]                   # tried release, then fell back


def test_no_release_mbid_uses_fallback_directly():
    fb = "https://provider.example/standard.jpg"
    fetch = _fetcher({fb: b"Y" * 5000})
    data, _, used = fetch_release_preferred_art(None, fb, fetch_fn=fetch)
    assert data and used == fb
    assert fetch.calls == [fb]                         # no wasted release lookup


def test_tiny_image_is_treated_as_a_miss():
    rel = "https://coverartarchive.org/release/REL/front-1200"
    fb = "https://provider.example/standard.jpg"
    fetch = _fetcher({rel: b"tiny", fb: b"Y" * 5000})  # release art under min_bytes
    data, _, used = fetch_release_preferred_art("REL", fb, fetch_fn=fetch, min_bytes=1000)
    assert used == fb


def test_nothing_available_returns_none():
    fetch = _fetcher({})
    assert fetch_release_preferred_art("REL", None, fetch_fn=fetch) == (None, None, None)
    assert fetch_release_preferred_art(None, None, fetch_fn=fetch) == (None, None, None)


def test_fetch_exception_is_treated_as_miss_not_fatal():
    fb = "https://provider.example/standard.jpg"
    def fetch(url):
        if "release" in url:
            raise RuntimeError("network boom")
        return b"Y" * 5000, "image/jpeg"
    data, _, used = fetch_release_preferred_art("REL", fb, fetch_fn=fetch)
    assert data == b"Y" * 5000 and used == fb          # exception on release → fell back safely
