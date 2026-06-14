"""TrackResult.set_quality() — merge a mapped AudioQuality onto a result so
its derived ``audio_quality`` reflects the source's real/claimed tier.
"""

from core.download_plugins.types import TrackResult
from core.quality.model import AudioQuality


def _tr(**kw):
    base = dict(
        username='x', filename='f', size=0, bitrate=None, duration=None,
        quality='', free_upload_slots=0, upload_speed=0, queue_length=0,
    )
    base.update(kw)
    return TrackResult(**base)


def test_set_quality_populates_lossless_fields():
    tr = _tr(quality='flac', bitrate=1411)
    tr.set_quality(AudioQuality('flac', sample_rate=96000, bit_depth=24))
    assert tr.quality == 'flac'
    assert tr.sample_rate == 96000
    assert tr.bit_depth == 24
    # derived descriptor must reflect the merge
    assert tr.audio_quality.sample_rate == 96000
    assert tr.audio_quality.bit_depth == 24


def test_set_quality_keeps_existing_bitrate_when_mapper_has_none():
    tr = _tr(quality='flac', bitrate=950)
    tr.set_quality(AudioQuality('flac', sample_rate=44100, bit_depth=16))
    assert tr.bitrate == 950  # mapper bitrate is None → preserve probed/reported


def test_set_quality_overwrites_bitrate_for_lossy():
    tr = _tr(quality='mp3', bitrate=128)
    tr.set_quality(AudioQuality('mp3', bitrate=320))
    assert tr.bitrate == 320
    assert tr.bit_depth is None
