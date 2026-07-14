"""Quality evaluation for acceptable / until_cutoff / until_top policies."""

from __future__ import annotations

import json

from core.library2.quality_eval import evaluate_file, is_upgrade_policy, profile_targets

_TARGETS = json.dumps([
    {"label": "FLAC 24/96", "format": "flac", "bit_depth": 24, "min_sample_rate": 96000},
    {"label": "FLAC 16", "format": "flac", "bit_depth": 16},
    {"label": "MP3 320", "format": "mp3", "min_bitrate": 320},
])


def _profile(policy: str, cutoff: int = 0):
    return {"ranked_targets": _TARGETS, "upgrade_policy": policy,
            "upgrade_cutoff_index": cutoff}


def _flac16():
    return {"format": "flac", "bitrate": 900, "sample_rate": 44100, "bit_depth": 16}


def _mp3():
    return {"format": "mp3", "bitrate": 320, "sample_rate": 44100, "bit_depth": None}


def test_acceptable_never_upgrades_once_met():
    targets, policy, cutoff = profile_targets(_profile("acceptable"))
    ev = evaluate_file(_flac16(), targets, policy, cutoff)
    assert ev == {"meets_profile": True, "upgrade_candidate": False}


def test_until_top_upgrades_below_first_target():
    targets, policy, cutoff = profile_targets(_profile("until_top"))
    ev = evaluate_file(_flac16(), targets, policy, cutoff)
    assert ev["meets_profile"] is True
    assert ev["upgrade_candidate"] is True  # FLAC16 is rank 1, top is rank 0


def test_until_cutoff_respects_cutoff_index():
    # Cutoff at index 1 (FLAC 16): a FLAC16 file is done, an MP3 is not.
    targets, policy, cutoff = profile_targets(_profile("until_cutoff", cutoff=1))
    assert evaluate_file(_flac16(), targets, policy, cutoff)["upgrade_candidate"] is False
    assert evaluate_file(_mp3(), targets, policy, cutoff)["upgrade_candidate"] is True


def test_is_upgrade_policy():
    assert is_upgrade_policy("until_top")
    assert is_upgrade_policy("until_cutoff")
    assert not is_upgrade_policy("acceptable")
    assert not is_upgrade_policy(None)


def test_unknown_quality_never_flags():
    targets, policy, cutoff = profile_targets(_profile("until_cutoff"))
    ev = evaluate_file(None, targets, policy, cutoff)
    assert ev == {"meets_profile": True, "upgrade_candidate": False}
