"""qobuz album downloads pick the album's own release of each track (#1866).

an album download searches qobuz per track. a big catalog song comes back on
the studio album and on every best-of, and the matcher only read the album off
folder names in the filename. qobuz filenames are 'id||Artist - Title', so
every copy tied and qobuz's result order picked the release. when that was a
cd-only compilation, a few tracks of a 24/96 album landed at 16/44.1.
"""

from __future__ import annotations

import pytest

from core.downloads.candidates import order_candidates
from core.matching_engine import MusicMatchingEngine
from core.qobuz_client import QOBUZ_QUALITY_MAP, QobuzClient
from core.spotify_client import Track as SpotifyTrack

WANTED_ALBUM = 'Timeless - The All-Time Greatest Hits'


def _qobuz_api_track(track_id, album_title, *, hires, duration=182):
    # shape of a track/search item from the qobuz api
    return {
        'id': track_id,
        'title': "I've Gotta Get A Message To You",
        'version': None,
        'performer': {'name': 'Bee Gees'},
        'duration': duration,
        'track_number': 11,
        'streamable': True,
        'hires_streamable': hires,
        'album': {
            'title': album_title,
            'maximum_bit_depth': 24 if hires else 16,
            'maximum_sampling_rate': 96 if hires else 44.1,
            'image': {'large': 'x'},
        },
    }


def _results():
    client = QobuzClient.__new__(QobuzClient)
    quality = QOBUZ_QUALITY_MAP['hires']
    # qobuz lists the cd-only best-of first, the album asked for second
    api = [
        _qobuz_api_track(111, 'Number Ones', hires=False),
        _qobuz_api_track(222, WANTED_ALBUM, hires=True),
    ]
    return [client._qobuz_to_track_result(t, quality) for t in api]


def _wanted():
    return SpotifyTrack(
        id='1', name="I've Gotta Get A Message To You", artists=['Bee Gees'],
        album=WANTED_ALBUM, duration_ms=182000, popularity=0,
        preview_url=None, external_urls={},
    )


@pytest.fixture
def engine():
    return MusicMatchingEngine()


def test_the_albums_own_release_wins_the_match(engine):
    ranked = engine.find_best_slskd_matches_enhanced(_wanted(), _results())
    assert ranked[0].album == WANTED_ALBUM
    assert ranked[0].bit_depth == 24


def test_the_download_walk_keeps_it_first(engine):
    # the walk re-sorts on its own, so hand it qobuz's order, not the
    # matcher's
    results = _results()
    for r in results:
        r.confidence, r.version_type = \
            engine.calculate_slskd_match_confidence_enhanced(_wanted(), r)
    walk = order_candidates(results)
    assert walk[0].filename.startswith('222||')


def test_a_path_result_still_reads_its_folders_not_the_field(engine):
    # soulseek paths already carry the album; the field must not add a second
    # guess on top of them.
    from core.download_plugins.types import TrackResult

    def scored(album):
        r = TrackResult(
            username='peer', filename='Music/Bee Gees/Other/01 - Words.flac',
            size=1, bitrate=1411, duration=182000, quality='flac',
            free_upload_slots=1, upload_speed=1, queue_length=0,
            artist='Bee Gees', title='Words', album=album,
        )
        wanted = SpotifyTrack(
            id='1', name='Words', artists=['Bee Gees'], album=WANTED_ALBUM,
            duration_ms=182000, popularity=0, preview_url=None, external_urls={},
        )
        return engine.calculate_slskd_match_confidence(wanted, r)

    assert scored(WANTED_ALBUM) == scored(None)


def test_album_downloads_score_streaming_hits_the_same_way(engine, monkeypatch):
    # album downloads route qobuz through the streaming validator, not the
    # filename matcher. qobuz's order goes in, the album's release comes out.
    from core.downloads import validation
    monkeypatch.setattr(validation, 'matching_engine', engine)
    scored = validation._score_streaming_candidates(_results(), _wanted())
    assert [r.filename.split('||')[0] for r in scored] == ['222', '111']
    walk = order_candidates(_results_scored_like(scored))
    assert walk[0].bit_depth == 24


def _results_scored_like(scored):
    # the walk gets the validator's rows back in qobuz's order
    return sorted(scored, key=lambda r: r.filename)
