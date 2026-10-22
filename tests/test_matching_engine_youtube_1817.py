"""YouTube results scored on their title and artist, not the synthetic filename (#1817).

A YouTube Music catalog hit comes back as filename 'videoId||Sugar' with the
artist in its own field. The scorer read the whole filename like a Soulseek
path, so the video id swamped the title and the artist was nowhere to be
found. Sugar by Maroon 5 scored 0.53 against itself and got rejected.
"""

from __future__ import annotations

import pytest

from core.matching_engine import MusicMatchingEngine
from core.download_plugins.types import TrackResult
from core.spotify_client import Track as SpotifyTrack

# 0.58 is the accept threshold in find_best_slskd_matches_enhanced.
ACCEPT_THRESHOLD = 0.58


@pytest.fixture
def engine() -> MusicMatchingEngine:
    return MusicMatchingEngine()


def _track(name: str, artists: list[str], duration_ms: int = 235000) -> SpotifyTrack:
    return SpotifyTrack(
        id='1', name=name, artists=artists, album='V',
        duration_ms=duration_ms, popularity=0, preview_url=None, external_urls={},
    )


def _yt(filename: str, artist, title, duration_ms: int = 236000) -> TrackResult:
    return TrackResult(
        username='youtube', filename=filename, size=0, bitrate=160,
        duration=duration_ms, quality='opus',
        free_upload_slots=999, upload_speed=999999, queue_length=0,
        artist=artist, title=title, album=None, track_number=None,
    )


def _enhanced(engine, track, result):
    return engine.calculate_slskd_match_confidence_enhanced(track, result)


def test_exact_catalog_hit_is_accepted(engine):
    conf, version = _enhanced(engine, _track('Sugar', ['Maroon 5']),
                              _yt('MozAXGgC1Mc||Sugar', 'Maroon 5', 'Sugar'))
    assert conf > ACCEPT_THRESHOLD
    assert version == 'original'


def test_catalog_hit_artist_comes_from_the_artist_field(engine):
    result = _yt('MozAXGgC1Mc||Sugar', 'Maroon 5', 'Sugar')
    engine.calculate_slskd_match_confidence(_track('Sugar', ['Maroon 5']), result)
    assert result.artist_score == 1.0
    assert result.title_score == 1.0


def test_different_song_same_artist_is_rejected(engine):
    conf, _ = _enhanced(engine, _track('Sugar', ['Maroon 5']),
                        _yt('abcdefghijk||Feelings', 'Maroon 5', 'Feelings'))
    assert conf <= ACCEPT_THRESHOLD


def test_same_title_different_artist_scores_below_the_real_one(engine):
    track = _track('Sugar', ['Maroon 5'])
    right, _ = _enhanced(engine, track, _yt('MozAXGgC1Mc||Sugar', 'Maroon 5', 'Sugar'))
    wrong, _ = _enhanced(engine, track, _yt('zzzzzzzzzzz||Sugar', 'Sing2Piano', 'Sugar'))
    assert wrong < right


def test_karaoke_version_is_rejected(engine):
    conf, _ = _enhanced(engine, _track('Sugar', ['Maroon 5']),
                        _yt('abcdefghijk||Sugar (Piano Karaoke Version)',
                            'Sing2Piano', 'Sugar (Piano Karaoke Version)'))
    assert conf <= ACCEPT_THRESHOLD


def test_acoustic_of_a_different_song_is_rejected(engine):
    conf, _ = _enhanced(engine, _track('Sugar', ['Maroon 5']),
                        _yt('abcdefghijk||This Love (Acoustic)',
                            'Maroon 5', 'This Love (Acoustic)'))
    assert conf <= ACCEPT_THRESHOLD


def test_remix_rejected_when_original_asked_for(engine):
    conf, _ = _enhanced(engine, _track('Sugar', ['Maroon 5']),
                        _yt('abcdefghijk||Sugar (Remix)', 'Maroon 5', 'Sugar (Remix)'))
    assert conf <= ACCEPT_THRESHOLD


def test_remix_accepted_when_remix_asked_for(engine):
    conf, version = _enhanced(engine, _track('Sugar (Remix)', ['Maroon 5']),
                              _yt('abcdefghijk||Sugar (Remix)', 'Maroon 5', 'Sugar (Remix)'))
    assert conf > ACCEPT_THRESHOLD
    assert version == 'remix'


def test_video_id_never_read_as_a_version_marker(engine):
    # ids are [A-Za-z0-9_-], so 'a-live-bcde' is a real possibility
    conf, version = _enhanced(engine, _track('Sugar', ['Maroon 5']),
                              _yt('a-live-bcde||Sugar', 'Maroon 5', 'Sugar'))
    assert version == 'original'
    assert conf > ACCEPT_THRESHOLD


def test_short_title_long_artist_not_vetoed(engine):
    # the id used to be most of the candidate base title, so 'up' vs
    # 'abcdefghijk up' sat under the veto floor once the artist matched
    conf, _ = _enhanced(engine, _track('Up', ['Pink Floyd'], 200000),
                        _yt('abcdefghijk||Up', 'Pink Floyd', 'Up', 200000))
    assert conf > ACCEPT_THRESHOLD


def test_video_result_without_artist_still_matches_from_title(engine):
    conf, _ = _enhanced(engine, _track('Sugar', ['Maroon 5']),
                        _yt('MozAXGgC1Mc||Maroon 5 - Sugar (Official Music Video)', None, None))
    assert conf > ACCEPT_THRESHOLD


def test_preferred_version_matches_youtube_title(engine):
    result = _yt('abcdefghijk||Sugar (Extended Version)', 'Maroon 5', 'Sugar (Extended Version)')
    result.version_type = 'extended'
    ids = engine.preferred_match_ids(_track('Sugar', ['Maroon 5']), [result], 'extended')
    assert id(result) in ids


def test_non_youtube_candidate_text_is_the_raw_filename(engine):
    tidal = TrackResult(
        username='tidal', filename='12345||Maroon 5 - Sugar', size=0, bitrate=1000,
        duration=236000, quality='flac', free_upload_slots=1, upload_speed=1, queue_length=0,
    )
    peer = TrackResult(
        username='peer', filename='Maroon 5/V/03 - Sugar.flac', size=0, bitrate=1000,
        duration=236000, quality='flac', free_upload_slots=1, upload_speed=1, queue_length=0,
    )
    assert engine._candidate_text(tidal) == '12345||Maroon 5 - Sugar'
    assert engine._candidate_text(peer) == 'Maroon 5/V/03 - Sugar.flac'
