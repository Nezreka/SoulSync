"""Media servers map existing Library-v2 rows; only imports create them."""

from __future__ import annotations


import pytest

from core.library2.media_server_sync import (
    _upsert_file, resolve_album, resolve_artist, upsert_album, upsert_artist,
    upsert_track,
)
from core.library2.schema import ensure_library_v2_schema
from core.library2.track_files import set_primary_file
from tests.lib2_seed import row_conn


@pytest.fixture()
def cur():
    conn = row_conn(":memory:")
    ensure_library_v2_schema(conn)
    yield conn.cursor()
    conn.close()


def _imported(cur, *, origin="library", path="/music/song.flac"):
    artist = cur.execute(
        "INSERT INTO lib2_artists(name,name_key,image_url) VALUES('Muse','muse','provider.jpg')"
    ).lastrowid
    album = cur.execute(
        "INSERT INTO lib2_albums(primary_artist_id,title,origin) VALUES(?,'Absolution',?)",
        (artist, origin),
    ).lastrowid
    track = cur.execute(
        "INSERT INTO lib2_tracks(album_id,title,track_number,disc_number) "
        "VALUES(?,'Time Is Running Out',4,1)", (album,),
    ).lastrowid
    file_id = cur.execute(
        "INSERT INTO lib2_track_files(track_id,path,is_primary,file_state,size) "
        "VALUES(?,?,1,'active',4096)", (track, path),
    ).lastrowid
    return artist, album, track, file_id


def test_unknown_server_entities_never_create_catalogue_rows(cur):
    assert upsert_artist(
        cur, server_source="plex", server_id="7", name="Unknown"
    ) is None
    assert upsert_album(
        cur, server_source="plex", server_id="70", artist_id=999, title="Unknown"
    ) is None
    assert upsert_track(
        cur, server_source="plex", server_id="700", album_id=999,
        artist_id=999, title="Unknown", file_path="/media/unknown.flac",
    ) is None
    assert cur.execute("SELECT COUNT(*) FROM lib2_artists").fetchone()[0] == 0
    assert cur.execute("SELECT COUNT(*) FROM lib2_albums").fetchone()[0] == 0
    assert cur.execute("SELECT COUNT(*) FROM lib2_tracks").fetchone()[0] == 0
    assert cur.execute("SELECT COUNT(*) FROM lib2_track_files").fetchone()[0] == 0


@pytest.mark.parametrize('server', ['navidrome', 'plex', 'jellyfin'])
def test_server_can_recognize_an_owned_featured_artist_without_solo_albums(cur, server):
    _, _, track, _ = _imported(cur)
    featured = cur.execute("INSERT INTO lib2_artists(name,name_key) VALUES('GIVĒON','givēon')").lastrowid
    cur.execute("INSERT INTO lib2_track_artists(track_id,artist_id,role,position) VALUES(?,?,'featured',1)",
                (track, featured))
    assert upsert_artist(cur, server_source=server, server_id='featured-id', name='GIVĒON') == featured


@pytest.mark.parametrize('server,credit_key,credit', [
    ('navidrome', 'artists', {'id': 'giveon-id', 'name': 'GIVĒON'}),
    ('jellyfin', 'ArtistItems', {'Id': 'giveon-id', 'Name': 'GIVĒON'}),
])
def test_track_scan_recognizes_existing_featured_artist_ids(tmp_path, server, credit_key, credit):
    from types import SimpleNamespace
    from database.music_database import MusicDatabase
    from tests.support.catalogue_seed import seed_library_track
    db = MusicDatabase(str(tmp_path / 'credits.db'))
    path = tmp_path / 'owned.flac'
    path.write_bytes(b'owned audio')
    with db._get_connection() as conn:
        tid = seed_library_track(conn, artist='Joji', album='Piss In The Wind',
                                 title='Piece of You (with Giveon)', file_path=str(path), server_source='soulsync')
        featured = conn.execute("INSERT INTO lib2_artists(name,name_key) VALUES('GIVĒON','givēon')").lastrowid
        conn.execute("INSERT INTO lib2_track_artists(track_id,artist_id,role,position) VALUES(?,?,'featured',1)",
                     (tid, featured))
    observed = SimpleNamespace(ratingKey='song-id', title='Piece of You (with Giveon)', path=str(path),
                               _data={credit_key: [credit, {'id': 'unknown', 'name': 'Not Credited'}]})
    assert db.insert_or_update_media_track(observed, 'unmapped-album', 'joji-id', server_source=server)
    with db._get_connection() as conn:
        row = conn.execute("SELECT entity_id FROM lib2_media_server_mappings WHERE entity_type='artist' AND server_source=? AND server_id='giveon-id'", (server,)).fetchone()
        assert row is not None and row[0] == featured
        assert conn.execute('SELECT COUNT(*) FROM lib2_artists').fetchone()[0] == 2


