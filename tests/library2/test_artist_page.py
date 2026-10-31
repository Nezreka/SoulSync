"""The full artist page reads the same native ownership as Library v2."""
from database.music_database import MusicDatabase
from tests import lib2_seed


def test_native_artist_page_counts_owned_files_and_keeps_provider_identity(tmp_path):
    from core.library2.queries import get_artist
    db = MusicDatabase(str(tmp_path / 'm.db'))
    with db._get_connection() as conn:
        lib2_seed.track(conn, 'Artist', 'Owned', 'Song', credit='Artist', album_cols={'spotify_id': 'provider-album'})
        aid = lib2_seed.artist(conn, 'Artist', spotify_id='provider-artist')
        lib2_seed.track(conn, 'Artist', 'Missing', 'Missing song', owned=False)
        conn.commit()
        result = get_artist(conn, aid)
    assert result['id'] == aid
    assert result['provider_ids']['spotify'] == 'provider-artist'
    assert [a['title'] for a in result['albums'] if a['tracks_present']] == ['Owned']
    assert result['albums'][0]['tracks_present'] == 1


def test_artist_page_ownership_is_scoped_to_selected_library(tmp_path, monkeypatch):
    from core.library2.queries import get_artist
    from core.library_scope import library_scope
    monkeypatch.setattr('core.library_scope.any_own_library_exists', lambda: True)
    db = MusicDatabase(str(tmp_path / 'm.db'))
    with db._get_connection() as conn:
        lib2_seed.track(conn, 'Artist', 'Private', 'Song', credit='Artist')
        conn.execute('UPDATE lib2_track_files SET owner_profile_id=7')
        aid = lib2_seed.artist(conn, 'Artist')
        conn.commit()
        with library_scope('shared'):
            assert all(a['tracks_present'] == 0 for a in get_artist(conn, aid)['albums'])


def test_provider_page_keeps_qualified_provider_id_even_when_catalogue_knows_artist(tmp_path, monkeypatch):
    from flask import Flask, jsonify
    from api import artist_detail
    db = MusicDatabase(str(tmp_path / 'm.db'))
    with db._get_connection() as conn:
        native_id = lib2_seed.artist(conn, 'Correct', spotify_id='42')
        lib2_seed.track(conn, 'Correct', 'Owned', 'Song', credit='Correct')
        conn.commit()
    monkeypatch.setattr(artist_detail, 'get_database', lambda: db, raising=False)
    monkeypatch.setattr(artist_detail, '_mark_request_free_ok_for_spotify', lambda *_: None)
    def provider(pid, name, source):
        assert (pid, name, source) == ('42', 'Correct', 'spotify')
        return jsonify(success=True, artist={'id': pid, 'name': name},
                       discography={'source': source, 'albums': [{'id': 'sp-album', 'owned': None}]}), 200
    monkeypatch.setattr(artist_detail, '_build_source_only_artist_detail', provider)
    app = Flask(__name__)
    app.register_blueprint(artist_detail.bp)
    response = app.test_client().get('/api/artist-detail/42?source=spotify&name=Correct')
    assert response.status_code == 200
    body = response.get_json()
    assert native_id != 42
    assert body['artist']['id'] == '42'
    assert body['discography']['albums'][0]['id'] == 'sp-album'


def test_appears_on_uses_native_credits_and_scoped_files(tmp_path, monkeypatch):
    from core.library2.artist_page import appears_on_payload
    from core.library_scope import library_scope
    monkeypatch.setattr('core.library_scope.any_own_library_exists', lambda: True)
    db = MusicDatabase(str(tmp_path / 'm.db'))
    with db._get_connection() as conn:
        tid = lib2_seed.track(conn, 'Host', 'Guest appearance', 'Song', credit='Host')
        guest = lib2_seed.artist(conn, 'Guest')
        conn.execute("INSERT INTO lib2_track_artists(track_id,artist_id,role) VALUES (?,?,'featured')", (tid, guest))
        conn.commit()
        result = appears_on_payload(conn, guest)
        assert result['tracks'][0]['lib2_track_id'] == tid
        assert result['tracks'][0]['file_path']
        assert result['albums'] == []
        conn.execute('UPDATE lib2_track_files SET owner_profile_id=7')
        with library_scope('shared'):
            # another library's file is not played here: the track the shared
            # library still wants is listed without one
            scoped = appears_on_payload(conn, guest)
            assert scoped['albums'] == []
            assert [(t['file_path'], t['wanted']) for t in scoped['tracks']] == [(None, True)]
            conn.execute('UPDATE lib2_tracks SET monitored=0 WHERE id=?', (tid,))
            assert appears_on_payload(conn, guest) == {'success': True, 'albums': [], 'tracks': []}



def test_appears_on_lists_a_wanted_guest_track_that_is_not_here_yet(tmp_path):
    # Rockwell on a monitored Michael Jackson compilation: the wanted track is
    # what puts him in the library, so his page shows it, with nothing to play
    from core.library2.artist_page import appears_on_payload
    db = MusicDatabase(str(tmp_path / 'm.db'))
    with db._get_connection() as conn:
        wanted = lib2_seed.track(conn, 'Host', 'Scream', 'Wanted song', owned=False, monitored=1)
        unwanted = lib2_seed.track(conn, 'Host', 'Scream', 'Unwanted song', owned=False, monitored=0)
        guest = lib2_seed.artist(conn, 'Guest')
        for tid in (wanted, unwanted):
            conn.execute("INSERT INTO lib2_track_artists(track_id,artist_id,role) VALUES (?,?,'featured')",
                         (tid, guest))
        conn.commit()
        result = appears_on_payload(conn, guest)
    assert [(t['lib2_track_id'], t['file_path'], t['wanted']) for t in result['tracks']] == [
        (wanted, None, True)]


def test_appears_on_route_includes_collaborative_album_and_manual_projection(tmp_path, monkeypatch):
    from flask import Flask
    from api import artist_detail
    from core.library2.metadata_overrides import set_field_override
    db = MusicDatabase(str(tmp_path / 'm.db'))
    with db._get_connection() as conn:
        tid = lib2_seed.track(conn, 'Host', 'Collaboration', 'Provider title', credit='Host')
        guest = lib2_seed.artist(conn, 'Guest')
        alias = lib2_seed.artist(conn, 'Guest alias', canonical_artist_id=guest)
        album = conn.execute('SELECT album_id FROM lib2_tracks WHERE id=?', (tid,)).fetchone()[0]
        conn.execute("INSERT INTO lib2_album_artists(album_id,artist_id,role) VALUES (?,?,'primary')", (album, alias))
        set_field_override(conn, entity_type='track', entity_id=tid, field_name='title', value='My title')
        conn.commit()
    monkeypatch.setattr(artist_detail, 'get_database', lambda: db)
    app = Flask(__name__)
    app.register_blueprint(artist_detail.bp)
    response = app.test_client().get(f'/api/artist/{guest}/appears-on')
    assert response.status_code == 200
    body = response.get_json()
    assert body['tracks'] == []
    assert body['albums'][0]['id'] == album
    assert body['albums'][0]['tracks'][0]['title'] == 'My title'
    assert body['albums'][0]['tracks'][0]['lib2_track_id'] == tid
