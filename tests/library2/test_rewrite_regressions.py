"""Cross-boundary regressions found by the 2026-08-13 rewrite audit."""

from __future__ import annotations

import json

from database.music_database import MusicDatabase
from tests.support.catalogue_seed import seed_album, seed_artist, seed_track


def _db(tmp_path):
    return MusicDatabase(str(tmp_path / "music.db"))


def test_public_api_projects_native_fields(tmp_path):
    db = _db(tmp_path)
    with db._get_connection() as conn:
        artist = conn.execute(
            "INSERT INTO lib2_artists(name,image_url,spotify_id,external_ids,enrichment) "
            "VALUES('Rone','artist.jpg','sp-a','{\"deezer\":\"dz-a\"}',"
            "'{\"lastfm\":{\"listeners\":12}}')").lastrowid
        album = conn.execute(
            "INSERT INTO lib2_albums(primary_artist_id,title,image_url,album_type,spotify_id) "
            "VALUES(?,'Tohu Bohu','album.jpg','ep','sp-al')", (artist,)).lastrowid
        track = conn.execute(
            "INSERT INTO lib2_tracks(album_id,title,external_ids) "
            "VALUES(?,'Bora','{\"itunes\":\"it-t\"}')", (album,)).lastrowid
        conn.execute(
            "INSERT INTO lib2_provider_attempts(entity_type,entity_id,service,status) "
            "VALUES('artist',?,'deezer','matched')", (artist,))

    art, release, song = db.api_get_artist(artist), db.api_get_album(album), db.api_get_track(track)
    listed = db.get_library_artists()['artists'][0]
    assert (art['thumb_url'], art['spotify_artist_id'], art['deezer_id'],
            art['lastfm_listeners'], art['deezer_match_status']) == (
                'artist.jpg', 'sp-a', 'dz-a', 12, 'matched')
    assert (release['artist_id'], release['thumb_url'], release['record_type'],
            release['spotify_album_id']) == (artist, 'album.jpg', 'ep', 'sp-al')
    assert (song['artist_id'], song['itunes_track_id']) == (artist, 'it-t')
    assert (listed['thumb_url'], listed['spotify_artist_id'], listed['deezer_id']) == (
        'artist.jpg', 'sp-a', 'dz-a')


def test_catalogue_and_server_ids_cannot_collide(tmp_path):
    db = _db(tmp_path)
    with db._get_connection() as conn:
        artist = seed_artist(conn, server_id='a', name='A')
        album = seed_album(conn, server_id='al', title='Album', artist_id=artist)
        conn.execute("INSERT INTO lib2_tracks(id,album_id,title,server_source,server_id) "
                     "VALUES(10,?,'Catalogue','plex','99')", (album,))
        seed_track(conn, server_id='10', title='Server', album_id=album,
                   artist_id=artist)

    assert db.get_track_by_id(10).title == 'Catalogue'
    assert db.get_track_by_server_id('10', 'plex').title == 'Server'
    assert db.search_tracks(title='Server', server_source='plex')[0].id == '10'


def test_server_cleanup_detaches_only_its_contribution(tmp_path):
    db = _db(tmp_path)
    with db._get_connection() as conn:
        artist = seed_artist(conn, server_id='a', name='A')
        album = seed_album(conn, server_id='al', title='Album', artist_id=artist)
        track = seed_track(conn, server_id='t', title='Song', album_id=album,
                           artist_id=artist, file_path='/scan/song.flac')
        conn.execute("UPDATE lib2_artists SET external_ids='{\"deezer\":\"d1\"}' WHERE id=?",
                     (artist,))
        conn.execute("INSERT INTO lib2_track_files(track_id,path,source,is_primary) "
                     "VALUES(?, '/keeper/song.flac', 'soulseek', 0)", (track,))
        conn.execute("INSERT INTO lib2_monitor_rules(entity_type,entity_id,monitored,provenance) "
                     "VALUES('artist',?,1,'user_explicit')", (artist,))

    db.clear_server_data('plex')

    with db._get_connection() as conn:
        assert tuple(conn.execute(
            "SELECT external_ids,server_id FROM lib2_artists WHERE id=?",
            (artist,)).fetchone()) == ('{"deezer":"d1"}', None)
        assert tuple(conn.execute(
            "SELECT origin,server_id FROM lib2_albums WHERE id=?",
            (album,)).fetchone()) == ('library', None)
        assert conn.execute("SELECT server_id FROM lib2_tracks WHERE id=?",
                            (track,)).fetchone()[0] is None
        assert [tuple(row) for row in conn.execute(
            "SELECT path,file_state FROM lib2_track_files WHERE track_id=? ORDER BY id",
            (track,))] == [('/scan/song.flac', 'active'),
                           ('/keeper/song.flac', 'active')]
        assert conn.execute("SELECT monitored FROM lib2_monitor_rules WHERE entity_id=?",
                            (artist,)).fetchone()[0] == 1


