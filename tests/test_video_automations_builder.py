"""Video automation BUILDER wiring — the isolated video page must be able to
create/edit its own automations without hijacking the music page's builder.

These pin the wiring contract that makes that work:
- the video automations subpage hosts its OWN builder DOM (vauto- prefixed ids);
  the list beside it is the React page now (/automations?media=video), which
  shows this subpage for the length of an edit;
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
_VSIDE = (_ROOT / "webui" / "static" / "video" / "video-side.js").read_text(encoding="utf-8")
_VCSS = (_ROOT / "webui" / "static" / "video" / "video-side.css").read_text(encoding="utf-8")
_INIT = (_ROOT / "webui" / "static" / "init.js").read_text(encoding="utf-8")
_REACT = _ROOT / "webui" / "src" / "routes" / "automations"
_BUILDER_TS = (_REACT / "-automations.builder.ts").read_text(encoding="utf-8")
_HELPERS_TS = (_REACT / "-automations.helpers.ts").read_text(encoding="utf-8")


# --- the video subpage builder DOM ----------------------------------------

def test_video_subpage_holds_only_the_builder():
    # the vanilla list (header, stats, cards, empty state) is the React page
    # now. what stays is the builder, and the node it swaps itself with.
    sub = _INDEX.split('data-video-subpage="video-automations"', 1)[1].split('</section>', 1)[0]
    assert 'id="vauto-builder-view"' in sub
    assert 'data-vauto-list' not in sub and 'data-vauto-stats' not in sub
    assert 'showVideoAutomationBuilder()' not in sub


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
    # CONTRACT FLIPPED (39a688a96): the generic config_fields renderer/reader
    # now serves BOTH sides — music's bespoke branches all return early, so
    # the generic path only catches blocks that previously rendered nothing
    # (monthly_time, full_refresh, search_and_download). The un-gating is
    # pinned from the other side in tests/automation/test_builder_bic.py.
    assert 'function _renderGenericConfigField(' in _STATS
    assert 'function _readGenericConfigField(' in _STATS
    assert 'if (_autoBuilderCtx.ownedBy) {' not in _STATS


# --- the page is the React automations page on ?media=video ----------------

def test_video_automations_are_handed_off_to_the_react_route():
    assert "'video-automations': 1" in _VSIDE              # HANDED_OFF: no url re-assert
    assert "function gotoUnifiedAutomations" in _VSIDE
    assert "if (pageId === 'video-automations') { gotoUnifiedAutomations(mode); return; }" in _VSIDE
    assert "wsr.navigateToPage('automations', { query: { media: 'video' }" in _VSIDE
    # a reload or Back onto /automations?media=video stays on the video side
    assert "if (path === '/automations') return media === 'video' ? 'video-automations' : null;" in _VSIDE


def test_video_automations_stay_admin_only():
    # the sidebar entry is admin-only, and so is the url
    assert "automations: { media: ['video'], videoPage: 'video-automations', adminOnly: true }" in _INIT
    assert "if (view.adminOnly) return" in _INIT


def test_the_builder_takes_the_screen_back_from_the_react_page():
    # the React root shows for the page, except while the builder is open: the
    # React page puts vauto-building on the body and the video host shows again
    assert ('body[data-side="video"][data-video-page="video-automations"]:not(.vauto-building) '
            '#webui-react-root') in _VCSS
    assert ('body[data-side="video"][data-video-page="video-automations"]:not(.vauto-building) '
            '#video-page-host') in _VCSS
    assert "VIDEO_BUILDING_CLASS = 'vauto-building'" in _BUILDER_TS
    # a builder left open by leaving mid-edit must not greet the next visit
    assert "document.body.classList.remove('vauto-building');" in _VSIDE


def test_a_save_or_a_deploy_tells_the_react_page():
    # the list is React's; the vanilla builder and hub only announce a change
    assert _STATS.count("new CustomEvent('soulsync:automations-changed')") >= 2
    page = (_REACT / "-ui" / "automations-page.tsx").read_text(encoding="utf-8")
    assert "'soulsync:automations-changed'" in page


def test_the_video_side_shows_only_video_owned_rows():
    assert "return automations.filter((a) => a.owned_by === 'video');" in _HELPERS_TS


def test_every_video_automation_has_a_friendly_label_and_icon():
    # regression: a video action missing from the label/icon maps falls back to the gear +
    # raw action_type ("⚙️ video_clean_search_history") — inconsistent. Every seeded video
    # automation must have an entry in BOTH maps in stats-automations.js.
    import core.automation_engine as ae
    types = {a.get("action_type") for a in ae.SYSTEM_AUTOMATIONS if a.get("owned_by") == "video"}
    missing = [t for t in types if _STATS.count(t + ":") < 2]   # icon map + label map
    assert not missing, "video actions missing a label/icon: " + ", ".join(sorted(missing))


def test_hourly_db_update_is_a_scheduled_safety_net():
    # second trigger (a schedule) for the same incremental DB read, so manual library adds
    # show up within the hour instead of waiting for the weekly deep scan.
    import core.automation_engine as ae
    row = next((a for a in ae.SYSTEM_AUTOMATIONS if a.get("action_type") == "video_update_database_hourly"), None)
    assert row is not None
    assert row["trigger_type"] == "schedule" and row["trigger_config"]["unit"] == "hours"
    assert row["action_config"]["mode"] == "incremental"     # cheap read, not a deep scan


