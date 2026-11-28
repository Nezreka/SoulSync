"""#2327: native rating logo badges in Overlay Studio.

Covers all 8 providers (IMDb, Rotten Tomatoes critic & audience with Fresh/Rotten
icons, TMDB, Metacritic, Trakt, TVmaze, AniList), horizontal and vertical layouts,
percentage/decimal/numeric score formatting, uniform fixed dimensions, Kometa
logo-pack resolution with built-in fallback marks, and auto-hiding when missing.
"""

from __future__ import annotations

import io
import pytest
from PIL import Image

from core.video.overlays.compositor import render_overlay
from core.video.overlays.fields import format_rating_score
from core.video.overlays.logo_packs import CURATED, SOURCEABLE_FIELDS
from core.video.overlays.logos import rating_logo_ref


def _blank_poster(w=400, h=600) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (w, h), (20, 22, 28)).save(buf, format="JPEG")
    return buf.getvalue()


def _fake_png(color=(255, 0, 128, 255), size=(64, 32)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGBA", size, color).save(buf, format="PNG")
    return buf.getvalue()


def test_rating_logo_ref_covers_all_providers_and_rt_thresholds():
    assert rating_logo_ref("imdb", 8.4) == ("rating", "imdb")
    assert rating_logo_ref("tmdb", 7.8) == ("rating", "tmdb")
    assert rating_logo_ref("metacritic", 81) == ("rating", "metacritic")
    assert rating_logo_ref("trakt", 8.1) == ("rating", "trakt")
    assert rating_logo_ref("tvmaze", 7.9) == ("rating", "tvmaze")
    assert rating_logo_ref("anilist", 85) == ("rating", "anilist")

    # Rotten Tomatoes critic + audience Fresh (>=60) vs Rotten (<60)
    assert rating_logo_ref("rt", 60) == ("rating", "rt_fresh")
    assert rating_logo_ref("rt", 59) == ("rating", "rt_rotten")
    assert rating_logo_ref("rt_critic", 92) == ("rating", "rt_fresh")
    assert rating_logo_ref("rt_audience", 88) == ("rating", "rt_aud_fresh")
    assert rating_logo_ref("rt_audience", 42) == ("rating", "rt_aud_rotten")

    # missing or zero hides cleanly
    assert rating_logo_ref("imdb", None) is None
    assert rating_logo_ref("imdb", 0) is None
    assert rating_logo_ref("rt", "") is None


def test_format_rating_score_modes():
    assert format_rating_score("imdb", 8.43, "auto") == "8.4"
    assert format_rating_score("imdb", 8.4, "percentage") == "84%"
    assert format_rating_score("imdb", 8.4, "numeric") == "8.4"

    assert format_rating_score("rt", 92, "auto") == "92%"
    assert format_rating_score("rt", 92, "decimal") == "9.2"
    assert format_rating_score("rt", 92, "numeric") == "92"

    assert format_rating_score("metacritic", 81, "auto") == "81"
    assert format_rating_score("metacritic", 81, "percentage") == "81%"
    assert format_rating_score("metacritic", 81, "decimal") == "8.1"

    assert format_rating_score("anilist", 86, "auto") == "86%"
    assert format_rating_score("rt_audience", 77, "auto") == "77%"
    assert format_rating_score("imdb", None, "auto") is None


def test_kometa_logo_pack_includes_all_rating_provider_art():
    assert "rating" in CURATED
    assert "rating" in SOURCEABLE_FIELDS
    expected = {
        "imdb", "tmdb", "metacritic", "trakt", "tvmaze", "anilist",
        "rt_fresh", "rt_rotten", "rt_aud_fresh", "rt_aud_rotten",
    }
    assert expected.issubset(set(CURATED["rating"].keys()))


@pytest.mark.parametrize("layout", ["horizontal", "vertical"])
@pytest.mark.parametrize("provider,val", [
    ("imdb", 8.6),
    ("rt", 94),
    ("rt", 38),
    ("rt_audience", 88),
    ("rt_audience", 41),
    ("tmdb", 8.1),
    ("metacritic", 82),
    ("trakt", 8.3),
    ("tvmaze", 8.0),
    ("anilist", 87),
])
def test_render_rating_badge_all_providers_and_layouts(layout, provider, val):
    base = _blank_poster()
    defn = {
        "layers": [{
            "id": "r1",
            "type": "rating_badge",
            "provider": provider,
            "layout": layout,
            "format": "auto",
            "x": 0.1,
            "y": 0.85,
            "anchor": "bottom-left",
            "size": 0.04,
            "bg": {
                "enabled": True,
                "color": "#11141a",
                "opacity": 0.85,
                "radius": 0.015,
                "padX": 0.02,
                "padY": 0.012,
                "w": 0.24,
                "h": 0.09,
            },
        }],
    }
    out = render_overlay(base, defn, {provider: val})
    assert out != base
    img = Image.open(io.BytesIO(out))
    assert img.size == (400, 600)


def test_rating_badge_uses_pack_logo_when_installed_and_hides_when_missing():
    base = _blank_poster()
    seen = []

    def loader(pack, name):
        seen.append((pack, name))
        return _fake_png()

    defn = {
        "layers": [{
            "id": "r1",
            "type": "rating_badge",
            "provider": "rt_audience",
            "layout": "horizontal",
            "x": 0.5,
            "y": 0.5,
            "anchor": "center",
        }],
    }
    rendered = render_overlay(base, defn, {"rt_audience": 45}, logo_loader=loader)
    assert seen == [("rating", "rt_aud_rotten")]
    assert rendered != base

    # missing rating auto-hides: poster pixels remain identical to base
    seen.clear()
    empty = render_overlay(base, defn, {}, logo_loader=loader)
    assert seen == []
    im_base = Image.open(io.BytesIO(base)).convert("RGB")
    im_empty = Image.open(io.BytesIO(empty)).convert("RGB")
    assert list(im_base.getdata()) == list(im_empty.getdata())
