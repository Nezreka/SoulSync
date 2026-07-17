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
_WL = (_ROOT / "webui" / "static" / "video" / "video-wishlist.js").read_text(encoding="utf-8")


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
    assert "function sized(url, w)" in _WL
    assert "'/api/video/poster/'" in _WL and "'w=' + w" in _WL
    assert "image.tmdb.org" in _WL and "/t/p/w" in _WL
    # every image render site goes through it — no raw full-size poster_url left
    for site in ("sized(it.poster_url, 342)",        # movie card
                 "sized(pimg(poster), 240)",         # show orb
                 "sized(pimg(posterUrl), 342)",      # season thumb
                 "sized(pimg(poster), 500)",         # expand backdrop
                 "sized(pimg(e.still_url), 342)"):   # episode still
        assert site in _WL, site


def test_no_raw_fullsize_poster_src_remains():
    # a raw `src="' + esc(poster` (unsized) would reintroduce the lag
    assert "src=\"' + esc(it.poster_url)" not in _WL
    assert "src=\"' + esc(pimg(poster))" not in _WL
