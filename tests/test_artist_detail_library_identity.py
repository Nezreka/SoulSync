"""a profile that may search but not open the library still reaches an
owned artist: the route asks for the catalogue row's provider identity and
opens the provider page instead of bouncing home."""

from __future__ import annotations

import pytest

from tests.lib2_seed import row_conn

flask = pytest.importorskip("flask")


class _Db:
    def __init__(self, path):
        self.path = path

    def _get_connection(self):
        return row_conn(self.path)

    keys: set = set()

    def manual_path_keys(self):
        return set(self.keys)

    @staticmethod
    def manual_path_key(file_path):
        from database.music_database import MusicDatabase
        return MusicDatabase.manual_path_key(file_path)


@pytest.fixture
def client(tmp_path, monkeypatch):
    import api.artist_detail as ad
    from core.library2.schema import ensure_library_v2_schema

    path = str(tmp_path / "lib2.db")
    conn = row_conn(path)
    ensure_library_v2_schema(conn)
    conn.execute("INSERT INTO lib2_artists(id, name, sort_name, spotify_id) "
                 "VALUES(1, 'Drake', 'Drake', 'sp-drake')")
    conn.execute("INSERT INTO lib2_artists(id, name, sort_name, external_ids) "
                 "VALUES(2, 'Deezer Only', 'Deezer Only', '{\"deezer\":\"dz-9\"}')")
    conn.execute("INSERT INTO lib2_artists(id, name, sort_name) VALUES(3, 'Bare', 'Bare')")
    for album_id, fpath in ((10, "/m/Drake/Hand Tagged/01.flac"), (11, "/m/Drake/Plain/01.flac")):
        conn.execute("INSERT INTO lib2_albums(id, primary_artist_id, title) VALUES(?, 1, ?)",
                     (album_id, f"a{album_id}"))
        conn.execute("INSERT INTO lib2_tracks(id, album_id, title) VALUES(?, ?, 't')",
                     (album_id, album_id))
        conn.execute("INSERT INTO lib2_track_files(track_id, path, format) VALUES(?, ?, ?)",
                     (album_id, fpath, "flac" if album_id == 10 else "mp3"))
    conn.commit()
    conn.close()
    _Db.keys = {"drake/hand tagged/01.flac"}
    monkeypatch.setattr(ad, "get_database", lambda: _Db(path))
    app = flask.Flask(__name__)
    app.register_blueprint(ad.bp)
    return app.test_client()


def test_identity_prefers_spotify(client):
    body = client.get("/api/artist-detail/library/1/identity").get_json()
    assert (body["source"], body["provider_id"], body["name"]) == ("spotify", "sp-drake", "Drake")


def test_identity_falls_back_to_other_providers(client):
    body = client.get("/api/artist-detail/library/2/identity").get_json()
    assert (body["source"], body["provider_id"]) == ("deezer", "dz-9")


def test_identity_without_provider_ids_and_unknown_rows(client):
    assert client.get("/api/artist-detail/library/3/identity").get_json()["source"] is None
    assert client.get("/api/artist-detail/library/99/identity").status_code == 404


def test_metadata_lock_reads_the_hand_tagged_files_of_a_lib2_album(client):
    """the unlock item shows only on an album that was tagged by hand."""
    assert client.get("/api/album/10/metadata-lock").get_json()["metadata_locked"] is True
    assert client.get("/api/album/11/metadata-lock").get_json()["metadata_locked"] is False


def test_record_carries_the_artists_live_file_formats(client):
    """the hero's format tags come from the record the db viewer already reads."""
    body = client.get("/api/artist/1/record").get_json()
    assert body["formats"] == ["FLAC", "MP3"]
    assert body["record"]["name"] == "Drake"
