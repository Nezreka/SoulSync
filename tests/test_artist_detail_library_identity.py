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
    conn.commit()
    conn.close()
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
