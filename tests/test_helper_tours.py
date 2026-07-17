"""Guided-tour integrity (Kazimir: red dot never clears, boxes detach and
'live in a corner').

Root causes fixed: 28 of 83 tour selectors had rotted through page
redesigns (dead anchors → the popover fell back to nowhere), the anchor
resolve was a single fixed 350ms wait (React pages mount later), and the
helper button's What's New dot only cleared through one specific panel.

This file is the net that keeps tours honest: EVERY tour selector must
resolve to something some renderer actually creates — index.html, a
static JS renderer, or the React source. A redesign that removes an
anchored element now fails here instead of silently stranding the tour.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
_HELPER = (_ROOT / 'webui' / 'static' / 'helper.js').read_text(encoding='utf-8', errors='replace')
_INDEX = (_ROOT / 'webui' / 'index.html').read_text(encoding='utf-8', errors='replace')


def _tour_block() -> str:
    start = _HELPER.index('const HELPER_TOURS = {')
    i = _HELPER.index('{', start)
    depth = 0
    for j in range(i, len(_HELPER)):
        if _HELPER[j] == '{':
            depth += 1
        elif _HELPER[j] == '}':
            depth -= 1
            if depth == 0:
                return _HELPER[i:j + 1]
    raise AssertionError('HELPER_TOURS block not closed')


def _all_frontend_source() -> str:
    """Everything that can create DOM: the shell, the classic JS, the React
    source (dist is built FROM src, so src is the truth)."""
    chunks = [_INDEX]
    for p in (_ROOT / 'webui' / 'static').rglob('*.js'):
        if 'dist' in p.parts or p.name == 'helper.js':
            continue
        chunks.append(p.read_text(encoding='utf-8', errors='replace'))
    src = _ROOT / 'webui' / 'src'
    if src.is_dir():
        for p in src.rglob('*'):
            if p.suffix in ('.tsx', '.ts', '.jsx', '.js', '.html'):
                chunks.append(p.read_text(encoding='utf-8', errors='replace'))
    return '\n'.join(chunks)


_SELECTORS = sorted(set(re.findall(r"selector:\s*'([^']+)'", _tour_block())))
_SOURCE = _all_frontend_source()


def test_tours_have_selectors():
    assert len(_SELECTORS) > 50     # the tours are substantial; a parse break would zero this


@pytest.mark.parametrize('selector', _SELECTORS)
def test_every_tour_selector_has_a_renderer(selector):
    """Each simple token of the selector must exist somewhere a renderer
    writes it. This is intentionally string-level: it catches deletions and
    renames (the rot), not runtime visibility."""
    checks = []
    for kind, name in re.findall(r'([#.])([\w-]+)', selector):
        if kind == '#':
            checks.append(f'id="{name}"' in _SOURCE or f"id='{name}'" in _SOURCE
                          or f"'{name}'" in _SOURCE or f'"{name}"' in _SOURCE)
        else:
            checks.append(name in _SOURCE)
    for value in re.findall(r'\[data-tab="([^"]+)"\]', selector):
        checks.append(f'data-tab="{value}"' in _SOURCE or f"'{value}'" in _SOURCE)
    assert checks and all(checks), f'tour anchor has no renderer: {selector}'


def _page_sections():
    """Split index.html into the shell (sidebar/header/modals) and per-page
    content. Approximate but directional: a page's slice runs to the next
    page div."""
    parts = re.split(r'<div class="page(?:\s[^"]*)?" id="([\w-]+)-page">', _INDEX)
    shell = parts[0]
    pages = {}
    for k in range(1, len(parts) - 1, 2):
        pages[parts[k]] = parts[k + 1]
    return shell, pages


_STEPS = re.findall(r"page:\s*'([\w-]+)',\s*selector:\s*'([^']+)'", _tour_block())


def test_steps_anchor_to_their_own_page_or_the_shell():
    """Boulder's report: the dashboard tour talked about tool cards that MOVED
    to another page — the ids still existed, so the renderer check passed,
    but the elements were invisible on the tour's page and every step showed
    the centered fallback. A static #id anchor that index.html renders must
    live inside the step's own page section or the shell."""
    shell, pages = _page_sections()
    offenders = []
    for page, selector in _STEPS:
        m = re.match(r'^#([\w-]+)$', selector)
        if not m:
            continue
        needle = f'id="{m.group(1)}"'
        if needle not in _INDEX:
            continue        # dynamically rendered (React/JS) — renderer test covers it
        if needle not in shell and needle not in pages.get(page, ''):
            offenders.append(f'{page}: {selector}')
    assert not offenders, f'tour anchors living on the WRONG page: {offenders}'


def test_dashboard_tour_matches_the_current_dashboard():
    # the sections Boulder enumerated, in walk order
    block = _tour_block()
    dash = block.split("'dashboard': {")[1].split("'first-download': {")[0]
    for anchor in ('.dashboard-header', '.header-actions', '#watchlist-button',
                   '#wishlist-button', '.service-status-grid', '.stats-grid-dashboard',
                   '#library-status-card', '#sync-history-cards', '.dash-card--quick-actions',
                   '#dashboard-activity-feed', '#enrichment-pills-section',
                   '.side-toggle', '#profile-indicator', '.version-button'):
        assert anchor in dash, f'dashboard tour lost its {anchor} step'
    # the pre-redesign tool cards are gone from the dashboard tour
    for stale in ('#db-updater-card', '#metadata-updater-card', '#duplicate-cleaner-card',
                  '#backup-manager-card', '#metadata-cache-card', '#media-scan-card',
                  '#discovery-pool-card'):
        assert stale not in dash, f'dashboard tour still anchors to the moved {stale}'


# ── engine contracts ──────────────────────────────────────────────────────────

def test_anchor_resolution_retries_instead_of_one_fixed_wait():
    assert 'function _resolveTourTarget' in _HELPER
    assert '_resolveTourTarget(step.selector' in _HELPER
    # visibility gate: a hidden element is NOT an anchor
    assert 'offsetParent !== null' in _HELPER
    # the old blind render-after-350ms path is gone
    assert '_renderTourStep(tour, step), 350' not in _HELPER


def test_popover_reanchors_on_resize_and_cleans_up():
    assert '_tourRepositionHandler' in _HELPER
    assert "window.addEventListener('resize', _tourRepositionHandler)" in _HELPER
    assert '_removeTourReposition()' in _HELPER.split('function removeTourOverlay')[1][:300]


def test_whats_new_dot_clears_from_the_version_modal_too():
    js = (_ROOT / 'webui' / 'static' / 'downloads.js').read_text(encoding='utf-8', errors='replace')
    body = js.split('async function showVersionInfo')[1]
    assert 'soulsync_helper_version_seen' in body


def test_the_dead_selectors_stay_dead():
    # the six anchors the redesigns orphaned — never reference them again
    block = _tour_block()
    for dead in ('#enh-results-container', '#retag-tool-card', '.import-page-header',
                 '.import-page-refresh-btn', '.import-page-staging-bar', '.import-page-tab-bar'):
        assert dead not in block, f'{dead} is a dead anchor'
