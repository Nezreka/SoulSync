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
    from core.library2.importer import normalize_name
    db = MusicDatabase(str(tmp_path / "m.db"))
    media = tmp_path / "media"
    media.mkdir()
    track_file = media / "song.flac"
    track_file.write_bytes(b"x" * 4096)
    ids = {}
    with db._get_connection() as conn:
        artist = conn.execute(
            "INSERT INTO lib2_artists(name, name_key) VALUES(?,?)",
            ("Muse", normalize_name("Muse"))).lastrowid
        album = conn.execute(
            "INSERT INTO lib2_albums(primary_artist_id, title, origin) VALUES(?,?,'library')",
            (artist, "The Resistance")).lastrowid
        for title, path, size in (
            ("Uprising", str(track_file), 4096),
            ("Ghost Track", "/nowhere/gone.flac", None),
        ):
            track = conn.execute(
                "INSERT INTO lib2_tracks(album_id, title) VALUES(?,?)",
                (album, title)).lastrowid
            conn.execute(
                "INSERT INTO lib2_track_artists(track_id, artist_id) VALUES(?,?)",
                (track, artist))
            conn.execute(
                "INSERT INTO lib2_track_files(track_id, path, size, is_primary) "
                "VALUES(?,?,?,1)", (track, path, size))
            ids[title] = track
        # A track the catalogue knows and the disk does not: no file row at all,
        # which is what "we never got this one" looks like in v2 (ADR-03).
        ids["Fileless"] = conn.execute(
            "INSERT INTO lib2_tracks(album_id, title) VALUES(?,?)",
            (album, "Fileless Song")).lastrowid
        conn.commit()

    client = _FakeClient()
    state = {"client": client, "admin": True, "ids": ids, "db": db,
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
    r = http.post("/api/chat/files/upload",
                  json={"track_id": state["ids"]["Uprising"]})
    body = r.get_json()
    assert body["ok"] is True
    assert body["url"].endswith("song.flac")
    assert body["name"] == "song.flac" and body["size"] == 4096
    assert uploads[0]["key"] == "K123" and uploads[0]["bytes"] == 4096


def test_unreachable_track_404s_without_uploading(files_app):
    http, state, client, uploads = files_app
    r = http.post("/api/chat/files/upload",
                  json={"track_id": state["ids"]["Ghost Track"]})
    assert r.status_code == 404
    assert uploads == []


def test_track_without_a_file_row_404s(files_app):
    """A v2 track carries no path of its own — the file is a separate row
    (ADR-03). A track the library knows but never got a file for has none."""
    http, state, client, uploads = files_app
    r = http.post("/api/chat/files/upload",
                  json={"track_id": state["ids"]["Fileless"]})
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
    r = http.post("/api/chat/files/upload",
                  json={"track_id": state["ids"]["Uprising"]})
    assert r.status_code == 503
    assert uploads == []


def test_expiry_setting_travels(files_app):
    http, state, client, uploads = files_app
    state["config"]["soulseek.chat_filepost_expiry"] = "7d"
    http.post("/api/chat/files/upload",
              json={"track_id": state["ids"]["Uprising"]})
    assert uploads[0]["expiry"] == "7d"


def test_library_search_only_tracks_with_files(files_app):
    http, state, client, uploads = files_app
    body = http.get("/api/chat/files/library-search?q=upri").get_json()
    assert [t["title"] for t in body["tracks"]] == ["Uprising"]
    assert http.get("/api/chat/files/library-search?q=x").get_json()["tracks"] == []


def test_library_search_ids_are_what_upload_resolves(files_app):
    """The search is the only producer of the ids this route's sibling
    consumes, so they speak the same id space by construction."""
    http, state, client, uploads = files_app
    found = http.get("/api/chat/files/library-search?q=upri").get_json()["tracks"][0]
    assert found["id"] == state["ids"]["Uprising"]
    assert found["artist"] == "Muse" and found["album"] == "The Resistance"
    assert found["size"] == 4096
    assert http.post("/api/chat/files/upload",
                     json={"track_id": found["id"]}).get_json()["ok"] is True


def test_library_search_finds_a_track_by_its_artist(files_app):
    """Both of Muse's tracks have a file row — whether the file is still on
    disk is the upload's problem, exactly as it was when the path hung off the
    track row."""
    http, state, client, uploads = files_app
    body = http.get("/api/chat/files/library-search?q=muse").get_json()
    assert [t["title"] for t in body["tracks"]] == ["Ghost Track", "Uprising"]


def test_library_search_folds_accents(files_app):
    """SQLite's LIKE folds ASCII case only, so a stored 'Björk' never answered
    a typed 'bjork' — the same fold the rest of the library search uses."""
    http, state, client, uploads = files_app
    from core.library2.importer import normalize_name
    with state["db"]._get_connection() as conn:
        artist = conn.execute(
            "INSERT INTO lib2_artists(name, name_key) VALUES(?,?)",
            ("Björk", normalize_name("Björk"))).lastrowid
        album = conn.execute(
            "INSERT INTO lib2_albums(primary_artist_id, title, origin)"
            " VALUES(?,?,'library')", (artist, "Post")).lastrowid
        track = conn.execute(
            "INSERT INTO lib2_tracks(album_id, title) VALUES(?,?)",
            (album, "Hyperballad")).lastrowid
        conn.execute("INSERT INTO lib2_track_artists(track_id, artist_id)"
                     " VALUES(?,?)", (track, artist))
        conn.execute("INSERT INTO lib2_track_files(track_id, path, is_primary)"
                     " VALUES(?,'/m/hyper.flac',1)", (track,))
        conn.commit()

    body = http.get("/api/chat/files/library-search?q=bjork").get_json()
    assert [t["title"] for t in body["tracks"]] == ["Hyperballad"]


def test_library_search_treats_wildcards_as_text(files_app):
    """`%` and `_` are LIKE syntax; typed into a search box they are letters."""
    http, state, client, uploads = files_app
    assert http.get("/api/chat/files/library-search?q=%25").get_json()["tracks"] == []
    assert http.get("/api/chat/files/library-search?q=_p").get_json()["tracks"] == []


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


def test_media_server_path_resolves_via_music_paths(files_app, tmp_path, monkeypatch):
    """#1078-adjacent (Boulder): the DB stores a track's path as the MEDIA
    SERVER sees it (e.g. Plex '/mnt/musicBackup/...'), which SoulSync can't
    open directly. The upload must resolve it through the shared library
    resolver + Settings → Library → Music Paths, not a naive as-stored check.
    """
    http, state, client, uploads = files_app
    from core.settings import config_manager
    from database.music_database import MusicDatabase

    # the real file lives where SoulSync mounts the library
    mount = tmp_path / "ssmount"
    (mount / "Artist" / "Album").mkdir(parents=True)
    real = mount / "Artist" / "Album" / "07 - Song.flac"
    real.write_bytes(b"y" * 2048)

    # ...but the DB records the MEDIA-SERVER path, which doesn't exist here
    db = MusicDatabase(str(tmp_path / "m2.db"))
    with db._get_connection() as conn:
        artist = conn.execute(
            "INSERT INTO lib2_artists(name, name_key) VALUES('A','a')").lastrowid
        album = conn.execute(
            "INSERT INTO lib2_albums(primary_artist_id, title, origin)"
            " VALUES(?,'Album','library')", (artist,)).lastrowid
        track_id = conn.execute(
            "INSERT INTO lib2_tracks(album_id, title) VALUES(?,'Song')",
            (album,)).lastrowid
        conn.execute(
            "INSERT INTO lib2_track_files(track_id, path, size, is_primary)"
            " VALUES(?,'/mnt/musicBackup/Artist/Album/07 - Song.flac',2048,1)",
            (track_id,))
        conn.commit()
    monkeypatch.setattr(chat_api, "_db", lambda: db)

    # without a music-paths mapping → unreachable (honest 404 pointing at the fix)
    r = http.post("/api/chat/files/upload", json={"track_id": track_id})
    assert r.status_code == 404
    assert "Music Paths" in r.get_json()["error"]

    # configure where SoulSync sees the music → the suffix match resolves it
    prev = config_manager.get("library.music_paths", [])
    try:
        config_manager.set("library.music_paths", [str(mount)])
        r = http.post("/api/chat/files/upload", json={"track_id": track_id})
        body = r.get_json()
        assert body["ok"] is True
        assert body["url"].endswith("07 - Song.flac")
        assert uploads[-1]["bytes"] == 2048
    finally:
        config_manager.set("library.music_paths", prev)


# ── save-to-library (import a shared audio file into staging) ────────────────

def test_url_safety_guard():
    assert chat_api._is_safe_filepost_url("https://cdn.filepost.dev/a.flac")
    assert chat_api._is_safe_filepost_url("https://filepost.dev/x.mp3")
    # SSRF / spoof attempts all rejected
    assert not chat_api._is_safe_filepost_url("http://cdn.filepost.dev/a.flac")   # not https
    assert not chat_api._is_safe_filepost_url("https://evil.com/a.flac")
    assert not chat_api._is_safe_filepost_url("https://filepost.dev.evil.com/a.flac")
    assert not chat_api._is_safe_filepost_url("https://notfilepost.dev/a.flac")
    assert not chat_api._is_safe_filepost_url("file:///etc/passwd")
    assert not chat_api._is_safe_filepost_url("")


@pytest.fixture()
def import_app(files_app, tmp_path, monkeypatch):
    http, state, client, uploads = files_app
    staging = tmp_path / "staging"
    state["config"]["import.staging_path"] = str(staging)
    fetches = []

    def fake_fetch(url, dest, max_bytes):
        fetches.append({"url": url, "dest": dest, "max": max_bytes})
        with open(dest, "wb") as fh:
            fh.write(b"AUDIODATA")
        return 9

    monkeypatch.setattr(chat_api, "_fetch_url_to_file", fake_fetch)
    return http, state, fetches, staging


def test_save_audio_to_staging(import_app):
    http, state, fetches, staging = import_app
    r = http.post("/api/chat/files/import",
                  json={"url": "https://cdn.filepost.dev/x/song.flac",
                        "name": "song.flac", "mime": "audio/flac"})
    body = r.get_json()
    assert body["ok"] is True and body["name"] == "song.flac"
    assert (staging / "song.flac").read_bytes() == b"AUDIODATA"
    assert fetches[0]["url"] == "https://cdn.filepost.dev/x/song.flac"


def test_save_reports_auto_import_state(import_app):
    http, state, fetches, staging = import_app
    state["config"]["auto_import.enabled"] = True
    body = http.post("/api/chat/files/import",
                     json={"url": "https://cdn.filepost.dev/x/s.mp3", "name": "s.mp3",
                           "mime": "audio/mpeg"}).get_json()
    assert body["auto_import"] is True


def test_save_rejects_non_filepost_url(import_app):
    http, state, fetches, staging = import_app
    r = http.post("/api/chat/files/import",
                  json={"url": "https://evil.com/s.flac", "name": "s.flac", "mime": "audio/flac"})
    assert r.status_code == 400
    assert fetches == []                       # never fetched


def test_save_rejects_video(import_app):
    http, state, fetches, staging = import_app
    r = http.post("/api/chat/files/import",
                  json={"url": "https://cdn.filepost.dev/x/clip.mp4", "name": "clip.mp4",
                        "mime": "video/mp4"})
    assert r.status_code == 400
    assert fetches == []


def test_save_duplicate_name_409(import_app):
    http, state, fetches, staging = import_app
    staging.mkdir(parents=True, exist_ok=True)
    (staging / "dup.flac").write_bytes(b"x")
    r = http.post("/api/chat/files/import",
                  json={"url": "https://cdn.filepost.dev/x/dup.flac", "name": "dup.flac",
                        "mime": "audio/flac"})
    assert r.status_code == 409
    assert fetches == []


def test_save_is_admin_only(import_app):
    http, state, fetches, staging = import_app
    state["admin"] = False
    r = http.post("/api/chat/files/import",
                  json={"url": "https://cdn.filepost.dev/x/s.flac", "name": "s.flac",
                        "mime": "audio/flac"})
    assert r.status_code == 403
    assert fetches == []


def test_save_basename_strips_path_traversal(import_app):
    http, state, fetches, staging = import_app
    http.post("/api/chat/files/import",
              json={"url": "https://cdn.filepost.dev/x/e.flac",
                    "name": "../../etc/evil.flac", "mime": "audio/flac"})
    # landed as a plain basename inside staging, never escaped it
    assert (staging / "evil.flac").exists()
    assert fetches[0]["dest"] == str(staging / "evil.flac")


def test_fetch_seam_enforces_size_cap(tmp_path):
    """The real fetch seam streams to a .part temp and aborts + cleans up when
    the byte cap is exceeded — a partial file never lands where auto-import
    would grab it."""
    import types

    class _Resp:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def raise_for_status(self): pass
        def iter_content(self, chunk_size=0):
            yield b"0" * 1000
            yield b"0" * 1000
    monkeypatch_requests = types.SimpleNamespace(get=lambda *a, **k: _Resp())
    import sys
    real = sys.modules.get("requests")
    sys.modules["requests"] = monkeypatch_requests
    try:
        dest = tmp_path / "big.flac"
        with pytest.raises(ValueError):
            chat_api._fetch_url_to_file("https://cdn.filepost.dev/big.flac", str(dest), 1500)
        assert not dest.exists()
        assert not (tmp_path / "big.flac.part").exists()   # temp cleaned up
    finally:
        if real is not None:
            sys.modules["requests"] = real


def test_frontend_save_wiring():
    js = (_ROOT / "webui" / "static" / "chat.js").read_text(encoding="utf-8")
    assert "data-chat-file-save" in js and "_saveFileToLibrary" in js
    assert "'/api/chat/files/import'" in js
    # only audio cards get the save chip
    card = js[js.index("function _fileCardHtml"):js.index("function renderGroups")]
    assert "isAudio\n" in card or "(isAudio" in card


def test_every_room_send_path_carries_the_channel_envelope():
    """A file share and the GIF picker built their payloads by hand and skipped
    the channel/thread tags the composer sends — so an upload posted in #help
    folded into #general for everyone (untagged messages fall back to the
    default channel by design, so they can never be swallowed entirely). One
    tagger now stamps every room send; this pins each path to it."""
    from pathlib import Path
    js = (Path(__file__).resolve().parents[1] / "webui" / "static" / "chat.js"
          ).read_text(encoding="utf-8")
    assert "function _tagRoomPayload" in js
    tagger = js.split("function _tagRoomPayload")[1].split("function _sendFileMessage")[0]
    assert "payload.chan" in tagger and "payload.thread" in tagger

    # the three room send paths, each through the tagger
    file_send = js.split("function _sendFileMessage")[1].split("\n    function ")[0]
    assert "_tagRoomPayload({" in file_send, "file shares post untagged again"
    gif_send = js.split("function sendGif")[1].split("\n    function ")[0]
    assert "_tagRoomPayload({" in gif_send, "GIFs post untagged again"
    # the composer path delegates rather than duplicating the tag logic
    assert js.count("payload.chan = state.channel") == 1, \
        "channel tagging exists in more than one place — the copies WILL drift"
