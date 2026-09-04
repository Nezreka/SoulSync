"""Prowlarr release-title quality parsing at the source boundary."""

import pytest

from core.quality.release_format import (
    audio_quality_from_release,
    audio_quality_from_release_title,
    evaluate_release,
    formats_in_title,
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
        ('Artist - Album [WAV 24-96]', 'wav', None, 96_000, 24),
        ('Artist - Album [AIFF]', 'wav', None, None, None),
        ('Artist - Album [DSD256]', 'dsf', None, None, None),
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


def test_a_named_codec_wins_over_a_generic_mp3_category():
    """See test_a_generic_mp3_category_does_not_contradict_a_named_codec.

    This used to resolve to `unknown`, on the assumption that 3010 is exact
    codec evidence. It is not exact enough to overrule a title, and the two
    readers have to agree, so both let the title stand.
    """
    quality = audio_quality_from_release('Artist - Album [FLAC]', [3010])

    assert quality.format == 'flac'


def test_exact_category_does_not_hide_a_mixed_release_title():
    quality = audio_quality_from_release(
        'Artist - Album [FLAC + MP3]',
        [3010],
    )

    assert quality.format == 'unknown'


@pytest.mark.parametrize(
    ('title', 'fmt', 'bitrate', 'bit_depth'),
    [
        # Lidarr's QualityParser recognises these; SoulSync did not, so a
        # correctly-labelled release read as 'unknown' and lost its bucket.
        ('Artist - Album [FLAC24]', 'flac', None, 24),
        ('Artist - Album (TR24)', 'flac', None, 24),
        ('Artist - Album [AAC iTunes Plus]', 'aac', 256, None),
        ('Artist - Album [Ogg Vorbis q8]', 'ogg', 256, None),
        ('Artist - Album [Opus Q10]', 'opus', 500, None),
    ],
)
def test_lidarr_style_markers_are_understood(title, fmt, bitrate, bit_depth):
    quality = audio_quality_from_release_title(title)

    assert (quality.format, quality.bitrate, quality.bit_depth) == (fmt, bitrate, bit_depth)


def test_a_vorbis_quality_marker_needs_a_vorbis_codec():
    """``q8`` is only a bitrate claim next to Ogg/Opus.

    Lidarr maps a bare ``q8`` to 256 kbps whatever the codec. Here it must not
    invent a bitrate for a release whose codec is unknown — the same rule that
    keeps a bare ``192`` from becoming a bitrate.
    """
    assert audio_quality_from_release_title('Artist - Album Q8 (2026)').bitrate is None


def test_the_file_list_outranks_the_title():
    """A title is a claim; the file list is evidence.

    Lidarr cascades desc (tags) -> name -> extension and records which one
    answered. The same precedence belongs in one place here, instead of being
    re-implemented by each caller that happens to have a file list.
    """
    quality = audio_quality_from_release(
        'Artist - Album [MP3 320]',
        categories=(3010,),
        file_names=('01 - One.flac', '02 - Two.flac', 'cover.jpg'),
    )

    assert quality.format == 'flac'


def test_a_mixed_file_list_is_unknown_whatever_the_title_claims():
    quality = audio_quality_from_release(
        'Artist - Album [FLAC]',
        file_names=('01 - One.flac', '02 - Two.mp3'),
    )

    assert quality.format == 'unknown'


def test_a_file_list_without_audio_leaves_the_title_alone():
    quality = audio_quality_from_release(
        'Artist - Album [FLAC]',
        file_names=('cover.jpg', 'release.nfo'),
    )

    assert quality.format == 'flac'


@pytest.mark.parametrize('title', [
    'Artist - Heat Wave (2019) [FLAC]',
    'Artist - Wave (2020) [FLAC]',
    'Artist - New Wave Classics [FLAC]',
])
def test_the_word_wave_is_not_a_format_claim(title):
    """`wav` has a word boundary, `wave` did not, and albums are called Wave.

    A `wave` marker turned every one of these into a mixed flac/wav release,
    which a lossless-only profile then refused. Lidarr's codec regex only ever
    matched `WAV`, never `WAVE`, for this reason.
    """
    assert formats_in_title(title) == {'flac'}


def test_a_wav_release_is_still_recognised():
    assert formats_in_title('Artist - Album [WAV]') == {'wav'}


def test_a_generic_mp3_category_does_not_contradict_a_named_codec():
    """3010 is Audio/MP3, but plenty of indexers map their whole music
    category to it, FLAC torrents included. The title names this release; the
    category names whatever bucket the indexer put it in. So the title wins
    when it is specific, and the category only fills a title that said
    nothing.
    """
    assert audio_quality_from_release('Artist - Album [FLAC]', [3010]).format == 'flac'
    assert evaluate_release({'flac'}, 'Artist - Album [FLAC]', categories=(3010,))[0] is True


def test_a_bare_title_still_takes_the_exact_category():
    assert audio_quality_from_release('Artist - Album', [3010]).format == 'mp3'
