"""Does the release's SIZE support the quality its title claims?

Modelled on Lidarr's ``AcceptableSizeSpecification``, which multiplies a
quality's allowed kbit/s by the album's known duration and refuses a release
that falls outside it. The mechanism is the same here; the direction that
matters most for music is the floor, because a transcode relabelled FLAC is
the one lie a title can tell that costs a re-download.
"""

import pytest

from core.quality.model import AudioQuality
from core.quality.release_format import (
    implied_bitrate_kbps,
    size_contradicts_quality,
)


def test_implied_bitrate_is_size_over_duration():
    # 45 minutes at ~1000 kbit/s
    assert implied_bitrate_kbps(337_500_000, 2700) == pytest.approx(1000, rel=0.01)


@pytest.mark.parametrize('size_bytes, duration', [(0, 2700), (1_000, 0), (1_000, None), (None, 2700)])
def test_no_opinion_without_both_numbers(size_bytes, duration):
    assert implied_bitrate_kbps(size_bytes, duration) is None


def test_a_flac_claim_that_cannot_fit_its_own_bytes_is_contradicted():
    """A 45-minute "FLAC" album weighing 60 MB is a 190 kbit/s transcode."""
    reason = size_contradicts_quality(
        AudioQuality(format='flac'), 60_000_000, 2700,
    )

    assert reason and 'lossless' in reason.lower()


def test_a_real_flac_album_is_accepted():
    assert size_contradicts_quality(
        AudioQuality(format='flac'), 337_500_000, 2700,
    ) is None


def test_a_lossy_claim_far_above_its_own_bitrate_is_contradicted():
    """"MP3 320" that averages over 1000 kbit/s is not that album."""
    reason = size_contradicts_quality(
        AudioQuality(format='mp3', bitrate=320), 400_000_000, 2700,
    )

    assert reason and '320' in reason


def test_a_lossy_claim_within_tolerance_is_accepted():
    # 320 kbit/s of audio plus artwork and a log still lands near the claim.
    assert size_contradicts_quality(
        AudioQuality(format='mp3', bitrate=320), 115_000_000, 2700,
    ) is None


def test_an_unknown_format_has_no_size_expectation():
    assert size_contradicts_quality(
        AudioQuality(format='unknown'), 60_000_000, 2700,
    ) is None


def test_a_multichannel_hires_release_is_not_punished_as_oversized():
    """The ceiling is only asked of a stated lossy bitrate.

    24/192 multichannel legitimately runs into thousands of kbit/s; there is no
    upper bound that can separate it from a padded release, so lossless is only
    ever checked against the floor.
    """
    assert size_contradicts_quality(
        AudioQuality(format='flac', sample_rate=192_000, bit_depth=24),
        9_000_000_000, 2700,
    ) is None
