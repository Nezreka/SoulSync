"""quality_meets_profile / targets_from_profile — the shared strict
quality-decision core used by BOTH the import quality guard and the library
quality scanner. A file "meets" the profile iff its real measured quality
satisfies at least one ranked target (bit depth + sample rate are minimums;
fallback is NOT consulted — that's a download-time concession, not a definition
of quality).
"""

from core.quality.model import AudioQuality, QualityTarget
from core.quality.selection import quality_meets_profile, targets_from_profile


FLAC_HI = AudioQuality('flac', sample_rate=96000, bit_depth=24)
FLAC_CD = AudioQuality('flac', sample_rate=44100, bit_depth=16)
MP3 = AudioQuality('mp3', bitrate=320)

WANT_24BIT = [
    QualityTarget(label='FLAC 24/96', format='flac', bit_depth=24, min_sample_rate=96000),
    QualityTarget(label='FLAC 24/44.1', format='flac', bit_depth=24, min_sample_rate=44100),
]


def test_24bit_meets_24bit_target():
    assert quality_meets_profile(FLAC_HI, WANT_24BIT) is True


def test_16bit_does_not_meet_24bit_target():
    assert quality_meets_profile(FLAC_CD, WANT_24BIT) is False


def test_mp3_does_not_meet_flac_target():
    assert quality_meets_profile(MP3, WANT_24BIT) is False


def test_no_targets_means_no_constraint():
    assert quality_meets_profile(FLAC_CD, []) is True


def test_unprobeable_file_is_not_flagged():
    # aq=None (probe failed) → don't act (avoid false re-downloads).
    assert quality_meets_profile(None, WANT_24BIT) is True


def test_targets_from_profile_reads_v3_ranked_targets():
    profile = {
        'version': 3,
        'fallback_enabled': False,
        'ranked_targets': [
            {'label': 'FLAC 24/96', 'format': 'flac', 'bit_depth': 24, 'min_sample_rate': 96000},
        ],
    }
    targets, fallback = targets_from_profile(profile)
    assert [t.label for t in targets] == ['FLAC 24/96']
    assert fallback is False


def test_targets_from_profile_migrates_v2_qualities():
    profile = {'qualities': {'flac': {'enabled': True, 'priority': 1}}}
    targets, _ = targets_from_profile(profile)
    assert any(t.format == 'flac' for t in targets)
