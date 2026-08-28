"""Prowlarr release-title quality parsing at the source boundary."""

import pytest

from core.quality.release_format import (
    audio_quality_from_release,
    audio_quality_from_release_title,
)


@pytest.mark.parametrize(
    ('title', 'fmt', 'bitrate', 'sample_rate', 'bit_depth'),
    [
        ('Artist - Album [FLAC 24-96]', 'flac', None, 96_000, 24),
        ('Artist - Album FLAC 24bit 192kHz', 'flac', None, 192_000, 24),
        ('Artist - Album [FLAC 16bit 44.1kHz]', 'flac', None, 44_100, 16),
        ('Artist - Album [MP3 320]', 'mp3', 320, None, None),
        ('Artist - Album AAC 256kbps', 'aac', 256, None, None),
        ('Artist - Album [V0]', 'mp3', None, None, None),
    ],
)
def test_release_title_quality_matrix(title, fmt, bitrate, sample_rate, bit_depth):
    quality = audio_quality_from_release_title(title)

    assert (
        quality.format,
        quality.bitrate,
        quality.sample_rate,
        quality.bit_depth,
    ) == (fmt, bitrate, sample_rate, bit_depth)


def test_unlabelled_release_does_not_claim_mp3():
    quality = audio_quality_from_release_title('Artist - Album (2026)')

    assert quality.format == 'unknown'
    assert quality.bitrate is None


def test_mixed_release_does_not_claim_one_of_its_formats():
    quality = audio_quality_from_release_title('Artist - Album [FLAC + MP3]')

    assert quality.format == 'unknown'


def test_codec_markers_require_word_boundaries():
    assert audio_quality_from_release_title('The Escape Artists').format == 'unknown'


def test_newznab_leaf_category_only_fills_an_exact_codec():
    # 3040 is generic lossless and may be FLAC, ALAC, APE, WavPack, ...
    assert audio_quality_from_release('Artist - Album', [3000, 3040]).format == 'unknown'
    assert audio_quality_from_release('Artist - Album', [3010]).format == 'mp3'


def test_lossless_category_keeps_a_named_lossless_codec():
    assert audio_quality_from_release('Artist - Album [ALAC]', [3040]).format == 'alac'


def test_title_category_conflict_stays_unknown():
    quality = audio_quality_from_release('Artist - Album [FLAC]', [3010])

    assert quality.format == 'unknown'
