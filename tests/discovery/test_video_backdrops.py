"""music videos behind discover banners (sept 29 2026): the pick is strict,
remembered, and never searches twice for the same artist."""

import time

import pytest

from core.discovery import video_backdrops as vb
from database.music_database import MusicDatabase


def _v(vid, title, channel='x', duration=220, views=1_000_000):
    return {'video_id': vid, 'title': title, 'channel': channel, 'duration': duration,
            'view_count': views}


def test_the_official_video_wins():
    picks = [
        _v('lyric', 'M83 - Midnight City (Lyrics)'),
        _v('fan', 'midnight city but it is slowed'),
        _v('off', 'M83 - Midnight City (Official Video)', channel='M83VEVO'),
    ]
    assert vb.pick_video(picks, 'M83') == 'off'


def test_a_video_that_isnt_this_artist_is_no_video():
    assert vb.pick_video([_v('a', 'Somebody Else - Official Video')], 'M83') is None


def test_hour_long_mixes_and_shorts_are_out():
    assert vb.pick_video([_v('a', 'M83 official video', duration=3600)], 'M83') is None
    assert vb.pick_video([_v('b', 'M83 official video', duration=30)], 'M83') is None


def test_a_release_prefers_its_own_video():
    picks = [_v('other', 'M83 - Midnight City (Official Video)', channel='M83VEVO', views=10**8),
             _v('this', 'M83 - Oceans Niagara (Official Video)', channel='M83VEVO', views=10**5)]
    assert vb.pick_video(picks, 'M83', 'Oceans Niagara') == 'this'


def test_live_recordings_lose_to_the_studio_video():
    picks = [_v('live', 'M83 - Wait (Official Live Video)', channel='M83'),
             _v('studio', 'M83 - Wait (Official Video)', channel='M83')]
    assert vb.pick_video(picks, 'M83') == 'studio'


@pytest.fixture()
def db(tmp_path):
    return MusicDatabase(str(tmp_path / 'm.db'))


class FakeYouTube:
    def __init__(self, results):
        self.results, self.queries = results, []

    async def search_videos(self, query, max_results=20):
        self.queries.append(query)
        return self.results


def test_one_search_per_artist_then_the_answer_is_remembered(db):
    yt = FakeYouTube([_v('off', 'Tool - Schism (Official Video)', channel='TOOL')])
    assert vb.find_backdrop(db, yt, 'Tool') == 'off'
    assert vb.find_backdrop(db, yt, 'Tool') == 'off'
    assert yt.queries == ['Tool official music video']


def test_a_miss_is_remembered_too(db):
    yt = FakeYouTube([])
    assert vb.find_backdrop(db, yt, 'Nobody') is None
    assert vb.find_backdrop(db, yt, 'Nobody') is None
    assert len(yt.queries) == 1


def test_a_failed_search_is_not_remembered_as_a_miss(db):
    class Broken:
        async def search_videos(self, query, max_results=20):
            raise RuntimeError('yt-dlp down')
    assert vb.find_backdrop(db, Broken(), 'Tool') is None
    yt = FakeYouTube([_v('off', 'Tool - Schism (Official Video)', channel='TOOL')])
    assert vb.find_backdrop(db, yt, 'Tool') == 'off'


def test_old_answers_expire(db):
    vb.remember(db, 'Tool', None, 'old', now=time.time() - vb.HIT_TTL - 5)
    assert vb.cached_pick(db, 'Tool', None) == (False, None)
    vb.remember(db, 'Tool', None, None, now=time.time() - vb.MISS_TTL + 60)
    assert vb.cached_pick(db, 'Tool', None) == (True, None)
