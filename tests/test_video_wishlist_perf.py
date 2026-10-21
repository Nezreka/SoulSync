"""Video wishlist performance: right-sized poster art.

The orbs render at ~110px but the raw poster_urls are full-size (1000px+).
28 shows × full-res bitmaps was the wishlist lag Boulder hit. The frontend
now routes every image through sized(): ?w= on the poster proxy, a small
/t/p/wNNN/ bucket for TMDB urls. The proxy's own resize is pinned here too.
"""

from __future__ import annotations

from pathlib import Path

from api.video.poster import _tmdb_resize

_ROOT = Path(__file__).resolve().parent.parent
# the page is React now (webui/src/routes/wishlist); sized() is the vanilla
# helper, ported, and checked against the frozen original by the differential test.
_WLD = _ROOT / "webui" / "src" / "routes" / "wishlist"
_HELPERS = (_WLD / "-video.helpers.ts").read_text(encoding="utf-8")
_PAGE = (_WLD / "-ui" / "video-wishlist.tsx").read_text(encoding="utf-8")
_SHEETS = (_WLD / "-ui" / "video-sheets.tsx").read_text(encoding="utf-8")
_WL = _PAGE + _SHEETS


def test_tmdb_resize_rewrites_to_a_small_bucket():
    url = "https://image.tmdb.org/t/p/original/abc.jpg"
    assert _tmdb_resize(url, 240, backdrop=False) == "https://image.tmdb.org/t/p/w342/abc.jpg"
    assert _tmdb_resize(url, 150, backdrop=False) == "https://image.tmdb.org/t/p/w185/abc.jpg"
    # backdrops use the wide buckets
    assert _tmdb_resize(url, 500, backdrop=True) == "https://image.tmdb.org/t/p/w780/abc.jpg"
    # a non-tmdb / non-matching url is left alone
    assert _tmdb_resize("https://x/y.jpg", 240, backdrop=False) == "https://x/y.jpg"


def test_wishlist_has_a_sizer_and_uses_it_everywhere():
    # the helper exists and handles both the proxy (?w=) and tmdb (bucket) forms
    assert "export function sized<" in _HELPERS
    assert "'/api/video/poster/'" in _HELPERS and "w=${w}" in _HELPERS
    assert "image.tmdb.org" in _HELPERS and "/t/p/w${b}/" in _HELPERS
    # every image render site goes through it — no raw full-size poster_url left
    for site in ("sized(m.poster_url || '', 342)",      # movie poster
                 "sized(img(poster) || '', 240)",       # show / channel orb
                 "sized(img(u), 185)",                  # the orb's season ring
                 "sized(movie.poster_url || '', 342)",  # movie panel (and its backdrop)
                 "sized(img(poster) || '', 342)",       # show panel (and its backdrop)
                 "sized(img(posterUrl), 342)",          # season cover
                 "sized(img(ep.still_url), 342)"):      # episode still
        assert site in _WL, site


def test_no_raw_fullsize_poster_src_remains():
    # a raw poster_url / still_url straight into an image would reintroduce the lag
    import re
    assert not re.search(r"(src|image)=\{[a-zA-Z.]*(poster_url|still_url)\}", _WL)
