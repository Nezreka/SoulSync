"""mirrored beatport lists refresh like any other source.

beatport mirrors used to be keyed by a random hash, so refresh skipped them
and a mirrored chart was frozen the day it was made. now a mirror is keyed by
its list ref: refresh pulls the list again, new tracks come in, dropped ones go
out, and a track that stays keeps its identification. old hash mirrors stay
frozen, and stay out of the pipeline, exactly as before.
"""

import json

from core.automation.handlers.refresh_mirrored import auto_refresh_mirrored
from core.playlists.sources.base import to_mirror_track_dict
from core.playlists.sources.beatport import BeatportPlaylistSource, is_refreshable_mirror
from core.playlists.sources.registry import PlaylistSourceRegistry
from core.playlists.user_playlists import has_upstream, pipeline_runs
from database.music_database import MusicDatabase
from tests.automation.test_handlers_playlist import _build_deps

REF = 'genre:trance-main-floor:7:hype-10'


def _bp_track(n, title, isrc='DEA312600932'):
    return {
        'id': f'beatport_{n}', 'beatport_id': n, 'title': title, 'mix_name': 'Extended Mix',
        'artist': 'Talla 2XLC', 'artists': [{'name': 'Talla 2XLC'}], 'release_name': f'{title} EP',
        'duration_ms': 300000, 'release_image': 'http://img', 'isrc': isrc,
    }


def _listed(tracks):
    return {'ref': REF, 'name': 'Trance (Main Floor) Hype Top 10', 'image': 'http://img',
            'url': 'https://www.beatport.com/genre/trance-main-floor/7', 'creator': 'Beatport',
            'tracks': tracks}


def test_adapter_shape():
    adapter = BeatportPlaylistSource(fetch=lambda ref: _listed([_bp_track(1, 'Sturm')]))
    detail = adapter.refresh_playlist(REF)
    assert detail.meta.source_playlist_id == REF
    assert detail.meta.name == 'Trance (Main Floor) Hype Top 10'
    row = to_mirror_track_dict(detail.tracks[0])
    assert row == {'track_name': 'Sturm (Extended Mix)', 'artist_name': 'Talla 2XLC',
                   'album_name': 'Sturm EP', 'duration_ms': 300000, 'source_track_id': 'beatport_1'}
    # no extra_data: a surviving row keeps the identification it has
    assert 'extra_data' not in row


def test_adapter_refuses_junk_and_dead_fetches():
    calls = []
    adapter = BeatportPlaylistSource(fetch=lambda ref: calls.append(ref))
    assert adapter.refresh_playlist('a1b2c3d4') is None
    assert calls == []
    assert adapter.refresh_playlist(REF) is None
    assert BeatportPlaylistSource(fetch=lambda ref: _listed([])).refresh_playlist(REF) is None


def test_who_refreshes_and_who_gets_the_pipeline():
    live = {'source': 'beatport', 'source_playlist_id': REF}
    chart = {'source': 'beatport', 'source_playlist_id': 'chart:weekend-picks:905427'}
    old = {'source': 'beatport', 'source_playlist_id': 'a1b2c3d4'}
    release = {'source': 'beatport', 'source_playlist_id': 'release:the-jump-ep:7473727'}
    for pl in (live, chart):
        assert is_refreshable_mirror(pl) and has_upstream(pl) and pipeline_runs(pl)
    for pl in (old, release):
        assert not is_refreshable_mirror(pl) and not has_upstream(pl) and not pipeline_runs(pl)
    assert not has_upstream({'source': 'file'}) and not pipeline_runs({'source': 'file'})
    assert not has_upstream({'source': 'soulsync'}) and pipeline_runs({'source': 'soulsync'})
    assert has_upstream({'source': 'spotify'}) and pipeline_runs({'source': 'spotify'})
    assert not is_refreshable_mirror({'source': 'spotify', 'source_playlist_id': REF})


def _db_with_mirror(tmp_path):
    db = MusicDatabase(database_path=str(tmp_path / 'music.db'))
    pid = db.mirror_playlist(
        source='beatport', source_playlist_id=REF, name='Trance (Main Floor) Hype Top 10',
        tracks=[
            {'track_name': 'Sturm (Extended Mix)', 'artist_name': 'Talla 2XLC',
             'source_track_id': 'beatport_1',
             'extra_data': {'discovered': True, 'provider': 'deezer',
                            'matched_data': {'id': '3317', 'name': 'Sturm (Extended Mix)'}}},
            {'track_name': 'Gone (Extended Mix)', 'artist_name': 'X', 'source_track_id': 'beatport_2'},
        ],
        profile_id=1, description='https://www.beatport.com/genre/trance-main-floor/7')
    return db, pid


def _registry(fetch):
    reg = PlaylistSourceRegistry()
    reg.register('beatport', lambda: BeatportPlaylistSource(fetch=fetch))
    return reg


def test_refresh_follows_the_chart_and_keeps_identification(tmp_path):
    db, pid = _db_with_mirror(tmp_path)
    fetched = []

    def fetch(ref):
        fetched.append(ref)
        return _listed([_bp_track(1, 'Sturm'), _bp_track(3, 'New One', isrc='GBAAA2600001')])

    deps = _build_deps(get_database=lambda: db, playlist_source_registry=_registry(fetch))
    result = auto_refresh_mirrored({'playlist_id': pid}, deps)
    assert result['status'] == 'completed' and result['refreshed'] == '1'
    assert fetched == [REF]
    rows = db.get_mirrored_playlist_tracks(pid)
    assert [r['source_track_id'] for r in rows] == ['beatport_1', 'beatport_3']
    kept = json.loads(rows[0]['extra_data'])
    assert kept['discovered'] is True and kept['matched_data']['id'] == '3317'
    assert not rows[1]['extra_data']
    assert rows[1]['album_name'] == 'New One EP'


def test_an_old_hash_mirror_is_left_alone(tmp_path):
    db = MusicDatabase(database_path=str(tmp_path / 'music.db'))
    pid = db.mirror_playlist(source='beatport', source_playlist_id='a1b2c3d4', name='Old Chart',
                             tracks=[{'track_name': 'A', 'artist_name': 'B', 'source_track_id': 'beatport_chart_0'}],
                             profile_id=1)
    fetched = []
    deps = _build_deps(get_database=lambda: db,
                       playlist_source_registry=_registry(lambda ref: fetched.append(ref)))
    result = auto_refresh_mirrored({'playlist_id': pid}, deps)
    assert fetched == []
    assert result['errors'] == '0'
    assert [r['track_name'] for r in db.get_mirrored_playlist_tracks(pid)] == ['A']


def test_a_dead_beatport_keeps_the_mirror(tmp_path):
    db, pid = _db_with_mirror(tmp_path)
    deps = _build_deps(get_database=lambda: db, playlist_source_registry=_registry(lambda ref: None))
    result = auto_refresh_mirrored({'playlist_id': pid}, deps)
    assert result['errors'] == '1'
    assert len(db.get_mirrored_playlist_tracks(pid)) == 2


def test_the_real_registry_has_beatport():
    from core.playlists.sources.bootstrap import build_playlist_source_registry
    reg = build_playlist_source_registry(
        spotify_client_getter=lambda: None, tidal_client_getter=lambda: None,
        qobuz_client_getter=lambda: None, deezer_client_getter=lambda: None)
    assert isinstance(reg.get_source('beatport'), BeatportPlaylistSource)
