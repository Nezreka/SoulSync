"""Both replacement consumers use one measured, live-profile verdict."""

import pytest

from core.library2 import quality_eval
from core.quality.model import AudioQuality


@pytest.mark.parametrize("old,new,policy,cutoff,fallback,allowed", [
    (AudioQuality("mp3", 128), AudioQuality("mp3", 320), "until_cutoff", 0, True, True),
    (AudioQuality("mp3", 320), AudioQuality("mp3", 128), "until_cutoff", 0, True, False),
    (AudioQuality("mp3", 128), AudioQuality("mp3", 128), "until_cutoff", 0, True, False),
    (AudioQuality("mp3", 128), AudioQuality("mp3", 320), "none", 0, True, False),
    (AudioQuality("mp3", 128), AudioQuality("mp3", 320), "until_cutoff", 1, True, False),
    (None, AudioQuality("mp3", 320), "until_cutoff", 0, True, False),
    (AudioQuality("mp3", 128), None, "until_cutoff", 0, True, False),
])
def test_probed_verdict_requires_measured_progress(old, new, policy, cutoff, fallback, allowed):
    profile = {"id": 4, "ranked_targets": [
        {"format": "mp3", "min_bitrate": 320},
        {"format": "mp3", "min_bitrate": 128},
    ], "upgrade_policy": policy, "upgrade_cutoff_index": cutoff, "fallback_enabled": fallback}

    result = quality_eval.decide_probed_upgrade(old, new, profile, track_id=7)

    assert result.allowed is allowed
    assert result.track_id == 7
    assert result.profile_id == 4


def test_intentional_retention_does_not_start_an_upgrade_loop():
    profile = {"ranked_targets": [{"format": "flac", "bit_depth": 24}],
               "upgrade_policy": "until_cutoff"}
    result = quality_eval.decide_probed_upgrade(
        AudioQuality("mp3", 320), AudioQuality("flac", None, 96000, 24), profile,
        track_id=1, acquired_quality_json={"format": "flac", "bit_depth": 24, "sample_rate": 96000},
        retention_json=[{"source_replaced": True}],
    )
    assert result.allowed is False
    assert "cutoff" in result.reason
