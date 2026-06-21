"""Video Import page — frontend wiring (string-contract, like the other video page
tests). Pins the page module, its container, nav registration, and the endpoints it
calls so a refactor can't quietly unhook the manual-import flow. The placement LOGIC
itself is covered by tests/test_video_importer.py + tests/test_video_manual_import.py.
"""

from __future__ import annotations

from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
_JS = (_ROOT / "webui" / "static" / "video" / "video-import.js").read_text(encoding="utf-8")
_SIDE = (_ROOT / "webui" / "static" / "video" / "video-side.js").read_text(encoding="utf-8")
_INDEX = (_ROOT / "webui" / "index.html").read_text(encoding="utf-8")
_CSS = (_ROOT / "webui" / "static" / "video" / "video-side.css").read_text(encoding="utf-8")


def test_module_is_an_isolated_iife():
    s = _JS.strip()
    assert s.startswith("/*") or s.startswith("(function")
    assert "(function" in _JS and "})();" in _JS
    # isolated: wrapped in an IIFE and never ASSIGNS a global (reads like
    # window.confirm are fine). No `window.<name> =` and no `var X` at top level.
    import re
    assert not re.search(r"window\.\w+\s*=", _JS)
    assert "PAGE_ID = 'video-import'" in _JS


def test_page_is_a_real_video_page_not_shared():
    # the nav entry exists and is NO LONGER flagged shared (it's a true video page now)
    assert 'data-video-page="video-import"' in _INDEX
    assert "{ id: 'video-import', label: 'Import' }" in _SIDE
    assert "'video-import', label: 'Import', shared" not in _SIDE


def test_subpage_container_and_script_present():
    assert 'data-video-subpage="video-import"' in _INDEX
    assert "data-vimp-grid" in _INDEX and "data-vimp-empty" in _INDEX
    assert "video/video-import.js" in _INDEX           # script include
    assert ".vimp-card" in _CSS and ".vimp-modal" in _CSS


def test_loads_and_polls_the_failed_queue():
    assert "/api/video/import/failed" in _JS
    assert "soulsync:video-page-shown" in _JS
    assert "setInterval(" in _JS                       # 5s poll while shown


def test_resolve_flow_wired_to_place_and_search():
    # the picker reuses the existing TMDB search, library-owned floated to the top
    assert "/api/video/search?q=" in _JS
    assert "library first" in _JS or "owned" in _JS
    # movie vs episode placement + the place/dismiss endpoints
    assert "scope: r.kind" in _JS
    assert "/api/video/import/' + r.item.id + '/place'" in _JS
    assert "/dismiss'" in _JS


def test_endpoints_registered_on_the_blueprint():
    init = (_ROOT / "api" / "video" / "__init__.py").read_text(encoding="utf-8")
    assert "reg_manual_import(bp)" in init
