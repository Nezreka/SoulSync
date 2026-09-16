"""#1253: a jellyfin artist with no photo on the server got a thumb_url anyway.

/Items/<id>/Images/Primary answers 404 for an item with no primary image, but
the client stored it for every artist and album regardless. the library grid
showed the music-note placeholder, the artist page fell back to release art,
and the photo picker's "current" tile was a broken image. worse, every
enrichment worker only backfills a photo into an EMPTY thumb_url, so the
phantom url kept a real photo out for good.

jellyfin lists an item's images in ImageTags; no 'Primary' entry means no
image. an item that carries no ImageTags at all is ambiguous and keeps the
old url.
"""

from core.jellyfin_client import JellyfinAlbum, JellyfinArtist


class _Client:
    pass


def test_artist_without_primary_image_has_no_thumb():
    art = JellyfinArtist({'Id': 'a1', 'Name': 'Maelstrom', 'ImageTags': {}}, _Client())
    assert art.thumb is None


def test_artist_with_only_other_image_kinds_has_no_thumb():
    art = JellyfinArtist(
        {'Id': 'a1', 'Name': 'Maelstrom', 'ImageTags': {'Backdrop': 'x', 'Logo': 'y'}},
        _Client(),
    )
    assert art.thumb is None


def test_artist_with_primary_image_keeps_the_url():
    art = JellyfinArtist({'Id': 'a1', 'Name': 'Maelstrom', 'ImageTags': {'Primary': 'abc'}}, _Client())
    assert art.thumb == '/Items/a1/Images/Primary'


def test_artist_dto_without_image_tags_keeps_the_old_behaviour():
    # older servers / lean requests: nothing to go on, so nothing changes
    art = JellyfinArtist({'Id': 'a1', 'Name': 'Maelstrom'}, _Client())
    assert art.thumb == '/Items/a1/Images/Primary'


def test_album_follows_the_same_rule():
    assert JellyfinAlbum({'Id': 'b1', 'Name': 'EP', 'ImageTags': {}}, _Client()).thumb is None
    assert JellyfinAlbum({'Id': 'b1', 'Name': 'EP', 'ImageTags': {'Primary': 't'}}, _Client()).thumb == '/Items/b1/Images/Primary'
    assert JellyfinAlbum({'Id': 'b1', 'Name': 'EP'}, _Client()).thumb == '/Items/b1/Images/Primary'


# ── the sweep: existing rows already hold the phantom url ─────────────────────

import pytest

from database.music_database import MusicDatabase


@pytest.fixture()
def db(tmp_path):
    return MusicDatabase(str(tmp_path / "phantom.db"))


def _seed(db, rows):
    """Catalogue artists as the jellyfin scan leaves them.

    The sweep is keyed on the SERVER's id, which on the v2 catalogue lives in
    lib2_media_server_mappings (with server_source/server_id on the row as the
    compatibility projection), not on the primary key.
    """
    from tests.support.catalogue_seed import seed_artist

    conn = db._get_connection()
    try:
        for artist_id, thumb, locked in rows:
            row_id = seed_artist(conn, server_id=artist_id, name=f'Artist {artist_id}',
                                 server_source='jellyfin', image_url=thumb)
            conn.execute("UPDATE lib2_artists SET art_locked=? WHERE id=?", (locked, row_id))
            conn.execute(
                "INSERT OR REPLACE INTO lib2_media_server_mappings"
                "(entity_type, entity_id, server_source, server_id)"
                " VALUES('artist', ?, 'jellyfin', ?)", (row_id, artist_id))
        conn.commit()
    finally:
        conn.close()


def _thumbs(db):
    conn = db._get_connection()
    try:
        return {r['server_id']: r['image_url']
                for r in conn.execute("SELECT server_id, image_url FROM lib2_artists")}
    finally:
        conn.close()


