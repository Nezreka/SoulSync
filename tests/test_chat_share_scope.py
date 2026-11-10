"""review 5 W3-TB-2: the chat share flow read every library, while resolve-share in the
same file reads only the caller's. A track whose only file lives in another
profile's own library must not be offered or uploaded from the shared library."""
from __future__ import annotations

import pytest
from flask import Flask, g

import api.chat as chat_api


@pytest.fixture()
def app(tmp_path, monkeypatch):
    from database.music_database import MusicDatabase
    from core.library2.importer import normalize_name
    import core.library_scope as scope_mod

    db = MusicDatabase(str(tmp_path / "m.db"))
    song = tmp_path / "theirs.flac"
    song.write_bytes(b"x" * 2048)
    with db._get_connection() as conn:
        ar = conn.execute("INSERT INTO lib2_artists(name, name_key) VALUES(?,?)",
                          ("Muse", normalize_name("Muse"))).lastrowid
        al = conn.execute("INSERT INTO lib2_albums(primary_artist_id, title, origin)"
                          " VALUES(?,?,'library')", (ar, "Absolution")).lastrowid
        t = conn.execute("INSERT INTO lib2_tracks(album_id, title) VALUES(?,?)",
                         (al, "Hysteria")).lastrowid
        conn.execute("INSERT INTO lib2_track_artists(track_id, artist_id) VALUES(?,?)", (t, ar))
        # the only copy is profile 9's own library
        conn.execute("INSERT INTO lib2_track_files(track_id, path, size, is_primary, owner_profile_id)"
                     " VALUES(?,?,?,1,9)", (t, str(song), 2048))
        conn.commit()

    # libraries are separated; the caller reads the shared one
    monkeypatch.setattr(scope_mod, "any_own_library_exists", lambda: True)
    monkeypatch.setattr(scope_mod, "current_library_scope", lambda: "shared")

    uploads = []
    monkeypatch.setattr(chat_api, "_filepost_upload",
                        lambda k, n, s, e=None: uploads.append(n) or {"url": "https://cdn.filepost.dev/x/" + n})
    monkeypatch.setattr(chat_api, "_db", lambda: db)
    monkeypatch.setattr("core.library.path_resolver.resolve_library_file_path",
                        lambda fp, config_manager=None: fp)
    cfg = {"soulseek.chat_filepost_key": "K"}
    chat_api.configure(client_getter=lambda: None, run_async=lambda v: v,
                       config_get=lambda k, d=None: cfg.get(k, d))
    flask_app = Flask(__name__)

    @flask_app.before_request
    def _p():
        g.is_admin = True

    flask_app.register_blueprint(chat_api.create_blueprint())
    yield flask_app.test_client(), t, uploads
    chat_api.configure(client_getter=lambda: None, run_async=lambda v: v,
                       config_get=lambda k, d=None: d)


def test_resolve_share_already_reads_only_this_library(app):
    http, t, uploads = app
    body = http.post("/api/chat/wanted/resolve-share",
                     json={"title": "Hysteria", "artist": "Muse", "type": "track"}).get_json()
    assert body["found"] is False


def test_library_search_does_not_offer_another_librarys_track(app):
    http, t, uploads = app
    body = http.get("/api/chat/files/library-search?q=hyst").get_json()
    assert body["tracks"] == [], body


def test_upload_does_not_share_another_librarys_file(app):
    http, t, uploads = app
    r = http.post("/api/chat/files/upload", json={"track_id": t})
    assert r.status_code == 404 and uploads == [], (r.status_code, r.get_json(), uploads)


def test_this_librarys_own_copy_is_still_offered_and_shared(app, tmp_path):
    """the shared library's copy of the same song is the one offered and
    uploaded, even though profile 9's copy holds the primary flag"""
    http, t, uploads = app
    ours = tmp_path / "ours.flac"
    ours.write_bytes(b"y" * 1024)
    with chat_api._db()._get_connection() as conn:
        conn.execute("INSERT INTO lib2_track_files(track_id, path, size, format, owner_profile_id)"
                     " VALUES(?,?,?,'mp3',NULL)", (t, str(ours), 1024))
        # theirs is the lossless copy, so it keeps the one primary flag
        conn.execute("UPDATE lib2_track_files SET format='flac' WHERE owner_profile_id=9")
        conn.execute("UPDATE lib2_track_files SET is_primary = (owner_profile_id IS 9) WHERE track_id=?", (t,))
        conn.commit()
        assert conn.execute("SELECT owner_profile_id FROM lib2_track_files"
                            " WHERE track_id=? AND is_primary=1", (t,)).fetchone()[0] == 9
    body = http.get("/api/chat/files/library-search?q=hyst").get_json()
    assert [(r["id"], r["size"]) for r in body["tracks"]] == [(t, 1024)]
    r = http.post("/api/chat/files/upload", json={"track_id": t})
    assert r.status_code == 200 and uploads == ["ours.flac"], (r.status_code, r.get_json())
