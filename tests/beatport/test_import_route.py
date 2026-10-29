"""importing a beatport list into soulsync (the playlist import flow).

a list click used to start an identify in a beatport-only window, which made
the live mirror as a side effect. now importing makes that same mirror on
purpose, keyed by the canonical ref so it refreshes with the chart, starts
its background identify, and answers the mirror's id to land on."""

from flask import Flask

import api.beatport as beatport_api
import core.beatport.fetch as bfetch
import core.playlists.auto_identify as auto_identify
import core.profile_context as pc
import database.music_database as mdb
from database.music_database import MusicDatabase

REF = 'genre:trance-main-floor:7:hype-10'


def _listed():
    return {'ref': REF, 'name': 'Trance (Main Floor) Hype Top 10', 'image': 'http://img',
            'url': 'https://www.beatport.com/genre/trance-main-floor/7', 'creator': 'Beatport',
            'tracks': [{'id': 'beatport_1', 'beatport_id': 1, 'title': 'Sturm', 'mix_name': 'Extended Mix',
                        'artists': [{'name': 'A'}], 'release_name': 'Sturm', 'duration_ms': 1000}]}


def _client(tmp_path, monkeypatch, listed=_listed):
    db = MusicDatabase(database_path=str(tmp_path / 'm.db'))
    monkeypatch.setattr(mdb, 'get_database', lambda: db)
    monkeypatch.setattr(pc, 'get_current_profile_id', lambda: 1)
    monkeypatch.setattr(bfetch, 'fetch_list', lambda ref, **kw: listed())
    identified = []
    monkeypatch.setattr(auto_identify, 'request_if_unmatched',
                        lambda pid, tracks: identified.append(pid) or True)
    app = Flask(__name__)
    app.register_blueprint(beatport_api.bp)
    return app.test_client(), db, identified


def test_import_makes_the_live_mirror_and_identifies_it(tmp_path, monkeypatch):
    client, db, identified = _client(tmp_path, monkeypatch)
    body = client.post('/api/beatport/import', json={'ref': REF}).get_json()
    assert body['success'] is True
    assert body['source_playlist_id'] == REF
    mirror = db.get_mirrored_playlist(body['playlist_id'])
    assert mirror['source'] == 'beatport' and mirror['source_playlist_id'] == REF
    assert db.get_mirrored_playlist_tracks(body['playlist_id'])[0]['source_track_id'] == 'beatport_1'
    assert identified == [body['playlist_id']]


def test_importing_again_updates_the_same_mirror(tmp_path, monkeypatch):
    client, _db, _ = _client(tmp_path, monkeypatch)
    first = client.post('/api/beatport/import', json={'ref': REF}).get_json()
    again = client.post('/api/beatport/import', json={'ref': REF}).get_json()
    assert again['playlist_id'] == first['playlist_id']


def test_junk_refs_and_silent_beatport_say_so(tmp_path, monkeypatch):
    client, _db, identified = _client(tmp_path, monkeypatch, listed=lambda: None)
    bad = client.post('/api/beatport/import', json={'ref': 'not a ref'})
    assert bad.status_code == 400 and bad.get_json()['success'] is False
    silent = client.post('/api/beatport/import', json={'ref': REF})
    assert silent.status_code == 502 and silent.get_json()['success'] is False
    assert identified == []