def test_sweep_clears_only_the_phantom_url_for_the_named_artists(db):
    _seed(db, [
        ('a1', '/Items/a1/Images/Primary', 0),          # phantom, server says no image
        ('a2', '/Items/a2/Images/Primary', 0),          # server still has an image: not named
        ('a3', 'https://cdn.deezer/real.jpg', 0),       # enrichment wrote a real one: keep
        ('a4', '/Items/a4/Images/Primary', 1),          # hand-picked and locked: keep
        ('a5', '/Items/other/Images/Primary', 0),       # someone else's id shape: keep
    ])
    cleared = db.clear_phantom_artist_thumbs({'a1', 'a3', 'a4', 'a5'}, 'jellyfin')
    assert cleared == 1
    thumbs = _thumbs(db)
    assert thumbs['a1'] is None
    assert thumbs['a2'] == '/Items/a2/Images/Primary'
    assert thumbs['a3'] == 'https://cdn.deezer/real.jpg'
    assert thumbs['a4'] == '/Items/a4/Images/Primary'
    assert thumbs['a5'] == '/Items/other/Images/Primary'


def test_sweep_is_scoped_to_the_server(db):
    _seed(db, [('a1', '/Items/a1/Images/Primary', 0)])
    assert db.clear_phantom_artist_thumbs({'a1'}, 'emby') == 0
    assert _thumbs(db)['a1'] == '/Items/a1/Images/Primary'


def test_sweep_with_nothing_named_touches_nothing(db):
    _seed(db, [('a1', '/Items/a1/Images/Primary', 0)])
    assert db.clear_phantom_artist_thumbs(set(), 'jellyfin') == 0
    assert db.clear_phantom_artist_thumbs(None, 'jellyfin') == 0


def test_sweep_handles_more_ids_than_one_statement_takes(db):
    ids = [f'a{i}' for i in range(1200)]
    _seed(db, [(i, f'/Items/{i}/Images/Primary', 0) for i in ids])
    assert db.clear_phantom_artist_thumbs(set(ids), 'jellyfin') == 1200
    assert all(v is None for v in _thumbs(db).values())


# ── the worker asks the client, and only a client that can answer ─────────────

from core.database_update_worker import DatabaseUpdateWorker


class _Db:
    def __init__(self):
        self.calls = []

    def clear_phantom_artist_thumbs(self, ids, server_source):
        self.calls.append((set(ids), server_source))
        return len(ids)


def _worker(client, db):
    w = DatabaseUpdateWorker.__new__(DatabaseUpdateWorker)
    w.media_client = client
    w.database = db
    w.server_type = 'jellyfin'
    return w


def test_worker_sweeps_what_the_client_reports():
    class _Client:
        def get_artist_ids_without_image(self):
            return {'a1', 'a9'}
    db = _Db()
    _worker(_Client(), db)._clear_phantom_artist_thumbs()
    assert db.calls == [({'a1', 'a9'}, 'jellyfin')]


def test_worker_leaves_the_db_alone_when_the_client_cannot_answer():
    class _Unsure:
        def get_artist_ids_without_image(self):
            return None      # request failed: don't guess
    class _Plex:
        pass                 # no such method: not its problem
    for client in (_Unsure(), _Plex()):
        db = _Db()
        _worker(client, db)._clear_phantom_artist_thumbs()
        assert db.calls == []


def test_client_reports_ids_lacking_a_primary_image(monkeypatch):
    from core.jellyfin_client import JellyfinClient
    client = JellyfinClient.__new__(JellyfinClient)
    client.music_library_id = 'lib'
    monkeypatch.setattr(client, 'ensure_connection', lambda: True)
    monkeypatch.setattr(client, '_make_request', lambda path, params: {'Items': [
        {'Id': 'a1', 'ImageTags': {}},
        {'Id': 'a2', 'ImageTags': {'Primary': 'tag'}},
        {'Id': 'a3'},                                    # ambiguous: not reported
        {'Id': 'a4', 'ImageTags': {'Backdrop': 'b'}},
    ]})
    assert client.get_artist_ids_without_image() == {'a1', 'a4'}


def test_client_answers_none_when_the_request_fails(monkeypatch):
    from core.jellyfin_client import JellyfinClient
    client = JellyfinClient.__new__(JellyfinClient)
    client.music_library_id = 'lib'
    monkeypatch.setattr(client, 'ensure_connection', lambda: True)
    monkeypatch.setattr(client, '_make_request', lambda path, params: None)
    assert client.get_artist_ids_without_image() is None
    monkeypatch.setattr(client, 'ensure_connection', lambda: False)
    assert client.get_artist_ids_without_image() is None
