"""Video automation BUILDER wiring — the isolated video page must be able to
create/edit its own automations without hijacking the music page's builder.

These pin the wiring contract that makes that work:
- the video automations subpage hosts its OWN builder DOM (vauto- prefixed ids)
  + a "New Automation" button, swapping list/builder like the music page;
- the shared builder (stats-automations.js) is context-aware: a video context
  points at the video ids + the video-scoped blocks endpoint + owned_by='video',
  and the card cog routes to the video builder when the video side is active;
- a save tags the row owned_by='video' so it stays off the music page;
- a generic config_fields renderer drives video block config (so the video
  action's fields show up) and is gated to the video context (music untouched).

String-contract level (like tests/test_video_side_shell.py) so a refactor that
silently breaks the coupling fails here.
"""

from __future__ import annotations

from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
_INDEX = (_ROOT / "webui" / "index.html").read_text(encoding="utf-8", errors="replace")
_STATS = (_ROOT / "webui" / "static" / "stats-automations.js").read_text(encoding="utf-8")
_VAUTO = (_ROOT / "webui" / "static" / "video" / "video-automations.js").read_text(encoding="utf-8")


# --- the video subpage builder DOM ----------------------------------------

def test_video_subpage_has_new_automation_button():
    # Header button + empty-state button both open the VIDEO builder.
    assert _INDEX.count('onclick="showVideoAutomationBuilder()"') >= 1
    assert 'auto-new-btn' in _INDEX


def test_video_subpage_has_list_and_builder_views():
    for tok in ('id="vauto-list-view"', 'id="vauto-builder-view"'):
        assert tok in _INDEX, tok


def test_video_builder_has_prefixed_ids():
    for tok in (
        'id="vauto-builder-name"', 'id="vauto-builder-group-name"',
        'id="vauto-builder-group-list"', 'id="vauto-builder-sidebar"',
        'id="vauto-builder-canvas"',
    ):
        assert tok in _INDEX, tok


def test_video_builder_buttons_use_shared_handlers():
    # Save / Cancel / Back reuse the SAME shared functions (ctx-driven).
    assert 'onclick="saveAutomation()"' in _INDEX
    assert 'onclick="hideAutomationBuilder()"' in _INDEX


# --- the shared builder is context-aware ----------------------------------

def test_shared_builder_has_video_entry_point():
    assert 'function showVideoAutomationBuilder(' in _STATS
    assert 'function showAutomationBuilder(' in _STATS
    assert 'function _openAutomationBuilder(' in _STATS


def test_video_context_targets_video_ids_and_endpoint():
    assert "'/api/video/automations/blocks'" in _STATS
    assert "ownedBy: 'video'" in _STATS
    # The video context must reference the vauto- prefixed element ids.
    for tok in ('vauto-builder-name', 'vauto-builder-sidebar', 'vauto-builder-canvas',
                'vauto-list-view', 'vauto-builder-view'):
        assert tok in _STATS, tok


def test_card_cog_routes_by_active_side():
    # Cards call editAutomation (not showAutomationBuilder directly) so a video
    # card opens the video builder instead of the hidden music one.
    assert 'editAutomation(${a.id})' in _STATS
    assert 'function editAutomation(' in _STATS
    assert "getAttribute('data-side') === 'video'" in _STATS


def test_save_tags_owned_by_from_context():
    assert 'body.owned_by = _autoBuilderCtx.ownedBy' in _STATS


def test_open_clears_both_builders_to_avoid_id_collision():
    # cfg-* ids exist in both builders; opening clears both canvases/sidebars.
    assert "'vauto-builder-sidebar', 'vauto-builder-canvas'" in _STATS


def test_generic_config_renderer_is_video_gated():
    # The generic config_fields renderer/reader must only run in the video
    # context so the music side keeps its bespoke renderers (byte-identical).
    assert 'function _renderGenericConfigField(' in _STATS
    assert 'function _readGenericConfigField(' in _STATS
    assert 'if (_autoBuilderCtx.ownedBy) {' in _STATS


# --- the video page exposes its reload hook -------------------------------

def test_video_page_exposes_reload_hook():
    assert 'window._reloadVideoAutomations = load' in _VAUTO


# --- the System list is shown in a logical pipeline order -----------------

def test_video_system_automations_are_sorted_by_pipeline_order():
    # The API returns newest-created-first (jumbled); the page re-sorts by an
    # explicit order so it reads scans → processors → library → maintenance.
    assert 'sortSystem(all.filter(isVideoAutomation))' in _VAUTO
    assert '_SYS_ORDER' in _VAUTO
    # the order must put the watchlist SCANS before the wishlist PROCESSORS
    scan = _VAUTO.index("'video_scan_watchlist_people'")
    proc = _VAUTO.index("'video_process_movie_wishlist'")
    maint = _VAUTO.index("'video_backup_database'")
    assert scan < proc < maint
