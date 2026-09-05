from core.download_plugins.types import AlbumResult, TrackResult


def _track(fmt='flac', *, bitrate=None, sample_rate=None, bit_depth=None):
    return TrackResult(
        username='peer',
        filename=f'Artist/Album/track.{fmt}',
        size=10,
        bitrate=bitrate,
        duration=None,
        quality=fmt,
        free_upload_slots=1,
        upload_speed=1,
        queue_length=0,
        sample_rate=sample_rate,
        bit_depth=bit_depth,
        artist='Artist',
        title='Track',
    )


def _album(tracks):
    return AlbumResult(
        username='peer',
        album_path='Artist/Album',
        album_title='Album',
        artist='Artist',
        track_count=len(tracks),
        total_size=100,
        tracks=tracks,
        dominant_quality='flac',
    )


def test_album_quality_cannot_combine_maxima_from_different_tracks():
    quality = _album([
        _track(sample_rate=48_000, bit_depth=24),
        _track(sample_rate=96_000, bit_depth=16),
    ]).audio_quality

    assert quality.sample_rate == 48_000
    assert quality.bit_depth == 16


def test_album_quality_keeps_missing_resolution_unknown():
    quality = _album([
        _track(sample_rate=96_000, bit_depth=24),
        _track(sample_rate=None, bit_depth=None),
    ]).audio_quality

    assert quality.sample_rate is None
    assert quality.bit_depth is None


def test_mixed_album_does_not_claim_the_dominant_format():
    quality = _album([_track('flac'), _track('mp3', bitrate=320)]).audio_quality

    assert quality.format == 'unknown'


def test_album_with_unknown_track_does_not_claim_the_other_tracks_format():
    quality = _album([_track('flac'), _track('unknown')]).audio_quality

    assert quality.format == 'unknown'
