"""The profile shown in Settings -> Quality is the save target.

Historically a non-default row was only a preview: autosave and the prominent
"Save Settings" button both reported success without writing it, while the
small per-row pencil button was the only real save. These source-level seams
pin the intuitive contract: every profile-owned control uses profile autosave,
the target and payload survive a quick profile switch, and the full bundle is
written through the id-qualified endpoint.

The Quality tab is React now (SETTINGS_PORT.md step 6): the seams are in
webui/src/features/settings/tabs/quality-tab.tsx and quality.ts, and the
behaviour itself is tested there (quality.test.tsx). The page-wide save in
settings.js no longer sends any profile-owned key, so it cannot write the
profile on screen into the live default.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
TABS = ROOT / "webui" / "src" / "features" / "settings" / "tabs"


@pytest.fixture(scope="module")
def settings_js() -> str:
    return (ROOT / "webui" / "static" / "settings.js").read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def tab() -> str:
    return (TABS / "quality-tab.tsx").read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def logic() -> str:
    return (TABS / "quality.ts").read_text(encoding="utf-8")


def _body(source: str, start_marker: str) -> str:
    """A function's text, from its opening line to the matching brace."""
    start = source.index(start_marker)
    depth = 0
    for i in range(source.index("{", start), len(source)):
        if source[i] == "{":
            depth += 1
        elif source[i] == "}":
            depth -= 1
            if depth == 0:
                return source[start:i + 1]
    raise AssertionError(f"{start_marker} has no end")


# The eight controls a profile captures beyond the ladder, with the bundle key
# each one is saved under. Same list as quality.ts::collectBundle writes.
BUNDLE_CONTROLS = {
    "acoustid-require-verified": "acoustid_required",
    "downsample-hires": "downsample_enabled",
    "audio-completeness-check": "deep_audio_verify",
    "import-replace-lower-quality": "replace_lower_quality",
    "lossy-copy-enabled": "lossy_copy_enabled",
    "lossy-copy-codec": "lossy_copy_codec",
    "lossy-copy-bitrate": "lossy_copy_bitrate",
    "lossy-copy-delete-original": "lossy_copy_delete_original",
}

LADDER_CONTROLS = (
    "quality-fallback-enabled",
    "quality-search-mode",
    "quality-rank-candidates",
    "quality-upgrade-policy",
    "quality-upgrade-cutoff",
    "quality-release-import-mode",
)


def _handler_after(tab: str, control_id: str) -> str:
    """The text from a control's id to the end of its element: where its
    onChange is. The last id= is the control's own (a select's row takes the
    same id first, for its label)."""
    at = tab.rindex(f'id="{control_id}"')
    return tab[at:at + 700]


@pytest.mark.parametrize("control_id", BUNDLE_CONTROLS)
def test_every_profile_captured_control_is_in_the_bundle_set(tab, control_id):
    """A control missing here edits silently again — the exact bug."""
    assert "editExtras(" in _handler_after(tab, control_id)


@pytest.mark.parametrize("control_id,key", BUNDLE_CONTROLS.items())
def test_the_bundle_set_matches_what_the_profile_actually_captures(logic, control_id, key):
    """Guard against the two lists drifting: every control must really be
    written by collectBundle."""
    assert f"{key}:" in _body(logic, "export function collectBundle(")


@pytest.mark.parametrize("control_id", LADDER_CONTROLS)
def test_every_ladder_control_routes_to_profile_autosave(tab, control_id):
    handler = _handler_after(tab, control_id)
    assert "editLadder(" in handler or "onChange={onChange}" in handler


def test_the_full_bundle_is_included_in_profile_autosave(tab):
    queue = _body(tab, "function queueSave()")
    assert "collectBundle(l, e)" in queue
    # both kinds of edit go through it
    assert "queueSave();" in _body(tab, "const editLadder =")
    assert "queueSave();" in _body(tab, "const editExtras =")


def test_the_settings_autosave_leaves_quality_changes_to_the_tab(settings_js):
    start = settings_js.index("function debouncedAutoSaveSettings(event)")
    body = settings_js[start:settings_js.index("\n}", start)]
    # a React control returns here; a vanilla source panel moved into the
    # React modal does not (step 7b)
    assert "if (_t?.closest?.('[data-stg-react]')" in body
    assert "!_t.closest('.src-config-panel:not([data-react-panel])')) return;" in body
    assert "_qpHandleProfileControlChange" not in settings_js


def test_debounce_captures_profile_id_and_full_payload_before_switch(tab):
    queue = _body(tab, "function queueSave()")
    assert "const target = id ?? defaultProfileId(list)" in queue
    # one pending save per profile: a switch never retargets an edit
    assert "`${SAVE_KEY}${target ?? 'default'}`" in queue
    assert "{ target, bundle: collectBundle(l, e) }" in queue


def test_save_targets_the_displayed_profile_and_refreshes_rows(tab):
    save = _body(tab, "async function saveProfile(")
    assert "`/api/quality-profile/custom/${target}/update`" in save
    assert "postJson(url, bundle)" in save
    assert "setRows(data.profiles)" in save
    # and the rows drop every other picker's cached list (R2-11)
    assert "dropProfileCache()" in _body(tab, "const setRows =")


def test_loading_settings_resets_view_to_full_default_bundle(tab):
    start = tab.index("each visit to Settings loads the settings")
    load = tab[start:tab.index("}, [state.loads])", start)]
    assert "call('/api/quality-profile')" in load
    assert "setEditingId(null)" in load
    assert "setLadder(ladderFrom(profile" in load
    assert "setExtras(extrasFrom(profile" in load


def test_quick_preset_on_named_profile_uses_the_same_autosave(tab):
    preset = _body(tab, "async function applyPreset(")
    named = preset[preset.index("if (editingOther())"):preset.index("const data = await call(`/api/quality-profile/preset/")]
    assert "call('/api/quality-profile/presets')" in named
    assert "queueSave();" in named
    assert "/api/quality-profile/preset/${name}" not in named
    assert "click ✎" not in preset


def test_full_settings_save_never_writes_profile_keys(settings_js):
    """The page-wide save used to put the live default's values back into
    these keys while another profile was open. It sends none of them now, so
    there is nothing for it to overwrite the default with."""
    start = settings_js.index("async function saveSettings(")
    end = settings_js.index("\nasync function authorizeLastfmScrobbling", start)
    # its comments say where these keys went; only the code counts
    save = re.sub(r"//[^\n]*", "", settings_js[start:end])
    for control_id in BUNDLE_CONTROLS:
        assert f"'{control_id}'" not in save
    assert not re.search(r"\blossy_copy:\s*\{", save)
    assert "require_verified:" not in save
    assert "replace_lower_quality:" not in save
    assert "audio_completeness_check:" not in save
    assert "saveQualityProfile" not in save


def test_the_banner_explains_intuitive_save_behaviour(tab):
    start = tab.index('id="qp-editing-banner"')
    banner = re.sub(r"\s+", " ", tab[start:tab.index("</div>", start)])
    assert "Changes autosave to this profile" in banner
    assert "the save status at the top saves it right away too" in banner


def test_conversion_copy_distinguishes_acquisition_from_retained_output(tab, logic):
    flat = re.sub(r"\s+", " ", tab)
    assert "One profile, two decisions" in flat
    assert "acquired quality is remembered" in flat
    assert "lossless + ${codec} companion" in logic
    assert "retain ${codec} only (acquisition remembered)" in logic
    assert "Blasphemy Mode" not in tab
