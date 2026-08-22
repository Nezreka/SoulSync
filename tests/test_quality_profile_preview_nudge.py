"""The profile shown in Settings -> Quality is the save target.

Historically a non-default row was only a preview: autosave and the prominent
"Save Settings" button both reported success without writing it, while the
small per-row pencil button was the only real save. These source-level seams
pin the intuitive contract: every profile-owned control uses profile autosave,
the target and payload survive a quick profile switch, and the full bundle is
written through the id-qualified endpoint.
"""

from __future__ import annotations

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture(scope="module")
def settings_js() -> str:
    return (ROOT / "webui" / "static" / "settings.js").read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def index_html() -> str:
    return (ROOT / "webui" / "index.html").read_text(
        encoding="utf-8", errors="replace")


# The eight controls a profile captures beyond the ladder. Same list as
# settings.js::collectFullQualityBundleFromUI reads.
BUNDLE_CONTROL_IDS = (
    "acoustid-require-verified",
    "downsample-hires",
    "audio-completeness-check",
    "import-replace-lower-quality",
    "lossy-copy-enabled",
    "lossy-copy-codec",
    "lossy-copy-bitrate",
    "lossy-copy-delete-original",
)

LADDER_CONTROL_IDS = (
    "quality-fallback-enabled",
    "quality-search-mode",
    "quality-rank-candidates",
    "quality-upgrade-policy",
    "quality-upgrade-cutoff",
)


@pytest.mark.parametrize("control_id", BUNDLE_CONTROL_IDS)
def test_every_profile_captured_control_is_in_the_bundle_set(settings_js, control_id):
    """A control missing here edits silently again — the exact bug."""
    start = settings_js.index("_QP_BUNDLE_CONTROL_IDS = new Set([")
    end = settings_js.index("]);", start)
    assert f"'{control_id}'" in settings_js[start:end]


@pytest.mark.parametrize("control_id", BUNDLE_CONTROL_IDS)
def test_the_bundle_set_matches_what_the_profile_actually_captures(settings_js,
                                                                   control_id):
    """Guard against the two lists drifting: every id in the nudge set must
    really be read by collectFullQualityBundleFromUI."""
    start = settings_js.index("function collectFullQualityBundleFromUI()")
    end = settings_js.index("\n}", start)
    assert f"'{control_id}'" in settings_js[start:end]


@pytest.mark.parametrize("control_id", LADDER_CONTROL_IDS)
def test_every_ladder_control_routes_to_profile_autosave(settings_js, control_id):
    start = settings_js.index("_QP_PROFILE_CONTROL_IDS = new Set([")
    end = settings_js.index("]);", start)
    assert f"'{control_id}'" in settings_js[start:end]


def test_the_full_bundle_is_included_in_profile_autosave(settings_js):
    start = settings_js.index("_QP_PROFILE_CONTROL_IDS = new Set([")
    end = settings_js.index("]);", start)
    assert "..._QP_BUNDLE_CONTROL_IDS" in settings_js[start:end]


def test_the_settings_autosave_routes_quality_changes_and_stops(settings_js):
    start = settings_js.index("function debouncedAutoSaveSettings(event)")
    end = settings_js.index("\n}", start)
    body = settings_js[start:end]
    assert "if (_qpHandleProfileControlChange(event)) return" in body
    assert "debouncedAutoSaveSettings(event)" in settings_js


def test_the_quality_change_handler_schedules_profile_autosave(settings_js):
    start = settings_js.index("function _qpHandleProfileControlChange(event)")
    end = settings_js.index("\n}", start)
    body = settings_js[start:end]
    assert "_QP_PROFILE_CONTROL_IDS.has(id)" in body
    assert "debouncedSaveQualityProfile()" in body
    assert "return true" in body


def test_debounce_captures_profile_id_and_full_payload_before_switch(settings_js):
    start = settings_js.index("function debouncedSaveQualityProfile()")
    end = settings_js.index("\n}", start)
    body = settings_js[start:end]
    assert "const targetProfileId = _qpEditingProfileId ?? _qpDefaultProfileId()" in body
    assert "const profile = collectFullQualityBundleFromUI()" in body
    assert "saveQualityProfile({ targetProfileId, profile })" in body


def test_save_targets_the_displayed_profile_and_refreshes_rows(settings_js):
    start = settings_js.index("async function saveQualityProfile(")
    end = settings_js.index("\n}\n\n//", start)
    body = settings_js[start:end]
    assert "`/api/quality-profile/custom/${resolvedTargetId}/update`" in body
    assert "body: JSON.stringify(payload)" in body
    assert "_qpSetProfileRows(data.profiles)" in body
    assert "renderCustomQualityProfiles(_qpProfileRows)" in body


def test_loading_settings_resets_view_to_full_default_bundle(settings_js):
    start = settings_js.index("async function loadQualityProfile()")
    end = settings_js.index("\n}\n\n//", start)
    body = settings_js[start:end]
    assert "_qpEditingProfileId = null" in body
    assert "populateQualityProfileUI(currentQualityProfile)" in body
    assert "applyFullQualityBundleToDom(currentQualityProfile)" in body
    assert "qpHideEditingBanner()" in body


def test_quick_preset_on_named_profile_uses_the_same_autosave(settings_js):
    start = settings_js.index("async function applyQualityPreset(presetName)")
    end = settings_js.index("\n}\n\n// Discard", start)
    body = settings_js[start:end]
    assert "currentQualityProfile = merged" in body
    assert "debouncedSaveQualityProfile()" in body
    assert "click ✎" not in body


def test_full_settings_save_still_protects_the_default_while_editing_another(
        settings_js):
    """If this ever goes away the nudge becomes a lie: the edit WOULD then be
    written, straight into the wrong profile."""
    for line in (
        "settings.acoustid.require_verified = !!def.acoustid_required",
        "settings.import.replace_lower_quality = !!def.replace_lower_quality",
        "settings.post_processing.audio_completeness_check = !!def.deep_audio_verify",
        "settings.lossy_copy.enabled = !!def.lossy_copy_enabled",
    ):
        assert line in settings_js


def test_the_banner_explains_intuitive_save_behaviour(index_html):
    start = index_html.index('id="qp-editing-banner"')
    end = index_html.index("</div>", start)
    banner = index_html[start:end]
    assert "Changes autosave to this profile" in banner
    assert "Save Settings saves it too" in banner