def test_full_refresh_keeps_featured_artist_observed_only_on_a_song(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from database.music_database import MusicDatabase
    from core.database_update_worker import DatabaseUpdateWorker
    from tests.support.catalogue_seed import seed_library_track
    db = MusicDatabase(str(tmp_path / 'featured_refresh.db'))
    path = tmp_path / 'owned.flac'
    path.write_bytes(b'owned audio')
    with db._get_connection() as conn:
        tid = seed_library_track(conn, artist='Joji', album='Album', title='Song',
                                 file_path=str(path), server_source='soulsync')
        featured = conn.execute("INSERT INTO lib2_artists(name,name_key) VALUES('GIVĒON','givēon')").lastrowid
        conn.execute("INSERT INTO lib2_track_artists(track_id,artist_id,role,position) VALUES(?,?,'featured',1)",
                     (tid, featured))
    track = SimpleNamespace(ratingKey='song-id', title='Song', path=str(path),
                            _data={'artists': [{'id': 'giveon-id', 'name': 'GIVĒON'}]})
    album = SimpleNamespace(ratingKey='album-id', title='Album', tracks=lambda: [track])
    artist = SimpleNamespace(ratingKey='joji-id', title='Joji', albums=lambda: [album])
    client = SimpleNamespace(last_fetch_failed=False)
    def verified_ids(ids):
        client.last_fetch_failed = False
        return ids
    client.get_all_artist_ids = lambda: verified_ids({'joji-id'})
    client.get_all_album_ids = lambda: verified_ids({'album-id'})
    worker = DatabaseUpdateWorker(client, database_path=str(db.database_path), server_type='navidrome')
    worker.database = db
    monkeypatch.setattr(worker, '_can_detach_server_mappings', lambda *a, **kw: True)
    worker._process_artist_with_content(artist)
    worker._detect_and_remove_stale_content()
    with db._get_connection() as conn:
        assert conn.execute("SELECT entity_id FROM lib2_media_server_mappings WHERE entity_type='artist' AND server_id='giveon-id'").fetchone()[0] == featured


@pytest.mark.parametrize('server', ['navidrome', 'plex', 'jellyfin'])
@pytest.mark.parametrize('evidence', ['path', 'recording_id'])
def test_track_recognition_survives_unmapped_edition_album(tmp_path, server, evidence):
    from types import SimpleNamespace
    from database.music_database import MusicDatabase
    from tests.support.catalogue_seed import seed_library_track

    db = MusicDatabase(database_path=str(tmp_path / 'recognition.db'))
    path = tmp_path / 'owned.flac'
    path.write_bytes(b'owned audio')
    with db._get_connection() as conn:
        tid = seed_library_track(conn, artist='Muse', album='Absolution',
                                 title='Time Is Running Out', file_path=str(path),
                                 duration=180000, musicbrainz_id='recording-id',
                                 server_source='soulsync')
        conn.execute('UPDATE lib2_tracks SET server_source=NULL,server_id=NULL WHERE id=?', (tid,))
    observed = SimpleNamespace(ratingKey='live-song', title='Time Is Running Out',
                               duration=180000, trackNumber=4, discNumber=1,
                               path=str(path) if evidence == 'path' else '/old/edition/song.flac',
                               musicBrainzId='recording-id' if evidence == 'recording_id' else None)

    assert db.insert_or_update_media_track(observed, 'unknown-edition', 'unknown-artist',
                                           server_source=server)
    assert db.get_track_by_server_id('live-song', server).title == 'Time Is Running Out'
    with db._get_connection() as conn:
        assert conn.execute('SELECT COUNT(*) FROM lib2_tracks').fetchone()[0] == 1
        assert conn.execute('SELECT title FROM lib2_albums').fetchone()[0] == 'Absolution'
        assert conn.execute('SELECT path FROM lib2_track_files').fetchone()[0] == str(path)


@pytest.mark.parametrize('server', ['navidrome', 'plex', 'jellyfin'])
def test_scan_inspects_tracks_when_artist_and_album_labels_are_unmapped(tmp_path, server):
    from types import SimpleNamespace
    from database.music_database import MusicDatabase
    from core.database_update_worker import DatabaseUpdateWorker
    from tests.support.catalogue_seed import seed_library_track

    db = MusicDatabase(database_path=str(tmp_path / 'scan_recognition.db'))
    path = tmp_path / 'owned.flac'
    path.write_bytes(b'owned audio')
    with db._get_connection() as conn:
        seed_library_track(conn, artist='Muse', album='Absolution', title='Time Is Running Out',
                           file_path=str(path), server_source='soulsync')
    track = SimpleNamespace(ratingKey='live-song', title='Time Is Running Out', path=str(path))
    album = SimpleNamespace(ratingKey='edition-id', title='Absolution (Edition)', tracks=lambda: [track])
    artist = SimpleNamespace(ratingKey='artist-id', title='Muse & Guests', albums=lambda: [album])
    worker = DatabaseUpdateWorker(None, database_path=str(db.database_path), server_type=server)
    worker.database = db
    seen = set()
    worker._process_artist_with_content(artist, seen_track_ids=seen)
    assert seen == {'live-song'}
    assert db.get_track_by_server_id('live-song', server) is not None


@pytest.mark.parametrize('rejection', ['duration', 'ambiguous'])
def test_recording_id_cannot_map_an_ambiguous_or_different_recording(tmp_path, rejection):
    from types import SimpleNamespace
    from database.music_database import MusicDatabase
    from tests.support.catalogue_seed import seed_library_track

    db = MusicDatabase(database_path=str(tmp_path / 'unsafe_recognition.db'))
    with db._get_connection() as conn:
        seed_library_track(conn, artist='Muse', album='Absolution', title='Song',
                           file_path=str(tmp_path / 'one.flac'), duration=180000,
                           musicbrainz_id='recording-id', server_source='soulsync')
        if rejection == 'ambiguous':
            seed_library_track(conn, artist='Muse', album='Absolution', title='Song',
                               file_path=str(tmp_path / 'two.flac'), duration=180000,
                               musicbrainz_id='recording-id', server_source='soulsync')
    observed = SimpleNamespace(ratingKey='live', title='Song', path='/old/song.flac',
                               musicBrainzId='recording-id', trackNumber=1,
                               duration=240000 if rejection == 'duration' else 180000)
    assert db.insert_or_update_media_track(observed, 'unknown', 'unknown', server_source='navidrome') is False
    assert db.get_track_by_server_id('live', 'navidrome') is None


def test_server_maps_existing_imported_rows_and_preserves_catalogue_metadata(cur):
    artist, album, track, _file = _imported(cur)

    assert upsert_artist(
        cur, server_source="plex", server_id="7", name="Muse",
        image_url=None, genres_json=None,
    ) == artist
    assert upsert_album(
        cur, server_source="plex", server_id="70", artist_id=artist,
        title="absolution", track_count=12, duration=1000,
    ) == album
    assert upsert_track(
        cur, server_source="plex", server_id="700", album_id=album,
        artist_id=artist, title="Time Is Running Out", track_number=4,
        disc_number=1, file_path="/music/song.flac", file_size=5000,
        bitrate=1411,
    ) == track

    assert resolve_artist(cur, "plex", "7") == artist
    assert resolve_album(cur, "plex", "70") == album
    assert cur.execute(
        "SELECT image_url FROM lib2_artists WHERE id=?", (artist,)
    ).fetchone()[0] == "provider.jpg"
    assert cur.execute(
        "SELECT origin FROM lib2_albums WHERE id=?", (album,)
    ).fetchone()[0] == "library"
    file_row = cur.execute(
        "SELECT path,size,bitrate FROM lib2_track_files WHERE track_id=?", (track,)
    ).fetchone()
    assert tuple(file_row) == ("/music/song.flac", 5000, 1411)


def test_import_refresh_does_not_overwrite_user_locked_art(cur):
    """Port of dev's custom-art sync regression to the native catalogue."""
    artist, album, _track, _file = _imported(cur)
    cur.execute(
        "UPDATE lib2_artists SET image_url='custom-artist.jpg', art_locked=1 WHERE id=?",
        (artist,),
    )
    cur.execute(
        "UPDATE lib2_albums SET image_url='custom-album.jpg', art_locked=1 WHERE id=?",
        (album,),
    )

    upsert_artist(
        cur, server_source="soulsync", server_id="refresh-a", name="Muse",
        image_url="server-artist.jpg", allow_create=True,
    )
    # Match the existing release by title/artist as a refresh with a new source id.
    refreshed_album = upsert_album(
        cur, server_source="soulsync", server_id="refresh-al", artist_id=artist,
        title="Absolution", image_url="server-album.jpg", allow_create=True,
    )

    assert refreshed_album == album
    assert cur.execute(
        "SELECT image_url FROM lib2_artists WHERE id=?", (artist,)
    ).fetchone()[0] == "custom-artist.jpg"
    assert cur.execute(
        "SELECT image_url FROM lib2_albums WHERE id=?", (album,)
    ).fetchone()[0] == "custom-album.jpg"


def test_unlocked_native_art_follows_import_refresh(cur):
    artist, album, _track, _file = _imported(cur)
    upsert_artist(
        cur, server_source="soulsync", server_id="refresh-a", name="Muse",
        image_url="new-artist.jpg", allow_create=True,
    )
    upsert_album(
        cur, server_source="soulsync", server_id="refresh-al", artist_id=artist,
        title="Absolution", image_url="new-album.jpg", allow_create=True,
    )

    assert cur.execute(
        "SELECT image_url FROM lib2_artists WHERE id=?", (artist,)
    ).fetchone()[0] == "new-artist.jpg"
    assert cur.execute(
        "SELECT image_url FROM lib2_albums WHERE id=?", (album,)
    ).fetchone()[0] == "new-album.jpg"


def test_server_does_not_stamp_catalogue_only_release(cur):
    artist, album, track, file_id = _imported(cur, origin="discography")
    cur.execute("DELETE FROM lib2_track_files WHERE id=?", (file_id,))

    assert upsert_artist(
        cur, server_source="plex", server_id="7", name="Muse",
    ) is None
    assert upsert_album(
        cur, server_source="plex", server_id="70", artist_id=artist,
        title="Absolution",
    ) is None
    assert upsert_track(
        cur, server_source="plex", server_id="700", album_id=album,
        artist_id=artist, title="Time Is Running Out", track_number=4,
        file_path="/server/not-imported.flac",
    ) is None
    assert tuple(cur.execute(
        "SELECT origin,server_id FROM lib2_albums WHERE id=?", (album,)
    ).fetchone()) == ("discography", None)
    assert cur.execute("SELECT COUNT(*) FROM lib2_track_files").fetchone()[0] == 0


def test_server_path_never_replaces_or_adds_imported_file_evidence(cur):
    artist, album, track, _file = _imported(cur)
    upsert_artist(cur, server_source="plex", server_id="7", name="Muse")
    upsert_album(cur, server_source="plex", server_id="70", artist_id=artist,
                 title="Absolution")
    assert upsert_track(
        cur, server_source="plex", server_id="700", album_id=album,
        artist_id=artist, title="Time Is Running Out", track_number=4,
        disc_number=1, file_path="/server/moved.flac",
    ) == track
    assert [row[0] for row in cur.execute(
        "SELECT path FROM lib2_track_files WHERE track_id=?", (track,)
    )] == ["/music/song.flac"]


def test_import_pipeline_helpers_can_create_rows_and_keep_disc_identity(cur):
    artist = upsert_artist(
        cur, server_source="soulsync", server_id="a", name="Muse",
        allow_create=True,
    )
    album = upsert_album(
        cur, server_source="soulsync", server_id="al", artist_id=artist,
        title="Live", allow_create=True,
    )
    for server_id, disc in (("t1", 1), ("t2", 2)):
        upsert_track(
            cur, server_source="soulsync", server_id=server_id,
            album_id=album, artist_id=artist, title="Intro", track_number=1,
            disc_number=disc, file_path=f"/music/{disc}.flac", allow_create=True,
        )
    assert cur.execute("SELECT COUNT(*) FROM lib2_tracks").fetchone()[0] == 2
    assert cur.execute("SELECT COUNT(*) FROM lib2_track_files").fetchone()[0] == 2


def test_pipeline_file_has_ownership_provenance(cur):
    artist = upsert_artist(cur, server_source="soulsync", server_id="a", name="Muse",
                           allow_create=True)
    album = upsert_album(cur, server_source="soulsync", server_id="al", artist_id=artist,
                         title="Album", allow_create=True)
    track = upsert_track(
        cur, server_source="soulsync", server_id="tr", album_id=album,
        artist_id=artist, title="Song", file_path="/music/song.flac",
        allow_create=True, file_source="import",
    )
    assert tuple(cur.execute(
        "SELECT source,file_state FROM lib2_track_files WHERE track_id=?", (track,)
    ).fetchone()) == ("import", "active")


def test_file_observation_uses_quality_election_instead_of_last_seen(cur):
    _artist_id, _album_id, track_id, master_id = _imported(cur)
    cur.execute(
        "UPDATE lib2_track_files SET format='flac', bit_depth=24,"
        " sample_rate=96000, bitrate=3000 WHERE id=?", (master_id,),
    )

    _upsert_file(
        cur, track_id, "/music/song.opus", 1024, 192,
        server_source="soulsync", source="companion",
    )

    primary = cur.execute(
        "SELECT path FROM lib2_track_files WHERE track_id=? AND is_primary=1",
        (track_id,),
    ).fetchone()
    assert primary[0] == "/music/song.flac"


def test_file_observation_preserves_manual_primary(cur):
    _artist_id, _album_id, track_id, master_id = _imported(cur)
    cur.execute(
        "UPDATE lib2_track_files SET format='flac', bitrate=3000 WHERE id=?",
        (master_id,),
    )
    _upsert_file(
        cur, track_id, "/music/song.opus", 1024, 192,
        server_source="soulsync", source="companion",
    )
    derivative_id = cur.execute(
        "SELECT id FROM lib2_track_files WHERE path='/music/song.opus'"
    ).fetchone()[0]
    assert set_primary_file(cur, track_id, derivative_id)

    _upsert_file(
        cur, track_id, "/music/song.flac", 5000, 3000,
        server_source="soulsync", source="import",
    )

    primary = cur.execute(
        "SELECT id,primary_manual FROM lib2_track_files"
        " WHERE track_id=? AND is_primary=1", (track_id,),
    ).fetchone()
    assert tuple(primary) == (derivative_id, 1)


def test_pipeline_primary_artist_corrections_replace_junctions(cur):
    old = upsert_artist(cur, server_source="soulsync", server_id="old", name="Old",
                        allow_create=True)
    new = upsert_artist(cur, server_source="soulsync", server_id="new", name="New",
                        allow_create=True)
    album = upsert_album(cur, server_source="soulsync", server_id="al", artist_id=old,
                         title="Album", allow_create=True)
    track = upsert_track(cur, server_source="soulsync", server_id="tr", album_id=album,
                         artist_id=old, title="Song", allow_create=True)
    upsert_album(cur, server_source="soulsync", server_id="al", artist_id=new,
                 title="Album", allow_create=True)
    upsert_track(cur, server_source="soulsync", server_id="tr", album_id=album,
                 artist_id=new, title="Song", allow_create=True)
    assert cur.execute("SELECT artist_id FROM lib2_album_artists").fetchone()[0] == new
    assert cur.execute("SELECT artist_id FROM lib2_track_artists WHERE track_id=?",
                       (track,)).fetchone()[0] == new


def test_server_ids_are_provider_scoped(cur):
    artist, _album, _track, _file = _imported(cur)
    upsert_artist(cur, server_source="plex", server_id="7", name="Muse")
    assert resolve_artist(cur, "jellyfin", "7") is None
    assert resolve_artist(cur, "plex", "7") == artist


def test_same_catalogue_rows_keep_multiple_media_server_mappings(cur):
    artist, album, track, _file = _imported(cur)
    for source, prefix in (("plex", "p"), ("jellyfin", "j")):
        assert upsert_artist(
            cur, server_source=source, server_id=f"{prefix}-a", name="Muse"
        ) == artist
        assert upsert_album(
            cur, server_source=source, server_id=f"{prefix}-al",
            artist_id=artist, title="Absolution",
        ) == album
        assert upsert_track(
            cur, server_source=source, server_id=f"{prefix}-t",
            album_id=album, artist_id=artist, title="Time Is Running Out",
            track_number=4, disc_number=1, file_path="/music/song.flac",
        ) == track

    assert resolve_artist(cur, "plex", "p-a") == artist
    assert resolve_artist(cur, "jellyfin", "j-a") == artist
    rows = cur.execute(
        "SELECT entity_type,server_source,server_id "
        "FROM lib2_media_server_mappings ORDER BY entity_type,server_source"
    ).fetchall()
    assert {(r[0], r[1], r[2]) for r in rows} == {
        ("artist", "plex", "p-a"), ("artist", "jellyfin", "j-a"),
        ("album", "plex", "p-al"), ("album", "jellyfin", "j-al"),
        ("track", "plex", "p-t"), ("track", "jellyfin", "j-t"),
    }


def test_mapping_delete_trigger_removes_only_deleted_entity(cur):
    artist, album, track, _file = _imported(cur)
    upsert_artist(cur, server_source="plex", server_id="p-a", name="Muse")
    upsert_album(cur, server_source="plex", server_id="p-al",
                 artist_id=artist, title="Absolution")
    upsert_track(cur, server_source="plex", server_id="p-t",
                 album_id=album, artist_id=artist, title="Time Is Running Out",
                 track_number=4, disc_number=1, file_path="/music/song.flac")

    cur.execute("DELETE FROM lib2_tracks WHERE id=?", (track,))

    assert cur.execute(
        "SELECT COUNT(*) FROM lib2_media_server_mappings "
        "WHERE entity_type='track' AND entity_id=?", (track,),
    ).fetchone()[0] == 0
    assert cur.execute(
        "SELECT COUNT(*) FROM lib2_media_server_mappings "
        "WHERE entity_type='artist' AND entity_id=?", (artist,),
    ).fetchone()[0] == 1


@pytest.mark.parametrize("allow_create", [False, True])
def test_server_length_fills_a_missing_one_and_never_replaces_the_catalogues(cur, allow_create):
    # the studio cut sat at the slowed track's path; Navidrome's 136 s became
    # the catalogue length every later download was checked against
    artist, album, track, _file = _imported(cur)

    def sync(duration):
        upsert_track(cur, server_source="navidrome", server_id="t1", album_id=album,
                     artist_id=artist, title="Time Is Running Out", track_number=4,
                     disc_number=1, duration=duration, file_path="/music/song.flac",
                     allow_create=allow_create)
        return cur.execute("SELECT duration FROM lib2_tracks WHERE id=?", (track,)).fetchone()[0]

    assert sync(0) is None
    assert sync(237000) == 237000
    assert sync(136000) == 237000
