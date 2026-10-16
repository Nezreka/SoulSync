"""dated snapshots of a beatport list.

a mirrored chart follows the live list, so last week's tracks drop out. a
snapshot keeps today's list as its own mirror that never refreshes, and still
goes through identify / sync / download like any playlist.
"""

from flask import Flask

from core.automation.handlers.refresh_mirrored import auto_refresh_mirrored
from core.beatport.playlist import save_snapshot
from core.beatport.refs import parse_snapshot, snapshot_id
from core.playlists.sources.beatport import BeatportPlaylistSource, is_snapshot_mirror
from core.playlists.sources.registry import PlaylistSourceRegistry
from core.playlists.user_playlists import has_upstream, pipeline_runs
from database.music_database import MusicDatabase
from tests.automation.test_handlers_playlist import _build_deps

REF = 'genre:trance-main-floor:7:hype-10'


def _listed(titles):
    return {'ref': REF, 'name': 'Trance (Main Floor) Hype Top 10', 'image': 'http://img',
            'url': 'https://www.beatport.com/genre/trance-main-floor/7', 'creator': 'Beatport',
            'tracks': [{'id': f'beatport_{i}', 'beatport_id': i, 'title': t, 'mix_name': 'Extended Mix',
                        'artists': [{'name': 'A'}], 'release_name': t, 'duration_ms': 1000}
                       for i, t in enumerate(titles, 1)]}


def test_snapshot_ids():
    assert snapshot_id(REF, '2026-10-16') == REF + '@2026-10-16'
    assert snapshot_id('junk', '2026-10-16') is None
    assert snapshot_id(REF, 'today') is None
    ref, day = parse_snapshot(REF + '@2026-10-16')
    assert str(ref) == REF and day == '2026-10-16'
    assert parse_snapshot(REF) is None and parse_snapshot('junk@2026-10-16') is None


def test_save_snapshot(tmp_path):
    db = MusicDatabase(database_path=str(tmp_path / 'm.db'))
    saved = save_snapshot(db, REF, 1, today='2026-10-16', fetch=lambda r: _listed(['Sturm', 'Stay']))
    assert saved['name'] == 'Trance (Main Floor) Hype Top 10 · 2026-10-16' and saved['track_count'] == 2
    mirror = db.get_mirrored_playlist_by_source('beatport', REF + '@2026-10-16', 1)
    assert mirror['name'] == saved['name']
    rows = db.get_mirrored_playlist_tracks(mirror['id'])
    assert [r['track_name'] for r in rows] == ['Sturm (Extended Mix)', 'Stay (Extended Mix)']
    # again the same day: that day's copy, not a second one
    again = save_snapshot(db, REF, 1, today='2026-10-16', fetch=lambda r: _listed(['Sturm']))
    assert again['playlist_id'] == saved['playlist_id']
    assert len(db.get_mirrored_playlists(profile_id=1)) == 1
    # another day is another snapshot
    save_snapshot(db, REF, 1, today='2026-10-23', fetch=lambda r: _listed(['New']))
    assert len(db.get_mirrored_playlists(profile_id=1)) == 2


def test_junk_or_dead_is_none(tmp_path):
    db = MusicDatabase(database_path=str(tmp_path / 'm.db'))
    assert save_snapshot(db, 'a1b2c3', 1, fetch=lambda r: _listed(['x'])) is None
    assert save_snapshot(db, REF, 1, fetch=lambda r: None) is None
    assert save_snapshot(db, REF, 1, fetch=lambda r: _listed([])) is None


def test_frozen_but_still_pipelined():
    snap = {'source': 'beatport', 'source_playlist_id': REF + '@2026-10-16'}
    assert is_snapshot_mirror(snap)
    assert not has_upstream(snap)
    assert pipeline_runs(snap)
    assert not is_snapshot_mirror({'source': 'beatport', 'source_playlist_id': REF})


def test_refresh_leaves_a_snapshot_alone(tmp_path):
    db = MusicDatabase(database_path=str(tmp_path / 'm.db'))
    saved = save_snapshot(db, REF, 1, today='2026-10-16', fetch=lambda r: _listed(['Sturm']))
    fetched = []
    reg = PlaylistSourceRegistry()
    reg.register('beatport', lambda: BeatportPlaylistSource(fetch=lambda r: fetched.append(r) or _listed(['Other'])))
    deps = _build_deps(get_database=lambda: db, playlist_source_registry=reg)
    auto_refresh_mirrored({'playlist_id': saved['playlist_id']}, deps)
    assert fetched == []
    assert [r['track_name'] for r in db.get_mirrored_playlist_tracks(saved['playlist_id'])] == ['Sturm (Extended Mix)']


def test_route(tmp_path, monkeypatch):
    import api.beatport as beatport_api
    import core.beatport.playlist as pl
    import database.music_database as mdb
    import core.profile_context as pc
    db = MusicDatabase(database_path=str(tmp_path / 'm.db'))
    monkeypatch.setattr(mdb, 'get_database', lambda: db)
    monkeypatch.setattr(pc, 'get_current_profile_id', lambda: 1)
    real = pl.save_snapshot
    monkeypatch.setattr(pl, 'save_snapshot',
                        lambda d, ref, pid: real(d, ref, pid, today='2026-10-16', fetch=lambda r: _listed(['Sturm'])))
    app = Flask(__name__)
    app.register_blueprint(beatport_api.bp)
    client = app.test_client()
    ok = client.post('/api/beatport/snapshot', json={'ref': REF}).get_json()
    assert ok['success'] is True and ok['name'].endswith('· 2026-10-16')
    bad = client.post('/api/beatport/snapshot', json={'ref': 'nope'})
    assert bad.status_code == 400 and bad.get_json()['success'] is False
