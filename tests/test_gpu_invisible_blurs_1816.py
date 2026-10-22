"""Blurs nobody can see, kept out of the hot paths (#1816).

A user's GPU sat at 100% while a sync or wishlist download ran. The download
modal stacked four backdrop-filters, and the browser redoes every one of them
on every frame anything animates (the row status pulses tick the whole time).
Three of the four sat on surfaces 97-99% opaque or on the modal's own flat
fill, so they never showed: a pixel diff with and without them is identical.
The wishlist hero had the same problem on the flat page background.

Measured in Chromium: per-frame cost in the open modal 182ms -> 82ms, and the
wishlist page 50ms -> 30ms. The overlay behind the modal keeps its blur, that
one is the frosted look you actually see.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STYLE = (ROOT / 'webui' / 'static' / 'style.css').read_text(encoding='utf-8')
PREMIER = (ROOT / 'webui' / 'static' / 'wishlist-premier.css').read_text(encoding='utf-8')


def _rules(css: str):
    """(selector list, body) for every plain rule, @media insides included."""
    css = re.sub(r'/\*.*?\*/', '', css, flags=re.S)
    for match in re.finditer(r'([^{}]+)\{([^{}]*)\}', css):
        selectors = [s.strip() for s in match.group(1).split(',')]
        yield selectors, match.group(2)


def _blurs(css: str, selector: str) -> list[str]:
    return [body.strip()[:80] for sels, body in _rules(css)
            if selector in sels and 'backdrop-filter' in body]


def test_download_modal_surface_has_no_blur():
    assert _blurs(STYLE, '.download-missing-modal-content') == []


def test_download_tracks_section_has_no_blur():
    assert _blurs(STYLE, '.download-tracks-section') == []


def test_download_modal_footer_has_no_blur():
    assert _blurs(STYLE, '.download-missing-modal-footer') == []


def test_wishlist_hero_has_no_blur():
    assert _blurs(PREMIER, '.wlp-hero') == []


def test_the_visible_overlay_blurs_stay():
    # the frosted look behind the modal is real, removing it would change the look
    assert _blurs(STYLE, '#download-missing-modal')
    assert _blurs(STYLE, '.download-missing-modal')
