"""Chat next-level P2 — file sharing via filepost.dev.

Upload (browser file OR a library track resolved server-side), get a CDN
link, send it dressed as a rich file card (the URL travels as message TEXT
so links survive archives; the 'f' envelope key only dresses the card).
Hermetic: the filepost HTTP call is a monkeypatched seam, temp DBs, fake
slskd client.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from flask import Flask, g

import api.chat as chat_api
from core import chat_codec

_ROOT = Path(__file__).resolve().parent.parent


# ── codec: file_of ──────────────────────────────────────────────────────────

def test_file_of_roundtrip():
    env = chat_codec.encode("https://cdn.filepost.dev/x.flac",
                            {"f": {"n": "song.flac", "s": 12345, "m": "audio/flac"}})
    dec = chat_codec.decode(env)
    assert chat_codec.file_of(dec) == {"n": "song.flac", "s": 12345, "m": "audio/flac"}
    assert dec["t"] == "https://cdn.filepost.dev/x.flac"


@pytest.mark.parametrize("bad", [
    None, {}, {"f": None}, {"f": []}, {"f": {"s": 5}},         # no name
    {"f": {"n": ""}},
])
def test_file_of_rejects_garbage(bad):
    assert chat_codec.file_of(bad) is None


def test_file_of_caps_and_coerces():
    f = chat_codec.file_of({"f": {"n": "x" * 500, "s": "not-a-number", "m": "y" * 200}})
    assert len(f["n"]) == 200
    assert "s" not in f
    assert len(f["m"]) == 80


# ── unwrap: the card rides the visible message ──────────────────────────────

def test_unwrap_attaches_file_card():
    env = chat_codec.encode("https://cdn.filepost.dev/a.flac",
                            {"f": {"n": "a.flac", "s": 100}})
    out, _r, _p = chat_api._unwrap_room_messages(
        [{"username": "dj", "message": env, "timestamp": "t1"}])
    assert out[0]["message"] == "https://cdn.filepost.dev/a.flac"
    assert out[0]["file"] == {"n": "a.flac", "s": 100}


# ── endpoints ───────────────────────────────────────────────────────────────

class _FakeClient:
    base_url = "http://slskd"

    def __init__(self):
        self.joined = ["SoulSync"]
        self.sent_room = []

    def get_joined_rooms(self):
        return list(self.joined)

    def join_room(self, room):
        self.joined.append(room)
        return True

    def send_room_message(self, room, message):
        self.sent_room.append((room, message))
        return True


@pytest.fixture()
def files_app(tmp_path, monkeypatch):
    from database.music_database import MusicDatabase
    db = MusicDatabase(str(tmp_path / "m.db"))
    media = tmp_path / "media"
    media.mkdir()
    track_file = media / "song.flac"
    track_file.write_bytes(b"x" * 4096)
    with db._get_connection() as conn:
        conn.execute("INSERT INTO artists (id, name, server_source) VALUES ('AR1', 'Muse', 'test')")
        conn.execute("INSERT INTO albums (id, artist_id, title, server_source) "
                     "VALUES ('AL1', 'AR1', 'The Resistance', 'test')")
        conn.execute("INSERT INTO tracks (id, album_id, artist_id, title, file_path, file_size, server_source) "
                     "VALUES ('T1', 'AL1', 'AR1', 'Uprising', ?, 4096, 'test')", (str(track_file),))
        conn.execute("INSERT INTO tracks (id, album_id, artist_id, title, file_path, server_source) "
                     "VALUES ('T2', 'AL1', 'AR1', 'Ghost Track', '/nowhere/gone.flac', 'test')")
        conn.commit()

    client = _FakeClient()
    state = {"client": client, "admin": True,
             "config": {"soulseek.chat_filepost_key": "K123"}}
    uploads = []

    def fake_upload(api_key, name, stream, expiry=None):
        uploads.append({"key": api_key, "name": name,
                        "bytes": len(stream.read()), "expiry": expiry})
        return {"url": "https://cdn.filepost.dev/abc/" + name}

    monkeypatch.setattr(chat_api, "_filepost_upload", fake_upload)
    monkeypatch.setattr(chat_api, "_db", lambda: db)
    chat_api.configure(
        client_getter=lambda: state["client"],
        run_async=lambda v: v,
        config_get=lambda key, default=None: state["config"].get(key, default),
        config_set=lambda key, value: state["config"].__setitem__(key, value),
    )
    app = Flask(__name__)

    @app.before_request
    def _fake_profile():
        g.is_admin = state["admin"]

    app.register_blueprint(chat_api.create_blueprint())
    yield app.test_client(), state, client, uploads
    chat_api.configure(client_getter=lambda: None, run_async=lambda v: v,
                       config_get=lambda k, d=None: d)


def test_library_track_upload_resolves_path(files_app):
    http, state, client, uploads = files_app
    r = http.post("/api/chat/files/upload", json={"track_id": "T1"})
    body = r.get_json()
    assert body["ok"] is True
    assert body["url"].endswith("song.flac")
    assert body["name"] == "song.flac" and body["size"] == 4096
    assert uploads[0]["key"] == "K123" and uploads[0]["bytes"] == 4096


def test_unreachable_track_404s_without_uploading(files_app):
    http, state, client, uploads = files_app
    r = http.post("/api/chat/files/upload", json={"track_id": "T2"})
    assert r.status_code == 404
    assert uploads == []


def test_browser_file_upload(files_app):
    import io
    http, state, client, uploads = files_app
    r = http.post("/api/chat/files/upload",
                  data={"file": (io.BytesIO(b"imgdata"), "cover.png")},
                  content_type="multipart/form-data")
    body = r.get_json()
    assert body["ok"] is True and body["name"] == "cover.png"
    assert body["mime"] == "image/png"


def test_no_key_means_503(files_app):
    http, state, client, uploads = files_app
    state["config"]["soulseek.chat_filepost_key"] = ""
    r = http.post("/api/chat/files/upload", json={"track_id": "T1"})
    assert r.status_code == 503
    assert uploads == []


def test_expiry_setting_travels(files_app):
    http, state, client, uploads = files_app
    state["config"]["soulseek.chat_filepost_expiry"] = "7d"
    http.post("/api/chat/files/upload", json={"track_id": "T1"})
    assert uploads[0]["expiry"] == "7d"


def test_library_search_only_tracks_with_files(files_app):
    http, state, client, uploads = files_app
    body = http.get("/api/chat/files/library-search?q=upri").get_json()
    assert [t["title"] for t in body["tracks"]] == ["Uprising"]
    assert http.get("/api/chat/files/library-search?q=x").get_json()["tracks"] == []


def test_room_send_dresses_the_file_card(files_app):
    http, state, client, uploads = files_app
    r = http.post("/api/chat/room/message",
                  json={"room": "SoulSync", "message": "https://cdn.filepost.dev/a.flac",
                        "file": {"n": "a.flac", "s": 100, "m": "audio/flac"}})
    assert r.get_json()["ok"] is True
    dec = chat_codec.decode(client.sent_room[-1][1])
    assert dec["t"] == "https://cdn.filepost.dev/a.flac"
    assert chat_codec.file_of(dec) == {"n": "a.flac", "s": 100, "m": "audio/flac"}


def test_settings_round_trip(files_app):
    http, state, client, uploads = files_app
    http.post("/api/chat/settings", json={"filepost_key": "NEWKEY", "filepost_expiry": "24h"})
    assert state["config"]["soulseek.chat_filepost_key"] == "NEWKEY"
    assert state["config"]["soulseek.chat_filepost_expiry"] == "24h"
    body = http.get("/api/chat/settings").get_json()
    assert body["filepost_key_set"] is True and body["filepost_expiry"] == "24h"
    # bogus expiry values never save
    http.post("/api/chat/settings", json={"filepost_expiry": "99y"})
    assert state["config"]["soulseek.chat_filepost_expiry"] == "24h"


# ── frontend pins ───────────────────────────────────────────────────────────

def test_frontend_file_wiring():
    js = (_ROOT / "webui" / "static" / "chat.js").read_text(encoding="utf-8")
    assert "_fileCardHtml" in js
    assert "data-chat-file-audio" in js and "data-chat-file-video" in js
    assert "toggleAttachPanel" in js and "attachSendTrack" in js
    assert "'/api/chat/files/upload'" in js
    assert "^https:\\/\\//i" in js or "https:" in js   # non-https cards degrade to text
    html = (_ROOT / "webui" / "index.html").read_text(encoding="utf-8")
    assert "data-chat-attach-btn" in html and "data-chat-attach-pop" in html
    assert "data-chat-set-filepost" in html


def test_browser_upload_size_cap_holds_server_side(files_app):
    """REVIEW CATCH: the 50MB cap must reject oversized BROWSER uploads
    before anything streams to filepost (the library branch already
    checked; the multipart branch didn't)."""
    import io
    http, state, client, uploads = files_app
    big = io.BytesIO(b"0" * (52 * 1024 * 1024))
    r = http.post("/api/chat/files/upload",
                  data={"file": (big, "huge.bin")},
                  content_type="multipart/form-data")
    assert r.status_code == 413
    assert uploads == []
