"""artist.jpg only ever lands in a folder named after the artist.

picking an artist photo also writes artist.jpg, "two folders up" from one
of their tracks. a user with three loose tracks in the library root picked
a photo for that artist and it was written to /music/artist.jpg. navidrome
then used it for every artist without a photo of their own, and the whole
library page turned into one face.
"""

from __future__ import annotations

import os
import tempfile

import pytest

from core.library.artist_image import find_artist_folder


def _touch(path):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    open(path, 'wb').close()
    return path


# ── the helper ───────────────────────────────────────────────────────────────

def test_standard_layout_finds_the_artist_folder(tmp_path):
    track = _touch(str(tmp_path / "music" / "Tiësto" / "Kaleidoscope" / "01.flac"))
    assert find_artist_folder([track], "Tiësto") == str(tmp_path / "music" / "Tiësto")


def test_a_loose_track_in_the_root_finds_nothing(tmp_path):
    track = _touch(str(tmp_path / "music" / "ASOT 1294 - 01.flac"))
    assert find_artist_folder([track], "Armin van Buuren") == ""


def test_a_track_straight_in_the_artist_folder_finds_it(tmp_path):
    # Artist/track with no album folder: two up would be the library root
    track = _touch(str(tmp_path / "music" / "Tiësto" / "01.flac"))
    assert find_artist_folder([track], "Tiësto") == str(tmp_path / "music" / "Tiësto")


def test_the_loose_track_is_skipped_for_one_in_the_artist_folder(tmp_path):
    loose = _touch(str(tmp_path / "music" / "loose.flac"))
    proper = _touch(str(tmp_path / "music" / "Tiësto" / "Album" / "01.flac"))
    assert find_artist_folder([loose, proper], "Tiësto") == str(tmp_path / "music" / "Tiësto")


def test_a_self_titled_album_still_picks_the_artist_folder(tmp_path):
    track = _touch(str(tmp_path / "music" / "Weezer" / "Weezer" / "01.flac"))
    assert find_artist_folder([track], "Weezer") == str(tmp_path / "music" / "Weezer")


def test_sanitized_and_accented_names_still_match(tmp_path):
    acdc = _touch(str(tmp_path / "music" / "AC_DC" / "Back in Black" / "01.flac"))
    assert find_artist_folder([acdc], "AC/DC") == str(tmp_path / "music" / "AC_DC")
    tiesto = _touch(str(tmp_path / "music" / "Tiesto" / "In Search of Sunrise" / "01.flac"))
    assert find_artist_folder([tiesto], "Tiësto") == str(tmp_path / "music" / "Tiesto")


def test_a_compilation_folder_is_not_the_artists(tmp_path):
    track = _touch(str(tmp_path / "music" / "Various Artists" / "Now 99" / "07.flac"))
    assert find_artist_folder([track], "Akon") == ""


def test_no_name_no_folder(tmp_path):
    track = _touch(str(tmp_path / "music" / "X" / "A" / "01.flac"))
    assert find_artist_folder([track], "") == ""
    assert find_artist_folder([None, ""], "X") == ""


# ── through the real endpoints ───────────────────────────────────────────────

_TMP = tempfile.mkdtemp(prefix='soulsync-testdb-artistjpg-')
os.environ['DATABASE_PATH'] = os.path.join(_TMP, 'artistjpg.db')
os.environ['SOULSYNC_TEST_DB_READY'] = '1'

_PNG = b'\x89PNG\r\n\x1a\n' + b'\x00' * 64


class _Resp:
    def __init__(self, body):
        self.body = body

    def read(self):
        return self.body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _seed(db, name, track_paths):
    """Library v2 port: an owned artist with one album of these files.
    Returns the catalogue artist id the endpoints take."""
    from tests import lib2_seed

    conn = db._get_connection()
    try:
        artist_id = lib2_seed.artist(conn, name)
        for i, path in enumerate(track_paths):
            lib2_seed.track(conn, name, "Album", f"t{i}", path=path)
        conn.commit()
        return artist_id
    finally:
        conn.close()


def _unseed(db, artist_id):
    conn = db._get_connection()
    try:
        tracks = [r[0] for r in conn.execute(
            "SELECT t.id FROM lib2_tracks t JOIN lib2_albums al ON al.id = t.album_id"
            " WHERE al.primary_artist_id = ?", (artist_id,))]
        for track_id in tracks:
            conn.execute("DELETE FROM lib2_track_files WHERE track_id = ?", (track_id,))
            conn.execute("DELETE FROM lib2_tracks WHERE id = ?", (track_id,))
        conn.execute("DELETE FROM lib2_albums WHERE primary_artist_id = ?", (artist_id,))
        conn.execute("DELETE FROM lib2_artists WHERE id = ?", (artist_id,))
        conn.commit()
    finally:
        conn.close()


@pytest.fixture
def server(monkeypatch):
    web_server = pytest.importorskip('web_server')
    from api import artist_detail
    import urllib.request
    monkeypatch.setattr(artist_detail, 'media_server_engine', None)
    monkeypatch.setattr(artist_detail, '_resolve_library_file_path', lambda p: p)
    monkeypatch.setattr(urllib.request, 'urlopen', lambda *a, **k: _Resp(_PNG))
    monkeypatch.setattr('core.library.artist_image.download_image_bytes', lambda *a, **k: _PNG)
    db = web_server.get_database()
    seeded = []

    def seed(name, paths):
        artist_id = _seed(db, name, paths)
        seeded.append(artist_id)
        return artist_id

    yield web_server.app.test_client(), seed
    for artist_id in seeded:
        _unseed(db, artist_id)


def test_picking_a_photo_for_a_loose_root_artist_leaves_the_root_alone(server, tmp_path):
    client, seed = server
    root = tmp_path / "music"
    loose = _touch(str(root / "ASOT 1294 - 01.flac"))
    _touch(str(root / "Akon" / "Trouble" / "01.flac"))
    artist_id = seed('Armin van Buuren', [loose])

    r = client.post(f'/api/artist/{artist_id}/art', json={'url': 'https://example.invalid/a.jpg'})
    body = r.get_json()
    assert r.status_code == 200 and body['success'] is True
    assert body['disk_written'] is False
    assert 'no folder named after this artist' in body['disk_skipped']
    assert not os.path.exists(root / "artist.jpg")
    assert not os.path.exists(tmp_path / "artist.jpg")


def test_picking_a_photo_writes_into_the_artist_folder(server, tmp_path):
    client, seed = server
    track = _touch(str(tmp_path / "music" / "Tiësto" / "Kaleidoscope" / "01.flac"))
    artist_id = seed('Tiësto', [track])

    body = client.post(f'/api/artist/{artist_id}/art', json={'url': 'https://example.invalid/t.jpg'}).get_json()
    assert body['disk_written'] is True and body['disk_skipped'] is None
    assert os.path.exists(tmp_path / "music" / "Tiësto" / "artist.jpg")
    assert not os.path.exists(tmp_path / "music" / "artist.jpg")


def test_write_image_to_disk_refuses_the_root_too(server, tmp_path):
    client, seed = server
    loose = _touch(str(tmp_path / "music" / "ASOT 1294 - 02.flac"))
    artist_id = seed('Armin van Buuren', [loose])

    r = client.post(f'/api/artist/{artist_id}/write-image-to-disk',
                    json={'image_url': 'https://example.invalid/a.jpg'})
    assert r.status_code == 400
    assert 'no folder named after this artist' in r.get_json()['error']
    assert not os.path.exists(tmp_path / "music" / "artist.jpg")
    assert not os.path.exists(tmp_path / "artist.jpg")
