"""AudioQuality.matches_target + v2->v3 migration.

Locks the bitrate-as-threshold behaviour: lossy formats match on a MINIMUM
bitrate (>=, a range), and lossless matches on bit depth + sample rate — NOT
on exact bitrate, so a FLAC's wildly-varying bitrate (stereo vs mono, FLAC
compression) never falsely rejects it.
"""

import pytest

from core.quality.model import (
    AudioQuality,
    QualityTarget,
    v2_qualities_to_ranked_targets,
)


# ── lossy: bitrate is a minimum threshold (a range), never exact ───────────

def test_mp3_meets_minimum_bitrate():
    t = QualityTarget(format='mp3', min_bitrate=320)
    assert AudioQuality('mp3', bitrate=320).matches_target(t) is True
    assert AudioQuality('mp3', bitrate=400).matches_target(t) is True  # above floor ok


def test_mp3_below_minimum_bitrate_rejected():
    t = QualityTarget(format='mp3', min_bitrate=320)
    assert AudioQuality('mp3', bitrate=300).matches_target(t) is False


def test_mp3_matches_lower_threshold():
    t = QualityTarget(format='mp3', min_bitrate=192)
    assert AudioQuality('mp3', bitrate=320).matches_target(t) is True


# ── lossless: matched on bit depth + sample rate, NOT exact bitrate ────────

def test_flac_matches_on_depth_and_rate_regardless_of_bitrate():
    t = QualityTarget(format='flac', bit_depth=24, min_sample_rate=96000)
    # An unusual/low bitrate (e.g. a mono or highly-compressed FLAC) must
    # still match when bit depth + sample rate satisfy the target.
    weird = AudioQuality('flac', bitrate=300, sample_rate=96000, bit_depth=24)
    assert weird.matches_target(t) is True


def test_flac_below_target_sample_rate_rejected():
    t = QualityTarget(format='flac', bit_depth=24, min_sample_rate=96000)
    assert AudioQuality('flac', sample_rate=44100, bit_depth=24).matches_target(t) is False


def test_flac_below_target_bit_depth_rejected():
    t = QualityTarget(format='flac', bit_depth=24)
    assert AudioQuality('flac', sample_rate=96000, bit_depth=16).matches_target(t) is False


def test_format_mismatch_rejected():
    t = QualityTarget(format='flac', bit_depth=16)
    assert AudioQuality('mp3', bitrate=320).matches_target(t) is False


# ── v2 -> v3 migration preserves the user's priority order ─────────────────

def test_v2_to_v3_preserves_order_and_maps_fields():
    qualities = {
        'flac':    {'enabled': True,  'priority': 1, 'bit_depth': '24'},
        'mp3_320': {'enabled': True,  'priority': 2},
        'mp3_192': {'enabled': False, 'priority': 3},  # disabled → dropped
    }
    targets = v2_qualities_to_ranked_targets(qualities)
    formats = [t['format'] for t in targets]
    assert formats == ['flac', 'mp3']          # disabled mp3_192 omitted
    assert targets[0]['bit_depth'] == 24
    assert targets[1]['min_bitrate'] == 320