def test_orphan_cleanup_only_detaches_server_mapping(tmp_path):
    db = _db(tmp_path)
    with db._get_connection() as conn:
        artist = seed_artist(conn, server_id='a', name='Wanted')
        album = seed_album(conn, server_id='al', title='Missing', artist_id=artist,
                           origin='discography')

    result = db.cleanup_orphaned_records()

    with db._get_connection() as conn:
        assert conn.execute("SELECT server_id FROM lib2_albums WHERE id=?", (album,)).fetchone()[0] is None
        assert conn.execute("SELECT server_id FROM lib2_artists WHERE id=?", (artist,)).fetchone()[0] is None
        assert conn.execute("SELECT COUNT(*) FROM lib2_albums WHERE id=?", (album,)).fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM lib2_artists WHERE id=?", (artist,)).fetchone()[0] == 1
    assert result['orphaned_albums_removed'] == 1


def test_duplicate_merge_preserves_structured_and_user_state(tmp_path):
    db = _db(tmp_path)
    with db._get_connection() as conn:
        conn.execute("INSERT INTO quality_profiles(id,name) VALUES(7,'Hi-Res')")
        keeper = conn.execute(
            "INSERT INTO lib2_artists(name,name_key,server_source,server_id,spotify_id,"
            "external_ids,enrichment) VALUES('Rone','rone','plex','1','sp','{\"deezer\":\"d\"}',"
            "'{\"lastfm\":{\"listeners\":1}}')").lastrowid
        donor = conn.execute(
            "INSERT INTO lib2_artists(name,name_key,server_source,server_id,external_ids,"
            "enrichment,monitored,quality_profile_id,quality_profile_explicit) "
            "VALUES('Rone','rone','plex','2','{\"itunes\":\"i\"}',"
            "'{\"genius\":{\"description\":\"bio\"}}',1,7,1)").lastrowid
        conn.execute("INSERT INTO lib2_monitor_rules(entity_type,entity_id,monitored,provenance) "
                     "VALUES('artist',?,1,'user_explicit')", (donor,))
        conn.execute("INSERT INTO lib2_metadata_overrides(entity_type,entity_id,field_name,value_json) "
                     "VALUES('artist',?,'name','\"User name\"')", (donor,))
        conn.execute("INSERT INTO library_provider_snapshots(provider,entity_type,entity_id,scope,"
                     "is_complete,parser_version,payload_hash,payload_json) "
                     "VALUES('spotify','artist',?,'metadata',1,'1','hash','{}')", (donor,))

    assert db.merge_duplicate_artists()['artists_merged'] == 1
    with db._get_connection() as conn:
        row = conn.execute("SELECT external_ids,enrichment,monitored,quality_profile_id,"
                           "quality_profile_explicit FROM lib2_artists WHERE id=?", (keeper,)).fetchone()
        assert json.loads(row[0]) == {'deezer': 'd', 'itunes': 'i'}
        assert set(json.loads(row[1])) == {'lastfm', 'genius'}
        assert tuple(row[2:]) == (1, 7, 1)
        assert conn.execute("SELECT entity_id FROM lib2_monitor_rules").fetchone()[0] == keeper
        assert conn.execute("SELECT entity_id FROM lib2_metadata_overrides").fetchone()[0] == keeper
        assert conn.execute("SELECT entity_id FROM library_provider_snapshots").fetchone()[0] == keeper


def test_playlist_status_does_not_count_provider_catalogue(tmp_path):
    db = _db(tmp_path)
    playlist = db.mirror_playlist(
        source='spotify', source_playlist_id='p', name='P',
        tracks=[{'track_name': 'Song', 'artist_name': 'A'}])
    with db._get_connection() as conn:
        artist = conn.execute("INSERT INTO lib2_artists(name) VALUES('A')").lastrowid
        album = conn.execute("INSERT INTO lib2_albums(primary_artist_id,title,origin) "
                             "VALUES(?,'Album','library')", (artist,)).lastrowid
        conn.execute("INSERT INTO lib2_tracks(album_id,title) VALUES(?,'Song')", (album,))

    assert db.get_mirrored_playlist_status_counts(playlist)['in_library'] == 0
    assert db.get_all_mirrored_playlist_status_counts()[playlist]['in_library'] == 0
