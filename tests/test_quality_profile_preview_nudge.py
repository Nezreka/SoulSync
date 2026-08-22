"""Editing a profile-captured toggle while previewing somebody else's profile
must say so.

The Settings -> Quality page owns two kinds of control. The ranked-target
ladder saves through `debouncedSaveQualityProfile`, which already refuses to
write while a non-default profile is previewed and shows the editing banner
instead. The other eight (AcoustID strictness, downsample, deep verify,
replace-lower-quality and the lossy-copy group) are ordinary config keys saved
by the whole-page `saveSettings`, and that one substitutes the real default's
stored values back in for exactly those keys before sending — so the edit is
correctly NOT written anywhere.

Correct, but silent: the user toggled something, no banner appeared, and the
value read Off again after switching profiles and back. These assertions pin
the nudge that closes the gap.
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


@pytest.mark.parametrize("control_id", BUNDLE_CONTROL_IDS)
def test_every_profile_captured_control_is_in_the_nudge_set(settings_js, control_id):
    """A control missing here edits silently again — the exact bug."""
    start = settings_js.index("_QP_BUNDLE_CONTROL_IDS = new Set([")
    end = settings_js.index("]);", start)
    assert f"'{control_id}'" in settings_js[start:end]


@pytest.mark.parametrize("control_id", BUNDLE_CONTROL_IDS)
def test_the_nudge_set_matches_what_the_profile_actually_captures(settings_js,
                                                                  control_id):
    """Guard against the two lists drifting: every id in the nudge set must
    really be read by collectFullQualityBundleFromUI."""
    start = settings_js.index("function collectFullQualityBundleFromUI()")
    end = settings_js.index("\n}", start)
    assert f"'{control_id}'" in settings_js[start:end]


def test_the_settings_autosave_runs_the_nudge(settings_js):
    """It has to hang off the whole-page autosave, not the quality one — those
    eight controls never reach debouncedSaveQualityProfile."""
    start = settings_js.index("function debouncedAutoSaveSettings(event)")
    end = settings_js.index("\n}", start)
    body = settings_js[start:end]
    assert "_qpNudgeIfEditingForeignProfile(event)" in body
    # The event is what identifies the control; a no-arg signature cannot tell
    # a lossy-copy toggle from a Plex URL.
    assert "debouncedAutoSaveSettings(event)" in settings_js


def test_the_nudge_only_fires_for_a_foreign_profile(settings_js):
    """Editing the live default is the normal case and must stay quiet."""
    start = settings_js.index("function _qpNudgeIfEditingForeignProfile(event)")
    end = settings_js.index("\n}", start)
    body = settings_js[start:end]
    assert "_qpEditingProfileId === null" in body
    assert "_qpEditingProfileId === _qpDefaultProfileId()" in body
    assert "qpShowEditingBanner()" in body


def test_the_nudge_does_not_block_the_save(settings_js):
    """saveSettings already substitutes the default's values back in for these
    keys, so the save is a no-op for them — skipping it outright would drop
    whatever unrelated edit was debounced alongside."""
    start = settings_js.index("function _qpNudgeIfEditingForeignProfile(event)")
    end = settings_js.index("\n}", start)
    body = settings_js[start:end]
    # It may return early (nothing to say), but it must never report back.
    assert "return true" not in body and "return false" not in body


def test_the_substitution_block_that_makes_the_nudge_honest_is_still_there(
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


def test_the_banner_tells_the_user_what_to_do(index_html):
    """It used to say 'changes on the left', which is the ladder tiles — but
    six of the eight controls this now fires for live in completely different
    sections of the page."""
    start = index_html.index('id="qp-editing-banner"')
    end = index_html.index("</div>", start)
    banner = index_html[start:end]
    assert "on this page" in banner
    assert "✎" in banner
