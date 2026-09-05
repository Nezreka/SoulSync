from core.quality.model import AudioQuality
from core.soulseek_client import SoulseekClient


def test_slskd_direct_resolution_reaches_search_results():
    client = SoulseekClient.__new__(SoulseekClient)
    file_data = {
        'filename': 'Son 2 Sea Ver.flac',
        'size': 74_931_241,
        'length': 238,
        'bitRate': 1411,
        'sampleRate': 48_000,
        'bitDepth': 24,
    }

    searched, _ = client._process_search_responses([
        {'username': 'fishingpvalues', 'files': [file_data]}
    ])
    browsed = client.parse_browse_results_to_tracks('fishingpvalues', [file_data])

    assert [(r.bitrate, r.sample_rate, r.bit_depth) for r in searched + browsed] == [
        (1411, 48_000, 24),
        (1411, 48_000, 24),
    ]


def test_audio_quality_supports_direct_and_attribute_slskd_shapes():
    direct = AudioQuality.from_slskd_file(
        {'bitRate': 1411, 'sampleRate': 96_000, 'bitDepth': 24}, '.flac'
    )
    attributes = AudioQuality.from_slskd_file(
        {'attributes': [
            {'type': 0, 'value': 900},
            {'type': 4, 'value': 48_000},
            {'type': 5, 'value': 16},
        ]},
        '.flac',
    )

    assert (direct.bitrate, direct.sample_rate, direct.bit_depth) == (1411, 96_000, 24)
    assert (attributes.bitrate, attributes.sample_rate, attributes.bit_depth) == (
        900,
        48_000,
        16,
    )
