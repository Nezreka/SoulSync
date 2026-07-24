"""Inline episode/season grabs carry the show's poster (Boulder, Issue/1.png).

The detail page's auto-grab buttons (single episode + Grab Season) posted
grab payloads with NO poster — every row a season grab queued rendered the
placeholder TV orb on the Downloads page, and the season group header (which
borrows its first row's art) was art-less too. The get-modal and pack paths
always passed poster; the inline paths now use the same `_showPoster()`
resolver the wishlist writes use (library shows → /api/video/poster/show/<id>,
TMDB previews → poster_url).

Source-contract pins: the payload is assembled client-side across two files,
so the contract is pinned where it lives.
"""

from __future__ import annotations

import re
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
_DETAIL = (_ROOT / "webui" / "static" / "video" / "video-detail.js").read_text(encoding="utf-8")
_GRAB = (_ROOT / "webui" / "static" / "video" / "video-grab.js").read_text(encoding="utf-8")


def _fn(src, name):
    start = src.index("function " + name)
    return src[start:start + 900]


def test_single_episode_grab_params_carry_the_poster():
    body = _fn(_DETAIL, "_grabParams")
    assert "poster: _showPoster()" in body


def test_season_grab_carries_the_poster():
    body = _fn(_DETAIL, "grabSeasonInline")
    assert "poster: _showPoster()" in body


def test_grab_module_forwards_poster_to_the_backend():
    # episode(): the grab payload must send poster_url from opts.poster …
    assert re.search(r"poster_url:\s*opts\.poster", _GRAB)
    # … and season() must forward opts.poster into each episode() call.
    body = _fn(_GRAB, "season")
    assert "poster: opts.poster" in body


def test_show_poster_resolver_exists_and_prefers_library_art():
    body = _fn(_DETAIL, "_showPoster")
    assert "/api/video/poster/show/" in body
    assert "poster_url" in body
